from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.config import DEFAULT_OUTPUT_ROOT
from ..core.formal_data_guard import assert_not_under_formal_task_root
from ..core.io_utils import ensure_dir, read_jsonl, write_json, write_jsonl
from ..evalkit.calibration import VALID_JUDGE_LABELS, judge_agreement
from ..evalkit.mm_judge import PREDICTION_AUDIT_ARTIFACT_TYPE


ARTIFACT_TYPE = "mm_calibration_gate"
ANNOTATION_PACK_ARTIFACT_TYPE = "mm_annotation_pack"
ANNOTATION_ITEM_ARTIFACT_TYPE = "mm_annotation_item"
ANNOTATION_AUDIT_ARTIFACT_TYPE = "mm_annotation_pack_audit"
HUMAN_ANNOTATION_TEMPLATE_ARTIFACT_TYPE = "mm_human_annotation_template"
HUMAN_ANNOTATION_QUEUE_ARTIFACT_TYPE = "mm_human_annotation_queue"
HUMAN_ANNOTATION_APPEND_AUDIT_ARTIFACT_TYPE = "mm_human_annotation_append_audit"
HUMAN_LABEL_ITEM_ARTIFACT_TYPE = "mm_human_label_item"
HUMAN_LABEL_FREEZE_AUDIT_ARTIFACT_TYPE = "mm_human_label_freeze_audit"
DEFAULT_MIN_ITEMS = 150
DEFAULT_MIN_AGREEMENT = 0.8
DEFAULT_MIN_KAPPA = 0.6
DEFAULT_CHANGE_ANNOTATION_PACK = DEFAULT_OUTPUT_ROOT / "mm_calibration" / "change_annotation_pack"
DEFAULT_CHANGE_HUMAN_ANNOTATIONS = DEFAULT_CHANGE_ANNOTATION_PACK / "human_annotations.jsonl"
DEFAULT_CHANGE_HUMAN_ANNOTATION_QUEUE = DEFAULT_CHANGE_ANNOTATION_PACK / "human_annotation_queue.json"
DEFAULT_CHANGE_JUDGE_PREDICTIONS = DEFAULT_OUTPUT_ROOT / "mm_calibration" / "change_judge_predictions.jsonl"
DEFAULT_CHANGE_JUDGE_PREDICTION_AUDIT = DEFAULT_OUTPUT_ROOT / "mm_calibration" / "change_judge_predictions.audit.json"
PUBLISHABLE_LABEL_SOURCES = {"human", "human_calibration", "human_review"}
PREDICTION_LABEL_FIELDS = ("judge_label", "prediction", "model_label", "label", "verdict")


def assert_nonformal_output(path: Path | None, *, purpose: str) -> None:
    if path is not None:
        try:
            assert_not_under_formal_task_root(path, purpose=purpose)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc


