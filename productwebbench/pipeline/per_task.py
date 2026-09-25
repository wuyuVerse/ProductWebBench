from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.config import DEFAULT_OUTPUT_ROOT
from ..core.io_utils import read_jsonl, write_json
from ..taxonomy.capability import CHANGE_REGIME, CONSTRUCTION_REGIME


SCHEMA_VERSION = "2026-06-19"
ARTIFACT_TYPE = "sitecontinuum_per_task_progress"
DEFAULT_LEDGER_ROOT = DEFAULT_OUTPUT_ROOT / "authoring_ledger"
DEFAULT_PROGRESS_ROOT = DEFAULT_LEDGER_ROOT / "per_task_progress"
DEFAULT_ACTIVE_LOCK = DEFAULT_LEDGER_ROOT / "active_authoring_task.json"
DEFAULT_TASK_ROOT = DEFAULT_OUTPUT_ROOT / "tasks"
DEFAULT_TASK_PATTERN = "slot_*/task.jsonl"

VALID_REGIMES = {CHANGE_REGIME, CONSTRUCTION_REGIME}
VALID_STAGE_STATUSES = {"not_started", "in_progress", "passed", "failed", "rejected"}
FINAL_DECISIONS = {"accepted", "rejected"}
STRICT_REPAIR_PLAN_ARTIFACT_TYPE = "strict_authoring_repair_plan"

STAGES = [
    {
        "id": "P0_slot",
        "short": "P0",
        "label": "Pick coverage slot",
        "required_artifacts": [],
    },
    {
        "id": "P1_repo",
        "short": "P1",
        "label": "Select one repo",
        "required_artifacts": ["candidate_review_or_repo_reason"],
    },
    {
        "id": "P2_triage",
        "short": "P2",
        "label": "Extract and runability triage",
        "required_artifacts": ["runability_report"],
    },
    {
        "id": "P3_capture",
        "short": "P3",
        "label": "Capture browser states",
        "required_artifacts": ["state_capture"],
    },
    {
        "id": "P4_design",
        "short": "P4",
        "label": "Author task design",
        "required_artifacts": [
            "design_brief_or_rationale",
            "repo_specific_inspection_log",
            "no_bulk_generation_declaration",
        ],
    },
    {
        "id": "P5_verifier",
        "short": "P5",
        "label": "Write verifier specs",
        "required_artifacts": ["reference_verifier", "submission_verifier"],
    },
    {
        "id": "P6_sanity",
        "short": "P6",
        "label": "Run reference/original/bad sanity",
        "required_artifacts": ["reference_results", "negative_or_baseline_results"],
    },
    {
        "id": "P7_gates",
        "short": "P7",
        "label": "Run de-leak, quality, and package gates",
        "required_artifacts": ["gate_report"],
    },
    {
        "id": "P8_freeze",
        "short": "P8",
        "label": "Freeze package and ledger entry",
        "required_artifacts": ["task_package", "ledger_entry", "single_task_freeze_audit"],
    },
    {
        "id": "P9_split_audit",
        "short": "P9",
        "label": "Run split-level audit",
        "required_artifacts": ["split_audit"],
    },
]

