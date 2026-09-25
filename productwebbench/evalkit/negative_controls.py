from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..core.config import DEFAULT_OUTPUT_ROOT, workspace_meta_path
from ..core.formal_data_guard import assert_not_under_formal_task_root
from ..core.io_utils import ensure_dir, read_jsonl, write_json
from ..construction.inventory.workspace import find_project_root
from ..evaluation.verification.verifier import verify_submission_specs
from ..execution.runtime.browser_state import run_state_capture
from ..execution.runtime.runability import (
    dev_command_for_project,
    is_eleventy_project,
    is_next_project,
    package_dependencies,
    should_build_static_output_before_capture,
    static_site_output_dir,
)
from ..construction.inventory.workspace import load_repo_record, materialize_workspace
from .metareval import expected_checks_for_control_id, result_pass_values
from .metareval import choose_canonical, evidence_candidates_for_slot


def assert_nonformal_output(path: Path | None, *, purpose: str) -> None:
    if path is not None:
        try:
            assert_not_under_formal_task_root(path, purpose=purpose)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return load_json(path)
    except Exception:
        return {}


def first_jsonl(path: Path) -> dict[str, Any]:
    rows = list(read_jsonl(path))
    return rows[0] if rows else {}


def slot_id_from_task_file(path: Path) -> str:
    return path.parent.name if path.parent.name.startswith("slot_") else path.stem


def spec_for_task(spec_path: Path, task_id: str) -> dict[str, Any]:
    data = load_json(spec_path)
    for item in data.get("tasks", []):
        if item.get("task_id") == task_id:
            return item
    return data.get("tasks", [{}])[0] if data.get("tasks") else {}


def control(control_id: str, category: str, expected_failed_checks: list[str], description: str) -> dict[str, Any]:
    return {
        "control_id": control_id,
        "category": category,
        "expected_failed_checks": expected_failed_checks,
        "description": description,
    }


def recommended_controls(task: dict[str, Any], spec: dict[str, Any]) -> list[dict[str, Any]]:
    controls: list[dict[str, Any]] = [
        control(
            "missing_completion",
            "completion_shortcut",
            ["completion_text_signals"],
            "Implement a superficial code change that does not add the required completion content.",
        )
    ]
    if spec.get("forbidden_text_patterns"):
        controls.append(
            control(
                "placeholder_completion",
                "placeholder_shortcut",
                ["forbidden_text_patterns"],
                "Use TODO/coming-soon/lorem/placeholder text that should be rejected by the forbidden-text gate.",
            )
        )
    if spec.get("regression_text_signals"):
        controls.append(
            control(
                "regression_deletion",
                "regression_shortcut",
                ["regression_text_signals"],
                "Delete or overwrite existing navigation, hero, footer, or route content while adding the requested feature.",
            )
        )
    if spec.get("asset_path_signals"):
        controls.append(
            control(
                "asset_shortcut",
                "asset_shortcut",
                ["asset_path_signals"],
                "Replace required local assets with remote stock images, screenshots, or unrelated placeholders.",
            )
        )
    if spec.get("visible_text_signals"):
        controls.append(
            control(
                "hidden_text_completion",
                "hidden_completion_shortcut",
                ["visible_text_signals"],
                "Satisfy completion strings only in hidden/metadata/comment text rather than visible rendered UI.",
            )
        )
    if spec.get("source_change_expectations"):
        controls.append(
            control(
                "source_shortcut",
                "source_shortcut",
                ["source_change_audit"],
                "Avoid the expected implementation files or rewrite unrelated generated output instead of repo-native source.",
            )
        )
    return controls


def slot_number(slot_id: str) -> str:
    return slot_id.replace("slot_", "slot")


def find_task_path(task_root: Path, slot_id: str) -> Path:
    task_path = task_root / slot_id / "task.jsonl"
    if not task_path.exists():
        raise FileNotFoundError(f"task file not found for {slot_id}: {task_path}")
    return task_path


def find_state_plan(task_root: Path, slot_id: str) -> Path:
    direct = task_root / slot_id / "state_plan.json"
    if direct.exists():
        return direct
    nested_named = task_root / slot_id / f"{slot_id}_state_plan.json"
    if nested_named.exists():
        return nested_named
    nested_matches = sorted((task_root / slot_id).glob("*state_plan.json"))
    if nested_matches:
        return nested_matches[0]
    simple = task_root / f"{slot_id}_state_plan.json"
    if simple.exists():
        return simple
    matches = sorted(task_root.glob(f"{slot_id}_*_state_plan.json"))
    if matches:
        return matches[0]
    raise FileNotFoundError(f"state plan not found for {slot_id}")


