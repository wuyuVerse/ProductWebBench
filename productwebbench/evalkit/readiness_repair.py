from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.config import DEFAULT_OUTPUT_ROOT
from ..core.formal_data_guard import assert_not_under_formal_task_root
from ..core.io_utils import ensure_dir, write_json


ARTIFACT_TYPE = "sitecontinuum_0618_readiness_repair_plan"
DEFAULT_COVERAGE_REPORT = DEFAULT_OUTPUT_ROOT / "coverage" / "status.json"
DEFAULT_OUTPUT = DEFAULT_OUTPUT_ROOT / "eval_protocol" / "0618_readiness_repair_plan.json"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def stable_json_sha(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def readiness_repair_fingerprint(status: dict[str, Any]) -> str:
    return stable_json_sha(
        {
            "readiness_0618": status.get("readiness_0618") or {},
            "eval_protocol": status.get("eval_protocol") or {},
            "strict_freeze_authoring_audit": status.get("strict_freeze_authoring_audit") or {},
            "duplicate_repo_repair_plan": status.get("duplicate_repo_repair_plan") or {},
            "strict_authoring_repair_plan": status.get("strict_authoring_repair_plan") or {},
            "mm_protocol_repair_plan": status.get("mm_protocol_repair_plan") or {},
            "construction_repair_plan": status.get("construction_repair_plan") or {},
            "model_isolation": status.get("model_isolation") or {},
            "de_ai_contrast": status.get("de_ai_contrast") or {},
            "mm_judge_perturbations": status.get("mm_judge_perturbations") or {},
            "mm_block": status.get("mm_block") or {},
        }
    )


def add_item(
    items: list[dict[str, Any]],
    *,
    key: str,
    priority: int,
    invariant: str,
    title: str,
    current_state: dict[str, Any],
    required_action: str,
    evidence_to_create: list[str],
    existing_plan_refs: list[str],
    forbidden_actions: list[str] | None = None,
) -> None:
    items.append(
        {
            "priority": priority,
            "key": key,
            "invariant": invariant,
            "title": title,
            "current_state": current_state,
            "required_action": required_action,
            "evidence_to_create": evidence_to_create,
            "existing_plan_refs": existing_plan_refs,
            "forbidden_actions": forbidden_actions
            or [
                "do not generate formal benchmark rows in bulk",
                "do not mark a diagnostic artifact as publishable evidence",
                "do not hand-write passing audits without bound input file digests",
            ],
        }
    )


def build_readiness_repair_plan(*, coverage_report_path: Path, output_path: Path) -> dict[str, Any]:
    if not coverage_report_path.exists():
        raise FileNotFoundError(f"coverage/status report missing: {coverage_report_path}")
    status = load_json(coverage_report_path)
    readiness = status.get("readiness_0618") or {}
    phases = readiness.get("phases") or {}
    invariants = readiness.get("invariants") or {}
    strict_freeze = status.get("strict_freeze_authoring_audit") or {}
    mm = status.get("mm_block") or {}
    model_isolation = status.get("model_isolation") or {}
    perturbations = status.get("mm_judge_perturbations") or {}
    deai = status.get("de_ai_contrast") or {}
    construction_repair = status.get("construction_repair_plan") or {}
    mm_repair = status.get("mm_protocol_repair_plan") or {}

    items: list[dict[str, Any]] = []
    add_item(
        items,
        key="R3_change_strict_freeze_no_bulk",
        priority=10,
        invariant="R3",
        title="Backfill B/change strict one-by-one freeze evidence",
        current_state={
            "strict_clean_tasks": strict_freeze.get("strict_clean_task_count"),
            "strict_blocked_tasks": strict_freeze.get("strict_blocked_task_count"),
            "duplicate_repo_id_count": strict_freeze.get("duplicate_repo_id_count"),
            "freeze_audit_issue_count": strict_freeze.get("freeze_audit_issue_count"),
            "no_bulk_declaration_issue_count": strict_freeze.get("no_bulk_declaration_issue_count"),
        },
        required_action=(
            "Repair existing formal change slots one slot at a time: resolve duplicate repo groups, "
            "write task-specific no-bulk declarations after inspection, run single-task freeze audits, "
            "then rerun strict authoring audit."
        ),
        evidence_to_create=[
            "one inspected duplicate-repo resolution or explicit exception per affected slot",
            "slot-local no_bulk_generation_declaration.md",
            "slot-local single_task_freeze_audit.json",
            "fresh strict authoring audit with strict_clean_task_count increasing",
        ],
        existing_plan_refs=[
            status.get("duplicate_repo_repair_plan", {}).get("path"),
            status.get("strict_authoring_repair_plan", {}).get("path"),
        ],
        forbidden_actions=[
            "do not batch-write no-bulk declarations",
            "do not batch-generate freeze audits",
            "do not rewrite duplicate formal task rows without inspecting one replacement repo at a time",
        ],
    )
    add_item(
        items,
        key="R3_construction_spec_only_reference_evidence",
        priority=20,
        invariant="R3",
        title="Complete A/construction spec-only reference trajectory evidence",
        current_state={
            "strict_precheck_passed": (status.get("construction_artifacts") or {}).get("strict_precheck_passed"),
            "strict_precheck_count": (status.get("construction_artifacts") or {}).get("strict_precheck_count"),
            "draft_repair_item_count": construction_repair.get("draft_repair_item_count"),
            "repair_keys": construction_repair.get("repair_keys"),
        },
        required_action=(
            "For each construction draft, run the real spec-only actor loop and attach completed "
            "reference_actor_trace, reference_trajectory_report, reference/original/bad/repeat evidence, "
            "and construction_metareval before strict acceptance."
        ),
        evidence_to_create=[
            "spec_only_actor_run trajectory report",
            "repeat_spec_only_actor_run trajectory report",
            "empty_baseline and bad_solution trajectory reports",
            "reference_actor_trace bound to real milestone artifacts",
            "construction_metareval_report with reference pass, original fail, bad fail, repeat reproducible",
        ],
        existing_plan_refs=[construction_repair.get("path")],
    )
    add_item(
        items,
        key="R4_mm_human_calibration_and_judge_predictions",
        priority=30,
        invariant="R4",
        title="Create real B/change MM judge predictions and freeze human labels",
        current_state={
            "sample_items": mm.get("change_calibration_items"),
            "human_labeled_items": mm.get("change_human_labeled_items"),
            "publish_gate_passed": mm.get("change_publish_gate_passed"),
            "publishable": mm.get("change_publishable"),
            "blocking_sections": mm_repair.get("blocking_sections"),
        },
        required_action=(
            "Append real temperature-0 judge predictions for the sampled checkpoints, append independent "
            "human annotations, freeze labels with digest-bound inputs, and pass JA/kappa thresholds."
        ),
        evidence_to_create=[
            "change_judge_predictions.jsonl with provider/model/temperature=0 metadata",
            "change_annotation_pack/human_annotations.jsonl with independent human labels",
            "change_sample.human_labels.freeze_audit.json with publishable_candidate=true",
            "score-mm-calibration report meeting agreement and Cohen's kappa thresholds",
        ],
        existing_plan_refs=[mm_repair.get("path")],
    )
    add_item(
        items,
        key="R4_mm_perturbation_controls",
        priority=40,
        invariant="R4",
        title="Build publishable MM judge perturbation controls",
        current_state={
            "passed": perturbations.get("passed"),
            "publishable": perturbations.get("publishable"),
            "group_count": perturbations.get("group_count"),
            "record_count": perturbations.get("record_count"),
            "missing_required_perturbation_types": perturbations.get("missing_required_perturbation_types"),
        },
        required_action=(
            "Append inspected perturbation groups until there are at least 12 groups covering position, "
            "palette, length, and style with zero label flips."
        ),
        evidence_to_create=[
            "change_perturbations.jsonl",
            "mm_judge_perturbation_audit.json with publishable=true",
        ],
        existing_plan_refs=[mm_repair.get("path")],
    )
    add_item(
        items,
        key="R4_model_isolation_binding",
        priority=50,
        invariant="R4",
        title="Bind spec/judge/actor model roles to real judge audits",
        current_state={
            "passed": model_isolation.get("passed"),
            "publishable": model_isolation.get("publishable"),
            "role_count": model_isolation.get("role_count"),
            "issue_count": model_isolation.get("issue_count"),
        },
        required_action=(
            "After real judge prediction and perturbation audits exist, build a model_roles.json with disjoint "
            "spec, judge, and actor model families and rerun model isolation with digest-bound audit inputs."
        ),
        evidence_to_create=[
            "eval_protocol/model_roles.json",
            "model_isolation_audit.json with publishable=true",
        ],
        existing_plan_refs=[mm_repair.get("path")],
    )
    add_item(
        items,
        key="Phase0_deai_contrast_set",
        priority=60,
        invariant="Phase0",
        title="Build the de-AI contrast set before claiming B dual leaderboard",
        current_state={
            "passed": deai.get("passed"),
            "publishable": deai.get("publishable"),
            "anchor_count": deai.get("anchor_count"),
            "model_family_count": deai.get("model_family_count"),
            "website_type_count": deai.get("website_type_count"),
            "fingerprint_rich_records": deai.get("fingerprint_rich_records"),
        },
        required_action=(
            "Append inspected de-AI anchors one by one until the contrast set has at least 12 anchors, "
            "3 model families, 3 website types, and 8 fingerprint-rich records."
        ),
        evidence_to_create=[
            "eval_protocol/deai_contrast_set.jsonl",
            "deai_contrast_audit.json with publishable=true",
        ],
        existing_plan_refs=[mm_repair.get("path")],
    )

    failed_invariants = [key for key, item in invariants.items() if isinstance(item, dict) and not item.get("passed")]
    failed_phases = [key for key, item in phases.items() if isinstance(item, dict) and not item.get("passed")]
    report = {
        "schema_version": "2026-06-19",
        "artifact_type": ARTIFACT_TYPE,
        "formal_task_record": False,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_document": "docs/0618_multimodal_capability_traction_direction.md",
        "source_coverage_report": str(coverage_report_path),
        "source_fingerprint": readiness_repair_fingerprint(status),
        "readiness_passed": readiness.get("passed"),
        "failed_invariants": failed_invariants,
        "failed_phases": failed_phases,
        "repair_item_count": len(items),
        "repair_items": sorted(items, key=lambda item: item["priority"]),
        "global_policy": [
            "This plan is non-formal evidence orchestration only.",
            "Formal task data remains one-task-at-a-time manual authoring.",
            "Diagnostic/synthetic MM artifacts cannot unlock publishable leaderboard views.",
            "Do not call a view publishable until audit-eval-protocol marks that view true.",
        ],
    }
    write_json(output_path, report)
    return report


def add_readiness_repair_plan_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--coverage-report", type=Path, default=DEFAULT_COVERAGE_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)


def run_readiness_repair_plan_from_args(args: argparse.Namespace) -> None:
    try:
        assert_not_under_formal_task_root(args.output, purpose="0618 readiness repair plan")
        ensure_dir(args.output.parent)
        report = build_readiness_repair_plan(
            coverage_report_path=args.coverage_report,
            output_path=args.output,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(
        "0618 readiness repair plan: "
        f"failed_invariants={','.join(report['failed_invariants']) or 'none'} "
        f"failed_phases={','.join(report['failed_phases']) or 'none'} "
        f"items={report['repair_item_count']} output={args.output}"
    )
