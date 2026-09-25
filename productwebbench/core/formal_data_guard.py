from __future__ import annotations

from pathlib import Path

from .config import TASK_DIR


def _resolve_for_policy(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def assert_not_under_formal_task_root(path: Path, *, purpose: str) -> None:
    """Prevent derived/evaluation artifacts from being written into task data."""

    resolved_path = _resolve_for_policy(path)
    resolved_task_dir = _resolve_for_policy(TASK_DIR)
    if _is_relative_to(resolved_path, resolved_task_dir):
        raise ValueError(
            f"{purpose} must not write inside formal task data root {resolved_task_dir}. "
            "Formal slot task files are authored one at a time under the strict P0-P9 workflow; "
            "derived evaluation/export artifacts must use a separate output root."
        )
