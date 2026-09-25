from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ...core.config import DEFAULT_OUTPUT_ROOT
from ...core.io_utils import ensure_dir, read_jsonl, write_json
from ...pipeline import per_task
from .declarations import (
    DECLARATION_NAME,
    audit_no_bulk_declaration_file,
    build_no_bulk_declaration_template,
)
from .tasks import REQUIRED_REVIEW_ARTIFACTS, validate_task


ARTIFACT_TYPE = "single_task_freeze_audit"
DECLARATION_TEMPLATE_NAME = "no_bulk_generation_declaration.template.md"
STRICT_REQUIRED_FILES = {
    "bad_solution_metadata": ("bad_solution_metadata.json",),
    "bad_solution_results": ("bad_solution_results.json",),
    "consistency_audit": ("consistency_audit.json",),
    "design_anchors": ("design_anchors.json",),
    "evidence_index": ("evidence_index.json",),
    "package_validation": ("package_validation.json",),
    "provenance": ("provenance.json",),
    "quality_audit": ("quality_audit.json",),
    "rationale_validation": ("rationale_validation.json",),
    "rationales": ("rationales.json",),
    "reference_results": ("reference_results.json",),
    "source_audit": ("source_audit.json",),
    "submission_baseline_results": ("submission_results.baseline_states.json",),
    "submission_reference_results": ("submission_results.reference_states.json",),
    "submission_specs": ("submission_specs.json",),
    "task_validation": ("task_validation.json",),
    "verifier_specs": ("verifier_specs.json",),
}
SUMMARY_EXPECTED_PASS = {
    "package_validation": "package_validation.json",
    "consistency_audit": "consistency_audit.json",
    "quality_audit": "quality_audit.json",
    "rationale_validation": "rationale_validation.json",
    "source_audit": "source_audit.json",
    "reference_results": "reference_results.json",
    "submission_reference_results": "submission_results.reference_states.json",
}
SUMMARY_EXPECTED_FAIL = {
    "submission_baseline_results": "submission_results.baseline_states.json",
    "bad_solution_results": "bad_solution_results.json",
}
def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def first_existing(slot_dir: Path, names: tuple[str, ...]) -> Path | None:
    for name in names:
        path = slot_dir / name
        if path.exists():
            return path
    return None


def one_result_for_task(report: dict[str, Any], task_id: str) -> list[dict[str, Any]]:
    results = report.get("results", [])
    if not isinstance(results, list):
        return []
    return [item for item in results if item.get("task_id") == task_id]


def add_check(checks: list[dict[str, Any]], issues: list[str], name: str, passed: bool, issue: str, **extra: Any) -> None:
    checks.append({"name": name, "passed": bool(passed), **extra})
    if not passed:
        issues.append(issue)


def audit_formal_task(slot_dir: Path, checks: list[dict[str, Any]], issues: list[str]) -> dict[str, Any] | None:
    task_path = slot_dir / "task.jsonl"
    add_check(
        checks,
        issues,
        "task_file_exists",
        task_path.exists(),
        f"missing formal task file: {task_path}",
        path=str(task_path),
    )
    if not task_path.exists():
        return None

    rows = list(read_jsonl(task_path))
    add_check(
        checks,
        issues,
        "exactly_one_task_row",
        len(rows) == 1,
        f"single-task freeze audit requires exactly one task row, observed {len(rows)}",
        observed=len(rows),
    )
    if len(rows) != 1:
        return None

    task = rows[0]
    validation_errors = validate_task(task)
    add_check(
        checks,
        issues,
        "formal_task_schema_valid",
        not validation_errors,
        "formal task row failed schema/content validation",
        validation_errors=validation_errors,
        task_id=task.get("task_id"),
        repo_id=task.get("repo_id"),
    )
    return task


