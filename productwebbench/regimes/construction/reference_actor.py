from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...core.config import DEFAULT_OUTPUT_ROOT
from ...core.formal_data_guard import assert_not_under_formal_task_root
from ...core.io_utils import ensure_dir, write_json
from ...taxonomy.capability import L_HARD, L_METRIC
from .task_digest import stable_task_digest


TRACE_SCHEMA_VERSION = "2026-06-18"
TRACE_ARTIFACT_TYPE = "reference_actor_trace"
MILESTONE_RUN_ARTIFACT_TYPE = "construction_milestone_run_report"
CAPTURE_REPORT_ARTIFACT_TYPE = "construction_milestone_capture_report"
VERIFIER_REPORT_ARTIFACT_TYPE = "construction_milestone_verifier_report"
SPEC_ONLY_SOURCE_KIND = "spec_only_actor_run"
REQUIRED_ARTIFACT_REF_KEYS = (
    "workspace",
    "trajectory_report",
    "capture_report",
    "verifier_report",
)
SIGNAL_REPORT_ARTIFACT_TYPES = {
    "capture_report": CAPTURE_REPORT_ARTIFACT_TYPE,
    "verifier_report": VERIFIER_REPORT_ARTIFACT_TYPE,
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def milestone_ids(task: dict[str, Any]) -> list[str]:
    return [str(item.get("milestone_id", "")) for item in task.get("milestones", []) if isinstance(item, dict)]


def trace_milestones(trace: dict[str, Any]) -> list[dict[str, Any]]:
    items = trace.get("milestones", [])
    return [item for item in items if isinstance(item, dict)]


def boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "pass", "passed"}
    return bool(value)


def candidate_artifact_paths(raw_path: str, artifact_root: Path | None) -> list[Path]:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return [path]

    roots = [root for root in (artifact_root, Path.cwd()) if root is not None]
    candidates: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        candidate = root / path
        key = str(candidate)
        if key not in seen:
            candidates.append(candidate)
            seen.add(key)
    return candidates


def existing_artifact_path(raw_path: str, artifact_root: Path | None) -> Path | None:
    return next((candidate for candidate in candidate_artifact_paths(raw_path, artifact_root) if candidate.exists()), None)


def relative_ref(path: Path, output_dir: Path) -> str:
    try:
        return str(path.resolve().relative_to(output_dir.resolve()))
    except ValueError:
        return os.path.relpath(path.resolve(), output_dir.resolve())


def default_actor_input_policy() -> dict[str, Any]:
    return {
        "used_source_code": False,
        "used_source_text": False,
        "used_target_screenshots": False,
        "used_target_render_crops": False,
        "used_exact_target_text": False,
        "actor_visible_inputs": [
            "lm_task.instructions",
            "milestones[].actor_spec",
            "milestones[].checkpoints[].assert_text",
        ],
    }


def report_status_passed(report: dict[str, Any]) -> bool:
    if "passed" in report:
        return boolish(report.get("passed"))
    if str(report.get("status", "")).lower() in {"passed", "pass", "ok"}:
        return True
    capture = report.get("capture", {})
    if isinstance(capture, dict) and capture.get("exit_code") == 0 and not capture.get("timed_out") and report.get("failure_stage") in {None, ""}:
        return True
    if report.get("quality_pass") is True and report.get("failure_stage") in {None, ""}:
        return True
    if isinstance(report.get("TCS"), dict):
        return boolish(report["TCS"].get("passed"))
    return False


