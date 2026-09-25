from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...core.config import DEFAULT_OUTPUT_ROOT
from ...core.formal_data_guard import assert_not_under_formal_task_root
from ...core.io_utils import ensure_dir, write_json
from ...core.process_utils import run_logged_command
from .evidence_root import audit_construction_evidence_root
from .reference_actor import TRACE_SCHEMA_VERSION, milestone_ids
from .trajectory_run import (
    SOURCE_KINDS,
    TRAJECTORY_RUN_ARTIFACT_TYPE,
    build_milestone_run_report,
    build_trajectory_run_report,
    validate_trajectory_run_report,
)


ACTOR_LOOP_ARTIFACT_TYPE = "construction_actor_loop_report"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def milestone_dir(evidence_root: Path, milestone_id: str) -> Path:
    return evidence_root / "evidence" / milestone_id


def command_env(
    *,
    task_path: Path,
    evidence_root: Path,
    milestone_id: str,
    source_kind: str,
) -> dict[str, str]:
    root = milestone_dir(evidence_root, milestone_id)
    env = os.environ.copy()
    env.update(
        {
            "PRODUCTWEBBENCH_TASK": str(task_path.resolve()),
            "PRODUCTWEBBENCH_EVIDENCE_ROOT": str(evidence_root.resolve()),
            "PRODUCTWEBBENCH_MILESTONE_ID": milestone_id,
            "PRODUCTWEBBENCH_SOURCE_KIND": source_kind,
            "PRODUCTWEBBENCH_MILESTONE_DIR": str(root.resolve()),
            "PRODUCTWEBBENCH_ACTOR_INPUT": str((root / "actor_input.json").resolve()),
            "PRODUCTWEBBENCH_WORKSPACE": str((root / "workspace").resolve()),
            "PRODUCTWEBBENCH_CAPTURE_REPORT": str((root / "capture_report.json").resolve()),
            "PRODUCTWEBBENCH_VERIFIER_REPORT": str((root / "verifier_report.json").resolve()),
            "PRODUCTWEBBENCH_TRAJECTORY_REPORT": str((root / "trajectory_report.json").resolve()),
        }
    )
    return env


def command_cwd(evidence_root: Path, milestone_id: str, mode: str) -> Path:
    root = milestone_dir(evidence_root, milestone_id)
    if mode == "workspace":
        return root / "workspace"
    if mode == "milestone":
        return root
    if mode == "evidence_root":
        return evidence_root
    raise ValueError(f"invalid command cwd mode: {mode}")


def run_optional_command(
    command: str | None,
    *,
    cwd: Path,
    log_dir: Path,
    name: str,
    timeout_sec: int,
    env: dict[str, str],
) -> dict[str, Any] | None:
    if not command:
        return None
    result = run_logged_command(command, cwd, log_dir, name, timeout_sec=timeout_sec, env=env)
    return asdict(result)


def command_failed(result: dict[str, Any] | None) -> bool:
    if result is None:
        return False
    return result.get("timed_out") or result.get("exit_code") not in {0, None}