STAGE_IDS = [stage["id"] for stage in STAGES]
SHORT_TO_STAGE = {stage["short"]: stage["id"] for stage in STAGES}
STAGE_INDEX = {stage_id: index for index, stage_id in enumerate(STAGE_IDS)}
FORMAL_AUTHORING_CONTRACT = {
    "one_task_at_a_time": True,
    "formal_task_records_must_be_agent_designed_one_by_one": True,
    "candidate_ranking_is_not_authoring": True,
    "candidate_review_cards_are_not_formal_design_briefs": True,
    "does_not_write_formal_task_data": True,
    "batch_formal_task_generation_forbidden": True,
    "formal_task_records_must_be_authored_separately": True,
    "new_expansion_repo_ids_must_be_unused_unless_explicitly_approved": True,
}
FORBIDDEN_FORMAL_GENERATION_MARKERS = {
    "auto_generated_formal_task",
    "auto_generated_task_jsonl",
    "batch_generated_formal_tasks",
    "batch_task_jsonl",
    "bulk_generated",
    "bulk_task_generation",
    "generated_formal_task",
    "generated_from_template",
    "mass_generated_tasks",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_stage(value: str) -> str:
    key = value.strip()
    if key in STAGE_INDEX:
        return key
    upper = key.upper()
    if upper in SHORT_TO_STAGE:
        return SHORT_TO_STAGE[upper]
    raise ValueError(f"unknown stage {value!r}; use one of {', '.join(STAGE_IDS)} or P0..P9")


def parse_key_values(values: list[str] | None, *, field_name: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for raw in values or []:
        if "=" not in raw:
            raise ValueError(f"{field_name} entry must use key=value: {raw}")
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"{field_name} entry has empty key: {raw}")
        parsed[key] = value.strip()
    return parsed


def nested_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            keys.add(str(key))
            keys.update(nested_keys(nested))
    elif isinstance(value, list):
        for item in value:
            keys.update(nested_keys(item))
    return keys


def nested_string_values(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, dict):
        for nested in value.values():
            values.extend(nested_string_values(nested))
    elif isinstance(value, list):
        for item in value:
            values.extend(nested_string_values(item))
    elif isinstance(value, str):
        values.append(value)
    return values


def progress_path_for(slot_id: str, progress_root: Path = DEFAULT_PROGRESS_ROOT) -> Path:
    safe_slot = slot_id.strip()
    if not safe_slot:
        raise ValueError("slot_id must not be empty")
    if "/" in safe_slot or "\\" in safe_slot:
        raise ValueError("slot_id must not contain path separators")
    return progress_root / f"{safe_slot}.json"


def initial_stages() -> dict[str, dict[str, Any]]:
    return {
        stage["id"]: {
            "status": "not_started",
            "label": stage["label"],
            "required_artifacts": list(stage["required_artifacts"]),
            "artifacts": {},
            "checks": {},
            "notes": [],
        }
        for stage in STAGES
    }


def active_lock_payload(progress_path: Path, progress: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "sitecontinuum_active_authoring_lock",
        "formal_task_record": False,
        "active": True,
        "slot_id": progress.get("slot_id"),
        "regime": progress.get("regime"),
        "repo_id": progress.get("repo_id"),
        "task_id": progress.get("task_id"),
        "progress_path": str(progress_path),
        "started_at_utc": progress.get("started_at_utc"),
        "updated_at_utc": progress.get("updated_at_utc"),
    }


def read_active_lock(active_lock: Path) -> dict[str, Any] | None:
    if not active_lock.exists():
        return None
    lock = load_json(active_lock)
    return lock if lock.get("active") else None


def formal_task_repo_refs(
    *,
    repo_id: str,
    current_slot_id: str,
    task_root: Path = DEFAULT_TASK_ROOT,
    task_pattern: str = DEFAULT_TASK_PATTERN,
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    if not repo_id or not task_root.exists():
        return refs
    for task_file in sorted(path for path in task_root.glob(task_pattern) if path.is_file()):
        slot_id = task_file.parent.name
        if slot_id == current_slot_id:
            continue
        try:
            rows = list(read_jsonl(task_file))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot inspect existing formal task file for repo reuse: {task_file}: {exc}") from exc
        for line_number, task in enumerate(rows, start=1):
            if str(task.get("repo_id", "")).strip() == repo_id:
                refs.append(
                    {
                        "slot_id": slot_id,
                        "path": str(task_file),
                        "line": line_number,
                        "task_id": task.get("task_id"),
                    }
                )
    return refs


def assert_repo_unused_for_new_slot(
    *,
    repo_id: str | None,
    slot_id: str,
    task_root: Path = DEFAULT_TASK_ROOT,
    task_pattern: str = DEFAULT_TASK_PATTERN,
    allow_reused_repo: bool = False,
) -> list[dict[str, Any]]:
    if not repo_id or allow_reused_repo:
        return []
    refs = formal_task_repo_refs(
        repo_id=repo_id,
        current_slot_id=slot_id,
        task_root=task_root,
        task_pattern=task_pattern,
    )
    if refs:
        shown = "; ".join(
            f"{ref['slot_id']}:{ref.get('task_id') or 'unknown'}@{ref['path']}:{ref['line']}"
            for ref in refs[:8]
        )
        omitted = "" if len(refs) <= 8 else f"; ... +{len(refs) - 8} more"
        raise ValueError(
            f"repo_id {repo_id} is already used by existing formal task(s): {shown}{omitted}. "
            "Choose an unused candidate repo, or pass --allow-reused-repo only for an explicitly approved exception."
        )
    return refs


def write_progress(path: Path, progress: dict[str, Any], active_lock: Path | None = None) -> None:
    progress["updated_at_utc"] = utc_now()
    write_json(path, progress)
    if active_lock is not None and progress.get("active"):
        write_json(active_lock, active_lock_payload(path, progress))


def start_authoring_task(
    *,
    slot_id: str,
    regime: str,
    progress_root: Path = DEFAULT_PROGRESS_ROOT,
    active_lock: Path = DEFAULT_ACTIVE_LOCK,
    repo_id: str | None = None,
    task_id: str | None = None,
    target: dict[str, Any] | None = None,
    notes: list[str] | None = None,
    allow_resume: bool = False,
    task_root: Path = DEFAULT_TASK_ROOT,
    task_pattern: str = DEFAULT_TASK_PATTERN,
    allow_reused_repo: bool = False,
) -> dict[str, Any]:
    if regime not in VALID_REGIMES:
        raise ValueError(f"regime must be one of {sorted(VALID_REGIMES)}")
    progress_path = progress_path_for(slot_id, progress_root)
    active = read_active_lock(active_lock)
    if active and active.get("progress_path") != str(progress_path):
        raise ValueError(
            "another authoring task is active: "
            f"{active.get('slot_id')} at {active.get('progress_path')}; close or reject it before starting a new task"
        )
    if progress_path.exists():
        if not allow_resume:
            raise ValueError(f"progress file already exists: {progress_path}; pass --allow-resume to reuse it")
        progress = load_json(progress_path)
        if progress.get("active") is False:
            raise ValueError(f"progress file is already closed: {progress_path}")
        progress["active"] = True
        write_progress(progress_path, progress, active_lock)
        return progress
    repo_reuse_refs = assert_repo_unused_for_new_slot(
        repo_id=repo_id,
        slot_id=slot_id,
        task_root=task_root,
        task_pattern=task_pattern,
        allow_reused_repo=allow_reused_repo,
    )

    now = utc_now()
    progress = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "formal_task_record": False,
        "active": True,
        "regime": regime,
        "slot_id": slot_id,
        "repo_id": repo_id,
        "task_id": task_id,
        "target": target or {},
        "started_at_utc": now,
        "updated_at_utc": now,
        "current_stage": STAGE_IDS[0],
        "stages": initial_stages(),
        "events": [
            {
                "at": now,
                "event": "started",
                "stage": STAGE_IDS[0],
                "note": "one-task-at-a-time authoring progress started",
            }
        ],
        "constraints": dict(FORMAL_AUTHORING_CONTRACT),
        "repo_reuse_policy": {
            "task_root": str(task_root),
            "task_pattern": task_pattern,
            "allow_reused_repo": bool(allow_reused_repo),
            "checked_repo_id": repo_id,
            "existing_refs": repo_reuse_refs,
        },
    }
    progress["stages"][STAGE_IDS[0]]["status"] = "in_progress"
    progress["stages"][STAGE_IDS[0]]["updated_at_utc"] = now
    for note in notes or []:
        progress["stages"][STAGE_IDS[0]]["notes"].append(note)
    write_progress(progress_path, progress, active_lock)
    return progress


def stage_can_be_recorded(progress: dict[str, Any], stage_id: str, status: str) -> list[str]:
    issues: list[str] = []
    if status not in VALID_STAGE_STATUSES:
        issues.append(f"invalid status {status!r}; use one of {sorted(VALID_STAGE_STATUSES)}")
        return issues
    index = STAGE_INDEX[stage_id]
    stages = progress.get("stages", {})
    for prior_stage in STAGE_IDS[:index]:
        prior_status = stages.get(prior_stage, {}).get("status")
        if prior_status != "passed":
            issues.append(f"{stage_id} cannot be marked before {prior_stage} is passed; observed {prior_status}")
            break
    if status == "passed":
        required = set(stages.get(stage_id, {}).get("required_artifacts", []))
        artifacts = set(stages.get(stage_id, {}).get("artifacts", {}))
        missing = sorted(required - artifacts)
        if missing:
            issues.append(f"{stage_id} cannot pass without required artifacts: {', '.join(missing)}")
    return issues


def next_open_stage(progress: dict[str, Any]) -> str | None:
    stages = progress.get("stages", {})
    for stage_id in STAGE_IDS:
        if stages.get(stage_id, {}).get("status") != "passed":
            return stage_id
    return None


def record_authoring_stage(
    *,
    progress_path: Path,
    stage: str,
    status: str,
    artifacts: dict[str, Any] | None = None,
    checks: dict[str, Any] | None = None,
    notes: list[str] | None = None,
    repo_id: str | None = None,
    task_id: str | None = None,
    active_lock: Path = DEFAULT_ACTIVE_LOCK,
    allow_missing_required: bool = False,
    task_root: Path = DEFAULT_TASK_ROOT,
    task_pattern: str = DEFAULT_TASK_PATTERN,
    allow_reused_repo: bool = False,
) -> dict[str, Any]:
    progress = load_json(progress_path)
    if progress.get("artifact_type") != ARTIFACT_TYPE or progress.get("formal_task_record") is not False:
        raise ValueError(f"not a non-formal per-task progress file: {progress_path}")
    if not progress.get("active"):
        raise ValueError(f"cannot update closed authoring progress: {progress_path}")
    stage_id = normalize_stage(stage)
    stage_record = progress["stages"][stage_id]
    effective_repo_id = repo_id or progress.get("repo_id")
    assert_repo_unused_for_new_slot(
        repo_id=effective_repo_id,
        slot_id=str(progress.get("slot_id") or ""),
        task_root=task_root,
        task_pattern=task_pattern,
        allow_reused_repo=allow_reused_repo,
    )
    if repo_id:
        progress["repo_id"] = repo_id
        progress["repo_reuse_policy"] = {
            "task_root": str(task_root),
            "task_pattern": task_pattern,
            "allow_reused_repo": bool(allow_reused_repo),
            "checked_repo_id": repo_id,
            "existing_refs": [],
        }
    if task_id:
        progress["task_id"] = task_id
    stage_record.setdefault("artifacts", {}).update(artifacts or {})
    stage_record.setdefault("checks", {}).update(checks or {})
    stage_record.setdefault("notes", []).extend(notes or [])
    issues = [] if allow_missing_required else stage_can_be_recorded(progress, stage_id, status)
    if issues:
        raise ValueError("; ".join(issues))
    stage_record["status"] = status
    stage_record["updated_at_utc"] = utc_now()
    if status == "passed":
        progress["current_stage"] = next_open_stage(progress) or STAGE_IDS[-1]
        next_stage = next_open_stage(progress)
        if next_stage and progress["stages"][next_stage]["status"] == "not_started":
            progress["stages"][next_stage]["status"] = "in_progress"
            progress["stages"][next_stage]["updated_at_utc"] = utc_now()
    else:
        progress["current_stage"] = stage_id
    progress.setdefault("events", []).append(
        {
            "at": utc_now(),
            "event": "stage_recorded",
            "stage": stage_id,
            "status": status,
            "artifacts": sorted((artifacts or {}).keys()),
            "checks": sorted((checks or {}).keys()),
        }
    )
    write_progress(progress_path, progress, active_lock)
    return progress


def audit_progress(progress: dict[str, Any], progress_path: Path | None = None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    issues: list[str] = []

    def add(name: str, passed: bool, issue: str, **extra: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), **extra})
        if not passed:
            issues.append(issue)

    add(
        "non_formal_progress",
        progress.get("artifact_type") == ARTIFACT_TYPE and progress.get("formal_task_record") is False,
        "progress file must be a non-formal per-task progress artifact",
        observed_artifact_type=progress.get("artifact_type"),
        observed_formal_task_record=progress.get("formal_task_record"),
    )
    add(
        "valid_regime",
        progress.get("regime") in VALID_REGIMES,
        "progress regime must be change or construction",
        observed=progress.get("regime"),
    )
    add(
        "slot_id_present",
        bool(progress.get("slot_id")),
        "progress must include slot_id",
    )
    constraints = progress.get("constraints")
    contract_failures = []
    if not isinstance(constraints, dict):
        contract_failures.append("constraints missing")
    else:
        for key, expected in FORMAL_AUTHORING_CONTRACT.items():
            if constraints.get(key) is not expected:
                contract_failures.append(key)
    add(
        "formal_authoring_contract",
        not contract_failures,
        "progress must preserve the no-bulk formal data authoring contract",
        missing_or_false=contract_failures,
    )
    progress_keys = nested_keys(progress)
    forbidden_keys = sorted(progress_keys & FORBIDDEN_FORMAL_GENERATION_MARKERS)
    forbidden_values = sorted(
        {
            value
            for value in nested_string_values(progress)
            if any(marker in value.lower().replace("-", "_").replace(" ", "_") for marker in FORBIDDEN_FORMAL_GENERATION_MARKERS)
        }
    )
    add(
        "no_bulk_generation_markers",
        not forbidden_keys and not forbidden_values,
        "progress contains markers associated with generated or bulk formal task data",
        forbidden_keys=forbidden_keys,
        forbidden_values=forbidden_values[:20],
        omitted_forbidden_value_count=max(0, len(forbidden_values) - 20),
    )
    stages = progress.get("stages")
    add(
        "all_stages_present",
        isinstance(stages, dict) and all(stage_id in stages for stage_id in STAGE_IDS),
        "progress must include all P0-P9 stages",
    )
    if isinstance(stages, dict):
        first_not_passed_seen = False
        for stage_id in STAGE_IDS:
            status = stages.get(stage_id, {}).get("status")
            valid_status = status in VALID_STAGE_STATUSES
            add(
                f"{stage_id}_status_valid",
                valid_status,
                f"{stage_id} has invalid status {status!r}",
                observed=status,
            )
            if first_not_passed_seen and status == "passed":
                add(
                    f"{stage_id}_sequential",
                    False,
                    f"{stage_id} passed after an earlier stage was not passed",
                )
            if status != "passed":
                first_not_passed_seen = True
            if status == "passed":
                required = set(stages.get(stage_id, {}).get("required_artifacts", []))
                artifacts = set(stages.get(stage_id, {}).get("artifacts", {}))
                missing = sorted(required - artifacts)
                add(
                    f"{stage_id}_required_artifacts",
                    not missing,
                    f"{stage_id} is missing required artifacts: {', '.join(missing)}",
                    missing=missing,
                )
    report = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "sitecontinuum_per_task_progress_audit",
        "formal_task_record": False,
        "generated_at_utc": utc_now(),
        "progress_path": str(progress_path) if progress_path else None,
        "slot_id": progress.get("slot_id"),
        "regime": progress.get("regime"),
        "repo_id": progress.get("repo_id"),
        "task_id": progress.get("task_id"),
        "active": progress.get("active"),
        "current_stage": progress.get("current_stage"),
        "final_decision": progress.get("final_decision"),
        "passed": not issues,
        "issue_count": len(issues),
        "issues": issues,
        "checks": checks,
    }
    return report


