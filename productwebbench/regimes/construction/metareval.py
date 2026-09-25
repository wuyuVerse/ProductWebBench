from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...core.formal_data_guard import assert_not_under_formal_task_root
from ...core.io_utils import ensure_dir, write_json
from ...evalkit.metareval import compare_reproducible, summarize_report
from .evidence_reports import validate_construction_trajectory_evidence_report
from .reference_actor import TRACE_SCHEMA_VERSION, candidate_artifact_paths
from .task_digest import stable_task_digest


META_EVAL_ARTIFACT_TYPE = "construction_metareval_report"
REQUIRED_META_EVAL_REFS = (
    "reference_report",
    "original_report",
    "bad_solution_report",
    "repeat_report",
)
EXPECTED_EVIDENCE_KIND_BY_REF = {
    "reference_report": "reference",
    "original_report": "original",
    "bad_solution_report": "bad_solution",
    "repeat_report": "repeat",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "pass", "passed"}
    return bool(value)


def bad_solution_failed(report: dict[str, Any]) -> bool:
    if "bad_solution_failed" in report:
        return boolish(report.get("bad_solution_failed"))
    return boolish(report.get("bad_solutions_failed"))


def evidence_ref(path: Path, output_dir: Path) -> str:
    try:
        return str(path.resolve().relative_to(output_dir.resolve()))
    except ValueError:
        return os.path.relpath(path.resolve(), output_dir.resolve())


def summarize_expected(path: Path, expected: str, *, task: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, list[str]]:
    if not path.exists():
        return None, [f"missing {expected} report: {path}"]
    try:
        evidence_validation = validate_construction_trajectory_evidence_report(load_json(path), task=task)
        if not evidence_validation.get("passed"):
            return None, [f"invalid construction trajectory evidence report {path}: {'; '.join(evidence_validation.get('issues', []))}"]
        return summarize_report(path, expected), []
    except Exception as exc:  # pragma: no cover - defensive for corrupt external artifacts
        return None, [f"could not read {expected} report {path}: {exc}"]


def build_construction_metareval_report(
    *,
    reference_report: Path,
    original_report: Path,
    bad_solution_report: Path,
    repeat_report: Path,
    output_path: Path,
    task: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output_dir = output_path.parent
    issues: list[str] = []
    reference, reference_issues = summarize_expected(reference_report, "pass", task=task)
    original, original_issues = summarize_expected(original_report, "fail", task=task)
    bad_solution, bad_issues = summarize_expected(bad_solution_report, "fail", task=task)
    repeat, repeat_issues = summarize_expected(repeat_report, "pass", task=task)
    issues.extend(reference_issues)
    issues.extend(original_issues)
    issues.extend(bad_issues)
    issues.extend(repeat_issues)

    reference_passed = bool(reference and reference.get("ok"))
    original_failed = bool(original and original.get("ok"))
    bad_failed = bool(bad_solution and bad_solution.get("ok"))
    reproducible = compare_reproducible([reference], [repeat]) if reference and repeat else False
    if reference and not reference_passed:
        issues.append("reference report did not pass")
    if original and not original_failed:
        issues.append("original/empty baseline report did not fail")
    if bad_solution and not bad_failed:
        issues.append("bad-solution report did not fail")
    if reference and repeat and not reproducible:
        issues.append("repeat report is not reproducible with reference")

    report = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "artifact_type": META_EVAL_ARTIFACT_TYPE,
        "formal_task_record": False,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "task_id": task.get("task_id") if isinstance(task, dict) else None,
        "repo_id": task.get("repo_id") if isinstance(task, dict) else None,
        "task_sha256": stable_task_digest(task) if isinstance(task, dict) else None,
        "passed": reference_passed and original_failed and bad_failed and reproducible and not issues,
        "reference_passed": reference_passed,
        "original_failed": original_failed,
        "bad_solution_failed": bad_failed,
        "reproducible": reproducible,
        "evidence_refs": {
            "reference_report": evidence_ref(reference_report, output_dir),
            "original_report": evidence_ref(original_report, output_dir),
            "bad_solution_report": evidence_ref(bad_solution_report, output_dir),
            "repeat_report": evidence_ref(repeat_report, output_dir),
        },
        "summaries": {
            "reference_report": reference,
            "original_report": original,
            "bad_solution_report": bad_solution,
            "repeat_report": repeat,
        },
        "issues": issues,
    }
    return report


