from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...core.config import DEFAULT_OUTPUT_ROOT
from ...core.formal_data_guard import assert_not_under_formal_task_root
from ...core.io_utils import ensure_dir, write_json
from .reference_actor import (
    MILESTONE_RUN_ARTIFACT_TYPE,
    REQUIRED_ARTIFACT_REF_KEYS,
    TRACE_SCHEMA_VERSION,
    candidate_artifact_paths,
    derive_milestone_result,
    milestone_ids,
    relative_ref,
    validate_milestone_signal_report,
)
from .scoring import score_trajectory
from .task_digest import stable_task_digest


TRAJECTORY_RUN_ARTIFACT_TYPE = "construction_trajectory_run_report"
SOURCE_KINDS = (
    "spec_only_actor_run",
    "repeat_spec_only_actor_run",
    "model_actor_run",
    "empty_baseline",
    "bad_solution",
)
MILESTONE_ARTIFACT_REF_KEYS = (
    "workspace",
    "capture_report",
    "verifier_report",
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def task_milestone(task: dict[str, Any], milestone_id: str) -> dict[str, Any] | None:
    for milestone in task.get("milestones", []):
        if isinstance(milestone, dict) and str(milestone.get("milestone_id", "")) == milestone_id:
            return milestone
    return None


def boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "pass", "passed"}
    return bool(value)


