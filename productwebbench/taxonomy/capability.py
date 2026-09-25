from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


CHANGE_REGIME = "change"
CONSTRUCTION_REGIME = "construction"
LM_BLOCK = "lm"
MM_BLOCK = "mm"

L_HARD = "L-hard"
L_METRIC = "L-metric"
L_SOFT = "L-soft"


@dataclass(frozen=True)
class RegimeSpec:
    key: str
    label: str
    description: str
    continuity_success_metric: str
    completion_metric: str
    regression_safety_metric: str
    quality_metric: str
    judge_agreement_metric: str
    target_tasks: int = 400


@dataclass(frozen=True)
class BlockSpec:
    key: str
    label: str
    tested_model: str
    signal: str


@dataclass(frozen=True)
class SignalLayerSpec:
    key: str
    deterministic: bool
    uses_model: bool
    role: str
    examples: list[str]


@dataclass(frozen=True)
class MetricSpec:
    key: str
    label: str
    unified_family: str
    regime: str | None
    primary: bool
    deterministic: bool
    uses_mllm: bool
    description: str


REGIMES = [
    RegimeSpec(
        key=CHANGE_REGIME,
        label="Continuity under Change",
        description="Modify an existing website while preserving browser, design, state, content, and implementation continuity.",
        continuity_success_metric="WCS",
        completion_metric="CCS",
        regression_safety_metric="RCS",
        quality_metric="DCS",
        judge_agreement_metric="JA_B",
    ),
    RegimeSpec(
        key=CONSTRUCTION_REGIME,
        label="Continuity under Construction",
        description="Grow a website through milestones while each step runs, passes checkpoints, and preserves prior checkpoints.",
        continuity_success_metric="TCS",
        completion_metric="TD",
        regression_safety_metric="ITR",
        quality_metric="QS",
        judge_agreement_metric="JA_A",
    ),
]

BLOCKS = [
    BlockSpec(
        key=LM_BLOCK,
        label="Language-model block",
        tested_model="coding agent / language model",
        signal="generated code or patch passes executable browser-state checks",
    ),
    BlockSpec(
        key=MM_BLOCK,
        label="Multimodal judge block",
        tested_model="multimodal model",
        signal="local crop/checkpoint judgments agree with human labels",
    ),
]

SIGNAL_LAYERS = [
    SignalLayerSpec(
        key=L_HARD,
        deterministic=True,
        uses_model=False,
        role="Primary pass/fail evidence.",
        examples=[
            "install/build/run succeeds",
            "route is reachable",
            "DOM node exists",
            "Playwright interaction reaches target state",
            "no horizontal overflow",
            "trajectory regression replay passes",
        ],
    ),
    SignalLayerSpec(
        key=L_METRIC,
        deterministic=True,
        uses_model=False,
        role="Thresholded numeric evidence that may contribute to hard success.",
        examples=[
            "layout geometry threshold",
            "token drift threshold",
            "content slot count",
            "information density",
            "de-AI fingerprint distance",
        ],
    ),
    SignalLayerSpec(
        key=L_SOFT,
        deterministic=False,
        uses_model=True,
        role="Calibrated quality-only evidence; never controls primary success alone.",
        examples=[
            "local crop style fit",
            "visual hierarchy judgment",
            "image/text semantic match",
        ],
    ),
]

