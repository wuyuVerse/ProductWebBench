from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from ...core.config import DEFAULT_OUTPUT_ROOT
from ...core.io_utils import ensure_dir, read_jsonl, write_json
from ...core.visual import (
    screenshot_summary,
    pixel_diff_ratio,
    ssim_score,
    block_fidelity_score,
)


@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_task_map(tasks_path: Path) -> dict[str, dict]:
    return {task["task_id"]: task for task in read_jsonl(tasks_path)}


def state_dir(states_root: Path, repo_id: str, state_id: str) -> Path:
    return states_root / repo_id / state_id


def state_metrics(states_root: Path, repo_id: str, state_id: str) -> dict | None:
    path = state_dir(states_root, repo_id, state_id) / "metrics.json"
    if not path.exists():
        return None
    return load_json(path)


def state_quality(states_root: Path, repo_id: str, state_id: str) -> dict | None:
    path = state_dir(states_root, repo_id, state_id) / "quality.json"
    if not path.exists():
        return None
    return load_json(path)


def state_actions(states_root: Path, repo_id: str, state_id: str) -> list[dict[str, Any]] | None:
    path = state_dir(states_root, repo_id, state_id) / "actions.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else None


def screenshot_path(states_root: Path, repo_id: str, state_id: str) -> Path:
    return state_dir(states_root, repo_id, state_id) / "screenshot.png"


def _scroll_y_from_metrics(metrics_path: Path) -> int:
    """Recorded vertical scroll offset (px) of a capture, 0 if unavailable.

    Used to align reference vs submission screenshots that captured the same
    document at different scroll positions (a capture-recipe artifact). Reads
    the ground-truth `scroll.y` the capture engine recorded — never a search.
    """
    try:
        data = load_json(metrics_path)
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0
    scroll = data.get("scroll") or {}
    try:
        return int(scroll.get("y", 0) or 0)
    except (TypeError, ValueError):
        return 0


def is_interactive_canvas_task(task: dict) -> bool:
    blob = " ".join(
        str(task.get(key, ""))
        for key in ("website_type", "intent", "scope", "problem_statement", "author_notes")
    ).lower()
    return task.get("website_type") == "interactive_canvas" or any(
        token in blob for token in ("webgl", "three.js", "react three fiber", "canvas")
    )


def has_canvas_visual_surface(task: dict, spec: dict, states_root: Path, repo_id: str, state_id: str) -> bool:
    if not is_interactive_canvas_task(task):
        return False
    metrics = state_metrics(states_root, repo_id, state_id) or {}
    viewport = metrics.get("viewport", {})
    min_area = max(48_000, int(viewport.get("width", 0) * viewport.get("height", 0) * 0.15))
    has_visible_canvas = any(
        box.get("tag") == "canvas"
        and box.get("rect", {}).get("width", 0) * box.get("rect", {}).get("height", 0) >= min_area
        for box in metrics.get("boxes", [])
    )
    screenshot = screenshot_path(states_root, repo_id, state_id)
    min_size = spec.get("min_canvas_screenshot_bytes", spec.get("min_screenshot_bytes", 20_000))
    screenshot_large_enough = screenshot.exists() and screenshot.stat().st_size >= min_size
    return has_visible_canvas and screenshot_large_enough


def collect_state_text(states_root: Path, repo_id: str, states: list[str]) -> str:
    chunks = []
    for state_id in states:
        metrics = state_metrics(states_root, repo_id, state_id)
        if metrics:
            chunks.append(metrics.get("text", ""))
    return "\n".join(chunks)


def rect_intersects_viewport(rect: dict[str, Any], viewport: dict[str, Any]) -> bool:
    width = float(rect.get("width") or 0)
    height = float(rect.get("height") or 0)
    if width <= 0 or height <= 0:
        return False
    x = float(rect.get("x") or 0)
    y = float(rect.get("y") or 0)
    viewport_width = float(viewport.get("width") or 0)
    viewport_height = float(viewport.get("height") or 0)
    if viewport_width <= 0 or viewport_height <= 0:
        return True
    return x < viewport_width and x + width > 0 and y < viewport_height and y + height > 0


def collect_visible_box_text(states_root: Path, repo_id: str, states: list[str]) -> str:
    chunks = []
    for state_id in states:
        metrics = state_metrics(states_root, repo_id, state_id)
        if not metrics:
            continue
        viewport = metrics.get("viewport", {})
        for box in metrics.get("boxes", []):
            text = str(box.get("text") or "").strip()
            if text and rect_intersects_viewport(box.get("rect", {}), viewport):
                chunks.append(text)
    return "\n".join(chunks)


def contains_all_text(text: str, signals: list[str]) -> list[str]:
    lowered = text.lower()
    return [signal for signal in signals if signal.lower() not in lowered]


def check_required_states(task: dict, spec: dict, states_root: Path) -> CheckResult:
    repo_id = task["repo_id"]
    required = spec.get("reference_states") or task.get("required_states", [])
    missing = []
    for state_id in required:
        if not (state_dir(states_root, repo_id, state_id) / "metrics.json").exists():
            missing.append(state_id)
    return CheckResult(
        name="required_states",
        passed=not missing,
        message="all required reference states are present" if not missing else "missing reference states",
        details={"required": required, "missing": missing},
    )


def check_state_quality(task: dict, spec: dict, states_root: Path) -> CheckResult:
    repo_id = task["repo_id"]
    required = spec.get("reference_states") or task.get("required_states", [])
    failures = []
    warnings = []
    summaries = {}
    for state_id in required:
        quality = state_quality(states_root, repo_id, state_id)
        if quality is None:
            failures.append({"state_id": state_id, "reason": "missing_quality"})
            continue
        summaries[state_id] = quality
        if quality.get("is_loading_like") and not has_canvas_visual_surface(task, spec, states_root, repo_id, state_id):
            failures.append({"state_id": state_id, "reason": "loading_like", "quality": quality})
        if quality.get("action_failure_count", 0) > 0:
            failures.append({"state_id": state_id, "reason": "action_failures", "quality": quality})
        if quality.get("text_length", 0) < spec.get("min_text_length", 40):
            warnings.append({"state_id": state_id, "reason": "low_text", "quality": quality})
        min_links = spec.get("min_link_count")
        if min_links is not None and quality.get("link_count", 0) < min_links:
            failures.append({"state_id": state_id, "reason": "low_link_count", "quality": quality})
    return CheckResult(
        name="state_quality",
        passed=not failures,
        message="reference states pass quality gates" if not failures else "some states fail quality gates",
        details={"failures": failures, "warnings": warnings, "summaries": summaries},
    )


def check_state_artifacts(task: dict, spec: dict, states_root: Path) -> CheckResult:
    repo_id = task["repo_id"]
    states = spec.get("reference_states") or spec.get("submission_states") or task.get("required_states", [])
    required_files = spec.get(
        "required_state_artifacts",
        ["screenshot.png", "metrics.json", "quality.json", "dom.html", "a11y.json", "boxes.json", "console.json", "actions.json", "crops.json"],
    )
    failures = []
    for state_id in states:
        current_dir = state_dir(states_root, repo_id, state_id)
        for filename in required_files:
            path = current_dir / filename
            if not path.exists():
                failures.append({"state_id": state_id, "artifact": filename, "reason": "missing"})
            elif path.is_file() and path.stat().st_size == 0:
                failures.append({"state_id": state_id, "artifact": filename, "reason": "empty"})
    return CheckResult(
        name="state_artifacts",
        passed=not failures,
        message="all required state artifacts exist" if not failures else "missing state artifacts",
        details={"states": states, "required_files": required_files, "failures": failures},
    )


