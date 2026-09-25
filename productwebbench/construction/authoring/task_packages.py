from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ...core.config import DEFAULT_OUTPUT_ROOT
from ...core.io_utils import ensure_dir, read_jsonl, write_json


DEFAULT_PACKAGE_ROOT = DEFAULT_OUTPUT_ROOT / "bench" / "dev"
REQUIRED_PACKAGE_FILES = [
    "README.md",
    "task.json",
    "reference_verifier.json",
    "submission_verifier.json",
    "design_anchors.summary.json",
    "provenance.json",
    "evidence.json",
    "rationale.json",
    "asset_gallery.json",
    "reference_artifacts.json",
]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def keyed(items: list[dict[str, Any]], key: str = "task_id") -> dict[str, dict[str, Any]]:
    return {item[key]: item for item in items}


def load_spec_map(path: Path, container_key: str = "tasks") -> dict[str, dict[str, Any]]:
    data = load_json(path)
    return keyed(data.get(container_key, []))


def load_item_map(path: Path, container_key: str = "items") -> dict[str, dict[str, Any]]:
    data = load_json(path)
    return keyed(data.get(container_key, []))


def compact_anchor(anchor: dict[str, Any] | None) -> dict[str, Any] | None:
    if anchor is None:
        return None
    tokens = anchor.get("tokens", {})
    samples = anchor.get("samples", {})
    return {
        "task_id": anchor["task_id"],
        "repo_id": anchor["repo_id"],
        "has_design_anchors": anchor.get("has_design_anchors", False),
        "missing_design_states": anchor.get("missing_design_states", []),
        "state_ids": anchor.get("state_ids", []),
        "top_tokens": {
            "background_colors": tokens.get("background_colors", [])[:8],
            "text_colors": tokens.get("text_colors", [])[:8],
            "font_families": tokens.get("font_families", [])[:6],
            "font_sizes": tokens.get("font_sizes", [])[:8],
            "font_weights": tokens.get("font_weights", [])[:8],
            "radii": tokens.get("radii", [])[:8],
            "shadows": tokens.get("shadows", [])[:6],
        },
        "sample_counts": {name: len(value) for name, value in samples.items()},
        "screenshots": anchor.get("screenshots", []),
        "component_crops": anchor.get("component_crops", []),
        "state_summaries": anchor.get("state_summaries", []),
    }


def state_artifact_paths(repo_id: str, state_ids: list[str], states_root: Path, package_root: Path) -> list[dict[str, Any]]:
    artifacts = []
    for state_id in state_ids:
        state_dir = states_root / repo_id / state_id
        artifacts.append(
            {
                "state_id": state_id,
                "screenshot": relative_or_absolute(state_dir / "screenshot.png", package_root),
                "metrics": relative_or_absolute(state_dir / "metrics.json", package_root),
                "quality": relative_or_absolute(state_dir / "quality.json", package_root),
                "dom": relative_or_absolute(state_dir / "dom.html", package_root),
                "a11y": relative_or_absolute(state_dir / "a11y.json", package_root),
                "boxes": relative_or_absolute(state_dir / "boxes.json", package_root),
                "console": relative_or_absolute(state_dir / "console.json", package_root),
                "actions": relative_or_absolute(state_dir / "actions.json", package_root),
                "crops": relative_or_absolute(state_dir / "crops.json", package_root),
            }
        )
    return artifacts


def relative_or_absolute(path: Path, start: Path) -> str:
    try:
        return str(path.resolve().relative_to(start.resolve()))
    except ValueError:
        return str(path.resolve())


def markdown_list(items: list[str]) -> str:
    if not items:
        return "- None"
    return "\n".join(f"- {item}" for item in items)


def rubric_markdown(rubric: dict[str, list[str]]) -> str:
    chunks = []
    for name, items in rubric.items():
        chunks.append(f"### {name.replace('_', ' ').title()}\n\n{markdown_list(items)}")
    return "\n\n".join(chunks)


