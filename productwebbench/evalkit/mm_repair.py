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


ARTIFACT_TYPE = "mm_protocol_repair_plan"
DEFAULT_COVERAGE_REPORT = DEFAULT_OUTPUT_ROOT / "authoring_ledger" / "coverage_gaps.json"
DEFAULT_OUTPUT = DEFAULT_OUTPUT_ROOT / "mm_calibration" / "mm_protocol_repair_plan.json"


def file_sha256(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def input_file_record(path: Path) -> dict[str, str | None]:
    return {"path": str(path), "sha256": file_sha256(path)}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def stable_json_sha(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def mm_protocol_fingerprint(status: dict[str, Any]) -> str:
    return stable_json_sha(
        {
            "mm_block": status.get("mm_block") or {},
            "mm_judge_perturbations": status.get("mm_judge_perturbations") or {},
            "model_isolation": status.get("model_isolation") or {},
            "de_ai_contrast": status.get("de_ai_contrast") or {},
        }
    )


def section(
    key: str,
    *,
    passed: bool,
    current: bool | None = None,
    blocker_count: int | None = None,
    evidence: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    required_actions: list[str],
    forbidden_actions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "passed": bool(passed),
        "current": current,
        "blocker_count": blocker_count if blocker_count is not None else len(issues or []),
        "evidence": evidence or {},
        "issues": issues or [],
        "required_actions": required_actions,
        "forbidden_actions": forbidden_actions
        or [
            "do not generate formal task rows",
            "do not infer missing human labels",
            "do not batch-fill calibration artifacts",
            "do not use synthetic smoke labels as publishable MM labels",
        ],
    }


def build_mm_protocol_repair_plan(*, coverage_report_path: Path, output_path: Path) -> dict[str, Any]:
    if not coverage_report_path.exists():
        raise FileNotFoundError(f"coverage report missing: {coverage_report_path}")
    status = load_json(coverage_report_path)
    mm = status.get("mm_block") or {}
    perturb = status.get("mm_judge_perturbations") or {}
    isolation = status.get("model_isolation") or {}
    deai = status.get("de_ai_contrast") or {}
    repair_sections = [
        section(
            "de_ai_contrast",
            passed=bool(deai.get("passed")) and bool(deai.get("publishable")),
            current=deai.get("current"),
            evidence={
                "anchors": deai.get("anchor_count"),
                "model_families": deai.get("model_family_count"),
                "website_types": deai.get("website_type_count"),
                "fingerprint_rich_records": deai.get("fingerprint_rich_records"),
            },
            issues=[str(issue) for issue in deai.get("issues", [])],
            required_actions=[
                "Append one inspected de-AI contrast anchor at a time with append-deai-contrast-anchor.",
                "Cover at least 12 anchors, 3 model families, 3 website types, and 8 fingerprint-rich records.",
                "Re-run audit-deai-contrast after each appended anchor or explicitly reviewed manual session.",
            ],
        ),
        section(
            "judge_predictions",
            passed=bool(mm.get("change_judge_prediction_audit_passed")),
            current=mm.get("change_judge_prediction_audit_current"),
            evidence={
                "sample_items": mm.get("change_calibration_items"),
                "prediction_records": mm.get("change_judge_prediction_audit_records"),
                "usable_prediction_records": mm.get("change_judge_prediction_audit_usable_records"),
            },
            issues=[str(issue) for issue in mm.get("change_judge_prediction_audit_issues", [])],
            required_actions=[
                "Run the chosen real MM judge at temperature=0 for one sampled checkpoint at a time.",
                "Append each real prediction with append-mm-judge-prediction and explicit provider/model/model-family metadata.",
                "Re-run audit-mm-judge-predictions until all sampled checkpoint IDs are covered with valid discrete labels.",
            ],
        ),
        section(
            "human_annotations_and_freeze",
            passed=bool(mm.get("change_human_label_freeze_passed")) and bool(mm.get("change_publishable")),
            current=mm.get("change_human_label_freeze_current"),
            evidence={
                "annotation_pack_passed": mm.get("change_annotation_pack_passed"),
                "annotation_pack_items": mm.get("change_annotation_pack_items"),
                "human_label_records": mm.get("change_human_label_freeze_records"),
                "publishable": mm.get("change_publishable"),
            },
            issues=[str(issue) for issue in mm.get("change_human_label_freeze_issues", [])]
            + [str(issue) for issue in mm.get("change_publish_gate_issues", [])],
            required_actions=[
                "Fill judge-hidden human annotations one checkpoint at a time with append-mm-human-annotation.",
                "Do not expose judge predictions to human annotators before labels are frozen.",
                "After all sampled checkpoints have human labels and judge predictions, run freeze-mm-human-labels.",
                "Run score-mm-calibration and audit-mm-calibration to confirm agreement and Cohen's kappa thresholds.",
            ],
        ),
        section(
            "judge_perturbations",
            passed=bool(perturb.get("passed")) and bool(perturb.get("publishable")),
            current=perturb.get("current"),
            evidence={
                "groups": perturb.get("group_count"),
                "records": perturb.get("record_count"),
                "label_flip_groups": perturb.get("label_flip_group_count"),
            },
            issues=[str(issue) for issue in perturb.get("issues", [])],
            required_actions=[
                "Create one real content-preserving perturbation group at a time.",
                "Append each group with append-mm-judge-perturbation-group using the same temperature=0 judge metadata.",
                "Cover position, palette, length, and style perturbation types with at least 12 groups.",
                "Re-run audit-mm-judge-perturbations and reject any label-flip group.",
            ],
        ),
        section(
            "model_isolation",
            passed=bool(isolation.get("passed")) and bool(isolation.get("publishable")),
            current=isolation.get("current"),
            evidence={
                "role_count": isolation.get("role_count"),
                "role_kind_counts": isolation.get("role_kind_counts"),
                "families_by_kind": isolation.get("families_by_kind"),
            },
            issues=[str(issue) for issue in isolation.get("issues", [])],
            required_actions=[
                "Record real spec, judge, and actor roles with append-model-role or build-model-role-manifest.",
                "Use disjoint model families for spec, judge, and actor roles.",
                "Bind the judge role to passed temperature=0 judge prediction and perturbation audits.",
                "Re-run audit-model-isolation after prediction and perturbation audits pass.",
            ],
        ),
    ]
    blocking_sections = [item["key"] for item in repair_sections if not item["passed"]]
    report = {
        "schema_version": "2026-06-19",
        "artifact_type": ARTIFACT_TYPE,
        "formal_task_record": False,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "input_files": {"coverage_report": input_file_record(coverage_report_path)},
        "source_fingerprint": mm_protocol_fingerprint(status),
        "source_summary": {
            "change_calibration_items": mm.get("change_calibration_items"),
            "change_publishable": mm.get("change_publishable"),
            "model_isolation_publishable": isolation.get("publishable"),
            "de_ai_publishable": deai.get("publishable"),
            "perturbations_publishable": perturb.get("publishable"),
        },
        "section_count": len(repair_sections),
        "blocking_section_count": len(blocking_sections),
        "blocking_sections": blocking_sections,
        "sections": repair_sections,
        "one_at_a_time_policy": {
            "formal_task_rows_created": 0,
            "model_calls_performed": 0,
            "labels_inferred": 0,
            "allowed_append_commands": [
                "append-deai-contrast-anchor",
                "append-mm-judge-prediction",
                "append-mm-human-annotation",
                "append-mm-judge-perturbation-group",
                "append-model-role",
            ],
            "each_append_requires_real_inspection_or_real_temperature0_run": True,
        },
        "notes": [
            "This is a non-formal MM protocol repair plan.",
            "It does not call models, infer labels, create human labels, create judge predictions, or write formal tasks.",
        ],
    }
    write_json(output_path, report)
    return report


def add_mm_repair_plan_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--coverage-report", type=Path, default=DEFAULT_COVERAGE_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)


def run_mm_repair_plan_from_args(args: argparse.Namespace) -> None:
    try:
        assert_not_under_formal_task_root(args.output, purpose="MM protocol repair plan")
        ensure_dir(args.output.parent)
        report = build_mm_protocol_repair_plan(
            coverage_report_path=args.coverage_report,
            output_path=args.output,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(
        f"MM protocol repair plan: blocking_sections={report['blocking_section_count']} "
        f"output={args.output}"
    )
