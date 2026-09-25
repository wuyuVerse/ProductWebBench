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
python3 -m pip install -e .
```

Python ≥ 3.10. Runtime deps are `jsonschema`, `numpy`, `pillow` and
`opencv-python-headless`. Actually *running* agents against tasks additionally
needs Node, Chromium and (for a handful of tasks) Hugo and PHP — see
[`harbor/README.md`](harbor/README.md).

## Run an agent

```bash
pwb validate-tasks tasks                           # schema-check all 400 frozen tasks
pwb export-harbor-tasks --out out/harbor           # → 400 Harbor task dirs

pwb verify-submission \
  --tasks tasks/slot_007/task.jsonl \
  --specs tasks/slot_007/submission_specs.json \
  --states-root path/to/captured/states            # → WCS / CCS / RCS / BES
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
tasks/               all 400 evaluated slots: manifest.json, a per-slot
                     specification (problem statement, rubric, required states,
                     constraints), the frozen per-stage assertion spec, and the
                     Build acceptance record for slots 506-585
baselines/pristine/  the 19 checks run on the *untouched* repository, for all
                     320 Change slots — this is what makes a regression exactly
                     attributable
results/             cells_flat.jsonl: one row per (task, model, leg) evaluated
                     cell, 8,737 rows — the scores reported in the paper
harbor/              Harbor integration guide
tools/               sweep aggregator
docs/                scoring.md, tasks.md
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

## What you can and cannot do with this repository

**You can** read the full specification of all 400 tasks, read the scores every
one of them received, and read and extend the benchmark code — the construction
pipeline, the 19-gate verifier, the scoring, and the Harbor adapter.

**You cannot yet run an agent on the 400 tasks from this repository alone.**
Three pieces are missing, and they are missing because of size and third-party
licensing, not by oversight:

| missing | size | what it is |
|---|---|---|
| repository snapshots | GB | the runnable site handed to the agent, one per task |
| reference browser captures | ~1.9 GB | the screenshots and DOM states the visual and layout gates compare against |
| exported task packages | 44.6 MB | `submission_verifier.json` and friends — the frozen gate definitions for the Change slots (the 80 Build slots have none) |

### All 400 tasks have a specification

`tasks/manifest.json` is the authoritative inventory: **all 400 slots** that were
scored in the paper — 320 Change and 80 Build — with each one's upstream
repository and pinned commit, cell count, stage count and coverage flags. Its
`n_cells` sum is exactly the 8,737 rows of `results/cells_flat.jsonl`.

Every slot ships the frozen record the sweep scored it against: the task
statement and rubric in `task.jsonl`, the gate definitions in
`submission_specs.json`, and the capture recipe — route, viewport and action
sequence per state — in `state_plan.json`. The 80 Build slots additionally
carry their `build_acceptance_*.json`. `pwb validate-tasks tasks` schema-checks
all 400 in one pass.

The **raw per-run artifact tree** (per-run DOM dumps, screenshots and
transcripts, terabytes) is not here either; `results/cells_flat.jsonl` is the
distilled form, one row per evaluated cell.

## Citation

See [`CITATION.cff`](CITATION.cff). MIT licensed; the benchmark *tasks* derive
from third-party repositories that carry their own licences.
