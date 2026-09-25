from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..core.config import DEFAULT_OUTPUT_ROOT
from ..core.formal_data_guard import assert_not_under_formal_task_root
from ..core.io_utils import ensure_dir, write_json
from .denominators import audit_denominator_consistency_from_status


ARTIFACT_TYPE = "eval_protocol_audit"
DEFAULT_COVERAGE_REPORT = DEFAULT_OUTPUT_ROOT / "authoring_ledger" / "coverage_gaps.json"
DEFAULT_OUTPUT = DEFAULT_OUTPUT_ROOT / "eval_protocol" / "protocol_audit.json"


def load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def gate(name: str, *, passed: bool, issues: list[str], summary: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "issues": issues,
        "issue_count": len(issues),
        "summary": summary or {},
    }


def require(condition: bool, issue: str, issues: list[str]) -> None:
    if not condition:
        issues.append(issue)


BLOCKER_CATEGORY_RULES = (
    (
        "change_strict_freeze_no_bulk",
        (
            "strict freeze",
            "no-bulk",
            "single-task freeze",
            "duplicate repo_id",
            "authored tasks reuse",
        ),
    ),
    (
        "deai_contrast_missing_or_incomplete",
        (
            "de-ai contrast",
            "contrast set",
            "fingerprint-rich",
            "model families",
            "website types",
        ),
    ),
    (
        "model_isolation_missing_or_incomplete",
        (
            "model isolation",
            "model role",
            "provider/model/family",
        ),
    ),
    (
        "mm_human_calibration_missing_or_incomplete",
        (
            "human-label freeze",
            "human labeled",
            "human labels",
            "annotation",
            "judge predictions",
            "agreement",
            "cohen_kappa",
            "frozen label",
            "change mm publish gate",
            "change mm calibration",
        ),
    ),
    (
        "mm_judge_perturbation_missing_or_incomplete",
        (
            "judge_prediction audit",
            "judge_perturbation audit",
            "perturbation",
            "label-flip",
            "required perturbation",
        ),
    ),
    (
        "construction_task_data_missing",
        (
            "construction regime has no accepted tasks",
            "construction capability records",
            "construction capability split",
            "construction mm checkpoints",
        ),
    ),
    (
        "construction_reference_evidence_missing",
        (
            "construction strict issue",
            "strict construction prechecks",
            "reference_actor_trace",
            "reference trajectory",
            "construction metareval",
            "metareval report",
        ),
    ),
    (
        "phase_dependency_not_ready",
        (
            "phase0",
            "phase3",
            "publish_gate is not passed",
            "leaderboard is not passed",
            "lm publish gate is not passed",
        ),
    ),
    (
        "mm_judge_spec_missing_or_incomplete",
        (
            "mm judge spec",
            "mm judge checkpoints",
        ),
    ),
)


def classify_protocol_issue(issue: str) -> str:
    lowered = issue.lower()
    for category, tokens in BLOCKER_CATEGORY_RULES:
        if any(token in lowered for token in tokens):
            return category
    if "stale" in lowered or "changed or is missing" in lowered:
        return "stale_or_changed_evidence"
    return "other"


def blocker_summary(issues: list[str]) -> dict[str, Any]:
    categories: dict[str, dict[str, Any]] = {}
    for issue in issues:
        category = classify_protocol_issue(issue)
        item = categories.setdefault(category, {"count": 0, "examples": []})
        item["count"] += 1
        if len(item["examples"]) < 5:
            item["examples"].append(issue)
    return {
        "category_count": len(categories),
        "categories": dict(sorted(categories.items())),
        "top_categories": [
            {"category": category, "count": item["count"]}
            for category, item in sorted(categories.items(), key=lambda pair: (-pair[1]["count"], pair[0]))
        ],
    }


