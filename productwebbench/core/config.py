from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPO_ROOT = PROJECT_ROOT / "data" / "awesome_web_repo"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data" / "productwebbench"

MANIFEST_DIR = DEFAULT_OUTPUT_ROOT / "manifest"
TASK_DIR = DEFAULT_OUTPUT_ROOT / "tasks"
ARTIFACT_DIR = DEFAULT_OUTPUT_ROOT / "artifacts"


WORKSPACE_META_NAME = ".productwebbench_workspace.json"
# The marker was written under the benchmark's old working name. Workspaces
# extracted before the rename still carry it, so reads accept either.
LEGACY_WORKSPACE_META_NAME = ".sitecontinuum_workspace.json"


def workspace_meta_path(directory):
    """Path to a workspace's marker, preferring the current name.

    Returns the legacy path only when it exists and the current one does not,
    so a write through this function always lands on the current name.
    """
    from pathlib import Path

    directory = Path(directory)
    current = directory / WORKSPACE_META_NAME
    if current.exists():
        return current
    legacy = directory / LEGACY_WORKSPACE_META_NAME
    return legacy if legacy.exists() else current
