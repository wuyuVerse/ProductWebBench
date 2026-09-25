from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

from ..core.config import DEFAULT_OUTPUT_ROOT
from ..core.formal_data_guard import assert_not_under_formal_task_root
from ..core.io_utils import write_json
from ..taxonomy.capability import CHANGE_REGIME, CONSTRUCTION_REGIME
from .base import ContinuityRegime, RegimeContext
from .change.scoring import ChangeRegime
from .construction.scoring import ConstructionRegime


REGIME_PLUGINS: dict[str, ContinuityRegime] = {
    CHANGE_REGIME: ChangeRegime(),
    CONSTRUCTION_REGIME: ConstructionRegime(),
}


def assert_nonformal_output(path: Path | None, *, purpose: str) -> None:
    if path is not None:
        try:
            assert_not_under_formal_task_root(path, purpose=purpose)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def list_regimes() -> list[dict[str, Any]]:
    return [
        {
            "key": regime.key,
            "label": regime.label,
            "primary_metrics": list(regime.primary_metrics),
            "plugin_class": regime.__class__.__name__,
            "module_wrappers": regime_module_wrappers(regime.key),
            "interfaces": {
                "author_task": callable(getattr(regime, "author_task", None)),
                "build_verifier": callable(getattr(regime, "build_verifier", None)),
                "score": callable(getattr(regime, "score", None)),
                "package": callable(getattr(regime, "package", None)),
            },
        }
        for regime in sorted(REGIME_PLUGINS.values(), key=lambda item: item.key)
    ]


def regime_module_wrappers(key: str) -> dict[str, bool]:
    def has_module(name: str) -> bool:
        return importlib.util.find_spec(name) is not None

    if key == CHANGE_REGIME:
        return {
            "authoring": has_module("sitecontinuum.regimes.change.authoring"),
            "verifier": has_module("sitecontinuum.regimes.change.verifier"),
            "scoring": has_module("sitecontinuum.regimes.change.scoring"),
        }
    if key == CONSTRUCTION_REGIME:
        return {
            "authoring": has_module("sitecontinuum.regimes.construction.authoring"),
            "trajectory_verifier": has_module("sitecontinuum.regimes.construction.trajectory_verifier"),
            "scoring": has_module("sitecontinuum.regimes.construction.scoring"),
        }
    return {}


def get_regime(key: str) -> ContinuityRegime:
    try:
        return REGIME_PLUGINS[key]
    except KeyError as exc:
        raise ValueError(f"unknown regime {key!r}; available regimes: {', '.join(sorted(REGIME_PLUGINS))}") from exc


def score_regime(key: str, report: dict[str, Any]) -> dict[str, Any]:
    regime = get_regime(key)
    score = regime.score(report)
    return {
        "schema_version": "2026-06-19",
        "artifact_type": "regime_score",
        "regime": regime.key,
        "primary_metrics": list(regime.primary_metrics),
        "score": score,
    }


def parse_key_values(values: list[str] | None, *, field_name: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for raw in values or []:
        if "=" not in raw:
            raise ValueError(f"{field_name} entry must use key=value: {raw}")
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"{field_name} entry has empty key: {raw}")
        parsed[key] = value.strip()
    return parsed


def context_from_args(
    *,
    task_id: str,
    repo_id: str,
    workspace_root: Path,
    states_root: Path,
    output_root: Path,
) -> RegimeContext:
    return RegimeContext(
        task_id=task_id,
        repo_id=repo_id,
        workspace_root=workspace_root,
        states_root=states_root,
        output_root=output_root,
    )


def author_regime_task(
    *,
    key: str,
    task_id: str,
    repo_id: str,
    workspace_root: Path,
    states_root: Path,
    output_root: Path,
    target: dict[str, Any] | None = None,
) -> dict[str, Any]:
    regime = get_regime(key)
    plan = regime.author_task(
        context_from_args(
            task_id=task_id,
            repo_id=repo_id,
            workspace_root=workspace_root,
            states_root=states_root,
            output_root=output_root,
        ),
        target=target,
    )
    return {
        "schema_version": "2026-06-19",
        "artifact_type": "regime_authoring_plan",
        "formal_task_record": False,
        "generated_formal_task": False,
        "regime": regime.key,
        "primary_metrics": list(regime.primary_metrics),
        "plan": plan,
    }