def audit_authoring_lock(
    *,
    ledger_root: Path = DEFAULT_LEDGER_ROOT,
    progress_root: Path = DEFAULT_PROGRESS_ROOT,
    active_lock: Path = DEFAULT_ACTIVE_LOCK,
) -> dict[str, Any]:
    progress_files = sorted(progress_root.glob("*.json")) if progress_root.exists() else []
    progress_reports = []
    active_progress = []
    issues: list[str] = []
    for path in progress_files:
        progress = load_json(path)
        report = audit_progress(progress, path)
        progress_reports.append(report)
        if progress.get("active"):
            active_progress.append(path)
        if not report["passed"]:
            issues.extend(f"{path}: {issue}" for issue in report["issues"])

    lock = read_active_lock(active_lock)
    if len(active_progress) > 1:
        issues.append(f"more than one active per-task progress file: {', '.join(str(path) for path in active_progress)}")
    if active_progress and not lock:
        issues.append("active progress exists but active lock is missing")
    if lock:
        lock_path = Path(str(lock.get("progress_path", "")))
        try:
            lock_path.resolve().relative_to(progress_root.resolve())
        except ValueError:
            issues.append(f"active lock points outside progress root: {lock_path}")
        if not lock_path.exists():
            issues.append(f"active lock points to missing progress file: {lock_path}")
        elif not active_progress:
            issues.append(f"active lock exists but no active progress file is present under progress root: {lock_path}")
        elif active_progress and lock_path.resolve() not in {path.resolve() for path in active_progress}:
            issues.append(f"active lock does not match active progress files: {lock_path}")
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "sitecontinuum_authoring_lock_audit",
        "formal_task_record": False,
        "generated_at_utc": utc_now(),
        "ledger_root": str(ledger_root),
        "progress_root": str(progress_root),
        "active_lock": str(active_lock),
        "progress_file_count": len(progress_files),
        "active_progress_count": len(active_progress),
        "active_progress_paths": [str(path) for path in active_progress],
        "lock": lock,
        "progress_reports": progress_reports,
        "passed": not issues,
        "issue_count": len(issues),
        "issues": issues,
    }


