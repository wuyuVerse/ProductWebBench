from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...core.config import DEFAULT_OUTPUT_ROOT
from ...core.formal_data_guard import assert_not_under_formal_task_root
from ...core.io_utils import ensure_dir, write_json
from .reference_actor import TRACE_SCHEMA_VERSION, candidate_artifact_paths


ASSET_POLICY_ARTIFACT_TYPE = "construction_asset_policy"
ALLOWED_ASSET_CLASSES = {"intrinsic", "decorative", "placeholder"}
ALLOWED_SOURCE_KINDS = {"original_repo", "replacement", "placeholder", "generated", "external_allowed"}
ALLOWED_REPLACEMENT_STATUS = {"not_required", "hidden", "replaced", "placeholder"}
FORBIDDEN_ASSET_PATH_TOKENS = (
    "screenshot",
    "screenshots",
    "capture",
    "render",
    "target",
    "oracle",
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "pass", "passed"}
    return bool(value)


def no_original_asset_policy(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": TRACE_SCHEMA_VERSION,
        "artifact_type": ASSET_POLICY_ARTIFACT_TYPE,
        "formal_task_record": False,
        "generated_at": utc_now(),
        "task_id": task.get("task_id"),
        "repo_id": task.get("repo_id"),
        "passed": True,
        "policy": "no_actor_visible_original_assets",
        "provided_assets": [],
        "target_render_refs": [],
        "notes": (
            "No original repo assets are provided to the construction actor. "
            "Asset slots must be satisfied with placeholders, replacements, or later explicitly classified intrinsic assets."
        ),
    }


