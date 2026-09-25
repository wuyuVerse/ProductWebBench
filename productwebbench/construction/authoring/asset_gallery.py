from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from ...core.config import DEFAULT_OUTPUT_ROOT
from ...core.io_utils import ensure_dir, read_jsonl, write_json
from .dossiers import ASSET_SUFFIXES, EXCLUDED_PARTS, SOURCE_SUFFIXES, workspace_meta


PREVIEW_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif", ".ico"}
MEDIA_SUFFIXES = {".mp4", ".webm", ".ogv"}
MODEL_SUFFIXES = {".glb", ".gltf"}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def is_excluded(path: Path) -> bool:
    return any(part in EXCLUDED_PARTS for part in path.parts)


def all_asset_files(project_root: Path) -> list[Path]:
    assets: list[Path] = []
    for root, dirs, filenames in os.walk(project_root):
        dirs[:] = [dirname for dirname in dirs if dirname not in EXCLUDED_PARTS]
        root_path = Path(root)
        for filename in filenames:
            path = root_path / filename
            rel = path.relative_to(project_root)
            if not is_excluded(rel) and path.suffix.lower() in ASSET_SUFFIXES:
                assets.append(path)
    return sorted(assets)


def all_source_files(project_root: Path) -> list[Path]:
    sources: list[Path] = []
    for root, dirs, filenames in os.walk(project_root):
        dirs[:] = [dirname for dirname in dirs if dirname not in EXCLUDED_PARTS]
        root_path = Path(root)
        for filename in filenames:
            path = root_path / filename
            rel = path.relative_to(project_root)
            if not is_excluded(rel) and path.suffix.lower() in SOURCE_SUFFIXES:
                sources.append(path)
    return sorted(sources)


def relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def task_asset_patterns(tasks: list[dict[str, Any]], repo_id: str) -> list[str]:
    patterns: list[str] = []
    for task in tasks:
        if task["repo_id"] == repo_id:
            patterns.extend(task.get("assets_to_consider", []))
    return patterns


def task_is_canvas_like(tasks: list[dict[str, Any]], repo_id: str) -> bool:
    for task in tasks:
        if task["repo_id"] != repo_id:
            continue
        blob = " ".join(
            [
                str(task.get("website_type", "")),
                str(task.get("problem_statement", "")),
                " ".join(str(item) for item in task.get("required_content", [])),
            ]
        ).lower()
        if "canvas" in blob or "webgl" in blob or "three.js" in blob or task.get("website_type") == "interactive_canvas":
            return True
    return False


def matches_any(path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        normalized = pattern.rstrip("/")
        if fnmatch.fnmatch(path, normalized) or fnmatch.fnmatch(path, normalized.rstrip("*") + "*"):
            return True
        if normalized.endswith("/**") and path.startswith(normalized[:-3].rstrip("/") + "/"):
            return True
    return False


def semantic_tags(rel_path: str) -> list[str]:
    lowered = rel_path.lower()
    tags = []
    for tag in [
        "product",
        "quickview",
        "user",
        "brand",
        "icon",
        "project",
        "skill",
        "lottie",
        "tutorial",
        "resources",
        "video",
        "mobile",
        "model",
        "canvas",
    ]:
        if tag in lowered:
            tags.append(tag)
    suffix = Path(rel_path).suffix.lower()
    if suffix in MODEL_SUFFIXES:
        tags.append("3d")
    if suffix in MEDIA_SUFFIXES:
        tags.append("video")
    return sorted(set(tags))


def rank_asset(rel_path: str, patterns: list[str]) -> tuple[int, int, str]:
    score = 0
    suffix = Path(rel_path).suffix.lower()
    if matches_any(rel_path, patterns):
        score += 100
    score += 10 * len(semantic_tags(rel_path))
    if suffix in PREVIEW_SUFFIXES:
        score += 8
    elif suffix in MEDIA_SUFFIXES:
        score += 5
    elif suffix in MODEL_SUFFIXES:
        score += 4
    return (-score, rel_path.count("/"), rel_path)


def safe_gallery_name(index: int, rel_path: str) -> str:
    suffix = Path(rel_path).suffix.lower()
    stem = Path(rel_path).stem.replace(" ", "_")
    safe_stem = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in stem)[:60]
    return f"{index:03d}_{safe_stem}{suffix}"


def asset_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in PREVIEW_SUFFIXES:
        return "preview_image"
    if suffix in MEDIA_SUFFIXES:
        return "video"
    if suffix in MODEL_SUFFIXES:
        return "3d_model"
    return "asset"


