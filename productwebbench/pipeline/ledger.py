from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.config import DEFAULT_OUTPUT_ROOT, PROJECT_ROOT
from ..core.io_utils import ensure_dir, read_jsonl, write_json
from ..core.task_files import normalize_task_path
from ..evalkit.protocol import audit_eval_protocol_from_status
from ..evalkit.readiness import audit_readiness_from_status
from ..evalkit.denominators import audit_denominator_consistency_from_status
from ..capture.runtime import runtime_status as capture_runtime_status
from ..evalkit.mm_judge import audit_mm_judge_spec
from ..evalkit.signals import audit_signal_layers
from ..taxonomy.capability import (
    CHANGE_REGIME,
    CONSTRUCTION_REGIME,
    metric_schema,
    task_package_json_schema,
)
from . import per_task
from .mm_calibration import audit_calibration_gate
from ..regimes.construction.ledger import validate_acceptance_ledger_row
from ..regimes.registry import list_regimes


DEFAULT_LEDGER_ROOT = DEFAULT_OUTPUT_ROOT / "authoring_ledger"
DEFAULT_TASK_ROOT = DEFAULT_OUTPUT_ROOT / "tasks"
DEFAULT_SCHEMA_ROOT = DEFAULT_OUTPUT_ROOT / "schema"
DEFAULT_DOCS_STATUS = PROJECT_ROOT / "docs" / "STATUS.md"
DEFAULT_CANDIDATE_PATH = DEFAULT_OUTPUT_ROOT / "manifest" / "candidates.jsonl"
DEFAULT_CANDIDATE_SUMMARY_PATH = DEFAULT_OUTPUT_ROOT / "manifest" / "candidates.summary.json"
DEFAULT_AUTHORING_AUDIT_PATH = DEFAULT_LEDGER_ROOT / "authoring_data_audit.json"
DEFAULT_STRICT_AUTHORING_AUDIT_PATH = DEFAULT_LEDGER_ROOT / "authoring_data_audit.strict_review_artifacts.json"
DEFAULT_STRICT_FREEZE_AUDIT_PATH = DEFAULT_LEDGER_ROOT / "authoring_data_audit.strict_freeze.json"
DEFAULT_DUPLICATE_REPO_REPAIR_PLAN = DEFAULT_LEDGER_ROOT / "duplicate_repo_repair_plan.json"
DEFAULT_STRICT_AUTHORING_REPAIR_PLAN = DEFAULT_LEDGER_ROOT / "strict_authoring_repair_plan.json"
DEFAULT_STRICT_FREEZE_WORKLIST = DEFAULT_LEDGER_ROOT / "strict_freeze_worklist.json"
DEFAULT_MM_CALIBRATION_ROOT = DEFAULT_OUTPUT_ROOT / "mm_calibration"
DEFAULT_MM_PROTOCOL_REPAIR_PLAN = DEFAULT_MM_CALIBRATION_ROOT / "mm_protocol_repair_plan.json"
DEFAULT_CONSTRUCTION_DRAFT_ROOT = DEFAULT_OUTPUT_ROOT / "construction_drafts"
DEFAULT_CONSTRUCTION_REPAIR_PLAN = DEFAULT_CONSTRUCTION_DRAFT_ROOT / "construction_repair_plan.json"
DEFAULT_CONSTRUCTION_EVIDENCE_WORKLIST = DEFAULT_CONSTRUCTION_DRAFT_ROOT / "construction_evidence_worklist.json"
DEFAULT_METAREVAL_ROOT = DEFAULT_OUTPUT_ROOT / "metareval"
DEFAULT_NEGATIVE_CONTROL_PLAN = DEFAULT_METAREVAL_ROOT / "negative_control_plan.json"
DEFAULT_NEGATIVE_CONTROL_AUDIT = DEFAULT_METAREVAL_ROOT / "negative_control_audit.json"
DEFAULT_CAPABILITY_SPLIT_PATH = DEFAULT_OUTPUT_ROOT / "capability_splits" / "change_lm_mm.jsonl"
DEFAULT_CAPABILITY_SPLIT_AUDIT = DEFAULT_OUTPUT_ROOT / "capability_splits" / "change_lm_mm.audit.json"
DEFAULT_CONSTRUCTION_CAPABILITY_SPLIT_PATH = DEFAULT_OUTPUT_ROOT / "capability_splits" / "construction_lm_mm.jsonl"
DEFAULT_CONSTRUCTION_CAPABILITY_SPLIT_AUDIT = DEFAULT_OUTPUT_ROOT / "capability_splits" / "construction_lm_mm.audit.json"
DEFAULT_SIGNAL_LAYER_AUDIT = DEFAULT_OUTPUT_ROOT / "capability_splits" / "change_lm_mm.signal_layers.json"
DEFAULT_MM_JUDGE_SPEC_AUDIT = DEFAULT_OUTPUT_ROOT / "capability_splits" / "change_lm_mm.mm_judge_spec_audit.json"
DEFAULT_CONSTRUCTION_MM_JUDGE_SPEC_AUDIT = DEFAULT_OUTPUT_ROOT / "capability_splits" / "construction_lm_mm.mm_judge_spec_audit.json"
DEFAULT_MM_JUDGE_PERTURBATION_AUDIT = DEFAULT_OUTPUT_ROOT / "mm_calibration" / "mm_judge_perturbation_audit.json"
DEFAULT_MM_JUDGE_PERTURBATION_QUEUE = DEFAULT_OUTPUT_ROOT / "mm_calibration" / "change_perturbation_queue.json"
DEFAULT_MODEL_ISOLATION_AUDIT = DEFAULT_OUTPUT_ROOT / "eval_protocol" / "model_isolation_audit.json"
DEFAULT_MODEL_ROLE_QUEUE = DEFAULT_OUTPUT_ROOT / "eval_protocol" / "model_role_queue.json"
DEFAULT_DEAI_CONTRAST_AUDIT = DEFAULT_OUTPUT_ROOT / "eval_protocol" / "deai_contrast_audit.json"
DEFAULT_DEAI_CONTRAST_QUEUE = DEFAULT_OUTPUT_ROOT / "eval_protocol" / "deai_contrast_anchor_queue.json"
DEFAULT_0618_READINESS_AUDIT = DEFAULT_OUTPUT_ROOT / "eval_protocol" / "0618_readiness_audit.json"
DEFAULT_0618_READINESS_REPAIR_PLAN = DEFAULT_OUTPUT_ROOT / "eval_protocol" / "0618_readiness_repair_plan.json"
DEFAULT_TASK_PATTERN = "slot_*/task.jsonl"


def load_json_if_exists(path: Path) -> dict[str, Any]:
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


