"""Workspace source-text gate, sampled by the capture runner, not the verifier.

`code_contains` asserts that a named workspace file literally contains a needle
at least `min_count` times.  It is the source-tree sibling of
`workspace_files_sha256`: the capture runner reads the file once and records the
occurrence count; verification only reads that record back.

Two needle forms:

    substring: "import scipy"          -> count occurrences of that one string
    any_of: ["import scipy", "from scipy"]
                                       -> count = max over the alternatives

`any_of` exists because a Python import can be spelled two ways for the same
library (`import scipy` vs `from scipy.stats import norm`) and a gate that only
knows one spelling rejects a correct solution.  `max` (not `sum`) keeps the
accepted set as small as possible while still admitting both spellings.
"""
from productwebbench._vendor.verifier.repository_evidence import (
    collect_state_checks, load_report, read_workspace_file, single_record,
    validate_min_count, validate_note, validate_workspace_file,
)

PARAMETERS = {"file", "substring", "any_of", "min_count", "note"}
PROBE_KEY = "code_contains_checks"
KIND = "code_contains"

MAX_NEEDLES = 8
MAX_NEEDLE_CHARS = 400


def validate_needles(assertion):
    substring = assertion.get("substring")
    alternatives = assertion.get("any_of")
    if (substring is None) == (alternatives is None):
        raise ValueError("code_contains needs exactly one of substring / any_of")
    needles = [substring] if alternatives is None else alternatives
    if not isinstance(needles, list) or not 1 <= len(needles) <= MAX_NEEDLES:
        raise ValueError(f"code_contains any_of must list 1..{MAX_NEEDLES} alternatives")
    for needle in needles:
        if not isinstance(needle, str) or not needle or len(needle) > MAX_NEEDLE_CHARS or "\x00" in needle:
            raise ValueError("code_contains needles must be short non-empty strings")
    if len(set(needles)) != len(needles):
        raise ValueError("Duplicate code_contains alternative")
    return needles


def validate_assertion(assertion):
    """assertion (already flattened) -> canonical probe dict."""
    identity = assertion.get("assertion_id")
    if not isinstance(identity, str) or not identity:
        raise ValueError("code_contains needs an assertion_id")
    probe = {
        "id": identity,
        "file": validate_workspace_file(assertion.get("file")),
        "needles": validate_needles(assertion),
        "min_count": validate_min_count(assertion.get("min_count")),
    }
    validate_note(assertion.get("note"))
    return probe


def validate_probe(probe):
    if not isinstance(probe, dict) or set(probe) != {"id", "file", "needles", "min_count"}:
        raise ValueError("Invalid code_contains probe")
    if not isinstance(probe["id"], str) or not probe["id"]:
        raise ValueError("Invalid code_contains probe id")
    validate_workspace_file(probe["file"])
    validate_needles({"any_of": probe["needles"]})
    validate_min_count(probe["min_count"])
    return probe


def observe(project_root, state, probe):
    status, text = read_workspace_file(project_root, probe["file"])
    observation = {"file": probe["file"], "status": status, "count": 0, "per_needle": {}}
    if status != "present":
        return observation
    for needle in probe["needles"]:
        observation["per_needle"][needle] = text.count(needle)
    observation["count"] = max(observation["per_needle"].values())
    observation["bytes"] = len(text)
    return observation


def collect_checks(project_root, states):
    return collect_state_checks(project_root, states, PROBE_KEY, validate_probe, observe)


def check_code_contains(states_root, repo_id, assertion):
    try:
        probe = validate_assertion(assertion)
        report = load_report(states_root, repo_id, assertion.get("state"))
        observation = single_record(report, KIND, assertion.get("state"), probe)
        if observation.get("status") != "present":
            return False, f"code_contains: {probe['file']} is {observation.get('status')}"
        count = observation.get("count")
        if type(count) is not int:
            return False, "code_contains: unusable occurrence evidence"
        if count < probe["min_count"]:
            return False, (f"code_contains: {probe['file']} holds {count} of the required "
                           f"{probe['min_count']} occurrence(s) of {probe['needles']!r}")
        return True, f"code_contains: {probe['file']} holds {count} occurrence(s) of {probe['needles']!r}"
    except (OSError, ValueError, KeyError, TypeError):
        return False, "Invalid or missing code_contains evidence"
