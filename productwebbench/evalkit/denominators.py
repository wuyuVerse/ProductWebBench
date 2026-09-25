from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.config import DEFAULT_OUTPUT_ROOT
from ..core.formal_data_guard import assert_not_under_formal_task_root
from ..core.io_utils import ensure_dir, write_json


ARTIFACT_TYPE = "denominator_consistency_audit"
DEFAULT_COVERAGE_REPORT = DEFAULT_OUTPUT_ROOT / "authoring_ledger" / "coverage_gaps.json"
DEFAULT_OUTPUT = DEFAULT_OUTPUT_ROOT / "eval_protocol" / "denominator_consistency_audit.json"
EVALUABLE_DENOMINATOR_SOURCE = "data/productwebbench/tasks/slot_*/task.jsonl"
PUBLISHABLE_DENOMINATOR_SOURCE = "strict_freeze_authoring_audit.strict_clean_task_count"


def load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def as_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def require(condition: bool, issue: str, issues: list[str]) -> None:
    if not condition:
        issues.append(issue)


def coverage_group_sum(group: object) -> int:
    if not isinstance(group, dict):
        return 0
    total = 0
    for value in group.values():
        if isinstance(value, dict):
            total += as_int(value.get("accepted"))
    return total


def audit_denominator_consistency_from_status(status_report: dict[str, Any]) -> dict[str, Any]:
    alignment = status_report.get("change_ledger_alignment") or {}
    strict_freeze = status_report.get("strict_freeze_authoring_audit") or {}
    change = (status_report.get("regimes") or {}).get("change") or {}
    issues: list[str] = []
    warnings: list[str] = []

    require(bool(alignment), "change_ledger_alignment is missing", issues)
    require(bool(strict_freeze), "strict_freeze_authoring_audit is missing", issues)

    ledger_accepted = as_int(alignment.get("ledger_accepted"), as_int(change.get("accepted")))
    formal_rows = as_int(alignment.get("formal_task_rows"))
    formal_files = as_int(alignment.get("formal_task_files"))
    evaluable_rows = as_int(alignment.get("evaluable_formal_task_count"))
    publishable_rows = as_int(alignment.get("publishable_formal_task_count"))
    strict_publishable_rows = as_int(alignment.get("strict_publishable_formal_task_count"))
    accepted_without_formal = as_int(alignment.get("accepted_without_formal_task_count"))
    formal_without_accepted = as_int(alignment.get("formal_without_accepted_ledger_count"))
    duplicate_accepted = len(alignment.get("duplicate_accepted_task_ids") or [])
    duplicate_formal = len(alignment.get("duplicate_formal_task_ids") or [])
    change_formal_materialized = as_int(change.get("materialized_formal_task_count"))
    coverage_denominator = change.get("coverage_denominator")
    coverage = change.get("coverage") or {}
    formal_coverage = change.get("formal_materialized_coverage") or {}
    ledger_coverage = change.get("ledger_coverage") or {}

    strict_available = bool(strict_freeze.get("available"))
    strict_passed = bool(strict_freeze.get("passed"))
    strict_task_count = as_int(strict_freeze.get("task_count"))
    strict_clean = as_int(strict_freeze.get("strict_clean_task_count"))
    strict_blocked = as_int(strict_freeze.get("strict_blocked_task_count"))
    strict_unique_repo_ids_required = strict_freeze.get("unique_repo_ids_required") is True
    strict_duplicate_repo_ids = as_int(strict_freeze.get("duplicate_repo_id_count"))
    strict_global_policy_issues = as_int(strict_freeze.get("global_policy_issue_count"))

    require(
        alignment.get("evaluable_denominator_source") == EVALUABLE_DENOMINATOR_SOURCE,
        (
            "evaluable denominator source must be "
            f"{EVALUABLE_DENOMINATOR_SOURCE}, observed {alignment.get('evaluable_denominator_source')}"
        ),
        issues,
    )
    require(
        alignment.get("publishable_denominator_source") == PUBLISHABLE_DENOMINATOR_SOURCE,
        (
            "publishable denominator source must be "
            f"{PUBLISHABLE_DENOMINATOR_SOURCE}, observed {alignment.get('publishable_denominator_source')}"
        ),
        issues,
    )
    require(evaluable_rows == formal_rows, f"evaluable formal task count {evaluable_rows} != formal task rows {formal_rows}", issues)
    require(
        change_formal_materialized == evaluable_rows,
        (
            "change materialized formal task count "
            f"{change_formal_materialized} != evaluable formal rows {evaluable_rows}"
        ),
        issues,
    )
    require(
        coverage_denominator == "formal_materialized_task_rows",
        f"change coverage denominator must be formal_materialized_task_rows, observed {coverage_denominator}",
        issues,
    )
    require(
        coverage == formal_coverage,
        "change coverage must equal formal_materialized_coverage, not ledger/planning coverage",
        issues,
    )
    require(
        not ledger_coverage or coverage != ledger_coverage or ledger_accepted == evaluable_rows,
        "change coverage unexpectedly matches ledger_coverage while ledger/formal denominators differ",
        issues,
    )
    for group_name, group in coverage.items() if isinstance(coverage, dict) else []:
        group_sum = coverage_group_sum(group)
        require(
            group_sum == evaluable_rows,
            f"change coverage group {group_name} sums to {group_sum}, expected evaluable formal rows {evaluable_rows}",
            issues,
        )
    require(ledger_accepted >= evaluable_rows, f"ledger accepted {ledger_accepted} < evaluable formal rows {evaluable_rows}", issues)
    require(
        0 <= publishable_rows <= evaluable_rows,
        f"publishable formal task count {publishable_rows} must be between 0 and evaluable rows {evaluable_rows}",
        issues,
    )
    require(
        strict_publishable_rows == publishable_rows,
        f"strict publishable count {strict_publishable_rows} != publishable count {publishable_rows}",
        issues,
    )
    require(duplicate_accepted == 0, f"duplicate accepted ledger task ids present: {duplicate_accepted}", issues)
    require(duplicate_formal == 0, f"duplicate formal task ids present: {duplicate_formal}", issues)
    require(
        accepted_without_formal + formal_rows - formal_without_accepted == ledger_accepted,
        (
            "ledger/formal gap equation failed: "
            f"accepted_without_formal {accepted_without_formal} + formal_rows {formal_rows} "
            f"- formal_without_accepted {formal_without_accepted} != ledger_accepted {ledger_accepted}"
        ),
        issues,
    )
    if formal_files and formal_rows:
        require(formal_files == formal_rows, f"formal task files {formal_files} != formal task rows {formal_rows}", issues)

    if strict_available:
        require(strict_task_count == formal_rows, f"strict freeze task count {strict_task_count} != formal task rows {formal_rows}", issues)
        require(
            strict_clean + strict_blocked == strict_task_count,
            (
                "strict freeze clean/blocked equation failed: "
                f"clean {strict_clean} + blocked {strict_blocked} != task_count {strict_task_count}"
            ),
            issues,
        )
        require(
            publishable_rows == strict_clean,
            f"publishable formal task count {publishable_rows} != strict clean task count {strict_clean}",
            issues,
        )
        if strict_clean > 0 or publishable_rows > 0:
            require(strict_passed, "strict clean/publishable rows require the strict freeze audit to pass", issues)
            require(
                strict_unique_repo_ids_required,
                "strict clean/publishable rows require unique_repo_ids_required=true",
                issues,
            )
            require(
                strict_duplicate_repo_ids == 0,
                f"strict clean/publishable rows require 0 duplicate repo ids, observed {strict_duplicate_repo_ids}",
                issues,
            )
            require(
                strict_global_policy_issues == 0,
                (
                    "strict clean/publishable rows require 0 strict global policy issues, "
                    f"observed {strict_global_policy_issues}"
                ),
                issues,
            )
        else:
            require(
                strict_unique_repo_ids_required or strict_global_policy_issues > 0,
                "strict freeze audit must either require unique repo ids or keep strict_clean/publishable at 0 with a policy issue",
                issues,
            )
            if not strict_unique_repo_ids_required:
                warnings.append("strict freeze audit did not require unique repo ids; publishable denominator remains 0")
            if strict_duplicate_repo_ids:
                warnings.append(
                    f"strict freeze audit reports {strict_duplicate_repo_ids} duplicate repo ids; publishable denominator remains 0"
                )
            if strict_global_policy_issues:
                warnings.append(
                    f"strict freeze audit has {strict_global_policy_issues} global policy issue(s); publishable denominator remains 0"
                )
    else:
        require(publishable_rows == 0, "publishable formal task count must be 0 when strict freeze audit is unavailable", issues)

    if accepted_without_formal:
        warnings.append(
            f"{accepted_without_formal} accepted ledger rows are planning progress only and are excluded from the evaluable denominator"
        )
    if publishable_rows < evaluable_rows:
        warnings.append(
            f"{evaluable_rows - publishable_rows} formal rows are evaluable/exportable but not strict-publishable"
        )

    return {
        "schema_version": "2026-06-19",
        "artifact_type": ARTIFACT_TYPE,
        "formal_task_record": False,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "passed": not issues,
        "issue_count": len(issues),
        "issues": issues,
        "warning_count": len(warnings),
        "warnings": warnings,
        "summary": {
            "ledger_accepted": ledger_accepted,
            "formal_task_rows": formal_rows,
            "formal_task_files": formal_files,
            "evaluable_formal_task_count": evaluable_rows,
            "change_materialized_formal_task_count": change_formal_materialized,
            "change_coverage_denominator": coverage_denominator,
            "change_coverage_group_sums": {
                name: coverage_group_sum(group)
                for name, group in coverage.items()
            }
            if isinstance(coverage, dict)
            else {},
            "publishable_formal_task_count": publishable_rows,
            "strict_publishable_formal_task_count": strict_publishable_rows,
            "accepted_without_formal_task_count": accepted_without_formal,
            "formal_without_accepted_ledger_count": formal_without_accepted,
            "strict_freeze_available": strict_available,
            "strict_freeze_passed": strict_passed,
            "strict_freeze_task_count": strict_task_count,
            "strict_clean_task_count": strict_clean,
            "strict_blocked_task_count": strict_blocked,
            "strict_unique_repo_ids_required": strict_unique_repo_ids_required,
            "strict_duplicate_repo_id_count": strict_duplicate_repo_ids,
            "strict_global_policy_issue_count": strict_global_policy_issues,
            "evaluable_denominator_source": alignment.get("evaluable_denominator_source"),
            "publishable_denominator_source": alignment.get("publishable_denominator_source"),
        },
        "policy": {
            "ledger_rows_are_progress_only": True,
            "evaluable_denominator": EVALUABLE_DENOMINATOR_SOURCE,
            "publishable_denominator": PUBLISHABLE_DENOMINATOR_SOURCE,
            "formal_task_generation": "manual_one_task_at_a_time_only",
            "automation_scope": "candidate ranking, evidence capture, verifier execution, scoring, and audits only",
        },
    }


