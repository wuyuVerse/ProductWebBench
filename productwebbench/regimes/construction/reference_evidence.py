from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...core.formal_data_guard import assert_not_under_formal_task_root
from ...core.io_utils import ensure_dir, write_json
from .reference_actor import (
    SPEC_ONLY_SOURCE_KIND,
    TRACE_SCHEMA_VERSION,
    attach_reference_actor_trace,
    trace_milestones,
)
from .reference_trajectory import (
    TRAJECTORY_ARTIFACT_TYPE,
    validate_reference_trajectory_report,
)
from .scoring import score_trajectory
from .task_digest import stable_task_digest


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def existing_report_by_milestone(task: dict[str, Any]) -> dict[str, dict[str, Any]]:
    report = task.get("reference_trajectory_report", {})
    milestones = report.get("milestones", []) if isinstance(report, dict) else []
    return {
        str(item.get("milestone_id")): item
        for item in milestones
        if isinstance(item, dict) and item.get("milestone_id")
    }


def build_reference_trajectory_report_from_trace(task: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    prior_by_id = existing_report_by_milestone(task)
    milestones: list[dict[str, Any]] = []
    for item in trace_milestones(trace):
        mid = str(item.get("milestone_id", ""))
        prior = prior_by_id.get(mid, {})
        milestone: dict[str, Any] = {
            "milestone_id": mid,
            "ladder_step": prior.get("ladder_step", mid),
            "hard_passed": bool(item.get("hard_passed")),
            "metric_passed": bool(item.get("metric_passed", True)),
            "regression_events": int(item.get("regression_events", 0) or 0),
            "artifact_refs": item.get("artifact_refs", {}),
        }
        if prior.get("metric_score") is not None:
            milestone["metric_score"] = prior.get("metric_score")
        elif milestone["metric_passed"]:
            milestone["metric_score"] = 1.0
        if prior.get("soft_score") is not None:
            milestone["soft_score"] = prior.get("soft_score")
        if prior.get("failure_type") is not None:
            milestone["failure_type"] = prior.get("failure_type")
        milestones.append(milestone)

    report = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "artifact_type": TRAJECTORY_ARTIFACT_TYPE,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "formal_task_record": False,
        "task_id": task.get("task_id"),
        "repo_id": task.get("repo_id"),
        "task_sha256": stable_task_digest(task),
        "source_kind": SPEC_ONLY_SOURCE_KIND,
        "source_trace": {
            "artifact_type": trace.get("artifact_type"),
            "source_kind": trace.get("source_kind"),
            "milestone_count": len(milestones),
        },
        "milestones": milestones,
    }
    report["score"] = score_trajectory(report)
    report["passed"] = bool(report["score"].get("TCS", {}).get("passed"))
    return report


def update_reference_gates(task: dict[str, Any], trace_validation: dict[str, Any], trajectory_validation: dict[str, Any]) -> dict[str, Any]:
    updated = dict(task)
    gates = dict(updated.get("gates", {}))
    gates["spec_completeness_precheck"] = {
        "gate": "spec_completeness_precheck",
        "passed": True,
        "status": "passed",
        "report_paths": [],
        "summary": {
            "reference_actor_trace": "passed",
            "source_kind": SPEC_ONLY_SOURCE_KIND,
            "milestone_count": trace_validation.get("details", {}).get("milestone_count"),
        },
    }
    gates["trajectory_regression_sanity"] = {
        "gate": "trajectory_regression_sanity",
        "passed": True,
        "status": "passed",
        "report_paths": [],
        "summary": {
            "reference_trajectory_report": "passed",
            "source_kind": SPEC_ONLY_SOURCE_KIND,
            "score": trajectory_validation.get("score", {}),
        },
    }
    gates["regression_sanity"] = gates["trajectory_regression_sanity"]
    updated["gates"] = gates
    return updated


def finalize_reference_evidence(
    task: dict[str, Any],
    trace: dict[str, Any],
    *,
    artifact_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    with_trace, trace_validation = attach_reference_actor_trace(
        task,
        trace,
        artifact_root=artifact_root,
        require_existing_artifacts=True,
    )
    if not trace_validation.get("passed"):
        return task, {
            "name": "construction_reference_evidence",
            "passed": False,
            "issues": trace_validation.get("issues", []),
            "trace_validation": trace_validation,
        }

    trajectory_report = build_reference_trajectory_report_from_trace(with_trace, trace)
    with_report = dict(with_trace)
    with_report["reference_trajectory_report"] = trajectory_report
    trajectory_validation = validate_reference_trajectory_report(
        with_report,
        trajectory_report,
        trace=trace,
        require_completed_artifact=True,
    )
    if not trajectory_validation.get("passed"):
        return task, {
            "name": "construction_reference_evidence",
            "passed": False,
            "issues": trajectory_validation.get("issues", []),
            "trace_validation": trace_validation,
            "trajectory_validation": trajectory_validation,
        }

    finalized = update_reference_gates(with_report, trace_validation, trajectory_validation)
    return finalized, {
        "name": "construction_reference_evidence",
        "passed": True,
        "issues": [],
        "trace_validation": trace_validation,
        "trajectory_validation": trajectory_validation,
    }


def add_finalize_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--in-place", action="store_true", help="Overwrite --task after all evidence validates.")
    parser.add_argument("--validation-output", type=Path, default=None)


def run_finalize_from_args(args: argparse.Namespace) -> None:
    if args.validation_output is not None:
        try:
            assert_not_under_formal_task_root(
                args.validation_output,
                purpose="Construction reference evidence validation report",
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    task = load_json(args.task)
    trace = load_json(args.trace)
    updated, validation = finalize_reference_evidence(
        task,
        trace,
        artifact_root=args.trace.parent,
    )

    if args.validation_output is not None:
        ensure_dir(args.validation_output.parent)
        write_json(args.validation_output, validation)

    if not validation["passed"]:
        raise SystemExit(
            "construction reference evidence did not validate; task was not updated: "
            + "; ".join(validation.get("issues", []))
        )

    if args.in_place:
        output = args.task
    elif args.output is not None:
        output = args.output
    else:
        output = args.task.with_name(f"{args.task.stem}.with_reference_evidence.json")
    try:
        assert_not_under_formal_task_root(output, purpose="Construction task with finalized reference evidence")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    ensure_dir(output.parent)
    write_json(output, updated)
    print(f"attached validated construction reference evidence to {output}")
