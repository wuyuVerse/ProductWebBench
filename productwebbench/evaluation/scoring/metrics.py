from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any

from ...core.config import DEFAULT_OUTPUT_ROOT
from ...core.io_utils import write_json
from ...taxonomy.capability import METRICS


METRIC_NAMES = [
    "CCS",
    "SCS",
    "DCS",
    "ECS",
    "CACS",
    "RCS",
    "ICS",
    "BES",
]

METRIC_DESCRIPTIONS = {item.key: item.label for item in METRICS}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def check_map(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {check["name"]: check for check in result.get("checks", [])}


def bool_score(check: dict[str, Any] | None) -> float | None:
    if check is None:
        return None
    return 1.0 if check.get("passed") else 0.0


def completion_score(check: dict[str, Any] | None) -> float | None:
    if check is None:
        return None
    details = check.get("details", {})
    min_found = max(1, int(details.get("min_found", 1)))
    found = int(details.get("found_count", 0))
    return min(1.0, found / min_found)


def change_completion_score(checks: dict[str, dict[str, Any]]) -> float | None:
    completion = completion_score(checks.get("completion_text_signals"))
    forbidden = bool_score(checks.get("forbidden_text_patterns"))
    values = [value for value in [completion, forbidden] if value is not None]
    if not values:
        return None
    return min(values)


def missing_fraction_score(check: dict[str, Any] | None, missing_key: str = "missing", total_key: str = "signals") -> float | None:
    if check is None:
        return None
    if check.get("passed"):
        return 1.0
    details = check.get("details", {})
    if "found_count" in details and "min_found" in details:
        min_found = max(1, int(details.get("min_found", 1)))
        found = int(details.get("found_count", 0))
        return min(1.0, found / min_found)
    total = len(details.get(total_key, []))
    if total == 0:
        return bool_score(check)
    missing = len(details.get(missing_key, []))
    return max(0.0, 1.0 - missing / total)


def state_url_signal_score(check: dict[str, Any] | None) -> float | None:
    if check is None:
        return None
    if check.get("passed"):
        return 1.0
    details = check.get("details", {})
    signals = details.get("signals", [])
    total = len(signals)
    if total == 0:
        return bool_score(check)
    failed_signal_ids = {
        failure.get("state_id") or failure.get("reason") or str(index)
        for index, failure in enumerate(details.get("failures", []))
    }
    return max(0.0, 1.0 - min(total, len(failed_signal_ids)) / total)


def design_continuity_score(checks: dict[str, dict[str, Any]]) -> float | None:
    """Visual fidelity bucket (DCS): anchor gate + D3 visual_regression gate.

    Only counts gates that are actually configured (unconfigured gates report
    a trivial pass and are ignored so they don't inflate the score). When the
    D3 gate is present it must hold alongside the anchor gate -> min of both.
    """
    values: list[float] = []
    anchor = checks.get("visual_anchor_similarity")
    if anchor is not None:
        values.append(bool_score(anchor))
    vr = checks.get("visual_regression")
    if vr is not None and vr.get("message") != "visual-regression gate not configured":
        values.append(bool_score(vr))
    if not values:
        return bool_score(checks.get("visual_anchor_similarity"))
    return min(values)


def state_continuity_score(checks: dict[str, dict[str, Any]]) -> float | None:
    """Interaction bucket (SCS): trajectory-ran (action_history) + D4 terminal
    assertion (interaction_assertions). Only counts configured gates; when the
    D4 gate is present it must hold alongside action_history -> min of both.
    """
    values: list[float] = []
    ah = checks.get("action_history")
    if ah is not None:
        values.append(bool_score(ah))
    ia = checks.get("interaction_assertions")
    if ia is not None and ia.get("message") != "no interaction assertions configured":
        values.append(bool_score(ia))
    if not values:
        return bool_score(checks.get("action_history"))
    return min(values)


def browser_experience_score(checks: dict[str, dict[str, Any]]) -> float | None:
    names = ["required_states", "state_artifacts", "state_quality", "screenshot_files"]
    values = [bool_score(checks.get(name)) for name in names]
    values = [value for value in values if value is not None]
    if not values:
        return None
    return mean(values)


def experience_continuity_score(checks: dict[str, dict[str, Any]]) -> float | None:
    values = [
        bool_score(checks.get("required_states")),
        bool_score(checks.get("state_artifacts")),
        bool_score(checks.get("no_horizontal_overflow")),
        missing_fraction_score(checks.get("completion_text_signals")),
        state_url_signal_score(checks.get("state_url_signals")),
    ]
    values = [value for value in values if value is not None]
    if not values:
        return None
    return mean(values)


def metric_scores(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    checks = check_map(result)
    asset_scores = []
    if checks.get("asset_path_signals"):
        asset_scores.append(missing_fraction_score(checks.get("asset_path_signals")))
    elif checks.get("asset_signals"):
        asset_scores.append(bool_score(checks.get("asset_signals")))
    if checks.get("image_alt_signals"):
        asset_scores.append(missing_fraction_score(checks.get("image_alt_signals")))
    asset_scores = [score for score in asset_scores if score is not None]
    scores: dict[str, float | None] = {
        "CCS": change_completion_score(checks),
        "SCS": state_continuity_score(checks),
        "DCS": design_continuity_score(checks),
        "ECS": experience_continuity_score(checks),
        "CACS": mean(asset_scores) if asset_scores else None,
        "RCS": missing_fraction_score(checks.get("regression_text_signals")) if checks.get("regression_text_signals") else missing_fraction_score(checks.get("reference_text_signals")),
        "ICS": bool_score(checks.get("source_change_audit")),
        "BES": browser_experience_score(checks),
    }
    return {
        name: {
            "description": METRIC_DESCRIPTIONS[name],
            "score": None if score is None else round(score, 4),
            "available": score is not None,
        }
        for name, score in scores.items()
    }


def failed_check_names(result: dict[str, Any]) -> list[str]:
    return [check["name"] for check in result.get("checks", []) if not check.get("passed")]


def failed_metric_names(metrics: dict[str, dict[str, Any]]) -> list[str]:
    return [name for name, item in metrics.items() if item["available"] and item["score"] < 1.0]


def score_result(result: dict[str, Any]) -> dict[str, Any]:
    metrics = metric_scores(result)
    available_scores = [item["score"] for item in metrics.values() if item["available"]]
    wcs = bool(result.get("passed"))
    return {
        "task_id": result.get("task_id"),
        "repo_id": result.get("repo_id"),
        "WCS": {
            "description": METRIC_DESCRIPTIONS["WCS"],
            "passed": wcs,
            "score": 1.0 if wcs else 0.0,
        },
        "metrics": metrics,
        "available_metric_count": len(available_scores),
        "mean_available_metric_score": round(mean(available_scores), 4) if available_scores else None,
        "failed_checks": failed_check_names(result),
        "failed_metrics": failed_metric_names(metrics),
    }


def aggregate_metric_scores(items: list[dict[str, Any]]) -> dict[str, Any]:
    aggregate: dict[str, Any] = {}
    for name in METRIC_NAMES:
        values = [
            item["metrics"][name]["score"]
            for item in items
            if item["metrics"][name]["available"]
        ]
        aggregate[name] = {
            "description": METRIC_DESCRIPTIONS[name],
            "available": len(values),
            "mean": round(mean(values), 4) if values else None,
            "perfect": sum(1 for value in values if value == 1.0),
        }
    return aggregate


def build_failure_taxonomy(items: list[dict[str, Any]]) -> dict[str, Any]:
    metric_counts = {name: 0 for name in METRIC_NAMES}
    check_counts: dict[str, int] = {}
    for item in items:
        for name in item["failed_metrics"]:
            metric_counts[name] += 1
        for name in item["failed_checks"]:
            check_counts[name] = check_counts.get(name, 0) + 1
    return {
        "failed_metrics": {name: count for name, count in metric_counts.items() if count},
        "failed_checks": dict(sorted(check_counts.items(), key=lambda pair: (-pair[1], pair[0]))),
    }


def score_report(report_path: Path, output_path: Path) -> dict[str, Any]:
    report = load_json(report_path)
    items = [score_result(result) for result in report.get("results", [])]
    summary = {
        "report_path": str(report_path),
        "total": len(items),
        "wcs_passed": sum(1 for item in items if item["WCS"]["passed"]),
        "wcs_failed": sum(1 for item in items if not item["WCS"]["passed"]),
        "wcs_rate": round(sum(1 for item in items if item["WCS"]["passed"]) / len(items), 4) if items else None,
        "metric_aggregates": aggregate_metric_scores(items),
        "failure_taxonomy": build_failure_taxonomy(items),
        "items": items,
    }
    write_json(output_path, summary)
    return summary


def add_score_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_seed.score_report.json")


def run_score_from_args(args: argparse.Namespace) -> None:
    summary = score_report(args.report, args.output)
    print(
        f"scored {summary['total']} tasks: "
        f"WCS {summary['wcs_passed']} passed, {summary['wcs_failed']} failed, "
        f"rate={summary['wcs_rate']}"
    )
