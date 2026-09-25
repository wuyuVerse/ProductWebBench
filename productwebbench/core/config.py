from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPO_ROOT = PROJECT_ROOT / "data" / "awesome_web_repo"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data" / "sitecontinuum"

MANIFEST_DIR = DEFAULT_OUTPUT_ROOT / "manifest"
TASK_DIR = DEFAULT_OUTPUT_ROOT / "tasks"
ARTIFACT_DIR = DEFAULT_OUTPUT_ROOT / "artifacts"
