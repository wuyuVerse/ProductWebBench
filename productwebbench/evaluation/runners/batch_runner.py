from __future__ import annotations

import argparse
import concurrent.futures
import difflib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from ...core.config import DEFAULT_OUTPUT_ROOT, workspace_meta_path
from ...core.formal_data_guard import assert_not_under_formal_task_root
from ...core.io_utils import ensure_dir, read_jsonl, write_json
from ...core.task_files import task_file_patterns
from ..scoring.leaderboard import leaderboard as build_leaderboard_report
from .model_providers import ChatRequest, OpenAICompatibleProvider


DEFAULT_MODELS = {
    "kimi_k26": "Pro/moonshotai/Kimi-K2.6",
    "glm_51": "Pro/zai-org/GLM-5.1",
    "minimax_m25": "Pro/MiniMaxAI/MiniMax-M2.5",
}

TEXT_SUFFIXES = {
    ".astro",
    ".blade.php",
    ".cjs",
    ".css",
    ".ejs",
    ".handlebars",
    ".hbs",
    ".htm",
    ".html",
    ".js",
    ".jsx",
    ".json",
    ".less",
    ".liquid",
    ".md",
    ".mdx",
    ".mjs",
    ".njk",
    ".php",
    ".pug",
    ".sass",
    ".scss",
    ".svelte",
    ".toml",
    ".ts",
    ".tsx",
    ".vue",
    ".yaml",
    ".yml",
}
SKIP_PARTS = {".git", ".next", ".nuxt", "build", "dist", "node_modules", "storage", "vendor"}
LOCKFILES = {"package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb", "composer.lock"}


@dataclass(frozen=True)
class RunnerConfig:
    base_url: str
    api_key: str
    output_root: Path
    tasks_path: Path
    leaderboard_tasks_path: Path | None
    specs_path: Path
    bench_root: Path
    workspace_root: Path
    state_plan: Path
    design_anchors: Path
    models: dict[str, str]
    build_capabilities: Path | None = DEFAULT_OUTPUT_ROOT / "tasks" / "dev_next_build_capabilities.json"
    request_workers: int = 4
    eval_workers: int = 2
    limit: int | None = None
    force: bool = False
    patch_retry: bool = True
    max_files: int = 10
    max_context_chars: int = 28000
    max_tokens: int = 5000
    # api_timeout 之前是 900s → 单请求容忍 15 分钟。sidecar 实测直连 1-4s,15min 是异常挂断的兜底,
    # 但 3 retry × 900s = 46min/次 API call → 6 连败 4.6h/cell wall clock。缩到 180s 后 6 连败降到 ~40min/cell。
    # 允许 env override 应对个别慢模型。
    api_timeout: int = int(__import__("os").environ.get("MB_API_TIMEOUT", "180"))
    api_retries: int = int(__import__("os").environ.get("MB_API_RETRIES", "1"))
    eval_skip_build: bool = False
    build_timeout: int = 900
    server_timeout: int = 120
    capture_timeout: int = 360
    port_start: int = 3100
    port_stride: int = 100
    normalize_asset_paths: bool = True
    generation_protocol: str = "search_replace"


@dataclass
class EvalStatus:
    task_id: str
    repo_id: str
    model_slug: str
    model: str
    phase: str = "created"
    patch_applied: bool = False
    patch_retry_used: bool = False
    changed: bool = False
    generation_ok: bool = False
    capture_ok: bool = False
    capture_status: str | None = None
    capture_failure_stage: str | None = None
    raw_wcs_rate: float | None = None
    normalized_wcs_rate: float | None = None
    raw_passed: int = 0
    normalized_passed: int = 0
    failure_type: str | None = None
    error: str | None = None
    api_latency_sec: float | None = None
    api_usage: dict[str, Any] = field(default_factory=dict)


def load_specs(path: Path) -> dict[str, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("tasks"), list):
        return {item["task_id"]: item for item in data["tasks"]}
    if isinstance(data, list):
        return {item["task_id"]: item for item in data}
    raise ValueError(f"unsupported spec file shape: {path}")


