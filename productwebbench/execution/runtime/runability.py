from __future__ import annotations

import argparse
import json
import os
import re
import signal
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict
from pathlib import Path

from ...core.config import DEFAULT_OUTPUT_ROOT
from ...core.io_utils import ensure_dir, write_json
from ...core.process_utils import run_logged_command
from ...construction.inventory.workspace import WORKSPACE_ROOT, load_repo_record, materialize_workspace


RUNS_ROOT = DEFAULT_OUTPUT_ROOT / "runability"
STATIC_SERVER_SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "static_server.py"


def find_free_port(start: int = 3100, end: int = 3999) -> int:
    start = int(os.environ.get("PRODUCTWEBBENCH_PORT_START", start))
    end = int(os.environ.get("PRODUCTWEBBENCH_PORT_END", end))
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"no free port in range {start}-{end}")


def is_port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def wait_for_http(url: str, timeout_sec: int) -> tuple[bool, str]:
    deadline = time.time() + timeout_sec
    last_error = ""
    # Send an HTML Accept header so SPA dev servers (Vite) exercise their
    # history-fallback middleware for "/" instead of returning a bare 404 that a
    # default urllib request would receive.
    def _request(u: str) -> urllib.request.Request:
        return urllib.request.Request(u, headers={"Accept": "text/html,*/*"})
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(_request(url), timeout=5) as response:
                if 200 <= response.status < 400:
                    return True, f"HTTP {response.status}"
        except urllib.error.HTTPError as exc:
            # Any HTTP status (even 404) proves the dev server is listening and
            # responding — readiness is about the server being up, not about "/"
            # having content. Vite SPAs 404 "/" for non-HTML probes; capture then
            # navigates the real state URLs. Treat any HTTP response as ready.
            return True, f"HTTP {exc.code}"
        except urllib.error.URLError as exc:
            last_error = str(exc)
        except TimeoutError as exc:
            last_error = str(exc)
        except ConnectionResetError as exc:
            last_error = str(exc)
        time.sleep(1)
    return False, last_error


def command_with_port(command: str, port: int, framework: str | None = None) -> str:
    if not command:
        return ""
    if command.startswith("yarn "):
        command = "corepack " + command
    if command.startswith("pnpm "):
        command = "corepack " + command
    if "next dev" in command or "next start" in command:
        return f"{command} --hostname 127.0.0.1 --port {port}"
    if command in {"npm run dev", "npm run start", "pnpm dev", "corepack pnpm dev", "yarn dev", "corepack yarn dev", "bun run dev"}:
        # Generic package scripts need framework-specific forwarded flags.
        # Next accepts --hostname; Vite/Astro accept --host.
        if framework == "next":
            if command in {"npm run dev", "npm run start"}:
                return f"{command} -- --hostname 127.0.0.1 --port {port}"
            if command in {"pnpm dev", "corepack pnpm dev", "bun run dev"}:
                return f"{command} --hostname 127.0.0.1 --port {port}"
            return f"{command} --hostname 127.0.0.1 --port {port}"
        if command in {"pnpm dev", "corepack pnpm dev", "yarn dev", "corepack yarn dev", "bun run dev"}:
            return f"{command} --host 127.0.0.1 --port {port}"
        return f"{command} -- --host 127.0.0.1 --port {port}"
    if "vite" in command:
        return f"{command} --host 127.0.0.1 --port {port}"
    if "astro" in command:
        return f"{command} --host 127.0.0.1 --port {port}"
    if "nuxt" in command:
        return f"{command} --host 127.0.0.1 --port {port}"
    if "gatsby" in command:
        return f"{command} --host 127.0.0.1 --port {port}"
    if command.startswith("php artisan serve"):
        return f"{command} --host=127.0.0.1 --port={port}"
    if command.startswith("hugo server"):
        return f"{command} --bind 127.0.0.1 -p {port} --baseURL http://127.0.0.1:{port}/"
    if command.startswith("python3 -m http.server"):
        suffix = command.removeprefix("python3 -m http.server").strip()
        parts = suffix.split()
        if parts and parts[0].isdigit():
            suffix = " ".join(parts[1:])
        return f"python3 -m http.server {port} --bind 127.0.0.1 {suffix}".strip()
    if "static_server.py" in command:
        return f"{command} {port} --bind 127.0.0.1"
    return command


def is_laravel_project(project_root: Path) -> bool:
    return (project_root / "artisan").exists() and (project_root / "routes" / "web.php").exists()


def is_hugo_project(project_root: Path) -> bool:
    has_hugo_config = any(
        (project_root / name).exists()
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
    ) or any(
        (project_root / "config" / "_default" / name).exists()
        for name in ("hugo.toml", "hugo.yaml", "hugo.yml", "config.toml", "config.yaml", "config.yml")
    )
    has_hugo_layout = (project_root / "layouts").exists() and (project_root / "content").exists()
    imports_theme_layout = (project_root / "go.mod").exists() and (project_root / "content").exists()
    return is_hugo_theme_root(project_root) or (
        has_hugo_config and (has_hugo_layout or imports_theme_layout or is_hugo_theme_example_site(project_root) or has_local_hugo_theme(project_root))
    )