def regression_events(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 1


def validate_artifact_refs(
    artifact_refs: dict[str, Any],
    *,
    required_keys: tuple[str, ...],
    artifact_root: Path | None,
    require_existing_artifacts: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    issues: list[str] = []
    checks: list[dict[str, Any]] = []
    refs_ok = (
        isinstance(artifact_refs, dict)
        and all(isinstance(artifact_refs.get(key), str) and artifact_refs.get(key).strip() for key in required_keys)
    )
    checks.append(
        {
            "name": "artifact_refs_present",
            "passed": refs_ok,
            "required_keys": list(required_keys),
            "issue": "artifact refs are incomplete",
        }
    )
    if not refs_ok:
        issues.append("artifact refs are incomplete")
        return checks, issues

    if require_existing_artifacts:
        resolved_refs: dict[str, str] = {}
        missing_ref_paths: dict[str, list[str]] = {}
        for key in required_keys:
            raw_ref = str(artifact_refs[key]).strip()
            candidates = candidate_artifact_paths(raw_ref, artifact_root)
            existing = next((candidate for candidate in candidates if candidate.exists()), None)
            if existing is None:
                missing_ref_paths[key] = [str(candidate) for candidate in candidates]
            else:
                resolved_refs[key] = str(existing)
        refs_exist = not missing_ref_paths
        checks.append(
            {
                "name": "artifact_refs_exist",
                "passed": refs_exist,
                "artifact_root": str(artifact_root) if artifact_root else None,
                "resolved_refs": resolved_refs,
                "missing_ref_paths": missing_ref_paths,
                "issue": "artifact refs do not exist",
            }
        )
        if not refs_exist:
            issues.append("artifact refs do not exist")
    return checks, issues


def build_milestone_run_report(
    task: dict[str, Any],
    *,
    milestone_id: str,
    workspace: Path,
    capture_report_path: Path,
    verifier_report_path: Path,
    output_path: Path,
    source_kind: str,
    trajectory_source_path: Path | None = None,
) -> dict[str, Any]:
    milestone = task_milestone(task, milestone_id)
    if milestone is None:
        raise ValueError(f"unknown milestone_id for task: {milestone_id}")
    if source_kind not in SOURCE_KINDS:
        raise ValueError(f"invalid construction trajectory source_kind: {source_kind}")
    if not workspace.exists():
        raise FileNotFoundError(f"workspace does not exist: {workspace}")
    if not capture_report_path.exists():
        raise FileNotFoundError(f"capture_report does not exist: {capture_report_path}")
    if not verifier_report_path.exists():
        raise FileNotFoundError(f"verifier_report does not exist: {verifier_report_path}")

    capture_report = load_json(capture_report_path)
    verifier_report = load_json(verifier_report_path)
    capture_validation = validate_milestone_signal_report(
        task,
        capture_report,
        report_kind="capture_report",
        milestone_id=milestone_id,
        source_kind=source_kind,
    )
    verifier_validation = validate_milestone_signal_report(
        task,
        verifier_report,
        report_kind="verifier_report",
        milestone_id=milestone_id,
        source_kind=source_kind,
    )
    trajectory_source = load_json(trajectory_source_path) if trajectory_source_path else {}
    result, result_issues = derive_milestone_result(
        milestone_id,
        trajectory_source,
        verifier_report,
        capture_report,
    )
    signal_validation_issues = [
        *capture_validation.get("issues", []),
        *verifier_validation.get("issues", []),
    ]
    result_issues.extend(signal_validation_issues)
    hard_passed = bool(result.get("hard_passed"))
    metric_passed = bool(result.get("metric_passed"))
    regressions = regression_events(result.get("regression_events", 0))
    passed = (
        bool(capture_validation.get("passed"))
        and bool(verifier_validation.get("passed"))
        and hard_passed
        and metric_passed
        and regressions == 0
    )
    output_dir = output_path.parent
    report: dict[str, Any] = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "artifact_type": MILESTONE_RUN_ARTIFACT_TYPE,
        "formal_task_record": False,
        "generated_at": utc_now(),
        "task_id": task.get("task_id"),
        "repo_id": task.get("repo_id"),
        "task_sha256": stable_task_digest(task),
        "source_kind": source_kind,
        "milestone_id": milestone_id,
        "ladder_step": milestone.get("ladder_step", milestone_id),
        "passed": passed,
        "hard_passed": hard_passed,
        "metric_passed": metric_passed,
        "regression_events": regressions,
        "metric_score": result.get("metric_score"),
        "soft_score": result.get("soft_score"),
        "failure_type": result.get("failure_type"),
        "derivation_issues": result_issues,
        "input_report_validations": {
            "capture_report": capture_validation,
            "verifier_report": verifier_validation,
        },
        "artifact_refs": {
            "workspace": relative_ref(workspace, output_dir),
            "capture_report": relative_ref(capture_report_path, output_dir),
            "verifier_report": relative_ref(verifier_report_path, output_dir),
        },
    }
    if trajectory_source_path is not None:
        report["artifact_refs"]["trajectory_source"] = relative_ref(trajectory_source_path, output_dir)
    if result_issues and passed:
        report["passed"] = False
        report["failure_type"] = report.get("failure_type") or "derivation_issue"
    if signal_validation_issues:
        report["failure_type"] = report.get("failure_type") or "unbound_signal_report"
    return report


def validate_milestone_run_report(
    task: dict[str, Any],
    report: dict[str, Any] | None,
    *,
    artifact_root: Path | None = None,
    require_existing_artifacts: bool = False,
) -> dict[str, Any]:
    issues: list[str] = []
    checks: list[dict[str, Any]] = []

    if not isinstance(report, dict):
        return {
            "name": "construction_milestone_run_report",
            "passed": False,
            "issues": ["missing construction milestone run report"],
            "checks": [{"name": "milestone_run_report_present", "passed": False}],
        }

    def add_check(name: str, passed: bool, **details: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), **details})
        if not passed:
            issues.append(details.get("issue") or name)

    mid = str(report.get("milestone_id", ""))
    milestone = task_milestone(task, mid)
    add_check(
        "milestone_run_schema_version",
        report.get("schema_version") == TRACE_SCHEMA_VERSION,
        observed=report.get("schema_version"),
        expected=TRACE_SCHEMA_VERSION,
        issue="construction milestone run report has wrong schema_version",
    )
    add_check(
        "milestone_run_artifact_type",
        report.get("artifact_type") == MILESTONE_RUN_ARTIFACT_TYPE,
        observed=report.get("artifact_type"),
        expected=MILESTONE_RUN_ARTIFACT_TYPE,
        issue="construction milestone run report has wrong artifact_type",
    )
    add_check(
        "milestone_run_not_formal",
        report.get("formal_task_record") is False,
        observed=report.get("formal_task_record"),
        issue="construction milestone run report must be non-formal",
    )
    add_check(
        "milestone_run_source_kind",
        report.get("source_kind") in SOURCE_KINDS,
        observed=report.get("source_kind"),
        expected=list(SOURCE_KINDS),
        issue="construction milestone run report has invalid source_kind",
    )
    add_check(
        "milestone_run_task_id_matches_task",
        report.get("task_id") == task.get("task_id"),
        observed=report.get("task_id"),
        expected=task.get("task_id"),
        issue="construction milestone run report task_id does not match task",
    )
    add_check(
        "milestone_run_repo_id_matches_task",
        report.get("repo_id") == task.get("repo_id"),
        observed=report.get("repo_id"),
        expected=task.get("repo_id"),
        issue="construction milestone run report repo_id does not match task",
    )
    add_check(
        "milestone_run_task_sha256_matches_task",
        report.get("task_sha256") == stable_task_digest(task),
        observed=report.get("task_sha256"),
        expected=stable_task_digest(task),
        issue="construction milestone run report task_sha256 does not match task",
    )
    add_check(
        "milestone_id_matches_task",
        milestone is not None,
        observed=mid,
        expected=milestone_ids(task),
        issue="construction milestone run report has unknown milestone_id",
    )
    if milestone is not None:
        add_check(
            "milestone_ladder_step_matches_task",
            report.get("ladder_step") == milestone.get("ladder_step"),
            observed=report.get("ladder_step"),
            expected=milestone.get("ladder_step"),
            issue=f"{mid} ladder_step does not match task",
        )

    hard_passed = boolish(report.get("hard_passed"))
    metric_passed = boolish(report.get("metric_passed"))
    regressions = regression_events(report.get("regression_events", 0))
    input_validations = report.get("input_report_validations", {})
    capture_input_validation = input_validations.get("capture_report") if isinstance(input_validations, dict) else None
    verifier_input_validation = input_validations.get("verifier_report") if isinstance(input_validations, dict) else None
    signal_validations_passed = (
        isinstance(capture_input_validation, dict)
        and bool(capture_input_validation.get("passed"))
        and isinstance(verifier_input_validation, dict)
        and bool(verifier_input_validation.get("passed"))
    )
    expected_passed = hard_passed and metric_passed and regressions == 0 and signal_validations_passed
    add_check(
        "milestone_pass_consistent",
        boolish(report.get("passed")) == expected_passed,
        report_passed=report.get("passed"),
        hard_passed=hard_passed,
        metric_passed=metric_passed,
        regression_events=regressions,
        signal_validations_passed=signal_validations_passed,
        issue=f"{mid or '<unknown>'} passed flag does not match hard/metric/regression/signal-validation fields",
    )
    add_check(
        "capture_report_binding_validation_passed",
        isinstance(capture_input_validation, dict) and bool(capture_input_validation.get("passed")),
        issue=f"{mid or '<unknown>'} capture_report binding validation is missing or failed",
    )
    add_check(
        "verifier_report_binding_validation_passed",
        isinstance(verifier_input_validation, dict) and bool(verifier_input_validation.get("passed")),
        issue=f"{mid or '<unknown>'} verifier_report binding validation is missing or failed",
    )
    ref_checks, ref_issues = validate_artifact_refs(
        report.get("artifact_refs", {}),
        required_keys=MILESTONE_ARTIFACT_REF_KEYS,
        artifact_root=artifact_root,
        require_existing_artifacts=require_existing_artifacts,
    )
    for check in ref_checks:
        check["milestone_id"] = mid
    checks.extend(ref_checks)
    issues.extend(f"{mid}: {issue}" for issue in ref_issues)
    return {
        "name": "construction_milestone_run_report",
        "passed": not issues,
        "issues": issues,
        "checks": checks,
    }


