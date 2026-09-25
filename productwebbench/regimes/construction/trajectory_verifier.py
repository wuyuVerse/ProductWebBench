from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ...evalkit.signals import SignalCheck, hard_metric_passed


@dataclass(frozen=True)
class RegressionReplayResult:
    checkpoint_id: str
    passed: bool
    introduced_at: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def milestone_passes(checks: list[SignalCheck], replay_results: list[RegressionReplayResult]) -> bool:
    return hard_metric_passed(checks) and all(item.passed for item in replay_results)