def has_local_hugo_theme(project_root: Path) -> bool:
    import tomllib
    config = project_root / 'hugo.toml'
    if not config.is_file() or not (project_root / 'content').is_dir():
        return False
    try:
        theme = tomllib.loads(config.read_text()).get('theme')
        names = [theme] if isinstance(theme, str) else theme
        if not isinstance(names, list) or not names:
            return False
        root = project_root.resolve()
        return all(isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9_.-]+', name)
                   and name not in ('.', '..')
                   and (project_root / 'themes' / name).resolve().is_relative_to(root)
                   and (project_root / 'themes' / name / 'layouts').is_dir() for name in names)
    except (OSError, ValueError):
        return False


def is_hugo_theme_root(project_root: Path) -> bool:
    example_site = project_root / "exampleSite"
    return (
        (project_root / "theme.toml").exists()
        and (project_root / "layouts").exists()
        and example_site.exists()
        and (example_site / "content").exists()
        and any(
            (example_site / name).exists()
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
        )
    )


def is_hugo_theme_example_site(project_root: Path) -> bool:
    """Detect Hugo theme repos where exampleSite uses layouts/assets from the parent theme."""
    has_hugo_config = any(
        (project_root / name).exists()
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
    )
    parent = project_root.parent
    return (
        has_hugo_config
        and project_root.name.lower() == "examplesite"
        and (project_root / "content").exists()
        and (parent / "theme.toml").exists()
        and (parent / "layouts").exists()
    )


def looks_like_unbuilt_app_shell(index_path: Path) -> bool:
    try:
        text = index_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    has_app_mount = bool(re.search(r"""<div\b[^>]*\bid=["'](?:root|app)["'][^>]*>\s*</div>""", text))
    has_bundled_assets = bool(
        re.search(r"""<script[^>]+src=["'][^"']*(?:assets|_next/static|static/js)/[^"']+\.m?js["']""", text)
        or re.search(r"""<link[^>]+href=["'][^"']*(?:assets|_next/static|static/css)/[^"']+\.css["']""", text)
    )
    local_script_sources = re.findall(r"""<script[^>]+src=["']([^"']+)["']""", text)
    has_static_runtime_script = any(
        re.search(r"\.m?js(?:\?[^/]+)?$", src)
        and not src.startswith(("%PUBLIC_URL%", "/src/", "src/", "http://", "https://", "//"))
        for src in local_script_sources
    )
    has_source_module_script = any(
        src.startswith(("/src/", "src/")) or re.search(r"\.(?:ts|tsx|jsx)(?:\?[^/]+)?$", src)
        for src in local_script_sources
    )
    return (
        "%PUBLIC_URL%" in text
        or (has_app_mount and has_source_module_script)
        or (has_app_mount and not has_bundled_assets and not has_static_runtime_script)
    )


def looks_like_jekyll_source_page(index_path: Path, project_root: Path) -> bool:
    if not (project_root / "_config.yml").exists():
        return False
    try:
        text = index_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    has_front_matter = text.lstrip().startswith("---")
    has_liquid_tags = "{%" in text or "{{" in text
    return has_front_matter and has_liquid_tags


def static_site_output_dir(project_root: Path) -> Path | None:
    root_index = project_root / "index.html"
    if (
        root_index.exists()
        and not looks_like_unbuilt_app_shell(root_index)
        and not looks_like_jekyll_source_page(root_index, project_root)
    ):
        return project_root
    if (
        (project_root / "mode" / "index.html").exists()
        and (project_root / "doc" / "docs.css").exists()
        and (project_root / "lib").exists()
    ):
        return project_root
    if (project_root / "docs" / "index.html").exists():
        return project_root / "docs"
    for dirname in ("out", "dist", "build", "public", "_gh_pages", "static", "theme", "_site", "docs/.vitepress/dist", "_site/next"):
        candidate = project_root / dirname
        index_path = candidate / "index.html"
        if index_path.exists() and not looks_like_unbuilt_app_shell(index_path):
            return candidate
    return None


def built_static_output_dir(project_root: Path) -> Path | None:
    for dirname in ("out", "dist", "build", "_gh_pages", ".output/public", "docs/.vitepress/dist", "_site/next"):
        candidate = project_root / dirname
        index_path = candidate / "index.html"
        if index_path.exists() and not looks_like_unbuilt_app_shell(index_path):
            return candidate
    return None


def static_source_dir(project_root: Path) -> Path | None:
    candidate = project_root / "src"
    index_path = candidate / "index.html"
    if not index_path.exists() or looks_like_unbuilt_app_shell(index_path):
        return None
    has_local_static_assets = (candidate / "style.css").exists() or (candidate / "images").exists() or (candidate / "js").exists()
    return candidate if has_local_static_assets else None


def astro_base_path(project_root: Path) -> str | None:
    config_path = next(
        (project_root / name for name in ("astro.config.mjs", "astro.config.js", "astro.config.ts") if (project_root / name).exists()),
        None,
    )
    if config_path is None:
        return None
    try:
        text = config_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    match = re.search(r"\bbase\s*:\s*['\"]([^'\"]+)['\"]", text)
    if not match:
        return None
    base = "/" + match.group(1).strip().strip("/")
    return base if base != "/" else None


def jekyll_base_path(project_root: Path) -> str | None:
    config_path = project_root / "_config.yml"
    if not config_path.exists():
        return None
    try:
        text = config_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    match = re.search(r"(?m)^\s*baseurl\s*:\s*['\"]?([^'\"\n#]+)['\"]?\s*(?:#.*)?$", text)
    if not match:
        return None
    base = "/" + match.group(1).strip().strip("/")
    return base if base != "/" else None