def iter_mm_checkpoints(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checkpoints: list[dict[str, Any]] = []
    for record in records:
        for checkpoint in record.get("mm_task", {}).get("checkpoints", []):
            checkpoints.append(
                {
                    "task_id": record.get("task_id"),
                    "repo_id": record.get("repo_id"),
                    "regime": record.get("regime"),
                    "judge_metric": record.get("mm_task", {}).get("judge_metric"),
                    "checkpoint_id": checkpoint.get("checkpoint_id"),
                    "region": checkpoint.get("region"),
                    "prompt": checkpoint.get("prompt"),
                    "target_crop_ref": checkpoint.get("target_crop_ref"),
                    "label_set": checkpoint.get("label_set", ["match", "partial", "mismatch"]),
                }
            )
    return checkpoints


def sample_calibration_items(input_path: Path, output_path: Path, *, limit: int, regime: str | None = None) -> dict[str, Any]:
    records = list(read_jsonl(input_path))
    if regime:
        records = [record for record in records if record.get("regime") == regime]
    checkpoints = iter_mm_checkpoints(records)
    # Deterministic round-robin by task so the sample is not dominated by a few tasks with many checkpoints.
    by_task: dict[str, list[dict[str, Any]]] = {}
    for item in checkpoints:
        by_task.setdefault(item["task_id"], []).append(item)
    sampled: list[dict[str, Any]] = []
    while len(sampled) < limit and by_task:
        for task_id in sorted(list(by_task)):
            task_items = by_task[task_id]
            if not task_items:
                by_task.pop(task_id, None)
                continue
            item = task_items.pop(0)
            sampled.append(
                {
                    **item,
                    "human_label": None,
                    "judge_label": None,
                    "label_instructions": "Fill both labels with one of: match, partial, mismatch.",
                }
            )
            if len(sampled) >= limit:
                break
        by_task = {task_id: items for task_id, items in by_task.items() if items}
    write_jsonl(output_path, sampled)
    report = {
        "input_path": str(input_path),
        "output_path": str(output_path),
        "regime": regime,
        "requested_limit": limit,
        "available_checkpoints": len(checkpoints),
        "sampled": len(sampled),
        "unique_tasks": len({item["task_id"] for item in sampled}),
    }
    write_json(output_path.with_suffix(".summary.json"), report)
    return report


def infer_label_source(path: Path, explicit: str | None = None) -> str:
    if explicit and explicit != "unknown":
        return explicit
    name = path.name.lower()
    if "synthetic" in name or "smoke" in name:
        return "synthetic"
    if "human" in name:
        return "human"
    return explicit or "unknown"


def score_calibration_labels(
    labels_path: Path,
    output_path: Path,
    *,
    min_items: int,
    min_agreement: float,
    min_kappa: float,
    label_source: str = "unknown",
) -> dict[str, Any]:
    records = list(read_jsonl(labels_path))
    labeled = [
        record
        for record in records
        if record.get("human_label") is not None and record.get("judge_label") is not None
    ]
    report = judge_agreement(
        [record["human_label"] for record in labeled],
        [record["judge_label"] for record in labeled],
        min_items=min_items,
        min_agreement=min_agreement,
        min_kappa=min_kappa,
    ).to_json()
    report.update(
        {
            "labels_path": str(labels_path),
            "output_path": str(output_path),
            "label_source": infer_label_source(labels_path, label_source),
            "score_report_role": "diagnostic_only",
            "human_label_source_eligible": infer_label_source(labels_path, label_source) in PUBLISHABLE_LABEL_SOURCES,
            "publishable_candidate": False,
            "publish_blockers": [
                "score-mm-calibration does not publish MM labels",
                "publishable MM labels require freeze-mm-human-labels with a passed judge prediction audit",
                "leaderboard publication requires audit-mm-calibration publish_gate_passed=true",
            ],
            "records": len(records),
            "labeled_records": len(labeled),
            "unlabeled_records": len(records) - len(labeled),
        }
    )
    write_json(output_path, report)
    return report


def duplicate_values(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def invalid_label_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        for field in ("human_label", "judge_label"):
            label = record.get(field)
            if label is not None and label not in VALID_JUDGE_LABELS:
                issues.append(
                    {
                        "line": index,
                        "checkpoint_id": record.get("checkpoint_id"),
                        "field": field,
                        "label": label,
                    }
                )
        label_set = record.get("label_set")
        if label_set is not None and (not isinstance(label_set, list) or set(label_set) != VALID_JUDGE_LABELS):
            issues.append(
                {
                    "line": index,
                    "checkpoint_id": record.get("checkpoint_id"),
                    "field": "label_set",
                    "label": label_set,
                }
            )
    return issues


def parse_label(record: dict[str, Any], fields: tuple[str, ...]) -> tuple[str | None, str | None]:
    for field in fields:
        if field not in record or record.get(field) is None:
            continue
        value = str(record.get(field)).strip().lower()
        if value in VALID_JUDGE_LABELS:
            return value, None
        return None, value
    return None, None


def load_json_or_jsonl_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if path.suffix == ".jsonl":
        return list(read_jsonl(path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    for key in ("items", "predictions", "labels", "checkpoints", "results"):
        values = payload.get(key)
        if isinstance(values, list):
            return [item for item in values if isinstance(item, dict)]
    return []


def load_json_report(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def file_sha256(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def input_file_record(path: Path | None) -> dict[str, Any]:
    return {
        "path": str(path) if path is not None else None,
        "exists": bool(path is not None and path.exists()),
        "sha256": file_sha256(path),
    }


def default_prediction_audit_path(judge_predictions_path: Path) -> Path:
    return judge_predictions_path.with_suffix(".audit.json")


def default_human_label_freeze_audit_path(labels_path: Path) -> Path:
    return labels_path.with_suffix(".freeze_audit.json")


def paths_match(left: object, right: Path) -> bool:
    if not left:
        return False
    try:
        return Path(str(left)).resolve() == right.resolve()
    except OSError:
        return str(left) == str(right)


def prediction_audit_freeze_issues(
    *,
    prediction_audit_path: Path,
    judge_predictions_path: Path,
    sample_path: Path,
    expected_prediction_records: int,
) -> tuple[list[str], dict[str, Any]]:
    audit = load_json_report(prediction_audit_path)
    issues: list[str] = []
    if not prediction_audit_path.exists():
        return [f"judge prediction audit missing: {prediction_audit_path}"], {}
    if audit.get("artifact_type") != PREDICTION_AUDIT_ARTIFACT_TYPE:
        issues.append(f"judge prediction audit has wrong artifact_type: {audit.get('artifact_type')}")
    if audit.get("formal_task_record") is not False:
        issues.append("judge prediction audit must be formal_task_record=false")
    if audit.get("passed") is not True:
        issues.append("judge prediction audit did not pass")
    if audit.get("publishable_candidate") is not True:
        issues.append("judge prediction audit is not publishable_candidate=true")
    if audit.get("issue_count") not in (0, None):
        issues.append(f"judge prediction audit issue_count is nonzero: {audit.get('issue_count')}")
    if not paths_match(audit.get("predictions_path"), judge_predictions_path):
        issues.append(
            f"judge prediction audit predictions_path mismatch: {audit.get('predictions_path')} != {judge_predictions_path}"
        )
    if not paths_match(audit.get("sample_path"), sample_path):
        issues.append(f"judge prediction audit sample_path mismatch: {audit.get('sample_path')} != {sample_path}")
    if audit.get("require_complete") is not True:
        issues.append("judge prediction audit must be complete for the calibration sample")
    if audit.get("temperature") is None:
        issues.append("judge prediction audit missing temperature=0 metadata")
    try:
        if float(audit.get("temperature")) != 0.0:
            issues.append(f"judge prediction audit temperature must be 0, got {audit.get('temperature')}")
    except (TypeError, ValueError):
        issues.append(f"judge prediction audit has invalid temperature: {audit.get('temperature')}")
    if audit.get("prediction_records") != expected_prediction_records:
        issues.append(
            f"judge prediction audit prediction_records {audit.get('prediction_records')} "
            f"!= current prediction records {expected_prediction_records}"
        )
    if audit.get("usable_prediction_records") != expected_prediction_records:
        issues.append(
            f"judge prediction audit usable_prediction_records {audit.get('usable_prediction_records')} "
            f"!= current prediction records {expected_prediction_records}"
        )
    if audit.get("missing_expected_count") not in (0, None):
        issues.append(f"judge prediction audit missing_expected_count is nonzero: {audit.get('missing_expected_count')}")
    if audit.get("extra_prediction_count") not in (0, None):
        issues.append(f"judge prediction audit extra_prediction_count is nonzero: {audit.get('extra_prediction_count')}")
    return issues, audit


def human_label_freeze_gate_issues(
    *,
    freeze_audit_path: Path,
    labels_path: Path,
    expected_label_records: int,
) -> tuple[list[str], dict[str, Any]]:
    audit = load_json_report(freeze_audit_path)
    issues: list[str] = []
    if not freeze_audit_path.exists():
        return [f"human label freeze audit missing: {freeze_audit_path}"], {}
    if audit.get("artifact_type") != HUMAN_LABEL_FREEZE_AUDIT_ARTIFACT_TYPE:
        issues.append(f"human label freeze audit has wrong artifact_type: {audit.get('artifact_type')}")
    if audit.get("formal_task_record") is not False:
        issues.append("human label freeze audit must be formal_task_record=false")
    if audit.get("passed") is not True:
        issues.append("human label freeze audit did not pass")
    if audit.get("publishable_candidate") is not True:
        issues.append("human label freeze audit is not publishable_candidate=true")
    if audit.get("judge_prediction_audit_required") is not True:
        issues.append("human label freeze audit must require a passed judge prediction audit")
    if audit.get("issue_count") not in (0, None):
        issues.append(f"human label freeze audit issue_count is nonzero: {audit.get('issue_count')}")
    if not paths_match(audit.get("output_path"), labels_path):
        issues.append(f"human label freeze audit output_path mismatch: {audit.get('output_path')} != {labels_path}")
    if audit.get("frozen_label_records") != expected_label_records:
        issues.append(
            f"human label freeze audit frozen_label_records {audit.get('frozen_label_records')} "
            f"!= current label records {expected_label_records}"
        )
    output_input = (audit.get("input_files") or {}).get("output_labels")
    if not isinstance(output_input, dict):
        issues.append("human label freeze audit input_files.output_labels is missing or malformed")
    else:
        if not paths_match(output_input.get("path"), labels_path):
            issues.append(f"human label freeze audit output_labels path mismatch: {output_input.get('path')} != {labels_path}")
        if file_sha256(labels_path) != output_input.get("sha256"):
            issues.append("human label freeze audit output_labels digest is stale")
    return issues, audit


def freeze_human_labels(
    *,
    sample_path: Path,
    annotation_items_path: Path,
    judge_predictions_path: Path,
    output_path: Path,
    audit_output_path: Path | None = None,
    prediction_audit_path: Path | None = None,
    min_items: int = DEFAULT_MIN_ITEMS,
    label_source: str = "human",
    require_prediction_audit: bool = True,
) -> dict[str, Any]:
    issues: list[str] = []
    sample_records = list(read_jsonl(sample_path)) if sample_path.exists() else []
    annotation_records = load_json_or_jsonl_records(annotation_items_path)
    prediction_records = load_json_or_jsonl_records(judge_predictions_path)
    if not sample_path.exists():
        issues.append(f"sample file missing: {sample_path}")
    if not annotation_items_path.exists():
        issues.append(f"annotation items file missing: {annotation_items_path}")
    if not judge_predictions_path.exists():
        issues.append(f"judge predictions file missing: {judge_predictions_path}")
    if prediction_audit_path is None:
        prediction_audit_path = default_prediction_audit_path(judge_predictions_path)
    prediction_audit_issues: list[str] = []
    prediction_audit: dict[str, Any] = {}
    if require_prediction_audit:
        prediction_audit_issues, prediction_audit = prediction_audit_freeze_issues(
            prediction_audit_path=prediction_audit_path,
            judge_predictions_path=judge_predictions_path,
            sample_path=sample_path,
            expected_prediction_records=len(prediction_records),
        )
        issues.extend(prediction_audit_issues)
    if label_source not in PUBLISHABLE_LABEL_SOURCES:
        issues.append(f"label_source must be one of {sorted(PUBLISHABLE_LABEL_SOURCES)}")

    sample_ids = [str(record.get("checkpoint_id")) for record in sample_records if record.get("checkpoint_id")]
    annotation_ids = [str(record.get("checkpoint_id")) for record in annotation_records if record.get("checkpoint_id")]
    prediction_ids = [str(record.get("checkpoint_id")) for record in prediction_records if record.get("checkpoint_id")]
    duplicate_sample_ids = duplicate_values(sample_ids)
    duplicate_annotation_ids = duplicate_values(annotation_ids)
    duplicate_prediction_ids = duplicate_values(prediction_ids)
    if duplicate_sample_ids:
        issues.append(f"sample has {len(duplicate_sample_ids)} duplicate checkpoint ids")
    if duplicate_annotation_ids:
        issues.append(f"annotation labels have {len(duplicate_annotation_ids)} duplicate checkpoint ids")
    if duplicate_prediction_ids:
        issues.append(f"judge predictions have {len(duplicate_prediction_ids)} duplicate checkpoint ids")
    sample_set = set(sample_ids)
    annotation_set = set(annotation_ids)
    prediction_set = set(prediction_ids)
    missing_annotations = sorted(sample_set - annotation_set)
    extra_annotations = sorted(annotation_set - sample_set)
    missing_predictions = sorted(sample_set - prediction_set)
    extra_predictions = sorted(prediction_set - sample_set)
    if missing_annotations:
        issues.append(f"annotation labels missing {len(missing_annotations)} sampled checkpoint ids")
    if extra_annotations:
        issues.append(f"annotation labels include {len(extra_annotations)} checkpoint ids outside sample")
    if missing_predictions:
        issues.append(f"judge predictions missing {len(missing_predictions)} sampled checkpoint ids")
    if extra_predictions:
        issues.append(f"judge predictions include {len(extra_predictions)} checkpoint ids outside sample")
    if len(sample_records) < min_items:
        issues.append(f"sample has {len(sample_records)} items; expected at least {min_items}")
    if len(annotation_records) < min_items:
        issues.append(f"annotation labels have {len(annotation_records)} items; expected at least {min_items}")
    if len(prediction_records) < min_items:
        issues.append(f"judge predictions have {len(prediction_records)} items; expected at least {min_items}")

    sample_by_id = {str(record.get("checkpoint_id")): record for record in sample_records if record.get("checkpoint_id")}
    annotation_by_id = {
        str(record.get("checkpoint_id")): record
        for record in annotation_records
        if record.get("checkpoint_id")
    }
    prediction_by_id = {
        str(record.get("checkpoint_id")): record
        for record in prediction_records
        if record.get("checkpoint_id")
    }
    invalid_annotations: list[dict[str, Any]] = []
    invalid_predictions: list[dict[str, Any]] = []
    leaked_judge_labels = 0
    unlabeled_annotations = 0
    frozen_records: list[dict[str, Any]] = []
    for index, checkpoint_id in enumerate(sample_ids, start=1):
        sample = sample_by_id.get(checkpoint_id, {})
        annotation = annotation_by_id.get(checkpoint_id, {})
        prediction = prediction_by_id.get(checkpoint_id, {})
        if "judge_label" in annotation:
            leaked_judge_labels += 1
            invalid_annotations.append(
                {
                    "checkpoint_id": checkpoint_id,
                    "issue": "annotation record must not contain judge_label",
                }
            )
        human_label, invalid_human = parse_label(annotation, ("human_label", "gold_label", "reference_label", "label"))
        if invalid_human is not None:
            invalid_annotations.append(
                {
                    "checkpoint_id": checkpoint_id,
                    "issue": f"invalid human_label: {invalid_human}",
                }
            )
        if human_label is None:
            unlabeled_annotations += 1
            invalid_annotations.append(
                {
                    "checkpoint_id": checkpoint_id,
                    "issue": "missing human_label",
                }
            )
        judge_label, invalid_judge = parse_label(prediction, PREDICTION_LABEL_FIELDS)
        if invalid_judge is not None:
            invalid_predictions.append(
                {
                    "checkpoint_id": checkpoint_id,
                    "issue": f"invalid judge prediction label: {invalid_judge}",
                }
            )
        if judge_label is None:
            invalid_predictions.append(
                {
                    "checkpoint_id": checkpoint_id,
                    "issue": "missing judge_label/prediction",
                }
            )
        if human_label is None or judge_label is None:
            continue
        frozen_records.append(
            {
                "schema_version": "2026-06-19",
                "artifact_type": HUMAN_LABEL_ITEM_ARTIFACT_TYPE,
                "formal_task_record": False,
                "label_source": label_source,
                "checkpoint_id": checkpoint_id,
                "task_id": sample.get("task_id") or annotation.get("task_id") or prediction.get("task_id"),
                "repo_id": sample.get("repo_id") or annotation.get("repo_id") or prediction.get("repo_id"),
                "regime": sample.get("regime") or annotation.get("regime") or prediction.get("regime"),
                "judge_metric": sample.get("judge_metric") or annotation.get("judge_metric"),
                "region": sample.get("region") or annotation.get("region"),
                "prompt": sample.get("prompt") or annotation.get("prompt"),
                "target_crop_ref": sample.get("target_crop_ref") or annotation.get("target_crop_ref"),
                "label_set": sample.get("label_set", ["match", "partial", "mismatch"]),
                "human_label": human_label,
                "judge_label": judge_label,
                "annotator_id": annotation.get("annotator_id"),
                "annotation_notes": annotation.get("annotation_notes"),
                "judge_prediction_source": str(judge_predictions_path),
                "annotation_index": annotation.get("annotation_index") or index,
            }
        )
    if leaked_judge_labels:
        issues.append(f"annotation labels leaked {leaked_judge_labels} judge_label fields")
    if unlabeled_annotations:
        issues.append(f"annotation labels have {unlabeled_annotations} missing human labels")
    if invalid_annotations:
        issues.append(f"annotation labels have {len(invalid_annotations)} invalid records")
    if invalid_predictions:
        issues.append(f"judge predictions have {len(invalid_predictions)} invalid records")
    if len(frozen_records) < min_items:
        issues.append(f"frozen label records {len(frozen_records)} < {min_items}")

    recovery_only = not require_prediction_audit
    report = {
        "schema_version": "2026-06-19",
        "artifact_type": HUMAN_LABEL_FREEZE_AUDIT_ARTIFACT_TYPE,
        "formal_task_record": False,
        "input_files": {
            "sample": input_file_record(sample_path),
            "annotations": input_file_record(annotation_items_path),
            "judge_predictions": input_file_record(judge_predictions_path),
            "judge_prediction_audit": input_file_record(prediction_audit_path),
            "output_labels": input_file_record(output_path),
        },
        "sample_path": str(sample_path),
        "annotation_items_path": str(annotation_items_path),
        "judge_predictions_path": str(judge_predictions_path),
        "judge_prediction_audit_path": str(prediction_audit_path) if prediction_audit_path else None,
        "output_path": str(output_path),
        "label_source": label_source,
        "passed": not issues,
        "publishable_candidate": (
            not issues and label_source in PUBLISHABLE_LABEL_SOURCES and require_prediction_audit
        ),
        "recovery_only": recovery_only,
        "publish_blockers": (
            ["judge_prediction_audit_not_required"] if recovery_only else []
        ),
        "issue_count": len(issues),
        "issues": issues,
        "sample_items": len(sample_records),
        "annotation_items": len(annotation_records),
        "judge_prediction_items": len(prediction_records),
        "judge_prediction_audit_required": require_prediction_audit,
        "judge_prediction_audit_available": bool(prediction_audit),
        "judge_prediction_audit_passed": prediction_audit.get("passed"),
        "judge_prediction_audit_issue_count": len(prediction_audit_issues),
        "judge_prediction_audit_issues": prediction_audit_issues,
        "frozen_label_records": len(frozen_records),
        "duplicate_sample_ids": duplicate_sample_ids,
        "duplicate_annotation_ids": duplicate_annotation_ids,
        "duplicate_prediction_ids": duplicate_prediction_ids,
        "missing_annotations_count": len(missing_annotations),
        "extra_annotations_count": len(extra_annotations),
        "missing_predictions_count": len(missing_predictions),
        "extra_predictions_count": len(extra_predictions),
        "leaked_judge_labels": leaked_judge_labels,
        "unlabeled_annotations": unlabeled_annotations,
        "invalid_annotation_count": len(invalid_annotations),
        "invalid_prediction_count": len(invalid_predictions),
        "invalid_annotations": invalid_annotations[:100],
        "invalid_predictions": invalid_predictions[:100],
        "gate": "Human labels are frozen only after judge-hidden annotations and independent judge predictions align exactly by checkpoint_id.",
    }
    if issues:
        if audit_output_path is not None:
            write_json(audit_output_path, report)
        return report
    ensure_dir(output_path.parent)
    write_jsonl(output_path, frozen_records)
    report["input_files"]["output_labels"] = input_file_record(output_path)
    if audit_output_path is not None:
        write_json(audit_output_path, report)
    return report


def annotation_item_from_sample(record: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "schema_version": "2026-06-19",
        "artifact_type": ANNOTATION_ITEM_ARTIFACT_TYPE,
        "formal_task_record": False,
        "annotation_index": index,
        "checkpoint_id": record.get("checkpoint_id"),
        "task_id": record.get("task_id"),
        "repo_id": record.get("repo_id"),
        "regime": record.get("regime"),
        "judge_metric": record.get("judge_metric"),
        "region": record.get("region"),
        "prompt": record.get("prompt"),
        "target_crop_ref": record.get("target_crop_ref"),
        "label_set": record.get("label_set", ["match", "partial", "mismatch"]),
        "human_label": None,
        "annotator_id": None,
        "annotation_notes": None,
        "instructions": "Fill human_label with one of: match, partial, mismatch. Do not add judge_label to this annotation file.",
    }


def human_annotation_template() -> dict[str, Any]:
    return {
        "schema_version": "2026-06-19",
        "artifact_type": HUMAN_ANNOTATION_TEMPLATE_ARTIFACT_TYPE,
        "formal_task_record": False,
        "template_only": True,
        "template_warning": (
            "Use append-mm-human-annotation after inspecting one sampled local checkpoint. "
            "This template is not a label record and must not be frozen directly."
        ),
        "checkpoint_id": "TBD_checkpoint_id_from_change_sample",
        "human_label": "TBD_match_partial_or_mismatch",
        "annotator_id": "TBD_human_or_review_id",
        "annotation_notes": "TBD short rationale. Do not include judge_label.",
    }


def build_human_annotation_queue(
    *,
    sample_path: Path,
    pack_dir: Path,
    labels_path: Path,
    output_path: Path,
    limit: int | None = None,
) -> dict[str, Any]:
    manifest_path = pack_dir / "manifest.json"
    items_path = pack_dir / "annotation_items.jsonl"
    sample_records = list(read_jsonl(sample_path)) if sample_path.exists() else []
    pack_items = list(read_jsonl(items_path)) if items_path.exists() else []
    labels = list(read_jsonl(labels_path)) if labels_path.exists() else []
    issues: list[str] = []
    if not sample_path.exists():
        issues.append(f"sample file missing: {sample_path}")
    if not manifest_path.exists():
        issues.append(f"annotation manifest missing: {manifest_path}")
    if not items_path.exists():
        issues.append(f"annotation items missing: {items_path}")

    sample_ids = [str(record.get("checkpoint_id")) for record in sample_records if record.get("checkpoint_id")]
    pack_ids = [str(record.get("checkpoint_id")) for record in pack_items if record.get("checkpoint_id")]
    label_ids = [str(record.get("checkpoint_id")) for record in labels if record.get("checkpoint_id")]
    duplicate_sample_ids = duplicate_values(sample_ids)
    duplicate_pack_ids = duplicate_values(pack_ids)
    duplicate_label_ids = duplicate_values(label_ids)
    if duplicate_sample_ids:
        issues.append(f"sample has {len(duplicate_sample_ids)} duplicate checkpoint ids")
    if duplicate_pack_ids:
        issues.append(f"annotation pack has {len(duplicate_pack_ids)} duplicate checkpoint ids")
    if duplicate_label_ids:
        issues.append(f"human annotations have {len(duplicate_label_ids)} duplicate checkpoint ids")

    sample_set = set(sample_ids)
    pack_set = set(pack_ids)
    label_set = set(label_ids)
    missing_from_pack = sorted(sample_set - pack_set)
    extra_in_pack = sorted(pack_set - sample_set)
    labels_outside_sample = sorted(label_set - sample_set)
    if missing_from_pack:
        issues.append(f"annotation pack is missing {len(missing_from_pack)} sampled checkpoint ids")
    if extra_in_pack:
        issues.append(f"annotation pack has {len(extra_in_pack)} checkpoint ids outside sample")
    if labels_outside_sample:
        issues.append(f"human annotations include {len(labels_outside_sample)} checkpoint ids outside sample")

    invalid_labels = existing_annotation_issues(labels)
    if invalid_labels:
        issues.append(f"human annotations have {len(invalid_labels)} invalid records")

    pack_by_id = {str(item.get("checkpoint_id")): item for item in pack_items if item.get("checkpoint_id")}
    label_by_id = {str(item.get("checkpoint_id")): item for item in labels if item.get("checkpoint_id")}
    unlabeled_ids = [
        checkpoint_id
        for checkpoint_id in sample_ids
        if checkpoint_id in pack_by_id and checkpoint_id not in label_by_id
    ]
    if limit is not None:
        unlabeled_ids = unlabeled_ids[:limit]
    queue_items: list[dict[str, Any]] = []
    for queue_index, checkpoint_id in enumerate(unlabeled_ids, start=1):
        item = pack_by_id[checkpoint_id]
        queue_items.append(
            {
                "queue_index": queue_index,
                "checkpoint_id": checkpoint_id,
                "task_id": item.get("task_id"),
                "repo_id": item.get("repo_id"),
                "regime": item.get("regime"),
                "judge_metric": item.get("judge_metric"),
                "region": item.get("region"),
                "prompt": item.get("prompt"),
                "target_crop_ref": item.get("target_crop_ref"),
                "label_set": item.get("label_set", ["match", "partial", "mismatch"]),
                "annotation_notes": item.get("annotation_notes"),
                "append_command": (
                    "python -m sitecontinuum append-mm-human-annotation "
                    f"--sample {sample_path} "
                    f"--labels {labels_path} "
                    f"--checkpoint-id {checkpoint_id} "
                    "--human-label <match|partial|mismatch> "
                    "--annotator-id <annotator_id> "
                    "--annotation-notes '<short rationale; no judge_label>'"
                ),
            }
        )

    report = {
        "schema_version": "2026-06-19",
        "artifact_type": HUMAN_ANNOTATION_QUEUE_ARTIFACT_TYPE,
        "formal_task_record": False,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sample_path": str(sample_path),
        "pack_dir": str(pack_dir),
        "labels_path": str(labels_path),
        "output_path": str(output_path),
        "input_files": {
            "sample": input_file_record(sample_path),
            "manifest": input_file_record(manifest_path),
            "annotation_items": input_file_record(items_path),
            "human_annotations": input_file_record(labels_path),
        },
        "passed": not issues,
        "issue_count": len(issues),
        "issues": issues,
        "sample_items": len(sample_records),
        "annotation_pack_items": len(pack_items),
        "human_annotation_records": len(labels),
        "labeled_count": len(label_set & sample_set),
        "unlabeled_count": max(0, len(sample_set) - len(label_set & sample_set)),
        "queue_item_count": len(queue_items),
        "limit": limit,
        "duplicate_sample_ids": duplicate_sample_ids,
        "duplicate_pack_ids": duplicate_pack_ids,
        "duplicate_label_ids": duplicate_label_ids,
        "missing_from_pack_count": len(missing_from_pack),
        "extra_in_pack_count": len(extra_in_pack),
        "labels_outside_sample_count": len(labels_outside_sample),
        "invalid_label_count": len(invalid_labels),
        "invalid_labels": invalid_labels[:100],
        "queue_items": queue_items,
        "gate": "This queue only schedules judge-hidden human annotations; it must not contain human_label or judge_label outputs.",
    }
    write_json(output_path, report)
    return report


def sample_record_by_checkpoint(sample_path: Path, checkpoint_id: str) -> tuple[dict[str, Any] | None, list[str]]:
    issues: list[str] = []
    if not sample_path.exists():
        return None, [f"sample file missing: {sample_path}"]
    records = list(read_jsonl(sample_path))
    ids = [str(record.get("checkpoint_id") or "") for record in records if record.get("checkpoint_id")]
    duplicates = duplicate_values(ids)
    if duplicates:
        issues.append(f"sample has {len(duplicates)} duplicate checkpoint ids")
    matches = [record for record in records if str(record.get("checkpoint_id") or "") == checkpoint_id]
    if not matches:
        issues.append(f"checkpoint_id not found in sample: {checkpoint_id}")
        return None, issues
    if len(matches) > 1:
        issues.append(f"checkpoint_id has {len(matches)} sample records: {checkpoint_id}")
    return matches[0], issues


def existing_annotation_issues(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        item_issues: list[str] = []
        if record.get("artifact_type") != ANNOTATION_ITEM_ARTIFACT_TYPE:
            item_issues.append(f"artifact_type must be {ANNOTATION_ITEM_ARTIFACT_TYPE}")
        if record.get("formal_task_record") is not False:
            item_issues.append("formal_task_record must be false")
        if "judge_label" in record:
            item_issues.append("annotation record must not include judge_label")
        human_label = record.get("human_label")
        if human_label not in VALID_JUDGE_LABELS:
            item_issues.append(f"human_label must be one of {sorted(VALID_JUDGE_LABELS)}")
        if not record.get("checkpoint_id"):
            item_issues.append("missing checkpoint_id")
        if item_issues:
            issues.append(
                {
                    "line": index,
                    "checkpoint_id": record.get("checkpoint_id"),
                    "issues": item_issues,
                }
            )
    return issues


def append_human_annotation(
    *,
    sample_path: Path,
    labels_path: Path,
    checkpoint_id: str,
    human_label: str,
    annotator_id: str,
    annotation_notes: str | None = None,
    audit_output_path: Path | None = None,
) -> dict[str, Any]:
    issues: list[str] = []
    checkpoint_id = str(checkpoint_id or "").strip()
    human_label = str(human_label or "").strip().lower()
    annotator_id = str(annotator_id or "").strip()
    if not checkpoint_id:
        issues.append("checkpoint_id is required")
    if human_label not in VALID_JUDGE_LABELS:
        issues.append(f"human_label must be one of {sorted(VALID_JUDGE_LABELS)}")
    if not annotator_id:
        issues.append("annotator_id is required")

    sample, sample_issues = sample_record_by_checkpoint(sample_path, checkpoint_id) if checkpoint_id else (None, [])
    issues.extend(sample_issues)
    existing = list(read_jsonl(labels_path)) if labels_path.exists() else []
    existing_ids = [str(record.get("checkpoint_id") or "") for record in existing if record.get("checkpoint_id")]
    duplicate_existing_ids = duplicate_values(existing_ids)
    if duplicate_existing_ids:
        issues.append(f"existing annotation labels have {len(duplicate_existing_ids)} duplicate checkpoint ids")
    if checkpoint_id and checkpoint_id in set(existing_ids):
        issues.append(f"checkpoint_id already has a human annotation: {checkpoint_id}")
    invalid_existing = existing_annotation_issues(existing)
    if invalid_existing:
        issues.append(f"existing annotation labels have {len(invalid_existing)} invalid records")

    record: dict[str, Any] | None = None
    if sample is not None and human_label in VALID_JUDGE_LABELS and annotator_id:
        record = {
            "schema_version": "2026-06-19",
            "artifact_type": ANNOTATION_ITEM_ARTIFACT_TYPE,
            "formal_task_record": False,
            "label_source": "human",
            "annotation_index": len(existing) + 1,
            "checkpoint_id": checkpoint_id,
            "task_id": sample.get("task_id"),
            "repo_id": sample.get("repo_id"),
            "regime": sample.get("regime"),
            "judge_metric": sample.get("judge_metric"),
            "region": sample.get("region"),
            "prompt": sample.get("prompt"),
            "target_crop_ref": sample.get("target_crop_ref"),
            "label_set": sample.get("label_set", ["match", "partial", "mismatch"]),
            "human_label": human_label,
            "annotator_id": annotator_id,
            "annotation_notes": annotation_notes,
            "annotated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    report = {
        "schema_version": "2026-06-19",
        "artifact_type": HUMAN_ANNOTATION_APPEND_AUDIT_ARTIFACT_TYPE,
        "formal_task_record": False,
        "sample_path": str(sample_path),
        "labels_path": str(labels_path),
        "checkpoint_id": checkpoint_id,
        "human_label": human_label,
        "annotator_id": annotator_id,
        "passed": not issues,
        "issue_count": len(issues),
        "issues": issues,
        "existing_label_count": len(existing),
        "candidate_record": record,
        "would_write_label_count": len(existing) + (0 if issues else 1),
        "invalid_existing_records": invalid_existing[:100],
        "gate": "Append exactly one judge-hidden human MM annotation after inspecting one sampled local checkpoint.",
    }
    if issues:
        if audit_output_path is not None:
            write_json(audit_output_path, report)
        return report
    ensure_dir(labels_path.parent)
    write_jsonl(labels_path, [*existing, record])
    if audit_output_path is not None:
        write_json(audit_output_path, report)
    return report


def export_annotation_pack(sample_path: Path, output_dir: Path, *, regime: str | None = None) -> dict[str, Any]:
    records = list(read_jsonl(sample_path))
    if regime:
        records = [record for record in records if record.get("regime") == regime]
    ensure_dir(output_dir)
    items = [annotation_item_from_sample(record, index) for index, record in enumerate(records, start=1)]
    items_path = output_dir / "annotation_items.jsonl"
    manifest_path = output_dir / "manifest.json"
    instructions_path = output_dir / "README.md"
    write_jsonl(items_path, items)
    manifest = {
        "schema_version": "2026-06-19",
        "artifact_type": ANNOTATION_PACK_ARTIFACT_TYPE,
        "formal_task_record": False,
        "sample_path": str(sample_path),
        "output_dir": str(output_dir),
        "items_path": str(items_path),
        "instructions_path": str(instructions_path),
        "regime": regime,
        "item_count": len(items),
        "unique_checkpoint_count": len({item.get("checkpoint_id") for item in items if item.get("checkpoint_id")}),
        "label_set": sorted(VALID_JUDGE_LABELS),
        "judge_labels_hidden": True,
        "intended_use": "human MM calibration annotation template; not benchmark task data",
    }
    write_json(manifest_path, manifest)
    instructions_path.write_text(
        "\n".join(
            [
                "# SiteContinuum MM Annotation Pack",
                "",
                "This directory is non-formal calibration evidence, not benchmark task data.",
                "",
                "Fill `human_label` in `annotation_items.jsonl` with one of `match`, `partial`, or `mismatch`.",
                "Do not add `judge_label` here; model predictions are merged only after human labels are frozen.",
                "Keep `checkpoint_id`, `task_id`, `repo_id`, `region`, and `prompt` unchanged.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return manifest


def audit_annotation_pack(
    sample_path: Path,
    pack_dir: Path,
    output_path: Path | None = None,
    *,
    require_unlabeled: bool = True,
    min_items: int = DEFAULT_MIN_ITEMS,
) -> dict[str, Any]:
    issues: list[str] = []
    manifest_path = pack_dir / "manifest.json"
    items_path = pack_dir / "annotation_items.jsonl"
    sample_records = list(read_jsonl(sample_path)) if sample_path.exists() else []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    items = list(read_jsonl(items_path)) if items_path.exists() else []
    if not sample_path.exists():
        issues.append(f"sample file missing: {sample_path}")
    if not manifest_path.exists():
        issues.append(f"annotation manifest missing: {manifest_path}")
    if not items_path.exists():
        issues.append(f"annotation items missing: {items_path}")
    if manifest and manifest.get("artifact_type") != ANNOTATION_PACK_ARTIFACT_TYPE:
        issues.append(f"annotation manifest has wrong artifact_type: {manifest.get('artifact_type')}")
    if manifest and manifest.get("formal_task_record") is not False:
        issues.append("annotation manifest must be formal_task_record=false")
    sample_checkpoint_ids = [
        str(record.get("checkpoint_id"))
        for record in sample_records
        if record.get("checkpoint_id")
    ]
    item_checkpoint_ids = [
        str(record.get("checkpoint_id"))
        for record in items
        if record.get("checkpoint_id")
    ]
    duplicate_sample_ids = duplicate_values(sample_checkpoint_ids)
    duplicate_item_ids = duplicate_values(item_checkpoint_ids)
    if duplicate_sample_ids:
        issues.append(f"sample has {len(duplicate_sample_ids)} duplicate checkpoint ids")
    if duplicate_item_ids:
        issues.append(f"annotation pack has {len(duplicate_item_ids)} duplicate checkpoint ids")
    missing_from_pack = sorted(set(sample_checkpoint_ids) - set(item_checkpoint_ids))
    extra_in_pack = sorted(set(item_checkpoint_ids) - set(sample_checkpoint_ids))
    if missing_from_pack:
        issues.append(f"annotation pack is missing {len(missing_from_pack)} sampled checkpoint ids")
    if extra_in_pack:
        issues.append(f"annotation pack has {len(extra_in_pack)} checkpoint ids outside sample")
    if len(items) < min_items:
        issues.append(f"annotation pack has {len(items)} items; expected at least {min_items}")
    invalid_items: list[dict[str, Any]] = []
    prefilled_human_labels = 0
    leaked_judge_labels = 0
    for index, item in enumerate(items, start=1):
        item_issues: list[str] = []
        if item.get("artifact_type") != ANNOTATION_ITEM_ARTIFACT_TYPE:
            item_issues.append(f"artifact_type must be {ANNOTATION_ITEM_ARTIFACT_TYPE}")
        if item.get("formal_task_record") is not False:
            item_issues.append("formal_task_record must be false")
        label_set = item.get("label_set")
        if not isinstance(label_set, list) or set(label_set) != VALID_JUDGE_LABELS:
            item_issues.append("label_set must be exactly match/partial/mismatch")
        human_label = item.get("human_label")
        if human_label is not None:
            prefilled_human_labels += 1
            if human_label not in VALID_JUDGE_LABELS:
                item_issues.append(f"invalid human_label: {human_label}")
        if item.get("judge_label") is not None or "judge_label" in item:
            leaked_judge_labels += 1
            item_issues.append("annotation item must not include judge_label")
        for required in ("checkpoint_id", "task_id", "repo_id", "regime", "judge_metric", "region", "prompt"):
            if not item.get(required):
                item_issues.append(f"missing {required}")
        if item_issues:
            invalid_items.append({"line": index, "checkpoint_id": item.get("checkpoint_id"), "issues": item_issues})
    if require_unlabeled and prefilled_human_labels:
        issues.append(f"annotation pack has {prefilled_human_labels} prefilled human labels")
    if leaked_judge_labels:
        issues.append(f"annotation pack has {leaked_judge_labels} leaked judge_label fields")
    if invalid_items:
        issues.append(f"annotation pack has {len(invalid_items)} invalid item records")
    report = {
        "schema_version": "2026-06-19",
        "artifact_type": ANNOTATION_AUDIT_ARTIFACT_TYPE,
        "formal_task_record": False,
        "sample_path": str(sample_path),
        "pack_dir": str(pack_dir),
        "manifest_path": str(manifest_path),
        "items_path": str(items_path),
        "input_files": {
            "sample": input_file_record(sample_path),
            "manifest": input_file_record(manifest_path),
            "annotation_items": input_file_record(items_path),
            "instructions": input_file_record(pack_dir / "README.md"),
        },
        "passed": not issues,
        "publishable": not issues,
        "issue_count": len(issues),
        "issues": issues,
        "sample_items": len(sample_records),
        "annotation_items": len(items),
        "unique_sample_checkpoints": len(set(sample_checkpoint_ids)),
        "unique_annotation_checkpoints": len(set(item_checkpoint_ids)),
        "missing_from_pack_count": len(missing_from_pack),
        "extra_in_pack_count": len(extra_in_pack),
        "prefilled_human_labels": prefilled_human_labels,
        "leaked_judge_labels": leaked_judge_labels,
        "invalid_item_count": len(invalid_items),
        "invalid_items": invalid_items[:100],
        "gate": "Human MM calibration annotations must be exported as an unlabeled, judge-hidden, non-formal annotation pack.",
    }
    if output_path is not None:
        write_json(output_path, report)
    return report


def audit_calibration_gate(
    *,
    regime: str,
    sample_path: Path,
    labels_path: Path,
    score_report_path: Path,
    synthetic_report_path: Path,
    freeze_audit_path: Path | None = None,
    output_path: Path | None = None,
    min_items: int = DEFAULT_MIN_ITEMS,
    min_agreement: float = DEFAULT_MIN_AGREEMENT,
    min_kappa: float = DEFAULT_MIN_KAPPA,
    label_source: str = "unknown",
) -> dict[str, Any]:
    issues: list[str] = []
    sample_records = list(read_jsonl(sample_path)) if sample_path.exists() else []
    if not sample_path.exists():
        issues.append(f"sample file missing: {sample_path}")
    sample_checkpoint_ids = [
        str(record.get("checkpoint_id"))
        for record in sample_records
        if record.get("checkpoint_id")
    ]
    sample_duplicate_checkpoint_ids = duplicate_values(sample_checkpoint_ids)
    sample_invalid_labels = invalid_label_records(sample_records)
    if len(sample_records) < min_items:
        issues.append(f"sample has {len(sample_records)} items; expected at least {min_items}")
    if sample_duplicate_checkpoint_ids:
        issues.append(f"sample has {len(sample_duplicate_checkpoint_ids)} duplicate checkpoint ids")
    if sample_invalid_labels:
        issues.append(f"sample has {len(sample_invalid_labels)} invalid label fields")

    labels_records = list(read_jsonl(labels_path)) if labels_path.exists() else []
    if freeze_audit_path is None:
        freeze_audit_path = default_human_label_freeze_audit_path(labels_path)
    freeze_audit_issues, freeze_audit = human_label_freeze_gate_issues(
        freeze_audit_path=freeze_audit_path,
        labels_path=labels_path,
        expected_label_records=len(labels_records),
    )
    inferred_label_source = infer_label_source(labels_path, label_source)
    label_checkpoint_ids = [
        str(record.get("checkpoint_id"))
        for record in labels_records
        if record.get("checkpoint_id")
    ]
    labeled_records = [
        record
        for record in labels_records
        if record.get("human_label") is not None and record.get("judge_label") is not None
    ]
    human_labeled_records = labeled_records if inferred_label_source in PUBLISHABLE_LABEL_SOURCES else []
    label_duplicate_checkpoint_ids = duplicate_values(label_checkpoint_ids)
    label_invalid_labels = invalid_label_records(labels_records)
    labels_outside_sample = sorted(set(label_checkpoint_ids) - set(sample_checkpoint_ids))

    label_report: dict[str, Any] | None = None
    label_report_issue: str | None = None
    if labeled_records:
        try:
            label_report = judge_agreement(
                [record["human_label"] for record in labeled_records],
                [record["judge_label"] for record in labeled_records],
                min_items=min_items,
                min_agreement=min_agreement,
                min_kappa=min_kappa,
            ).to_json()
        except ValueError as exc:
            label_report_issue = str(exc)
            issues.append(f"label scoring failed: {exc}")
    elif labels_path.exists():
        issues.append("label file exists but has no fully labeled records")

    if not labels_path.exists():
        issues.append(f"human label file missing: {labels_path}")
    if label_duplicate_checkpoint_ids:
        issues.append(f"label file has {len(label_duplicate_checkpoint_ids)} duplicate checkpoint ids")
    if label_invalid_labels:
        issues.append(f"label file has {len(label_invalid_labels)} invalid label fields")
    if labels_outside_sample:
        issues.append(f"label file has {len(labels_outside_sample)} checkpoint ids outside the sampled set")

    saved_score_report = {}
    if score_report_path.exists():
        saved_score_report = json.loads(score_report_path.read_text(encoding="utf-8"))
    synthetic_report = {}
    if synthetic_report_path.exists():
        synthetic_report = json.loads(synthetic_report_path.read_text(encoding="utf-8"))

    score = label_report or {}
    publish_gate_issues: list[str] = []
    if inferred_label_source not in PUBLISHABLE_LABEL_SOURCES:
        publish_gate_issues.append(f"label_source is {inferred_label_source}, not human")
    if len(human_labeled_records) < min_items:
        publish_gate_issues.append(f"human labeled records {len(human_labeled_records)} < {min_items}")
    if score.get("agreement") is None or float(score.get("agreement", 0.0)) < min_agreement:
        publish_gate_issues.append(f"agreement {score.get('agreement')} < {min_agreement}")
    if score.get("cohen_kappa") is None or float(score.get("cohen_kappa", 0.0)) < min_kappa:
        publish_gate_issues.append(f"cohen_kappa {score.get('cohen_kappa')} < {min_kappa}")
    if label_invalid_labels or label_duplicate_checkpoint_ids or labels_outside_sample:
        publish_gate_issues.append("label file structural audit failed")
    if label_report_issue:
        publish_gate_issues.append(label_report_issue)
    publish_gate_issues.extend(freeze_audit_issues)

    sample_gate_passed = (
        sample_path.exists()
        and len(sample_records) >= min_items
        and not sample_duplicate_checkpoint_ids
        and not sample_invalid_labels
    )
    publish_gate_passed = not publish_gate_issues
    report = {
        "schema_version": "2026-06-18",
        "artifact_type": ARTIFACT_TYPE,
        "formal_task_record": False,
        "regime": regime,
        "sample_path": str(sample_path),
        "labels_path": str(labels_path),
        "freeze_audit_path": str(freeze_audit_path),
        "score_report_path": str(score_report_path),
        "synthetic_report_path": str(synthetic_report_path),
        "min_items": min_items,
        "min_agreement": min_agreement,
        "min_kappa": min_kappa,
        "sample_items": len(sample_records),
        "sample_unique_tasks": len({record.get("task_id") for record in sample_records if record.get("task_id")}),
        "sample_unique_checkpoints": len(set(sample_checkpoint_ids)),
        "sample_duplicate_checkpoint_ids": sample_duplicate_checkpoint_ids,
        "sample_invalid_label_issue_count": len(sample_invalid_labels),
        "label_source": inferred_label_source,
        "label_records": len(labels_records),
        "labeled_records": len(labeled_records),
        "human_labeled_records": len(human_labeled_records),
        "human_label_freeze_audit_available": bool(freeze_audit),
        "human_label_freeze_audit_passed": freeze_audit.get("passed"),
        "human_label_freeze_audit_issue_count": len(freeze_audit_issues),
        "human_label_freeze_audit_issues": freeze_audit_issues,
        "label_unique_checkpoints": len(set(label_checkpoint_ids)),
        "label_duplicate_checkpoint_ids": label_duplicate_checkpoint_ids,
        "label_invalid_label_issue_count": len(label_invalid_labels),
        "labels_outside_sample_count": len(labels_outside_sample),
        "agreement": score.get("agreement"),
        "cohen_kappa": score.get("cohen_kappa"),
        "label_counts": score.get("label_counts", {}),
        "sample_gate_passed": sample_gate_passed,
        "publish_gate_passed": publish_gate_passed,
        "publishable": publish_gate_passed,
        "publish_gate_issues": publish_gate_issues,
        "issues": issues,
        "issue_count": len(issues),
        "saved_score_report_available": bool(saved_score_report),
        "saved_score_report_passed_gate": saved_score_report.get("passed_gate"),
        "saved_score_report_label_source": saved_score_report.get("label_source"),
        "synthetic_smoke_available": bool(synthetic_report),
        "synthetic_smoke_passed_gate": synthetic_report.get("passed_gate"),
        "synthetic_smoke_agreement": synthetic_report.get("agreement"),
        "synthetic_smoke_kappa": synthetic_report.get("cohen_kappa"),
        "synthetic_smoke_publishable": False,
        "gate": "MM judge outputs are leaderboard-publishable only when frozen human labels meet min_items/agreement/kappa thresholds and the human-label freeze audit is current.",
    }
    if output_path is not None:
        write_json(output_path, report)
    return report


def add_sample_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", type=Path, default=DEFAULT_OUTPUT_ROOT / "capability_splits" / "change_lm_mm.jsonl")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "mm_calibration" / "change_sample.jsonl")
    parser.add_argument("--limit", type=int, default=150)
    parser.add_argument("--regime", default="change")


def run_sample_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.output, purpose="MM calibration sample")
    report = sample_calibration_items(args.input, args.output, limit=args.limit, regime=args.regime)
    print(
        f"sampled {report['sampled']} MM calibration items "
        f"from {report['unique_tasks']} tasks to {args.output}"
    )


def add_annotation_export_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--sample", type=Path, default=DEFAULT_OUTPUT_ROOT / "mm_calibration" / "change_sample.jsonl")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_CHANGE_ANNOTATION_PACK)
    parser.add_argument("--regime", default="change")


def run_annotation_export_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.output_dir, purpose="MM annotation pack")
    report = export_annotation_pack(args.sample, args.output_dir, regime=args.regime)
    print(f"exported MM annotation pack: items={report['item_count']} output_dir={args.output_dir}")


def add_annotation_audit_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--sample", type=Path, default=DEFAULT_OUTPUT_ROOT / "mm_calibration" / "change_sample.jsonl")
    parser.add_argument("--pack-dir", type=Path, default=DEFAULT_CHANGE_ANNOTATION_PACK)
    parser.add_argument("--output", type=Path, default=DEFAULT_CHANGE_ANNOTATION_PACK / "annotation_audit.json")
    parser.add_argument("--allow-prefilled-labels", action="store_true")
    parser.add_argument("--min-items", type=int, default=DEFAULT_MIN_ITEMS)


def run_annotation_audit_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.output, purpose="MM annotation pack audit")
    ensure_dir(args.output.parent)
    report = audit_annotation_pack(
        args.sample,
        args.pack_dir,
        output_path=args.output,
        require_unlabeled=not args.allow_prefilled_labels,
        min_items=args.min_items,
    )
    print(
        f"MM annotation pack audit: passed={report['passed']} "
        f"items={report['annotation_items']} issues={report['issue_count']}"
    )


def add_human_annotation_template_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_CHANGE_ANNOTATION_PACK / "human_annotation.template.json",
    )


def run_human_annotation_template_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.output, purpose="MM human annotation template")
    ensure_dir(args.output.parent)
    write_json(args.output, human_annotation_template())
    print(f"wrote non-formal MM human annotation template to {args.output}; template is not publishable")


def add_human_annotation_queue_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--sample", type=Path, default=DEFAULT_OUTPUT_ROOT / "mm_calibration" / "change_sample.jsonl")
    parser.add_argument("--pack-dir", type=Path, default=DEFAULT_CHANGE_ANNOTATION_PACK)
    parser.add_argument("--labels", type=Path, default=DEFAULT_CHANGE_HUMAN_ANNOTATIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_CHANGE_HUMAN_ANNOTATION_QUEUE)
    parser.add_argument("--limit", type=int, default=None)


def run_human_annotation_queue_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.output, purpose="MM human annotation queue")
    ensure_dir(args.output.parent)
    report = build_human_annotation_queue(
        sample_path=args.sample,
        pack_dir=args.pack_dir,
        labels_path=args.labels,
        output_path=args.output,
        limit=args.limit,
    )
    print(
        "MM human annotation queue: "
        f"passed={report['passed']} labeled={report['labeled_count']} "
        f"unlabeled={report['unlabeled_count']} queue={report['queue_item_count']} "
        f"issues={report['issue_count']} output={args.output}"
    )
    if not report["passed"]:
        raise SystemExit(1)


def add_append_human_annotation_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--sample", type=Path, default=DEFAULT_OUTPUT_ROOT / "mm_calibration" / "change_sample.jsonl")
    parser.add_argument("--labels", type=Path, default=DEFAULT_CHANGE_HUMAN_ANNOTATIONS)
    parser.add_argument("--checkpoint-id", required=True)
    parser.add_argument("--human-label", choices=sorted(VALID_JUDGE_LABELS), required=True)
    parser.add_argument("--annotator-id", required=True)
    parser.add_argument("--annotation-notes", default=None)
    parser.add_argument("--audit-output", type=Path, default=None)


def run_append_human_annotation_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.labels, purpose="MM human annotation labels")
    assert_nonformal_output(args.audit_output, purpose="MM human annotation append audit")
    report = append_human_annotation(
        sample_path=args.sample,
        labels_path=args.labels,
        checkpoint_id=args.checkpoint_id,
        human_label=args.human_label,
        annotator_id=args.annotator_id,
        annotation_notes=args.annotation_notes,
        audit_output_path=args.audit_output,
    )
    print(
        "append MM human annotation: "
        f"passed={report['passed']} checkpoint={report.get('checkpoint_id')} "
        f"labels={report.get('would_write_label_count')} issues={report['issue_count']}"
    )
    if not report["passed"]:
        raise SystemExit(1)


def add_freeze_labels_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--sample", type=Path, default=DEFAULT_OUTPUT_ROOT / "mm_calibration" / "change_sample.jsonl")
    parser.add_argument(
        "--annotations",
        type=Path,
        default=DEFAULT_CHANGE_HUMAN_ANNOTATIONS,
    )
    parser.add_argument("--judge-predictions", type=Path, default=DEFAULT_CHANGE_JUDGE_PREDICTIONS)
    parser.add_argument(
        "--prediction-audit",
        type=Path,
        default=None,
        help="Passed audit-mm-judge-predictions report for the same prediction file and sample.",
    )
    parser.add_argument(
        "--allow-missing-prediction-audit",
        action="store_true",
        help="Recovery-only escape hatch; publishable freezes require a passed prediction audit.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT / "mm_calibration" / "change_sample.human_labels.jsonl",
    )
    parser.add_argument(
        "--audit-output",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT / "mm_calibration" / "change_sample.human_labels.freeze_audit.json",
    )
    parser.add_argument("--min-items", type=int, default=DEFAULT_MIN_ITEMS)
    parser.add_argument(
        "--label-source",
        choices=sorted(PUBLISHABLE_LABEL_SOURCES),
        default="human",
    )


def run_freeze_labels_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.output, purpose="MM frozen human labels")
    assert_nonformal_output(args.audit_output, purpose="MM human label freeze audit")
    report = freeze_human_labels(
        sample_path=args.sample,
        annotation_items_path=args.annotations,
        judge_predictions_path=args.judge_predictions,
        output_path=args.output,
        audit_output_path=args.audit_output,
        prediction_audit_path=args.prediction_audit,
        min_items=args.min_items,
        label_source=args.label_source,
        require_prediction_audit=not args.allow_missing_prediction_audit,
    )
    print(
        f"MM human label freeze: passed={report['passed']} "
        f"frozen={report['frozen_label_records']} issues={report['issue_count']} "
        f"output={args.output}"
    )
    if not report["passed"]:
        raise SystemExit(1)


def add_score_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "mm_calibration" / "calibration_report.json")
    parser.add_argument("--min-items", type=int, default=150)
    parser.add_argument("--min-agreement", type=float, default=0.8)
    parser.add_argument("--min-kappa", type=float, default=0.6)
    parser.add_argument(
        "--label-source",
        choices=["human", "human_calibration", "human_review", "synthetic", "unknown"],
        default="unknown",
        help="Whether the labels are human calibration labels or a synthetic smoke-test file.",
    )


def run_score_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.output, purpose="MM calibration score report")
    ensure_dir(args.output.parent)
    report = score_calibration_labels(
        args.labels,
        args.output,
        min_items=args.min_items,
        min_agreement=args.min_agreement,
        min_kappa=args.min_kappa,
        label_source=args.label_source,
    )
    print(
        f"scored {report['labeled_records']} labeled MM items: "
        f"agreement={report['agreement']} kappa={report['cohen_kappa']} "
        f"passed_gate={report['passed_gate']}"
    )


