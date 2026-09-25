from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..core.config import DEFAULT_OUTPUT_ROOT
from ..core.formal_data_guard import assert_not_under_formal_task_root
from ..core.io_utils import ensure_dir, write_json


ARTIFACT_TYPE = "model_isolation_audit"
MANIFEST_ARTIFACT_TYPE = "model_role_manifest"
ROLE_QUEUE_ARTIFACT_TYPE = "model_role_queue"
ROLE_APPEND_AUDIT_ARTIFACT_TYPE = "model_role_append_audit"
DEFAULT_MANIFEST = DEFAULT_OUTPUT_ROOT / "eval_protocol" / "model_roles.json"
DEFAULT_OUTPUT = DEFAULT_OUTPUT_ROOT / "eval_protocol" / "model_isolation_audit.json"
DEFAULT_ROLE_QUEUE = DEFAULT_OUTPUT_ROOT / "eval_protocol" / "model_role_queue.json"
DEFAULT_JUDGE_PREDICTION_AUDIT = DEFAULT_OUTPUT_ROOT / "mm_calibration" / "change_judge_predictions.audit.json"
DEFAULT_JUDGE_PERTURBATION_AUDIT = DEFAULT_OUTPUT_ROOT / "mm_calibration" / "mm_judge_perturbation_audit.json"
PREDICTION_AUDIT_ARTIFACT_TYPE = "mm_judge_prediction_audit"
PERTURBATION_AUDIT_ARTIFACT_TYPE = "mm_judge_perturbation_audit"
MIN_PUBLISHABLE_PERTURBATION_GROUPS = 12
REQUIRED_PUBLISHABLE_PERTURBATION_TYPES = {"position", "palette", "length", "style"}
ROLE_KIND_ALIASES = {
    "author": "spec",
    "task_author": "spec",
    "spec": "spec",
    "spec_author": "spec",
    "judge": "judge",
    "mm_judge": "judge",
    "actor": "actor",
    "change_actor": "actor",
    "construction_actor": "actor",
    "tested_actor": "actor",
    "user": "user",
    "feedback_user": "user",
}


def assert_nonformal_output(path: Path | None, *, purpose: str) -> None:
    if path is not None:
        try:
            assert_not_under_formal_task_root(path, purpose=purpose)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
STRICT_ROLE_KINDS = ("spec", "judge", "actor")
MM_REQUIRED_ROLE_KINDS = ("spec", "judge", "actor")
UNKNOWN_VALUES = {"", "unknown", "unk", "n/a", "na", "none", "null", "tbd", "todo"}


def load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
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
        "exists": bool(path is not None and path.exists()),
        "sha256": file_sha256(path),
    }


def audit_input_binding_issues(audit: dict[str, Any], key: str, *, label: str) -> list[str]:
    issues: list[str] = []
    input_files = audit.get("input_files")
    if not isinstance(input_files, dict):
        return [f"{label} audit input_files is missing or malformed"]
    record = input_files.get(key)
    if not isinstance(record, dict):
        return [f"{label} audit input_files.{key} is missing or malformed"]
    raw_path = record.get("path")
    if not raw_path:
        return [f"{label} audit input_files.{key}.path is missing"]
    path = Path(str(raw_path))
    if not path.exists():
        issues.append(f"{label} audit input_files.{key}.path does not exist: {path}")
        return issues
    expected_sha = record.get("sha256")
    actual_sha = file_sha256(path)
    if not expected_sha:
        issues.append(f"{label} audit input_files.{key}.sha256 is missing")
    elif expected_sha != actual_sha:
        issues.append(f"{label} audit input_files.{key}.sha256 is stale")
    return issues


