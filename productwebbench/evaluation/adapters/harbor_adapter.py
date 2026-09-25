from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Any

from ...core.config import DEFAULT_OUTPUT_ROOT
from ...core.formal_data_guard import assert_not_under_formal_task_root
from ...core.io_utils import ensure_dir, read_jsonl, write_json
from ...core.task_files import task_file_patterns
from ..runners.batch_runner import load_specs


def toml_string(value: str) -> str:
    return json.dumps(value)


def task_name(task_id: str) -> str:
    return "sitecontinuum/" + task_id


def markdown_list(values: list[Any], *, empty: str) -> str:
    if not values:
        return f"- {empty}"
    return "\n".join(f"- {value}" for value in values)


def markdown_code_list(values: list[Any], *, empty: str) -> str:
    if not values:
        return f"- {empty}"
    return "\n".join(f"- `{value}`" for value in values)


def rubric_markdown(task: dict[str, Any]) -> str:
    rubric = task.get("evaluation_rubric")
    if not isinstance(rubric, dict) or not rubric:
        return "- No explicit rubric listed."
    lines = []
    for key, value in rubric.items():
        if isinstance(value, list):
            rendered = "; ".join(str(item) for item in value)
        else:
            rendered = str(value)
        lines.append(f"- {key}: {rendered}")
    return "\n".join(lines)


def instruction_markdown(task: dict[str, Any], readme: str) -> str:
    sections = [
        "# SiteContinuum Task\n\n"
        "Modify the website in the repository workspace to satisfy the requested change. "
        "Edit files in place using the repository's existing framework, routes, components, assets, and styling conventions.\n\n"
        "Do not edit generated outputs, dependency folders, lockfiles, verifier files, or benchmark metadata. "
        "Do not hard-code screenshots or hide content only to satisfy checks. Preserve existing reference/regression states unless the task explicitly asks otherwise.",
        f"## Task ID\n\n`{task['task_id']}`",
        f"## Repository\n\n`{task['repo_id']}`",
        f"## Requested Change\n\n{task.get('problem_statement') or task.get('instruction') or 'No task statement provided.'}",
        "## Required Content\n\n" + markdown_list(task.get("required_content", []), empty="No explicit required content listed."),
        "## Design Constraints\n\n" + markdown_list(task.get("design_constraints", []), empty="No explicit design constraints listed."),
        "## State Constraints\n\n" + markdown_list(task.get("state_constraints", []), empty="No explicit state constraints listed."),
        "## Preferred Source Files\n\n" + markdown_code_list(task_file_patterns(task), empty="No preferred files listed."),
        "## Required Browser States\n\n" + markdown_code_list(task.get("required_states", []), empty="No required states listed."),
        "## Evaluation Rubric\n\n" + rubric_markdown(task),
    ]
    if readme.strip():
        sections.append("## Original Task Package README\n\n" + readme.strip())
    return "\n\n".join(sections) + "\n"


def task_toml(task: dict[str, Any], *, agent_timeout_sec: int, verifier_timeout_sec: int) -> str:
    difficulty = task.get("difficulty", "unknown")
    tags = ["sitecontinuum", task.get("split", "dev"), task.get("scope", "web")]
    tags_text = ", ".join(toml_string(str(tag)) for tag in tags)
    return (
        'version = "1.0"\n\n'
        "[task]\n"
        f"name = {toml_string(task_name(task['task_id']))}\n\n"
        "[metadata]\n"
        f"author_name = {toml_string('SiteContinuum authors')}\n"
        f"author_email = {toml_string('unknown')}\n"
        f"difficulty = {toml_string(str(difficulty))}\n"
        f"category = {toml_string('web_frontend')}\n"
        f"tags = [{tags_text}]\n\n"
        "[agent]\n"
        f"timeout_sec = {float(agent_timeout_sec)}\n"
        'network_mode = "public"\n\n'
        "[verifier]\n"
        f"timeout_sec = {float(verifier_timeout_sec)}\n"
        'network_mode = "public"\n\n'
        "[environment]\n"
        "build_timeout_sec = 900.0\n"
        "cpus = 4\n"
        "memory_mb = 8192\n"
        "storage_mb = 20480\n"
    )


