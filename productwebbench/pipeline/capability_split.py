from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..core.config import DEFAULT_OUTPUT_ROOT, TASK_DIR
from ..core.formal_data_guard import assert_not_under_formal_task_root
from ..core.io_utils import ensure_dir, read_jsonl, write_json, write_jsonl
from ..evalkit.metareval import build_meta_eval, choose_canonical, evidence_candidates_for_slot
from ..regimes.construction.ledger import validate_acceptance_ledger_row
from ..taxonomy.capability import CHANGE_REGIME, CONSTRUCTION_REGIME, L_HARD, L_METRIC, L_SOFT, LM_BLOCK, MM_BLOCK
from ..taxonomy.capability import task_package_json_schema

try:
    import jsonschema
except ImportError:  # pragma: no cover - optional dependency in minimal installs
    jsonschema = None


DEFAULT_CONSTRUCTION_LEDGER = DEFAULT_OUTPUT_ROOT / "authoring_ledger" / "construction_ledger.jsonl"
DEFAULT_CHANGE_SPLIT = DEFAULT_OUTPUT_ROOT / "capability_splits" / "change_lm_mm.jsonl"
DEFAULT_CONSTRUCTION_SPLIT = DEFAULT_OUTPUT_ROOT / "capability_splits" / "construction_lm_mm.jsonl"