def check_action_history(task: dict, spec: dict, states_root: Path) -> CheckResult:
    repo_id = task["repo_id"]
    planned = {
        state.get("state_id"): state.get("actions", [])
        for state in spec.get("state_plan", [])
    }
    states = spec.get("interaction_states") or [
        state_id
        for state_id in (spec.get("reference_states") or spec.get("submission_states") or task.get("required_states", []))
        if state_actions(states_root, repo_id, state_id)
    ]
    failures = []
    summaries = {}
    for state_id in states:
        actions = state_actions(states_root, repo_id, state_id)
        if actions is None:
            failures.append({"state_id": state_id, "reason": "missing_actions_json"})
            continue
        failed = [action for action in actions if action.get("status") == "failed"]
        skipped_required = [action for action in actions if action.get("status") == "skipped" and not action.get("optional")]
        summaries[state_id] = {
            "action_count": len(actions),
            "failed_count": len(failed),
            "skipped_required_count": len(skipped_required),
            "actions": actions,
        }
        if failed:
            failures.append({"state_id": state_id, "reason": "failed_actions", "actions": failed})
        if skipped_required:
            failures.append({"state_id": state_id, "reason": "skipped_required_actions", "actions": skipped_required})
        if state_id in planned and len(actions) != len(planned[state_id]):
            failures.append(
                {
                    "state_id": state_id,
                    "reason": "action_count_mismatch",
                    "expected": len(planned[state_id]),
                    "actual": len(actions),
                }
            )
    return CheckResult(
        name="action_history",
        passed=not failures,
        message="all captured actions passed" if not failures else "captured action failures detected",
        details={"states": states, "failures": failures, "summaries": summaries},
    )


def check_no_horizontal_overflow(task: dict, spec: dict, states_root: Path) -> CheckResult:
    repo_id = task["repo_id"]
    states = spec.get("responsive_states", [])
    tolerance = spec.get("overflow_tolerance_px", 4)
    failures = []
    for state_id in states:
        metrics = state_metrics(states_root, repo_id, state_id)
        if metrics is None:
            failures.append({"state_id": state_id, "reason": "missing_metrics"})
            continue
        viewport_width = metrics.get("viewport", {}).get("width", 0)
        doc_width = metrics.get("document", {}).get("width", 0)
        if doc_width > viewport_width + tolerance:
            failures.append(
                {
                    "state_id": state_id,
                    "viewport_width": viewport_width,
                    "document_width": doc_width,
                }
            )
    return CheckResult(
        name="no_horizontal_overflow",
        passed=not failures,
        message="responsive states have no horizontal overflow" if not failures else "horizontal overflow detected",
        details={"failures": failures},
    )


def check_text_signals(task: dict, spec: dict, states_root: Path) -> CheckResult:
    repo_id = task["repo_id"]
    signals = spec.get("reference_text_signals", [])
    if not signals:
        return CheckResult("reference_text_signals", True, "no text signals configured")
    states = spec.get("text_signal_states") or spec.get("reference_states") or task.get("required_states", [])
    combined = collect_state_text(states_root, repo_id, states)
    missing = contains_all_text(combined, signals)
    return CheckResult(
        name="reference_text_signals",
        passed=not missing,
        message="all reference text signals found" if not missing else "missing reference text signals",
        details={"signals": signals, "missing": missing},
    )


def check_visible_text_signals(task: dict, spec: dict, states_root: Path) -> CheckResult:
    repo_id = task["repo_id"]
    signals = spec.get("visible_text_signals", [])
    if not signals:
        return CheckResult("visible_text_signals", True, "no visible text signals configured")
    states = (
        spec.get("visible_text_states")
        or spec.get("text_signal_states")
        or spec.get("reference_states")
        or spec.get("submission_states")
        or task.get("required_states", [])
    )
    combined = collect_visible_box_text(states_root, repo_id, states)
    missing = contains_all_text(combined, signals)
    min_found = spec.get("min_visible_text_signals", len(signals))
    found_count = len(signals) - len(missing)
    passed = found_count >= min_found and not missing if spec.get("require_all_visible_text_signals", True) else found_count >= min_found
    return CheckResult(
        name="visible_text_signals",
        passed=passed,
        message="required visible text signals found" if passed else "required visible text signals missing",
        details={
            "states": states,
            "signals": signals,
            "missing": missing,
            "found_count": found_count,
            "min_found": min_found,
            "require_all": spec.get("require_all_visible_text_signals", True),
        },
    )


def check_state_url_signals(task: dict, spec: dict, states_root: Path) -> CheckResult:
    repo_id = task["repo_id"]
    signals = spec.get("state_url_signals", [])
    if not signals:
        return CheckResult("state_url_signals", True, "no state URL signals configured")
    failures = []
    summaries = {}
    for signal in signals:
        if isinstance(signal, str):
            state_id = signal
            contains = signal
            equals = None
            ends_with = None
            not_contains = []
        else:
            state_id = signal.get("state_id")
            contains = signal.get("contains")
            equals = signal.get("equals")
            ends_with = signal.get("ends_with")
            not_contains = signal.get("not_contains") or []
            if isinstance(not_contains, str):
                not_contains = [not_contains]
        if not state_id:
            failures.append({"reason": "missing_state_id", "signal": signal})
            continue
        metrics = state_metrics(states_root, repo_id, state_id)
        url = str((metrics or {}).get("url") or "")
        summaries[state_id] = {"url": url, "signal": signal}
        if not metrics:
            failures.append({"state_id": state_id, "reason": "missing_metrics", "signal": signal})
            continue
        if contains and contains not in url:
            failures.append({"state_id": state_id, "reason": "missing_url_substring", "expected": contains, "url": url})
        if equals and url != equals:
            failures.append({"state_id": state_id, "reason": "url_not_equal", "expected": equals, "url": url})
        if ends_with and not url.endswith(ends_with):
            failures.append({"state_id": state_id, "reason": "url_suffix_mismatch", "expected": ends_with, "url": url})
        forbidden_hits = [token for token in not_contains if token in url]
        if forbidden_hits:
            failures.append({"state_id": state_id, "reason": "forbidden_url_substring", "forbidden": forbidden_hits, "url": url})
    return CheckResult(
        name="state_url_signals",
        passed=not failures,
        message="state URLs match expected signals" if not failures else "state URL signals missing",
        details={"signals": signals, "failures": failures, "summaries": summaries},
    )


def check_image_alt_signals(task: dict, spec: dict, states_root: Path) -> CheckResult:
    repo_id = task["repo_id"]
    signals = spec.get("image_alt_signals", [])
    if not signals:
        return CheckResult("image_alt_signals", True, "no image alt signals configured")
    states = (
        spec.get("image_alt_states")
        or spec.get("asset_signal_states")
        or spec.get("submission_states")
        or spec.get("reference_states")
        or task.get("required_states", [])
    )
    alt_values = []
    for state_id in states:
        metrics = state_metrics(states_root, repo_id, state_id)
        if not metrics:
            continue
        for image in metrics.get("images", []):
            alt = image.get("alt")
            if alt:
                alt_values.append(str(alt))
    combined = "\n".join(alt_values)
    missing = contains_all_text(combined, signals)
    min_found = spec.get("min_image_alt_signals", len(signals))
    found_count = len(signals) - len(missing)
    passed = found_count >= min_found and not missing if spec.get("require_all_image_alt_signals", True) else found_count >= min_found
    return CheckResult(
        name="image_alt_signals",
        passed=passed,
        message="required image alt signals found" if passed else "required image alt signals missing",
        details={
            "states": states,
            "signals": signals,
            "missing": missing,
            "found_count": found_count,
            "min_found": min_found,
            "require_all": spec.get("require_all_image_alt_signals", True),
            "sample_alts": alt_values[:20],
        },
    )