def validate_milestone_signal_report(
    task: dict[str, Any],
    report: dict[str, Any] | None,
    *,
    report_kind: str,
    milestone_id: str,
    source_kind: str,
) -> dict[str, Any]:
    issues: list[str] = []
    checks: list[dict[str, Any]] = []
    expected_artifact_type = SIGNAL_REPORT_ARTIFACT_TYPES.get(report_kind)

    if not isinstance(report, dict):
        return {
            "name": f"construction_{report_kind}",
            "passed": False,
            "issues": [f"missing construction {report_kind}"],
            "checks": [{"name": f"{report_kind}_present", "passed": False}],
        }

    def add_check(name: str, passed: bool, **details: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), **details})
        if not passed:
            issues.append(details.get("issue") or name)

    add_check(
        f"{report_kind}_schema_version",
        report.get("schema_version") == TRACE_SCHEMA_VERSION,
        observed=report.get("schema_version"),
        expected=TRACE_SCHEMA_VERSION,
        issue=f"construction {report_kind} has wrong schema_version",
    )
    add_check(
        f"{report_kind}_artifact_type",
        report.get("artifact_type") == expected_artifact_type,
        observed=report.get("artifact_type"),
        expected=expected_artifact_type,
        issue=f"construction {report_kind} has wrong artifact_type",
    )
    add_check(
        f"{report_kind}_not_formal",
        report.get("formal_task_record") is False,
        observed=report.get("formal_task_record"),
        issue=f"construction {report_kind} must be non-formal",
    )
    add_check(
        f"{report_kind}_task_id_matches_task",
        report.get("task_id") == task.get("task_id"),
        observed=report.get("task_id"),
        expected=task.get("task_id"),
        issue=f"construction {report_kind} task_id does not match task",
    )
    add_check(
        f"{report_kind}_repo_id_matches_task",
        report.get("repo_id") == task.get("repo_id"),
        observed=report.get("repo_id"),
        expected=task.get("repo_id"),
        issue=f"construction {report_kind} repo_id does not match task",
    )
    add_check(
        f"{report_kind}_task_sha256_matches_task",
        report.get("task_sha256") == stable_task_digest(task),
        observed=report.get("task_sha256"),
        expected=stable_task_digest(task),
        issue=f"construction {report_kind} task_sha256 does not match task",
    )
    add_check(
        f"{report_kind}_milestone_id_matches_task",
        report.get("milestone_id") == milestone_id,
        observed=report.get("milestone_id"),
        expected=milestone_id,
        issue=f"construction {report_kind} milestone_id does not match task",
    )
    add_check(
        f"{report_kind}_source_kind_matches_run",
        report.get("source_kind") == source_kind,
        observed=report.get("source_kind"),
        expected=source_kind,
        issue=f"construction {report_kind} source_kind does not match run",
    )
    add_check(
        f"{report_kind}_status_passed",
        report_status_passed(report),
        observed_passed=report.get("passed"),
        observed_status=report.get("status"),
        issue=f"construction {report_kind} did not pass",
    )
    if report_kind == "verifier_report":
        checks_payload = collect_checks(report)
        add_check(
            "verifier_report_has_checks",
            bool(checks_payload),
            issue="construction verifier_report has no checks",
        )
        add_check(
            "verifier_report_has_hard_or_metric_checks",
            any(check_layer(check) in {L_HARD, L_METRIC} for check in checks_payload),
            issue="construction verifier_report has no L-hard/L-metric checks",
        )

    return {
        "name": f"construction_{report_kind}",
        "passed": not issues,
        "issues": issues,
        "checks": checks,
    }


