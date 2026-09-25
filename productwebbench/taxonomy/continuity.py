from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ContinuityFacet:
    key: str
    label: str
    description: str


STATE_CONTINUITY = "state_continuity"
DESIGN_CONTINUITY = "design_continuity"
EXPERIENCE_CONTINUITY = "experience_continuity"
CONTENT_ASSET_CONTINUITY = "content_asset_continuity"
REGRESSION_CONTINUITY = "regression_continuity"
IMPLEMENTATION_CONTINUITY = "implementation_continuity"

CONTINUITY_FACETS = [
    ContinuityFacet(STATE_CONTINUITY, "State Continuity", "Route, viewport, action, and browser-state continuity."),
    ContinuityFacet(DESIGN_CONTINUITY, "Design Continuity", "Brand, component, token, layout, and visual-language continuity."),
    ContinuityFacet(EXPERIENCE_CONTINUITY, "Experience Continuity", "Navigation, information architecture, CTA, and workflow continuity."),
    ContinuityFacet(CONTENT_ASSET_CONTINUITY, "Content/Asset Continuity", "Grounded content, local media, data, and asset semantics."),
    ContinuityFacet(REGRESSION_CONTINUITY, "Regression Continuity", "Preservation of old routes, text, interactions, and responsive states."),
    ContinuityFacet(IMPLEMENTATION_CONTINUITY, "Implementation Continuity", "Build, dependency, source-boundary, data-model, and maintainability fit."),
]

CONTINUITY_FACET_KEYS = {facet.key for facet in CONTINUITY_FACETS}
CONTINUITY_FACET_LABELS = {facet.key: facet.label for facet in CONTINUITY_FACETS}

LEGACY_FAMILY_ALIASES = {
    "browser_state": STATE_CONTINUITY,
    "state": STATE_CONTINUITY,
    "design_system": DESIGN_CONTINUITY,
    "design": DESIGN_CONTINUITY,
    "experience_architecture": EXPERIENCE_CONTINUITY,
    "experience": EXPERIENCE_CONTINUITY,
    "grounded_content_assets": CONTENT_ASSET_CONTINUITY,
    "asset_grounding": CONTENT_ASSET_CONTINUITY,
    "content_assets": CONTENT_ASSET_CONTINUITY,
    "regression_safe": REGRESSION_CONTINUITY,
    "regression": REGRESSION_CONTINUITY,
    "engineering_discipline": IMPLEMENTATION_CONTINUITY,
    "implementation": IMPLEMENTATION_CONTINUITY,
}


def normalize_continuity_facet(value: str | None) -> str | None:
    if not value:
        return None
    key = value.strip().lower().replace("-", "_")
    if key in CONTINUITY_FACET_KEYS:
        return key
    return LEGACY_FAMILY_ALIASES.get(key)


def infer_continuity_facet(task: dict[str, Any]) -> str:
    explicit = normalize_continuity_facet(task.get("family") or task.get("continuity_facet"))
    if explicit:
        return explicit

    text = " ".join(
        str(task.get(field, ""))
        for field in ["task_id", "scope", "intent", "problem_statement", "author_notes"]
    ).lower()
    text += " " + " ".join(task.get("required_states", []) + task.get("hidden_states", [])).lower()

    if any(token in text for token in ["hover", "modal", "menu", "sidebar", "canvas", "form", "dropdown", "state"]):
        return STATE_CONTINUITY
    if any(token in text for token in ["nav", "route", "cta", "journey", "workflow", "cross_page", "funnel"]):
        return EXPERIENCE_CONTINUITY
    if any(token in text for token in ["asset", "image", "video", "svg", "glb", "product", "gallery", "media"]):
        return CONTENT_ASSET_CONTINUITY
    if any(token in text for token in ["regression", "preserve", "old route", "remain", "not break"]):
        return REGRESSION_CONTINUITY
    if any(token in text for token in ["build", "dependency", "component api", "data model", "source"]):
        return IMPLEMENTATION_CONTINUITY
    return DESIGN_CONTINUITY
