# Harbor integration

ProductWebBench ships a first-class Harbor adapter. A task exports to a Harbor
task directory, the agent runs inside Harbor's container, and Harbor's verifier
step calls back into this package to produce the score.

## 1. Export tasks

```bash
pwb export-harbor-tasks \
  --tasks         data/productwebbench/tasks/dev_next.jsonl \
  --specs         data/productwebbench/tasks/dev_next_submission_specs.json \
  --bench-root    data/productwebbench/tasks/packages/dev_next \
  --state-plan    data/productwebbench/tasks/dev_next_state_plan.json \
  --design-anchors data/productwebbench/tasks/dev_next.design_anchors.json \
  --output-root   out/harbor/dev_next \
  --agent-timeout-sec 1800 --verifier-timeout-sec 900
```

Every flag has a default under the package's output root, so in a normal
checkout `pwb export-harbor-tasks --output-root out/harbor` is enough. Use
`--limit N` for a smoke export.

Each task lands as a Harbor task directory:

```
out/harbor/dev_next/<task_id>/
├── task.toml          # [task] [metadata] [agent] [verifier] [environment]
├── instruction.md     # the staged requirement text shown to the agent
├── rubric.md          # the human-readable check list
├── solution.sh        # reference-solution stub (not shipped to the agent)
└── tests/test.sh      # verifier entry point, writes the reward file
```

`task.toml` requests `cpus = 4`, `memory_mb = 8192`, `storage_mb = 20480`,
`build_timeout_sec = 900`, and `network_mode = "public"` for both the agent and
the verifier step.

## 2. Image and toolchain

We do **not** publish a custom image. The adapter targets Harbor's stock
`chrome` image and mounts the browser/runtime toolchain from the host, because
building an image that fetches Node, Chromium and Hugo is unreliable behind
restrictive CDNs. Concretely the verifier needs, on `PATH` inside the container:

| tool      | why                                                      |
|-----------|----------------------------------------------------------|
| node, npm | building the site under test                             |
| chromium  | Playwright state capture and screenshots                 |
| hugo      | five tasks are Hugo sites (pinned 0.111 for two themes)   |
| php, composer | one task is a Laravel app                            |

Point `PWB_TOOL_ROOT` at a directory holding these if they are not already on
`PATH`.

## 3. Scoring contract

`tests/test.sh` calls the stable entry point in
`productwebbench/evaluation/verifier.py`, which is documented as the
container-friendly surface for out-of-tree adapters. It emits flat metrics:

```json
{"metrics": {"WCS": 0.0, "CCS": 0.0, "RCS": 0.0, "BES": 0.0}}
```

`WCS` is the headline score used in the paper:

```
WCS = [0.5*s + 0.3*g + 0.2*v - 0.05*b]_+
```

where `s` is staged requested-work credit, `g` the final gate pass, `v` the
whole-task verdict and `b` a break penalty. `test.sh` also writes the reward
file Harbor reads, so a Harbor run needs no extra glue.

## 4. Writing another runner

The adapter is ~400 lines and the only Harbor-specific part is `task_toml` and
`test_script`. To target a different harness, reuse
`productwebbench.evaluation.verifier` directly — it takes a submission
directory and returns the same metrics dict — and write your own task-manifest
emitter beside `harbor_adapter.py`.

## Known naming legacy

`sitecontinuum` was this benchmark's working name. Everything the package
writes and reads now says `productwebbench`: module paths, the CLI, the
runtime output root, the exported task-name prefix, `task.toml` metadata, the
instruction heading, the tags, and the `PWB_ROOT` / `PWB_VERIFY_OUT` /
`PWB_SERVER_TIMEOUT` / `PWB_CAPTURE_TIMEOUT` variables the generated
`tests/test.sh` reads. The runs scored in the paper carried the old task-name
prefix; that prefix is a Harbor display namespace and has no effect on
capture, gating or scoring.

Re-export any task directories produced before this change rather than
patching them: an older `tests/test.sh` invokes `python3 -m sitecontinuum`,
which cannot resolve, and reads the old `SITECONTINUUM_*` variables.
Workspaces extracted before the change carry a `.sitecontinuum_workspace.json`
marker; the package writes the new name but still accepts the old one, so
those workspaces keep working.

Fifty-six occurrences of the old name survive on purpose, because they are
bound to the frozen repository snapshots rather than to the package:

| what | count | why it cannot be renamed |
|---|--:|---|
| `forbidden_text_patterns` entries containing `sitecontinuum`, in `tasks/` | 32 | the gate catches a placeholder marker that is literally present in the frozen baseline; renaming the pattern would stop it matching |
| the same patterns quoted back in the E2 rating shards | 2 | the shards record the criterion each card was rated against, verbatim |
| `[data-sitecontinuum-crop]` selectors in `tasks/` | 8 | an attribute baked into the snapshot HTML that the state plans select on |
| the same selector in `tools/playwright_capture.js` | 3 | the capture script has to select the attribute the snapshots actually carry |
| `assets/css/sitecontinuum-state.css`, `assets/js/sitecontinuum-state.js` | 10 | real files inside two task snapshots; the paths appear in `required_content` |
| `.sitecontinuum_workspace.json` in `core/config.py` | 1 | read-only fallback so workspaces extracted before the rename still resolve |

Renaming any of these would change what the benchmark measures, so they are
left exactly as they were scored.