def collect_checks(report: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if isinstance(report.get("checks"), list):
        checks.extend(item for item in report["checks"] if isinstance(item, dict))
    for result in report.get("results", []):
        if isinstance(result, dict) and isinstance(result.get("checks"), list):
            checks.extend(item for item in result["checks"] if isinstance(item, dict))
    return checks


def check_layer(check: dict[str, Any]) -> str | None:
    layer = check.get("layer") or check.get("signal_layer")
    return str(layer) if layer in {L_HARD, L_METRIC} else None


def check_passed(check: dict[str, Any]) -> bool:
    if "score" in check and check.get("layer") == L_METRIC:
        try:
            return float(check.get("score")) >= 1.0
        except (TypeError, ValueError):
            return boolish(check.get("passed"))
    return boolish(check.get("passed"))


def regression_events_from_report(report: dict[str, Any]) -> int:
    if "regression_events" in report:
        try:
            return int(report.get("regression_events", 0) or 0)
        except (TypeError, ValueError):
            return 1
    total = 0
    for key in ("regression_results", "replay_results", "regression_replay"):
        items = report.get(key, [])
        if isinstance(items, list):
            total += sum(1 for item in items if isinstance(item, dict) and not boolish(item.get("passed")))
    return total


def milestone_payload_from_trajectory(mid: str, report: dict[str, Any]) -> dict[str, Any] | None:
    if str(report.get("milestone_id", "")) == mid:
        return report
    for item in report.get("milestones", []):
        if isinstance(item, dict) and str(item.get("milestone_id", "")) == mid:
            return item
    return None


def derive_milestone_result(mid: str, trajectory_report: dict[str, Any], verifier_report: dict[str, Any], capture_report: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    issues: list[str] = []
    capture_ok = report_status_passed(capture_report)
    verifier_ok = report_status_passed(verifier_report)
    if not capture_ok:
        issues.append(f"{mid} capture_report did not pass")
    if not verifier_ok:
        issues.append(f"{mid} verifier_report did not pass")

    payload = milestone_payload_from_trajectory(mid, trajectory_report)
    if payload and {"hard_passed", "metric_passed"}.issubset(payload.keys()):
        hard_passed = boolish(payload.get("hard_passed")) and capture_ok and verifier_ok
        metric_passed = boolish(payload.get("metric_passed"))
        regression_events = regression_events_from_report(payload)
        result = {
            "milestone_id": mid,
            "hard_passed": hard_passed,
            "metric_passed": metric_passed,
            "regression_events": regression_events,
            "metric_score": payload.get("metric_score"),
            "soft_score": payload.get("soft_score"),
            "failure_type": payload.get("failure_type"),
        }
        return result, issues

    checks = collect_checks(verifier_report)
    hard_checks = [check for check in checks if check_layer(check) == L_HARD]
    metric_checks = [check for check in checks if check_layer(check) == L_METRIC]
    if not hard_checks and checks:
        hard_checks = checks
    hard_passed = bool(hard_checks) and all(check_passed(check) for check in hard_checks) and capture_ok and verifier_ok
    metric_passed = all(check_passed(check) for check in metric_checks)
    if not hard_checks:
        issues.append(f"{mid} verifier_report has no hard checks")
    regression_events = regression_events_from_report(verifier_report)
    result = {
        "milestone_id": mid,
        "hard_passed": hard_passed,
        "metric_passed": metric_passed,
        "regression_events": regression_events,
        "metric_score": 1.0 if metric_passed else 0.0,
        "failure_type": None if hard_passed and metric_passed and regression_events == 0 else "verifier_or_regression_failure",
    }
    return result, issues


def validate_reference_actor_trace(
    task: dict[str, Any],
    trace: dict[str, Any] | None,
    *,
    artifact_root: Path | None = None,
    require_existing_artifacts: bool = False,
) -> dict[str, Any]:
    issues: list[str] = []
    checks: list[dict[str, Any]] = []
    expected_ids = milestone_ids(task)

    if not isinstance(trace, dict):
        return {
            "name": "reference_actor_spec_only_trace",
            "passed": False,
            "issues": ["missing reference_actor_trace"],
            "checks": [{"name": "trace_present", "passed": False}],
        }

    def add_check(name: str, passed: bool, **details: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), **details})
        if not passed:
            issues.append(details.get("issue") or name)

    add_check(
        "trace_schema_version",
        trace.get("schema_version") == TRACE_SCHEMA_VERSION,
        observed=trace.get("schema_version"),
        expected=TRACE_SCHEMA_VERSION,
        issue="reference_actor_trace has wrong schema_version",
    )
    add_check(
        "trace_artifact_type",
        trace.get("artifact_type") == TRACE_ARTIFACT_TYPE,
        observed=trace.get("artifact_type"),
        expected=TRACE_ARTIFACT_TYPE,
        issue="reference_actor_trace must be a completed trace artifact, not a template",
    )
    add_check(
        "trace_non_formal",
        trace.get("formal_task_record") is False,
        observed=trace.get("formal_task_record"),
        issue="reference_actor_trace must be non-formal",
    )
    add_check(
        "trace_task_id_matches_task",
        trace.get("task_id") == task.get("task_id"),
        observed=trace.get("task_id"),
        expected=task.get("task_id"),
        issue="reference_actor_trace task_id does not match task",
    )
    add_check(
        "trace_repo_id_matches_task",
        trace.get("repo_id") == task.get("repo_id"),
        observed=trace.get("repo_id"),
        expected=task.get("repo_id"),
        issue="reference_actor_trace repo_id does not match task",
    )
    add_check(
        "trace_task_sha256_matches_task",
        trace.get("task_sha256") == stable_task_digest(task),
        observed=trace.get("task_sha256"),
        expected=stable_task_digest(task),
        issue="reference_actor_trace task_sha256 does not match task",
    )
    add_check(
        "trace_source_kind",
        trace.get("source_kind") == SPEC_ONLY_SOURCE_KIND,
        observed=trace.get("source_kind"),
        expected=SPEC_ONLY_SOURCE_KIND,
        issue="reference_actor_trace must come from a spec-only actor run",
    )
    add_check(
        "trace_passed",
        bool(trace.get("passed")),
        issue="reference_actor_trace did not pass",
    )

    input_policy = trace.get("input_policy", {})
    if not isinstance(input_policy, dict):
        input_policy = {}
    forbidden_flags = {
        "used_source_code": input_policy.get("used_source_code"),
        "used_source_text": input_policy.get("used_source_text"),
        "used_target_screenshots": input_policy.get("used_target_screenshots"),
        "used_target_render_crops": input_policy.get("used_target_render_crops"),
        "used_exact_target_text": input_policy.get("used_exact_target_text"),
    }
    add_check(
        "actor_input_content_free",
        all(value is False for value in forbidden_flags.values()),
        flags=forbidden_flags,
        issue="reference actor trace used source/target-visible input",
    )

    observed_milestones = trace_milestones(trace)
    observed_ids = [str(item.get("milestone_id", "")) for item in observed_milestones]
    add_check(
        "milestone_sequence_matches_task",
        observed_ids == expected_ids,
        observed=observed_ids,
        expected=expected_ids,
        issue="reference actor trace milestone sequence does not match task",
    )

    for index, milestone in enumerate(observed_milestones):
        mid = str(milestone.get("milestone_id", f"milestone_{index + 1}"))
        hard_passed = bool(milestone.get("hard_passed"))
        metric_passed = bool(milestone.get("metric_passed", True))
        regression_raw = milestone.get("regression_events", 0)
        try:
            regression_events = int(regression_raw)
        except (TypeError, ValueError):
            regression_events = None
        artifact_refs = milestone.get("artifact_refs", {})
        artifact_refs_ok = (
            isinstance(artifact_refs, dict)
            and all(isinstance(artifact_refs.get(key), str) and bool(artifact_refs.get(key).strip()) for key in REQUIRED_ARTIFACT_REF_KEYS)
        )
        resolved_refs: dict[str, str] = {}
        missing_ref_paths: dict[str, list[str]] = {}
        if require_existing_artifacts and artifact_refs_ok:
            for key in REQUIRED_ARTIFACT_REF_KEYS:
                raw_ref = str(artifact_refs[key]).strip()
                candidates = candidate_artifact_paths(raw_ref, artifact_root)
                existing = next((candidate for candidate in candidates if candidate.exists()), None)
                if existing is None:
                    missing_ref_paths[key] = [str(candidate) for candidate in candidates]
                else:
                    resolved_refs[key] = str(existing)
        add_check(
            "milestone_hard_metric_regression",
            hard_passed and metric_passed and regression_events == 0,
            milestone_id=mid,
            hard_passed=hard_passed,
            metric_passed=metric_passed,
            regression_events=regression_events,
            regression_events_raw=regression_raw,
            issue=f"{mid} does not pass hard/metric/regression checks",
        )
        add_check(
            "milestone_artifact_refs_present",
            artifact_refs_ok,
            milestone_id=mid,
            required_keys=list(REQUIRED_ARTIFACT_REF_KEYS),
            issue=f"{mid} has no trace artifact refs",
        )
        if require_existing_artifacts:
            add_check(
                "milestone_artifact_refs_exist",
                artifact_refs_ok and not missing_ref_paths,
                milestone_id=mid,
                artifact_root=str(artifact_root) if artifact_root else None,
                resolved_refs=resolved_refs,
                missing_ref_paths=missing_ref_paths,
                issue=f"{mid} trace artifact refs do not exist",
            )

    return {
        "name": "reference_actor_spec_only_trace",
        "passed": not issues,
        "issues": issues,
        "checks": checks,
        "details": {
            "source_kind": trace.get("source_kind"),
            "milestone_count": len(observed_milestones),
            "expected_milestone_count": len(expected_ids),
            "require_existing_artifacts": require_existing_artifacts,
        },
    }


