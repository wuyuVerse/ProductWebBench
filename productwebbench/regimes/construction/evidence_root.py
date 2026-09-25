from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...core.config import DEFAULT_OUTPUT_ROOT, TASK_DIR
from ...core.formal_data_guard import assert_not_under_formal_task_root
from ...core.io_utils import ensure_dir, write_json
from .precheck import ALLOWED_SAFETY_PHRASES, text_is_content_free
from .reference_actor import (
    CAPTURE_REPORT_ARTIFACT_TYPE,
    REQUIRED_ARTIFACT_REF_KEYS,
    TRACE_SCHEMA_VERSION,
    VERIFIER_REPORT_ARTIFACT_TYPE,
    build_reference_actor_evidence_manifest,
    build_reference_actor_trace_from_manifest,
    default_actor_input_policy,
    milestone_ids,
)


SCAFFOLD_ARTIFACT_TYPE = "construction_evidence_root_scaffold"
ACTOR_INPUT_ARTIFACT_TYPE = "construction_actor_visible_input"
EVIDENCE_CONTRACT_ARTIFACT_TYPE = "construction_milestone_evidence_contract"
AUDIT_ARTIFACT_TYPE = "construction_evidence_root_audit"

REQUIRED_MILESTONE_FILES = {
    "actor_input": "actor_input.json",
    "evidence_contract": "evidence_contract.json",
    "workspace": "workspace",
    "trajectory_report": "trajectory_report.json",
    "capture_report": "capture_report.json",
    "verifier_report": "verifier_report.json",
}

FORBIDDEN_ACTOR_INPUT_STRINGS = (
    "target screenshot",
    "target render",
    "target crop",
    "exact text",
    "verbatim",
    "pixel-perfect",
    "copy this",
    "capture_summary",
    "boxes.json",
    "metrics.json",
)

CODE_SNIPPET_MARKERS = (
    "<html",
    "</div>",
    "function ",
    "const ",
    "let ",
    "import ",
    "export default",
)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def path_is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def validate_scaffold_output_root(output_root: Path) -> None:
    resolved = output_root.resolve()
    forbidden_roots = [
        TASK_DIR.resolve(),
        (DEFAULT_OUTPUT_ROOT / "authoring_ledger").resolve(),
    ]
    if any(path_is_under(resolved, root) or resolved == root for root in forbidden_roots):
        raise ValueError(
            "construction evidence scaffolds must not be written under formal task or ledger directories"
        )
    if output_root.name == "task.jsonl" or output_root.suffix == ".jsonl":
        raise ValueError("construction evidence scaffolds must not be written as task JSONL files")