def provenance_markdown(provenance: dict[str, Any] | None) -> str:
    if not provenance:
        return "No repo provenance available."

    source_matches = []
    for item in provenance.get("source_patterns", []):
        for match in item.get("matches", [])[:2]:
            source_matches.append(f"`{match}`")
        if len(source_matches) >= 5:
            break

    asset_matches = []
    for item in provenance.get("asset_patterns", []):
        for match in item.get("matches", [])[:2]:
            asset_matches.append(f"`{match}`")
        if len(asset_matches) >= 5:
            break

    code_evidence = []
    for item in provenance.get("source_text_evidence", []):
        for match in item.get("matches", [])[:1]:
            code_evidence.append(f"`{item['keyword']}` -> `{match['path']}:{match['line']}`")
        if len(code_evidence) >= 5:
            break

    browser_evidence = []
    for item in provenance.get("browser_text_evidence", [])[:3]:
        signals = ", ".join(item.get("found_signals", []))
        browser_evidence.append(f"`{item['state_id']}` contains {signals}")

    workspace = provenance.get("workspace", {})
    return "\n\n".join(
        [
            f"- Project root: `{workspace.get('project_root', 'unknown')}`",
            f"- Zip source: `{workspace.get('zip_path', 'unknown')}`",
            "Source file evidence:\n" + markdown_list(source_matches),
            "Asset/component evidence:\n" + markdown_list(asset_matches),
            "Code keyword evidence:\n" + markdown_list(code_evidence),
            "Browser-state text evidence:\n" + markdown_list(browser_evidence),
        ]
    )


def rationale_markdown(rationale: dict[str, Any] | None) -> str:
    if not rationale:
        return "No construction rationale available."
    sections = [
        ("Why This Repo", rationale.get("why_this_repo", "")),
        ("Natural Change Location", rationale.get("natural_change_location", "")),
        ("Asset Grounding", rationale.get("asset_grounding", "")),
        ("Design Integration", rationale.get("design_integration", "")),
        ("State Coverage", rationale.get("state_coverage", "")),
    ]
    blocks = [f"### {title}\n\n{text}" for title, text in sections]
    blocks.append("### Repo Evidence\n\n" + markdown_list(rationale.get("repo_evidence", [])))
    blocks.append("### Anti-Shortcut Checks\n\n" + markdown_list(rationale.get("anti_shortcut_checks", [])))
    blocks.append("### Verifier Alignment\n\n" + markdown_list(rationale.get("verifier_alignment", [])))
    blocks.append("### Agent Audit Notes\n\n" + markdown_list(rationale.get("agent_audit_notes", [])))
    return "\n\n".join(blocks)


def asset_gallery_markdown(gallery: dict[str, Any] | None) -> str:
    if not gallery:
        return "No asset gallery available."
    lines = [
        f"- Selected assets: `{gallery.get('total_selected', 0)}`",
        f"- Asset kinds: `{json.dumps(gallery.get('kind_counts', {}), sort_keys=True)}`",
    ]
    preview_items = gallery.get("items", [])[:10]
    for item in preview_items:
        tags = ", ".join(item.get("semantic_tags", [])) or "-"
        lines.append(f"- `{item['repo_path']}` ({item['kind']}, tags: {tags})")
    return "\n".join(lines)