def build_reference_actor_trace_template(task: dict[str, Any]) -> dict[str, Any]:
    """Return a non-passing template for a manually audited spec-only actor run."""
    return {
        "schema_version": TRACE_SCHEMA_VERSION,
        "artifact_type": "reference_actor_trace_template_only",
        "formal_task_record": False,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "task_id": task.get("task_id"),
        "repo_id": task.get("repo_id"),
        "task_sha256": stable_task_digest(task),
        "source_kind": SPEC_ONLY_SOURCE_KIND,
        "passed": False,
        "input_policy": default_actor_input_policy(),
        "milestones": [
            {
                "milestone_id": milestone_id,
                "hard_passed": False,
                "metric_passed": False,
                "regression_events": None,
                "artifact_refs": {
                    "workspace": "",
                    "trajectory_report": "",
                    "capture_report": "",
                    "verifier_report": "",
                },
                "notes": "Fill manually after a real spec-only reference actor run.",
            }
            for milestone_id in milestone_ids(task)
        ],
    }


def milestone_evidence_dir(evidence_root: Path, milestone_id: str) -> Path | None:
    candidates = [
        evidence_root / "evidence" / milestone_id,
        evidence_root / milestone_id,
    ]
    return next((candidate for candidate in candidates if candidate.exists()), None)