def relative_to_root(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return os.path.relpath(path.resolve(), root.resolve())


def write_json_no_overwrite(path: Path, payload: dict[str, Any], *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} already exists; pass --overwrite to refresh scaffold files")
    write_json(path, payload)


def write_text_no_overwrite(path: Path, text: str, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} already exists; pass --overwrite to refresh scaffold files")
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")


def visible_checkpoints(milestone: dict[str, Any], key: str) -> list[dict[str, Any]]:
    checkpoints: list[dict[str, Any]] = []
    for item in milestone.get(key, []):
        if not isinstance(item, dict):
            continue
        if item.get("actor_visible", True) is False:
            continue
        checkpoints.append(
            {
                "predicate_id": item.get("predicate_id"),
                "kind": item.get("kind"),
                "layer": item.get("layer"),
                "assert_text": item.get("assert_text"),
            }
        )
    return checkpoints


def actor_input_for_milestone(task: dict[str, Any], milestone: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": TRACE_SCHEMA_VERSION,
        "artifact_type": ACTOR_INPUT_ARTIFACT_TYPE,
        "formal_task_record": False,
        "generated_at": utc_now(),
        "task_id": task.get("task_id"),
        "repo_id": task.get("repo_id"),
        "regime": task.get("regime"),
        "milestone_id": milestone.get("milestone_id"),
        "ladder_step": milestone.get("ladder_step"),
        "passed": False,
        "completion_claim": "not_run",
        "input_policy": default_actor_input_policy(),
        "actor_visible_inputs": {
            "instructions": (
                "Build this milestone only from this JSON package. Use original copy and a runnable implementation "
                "that satisfies the listed checkpoints."
            ),
            "actor_spec": milestone.get("actor_spec"),
            "checkpoints": visible_checkpoints(milestone, "checkpoints"),
            "soft_checkpoints": visible_checkpoints(milestone, "soft_checkpoints"),
        },
        "forbidden_external_input_classes": [
            "repo_source_code",
            "repo_source_text",
            "target_visual_capture",
            "target_render_crop",
            "exact_target_copy",
        ],
    }


def evidence_contract_for_milestone(task: dict[str, Any], milestone: dict[str, Any], milestone_dir: Path) -> dict[str, Any]:
    return {
        "schema_version": TRACE_SCHEMA_VERSION,
        "artifact_type": EVIDENCE_CONTRACT_ARTIFACT_TYPE,
        "formal_task_record": False,
        "generated_at": utc_now(),
        "task_id": task.get("task_id"),
        "repo_id": task.get("repo_id"),
        "milestone_id": milestone.get("milestone_id"),
        "passed": False,
        "completion_claim": "not_run",
        "required_artifacts": {
            "workspace": {
                "path": str(milestone_dir / REQUIRED_MILESTONE_FILES["workspace"]),
                "kind": "directory",
                "producer": "spec_only_actor_run",
            },
            "trajectory_report": {
                "path": str(milestone_dir / REQUIRED_MILESTONE_FILES["trajectory_report"]),
                "kind": "json_report",
                "producer": "construction_trajectory_scorer",
            },
            "capture_report": {
                "path": str(milestone_dir / REQUIRED_MILESTONE_FILES["capture_report"]),
                "kind": "json_report",
                "producer": "browser_capture_runner",
            },
            "verifier_report": {
                "path": str(milestone_dir / REQUIRED_MILESTONE_FILES["verifier_report"]),
                "kind": "json_report",
                "producer": "construction_verifier",
            },
        },
        "required_report_bindings": {
            "capture_report": {
                "schema_version": TRACE_SCHEMA_VERSION,
                "artifact_type": CAPTURE_REPORT_ARTIFACT_TYPE,
                "formal_task_record": False,
                "task_id": task.get("task_id"),
                "repo_id": task.get("repo_id"),
                "milestone_id": milestone.get("milestone_id"),
                "source_kind": "$PRODUCTWEBBENCH_SOURCE_KIND",
                "task_sha256": "must match current task digest",
                "passed": True,
            },
            "verifier_report": {
                "schema_version": TRACE_SCHEMA_VERSION,
                "artifact_type": VERIFIER_REPORT_ARTIFACT_TYPE,
                "formal_task_record": False,
                "task_id": task.get("task_id"),
                "repo_id": task.get("repo_id"),
                "milestone_id": milestone.get("milestone_id"),
                "source_kind": "$PRODUCTWEBBENCH_SOURCE_KIND",
                "task_sha256": "must match current task digest",
                "passed": True,
                "checks": "non-empty list with L-hard/L-metric checks and passed flags",
            },
        },
        "non_artifacts": [
            "reference_actor_trace",
            "reference_trajectory_report",
            "construction_metareval_report",
            "accepted_task_jsonl_record",
        ],
        "notes": (
            "This contract describes files that must be produced by a real spec-only actor/verifier run. "
            "It is not a passing trace and does not make the task acceptable."
        ),
    }


def scaffold_manifest(task: dict[str, Any], output_root: Path, milestone_dirs: list[Path]) -> dict[str, Any]:
    return {
        "schema_version": TRACE_SCHEMA_VERSION,
        "artifact_type": SCAFFOLD_ARTIFACT_TYPE,
        "formal_task_record": False,
        "generated_at": utc_now(),
        "task_id": task.get("task_id"),
        "repo_id": task.get("repo_id"),
        "regime": task.get("regime"),
        "passed": False,
        "completion_claim": "not_run",
        "milestone_count": len(milestone_dirs),
        "evidence_root": str(output_root),
        "milestone_dirs": [relative_to_root(path, output_root) for path in milestone_dirs],
        "allowed_automation": [
            "prepare_scaffold",
            "audit_scaffold",
            "build_manifest_from_existing_artifacts",
            "derive_trace_from_existing_reports",
        ],
        "forbidden_automation": [
            "mass_generate_formal_task_jsonl",
            "write_construction_ledger",
            "synthesize_passing_reference_trace",
            "mark_task_accepted",
        ],
    }


def scaffold_readme(task: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Construction Evidence Root Scaffold",
            "",
            "This directory is a non-formal scaffold for one spec-only construction actor run.",
            "",
            f"- task_id: {task.get('task_id')}",
            f"- repo_id: {task.get('repo_id')}",
            "- formal_task_record: false",
            "- accepted benchmark data: no",
            "",
            "A real run must fill each milestone workspace and reports before a manifest or trace can be built.",
            "The scaffold itself must never be copied into data/productwebbench/tasks or the construction ledger.",
            "",
        ]
    )


