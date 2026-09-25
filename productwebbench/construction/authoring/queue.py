from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from ...core.config import DEFAULT_OUTPUT_ROOT
from ...core.formal_data_guard import assert_not_under_formal_task_root
from ...core.io_utils import read_jsonl, write_json


ARCHETYPES = [
    {
        "gap": "component",
        "intent": "modify",
        "scope": "component",
        "difficulty": "L1",
        "families": ["dashboard", "docs", "portfolio", "commerce"],
        "problem_shape": "Modify one existing reusable component so it solves a small visual/product need while preserving the surrounding page.",
        "inspection_focus": ["component reuse", "nearby page usage", "DOM text signal", "one desktop and one mobile state"],
    },
    {
        "gap": "layout",
        "intent": "repair",
        "scope": "layout",
        "difficulty": "L2",
        "families": ["commerce", "dashboard", "docs", "marketing"],
        "problem_shape": "Repair a responsive layout or spacing problem in an existing section without changing its information hierarchy.",
        "inspection_focus": ["mobile overflow risk", "section spacing", "grid/card alignment", "before/after responsive states"],
    },
    {
        "gap": "asset",
        "intent": "modify",
        "scope": "asset",
        "difficulty": "L2",
        "families": ["commerce", "portfolio", "interactive_canvas", "marketing"],
        "problem_shape": "Improve how an existing image/icon/video/model asset is selected, cropped, captioned, or positioned inside an existing page.",
        "inspection_focus": ["asset inventory", "object-fit/aspect ratio", "semantic asset choice", "visual anchor crop"],
    },
    {
        "gap": "restyle",
        "intent": "restyle",
        "scope": "layout",
        "difficulty": "L2",
        "families": ["docs", "dashboard", "marketing", "portfolio"],
        "problem_shape": "Restyle a small existing area to match the site's own design language more consistently, without introducing a new theme.",
        "inspection_focus": ["design tokens", "button/card style", "typography scale", "regression states"],
    },
    {
        "gap": "cross_page",
        "intent": "restyle",
        "scope": "cross_page",
        "difficulty": "L5",
        "families": ["docs", "marketing", "commerce"],
        "problem_shape": "Evolve two related pages so shared cards, buttons, or section rhythm become consistent while preserving each page's content hierarchy.",
        "inspection_focus": ["two routes", "shared components", "nav/footer stability", "cross-page screenshots"],
    },
    {
        "gap": "repair",
        "intent": "repair",
        "scope": "component",
        "difficulty": "L1",
        "families": ["dashboard", "docs", "commerce", "portfolio"],
        "problem_shape": "Repair a local visual problem in an existing component state, such as clipped text, weak focus state, or icon/button misalignment.",
        "inspection_focus": ["specific defect state", "component crop", "focus/hover action", "no broad rewrite"],
    },
]

