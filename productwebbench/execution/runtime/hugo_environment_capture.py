"""Capture explicitly different Hugo environments from isolated current-source copies."""
import json
import shutil
from pathlib import Path


def capture_environments(repo_record, workspace_root, output_root, state_plan, metadata, options):
    from .browser_state import run_state_capture
    repo_id = repo_record["repo_id"]
    raw = json.loads(Path(state_plan).read_text())
    states = raw[repo_id]
    base = metadata.get("capture_build_contract")
    groups = {}
    for state in states:
        contract = state.get("capture_build_contract",base)
        if (not isinstance(contract,dict) or set(contract)!={"engine","version","environment"}
                or contract["engine"]!="hugo" or contract["version"]!="0.147.2"
                or contract["environment"] not in {"production","development"}):
            raise ValueError("Only explicit pinned Hugo state environments are supported")
        groups.setdefault(contract["environment"],[]).append(state)
    output = Path(output_root).resolve() / repo_id
    output.mkdir(parents=True,exist_ok=True)
    project = Path(metadata["project_root"]).resolve()
    if output.is_relative_to(project):
        raise ValueError("Environment capture output must be outside source")
    reports = {}
    for environment, group in groups.items():
        root = output / "environment_captures" / environment
        root.mkdir(parents=True,exist_ok=False)
        copied = root / "workspace"
        shutil.copytree(project,copied,symlinks=True)
        contract = {"engine":"hugo","version":"0.147.2","environment":environment}
        child_metadata = dict(metadata,project_root=str(copied),workspace=str(copied),capture_build_contract=contract)
        child_states = [{k:v for k,v in state.items() if k!="capture_build_contract"} for state in group]
        plan = root / "state_plan.json"
        plan.write_text(json.dumps({repo_id:child_states}))
        child = run_state_capture(repo_record,workspace_root,root / "states",state_plan=plan,
            prepared_workspace_meta=child_metadata,**options)
        reports[environment] = child
        child_output = root / "states" / repo_id
        for state in group:
            sid = state["state_id"]
            if not isinstance(sid,str) or Path(sid).name!=sid or sid in {".",".."}:
                raise ValueError("Invalid environment state identity")
            if (child_output / sid).is_dir():
                target = output / sid
                shutil.copytree(child_output / sid,target)
                (target / "capture_environment_report.json").write_text(json.dumps(child,indent=2))
    report = {"repo_id":repo_id,"project_root":str(project),"status":"passed" if all(r["status"]=="passed" for r in reports.values()) else "failed",
        "environment_reports":reports,"state_environments":{s["state_id"]:env for env,group in groups.items() for s in group},
        "scope":"Independent current-source Hugo builds; not source or trajectory acceptance"}
    (output / "capture_report.json").write_text(json.dumps(report,indent=2))
    return report