def meta_eval_gate(status_report: dict[str, Any]) -> dict[str, Any]:
    meta = status_report.get("metareval", {})
    issues: list[str] = []
    report_count = int(meta.get("report_count") or 0)
    passed_count = int(meta.get("passed_count") or 0)
    require(report_count > 0, "meta-eval report_count is zero", issues)
    require(passed_count == report_count and report_count > 0, f"meta-eval passed {passed_count}/{report_count}", issues)
    for field in ("missing_reference", "missing_original", "missing_bad_solution", "missing_repeat"):
        value = meta.get(field)
        if value is not None:
            require(int(value) == 0, f"meta-eval {field}={value}", issues)
    open_issues = meta.get("open_issues") or []
    require(not open_issues, f"meta-eval has open issues: {open_issues[:3]}", issues)
    return gate(
        "meta_eval",
        passed=not issues,
        issues=issues,
        summary={
            "report_count": report_count,
            "passed_count": passed_count,
            "reference_passed": meta.get("reference_passed"),
            "original_failed": meta.get("original_failed"),
            "bad_solutions_failed": meta.get("bad_solutions_failed"),
            "reproducible": meta.get("reproducible"),
        },
    )


def negative_control_gate(status_report: dict[str, Any]) -> dict[str, Any]:
    negative = status_report.get("negative_controls", {})
    issues: list[str] = []
    total = int(negative.get("total_tasks") or 0)
    valid = int(negative.get("valid_bad_reports") or 0)
    invalid = int(negative.get("invalid_bad_reports") or 0)
    missing = int(negative.get("missing_bad_reports") or 0)
    expected_check_covered = int(negative.get("expected_check_covered") or 0)
    require(total > 0, "negative-control plan has zero tasks", issues)
    require(valid == total and total > 0, f"valid bad-solution reports {valid}/{total}", issues)
    require(invalid == 0, f"invalid bad-solution reports={invalid}", issues)
    require(missing == 0, f"missing bad-solution reports={missing}", issues)
    require(
        expected_check_covered >= total and total > 0,
        f"expected failed checks covered {expected_check_covered}/{total}",
        issues,
    )
    return gate(
        "negative_controls",
        passed=not issues,
        issues=issues,
        summary={
            "total_tasks": total,
            "valid_bad_reports": valid,
            "invalid_bad_reports": invalid,
            "missing_bad_reports": missing,
            "expected_check_covered": expected_check_covered,
        },
    )


def deai_contrast_gate(status_report: dict[str, Any]) -> dict[str, Any]:
    deai = status_report.get("de_ai_contrast", {})
    issues: list[str] = []
    require(bool(deai.get("passed")), "de-AI contrast audit is not passed", issues)
    require(bool(deai.get("publishable")), "de-AI contrast set is not publishable", issues)
    for issue in deai.get("issues", []) or []:
        issues.append(str(issue))
    return gate(
        "de_ai_contrast_gate",
        passed=not issues,
        issues=issues,
        summary={
            "anchor_count": deai.get("anchor_count"),
            "model_family_count": deai.get("model_family_count"),
            "website_type_count": deai.get("website_type_count"),
            "fingerprint_rich_records": deai.get("fingerprint_rich_records"),
        },
    )


def denominator_consistency_gate(status_report: dict[str, Any]) -> dict[str, Any]:
    audit = audit_denominator_consistency_from_status(status_report)
    issues = [str(issue) for issue in audit.get("issues", [])]
    summary = dict(audit.get("summary", {}))
    summary["warning_count"] = audit.get("warning_count")
    return gate(
        "denominator_consistency_gate",
        passed=not issues,
        issues=issues,
        summary=summary,
    )


