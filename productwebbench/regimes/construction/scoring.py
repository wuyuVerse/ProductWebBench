from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any

from ...core.config import DEFAULT_OUTPUT_ROOT
from ...core.formal_data_guard import assert_not_under_formal_task_root
from ...core.io_utils import ensure_dir, write_json
from ...taxonomy.capability import CONSTRUCTION_REGIME
from ..base import ContinuityRegime, RegimeContext


SCORE_ARTIFACT_TYPE = "construction_trajectory_score_snapshot"


@dataclass(frozen=True)
class MilestoneResult:
    milestone_id: str
    ladder_step: str
    hard_passed: bool
    metric_passed: bool
    regression_events: int = 0
    metric_score: float | None = None
    soft_score: float | None = None
    failure_type: str | None = None

    @property
    def passed(self) -> bool:
        return self.hard_passed and self.metric_passed and self.regression_events == 0


@dataclass(frozen=True)
class TrajectoryScore:
    schema_version: str
    artifact_type: str
    formal_task_record: bool
    publishable: bool
    source_kind: str
    task_id: str | None
    repo_id: str | None
    TCS: dict[str, Any]
    TD: dict[str, Any]
    ITR: dict[str, Any]
    QS: dict[str, Any]
    milestones: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def parse_milestones(report: dict[str, Any]) -> list[MilestoneResult]:
    out: list[MilestoneResult] = []
    for item in report.get("milestones", []):
        out.append(
            MilestoneResult(
                milestone_id=item["milestone_id"],
                ladder_step=item.get("ladder_step", item["milestone_id"]),
                hard_passed=bool(item.get("hard_passed")),
                metric_passed=bool(item.get("metric_passed", True)),
                regression_events=int(item.get("regression_events", 0)),
                metric_score=item.get("metric_score"),
                soft_score=item.get("soft_score"),
                failure_type=item.get("failure_type"),
            )
        )
    return out


def score_trajectory(report: dict[str, Any]) -> dict[str, Any]:
    milestones = parse_milestones(report)
    passed_prefix = 0
    for milestone in milestones:
        if milestone.passed:
            passed_prefix += 1
            continue
        break
    total = len(milestones)
    total_regressions = sum(item.regression_events for item in milestones)
    tcs_passed = bool(total and passed_prefix == total and total_regressions == 0)
    metric_values = [item.metric_score for item in milestones[:passed_prefix] if item.metric_score is not None]
    soft_values = [item.soft_score for item in milestones[:passed_prefix] if item.soft_score is not None]
    quality_values = metric_values + soft_values
    itr_denominator = max(1, sum(1 for item in milestones if item.hard_passed and item.metric_passed))
    itr_score = max(0.0, 1.0 - min(total_regressions, itr_denominator) / itr_denominator)
    score = TrajectoryScore(
        schema_version="2026-06-19",
        artifact_type=SCORE_ARTIFACT_TYPE,
        formal_task_record=False,
        publishable=False,
        source_kind="score_snapshot_only",
        task_id=report.get("task_id"),
        repo_id=report.get("repo_id"),
        TCS={"description": "Trajectory Continuity Success", "passed": tcs_passed, "score": 1.0 if tcs_passed else 0.0},
        TD={"description": "Trajectory Depth", "passed_milestones": passed_prefix, "total_milestones": total, "score": round(passed_prefix / total, 4) if total else None},
        ITR={"description": "Intra-Trajectory Regression Pass Rate", "regression_events": total_regressions, "score": round(itr_score, 4)},
        QS={"description": "Construction Quality Score", "score": round(mean(quality_values), 4) if quality_values else None, "contains_l_soft": bool(soft_values)},
        milestones=[asdict(item) | {"passed": item.passed} for item in milestones],
    )
    return score.to_json()


class ConstructionRegime(ContinuityRegime):
    key = CONSTRUCTION_REGIME
    label = "Continuity under Construction"
    primary_metrics = ("TCS", "TD", "ITR")

    def author_task(self, context: RegimeContext, target: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "regime": self.key,
            "task_id": context.task_id,
            "repo_id": context.repo_id,
            "formal_task_record": False,
            "generated_formal_task": False,
            "manual_authoring_required": True,
            "target": target or {},
            "one_task_workflow": ["P0_slot", "P1_repo", "P2_triage", "P3_capture", "P4_design", "P5_verifier", "P6_sanity", "P7_gates", "P8_freeze", "P9_split_audit"],
            "required_evidence": [
                "content_free_invariant_set",
                "six_layer_milestone_sequence",
                "asset_policy_with_intrinsic_decorative_classification",
                "non_formal_evidence_root",
                "per_milestone_actor_input_contracts",
                "per_milestone_capture_reports",
                "per_milestone_verifier_reports",
                "standardized_milestone_run_reports",
                "trajectory_run_report_with_TCS_TD_ITR",
                "spec_only_reference_actor_trace",
                "construction_metareval_report",
                "strict_precheck_pass_report",
            ],
            "forbidden_outputs": [
                "target_screenshot_in_actor_input",
                "source_code_in_actor_input",
                "decorative_original_assets_visible_to_actor",
                "accepted_ledger_row_before_reference_trace_and_metareval",
            ],
        }

    def build_verifier(self, context: RegimeContext, task: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "regime": self.key,
            "task_id": context.task_id,
            "repo_id": context.repo_id,
            "formal_task_record": False,
            "generated_verifier_spec": False,
            "manual_verifier_authoring_required": True,
            "required_signal_layers": {
                "L-hard": [
                    "milestone_route_reachability",
                    "milestone_dom_or_accessibility_checkpoint",
                    "interaction_checkpoint_when_applicable",
                    "no_horizontal_overflow",
                    "cumulative_prior_checkpoint_replay",
                ],
                "L-metric": [
                    "layout_geometry_threshold",
                    "token_drift_threshold",
                    "content_slot_count_or_info_density",
                    "asset_grounding_threshold",
                ],
                "L-soft": [
                    "calibrated_mm_checkpoint_only_for_quality_score",
                ],
            },
            "sanity_required": [
                "spec_only_reference_actor_passes_all_milestones",
                "empty_workspace_fails_each_milestone_completion",
                "known_bad_trajectory_fails_relevant_checkpoint",
                "repeat_run_reproduces_TCS_TD_ITR",
            ],
            "primary_success_metrics": list(self.primary_metrics),
            "primary_success_uses_mllm": False,
        }

    def score(self, report: dict[str, Any]) -> dict[str, Any]:
        return score_trajectory(report)

    def package(self, context: RegimeContext) -> dict[str, Any]:
        return {
            "regime": self.key,
            "task_id": context.task_id,
            "repo_id": context.repo_id,
            "primary_metrics": list(self.primary_metrics),
        }


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def add_score_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "construction_drafts" / "trajectory_score.json")


def run_score_from_args(args: argparse.Namespace) -> None:
    try:
        assert_not_under_formal_task_root(args.output, purpose="Construction trajectory score snapshot")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    ensure_dir(args.output.parent)
    report = load_json(args.report)
    if "reference_trajectory_report" in report:
        report = report["reference_trajectory_report"]
    score = score_trajectory(report)
    write_json(args.output, score)
    print(
        f"scored construction trajectory {score.get('task_id')}: "
        f"TCS={score['TCS']['score']} TD={score['TD']['score']} ITR={score['ITR']['score']}"
    )
