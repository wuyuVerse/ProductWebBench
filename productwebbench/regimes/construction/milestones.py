from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .invariants import InvariantPredicate


MILESTONE_LADDER = (
    "layout_skeleton",
    "content_slots",
    "visual_system",
    "interaction",
    "asset_grounding",
    "responsive_polish",
)


@dataclass(frozen=True)
class MilestoneSpec:
    milestone_id: str
    ladder_step: str
    actor_spec: str
    checkpoints: list[InvariantPredicate]
    soft_checkpoints: list[InvariantPredicate] = field(default_factory=list)
    max_rounds: int = 6

    def __post_init__(self) -> None:
        if self.ladder_step not in MILESTONE_LADDER:
            raise ValueError(f"unknown milestone ladder step: {self.ladder_step}")

    def to_json(self) -> dict[str, Any]:
        return asdict(self)