def check_screenshot_files(task: dict, spec: dict, states_root: Path) -> CheckResult:
    repo_id = task["repo_id"]
    min_size = spec.get("min_screenshot_bytes")
    if min_size is None:
        return CheckResult("screenshot_files", True, "no screenshot-size gate configured")
    states = spec.get("reference_states") or task.get("required_states", [])
    failures = []
    sizes = {}
    for state_id in states:
        path = state_dir(states_root, repo_id, state_id) / "screenshot.png"
        if not path.exists():
            failures.append({"state_id": state_id, "reason": "missing_screenshot"})
            continue
        size = path.stat().st_size
        sizes[state_id] = size
        if size < min_size:
            failures.append({"state_id": state_id, "size": size, "min_size": min_size})
    return CheckResult(
        name="screenshot_files",
        passed=not failures,
        message="screenshots meet size gate" if not failures else "some screenshots are too small",
        details={"min_screenshot_bytes": min_size, "sizes": sizes, "failures": failures},
    )


def check_asset_signals(task: dict, spec: dict, states_root: Path) -> CheckResult:
    repo_id = task["repo_id"]
    min_images = spec.get("min_image_count")
    if min_images is None:
        return CheckResult("asset_signals", True, "no image-count gate configured")
    states = spec.get("asset_signal_states") or spec.get("reference_states") or task.get("required_states", [])
    counts = {}
    failures = []
    for state_id in states:
        quality = state_quality(states_root, repo_id, state_id)
        if not quality:
            failures.append({"state_id": state_id, "reason": "missing_quality"})
            continue
        counts[state_id] = quality.get("image_count", 0)
        if quality.get("image_count", 0) < min_images:
            failures.append({"state_id": state_id, "image_count": quality.get("image_count", 0)})
    return CheckResult(
        name="asset_signals",
        passed=not failures,
        message="reference states contain expected image density" if not failures else "image density below threshold",
        details={"min_image_count": min_images, "counts": counts, "failures": failures},
    )


def state_console(states_root: Path, repo_id: str, state_id: str) -> list[dict[str, Any]] | None:
    path = state_dir(states_root, repo_id, state_id) / "console.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else None


def state_network(states_root: Path, repo_id: str, state_id: str) -> list[dict[str, Any]] | None:
    path = state_dir(states_root, repo_id, state_id) / "network.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else None


# Console messages that are benign in the offline capture harness (analytics
# beacons, favicons, dev-only telemetry) and must not fail a product-grade gate.
DEFAULT_CONSOLE_ERROR_ALLOWLIST = [
    "/_vercel/",
    "web analytics",
    "vercel web analytics",
    "favicon.ico",
    "insights/script.js",
    "google-analytics.com",
    "googletagmanager.com",
    "google-analytics",
]


def check_console_errors(task: dict, spec: dict, states_root: Path) -> CheckResult:
    """Product-grade runtime-health gate: fail if a state emits console errors
    that are not on the benign allowlist. Opt-in: no-op unless the spec sets
    ``enforce_console_errors`` (or supplies ``console_error_states``)."""
    if not spec.get("enforce_console_errors") and not spec.get("console_error_states"):
        return CheckResult("console_errors", True, "console-error gate not configured")
    repo_id = task["repo_id"]
    states = spec.get("console_error_states") or spec.get("submission_states") or spec.get("reference_states") or task.get("required_states", [])
    allowlist = [str(s).lower() for s in spec.get("console_error_allowlist", DEFAULT_CONSOLE_ERROR_ALLOWLIST)]
    max_errors = int(spec.get("max_console_errors", 0))
    failures = []
    per_state = {}
    for state_id in states:
        messages = state_console(states_root, repo_id, state_id)
        if messages is None:
            failures.append({"state_id": state_id, "reason": "missing_console"})
            continue
        network = state_network(states_root, repo_id, state_id) or []
        offending = []
        # (1) Real JS errors / uncaught exceptions: console pageerror, or console
        # "error" messages that are NOT the browser's generic urlless
        # "Failed to load resource" (those are network failures, judged below).
        for m in messages:
            t = str(m.get("text", ""))
            typ = m.get("type")
            include = typ == "pageerror" or (
                typ in ({"error", "warning"} if spec.get("console_include_warnings") else {"error"})
                and "failed to load resource" not in t.lower()
            )
            if not include:
                continue
            blob = (t + " " + str(m.get("url", ""))).lower()
            if not any(token in blob for token in allowlist):
                offending.append(t[:120])
        # (2) Network failures (status>=400 / requestfailed) carry the URL, so the
        # allowlist can distinguish benign analytics/favicon from real app errors.
        for e in network:
            url = str(e.get("url", "")).lower()
            if any(token in url for token in allowlist):
                continue
            offending.append(f"net {e.get('status')} {str(e.get('url'))[:100]}")
        per_state[state_id] = len(offending)
        if len(offending) > max_errors:
            failures.append({"state_id": state_id, "offending": offending[:5], "count": len(offending)})
    return CheckResult(
        name="console_errors",
        passed=not failures,
        message="no disallowed console errors" if not failures else "states emit disallowed console errors",
        details={"states": states, "max_console_errors": max_errors, "per_state": per_state, "failures": failures},
    )


# Tags a plan may legitimately assert that the renderer can NEVER paint: the UA
# stylesheet gives them display:none, or (for <option>/<optgroup>) a 0x0 client rect
# inside a collapsed <select>. The capture engine puts them in a separate
# ``structural_boxes`` channel. Only these tags may fall back to that channel --
# an assertion about a *visible* element must keep failing when the element is
# hidden, otherwise a bad variant could pass by merely CSS-hiding what it broke.
_STRUCTURAL_ONLY_TAGS = frozenset({
    "style", "script", "title", "noscript", "meta", "link", "base", "template",
    "option", "optgroup", "track", "param",
    "defs", "symbol", "desc", "metadata", "clippath", "lineargradient", "radialgradient",
})

# Plans routinely write an <input> *type* where a tag belongs (tag: "checkbox" for
# <input type="checkbox">). Resolve the alias instead of never matching.
_INPUT_TYPE_ALIASES = frozenset({
    "checkbox", "radio", "text", "email", "password", "search", "tel", "url",
    "number", "range", "date", "time", "datetime-local", "month", "week",
    "color", "file", "submit", "reset", "hidden",
})


def _candidate_boxes(metrics: dict[str, Any], rule: dict[str, Any]) -> list[dict[str, Any]]:
    """Boxes a rule may match: the visible channel, plus -- only for structurally
    unpaintable tags -- the structural channel. Old captures lack
    ``structural_boxes`` and simply behave as before."""
    boxes = list(metrics.get("boxes") or [])
    tag = str(rule.get("tag", "")).lower()
    if tag in _STRUCTURAL_ONLY_TAGS:
        boxes.extend(metrics.get("structural_boxes") or [])
    return boxes


def _tag_matches(box: dict[str, Any], want: str) -> bool:
    want = want.lower()
    if str(box.get("tag", "")).lower() == want:
        return True
    # <input type="checkbox"> satisfies tag: "checkbox"
    if want in _INPUT_TYPE_ALIASES and str(box.get("tag", "")).lower() == "input":
        return str(box.get("type", "")).lower() == want
    return False


def _box_matches(box: dict[str, Any], rule: dict[str, Any]) -> bool:
    if "tag" in rule and not _tag_matches(box, str(rule["tag"])):
        return False
    if "role" in rule and str(box.get("role", "")).lower() != str(rule["role"]).lower():
        return False
    if "class_contains" in rule and str(rule["class_contains"]).lower() not in str(box.get("class_name", "")).lower():
        return False
    if "text_contains" in rule and str(rule["text_contains"]).lower() not in str(box.get("text", "")).lower():
        return False
    return True


