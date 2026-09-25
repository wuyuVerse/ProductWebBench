from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Iterable


DEFAULT_BRAND_TOKEN_PATTERN = re.compile(r"\b([A-Z][A-Za-z0-9]+(?:[-_ ][A-Z][A-Za-z0-9]+){1,})\b")
DEFAULT_GENERIC_BRAND_TOKENS = {
    "At least",
    "Do not",
    "The UI",
    "Every L",
    "All action",
    "Build a",
    "Fill semantic",
    "Apply a",
    "Add reachable",
    "Use provided",
    "Polish desktop",
}


@dataclass(frozen=True)
class LeakAuditReport:
    passed: bool
    max_verbatim_overlap: int
    brand_token_hits: list[str] = field(default_factory=list)
    target_image_in_input: bool = False
    unclassified_assets: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)


def longest_common_substring_length(left: str, right: str) -> int:
    if not left or not right:
        return 0
    previous = [0] * (len(right) + 1)
    best = 0
    for i, lch in enumerate(left, start=1):
        current = [0] * (len(right) + 1)
        for j, rch in enumerate(right, start=1):
            if lch == rch:
                current[j] = previous[j - 1] + 1
                best = max(best, current[j])
        previous = current
    return best


def audit_text_leakage(
    actor_visible_text: str,
    source_texts: Iterable[str],
    *,
    max_allowed_overlap: int = 48,
    allowed_brand_tokens: set[str] | None = None,
    target_image_in_input: bool = False,
    provided_assets: dict[str, str] | None = None,
) -> LeakAuditReport:
    source_blob = "\n".join(source_texts)
    source_blob_lower = source_blob.lower()
    overlap = longest_common_substring_length(actor_visible_text.lower(), source_blob.lower())
    allowed_brand_tokens = allowed_brand_tokens or set()
    brand_hits = sorted(
        {
            match.group(1)
            for match in DEFAULT_BRAND_TOKEN_PATTERN.finditer(actor_visible_text)
            if match.group(1) not in allowed_brand_tokens
            and match.group(1) not in DEFAULT_GENERIC_BRAND_TOKENS
            and match.group(1).lower() in source_blob_lower
        }
    )
    unclassified = sorted(
        path
        for path, classification in (provided_assets or {}).items()
        if classification not in {"intrinsic", "decorative", "placeholder"}
    )
    issues: list[str] = []
    if overlap > max_allowed_overlap:
        issues.append(f"verbatim overlap {overlap} exceeds threshold {max_allowed_overlap}")
    if brand_hits:
        issues.append("actor-visible spec contains unapproved brand-like tokens")
    if target_image_in_input:
        issues.append("target render/image appears in actor-visible input")
    if unclassified:
        issues.append("one or more provided assets lack intrinsic/decorative classification")
    return LeakAuditReport(
        passed=not issues,
        max_verbatim_overlap=overlap,
        brand_token_hits=brand_hits,
        target_image_in_input=target_image_in_input,
        unclassified_assets=unclassified,
        issues=issues,
    )