def add_audit_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--regime", default="change")
    parser.add_argument("--sample", type=Path, default=DEFAULT_OUTPUT_ROOT / "mm_calibration" / "change_sample.jsonl")
    parser.add_argument(
        "--labels",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT / "mm_calibration" / "change_sample.human_labels.jsonl",
    )
    parser.add_argument(
        "--score-report",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT / "mm_calibration" / "change_sample.human_report.json",
    )
    parser.add_argument(
        "--freeze-audit",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT / "mm_calibration" / "change_sample.human_labels.freeze_audit.json",
    )
    parser.add_argument(
        "--synthetic-report",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT / "mm_calibration" / "change_sample.synthetic_report.json",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "mm_calibration" / "mm_calibration_gate.json")
    parser.add_argument("--min-items", type=int, default=DEFAULT_MIN_ITEMS)
    parser.add_argument("--min-agreement", type=float, default=DEFAULT_MIN_AGREEMENT)
    parser.add_argument("--min-kappa", type=float, default=DEFAULT_MIN_KAPPA)
    parser.add_argument(
        "--label-source",
        choices=["human", "human_calibration", "human_review", "synthetic", "unknown"],
        default="unknown",
    )


def run_audit_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.output, purpose="MM calibration gate audit")
    ensure_dir(args.output.parent)
    report = audit_calibration_gate(
        regime=args.regime,
        sample_path=args.sample,
        labels_path=args.labels,
        score_report_path=args.score_report,
        synthetic_report_path=args.synthetic_report,
        freeze_audit_path=args.freeze_audit,
        output_path=args.output,
        min_items=args.min_items,
        min_agreement=args.min_agreement,
        min_kappa=args.min_kappa,
        label_source=args.label_source,
    )
    print(
        f"MM calibration gate: sample={report['sample_items']} "
        f"sample_gate={report['sample_gate_passed']} "
        f"human_labeled={report['human_labeled_records']} "
        f"agreement={report['agreement']} kappa={report['cohen_kappa']} "
        f"publishable={report['publishable']}"
    )