def check_dom_assertions(task: dict, spec: dict, states_root: Path) -> CheckResult:
    """Product-grade structural gate: assert presence/count of DOM elements
    (by tag/role/class/text) in captured states, beyond text-substring matching.
    Opt-in: no-op unless the spec supplies ``dom_assertions``.

    Each assertion: {state, tag?, role?, class_contains?, text_contains?,
    min_count? (default 1), exact_count?}."""
    assertions = spec.get("dom_assertions")
    if not assertions:
        return CheckResult("dom_assertions", True, "no DOM assertions configured")
    repo_id = task["repo_id"]
    failures = []
    results = []
    for idx, rule in enumerate(assertions):
        state_id = rule.get("state")
        metrics = state_metrics(states_root, repo_id, state_id) if state_id else None
        if not metrics:
            failures.append({"index": idx, "state_id": state_id, "reason": "missing_metrics"})
            continue
        boxes = _candidate_boxes(metrics, rule)
        matched = sum(1 for box in boxes if _box_matches(box, rule))
        if "exact_count" in rule:
            ok = matched == int(rule["exact_count"])
        else:
            ok = matched >= int(rule.get("min_count", 1))
        results.append({"index": idx, "state_id": state_id, "matched": matched, "rule": rule, "ok": ok})
        if not ok:
            failures.append({"index": idx, "state_id": state_id, "matched": matched, "rule": rule})
    return CheckResult(
        name="dom_assertions",
        passed=not failures,
        message="all DOM assertions satisfied" if not failures else "DOM assertions failed",
        details={"results": results, "failures": failures},
    )


# ---------------------------------------------------------------------------
# D4 interaction-trajectory gate (check_interaction_assertions)
#
# 0702 §6.1 D4: acceptance happens AFTER a real click/type/submit/route
# sequence, on the post-interaction DOM/URL terminal state — not on a static
# first paint. This gate goes beyond check_action_history (which only checks
# the trajectory ran without failed/skipped steps): it verifies (1) the state
# actually performed a real interaction verb, (2) the post-interaction terminal
# state satisfies DOM/URL expectations, and (3) optionally that a "delta"
# assertion is FALSE in a before-state and TRUE in the after-state (proving the
# interaction changed the observable terminal state, not a static match).
#
# Extensible: interaction verbs live in _INTERACTION_VERBS; terminal-assertion
# kinds (dom/url) are dispatched in a small table — add a kind to extend.
# ---------------------------------------------------------------------------

_INTERACTION_VERBS = {"click", "type", "fill", "press", "submit", "select", "hover", "drag", "check"}


# A successful captured action is written as "succeeded" by the reference capture
# path but as "passed" by tools/playwright_capture.js (the evaluate-run path).
# Both denote a real, executed interaction — accept either, else every solver
# submission fails D4 with "no_real_interaction_in_trajectory" on a token mismatch.
_INTERACTION_OK_STATUS = {"succeeded", "passed"}


def _actions_have_interaction(actions: list[dict] | None) -> bool:
    """True if the trajectory contains a real (non-goto/scroll) interaction verb."""
    for action in actions or []:
        if action.get("type") in _INTERACTION_VERBS and action.get("status") in _INTERACTION_OK_STATUS:
            return True
    return False


def _dom_rule_matched(states_root: Path, repo_id: str, state_id: str, rule: dict) -> tuple[bool, int]:
    """Evaluate a dom-style rule on a state's captured boxes -> (ok, matched)."""
    metrics = state_metrics(states_root, repo_id, state_id)
    if not metrics:
        return False, -1
    boxes = _candidate_boxes(metrics, rule)
    matched = sum(1 for box in boxes if _box_matches(box, rule))
    if "exact_count" in rule:
        return matched == int(rule["exact_count"]), matched
    return matched >= int(rule.get("min_count", 1)), matched


def check_interaction_assertions(task: dict, spec: dict, states_root: Path) -> CheckResult:
    """D4: assert the post-interaction terminal state of an interaction trajectory.

    Opt-in: no-op unless the spec supplies ``interaction_assertions``.

    Each assertion:
      {state,                       # the interaction (after) state; its actions
                                    #   MUST include a real interaction verb
       require_interaction? (default True),
       dom? {tag?,role?,class_contains?,text_contains?,min_count?,exact_count?},
       url_contains?,               # post-interaction metrics.url must contain
       changed_from?}               # optional before-state: the `dom` rule must
                                    #   be FALSE there and TRUE in `state`
                                    #   (proves the interaction caused the change)
    """
    assertions = spec.get("interaction_assertions")
    if not assertions:
        return CheckResult("interaction_assertions", True, "no interaction assertions configured")
    repo_id = task["repo_id"]
    failures: list[dict] = []
    results: list[dict] = []
    for idx, rule in enumerate(assertions):
        state_id = rule.get("state")
        record: dict[str, Any] = {"index": idx, "state_id": state_id}
        # (1) the state's trajectory must contain a real interaction verb
        if rule.get("require_interaction", True):
            actions = state_actions(states_root, repo_id, state_id)
            if actions is None:
                failures.append({**record, "reason": "missing_actions_json"})
                continue
            if not _actions_have_interaction(actions):
                failures.append({**record, "reason": "no_real_interaction_in_trajectory"})
                continue
            record["interaction_verbs"] = [a.get("type") for a in actions if a.get("type") in _INTERACTION_VERBS]
        metrics = state_metrics(states_root, repo_id, state_id)
        if not metrics:
            failures.append({**record, "reason": "missing_metrics"})
            continue
        ok = True
        # (2a) post-interaction DOM assertion on the terminal state
        dom_rule = rule.get("dom")
        if dom_rule:
            dom_ok, matched = _dom_rule_matched(states_root, repo_id, state_id, dom_rule)
            record["dom_matched"] = matched
            ok = ok and dom_ok
            # (3) delta: the same rule must NOT hold in the before-state
            before = rule.get("changed_from")
            if before:
                before_ok, before_matched = _dom_rule_matched(states_root, repo_id, before, dom_rule)
                record["before_state"] = before
                record["before_matched"] = before_matched
                # interaction must have CAUSED the change: false before, true after
                ok = ok and dom_ok and not before_ok
        # (2b) post-interaction URL terminal assertion
        url_contains = rule.get("url_contains")
        if url_contains:
            url = str(metrics.get("url", ""))
            url_ok = url_contains in url
            record["url"] = url
            ok = ok and url_ok
        record["ok"] = ok
        results.append(record)
        if not ok:
            failures.append(record)
    return CheckResult(
        name="interaction_assertions",
        passed=not failures,
        message="all interaction-trajectory terminal assertions satisfied"
        if not failures
        else "interaction-trajectory terminal assertion failed",
        details={"results": results, "failures": failures},
    )


def verify_task(task: dict, spec: dict, states_root: Path) -> dict:
    reference_spec = dict(spec)
    if "reference_overflow_tolerance_px" in spec:
        reference_spec["overflow_tolerance_px"] = spec["reference_overflow_tolerance_px"]
    checks = [
        check_required_states(task, spec, states_root),
        check_state_artifacts(task, spec, states_root),
        check_state_quality(task, spec, states_root),
        check_action_history(task, spec, states_root),
        check_screenshot_files(task, spec, states_root),
        check_no_horizontal_overflow(task, reference_spec, states_root),
        check_text_signals(task, spec, states_root),
        check_visible_text_signals(task, spec, states_root),
        check_state_url_signals(task, spec, states_root),
        check_image_alt_signals(task, spec, states_root),
        check_asset_signals(task, spec, states_root),
        check_exact_asset_path_signals(task, spec, states_root),
        check_console_errors(task, {"reference_states": spec.get("reference_states", []), **spec}, states_root),
        check_dom_assertions(task, spec, states_root),
    ]
    return {
        "task_id": task["task_id"],
        "repo_id": task["repo_id"],
        "passed": all(check.passed for check in checks),
        "checks": [check.__dict__ for check in checks],
    }


