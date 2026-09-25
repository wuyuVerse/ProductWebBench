from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..core.config import DEFAULT_OUTPUT_ROOT
from ..core.formal_data_guard import assert_not_under_formal_task_root
from ..core.io_utils import ensure_dir, read_jsonl, write_json, write_jsonl
from ..taxonomy.capability import CHANGE_REGIME, CONSTRUCTION_REGIME, L_SOFT
from .calibration import VALID_JUDGE_LABELS


ARTIFACT_TYPE = "mm_judge_spec_audit"
PREDICTION_AUDIT_ARTIFACT_TYPE = "mm_judge_prediction_audit"
PREDICTION_QUEUE_ARTIFACT_TYPE = "mm_judge_prediction_queue"
PREDICTION_ITEM_ARTIFACT_TYPE = "mm_judge_prediction_item"
PREDICTION_APPEND_AUDIT_ARTIFACT_TYPE = "mm_judge_prediction_append_audit"
PERTURBATION_AUDIT_ARTIFACT_TYPE = "mm_judge_perturbation_audit"
PERTURBATION_QUEUE_ARTIFACT_TYPE = "mm_judge_perturbation_queue"
PERTURBATION_ITEM_ARTIFACT_TYPE = "mm_judge_perturbation_item"
PERTURBATION_GROUP_TEMPLATE_ARTIFACT_TYPE = "mm_judge_perturbation_group_template"
PERTURBATION_GROUP_APPEND_AUDIT_ARTIFACT_TYPE = "mm_judge_perturbation_group_append_audit"
DEFAULT_CHANGE_SPLIT = DEFAULT_OUTPUT_ROOT / "capability_splits" / "change_lm_mm.jsonl"
DEFAULT_CHANGE_AUDIT = DEFAULT_OUTPUT_ROOT / "capability_splits" / "change_lm_mm.mm_judge_spec_audit.json"
DEFAULT_PREDICTION_AUDIT = DEFAULT_OUTPUT_ROOT / "mm_calibration" / "mm_judge_prediction_audit.json"
DEFAULT_CHANGE_PREDICTION_QUEUE = DEFAULT_OUTPUT_ROOT / "mm_calibration" / "change_judge_prediction_queue.json"
DEFAULT_PERTURBATION_SET = DEFAULT_OUTPUT_ROOT / "mm_calibration" / "change_perturbations.jsonl"
DEFAULT_PERTURBATION_AUDIT = DEFAULT_OUTPUT_ROOT / "mm_calibration" / "mm_judge_perturbation_audit.json"
DEFAULT_PERTURBATION_QUEUE = DEFAULT_OUTPUT_ROOT / "mm_calibration" / "change_perturbation_queue.json"
DEFAULT_PERTURBATION_GROUP_TEMPLATE = DEFAULT_OUTPUT_ROOT / "mm_calibration" / "mm_judge_perturbation_group.template.json"
PREDICTION_LABEL_FIELDS = ("judge_label", "prediction", "model_label", "label", "verdict")
RATIONALE_FIELDS = ("rationale", "reason", "explanation")
HUMAN_LABEL_LEAK_FIELDS = ("human_label", "gold_label", "reference_label")
REQUIRED_LABEL_SET = {"match", "partial", "mismatch"}
DEFAULT_MIN_PUBLISHABLE_PERTURBATION_GROUPS = 12
DEFAULT_REQUIRED_PERTURBATION_TYPES = ("position", "palette", "length", "style")
VALID_PERTURBATION_TYPES = {
    "position",
    "palette",
    "length",
    "style",
    "crop_context",
    "layout_density",
    "responsive_viewport",
}


def assert_nonformal_output(path: Path | None, *, purpose: str) -> None:
    if path is not None:
        try:
            assert_not_under_formal_task_root(path, purpose=purpose)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
BASELINE_VARIANT_KINDS = {"anchor", "baseline", "original", "control"}
PERTURBED_VARIANT_KINDS = {"perturbation", "perturbed", "variant"}
FORBIDDEN_REGION_VALUES = {
    "",
    "page",
    "full_page",
    "full-page",
    "whole_page",
    "whole-page",
    "entire_page",
    "entire-page",
    "screenshot",
    "full_screenshot",
    "full-page screenshot",
}
UNKNOWN_VALUES = {"", "unknown", "unk", "n/a", "na", "none", "null", "tbd", "todo"}
FORBIDDEN_PROMPT_PATTERNS = (
    "whole page",
    "entire page",
    "full page",
    "overall page",
    "overall website",
    "overall aesthetic",
    "aesthetic score",
    "beauty score",
    "rate the",
    "score from",
    "1-10",
    "1 to 10",
)
FORBIDDEN_CROP_VALUES = {
    "",
    "full_page",
    "full-page",
    "whole_page",
    "whole-page",
    "entire_page",
    "page_screenshot",
    "full_page_screenshot",
    "screenshot",
}
CONTINUOUS_SCORE_FIELDS = {
    "score",
    "rating",
    "score_scale",
    "rubric_score",
    "continuous_score",
    "aesthetic_score",
}


@dataclass(frozen=True)
class MMJudgeCheckpoint:
    checkpoint_id: str
    region: str
    prompt: str
    target_crop_ref: str | None = None
    current_crop_ref: str | None = None
    allowed_labels: tuple[str, ...] = ("match", "partial", "mismatch")
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        invalid = set(self.allowed_labels) - VALID_JUDGE_LABELS
        if invalid:
            raise ValueError(f"invalid labels for {self.checkpoint_id}: {sorted(invalid)}")

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MMJudgeVerdict:
    checkpoint_id: str
    label: str
    rationale: str
    model: str | None = None

    def __post_init__(self) -> None:
        if self.label not in VALID_JUDGE_LABELS:
            raise ValueError(f"invalid verdict label: {self.label}")

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def normalized(value: object) -> str:
    return str(value or "").strip().lower()


def duplicate_values(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


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
        "sha256": file_sha256(path),
    }


def load_json_or_jsonl_records(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    issues: list[str] = []
    metadata: dict[str, Any] = {}
    if not path.exists():
        return [], metadata, [f"input file missing: {path}"]
    try:
        if path.suffix == ".jsonl":
            return list(read_jsonl(path)), metadata, []
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return [], metadata, [f"could not read prediction file {path}: {exc}"]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)], metadata, []
    if not isinstance(data, dict):
        return [], metadata, [f"input file must contain a JSON object, array, or JSONL records: {path}"]
    if isinstance(data.get("metadata"), dict):
        metadata.update(data["metadata"])
    for key in ("provider", "model", "temperature", "model_family", "run_id"):
        if key in data and key not in metadata:
            metadata[key] = data[key]
    for key in ("predictions", "items", "labels", "results", "checkpoints"):
        values = data.get(key)
        if isinstance(values, list):
            return [item for item in values if isinstance(item, dict)], metadata, []
    return [data], metadata, []


def label_value(record: dict[str, Any], fields: tuple[str, ...] = PREDICTION_LABEL_FIELDS) -> tuple[str | None, str | None]:
    for field in fields:
        if field not in record:
            continue
        value = str(record.get(field) or "").strip().lower()
        if not value:
            return None, None
        if value not in VALID_JUDGE_LABELS:
            return None, f"{field}={value}"
        return value, None
    return None, None


def rationale_present(record: dict[str, Any]) -> bool:
    return any(str(record.get(field) or "").strip() for field in RATIONALE_FIELDS)


def temperature_is_zero(value: object) -> bool:
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def infer_common_record_value(records: list[dict[str, Any]], key: str) -> Any:
    if not records:
        return None
    values: list[Any] = []
    for record in records:
        value = record.get(key)
        if value is None or value == "":
            return None
        values.append(value)
    unique = {str(value) for value in values}
    if len(unique) == 1:
        return values[0]
    return None


def checkpoint_ids_from_sample(sample_path: Path | None, *, regime: str | None = None) -> tuple[set[str], list[str]]:
    if sample_path is None:
        return set(), []
    if not sample_path.exists():
        return set(), [f"sample file missing: {sample_path}"]
    ids: set[str] = set()
    issues: list[str] = []
    for index, record in enumerate(read_jsonl(sample_path), start=1):
        if regime and record.get("regime") != regime:
            continue
        checkpoint_id = str(record.get("checkpoint_id") or "")
        if not checkpoint_id:
            issues.append(f"{sample_path}:{index}: sample record missing checkpoint_id")
            continue
        ids.add(checkpoint_id)
    return ids, issues


def checkpoint_ids_from_split(split_path: Path | None, *, regime: str | None = None) -> tuple[set[str], list[str]]:
    if split_path is None:
        return set(), []
    if not split_path.exists():
        return set(), [f"capability split missing: {split_path}"]
    ids: set[str] = set()
    issues: list[str] = []
    for record in read_jsonl(split_path):
        if regime and record.get("regime") != regime:
            continue
        for checkpoint in record.get("mm_task", {}).get("checkpoints", []) or []:
            checkpoint_id = str(checkpoint.get("checkpoint_id") or "")
            if checkpoint_id:
                ids.add(checkpoint_id)
    if not ids:
        issues.append(f"capability split has no MM checkpoints for regime {regime or '<any>'}")
    return ids, issues