def build_regime_verifier(
    *,
    key: str,
    task_id: str,
    repo_id: str,
    workspace_root: Path,
    states_root: Path,
    output_root: Path,
    task: dict[str, Any] | None = None,
) -> dict[str, Any]:
    regime = get_regime(key)
    plan = regime.build_verifier(
        context_from_args(
            task_id=task_id,
            repo_id=repo_id,
            workspace_root=workspace_root,
            states_root=states_root,
            output_root=output_root,
        ),
        task=task,
    )
    return {
        "schema_version": "2026-06-19",
        "artifact_type": "regime_verifier_plan",
        "formal_task_record": False,
        "generated_verifier_spec": False,
        "regime": regime.key,
        "primary_metrics": list(regime.primary_metrics),
        "plan": plan,
    }


def package_regime(
    *,
    key: str,
    task_id: str,
    repo_id: str,
    workspace_root: Path,
    states_root: Path,
    output_root: Path,
) -> dict[str, Any]:
    regime = get_regime(key)
    context = RegimeContext(
        task_id=task_id,
        repo_id=repo_id,
        workspace_root=workspace_root,
        states_root=states_root,
        output_root=output_root,
    )
    package = regime.package(context)
    return {
        "schema_version": "2026-06-19",
        "artifact_type": "regime_package_metadata",
        "regime": regime.key,
        "primary_metrics": list(regime.primary_metrics),
        "package": package,
    }


def add_context_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--regime", choices=sorted(REGIME_PLUGINS), required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--repo-id", required=True)
    parser.add_argument("--workspace-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "workspaces")
    parser.add_argument("--states-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "states")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "bench")


def add_list_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", type=Path, default=None)


def run_list_from_args(args: argparse.Namespace) -> None:
    report = {
        "schema_version": "2026-06-19",
        "artifact_type": "regime_registry",
        "regime_count": len(REGIME_PLUGINS),
        "regimes": list_regimes(),
    }
    if args.output:
        assert_nonformal_output(args.output, purpose="Regime registry listing")
        write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


def add_score_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--regime", choices=sorted(REGIME_PLUGINS), required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "artifacts" / "regime_score.json")


def run_score_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.output, purpose="Regime score report")
    report = load_json(args.report)
    scored = score_regime(args.regime, report)
    write_json(args.output, scored)
    print(f"scored {args.regime} report with {', '.join(scored['primary_metrics'])} to {args.output}")


def add_author_args(parser: argparse.ArgumentParser) -> None:
    add_context_args(parser)
    parser.add_argument("--target", action="append", default=[], help="Target metadata as key=value; repeatable.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "artifacts" / "regime_authoring_plan.json")


def run_author_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.output, purpose="Regime authoring plan")
    try:
        plan = author_regime_task(
            key=args.regime,
            task_id=args.task_id,
            repo_id=args.repo_id,
            workspace_root=args.workspace_root,
            states_root=args.states_root,
            output_root=args.output_root,
            target=parse_key_values(args.target, field_name="target"),
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    write_json(args.output, plan)
    print(f"wrote non-formal {args.regime} authoring plan for {args.task_id} to {args.output}")


def add_verifier_args(parser: argparse.ArgumentParser) -> None:
    add_context_args(parser)
    parser.add_argument("--task", type=Path, default=None, help="Optional inspected task JSON to summarize; never used to auto-accept data.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "artifacts" / "regime_verifier_plan.json")


def run_verifier_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.output, purpose="Regime verifier plan")
    task = load_json(args.task) if args.task else None
    plan = build_regime_verifier(
        key=args.regime,
        task_id=args.task_id,
        repo_id=args.repo_id,
        workspace_root=args.workspace_root,
        states_root=args.states_root,
        output_root=args.output_root,
        task=task,
    )
    write_json(args.output, plan)
    print(f"wrote non-formal {args.regime} verifier plan for {args.task_id} to {args.output}")


def add_package_args(parser: argparse.ArgumentParser) -> None:
    add_context_args(parser)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT / "artifacts" / "regime_package_metadata.json")


def run_package_from_args(args: argparse.Namespace) -> None:
    assert_nonformal_output(args.output, purpose="Regime package metadata")
    package = package_regime(
        key=args.regime,
        task_id=args.task_id,
        repo_id=args.repo_id,
        workspace_root=args.workspace_root,
        states_root=args.states_root,
        output_root=args.output_root,
    )
    write_json(args.output, package)
    print(f"wrote {args.regime} package metadata for {args.task_id} to {args.output}")
