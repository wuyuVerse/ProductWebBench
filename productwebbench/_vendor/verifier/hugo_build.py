"""Explicit pinned Hugo capture contract, separate from generic dev serving."""
import json
import shlex
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def build_command(contract, project_root):
    from productwebbench.execution.runtime.runability import is_hugo_project, is_hugo_theme_root
    if (not isinstance(contract, dict) or set(contract) != {"engine", "version", "environment"}
            or contract["engine"] != "hugo" or contract["version"] != "0.147.2"
            or contract["environment"] not in {"production", "development"}):
        raise ValueError("Unsupported explicit Hugo capture contract")
    if not is_hugo_project(project_root):
        raise ValueError("Hugo capture contract requires a real Hugo project")
    command = [str(ROOT / "data/productwebbench/tool_node/bin/hugo-0.147"),
               "--environment", contract["environment"]]
    if is_hugo_theme_root(project_root):
        command += ["--source", "exampleSite", "--theme", ".", "--themesDir", ".."]
    return shlex.join(command)


def check_build(states_root, repo_id, assertion):
    try:
        directory = Path(states_root) / repo_id
        state_report = directory / assertion["state"] / "capture_environment_report.json"
        report = json.loads((state_report if state_report.exists() else directory / "capture_report.json").read_text())
        contract = report["capture_build_contract"]
        build = report["build"]
        expected = {"engine": "hugo", "version": assertion["version"], "environment": assertion["environment"]}
        if contract != expected or report.get("repo_id") != repo_id:
            return False, "Hugo build environment or identity mismatch"
        if (report.get("skip_build") is not False or report.get("status") != "passed"
                or type(build.get("exit_code")) is not int or build["exit_code"] != 0
                or build.get("timed_out") is not False):
            return False, "Hugo build/capture did not complete successfully"
        command = shlex.split(build["command"])
        if command[:3] != [str(ROOT / "data/productwebbench/tool_node/bin/hugo-0.147"), "--environment", expected["environment"]]:
            return False, "Hugo build command does not match pinned contract"
        if not (directory / assertion["state"] / "metrics.json").is_file():
            return False, "Hugo build lacks requested browser state"
        return True, "Pinned Hugo build and browser capture passed in " + expected["environment"]
    except (OSError, ValueError, KeyError, TypeError):
        return False, "missing or invalid Hugo build evidence"