def audit_denominator_consistency(coverage_report_path: Path, output_path: Path | None = None) -> dict[str, Any]:
    status = load_json_if_exists(coverage_report_path)
    if not status:
        report = {
            "schema_version": "2026-06-19",
            "artifact_type": ARTIFACT_TYPE,
            "formal_task_record": False,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "coverage_report_path": str(coverage_report_path),
            "passed": False,
            "issue_count": 1,
            "issues": [f"coverage report missing: {coverage_report_path}"],
            "warning_count": 0,
            "warnings": [],
            "summary": {},
            "policy": {
                "formal_task_generation": "manual_one_task_at_a_time_only",
                "automation_scope": "candidate ranking, evidence capture, verifier execution, scoring, and audits only",
            },
        }
    else:
        report = audit_denominator_consistency_from_status(status)
        report["coverage_report_path"] = str(coverage_report_path)
    if output_path is not None:
        write_json(output_path, report)
    return report


def add_denominator_audit_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--coverage-report", type=Path, default=DEFAULT_COVERAGE_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)


def run_denominator_audit_from_args(args: argparse.Namespace) -> None:
    try:
        assert_not_under_formal_task_root(args.output, purpose="Denominator consistency audit")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    ensure_dir(args.output.parent)
    report = audit_denominator_consistency(args.coverage_report, output_path=args.output)
    summary = report.get("summary", {})
    print(
        "denominator consistency audit: "
        f"passed={report.get('passed')} "
        f"ledger={summary.get('ledger_accepted')} "
        f"evaluable={summary.get('evaluable_formal_task_count')} "
        f"publishable={summary.get('publishable_formal_task_count')} "
        f"issues={report.get('issue_count')}"
    )
    if not report.get("passed"):
        raise SystemExit(1)
