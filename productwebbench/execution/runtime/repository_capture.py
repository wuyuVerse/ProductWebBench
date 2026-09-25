"""Browserless capture for repository-scoped states.

A state whose `capture_mode` is `"repository"` carries no page: its gates
(`code_contains`, `python_exec`, `plot_visual_match`) look at the source tree and
at what the source tree *does* when executed, not at a rendered DOM.  Driving
such a state through `run_state_capture` would install dependencies, start a dev
server and launch Chromium for a page that does not exist, and the capture would
abort long before any gate ran.

This module is the alternate capture path — same role as
`hugo_environment_capture.capture_environments`, dispatched from the same place
in `run_state_capture`.  It writes a `capture_report.json` in the identical
shape the workspace gates already read (`repo_id` / `status` / per-probe
before-and-after style records), so no verifier learns a second convention.
"""
import json
from pathlib import Path

CAPTURE_MODE = "repository"


def is_repository_state(state):
    return isinstance(state, dict) and state.get("capture_mode") == CAPTURE_MODE


def capture_repository_states(repo_record, output_root, state_plan, metadata):
    from productwebbench._vendor.verifier import code_contains, plot_visual_match, python_exec
    from productwebbench._vendor.verifier.repository_evidence import runtime_contract, script_runner

    repo_id = repo_record["repo_id"]
    states = json.loads(Path(state_plan).read_text())[repo_id]
    project_root = Path(metadata["project_root"]).resolve()
    output_dir = (Path(output_root) / repo_id).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if output_dir.is_relative_to(project_root):
        raise ValueError("Repository capture output must be outside the graded source")

    report = {
        "repo_id": repo_id,
        "project_root": str(project_root),
        "capture_mode": CAPTURE_MODE,
        "status": "unknown",
        "failure_stage": None,
        "states_dir": str(output_dir),
        "scope": ("Source-tree gates only: file contents, pinned-interpreter exit status and "
                  "rendered-figure similarity. No browser, no dev server, no page evidence."),
    }
    contract = runtime_contract()
    report["repository_runtime"] = contract
    if not contract.get("pinned"):
        report["status"] = "failed"
        report["failure_stage"] = "repository_runtime"
        (output_dir / "capture_report.json").write_text(json.dumps(report, indent=2))
        return report

    if not project_root.is_dir():
        report["status"] = "failed"
        report["failure_stage"] = "project_root"
        (output_dir / "capture_report.json").write_text(json.dumps(report, indent=2))
        return report

    runner = script_runner(output_dir / "repository_runs")
    try:
        checks = {
            "code_contains": code_contains.collect_checks(project_root, states),
            "python_exec": python_exec.collect_checks(project_root, states, runner),
            "plot_visual_match": plot_visual_match.collect_checks(project_root, states, runner),
        }
    except (OSError, ValueError, KeyError, TypeError) as error:
        report["status"] = "failed"
        report["failure_stage"] = "repository_checks"
        report["error"] = f"{type(error).__name__}: {error}"[:300]
        (output_dir / "capture_report.json").write_text(json.dumps(report, indent=2))
        return report

    report["repository_checks"] = checks
    report["status"] = "passed"
    for state in states:
        state_id = state["state_id"]
        if Path(state_id).name != state_id or state_id in {".", ".."}:
            raise ValueError("Invalid repository state identity")
        directory = output_dir / state_id
        directory.mkdir(parents=True, exist_ok=True)
        # `repository_capture.json` is to a repository state what `metrics.json`
        # is to a browser state: the marker that this state was actually sampled.
        (directory / "repository_capture.json").write_text(json.dumps({
            "repo_id": repo_id, "state_id": state_id, "capture_mode": CAPTURE_MODE,
            "probe_counts": {kind: sum(1 for r in records if r["state"] == state_id)
                             for kind, records in checks.items()},
        }, indent=2))
    (output_dir / "capture_report.json").write_text(json.dumps(report, indent=2))
    return report