def check_submission_completion(task: dict, spec: dict, states_root: Path) -> CheckResult:
    repo_id = task["repo_id"]
    signals = spec.get("completion_text_signals", [])
    if not signals:
        return CheckResult("completion_text_signals", True, "no completion text signals configured")
    states = spec.get("completion_states") or spec.get("submission_states") or task.get("required_states", [])
    combined = collect_state_text(states_root, repo_id, states)
    missing = contains_all_text(combined, signals)
    min_found = spec.get("min_completion_text_signals", len(signals))
    found_count = len(signals) - len(missing)
    passed = found_count >= min_found and not missing if spec.get("require_all_completion_signals", True) else found_count >= min_found
    return CheckResult(
        name="completion_text_signals",
        passed=passed,
        message="submission contains required completion signals" if passed else "submission is missing completion signals",
        details={
            "states": states,
            "signals": signals,
            "missing": missing,
            "found_count": found_count,
            "min_found": min_found,
            "require_all": spec.get("require_all_completion_signals", True),
        },
    )


def check_submission_regression_text(task: dict, spec: dict, states_root: Path) -> CheckResult:
    repo_id = task["repo_id"]
    signals = spec.get("regression_text_signals", [])
    if not signals:
        return CheckResult("regression_text_signals", True, "no regression text signals configured")
    states = spec.get("regression_states") or spec.get("submission_states") or task.get("required_states", [])
    combined = collect_state_text(states_root, repo_id, states)
    missing = contains_all_text(combined, signals)
    return CheckResult(
        name="regression_text_signals",
        passed=not missing,
        message="baseline content signals remain present" if not missing else "baseline content signals missing",
        details={"states": states, "signals": signals, "missing": missing},
    )


def check_forbidden_text(task: dict, spec: dict, states_root: Path) -> CheckResult:
    repo_id = task["repo_id"]
    forbidden = spec.get("forbidden_text_patterns", [])
    if not forbidden:
        return CheckResult("forbidden_text_patterns", True, "no forbidden text configured")
    states = spec.get("forbidden_states") or spec.get("completion_states") or spec.get("submission_states") or task.get("required_states", [])
    combined = collect_state_text(states_root, repo_id, states).lower()
    found = [pattern for pattern in forbidden if pattern.lower() in combined]
    return CheckResult(
        name="forbidden_text_patterns",
        passed=not found,
        message="no forbidden placeholder text found" if not found else "forbidden placeholder text found",
        details={"states": states, "forbidden": forbidden, "found": found},
    )


def check_asset_path_signals(task: dict, spec: dict, states_root: Path) -> CheckResult:
    repo_id = task["repo_id"]
    required = spec.get("asset_path_signals", [])
    if not required:
        return CheckResult("asset_path_signals", True, "no asset path signals configured")
    states = spec.get("asset_signal_states") or spec.get("submission_states") or task.get("required_states", [])
    asset_paths = []
    for state_id in states:
        metrics = state_metrics(states_root, repo_id, state_id)
        if not metrics:
            continue
        for image in metrics.get("images", []):
            src = image.get("src")
            if src:
                asset_paths.append(unquote(src))
        for link in metrics.get("links", []):
            href = link.get("href")
            if href:
                asset_paths.append(unquote(href))
    diff_text = ""
    diff_path = states_root.parent / "applied.diff"
    if diff_path.exists():
        diff_text = diff_path.read_text(encoding="utf-8", errors="ignore").lower()
    found = []
    source_found = []
    ignored = []
    missing = []
    for signal in required:
        matchable = browser_asset_signal(signal)
        if asset_signal_matches(signal, asset_paths):
            found.append(signal)
        elif source_asset_signal_matches(signal, diff_text):
            source_found.append(signal)
        elif not matchable:
            ignored.append(signal)
        else:
            missing.append(signal)
    min_found = spec.get("min_asset_path_signals")
    if min_found is None:
        min_found = 1 if required and len(ignored) < len(required) else 0
    found_count = len(found) + len(source_found)
    passed = found_count >= min_found
    return CheckResult(
        name="asset_path_signals",
        passed=passed,
        message="required asset path signals found" if passed else "required asset path signals missing",
        details={
            "states": states,
            "signals": required,
            "found": found,
            "source_found": source_found,
            "ignored_source_signals": ignored,
            "missing": missing,
            "found_count": found_count,
            "min_found": min_found,
            "sample_paths": asset_paths[:20],
        },
    )


def check_exact_asset_path_signals(task: dict, spec: dict, states_root: Path) -> CheckResult:
    repo_id = task["repo_id"]
    required = spec.get("exact_asset_path_signals", [])
    if not required:
        return CheckResult("exact_asset_path_signals", True, "no exact asset path signals configured")
    states = spec.get("asset_signal_states") or spec.get("submission_states") or task.get("required_states", [])
    paths = set()
    for state_id in states:
        metrics = state_metrics(states_root, repo_id, state_id)
        if not metrics:
            continue
        for image in metrics.get("images", []):
            src = image.get("src")
            if src:
                parsed = urlparse(unquote(src))
                paths.add(parsed.path or unquote(src))
    normalized_required = [signal if signal.startswith("/") else f"/{signal}" for signal in required]
    found = [signal for signal in normalized_required if signal in paths]
    missing = [signal for signal in normalized_required if signal not in paths]
    min_found = spec.get("min_exact_asset_path_signals", len(normalized_required))
    passed = len(found) >= min_found
    return CheckResult(
        name="exact_asset_path_signals",
        passed=passed,
        message="exact asset path signals found" if passed else "exact asset path signals missing",
        details={
            "states": states,
            "signals": normalized_required,
            "found": found,
            "missing": missing,
            "found_count": len(found),
            "min_found": min_found,
            "sample_paths": sorted(paths)[:20],
        },
    )


def browser_asset_signal(signal: str) -> bool:
    lowered = signal.lower().strip()
    source_prefixes = ("src/", "resources/", "content/", "app/", "components/", "pages/")
    source_suffixes = (
        ".astro",
        ".blade.php",
        ".css",
        ".html",
        ".js",
        ".jsx",
        ".json",
        ".md",
        ".php",
        ".scss",
        ".ts",
        ".tsx",
        ".vue",
    )
    if lowered.startswith(source_prefixes):
        return False
    if lowered.endswith(source_suffixes):
        return False
    return True


def asset_signal_tokens(signal: str) -> list[str]:
    signal = unquote(signal).lower().strip()
    signal = signal.replace("public/", "").replace("src/assets/", "assets/")
    signal = signal.replace("*", "")
    signal = signal.strip("/")
    tokens = [signal] if signal else []
    leaf = signal.rsplit("/", 1)[-1]
    if leaf and leaf not in tokens:
        tokens.append(leaf)
    stem = leaf.rsplit(".", 1)[0] if "." in leaf else leaf
    if stem and stem not in tokens:
        tokens.append(stem)
    return [token for token in tokens if len(token) >= 3]


def asset_signal_matches(signal: str, asset_paths: list[str]) -> bool:
    normalized_paths = []
    for path in asset_paths:
        lowered = unquote(path).lower()
        lowered = lowered.replace("%20", " ")
        normalized_paths.append(lowered)
    for token in asset_signal_tokens(signal):
        if any(token in path for path in normalized_paths):
            return True
    return False


