"""Pinned-interpreter exit-status gate, read from the capture report.

`python_exec` is the subprocess sibling of `hugo_build_success`: the capture
runner executes the named workspace script once, under a pinned interpreter with
a pinned matplotlib/numpy contract, and records the exit status.  Verification
reads that recorded status back — it never shells out, exactly as
`hugo_build.check_build` reads the recorded Hugo exit code rather than rebuilding.

Execution contract (all enforced capture-side, see `repository_evidence`):
    * absolute pinned interpreter, `matplotlib==3.10.1` / `numpy==1.26.4`;
    * `MPLBACKEND=Agg` — a chart script must never need a display;
    * hard wall-clock timeout with the child in its own session, plus
      RLIMIT_AS / RLIMIT_FSIZE / RLIMIT_CORE;
    * outbound network blocked by an injected `sitecustomize` guard and an
      unroutable proxy, mirroring the browser states' `block_external_network`.
"""
from productwebbench._vendor.verifier.repository_evidence import (
    collect_state_checks, load_report, single_record,
    validate_note, validate_timeout, validate_workspace_file,
)

PARAMETERS = {"file", "expected", "timeout_ms", "note"}
PROBE_KEY = "python_exec_checks"
KIND = "python_exec"

# Only the positive form is admitted. An "expected: fail" gate would reward a
# broken script, and no authored plan asks for one.
EXPECTED_VALUES = {"pass"}


def validate_assertion(assertion):
    identity = assertion.get("assertion_id")
    if not isinstance(identity, str) or not identity:
        raise ValueError("python_exec needs an assertion_id")
    expected = assertion.get("expected")
    if expected not in EXPECTED_VALUES:
        raise ValueError("python_exec expected must be 'pass'")
    validate_note(assertion.get("note"))
    return {
        "id": identity,
        "file": validate_workspace_file(assertion.get("file")),
        "expected": expected,
        "timeout_ms": validate_timeout(assertion.get("timeout_ms")),
    }


def validate_probe(probe):
    if not isinstance(probe, dict) or set(probe) != {"id", "file", "expected", "timeout_ms"}:
        raise ValueError("Invalid python_exec probe")
    if not isinstance(probe["id"], str) or not probe["id"]:
        raise ValueError("Invalid python_exec probe id")
    validate_workspace_file(probe["file"])
    if probe["expected"] not in EXPECTED_VALUES:
        raise ValueError("python_exec expected must be 'pass'")
    validate_timeout(probe["timeout_ms"])
    return probe


def observe(project_root, state, probe, runner):
    """`runner` comes from repository_evidence.script_runner — see that factory."""
    run = runner(project_root, probe["file"], probe["timeout_ms"])
    return {"file": probe["file"], "exit_code": run.get("exit_code"),
            "timed_out": bool(run.get("timed_out")),
            "harness_error": run.get("harness_error"),
            "error_tail": (run.get("error") or "")[-1200:] or None}


def collect_checks(project_root, states, runner):
    return collect_state_checks(project_root, states, PROBE_KEY, validate_probe,
                                lambda root, state, probe: observe(root, state, probe, runner))


def check_python_exec(states_root, repo_id, assertion):
    try:
        probe = validate_assertion(assertion)
        report = load_report(states_root, repo_id, assertion.get("state"))
        observation = single_record(report, KIND, assertion.get("state"), probe)
        if observation.get("harness_error"):
            return False, "python_exec: capture harness did not produce a usable run"
        if observation.get("timed_out") is True:
            return False, f"python_exec: {probe['file']} exceeded {probe['timeout_ms']}ms"
        exit_code = observation.get("exit_code")
        if type(exit_code) is not int:
            return False, "python_exec: unusable exit-status evidence"
        if exit_code != 0:
            tail = (observation.get("error_tail") or "").strip().splitlines()[-1:] or [""]
            return False, f"python_exec: {probe['file']} exited {exit_code} — {tail[0][:200]}"
        return True, f"python_exec: {probe['file']} ran to completion under the pinned runtime"
    except (OSError, ValueError, KeyError, TypeError):
        return False, "Invalid or missing python_exec evidence"
