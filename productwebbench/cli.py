from __future__ import annotations

import argparse

from .construction.authoring import (
    asset_gallery,
    consistency,
    coverage,
    design_anchors,
    dossiers,
    evidence,
    freeze,
    ledger,
    pipeline,
    provenance,
    quality,
    queue,
    rationales,
    review_board,
    task_packages,
    tasks,
)
from .construction.inventory import catalog, workspace
from .capture import runtime as capture_runtime
from .evaluation.adapters import harbor_adapter
from .evaluation.runners import batch_runner
from .evaluation.scoring import leaderboard, metrics
from .evaluation.verification import source_audit, verifier
from .evalkit import construction_repair, deai, denominators, metareval, mm_judge, mm_repair, model_isolation, negative_controls, protocol, readiness, readiness_repair, signals
from .pipeline import capability_split
from .pipeline import ledger as pipeline_ledger
from .pipeline import mm_calibration
from .pipeline import per_task
from .regimes import registry as regime_registry
from .regimes.construction import asset_policy as construction_asset_policy
from .regimes.construction import actor_loop as construction_actor_loop
from .regimes.construction import authoring as construction_authoring
from .regimes.construction import evidence_root as construction_evidence_root
from .regimes.construction import evidence_reports as construction_evidence_reports
from .regimes.construction import ledger as construction_ledger
from .regimes.construction import metareval as construction_metareval
from .regimes.construction import precheck as construction_precheck
from .regimes.construction import reference_actor as construction_reference_actor
from .regimes.construction import reference_evidence as construction_reference_evidence
from .regimes.construction import scoring as construction_scoring
from .regimes.construction import trajectory_run as construction_trajectory_run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pwb")
    subparsers = parser.add_subparsers(dest="command", required=True)

    catalog_parser = subparsers.add_parser("catalog", help="Build repo manifest from zipped repos.")
    catalog.add_catalog_args(catalog_parser)
    catalog_parser.set_defaults(func=catalog.run_from_args)

    select_parser = subparsers.add_parser(
        "select-candidates",
        help="Select high-value repos for SiteContinuum task authoring.",
    )
    tasks.add_select_args(select_parser)
    select_parser.set_defaults(func=tasks.run_select_from_args)

    validate_parser = subparsers.add_parser("validate-tasks", help="Validate task JSONL files.")
    tasks.add_validate_args(validate_parser)
    validate_parser.set_defaults(func=tasks.run_validate_from_args)

    authoring_data_parser = subparsers.add_parser(
        "audit-authoring-data",
        help="Audit candidate pool size/uniqueness and per-task duplicate file/state fields.",
    )
    tasks.add_authoring_data_audit_args(authoring_data_parser)
    authoring_data_parser.set_defaults(func=tasks.run_authoring_data_audit_from_args)

    duplicate_repo_repair_parser = subparsers.add_parser(
        "plan-duplicate-repo-repair",
        help="Write a non-formal one-group-at-a-time repair plan for duplicate formal repo IDs.",
    )
    tasks.add_duplicate_repo_repair_plan_args(duplicate_repo_repair_parser)
    duplicate_repo_repair_parser.set_defaults(func=tasks.run_duplicate_repo_repair_plan_from_args)

    strict_authoring_repair_parser = subparsers.add_parser(
        "plan-strict-authoring-repair",
        help="Write a non-formal one-slot-at-a-time repair plan for strict authoring blockers.",
    )
    tasks.add_strict_authoring_repair_plan_args(strict_authoring_repair_parser)
    strict_authoring_repair_parser.set_defaults(func=tasks.run_strict_authoring_repair_plan_from_args)

    strict_freeze_worklist_parser = subparsers.add_parser(
        "build-strict-freeze-worklist",
        help="Build a non-formal command worklist for one-slot-at-a-time strict freeze repairs.",
    )
    tasks.add_strict_freeze_worklist_args(strict_freeze_worklist_parser)
    strict_freeze_worklist_parser.set_defaults(func=tasks.run_strict_freeze_worklist_from_args)

    template_parser = subparsers.add_parser("task-template", help="Write a task authoring template.")
    tasks.add_template_args(template_parser)
    template_parser.set_defaults(func=tasks.run_template_from_args)

    extract_parser = subparsers.add_parser("extract-repo", help="Extract a repo zip into a workspace.")
    workspace.add_extract_args(extract_parser)
    extract_parser.set_defaults(func=workspace.run_extract_from_args)

    run_parser = subparsers.add_parser("runability", help="Install/build/start one repo and record logs.")
    capture_runtime.add_runability_args(run_parser)
    run_parser.set_defaults(func=capture_runtime.run_runability)

    capture_parser = subparsers.add_parser("capture-states", help="Capture browser states for one repo.")
    capture_runtime.add_capture_args(capture_parser)
    capture_parser.set_defaults(func=capture_runtime.run_capture)

    capture_status_parser = subparsers.add_parser(
        "capture-runtime-status",
        help="Report the shared capture/runtime facade used by both regimes.",
    )
    capture_runtime.add_status_args(capture_status_parser)
    capture_status_parser.set_defaults(func=capture_runtime.run_status_from_args)

    evidence_parser = subparsers.add_parser("evidence-index", help="Index captured states for a task file.")
    evidence.add_index_args(evidence_parser)
    evidence_parser.set_defaults(func=evidence.run_index_from_args)

    anchors_parser = subparsers.add_parser("design-anchors", help="Index visual design anchors from captured states.")
    design_anchors.add_design_anchor_args(anchors_parser)
    anchors_parser.set_defaults(func=design_anchors.run_design_anchor_from_args)

    dossier_parser = subparsers.add_parser("build-dossiers", help="Build repo dossiers before task authoring.")
    dossiers.add_dossier_args(dossier_parser)
    dossier_parser.set_defaults(func=dossiers.run_dossier_from_args)

    asset_gallery_parser = subparsers.add_parser("build-asset-galleries", help="Build repo asset galleries for task review.")
    asset_gallery.add_asset_gallery_args(asset_gallery_parser)
    asset_gallery_parser.set_defaults(func=asset_gallery.run_asset_gallery_from_args)

    rationale_parser = subparsers.add_parser(
        "validate-rationales",
        help="Validate agent audit rationales for repo-grounded task construction.",
    )
    rationales.add_validate_rationales_args(rationale_parser)
    rationale_parser.set_defaults(func=rationales.run_validate_rationales_from_args)

    package_parser = subparsers.add_parser("export-task-packages", help="Export one reviewable package per task.")
    task_packages.add_export_args(package_parser)
    package_parser.set_defaults(func=task_packages.run_export_from_args)

    package_validate_parser = subparsers.add_parser(
        "validate-task-packages",
        help="Validate exported per-task benchmark packages.",
    )
    task_packages.add_validate_package_args(package_validate_parser)
    package_validate_parser.set_defaults(func=task_packages.run_validate_package_from_args)

    review_board_parser = subparsers.add_parser(
        "build-review-board",
        help="Build a visual audit board from exported task packages.",
    )
    review_board.add_review_board_args(review_board_parser)
    review_board_parser.set_defaults(func=review_board.run_review_board_from_args)

    consistency_parser = subparsers.add_parser(
        "audit-consistency",
        help="Audit cross-file consistency for authored benchmark tasks.",
    )
    consistency.add_consistency_args(consistency_parser)
    consistency_parser.set_defaults(func=consistency.run_consistency_from_args)

    provenance_parser = subparsers.add_parser(
        "build-provenance",
        help="Build repo-grounded provenance evidence for authored tasks.",
    )
    provenance.add_provenance_args(provenance_parser)
    provenance_parser.set_defaults(func=provenance.run_provenance_from_args)

    quality_parser = subparsers.add_parser(
        "audit-quality",
        help="Audit task content quality and machine-check coverage.",
    )
    quality.add_quality_args(quality_parser)
    quality_parser.set_defaults(func=quality.run_quality_from_args)

    coverage_parser = subparsers.add_parser(
        "audit-coverage",
        help="Audit benchmark split diversity and coverage balance.",
    )
    coverage.add_coverage_args(coverage_parser)
    coverage_parser.set_defaults(func=coverage.run_coverage_from_args)

    freeze_audit_parser = subparsers.add_parser(
        "audit-single-task-freeze",
        help="Audit one slot before P8 freeze; does not generate formal task data.",
    )
    freeze.add_freeze_audit_args(freeze_audit_parser)
    freeze_audit_parser.set_defaults(func=freeze.run_freeze_audit_from_args)

    no_bulk_template_parser = subparsers.add_parser(
        "no-bulk-declaration-template",
        help="Write a non-passing per-task no-bulk declaration template for manual completion.",
    )
    freeze.add_declaration_template_args(no_bulk_template_parser)
    no_bulk_template_parser.set_defaults(func=freeze.run_declaration_template_from_args)

    pipeline_parser = subparsers.add_parser(
        "build-split",
        help="Run the full task packaging and audit pipeline for an authored split.",
    )
    pipeline.add_pipeline_args(pipeline_parser)
    pipeline_parser.set_defaults(func=pipeline.run_pipeline_from_args)

    queue_parser = subparsers.add_parser(
        "build-authoring-queue",
        help="Build a gap-driven queue of candidate repos for the next authored tasks.",
    )
    queue.add_queue_args(queue_parser)
    queue_parser.set_defaults(func=queue.run_queue_from_args)

    start_task_parser = subparsers.add_parser(
        "start-authoring-task",
        help="Start one active P0-P9 authoring task without writing formal task data.",
    )
    per_task.add_start_args(start_task_parser)
    start_task_parser.set_defaults(func=per_task.run_start_from_args)

    start_strict_repair_parser = subparsers.add_parser(
        "start-strict-authoring-repair",
        help="Start one active strict-repair task from the repair plan without generating formal data.",
    )
    per_task.add_start_strict_repair_args(start_strict_repair_parser)
    start_strict_repair_parser.set_defaults(func=per_task.run_start_strict_repair_from_args)

    record_stage_parser = subparsers.add_parser(
        "record-authoring-stage",
        help="Record one P0-P9 authoring stage and enforce sequential per-task gates.",
    )
    per_task.add_record_args(record_stage_parser)
    record_stage_parser.set_defaults(func=per_task.run_record_from_args)

    audit_task_parser = subparsers.add_parser(
        "audit-authoring-task",
        help="Audit one non-formal P0-P9 authoring progress file.",
    )
    per_task.add_audit_task_args(audit_task_parser)
    audit_task_parser.set_defaults(func=per_task.run_audit_task_from_args)

    audit_lock_parser = subparsers.add_parser(
        "audit-authoring-lock",
        help="Audit the one-task-at-a-time authoring lock.",
    )
    per_task.add_audit_lock_args(audit_lock_parser)
    audit_lock_parser.set_defaults(func=per_task.run_audit_lock_from_args)

    close_task_parser = subparsers.add_parser(
        "close-authoring-task",
        help="Close the active non-formal authoring progress after acceptance or rejection.",
    )
    per_task.add_close_args(close_task_parser)
    close_task_parser.set_defaults(func=per_task.run_close_from_args)

    slots_parser = subparsers.add_parser(
        "build-target-slots",
        help="Build the 400-task target slot ledger from current tasks and target distributions.",
    )
    ledger.add_build_slots_args(slots_parser)
    slots_parser.set_defaults(func=ledger.run_build_slots_from_args)

    candidate_cards_parser = subparsers.add_parser(
        "draft-candidate-review-cards",
        help="Draft generated candidate triage cards for open slots; these are not formal design briefs or task records.",
    )
    ledger.add_draft_candidate_review_cards_args(candidate_cards_parser)
    candidate_cards_parser.set_defaults(func=ledger.run_draft_candidate_review_cards_from_args)

    briefs_parser = subparsers.add_parser(
        "draft-agent-briefs",
        help="Deprecated alias for draft-candidate-review-cards; outputs generated triage cards, not formal task briefs.",
    )
    ledger.add_draft_briefs_args(briefs_parser)
    briefs_parser.set_defaults(func=ledger.run_draft_briefs_from_args)

    verify_parser = subparsers.add_parser("verify-reference", help="Run reference-state verifier specs.")
    verifier.add_verify_args(verify_parser)
    verify_parser.set_defaults(func=verifier.run_verify_from_args)

    submission_parser = subparsers.add_parser("verify-submission", help="Run post-patch submission verifier specs.")
    verifier.add_verify_submission_args(submission_parser)
    submission_parser.set_defaults(func=verifier.run_verify_submission_from_args)

    source_audit_parser = subparsers.add_parser(
        "audit-source",
        help="Audit submission source changes for implementation fit and anti-shortcut behavior.",
    )
    source_audit.add_source_audit_args(source_audit_parser)
    source_audit_parser.set_defaults(func=source_audit.run_source_audit_from_args)

    score_parser = subparsers.add_parser("score-report", help="Convert verifier reports into WCS and continuity sub-metric scores.")
    metrics.add_score_args(score_parser)
    score_parser.set_defaults(func=metrics.run_score_from_args)

    evaluate_parser = subparsers.add_parser(
        "evaluate-run",
        help="Run a batch model evaluation with concurrent API requests and parallel browser verification.",
    )
    batch_runner.add_evaluate_run_args(evaluate_parser)
    evaluate_parser.set_defaults(func=batch_runner.run_evaluate_run_from_args)

    reevaluate_parser = subparsers.add_parser(
        "reevaluate-run",
        help="Re-run verifier/capture for existing applied model patches without new model API calls.",
    )
    batch_runner.add_reevaluate_run_args(reevaluate_parser)
    reevaluate_parser.set_defaults(func=batch_runner.run_reevaluate_run_from_args)

    rescore_parser = subparsers.add_parser(
        "rescore-run",
        help="Re-run verifier scoring for existing captured model patches without recapturing or calling model APIs.",
    )
    batch_runner.add_rescore_run_args(rescore_parser)
    rescore_parser.set_defaults(func=batch_runner.run_rescore_run_from_args)

    harbor_parser = subparsers.add_parser(
        "export-harbor-tasks",
        help="Export ProductWebBench tasks into a Harbor-style task directory layout.",
    )
    harbor_adapter.add_export_harbor_args(harbor_parser)
    harbor_parser.set_defaults(func=harbor_adapter.export_harbor_tasks)

    leaderboard_parser = subparsers.add_parser(
        "build-leaderboard",
        help="Build a Website Continuity leaderboard from an evaluation run directory with eval-protocol publish gates.",
    )
    leaderboard.add_leaderboard_args(leaderboard_parser)
    leaderboard_parser.set_defaults(func=leaderboard.run_leaderboard_from_args)

    list_regimes_parser = subparsers.add_parser(
        "list-regimes",
        help="List registered Website Continuity regime plugins.",
    )
    regime_registry.add_list_args(list_regimes_parser)
    list_regimes_parser.set_defaults(func=regime_registry.run_list_from_args)

    author_regime_parser = subparsers.add_parser(
        "plan-regime-authoring",
        help="Write a non-formal regime authoring plan; does not generate task.jsonl.",
    )
    regime_registry.add_author_args(author_regime_parser)
    author_regime_parser.set_defaults(func=regime_registry.run_author_from_args)

    verifier_regime_parser = subparsers.add_parser(
        "plan-regime-verifier",
        help="Write a non-formal regime verifier plan; does not generate verifier specs.",
    )
    regime_registry.add_verifier_args(verifier_regime_parser)
    verifier_regime_parser.set_defaults(func=regime_registry.run_verifier_from_args)

    score_regime_parser = subparsers.add_parser(
        "score-regime-report",
        help="Score a report through the registered regime plugin interface.",
    )
    regime_registry.add_score_args(score_regime_parser)
    score_regime_parser.set_defaults(func=regime_registry.run_score_from_args)

    package_regime_parser = subparsers.add_parser(
        "build-regime-package-metadata",
        help="Build regime package metadata through the registered plugin interface.",
    )
    regime_registry.add_package_args(package_regime_parser)
    package_regime_parser.set_defaults(func=regime_registry.run_package_from_args)

    schema_parser = subparsers.add_parser(
        "export-capability-schema",
        help="Export Website Continuity metric and task-package schemas.",
    )
    pipeline_ledger.add_schema_args(schema_parser)
    schema_parser.set_defaults(func=pipeline_ledger.run_schema_from_args)

    status_parser = subparsers.add_parser(
        "build-status",
        help="Build Website Continuity coverage gaps and docs/STATUS.md from ledgers.",
    )
    pipeline_ledger.add_status_args(status_parser)
    status_parser.set_defaults(func=pipeline_ledger.run_status_from_args)

    capability_split_parser = subparsers.add_parser(
        "export-capability-split",
        help="Export existing change-regime tasks into the Website Continuity LM/MM schema.",
    )
    capability_split.add_export_args(capability_split_parser)
    capability_split_parser.set_defaults(func=capability_split.run_export_from_args)

    signal_audit_parser = subparsers.add_parser(
        "audit-signal-layers",
        help="Audit that LM/MM capability packages keep L-hard/L-metric primary and L-soft judge-only.",
    )
    signals.add_signal_audit_args(signal_audit_parser)
    signal_audit_parser.set_defaults(func=signals.run_signal_audit_from_args)

    mm_judge_spec_parser = subparsers.add_parser(
        "audit-mm-judge-spec",
        help="Audit MM judge checkpoints for local crops and discrete match/partial/mismatch labels.",
    )
    mm_judge.add_audit_args(mm_judge_spec_parser)
    mm_judge_spec_parser.set_defaults(func=mm_judge.run_audit_from_args)

    mm_judge_prediction_parser = subparsers.add_parser(
        "audit-mm-judge-predictions",
        help="Audit MM judge prediction files before calibration freeze or leaderboard scoring.",
    )
    mm_judge.add_prediction_audit_args(mm_judge_prediction_parser)
    mm_judge_prediction_parser.set_defaults(func=mm_judge.run_prediction_audit_from_args)

    mm_judge_prediction_queue_parser = subparsers.add_parser(
        "build-mm-judge-prediction-queue",
        help="Build a non-formal queue of sampled MM checkpoints still needing independent judge predictions.",
    )
    mm_judge.add_prediction_queue_args(mm_judge_prediction_queue_parser)
    mm_judge_prediction_queue_parser.set_defaults(func=mm_judge.run_prediction_queue_from_args)

    mm_judge_prediction_append_parser = subparsers.add_parser(
        "append-mm-judge-prediction",
        help="Append exactly one manually produced non-formal MM judge prediction.",
    )
    mm_judge.add_append_prediction_args(mm_judge_prediction_append_parser)
    mm_judge_prediction_append_parser.set_defaults(func=mm_judge.run_append_prediction_from_args)

    mm_judge_perturbation_template_parser = subparsers.add_parser(
        "mm-judge-perturbation-group-template",
        help="Write a non-publishable template for one MM judge perturbation-control group.",
    )
    mm_judge.add_perturbation_group_template_args(mm_judge_perturbation_template_parser)
    mm_judge_perturbation_template_parser.set_defaults(func=mm_judge.run_perturbation_group_template_from_args)

    mm_judge_perturbation_queue_parser = subparsers.add_parser(
        "build-mm-judge-perturbation-queue",
        help="Build a non-formal queue of MM judge perturbation-control groups still needing manual authoring.",
    )
    mm_judge.add_perturbation_queue_args(mm_judge_perturbation_queue_parser)
    mm_judge_perturbation_queue_parser.set_defaults(func=mm_judge.run_perturbation_queue_from_args)

    mm_judge_perturbation_append_parser = subparsers.add_parser(
        "append-mm-judge-perturbation-group",
        help="Append exactly one manually produced non-formal MM judge perturbation group.",
    )
    mm_judge.add_append_perturbation_group_args(mm_judge_perturbation_append_parser)
    mm_judge_perturbation_append_parser.set_defaults(func=mm_judge.run_append_perturbation_group_from_args)

    mm_judge_perturbation_parser = subparsers.add_parser(
        "audit-mm-judge-perturbations",
        help="Audit MM judge bias/perturbation controls for label stability.",
    )
    mm_judge.add_perturbation_audit_args(mm_judge_perturbation_parser)
    mm_judge_perturbation_parser.set_defaults(func=mm_judge.run_perturbation_audit_from_args)

    eval_protocol_parser = subparsers.add_parser(
        "audit-eval-protocol",
        help="Audit Website Continuity publish gates from existing schema/MM/meta-eval/negative-control evidence.",
    )
    protocol.add_protocol_args(eval_protocol_parser)
    eval_protocol_parser.set_defaults(func=protocol.run_protocol_from_args)

    readiness_parser = subparsers.add_parser(
        "audit-0618-readiness",
        help="Audit 0618 traction-document invariants and phase readiness from existing evidence.",
    )
    readiness.add_readiness_args(readiness_parser)
    readiness_parser.set_defaults(func=readiness.run_readiness_from_args)

    readiness_repair_parser = subparsers.add_parser(
        "plan-0618-readiness-repair",
        help="Write a non-formal repair plan for failed 0618 invariants and phases.",
    )
    readiness_repair.add_readiness_repair_plan_args(readiness_repair_parser)
    readiness_repair_parser.set_defaults(func=readiness_repair.run_readiness_repair_plan_from_args)

    denominator_parser = subparsers.add_parser(
        "audit-denominators",
        help="Audit that ledger/evaluable/publishable task denominators are internally consistent.",
    )
    denominators.add_denominator_audit_args(denominator_parser)
    denominator_parser.set_defaults(func=denominators.run_denominator_audit_from_args)

    mm_repair_parser = subparsers.add_parser(
        "plan-mm-protocol-repair",
        help="Write a non-formal repair plan for MM/de-AI/calibration/model-isolation blockers.",
    )
    mm_repair.add_mm_repair_plan_args(mm_repair_parser)
    mm_repair_parser.set_defaults(func=mm_repair.run_mm_repair_plan_from_args)

    construction_repair_parser = subparsers.add_parser(
        "plan-construction-repair",
        help="Write a non-formal repair plan for construction-regime strict blockers.",
    )
    construction_repair.add_construction_repair_plan_args(construction_repair_parser)
    construction_repair_parser.set_defaults(func=construction_repair.run_construction_repair_plan_from_args)

    construction_evidence_worklist_parser = subparsers.add_parser(
        "build-construction-evidence-worklist",
        help="Build a non-formal one-draft-at-a-time worklist for construction reference evidence.",
    )
    construction_repair.add_construction_evidence_worklist_args(construction_evidence_worklist_parser)
    construction_evidence_worklist_parser.set_defaults(
        func=construction_repair.run_construction_evidence_worklist_from_args
    )

    model_role_template_parser = subparsers.add_parser(
        "model-role-manifest-template",
        help="Write a non-publishable model role manifest template.",
    )
    model_isolation.add_template_args(model_role_template_parser)
    model_role_template_parser.set_defaults(func=model_isolation.run_template_from_args)

    model_role_queue_parser = subparsers.add_parser(
        "build-model-role-queue",
        help="Build a non-formal queue of real model roles still needed for MM model isolation.",
    )
    model_isolation.add_role_queue_args(model_role_queue_parser)
    model_role_queue_parser.set_defaults(func=model_isolation.run_role_queue_from_args)

    model_role_build_parser = subparsers.add_parser(
        "build-model-role-manifest",
        help="Build and immediately audit a non-formal model role manifest from explicit run roles.",
    )
    model_isolation.add_build_manifest_args(model_role_build_parser)
    model_role_build_parser.set_defaults(func=model_isolation.run_build_manifest_from_args)

    model_role_append_parser = subparsers.add_parser(
        "append-model-role",
        help="Append one real non-formal model role and refresh model-isolation audit.",
    )
    model_isolation.add_append_role_args(model_role_append_parser)
    model_role_append_parser.set_defaults(func=model_isolation.run_append_role_from_args)

    model_isolation_parser = subparsers.add_parser(
        "audit-model-isolation",
        help="Audit spec/judge/actor model-family separation for MM publish gates.",
    )
    model_isolation.add_audit_args(model_isolation_parser)
    model_isolation_parser.set_defaults(func=model_isolation.run_audit_from_args)

    deai_anchor_template_parser = subparsers.add_parser(
        "deai-contrast-anchor-template",
        help="Write a non-publishable template for one de-AI contrast anchor.",
    )
    deai.add_anchor_template_args(deai_anchor_template_parser)
    deai_anchor_template_parser.set_defaults(func=deai.run_anchor_template_from_args)

    deai_anchor_queue_parser = subparsers.add_parser(
        "build-deai-contrast-anchor-queue",
        help="Build a non-formal queue of inspected de-AI contrast anchors still needed for Phase 0.",
    )
    deai.add_anchor_queue_args(deai_anchor_queue_parser)
    deai_anchor_queue_parser.set_defaults(func=deai.run_anchor_queue_from_args)

    deai_append_anchor_parser = subparsers.add_parser(
        "append-deai-contrast-anchor",
        help="Append exactly one manually authored non-formal de-AI contrast anchor.",
    )
    deai.add_append_anchor_args(deai_append_anchor_parser)
    deai_append_anchor_parser.set_defaults(func=deai.run_append_anchor_from_args)

    deai_contrast_parser = subparsers.add_parser(
        "audit-deai-contrast",
        help="Audit the non-formal de-AI default-template fingerprint contrast set.",
    )
    deai.add_deai_contrast_audit_args(deai_contrast_parser)
    deai_contrast_parser.set_defaults(func=deai.run_deai_contrast_audit_from_args)

    mm_sample_parser = subparsers.add_parser(
        "sample-mm-calibration",
        help="Sample MM judge checkpoints for human calibration.",
    )
    mm_calibration.add_sample_args(mm_sample_parser)
    mm_sample_parser.set_defaults(func=mm_calibration.run_sample_from_args)

    mm_annotation_export_parser = subparsers.add_parser(
        "export-mm-annotation-pack",
        help="Export a judge-hidden, non-formal MM human annotation template from a calibration sample.",
    )
    mm_calibration.add_annotation_export_args(mm_annotation_export_parser)
    mm_annotation_export_parser.set_defaults(func=mm_calibration.run_annotation_export_from_args)

    mm_annotation_audit_parser = subparsers.add_parser(
        "audit-mm-annotation-pack",
        help="Audit a non-formal MM human annotation template before labeling.",
    )
    mm_calibration.add_annotation_audit_args(mm_annotation_audit_parser)
    mm_annotation_audit_parser.set_defaults(func=mm_calibration.run_annotation_audit_from_args)

    mm_human_annotation_template_parser = subparsers.add_parser(
        "mm-human-annotation-template",
        help="Write a non-publishable template for one MM human annotation.",
    )
    mm_calibration.add_human_annotation_template_args(mm_human_annotation_template_parser)
    mm_human_annotation_template_parser.set_defaults(func=mm_calibration.run_human_annotation_template_from_args)

    mm_human_annotation_queue_parser = subparsers.add_parser(
        "build-mm-human-annotation-queue",
        help="Build a non-formal queue of sampled MM checkpoints still needing human labels.",
    )
    mm_calibration.add_human_annotation_queue_args(mm_human_annotation_queue_parser)
    mm_human_annotation_queue_parser.set_defaults(func=mm_calibration.run_human_annotation_queue_from_args)

    mm_human_annotation_append_parser = subparsers.add_parser(
        "append-mm-human-annotation",
        help="Append exactly one judge-hidden human MM annotation.",
    )
    mm_calibration.add_append_human_annotation_args(mm_human_annotation_append_parser)
    mm_human_annotation_append_parser.set_defaults(func=mm_calibration.run_append_human_annotation_from_args)

    mm_freeze_labels_parser = subparsers.add_parser(
        "freeze-mm-human-labels",
        help="Freeze human MM labels by merging judge-hidden annotations with independent judge predictions.",
    )
    mm_calibration.add_freeze_labels_args(mm_freeze_labels_parser)
    mm_freeze_labels_parser.set_defaults(func=mm_calibration.run_freeze_labels_from_args)

    mm_score_parser = subparsers.add_parser(
        "score-mm-calibration",
        help="Score MM judge labels against human labels with agreement and Cohen's kappa.",
    )
    mm_calibration.add_score_args(mm_score_parser)
    mm_score_parser.set_defaults(func=mm_calibration.run_score_from_args)

    mm_audit_parser = subparsers.add_parser(
        "audit-mm-calibration",
        help="Audit whether MM judge calibration is publishable for leaderboard use.",
    )
    mm_calibration.add_audit_args(mm_audit_parser)
    mm_audit_parser.set_defaults(func=mm_calibration.run_audit_from_args)

    construction_draft_parser = subparsers.add_parser(
        "draft-construction",
        help="Draft a content-free construction-regime trajectory from captured browser states.",
    )
    construction_authoring.add_draft_args(construction_draft_parser)
    construction_draft_parser.set_defaults(func=construction_authoring.run_draft_from_args)

    construction_score_parser = subparsers.add_parser(
        "score-construction-trajectory",
        help="Score a construction-regime trajectory report with TCS/TD/ITR/QS.",
    )
    construction_scoring.add_score_args(construction_score_parser)
    construction_score_parser.set_defaults(func=construction_scoring.run_score_from_args)

    construction_evidence_report_parser = subparsers.add_parser(
        "build-construction-trajectory-evidence",
        help="Normalize a construction trajectory into a reference/original/bad/repeat evidence report.",
    )
    construction_evidence_reports.add_build_args(construction_evidence_report_parser)
    construction_evidence_report_parser.set_defaults(func=construction_evidence_reports.run_build_from_args)

    construction_milestone_run_parser = subparsers.add_parser(
        "build-construction-milestone-run-report",
        help="Build one non-formal construction milestone run report from workspace/capture/verifier artifacts.",
    )
    construction_trajectory_run.add_milestone_args(construction_milestone_run_parser)
    construction_milestone_run_parser.set_defaults(func=construction_trajectory_run.run_milestone_from_args)

    construction_trajectory_run_parser = subparsers.add_parser(
        "build-construction-trajectory-run-report",
        help="Aggregate per-milestone construction run artifacts into a scored trajectory report.",
    )
    construction_trajectory_run.add_trajectory_args(construction_trajectory_run_parser)
    construction_trajectory_run_parser.set_defaults(func=construction_trajectory_run.run_trajectory_from_args)

    construction_trajectory_run_validate_parser = subparsers.add_parser(
        "validate-construction-trajectory-run-report",
        help="Validate a construction trajectory run report and its artifact references.",
    )
    construction_trajectory_run.add_validate_trajectory_args(construction_trajectory_run_validate_parser)
    construction_trajectory_run_validate_parser.set_defaults(func=construction_trajectory_run.run_validate_trajectory_from_args)

    construction_actor_loop_parser = subparsers.add_parser(
        "run-construction-actor-loop",
        help="Run external actor/capture/verifier commands for a construction evidence root.",
    )
    construction_actor_loop.add_run_args(construction_actor_loop_parser)
    construction_actor_loop_parser.set_defaults(func=construction_actor_loop.run_from_args)

    construction_actor_loop_validate_parser = subparsers.add_parser(
        "validate-construction-actor-loop-report",
        help="Validate a non-formal construction actor loop report.",
    )
    construction_actor_loop.add_validate_args(construction_actor_loop_validate_parser)
    construction_actor_loop_validate_parser.set_defaults(func=construction_actor_loop.run_validate_from_args)

    construction_evidence_root_prepare_parser = subparsers.add_parser(
        "prepare-construction-evidence-root",
        help="Prepare a non-formal per-milestone scaffold for a spec-only construction actor run.",
    )
    construction_evidence_root.add_prepare_args(construction_evidence_root_prepare_parser)
    construction_evidence_root_prepare_parser.set_defaults(func=construction_evidence_root.run_prepare_from_args)

    construction_evidence_root_audit_parser = subparsers.add_parser(
        "audit-construction-evidence-root",
        help="Audit a construction evidence scaffold; optionally require completed actor/verifier artifacts.",
    )
    construction_evidence_root.add_audit_args(construction_evidence_root_audit_parser)
    construction_evidence_root_audit_parser.set_defaults(func=construction_evidence_root.run_audit_from_args)

    construction_asset_policy_template_parser = subparsers.add_parser(
        "construction-asset-policy-template",
        help="Write a construction asset policy template for actor-visible intrinsic/decorative assets.",
    )
    construction_asset_policy.add_template_args(construction_asset_policy_template_parser)
    construction_asset_policy_template_parser.set_defaults(func=construction_asset_policy.run_template_from_args)

    construction_asset_policy_validate_parser = subparsers.add_parser(
        "validate-construction-asset-policy",
        help="Validate construction asset classification and actor-visible asset exposure policy.",
    )
    construction_asset_policy.add_validate_args(construction_asset_policy_validate_parser)
    construction_asset_policy_validate_parser.set_defaults(func=construction_asset_policy.run_validate_from_args)

    construction_asset_policy_attach_parser = subparsers.add_parser(
        "attach-construction-asset-policy",
        help="Attach a validated construction asset policy to a construction draft.",
    )
    construction_asset_policy.add_attach_args(construction_asset_policy_attach_parser)
    construction_asset_policy_attach_parser.set_defaults(func=construction_asset_policy.run_attach_from_args)

    construction_precheck_parser = subparsers.add_parser(
        "precheck-construction-task",
        help="Run construction-regime de-leak/spec/trajectory freeze gates.",
    )
    construction_precheck.add_precheck_args(construction_precheck_parser)
    construction_precheck_parser.set_defaults(func=construction_precheck.run_precheck_from_args)

    construction_trace_template_parser = subparsers.add_parser(
        "construction-reference-actor-template",
        help="Write a non-passing template for a manually audited spec-only construction actor trace.",
    )
    construction_reference_actor.add_trace_template_args(construction_trace_template_parser)
    construction_trace_template_parser.set_defaults(func=construction_reference_actor.run_trace_template_from_args)

    construction_build_actor_manifest_parser = subparsers.add_parser(
        "build-construction-reference-actor-manifest",
        help="Build a reference actor evidence manifest from an existing per-milestone evidence root.",
    )
    construction_reference_actor.add_build_manifest_args(construction_build_actor_manifest_parser)
    construction_build_actor_manifest_parser.set_defaults(func=construction_reference_actor.run_build_manifest_from_args)

    construction_build_trace_parser = subparsers.add_parser(
        "build-construction-reference-actor-trace",
        help="Build a passing reference actor trace from existing per-milestone evidence artifacts.",
    )
    construction_reference_actor.add_build_trace_args(construction_build_trace_parser)
    construction_build_trace_parser.set_defaults(func=construction_reference_actor.run_build_trace_from_args)

    construction_attach_trace_parser = subparsers.add_parser(
        "attach-construction-reference-actor-trace",
        help="Validate and attach a spec-only reference actor trace to a construction draft.",
    )
    construction_reference_actor.add_attach_trace_args(construction_attach_trace_parser)
    construction_attach_trace_parser.set_defaults(func=construction_reference_actor.run_attach_trace_from_args)

    construction_finalize_evidence_parser = subparsers.add_parser(
        "finalize-construction-reference-evidence",
        help="Validate existing spec-only actor evidence and attach completed trajectory/actor reports.",
    )
    construction_reference_evidence.add_finalize_args(construction_finalize_evidence_parser)
    construction_finalize_evidence_parser.set_defaults(func=construction_reference_evidence.run_finalize_from_args)

    construction_build_meta_eval_parser = subparsers.add_parser(
        "build-construction-metareval",
        help="Build a construction meta-eval report from reference/original/bad/repeat evidence reports.",
    )
    construction_metareval.add_build_args(construction_build_meta_eval_parser)
    construction_build_meta_eval_parser.set_defaults(func=construction_metareval.run_build_from_args)

    construction_attach_meta_eval_parser = subparsers.add_parser(
        "attach-construction-metareval",
        help="Validate and attach construction meta-eval evidence before strict acceptance.",
    )
    construction_metareval.add_attach_args(construction_attach_meta_eval_parser)
    construction_attach_meta_eval_parser.set_defaults(func=construction_metareval.run_attach_from_args)

    construction_accept_parser = subparsers.add_parser(
        "accept-construction-task",
        help="Append a construction task to the ledger only after strict precheck passes.",
    )
    construction_ledger.add_accept_args(construction_accept_parser)
    construction_accept_parser.set_defaults(func=construction_ledger.run_accept_from_args)

    meta_eval_parser = subparsers.add_parser(
        "meta-eval",
        help="Check reference-pass/original-fail/bad-solution-fail/reproducibility sanity gates.",
    )
    metareval.add_meta_eval_args(meta_eval_parser)
    meta_eval_parser.set_defaults(func=metareval.run_meta_eval_from_args)

    meta_eval_batch_parser = subparsers.add_parser(
        "meta-eval-batch",
        help="Scan formal task files and summarize meta-eval evidence coverage.",
    )
    metareval.add_meta_eval_batch_args(meta_eval_batch_parser)
    meta_eval_batch_parser.set_defaults(func=metareval.run_meta_eval_batch_from_args)

    negative_controls_parser = subparsers.add_parser(
        "plan-negative-controls",
        help="Build the bad-solution negative-control execution plan for formal tasks.",
    )
    negative_controls.add_plan_args(negative_controls_parser)
    negative_controls_parser.set_defaults(func=negative_controls.run_plan_from_args)

    negative_control_run_parser = subparsers.add_parser(
        "run-negative-control",
        help="Execute one independent bad-solution negative control for a formal task.",
    )
    negative_controls.add_run_args(negative_control_run_parser)
    negative_control_run_parser.set_defaults(func=negative_controls.run_negative_control_from_args)

    negative_control_batch_parser = subparsers.add_parser(
        "run-negative-controls",
        help="Execute independent bad-solution negative controls for formal tasks with resume support.",
    )
    negative_controls.add_run_batch_args(negative_control_batch_parser)
    negative_control_batch_parser.set_defaults(func=negative_controls.run_negative_control_batch_from_args)

    negative_control_audit_parser = subparsers.add_parser(
        "audit-negative-controls",
        help="Audit bad-solution reports against expected failed verifier checks.",
    )
    negative_controls.add_audit_args(negative_control_audit_parser)
    negative_control_audit_parser.set_defaults(func=negative_controls.run_audit_from_args)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
