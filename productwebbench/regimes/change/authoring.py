from __future__ import annotations

from pathlib import Path
from typing import Any

from ...construction.authoring import pipeline, task_packages, tasks
from ...taxonomy.capability import CHANGE_REGIME


def validate_change_tasks(tasks_path: Path, report_path: Path) -> dict[str, Any]:
    report = tasks.validate_tasks(tasks_path, report_path)
    return {
        "schema_version": "2026-06-19",
        "artifact_type": "change_regime_task_validation",
        "formal_task_record": False,
        "regime": CHANGE_REGIME,
        "wrapped_module": "productwebbench.construction.authoring.tasks",
        "report": report,
    }


def audit_change_authoring_data(
    *,
    repo_manifest_path: Path,
    candidate_path: Path,
    task_root: Path,
    output_path: Path,
    expected_candidates: int | None,
    pattern: str,
    require_unique_task_repos: bool,
    require_review_artifacts: bool,
    require_freeze_audits: bool = False,
    require_no_bulk_declarations: bool = False,
) -> dict[str, Any]:
    report = tasks.audit_authoring_data(
        repo_manifest_path=repo_manifest_path,
        candidate_path=candidate_path,
        task_root=task_root,
        output_path=output_path,
        expected_candidates=expected_candidates,
        pattern=pattern,
        require_unique_task_repos=require_unique_task_repos,
        require_review_artifacts=require_review_artifacts,
        require_freeze_audits=require_freeze_audits,
        require_no_bulk_declarations=require_no_bulk_declarations,
    )
    return {
        "schema_version": "2026-06-19",
        "artifact_type": "change_regime_authoring_audit",
        "formal_task_record": False,
        "regime": CHANGE_REGIME,
        "wrapped_module": "productwebbench.construction.authoring.tasks",
        "report": report,
    }


def export_change_task_packages(
    *,
    tasks_path: Path,
    reference_specs_path: Path,
    submission_specs_path: Path,
    design_anchors_path: Path,
    provenance_path: Path,
    evidence_path: Path,
    rationales_path: Path,
    asset_galleries_root: Path,
    states_root: Path,
    output_root: Path,
    clean: bool = False,
) -> dict[str, Any]:
    report = task_packages.export_task_packages(
        tasks_path=tasks_path,
        reference_specs_path=reference_specs_path,
        submission_specs_path=submission_specs_path,
        design_anchors_path=design_anchors_path,
        provenance_path=provenance_path,
        evidence_path=evidence_path,
        rationales_path=rationales_path,
        asset_galleries_root=asset_galleries_root,
        states_root=states_root,
        output_root=output_root,
        clean=clean,
    )
    return {
        "schema_version": "2026-06-19",
        "artifact_type": "change_regime_package_export",
        "formal_task_record": False,
        "regime": CHANGE_REGIME,
        "wrapped_module": "productwebbench.construction.authoring.task_packages",
        "report": report,
    }


def build_change_split_pipeline(
    *,
    split: str,
    output_root: Path,
    workspace_root: Path,
    states_root: Path,
    clean_packages: bool,
    build_review: bool,
    max_assets: int,
    review_max_states: int,
    review_max_crops: int,
    review_max_assets: int,
    min_tasks: int,
    min_repos: int,
    min_scopes: int,
    min_difficulties: int,
    min_facets: int,
    min_tasks_for_full_targets: int,
) -> dict[str, Any]:
    report = pipeline.build_split_pipeline(
        split=split,
        output_root=output_root,
        workspace_root=workspace_root,
        states_root=states_root,
        clean_packages=clean_packages,
        build_review=build_review,
        max_assets=max_assets,
        review_max_states=review_max_states,
        review_max_crops=review_max_crops,
        review_max_assets=review_max_assets,
        min_tasks=min_tasks,
        min_repos=min_repos,
        min_scopes=min_scopes,
        min_difficulties=min_difficulties,
        min_facets=min_facets,
        min_tasks_for_full_targets=min_tasks_for_full_targets,
    )
    return {
        "schema_version": "2026-06-19",
        "artifact_type": "change_regime_split_pipeline",
        "formal_task_record": False,
        "regime": CHANGE_REGIME,
        "wrapped_module": "productwebbench.construction.authoring.pipeline",
        "report": report,
    }
