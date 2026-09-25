from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ..execution.runtime import browser_state, runability


def add_runability_args(parser: argparse.ArgumentParser) -> None:
    runability.add_run_args(parser)


def run_runability(args: argparse.Namespace) -> None:
    runability.run_from_args(args)


def add_capture_args(parser: argparse.ArgumentParser) -> None:
    browser_state.add_capture_args(parser)


def run_capture(args: argparse.Namespace) -> None:
    browser_state.run_from_args(args)


def state_capture_path(states_root: Path, repo_id: str) -> Path:
    return states_root / repo_id / "state_capture.json"


def capture_report_path(states_root: Path, repo_id: str) -> Path:
    return states_root / repo_id / "capture_report.json"


def runtime_status() -> dict[str, Any]:
    required = {
        "add_runability_args": callable(add_runability_args),
        "run_runability": callable(run_runability),
        "add_capture_args": callable(add_capture_args),
        "run_capture": callable(run_capture),
        "state_capture_path": callable(state_capture_path),
        "capture_report_path": callable(capture_report_path),
        "underlying_run_state_capture": callable(getattr(browser_state, "run_state_capture", None)),
        "underlying_runability_entry": callable(getattr(runability, "run_from_args", None)),
    }
    return {
        "schema_version": "2026-06-19",
        "artifact_type": "shared_capture_runtime_status",
        "formal_task_record": False,
        "shared_for_regimes": ["change", "construction"],
        "facade_module": __name__,
        "underlying_capture_module": browser_state.__name__,
        "underlying_runability_module": runability.__name__,
        "required_entrypoints": required,
        "passed": all(required.values()),
        "issues": [name for name, ok in required.items() if not ok],
    }


def add_status_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", type=Path, default=None)


def run_status_from_args(args: argparse.Namespace) -> None:
    import json

    from ..core.io_utils import write_json

    report = runtime_status()
    if args.output:
        write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