def validate_construction_metareval_report(
    report: dict[str, Any] | None,
    *,
    task: dict[str, Any] | None = None,
    artifact_root: Path | None = None,
    require_existing_artifacts: bool = True,
) -> dict[str, Any]:
    issues: list[str] = []
    checks: list[dict[str, Any]] = []

    if not isinstance(report, dict):
        return {
            "name": "construction_metareval",
            "passed": False,
            "issues": ["missing construction metareval report"],
            "checks": [{"name": "metareval_report_present", "passed": False}],
        }

    def add_check(name: str, passed: bool, **details: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), **details})
        if not passed:
            issues.append(details.get("issue") or name)

    add_check(
        "metareval_schema_version",
        report.get("schema_version") == TRACE_SCHEMA_VERSION,
        observed=report.get("schema_version"),
        expected=TRACE_SCHEMA_VERSION,
        issue="construction metareval report has wrong schema_version",
    )
    add_check(
        "metareval_artifact_type",
        report.get("artifact_type") == META_EVAL_ARTIFACT_TYPE,
        observed=report.get("artifact_type"),
        expected=META_EVAL_ARTIFACT_TYPE,
        issue="construction metareval report must be a completed metareval artifact",
    )
    add_check(
        "metareval_non_formal",
        report.get("formal_task_record") is False,
        observed=report.get("formal_task_record"),
        issue="construction metareval report must be non-formal",
    )
    add_check(
        "metareval_report_passed",
        boolish(report.get("passed")),
        issue="construction metareval report did not pass",
    )

    expected_flags = {
        "reference_passed": boolish(report.get("reference_passed")),
        "original_failed": boolish(report.get("original_failed")),
        "bad_solution_failed": bad_solution_failed(report),
        "reproducible": boolish(report.get("reproducible")),
    }
    add_check(
        "metareval_sanity_flags",
        all(expected_flags.values()),
        flags=expected_flags,
        issue="construction metareval sanity flags are not all true",
    )

    evidence_refs = report.get("evidence_refs", {})
    refs_ok = (
        isinstance(evidence_refs, dict)
        and all(isinstance(evidence_refs.get(key), str) and bool(evidence_refs.get(key).strip()) for key in REQUIRED_META_EVAL_REFS)
    )
    add_check(
        "metareval_evidence_refs_present",
        refs_ok,
        required_keys=list(REQUIRED_META_EVAL_REFS),
        issue="construction metareval evidence refs are incomplete",
    )

    resolved_refs: dict[str, str] = {}
    missing_ref_paths: dict[str, list[str]] = {}
    invalid_ref_reports: dict[str, list[str]] = {}
    if require_existing_artifacts and refs_ok:
        for key in REQUIRED_META_EVAL_REFS:
            raw_ref = str(evidence_refs[key]).strip()
            candidates = candidate_artifact_paths(raw_ref, artifact_root)
            existing = next((candidate for candidate in candidates if candidate.exists()), None)
            if existing is None:
                missing_ref_paths[key] = [str(candidate) for candidate in candidates]
            else:
                resolved_refs[key] = str(existing)
                try:
                    ref_report = load_json(existing)
                except Exception as exc:  # pragma: no cover - defensive for corrupt external artifacts
                    invalid_ref_reports[key] = [f"could not read evidence report: {exc}"]
                    continue
                ref_validation = validate_construction_trajectory_evidence_report(ref_report, task=task)
                ref_issues = list(ref_validation.get("issues", []))
                expected_kind = EXPECTED_EVIDENCE_KIND_BY_REF[key]
                if ref_report.get("evidence_kind") != expected_kind:
                    ref_issues.append(f"evidence_kind must be {expected_kind}")
                if ref_issues:
                    invalid_ref_reports[key] = ref_issues
    if require_existing_artifacts:
        add_check(
            "metareval_evidence_refs_exist",
            refs_ok and not missing_ref_paths,
            artifact_root=str(artifact_root) if artifact_root else None,
            resolved_refs=resolved_refs,
            missing_ref_paths=missing_ref_paths,
            issue="construction metareval evidence refs do not exist",
        )
        add_check(
            "metareval_evidence_refs_validate",
            refs_ok and not missing_ref_paths and not invalid_ref_reports,
            invalid_ref_reports=invalid_ref_reports,
            issue="construction metareval evidence refs are invalid or not bound to task",
        )

    if isinstance(task, dict):
        expected_sha = stable_task_digest(task)
        add_check(
            "metareval_task_id_matches_task",
            report.get("task_id") == task.get("task_id"),
            observed=report.get("task_id"),
            expected=task.get("task_id"),
            issue="construction metareval report task_id does not match task",
        )
        add_check(
            "metareval_repo_id_matches_task",
            report.get("repo_id") == task.get("repo_id"),
            observed=report.get("repo_id"),
            expected=task.get("repo_id"),
            issue="construction metareval report repo_id does not match task",
        )
        add_check(
            "metareval_task_sha256_matches_task",
            report.get("task_sha256") == expected_sha,
            observed=report.get("task_sha256"),
            expected=expected_sha,
            issue="construction metareval report task_sha256 does not match task",
        )

    return {
        "name": "construction_metareval",
        "passed": not issues,
        "issues": issues,
        "checks": checks,
        "details": {
            "require_existing_artifacts": require_existing_artifacts,
            "artifact_root": str(artifact_root) if artifact_root else None,
        },
    }


