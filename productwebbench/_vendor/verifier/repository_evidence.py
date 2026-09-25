"""Shared capture-time evidence plumbing for repository-scoped (non-browser) gates.

Three gates — `code_contains`, `python_exec`, `plot_visual_match` — assert about
the *source tree*, not about a rendered page.  They follow exactly the contract
the existing workspace gates already use (`workspace_paths.py` /
`hugo_build.py`):

    * the CAPTURE runner samples the workspace once and writes what it saw into
      `capture_report.json`;
    * the VERIFIER re-derives the probe from the assertion and compares it with
      the recorded probe, then reads the recorded observation.  It never opens
      the live workspace, so a verification pass cannot be influenced by edits
      that landed after the capture it is grading.

The interpreter is pinned the same way `hugo_build.build_command` pins the Hugo
binary: one absolute path plus an exact library contract recorded in the report
and re-checked at verify time.
"""
import hashlib
import json
import os
import resource
import shutil
import signal
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[3]
BENCH_ROOT = ROOT / "method/benches"

# Reference figures ship vendored with the bench.  A `ref_image` is resolved
# against these roots in order; the first existing file wins.  Nothing outside
# them is reachable (the path itself is validated as a relative workspace path).
REFERENCE_ROOTS = ("chartmimic", "plot2code/data")

# Pinned execution contract.  `interpreter` is an absolute path, exactly like
# hugo_build pins `tool_node/bin/hugo-0.147`; the library versions are recorded
# in the capture report and re-checked by every verifier read, so evidence
# produced under a different stack is rejected instead of silently trusted.
PINNED_INTERPRETER = "/usr/bin/python3"
PINNED_PYTHON = "3.12"
# One entry per library, each an ordered tuple of ACCEPTED versions. The dev
# machine ships matplotlib 3.10.1 and the production pod image ships 3.10.3,
# and a singleton pin turned that patch drift into a hard stop: measured on the
# 2026-09-22 chartmimic canary, 109 of 109 capture reports came back
# {"matplotlib": "3.10.3", "pinned": false, "failure_stage": "repository_runtime"}
# -- the capture exited cleanly having refused to start, so not one gate was
# ever evaluated and the solver was told its dev server had crashed. The set
# stays explicit, so evidence from an unvetted stack is still rejected; it just
# no longer has to be a singleton. Widen it only after re-measuring the
# ground-truth SSIM floor on the new version.
PINNED_LIBRARIES = {"matplotlib": ("3.10.1", "3.10.3"), "numpy": ("1.26.4",)}

# Deterministic render contract for plot_visual_match.  150 dpi + tight bbox is
# what reproduces the vendored ChartMimic reference PNGs most faithfully
# (measured: ground-truth SSIM min 0.888 / mean 0.928 over a 14-slot sample,
# versus 0.784 / 0.873 at 100 dpi).  Never tune this per task.
RENDER_DPI = 150

DEFAULT_TIMEOUT_MS = 60000
MIN_TIMEOUT_MS = 1000
MAX_TIMEOUT_MS = 600000
# Address space, not resident memory: numpy/matplotlib reserve far more virtual
# address space than they touch, and a 2 GiB cap made the pinned stack raise
# MemoryError during import. 4 GiB still bounds a runaway script.
MEMORY_LIMIT_BYTES = 4 * 1024 * 1024 * 1024
FILE_LIMIT_BYTES = 64 * 1024 * 1024
MAX_SOURCE_BYTES = 32 * 1024 * 1024

# Imported by the sandboxed interpreter through PYTHONPATH before any user code
# runs.  This is an in-process guard, not a kernel network namespace: it is the
# strongest block available without extra privileges, and it is the same intent
# as the capture-side `block_external_network` flag used for browser states.
_SITECUSTOMIZE = '''\
"""Injected by repository capture: refuse outbound network access."""
import socket

_LOCAL = {"127.0.0.1", "::1", "localhost", ""}


class _Blocked(OSError):
    pass


def _local(address):
    try:
        host = address[0]
    except (TypeError, IndexError):
        return False
    return host in _LOCAL


_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex
_create = socket.create_connection


def connect(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6) and not _local(address):
        raise _Blocked("external network access is blocked during repository capture")
    return _connect(self, address)


def connect_ex(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6) and not _local(address):
        raise _Blocked("external network access is blocked during repository capture")
    return _connect_ex(self, address)


def create_connection(address, *args, **kwargs):
    if not _local(address):
        raise _Blocked("external network access is blocked during repository capture")
    return _create(address, *args, **kwargs)


socket.socket.connect = connect
socket.socket.connect_ex = connect_ex
socket.create_connection = create_connection
'''