def build_reference_actor_evidence_manifest(
    task: dict[str, Any],
    *,
    evidence_root: Path,
    output_path: Path,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    issues: list[str] = []
    checks: list[dict[str, Any]] = []
    output_dir = output_path.parent
    manifest_items: list[dict[str, Any]] = []

    def add_check(name: str, passed: bool, **details: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), **details})
        if not passed:
            issues.append(details.get("issue") or name)

    for mid in milestone_ids(task):
        root = milestone_evidence_dir(evidence_root, mid)
        add_check(
            "evidence_milestone_dir_present",
            root is not None,
            milestone_id=mid,
            searched=[str(evidence_root / "evidence" / mid), str(evidence_root / mid)],
            issue=f"{mid} evidence directory is missing",
        )
        if root is None:
            continue
        expected_paths = {
            "workspace": root / "workspace",
            "trajectory_report": root / "trajectory_report.json",
            "capture_report": root / "capture_report.json",
            "verifier_report": root / "verifier_report.json",
        }
        missing = {key: str(path) for key, path in expected_paths.items() if not path.exists()}
        add_check(
            "evidence_milestone_artifacts_present",
            not missing,
            milestone_id=mid,
            missing=missing,
            issue=f"{mid} evidence artifacts are incomplete",
        )
        if missing:
            continue
        try:
            milestone_report = load_json(expected_paths["trajectory_report"])
        except json.JSONDecodeError as exc:
            add_check(
                "evidence_milestone_trajectory_report_parse",
                False,
                milestone_id=mid,
                issue=f"{mid} trajectory_report.json is not valid JSON: {exc}",
            )
            continue
        standardized_report = (
            milestone_report.get("artifact_type") == MILESTONE_RUN_ARTIFACT_TYPE
            and milestone_report.get("formal_task_record") is False
            and str(milestone_report.get("milestone_id", "")) == mid
        )
        add_check(
            "evidence_milestone_trajectory_report_standard",
            standardized_report,
            milestone_id=mid,
            observed_artifact_type=milestone_report.get("artifact_type"),
            observed_milestone_id=milestone_report.get("milestone_id"),
            issue=f"{mid} trajectory_report.json must be a standardized construction_milestone_run_report",
        )
        if not standardized_report:
            continue
        manifest_items.append(
            {
                "milestone_id": mid,
                "artifact_refs": {
                    key: relative_ref(path, output_dir)
                    for key, path in expected_paths.items()
                },
            }
        )

    manifest = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "artifact_type": "reference_actor_evidence_manifest",
        "formal_task_record": False,
        "task_id": task.get("task_id"),
        "repo_id": task.get("repo_id"),
        "source_kind": SPEC_ONLY_SOURCE_KIND,
        "input_policy": default_actor_input_policy(),
        "milestones": manifest_items,
    }
    validation = {
        "name": "reference_actor_evidence_manifest",
        "passed": not issues and [item.get("milestone_id") for item in manifest_items] == milestone_ids(task),
        "issues": issues,
        "checks": checks,
        "details": {
            "milestone_count": len(manifest_items),
            "expected_milestone_count": len(milestone_ids(task)),
        },
    }
    return (manifest if validation["passed"] else None), validation