def build_task_readme(
    task: dict[str, Any],
    reference_spec: dict[str, Any] | None,
    submission_spec: dict[str, Any] | None,
    anchor: dict[str, Any] | None,
    provenance: dict[str, Any] | None,
    evidence: dict[str, Any] | None,
    rationale: dict[str, Any] | None,
    asset_gallery: dict[str, Any] | None,
) -> str:
    reference_states = reference_spec.get("reference_states", []) if reference_spec else []
    submission_states = submission_spec.get("submission_states", []) if submission_spec else []
    completion_signals = submission_spec.get("completion_text_signals", []) if submission_spec else []
    visual_states = submission_spec.get("visual_anchor_states", []) if submission_spec else []
    asset_signals = submission_spec.get("asset_path_signals", []) if submission_spec else []
    image_alt_signals = submission_spec.get("image_alt_signals", []) if submission_spec else []
    anchor_status = "available" if anchor and anchor.get("has_design_anchors") else "missing"
    provenance_status = "repo-grounded" if provenance and provenance.get("passed") else "missing-or-failed"
    evidence_status = "quality-pass" if evidence and evidence.get("quality_pass") else "missing-or-failed"
    statement = task.get("problem_statement", "")

    return f"""# {task['task_id']}

## Repo

- Repo id: `{task['repo_id']}`
- Split: `{task['split']}`
- Intent: `{task['intent']}`
- Scope: `{task['scope']}`
- Difficulty: `{task['difficulty']}`
- Evidence: `{evidence_status}`
- Design anchors: `{anchor_status}`
- Repo provenance: `{provenance_status}`

## Problem Statement

{statement}

## Required Content

{markdown_list(task.get('required_content', []))}

## Design Constraints

{markdown_list(task.get('design_constraints', []))}

## State Constraints

{markdown_list(task.get('state_constraints', []))}

## Assets To Consider

{markdown_list(task.get('assets_to_consider', []))}

## Suggested Files

{markdown_list(task.get('suggested_files', []))}

## Reference States

{markdown_list(reference_states)}

## Submission States

{markdown_list(submission_states)}

## Machine-Checkable Completion Signals

{markdown_list(completion_signals)}

## Visual Anchor States

{markdown_list(visual_states)}

## Asset Path Signals

{markdown_list(asset_signals)}

## Image Alt Signals

{markdown_list(image_alt_signals)}

## Evidence From Repo

{provenance_markdown(provenance)}

## Asset Gallery

{asset_gallery_markdown(asset_gallery)}

## Construction Rationale

{rationale_markdown(rationale)}

## Evaluation Rubric

{rubric_markdown(task.get('evaluation_rubric', {}))}

## Author Notes

{task.get('author_notes', '')}
"""


def write_task_package(
    task: dict[str, Any],
    package_root: Path,
    states_root: Path,
    reference_spec: dict[str, Any] | None,
    submission_spec: dict[str, Any] | None,
    anchor: dict[str, Any] | None,
    provenance: dict[str, Any] | None,
    evidence: dict[str, Any] | None,
    rationale: dict[str, Any] | None,
    asset_gallery: dict[str, Any] | None,
) -> dict[str, Any]:
    task_dir = ensure_dir(package_root / task["task_id"])
    reference_states = reference_spec.get("reference_states", []) if reference_spec else []
    submission_states = submission_spec.get("submission_states", []) if submission_spec else []
    all_states = sorted(set(reference_states) | set(submission_states))

    write_json(task_dir / "task.json", task)
    write_json(task_dir / "reference_verifier.json", reference_spec or {"task_id": task["task_id"], "missing": True})
    write_json(task_dir / "submission_verifier.json", submission_spec or {"task_id": task["task_id"], "missing": True})
    write_json(task_dir / "design_anchors.summary.json", compact_anchor(anchor))
    write_json(task_dir / "provenance.json", provenance or {"task_id": task["task_id"], "missing": True})
    write_json(task_dir / "evidence.json", evidence or {"task_id": task["task_id"], "missing": True})
    write_json(task_dir / "rationale.json", rationale or {"task_id": task["task_id"], "missing": True})
    write_json(task_dir / "asset_gallery.json", asset_gallery or {"repo_id": task["repo_id"], "missing": True})
    write_json(
        task_dir / "reference_artifacts.json",
        {
            "task_id": task["task_id"],
            "repo_id": task["repo_id"],
            "states_root": str(states_root),
            "states": state_artifact_paths(task["repo_id"], all_states, states_root, task_dir),
        },
    )
    (task_dir / "README.md").write_text(
        build_task_readme(task, reference_spec, submission_spec, anchor, provenance, evidence, rationale, asset_gallery),
        encoding="utf-8",
    )

    return {
        "task_id": task["task_id"],
        "repo_id": task["repo_id"],
        "package_dir": str(task_dir),
        "has_reference_spec": reference_spec is not None,
        "has_submission_spec": submission_spec is not None,
        "has_design_anchors": bool(anchor and anchor.get("has_design_anchors")),
        "has_repo_provenance": bool(provenance and provenance.get("passed")),
        "has_quality_evidence": bool(evidence and evidence.get("quality_pass")),
        "has_construction_rationale": bool(rationale and not rationale.get("missing")),
        "has_asset_gallery": bool(asset_gallery and asset_gallery.get("passed")),
        "reference_state_count": len(reference_states),
        "submission_state_count": len(submission_states),
    }