def milestone_evidence_dir(evidence_root: Path, milestone_id: str) -> Path:
    return evidence_root / "evidence" / milestone_id


def build_trajectory_run_report(
    task: dict[str, Any],
    *,
    evidence_root: Path,
    output_path: Path,
    source_kind: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if source_kind not in SOURCE_KINDS:
        raise ValueError(f"invalid construction trajectory source_kind: {source_kind}")
    issues: list[str] = []
    checks: list[dict[str, Any]] = []
    output_dir = output_path.parent
    milestones: list[dict[str, Any]] = []

    for mid in milestone_ids(task):
        root = milestone_evidence_dir(evidence_root, mid)
        milestone_report_path = root / "trajectory_report.json"
        capture_report_path = root / "capture_report.json"
        verifier_report_path = root / "verifier_report.json"
        workspace_path = root / "workspace"
        if not milestone_report_path.exists():
            issues.append(f"{mid} trajectory_report.json is missing")
            checks.append({"name": "milestone_run_report_present", "milestone_id": mid, "passed": False})
            continue
        milestone_report = load_json(milestone_report_path)
        validation = validate_milestone_run_report(
            task,
            milestone_report,
            artifact_root=milestone_report_path.parent,
            require_existing_artifacts=True,
        )
        checks.append(
            {
                "name": "milestone_run_report_valid",
                "milestone_id": mid,
                "passed": validation.get("passed"),
                "details": validation,
            }
        )
        if not validation.get("passed"):
            issues.extend(validation.get("issues", []))
        capture_report = load_json(capture_report_path) if capture_report_path.exists() else {}
        verifier_report = load_json(verifier_report_path) if verifier_report_path.exists() else {}
        result, result_issues = derive_milestone_result(mid, milestone_report, verifier_report, capture_report)
        issues.extend(result_issues)
        item: dict[str, Any] = {
            "milestone_id": mid,
            "ladder_step": (task_milestone(task, mid) or {}).get("ladder_step", mid),
            "hard_passed": bool(result.get("hard_passed")),
            "metric_passed": bool(result.get("metric_passed")),
            "regression_events": regression_events(result.get("regression_events", 0)),
            "metric_score": result.get("metric_score"),
            "soft_score": result.get("soft_score"),
            "failure_type": result.get("failure_type"),
            "artifact_refs": {
                "workspace": relative_ref(workspace_path, output_dir),
                "trajectory_report": relative_ref(milestone_report_path, output_dir),
                "capture_report": relative_ref(capture_report_path, output_dir),
                "verifier_report": relative_ref(verifier_report_path, output_dir),
            },
        }
        milestones.append(item)

    report: dict[str, Any] = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "artifact_type": TRAJECTORY_RUN_ARTIFACT_TYPE,
        "formal_task_record": False,
        "generated_at": utc_now(),
        "task_id": task.get("task_id"),
        "repo_id": task.get("repo_id"),
        "task_sha256": stable_task_digest(task),
        "source_kind": source_kind,
        "evidence_root": relative_ref(evidence_root, output_dir),
        "milestones": milestones,
    }
    score = score_trajectory(report)
    report["score"] = score
    report["TCS"] = score.get("TCS")
    report["TD"] = score.get("TD")
    report["ITR"] = score.get("ITR")
    report["QS"] = score.get("QS")
    report["passed"] = bool(score.get("TCS", {}).get("passed")) and not issues
    validation = validate_trajectory_run_report(
        task,
        report,
        artifact_root=output_dir,
        require_existing_artifacts=True,
    )
    return report, {
        "name": "construction_trajectory_run_builder",
        "passed": validation.get("passed") and not issues,
        "issues": issues + validation.get("issues", []),
        "checks": checks,
        "trajectory_validation": validation,
    }


