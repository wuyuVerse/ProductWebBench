from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..core.config import DEFAULT_OUTPUT_ROOT
from ..core.formal_data_guard import assert_not_under_formal_task_root
from ..core.io_utils import ensure_dir, write_json


ARTIFACT_TYPE = "productwebbench_0618_readiness_audit"
DEFAULT_COVERAGE_REPORT = DEFAULT_OUTPUT_ROOT / "authoring_ledger" / "coverage_gaps.json"
DEFAULT_OUTPUT = DEFAULT_OUTPUT_ROOT / "eval_protocol" / "0618_readiness_audit.json"


def load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def check(key: str, title: str, *, passed: bool, evidence: dict[str, Any], issues: list[str]) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "passed": bool(passed),
        "issue_count": len(issues),
        "issues": issues,
        "evidence": evidence,
    }


def audit_invariants(status: dict[str, Any]) -> dict[str, Any]:
    schema = status.get("schema_gate", {})
    signals = status.get("signal_layers", {})
    construction_artifacts = status.get("construction_artifacts", {})
    strict_freeze = status.get("strict_freeze_authoring_audit", {})
    capability = status.get("capability_split", {})
    meta = status.get("metareval", {})
    negative = status.get("negative_controls", {})
    mm = status.get("mm_block", {})
    model_isolation = status.get("model_isolation", {})
    perturbations = status.get("mm_judge_perturbations", {})
    eval_protocol = status.get("eval_protocol", {})
    construction = status.get("regimes", {}).get("construction", {})

    primary_mllm = schema.get("primary_mllm_metrics") or []
    r1_issues: list[str] = []
    if primary_mllm:
        r1_issues.append(f"primary metrics use MLLM: {primary_mllm}")
    if not schema.get("required_metrics_present"):
        r1_issues.append("required metric schema entries are missing")
    if not signals.get("passed_all"):
        r1_issues.append("signal-layer audit is not all-pass")
    r1 = check(
        "R1",
        "Primary metrics are pure L-hard/L-metric",
        passed=not r1_issues,
        evidence={
            "primary_metric_keys": schema.get("primary_metric_keys"),
            "primary_mllm_metrics": primary_mllm,
            "signal_layer_passed_all": signals.get("passed_all"),
            "signal_records": signals.get("total"),
        },
        issues=r1_issues,
    )

    structural_total = as_int(construction_artifacts.get("structural_precheck_count"))
    structural_passed = as_int(construction_artifacts.get("structural_precheck_passed"))
    saved_precheck_issues = as_int(construction_artifacts.get("saved_precheck_issue_count"))
    r2_issues: list[str] = []
    if structural_total == 0:
        r2_issues.append("no construction structural precheck artifacts exist")
    if structural_total and structural_passed != structural_total:
        r2_issues.append(f"structural construction prechecks {structural_passed}/{structural_total}")
    if saved_precheck_issues:
        r2_issues.append(f"saved construction precheck artifacts have {saved_precheck_issues} issues")
    r2 = check(
        "R2",
        "Construction actor input is content-free; intrinsic assets are policy-gated",
        passed=not r2_issues,
        evidence={
            "structural_precheck_passed": structural_passed,
            "structural_precheck_count": structural_total,
            "saved_precheck_issue_count": saved_precheck_issues,
            "accepted_construction_tasks": construction.get("accepted"),
            "note": "Strict acceptance is checked under R3/R5; R2 is the actor-visible structural/input-policy gate.",
        },
        issues=r2_issues,
    )

    strict_clean = as_int(strict_freeze.get("strict_clean_task_count"))
    strict_blocked = as_int(strict_freeze.get("strict_blocked_task_count"))
    strict_precheck_total = as_int(construction_artifacts.get("strict_precheck_count"))
    strict_precheck_passed = as_int(construction_artifacts.get("strict_precheck_passed"))
    r3_issues: list[str] = []
    if not strict_freeze.get("available"):
        r3_issues.append("change strict freeze audit is missing")
    if not strict_freeze.get("passed"):
        r3_issues.append(f"change strict freeze/no-bulk audit is not passed; strict_clean={strict_clean}, blocked={strict_blocked}")
    if strict_precheck_total == 0:
        r3_issues.append("construction strict precheck has not been run")
    elif strict_precheck_passed != strict_precheck_total:
        r3_issues.append(f"construction strict prechecks {strict_precheck_passed}/{strict_precheck_total}")
    r3 = check(
        "R3",
        "Every frozen task passes de-leak, leak-audit, spec completeness, and regression sanity",
        passed=not r3_issues,
        evidence={
            "change_package_gate_status_counts": capability.get("gate_status_counts"),
            "change_strict_freeze_passed": strict_freeze.get("passed"),
            "change_strict_clean_tasks": strict_clean,
            "change_strict_blocked_tasks": strict_blocked,
            "construction_strict_precheck_passed": strict_precheck_passed,
            "construction_strict_precheck_count": strict_precheck_total,
        },
        issues=r3_issues,
    )

    r4_issues: list[str] = []
    if not mm.get("change_human_label_freeze_passed"):
        r4_issues.append("change MM human-label freeze is not passed")
    if not mm.get("change_publishable"):
        r4_issues.append("change MM calibration is not publishable")
    if as_int(mm.get("construction_calibration_items")) < 150:
        r4_issues.append("construction MM calibration sample has fewer than 150 items")
    if not model_isolation.get("passed"):
        r4_issues.append("model isolation audit is not passed")
    if not perturbations.get("passed"):
        r4_issues.append("MM judge perturbation audit is not passed")
    r4 = check(
        "R4",
        "MM judge passes JA/kappa calibration and spec/judge/actor model isolation",
        passed=not r4_issues,
        evidence={
            "change_human_label_freeze_passed": mm.get("change_human_label_freeze_passed"),
            "change_publishable": mm.get("change_publishable"),
            "construction_calibration_items": mm.get("construction_calibration_items"),
            "model_isolation_passed": model_isolation.get("passed"),
            "model_isolation_roles": model_isolation.get("role_count"),
            "mm_perturbation_passed": perturbations.get("passed"),
        },
        issues=r4_issues,
    )

    r5_issues: list[str] = []
    report_count = as_int(meta.get("report_count"))
    if report_count == 0:
        r5_issues.append("no meta-eval reports exist")
    if as_int(meta.get("passed_count")) != report_count:
        r5_issues.append(f"meta-eval passed {meta.get('passed_count')}/{report_count}")
    if as_int(meta.get("missing_reference")) or as_int(meta.get("missing_original")) or as_int(meta.get("missing_bad_solution")):
        r5_issues.append("meta-eval has missing reference/original/bad-solution evidence")
    if as_int(negative.get("valid_bad_reports")) != as_int(negative.get("total_tasks")):
        r5_issues.append(f"negative controls valid {negative.get('valid_bad_reports')}/{negative.get('total_tasks')}")
    r5 = check(
        "R5",
        "Meta-evaluation precedes leaderboard use: reference passes, original and bad solutions fail",
        passed=not r5_issues,
        evidence={
            "meta_eval_report_count": report_count,
            "meta_eval_passed_count": meta.get("passed_count"),
            "reference_passed": meta.get("reference_passed"),
            "original_failed": meta.get("original_failed"),
            "bad_solutions_failed": meta.get("bad_solutions_failed"),
            "reproducible": meta.get("reproducible"),
            "valid_bad_reports": negative.get("valid_bad_reports"),
            "negative_control_total_tasks": negative.get("total_tasks"),
            "publishable_views": eval_protocol.get("publishable_views"),
        },
        issues=r5_issues,
    )

    invariants = {item["key"]: item for item in (r1, r2, r3, r4, r5)}
    return invariants


