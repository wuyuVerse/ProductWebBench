from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
from dataclasses import asdict
from pathlib import Path

from ...core.config import DEFAULT_OUTPUT_ROOT
from ...core.io_utils import ensure_dir, write_json
from ...core.process_utils import run_logged_command
from .runability import (
    command_environment,
    command_with_port,
    can_serve_without_node_install,
    dev_command_for_project,
    find_free_port,
    hardcoded_dev_port,
    is_eleventy_project,
    is_laravel_project,
    is_next_project,
    is_port_available,
    package_scripts,
    prepare_laravel_environment,
    prepare_node_environment,
    prisma_generate_command,
    project_shell_command,
    run_dev_server,
    terminate_process,
    wait_for_http,
)
from ...construction.inventory.workspace import WORKSPACE_ROOT, load_repo_record, materialize_workspace


STATE_ROOT = DEFAULT_OUTPUT_ROOT / "states"
TOOL_NODE_ROOT = DEFAULT_OUTPUT_ROOT / "tool_node"
CAPTURE_SERVER_LOCK = STATE_ROOT / ".capture_server.lock"


DEFAULT_VIEWPORTS = {
    "desktop_initial": {"width": 1440, "height": 1000},
    "tablet_initial": {"width": 834, "height": 1112},
    "mobile_initial": {"width": 390, "height": 844},
    # short-name aliases used by per-slot state_plan viewport fields
    # (dimensions match the per-slot capture_states_*.cjs VIEWPORTS map)
    "desktop": {"width": 1440, "height": 1000},
    "tablet": {"width": 834, "height": 1112},
    "mobile": {"width": 390, "height": 844},
}


def state_url(base_url: str, path: str | None) -> str:
    if not path:
        return base_url
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def default_capture_states(base_url: str, states: list[str]) -> list[dict]:
    capture_states = []
    for state_id in states:
        viewport = DEFAULT_VIEWPORTS[state_id]
        capture_states.append(
            {
                "state_id": state_id,
                "url": state_url(base_url, "/"),
                "viewport": viewport,
                "actions": [],
            }
        )
    return capture_states


def _resolve_viewport(viewport: str) -> dict:
    """Map a state_plan viewport string to width/height. Some per-slot state_plans
    fill the viewport field with the STATE_ID (e.g. 'desktop_about_skills') instead
    of a canonical name — a data bug in plan generation. Resolve by: exact match →
    device-prefix (desktop_/tablet_/mobile_) → desktop default, so a stray value
    never crashes the whole capture with KeyError (which was failing 69 tasks)."""
    if viewport in DEFAULT_VIEWPORTS:
        return DEFAULT_VIEWPORTS[viewport]
    v = viewport.lower()
    if v.startswith("tablet"):
        return DEFAULT_VIEWPORTS["tablet"]
    if v.startswith("mobile"):
        return DEFAULT_VIEWPORTS["mobile"]
    # desktop_* and anything unrecognized fall back to the desktop viewport
    return DEFAULT_VIEWPORTS["desktop"]


def load_state_plan(plan_path: Path | None, repo_id: str, base_url: str) -> list[dict] | None:
    if plan_path is None:
        return None
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    raw_states = data.get(repo_id)
    if raw_states is None:
        return None
    states = []
    for item in raw_states:
        viewport = item.get("viewport", {})
        if isinstance(viewport, str):
            viewport = _resolve_viewport(viewport)
        states.append(
            {
                "state_id": item["state_id"],
                "url": state_url(base_url, item.get("path", "/")),
                "viewport": viewport,
                "actions": item.get("actions", []),
                "ready_timeout_ms": item.get("ready_timeout_ms", 15000),
                "goto_wait_until": item.get("goto_wait_until", item.get("wait_until")),
                "goto_timeout_ms": item.get("goto_timeout_ms"),
                "full_page": item.get("full_page", True),
                "disable_animations": item.get("disable_animations", True),
                "capture_crops": item.get("capture_crops", True),
                "probes": item.get("probes", []),
                "workspace_path_checks": item.get("workspace_path_checks", []),
                "capture_mode": item.get("capture_mode"),
                "code_contains_checks": item.get("code_contains_checks", []),
                "python_exec_checks": item.get("python_exec_checks", []),
                "plot_visual_match_checks": item.get("plot_visual_match_checks", []),
                "analytics_fixture": item.get("analytics_fixture"),
                "audio_fixture": item.get("audio_fixture"),
                "web_audio_fixture": item.get("web_audio_fixture"),
                "block_external_network": item.get("block_external_network", False),
                "screenshot_timeout_ms": item.get("screenshot_timeout_ms"),
            }
        )
    return states