def close_authoring_task(
    *,
    progress_path: Path,
    decision: str,
    active_lock: Path = DEFAULT_ACTIVE_LOCK,
    notes: list[str] | None = None,
    allow_incomplete_accept: bool = False,
) -> dict[str, Any]:
    if decision not in FINAL_DECISIONS:
        raise ValueError(f"decision must be one of {sorted(FINAL_DECISIONS)}")
    progress = load_json(progress_path)
    if not progress.get("active"):
        raise ValueError(f"progress file is already closed: {progress_path}")
    report = audit_progress(progress, progress_path)
    if not report["passed"]:
        raise ValueError("cannot close invalid progress: " + "; ".join(report["issues"]))
    if decision == "accepted" and not allow_incomplete_accept:
        incomplete = [stage_id for stage_id in STAGE_IDS if progress["stages"][stage_id]["status"] != "passed"]
        if incomplete:
            raise ValueError(
                "accepted authoring task must pass P0-P9 first; incomplete stages: " + ", ".join(incomplete)
            )
    now = utc_now()
    progress["active"] = False
    progress["final_decision"] = decision
    progress["closed_at_utc"] = now
    if notes:
        progress.setdefault("close_notes", []).extend(notes)
    progress.setdefault("events", []).append({"at": now, "event": "closed", "decision": decision})
    write_progress(progress_path, progress, active_lock=None)
    lock = read_active_lock(active_lock)
    if lock and lock.get("progress_path") == str(progress_path):
        write_json(
            active_lock,
            {
                **lock,
                "active": False,
                "closed_at_utc": now,
                "final_decision": decision,
            },
        )
    return progress