def prepare_construction_evidence_root(task: dict[str, Any], *, output_root: Path, overwrite: bool = False) -> dict[str, Any]:
    validate_scaffold_output_root(output_root)
    ensure_dir(output_root)
    evidence_root = ensure_dir(output_root / "evidence")
    milestone_dirs: list[Path] = []

    for milestone in task.get("milestones", []):
        if not isinstance(milestone, dict):
            continue
        milestone_id = str(milestone.get("milestone_id", "")).strip()
        if not milestone_id:
            continue
        milestone_dir = ensure_dir(evidence_root / milestone_id)
        ensure_dir(milestone_dir / REQUIRED_MILESTONE_FILES["workspace"])
        write_json_no_overwrite(
            milestone_dir / REQUIRED_MILESTONE_FILES["actor_input"],
            actor_input_for_milestone(task, milestone),
            overwrite=overwrite,
        )
        write_json_no_overwrite(
            milestone_dir / REQUIRED_MILESTONE_FILES["evidence_contract"],
            evidence_contract_for_milestone(task, milestone, milestone_dir),
            overwrite=overwrite,
        )
        milestone_dirs.append(milestone_dir)

    manifest = scaffold_manifest(task, output_root, milestone_dirs)
    write_json_no_overwrite(output_root / "evidence_root_scaffold.json", manifest, overwrite=overwrite)
    write_text_no_overwrite(output_root / "README.md", scaffold_readme(task), overwrite=overwrite)
    return manifest


def actor_text_payload(actor_input: dict[str, Any]) -> list[tuple[str, str]]:
    visible = actor_input.get("actor_visible_inputs", {})
    if not isinstance(visible, dict):
        return []
    payloads: list[tuple[str, str]] = []
    for key in ("instructions", "actor_spec"):
        value = visible.get(key)
        if isinstance(value, str):
            payloads.append((key, value))
    for group_name in ("checkpoints", "soft_checkpoints"):
        for index, checkpoint in enumerate(visible.get(group_name, [])):
            if isinstance(checkpoint, dict) and isinstance(checkpoint.get("assert_text"), str):
                label = f"{group_name}[{index}].assert_text"
                payloads.append((label, checkpoint["assert_text"]))
    return payloads


