from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..core.config import DEFAULT_OUTPUT_ROOT
from ..core.formal_data_guard import assert_not_under_formal_task_root
from ..core.io_utils import ensure_dir, read_jsonl, write_json


@dataclass(frozen=True)
class MetaEvalSummary:
    reference_passed: bool
    original_failed: bool
    bad_solutions_failed: bool
    reproducible: bool
    reference_reports: list[dict[str, Any]] = field(default_factory=list)
    original_reports: list[dict[str, Any]] = field(default_factory=list)
    bad_solution_reports: list[dict[str, Any]] = field(default_factory=list)
    repeat_reports: list[dict[str, Any]] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (
            self.reference_passed
            and self.original_failed
            and self.bad_solutions_failed
            and self.reproducible
            and not self.issues
        )

    def to_json(self) -> dict:
        data = asdict(self)
        data["passed"] = self.passed
        return data


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "passed", "pass"}
    return bool(value)


def result_pass_values(report: dict[str, Any]) -> list[bool]:
    if isinstance(report.get("results"), list) and report["results"]:
        return [boolish(item.get("passed")) for item in report["results"] if isinstance(item, dict)]
    if "items" in report and isinstance(report["items"], list) and report["items"]:
        values: list[bool] = []
        for item in report["items"]:
            if isinstance(item, dict) and isinstance(item.get("WCS"), dict):
                values.append(boolish(item["WCS"].get("passed")))
            elif isinstance(item, dict) and "passed" in item:
                values.append(boolish(item.get("passed")))
        return values
    if "passed" in report:
        return [boolish(report.get("passed"))]
    if "wcs_passed" in report and "total" in report:
        return [int(report.get("wcs_passed", 0)) == int(report.get("total", 0))]
    if "TCS" in report and isinstance(report["TCS"], dict):
        return [boolish(report["TCS"].get("passed"))]
    return []


def report_failed_check_names(report: dict[str, Any]) -> list[str]:
    names: set[str] = set()
    for result in report.get("results", []):
        if not isinstance(result, dict):
            continue
        for check in result.get("checks", []):
            if isinstance(check, dict) and check.get("passed") is False and check.get("name"):
                names.add(str(check["name"]))
    return sorted(names)


def bad_solution_metadata_path(report_path: Path) -> Path:
    return report_path.with_name("bad_solution_metadata.json")


def expected_checks_for_control_id(control_id: str | None) -> list[str]:
    if control_id == "missing_completion":
        return ["completion_text_signals"]
    if control_id == "placeholder_completion":
        return ["forbidden_text_patterns"]
    if control_id == "regression_deletion":
        return ["regression_text_signals"]
    if control_id == "asset_shortcut":
        return ["asset_path_signals"]
    if control_id == "hidden_text_completion":
        return ["visible_text_signals"]
    if control_id == "source_shortcut":
        return ["source_change_audit"]
    return []


def bad_solution_validation_issues(report_path: Path) -> list[str]:
    issues: list[str] = []
    metadata_path = bad_solution_metadata_path(report_path)
    if not metadata_path.exists():
        issues.append(f"bad-solution metadata missing: {report_path}")
        return issues

    metadata = load_json(metadata_path)
    if metadata.get("capture_status") != "passed":
        issues.append(f"bad-solution capture did not pass: {report_path}")

    report = load_json(report_path)
    pass_values = result_pass_values(report)
    if not pass_values or any(pass_values):
        issues.append(f"bad-solution report did not fail all evaluated tasks: {report_path}")

    failed_checks = set(report_failed_check_names(report))
    expected_checks = set(metadata.get("expected_failed_checks") or expected_checks_for_control_id(metadata.get("control_id")))
    if expected_checks and not failed_checks.intersection(expected_checks):
        issues.append(f"bad-solution failed checks do not cover expected checks: {report_path}")
    return issues


def summarize_report(path: Path, expected: str) -> dict[str, Any]:
    report = load_json(path)
    pass_values = result_pass_values(report)
    passed_all = bool(pass_values) and all(pass_values)
    failed_all = bool(pass_values) and not any(pass_values)
    ok = passed_all if expected == "pass" else failed_all
    return {
        "path": str(path),
        "expected": expected,
        "observed_pass_values": pass_values,
        "passed_all": passed_all,
        "failed_all": failed_all,
        "ok": ok,
        "task_count": len(pass_values),
    }


def compare_reproducible(reference_summaries: list[dict[str, Any]], repeat_summaries: list[dict[str, Any]]) -> bool:
    if not reference_summaries or not repeat_summaries:
        return False
    reference_vector = [value for item in reference_summaries for value in item["observed_pass_values"]]
    for repeat in repeat_summaries:
        if repeat["observed_pass_values"] != reference_vector:
            return False
    return True