def validate_trajectory_run_report(
    task: dict[str, Any],
    report: dict[str, Any] | None,
    *,
    artifact_root: Path | None = None,
    require_existing_artifacts: bool = False,
) -> dict[str, Any]:
    issues: list[str] = []
    checks: list[dict[str, Any]] = []

    if not isinstance(report, dict):
        return {
            "name": "construction_trajectory_run_report",
            "passed": False,
            "issues": ["missing construction trajectory run report"],
            "checks": [{"name": "trajectory_run_report_present", "passed": False}],
        }

    def add_check(name: str, passed: bool, **details: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), **details})
        if not passed:
            issues.append(details.get("issue") or name)

    observed_ids = [str(item.get("milestone_id", "")) for item in report.get("milestones", []) if isinstance(item, dict)]
    expected_ids = milestone_ids(task)
    add_check(
        "trajectory_run_schema_version",
        report.get("schema_version") == TRACE_SCHEMA_VERSION,
        observed=report.get("schema_version"),
        expected=TRACE_SCHEMA_VERSION,
        issue="construction trajectory run report has wrong schema_version",
    )
    add_check(
        "trajectory_run_artifact_type",
        report.get("artifact_type") == TRAJECTORY_RUN_ARTIFACT_TYPE,
        observed=report.get("artifact_type"),
        expected=TRAJECTORY_RUN_ARTIFACT_TYPE,
        issue="construction trajectory run report has wrong artifact_type",
    )
    add_check(
        "trajectory_run_not_formal",
        report.get("formal_task_record") is False,
        observed=report.get("formal_task_record"),
        issue="construction trajectory run report must be non-formal",
    )
    add_check(
        "trajectory_run_source_kind",
        report.get("source_kind") in SOURCE_KINDS,
        observed=report.get("source_kind"),
        expected=list(SOURCE_KINDS),
        issue="construction trajectory run report has invalid source_kind",
    )
    add_check(
        "trajectory_run_task_id_matches_task",
        report.get("task_id") == task.get("task_id"),
        observed=report.get("task_id"),
        expected=task.get("task_id"),
        issue="construction trajectory run report task_id does not match task",
    )
    add_check(
        "trajectory_run_repo_id_matches_task",
        report.get("repo_id") == task.get("repo_id"),
        observed=report.get("repo_id"),
        expected=task.get("repo_id"),
        issue="construction trajectory run report repo_id does not match task",
    )
    add_check(
        "trajectory_run_task_sha256_matches_task",
        report.get("task_sha256") == stable_task_digest(task),
        observed=report.get("task_sha256"),
        expected=stable_task_digest(task),
        issue="construction trajectory run report task_sha256 does not match task",
    )
    add_check(
        "trajectory_milestone_sequence_matches_task",
        observed_ids == expected_ids,
        observed=observed_ids,
        expected=expected_ids,
        issue="construction trajectory run milestone sequence does not match task",
    )
    score = score_trajectory(report)
    add_check(
        "trajectory_metrics_present",
        isinstance(report.get("TCS"), dict) and isinstance(report.get("TD"), dict) and isinstance(report.get("ITR"), dict),
        issue="construction trajectory run report is missing TCS/TD/ITR",
    )
    add_check(
        "trajectory_pass_consistent",
        boolish(report.get("passed")) == bool(score.get("TCS", {}).get("passed")),
        report_passed=report.get("passed"),
        tcs_passed=score.get("TCS", {}).get("passed"),
        issue="construction trajectory run passed flag does not match TCS",
    )
    for item in report.get("milestones", []):
        if not isinstance(item, dict):
            continue
        mid = str(item.get("milestone_id", ""))
        ref_checks, ref_issues = validate_artifact_refs(
            item.get("artifact_refs", {}),
            required_keys=REQUIRED_ARTIFACT_REF_KEYS,
            artifact_root=artifact_root,
            require_existing_artifacts=require_existing_artifacts,
        )
        for check in ref_checks:
            check["milestone_id"] = mid
        checks.extend(ref_checks)
        issues.extend(f"{mid}: {issue}" for issue in ref_issues)
    return {
        "name": "construction_trajectory_run_report",
        "passed": not issues,
        "issues": issues,
        "checks": checks,
        "score": score,
    }


