from __future__ import annotations

import argparse
import json
import zipfile
from collections import Counter
from pathlib import Path
from typing import Iterable

from ...core.config import DEFAULT_REPO_ROOT, MANIFEST_DIR
from ...core.io_utils import write_json, write_jsonl
from ...core.models import MEDIA_EXTENSIONS, RepoRecord, repo_id_from_zip


LOCKFILES = {
    "package-lock.json": "npm",
    "pnpm-lock.yaml": "pnpm",
    "yarn.lock": "yarn",
    "bun.lockb": "bun",
    "bun.lock": "bun",
}


FRAMEWORK_MARKERS = {
    "next": ("next.config",),
    "astro": ("astro.config", ".astro/"),
    "gatsby": ("gatsby-config", "gatsby-node"),
    "vite": ("vite.config",),
    "nuxt": ("nuxt.config",),
    "svelte": ("svelte.config",),
    "three": ("three", "three.js", "webgl", "canvas"),
}


UI_PATH_MARKERS = (
    "/src/",
    "/app/",
    "/pages/",
    "/components/",
    "/public/",
    "/assets/",
    "/styles/",
    "/css/",
    "/index.html",
)


ROUTE_MARKERS = (
    "/app/",
    "/pages/",
    "/routes/",
    "/src/pages/",
    "/src/app/",
    "/index.html",
)


TEST_MARKERS = (
    ".test.",
    ".spec.",
    "/__tests__/",
    "/tests/",
    "playwright.config",
    "cypress.config",
)


def find_zip_files(repo_root: Path, limit: int | None = None) -> list[Path]:
    paths = sorted(repo_root.glob("*/*.zip"))
    if limit is not None:
        return paths[:limit]
    return paths


def detect_package_manager(names: Iterable[str]) -> tuple[str, bool]:
    basenames = {Path(name).name for name in names}
    for lockfile, manager in LOCKFILES.items():
        if lockfile in basenames:
            return manager, True
    return "npm", False


def package_dependencies(package_json: dict) -> set[str]:
    deps: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        value = package_json.get(key, {})
        if isinstance(value, dict):
            deps.update(value.keys())
    return deps


def detect_framework(names: list[str], package_json: dict, package_json_text: str | None) -> str:
    haystack = "\n".join(names[:5000]).lower()
    if package_json_text:
        haystack += "\n" + package_json_text.lower()

    deps = package_dependencies(package_json) if isinstance(package_json, dict) else set()
    for framework, dep in (
        ("next", "next"),
        ("astro", "astro"),
        ("gatsby", "gatsby"),
        ("nuxt", "nuxt"),
        ("svelte", "svelte"),
        ("gridsome", "gridsome"),
        ("vite", "vite"),
    ):
        if dep in deps:
            return framework

    # Prefer explicit framework markers before generic React/static signals.
    for framework in ("next", "astro", "gatsby", "nuxt", "svelte"):
        if any(marker in haystack for marker in FRAMEWORK_MARKERS[framework]):
            return framework
    if "vite" in haystack:
        return "vite"
    if "three" in haystack or "webgl" in haystack:
        return "three"
    if "react" in haystack:
        return "react"
    if any(name.endswith("/index.html") or name.endswith("index.html") for name in names):
        return "static"
    return "other"


def command_for(framework: str, package_manager: str, scripts: dict[str, str]) -> tuple[str, str, str]:
    runner = {
        "npm": "npm run",
        "pnpm": "pnpm",
        "yarn": "yarn",
        "bun": "bun run",
    }.get(package_manager, "npm run")

    install = {
        "npm": "npm ci || npm install",
        "pnpm": "pnpm install --frozen-lockfile || pnpm install",
        "yarn": "(corepack enable && corepack yarn install --frozen-lockfile) || npm install",
        "bun": "bun install",
    }.get(package_manager, "npm install")

    build_script = "build" if "build" in scripts else ""
    dev_script = "dev" if "dev" in scripts else "start" if "start" in scripts else ""
    if not dev_script and framework == "static":
        return "", "", "python3 -m http.server 3000"

    build = f"{runner} {build_script}" if build_script else ""
    dev = f"{runner} {dev_script}" if dev_script else ""
    return install, build, dev


def read_package_json(zip_file: zipfile.ZipFile, names: list[str]) -> tuple[bool, dict, str | None]:
    package_paths = [name for name in names if name.endswith("/package.json") or name == "package.json"]
    if not package_paths:
        return False, {}, None
    # Prefer root-most package.json.
    package_path = sorted(package_paths, key=lambda p: p.count("/"))[0]
    try:
        text = zip_file.read(package_path).decode("utf-8", errors="replace")
        return True, json.loads(text), text
    except Exception:
        return True, {}, None


