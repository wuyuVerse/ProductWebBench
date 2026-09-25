from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.config import DEFAULT_OUTPUT_ROOT
from ..core.formal_data_guard import assert_not_under_formal_task_root
from ..core.io_utils import ensure_dir, write_json


ARTIFACT_TYPE = "construction_repair_plan"
EVIDENCE_WORKLIST_ARTIFACT_TYPE = "construction_evidence_worklist"
DEFAULT_COVERAGE_REPORT = DEFAULT_OUTPUT_ROOT / "authoring_ledger" / "coverage_gaps.json"
DEFAULT_OUTPUT = DEFAULT_OUTPUT_ROOT / "construction_drafts" / "construction_repair_plan.json"
DEFAULT_EVIDENCE_WORKLIST = DEFAULT_OUTPUT_ROOT / "construction_drafts" / "construction_evidence_worklist.json"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return load_json(path)


def stable_json_sha(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def input_file_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": file_sha256(path),
        "exists": path.exists(),
    }


def construction_fingerprint(status: dict[str, Any]) -> str:
    regimes = status.get("regimes") or {}
    readiness = status.get("readiness_0618") or {}
    phases = readiness.get("phases") or {}
    return stable_json_sha(
        {
            "construction_regime": regimes.get("construction") or {},
            "construction_artifacts": status.get("construction_artifacts") or {},
            "construction_capability_split": status.get("construction_capability_split") or {},
            "construction_mm_judge_spec": status.get("construction_mm_judge_spec") or {},
            "phase1": phases.get("Phase1") or {},
            "phase2": phases.get("Phase2") or {},
        }
    )


def repair_item_from_requirement(requirement: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "repair_index": index,
        "key": requirement.get("key"),
        "label": requirement.get("label"),
        "count": requirement.get("count"),
        "matched_issues": requirement.get("matched_issues", []),
        "required_evidence": requirement.get("required_evidence", []),
        "required_action": requirement.get("action"),
        "allowed_next_artifacts": [
            "non-formal actor input/evidence contract",
            "per-milestone workspace",
            "per-milestone trajectory_report.json",
            "per-milestone capture_report.json",
            "per-milestone verifier_report.json",
            "reference_actor_trace",
            "reference_trajectory_report",
            "construction_trajectory_evidence_report",
            "construction_metareval_report",
            "strict construction_precheck_report",
        ],
        "forbidden_actions": [
            "do not mark a construction draft accepted without strict precheck passing",
            "do not use a reference actor trace template as a passing trace",
            "do not hand-write pass/fail trajectory summaries",
            "do not attach metareval without reference, original, bad-solution, and repeat evidence",
            "do not create formal task rows from this repair plan",
        ],
    }


def path_with_extra_suffix(path: Path, suffix: str) -> Path:
    return path.with_name(path.stem + suffix)


def failed_checks_from_precheck(precheck: dict[str, Any]) -> list[dict[str, Any]]:
    failed: list[dict[str, Any]] = []
    for check in precheck.get("checks", []) or []:
        if isinstance(check, dict) and check.get("passed") is False:
            failed.append(
                {
                    "name": check.get("name"),
                    "details": {key: value for key, value in check.items() if key not in {"name", "passed"}},
                }
            )
    return failed