def build_meta_eval(
    reference_reports: list[Path],
    original_reports: list[Path],
    bad_solution_reports: list[Path],
    repeat_reports: list[Path],
    *,
    assume_reproducible: bool = False,
) -> dict[str, Any]:
    issues: list[str] = []
    reference = [summarize_report(path, "pass") for path in reference_reports]
    original = [summarize_report(path, "fail") for path in original_reports]
    bad = [summarize_report(path, "fail") for path in bad_solution_reports]
    repeat = [summarize_report(path, "pass") for path in repeat_reports]

    for index, path in enumerate(bad_solution_reports):
        validation_issues = bad_solution_validation_issues(path)
        if validation_issues:
            bad[index]["ok"] = False
            bad[index]["validation_issues"] = validation_issues
            issues.extend(validation_issues)

    if not reference:
        issues.append("missing reference report; reference must pass before task freeze")
    if not original:
        issues.append("missing original/baseline negative report; original must fail target completion")
    if not bad:
        issues.append("missing bad-solution reports; known shortcuts must fail")
    if not repeat and not assume_reproducible:
        issues.append("missing repeat report; reproducibility is unproven")

    reference_passed = bool(reference) and all(item["ok"] for item in reference)
    original_failed = bool(original) and all(item["ok"] for item in original)
    bad_solutions_failed = bool(bad) and all(item["ok"] for item in bad)
    reproducible = True if assume_reproducible else compare_reproducible(reference, repeat)

    summary = MetaEvalSummary(
        reference_passed=reference_passed,
        original_failed=original_failed,
        bad_solutions_failed=bad_solutions_failed,
        reproducible=reproducible,
        reference_reports=reference,
        original_reports=original,
        bad_solution_reports=bad,
        repeat_reports=repeat,
        issues=issues,
    )
    return summary.to_json()


def priority_for(path: Path, category: str) -> tuple[int, str]:
    name = path.name.lower()
    priorities = {
        "reference": [
            "reference_results.json",
            "reference_verifier.json",
            "reference_verification.json",
            "reference_verifier_report.json",
            "reference_score_report.json",
            "reference_score.json",
        ],
        "original": [
            "submission_results.baseline_states.json",
            "baseline_submission_verifier.json",
            "baseline_submission_verification.json",
            "baseline_as_submission.json",
            "baseline_as_submission_report.json",
            "baseline_reference_report.json",
            "baseline_reference_verification.json",
            "score_report.baseline_states.json",
            "baseline_submission_score.json",
        ],
        "repeat": [
            "submission_results.reference_states.json",
            "reference_submission_verifier.json",
            "reference_submission_verification.json",
            "reference_as_submission.json",
            "reference_as_submission_report.json",
            "score_report.reference_states.json",
            "reference_submission_score.json",
        ],
        "bad": [
            "bad_solution_results.json",
            "bad_solution_report.json",
            "negative_control_results.json",
            "shortcut_solution_results.json",
        ],
    }[category]
    for index, suffix in enumerate(priorities):
        if name == suffix or name.endswith("_" + suffix):
            return index, name
    return len(priorities), name


def classify_evidence_file(path: Path) -> str | None:
    name = path.name.lower()
    if not name.endswith(".json"):
        return None
    if any(token in name for token in ("bad", "negative", "shortcut")):
        return "bad"
    if "baseline" in name and ("submission" in name or "as_submission" in name or "baseline_reference" in name or "score_report.baseline" in name):
        return "original"
    if "reference" in name and ("submission" in name or "as_submission" in name or "score_report.reference" in name):
        return "repeat"
    if "reference" in name and "baseline" not in name and "submission" not in name and "as_submission" not in name:
        return "reference"
    return None


def task_record_from_jsonl(path: Path) -> dict[str, Any]:
    records = list(read_jsonl(path))
    if not records:
        return {}
    return records[0]


def slot_id_from_task_file(path: Path) -> str:
    if path.parent.name.startswith("slot_"):
        return path.parent.name
    return path.stem


def evidence_candidates_for_slot(task_root: Path, slot_id: str) -> dict[str, list[Path]]:
    candidates: dict[str, list[Path]] = {"reference": [], "original": [], "repeat": [], "bad": []}
    search_roots = []
    slot_dir = task_root / slot_id
    if slot_dir.exists():
        search_roots.append(slot_dir)
    search_roots.append(task_root)
    seen: set[Path] = set()
    for root in search_roots:
        for path in sorted(root.glob("*.json")):
            if path in seen:
                continue
            if root == task_root and not path.name.startswith(slot_id):
                continue
            seen.add(path)
            category = classify_evidence_file(path)
            if category:
                candidates[category].append(path)
    return candidates


def choose_canonical(paths: list[Path], category: str) -> Path | None:
    if not paths:
        return None
    return sorted(paths, key=lambda path: priority_for(path, category))[0]


