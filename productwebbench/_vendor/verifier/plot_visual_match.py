"""Rendered-figure similarity gate against a vendored reference image.

The image algorithm is NOT reimplemented here: the score comes from
`productwebbench.core.visual.ssim_score`, the same deterministic numpy-only SSIM
the D3 visual-regression gate registers in `_VISUAL_METRICS`.  This module only
decides *what* to compare and records the evidence:

    * the submission side is the figure produced by executing the workspace
      script under the pinned offline sandbox (the very same run that
      `python_exec` grades), rendered at the pinned `RENDER_DPI` with a tight
      bounding box;
    * the reference side is a file vendored with the bench under
      `method/benches/{chartmimic,plot2code/data}/`, identified by its SHA-256 so
      the graded comparison cannot be pointed at a different image afterwards.

`min_ssim` is taken from the task exactly as authored.  It is never lowered.
"""
from productwebbench._vendor.verifier.repository_evidence import (
    RENDER_DPI, collect_state_checks, file_sha256, load_report, resolve_reference_image,
    single_record, validate_note, validate_timeout, validate_workspace_file,
)

PARAMETERS = {"file", "ref_image", "min_ssim", "timeout_ms", "note"}
PROBE_KEY = "plot_visual_match_checks"
KIND = "plot_visual_match"


def validate_min_ssim(value):
    if type(value) is not float and type(value) is not int:
        raise ValueError("min_ssim must be a number")
    if isinstance(value, bool) or not 0.0 < float(value) <= 1.0:
        raise ValueError("min_ssim must be in (0, 1]")
    return float(value)


def validate_assertion(assertion):
    identity = assertion.get("assertion_id")
    if not isinstance(identity, str) or not identity:
        raise ValueError("plot_visual_match needs an assertion_id")
    validate_note(assertion.get("note"))
    reference, _path = resolve_reference_image(assertion.get("ref_image"))
    return {
        "id": identity,
        "file": validate_workspace_file(assertion.get("file")),
        "ref_image": reference,
        "min_ssim": validate_min_ssim(assertion.get("min_ssim")),
        "timeout_ms": validate_timeout(assertion.get("timeout_ms")),
        "render_dpi": RENDER_DPI,
    }


def validate_probe(probe):
    expected = {"id", "file", "ref_image", "min_ssim", "timeout_ms", "render_dpi"}
    if not isinstance(probe, dict) or set(probe) != expected:
        raise ValueError("Invalid plot_visual_match probe")
    if not isinstance(probe["id"], str) or not probe["id"]:
        raise ValueError("Invalid plot_visual_match probe id")
    validate_workspace_file(probe["file"])
    validate_min_ssim(probe["min_ssim"])
    validate_timeout(probe["timeout_ms"])
    if probe["render_dpi"] != RENDER_DPI:
        raise ValueError("plot_visual_match render contract is pinned")
    _reference, _path = _reference_from_probe(probe)
    return probe


def _reference_from_probe(probe):
    """Probe carries the already-rooted path, e.g. `chartmimic/direct_600/bar_1.png`."""
    from productwebbench._vendor.verifier.repository_evidence import BENCH_ROOT, REFERENCE_ROOTS
    stored = probe["ref_image"]
    for root in REFERENCE_ROOTS:
        prefix = root + "/"
        if stored.startswith(prefix):
            relative = stored[len(prefix):]
            validate_workspace_file(relative)
            candidate = BENCH_ROOT / root / relative
            if candidate.is_file() and not candidate.is_symlink():
                return stored, candidate
    raise ValueError(f"Reference image is not vendored with the bench: {stored}")


def observe(project_root, state, probe, runner):
    _reference, reference_path = _reference_from_probe(probe)
    observation = {"file": probe["file"], "ref_image": probe["ref_image"],
                   "ref_sha256": file_sha256(reference_path),
                   "render_dpi": RENDER_DPI, "available": False, "ssim": None,
                   "reason": None, "rendered": None}
    run = runner(project_root, probe["file"], probe["timeout_ms"])
    if run.get("harness_error"):
        observation["reason"] = "capture harness did not produce a usable run"
        return observation
    if run.get("timed_out"):
        observation["reason"] = "script exceeded its execution budget"
        return observation
    if run.get("exit_code") != 0:
        observation["reason"] = "script did not run to completion"
        return observation
    rendered = run.get("render")
    if not rendered:
        observation["reason"] = run.get("render_error") or "script produced no matplotlib figure"
        return observation
    from productwebbench.core.visual import ssim_score
    score = ssim_score(reference_path, rendered)
    if not score.get("available"):
        observation["reason"] = score.get("reason") or "ssim unavailable"
        return observation
    observation.update(available=True, ssim=score["ssim"], rendered=str(rendered))
    return observation


def collect_checks(project_root, states, runner):
    return collect_state_checks(project_root, states, PROBE_KEY, validate_probe,
                                lambda root, state, probe: observe(root, state, probe, runner))


def check_plot_visual_match(states_root, repo_id, assertion):
    try:
        probe = validate_assertion(assertion)
        _reference, reference_path = _reference_from_probe(probe)
        report = load_report(states_root, repo_id, assertion.get("state"))
        observation = single_record(report, KIND, assertion.get("state"), probe)
        if observation.get("ref_sha256") != file_sha256(reference_path):
            return False, "plot_visual_match: reference image identity mismatch"
        if observation.get("render_dpi") != RENDER_DPI:
            return False, "plot_visual_match: render contract mismatch"
        if observation.get("available") is not True:
            return False, f"plot_visual_match: {observation.get('reason') or 'no comparable render'}"
        score = observation.get("ssim")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            return False, "plot_visual_match: unusable similarity evidence"
        if float(score) < probe["min_ssim"]:
            return False, (f"plot_visual_match: SSIM {float(score):.4f} against {probe['ref_image']} "
                           f"is below the required {probe['min_ssim']}")
        return True, (f"plot_visual_match: SSIM {float(score):.4f} against {probe['ref_image']} "
                      f"meets the required {probe['min_ssim']}")
    except (OSError, ValueError, KeyError, TypeError):
        return False, "Invalid or missing plot_visual_match evidence"