def b_lm_gate(
    status_report: dict[str, Any],
    meta_gate: dict[str, Any],
    negative_gate: dict[str, Any],
    denominator_gate: dict[str, Any],
) -> dict[str, Any]:
    change = status_report.get("regimes", {}).get("change", {})
    schema = status_report.get("schema_gate", {})
    capture = status_report.get("capture_runtime", {})
    capability = status_report.get("capability_split", {})
    signals = status_report.get("signal_layers", {})
    authoring = status_report.get("authoring_data_audit", {})
    strict_authoring = status_report.get("strict_authoring_data_audit", {})
    strict_freeze = status_report.get("strict_freeze_authoring_audit", {})
    lock = status_report.get("authoring_lock", {})
    issues: list[str] = []
    formal_materialized = int(change.get("materialized_formal_task_count") or 0)
    capability_total = int(capability.get("total") or 0)
    signal_total = int(signals.get("total") or 0)
    require(bool(schema.get("passed")), "schema gate is not passed", issues)
    require(bool(capture.get("passed")), "shared capture runtime gate is not passed", issues)
    require(bool(capability.get("passed_all")), "change capability split is not all-pass", issues)
    require(
        capability_total == formal_materialized and formal_materialized > 0,
        f"change capability records {capability_total}/{formal_materialized} formal materialized tasks",
        issues,
    )
    require(bool(signals.get("passed_all")), "signal-layer audit is not all-pass", issues)
    require(
        signal_total == formal_materialized and formal_materialized > 0,
        f"signal-layer records {signal_total}/{formal_materialized} formal materialized tasks",
        issues,
    )
    require(bool(authoring.get("passed")), "authoring data audit is not passed", issues)
    require(bool(strict_authoring.get("passed")), "strict review-artifact audit is not passed", issues)
    require(bool(strict_freeze.get("available")), "strict freeze audit has not been generated", issues)
    require(bool(strict_freeze.get("passed")), "strict freeze/no-bulk audit is not passed", issues)
    require(bool(lock.get("passed")), "authoring lock audit is not passed", issues)
    require(bool(meta_gate.get("passed")), "meta-eval gate is not passed", issues)
    require(bool(negative_gate.get("passed")), "negative-control gate is not passed", issues)
    require(bool(denominator_gate.get("passed")), "denominator consistency gate is not passed", issues)
    for issue in strict_freeze.get("issues", []) or []:
        issues.append(f"strict freeze audit: {issue}")
    return gate(
        "change_lm_publish_gate",
        passed=not issues,
        issues=issues,
        summary={
            "formal_materialized_tasks": formal_materialized,
            "ledger_accepted": change.get("accepted"),
            "unmaterialized_accepted": change.get("unmaterialized_accepted_count"),
            "formal_tasks": authoring.get("task_count"),
            "capability_records": capability_total,
            "signal_records": signal_total,
            "strict_freeze_available": strict_freeze.get("available"),
            "strict_freeze_passed": strict_freeze.get("passed"),
            "strict_clean_tasks": strict_freeze.get("strict_clean_task_count"),
            "strict_blocked_tasks": strict_freeze.get("strict_blocked_task_count"),
            "strict_freeze_failures": strict_freeze.get("freeze_audit_issue_count"),
            "strict_no_bulk_failures": strict_freeze.get("no_bulk_declaration_issue_count"),
            "denominator_consistency_passed": denominator_gate.get("passed"),
            "primary_metric_policy": "WCS uses L-hard/L-metric only; L-soft is not in primary pass/fail.",
        },
    )


