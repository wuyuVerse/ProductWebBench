from __future__ import annotations

import argparse
import fnmatch
import json
import os
from pathlib import Path
from typing import Any

from ...core.config import DEFAULT_OUTPUT_ROOT, workspace_meta_path
from ...core.io_utils import read_jsonl, write_json
from ...core.task_files import task_file_patterns


EXCLUDED_PARTS = {"node_modules", ".next", "dist", "build", "out", ".git"}
TEXT_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".md", ".mdx", ".css", ".scss", ".json", ".html"}
MAX_TEXT_EVIDENCE_BYTES = 1_000_000
TEXT_EVIDENCE_SKIP_NAMES = {
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lockb",
    "composer.lock",
}

TASK_KEYWORDS = {
    "curated_picks": ["ProductItem", "QuickView", "New Arrivals", "Best Sellers", "product", "rating"],
    "customer_health": ["Dashboard", "Customers", "RecentOrders", "DemographicCard", "user", "brand"],
    "case_studies": ["projectsData", "skillsData", "Projects", "Skills", "GlowCard", "lottie"],
    "integration_resources": ["CustomCards", "RoundedLinkButton", "Tutorial", "Zapier", "resources"],
    "portfolio_milestone": ["World", "ProjectsSection", "Resources", "ProjectBoard", "controls", "mobile"],
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def workspace_meta(workspace_root: Path, repo_id: str) -> dict[str, Any]:
    path = workspace_meta_path(workspace_root / repo_id)
    if not path.exists():
        return {"repo_id": repo_id, "missing": True}
    return load_json(path)


def is_excluded(path: Path) -> bool:
    return any(part in EXCLUDED_PARTS for part in path.parts)


def all_repo_files(project_root: Path) -> list[Path]:
    files: list[Path] = []
    for root, dirs, filenames in os.walk(project_root):
        dirs[:] = [dirname for dirname in dirs if dirname not in EXCLUDED_PARTS]
        root_path = Path(root)
        for filename in filenames:
            path = root_path / filename
            rel = path.relative_to(project_root)
            if not is_excluded(rel):
                files.append(path)
    return sorted(files)


def match_patterns(
    project_root: Path,
    files: list[Path],
    patterns: list[str],
    limit_per_pattern: int = 20,
) -> list[dict[str, Any]]:
    results = []
    for pattern in patterns:
        matches = []
        normalized = pattern.rstrip("/")
        for path in files:
            rel = path.relative_to(project_root).as_posix()
            if rel == normalized:
                matches.append(rel)
            elif fnmatch.fnmatch(rel, normalized) or fnmatch.fnmatch(rel, normalized.rstrip("*") + "*"):
                matches.append(rel)
            elif normalized.endswith("/**") and rel.startswith(normalized[:-3].rstrip("/") + "/"):
                matches.append(rel)
        results.append({"pattern": pattern, "matches": matches[:limit_per_pattern], "match_count": len(matches)})
    return results


def task_keywords(task: dict[str, Any]) -> list[str]:
    task_id = task["task_id"]
    for suffix, keywords in TASK_KEYWORDS.items():
        if task_id.endswith(suffix):
            return keywords
    raw_terms = []
    statement = task.get("problem_statement", "")
    for text in [statement] + task.get("required_content", []) + task.get("design_constraints", []):
        raw_terms.extend(term.strip(".,:;()").strip() for term in text.split())
    return [term for term in raw_terms if len(term) > 6][:8]


def search_text_evidence(
    project_root: Path,
    files: list[Path],
    keywords: list[str],
    limit_per_keyword: int = 8,
) -> list[dict[str, Any]]:
    text_files = [
        path
        for path in files
        if path.suffix.lower() in TEXT_SUFFIXES
        and path.name not in TEXT_EVIDENCE_SKIP_NAMES
        and ".min." not in path.name
        and path.stat().st_size <= MAX_TEXT_EVIDENCE_BYTES
    ]
    evidence = []
    for keyword in keywords:
        matches = []
        lowered = keyword.lower()
        for path in text_files:
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            for idx, line in enumerate(lines, start=1):
                if lowered in line.lower():
                    matches.append(
                        {
                            "path": path.relative_to(project_root).as_posix(),
                            "line": idx,
                            "text": line.strip()[:240],
                        }
                    )
                    break
            if len(matches) >= limit_per_keyword:
                break
        evidence.append({"keyword": keyword, "matches": matches, "match_count": len(matches)})
    return evidence


def browser_text_evidence(task: dict[str, Any], states_root: Path, signals: list[str]) -> list[dict[str, Any]]:
    results = []
    for state_id in task.get("required_states", []):
        metrics_path = states_root / task["repo_id"] / state_id / "metrics.json"
        if not metrics_path.exists():
            continue
        metrics = load_json(metrics_path)
        text = metrics.get("text", "")
        found = [signal for signal in signals if signal.lower() in text.lower()]
        if found:
            results.append({"state_id": state_id, "found_signals": found})
    return results


def canvas_only_reference_evidence(task: dict[str, Any], states_root: Path) -> dict[str, Any]:
    task_text = " ".join(
        [
            task.get("problem_statement", ""),
            task.get("author_notes", ""),
            " ".join(task.get("design_constraints", [])),
            " ".join(task.get("state_constraints", [])),
        ]
    ).lower()
    canvas_like = any(token in task_text for token in ("canvas", "webgl", "three.js", "threejs"))
    states = task.get("required_states", [])
    if not canvas_like or not states:
        return {"passed": False, "reason": "not_canvas_like"}

    summaries = []
    for state_id in states:
        state_dir = states_root / task["repo_id"] / state_id
        metrics_path = state_dir / "metrics.json"
        screenshot_path = state_dir / "screenshot.png"
        if not metrics_path.exists() or not screenshot_path.exists():
            summaries.append({"state_id": state_id, "has_canvas": False, "text_length": None, "screenshot_bytes": 0})
            continue
        metrics = load_json(metrics_path)
        has_canvas = any(
            box.get("tag") == "canvas"
            and box.get("rect", {}).get("width", 0) >= 120
            and box.get("rect", {}).get("height", 0) >= 120
            for box in metrics.get("boxes", [])
        )
        summaries.append(
            {
                "state_id": state_id,
                "has_canvas": has_canvas,
                "text_length": len(metrics.get("text", "").strip()),
                "screenshot_bytes": screenshot_path.stat().st_size,
            }
        )

    canvas_states = [item for item in summaries if item["has_canvas"] and item["screenshot_bytes"] >= 250_000]
    textless_states = [item for item in summaries if item["text_length"] == 0]
    passed = len(canvas_states) >= 2 and len(textless_states) == len(summaries)
    return {
        "passed": passed,
        "reason": "canvas_only_reference" if passed else "insufficient_canvas_reference",
        "summaries": summaries,
    }


def build_task_provenance(
    task: dict[str, Any],
    workspace_root: Path,
    states_root: Path,
    submission_spec: dict[str, Any] | None,
) -> dict[str, Any]:
    meta = workspace_meta(workspace_root, task["repo_id"])
    if meta.get("missing"):
        return {
            "task_id": task["task_id"],
            "repo_id": task["repo_id"],
            "passed": False,
            "errors": ["missing workspace metadata"],
        }

    project_root = Path(meta["project_root"])
    repo_files = all_repo_files(project_root)
    source_matches = match_patterns(project_root, repo_files, task_file_patterns(task))
    asset_matches = match_patterns(project_root, repo_files, task.get("assets_to_consider", []))
    keywords = task_keywords(task)
    text_evidence = search_text_evidence(project_root, repo_files, keywords)
    submission_spec = submission_spec or {}
    reference_signals = submission_spec.get("regression_text_signals", [])
    baseline_signals = submission_spec.get("provenance_baseline_text_signals", [])
    browser_evidence_signals = list(dict.fromkeys(reference_signals + baseline_signals))
    browser_evidence = browser_text_evidence(task, states_root, browser_evidence_signals)
    canvas_reference = canvas_only_reference_evidence(task, states_root)

    errors = []
    if any(item["match_count"] == 0 for item in source_matches):
        errors.append("one or more task file patterns have no repo matches")
    if any(item["match_count"] == 0 for item in asset_matches):
        errors.append("one or more assets_to_consider patterns have no repo matches")
    if sum(item["match_count"] for item in text_evidence) < 3:
        errors.append("insufficient source keyword evidence")
    if browser_evidence_signals and not browser_evidence and not canvas_reference.get("passed"):
        errors.append("no browser-state text evidence for regression or baseline signals")

    return {
        "task_id": task["task_id"],
        "repo_id": task["repo_id"],
        "passed": not errors,
        "errors": errors,
        "workspace": {
            "workspace": meta.get("workspace"),
            "project_root": meta.get("project_root"),
            "zip_path": meta.get("zip_path"),
            "package_name": meta.get("package_name"),
            "scripts": meta.get("scripts", {}),
        },
        "source_patterns": source_matches,
        "asset_patterns": asset_matches,
        "keywords": keywords,
        "source_text_evidence": text_evidence,
        "browser_text_evidence": browser_evidence,
        "canvas_reference_evidence": canvas_reference,
    }


def build_provenance_index(
    tasks_path: Path,
    submission_specs_path: Path,
    workspace_root: Path,
    states_root: Path,
    output_path: Path,
) -> dict[str, Any]:
    tasks = list(read_jsonl(tasks_path))
    submission_specs = {
        item["task_id"]: item
        for item in load_json(submission_specs_path).get("tasks", [])
    }
    items = [
        build_task_provenance(task, workspace_root, states_root, submission_specs.get(task["task_id"]))
        for task in tasks
    ]
    summary = {
        "tasks_path": str(tasks_path),
        "submission_specs_path": str(submission_specs_path),
        "workspace_root": str(workspace_root),
        "states_root": str(states_root),
        "total": len(items),
        "passed": sum(1 for item in items if item["passed"]),
        "failed": sum(1 for item in items if not item["passed"]),
        "items": items,
    }
    write_json(output_path, summary)
    return summary


def add_provenance_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tasks", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.jsonl")
    parser.add_argument(
        "--submission-specs",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed_submission_specs.json",
    )
    parser.add_argument("--workspace-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "workspaces")
    parser.add_argument("--states-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "states")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.provenance.json")


def run_provenance_from_args(args: argparse.Namespace) -> None:
    summary = build_provenance_index(
        tasks_path=args.tasks,
        submission_specs_path=args.submission_specs,
        workspace_root=args.workspace_root,
        states_root=args.states_root,
        output_path=args.output,
    )
    print(f"built provenance for {summary['total']} tasks: {summary['passed']} passed, {summary['failed']} failed")