def prediction_record_issues(
    record: dict[str, Any],
    *,
    expected_ids: set[str] | None = None,
    require_rationale: bool = True,
) -> tuple[str, list[str]]:
    record_issues: list[str] = []
    checkpoint_id = str(record.get("checkpoint_id") or "")
    if not checkpoint_id:
        record_issues.append("missing checkpoint_id")
    elif expected_ids and checkpoint_id not in expected_ids:
        record_issues.append("checkpoint_id is outside the audited sample/split")
    artifact_type = record.get("artifact_type")
    if artifact_type is not None and artifact_type != PREDICTION_ITEM_ARTIFACT_TYPE:
        record_issues.append(f"unexpected artifact_type: {artifact_type}")
    if record.get("formal_task_record") is not False:
        record_issues.append("prediction item must be marked formal_task_record=false")
    leaked_human_fields = sorted(field for field in HUMAN_LABEL_LEAK_FIELDS if field in record)
    if leaked_human_fields:
        record_issues.append(f"prediction item leaks human/gold labels: {', '.join(leaked_human_fields)}")
    score_fields = sorted(field for field in CONTINUOUS_SCORE_FIELDS if field in record)
    if score_fields:
        record_issues.append(f"prediction item must not contain continuous scores: {', '.join(score_fields)}")
    label, invalid_label = label_value(record)
    if invalid_label is not None:
        record_issues.append(f"invalid judge label: {invalid_label}")
    if label is None and invalid_label is None:
        record_issues.append("missing judge_label/prediction")
    if require_rationale and not rationale_present(record):
        record_issues.append("missing rationale/reason/explanation")
    if "temperature" in record and not temperature_is_zero(record.get("temperature")):
        record_issues.append(f"record temperature must be 0, got {record.get('temperature')}")
    return checkpoint_id, record_issues


def prediction_record_metadata_issues(
    record: dict[str, Any],
    *,
    provider: str,
    model: str,
    temperature: float,
    model_family: str | None = None,
    run_id: str | None = None,
) -> list[str]:
    issues: list[str] = []
    if str(record.get("provider") or "").strip() != provider:
        issues.append("provider missing or inconsistent with append run")
    if str(record.get("model") or "").strip() != model:
        issues.append("model missing or inconsistent with append run")
    if not temperature_is_zero(record.get("temperature")) or not temperature_is_zero(temperature):
        issues.append(f"temperature must be 0, got {record.get('temperature')}")
    if model_family is not None and str(record.get("model_family") or "").strip() != model_family:
        issues.append("model_family missing or inconsistent with append run")
    if run_id is not None and str(record.get("run_id") or "").strip() != run_id:
        issues.append("run_id missing or inconsistent with append run")
    return issues


def checkpoint_issues(record: dict[str, Any], checkpoint: dict[str, Any], *, require_current_crop_ref: bool = False) -> list[str]:
    issues: list[str] = []
    checkpoint_id = str(checkpoint.get("checkpoint_id") or "")
    region = normalized(checkpoint.get("region"))
    prompt = normalized(checkpoint.get("prompt"))
    target_crop_ref = normalized(checkpoint.get("target_crop_ref"))
    current_crop_ref = normalized(checkpoint.get("current_crop_ref"))
    label_set = checkpoint.get("label_set") or checkpoint.get("allowed_labels") or []
    if not checkpoint_id:
        issues.append("missing checkpoint_id")
    if checkpoint.get("layer") != L_SOFT:
        issues.append(f"checkpoint layer must be {L_SOFT}")
    if region in FORBIDDEN_REGION_VALUES or "full_page" in region or "whole_page" in region:
        issues.append("region must identify a local checkpoint/crop, not a full-page judgment")
    if target_crop_ref in FORBIDDEN_CROP_VALUES:
        issues.append("target_crop_ref must identify a local/internal crop reference, not a full-page screenshot")
    if require_current_crop_ref and current_crop_ref in FORBIDDEN_CROP_VALUES:
        issues.append("current_crop_ref is required for strict local-crop auditing")
    if not isinstance(label_set, list) or set(label_set) != REQUIRED_LABEL_SET or len(label_set) != 3:
        issues.append("label_set must be exactly match/partial/mismatch")
    if "judge_label" in checkpoint or "human_label" in checkpoint:
        issues.append("checkpoint spec must not contain judge_label or human_label")
    leaked_score_fields = sorted(field for field in CONTINUOUS_SCORE_FIELDS if field in checkpoint)
    if leaked_score_fields:
        issues.append(f"checkpoint spec must not request continuous scores: {', '.join(leaked_score_fields)}")
    if prompt in UNKNOWN_VALUES:
        issues.append("prompt is missing")
    else:
        for pattern in FORBIDDEN_PROMPT_PATTERNS:
            if pattern in prompt:
                issues.append(f"prompt asks for forbidden whole-page/continuous judgment: {pattern}")
                break
    judge_metric = record.get("mm_task", {}).get("judge_metric")
    regime = record.get("regime")
    if regime == CHANGE_REGIME and judge_metric != "JA_B":
        issues.append("change MM judge_metric must be JA_B")
    if regime == CONSTRUCTION_REGIME and judge_metric != "JA_A":
        issues.append("construction MM judge_metric must be JA_A")
    return issues


def audit_mm_judge_spec(
    split_path: Path,
    output_path: Path | None = None,
    *,
    regime: str | None = None,
    min_checkpoints: int = 1,
    require_current_crop_ref: bool = False,
) -> dict[str, Any]:
    issues: list[str] = []
    records = list(read_jsonl(split_path)) if split_path.exists() else []
    if not split_path.exists():
        issues.append(f"capability split missing: {split_path}")
    if regime:
        records = [record for record in records if record.get("regime") == regime]
    checkpoint_ids: list[str] = []
    invalid_checkpoints: list[dict[str, Any]] = []
    warning_items: list[dict[str, Any]] = []
    task_count = 0
    checkpoint_count = 0
    for record in records:
        task_id = str(record.get("task_id") or "")
        mm_task = record.get("mm_task") if isinstance(record.get("mm_task"), dict) else {}
        checkpoints = mm_task.get("checkpoints") if isinstance(mm_task.get("checkpoints"), list) else []
        if checkpoints:
            task_count += 1
        if mm_task.get("human_calibration_required") is not True:
            invalid_checkpoints.append(
                {
                    "task_id": task_id,
                    "checkpoint_id": None,
                    "issues": ["mm_task.human_calibration_required must be true"],
                }
            )
        for checkpoint in checkpoints:
            checkpoint_count += 1
            checkpoint_id = str(checkpoint.get("checkpoint_id") or "")
            if checkpoint_id:
                checkpoint_ids.append(checkpoint_id)
            item_issues = checkpoint_issues(
                record,
                checkpoint,
                require_current_crop_ref=require_current_crop_ref,
            )
            if item_issues:
                invalid_checkpoints.append(
                    {
                        "task_id": task_id,
                        "checkpoint_id": checkpoint_id or None,
                        "region": checkpoint.get("region"),
                        "issues": item_issues,
                    }
                )
            if not checkpoint.get("current_crop_ref"):
                warning_items.append(
                    {
                        "task_id": task_id,
                        "checkpoint_id": checkpoint_id or None,
                        "warning": "current_crop_ref is absent; runtime runner must crop by region/state before judging",
                    }
                )
    duplicate_checkpoint_ids = duplicate_values(checkpoint_ids)
    if checkpoint_count < min_checkpoints:
        issues.append(f"MM judge checkpoints {checkpoint_count} < {min_checkpoints}")
    if duplicate_checkpoint_ids:
        issues.append(f"MM judge spec has {len(duplicate_checkpoint_ids)} duplicate checkpoint ids")
    if invalid_checkpoints:
        issues.append(f"MM judge spec has {len(invalid_checkpoints)} invalid checkpoint records")
    report = {
        "schema_version": "2026-06-19",
        "artifact_type": ARTIFACT_TYPE,
        "formal_task_record": False,
        "split_path": str(split_path),
        "regime": regime,
        "passed": not issues,
        "publishable": not issues,
        "issue_count": len(issues),
        "issues": issues,
        "task_count": task_count,
        "record_count": len(records),
        "checkpoint_count": checkpoint_count,
        "unique_checkpoint_ids": len(set(checkpoint_ids)),
        "duplicate_checkpoint_ids": duplicate_checkpoint_ids,
        "invalid_checkpoint_count": len(invalid_checkpoints),
        "invalid_checkpoints": invalid_checkpoints[:100],
        "warning_count": len(warning_items),
        "warnings": warning_items[:100],
        "require_current_crop_ref": require_current_crop_ref,
        "gate": "MM judge specs must use local L-soft checkpoints with discrete match/partial/mismatch labels; whole-page or continuous aesthetic scoring is not publishable.",
    }
    if output_path is not None:
        write_json(output_path, report)
    return report