def build_gallery_for_repo(
    repo_id: str,
    tasks: list[dict[str, Any]],
    workspace_root: Path,
    output_root: Path,
    max_assets: int,
) -> dict[str, Any]:
    meta = workspace_meta(workspace_root, repo_id)
    gallery_dir = ensure_dir(output_root / repo_id)
    asset_dir = ensure_dir(gallery_dir / "assets")
    if meta is None:
        gallery = {"repo_id": repo_id, "passed": False, "errors": ["missing workspace metadata"], "items": []}
        write_json(gallery_dir / "gallery.json", gallery)
        return gallery

    project_root = Path(meta["project_root"])
    patterns = task_asset_patterns(tasks, repo_id)
    candidates = [(relative(path, project_root), path) for path in all_asset_files(project_root)]
    ranked = sorted(candidates, key=lambda pair: rank_asset(pair[0], patterns))[:max_assets]
    items = []
    for index, (rel_path, source_path) in enumerate(ranked):
        copied_name = safe_gallery_name(index, rel_path)
        copied_path = asset_dir / copied_name
        shutil.copy2(source_path, copied_path)
        items.append(
            {
                "repo_path": rel_path,
                "gallery_path": str(copied_path),
                "kind": asset_kind(source_path),
                "suffix": source_path.suffix.lower().lstrip("."),
                "size_bytes": source_path.stat().st_size,
                "semantic_tags": semantic_tags(rel_path),
                "matches_task_asset_pattern": matches_any(rel_path, patterns),
            }
        )

    if not items and patterns:
        source_candidates = [
            (relative(path, project_root), path)
            for path in all_source_files(project_root)
            if matches_any(relative(path, project_root), patterns)
        ][:max_assets]
        for index, (rel_path, source_path) in enumerate(source_candidates):
            copied_name = safe_gallery_name(index, rel_path)
            copied_path = asset_dir / copied_name
            shutil.copy2(source_path, copied_path)
            items.append(
                {
                    "repo_path": rel_path,
                    "gallery_path": str(copied_path),
                    "kind": "runtime_source",
                    "suffix": source_path.suffix.lower().lstrip("."),
                    "size_bytes": source_path.stat().st_size,
                    "semantic_tags": sorted(set(semantic_tags(rel_path) + (["canvas"] if task_is_canvas_like(tasks, repo_id) else ["source"]))),
                    "matches_task_asset_pattern": True,
                }
            )

    counts = Counter(item["kind"] for item in items)
    gallery = {
        "repo_id": repo_id,
        "passed": bool(items),
        "errors": [] if items else ["no gallery assets selected"],
        "project_root": str(project_root),
        "asset_patterns": patterns,
        "total_selected": len(items),
        "kind_counts": dict(sorted(counts.items())),
        "items": items,
    }
    write_json(gallery_dir / "gallery.json", gallery)
    (gallery_dir / "README.md").write_text(gallery_markdown(gallery), encoding="utf-8")
    return gallery


def gallery_markdown(gallery: dict[str, Any]) -> str:
    rows = []
    for item in gallery.get("items", []):
        path = item["gallery_path"]
        repo_path = item["repo_path"]
        tags = ", ".join(item.get("semantic_tags", [])) or "-"
        if item["kind"] == "preview_image":
            rows.append(f"### `{repo_path}`\n\n![{repo_path}]({path})\n\nTags: {tags}\n")
        else:
            rows.append(f"- `{repo_path}` -> `{path}` ({item['kind']}, tags: {tags})")
    body = "\n".join(rows) if rows else "No assets selected."
    return f"""# Asset Gallery: {gallery['repo_id']}

Selected assets are copied from the extracted repo workspace for local review.

```json
{json.dumps(gallery.get('kind_counts', {}), indent=2, sort_keys=True)}
```

{body}
"""


def build_asset_galleries(
    tasks_path: Path,
    workspace_root: Path,
    output_root: Path,
    max_assets: int,
) -> dict[str, Any]:
    tasks = list(read_jsonl(tasks_path))
    repo_ids = sorted({task["repo_id"] for task in tasks})
    items = [
        build_gallery_for_repo(repo_id, tasks, workspace_root, output_root, max_assets)
        for repo_id in repo_ids
    ]
    summary = {
        "tasks_path": str(tasks_path),
        "workspace_root": str(workspace_root),
        "output_root": str(output_root),
        "max_assets": max_assets,
        "total": len(items),
        "passed": sum(1 for item in items if item.get("passed")),
        "failed": sum(1 for item in items if not item.get("passed")),
        "items": [
            {
                "repo_id": item["repo_id"],
                "passed": item.get("passed", False),
                "total_selected": item.get("total_selected", 0),
                "kind_counts": item.get("kind_counts", {}),
            }
            for item in items
        ],
    }
    write_json(output_root / "manifest.json", summary)
    return summary


def add_asset_gallery_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tasks", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.jsonl")
    parser.add_argument("--workspace-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "workspaces")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "asset_galleries" / "dev_seed")
    parser.add_argument("--max-assets", type=int, default=32)


def run_asset_gallery_from_args(args: argparse.Namespace) -> None:
    summary = build_asset_galleries(args.tasks, args.workspace_root, args.output_root, args.max_assets)
    print(f"built asset galleries for {summary['total']} repos: {summary['passed']} passed, {summary['failed']} failed")