def normalized_items(policy: dict[str, Any]) -> list[dict[str, Any]]:
    items = policy.get("provided_assets", [])
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def asset_path_values(item: dict[str, Any]) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    for key in ("source_path", "actor_visible_path", "replacement_path"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            values.append((key, value.strip()))
    return values


def path_is_target_like(path: str) -> bool:
    lowered = path.lower().replace("\\", "/")
    return any(token in lowered for token in FORBIDDEN_ASSET_PATH_TOKENS)


def existing_asset_path(raw_path: str, artifact_root: Path | None) -> Path | None:
    return next((candidate for candidate in candidate_artifact_paths(raw_path, artifact_root) if candidate.exists()), None)


def validate_construction_asset_policy(
    policy: dict[str, Any] | None,
    *,
    artifact_root: Path | None = None,
    require_existing_actor_assets: bool = False,
) -> dict[str, Any]:
    issues: list[str] = []
    checks: list[dict[str, Any]] = []

    if not isinstance(policy, dict):
        return {
            "name": "construction_asset_policy",
            "passed": False,
            "issues": ["missing construction asset_policy"],
            "checks": [{"name": "asset_policy_present", "passed": False}],
        }

    def add_check(name: str, passed: bool, **details: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), **details})
        if not passed:
            issues.append(details.get("issue") or name)

    add_check(
        "asset_policy_schema_version",
        policy.get("schema_version") == TRACE_SCHEMA_VERSION,
        observed=policy.get("schema_version"),
        expected=TRACE_SCHEMA_VERSION,
        issue="construction asset_policy has wrong schema_version",
    )
    add_check(
        "asset_policy_artifact_type",
        policy.get("artifact_type") == ASSET_POLICY_ARTIFACT_TYPE,
        observed=policy.get("artifact_type"),
        expected=ASSET_POLICY_ARTIFACT_TYPE,
        issue="construction asset_policy has wrong artifact_type",
    )
    add_check(
        "asset_policy_not_formal_record",
        policy.get("formal_task_record") is False,
        observed=policy.get("formal_task_record"),
        issue="construction asset_policy must be a non-formal policy artifact",
    )
    add_check(
        "asset_policy_passed",
        boolish(policy.get("passed")),
        issue="construction asset_policy did not pass",
    )

    items_raw = policy.get("provided_assets", [])
    items = normalized_items(policy)
    add_check(
        "provided_assets_is_list",
        isinstance(items_raw, list) and len(items) == len(items_raw),
        observed_type=type(items_raw).__name__,
        issue="construction asset_policy provided_assets must be a list of objects",
    )

    target_refs = policy.get("target_render_refs", [])
    target_refs_ok = target_refs is None or target_refs == []
    add_check(
        "no_target_render_refs",
        target_refs_ok,
        observed=target_refs,
        issue="construction asset_policy must not expose target render refs",
    )

    seen_ids: set[str] = set()
    duplicate_ids: set[str] = set()
    actor_visible_assets: list[str] = []
    missing_actor_asset_paths: dict[str, list[str]] = {}
    target_like_paths: dict[str, list[str]] = {}
    decorative_raw_exposed: list[str] = []
    placeholder_original_exposed: list[str] = []
    intrinsic_without_reason: list[str] = []
    invalid_classes: dict[str, str] = {}
    invalid_source_kinds: dict[str, str] = {}
    invalid_replacement_status: dict[str, str] = {}

    for index, item in enumerate(items):
        asset_id = str(item.get("asset_id") or item.get("id") or f"asset_{index + 1}")
        if asset_id in seen_ids:
            duplicate_ids.add(asset_id)
        seen_ids.add(asset_id)

        classification = str(item.get("classification", "")).strip()
        source_kind = str(item.get("source_kind", "")).strip() or "original_repo"
        replacement_status = str(item.get("replacement_status", "")).strip() or "not_required"
        actor_visible = boolish(item.get("actor_visible"))
        intrinsic_reason = str(item.get("intrinsic_reason") or item.get("reason") or "").strip()

        if classification not in ALLOWED_ASSET_CLASSES:
            invalid_classes[asset_id] = classification
        if source_kind not in ALLOWED_SOURCE_KINDS:
            invalid_source_kinds[asset_id] = source_kind
        if replacement_status not in ALLOWED_REPLACEMENT_STATUS:
            invalid_replacement_status[asset_id] = replacement_status
        if classification == "intrinsic" and actor_visible and len(intrinsic_reason) < 12:
            intrinsic_without_reason.append(asset_id)
        if classification == "decorative" and actor_visible and not (
            replacement_status in {"replaced", "placeholder"} and source_kind in {"replacement", "placeholder", "generated", "external_allowed"}
        ):
            decorative_raw_exposed.append(asset_id)
        if classification == "placeholder" and actor_visible and source_kind == "original_repo":
            placeholder_original_exposed.append(asset_id)

        paths_for_item = asset_path_values(item)
        bad_paths = [path for _, path in paths_for_item if path_is_target_like(path)]
        if bad_paths:
            target_like_paths[asset_id] = bad_paths
        actor_visible_path = item.get("actor_visible_path")
        if actor_visible:
            actor_visible_assets.append(asset_id)
            if require_existing_actor_assets:
                if not isinstance(actor_visible_path, str) or not actor_visible_path.strip():
                    missing_actor_asset_paths[asset_id] = []
                else:
                    existing = existing_asset_path(actor_visible_path.strip(), artifact_root)
                    if existing is None:
                        missing_actor_asset_paths[asset_id] = [
                            str(candidate) for candidate in candidate_artifact_paths(actor_visible_path.strip(), artifact_root)
                        ]

    add_check(
        "asset_ids_unique",
        not duplicate_ids,
        duplicates=sorted(duplicate_ids),
        issue="construction asset_policy contains duplicate asset_id values",
    )
    add_check(
        "asset_classes_valid",
        not invalid_classes,
        invalid_classes=invalid_classes,
        allowed=sorted(ALLOWED_ASSET_CLASSES),
        issue="construction asset_policy has invalid asset classifications",
    )
    add_check(
        "asset_source_kinds_valid",
        not invalid_source_kinds,
        invalid_source_kinds=invalid_source_kinds,
        allowed=sorted(ALLOWED_SOURCE_KINDS),
        issue="construction asset_policy has invalid source_kind values",
    )
    add_check(
        "asset_replacement_status_valid",
        not invalid_replacement_status,
        invalid_replacement_status=invalid_replacement_status,
        allowed=sorted(ALLOWED_REPLACEMENT_STATUS),
        issue="construction asset_policy has invalid replacement_status values",
    )
    add_check(
        "intrinsic_assets_justified",
        not intrinsic_without_reason,
        asset_ids=intrinsic_without_reason,
        issue="actor-visible intrinsic assets need an intrinsic_reason",
    )
    add_check(
        "decorative_assets_not_exposed_raw",
        not decorative_raw_exposed,
        asset_ids=decorative_raw_exposed,
        issue="actor-visible decorative assets must be replaced or converted to placeholders",
    )
    add_check(
        "placeholders_not_original_repo_assets",
        not placeholder_original_exposed,
        asset_ids=placeholder_original_exposed,
        issue="actor-visible placeholder assets must not be original repo assets",
    )
    add_check(
        "asset_paths_do_not_reference_targets",
        not target_like_paths,
        target_like_paths=target_like_paths,
        issue="asset paths appear to reference target render/capture/oracle files",
    )
    if require_existing_actor_assets:
        add_check(
            "actor_visible_asset_paths_exist",
            not missing_actor_asset_paths,
            artifact_root=str(artifact_root) if artifact_root else None,
            missing_actor_asset_paths=missing_actor_asset_paths,
            issue="actor-visible asset paths do not exist",
        )

    return {
        "name": "construction_asset_policy",
        "passed": not issues,
        "issues": issues,
        "checks": checks,
        "details": {
            "provided_asset_count": len(items),
            "actor_visible_asset_count": len(actor_visible_assets),
            "actor_visible_assets": actor_visible_assets,
            "require_existing_actor_assets": require_existing_actor_assets,
            "artifact_root": str(artifact_root) if artifact_root else None,
        },
    }