def audit_phases(status: dict[str, Any], invariants: dict[str, dict[str, Any]]) -> dict[str, Any]:
    change = status.get("regimes", {}).get("change", {})
    construction = status.get("regimes", {}).get("construction", {})
    eval_protocol = status.get("eval_protocol", {})
    deai = status.get("de_ai_contrast", {})
    mm = status.get("mm_block", {})
    construction_artifacts = status.get("construction_artifacts", {})
    capability = status.get("construction_capability_split", {})

    phase0_issues: list[str] = []
    if not eval_protocol.get("phase0_ready"):
        phase0_issues.append("eval protocol phase0 gate is not passed")
    if not deai.get("passed"):
        phase0_issues.append("de-AI contrast audit is not passed")
    if not invariants["R1"].get("passed") or not invariants["R4"].get("passed") or not invariants["R5"].get("passed"):
        phase0_issues.append("R1/R4/R5 are not all passed")
    phase0 = check(
        "Phase0",
        "B MM-block and de-AI contrast are ready; B can publish LM+MM views",
        passed=not phase0_issues,
        evidence={
            "change_ledger_accepted": change.get("accepted"),
            "change_formal_materialized": change.get("materialized_formal_task_count"),
            "phase0_ready": eval_protocol.get("phase0_ready"),
            "change_lm_publishable": eval_protocol.get("publishable_views", {}).get("change_lm"),
            "change_mm_publishable": eval_protocol.get("publishable_views", {}).get("change_mm"),
            "de_ai_contrast_passed": deai.get("passed"),
        },
        issues=phase0_issues,
    )

    construction_accepted = as_int(construction.get("accepted"))
    phase1_issues: list[str] = []
    if construction_accepted < 50:
        phase1_issues.append(f"construction pilot accepted tasks {construction_accepted} < 50")
    if construction_accepted > 80:
        phase1_issues.append(f"construction pilot accepted tasks {construction_accepted} > 80; this is no longer pilot scale")
    if as_int(construction_artifacts.get("strict_precheck_passed")) < construction_accepted or construction_accepted == 0:
        phase1_issues.append("construction pilot strict gates are not all passed")
    if as_int(mm.get("construction_calibration_items")) < 150:
        phase1_issues.append("construction JA calibration subset has fewer than 150 items")
    phase1 = check(
        "Phase1",
        "A pilot 50-80 tasks runs four gates and establishes JA calibration subset",
        passed=not phase1_issues,
        evidence={
            "construction_accepted": construction_accepted,
            "construction_strict_precheck_passed": construction_artifacts.get("strict_precheck_passed"),
            "construction_strict_precheck_count": construction_artifacts.get("strict_precheck_count"),
            "construction_calibration_items": mm.get("construction_calibration_items"),
        },
        issues=phase1_issues,
    )

    phase2_issues: list[str] = []
    change_formal = as_int(change.get("materialized_formal_task_count"))
    if change_formal == 0 and change.get("materialized_formal_task_count") is None:
        change_formal = as_int(change.get("accepted"))
    if change_formal < 400:
        phase2_issues.append(f"change formal materialized tasks {change_formal}/400")
    if construction_accepted < 400:
        phase2_issues.append(f"construction accepted {construction_accepted}/400")
    for key in ("R1", "R2", "R3", "R4", "R5"):
        if not invariants[key].get("passed"):
            phase2_issues.append(f"{key} is not passed")
    phase2 = check(
        "Phase2",
        "A and B each reach 400 tasks; four gates and meta-eval are green",
        passed=not phase2_issues,
        evidence={
            "change_ledger_accepted": change.get("accepted"),
            "change_formal_materialized": change_formal,
            "change_unmaterialized_accepted": change.get("unmaterialized_accepted_count"),
            "construction_accepted": construction_accepted,
            "construction_capability_records": capability.get("total"),
            "invariant_passed": {key: value.get("passed") for key, value in invariants.items()},
        },
        issues=phase2_issues,
    )

    phase3_issues: list[str] = []
    if not eval_protocol.get("phase3_ready"):
        phase3_issues.append("eval protocol phase3 gate is not passed")
    publishable = eval_protocol.get("publishable_views", {})
    for view in ("change_lm", "change_mm", "construction_lm", "construction_mm"):
        if not publishable.get(view):
            phase3_issues.append(f"{view} view is not publishable")
    phase3 = check(
        "Phase3",
        "A/B x LM/MM four leaderboard views and unified metric family can be generated",
        passed=not phase3_issues,
        evidence={
            "phase3_ready": eval_protocol.get("phase3_ready"),
            "publishable_views": publishable,
        },
        issues=phase3_issues,
    )
    return {item["key"]: item for item in (phase0, phase1, phase2, phase3)}