def build_reference_actor_trace_from_manifest(
    task: dict[str, Any],
    manifest: dict[str, Any],
    *,
    artifact_root: Path,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    issues: list[str] = []
    checks: list[dict[str, Any]] = []
    expected_ids = milestone_ids(task)
    manifest_items = trace_milestones(manifest)
    manifest_ids = [str(item.get("milestone_id", "")) for item in manifest_items]

    def add_check(name: str, passed: bool, **details: Any) -> None:
        checks.append({"name": name, "passed": bool(passed), **details})
        if not passed:
            issues.append(details.get("issue") or name)

    add_check(
        "manifest_source_kind",
        manifest.get("source_kind") == SPEC_ONLY_SOURCE_KIND,
        observed=manifest.get("source_kind"),
        expected=SPEC_ONLY_SOURCE_KIND,
        issue="reference actor evidence manifest must come from a spec-only actor run",
    )
    input_policy = manifest.get("input_policy", {})
    if not isinstance(input_policy, dict):
        input_policy = {}
    forbidden_flags = {
        "used_source_code": input_policy.get("used_source_code"),
        "used_source_text": input_policy.get("used_source_text"),
        "used_target_screenshots": input_policy.get("used_target_screenshots"),
        "used_target_render_crops": input_policy.get("used_target_render_crops"),
        "used_exact_target_text": input_policy.get("used_exact_target_text"),
    }
    add_check(
        "manifest_input_content_free",
        all(value is False for value in forbidden_flags.values()),
        flags=forbidden_flags,
        issue="reference actor evidence manifest used source/target-visible input",
    )
    add_check(
        "manifest_milestone_sequence_matches_task",
        manifest_ids == expected_ids,
        observed=manifest_ids,
        expected=expected_ids,
        issue="reference actor evidence manifest milestone sequence does not match task",
    )

    trace_milestone_items: list[dict[str, Any]] = []
    for item in manifest_items:
        mid = str(item.get("milestone_id", ""))
        artifact_refs = item.get("artifact_refs", {})
        refs_ok = isinstance(artifact_refs, dict) and all(
            isinstance(artifact_refs.get(key), str) and artifact_refs.get(key).strip()
            for key in REQUIRED_ARTIFACT_REF_KEYS
        )
        add_check(
            "manifest_milestone_artifact_refs_present",
            refs_ok,
            milestone_id=mid,
            required_keys=list(REQUIRED_ARTIFACT_REF_KEYS),
            issue=f"{mid} evidence manifest artifact refs are incomplete",
        )
        if not refs_ok:
            continue
        resolved: dict[str, Path] = {}
        missing: dict[str, list[str]] = {}
        for key in REQUIRED_ARTIFACT_REF_KEYS:
            raw = str(artifact_refs[key]).strip()
            path = existing_artifact_path(raw, artifact_root)
            if path is None:
                missing[key] = [str(candidate) for candidate in candidate_artifact_paths(raw, artifact_root)]
            else:
                resolved[key] = path
        add_check(
            "manifest_milestone_artifact_refs_exist",
            not missing,
            milestone_id=mid,
            resolved_refs={key: str(path) for key, path in resolved.items()},
            missing_ref_paths=missing,
            issue=f"{mid} evidence manifest artifact refs do not exist",
        )
        if missing:
            continue
        try:
            trajectory_report = load_json(resolved["trajectory_report"])
            verifier_report = load_json(resolved["verifier_report"])
            capture_report = load_json(resolved["capture_report"])
        except json.JSONDecodeError as exc:
            add_check(
                "manifest_milestone_artifacts_parse",
                False,
                milestone_id=mid,
                issue=f"{mid} evidence artifact is not valid JSON: {exc}",
            )
            continue
        capture_validation = validate_milestone_signal_report(
            task,
            capture_report,
            report_kind="capture_report",
            milestone_id=mid,
            source_kind=SPEC_ONLY_SOURCE_KIND,
        )
        verifier_validation = validate_milestone_signal_report(
            task,
            verifier_report,
            report_kind="verifier_report",
            milestone_id=mid,
            source_kind=SPEC_ONLY_SOURCE_KIND,
        )
        add_check(
            "manifest_milestone_capture_report_bound",
            bool(capture_validation.get("passed")),
            milestone_id=mid,
            details=capture_validation,
            issue=f"{mid} capture_report is not a bound construction capture report",
        )
        add_check(
            "manifest_milestone_verifier_report_bound",
            bool(verifier_validation.get("passed")),
            milestone_id=mid,
            details=verifier_validation,
            issue=f"{mid} verifier_report is not a bound construction verifier report",
        )
        if not capture_validation.get("passed") or not verifier_validation.get("passed"):
            issues.extend(capture_validation.get("issues", []))
            issues.extend(verifier_validation.get("issues", []))
        result, result_issues = derive_milestone_result(mid, trajectory_report, verifier_report, capture_report)
        for issue in result_issues:
            issues.append(issue)
        result_passed = (
            bool(capture_validation.get("passed"))
            and bool(verifier_validation.get("passed"))
            and bool(result.get("hard_passed"))
            and bool(result.get("metric_passed"))
            and int(result.get("regression_events", 0) or 0) == 0
        )
        add_check(
            "manifest_milestone_derived_result_passes",
            result_passed,
            milestone_id=mid,
            derived_result=result,
            issue=f"{mid} derived reference actor result did not pass",
        )
        trace_milestone = {
            "milestone_id": mid,
            "hard_passed": bool(result.get("hard_passed")),
            "metric_passed": bool(result.get("metric_passed")),
            "regression_events": int(result.get("regression_events", 0) or 0),
            "artifact_refs": {key: str(artifact_refs[key]).strip() for key in REQUIRED_ARTIFACT_REF_KEYS},
        }
        if result.get("metric_score") is not None:
            trace_milestone["metric_score"] = result.get("metric_score")
        if result.get("soft_score") is not None:
            trace_milestone["soft_score"] = result.get("soft_score")
        if result.get("failure_type") is not None:
            trace_milestone["failure_type"] = result.get("failure_type")
        trace_milestone_items.append(trace_milestone)

    passed = not issues and len(trace_milestone_items) == len(expected_ids)
    trace = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "artifact_type": TRACE_ARTIFACT_TYPE,
        "formal_task_record": False,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "task_id": task.get("task_id"),
        "repo_id": task.get("repo_id"),
        "task_sha256": stable_task_digest(task),
        "source_kind": SPEC_ONLY_SOURCE_KIND,
        "passed": passed,
        "input_policy": input_policy,
        "milestones": trace_milestone_items,
    }
    trace_validation = validate_reference_actor_trace(
        task,
        trace,
        artifact_root=artifact_root,
        require_existing_artifacts=True,
    )
    validation = {
        "name": "reference_actor_trace_builder",
        "passed": passed and trace_validation.get("passed"),
        "issues": issues + trace_validation.get("issues", []),
        "checks": checks,
        "trace_validation": trace_validation,
    }
    return (trace if validation["passed"] else None), validation