def static_base_path(repo_record: dict, project_root: Path) -> str | None:
    if repo_record.get("framework") == "astro":
        return astro_base_path(project_root)
    if repo_record.get("framework") == "static":
        return jekyll_base_path(project_root)
    return None


def static_site_serve_dir(repo_record: dict, project_root: Path, static_dir: Path) -> Path:
    base = static_base_path(repo_record, project_root)
    if not base:
        return static_dir

    wrapper = project_root / ".productwebbench_static_base"
    if wrapper.exists():
        if wrapper.is_symlink() or wrapper.is_file():
            wrapper.unlink()
        else:
            shutil.rmtree(wrapper)
    target_parent = wrapper / base.strip("/")
    target_parent.parent.mkdir(parents=True, exist_ok=True)
    try:
        target_parent.symlink_to(static_dir.resolve(), target_is_directory=True)
    except OSError:
        shutil.copytree(static_dir, target_parent)
    return wrapper


def static_server_command(project_root: Path, serve_dir: Path) -> str:
    if os.environ.get('PWB_STATIC_ROUTING') == 'hugo-static-v1' and is_hugo_project(project_root):
        script = STATIC_SERVER_SCRIPT.with_name('hugo_static_server.py')
        return f"python3 {script} --directory {serve_dir.relative_to(project_root)}"
    return f"python3 {STATIC_SERVER_SCRIPT} --directory {serve_dir.relative_to(project_root)}"


def should_build_static_output_before_capture(repo_record: dict, project_root: Path) -> bool:
    if repo_record.get("framework") != "static" or static_site_output_dir(project_root) is not None:
        return False
    scripts = repo_record.get("scripts") or package_scripts(project_root)
    build_command = repo_record.get("build_command") or ("npm run build" if "build" in scripts else "")
    if not build_command:
        return False
    dev_or_build = " ".join(str(scripts.get(name, "")) for name in ("dev", "start", "build", "localhost"))
    return (project_root / "gulpfile.js").exists() or (project_root / "source" / "index.html").exists() or "browser-sync" in dev_or_build


def can_serve_without_node_install(repo_record: dict, project_root: Path, *, prefer_dev: bool = False) -> bool:
    """Return true for static templates whose checked-in HTML/CSS can be served directly."""
    if static_site_output_dir(project_root) is None:
        return False
    command = dev_command_for_project(repo_record, project_root, prefer_dev=prefer_dev)
    return "http.server" in command or "static_server.py" in command


def has_nested_static_pages(project_root: Path) -> bool:
    for index_path in project_root.glob("src/**/index.html"):
        if "node_modules" not in index_path.parts:
            return True
    return False


