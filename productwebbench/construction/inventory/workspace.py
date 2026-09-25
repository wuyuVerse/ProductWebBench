from __future__ import annotations

import argparse
import json
import shutil
import zipfile
from pathlib import Path

from ...core.config import DEFAULT_OUTPUT_ROOT, PROJECT_ROOT
from ...core.io_utils import ensure_dir, read_jsonl, write_json


WORKSPACE_ROOT = DEFAULT_OUTPUT_ROOT / "workspaces"
IGNORED_PROJECT_ROOT_PARTS = {
    ".git",
    ".next",
    ".nuxt",
    "build",
    "dist",
    "node_modules",
    "out",
    "vendor",
    "vendors",
}


def resolve_zip_path(zip_path: str) -> Path:
    path = Path(zip_path)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def safe_extract(zip_path: Path, destination: Path) -> None:
    ensure_dir(destination)
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            target = destination / member.filename
            resolved = target.resolve()
            if not str(resolved).startswith(str(destination.resolve())):
                raise ValueError(f"unsafe zip member path: {member.filename}")
        zf.extractall(destination)


def package_root_score(package_json: Path, extracted_dir: Path) -> tuple[int, int, str]:
    root = package_json.parent
    package = read_package_json(root)
    scripts = package.get("scripts", {}) if isinstance(package.get("scripts", {}), dict) else {}
    dependencies = {}
    for key in ("dependencies", "devDependencies"):
        value = package.get(key, {})
        if isinstance(value, dict):
            dependencies.update(value)

    score = 0
    if scripts.get("dev"):
        score += 20
    if scripts.get("build"):
        score += 10
    if "hugo" in " ".join(str(value) for value in scripts.values()).lower():
        score += 28
    for dirname, points in {
        "pages": 16,
        "app": 14,
        "src": 10,
        "content": 12,
        "components": 10,
        "layouts": 8,
        "assets": 6,
        "static": 5,
        "public": 4,
        "resources": 2,
    }.items():
        if (root / dirname).exists():
            score += points
    for dep, points in {
        "next": 18,
        "nuxt": 18,
        "vite": 12,
        "react": 8,
        "vue": 8,
        "svelte": 8,
        "astro": 10,
        "gatsby": 10,
        "laravel-vite-plugin": -8,
    }.items():
        if dep in dependencies:
            score += points
    if "laravel" in root.as_posix().lower() and "nuxt" not in package.get("name", "").lower():
        score -= 8
    depth_penalty = len(root.relative_to(extracted_dir).parts)
    return score, -depth_penalty, root.as_posix()


def has_hugo_config(root: Path) -> bool:
    return any(
        (root / name).exists()
        for name in (
            "hugo.toml",
            "hugo.yaml",
            "hugo.yml",
            "hugo.json",
            "config.toml",
            "config.yaml",
            "config.yml",
            "config.json",
        )
    ) or any((root / "config" / "_default" / name).exists() for name in ("hugo.toml", "hugo.yaml", "hugo.yml", "config.toml", "config.yaml", "config.yml"))


def is_hugo_theme_root(root: Path) -> bool:
    example_site = root / "exampleSite"
    return (
        (root / "theme.toml").exists()
        and (root / "layouts").exists()
        and example_site.exists()
        and has_hugo_config(example_site)
        and (example_site / "content").exists()
    )


def hugo_root_score(config_path: Path, extracted_dir: Path) -> tuple[int, int, str]:
    root = config_path.parent
    score = 0
    if (root / "content").exists():
        score += 30
    if (root / "layouts").exists():
        score += 14
    if (root / "assets").exists():
        score += 10
    if (root / "static").exists():
        score += 8
    if (root / "go.mod").exists():
        score += 8
    if "examplesite" in root.as_posix().lower():
        score += 10
    depth_penalty = len(root.relative_to(extracted_dir).parts)
    return score, -depth_penalty, root.as_posix()