def add_milestone_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--milestone-id", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--capture-report", type=Path, required=True)
    parser.add_argument("--verifier-report", type=Path, required=True)
    parser.add_argument("--trajectory-source", type=Path, default=None)
    parser.add_argument("--source-kind", choices=SOURCE_KINDS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validation-output", type=Path, default=None)


def add_trajectory_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--source-kind", choices=SOURCE_KINDS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validation-output", type=Path, default=None)


def add_validate_trajectory_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--require-existing-artifacts", action="store_true")


def run_milestone_from_args(args: argparse.Namespace) -> None:
    try:
        assert_not_under_formal_task_root(args.output, purpose="Construction milestone run report")
        if args.validation_output is not None:
            assert_not_under_formal_task_root(
                args.validation_output,
                purpose="Construction milestone run validation report",
            )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    task = load_json(args.task)
    report = build_milestone_run_report(
        task,
        milestone_id=args.milestone_id,
        workspace=args.workspace,
        capture_report_path=args.capture_report,
        verifier_report_path=args.verifier_report,
        output_path=args.output,
        source_kind=args.source_kind,
        trajectory_source_path=args.trajectory_source,
    )
    validation = validate_milestone_run_report(
        task,
        report,
        artifact_root=args.output.parent,
        require_existing_artifacts=True,
    )
    if args.validation_output is not None:
        ensure_dir(args.validation_output.parent)
        write_json(args.validation_output, validation)
    ensure_dir(args.output.parent)
    write_json(args.output, report)
    print(
        "construction milestone run "
        f"milestone={report['milestone_id']} passed={report['passed']} "
        f"hard={report['hard_passed']} metric={report['metric_passed']} "
        f"regressions={report['regression_events']} output={args.output}"
    )


