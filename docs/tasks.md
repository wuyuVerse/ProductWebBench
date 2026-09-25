# Task format

400 frozen tasks: **320 Change** tasks over existing code (`slot_001`–`slot_320`)
and **80 long-horizon Build** tasks (`slot_506`–`slot_585`).

Every task freezes, before evaluation:

- a **repository snapshot** — the exact tree handed to the agent;
- **staged requirements** — revealed one stage at a time by a simulated user;
- **browser evidence** — reference screenshots and DOM captures;
- **deterministic checks** — the 19-gate suite, per state.

A task enters the benchmark only once a reference solution passes every check
**and** plausible-wrong variants fail. That two-sided admission is what keeps a
gate from being satisfiable by doing nothing.

## What is in this repository

`tasks/slot_*/task.jsonl` — the per-task metadata the analyses need: required
and hidden state counts, required content, suggested files, stage plan shape.

The **repository snapshots and reference captures are not in this repository.**
They are large, and they embed third-party sites under their own licences.
`pwb catalog` lists what a full task package contains, and
`pwb export-task-packages` builds them from a snapshot root.

## Inspecting a task

```bash
pwb catalog                       # the 400 tasks with split and capability
pwb validate-tasks                # schema + admission invariants
pwb task-template --slot 7        # the staged requirement text
pwb score-report --help           # how a scored run is summarised
```

## Gate families

| family | checks |
|---|---|
| Render | `required_states`, `state_artifacts`, `screenshot_files` |
| Layout | `no_horizontal_overflow`, `visual_anchor_similarity` |
| Content | `regression_text_signals`, `asset_path_signals`, `exact_asset_path_signals`, `image_alt_signals` |
| Console | `console_errors` |
| Assertion | `dom_assertions`, `interaction_assertions`, `state_url_signals`, … |

A route that stops rendering takes its capture and its screenshot down with it,
so Render-family checks co-fire; the paper reports this explicitly rather than
counting it as three independent failures.