def find_project_root(extracted_dir: Path) -> Path:
    children = [child for child in extracted_dir.iterdir() if child.is_dir()]
    if len(children) == 1:
        child = children[0]
        if (child / "docs" / "index.html").exists() and not (child / "index.html").exists():
            return child

    package_jsons = [
        path
        for path in extracted_dir.rglob("package.json")
        if not any(part in IGNORED_PROJECT_ROOT_PARTS for part in path.relative_to(extracted_dir).parts)
    ]
    package_jsons = sorted(package_jsons, key=lambda p: package_root_score(p, extracted_dir), reverse=True)
    if package_jsons:
        return package_jsons[0].parent

    hugo_configs = [
        path
        for pattern in ("hugo.*", "config.*", "config/_default/*")
        for path in extracted_dir.rglob(pattern)
        if path.is_file()
        and path.suffix.lower() in {".toml", ".yaml", ".yml", ".json"}
        and not any(part in IGNORED_PROJECT_ROOT_PARTS for part in path.relative_to(extracted_dir).parts)
        and has_hugo_config(path.parent)
        and (path.parent / "content").exists()
    ]
    hugo_configs = sorted(hugo_configs, key=lambda p: hugo_root_score(p, extracted_dir), reverse=True)
    if hugo_configs:
        root = hugo_configs[0].parent
        if root.name.lower() == "examplesite" and is_hugo_theme_root(root.parent):
            return root.parent
        return root

    index_files = [
        path
        for path in extracted_dir.rglob("index.html")
        if not any(part in IGNORED_PROJECT_ROOT_PARTS for part in path.relative_to(extracted_dir).parts)
    ]
    for index_path in index_files:
        parent = index_path.parent
        code_docs_root = parent.parent
        if parent.name == "mode" and (code_docs_root / "doc").exists() and (code_docs_root / "lib").exists():
            return code_docs_root
    index_files = sorted(index_files, key=lambda p: len(p.relative_to(extracted_dir).parts))
    if index_files:
        return index_files[0].parent
    if len(children) == 1:
        return children[0]
    return extracted_dir


def read_package_json(project_root: Path) -> dict:
    package_path = project_root / "package.json"
    if not package_path.exists():
        return {}
    try:
        return json.loads(package_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def resolve_metadata_path(path_value: str | None) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_existing_workspace_metadata(destination: Path, repo_id: str, zip_path: Path) -> dict | None:
    metadata_path = destination / ".sitecontinuum_workspace.json"
    if not metadata_path.exists():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    project_root = resolve_metadata_path(metadata.get("project_root"))
    if project_root is None or not project_root.exists():
        return None
    package_json = read_package_json(project_root)
    metadata.update(
        {
            "repo_id": repo_id,
            "zip_path": str(zip_path),
            "workspace": str(destination),
            "project_root": str(project_root),
            "has_package_json": bool(package_json),
            "package_name": package_json.get("name"),
            "scripts": package_json.get("scripts", {}),
        }
    )
    write_json(metadata_path, metadata)
    return metadata


def materialize_workspace(repo_record: dict, workspace_root: Path, clean: bool = False) -> dict:
    repo_id = repo_record["repo_id"]
    zip_path = resolve_zip_path(repo_record["zip_path"])
    destination = workspace_root / repo_id
    if clean and destination.exists():
        shutil.rmtree(destination)
    if not destination.exists():
        safe_extract(zip_path, destination)
    existing_metadata = load_existing_workspace_metadata(destination, repo_id, zip_path)
    if existing_metadata is not None:
        return existing_metadata
    project_root = find_project_root(destination)
    package_json = read_package_json(project_root)
    metadata = {
        "repo_id": repo_id,
        "zip_path": str(zip_path),
        "workspace": str(destination),
        "project_root": str(project_root),
        "has_package_json": bool(package_json),
        "package_name": package_json.get("name"),
        "scripts": package_json.get("scripts", {}),
    }
    write_json(destination / ".sitecontinuum_workspace.json", metadata)
    return metadata


def load_repo_record(manifest_path: Path, repo_id: str) -> dict:
    for record in read_jsonl(manifest_path):
        if record.get("repo_id") == repo_id:
            return record
    raise KeyError(f"repo_id not found in manifest: {repo_id}")


def add_extract_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("repo_id")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_OUTPUT_ROOT / "manifest" / "repos.jsonl")
    parser.add_argument("--workspace-root", type=Path, default=WORKSPACE_ROOT)
    parser.add_argument("--clean", action="store_true")


def run_extract_from_args(args: argparse.Namespace) -> None:
    record = load_repo_record(args.manifest, args.repo_id)
    metadata = materialize_workspace(record, args.workspace_root, clean=args.clean)
    print(json.dumps(metadata, indent=2, sort_keys=True))
