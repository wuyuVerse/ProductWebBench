from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from ...core.config import MANIFEST_DIR, TASK_DIR
from ...core.formal_data_guard import assert_not_under_formal_task_root
from ...core.io_utils import ensure_dir, read_jsonl, write_json, write_jsonl
from ...core.task_files import cross_field_path_duplicates, normalize_task_path
from .declarations import DECLARATION_NAME as NO_BULK_DECLARATION_FILE
from .declarations import audit_no_bulk_declaration_file


REQUIRED_TASK_FIELDS = {
    "task_id",
    "repo_id",
    "split",
    "intent",
    "scope",
    "difficulty",
    "required_content",
    "design_constraints",
    "state_constraints",
    "evaluation_rubric",
}


VALID_INTENTS = {"add", "modify", "repair", "restyle", "extend_interaction"}
# Build tasks are authored against their own vocabulary: one intent, page scope,
# and a tier instead of an L1-L5 difficulty. They carry no state_constraints and
# no four-part rubric, because acceptance is the build_acceptance record.
BUILD_INTENT = "build"
BUILD_REQUIRED_FIELDS = {
    "task_id", "repo_id", "split", "intent", "scope", "difficulty",
    "required_content", "design_constraints",
    "target_route", "tier",
}
VALID_BUILD_TIERS = {"T1", "T2", "T3", "T4"}
VALID_SCOPES = {"section", "component", "page", "flow", "asset", "layout", "cross_page"}
VALID_DIFFICULTIES = {"L1", "L2", "L3", "L4", "L5"}
PATH_LIST_FIELDS = {"assets_to_consider", "suggested_files"}
DUPLICATE_LIST_FIELDS = ("assets_to_consider", "suggested_files", "required_states", "hidden_states")
REQUIRED_REVIEW_ARTIFACTS = {
    "rationales": ("rationales.json",),
    "provenance": ("provenance.json",),
    "design_anchors": ("design_anchors.json",),
    "verifier_specs": ("verifier_specs.json",),
    "submission_specs": ("submission_specs.json",),
    "source_audit": ("source_audit.json",),
    "quality_audit": ("quality_audit.json",),
    "consistency_audit": ("consistency_audit.json",),
    "package_validation": ("package_validation.json",),
}
FREEZE_AUDIT_FILE = "single_task_freeze_audit.json"
FREEZE_AUDIT_ARTIFACT_TYPE = "single_task_freeze_audit"
DUPLICATE_REPO_REPAIR_PLAN_ARTIFACT_TYPE = "duplicate_repo_repair_plan"
STRICT_AUTHORING_REPAIR_PLAN_ARTIFACT_TYPE = "strict_authoring_repair_plan"
STRICT_FREEZE_WORKLIST_ARTIFACT_TYPE = "strict_freeze_worklist"
NON_FORMAL_ARTIFACT_TYPES = {
    "authoring_template_only",
    "candidate_review_card",
    "change_ledger_alignment_audit",
    "change_regime_authoring_audit",
    "change_regime_package_export",
    "change_regime_reference_verification",
    "change_regime_split_pipeline",
    "change_regime_submission_verification",
    "change_regime_task_validation",
    "construction_actor_loop_report",
    "construction_evidence_scaffold",
    "construction_milestone_run_report",
    "construction_precheck_report",
    "construction_repair_plan",
    "construction_trajectory_run_report",
    "de_ai_contrast_anchor",
    "de_ai_contrast_anchor_append_audit",
    "de_ai_contrast_audit",
    "denominator_consistency_audit",
    DUPLICATE_REPO_REPAIR_PLAN_ARTIFACT_TYPE,
    "eval_protocol_audit",
    "model_isolation_audit",
    "model_role_append_audit",
    "model_role_manifest",
    "mm_annotation_item",
    "mm_annotation_pack",
    "mm_annotation_pack_audit",
    "mm_calibration_gate",
    "mm_human_annotation_append_audit",
    "mm_human_annotation_template",
    "mm_human_label_freeze_audit",
    "mm_human_label_item",
    "mm_judge_perturbation_audit",
    "mm_judge_perturbation_group_append_audit",
    "mm_judge_perturbation_group_template",
    "mm_judge_perturbation_item",
    "mm_judge_prediction_audit",
    "mm_judge_prediction_append_audit",
    "mm_judge_prediction_item",
    "mm_judge_spec_audit",
    "mm_protocol_repair_plan",
    "regime_authoring_plan",
    "regime_package_metadata",
    "regime_score",
    "regime_verifier_plan",
    "signal_layer_audit",
    STRICT_AUTHORING_REPAIR_PLAN_ARTIFACT_TYPE,
    FREEZE_AUDIT_ARTIFACT_TYPE,
}
AUTHORING_ONLY_FIELDS = {
    "auto_generated",
    "batch_generated",
    "candidate_repos",
    "formal_design_brief",
    "generated_formal_task",
    "generated_from_template",
    "generator",
    "source_template",
    "template_warning",
}
FORBIDDEN_FORMAL_ROOT_FILENAMES = {
    "asset_policy.template.json",
    "construction_asset_policy.template.json",
    "construction_repair_plan.json",
    "deai_contrast_anchor.template.json",
    "deai_contrast_audit.json",
    "deai_contrast_set.jsonl",
    "generated_ledger.jsonl",
    "generated_queue.json",
    "generated_queue.md",
    "mm_calibration_gate.json",
    "mm_human_annotation.template.json",
    "mm_judge_perturbation_group.template.json",
    "mm_judge_perturbation_audit.json",
    "mm_judge_prediction_audit.json",
    "mm_protocol_repair_plan.json",
    "model_isolation_audit.json",
    "model_roles.json",
    "model_roles.template.json",
    "reference_actor_trace.template.json",
    "task_template.json",
}
FORBIDDEN_FORMAL_ROOT_ARTIFACT_TYPES = {
    "authoring_queue",
    "authoring_template_only",
    "candidate_review_card",
    "construction_repair_plan",
    "de_ai_contrast_anchor",
    "de_ai_contrast_anchor_append_audit",
    "de_ai_contrast_audit",
    "model_isolation_audit",
    "model_role_append_audit",
    "model_role_manifest",
    "mm_annotation_item",
    "mm_annotation_pack",
    "mm_annotation_pack_audit",
    "mm_calibration_gate",
    "mm_human_annotation_append_audit",
    "mm_human_annotation_template",
    "mm_human_label_freeze_audit",
    "mm_human_label_item",
    "mm_judge_perturbation_audit",
    "mm_judge_perturbation_group_append_audit",
    "mm_judge_perturbation_group_template",
    "mm_judge_perturbation_item",
    "mm_judge_prediction_audit",
    "mm_judge_prediction_append_audit",
    "mm_judge_prediction_item",
    "mm_judge_spec_audit",
    "mm_protocol_repair_plan",
    "reference_actor_trace_template_only",
    "regime_authoring_plan",
    "regime_package_metadata",
    "regime_score",
    "regime_verifier_plan",
}