def recursive_strings(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(prefix, value)]
    if isinstance(value, dict):
        items: list[tuple[str, str]] = []
        for key, child in value.items():
            next_prefix = f"{prefix}.{key}" if prefix else str(key)
            items.extend(recursive_strings(child, next_prefix))
        return items
    if isinstance(value, list):
        items = []
        for index, child in enumerate(value):
            items.extend(recursive_strings(child, f"{prefix}[{index}]"))
        return items
    return []


def actor_input_leak_issues(actor_input: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for label, text in actor_text_payload(actor_input):
        if not text_is_content_free(text):
            issues.append(f"actor_input {label} is not content-free")
    for label, text in recursive_strings(actor_input):
        lowered = text.lower()
        for phrase in ALLOWED_SAFETY_PHRASES:
            lowered = lowered.replace(phrase, "")
        if any(token in lowered for token in FORBIDDEN_ACTOR_INPUT_STRINGS):
            issues.append(f"actor_input {label} contains forbidden actor-visible wording")
        if "<path>" in text or text.startswith("/") or "://" in text:
            issues.append(f"actor_input {label} appears to expose a path or URL")
        if any(ext in lowered for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg")):
            issues.append(f"actor_input {label} appears to expose an image or asset filename")
        if any(marker in lowered for marker in CODE_SNIPPET_MARKERS):
            issues.append(f"actor_input {label} appears to expose source-like code")
    return sorted(set(issues))


def validate_actor_policy(actor_input: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    policy = actor_input.get("input_policy", {})
    if not isinstance(policy, dict):
        return False, {"issue": "actor_input input_policy is missing or invalid"}
    forbidden_flags = {
        "used_source_code": policy.get("used_source_code"),
        "used_source_text": policy.get("used_source_text"),
        "used_target_screenshots": policy.get("used_target_screenshots"),
        "used_target_render_crops": policy.get("used_target_render_crops"),
        "used_exact_target_text": policy.get("used_exact_target_text"),
    }
    return all(value is False for value in forbidden_flags.values()), {"flags": forbidden_flags}


def audit_construction_evidence_root(
    task: dict[str, Any],
    *,
    evidence_root: Path,
    require_complete: bool = False,
) -> dict[str, Any]:
    issues: list[str] = []
    checks: list[dict[str, Any]] = []
    expected_ids = milestone_ids(task)

    def add_check(name: str, passed: bool, **details: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), **details})
        if not passed:
            issues.append(details.get("issue") or name)

    add_check("evidence_root_exists", evidence_root.exists(), path=str(evidence_root), issue="evidence root is missing")
    scaffold_path = evidence_root / "evidence_root_scaffold.json"
    scaffold_raw = load_json(scaffold_path) if scaffold_path.exists() else {}
    scaffold = scaffold_raw if isinstance(scaffold_raw, dict) else {}
    add_check(
        "scaffold_manifest_present",
        scaffold_path.exists(),
        path=str(scaffold_path),
        issue="evidence_root_scaffold.json is missing",
    )
    if scaffold_path.exists():
        add_check(
            "scaffold_is_non_formal",
            scaffold.get("artifact_type") == SCAFFOLD_ARTIFACT_TYPE and scaffold.get("formal_task_record") is False,
            observed_artifact_type=scaffold.get("artifact_type"),
            observed_formal_task_record=scaffold.get("formal_task_record"),
            issue="scaffold manifest must be non-formal",
        )

    for milestone in task.get("milestones", []):
        if not isinstance(milestone, dict):
            continue
        mid = str(milestone.get("milestone_id", "")).strip()
        milestone_dir = evidence_root / "evidence" / mid
        add_check(
            "milestone_dir_present",
            milestone_dir.exists(),
            milestone_id=mid,
            path=str(milestone_dir),
            issue=f"{mid} evidence directory is missing",
        )
        actor_input_path = milestone_dir / REQUIRED_MILESTONE_FILES["actor_input"]
        contract_path = milestone_dir / REQUIRED_MILESTONE_FILES["evidence_contract"]
        workspace_path = milestone_dir / REQUIRED_MILESTONE_FILES["workspace"]

        add_check(
            "actor_input_present",
            actor_input_path.exists(),
            milestone_id=mid,
            path=str(actor_input_path),
            issue=f"{mid} actor_input.json is missing",
        )
        if actor_input_path.exists():
            try:
                actor_input_raw = load_json(actor_input_path)
                actor_input = actor_input_raw if isinstance(actor_input_raw, dict) else {}
            except json.JSONDecodeError as exc:
                actor_input = {}
                add_check(
                    "actor_input_parse",
                    False,
                    milestone_id=mid,
                    issue=f"{mid} actor_input.json is not valid JSON: {exc}",
                )
            add_check(
                "actor_input_is_non_formal",
                actor_input.get("artifact_type") == ACTOR_INPUT_ARTIFACT_TYPE
                and actor_input.get("formal_task_record") is False
                and actor_input.get("passed") is not True,
                milestone_id=mid,
                observed_artifact_type=actor_input.get("artifact_type"),
                issue=f"{mid} actor_input must be a non-passing non-formal input package",
            )
            policy_ok, policy_details = validate_actor_policy(actor_input)
            add_check(
                "actor_input_policy_content_free",
                policy_ok,
                milestone_id=mid,
                **policy_details,
                issue=f"{mid} actor input policy exposes forbidden inputs",
            )
            leak_issues = actor_input_leak_issues(actor_input)
            add_check(
                "actor_input_no_leak_tokens",
                not leak_issues,
                milestone_id=mid,
                leak_issues=leak_issues,
                issue=f"{mid} actor input contains leak-prone fields or text",
            )

        add_check(
            "evidence_contract_present",
            contract_path.exists(),
            milestone_id=mid,
            path=str(contract_path),
            issue=f"{mid} evidence_contract.json is missing",
        )
        if contract_path.exists():
            try:
                contract_raw = load_json(contract_path)
                contract = contract_raw if isinstance(contract_raw, dict) else {}
            except json.JSONDecodeError as exc:
                contract = {}
                add_check(
                    "evidence_contract_parse",
                    False,
                    milestone_id=mid,
                    issue=f"{mid} evidence_contract.json is not valid JSON: {exc}",
                )
            required_artifacts = contract.get("required_artifacts", {})
            contract_keys = set(required_artifacts.keys()) if isinstance(required_artifacts, dict) else set()
            add_check(
                "evidence_contract_required_artifacts",
                set(REQUIRED_ARTIFACT_REF_KEYS).issubset(contract_keys),
                milestone_id=mid,
                observed=sorted(contract_keys),
                expected=list(REQUIRED_ARTIFACT_REF_KEYS),
                issue=f"{mid} evidence contract does not list all required artifacts",
            )
            report_bindings = contract.get("required_report_bindings", {})
            capture_binding = report_bindings.get("capture_report") if isinstance(report_bindings, dict) else None
            verifier_binding = report_bindings.get("verifier_report") if isinstance(report_bindings, dict) else None
            add_check(
                "evidence_contract_capture_report_binding",
                isinstance(capture_binding, dict)
                and capture_binding.get("artifact_type") == CAPTURE_REPORT_ARTIFACT_TYPE
                and capture_binding.get("formal_task_record") is False
                and capture_binding.get("task_id") == task.get("task_id")
                and capture_binding.get("repo_id") == task.get("repo_id")
                and capture_binding.get("milestone_id") == mid,
                milestone_id=mid,
                issue=f"{mid} evidence contract does not require a bound construction capture report",
            )
            add_check(
                "evidence_contract_verifier_report_binding",
                isinstance(verifier_binding, dict)
                and verifier_binding.get("artifact_type") == VERIFIER_REPORT_ARTIFACT_TYPE
                and verifier_binding.get("formal_task_record") is False
                and verifier_binding.get("task_id") == task.get("task_id")
                and verifier_binding.get("repo_id") == task.get("repo_id")
                and verifier_binding.get("milestone_id") == mid,
                milestone_id=mid,
                issue=f"{mid} evidence contract does not require a bound construction verifier report",
            )

        add_check(
            "workspace_dir_present",
            workspace_path.exists() and workspace_path.is_dir(),
            milestone_id=mid,
            path=str(workspace_path),
            issue=f"{mid} workspace directory is missing",
        )
        if require_complete:
            for key in ("trajectory_report", "capture_report", "verifier_report"):
                report_path = milestone_dir / REQUIRED_MILESTONE_FILES[key]
                add_check(
                    "completed_report_present",
                    report_path.exists(),
                    milestone_id=mid,
                    report=key,
                    path=str(report_path),
                    issue=f"{mid} {REQUIRED_MILESTONE_FILES[key]} is missing",
                )

    if require_complete:
        manifest, manifest_validation = build_reference_actor_evidence_manifest(
            task,
            evidence_root=evidence_root,
            output_path=evidence_root / "reference_actor_evidence_manifest.json",
        )
        add_check(
            "complete_manifest_can_be_derived",
            bool(manifest_validation.get("passed")) and manifest is not None,
            details=manifest_validation,
            issue="completed evidence root cannot derive a full reference actor manifest",
        )
        if manifest is not None:
            trace, trace_validation = build_reference_actor_trace_from_manifest(
                task,
                manifest,
                artifact_root=evidence_root,
            )
            add_check(
                "complete_trace_can_be_derived",
                bool(trace_validation.get("passed")) and trace is not None,
                details=trace_validation,
                issue="completed evidence root cannot derive a passing reference actor trace",
            )

    return {
        "schema_version": TRACE_SCHEMA_VERSION,
        "artifact_type": AUDIT_ARTIFACT_TYPE,
        "formal_task_record": False,
        "generated_at": utc_now(),
        "task_id": task.get("task_id"),
        "repo_id": task.get("repo_id"),
        "evidence_root": str(evidence_root),
        "require_complete": require_complete,
        "expected_milestone_ids": expected_ids,
        "passed": not issues,
        "issue_count": len(issues),
        "issues": issues,
        "checks": checks,
    }


def add_prepare_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Directory for the non-formal evidence scaffold. Defaults to data/productwebbench/construction_evidence_roots/<task_id>.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Refresh scaffold files if they already exist.")


def add_audit_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Require real workspace/report artifacts and verify they can derive a passing trace.",
    )