def load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl_if_exists(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return list(read_jsonl(path))


def specs_by_task(path: Path) -> dict[str, dict[str, Any]]:
    data = load_json_if_exists(path)
    return {item["task_id"]: item for item in data.get("tasks", [])}


def stable_id(text: str, prefix: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text.lower()).strip("_")
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    return f"{prefix}_{slug[:40]}_{digest}" if slug else f"{prefix}_{digest}"


def mm_checkpoints_from_spec(task_id: str, spec: dict[str, Any]) -> list[dict[str, Any]]:
    signals = spec.get("visible_text_signals") or spec.get("completion_text_signals") or []
    states = spec.get("visible_text_states") or spec.get("completion_states") or spec.get("submission_states") or []
    region = states[0] if states else "changed_region"
    checkpoints = []
    for index, signal in enumerate(signals[:12], start=1):
        checkpoints.append(
            {
                "checkpoint_id": stable_id(f"{task_id}_{index}_{signal}", "b_mm"),
                "layer": L_SOFT,
                "region": region,
                "target_crop_ref": "reference_state_crop",
                "label_set": ["match", "partial", "mismatch"],
                "prompt": f"Judge whether the changed region visibly satisfies this continuity signal: {signal}",
            }
        )
    return checkpoints


def existing_path(slot_dir: Path, *names: str) -> str:
    for name in names:
        candidate = slot_dir / name
        if candidate.exists():
            return str(candidate)
    return ""


def load_report(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def report_passed(path: Path) -> bool:
    report = load_report(path)
    if not report:
        return False
    if isinstance(report.get("passed"), bool):
        return bool(report["passed"])
    if isinstance(report.get("valid"), int) and isinstance(report.get("invalid"), int):
        return int(report.get("invalid", 0)) == 0 and int(report.get("valid", 0)) > 0
    if isinstance(report.get("passed"), int) and isinstance(report.get("failed"), int):
        return int(report.get("failed", 0)) == 0 and int(report.get("passed", 0)) > 0
    if isinstance(report.get("total"), int) and isinstance(report.get("failed"), int):
        return int(report.get("failed", 0)) == 0 and int(report.get("total", 0)) > 0
    if isinstance(report.get("issues"), list):
        return not report["issues"]
    return False


def gate_result(
    gate: str,
    *,
    passed: bool,
    status: str | None = None,
    report_paths: list[Path] | None = None,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "gate": gate,
        "passed": bool(passed),
        "status": status or ("passed" if passed else "failed"),
        "report_paths": [str(path) for path in report_paths or []],
        "summary": summary or {},
    }


def reports_gate(gate: str, paths: list[Path]) -> dict[str, Any]:
    existing = [path for path in paths if path.exists()]
    passed = bool(existing) and all(report_passed(path) for path in existing)
    status = "passed" if passed else ("missing" if not existing else "failed")
    return gate_result(
        gate,
        passed=passed,
        status=status,
        report_paths=existing,
        summary={"expected_reports": [str(path) for path in paths], "existing_reports": len(existing)},
    )


def metareval_gate(task_root: Path, slot_dir: Path) -> dict[str, Any]:
    slot_id = slot_dir.name
    candidates = evidence_candidates_for_slot(task_root, slot_id)
    selected = {category: choose_canonical(paths, category) for category, paths in candidates.items()}
    report = build_meta_eval(
        reference_reports=[selected["reference"]] if selected["reference"] else [],
        original_reports=[selected["original"]] if selected["original"] else [],
        bad_solution_reports=[selected["bad"]] if selected["bad"] else [],
        repeat_reports=[selected["repeat"]] if selected["repeat"] else [],
        assume_reproducible=False,
    )
    passed = bool(report["passed"])
    missing = [category for category, path in selected.items() if path is None]
    status = "passed" if passed else ("partial" if missing else "failed")
    return gate_result(
        "metareval",
        passed=passed,
        status=status,
        report_paths=[path for path in selected.values() if path is not None],
        summary={
            "reference_passed": report["reference_passed"],
            "original_failed": report["original_failed"],
            "bad_solutions_failed": report["bad_solutions_failed"],
            "reproducible": report["reproducible"],
            "missing": missing,
            "issues": report["issues"],
        },
    )


def regression_sanity_gate(task_root: Path, slot_dir: Path) -> dict[str, Any]:
    slot_id = slot_dir.name
    candidates = evidence_candidates_for_slot(task_root, slot_id)
    selected = {category: choose_canonical(paths, category) for category, paths in candidates.items()}
    report = build_meta_eval(
        reference_reports=[selected["reference"]] if selected["reference"] else [],
        original_reports=[selected["original"]] if selected["original"] else [],
        bad_solution_reports=[],
        repeat_reports=[selected["repeat"]] if selected["repeat"] else [],
        assume_reproducible=False,
    )
    passed = bool(report["reference_passed"] and report["original_failed"] and report["reproducible"])
    missing = [category for category in ("reference", "original", "repeat") if selected[category] is None]
    return gate_result(
        "regression_sanity",
        passed=passed,
        status="passed" if passed else ("partial" if missing else "failed"),
        report_paths=[selected[category] for category in ("reference", "original", "repeat") if selected[category]],
        summary={
            "reference_passed": report["reference_passed"],
            "original_failed": report["original_failed"],
            "reproducible": report["reproducible"],
            "missing": missing,
            "issues": report["issues"],
        },
    )


def build_change_gates(task_root: Path, slot_dir: Path) -> dict[str, Any]:
    source_audit = slot_dir / "source_audit.json"
    quality = slot_dir / "quality_audit.json"
    spec_reports = [
        slot_dir / "task_validation.json",
        slot_dir / "package_validation.json",
        slot_dir / "consistency_audit.json",
    ]
    return {
        "metareval": metareval_gate(task_root, slot_dir),
        "leak_audit": reports_gate("leak_audit", [source_audit]),
        "de_ai": reports_gate("de_ai", [quality]),
        "spec_completeness_precheck": reports_gate("spec_completeness_precheck", spec_reports),
        "regression_sanity": regression_sanity_gate(task_root, slot_dir),
        "signal_layers": {
            L_HARD: ["browser capture", "state artifacts", "actions", "completion/regression text"],
            L_METRIC: ["screenshot bytes", "overflow tolerance", "palette/design-anchor thresholds"],
            L_SOFT: ["MM local crop continuity checkpoints; quality-only until calibrated"],
        },
    }


def build_change_package_record(
    task: dict[str, Any],
    slot_dir: Path,
    submission_spec: dict[str, Any],
    task_root: Path,
) -> dict[str, Any]:
    required_states = task.get("required_states", [])
    return {
        "schema_version": "2026-06-18",
        "task_id": task["task_id"],
        "repo_id": task["repo_id"],
        "regime": CHANGE_REGIME,
        "capability": task.get("capability", "unclassified"),
        "intent": task.get("intent"),
        "website_type": task.get("website_type", "unknown"),
        "split": task.get("split", "dev"),
        "lm_task": {
            "block": LM_BLOCK,
            "primary_metrics": ["WCS"],
            "instructions": task.get("problem_statement", ""),
            "change_facet": task.get("continuity_facet") or task.get("family"),
            "required_states": required_states,
        },
        "mm_task": {
            "block": MM_BLOCK,
            "judge_metric": "JA_B",
            "human_calibration_required": True,
            "checkpoints": mm_checkpoints_from_spec(task["task_id"], submission_spec),
        },
        "evidence": {
            "capture_states": required_states,
            "design_anchors": existing_path(slot_dir, "design_anchors.json", "design_anchors.summary.json"),
            "provenance": existing_path(slot_dir, "provenance.json"),
        },
        "gates": build_change_gates(task_root, slot_dir),
    }


def build_construction_package_record(task: dict[str, Any]) -> dict[str, Any]:
    lm_task = task.get("lm_task", {})
    mm_task = task.get("mm_task", {})
    evidence = task.get("evidence", {})
    gates = task.get("gates", {})
    return {
        "schema_version": "2026-06-18",
        "task_id": task["task_id"],
        "repo_id": task["repo_id"],
        "regime": CONSTRUCTION_REGIME,
        "website_type": task.get("website_type", "unknown"),
        "split": task.get("split", "pilot"),
        "lm_task": {
            "block": LM_BLOCK,
            "primary_metrics": lm_task.get("primary_metrics", ["TCS", "TD", "ITR"]),
            "instructions": lm_task.get("instructions", ""),
            "actor_input_policy": lm_task.get("actor_input_policy"),
            "milestone_count": lm_task.get("milestone_count") or len(task.get("milestones", [])),
        },
        "mm_task": {
            "block": MM_BLOCK,
            "judge_metric": mm_task.get("judge_metric", "JA_A"),
            "human_calibration_required": mm_task.get("human_calibration_required", True),
            "checkpoints": mm_task.get("checkpoints", []),
        },
        "evidence": {
            "capture_states": evidence.get("capture_states", []),
            "design_anchors": evidence.get("design_anchors", ""),
            "provenance": evidence.get("provenance", ""),
        },
        "gates": gates,
        "asset_policy": task.get("asset_policy"),
    }


def validate_capability_record(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = ["schema_version", "task_id", "repo_id", "regime", "lm_task", "mm_task", "evidence", "gates"]
    for field in required:
        if field not in record:
            errors.append(f"missing {field}")
    if record.get("schema_version") != "2026-06-18":
        errors.append("schema_version must be 2026-06-18")
    if record.get("regime") not in {CHANGE_REGIME, CONSTRUCTION_REGIME}:
        errors.append(f"unknown regime: {record.get('regime')}")
    if record.get("lm_task", {}).get("block") != LM_BLOCK:
        errors.append("lm_task.block must be lm")
    if record.get("mm_task", {}).get("block") != MM_BLOCK:
        errors.append("mm_task.block must be mm")
    primary_metrics = record.get("lm_task", {}).get("primary_metrics", [])
    if record.get("regime") == CHANGE_REGIME:
        if "WCS" not in primary_metrics:
            errors.append("change lm_task.primary_metrics must include WCS")
        if record.get("mm_task", {}).get("judge_metric") != "JA_B":
            errors.append("change mm_task.judge_metric must be JA_B")
    if record.get("regime") == CONSTRUCTION_REGIME:
        for metric in ("TCS", "TD", "ITR"):
            if metric not in primary_metrics:
                errors.append(f"construction lm_task.primary_metrics must include {metric}")
        if record.get("mm_task", {}).get("judge_metric") != "JA_A":
            errors.append("construction mm_task.judge_metric must be JA_A")
        if not isinstance(record.get("asset_policy"), dict):
            errors.append("construction records must include asset_policy")
        if "asset_policy" not in record.get("gates", {}):
            errors.append("construction gates must include asset_policy")
    checkpoint_ids = [
        item.get("checkpoint_id")
        for item in record.get("mm_task", {}).get("checkpoints", [])
        if item.get("checkpoint_id")
    ]
    if len(checkpoint_ids) != len(set(checkpoint_ids)):
        errors.append("mm_task checkpoints contain duplicate checkpoint_id values")
    if not record.get("lm_task", {}).get("instructions"):
        errors.append("lm_task.instructions is empty")
    for layer in (L_HARD, L_METRIC, L_SOFT):
        if layer not in record.get("gates", {}).get("signal_layers", {}):
            errors.append(f"missing signal layer {layer}")
    if jsonschema is not None:
        try:
            jsonschema.validate(record, task_package_json_schema())
        except jsonschema.ValidationError as exc:
            path = ".".join(str(item) for item in exc.absolute_path)
            errors.append(f"task package schema validation failed at {path or '<root>'}: {exc.message}")
    return errors


def export_change_capability_split(task_root: Path, output_path: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    audit_items: list[dict[str, Any]] = []
    for task_path in sorted(task_root.glob("slot_*/task.jsonl")):
        slot_dir = task_path.parent
        submission_specs = specs_by_task(slot_dir / "submission_specs.json")
        for task in read_jsonl(task_path):
            spec = submission_specs.get(task["task_id"], {})
            record = build_change_package_record(task, slot_dir, spec, task_root)
            records.append(record)
            errors = validate_capability_record(record)
            audit_items.append(
                {
                    "task_id": task["task_id"],
                    "repo_id": task["repo_id"],
                    "slot_dir": str(slot_dir),
                    "mm_checkpoint_count": len(record["mm_task"]["checkpoints"]),
                    "errors": errors,
                    "passed": not errors,
                }
            )
    write_jsonl(output_path, records)
    audit = {
        "schema_version": "2026-06-18",
        "task_root": str(task_root),
        "output_path": str(output_path),
        "total": len(records),
        "passed": sum(1 for item in audit_items if item["passed"]),
        "failed": sum(1 for item in audit_items if not item["passed"]),
        "mm_checkpoint_total": sum(item["mm_checkpoint_count"] for item in audit_items),
        "items": audit_items,
    }
    write_json(output_path.with_suffix(".audit.json"), audit)
    return audit


def export_construction_capability_split(ledger_path: Path, output_path: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    audit_items: list[dict[str, Any]] = []
    ledger_rows = load_jsonl_if_exists(ledger_path)
    accepted_rows = [row for row in ledger_rows if row.get("status") == "accepted"]
    for row in accepted_rows:
        task_path = Path(str(row.get("task_path", "")))
        task: dict[str, Any] = {}
        row_errors: list[str] = validate_acceptance_ledger_row(row)
        if not task_path.exists():
            if not any("task_path does not exist" in issue for issue in row_errors):
                row_errors.append(f"accepted construction task_path does not exist: {task_path}")
        else:
            task = load_json_if_exists(task_path)
        if task:
            try:
                record = build_construction_package_record(task)
            except KeyError as exc:
                record = {}
                row_errors.append(f"missing construction task field: {exc}")
            if record and not row_errors:
                records.append(record)
                row_errors.extend(validate_capability_record(record))
        audit_items.append(
            {
                "task_id": row.get("task_id") or task.get("task_id"),
                "repo_id": row.get("repo_id") or task.get("repo_id"),
                "task_path": str(task_path) if str(task_path) else None,
                "mm_checkpoint_count": len(task.get("mm_task", {}).get("checkpoints", [])) if task else 0,
                "errors": row_errors,
                "passed": not row_errors,
            }
        )
    write_jsonl(output_path, records)
    audit = {
        "schema_version": "2026-06-18",
        "regime": CONSTRUCTION_REGIME,
        "ledger_path": str(ledger_path),
        "output_path": str(output_path),
        "ledger_rows": len(ledger_rows),
        "accepted_rows": len(accepted_rows),
        "total": len(records),
        "passed": sum(1 for item in audit_items if item["passed"]),
        "failed": sum(1 for item in audit_items if not item["passed"]),
        "mm_checkpoint_total": sum(item["mm_checkpoint_count"] for item in audit_items),
        "items": audit_items,
    }
    write_json(output_path.with_suffix(".audit.json"), audit)
    return audit


def add_export_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task-root", type=Path, default=TASK_DIR)
    parser.add_argument("--construction-ledger", type=Path, default=DEFAULT_CONSTRUCTION_LEDGER)
    parser.add_argument("--regime", choices=[CHANGE_REGIME, CONSTRUCTION_REGIME], default=CHANGE_REGIME)
    parser.add_argument("--output", type=Path, default=None)


def run_export_from_args(args: argparse.Namespace) -> None:
    output = args.output
    if output is None:
        output = DEFAULT_CONSTRUCTION_SPLIT if args.regime == CONSTRUCTION_REGIME else DEFAULT_CHANGE_SPLIT
    try:
        assert_not_under_formal_task_root(output, purpose="Capability split export")
        assert_not_under_formal_task_root(output.with_suffix(".audit.json"), purpose="Capability split audit")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    ensure_dir(output.parent)
    if args.regime == CONSTRUCTION_REGIME:
        audit = export_construction_capability_split(args.construction_ledger, output)
    else:
        audit = export_change_capability_split(args.task_root, output)
    print(
        f"exported {audit['total']} {args.regime} capability records: "
        f"{audit['passed']} passed, {audit['failed']} failed, "
        f"mm_checkpoints={audit['mm_checkpoint_total']} output={output}"
    )
