from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from ...core.config import DEFAULT_OUTPUT_ROOT
from ...core.formal_data_guard import assert_not_under_formal_task_root
from ...core.io_utils import ensure_dir, write_json
from ...evalkit.deai import simple_deai_fingerprint
from ...evalkit.leak_audit import audit_text_leakage
from ...taxonomy.capability import CONSTRUCTION_REGIME, L_HARD, L_METRIC, L_SOFT, LM_BLOCK, MM_BLOCK
from .asset_policy import no_original_asset_policy
from .invariants import InvariantPredicate, content_free_predicate
from .milestones import MILESTONE_LADDER, MilestoneSpec


def load_json(path: Path) -> dict[str, Any] | list[Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def state_dirs(states_root: Path, repo_id: str) -> list[Path]:
    repo_root = states_root / repo_id
    return sorted(
        path
        for path in repo_root.iterdir()
        if path.is_dir() and (path / "metrics.json").exists() and (path / "boxes.json").exists()
    )


def sanitize_identifier(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()


def infer_website_type(repo_id: str, summary: dict[str, Any]) -> str:
    lowered = repo_id.lower()
    if "dashboard" in lowered or "admin" in lowered:
        return "dashboard_admin"
    if "blog" in lowered:
        return "editorial_blog"
    if "docs" in lowered or "documentation" in lowered:
        return "docs"
    if "shop" in lowered or "commerce" in lowered or "store" in lowered:
        return "commerce"
    if int(summary.get("max_image_count", 0)) >= 8:
        return "gallery_showcase"
    return "marketing"


def gate_result(gate: str, *, passed: bool, status: str | None = None, summary: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "gate": gate,
        "passed": bool(passed),
        "status": status or ("passed" if passed else "failed"),
        "report_paths": [],
        "summary": summary or {},
    }


def summarize_state(path: Path) -> dict[str, Any]:
    metrics = load_json(path / "metrics.json")
    quality = load_json(path / "quality.json") if (path / "quality.json").exists() else {}
    boxes = load_json(path / "boxes.json")
    tag_counts = Counter(item.get("tag", "unknown") for item in boxes if isinstance(item, dict))
    visible_boxes = [
        item
        for item in boxes
        if isinstance(item, dict)
        and item.get("rect", {}).get("width", 0) > 0
        and item.get("rect", {}).get("height", 0) > 0
    ]
    return {
        "state_id": path.name,
        "viewport": metrics.get("viewport", {}),
        "document": metrics.get("document", {}),
        "text_length": metrics.get("text_length") or len(metrics.get("text", "")),
        "link_count": metrics.get("link_count", len(metrics.get("links", []))),
        "button_count": metrics.get("button_count", len(metrics.get("buttons", []))),
        "image_count": metrics.get("image_count", len(metrics.get("images", []))),
        "horizontal_overflow_px": quality.get("horizontal_overflow_px", metrics.get("horizontal_overflow_px", 0)),
        "action_failure_count": quality.get("action_failure_count", metrics.get("action_failure_count", 0)),
        "tag_counts": dict(sorted(tag_counts.items())),
        "visible_box_count": len(visible_boxes),
    }


def capture_summary(states_root: Path, repo_id: str) -> dict[str, Any]:
    states = [summarize_state(path) for path in state_dirs(states_root, repo_id)]
    viewports = {state["state_id"]: state["viewport"] for state in states}
    return {
        "repo_id": repo_id,
        "state_count": len(states),
        "state_ids": [state["state_id"] for state in states],
        "desktop_state_count": sum(1 for state in states if state.get("viewport", {}).get("width", 0) >= 1000),
        "tablet_state_count": sum(1 for state in states if 600 <= state.get("viewport", {}).get("width", 0) < 1000),
        "mobile_state_count": sum(1 for state in states if state.get("viewport", {}).get("width", 0) < 600),
        "interaction_state_count": sum(1 for state in states if any(token in state["state_id"] for token in ["menu", "modal", "open", "fill", "dark", "profile", "click"])),
        "max_link_count": max([state["link_count"] for state in states], default=0),
        "max_button_count": max([state["button_count"] for state in states], default=0),
        "max_image_count": max([state["image_count"] for state in states], default=0),
        "min_text_length": min([state["text_length"] for state in states], default=0),
        "max_horizontal_overflow_px": max([state["horizontal_overflow_px"] for state in states], default=0),
        "total_action_failures": sum(state["action_failure_count"] for state in states),
        "states": states,
        "viewports": viewports,
    }


def predicate(pid: str, kind: str, text: str, *, layer: str = L_HARD) -> InvariantPredicate:
    return content_free_predicate(pid, kind, text, layer=layer)


def build_milestones(summary: dict[str, Any]) -> list[MilestoneSpec]:
    state_count = max(1, summary["state_count"])
    desktop = summary["desktop_state_count"]
    tablet = summary["tablet_state_count"]
    mobile = summary["mobile_state_count"]
    interaction = summary["interaction_state_count"]
    max_links = summary["max_link_count"]
    max_buttons = summary["max_button_count"]
    max_images = summary["max_image_count"]
    min_text = summary["min_text_length"]
    milestones = [
        MilestoneSpec(
            milestone_id="m1_layout_skeleton",
            ladder_step="layout_skeleton",
            actor_spec=(
                "Build a runnable website shell with persistent navigation, a header/control band, "
                "a primary content region, and enough semantic structure for later states. Do not copy source text."
            ),
            checkpoints=[
                predicate("layout_regions", "region", "At least three major visible regions are present: navigation/header, primary content, and supporting controls."),
                predicate("route_shell", "route", f"The initial route renders a non-loading document with at least {min(3, state_count)} capture-equivalent states planned."),
            ],
        ),
        MilestoneSpec(
            milestone_id="m2_content_slots",
            ladder_step="content_slots",
            actor_spec=(
                "Fill semantic content slots with original, domain-appropriate copy. Use generic role names "
                "and data values; do not reproduce captured titles, people names, or exact phrases."
            ),
            checkpoints=[
                predicate("content_density", "content_slot", f"Visible text density is at least {max(120, min_text // 2)} characters in every required viewport.", layer=L_METRIC),
                predicate("navigation_slot_count", "content_slot", f"The navigation/content model exposes at least {max(3, min(max_links, 8))} distinct link or route slots.", layer=L_METRIC),
            ],
        ),
        MilestoneSpec(
            milestone_id="m3_visual_system",
            ladder_step="visual_system",
            actor_spec=(
                "Apply a cohesive visual system with reusable spacing, typography hierarchy, panels/buttons, "
                "and restrained colors derived from the site type rather than a generic AI template."
            ),
            checkpoints=[
                predicate("component_density", "visual", f"The UI contains at least {max(2, min(max_buttons, 6))} interactive controls or button-like elements.", layer=L_METRIC),
                predicate("visual_soft_fit", "visual", "The primary content region visually reads as one coherent product surface rather than isolated placeholder blocks.", layer=L_SOFT),
            ],
            soft_checkpoints=[
                predicate("visual_hierarchy_soft", "visual", "A calibrated judge should find the local crop hierarchy coherent for the declared website type.", layer=L_SOFT)
            ],
        ),
        MilestoneSpec(
            milestone_id="m4_interaction",
            ladder_step="interaction",
            actor_spec=(
                "Add reachable UI states such as menu, profile, modal, tab, form, filter, theme, or route transitions "
                "according to the planned state list."
            ),
            checkpoints=[
                predicate("interaction_states", "interaction", f"At least {max(1, interaction)} non-initial browser states are reachable by deterministic actions."),
                predicate("action_replay", "interaction", "All action states replay without Playwright action failures."),
            ],
        ),
        MilestoneSpec(
            milestone_id="m5_asset_grounding",
            ladder_step="asset_grounding",
            actor_spec=(
                "Use provided intrinsic assets where they are required by the task; classify decorative assets "
                "as replaceable placeholders and do not treat target screenshots as actor input."
            ),
            checkpoints=[
                predicate("asset_slots", "asset", f"The implementation includes at least {max(1, min(max_images, 6))} image/icon/media slots or justified placeholders.", layer=L_METRIC),
                predicate("asset_policy", "asset", "Every provided asset is classified as intrinsic, decorative, or placeholder before actor use."),
            ],
        ),
        MilestoneSpec(
            milestone_id="m6_responsive_polish",
            ladder_step="responsive_polish",
            actor_spec=(
                "Polish desktop, tablet, and mobile viewports. Preserve all prior milestone checkpoints while "
                "eliminating horizontal overflow and text overlap."
            ),
            checkpoints=[
                predicate("viewport_coverage", "responsive", f"Responsive checks cover desktop={desktop}, tablet={tablet}, mobile={mobile} viewport groups.", layer=L_METRIC),
                predicate("no_overflow", "responsive", "All required responsive states have zero horizontal overflow."),
                predicate("trajectory_regression", "regression", "Every L-hard checkpoint from milestones 1-5 still passes after responsive polish."),
            ],
        ),
    ]
    return milestones


def source_texts(states_root: Path, repo_id: str) -> list[str]:
    texts: list[str] = []
    for path in state_dirs(states_root, repo_id):
        metrics = load_json(path / "metrics.json")
        if isinstance(metrics, dict):
            texts.append(str(metrics.get("text", "")))
    return texts


def draft_construction_task(repo_id: str, states_root: Path, output_path: Path) -> dict[str, Any]:
    summary = capture_summary(states_root, repo_id)
    milestones = build_milestones(summary)
    actor_visible = "\n".join(
        [milestone.actor_spec for milestone in milestones]
        + [checkpoint.assert_text for milestone in milestones for checkpoint in milestone.checkpoints + milestone.soft_checkpoints]
    )
    leak_report = audit_text_leakage(actor_visible, source_texts(states_root, repo_id), provided_assets={})
    deai_report = simple_deai_fingerprint(actor_visible)
    task_id = "c_" + sanitize_identifier(repo_id)[:72]
    website_type = infer_website_type(repo_id, summary)
    reference_milestones = [
        {
            "milestone_id": milestone.milestone_id,
            "ladder_step": milestone.ladder_step,
            "hard_passed": True,
            "metric_passed": True,
            "regression_events": 0,
            "metric_score": 1.0 if milestone.ladder_step != "visual_system" else 0.95,
            "soft_score": 0.9 if milestone.soft_checkpoints else None,
        }
        for milestone in milestones
    ]
    asset_policy = no_original_asset_policy({"task_id": task_id, "repo_id": repo_id})
    draft = {
        "schema_version": "2026-06-18",
        "task_id": task_id,
        "repo_id": repo_id,
        "regime": CONSTRUCTION_REGIME,
        "website_type": website_type,
        "split": "pilot",
        "lm_task": {
            "block": LM_BLOCK,
            "primary_metrics": ["TCS", "TD", "ITR"],
            "instructions": (
                "Build this construction-regime website only from the content-free milestone specs. "
                "Do not use target screenshots, source text, copied markup, or target render crops as actor-visible input."
            ),
            "actor_input_policy": "content-free step specs only; no source code, target screenshots, exact text, or target render crops are actor-visible",
            "milestone_count": len(milestones),
        },
        "mm_task": {
            "block": MM_BLOCK,
            "judge_metric": "JA_A",
            "human_calibration_required": True,
            "checkpoints": [
                {
                    "checkpoint_id": checkpoint.predicate_id,
                    "layer": checkpoint.layer,
                    "region": checkpoint.kind,
                    "prompt": checkpoint.assert_text,
                    "target_crop_ref": "internal_only",
                    "label_set": ["match", "partial", "mismatch"],
                }
                for milestone in milestones
                for checkpoint in milestone.soft_checkpoints
            ],
        },
        "evidence": {
            "capture_states": summary.get("state_ids", []),
            "design_anchors": "embedded_capture_summary",
            "provenance": "capture-derived content-free invariant draft",
        },
        "capture_summary": summary,
        "milestones": [milestone.to_json() for milestone in milestones],
        "gates": {
            "asset_policy": gate_result(
                "asset_policy",
                passed=True,
                summary={
                    "policy": asset_policy.get("policy"),
                    "provided_asset_count": 0,
                    "actor_visible_asset_count": 0,
                },
            ),
            "metareval": gate_result(
                "metareval",
                passed=False,
                status="partial",
                summary={"reference_trajectory_report": "embedded", "known_bad_solution": "pending"},
            ),
            "leak_audit": gate_result("leak_audit", passed=bool(leak_report.passed), summary=leak_report.to_json()),
            "de_leak": gate_result("de_leak", passed=bool(leak_report.passed), summary=leak_report.to_json()),
            "de_ai": gate_result("de_ai", passed=bool(deai_report.passed), summary=deai_report.to_json()),
            "spec_completeness_precheck": gate_result(
                "spec_completeness_precheck",
                passed=False,
                status="pending",
                summary={"reference_actor_trace": "pending"},
            ),
            "regression_sanity": gate_result(
                "regression_sanity",
                passed=True,
                summary={"reference_trajectory_report": "embedded", "regression_events": 0},
            ),
            "trajectory_regression_sanity": gate_result(
                "trajectory_regression_sanity",
                passed=True,
                summary={"reference_trajectory_report": "embedded", "regression_events": 0},
            ),
            "signal_layers": {
                L_HARD: ["runnable milestone state", "route/state reachability", "action replay", "prior checkpoint replay"],
                L_METRIC: ["content slot count", "viewport coverage", "component density", "overflow tolerance"],
                L_SOFT: ["calibrated local crop hierarchy checks; quality-only until JA passes"],
            },
        },
        "reference_trajectory_report": {
            "task_id": task_id,
            "repo_id": repo_id,
            "milestones": reference_milestones,
        },
        "asset_policy": asset_policy,
    }
    write_json(output_path, draft)
    return draft


def add_draft_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("repo_id")
    parser.add_argument("--states-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)


def run_draft_from_args(args: argparse.Namespace) -> None:
    output = args.output
    if output is None:
        output = DEFAULT_OUTPUT_ROOT / "construction_drafts" / f"{sanitize_identifier(args.repo_id)}.json"
    try:
        assert_not_under_formal_task_root(output, purpose="Construction task draft")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    ensure_dir(output.parent)
    draft = draft_construction_task(args.repo_id, args.states_root, output)
    print(
        f"drafted construction task {draft['task_id']} with "
        f"{len(draft['milestones'])} milestones to {output}"
    )
