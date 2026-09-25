from __future__ import annotations

from typing import Any

from ...evaluation.scoring.metrics import score_result
from ...taxonomy.capability import CHANGE_REGIME
from ..base import ContinuityRegime, RegimeContext


class ChangeRegime(ContinuityRegime):
    key = CHANGE_REGIME
    label = "Continuity under Change"
    primary_metrics = ("WCS",)

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
                "coverage_gap_slot",
                "repo_selection_reason",
                "runability_report",
                "desktop_tablet_mobile_capture",
                "design_anchor_summary",
                "repo_grounded_rationale",
                "provenance_index",
                "asset_gallery",
                "reference_and_submission_verifier_specs",
                "reference_pass_report",
                "baseline_or_original_failure_report",
                "bad_solution_negative_control_report",
                "package_validation",
                "quality_consistency_source_audits",
            ],
            "forbidden_outputs": [
                "batch_generated_task_jsonl",
                "candidate_review_card_as_design_brief",
                "accepted_task_without_repo_inspection",
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
                    "route_reachability",
                    "required_dom_text_or_accessible_name",
                    "state_artifacts",
                    "interaction_state_when_applicable",
                    "no_horizontal_overflow",
                    "regression_text_or_route_signals",
                ],
                "L-metric": [
                    "visual_anchor_similarity_or_token_drift",
                    "content_slot_count_or_density",
                    "asset_path_or_alt_signal_coverage",
                ],
                "L-soft": [
                    "mm_checkpoint_for_design_continuity_when_available",
                ],
            },
            "sanity_required": [
                "reference_states_pass",
                "unmodified_or_original_repo_fails_completion",
                "known_bad_solutions_fail_expected_checks",
            ],
            "primary_success_metric": "WCS",
            "primary_success_uses_mllm": False,
        }

    def score(self, report: dict[str, Any]) -> dict[str, Any]:
        return score_result(report)

    def package(self, context: RegimeContext) -> dict[str, Any]:
        return {
            "regime": self.key,
            "task_id": context.task_id,
            "repo_id": context.repo_id,
            "primary_metrics": list(self.primary_metrics),
        }