def run_prepare_from_args(args: argparse.Namespace) -> None:
    task = load_json(args.task)
    output_root = args.output_root
    if output_root is None:
        task_id = str(task.get("task_id", "construction_task")).strip() or "construction_task"
        output_root = DEFAULT_OUTPUT_ROOT / "construction_evidence_roots" / task_id
    try:
        manifest = prepare_construction_evidence_root(task, output_root=output_root, overwrite=args.overwrite)
    except (FileExistsError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(
        "prepared non-formal construction evidence scaffold "
        f"task={manifest.get('task_id')} milestones={manifest.get('milestone_count')} root={output_root}"
    )


def run_audit_from_args(args: argparse.Namespace) -> None:
    task = load_json(args.task)
    report = audit_construction_evidence_root(
        task,
        evidence_root=args.evidence_root,
        require_complete=args.require_complete,
    )
    output = args.output
    if output is None:
        suffix = "complete_audit" if args.require_complete else "scaffold_audit"
        output = args.evidence_root / f"{suffix}.json"
    try:
        assert_not_under_formal_task_root(output, purpose="Construction evidence root audit report")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    write_json(output, report)
    print(
        "construction evidence root audit "
        f"passed={report['passed']} task={report.get('task_id')} issues={report['issue_count']} output={output}"
    )
    if not report["passed"]:
        raise SystemExit("; ".join(report.get("issues", [])))