def draft_repair_item(task_path: Path, index: int) -> dict[str, Any]:
    task = load_json_if_exists(task_path)
    strict_precheck_path = path_with_extra_suffix(task_path, ".precheck.json")
    structural_precheck_path = path_with_extra_suffix(task_path, ".structural_precheck.json")
    score_path = path_with_extra_suffix(task_path, ".score.json")
    reference_template_path = path_with_extra_suffix(task_path, ".reference_actor_trace.template.json")
    evidence_root = task_path.parent / f"{task_path.stem}.evidence"
    spec_root = evidence_root / "spec_only_actor_run"
    repeat_root = evidence_root / "repeat_spec_only_actor_run"
    original_root = evidence_root / "empty_baseline"
    bad_root = evidence_root / "bad_solution"
    spec_run_report = evidence_root / "spec_only_actor_run.trajectory_run_report.json"
    repeat_run_report = evidence_root / "repeat_spec_only_actor_run.trajectory_run_report.json"
    original_run_report = evidence_root / "empty_baseline.trajectory_run_report.json"
    bad_run_report = evidence_root / "bad_solution.trajectory_run_report.json"
    reference_trace = evidence_root / "reference_actor_trace.json"
    reference_manifest = spec_root / "reference_actor_evidence_manifest.json"
    reference_task = task_path.with_name(task_path.stem + ".with_reference_evidence.json")
    metareval_task = task_path.with_name(task_path.stem + ".with_metareval.json")
    reference_evidence = evidence_root / "reference.evidence.json"
    original_evidence = evidence_root / "original.evidence.json"
    bad_evidence = evidence_root / "bad_solution.evidence.json"
    repeat_evidence = evidence_root / "repeat.evidence.json"
    metareval_report = evidence_root / "construction_metareval_report.json"
    strict_precheck = load_json_if_exists(strict_precheck_path)
    structural_precheck = load_json_if_exists(structural_precheck_path)
    blockers = strict_precheck.get("blocking_requirements", []) if strict_precheck else []
    blocker_keys = [item.get("key") for item in blockers if isinstance(item, dict)]
    commands = [
        f"python -m sitecontinuum prepare-construction-evidence-root --task {task_path} --output-root {spec_root}",
        f"python -m sitecontinuum run-construction-actor-loop --task {task_path} --evidence-root {spec_root} --source-kind spec_only_actor_run --actor-command '<actor-command>' --capture-command '<capture-command>' --verifier-command '<verifier-command>' --output {spec_run_report}",
        f"python -m sitecontinuum build-construction-reference-actor-manifest --task {task_path} --evidence-root {spec_root} --output {reference_manifest}",
        f"python -m sitecontinuum build-construction-reference-actor-trace --task {task_path} --manifest {reference_manifest} --output {reference_trace}",
        f"python -m sitecontinuum finalize-construction-reference-evidence --task {task_path} --trace {reference_trace} --output {reference_task}",
        f"python -m sitecontinuum prepare-construction-evidence-root --task {reference_task} --output-root {repeat_root}",
        f"python -m sitecontinuum run-construction-actor-loop --task {reference_task} --evidence-root {repeat_root} --source-kind repeat_spec_only_actor_run --actor-command '<actor-command>' --capture-command '<capture-command>' --verifier-command '<verifier-command>' --output {repeat_run_report}",
        f"python -m sitecontinuum prepare-construction-evidence-root --task {reference_task} --output-root {original_root}",
        f"python -m sitecontinuum run-construction-actor-loop --task {reference_task} --evidence-root {original_root} --source-kind empty_baseline --capture-command '<capture-command>' --verifier-command '<verifier-command>' --output {original_run_report}",
        f"python -m sitecontinuum prepare-construction-evidence-root --task {reference_task} --output-root {bad_root}",
        f"python -m sitecontinuum run-construction-actor-loop --task {reference_task} --evidence-root {bad_root} --source-kind bad_solution --actor-command '<bad-solution-command>' --capture-command '<capture-command>' --verifier-command '<verifier-command>' --output {bad_run_report}",
        f"python -m sitecontinuum build-construction-trajectory-evidence --trajectory {reference_task} --kind reference --task {reference_task} --output {reference_evidence}",
        f"python -m sitecontinuum build-construction-trajectory-evidence --trajectory {original_root / 'trajectory_run.json'} --kind original --task {reference_task} --output {original_evidence}",
        f"python -m sitecontinuum build-construction-trajectory-evidence --trajectory {bad_root / 'trajectory_run.json'} --kind bad_solution --task {reference_task} --output {bad_evidence}",
        f"python -m sitecontinuum build-construction-trajectory-evidence --trajectory {repeat_root / 'trajectory_run.json'} --kind repeat --task {reference_task} --output {repeat_evidence}",
        f"python -m sitecontinuum build-construction-metareval --task {reference_task} --reference {reference_evidence} --original {original_evidence} --bad-solution {bad_evidence} --repeat {repeat_evidence} --output {metareval_report}",
        f"python -m sitecontinuum attach-construction-metareval --task {reference_task} --report {metareval_report} --output {metareval_task}",
        f"python -m sitecontinuum precheck-construction-task --task {metareval_task} --output {strict_precheck_path}",
    ]
    return {
        "repair_index": index,
        "task_path": str(task_path),
        "task_id": task.get("task_id"),
        "repo_id": task.get("repo_id"),
        "formal_task_record": False,
        "strict_precheck_path": str(strict_precheck_path),
        "strict_precheck_available": bool(strict_precheck),
        "strict_precheck_passed": bool(strict_precheck.get("passed")) if strict_precheck else False,
        "strict_precheck_issue_count": int(strict_precheck.get("issue_count") or 0) if strict_precheck else None,
        "structural_precheck_path": str(structural_precheck_path),
        "structural_precheck_available": bool(structural_precheck),
        "structural_precheck_passed": bool(structural_precheck.get("passed")) if structural_precheck else False,
        "score_snapshot_path": str(score_path),
        "score_snapshot_available": score_path.exists(),
        "reference_actor_template_path": str(reference_template_path),
        "reference_actor_template_available": reference_template_path.exists(),
        "recommended_evidence_root": str(evidence_root),
        "expected_evidence_outputs": {
            "spec_only_actor_run": str(spec_run_report),
            "repeat_spec_only_actor_run": str(repeat_run_report),
            "empty_baseline": str(original_run_report),
            "bad_solution": str(bad_run_report),
            "spec_only_trajectory_run": str(spec_root / "trajectory_run.json"),
            "repeat_trajectory_run": str(repeat_root / "trajectory_run.json"),
            "empty_trajectory_run": str(original_root / "trajectory_run.json"),
            "bad_solution_trajectory_run": str(bad_root / "trajectory_run.json"),
            "reference_actor_trace": str(reference_trace),
            "reference_evidence": str(reference_evidence),
            "original_evidence": str(original_evidence),
            "bad_solution_evidence": str(bad_evidence),
            "repeat_evidence": str(repeat_evidence),
            "metareval_report": str(metareval_report),
        },
        "blocking_requirement_keys": blocker_keys,
        "blocking_requirements": blockers,
        "failed_strict_checks": failed_checks_from_precheck(strict_precheck),
        "next_commands": commands,
        "required_next_step": (
            "Run a real spec-only construction actor trajectory for this one draft, then attach completed "
            "reference_actor_trace, reference_trajectory_report, reference/original/bad/repeat evidence, "
            "and construction_metareval before strict acceptance."
        ),
        "forbidden_actions": [
            "do not treat the reference actor trace template as a completed trace",
            "do not attach score snapshots as reference trajectory evidence",
            "do not hand-write passing metareval evidence",
            "do not accept this construction task until strict precheck passes",
        ],
    }


