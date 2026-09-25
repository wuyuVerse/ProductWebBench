from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from ...core.config import DEFAULT_OUTPUT_ROOT
from ...core.io_utils import read_jsonl, write_json
from ...core.task_files import task_file_patterns
from ...taxonomy.continuity import CONTINUITY_FACET_KEYS, infer_continuity_facet


TARGET_INTENTS = {"add", "modify", "repair", "restyle", "extend_interaction"}
TARGET_SCOPES = {"section", "component", "page", "flow", "asset", "layout", "cross_page"}
TARGET_DIFFICULTIES = {"L1", "L2", "L3", "L4", "L5"}
TARGET_RUBRIC_KEYS = {"change_completion", "design_consistency", "responsive", "regression"}
WEBSITE_TYPE_ALIASES = {
    "admin": "dashboard_admin",
    "ai": "ai_chat_app",
    "blog": "editorial_blog",
    "canvas": "interactive_canvas",
    "chat": "ai_chat_app",
    "dashboard": "dashboard_admin",
    "documentation": "docs",
    "gallery": "gallery_showcase",
}
WEBSITE_TYPE_KEYS = {
    "ai_chat_app",
    "commerce",
    "dashboard_admin",
    "docs",
    "editorial_blog",
    "gallery_showcase",
    "interactive_canvas",
    "marketing",
    "portfolio",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def count_values(tasks: list[dict[str, Any]], field: str) -> dict[str, int]:
    return dict(sorted(Counter(str(task.get(field, "missing")) for task in tasks).items()))


def package_task_ids(package_manifest_path: Path | None) -> set[str]:
    if not package_manifest_path or not package_manifest_path.exists():
        return set()
    manifest = load_json(package_manifest_path)
    return {item["task_id"] for item in manifest.get("packages", [])}


def website_type_label(task: dict[str, Any]) -> str:
    explicit = str(task.get("website_type", "")).strip().lower()
    if explicit:
        normalized = WEBSITE_TYPE_ALIASES.get(explicit, explicit)
        if normalized in WEBSITE_TYPE_KEYS:
            return normalized
    text = " ".join(
        [
            task.get("task_id", ""),
            task.get("repo_id", ""),
            task.get("problem_statement", ""),
            task.get("author_notes", ""),
        ]
    ).lower()
    if any(token in text for token in ["3d", "three.js", "canvas", "game", "webgl"]):
        return "interactive_canvas"
    if any(token in text for token in ["admin", "dashboard", "customer health", "orders", "analytics"]):
        return "dashboard_admin"
    if any(token in text for token in ["chat", "assistant", "gpt", "prompt", "llm"]):
        return "ai_chat_app"
    if any(token in text for token in ["docs", "documentation", "developer guide"]):
        return "docs"
    if any(token in text for token in ["ecommerce", "storefront", "product", "shop"]):
        return "commerce"
    if any(token in text for token in ["blog", "editorial", "article", "publication"]):
        return "editorial_blog"
    if any(token in text for token in ["gallery", "showcase", "lightbox"]):
        return "gallery_showcase"
    if any(token in text for token in ["portfolio", "case studies", "milestone"]):
        return "portfolio"
    if any(token in text for token in ["marketing", "landing", "church", "agency", "saas", "visitor", "campaign"]):
        return "marketing"
    return "other"


def has_interaction_state(task: dict[str, Any]) -> bool:
    joined = " ".join(task.get("required_states", []) + task.get("hidden_states", [])).lower()
    return any(
        token in joined
        for token in [
            "hover",
            "modal",
            "menu",
            "sidebar",
            "canvas",
            "interacted",
            "carousel",
            "form",
            "dropdown",
            "filter",
            "search",
            "tab",
        ]
    )


def has_asset_grounding(task: dict[str, Any]) -> bool:
    return bool(task.get("assets_to_consider"))


def task_quality_flags(task: dict[str, Any]) -> list[str]:
    flags = []
    if len(task.get("required_states", [])) < 3:
        flags.append("few_required_states")
    if len(task.get("hidden_states", [])) < 1:
        flags.append("no_hidden_states")
    if len(task.get("required_content", [])) < 2:
        flags.append("weak_required_content")
    if len(task.get("design_constraints", [])) < 2:
        flags.append("weak_design_constraints")
    if len(task.get("state_constraints", [])) < 2:
        flags.append("weak_state_constraints")
    rubric_keys = set(task.get("evaluation_rubric", {}))
    missing_rubric = sorted(TARGET_RUBRIC_KEYS - rubric_keys)
    if missing_rubric:
        flags.append("missing_rubric:" + ",".join(missing_rubric))
    if not task_file_patterns(task):
        flags.append("no_suggested_files")
    if not has_asset_grounding(task):
        flags.append("no_asset_grounding")
    return flags


def coverage_warnings(tasks: list[dict[str, Any]], min_tasks_for_full_targets: int) -> list[str]:
    warnings = []
    intents = {task.get("intent") for task in tasks}
    scopes = {task.get("scope") for task in tasks}
    difficulties = {task.get("difficulty") for task in tasks}
    facets = {infer_continuity_facet(task) for task in tasks}

    if len(tasks) >= min_tasks_for_full_targets:
        missing_intents = sorted(TARGET_INTENTS - intents)
        missing_scopes = sorted(TARGET_SCOPES - scopes)
        missing_difficulties = sorted(TARGET_DIFFICULTIES - difficulties)
        if missing_intents:
            warnings.append(f"missing target intents for full split: {missing_intents}")
        if missing_scopes:
            warnings.append(f"missing target scopes for full split: {missing_scopes}")
        if missing_difficulties:
            warnings.append(f"missing target difficulties for full split: {missing_difficulties}")
    else:
        if len(scopes) < min(3, len(tasks)):
            warnings.append("seed split has narrow scope diversity")
        if len(difficulties) < min(3, len(tasks)):
            warnings.append("seed split has narrow difficulty diversity")

    if not any(has_interaction_state(task) for task in tasks):
        warnings.append("no interaction-aware state coverage")
    if not any(has_asset_grounding(task) for task in tasks):
        warnings.append("no asset-grounded task coverage")
    missing_facets = sorted(CONTINUITY_FACET_KEYS - facets)
    if missing_facets and len(tasks) >= min_tasks_for_full_targets:
        warnings.append(f"missing continuity facets for full split: {missing_facets}")
    if len(facets) < min(3, len(tasks)):
        warnings.append("continuity facet diversity is narrow")
    if len(tasks) >= min_tasks_for_full_targets:
        if not any(task.get("scope") == "page" for task in tasks):
            warnings.append("no add-page/route task")
        if not any(task.get("scope") == "flow" or task.get("intent") == "extend_interaction" for task in tasks):
            warnings.append("no interaction-flow task")
    return warnings


def growth_gaps(tasks: list[dict[str, Any]]) -> dict[str, list[str]]:
    intents = {task.get("intent") for task in tasks}
    scopes = {task.get("scope") for task in tasks}
    difficulties = {task.get("difficulty") for task in tasks}
    facets = {infer_continuity_facet(task) for task in tasks}
    return {
        "missing_target_intents": sorted(TARGET_INTENTS - intents),
        "missing_target_scopes": sorted(TARGET_SCOPES - scopes),
        "missing_target_difficulties": sorted(TARGET_DIFFICULTIES - difficulties),
        "missing_continuity_facets": sorted(CONTINUITY_FACET_KEYS - facets),
    }


def audit_coverage(
    tasks_path: Path,
    output_path: Path,
    package_manifest_path: Path | None,
    min_tasks: int,
    min_repos: int,
    min_scopes: int,
    min_difficulties: int,
    min_facets: int,
    min_tasks_for_full_targets: int,
) -> dict[str, Any]:
    tasks = list(read_jsonl(tasks_path))
    errors: list[str] = []
    warnings: list[str] = []
    repo_ids = [task.get("repo_id", "") for task in tasks]
    task_ids = [task.get("task_id", "") for task in tasks]
    packaged_ids = package_task_ids(package_manifest_path)

    if len(tasks) < min_tasks:
        errors.append(f"too few tasks: {len(tasks)} < {min_tasks}")
    if len(set(repo_ids)) < min_repos:
        errors.append(f"too few unique repos: {len(set(repo_ids))} < {min_repos}")
    if len(set(task.get("scope") for task in tasks)) < min_scopes:
        errors.append("scope diversity below threshold")
    if len(set(task.get("difficulty") for task in tasks)) < min_difficulties:
        errors.append("difficulty diversity below threshold")
    if len({infer_continuity_facet(task) for task in tasks}) < min_facets:
        errors.append("continuity facet diversity below threshold")

    duplicate_tasks = sorted(task_id for task_id, count in Counter(task_ids).items() if count > 1)
    duplicate_repos = sorted(repo_id for repo_id, count in Counter(repo_ids).items() if count > 1)
    if duplicate_tasks:
        errors.append(f"duplicate task ids: {duplicate_tasks}")
    if duplicate_repos:
        warnings.append(f"multiple tasks from same repo: {duplicate_repos}")
    if packaged_ids:
        missing_packages = sorted(set(task_ids) - packaged_ids)
        extra_packages = sorted(packaged_ids - set(task_ids))
        if missing_packages:
            errors.append(f"tasks missing exported packages: {missing_packages}")
        if extra_packages:
            warnings.append(f"package manifest has extra task packages: {extra_packages}")

    task_flags = {task["task_id"]: task_quality_flags(task) for task in tasks}
    weak_tasks = {task_id: flags for task_id, flags in task_flags.items() if flags}
    for task_id, flags in weak_tasks.items():
        warnings.append(f"{task_id} quality flags: {flags}")
    warnings.extend(coverage_warnings(tasks, min_tasks_for_full_targets))

    facets = dict(sorted(Counter(infer_continuity_facet(task) for task in tasks).items()))
    website_types = dict(sorted(Counter(website_type_label(task) for task in tasks).items()))
    interaction_count = sum(1 for task in tasks if has_interaction_state(task))
    asset_grounded_count = sum(1 for task in tasks if has_asset_grounding(task))
    required_state_counts = [len(task.get("required_states", [])) for task in tasks]
    hidden_state_counts = [len(task.get("hidden_states", [])) for task in tasks]
    summary = {
        "tasks_path": str(tasks_path),
        "package_manifest_path": str(package_manifest_path) if package_manifest_path else None,
        "total": len(tasks),
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "warning_count": len(warnings),
        "unique_repos": len(set(repo_ids)),
        "distributions": {
            "intent": count_values(tasks, "intent"),
            "scope": count_values(tasks, "scope"),
            "difficulty": count_values(tasks, "difficulty"),
            "split": count_values(tasks, "split"),
            "continuity_facet": facets,
            "website_type": website_types,
        },
        "coverage": {
            "interaction_tasks": interaction_count,
            "asset_grounded_tasks": asset_grounded_count,
            "avg_required_states": round(sum(required_state_counts) / len(required_state_counts), 2) if required_state_counts else None,
            "avg_hidden_states": round(sum(hidden_state_counts) / len(hidden_state_counts), 2) if hidden_state_counts else None,
            "min_required_states": min(required_state_counts) if required_state_counts else None,
            "min_hidden_states": min(hidden_state_counts) if hidden_state_counts else None,
        },
        "growth_gaps": growth_gaps(tasks),
        "task_quality_flags": weak_tasks,
        "thresholds": {
            "min_tasks": min_tasks,
            "min_repos": min_repos,
            "min_scopes": min_scopes,
            "min_difficulties": min_difficulties,
            "min_facets": min_facets,
            "min_tasks_for_full_targets": min_tasks_for_full_targets,
        },
    }
    write_json(output_path, summary)
    return summary


def add_coverage_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tasks", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.jsonl")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.coverage_audit.json")
    parser.add_argument("--package-manifest", type=Path, default=DEFAULT_OUTPUT_ROOT / "bench" / "dev" / "manifest.json")
    parser.add_argument("--min-tasks", type=int, default=5)
    parser.add_argument("--min-repos", type=int, default=5)
    parser.add_argument("--min-scopes", type=int, default=3)
    parser.add_argument("--min-difficulties", type=int, default=3)
    parser.add_argument("--min-facets", type=int, default=4)
    parser.add_argument("--min-tasks-for-full-targets", type=int, default=50)


def run_coverage_from_args(args: argparse.Namespace) -> None:
    summary = audit_coverage(
        tasks_path=args.tasks,
        output_path=args.output,
        package_manifest_path=args.package_manifest,
        min_tasks=args.min_tasks,
        min_repos=args.min_repos,
        min_scopes=args.min_scopes,
        min_difficulties=args.min_difficulties,
        min_facets=args.min_facets,
        min_tasks_for_full_targets=args.min_tasks_for_full_targets,
    )
    status = "passed" if summary["passed"] else "failed"
    print(
        f"coverage audit {status}: {summary['total']} tasks, "
        f"{summary['unique_repos']} repos, {summary['warning_count']} warnings"
    )