def export_task_packages(
    tasks_path: Path,
    reference_specs_path: Path,
    submission_specs_path: Path,
    design_anchors_path: Path,
    provenance_path: Path,
    evidence_path: Path,
    rationales_path: Path,
    asset_galleries_root: Path,
    states_root: Path,
    output_root: Path,
    clean: bool,
) -> dict[str, Any]:
    if clean and output_root.exists():
        for child in output_root.iterdir():
            if child.is_dir():
                for nested in sorted(child.rglob("*"), reverse=True):
                    if nested.is_file() or nested.is_symlink():
                        nested.unlink()
                    elif nested.is_dir():
                        nested.rmdir()
                child.rmdir()
            else:
                child.unlink()
    ensure_dir(output_root)

    tasks = list(read_jsonl(tasks_path))
    reference_specs = load_spec_map(reference_specs_path)
    submission_specs = load_spec_map(submission_specs_path)
    anchors = load_item_map(design_anchors_path)
    provenance_items = load_item_map(provenance_path)
    evidence_items = load_item_map(evidence_path)
    rationale_items = load_item_map(rationales_path) if rationales_path.exists() else {}
    asset_galleries = {}
    if asset_galleries_root.exists():
        for gallery_path in asset_galleries_root.glob("*/gallery.json"):
            gallery = load_json(gallery_path)
            asset_galleries[gallery["repo_id"]] = gallery

    packages = []
    for task in tasks:
        task_id = task["task_id"]
        packages.append(
            write_task_package(
                task=task,
                package_root=output_root,
                states_root=states_root,
                reference_spec=reference_specs.get(task_id),
                submission_spec=submission_specs.get(task_id),
                anchor=anchors.get(task_id),
                provenance=provenance_items.get(task_id),
                evidence=evidence_items.get(task_id),
                rationale=rationale_items.get(task_id),
                asset_gallery=asset_galleries.get(task["repo_id"]),
            )
        )

    manifest = {
        "tasks_path": str(tasks_path),
        "reference_specs_path": str(reference_specs_path),
        "submission_specs_path": str(submission_specs_path),
        "design_anchors_path": str(design_anchors_path),
        "provenance_path": str(provenance_path),
        "evidence_path": str(evidence_path),
        "rationales_path": str(rationales_path),
        "asset_galleries_root": str(asset_galleries_root),
        "states_root": str(states_root),
        "output_root": str(output_root),
        "total": len(packages),
        "with_reference_specs": sum(1 for item in packages if item["has_reference_spec"]),
        "with_submission_specs": sum(1 for item in packages if item["has_submission_spec"]),
        "with_design_anchors": sum(1 for item in packages if item["has_design_anchors"]),
        "with_repo_provenance": sum(1 for item in packages if item["has_repo_provenance"]),
        "with_quality_evidence": sum(1 for item in packages if item["has_quality_evidence"]),
        "with_construction_rationales": sum(1 for item in packages if item["has_construction_rationale"]),
        "with_asset_galleries": sum(1 for item in packages if item["has_asset_gallery"]),
        "packages": packages,
    }
    write_json(output_root / "manifest.json", manifest)
    return manifest


