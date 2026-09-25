from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..core.formal_data_guard import assert_not_under_formal_task_root
from ..core.io_utils import read_jsonl, write_json
from ..taxonomy.capability import (
    CHANGE_REGIME,
    CONSTRUCTION_REGIME,
    L_HARD,
    L_METRIC,
    L_SOFT,
    LM_BLOCK,
    METRICS,
    MM_BLOCK,
    SIGNAL_LAYER_KEYS,
)


ARTIFACT_TYPE = "signal_layer_audit"
LAYER_ORDER = (L_HARD, L_METRIC, L_SOFT)
REGIME_JUDGE_METRIC = {
    CHANGE_REGIME: "JA_B",
    CONSTRUCTION_REGIME: "JA_A",
}
SOFT_DESCRIPTOR_TOKENS = ("quality", "judge", "agreement", "calibrat", "separate", "soft")
MODEL_DESCRIPTOR_TOKENS = ("judge", "mllm", "vision model", "multimodal model", "llm")


def metric_specs_by_key() -> dict[str, Any]:
    return {metric.key: metric for metric in METRICS}


@dataclass(frozen=True)
class SignalCheck:
    name: str
    layer: str
    passed: bool
    score: float | None = None
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.layer not in SIGNAL_LAYER_KEYS:
            raise ValueError(f"unknown signal layer: {self.layer}")
        if self.score is not None and not 0.0 <= self.score <= 1.0:
            raise ValueError(f"score must be in [0, 1], got {self.score}")

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def checks_by_layer(checks: list[SignalCheck]) -> dict[str, list[SignalCheck]]:
    return {
        layer: [check for check in checks if check.layer == layer]
        for layer in LAYER_ORDER
    }


def hard_metric_passed(checks: list[SignalCheck], *, metric_threshold: float = 1.0) -> bool:
    for check in checks:
        if check.layer == L_HARD and not check.passed:
            return False
        if check.layer == L_METRIC:
            value = check.score if check.score is not None else (1.0 if check.passed else 0.0)
            if value < metric_threshold:
                return False
    return True