def load_build_capabilities(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    repos = data.get("repos", {})
    return repos if isinstance(repos, dict) else {}


def baseline_build_ok(config: RunnerConfig, repo_id: str) -> bool | None:
    entry = load_build_capabilities(config.build_capabilities).get(repo_id)
    if entry is None:
        return None
    return bool(entry.get("build_ok"))


def status_path(output_root: Path, model_slug: str, task_id: str) -> Path:
    return output_root / model_slug / task_id / "status.json"


def task_run_dir(output_root: Path, model_slug: str, task_id: str) -> Path:
    return output_root / model_slug / task_id


def save_status(config: RunnerConfig, status: EvalStatus) -> None:
    write_json(status_path(config.output_root, status.model_slug, status.task_id), asdict(status))


def load_existing_status(config: RunnerConfig, model_slug: str, task_id: str) -> EvalStatus | None:
    path = status_path(config.output_root, model_slug, task_id)
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    allowed = {item.name for item in fields(EvalStatus)}
    return EvalStatus(**{key: value for key, value in raw.items() if key in allowed})


def project_root_for_workspace(workspace: Path) -> Path:
    meta_path = workspace_meta_path(workspace)
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        project_root = Path(meta.get("project_root", ""))
        if project_root.exists() and str(project_root).startswith(str(workspace)):
            return project_root
    package_jsons = [
        path
        for path in workspace.rglob("package.json")
        if not any(part in SKIP_PARTS for part in path.relative_to(workspace).parts)
    ]
    if package_jsons:
        return sorted(package_jsons, key=lambda path: len(path.relative_to(workspace).parts))[0].parent
    children = [path for path in workspace.iterdir() if path.is_dir()]
    return children[0] if len(children) == 1 else workspace


def copy_workspace(source_workspace: Path, destination_parent: Path, repo_id: str) -> Path:
    destination = destination_parent / repo_id
    if destination.exists():
        return destination
    ensure_dir(destination_parent)
    subprocess.run(["cp", "-a", "--reflink=auto", str(source_workspace), str(destination)], check=True)
    meta_path = destination / ".productwebbench_workspace.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["workspace"] = str(destination)
        meta["project_root"] = str(project_root_for_workspace(destination))
        write_json(meta_path, meta)
    return destination


def candidate_files(project_root: Path, patterns: list[str], max_files: int, max_chars: int) -> list[tuple[str, str]]:
    paths: list[Path] = []
    for pattern in patterns:
        matches = sorted(project_root.glob(pattern)) if any(ch in pattern for ch in "*?[") else [project_root / pattern]
        for path in matches:
            if not path.is_file():
                continue
            rel = path.relative_to(project_root)
            if any(part in SKIP_PARTS for part in rel.parts):
                continue
            if path.name in LOCKFILES:
                continue
            if not (path.suffix in TEXT_SUFFIXES or path.name.endswith(".blade.php")):
                continue
            if path not in paths:
                paths.append(path)
    if not paths:
        paths = sorted(
            path
            for path in project_root.rglob("*")
            if path.is_file()
            and not any(part in SKIP_PARTS for part in path.relative_to(project_root).parts)
            and (path.suffix in TEXT_SUFFIXES or path.name.endswith(".blade.php"))
        )[:max_files]

    out: list[tuple[str, str]] = []
    used = 0
    for path in paths:
        if len(out) >= max_files or used >= max_chars:
            break
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        remaining = max_chars - used
        if len(text) > remaining:
            text = text[:remaining] + "\n/* TRUNCATED */\n"
        out.append((str(path.relative_to(project_root)), text))
        used += len(text)
    return out


def build_prompt(task: dict, readme: str, files: list[tuple[str, str]]) -> str:
    sections = []
    for relpath, text in files:
        sections.append(f"### FILE: {relpath}\n```\n{text}\n```")
    return (
        "You are a coding agent solving an existing frontend website-change benchmark task.\n"
        "Return ONLY a valid unified diff patch. No markdown fences. No explanation.\n"
        "Keep edits repo-native and scoped. Do not edit lockfiles, generated outputs, node_modules, vendor, or cache/storage files.\n"
        "Preserve existing routes and regression states unless the task explicitly asks otherwise.\n\n"
        f"Task id: {task['task_id']}\nRepo id: {task['repo_id']}\n\n"
        f"Task README:\n{readme[:9000]}\n\n"
        "Relevant current files:\n"
        + "\n\n".join(sections)
        + "\n\nReturn the unified diff patch now.\n"
    )


def build_file_replacement_prompt(task: dict, readme: str, files: list[tuple[str, str]]) -> str:
    file_list = "\n".join(f"- {relpath}" for relpath, _ in files)
    sections = []
    for relpath, text in files:
        sections.append(f"### FILE: {relpath}\n```\n{text}\n```")
    return (
        "You are solving one existing frontend website-change benchmark task.\n"
        "Return ONLY a JSON object with this exact shape:\n"
        '{"edits":[{"path":"relative/path.ext","content":"complete updated file content"}]}\n'
        "The content must be the full updated content for each changed file, not a diff and not a snippet. "
        "You may create a new safe source file by returning a new relative source path with full content.\n"
        "Prefer the files listed below, but you may edit another existing safe source file if it is the correct implementation location. "
        "Do not edit lockfiles, generated outputs, node_modules, vendor, or cache/storage files.\n\n"
        f"Task id: {task['task_id']}\nRepo id: {task['repo_id']}\n\n"
        f"Preferred files:\n{file_list}\n\n"
        f"Task README:\n{readme[:9000]}\n\n"
        "Current files:\n"
        + "\n\n".join(sections)
        + "\n\nReturn the JSON object now.\n"
    )


def build_search_replace_prompt(task: dict, readme: str, files: list[tuple[str, str]]) -> str:
    file_list = "\n".join(f"- {relpath}" for relpath, _ in files)
    sections = []
    for relpath, text in files:
        sections.append(f"### FILE: {relpath}\n```\n{text}\n```")
    return (
        "You are solving one existing frontend website-change benchmark task.\n"
        "Return ONLY a JSON object with this exact shape:\n"
        '{"edits":[{"path":"relative/path.ext","old":"exact existing text to replace","new":"replacement text"}]}\n'
        "Rules:\n"
        "- `old` must be an exact contiguous substring from the current file.\n"
        "- To create a new safe source file, use `old` as an empty string and `new` as the complete file content.\n"
        "- `new` should be the minimal replacement needed for the task.\n"
        "- Prefer small localized edits over replacing entire files.\n"
        "- Prefer the files listed below, but you may edit another existing safe source file if it is the correct implementation location.\n"
        "- Do not edit lockfiles, generated outputs, node_modules, vendor, storage, or cache files.\n\n"
        f"Task id: {task['task_id']}\nRepo id: {task['repo_id']}\n\n"
        f"Preferred files:\n{file_list}\n\n"
        f"Task README:\n{readme[:9000]}\n\n"
        "Current files:\n"
        + "\n\n".join(sections)
        + "\n\nReturn the JSON object now.\n"
    )


def extract_patch(text: str) -> str:
    fenced = re.search(r"```(?:diff|patch)?\s*(.*?)```", text, re.S | re.I)
    if fenced:
        text = fenced.group(1)
    for marker in ("diff --git ", "--- "):
        idx = text.find(marker)
        if idx >= 0:
            return text[idx:].strip() + "\n"
    return text.strip() + "\n"


def extract_json_object(text: str) -> dict[str, Any] | None:
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S | re.I)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def paths_from_apply_log(apply_log: str, allowed_paths: set[str]) -> list[str]:
    patterns = [
        r"in ([^\s]+)$",
        r"file: ([^\s]+)",
        r"path is not a safe source file: ([^\s]+)",
        r"path is not allowed: ([^\s]+)",
        r"target does not exist: ([^\s]+)",
    ]
    paths: list[str] = []
    for line in apply_log.splitlines():
        for pattern in patterns:
            match = re.search(pattern, line.strip())
            if match:
                paths.append(match.group(1).strip())
    paths.extend(sorted(allowed_paths))
    seen = set()
    unique = []
    for path in paths:
        if path and path not in seen:
            seen.add(path)
            unique.append(path)
    return unique[:6]


def retry_file_context(project_root: Path, paths: list[str], max_chars: int = 18000) -> str:
    sections = []
    used = 0
    for relpath in paths:
        target = (project_root / relpath).resolve()
        if not str(target).startswith(str(project_root.resolve())) or not target.is_file():
            continue
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        remaining = max_chars - used
        if remaining <= 0:
            break
        if len(text) > remaining:
            text = text[:remaining] + "\n/* TRUNCATED */\n"
        sections.append(f"### CURRENT FILE: {relpath}\n```\n{text}\n```")
        used += len(text)
    return "\n\n".join(sections)