def audit_mm_judge_predictions(
    predictions_path: Path,
    output_path: Path | None = None,
    *,
    sample_path: Path | None = None,
    split_path: Path | None = None,
    regime: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    model_family: str | None = None,
    temperature: float | None = None,
    min_predictions: int = 1,
    require_rationale: bool = True,
    allow_partial: bool = False,
) -> dict[str, Any]:
    issues: list[str] = []
    records, metadata, load_issues = load_json_or_jsonl_records(predictions_path)
    issues.extend(load_issues)

    run_provider = provider if provider is not None else metadata.get("provider")
    run_model = model if model is not None else metadata.get("model")
    run_model_family = model_family if model_family is not None else metadata.get("model_family")
    run_temperature = temperature if temperature is not None else metadata.get("temperature")
    if run_provider is None:
        run_provider = infer_common_record_value(records, "provider")
    if run_model is None:
        run_model = infer_common_record_value(records, "model")
    if run_model_family is None:
        run_model_family = infer_common_record_value(records, "model_family")
    if run_temperature is None:
        run_temperature = infer_common_record_value(records, "temperature")
    if normalized(run_provider) in UNKNOWN_VALUES:
        issues.append("MM judge prediction run is missing provider")
    if normalized(run_model) in UNKNOWN_VALUES:
        issues.append("MM judge prediction run is missing model")
    if run_temperature is None:
        issues.append("MM judge prediction run is missing temperature=0 metadata")
    elif not temperature_is_zero(run_temperature):
        issues.append(f"MM judge prediction temperature must be 0, got {run_temperature}")

    sample_ids, sample_issues = checkpoint_ids_from_sample(sample_path, regime=regime)
    split_ids, split_issues = checkpoint_ids_from_split(split_path, regime=regime)
    issues.extend(sample_issues)
    issues.extend(split_issues)
    expected_ids = sample_ids or split_ids
    require_complete = bool(sample_ids) and not allow_partial

    checkpoint_ids: list[str] = []
    invalid_records: list[dict[str, Any]] = []
    usable_records = 0
    for index, record in enumerate(records, start=1):
        checkpoint_id, record_issues = prediction_record_issues(
            record,
            expected_ids=expected_ids,
            require_rationale=require_rationale,
        )
        if run_provider is not None and record.get("provider") not in {None, "", run_provider}:
            record_issues.append("record provider is inconsistent with run provider")
        if run_model is not None and record.get("model") not in {None, "", run_model}:
            record_issues.append("record model is inconsistent with run model")
        if run_model_family is not None and record.get("model_family") not in {None, "", run_model_family}:
            record_issues.append("record model_family is inconsistent with run model_family")
        if run_temperature is not None and "temperature" in record:
            try:
                temperature_matches = float(record.get("temperature")) == float(run_temperature)
            except (TypeError, ValueError):
                temperature_matches = False
            if not temperature_matches:
                record_issues.append("record temperature is inconsistent with run temperature")
        if checkpoint_id:
            checkpoint_ids.append(checkpoint_id)
        if record_issues:
            invalid_records.append(
                {
                    "line": index,
                    "checkpoint_id": checkpoint_id or None,
                    "issues": record_issues,
                }
            )
        else:
            usable_records += 1

    duplicate_checkpoint_ids = duplicate_values(checkpoint_ids)
    missing_expected = sorted(expected_ids - set(checkpoint_ids)) if expected_ids and require_complete else []
    extra_predictions = sorted(set(checkpoint_ids) - expected_ids) if expected_ids else []
    if len(records) < min_predictions:
        issues.append(f"MM judge prediction records {len(records)} < {min_predictions}")
    if duplicate_checkpoint_ids:
        issues.append(f"MM judge predictions have {len(duplicate_checkpoint_ids)} duplicate checkpoint ids")
    if invalid_records:
        issues.append(f"MM judge predictions have {len(invalid_records)} invalid records")
    if missing_expected:
        issues.append(f"MM judge predictions missing {len(missing_expected)} expected checkpoint ids")
    if extra_predictions:
        issues.append(f"MM judge predictions include {len(extra_predictions)} checkpoint ids outside expected set")
    publish_blockers: list[str] = []
    if not sample_ids:
        publish_blockers.append("publishable prediction audits require an explicit calibration sample")
    if allow_partial:
        publish_blockers.append("--allow-partial makes this prediction audit diagnostic-only")
    if not require_complete:
        publish_blockers.append("prediction audit is not complete for the calibration sample")
    if missing_expected:
        publish_blockers.append("prediction audit is missing sampled checkpoint predictions")
    if extra_predictions:
        publish_blockers.append("prediction audit includes checkpoint ids outside the sample")
    publishable_candidate = not issues and not publish_blockers

    report = {
        "schema_version": "2026-06-19",
        "artifact_type": PREDICTION_AUDIT_ARTIFACT_TYPE,
        "formal_task_record": False,
        "predictions_path": str(predictions_path),
        "sample_path": str(sample_path) if sample_path else None,
        "split_path": str(split_path) if split_path else None,
        "input_files": {
            "predictions": input_file_record(predictions_path),
            "sample": input_file_record(sample_path),
            "split": input_file_record(split_path),
        },
        "regime": regime,
        "passed": not issues,
        "publishable_candidate": publishable_candidate,
        "publish_blockers": publish_blockers,
        "issue_count": len(issues),
        "issues": issues,
        "provider": run_provider,
        "model": run_model,
        "model_family": run_model_family,
        "temperature": run_temperature,
        "metadata": {
            **metadata,
            **({"provider": run_provider} if run_provider is not None else {}),
            **({"model": run_model} if run_model is not None else {}),
            **({"model_family": run_model_family} if run_model_family is not None else {}),
            **({"temperature": run_temperature} if run_temperature is not None else {}),
        },
        "prediction_records": len(records),
        "usable_prediction_records": usable_records,
        "unique_checkpoint_ids": len(set(checkpoint_ids)),
        "duplicate_checkpoint_ids": duplicate_checkpoint_ids,
        "invalid_record_count": len(invalid_records),
        "invalid_records": invalid_records[:100],
        "expected_checkpoint_count": len(expected_ids),
        "require_complete": require_complete,
        "missing_expected_count": len(missing_expected),
        "missing_expected_checkpoint_ids": missing_expected[:100],
        "extra_prediction_count": len(extra_predictions),
        "extra_prediction_checkpoint_ids": extra_predictions[:100],
        "require_rationale": require_rationale,
        "gate": "MM judge predictions must be discrete match/partial/mismatch labels from a temperature-0 judge run, with no human-label leakage or continuous aesthetic scores.",
    }
    if output_path is not None:
        write_json(output_path, report)
    return report