def run_trajectory_from_args(args: argparse.Namespace) -> None:
    try:
        assert_not_under_formal_task_root(args.output, purpose="Construction trajectory run report")
        if args.validation_output is not None:
            assert_not_under_formal_task_root(
                args.validation_output,
                purpose="Construction trajectory run validation report",
            )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    task = load_json(args.task)
    report, validation = build_trajectory_run_report(
        task,
        evidence_root=args.evidence_root,
        output_path=args.output,
        source_kind=args.source_kind,
    )
    if args.validation_output is not None:
        ensure_dir(args.validation_output.parent)
        write_json(args.validation_output, validation)
    ensure_dir(args.output.parent)
    write_json(args.output, report)
    score = report.get("score", {})
    print(
        "construction trajectory run "
        f"passed={report['passed']} TCS={score.get('TCS', {}).get('score')} "
        f"TD={score.get('TD', {}).get('score')} ITR={score.get('ITR', {}).get('score')} "
        f"output={args.output}"
    )
    if not validation.get("passed"):
        raise SystemExit("; ".join(validation.get("issues", [])))


def run_validate_trajectory_from_args(args: argparse.Namespace) -> None:
    if args.output is not None:
        try:
            assert_not_under_formal_task_root(args.output, purpose="Construction trajectory validation report")
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    task = load_json(args.task)
    report = load_json(args.report)
    validation = validate_trajectory_run_report(
        task,
        report,
        artifact_root=args.report.parent,
        require_existing_artifacts=args.require_existing_artifacts,
    )
    if args.output is not None:
        ensure_dir(args.output.parent)
        write_json(args.output, validation)
    print(
        "construction trajectory run validation "
        f"passed={validation['passed']} issues={len(validation.get('issues', []))}"
    )
    if not validation.get("passed"):
        raise SystemExit("; ".join(validation.get("issues", [])))