def layer_summary(checks: list[SignalCheck]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for layer, items in checks_by_layer(checks).items():
        scores = [
            item.score if item.score is not None else (1.0 if item.passed else 0.0)
            for item in items
        ]
        summary[layer] = {
            "count": len(items),
            "passed": sum(1 for item in items if item.passed),
            "failed": sum(1 for item in items if not item.passed),
            "mean_score": round(sum(scores) / len(scores), 4) if scores else None,
        }
    return summary


def _string_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _descriptor_text(value: Any) -> str:
    if isinstance(value, str):
        return value.lower()
    if isinstance(value, list):
        return " ".join(str(item).lower() for item in value)
    return str(value).lower()


def _has_any_token(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def audit_signal_layer_record(record: dict[str, Any], *, line: int | None = None) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    metrics_by_key = metric_specs_by_key()
    regime = record.get("regime")

    if record.get("formal_task_record") is False:
        errors.append("capability package records must not be authoring-only formal_task_record=false artifacts")
    if record.get("lm_task", {}).get("block") != LM_BLOCK:
        errors.append("lm_task.block must be lm")
    if record.get("mm_task", {}).get("block") != MM_BLOCK:
        errors.append("mm_task.block must be mm")

    signal_layers = record.get("gates", {}).get("signal_layers")
    if not isinstance(signal_layers, dict):
        errors.append("gates.signal_layers must be an object")
        signal_layers = {}
    for layer in LAYER_ORDER:
        layer_items = _string_items(signal_layers.get(layer))
        if layer not in signal_layers:
            errors.append(f"missing signal layer {layer}")
            continue
        if not layer_items:
            errors.append(f"signal layer {layer} must contain at least one non-empty descriptor")
            continue
        text = _descriptor_text(layer_items)
        if layer in (L_HARD, L_METRIC) and _has_any_token(text, MODEL_DESCRIPTOR_TOKENS):
            errors.append(f"{layer} descriptors must not depend on model/judge evidence")
        if layer == L_SOFT and not _has_any_token(text, SOFT_DESCRIPTOR_TOKENS):
            errors.append("L-soft descriptor must state judge/quality/calibration/separate-report role")

    primary_metrics = record.get("lm_task", {}).get("primary_metrics")
    if not isinstance(primary_metrics, list) or not primary_metrics:
        errors.append("lm_task.primary_metrics must be a non-empty list")
        primary_metrics = []
    for metric_key in primary_metrics:
        spec = metrics_by_key.get(metric_key)
        if spec is None:
            errors.append(f"unknown primary metric: {metric_key}")
            continue
        if spec.uses_mllm:
            errors.append(f"primary metric {metric_key} uses MLLM evidence")
        if not spec.deterministic:
            errors.append(f"primary metric {metric_key} is not deterministic")
        if spec.unified_family == "Judge-Agreement":
            errors.append(f"judge-agreement metric {metric_key} cannot be a primary LM metric")

    judge_metric = record.get("mm_task", {}).get("judge_metric")
    expected_judge = REGIME_JUDGE_METRIC.get(regime)
    if expected_judge and judge_metric != expected_judge:
        errors.append(f"mm_task.judge_metric must be {expected_judge} for {regime} regime")
    if judge_metric in primary_metrics:
        errors.append(f"judge metric {judge_metric} must not appear in lm_task.primary_metrics")
    judge_spec = metrics_by_key.get(str(judge_metric))
    if judge_metric and judge_spec is None:
        errors.append(f"unknown judge metric: {judge_metric}")
    elif judge_spec and judge_spec.unified_family != "Judge-Agreement":
        errors.append(f"mm_task.judge_metric {judge_metric} must be a Judge-Agreement metric")

    if record.get("mm_task", {}).get("human_calibration_required") is not True:
        errors.append("mm_task.human_calibration_required must be true")
    checkpoints = record.get("mm_task", {}).get("checkpoints")
    if checkpoints is None:
        errors.append("mm_task.checkpoints is missing")
        checkpoints = []
    if not isinstance(checkpoints, list):
        errors.append("mm_task.checkpoints must be a list")
        checkpoints = []

    checkpoint_ids: list[str] = []
    for index, checkpoint in enumerate(checkpoints, start=1):
        if not isinstance(checkpoint, dict):
            errors.append(f"mm_task.checkpoints[{index}] must be an object")
            continue
        checkpoint_id = checkpoint.get("checkpoint_id")
        if isinstance(checkpoint_id, str) and checkpoint_id:
            checkpoint_ids.append(checkpoint_id)
        else:
            errors.append(f"mm_task.checkpoints[{index}] missing checkpoint_id")
        if checkpoint.get("layer") != L_SOFT:
            errors.append(f"mm_task.checkpoints[{index}] must be L-soft")
        label_set = checkpoint.get("label_set")
        if set(label_set or []) != {"match", "partial", "mismatch"}:
            errors.append(f"mm_task.checkpoints[{index}] label_set must be match/partial/mismatch")
        if not checkpoint.get("region"):
            errors.append(f"mm_task.checkpoints[{index}] missing region")
        if not checkpoint.get("prompt"):
            warnings.append(f"mm_task.checkpoints[{index}] has no prompt")
    duplicate_checkpoint_ids = sorted(
        checkpoint_id for checkpoint_id in set(checkpoint_ids) if checkpoint_ids.count(checkpoint_id) > 1
    )
    if duplicate_checkpoint_ids:
        errors.append(f"duplicate mm checkpoint ids: {', '.join(duplicate_checkpoint_ids[:10])}")

    return {
        "line": line,
        "task_id": record.get("task_id"),
        "repo_id": record.get("repo_id"),
        "regime": regime,
        "primary_metrics": primary_metrics,
        "judge_metric": judge_metric,
        "signal_layers_present": sorted(layer for layer in LAYER_ORDER if layer in signal_layers),
        "mm_checkpoint_count": len(checkpoints),
        "errors": errors,
        "warnings": warnings,
        "passed": not errors,
    }


def audit_signal_layers(split_path: Path, output_path: Path | None = None) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    issues: list[str] = []
    if not split_path.exists():
        issues.append(f"capability split file does not exist: {split_path}")
    else:
        for line, record in enumerate(read_jsonl(split_path), start=1):
            items.append(audit_signal_layer_record(record, line=line))
    failed_items = [item for item in items if not item["passed"]]
    warning_items = [item for item in items if item["warnings"]]
    report = {
        "schema_version": "2026-06-18",
        "artifact_type": ARTIFACT_TYPE,
        "formal_task_record": False,
        "split_path": str(split_path),
        "total": len(items),
        "passed": len(items) - len(failed_items),
        "failed": len(failed_items),
        "warning_items": len(warning_items),
        "mm_checkpoint_total": sum(int(item.get("mm_checkpoint_count", 0)) for item in items),
        "invariants": [
            "Primary LM metrics must be deterministic and must not use MLLM evidence.",
            "MM checkpoints must be L-soft and reported through Judge-Agreement metrics.",
            "L-soft may affect quality reporting only; it must not control primary pass/fail.",
        ],
        "issues": issues + [
            f"{item.get('task_id') or 'line ' + str(item.get('line'))}: {error}"
            for item in failed_items
            for error in item.get("errors", [])
        ],
        "issue_count": len(issues) + sum(len(item.get("errors", [])) for item in failed_items),
        "items": items,
        "passed_all": not issues and bool(items) and not failed_items,
    }
    if output_path is not None:
        write_json(output_path, report)
    return report


def add_signal_audit_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("split_path", type=Path)
    parser.add_argument("--output", type=Path, default=None)


def run_signal_audit_from_args(args: argparse.Namespace) -> None:
    if args.output is not None:
        try:
            assert_not_under_formal_task_root(args.output, purpose="Signal-layer audit")
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    report = audit_signal_layers(args.split_path, output_path=args.output)
    print(
        f"signal-layer audit: {report['passed']}/{report['total']} passed, "
        f"failed={report['failed']}, issues={report['issue_count']}, "
        f"mm_checkpoints={report['mm_checkpoint_total']}"
    )