METRICS = [
    MetricSpec(
        key="WCS",
        label="Website Continuity Success",
        unified_family="Continuity-Success",
        regime=CHANGE_REGIME,
        primary=True,
        deterministic=True,
        uses_mllm=False,
        description="A change-regime patch passes the browser-state verifier.",
    ),
    MetricSpec(
        key="TCS",
        label="Trajectory Continuity Success",
        unified_family="Continuity-Success",
        regime=CONSTRUCTION_REGIME,
        primary=True,
        deterministic=True,
        uses_mllm=False,
        description="A construction trajectory completes all milestones with zero intra-trajectory regression.",
    ),
    MetricSpec(
        key="CCS",
        label="Change Completion Score",
        unified_family="Completion",
        regime=CHANGE_REGIME,
        primary=False,
        deterministic=True,
        uses_mllm=False,
        description="How much of the requested change is completed in the change regime.",
    ),
    MetricSpec(
        key="TD",
        label="Trajectory Depth",
        unified_family="Completion",
        regime=CONSTRUCTION_REGIME,
        primary=False,
        deterministic=True,
        uses_mllm=False,
        description="Normalized milestone depth reached by a construction trajectory.",
    ),
    MetricSpec(
        key="RCS",
        label="Regression Continuity Score",
        unified_family="Regression-Safety",
        regime=CHANGE_REGIME,
        primary=False,
        deterministic=True,
        uses_mllm=False,
        description="Existing website content and states remain intact after a patch.",
    ),
    MetricSpec(
        key="ITR",
        label="Intra-Trajectory Regression Pass Rate",
        unified_family="Regression-Safety",
        regime=CONSTRUCTION_REGIME,
        primary=False,
        deterministic=True,
        uses_mllm=False,
        description="Prior milestone checkpoints still pass after later construction steps.",
    ),
    MetricSpec(
        key="DCS",
        label="Design Continuity Score",
        unified_family="Quality",
        regime=CHANGE_REGIME,
        primary=False,
        deterministic=True,
        uses_mllm=False,
        description="Design-token and visual-anchor continuity in the change regime.",
    ),
    MetricSpec(
        key="SCS",
        label="State Continuity Score",
        unified_family="Regression-Safety",
        regime=CHANGE_REGIME,
        primary=False,
        deterministic=True,
        uses_mllm=False,
        description="Interactive browser state and action-history continuity in the change regime.",
    ),
    MetricSpec(
        key="ECS",
        label="Experience Continuity Score",
        unified_family="Completion",
        regime=CHANGE_REGIME,
        primary=False,
        deterministic=True,
        uses_mllm=False,
        description="Workflow, route, and user-experience continuity in the change regime.",
    ),
    MetricSpec(
        key="CACS",
        label="Content/Asset Continuity Score",
        unified_family="Completion",
        regime=CHANGE_REGIME,
        primary=False,
        deterministic=True,
        uses_mllm=False,
        description="Grounded content, local asset, and media-path continuity in the change regime.",
    ),
    MetricSpec(
        key="ICS",
        label="Implementation Continuity Score",
        unified_family="Regression-Safety",
        regime=CHANGE_REGIME,
        primary=False,
        deterministic=True,
        uses_mllm=False,
        description="Source-boundary, dependency, and implementation-fit continuity in the change regime.",
    ),
    MetricSpec(
        key="QS",
        label="Construction Quality Score",
        unified_family="Quality",
        regime=CONSTRUCTION_REGIME,
        primary=False,
        deterministic=False,
        uses_mllm=True,
        description="Auxiliary construction quality over passed milestones; may include calibrated L-soft evidence.",
    ),
    MetricSpec(
        key="JA_B",
        label="Change Judge Agreement",
        unified_family="Judge-Agreement",
        regime=CHANGE_REGIME,
        primary=False,
        deterministic=False,
        uses_mllm=True,
        description="MM judge agreement with human labels for change-regime continuity/aesthetic judgments.",
    ),
    MetricSpec(
        key="JA_A",
        label="Construction Judge Agreement",
        unified_family="Judge-Agreement",
        regime=CONSTRUCTION_REGIME,
        primary=False,
        deterministic=False,
        uses_mllm=True,
        description="MM judge agreement with human labels for construction checkpoint judgments.",
    ),
    MetricSpec(
        key="BES",
        label="Browser Execution Score",
        unified_family="Execution",
        regime=None,
        primary=False,
        deterministic=True,
        uses_mllm=False,
        description="Shared browser execution health across regimes.",
    ),
]


REGIME_KEYS = {item.key for item in REGIMES}
BLOCK_KEYS = {item.key for item in BLOCKS}
SIGNAL_LAYER_KEYS = {item.key for item in SIGNAL_LAYERS}
METRIC_KEYS = {item.key for item in METRICS}


def metric_schema() -> dict[str, Any]:
    return {
        "schema_version": "2026-06-18",
        "object": "Website Continuity",
        "regimes": [asdict(item) for item in REGIMES],
        "blocks": [asdict(item) for item in BLOCKS],
        "signal_layers": [asdict(item) for item in SIGNAL_LAYERS],
        "metrics": [asdict(item) for item in METRICS],
        "invariants": [
            "Primary success metrics WCS/TCS/TD/ITR are computed from L-hard/L-metric evidence only.",
            "L-soft and MM judge outputs never control primary pass/fail alone.",
            "MM judge results are reported as judge-agreement metrics and require human calibration before leaderboard use.",
        ],
    }