def test_script(task: dict[str, Any]) -> str:
    task_id = task["task_id"]
    repo_id = task["repo_id"]
    return f"""#!/usr/bin/env bash
set -euo pipefail

ROOT="${{SITECONTINUUM_ROOT:-/workspace}}"
OUT="${{SITECONTINUUM_VERIFY_OUT:-/tmp/sitecontinuum_verify}}"
TASK_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")/.." && pwd)"
mkdir -p "$OUT" /logs/verifier
export SITECONTINUUM_VERIFY_OUT="$OUT"

python3 -m sitecontinuum capture-states {repo_id} \\
  --workspace-root "$ROOT" \\
  --output-root "$OUT/states" \\
  --state-plan "$TASK_DIR/state_plan.json" \\
  --skip-install \\
  --skip-build \\
  --server-timeout "${{SITECONTINUUM_SERVER_TIMEOUT:-120}}" \\
  --capture-timeout "${{SITECONTINUUM_CAPTURE_TIMEOUT:-360}}" \\
  --no-server-lock

python3 -m sitecontinuum verify-submission \\
  --tasks "$TASK_DIR/task.jsonl" \\
  --specs "$TASK_DIR/submission_spec.normalized.json" \\
  --states-root "$OUT/states" \\
  --design-anchors "$TASK_DIR/design_anchors.json" \\
  --output "$OUT/submission_results.json"

python3 - <<'PY'
import json
import os
from pathlib import Path
out = Path(os.environ["SITECONTINUUM_VERIFY_OUT"])
report = json.loads((out / "submission_results.json").read_text())
passed = int(report.get("passed", 0))
total = int(report.get("total", 1)) or 1
reward = 1.0 if passed == total else 0.0
Path("/logs/verifier/reward.txt").write_text(str(reward))
Path("/logs/verifier/report.json").write_text(json.dumps(report, indent=2))
if reward < 1.0:
    raise SystemExit(1)
PY
"""


def solve_stub() -> str:
    return """#!/usr/bin/env bash
set -euo pipefail
echo "No oracle solution is bundled for this task yet." >&2
exit 1
"""


def export_one(
    task: dict[str, Any],
    spec: dict[str, Any],
    *,
    output_root: Path,
    bench_root: Path,
    state_plan: Path,
    design_anchors: Path,
    agent_timeout_sec: int,
    verifier_timeout_sec: int,
) -> Path:
    task_dir = output_root / task["task_id"]
    ensure_dir(task_dir)
    ensure_dir(task_dir / "tests")
    ensure_dir(task_dir / "solution")
    ensure_dir(task_dir / "environment")

    readme_path = bench_root / task["task_id"] / "README.md"
    readme = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""

    (task_dir / "instruction.md").write_text(instruction_markdown(task, readme), encoding="utf-8")
    (task_dir / "task.toml").write_text(
        task_toml(task, agent_timeout_sec=agent_timeout_sec, verifier_timeout_sec=verifier_timeout_sec),
        encoding="utf-8",
    )
    (task_dir / "tests" / "test.sh").write_text(test_script(task), encoding="utf-8")
    (task_dir / "solution" / "solve.sh").write_text(solve_stub(), encoding="utf-8")
    os.chmod(task_dir / "tests" / "test.sh", 0o755)
    os.chmod(task_dir / "solution" / "solve.sh", 0o755)
    (task_dir / "environment" / "Dockerfile").write_text(
        "FROM node:22-bookworm\n"
        "RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip git curl php-cli composer && rm -rf /var/lib/apt/lists/*\n"
        "WORKDIR /workspace\n",
        encoding="utf-8",
    )
    (task_dir / "task.jsonl").write_text(json.dumps(task, ensure_ascii=False) + "\n", encoding="utf-8")
    write_json(task_dir / "submission_spec.normalized.json", {"tasks": [spec]})
    shutil.copyfile(state_plan, task_dir / "state_plan.json")
    shutil.copyfile(design_anchors, task_dir / "design_anchors.json")
    return task_dir


def export_harbor_tasks(args: argparse.Namespace) -> None:
    assert_not_under_formal_task_root(args.output_root, purpose="Harbor export")
    tasks = list(read_jsonl(args.tasks))
    if args.limit is not None:
        tasks = tasks[: args.limit]
    specs = load_specs(args.specs)
    ensure_dir(args.output_root)
    exported = []
    for task in tasks:
        exported.append(
            str(
                export_one(
                    task,
                    specs[task["task_id"]],
                    output_root=args.output_root,
                    bench_root=args.bench_root,
                    state_plan=args.state_plan,
                    design_anchors=args.design_anchors,
                    agent_timeout_sec=args.agent_timeout_sec,
                    verifier_timeout_sec=args.verifier_timeout_sec,
                )
            )
        )
    write_json(args.output_root / "export_manifest.json", {"count": len(exported), "tasks": exported})
    print(json.dumps({"count": len(exported), "output_root": str(args.output_root)}, indent=2))


def add_export_harbor_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tasks", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_next.jsonl")
    parser.add_argument("--specs", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_next_submission_specs.json")
    parser.add_argument("--bench-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "packages" / "dev_next")
    parser.add_argument("--state-plan", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_next_state_plan.json")
    parser.add_argument("--design-anchors", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_next.design_anchors.json")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "harbor" / "dev_next")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--agent-timeout-sec", type=int, default=1800)
    parser.add_argument("--verifier-timeout-sec", type=int, default=900)
