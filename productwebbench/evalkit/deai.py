from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..core.config import DEFAULT_OUTPUT_ROOT
from ..core.formal_data_guard import assert_not_under_formal_task_root
from ..core.io_utils import ensure_dir, read_jsonl, write_json, write_jsonl


GENERIC_AI_TEXT_PATTERNS = (
    "lorem ipsum",
    "your company",
    "welcome to our website",
    "modern and beautiful",
    "seamless experience",
)
CONTRAST_ANCHOR_ARTIFACT_TYPE = "de_ai_contrast_anchor"
CONTRAST_QUEUE_ARTIFACT_TYPE = "de_ai_contrast_anchor_queue"
CONTRAST_AUDIT_ARTIFACT_TYPE = "de_ai_contrast_audit"
DEFAULT_CONTRAST_SET = DEFAULT_OUTPUT_ROOT / "eval_protocol" / "deai_contrast_set.jsonl"
DEFAULT_CONTRAST_QUEUE = DEFAULT_OUTPUT_ROOT / "eval_protocol" / "deai_contrast_anchor_queue.json"
DEFAULT_CONTRAST_AUDIT = DEFAULT_OUTPUT_ROOT / "eval_protocol" / "deai_contrast_audit.json"
DEFAULT_MIN_PUBLISHABLE_ANCHORS = 12
DEFAULT_MIN_PUBLISHABLE_MODEL_FAMILIES = 3
DEFAULT_MIN_PUBLISHABLE_WEBSITE_TYPES = 3
DEFAULT_MIN_PUBLISHABLE_FINGERPRINT_RECORDS = 8
REQUIRED_FINGERPRINT_FIELDS = {
    "layout_patterns",
    "palette_patterns",
    "text_patterns",
    "asset_patterns",
    "component_patterns",
}
VALID_SOURCE_KINDS = {"unconstrained_model_generation", "ai_default_template", "manual_ai_template_audit"}
UNKNOWN_VALUES = {"", "unknown", "unk", "n/a", "na", "none", "null", "tbd", "todo"}


def assert_nonformal_output(path: Path | None, *, purpose: str) -> None:
    if path is not None:
        try:
            assert_not_under_formal_task_root(path, purpose=purpose)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc


@dataclass(frozen=True)
class DeAIFingerprintReport:
    score: float
    placeholder_hits: list[str]
    passed: bool

    def to_json(self) -> dict:
        return asdict(self)


def simple_deai_fingerprint(text: str, *, min_score: float = 0.8) -> DeAIFingerprintReport:
    lowered = text.lower()
    hits = [pattern for pattern in GENERIC_AI_TEXT_PATTERNS if pattern in lowered]
    score = max(0.0, 1.0 - 0.25 * len(hits))
    return DeAIFingerprintReport(score=round(score, 4), placeholder_hits=hits, passed=score >= min_score)


def normalized(value: object) -> str:
    return str(value or "").strip().lower()


