# Scoring

A run is scored on three different notions of success. Keeping them apart is the
point of the benchmark.

1. **Requested-work completion** — did the agent do what it was asked, stage by
   stage. This is what most frontend agent benchmarks report.
2. **Final gate pass** — does the site still satisfy every armed check at the
   last checkpoint (`v_i` in the paper).
3. **Whole-task pass** — every stage's checks pass at every later checkpoint.

The headline scalar is

```
WCS = [0.5*s + 0.3*g + 0.2*v - 0.05*b]_+
```

| term | meaning |
|---|---|
| `s` | staged requested-work credit, averaged over stages |
| `g` | final gate pass |
| `v` | whole-task verdict |
| `b` | break penalty: checks that passed earlier and fail later |

`[ ]_+` clamps at zero, so a run cannot go negative by breaking things.

## Cumulative gating

A simulated user reveals the task one stage at a time. **A cleared stage is
never retired**: once a stage's assertions pass, they stay armed at every later
checkpoint. That is what makes the score a property of the whole trajectory
rather than of its endpoint, and it is what lets the benchmark see an agent
satisfy stage 3 by breaking stage 1.

## Two regression channels

| channel | baseline compared against | what it catches |
|---|---|---|
| class (a) | the repository **as handed to the agent** (`baselines/pristine/`) | the agent broke something that already worked |
| class (b) | a state **this run itself** had already passed | the agent broke its own earlier work |

Class (a) is exact provenance, not a post-hoc label, because the same 19 checks
were run on the untouched repository before the agent started.

## Continuity checks

The conservative non-completion family: blank route, horizontal overflow,
console errors, protected-copy changes, asset-path breakage, visual anchors,
capture completeness, screenshots. `visual_regression` is excluded — see the
comment at the top of `analysis/E25_pristine_regression/compute_e25.py` for why.
