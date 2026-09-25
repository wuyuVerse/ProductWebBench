from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ...core.config import DEFAULT_OUTPUT_ROOT
from ...core.io_utils import read_jsonl, write_json


REQUIRED_FIELDS = [
    "task_id",
    "repo_id",
    "why_this_repo",
    "natural_change_location",
    "repo_evidence",
    "asset_grounding",
    "design_integration",
    "state_coverage",
    "anti_shortcut_checks",
    "verifier_alignment",
    "agent_audit_notes",
]

def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def keyed(items: list[dict[str, Any]], key: str = "task_id") -> dict[str, dict[str, Any]]:
    return {item[key]: item for item in items}


def load_rationales(path: Path) -> dict[str, dict[str, Any]]:
    data = load_json(path)
    return keyed(data.get("items", []))


def task_terms(task: dict[str, Any]) -> list[str]:
    values = [
        task.get("problem_statement", ""),
        task.get("author_notes", ""),
        " ".join(task.get("required_content", [])),
        " ".join(task.get("design_constraints", [])),
        " ".join(task.get("assets_to_consider", [])),
    ]
    raw_terms: list[str] = []
    for value in values:
        raw_terms.extend(term.strip(".,:;()[]{}").lower() for term in value.split())
    return sorted({term for term in raw_terms if len(term) >= 6})


def validate_rationale(
    task: dict[str, Any],
    rationale: dict[str, Any] | None,
    dossier: dict[str, Any] | None,
    provenance: dict[str, Any] | None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    if rationale is None:
        return {
            "task_id": task["task_id"],
            "repo_id": task["repo_id"],
            "passed": False,
            "errors": ["missing rationale"],
            "warnings": [],
        }

    missing = [
        field
        for field in REQUIRED_FIELDS
        if field not in rationale
    ]
    if missing:
        errors.append(f"missing fields: {', '.join(missing)}")
    if rationale.get("repo_id") != task["repo_id"]:
        errors.append("repo_id mismatch")
    if rationale.get("task_id") != task["task_id"]:
        errors.append("task_id mismatch")

    for field in ("why_this_repo", "natural_change_location", "asset_grounding", "design_integration", "state_coverage"):
        text = str(rationale.get(field, ""))
        if len(text.split()) < 20:
            errors.append(f"{field} is too short")

    for field in ("repo_evidence", "anti_shortcut_checks", "verifier_alignment", "agent_audit_notes"):
        values = rationale.get(field, [])
        if not isinstance(values, list) or len(values) < 3:
            errors.append(f"{field} should contain at least three reviewable items")

    if dossier is None or not dossier.get("passed"):
        errors.append("missing or failed repo dossier")
    if provenance is None or not provenance.get("passed"):
        errors.append("missing or failed provenance")

    evidence_text = " ".join(rationale.get("repo_evidence", [])).lower()
    term_hits = [term for term in task_terms(task) if term in evidence_text]
    if len(term_hits) < 3:
        warnings.append("repo_evidence weakly overlaps with task-specific terms")

    return {
        "task_id": task["task_id"],
        "repo_id": task["repo_id"],
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "term_hits": term_hits[:12],
    }


def validate_rationales(
    tasks_path: Path,
    rationales_path: Path,
    dossiers_root: Path,
    provenance_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    tasks = list(read_jsonl(tasks_path))
    rationales = load_rationales(rationales_path)
    provenance = keyed(load_json(provenance_path).get("items", []))
    results = []
    for task in tasks:
        repo_id = task["repo_id"]
        dossier_path = dossiers_root / repo_id / "dossier.json"
        dossier = load_json(dossier_path) if dossier_path.exists() else None
        results.append(validate_rationale(task, rationales.get(task["task_id"]), dossier, provenance.get(task["task_id"])))

    summary = {
        "tasks_path": str(tasks_path),
        "rationales_path": str(rationales_path),
        "dossiers_root": str(dossiers_root),
        "provenance_path": str(provenance_path),
        "total": len(results),
        "passed": sum(1 for result in results if result["passed"]),
        "failed": sum(1 for result in results if not result["passed"]),
        "warnings": sum(len(result["warnings"]) for result in results),
        "results": results,
    }
    write_json(output_path, summary)
    return summary


def add_validate_rationales_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tasks", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.jsonl")
    parser.add_argument("--rationales", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.rationales.json")
    parser.add_argument("--dossiers-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "dossiers" / "dev_seed")
    parser.add_argument("--provenance", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.provenance.json")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.rationales.validation.json")


def run_validate_rationales_from_args(args: argparse.Namespace) -> None:
    summary = validate_rationales(args.tasks, args.rationales, args.dossiers_root, args.provenance, args.output)
    print(
        f"validated {summary['total']} rationales: "
        f"{summary['passed']} passed, {summary['failed']} failed, {summary['warnings']} warnings"
    )
