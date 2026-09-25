from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...core.config import DEFAULT_OUTPUT_ROOT
from ...core.formal_data_guard import assert_not_under_formal_task_root
from ...core.io_utils import ensure_dir, read_jsonl, write_json, write_jsonl
from ...taxonomy.capability import CONSTRUCTION_REGIME
from .precheck import PRECHECK_ARTIFACT_TYPE, precheck_construction_task
from .scoring import score_trajectory
from .task_digest import stable_task_digest


DEFAULT_CONSTRUCTION_LEDGER = DEFAULT_OUTPUT_ROOT / "authoring_ledger" / "construction_ledger.jsonl"
ACCEPTANCE_ARTIFACT_TYPE = "construction_acceptance_ledger_row"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def existing_ledger_rows(path: Path) -> list[dict[str, Any]]:
    return list(read_jsonl(path)) if path.exists() else []


def file_sha256(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    import hashlib

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


def paths_equal(left: str | None, right: Path) -> bool:
    if not left:
        return False
    try:
        return Path(left).resolve() == right.resolve()
    except OSError:
        return str(Path(left)) == str(right)


def validate_acceptance_ledger_row(row: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if row.get("artifact_type") != ACCEPTANCE_ARTIFACT_TYPE:
        issues.append("accepted construction ledger row has wrong or missing artifact_type")
    if row.get("formal_task_record") is not False:
        issues.append("accepted construction ledger row must be formal_task_record=false")
    if row.get("status") != "accepted":
        issues.append("construction ledger row status must be accepted")
    if row.get("precheck_artifact_type") != PRECHECK_ARTIFACT_TYPE:
        issues.append("accepted construction ledger row must reference a construction_precheck_report")
    if row.get("precheck_formal_task_record") is not False:
        issues.append("accepted construction precheck must be formal_task_record=false")
    if row.get("precheck_passed") is not True:
        issues.append("accepted construction ledger row must have precheck_passed=true")
    if row.get("precheck_issue_count") not in (0, None):
        issues.append(f"accepted construction ledger row has precheck_issue_count={row.get('precheck_issue_count')}")
    if row.get("precheck_require_reference_actor") is not True:
        issues.append("accepted construction ledger row must come from strict precheck with require_reference_actor=true")

    task_path_text = str(row.get("task_path") or "")
    task_path = Path(task_path_text) if task_path_text else None
    task: dict[str, Any] = {}
    if task_path is None or not task_path.exists():
        issues.append(f"accepted construction task_path does not exist: {task_path_text or '<missing>'}")
    else:
        try:
            task = load_json(task_path)
        except (OSError, json.JSONDecodeError) as exc:
            issues.append(f"accepted construction task_path is unreadable: {task_path}: {exc}")
    if task:
        task_digest = stable_task_digest(task)
        if row.get("task_id") != task.get("task_id"):
            issues.append("accepted construction ledger row task_id does not match current task")
        if row.get("repo_id") != task.get("repo_id"):
            issues.append("accepted construction ledger row repo_id does not match current task")
        if row.get("task_sha256") != task_digest:
            issues.append("accepted construction ledger row task_sha256 does not match current task")
        if row.get("precheck_task_sha256") != task_digest:
            issues.append("accepted construction ledger row precheck_task_sha256 does not match current task")

    input_files = row.get("input_files")
    if not isinstance(input_files, dict):
        issues.append("accepted construction ledger row input_files is missing or malformed")
        input_files = {}
    task_input = input_files.get("task")
    if not isinstance(task_input, dict):
        issues.append("accepted construction ledger row input_files.task is missing or malformed")
    elif task_path is not None:
        if not paths_equal(task_input.get("path"), task_path):
            issues.append("accepted construction ledger row input_files.task path mismatch")
        if file_sha256(task_path) != task_input.get("sha256"):
            issues.append("accepted construction ledger row input_files.task digest is stale")

    precheck_path_text = str(row.get("precheck_path") or "")
    precheck_path = Path(precheck_path_text) if precheck_path_text else None
    if precheck_path is None:
        issues.append("accepted construction ledger row must store a strict precheck_path")
    elif not precheck_path.exists():
        issues.append(f"accepted construction precheck_path does not exist: {precheck_path}")
    precheck_input = input_files.get("precheck")
    if not isinstance(precheck_input, dict):
        issues.append("accepted construction ledger row input_files.precheck is missing or malformed")
    elif precheck_path is not None:
        if not paths_equal(precheck_input.get("path"), precheck_path):
            issues.append("accepted construction ledger row input_files.precheck path mismatch")
        if file_sha256(precheck_path) != precheck_input.get("sha256"):
            issues.append("accepted construction ledger row input_files.precheck digest is stale")
    return issues


def validate_supplied_precheck(
    supplied_precheck: dict[str, Any],
    *,
    task: dict[str, Any],
    task_path: Path,
) -> list[str]:
    issues: list[str] = []
    task_digest = stable_task_digest(task)
    if supplied_precheck.get("artifact_type") != PRECHECK_ARTIFACT_TYPE:
        issues.append(f"supplied precheck artifact_type must be {PRECHECK_ARTIFACT_TYPE}")
    if supplied_precheck.get("formal_task_record") is not False:
        issues.append("supplied precheck must be a non-formal report with formal_task_record=false")
    if supplied_precheck.get("task_id") != task.get("task_id"):
        issues.append("supplied precheck task_id does not match the task being accepted")
    if supplied_precheck.get("repo_id") != task.get("repo_id"):
        issues.append("supplied precheck repo_id does not match the task being accepted")
    if supplied_precheck.get("require_reference_actor") is not True:
        issues.append("supplied precheck must be a strict report with require_reference_actor=true")
    if supplied_precheck.get("task_sha256") != task_digest:
        issues.append("supplied precheck task_sha256 does not match the current task content")
    if not paths_equal(supplied_precheck.get("artifact_root"), task_path.parent):
        issues.append("supplied precheck artifact_root does not match the task directory")
    if supplied_precheck.get("passed") is not True:
        issues.append("supplied precheck did not pass")
    try:
        issue_count = int(supplied_precheck.get("issue_count") or 0)
    except (TypeError, ValueError):
        issue_count = -1
    if issue_count != 0:
        issues.append("supplied precheck has nonzero issue_count")
    return issues


def construction_acceptance_record(task: dict[str, Any], task_path: Path, precheck: dict[str, Any], precheck_path: Path | None) -> dict[str, Any]:
    reference_report = task.get("reference_trajectory_report", {})
    score = score_trajectory(reference_report) if isinstance(reference_report, dict) else {}
    trace = task.get("reference_actor_trace", {})
    milestones = task.get("milestones", [])
    return {
        "schema_version": "2026-06-18",
        "artifact_type": ACCEPTANCE_ARTIFACT_TYPE,
        "formal_task_record": False,
        "status": "accepted",
        "accepted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "regime": CONSTRUCTION_REGIME,
        "task_id": task.get("task_id"),
        "repo_id": task.get("repo_id"),
        "website_type": task.get("website_type"),
        "split": task.get("split"),
        "task_path": str(task_path),
        "task_sha256": stable_task_digest(task),
        "precheck_path": str(precheck_path) if precheck_path else None,
        "precheck_artifact_type": precheck.get("artifact_type"),
        "precheck_formal_task_record": precheck.get("formal_task_record"),
        "precheck_passed": precheck.get("passed"),
        "precheck_issue_count": precheck.get("issue_count"),
        "precheck_require_reference_actor": precheck.get("require_reference_actor"),
        "precheck_task_sha256": precheck.get("task_sha256"),
        "input_files": {
            "task": input_file_record(task_path),
            "precheck": input_file_record(precheck_path),
        },
        "milestone_count": len(milestones) if isinstance(milestones, list) else None,
        "reference_actor_trace_source": trace.get("source_kind") if isinstance(trace, dict) else None,
        "metrics": {
            "TCS": score.get("TCS", {}).get("score"),
            "TD": score.get("TD", {}).get("score"),
            "ITR": score.get("ITR", {}).get("score"),
            "QS": score.get("QS", {}).get("score"),
        },
    }


def accept_construction_task(
    *,
    task_path: Path,
    ledger_path: Path = DEFAULT_CONSTRUCTION_LEDGER,
    precheck_path: Path | None = None,
    write_precheck_path: Path | None = None,
    allow_duplicate: bool = False,
) -> dict[str, Any]:
    assert_not_under_formal_task_root(ledger_path, purpose="Construction acceptance ledger")
    if write_precheck_path is not None:
        assert_not_under_formal_task_root(write_precheck_path, purpose="Construction live precheck report")
    task = load_json(task_path)
    if precheck_path is not None and not precheck_path.exists():
        raise ValueError(f"supplied construction precheck does not exist: {precheck_path}")
    supplied_precheck = load_json(precheck_path) if precheck_path else None
    precheck = precheck_construction_task(task, artifact_root=task_path.parent)
    if isinstance(supplied_precheck, dict):
        supplied_issues = validate_supplied_precheck(supplied_precheck, task=task, task_path=task_path)
        precheck["supplied_precheck"] = {
            "path": str(precheck_path),
            "passed": supplied_precheck.get("passed"),
            "issue_count": supplied_precheck.get("issue_count"),
            "task_sha256": supplied_precheck.get("task_sha256"),
            "validation_passed": not supplied_issues,
            "validation_issues": supplied_issues,
        }
        if supplied_issues:
            raise ValueError("supplied construction precheck is not bound to this task: " + "; ".join(supplied_issues))
    if write_precheck_path is None:
        raise ValueError(
            "construction acceptance requires --write-precheck so the live strict precheck computed at acceptance time is persisted"
        )
    ensure_dir(write_precheck_path.parent)
    write_json(write_precheck_path, precheck)
    precheck_path = write_precheck_path

    if not precheck.get("passed"):
        raise ValueError(
            "construction task cannot be accepted; strict precheck failed: "
            + "; ".join(str(issue) for issue in precheck.get("issues", []))
        )
    rows = existing_ledger_rows(ledger_path)
    task_id = task.get("task_id")
    if not allow_duplicate and any(row.get("task_id") == task_id and row.get("status") == "accepted" for row in rows):
        raise ValueError(f"construction task already accepted in ledger: {task_id}")

    record = construction_acceptance_record(task, task_path, precheck, precheck_path)
    record_issues = validate_acceptance_ledger_row(record)
    if record_issues:
        raise ValueError("construction acceptance row failed self-audit: " + "; ".join(record_issues))
    rows.append(record)
    ensure_dir(ledger_path.parent)
    write_jsonl(ledger_path, rows)
    return record


def add_accept_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_CONSTRUCTION_LEDGER)
    parser.add_argument("--precheck", type=Path, default=None)
    parser.add_argument(
        "--write-precheck",
        type=Path,
        default=None,
        help="Required for acceptance; writes the live strict precheck that the ledger row binds to.",
    )
    parser.add_argument("--allow-duplicate", action="store_true")


def run_accept_from_args(args: argparse.Namespace) -> None:
    try:
        record = accept_construction_task(
            task_path=args.task,
            ledger_path=args.ledger,
            precheck_path=args.precheck,
            write_precheck_path=args.write_precheck,
            allow_duplicate=args.allow_duplicate,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"accepted construction task {record.get('task_id')} into {args.ledger}")