def validate_construction_metareval_gate(
    gate: dict[str, Any] | None,
    *,
    task: dict[str, Any] | None = None,
    artifact_root: Path | None = None,
) -> dict[str, Any]:
    if not isinstance(gate, dict):
        return {
            "name": "construction_metareval_gate",
            "passed": False,
            "issues": ["missing metareval gate"],
            "checks": [{"name": "metareval_gate_present", "passed": False}],
        }
    gate_passed = boolish(gate.get("passed")) and str(gate.get("status", "passed")) == "passed"
    report_paths = gate.get("report_paths", [])
    report_paths_ok = isinstance(report_paths, list) and bool(report_paths)
    missing_report_paths: dict[str, list[str]] = {}
    resolved_report_paths: list[str] = []
    if report_paths_ok:
        for raw_path in report_paths:
            if not isinstance(raw_path, str) or not raw_path.strip():
                missing_report_paths[str(raw_path)] = []
                continue
            candidates = candidate_artifact_paths(raw_path.strip(), artifact_root)
            existing = next((candidate for candidate in candidates if candidate.exists()), None)
            if existing is None:
                missing_report_paths[raw_path] = [str(candidate) for candidate in candidates]
            else:
                resolved_report_paths.append(str(existing))
    report_artifact_root = Path(resolved_report_paths[0]).parent if resolved_report_paths else artifact_root
    report = gate.get("summary")
    report_file_matches_summary = False
    report_file_issue = None
    if resolved_report_paths and isinstance(report, dict):
        try:
            report_file = load_json(Path(resolved_report_paths[0]))
            report_file_matches_summary = report_file == report
            if not report_file_matches_summary:
                report_file_issue = "metareval gate summary does not match report_paths[0] content"
        except Exception as exc:  # pragma: no cover - defensive for corrupt external artifacts
            report_file_issue = f"could not read metareval gate report file: {exc}"
    validation = validate_construction_metareval_report(
        report if isinstance(report, dict) else None,
        task=task,
        artifact_root=report_artifact_root,
        require_existing_artifacts=True,
    )
    issues = list(validation.get("issues", []))
    checks = [
        {"name": "metareval_gate_passed", "passed": gate_passed, "issue": "metareval gate is not marked passed"},
        {"name": "metareval_gate_report_paths_present", "passed": report_paths_ok, "issue": "metareval gate has no report_paths"},
        {
            "name": "metareval_gate_report_paths_exist",
            "passed": report_paths_ok and not missing_report_paths,
            "artifact_root": str(artifact_root) if artifact_root else None,
            "resolved_report_paths": resolved_report_paths,
            "missing_report_paths": missing_report_paths,
            "issue": "metareval gate report_paths do not exist",
        },
        {
            "name": "metareval_gate_summary_matches_report_file",
            "passed": bool(report_file_matches_summary),
            "issue": report_file_issue or "metareval gate summary does not match report_paths[0] content",
        },
        *validation.get("checks", []),
    ]
    if not gate_passed:
        issues.append("metareval gate is not marked passed")
    if not report_paths_ok:
        issues.append("metareval gate has no report_paths")
    elif missing_report_paths:
        issues.append("metareval gate report_paths do not exist")
    if report_file_issue:
        issues.append(report_file_issue)
    return {
        "name": "construction_metareval_gate",
        "passed": gate_passed and report_paths_ok and not missing_report_paths and report_file_matches_summary and validation.get("passed"),
        "issues": issues,
        "checks": checks,
        "report_validation": validation,
    }