def load_strict_repair_item(
    *,
    repair_plan: Path,
    slot_id: str | None = None,
    repair_index: int | None = None,
) -> dict[str, Any]:
    if not repair_plan.exists():
        raise ValueError(f"strict authoring repair plan missing: {repair_plan}")
    plan = load_json(repair_plan)
    if plan.get("artifact_type") != STRICT_REPAIR_PLAN_ARTIFACT_TYPE or plan.get("formal_task_record") is not False:
        raise ValueError(f"not a non-formal strict authoring repair plan: {repair_plan}")
    items = plan.get("repair_items")
    if not isinstance(items, list):
        raise ValueError(f"strict authoring repair plan has no repair_items list: {repair_plan}")
    if slot_id and repair_index is not None:
        raise ValueError("choose either --slot-id or --repair-index, not both")
    if repair_index is None and not slot_id:
        repair_index = 1
    matches = [
        item
        for item in items
        if isinstance(item, dict)
        and ((slot_id and item.get("slot_id") == slot_id) or (repair_index is not None and item.get("repair_index") == repair_index))
    ]
    if len(matches) != 1:
        selector = f"slot_id={slot_id}" if slot_id else f"repair_index={repair_index}"
        raise ValueError(f"strict authoring repair plan has {len(matches)} matches for {selector}")
    return matches[0]


