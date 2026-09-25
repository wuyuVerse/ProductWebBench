from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Iterable


VALID_JUDGE_LABELS = {"match", "partial", "mismatch"}


@dataclass(frozen=True)
class CalibrationReport:
    total: int
    agreement: float | None
    cohen_kappa: float | None
    label_counts: dict[str, int]
    passed_gate: bool
    min_items: int
    min_agreement: float
    min_kappa: float

    def to_json(self) -> dict:
        return asdict(self)


def cohen_kappa(human: list[str], judge: list[str]) -> float | None:
    if len(human) != len(judge):
        raise ValueError("human and judge label lists must have the same length")
    total = len(human)
    if not total:
        return None
    observed = sum(1 for left, right in zip(human, judge) if left == right) / total
    human_counts = Counter(human)
    judge_counts = Counter(judge)
    expected = sum((human_counts[label] / total) * (judge_counts[label] / total) for label in VALID_JUDGE_LABELS)
    if expected == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return round((observed - expected) / (1 - expected), 4)


def judge_agreement(
    human_labels: Iterable[str],
    judge_labels: Iterable[str],
    *,
    min_items: int = 150,
    min_agreement: float = 0.8,
    min_kappa: float = 0.6,
) -> CalibrationReport:
    human = list(human_labels)
    judge = list(judge_labels)
    if len(human) != len(judge):
        raise ValueError("human and judge label lists must have the same length")
    invalid = sorted((set(human) | set(judge)) - VALID_JUDGE_LABELS)
    if invalid:
        raise ValueError(f"invalid judge labels: {invalid}")
    total = len(human)
    agreement = None if not total else round(sum(1 for left, right in zip(human, judge) if left == right) / total, 4)
    kappa = cohen_kappa(human, judge)
    passed = bool(total >= min_items and agreement is not None and agreement >= min_agreement and kappa is not None and kappa >= min_kappa)
    return CalibrationReport(
        total=total,
        agreement=agreement,
        cohen_kappa=kappa,
        label_counts=dict(sorted(Counter(judge).items())),
        passed_gate=passed,
        min_items=min_items,
        min_agreement=min_agreement,
        min_kappa=min_kappa,
    )