# Runs the workspace script in the sandbox and reports both what `python_exec`
# needs (exit status) and what `plot_visual_match` needs (a rendered figure),
# so one subprocess serves both gates for the same file.
_RUNNER = '''\
"""Injected by repository capture: run one workspace script, record the figure."""
import json
import os
import sys
import traceback

os.environ.setdefault("MPLBACKEND", "Agg")
target, out_json, out_png, dpi = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
record = {"exit_code": 0, "error": None, "render": None, "render_error": None, "runtime": {}}
pyplot = None
figures = []
try:
    import matplotlib
    matplotlib.use("Agg", force=True)
    import matplotlib.figure
    import matplotlib.pyplot as pyplot
    import numpy
    record["runtime"] = {"python": "%d.%d" % sys.version_info[:2],
                         "matplotlib": matplotlib.__version__,
                         "numpy": numpy.__version__}
    _savefig = matplotlib.figure.Figure.savefig

    def savefig(self, *args, **kwargs):
        if self not in figures:
            figures.append(self)
        return _savefig(self, *args, **kwargs)

    matplotlib.figure.Figure.savefig = savefig
except BaseException:
    record["runtime"] = {"python": "%d.%d" % sys.version_info[:2]}
    record["error"] = traceback.format_exc()[-2000:]
    record["exit_code"] = 1

if record["exit_code"] == 0:
    with open(target, "rb") as stream:
        source = stream.read()
    try:
        exec(compile(source, target, "exec"), {"__name__": "__main__", "__file__": target})
    except SystemExit as exit_request:
        if exit_request.code not in (None, 0):
            record["exit_code"] = 1
            record["error"] = "SystemExit: %r" % (exit_request.code,)
    except BaseException:
        record["exit_code"] = 1
        record["error"] = traceback.format_exc()[-2000:]

if pyplot is not None:
    figure = None
    if figures:
        figure = figures[0]
    elif pyplot.get_fignums():
        figure = pyplot.figure(sorted(pyplot.get_fignums())[0])
    if figure is not None:
        try:
            figure.savefig(out_png, dpi=dpi, bbox_inches="tight", facecolor="white")
            record["render"] = out_png
        except BaseException:
            record["render_error"] = traceback.format_exc()[-1000:]

with open(out_json, "w") as stream:
    json.dump(record, stream)
'''


def validate_workspace_file(path):
    """Same relative-path rules the workspace path gates already enforce."""
    if (not isinstance(path, str) or not path or len(path) > 1024 or "\\" in path or "\x00" in path
            or PurePosixPath(path).is_absolute()
            or any(part in {"", ".", ".."} for part in path.split("/"))):
        raise ValueError("Invalid relative workspace path")
    return path


def validate_timeout(value):
    if value is None:
        return DEFAULT_TIMEOUT_MS
    if type(value) is not int or not MIN_TIMEOUT_MS <= value <= MAX_TIMEOUT_MS:
        raise ValueError("timeout_ms must be an integer between 1000 and 600000")
    return value