def files_from_context(project_root: Path, paths: list[str], max_files: int = 4, max_chars: int = 24000) -> list[tuple[str, str]]:
    files = []
    used = 0
    for relpath in paths:
        target = (project_root / relpath).resolve()
        if not str(target).startswith(str(project_root.resolve())) or not target.is_file():
            continue
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        remaining = max_chars - used
        if remaining <= 0 or len(files) >= max_files:
            break
        if len(text) > remaining:
            text = text[:remaining] + "\n/* TRUNCATED */\n"
        files.append((relpath, text))
        used += len(text)
    return files


def apply_file_replacements(project_root: Path, response_text: str, allowed_paths: set[str]) -> tuple[bool, str]:
    data = extract_json_object(response_text)
    if data is None:
        return False, "model response did not contain a JSON object"
    edits = data.get("edits")
    if not isinstance(edits, list) or not edits:
        return False, "JSON object does not contain a non-empty edits list"
    logs = []
    changed = False
    for edit in edits:
        if not isinstance(edit, dict):
            return False, "edit entry is not an object"
        relpath = edit.get("path")
        content = edit.get("content")
        if not isinstance(relpath, str) or not isinstance(content, str):
            return False, "edit path/content must be strings"
        target = (project_root / relpath).resolve()
        if not str(target).startswith(str(project_root.resolve())):
            return False, f"edit escapes project root: {relpath}"
        if relpath not in allowed_paths and not safe_edit_path(project_root, relpath, allow_missing=True):
            return False, f"edit path is not a safe source file: {relpath}"
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            before = ""
        else:
            before = target.read_text(encoding="utf-8")
        if before != content:
            target.write_text(content, encoding="utf-8")
            changed = True
            diff = difflib.unified_diff(
                before.splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile=f"a/{relpath}",
                tofile=f"b/{relpath}",
            )
            logs.append("".join(diff))
    return changed, "\n".join(logs) if logs else "no file content changed"


def apply_search_replacements(project_root: Path, response_text: str, allowed_paths: set[str]) -> tuple[bool, str]:
    data = extract_json_object(response_text)
    if data is None:
        return False, "model response did not contain a JSON object"
    edits = data.get("edits")
    if not isinstance(edits, list) or not edits:
        return False, "JSON object does not contain a non-empty edits list"
    file_updates: dict[str, str] = {}
    originals: dict[str, str] = {}
    for edit in edits:
        if not isinstance(edit, dict):
            return False, "edit entry is not an object"
        relpath = edit.get("path")
        old = edit.get("old")
        new = edit.get("new")
        if not all(isinstance(value, str) for value in (relpath, old, new)):
            return False, "edit path/old/new must be strings"
        target = (project_root / relpath).resolve()
        if not str(target).startswith(str(project_root.resolve())):
            return False, f"edit escapes project root: {relpath}"
        if relpath not in allowed_paths and not safe_edit_path(project_root, relpath, allow_missing=old == ""):
            return False, f"edit path is not a safe source file: {relpath}"
        if not target.exists():
            if old == "":
                target.parent.mkdir(parents=True, exist_ok=True)
                current = ""
                originals[relpath] = current
                file_updates[relpath] = new
                continue
            return False, f"edit target does not exist: {relpath}"
        current = file_updates.get(relpath)
        if current is None:
            current = target.read_text(encoding="utf-8")
            originals[relpath] = current
        if old not in current:
            return False, f"old text not found in {relpath}"
        current = current.replace(old, new, 1)
        file_updates[relpath] = current

    logs = []
    changed = False
    for relpath, content in file_updates.items():
        before = originals[relpath]
        if before == content:
            continue
        (project_root / relpath).write_text(content, encoding="utf-8")
        changed = True
        logs.append(
            "".join(
                difflib.unified_diff(
                    before.splitlines(keepends=True),
                    content.splitlines(keepends=True),
                    fromfile=f"a/{relpath}",
                    tofile=f"b/{relpath}",
                )
            )
        )
    return changed, "\n".join(logs) if logs else "no file content changed"


def safe_edit_path(project_root: Path, relpath: str, allow_missing: bool = False) -> bool:
    if relpath.startswith("/") or ".." in Path(relpath).parts:
        return False
    path = project_root / relpath
    parts = set(Path(relpath).parts)
    if parts & SKIP_PARTS:
        return False
    if path.name in LOCKFILES:
        return False
    is_text = path.suffix in TEXT_SUFFIXES or path.name.endswith(".blade.php")
    if not is_text:
        return False
    if path.exists():
        return path.is_file()
    if not allow_missing:
        return False
    existing_ancestor = path.parent
    while existing_ancestor != project_root and not existing_ancestor.exists():
        existing_ancestor = existing_ancestor.parent
    return existing_ancestor.exists() and str(existing_ancestor.resolve()).startswith(str(project_root.resolve()))


def apply_model_patch(project_root: Path, patch_path: Path) -> tuple[bool, str]:
    patch_path = patch_path.resolve()
    commands = [
        ["git", "apply", str(patch_path)],
        ["git", "apply", "--unsafe-paths", str(patch_path)],
        ["patch", "-p1", "--forward", "--batch", "-i", str(patch_path)],
    ]
    logs = []
    for command in commands:
        proc = subprocess.run(command, cwd=project_root, text=True, capture_output=True, timeout=90)
        logs.append("$ " + " ".join(command) + "\n" + proc.stdout + proc.stderr)
        if proc.returncode == 0:
            return True, "\n".join(logs)
    return False, "\n".join(logs)


def project_diff(reference_project: Path, submission_project: Path) -> str:
    proc = subprocess.run(
        [
            "diff",
            "-ru",
            "--exclude",
            "node_modules",
            "--exclude",
            "vendor",
            "--exclude",
            "storage",
            str(reference_project),
            str(submission_project),
        ],
        text=True,
        capture_output=True,
        timeout=120,
    )
    return proc.stdout