def build_capture_config(repo_id: str, base_url: str, output_dir: Path, capture_states: list[dict]) -> Path:
    config = {
        "repo_id": repo_id,
        "base_url": base_url,
        "output_dir": str(output_dir),
        "states": capture_states,
    }
    config_path = output_dir / "capture_config.json"
    write_json(config_path, config)
    return config_path


def ensure_playwright_tool(tool_root: Path, log_dir: Path) -> Path:
    package_json = tool_root / "package.json"
    playwright_pkg = tool_root / "node_modules" / "playwright"
    ensure_dir(tool_root)
    if not package_json.exists():
        package_json.write_text('{"private":true,"dependencies":{}}\n', encoding="utf-8")
    if not playwright_pkg.exists():
        install = run_logged_command(
            "npm install playwright@1.60.0",
            tool_root,
            log_dir,
            "playwright_tool_install",
            timeout_sec=600,
        )
        if install.exit_code != 0:
            raise RuntimeError(f"failed to install Playwright tool package; see {install.stderr_path}")
    # Honor PLAYWRIGHT_BROWSERS_PATH (shared-volume chromium) before falling back
    # to the default per-user cache. In headless pod images $HOME/.cache is empty,
    # so without this the launch fails "Executable doesn't exist" and every capture
    # fake-0s. A pre-staged shared chromium means no per-pod download is needed.
    browser_dirs = []
    env_bp = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env_bp:
        browser_dirs.append(Path(env_bp))
    browser_dirs.append(Path.home() / ".cache" / "ms-playwright")
    has_chromium = any(
        d.exists() and any(d.glob("chromium-*")) for d in browser_dirs
    )
    if not has_chromium:
        browser_install = run_logged_command(
            "npx playwright install chromium",
            tool_root,
            log_dir,
            "playwright_browser_install",
            timeout_sec=900,
        )
        if browser_install.exit_code != 0:
            raise RuntimeError(f"failed to install Playwright Chromium; see {browser_install.stderr_path}")
    return tool_root / "node_modules"