def build_meta_eval_batch(
    task_root: Path,
    *,
    pattern: str = "slot_*/task.jsonl",
    output_jsonl: Path | None = None,
    assume_reproducible: bool = False,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for task_path in sorted(task_root.glob(pattern)):
        task = task_record_from_jsonl(task_path)
        slot_id = slot_id_from_task_file(task_path)
        candidates = evidence_candidates_for_slot(task_root, slot_id)
        selected = {
            category: choose_canonical(paths, category)
            for category, paths in candidates.items()
        }
        report = build_meta_eval(
            reference_reports=[selected["reference"]] if selected["reference"] else [],
            original_reports=[selected["original"]] if selected["original"] else [],
            bad_solution_reports=[selected["bad"]] if selected["bad"] else [],
            repeat_reports=[selected["repeat"]] if selected["repeat"] else [],
            assume_reproducible=assume_reproducible,
        )
        items.append(
            {
                "slot_id": slot_id,
                "task_id": task.get("task_id"),
                "repo_id": task.get("repo_id"),
                "passed": report["passed"],
                "reference_passed": report["reference_passed"],
                "original_failed": report["original_failed"],
                "bad_solutions_failed": report["bad_solutions_failed"],
                "reproducible": report["reproducible"],
                "issues": report["issues"],
                "selected_reports": {
                    category: str(path) if path else None
                    for category, path in selected.items()
                },
                "candidate_report_counts": {
                    category: len(paths)
                    for category, paths in candidates.items()
                },
            }
        )

    if output_jsonl is not None:
        ensure_dir(output_jsonl.parent)
        output_jsonl.write_text(
            "\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in items) + ("\n" if items else ""),
            encoding="utf-8",
        )

    total = len(items)
    summary = {
        "schema_version": "2026-06-18",
        "task_root": str(task_root),
        "task_pattern": pattern,
        "total_tasks": total,
        "passed": sum(1 for item in items if item["passed"]),
        "failed": sum(1 for item in items if not item["passed"]),
        "reference_passed": sum(1 for item in items if item["reference_passed"]),
        "original_failed": sum(1 for item in items if item["original_failed"]),
        "bad_solutions_failed": sum(1 for item in items if item["bad_solutions_failed"]),
        "reproducible": sum(1 for item in items if item["reproducible"]),
        "missing_reference": sum(1 for item in items if item["selected_reports"]["reference"] is None),
        "missing_original": sum(1 for item in items if item["selected_reports"]["original"] is None),
        "missing_bad_solution": sum(1 for item in items if item["selected_reports"]["bad"] is None),
        "missing_repeat": sum(1 for item in items if item["selected_reports"]["repeat"] is None),
        "issue_counts": {},
        "items": items,
    }
    issue_counts: dict[str, int] = {}
    for item in items:
        for issue in item["issues"]:
            issue_counts[issue] = issue_counts.get(issue, 0) + 1
    summary["issue_counts"] = dict(sorted(issue_counts.items(), key=lambda pair: (-pair[1], pair[0])))
    return summary


def add_meta_eval_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--reference", type=Path, action="append", default=[], help="Verifier/score report that must pass.")
    parser.add_argument("--original", type=Path, action="append", default=[], help="Original or empty baseline report that must fail.")
    parser.add_argument("--bad-solution", type=Path, action="append", default=[], help="Known bad solution report that must fail.")
    parser.add_argument("--repeat", type=Path, action="append", default=[], help="Repeat run report used to prove deterministic reproducibility.")
    parser.add_argument("--assume-reproducible", action="store_true", help="Only for scaffolding: mark reproducibility as assumed when no repeat report exists.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "metareval" / "meta_eval.json")


def add_meta_eval_batch_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks")
    parser.add_argument("--task-pattern", default="slot_*/task.jsonl")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "metareval" / "formal_tasks.meta_eval_summary.json")
    parser.add_argument("--output-jsonl", type=Path, default=DEFAULT_OUTPUT_ROOT / "metareval" / "formal_tasks.meta_eval_items.jsonl")
    parser.add_argument("--assume-reproducible", action="store_true", help="Only for scaffolding: mark reproducibility as assumed when no repeat report exists.")


def run_meta_eval_from_args(args: argparse.Namespace) -> None:
    try:
        assert_not_under_formal_task_root(args.output, purpose="Meta-eval report")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    ensure_dir(args.output.parent)
    report = build_meta_eval(
        reference_reports=args.reference,
        original_reports=args.original,
        bad_solution_reports=args.bad_solution,
        repeat_reports=args.repeat,
        assume_reproducible=args.assume_reproducible,
    )
    write_json(args.output, report)
    print(
        "meta-eval "
        f"passed={report['passed']} reference={report['reference_passed']} "
        f"original_failed={report['original_failed']} bad_failed={report['bad_solutions_failed']} "
        f"reproducible={report['reproducible']} issues={len(report['issues'])}"
    )


def run_meta_eval_batch_from_args(args: argparse.Namespace) -> None:
    try:
        assert_not_under_formal_task_root(args.output, purpose="Meta-eval batch summary")
        assert_not_under_formal_task_root(args.output_jsonl, purpose="Meta-eval batch items")
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    ensure_dir(args.output.parent)
    summary = build_meta_eval_batch(
        task_root=args.task_root,
        pattern=args.task_pattern,
        output_jsonl=args.output_jsonl,
        assume_reproducible=args.assume_reproducible,
    )
    write_json(args.output, summary)
    print(
        "meta-eval batch "
        f"passed={summary['passed']}/{summary['total_tasks']} "
        f"reference={summary['reference_passed']} original={summary['original_failed']} "
        f"bad={summary['bad_solutions_failed']} reproducible={summary['reproducible']} "
        f"output={args.output}"
    )