def task_package_json_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://productwebbench.local/schema/task_package.schema.json",
        "title": "Website Continuity Task Package",
        "type": "object",
        "allOf": [
            {
                "if": {"properties": {"regime": {"const": CHANGE_REGIME}}, "required": ["regime"]},
                "then": {
                    "properties": {
                        "lm_task": {
                            "properties": {
                                "primary_metrics": {"contains": {"const": "WCS"}},
                            }
                        },
                        "mm_task": {
                            "properties": {
                                "judge_metric": {"const": "JA_B"},
                            }
                        },
                    }
                },
            },
            {
                "if": {"properties": {"regime": {"const": CONSTRUCTION_REGIME}}, "required": ["regime"]},
                "then": {
                    "required": ["asset_policy"],
                    "properties": {
                        "lm_task": {
                            "properties": {
                                "primary_metrics": {
                                    "allOf": [
                                        {"contains": {"const": "TCS"}},
                                        {"contains": {"const": "TD"}},
                                        {"contains": {"const": "ITR"}},
                                    ]
                                },
                            }
                        },
                        "mm_task": {
                            "properties": {
                                "judge_metric": {"const": "JA_A"},
                            }
                        },
                        "asset_policy": {"$ref": "#/$defs/asset_policy"},
                        "gates": {
                            "required": ["asset_policy"],
                            "properties": {
                                "asset_policy": {"$ref": "#/$defs/gate_result"},
                            },
                        },
                    }
                },
            },
        ],
        "required": [
            "schema_version",
            "task_id",
            "repo_id",
            "regime",
            "website_type",
            "split",
            "lm_task",
            "mm_task",
            "evidence",
            "gates",
        ],
        "additionalProperties": True,
        "properties": {
            "schema_version": {"const": "2026-06-18"},
            "task_id": {"type": "string", "minLength": 8},
            "repo_id": {"type": "string", "minLength": 8},
            "regime": {"enum": sorted(REGIME_KEYS)},
            "website_type": {"type": "string", "minLength": 2},
            "split": {"type": "string", "minLength": 2},
            "lm_task": {
                "type": "object",
                "required": ["block", "primary_metrics", "instructions"],
                "properties": {
                    "block": {"const": LM_BLOCK},
                    "primary_metrics": {
                        "type": "array",
                        "items": {"enum": sorted(METRIC_KEYS)},
                        "minItems": 1,
                    },
                    "instructions": {"type": "string", "minLength": 20},
                },
            },
            "mm_task": {
                "type": "object",
                "required": ["block", "judge_metric", "checkpoints"],
                "properties": {
                    "block": {"const": MM_BLOCK},
                    "judge_metric": {"enum": ["JA_A", "JA_B"]},
                    "checkpoints": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/mm_checkpoint"},
                    },
                    "human_calibration_required": {"type": "boolean"},
                },
            },
            "evidence": {
                "type": "object",
                "required": ["capture_states", "design_anchors", "provenance"],
                "properties": {
                    "capture_states": {"type": "array", "items": {"type": "string"}},
                    "design_anchors": {"type": "string"},
                    "provenance": {"type": "string"},
                },
            },
            "gates": {
                "type": "object",
                "required": [
                    "metareval",
                    "leak_audit",
                    "de_ai",
                    "spec_completeness_precheck",
                    "regression_sanity",
                    "signal_layers",
                ],
                "properties": {
                    "metareval": {"$ref": "#/$defs/gate_result"},
                    "leak_audit": {"$ref": "#/$defs/gate_result"},
                    "de_ai": {"$ref": "#/$defs/gate_result"},
                    "spec_completeness_precheck": {"$ref": "#/$defs/gate_result"},
                    "regression_sanity": {"$ref": "#/$defs/gate_result"},
                    "signal_layers": {
                        "type": "object",
                        "required": sorted(SIGNAL_LAYER_KEYS),
                    },
                },
            },
        },
        "$defs": {
            "gate_result": {
                "type": "object",
                "required": ["gate", "passed", "status"],
                "additionalProperties": True,
                "properties": {
                    "gate": {"type": "string", "minLength": 2},
                    "passed": {"type": "boolean"},
                    "status": {
                        "enum": [
                            "passed",
                            "failed",
                            "partial",
                            "pending",
                            "missing",
                            "not_applicable",
                        ]
                    },
                    "report_path": {"type": "string"},
                    "report_paths": {"type": "array", "items": {"type": "string"}},
                    "summary": {"type": "object"},
                },
            },
            "mm_checkpoint": {
                "type": "object",
                "required": ["checkpoint_id", "layer", "region", "label_set"],
                "properties": {
                    "checkpoint_id": {"type": "string", "minLength": 2},
                    "layer": {"const": L_SOFT},
                    "region": {"type": "string", "minLength": 2},
                    "target_crop_ref": {"type": "string"},
                    "label_set": {
                        "type": "array",
                        "items": {"enum": ["match", "partial", "mismatch"]},
                        "minItems": 3,
                        "maxItems": 3,
                        "uniqueItems": True,
                    },
                },
            },
            "asset_policy": {
                "type": "object",
                "required": [
                    "schema_version",
                    "artifact_type",
                    "formal_task_record",
                    "passed",
                    "provided_assets",
                    "target_render_refs",
                ],
                "additionalProperties": True,
                "properties": {
                    "schema_version": {"const": "2026-06-18"},
                    "artifact_type": {"const": "construction_asset_policy"},
                    "formal_task_record": {"const": False},
                    "passed": {"const": True},
                    "provided_assets": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/asset_policy_item"},
                    },
                    "target_render_refs": {
                        "type": "array",
                        "maxItems": 0,
                    },
                },
            },
            "asset_policy_item": {
                "type": "object",
                "required": ["asset_id", "classification", "actor_visible"],
                "additionalProperties": True,
                "properties": {
                    "asset_id": {"type": "string", "minLength": 1},
                    "classification": {"enum": ["intrinsic", "decorative", "placeholder"]},
                    "actor_visible": {"type": "boolean"},
                    "source_kind": {
                        "enum": [
                            "original_repo",
                            "replacement",
                            "placeholder",
                            "generated",
                            "external_allowed",
                        ]
                    },
                    "replacement_status": {
                        "enum": ["not_required", "hidden", "replaced", "placeholder"]
                    },
                },
            },
        },
    }
