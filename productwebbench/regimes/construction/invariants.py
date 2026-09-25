from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class InvariantPredicate:
    predicate_id: str
    kind: str
    layer: str
    assert_text: str
    locate_by: str | None = None
    actor_visible: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def content_free_predicate(predicate_id: str, kind: str, assert_text: str, *, layer: str = "L-hard") -> InvariantPredicate:
    forbidden_surface_tokens = ["exact text:", "screenshot", "pixel-perfect", "copy this"]
    lowered = assert_text.lower()
    if any(token in lowered for token in forbidden_surface_tokens):
        raise ValueError(f"predicate appears actor-leaky: {predicate_id}")
    return InvariantPredicate(predicate_id=predicate_id, kind=kind, layer=layer, assert_text=assert_text)