@contextlib.contextmanager
def capture_server_lock():
    ensure_dir(CAPTURE_SERVER_LOCK.parent)
    with CAPTURE_SERVER_LOCK.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def run_state_capture(
    repo_record: dict,
    workspace_root: Path,
    output_root: Path,
    skip_install: bool,
    skip_build: bool,
    install_timeout: int,
    build_timeout: int,
    server_timeout: int,
    capture_timeout: int,
    state_plan: Path | None,
    server_lock: bool = True,
    prepared_workspace_meta: dict | None = None,
) -> dict:
    workspace_meta = prepared_workspace_meta or materialize_workspace(repo_record, workspace_root, clean=False)
    if state_plan is not None:
        declared = json.loads(Path(state_plan).read_text()).get(repo_record["repo_id"],[])
        # Repository states have no page. Route them to the browserless capture
        # instead of installing, building, serving and launching Chromium for a
        # site that does not exist. synth_state_plan already refuses to compile a
        # plan that mixes the two modes, so this is all-or-nothing by construction.
        from .repository_capture import capture_repository_states, is_repository_state
        if declared and all(is_repository_state(state) for state in declared):
            return capture_repository_states(repo_record, output_root, state_plan, workspace_meta)
        if any(is_repository_state(state) for state in declared):
            raise ValueError("A capture cannot mix repository states with browser states")
        if any("capture_build_contract" in state for state in declared):
            from .hugo_environment_capture import capture_environments
            return capture_environments(repo_record,workspace_root,output_root,state_plan,workspace_meta,
                dict(skip_install=skip_install,skip_build=skip_build,install_timeout=install_timeout,
                     build_timeout=build_timeout,server_timeout=server_timeout,capture_timeout=capture_timeout,
                     server_lock=server_lock))
    project_root = Path(workspace_meta["project_root"]).resolve()
    framework = "next" if is_next_project(project_root) else repo_record.get("framework")
    output_dir = ensure_dir((output_root / repo_record["repo_id"]).resolve())
    log_dir = ensure_dir(output_dir / "logs")

    report: dict = {
        "repo_id": repo_record["repo_id"],
        "project_root": str(project_root),
        "status": "unknown",
        "failure_stage": None,
        "skip_install": skip_install,
        "skip_build": skip_build,
        "states_dir": str(output_dir),
    }

    install_command = repo_record.get("install_command") or ("npm install" if is_eleventy_project(project_root) else "")
    build_command = repo_record.get("build_command") or (
        "npx @11ty/eleventy" if is_eleventy_project(project_root) else
        "npm run build" if "build" in package_scripts(project_root) else "")
    if workspace_meta.get("capture_build_contract"):
        from productwebbench._vendor.verifier.hugo_build import build_command as explicit_hugo_command
        build_command = explicit_hugo_command(workspace_meta["capture_build_contract"], project_root)
        report["capture_build_contract"] = workspace_meta["capture_build_contract"]
        if skip_build:
            raise ValueError("Explicit capture build contract cannot skip its build")
    static_without_node = (skip_build or not build_command) and can_serve_without_node_install(
        repo_record,
        project_root,
        prefer_dev=skip_build,
    )
    if static_without_node:
        report["install_skipped_reason"] = "static_site_can_be_served_without_node_install"
        install_command = ""
    if not skip_install and install_command and not (project_root / "node_modules").exists():
        install = run_logged_command(
            project_shell_command(install_command, project_root),
            project_root,
            log_dir,
            "install",
            timeout_sec=install_timeout,
            env=command_environment(project_root),
        )
        report["install"] = asdict(install)
        if install.exit_code != 0:
            report["status"] = "failed"
            report["failure_stage"] = "install"
            write_json(output_dir / "capture_report.json", report)
            return report

    if not static_without_node:
        prepare_node_environment(project_root)
        prisma_command = prisma_generate_command(project_root)
        if prisma_command:
            prisma_generate = run_logged_command(
                project_shell_command(prisma_command, project_root),
                project_root,
                log_dir,
                "prisma_generate",
                timeout_sec=180,
                env=command_environment(project_root),
            )
            report["prisma_generate"] = asdict(prisma_generate)
            if prisma_generate.exit_code != 0:
                report["status"] = "failed"
                report["failure_stage"] = "prisma_generate"
                write_json(output_dir / "capture_report.json", report)
                return report

    if (
        not skip_install
        and is_laravel_project(project_root)
        and (project_root / "composer.json").exists()
        and not (project_root / "vendor").exists()
    ):
        composer_install = run_logged_command(
            "composer install --no-interaction --prefer-dist",
            project_root,
            log_dir,
            "composer_install",
            timeout_sec=install_timeout,
            env=command_environment(project_root),
        )
        report["composer_install"] = asdict(composer_install)
        if composer_install.exit_code != 0:
            report["status"] = "failed"
            report["failure_stage"] = "composer_install"
            write_json(output_dir / "capture_report.json", report)
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
        build = run_logged_command(
            project_shell_command(build_command, project_root),
            project_root,
            log_dir,
            "build",
            timeout_sec=build_timeout,
            env=command_environment(project_root),
        )
        report["build"] = asdict(build)
        if build.exit_code != 0:
            report["status"] = "failed"
            report["failure_stage"] = "build"
            write_json(output_dir / "capture_report.json", report)
            return report

    dev_command = dev_command_for_project(repo_record, project_root, prefer_dev=skip_build)
    if not dev_command:
        report["status"] = "failed"
        report["failure_stage"] = "no_dev_command"
        write_json(output_dir / "capture_report.json", report)
        return report

    lock_context = capture_server_lock() if server_lock else contextlib.nullcontext()
    with lock_context:
        fixed_port = hardcoded_dev_port(project_root)
        port = fixed_port if fixed_port and is_port_available(fixed_port) else find_free_port()
        command = command_with_port(dev_command, port, framework=framework)
        base_url = f"http://127.0.0.1:{port}/"
        process = run_dev_server(command, project_root, log_dir, port)
        try:
            ready, message = wait_for_http(base_url, timeout_sec=server_timeout)
            report["dev_server"] = {"command": command, "port": port, "ready": ready, "message": message}
            if not ready:
                report["status"] = "failed"
                report["failure_stage"] = "dev_server"
                write_json(output_dir / "capture_report.json", report)
                return report

            capture_states = load_state_plan(state_plan, repo_record["repo_id"], base_url)
            if capture_states is None:
                capture_states = default_capture_states(
                    base_url,
                    states=["desktop_initial", "tablet_initial", "mobile_initial"],
                )
            config_path = build_capture_config(repo_record["repo_id"], base_url, output_dir, capture_states)
            has_path_checks = any(s.get("workspace_path_checks") for s in capture_states)
            if has_path_checks:
                from productwebbench._vendor.verifier.workspace_paths import collect_checks
                report["workspace_path_checks"] = {"before":collect_checks(project_root, capture_states)}
            node_modules = ensure_playwright_tool(TOOL_NODE_ROOT, log_dir)
            script_path = Path(__file__).resolve().parents[2] / "tools" / "playwright_capture.js"
            env = os.environ.copy()
            env["NODE_PATH"] = str(node_modules)
            capture = run_logged_command(
                f"node {script_path} {config_path}",
                project_root,
                log_dir,
                "capture",
                timeout_sec=capture_timeout,
                env=env,
            )
            report["capture"] = asdict(capture)
            if has_path_checks:
                report["workspace_path_checks"]["after"] = collect_checks(project_root, capture_states)
            if capture.exit_code != 0:
                report["status"] = "failed"
                report["failure_stage"] = "capture"
            else:
                state_capture_path = output_dir / "state_capture.json"
                report["state_capture"] = str(state_capture_path)
                state_capture = json.loads(state_capture_path.read_text(encoding="utf-8"))
                report["quality_pass"] = bool(state_capture.get("quality_pass"))
                if report["quality_pass"]:
                    report["status"] = "passed"
                else:
                    report["status"] = "failed"
                    report["failure_stage"] = "capture_quality"
        finally:
            terminate_process(process)

    write_json(output_dir / "capture_report.json", report)
    return report


