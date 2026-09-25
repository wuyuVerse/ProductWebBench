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

`sitecontinuum` was this benchmark's working name. Everything the export
writes now says `productwebbench`: the exported task name prefix, the
`[metadata]` block in `task.toml`, the instruction heading, the tags, and the
`PWB_ROOT` / `PWB_VERIFY_OUT` / `PWB_SERVER_TIMEOUT` / `PWB_CAPTURE_TIMEOUT`
environment variables the generated `tests/test.sh` reads. The runs scored in
the paper carried the old prefix; it is a Harbor display namespace and has no
effect on capture, gating or scoring.

The old name still appears inside the package's own module paths and in some
task-record fields. Re-export any task directories produced before this commit
rather than patching them in place — an older `tests/test.sh` invokes
`python3 -m sitecontinuum`, which cannot resolve, and reads the old
`SITECONTINUUM_*` variables.