def duplicate_values(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


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


def fingerprint_field_count(record: dict[str, Any]) -> int:
    fingerprint = record.get("fingerprint")
    if not isinstance(fingerprint, dict):
        return 0
    return sum(1 for field in REQUIRED_FINGERPRINT_FIELDS if fingerprint.get(field))


def text_for_record(record: dict[str, Any]) -> str:
    values = []
    for field in ("text", "html", "sample_text", "sample_html", "prompt"):
        value = record.get(field)
        if value:
            values.append(str(value))
    return "\n".join(values)


def load_contrast_records(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    if not path.exists():
        return [], [f"de-AI contrast set missing: {path}"]
    records: list[dict[str, Any]] = []
    issues: list[str] = []
    try:
        for index, record in enumerate(read_jsonl(path), start=1):
            if not isinstance(record, dict):
                issues.append(f"{path}:{index}: contrast record must be a JSON object")
                continue
            records.append(record)
    except (OSError, json.JSONDecodeError) as exc:
        issues.append(f"could not read de-AI contrast set {path}: {exc}")
    return records, issues


def anchor_structural_issues(record: dict[str, Any]) -> list[str]:
    anchor_id = str(record.get("anchor_id") or "")
    artifact_type = record.get("artifact_type")
    formal_task_record = record.get("formal_task_record")
    model_family = normalized(record.get("model_family"))
    provider = normalized(record.get("provider"))
    model = normalized(record.get("model"))
    website_type = normalized(record.get("website_type"))
    source_kind = normalized(record.get("source_kind"))
    issues: list[str] = []
    if artifact_type != CONTRAST_ANCHOR_ARTIFACT_TYPE:
        issues.append(f"artifact_type must be {CONTRAST_ANCHOR_ARTIFACT_TYPE}")
    if formal_task_record is not False:
        issues.append("formal_task_record must be false")
    if not anchor_id:
        issues.append("missing anchor_id")
    if model_family in UNKNOWN_VALUES:
        issues.append("missing or unknown model_family")
    if provider in UNKNOWN_VALUES:
        issues.append("missing or unknown provider")
    if model in UNKNOWN_VALUES:
        issues.append("missing or unknown model")
    if website_type in UNKNOWN_VALUES:
        issues.append("missing or unknown website_type")
    if source_kind not in VALID_SOURCE_KINDS:
        issues.append(f"invalid source_kind: {source_kind or '<missing>'}")
    if fingerprint_field_count(record) == 0 and not text_for_record(record):
        issues.append("missing fingerprint fields and sample text/html")
    if record.get("template_only") is True:
        issues.append("template_only contrast anchors are not publishable records")
    if record.get("template_warning"):
        issues.append("contrast anchor still contains template_warning and must be replaced by a real inspected sample")
    return issues


def contrast_anchor_template() -> dict[str, Any]:
    return {
        "schema_version": "2026-06-19",
        "artifact_type": CONTRAST_ANCHOR_ARTIFACT_TYPE,
        "formal_task_record": False,
        "template_only": True,
        "template_warning": "Fill one real de-AI negative anchor after inspecting one model-generated website sample. This template is not a passing anchor.",
        "anchor_id": "TBD_unique_anchor_id",
        "model_family": "TBD",
        "provider": "TBD",
        "model": "TBD",
        "website_type": "TBD",
        "source_kind": "unconstrained_model_generation",
        "prompt": "TBD prompt used to produce the default AI website sample",
        "sample_text": "TBD visible/default text snippets",
        "sample_html": "",
        "fingerprint": {
            "layout_patterns": [],
            "palette_patterns": [],
            "text_patterns": [],
            "asset_patterns": [],
            "component_patterns": [],
        },
        "notes": "One anchor only. Do not batch-generate contrast anchors.",
    }


def build_contrast_anchor(
    *,
    anchor_id: str,
    model_family: str,
    website_type: str,
    source_kind: str,
    provider: str | None = None,
    model: str | None = None,
    prompt: str | None = None,
    sample_text: str | None = None,
    sample_html: str | None = None,
    layout_patterns: list[str] | None = None,
    palette_patterns: list[str] | None = None,
    text_patterns: list[str] | None = None,
    asset_patterns: list[str] | None = None,
    component_patterns: list[str] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    fingerprint = {
        "layout_patterns": layout_patterns or [],
        "palette_patterns": palette_patterns or [],
        "text_patterns": text_patterns or [],
        "asset_patterns": asset_patterns or [],
        "component_patterns": component_patterns or [],
    }
    return {
        "schema_version": "2026-06-19",
        "artifact_type": CONTRAST_ANCHOR_ARTIFACT_TYPE,
        "formal_task_record": False,
        "anchor_id": anchor_id,
        "model_family": model_family,
        "provider": provider,
        "model": model,
        "website_type": website_type,
        "source_kind": source_kind,
        "prompt": prompt,
        "sample_text": sample_text,
        "sample_html": sample_html,
        "fingerprint": fingerprint,
        "notes": notes,
    }


def append_contrast_anchor(
    contrast_set_path: Path,
    anchor: dict[str, Any],
    *,
    audit_output_path: Path | None = None,
) -> dict[str, Any]:
    issues = anchor_structural_issues(anchor)
    existing, load_issues = load_contrast_records(contrast_set_path) if contrast_set_path.exists() else ([], [])
    issues.extend(load_issues)
    anchor_id = str(anchor.get("anchor_id") or "")
    if anchor_id and anchor_id in {str(record.get("anchor_id") or "") for record in existing}:
        issues.append(f"duplicate anchor_id already exists in contrast set: {anchor_id}")
    report = {
        "schema_version": "2026-06-19",
        "artifact_type": "de_ai_contrast_anchor_append_audit",
        "formal_task_record": False,
        "contrast_set_path": str(contrast_set_path),
        "anchor_id": anchor_id,
        "passed": not issues,
        "issue_count": len(issues),
        "issues": issues,
        "existing_anchor_count": len(existing),
        "would_write_anchor_count": len(existing) + (0 if issues else 1),
        "gate": "Append exactly one manually authored de-AI contrast anchor; never batch-generate contrast anchors.",
    }
    if issues:
        if audit_output_path is not None:
            write_json(audit_output_path, report)
        return report
    ensure_dir(contrast_set_path.parent)
    write_jsonl(contrast_set_path, [*existing, anchor])
    if audit_output_path is not None:
        write_json(audit_output_path, report)
    return report


def audit_deai_contrast_set(
    contrast_set_path: Path,
    output_path: Path | None = None,
    *,
    min_anchors: int = 12,
    min_model_families: int = 3,
    min_website_types: int = 3,
    min_fingerprint_field_records: int = 8,
) -> dict[str, Any]:
    issues: list[str] = []
    records, load_issues = load_contrast_records(contrast_set_path)
    issues.extend(load_issues)
    if len(records) < min_anchors:
        issues.append(f"de-AI contrast anchors {len(records)} < {min_anchors}")

    anchor_ids: list[str] = []
    family_counts: Counter[str] = Counter()
    website_type_counts: Counter[str] = Counter()
    source_kind_counts: Counter[str] = Counter()
    fingerprint_field_records = 0
    placeholder_hits: Counter[str] = Counter()
    invalid_records: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        anchor_id = str(record.get("anchor_id") or "")
        if anchor_id:
            anchor_ids.append(anchor_id)
        artifact_type = record.get("artifact_type")
        formal_task_record = record.get("formal_task_record")
        model_family = normalized(record.get("model_family"))
        website_type = normalized(record.get("website_type"))
        source_kind = normalized(record.get("source_kind"))
        record_issues = anchor_structural_issues(record)
        if fingerprint_field_count(record) > 0:
            fingerprint_field_records += 1
        if record_issues:
            invalid_records.append({"line": index, "anchor_id": anchor_id, "issues": record_issues})
        if model_family not in UNKNOWN_VALUES:
            family_counts[model_family] += 1
        if website_type not in UNKNOWN_VALUES:
            website_type_counts[website_type] += 1
        if source_kind:
            source_kind_counts[source_kind] += 1
        text = text_for_record(record)
        if text:
            report = simple_deai_fingerprint(text)
            for hit in report.placeholder_hits:
                placeholder_hits[hit] += 1

    duplicate_anchor_ids = duplicate_values(anchor_ids)
    if duplicate_anchor_ids:
        issues.append(f"de-AI contrast set has {len(duplicate_anchor_ids)} duplicate anchor ids")
    if len(family_counts) < min_model_families:
        issues.append(f"model families {len(family_counts)} < {min_model_families}")
    if len(website_type_counts) < min_website_types:
        issues.append(f"website types {len(website_type_counts)} < {min_website_types}")
    if fingerprint_field_records < min_fingerprint_field_records:
        issues.append(f"fingerprint-rich records {fingerprint_field_records} < {min_fingerprint_field_records}")
    if invalid_records:
        issues.append(f"de-AI contrast set has {len(invalid_records)} structurally invalid records")
    publish_blockers: list[str] = []
    if min_anchors < DEFAULT_MIN_PUBLISHABLE_ANCHORS:
        publish_blockers.append(f"publishable de-AI contrast audits require min_anchors >= {DEFAULT_MIN_PUBLISHABLE_ANCHORS}")
    if min_model_families < DEFAULT_MIN_PUBLISHABLE_MODEL_FAMILIES:
        publish_blockers.append(
            f"publishable de-AI contrast audits require min_model_families >= {DEFAULT_MIN_PUBLISHABLE_MODEL_FAMILIES}"
        )
    if min_website_types < DEFAULT_MIN_PUBLISHABLE_WEBSITE_TYPES:
        publish_blockers.append(
            f"publishable de-AI contrast audits require min_website_types >= {DEFAULT_MIN_PUBLISHABLE_WEBSITE_TYPES}"
        )
    if min_fingerprint_field_records < DEFAULT_MIN_PUBLISHABLE_FINGERPRINT_RECORDS:
        publish_blockers.append(
            "publishable de-AI contrast audits require "
            f"min_fingerprint_field_records >= {DEFAULT_MIN_PUBLISHABLE_FINGERPRINT_RECORDS}"
        )
    if len(records) < DEFAULT_MIN_PUBLISHABLE_ANCHORS:
        publish_blockers.append("de-AI contrast set has fewer than 12 anchors")
    if len(family_counts) < DEFAULT_MIN_PUBLISHABLE_MODEL_FAMILIES:
        publish_blockers.append("de-AI contrast set has fewer than 3 model families")
    if len(website_type_counts) < DEFAULT_MIN_PUBLISHABLE_WEBSITE_TYPES:
        publish_blockers.append("de-AI contrast set has fewer than 3 website types")
    if fingerprint_field_records < DEFAULT_MIN_PUBLISHABLE_FINGERPRINT_RECORDS:
        publish_blockers.append("de-AI contrast set has fewer than 8 fingerprint-rich records")
    publishable = not issues and not publish_blockers

    report = {
        "schema_version": "2026-06-19",
        "artifact_type": CONTRAST_AUDIT_ARTIFACT_TYPE,
        "formal_task_record": False,
        "contrast_set_path": str(contrast_set_path),
        "input_files": {
            "contrast_set": input_file_record(contrast_set_path),
        },
        "contrast_set_available": contrast_set_path.exists(),
        "passed": not issues,
        "publishable": publishable,
        "publish_blockers": publish_blockers,
        "issue_count": len(issues),
        "issues": issues,
        "anchor_count": len(records),
        "unique_anchor_ids": len(set(anchor_ids)),
        "duplicate_anchor_ids": duplicate_anchor_ids,
        "model_family_count": len(family_counts),
        "model_family_counts": dict(sorted(family_counts.items())),
        "website_type_count": len(website_type_counts),
        "website_type_counts": dict(sorted(website_type_counts.items())),
        "source_kind_counts": dict(sorted(source_kind_counts.items())),
        "fingerprint_rich_records": fingerprint_field_records,
        "placeholder_hit_counts": dict(sorted(placeholder_hits.items())),
        "invalid_records": invalid_records[:100],
        "gate": "Phase 0 requires a non-formal de-AI negative-anchor contrast set before claiming MM/de-AI publish readiness.",
    }
    if output_path is not None:
        write_json(output_path, report)
    return report


def build_deai_contrast_anchor_queue(
    *,
    contrast_set_path: Path,
    output_path: Path,
    min_anchors: int = DEFAULT_MIN_PUBLISHABLE_ANCHORS,
    min_model_families: int = DEFAULT_MIN_PUBLISHABLE_MODEL_FAMILIES,
    min_website_types: int = DEFAULT_MIN_PUBLISHABLE_WEBSITE_TYPES,
    min_fingerprint_field_records: int = DEFAULT_MIN_PUBLISHABLE_FINGERPRINT_RECORDS,
) -> dict[str, Any]:
    records, load_issues = load_contrast_records(contrast_set_path) if contrast_set_path.exists() else ([], [])
    issues: list[str] = list(load_issues)
    valid_records: list[dict[str, Any]] = []
    invalid_records: list[dict[str, Any]] = []
    anchor_ids: list[str] = []
    for index, record in enumerate(records, start=1):
        anchor_id = str(record.get("anchor_id") or "")
        if anchor_id:
            anchor_ids.append(anchor_id)
        record_issues = anchor_structural_issues(record)
        if record_issues:
            invalid_records.append({"line": index, "anchor_id": anchor_id or None, "issues": record_issues})
            continue
        valid_records.append(record)

    duplicate_anchor_ids = duplicate_values(anchor_ids)
    if duplicate_anchor_ids:
        issues.append(f"existing de-AI contrast set has {len(duplicate_anchor_ids)} duplicate anchor ids")
    if invalid_records:
        issues.append(f"existing de-AI contrast set has {len(invalid_records)} invalid records")

    family_counts: Counter[str] = Counter(
        normalized(record.get("model_family"))
        for record in valid_records
        if normalized(record.get("model_family")) not in UNKNOWN_VALUES
    )
    website_type_counts: Counter[str] = Counter(
        normalized(record.get("website_type"))
        for record in valid_records
        if normalized(record.get("website_type")) not in UNKNOWN_VALUES
    )
    fingerprint_rich_count = sum(1 for record in valid_records if fingerprint_field_count(record) > 0)
    existing_anchor_count = len(valid_records)
    missing_anchor_count = max(0, min_anchors - existing_anchor_count)
    missing_fingerprint_count = max(0, min_fingerprint_field_records - fingerprint_rich_count)
    family_slots = [f"model_family_slot_{index}" for index in range(1, max(min_model_families, 1) + 1)]
    website_type_slots = [f"website_type_slot_{index}" for index in range(1, max(min_website_types, 1) + 1)]
    source_kind_cycle = [
        "unconstrained_model_generation",
        "ai_default_template",
        "manual_ai_template_audit",
    ]

    queue_items: list[dict[str, Any]] = []
    existing_id_set = set(anchor_ids)
    for offset in range(missing_anchor_count):
        target_family_slot = family_slots[(len(valid_records) + offset) % len(family_slots)]
        target_website_type_slot = website_type_slots[(len(valid_records) + offset) % len(website_type_slots)]
        source_kind = source_kind_cycle[(len(valid_records) + offset) % len(source_kind_cycle)]
        anchor_id = f"deai_anchor_{existing_anchor_count + offset + 1:03d}_{target_family_slot}_{target_website_type_slot}"
        while anchor_id in existing_id_set:
            anchor_id += "_next"
        existing_id_set.add(anchor_id)
        fingerprint_required = offset < missing_fingerprint_count
        queue_items.append(
            {
                "queue_index": len(queue_items) + 1,
                "anchor_id": anchor_id,
                "target_model_family_slot": target_family_slot,
                "target_website_type_slot": target_website_type_slot,
                "source_kind": source_kind,
                "fingerprint_required": fingerprint_required,
                "required_fingerprint_fields": sorted(REQUIRED_FINGERPRINT_FIELDS) if fingerprint_required else [],
                "authoring_requirements": {
                    "must_inspect_one_real_ai_generated_website_sample": True,
                    "must_record_provider_model_family": True,
                    "must_record_visible_sample_text_or_html": True,
                    "must_record_fingerprint_patterns": fingerprint_required,
                    "forbidden": [
                        "do not batch-generate anchors",
                        "do not use TBD/unknown provider, model, model_family, or website_type",
                        "do not count this queue item as a contrast anchor",
                        "do not write under data/productwebbench/tasks",
                    ],
                },
                "append_command": (
                    "python -m productwebbench append-deai-contrast-anchor "
                    f"--contrast-set {contrast_set_path} "
                    f"--anchor-id {anchor_id} "
                    "--model-family <real_model_family> "
                    "--provider <provider> "
                    "--model <model> "
                    "--website-type <website_type> "
                    f"--source-kind {source_kind} "
                    "--prompt '<prompt used to produce or identify the sample>' "
                    "--sample-text '<visible default/AI-template text snippets>' "
                    "--layout-pattern '<inspected layout pattern>' "
                    "--palette-pattern '<inspected palette pattern>' "
                    "--text-pattern '<inspected text/default-template pattern>' "
                    "--asset-pattern '<inspected asset pattern>' "
                    "--component-pattern '<inspected component pattern>'"
                ),
            }
        )

    report = {
        "schema_version": "2026-06-19",
        "artifact_type": CONTRAST_QUEUE_ARTIFACT_TYPE,
        "formal_task_record": False,
        "contrast_set_path": str(contrast_set_path),
        "output_path": str(output_path),
        "input_files": {
            "contrast_set": input_file_record(contrast_set_path),
        },
        "passed": not issues,
        "issue_count": len(issues),
        "issues": issues,
        "contrast_set_available": contrast_set_path.exists(),
        "existing_record_count": len(records),
        "valid_anchor_count": existing_anchor_count,
        "invalid_record_count": len(invalid_records),
        "invalid_records": invalid_records[:100],
        "duplicate_anchor_ids": duplicate_anchor_ids,
        "model_family_count": len(family_counts),
        "model_family_counts": dict(sorted(family_counts.items())),
        "website_type_count": len(website_type_counts),
        "website_type_counts": dict(sorted(website_type_counts.items())),
        "fingerprint_rich_records": fingerprint_rich_count,
        "required_anchor_count": min_anchors,
        "required_model_family_count": min_model_families,
        "required_website_type_count": min_website_types,
        "required_fingerprint_rich_records": min_fingerprint_field_records,
        "missing_anchor_count": missing_anchor_count,
        "missing_fingerprint_rich_count": missing_fingerprint_count,
        "queue_item_count": len(queue_items),
        "queue_items": queue_items,
        "gate": "This queue schedules one inspected de-AI contrast anchor at a time; it must not contain completed sample evidence or count as a contrast anchor.",
    }
    write_json(output_path, report)
    return report


def add_anchor_template_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "eval_protocol" / "deai_contrast_anchor.template.json")


def run_anchor_template_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.output, purpose="de-AI contrast anchor template")
    ensure_dir(args.output.parent)
    write_json(args.output, contrast_anchor_template())
    print(f"wrote non-formal de-AI contrast anchor template to {args.output}; template is not publishable")


def add_anchor_queue_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--contrast-set", type=Path, default=DEFAULT_CONTRAST_SET)
    parser.add_argument("--output", type=Path, default=DEFAULT_CONTRAST_QUEUE)
    parser.add_argument("--min-anchors", type=int, default=DEFAULT_MIN_PUBLISHABLE_ANCHORS)
    parser.add_argument("--min-model-families", type=int, default=DEFAULT_MIN_PUBLISHABLE_MODEL_FAMILIES)
    parser.add_argument("--min-website-types", type=int, default=DEFAULT_MIN_PUBLISHABLE_WEBSITE_TYPES)
    parser.add_argument("--min-fingerprint-field-records", type=int, default=DEFAULT_MIN_PUBLISHABLE_FINGERPRINT_RECORDS)


def run_anchor_queue_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.output, purpose="de-AI contrast anchor queue")
    ensure_dir(args.output.parent)
    report = build_deai_contrast_anchor_queue(
        contrast_set_path=args.contrast_set,
        output_path=args.output,
        min_anchors=args.min_anchors,
        min_model_families=args.min_model_families,
        min_website_types=args.min_website_types,
        min_fingerprint_field_records=args.min_fingerprint_field_records,
    )
    print(
        "de-AI contrast anchor queue: "
        f"passed={report['passed']} valid_anchors={report['valid_anchor_count']} "
        f"missing={report['missing_anchor_count']} queue={report['queue_item_count']} "
        f"issues={report['issue_count']} output={args.output}"
    )
    if not report["passed"]:
        raise SystemExit(1)