def b_mm_gate(status_report: dict[str, Any], denominator_gate: dict[str, Any]) -> dict[str, Any]:
    change = status_report.get("regimes", {}).get("change", {})
    capability = status_report.get("capability_split", {})
    signals = status_report.get("signal_layers", {})
    mm_spec = status_report.get("mm_judge_spec", {})
    perturbations = status_report.get("mm_judge_perturbations", {})
    mm = status_report.get("mm_block", {})
    isolation = status_report.get("model_isolation", {})
    issues: list[str] = []
    formal_materialized = int(change.get("materialized_formal_task_count") or 0)
    capability_total = int(capability.get("total") or 0)
    signal_total = int(signals.get("total") or 0)
    require(bool(capability.get("passed_all")), "change capability split is not all-pass", issues)
    require(
        capability_total == formal_materialized and formal_materialized > 0,
        f"change capability records {capability_total}/{formal_materialized} formal materialized tasks",
        issues,
    )
    require(bool(signals.get("passed_all")), "signal-layer audit is not all-pass", issues)
    require(
        signal_total == formal_materialized and formal_materialized > 0,
        f"signal-layer records {signal_total}/{formal_materialized} formal materialized tasks",
        issues,
    )
    require(bool(mm_spec.get("passed")), "change MM judge spec audit is not passed", issues)
    require(bool(mm_spec.get("publishable")), "change MM judge spec is not publishable", issues)
    require(bool(perturbations.get("passed")), "MM judge perturbation audit is not passed", issues)
    require(bool(perturbations.get("publishable")), "MM judge perturbation controls are not publishable", issues)
    require(bool(mm.get("change_sample_gate_passed")), "change MM sample gate is not passed", issues)
    require(bool(mm.get("change_human_label_freeze_available")), "change MM human-label freeze audit is missing", issues)
    require(bool(mm.get("change_human_label_freeze_passed")), "change MM human-label freeze audit is not passed", issues)
    require(bool(mm.get("change_publish_gate_passed")), "change MM publish gate is not passed", issues)
    require(bool(mm.get("change_publishable")), "change MM calibration is not publishable", issues)
    require(bool(isolation.get("passed")), "model isolation audit is not passed", issues)
    require(bool(isolation.get("publishable")), "model isolation is not publishable", issues)
    require(bool(denominator_gate.get("passed")), "denominator consistency gate is not passed", issues)
    for issue in mm.get("change_publish_gate_issues", []) or []:
        issues.append(f"MM publish gate: {issue}")
    for issue in mm.get("change_human_label_freeze_issues", []) or []:
        issues.append(f"MM human-label freeze: {issue}")
    for issue in mm_spec.get("issues", []) or []:
        issues.append(f"MM judge spec: {issue}")
    for issue in perturbations.get("issues", []) or []:
        issues.append(f"MM perturbation: {issue}")
    for issue in isolation.get("issues", []) or []:
        issues.append(f"model isolation: {issue}")
    return gate(
        "change_mm_publish_gate",
        passed=not issues,
        issues=issues,
        summary={
            "formal_materialized_tasks": formal_materialized,
            "ledger_accepted": change.get("accepted"),
            "unmaterialized_accepted": change.get("unmaterialized_accepted_count"),
            "capability_records": capability_total,
            "signal_records": signal_total,
            "available_checkpoints": mm.get("change_available_checkpoints"),
            "sample_items": mm.get("change_calibration_items"),
            "human_labeled_items": mm.get("change_human_labeled_items"),
            "human_label_freeze_available": mm.get("change_human_label_freeze_available"),
            "human_label_freeze_passed": mm.get("change_human_label_freeze_passed"),
            "human_label_freeze_records": mm.get("change_human_label_freeze_records"),
            "human_label_freeze_issues": mm.get("change_human_label_freeze_issue_count"),
            "synthetic_smoke_passed": mm.get("change_synthetic_gate_passed"),
            "synthetic_smoke_publishable": mm.get("change_synthetic_publishable"),
            "mm_judge_spec_passed": mm_spec.get("passed"),
            "mm_judge_spec_checkpoints": mm_spec.get("checkpoint_count"),
            "mm_judge_spec_warnings": mm_spec.get("warning_count"),
            "mm_perturbation_passed": perturbations.get("passed"),
            "mm_perturbation_groups": perturbations.get("group_count"),
            "mm_perturbation_label_flips": perturbations.get("label_flip_group_count"),
            "model_isolation_passed": isolation.get("passed"),
            "model_isolation_roles": isolation.get("role_count"),
            "denominator_consistency_passed": denominator_gate.get("passed"),
        },
    )


def construction_gate(status_report: dict[str, Any], denominator_gate: dict[str, Any]) -> dict[str, Any]:
    construction = status_report.get("regimes", {}).get("construction", {})
    artifacts = status_report.get("construction_artifacts", {})
    capability = status_report.get("construction_capability_split", {})
    issues: list[str] = []
    accepted = int(construction.get("accepted") or 0)
    strict_total = int(artifacts.get("strict_precheck_count") or 0)
    strict_passed = int(artifacts.get("strict_precheck_passed") or 0)
    structural_total = int(artifacts.get("structural_precheck_count") or 0)
    structural_passed = int(artifacts.get("structural_precheck_passed") or 0)
    schema_total = int(artifacts.get("task_package_schema_precheck_total") or 0)
    schema_passed = int(artifacts.get("task_package_schema_precheck_passed") or 0)
    capability_total = int(capability.get("total") or 0)
    require(accepted > 0, "construction regime has no accepted tasks", issues)
    require(
        capability_total == accepted and accepted > 0,
        f"construction capability records {capability_total}/{accepted}",
        issues,
    )
    require(bool(capability.get("passed_all")), "construction capability split is not all-pass", issues)
    require(strict_total > 0 and strict_passed == strict_total, f"strict construction prechecks {strict_passed}/{strict_total}", issues)
    require(
        structural_total > 0 and structural_passed == structural_total,
        f"structural construction prechecks {structural_passed}/{structural_total}",
        issues,
    )
    require(schema_total > 0 and schema_passed == schema_total, f"construction schema prechecks {schema_passed}/{schema_total}", issues)
    require(bool(denominator_gate.get("passed")), "denominator consistency gate is not passed", issues)
    for issue in artifacts.get("strict_issues", [])[:5]:
        issues.append(f"construction strict issue: {issue}")
    return gate(
        "construction_lm_publish_gate",
        passed=not issues,
        issues=issues,
        summary={
            "accepted": accepted,
            "target_total": construction.get("target_total"),
            "strict_precheck_passed": strict_passed,
            "strict_precheck_count": strict_total,
            "structural_precheck_passed": structural_passed,
            "structural_precheck_count": structural_total,
            "schema_precheck_passed": schema_passed,
            "schema_precheck_count": schema_total,
            "capability_records": capability_total,
            "capability_passed": capability.get("passed"),
            "capability_failed": capability.get("failed"),
            "denominator_consistency_passed": denominator_gate.get("passed"),
        },
    )