def summarize_zip(zip_path: Path, repo_root: Path) -> RepoRecord:
    repo_id, owner, name, commit = repo_id_from_zip(zip_path)
    notes: list[str] = []
    try:
        with zipfile.ZipFile(zip_path) as zf:
            infos = zf.infolist()
            names = [info.filename for info in infos if not info.is_dir()]
            package_present, package_json, package_text = read_package_json(zf, names)
    except zipfile.BadZipFile:
        return RepoRecord(
            repo_id=repo_id,
            owner=owner,
            name=name,
            commit=commit,
            zip_path=str(zip_path),
            date_bucket=zip_path.parent.name,
            framework="invalid_zip",
            package_manager="unknown",
            has_package_json=False,
            has_lockfile=False,
            has_routes=False,
            has_tests=False,
            has_media=False,
            media_count={},
            file_count=0,
            uncompressed_size=0,
            likely_ui_repo=False,
            install_command="",
            build_command="",
            dev_command="",
            notes=["bad_zip"],
        )

    media_counter: Counter[str] = Counter()
    for filename in names:
        ext = Path(filename).suffix.lower()
        if ext in MEDIA_EXTENSIONS:
            media_counter[ext.lstrip(".")] += 1

    package_manager, has_lockfile = detect_package_manager(names)
    scripts = package_json.get("scripts", {}) if isinstance(package_json, dict) else {}
    framework = detect_framework(names, package_json, package_text)
    install, build, dev = command_for(framework, package_manager, scripts)
    lowered = [f"/{name.lower()}" for name in names[:20000]]

    has_routes = any(any(marker in path for marker in ROUTE_MARKERS) for path in lowered)
    has_tests = any(any(marker in path for marker in TEST_MARKERS) for path in lowered)
    has_media = bool(media_counter)
    likely_ui_repo = (
        framework in {"next", "astro", "gatsby", "gridsome", "vite", "nuxt", "svelte", "react", "static", "three"}
        and has_routes
        and has_media
        and any(any(marker in path for marker in UI_PATH_MARKERS) for path in lowered)
    )
    if not package_present:
        notes.append("no_package_json")
    if not has_routes:
        notes.append("no_route_marker")
    if not has_media:
        notes.append("no_media")

    return RepoRecord(
        repo_id=repo_id,
        owner=owner,
        name=name,
        commit=commit,
        zip_path=str(zip_path.relative_to(repo_root.parent.parent) if zip_path.is_absolute() else zip_path),
        date_bucket=zip_path.parent.name,
        framework=framework,
        package_manager=package_manager,
        has_package_json=package_present,
        has_lockfile=has_lockfile,
        has_routes=has_routes,
        has_tests=has_tests,
        has_media=has_media,
        media_count=dict(sorted(media_counter.items())),
        file_count=len(names),
        uncompressed_size=sum(info.file_size for info in infos),
        likely_ui_repo=likely_ui_repo,
        install_command=install,
        build_command=build,
        dev_command=dev,
        notes=notes,
    )


def record_preference(record: RepoRecord) -> tuple[int, int, str, int, int]:
    return (
        0 if record.framework == "invalid_zip" else 1,
        1 if record.likely_ui_repo else 0,
        record.date_bucket,
        record.file_count,
        record.uncompressed_size,
    )


def dedupe_by_repo_id(records: list[RepoRecord]) -> list[RepoRecord]:
    by_repo_id: dict[str, RepoRecord] = {}
    for record in records:
        existing = by_repo_id.get(record.repo_id)
        if existing is None or record_preference(record) > record_preference(existing):
            by_repo_id[record.repo_id] = record
    return list(by_repo_id.values())


def build_catalog(
    repo_root: Path,
    output: Path,
    limit: int | None = None,
    keep_duplicate_repo_ids: bool = False,
) -> list[RepoRecord]:
    zip_paths = find_zip_files(repo_root, limit=limit)
    raw_records = [summarize_zip(path, repo_root) for path in zip_paths]
    records = raw_records if keep_duplicate_repo_ids else dedupe_by_repo_id(raw_records)
    write_jsonl(output, records)
    summary = {
        "repo_root": str(repo_root),
        "output": str(output),
        "source_total": len(raw_records),
        "source_unique_repo_ids": len({item.repo_id for item in raw_records}),
        "source_duplicate_repo_ids": len(raw_records) - len({item.repo_id for item in raw_records}),
        "deduped_by_repo_id": not keep_duplicate_repo_ids,
        "total": len(records),
        "unique_repo_ids": len({item.repo_id for item in records}),
        "duplicate_repo_ids": len(records) - len({item.repo_id for item in records}),
        "likely_ui_repo": sum(1 for item in records if item.likely_ui_repo),
        "has_package_json": sum(1 for item in records if item.has_package_json),
        "has_media": sum(1 for item in records if item.has_media),
        "frameworks": Counter(item.framework for item in records),
    }
    write_json(output.with_suffix(".summary.json"), summary)
    return records


def add_catalog_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--output", type=Path, default=MANIFEST_DIR / "repos.jsonl")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--keep-duplicate-repo-ids",
        action="store_true",
        help="Preserve duplicate repo_id rows instead of keeping one preferred record per repo.",
    )


def run_from_args(args: argparse.Namespace) -> None:
    records = build_catalog(
        args.repo_root,
        args.output,
        limit=args.limit,
        keep_duplicate_repo_ids=args.keep_duplicate_repo_ids,
    )
    print(f"wrote {len(records)} repo records to {args.output}")
    print(f"likely UI repos: {sum(1 for item in records if item.likely_ui_repo)}")