def run_construction_actor_loop(
    *,
    task_path: Path,
    evidence_root: Path,
    source_kind: str,
    actor_command: str | None,
    capture_command: str | None,
    verifier_command: str | None,
    output_path: Path,
    command_cwd_mode: str = "workspace",
    actor_timeout: int = 1800,
    capture_timeout: int = 600,
    verifier_timeout: int = 600,
    stop_on_failure: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if source_kind not in SOURCE_KINDS:
        raise ValueError(f"invalid construction actor loop source_kind: {source_kind}")
    assert_not_under_formal_task_root(evidence_root, purpose="Construction actor loop evidence root")
    assert_not_under_formal_task_root(output_path, purpose="Construction actor loop report")
    task = load_json(task_path)
    scaffold_audit = audit_construction_evidence_root(task, evidence_root=evidence_root, require_complete=False)
    issues: list[str] = []
    checks: list[dict[str, Any]] = [
        {
            "name": "evidence_scaffold_audit",
            "passed": scaffold_audit.get("passed"),
            "details": scaffold_audit,
        }
    ]
    if not scaffold_audit.get("passed"):
        issues.extend(scaffold_audit.get("issues", []))

    milestone_reports: list[dict[str, Any]] = []
    output_dir = output_path.parent
    for mid in milestone_ids(task):
        root = milestone_dir(evidence_root, mid)
        ensure_dir(root / "workspace")
        log_dir = ensure_dir(root / "logs")
        env = command_env(
            task_path=task_path,
            evidence_root=evidence_root,
            milestone_id=mid,
            source_kind=source_kind,
        )
        cwd = command_cwd(evidence_root, mid, command_cwd_mode)

        actor_result = run_optional_command(
            actor_command,
            cwd=cwd,
            log_dir=log_dir,
            name="actor",
            timeout_sec=actor_timeout,
            env=env,
        )
        capture_result = None
        verifier_result = None
        if command_failed(actor_result):
            issues.append(f"{mid} actor command failed")
        else:
            capture_result = run_optional_command(
                capture_command,
                cwd=cwd,
                log_dir=log_dir,
                name="capture",
                timeout_sec=capture_timeout,
                env=env,
            )
            if command_failed(capture_result):
                issues.append(f"{mid} capture command failed")
            else:
                verifier_result = run_optional_command(
                    verifier_command,
                    cwd=cwd,
                    log_dir=log_dir,
                    name="verifier",
                    timeout_sec=verifier_timeout,
                    env=env,
                )
                if command_failed(verifier_result):
                    issues.append(f"{mid} verifier command failed")

        milestone_summary: dict[str, Any] = {
            "milestone_id": mid,
            "actor_command": actor_result,
            "capture_command": capture_result,
            "verifier_command": verifier_result,
            "trajectory_report_path": str(root / "trajectory_report.json"),
        }

        capture_report = root / "capture_report.json"
        verifier_report = root / "verifier_report.json"
        try:
            if capture_report.exists() and verifier_report.exists():
                milestone_report = build_milestone_run_report(
                    task,
                    milestone_id=mid,
                    workspace=root / "workspace",
                    capture_report_path=capture_report,
                    verifier_report_path=verifier_report,
                    output_path=root / "trajectory_report.json",
                    source_kind=source_kind,
                )
                write_json(root / "trajectory_report.json", milestone_report)
                milestone_summary["trajectory_report"] = {
                    "passed": milestone_report.get("passed"),
                    "hard_passed": milestone_report.get("hard_passed"),
                    "metric_passed": milestone_report.get("metric_passed"),
                    "regression_events": milestone_report.get("regression_events"),
                    "failure_type": milestone_report.get("failure_type"),
                }
                milestone_reports.append(milestone_report)
                if not milestone_report.get("passed"):
                    issues.append(f"{mid} milestone trajectory_report did not pass")
            else:
                missing = [
                    str(path)
                    for path in (capture_report, verifier_report)
                    if not path.exists()
                ]
                milestone_summary["missing_reports"] = missing
                issues.append(f"{mid} missing capture/verifier reports")
        except Exception as exc:  # pragma: no cover - external command artifact failures
            milestone_summary["trajectory_report_error"] = str(exc)
            issues.append(f"{mid} could not build milestone run report: {exc}")

        checks.append(
            {
                "name": "milestone_actor_loop",
                "milestone_id": mid,
                "passed": not any(
                    str(issue).startswith(f"{mid} ")
                    for issue in issues
                ),
                "details": milestone_summary,
            }
        )
        if stop_on_failure and any(str(issue).startswith(f"{mid} ") for issue in issues):
            break

    trajectory_report = None
    trajectory_validation = None
    trajectory_path = evidence_root / "trajectory_run.json"
    if len(milestone_reports) == len(milestone_ids(task)):
        try:
            trajectory_report, trajectory_builder_validation = build_trajectory_run_report(
                task,
                evidence_root=evidence_root,
                output_path=trajectory_path,
                source_kind=source_kind,
            )
            write_json(trajectory_path, trajectory_report)
            trajectory_validation = validate_trajectory_run_report(
                task,
                trajectory_report,
                artifact_root=trajectory_path.parent,
                require_existing_artifacts=True,
            )
            checks.append(
                {
                    "name": "trajectory_run_report",
                    "passed": trajectory_validation.get("passed") and trajectory_report.get("passed"),
                    "details": {
                        "path": str(trajectory_path),
                        "builder_validation": trajectory_builder_validation,
                        "validation": trajectory_validation,
                        "TCS": trajectory_report.get("TCS"),
                        "TD": trajectory_report.get("TD"),
                        "ITR": trajectory_report.get("ITR"),
                    },
                }
            )
            if not trajectory_report.get("passed"):
                issues.append("trajectory_run_report did not pass TCS")
            if not trajectory_validation.get("passed"):
                issues.extend(trajectory_validation.get("issues", []))
        except Exception as exc:  # pragma: no cover - defensive for external artifacts
            issues.append(f"could not build trajectory run report: {exc}")
            checks.append({"name": "trajectory_run_report", "passed": False, "issue": str(exc)})

    report = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "artifact_type": ACTOR_LOOP_ARTIFACT_TYPE,
        "formal_task_record": False,
        "generated_at": utc_now(),
        "task_id": task.get("task_id"),
        "repo_id": task.get("repo_id"),
        "source_kind": source_kind,
        "evidence_root": str(evidence_root),
        "trajectory_report_path": str(trajectory_path) if trajectory_report else None,
        "passed": not issues and bool(trajectory_report and trajectory_report.get("passed")),
        "issue_count": len(issues),
        "issues": issues,
        "checks": checks,
    }
    validation = validate_actor_loop_report(report)
    write_json(output_path, report)
    return report, validation