def draft_repair_items_from_status(status: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts = status.get("construction_artifacts") or {}
    items: list[dict[str, Any]] = []
    for index, raw_path in enumerate(artifacts.get("draft_paths", []) or [], start=1):
        if not raw_path:
            continue
        items.append(draft_repair_item(Path(str(raw_path)), index))
    return items


def build_construction_repair_plan(*, coverage_report_path: Path, output_path: Path) -> dict[str, Any]:
    if not coverage_report_path.exists():
        raise FileNotFoundError(f"coverage report missing: {coverage_report_path}")
    status = load_json(coverage_report_path)
    construction = (status.get("regimes") or {}).get("construction") or {}
    artifacts = status.get("construction_artifacts") or {}
    capability = status.get("construction_capability_split") or {}
    mm_spec = status.get("construction_mm_judge_spec") or {}
    readiness = status.get("readiness_0618") or {}
    phases = readiness.get("phases") or {}
    requirements = artifacts.get("strict_blocking_requirements", []) or []
    repair_items = [
        repair_item_from_requirement(requirement, index)
        for index, requirement in enumerate(requirements, start=1)
        if isinstance(requirement, dict)
    ]
    draft_repair_items = draft_repair_items_from_status(status)
    phase1 = phases.get("Phase1") or {}
    phase2 = phases.get("Phase2") or {}
    report = {
        "schema_version": "2026-06-19",
        "artifact_type": ARTIFACT_TYPE,
        "formal_task_record": False,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_coverage_report": str(coverage_report_path),
        "source_fingerprint": construction_fingerprint(status),
        "source_summary": {
            "construction_accepted": construction.get("accepted"),
            "construction_target_total": construction.get("target_total"),
            "draft_count": artifacts.get("draft_count"),
            "strict_precheck_count": artifacts.get("strict_precheck_count"),
            "strict_precheck_passed": artifacts.get("strict_precheck_passed"),
            "structural_precheck_passed": artifacts.get("structural_precheck_passed"),
            "capability_records": capability.get("total"),
            "mm_checkpoints": mm_spec.get("checkpoint_count"),
            "phase1_passed": phase1.get("passed"),
            "phase2_passed": phase2.get("passed"),
        },
        "blocking_requirement_count": len(repair_items),
        "repair_items": repair_items,
        "draft_repair_item_count": len(draft_repair_items),
        "draft_repair_items": draft_repair_items,
        "phase_blockers": {
            "Phase1": phase1.get("issues", []),
            "Phase2": phase2.get("issues", []),
        },
        "global_required_sequence": [
            "Keep the current construction draft non-formal until all strict evidence passes.",
            "Run the spec-only actor loop and collect per-milestone workspace/capture/verifier/trajectory artifacts.",
            "Build a validated reference_actor_trace from the real milestone artifacts.",
            "Build a completed reference_trajectory_report with TCS=1.0, TD=1.0, and ITR=1.0.",
            "Build construction trajectory evidence for reference, original/empty, bad-solution, and repeat runs.",
            "Build and attach a passed construction_metareval_report.",
            "Re-run strict precheck and only then accept the construction task.",
        ],
        "forbidden_actions": [
            "do not use template-only artifacts as passing evidence",
            "do not accept construction tasks from score snapshots",
            "do not use target renders as actor-visible input",
            "do not generate construction tasks in bulk",
            "do not write formal task rows from this repair plan",
        ],
        "notes": [
            "This is a non-formal construction repair plan.",
            "It reads status evidence only and does not run actors, create trajectory evidence, accept tasks, or write ledger rows.",
        ],
    }
    write_json(output_path, report)
    return report


def add_construction_repair_plan_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--coverage-report", type=Path, default=DEFAULT_COVERAGE_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)


def run_construction_repair_plan_from_args(args: argparse.Namespace) -> None:
    try:
        assert_not_under_formal_task_root(args.output, purpose="Construction repair plan")
        ensure_dir(args.output.parent)
        report = build_construction_repair_plan(
            coverage_report_path=args.coverage_report,
            output_path=args.output,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(
        f"construction repair plan: blockers={report['blocking_requirement_count']} "
        f"output={args.output}"
    )


def existing_output_status(outputs: dict[str, Any]) -> dict[str, Any]:
    expected_paths = {
        key: Path(str(value))
        for key, value in outputs.items()
        if isinstance(value, str) and value.strip()
    }
    existing = sorted(key for key, path in expected_paths.items() if path.exists())
    missing = sorted(key for key, path in expected_paths.items() if not path.exists())
    return {
        "expected_count": len(expected_paths),
        "existing_count": len(existing),
        "missing_count": len(missing),
        "existing": existing,
        "missing": missing,
    }


def audit_file_status(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "path": str(path),
            "available": False,
            "passed": False,
            "issue_count": None,
            "issues": [],
        }
    payload = load_json_if_exists(path)
    issues = payload.get("issues", []) if isinstance(payload.get("issues"), list) else []
    return {
        "path": str(path),
        "available": True,
        "passed": bool(payload.get("passed")),
        "issue_count": int(payload.get("issue_count") or len(issues) or 0),
        "issues": issues[:20],
    }


def construction_evidence_work_item(item: dict[str, Any], index: int, repair_plan_path: Path) -> dict[str, Any]:
    task_path = Path(str(item.get("task_path") or ""))
    evidence_root = Path(str(item.get("recommended_evidence_root") or task_path.with_suffix(".evidence")))
    spec_root = evidence_root / "spec_only_actor_run"
    repeat_root = evidence_root / "repeat_spec_only_actor_run"
    empty_root = evidence_root / "empty_baseline"
    bad_root = evidence_root / "bad_solution"
    scaffold_audit = spec_root / "scaffold_audit.json"
    complete_audit = spec_root / "complete_audit.json"
    manifest = spec_root / "reference_actor_evidence_manifest.json"
    trace = evidence_root / "reference_actor_trace.json"
    reference_task = task_path.with_name(f"{task_path.stem}.with_reference_evidence.json")
    metareval_task = task_path.with_name(f"{task_path.stem}.with_metareval.json")
    strict_precheck_path = task_path.with_name(f"{task_path.stem}.precheck.json")
    acceptance_precheck_path = task_path.with_name(f"{task_path.stem}.accepted_precheck.json")
    expected_outputs = item.get("expected_evidence_outputs", {}) if isinstance(item.get("expected_evidence_outputs"), dict) else {}
    expected_outputs = {
        **expected_outputs,
        "spec_only_trajectory_run": str(spec_root / "trajectory_run.json"),
        "repeat_trajectory_run": str(repeat_root / "trajectory_run.json"),
        "empty_trajectory_run": str(empty_root / "trajectory_run.json"),
        "bad_solution_trajectory_run": str(bad_root / "trajectory_run.json"),
    }
    output_status = existing_output_status(expected_outputs)
    blockers = sorted(str(key) for key in item.get("blocking_requirement_keys", []) or [])
    commands = [
        (
            "python -m sitecontinuum prepare-construction-evidence-root "
            f"--task {task_path} --output-root {spec_root}"
        ),
        (
            "python -m sitecontinuum audit-construction-evidence-root "
            f"--task {task_path} --evidence-root {spec_root} --output {scaffold_audit}"
        ),
        (
            "python -m sitecontinuum run-construction-actor-loop "
            f"--task {task_path} --evidence-root {spec_root} --source-kind spec_only_actor_run "
            "--actor-command '<spec-only-actor-command>' "
            "--capture-command '<capture-command-writing-$SITECONTINUUM_CAPTURE_REPORT>' "
            "--verifier-command '<verifier-command-writing-$SITECONTINUUM_VERIFIER_REPORT>' "
            f"--output {expected_outputs.get('spec_only_actor_run', evidence_root / 'spec_only_actor_run.trajectory_run_report.json')}"
        ),
        (
            "python -m sitecontinuum audit-construction-evidence-root "
            f"--task {task_path} --evidence-root {spec_root} --require-complete --output {complete_audit}"
        ),
        (
            "python -m sitecontinuum build-construction-reference-actor-manifest "
            f"--task {task_path} --evidence-root {spec_root} --output {manifest}"
        ),
        (
            "python -m sitecontinuum build-construction-reference-actor-trace "
            f"--task {task_path} --manifest {manifest} --output {trace}"
        ),
        (
            "python -m sitecontinuum finalize-construction-reference-evidence "
            f"--task {task_path} --trace {trace} --output {reference_task}"
        ),
        (
            "python -m sitecontinuum prepare-construction-evidence-root "
            f"--task {reference_task} --output-root {repeat_root}"
        ),
        (
            "python -m sitecontinuum run-construction-actor-loop "
            f"--task {reference_task} --evidence-root {repeat_root} --source-kind repeat_spec_only_actor_run "
            "--actor-command '<same-spec-only-actor-command>' "
            "--capture-command '<capture-command-writing-$SITECONTINUUM_CAPTURE_REPORT>' "
            "--verifier-command '<verifier-command-writing-$SITECONTINUUM_VERIFIER_REPORT>' "
            f"--output {expected_outputs.get('repeat_spec_only_actor_run', evidence_root / 'repeat_spec_only_actor_run.trajectory_run_report.json')}"
        ),
        (
            "python -m sitecontinuum prepare-construction-evidence-root "
            f"--task {reference_task} --output-root {empty_root}"
        ),
        (
            "python -m sitecontinuum run-construction-actor-loop "
            f"--task {reference_task} --evidence-root {empty_root} --source-kind empty_baseline "
            "--capture-command '<capture-command-writing-$SITECONTINUUM_CAPTURE_REPORT>' "
            "--verifier-command '<verifier-command-writing-$SITECONTINUUM_VERIFIER_REPORT>' "
            f"--output {expected_outputs.get('empty_baseline', evidence_root / 'empty_baseline.trajectory_run_report.json')}"
        ),
        (
            "python -m sitecontinuum prepare-construction-evidence-root "
            f"--task {reference_task} --output-root {bad_root}"
        ),
        (
            "python -m sitecontinuum run-construction-actor-loop "
            f"--task {reference_task} --evidence-root {bad_root} --source-kind bad_solution "
            "--actor-command '<bad-solution-command>' "
            "--capture-command '<capture-command-writing-$SITECONTINUUM_CAPTURE_REPORT>' "
            "--verifier-command '<verifier-command-writing-$SITECONTINUUM_VERIFIER_REPORT>' "
            f"--output {expected_outputs.get('bad_solution', evidence_root / 'bad_solution.trajectory_run_report.json')}"
        ),
        (
            "python -m sitecontinuum build-construction-trajectory-evidence "
            f"--trajectory {reference_task} --kind reference --task {reference_task} "
            f"--output {expected_outputs.get('reference_evidence', evidence_root / 'reference.evidence.json')}"
        ),
        (
            "python -m sitecontinuum build-construction-trajectory-evidence "
            f"--trajectory {empty_root / 'trajectory_run.json'} "
            f"--kind original --task {reference_task} "
            f"--output {expected_outputs.get('original_evidence', evidence_root / 'original.evidence.json')}"
        ),
        (
            "python -m sitecontinuum build-construction-trajectory-evidence "
            f"--trajectory {bad_root / 'trajectory_run.json'} "
            f"--kind bad_solution --task {reference_task} "
            f"--output {expected_outputs.get('bad_solution_evidence', evidence_root / 'bad_solution.evidence.json')}"
        ),
        (
            "python -m sitecontinuum build-construction-trajectory-evidence "
            f"--trajectory {repeat_root / 'trajectory_run.json'} "
            f"--kind repeat --task {reference_task} "
            f"--output {expected_outputs.get('repeat_evidence', evidence_root / 'repeat.evidence.json')}"
        ),
        (
            "python -m sitecontinuum build-construction-metareval "
            f"--task {reference_task} "
            f"--reference {expected_outputs.get('reference_evidence', evidence_root / 'reference.evidence.json')} "
            f"--original {expected_outputs.get('original_evidence', evidence_root / 'original.evidence.json')} "
            f"--bad-solution {expected_outputs.get('bad_solution_evidence', evidence_root / 'bad_solution.evidence.json')} "
            f"--repeat {expected_outputs.get('repeat_evidence', evidence_root / 'repeat.evidence.json')} "
            f"--output {expected_outputs.get('metareval_report', evidence_root / 'construction_metareval_report.json')}"
        ),
        (
            "python -m sitecontinuum attach-construction-metareval "
            f"--task {reference_task} "
            f"--report {expected_outputs.get('metareval_report', evidence_root / 'construction_metareval_report.json')} "
            f"--output {metareval_task}"
        ),
        (
            "python -m sitecontinuum precheck-construction-task "
            f"--task {metareval_task} --output {strict_precheck_path}"
        ),
        (
            "# accept only after the strict precheck passes: "
            "python -m sitecontinuum accept-construction-task "
            f"--task {metareval_task} --precheck {strict_precheck_path} --write-precheck {acceptance_precheck_path}"
        ),
    ]
    return {
        "work_index": index,
        "repair_index": item.get("repair_index"),
        "task_id": item.get("task_id"),
        "repo_id": item.get("repo_id"),
        "task_path": str(task_path),
        "formal_task_record": False,
        "blocking_requirement_keys": blockers,
        "strict_precheck_passed": item.get("strict_precheck_passed"),
        "structural_precheck_passed": item.get("structural_precheck_passed"),
        "reference_actor_template_available": item.get("reference_actor_template_available"),
        "evidence_root": str(evidence_root),
        "run_evidence_roots": {
            "spec_only_actor_run": str(spec_root),
            "repeat_spec_only_actor_run": str(repeat_root),
            "empty_baseline": str(empty_root),
            "bad_solution": str(bad_root),
        },
        "scaffold_exists": evidence_root.exists(),
        "spec_only_scaffold_audit": audit_file_status(scaffold_audit),
        "spec_only_complete_audit": audit_file_status(complete_audit),
        "expected_output_status": output_status,
        "next_commands": commands,
        "required_real_evidence": [
            "one real spec-only actor trajectory, using actor-visible milestone inputs only",
            "per-milestone workspace/capture/verifier/trajectory artifacts",
            "one repeat spec-only trajectory for reproducibility",
            "one empty/original baseline trajectory that fails the target construction task",
            "one bad-solution trajectory that fails the relevant checks",
            "construction_metareval_report binding all four evidence reports",
        ],
        "forbidden_actions": [
            "do not copy target screenshots, target text, source code, or target render crops into actor input",
            "do not treat scaffold/template files as passing traces",
            "do not hand-write passing trajectory, trace, or metareval reports",
            "do not accept a construction task until strict precheck passes on the final attached task file",
            "do not create or edit data/sitecontinuum/tasks/slot_*/task.jsonl from this worklist",
        ],
    }


def build_construction_evidence_worklist(
    *,
    repair_plan_path: Path,
    output_path: Path,
    max_items: int | None = None,
) -> dict[str, Any]:
    if not repair_plan_path.exists():
        raise FileNotFoundError(f"construction repair plan missing: {repair_plan_path}")
    plan = load_json(repair_plan_path)
    issues: list[str] = []
    if plan.get("artifact_type") != ARTIFACT_TYPE:
        issues.append(f"construction repair plan has wrong artifact_type: {plan.get('artifact_type')}")
    if plan.get("formal_task_record") is not False:
        issues.append("construction repair plan must be formal_task_record=false")
    draft_items = [item for item in plan.get("draft_repair_items", []) if isinstance(item, dict)]
    if max_items is not None:
        draft_items = draft_items[:max_items]
    work_items = [
        construction_evidence_work_item(item, index, repair_plan_path)
        for index, item in enumerate(draft_items, start=1)
    ]
    blocker_counts = Counter(
        blocker
        for item in work_items
        for blocker in item.get("blocking_requirement_keys", [])
    )
    missing_output_counts = Counter(
        missing
        for item in work_items
        for missing in item.get("expected_output_status", {}).get("missing", [])
    )
    scaffold_exists_count = sum(1 for item in work_items if item.get("scaffold_exists"))
    scaffold_audit_passed_count = sum(
        1 for item in work_items if item.get("spec_only_scaffold_audit", {}).get("passed")
    )
    complete_audit_passed_count = sum(
        1 for item in work_items if item.get("spec_only_complete_audit", {}).get("passed")
    )
    complete_audit_issue_count = sum(
        int(item.get("spec_only_complete_audit", {}).get("issue_count") or 0)
        for item in work_items
        if item.get("spec_only_complete_audit", {}).get("available")
    )
    report = {
        "schema_version": "2026-06-19",
        "artifact_type": EVIDENCE_WORKLIST_ARTIFACT_TYPE,
        "formal_task_record": False,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_repair_plan": str(repair_plan_path),
        "output_path": str(output_path),
        "input_files": {
            "repair_plan": input_file_record(repair_plan_path),
        },
        "passed": not issues,
        "issue_count": len(issues),
        "issues": issues,
        "source_blocking_requirement_count": int(plan.get("blocking_requirement_count") or 0),
        "source_draft_repair_item_count": int(plan.get("draft_repair_item_count") or 0),
        "work_item_count": len(work_items),
        "max_items": max_items,
        "blocker_counts": dict(sorted(blocker_counts.items())),
        "scaffold_exists_count": scaffold_exists_count,
        "scaffold_audit_passed_count": scaffold_audit_passed_count,
        "complete_audit_passed_count": complete_audit_passed_count,
        "complete_audit_issue_count": complete_audit_issue_count,
        "missing_expected_output_counts": dict(sorted(missing_output_counts.items())),
        "work_items": work_items,
        "gate": (
            "This worklist only schedules one-draft construction evidence runs. It does not run actors, "
            "write passing evidence, accept construction tasks, or create formal task rows."
        ),
    }
    write_json(output_path, report)
    return report


def add_construction_evidence_worklist_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repair-plan", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_EVIDENCE_WORKLIST)
    parser.add_argument("--max-items", type=int, default=None)


def run_construction_evidence_worklist_from_args(args: argparse.Namespace) -> None:
    try:
        assert_not_under_formal_task_root(args.output, purpose="Construction evidence worklist")
        ensure_dir(args.output.parent)
        report = build_construction_evidence_worklist(
            repair_plan_path=args.repair_plan,
            output_path=args.output,
            max_items=args.max_items,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(
        "construction evidence worklist: "
        f"passed={report['passed']} work_items={report['work_item_count']} "
        f"issues={report['issue_count']} output={args.output}"
    )
    if not report["passed"]:
        raise SystemExit(1)