def write_task_files(run_dir: Path, task: dict, spec: dict, normalize_asset_paths: bool) -> tuple[Path, Path]:
    assert_not_under_formal_task_root(run_dir, purpose="Evaluation run task snapshot")
    task_path = run_dir / "task.jsonl"
    spec_path = run_dir / ("submission_spec.normalized.json" if normalize_asset_paths else "submission_spec.raw.json")
    task_path.write_text(json.dumps(task, ensure_ascii=False) + "\n", encoding="utf-8")
    spec_copy = json.loads(json.dumps(spec))
    if normalize_asset_paths:
        signals = spec_copy.get("asset_path_signals") or []
        additions = [signal.replace("/assets/json/img/", "/assets/img/") for signal in signals if "/assets/json/img/" in signal]
        if additions:
            spec_copy["asset_path_signals"] = sorted(set(signals + additions))
    write_json(spec_path, {"tasks": [spec_copy]})
    return task_path, spec_path


def run_command(command: list[str], cwd: Path, timeout: int, env: dict[str, str] | None = None) -> tuple[int, str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    try:
        proc = subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=timeout, env=merged_env)
        return proc.returncode, proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return 124, stdout + stderr + f"\ncommand timed out after {timeout}s\n"


def load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def generate_patch(config: RunnerConfig, task: dict, spec: dict, model_slug: str, model: str) -> EvalStatus:
    existing = load_existing_status(config, model_slug, task["task_id"])
    if existing and existing.patch_applied and existing.changed and not config.force:
        return existing

    run_dir = task_run_dir(config.output_root, model_slug, task["task_id"])
    if config.force and run_dir.exists():
        shutil.rmtree(run_dir)
    ensure_dir(run_dir)
    status = EvalStatus(task_id=task["task_id"], repo_id=task["repo_id"], model_slug=model_slug, model=model, phase="generation")
    save_status(config, status)

    source_workspace = config.workspace_root / task["repo_id"]
    if not source_workspace.exists():
        status.phase = "failed"
        status.error = f"missing workspace: {source_workspace}"
        save_status(config, status)
        return status

    workspace_parent = run_dir / "workspace"
    submission_workspace = copy_workspace(source_workspace, workspace_parent, task["repo_id"])
    project_root = project_root_for_workspace(submission_workspace)
    reference_project = project_root_for_workspace(source_workspace)

    readme_path = config.bench_root / task["task_id"] / "README.md"
    readme = readme_path.read_text(encoding="utf-8") if readme_path.exists() else json.dumps(task, ensure_ascii=False)
    files = candidate_files(project_root, task_file_patterns(task), config.max_files, config.max_context_chars)
    if config.generation_protocol == "diff":
        prompt = build_prompt(task, readme, files)
    elif config.generation_protocol == "search_replace":
        prompt = build_search_replace_prompt(task, readme, files)
    elif config.generation_protocol == "file_replacement":
        prompt = build_file_replacement_prompt(task, readme, files)
    else:
        raise ValueError(f"unknown generation protocol: {config.generation_protocol}")
    (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

    provider = OpenAICompatibleProvider(config.base_url, config.api_key, config.api_timeout, max_retries=config.api_retries)
    # Modality C (design-fidelity): when PWB_MODALITY_C=1, attach up to N reference
    # design screenshots so a VLM solver can SEE the visual target. Default off
    # (modality A, text-only) so existing runs are unchanged. Screenshots live in
    # data/productwebbench/states_slot_*_reference/<repo_id>/<state>/screenshot.png.
    images_b64: tuple = ()
    if os.environ.get("PWB_MODALITY_C") == "1":
        import base64 as _b64
        import glob as _glob
        data_root = config.workspace_root.parent  # .../data/productwebbench
        cand = _glob.glob(str(data_root / "states_slot_*_reference" / task["repo_id"] / "*" / "screenshot.png"))
        n = int(os.environ.get("PWB_MODALITY_C_MAX", "4"))
        shots = sorted(cand)[:n]
        images_b64 = tuple(_b64.b64encode(open(p, "rb").read()).decode() for p in shots)
        (run_dir / "modalityC_images.txt").write_text("\n".join(shots), encoding="utf-8")
    response = provider.complete(ChatRequest(model=model, prompt=prompt, max_tokens=config.max_tokens, images_b64=images_b64))
    status.api_latency_sec = response.latency_sec
    status.api_usage = response.usage
    write_json(run_dir / "api_response.json", {k: v for k, v in asdict(response).items() if k != "content"})
    (run_dir / "model_response.txt").write_text(response.content or response.error, encoding="utf-8")
    if not response.ok:
        status.phase = "failed"
        status.failure_type = "provider_timeout" if "timed out" in response.error.lower() else "provider_error"
        status.error = response.error
        save_status(config, status)
        return status

    allowed_paths = {relpath for relpath, _ in files}
    if config.generation_protocol == "diff":
        patch_path = run_dir / "model.patch"
        patch_path.write_text(extract_patch(response.content), encoding="utf-8")
        applied, apply_log = apply_model_patch(project_root, patch_path)
    elif config.generation_protocol == "search_replace":
        applied, apply_log = apply_search_replacements(project_root, response.content, allowed_paths)
    else:
        applied, apply_log = apply_file_replacements(project_root, response.content, allowed_paths)
    (run_dir / "apply.log").write_text(apply_log, encoding="utf-8")

    if not applied and config.patch_retry:
        status.patch_retry_used = True
        retry_context = retry_file_context(project_root, paths_from_apply_log(apply_log, allowed_paths))
        if config.generation_protocol == "diff":
            retry_prompt = (
                "Your previous patch did not apply. Return ONLY a corrected valid unified diff patch.\n\n"
                f"Apply error:\n{apply_log[-3000:]}\n\nCurrent file context:\n{retry_context}\n\nPrevious response:\n{response.content[:6000]}\n"
            )
        elif config.generation_protocol == "search_replace":
            retry_prompt = (
                "Your previous JSON search/replace response could not be applied. Return ONLY a valid JSON object with shape "
                '{"edits":[{"path":"relative/path.ext","old":"exact existing text to replace","new":"replacement text"}]}.\n'
                "`old` must be an exact contiguous substring from the provided file. Do not return markdown or explanation.\n\n"
                f"Apply error:\n{apply_log[-3000:]}\n\nCurrent file context:\n{retry_context}\n\nPrevious response:\n{response.content[:6000]}\n"
            )
        else:
            retry_prompt = (
                "Your previous JSON edit response could not be applied. Return ONLY a valid JSON object with shape "
                '{"edits":[{"path":"relative/path.ext","content":"complete updated file content"}]}.\n'
                "Do not return a diff, markdown, or explanation.\n\n"
                f"Apply error:\n{apply_log[-3000:]}\n\nCurrent file context:\n{retry_context}\n\nPrevious response:\n{response.content[:6000]}\n"
            )
        retry = provider.complete(ChatRequest(model=model, prompt=retry_prompt, max_tokens=config.max_tokens))
        write_json(run_dir / "api_response.retry.json", {k: v for k, v in asdict(retry).items() if k != "content"})
        (run_dir / "model_response.retry.txt").write_text(retry.content or retry.error, encoding="utf-8")
        if retry.ok:
            if config.generation_protocol == "diff":
                retry_patch = run_dir / "model.retry.patch"
                retry_patch.write_text(extract_patch(retry.content), encoding="utf-8")
                applied, retry_log = apply_model_patch(project_root, retry_patch)
            elif config.generation_protocol == "search_replace":
                applied, retry_log = apply_search_replacements(project_root, retry.content, allowed_paths)
            else:
                applied, retry_log = apply_file_replacements(project_root, retry.content, allowed_paths)
            (run_dir / "apply.retry.log").write_text(retry_log, encoding="utf-8")

        if not applied and config.generation_protocol == "search_replace":
            fallback_paths = paths_from_apply_log(apply_log, allowed_paths)
            fallback_files = files_from_context(project_root, fallback_paths)
            if fallback_files:
                fallback_prompt = build_file_replacement_prompt(task, readme, fallback_files)
                fallback_prompt += (
                    "\n\nYour previous search/replace edits did not apply because exact `old` text was not found. "
                    "Return full updated content only for the listed file(s) that need changes. "
                    "Do not include unchanged files unless they are required for the task.\n"
                )
                (run_dir / "prompt.file_fallback.txt").write_text(fallback_prompt, encoding="utf-8")
                fallback = provider.complete(ChatRequest(model=model, prompt=fallback_prompt, max_tokens=config.max_tokens))
                write_json(run_dir / "api_response.file_fallback.json", {k: v for k, v in asdict(fallback).items() if k != "content"})
                (run_dir / "model_response.file_fallback.txt").write_text(fallback.content or fallback.error, encoding="utf-8")
                if fallback.ok:
                    applied, fallback_log = apply_file_replacements(project_root, fallback.content, {relpath for relpath, _ in fallback_files})
                    (run_dir / "apply.file_fallback.log").write_text(fallback_log, encoding="utf-8")

    diff_text = project_diff(reference_project, project_root)
    (run_dir / "applied.diff").write_text(diff_text, encoding="utf-8")
    prediction = {
        "instance_id": task["task_id"],
        "task_id": task["task_id"],
        "repo_id": task["repo_id"],
        "model_name_or_path": model,
        "model_patch": diff_text,
        "generation_protocol": config.generation_protocol,
    }
    write_json(run_dir / "prediction.json", prediction)
    status.generation_ok = True
    status.patch_applied = applied
    status.changed = bool(diff_text.strip())
    status.phase = "patch_ready" if applied and status.changed else "failed"
    if status.phase == "failed":
        status.failure_type = "model_patch_failed"
        status.error = "patch did not apply or produced no source diff"
    save_status(config, status)
    return status


def evaluate_patch(config: RunnerConfig, task: dict, spec: dict, model_slug: str, worker_index: int) -> EvalStatus:
    status = load_existing_status(config, model_slug, task["task_id"])
    if status is None:
        raise RuntimeError(f"missing generation status for {model_slug}/{task['task_id']}")
    if status.phase == "done" and not config.force:
        return status
    if not status.patch_applied or not status.changed:
        return status

    run_dir = task_run_dir(config.output_root, model_slug, task["task_id"])
    task_path, raw_spec_path = write_task_files(run_dir, task, spec, normalize_asset_paths=False)
    _, normalized_spec_path = write_task_files(run_dir, task, spec, normalize_asset_paths=config.normalize_asset_paths)

    states_root = run_dir / "states"
    port_start = config.port_start + worker_index * config.port_stride
    env = {
        "PRODUCTWEBBENCH_PORT_START": str(port_start),
        "PRODUCTWEBBENCH_PORT_END": str(port_start + config.port_stride - 1),
    }
    skip_build = config.eval_skip_build or baseline_build_ok(config, task["repo_id"]) is False
    capture_command = [
        "python3",
        "-m",
        "productwebbench",
        "capture-states",
        task["repo_id"],
        "--workspace-root",
        str(run_dir / "workspace"),
        "--output-root",
        str(states_root),
        "--state-plan",
        str(config.state_plan),
        "--skip-install",
    ]
    if skip_build:
        capture_command.append("--skip-build")
    capture_command += [
        "--build-timeout",
        str(config.build_timeout),
        "--server-timeout",
        str(config.server_timeout),
        "--capture-timeout",
        str(config.capture_timeout),
        "--no-server-lock",
    ]
    status.phase = "capture"
    save_status(config, status)
    rc, output = run_command(capture_command, Path.cwd(), config.build_timeout + config.server_timeout + config.capture_timeout + 180, env=env)
    (run_dir / "capture.log").write_text(output, encoding="utf-8")
    capture_report = load_json_if_exists(states_root / task["repo_id"] / "capture_report.json")
    status.capture_status = capture_report.get("status")
    status.capture_failure_stage = capture_report.get("failure_stage")
    status.capture_ok = rc == 0 and status.capture_status == "passed"
    if rc != 0:
        status.phase = "failed"
        status.error = "capture failed"
        save_status(config, status)
        return status

    for label, spec_path in (("raw", raw_spec_path), ("normalized", normalized_spec_path)):
        report_path = run_dir / f"submission_results.{label}.json"
        score_path = run_dir / f"score_report.{label}.json"
        verify_command = [
            "python3",
            "-m",
            "productwebbench",
            "verify-submission",
            "--tasks",
            str(task_path),
            "--specs",
            str(spec_path),
            "--states-root",
            str(states_root),
            "--design-anchors",
            str(config.design_anchors),
            "--output",
            str(report_path),
        ]
        rc, output = run_command(verify_command, Path.cwd(), 180)
        (run_dir / f"verify.{label}.log").write_text(output, encoding="utf-8")
        if report_path.exists():
            report = json.loads(report_path.read_text(encoding="utf-8"))
            setattr(status, f"{label}_passed", int(report.get("passed", 0)))
        if report_path.exists():
            run_command(["python3", "-m", "productwebbench", "score-report", "--report", str(report_path), "--output", str(score_path)], Path.cwd(), 120)
            if score_path.exists():
                score = json.loads(score_path.read_text(encoding="utf-8"))
                setattr(status, f"{label}_wcs_rate", score.get("wcs_rate"))

    status.phase = "done"
    status.error = None
    save_status(config, status)
    return status


def summarize(config: RunnerConfig, statuses: list[EvalStatus]) -> dict[str, Any]:
    summary: dict[str, Any] = {"total": len(statuses), "models": list(config.models), "items": [asdict(item) for item in statuses]}
    predictions = []
    for item in statuses:
        prediction_path = task_run_dir(config.output_root, item.model_slug, item.task_id) / "prediction.json"
        if prediction_path.exists():
            prediction = json.loads(prediction_path.read_text(encoding="utf-8"))
            predictions.append(prediction)
    if predictions:
        predictions_path = config.output_root / "predictions.jsonl"
        predictions_path.write_text(
            "".join(json.dumps(prediction, ensure_ascii=False) + "\n" for prediction in predictions),
            encoding="utf-8",
        )
        summary["predictions_path"] = str(predictions_path)
    for slug in config.models:
        rows = [item for item in statuses if item.model_slug == slug]
        summary[slug] = {
            "total": len(rows),
            "done": sum(item.phase == "done" for item in rows),
            "failed": sum(item.phase == "failed" for item in rows),
            "provider_failed": sum(item.failure_type in {"provider_timeout", "provider_error"} for item in rows),
            "provider_timeout": sum(item.failure_type == "provider_timeout" for item in rows),
            "model_patch_failed": sum(item.failure_type == "model_patch_failed" for item in rows),
            "capture_failed": sum(item.phase == "done" and item.capture_status not in (None, "passed") for item in rows),
            "patch_applied": sum(item.patch_applied for item in rows),
            "retry_used": sum(item.patch_retry_used for item in rows),
            "raw_passed": sum(item.raw_passed for item in rows),
            "normalized_passed": sum(item.normalized_passed for item in rows),
        }
    write_json(config.output_root / "summary.json", summary)
    build_leaderboard_report(
        config.output_root,
        config.leaderboard_tasks_path or config.tasks_path,
        config.output_root / "leaderboard",
        config.build_capabilities,
    )
    return summary


def run_batch(config: RunnerConfig) -> dict[str, Any]:
    ensure_dir(config.output_root)
    tasks = list(read_jsonl(config.tasks_path))
    if config.limit is not None:
        tasks = tasks[: config.limit]
    specs = load_specs(config.specs_path)
    task_by_id = {task["task_id"]: task for task in tasks}

    generation_jobs = [(task, specs[task["task_id"]], slug, model) for task in tasks for slug, model in config.models.items()]
    generated: list[EvalStatus] = []
    final_statuses: dict[tuple[str, str], EvalStatus] = {}
    eval_futures: dict[concurrent.futures.Future[EvalStatus], tuple[str, str]] = {}
    eval_index = 0

    with (
        concurrent.futures.ThreadPoolExecutor(max_workers=config.request_workers) as request_pool,
        concurrent.futures.ThreadPoolExecutor(max_workers=config.eval_workers) as eval_pool,
    ):
        generation_futures = {
            request_pool.submit(generate_patch, config, task, spec, slug, model): (slug, task["task_id"])
            for task, spec, slug, model in generation_jobs
        }
        for future in concurrent.futures.as_completed(generation_futures):
            slug, task_id = generation_futures[future]
            status = future.result()
            key = (slug, task_id)
            generated.append(status)
            final_statuses[key] = status
            write_json(config.output_root / "summary.generation.partial.json", [asdict(item) for item in generated])
            print(json.dumps(asdict(status), ensure_ascii=False), flush=True)

            if status.phase != "done" and status.patch_applied and status.changed:
                task = task_by_id[task_id]
                spec = specs[task_id]
                worker_index = eval_index % max(1, config.eval_workers)
                eval_index += 1
                eval_futures[eval_pool.submit(evaluate_patch, config, task, spec, slug, worker_index)] = key

        evaluated: list[EvalStatus] = []
        for future in concurrent.futures.as_completed(eval_futures):
            key = eval_futures[future]
            status = future.result()
            evaluated.append(status)
            final_statuses[key] = status
            write_json(config.output_root / "summary.evaluation.partial.json", [asdict(item) for item in evaluated])
            print(json.dumps(asdict(status), ensure_ascii=False), flush=True)

    merged_statuses: dict[tuple[str, str], EvalStatus] = {}
    for path in sorted(config.output_root.glob("*/*/status.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        allowed = {item.name for item in fields(EvalStatus)}
        status = EvalStatus(**{key: value for key, value in raw.items() if key in allowed})
        merged_statuses[(status.model_slug, status.task_id)] = status
    merged_statuses.update(final_statuses)
    return summarize(config, list(merged_statuses.values()))


def should_reevaluate(status: EvalStatus, mode: str) -> bool:
    if not status.patch_applied or not status.changed:
        return False
    if mode == "all":
        return True
    if mode == "failed":
        return not bool(status.normalized_passed)
    if mode == "capture_failed":
        return (
            status.capture_failure_stage == "capture"
            and status.capture_status not in (None, "passed")
            and not status.normalized_passed
        )
    raise ValueError(f"unsupported reevaluate mode: {mode}")


def run_reevaluate_existing(config: RunnerConfig, mode: str) -> dict[str, Any]:
    ensure_dir(config.output_root)
    tasks = list(read_jsonl(config.tasks_path))
    if config.limit is not None:
        tasks = tasks[: config.limit]
    specs = load_specs(config.specs_path)
    jobs = []
    for task in tasks:
        for slug, _model in config.models.items():
            status = load_existing_status(config, slug, task["task_id"])
            if status and should_reevaluate(status, mode):
                jobs.append((task, specs[task["task_id"]], slug))

    final_statuses: dict[tuple[str, str], EvalStatus] = {}
    for path in sorted(config.output_root.glob("*/*/status.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        allowed = {item.name for item in fields(EvalStatus)}
        status = EvalStatus(**{key: value for key, value in raw.items() if key in allowed})
        final_statuses[(status.model_slug, status.task_id)] = status

    with concurrent.futures.ThreadPoolExecutor(max_workers=config.eval_workers) as pool:
        futures = {
            pool.submit(evaluate_patch, config, task, spec, slug, index % max(1, config.eval_workers)): (slug, task["task_id"])
            for index, (task, spec, slug) in enumerate(jobs)
        }
        evaluated: list[EvalStatus] = []
        for future in concurrent.futures.as_completed(futures):
            key = futures[future]
            status = future.result()
            evaluated.append(status)
            final_statuses[key] = status
            write_json(config.output_root / "summary.reevaluation.partial.json", [asdict(item) for item in evaluated])
            print(json.dumps(asdict(status), ensure_ascii=False), flush=True)

    return summarize(config, list(final_statuses.values()))


def rescore_status(config: RunnerConfig, task: dict, spec: dict, model_slug: str) -> EvalStatus:
    status = load_existing_status(config, model_slug, task["task_id"])
    if status is None:
        raise RuntimeError(f"missing status for {model_slug}/{task['task_id']}")
    if status.capture_status != "passed":
        return status

    run_dir = task_run_dir(config.output_root, model_slug, task["task_id"])
    task_path = run_dir / "task.jsonl"
    raw_spec_path = run_dir / "submission_spec.raw.json"
    normalized_spec_path = run_dir / "submission_spec.normalized.json"
    if not task_path.exists() or not raw_spec_path.exists() or not normalized_spec_path.exists():
        task_path, raw_spec_path = write_task_files(run_dir, task, spec, normalize_asset_paths=False)
        _, normalized_spec_path = write_task_files(run_dir, task, spec, normalize_asset_paths=config.normalize_asset_paths)

    states_root = run_dir / "states"
    for label, spec_path in (("raw", raw_spec_path), ("normalized", normalized_spec_path)):
        report_path = run_dir / f"submission_results.{label}.json"
        score_path = run_dir / f"score_report.{label}.json"
        verify_command = [
            "python3",
            "-m",
            "productwebbench",
            "verify-submission",
            "--tasks",
            str(task_path),
            "--specs",
            str(spec_path),
            "--states-root",
            str(states_root),
            "--design-anchors",
            str(config.design_anchors),
            "--output",
            str(report_path),
        ]
        rc, output = run_command(verify_command, Path.cwd(), 180)
        (run_dir / f"verify.{label}.log").write_text(output, encoding="utf-8")
        if rc != 0:
            status.error = f"{label} verifier failed"
            save_status(config, status)
            return status
        if report_path.exists():
            report = json.loads(report_path.read_text(encoding="utf-8"))
            setattr(status, f"{label}_passed", int(report.get("passed", 0)))
        if report_path.exists():
            rc, output = run_command(
                ["python3", "-m", "productwebbench", "score-report", "--report", str(report_path), "--output", str(score_path)],
                Path.cwd(),
                120,
            )
            (run_dir / f"score.{label}.log").write_text(output, encoding="utf-8")
            if rc != 0:
                status.error = f"{label} score failed"
                save_status(config, status)
                return status
            if score_path.exists():
                score = json.loads(score_path.read_text(encoding="utf-8"))
                setattr(status, f"{label}_wcs_rate", score.get("wcs_rate"))

    status.phase = "done"
    status.error = None
    save_status(config, status)
    return status


def run_rescore_existing(config: RunnerConfig) -> dict[str, Any]:
    ensure_dir(config.output_root)
    tasks = list(read_jsonl(config.tasks_path))
    if config.limit is not None:
        tasks = tasks[: config.limit]
    specs = load_specs(config.specs_path)
    jobs = []
    for task in tasks:
        for slug, _model in config.models.items():
            status = load_existing_status(config, slug, task["task_id"])
            if status and status.capture_status == "passed":
                jobs.append((task, specs[task["task_id"]], slug))

    final_statuses: dict[tuple[str, str], EvalStatus] = {}
    for path in sorted(config.output_root.glob("*/*/status.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        allowed = {item.name for item in fields(EvalStatus)}
        status = EvalStatus(**{key: value for key, value in raw.items() if key in allowed})
        final_statuses[(status.model_slug, status.task_id)] = status

    with concurrent.futures.ThreadPoolExecutor(max_workers=config.eval_workers) as pool:
        futures = {
            pool.submit(rescore_status, config, task, spec, slug): (slug, task["task_id"])
            for task, spec, slug in jobs
        }
        rescored: list[EvalStatus] = []
        for future in concurrent.futures.as_completed(futures):
            key = futures[future]
            status = future.result()
            rescored.append(status)
            final_statuses[key] = status
            write_json(config.output_root / "summary.rescore.partial.json", [asdict(item) for item in rescored])
            print(json.dumps(asdict(status), ensure_ascii=False), flush=True)

    return summarize(config, list(final_statuses.values()))


def add_evaluate_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--api-key", default=os.environ.get("SILICONFLOW_API_KEY"))
    parser.add_argument("--base-url", default="https://api.siliconflow.cn/v1")
    parser.add_argument("--tasks", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_next.jsonl")
    parser.add_argument("--leaderboard-tasks", type=Path)
    parser.add_argument("--specs", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_next_submission_specs.json")
    parser.add_argument("--bench-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "bench" / "dev_next")
    parser.add_argument("--workspace-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "workspaces")
    parser.add_argument("--state-plan", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_next_state_plan.json")
    parser.add_argument("--design-anchors", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_next.design_anchors.json")
    parser.add_argument("--build-capabilities", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_next_build_capabilities.json")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "submissions" / "eval_run")
    parser.add_argument("--model", action="append", default=[], help="alias=model_id; may be passed multiple times")
    parser.add_argument("--request-workers", type=int, default=4)
    parser.add_argument("--eval-workers", type=int, default=2)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-patch-retry", action="store_true")
    parser.add_argument("--max-files", type=int, default=10)
    parser.add_argument("--max-context-chars", type=int, default=28000)
    parser.add_argument("--max-tokens", type=int, default=5000)
    # argparse defaults 会覆盖 dataclass field defaults → 必须也从 env 读,否则 shard_runner 传 900.
    parser.add_argument("--api-timeout", type=int, default=int(os.environ.get("MB_API_TIMEOUT", "180")))
    parser.add_argument("--api-retries", type=int, default=int(os.environ.get("MB_API_RETRIES", "1")))
    parser.add_argument("--eval-skip-build", action="store_true")
    parser.add_argument("--build-timeout", type=int, default=900)
    parser.add_argument("--server-timeout", type=int, default=120)
    parser.add_argument("--capture-timeout", type=int, default=360)
    parser.add_argument("--port-start", type=int, default=3100)
    parser.add_argument("--port-stride", type=int, default=100)
    parser.add_argument("--no-normalize-asset-paths", action="store_true")
    parser.add_argument("--generation-protocol", choices=["search_replace", "file_replacement", "diff"], default="search_replace")


def add_reevaluate_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tasks", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_next.jsonl")
    parser.add_argument("--leaderboard-tasks", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_next.jsonl")
    parser.add_argument("--specs", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_next_submission_specs.json")
    parser.add_argument("--bench-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "bench" / "dev_next")
    parser.add_argument("--workspace-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "workspaces")
    parser.add_argument("--state-plan", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_next_state_plan.json")
    parser.add_argument("--design-anchors", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_next.design_anchors.json")
    parser.add_argument("--build-capabilities", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_next_build_capabilities.json")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--model", action="append", default=[], help="alias=model_id; may be passed multiple times")
    parser.add_argument("--eval-workers", type=int, default=2)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--mode", choices=["capture_failed", "failed", "all"], default="capture_failed")
    parser.add_argument("--eval-skip-build", action="store_true")
    parser.add_argument("--build-timeout", type=int, default=900)
    parser.add_argument("--server-timeout", type=int, default=120)
    parser.add_argument("--capture-timeout", type=int, default=480)
    parser.add_argument("--port-start", type=int, default=3700)
    parser.add_argument("--port-stride", type=int, default=100)
    parser.add_argument("--no-normalize-asset-paths", action="store_true")


def add_rescore_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tasks", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_next.jsonl")
    parser.add_argument("--leaderboard-tasks", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_next.jsonl")
    parser.add_argument("--specs", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_next_submission_specs.json")
    parser.add_argument("--bench-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "bench" / "dev_next")
    parser.add_argument("--workspace-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "workspaces")
    parser.add_argument("--state-plan", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_next_state_plan.json")
    parser.add_argument("--design-anchors", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_next.design_anchors.json")
    parser.add_argument("--build-capabilities", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_next_build_capabilities.json")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--model", action="append", default=[], help="alias=model_id; may be passed multiple times")
    parser.add_argument("--eval-workers", type=int, default=4)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--no-normalize-asset-paths", action="store_true")


def parse_models(items: list[str]) -> dict[str, str]:
    if not items:
        return DEFAULT_MODELS
    models = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"--model must be alias=model_id, got: {item}")
        alias, model = item.split("=", 1)
        models[alias] = model
    return models


def run_evaluate_run_from_args(args: argparse.Namespace) -> None:
    if not args.api_key:
        raise SystemExit("missing --api-key or SILICONFLOW_API_KEY")
    config = RunnerConfig(
        base_url=args.base_url,
        api_key=args.api_key,
        output_root=args.output_root,
        tasks_path=args.tasks,
        leaderboard_tasks_path=args.leaderboard_tasks,
        specs_path=args.specs,
        bench_root=args.bench_root,
        workspace_root=args.workspace_root,
        state_plan=args.state_plan,
        design_anchors=args.design_anchors,
        build_capabilities=args.build_capabilities,
        models=parse_models(args.model),
        request_workers=args.request_workers,
        eval_workers=args.eval_workers,
        limit=args.limit,
        force=args.force,
        patch_retry=not args.no_patch_retry,
        max_files=args.max_files,
        max_context_chars=args.max_context_chars,
        max_tokens=args.max_tokens,
        api_timeout=args.api_timeout,
        api_retries=args.api_retries,
        eval_skip_build=args.eval_skip_build,
        build_timeout=args.build_timeout,
        server_timeout=args.server_timeout,
        capture_timeout=args.capture_timeout,
        port_start=args.port_start,
        port_stride=args.port_stride,
        normalize_asset_paths=not args.no_normalize_asset_paths,
        generation_protocol=args.generation_protocol,
    )
    summary = run_batch(config)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def run_reevaluate_run_from_args(args: argparse.Namespace) -> None:
    config = RunnerConfig(
        base_url="",
        api_key="",
        output_root=args.output_root,
        tasks_path=args.tasks,
        leaderboard_tasks_path=args.leaderboard_tasks,
        specs_path=args.specs,
        bench_root=args.bench_root,
        workspace_root=args.workspace_root,
        state_plan=args.state_plan,
        design_anchors=args.design_anchors,
        build_capabilities=args.build_capabilities,
        models=parse_models(args.model),
        eval_workers=args.eval_workers,
        limit=args.limit,
        force=True,
        eval_skip_build=args.eval_skip_build,
        build_timeout=args.build_timeout,
        server_timeout=args.server_timeout,
        capture_timeout=args.capture_timeout,
        port_start=args.port_start,
        port_stride=args.port_stride,
        normalize_asset_paths=not args.no_normalize_asset_paths,
    )
    summary = run_reevaluate_existing(config, args.mode)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def run_rescore_run_from_args(args: argparse.Namespace) -> None:
    config = RunnerConfig(
        base_url="",
        api_key="",
        output_root=args.output_root,
        tasks_path=args.tasks,
        leaderboard_tasks_path=args.leaderboard_tasks,
        specs_path=args.specs,
        bench_root=args.bench_root,
        workspace_root=args.workspace_root,
        state_plan=args.state_plan,
        design_anchors=args.design_anchors,
        build_capabilities=args.build_capabilities,
        models=parse_models(args.model),
        eval_workers=args.eval_workers,
        limit=args.limit,
        force=True,
        normalize_asset_paths=not args.no_normalize_asset_paths,
    )
    summary = run_rescore_existing(config)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