def build_mm_judge_prediction_queue(
    *,
    sample_path: Path,
    predictions_path: Path,
    output_path: Path,
    limit: int | None = None,
) -> dict[str, Any]:
    sample_records = list(read_jsonl(sample_path)) if sample_path.exists() else []
    prediction_records = list(read_jsonl(predictions_path)) if predictions_path.exists() else []
    issues: list[str] = []
    if not sample_path.exists():
        issues.append(f"sample file missing: {sample_path}")
    if predictions_path.exists() and predictions_path.suffix != ".jsonl":
        issues.append("prediction queue only supports JSONL prediction files")

    sample_ids = [str(record.get("checkpoint_id") or "") for record in sample_records if record.get("checkpoint_id")]
    prediction_ids = [
        str(record.get("checkpoint_id") or "")
        for record in prediction_records
        if record.get("checkpoint_id")
    ]
    duplicate_sample_ids = duplicate_values(sample_ids)
    duplicate_prediction_ids = duplicate_values(prediction_ids)
    if duplicate_sample_ids:
        issues.append(f"sample has {len(duplicate_sample_ids)} duplicate checkpoint ids")
    if duplicate_prediction_ids:
        issues.append(f"judge predictions have {len(duplicate_prediction_ids)} duplicate checkpoint ids")

    sample_set = set(sample_ids)
    prediction_set = set(prediction_ids)
    predictions_outside_sample = sorted(prediction_set - sample_set)
    if predictions_outside_sample:
        issues.append(f"judge predictions include {len(predictions_outside_sample)} checkpoint ids outside sample")

    invalid_predictions: list[dict[str, Any]] = []
    for index, record in enumerate(prediction_records, start=1):
        checkpoint_id, record_issues = prediction_record_issues(
            record,
            expected_ids=sample_set,
            require_rationale=True,
        )
        if record_issues:
            invalid_predictions.append(
                {
                    "line": index,
                    "checkpoint_id": checkpoint_id or None,
                    "issues": record_issues,
                }
            )
    if invalid_predictions:
        issues.append(f"judge predictions have {len(invalid_predictions)} invalid records")

    unlabeled_ids = [checkpoint_id for checkpoint_id in sample_ids if checkpoint_id not in prediction_set]
    if limit is not None:
        unlabeled_ids = unlabeled_ids[:limit]
    sample_by_id = {str(record.get("checkpoint_id")): record for record in sample_records if record.get("checkpoint_id")}
    queue_items: list[dict[str, Any]] = []
    for queue_index, checkpoint_id in enumerate(unlabeled_ids, start=1):
        item = sample_by_id[checkpoint_id]
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
                "runtime_requirements": {
                    "temperature": 0,
                    "provider": "TBD independent MM judge provider",
                    "model": "TBD independent MM judge model",
                    "model_family": "TBD independent MM judge family",
                    "run_id": "TBD reproducible run id",
                },
                "append_command": (
                    "python -m sitecontinuum append-mm-judge-prediction "
                    f"--sample {sample_path} "
                    f"--predictions {predictions_path} "
                    f"--checkpoint-id {checkpoint_id} "
                    "--judge-label <match|partial|mismatch> "
                    "--rationale '<temperature-0 judge rationale; no human_label>' "
                    "--provider <provider> "
                    "--model <model> "
                    "--model-family <model_family> "
                    "--run-id <run_id> "
                    "--temperature 0"
                ),
            }
        )

    report = {
        "schema_version": "2026-06-19",
        "artifact_type": PREDICTION_QUEUE_ARTIFACT_TYPE,
        "formal_task_record": False,
        "sample_path": str(sample_path),
        "predictions_path": str(predictions_path),
        "output_path": str(output_path),
        "input_files": {
            "sample": input_file_record(sample_path),
            "predictions": input_file_record(predictions_path),
        },
        "passed": not issues,
        "issue_count": len(issues),
        "issues": issues,
        "sample_items": len(sample_records),
        "prediction_records": len(prediction_records),
        "completed_prediction_count": len(prediction_set & sample_set),
        "missing_prediction_count": max(0, len(sample_set) - len(prediction_set & sample_set)),
        "queue_item_count": len(queue_items),
        "limit": limit,
        "duplicate_sample_ids": duplicate_sample_ids,
        "duplicate_prediction_ids": duplicate_prediction_ids,
        "predictions_outside_sample_count": len(predictions_outside_sample),
        "invalid_prediction_count": len(invalid_predictions),
        "invalid_predictions": invalid_predictions[:100],
        "queue_items": queue_items,
        "gate": "This queue schedules independent temperature-0 MM judge predictions only; it must not contain judge_label, human_label, gold_label, or continuous score outputs.",
    }
    write_json(output_path, report)
    return report


def sample_record_by_checkpoint(sample_path: Path, checkpoint_id: str) -> tuple[dict[str, Any] | None, list[str]]:
    if not sample_path.exists():
        return None, [f"sample file missing: {sample_path}"]
    records = list(read_jsonl(sample_path))
    matches = [record for record in records if str(record.get("checkpoint_id") or "") == checkpoint_id]
    if not matches:
        return None, [f"checkpoint_id not found in sample: {checkpoint_id}"]
    if len(matches) > 1:
        return matches[0], [f"checkpoint_id has {len(matches)} sample records: {checkpoint_id}"]
    return matches[0], []


def append_mm_judge_prediction(
    *,
    sample_path: Path,
    predictions_path: Path,
    checkpoint_id: str,
    judge_label: str,
    rationale: str,
    provider: str,
    model: str,
    temperature: float,
    model_family: str | None = None,
    run_id: str | None = None,
    audit_output_path: Path | None = None,
) -> dict[str, Any]:
    issues: list[str] = []
    checkpoint_id = str(checkpoint_id or "").strip()
    judge_label = normalized(judge_label)
    rationale = str(rationale or "").strip()
    provider = str(provider or "").strip()
    model = str(model or "").strip()
    model_family = str(model_family or "").strip() or None
    run_id = str(run_id or "").strip() or None

    if predictions_path.suffix != ".jsonl":
        issues.append("append-mm-judge-prediction only writes JSONL prediction files")
    if not checkpoint_id:
        issues.append("checkpoint_id is required")
    if judge_label not in VALID_JUDGE_LABELS:
        issues.append(f"judge_label must be one of {sorted(VALID_JUDGE_LABELS)}")
    if not rationale:
        issues.append("rationale is required")
    if normalized(provider) in UNKNOWN_VALUES:
        issues.append("provider is required")
    if normalized(model) in UNKNOWN_VALUES:
        issues.append("model is required")
    if not temperature_is_zero(temperature):
        issues.append(f"temperature must be 0, got {temperature}")

    sample, sample_issues = sample_record_by_checkpoint(sample_path, checkpoint_id) if checkpoint_id else (None, [])
    issues.extend(sample_issues)
    existing = list(read_jsonl(predictions_path)) if predictions_path.exists() else []
    existing_ids = [str(record.get("checkpoint_id") or "") for record in existing if record.get("checkpoint_id")]
    duplicate_existing_ids = duplicate_values(existing_ids)
    if duplicate_existing_ids:
        issues.append(f"existing judge predictions have {len(duplicate_existing_ids)} duplicate checkpoint ids")
    if checkpoint_id and checkpoint_id in set(existing_ids):
        issues.append(f"checkpoint_id already has a judge prediction: {checkpoint_id}")
    sample_ids, sample_id_issues = checkpoint_ids_from_sample(sample_path)
    issues.extend(sample_id_issues)
    invalid_existing_records: list[dict[str, Any]] = []
    for index, record in enumerate(existing, start=1):
        existing_checkpoint_id, record_issues = prediction_record_issues(
            record,
            expected_ids=sample_ids,
            require_rationale=True,
        )
        record_issues.extend(
            prediction_record_metadata_issues(
                record,
                provider=provider,
                model=model,
                model_family=model_family,
                run_id=run_id,
                temperature=temperature,
            )
        )
        if record_issues:
            invalid_existing_records.append(
                {
                    "line": index,
                    "checkpoint_id": existing_checkpoint_id or None,
                    "issues": record_issues,
                }
            )
    if invalid_existing_records:
        issues.append(f"existing judge predictions have {len(invalid_existing_records)} invalid records")

    record: dict[str, Any] | None = None
    if sample is not None and judge_label in VALID_JUDGE_LABELS and rationale and provider and model and temperature_is_zero(temperature):
        record = {
            "schema_version": "2026-06-19",
            "artifact_type": PREDICTION_ITEM_ARTIFACT_TYPE,
            "formal_task_record": False,
            "checkpoint_id": checkpoint_id,
            "task_id": sample.get("task_id"),
            "repo_id": sample.get("repo_id"),
            "regime": sample.get("regime"),
            "judge_metric": sample.get("judge_metric"),
            "region": sample.get("region"),
            "prompt": sample.get("prompt"),
            "target_crop_ref": sample.get("target_crop_ref"),
            "judge_label": judge_label,
            "rationale": rationale,
            "provider": provider,
            "model": model,
            "model_family": model_family,
            "run_id": run_id,
            "temperature": 0,
        }

    report = {
        "schema_version": "2026-06-19",
        "artifact_type": PREDICTION_APPEND_AUDIT_ARTIFACT_TYPE,
        "formal_task_record": False,
        "sample_path": str(sample_path),
        "predictions_path": str(predictions_path),
        "input_files": {
            "sample": input_file_record(sample_path),
            "predictions_before": input_file_record(predictions_path),
        },
        "checkpoint_id": checkpoint_id,
        "judge_label": judge_label,
        "provider": provider,
        "model": model,
        "model_family": model_family,
        "run_id": run_id,
        "temperature": temperature,
        "passed": not issues,
        "issue_count": len(issues),
        "issues": issues,
        "existing_prediction_count": len(existing),
        "invalid_existing_record_count": len(invalid_existing_records),
        "invalid_existing_records": invalid_existing_records[:100],
        "candidate_record": record,
        "would_write_prediction_count": len(existing) + (0 if issues else 1),
        "gate": "Append exactly one real temperature-0 MM judge prediction after inspecting one local checkpoint; no human/gold labels or formal task data are written.",
    }
    if issues:
        if audit_output_path is not None:
            write_json(audit_output_path, report)
        return report

    ensure_dir(predictions_path.parent)
    write_jsonl(predictions_path, [*existing, record])
    report["input_files"]["predictions_after"] = input_file_record(predictions_path)
    if audit_output_path is not None:
        write_json(audit_output_path, report)
    return report


def variant_kind(record: dict[str, Any]) -> str:
    return normalized(record.get("variant_kind") or record.get("role") or record.get("kind"))


def group_id_value(record: dict[str, Any]) -> str:
    return str(record.get("perturbation_group_id") or record.get("group_id") or "").strip()


def perturbation_type_value(record: dict[str, Any]) -> str:
    return normalized(record.get("perturbation_type") or record.get("bias_type"))