def path_from_artifact(value: str, task_dir: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return task_dir / path


def validate_one_package(task_dir: Path) -> dict[str, Any]:
    missing_files = [name for name in REQUIRED_PACKAGE_FILES if not (task_dir / name).exists()]
    json_errors = []
    for name in REQUIRED_PACKAGE_FILES:
        if not name.endswith(".json"):
            continue
        path = task_dir / name
        if not path.exists():
            continue
        try:
            load_json(path)
        except json.JSONDecodeError as exc:
            json_errors.append({"file": name, "error": str(exc)})

    missing_artifacts = []
    reference_artifacts_path = task_dir / "reference_artifacts.json"
    if reference_artifacts_path.exists() and not json_errors:
        artifacts = load_json(reference_artifacts_path)
        for state in artifacts.get("states", []):
            for key in ("screenshot", "metrics", "quality", "dom", "a11y", "boxes", "console", "actions", "crops"):
                artifact_path = path_from_artifact(state.get(key, ""), task_dir)
                if not artifact_path.exists():
                    missing_artifacts.append({"state_id": state.get("state_id"), "kind": key, "path": str(artifact_path)})

    readme_text = (task_dir / "README.md").read_text(encoding="utf-8") if (task_dir / "README.md").exists() else ""
    readme_sections = [
        "## Problem Statement",
        "## Required Content",
        "## Design Constraints",
        "## Machine-Checkable Completion Signals",
        "## Evidence From Repo",
        "## Asset Gallery",
        "## Construction Rationale",
        "## Evaluation Rubric",
    ]
    missing_readme_sections = [section for section in readme_sections if section not in readme_text]
    provenance_errors = []
    provenance_path = task_dir / "provenance.json"
    if provenance_path.exists():
        provenance = load_json(provenance_path)
        if not provenance.get("passed"):
            provenance_errors.append("provenance is not passed")
    asset_gallery_errors = []
    asset_gallery_path = task_dir / "asset_gallery.json"
    if asset_gallery_path.exists():
        gallery = load_json(asset_gallery_path)
        if not gallery.get("passed"):
            asset_gallery_errors.append("asset gallery is not passed")

    passed = (
        not missing_files
        and not json_errors
        and not missing_artifacts
        and not missing_readme_sections
        and not provenance_errors
        and not asset_gallery_errors
    )
    return {
        "task_dir": str(task_dir),
        "task_id": task_dir.name,
        "passed": passed,
        "missing_files": missing_files,
        "json_errors": json_errors,
        "missing_artifacts": missing_artifacts,
        "missing_readme_sections": missing_readme_sections,
        "provenance_errors": provenance_errors,
        "asset_gallery_errors": asset_gallery_errors,
    }


def validate_task_packages(package_root: Path, output_path: Path) -> dict[str, Any]:
    manifest_path = package_root / "manifest.json"
    package_dirs: list[Path]
    if manifest_path.exists():
        manifest = load_json(manifest_path)
        package_dirs = [Path(item["package_dir"]) for item in manifest.get("packages", [])]
    else:
        package_dirs = sorted(path for path in package_root.iterdir() if path.is_dir())

    results = [validate_one_package(task_dir) for task_dir in package_dirs]
    summary = {
        "package_root": str(package_root),
        "total": len(results),
        "passed": sum(1 for result in results if result["passed"]),
        "failed": sum(1 for result in results if not result["passed"]),
        "results": results,
    }
    write_json(output_path, summary)
    return summary


def add_export_args(parser: argparse.ArgumentParser) -> None:
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
    parser.add_argument("--provenance", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.provenance.json")
    parser.add_argument("--evidence", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.evidence.json")
    parser.add_argument("--rationales", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.rationales.json")
    parser.add_argument("--asset-galleries-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "asset_galleries" / "dev_seed")
    parser.add_argument("--states-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "states")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_PACKAGE_ROOT)
    parser.add_argument("--clean", action="store_true")


def run_export_from_args(args: argparse.Namespace) -> None:
    manifest = export_task_packages(
        tasks_path=args.tasks,
        reference_specs_path=args.reference_specs,
        submission_specs_path=args.submission_specs,
        design_anchors_path=args.design_anchors,
        provenance_path=args.provenance,
        evidence_path=args.evidence,
        rationales_path=args.rationales,
        asset_galleries_root=args.asset_galleries_root,
        states_root=args.states_root,
        output_root=args.output_root,
        clean=args.clean,
    )
    print(
        f"exported {manifest['total']} task packages: "
        f"{manifest['with_design_anchors']} with anchors, "
            f"{manifest['with_repo_provenance']} with provenance, "
            f"{manifest['with_quality_evidence']} with quality evidence, "
            f"{manifest['with_construction_rationales']} with rationales, "
            f"{manifest['with_asset_galleries']} with asset galleries"
    )


def add_validate_package_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--package-root", type=Path, default=DEFAULT_PACKAGE_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_PACKAGE_ROOT / "validation.json")


def run_validate_package_from_args(args: argparse.Namespace) -> None:
    summary = validate_task_packages(args.package_root, args.output)
    print(f"validated {summary['total']} task packages: {summary['passed']} passed, {summary['failed']} failed")
