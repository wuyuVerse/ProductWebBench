"""Exact workspace path absence, sampled by the capture runner, not the page."""
import json
import stat
import hashlib
import re
from pathlib import Path, PurePosixPath


def validate_paths(paths):
    if not isinstance(paths, list) or not 1 <= len(paths) <= 100:
        raise ValueError("Expected 1..100 exact workspace paths")
    for path in paths:
        if (not isinstance(path, str) or not path or len(path) > 1024 or "\\" in path or "\x00" in path
                or PurePosixPath(path).is_absolute() or any(p in {"", ".", ".."} for p in path.split("/"))):
            raise ValueError("Invalid relative workspace path")
    if len(set(paths)) != len(paths):
        raise ValueError("Duplicate workspace paths")
    return paths


def observe_paths(project_root, paths):
    validate_paths(paths)
    root = Path(project_root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Missing or symlink workspace root")
    result = []
    for path in paths:
        current = root
        status = "present"
        for component in path.split("/"):
            current = current / component
            try:
                mode = current.lstat().st_mode
            except FileNotFoundError:
                status = "absent"
                break
            if stat.S_ISLNK(mode):
                status = "symlink"
                break
            if current != root / path and not stat.S_ISDIR(mode):
                status = "invalid_parent"
                break
        result.append({"path":path,"status":status})
    return result


def validate_files(files):
    if not isinstance(files,dict):
        raise ValueError("Expected relative file/hash mapping")
    validate_paths(list(files))
    if any(not isinstance(digest,str) or not re.fullmatch(r"[0-9a-f]{64}",digest) for digest in files.values()):
        raise ValueError("Expected lowercase SHA-256 file identities")
    return files


def observe_files(project_root, files):
    validate_files(files)
    observations=observe_paths(project_root,list(files))
    total=0
    for item in observations:
        if item["status"]!="present":
            continue
        file=Path(project_root) / item["path"]
        info=file.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size>32*1024*1024:
            item["status"]="invalid_file"
            continue
        total+=info.st_size
        if total>64*1024*1024:
            raise ValueError("File identity capture exceeds size bound")
        with file.open("rb") as stream:
            raw=stream.read(32*1024*1024+1)
        if len(raw)!=info.st_size:
            raise ValueError("File changed during identity capture")
        item["sha256"]=hashlib.sha256(raw).hexdigest()
    return observations


def collect_checks(project_root, states):
    records = []
    for state in states:
        for probe in state.get("workspace_path_checks", []):
            if set(probe) not in ({"id", "paths"},{"id","files"}) or not isinstance(probe["id"], str):
                raise ValueError("Invalid workspace path probe")
            record = {"state":state["state_id"],"probe":probe,
                "observation":(observe_files(project_root,probe["files"]) if "files" in probe
                               else observe_paths(project_root, probe["paths"]))}
            if any(r["state"]==record["state"] and r["probe"]["id"]==probe["id"] for r in records):
                raise ValueError("Duplicate workspace path probe")
            records.append(record)
    return records


def check_paths(states_root, repo_id, assertion):
    try:
        file_check=assertion.get("gate")=="workspace_files_sha256"
        if file_check:
            files=validate_files(assertion["files"])
            probe={"id":assertion["assertion_id"],"files":files}
            expected=[{"path":path,"status":"present","sha256":digest} for path,digest in files.items()]
        else:
            paths = validate_paths(assertion["paths"])
            probe = {"id":assertion["assertion_id"],"paths":paths}
            expected = [{"path":path,"status":"absent"} for path in paths]
        directory = Path(states_root) / repo_id
        state_report = directory / assertion["state"] / "capture_environment_report.json"
        report = json.loads((state_report if state_report.exists() else directory / "capture_report.json").read_text())
        if report["repo_id"] != repo_id or report["status"] != "passed":
            return False,"Workspace capture identity or status mismatch"
        for phase in ("before", "after"):
            records = report["workspace_path_checks"][phase]
            matches = [r for r in records if r["state"]==assertion["state"] and r["probe"]["id"]==probe["id"]]
            if len(matches)!=1 or matches[0]["probe"]!=probe or matches[0]["observation"]!=expected:
                return False,"Workspace file identity mismatch " + phase + " capture" if file_check else "Declared workspace paths not proven absent " + phase + " capture"
        return True,"Exact file SHA-256 identities retained before and after capture" if file_check else "Exact workspace paths absent before and after capture"
    except (OSError,KeyError,TypeError,ValueError):
        return False,"Invalid or missing workspace path evidence"