def path_from_capture_url(base_url: str, url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    query = f"?{parsed.query}" if parsed.query else ""
    return path + query


def state_plan_from_capture_config(config_path: Path, output_path: Path) -> Path:
    config = load_json(config_path)
    repo_id = config["repo_id"]
    states = []
    for item in config.get("states", []):
        states.append(
            {
                "state_id": item["state_id"],
                "path": path_from_capture_url(config.get("base_url", ""), item.get("url", "/")),
                "viewport": item.get("viewport", {}),
                "actions": item.get("actions", []),
                "ready_timeout_ms": item.get("ready_timeout_ms", 15000),
                "goto_wait_until": item.get("goto_wait_until"),
                "goto_timeout_ms": item.get("goto_timeout_ms"),
                "full_page": item.get("full_page", True),
                "disable_animations": item.get("disable_animations", True),
                "capture_crops": item.get("capture_crops", True),
                "screenshot_timeout_ms": item.get("screenshot_timeout_ms"),
            }
        )
    ensure_dir(output_path.parent)
    write_json(output_path, {repo_id: states})
    return output_path


def normalize_replay_action(action: dict[str, Any]) -> dict[str, Any] | None:
    action_type = action.get("type")
    if not action_type:
        return None
    if action_type == "evaluate" and not action.get("expression"):
        return {"type": "wait", "wait_ms": int(action.get("wait_ms") or 500)}
    allowed_keys = {
        "type",
        "selector",
        "x",
        "y",
        "optional",
        "wait_ms",
        "ms",
        "state",
        "timeout_ms",
        "force",
        "button",
        "value",
        "key",
        "expression",
    }
    replay = {key: value for key, value in action.items() if key in allowed_keys and value is not None}
    if action_type == "wait" and "wait_ms" not in replay and "ms" not in replay:
        replay["wait_ms"] = 500
    return replay


def find_state_artifact_root(output_root: Path, slot_id: str, repo_id: str) -> Path | None:
    slot_token = slot_number(slot_id)
    candidates: list[Path] = []
    direct_roots = [
        output_root / "states" / f"{slot_id}_baseline",
        output_root / "states" / f"{slot_id}_base",
        output_root / "states" / f"{slot_id}_reference",
        output_root / f"states_{slot_token}_baseline",
        output_root / f"states_{slot_token}_base",
        output_root / f"states_{slot_token}_reference",
    ]
    for root in direct_roots:
        if (root / repo_id).exists():
            candidates.append(root)

    for parent in [output_root, output_root / "states"]:
        if not parent.exists():
            continue
        for root in parent.iterdir():
            if not root.is_dir() or slot_id not in root.name and slot_token not in root.name:
                continue
            if (root / repo_id).exists():
                candidates.append(root)
    candidates = sorted(set(candidates), key=lambda path: capture_config_priority(path / repo_id / "capture_config.json"))
    for root in candidates:
        repo_dir = root / repo_id
        if any((state_dir / "metrics.json").exists() for state_dir in repo_dir.iterdir() if state_dir.is_dir()):
            return root
    return None


def state_plan_from_state_artifacts(states_root: Path, repo_id: str, output_path: Path) -> Path:
    repo_dir = states_root / repo_id
    states: list[dict[str, Any]] = []
    for state_dir in sorted(path for path in repo_dir.iterdir() if path.is_dir()):
        metrics_path = state_dir / "metrics.json"
        if not metrics_path.exists():
            continue
        metrics = load_json(metrics_path)
        actions_path = state_dir / "actions.json"
        raw_actions = json.loads(actions_path.read_text(encoding="utf-8")) if actions_path.exists() else []
        actions = [action for action in (normalize_replay_action(item) for item in raw_actions) if action]
        states.append(
            {
                "state_id": state_dir.name,
                "path": path_from_capture_url("", metrics.get("url", "/")),
                "viewport": metrics.get("viewport", {}),
                "actions": actions,
                "ready_timeout_ms": 15000,
                "goto_wait_until": "domcontentloaded",
                "goto_timeout_ms": 60000,
                "full_page": True,
                "disable_animations": True,
                "capture_crops": True,
                "derived_from": str(states_root),
                "derived_plan_note": "Reconstructed from metrics/actions artifacts; evaluate actions without expressions are replayed as waits.",
            }
        )
    if not states:
        raise FileNotFoundError(f"no state artifacts found under {repo_dir}")
    ensure_dir(output_path.parent)
    write_json(output_path, {repo_id: states})
    return output_path


def capture_config_priority(path: Path) -> tuple[int, str]:
    name = path.parent.parent.name.lower()
    priority = 50
    if "baseline" in name:
        priority = 0
    elif name.endswith("_base") or "_base" in name:
        priority = 1
    elif "original" in name:
        priority = 2
    elif "reference" in name:
        priority = 8
    elif "probe" in name:
        priority = 20
    elif "smoke" in name or "tmp" in name:
        priority = 30
    return priority, str(path)


def find_capture_config(output_root: Path, slot_id: str, repo_id: str) -> Path | None:
    slot_token = slot_number(slot_id)
    configs: list[Path] = []
    for root in output_root.iterdir() if output_root.exists() else []:
        if not root.is_dir():
            continue
        root_name = root.name
        if slot_token not in root_name:
            continue
        if not any(token in root_name for token in ("states", "captures")):
            continue
        config_path = root / repo_id / "capture_config.json"
        if config_path.exists():
            configs.append(config_path)
    if not configs:
        return None
    return sorted(configs, key=capture_config_priority)[0]


def state_plan_readiness(task_root: Path, output_root: Path, slot_id: str, repo_id: str) -> dict[str, Any]:
    try:
        return {"status": "ready", "source": "task_state_plan", "path": str(find_state_plan(task_root, slot_id))}
    except FileNotFoundError:
        config_path = find_capture_config(output_root, slot_id, repo_id)
        if config_path is not None:
            return {"status": "ready", "source": "capture_config", "path": str(config_path)}
        artifact_root = find_state_artifact_root(output_root, slot_id, repo_id)
        if artifact_root is not None:
            return {"status": "ready", "source": "state_artifacts", "path": str(artifact_root / repo_id)}
    return {"status": "missing", "source": None, "path": None}


def find_or_build_state_plan(task_root: Path, output_root: Path, slot_id: str, repo_id: str) -> Path:
    try:
        return find_state_plan(task_root, slot_id)
    except FileNotFoundError:
        config_path = find_capture_config(output_root, slot_id, repo_id)
        output_path = output_root / "negative_controls" / "state_plans" / f"{slot_id}_state_plan.json"
        if config_path is not None:
            return state_plan_from_capture_config(config_path, output_path)
        artifact_root = find_state_artifact_root(output_root, slot_id, repo_id)
        if artifact_root is not None:
            return state_plan_from_state_artifacts(artifact_root, repo_id, output_path)
        raise


def find_baseline_workspace_root(output_root: Path, slot_id: str, repo_id: str) -> Path:
    candidates = [
        output_root / f"workspaces_{slot_number(slot_id)}_base",
        output_root / f"workspaces_{slot_number(slot_id)}_baseline",
        output_root / f"workspaces_{slot_id}_base",
        output_root / f"workspaces_{slot_id}_baseline",
        output_root / f"workspaces_{slot_number(slot_id)}_clean_baseline",
        output_root / f"workspaces_{slot_id}_clean_baseline",
        output_root / "workspaces_baseline",
    ]
    for root in candidates:
        if (root / repo_id).exists():
            return root
    fallback_roots = sorted(
        path
        for path in output_root.glob(f"workspaces_{slot_number(slot_id)}*")
        if path.is_dir() and (path / repo_id).exists()
        and any(token in path.name for token in ("base", "baseline", "clean_baseline"))
    )
    if fallback_roots:
        return fallback_roots[0]
    global_roots = sorted(
        path
        for path in output_root.glob("workspaces*baseline*")
        if path.is_dir() and (path / repo_id).exists()
    )
    if global_roots:
        return global_roots[0]
    searched = ", ".join(str(root / repo_id) for root in candidates)
    raise FileNotFoundError(f"baseline workspace for {repo_id} not found; searched {searched}")


def find_reference_workspace_root(output_root: Path, slot_id: str, repo_id: str) -> Path:
    candidates = [
        output_root / f"workspaces_{slot_number(slot_id)}_reference",
        output_root / f"workspaces_{slot_id}_reference",
        output_root / "workspaces_reference",
    ]
    for root in candidates:
        if (root / repo_id).exists():
            return root
    report_root = reference_workspace_root_from_capture_report(output_root, slot_id, repo_id)
    if report_root is not None:
        return report_root
    fallback_roots = sorted(
        path
        for path in output_root.glob(f"workspaces_{slot_number(slot_id)}*reference*")
        if path.is_dir() and (path / repo_id).exists()
    )
    if fallback_roots:
        return fallback_roots[0]
    searched = ", ".join(str(root / repo_id) for root in candidates)
    raise FileNotFoundError(f"reference workspace for {repo_id} not found; searched {searched}")


def reference_workspace_root_from_capture_report(output_root: Path, slot_id: str, repo_id: str) -> Path | None:
    report_candidates = [
        output_root / f"states_{slot_number(slot_id)}_reference" / repo_id / "capture_report.json",
        output_root / f"states_{slot_id}_reference" / repo_id / "capture_report.json",
        output_root / "states_reference" / repo_id / "capture_report.json",
    ]
    for report_path in report_candidates:
        if not report_path.exists():
            continue
        try:
            project_root_value = str(load_json(report_path).get("project_root", "")).strip()
        except Exception:
            continue
        if not project_root_value:
            continue
        project_root = Path(project_root_value)
        if not project_root.is_absolute():
            project_root = (Path.cwd() / project_root).resolve()
        for ancestor in [project_root, *project_root.parents]:
            if ancestor.name == repo_id and ancestor.exists():
                root = ancestor.parent
                if (root / repo_id).exists():
                    return root
    return None


def state_plan_click_selectors(state_plan_path: Path, repo_id: str) -> list[str]:
    try:
        plan = load_json(state_plan_path)
    except Exception:
        return []
    states = plan.get(repo_id, plan if isinstance(plan, list) else [])
    selectors: list[str] = []
    if not isinstance(states, list):
        return selectors
    for state in states:
        for action in state.get("actions", []) if isinstance(state, dict) else []:
            if not isinstance(action, dict):
                continue
            if action.get("type") not in {"click", "fill", "hover", "wait_for_selector"}:
                continue
            selector = str(action.get("selector") or "").strip()
            if selector:
                selectors.append(selector)
    return selectors


def selector_source_needles(selector: str) -> list[str]:
    selector = selector.strip()
    needles: list[str] = []
    if selector:
        needles.append(selector)
    for attr, value in re.findall(r"\[([A-Za-z_][\w:.-]*)(?:[*^$|~]?=\s*[\"']?([^\"'\]]+)[\"']?)?\]", selector):
        if attr:
            needles.append(attr)
        if attr and value:
            needles.append(f'{attr}="{value}"')
            needles.append(f"{attr}='{value}'")
    if selector.startswith("#") and len(selector) > 1:
        ident = re.split(r"[\s.:#[\]>+~]", selector[1:], maxsplit=1)[0]
        if ident:
            needles.extend([f'id="{ident}"', f"id='{ident}'"])
    return [needle for needle in dict.fromkeys(needles) if len(needle) >= 3]


def selector_has_source_locatable_attribute(selector: str) -> bool:
    return bool(re.search(r"\[(?:data-[\w:.-]+|aria-[\w:.-]+)", selector))


def text_source_files(root: Path) -> list[Path]:
    suffixes = {".html", ".js", ".jsx", ".ts", ".tsx", ".css", ".vue", ".svelte", ".astro"}
    skipped_parts = {"node_modules", ".next", "dist", "build", ".git", "coverage"}
    files: list[Path] = []
    if not root.exists():
        return files
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        if any(part in skipped_parts for part in path.parts):
            continue
        files.append(path)
    return files


def workspace_source_corpus(workspace_root: Path, repo_id: str, max_chars: int = 5_000_000) -> str:
    repo_root = workspace_root / repo_id
    chunks: list[str] = []
    total = 0
    for path in text_source_files(repo_root):
        text = path.read_text(encoding="utf-8", errors="replace")
        chunks.append(text)
        total += len(text)
        if total >= max_chars:
            break
    return "\n".join(chunks)


def source_corpus_contains_selector(corpus: str, selector: str) -> bool:
    needles = selector_source_needles(selector)
    return bool(needles) and any(needle in corpus for needle in needles)


def state_plan_needs_reference_workspace(state_plan_path: Path, baseline_root: Path, reference_root: Path, repo_id: str) -> bool:
    selectors = [
        selector
        for selector in state_plan_click_selectors(state_plan_path, repo_id)
        if selector_has_source_locatable_attribute(selector)
    ]
    if not selectors:
        return False
    baseline_corpus = workspace_source_corpus(baseline_root, repo_id)
    reference_corpus = workspace_source_corpus(reference_root, repo_id)
    for selector in selectors:
        if source_corpus_contains_selector(reference_corpus, selector) and not source_corpus_contains_selector(baseline_corpus, selector):
            return True
    return False


def source_readiness(
    output_root: Path,
    manifest_path: Path,
    slot_id: str,
    repo_id: str,
    prefer_reference: bool = False,
    state_plan_path: Path | None = None,
) -> dict[str, Any]:
    if prefer_reference:
        try:
            root = find_reference_workspace_root(output_root, slot_id, repo_id)
            return {"status": "ready", "source": "reference_workspace", "path": str(root / repo_id)}
        except FileNotFoundError:
            pass
    if state_plan_path is not None:
        try:
            baseline_root = find_baseline_workspace_root(output_root, slot_id, repo_id)
            reference_root = find_reference_workspace_root(output_root, slot_id, repo_id)
            if state_plan_needs_reference_workspace(state_plan_path, baseline_root, reference_root, repo_id):
                return {"status": "ready", "source": "reference_workspace", "path": str(reference_root / repo_id)}
        except FileNotFoundError:
            pass
    try:
        root = find_baseline_workspace_root(output_root, slot_id, repo_id)
        return {"status": "ready", "source": "baseline_workspace", "path": str(root / repo_id)}
    except FileNotFoundError:
        pass
    try:
        record = load_repo_record(manifest_path, repo_id)
    except Exception:
        return {"status": "missing", "source": None, "path": None}
    return {"status": "ready", "source": "manifest_zip", "path": str(record.get("zip_path"))}


def task_spec(spec_path: Path, task_id: str) -> dict[str, Any]:
    data = load_json(spec_path)
    for item in data.get("tasks", []):
        if item.get("task_id") == task_id:
            return item
    raise KeyError(f"submission spec missing task_id {task_id}: {spec_path}")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_candidates_for_missing_completion(task: dict[str, Any], spec: dict[str, Any], project_root: Path) -> list[Path]:
    raw: list[str] = []
    source_expectations = spec.get("source_change_expectations", {})
    if isinstance(source_expectations, dict):
        raw.extend(str(item) for item in source_expectations.get("must_touch_one_of", []) if item)
    raw.extend(str(item) for item in task.get("suggested_files", []) if item)
    raw.extend(str(item) for item in task.get("assets_to_consider", []) if item)
    raw.extend(
        [
            "index.html",
            "app/page.tsx",
            "app/layout.tsx",
            "src/App.tsx",
            "src/App.jsx",
            "src/main.tsx",
            "src/main.jsx",
            "src/pages/index.tsx",
            "src/pages/index.jsx",
            "pages/index.tsx",
            "pages/index.jsx",
            "next.config.js",
            "vite.config.ts",
            "vite.config.js",
        ]
    )

    candidates: list[Path] = []
    seen: set[Path] = set()
    candidate_roots = [
        project_root,
        project_root / "exampleSite",
        project_root / "src",
        project_root / "app",
        project_root / "public",
    ]
    for value in raw:
        rel = Path(value)
        if rel.is_absolute() or ".." in rel.parts:
            continue
        for root in candidate_roots:
            path = root / rel
            if path.exists() and path.is_file() and path not in seen:
                candidates.append(path)
                seen.add(path)
    if candidates:
        return sorted(
            candidates,
            key=lambda path: (
                any(part in {"layouts", "templates"} for part in path.relative_to(project_root).parts),
                path.suffix not in {".md", ".mdx", ".css", ".scss", ".html", ".htm", ".tsx", ".jsx", ".ts", ".js"},
            ),
        )
    ignored_parts = {
        ".git",
        ".next",
        ".nuxt",
        "node_modules",
        "dist",
        "build",
        "coverage",
        "vendor",
    }
    preferred_names = {
        "index.html",
        "page.tsx",
        "page.jsx",
        "App.tsx",
        "App.jsx",
        "main.tsx",
        "main.jsx",
        "layout.tsx",
        "layout.jsx",
    }
    suffixes = {".html", ".htm", ".tsx", ".jsx", ".ts", ".js", ".vue", ".svelte", ".astro", ".css"}
    recursive = [
        path
        for path in project_root.rglob("*")
        if path.is_file()
        and path.suffix in suffixes
        and not any(part in ignored_parts for part in path.relative_to(project_root).parts)
    ]
    recursive = sorted(
        recursive,
        key=lambda path: (
            path.name not in preferred_names,
            len(path.relative_to(project_root).parts),
            str(path.relative_to(project_root)),
        ),
    )
    return recursive[:1]


def comment_for_path(path: Path, control_id: str) -> str:
    suffix = path.suffix.lower()
    marker = f"ProductWebBench negative control: {control_id}"
    if suffix == ".mdx":
        return f"{{/* {marker} */}}\n"
    if suffix == ".md":
        return f"<!-- {marker} -->\n"
    if suffix in {".html", ".htm", ".xml", ".svg"}:
        return f"<!-- {marker} -->\n"
    if suffix in {".css", ".scss", ".sass"}:
        return f"/* {marker} */\n"
    if suffix in {".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".astro"}:
        return f"// {marker}\n"
    return f"# {marker}\n"


def insert_marker(text: str, marker: str, path: Path) -> str:
    if marker in text:
        return text
    if path.suffix.lower() in {".html", ".htm"}:
        doctype_match = re.match(r"(?is)\s*<!doctype[^>]*>\s*", text)
        if doctype_match:
            insert_at = doctype_match.end()
            return text[:insert_at] + marker + text[insert_at:]
    if path.suffix.lower() in {".md", ".mdx"} and text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            insert_at = end + len("\n---\n")
            return text[:insert_at] + marker + text[insert_at:]
    return marker + text


def apply_missing_completion_edit(task: dict[str, Any], spec: dict[str, Any], project_root: Path, control_id: str) -> dict[str, Any]:
    candidates = source_candidates_for_missing_completion(task, spec, project_root)
    if not candidates:
        raise FileNotFoundError(f"no editable source file found under {project_root}")
    path = candidates[0]
    before = path.read_text(encoding="utf-8", errors="replace")
    marker = comment_for_path(path, control_id)
    after = insert_marker(before, marker, path)
    path.write_text(after, encoding="utf-8")
    return {
        "control_id": control_id,
        "edited_file": str(path),
        "relative_file": str(path.relative_to(project_root)),
        "before_sha256": sha256_text(before),
        "after_sha256": sha256_text(after),
        "changed": before != after,
        "edit_policy": "source-only marker comment; does not add required visible/completion content",
    }


def replacement_for_signal(signal: str) -> str:
    return "[productwebbench removed completion signal]"


def apply_completion_signal_removal_edit(task: dict[str, Any], spec: dict[str, Any], project_root: Path, control_id: str) -> dict[str, Any]:
    signals = [str(signal) for signal in spec.get("completion_text_signals", []) if str(signal).strip()]
    candidates = source_candidates_for_missing_completion(task, spec, project_root)
    checked: list[str] = []
    best: tuple[int, int, Path, str, list[str]] | None = None
    for path in candidates:
        before = path.read_text(encoding="utf-8", errors="replace")
        checked.append(str(path.relative_to(project_root)))
        found = [signal for signal in signals if signal in before]
        if found:
            total_occurrences = sum(before.count(signal) for signal in found)
            candidate_score = (len(found), total_occurrences)
            if best is None or candidate_score > (best[0], best[1]):
                best = (len(found), total_occurrences, path, before, found)
    if best is not None:
        _, _, path, before, found = best
        after = before
        removed: list[str] = []
        for signal in found:
            after = after.replace(signal, replacement_for_signal(signal))
            removed.append(signal)
        if after != before:
            path.write_text(after, encoding="utf-8")
            return {
                "control_id": control_id,
                "edited_file": str(path),
                "relative_file": str(path.relative_to(project_root)),
                "before_sha256": sha256_text(before),
                "after_sha256": sha256_text(after),
                "changed": True,
                "removed_completion_signals": removed,
                "candidate_signal_count": len(found),
                "checked_files": checked,
                "edit_policy": "reference-derived negative control; removes visible completion text signals while preserving runability.",
            }
    raise ValueError(f"no completion_text_signals found in candidate source files under {project_root}; checked {checked}")


def prefers_reference_workspace_source(task: dict[str, Any], spec: dict[str, Any]) -> bool:
    if spec.get("provenance_baseline_text_signals"):
        return True
    forbidden = " ".join(str(item).lower() for item in spec.get("forbidden_text_patterns", []))
    return "application error" in forbidden or "client-side exception" in forbidden or "digest" in forbidden


def copy_baseline_workspace(baseline_workspace_root: Path, bad_workspace_root: Path, repo_id: str, *, reuse_existing: bool) -> Path:
    source = baseline_workspace_root / repo_id
    destination = bad_workspace_root / repo_id
    if destination.exists() and not reuse_existing:
        shutil.rmtree(destination)
    if not destination.exists():
        ensure_dir(destination.parent)
        shutil.copytree(
            source,
            destination,
            symlinks=True,
            ignore=shutil.ignore_patterns(
                "node_modules",
                ".git",
                ".cache",
                ".turbo",
                ".parcel-cache",
                ".vite",
                "coverage",
            ),
        )
    source_meta_path = workspace_meta_path(source)
    destination_meta_path = destination / ".productwebbench_workspace.json"
    if source_meta_path.exists():
        metadata = load_json(source_meta_path)
        source_workspace = Path(metadata.get("workspace", source))
        if not source_workspace.is_absolute():
            source_workspace = (Path.cwd() / source_workspace).resolve()
        source_project = Path(metadata.get("project_root", source))
        if not source_project.is_absolute():
            source_project = (Path.cwd() / source_project).resolve()
        try:
            project_relative = source_project.relative_to(source_workspace)
            destination_project = (destination / project_relative).resolve()
        except ValueError:
            destination_project = find_project_root(destination).resolve()
        metadata["workspace"] = str(destination.resolve())
        metadata["project_root"] = str(destination_project)
        write_json(destination_meta_path, metadata)
    return destination


def needs_node_install_for_capture(repo_record: dict[str, Any], project_root: Path, *, skip_build: bool) -> bool:
    if (project_root / "node_modules").exists() or not (project_root / "package.json").exists():
        return False
    if is_eleventy_project(project_root):
        return True
    if not repo_record.get("install_command"):
        return False
    if not skip_build and repo_record.get("build_command"):
        return True

    dev_command = dev_command_for_project(repo_record, project_root, prefer_dev=skip_build)
    if not dev_command:
        return False
    if "http.server" in dev_command or "static_server.py" in dev_command:
        return False
    node_command_markers = (
        "npm ",
        "npx ",
        "yarn ",
        "pnpm ",
        "next ",
        "vite ",
        "astro ",
        "gatsby ",
        "nuxt ",
    )
    return any(marker in f" {dev_command} " for marker in node_command_markers)


def next_major_version(project_root: Path) -> int | None:
    value = package_dependencies(project_root).get("next", "")
    match = re.search(r"\b(\d{1,2})(?:\.\d+)?", value)
    return int(match.group(1)) if match else None


def infer_framework_from_project(project_root: Path) -> str:
    if is_next_project(project_root):
        return "next"
    package = load_json_if_exists(project_root / "package.json")
    deps = " ".join(
        str(key)
        for section in ("dependencies", "devDependencies")
        for key in (package.get(section, {}) or {})
    ).lower()
    if "astro" in deps or (project_root / "astro.config.mjs").exists():
        return "astro"
    if "vite" in deps or (project_root / "vite.config.js").exists() or (project_root / "vite.config.ts").exists():
        return "vite"
    if "svelte" in deps:
        return "svelte"
    if "react" in deps:
        return "react"
    if (project_root / "index.html").exists() or (project_root / "public" / "index.html").exists():
        return "static"
    return "unknown"


def repo_record_from_workspace(repo_id: str, workspace_root: Path, project_root: Path) -> dict[str, Any]:
    metadata = load_json_if_exists(workspace_root / repo_id / ".productwebbench_workspace.json")
    package = load_json_if_exists(project_root / "package.json")
    scripts = metadata.get("scripts") or package.get("scripts") or {}
    install_command = metadata.get("install_command")
    if install_command is None and package:
        if (project_root / "pnpm-lock.yaml").exists():
            install_command = "pnpm install"
        elif (project_root / "yarn.lock").exists():
            install_command = "yarn install"
        else:
            install_command = "npm install"
    return {
        "repo_id": repo_id,
        "framework": metadata.get("framework") or infer_framework_from_project(project_root),
        "package_name": metadata.get("package_name") or package.get("name"),
        "scripts": scripts,
        "install_command": install_command or "",
        "build_command": metadata.get("build_command") or ("npm run build" if "build" in scripts else ""),
        "dev_command": metadata.get("dev_command") or "",
        "zip_path": metadata.get("zip_path"),
        "has_package_json": bool(package),
    }


def load_repo_record_optional(manifest_path: Path, repo_id: str) -> dict[str, Any] | None:
    try:
        return load_repo_record(manifest_path, repo_id)
    except Exception:
        return None


def build_metadata(
    *,
    slot_id: str,
    task: dict[str, Any],
    control_id: str,
    baseline_workspace_root: Path | None,
    source_kind: str,
    bad_workspace_root: Path,
    bad_states_root: Path,
    task_path: Path,
    spec_path: Path,
    state_plan_path: Path,
    design_anchors_path: Path | None,
    edit: dict[str, Any],
    run_config: dict[str, Any],
    capture_report: dict[str, Any],
    verifier_report_path: Path,
    verifier_report: dict[str, Any],
) -> dict[str, Any]:
    failed_checks = failed_check_names(verifier_report)
    pass_values = result_pass_values(verifier_report)
    failed_all = bool(pass_values) and not any(pass_values)
    expected_failed_checks = expected_checks_for_control_id(control_id)
    expected_check_covered = (
        bool(set(failed_checks).intersection(expected_failed_checks)) if expected_failed_checks else failed_all
    )
    return {
        "schema_version": "2026-06-18",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "slot_id": slot_id,
        "task_id": task.get("task_id"),
        "repo_id": task.get("repo_id"),
        "control_id": control_id,
        "source_kind": source_kind,
        "source_workspace_root": str(baseline_workspace_root) if baseline_workspace_root else None,
        "source_baseline_workspace_root": str(baseline_workspace_root) if baseline_workspace_root else None,
        "bad_workspace_root": str(bad_workspace_root),
        "bad_states_root": str(bad_states_root),
        "task_path": str(task_path),
        "spec_path": str(spec_path),
        "state_plan_path": str(state_plan_path),
        "design_anchors_path": str(design_anchors_path) if design_anchors_path else None,
        "edit": edit,
        "run_config": run_config,
        "capture_status": capture_report.get("status"),
        "capture_failure_stage": capture_report.get("failure_stage"),
        "capture_report_path": str(bad_states_root / task["repo_id"] / "capture_report.json"),
        "verifier_report_path": str(verifier_report_path),
        "verifier_pass_values": pass_values,
        "failed_checks": failed_checks,
        "expected_failed_checks": expected_failed_checks,
        "bad_solution_failed_all": failed_all,
        "expected_check_covered": expected_check_covered,
        "valid_bad_solution_failure": failed_all and expected_check_covered,
        "policy": "Independent negative-control workspace derived from baseline source; baseline/original reports are not reused as bad-solution evidence.",
    }


def run_negative_control(
    *,
    slot_id: str,
    control_id: str,
    task_root: Path,
    output_root: Path,
    manifest_path: Path,
    baseline_workspace_root: Path | None,
    bad_workspace_root: Path | None,
    bad_states_root: Path | None,
    reuse_existing: bool,
    skip_install: bool,
    skip_build: bool,
    server_timeout: int,
    capture_timeout: int,
) -> dict[str, Any]:
    if control_id != "missing_completion":
        raise ValueError(f"unsupported negative control for automated execution: {control_id}")

    task_path = find_task_path(task_root, slot_id)
    task = first_jsonl(task_path)
    repo_id = task["repo_id"]
    spec_path = task_path.parent / "submission_specs.json"
    spec = task_spec(spec_path, task["task_id"])
    state_plan_path = find_or_build_state_plan(task_root, output_root, slot_id, repo_id)
    design_anchors_path = task_path.parent / "design_anchors.json"
    if not design_anchors_path.exists():
        design_anchors_path = None

    workspace_root = bad_workspace_root or (output_root / "negative_controls" / "workspaces" / f"{slot_id}_{control_id}")
    states_root = bad_states_root or (output_root / "negative_controls" / "states" / f"{slot_id}_{control_id}")
    repo_record = load_repo_record_optional(manifest_path, repo_id)
    source_kind = "baseline_workspace"
    try:
        if baseline_workspace_root is not None:
            baseline_root = baseline_workspace_root
        elif prefers_reference_workspace_source(task, spec):
            baseline_root = find_reference_workspace_root(output_root, slot_id, repo_id)
            source_kind = "reference_workspace"
        else:
            baseline_candidate = find_baseline_workspace_root(output_root, slot_id, repo_id)
            try:
                reference_candidate = find_reference_workspace_root(output_root, slot_id, repo_id)
            except FileNotFoundError:
                reference_candidate = None
            if reference_candidate is not None and state_plan_needs_reference_workspace(
                state_plan_path,
                baseline_candidate,
                reference_candidate,
                repo_id,
            ):
                baseline_root = reference_candidate
                source_kind = "reference_workspace"
            else:
                baseline_root = baseline_candidate
    except FileNotFoundError:
        if baseline_workspace_root is not None:
            raise
        baseline_root = None
        source_kind = "manifest_zip"

    if baseline_root is not None:
        copy_baseline_workspace(baseline_root, workspace_root, repo_id, reuse_existing=reuse_existing)
        project_root = find_project_root(workspace_root / repo_id)
        if repo_record is None:
            repo_record = repo_record_from_workspace(repo_id, workspace_root, project_root)
    else:
        if repo_record is None:
            raise ValueError(f"repo_id not found in manifest and no reusable workspace is available: {repo_id}")
        if (workspace_root / repo_id).exists() and not reuse_existing:
            shutil.rmtree(workspace_root / repo_id)
        workspace_meta = materialize_workspace(repo_record, workspace_root, clean=not reuse_existing)
        project_root = Path(workspace_meta["project_root"])
    if source_kind == "reference_workspace":
        edit = apply_completion_signal_removal_edit(task, spec, project_root, control_id)
    else:
        edit = apply_missing_completion_edit(task, spec, project_root, control_id)
    effective_skip_build = skip_build
    if source_kind == "reference_workspace" and edit.get("changed"):
        effective_skip_build = False
    if skip_build and source_kind == "reference_workspace" and static_site_output_dir(project_root) is not None:
        effective_skip_build = False
    if skip_build and should_build_static_output_before_capture(repo_record, project_root):
        effective_skip_build = False
    if skip_build and is_eleventy_project(project_root):
        effective_skip_build = False
    if skip_build and is_next_project(project_root) and (next_major_version(project_root) or 0) >= 16:
        effective_skip_build = False
    effective_skip_install = skip_install
    if skip_install and needs_node_install_for_capture(repo_record, project_root, skip_build=effective_skip_build):
        effective_skip_install = False
    run_config = {
        "requested_skip_install": skip_install,
        "effective_skip_install": effective_skip_install,
        "requested_skip_build": skip_build,
        "effective_skip_build": effective_skip_build,
    }

    capture_report = run_state_capture(
        repo_record,
        workspace_root,
        states_root,
        skip_install=effective_skip_install,
        skip_build=effective_skip_build,
        install_timeout=600,
        build_timeout=600,
        server_timeout=server_timeout,
        capture_timeout=capture_timeout,
        state_plan=state_plan_path,
        server_lock=True,
    )

    verifier_report_path = task_path.parent / "bad_solution_results.json"
    verifier_report = verify_submission_specs(
        task_path,
        spec_path,
        states_root,
        verifier_report_path,
        design_anchors_path,
    )
    metadata = build_metadata(
        slot_id=slot_id,
        task=task,
        control_id=control_id,
        baseline_workspace_root=baseline_root,
        source_kind=source_kind,
        bad_workspace_root=workspace_root,
        bad_states_root=states_root,
        task_path=task_path,
        spec_path=spec_path,
        state_plan_path=state_plan_path,
        design_anchors_path=design_anchors_path,
        edit=edit,
        run_config=run_config,
        capture_report=capture_report,
        verifier_report_path=verifier_report_path,
        verifier_report=verifier_report,
    )
    metadata_path = task_path.parent / "bad_solution_metadata.json"
    write_json(metadata_path, metadata)
    return {
        "metadata_path": str(metadata_path),
        "verifier_report_path": str(verifier_report_path),
        **metadata,
    }


def failed_check_names(report: dict[str, Any]) -> list[str]:
    names: set[str] = set()
    for result in report.get("results", []):
        if not isinstance(result, dict):
            continue
        for check in result.get("checks", []):
            if isinstance(check, dict) and check.get("passed") is False and check.get("name"):
                names.add(str(check["name"]))
    return sorted(names)


def metadata_for_bad_report(report_path: Path) -> dict[str, Any]:
    metadata_path = report_path.with_name("bad_solution_metadata.json")
    if metadata_path.exists():
        return load_json(metadata_path)
    return {}


def bad_report_usable(report_path: Path | None) -> bool:
    if report_path is None or not report_path.exists():
        return False
    metadata = metadata_for_bad_report(report_path)
    if not metadata or metadata.get("capture_status") != "passed":
        return False
    report = load_json(report_path)
    pass_values = result_pass_values(report)
    if not pass_values or any(pass_values):
        return False
    failed_checks = set(failed_check_names(report))
    expected = set(metadata.get("expected_failed_checks") or expected_checks_for_control_id(metadata.get("control_id")))
    return not expected or bool(failed_checks.intersection(expected))


def expected_checks_for_item(item: dict[str, Any], metadata: dict[str, Any]) -> list[str]:
    controls = item.get("recommended_controls", [])
    control_id = metadata.get("control_id")
    if control_id:
        for control_item in controls:
            if control_item.get("control_id") == control_id:
                return list(control_item.get("expected_failed_checks", []))
    expected: set[str] = set()
    for control_item in controls:
        expected.update(str(name) for name in control_item.get("expected_failed_checks", []))
    return sorted(expected)


def audit_negative_control_plan(plan_path: Path, output_path: Path | None = None) -> dict[str, Any]:
    plan = load_json(plan_path)
    items: list[dict[str, Any]] = []
    for item in plan.get("items", []):
        canonical = Path(item["canonical_bad_report_path"])
        selected = canonical if canonical.exists() else None
        if selected is None and item.get("existing_bad_report"):
            candidate = Path(item["existing_bad_report"])
            if candidate.exists():
                selected = candidate

        if selected is None:
            audit = {
                "slot_id": item.get("slot_id"),
                "task_id": item.get("task_id"),
                "repo_id": item.get("repo_id"),
                "status": "missing",
                "bad_report_path": None,
                "failed_all": False,
                "expected_check_covered": False,
                "failed_checks": [],
                "expected_failed_checks": [],
                "issues": ["missing bad-solution verifier report"],
            }
            items.append(audit)
            continue

        report = load_json(selected)
        pass_values = result_pass_values(report)
        failed_all = bool(pass_values) and not any(pass_values)
        failed_checks = failed_check_names(report)
        metadata = metadata_for_bad_report(selected)
        expected_checks = expected_checks_for_item(item, metadata)
        expected_covered = bool(set(failed_checks) & set(expected_checks)) if expected_checks else failed_all
        issues: list[str] = []
        if not failed_all:
            issues.append("bad-solution report did not fail all evaluated tasks")
        if not expected_covered:
            issues.append("bad-solution failed checks do not cover expected control checks")
        if metadata and metadata.get("capture_status") != "passed":
            issues.append(f"capture status is {metadata.get('capture_status')}")
        items.append(
            {
                "slot_id": item.get("slot_id"),
                "task_id": item.get("task_id"),
                "repo_id": item.get("repo_id"),
                "status": "valid" if not issues else "invalid",
                "bad_report_path": str(selected),
                "control_id": metadata.get("control_id"),
                "failed_all": failed_all,
                "expected_check_covered": expected_covered,
                "failed_checks": failed_checks,
                "expected_failed_checks": expected_checks,
                "pass_values": pass_values,
                "issues": issues,
            }
        )

    status_counts = Counter(item["status"] for item in items)
    summary = {
        "schema_version": "2026-06-18",
        "plan_path": str(plan_path),
        "total_tasks": len(items),
        "valid_bad_reports": status_counts.get("valid", 0),
        "invalid_bad_reports": status_counts.get("invalid", 0),
        "missing_bad_reports": status_counts.get("missing", 0),
        "expected_check_covered": sum(1 for item in items if item.get("status") == "valid" and item.get("expected_check_covered")),
        "raw_expected_check_covered": sum(1 for item in items if item.get("expected_check_covered")),
        "items": items,
    }
    if output_path is not None:
        write_json(output_path, summary)
    return summary


def build_negative_control_plan(
    task_root: Path,
    pattern: str = "slot_*/task.jsonl",
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    manifest_path: Path = DEFAULT_OUTPUT_ROOT / "manifest" / "repos.jsonl",
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for task_path in sorted(task_root.glob(pattern)):
        task = first_jsonl(task_path)
        slot_id = slot_id_from_task_file(task_path)
        repo_id = task.get("repo_id")
        spec_path = task_path.parent / "submission_specs.json"
        spec = spec_for_task(spec_path, task.get("task_id")) if spec_path.exists() else {}
        controls = recommended_controls(task, spec)
        candidates = evidence_candidates_for_slot(task_root, slot_id)
        selected_bad = choose_canonical(candidates.get("bad", []), "bad")
        selected_bad_usable = bad_report_usable(selected_bad)
        state_plan_status = state_plan_readiness(task_root, output_root, slot_id, repo_id) if repo_id else {"status": "missing", "source": None, "path": None}
        state_plan_path = Path(state_plan_status["path"]) if state_plan_status.get("path") else None
        if selected_bad_usable:
            source_status = {"status": "not_required", "source": "covered", "path": None}
        else:
            source_status = (
                source_readiness(
                    output_root,
                    manifest_path,
                    slot_id,
                    repo_id,
                    prefer_reference=prefers_reference_workspace_source(task, spec),
                    state_plan_path=state_plan_path,
                )
                if repo_id
                else {"status": "missing", "source": None, "path": None}
            )
        items.append(
            {
                "slot_id": slot_id,
                "task_id": task.get("task_id"),
                "repo_id": repo_id,
                "spec_path": str(spec_path) if spec_path.exists() else None,
                "minimum_required_bad_reports": 1,
                "recommended_controls": controls,
                "recommended_control_count": len(controls),
                "existing_bad_report": str(selected_bad) if selected_bad else None,
                "existing_bad_report_usable": selected_bad_usable,
                "existing_bad_candidate_count": len(candidates.get("bad", [])),
                "state_plan_readiness": state_plan_status,
                "source_readiness": source_status,
                "execution_ready": selected_bad_usable or (state_plan_status["status"] == "ready" and source_status["status"] == "ready"),
                "status": "covered" if selected_bad_usable else "needs_execution",
                "canonical_bad_report_path": str(task_path.parent / "bad_solution_results.json"),
            }
        )
    control_counts = Counter(control["control_id"] for item in items for control in item["recommended_controls"])
    status_counts = Counter(item["status"] for item in items)
    state_plan_counts = Counter(item["state_plan_readiness"]["source"] or "missing" for item in items)
    source_counts = Counter(item["source_readiness"]["source"] or "missing" for item in items)
    return {
        "schema_version": "2026-06-18",
        "task_root": str(task_root),
        "task_pattern": pattern,
        "output_root": str(output_root),
        "manifest_path": str(manifest_path),
        "total_tasks": len(items),
        "covered_tasks": status_counts.get("covered", 0),
        "needs_execution_tasks": status_counts.get("needs_execution", 0),
        "execution_ready_tasks": sum(1 for item in items if item["execution_ready"]),
        "execution_not_ready_tasks": sum(1 for item in items if not item["execution_ready"]),
        "total_recommended_controls": sum(item["recommended_control_count"] for item in items),
        "control_counts": dict(sorted(control_counts.items())),
        "state_plan_source_counts": dict(sorted(state_plan_counts.items())),
        "source_kind_counts": dict(sorted(source_counts.items())),
        "canonical_report_name": "bad_solution_results.json",
        "policy": "A task is not meta-eval complete until at least one independently executed bad-solution report exists and fails.",
        "items": items,
    }


def write_jsonl(path: Path, items: list[dict[str, Any]]) -> None:
    ensure_dir(path.parent)
    path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in items) + ("\n" if items else ""),
        encoding="utf-8",
    )


def batch_item_selected(
    item: dict[str, Any],
    *,
    slot_ids: set[str] | None,
    start_after: str | None,
    include_existing: bool,
) -> bool:
    slot_id = str(item.get("slot_id"))
    if slot_ids is not None and slot_id not in slot_ids:
        return False
    if start_after is not None and slot_id <= start_after:
        return False
    if not item.get("execution_ready"):
        return False
    if not include_existing and Path(item["canonical_bad_report_path"]).exists():
        return False
    return True


def batch_summary(results: list[dict[str, Any]], plan: dict[str, Any], plan_path: Path) -> dict[str, Any]:
    status_counts = Counter(result["status"] for result in results)
    summary = {
        "schema_version": "2026-06-18",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "plan_path": str(plan_path),
        "total_plan_tasks": int(plan.get("total_tasks", 0)),
        "attempted": len(results),
        "succeeded": status_counts.get("succeeded", 0),
        "invalid": status_counts.get("invalid", 0),
        "failed": status_counts.get("failed", 0),
        "skipped": status_counts.get("skipped", 0),
        "results": results,
    }
    return summary


def run_negative_control_batch(
    *,
    plan_path: Path,
    output_path: Path,
    task_root: Path,
    output_root: Path,
    manifest_path: Path,
    control_id: str,
    limit: int | None,
    slot_ids: list[str],
    start_after: str | None,
    include_existing: bool,
    reuse_existing: bool,
    skip_install: bool,
    skip_build: bool,
    server_timeout: int,
    capture_timeout: int,
    max_failures: int,
) -> dict[str, Any]:
    plan = load_json(plan_path)
    selected_slot_ids = set(slot_ids) if slot_ids else None
    candidates = [
        item
        for item in plan.get("items", [])
        if batch_item_selected(
            item,
            slot_ids=selected_slot_ids,
            start_after=start_after,
            include_existing=include_existing,
        )
    ]
    if limit is not None:
        candidates = candidates[:limit]

    ensure_dir(output_path.parent)
    results: list[dict[str, Any]] = []
    failure_count = 0
    for item in candidates:
        slot_id = str(item["slot_id"])
        started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            report = run_negative_control(
                slot_id=slot_id,
                control_id=control_id,
                task_root=task_root,
                output_root=output_root,
                manifest_path=manifest_path,
                baseline_workspace_root=None,
                bad_workspace_root=None,
                bad_states_root=None,
                reuse_existing=reuse_existing,
                skip_install=skip_install,
                skip_build=skip_build,
                server_timeout=server_timeout,
                capture_timeout=capture_timeout,
            )
            status = (
                "succeeded"
                if report.get("capture_status") == "passed" and report.get("valid_bad_solution_failure")
                else "invalid"
            )
            if status != "succeeded":
                failure_count += 1
            results.append(
                {
                    "slot_id": slot_id,
                    "task_id": item.get("task_id"),
                    "repo_id": item.get("repo_id"),
                    "status": status,
                    "started_at": started_at,
                    "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "capture_status": report.get("capture_status"),
                    "capture_failure_stage": report.get("capture_failure_stage"),
                    "pass_values": report.get("verifier_pass_values"),
                    "failed_checks": report.get("failed_checks", []),
                    "expected_failed_checks": report.get("expected_failed_checks", []),
                    "bad_solution_failed_all": report.get("bad_solution_failed_all"),
                    "expected_check_covered": report.get("expected_check_covered"),
                    "valid_bad_solution_failure": report.get("valid_bad_solution_failure"),
                    "verifier_report_path": report.get("verifier_report_path"),
                    "metadata_path": report.get("metadata_path"),
                }
            )
        except Exception as exc:
            failure_count += 1
            results.append(
                {
                    "slot_id": slot_id,
                    "task_id": item.get("task_id"),
                    "repo_id": item.get("repo_id"),
                    "status": "failed",
                    "started_at": started_at,
                    "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }
            )

        write_json(output_path, batch_summary(results, plan, plan_path))
        if max_failures >= 0 and failure_count >= max_failures:
            break

    if not candidates:
        write_json(output_path, batch_summary(results, plan, plan_path))
    return batch_summary(results, plan, plan_path)


def add_plan_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks")
    parser.add_argument("--task-pattern", default="slot_*/task.jsonl")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_OUTPUT_ROOT / "manifest" / "repos.jsonl")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "metareval" / "negative_control_plan.json")
    parser.add_argument("--output-jsonl", type=Path, default=DEFAULT_OUTPUT_ROOT / "metareval" / "negative_control_plan.jsonl")


