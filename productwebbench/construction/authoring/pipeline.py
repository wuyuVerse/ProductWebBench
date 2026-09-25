from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ...core.config import DEFAULT_OUTPUT_ROOT
from ...core.io_utils import ensure_dir, write_json
from ...evaluation.scoring import metrics
from ...evaluation.verification import verifier
from . import asset_gallery, consistency, coverage, design_anchors, dossiers, evidence, provenance, quality, rationales, review_board, task_packages, tasks


@dataclass(frozen=True)
class SplitPaths:
    split: str
    tasks: Path
    state_plan: Path
    reference_specs: Path
    submission_specs: Path
    rationales: Path
    evidence: Path
    design_anchors: Path
    provenance: Path
    rationales_validation: Path
    reference_results: Path
    submission_results: Path
    reference_score: Path
    submission_score: Path
    package_validation: Path
    consistency: Path
    quality: Path
    coverage: Path
    asset_galleries_root: Path
    dossiers_root: Path
    package_root: Path
    review_root: Path
    pipeline_report: Path


def split_paths(output_root: Path, split: str) -> SplitPaths:
    task_root = output_root / "tasks"
    return SplitPaths(
        split=split,
        tasks=task_root / f"{split}.jsonl",
        state_plan=task_root / f"{split}_state_plan.json",
        reference_specs=task_root / f"{split}_verifier_specs.json",
        submission_specs=task_root / f"{split}_submission_specs.json",
        rationales=task_root / f"{split}.rationales.json",
        evidence=task_root / f"{split}.evidence_index.json",
        design_anchors=task_root / f"{split}.design_anchors.json",
        provenance=task_root / f"{split}.provenance.json",
        rationales_validation=task_root / f"{split}.rationales.validation.json",
        reference_results=task_root / f"{split}.reference_results.json",
        submission_results=task_root / f"{split}.submission_results.reference_states.json",
        reference_score=task_root / f"{split}.reference_score_report.json",
        submission_score=task_root / f"{split}.submission_score_report.reference_states.json",
        package_validation=task_root / f"{split}.package_validation.json",
        consistency=task_root / f"{split}.consistency.json",
        quality=task_root / f"{split}.quality_audit.json",
        coverage=task_root / f"{split}.coverage_audit.json",
        asset_galleries_root=output_root / "asset_galleries" / split,
        dossiers_root=output_root / "dossiers" / split,
        package_root=output_root / "bench" / split,
        review_root=output_root / "review" / split,
        pipeline_report=task_root / f"{split}.pipeline_report.json",
    )


def step_status(name: str, summary: dict[str, Any], passed: bool | None = None) -> dict[str, Any]:
    if passed is None:
        if "failed" in summary:
            passed = int(summary.get("failed", 0)) == 0
        elif "passed" in summary and isinstance(summary.get("passed"), bool):
            passed = bool(summary["passed"])
        elif "missing_path_count" in summary:
            passed = int(summary.get("missing_path_count", 0)) == 0
        else:
            passed = True
    return {
        "name": name,
        "passed": bool(passed),
        "total": summary.get("total"),
        "passed_count": summary.get("passed") if not isinstance(summary.get("passed"), bool) else None,
        "failed_count": summary.get("failed"),
        "warning_count": summary.get("warning_count", summary.get("warnings")),
        "output": summary.get("output_path") or summary.get("html_path") or summary.get("package_root"),
    }


def run_step(name: str, collector: list[dict[str, Any]], func: Callable[[], dict[str, Any]], passed: Callable[[dict[str, Any]], bool] | None = None) -> dict[str, Any]:
    summary = func()
    collector.append(step_status(name, summary, passed(summary) if passed else None))
    status = "passed" if collector[-1]["passed"] else "failed"
    print(f"[{status}] {name}")
    return summary