def normalize_token(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def normalize_role_kind(value: object) -> str:
    token = normalize_token(value)
    return ROLE_KIND_ALIASES.get(token, token)


def infer_family(provider: object, model: object, explicit_family: object = None) -> str:
    explicit = normalize_token(explicit_family)
    if explicit and explicit not in UNKNOWN_VALUES:
        return explicit
    text = normalize_token(f"{provider or ''} {model or ''}")
    family_patterns = (
        ("gpt", ("gpt", "openai", "chatgpt")),
        ("claude", ("claude", "anthropic")),
        ("qwen", ("qwen", "qwq", "tongyi")),
        ("kimi", ("kimi", "moonshot")),
        ("glm", ("glm", "zhipu")),
        ("minimax", ("minimax", "abab")),
        ("gemini", ("gemini", "google")),
        ("deepseek", ("deepseek",)),
        ("llama", ("llama", "meta")),
        ("mistral", ("mistral", "mixtral")),
    )
    for family, needles in family_patterns:
        if any(needle in text for needle in needles):
            return family
    return "unknown"


def role_entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    roles = manifest.get("roles", [])
    if isinstance(roles, dict):
        expanded = []
        for role_name, value in roles.items():
            if isinstance(value, dict):
                expanded.append({"role": role_name, **value})
        return expanded
    if isinstance(roles, list):
        return [item for item in roles if isinstance(item, dict)]
    return []


def normalize_roles(manifest: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    issues: list[str] = []
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, role in enumerate(role_entries(manifest), start=1):
        role_name = str(role.get("role") or role.get("name") or f"role_{index}")
        kind = normalize_role_kind(role.get("kind") or role.get("role_kind") or role_name)
        provider = str(role.get("provider") or "")
        model = str(role.get("model") or "")
        family = infer_family(provider, model, role.get("family"))
        role_id = normalize_token(role.get("role_id") or role_name)
        if role_id in seen_ids:
            issues.append(f"duplicate role_id: {role_id}")
        seen_ids.add(role_id)
        if not role_id:
            issues.append(f"role {index} has empty role_id")
        if kind not in ROLE_KIND_ALIASES.values():
            issues.append(f"role {role_name} has unsupported kind: {kind}")
        if normalize_token(provider) in UNKNOWN_VALUES:
            issues.append(f"role {role_name} is missing provider")
        if normalize_token(model) in UNKNOWN_VALUES:
            issues.append(f"role {role_name} is missing model")
        if family in UNKNOWN_VALUES or family == "unknown":
            issues.append(f"role {role_name} has unknown model family")
        temperature = role.get("temperature")
        if kind == "judge" and temperature is not None:
            try:
                if float(temperature) != 0.0:
                    issues.append(f"judge role {role_name} temperature must be 0 for reproducible MM scoring")
            except (TypeError, ValueError):
                issues.append(f"judge role {role_name} has invalid temperature: {temperature}")
        normalized.append(
            {
                **role,
                "role_id": role_id,
                "role": role_name,
                "kind": kind,
                "provider": provider,
                "model": model,
                "family": family,
            }
        )
    return normalized, issues


def audit_model_isolation(
    manifest_path: Path,
    output_path: Path | None = None,
    *,
    require_mm_roles: bool = True,
    judge_prediction_audit_path: Path | None = DEFAULT_JUDGE_PREDICTION_AUDIT,
    judge_perturbation_audit_path: Path | None = DEFAULT_JUDGE_PERTURBATION_AUDIT,
    require_judge_audit_binding: bool = True,
) -> dict[str, Any]:
    issues: list[str] = []
    manifest = load_json_if_exists(manifest_path)
    if not manifest:
        issues.append(f"model role manifest missing: {manifest_path}")
        roles: list[dict[str, Any]] = []
        normalization_issues: list[str] = []
    else:
        roles, normalization_issues = normalize_roles(manifest)
        issues.extend(normalization_issues)
        artifact_type = manifest.get("artifact_type")
        if artifact_type and artifact_type != MANIFEST_ARTIFACT_TYPE:
            issues.append(f"model role manifest has unexpected artifact_type: {artifact_type}")
        if manifest.get("formal_task_record") is not False:
            issues.append("model role manifest must be marked formal_task_record=false")
        if manifest.get("template_only") is True:
            issues.append("model role manifest is template_only and cannot satisfy model isolation")
        if manifest.get("template_warning"):
            issues.append("model role manifest still contains template_warning and must be replaced by real run roles")

    by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for role in roles:
        by_kind[role["kind"]].append(role)

    if require_mm_roles:
        for kind in MM_REQUIRED_ROLE_KINDS:
            if not by_kind.get(kind):
                issues.append(f"missing required model role kind for MM isolation: {kind}")

    strict_family_to_kinds: dict[str, set[str]] = defaultdict(set)
    for role in roles:
        if role["kind"] in STRICT_ROLE_KINDS and role["family"] not in UNKNOWN_VALUES and role["family"] != "unknown":
            strict_family_to_kinds[role["family"]].add(role["kind"])
    family_collisions = {
        family: sorted(kinds)
        for family, kinds in strict_family_to_kinds.items()
        if len(kinds) > 1
    }
    for family, kinds in sorted(family_collisions.items()):
        issues.append(f"model family {family} is shared across isolated roles: {', '.join(kinds)}")

    judge_families = {role["family"] for role in by_kind.get("judge", [])}
    actor_families = {role["family"] for role in by_kind.get("actor", [])}
    spec_families = {role["family"] for role in by_kind.get("spec", [])}
    judge_bindings: list[dict[str, Any]] = []
    if require_judge_audit_binding:
        judge_roles = by_kind.get("judge", [])

        def bind_judge_audit(path: Path | None, label: str, expected_artifact_type: str, publishable_field: str) -> None:
            if path is None:
                issues.append(f"{label} audit path is not configured")
                return
            audit = load_json_if_exists(path)
            if not audit:
                issues.append(f"{label} audit missing: {path}")
                judge_bindings.append({"label": label, "path": str(path), "passed": False, "issues": ["missing audit"]})
                return
            binding_issues: list[str] = []
            if audit.get("artifact_type") != expected_artifact_type:
                binding_issues.append(f"{label} audit has wrong artifact_type: {audit.get('artifact_type')}")
            if audit.get("formal_task_record") is not False:
                binding_issues.append(f"{label} audit must have formal_task_record=false")
            if audit.get("passed") is not True:
                binding_issues.append(f"{label} audit did not pass")
            if audit.get(publishable_field) is not True:
                binding_issues.append(f"{label} audit is not publishable via {publishable_field}=true")
            if audit.get("issue_count") not in (0, None):
                binding_issues.append(f"{label} audit issue_count is nonzero: {audit.get('issue_count')}")
            if audit.get("publish_blockers"):
                binding_issues.append(f"{label} audit has publish_blockers: {audit.get('publish_blockers')}")
            if label == "judge_prediction":
                binding_issues.extend(audit_input_binding_issues(audit, "predictions", label=label))
                binding_issues.extend(audit_input_binding_issues(audit, "sample", label=label))
                if not audit.get("predictions_path"):
                    binding_issues.append("judge_prediction audit missing predictions_path")
                if not audit.get("sample_path"):
                    binding_issues.append("judge_prediction audit missing sample_path")
                if audit.get("require_complete") is not True:
                    binding_issues.append("judge_prediction audit must be complete for the calibration sample")
                if int(audit.get("prediction_records") or 0) <= 0:
                    binding_issues.append("judge_prediction audit has no prediction records")
                if audit.get("usable_prediction_records") != audit.get("prediction_records"):
                    binding_issues.append("judge_prediction audit usable_prediction_records must equal prediction_records")
                if int(audit.get("expected_checkpoint_count") or 0) <= 0:
                    binding_issues.append("judge_prediction audit has no expected checkpoints")
                if audit.get("missing_expected_count") not in (0, None):
                    binding_issues.append("judge_prediction audit missing_expected_count must be zero")
                if audit.get("extra_prediction_count") not in (0, None):
                    binding_issues.append("judge_prediction audit extra_prediction_count must be zero")
            if label == "judge_perturbation":
                binding_issues.extend(audit_input_binding_issues(audit, "perturbations", label=label))
                if not audit.get("perturbations_path"):
                    binding_issues.append("judge_perturbation audit missing perturbations_path")
                if int(audit.get("record_count") or 0) <= 0:
                    binding_issues.append("judge_perturbation audit has no perturbation records")
                if audit.get("usable_record_count") != audit.get("record_count"):
                    binding_issues.append("judge_perturbation audit usable_record_count must equal record_count")
                if int(audit.get("group_count") or 0) < MIN_PUBLISHABLE_PERTURBATION_GROUPS:
                    binding_issues.append(
                        f"judge_perturbation audit group_count must be >= {MIN_PUBLISHABLE_PERTURBATION_GROUPS}"
                    )
                if int(audit.get("min_groups") or 0) < MIN_PUBLISHABLE_PERTURBATION_GROUPS:
                    binding_issues.append(
                        f"judge_perturbation audit min_groups must be >= {MIN_PUBLISHABLE_PERTURBATION_GROUPS}"
                    )
                required_types = {str(item) for item in audit.get("required_perturbation_types") or []}
                if required_types != REQUIRED_PUBLISHABLE_PERTURBATION_TYPES:
                    binding_issues.append("judge_perturbation audit must require position, palette, length, and style")
                observed_types = {str(item) for item in audit.get("perturbation_types") or []}
                if not REQUIRED_PUBLISHABLE_PERTURBATION_TYPES.issubset(observed_types):
                    binding_issues.append("judge_perturbation audit does not cover all required perturbation types")
                if audit.get("missing_required_perturbation_types"):
                    binding_issues.append("judge_perturbation audit still has missing required perturbation types")
                if audit.get("label_flip_group_count") not in (0, None):
                    binding_issues.append("judge_perturbation audit label_flip_group_count must be zero")
                if audit.get("invalid_record_count") not in (0, None):
                    binding_issues.append("judge_perturbation audit invalid_record_count must be zero")
                if audit.get("invalid_group_count") not in (0, None):
                    binding_issues.append("judge_perturbation audit invalid_group_count must be zero")
            audit_provider = str(audit.get("provider") or "")
            audit_model = str(audit.get("model") or "")
            audit_family = infer_family(
                audit_provider,
                audit_model,
                audit.get("model_family") or (audit.get("metadata") or {}).get("model_family"),
            )
            audit_temperature = audit.get("temperature")
            if normalize_token(audit_provider) in UNKNOWN_VALUES:
                binding_issues.append(f"{label} audit is missing provider")
            if normalize_token(audit_model) in UNKNOWN_VALUES:
                binding_issues.append(f"{label} audit is missing model")
            try:
                audit_temp_ok = float(audit_temperature) == 0.0
            except (TypeError, ValueError):
                audit_temp_ok = False
            if not audit_temp_ok:
                binding_issues.append(f"{label} audit temperature must be 0, got {audit_temperature}")
            matching_roles = [
                role
                for role in judge_roles
                if normalize_token(role.get("provider")) == normalize_token(audit_provider)
                and normalize_token(role.get("model")) == normalize_token(audit_model)
                and role.get("family") == audit_family
            ]
            if not matching_roles:
                binding_issues.append(
                    f"{label} audit provider/model/family does not match any judge role: "
                    f"{audit_provider}/{audit_model}/{audit_family}"
                )
            if binding_issues:
                issues.extend(binding_issues)
            judge_bindings.append(
                {
                    "label": label,
                    "path": str(path),
                    "passed": not binding_issues,
                    "issues": binding_issues,
                    "audit_provider": audit_provider,
                    "audit_model": audit_model,
                    "audit_family": audit_family,
                    "audit_temperature": audit_temperature,
                    "matching_judge_role_ids": [role.get("role_id") for role in matching_roles],
                }
            )

        bind_judge_audit(judge_prediction_audit_path, "judge_prediction", PREDICTION_AUDIT_ARTIFACT_TYPE, "publishable_candidate")
        bind_judge_audit(judge_perturbation_audit_path, "judge_perturbation", PERTURBATION_AUDIT_ARTIFACT_TYPE, "publishable")
    publish_blockers: list[str] = []
    if not require_mm_roles:
        publish_blockers.append("model isolation audit without required MM roles is diagnostic-only")
    if not require_judge_audit_binding:
        publish_blockers.append("model isolation audit without judge prediction/perturbation binding is diagnostic-only")
    publishable = not issues and not publish_blockers
    report = {
        "schema_version": "2026-06-19",
        "artifact_type": ARTIFACT_TYPE,
        "formal_task_record": False,
        "manifest_path": str(manifest_path),
        "manifest_available": bool(manifest),
        "input_files": {
            "manifest": input_file_record(manifest_path),
            "judge_prediction_audit": input_file_record(judge_prediction_audit_path),
            "judge_perturbation_audit": input_file_record(judge_perturbation_audit_path),
        },
        "require_mm_roles": require_mm_roles,
        "require_judge_audit_binding": require_judge_audit_binding,
        "passed": not issues,
        "publishable": publishable,
        "publish_blockers": publish_blockers,
        "issue_count": len(issues),
        "issues": issues,
        "role_count": len(roles),
        "role_kind_counts": {kind: len(items) for kind, items in sorted(by_kind.items())},
        "families_by_kind": {
            "spec": sorted(spec_families),
            "judge": sorted(judge_families),
            "actor": sorted(actor_families),
            "user": sorted({role["family"] for role in by_kind.get("user", [])}),
        },
        "family_collisions": family_collisions,
        "judge_audit_bindings": judge_bindings,
        "roles": roles,
        "gate": "MM publishable views require spec author, MM judge, and tested actor roles to use disjoint model families; the judge role must match real temperature-0 judge prediction and perturbation audits.",
    }
    if output_path is not None:
        write_json(output_path, report)
    return report


def role_record(
    *,
    role: str,
    kind: str,
    provider: str,
    model: str,
    family: str | None = None,
    temperature: float | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "role_id": normalize_token(role),
        "role": role,
        "kind": kind,
        "provider": provider,
        "model": model,
    }
    if family:
        record["family"] = family
    if temperature is not None:
        record["temperature"] = temperature
    return record


def build_model_role_manifest(
    *,
    spec_provider: str,
    spec_model: str,
    judge_provider: str,
    judge_model: str,
    actor_provider: str,
    actor_model: str,
    spec_family: str | None = None,
    judge_family: str | None = None,
    actor_family: str | None = None,
    judge_temperature: float = 0.0,
    user_provider: str | None = None,
    user_model: str | None = None,
    user_family: str | None = None,
) -> dict[str, Any]:
    roles = [
        role_record(
            role="spec_author",
            kind="spec",
            provider=spec_provider,
            model=spec_model,
            family=spec_family,
        ),
        role_record(
            role="mm_judge",
            kind="judge",
            provider=judge_provider,
            model=judge_model,
            family=judge_family,
            temperature=judge_temperature,
        ),
        role_record(
            role="tested_actor",
            kind="actor",
            provider=actor_provider,
            model=actor_model,
            family=actor_family,
        ),
    ]
    if user_provider or user_model or user_family:
        roles.append(
            role_record(
                role="feedback_user",
                kind="user",
                provider=user_provider or "",
                model=user_model or "",
                family=user_family,
            )
        )
    return {
        "schema_version": "2026-06-19",
        "artifact_type": MANIFEST_ARTIFACT_TYPE,
        "formal_task_record": False,
        "intended_use": "Non-formal model role manifest for ProductWebBench MM publish gates.",
        "roles": roles,
    }


def model_role_manifest_template() -> dict[str, Any]:
    return {
        "schema_version": "2026-06-19",
        "artifact_type": MANIFEST_ARTIFACT_TYPE,
        "formal_task_record": False,
        "template_only": True,
        "template_warning": "Fill provider/model/family with real run roles. This template is not a passing model-isolation manifest.",
        "roles": [
            {
                "role_id": "spec_author",
                "role": "spec_author",
                "kind": "spec",
                "provider": "TBD",
                "model": "TBD",
                "family": "TBD",
            },
            {
                "role_id": "mm_judge",
                "role": "mm_judge",
                "kind": "judge",
                "provider": "TBD",
                "model": "TBD",
                "family": "TBD",
                "temperature": 0.0,
            },
            {
                "role_id": "tested_actor",
                "role": "tested_actor",
                "kind": "actor",
                "provider": "TBD",
                "model": "TBD",
                "family": "TBD",
            },
        ],
    }


def base_model_role_manifest() -> dict[str, Any]:
    return {
        "schema_version": "2026-06-19",
        "artifact_type": MANIFEST_ARTIFACT_TYPE,
        "formal_task_record": False,
        "intended_use": "Non-formal model role manifest for ProductWebBench MM publish gates.",
        "roles": [],
    }


def build_model_role_queue(
    *,
    manifest_path: Path,
    output_path: Path,
    judge_prediction_audit_path: Path | None = DEFAULT_JUDGE_PREDICTION_AUDIT,
    judge_perturbation_audit_path: Path | None = DEFAULT_JUDGE_PERTURBATION_AUDIT,
    include_user_role: bool = False,
) -> dict[str, Any]:
    manifest = load_json_if_exists(manifest_path)
    issues: list[str] = []
    roles: list[dict[str, Any]] = []
    normalization_issues: list[str] = []
    if manifest:
        if manifest.get("artifact_type") and manifest.get("artifact_type") != MANIFEST_ARTIFACT_TYPE:
            issues.append(f"model role manifest has unexpected artifact_type: {manifest.get('artifact_type')}")
        if manifest.get("formal_task_record") is not False:
            issues.append("model role manifest must be marked formal_task_record=false")
        if manifest.get("template_only") is True:
            issues.append("template_only model role manifest cannot count as real roles")
        if manifest.get("template_warning"):
            issues.append("model role manifest still contains template_warning")
        roles, normalization_issues = normalize_roles(manifest)
        issues.extend(normalization_issues)

    valid_roles_by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for role in roles:
        role_id = normalize_token(role.get("role_id") or role.get("role"))
        kind = normalize_role_kind(role.get("kind"))
        provider = normalize_token(role.get("provider"))
        model = normalize_token(role.get("model"))
        family = normalize_token(role.get("family"))
        if not role_id or provider in UNKNOWN_VALUES or model in UNKNOWN_VALUES or family in UNKNOWN_VALUES:
            continue
        if role.get("family") == "unknown":
            continue
        if kind in ROLE_KIND_ALIASES.values():
            valid_roles_by_kind[kind].append(role)

    required_roles = [
        {
            "role": "spec_author",
            "kind": "spec",
            "temperature_required": None,
            "purpose": "Task/spec authoring model family; must be disjoint from judge and actor.",
        },
        {
            "role": "mm_judge",
            "kind": "judge",
            "temperature_required": 0,
            "purpose": "Independent MM judge model used for prediction and perturbation audits.",
        },
        {
            "role": "tested_actor",
            "kind": "actor",
            "temperature_required": None,
            "purpose": "Model family being evaluated as the actor; must be disjoint from spec and judge.",
        },
    ]
    if include_user_role:
        required_roles.append(
            {
                "role": "feedback_user",
                "kind": "user",
                "temperature_required": None,
                "purpose": "Optional verifier-grounded feedback/user model for construction loops.",
            }
        )

    strict_families: dict[str, set[str]] = defaultdict(set)
    for role in roles:
        kind = normalize_role_kind(role.get("kind"))
        family = infer_family(role.get("provider"), role.get("model"), role.get("family"))
        if kind in STRICT_ROLE_KINDS and family not in UNKNOWN_VALUES and family != "unknown":
            strict_families[family].add(kind)
    family_collisions = {
        family: sorted(kinds)
        for family, kinds in strict_families.items()
        if len(kinds) > 1
    }
    if family_collisions:
        for family, kinds in sorted(family_collisions.items()):
            issues.append(f"existing model family collision {family}: {', '.join(kinds)}")

    queue_items: list[dict[str, Any]] = []
    for spec in required_roles:
        kind = spec["kind"]
        if valid_roles_by_kind.get(kind):
            continue
        temperature_arg = " --temperature 0" if spec["temperature_required"] == 0 else ""
        queue_items.append(
            {
                "queue_index": len(queue_items) + 1,
                "role": spec["role"],
                "kind": kind,
                "purpose": spec["purpose"],
                "temperature_required": spec["temperature_required"],
                "manifest_path": str(manifest_path),
                "authoring_requirements": {
                    "must_use_real_run_role": True,
                    "must_record_provider_model_family": True,
                    "strict_family_disjoint_from": [item["kind"] for item in required_roles if item["kind"] in STRICT_ROLE_KINDS and item["kind"] != kind],
                    "judge_binding": (
                        "Judge provider/model/family must match the publishable prediction and perturbation audits."
                        if kind == "judge"
                        else None
                    ),
                    "forbidden": [
                        "do not use TBD/unknown provider, model, or family",
                        "do not reuse a model family across spec, judge, and actor",
                        "do not count template roles as real run roles",
                    ],
                },
                "append_command": (
                    "python -m productwebbench append-model-role "
                    f"--manifest {manifest_path} "
                    f"--role {spec['role']} "
                    f"--kind {kind} "
                    "--provider <provider> "
                    "--model <model> "
                    "--family <family>"
                    f"{temperature_arg}"
                ),
            }
        )

    report = {
        "schema_version": "2026-06-19",
        "artifact_type": ROLE_QUEUE_ARTIFACT_TYPE,
        "formal_task_record": False,
        "manifest_path": str(manifest_path),
        "output_path": str(output_path),
        "input_files": {
            "manifest": input_file_record(manifest_path),
            "judge_prediction_audit": input_file_record(judge_prediction_audit_path),
            "judge_perturbation_audit": input_file_record(judge_perturbation_audit_path),
        },
        "passed": not issues,
        "issue_count": len(issues),
        "issues": issues,
        "manifest_available": bool(manifest),
        "existing_role_count": len(roles),
        "valid_role_count": sum(len(items) for items in valid_roles_by_kind.values()),
        "valid_role_kind_counts": {kind: len(items) for kind, items in sorted(valid_roles_by_kind.items())},
        "required_role_kinds": [item["kind"] for item in required_roles],
        "missing_role_count": len(queue_items),
        "queue_item_count": len(queue_items),
        "family_collisions": family_collisions,
        "queue_items": queue_items,
        "gate": "This queue schedules one real model role append at a time; it must not invent provider/model/family metadata or satisfy model isolation by template placeholders.",
    }
    write_json(output_path, report)
    return report


def append_model_role(
    manifest_path: Path,
    *,
    role: str,
    kind: str,
    provider: str,
    model: str,
    family: str | None = None,
    temperature: float | None = None,
    append_audit_output_path: Path | None = None,
    isolation_output_path: Path | None = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    issues: list[str] = []
    existing_manifest = load_json_if_exists(manifest_path)
    manifest = existing_manifest or base_model_role_manifest()
    if manifest.get("template_only") is True:
        issues.append("cannot append to a template_only model role manifest")
    if manifest.get("template_warning"):
        issues.append("cannot append to a manifest that still contains template_warning")
    if manifest.get("artifact_type") and manifest.get("artifact_type") != MANIFEST_ARTIFACT_TYPE:
        issues.append(f"manifest has unexpected artifact_type: {manifest.get('artifact_type')}")
    if manifest.get("formal_task_record") is not False:
        issues.append("model role manifest must be marked formal_task_record=false")

    existing_roles = role_entries(manifest)
    candidate_role = role_record(
        role=role,
        kind=normalize_role_kind(kind),
        provider=provider,
        model=model,
        family=family,
        temperature=temperature,
    )
    role_id = str(candidate_role.get("role_id") or "")
    existing_role_ids = {normalize_token(item.get("role_id") or item.get("role") or item.get("name")) for item in existing_roles}
    if role_id in existing_role_ids:
        issues.append(f"role_id already exists in model role manifest: {role_id}")
    candidate_manifest = {
        **base_model_role_manifest(),
        **{key: value for key, value in manifest.items() if key not in {"template_only", "template_warning"}},
        "roles": [*existing_roles, candidate_role],
    }
    _, normalization_issues = normalize_roles({"roles": [candidate_role]})
    issues.extend(normalization_issues)

    post_audit_summary: dict[str, Any] | None = None
    if not issues:
        ensure_dir(manifest_path.parent)
        write_json(manifest_path, candidate_manifest)
        if isolation_output_path is not None:
            ensure_dir(isolation_output_path.parent)
            post_audit = audit_model_isolation(manifest_path, output_path=isolation_output_path)
            post_audit_summary = {
                "path": str(isolation_output_path),
                "passed": post_audit.get("passed"),
                "publishable": post_audit.get("publishable"),
                "role_count": post_audit.get("role_count"),
                "issue_count": post_audit.get("issue_count"),
            }

    report = {
        "schema_version": "2026-06-19",
        "artifact_type": ROLE_APPEND_AUDIT_ARTIFACT_TYPE,
        "formal_task_record": False,
        "manifest_path": str(manifest_path),
        "role_id": role_id,
        "role": role,
        "kind": normalize_role_kind(kind),
        "provider": provider,
        "model": model,
        "family": infer_family(provider, model, family),
        "temperature": temperature,
        "passed": not issues,
        "issue_count": len(issues),
        "issues": issues,
        "existing_role_count": len(existing_roles),
        "would_write_role_count": len(existing_roles) + (0 if issues else 1),
        "post_append_model_isolation": post_audit_summary,
        "gate": "Append exactly one real model role; full model isolation may remain blocked until all roles and judge audits pass.",
    }
    if append_audit_output_path is not None:
        ensure_dir(append_audit_output_path.parent)
        write_json(append_audit_output_path, report)
    return report


def add_template_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "eval_protocol" / "model_roles.template.json")


def run_template_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.output, purpose="model role manifest template")
    ensure_dir(args.output.parent)
    write_json(args.output, model_role_manifest_template())
    print(f"wrote non-formal model role manifest template to {args.output}; template is not publishable")


def add_role_queue_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_ROLE_QUEUE)
    parser.add_argument("--judge-prediction-audit", type=Path, default=DEFAULT_JUDGE_PREDICTION_AUDIT)
    parser.add_argument("--judge-perturbation-audit", type=Path, default=DEFAULT_JUDGE_PERTURBATION_AUDIT)
    parser.add_argument("--include-user-role", action="store_true")


