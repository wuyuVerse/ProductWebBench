from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from ...core.config import DEFAULT_OUTPUT_ROOT, workspace_meta_path
from ...core.io_utils import ensure_dir, read_jsonl, write_json


EXCLUDED_PARTS = {"node_modules", ".next", "dist", "build", "out", ".git"}
SOURCE_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".mdx", ".css", ".scss", ".html"}
ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif", ".ico", ".mp4", ".webm", ".ogv", ".glb", ".gltf"}
ROUTE_HINTS = ("page.", "layout.", "route.", "index.html", "index.mdx")
COMPONENT_HINTS = ("components/", "component/", "src/components/", "app/components/")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def workspace_meta(workspace_root: Path, repo_id: str) -> dict[str, Any] | None:
    path = workspace_meta_path(workspace_root / repo_id)
    if not path.exists():
        return None
    return load_json(path)


def is_excluded(path: Path) -> bool:
    return any(part in EXCLUDED_PARTS for part in path.parts)


def repo_files(project_root: Path) -> list[Path]:
    paths: list[Path] = []
    for root, dirs, files in os.walk(project_root):
        dirs[:] = [name for name in dirs if name not in EXCLUDED_PARTS]
        root_path = Path(root)
        paths.extend(root_path / name for name in files)
    return sorted(paths)


def relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def top_files(paths: list[str], limit: int) -> list[str]:
    return sorted(paths, key=lambda item: (item.count("/"), item))[:limit]


def read_text_sample(path: Path, limit: int = 1200) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def keyword_hotspots(project_root: Path, files: list[Path], keywords: list[str]) -> list[dict[str, Any]]:
    text_files = [path for path in files if path.suffix.lower() in SOURCE_SUFFIXES]
    hotspots = []
    for keyword in keywords:
        matches = []
        lowered = keyword.lower()
        for path in text_files:
            sample = read_text_sample(path, limit=20000)
            if lowered in sample.lower():
                matches.append(relative(path, project_root))
            if len(matches) >= 8:
                break
        hotspots.append({"keyword": keyword, "matches": matches, "match_count": len(matches)})
    return hotspots


def infer_keywords(repo_id: str) -> list[str]:
    lowered = repo_id.lower()
    if "ecommerce" in lowered or "merce" in lowered:
        return ["product", "quickview", "cart", "price", "rating", "category"]
    if "dashboard" in lowered or "admin" in lowered:
        return ["dashboard", "customer", "user", "table", "card", "chart"]
    if "portfolio" in lowered and "folio" not in lowered:
        return ["project", "skill", "portfolio", "experience", "contact", "lottie"]
    if "dev-docs" in lowered or "docs" in lowered:
        return ["docs", "tutorial", "resources", "CustomCards", "RoundedLinkButton", "Zapier"]
    if "folio" in lowered:
        return ["World", "ProjectsSection", "Resources", "controls", "mobile", "ProjectBoard"]
    return ["section", "component", "asset", "page", "button", "card"]


def summarize_design_anchor(anchor: dict[str, Any] | None) -> dict[str, Any]:
    if not anchor:
        return {"available": False}
    tokens = anchor.get("tokens", {})
    return {
        "available": bool(anchor.get("has_design_anchors")),
        "states": anchor.get("state_ids", []),
        "background_colors": tokens.get("background_colors", [])[:5],
        "text_colors": tokens.get("text_colors", [])[:5],
        "font_families": tokens.get("font_families", [])[:4],
        "font_sizes": tokens.get("font_sizes", [])[:5],
        "radii": tokens.get("radii", [])[:5],
        "sample_counts": {name: len(value) for name, value in anchor.get("samples", {}).items()},
    }