def start_strict_authoring_repair(
    *,
    repair_plan: Path,
    slot_id: str | None = None,
    repair_index: int | None = None,
    ledger_root: Path = DEFAULT_LEDGER_ROOT,
    progress_root: Path | None = None,
    active_lock: Path | None = None,
    allow_resume: bool = False,
) -> dict[str, Any]:
    item = load_strict_repair_item(repair_plan=repair_plan, slot_id=slot_id, repair_index=repair_index)
    selected_slot_id = str(item.get("slot_id") or "")
    if not selected_slot_id:
        raise ValueError("selected strict repair item has no slot_id")
    blockers = sorted(str(blocker) for blocker in item.get("blockers", []) or [])
    target = {
        "authoring_mode": "strict_repair_existing_slot",
        "source_repair_plan": str(repair_plan),
        "repair_index": item.get("repair_index"),
        "blockers": blockers,
        "details": item.get("details", {}),
        "required_resolution": item.get("required_resolution", []),
        "forbidden_actions": item.get("forbidden_actions", []),
        "formal_task_path": item.get("path"),
        "writes_formal_task_data": False,
        "does_not_generate_no_bulk_declaration": True,
        "does_not_generate_freeze_audit": True,
        "duplicate_repo_requires_explicit_resolution": "duplicate_repo_id" in blockers,
    }
    notes = [
        "Started from strict_authoring_repair_plan; repair is one slot only.",
        "Do not batch-write declarations, freeze audits, replacement task rows, or ledger acceptances.",
    ]
    return start_authoring_task(
        slot_id=selected_slot_id,
        regime=CHANGE_REGIME,
        progress_root=progress_root or ledger_root / "per_task_progress",
        active_lock=active_lock or ledger_root / "active_authoring_task.json",
        repo_id=str(item.get("repo_id") or "") or None,
        task_id=str(item.get("task_id") or "") or None,
        target=target,
        notes=notes,
        allow_resume=allow_resume,
        allow_reused_repo=True,
    )


