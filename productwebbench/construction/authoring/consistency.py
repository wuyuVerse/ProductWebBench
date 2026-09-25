from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ...core.config import DEFAULT_OUTPUT_ROOT
from ...core.io_utils import read_jsonl, write_json


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def keyed(items: list[dict[str, Any]], key: str = "task_id") -> dict[str, dict[str, Any]]:
    return {item[key]: item for item in items}


def state_ids_from_plan(state_plan_path: Path, repo_id: str) -> list[str]:
    if not state_plan_path.exists():
        return []
    data = load_json(state_plan_path)
    return [state["state_id"] for state in data.get(repo_id, [])]


def state_ids_from_capture(states_root: Path, repo_id: str) -> list[str]:
    capture_path = states_root / repo_id / "state_capture.json"
    if not capture_path.exists():
        return []
    capture = load_json(capture_path)
    return [state["state_id"] for state in capture.get("states", [])]


def package_task_ids(package_manifest_path: Path) -> set[str]:
    if not package_manifest_path.exists():
        return set()
    manifest = load_json(package_manifest_path)
    return {item["task_id"] for item in manifest.get("packages", [])}


def package_rationale_ids(package_manifest_path: Path) -> set[str]:
    if not package_manifest_path.exists():
        return set()
    manifest = load_json(package_manifest_path)
    return {
        item["task_id"]
        for item in manifest.get("packages", [])
        if item.get("has_construction_rationale")
    }


def compare_sets(name: str, expected: list[str], actual: list[str]) -> list[str]:
    errors = []
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if missing:
        errors.append(f"{name} missing states: {missing}")
    if extra:
        errors.append(f"{name} has extra states: {extra}")
    return errors