def construction_mm_gate(
    status_report: dict[str, Any],
    construction_lm_gate: dict[str, Any],
    denominator_gate: dict[str, Any],
) -> dict[str, Any]:
    construction_capability = status_report.get("construction_capability_split", {})
    construction_mm_spec = status_report.get("construction_mm_judge_spec", {})
    perturbations = status_report.get("mm_judge_perturbations", {})
    mm = status_report.get("mm_block", {})
    isolation = status_report.get("model_isolation", {})
    issues: list[str] = []
    require(bool(construction_lm_gate.get("passed")), "construction LM publish gate is not passed", issues)
    require(bool(construction_capability.get("passed_all")), "construction capability split is not all-pass", issues)
    require(int(construction_capability.get("mm_checkpoint_total") or 0) > 0, "construction MM checkpoints are zero", issues)
    require(bool(construction_mm_spec.get("passed")), "construction MM judge spec audit is not passed", issues)
    require(bool(construction_mm_spec.get("publishable")), "construction MM judge spec is not publishable", issues)
    require(bool(perturbations.get("passed")), "MM judge perturbation audit is not passed", issues)
    require(bool(perturbations.get("publishable")), "MM judge perturbation controls are not publishable", issues)
    require(int(mm.get("construction_calibration_items") or 0) >= 150, "construction MM calibration sample has fewer than 150 items", issues)
    require(bool(isolation.get("passed")), "model isolation audit is not passed", issues)
    require(bool(isolation.get("publishable")), "model isolation is not publishable", issues)
    require(bool(denominator_gate.get("passed")), "denominator consistency gate is not passed", issues)
    for issue in isolation.get("issues", []) or []:
        issues.append(f"model isolation: {issue}")
    for issue in construction_mm_spec.get("issues", []) or []:
        issues.append(f"MM judge spec: {issue}")
    for issue in perturbations.get("issues", []) or []:
        issues.append(f"MM perturbation: {issue}")
    return gate(
        "construction_mm_publish_gate",
        passed=not issues,
        issues=issues,
        summary={
            "construction_mm_checkpoints": construction_capability.get("mm_checkpoint_total"),
            "mm_judge_spec_passed": construction_mm_spec.get("passed"),
            "mm_judge_spec_checkpoints": construction_mm_spec.get("checkpoint_count"),
            "construction_calibration_items": mm.get("construction_calibration_items"),
            "mm_perturbation_passed": perturbations.get("passed"),
            "mm_perturbation_groups": perturbations.get("group_count"),
            "model_isolation_passed": isolation.get("passed"),
            "model_isolation_roles": isolation.get("role_count"),
            "denominator_consistency_passed": denominator_gate.get("passed"),
        },
    )


