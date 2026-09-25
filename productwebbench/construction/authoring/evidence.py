from __future__ import annotations

import argparse
import json
from pathlib import Path

from ...core.config import DEFAULT_OUTPUT_ROOT
from ...core.io_utils import read_jsonl, write_json


def load_capture(repo_id: str, states_root: Path) -> dict | None:
    capture_path = states_root / repo_id / "state_capture.json"
    if not capture_path.exists():
        return None
    return json.loads(capture_path.read_text(encoding="utf-8"))


def build_evidence_index(tasks_path: Path, states_root: Path, output_path: Path) -> dict:
    items = []
    for task in read_jsonl(tasks_path):
        repo_id = task["repo_id"]
        capture = load_capture(repo_id, states_root)
        if capture is None:
            items.append(
                {
                    "task_id": task["task_id"],
                    "repo_id": repo_id,
                    "has_state_capture": False,
                    "quality_pass": False,
                    "states": [],
                    "missing_required_states": task.get("required_states", []),
                }
            )
            continue

        captured_state_ids = {state["state_id"] for state in capture.get("states", [])}
        required = set(task.get("required_states", []))
        # Initial states are generic evidence. Task-specific scrolled/interaction states are expected later.
        generic_required = {state for state in required if state in {"desktop_initial", "tablet_initial", "mobile_initial"}}
        missing = sorted(generic_required - captured_state_ids)
        items.append(
            {
                "task_id": task["task_id"],
                "repo_id": repo_id,
                "has_state_capture": True,
                "quality_pass": bool(capture.get("quality_pass")),
                "states": capture.get("states", []),
                "missing_required_states": missing,
                "console_message_count": len(capture.get("console_messages", [])),
            }
        )

    summary = {
        "tasks_path": str(tasks_path),
        "states_root": str(states_root),
        "total": len(items),
        "with_state_capture": sum(1 for item in items if item["has_state_capture"]),
        "quality_pass": sum(1 for item in items if item["quality_pass"]),
        "items": items,
    }
    write_json(output_path, summary)
    return summary


def add_index_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tasks", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.jsonl")
    parser.add_argument("--states-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "states")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.evidence.json")


def run_index_from_args(args: argparse.Namespace) -> None:
    summary = build_evidence_index(args.tasks, args.states_root, args.output)
    print(
        f"indexed {summary['total']} tasks: "
        f"{summary['with_state_capture']} with captures, {summary['quality_pass']} quality-pass"
    )