def audit_readiness_from_status(status: dict[str, Any]) -> dict[str, Any]:
    invariants = audit_invariants(status)
    phases = audit_phases(status, invariants)
    all_items = list(invariants.values()) + list(phases.values())
    return {
        "schema_version": "2026-06-19",
        "artifact_type": ARTIFACT_TYPE,
        "formal_task_record": False,
        "object": "Website Continuity",
        "source_document": "docs/0618_multimodal_capability_traction_direction.md",
        "passed": all(item["passed"] for item in all_items),
        "invariants": invariants,
        "phases": phases,
        "passed_invariants": sum(1 for item in invariants.values() if item.get("passed")),
        "total_invariants": len(invariants),
        "passed_phases": sum(1 for item in phases.values() if item.get("passed")),
        "total_phases": len(phases),
        "issue_count": sum(item["issue_count"] for item in all_items),
        "issues": [f"{item['key']}: {issue}" for item in all_items for issue in item.get("issues", [])],
        "notes": [
            "This artifact is a requirement-to-evidence matrix for the 0618 traction document.",
            "It reads existing status/audit artifacts only; it does not create formal tasks or run model evaluations.",
        ],
    }


def audit_readiness(coverage_report_path: Path, output_path: Path | None = None) -> dict[str, Any]:
    status = load_json_if_exists(coverage_report_path)
    if not status:
        report = {
            "schema_version": "2026-06-19",
            "artifact_type": ARTIFACT_TYPE,
            "formal_task_record": False,
            "source_document": "docs/0618_multimodal_capability_traction_direction.md",
            "passed": False,
            "coverage_report_path": str(coverage_report_path),
            "invariants": {},
            "phases": {},
            "passed_invariants": 0,
            "total_invariants": 5,
            "passed_phases": 0,
            "total_phases": 4,
            "issue_count": 1,
            "issues": [f"coverage report missing: {coverage_report_path}"],
        }
    else:
        report = audit_readiness_from_status(status)
        report["coverage_report_path"] = str(coverage_report_path)
    if output_path is not None:
        write_json(output_path, report)
    return report


def add_readiness_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--coverage-report", type=Path, default=DEFAULT_COVERAGE_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)


def run_readiness_from_args(args: argparse.Namespace) -> None:
    try:
        assert_not_under_formal_task_root(args.output, purpose="0618 readiness audit")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    ensure_dir(args.output.parent)
    report = audit_readiness(args.coverage_report, output_path=args.output)
    print(
        "0618 readiness audit: "
        f"passed={report.get('passed')} "
        f"invariants={report.get('passed_invariants')}/{report.get('total_invariants')} "
        f"phases={report.get('passed_phases')}/{report.get('total_phases')} "
        f"issues={report.get('issue_count')} output={args.output}"
    )