def source_asset_signal_matches(signal: str, diff_text: str) -> bool:
    if not diff_text:
        return False
    normalized = unquote(signal).lower().strip().replace("public/", "")
    normalized = normalized.strip()
    directory_signal = normalized.strip("/")
    if "/" in normalized and directory_signal and "." not in directory_signal.rsplit("/", 1)[-1]:
        directory_prefix = "/" + directory_signal.rstrip("/") + "/"
        if directory_prefix in diff_text:
            return True
    generic_tokens = {"assets", "images", "media", "resources", "static", "public"}
    tokens = [token for token in asset_signal_tokens(signal) if token not in generic_tokens]
    return any(token in diff_text for token in tokens)


def build_anchor_lookup(design_anchors: dict | None) -> dict[str, dict]:
    if not design_anchors:
        return {}
    return {item["task_id"]: item for item in design_anchors.get("items", [])}


def palette_set(summary: dict, limit: int) -> set[str]:
    return {item["hex"] for item in summary.get("palette", [])[:limit] if item.get("hex")}


def check_visual_anchor_similarity(
    task: dict,
    spec: dict,
    states_root: Path,
    anchor_lookup: dict[str, dict],
) -> CheckResult:
    if not anchor_lookup:
        return CheckResult("visual_anchor_similarity", True, "no design anchors supplied")
    anchor = anchor_lookup.get(task["task_id"])
    if not anchor:
        return CheckResult("visual_anchor_similarity", False, "missing task design anchor")

    reference_by_state = {
        screenshot["state_id"]: screenshot
        for screenshot in anchor.get("screenshots", [])
        if screenshot.get("available")
    }
    states = spec.get("visual_anchor_states") or spec.get("submission_states") or task.get("required_states", [])
    skipped_states: list[str] = []
    if task.get("intent") == "repair" and not spec.get("anchor_repair_problem_states", False):
        original_states = list(states)
        states = [state_id for state_id in original_states if state_id.startswith("desktop_")]
        skipped_states = [state_id for state_id in original_states if state_id not in states]
        if not states:
            states = original_states
            skipped_states = []
    luminance_tolerance = spec.get("mean_luminance_tolerance", 65)
    min_palette_overlap = spec.get("min_palette_overlap", 1)
    palette_limit = spec.get("palette_compare_limit", 5)
    failures = []
    comparisons = {}

    for state_id in states:
        reference = reference_by_state.get(state_id)
        current = screenshot_summary(screenshot_path(states_root, task["repo_id"], state_id))
        if not reference:
            failures.append({"state_id": state_id, "reason": "missing_reference_visual_anchor"})
            continue
        if not current.get("available"):
            failures.append({"state_id": state_id, "reason": current.get("reason", "missing_submission_screenshot")})
            continue
        luminance_delta = abs(current.get("mean_luminance", 0) - reference.get("mean_luminance", 0))
        palette_overlap = len(palette_set(current, palette_limit) & palette_set(reference, palette_limit))
        comparisons[state_id] = {
            "reference_luminance": reference.get("mean_luminance"),
            "submission_luminance": current.get("mean_luminance"),
            "luminance_delta": round(luminance_delta, 2),
            "palette_overlap": palette_overlap,
            "reference_palette": reference.get("palette", [])[:palette_limit],
            "submission_palette": current.get("palette", [])[:palette_limit],
        }
        if luminance_delta > luminance_tolerance:
            failures.append(
                {
                    "state_id": state_id,
                    "reason": "luminance_drift",
                    "luminance_delta": round(luminance_delta, 2),
                    "tolerance": luminance_tolerance,
                }
            )
        if palette_overlap < min_palette_overlap:
            failures.append(
                {
                    "state_id": state_id,
                    "reason": "palette_drift",
                    "palette_overlap": palette_overlap,
                    "min_palette_overlap": min_palette_overlap,
                }
            )

    return CheckResult(
        name="visual_anchor_similarity",
        passed=not failures,
        message="submission remains visually close to reference anchors" if not failures else "visual anchor drift detected",
        details={
            "states": states,
            "skipped_repair_states": skipped_states,
            "luminance_tolerance": luminance_tolerance,
            "min_palette_overlap": min_palette_overlap,
            "failures": failures,
            "comparisons": comparisons,
        },
    )


# ---------------------------------------------------------------------------
# D3 visual-fidelity gate (check_visual_regression)
#
# Extensible metric registry: each metric is a callable that takes the resolved
# reference/submission screenshot paths + the metric's spec config + the block
# list (component crops for this state), and returns (passed, detail_dict).
# To add a new metric (e.g. CLIP, full Design2Code), register one function in
# _VISUAL_METRICS — the gate body needs no change. Open for extension, closed
# for modification.
# ---------------------------------------------------------------------------


def _metric_pixelmatch(ref_path, sub_path, cfg, blocks, y_shift=0):
    result = pixel_diff_ratio(
        ref_path,
        sub_path,
        tolerance=int(cfg.get("tolerance", 30)),
        mask_regions=cfg.get("mask_regions"),
        y_shift=y_shift,
    )
    if not result.get("available"):
        return False, result
    max_ratio = float(cfg.get("max_diff_ratio", 0.08))
    result["max_diff_ratio"] = max_ratio
    return result.get("diff_ratio", 1.0) <= max_ratio, result


def _metric_ssim(ref_path, sub_path, cfg, blocks, y_shift=0):
    result = ssim_score(ref_path, sub_path, y_shift=y_shift)
    if not result.get("available"):
        return False, result
    min_ssim = float(cfg.get("min_score", 0.85))
    result["min_score"] = min_ssim
    return result.get("ssim", 0.0) >= min_ssim, result


def _metric_block_fidelity(ref_path, sub_path, cfg, blocks, y_shift=0):
    result = block_fidelity_score(ref_path, sub_path, blocks or [], y_shift=y_shift)
    if not result.get("available"):
        return False, result
    min_match = float(cfg.get("min_block_match", 0.8))
    max_delta_e = float(cfg.get("max_color_delta_e", 15.0))
    result["min_block_match"] = min_match
    result["max_color_delta_e_allowed"] = max_delta_e
    ok = result.get("block_match", 0.0) >= min_match and result.get("max_color_delta_e", 999) <= max_delta_e
    return ok, result


# metric name -> callable. Extend here to add new deterministic visual metrics.
_VISUAL_METRICS = {
    "pixelmatch": _metric_pixelmatch,
    "ssim": _metric_ssim,
    "block_fidelity": _metric_block_fidelity,
}


def _blocks_for_state(anchor: dict, state_id: str) -> list[dict]:
    """Flatten this anchor's component-crop rects for a state into block dicts.

    State scoping: `state_id` lives on the CROP group (each entry of
    `component_crops` carries its own `state_id`); the per-sample dicts do NOT
    (they only have kind/path/rect/size_bytes/text). The historical code filtered
    on `sample.get("state_id")`, which is always None → the filter never fired →
    every state was compared against the union of ALL crops' rects (rects from
    other viewports/states leaking in), so block_fidelity's block_match was
    systematically 0. We now scope by the crop's state_id, falling back to a
    sample-level state_id if a future anchor schema ever carries one. When neither
    level declares a state_id we keep the block (older single-state anchors).
    """
    blocks: list[dict] = []
    for crop in anchor.get("component_crops", []) or []:
        crop_state = crop.get("state_id")
        if crop_state and crop_state != state_id:
            continue
        for sample in crop.get("samples", []) or []:
            sample_state = sample.get("state_id")
            if sample_state and sample_state != state_id:
                continue
            rect = sample.get("rect") or {}
            if not rect:
                continue
            blocks.append(
                {
                    "x": rect.get("x", 0),
                    "y": rect.get("y", 0),
                    "width": rect.get("width", rect.get("w", 0)),
                    "height": rect.get("height", rect.get("h", 0)),
                    "kind": sample.get("kind"),
                }
            )
    return blocks