def add_capture_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("repo_id")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_OUTPUT_ROOT / "manifest" / "repos.jsonl")
    parser.add_argument("--workspace-root", type=Path, default=WORKSPACE_ROOT)
    parser.add_argument("--output-root", type=Path, default=STATE_ROOT)
    parser.add_argument("--skip-install", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--install-timeout", type=int, default=600)
    parser.add_argument("--build-timeout", type=int, default=600)
    parser.add_argument("--server-timeout", type=int, default=90)
    parser.add_argument("--capture-timeout", type=int, default=180)
    parser.add_argument("--state-plan", type=Path, default=None)
    parser.add_argument("--no-server-lock", action="store_true")
    parser.add_argument("--prepared-workspace", type=Path, help="Use an explicitly prepared workspace without archive extraction")


def load_prepared_workspace(workspace: Path, repo_id: str) -> dict:
    workspace = workspace.resolve()
    paths = [p for p in (workspace / ".productwebbench_workspace.json",
                          workspace / "app/.productwebbench_workspace.json") if p.is_file()]
    if len(paths) != 1:
        raise ValueError("Prepared workspace requires one explicit metadata file")
    metadata = json.loads(paths[0].read_text())
    project = Path(metadata["project_root"]).resolve()
    declared_workspace = Path(metadata.get("workspace") or metadata.get("workspace_root", "")).resolve()
    if metadata.get("repo_id") != repo_id or declared_workspace != workspace:
        raise ValueError("Prepared workspace identity mismatch")
    if not project.is_dir() or not project.is_relative_to(workspace):
        raise ValueError("Prepared project root escapes workspace or is missing")
    return dict(metadata, project_root=str(project), workspace=str(workspace))


def run_from_args(args: argparse.Namespace) -> None:
    prepared = load_prepared_workspace(args.prepared_workspace, args.repo_id) if getattr(args, "prepared_workspace", None) else None
    record = {"repo_id": args.repo_id, "framework": prepared.get("framework")} if prepared else load_repo_record(args.manifest, args.repo_id)
    report = run_state_capture(
        record,
        args.workspace_root,
        args.output_root,
        skip_install=args.skip_install,
        skip_build=args.skip_build,
        install_timeout=args.install_timeout,
        build_timeout=args.build_timeout,
        server_timeout=args.server_timeout,
        capture_timeout=args.capture_timeout,
        state_plan=args.state_plan,
        server_lock=not args.no_server_lock,
        prepared_workspace_meta=prepared,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