def perturbation_group_template() -> dict[str, Any]:
    group_id = "TBD_checkpoint_id_position_001"
    common = {
        "artifact_type": PERTURBATION_ITEM_ARTIFACT_TYPE,
        "formal_task_record": False,
        "perturbation_group_id": group_id,
        "checkpoint_id": "TBD_checkpoint_id",
        "perturbation_type": "position",
        "temperature": 0,
    }
    return {
        "schema_version": "2026-06-19",
        "artifact_type": PERTURBATION_GROUP_TEMPLATE_ARTIFACT_TYPE,
        "formal_task_record": False,
        "template_only": True,
        "template_warning": (
            "Fill this for exactly one real temperature-0 judge perturbation group after running "
            "the judge on one baseline crop and one content-preserving local variant. "
            "This template is not publishable and append-mm-judge-perturbation-group rejects it."
        ),
        "metadata": {
            "provider": "TBD",
            "model": "TBD",
            "temperature": 0,
            "run_id": "TBD_real_judge_run_id",
        },
        "items": [
            {
                **common,
                "variant_id": "baseline",
                "variant_kind": "baseline",
                "judge_label": "TBD_match_partial_or_mismatch",
                "rationale": "TBD judge rationale for the baseline local crop",
                "crop_ref": "TBD_local_baseline_crop_ref",
            },
            {
                **common,
                "variant_id": "position_variant_01",
                "variant_kind": "perturbation",
                "judge_label": "TBD_same_label_as_baseline",
                "rationale": "TBD judge rationale for the content-preserving variant",
                "crop_ref": "TBD_local_perturbed_crop_ref",
                "perturbation_description": "TBD content-preserving local position change",
            },
        ],
        "notes": "One group only. Do not batch-generate perturbation controls or labels.",
    }


def perturbation_record_basic_issues(record: dict[str, Any]) -> tuple[str, str, str, str | None, list[str]]:
    issues: list[str] = []
    group_id = group_id_value(record)
    checkpoint_id = str(record.get("checkpoint_id") or "").strip()
    ptype = perturbation_type_value(record)
    kind = variant_kind(record)
    if not group_id:
        issues.append("missing perturbation_group_id/group_id")
    if not checkpoint_id:
        issues.append("missing checkpoint_id")
    if ptype not in VALID_PERTURBATION_TYPES:
        issues.append(f"invalid perturbation_type: {ptype or '<missing>'}")
    if kind not in BASELINE_VARIANT_KINDS | PERTURBED_VARIANT_KINDS:
        issues.append(f"invalid variant_kind: {kind or '<missing>'}")
    artifact_type = record.get("artifact_type")
    if artifact_type is not None and artifact_type != PERTURBATION_ITEM_ARTIFACT_TYPE:
        issues.append(f"unexpected artifact_type: {artifact_type}")
    if record.get("formal_task_record") is not False:
        issues.append("perturbation item must be marked formal_task_record=false")
    leaked_human_fields = sorted(field for field in HUMAN_LABEL_LEAK_FIELDS if field in record)
    if leaked_human_fields:
        issues.append(f"perturbation item leaks human/gold labels: {', '.join(leaked_human_fields)}")
    score_fields = sorted(field for field in CONTINUOUS_SCORE_FIELDS if field in record)
    if score_fields:
        issues.append(f"perturbation item must not contain continuous scores: {', '.join(score_fields)}")
    label, invalid_label = label_value(record)
    if invalid_label is not None:
        issues.append(f"invalid judge label: {invalid_label}")
    if label is None and invalid_label is None:
        issues.append("missing judge_label/prediction")
    if not rationale_present(record):
        issues.append("missing rationale/reason/explanation")
    if "temperature" in record and not temperature_is_zero(record.get("temperature")):
        issues.append(f"record temperature must be 0, got {record.get('temperature')}")
    return group_id, checkpoint_id, ptype, label, issues


def build_mm_judge_perturbation_queue(
    *,
    sample_path: Path,
    perturbations_path: Path,
    output_path: Path,
    groups_per_type: int = 3,
    required_perturbation_types: tuple[str, ...] = DEFAULT_REQUIRED_PERTURBATION_TYPES,
) -> dict[str, Any]:
    sample_records = list(read_jsonl(sample_path)) if sample_path.exists() else []
    perturbation_records = list(read_jsonl(perturbations_path)) if perturbations_path.exists() else []
    issues: list[str] = []
    if not sample_path.exists():
        issues.append(f"sample file missing: {sample_path}")
    if perturbations_path.exists() and perturbations_path.suffix != ".jsonl":
        issues.append("perturbation queue only supports JSONL perturbation files")
    if groups_per_type < 1:
        issues.append("groups_per_type must be >= 1")
    invalid_required_types = sorted(set(required_perturbation_types) - VALID_PERTURBATION_TYPES)
    if invalid_required_types:
        issues.append(f"invalid required perturbation types: {', '.join(invalid_required_types)}")

    sample_ids = [str(record.get("checkpoint_id") or "") for record in sample_records if record.get("checkpoint_id")]
    duplicate_sample_ids = duplicate_values(sample_ids)
    if duplicate_sample_ids:
        issues.append(f"sample has {len(duplicate_sample_ids)} duplicate checkpoint ids")
    sample_set = set(sample_ids)
    sample_by_id = {str(record.get("checkpoint_id")): record for record in sample_records if record.get("checkpoint_id")}

    invalid_records: list[dict[str, Any]] = []
    groups: dict[str, list[dict[str, Any]]] = {}
    group_types: dict[str, str] = {}
    for index, record in enumerate(perturbation_records, start=1):
        group_id, checkpoint_id, ptype, _label, record_issues = perturbation_record_basic_issues(record)
        if checkpoint_id and sample_set and checkpoint_id not in sample_set:
            record_issues.append("checkpoint_id is outside the calibration sample")
        if record_issues:
            invalid_records.append(
                {
                    "line": index,
                    "group_id": group_id or None,
                    "checkpoint_id": checkpoint_id or None,
                    "issues": record_issues,
                }
            )
            continue
        groups.setdefault(group_id, []).append(record)
        group_types[group_id] = ptype

    invalid_groups: list[dict[str, Any]] = []
    label_flip_groups: list[dict[str, Any]] = []
    for group_id, items in sorted(groups.items()):
        baselines = [item for item in items if variant_kind(item) in BASELINE_VARIANT_KINDS]
        variants = [item for item in items if variant_kind(item) in PERTURBED_VARIANT_KINDS]
        checkpoint_set = {str(item.get("checkpoint_id") or "") for item in items}
        type_set = {perturbation_type_value(item) for item in items}
        group_issues: list[str] = []
        if len(baselines) != 1:
            group_issues.append(f"group must contain exactly one baseline/anchor item, observed {len(baselines)}")
        if not variants:
            group_issues.append("group must contain at least one perturbation item")
        if len(checkpoint_set) != 1:
            group_issues.append("all items in a perturbation group must use the same checkpoint_id")
        if len(type_set) != 1:
            group_issues.append("all items in a perturbation group must use the same perturbation_type")
        if group_issues:
            invalid_groups.append(
                {
                    "group_id": group_id,
                    "checkpoint_ids": sorted(checkpoint_set),
                    "perturbation_types": sorted(type_set),
                    "issues": group_issues,
                }
            )
            continue
        baseline_label, _ = label_value(baselines[0])
        flipped = []
        for variant in variants:
            variant_label, _ = label_value(variant)
            if variant_label != baseline_label:
                flipped.append(
                    {
                        "variant_id": variant.get("variant_id"),
                        "observed": variant_label,
                        "expected": baseline_label,
                    }
                )
        if flipped:
            label_flip_groups.append(
                {
                    "group_id": group_id,
                    "checkpoint_id": next(iter(checkpoint_set)),
                    "perturbation_type": next(iter(type_set)),
                    "flipped_variants": flipped,
                }
            )

    duplicate_variant_keys = duplicate_values(
        [
            f"{group_id_value(record)}::{record.get('variant_id') or variant_kind(record)}"
            for record in perturbation_records
            if group_id_value(record)
        ]
    )
    if invalid_records:
        issues.append(f"existing perturbations have {len(invalid_records)} invalid records")
    if invalid_groups:
        issues.append(f"existing perturbations have {len(invalid_groups)} invalid groups")
    if label_flip_groups:
        issues.append(f"existing perturbations have {len(label_flip_groups)} label-flip groups")
    if duplicate_variant_keys:
        issues.append(f"existing perturbations have {len(duplicate_variant_keys)} duplicate group/variant ids")

    existing_groups_by_type: dict[str, set[str]] = {
        ptype: {group_id for group_id, group_type in group_types.items() if group_type == ptype}
        for ptype in required_perturbation_types
    }
    used_checkpoints = {
        str(item.get("checkpoint_id") or "")
        for items in groups.values()
        for item in items
        if item.get("checkpoint_id")
    }
    checkpoint_cursor = 0
    queue_items: list[dict[str, Any]] = []
    workitem_root = output_path.parent / "perturbation_group_workitems"
    required_group_count = groups_per_type * len(required_perturbation_types)
    for perturbation_type in required_perturbation_types:
        existing_count = len(existing_groups_by_type.get(perturbation_type, set()))
        missing_count = max(0, groups_per_type - existing_count)
        for _ in range(missing_count):
            checkpoint_id = ""
            for _attempt in range(len(sample_ids)):
                candidate = sample_ids[checkpoint_cursor % len(sample_ids)] if sample_ids else ""
                checkpoint_cursor += 1
                if candidate and candidate not in used_checkpoints:
                    checkpoint_id = candidate
                    used_checkpoints.add(candidate)
                    break
            if not checkpoint_id and sample_ids:
                checkpoint_id = sample_ids[checkpoint_cursor % len(sample_ids)]
                checkpoint_cursor += 1
            sample = sample_by_id.get(checkpoint_id, {})
            group_id = f"{checkpoint_id}_{perturbation_type}_control" if checkpoint_id else f"TBD_{perturbation_type}_control"
            queue_items.append(
                {
                    "queue_index": len(queue_items) + 1,
                    "perturbation_group_id": group_id,
                    "checkpoint_id": checkpoint_id,
                    "task_id": sample.get("task_id"),
                    "repo_id": sample.get("repo_id"),
                    "regime": sample.get("regime"),
                    "judge_metric": sample.get("judge_metric"),
                    "region": sample.get("region"),
                    "prompt": sample.get("prompt"),
                    "target_crop_ref": sample.get("target_crop_ref"),
                    "perturbation_type": perturbation_type,
                    "required_variants": ["baseline", "perturbation"],
                    "workitem_path": str(workitem_root / f"{group_id}.json"),
                    "authoring_requirements": {
                        "temperature": 0,
                        "baseline": "Run the independent MM judge on the unmodified local crop.",
                        "perturbation": "Create one content-preserving local variant of the same crop and rerun the same judge.",
                        "stability_rule": "The perturbation label must match the baseline label; label flips are audit failures.",
                        "forbidden": [
                            "do not include human_label or gold_label",
                            "do not use continuous scores",
                            "do not batch-fill labels",
                            "do not judge the full page",
                        ],
                    },
                    "append_command": (
                        "python -m sitecontinuum append-mm-judge-perturbation-group "
                        f"--perturbations {perturbations_path} "
                        f"--group-file {workitem_root / (group_id + '.json')} "
                        "--provider <provider> "
                        "--model <model> "
                        "--temperature 0"
                    ),
                }
            )

    report = {
        "schema_version": "2026-06-19",
        "artifact_type": PERTURBATION_QUEUE_ARTIFACT_TYPE,
        "formal_task_record": False,
        "sample_path": str(sample_path),
        "perturbations_path": str(perturbations_path),
        "output_path": str(output_path),
        "input_files": {
            "sample": input_file_record(sample_path),
            "perturbations": input_file_record(perturbations_path),
        },
        "passed": not issues,
        "issue_count": len(issues),
        "issues": issues,
        "sample_items": len(sample_records),
        "perturbation_records": len(perturbation_records),
        "existing_group_count": len(groups),
        "existing_groups_by_type": {key: len(value) for key, value in existing_groups_by_type.items()},
        "required_group_count": required_group_count,
        "groups_per_type": groups_per_type,
        "required_perturbation_types": list(required_perturbation_types),
        "queue_item_count": len(queue_items),
        "missing_group_count": len(queue_items),
        "invalid_record_count": len(invalid_records),
        "invalid_records": invalid_records[:100],
        "invalid_group_count": len(invalid_groups),
        "invalid_groups": invalid_groups[:100],
        "label_flip_group_count": len(label_flip_groups),
        "label_flip_groups": label_flip_groups[:100],
        "duplicate_variant_keys": duplicate_variant_keys[:100],
        "queue_items": queue_items,
        "gate": "This queue schedules one manually authored MM judge perturbation group at a time; it must not contain judge_label, human_label, gold_label, or continuous score outputs.",
    }
    write_json(output_path, report)
    return report