def check_visual_regression(
    task: dict,
    spec: dict,
    states_root: Path,
    anchor_lookup: dict[str, dict] | None = None,
) -> CheckResult:
    """D3: deterministic per-pixel / structural / block visual-fidelity gate.

    Opt-in: a spec WITHOUT a `visual_regression` block passes trivially, so the
    existing frozen slots that don't configure it are unaffected. When present:
      visual_regression: {
        states: [state_id, ...],            # default: submission_states
        metrics: { pixelmatch:{max_diff_ratio,tolerance,mask_regions},
                   ssim:{min_score},
                   block_fidelity:{min_block_match,max_color_delta_e} }
      }
    Reference screenshots come from design_anchors (same source as the anchor
    gate); submission screenshots come from states_root. Fails if any configured
    metric fails on any configured state.
    """
    config = spec.get("visual_regression")
    if not config:
        return CheckResult("visual_regression", True, "visual-regression gate not configured")

    anchor = (anchor_lookup or {}).get(task["task_id"])
    if not anchor:
        return CheckResult("visual_regression", False, "missing task design anchor for visual regression")

    reference_by_state = {
        shot["state_id"]: shot
        for shot in anchor.get("screenshots", [])
        if shot.get("available") and shot.get("path")
    }
    metrics_cfg = config.get("metrics") or {}
    active_metrics = [name for name in metrics_cfg if name in _VISUAL_METRICS]
    if not active_metrics:
        return CheckResult("visual_regression", False, "no known visual metrics configured", details={"configured": list(metrics_cfg)})

    states = config.get("states") or spec.get("submission_states") or task.get("required_states", [])
    failures: list[dict] = []
    comparisons: dict[str, dict] = {}

    for state_id in states:
        ref = reference_by_state.get(state_id)
        if not ref:
            failures.append({"state_id": state_id, "reason": "missing_reference_screenshot"})
            continue
        ref_path = Path(ref["path"])
        sub_path = screenshot_path(states_root, task["repo_id"], state_id)
        blocks = _blocks_for_state(anchor, state_id)
        # Align on the recorded scroll delta: the reference and submission may
        # have captured the SAME document at different scroll offsets (a capture
        # -recipe artifact — e.g. reference frozen under a goto recipe that
        # landed scrolled, submission under scroll_to_selector that landed at
        # top). y_shift = ref_scroll_y - sub_scroll_y is ground-truth metadata,
        # not a fitted offset, so a genuine regression cannot be aligned away.
        ref_scroll_y = _scroll_y_from_metrics(ref_path.parent / "metrics.json")
        sub_metrics = state_metrics(states_root, task["repo_id"], state_id) or {}
        sub_scroll_y = 0
        try:
            sub_scroll_y = int((sub_metrics.get("scroll") or {}).get("y", 0) or 0)
        except (TypeError, ValueError):
            sub_scroll_y = 0
        y_shift = ref_scroll_y - sub_scroll_y
        state_report: dict[str, Any] = {}
        if y_shift:
            state_report["_scroll_align"] = {
                "ref_scroll_y": ref_scroll_y,
                "sub_scroll_y": sub_scroll_y,
                "y_shift": y_shift,
            }
        for name in active_metrics:
            passed, detail = _VISUAL_METRICS[name](
                ref_path, sub_path, metrics_cfg[name] or {}, blocks, y_shift
            )
            state_report[name] = detail
            if not passed:
                failures.append({"state_id": state_id, "metric": name, "detail": detail})
        comparisons[state_id] = state_report

    return CheckResult(
        name="visual_regression",
        passed=not failures,
        message="submission visually matches reference within tolerance"
        if not failures
        else "visual regression detected",
        details={
            "states": list(states),
            "metrics": active_metrics,
            "failures": failures,
            "comparisons": comparisons,
        },
    )


def verify_submission_task(
    task: dict,
    spec: dict,
    states_root: Path,
    anchor_lookup: dict[str, dict] | None = None,
) -> dict:
    checks = [
        check_required_states(task, {"reference_states": spec.get("submission_states", [])}, states_root),
        check_state_artifacts(task, {"reference_states": spec.get("submission_states", []), **spec}, states_root),
        check_state_quality(task, {"reference_states": spec.get("submission_states", []), **spec}, states_root),
        check_action_history(task, {"reference_states": spec.get("submission_states", []), **spec}, states_root),
        check_screenshot_files(task, {"reference_states": spec.get("submission_states", []), **spec}, states_root),
        check_no_horizontal_overflow(task, spec, states_root),
        check_submission_completion(task, spec, states_root),
        check_visible_text_signals(task, spec, states_root),
        check_state_url_signals(task, spec, states_root),
        check_submission_regression_text(task, spec, states_root),
        check_forbidden_text(task, spec, states_root),
        check_image_alt_signals(task, spec, states_root),
        check_asset_path_signals(task, spec, states_root),
        check_exact_asset_path_signals(task, spec, states_root),
        check_visual_anchor_similarity(task, spec, states_root, anchor_lookup or {}),
        check_visual_regression(task, spec, states_root, anchor_lookup or {}),
        check_console_errors(task, spec, states_root),
        check_dom_assertions(task, spec, states_root),
        check_interaction_assertions(task, spec, states_root),
    ]
    return {
        "task_id": task["task_id"],
        "repo_id": task["repo_id"],
        "passed": all(check.passed for check in checks),
        "checks": [check.__dict__ for check in checks],
    }


def verify_specs(tasks_path: Path, specs_path: Path, states_root: Path, output_path: Path) -> dict:
    tasks = load_task_map(tasks_path)
    specs = load_json(specs_path)
    results = []
    for spec in specs.get("tasks", []):
        task_id = spec["task_id"]
        if task_id not in tasks:
            results.append({"task_id": task_id, "passed": False, "checks": [], "error": "task_missing"})
            continue
        results.append(verify_task(tasks[task_id], spec, states_root))
    summary = {
        "tasks_path": str(tasks_path),
        "specs_path": str(specs_path),
        "states_root": str(states_root),
        "total": len(results),
        "passed": sum(1 for result in results if result.get("passed")),
        "failed": sum(1 for result in results if not result.get("passed")),
        "results": results,
    }
    write_json(output_path, summary)
    return summary


def verify_submission_specs(
    tasks_path: Path,
    specs_path: Path,
    states_root: Path,
    output_path: Path,
    design_anchors_path: Path | None = None,
) -> dict:
    tasks = load_task_map(tasks_path)
    specs = load_json(specs_path)
    design_anchors = load_json(design_anchors_path) if design_anchors_path else None
    anchor_lookup = build_anchor_lookup(design_anchors)
    results = []
    for spec in specs.get("tasks", []):
        task_id = spec["task_id"]
        if task_id not in tasks:
            results.append({"task_id": task_id, "passed": False, "checks": [], "error": "task_missing"})
            continue
        results.append(verify_submission_task(tasks[task_id], spec, states_root, anchor_lookup))
    summary = {
        "tasks_path": str(tasks_path),
        "specs_path": str(specs_path),
        "states_root": str(states_root),
        "design_anchors_path": str(design_anchors_path) if design_anchors_path else None,
        "total": len(results),
        "passed": sum(1 for result in results if result.get("passed")),
        "failed": sum(1 for result in results if not result.get("passed")),
        "results": results,
    }
    write_json(output_path, summary)
    return summary


