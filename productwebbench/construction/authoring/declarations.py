from __future__ import annotations

from pathlib import Path
from typing import Any


DECLARATION_NAME = "no_bulk_generation_declaration.md"
DECLARATION_ARTIFACT_TYPE = "no_bulk_generation_declaration"
ALLOWED_AUTHORING_MODES = {"single_task_manual", "one_by_one_manual"}
FALSE_VALUES = {"false", "no", "0"}
TRUE_VALUES = {"true", "yes", "1"}
REQUIRED_TRUE_FIELDS = (
    "task_designed_one_by_one",
    "repo_inspected_manually",
    "evidence_not_generated_in_bulk",
    "reviewer_confirmation",
)
REQUIRED_TEXT_FIELDS = (
    "repo_inspection_evidence",
    "design_decision_summary",
    "verifier_evidence",
    "negative_control_evidence",
)
TEMPLATE_MARKERS = (
    "template_only: true",
    "todo",
    "<fill",
    "<task",
    "<repo",
    "replace this",
)


def parse_frontmatter(text: str) -> tuple[dict[str, str], list[str]]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, ["declaration must start with YAML-style front matter"]
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration:
        return {}, ["declaration front matter must be closed with ---"]
    fields: dict[str, str] = {}
    issues: list[str] = []
    for line in lines[1:end]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            issues.append(f"front matter line is not key: value: {stripped}")
            continue
        key, value = stripped.split(":", 1)
        fields[key.strip().lower()] = value.strip().strip("\"'")
    return fields, issues


def bool_field(fields: dict[str, str], key: str) -> bool | None:
    value = fields.get(key)
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in TRUE_VALUES:
        return True
    if lowered in FALSE_VALUES:
        return False
    return None


def audit_no_bulk_declaration_text(
    text: str,
    *,
    task_id: str | None,
    repo_id: str | None,
    slot_id: str | None = None,
) -> dict[str, Any]:
    fields, issues = parse_frontmatter(text)
    lowered_text = text.lower()
    for marker in TEMPLATE_MARKERS:
        if marker in lowered_text:
            issues.append(f"declaration contains template marker: {marker}")

    if len(text.strip()) < 300:
        issues.append("declaration is too short for a task-specific freeze statement")
    if fields.get("artifact_type") != DECLARATION_ARTIFACT_TYPE:
        issues.append(f"artifact_type must be {DECLARATION_ARTIFACT_TYPE}")
    if bool_field(fields, "formal_task_record") is not False:
        issues.append("formal_task_record must be false")
    if task_id and fields.get("task_id") != task_id:
        issues.append(f"task_id mismatch: {fields.get('task_id')} != {task_id}")
    if repo_id and fields.get("repo_id") != repo_id:
        issues.append(f"repo_id mismatch: {fields.get('repo_id')} != {repo_id}")
    if slot_id and fields.get("slot_id") != slot_id:
        issues.append(f"slot_id mismatch: {fields.get('slot_id')} != {slot_id}")
    if fields.get("authoring_mode") not in ALLOWED_AUTHORING_MODES:
        issues.append(f"authoring_mode must be one of {sorted(ALLOWED_AUTHORING_MODES)}")
    if bool_field(fields, "bulk_generation_used") is not False:
        issues.append("bulk_generation_used must be false")
    for key in REQUIRED_TRUE_FIELDS:
        if bool_field(fields, key) is not True:
            issues.append(f"{key} must be true")
    for key in REQUIRED_TEXT_FIELDS:
        value = fields.get(key, "")
        if len(value.strip()) < 20:
            issues.append(f"{key} must contain concrete task-specific evidence")
    return {
        "passed": not issues,
        "issues": issues,
        "fields": fields,
        "text_length": len(text.strip()),
    }


def audit_no_bulk_declaration_file(
    declaration_path: Path,
    *,
    task_id: str | None,
    repo_id: str | None,
    slot_id: str | None = None,
) -> dict[str, Any]:
    if not declaration_path.exists():
        return {
            "passed": False,
            "issues": [f"missing {DECLARATION_NAME}"],
            "fields": {},
            "text_length": 0,
        }
    text = declaration_path.read_text(encoding="utf-8", errors="ignore")
    report = audit_no_bulk_declaration_text(text, task_id=task_id, repo_id=repo_id, slot_id=slot_id)
    report["path"] = str(declaration_path)
    return report


def build_no_bulk_declaration_template(task: dict[str, Any], *, slot_id: str) -> str:
    task_id = str(task.get("task_id") or "")
    repo_id = str(task.get("repo_id") or "")
    return f"""---
artifact_type: {DECLARATION_ARTIFACT_TYPE}
formal_task_record: false
template_only: true
task_id: {task_id}
repo_id: {repo_id}
slot_id: {slot_id}
authoring_mode: one_by_one_manual
bulk_generation_used: false
task_designed_one_by_one: false
repo_inspected_manually: false
evidence_not_generated_in_bulk: false
reviewer_confirmation: false
repo_inspection_evidence: TODO fill concrete files, routes, states, screenshots, or captures inspected for this one task.
design_decision_summary: TODO fill the one-task design rationale and why this repo/task pair was chosen.
verifier_evidence: TODO fill reference/submission verifier artifacts inspected for this one task.
negative_control_evidence: TODO fill original/baseline and bad-solution evidence inspected for this one task.
---

This is a non-formal template. It is intentionally rejected by the freeze auditor until
template_only and every TODO/false field are replaced with concrete, task-specific evidence.
"""