def add_append_anchor_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--contrast-set", type=Path, default=DEFAULT_CONTRAST_SET)
    parser.add_argument("--audit-output", type=Path, default=None)
    parser.add_argument("--anchor-id", required=True)
    parser.add_argument("--model-family", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--website-type", required=True)
    parser.add_argument("--source-kind", choices=sorted(VALID_SOURCE_KINDS), default="unconstrained_model_generation")
    parser.add_argument("--prompt", default=None)
    parser.add_argument("--sample-text", default=None)
    parser.add_argument("--sample-html", default=None)
    parser.add_argument("--layout-pattern", action="append", default=[])
    parser.add_argument("--palette-pattern", action="append", default=[])
    parser.add_argument("--text-pattern", action="append", default=[])
    parser.add_argument("--asset-pattern", action="append", default=[])
    parser.add_argument("--component-pattern", action="append", default=[])
    parser.add_argument("--notes", default=None)


def run_append_anchor_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.contrast_set, purpose="de-AI contrast set")
    assert_nonformal_output(args.audit_output, purpose="de-AI contrast append audit")
    anchor = build_contrast_anchor(
        anchor_id=args.anchor_id,
        model_family=args.model_family,
        provider=args.provider,
        model=args.model,
        website_type=args.website_type,
        source_kind=args.source_kind,
        prompt=args.prompt,
        sample_text=args.sample_text,
        sample_html=args.sample_html,
        layout_patterns=args.layout_pattern,
        palette_patterns=args.palette_pattern,
        text_patterns=args.text_pattern,
        asset_patterns=args.asset_pattern,
        component_patterns=args.component_pattern,
        notes=args.notes,
    )
    report = append_contrast_anchor(args.contrast_set, anchor, audit_output_path=args.audit_output)
    print(
        f"append de-AI contrast anchor: passed={report['passed']} "
        f"anchor={report['anchor_id']} issues={report['issue_count']} contrast_set={args.contrast_set}"
    )
    if not report["passed"]:
        raise SystemExit(1)


def add_deai_contrast_audit_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--contrast-set", type=Path, default=DEFAULT_CONTRAST_SET)
    parser.add_argument("--output", type=Path, default=DEFAULT_CONTRAST_AUDIT)
    parser.add_argument("--min-anchors", type=int, default=12)
    parser.add_argument("--min-model-families", type=int, default=3)
    parser.add_argument("--min-website-types", type=int, default=3)
    parser.add_argument("--min-fingerprint-field-records", type=int, default=8)


def run_deai_contrast_audit_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.output, purpose="de-AI contrast audit")
    ensure_dir(args.output.parent)
    report = audit_deai_contrast_set(
        args.contrast_set,
        output_path=args.output,
        min_anchors=args.min_anchors,
        min_model_families=args.min_model_families,
        min_website_types=args.min_website_types,
        min_fingerprint_field_records=args.min_fingerprint_field_records,
    )
    print(
        f"de-AI contrast audit: passed={report['passed']} "
        f"anchors={report['anchor_count']} issues={report['issue_count']}"
    )
    if not report["passed"]:
        raise SystemExit(1)