def load_jsonl_if_exists(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return list(read_jsonl(path))


def count_jsonl_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    return dict(sorted(Counter(str(item.get(key, "unknown")) for item in items if item.get("status") == "accepted").items()))


def format_issue_list(issues: list[str], *, limit: int = 10) -> str:
    if not issues:
        return "`none`"
    rendered = "; ".join(f"`{issue}`" for issue in issues[:limit])
    remaining = len(issues) - limit
    if remaining > 0:
        rendered += f"; ... plus `{remaining}` more"
    return rendered


def format_count_map(counts: dict[str, int], *, limit: int = 8) -> str:
    if not counts:
        return "`none`"
    items = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    rendered = "; ".join(f"`{key}={value}`" for key, value in items[:limit])
    remaining = len(items) - limit
    if remaining > 0:
        rendered += f"; ... plus `{remaining}` more"
    return rendered


def format_inline_items(items: list[Any], *, limit: int = 6) -> str:
    if not items:
        return "`none`"
    rendered = ", ".join(f"`{item}`" for item in items[:limit])
    remaining = len(items) - limit
    if remaining > 0:
        rendered += f", ... plus `{remaining}` more"
    return rendered


def audit_input_file_digests(audit: dict[str, Any], prefix: str) -> list[str]:
    current_issues: list[str] = []
    input_files = audit.get("input_files") or {}
    if not isinstance(input_files, dict):
        return [f"{prefix} input_files is malformed"]
    for key, item in input_files.items():
        if not isinstance(item, dict):
            current_issues.append(f"{prefix} input_files.{key} is malformed")
            continue
        raw_path = item.get("path")
        expected_sha = item.get("sha256")
        path = Path(str(raw_path)) if raw_path else None
        current_sha = file_sha256(path)
        if current_sha != expected_sha:
            current_issues.append(f"{prefix} input {key} changed or is missing")
    return current_issues


def target_gaps(targets: dict[str, int], actual: dict[str, int]) -> dict[str, dict[str, int]]:
    keys = sorted(set(targets) | set(actual))
    return {
        key: {
            "target": int(targets.get(key, 0)),
            "accepted": int(actual.get(key, 0)),
            "gap": max(0, int(targets.get(key, 0)) - int(actual.get(key, 0))),
        }
        for key in keys
    }


def summarize_change_regime(
    ledger_root: Path,
    task_root: Path = DEFAULT_TASK_ROOT,
    pattern: str = DEFAULT_TASK_PATTERN,
) -> dict[str, Any]:
    summary = load_json_if_exists(ledger_root / "tasks_400_summary.json")
    entries = load_jsonl_if_exists(ledger_root / "tasks_400_ledger.jsonl")
    accepted = [item for item in entries if item.get("status") == "accepted"]
    formal_records = formal_change_task_records(task_root, pattern)
    formal_task_ids = {str(item.get("task_id") or "") for item in formal_records if item.get("task_id")}
    formal_slot_ids = {str(item.get("slot_id") or "") for item in formal_records if item.get("slot_id")}
    materialized = [
        item
        for item in accepted
        if str(item.get("task_id") or "") in formal_task_ids
        and str(item.get("slot_id") or "") in formal_slot_ids
    ]
    target_total = int(summary.get("total", 400))
    targets = summary.get("targets", {})
    ledger_coverage = {
        "continuity_facets": target_gaps(targets.get("continuity_facets", {}), count_by(accepted, "continuity_facet")),
        "website_types": target_gaps(targets.get("website_types", {}), count_by(accepted, "website_type")),
        "intents": target_gaps(targets.get("intents", {}), count_by(accepted, "intent")),
        "scopes": target_gaps(targets.get("scopes", {}), count_by(accepted, "scope")),
        "difficulties": target_gaps(targets.get("difficulties", {}), count_by(accepted, "difficulty")),
    }
    materialized_coverage = {
        "continuity_facets": target_gaps(
            targets.get("continuity_facets", {}),
            count_by(materialized, "continuity_facet"),
        ),
        "website_types": target_gaps(targets.get("website_types", {}), count_by(materialized, "website_type")),
        "intents": target_gaps(targets.get("intents", {}), count_by(materialized, "intent")),
        "scopes": target_gaps(targets.get("scopes", {}), count_by(materialized, "scope")),
        "difficulties": target_gaps(targets.get("difficulties", {}), count_by(materialized, "difficulty")),
    }
    return {
        "regime": CHANGE_REGIME,
        "target_total": target_total,
        "accepted": len(accepted),
        "formal_materialized": len(materialized),
        "formal_materialized_task_count": len(materialized),
        "coverage_denominator": "formal_materialized_task_rows",
        "open": max(0, target_total - len(accepted)),
        "first_open": next((item.get("slot_id") for item in entries if item.get("status") == "open"), None),
        "last_task_id": summary.get("last_task_id"),
        "targets": targets,
        "coverage": materialized_coverage,
        "formal_materialized_coverage": materialized_coverage,
        "ledger_coverage": ledger_coverage,
    }


def formal_change_task_records(task_root: Path, pattern: str = DEFAULT_TASK_PATTERN) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for task_path in sorted(task_root.glob(pattern)):
        slot_id = task_path.parent.name
        for row in read_jsonl(task_path):
            if not isinstance(row, dict):
                continue
            records.append(
                {
                    "slot_id": slot_id,
                    "task_id": row.get("task_id"),
                    "repo_id": row.get("repo_id"),
                    "task_path": str(task_path),
                }
            )
    return records


def summarize_change_ledger_alignment(
    ledger_root: Path,
    task_root: Path = DEFAULT_TASK_ROOT,
    pattern: str = DEFAULT_TASK_PATTERN,
) -> dict[str, Any]:
    ledger_path = ledger_root / "tasks_400_ledger.jsonl"
    entries = load_jsonl_if_exists(ledger_path)
    accepted = [item for item in entries if item.get("status") == "accepted"]
    formal_records = formal_change_task_records(task_root, pattern)

    accepted_task_ids = [str(item.get("task_id") or "") for item in accepted if item.get("task_id")]
    accepted_slots = [str(item.get("slot_id") or "") for item in accepted if item.get("slot_id")]
    formal_task_ids = [str(item.get("task_id") or "") for item in formal_records if item.get("task_id")]
    formal_slots = [str(item.get("slot_id") or "") for item in formal_records if item.get("slot_id")]
    accepted_task_id_set = set(accepted_task_ids)
    accepted_slot_set = set(accepted_slots)
    formal_task_id_set = set(formal_task_ids)
    formal_slot_set = set(formal_slots)

    accepted_without_formal_task = [
        {
            "slot_id": item.get("slot_id"),
            "task_id": item.get("task_id"),
            "repo_id": item.get("repo_id"),
            "source": item.get("source"),
            "stage": item.get("stage"),
            "agent_decision": item.get("agent_decision"),
        }
        for item in accepted
        if item.get("task_id") not in formal_task_id_set
    ]
    accepted_slots_without_formal_file = [
        {
            "slot_id": item.get("slot_id"),
            "task_id": item.get("task_id"),
            "repo_id": item.get("repo_id"),
            "source": item.get("source"),
            "stage": item.get("stage"),
            "agent_decision": item.get("agent_decision"),
        }
        for item in accepted
        if item.get("slot_id") not in formal_slot_set
    ]
    formal_without_accepted_ledger = [
        item
        for item in formal_records
        if item.get("task_id") not in accepted_task_id_set
    ]
    duplicate_accepted_task_ids = [
        task_id for task_id, count in Counter(accepted_task_ids).items() if task_id and count > 1
    ]
    duplicate_formal_task_ids = [
        task_id for task_id, count in Counter(formal_task_ids).items() if task_id and count > 1
    ]
    issues: list[str] = []
    if accepted_without_formal_task:
        issues.append(f"accepted ledger rows without matching formal task_id: {len(accepted_without_formal_task)}")
    if accepted_slots_without_formal_file:
        issues.append(f"accepted ledger rows without matching formal slot file: {len(accepted_slots_without_formal_file)}")
    if formal_without_accepted_ledger:
        issues.append(f"formal task rows without accepted ledger task_id: {len(formal_without_accepted_ledger)}")
    if duplicate_accepted_task_ids:
        issues.append(f"duplicate accepted ledger task ids: {len(duplicate_accepted_task_ids)}")
    if duplicate_formal_task_ids:
        issues.append(f"duplicate formal task ids: {len(duplicate_formal_task_ids)}")
    non_evaluable_planning_rows = len(accepted_without_formal_task)
    return {
        "schema_version": "2026-06-19",
        "artifact_type": "change_ledger_alignment_audit",
        "formal_task_record": False,
        "ledger_path": str(ledger_path),
        "task_root": str(task_root),
        "task_pattern": pattern,
        "passed": not issues,
        "issue_count": len(issues),
        "issues": issues,
        "ledger_rows": len(entries),
        "ledger_accepted": len(accepted),
        "ledger_accepted_semantics": (
            "progress ledger only; a row with status=accepted is not evaluable or publishable unless a matching "
            "data/productwebbench/tasks/slot_*/task.jsonl file exists and strict freeze/no-bulk gates pass"
        ),
        "formal_task_rows": len(formal_records),
        "formal_task_files": len(set(formal_slots)),
        "evaluable_denominator_source": "data/productwebbench/tasks/slot_*/task.jsonl",
        "evaluable_formal_task_count": len(formal_records),
        "publishable_denominator_source": "strict_freeze_authoring_audit.strict_clean_task_count",
        "publishable_formal_task_count": None,
        "strict_publishable_formal_task_count": None,
        "unmaterialized_accepted_source_counts": dict(
            sorted(Counter(str(item.get("source") or "unknown") for item in accepted_without_formal_task).items())
        ),
        "unmaterialized_accepted_stage_counts": dict(
            sorted(Counter(str(item.get("stage") or "unknown") for item in accepted_without_formal_task).items())
        ),
        "unmaterialized_accepted_decision_counts": dict(
            sorted(Counter(str(item.get("agent_decision") or "unknown") for item in accepted_without_formal_task).items())
        ),
        "accepted_without_formal_task_count": len(accepted_without_formal_task),
        "accepted_without_formal_task_examples": accepted_without_formal_task[:20],
        "accepted_slots_without_formal_file_count": len(accepted_slots_without_formal_file),
        "accepted_slots_without_formal_file_examples": accepted_slots_without_formal_file[:20],
        "non_evaluable_planning_row_count": non_evaluable_planning_rows,
        "non_evaluable_planning_rows_are_formal_acceptance": False,
        "non_evaluable_planning_rows_block_publish": bool(non_evaluable_planning_rows),
        "non_evaluable_planning_row_policy": (
            "These rows may guide one-task-at-a-time authoring, but they must not be evaluated, exported as formal "
            "benchmark tasks, or counted as accepted formal data until each slot has its own formal task file, "
            "repo-specific inspection evidence, no-bulk declaration, and single-task freeze audit."
        ),
        "formal_without_accepted_ledger_count": len(formal_without_accepted_ledger),
        "formal_without_accepted_ledger_examples": formal_without_accepted_ledger[:20],
        "duplicate_accepted_task_ids": duplicate_accepted_task_ids[:100],
        "duplicate_formal_task_ids": duplicate_formal_task_ids[:100],
        "gate": "Progress ledger rows and evaluable formal task rows are separate; strict publishable rows additionally require P8 freeze/no-bulk evidence.",
    }


def summarize_construction_regime(ledger_root: Path) -> dict[str, Any]:
    entries = load_jsonl_if_exists(ledger_root / "construction_ledger.jsonl")
    accepted_raw = [item for item in entries if item.get("status") == "accepted"]
    valid_accepted: list[dict[str, Any]] = []
    invalid_accepted: list[dict[str, Any]] = []
    for item in accepted_raw:
        issues = validate_acceptance_ledger_row(item)
        if issues:
            invalid_accepted.append(
                {
                    "task_id": item.get("task_id"),
                    "repo_id": item.get("repo_id"),
                    "task_path": item.get("task_path"),
                    "issues": issues,
                }
            )
        else:
            valid_accepted.append(item)
    target_total = 400
    return {
        "regime": CONSTRUCTION_REGIME,
        "target_total": target_total,
        "accepted": len(valid_accepted),
        "raw_accepted": len(accepted_raw),
        "invalid_accepted": len(invalid_accepted),
        "invalid_accepted_examples": invalid_accepted[:20],
        "accepted_semantics": "strict construction acceptance rows only; hand-written or stale ledger rows are excluded",
        "open": max(0, target_total - len(valid_accepted)),
        "first_open": None if valid_accepted else "c_001",
        "last_task_id": valid_accepted[-1].get("task_id") if valid_accepted else None,
        "targets": {"total": target_total},
        "coverage": {},
        "status": "greenfield" if not valid_accepted else "in_progress",
    }


def formal_task_repo_ids(task_root: Path, pattern: str = DEFAULT_TASK_PATTERN) -> set[str]:
    repo_ids: set[str] = set()
    for path in task_root.glob(pattern):
        if not path.is_file():
            continue
        for task in load_jsonl_if_exists(path):
            repo_id = task.get("repo_id")
            if repo_id:
                repo_ids.add(str(repo_id))
    return repo_ids


def summarize_candidate_pool(candidate_path: Path, summary_path: Path, task_root: Path) -> dict[str, Any]:
    rows = load_jsonl_if_exists(candidate_path)
    repo_ids = [item.get("repo_id") for item in rows if item.get("repo_id")]
    duplicate_count = len(repo_ids) - len(set(repo_ids))
    zip_paths = [normalize_task_path(item.get("zip_path")) for item in rows if item.get("zip_path")]
    duplicate_zip_path_count = len(zip_paths) - len(set(zip_paths))
    used_repo_ids = formal_task_repo_ids(task_root)
    used_repo_overlaps = sorted(str(repo_id) for repo_id in set(repo_ids) if repo_id in used_repo_ids)
    summary = load_json_if_exists(summary_path)
    expected_candidates = summary.get("total_limit")
    if expected_candidates is None:
        expected_candidates = summary.get("total")
    expected_matches = expected_candidates is None or len(rows) == expected_candidates
    summary_total_matches = summary.get("total") in (None, len(rows))
    summary_unique_matches = summary.get("unique_repo_ids") in (None, len(set(repo_ids)))
    return {
        "path": str(candidate_path),
        "rows": len(rows),
        "unique_repo_ids": len(set(repo_ids)),
        "duplicate_repo_id_count": duplicate_count,
        "missing_repo_id_count": len(rows) - len(repo_ids),
        "unique_zip_paths": len(set(zip_paths)),
        "duplicate_zip_path_count": duplicate_zip_path_count,
        "missing_zip_path_count": len(rows) - len(zip_paths),
        "used_task_repo_overlap_count": len(used_repo_overlaps),
        "used_task_repo_overlap_examples": used_repo_overlaps[:50],
        "expected_candidates": expected_candidates,
        "summary_total": summary.get("total"),
        "summary_total_limit": summary.get("total_limit"),
        "summary_source_total": summary.get("source_total"),
        "summary_source_unique_repo_ids": summary.get("source_unique_repo_ids"),
        "summary_duplicate_repo_ids": summary.get("duplicate_repo_ids"),
        "candidate_pool_is_not_formal_task_data": True,
        "passed": (
            expected_matches
            and summary_total_matches
            and summary_unique_matches
            and duplicate_count == 0
            and duplicate_zip_path_count == 0
            and not used_repo_overlaps
            and len(rows) == len(zip_paths)
            and len(rows) == len(repo_ids)
        ),
    }


def summarize_capture_runtime() -> dict[str, Any]:
    return capture_runtime_status()


def summarize_authoring_audit(audit_path: Path) -> dict[str, Any]:
    audit = load_json_if_exists(audit_path)
    manifest = audit.get("repo_manifest", {}) if audit else {}
    tasks = audit.get("authored_tasks", {}) if audit else {}
    formal_policy = tasks.get("formal_authoring_policy", {}) if tasks else {}
    candidates = audit.get("candidate_pool", {}) if audit else {}
    scaffolds = audit.get("formal_root_scaffolds", {}) if audit else {}
    return {
        "path": str(audit_path),
        "passed": bool(audit.get("passed", False)) if audit else False,
        "issues": audit.get("issues", []) if audit else ["authoring audit has not been generated"],
        "manifest_rows": manifest.get("rows", 0),
        "manifest_unique_repo_ids": manifest.get("unique_repo_ids", 0),
        "manifest_duplicate_repo_id_count": manifest.get("duplicate_repo_id_count", 0),
        "candidate_rows": candidates.get("rows", 0),
        "candidate_unique_repo_ids": candidates.get("unique_repo_ids", 0),
        "candidate_duplicate_repo_id_count": candidates.get("duplicate_repo_id_count", 0),
        "task_pattern": tasks.get("pattern", "slot_*/task.jsonl"),
        "task_files": tasks.get("file_count", 0),
        "task_count": tasks.get("task_count", 0),
        "task_file_row_count_issue_count": tasks.get("task_file_row_count_issue_count", 0),
        "task_file_row_count_issues": tasks.get("task_file_row_count_issues", []),
        "unique_task_ids": tasks.get("unique_task_ids", 0),
        "duplicate_task_id_count": tasks.get("duplicate_task_id_count", 0),
        "duplicate_repo_id_count": tasks.get("duplicate_repo_id_count", 0),
        "duplicate_repo_refs": tasks.get("duplicate_repo_refs", []),
        "unique_repo_ids_required": tasks.get("unique_repo_ids_required", True),
        "duplicate_field_issue_count": tasks.get("duplicate_field_issue_count", 0),
        "validation_issue_count": tasks.get("validation_issue_count", 0),
        "review_artifacts_required": tasks.get("review_artifacts_required", False),
        "review_artifact_issue_count": tasks.get("review_artifact_issue_count", 0),
        "no_bulk_declarations_required": tasks.get("no_bulk_declarations_required", False),
        "no_bulk_declaration_issue_count": tasks.get("no_bulk_declaration_issue_count", 0),
        "formal_authoring_policy_passed": formal_policy.get("passed", False),
        "formal_authoring_policy_issues": formal_policy.get("issues", []),
        "formal_automated_marker_issue_count": formal_policy.get("automated_marker_issue_count", 0),
        "formal_strict_evidence_requirements_enabled": formal_policy.get("strict_evidence_requirements_enabled", False),
        "formal_root_scaffold_passed": scaffolds.get("passed", True),
        "formal_root_scanned_file_count": scaffolds.get("scanned_file_count", 0),
        "formal_root_forbidden_scaffold_count": scaffolds.get("forbidden_scaffold_count", 0),
        "formal_root_forbidden_scaffolds": scaffolds.get("forbidden_scaffolds", []),
    }


def summarize_strict_authoring_audit(audit_path: Path) -> dict[str, Any]:
    audit = load_json_if_exists(audit_path)
    tasks = audit.get("authored_tasks", {}) if audit else {}
    formal_policy = tasks.get("formal_authoring_policy", {}) if tasks else {}
    artifact_issues = tasks.get("review_artifact_issues", []) if tasks else []
    freeze_issues = tasks.get("freeze_audit_issues", []) if tasks else []
    no_bulk_issues = tasks.get("no_bulk_declaration_issues", []) if tasks else []
    row_count_issues = tasks.get("task_file_row_count_issues", []) if tasks else []
    validation_issues = tasks.get("validation_issues", []) if tasks else []
    duplicate_field_issues = tasks.get("duplicate_field_issues", []) if tasks else []
    blocked_paths = {
        str(item.get("path"))
        for item in [
            *artifact_issues,
            *freeze_issues,
            *no_bulk_issues,
            *row_count_issues,
            *validation_issues,
            *duplicate_field_issues,
        ]
        if item.get("path")
    }
    task_count = int(tasks.get("task_count", 0) or 0)
    global_policy_issues: list[str] = []
    if tasks and tasks.get("unique_repo_ids_required") is not True:
        global_policy_issues.append("strict authoring audits must require unique repo ids")
    if int(tasks.get("duplicate_task_id_count", 0) or 0):
        global_policy_issues.append(
            f"strict authoring audit has {tasks.get('duplicate_task_id_count')} duplicate task ids"
        )
    if tasks.get("unique_repo_ids_required") is True and int(tasks.get("duplicate_repo_id_count", 0) or 0):
        global_policy_issues.append(
            f"strict authoring audit has {tasks.get('duplicate_repo_id_count')} duplicate repo ids"
        )
    if tasks and formal_policy.get("passed") is not True:
        global_policy_issues.extend(str(issue) for issue in formal_policy.get("issues", []))
    strict_clean = 0 if global_policy_issues else max(0, task_count - len(blocked_paths))
    raw_issues = audit.get("issues", []) if audit else ["strict authoring audit has not been generated"]
    issues = [*raw_issues, *global_policy_issues]
    return {
        "path": str(audit_path),
        "available": bool(audit),
        "passed": bool(audit.get("passed", False)) and not global_policy_issues if audit else False,
        "task_count": task_count,
        "strict_blocked_task_count": len(blocked_paths),
        "strict_clean_task_count": strict_clean,
        "strict_blocked_task_paths": sorted(blocked_paths)[:100],
        "global_policy_issues": global_policy_issues,
        "global_policy_issue_count": len(global_policy_issues),
        "task_file_row_count_issue_count": tasks.get("task_file_row_count_issue_count", 0),
        "task_file_row_count_issues": row_count_issues,
        "validation_issue_count": tasks.get("validation_issue_count", 0),
        "duplicate_field_issue_count": tasks.get("duplicate_field_issue_count", 0),
        "unique_repo_ids_required": tasks.get("unique_repo_ids_required", False),
        "duplicate_repo_id_count": tasks.get("duplicate_repo_id_count", 0),
        "duplicate_repo_refs": tasks.get("duplicate_repo_refs", []),
        "duplicate_task_id_count": tasks.get("duplicate_task_id_count", 0),
        "review_artifacts_required": tasks.get("review_artifacts_required", False),
        "review_artifact_issue_count": tasks.get("review_artifact_issue_count", 0),
        "review_artifact_issues": artifact_issues,
        "freeze_audits_required": tasks.get("freeze_audits_required", False),
        "freeze_audit_issue_count": tasks.get("freeze_audit_issue_count", 0),
        "freeze_audit_issues": freeze_issues,
        "no_bulk_declarations_required": tasks.get("no_bulk_declarations_required", False),
        "no_bulk_declaration_issue_count": tasks.get("no_bulk_declaration_issue_count", 0),
        "no_bulk_declaration_issues": no_bulk_issues,
        "formal_authoring_policy_passed": formal_policy.get("passed", False),
        "formal_authoring_policy_issues": formal_policy.get("issues", []),
        "formal_automated_marker_issue_count": formal_policy.get("automated_marker_issue_count", 0),
        "formal_strict_evidence_requirements_enabled": formal_policy.get("strict_evidence_requirements_enabled", False),
        "issues": issues,
    }


def summarize_duplicate_repo_repair_plan(plan_path: Path, strict_audit_path: Path) -> dict[str, Any]:
    plan = load_json_if_exists(plan_path)
    strict_audit = load_json_if_exists(strict_audit_path)
    if not plan:
        return {
            "path": str(plan_path),
            "source_audit": str(strict_audit_path),
            "available": False,
            "current": False,
            "formal_task_record": None,
            "duplicate_group_count": 0,
            "included_group_count": 0,
            "issue_count": 1,
            "issues": [f"duplicate repo repair plan missing: {plan_path}"],
            "stale_issue_count": 0,
            "stale_issues": [],
            "first_repair_items": [],
        }
    issues: list[str] = []
    stale_issues = audit_input_file_digests(plan, "duplicate repo repair plan")
    if plan.get("artifact_type") != "duplicate_repo_repair_plan":
        issues.append(f"duplicate repo repair plan has wrong artifact_type: {plan.get('artifact_type')}")
    if plan.get("formal_task_record") is not False:
        issues.append("duplicate repo repair plan must be formal_task_record=false")
    strict_duplicate_count = (
        ((strict_audit.get("authored_tasks") or {}).get("duplicate_repo_id_count"))
        if strict_audit
        else None
    )
    if strict_duplicate_count is not None and plan.get("duplicate_group_count") != strict_duplicate_count:
        stale_issues.append(
            "duplicate repo repair plan duplicate_group_count "
            f"{plan.get('duplicate_group_count')} != strict audit duplicate_repo_id_count {strict_duplicate_count}"
        )
    current = not stale_issues
    return {
        "path": str(plan_path),
        "source_audit": plan.get("source_audit", str(strict_audit_path)),
        "available": True,
        "current": current,
        "formal_task_record": plan.get("formal_task_record"),
        "duplicate_group_count": int(plan.get("duplicate_group_count") or 0),
        "included_group_count": int(plan.get("included_group_count") or 0),
        "source_unique_repo_ids_required": plan.get("source_unique_repo_ids_required"),
        "source_duplicate_repo_id_count": plan.get("source_duplicate_repo_id_count"),
        "issue_count": len(issues) + len(stale_issues),
        "issues": issues + stale_issues,
        "stale_issue_count": len(stale_issues),
        "stale_issues": stale_issues,
        "first_repair_items": plan.get("repair_items", [])[:5],
    }


def summarize_strict_authoring_repair_plan(plan_path: Path, strict_audit_path: Path) -> dict[str, Any]:
    plan = load_json_if_exists(plan_path)
    strict_audit = load_json_if_exists(strict_audit_path)
    if not plan:
        return {
            "path": str(plan_path),
            "source_audit": str(strict_audit_path),
            "available": False,
            "current": False,
            "formal_task_record": None,
            "blocked_slot_count": 0,
            "included_slot_count": 0,
            "blocker_counts": {},
            "issue_count": 1,
            "issues": [f"strict authoring repair plan missing: {plan_path}"],
            "stale_issue_count": 0,
            "stale_issues": [],
            "first_repair_items": [],
        }
    issues: list[str] = []
    stale_issues = audit_input_file_digests(plan, "strict authoring repair plan")
    if plan.get("artifact_type") != "strict_authoring_repair_plan":
        issues.append(f"strict authoring repair plan has wrong artifact_type: {plan.get('artifact_type')}")
    if plan.get("formal_task_record") is not False:
        issues.append("strict authoring repair plan must be formal_task_record=false")
    authored_tasks = strict_audit.get("authored_tasks") or {}
    strict_task_count = authored_tasks.get("task_count")
    if strict_task_count is not None and plan.get("source_task_count") != strict_task_count:
        stale_issues.append(
            f"strict authoring repair plan source_task_count {plan.get('source_task_count')} "
            f"!= strict audit task_count {strict_task_count}"
        )
    current = not stale_issues
    return {
        "path": str(plan_path),
        "source_audit": plan.get("source_audit", str(strict_audit_path)),
        "available": True,
        "current": current,
        "formal_task_record": plan.get("formal_task_record"),
        "blocked_slot_count": int(plan.get("blocked_slot_count") or 0),
        "included_slot_count": int(plan.get("included_slot_count") or 0),
        "source_task_count": plan.get("source_task_count"),
        "blocker_counts": plan.get("blocker_counts", {}),
        "issue_count": len(issues) + len(stale_issues),
        "issues": issues + stale_issues,
        "stale_issue_count": len(stale_issues),
        "stale_issues": stale_issues,
        "first_repair_items": plan.get("repair_items", [])[:5],
    }


def summarize_strict_freeze_worklist(worklist_path: Path, repair_plan_path: Path) -> dict[str, Any]:
    worklist = load_json_if_exists(worklist_path)
    repair_plan = load_json_if_exists(repair_plan_path)
    if not worklist:
        return {
            "path": str(worklist_path),
            "source_repair_plan": str(repair_plan_path),
            "available": False,
            "current": False,
            "formal_task_record": None,
            "passed": False,
            "work_item_count": 0,
            "source_blocked_slot_count": 0,
            "source_included_slot_count": 0,
            "blocker_counts": {},
            "issue_count": 1,
            "issues": [f"strict freeze worklist missing: {worklist_path}"],
            "stale_issue_count": 0,
            "stale_issues": [],
            "first_work_items": [],
        }
    issues: list[str] = []
    stale_issues = audit_input_file_digests(worklist, "strict freeze worklist")
    if worklist.get("artifact_type") != "strict_freeze_worklist":
        issues.append(f"strict freeze worklist has wrong artifact_type: {worklist.get('artifact_type')}")
    if worklist.get("formal_task_record") is not False:
        issues.append("strict freeze worklist must be formal_task_record=false")
    if not worklist.get("passed"):
        issues.extend(str(issue) for issue in worklist.get("issues", []))
    if repair_plan:
        if worklist.get("source_blocked_slot_count") != repair_plan.get("blocked_slot_count"):
            stale_issues.append("strict freeze worklist source_blocked_slot_count does not match repair plan")
        if worklist.get("source_included_slot_count") != repair_plan.get("included_slot_count"):
            stale_issues.append("strict freeze worklist source_included_slot_count does not match repair plan")
    current = not stale_issues
    return {
        "path": str(worklist_path),
        "source_repair_plan": worklist.get("source_repair_plan", str(repair_plan_path)),
        "available": True,
        "current": current,
        "formal_task_record": worklist.get("formal_task_record"),
        "passed": bool(worklist.get("passed")) and current,
        "work_item_count": int(worklist.get("work_item_count") or 0),
        "source_blocked_slot_count": int(worklist.get("source_blocked_slot_count") or 0),
        "source_included_slot_count": int(worklist.get("source_included_slot_count") or 0),
        "blocker_counts": worklist.get("blocker_counts", {}),
        "issue_count": len(issues) + len(stale_issues),
        "issues": issues + stale_issues,
        "stale_issue_count": len(stale_issues),
        "stale_issues": stale_issues,
        "first_work_items": worklist.get("work_items", [])[:5],
    }


def stable_json_sha(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def mm_protocol_fingerprint(status_report: dict[str, Any]) -> str:
    return stable_json_sha(
        {
            "mm_block": status_report.get("mm_block") or {},
            "mm_judge_perturbations": status_report.get("mm_judge_perturbations") or {},
            "model_isolation": status_report.get("model_isolation") or {},
            "de_ai_contrast": status_report.get("de_ai_contrast") or {},
        }
    )


def construction_protocol_fingerprint(status_report: dict[str, Any]) -> str:
    regimes = status_report.get("regimes") or {}
    readiness = status_report.get("readiness_0618") or {}
    phases = readiness.get("phases") or {}
    return stable_json_sha(
        {
            "construction_regime": regimes.get(CONSTRUCTION_REGIME) or {},
            "construction_artifacts": status_report.get("construction_artifacts") or {},
            "construction_capability_split": status_report.get("construction_capability_split") or {},
            "construction_mm_judge_spec": status_report.get("construction_mm_judge_spec") or {},
            "phase1": phases.get("Phase1") or {},
            "phase2": phases.get("Phase2") or {},
        }
    )


def readiness_repair_fingerprint(status_report: dict[str, Any]) -> str:
    return stable_json_sha(
        {
            "readiness_0618": status_report.get("readiness_0618") or {},
            "eval_protocol": status_report.get("eval_protocol") or {},
            "strict_freeze_authoring_audit": status_report.get("strict_freeze_authoring_audit") or {},
            "duplicate_repo_repair_plan": status_report.get("duplicate_repo_repair_plan") or {},
            "strict_authoring_repair_plan": status_report.get("strict_authoring_repair_plan") or {},
            "mm_protocol_repair_plan": status_report.get("mm_protocol_repair_plan") or {},
            "construction_repair_plan": status_report.get("construction_repair_plan") or {},
            "model_isolation": status_report.get("model_isolation") or {},
            "de_ai_contrast": status_report.get("de_ai_contrast") or {},
            "mm_judge_perturbations": status_report.get("mm_judge_perturbations") or {},
            "mm_block": status_report.get("mm_block") or {},
        }
    )


def summarize_readiness_repair_plan(plan_path: Path, status_report: dict[str, Any]) -> dict[str, Any]:
    plan = load_json_if_exists(plan_path)
    if not plan:
        return {
            "path": str(plan_path),
            "available": False,
            "current": False,
            "formal_task_record": None,
            "repair_item_count": 0,
            "failed_invariants": [],
            "failed_phases": [],
            "repair_keys": [],
            "issue_count": 1,
            "issues": [f"0618 readiness repair plan missing: {plan_path}"],
            "stale_issue_count": 0,
            "stale_issues": [],
        }
    issues: list[str] = []
    stale_issues: list[str] = []
    if plan.get("artifact_type") != "productwebbench_0618_readiness_repair_plan":
        issues.append(f"0618 readiness repair plan has wrong artifact_type: {plan.get('artifact_type')}")
    if plan.get("formal_task_record") is not False:
        issues.append("0618 readiness repair plan must be formal_task_record=false")
    if plan.get("source_fingerprint") != readiness_repair_fingerprint(status_report):
        stale_issues.append("0618 readiness repair plan source_fingerprint does not match current status")
    return {
        "path": str(plan_path),
        "available": True,
        "current": not stale_issues,
        "formal_task_record": plan.get("formal_task_record"),
        "repair_item_count": int(plan.get("repair_item_count") or 0),
        "failed_invariants": plan.get("failed_invariants", []),
        "failed_phases": plan.get("failed_phases", []),
        "repair_keys": [item.get("key") for item in plan.get("repair_items", []) if isinstance(item, dict)],
        "issue_count": len(issues) + len(stale_issues),
        "issues": issues + stale_issues,
        "stale_issue_count": len(stale_issues),
        "stale_issues": stale_issues,
    }


def summarize_mm_protocol_repair_plan(plan_path: Path, status_report: dict[str, Any]) -> dict[str, Any]:
    plan = load_json_if_exists(plan_path)
    if not plan:
        return {
            "path": str(plan_path),
            "available": False,
            "current": False,
            "formal_task_record": None,
            "blocking_section_count": 0,
            "blocking_sections": [],
            "section_count": 0,
            "issue_count": 1,
            "issues": [f"MM protocol repair plan missing: {plan_path}"],
            "stale_issue_count": 0,
            "stale_issues": [],
        }
    issues: list[str] = []
    stale_issues: list[str] = []
    if plan.get("artifact_type") != "mm_protocol_repair_plan":
        issues.append(f"MM protocol repair plan has wrong artifact_type: {plan.get('artifact_type')}")
    if plan.get("formal_task_record") is not False:
        issues.append("MM protocol repair plan must be formal_task_record=false")
    current_fingerprint = mm_protocol_fingerprint(status_report)
    if plan.get("source_fingerprint") != current_fingerprint:
        stale_issues.append("MM protocol repair plan source_fingerprint does not match current MM protocol status")
    return {
        "path": str(plan_path),
        "available": True,
        "current": not stale_issues,
        "formal_task_record": plan.get("formal_task_record"),
        "blocking_section_count": int(plan.get("blocking_section_count") or 0),
        "blocking_sections": plan.get("blocking_sections", []),
        "section_count": int(plan.get("section_count") or 0),
        "issue_count": len(issues) + len(stale_issues),
        "issues": issues + stale_issues,
        "stale_issue_count": len(stale_issues),
        "stale_issues": stale_issues,
        "source_summary": plan.get("source_summary", {}),
    }


def summarize_construction_repair_plan(plan_path: Path, status_report: dict[str, Any]) -> dict[str, Any]:
    plan = load_json_if_exists(plan_path)
    if not plan:
        return {
            "path": str(plan_path),
            "available": False,
            "current": False,
            "formal_task_record": None,
            "blocking_requirement_count": 0,
            "draft_repair_item_count": 0,
            "draft_repair_items": [],
            "issue_count": 1,
            "issues": [f"construction repair plan missing: {plan_path}"],
            "stale_issue_count": 0,
            "stale_issues": [],
            "source_summary": {},
        }
    issues: list[str] = []
    stale_issues: list[str] = []
    if plan.get("artifact_type") != "construction_repair_plan":
        issues.append(f"construction repair plan has wrong artifact_type: {plan.get('artifact_type')}")
    if plan.get("formal_task_record") is not False:
        issues.append("construction repair plan must be formal_task_record=false")
    if plan.get("source_fingerprint") != construction_protocol_fingerprint(status_report):
        stale_issues.append("construction repair plan source_fingerprint does not match current construction status")
    return {
        "path": str(plan_path),
        "available": True,
        "current": not stale_issues,
        "formal_task_record": plan.get("formal_task_record"),
        "blocking_requirement_count": int(plan.get("blocking_requirement_count") or 0),
        "repair_keys": [item.get("key") for item in plan.get("repair_items", []) if isinstance(item, dict)],
        "draft_repair_item_count": int(plan.get("draft_repair_item_count") or 0),
        "draft_repair_items": plan.get("draft_repair_items", [])[:5],
        "issue_count": len(issues) + len(stale_issues),
        "issues": issues + stale_issues,
        "stale_issue_count": len(stale_issues),
        "stale_issues": stale_issues,
        "source_summary": plan.get("source_summary", {}),
    }


def summarize_construction_evidence_worklist(worklist_path: Path, repair_plan_path: Path) -> dict[str, Any]:
    worklist = load_json_if_exists(worklist_path)
    repair_plan = load_json_if_exists(repair_plan_path)
    if not worklist:
        return {
            "path": str(worklist_path),
            "source_repair_plan": str(repair_plan_path),
            "available": False,
            "current": False,
            "formal_task_record": None,
            "passed": False,
            "work_item_count": 0,
            "source_draft_repair_item_count": 0,
            "source_blocking_requirement_count": 0,
            "blocker_counts": {},
            "missing_expected_output_counts": {},
            "scaffold_exists_count": 0,
            "scaffold_audit_passed_count": 0,
            "complete_audit_passed_count": 0,
            "complete_audit_issue_count": 0,
            "issue_count": 1,
            "issues": [f"construction evidence worklist missing: {worklist_path}"],
            "stale_issue_count": 0,
            "stale_issues": [],
            "first_work_items": [],
        }
    issues: list[str] = []
    stale_issues = audit_input_file_digests(worklist, "construction evidence worklist")
    if worklist.get("artifact_type") != "construction_evidence_worklist":
        issues.append(f"construction evidence worklist has wrong artifact_type: {worklist.get('artifact_type')}")
    if worklist.get("formal_task_record") is not False:
        issues.append("construction evidence worklist must be formal_task_record=false")
    if not worklist.get("passed"):
        issues.extend(str(issue) for issue in worklist.get("issues", []))
    if repair_plan:
        if worklist.get("source_draft_repair_item_count") != repair_plan.get("draft_repair_item_count"):
            stale_issues.append("construction evidence worklist source_draft_repair_item_count does not match repair plan")
        if worklist.get("source_blocking_requirement_count") != repair_plan.get("blocking_requirement_count"):
            stale_issues.append("construction evidence worklist source_blocking_requirement_count does not match repair plan")
    current = not stale_issues
    return {
        "path": str(worklist_path),
        "source_repair_plan": worklist.get("source_repair_plan", str(repair_plan_path)),
        "available": True,
        "current": current,
        "formal_task_record": worklist.get("formal_task_record"),
        "passed": bool(worklist.get("passed")) and current,
        "work_item_count": int(worklist.get("work_item_count") or 0),
        "source_draft_repair_item_count": int(worklist.get("source_draft_repair_item_count") or 0),
        "source_blocking_requirement_count": int(worklist.get("source_blocking_requirement_count") or 0),
        "blocker_counts": worklist.get("blocker_counts", {}),
        "missing_expected_output_counts": worklist.get("missing_expected_output_counts", {}),
        "scaffold_exists_count": int(worklist.get("scaffold_exists_count") or 0),
        "scaffold_audit_passed_count": int(worklist.get("scaffold_audit_passed_count") or 0),
        "complete_audit_passed_count": int(worklist.get("complete_audit_passed_count") or 0),
        "complete_audit_issue_count": int(worklist.get("complete_audit_issue_count") or 0),
        "issue_count": len(issues) + len(stale_issues),
        "issues": issues + stale_issues,
        "stale_issue_count": len(stale_issues),
        "stale_issues": stale_issues,
        "first_work_items": worklist.get("work_items", [])[:5],
    }


def summarize_authoring_lock(ledger_root: Path) -> dict[str, Any]:
    progress_root = ledger_root / "per_task_progress"
    active_lock = ledger_root / "active_authoring_task.json"
    report = per_task.audit_authoring_lock(
        ledger_root=ledger_root,
        progress_root=progress_root,
        active_lock=active_lock,
    )
    active_reports = [item for item in report.get("progress_reports", []) if item.get("active")]
    active_report = active_reports[0] if active_reports else None
    lock = report.get("lock") or {}
    return {
        "ledger_root": str(ledger_root),
        "progress_root": str(progress_root),
        "active_lock": str(active_lock),
        "passed": bool(report.get("passed")),
        "issue_count": int(report.get("issue_count", 0)),
        "issues": report.get("issues", []),
        "progress_file_count": int(report.get("progress_file_count", 0)),
        "active_progress_count": int(report.get("active_progress_count", 0)),
        "active_progress_paths": report.get("active_progress_paths", []),
        "active_slot_id": active_report.get("slot_id") if active_report else lock.get("slot_id"),
        "active_regime": active_report.get("regime") if active_report else lock.get("regime"),
        "active_repo_id": active_report.get("repo_id") if active_report else lock.get("repo_id"),
        "active_task_id": active_report.get("task_id") if active_report else lock.get("task_id"),
        "active_current_stage": active_report.get("current_stage") if active_report else None,
        "active_progress_path": active_report.get("progress_path") if active_report else lock.get("progress_path"),
    }


def summarize_mm_block(mm_root: Path) -> dict[str, Any]:
    change_sample = mm_root / "change_sample.jsonl"
    change_summary = load_json_if_exists(mm_root / "change_sample.summary.json")
    synthetic_report = load_json_if_exists(mm_root / "change_sample.synthetic_report.json")
    annotation_audit = load_json_if_exists(mm_root / "change_annotation_pack" / "annotation_audit.json")
    human_annotation_queue = load_json_if_exists(mm_root / "change_annotation_pack" / "human_annotation_queue.json")
    judge_prediction_queue = load_json_if_exists(mm_root / "change_judge_prediction_queue.json")
    judge_prediction_audit = load_json_if_exists(mm_root / "change_judge_predictions.audit.json")
    human_label_freeze_audit = load_json_if_exists(mm_root / "change_sample.human_labels.freeze_audit.json")
    annotation_current_issues = (
        audit_input_file_digests(annotation_audit, "annotation pack audit") if annotation_audit else []
    )
    annotation_current = bool(annotation_audit) and not annotation_current_issues
    annotation_issues = (
        [str(issue) for issue in annotation_audit.get("issues", [])] + annotation_current_issues
        if annotation_audit
        else ["annotation pack audit missing"]
    )
    queue_current_issues = (
        audit_input_file_digests(human_annotation_queue, "human annotation queue") if human_annotation_queue else []
    )
    queue_current = bool(human_annotation_queue) and not queue_current_issues
    queue_issues: list[str] = []
    if human_annotation_queue:
        if human_annotation_queue.get("artifact_type") != "mm_human_annotation_queue":
            queue_issues.append("human annotation queue has wrong artifact_type")
        if human_annotation_queue.get("formal_task_record") is not False:
            queue_issues.append("human annotation queue must be formal_task_record=false")
        if not human_annotation_queue.get("passed"):
            queue_issues.extend(str(issue) for issue in human_annotation_queue.get("issues", []))
        queue_issues.extend(queue_current_issues)
    else:
        queue_issues.append("human annotation queue missing")
    prediction_queue_current_issues = (
        audit_input_file_digests(judge_prediction_queue, "judge prediction queue") if judge_prediction_queue else []
    )
    prediction_queue_current = bool(judge_prediction_queue) and not prediction_queue_current_issues
    prediction_queue_issues: list[str] = []
    if judge_prediction_queue:
        if judge_prediction_queue.get("artifact_type") != "mm_judge_prediction_queue":
            prediction_queue_issues.append("judge prediction queue has wrong artifact_type")
        if judge_prediction_queue.get("formal_task_record") is not False:
            prediction_queue_issues.append("judge prediction queue must be formal_task_record=false")
        if not judge_prediction_queue.get("passed"):
            prediction_queue_issues.extend(str(issue) for issue in judge_prediction_queue.get("issues", []))
        prediction_queue_issues.extend(prediction_queue_current_issues)
    else:
        prediction_queue_issues.append("judge prediction queue missing")
    prediction_current_issues = (
        audit_input_file_digests(judge_prediction_audit, "judge prediction audit") if judge_prediction_audit else []
    )
    prediction_current = bool(judge_prediction_audit) and not prediction_current_issues
    prediction_issues = (
        [str(issue) for issue in judge_prediction_audit.get("issues", [])] + prediction_current_issues
        if judge_prediction_audit
        else ["judge prediction audit missing"]
    )
    freeze_current_issues = (
        audit_input_file_digests(human_label_freeze_audit, "human label freeze") if human_label_freeze_audit else []
    )
    freeze_current = bool(human_label_freeze_audit) and not freeze_current_issues
    freeze_publishable_candidate = bool(human_label_freeze_audit.get("publishable_candidate")) if human_label_freeze_audit else False
    freeze_issues = (
        [str(issue) for issue in human_label_freeze_audit.get("issues", [])] + freeze_current_issues
        if human_label_freeze_audit
        else ["human label freeze audit missing"]
    )
    change_gate = audit_calibration_gate(
        regime=CHANGE_REGIME,
        sample_path=change_sample,
        labels_path=mm_root / "change_sample.human_labels.jsonl",
        score_report_path=mm_root / "change_sample.human_report.json",
        synthetic_report_path=mm_root / "change_sample.synthetic_report.json",
        freeze_audit_path=mm_root / "change_sample.human_labels.freeze_audit.json",
        output_path=None,
    )
    return {
        "required_human_calibration_per_regime": 150,
        "change_calibration_items": count_jsonl_lines(change_sample),
        "change_unique_tasks": change_summary.get("unique_tasks", 0),
        "change_available_checkpoints": change_summary.get("available_checkpoints", 0),
        "change_sample_gate_passed": change_gate.get("sample_gate_passed"),
        "change_human_labeled_items": change_gate.get("human_labeled_records", 0),
        "change_publish_gate_passed": change_gate.get("publish_gate_passed"),
        "change_publishable": change_gate.get("publishable"),
        "change_publish_gate_issues": change_gate.get("publish_gate_issues", []),
        "change_calibration_gate_issue_count": change_gate.get("issue_count", 0),
        "change_calibration_gate_issues": change_gate.get("issues", []),
        "change_annotation_pack_available": bool(annotation_audit),
        "change_annotation_pack_current": annotation_current,
        "change_annotation_pack_passed": bool(annotation_audit.get("passed")) and annotation_current if annotation_audit else False,
        "change_annotation_pack_items": annotation_audit.get("annotation_items", 0) if annotation_audit else 0,
        "change_annotation_pack_issue_count": len(annotation_issues),
        "change_annotation_pack_issues": annotation_issues,
        "change_annotation_pack_stale_issue_count": len(annotation_current_issues),
        "change_annotation_pack_stale_issues": annotation_current_issues,
        "change_human_annotation_queue_available": bool(human_annotation_queue),
        "change_human_annotation_queue_current": queue_current,
        "change_human_annotation_queue_passed": bool(human_annotation_queue.get("passed")) and queue_current if human_annotation_queue else False,
        "change_human_annotation_queue_items": human_annotation_queue.get("queue_item_count", 0) if human_annotation_queue else 0,
        "change_human_annotation_queue_labeled": human_annotation_queue.get("labeled_count", 0) if human_annotation_queue else 0,
        "change_human_annotation_queue_unlabeled": human_annotation_queue.get("unlabeled_count", 0) if human_annotation_queue else 0,
        "change_human_annotation_queue_issue_count": len(queue_issues),
        "change_human_annotation_queue_issues": queue_issues,
        "change_human_annotation_queue_stale_issue_count": len(queue_current_issues),
        "change_human_annotation_queue_stale_issues": queue_current_issues,
        "change_judge_prediction_queue_available": bool(judge_prediction_queue),
        "change_judge_prediction_queue_current": prediction_queue_current,
        "change_judge_prediction_queue_passed": bool(judge_prediction_queue.get("passed")) and prediction_queue_current if judge_prediction_queue else False,
        "change_judge_prediction_queue_items": judge_prediction_queue.get("queue_item_count", 0) if judge_prediction_queue else 0,
        "change_judge_prediction_queue_completed": judge_prediction_queue.get("completed_prediction_count", 0) if judge_prediction_queue else 0,
        "change_judge_prediction_queue_missing": judge_prediction_queue.get("missing_prediction_count", 0) if judge_prediction_queue else 0,
        "change_judge_prediction_queue_issue_count": len(prediction_queue_issues),
        "change_judge_prediction_queue_issues": prediction_queue_issues,
        "change_judge_prediction_queue_stale_issue_count": len(prediction_queue_current_issues),
        "change_judge_prediction_queue_stale_issues": prediction_queue_current_issues,
        "change_judge_prediction_audit_available": bool(judge_prediction_audit),
        "change_judge_prediction_audit_current": prediction_current,
        "change_judge_prediction_audit_passed": bool(judge_prediction_audit.get("passed")) and prediction_current if judge_prediction_audit else False,
        "change_judge_prediction_audit_records": judge_prediction_audit.get("prediction_records", 0) if judge_prediction_audit else 0,
        "change_judge_prediction_audit_usable_records": judge_prediction_audit.get("usable_prediction_records", 0) if judge_prediction_audit else 0,
        "change_judge_prediction_audit_issue_count": len(prediction_issues),
        "change_judge_prediction_audit_issues": prediction_issues,
        "change_judge_prediction_audit_stale_issue_count": len(prediction_current_issues),
        "change_judge_prediction_audit_stale_issues": prediction_current_issues,
        "change_human_label_freeze_available": bool(human_label_freeze_audit),
        "change_human_label_freeze_current": freeze_current,
        "change_human_label_freeze_passed": (
            bool(human_label_freeze_audit.get("passed")) and freeze_publishable_candidate and freeze_current
            if human_label_freeze_audit
            else False
        ),
        "change_human_label_freeze_recovery_only": bool(human_label_freeze_audit.get("recovery_only")) if human_label_freeze_audit else False,
        "change_human_label_freeze_publishable_candidate": freeze_publishable_candidate,
        "change_human_label_freeze_records": human_label_freeze_audit.get("frozen_label_records", 0) if human_label_freeze_audit else 0,
        "change_human_label_freeze_issue_count": len(freeze_issues),
        "change_human_label_freeze_issues": freeze_issues,
        "change_human_label_freeze_stale_issue_count": len(freeze_current_issues),
        "change_human_label_freeze_stale_issues": freeze_current_issues,
        "change_synthetic_gate_passed": synthetic_report.get("passed_gate"),
        "change_synthetic_agreement": synthetic_report.get("agreement"),
        "change_synthetic_kappa": synthetic_report.get("cohen_kappa"),
        "change_synthetic_publishable": False,
        "construction_calibration_items": 0,
        "gate": "MM judge cannot be used for leaderboard until JA and Cohen's kappa pass thresholds.",
    }


def summarize_model_isolation(
    audit_path: Path,
    queue_path: Path = DEFAULT_MODEL_ROLE_QUEUE,
) -> dict[str, Any]:
    audit = load_json_if_exists(audit_path)
    queue = load_json_if_exists(queue_path)
    queue_current_issues = audit_input_file_digests(queue, "model role queue") if queue else []
    queue_current = bool(queue) and not queue_current_issues
    queue_issues: list[str] = []
    if queue:
        if queue.get("artifact_type") != "model_role_queue":
            queue_issues.append("model role queue has wrong artifact_type")
        if queue.get("formal_task_record") is not False:
            queue_issues.append("model role queue must be formal_task_record=false")
        if not queue.get("passed"):
            queue_issues.extend(str(issue) for issue in queue.get("issues", []))
        queue_issues.extend(queue_current_issues)
    else:
        queue_issues.append(f"model role queue missing: {queue_path}")
    if not audit:
        return {
            "path": str(audit_path),
            "available": False,
            "current": False,
            "passed": False,
            "publishable": False,
            "role_count": 0,
            "role_kind_counts": {},
            "families_by_kind": {},
            "issue_count": 1,
            "issues": [f"model isolation audit missing: {audit_path}"],
            "queue_path": str(queue_path),
            "queue_available": bool(queue),
            "queue_current": queue_current,
            "queue_passed": bool(queue.get("passed")) and queue_current if queue else False,
            "queue_item_count": int(queue.get("queue_item_count") or 0) if queue else 0,
            "queue_missing_role_count": int(queue.get("missing_role_count") or 0) if queue else 0,
            "queue_issue_count": len(queue_issues),
            "queue_issues": queue_issues,
            "queue_stale_issue_count": len(queue_current_issues),
            "queue_stale_issues": queue_current_issues,
        }
    current_issues = audit_input_file_digests(audit, "model isolation")
    current = not current_issues
    issues = [str(issue) for issue in audit.get("issues", [])] + current_issues
    return {
        "path": str(audit_path),
        "available": True,
        "current": current,
        "passed": bool(audit.get("passed")) and current,
        "publishable": bool(audit.get("publishable")) and current,
        "role_count": int(audit.get("role_count") or 0),
        "role_kind_counts": audit.get("role_kind_counts", {}),
        "families_by_kind": audit.get("families_by_kind", {}),
        "issue_count": len(issues),
        "issues": issues,
        "stale_issue_count": len(current_issues),
        "stale_issues": current_issues,
        "queue_path": str(queue_path),
        "queue_available": bool(queue),
        "queue_current": queue_current,
        "queue_passed": bool(queue.get("passed")) and queue_current if queue else False,
        "queue_item_count": int(queue.get("queue_item_count") or 0) if queue else 0,
        "queue_missing_role_count": int(queue.get("missing_role_count") or 0) if queue else 0,
        "queue_issue_count": len(queue_issues),
        "queue_issues": queue_issues,
        "queue_stale_issue_count": len(queue_current_issues),
        "queue_stale_issues": queue_current_issues,
    }


def summarize_deai_contrast(
    audit_path: Path,
    queue_path: Path = DEFAULT_DEAI_CONTRAST_QUEUE,
) -> dict[str, Any]:
    audit = load_json_if_exists(audit_path)
    queue = load_json_if_exists(queue_path)
    queue_current_issues = audit_input_file_digests(queue, "de-AI contrast queue") if queue else []
    queue_current = bool(queue) and not queue_current_issues
    queue_issues: list[str] = []
    if queue:
        if queue.get("artifact_type") != "de_ai_contrast_anchor_queue":
            queue_issues.append("de-AI contrast queue has wrong artifact_type")
        if queue.get("formal_task_record") is not False:
            queue_issues.append("de-AI contrast queue must be formal_task_record=false")
        if not queue.get("passed"):
            queue_issues.extend(str(issue) for issue in queue.get("issues", []))
        queue_issues.extend(queue_current_issues)
    else:
        queue_issues.append(f"de-AI contrast queue missing: {queue_path}")
    if not audit:
        return {
            "path": str(audit_path),
            "available": False,
            "current": False,
            "passed": False,
            "publishable": False,
            "anchor_count": 0,
            "model_family_count": 0,
            "website_type_count": 0,
            "fingerprint_rich_records": 0,
            "issue_count": 1,
            "issues": [f"de-AI contrast audit missing: {audit_path}"],
            "stale_issue_count": 0,
            "stale_issues": [],
            "queue_path": str(queue_path),
            "queue_available": bool(queue),
            "queue_current": queue_current,
            "queue_passed": bool(queue.get("passed")) and queue_current if queue else False,
            "queue_item_count": int(queue.get("queue_item_count") or 0) if queue else 0,
            "queue_missing_anchor_count": int(queue.get("missing_anchor_count") or 0) if queue else 0,
            "queue_missing_fingerprint_rich_count": int(queue.get("missing_fingerprint_rich_count") or 0) if queue else 0,
            "queue_issue_count": len(queue_issues),
            "queue_issues": queue_issues,
            "queue_stale_issue_count": len(queue_current_issues),
            "queue_stale_issues": queue_current_issues,
        }
    current_issues = audit_input_file_digests(audit, "de-AI contrast audit")
    current = not current_issues
    issues = [str(issue) for issue in audit.get("issues", [])] + current_issues
    return {
        "path": str(audit_path),
        "available": True,
        "current": current,
        "passed": bool(audit.get("passed")) and current,
        "publishable": bool(audit.get("publishable")) and current,
        "anchor_count": int(audit.get("anchor_count") or 0),
        "model_family_count": int(audit.get("model_family_count") or 0),
        "website_type_count": int(audit.get("website_type_count") or 0),
        "fingerprint_rich_records": int(audit.get("fingerprint_rich_records") or 0),
        "issue_count": len(issues),
        "issues": issues,
        "stale_issue_count": len(current_issues),
        "stale_issues": current_issues,
        "queue_path": str(queue_path),
        "queue_available": bool(queue),
        "queue_current": queue_current,
        "queue_passed": bool(queue.get("passed")) and queue_current if queue else False,
        "queue_item_count": int(queue.get("queue_item_count") or 0) if queue else 0,
        "queue_missing_anchor_count": int(queue.get("missing_anchor_count") or 0) if queue else 0,
        "queue_missing_fingerprint_rich_count": int(queue.get("missing_fingerprint_rich_count") or 0) if queue else 0,
        "queue_issue_count": len(queue_issues),
        "queue_issues": queue_issues,
        "queue_stale_issue_count": len(queue_current_issues),
        "queue_stale_issues": queue_current_issues,
    }


def summarize_construction_artifacts(draft_root: Path) -> dict[str, Any]:
    from ..regimes.construction.precheck import PRECHECK_ARTIFACT_TYPE, precheck_construction_task
    from ..regimes.construction.scoring import SCORE_ARTIFACT_TYPE
    from ..regimes.construction.task_digest import stable_task_digest

    planning_artifact_types = {
        "construction_repair_plan",
        "construction_evidence_worklist",
    }
    drafts = []
    template_artifacts = []
    planning_artifacts = []
    scores = []
    saved_strict_prechecks = []
    saved_structural_prechecks = []
    reference_actor_trace_templates = []
    if draft_root.exists():
        for path in sorted(draft_root.glob("*.json")):
            if path.name.endswith(".score.json"):
                scores.append(path)
            elif path.name.endswith(".structural_precheck.json"):
                saved_structural_prechecks.append(path)
            elif path.name.endswith(".precheck.json"):
                saved_strict_prechecks.append(path)
            elif path.name.endswith(".reference_actor_trace.template.json"):
                reference_actor_trace_templates.append(path)
                template_artifacts.append(path)
            else:
                payload = load_json_if_exists(path)
                if payload.get("artifact_type") in planning_artifact_types:
                    planning_artifacts.append(path)
                elif payload.get("regime") == CONSTRUCTION_REGIME and isinstance(payload.get("milestones"), list):
                    drafts.append(path)
                elif payload.get("artifact_type") or payload.get("formal_task_record") is False:
                    template_artifacts.append(path)
                else:
                    template_artifacts.append(path)
    draft_payloads = [(path, load_json_if_exists(path)) for path in drafts]
    strict_reports = [
        precheck_construction_task(payload, artifact_root=path.parent)
        for path, payload in draft_payloads
        if isinstance(payload, dict)
    ]
    structural_reports = [
        precheck_construction_task(payload, require_reference_actor=False, artifact_root=path.parent)
        for path, payload in draft_payloads
        if isinstance(payload, dict)
    ]
    draft_by_task_id = {
        str(payload.get("task_id")): payload
        for _, payload in draft_payloads
        if isinstance(payload, dict) and payload.get("task_id")
    }
    draft_path_by_task_id = {
        str(payload.get("task_id")): path
        for path, payload in draft_payloads
        if isinstance(payload, dict) and payload.get("task_id")
    }
    saved_precheck_issues: list[dict[str, Any]] = []

    def audit_saved_precheck(path: Path, *, expected_strict: bool) -> None:
        payload = load_json_if_exists(path)
        issues: list[str] = []
        if payload.get("artifact_type") != PRECHECK_ARTIFACT_TYPE:
            issues.append(f"artifact_type must be {PRECHECK_ARTIFACT_TYPE}")
        if payload.get("formal_task_record") is not False:
            issues.append("formal_task_record must be false")
        if payload.get("require_reference_actor") is not expected_strict:
            issues.append(f"require_reference_actor must be {str(expected_strict).lower()}")
        task_id = payload.get("task_id")
        matching_task = draft_by_task_id.get(str(task_id)) if task_id else None
        matching_task_path = draft_path_by_task_id.get(str(task_id)) if task_id else None
        if not matching_task:
            issues.append("saved precheck does not match a current construction draft task_id")
        elif payload.get("task_sha256") != stable_task_digest(matching_task):
            issues.append("saved precheck task_sha256 does not match current draft content")
        input_files = payload.get("input_files")
        if not isinstance(input_files, dict):
            issues.append("saved precheck input_files is missing or malformed")
        else:
            task_input = input_files.get("task")
            if not isinstance(task_input, dict):
                issues.append("saved precheck input_files.task is missing or malformed")
            else:
                raw_path = task_input.get("path")
                current_path = Path(str(raw_path)) if raw_path else None
                if (
                    matching_task_path is not None
                    and current_path is not None
                    and current_path.resolve() != matching_task_path.resolve()
                ):
                    issues.append("saved precheck input_files.task.path does not match current draft path")
                if file_sha256(current_path) != task_input.get("sha256"):
                    issues.append("saved precheck input_files.task changed or is missing")
        if payload.get("artifact_root") and Path(str(payload.get("artifact_root"))).resolve() != draft_root.resolve():
            issues.append("saved precheck artifact_root does not match construction draft root")
        if issues:
            saved_precheck_issues.append({"path": str(path), "issues": issues})

    for path in saved_strict_prechecks:
        audit_saved_precheck(path, expected_strict=True)
    for path in saved_structural_prechecks:
        audit_saved_precheck(path, expected_strict=False)

    reference_actor_template_issues: list[dict[str, Any]] = []
    for path in reference_actor_trace_templates:
        payload = load_json_if_exists(path)
        issues: list[str] = []
        if payload.get("artifact_type") != "reference_actor_trace_template_only":
            issues.append("artifact_type must be reference_actor_trace_template_only")
        if payload.get("formal_task_record") is not False:
            issues.append("formal_task_record must be false")
        if payload.get("passed") is not False:
            issues.append("reference actor trace templates must be passed=false")
        if payload.get("source_kind") != "spec_only_actor_run":
            issues.append("source_kind must be spec_only_actor_run")
        task_id = payload.get("task_id")
        matching_task = draft_by_task_id.get(str(task_id)) if task_id else None
        if not matching_task:
            issues.append("template does not match a current construction draft task_id")
        else:
            if payload.get("repo_id") != matching_task.get("repo_id"):
                issues.append("template repo_id does not match current draft")
            if payload.get("task_sha256") != stable_task_digest(matching_task):
                issues.append("template task_sha256 missing or stale vs current draft")
        if issues:
            reference_actor_template_issues.append({"path": str(path), "issues": issues})

    score_snapshot_issues: list[dict[str, Any]] = []
    valid_score_snapshots = 0
    for path in scores:
        payload = load_json_if_exists(path)
        issues: list[str] = []
        if payload.get("artifact_type") != SCORE_ARTIFACT_TYPE:
            issues.append(f"artifact_type must be {SCORE_ARTIFACT_TYPE}")
        if payload.get("formal_task_record") is not False:
            issues.append("formal_task_record must be false")
        if payload.get("publishable") is not False:
            issues.append("construction score snapshots must be publishable=false")
        if payload.get("source_kind") != "score_snapshot_only":
            issues.append("source_kind must be score_snapshot_only")
        if issues:
            score_snapshot_issues.append({"path": str(path), "issues": issues})
        else:
            valid_score_snapshots += 1
    schema_check_passed = 0
    schema_check_total = 0
    strict_issues: list[str] = []
    strict_blocking_requirements: dict[str, dict[str, Any]] = {}
    for report in strict_reports + structural_reports:
        for check in report.get("checks", []):
            if check.get("name") == "task_package_schema":
                schema_check_total += 1
                if check.get("passed") is True:
                    schema_check_passed += 1
        strict_issues.extend(str(issue) for issue in report.get("issues", []))
        for blocker in report.get("blocking_requirements", []) or []:
            if not isinstance(blocker, dict):
                continue
            key = str(blocker.get("key") or "")
            if not key:
                continue
            existing = strict_blocking_requirements.setdefault(
                key,
                {
                    "key": key,
                    "label": blocker.get("label"),
                    "action": blocker.get("action"),
                    "count": 0,
                    "matched_issues": [],
                    "required_evidence": [],
                },
            )
            existing["count"] += 1
            existing["matched_issues"].extend(blocker.get("matched_issues", []) or [])
            for evidence in blocker.get("required_evidence", []) or []:
                if evidence not in existing["required_evidence"]:
                    existing["required_evidence"].append(evidence)
    return {
        "draft_root": str(draft_root),
        "draft_count": len(drafts),
        "score_count": len(scores),
        "valid_score_snapshot_count": valid_score_snapshots,
        "score_snapshot_issue_count": len(score_snapshot_issues),
        "score_snapshot_issues": score_snapshot_issues,
        "strict_precheck_count": len(strict_reports),
        "strict_precheck_passed": sum(1 for report in strict_reports if report.get("passed")),
        "structural_precheck_count": len(structural_reports),
        "structural_precheck_passed": sum(1 for report in structural_reports if report.get("passed")),
        "saved_strict_precheck_artifact_count": len(saved_strict_prechecks),
        "saved_structural_precheck_artifact_count": len(saved_structural_prechecks),
        "saved_precheck_issue_count": len(saved_precheck_issues),
        "saved_precheck_issues": saved_precheck_issues,
        "reference_actor_trace_template_count": len(reference_actor_trace_templates),
        "reference_actor_trace_template_issue_count": len(reference_actor_template_issues),
        "reference_actor_trace_template_issues": reference_actor_template_issues,
        "task_package_schema_precheck_passed": schema_check_passed,
        "task_package_schema_precheck_total": schema_check_total,
        "strict_issues": strict_issues,
        "strict_blocking_requirements": list(strict_blocking_requirements.values()),
        "draft_paths": [str(path) for path in drafts],
        "template_artifact_count": len(template_artifacts),
        "template_artifact_paths": [str(path) for path in template_artifacts],
        "planning_artifact_count": len(planning_artifacts),
        "planning_artifact_paths": [str(path) for path in planning_artifacts],
    }


def summarize_schema_gate(schema_root: Path = DEFAULT_SCHEMA_ROOT) -> dict[str, Any]:
    metric_path = schema_root / "metrics.schema.json"
    package_path = schema_root / "task_package.schema.json"
    metrics_disk = load_json_if_exists(metric_path)
    package_disk = load_json_if_exists(package_path)
    metrics_expected = metric_schema()
    package_expected = task_package_json_schema()
    required_metrics = {"WCS", "TCS", "CCS", "TD", "RCS", "ITR", "DCS", "QS", "JA_B", "JA_A"}
    metric_items = metrics_disk.get("metrics", []) if metrics_disk else []
    metric_keys = {item.get("key") for item in metric_items if isinstance(item, dict)}
    primary_metric_keys = {item.get("key") for item in metric_items if isinstance(item, dict) and item.get("primary")}
    primary_mllm_metrics = sorted(
        str(item.get("key"))
        for item in metric_items
        if isinstance(item, dict) and item.get("primary") and item.get("uses_mllm")
    )
    missing_metrics = sorted(required_metrics - metric_keys)
    regimes = list_regimes()
    registered_regime_keys = {item["key"] for item in regimes}
    missing_regimes = sorted({CHANGE_REGIME, CONSTRUCTION_REGIME} - registered_regime_keys)
    required_interfaces = {"author_task", "build_verifier", "score", "package"}
    missing_regime_interfaces = [
        f"{item['key']}.{name}"
        for item in regimes
        for name in sorted(required_interfaces)
        if not item.get("interfaces", {}).get(name)
    ]
    required_module_wrappers = {
        CHANGE_REGIME: {"authoring", "verifier", "scoring"},
        CONSTRUCTION_REGIME: {"authoring", "trajectory_verifier", "scoring"},
    }
    missing_regime_module_wrappers = [
        f"{item['key']}.{name}"
        for item in regimes
        for name in sorted(required_module_wrappers.get(item["key"], set()))
        if not item.get("module_wrappers", {}).get(name)
    ]
    missing_files = [
        str(path)
        for path, data in [(metric_path, metrics_disk), (package_path, package_disk)]
        if not data
    ]
    issues: list[str] = []
    if missing_files:
        issues.append("schema files missing")
    if missing_metrics:
        issues.append("required metrics missing")
    if primary_mllm_metrics:
        issues.append("primary metrics use MLLM")
    if missing_regimes:
        issues.append("required regime plugins missing")
    if missing_regime_interfaces:
        issues.append("required regime plugin interfaces missing")
    if missing_regime_module_wrappers:
        issues.append("required regime module wrappers missing")
    if metrics_disk and metrics_disk != metrics_expected:
        issues.append("metrics schema file is stale vs taxonomy source")
    if package_disk and package_disk != package_expected:
        issues.append("task package schema file is stale vs taxonomy source")
    return {
        "schema_root": str(schema_root),
        "metrics_path": str(metric_path),
        "task_package_path": str(package_path),
        "metrics_available": bool(metrics_disk),
        "task_package_available": bool(package_disk),
        "metrics_current": bool(metrics_disk) and metrics_disk == metrics_expected,
        "task_package_current": bool(package_disk) and package_disk == package_expected,
        "metric_count": len(metric_items),
        "required_metrics_present": not missing_metrics,
        "missing_metrics": missing_metrics,
        "primary_metric_keys": sorted(str(key) for key in primary_metric_keys if key),
        "primary_mllm_metrics": primary_mllm_metrics,
        "registered_regimes": regimes,
        "registered_regime_keys": sorted(registered_regime_keys),
        "missing_regimes": missing_regimes,
        "missing_regime_interfaces": missing_regime_interfaces,
        "missing_regime_module_wrappers": missing_regime_module_wrappers,
        "passed": not issues,
        "issues": issues,
    }


def summarize_capability_split(
    audit_path: Path = DEFAULT_CAPABILITY_SPLIT_AUDIT,
    split_path: Path = DEFAULT_CAPABILITY_SPLIT_PATH,
) -> dict[str, Any]:
    audit = load_json_if_exists(audit_path)
    gate_status_counts: dict[str, dict[str, int]] = {}
    if split_path.exists():
        for record in read_jsonl(split_path):
            for gate_name, gate in record.get("gates", {}).items():
                if gate_name == "signal_layers" or not isinstance(gate, dict):
                    continue
                status = str(gate.get("status", "unknown"))
                gate_status_counts.setdefault(gate_name, {})[status] = gate_status_counts.setdefault(gate_name, {}).get(status, 0) + 1
    total = int(audit.get("total", 0)) if audit else 0
    passed = int(audit.get("passed", 0)) if audit else 0
    failed = int(audit.get("failed", 0)) if audit else 0
    return {
        "audit_path": str(audit_path),
        "available": bool(audit),
        "split_path": str(split_path),
        "total": total,
        "passed": passed,
        "failed": failed,
        "mm_checkpoint_total": int(audit.get("mm_checkpoint_total", 0)) if audit else 0,
        "gate_status_counts": gate_status_counts,
        "schema_version": audit.get("schema_version") if audit else None,
        "passed_all": bool(audit) and total > 0 and failed == 0 and passed == total,
    }


def summarize_signal_layers(
    split_path: Path = DEFAULT_CAPABILITY_SPLIT_PATH,
    audit_path: Path = DEFAULT_SIGNAL_LAYER_AUDIT,
) -> dict[str, Any]:
    saved_audit = load_json_if_exists(audit_path)
    live_audit = audit_signal_layers(split_path, output_path=None)
    saved_total = saved_audit.get("total") if saved_audit else None
    saved_failed = saved_audit.get("failed") if saved_audit else None
    return {
        "audit_path": str(audit_path),
        "split_path": str(split_path),
        "saved_available": bool(saved_audit),
        "saved_matches_live_counts": bool(saved_audit)
        and saved_total == live_audit.get("total")
        and saved_failed == live_audit.get("failed"),
        "total": int(live_audit.get("total", 0)),
        "passed": int(live_audit.get("passed", 0)),
        "failed": int(live_audit.get("failed", 0)),
        "issue_count": int(live_audit.get("issue_count", 0)),
        "warning_items": int(live_audit.get("warning_items", 0)),
        "mm_checkpoint_total": int(live_audit.get("mm_checkpoint_total", 0)),
        "passed_all": bool(live_audit.get("passed_all")),
        "issues": live_audit.get("issues", []),
        "invariants": live_audit.get("invariants", []),
    }


def summarize_mm_judge_spec(
    split_path: Path = DEFAULT_CAPABILITY_SPLIT_PATH,
    audit_path: Path = DEFAULT_MM_JUDGE_SPEC_AUDIT,
    regime: str | None = CHANGE_REGIME,
) -> dict[str, Any]:
    saved_audit = load_json_if_exists(audit_path)
    live_audit = audit_mm_judge_spec(split_path, output_path=None, regime=regime, min_checkpoints=1)
    saved_checkpoint_count = saved_audit.get("checkpoint_count") if saved_audit else None
    saved_invalid_count = saved_audit.get("invalid_checkpoint_count") if saved_audit else None
    return {
        "audit_path": str(audit_path),
        "split_path": str(split_path),
        "regime": regime,
        "saved_available": bool(saved_audit),
        "saved_matches_live_counts": bool(saved_audit)
        and saved_checkpoint_count == live_audit.get("checkpoint_count")
        and saved_invalid_count == live_audit.get("invalid_checkpoint_count"),
        "passed": bool(live_audit.get("passed")),
        "publishable": bool(live_audit.get("publishable")),
        "record_count": int(live_audit.get("record_count", 0)),
        "task_count": int(live_audit.get("task_count", 0)),
        "checkpoint_count": int(live_audit.get("checkpoint_count", 0)),
        "unique_checkpoint_ids": int(live_audit.get("unique_checkpoint_ids", 0)),
        "invalid_checkpoint_count": int(live_audit.get("invalid_checkpoint_count", 0)),
        "warning_count": int(live_audit.get("warning_count", 0)),
        "issue_count": int(live_audit.get("issue_count", 0)),
        "issues": live_audit.get("issues", []),
        "warnings": live_audit.get("warnings", []),
    }


def summarize_mm_judge_perturbations(
    audit_path: Path = DEFAULT_MM_JUDGE_PERTURBATION_AUDIT,
    queue_path: Path = DEFAULT_MM_JUDGE_PERTURBATION_QUEUE,
) -> dict[str, Any]:
    audit = load_json_if_exists(audit_path)
    queue = load_json_if_exists(queue_path)
    queue_current_issues = audit_input_file_digests(queue, "MM judge perturbation queue") if queue else []
    queue_current = bool(queue) and not queue_current_issues
    queue_issues: list[str] = []
    if queue:
        if queue.get("artifact_type") != "mm_judge_perturbation_queue":
            queue_issues.append("MM judge perturbation queue has wrong artifact_type")
        if queue.get("formal_task_record") is not False:
            queue_issues.append("MM judge perturbation queue must be formal_task_record=false")
        if not queue.get("passed"):
            queue_issues.extend(str(issue) for issue in queue.get("issues", []))
        queue_issues.extend(queue_current_issues)
    else:
        queue_issues.append(f"MM judge perturbation queue missing: {queue_path}")
    if not audit:
        return {
            "path": str(audit_path),
            "available": False,
            "current": False,
            "passed": False,
            "publishable": False,
            "group_count": 0,
            "record_count": 0,
            "label_flip_group_count": 0,
            "perturbation_types": [],
            "required_perturbation_types": [],
            "issue_count": 1,
            "issues": [f"MM judge perturbation audit missing: {audit_path}"],
            "stale_issue_count": 0,
            "stale_issues": [],
            "queue_path": str(queue_path),
            "queue_available": bool(queue),
            "queue_current": queue_current,
            "queue_passed": bool(queue.get("passed")) and queue_current if queue else False,
            "queue_item_count": int(queue.get("queue_item_count") or 0) if queue else 0,
            "queue_missing_group_count": int(queue.get("missing_group_count") or 0) if queue else 0,
            "queue_required_group_count": int(queue.get("required_group_count") or 0) if queue else 0,
            "queue_issue_count": len(queue_issues),
            "queue_issues": queue_issues,
            "queue_stale_issue_count": len(queue_current_issues),
            "queue_stale_issues": queue_current_issues,
        }
    current_issues = audit_input_file_digests(audit, "MM judge perturbation audit")
    current = not current_issues
    issues = [str(issue) for issue in audit.get("issues", [])] + current_issues
    return {
        "path": str(audit_path),
        "available": True,
        "current": current,
        "passed": bool(audit.get("passed")) and current,
        "publishable": bool(audit.get("publishable")) and current,
        "group_count": int(audit.get("group_count") or 0),
        "record_count": int(audit.get("record_count") or 0),
        "label_flip_group_count": int(audit.get("label_flip_group_count") or 0),
        "perturbation_types": audit.get("perturbation_types", []),
        "required_perturbation_types": audit.get("required_perturbation_types", []),
        "missing_required_perturbation_types": audit.get("missing_required_perturbation_types", []),
        "issue_count": len(issues),
        "issues": issues,
        "stale_issue_count": len(current_issues),
        "stale_issues": current_issues,
        "queue_path": str(queue_path),
        "queue_available": bool(queue),
        "queue_current": queue_current,
        "queue_passed": bool(queue.get("passed")) and queue_current if queue else False,
        "queue_item_count": int(queue.get("queue_item_count") or 0) if queue else 0,
        "queue_missing_group_count": int(queue.get("missing_group_count") or 0) if queue else 0,
        "queue_required_group_count": int(queue.get("required_group_count") or 0) if queue else 0,
        "queue_issue_count": len(queue_issues),
        "queue_issues": queue_issues,
        "queue_stale_issue_count": len(queue_current_issues),
        "queue_stale_issues": queue_current_issues,
    }


def summarize_metareval(metareval_root: Path) -> dict[str, Any]:
    batch_path = metareval_root / "formal_tasks.meta_eval_summary.json"
    if batch_path.exists():
        batch = load_json_if_exists(batch_path)
        return {
            "root": str(metareval_root),
            "batch_path": str(batch_path),
            "report_count": int(batch.get("total_tasks", 0)),
            "passed_count": int(batch.get("passed", 0)),
            "failed_count": int(batch.get("failed", 0)),
            "reference_passed": int(batch.get("reference_passed", 0)),
            "original_failed": int(batch.get("original_failed", 0)),
            "bad_solutions_failed": int(batch.get("bad_solutions_failed", 0)),
            "reproducible": int(batch.get("reproducible", 0)),
            "missing_reference": int(batch.get("missing_reference", 0)),
            "missing_original": int(batch.get("missing_original", 0)),
            "missing_bad_solution": int(batch.get("missing_bad_solution", 0)),
            "missing_repeat": int(batch.get("missing_repeat", 0)),
            "open_issues": [
                f"{issue} ({count})"
                for issue, count in list(batch.get("issue_counts", {}).items())[:20]
            ],
        }
    reports = []
    if metareval_root.exists():
        reports = [
            load_json_if_exists(path)
            for path in sorted(metareval_root.glob("*.json"))
            if path.name != "formal_tasks.meta_eval_summary.json"
        ]
    return {
        "root": str(metareval_root),
        "batch_path": None,
        "report_count": len(reports),
        "passed_count": sum(1 for report in reports if report.get("passed")),
        "failed_count": sum(1 for report in reports if report and not report.get("passed")),
        "reference_passed": sum(1 for report in reports if report.get("reference_passed")),
        "original_failed": sum(1 for report in reports if report.get("original_failed")),
        "bad_solutions_failed": sum(1 for report in reports if report.get("bad_solutions_failed")),
        "reproducible": sum(1 for report in reports if report.get("reproducible")),
        "missing_reference": None,
        "missing_original": None,
        "missing_bad_solution": None,
        "missing_repeat": None,
        "open_issues": [
            issue
            for report in reports
            for issue in report.get("issues", [])
        ][:20],
    }


def summarize_negative_controls(plan_path: Path, audit_path: Path = DEFAULT_NEGATIVE_CONTROL_AUDIT) -> dict[str, Any]:
    plan = load_json_if_exists(plan_path)
    audit = load_json_if_exists(audit_path)
    if not plan:
        return {
            "path": str(plan_path),
            "audit_path": str(audit_path),
            "available": False,
            "total_tasks": 0,
            "covered_tasks": 0,
            "needs_execution_tasks": 0,
            "execution_ready_tasks": 0,
            "execution_not_ready_tasks": 0,
            "total_recommended_controls": 0,
            "control_counts": {},
            "state_plan_source_counts": {},
            "source_kind_counts": {},
            "valid_bad_reports": 0,
            "invalid_bad_reports": 0,
            "missing_bad_reports": 0,
            "expected_check_covered": 0,
        }
    total_tasks = int(audit.get("total_tasks", plan.get("total_tasks", 0))) if audit else int(plan.get("total_tasks", 0))
    valid_bad_reports = int(audit.get("valid_bad_reports", 0)) if audit else 0
    invalid_bad_reports = int(audit.get("invalid_bad_reports", 0)) if audit else 0
    missing_bad_reports = (
        int(audit.get("missing_bad_reports", total_tasks)) if audit else int(plan.get("total_tasks", 0))
    )
    covered_tasks = valid_bad_reports if audit else int(plan.get("covered_tasks", 0))
    needs_execution_tasks = (
        invalid_bad_reports + missing_bad_reports if audit else int(plan.get("needs_execution_tasks", 0))
    )
    return {
        "path": str(plan_path),
        "audit_path": str(audit_path),
        "available": True,
        "total_tasks": total_tasks,
        "covered_tasks": covered_tasks,
        "needs_execution_tasks": needs_execution_tasks,
        "execution_ready_tasks": int(plan.get("execution_ready_tasks", 0)),
        "execution_not_ready_tasks": int(plan.get("execution_not_ready_tasks", 0)),
        "total_recommended_controls": int(plan.get("total_recommended_controls", 0)),
        "control_counts": plan.get("control_counts", {}),
        "state_plan_source_counts": plan.get("state_plan_source_counts", {}),
        "source_kind_counts": plan.get("source_kind_counts", {}),
        "valid_bad_reports": valid_bad_reports,
        "invalid_bad_reports": invalid_bad_reports,
        "missing_bad_reports": missing_bad_reports,
        "expected_check_covered": int(audit.get("expected_check_covered", 0)) if audit else 0,
    }


def build_coverage_gaps(
    ledger_root: Path,
    output_path: Path,
    task_root: Path = DEFAULT_TASK_ROOT,
    candidate_path: Path = DEFAULT_CANDIDATE_PATH,
    candidate_summary_path: Path = DEFAULT_CANDIDATE_SUMMARY_PATH,
    schema_root: Path = DEFAULT_SCHEMA_ROOT,
    authoring_audit_path: Path = DEFAULT_AUTHORING_AUDIT_PATH,
    strict_authoring_audit_path: Path = DEFAULT_STRICT_AUTHORING_AUDIT_PATH,
    strict_freeze_audit_path: Path = DEFAULT_STRICT_FREEZE_AUDIT_PATH,
    duplicate_repo_repair_plan_path: Path = DEFAULT_DUPLICATE_REPO_REPAIR_PLAN,
    strict_authoring_repair_plan_path: Path = DEFAULT_STRICT_AUTHORING_REPAIR_PLAN,
    strict_freeze_worklist_path: Path = DEFAULT_STRICT_FREEZE_WORKLIST,
    capability_split_audit: Path = DEFAULT_CAPABILITY_SPLIT_AUDIT,
    capability_split_path: Path = DEFAULT_CAPABILITY_SPLIT_PATH,
    construction_capability_split_audit: Path = DEFAULT_CONSTRUCTION_CAPABILITY_SPLIT_AUDIT,
    construction_capability_split_path: Path = DEFAULT_CONSTRUCTION_CAPABILITY_SPLIT_PATH,
    signal_layer_audit: Path = DEFAULT_SIGNAL_LAYER_AUDIT,
    mm_judge_spec_audit: Path = DEFAULT_MM_JUDGE_SPEC_AUDIT,
    construction_mm_judge_spec_audit: Path = DEFAULT_CONSTRUCTION_MM_JUDGE_SPEC_AUDIT,
    mm_judge_perturbation_audit: Path = DEFAULT_MM_JUDGE_PERTURBATION_AUDIT,
    model_isolation_audit: Path = DEFAULT_MODEL_ISOLATION_AUDIT,
    deai_contrast_audit: Path = DEFAULT_DEAI_CONTRAST_AUDIT,
    mm_protocol_repair_plan_path: Path = DEFAULT_MM_PROTOCOL_REPAIR_PLAN,
    mm_root: Path = DEFAULT_MM_CALIBRATION_ROOT,
    construction_draft_root: Path = DEFAULT_CONSTRUCTION_DRAFT_ROOT,
    construction_repair_plan_path: Path = DEFAULT_CONSTRUCTION_REPAIR_PLAN,
    construction_evidence_worklist_path: Path = DEFAULT_CONSTRUCTION_EVIDENCE_WORKLIST,
    readiness_repair_plan_path: Path = DEFAULT_0618_READINESS_REPAIR_PLAN,
    metareval_root: Path = DEFAULT_METAREVAL_ROOT,
    negative_control_plan: Path = DEFAULT_NEGATIVE_CONTROL_PLAN,
    negative_control_audit: Path = DEFAULT_NEGATIVE_CONTROL_AUDIT,
    readiness_audit_path: Path = DEFAULT_0618_READINESS_AUDIT,
) -> dict[str, Any]:
    report = {
        "schema_version": "2026-06-18",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "object": "Website Continuity",
        "regimes": {
            CHANGE_REGIME: summarize_change_regime(ledger_root, task_root),
            CONSTRUCTION_REGIME: summarize_construction_regime(ledger_root),
        },
        "change_ledger_alignment": summarize_change_ledger_alignment(ledger_root, task_root),
        "candidate_pool": summarize_candidate_pool(candidate_path, candidate_summary_path, task_root),
        "capture_runtime": summarize_capture_runtime(),
        "schema_gate": summarize_schema_gate(schema_root),
        "capability_split": summarize_capability_split(capability_split_audit, capability_split_path),
        "construction_capability_split": summarize_capability_split(
            construction_capability_split_audit,
            construction_capability_split_path,
        ),
        "signal_layers": summarize_signal_layers(capability_split_path, signal_layer_audit),
        "mm_judge_spec": summarize_mm_judge_spec(
            capability_split_path,
            mm_judge_spec_audit,
            regime=CHANGE_REGIME,
        ),
        "construction_mm_judge_spec": summarize_mm_judge_spec(
            construction_capability_split_path,
            construction_mm_judge_spec_audit,
            regime=CONSTRUCTION_REGIME,
        ),
        "mm_judge_perturbations": summarize_mm_judge_perturbations(mm_judge_perturbation_audit),
        "model_isolation": summarize_model_isolation(model_isolation_audit),
        "de_ai_contrast": summarize_deai_contrast(deai_contrast_audit),
        "authoring_data_audit": summarize_authoring_audit(authoring_audit_path),
        "strict_authoring_data_audit": summarize_strict_authoring_audit(strict_authoring_audit_path),
        "strict_freeze_authoring_audit": summarize_strict_authoring_audit(strict_freeze_audit_path),
        "duplicate_repo_repair_plan": summarize_duplicate_repo_repair_plan(
            duplicate_repo_repair_plan_path,
            strict_freeze_audit_path,
        ),
        "strict_authoring_repair_plan": summarize_strict_authoring_repair_plan(
            strict_authoring_repair_plan_path,
            strict_freeze_audit_path,
        ),
        "strict_freeze_worklist": summarize_strict_freeze_worklist(
            strict_freeze_worklist_path,
            strict_authoring_repair_plan_path,
        ),
        "authoring_lock": summarize_authoring_lock(ledger_root),
        "mm_block": summarize_mm_block(mm_root),
        "construction_artifacts": summarize_construction_artifacts(construction_draft_root),
        "metareval": summarize_metareval(metareval_root),
        "negative_controls": summarize_negative_controls(negative_control_plan, negative_control_audit),
    }
    report["mm_protocol_repair_plan"] = summarize_mm_protocol_repair_plan(mm_protocol_repair_plan_path, report)
    strict_publishable = int(report["strict_freeze_authoring_audit"].get("strict_clean_task_count") or 0)
    report["change_ledger_alignment"]["publishable_formal_task_count"] = strict_publishable
    report["change_ledger_alignment"]["strict_publishable_formal_task_count"] = strict_publishable
    change_alignment = report["change_ledger_alignment"]
    change_regime = report["regimes"][CHANGE_REGIME]
    change_regime["ledger_accepted"] = change_regime.get("accepted", 0)
    change_regime["materialized_formal_task_count"] = change_alignment.get("evaluable_formal_task_count", 0)
    change_regime["unmaterialized_accepted_count"] = change_alignment.get("accepted_without_formal_task_count", 0)
    change_regime["strict_publishable_task_count"] = strict_publishable
    change_regime["materialized_progress_label"] = (
        f"{change_regime['materialized_formal_task_count']}/{change_regime['target_total']}"
    )
    report["denominator_consistency"] = audit_denominator_consistency_from_status(report)
    report["eval_protocol"] = audit_eval_protocol_from_status(report)
    report["readiness_0618"] = audit_readiness_from_status(report)
    report["construction_repair_plan"] = summarize_construction_repair_plan(construction_repair_plan_path, report)
    report["construction_evidence_worklist"] = summarize_construction_evidence_worklist(
        construction_evidence_worklist_path,
        construction_repair_plan_path,
    )
    report["readiness_repair_plan"] = summarize_readiness_repair_plan(readiness_repair_plan_path, report)
    write_json(readiness_audit_path, report["readiness_0618"])
    write_json(output_path, report)
    return report


def render_status(report: dict[str, Any]) -> str:
    change = report["regimes"][CHANGE_REGIME]
    construction = report["regimes"][CONSTRUCTION_REGIME]
    lines = [
        "# Website Continuity Status",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Regime Progress",
        "",
        (
            f"- Change (B): `{change.get('materialized_formal_task_count', report['change_ledger_alignment']['evaluable_formal_task_count'])}/{change['target_total']}` "
            f"formal task rows materialized, `{change['accepted']}` ledger accepted, "
            f"`{report['change_ledger_alignment']['evaluable_formal_task_count']}` formal task rows exportable, "
            f"`{report['strict_freeze_authoring_audit']['strict_clean_task_count']}` strict-publishable, "
            f"first open `{change['first_open']}`."
        ),
        (
            f"- Construction (A): `{construction['accepted']}/{construction['target_total']}` strict accepted, "
            f"raw accepted=`{construction.get('raw_accepted', construction['accepted'])}`, "
            f"invalid accepted=`{construction.get('invalid_accepted', 0)}`, "
            f"first open `{construction['first_open']}`."
        ),
            (
                f"- Change ledger/formal alignment: passed=`{report['change_ledger_alignment']['passed']}`, "
                f"accepted_without_formal=`{report['change_ledger_alignment']['accepted_without_formal_task_count']}`, "
                f"formal_without_ledger=`{report['change_ledger_alignment']['formal_without_accepted_ledger_count']}`."
            ),
            (
                f"- Change non-evaluable planning rows: "
                f"`{report['change_ledger_alignment'].get('non_evaluable_planning_row_count', 0)}` "
                "(not formal acceptance; block publish until individually materialized/frozen)."
            ),
            (
                "- Unmaterialized accepted ledger rows by source: "
                + format_count_map(report["change_ledger_alignment"].get("unmaterialized_accepted_source_counts", {}))
                + "."
            ),
        (
            f"- Denominator consistency: passed=`{report['denominator_consistency']['passed']}`, "
            f"ledger=`{report['denominator_consistency']['summary']['ledger_accepted']}`, "
            f"evaluable=`{report['denominator_consistency']['summary']['evaluable_formal_task_count']}`, "
            f"publishable=`{report['denominator_consistency']['summary']['publishable_formal_task_count']}`."
        ),
        "",
        "## Current Lanes",
        "",
        "- Lane 0: schema/ledger/eval protocol alignment.",
        "- Lane 1: B change-regime task authoring to 400.",
            "- Lane 2: MM-block judge protocol and JA calibration.",
            "- Lane 3: A construction-regime minimal loop, then 50-80 pilot, then scale.",
            "",
            "## Schema Gates",
            "",
            (
                f"- Metrics schema: available=`{report['schema_gate']['metrics_available']}`, "
                f"current=`{report['schema_gate']['metrics_current']}`, "
                f"metrics=`{report['schema_gate']['metric_count']}`, "
                f"required present=`{report['schema_gate']['required_metrics_present']}`."
            ),
            (
                f"- Task package schema: available=`{report['schema_gate']['task_package_available']}`, "
                f"current=`{report['schema_gate']['task_package_current']}`."
            ),
            (
                f"- Primary metrics: `{', '.join(report['schema_gate']['primary_metric_keys'])}`; "
                f"primary MLLM metrics=`{', '.join(report['schema_gate']['primary_mllm_metrics']) if report['schema_gate']['primary_mllm_metrics'] else 'none'}`."
            ),
            (
                f"- Shared capture runtime: passed=`{report['capture_runtime']['passed']}`, "
                f"facade=`{report['capture_runtime']['facade_module']}`, "
                f"capture=`{report['capture_runtime']['underlying_capture_module']}`, "
                f"runability=`{report['capture_runtime']['underlying_runability_module']}`, "
                f"regimes=`{', '.join(report['capture_runtime']['shared_for_regimes'])}`."
            ),
            (
                "- Regime plugins: "
                + (
                    "; ".join(
                        f"`{item['key']}` primary=`{', '.join(item['primary_metrics'])}` "
                        f"class=`{item['plugin_class']}` "
                        f"interfaces=`{', '.join(name for name, ok in sorted(item.get('interfaces', {}).items()) if ok)}` "
                        f"wrappers=`{', '.join(name for name, ok in sorted(item.get('module_wrappers', {}).items()) if ok)}`"
                        for item in report["schema_gate"].get("registered_regimes", [])
                    )
                    if report["schema_gate"].get("registered_regimes")
                    else "`none`"
                )
                + "."
            ),
            (
                "- Schema gate issues: "
                + ("; ".join(f"`{issue}`" for issue in report["schema_gate"]["issues"]) if report["schema_gate"]["issues"] else "`none`")
                + "."
            ),
            "",
            "## Eval Protocol",
            "",
            (
                f"- Publishable views: change_lm=`{report['eval_protocol']['publishable_views'].get('change_lm')}`, "
                f"change_mm=`{report['eval_protocol']['publishable_views'].get('change_mm')}`, "
                f"construction_lm=`{report['eval_protocol']['publishable_views'].get('construction_lm')}`, "
                f"construction_mm=`{report['eval_protocol']['publishable_views'].get('construction_mm')}`."
            ),
            (
                f"- Phase readiness: phase0_change_dual_leaderboard=`{report['eval_protocol']['phase0_ready']}`, "
                f"phase3_four_view_leaderboard=`{report['eval_protocol']['phase3_ready']}`."
            ),
            (
                f"- 0618 readiness: passed=`{report['readiness_0618']['passed']}`, "
                f"invariants=`{report['readiness_0618']['passed_invariants']}/"
                f"{report['readiness_0618']['total_invariants']}`, "
                f"phases=`{report['readiness_0618']['passed_phases']}/"
                f"{report['readiness_0618']['total_phases']}`, "
                f"issues=`{report['readiness_0618']['issue_count']}`."
            ),
            (
                "- 0618 invariant gates: "
                + "; ".join(
                    f"`{key}`={value.get('passed')}"
                    for key, value in report["readiness_0618"].get("invariants", {}).items()
                )
                + "."
            ),
            (
                "- 0618 phase gates: "
                + "; ".join(
                    f"`{key}`={value.get('passed')}"
                    for key, value in report["readiness_0618"].get("phases", {}).items()
                )
                + "."
            ),
        (
            f"- Eval protocol issue count: `{report['eval_protocol']['issue_count']}`."
        ),
        (
            f"- 0618 repair plan: available=`{report['readiness_repair_plan']['available']}`, "
            f"current=`{report['readiness_repair_plan']['current']}`, "
            f"items=`{report['readiness_repair_plan']['repair_item_count']}`, "
            f"failed invariants=`{', '.join(report['readiness_repair_plan'].get('failed_invariants', [])) or 'none'}`, "
            f"failed phases=`{', '.join(report['readiness_repair_plan'].get('failed_phases', [])) or 'none'}`."
        ),
        (
            "- 0618 repair keys: "
            + format_inline_items(report["readiness_repair_plan"].get("repair_keys", []), limit=8)
            + "."
        ),
            (
                "- Eval protocol blocker summary: "
                + (
                    "; ".join(
                        f"`{item['category']}={item['count']}`"
                        for item in report["eval_protocol"].get("blocker_summary", {}).get("top_categories", [])[:8]
                    )
                    if report["eval_protocol"].get("blocker_summary", {}).get("top_categories")
                    else "`none`"
                )
                + "."
            ),
            (
                f"- Denominator gate: passed=`{report['eval_protocol']['gates']['denominator_consistency']['passed']}`, "
                f"issues=`{report['eval_protocol']['gates']['denominator_consistency']['issue_count']}`."
            ),
            (
                "- Eval protocol issues: "
                + format_issue_list(report["eval_protocol"].get("issues", []), limit=8)
                + "."
            ),
            "",
            "## Capability Split",
            "",
            (
                f"- Change LM/MM package export: available=`{report['capability_split']['available']}`, "
                f"passed=`{report['capability_split']['passed']}/"
                f"{report['capability_split']['total']}`, failed=`{report['capability_split']['failed']}`, "
                f"schema all-pass=`{report['capability_split']['passed_all']}`."
            ),
            (
                f"- Change MM checkpoints exported: `{report['capability_split']['mm_checkpoint_total']}`."
            ),
            (
                f"- Construction LM/MM package export: available=`{report['construction_capability_split']['available']}`, "
                f"passed=`{report['construction_capability_split']['passed']}/"
                f"{report['construction_capability_split']['total']}`, failed=`{report['construction_capability_split']['failed']}`, "
                f"schema all-pass=`{report['construction_capability_split']['passed_all']}`."
            ),
            (
                f"- Construction MM checkpoints exported: `{report['construction_capability_split']['mm_checkpoint_total']}`."
            ),
            (
                f"- Signal-layer audit: passed=`{report['signal_layers']['passed']}/"
                f"{report['signal_layers']['total']}`, failed=`{report['signal_layers']['failed']}`, "
                f"issues=`{report['signal_layers']['issue_count']}`, "
                f"saved report current=`{report['signal_layers']['saved_matches_live_counts']}`."
            ),
            (
                "- Signal-layer issues: "
                + format_issue_list(report["signal_layers"].get("issues", []))
                + "."
            ),
            (
                f"- MM judge spec audit: passed=`{report['mm_judge_spec']['passed']}`, "
                f"checkpoints=`{report['mm_judge_spec']['checkpoint_count']}`, "
                f"invalid=`{report['mm_judge_spec']['invalid_checkpoint_count']}`, "
                f"warnings=`{report['mm_judge_spec']['warning_count']}`, "
                f"saved report current=`{report['mm_judge_spec']['saved_matches_live_counts']}`."
            ),
            (
                "- MM judge spec issues: "
                + format_issue_list(report["mm_judge_spec"].get("issues", []))
                + "."
            ),
            (
                f"- Model isolation audit: available=`{report['model_isolation']['available']}`, "
                f"current=`{report['model_isolation'].get('current')}`, "
                f"passed=`{report['model_isolation']['passed']}`, roles=`{report['model_isolation']['role_count']}`, "
                f"issues=`{report['model_isolation']['issue_count']}`, "
                f"stale_issues=`{report['model_isolation'].get('stale_issue_count', 0)}`."
            ),
            (
                f"- Model role queue: available=`{report['model_isolation'].get('queue_available')}`, "
                f"current=`{report['model_isolation'].get('queue_current')}`, "
                f"passed=`{report['model_isolation'].get('queue_passed')}`, "
                f"items=`{report['model_isolation'].get('queue_item_count')}`, "
                f"missing_roles=`{report['model_isolation'].get('queue_missing_role_count')}`, "
                f"issues=`{report['model_isolation'].get('queue_issue_count')}`, "
                f"stale_issues=`{report['model_isolation'].get('queue_stale_issue_count')}`."
            ),
            (
                "- Model role queue issues: "
                + format_issue_list(report["model_isolation"].get("queue_issues", []))
                + "."
            ),
            (
                "- Model isolation issues: "
                + format_issue_list(report["model_isolation"].get("issues", []))
                + "."
            ),
            (
                f"- de-AI contrast audit: available=`{report['de_ai_contrast']['available']}`, "
                f"current=`{report['de_ai_contrast'].get('current')}`, "
                f"passed=`{report['de_ai_contrast']['passed']}`, anchors=`{report['de_ai_contrast']['anchor_count']}`, "
                f"families=`{report['de_ai_contrast']['model_family_count']}`, "
                f"website_types=`{report['de_ai_contrast']['website_type_count']}`, "
                f"fingerprint_records=`{report['de_ai_contrast']['fingerprint_rich_records']}`, "
                f"issues=`{report['de_ai_contrast']['issue_count']}`, "
                f"stale_issues=`{report['de_ai_contrast'].get('stale_issue_count', 0)}`."
            ),
            (
                f"- de-AI contrast queue: available=`{report['de_ai_contrast'].get('queue_available')}`, "
                f"current=`{report['de_ai_contrast'].get('queue_current')}`, "
                f"passed=`{report['de_ai_contrast'].get('queue_passed')}`, "
                f"items=`{report['de_ai_contrast'].get('queue_item_count')}`, "
                f"missing_anchors=`{report['de_ai_contrast'].get('queue_missing_anchor_count')}`, "
                f"missing_fingerprint_records=`{report['de_ai_contrast'].get('queue_missing_fingerprint_rich_count')}`, "
                f"issues=`{report['de_ai_contrast'].get('queue_issue_count')}`, "
                f"stale_issues=`{report['de_ai_contrast'].get('queue_stale_issue_count')}`."
            ),
            (
                "- de-AI contrast queue issues: "
                + format_issue_list(report["de_ai_contrast"].get("queue_issues", []))
                + "."
            ),
            (
                "- de-AI contrast issues: "
                + format_issue_list(report["de_ai_contrast"].get("issues", []))
                + "."
            ),
            "- Change package gate statuses: "
            + (
                "; ".join(
                    "`"
                    + gate
                    + "="
                    + ", ".join(f"{status}:{count}" for status, count in sorted(status_counts.items()))
                    + "`"
                    for gate, status_counts in sorted(report["capability_split"]["gate_status_counts"].items())
                )
                if report["capability_split"]["gate_status_counts"]
                else "`none`"
            )
            + ".",
            "",
            "## Authoring Gates",
            "",
        (
            f"- Repo manifest: `{report['authoring_data_audit']['manifest_rows']}` rows, "
            f"`{report['authoring_data_audit']['manifest_unique_repo_ids']}` unique repos, "
            f"`{report['authoring_data_audit']['manifest_duplicate_repo_id_count']}` duplicate repo ids."
        ),
        (
            f"- Candidate pool: `"
            f"{report['candidate_pool']['rows']}"
            f"{('/' + str(report['candidate_pool']['expected_candidates'])) if report['candidate_pool']['expected_candidates'] is not None else ''}` "
            f"screening rows, `{report['candidate_pool']['unique_repo_ids']}` unique repos, "
            f"`{report['candidate_pool']['duplicate_repo_id_count']}` duplicate repo ids, "
            f"`{report['candidate_pool'].get('unique_zip_paths', 0)}` unique zip paths, "
            f"`{report['candidate_pool'].get('duplicate_zip_path_count', 0)}` duplicate zip paths, "
            f"`{report['candidate_pool'].get('used_task_repo_overlap_count', 0)}` used-repo overlaps "
            f"(not formal task data)."
        ),
        (
            f"- Formal task audit: `{report['authoring_data_audit']['task_count']}` tasks from "
            f"`{report['authoring_data_audit']['task_files']}` `{report['authoring_data_audit']['task_pattern']}` files, "
            f"`{report['authoring_data_audit']['task_file_row_count_issue_count']}` one-row slot violations, "
            f"`{report['authoring_data_audit']['duplicate_task_id_count']}` duplicate task ids, "
            f"`{report['authoring_data_audit']['duplicate_repo_id_count']}` duplicate repo ids, "
            f"`{report['authoring_data_audit']['duplicate_field_issue_count']}` duplicate per-task file/state fields, "
            f"`{report['authoring_data_audit']['validation_issue_count']}` validation failures, "
            f"`{report['authoring_data_audit']['review_artifact_issue_count']}` review-artifact failures, "
            f"`{report['authoring_data_audit']['no_bulk_declaration_issue_count']}` no-bulk declaration failures "
            f"(review artifacts required=`{report['authoring_data_audit']['review_artifacts_required']}`, "
            f"no-bulk declarations required=`{report['authoring_data_audit']['no_bulk_declarations_required']}`, "
            f"unique task repos required=`{report['authoring_data_audit']['unique_repo_ids_required']}`)."
        ),
        (
            f"- Formal root scaffold audit: passed=`{report['authoring_data_audit']['formal_root_scaffold_passed']}`, "
            f"scanned files=`{report['authoring_data_audit']['formal_root_scanned_file_count']}`, "
            f"forbidden scaffold/protocol artifacts=`{report['authoring_data_audit']['formal_root_forbidden_scaffold_count']}`."
        ),
        (
            f"- One-by-one formal authoring policy: passed=`{report['authoring_data_audit']['formal_authoring_policy_passed']}`, "
            f"strict evidence enabled=`{report['authoring_data_audit']['formal_strict_evidence_requirements_enabled']}`, "
            f"automated/template marker failures=`{report['authoring_data_audit']['formal_automated_marker_issue_count']}`."
        ),
        (
            "- One-by-one formal authoring policy issues: "
            + format_issue_list(report["authoring_data_audit"].get("formal_authoring_policy_issues", []))
            + "."
        ),
        (
            "- Formal root scaffold issues: "
            + (
                "; ".join(
                    f"`{item.get('path')}` ({', '.join(item.get('reasons', []))})"
                    for item in report["authoring_data_audit"].get("formal_root_forbidden_scaffolds", [])[:8]
                )
                if report["authoring_data_audit"].get("formal_root_forbidden_scaffolds")
                else "`none`"
            )
            + "."
        ),
            (
                f"- Change ledger/formal audit: ledger accepted=`{report['change_ledger_alignment']['ledger_accepted']}`, "
                f"formal rows=`{report['change_ledger_alignment']['formal_task_rows']}`, "
                f"accepted without formal task=`{report['change_ledger_alignment']['accepted_without_formal_task_count']}`, "
                f"formal without accepted ledger=`{report['change_ledger_alignment']['formal_without_accepted_ledger_count']}`."
            ),
            (
                f"- Change non-evaluable planning rows: "
                f"`{report['change_ledger_alignment'].get('non_evaluable_planning_row_count', 0)}`, "
                f"formal_acceptance=`{report['change_ledger_alignment'].get('non_evaluable_planning_rows_are_formal_acceptance')}`, "
                f"block_publish=`{report['change_ledger_alignment'].get('non_evaluable_planning_rows_block_publish')}`."
            ),
            (
                "- Change unmaterialized accepted sources: "
                + format_count_map(report["change_ledger_alignment"].get("unmaterialized_accepted_source_counts", {}))
                + "."
            ),
        (
            "- Change unmaterialized accepted decisions: "
            + format_count_map(report["change_ledger_alignment"].get("unmaterialized_accepted_decision_counts", {}))
            + "."
        ),
        (
            "- Denominator consistency issues: "
            + format_issue_list(report["denominator_consistency"].get("issues", []))
            + "."
        ),
        (
            "- Change ledger/formal issues: "
            + format_issue_list(report["change_ledger_alignment"].get("issues", []))
            + "."
        ),
        f"- Authoring audit passed: `{report['authoring_data_audit']['passed']}`.",
        (
            f"- Strict review-artifact audit: passed=`{report['strict_authoring_data_audit']['passed']}`, "
            f"tasks=`{report['strict_authoring_data_audit']['task_count']}`, "
            f"unique repos required=`{report['strict_authoring_data_audit']['unique_repo_ids_required']}`, "
            f"duplicate repo ids=`{report['strict_authoring_data_audit']['duplicate_repo_id_count']}`, "
            f"global policy issues=`{report['strict_authoring_data_audit']['global_policy_issue_count']}`, "
            f"missing-artifact tasks=`{report['strict_authoring_data_audit']['review_artifact_issue_count']}`, "
            f"no-bulk declaration failures=`{report['strict_authoring_data_audit']['no_bulk_declaration_issue_count']}`."
        ),
        (
            "- Strict missing-artifact slots: "
            + (
                "; ".join(
                    f"`{Path(item.get('path', '')).parent.name}` missing `{', '.join(item.get('missing', []))}`"
                    for item in report["strict_authoring_data_audit"].get("review_artifact_issues", [])[:12]
                )
                if report["strict_authoring_data_audit"].get("review_artifact_issues")
                else "`none`"
            )
            + "."
        ),
        (
            f"- Strict freeze audit: available=`{report['strict_freeze_authoring_audit']['available']}`, "
            f"passed=`{report['strict_freeze_authoring_audit']['passed']}`, "
            f"tasks=`{report['strict_freeze_authoring_audit']['task_count']}`, "
            f"strict-clean tasks=`{report['strict_freeze_authoring_audit']['strict_clean_task_count']}`, "
            f"one-by-one policy=`{report['strict_freeze_authoring_audit']['formal_authoring_policy_passed']}`, "
            f"automated/template markers=`{report['strict_freeze_authoring_audit']['formal_automated_marker_issue_count']}`, "
            f"unique repos required=`{report['strict_freeze_authoring_audit']['unique_repo_ids_required']}`, "
            f"duplicate repo ids=`{report['strict_freeze_authoring_audit']['duplicate_repo_id_count']}`, "
            f"global policy issues=`{report['strict_freeze_authoring_audit']['global_policy_issue_count']}`, "
            f"freeze-audit failures=`{report['strict_freeze_authoring_audit']['freeze_audit_issue_count']}`, "
            f"no-bulk declaration failures=`{report['strict_freeze_authoring_audit']['no_bulk_declaration_issue_count']}`."
        ),
        (
            "- Strict authoring policy issues: "
            + (
                "; ".join(f"`{issue}`" for issue in report["strict_freeze_authoring_audit"].get("global_policy_issues", [])[:12])
                if report["strict_freeze_authoring_audit"].get("global_policy_issues")
                else "`none`"
            )
            + "."
        ),
        (
            "- Strict duplicate repo groups: "
            + (
                "; ".join(
                    "`{repo}` -> {slots}".format(
                        repo=item.get("repo_id"),
                        slots=", ".join(
                            str(ref.get("slot_id") or Path(str(ref.get("path", ""))).parent.name)
                            for ref in item.get("refs", [])[:6]
                        ),
                    )
                    for item in report["strict_freeze_authoring_audit"].get("duplicate_repo_refs", [])[:12]
                )
                if report["strict_freeze_authoring_audit"].get("duplicate_repo_refs")
                else "`none`"
            )
            + "."
        ),
        (
            f"- Duplicate repo repair plan: available=`{report['duplicate_repo_repair_plan']['available']}`, "
            f"current=`{report['duplicate_repo_repair_plan']['current']}`, "
            f"groups=`{report['duplicate_repo_repair_plan']['duplicate_group_count']}`, "
            f"included=`{report['duplicate_repo_repair_plan']['included_group_count']}`, "
            f"issues=`{report['duplicate_repo_repair_plan']['issue_count']}`."
        ),
        (
            "- Duplicate repo repair plan issues: "
            + format_issue_list(report["duplicate_repo_repair_plan"].get("issues", []))
            + "."
        ),
        (
            f"- Strict authoring repair plan: available=`{report['strict_authoring_repair_plan']['available']}`, "
            f"current=`{report['strict_authoring_repair_plan']['current']}`, "
            f"blocked slots=`{report['strict_authoring_repair_plan']['blocked_slot_count']}`, "
            f"included=`{report['strict_authoring_repair_plan']['included_slot_count']}`, "
            f"issues=`{report['strict_authoring_repair_plan']['issue_count']}`."
        ),
        (
            "- Strict authoring repair blocker counts: "
            + format_count_map(report["strict_authoring_repair_plan"].get("blocker_counts", {}))
            + "."
        ),
        (
            "- Strict authoring repair plan issues: "
            + format_issue_list(report["strict_authoring_repair_plan"].get("issues", []))
            + "."
        ),
        (
            f"- Strict freeze worklist: available=`{report['strict_freeze_worklist']['available']}`, "
            f"current=`{report['strict_freeze_worklist']['current']}`, "
            f"passed=`{report['strict_freeze_worklist']['passed']}`, "
            f"items=`{report['strict_freeze_worklist']['work_item_count']}`, "
            f"issues=`{report['strict_freeze_worklist']['issue_count']}`, "
            f"stale_issues=`{report['strict_freeze_worklist'].get('stale_issue_count', 0)}`."
        ),
        (
            "- Strict freeze worklist blocker counts: "
            + format_count_map(report["strict_freeze_worklist"].get("blocker_counts", {}))
            + "."
        ),
        (
            "- Strict freeze worklist issues: "
            + format_issue_list(report["strict_freeze_worklist"].get("issues", []))
            + "."
        ),
        (
            "- Strict freeze-audit issue slots: "
            + (
                "; ".join(
                    f"`{Path(item.get('path', '')).parent.name}` `{'; '.join(item.get('errors', []))}`"
                    for item in report["strict_freeze_authoring_audit"].get("freeze_audit_issues", [])[:12]
                )
                if report["strict_freeze_authoring_audit"].get("freeze_audit_issues")
                else "`none`"
            )
            + "."
        ),
        (
            "- Strict no-bulk declaration issue slots: "
            + (
                "; ".join(
                    f"`{Path(item.get('path', '')).parent.name}` `{'; '.join(item.get('errors', []))}`"
                    for item in report["strict_freeze_authoring_audit"].get("no_bulk_declaration_issues", [])[:12]
                )
                if report["strict_freeze_authoring_audit"].get("no_bulk_declaration_issues")
                else "`none`"
            )
            + "."
        ),
        (
            f"- Active authoring lock: passed=`{report['authoring_lock']['passed']}`, "
            f"active=`{report['authoring_lock']['active_progress_count']}`, "
            f"progress_files=`{report['authoring_lock']['progress_file_count']}`."
        ),
        (
            "- Current authoring task: "
            + (
                f"`{report['authoring_lock']['active_slot_id']}` "
                f"regime=`{report['authoring_lock']['active_regime']}` "
                f"stage=`{report['authoring_lock']['active_current_stage']}` "
                f"repo=`{report['authoring_lock']['active_repo_id']}` "
                f"task=`{report['authoring_lock']['active_task_id']}`."
                if report["authoring_lock"].get("active_progress_count")
                else "`none`."
            )
        ),
        (
            "- Authoring lock issues: "
            + format_issue_list(report["authoring_lock"].get("issues", []))
            + "."
        ),
        "",
        "## Change Formal Coverage Gaps",
        "",
        f"- Coverage denominator: `{change.get('coverage_denominator', 'formal_materialized_task_rows')}`.",
        f"- Formal materialized tasks: `{change.get('formal_materialized_task_count', change.get('materialized_formal_task_count'))}/{change['target_total']}`.",
        f"- Ledger accepted planning rows: `{change['accepted']}/{change['target_total']}`.",
        "",
    ]
    for group_name, group in change.get("coverage", {}).items():
        remaining = {key: value for key, value in group.items() if value["gap"] > 0}
        lines.append(f"### {group_name}")
        if not remaining:
            lines.append("- no remaining gap")
        else:
            for key, value in sorted(remaining.items(), key=lambda pair: (-pair[1]["gap"], pair[0]))[:12]:
                lines.append(f"- `{key}`: {value['accepted']}/{value['target']} materialized, gap {value['gap']}")
        lines.append("")
    lines.extend(
        [
            "## MM Calibration",
            "",
            f"- Required human labels per regime: `{report['mm_block']['required_human_calibration_per_regime']}`.",
            f"- Change calibration items: `{report['mm_block']['change_calibration_items']}`.",
            f"- Change calibration unique tasks: `{report['mm_block']['change_unique_tasks']}`.",
            f"- Change available MM checkpoints: `{report['mm_block']['change_available_checkpoints']}`.",
            f"- Change sample gate passed: `{report['mm_block']['change_sample_gate_passed']}`.",
            (
                f"- MM protocol repair plan: available=`{report['mm_protocol_repair_plan']['available']}`, "
                f"current=`{report['mm_protocol_repair_plan']['current']}`, "
                f"blocking sections=`{report['mm_protocol_repair_plan']['blocking_section_count']}`, "
                f"sections=`{report['mm_protocol_repair_plan']['section_count']}`, "
                f"issues=`{report['mm_protocol_repair_plan']['issue_count']}`."
            ),
            (
                "- MM protocol repair blockers: "
                + format_inline_items(report["mm_protocol_repair_plan"].get("blocking_sections", []), limit=8)
                + "."
            ),
            (
                "- MM protocol repair plan issues: "
                + format_issue_list(report["mm_protocol_repair_plan"].get("issues", []))
                + "."
            ),
            (
                f"- Change annotation pack: available=`{report['mm_block']['change_annotation_pack_available']}`, "
                f"current=`{report['mm_block'].get('change_annotation_pack_current')}`, "
                f"passed=`{report['mm_block']['change_annotation_pack_passed']}`, "
                f"items=`{report['mm_block']['change_annotation_pack_items']}`, "
                f"issues=`{report['mm_block']['change_annotation_pack_issue_count']}`, "
                f"stale_issues=`{report['mm_block'].get('change_annotation_pack_stale_issue_count', 0)}`."
            ),
            (
                "- Change annotation pack issues: "
                + format_issue_list(report["mm_block"].get("change_annotation_pack_issues", []))
                + "."
            ),
            (
                f"- Change human annotation queue: available=`{report['mm_block'].get('change_human_annotation_queue_available')}`, "
                f"current=`{report['mm_block'].get('change_human_annotation_queue_current')}`, "
                f"passed=`{report['mm_block'].get('change_human_annotation_queue_passed')}`, "
                f"items=`{report['mm_block'].get('change_human_annotation_queue_items')}`, "
                f"labeled=`{report['mm_block'].get('change_human_annotation_queue_labeled')}`, "
                f"unlabeled=`{report['mm_block'].get('change_human_annotation_queue_unlabeled')}`, "
                f"issues=`{report['mm_block'].get('change_human_annotation_queue_issue_count')}`, "
                f"stale_issues=`{report['mm_block'].get('change_human_annotation_queue_stale_issue_count')}`."
            ),
            (
                "- Change human annotation queue issues: "
                + format_issue_list(report["mm_block"].get("change_human_annotation_queue_issues", []))
                + "."
            ),
            (
                f"- Change judge prediction queue: available=`{report['mm_block'].get('change_judge_prediction_queue_available')}`, "
                f"current=`{report['mm_block'].get('change_judge_prediction_queue_current')}`, "
                f"passed=`{report['mm_block'].get('change_judge_prediction_queue_passed')}`, "
                f"items=`{report['mm_block'].get('change_judge_prediction_queue_items')}`, "
                f"completed=`{report['mm_block'].get('change_judge_prediction_queue_completed')}`, "
                f"missing=`{report['mm_block'].get('change_judge_prediction_queue_missing')}`, "
                f"issues=`{report['mm_block'].get('change_judge_prediction_queue_issue_count')}`, "
                f"stale_issues=`{report['mm_block'].get('change_judge_prediction_queue_stale_issue_count')}`."
            ),
            (
                "- Change judge prediction queue issues: "
                + format_issue_list(report["mm_block"].get("change_judge_prediction_queue_issues", []))
                + "."
            ),
            (
                f"- Change judge prediction audit: available=`{report['mm_block']['change_judge_prediction_audit_available']}`, "
                f"current=`{report['mm_block'].get('change_judge_prediction_audit_current')}`, "
                f"passed=`{report['mm_block']['change_judge_prediction_audit_passed']}`, "
                f"records=`{report['mm_block']['change_judge_prediction_audit_records']}`, "
                f"usable=`{report['mm_block']['change_judge_prediction_audit_usable_records']}`, "
                f"issues=`{report['mm_block']['change_judge_prediction_audit_issue_count']}`, "
                f"stale_issues=`{report['mm_block'].get('change_judge_prediction_audit_stale_issue_count', 0)}`."
            ),
            (
                "- Change judge prediction audit issues: "
                + format_issue_list(report["mm_block"].get("change_judge_prediction_audit_issues", []))
                + "."
            ),
            (
                f"- Change human label freeze: available=`{report['mm_block']['change_human_label_freeze_available']}`, "
                f"current=`{report['mm_block'].get('change_human_label_freeze_current')}`, "
                f"passed=`{report['mm_block']['change_human_label_freeze_passed']}`, "
                f"publishable_candidate=`{report['mm_block'].get('change_human_label_freeze_publishable_candidate')}`, "
                f"recovery_only=`{report['mm_block'].get('change_human_label_freeze_recovery_only')}`, "
                f"records=`{report['mm_block']['change_human_label_freeze_records']}`, "
                f"issues=`{report['mm_block']['change_human_label_freeze_issue_count']}`, "
                f"stale_issues=`{report['mm_block'].get('change_human_label_freeze_stale_issue_count', 0)}`."
            ),
            (
                "- Change human label freeze issues: "
                + format_issue_list(report["mm_block"].get("change_human_label_freeze_issues", []))
                + "."
            ),
            f"- Change human-labeled items: `{report['mm_block']['change_human_labeled_items']}`.",
            (
                f"- Change MM publish gate: passed=`{report['mm_block']['change_publish_gate_passed']}`, "
                f"publishable=`{report['mm_block']['change_publishable']}`."
            ),
            (
                "- Change MM publish gate issues: "
                + format_issue_list(report["mm_block"].get("change_publish_gate_issues", []))
                + "."
            ),
            (
                f"- Synthetic smoke gate: passed=`{report['mm_block']['change_synthetic_gate_passed']}`, "
                f"agreement=`{report['mm_block']['change_synthetic_agreement']}`, "
                f"kappa=`{report['mm_block']['change_synthetic_kappa']}`, "
                f"publishable=`{report['mm_block']['change_synthetic_publishable']}`."
            ),
            (
                f"- MM perturbation audit: available=`{report['mm_judge_perturbations']['available']}`, "
                f"current=`{report['mm_judge_perturbations'].get('current')}`, "
                f"passed=`{report['mm_judge_perturbations']['passed']}`, "
                f"groups=`{report['mm_judge_perturbations']['group_count']}`, "
                f"records=`{report['mm_judge_perturbations']['record_count']}`, "
                f"label-flip groups=`{report['mm_judge_perturbations']['label_flip_group_count']}`, "
                f"stale_issues=`{report['mm_judge_perturbations'].get('stale_issue_count', 0)}`."
            ),
            (
                f"- MM perturbation queue: available=`{report['mm_judge_perturbations'].get('queue_available')}`, "
                f"current=`{report['mm_judge_perturbations'].get('queue_current')}`, "
                f"passed=`{report['mm_judge_perturbations'].get('queue_passed')}`, "
                f"items=`{report['mm_judge_perturbations'].get('queue_item_count')}`, "
                f"required_groups=`{report['mm_judge_perturbations'].get('queue_required_group_count')}`, "
                f"missing_groups=`{report['mm_judge_perturbations'].get('queue_missing_group_count')}`, "
                f"issues=`{report['mm_judge_perturbations'].get('queue_issue_count')}`, "
                f"stale_issues=`{report['mm_judge_perturbations'].get('queue_stale_issue_count')}`."
            ),
            (
                "- MM perturbation queue issues: "
                + format_issue_list(report["mm_judge_perturbations"].get("queue_issues", []))
                + "."
            ),
            (
                "- MM perturbation issues: "
                + format_issue_list(report["mm_judge_perturbations"].get("issues", []))
                + "."
            ),
            f"- Construction calibration items: `{report['mm_block']['construction_calibration_items']}`.",
            "",
            "## Construction Artifacts",
            "",
            f"- Draft construction tasks: `{report['construction_artifacts']['draft_count']}`.",
            f"- Construction template-only artifacts: `{report['construction_artifacts']['template_artifact_count']}`.",
            f"- Construction planning artifacts: `{report['construction_artifacts'].get('planning_artifact_count', 0)}` "
            "(non-formal repair/worklist protocol files; not drafts or evidence).",
            f"- Construction trajectory score snapshots: `{report['construction_artifacts']['score_count']}` "
            "(non-formal; not reference trajectory artifacts).",
            (
                f"- Valid construction score snapshots: `{report['construction_artifacts']['valid_score_snapshot_count']}/"
                f"{report['construction_artifacts']['score_count']}`, "
                f"issues=`{report['construction_artifacts']['score_snapshot_issue_count']}`."
            ),
            (
                "- Construction score snapshot issues: "
                + format_issue_list(
                    [
                        f"{Path(item.get('path', '')).name} " + "; ".join(item.get("issues", []))
                        for item in report["construction_artifacts"].get("score_snapshot_issues", [])
                    ]
                )
                + "."
            ),
            (
                f"- Saved construction precheck artifacts: "
                f"strict=`{report['construction_artifacts']['saved_strict_precheck_artifact_count']}`, "
                f"structural=`{report['construction_artifacts']['saved_structural_precheck_artifact_count']}`, "
                f"issues=`{report['construction_artifacts']['saved_precheck_issue_count']}`."
            ),
            (
                "- Saved construction precheck issues: "
                + format_issue_list(
                    [
                        f"{Path(item.get('path', '')).name} " + "; ".join(item.get("issues", []))
                        for item in report["construction_artifacts"].get("saved_precheck_issues", [])
                    ]
                )
                + "."
            ),
            (
                f"- Reference actor trace templates: `{report['construction_artifacts'].get('reference_actor_trace_template_count', 0)}`, "
                f"issues=`{report['construction_artifacts'].get('reference_actor_trace_template_issue_count', 0)}`."
            ),
            (
                f"- Construction repair plan: available=`{report['construction_repair_plan']['available']}`, "
                f"current=`{report['construction_repair_plan']['current']}`, "
                f"blockers=`{report['construction_repair_plan']['blocking_requirement_count']}`, "
                f"draft repair items=`{report['construction_repair_plan'].get('draft_repair_item_count', 0)}`, "
                f"issues=`{report['construction_repair_plan']['issue_count']}`."
            ),
            (
                "- Construction draft repair items: "
                + (
                    "; ".join(
                        "`{task}` blockers `{blockers}`".format(
                            task=item.get("task_id") or Path(str(item.get("task_path", ""))).name,
                            blockers=", ".join(str(key) for key in item.get("blocking_requirement_keys", [])[:6]) or "none",
                        )
                        for item in report["construction_repair_plan"].get("draft_repair_items", [])[:5]
                    )
                    if report["construction_repair_plan"].get("draft_repair_items")
                    else "`none`"
                )
                + "."
            ),
            (
                f"- Construction evidence worklist: available=`{report['construction_evidence_worklist']['available']}`, "
                f"current=`{report['construction_evidence_worklist']['current']}`, "
                f"passed=`{report['construction_evidence_worklist']['passed']}`, "
                f"items=`{report['construction_evidence_worklist']['work_item_count']}`, "
                f"issues=`{report['construction_evidence_worklist']['issue_count']}`, "
                f"stale_issues=`{report['construction_evidence_worklist'].get('stale_issue_count', 0)}`."
            ),
            (
                "- Construction evidence worklist blockers: "
                + format_count_map(report["construction_evidence_worklist"].get("blocker_counts", {}))
                + "."
            ),
            (
                f"- Construction evidence scaffold audits: scaffold_roots=`{report['construction_evidence_worklist'].get('scaffold_exists_count', 0)}/"
                f"{report['construction_evidence_worklist']['work_item_count']}`, "
                f"scaffold_passed=`{report['construction_evidence_worklist'].get('scaffold_audit_passed_count', 0)}/"
                f"{report['construction_evidence_worklist']['work_item_count']}`, "
                f"complete_passed=`{report['construction_evidence_worklist'].get('complete_audit_passed_count', 0)}/"
                f"{report['construction_evidence_worklist']['work_item_count']}`, "
                f"complete_issues=`{report['construction_evidence_worklist'].get('complete_audit_issue_count', 0)}`."
            ),
            (
                "- Construction evidence missing outputs: "
                + format_count_map(report["construction_evidence_worklist"].get("missing_expected_output_counts", {}))
                + "."
            ),
            (
                "- Construction evidence worklist issues: "
                + format_issue_list(report["construction_evidence_worklist"].get("issues", []))
                + "."
            ),
            (
                "- Construction repair keys: "
                + format_inline_items(report["construction_repair_plan"].get("repair_keys", []), limit=8)
                + "."
            ),
            (
                "- Construction repair plan issues: "
                + format_issue_list(report["construction_repair_plan"].get("issues", []))
                + "."
            ),
            (
                "- Reference actor trace template issues: "
                + format_issue_list(
                    [
                        f"{Path(item.get('path', '')).name} " + "; ".join(item.get("issues", []))
                        for item in report["construction_artifacts"].get("reference_actor_trace_template_issues", [])
                    ]
                )
                + "."
            ),
            (
                f"- Strict construction prechecks: `{report['construction_artifacts']['strict_precheck_passed']}/"
                f"{report['construction_artifacts']['strict_precheck_count']}` passed."
            ),
            (
                f"- Structural construction prechecks: `{report['construction_artifacts']['structural_precheck_passed']}/"
                f"{report['construction_artifacts']['structural_precheck_count']}` passed."
            ),
            (
                f"- Construction task-package schema prechecks: "
                f"`{report['construction_artifacts']['task_package_schema_precheck_passed']}/"
                f"{report['construction_artifacts']['task_package_schema_precheck_total']}` passed."
            ),
            (
                "- Construction strict issues: "
                + format_issue_list(report["construction_artifacts"]["strict_issues"])
                + "."
            ),
            (
                "- Construction strict blockers: "
                + (
                    "; ".join(
                        "`"
                        + str(item.get("key"))
                        + "`"
                        + f" ({item.get('count')})"
                        for item in report["construction_artifacts"].get("strict_blocking_requirements", [])
                    )
                    if report["construction_artifacts"].get("strict_blocking_requirements")
                    else "`none`"
                )
                + "."
            ),
            (
                "- Construction blocker evidence requirements: "
                + (
                    "; ".join(
                        "`"
                        + str(item.get("key"))
                        + "` requires "
                        + format_inline_items(item.get("required_evidence", []), limit=5)
                        for item in report["construction_artifacts"].get("strict_blocking_requirements", [])
                    )
                    if report["construction_artifacts"].get("strict_blocking_requirements")
                    else "`none`"
                )
                + "."
            ),
            "",
            "## Meta-Evaluation",
            "",
            (
                f"- Meta-eval reports: `{report['metareval']['passed_count']}/"
                f"{report['metareval']['report_count']}` passed."
            ),
            (
                f"- Evidence gates: reference=`{report['metareval']['reference_passed']}`, "
                f"original_failed=`{report['metareval']['original_failed']}`, "
                f"bad_solution_failed=`{report['metareval']['bad_solutions_failed']}`, "
                f"reproducible=`{report['metareval']['reproducible']}`."
            ),
            (
                f"- Missing evidence: reference=`{report['metareval']['missing_reference']}`, "
                f"original=`{report['metareval']['missing_original']}`, "
                f"bad_solution=`{report['metareval']['missing_bad_solution']}`, "
                f"repeat=`{report['metareval']['missing_repeat']}`."
            ),
            (
                "- Open meta-eval issues: "
                + ("; ".join(f"`{issue}`" for issue in report["metareval"]["open_issues"]) if report["metareval"]["open_issues"] else "`none`")
                + "."
            ),
            "",
            "## Negative Controls",
            "",
            (
                f"- Bad-solution evidence covered: `{report['negative_controls']['covered_tasks']}/"
                f"{report['negative_controls']['total_tasks']}` tasks."
            ),
            (
                f"- Valid bad-solution reports: `{report['negative_controls']['valid_bad_reports']}/"
                f"{report['negative_controls']['total_tasks']}`; invalid=`{report['negative_controls']['invalid_bad_reports']}`, "
                f"missing=`{report['negative_controls']['missing_bad_reports']}`, "
                f"expected-check covered=`{report['negative_controls']['expected_check_covered']}`."
            ),
            (
                f"- Tasks needing bad-solution execution: `{report['negative_controls']['needs_execution_tasks']}`; "
                f"recommended control instances: `{report['negative_controls']['total_recommended_controls']}`."
            ),
            (
                f"- Execution readiness: ready=`{report['negative_controls']['execution_ready_tasks']}`, "
                f"not_ready=`{report['negative_controls']['execution_not_ready_tasks']}`."
            ),
            (
                "- State-plan sources: "
                + (
                    ", ".join(
                        f"`{name}`={count}"
                        for name, count in sorted(report["negative_controls"]["state_plan_source_counts"].items())
                    )
                    if report["negative_controls"]["state_plan_source_counts"]
                    else "`none`"
                )
                + "."
            ),
            (
                "- Source workspace sources: "
                + (
                    ", ".join(
                        f"`{name}`={count}"
                        for name, count in sorted(report["negative_controls"]["source_kind_counts"].items())
                    )
                    if report["negative_controls"]["source_kind_counts"]
                    else "`none`"
                )
                + "."
            ),
            (
                "- Control mix: "
                + (
                    ", ".join(
                        f"`{name}`={count}"
                        for name, count in sorted(report["negative_controls"]["control_counts"].items())
                    )
                    if report["negative_controls"]["control_counts"]
                    else "`none`"
                )
                + "."
            ),
            "",
            "## Notes",
            "",
            "- Counts are generated from ledger files; do not hand-edit this status page.",
            "- Formal benchmark tasks are only counted from `slot_*/task.jsonl`; draft/audit JSON files under `data/productwebbench/tasks` are intermediate artifacts.",
            "- Candidate ranking, capture, verifier runs, negative controls, and audits are evidence automation only; they must not mass-generate final task specs.",
            "- Primary metrics remain L-hard/L-metric only; L-soft/Judge Agreement is reported separately.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_status(report: dict[str, Any], status_path: Path) -> None:
    ensure_dir(status_path.parent)
    status_path.write_text(render_status(report), encoding="utf-8")


def export_schema(schema_root: Path) -> dict[str, Path]:
    ensure_dir(schema_root)
    metric_path = schema_root / "metrics.schema.json"
    package_path = schema_root / "task_package.schema.json"
    write_json(metric_path, metric_schema())
    write_json(package_path, task_package_json_schema())
    return {"metrics": metric_path, "task_package": package_path}


def add_status_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ledger-root", type=Path, default=DEFAULT_LEDGER_ROOT)
    parser.add_argument("--task-root", type=Path, default=DEFAULT_TASK_ROOT)
    parser.add_argument(
        "--coverage-output",
        type=Path,
        default=DEFAULT_LEDGER_ROOT / "coverage_gaps.json",
        help="Machine-readable coverage/status JSON output.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Deprecated alias for --coverage-output. If a .md path is supplied, "
            "it is treated as --status for compatibility."
        ),
    )
    parser.add_argument("--status", type=Path, default=DEFAULT_DOCS_STATUS)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATE_PATH)
    parser.add_argument("--candidate-summary", type=Path, default=DEFAULT_CANDIDATE_SUMMARY_PATH)
    parser.add_argument("--schema-root", type=Path, default=DEFAULT_SCHEMA_ROOT)
    parser.add_argument("--authoring-audit", type=Path, default=DEFAULT_AUTHORING_AUDIT_PATH)
    parser.add_argument("--strict-authoring-audit", type=Path, default=DEFAULT_STRICT_AUTHORING_AUDIT_PATH)
    parser.add_argument("--strict-freeze-audit", type=Path, default=DEFAULT_STRICT_FREEZE_AUDIT_PATH)
    parser.add_argument("--duplicate-repo-repair-plan", type=Path, default=DEFAULT_DUPLICATE_REPO_REPAIR_PLAN)
    parser.add_argument("--strict-authoring-repair-plan", type=Path, default=DEFAULT_STRICT_AUTHORING_REPAIR_PLAN)
    parser.add_argument("--strict-freeze-worklist", type=Path, default=DEFAULT_STRICT_FREEZE_WORKLIST)
    parser.add_argument("--capability-split", type=Path, default=DEFAULT_CAPABILITY_SPLIT_PATH)
    parser.add_argument("--capability-split-audit", type=Path, default=DEFAULT_CAPABILITY_SPLIT_AUDIT)
    parser.add_argument("--construction-capability-split", type=Path, default=DEFAULT_CONSTRUCTION_CAPABILITY_SPLIT_PATH)
    parser.add_argument("--construction-capability-split-audit", type=Path, default=DEFAULT_CONSTRUCTION_CAPABILITY_SPLIT_AUDIT)
    parser.add_argument("--signal-layer-audit", type=Path, default=DEFAULT_SIGNAL_LAYER_AUDIT)
    parser.add_argument("--mm-judge-spec-audit", type=Path, default=DEFAULT_MM_JUDGE_SPEC_AUDIT)
    parser.add_argument("--construction-mm-judge-spec-audit", type=Path, default=DEFAULT_CONSTRUCTION_MM_JUDGE_SPEC_AUDIT)
    parser.add_argument("--mm-judge-perturbation-audit", type=Path, default=DEFAULT_MM_JUDGE_PERTURBATION_AUDIT)
    parser.add_argument("--model-isolation-audit", type=Path, default=DEFAULT_MODEL_ISOLATION_AUDIT)
    parser.add_argument("--deai-contrast-audit", type=Path, default=DEFAULT_DEAI_CONTRAST_AUDIT)
    parser.add_argument("--mm-protocol-repair-plan", type=Path, default=DEFAULT_MM_PROTOCOL_REPAIR_PLAN)
    parser.add_argument("--readiness-0618-audit", type=Path, default=DEFAULT_0618_READINESS_AUDIT)
    parser.add_argument("--mm-root", type=Path, default=DEFAULT_MM_CALIBRATION_ROOT)
    parser.add_argument("--construction-draft-root", type=Path, default=DEFAULT_CONSTRUCTION_DRAFT_ROOT)
    parser.add_argument("--construction-repair-plan", type=Path, default=DEFAULT_CONSTRUCTION_REPAIR_PLAN)
    parser.add_argument("--construction-evidence-worklist", type=Path, default=DEFAULT_CONSTRUCTION_EVIDENCE_WORKLIST)
    parser.add_argument("--readiness-repair-plan", type=Path, default=DEFAULT_0618_READINESS_REPAIR_PLAN)
    parser.add_argument("--metareval-root", type=Path, default=DEFAULT_METAREVAL_ROOT)
    parser.add_argument("--negative-control-plan", type=Path, default=DEFAULT_NEGATIVE_CONTROL_PLAN)
    parser.add_argument("--negative-control-audit", type=Path, default=DEFAULT_NEGATIVE_CONTROL_AUDIT)


def run_status_from_args(args: argparse.Namespace) -> None:
    coverage_output = args.coverage_output
    status_path = args.status
    if args.output is not None:
        if args.output.suffix.lower() in {".md", ".markdown"}:
            if args.status != DEFAULT_DOCS_STATUS and args.status != args.output:
                raise SystemExit("--output points to Markdown but --status was also provided; use --status for Markdown and --coverage-output for JSON")
            status_path = args.output
        else:
            if args.coverage_output != DEFAULT_LEDGER_ROOT / "coverage_gaps.json":
                raise SystemExit("--output and --coverage-output both set coverage JSON paths; use only --coverage-output")
            coverage_output = args.output
    report = build_coverage_gaps(
        args.ledger_root,
        coverage_output,
        task_root=args.task_root,
        candidate_path=args.candidates,
        candidate_summary_path=args.candidate_summary,
        schema_root=args.schema_root,
        authoring_audit_path=args.authoring_audit,
        strict_authoring_audit_path=args.strict_authoring_audit,
        strict_freeze_audit_path=args.strict_freeze_audit,
        duplicate_repo_repair_plan_path=args.duplicate_repo_repair_plan,
        strict_authoring_repair_plan_path=args.strict_authoring_repair_plan,
        strict_freeze_worklist_path=args.strict_freeze_worklist,
        capability_split_audit=args.capability_split_audit,
        capability_split_path=args.capability_split,
        construction_capability_split_audit=args.construction_capability_split_audit,
        construction_capability_split_path=args.construction_capability_split,
        signal_layer_audit=args.signal_layer_audit,
        mm_judge_spec_audit=args.mm_judge_spec_audit,
        construction_mm_judge_spec_audit=args.construction_mm_judge_spec_audit,
        mm_judge_perturbation_audit=args.mm_judge_perturbation_audit,
        model_isolation_audit=args.model_isolation_audit,
        deai_contrast_audit=args.deai_contrast_audit,
        mm_protocol_repair_plan_path=args.mm_protocol_repair_plan,
        readiness_audit_path=args.readiness_0618_audit,
        mm_root=args.mm_root,
        construction_draft_root=args.construction_draft_root,
        construction_repair_plan_path=args.construction_repair_plan,
        construction_evidence_worklist_path=args.construction_evidence_worklist,
        readiness_repair_plan_path=args.readiness_repair_plan,
        metareval_root=args.metareval_root,
        negative_control_plan=args.negative_control_plan,
        negative_control_audit=args.negative_control_audit,
    )
    write_status(report, status_path)
    print(
        "built Website Continuity status: "
        f"B_formal={report['regimes'][CHANGE_REGIME].get('materialized_formal_task_count')}/400 "
        f"B_ledger={report['regimes'][CHANGE_REGIME]['accepted']}/400 "
        f"A={report['regimes'][CONSTRUCTION_REGIME]['accepted']}/400 "
        f"coverage={coverage_output} status={status_path}"
    )


def add_schema_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output-root", type=Path, default=DEFAULT_SCHEMA_ROOT)


def run_schema_from_args(args: argparse.Namespace) -> None:
    paths = export_schema(args.output_root)
    print(f"wrote metric schema to {paths['metrics']}")
    print(f"wrote task package schema to {paths['task_package']}")