def run_role_queue_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.output, purpose="model role queue")
    ensure_dir(args.output.parent)
    report = build_model_role_queue(
        manifest_path=args.manifest,
        output_path=args.output,
        judge_prediction_audit_path=args.judge_prediction_audit,
        judge_perturbation_audit_path=args.judge_perturbation_audit,
        include_user_role=args.include_user_role,
    )
    print(
        "model role queue: "
        f"passed={report['passed']} existing_roles={report['existing_role_count']} "
        f"valid_roles={report['valid_role_count']} missing={report['missing_role_count']} "
        f"queue={report['queue_item_count']} issues={report['issue_count']} output={args.output}"
    )
    if not report["passed"]:
        raise SystemExit(1)


def add_append_role_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--append-audit-output", type=Path, default=None)
    parser.add_argument("--isolation-output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--role", required=True)
    parser.add_argument("--kind", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--family", default=None)
    parser.add_argument("--temperature", type=float, default=None)


def run_append_role_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.manifest, purpose="model role manifest")
    assert_nonformal_output(args.append_audit_output, purpose="model role append audit")
    assert_nonformal_output(args.isolation_output, purpose="model isolation audit")
    report = append_model_role(
        args.manifest,
        role=args.role,
        kind=args.kind,
        provider=args.provider,
        model=args.model,
        family=args.family,
        temperature=args.temperature,
        append_audit_output_path=args.append_audit_output,
        isolation_output_path=args.isolation_output,
    )
    post = report.get("post_append_model_isolation") or {}
    print(
        "append model role: "
        f"passed={report['passed']} role={report['role_id']} kind={report['kind']} "
        f"roles={report['would_write_role_count']} issues={report['issue_count']} "
        f"isolation_passed={post.get('passed')}"
    )
    if not report["passed"]:
        raise SystemExit(1)