def load_perturbation_group_file(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any], list[str], dict[str, Any]]:
    issues: list[str] = []
    if not path.exists():
        return [], {}, [f"perturbation group file missing: {path}"], {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return [], {}, [f"could not read perturbation group file {path}: {exc}"], {}
    metadata: dict[str, Any] = {}
    records: list[dict[str, Any]] = []
    if isinstance(data, dict):
        if data.get("template_only") is True:
            issues.append("perturbation group template_only=true cannot be appended")
        if data.get("artifact_type") == PERTURBATION_GROUP_TEMPLATE_ARTIFACT_TYPE:
            issues.append("perturbation group template artifact cannot be appended")
        if isinstance(data.get("metadata"), dict):
            metadata.update(data["metadata"])
        for key in ("provider", "model", "temperature", "model_family", "run_id"):
            if key in data and key not in metadata:
                metadata[key] = data[key]
        for key in ("items", "records", "perturbations", "results"):
            values = data.get(key)
            if isinstance(values, list):
                records = [item for item in values if isinstance(item, dict)]
                break
        if not records and all(key in data for key in ("checkpoint_id", "perturbation_group_id")):
            records = [data]
    elif isinstance(data, list):
        records = [item for item in data if isinstance(item, dict)]
    else:
        issues.append("perturbation group file must contain a JSON object or array")
    if not records:
        issues.append("perturbation group file contains no perturbation item records")
    for index, record in enumerate(records, start=1):
        if record.get("template_only") is True:
            issues.append(f"item {index} has template_only=true")
    return records, metadata, issues, data if isinstance(data, dict) else {}


def append_mm_judge_perturbation_group(
    perturbations_path: Path,
    group_file: Path,
    *,
    audit_output_path: Path | None = None,
    provider: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
) -> dict[str, Any]:
    records, metadata, load_issues, raw = load_perturbation_group_file(group_file)
    issues = list(load_issues)
    group_ids = sorted({group_id_value(record) for record in records if group_id_value(record)})
    ptypes = sorted({perturbation_type_value(record) for record in records if perturbation_type_value(record)})
    if len(group_ids) != 1:
        issues.append(f"append requires exactly one perturbation group, observed {len(group_ids)}")
    if len(ptypes) != 1:
        issues.append(f"append requires exactly one perturbation_type, observed {len(ptypes)}")
    candidate_required_types = tuple(ptypes) if len(ptypes) == 1 else ()
    candidate_report = audit_mm_judge_perturbations(
        group_file,
        output_path=None,
        provider=provider,
        model=model,
        temperature=temperature,
        min_groups=1,
        required_perturbation_types=candidate_required_types,
        require_rationale=True,
    )
    if not candidate_report.get("passed"):
        issues.extend(f"candidate group audit: {issue}" for issue in candidate_report.get("issues", []))

    existing = list(read_jsonl(perturbations_path)) if perturbations_path.exists() else []
    existing_group_ids = {group_id_value(record) for record in existing if group_id_value(record)}
    if group_ids and group_ids[0] in existing_group_ids:
        issues.append(f"duplicate perturbation_group_id already exists: {group_ids[0]}")
    existing_variant_keys = {
        f"{group_id_value(record)}::{record.get('variant_id') or variant_kind(record)}"
        for record in existing
        if group_id_value(record)
    }
    candidate_variant_keys = [
        f"{group_id_value(record)}::{record.get('variant_id') or variant_kind(record)}"
        for record in records
        if group_id_value(record)
    ]
    duplicate_existing_variants = sorted(set(candidate_variant_keys) & existing_variant_keys)
    if duplicate_existing_variants:
        issues.append(f"duplicate perturbation variant keys already exist: {', '.join(duplicate_existing_variants[:10])}")

    report = {
        "schema_version": "2026-06-19",
        "artifact_type": PERTURBATION_GROUP_APPEND_AUDIT_ARTIFACT_TYPE,
        "formal_task_record": False,
        "perturbations_path": str(perturbations_path),
        "group_file": str(group_file),
        "group_id": group_ids[0] if len(group_ids) == 1 else None,
        "perturbation_type": ptypes[0] if len(ptypes) == 1 else None,
        "passed": not issues,
        "issue_count": len(issues),
        "issues": issues,
        "existing_record_count": len(existing),
        "existing_group_count": len(existing_group_ids),
        "candidate_record_count": len(records),
        "would_write_record_count": len(existing) + (0 if issues else len(records)),
        "candidate_audit": {
            "passed": candidate_report.get("passed"),
            "issue_count": candidate_report.get("issue_count"),
            "group_count": candidate_report.get("group_count"),
            "record_count": candidate_report.get("record_count"),
            "label_flip_group_count": candidate_report.get("label_flip_group_count"),
        },
        "metadata": metadata or raw.get("metadata") or {},
        "gate": "Append exactly one manually produced MM judge perturbation group; never synthesize labels or batch-fill controls.",
    }
    if issues:
        if audit_output_path is not None:
            write_json(audit_output_path, report)
        return report
    ensure_dir(perturbations_path.parent)
    write_jsonl(perturbations_path, [*existing, *records])
    if audit_output_path is not None:
        write_json(audit_output_path, report)
    return report


def audit_mm_judge_perturbations(
    perturbations_path: Path,
    output_path: Path | None = None,
    *,
    provider: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    min_groups: int = 12,
    required_perturbation_types: tuple[str, ...] = DEFAULT_REQUIRED_PERTURBATION_TYPES,
    require_rationale: bool = True,
) -> dict[str, Any]:
    issues: list[str] = []
    records, metadata, load_issues = load_json_or_jsonl_records(perturbations_path)
    issues.extend(load_issues)

    run_provider = provider if provider is not None else metadata.get("provider")
    run_model = model if model is not None else metadata.get("model")
    run_temperature = temperature if temperature is not None else metadata.get("temperature")
    if normalized(run_provider) in UNKNOWN_VALUES:
        issues.append("MM judge perturbation run is missing provider")
    if normalized(run_model) in UNKNOWN_VALUES:
        issues.append("MM judge perturbation run is missing model")
    if run_temperature is None:
        issues.append("MM judge perturbation run is missing temperature=0 metadata")
    elif not temperature_is_zero(run_temperature):
        issues.append(f"MM judge perturbation temperature must be 0, got {run_temperature}")

    groups: dict[str, list[dict[str, Any]]] = {}
    invalid_records: list[dict[str, Any]] = []
    usable_records = 0
    perturbation_types: set[str] = set()
    checkpoint_ids: list[str] = []
    for index, record in enumerate(records, start=1):
        record_issues: list[str] = []
        group_id = group_id_value(record)
        checkpoint_id = str(record.get("checkpoint_id") or "")
        ptype = perturbation_type_value(record)
        kind = variant_kind(record)
        if not group_id:
            record_issues.append("missing perturbation_group_id/group_id")
        if not checkpoint_id:
            record_issues.append("missing checkpoint_id")
        else:
            checkpoint_ids.append(checkpoint_id)
        if ptype not in VALID_PERTURBATION_TYPES:
            record_issues.append(f"invalid perturbation_type: {ptype or '<missing>'}")
        else:
            perturbation_types.add(ptype)
        if kind not in BASELINE_VARIANT_KINDS | PERTURBED_VARIANT_KINDS:
            record_issues.append(f"invalid variant_kind: {kind or '<missing>'}")
        artifact_type = record.get("artifact_type")
        if artifact_type is not None and artifact_type != PERTURBATION_ITEM_ARTIFACT_TYPE:
            record_issues.append(f"unexpected artifact_type: {artifact_type}")
        if record.get("formal_task_record") is not False:
            record_issues.append("perturbation item must be marked formal_task_record=false")
        leaked_human_fields = sorted(field for field in HUMAN_LABEL_LEAK_FIELDS if field in record)
        if leaked_human_fields:
            record_issues.append(f"perturbation item leaks human/gold labels: {', '.join(leaked_human_fields)}")
        score_fields = sorted(field for field in CONTINUOUS_SCORE_FIELDS if field in record)
        if score_fields:
            record_issues.append(f"perturbation item must not contain continuous scores: {', '.join(score_fields)}")
        label, invalid_label = label_value(record)
        if invalid_label is not None:
            record_issues.append(f"invalid judge label: {invalid_label}")
        if label is None and invalid_label is None:
            record_issues.append("missing judge_label/prediction")
        if require_rationale and not rationale_present(record):
            record_issues.append("missing rationale/reason/explanation")
        if "temperature" in record and not temperature_is_zero(record.get("temperature")):
            record_issues.append(f"record temperature must be 0, got {record.get('temperature')}")
        if record_issues:
            invalid_records.append(
                {
                    "line": index,
                    "group_id": group_id or None,
                    "checkpoint_id": checkpoint_id or None,
                    "issues": record_issues,
                }
            )
            continue
        usable_records += 1
        groups.setdefault(group_id, []).append(record)

    invalid_groups: list[dict[str, Any]] = []
    flipped_groups: list[dict[str, Any]] = []
    for group_id, items in sorted(groups.items()):
        baselines = [item for item in items if variant_kind(item) in BASELINE_VARIANT_KINDS]
        perturbations = [item for item in items if variant_kind(item) in PERTURBED_VARIANT_KINDS]
        group_issues: list[str] = []
        if len(baselines) != 1:
            group_issues.append(f"group must contain exactly one baseline/anchor item, observed {len(baselines)}")
        if not perturbations:
            group_issues.append("group must contain at least one perturbation item")
        checkpoint_set = {str(item.get("checkpoint_id") or "") for item in items}
        if len(checkpoint_set) != 1:
            group_issues.append("all items in a perturbation group must use the same checkpoint_id")
        type_set = {perturbation_type_value(item) for item in items}
        if len(type_set) != 1:
            group_issues.append("all items in a perturbation group must use the same perturbation_type")
        if group_issues:
            invalid_groups.append(
                {
                    "group_id": group_id,
                    "checkpoint_ids": sorted(checkpoint_set),
                    "perturbation_types": sorted(type_set),
                    "issues": group_issues,
                }
            )
            continue
        baseline_label, _ = label_value(baselines[0])
        flipped = []
        for item in perturbations:
            item_label, _ = label_value(item)
            if item_label != baseline_label:
                flipped.append(
                    {
                        "variant_id": item.get("variant_id"),
                        "observed": item_label,
                        "expected": baseline_label,
                    }
                )
        if flipped:
            flipped_groups.append(
                {
                    "group_id": group_id,
                    "checkpoint_id": next(iter(checkpoint_set)),
                    "perturbation_type": next(iter(type_set)),
                    "baseline_label": baseline_label,
                    "flipped_variants": flipped,
                }
            )

    duplicate_variant_keys = duplicate_values(
        [
            f"{group_id_value(record)}::{record.get('variant_id') or variant_kind(record)}"
            for record in records
            if group_id_value(record)
        ]
    )
    missing_required_types = sorted(set(required_perturbation_types) - perturbation_types)
    if len(groups) < min_groups:
        issues.append(f"MM judge perturbation groups {len(groups)} < {min_groups}")
    if missing_required_types:
        issues.append(f"missing required perturbation types: {', '.join(missing_required_types)}")
    if invalid_records:
        issues.append(f"MM judge perturbations have {len(invalid_records)} invalid records")
    if invalid_groups:
        issues.append(f"MM judge perturbations have {len(invalid_groups)} invalid groups")
    if flipped_groups:
        issues.append(f"MM judge perturbations have {len(flipped_groups)} label-flip groups")
    if duplicate_variant_keys:
        issues.append(f"MM judge perturbations have {len(duplicate_variant_keys)} duplicate group/variant ids")
    publish_blockers: list[str] = []
    if min_groups < DEFAULT_MIN_PUBLISHABLE_PERTURBATION_GROUPS:
        publish_blockers.append(
            f"publishable perturbation audits require min_groups >= {DEFAULT_MIN_PUBLISHABLE_PERTURBATION_GROUPS}"
        )
    if set(required_perturbation_types) != set(DEFAULT_REQUIRED_PERTURBATION_TYPES):
        publish_blockers.append(
            "publishable perturbation audits must require position, palette, length, and style controls"
        )
    if missing_required_types:
        publish_blockers.append("perturbation audit is missing required perturbation types")
    publishable = not issues and not publish_blockers

    report = {
        "schema_version": "2026-06-19",
        "artifact_type": PERTURBATION_AUDIT_ARTIFACT_TYPE,
        "formal_task_record": False,
        "perturbations_path": str(perturbations_path),
        "input_files": {
            "perturbations": input_file_record(perturbations_path),
        },
        "passed": not issues,
        "publishable": publishable,
        "publish_blockers": publish_blockers,
        "issue_count": len(issues),
        "issues": issues,
        "provider": run_provider,
        "model": run_model,
        "temperature": run_temperature,
        "metadata": metadata,
        "record_count": len(records),
        "usable_record_count": usable_records,
        "group_count": len(groups),
        "min_groups": min_groups,
        "unique_checkpoint_ids": len(set(checkpoint_ids)),
        "perturbation_types": sorted(perturbation_types),
        "required_perturbation_types": sorted(required_perturbation_types),
        "missing_required_perturbation_types": missing_required_types,
        "invalid_record_count": len(invalid_records),
        "invalid_records": invalid_records[:100],
        "invalid_group_count": len(invalid_groups),
        "invalid_groups": invalid_groups[:100],
        "label_flip_group_count": len(flipped_groups),
        "label_flip_groups": flipped_groups[:100],
        "duplicate_variant_keys": duplicate_variant_keys[:100],
        "require_rationale": require_rationale,
        "gate": "MM judge perturbation controls must show temperature-0 discrete labels are invariant under content-preserving position/palette/length/style changes.",
    }
    if output_path is not None:
        write_json(output_path, report)
    return report


def add_audit_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--split", type=Path, default=DEFAULT_CHANGE_SPLIT)
    parser.add_argument("--output", type=Path, default=DEFAULT_CHANGE_AUDIT)
    parser.add_argument("--regime", choices=[CHANGE_REGIME, CONSTRUCTION_REGIME], default=None)
    parser.add_argument("--min-checkpoints", type=int, default=1)
    parser.add_argument("--require-current-crop-ref", action="store_true")


def run_audit_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.output, purpose="MM judge spec audit")
    ensure_dir(args.output.parent)
    report = audit_mm_judge_spec(
        args.split,
        output_path=args.output,
        regime=args.regime,
        min_checkpoints=args.min_checkpoints,
        require_current_crop_ref=args.require_current_crop_ref,
    )
    print(
        f"MM judge spec audit: passed={report['passed']} "
        f"checkpoints={report['checkpoint_count']} issues={report['issue_count']} warnings={report['warning_count']}"
    )
    if not report["passed"]:
        raise SystemExit(1)