def add_start_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--slot-id", required=True)
    parser.add_argument("--regime", choices=sorted(VALID_REGIMES), required=True)
    parser.add_argument("--repo-id", default=None)
    parser.add_argument("--task-id", default=None)
    parser.add_argument("--target", action="append", default=[], help="Target metadata as key=value; repeatable.")
    parser.add_argument("--note", action="append", default=[])
    parser.add_argument("--ledger-root", type=Path, default=DEFAULT_LEDGER_ROOT)
    parser.add_argument("--progress-root", type=Path, default=None)
    parser.add_argument("--active-lock", type=Path, default=None)
    parser.add_argument("--allow-resume", action="store_true")
    parser.add_argument("--task-root", type=Path, default=DEFAULT_TASK_ROOT)
    parser.add_argument("--task-pattern", default=DEFAULT_TASK_PATTERN)
    parser.add_argument(
        "--allow-reused-repo",
        action="store_true",
        help="Allow starting a new slot with a repo_id that already appears in another formal task; use only for an approved exception.",
    )


def add_start_strict_repair_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repair-plan",
        type=Path,
        default=DEFAULT_LEDGER_ROOT / "strict_authoring_repair_plan.json",
    )
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--slot-id", default=None)
    selector.add_argument("--repair-index", type=int, default=None)
    parser.add_argument("--ledger-root", type=Path, default=DEFAULT_LEDGER_ROOT)
    parser.add_argument("--progress-root", type=Path, default=None)
    parser.add_argument("--active-lock", type=Path, default=None)
    parser.add_argument("--allow-resume", action="store_true")


def run_start_from_args(args: argparse.Namespace) -> None:
    ledger_root = args.ledger_root
    progress_root = args.progress_root or ledger_root / "per_task_progress"
    active_lock = args.active_lock or ledger_root / "active_authoring_task.json"
    try:
        progress = start_authoring_task(
            slot_id=args.slot_id,
            regime=args.regime,
            progress_root=progress_root,
            active_lock=active_lock,
            repo_id=args.repo_id,
            task_id=args.task_id,
            target=parse_key_values(args.target, field_name="target"),
            notes=args.note,
            allow_resume=args.allow_resume,
            task_root=args.task_root,
            task_pattern=args.task_pattern,
            allow_reused_repo=args.allow_reused_repo,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"started authoring task {progress['slot_id']} ({progress['regime']}) at {progress_path_for(progress['slot_id'], progress_root)}")