def upsert_env_value(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    line = f"{key}={value}"
    if pattern.search(text):
        return pattern.sub(line, text)
    return text.rstrip() + "\n" + line + "\n"


def prepare_laravel_environment(project_root: Path) -> None:
    env_path = project_root / ".env"
    if not env_path.exists() and (project_root / ".env.example").exists():
        env_path.write_text((project_root / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    if env_path.exists():
        text = env_path.read_text(encoding="utf-8")
        for key, value in {
            "APP_ENV": "local",
            "APP_DEBUG": "true",
            "APP_URL": "http://127.0.0.1",
            "DB_CONNECTION": "sqlite",
            "DB_DATABASE": str(project_root / "database" / "database.sqlite"),
            "SESSION_DRIVER": "file",
            "CACHE_STORE": "file",
            "QUEUE_CONNECTION": "sync",
        }.items():
            text = upsert_env_value(text, key, value)
        env_path.write_text(text, encoding="utf-8")

    database_dir = project_root / "database"
    database_dir.mkdir(parents=True, exist_ok=True)
    (database_dir / "database.sqlite").touch(exist_ok=True)


def prepare_node_environment(project_root: Path, port: int | None = None) -> None:
    """Seed local-only env values needed by common frontend templates at build time."""
    (project_root / ".data").mkdir(parents=True, exist_ok=True)
    env_path = project_root / ".env"
    example_path = project_root / ".env.example"
    if not env_path.exists() and example_path.exists():
        env_path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
    if not env_path.exists():
        return
    base_url = f"http://127.0.0.1:{port}" if port is not None else "http://127.0.0.1:3100"
    text = env_path.read_text(encoding="utf-8")
    for key, value in {
        "APP_URL": base_url,
        "BETTER_AUTH_URL": base_url,
        "NEXT_PUBLIC_APP_URL": base_url,
        "BETTER_AUTH_SECRET": "productwebbench-local-secret",
        "GOOGLE_CLIENT_ID": "productwebbench-local-client-id",
        "GOOGLE_CLIENT_SECRET": "productwebbench-local-client-secret",
        "DATABASE_URL": "postgresql://productwebbench:productwebbench@127.0.0.1:5432/productwebbench",
        "SESSION_SECRET": "productwebbench-local-session-secret-000000",
        "TURSO_DATABASE_URL": "file:.data/sqlite.db",
        "TURSO_AUTH_TOKEN": "productwebbench-local-turso-token",
    }.items():
        if key in text:
            text = upsert_env_value(text, key, value)
    env_path.write_text(text, encoding="utf-8")


def package_scripts(project_root: Path) -> dict[str, str]:
    package_json = project_root / "package.json"
    if not package_json.exists():
        return {}
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    scripts = data.get("scripts") or {}
    return scripts if isinstance(scripts, dict) else {}


def package_dependencies(project_root: Path) -> dict[str, str]:
    package_json = project_root / "package.json"
    if not package_json.exists():
        return {}
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    dependencies: dict[str, str] = {}
    for key in ("dependencies", "devDependencies"):
        values = data.get(key) or {}
        if isinstance(values, dict):
            dependencies.update({str(k): str(v) for k, v in values.items()})
    return dependencies


def is_next_project(project_root: Path) -> bool:
    if any((project_root / name).exists() for name in ("next.config.js", "next.config.mjs", "next.config.ts")):
        return True
    return "next" in package_dependencies(project_root)


def is_eleventy_project(project_root: Path) -> bool:
    if any((project_root / name).exists() for name in (".eleventy.js", ".eleventy.cjs", "eleventy.config.js", "eleventy.config.mjs")):
        return True
    dependencies = package_dependencies(project_root)
    return "@11ty/eleventy" in dependencies or "eleventy" in dependencies


def has_invalid_pnpm_workspace(project_root: Path) -> bool:
    workspace_path = project_root / "pnpm-workspace.yaml"
    if not workspace_path.exists():
        return False
    try:
        text = workspace_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return not re.search(r"(?m)^\s*packages\s*:", text)


def npm_script_fallback_for_pnpm(command: str, project_root: Path) -> str | None:
    if not has_invalid_pnpm_workspace(project_root):
        return None
    scripts = package_scripts(project_root)
    normalized = command.strip()
    if normalized.startswith("corepack "):
        normalized = normalized.removeprefix("corepack ").strip()
    replacements = {
        "pnpm build": "build",
        "pnpm run build": "build",
        "pnpm dev": "dev",
        "pnpm run dev": "dev",
        "pnpm start": "start",
        "pnpm run start": "start",
        "pnpm preview": "preview",
        "pnpm run preview": "preview",
    }
    script = replacements.get(normalized)
    if script and script in scripts:
        return f"npm run {script}"
    return None


def prisma_generate_command(project_root: Path) -> str:
    scripts = package_scripts(project_root)
    if "db:generate" in scripts:
        return "npm run db:generate"
    if "prisma:generate" in scripts:
        return "npm run prisma:generate"
    if (project_root / "prisma" / "schema.prisma").exists() and (project_root / "package.json").exists():
        return "npx prisma generate"
    return ""


def hardcoded_dev_port(project_root: Path) -> int | None:
    server_js = project_root / "server.js"
    if not server_js.exists():
        source_port_files = [
            project_root / "src" / "utils" / "server.ts",
            project_root / "src" / "utils" / "server.js",
            project_root / "src" / "config.ts",
            project_root / "src" / "config.js",
            project_root / ".env",
            project_root / ".env.local",
            project_root / ".env.development",
        ]
        source_ports: set[int] = set()
        for path in source_port_files:
            if not path.exists() or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            source_ports.update(
                int(match.group(1))
                for match in re.finditer(r"https?://(?:localhost|127\.0\.0\.1):(\d{2,5})\b", text)
            )
        if len(source_ports) == 1:
            return next(iter(source_ports))
        static_dir = static_site_output_dir(project_root)
        index_path = static_dir / "index.html" if static_dir else None
        if index_path is None or not index_path.exists():
            return None
        try:
            text = index_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None
        ports = {
            int(match.group(1))
            for match in re.finditer(r"https?://(?:localhost|127\.0\.0\.1):(\d{2,5})\b", text)
        }
        return next(iter(sorted(ports))) if len(ports) == 1 else None
    try:
        text = server_js.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    match = re.search(r"\bconst\s+port\s*=\s*(\d{2,5})\s*;", text)
    if not match:
        return None
    return int(match.group(1))


# Dev binaries we can safely launch directly (bypassing the package-manager
# wrapper). `corepack pnpm dev` intermittently exits with ELIFECYCLE right after
# the dev server binds — invoking the local binary avoids that failure mode while
# preserving identical server behaviour. Only the first token of the run-script is
# rewritten, and only when it is one of these known dev CLIs with a resolvable
# node_modules/.bin entry.
_DIRECT_DEV_BINARIES = {
    "vite", "astro", "nuxt", "next", "gatsby", "svelte-kit",
    "vue-cli-service", "react-scripts",
}


def _pkg_bin_js(node_modules: Path, binary: str) -> str | None:
    """Resolve <binary>'s real JS entry inside a node_modules dir, so we can run
    it via `node <entry>` instead of the .bin shim. pnpm's .bin/<x> is a copied
    shell shim whose baked-in exec path breaks when the workspace is copied to a
    new location — but the underlying bin JS runs fine under node."""
    import json as _json
    # map common CLI names to their owning package when they differ
    pkg_for = {"svelte-kit": "@sveltejs/kit", "vue-cli-service": "@vue/cli-service"}
    pkg = pkg_for.get(binary, binary)
    # direct (hoisted / npm-flat) install
    search = [node_modules / pkg]
    # pnpm virtual store: node_modules/.pnpm/<pkg>@<ver>/node_modules/<pkg>
    pnpm = node_modules / ".pnpm"
    if pnpm.exists():
        safe = pkg.replace("/", "+")
        search += sorted(pnpm.glob(f"{safe}@*/node_modules/{pkg}"))
    for pkg_dir in search:
        pj = pkg_dir / "package.json"
        if not pj.exists():
            continue
        try:
            data = _json.loads(pj.read_text(encoding="utf-8"))
        except Exception:
            continue
        bin_field = data.get("bin")
        rel = None
        if isinstance(bin_field, str):
            rel = bin_field
        elif isinstance(bin_field, dict):
            rel = bin_field.get(binary) or next(iter(bin_field.values()), None)
        if rel:
            entry = (pkg_dir / rel).resolve()
            if entry.exists():
                return str(entry)
    return None


def _resolve_local_bin(binary: str, project_root: Path) -> str | None:
    """Prefer `node <real-bin.js>`; fall back to node_modules/.bin/<binary>.
    Walks up to a monorepo-root hoist."""
    p = project_root
    for _ in range(4):
        nm = p / "node_modules"
        js = _pkg_bin_js(nm, binary)
        if js:
            return f"node {js}"
        shim = nm / ".bin" / binary
        if shim.exists():
            return str(shim)
        p = p.parent
    return None


def direct_dev_command(dev_command: str, scripts: dict, project_root: Path) -> str:
    """Rewrite a `pnpm/yarn/npm/bun run <script>` dev launcher to the underlying
    local binary (node_modules/.bin/<cli>) when it maps cleanly to a known dev CLI.
    Returns dev_command unchanged when it does not (safe no-op)."""
    toks = dev_command.split()
    if not toks:
        return dev_command
    script_name = None
    if toks[0] in {"pnpm", "yarn", "bun", "npm", "corepack"}:
        rest = toks[1:]
        if rest and rest[0] == "pnpm":  # "corepack pnpm ..."
            rest = rest[1:]
        if rest:
            script_name = rest[1] if rest[0] == "run" and len(rest) > 1 else rest[0]
    if not script_name:
        return dev_command
    body = (scripts or {}).get(script_name, "")
    if not body:
        return dev_command
    body_toks = body.split()
    if not body_toks:
        return dev_command
    binary = body_toks[0]
    if binary not in _DIRECT_DEV_BINARIES:
        return dev_command
    bin_path = _resolve_local_bin(binary, project_root)
    if not bin_path:
        return dev_command
    tail = " ".join(body_toks[1:])
    return f"{bin_path} {tail}".strip()


def _is_jekyll_project(project_root: Path) -> bool:
    """Jekyll: needs Gemfile + _config.yml (both) — Ruby + `bundle exec jekyll serve`."""
    return (project_root / "Gemfile").exists() and (project_root / "_config.yml").exists()


def _infer_framework_from_scripts(scripts: dict, project_root: Path) -> str:
    """When repo_record lacks 'framework' (e.g. fresh 3200-repo baselines that were
    materialized without the catalog step), best-effort infer from package.json scripts.
    Returns a value from the same enum as `catalog.detect_framework`."""
    start = (scripts.get("start") or "") + " " + (scripts.get("dev") or "") + " " + (scripts.get("serve") or "")
    start = start.strip()
    if not start:
        if (project_root / "index.html").exists():
            return "static"
        return "other"
    for tok, fw in (
        ("react-scripts", "react"),
        ("gatsby", "gatsby"),
        ("vite", "vite"),
        ("next", "next"),
        ("nuxt", "nuxt"),
        ("astro", "astro"),
        ("svelte-kit", "svelte"),
        ("sveltekit", "svelte"),
        ("vue-cli-service", "vue"),
        ("ng serve", "other"),        # angular — treat as "other" so shortcut still fires
        ("@11ty/eleventy", "eleventy"),
        ("eleventy", "eleventy"),
        ("parcel", "other"),
        ("webpack", "other"),
        ("browser-sync", "other"),
    ):
        if tok in start:
            return fw
    if "gulp" in start:
        return "other"
    return "other"


def _infer_dev_command_from_scripts(scripts: dict) -> str:
    """When repo_record lacks 'dev_command', pick a canonical npm launcher.
    Preference: scripts.dev > scripts.start > scripts.serve > scripts.develop (gatsby)."""
    if not scripts:
        return ""
    if "dev" in scripts:
        return "npm run dev"
    if "start" in scripts:
        return "npm start"
    if "serve" in scripts:
        return "npm run serve"
    if "develop" in scripts:
        return "npm run develop"
    return ""


# Frameworks whose dev command is a browser-first SPA dev server (webpack/vite/etc.).
# Used by the prefer_dev shortcut to route around stale built-output static serving.
_DEV_OK_FRAMEWORKS = {
    "astro", "gatsby", "next", "nuxt", "react", "svelte", "vite",
    "gridsome", "three", "vue", "eleventy",
}


def dev_command_for_project(repo_record: dict, project_root: Path, *, prefer_dev: bool = False) -> str:
    if is_laravel_project(project_root):
        return "php artisan serve"
    if is_hugo_project(project_root):
        static_dir = static_site_output_dir(project_root)
        if static_dir:
            serve_dir = static_site_serve_dir(repo_record, project_root, static_dir)
            return static_server_command(project_root, serve_dir)
        if is_hugo_theme_root(project_root):
            return "hugo server --source exampleSite --theme . --themesDir .. --disableFastRender"
        if is_hugo_theme_example_site(project_root):
            return "hugo server --disableFastRender"
        return "hugo server --disableFastRender"
    if _is_jekyll_project(project_root):
        # Jekyll: real Ruby dev server. --skip-initial-build lets user's _site/ (if any)
        # be regenerated on first request instead of at server start.
        return "bundle exec jekyll serve --host 0.0.0.0 --port ${PORT:-4000} --skip-initial-build"
    scripts = repo_record.get("scripts") or package_scripts(project_root)
    # Auto-fill framework / dev_command when the workspace manifest didn't set them
    # (happens with fresh 3200-repo baselines materialized before the catalog step ran).
    framework = repo_record.get("framework") or _infer_framework_from_scripts(scripts, project_root)
    dev_command_hint = repo_record.get("dev_command") or _infer_dev_command_from_scripts(scripts)

    # === prefer_dev shortcut ===
    # When skip-build mode is on AND a real dev-server is available, route straight
    # to it — DO NOT fall through to the static-serve cascade below, which would
    # otherwise serve a stale pre-built build/ dir (invisible to solver edits in src/).
    # This is the fix that lets ALL 3200 repos work regardless of whether they were
    # pre-audited into dev_next_build_capabilities.json.
    if prefer_dev and dev_command_hint:
        if framework in _DEV_OK_FRAMEWORKS:
            return direct_dev_command(dev_command_hint, scripts, project_root)
        # Framework unknown/"other" but scripts.dev/start exists → try generic npm launcher.
        # This catches vue-cli, angular, parcel, webpack, gulp+browsersync, custom dev, etc.
        if scripts.get("dev"):
            return direct_dev_command("npm run dev", scripts, project_root)
        if scripts.get("start") and framework != "static":
            return direct_dev_command("npm start", scripts, project_root)
        if scripts.get("serve"):
            return direct_dev_command("npm run serve", scripts, project_root)

    start_script = scripts.get("start", "")
    if is_next_project(project_root) and not prefer_dev and (project_root / ".next").exists() and "next start" in start_script:
        return "npm run start"
    if not prefer_dev and (project_root / ".output" / "server" / "index.mjs").exists():
        return "node .output/server/index.mjs"
    if is_eleventy_project(project_root) and not prefer_dev and (project_root / "_site" / "index.html").exists():
        serve_dir = static_site_serve_dir(repo_record, project_root, project_root / "_site")
        return static_server_command(project_root, serve_dir)
    built_static_dir = built_static_output_dir(project_root)
    if built_static_dir and repo_record.get("framework") in {"astro", "vite", "react", "svelte", "nuxt"} and not prefer_dev:
        serve_dir = static_site_serve_dir(repo_record, project_root, built_static_dir)
        return static_server_command(project_root, serve_dir)
    source_static_dir = static_source_dir(project_root)
    if source_static_dir and repo_record.get("framework") == "static" and not prefer_dev:
        serve_dir = static_site_serve_dir(repo_record, project_root, source_static_dir)
        return static_server_command(project_root, serve_dir)
    static_dir = static_site_output_dir(project_root)
    if static_dir == project_root:
        return "python3 -m http.server"
    if static_dir and repo_record.get("framework") in {"astro", "static"}:
        serve_dir = static_site_serve_dir(repo_record, project_root, static_dir)
        return static_server_command(project_root, serve_dir)
    starts_with_browser_sync = "browser-sync" in scripts.get("localhost", "") or "browser-sync" in scripts.get("start", "")
    uses_gulp_browser_sync = (
        ("gulp" in scripts.get("dev", "") or "gulp" in scripts.get("start", ""))
        and (project_root / "gulpfile.js").exists()
    )
    if static_dir and (starts_with_browser_sync or uses_gulp_browser_sync):
        serve_dir = static_site_serve_dir(repo_record, project_root, static_dir)
        return static_server_command(project_root, serve_dir)
    if static_dir:
        serve_dir = static_site_serve_dir(repo_record, project_root, static_dir)
        return static_server_command(project_root, serve_dir)
    dev_command = repo_record.get("dev_command") or ""
    if repo_record.get("framework") == "nuxt" and (".output/server" in dev_command or ".output/server" in scripts.get("dev", "")):
        if (project_root / "pnpm-lock.yaml").exists():
            return "pnpm exec nuxt dev"
        if (project_root / "yarn.lock").exists():
            return "yarn nuxt dev"
        return "npx nuxt dev"
    if dev_command and repo_record.get("framework") in {"astro", "gatsby", "next", "nuxt", "react", "svelte", "vite"}:
        return direct_dev_command(dev_command, scripts, project_root)
    if has_nested_static_pages(project_root):
        return "python3 -m http.server"
    # === Universal fallback ===
    # Guarantee a fresh-3200-pool repo NEVER returns an empty command. If we got here,
    # the repo has no obvious dev-server AND no obvious static-output dir at the root,
    # but it may still contain servable assets (README.md, demo/, docs/, dist/, ...).
    # Serve project_root statically so the model at least sees the raw contents.
    # If the caller handed us a `.git` directory by mistake (rare — bare-repo zips),
    # walk one level up before deciding.
    serve_root = project_root
    if serve_root.name == ".git" and serve_root.parent.exists():
        serve_root = serve_root.parent
    if _has_any_web_assets(serve_root):
        return f"python3 -m http.server --directory {serve_root}"
    # Absolute last-resort: still return http.server on project_root. Even for
    # "unrunnable" repos (libraries, awesome-lists with only README), serving the
    # dir lets the pipeline continue without a fatal `no_dev_command` abort. The
    # PWB verifier will simply report an empty page + failing dom_assertions
    # (honest signal that the repo isn't a runnable app), which is the correct
    # downstream behavior — the rejection-SFT filter drops such trajectories.
    return "python3 -m http.server"


def _has_any_web_assets(project_root: Path) -> bool:
    """Broad check: any file that could plausibly be served / rendered.
    Covers awesome-lists (README.md), libraries (dist/), theme repos (exampleSite/),
    Hugo themes that fell through is_hugo_project, etc. Skips .git internals."""
    try:
        for p in project_root.rglob("*"):
            if not p.is_file():
                continue
            # Skip .git internals which aren't user-facing content
            try:
                rel = p.relative_to(project_root)
                if ".git" in rel.parts:
                    continue
            except Exception:
                pass
            sfx = p.suffix.lower()
            if sfx in {".html", ".htm", ".md", ".markdown", ".css", ".js", ".mjs",
                       ".jsx", ".tsx", ".ts", ".svg", ".png", ".jpg", ".jpeg",
                       ".webp", ".gif", ".json"}:
                return True
    except Exception:
        pass
    return False


def normalize_shell_command(command: str) -> str:
    if "|| pnpm " in command:
        command = command.replace("|| pnpm ", "|| corepack pnpm ")
    if command.startswith("pnpm install") or command.startswith("corepack pnpm install"):
        if "approve-builds --all" not in command:
            command = (
                f"{command} || "
                "(corepack pnpm approve-builds --all && corepack pnpm install --no-frozen-lockfile)"
            )
    if command.startswith("yarn "):
        return "corepack " + command
    if command.startswith("pnpm "):
        return "corepack " + command
    if command == "npm ci || npm install":
        return "npm ci || npm install --legacy-peer-deps || npm install"
    if "|| yarn " in command:
        return command.replace("|| yarn ", "|| corepack yarn ")
    return command


def project_shell_command(command: str, project_root: Path) -> str:
    normalized = command.strip()
    if has_invalid_pnpm_workspace(project_root) and (
        normalized.startswith("pnpm install") or normalized.startswith("corepack pnpm install")
    ):
        return "npm install --legacy-peer-deps || npm install"
    fallback = npm_script_fallback_for_pnpm(command, project_root)
    if fallback:
        return fallback
    return normalize_shell_command(command)


def run_dev_server(command: str, cwd: Path, log_dir: Path, port: int) -> subprocess.Popen:
    ensure_dir(log_dir)
    stdout = (log_dir / "dev_server.stdout.log").open("w", encoding="utf-8")
    stderr = (log_dir / "dev_server.stderr.log").open("w", encoding="utf-8")
    env = command_environment(cwd, port)
    return subprocess.Popen(
        command,
        cwd=cwd,
        shell=True,
        stdout=stdout,
        stderr=stderr,
        env=env,
        text=True,
        start_new_session=True,
    )


def command_environment(cwd: Path, port: int | None = None) -> dict[str, str]:
    env = os.environ.copy()
    is_astro = (cwd / "astro.config.ts").exists() or (cwd / "astro.config.mjs").exists() or (cwd / "astro.config.js").exists()
    if port is not None:
        env["PORT"] = str(port)
        env["API_HOST"] = f"http://127.0.0.1:{port}"
        if is_astro:
            env["HOST"] = f"http://127.0.0.1:{port}"
        else:
            env["HOST"] = "127.0.0.1"
    elif is_astro:
        env.setdefault("HOST", "http://127.0.0.1:4321")
        env.setdefault("API_HOST", "http://127.0.0.1:4321")
    # Older react-scripts/webpack 4 projects need this on modern Node.
    env.setdefault("NODE_OPTIONS", "--openssl-legacy-provider")
    # CRA treats warnings as build failures in CI; benchmark runability should
    # distinguish runnable UI from lint-level warnings.
    env.setdefault("CI", "false")
    env["ASTRO_TELEMETRY_DISABLED"] = "1"
    env.setdefault("CHOKIDAR_USEPOLLING", "1")
    env.setdefault("WATCHPACK_POLLING", "true")
    env.setdefault("VITE_FORCE_POLLING", "true")
    # Local benchmark captures should exercise the visible UI without requiring
    # optional production services to be configured.
    env.setdefault("SESSION_SECRET", "productwebbench-local-session-secret-000000")
    env.setdefault("TURSO_DATABASE_URL", "file:.data/sqlite.db")
    env.setdefault("TURSO_AUTH_TOKEN", "productwebbench-local-turso-token")
    # Prepend local node_modules/.bin so `npm run dev` / `yarn dev` / `gatsby develop` etc.
    # can find bins even when node_modules is a symlink (npm 10 sometimes fails to add
    # symlinked node_modules/.bin to script PATH). Also try the resolved symlink target.
    bin_paths: list[str] = []
    try:
        nm_bin = Path(cwd) / "node_modules" / ".bin"
        if nm_bin.exists():
            bin_paths.append(str(nm_bin.resolve()))
            bin_paths.append(str(nm_bin))
    except Exception:
        pass
    if bin_paths:
        env["PATH"] = ":".join(dict.fromkeys(bin_paths + env.get("PATH", "").split(":")))
    return env


def terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except Exception:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass


def run_repo_check(
    repo_record: dict,
    workspace_root: Path,
    runs_root: Path,
    clean_workspace: bool,
    skip_install: bool,
    skip_build: bool,
    install_timeout: int,
    build_timeout: int,
    server_timeout: int,
) -> dict:
    workspace_meta = materialize_workspace(repo_record, workspace_root, clean=clean_workspace)
    project_root = Path(workspace_meta["project_root"])
    framework = "next" if is_next_project(project_root) else repo_record.get("framework")
    run_dir = ensure_dir(runs_root / repo_record["repo_id"])
    log_dir = ensure_dir(run_dir / "logs")

    report: dict = {
        "repo_id": repo_record["repo_id"],
        "project_root": str(project_root),
        "workspace": workspace_meta["workspace"],
        "install": None,
        "build": None,
        "dev_server": None,
        "url": None,
        "status": "unknown",
        "failure_stage": None,
    }

    install_command = repo_record.get("install_command") or ("npm install" if is_eleventy_project(project_root) else "")
    build_command = repo_record.get("build_command") or ("npx @11ty/eleventy" if is_eleventy_project(project_root) else "")
    static_without_node = (skip_build or not build_command) and can_serve_without_node_install(
        repo_record,
        project_root,
        prefer_dev=skip_build,
    )
    if static_without_node:
        report["install_skipped_reason"] = "static_site_can_be_served_without_node_install"
        install_command = ""
    if not skip_install and install_command:
        install_command = project_shell_command(install_command, project_root)
        install_result = run_logged_command(
            install_command,
            project_root,
            log_dir,
            "install",
            timeout_sec=install_timeout,
            env=command_environment(project_root),
        )
        report["install"] = asdict(install_result)
        if install_result.exit_code != 0:
            report["status"] = "failed"
            report["failure_stage"] = "install"
            write_json(run_dir / "runability.json", report)
            return report

    if not static_without_node:
        prepare_node_environment(project_root)
        prisma_command = prisma_generate_command(project_root)
        if prisma_command:
            prisma_result = run_logged_command(
                project_shell_command(prisma_command, project_root),
                project_root,
                log_dir,
                "prisma_generate",
                timeout_sec=180,
                env=command_environment(project_root),
            )
            report["prisma_generate"] = asdict(prisma_result)
            if prisma_result.exit_code != 0:
                report["status"] = "failed"
                report["failure_stage"] = "prisma_generate"
                write_json(run_dir / "runability.json", report)
                return report

    if is_laravel_project(project_root) and (project_root / "composer.json").exists() and not (project_root / "vendor").exists():
        composer_result = run_logged_command(
            "composer install --no-interaction --prefer-dist",
            project_root,
            log_dir,
            "composer_install",
            timeout_sec=install_timeout,
            env=command_environment(project_root),
        )
        report["composer_install"] = asdict(composer_result)
        if composer_result.exit_code != 0:
            report["status"] = "failed"
            report["failure_stage"] = "composer_install"
            write_json(run_dir / "runability.json", report)
            return report

    if is_laravel_project(project_root):
        prepare_laravel_environment(project_root)
        config_clear = run_logged_command(
            "php artisan config:clear",
            project_root,
            log_dir,
            "laravel_config_clear",
            timeout_sec=120,
            env=command_environment(project_root),
        )
        report["laravel_config_clear"] = asdict(config_clear)

    if not skip_build and build_command:
        build_command = project_shell_command(build_command, project_root)
        build_result = run_logged_command(
            build_command,
            project_root,
            log_dir,
            "build",
            timeout_sec=build_timeout,
            env=command_environment(project_root),
        )
        report["build"] = asdict(build_result)
        if build_result.exit_code != 0:
            report["status"] = "failed"
            report["failure_stage"] = "build"
            write_json(run_dir / "runability.json", report)
            return report

    dev_command = dev_command_for_project(repo_record, project_root, prefer_dev=skip_build)
    if not dev_command:
        report["status"] = "failed"
        report["failure_stage"] = "no_dev_command"
        write_json(run_dir / "runability.json", report)
        return report

    hardcoded_port = hardcoded_dev_port(project_root)
    if hardcoded_port and is_port_available(hardcoded_port):
        port = hardcoded_port
    else:
        port = find_free_port()
    command = command_with_port(dev_command, port, framework=framework)
    url = f"http://127.0.0.1:{port}/"
    report["url"] = url
    process = run_dev_server(command, project_root, log_dir, port)
    try:
        ok, message = wait_for_http(url, timeout_sec=server_timeout)
        report["dev_server"] = {
            "command": command,
            "pid": process.pid,
            "port": port,
            "ready": ok,
            "message": message,
            "exit_code_at_check": process.poll(),
        }
        if ok:
            report["status"] = "passed"
        else:
            report["status"] = "failed"
            report["failure_stage"] = "dev_server"
    finally:
        terminate_process(process)

    write_json(run_dir / "runability.json", report)
    return report


def add_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("repo_id")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_OUTPUT_ROOT / "manifest" / "repos.jsonl")
    parser.add_argument("--workspace-root", type=Path, default=WORKSPACE_ROOT)
    parser.add_argument("--runs-root", type=Path, default=RUNS_ROOT)
    parser.add_argument("--clean-workspace", action="store_true")
    parser.add_argument("--skip-install", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--install-timeout", type=int, default=600)
    parser.add_argument("--build-timeout", type=int, default=600)
    parser.add_argument("--server-timeout", type=int, default=60)


def run_from_args(args: argparse.Namespace) -> None:
    record = load_repo_record(args.manifest, args.repo_id)
    report = run_repo_check(
        record,
        args.workspace_root,
        args.runs_root,
        clean_workspace=args.clean_workspace,
        skip_install=args.skip_install,
        skip_build=args.skip_build,
        install_timeout=args.install_timeout,
        build_timeout=args.build_timeout,
        server_timeout=args.server_timeout,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