def add_prediction_audit_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_PREDICTION_AUDIT)
    parser.add_argument("--sample", type=Path, default=None)
    parser.add_argument("--split", type=Path, default=None)
    parser.add_argument("--regime", choices=[CHANGE_REGIME, CONSTRUCTION_REGIME], default=None)
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--model-family", default=None)
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--min-predictions", type=int, default=1)
    parser.add_argument("--allow-missing-rationale", action="store_true")
    parser.add_argument("--allow-partial", action="store_true")


def run_prediction_audit_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.output, purpose="MM judge prediction audit")
    ensure_dir(args.output.parent)
    report = audit_mm_judge_predictions(
        args.predictions,
        output_path=args.output,
        sample_path=args.sample,
        split_path=args.split,
        regime=args.regime,
        provider=args.provider,
        model=args.model,
        model_family=args.model_family,
        temperature=args.temperature,
        min_predictions=args.min_predictions,
        require_rationale=not args.allow_missing_rationale,
        allow_partial=args.allow_partial,
    )
    print(
        f"MM judge prediction audit: passed={report['passed']} "
        f"records={report['prediction_records']} usable={report['usable_prediction_records']} "
        f"issues={report['issue_count']}"
    )
    if not report["passed"]:
        raise SystemExit(1)


def add_prediction_queue_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--sample", type=Path, default=DEFAULT_OUTPUT_ROOT / "mm_calibration" / "change_sample.jsonl")
    parser.add_argument("--predictions", type=Path, default=DEFAULT_OUTPUT_ROOT / "mm_calibration" / "change_judge_predictions.jsonl")
    parser.add_argument("--output", type=Path, default=DEFAULT_CHANGE_PREDICTION_QUEUE)
    parser.add_argument("--limit", type=int, default=None)


