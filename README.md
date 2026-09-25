# ProductWebBench

Frontend coding agents are usually graded on one question: did the requested
target get built. Real engineering changes carry a second obligation, because
the rest of the site has to keep working.

ProductWebBench scores both. It is a benchmark of **400 tasks on living
frontend repositories** — 320 Change tasks over existing code and 80
long-horizon Build tasks — where every task freezes a repository snapshot,
staged requirements, browser evidence and deterministic checks before
evaluation, and enters only once a reference solution passes every check and
plausible-wrong ones fail.

Across **8,737 runs from 13 models**, the two obligations come apart. Of the
Change runs that pass every requested-work check, **28.2% still fail a
continuity check** — a blank route, horizontal overflow, console errors — and
**10.8% push a browser state they had already passed back to failing**.

## Install

```bash
git clone https://github.com/wuyuVerse/ProductWebBench && cd ProductWebBench
python3 -m pip install -e ".[analysis]"
```

Python ≥ 3.10. Runtime deps are `jsonschema`, `numpy`, `pillow` and
`opencv-python-headless`; the `analysis` extra adds `matplotlib` and `scipy`.
Actually *running* agents against tasks additionally needs Node, Chromium and
(for a handful of tasks) Hugo and PHP — see [`harbor/README.md`](harbor/README.md).

## Reproduce every number in the paper

```bash
bash tools/reproduce.sh          # → 34 ok, 0 failed, 9 skipped
```

This regenerates every table and figure from the frozen evaluation cache
(`analysis/_data/cells_flat.jsonl`, 8,737 cells). No model API calls, no
browser. The nine skips are explicitly reported with reasons — four upstream
scripts need the raw per-run capture tree, five are argument-taking utilities.
See [`docs/reproduce.md`](docs/reproduce.md), which also lists the invariants a
successful run must satisfy.

## Run an agent

```bash
pwb catalog                              # the 400 frozen tasks
pwb export-harbor-tasks --output-root out/harbor   # → Harbor task dirs
pwb verify-submission --submission path/to/run     # → WCS / CCS / RCS / BES
```

Harbor is the supported runner and the one used for the paper's numbers:
[`harbor/README.md`](harbor/README.md) covers the export, the image and
toolchain requirements, and the scoring callback. Any other harness can call
`productwebbench.evaluation.verifier` directly — it takes a submission
directory and returns the metrics dict.

## Layout

```
productwebbench/     the benchmark package (task construction, capture,
                     evaluation, 19-gate verifier, Harbor adapter)
  _vendor/verifier/  six verifier primitives vendored so a checkout is
                     self-contained
analysis/            E1–E25: the paper's analyses. _data/cells_flat.jsonl is
                     the evaluation cache every number derives from.
figures/scripts/     the figure scripts; output lands in figures/
baselines/pristine/  the 19 checks run on the *untouched* repository, per task
                     — this is what makes a regression exactly attributable
tasks/               per-task metadata for the 400 frozen slots
harbor/              Harbor integration guide
tools/               reproduce.sh, sweep aggregator
docs/                reproduce.md, scoring.md, tasks.md
```

## Scoring in one line

```
WCS = [0.5*s + 0.3*g + 0.2*v - 0.05*b]_+
```

staged requested-work credit, final gate pass, whole-task verdict, minus a
penalty for checks that passed earlier and fail later. A simulated user reveals
the task one stage at a time and **a cleared stage is never retired** — its
assertions stay armed at every later checkpoint, so continuity is scored across
the whole trajectory rather than at its endpoint.
[`docs/scoring.md`](docs/scoring.md) has the rest.

## What is *not* here

The **repository snapshots and reference browser captures** for the 400 tasks.
They are large and they embed third-party sites under their own licences.
`pwb export-task-packages` builds task packages from a snapshot root;
[`docs/tasks.md`](docs/tasks.md) describes the format.

The **raw per-run artifact tree** (per-run DOM dumps, screenshots and
transcripts, terabytes). `analysis/_data/cells_flat.jsonl` is the distilled
form, and the four upstream scripts that produced it are shipped so the
derivation is auditable.

## Citation

See [`CITATION.cff`](CITATION.cff). MIT licensed; the benchmark *tasks* derive
from third-party repositories that carry their own licences.