def add_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--slot-id", required=True)
    parser.add_argument("--control-id", default="missing_completion")
    parser.add_argument("--task-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_OUTPUT_ROOT / "manifest" / "repos.jsonl")
    parser.add_argument("--baseline-workspace-root", type=Path, default=None)
    parser.add_argument("--bad-workspace-root", type=Path, default=None)
    parser.add_argument("--bad-states-root", type=Path, default=None)
    parser.add_argument("--reuse-existing", action="store_true")
    parser.add_argument("--skip-install", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--skip-build", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--server-timeout", type=int, default=120)
    parser.add_argument("--capture-timeout", type=int, default=240)


def add_run_batch_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--plan", type=Path, default=DEFAULT_OUTPUT_ROOT / "metareval" / "negative_control_plan.json")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "metareval" / "negative_control_run_summary.json")
    parser.add_argument("--task-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_OUTPUT_ROOT / "manifest" / "repos.jsonl")
    parser.add_argument("--control-id", default="missing_completion")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--slot-id", action="append", default=[])
    parser.add_argument("--start-after", default=None)
    parser.add_argument("--include-existing", action="store_true")
    parser.add_argument("--reuse-existing", action="store_true")
    parser.add_argument("--skip-install", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--skip-build", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--server-timeout", type=int, default=120)
    parser.add_argument("--capture-timeout", type=int, default=240)
    parser.add_argument("--max-failures", type=int, default=10)


def add_audit_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--plan", type=Path, default=DEFAULT_OUTPUT_ROOT / "metareval" / "negative_control_plan.json")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "metareval" / "negative_control_audit.json")


def run_plan_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.output, purpose="Negative-control plan")
    assert_nonformal_output(args.output_jsonl, purpose="Negative-control plan JSONL")
    assert_nonformal_output(args.output_root, purpose="Negative-control generated evidence root")
    ensure_dir(args.output.parent)
    plan = build_negative_control_plan(
        args.task_root,
        pattern=args.task_pattern,
        output_root=args.output_root,
        manifest_path=args.manifest,
    )
    write_json(args.output, plan)
    write_jsonl(args.output_jsonl, plan["items"])
    print(
        "negative-control plan "
        f"covered={plan['covered_tasks']}/{plan['total_tasks']} "
        f"needs_execution={plan['needs_execution_tasks']} "
        f"controls={plan['total_recommended_controls']} output={args.output}"
    )


def run_negative_control_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.output_root, purpose="Negative-control generated evidence root")
    assert_nonformal_output(args.baseline_workspace_root, purpose="Negative-control baseline workspace root")
    assert_nonformal_output(args.bad_workspace_root, purpose="Negative-control bad workspace root")
    assert_nonformal_output(args.bad_states_root, purpose="Negative-control bad states root")
    report = run_negative_control(
        slot_id=args.slot_id,
        control_id=args.control_id,
        task_root=args.task_root,
        output_root=args.output_root,
        manifest_path=args.manifest,
        baseline_workspace_root=args.baseline_workspace_root,
        bad_workspace_root=args.bad_workspace_root,
        bad_states_root=args.bad_states_root,
        reuse_existing=args.reuse_existing,
        skip_install=args.skip_install,
        skip_build=args.skip_build,
        server_timeout=args.server_timeout,
        capture_timeout=args.capture_timeout,
    )
    print(
        "negative-control run "
        f"slot={report['slot_id']} control={report['control_id']} "
        f"capture={report['capture_status']} pass_values={report['verifier_pass_values']} "
        f"failed_checks={','.join(report['failed_checks'])} "
        f"report={report['verifier_report_path']}"
    )