def audit_required_files(slot_dir: Path, checks: list[dict[str, Any]], issues: list[str]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    requirements = {**REQUIRED_REVIEW_ARTIFACTS, **STRICT_REQUIRED_FILES}
    for name, candidates in sorted(requirements.items()):
        path = first_existing(slot_dir, candidates)
        add_check(
            checks,
            issues,
            f"{name}_exists",
            path is not None,
            f"missing required single-task artifact: {name}",
            candidates=[str(slot_dir / candidate) for candidate in candidates],
        )
        if path is not None:
            resolved[name] = str(path)
    return resolved


def audit_count_summary(
    slot_dir: Path,
    task_id: str,
    name: str,
    filename: str,
    expected: str,
    checks: list[dict[str, Any]],
    issues: list[str],
) -> None:
    path = slot_dir / filename
    if not path.exists():
        add_check(checks, issues, f"{name}_summary_exists", False, f"missing summary report: {path}", path=str(path))
        return
    report = load_json(path)
    total = report.get("total")
    passed_count = report.get("passed")
    failed_count = report.get("failed")
    matches = one_result_for_task(report, task_id)
    result_pass_values = [item.get("passed") for item in matches]
    if expected == "pass":
        ok = total == 1 and passed_count == 1 and failed_count == 0 and len(matches) == 1 and result_pass_values == [True]
        issue = f"{name} must show exactly one passing result for {task_id}"
    else:
        ok = total == 1 and passed_count == 0 and failed_count == 1 and len(matches) == 1 and result_pass_values == [False]
        issue = f"{name} must show exactly one failing negative-control result for {task_id}"
    add_check(
        checks,
        issues,
        f"{name}_summary_{expected}",
        ok,
        issue,
        path=str(path),
        total=total,
        passed_count=passed_count,
        failed_count=failed_count,
        matching_results=len(matches),
        result_pass_values=result_pass_values,
    )


def audit_task_validation_report(slot_dir: Path, task_id: str, checks: list[dict[str, Any]], issues: list[str]) -> None:
    path = slot_dir / "task_validation.json"
    if not path.exists():
        add_check(checks, issues, "task_validation_summary_exists", False, f"missing summary report: {path}", path=str(path))
        return
    report = load_json(path)
    results = report.get("results", [])
    matches = [item for item in results if item.get("task_id") == task_id]
    match_errors = [item.get("errors", []) for item in matches]
    ok = (
        report.get("total") == 1
        and report.get("valid") == 1
        and report.get("invalid") == 0
        and len(matches) == 1
        and match_errors == [[]]
    )
    add_check(
        checks,
        issues,
        "task_validation_summary_pass",
        ok,
        f"task_validation must show exactly one valid row for {task_id}",
        path=str(path),
        total=report.get("total"),
        valid=report.get("valid"),
        invalid=report.get("invalid"),
        matching_results=len(matches),
        match_errors=match_errors,
    )


def audit_provenance_report(slot_dir: Path, task_id: str, checks: list[dict[str, Any]], issues: list[str]) -> None:
    path = slot_dir / "provenance.json"
    if not path.exists():
        add_check(checks, issues, "provenance_summary_exists", False, f"missing summary report: {path}", path=str(path))
        return
    report = load_json(path)
    items = [item for item in report.get("items", []) if item.get("task_id") == task_id]
    item_pass_values = [item.get("passed") for item in items]
    ok = (
        report.get("total") == 1
        and report.get("passed") == 1
        and report.get("failed") == 0
        and len(items) == 1
        and item_pass_values == [True]
    )
    add_check(
        checks,
        issues,
        "provenance_summary_pass",
        ok,
        f"provenance must show exactly one passing item for {task_id}",
        path=str(path),
        total=report.get("total"),
        passed_count=report.get("passed"),
        failed_count=report.get("failed"),
        matching_items=len(items),
        item_pass_values=item_pass_values,
    )


def audit_design_evidence(slot_dir: Path, task_id: str, checks: list[dict[str, Any]], issues: list[str]) -> None:
    design_path = slot_dir / "design_anchors.json"
    if design_path.exists():
        design = load_json(design_path)
        items = one_result_for_task(design, task_id) or [
            item for item in design.get("items", []) if item.get("task_id") == task_id
        ]
        has_anchor = len(items) == 1 and bool(items[0].get("has_design_anchors"))
        add_check(
            checks,
            issues,
            "design_anchor_item_passed",
            has_anchor,
            f"design anchor report must contain one passing item for {task_id}",
            path=str(design_path),
            matching_items=len(items),
            has_design_anchors=[item.get("has_design_anchors") for item in items],
        )

    evidence_path = slot_dir / "evidence_index.json"
    if evidence_path.exists():
        evidence = load_json(evidence_path)
        items = [item for item in evidence.get("items", []) if item.get("task_id") == task_id]
        quality_pass = len(items) == 1 and bool(items[0].get("quality_pass"))
        add_check(
            checks,
            issues,
            "evidence_index_quality_passed",
            quality_pass,
            f"evidence index must contain one quality-pass item for {task_id}",
            path=str(evidence_path),
            matching_items=len(items),
            quality_pass=[item.get("quality_pass") for item in items],
        )

    rationales_path = slot_dir / "rationales.json"
    if rationales_path.exists():
        rationales = load_json(rationales_path)
        items = [item for item in rationales.get("items", []) if item.get("task_id") == task_id]
        add_check(
            checks,
            issues,
            "rationale_item_present",
            len(items) == 1,
            f"rationales must contain exactly one item for {task_id}",
            path=str(rationales_path),
            matching_items=len(items),
        )


def audit_declaration(
    declaration_path: Path,
    task: dict[str, Any],
    *,
    slot_id: str,
    allow_missing: bool,
    checks: list[dict[str, Any]],
    issues: list[str],
) -> None:
    if not declaration_path.exists():
        add_check(
            checks,
            issues,
            "no_bulk_generation_declaration_exists",
            allow_missing,
            f"missing no-bulk generation declaration: {declaration_path}",
            path=str(declaration_path),
            allow_missing=allow_missing,
        )
        return
    declaration_report = audit_no_bulk_declaration_file(
        declaration_path,
        task_id=str(task.get("task_id", "")),
        repo_id=str(task.get("repo_id", "")),
        slot_id=slot_id,
    )
    add_check(
        checks,
        issues,
        "no_bulk_generation_declaration_solid",
        bool(declaration_report.get("passed")),
        "no-bulk declaration must be parseable, task-specific, and explicitly reject bulk generation",
        path=str(declaration_path),
        text_length=declaration_report.get("text_length"),
        declaration_issues=declaration_report.get("issues", []),
        declaration_fields=declaration_report.get("fields", {}),
    )


def audit_progress(
    progress_path: Path,
    slot_dir: Path,
    task: dict[str, Any],
    *,
    allow_missing: bool,
    checks: list[dict[str, Any]],
    issues: list[str],
) -> None:
    if not progress_path.exists():
        add_check(
            checks,
            issues,
            "per_task_progress_exists",
            allow_missing,
            f"missing per-task progress file: {progress_path}",
            path=str(progress_path),
            allow_missing=allow_missing,
        )
        return
    progress = load_json(progress_path)
    report = per_task.audit_progress(progress, progress_path)
    add_check(
        checks,
        issues,
        "per_task_progress_audit_passed",
        bool(report.get("passed")),
        "per-task progress audit failed",
        path=str(progress_path),
        progress_issues=report.get("issues", []),
    )
    stages = progress.get("stages", {})
    prior_stage_failures = [
        stage_id
        for stage_id in per_task.STAGE_IDS[: per_task.STAGE_INDEX["P8_freeze"]]
        if stages.get(stage_id, {}).get("status") != "passed"
    ]
    add_check(
        checks,
        issues,
        "p0_to_p7_passed_before_freeze",
        not prior_stage_failures,
        "P0-P7 must pass before the single-task freeze audit can pass",
        missing_or_unpassed=prior_stage_failures,
    )
    add_check(
        checks,
        issues,
        "progress_slot_matches",
        progress.get("slot_id") == slot_dir.name,
        "per-task progress slot_id must match the audited slot directory",
        observed=progress.get("slot_id"),
        expected=slot_dir.name,
    )
    if progress.get("task_id"):
        add_check(
            checks,
            issues,
            "progress_task_id_matches",
            progress.get("task_id") == task.get("task_id"),
            "per-task progress task_id must match the formal task row",
            observed=progress.get("task_id"),
            expected=task.get("task_id"),
        )
    if progress.get("repo_id"):
        add_check(
            checks,
            issues,
            "progress_repo_id_matches",
            progress.get("repo_id") == task.get("repo_id"),
            "per-task progress repo_id must match the formal task row",
            observed=progress.get("repo_id"),
            expected=task.get("repo_id"),
        )


def audit_bad_solution_metadata(slot_dir: Path, task: dict[str, Any], checks: list[dict[str, Any]], issues: list[str]) -> None:
    path = slot_dir / "bad_solution_metadata.json"
    if not path.exists():
        return
    metadata = load_json(path)
    passed = (
        metadata.get("task_id") == task.get("task_id")
        and metadata.get("repo_id") == task.get("repo_id")
        and metadata.get("valid_bad_solution_failure") is True
        and bool(metadata.get("failed_checks"))
    )
    add_check(
        checks,
        issues,
        "bad_solution_metadata_valid",
        passed,
        "bad-solution metadata must prove a valid failed negative control for this task",
        path=str(path),
        observed_task_id=metadata.get("task_id"),
        observed_repo_id=metadata.get("repo_id"),
        valid_bad_solution_failure=metadata.get("valid_bad_solution_failure"),
        failed_checks=metadata.get("failed_checks"),
    )


def audit_single_task_freeze(
    *,
    slot_dir: Path,
    output_path: Path,
    progress_path: Path | None = None,
    declaration_path: Path | None = None,
    allow_missing_progress: bool = False,
    allow_missing_declaration: bool = False,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    issues: list[str] = []
    slot_dir = slot_dir.resolve()
    add_check(checks, issues, "slot_dir_exists", slot_dir.exists(), f"slot directory does not exist: {slot_dir}", path=str(slot_dir))
    task = audit_formal_task(slot_dir, checks, issues) if slot_dir.exists() else None
    artifact_paths = audit_required_files(slot_dir, checks, issues) if slot_dir.exists() else {}

    if task is not None:
        audit_task_validation_report(slot_dir, task["task_id"], checks, issues)
        audit_provenance_report(slot_dir, task["task_id"], checks, issues)
        for name, filename in SUMMARY_EXPECTED_PASS.items():
            audit_count_summary(slot_dir, task["task_id"], name, filename, "pass", checks, issues)
        for name, filename in SUMMARY_EXPECTED_FAIL.items():
            audit_count_summary(slot_dir, task["task_id"], name, filename, "fail", checks, issues)
        audit_design_evidence(slot_dir, task["task_id"], checks, issues)
        audit_bad_solution_metadata(slot_dir, task, checks, issues)
        declaration = declaration_path or slot_dir / DECLARATION_NAME
        audit_declaration(
            declaration,
            task,
            slot_id=slot_dir.name,
            allow_missing=allow_missing_declaration,
            checks=checks,
            issues=issues,
        )
        progress = progress_path or DEFAULT_OUTPUT_ROOT / "authoring_ledger" / "per_task_progress" / f"{slot_dir.name}.json"
        audit_progress(
            progress,
            slot_dir,
            task,
            allow_missing=allow_missing_progress,
            checks=checks,
            issues=issues,
        )

    report = {
        "schema_version": "2026-06-19",
        "artifact_type": ARTIFACT_TYPE,
        "formal_task_record": False,
        "slot_dir": str(slot_dir),
        "task_id": task.get("task_id") if task else None,
        "repo_id": task.get("repo_id") if task else None,
        "artifact_paths": artifact_paths,
        "allow_missing_progress": allow_missing_progress,
        "allow_missing_declaration": allow_missing_declaration,
        "passed": not issues,
        "issue_count": len(issues),
        "issues": issues,
        "checks": checks,
    }
    write_json(output_path, report)
    return report


def add_freeze_audit_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--slot-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--progress", type=Path, default=None)
    parser.add_argument("--declaration", type=Path, default=None)
    parser.add_argument("--allow-missing-progress", action="store_true")
    parser.add_argument("--allow-missing-declaration", action="store_true")


def add_declaration_template_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--slot-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)


def run_freeze_audit_from_args(args: argparse.Namespace) -> None:
    output = args.output or args.slot_dir / "single_task_freeze_audit.json"
    report = audit_single_task_freeze(
        slot_dir=args.slot_dir,
        output_path=output,
        progress_path=args.progress,
        declaration_path=args.declaration,
        allow_missing_progress=args.allow_missing_progress,
        allow_missing_declaration=args.allow_missing_declaration,
    )
    status = "passed" if report["passed"] else "failed"
    print(
        f"single-task freeze audit {status}: slot={Path(report['slot_dir']).name} "
        f"issues={report['issue_count']} output={output}"
    )
    if not report["passed"]:
        raise SystemExit(1)


def run_declaration_template_from_args(args: argparse.Namespace) -> None:
    slot_dir = args.slot_dir.resolve()
    task = audit_formal_task(slot_dir, [], [])
    if task is None:
        raise SystemExit(f"cannot build declaration template; {slot_dir / 'task.jsonl'} must contain exactly one valid task row")
    output = args.output or slot_dir / DECLARATION_TEMPLATE_NAME
    if output.name == DECLARATION_NAME:
        raise SystemExit(
            f"refusing to write a template to {output}; write templates to {DECLARATION_TEMPLATE_NAME} "
            f"and create {DECLARATION_NAME} only after one-slot inspection is complete"
        )
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing no-bulk declaration template: {output}")
    ensure_dir(output.parent)
    output.write_text(build_no_bulk_declaration_template(task, slot_id=slot_dir.name), encoding="utf-8")
    print(f"wrote non-passing no-bulk declaration template to {output}")
