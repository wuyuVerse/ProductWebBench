from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
from pathlib import Path
from typing import Any

from ...core.config import DEFAULT_OUTPUT_ROOT, workspace_meta_path
from ...core.io_utils import read_jsonl, write_json
from ...core.task_files import task_file_patterns


EXCLUDED_PARTS = {
    ".git",
    ".cache",
    ".content-collections",
    ".data",
    ".next",
    ".nuxt",
    ".output",
    "_site",
    "_gh_pages",
    ".svelte-kit",
    ".turbo",
    ".vercel",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "out",
}

SOURCE_SUFFIXES = {
    ".astro",
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
}

PACKAGE_DEP_KEYS = ["dependencies", "devDependencies", "peerDependencies", "optionalDependencies"]
GENERATED_SOURCE_FILENAMES = {
    "auto-imports.d.ts",
    "components.d.ts",
    "typed-router.d.ts",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def is_excluded(path: Path) -> bool:
    return path.name in GENERATED_SOURCE_FILENAMES or any(part in EXCLUDED_PARTS for part in path.parts)


def is_hugo_project(root: Path) -> bool:
    has_hugo_config = any((root / name).exists() for name in ("hugo.yaml", "hugo.toml", "config.yaml", "config.toml"))
    if (
        (root / "theme.toml").exists()
        and (root / "layouts").exists()
        and (root / "exampleSite" / "content").exists()
        and any((root / "exampleSite" / name).exists() for name in ("hugo.yaml", "hugo.toml", "config.yaml", "config.toml"))
    ):
        return True
    if not has_hugo_config:
        return False
    package_json = root / "package.json"
    if package_json.exists():
        text = safe_text(package_json, limit=200_000).lower()
        if "hugo" in text:
            return True
    return (root / "go.mod").exists() or (root / "layouts").exists() or (root / "content").exists()


def is_generated_build_output(root: Path, rel: Path) -> bool:
    parts = rel.parts
    if not parts or not is_hugo_project(root):
        return False
    if parts[0] == "public":
        return True
    if len(parts) >= 2 and parts[0] == "exampleSite" and parts[1] == "public":
        return True
    if len(parts) >= 3 and parts[0] == "exampleSite" and parts[1] == "resources" and parts[2] == "_gen":
        return True
    return len(parts) >= 2 and parts[0] == "resources" and parts[1] == "_gen"


def is_text_candidate(path: Path) -> bool:
    return path.suffix.lower() in SOURCE_SUFFIXES or path.name in {"package.json"}


def safe_text(path: Path, limit: int = 2_000_000) -> str:
    try:
        if path.stat().st_size > limit:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_index(root: Path) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if is_excluded(rel) or is_generated_build_output(root, rel):
            continue
        index[rel.as_posix()] = {
            "hash": file_hash(path),
            "size": path.stat().st_size,
            "suffix": path.suffix.lower(),
        }
    return index


def workspace_project_root(workspace_root: Path, repo_id: str) -> Path | None:
    repo_dir = workspace_root / repo_id
    meta_path = workspace_meta_path(repo_dir)
    if meta_path.exists():
        meta = load_json(meta_path)
        project_root = Path(meta.get("project_root", ""))
        if project_root.exists() and is_relative_to(project_root.resolve(), repo_dir.resolve()):
            return project_root
        meta_workspace = Path(meta.get("workspace", ""))
        if project_root and meta_workspace:
            try:
                project_rel = project_root.resolve().relative_to(meta_workspace.resolve())
                relocated = repo_dir / project_rel
                if relocated.exists():
                    return relocated
            except ValueError:
                pass
    if (repo_dir / "package.json").exists() or (repo_dir / "index.html").exists():
        return repo_dir
    if workspace_root.name == repo_id and ((workspace_root / "package.json").exists() or (workspace_root / "index.html").exists()):
        return workspace_root
    return None


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def changed_files(reference_root: Path, submission_root: Path) -> dict[str, Any]:
    reference = file_index(reference_root)
    submission = file_index(submission_root)
    reference_paths = set(reference)
    submission_paths = set(submission)
    added = sorted(submission_paths - reference_paths)
    deleted = sorted(reference_paths - submission_paths)
    modified = sorted(
        path
        for path in reference_paths & submission_paths
        if reference[path]["hash"] != submission[path]["hash"]
    )
    changed = sorted(added + deleted + modified)
    source_changed = [path for path in changed if Path(path).suffix.lower() in SOURCE_SUFFIXES or Path(path).name == "package.json"]
    return {
        "added": added,
        "deleted": deleted,
        "modified": modified,
        "changed": changed,
        "source_changed": source_changed,
        "counts": {
            "added": len(added),
            "deleted": len(deleted),
            "modified": len(modified),
            "changed": len(changed),
            "source_changed": len(source_changed),
        },
    }


def match_any(path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        normalized = pattern.rstrip("/")
        if fnmatch.fnmatch(path, normalized):
            return True
        if fnmatch.fnmatch(path, normalized.rstrip("*") + "*"):
            return True
        if normalized.endswith("/**") and path.startswith(normalized[:-3].rstrip("/") + "/"):
            return True
    return False


def dependency_additions(reference_root: Path, submission_root: Path) -> dict[str, list[str]]:
    reference_pkg = reference_root / "package.json"
    submission_pkg = submission_root / "package.json"
    if not reference_pkg.exists() or not submission_pkg.exists():
        return {}
    reference = load_json(reference_pkg)
    submission = load_json(submission_pkg)
    additions: dict[str, list[str]] = {}
    for key in PACKAGE_DEP_KEYS:
        reference_deps = reference.get(key, {}) if isinstance(reference.get(key, {}), dict) else {}
        submission_deps = submission.get(key, {}) if isinstance(submission.get(key, {}), dict) else {}
        added = sorted(set(submission_deps) - set(reference_deps))
        if added:
            additions[key] = added
    return additions


def line_delta(reference_root: Path, submission_root: Path, changed: dict[str, Any]) -> dict[str, Any]:
    total_added = 0
    total_removed = 0
    samples = []
    for rel in changed["source_changed"]:
        ref_path = reference_root / rel
        sub_path = submission_root / rel
        ref_lines = safe_text(ref_path).splitlines() if ref_path.exists() else []
        sub_lines = safe_text(sub_path).splitlines() if sub_path.exists() else []
        added = max(0, len(sub_lines) - len(ref_lines))
        removed = max(0, len(ref_lines) - len(sub_lines))
        if ref_path.exists() and sub_path.exists() and len(ref_lines) == len(sub_lines):
            changed_lines = sum(1 for left, right in zip(ref_lines, sub_lines) if left != right)
            added = changed_lines
            removed = changed_lines
        total_added += added
        total_removed += removed
        if len(samples) < 20:
            samples.append({"path": rel, "added_or_changed_lines": added, "removed_or_changed_lines": removed})
    return {
        "added_or_changed_lines": total_added,
        "removed_or_changed_lines": total_removed,
        "samples": samples,
    }


def asset_reference_hits(task: dict[str, Any], reference_root: Path, submission_root: Path, changed: dict[str, Any]) -> dict[str, Any]:
    patterns = task.get("assets_to_consider", [])
    reference_assets = [
        rel
        for rel in file_index(reference_root)
        if match_any(rel, patterns)
    ]
    asset_needles = sorted(
        {
            needle
            for rel in reference_assets
            for needle in [rel, Path(rel).name]
            if len(needle) >= 5
        }
    )
    hits: list[dict[str, str]] = []
    for rel in changed["source_changed"]:
        if match_any(rel, patterns):
            hits.append({"changed_file": rel, "asset_signal": rel})
            continue
        path = submission_root / rel
        if not path.exists() or not is_text_candidate(path):
            continue
        text = safe_text(path)
        for needle in asset_needles:
            if needle in text:
                hits.append({"changed_file": rel, "asset_signal": needle})
                break
    return {
        "asset_patterns": patterns,
        "candidate_asset_count": len(reference_assets),
        "hit_count": len(hits),
        "hits": hits[:30],
    }


def audit_task_source(
    task: dict[str, Any],
    reference_workspace_root: Path,
    submission_workspace_root: Path,
    max_changed_files: int,
    max_added_files: int,
    max_deleted_files: int,
    max_line_delta: int,
    allow_dependency_additions: bool,
) -> dict[str, Any]:
    repo_id = task["repo_id"]
    reference_root = workspace_project_root(reference_workspace_root, repo_id)
    submission_root = workspace_project_root(submission_workspace_root, repo_id)
    errors: list[str] = []
    warnings: list[str] = []

    if reference_root is None:
        errors.append("missing reference project root")
    if submission_root is None:
        errors.append("missing submission project root")
    if errors:
        return {
            "task_id": task["task_id"],
            "repo_id": repo_id,
            "passed": False,
            "errors": errors,
            "warnings": warnings,
        }

    assert reference_root is not None
    assert submission_root is not None
    changed = changed_files(reference_root, submission_root)
    deps = dependency_additions(reference_root, submission_root)
    delta = line_delta(reference_root, submission_root, changed)
    file_patterns = task_file_patterns(task)
    suggested_hits = [path for path in changed["source_changed"] if match_any(path, file_patterns)]
    asset_hits = asset_reference_hits(task, reference_root, submission_root, changed)

    counts = changed["counts"]
    if counts["source_changed"] == 0:
        errors.append("no source files changed")
    if counts["changed"] > max_changed_files:
        errors.append(f"too many changed files: {counts['changed']} > {max_changed_files}")
    if counts["added"] > max_added_files:
        errors.append(f"too many added files: {counts['added']} > {max_added_files}")
    if counts["deleted"] > max_deleted_files:
        errors.append(f"too many deleted files: {counts['deleted']} > {max_deleted_files}")
    if delta["added_or_changed_lines"] + delta["removed_or_changed_lines"] > max_line_delta:
        errors.append("line delta exceeds threshold")
    if deps and not allow_dependency_additions:
        errors.append("new package dependencies added")
    if file_patterns and not suggested_hits:
        warnings.append("no changed file matches task file patterns")
    if task.get("assets_to_consider") and asset_hits["candidate_asset_count"] and not asset_hits["hits"]:
        warnings.append("no changed source file references task asset candidates")

    passed = not errors
    check = {
        "name": "source_change_audit",
        "passed": passed,
        "message": "source changes fit task constraints" if passed else "source changes violate task constraints",
        "details": {
            "errors": errors,
            "warnings": warnings,
            "changed_counts": counts,
            "line_delta": delta,
            "dependency_additions": deps,
            "suggested_file_hits": suggested_hits,
            "asset_reference_hits": asset_hits,
        },
    }
    return {
        "task_id": task["task_id"],
        "repo_id": repo_id,
        "passed": passed,
        "errors": errors,
        "warnings": warnings,
        "checks": [check],
        "reference_project_root": str(reference_root),
        "submission_project_root": str(submission_root),
        "changed_files": changed,
        "line_delta": delta,
        "dependency_additions": deps,
        "suggested_file_hits": suggested_hits,
        "asset_reference_hits": asset_hits,
        "thresholds": {
            "max_changed_files": max_changed_files,
            "max_added_files": max_added_files,
            "max_deleted_files": max_deleted_files,
            "max_line_delta": max_line_delta,
            "allow_dependency_additions": allow_dependency_additions,
        },
    }


def audit_source(
    tasks_path: Path,
    reference_workspace_root: Path,
    submission_workspace_root: Path,
    output_path: Path,
    max_changed_files: int,
    max_added_files: int,
    max_deleted_files: int,
    max_line_delta: int,
    allow_dependency_additions: bool,
) -> dict[str, Any]:
    tasks = list(read_jsonl(tasks_path))
    results = [
        audit_task_source(
            task,
            reference_workspace_root,
            submission_workspace_root,
            max_changed_files,
            max_added_files,
            max_deleted_files,
            max_line_delta,
            allow_dependency_additions,
        )
        for task in tasks
    ]
    summary = {
        "tasks_path": str(tasks_path),
        "reference_workspace_root": str(reference_workspace_root),
        "submission_workspace_root": str(submission_workspace_root),
        "total": len(results),
        "passed": sum(1 for result in results if result.get("passed")),
        "failed": sum(1 for result in results if not result.get("passed")),
        "warning_count": sum(len(result.get("warnings", [])) for result in results),
        "results": results,
    }
    write_json(output_path, summary)
    return summary


def add_source_audit_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tasks", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.jsonl")
    parser.add_argument("--reference-workspace-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "workspaces")
    parser.add_argument("--submission-workspace-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.source_audit.json")
    parser.add_argument("--max-changed-files", type=int, default=40)
    parser.add_argument("--max-added-files", type=int, default=20)
    parser.add_argument("--max-deleted-files", type=int, default=8)
    parser.add_argument("--max-line-delta", type=int, default=1800)
    parser.add_argument("--allow-dependency-additions", action="store_true")


def run_source_audit_from_args(args: argparse.Namespace) -> None:
    summary = audit_source(
        tasks_path=args.tasks,
        reference_workspace_root=args.reference_workspace_root,
        submission_workspace_root=args.submission_workspace_root,
        output_path=args.output,
        max_changed_files=args.max_changed_files,
        max_added_files=args.max_added_files,
        max_deleted_files=args.max_deleted_files,
        max_line_delta=args.max_line_delta,
        allow_dependency_additions=args.allow_dependency_additions,
    )
    print(
        f"audited source changes for {summary['total']} tasks: "
        f"{summary['passed']} passed, {summary['failed']} failed, {summary['warning_count']} warnings"
    )