def attach_reference_actor_trace(
    task: dict[str, Any],
    trace: dict[str, Any],
    *,
    artifact_root: Path | None = None,
    require_existing_artifacts: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    validation = validate_reference_actor_trace(
        task,
        trace,
        artifact_root=artifact_root,
        require_existing_artifacts=require_existing_artifacts,
    )
    if not validation["passed"]:
        return task, validation

    updated = dict(task)
    updated["reference_actor_trace"] = trace
    gates = dict(updated.get("gates", {}))
    gates["spec_completeness_precheck"] = {
        "gate": "spec_completeness_precheck",
        "passed": True,
        "status": "passed",
        "report_paths": [],
        "summary": {
            "reference_actor_trace": "passed",
            "source_kind": trace.get("source_kind"),
            "milestone_count": validation.get("details", {}).get("milestone_count"),
            "checks": validation.get("checks", []),
        },
    }
    updated["gates"] = gates
    return updated, validation


def add_trace_template_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "construction_drafts" / "reference_actor_trace.template.json")


def add_build_trace_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True, help="Spec-only actor evidence manifest with per-milestone artifact refs.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validation-output", type=Path, default=None)


def add_build_manifest_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True, help="Directory containing evidence/<milestone_id>/ artifacts.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validation-output", type=Path, default=None)


