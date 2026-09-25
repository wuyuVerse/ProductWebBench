from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from ...core.config import DEFAULT_OUTPUT_ROOT
from ...core.io_utils import read_jsonl, write_json
from ...core.visual import screenshot_summary


TRANSPARENT_COLORS = {
    "rgba(0, 0, 0, 0)",
    "transparent",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_metrics(states_root: Path, repo_id: str, state_id: str) -> dict[str, Any] | None:
    path = states_root / repo_id / state_id / "metrics.json"
    if not path.exists():
        return None
    return load_json(path)


def load_crops(states_root: Path, repo_id: str, state_id: str) -> list[dict[str, Any]]:
    path = states_root / repo_id / state_id / "crops.json"
    if not path.exists():
        return []
    data = load_json(path)
    return data if isinstance(data, list) else []


def normalize_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def add_counter(counter: Counter[str], value: Any) -> None:
    normalized = normalize_value(value)
    if normalized:
        counter[normalized] += 1


def top(counter: Counter[str], limit: int = 12) -> list[dict[str, Any]]:
    return [{"value": value, "count": count} for value, count in counter.most_common(limit)]


def collect_style_tokens(design: dict[str, Any], counters: dict[str, Counter[str]]) -> None:
    body = design.get("body", {})
    add_counter(counters["font_families"], body.get("font_family"))
    add_counter(counters["font_sizes"], body.get("font_size"))
    add_counter(counters["font_weights"], body.get("font_weight"))
    add_counter(counters["radii"], body.get("border_radius"))

    for sample in design.get("color_samples", []):
        add_counter(counters["text_colors"], sample.get("color"))
        background = normalize_value(sample.get("background_color"))
        if background and background not in TRANSPARENT_COLORS:
            counters["background_colors"][background] += 1
        border = normalize_value(sample.get("border_color"))
        if border and border not in TRANSPARENT_COLORS:
            counters["border_colors"][border] += 1
        shadow = normalize_value(sample.get("box_shadow"))
        if shadow and shadow != "none":
            counters["shadows"][shadow] += 1

    for group in ("headings", "buttons", "cards", "nav_items"):
        for item in design.get(group, []):
            style = item.get("style", {})
            add_counter(counters["font_families"], style.get("font_family"))
            add_counter(counters["font_sizes"], style.get("font_size"))
            add_counter(counters["font_weights"], style.get("font_weight"))
            add_counter(counters["radii"], style.get("border_radius"))
            shadow = normalize_value(style.get("box_shadow"))
            if shadow and shadow != "none":
                counters["shadows"][shadow] += 1


def compact_samples(samples: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    compacted = []
    seen = set()
    for sample in samples:
        key = (
            sample.get("tag"),
            sample.get("text"),
            sample.get("class_name"),
            tuple((sample.get("rect") or {}).items()),
        )
        if key in seen:
            continue
        seen.add(key)
        compacted.append(sample)
        if len(compacted) >= limit:
            break
    return compacted


def build_task_anchor(task: dict[str, Any], states_root: Path, state_ids: list[str], sample_limit: int) -> dict[str, Any]:
    repo_id = task["repo_id"]
    counters: dict[str, Counter[str]] = {
        "text_colors": Counter(),
        "background_colors": Counter(),
        "border_colors": Counter(),
        "font_families": Counter(),
        "font_sizes": Counter(),
        "font_weights": Counter(),
        "radii": Counter(),
        "shadows": Counter(),
    }
    state_summaries = []
    button_samples = []
    card_samples = []
    heading_samples = []
    nav_samples = []
    screenshot_summaries = []
    crop_summaries = []
    missing_design_states = []

    for state_id in state_ids:
        screenshot_summaries.append(
            {"state_id": state_id, **screenshot_summary(states_root / repo_id / state_id / "screenshot.png")}
        )
        crops = load_crops(states_root, repo_id, state_id)
        crop_kind_counts = Counter(crop.get("kind", "unknown") for crop in crops if not crop.get("error"))
        crop_summaries.append(
            {
                "state_id": state_id,
                "crop_count": sum(crop_kind_counts.values()),
                "kinds": dict(sorted(crop_kind_counts.items())),
                "samples": [
                    {
                        "kind": crop.get("kind"),
                        "path": crop.get("path"),
                        "text": crop.get("text", ""),
                        "rect": crop.get("rect", {}),
                        "size_bytes": crop.get("size_bytes"),
                    }
                    for crop in crops
                    if not crop.get("error")
                ][:12],
            }
        )
        metrics = load_metrics(states_root, repo_id, state_id)
        if metrics is None:
            state_summaries.append({"state_id": state_id, "has_metrics": False, "has_design": False})
            continue
        design = metrics.get("design")
        if not design:
            missing_design_states.append(state_id)
            state_summaries.append({"state_id": state_id, "has_metrics": True, "has_design": False})
            continue
        collect_style_tokens(design, counters)
        button_samples.extend(design.get("buttons", []))
        card_samples.extend(design.get("cards", []))
        heading_samples.extend(design.get("headings", []))
        nav_samples.extend(design.get("nav_items", []))
        state_summaries.append(
            {
                "state_id": state_id,
                "has_metrics": True,
                "has_design": True,
                "button_samples": len(design.get("buttons", [])),
                "card_samples": len(design.get("cards", [])),
                "heading_samples": len(design.get("headings", [])),
                "nav_samples": len(design.get("nav_items", [])),
            }
        )

    return {
        "task_id": task["task_id"],
        "repo_id": repo_id,
        "state_ids": state_ids,
        "has_design_anchors": not missing_design_states and any(summary.get("has_design") for summary in state_summaries),
        "missing_design_states": missing_design_states,
        "tokens": {name: top(counter) for name, counter in counters.items()},
        "samples": {
            "headings": compact_samples(heading_samples, sample_limit),
            "buttons": compact_samples(button_samples, sample_limit),
            "cards": compact_samples(card_samples, sample_limit),
            "nav_items": compact_samples(nav_samples, sample_limit),
        },
        "screenshots": screenshot_summaries,
        "component_crops": crop_summaries,
        "state_summaries": state_summaries,
    }


def build_design_anchor_index(
    tasks_path: Path,
    states_root: Path,
    output_path: Path,
    state_plan: Path | None,
    sample_limit: int,
) -> dict[str, Any]:
    tasks = list(read_jsonl(tasks_path))
    plan = json.loads(state_plan.read_text(encoding="utf-8")) if state_plan else {}
    items = []
    for task in tasks:
        planned_states = plan.get(task["repo_id"], [])
        if planned_states:
            state_ids = [state["state_id"] for state in planned_states]
        else:
            state_ids = task.get("required_states", [])
        items.append(build_task_anchor(task, states_root, state_ids, sample_limit))

    summary = {
        "tasks_path": str(tasks_path),
        "states_root": str(states_root),
        "state_plan": str(state_plan) if state_plan else None,
        "total": len(items),
        "with_design_anchors": sum(1 for item in items if item["has_design_anchors"]),
        "items": items,
    }
    write_json(output_path, summary)
    return summary


def add_design_anchor_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tasks", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.jsonl")
    parser.add_argument("--states-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "states")
    parser.add_argument("--state-plan", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed_state_plan.json")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.design_anchors.json")
    parser.add_argument("--sample-limit", type=int, default=12)


def run_design_anchor_from_args(args: argparse.Namespace) -> None:
    summary = build_design_anchor_index(
        tasks_path=args.tasks,
        states_root=args.states_root,
        output_path=args.output,
        state_plan=args.state_plan,
        sample_limit=args.sample_limit,
    )
    print(
        f"indexed design anchors for {summary['total']} tasks: "
        f"{summary['with_design_anchors']} with design anchors"
    )