NON_WEBSITE_NAME_PARTS = {
    "ionicons",
    "octicons",
    "fontawesome",
    "heroicons",
    "simple-icons",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def media_total(record: dict[str, Any]) -> int:
    return sum(int(value) for value in record.get("media_count", {}).values())


def family_label(record: dict[str, Any]) -> str:
    text = " ".join([record.get("owner", ""), record.get("name", ""), record.get("repo_id", "")]).lower()
    framework = record.get("framework", "")
    if framework == "three" or any(token in text for token in ["three", "3d", "canvas", "game", "webgl"]):
        return "interactive_canvas"
    if any(token in text for token in ["admin", "dashboard", "crm", "analytics"]):
        return "dashboard"
    if any(token in text for token in ["docs", "documentation", "guide", "learn"]):
        return "docs"
    if any(token in text for token in ["shop", "store", "commerce", "product", "shoe", "ecommerce"]):
        return "commerce"
    if any(token in text for token in ["portfolio", "folio", "resume", "personal"]):
        return "portfolio"
    if any(token in text for token in ["template", "landing", "startup", "agency", "blog"]):
        return "marketing"
    return "other"


def used_repo_ids(tasks_path: Path) -> set[str]:
    if not tasks_path.exists():
        return set()
    return {task["repo_id"] for task in read_jsonl(tasks_path)}


def normalize_pathish(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip()


def audit_queue_inputs(candidates: list[dict[str, Any]], used_repos: set[str], *, reject_used_repo_overlap: bool = False) -> dict[str, Any]:
    repo_ids = [str(record.get("repo_id")) for record in candidates if record.get("repo_id")]
    repo_counts = Counter(repo_ids)
    duplicate_repo_ids = sorted(repo_id for repo_id, count in repo_counts.items() if count > 1)
    zip_paths = [normalize_pathish(record.get("zip_path")) for record in candidates if record.get("zip_path")]
    zip_counts = Counter(zip_paths)
    duplicate_zip_paths = sorted(path for path, count in zip_counts.items() if count > 1)
    used_overlaps = sorted(repo_id for repo_id in repo_counts if repo_id in used_repos)
    formal_markers = [
        str(record.get("repo_id") or f"row_{index}")
        for index, record in enumerate(candidates, start=1)
        if record.get("formal_task_record") is True or record.get("status") == "accepted"
    ]
    issues: list[str] = []
    if len(repo_ids) != len(candidates):
        issues.append(f"candidate queue input has {len(candidates) - len(repo_ids)} rows without repo_id")
    if duplicate_repo_ids:
        issues.append(f"candidate queue input has {len(duplicate_repo_ids)} duplicate repo_id values")
    if len(zip_paths) != len(candidates):
        issues.append(f"candidate queue input has {len(candidates) - len(zip_paths)} rows without zip_path")
    if duplicate_zip_paths:
        issues.append(f"candidate queue input has {len(duplicate_zip_paths)} duplicate zip_path values")
    if reject_used_repo_overlap and used_overlaps:
        issues.append(f"candidate queue input contains {len(used_overlaps)} repos already used by formal tasks")
    if formal_markers:
        issues.append(f"candidate queue input contains {len(formal_markers)} rows marked like formal/accepted data")
    return {
        "artifact_type": "authoring_queue_input_audit",
        "formal_task_record": False,
        "candidate_rows": len(candidates),
        "unique_repo_ids": len(repo_counts),
        "duplicate_repo_ids": duplicate_repo_ids,
        "duplicate_repo_id_count": len(duplicate_repo_ids),
        "unique_zip_paths": len(zip_counts),
        "duplicate_zip_paths": duplicate_zip_paths,
        "duplicate_zip_path_count": len(duplicate_zip_paths),
        "used_repo_overlap_count": len(used_overlaps),
        "used_repo_overlap_examples": used_overlaps[:50],
        "used_repo_overlap_policy": "reject" if reject_used_repo_overlap else "filter_from_queue",
        "formal_marker_count": len(formal_markers),
        "formal_marker_examples": formal_markers[:50],
        "issues": issues,
        "passed": not issues,
    }


def candidate_score(record: dict[str, Any], archetype: dict[str, Any]) -> tuple[int, int, int, str]:
    score = 0
    family = family_label(record)
    if family in archetype["families"]:
        score += 30
    if record.get("dev_command"):
        score += 10
    if record.get("build_command"):
        score += 6
    if record.get("has_routes"):
        score += 8
    if record.get("has_tests"):
        score += 3
    score += min(media_total(record), 80) // 8
    if record.get("file_count", 0) > 1800:
        score -= 8
    if record.get("file_count", 0) < 80:
        score -= 4
    return (score, media_total(record), -int(record.get("file_count", 0)), record.get("repo_id", ""))


def is_authoring_candidate(record: dict[str, Any]) -> bool:
    name = str(record.get("name", "")).lower()
    repo_id = str(record.get("repo_id", "")).lower()
    if any(part in name or part in repo_id for part in NON_WEBSITE_NAME_PARTS):
        return False
    if not record.get("has_routes"):
        return False
    if not record.get("dev_command") and not record.get("build_command"):
        return False
    return True


def archetype_reason(record: dict[str, Any], archetype: dict[str, Any]) -> list[str]:
    reasons = [
        f"family={family_label(record)} aligns with target families {archetype['families']}",
        f"framework={record.get('framework')}, package_manager={record.get('package_manager')}",
        f"media_count={record.get('media_count', {})}",
    ]
    if record.get("dev_command"):
        reasons.append(f"has dev command: {record['dev_command']}")
    if record.get("has_routes"):
        reasons.append("has route/page structure")
    if record.get("has_tests"):
        reasons.append("has tests for optional source-level sanity")
    return reasons


def inspection_brief(record: dict[str, Any], archetype: dict[str, Any]) -> str:
    repo_name = record.get("name", "this site")
    return (
        f"For `{repo_name}`, inspect the running site as one possible {archetype['intent']}/{archetype['scope']} "
        f"case study: {archetype['problem_shape']} This is only a triage brief. The accepted task, if any, "
        "must be written after repo-specific inspection of source files, visual anchors, captured states, and assets."
    )


def build_queue_items(
    candidates: list[dict[str, Any]],
    coverage: dict[str, Any],
    used_repos: set[str],
    per_gap: int,
) -> list[dict[str, Any]]:
    gaps = coverage.get("growth_gaps", {})
    needed = set(gaps.get("missing_target_scopes", [])) | set(gaps.get("missing_target_intents", [])) | set(gaps.get("missing_target_difficulties", []))
    selected_items: list[dict[str, Any]] = []
    selected_repos: set[str] = set()
    archetypes = [item for item in ARCHETYPES if item["gap"] in needed or item["intent"] in needed or item["scope"] in needed or item["difficulty"] in needed]
    if not archetypes:
        archetypes = ARCHETYPES[:3]

    available = [
        record
        for record in candidates
        if record.get("repo_id") not in used_repos and is_authoring_candidate(record)
    ]
    for archetype in archetypes:
        ranked = sorted(available, key=lambda record: candidate_score(record, archetype), reverse=True)
        count = 0
        for record in ranked:
            repo_id = record["repo_id"]
            if repo_id in selected_repos:
                continue
            selected_repos.add(repo_id)
            count += 1
            selected_items.append(
                {
                    "queue_id": f"next_{len(selected_items) + 1:03d}_{archetype['gap']}",
                    "repo_id": repo_id,
                    "repo_name": record.get("name"),
                    "owner": record.get("owner"),
                    "framework": record.get("framework"),
                    "package_manager": record.get("package_manager"),
                    "zip_path": record.get("zip_path"),
                    "file_count": record.get("file_count"),
                    "media_count": record.get("media_count", {}),
                    "target_gap": archetype["gap"],
                    "proposed_intent": archetype["intent"],
                    "proposed_scope": archetype["scope"],
                    "proposed_difficulty": archetype["difficulty"],
                    "website_type": family_label(record),
                    "inspection_brief": inspection_brief(record, archetype),
                    "inspection_focus": archetype["inspection_focus"],
                    "why_candidate": archetype_reason(record, archetype),
                    "next_commands": [
                        f"python3 -m sitecontinuum extract-repo {repo_id} --clean",
                        f"python3 -m sitecontinuum runability {repo_id}",
                        f"python3 -m sitecontinuum capture-states {repo_id} --skip-install",
                    ],
                }
            )
            if count >= per_gap:
                break
    return selected_items


def render_markdown(items: list[dict[str, Any]], coverage: dict[str, Any]) -> str:
    lines = [
        "# SiteContinuum Authoring Queue",
        "",
        "This queue is gap-driven. Items are candidates for agent-led repo inspection and task construction, not final benchmark tasks.",
        "",
        "## Growth Gaps",
        "",
    ]
    gaps = coverage.get("growth_gaps", {})
    for key, values in gaps.items():
        lines.append(f"- `{key}`: {', '.join(values) if values else 'none'}")
    lines.append("")
    for item in items:
        lines.extend(
            [
                f"## {item['queue_id']}: {item['repo_id']}",
                "",
                f"- Proposed: `{item['proposed_intent']}` / `{item['proposed_scope']}` / `{item['proposed_difficulty']}`",
                f"- Website type: `{item['website_type']}`",
                f"- Framework: `{item['framework']}`",
                f"- Zip: `{item['zip_path']}`",
                "",
                item.get("inspection_brief") or "",
                "",
                "### Why This Candidate",
                "",
            ]
        )
        lines.extend(f"- {reason}" for reason in item["why_candidate"])
        lines.extend(["", "### Inspection Focus", ""])
        lines.extend(f"- {focus}" for focus in item["inspection_focus"])
        lines.extend(["", "### Next Commands", ""])
        lines.extend(f"```bash\n{command}\n```" for command in item["next_commands"])
        lines.append("")
    return "\n".join(lines)


def build_authoring_queue(
    candidates_path: Path,
    coverage_path: Path,
    tasks_path: Path,
    output_path: Path,
    markdown_path: Path,
    per_gap: int,
    reject_used_repo_overlap: bool = False,
) -> dict[str, Any]:
    assert_not_under_formal_task_root(output_path, purpose="Authoring queue JSON")
    assert_not_under_formal_task_root(markdown_path, purpose="Authoring queue Markdown")
    candidates = list(read_jsonl(candidates_path))
    coverage = load_json(coverage_path)
    used_repos = used_repo_ids(tasks_path)
    input_audit = audit_queue_inputs(candidates, used_repos, reject_used_repo_overlap=reject_used_repo_overlap)
    if not input_audit["passed"]:
        raise ValueError("authoring queue input audit failed: " + "; ".join(input_audit["issues"]))
    items = build_queue_items(candidates, coverage, used_repos, per_gap)
    website_type_counts = dict(sorted(Counter(item["website_type"] for item in items).items()))
    gap_counts = dict(sorted(Counter(item["target_gap"] for item in items).items()))
    summary = {
        "candidates_path": str(candidates_path),
        "coverage_path": str(coverage_path),
        "tasks_path": str(tasks_path),
        "total": len(items),
        "per_gap": per_gap,
        "used_repo_count": len(used_repos),
        "artifact_type": "authoring_queue",
        "formal_task_record": False,
        "formal_task_records": 0,
        "accepted_task_records": 0,
        "input_audit": input_audit,
        "warning": "Authoring queue items are candidate inspection prompts only. Formal task.jsonl rows must be authored one at a time and frozen separately.",
        "website_type_counts": website_type_counts,
        "gap_counts": gap_counts,
        "items": items,
    }
    write_json(output_path, summary)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_markdown(items, coverage), encoding="utf-8")
    return summary


def add_queue_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--candidates", type=Path, default=DEFAULT_OUTPUT_ROOT / "manifest" / "candidates.jsonl")
    parser.add_argument("--coverage", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.coverage_audit.json")
    parser.add_argument("--tasks", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.jsonl")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "authoring_queue" / "dev_next.json")
    parser.add_argument("--markdown", type=Path, default=DEFAULT_OUTPUT_ROOT / "authoring_queue" / "dev_next.md")
    parser.add_argument("--per-gap", type=int, default=2)
    parser.add_argument(
        "--reject-used-repo-overlap",
        action="store_true",
        help="Strict audit mode: fail if the candidate file contains repos from --tasks instead of only filtering them out.",
    )


def run_queue_from_args(args: argparse.Namespace) -> None:
    try:
        summary = build_authoring_queue(
            candidates_path=args.candidates,
            coverage_path=args.coverage,
            tasks_path=args.tasks,
            output_path=args.output,
            markdown_path=args.markdown,
            per_gap=args.per_gap,
            reject_used_repo_overlap=args.reject_used_repo_overlap,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(
        f"built authoring queue with {summary['total']} items "
        f"across gaps {summary['gap_counts']}"
    )