def attach_construction_metareval(task: dict[str, Any], report: dict[str, Any], *, report_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    validation = validate_construction_metareval_report(
        report,
        task=task,
        artifact_root=report_path.parent,
        require_existing_artifacts=True,
    )
    if not validation.get("passed"):
        return task, validation

    updated = dict(task)
    gates = dict(updated.get("gates", {}))
    gates["metareval"] = {
        "gate": "metareval",
        "passed": True,
        "status": "passed",
        "report_paths": [str(report_path)],
        "summary": report,
    }
    updated["gates"] = gates
    return updated, validation


def add_attach_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--in-place", action="store_true", help="Overwrite --task after the report validates.")
    parser.add_argument("--validation-output", type=Path, default=None)


def add_build_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task", type=Path, default=None, help="Optional construction task used to copy task_id/repo_id into the report.")
    parser.add_argument("--reference", type=Path, required=True, help="Reference trajectory report that must pass.")
    parser.add_argument("--original", type=Path, required=True, help="Original/empty baseline report that must fail.")
    parser.add_argument("--bad-solution", type=Path, required=True, help="Known bad construction trajectory report that must fail.")
    parser.add_argument("--repeat", type=Path, required=True, help="Repeat reference report used to prove reproducibility.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validation-output", type=Path, default=None)


def run_build_from_args(args: argparse.Namespace) -> None:
    try:
        assert_not_under_formal_task_root(args.output, purpose="Construction meta-eval report")
        if args.validation_output is not None:
            assert_not_under_formal_task_root(
                args.validation_output,
                purpose="Construction meta-eval validation report",
            )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    ensure_dir(args.output.parent)
    task = load_json(args.task) if args.task else None
    report = build_construction_metareval_report(
        reference_report=args.reference,
        original_report=args.original,
        bad_solution_report=args.bad_solution,
        repeat_report=args.repeat,
        output_path=args.output,
        task=task,
    )
    write_json(args.output, report)
    validation = validate_construction_metareval_report(
        report,
        task=task,
        artifact_root=args.output.parent,
        require_existing_artifacts=True,
    )
    if args.validation_output is not None:
        ensure_dir(args.validation_output.parent)
        write_json(args.validation_output, validation)
    print(
        "construction meta-eval "
        f"passed={report['passed']} reference={report['reference_passed']} "
        f"original_failed={report['original_failed']} bad_failed={report['bad_solution_failed']} "
        f"reproducible={report['reproducible']} issues={len(report['issues'])} output={args.output}"
    )


def run_attach_from_args(args: argparse.Namespace) -> None:
    if args.validation_output is not None:
        try:
            assert_not_under_formal_task_root(
                args.validation_output,
                purpose="Construction meta-eval attach validation report",
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    task = load_json(args.task)
    report = load_json(args.report)
    updated, validation = attach_construction_metareval(task, report, report_path=args.report)

    if args.validation_output is not None:
        ensure_dir(args.validation_output.parent)
        write_json(args.validation_output, validation)

    if not validation["passed"]:
        raise SystemExit(
            "construction metareval report did not validate; task was not updated: "
            + "; ".join(validation.get("issues", []))
        )

    if args.in_place:
        output = args.task
    elif args.output is not None:
        output = args.output
    else:
        output = args.task.with_name(f"{args.task.stem}.with_metareval.json")
    try:
        assert_not_under_formal_task_root(output, purpose="Construction task with attached meta-eval")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    ensure_dir(output.parent)
    write_json(output, updated)
    print(f"attached validated construction metareval to {output}")
