from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...core.formal_data_guard import assert_not_under_formal_task_root
from ...core.io_utils import ensure_dir, write_json
from .reference_actor import TRACE_SCHEMA_VERSION
from .scoring import score_trajectory
from .task_digest import stable_task_digest


EVIDENCE_REPORT_ARTIFACT_TYPE = "construction_trajectory_evidence_report"
EVIDENCE_KINDS = ("reference", "original", "bad_solution", "repeat")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def source_ref(path: Path, output_dir: Path) -> str:
    try:
        return str(path.resolve().relative_to(output_dir.resolve()))
    except ValueError:
        return os.path.relpath(path.resolve(), output_dir.resolve())


def extract_trajectory_report(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("reference_trajectory_report"), dict):
        return payload["reference_trajectory_report"]
    if isinstance(payload.get("trajectory_report"), dict):
        return payload["trajectory_report"]
    if isinstance(payload.get("report"), dict) and isinstance(payload["report"].get("milestones"), list):
        return payload["report"]
    return payload


def build_construction_trajectory_evidence_report(
    *,
    trajectory_path: Path,
    output_path: Path,
    evidence_kind: str,
    task: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = load_json(trajectory_path)
    trajectory = extract_trajectory_report(payload)
    score = score_trajectory(trajectory)
    tcs_passed = bool(score.get("TCS", {}).get("passed"))
    task_sha256 = stable_task_digest(task) if isinstance(task, dict) else trajectory.get("task_sha256")
    report = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "artifact_type": EVIDENCE_REPORT_ARTIFACT_TYPE,
        "formal_task_record": False,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "evidence_kind": evidence_kind,
        "task_id": (task or {}).get("task_id") or trajectory.get("task_id"),
        "repo_id": (task or {}).get("repo_id") or trajectory.get("repo_id"),
        "task_sha256": task_sha256,
        "source_report_ref": source_ref(trajectory_path, output_path.parent),
        "passed": tcs_passed,
        "TCS": score.get("TCS"),
        "TD": score.get("TD"),
        "ITR": score.get("ITR"),
        "QS": score.get("QS"),
        "score": score,
        "milestone_count": len(trajectory.get("milestones", [])) if isinstance(trajectory.get("milestones"), list) else 0,
        "issues": [] if tcs_passed else ["trajectory did not pass TCS"],
    }
    return report


def validate_construction_trajectory_evidence_report(
    report: dict[str, Any] | None,
    *,
    task: dict[str, Any] | None = None,
) -> dict[str, Any]:
    issues: list[str] = []
    checks: list[dict[str, Any]] = []

    if not isinstance(report, dict):
        return {
            "name": "construction_trajectory_evidence_report",
            "passed": False,
            "issues": ["missing construction trajectory evidence report"],
            "checks": [{"name": "trajectory_evidence_report_present", "passed": False}],
        }

    def add_check(name: str, passed: bool, **details: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), **details})
        if not passed:
            issues.append(details.get("issue") or name)

    add_check(
        "trajectory_evidence_artifact_type",
        report.get("artifact_type") == EVIDENCE_REPORT_ARTIFACT_TYPE,
        observed=report.get("artifact_type"),
        expected=EVIDENCE_REPORT_ARTIFACT_TYPE,
        issue="construction trajectory evidence report has wrong artifact_type",
    )
    add_check(
        "trajectory_evidence_non_formal",
        report.get("formal_task_record") is False,
        observed=report.get("formal_task_record"),
        issue="construction trajectory evidence report must be non-formal",
    )
    add_check(
        "trajectory_evidence_kind",
        report.get("evidence_kind") in EVIDENCE_KINDS,
        observed=report.get("evidence_kind"),
        expected=list(EVIDENCE_KINDS),
        issue="construction trajectory evidence report has invalid evidence_kind",
    )
    tcs = report.get("TCS", {})
    td = report.get("TD", {})
    itr = report.get("ITR", {})
    add_check(
        "trajectory_evidence_metrics_present",
        isinstance(tcs, dict) and isinstance(td, dict) and isinstance(itr, dict),
        issue="construction trajectory evidence report is missing TCS/TD/ITR",
    )
    add_check(
        "trajectory_evidence_pass_consistent",
        bool(report.get("passed")) == bool(tcs.get("passed")) if isinstance(tcs, dict) else False,
        report_passed=report.get("passed"),
        tcs_passed=tcs.get("passed") if isinstance(tcs, dict) else None,
        issue="construction trajectory evidence report passed flag does not match TCS",
    )
    if isinstance(task, dict):
        expected_sha = stable_task_digest(task)
        add_check(
            "trajectory_evidence_task_id_matches_task",
            report.get("task_id") == task.get("task_id"),
            observed=report.get("task_id"),
            expected=task.get("task_id"),
            issue="construction trajectory evidence report task_id does not match task",
        )
        add_check(
            "trajectory_evidence_repo_id_matches_task",
            report.get("repo_id") == task.get("repo_id"),
            observed=report.get("repo_id"),
            expected=task.get("repo_id"),
            issue="construction trajectory evidence report repo_id does not match task",
        )
        add_check(
            "trajectory_evidence_task_sha256_matches_task",
            report.get("task_sha256") == expected_sha,
            observed=report.get("task_sha256"),
            expected=expected_sha,
            issue="construction trajectory evidence report task_sha256 does not match task",
        )
    return {
        "name": "construction_trajectory_evidence_report",
        "passed": not issues,
        "issues": issues,
        "checks": checks,
    }


def add_build_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--trajectory", type=Path, required=True, help="Trajectory JSON or task JSON containing reference_trajectory_report.")
    parser.add_argument("--kind", choices=EVIDENCE_KINDS, required=True)
    parser.add_argument("--task", type=Path, default=None, help="Optional task JSON used to copy task_id/repo_id.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validation-output", type=Path, default=None)


def run_build_from_args(args: argparse.Namespace) -> None:
    try:
        assert_not_under_formal_task_root(args.output, purpose="Construction trajectory evidence report")
        if args.validation_output is not None:
            assert_not_under_formal_task_root(
                args.validation_output,
                purpose="Construction trajectory evidence validation report",
            )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    ensure_dir(args.output.parent)
    task = load_json(args.task) if args.task else None
    report = build_construction_trajectory_evidence_report(
        trajectory_path=args.trajectory,
        output_path=args.output,
        evidence_kind=args.kind,
        task=task,
    )
    write_json(args.output, report)
    validation = validate_construction_trajectory_evidence_report(report, task=task)
    if args.validation_output is not None:
        ensure_dir(args.validation_output.parent)
        write_json(args.validation_output, validation)
    print(
        "construction trajectory evidence "
        f"kind={report['evidence_kind']} passed={report['passed']} "
        f"TCS={report['TCS']['score']} TD={report['TD']['score']} ITR={report['ITR']['score']} "
        f"output={args.output}"
    )
