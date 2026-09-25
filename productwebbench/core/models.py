from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


MEDIA_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".svg",
    ".webp",
    ".gif",
    ".mp4",
    ".webm",
    ".glb",
    ".gltf",
    ".blend",
}


@dataclass(frozen=True)
class RepoRecord:
    repo_id: str
    owner: str
    name: str
    commit: str
    zip_path: str
    date_bucket: str
    framework: str
    package_manager: str
    has_package_json: bool
    has_lockfile: bool
    has_routes: bool
    has_tests: bool
    has_media: bool
    media_count: dict[str, int]
    file_count: int
    uncompressed_size: int
    likely_ui_repo: bool
    install_command: str
    build_command: str
    dev_command: str
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SiteContinuumTask:
    task_id: str
    repo_id: str
    split: str
    family: str
    intent: str
    scope: str
    difficulty: str
    problem_statement: str
    required_content: list[str]
    design_constraints: list[str]
    state_constraints: list[str]
    assets_to_consider: list[str]
    suggested_files: list[str]
    required_states: list[str]
    hidden_states: list[str]
    evaluation_rubric: dict[str, list[str]]
    author_notes: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def repo_id_from_zip(path: Path) -> tuple[str, str, str, str]:
    stem = path.stem
    parts = stem.split("#")
    if len(parts) >= 3:
        owner = parts[0]
        name = parts[1]
        commit = "#".join(parts[2:])
    else:
        owner = "unknown"
        name = stem
        commit = "unknown"
    repo_id = f"{owner}__{name}__{commit[:12]}"
    return repo_id, owner, name, commit