def add_build_manifest_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--audit-output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--spec-provider", required=True)
    parser.add_argument("--spec-model", required=True)
    parser.add_argument("--spec-family", default=None)
    parser.add_argument("--judge-provider", required=True)
    parser.add_argument("--judge-model", required=True)
    parser.add_argument("--judge-family", default=None)
    parser.add_argument("--judge-temperature", type=float, default=0.0)
    parser.add_argument("--actor-provider", required=True)
    parser.add_argument("--actor-model", required=True)
    parser.add_argument("--actor-family", default=None)
    parser.add_argument("--user-provider", default=None)
    parser.add_argument("--user-model", default=None)
    parser.add_argument("--user-family", default=None)


def run_build_manifest_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.output, purpose="model role manifest")
    assert_nonformal_output(args.audit_output, purpose="model isolation audit")
    ensure_dir(args.output.parent)
    manifest = build_model_role_manifest(
        spec_provider=args.spec_provider,
        spec_model=args.spec_model,
        spec_family=args.spec_family,
        judge_provider=args.judge_provider,
        judge_model=args.judge_model,
        judge_family=args.judge_family,
        judge_temperature=args.judge_temperature,
        actor_provider=args.actor_provider,
        actor_model=args.actor_model,
        actor_family=args.actor_family,
        user_provider=args.user_provider,
        user_model=args.user_model,
        user_family=args.user_family,
    )
    write_json(args.output, manifest)
    ensure_dir(args.audit_output.parent)
    report = audit_model_isolation(args.output, output_path=args.audit_output)
    print(
        f"built model role manifest: passed={report['passed']} "
        f"roles={report['role_count']} issues={report['issue_count']} output={args.output}"
    )
    if not report["passed"]:
        raise SystemExit(1)


def add_audit_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--no-require-mm-roles", action="store_true")
    parser.add_argument("--judge-prediction-audit", type=Path, default=DEFAULT_JUDGE_PREDICTION_AUDIT)
    parser.add_argument("--judge-perturbation-audit", type=Path, default=DEFAULT_JUDGE_PERTURBATION_AUDIT)
    parser.add_argument(
        "--no-require-judge-audit-binding",
        action="store_true",
        help="Recovery-only: do not require judge role metadata to match prediction/perturbation audits.",
    )


def run_audit_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.output, purpose="model isolation audit")
    ensure_dir(args.output.parent)
    report = audit_model_isolation(
        args.manifest,
        output_path=args.output,
        require_mm_roles=not args.no_require_mm_roles,
        judge_prediction_audit_path=args.judge_prediction_audit,
        judge_perturbation_audit_path=args.judge_perturbation_audit,
        require_judge_audit_binding=not args.no_require_judge_audit_binding,
    )
    print(
        f"model isolation audit: passed={report['passed']} "
        f"roles={report['role_count']} issues={report['issue_count']}"
    )
    if not report["passed"]:
        raise SystemExit(1)
