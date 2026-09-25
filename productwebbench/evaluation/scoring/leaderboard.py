from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any

from ...core.config import DEFAULT_OUTPUT_ROOT
from ...core.io_utils import ensure_dir, read_jsonl, write_json
from ...evalkit.calibration import VALID_JUDGE_LABELS, judge_agreement
from ...evalkit.mm_judge import CONTINUOUS_SCORE_FIELDS, HUMAN_LABEL_LEAK_FIELDS, temperature_is_zero
from ...regimes.construction.scoring import score_trajectory
from .metrics import METRIC_NAMES


PRIMARY_SCORE_NAME = "WCS pass@1"
DEFAULT_PROTOCOL_AUDIT = DEFAULT_OUTPUT_ROOT / "eval_protocol" / "protocol_audit.json"
MM_PREDICTION_FILENAMES = (
    "mm_judge_predictions.jsonl",
    "mm_judge_labels.jsonl",
    "judge_predictions.jsonl",
    "judge_labels.jsonl",
    "mm_predictions.jsonl",
    "mm_judge_report.json",
    "judge_report.json",
)
MM_HUMAN_LABEL_ARTIFACT_TYPE = "mm_human_label_item"
MM_HUMAN_LABEL_FREEZE_AUDIT_ARTIFACT_TYPE = "mm_human_label_freeze_audit"
PUBLISHABLE_MM_LABEL_SOURCES = {"human", "human_calibration", "human_review"}
LEADERBOARD_VIEWS = {
    "change_lm": {
        "regime": "change",
        "block": "lm",
        "primary_score": PRIMARY_SCORE_NAME,
        "implemented_in_runner": True,
        "description": "Continuity under Change language-model view: patches are scored by deterministic browser-state verifier pass/fail.",
    },
    "change_mm": {
        "regime": "change",
        "block": "mm",
        "primary_score": "JA_B",
        "implemented_in_runner": True,
        "description": "Continuity under Change multimodal judge view: only publishable after human JA/kappa calibration passes.",
    },
    "construction_lm": {
        "regime": "construction",
        "block": "lm",
        "primary_score": "TCS/TD/ITR",
        "implemented_in_runner": True,
        "description": "Continuity under Construction language-model view: trajectory success, depth, and intra-trajectory regression.",
    },
    "construction_mm": {
        "regime": "construction",
        "block": "mm",
        "primary_score": "JA_A",
        "implemented_in_runner": True,
        "description": "Continuity under Construction multimodal judge view: only publishable after human JA/kappa calibration passes.",
    },
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def file_sha256(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit_input_file_digests(audit: dict[str, Any], *, prefix: str) -> list[str]:
    issues: list[str] = []
    for key, item in (audit.get("input_files") or {}).items():
        if not isinstance(item, dict):
            issues.append(f"{prefix} input_files.{key} is malformed")
            continue
        raw_path = item.get("path")
        expected_sha = item.get("sha256")
        path = Path(str(raw_path)) if raw_path else None
        if file_sha256(path) != expected_sha:
            issues.append(f"{prefix} input {key} changed or is missing")
    return issues


def load_statuses(run_root: Path) -> dict[str, dict[str, dict[str, Any]]]:
    statuses: dict[str, dict[str, dict[str, Any]]] = {}
    for path in sorted(run_root.glob("*/*/status.json")):
        model_slug = path.parent.parent.name
        status = load_json(path)
        task_id = status.get("task_id") or path.parent.name
        statuses.setdefault(model_slug, {})[task_id] = status
    return statuses


def load_construction_reports(run_root: Path) -> dict[str, dict[str, dict[str, Any]]]:
    reports: dict[str, dict[str, dict[str, Any]]] = {}
    names = (
        "trajectory_report.json",
        "construction_trajectory_run_report.json",
        "trajectory_run_report.json",
    )
    for name in names:
        for path in sorted(run_root.glob(f"*/*/{name}")):
            model_slug = path.parent.parent.name
            report = load_json(path)
            task_id = report.get("task_id") or path.parent.name
            reports.setdefault(model_slug, {})[task_id] = report
    return reports


def default_mm_human_labels_path(view: str) -> Path:
    regime = LEADERBOARD_VIEWS.get(view, {}).get("regime") or "change"
    return DEFAULT_OUTPUT_ROOT / "mm_calibration" / f"{regime}_sample.human_labels.jsonl"


def default_mm_human_label_freeze_audit_path(labels_path: Path, view: str) -> Path:
    if labels_path.suffix:
        return labels_path.with_suffix(".freeze_audit.json")
    regime = LEADERBOARD_VIEWS.get(view, {}).get("regime") or "change"
    return DEFAULT_OUTPUT_ROOT / "mm_calibration" / f"{regime}_sample.human_labels.freeze_audit.json"


def label_value(record: dict[str, Any], fields: tuple[str, ...]) -> tuple[str | None, str | None]:
    for field in fields:
        if field not in record or record.get(field) is None:
            continue
        raw = str(record.get(field)).strip().lower()
        if raw in VALID_JUDGE_LABELS:
            return raw, None
        return None, raw
    return None, None


def json_records(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".jsonl":
        return list(read_jsonl(path))
    payload = load_json(path)
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    for key in ("items", "predictions", "labels", "checkpoints", "results"):
        values = payload.get(key)
        if isinstance(values, list):
            return [item for item in values if isinstance(item, dict)]
    return []


def load_mm_human_labels(labels_path: Path, view: str) -> tuple[dict[str, dict[str, Any]], list[str]]:
    expected_regime = LEADERBOARD_VIEWS.get(view, {}).get("regime")
    issues: list[str] = []
    if not labels_path.exists():
        return {}, [f"MM human label file missing: {labels_path}"]

    labels: dict[str, dict[str, Any]] = {}
    duplicate_checkpoint_ids: set[str] = set()
    skipped_regime = 0
    invalid_labels: list[str] = []
    missing_checkpoint_ids = 0
    missing_human_labels = 0
    missing_task_ids = 0
    missing_frozen_artifact_type = 0
    wrong_frozen_artifact_type = 0
    wrong_formal_task_record = 0
    invalid_label_sources: list[str] = []
    missing_frozen_judge_labels = 0
    invalid_frozen_judge_labels: list[str] = []
    for index, record in enumerate(read_jsonl(labels_path), start=1):
        checkpoint_id = record.get("checkpoint_id")
        if not checkpoint_id:
            missing_checkpoint_ids += 1
            continue
        checkpoint_id = str(checkpoint_id)
        regime = record.get("regime")
        if expected_regime and regime and regime != expected_regime:
            skipped_regime += 1
            continue
        human_label, invalid = label_value(record, ("human_label", "gold_label", "reference_label", "label"))
        if invalid is not None:
            invalid_labels.append(f"line {index} checkpoint {checkpoint_id}: {invalid}")
            continue
        if human_label is None:
            missing_human_labels += 1
            continue
        artifact_type = record.get("artifact_type")
        if artifact_type is None:
            missing_frozen_artifact_type += 1
            continue
        if artifact_type != MM_HUMAN_LABEL_ARTIFACT_TYPE:
            wrong_frozen_artifact_type += 1
            continue
        if record.get("formal_task_record") is not False:
            wrong_formal_task_record += 1
            continue
        label_source = str(record.get("label_source") or "").strip().lower()
        if label_source not in PUBLISHABLE_MM_LABEL_SOURCES:
            invalid_label_sources.append(f"line {index} checkpoint {checkpoint_id}: {label_source or '<missing>'}")
            continue
        frozen_judge_label, invalid_judge_label = label_value(record, ("judge_label",))
        if invalid_judge_label is not None:
            invalid_frozen_judge_labels.append(f"line {index} checkpoint {checkpoint_id}: {invalid_judge_label}")
            continue
        if frozen_judge_label is None:
            missing_frozen_judge_labels += 1
            continue
        if checkpoint_id in labels:
            duplicate_checkpoint_ids.add(checkpoint_id)
            continue
        task_id = str(record.get("task_id") or "")
        if not task_id:
            missing_task_ids += 1
            task_id = "__missing_task_id__"
        labels[checkpoint_id] = {
            **record,
            "checkpoint_id": checkpoint_id,
            "human_label": human_label,
            "task_id": task_id,
        }

    if duplicate_checkpoint_ids:
        issues.append(f"MM human label file has {len(duplicate_checkpoint_ids)} duplicate checkpoint ids")
    if invalid_labels:
        issues.append(f"MM human label file has {len(invalid_labels)} invalid labels")
    if missing_checkpoint_ids:
        issues.append(f"MM human label file has {missing_checkpoint_ids} records without checkpoint_id")
    if missing_human_labels:
        issues.append(f"MM human label file has {missing_human_labels} records without human_label")
    if missing_frozen_artifact_type:
        issues.append(
            f"MM human label file has {missing_frozen_artifact_type} records without artifact_type={MM_HUMAN_LABEL_ARTIFACT_TYPE}"
        )
    if wrong_frozen_artifact_type:
        issues.append(f"MM human label file has {wrong_frozen_artifact_type} records with wrong frozen label artifact_type")
    if wrong_formal_task_record:
        issues.append(f"MM human label file has {wrong_formal_task_record} records not marked formal_task_record=false")
    if invalid_label_sources:
        issues.append(f"MM human label file has {len(invalid_label_sources)} non-human label_source records")
    if missing_frozen_judge_labels:
        issues.append(f"MM human label file has {missing_frozen_judge_labels} frozen records without judge_label")
    if invalid_frozen_judge_labels:
        issues.append(f"MM human label file has {len(invalid_frozen_judge_labels)} invalid frozen judge_label values")
    if missing_task_ids:
        issues.append(f"MM human label file has {missing_task_ids} records without task_id")
    if skipped_regime:
        issues.append(f"MM human label file skipped {skipped_regime} records outside regime {expected_regime}")
    if not labels:
        issues.append("MM human label file has no usable labels for this view")
    return labels, issues


def audit_mm_human_label_freeze(
    freeze_audit_path: Path,
    labels_path: Path,
    usable_human_labels: int,
    view: str,
) -> tuple[dict[str, Any], list[str]]:
    issues: list[str] = []
    expected_regime = LEADERBOARD_VIEWS.get(view, {}).get("regime")
    if not freeze_audit_path.exists():
        return (
            {
                "path": str(freeze_audit_path),
                "available": False,
                "passed": False,
                "frozen_label_records": 0,
                "issue_count": 1,
            },
            [f"MM human-label freeze audit missing: {freeze_audit_path}"],
        )
    audit = load_json(freeze_audit_path)
    issues.extend(audit_input_file_digests(audit, prefix="MM human-label freeze audit"))
    if audit.get("artifact_type") != MM_HUMAN_LABEL_FREEZE_AUDIT_ARTIFACT_TYPE:
        issues.append(f"MM human-label freeze audit has wrong artifact_type: {audit.get('artifact_type')}")
    if audit.get("formal_task_record") is not False:
        issues.append("MM human-label freeze audit must be formal_task_record=false")
    if audit.get("passed") is not True:
        issues.append("MM human-label freeze audit is not passed")
    if audit.get("publishable_candidate") is not True:
        issues.append("MM human-label freeze audit is not publishable_candidate=true")
    if audit.get("output_path") and Path(str(audit.get("output_path"))).resolve() != labels_path.resolve():
        issues.append(f"MM human-label freeze output_path does not match labels path: {audit.get('output_path')}")
    if expected_regime:
        labels_name = labels_path.name.lower()
        audit_sample = str(audit.get("sample_path") or "").lower()
        if expected_regime not in labels_name and expected_regime not in audit_sample:
            issues.append(f"MM human-label freeze audit does not appear to match regime {expected_regime}")
    frozen_records = int(audit.get("frozen_label_records") or 0)
    if frozen_records != usable_human_labels:
        issues.append(
            f"MM human-label freeze record count {frozen_records} != usable human labels {usable_human_labels}"
        )
    if int(audit.get("issue_count") or 0) != 0:
        issues.append(f"MM human-label freeze audit issue_count is nonzero: {audit.get('issue_count')}")
    for issue in audit.get("issues", []) or []:
        issues.append(f"MM human-label freeze audit issue: {issue}")
    summary = {
        "path": str(freeze_audit_path),
        "available": True,
        "passed": bool(audit.get("passed")) and not issues,
        "artifact_type": audit.get("artifact_type"),
        "formal_task_record": audit.get("formal_task_record"),
        "publishable_candidate": audit.get("publishable_candidate"),
        "frozen_label_records": frozen_records,
        "audit_issue_count": int(audit.get("issue_count") or 0),
        "output_path": audit.get("output_path"),
    }
    return summary, issues


def load_mm_predictions(run_root: Path) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, list[str]]]:
    predictions: dict[str, dict[str, dict[str, Any]]] = {}
    issues: dict[str, list[str]] = {}
    if not run_root.exists():
        return predictions, {"_global": [f"run root missing: {run_root}"]}

    for model_dir in sorted(path for path in run_root.iterdir() if path.is_dir()):
        model_slug = model_dir.name
        model_issues: list[str] = []
        for task_dir in sorted(path for path in model_dir.iterdir() if path.is_dir()):
            for filename in MM_PREDICTION_FILENAMES:
                path = task_dir / filename
                if not path.exists():
                    continue
                try:
                    records = json_records(path)
                except (json.JSONDecodeError, OSError) as exc:
                    model_issues.append(f"{path}: failed to read predictions: {exc}")
                    continue
                for index, record in enumerate(records, start=1):
                    checkpoint_id = record.get("checkpoint_id")
                    if not checkpoint_id:
                        model_issues.append(f"{path}:{index}: missing checkpoint_id")
                        continue
                    checkpoint_id = str(checkpoint_id)
                    leaked_human_fields = sorted(field for field in HUMAN_LABEL_LEAK_FIELDS if field in record)
                    if leaked_human_fields:
                        model_issues.append(
                            f"{path}:{index}: prediction leaks human/gold labels for checkpoint {checkpoint_id}: "
                            + ", ".join(leaked_human_fields)
                        )
                        continue
                    score_fields = sorted(field for field in CONTINUOUS_SCORE_FIELDS if field in record)
                    if score_fields:
                        model_issues.append(
                            f"{path}:{index}: prediction contains continuous scores for checkpoint {checkpoint_id}: "
                            + ", ".join(score_fields)
                        )
                        continue
                    if "temperature" in record and not temperature_is_zero(record.get("temperature")):
                        model_issues.append(
                            f"{path}:{index}: prediction temperature must be 0 for checkpoint {checkpoint_id}"
                        )
                        continue
                    judge_label, invalid = label_value(
                        record,
                        ("judge_label", "prediction", "model_label", "label", "verdict"),
                    )
                    if invalid is not None:
                        if checkpoint_id in predictions.setdefault(model_slug, {}):
                            model_issues.append(f"{path}:{index}: duplicate prediction for checkpoint {checkpoint_id}")
                            continue
                        predictions.setdefault(model_slug, {})[checkpoint_id] = {
                            **record,
                            "checkpoint_id": checkpoint_id,
                            "task_id": str(record.get("task_id") or task_dir.name),
                            "judge_label": None,
                            "invalid_label": invalid,
                            "prediction_path": str(path),
                        }
                        continue
                    if judge_label is None:
                        model_issues.append(f"{path}:{index}: missing judge_label/prediction")
                        continue
                    if checkpoint_id in predictions.setdefault(model_slug, {}):
                        model_issues.append(f"{path}:{index}: duplicate prediction for checkpoint {checkpoint_id}")
                        continue
                    predictions[model_slug][checkpoint_id] = {
                        **record,
                        "checkpoint_id": checkpoint_id,
                        "task_id": str(record.get("task_id") or task_dir.name),
                        "judge_label": judge_label,
                        "prediction_path": str(path),
                    }
        if model_issues:
            issues[model_slug] = model_issues
        elif model_slug not in predictions:
            issues[model_slug] = ["no MM prediction files found"]
    return predictions, issues


def load_build_capabilities(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    data = load_json(path)
    repos = data.get("repos", {})
    return repos if isinstance(repos, dict) else {}


def load_protocol_audit(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    return load_json(path)


def protocol_view_gate(protocol_audit: dict[str, Any], view: str) -> dict[str, Any]:
    if view not in LEADERBOARD_VIEWS:
        return {
            "view": view,
            "available": False,
            "publishable": False,
            "issues": [f"unknown leaderboard view: {view}"],
            "gate": {},
        }
    if not protocol_audit:
        return {
            "view": view,
            "available": False,
            "publishable": False,
            "issues": ["eval protocol audit report is missing"],
            "gate": {},
        }
    implemented = bool(LEADERBOARD_VIEWS[view].get("implemented_in_runner"))
    publishable = bool((protocol_audit.get("publishable_views") or {}).get(view) and implemented)
    gate = (protocol_audit.get("gates") or {}).get(view, {})
    issues = list(gate.get("issues", []))
    if not implemented:
        issues.append(f"leaderboard scorer for {view} is not implemented")
    if not publishable and not issues:
        issues.append(f"eval protocol publishable_views.{view} is false")
    return {
        "view": view,
        "available": True,
        "publishable": publishable,
        "implemented_in_runner": implemented,
        "issues": issues,
        "gate": gate,
        "phase0_ready": protocol_audit.get("phase0_ready"),
        "phase3_ready": protocol_audit.get("phase3_ready"),
    }


def model_official_status(model: dict[str, Any], view_gate: dict[str, Any]) -> str:
    if not view_gate.get("available"):
        return "protocol_unverified"
    if not view_gate.get("publishable"):
        return "blocked_by_eval_protocol"
    if int(model.get("blocking_issue_count") or 0) != 0:
        return "provisional_infra_unresolved"
    if int(model.get("unresolved_infra") or 0) != 0:
        return "provisional_infra_unresolved"
    return "final"


def expected_task_ids(statuses: dict[str, dict[str, dict[str, Any]]], tasks_path: Path | None) -> list[str]:
    if tasks_path is not None:
        return [task["task_id"] for task in read_jsonl(tasks_path)]
    task_ids = set()
    for rows in statuses.values():
        task_ids.update(rows)
    return sorted(task_ids)


def expected_mm_task_ids(labels: dict[str, dict[str, Any]], tasks_path: Path | None) -> list[str]:
    label_task_ids = {record.get("task_id") for record in labels.values() if record.get("task_id")}
    if tasks_path is not None:
        return [task["task_id"] for task in read_jsonl(tasks_path) if task.get("task_id") in label_task_ids]
    return sorted(label_task_ids)


def infer_failure_type(status: dict[str, Any] | None, build_capabilities: dict[str, dict[str, Any]] | None = None) -> str:
    if status is None:
        return "missing_status"
    if int(status.get("normalized_passed") or 0) > 0:
        return "passed"
    explicit = status.get("failure_type")
    if explicit:
        return str(explicit)
    error = str(status.get("error") or "").lower()
    if not status.get("generation_ok"):
        if status.get("phase") == "generation" and not error:
            return "runner_interrupted"
        if "timed out" in error or "curl: (28)" in error or "timeout" in error:
            return "provider_timeout"
        return "provider_error"
    if not status.get("patch_applied"):
        return "model_patch_failed"
    if status.get("capture_failure_stage") == "build":
        repo_id = status.get("repo_id")
        entry = (build_capabilities or {}).get(repo_id)
        if entry and entry.get("build_ok") is False:
            return "infra_build_environment"
        return "model_build_failed"
    if status.get("capture_status") not in (None, "passed"):
        stage = status.get("capture_failure_stage")
        if stage == "capture":
            repo_id = status.get("repo_id")
            entry = (build_capabilities or {}).get(repo_id)
            if entry and entry.get("capture_ok") is False:
                return "infra_capture_environment"
        return f"capture_failed:{stage}" if stage else "capture_failed"
    if status.get("capture_ok") is False and status.get("phase") == "done":
        stage = status.get("capture_failure_stage")
        if stage == "capture":
            repo_id = status.get("repo_id")
            entry = (build_capabilities or {}).get(repo_id)
            if entry and entry.get("capture_ok") is False:
                return "infra_capture_environment"
        return f"capture_failed:{stage}" if stage else "capture_failed"
    if status.get("phase") == "done":
        return "verifier_failed"
    if status.get("phase") == "failed":
        return "runner_failed"
    return "unknown_failed"


def infer_construction_failure_type(report: dict[str, Any] | None) -> str:
    if report is None:
        return "missing_trajectory_report"
    score = score_trajectory(report)
    if bool(score.get("TCS", {}).get("passed")):
        return "passed"
    milestones = score.get("milestones", [])
    if any(int(item.get("regression_events") or 0) > 0 for item in milestones):
        return "construction_regression"
    first_failed = next((item for item in milestones if not item.get("passed")), None)
    if first_failed:
        if not first_failed.get("hard_passed"):
            return first_failed.get("failure_type") or "construction_hard_failed"
        if not first_failed.get("metric_passed"):
            return first_failed.get("failure_type") or "construction_metric_failed"
        return first_failed.get("failure_type") or "construction_trajectory_failed"
    return "construction_trajectory_failed"


def score_report_path(run_root: Path, model_slug: str, task_id: str) -> Path | None:
    task_dir = run_root / model_slug / task_id
    candidates = [
        task_dir / "score_report.normalized.json",
        task_dir / "score_report.normalized.current.json",
        task_dir / "score_report.raw.json",
        task_dir / "score_report.raw.current.json",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def load_partial_scores(run_root: Path, model_slug: str, task_id: str) -> tuple[float | None, dict[str, float]]:
    path = score_report_path(run_root, model_slug, task_id)
    if path is None:
        return None, {}
    try:
        report = load_json(path)
    except json.JSONDecodeError:
        return None, {}
    items = report.get("items") or []
    if not items:
        return None, {}
    item = items[0]
    partial = item.get("mean_available_metric_score")
    metrics: dict[str, float] = {}
    for name, metric in (item.get("metrics") or {}).items():
        if metric.get("available") and metric.get("score") is not None:
            metrics[name] = float(metric["score"])
    return (float(partial) if partial is not None else None), metrics


def summarize_model(
    run_root: Path,
    model_slug: str,
    rows: dict[str, dict[str, Any]],
    task_ids: list[str],
    build_capabilities: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    task_results = []
    failure_counts: dict[str, int] = {}
    metric_values: dict[str, list[float]] = {name: [] for name in METRIC_NAMES}
    partial_verified_values: list[float] = []
    partial_all_values: list[float] = []

    for task_id in task_ids:
        status = rows.get(task_id)
        passed = int((status or {}).get("normalized_passed") or 0) > 0
        failure_type = infer_failure_type(status, build_capabilities)
        failure_counts[failure_type] = failure_counts.get(failure_type, 0) + 1
        partial, metrics = load_partial_scores(run_root, model_slug, task_id)
        if partial is not None:
            partial_verified_values.append(partial)
        partial_all_values.append(partial if partial is not None else 0.0)
        for name, value in metrics.items():
            if name in metric_values:
                metric_values[name].append(value)
        task_results.append(
            {
                "task_id": task_id,
                "passed": passed,
                "score": 1.0 if passed else 0.0,
                "failure_type": failure_type,
                "partial_score": partial,
                "phase": (status or {}).get("phase"),
                "capture_status": (status or {}).get("capture_status"),
                "capture_failure_stage": (status or {}).get("capture_failure_stage"),
                "patch_applied": bool((status or {}).get("patch_applied")),
                "patch_retry_used": bool((status or {}).get("patch_retry_used")),
            }
        )

    total = len(task_ids)
    passed_count = sum(1 for item in task_results if item["passed"])
    unresolved_infra = sum(
        count
        for key, count in failure_counts.items()
        if key
        in {
            "provider_timeout",
            "provider_error",
            "runner_interrupted",
            "missing_status",
            "runner_failed",
            "unknown_failed",
            "infra_build_environment",
            "infra_capture_environment",
        }
    )
    resolved_total = total - unresolved_infra
    done_count = sum(1 for item in task_results if rows.get(item["task_id"], {}).get("phase") == "done")
    generation_ok = sum(1 for item in task_results if rows.get(item["task_id"], {}).get("generation_ok"))
    patch_applied = sum(1 for item in task_results if rows.get(item["task_id"], {}).get("patch_applied"))
    capture_passed = sum(1 for item in task_results if rows.get(item["task_id"], {}).get("capture_status") == "passed")
    retry_used = sum(1 for item in task_results if rows.get(item["task_id"], {}).get("patch_retry_used"))

    return {
        "model_slug": model_slug,
        "total": total,
        "passed": passed_count,
        "primary_score_name": PRIMARY_SCORE_NAME,
        "primary_score": round(100.0 * passed_count / total, 4) if total else None,
        "official_score_status": "final" if unresolved_infra == 0 else "provisional_infra_unresolved",
        "unresolved_infra": unresolved_infra,
        "resolved_total": resolved_total,
        "resolved_score": round(100.0 * passed_count / resolved_total, 4) if resolved_total else None,
        "valid_success_rate": round(passed_count / done_count, 4) if done_count else None,
        "done": done_count,
        "generation_ok": generation_ok,
        "generation_ok_rate": round(generation_ok / total, 4) if total else None,
        "patch_applied": patch_applied,
        "patch_apply_rate": round(patch_applied / total, 4) if total else None,
        "capture_passed": capture_passed,
        "capture_pass_rate": round(capture_passed / total, 4) if total else None,
        "retry_used": retry_used,
        "failure_counts": dict(sorted(failure_counts.items())),
        "provider_timeout": failure_counts.get("provider_timeout", 0),
        "provider_failed": failure_counts.get("provider_timeout", 0) + failure_counts.get("provider_error", 0),
        "model_patch_failed": failure_counts.get("model_patch_failed", 0),
        "model_build_failed": failure_counts.get("model_build_failed", 0),
        "infra_build_environment": failure_counts.get("infra_build_environment", 0),
        "infra_capture_environment": failure_counts.get("infra_capture_environment", 0),
        "capture_failed": sum(count for key, count in failure_counts.items() if key.startswith("capture_failed")),
        "verifier_failed": failure_counts.get("verifier_failed", 0),
        "partial_score_all": round(100.0 * mean(partial_all_values), 4) if partial_all_values else None,
        "partial_score_verified": round(100.0 * mean(partial_verified_values), 4) if partial_verified_values else None,
        "metric_averages": {
            name: {
                "available": len(values),
                "mean": round(mean(values), 4) if values else None,
            }
            for name, values in metric_values.items()
        },
        "tasks": task_results,
    }


def summarize_construction_model(
    model_slug: str,
    rows: dict[str, dict[str, Any]],
    task_ids: list[str],
) -> dict[str, Any]:
    task_results = []
    failure_counts: dict[str, int] = {}
    tcs_values: list[float] = []
    td_values: list[float] = []
    itr_values: list[float] = []
    qs_values: list[float] = []

    for task_id in task_ids:
        report = rows.get(task_id)
        failure_type = infer_construction_failure_type(report)
        failure_counts[failure_type] = failure_counts.get(failure_type, 0) + 1
        score = score_trajectory(report or {"task_id": task_id, "milestones": []})
        tcs_score = float(score.get("TCS", {}).get("score") or 0.0)
        td_score = score.get("TD", {}).get("score")
        itr_score = score.get("ITR", {}).get("score")
        qs_score = score.get("QS", {}).get("score")
        tcs_values.append(tcs_score)
        if td_score is not None:
            td_values.append(float(td_score))
        if itr_score is not None:
            itr_values.append(float(itr_score))
        if qs_score is not None:
            qs_values.append(float(qs_score))
        task_results.append(
            {
                "task_id": task_id,
                "passed": bool(score.get("TCS", {}).get("passed")),
                "score": tcs_score,
                "failure_type": failure_type,
                "partial_score": td_score,
                "phase": "done" if report is not None else "missing",
                "capture_status": None,
                "capture_failure_stage": None,
                "patch_applied": report is not None,
                "patch_retry_used": False,
                "TCS": score.get("TCS"),
                "TD": score.get("TD"),
                "ITR": score.get("ITR"),
                "QS": score.get("QS"),
            }
        )

    total = len(task_ids)
    passed_count = sum(1 for item in task_results if item["passed"])
    unresolved_infra = failure_counts.get("missing_trajectory_report", 0)
    resolved_total = total - unresolved_infra
    return {
        "model_slug": model_slug,
        "total": total,
        "passed": passed_count,
        "primary_score_name": "TCS pass@1",
        "primary_score": round(100.0 * mean(tcs_values), 4) if tcs_values else None,
        "official_score_status": "final" if unresolved_infra == 0 else "provisional_infra_unresolved",
        "unresolved_infra": unresolved_infra,
        "resolved_total": resolved_total,
        "resolved_score": round(100.0 * passed_count / resolved_total, 4) if resolved_total else None,
        "valid_success_rate": round(passed_count / resolved_total, 4) if resolved_total else None,
        "done": total - unresolved_infra,
        "generation_ok": total - unresolved_infra,
        "generation_ok_rate": round((total - unresolved_infra) / total, 4) if total else None,
        "patch_applied": total - unresolved_infra,
        "patch_apply_rate": round((total - unresolved_infra) / total, 4) if total else None,
        "capture_passed": None,
        "capture_pass_rate": None,
        "retry_used": 0,
        "failure_counts": dict(sorted(failure_counts.items())),
        "provider_timeout": 0,
        "provider_failed": 0,
        "model_patch_failed": 0,
        "model_build_failed": 0,
        "infra_build_environment": 0,
        "infra_capture_environment": 0,
        "capture_failed": 0,
        "verifier_failed": failure_counts.get("construction_hard_failed", 0)
        + failure_counts.get("construction_metric_failed", 0)
        + failure_counts.get("construction_trajectory_failed", 0)
        + failure_counts.get("construction_regression", 0),
        "partial_score_all": round(100.0 * mean(td_values), 4) if td_values else None,
        "partial_score_verified": round(100.0 * mean(td_values), 4) if td_values else None,
        "metric_averages": {
            "TCS": {"available": len(tcs_values), "mean": round(mean(tcs_values), 4) if tcs_values else None},
            "TD": {"available": len(td_values), "mean": round(mean(td_values), 4) if td_values else None},
            "ITR": {"available": len(itr_values), "mean": round(mean(itr_values), 4) if itr_values else None},
            "QS": {"available": len(qs_values), "mean": round(mean(qs_values), 4) if qs_values else None},
        },
        "tasks": task_results,
    }


def summarize_mm_model(
    model_slug: str,
    predictions: dict[str, dict[str, Any]],
    human_labels: dict[str, dict[str, Any]],
    task_ids: list[str],
    prediction_issues: list[str],
    primary_score_name: str,
) -> dict[str, Any]:
    labels_by_task: dict[str, list[dict[str, Any]]] = {task_id: [] for task_id in task_ids}
    for label in human_labels.values():
        task_id = label.get("task_id")
        if task_id in labels_by_task:
            labels_by_task[task_id].append(label)

    task_results = []
    checkpoint_results: list[dict[str, Any]] = []
    failure_counts: dict[str, int] = {}
    valid_human: list[str] = []
    valid_predicted: list[str] = []
    exact_matches = 0
    missing_predictions = 0
    invalid_predictions = 0

    for task_id in task_ids:
        task_labels = sorted(labels_by_task.get(task_id, []), key=lambda item: item["checkpoint_id"])
        task_matches = 0
        task_valid = 0
        task_missing = 0
        task_invalid = 0
        task_failures: Counter[str] = Counter()
        for label in task_labels:
            checkpoint_id = label["checkpoint_id"]
            human_label = label["human_label"]
            prediction = predictions.get(checkpoint_id)
            judge_label = None
            score = 0.0
            if prediction is None:
                failure_type = "missing_prediction"
                missing_predictions += 1
                task_missing += 1
            elif prediction.get("invalid_label") is not None:
                failure_type = "invalid_prediction"
                invalid_predictions += 1
                task_invalid += 1
                judge_label = None
            else:
                judge_label = prediction.get("judge_label")
                if judge_label in VALID_JUDGE_LABELS:
                    valid_human.append(human_label)
                    valid_predicted.append(judge_label)
                    task_valid += 1
                    if judge_label == human_label:
                        failure_type = "passed"
                        score = 1.0
                        exact_matches += 1
                        task_matches += 1
                    else:
                        failure_type = "label_mismatch"
                else:
                    failure_type = "invalid_prediction"
                    invalid_predictions += 1
                    task_invalid += 1
            failure_counts[failure_type] = failure_counts.get(failure_type, 0) + 1
            task_failures[failure_type] += 1
            checkpoint_results.append(
                {
                    "task_id": task_id,
                    "checkpoint_id": checkpoint_id,
                    "human_label": human_label,
                    "judge_label": judge_label,
                    "score": score,
                    "failure_type": failure_type,
                    "prediction_path": (prediction or {}).get("prediction_path"),
                }
            )

        task_total = len(task_labels)
        task_results.append(
            {
                "task_id": task_id,
                "passed": bool(task_total and task_matches == task_total),
                "score": round(task_matches / task_total, 4) if task_total else None,
                "failure_type": "passed" if task_total and task_matches == task_total else "mm_checkpoint_failed",
                "partial_score": round(task_matches / task_valid, 4) if task_valid else None,
                "phase": "done" if task_total else "missing_human_labels",
                "capture_status": None,
                "capture_failure_stage": None,
                "patch_applied": None,
                "patch_retry_used": None,
                "checkpoint_total": task_total,
                "checkpoint_exact_matches": task_matches,
                "checkpoint_valid_predictions": task_valid,
                "checkpoint_missing_predictions": task_missing,
                "checkpoint_invalid_predictions": task_invalid,
                "checkpoint_failure_counts": dict(sorted(task_failures.items())),
            }
        )

    total = len(checkpoint_results)
    valid_total = len(valid_human)
    agreement_report: dict[str, Any] = {}
    if valid_total:
        agreement_report = judge_agreement(
            valid_human,
            valid_predicted,
            min_items=0,
            min_agreement=0.0,
            min_kappa=-1.0,
        ).to_json()
    agreement = agreement_report.get("agreement")
    kappa = agreement_report.get("cohen_kappa")
    blocking_issue_count = len(prediction_issues)
    unresolved_infra = missing_predictions + blocking_issue_count
    resolved_total = total - unresolved_infra
    return {
        "model_slug": model_slug,
        "total": total,
        "passed": exact_matches,
        "primary_score_name": primary_score_name,
        "primary_score": round(100.0 * exact_matches / total, 4) if total else None,
        "official_score_status": "final" if unresolved_infra == 0 else "provisional_infra_unresolved",
        "unresolved_infra": unresolved_infra,
        "blocking_issue_count": blocking_issue_count,
        "prediction_issue_count": len(prediction_issues),
        "prediction_issues": prediction_issues[:20],
        "resolved_total": resolved_total,
        "resolved_score": round(100.0 * exact_matches / resolved_total, 4) if resolved_total else None,
        "valid_success_rate": round(exact_matches / valid_total, 4) if valid_total else None,
        "done": valid_total,
        "generation_ok": valid_total,
        "generation_ok_rate": round(valid_total / total, 4) if total else None,
        "patch_applied": None,
        "patch_apply_rate": None,
        "capture_passed": None,
        "capture_pass_rate": None,
        "retry_used": 0,
        "failure_counts": dict(sorted(failure_counts.items())),
        "provider_timeout": 0,
        "provider_failed": 0,
        "model_patch_failed": 0,
        "model_build_failed": 0,
        "infra_build_environment": 0,
        "infra_capture_environment": 0,
        "capture_failed": 0,
        "verifier_failed": failure_counts.get("label_mismatch", 0) + failure_counts.get("invalid_prediction", 0),
        "missing_predictions": missing_predictions,
        "invalid_predictions": invalid_predictions,
        "label_mismatches": failure_counts.get("label_mismatch", 0),
        "valid_predictions": valid_total,
        "coverage_rate": round(valid_total / total, 4) if total else None,
        "agreement_valid_pairs": agreement,
        "cohen_kappa_valid_pairs": kappa,
        "partial_score_all": round(100.0 * float(kappa), 4) if kappa is not None else None,
        "partial_score_verified": round(100.0 * float(agreement), 4) if agreement is not None else None,
        "metric_averages": {
            "JA_exact_all": {"available": total, "mean": round(exact_matches / total, 4) if total else None},
            "JA_valid_pairs": {"available": valid_total, "mean": agreement},
            "cohen_kappa_valid_pairs": {"available": valid_total, "mean": kappa},
            "coverage_rate": {"available": total, "mean": round(valid_total / total, 4) if total else None},
        },
        "label_counts": dict(sorted(Counter(valid_predicted).items())),
        "tasks": task_results,
        "checkpoints": checkpoint_results,
    }


def primary_rule_for_view(view: str) -> str:
    if view in {"change_mm", "construction_mm"}:
        return (
            "The denominator is the human-labeled MM calibration checkpoint set. "
            "A checkpoint receives 1 only when the model's judge label exactly matches the human label. "
            "Missing predictions count as unresolved run artifacts; invalid labels count as model output protocol failures."
        )
    if view == "construction_lm":
        return (
            "Each construction task is scored from trajectory reports. TCS is the primary pass signal, "
            "with TD, ITR, and QS reported as secondary trajectory metrics."
        )
    return (
        "Each task receives 1 if normalized verifier passes and 0 otherwise. "
        "Provider, patch, capture, verifier, and missing-status failures all count as 0."
    )


def leaderboard(
    run_root: Path,
    tasks_path: Path | None,
    output_dir: Path,
    build_capabilities_path: Path | None = DEFAULT_OUTPUT_ROOT / "tasks" / "dev_next_build_capabilities.json",
    protocol_audit_path: Path | None = DEFAULT_PROTOCOL_AUDIT,
    mm_human_labels_path: Path | None = None,
    mm_human_label_freeze_audit_path: Path | None = None,
    view: str = "change_lm",
) -> dict[str, Any]:
    statuses = load_statuses(run_root)
    task_ids = expected_task_ids(statuses, tasks_path)
    build_capabilities = load_build_capabilities(build_capabilities_path)
    protocol_audit = load_protocol_audit(protocol_audit_path)
    view_gate = protocol_view_gate(protocol_audit, view)
    mm_label_audit: dict[str, Any] | None = None
    if view == "construction_lm":
        construction_reports = load_construction_reports(run_root)
        task_ids = expected_task_ids(construction_reports, tasks_path)
        models = [
            summarize_construction_model(model_slug, rows, task_ids)
            for model_slug, rows in sorted(construction_reports.items())
        ]
    elif view == "change_lm":
        models = [
            summarize_model(run_root, model_slug, rows, task_ids, build_capabilities)
            for model_slug, rows in sorted(statuses.items())
        ]
    elif view in {"change_mm", "construction_mm"}:
        labels_path = mm_human_labels_path or default_mm_human_labels_path(view)
        human_labels, label_issues = load_mm_human_labels(labels_path, view)
        freeze_audit_path = mm_human_label_freeze_audit_path or default_mm_human_label_freeze_audit_path(labels_path, view)
        freeze_audit_summary, freeze_audit_issues = audit_mm_human_label_freeze(
            freeze_audit_path,
            labels_path,
            len(human_labels),
            view,
        )
        if tasks_path is not None:
            allowed_task_ids = {task["task_id"] for task in read_jsonl(tasks_path)}
            human_labels = {
                checkpoint_id: label
                for checkpoint_id, label in human_labels.items()
                if label.get("task_id") in allowed_task_ids
            }
            if not human_labels:
                label_issues.append("MM human labels do not overlap the requested --tasks denominator")
        task_ids = expected_mm_task_ids(human_labels, tasks_path)
        mm_predictions, prediction_issues = load_mm_predictions(run_root)
        model_slugs = sorted(
            set(mm_predictions)
            | {model_slug for model_slug in prediction_issues if model_slug != "_global"}
        )
        primary_score_name = str(LEADERBOARD_VIEWS[view]["primary_score"])
        models = [
            summarize_mm_model(
                model_slug,
                mm_predictions.get(model_slug, {}),
                human_labels,
                task_ids,
                prediction_issues.get(model_slug, []),
                primary_score_name,
            )
            for model_slug in model_slugs
        ]
        global_prediction_issues = prediction_issues.get("_global", [])
        label_issue_count = len(label_issues) + len(freeze_audit_issues) + len(global_prediction_issues)
        if label_issue_count:
            view_gate = {**view_gate}
            view_gate["publishable"] = False
            view_gate["issues"] = (
                list(view_gate.get("issues", []))
                + label_issues
                + freeze_audit_issues
                + global_prediction_issues
            )
        mm_label_audit = {
            "labels_path": str(labels_path),
            "freeze_audit_path": str(freeze_audit_path),
            "freeze_audit": freeze_audit_summary,
            "usable_human_labels": len(human_labels),
            "unique_label_tasks": len(task_ids),
            "label_issue_count": label_issue_count,
            "label_issues": (label_issues + freeze_audit_issues + global_prediction_issues)[:50],
            "prediction_filenames": list(MM_PREDICTION_FILENAMES),
        }
    else:
        models = []
    for model in models:
        model["official_score_status"] = model_official_status(model, view_gate)
    models.sort(key=lambda item: (item["primary_score"] or 0.0, item["passed"], item["partial_score_all"] or 0.0), reverse=True)
    all_models_final = bool(models) and all(model["official_score_status"] == "final" for model in models)
    report = {
        "schema_version": "2026-06-18",
        "object": "Website Continuity",
        "view": view,
        "regime": LEADERBOARD_VIEWS.get(view, {}).get("regime"),
        "block": LEADERBOARD_VIEWS.get(view, {}).get("block"),
        "publishable": bool(view_gate.get("publishable") and all_models_final),
        "publish_gate": view_gate,
        "protocol": {
            "name": "Website Continuity leaderboard v2",
            "primary_score": LEADERBOARD_VIEWS.get(view, {}).get("primary_score", PRIMARY_SCORE_NAME),
            "primary_rule": primary_rule_for_view(view),
            "official_status_rule": "A model score is final only when the requested Website Continuity view passes audit-eval-protocol and unresolved infrastructure failures are zero. Provider timeouts/errors, missing statuses, runner failures, and unknown failures must be rerun before publishing a final model-comparison claim.",
            "denominator": "All expected tasks for each model.",
            "leaderboard_view": LEADERBOARD_VIEWS.get(view, {}),
            "eval_protocol_audit_path": str(protocol_audit_path) if protocol_audit_path else None,
            "secondary_scores": {
                "partial_score_all": "Mean available verifier sub-metric score over all expected tasks, with missing verifier reports counted as 0.",
                "partial_score_verified": "Mean available verifier sub-metric score over tasks with verifier score reports only.",
            },
        },
        "run_root": str(run_root),
        "tasks_path": str(tasks_path) if tasks_path else None,
        "build_capabilities_path": str(build_capabilities_path) if build_capabilities_path else None,
        "mm_label_audit": mm_label_audit,
        "task_count": len(task_ids),
        "models": models,
    }
    ensure_dir(output_dir)
    write_json(output_dir / "leaderboard.json", report)
    (output_dir / "leaderboard.md").write_text(render_markdown(report), encoding="utf-8")
    (output_dir / "leaderboard.tsv").write_text(render_tsv(report), encoding="utf-8")
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Website Continuity Leaderboard",
        "",
        f"Protocol: {report['protocol']['name']}",
        f"View: `{report.get('view')}` (`{report.get('regime')}` / `{report.get('block')}`)",
        f"Publishable: `{report.get('publishable')}`",
        f"Protocol gate publishable: `{report.get('publish_gate', {}).get('publishable')}`",
        (
            "Protocol gate issues: "
            + (
                "; ".join(f"`{issue}`" for issue in report.get("publish_gate", {}).get("issues", [])[:8])
                if report.get("publish_gate", {}).get("issues")
                else "`none`"
            )
        ),
        "",
        "| Rank | Model | Status | Pass | Total | Primary Score | Resolved Score | Unresolved Infra | Partial All | Provider Timeout | Patch Failed | Model Build Failed | Infra Build | Infra Capture | Capture Failed | Verifier Failed |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, model in enumerate(report["models"], start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(rank),
                    model["model_slug"],
                    model["official_score_status"],
                    str(model["passed"]),
                    str(model["total"]),
                    f"{model['primary_score']:.2f}" if model["primary_score"] is not None else "",
                    f"{model['resolved_score']:.2f}" if model["resolved_score"] is not None else "",
                    str(model["unresolved_infra"]),
                    f"{model['partial_score_all']:.2f}" if model["partial_score_all"] is not None else "",
                    str(model["provider_timeout"]),
                    str(model["model_patch_failed"]),
                    str(model["model_build_failed"]),
                    str(model["infra_build_environment"]),
                    str(model["infra_capture_environment"]),
                    str(model["capture_failed"]),
                    str(model["verifier_failed"]),
                ]
            )
            + " |"
        )
    lines.append("")
    lines.append(report["protocol"]["primary_rule"])
    lines.append("")
    return "\n".join(lines)


def render_tsv(report: dict[str, Any]) -> str:
    columns = [
        "view",
        "regime",
        "block",
        "leaderboard_publishable",
        "protocol_view_publishable",
        "rank",
        "model_slug",
        "official_score_status",
        "passed",
        "total",
        "primary_score",
        "resolved_score",
        "unresolved_infra",
        "resolved_total",
        "partial_score_all",
        "partial_score_verified",
        "generation_ok_rate",
        "patch_apply_rate",
        "capture_pass_rate",
        "provider_timeout",
        "provider_failed",
        "model_patch_failed",
        "model_build_failed",
        "infra_build_environment",
        "infra_capture_environment",
        "capture_failed",
        "verifier_failed",
    ]
    lines = ["\t".join(columns)]
    for rank, model in enumerate(report["models"], start=1):
        row = {
            "view": report.get("view"),
            "regime": report.get("regime"),
            "block": report.get("block"),
            "leaderboard_publishable": report.get("publishable"),
            "protocol_view_publishable": report.get("publish_gate", {}).get("publishable"),
            "rank": rank,
            **model,
        }
        lines.append("\t".join("" if row.get(col) is None else str(row.get(col)) for col in columns))
    return "\n".join(lines) + "\n"


def add_leaderboard_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, default=None, help="Optional JSONL task/package list used as the denominator. If omitted, task ids are inferred from run artifacts.")
    parser.add_argument("--build-capabilities", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_next_build_capabilities.json")
    parser.add_argument("--protocol-audit", type=Path, default=DEFAULT_PROTOCOL_AUDIT)
    parser.add_argument(
        "--mm-human-labels",
        type=Path,
        default=None,
        help="Human-labeled MM checkpoint JSONL for change_mm/construction_mm views.",
    )
    parser.add_argument(
        "--mm-human-label-freeze-audit",
        type=Path,
        default=None,
        help="Freeze audit proving MM human labels came from judge-hidden annotations plus independent judge predictions.",
    )
    parser.add_argument("--view", choices=sorted(LEADERBOARD_VIEWS), default="change_lm")
    parser.add_argument("--output-dir", type=Path)


def run_leaderboard_from_args(args: argparse.Namespace) -> None:
    output_dir = args.output_dir or (args.run_root / "leaderboard")
    report = leaderboard(
        args.run_root,
        args.tasks,
        output_dir,
        args.build_capabilities,
        protocol_audit_path=args.protocol_audit,
        mm_human_labels_path=args.mm_human_labels,
        mm_human_label_freeze_audit_path=args.mm_human_label_freeze_audit,
        view=args.view,
    )
    print(render_tsv(report), end="")
