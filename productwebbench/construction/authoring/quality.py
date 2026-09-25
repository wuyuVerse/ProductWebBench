from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ...core.config import DEFAULT_OUTPUT_ROOT
from ...core.io_utils import read_jsonl, write_json
from ...core.task_files import task_file_patterns


QUALITY_WEIGHTS = {
    "task_specificity": 20,
    "asset_grounding": 15,
    "design_constraints": 20,
    "state_coverage": 15,
    "machine_checks": 20,
    "rubric_completeness": 10,
}

GENERIC_BAD_PHRASES = {
    "make it modern",
    "make it beautiful",
    "nice design",
    "good looking",
    "lorem ipsum",
    "placeholder",
}

DESIGN_KEYWORDS = {
    "typography",
    "spacing",
    "button",
    "card",
    "image",
    "asset",
    "style",
    "visual",
    "layout",
    "mobile",
    "responsive",
    "theme",
    "density",
    "canvas",
    "overlay",
    "controls",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def keyed(items: list[dict[str, Any]], key: str = "task_id") -> dict[str, dict[str, Any]]:
    return {item[key]: item for item in items}


def score_section(name: str, max_points: int, issues: list[str], warnings: list[str] | None = None) -> dict[str, Any]:
    penalty = 0
    penalty += 5 * len(issues)
    penalty += 2 * len(warnings or [])
    score = max(0, max_points - penalty)
    return {
        "name": name,
        "score": score,
        "max_score": max_points,
        "issues": issues,
        "warnings": warnings or [],
    }


def text_contains_any(text: str, values: list[str]) -> bool:
    lowered = text.lower()
    return any(value.lower() in lowered for value in values)


def is_canvas_like(task: dict[str, Any]) -> bool:
    blob = " ".join(
        [
            str(task.get("problem_statement", "")),
            str(task.get("website_type", "")),
            " ".join(str(value) for value in task.get("required_content", [])),
        ]
    )
    return (
        task.get("website_type") == "interactive_canvas"
        or text_contains_any(blob, ["canvas", "webgl", "three.js", "3d", "shader"])
    )


def requires_canvas_screenshot_check(task: dict[str, Any]) -> bool:
    blob = " ".join(
        [
            str(task.get("problem_statement", "")),
            str(task.get("website_type", "")),
            " ".join(str(value) for value in task.get("required_content", [])),
        ]
    )
    return task.get("website_type") == "interactive_canvas" or text_contains_any(
        blob, ["canvas", "webgl", "three.js", "shader"]
    )


def problem_statement(task: dict[str, Any]) -> str:
    return task.get("problem_statement", "")


def task_specificity(task: dict[str, Any]) -> dict[str, Any]:
    issues = []
    warnings = []
    statement = problem_statement(task)
    word_count = len(statement.split())
    if word_count < 30:
        issues.append(f"problem_statement too short: {word_count} words")
    repo_name = task["repo_id"].split("__")[1].lower()
    repo_variants = {
        repo_name,
        repo_name.replace("-", " "),
        repo_name.replace("-", ""),
        task["repo_id"].split("__")[0].lower(),
    }
    identity_text = (statement + " " + task.get("author_notes", "")).lower()
    if not any(variant in identity_text for variant in repo_variants):
        warnings.append("repo/project identity is not explicit in problem_statement or author_notes")
    lowered = statement.lower()
    for phrase in GENERIC_BAD_PHRASES:
        if phrase in lowered:
            issues.append(f"generic or placeholder phrase found: {phrase}")
    if len(task.get("required_content", [])) < 3:
        issues.append("required_content should contain at least three concrete requirements")
    if len(task_file_patterns(task)) < 3:
        warnings.append("task file context has fewer than three target hints")
    return score_section("task_specificity", QUALITY_WEIGHTS["task_specificity"], issues, warnings)


def asset_grounding(task: dict[str, Any], submission_spec: dict[str, Any] | None) -> dict[str, Any]:
    issues = []
    warnings = []
    assets = task.get("assets_to_consider", [])
    problem_blob = " ".join(
        [problem_statement(task), task.get("author_notes", "")]
        + task.get("design_constraints", [])
        + task.get("required_content", [])
    )
    if len(assets) < 2:
        issues.append("assets_to_consider should list at least two repo-specific assets or asset directories")
    if not text_contains_any(problem_blob, ["asset", "image", "avatar", "icon", "video", "svg", "lottie", "canvas", "3d", "character"]):
        warnings.append("task text does not explicitly discuss asset or visual-state grounding")
    asset_path_signals = (submission_spec or {}).get("asset_path_signals", [])
    canvas_like = is_canvas_like(task)
    if not asset_path_signals and not canvas_like:
        issues.append("non-canvas task lacks machine-checkable asset_path_signals")
    if canvas_like and not text_contains_any(problem_blob, ["canvas", "3d", "world", "controls", "interaction"]):
        issues.append("interactive/canvas task does not clearly specify interaction or canvas grounding")
    return score_section("asset_grounding", QUALITY_WEIGHTS["asset_grounding"], issues, warnings)


def design_constraints(task: dict[str, Any], anchor: dict[str, Any] | None) -> dict[str, Any]:
    issues = []
    warnings = []
    constraints = task.get("design_constraints", [])
    blob = " ".join(constraints).lower()
    if len(constraints) < 4:
        issues.append("design_constraints should contain at least four repo-specific constraints")
    keyword_hits = sum(1 for keyword in DESIGN_KEYWORDS if keyword in blob)
    if keyword_hits < 4:
        warnings.append(f"design constraints mention few concrete visual concepts: {keyword_hits}")
    if not anchor or not anchor.get("has_design_anchors"):
        issues.append("missing design anchors for task")
    else:
        samples = anchor.get("samples", {})
        screenshot_count = sum(1 for screenshot in anchor.get("screenshots", []) if screenshot.get("available"))
        if screenshot_count < 2:
            issues.append("fewer than two screenshot anchors available")
        if not any(len(samples.get(name, [])) for name in ("buttons", "cards", "headings", "nav_items")):
            canvas_like = is_canvas_like(task)
            if not canvas_like:
                issues.append("DOM-heavy task has no component style samples")
            else:
                warnings.append("canvas-like task relies mainly on screenshot anchors")
    return score_section("design_constraints", QUALITY_WEIGHTS["design_constraints"], issues, warnings)


def state_coverage(task: dict[str, Any], submission_spec: dict[str, Any] | None, reference_spec: dict[str, Any] | None) -> dict[str, Any]:
    issues = []
    warnings = []
    required = set(task.get("required_states", []))
    submission_states = set((submission_spec or {}).get("submission_states", []))
    reference_states = set((reference_spec or {}).get("reference_states", []))
    has_desktop = any("desktop" in state for state in required)
    has_mobile = any("mobile" in state for state in required)
    if not (has_desktop and has_mobile):
        issues.append("required_states must include both desktop and mobile states")
    if not any("tablet" in state for state in required):
        warnings.append("required_states do not include a tablet state")
    if not any(state not in {"desktop_initial", "tablet_initial", "mobile_initial"} for state in required):
        issues.append("required_states lack task-specific scroll/interaction states")
    interaction_states = set((submission_spec or {}).get("interaction_states", []))
    if not interaction_states:
        warnings.append("submission spec does not declare interaction_states")
    elif not interaction_states.issubset(required):
        issues.append("interaction_states are not a subset of required_states")
    if required != submission_states:
        issues.append("required_states do not match submission_states")
    if reference_states != submission_states:
        warnings.append("reference_states and submission_states differ")
    responsive_states = set((submission_spec or {}).get("responsive_states", []))
    if not responsive_states.issubset(submission_states):
        issues.append("responsive_states are not a subset of submission_states")
    return score_section("state_coverage", QUALITY_WEIGHTS["state_coverage"], issues, warnings)


def machine_checks(task: dict[str, Any], submission_spec: dict[str, Any] | None) -> dict[str, Any]:
    issues = []
    warnings = []
    if not submission_spec:
        return score_section("machine_checks", QUALITY_WEIGHTS["machine_checks"], ["missing submission spec"])
    if len(submission_spec.get("completion_text_signals", [])) < 3:
        issues.append("completion_text_signals should contain at least three concrete signals")
    if submission_spec.get("min_completion_text_signals", 0) < 2:
        issues.append("min_completion_text_signals should require at least two signals")
    requires_canvas_check = requires_canvas_screenshot_check(task)
    if not submission_spec.get("regression_text_signals") and task.get("intent") != "extend_interaction" and not requires_canvas_check:
        issues.append("missing regression_text_signals for non-interaction task")
    if requires_canvas_check and not submission_spec.get("min_canvas_screenshot_bytes"):
        issues.append("canvas-like task missing min_canvas_screenshot_bytes")
    if len(submission_spec.get("visual_anchor_states", [])) < 2:
        issues.append("visual_anchor_states should contain at least two states")
    if "forbidden_text_patterns" not in submission_spec:
        warnings.append("forbidden_text_patterns not configured")
    if "overflow_tolerance_px" not in submission_spec:
        issues.append("overflow_tolerance_px missing")
    return score_section("machine_checks", QUALITY_WEIGHTS["machine_checks"], issues, warnings)


def rubric_completeness(task: dict[str, Any]) -> dict[str, Any]:
    issues = []
    warnings = []
    rubric = task.get("evaluation_rubric", {})
    for key in ("change_completion", "design_consistency", "responsive", "regression"):
        items = rubric.get(key, [])
        if len(items) < 2:
            issues.append(f"rubric.{key} should contain at least two criteria")
    all_items = " ".join(item for values in rubric.values() for item in values).lower()
    if "mobile" not in all_items and "responsive" not in all_items:
        warnings.append("rubric does not explicitly mention mobile/responsive behavior")
    if "existing" not in all_items and "remain" not in all_items:
        warnings.append("rubric weakly specifies regression preservation")
    return score_section("rubric_completeness", QUALITY_WEIGHTS["rubric_completeness"], issues, warnings)


def audit_one_task(
    task: dict[str, Any],
    reference_spec: dict[str, Any] | None,
    submission_spec: dict[str, Any] | None,
    anchor: dict[str, Any] | None,
) -> dict[str, Any]:
    sections = [
        task_specificity(task),
        asset_grounding(task, submission_spec),
        design_constraints(task, anchor),
        state_coverage(task, submission_spec, reference_spec),
        machine_checks(task, submission_spec),
        rubric_completeness(task),
    ]
    score = sum(section["score"] for section in sections)
    max_score = sum(section["max_score"] for section in sections)
    issues = [
        {"section": section["name"], "issue": issue}
        for section in sections
        for issue in section["issues"]
    ]
    warnings = [
        {"section": section["name"], "warning": warning}
        for section in sections
        for warning in section["warnings"]
    ]
    return {
        "task_id": task["task_id"],
        "repo_id": task["repo_id"],
        "score": score,
        "max_score": max_score,
        "passed": score >= 85 and not issues,
        "issue_count": len(issues),
        "warning_count": len(warnings),
        "issues": issues,
        "warnings": warnings,
        "sections": sections,
    }


def audit_quality(
    tasks_path: Path,
    reference_specs_path: Path,
    submission_specs_path: Path,
    design_anchors_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    tasks = list(read_jsonl(tasks_path))
    reference_specs = keyed(load_json(reference_specs_path).get("tasks", []))
    submission_specs = keyed(load_json(submission_specs_path).get("tasks", []))
    anchors = keyed(load_json(design_anchors_path).get("items", []))
    results = [
        audit_one_task(
            task,
            reference_specs.get(task["task_id"]),
            submission_specs.get(task["task_id"]),
            anchors.get(task["task_id"]),
        )
        for task in tasks
    ]
    summary = {
        "tasks_path": str(tasks_path),
        "reference_specs_path": str(reference_specs_path),
        "submission_specs_path": str(submission_specs_path),
        "design_anchors_path": str(design_anchors_path),
        "total": len(results),
        "passed": sum(1 for result in results if result["passed"]),
        "failed": sum(1 for result in results if not result["passed"]),
        "min_score": min((result["score"] for result in results), default=0),
        "average_score": round(sum(result["score"] for result in results) / len(results), 2) if results else 0,
        "issue_count": sum(result["issue_count"] for result in results),
        "warning_count": sum(result["warning_count"] for result in results),
        "results": results,
    }
    write_json(output_path, summary)
    return summary


def add_quality_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tasks", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.jsonl")
    parser.add_argument(
        "--reference-specs",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed_verifier_specs.json",
    )
    parser.add_argument(
        "--submission-specs",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed_submission_specs.json",
    )
    parser.add_argument(
        "--design-anchors",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.design_anchors.json",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.quality_audit.json")


def run_quality_from_args(args: argparse.Namespace) -> None:
    summary = audit_quality(
        tasks_path=args.tasks,
        reference_specs_path=args.reference_specs,
        submission_specs_path=args.submission_specs,
        design_anchors_path=args.design_anchors,
        output_path=args.output,
    )
    print(
        f"audited quality for {summary['total']} tasks: "
        f"{summary['passed']} passed, {summary['failed']} failed, "
        f"avg={summary['average_score']}, issues={summary['issue_count']}, warnings={summary['warning_count']}"
    )