def run_build_manifest_from_args(args: argparse.Namespace) -> None:
    try:
        assert_not_under_formal_task_root(args.output, purpose="Reference actor evidence manifest")
        if args.validation_output is not None:
            assert_not_under_formal_task_root(
                args.validation_output,
                purpose="Reference actor evidence manifest validation report",
            )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    task = load_json(args.task)
    manifest, validation = build_reference_actor_evidence_manifest(
        task,
        evidence_root=args.evidence_root,
        output_path=args.output,
    )
    if args.validation_output is not None:
        ensure_dir(args.validation_output.parent)
        write_json(args.validation_output, validation)
    if not validation["passed"] or manifest is None:
        raise SystemExit(
            "reference actor evidence manifest could not be built; output was not written: "
            + "; ".join(validation.get("issues", []))
        )
    ensure_dir(args.output.parent)
    write_json(args.output, manifest)
    print(f"built reference actor evidence manifest to {args.output}")


def run_build_trace_from_args(args: argparse.Namespace) -> None:
    try:
        assert_not_under_formal_task_root(args.output, purpose="Reference actor trace")
        if args.validation_output is not None:
            assert_not_under_formal_task_root(
                args.validation_output,
                purpose="Reference actor trace validation report",
            )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    task = load_json(args.task)
    manifest = load_json(args.manifest)
    trace, validation = build_reference_actor_trace_from_manifest(
        task,
        manifest,
        artifact_root=args.manifest.parent,
    )
    if args.validation_output is not None:
        ensure_dir(args.validation_output.parent)
        write_json(args.validation_output, validation)
    if not validation["passed"] or trace is None:
        raise SystemExit(
            "reference actor trace could not be built; output was not written: "
            + "; ".join(validation.get("issues", []))
        )
    ensure_dir(args.output.parent)
    write_json(args.output, trace)
    print(f"built validated reference actor trace to {args.output}")


def run_trace_template_from_args(args: argparse.Namespace) -> None:
    try:
        assert_not_under_formal_task_root(args.output, purpose="Reference actor trace template")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    task = load_json(args.task)
    template = build_reference_actor_trace_template(task)
    ensure_dir(args.output.parent)
    write_json(args.output, template)
    print(f"wrote reference actor trace template to {args.output}; template is not a passing trace")


def add_attach_trace_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--in-place", action="store_true", help="Overwrite --task after the trace validates.")
    parser.add_argument("--validation-output", type=Path, default=None)


def run_attach_trace_from_args(args: argparse.Namespace) -> None:
    if args.validation_output is not None:
        try:
            assert_not_under_formal_task_root(
                args.validation_output,
                purpose="Reference actor trace attach validation report",
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    task = load_json(args.task)
    trace = load_json(args.trace)
    updated, validation = attach_reference_actor_trace(
        task,
        trace,
        artifact_root=args.trace.parent,
        require_existing_artifacts=True,
    )

    if args.validation_output is not None:
        ensure_dir(args.validation_output.parent)
        write_json(args.validation_output, validation)

    if not validation["passed"]:
        raise SystemExit(
            "reference actor trace did not validate; task was not updated: "
            + "; ".join(validation.get("issues", []))
        )

    if args.in_place:
        output = args.task
    elif args.output is not None:
        output = args.output
    else:
        output = args.task.with_name(f"{args.task.stem}.with_reference_actor.json")
    try:
        assert_not_under_formal_task_root(output, purpose="Construction task with attached reference actor trace")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    ensure_dir(output.parent)
    write_json(output, updated)
    print(f"attached validated reference actor trace to {output}")