def build_split_pipeline(
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
    paths = split_paths(output_root, split)
    steps: list[dict[str, Any]] = []

    run_step(
        "validate_tasks",
        steps,
        lambda: tasks.validate_tasks(paths.tasks, output_root / "tasks" / f"{split}.validation_report.json"),
    )
    run_step("evidence_index", steps, lambda: evidence.build_evidence_index(paths.tasks, states_root, paths.evidence))
    run_step(
        "design_anchors",
        steps,
        lambda: design_anchors.build_design_anchor_index(paths.tasks, states_root, paths.design_anchors, paths.state_plan, sample_limit=12),
    )
    run_step(
        "build_provenance",
        steps,
        lambda: provenance.build_provenance_index(paths.tasks, paths.submission_specs, workspace_root, states_root, paths.provenance),
    )
    run_step(
        "asset_galleries",
        steps,
        lambda: asset_gallery.build_asset_galleries(paths.tasks, workspace_root, paths.asset_galleries_root, max_assets=max_assets),
    )
    run_step(
        "dossiers",
        steps,
        lambda: dossiers.build_dossiers(paths.tasks, workspace_root, paths.design_anchors, paths.dossiers_root),
    )
    run_step(
        "validate_rationales",
        steps,
        lambda: rationales.validate_rationales(paths.tasks, paths.rationales, paths.dossiers_root, paths.provenance, paths.rationales_validation),
    )
    run_step(
        "verify_reference",
        steps,
        lambda: verifier.verify_specs(paths.tasks, paths.reference_specs, states_root, paths.reference_results),
    )
    run_step(
        "verify_submission_on_reference_states",
        steps,
        lambda: verifier.verify_submission_specs(paths.tasks, paths.submission_specs, states_root, paths.submission_results, paths.design_anchors),
        passed=lambda summary: int(summary.get("passed", 0)) == 0,
    )
    run_step(
        "score_reference_report",
        steps,
        lambda: metrics.score_report(paths.reference_results, paths.reference_score),
    )
    run_step(
        "score_submission_sanity_report",
        steps,
        lambda: metrics.score_report(paths.submission_results, paths.submission_score),
        passed=lambda summary: int(summary.get("wcs_passed", 0)) == 0,
    )
    run_step(
        "export_task_packages",
        steps,
        lambda: task_packages.export_task_packages(
            tasks_path=paths.tasks,
            reference_specs_path=paths.reference_specs,
            submission_specs_path=paths.submission_specs,
            design_anchors_path=paths.design_anchors,
            provenance_path=paths.provenance,
            evidence_path=paths.evidence,
            rationales_path=paths.rationales,
            asset_galleries_root=paths.asset_galleries_root,
            states_root=states_root,
            output_root=paths.package_root,
            clean=clean_packages,
        ),
    )
    run_step(
        "validate_task_packages",
        steps,
        lambda: task_packages.validate_task_packages(paths.package_root, paths.package_validation),
    )
    run_step(
        "audit_consistency",
        steps,
        lambda: consistency.audit_consistency(
            tasks_path=paths.tasks,
            reference_specs_path=paths.reference_specs,
            submission_specs_path=paths.submission_specs,
            design_anchors_path=paths.design_anchors,
            evidence_path=paths.evidence,
            provenance_path=paths.provenance,
            rationales_path=paths.rationales,
            state_plan_path=paths.state_plan,
            states_root=states_root,
            package_manifest_path=paths.package_root / "manifest.json",
            output_path=paths.consistency,
        ),
    )
    run_step(
        "audit_quality",
        steps,
        lambda: quality.audit_quality(paths.tasks, paths.reference_specs, paths.submission_specs, paths.design_anchors, paths.quality),
    )
    run_step(
        "audit_coverage",
        steps,
        lambda: coverage.audit_coverage(
            tasks_path=paths.tasks,
            output_path=paths.coverage,
            package_manifest_path=paths.package_root / "manifest.json",
            min_tasks=min_tasks,
            min_repos=min_repos,
            min_scopes=min_scopes,
            min_difficulties=min_difficulties,
            min_facets=min_facets,
            min_tasks_for_full_targets=min_tasks_for_full_targets,
        ),
    )
    if build_review:
        run_step(
            "build_review_board",
            steps,
            lambda: review_board.build_review_board(
                package_manifest_path=paths.package_root / "manifest.json",
                output_root=paths.review_root,
                max_states=review_max_states,
                max_crops=review_max_crops,
                max_assets=review_max_assets,
            ),
        )

    report = {
        "split": split,
        "output_root": str(output_root),
        "workspace_root": str(workspace_root),
        "states_root": str(states_root),
        "passed": all(step["passed"] for step in steps),
        "steps": steps,
        "paths": {key: str(value) for key, value in paths.__dict__.items()},
    }
    write_json(paths.pipeline_report, report)
    return report


def add_pipeline_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--split", default="dev_seed")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--workspace-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "workspaces")
    parser.add_argument("--states-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "states")
    parser.add_argument("--no-clean-packages", action="store_true")
    parser.add_argument("--skip-review-board", action="store_true")
    parser.add_argument("--max-assets", type=int, default=32)
    parser.add_argument("--review-max-states", type=int, default=12)
    parser.add_argument("--review-max-crops", type=int, default=80)
    parser.add_argument("--review-max-assets", type=int, default=60)
    parser.add_argument("--min-tasks", type=int, default=1)
    parser.add_argument("--min-repos", type=int, default=1)
    parser.add_argument("--min-scopes", type=int, default=1)
    parser.add_argument("--min-difficulties", type=int, default=1)
    parser.add_argument("--min-facets", type=int, default=1)
    parser.add_argument("--min-tasks-for-full-targets", type=int, default=50)


def run_pipeline_from_args(args: argparse.Namespace) -> None:
    ensure_dir(args.output_root / "tasks")
    report = build_split_pipeline(
        split=args.split,
        output_root=args.output_root,
        workspace_root=args.workspace_root,
        states_root=args.states_root,
        clean_packages=not args.no_clean_packages,
        build_review=not args.skip_review_board,
        max_assets=args.max_assets,
        review_max_states=args.review_max_states,
        review_max_crops=args.review_max_crops,
        review_max_assets=args.review_max_assets,
        min_tasks=args.min_tasks,
        min_repos=args.min_repos,
        min_scopes=args.min_scopes,
        min_difficulties=args.min_difficulties,
        min_facets=args.min_facets,
        min_tasks_for_full_targets=args.min_tasks_for_full_targets,
    )
    status = "passed" if report["passed"] else "failed"
    print(f"build-split {args.split}: {status}; report={report['paths']['pipeline_report']}")