def run_negative_control_batch_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.output, purpose="Negative-control batch summary")
    assert_nonformal_output(args.output_root, purpose="Negative-control generated evidence root")
    summary = run_negative_control_batch(
        plan_path=args.plan,
        output_path=args.output,
        task_root=args.task_root,
        output_root=args.output_root,
        manifest_path=args.manifest,
        control_id=args.control_id,
        limit=args.limit,
        slot_ids=args.slot_id,
        start_after=args.start_after,
        include_existing=args.include_existing,
        reuse_existing=args.reuse_existing,
        skip_install=args.skip_install,
        skip_build=args.skip_build,
        server_timeout=args.server_timeout,
        capture_timeout=args.capture_timeout,
        max_failures=args.max_failures,
    )
    print(
        "negative-control batch "
        f"attempted={summary['attempted']} succeeded={summary['succeeded']} "
        f"invalid={summary['invalid']} failed={summary['failed']} "
        f"skipped={summary['skipped']} output={args.output}"
    )


def run_audit_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.output, purpose="Negative-control audit")
    summary = audit_negative_control_plan(args.plan, args.output)
    print(
        "negative-control audit "
        f"valid={summary['valid_bad_reports']}/{summary['total_tasks']} "
        f"invalid={summary['invalid_bad_reports']} missing={summary['missing_bad_reports']} "
        f"expected_covered={summary['expected_check_covered']} output={args.output}"
    )