def run_start_strict_repair_from_args(args: argparse.Namespace) -> None:
    ledger_root = args.ledger_root
    progress_root = args.progress_root or ledger_root / "per_task_progress"
    active_lock = args.active_lock or ledger_root / "active_authoring_task.json"
    try:
        progress = start_strict_authoring_repair(
            repair_plan=args.repair_plan,
            slot_id=args.slot_id,
            repair_index=args.repair_index,
            ledger_root=ledger_root,
            progress_root=progress_root,
            active_lock=active_lock,
            allow_resume=args.allow_resume,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(
        f"started strict authoring repair {progress['slot_id']} at "
        f"{progress_path_for(progress['slot_id'], progress_root)}"
    )


def add_record_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--status", choices=sorted(VALID_STAGE_STATUSES), required=True)
    parser.add_argument("--artifact", action="append", default=[], help="Artifact reference as key=path; repeatable.")
    parser.add_argument("--check", action="append", default=[], help="Check summary as key=value; repeatable.")
    parser.add_argument("--note", action="append", default=[])
    parser.add_argument("--repo-id", default=None)
    parser.add_argument("--task-id", default=None)
    parser.add_argument("--active-lock", type=Path, default=DEFAULT_ACTIVE_LOCK)
    parser.add_argument("--allow-missing-required", action="store_true")
    parser.add_argument("--task-root", type=Path, default=DEFAULT_TASK_ROOT)
    parser.add_argument("--task-pattern", default=DEFAULT_TASK_PATTERN)
    parser.add_argument(
        "--allow-reused-repo",
        action="store_true",
        help="Allow recording a repo_id that already appears in another formal task; use only for an approved exception.",
    )


def run_record_from_args(args: argparse.Namespace) -> None:
    try:
        progress = record_authoring_stage(
            progress_path=args.progress,
            stage=args.stage,
            status=args.status,
            artifacts=parse_key_values(args.artifact, field_name="artifact"),
            checks=parse_key_values(args.check, field_name="check"),
            notes=args.note,
            repo_id=args.repo_id,
            task_id=args.task_id,
            active_lock=args.active_lock,
            allow_missing_required=args.allow_missing_required,
            task_root=args.task_root,
            task_pattern=args.task_pattern,
            allow_reused_repo=args.allow_reused_repo,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"recorded {normalize_stage(args.stage)}={args.status} for {progress.get('slot_id')}; current={progress.get('current_stage')}")


def add_audit_task_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)


def run_audit_task_from_args(args: argparse.Namespace) -> None:
    report = audit_progress(load_json(args.progress), args.progress)
    if args.output:
        write_json(args.output, report)
    status = "passed" if report["passed"] else "failed"
    print(f"authoring task audit {status}: issues={report['issue_count']}")
    if not report["passed"]:
        raise SystemExit(1)


def add_audit_lock_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ledger-root", type=Path, default=DEFAULT_LEDGER_ROOT)
    parser.add_argument("--progress-root", type=Path, default=None)
    parser.add_argument("--active-lock", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)


def run_audit_lock_from_args(args: argparse.Namespace) -> None:
    ledger_root = args.ledger_root
    progress_root = args.progress_root or ledger_root / "per_task_progress"
    active_lock = args.active_lock or ledger_root / "active_authoring_task.json"
    report = audit_authoring_lock(ledger_root=ledger_root, progress_root=progress_root, active_lock=active_lock)
    if args.output:
        write_json(args.output, report)
    status = "passed" if report["passed"] else "failed"
    print(
        f"authoring lock audit {status}: active={report['active_progress_count']} "
        f"progress_files={report['progress_file_count']} issues={report['issue_count']}"
    )
    if not report["passed"]:
        raise SystemExit(1)


def add_close_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--decision", choices=sorted(FINAL_DECISIONS), required=True)
    parser.add_argument("--active-lock", type=Path, default=DEFAULT_ACTIVE_LOCK)
    parser.add_argument("--note", action="append", default=[])
    parser.add_argument("--allow-incomplete-accept", action="store_true")


def run_close_from_args(args: argparse.Namespace) -> None:
    try:
        progress = close_authoring_task(
            progress_path=args.progress,
            decision=args.decision,
            active_lock=args.active_lock,
            notes=args.note,
            allow_incomplete_accept=args.allow_incomplete_accept,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"closed authoring task {progress.get('slot_id')} as {args.decision}")