def file_sha256(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def input_file_record(path: Path) -> dict[str, str | None]:
    return {"path": str(path), "sha256": file_sha256(path)}


def problem_statement(task: dict) -> str:
    return task.get("problem_statement", "")


def media_total(record: dict) -> int:
    return sum(int(v) for v in record.get("media_count", {}).values())


def normalize_pathish(value: object) -> str:
    return normalize_task_path(value)


def duplicate_entries(values: list, *, pathish: bool = False) -> list[str]:
    keys: dict[str, str] = {}
    counts: Counter[str] = Counter()
    for value in values:
        key = normalize_pathish(value) if pathish else str(value).strip()
        keys.setdefault(key, str(value))
        counts[key] += 1
    return [keys[key] for key, count in sorted(counts.items()) if count > 1]


def text_has_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def semantic_candidate_bonus(record: dict) -> int:
    text = " ".join(
        str(record.get(key, ""))
        for key in ("repo_id", "owner", "name", "framework", "package_name")
    ).lower()
    bonus = 0
    if text_has_any(text, ("chat", "chatbot", "chatgpt", "gemini", "openai", "assistant", "llm", "prompt")):
        bonus += 30
    if text_has_any(text, ("dashboard", "admin", "analytics", "invoice", "crm", "kpi")):
        bonus += 12
    if text_has_any(text, ("shop", "store", "commerce", "cart", "checkout", "product")):
        bonus += 12
    if text_has_any(text, ("docs", "documentation", "guide", "reference", "storybook", "component")):
        bonus += 10
    if text_has_any(text, ("portfolio", "resume", "cv", "case-study", "case_study")):
        bonus += 8
    if text_has_any(text, ("gallery", "showcase", "photo", "album", "lightbox")):
        bonus += 8
    if text_has_any(text, ("canvas", "webgl", "three", "shader", "map", "terrain", "3d")):
        bonus += 10
    return min(bonus, 40)


def score_candidate(record: dict) -> tuple[int, int, int]:
    framework_bonus = {
        "next": 8,
        "vite": 7,
        "astro": 7,
        "gatsby": 6,
        "gridsome": 6,
        "react": 6,
        "three": 8,
        "static": 4,
    }.get(record.get("framework"), 0)
    route_bonus = 5 if record.get("has_routes") else 0
    package_bonus = 4 if record.get("has_package_json") else 0
    media_bonus = min(media_total(record), 20)
    size_penalty = 0
    if record.get("file_count", 0) > 5000:
        size_penalty = 6
    elif record.get("file_count", 0) > 1500:
        size_penalty = 3
    score = framework_bonus + route_bonus + package_bonus + media_bonus + semantic_candidate_bonus(record) - size_penalty
    return score, media_total(record), -record.get("file_count", 0)


def task_repo_ids(task_root: Path, pattern: str) -> set[str]:
    repo_ids: set[str] = set()
    for path in task_files_under(task_root, pattern):
        for task in read_jsonl(path):
            repo_id = task.get("repo_id")
            if repo_id:
                repo_ids.add(str(repo_id))
    return repo_ids


def select_candidates(
    manifest_path: Path,
    output_path: Path,
    per_framework: int,
    total_limit: int | None,
    *,
    exclude_task_root: Path | None = TASK_DIR,
    exclude_task_pattern: str = "slot_*/task.jsonl",
    allow_used_task_repos: bool = False,
) -> list[dict]:
    raw_records = [item for item in read_jsonl(manifest_path) if item.get("likely_ui_repo")]
    raw_repo_ids = [str(item.get("repo_id")) for item in raw_records if item.get("repo_id")]
    used_repo_ids = set() if allow_used_task_repos or exclude_task_root is None else task_repo_ids(exclude_task_root, exclude_task_pattern)
    by_repo_id: dict[str, dict] = {}
    missing_repo_id: list[dict] = []
    excluded_used_repo_ids: set[str] = set()
    for record in raw_records:
        repo_id = record.get("repo_id")
        if not repo_id:
            missing_repo_id.append(record)
            continue
        if repo_id in used_repo_ids:
            excluded_used_repo_ids.add(str(repo_id))
            continue
        existing = by_repo_id.get(repo_id)
        if existing is None or score_candidate(record) > score_candidate(existing):
            by_repo_id[repo_id] = record
    records = list(by_repo_id.values())
    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        grouped[record.get("framework", "other")].append(record)

    selected: list[dict] = []
    for framework, items in sorted(grouped.items()):
        ranked = sorted(items, key=score_candidate, reverse=True)
        selected.extend(ranked[:per_framework])

    selected = sorted(selected, key=score_candidate, reverse=True)
    if total_limit is not None:
        selected = selected[:total_limit]

    write_jsonl(output_path, selected)
    summary = {
        "manifest": str(manifest_path),
        "output": str(output_path),
        "source_total": len(raw_records),
        "source_unique_repo_ids": len(set(raw_repo_ids)),
        "source_duplicate_repo_ids": len(raw_repo_ids) - len(set(raw_repo_ids)),
        "source_missing_repo_ids": len(missing_repo_id),
        "selection_source_unique_repo_ids_after_exclusion": len(by_repo_id),
        "excluded_used_task_repo_ids": len(excluded_used_repo_ids),
        "excluded_used_task_repo_id_examples": sorted(excluded_used_repo_ids)[:50],
        "used_task_repo_exclusion_enabled": not allow_used_task_repos and exclude_task_root is not None,
        "exclude_task_root": str(exclude_task_root) if exclude_task_root is not None else None,
        "exclude_task_pattern": exclude_task_pattern,
        "total": len(selected),
        "unique_repo_ids": len({item.get("repo_id") for item in selected}),
        "duplicate_repo_ids": len(selected) - len({item.get("repo_id") for item in selected}),
        "per_framework": per_framework,
        "total_limit": total_limit,
        "frameworks": Counter(item.get("framework", "other") for item in selected),
        "intended_use": "candidate pool for SiteContinuum task authoring",
    }
    write_json(output_path.with_suffix(".summary.json"), summary)
    return selected


def validate_build_task(task: dict) -> list[str]:
    """Schema check for a long-horizon Build task (slots 506-585)."""
    errors: list[str] = []
    missing = sorted(BUILD_REQUIRED_FIELDS - set(task))
    if missing:
        errors.append(f"missing fields: {', '.join(missing)}")
    # A Build task must name at least one state to capture. One task states it
    # entirely through hidden_states, so accept either key.
    if not (task.get("required_states") or task.get("hidden_states")):
        errors.append("neither required_states nor hidden_states names a state to capture")
    if task.get("scope") not in VALID_SCOPES:
        errors.append(f"invalid scope: {task.get('scope')}")
    if task.get("tier") not in VALID_BUILD_TIERS:
        errors.append(f"invalid tier: {task.get('tier')}")
    if task.get("difficulty") != task.get("tier"):
        errors.append(f"difficulty {task.get('difficulty')} does not match tier {task.get('tier')}")
    route = task.get("target_route")
    # Hash routing is a legitimate shape here: several SPA targets are "#/name".
    if not isinstance(route, str) or not (route.startswith("/") or route.startswith("#/")):
        errors.append(f"target_route must be a rooted or hash route: {route!r}")
    if not problem_statement(task):
        errors.append("missing problem_statement")
    for field in ("required_content", "design_constraints"):
        value = task.get(field)
        if not isinstance(value, list) or len(value) < 2:
            errors.append(f"{field} must contain at least two items")
    for field in DUPLICATE_LIST_FIELDS:
        value = task.get(field, [])
        if isinstance(value, list):
            duplicates = duplicate_entries(value, pathish=field in PATH_LIST_FIELDS)
            if duplicates:
                errors.append(f"{field} contains duplicate entries: {', '.join(map(str, duplicates))}")
    return errors


def validate_any_task(task: dict) -> list[str]:
    """Dispatch to the Build or the Change schema by the task's own intent."""
    if task.get("intent") == BUILD_INTENT:
        return validate_build_task(task)
    return validate_task(task)


def validate_task(task: dict) -> list[str]:
    errors: list[str] = []
    if task.get("formal_task_record") is False:
        errors.append("formal task files must not contain formal_task_record=false scaffolds or generated review artifacts")
    artifact_type = task.get("artifact_type")
    if artifact_type in NON_FORMAL_ARTIFACT_TYPES:
        errors.append(f"formal task files must not contain non-formal artifact_type: {artifact_type}")
    authoring_only = sorted(field for field in AUTHORING_ONLY_FIELDS if field in task)
    if authoring_only:
        errors.append(f"formal task files contain authoring-only fields: {', '.join(authoring_only)}")
    missing = sorted(REQUIRED_TASK_FIELDS - set(task))
    if missing:
        errors.append(f"missing fields: {', '.join(missing)}")
    if task.get("intent") not in VALID_INTENTS:
        errors.append(f"invalid intent: {task.get('intent')}")
    if task.get("scope") not in VALID_SCOPES:
        errors.append(f"invalid scope: {task.get('scope')}")
    if task.get("difficulty") not in VALID_DIFFICULTIES:
        errors.append(f"invalid difficulty: {task.get('difficulty')}")
    if not problem_statement(task):
        errors.append("missing problem_statement")
    elif len(problem_statement(task)) < 80:
        errors.append("problem_statement is too short for a solid website-change problem")

    for field in ("required_content", "design_constraints", "state_constraints"):
        value = task.get(field)
        if not isinstance(value, list) or len(value) < 2:
            errors.append(f"{field} must contain at least two items")

    for field in DUPLICATE_LIST_FIELDS:
        value = task.get(field, [])
        if isinstance(value, list):
            duplicates = duplicate_entries(value, pathish=field in PATH_LIST_FIELDS)
            if duplicates:
                errors.append(f"{field} contains duplicate entries: {', '.join(map(str, duplicates))}")
    file_overlaps = cross_field_path_duplicates(task)
    if file_overlaps:
        overlap_paths = ", ".join(list(file_overlaps)[:20])
        suffix = "" if len(file_overlaps) <= 20 else f", ... +{len(file_overlaps) - 20} more"
        errors.append(f"assets_to_consider and suggested_files contain duplicate paths: {overlap_paths}{suffix}")

    rubric = task.get("evaluation_rubric", {})
    if not isinstance(rubric, dict):
        errors.append("evaluation_rubric must be an object")
    else:
        for key in ("change_completion", "design_consistency", "responsive", "regression"):
            if key not in rubric:
                errors.append(f"evaluation_rubric missing {key}")
    return errors


def iter_task_records(path: Path):
    """Yield task records from a tasks JSONL, or from a per-slot task tree."""
    path = Path(path)
    if path.is_dir():
        for slot_dir in sorted(path.glob("slot_*")):
            task_file = slot_dir / "task.jsonl"
            if task_file.is_file():
                yield from read_jsonl(task_file)
        return
    yield from read_jsonl(path)


def validate_tasks(path: Path, report_path: Path) -> dict:
    results = []
    ok = 0
    for idx, task in enumerate(iter_task_records(path), start=1):
        errors = validate_any_task(task)
        if not errors:
            ok += 1
        results.append({"line": idx, "task_id": task.get("task_id"), "errors": errors})
    report = {
        "path": str(path),
        "total": len(results),
        "valid": ok,
        "invalid": len(results) - ok,
        "results": results,
    }
    write_json(report_path, report)
    return report


def add_select_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest", type=Path, default=MANIFEST_DIR / "repos.jsonl")
    parser.add_argument("--output", type=Path, default=MANIFEST_DIR / "candidates.jsonl")
    parser.add_argument("--per-framework", type=int, default=300)
    parser.add_argument("--total-limit", type=int, default=1200)
    parser.add_argument("--exclude-task-root", type=Path, default=TASK_DIR)
    parser.add_argument("--exclude-task-pattern", default="slot_*/task.jsonl")
    parser.add_argument(
        "--allow-used-task-repos",
        action="store_true",
        help="Recovery/diagnostic only: do not exclude repos already present in formal task files.",
    )


def run_select_from_args(args: argparse.Namespace) -> None:
    selected = select_candidates(
        args.manifest,
        args.output,
        per_framework=args.per_framework,
        total_limit=args.total_limit,
        exclude_task_root=args.exclude_task_root,
        exclude_task_pattern=args.exclude_task_pattern,
        allow_used_task_repos=args.allow_used_task_repos,
    )
    print(f"wrote {len(selected)} candidates to {args.output}")


def add_validate_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", type=Path)
    parser.add_argument("--report", type=Path, default=TASK_DIR / "validation_report.json")


def run_validate_from_args(args: argparse.Namespace) -> None:
    report = validate_tasks(args.path, args.report)
    print(f"validated {report['total']} tasks: {report['valid']} valid, {report['invalid']} invalid")
    if report["invalid"]:
        raise SystemExit(1)


def load_jsonl_file(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return list(read_jsonl(path))


def direct_formal_root_files(task_root: Path) -> list[Path]:
    if not task_root.exists():
        return []
    files: list[Path] = []
    for child in task_root.iterdir():
        if child.is_file():
            files.append(child)
        elif child.is_dir():
            if child.name.startswith("slot_"):
                files.extend(path for path in child.iterdir() if path.is_file())
            else:
                files.extend(path for path in child.rglob("*") if path.is_file())
    return sorted(files)


def artifact_type_from_file(path: Path) -> str | None:
    try:
        if path.suffix == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
        elif path.suffix == ".jsonl":
            payload = None
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        payload = json.loads(line)
                        break
        else:
            return None
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(payload, dict):
        artifact_type = payload.get("artifact_type")
        return str(artifact_type) if artifact_type else None
    return None


def audit_forbidden_formal_root_scaffolds(task_root: Path) -> dict:
    scanned_files = direct_formal_root_files(task_root)
    forbidden_items: list[dict] = []
    for path in scanned_files:
        reasons: list[str] = []
        artifact_type = artifact_type_from_file(path)
        if path.name in FORBIDDEN_FORMAL_ROOT_FILENAMES:
            reasons.append(f"forbidden scaffold/protocol filename: {path.name}")
        if artifact_type in FORBIDDEN_FORMAL_ROOT_ARTIFACT_TYPES:
            reasons.append(f"forbidden scaffold/protocol artifact_type: {artifact_type}")
        if reasons:
            forbidden_items.append(
                {
                    "path": str(path),
                    "artifact_type": artifact_type,
                    "reasons": reasons,
                }
            )
    issues = []
    if forbidden_items:
        issues.append(f"formal task root contains {len(forbidden_items)} forbidden scaffold/protocol artifacts")
    return {
        "task_root": str(task_root),
        "scanned_file_count": len(scanned_files),
        "forbidden_scaffold_count": len(forbidden_items),
        "forbidden_scaffolds": forbidden_items[:100],
        "issues": issues,
        "passed": not issues,
    }


def task_files_under(task_root: Path, pattern: str) -> list[Path]:
    return sorted(path for path in task_root.glob(pattern) if path.is_file())


def audit_repo_manifest(manifest_path: Path) -> dict:
    records = load_jsonl_file(manifest_path)
    repo_ids = [record.get("repo_id") for record in records if record.get("repo_id")]
    repo_counts = Counter(repo_ids)
    duplicate_repo_ids = sorted(repo_id for repo_id, count in repo_counts.items() if count > 1)
    issues: list[str] = []
    if not manifest_path.exists():
        issues.append(f"repo manifest does not exist: {manifest_path}")
    missing_repo_ids = len(records) - len(repo_ids)
    if missing_repo_ids:
        issues.append(f"repo manifest has {missing_repo_ids} rows without repo_id")
    if duplicate_repo_ids:
        issues.append(f"repo manifest has {len(duplicate_repo_ids)} duplicate repo_id values")
    return {
        "path": str(manifest_path),
        "rows": len(records),
        "unique_repo_ids": len(repo_counts),
        "missing_repo_ids": missing_repo_ids,
        "duplicate_repo_ids": duplicate_repo_ids,
        "duplicate_repo_id_count": len(duplicate_repo_ids),
        "issues": issues,
        "passed": not issues,
    }


def audit_candidate_pool(
    candidate_path: Path,
    expected_candidates: int | None,
    *,
    task_root: Path | None = None,
    task_pattern: str = "slot_*/task.jsonl",
) -> dict:
    records = load_jsonl_file(candidate_path)
    repo_ids = [record.get("repo_id") for record in records if record.get("repo_id")]
    repo_counts = Counter(repo_ids)
    duplicate_repo_ids = sorted(repo_id for repo_id, count in repo_counts.items() if count > 1)
    zip_paths = [normalize_task_path(record.get("zip_path")) for record in records if record.get("zip_path")]
    zip_path_counts = Counter(zip_paths)
    duplicate_zip_paths = sorted(path for path, count in zip_path_counts.items() if count > 1)
    issues: list[str] = []
    if not candidate_path.exists():
        issues.append(f"candidate manifest does not exist: {candidate_path}")
    if expected_candidates is not None and len(records) != expected_candidates:
        issues.append(f"candidate manifest has {len(records)} rows; expected {expected_candidates}")
    missing_repo_ids = len(records) - len(repo_ids)
    if missing_repo_ids:
        issues.append(f"candidate manifest has {missing_repo_ids} rows without repo_id")
    if duplicate_repo_ids:
        issues.append(f"candidate manifest has {len(duplicate_repo_ids)} duplicate repo_id values")
    missing_zip_paths = len(records) - len(zip_paths)
    if missing_zip_paths:
        issues.append(f"candidate manifest has {missing_zip_paths} rows without zip_path")
    if duplicate_zip_paths:
        issues.append(f"candidate manifest has {len(duplicate_zip_paths)} duplicate zip_path values")
    used_repo_ids = task_repo_ids(task_root, task_pattern) if task_root is not None else set()
    used_repo_overlaps = sorted(str(repo_id) for repo_id in repo_counts if repo_id in used_repo_ids)
    if used_repo_overlaps:
        issues.append(f"candidate manifest contains {len(used_repo_overlaps)} repo_id values already used by formal tasks")
    return {
        "path": str(candidate_path),
        "expected_candidates": expected_candidates,
        "rows": len(records),
        "unique_repo_ids": len(repo_counts),
        "missing_repo_ids": missing_repo_ids,
        "duplicate_repo_ids": duplicate_repo_ids,
        "duplicate_repo_id_count": len(duplicate_repo_ids),
        "unique_zip_paths": len(zip_path_counts),
        "missing_zip_paths": missing_zip_paths,
        "duplicate_zip_paths": duplicate_zip_paths,
        "duplicate_zip_path_count": len(duplicate_zip_paths),
        "used_task_repo_overlap_count": len(used_repo_overlaps),
        "used_task_repo_overlap_examples": used_repo_overlaps[:50],
        "issues": issues,
        "passed": not issues,
    }


def audit_task_file(path: Path) -> list[dict]:
    results: list[dict] = []
    for line, task in enumerate(read_jsonl(path), start=1):
        duplicate_field_issues = []
        for field in DUPLICATE_LIST_FIELDS:
            value = task.get(field, [])
            if isinstance(value, list):
                duplicates = duplicate_entries(value, pathish=field in PATH_LIST_FIELDS)
                if duplicates:
                    duplicate_field_issues.append(
                        {
                            "field": field,
                            "duplicates": duplicates,
                        }
                    )
        file_overlaps = cross_field_path_duplicates(task)
        if file_overlaps:
            duplicate_field_issues.append(
                {
                    "field": "assets_to_consider+suggested_files",
                    "duplicates": list(file_overlaps),
                }
            )
        results.append(
            {
                "path": str(path),
                "line": line,
                "task_id": task.get("task_id"),
                "repo_id": task.get("repo_id"),
                "validation_errors": validate_task(task),
                "duplicate_field_issues": duplicate_field_issues,
            }
        )
    return results


def automated_authoring_marker_issues(task_results: list[dict]) -> list[dict]:
    markers = (
        "formal_task_record=false",
        "non-formal artifact_type",
        "authoring-only fields",
        "generated",
        "template",
        "scaffold",
    )
    issues: list[dict] = []
    for result in task_results:
        matched = [
            str(error)
            for error in result.get("validation_errors", [])
            if any(marker in str(error).lower() for marker in markers)
        ]
        if matched:
            issues.append(
                {
                    "path": result.get("path"),
                    "line": result.get("line"),
                    "task_id": result.get("task_id"),
                    "repo_id": result.get("repo_id"),
                    "errors": matched,
                }
            )
    return issues


def build_formal_authoring_policy_report(
    *,
    task_count: int,
    task_file_row_count_issues: list[dict],
    automated_marker_issues: list[dict],
    require_freeze_audits: bool,
    freeze_audit_issues: list[dict],
    require_no_bulk_declarations: bool,
    no_bulk_declaration_issues: list[dict],
) -> dict:
    evidence_requirements_enabled = require_freeze_audits and require_no_bulk_declarations
    issues: list[str] = []
    if task_file_row_count_issues:
        issues.append("formal slot files must contain exactly one task row")
    if automated_marker_issues:
        issues.append("formal task rows contain generated/template/scaffold markers")
    if not evidence_requirements_enabled:
        issues.append("strict no-bulk and single-task freeze evidence gates are not both enabled")
    if require_freeze_audits and freeze_audit_issues:
        issues.append("some formal slots lack a passing single-task freeze audit")
    if require_no_bulk_declarations and no_bulk_declaration_issues:
        issues.append("some formal slots lack a passing no-bulk generation declaration")
    return {
        "policy": "one_task_at_a_time_manual_formal_authoring",
        "formal_task_record_generation_allowed": False,
        "batch_formal_task_generation_allowed": False,
        "candidate_queue_is_formal_data": False,
        "candidate_review_cards_are_formal_data": False,
        "formal_task_count": task_count,
        "single_row_slot_contract_passed": not task_file_row_count_issues,
        "automated_marker_issue_count": len(automated_marker_issues),
        "automated_marker_issues": automated_marker_issues,
        "strict_evidence_requirements_enabled": evidence_requirements_enabled,
        "freeze_audits_required": require_freeze_audits,
        "freeze_audit_issue_count": len(freeze_audit_issues),
        "no_bulk_declarations_required": require_no_bulk_declarations,
        "no_bulk_declaration_issue_count": len(no_bulk_declaration_issues),
        "passed": not issues,
        "issues": issues,
        "gate": (
            "Formal task rows are publishable only when each slot is authored manually, has exactly one task row, "
            "contains no generated/template markers, and has its own no-bulk declaration plus single-task freeze audit."
        ),
    }


def missing_review_artifacts(path: Path) -> list[str]:
    slot_dir = path.parent
    missing = []
    for artifact, names in REQUIRED_REVIEW_ARTIFACTS.items():
        if not any((slot_dir / name).exists() for name in names):
            missing.append(artifact)
    return missing


def audit_freeze_artifact(path: Path, task_id: str | None, repo_id: str | None) -> list[str]:
    errors: list[str] = []
    freeze_path = path.parent / FREEZE_AUDIT_FILE
    if not freeze_path.exists():
        return [f"missing {FREEZE_AUDIT_FILE}"]
    try:
        report = json.loads(freeze_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{FREEZE_AUDIT_FILE} is not valid JSON: {exc}"]
    if report.get("artifact_type") != FREEZE_AUDIT_ARTIFACT_TYPE:
        errors.append(f"{FREEZE_AUDIT_FILE} has wrong artifact_type: {report.get('artifact_type')}")
    if report.get("formal_task_record") is not False:
        errors.append(f"{FREEZE_AUDIT_FILE} must have formal_task_record=false")
    if report.get("passed") is not True:
        errors.append(f"{FREEZE_AUDIT_FILE} did not pass")
    if report.get("issue_count") not in (0, None):
        errors.append(f"{FREEZE_AUDIT_FILE} issue_count is nonzero: {report.get('issue_count')}")
    if task_id and report.get("task_id") != task_id:
        errors.append(f"{FREEZE_AUDIT_FILE} task_id mismatch: {report.get('task_id')} != {task_id}")
    if repo_id and report.get("repo_id") != repo_id:
        errors.append(f"{FREEZE_AUDIT_FILE} repo_id mismatch: {report.get('repo_id')} != {repo_id}")
    if report.get("allow_missing_progress") is not False:
        errors.append(f"{FREEZE_AUDIT_FILE} must be produced in strict mode with allow_missing_progress=false")
    if report.get("allow_missing_declaration") is not False:
        errors.append(f"{FREEZE_AUDIT_FILE} must be produced in strict mode with allow_missing_declaration=false")
    return errors


def audit_no_bulk_declaration(path: Path, task_id: str | None, repo_id: str | None) -> list[str]:
    declaration_path = path.parent / NO_BULK_DECLARATION_FILE
    report = audit_no_bulk_declaration_file(
        declaration_path,
        task_id=task_id,
        repo_id=repo_id,
        slot_id=path.parent.name,
    )
    return [str(issue) for issue in report.get("issues", [])]


def audit_authored_tasks(
    task_root: Path,
    pattern: str,
    require_unique_repos: bool,
    require_review_artifacts: bool,
    require_freeze_audits: bool = False,
    require_no_bulk_declarations: bool = False,
) -> dict:
    files = task_files_under(task_root, pattern)
    task_results_by_path = {str(path): audit_task_file(path) for path in files}
    task_results = [result for path_results in task_results_by_path.values() for result in path_results]
    task_file_row_count_issues = [
        {
            "path": path,
            "row_count": len(path_results),
            "errors": [f"formal task slot files must contain exactly one task row, observed {len(path_results)}"],
        }
        for path, path_results in task_results_by_path.items()
        if len(path_results) != 1
    ]
    task_ids = [result.get("task_id") for result in task_results if result.get("task_id")]
    repo_ids = [result.get("repo_id") for result in task_results if result.get("repo_id")]
    task_id_counts = Counter(task_ids)
    repo_id_counts = Counter(repo_ids)
    duplicate_task_ids = sorted(task_id for task_id, count in task_id_counts.items() if count > 1)
    duplicate_repo_ids = sorted(repo_id for repo_id, count in repo_id_counts.items() if count > 1)
    duplicate_repo_refs = []
    for repo_id in duplicate_repo_ids:
        refs = [
            {
                "slot_id": Path(str(result.get("path", ""))).parent.name,
                "path": result.get("path"),
                "line": result.get("line"),
                "task_id": result.get("task_id"),
            }
            for result in task_results
            if result.get("repo_id") == repo_id
        ]
        duplicate_repo_refs.append({"repo_id": repo_id, "count": len(refs), "refs": refs})
    duplicate_field_issues = [
        {
            "path": result["path"],
            "line": result["line"],
            "task_id": result.get("task_id"),
            "repo_id": result.get("repo_id"),
            **issue,
        }
        for result in task_results
        for issue in result["duplicate_field_issues"]
    ]
    validation_issues = [
        {
            "path": result["path"],
            "line": result["line"],
            "task_id": result.get("task_id"),
            "repo_id": result.get("repo_id"),
            "errors": result["validation_errors"],
        }
        for result in task_results
        if result["validation_errors"]
    ]
    review_artifact_issues = []
    if require_review_artifacts:
        for path in files:
            missing = missing_review_artifacts(path)
            if missing:
                review_artifact_issues.append(
                    {
                        "path": str(path),
                        "task_id": next((item.get("task_id") for item in task_results if item["path"] == str(path)), None),
                        "missing": missing,
                    }
                )
    freeze_audit_issues = []
    if require_freeze_audits:
        for path in files:
            path_results = task_results_by_path.get(str(path), [])
            if len(path_results) != 1:
                freeze_audit_issues.append(
                    {
                        "path": str(path),
                        "task_id": None,
                        "repo_id": None,
                        "errors": [f"freeze audit requires exactly one task row per slot, observed {len(path_results)}"],
                    }
                )
                continue
            result = path_results[0]
            errors = audit_freeze_artifact(path, result.get("task_id"), result.get("repo_id"))
            if errors:
                freeze_audit_issues.append(
                    {
                        "path": str(path),
                        "task_id": result.get("task_id"),
                        "repo_id": result.get("repo_id"),
                        "errors": errors,
                    }
                )
    no_bulk_declaration_issues = []
    if require_no_bulk_declarations:
        for path in files:
            path_results = task_results_by_path.get(str(path), [])
            if len(path_results) != 1:
                no_bulk_declaration_issues.append(
                    {
                        "path": str(path),
                        "task_id": None,
                        "repo_id": None,
                        "errors": [f"no-bulk declaration requires exactly one task row per slot, observed {len(path_results)}"],
                    }
                )
                continue
            result = path_results[0]
            errors = audit_no_bulk_declaration(path, result.get("task_id"), result.get("repo_id"))
            if errors:
                no_bulk_declaration_issues.append(
                    {
                        "path": str(path),
                        "task_id": result.get("task_id"),
                        "repo_id": result.get("repo_id"),
                        "errors": errors,
                    }
                )
    issues: list[str] = []
    if task_file_row_count_issues:
        issues.append(f"authored task slot files have {len(task_file_row_count_issues)} one-row contract violations")
    if duplicate_task_ids:
        issues.append(f"authored tasks have {len(duplicate_task_ids)} duplicate task_id values")
    if require_unique_repos and duplicate_repo_ids:
        issues.append(f"authored tasks reuse {len(duplicate_repo_ids)} repo_id values")
    if duplicate_field_issues:
        issues.append(f"authored tasks have {len(duplicate_field_issues)} duplicate per-task list fields")
    if validation_issues:
        issues.append(f"authored tasks have {len(validation_issues)} schema/content validation failures")
    if review_artifact_issues:
        issues.append(f"authored tasks have {len(review_artifact_issues)} tasks missing required per-task review artifacts")
    if freeze_audit_issues:
        issues.append(f"authored tasks have {len(freeze_audit_issues)} missing or invalid single-task freeze audits")
    if no_bulk_declaration_issues:
        issues.append(
            f"authored tasks have {len(no_bulk_declaration_issues)} missing or invalid no-bulk generation declarations"
        )
    automated_marker_issues = automated_authoring_marker_issues(task_results)
    formal_authoring_policy = build_formal_authoring_policy_report(
        task_count=len(task_results),
        task_file_row_count_issues=task_file_row_count_issues,
        automated_marker_issues=automated_marker_issues,
        require_freeze_audits=require_freeze_audits,
        freeze_audit_issues=freeze_audit_issues,
        require_no_bulk_declarations=require_no_bulk_declarations,
        no_bulk_declaration_issues=no_bulk_declaration_issues,
    )
    if automated_marker_issues:
        issues.append(f"authored tasks have {len(automated_marker_issues)} generated/template/scaffold marker failures")
    return {
        "task_root": str(task_root),
        "pattern": pattern,
        "file_count": len(files),
        "task_count": len(task_results),
        "task_file_row_count_issues": task_file_row_count_issues,
        "task_file_row_count_issue_count": len(task_file_row_count_issues),
        "unique_task_ids": len(task_id_counts),
        "duplicate_task_ids": duplicate_task_ids,
        "duplicate_task_id_count": len(duplicate_task_ids),
        "unique_repo_ids": len(repo_id_counts),
        "duplicate_repo_ids": duplicate_repo_ids,
        "duplicate_repo_id_count": len(duplicate_repo_ids),
        "duplicate_repo_refs": duplicate_repo_refs,
        "unique_repo_ids_required": require_unique_repos,
        "duplicate_field_issues": duplicate_field_issues,
        "duplicate_field_issue_count": len(duplicate_field_issues),
        "validation_issues": validation_issues,
        "validation_issue_count": len(validation_issues),
        "required_review_artifacts": sorted(REQUIRED_REVIEW_ARTIFACTS),
        "review_artifacts_required": require_review_artifacts,
        "review_artifact_issues": review_artifact_issues,
        "review_artifact_issue_count": len(review_artifact_issues),
        "freeze_audits_required": require_freeze_audits,
        "freeze_audit_file": FREEZE_AUDIT_FILE,
        "freeze_audit_issues": freeze_audit_issues,
        "freeze_audit_issue_count": len(freeze_audit_issues),
        "no_bulk_declarations_required": require_no_bulk_declarations,
        "no_bulk_declaration_file": NO_BULK_DECLARATION_FILE,
        "no_bulk_declaration_issues": no_bulk_declaration_issues,
        "no_bulk_declaration_issue_count": len(no_bulk_declaration_issues),
        "formal_authoring_policy": formal_authoring_policy,
        "issues": issues,
        "passed": not issues,
    }


def audit_authoring_data(
    repo_manifest_path: Path,
    candidate_path: Path,
    task_root: Path,
    output_path: Path,
    expected_candidates: int | None,
    pattern: str,
    require_unique_task_repos: bool,
    require_review_artifacts: bool,
    require_freeze_audits: bool = False,
    require_no_bulk_declarations: bool = False,
) -> dict:
    repo_manifest_report = audit_repo_manifest(repo_manifest_path)
    candidate_report = audit_candidate_pool(
        candidate_path,
        expected_candidates,
        task_root=task_root,
        task_pattern=pattern,
    )
    task_report = audit_authored_tasks(
        task_root,
        pattern,
        require_unique_task_repos,
        require_review_artifacts,
        require_freeze_audits,
        require_no_bulk_declarations,
    )
    scaffold_report = audit_forbidden_formal_root_scaffolds(task_root)
    report = {
        "repo_manifest": repo_manifest_report,
        "candidate_pool": candidate_report,
        "authored_tasks": task_report,
        "formal_root_scaffolds": scaffold_report,
        "passed": repo_manifest_report["passed"] and candidate_report["passed"] and task_report["passed"] and scaffold_report["passed"],
        "issue_count": (
            len(repo_manifest_report["issues"])
            + len(candidate_report["issues"])
            + len(task_report["issues"])
            + len(scaffold_report["issues"])
        ),
        "issues": repo_manifest_report["issues"] + candidate_report["issues"] + task_report["issues"] + scaffold_report["issues"],
    }
    write_json(output_path, report)
    return report


def add_authoring_data_audit_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-manifest", type=Path, default=MANIFEST_DIR / "repos.jsonl")
    parser.add_argument("--candidates", type=Path, default=MANIFEST_DIR / "candidates.jsonl")
    parser.add_argument("--task-root", type=Path, default=TASK_DIR)
    parser.add_argument("--task-pattern", default="slot_*/task.jsonl")
    parser.add_argument(
        "--expected-candidates",
        type=int,
        default=None,
        help="Optional expected candidate-row count. Omit to allow either a staging shortlist or a larger screening pool.",
    )
    parser.add_argument("--allow-reused-task-repos", action="store_true")
    parser.add_argument(
        "--require-review-artifacts",
        action="store_true",
        help="Require every formal task slot to include per-task design/provenance/verifier/audit artifacts.",
    )
    parser.add_argument(
        "--require-freeze-audits",
        action="store_true",
        help="Require every formal task slot to include a strict single_task_freeze_audit.json P8 gate.",
    )
    parser.add_argument(
        "--require-no-bulk-declarations",
        action="store_true",
        help="Require every formal task slot to include a task-specific no_bulk_generation_declaration.md.",
    )
    parser.add_argument("--output", type=Path, default=TASK_DIR.parent / "authoring_ledger" / "authoring_data_audit.json")


def run_authoring_data_audit_from_args(args: argparse.Namespace) -> None:
    report = audit_authoring_data(
        repo_manifest_path=args.repo_manifest,
        candidate_path=args.candidates,
        task_root=args.task_root,
        output_path=args.output,
        expected_candidates=args.expected_candidates,
        pattern=args.task_pattern,
        require_unique_task_repos=not args.allow_reused_task_repos,
        require_review_artifacts=args.require_review_artifacts,
        require_freeze_audits=args.require_freeze_audits,
        require_no_bulk_declarations=args.require_no_bulk_declarations,
    )
    status = "passed" if report["passed"] else "failed"
    manifest = report["repo_manifest"]
    candidates = report["candidate_pool"]
    tasks_report = report["authored_tasks"]
    scaffolds = report["formal_root_scaffolds"]
    print(
        f"authoring data audit {status}: "
        f"manifest={manifest['rows']} unique_repos={manifest['unique_repo_ids']} "
        f"manifest_duplicates={manifest['duplicate_repo_id_count']}; "
        f"candidates={candidates['rows']} unique_repos={candidates['unique_repo_ids']} "
        f"candidate_duplicates={candidates['duplicate_repo_id_count']} "
        f"unique_zip_paths={candidates.get('unique_zip_paths', 0)} "
        f"candidate_zip_duplicates={candidates.get('duplicate_zip_path_count', 0)} "
        f"used_repo_overlaps={candidates.get('used_task_repo_overlap_count', 0)}; "
        f"tasks={tasks_report['task_count']} task_files={tasks_report['file_count']} "
        f"one_row_slot_violations={tasks_report['task_file_row_count_issue_count']} "
        f"task_repo_duplicates={tasks_report['duplicate_repo_id_count']} "
        f"per_task_duplicate_fields={tasks_report['duplicate_field_issue_count']} "
        f"validation_failures={tasks_report['validation_issue_count']} "
        f"review_artifact_failures={tasks_report['review_artifact_issue_count']} "
        f"freeze_audit_failures={tasks_report['freeze_audit_issue_count']} "
        f"no_bulk_declaration_failures={tasks_report['no_bulk_declaration_issue_count']} "
        f"forbidden_scaffolds={scaffolds['forbidden_scaffold_count']}"
    )
    if not report["passed"]:
        raise SystemExit(1)


def build_duplicate_repo_repair_plan(
    *,
    audit_path: Path,
    output_path: Path,
    max_groups: int | None = None,
) -> dict:
    if not audit_path.exists():
        raise FileNotFoundError(f"authoring audit missing: {audit_path}")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    authored_tasks = audit.get("authored_tasks", {})
    duplicate_refs = authored_tasks.get("duplicate_repo_refs", [])
    if not isinstance(duplicate_refs, list):
        duplicate_refs = []
    selected = duplicate_refs[:max_groups] if max_groups is not None else duplicate_refs
    repair_items = []
    for index, item in enumerate(selected, start=1):
        refs = item.get("refs", []) if isinstance(item, dict) else []
        repair_items.append(
            {
                "repair_index": index,
                "repo_id": item.get("repo_id") if isinstance(item, dict) else None,
                "duplicate_count": item.get("count", len(refs)) if isinstance(item, dict) else len(refs),
                "conflicting_slots": [
                    {
                        "slot_id": ref.get("slot_id"),
                        "task_id": ref.get("task_id"),
                        "path": ref.get("path"),
                        "line": ref.get("line"),
                    }
                    for ref in refs
                    if isinstance(ref, dict)
                ],
                "required_resolution": [
                    "Inspect exactly one duplicate group at a time.",
                    "Keep at most one formal task for the repo_id unless an explicit exception is recorded.",
                    "For any replacement slot, select an unused repo_id, inspect the live repo, rebuild evidence, and run the single-task freeze path.",
                    "Do not script-rewrite formal task rows or batch-generate replacement tasks.",
                ],
                "allowed_artifacts": [
                    "repo-specific inspection log",
                    "replacement candidate review card",
                    "no-bulk generation declaration",
                    "single_task_freeze_audit",
                    "updated strict authoring audit",
                ],
                "forbidden_actions": [
                    "bulk editing slot_*/task.jsonl files",
                    "copying a duplicate task onto a new repo without repo-specific inspection",
                    "marking a reuse exception without an explicit written rationale",
                ],
            }
        )
    report = {
        "schema_version": "2026-06-19",
        "artifact_type": DUPLICATE_REPO_REPAIR_PLAN_ARTIFACT_TYPE,
        "formal_task_record": False,
        "source_audit": str(audit_path),
        "input_files": {
            "source_audit": input_file_record(audit_path),
        },
        "source_audit_passed": audit.get("passed"),
        "source_unique_repo_ids_required": authored_tasks.get("unique_repo_ids_required"),
        "source_duplicate_repo_id_count": authored_tasks.get("duplicate_repo_id_count", len(duplicate_refs)),
        "duplicate_group_count": len(duplicate_refs),
        "included_group_count": len(repair_items),
        "max_groups": max_groups,
        "repair_items": repair_items,
        "notes": [
            "This is a non-formal repair plan and must not be copied into slot_*/task.jsonl.",
            "It does not choose replacement repos, generate tasks, write formal rows, or approve exceptions.",
        ],
    }
    write_json(output_path, report)
    return report


def add_duplicate_repo_repair_plan_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--audit",
        type=Path,
        default=TASK_DIR.parent / "authoring_ledger" / "authoring_data_audit.strict_freeze.json",
        help="Strict authoring audit containing duplicate_repo_refs.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=TASK_DIR.parent / "authoring_ledger" / "duplicate_repo_repair_plan.json",
    )
    parser.add_argument("--max-groups", type=int, default=None)


def run_duplicate_repo_repair_plan_from_args(args: argparse.Namespace) -> None:
    try:
        report = build_duplicate_repo_repair_plan(
            audit_path=args.audit,
            output_path=args.output,
            max_groups=args.max_groups,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(
        f"duplicate repo repair plan: groups={report['duplicate_group_count']} "
        f"included={report['included_group_count']} output={args.output}"
    )


def issue_slot_id(item: dict) -> str | None:
    path = item.get("path")
    if path:
        return Path(str(path)).parent.name
    return item.get("slot_id")


def add_slot_blocker(
    slots: dict[str, dict],
    item: dict,
    *,
    blocker: str,
    errors: list[str] | None = None,
    missing: list[str] | None = None,
) -> None:
    slot_id = issue_slot_id(item)
    if not slot_id:
        return
    slot = slots.setdefault(
        slot_id,
        {
            "slot_id": slot_id,
            "path": item.get("path"),
            "task_id": item.get("task_id"),
            "repo_id": item.get("repo_id"),
            "blockers": [],
            "details": {},
        },
    )
    for key in ("path", "task_id", "repo_id"):
        if not slot.get(key) and item.get(key):
            slot[key] = item.get(key)
    if blocker not in slot["blockers"]:
        slot["blockers"].append(blocker)
    if errors is not None:
        slot["details"].setdefault(blocker, []).extend(str(error) for error in errors)
    if missing is not None:
        slot["details"].setdefault(blocker, []).extend(str(value) for value in missing)


def build_strict_authoring_repair_plan(
    *,
    audit_path: Path,
    output_path: Path,
    max_tasks: int | None = None,
) -> dict:
    if not audit_path.exists():
        raise FileNotFoundError(f"strict authoring audit missing: {audit_path}")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    authored_tasks = audit.get("authored_tasks", {})
    slots: dict[str, dict] = {}
    for item in authored_tasks.get("freeze_audit_issues", []) or []:
        if isinstance(item, dict):
            add_slot_blocker(slots, item, blocker="single_task_freeze_audit", errors=item.get("errors", []))
    for item in authored_tasks.get("no_bulk_declaration_issues", []) or []:
        if isinstance(item, dict):
            add_slot_blocker(slots, item, blocker="no_bulk_generation_declaration", errors=item.get("errors", []))
    for item in authored_tasks.get("review_artifact_issues", []) or []:
        if isinstance(item, dict):
            add_slot_blocker(slots, item, blocker="required_review_artifacts", missing=item.get("missing", []))
    for item in authored_tasks.get("task_file_row_count_issues", []) or []:
        if isinstance(item, dict):
            add_slot_blocker(slots, item, blocker="one_row_slot_contract", errors=item.get("errors", []))
    for item in authored_tasks.get("validation_issues", []) or []:
        if isinstance(item, dict):
            add_slot_blocker(slots, item, blocker="task_validation", errors=item.get("errors", []))
    for item in authored_tasks.get("duplicate_field_issues", []) or []:
        if isinstance(item, dict):
            add_slot_blocker(
                slots,
                item,
                blocker=f"duplicate_field:{item.get('field', 'unknown')}",
                errors=[", ".join(str(value) for value in item.get("duplicates", []))],
            )
    for group in authored_tasks.get("duplicate_repo_refs", []) or []:
        if not isinstance(group, dict):
            continue
        repo_id = group.get("repo_id")
        for ref in group.get("refs", []) or []:
            if isinstance(ref, dict):
                add_slot_blocker(
                    slots,
                    {**ref, "repo_id": repo_id},
                    blocker="duplicate_repo_id",
                    errors=[f"repo_id {repo_id} appears in {group.get('count')} formal slots"],
                )
    repair_items = sorted(slots.values(), key=lambda item: item.get("slot_id") or "")
    if max_tasks is not None:
        repair_items = repair_items[:max_tasks]
    for index, item in enumerate(repair_items, start=1):
        item["repair_index"] = index
        item["blockers"] = sorted(item.get("blockers", []))
        item["required_resolution"] = [
            "Inspect this slot and its current evidence before changing anything.",
            "Do not edit task.jsonl until the replacement/freeze evidence is ready for this single slot.",
            "Backfill or rebuild the listed artifacts one slot at a time.",
            "Run audit-single-task-freeze and then strict audit-authoring-data after the slot is repaired.",
        ]
        item["forbidden_actions"] = [
            "batch-writing no-bulk declarations",
            "batch-generating single_task_freeze_audit files",
            "script-rewriting formal task rows",
            "marking a duplicate repo exception without an explicit repo-specific rationale",
        ]
    report = {
        "schema_version": "2026-06-19",
        "artifact_type": STRICT_AUTHORING_REPAIR_PLAN_ARTIFACT_TYPE,
        "formal_task_record": False,
        "source_audit": str(audit_path),
        "input_files": {
            "source_audit": input_file_record(audit_path),
        },
        "source_audit_passed": audit.get("passed"),
        "source_task_count": authored_tasks.get("task_count"),
        "source_strict_issue_count": len(audit.get("issues", []) or []),
        "blocked_slot_count": len(slots),
        "included_slot_count": len(repair_items),
        "max_tasks": max_tasks,
        "blocker_counts": {
            "single_task_freeze_audit": authored_tasks.get("freeze_audit_issue_count", 0),
            "no_bulk_generation_declaration": authored_tasks.get("no_bulk_declaration_issue_count", 0),
            "required_review_artifacts": authored_tasks.get("review_artifact_issue_count", 0),
            "one_row_slot_contract": authored_tasks.get("task_file_row_count_issue_count", 0),
            "task_validation": authored_tasks.get("validation_issue_count", 0),
            "duplicate_fields": authored_tasks.get("duplicate_field_issue_count", 0),
            "duplicate_repo_groups": authored_tasks.get("duplicate_repo_id_count", 0),
        },
        "repair_items": repair_items,
        "notes": [
            "This is a non-formal repair plan and must not be copied into slot_*/task.jsonl.",
            "It does not generate no-bulk declarations, freeze audits, replacement tasks, or ledger entries.",
        ],
    }
    write_json(output_path, report)
    return report


def add_strict_authoring_repair_plan_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--audit",
        type=Path,
        default=TASK_DIR.parent / "authoring_ledger" / "authoring_data_audit.strict_freeze.json",
        help="Strict authoring audit with freeze/no-bulk/repo blockers.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=TASK_DIR.parent / "authoring_ledger" / "strict_authoring_repair_plan.json",
    )
    parser.add_argument("--max-tasks", type=int, default=None)


def run_strict_authoring_repair_plan_from_args(args: argparse.Namespace) -> None:
    try:
        report = build_strict_authoring_repair_plan(
            audit_path=args.audit,
            output_path=args.output,
            max_tasks=args.max_tasks,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(
        f"strict authoring repair plan: blocked_slots={report['blocked_slot_count']} "
        f"included={report['included_slot_count']} output={args.output}"
    )


def build_strict_freeze_worklist(
    *,
    repair_plan_path: Path,
    output_path: Path,
    max_items: int | None = None,
) -> dict:
    if not repair_plan_path.exists():
        raise FileNotFoundError(f"strict authoring repair plan missing: {repair_plan_path}")
    plan = json.loads(repair_plan_path.read_text(encoding="utf-8"))
    issues: list[str] = []
    if plan.get("artifact_type") != STRICT_AUTHORING_REPAIR_PLAN_ARTIFACT_TYPE:
        issues.append(f"strict authoring repair plan has wrong artifact_type: {plan.get('artifact_type')}")
    if plan.get("formal_task_record") is not False:
        issues.append("strict authoring repair plan must be formal_task_record=false")
    repair_items = [item for item in plan.get("repair_items", []) if isinstance(item, dict)]
    if max_items is not None:
        repair_items = repair_items[:max_items]
    work_items: list[dict] = []
    for index, item in enumerate(repair_items, start=1):
        slot_id = str(item.get("slot_id") or "")
        task_path = Path(str(item.get("path") or "")) if item.get("path") else TASK_DIR / slot_id / "task.jsonl"
        slot_dir = task_path.parent
        progress_path = TASK_DIR.parent / "authoring_ledger" / "per_task_progress" / f"{slot_id}.json"
        blockers = sorted(str(blocker) for blocker in item.get("blockers", []) or [])
        commands = [
            (
                "python -m sitecontinuum start-strict-authoring-repair "
                f"--repair-plan {repair_plan_path} --slot-id {slot_id}"
            ),
            (
                "python -m sitecontinuum no-bulk-declaration-template "
                f"--slot-dir {slot_dir}"
            ),
            (
                "python -m sitecontinuum audit-single-task-freeze "
                f"--slot-dir {slot_dir}"
            ),
            (
                "python -m sitecontinuum audit-authoring-task "
                f"--progress {progress_path}"
            ),
            (
                "python -m sitecontinuum audit-authoring-data "
                "--require-freeze-audits --require-no-bulk-declarations "
                "--report data/productwebbench/authoring_ledger/authoring_data_audit.strict_freeze.json"
            ),
        ]
        if "duplicate_repo_id" in blockers:
            commands.insert(
                1,
                (
                    "# duplicate repo blocker: inspect duplicate group, select a non-overlapping replacement repo if needed, "
                    "and do not edit task.jsonl until replacement evidence is ready"
                ),
            )
        work_items.append(
            {
                "work_index": index,
                "repair_index": item.get("repair_index"),
                "slot_id": slot_id,
                "task_id": item.get("task_id"),
                "repo_id": item.get("repo_id"),
                "task_path": str(task_path),
                "slot_dir": str(slot_dir),
                "progress_path": str(progress_path),
                "blockers": blockers,
                "details": item.get("details", {}),
                "required_resolution": item.get("required_resolution", []),
                "commands": commands,
                "forbidden_actions": [
                    "do not batch-write no_bulk_generation_declaration.md",
                    "do not batch-generate single_task_freeze_audit.json",
                    "do not rewrite task.jsonl by script",
                    "do not mark duplicate repo exceptions without repo-specific rationale",
                    "do not count this worklist as freeze evidence",
                ],
            }
        )
    report = {
        "schema_version": "2026-06-19",
        "artifact_type": STRICT_FREEZE_WORKLIST_ARTIFACT_TYPE,
        "formal_task_record": False,
        "source_repair_plan": str(repair_plan_path),
        "output_path": str(output_path),
        "input_files": {
            "repair_plan": input_file_record(repair_plan_path),
        },
        "passed": not issues,
        "issue_count": len(issues),
        "issues": issues,
        "source_blocked_slot_count": int(plan.get("blocked_slot_count") or 0),
        "source_included_slot_count": int(plan.get("included_slot_count") or 0),
        "work_item_count": len(work_items),
        "max_items": max_items,
        "blocker_counts": dict(sorted(Counter(blocker for item in work_items for blocker in item["blockers"]).items())),
        "work_items": work_items,
        "gate": "This worklist only schedules one-slot strict freeze repairs; it does not create declarations, freeze audits, replacement tasks, or formal task rows.",
    }
    write_json(output_path, report)
    return report


def add_strict_freeze_worklist_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repair-plan",
        type=Path,
        default=TASK_DIR.parent / "authoring_ledger" / "strict_authoring_repair_plan.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=TASK_DIR.parent / "authoring_ledger" / "strict_freeze_worklist.json",
    )
    parser.add_argument("--max-items", type=int, default=None)


def run_strict_freeze_worklist_from_args(args: argparse.Namespace) -> None:
    assert_not_under_formal_task_root(args.output, purpose="strict freeze worklist")
    ensure_dir(args.output.parent)
    try:
        report = build_strict_freeze_worklist(
            repair_plan_path=args.repair_plan,
            output_path=args.output,
            max_items=args.max_items,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(
        "strict freeze worklist: "
        f"passed={report['passed']} source_blocked={report['source_blocked_slot_count']} "
        f"work_items={report['work_item_count']} issues={report['issue_count']} output={args.output}"
    )
    if not report["passed"]:
        raise SystemExit(1)


def write_task_template(output_path: Path) -> None:
    assert_not_under_formal_task_root(output_path, purpose="Authoring-only task template")
    ensure_dir(output_path.parent)
    template = {
        "artifact_type": "authoring_template_only",
        "formal_task_record": False,
        "template_warning": "This file is only a per-task authoring scaffold. Do not batch-fill it or count it as benchmark data.",
        "task_id": "dev_owner__repo__shortsha__001",
        "repo_id": "owner__repo__shortsha",
        "split": "dev",
        "intent": "add",
        "scope": "section",
        "difficulty": "L2",
        "problem_statement": "Add a polished section to the existing page. It should use the current site's visual language and remain clean across desktop and mobile states.",
        "required_content": [
            "The new section is visible on the target route.",
            "The section contains realistic, non-placeholder content.",
        ],
        "design_constraints": [
            "Reuse the existing typography scale and button/card styling.",
            "Match the surrounding section spacing and image treatment.",
        ],
        "state_constraints": [
            "Desktop initial state remains stable.",
            "Mobile initial state has no horizontal overflow or text overlap.",
        ],
        "assets_to_consider": [],
        "suggested_files": [],
        "required_states": ["desktop_initial", "mobile_initial"],
        "hidden_states": ["tablet_initial"],
        "evaluation_rubric": {
            "change_completion": ["Required content exists in real DOM, not as a screenshot."],
            "design_consistency": ["New content visually matches existing components."],
            "responsive": ["Desktop and mobile layouts are polished."],
            "regression": ["Existing nav, footer, and hero remain stable."],
        },
        "author_notes": "Replace this template with a repo-specific, agent-audited task after inspecting the live repo, source files, assets, captured states, and verifier behavior.",
    }
    output_path.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")


def add_template_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", type=Path, default=TASK_DIR.parent / "authoring_ledger" / "task_template.json")


def run_template_from_args(args: argparse.Namespace) -> None:
    try:
        write_task_template(args.output)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"wrote authoring-only task template to {args.output} (not a formal benchmark task)")
