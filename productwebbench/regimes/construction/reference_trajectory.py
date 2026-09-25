from __future__ import annotations

from typing import Any

from .reference_actor import SPEC_ONLY_SOURCE_KIND, milestone_ids, trace_milestones
from .scoring import score_trajectory
from .task_digest import stable_task_digest


TRAJECTORY_ARTIFACT_TYPE = "reference_trajectory_report"


def report_milestones(report: dict[str, Any]) -> list[dict[str, Any]]:
    items = report.get("milestones", [])
    return [item for item in items if isinstance(item, dict)]


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def validate_reference_trajectory_report(
    task: dict[str, Any],
    report: dict[str, Any] | None,
    *,
    trace: dict[str, Any] | None = None,
    require_completed_artifact: bool = False,
) -> dict[str, Any]:
    issues: list[str] = []
    checks: list[dict[str, Any]] = []
    expected_ids = milestone_ids(task)

    if not isinstance(report, dict):
        return {
            "name": "reference_trajectory_report",
            "passed": False,
            "issues": ["missing reference_trajectory_report"],
            "checks": [{"name": "trajectory_report_present", "passed": False}],
        }

    def add_check(name: str, passed: bool, **details: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), **details})
        if not passed:
            issues.append(details.get("issue") or name)

    if require_completed_artifact:
        add_check(
            "trajectory_artifact_type",
            report.get("artifact_type") == TRAJECTORY_ARTIFACT_TYPE,
            observed=report.get("artifact_type"),
            expected=TRAJECTORY_ARTIFACT_TYPE,
            issue="reference_trajectory_report must be a completed trajectory artifact",
        )
        add_check(
            "trajectory_source_kind",
            report.get("source_kind") == SPEC_ONLY_SOURCE_KIND,
            observed=report.get("source_kind"),
            expected=SPEC_ONLY_SOURCE_KIND,
            issue="reference_trajectory_report must come from a spec-only actor run",
        )
        add_check(
            "trajectory_non_formal",
            report.get("formal_task_record") is False,
            observed=report.get("formal_task_record"),
            issue="reference_trajectory_report must be non-formal",
        )
        add_check(
            "trajectory_task_id_matches_task",
            report.get("task_id") == task.get("task_id"),
            observed=report.get("task_id"),
            expected=task.get("task_id"),
            issue="reference_trajectory_report task_id does not match task",
        )
        add_check(
            "trajectory_repo_id_matches_task",
            report.get("repo_id") == task.get("repo_id"),
            observed=report.get("repo_id"),
            expected=task.get("repo_id"),
            issue="reference_trajectory_report repo_id does not match task",
        )
        add_check(
            "trajectory_task_sha256_matches_task",
            report.get("task_sha256") == stable_task_digest(task),
            observed=report.get("task_sha256"),
            expected=stable_task_digest(task),
            issue="reference_trajectory_report task_sha256 does not match task",
        )

    observed_milestones = report_milestones(report)
    observed_ids = [str(item.get("milestone_id", "")) for item in observed_milestones]
    add_check(
        "trajectory_milestone_sequence_matches_task",
        observed_ids == expected_ids,
        observed=observed_ids,
        expected=expected_ids,
        issue="reference_trajectory_report milestone sequence does not match task",
    )

    score = score_trajectory(report)
    trajectory_passed = bool(score.get("TCS", {}).get("passed")) and score.get("TD", {}).get("score") == 1.0 and score.get("ITR", {}).get("score") == 1.0
    add_check(
        "trajectory_scores_pass",
        trajectory_passed,
        score={
            "TCS": score.get("TCS", {}).get("score"),
            "TD": score.get("TD", {}).get("score"),
            "ITR": score.get("ITR", {}).get("score"),
        },
        issue="reference trajectory does not pass TCS/TD/ITR sanity",
    )

    trace_items_by_id: dict[str, dict[str, Any]] = {}
    trace_available = isinstance(trace, dict)
    if trace_available:
        trace_items_by_id = {str(item.get("milestone_id", "")): item for item in trace_milestones(trace)}
    elif require_completed_artifact:
        add_check(
            "trajectory_reference_actor_trace_present",
            False,
            issue="missing reference_actor_trace for trajectory consistency check",
        )

    for index, milestone in enumerate(observed_milestones):
        mid = str(milestone.get("milestone_id", f"milestone_{index + 1}"))
        hard_passed = bool(milestone.get("hard_passed"))
        metric_passed = bool(milestone.get("metric_passed", True))
        regression_events = _as_int(milestone.get("regression_events", 0))
        add_check(
            "trajectory_milestone_hard_metric_regression",
            hard_passed and metric_passed and regression_events == 0,
            milestone_id=mid,
            hard_passed=hard_passed,
            metric_passed=metric_passed,
            regression_events=regression_events,
            issue=f"{mid} trajectory milestone does not pass hard/metric/regression checks",
        )

        if require_completed_artifact and trace_available:
            trace_item = trace_items_by_id.get(mid)
            trace_matches = (
                isinstance(trace_item, dict)
                and bool(trace_item.get("hard_passed")) == hard_passed
                and bool(trace_item.get("metric_passed", True)) == metric_passed
                and _as_int(trace_item.get("regression_events", 0)) == regression_events
            )
            add_check(
                "trajectory_matches_reference_actor_trace",
                trace_matches,
                milestone_id=mid,
                issue=f"{mid} trajectory report does not match reference_actor_trace",
            )

    return {
        "name": "reference_trajectory_report",
        "passed": not issues,
        "issues": issues,
        "checks": checks,
        "score": score,
        "details": {
            "source_kind": report.get("source_kind"),
            "milestone_count": len(observed_milestones),
            "expected_milestone_count": len(expected_ids),
            "require_completed_artifact": require_completed_artifact,
        },
    }