def validate_note(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 500:
        raise ValueError("note must be a short string")
    return value


def validate_min_count(value):
    if value is None:
        return 1
    if type(value) is not int or value < 1:
        raise ValueError("min_count must be a positive integer")
    return value


def resolve_reference_image(ref_image):
    """`ref_image` -> (root-relative posix path, absolute path) or raise."""
    validate_workspace_file(ref_image)
    for root in REFERENCE_ROOTS:
        candidate = BENCH_ROOT / root / ref_image
        if candidate.is_file() and not candidate.is_symlink():
            return f"{root}/{ref_image}", candidate
    raise ValueError(f"Reference image is not vendored with the bench: {ref_image}")


def file_sha256(path, limit=MAX_SOURCE_BYTES):
    digest = hashlib.sha256()
    total = 0
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            total += len(chunk)
            if total > limit:
                raise ValueError("File exceeds the capture size bound")
            digest.update(chunk)
    return digest.hexdigest()


def read_workspace_file(project_root, relative):
    """(status, text). Never follows a symlink out of the workspace."""
    root = Path(project_root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Missing or symlink workspace root")
    current = root
    for component in relative.split("/"):
        current = current / component
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            return "absent", None
        if stat.S_ISLNK(mode):
            return "symlink", None
        if current != root / relative and not stat.S_ISDIR(mode):
            return "invalid_parent", None
    info = current.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SOURCE_BYTES:
        return "invalid_file", None
    with current.open("rb") as stream:
        raw = stream.read(MAX_SOURCE_BYTES + 1)
    if len(raw) != info.st_size:
        raise ValueError("File changed during capture")
    return "present", raw.decode("utf-8", errors="replace")


def runtime_contract():
    """What the pinned interpreter actually offers right now."""
    probe = ("import json,sys;"
             "out={'python':'%d.%d'%sys.version_info[:2]};"
             "\nfor name in ('matplotlib','numpy'):\n"
             "    try:\n"
             "        out[name]=__import__(name).__version__\n"
             "    except Exception as error:\n"
             "        out[name]=None\n"
             "print(json.dumps(out))")
    try:
        result = subprocess.run([PINNED_INTERPRETER, "-c", probe], capture_output=True,
                                text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as error:
        return {"available": False, "reason": f"{type(error).__name__}: {error}"[:200]}
    if result.returncode != 0:
        return {"available": False, "reason": result.stderr[-200:]}
    try:
        found = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"available": False, "reason": "unreadable interpreter probe"}
    contract = {"available": True, "interpreter": PINNED_INTERPRETER, **found}
    contract["pinned"] = (found.get("python") == PINNED_PYTHON
                          and all(found.get(name) in accepted
                                  for name, accepted in PINNED_LIBRARIES.items()))
    return contract


def contract_matches(recorded):
    """Verifier-side check that the evidence came from the pinned stack."""
    return (isinstance(recorded, dict) and recorded.get("available") is True
            and recorded.get("pinned") is True
            and recorded.get("interpreter") == PINNED_INTERPRETER
            and recorded.get("python") == PINNED_PYTHON
            and all(recorded.get(name) in accepted
                    for name, accepted in PINNED_LIBRARIES.items()))


def _limits():  # pragma: no cover - runs in the forked child
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
    resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_LIMIT_BYTES, FILE_LIMIT_BYTES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def warm_font_cache(config_dir, timeout_sec=600):
    """Build matplotlib's font list once, outside any probe's time budget.

    A cold MPLCONFIGDIR costs tens of seconds on first import. Charging that to
    the script's declared `timeout_ms` turns a pinned 60 s budget into a coin
    flip, so the capture driver warms one shared cache before any probe runs.
    The cache affects start-up cost only, never what is drawn.
    """
    config_dir = Path(config_dir)
    config_dir.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, MPLCONFIGDIR=str(config_dir), MPLBACKEND="Agg",
                       PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1",
                       OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    try:
        subprocess.run([PINNED_INTERPRETER, "-c",
                        "import matplotlib;matplotlib.use('Agg');"
                        "import matplotlib.pyplot as plt;plt.figure();plt.plot([0,1],[0,1])"],
                       env=environment, capture_output=True, timeout=timeout_sec)
    except (OSError, subprocess.SubprocessError):
        pass
    return config_dir


def run_script(project_root, relative, run_dir, timeout_ms, mpl_config_dir=None):
    """Execute one workspace script under the pinned, offline sandbox.

    Returns the runner record plus `timed_out`. The script runs with
    `cwd=project_root` (so its own relative reads/writes behave as its author
    intended) but every harness artifact is written under `run_dir`, which lives
    in the capture output tree, never in the graded workspace.
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    sandbox = run_dir / "sandbox"
    sandbox.mkdir(exist_ok=True)
    (sandbox / "sitecustomize.py").write_text(_SITECUSTOMIZE)
    runner = sandbox / "repository_script_runner.py"
    runner.write_text(_RUNNER)
    out_json = run_dir / "run.json"
    out_png = run_dir / "render.png"
    for stale in (out_json, out_png):
        if stale.exists():
            stale.unlink()
    target = (Path(project_root) / relative).resolve()
    environment = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(run_dir),
        "TMPDIR": str(run_dir),
        "MPLBACKEND": "Agg",
        "MPLCONFIGDIR": str(mpl_config_dir or (run_dir / "mpl")),
        "PYTHONPATH": str(sandbox),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
        # Single-threaded BLAS. Without this, numpy/scipy size their thread
        # pools from the host core count (128 on these pods): a two-second KDE
        # then oversubscribes the box and blows past its timeout, and one capture
        # probe silently consumes far more than the per-pod vCPU budget. It also
        # makes the recorded evidence reproducible run to run.
        "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1", "VECLIB_MAXIMUM_THREADS": "1",
        # Belt and braces next to the sitecustomize guard: libraries that honour
        # proxy configuration get an unroutable one.
        "http_proxy": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9",
        "no_proxy": "", "NO_PROXY": "",
    }
    Path(environment["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)
    # No -I/-E: the network guard is delivered as `sitecustomize` through
    # PYTHONPATH, which an isolated interpreter would drop. PYTHONNOUSERSITE and
    # the scrubbed environment above give the same isolation without losing it.
    command = [PINNED_INTERPRETER, str(runner), str(target), str(out_json),
               str(out_png), str(RENDER_DPI)]
    timed_out = False
    # Own Popen rather than subprocess.run: the child gets its own session, and
    # on timeout the whole process GROUP is killed. subprocess.run would only
    # signal the direct child and leave anything it forked running on the pod.
    try:
        process = subprocess.Popen(command, cwd=str(project_root), env=environment,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True, preexec_fn=_limits)
    except OSError as error:
        return {"harness_error": f"{type(error).__name__}: {error}"[:200], "timed_out": False,
                "exit_code": None, "render": None}
    try:
        stdout, stderr = process.communicate(timeout=max(1.0, timeout_ms / 1000.0))
        status = process.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        status = None
        for signal_number in (signal.SIGKILL,):
            try:
                os.killpg(os.getpgid(process.pid), signal_number)
            except (OSError, ProcessLookupError):
                pass
        try:
            stdout, stderr = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = "", ""
    record = {"harness_error": None, "timed_out": timed_out, "harness_status": status,
              "stderr_tail": (stderr or "")[-2000:], "stdout_tail": (stdout or "")[-500:]}
    if out_json.is_file():
        try:
            record.update(json.loads(out_json.read_text()))
        except ValueError:
            record["harness_error"] = "unreadable runner record"
    elif not timed_out:
        record["harness_error"] = "runner produced no record"
    record.setdefault("exit_code", None)
    record.setdefault("render", None)
    record.setdefault("runtime", {})
    if timed_out:
        record["exit_code"] = None
        record["render"] = None
    if record.get("render") and not Path(record["render"]).is_file():
        record["render"] = None
    shutil.rmtree(sandbox, ignore_errors=True)
    return record


def script_runner(run_root):
    """Factory for the capture driver: one subprocess per (file, timeout).

    `python_exec` needs the exit status and `plot_visual_match` needs a rendered
    figure from the SAME execution, so both gates share one run per file. The
    cache key includes the timeout so a tighter declared budget is never served
    from a looser run.
    """
    run_root = Path(run_root)
    cache = {}
    config_dir = warm_font_cache(run_root / "mplconfig")

    def run(project_root, relative, timeout_ms):
        key = (str(project_root), relative, int(timeout_ms))
        if key not in cache:
            slug = hashlib.sha256(f"{relative}:{timeout_ms}".encode()).hexdigest()[:16]
            cache[key] = run_script(project_root, relative, run_root / slug, timeout_ms,
                                    mpl_config_dir=config_dir)
        return cache[key]

    return run


def load_report(states_root, repo_id, state):
    """Same evidence-resolution convention as hugo_build / workspace_paths."""
    directory = Path(states_root) / repo_id
    scoped = directory / str(state) / "capture_environment_report.json"
    path = scoped if scoped.exists() else directory / "capture_report.json"
    report = json.loads(path.read_text())
    if report.get("repo_id") != repo_id or report.get("status") != "passed":
        raise ValueError("Repository capture identity or status mismatch")
    return report


def single_record(report, kind, state, probe):
    """The one recorded observation for this probe, or raise."""
    records = (report.get("repository_checks") or {})[kind]
    matches = [r for r in records if r.get("state") == state and (r.get("probe") or {}).get("id") == probe["id"]]
    if len(matches) != 1:
        raise ValueError("Repository probe evidence is missing or ambiguous")
    if matches[0]["probe"] != probe:
        raise ValueError("Repository probe does not match the graded assertion")
    if not contract_matches(report.get("repository_runtime")):
        raise ValueError("Repository capture ran outside the pinned runtime")
    return matches[0]["observation"]


def collect_state_checks(project_root, states, key, validate, observe):
    """Generic capture-side collector, mirroring workspace_paths.collect_checks."""
    records = []
    for state in states:
        for probe in state.get(key, []) or []:
            probe = validate(probe)
            record = {"state": state["state_id"], "probe": probe,
                      "observation": observe(project_root, state, probe)}
            if any(r["state"] == record["state"] and r["probe"]["id"] == probe["id"] for r in records):
                raise ValueError(f"Duplicate {key} probe")
            records.append(record)
    return records