def validate_actor_loop_report(report: dict[str, Any] | None) -> dict[str, Any]:
    issues: list[str] = []
    checks: list[dict[str, Any]] = []
    if not isinstance(report, dict):
        return {
            "name": "construction_actor_loop_report",
            "passed": False,
            "issues": ["missing construction actor loop report"],
            "checks": [{"name": "actor_loop_report_present", "passed": False}],
        }

    def add_check(name: str, passed: bool, **details: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), **details})
        if not passed:
            issues.append(details.get("issue") or name)

    add_check(
        "actor_loop_schema_version",
        report.get("schema_version") == TRACE_SCHEMA_VERSION,
        observed=report.get("schema_version"),
        expected=TRACE_SCHEMA_VERSION,
        issue="construction actor loop report has wrong schema_version",
    )
    add_check(
        "actor_loop_artifact_type",
        report.get("artifact_type") == ACTOR_LOOP_ARTIFACT_TYPE,
        observed=report.get("artifact_type"),
        expected=ACTOR_LOOP_ARTIFACT_TYPE,
        issue="construction actor loop report has wrong artifact_type",
    )
    add_check(
        "actor_loop_not_formal",
        report.get("formal_task_record") is False,
        observed=report.get("formal_task_record"),
        issue="construction actor loop report must be non-formal",
    )
    add_check(
        "actor_loop_source_kind",
        report.get("source_kind") in SOURCE_KINDS,
        observed=report.get("source_kind"),
        expected=list(SOURCE_KINDS),
        issue="construction actor loop report has invalid source_kind",
    )
    add_check(
        "actor_loop_pass_consistent",
        bool(report.get("passed")) == (int(report.get("issue_count", 0) or 0) == 0 and bool(report.get("trajectory_report_path"))),
        report_passed=report.get("passed"),
        issue_count=report.get("issue_count"),
        trajectory_report_path=report.get("trajectory_report_path"),
        issue="construction actor loop passed flag is inconsistent",
    )
    return {
        "name": "construction_actor_loop_report",
        "passed": not issues,
        "issues": issues,
        "checks": checks,
    }


def add_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--source-kind", choices=SOURCE_KINDS, required=True)
    parser.add_argument("--actor-command", default=None, help="External actor command. Receives PRODUCTWEBBENCH_* env vars.")
    parser.add_argument("--capture-command", default=None, help="External capture command. Must write $PRODUCTWEBBENCH_CAPTURE_REPORT.")
    parser.add_argument("--verifier-command", default=None, help="External verifier command. Must write $PRODUCTWEBBENCH_VERIFIER_REPORT.")
    parser.add_argument("--command-cwd", choices=["workspace", "milestone", "evidence_root"], default="workspace")
    parser.add_argument("--actor-timeout", type=int, default=1800)
    parser.add_argument("--capture-timeout", type=int, default=600)
    parser.add_argument("--verifier-timeout", type=int, default=600)
    parser.add_argument("--no-stop-on-failure", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validation-output", type=Path, default=None)


def add_validate_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)


def run_from_args(args: argparse.Namespace) -> None:
    try:
        assert_not_under_formal_task_root(args.output, purpose="Construction actor loop report")
        if args.validation_output is not None:
            assert_not_under_formal_task_root(
                args.validation_output,
                purpose="Construction actor loop validation report",
            )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    try:
        report, validation = run_construction_actor_loop(
            task_path=args.task,
            evidence_root=args.evidence_root,
            source_kind=args.source_kind,
            actor_command=args.actor_command,
            capture_command=args.capture_command,
            verifier_command=args.verifier_command,
            output_path=args.output,
            command_cwd_mode=args.command_cwd,
            actor_timeout=args.actor_timeout,
            capture_timeout=args.capture_timeout,
            verifier_timeout=args.verifier_timeout,
            stop_on_failure=not args.no_stop_on_failure,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.validation_output is not None:
        ensure_dir(args.validation_output.parent)
        write_json(args.validation_output, validation)
    print(
        "construction actor loop "
        f"passed={report['passed']} issues={report['issue_count']} output={args.output}"
    )
    if not report.get("passed"):
        raise SystemExit("; ".join(report.get("issues", [])))


def run_validate_from_args(args: argparse.Namespace) -> None:
    if args.output is not None:
        try:
            assert_not_under_formal_task_root(args.output, purpose="Construction actor loop validation report")
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    report = load_json(args.report)
    validation = validate_actor_loop_report(report)
    if args.output is not None:
        ensure_dir(args.output.parent)
        write_json(args.output, validation)
    print(
        "construction actor loop validation "
        f"passed={validation['passed']} issues={len(validation.get('issues', []))}"
    )
    if not validation.get("passed"):
        raise SystemExit("; ".join(validation.get("issues", [])))