def build_dossier(
    repo_id: str,
    workspace_root: Path,
    design_anchor: dict[str, Any] | None,
    output_root: Path,
) -> dict[str, Any]:
    meta = workspace_meta(workspace_root, repo_id)
    if meta is None:
        dossier = {"repo_id": repo_id, "passed": False, "errors": ["missing workspace metadata"]}
        write_json(ensure_dir(output_root / repo_id) / "dossier.json", dossier)
        return dossier

    project_root = Path(meta["project_root"])
    files = repo_files(project_root)
    rel_files = [relative(path, project_root) for path in files]
    source_files = [item for item in rel_files if Path(item).suffix.lower() in SOURCE_SUFFIXES]
    asset_files = [item for item in rel_files if Path(item).suffix.lower() in ASSET_SUFFIXES]
    route_files = [
        item
        for item in source_files
        if any(hint in Path(item).name for hint in ROUTE_HINTS) or item.startswith(("app/", "pages/", "src/app/", "src/pages/"))
    ]
    component_files = [item for item in source_files if any(hint in item for hint in COMPONENT_HINTS)]
    asset_counts = Counter(Path(item).suffix.lower().lstrip(".") for item in asset_files)
    keywords = infer_keywords(repo_id)

    dossier = {
        "repo_id": repo_id,
        "passed": True,
        "workspace": {
            "project_root": meta.get("project_root"),
            "zip_path": meta.get("zip_path"),
            "package_name": meta.get("package_name"),
            "scripts": meta.get("scripts", {}),
        },
        "file_counts": {
            "total": len(rel_files),
            "source": len(source_files),
            "assets": len(asset_files),
            "routes": len(route_files),
            "components": len(component_files),
        },
        "routes": top_files(route_files, 30),
        "components": top_files(component_files, 40),
        "assets": top_files(asset_files, 50),
        "asset_counts": dict(sorted(asset_counts.items())),
        "keyword_hotspots": keyword_hotspots(project_root, files, keywords),
        "design_anchor_summary": summarize_design_anchor(design_anchor),
    }

    dossier_dir = ensure_dir(output_root / repo_id)
    write_json(dossier_dir / "dossier.json", dossier)
    (dossier_dir / "README.md").write_text(dossier_markdown(dossier), encoding="utf-8")
    return dossier


def list_markdown(items: list[str], limit: int | None = None) -> str:
    selected = items if limit is None else items[:limit]
    if not selected:
        return "- None"
    return "\n".join(f"- `{item}`" for item in selected)


def hotspot_markdown(hotspots: list[dict[str, Any]]) -> str:
    chunks = []
    for item in hotspots:
        matches = ", ".join(f"`{match}`" for match in item.get("matches", [])[:4]) or "none"
        chunks.append(f"- `{item['keyword']}`: {matches}")
    return "\n".join(chunks) if chunks else "- None"


def token_markdown(items: list[dict[str, Any]]) -> str:
    if not items:
        return "- None"
    return "\n".join(f"- `{item['value']}` ({item['count']})" for item in items)


def dossier_markdown(dossier: dict[str, Any]) -> str:
    workspace = dossier.get("workspace", {})
    design = dossier.get("design_anchor_summary", {})
    return f"""# Repo Dossier: {dossier['repo_id']}

## Workspace

- Project root: `{workspace.get('project_root')}`
- Zip source: `{workspace.get('zip_path')}`
- Package name: `{workspace.get('package_name')}`

## File Counts

```json
{json.dumps(dossier.get('file_counts', {}), indent=2, sort_keys=True)}
```

## Route/Page Candidates

{list_markdown(dossier.get('routes', []), 30)}

## Component Candidates

{list_markdown(dossier.get('components', []), 40)}

## Asset Candidates

{list_markdown(dossier.get('assets', []), 50)}

## Keyword Hotspots

{hotspot_markdown(dossier.get('keyword_hotspots', []))}

## Design Anchor Summary

Background colors:

{token_markdown(design.get('background_colors', []))}

Text colors:

{token_markdown(design.get('text_colors', []))}

Font families:

{token_markdown(design.get('font_families', []))}

Radii:

{token_markdown(design.get('radii', []))}
"""


def build_dossiers(
    tasks_path: Path,
    workspace_root: Path,
    design_anchors_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    tasks = list(read_jsonl(tasks_path))
    design_anchors = {
        item["repo_id"]: item
        for item in load_json(design_anchors_path).get("items", [])
    } if design_anchors_path.exists() else {}
    seen: set[str] = set()
    items = []
    for task in tasks:
        repo_id = task["repo_id"]
        if repo_id in seen:
            continue
        seen.add(repo_id)
        items.append(build_dossier(repo_id, workspace_root, design_anchors.get(repo_id), output_root))

    summary = {
        "tasks_path": str(tasks_path),
        "workspace_root": str(workspace_root),
        "design_anchors_path": str(design_anchors_path),
        "output_root": str(output_root),
        "total": len(items),
        "passed": sum(1 for item in items if item.get("passed")),
        "failed": sum(1 for item in items if not item.get("passed")),
        "items": [{"repo_id": item["repo_id"], "passed": item.get("passed", False)} for item in items],
    }
    write_json(output_root / "manifest.json", summary)
    return summary


def add_dossier_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tasks", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.jsonl")
    parser.add_argument("--workspace-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "workspaces")
    parser.add_argument("--design-anchors", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.design_anchors.json")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "dossiers" / "dev_seed")


def run_dossier_from_args(args: argparse.Namespace) -> None:
    summary = build_dossiers(args.tasks, args.workspace_root, args.design_anchors, args.output_root)
    print(f"built dossiers for {summary['total']} repos: {summary['passed']} passed, {summary['failed']} failed")