def audit_eval_protocol_from_status(status_report: dict[str, Any]) -> dict[str, Any]:
    denominator = denominator_consistency_gate(status_report)
    meta = meta_eval_gate(status_report)
    negative = negative_control_gate(status_report)
    deai_contrast = deai_contrast_gate(status_report)
    change_lm = b_lm_gate(status_report, meta, negative, denominator)
    change_mm = b_mm_gate(status_report, denominator)
    construction_lm = construction_gate(status_report, denominator)
    construction_mm = construction_mm_gate(status_report, construction_lm, denominator)
    phase0 = gate(
        "phase0_change_dual_leaderboard",
        passed=bool(change_lm["passed"] and change_mm["passed"] and deai_contrast["passed"]),
        issues=[
            issue
            for child in (change_lm, change_mm, deai_contrast)
            if not child["passed"]
            for issue in [f"{child['name']} is not passed"]
        ],
        summary={
            "change_lm_publishable": change_lm["passed"],
            "change_mm_publishable": change_mm["passed"],
            "de_ai_contrast_ready": deai_contrast["passed"],
        },
    )
    phase3 = gate(
        "phase3_four_view_leaderboard",
        passed=bool(phase0["passed"] and construction_lm["passed"]),
        issues=[
            issue
            for child in (phase0, construction_lm, construction_mm)
            if not child["passed"]
            for issue in [f"{child['name']} is not passed"]
        ],
        summary={
            "change_lm_publishable": change_lm["passed"],
            "change_mm_publishable": change_mm["passed"],
            "construction_lm_publishable": construction_lm["passed"],
            "construction_mm_publishable": construction_mm["passed"],
        },
    )
    gates = {
        "denominator_consistency": denominator,
        "meta_eval": meta,
        "negative_controls": negative,
        "de_ai_contrast": deai_contrast,
        "change_lm": change_lm,
        "change_mm": change_mm,
        "construction_lm": construction_lm,
        "construction_mm": construction_mm,
        "phase0": phase0,
        "phase3": phase3,
    }
    issues = [
        f"{name}: {issue}"
        for name, gate_item in gates.items()
        for issue in gate_item.get("issues", [])
    ]
    return {
        "schema_version": "2026-06-18",
        "artifact_type": ARTIFACT_TYPE,
        "formal_task_record": False,
        "object": "Website Continuity",
        "gates": gates,
        "publishable_views": {
            "change_lm": change_lm["passed"],
            "change_mm": change_mm["passed"],
            "construction_lm": construction_lm["passed"],
            "construction_mm": construction_mm["passed"],
        },
        "phase0_ready": phase0["passed"],
        "phase3_ready": phase3["passed"],
        "issue_count": sum(gate_item["issue_count"] for gate_item in gates.values()),
        "issues": issues,
        "blocker_summary": blocker_summary(issues),
        "notes": [
            "This audit reads existing evidence reports; it does not run model evaluation or generate formal tasks.",
            "MM synthetic smoke reports never unlock a publishable MM leaderboard.",
            "Primary LM publish gates require L-hard/L-metric signal integrity and meta-eval/negative-control evidence.",
        ],
    }


def audit_eval_protocol(coverage_report_path: Path, output_path: Path | None = None) -> dict[str, Any]:
    status = load_json_if_exists(coverage_report_path)
    if not status:
        report = {
            "schema_version": "2026-06-18",
            "artifact_type": ARTIFACT_TYPE,
            "formal_task_record": False,
            "coverage_report_path": str(coverage_report_path),
            "phase0_ready": False,
            "phase3_ready": False,
            "issue_count": 1,
            "issues": [f"coverage report missing: {coverage_report_path}"],
            "gates": {},
            "publishable_views": {},
        }
    else:
        report = audit_eval_protocol_from_status(status)
        report["coverage_report_path"] = str(coverage_report_path)
    if output_path is not None:
        write_json(output_path, report)
    return report


def add_protocol_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--coverage-report", type=Path, default=DEFAULT_COVERAGE_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)


def run_protocol_from_args(args: argparse.Namespace) -> None:
    try:
        assert_not_under_formal_task_root(args.output, purpose="Eval protocol audit")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    ensure_dir(args.output.parent)
    report = audit_eval_protocol(args.coverage_report, output_path=args.output)
    views = report.get("publishable_views", {})
    print(
        "eval protocol audit: "
        f"phase0={report.get('phase0_ready')} phase3={report.get('phase3_ready')} "
        f"change_lm={views.get('change_lm')} change_mm={views.get('change_mm')} "
        f"construction_lm={views.get('construction_lm')} construction_mm={views.get('construction_mm')} "
        f"issues={report.get('issue_count')}"
    )