def audit_task(
    task: dict[str, Any],
    reference_specs: dict[str, dict[str, Any]],
    submission_specs: dict[str, dict[str, Any]],
    anchors: dict[str, dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
    provenance: dict[str, dict[str, Any]],
    rationales: dict[str, dict[str, Any]],
    state_plan_path: Path,
    states_root: Path,
    packaged_ids: set[str],
    packaged_rationale_ids: set[str],
) -> dict[str, Any]:
    task_id = task["task_id"]
    repo_id = task["repo_id"]
    errors: list[str] = []
    warnings: list[str] = []

    reference_spec = reference_specs.get(task_id)
    submission_spec = submission_specs.get(task_id)
    anchor = anchors.get(task_id)
    evidence_item = evidence.get(task_id)
    provenance_item = provenance.get(task_id)
    rationale_item = rationales.get(task_id)

    if reference_spec is None:
        errors.append("missing reference spec")
    if submission_spec is None:
        errors.append("missing submission spec")
    if anchor is None:
        errors.append("missing design anchors")
    if evidence_item is None:
        errors.append("missing evidence index item")
    if provenance_item is None:
        errors.append("missing repo provenance item")
    elif not provenance_item.get("passed"):
        errors.append("repo provenance did not pass")
    if task_id not in packaged_ids:
        errors.append("missing exported task package")
    if rationale_item is None:
        errors.append("missing construction rationale")
    if task_id not in packaged_rationale_ids:
        errors.append("exported task package lacks construction rationale")

    plan_states = state_ids_from_plan(state_plan_path, repo_id)
    captured_states = state_ids_from_capture(states_root, repo_id)
    task_required = task.get("required_states", [])
    task_hidden = task.get("hidden_states", [])

    if submission_spec:
        submission_states = submission_spec.get("submission_states", [])
        errors.extend(compare_sets("task.required_states vs submission_states", submission_states, task_required))
        for state in submission_spec.get("responsive_states", []):
            if state not in submission_states:
                errors.append(f"responsive state not in submission_states: {state}")
        for state in submission_spec.get("visual_anchor_states", []):
            if state not in submission_states:
                errors.append(f"visual anchor state not in submission_states: {state}")
    else:
        submission_states = []

    if reference_spec:
        reference_states = reference_spec.get("reference_states", [])
        errors.extend(compare_sets("reference_states vs state plan", reference_states, plan_states))
    else:
        reference_states = []

    if plan_states:
        errors.extend(compare_sets("captured states vs state plan", plan_states, captured_states))
    else:
        errors.append("missing state plan states")

    if anchor:
        if not anchor.get("has_design_anchors"):
            errors.append("design anchors marked unavailable")
        anchor_states = anchor.get("state_ids", [])
        errors.extend(compare_sets("design anchor states vs state plan", plan_states, anchor_states))

    if evidence_item and not evidence_item.get("quality_pass"):
        errors.append("evidence quality_pass is false")

    if not set(task_hidden).issubset(set(task_required)):
        warnings.append("hidden_states are not a subset of required_states")

    if reference_states and submission_states and set(reference_states) != set(submission_states):
        warnings.append("reference_states and submission_states differ")

    return {
        "task_id": task_id,
        "repo_id": repo_id,
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "state_counts": {
            "task_required": len(task_required),
            "task_hidden": len(task_hidden),
            "reference": len(reference_states),
            "submission": len(submission_states),
            "state_plan": len(plan_states),
            "captured": len(captured_states),
        },
    }


def audit_consistency(
    tasks_path: Path,
    reference_specs_path: Path,
    submission_specs_path: Path,
    design_anchors_path: Path,
    evidence_path: Path,
    provenance_path: Path,
    rationales_path: Path,
    state_plan_path: Path,
    states_root: Path,
    package_manifest_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    tasks = list(read_jsonl(tasks_path))
    reference_specs = keyed(load_json(reference_specs_path).get("tasks", []))
    submission_specs = keyed(load_json(submission_specs_path).get("tasks", []))
    anchors = keyed(load_json(design_anchors_path).get("items", []))
    evidence = keyed(load_json(evidence_path).get("items", []))
    provenance = keyed(load_json(provenance_path).get("items", []))
    rationales = keyed(load_json(rationales_path).get("items", [])) if rationales_path.exists() else {}
    packaged_ids = package_task_ids(package_manifest_path)
    packaged_rationale_ids = package_rationale_ids(package_manifest_path)

    results = [
        audit_task(
            task,
            reference_specs,
            submission_specs,
            anchors,
            evidence,
            provenance,
            rationales,
            state_plan_path,
            states_root,
            packaged_ids,
            packaged_rationale_ids,
        )
        for task in tasks
    ]
    summary = {
        "tasks_path": str(tasks_path),
        "reference_specs_path": str(reference_specs_path),
        "submission_specs_path": str(submission_specs_path),
        "design_anchors_path": str(design_anchors_path),
        "evidence_path": str(evidence_path),
        "provenance_path": str(provenance_path),
        "rationales_path": str(rationales_path),
        "state_plan_path": str(state_plan_path),
        "states_root": str(states_root),
        "package_manifest_path": str(package_manifest_path),
        "total": len(results),
        "passed": sum(1 for result in results if result["passed"]),
        "failed": sum(1 for result in results if not result["passed"]),
        "warning_count": sum(len(result["warnings"]) for result in results),
        "results": results,
    }
    write_json(output_path, summary)
    return summary


def add_consistency_args(parser: argparse.ArgumentParser) -> None:
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
    parser.add_argument("--evidence", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.evidence.json")
    parser.add_argument("--provenance", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.provenance.json")
    parser.add_argument("--rationales", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.rationales.json")
    parser.add_argument("--state-plan", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed_state_plan.json")
    parser.add_argument("--states-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "states")
    parser.add_argument("--package-manifest", type=Path, default=DEFAULT_OUTPUT_ROOT / "bench" / "dev" / "manifest.json")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.consistency.json")


def run_consistency_from_args(args: argparse.Namespace) -> None:
    summary = audit_consistency(
        tasks_path=args.tasks,
        reference_specs_path=args.reference_specs,
        submission_specs_path=args.submission_specs,
        design_anchors_path=args.design_anchors,
        evidence_path=args.evidence,
        provenance_path=args.provenance,
        rationales_path=args.rationales,
        state_plan_path=args.state_plan,
        states_root=args.states_root,
        package_manifest_path=args.package_manifest,
        output_path=args.output,
    )
    print(
        f"audited {summary['total']} tasks: "
        f"{summary['passed']} passed, {summary['failed']} failed, {summary['warning_count']} warnings"
    )