def run_prediction_queue_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.output, purpose="MM judge prediction queue")
    ensure_dir(args.output.parent)
    report = build_mm_judge_prediction_queue(
        sample_path=args.sample,
        predictions_path=args.predictions,
        output_path=args.output,
        limit=args.limit,
    )
    print(
        "MM judge prediction queue: "
        f"passed={report['passed']} completed={report['completed_prediction_count']} "
        f"missing={report['missing_prediction_count']} queue={report['queue_item_count']} "
        f"issues={report['issue_count']} output={args.output}"
    )
    if not report["passed"]:
        raise SystemExit(1)


def add_append_prediction_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--sample", type=Path, default=DEFAULT_OUTPUT_ROOT / "mm_calibration" / "change_sample.jsonl")
    parser.add_argument("--predictions", type=Path, default=DEFAULT_OUTPUT_ROOT / "mm_calibration" / "change_judge_predictions.jsonl")
    parser.add_argument("--checkpoint-id", required=True)
    parser.add_argument("--judge-label", choices=sorted(VALID_JUDGE_LABELS), required=True)
    parser.add_argument("--rationale", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-family", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--audit-output", type=Path, default=None)


def run_append_prediction_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.predictions, purpose="MM judge predictions")
    assert_nonformal_output(args.audit_output, purpose="MM judge prediction append audit")
    report = append_mm_judge_prediction(
        sample_path=args.sample,
        predictions_path=args.predictions,
        checkpoint_id=args.checkpoint_id,
        judge_label=args.judge_label,
        rationale=args.rationale,
        provider=args.provider,
        model=args.model,
        model_family=args.model_family,
        run_id=args.run_id,
        temperature=args.temperature,
        audit_output_path=args.audit_output,
    )
    print(
        "append MM judge prediction: "
        f"passed={report['passed']} checkpoint={report.get('checkpoint_id')} "
        f"predictions={report.get('would_write_prediction_count')} issues={report['issue_count']}"
    )
    if not report["passed"]:
        raise SystemExit(1)


def add_perturbation_group_template_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", type=Path, default=DEFAULT_PERTURBATION_GROUP_TEMPLATE)


def run_perturbation_group_template_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.output, purpose="MM judge perturbation group template")
    ensure_dir(args.output.parent)
    write_json(args.output, perturbation_group_template())
    print(
        "wrote non-formal MM judge perturbation group template "
        f"to {args.output}; template is not publishable"
    )


def add_perturbation_queue_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--sample", type=Path, default=DEFAULT_OUTPUT_ROOT / "mm_calibration" / "change_sample.jsonl")
    parser.add_argument("--perturbations", type=Path, default=DEFAULT_PERTURBATION_SET)
    parser.add_argument("--output", type=Path, default=DEFAULT_PERTURBATION_QUEUE)
    parser.add_argument("--groups-per-type", type=int, default=3)
    parser.add_argument(
        "--required-perturbation-type",
        action="append",
        default=None,
        help="Required perturbation type; repeatable. Defaults to position/palette/length/style.",
    )


def run_perturbation_queue_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.output, purpose="MM judge perturbation queue")
    ensure_dir(args.output.parent)
    required_types = tuple(args.required_perturbation_type or DEFAULT_REQUIRED_PERTURBATION_TYPES)
    report = build_mm_judge_perturbation_queue(
        sample_path=args.sample,
        perturbations_path=args.perturbations,
        output_path=args.output,
        groups_per_type=args.groups_per_type,
        required_perturbation_types=required_types,
    )
    print(
        "MM judge perturbation queue: "
        f"passed={report['passed']} existing_groups={report['existing_group_count']} "
        f"required={report['required_group_count']} missing={report['missing_group_count']} "
        f"queue={report['queue_item_count']} issues={report['issue_count']} output={args.output}"
    )
    if not report["passed"]:
        raise SystemExit(1)


def add_append_perturbation_group_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--perturbations", type=Path, default=DEFAULT_PERTURBATION_SET)
    parser.add_argument("--group-file", type=Path, required=True)
    parser.add_argument("--audit-output", type=Path, default=None)
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--temperature", type=float, default=None)


def run_append_perturbation_group_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.perturbations, purpose="MM judge perturbation set")
    assert_nonformal_output(args.audit_output, purpose="MM judge perturbation append audit")
    report = append_mm_judge_perturbation_group(
        args.perturbations,
        args.group_file,
        audit_output_path=args.audit_output,
        provider=args.provider,
        model=args.model,
        temperature=args.temperature,
    )
    print(
        "append MM judge perturbation group: "
        f"passed={report['passed']} group={report.get('group_id')} "
        f"candidate_records={report['candidate_record_count']} issues={report['issue_count']}"
    )
    if not report["passed"]:
        raise SystemExit(1)


def add_perturbation_audit_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--perturbations", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_PERTURBATION_AUDIT)
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--min-groups", type=int, default=12)
    parser.add_argument(
        "--required-perturbation-type",
        action="append",
        default=[],
        help="Required perturbation type; repeatable. Defaults to position/palette/length/style.",
    )
    parser.add_argument("--allow-missing-rationale", action="store_true")


def run_perturbation_audit_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.output, purpose="MM judge perturbation audit")
    ensure_dir(args.output.parent)
    required_types = tuple(args.required_perturbation_type or DEFAULT_REQUIRED_PERTURBATION_TYPES)
    report = audit_mm_judge_perturbations(
        args.perturbations,
        output_path=args.output,
        provider=args.provider,
        model=args.model,
        temperature=args.temperature,
        min_groups=args.min_groups,
        required_perturbation_types=required_types,
        require_rationale=not args.allow_missing_rationale,
    )
    print(
        f"MM judge perturbation audit: passed={report['passed']} "
        f"groups={report['group_count']} records={report['record_count']} "
        f"label_flips={report['label_flip_group_count']} issues={report['issue_count']}"
    )
    if not report["passed"]:
        raise SystemExit(1)
