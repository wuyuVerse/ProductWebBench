from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...core.config import DEFAULT_OUTPUT_ROOT
from ...core.formal_data_guard import assert_not_under_formal_task_root
from ...core.io_utils import ensure_dir, write_json
from ...taxonomy.capability import CONSTRUCTION_REGIME, L_HARD, L_METRIC, L_SOFT
from ...taxonomy.capability import task_package_json_schema
from .asset_policy import validate_construction_asset_policy
from .metareval import validate_construction_metareval_gate
from .milestones import MILESTONE_LADDER
from .reference_actor import validate_reference_actor_trace
from .reference_trajectory import validate_reference_trajectory_report
from .task_digest import stable_task_digest

try:
    import jsonschema
except ImportError:  # pragma: no cover - optional dependency in minimal installs
    jsonschema = None


PRECHECK_ARTIFACT_TYPE = "construction_precheck_report"
FORBIDDEN_ACTOR_TOKENS = (
    "target screenshot",
    "target render",
    "copy this",
    "pixel-perfect",
    "exact text:",
    "verbatim",
)
ALLOWED_SAFETY_PHRASES = (
    "do not treat target screenshots as actor input",
    "target screenshots as actor input",
    "target render/image appears",
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def file_sha256(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def input_file_record(path: Path | None) -> dict[str, Any]:
    return {
        "path": str(path) if path is not None else None,
        "sha256": file_sha256(path),
    }


def checkpoint_layer(checkpoint: dict[str, Any]) -> str:
    return str(checkpoint.get("layer", ""))


def text_is_content_free(text: str) -> bool:
    lowered = text.lower()
    scrubbed = lowered
    for phrase in ALLOWED_SAFETY_PHRASES:
        scrubbed = scrubbed.replace(phrase, "")
    return not any(token in scrubbed for token in FORBIDDEN_ACTOR_TOKENS)


def check_milestones(task: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    milestones = task.get("milestones", [])
    issues: list[str] = []
    checks: list[dict[str, Any]] = []
    ladder = [item.get("ladder_step") for item in milestones if isinstance(item, dict)]
    expected = list(MILESTONE_LADDER)
    ladder_ok = ladder == expected
    checks.append({"name": "six_layer_ladder", "passed": ladder_ok, "observed": ladder, "expected": expected})
    if not ladder_ok:
        issues.append("milestones must follow the fixed six-layer ladder")

    checkpoint_ids: set[str] = set()
    duplicate_checkpoint_ids: set[str] = set()
    for index, milestone in enumerate(milestones, start=1):
        mid = milestone.get("milestone_id", f"milestone_{index}")
        actor_spec = str(milestone.get("actor_spec", ""))
        actor_spec_ok = len(actor_spec.strip()) >= 40 and text_is_content_free(actor_spec)
        checks.append({"name": "actor_spec_content_free", "milestone_id": mid, "passed": actor_spec_ok})
        if not actor_spec_ok:
            issues.append(f"{mid} has missing or actor-leaky actor_spec")

        hard_metric = [
            item
            for item in milestone.get("checkpoints", [])
            if isinstance(item, dict) and checkpoint_layer(item) in {L_HARD, L_METRIC}
        ]
        hard_metric_ok = bool(hard_metric)
        checks.append({"name": "hard_metric_checkpoint_present", "milestone_id": mid, "passed": hard_metric_ok})
        if not hard_metric_ok:
            issues.append(f"{mid} has no L-hard/L-metric checkpoint")

        for collection_name in ("checkpoints", "soft_checkpoints"):
            for checkpoint in milestone.get(collection_name, []):
                if not isinstance(checkpoint, dict):
                    issues.append(f"{mid} contains a non-object checkpoint")
                    continue
                cid = str(checkpoint.get("predicate_id", ""))
                if not cid:
                    issues.append(f"{mid} contains a checkpoint without predicate_id")
                elif cid in checkpoint_ids:
                    duplicate_checkpoint_ids.add(cid)
                checkpoint_ids.add(cid)
                assert_text = str(checkpoint.get("assert_text", ""))
                if not assert_text or not text_is_content_free(assert_text):
                    issues.append(f"{mid}:{cid} has missing or actor-leaky assert_text")
                layer = checkpoint_layer(checkpoint)
                if layer not in {L_HARD, L_METRIC, L_SOFT}:
                    issues.append(f"{mid}:{cid} has invalid layer {layer}")
                if collection_name == "soft_checkpoints" and layer != L_SOFT:
                    issues.append(f"{mid}:{cid} is in soft_checkpoints but is not L-soft")

    duplicate_ok = not duplicate_checkpoint_ids
    checks.append({"name": "unique_checkpoint_ids", "passed": duplicate_ok, "duplicates": sorted(duplicate_checkpoint_ids)})
    if duplicate_checkpoint_ids:
        issues.append(f"duplicate checkpoint ids: {', '.join(sorted(duplicate_checkpoint_ids))}")
    return checks, issues


def check_gates(
    task: dict[str, Any],
    *,
    require_metaeval: bool,
    artifact_root: Path | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    gates = task.get("gates", {})
    issues: list[str] = []
    checks: list[dict[str, Any]] = []
    gate_names = ["de_leak", "leak_audit", "de_ai"]
    if require_metaeval:
        validation = validate_construction_metareval_gate(gates.get("metareval"), task=task, artifact_root=artifact_root)
        checks.append({"name": "metareval", "passed": validation.get("passed"), "details": validation})
        issues.extend(validation.get("issues", []))
    for gate_name in gate_names:
        gate = gates.get(gate_name)
        passed = isinstance(gate, dict) and bool(gate.get("passed"))
        checks.append({"name": gate_name, "passed": passed, "details": gate})
        if not passed:
            issues.append(f"{gate_name} gate is not passed")
    return checks, issues


def check_mm_policy(task: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    issues: list[str] = []
    checkpoints = task.get("mm_task", {}).get("checkpoints", [])
    bad_refs = [
        item.get("checkpoint_id")
        for item in checkpoints
        if isinstance(item, dict) and item.get("target_crop_ref") not in {None, "internal_only"}
    ]
    passed = not bad_refs
    if bad_refs:
        issues.append("MM checkpoints expose target crop refs outside internal_only")
    return [{"name": "target_crop_internal_only", "passed": passed, "bad_checkpoint_ids": bad_refs}], issues


def check_asset_policy(
    task: dict[str, Any],
    *,
    require_existing_actor_assets: bool,
    artifact_root: Path | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    validation = validate_construction_asset_policy(
        task.get("asset_policy"),
        artifact_root=artifact_root,
        require_existing_actor_assets=require_existing_actor_assets,
    )
    gates = task.get("gates", {})
    gate = gates.get("asset_policy") if isinstance(gates, dict) else None
    gate_passed = isinstance(gate, dict) and bool(gate.get("passed")) and gate.get("status") == "passed"
    checks = [
        {
            "name": "asset_policy",
            "passed": validation.get("passed"),
            "details": validation,
        },
        {
            "name": "asset_policy_gate",
            "passed": gate_passed,
            "details": gate,
        },
    ]
    issues = list(validation.get("issues", []))
    if not gate_passed:
        issues.append("asset_policy gate is not passed")
    return checks, issues


def check_reference_trajectory(
    task: dict[str, Any],
    *,
    require_completed_artifact: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    result = validate_reference_trajectory_report(
        task,
        task.get("reference_trajectory_report"),
        trace=task.get("reference_actor_trace"),
        require_completed_artifact=require_completed_artifact,
    )
    if result["passed"]:
        return [{"name": "reference_trajectory_report", "passed": True, "details": result, "score": result.get("score")}], []
    return (
        [{"name": "reference_trajectory_report", "passed": False, "details": result, "score": result.get("score")}],
        result["issues"] or ["reference_trajectory_report did not validate"],
    )


def check_reference_actor(
    task: dict[str, Any],
    *,
    require_reference_actor: bool,
    artifact_root: Path | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    result = validate_reference_actor_trace(
        task,
        task.get("reference_actor_trace"),
        artifact_root=artifact_root,
        require_existing_artifacts=require_reference_actor,
    )
    if result["passed"]:
        return [{"name": "reference_actor_spec_only_trace", "passed": True, "details": result}], []
    if require_reference_actor:
        return (
            [{"name": "reference_actor_spec_only_trace", "passed": False, "details": result}],
            result["issues"] or ["missing passing reference_actor_trace; spec completeness is unproven"],
        )
    return [{"name": "reference_actor_spec_only_trace", "passed": None, "details": result}], []


def check_task_package_schema(task: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    if jsonschema is None:
        return [{"name": "task_package_schema", "passed": None, "details": "jsonschema unavailable"}], []
    try:
        jsonschema.validate(task, task_package_json_schema())
    except jsonschema.ValidationError as exc:
        path = ".".join(str(item) for item in exc.absolute_path)
        message = f"task package schema validation failed at {path or '<root>'}: {exc.message}"
        return [{"name": "task_package_schema", "passed": False, "details": message}], [message]
    return [{"name": "task_package_schema", "passed": True}], []


def blocking_requirements_from_issues(issues: list[str]) -> list[dict[str, Any]]:
    blockers: dict[str, dict[str, Any]] = {}

    def add(key: str, label: str, action: str, evidence: list[str], issue: str) -> None:
        item = blockers.setdefault(
            key,
            {
                "key": key,
                "label": label,
                "action": action,
                "required_evidence": evidence,
                "matched_issues": [],
            },
        )
        item["matched_issues"].append(issue)

    for issue in issues:
        lowered = issue.lower()
        if "metareval" in lowered:
            add(
                "completed_construction_metareval",
                "Completed construction meta-eval",
                "Build and attach a passed construction_metareval_report from reference, original/empty, bad-solution, and repeat trajectory evidence.",
                [
                    "construction_metareval_report",
                    "reference_report",
                    "original_report",
                    "bad_solution_report",
                    "repeat_report",
                ],
                issue,
            )
        if "reference_actor_trace" in lowered or "reference actor trace" in lowered:
            add(
                "spec_only_reference_actor_trace",
                "Spec-only reference actor trace",
                "Run the construction actor loop from actor-visible specs only, then build and attach a passed reference_actor_trace with per-milestone artifact refs.",
                [
                    "reference_actor_trace",
                    "evidence/<milestone_id>/workspace",
                    "evidence/<milestone_id>/trajectory_report.json",
                    "evidence/<milestone_id>/capture_report.json",
                    "evidence/<milestone_id>/verifier_report.json",
                ],
                issue,
            )
        if "reference_trajectory_report" in lowered or "reference trajectory" in lowered:
            add(
                "completed_reference_trajectory_report",
                "Completed reference trajectory report",
                "Finalize a reference_trajectory_report from the spec-only actor run; it must use source_kind=spec_only_actor_run and match the reference actor trace.",
                [
                    "reference_trajectory_report",
                    "source_kind=spec_only_actor_run",
                    "TCS=1.0",
                    "TD=1.0",
                    "ITR=1.0",
                ],
                issue,
            )
        if "asset_policy" in lowered:
            add(
                "validated_asset_policy",
                "Validated construction asset policy",
                "Attach a passed construction_asset_policy and asset_policy gate before strict acceptance.",
                ["construction_asset_policy", "asset_policy gate"],
                issue,
            )
        if "de_leak" in lowered or "leak_audit" in lowered:
            add(
                "passed_deleak_and_leak_audit",
                "Passed de-leak/leak-audit gates",
                "Rewrite actor-visible specs/assets until leak gates pass without source text, target images, or target crops.",
                ["de_leak gate", "leak_audit gate"],
                issue,
            )
    return list(blockers.values())


def precheck_construction_task(
    task: dict[str, Any],
    *,
    require_reference_actor: bool = True,
    artifact_root: Path | None = None,
    task_path: Path | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    issues: list[str] = []
    regime_ok = task.get("regime") == CONSTRUCTION_REGIME
    checks.append({"name": "construction_regime", "passed": regime_ok, "observed": task.get("regime")})
    if not regime_ok:
        issues.append("task regime is not construction")

    for group_checks, group_issues in (
        check_task_package_schema(task),
        check_milestones(task),
        check_gates(
            task,
            require_metaeval=require_reference_actor,
            artifact_root=artifact_root,
        ),
        check_mm_policy(task),
        check_asset_policy(
            task,
            require_existing_actor_assets=require_reference_actor,
            artifact_root=artifact_root,
        ),
        check_reference_trajectory(
            task,
            require_completed_artifact=require_reference_actor,
        ),
        check_reference_actor(
            task,
            require_reference_actor=require_reference_actor,
            artifact_root=artifact_root,
        ),
    ):
        checks.extend(group_checks)
        issues.extend(group_issues)

    hard_failures = [item for item in checks if item.get("passed") is False]
    return {
        "schema_version": "2026-06-18",
        "artifact_type": PRECHECK_ARTIFACT_TYPE,
        "formal_task_record": False,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "task_id": task.get("task_id"),
        "repo_id": task.get("repo_id"),
        "task_sha256": stable_task_digest(task),
        "input_files": {
            "task": input_file_record(task_path),
        },
        "require_reference_actor": require_reference_actor,
        "artifact_root": str(artifact_root) if artifact_root else None,
        "passed": not issues and not hard_failures,
        "issue_count": len(issues),
        "issues": issues,
        "blocking_requirements": blocking_requirements_from_issues(issues),
        "checks": checks,
    }


def add_precheck_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "construction_drafts" / "construction_precheck.json")
    parser.add_argument("--no-require-reference-actor", action="store_true", help="Run structural precheck without requiring the spec-only reference actor trace.")


def run_precheck_from_args(args: argparse.Namespace) -> None:
    try:
        assert_not_under_formal_task_root(args.output, purpose="Construction precheck report")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    ensure_dir(args.output.parent)
    task = load_json(args.task)
    report = precheck_construction_task(
        task,
        require_reference_actor=not args.no_require_reference_actor,
        artifact_root=args.task.parent,
        task_path=args.task,
    )
    write_json(args.output, report)
    print(
        f"construction precheck passed={report['passed']} "
        f"task={report.get('task_id')} issues={report['issue_count']} output={args.output}"
    )
    if not report["passed"]:
        raise SystemExit(1)