def attach_construction_asset_policy(task: dict[str, Any], policy: dict[str, Any], *, policy_path: Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    validation = validate_construction_asset_policy(
        policy,
        artifact_root=policy_path.parent if policy_path else None,
        require_existing_actor_assets=False,
    )
    if not validation.get("passed"):
        return task, validation

    updated = dict(task)
    updated["asset_policy"] = policy
    gates = dict(updated.get("gates", {}))
    gates["asset_policy"] = {
        "gate": "asset_policy",
        "passed": True,
        "status": "passed",
        "report_paths": [],
        "summary": {
            "artifact_type": policy.get("artifact_type"),
            "provided_asset_count": validation.get("details", {}).get("provided_asset_count"),
            "actor_visible_asset_count": validation.get("details", {}).get("actor_visible_asset_count"),
            "policy": policy.get("policy"),
        },
    }
    updated["gates"] = gates
    return updated, validation


def add_template_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "construction_drafts" / "construction_asset_policy.template.json")


def add_validate_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--require-existing-actor-assets", action="store_true")


def add_attach_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--in-place", action="store_true")
    parser.add_argument("--validation-output", type=Path, default=None)


def run_template_from_args(args: argparse.Namespace) -> None:
    try:
        assert_not_under_formal_task_root(args.output, purpose="Construction asset policy template")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    task = load_json(args.task)
    policy = no_original_asset_policy(task)
    ensure_dir(args.output.parent)
    write_json(args.output, policy)
    print(f"wrote construction asset policy template to {args.output}")


def run_validate_from_args(args: argparse.Namespace) -> None:
    if args.output is not None:
        try:
            assert_not_under_formal_task_root(args.output, purpose="Construction asset policy validation report")
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    policy = load_json(args.policy)
    validation = validate_construction_asset_policy(
        policy,
        artifact_root=args.policy.parent,
        require_existing_actor_assets=args.require_existing_actor_assets,
    )
    if args.output is not None:
        ensure_dir(args.output.parent)
        write_json(args.output, validation)
    print(
        "construction asset policy validation "
        f"passed={validation['passed']} issues={len(validation.get('issues', []))}"
    )
    if not validation.get("passed"):
        raise SystemExit("; ".join(validation.get("issues", [])))


def run_attach_from_args(args: argparse.Namespace) -> None:
    if args.validation_output is not None:
        try:
            assert_not_under_formal_task_root(
                args.validation_output,
                purpose="Construction asset policy attach validation report",
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    task = load_json(args.task)
    policy = load_json(args.policy)
    updated, validation = attach_construction_asset_policy(task, policy, policy_path=args.policy)
    if args.validation_output is not None:
        ensure_dir(args.validation_output.parent)
        write_json(args.validation_output, validation)
    if not validation.get("passed"):
        raise SystemExit(
            "construction asset_policy did not validate; task was not updated: "
            + "; ".join(validation.get("issues", []))
        )
    if args.in_place:
        output = args.task
    elif args.output is not None:
        output = args.output
    else:
        output = args.task.with_name(f"{args.task.stem}.with_asset_policy.json")
    try:
        assert_not_under_formal_task_root(output, purpose="Construction task with attached asset policy")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    ensure_dir(output.parent)
    write_json(output, updated)
    print(f"attached validated construction asset_policy to {output}")