def run_verifier(
    task_path: Path,
    spec_path: Path,
    states_root: Path,
    output_dir: Path,
    *,
    design_anchors_path: Path | None = None,
    reference_root: Path | None = None,
    submission_workspace_root: Path | None = None,
    reference_workspace_root: Path | None = None,
    audit_max_changed_files: int = 40,
    audit_max_added_files: int = 20,
    audit_max_deleted_files: int = 8,
    audit_max_line_delta: int = 1800,
    audit_allow_dependency_additions: bool = False,
) -> dict:
    """Stable container-friendly entry point for out-of-tree adapters (harbor et al).

    Runs the submission verifier against pre-captured states and (optionally) chains the canonical
    8-metric scorer. Returns a dict shaped for adapter consumption; also writes the two artifact
    files harbor's task template expects.

    Args:
        task_path: path to task.jsonl (single-task or multi-task).
        spec_path: path to submission_specs.json (`{"tasks":[spec,...]}`).
        states_root: root directory of pre-captured states (per-repo subdirs). The caller is
            responsible for running `sitecontinuum capture-states` beforehand — this function
            does NOT trigger capture (capture needs playwright/chromium + a live dev server and
            is best kept as a separate stage in the adapter's `tests/test.sh`).
        output_dir: where to write `verifier_report.json` and `score.json`.
        design_anchors_path: optional design anchors JSON (visual anchor / regression gates).
            If provided AND `reference_root` is given, anchor paths that start with a legacy
            absolute host prefix are rewritten to `reference_root/`-relative form so the file
            works portably inside containers.
        reference_root: optional container-local reference root; used for path rewriting only.

    Returns:
        {
            "passed":  bool,                    # WCS pass/fail (all-gate AND)
            "report":  {...},                   # full verifier summary (matches submission_results.json)
            "score":   {...},                   # score_result output — WCS + 8 continuity metrics
            "metrics": {"WCS":float, "CCS":float, ..., "BES":float},  # flat metric values for harbor
        }

    Reward-file convention for harbor's test.sh:
        Caller writes "1.0" / "0.0" to /logs/verifier/reward.txt based on `result["passed"]`.
    """
    # Deferred imports — avoid a circular during module load in edge cases.
    from ..scoring.metrics import METRIC_NAMES, score_result
    from .source_audit import audit_task_source

    ensure_dir(output_dir)
    report_path = output_dir / "verifier_report.json"
    score_path = output_dir / "score.json"

    # Optional: rewrite design-anchor absolute host paths to a portable container-relative form.
    # The legacy anchors JSON stores absolute paths like
    #   data/productwebbench/states_slot_XXX_reference/...
    # which don't exist inside a container. If reference_root is passed, we substitute the
    # `.../states_slot_XXX_reference/` prefix with reference_root/ and write the fixed file to
    # output_dir so the original stays untouched.
    effective_anchors = design_anchors_path
    if design_anchors_path is not None and reference_root is not None and design_anchors_path.exists():
        try:
            anchors = json.loads(design_anchors_path.read_text())
            for shot in anchors.get("screenshots", []):
                p = shot.get("path")
                if not p:
                    continue
                # locate the ".../states_slot_XXX_reference/" segment
                marker = "_reference/"
                idx = p.find(marker)
                if idx > 0:
                    rel = p[idx + len(marker):]
                    shot["path"] = str(reference_root / rel)
            effective_anchors = output_dir / "design_anchors.portable.json"
            effective_anchors.write_text(json.dumps(anchors))
        except (OSError, json.JSONDecodeError):
            # If rewriting fails, fall through to the original path — verifier will surface the
            # real error rather than us swallowing it.
            effective_anchors = design_anchors_path

    summary = verify_submission_specs(
        tasks_path=task_path,
        specs_path=spec_path,
        states_root=states_root,
        output_path=report_path,
        design_anchors_path=effective_anchors,
    )

    # Attach `source_change_audit` check per result so ICS is computable.
    # Runs iff both workspace roots are provided; otherwise ICS stays "unavailable"
    # (score_result treats a missing check as bool_score(None) → 0.0 today; when
    # workspaces are unavailable at the call site we prefer failing loudly here rather
    # than silently emitting a 0).
    if submission_workspace_root is not None and reference_workspace_root is not None:
        tasks_by_id: dict[str, dict] = {}
        try:
            tasks_list = json.loads(Path(task_path).read_text())
            if isinstance(tasks_list, dict):
                tasks_list = tasks_list.get("tasks", [tasks_list])
        except json.JSONDecodeError:
            tasks_list = [json.loads(line) for line in Path(task_path).read_text().splitlines() if line.strip()]
        for t in tasks_list:
            tasks_by_id[t["task_id"]] = t
        for r in summary.get("results", []):
            task = tasks_by_id.get(r.get("task_id"))
            if task is None:
                continue
            try:
                audit = audit_task_source(
                    task,
                    reference_workspace_root=Path(reference_workspace_root),
                    submission_workspace_root=Path(submission_workspace_root),
                    max_changed_files=audit_max_changed_files,
                    max_added_files=audit_max_added_files,
                    max_deleted_files=audit_max_deleted_files,
                    max_line_delta=audit_max_line_delta,
                    allow_dependency_additions=audit_allow_dependency_additions,
                )
            except Exception as exc:  # noqa: BLE001 — surface via check, don't crash verifier
                audit = {"passed": False, "errors": [f"audit failed: {exc}"]}
            audit_check = audit.get("checks", [{}])[0] if audit.get("checks") else {
                "name": "source_change_audit",
                "passed": bool(audit.get("passed")),
                "message": ("source audit failed" if not audit.get("passed") else "source audit passed"),
                "details": audit,
            }
            r.setdefault("checks", []).append(audit_check)

    # Score every result and pick the first (harbor tasks are single-slot, but the shim handles
    # multi-task specs too so adapters can batch if they choose).
    scores = [score_result(r) for r in summary.get("results", [])]
    write_json(score_path, {"scores": scores, "summary_stats": {
        "total": summary["total"], "passed": summary["passed"], "failed": summary["failed"],
    }})

    head_score = scores[0] if scores else None
    flat_metrics: dict[str, float] = {}
    if head_score is not None:
        flat_metrics["WCS"] = head_score["WCS"]["score"]
        for name in METRIC_NAMES:
            entry = head_score["metrics"].get(name)
            if entry and entry.get("available"):
                flat_metrics[name] = entry["score"]

    return {
        "passed": bool(summary["passed"] == summary["total"] and summary["total"] > 0),
        "report": summary,
        "score": head_score,
        "metrics": flat_metrics,
    }


def add_verify_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tasks", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.jsonl")
    parser.add_argument("--specs", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed_verifier_specs.json")
    parser.add_argument("--states-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "states")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.verifier_report.json")


def run_verify_from_args(args: argparse.Namespace) -> None:
    ensure_dir(args.output.parent)
    summary = verify_specs(args.tasks, args.specs, args.states_root, args.output)
    print(f"verified {summary['total']} specs: {summary['passed']} passed, {summary['failed']} failed")


def add_verify_submission_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tasks", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.jsonl")
    parser.add_argument(
        "--specs",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed_submission_specs.json",
    )
    parser.add_argument("--states-root", type=Path, required=True)
    parser.add_argument("--design-anchors", type=Path, default=None)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.submission_verifier_report.json",
    )


def run_verify_submission_from_args(args: argparse.Namespace) -> None:
    ensure_dir(args.output.parent)
    summary = verify_submission_specs(args.tasks, args.specs, args.states_root, args.output, args.design_anchors)
    print(f"verified {summary['total']} submissions: {summary['passed']} passed, {summary['failed']} failed")
