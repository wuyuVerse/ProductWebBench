from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...core.config import DEFAULT_OUTPUT_ROOT
from ...core.formal_data_guard import assert_not_under_formal_task_root
from ...core.io_utils import ensure_dir, read_jsonl, write_json, write_jsonl
from ...core.task_files import task_file_patterns
from ...taxonomy.continuity import (
    CONTINUITY_FACET_KEYS,
    CONTINUITY_FACET_LABELS,
    normalize_continuity_facet,
)
from .queue import family_label, is_authoring_candidate, media_total


TOTAL_TARGET_TASKS = 400

FACET_TARGETS = {
    "state_continuity": 60,
    "design_continuity": 80,
    "experience_continuity": 80,
    "content_asset_continuity": 80,
    "regression_continuity": 60,
    "implementation_continuity": 40,
}

WEBSITE_TYPE_TARGETS = {
    "dashboard_admin": 70,
    "marketing": 60,
    "portfolio": 55,
    "commerce": 45,
    "docs": 45,
    "editorial_blog": 40,
    "interactive_canvas": 45,
    "ai_chat_app": 25,
    "gallery_showcase": 15,
}

INTENT_TARGETS = {
    "modify": 145,
    "add": 80,
    "repair": 75,
    "restyle": 55,
    "extend_interaction": 45,
}

SCOPE_TARGETS = {
    "component": 65,
    "section": 55,
    "layout": 65,
    "page": 45,
    "flow": 55,
    "asset": 55,
    "cross_page": 60,
}

DIFFICULTY_TARGETS = {
    "L1": 40,
    "L2": 115,
    "L3": 150,
    "L4": 75,
    "L5": 20,
}

FACET_PROMPTS = {
    "state_continuity": "preserve and evolve interactive state, filters, forms, dialogs, routes, or session-like UI state",
    "design_continuity": "extend the local visual system through typography, spacing, component rhythm, and responsive layout",
    "experience_continuity": "complete or refine a user workflow across one or more meaningful screens",
    "content_asset_continuity": "use real repo content, media, icons, copy, and data structures without generic placeholders",
    "regression_continuity": "repair a concrete defect while keeping unrelated pages and states stable",
    "implementation_continuity": "fit the repo architecture, component boundaries, build system, and source conventions",
}

FAMILY_ALIASES = {
    "dashboard": "dashboard_admin",
    "dashboard_admin": "dashboard_admin",
    "admin": "dashboard_admin",
    "marketing": "marketing",
    "portfolio": "portfolio",
    "commerce": "commerce",
    "docs": "docs",
    "documentation": "docs",
    "blog": "editorial_blog",
    "editorial_blog": "editorial_blog",
    "interactive_canvas": "interactive_canvas",
    "canvas": "interactive_canvas",
    "ai_chat_app": "ai_chat_app",
    "chat": "ai_chat_app",
    "gallery": "gallery_showcase",
    "gallery_showcase": "gallery_showcase",
}
WEBSITE_TYPE_KEYS = set(WEBSITE_TYPE_TARGETS)


def text_has(text: str, *tokens: str) -> bool:
    return any(token in text for token in tokens)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_repo_index(manifest_path: Path) -> dict[str, dict[str, Any]]:
    if not manifest_path.exists():
        return {}
    return {record["repo_id"]: record for record in read_jsonl(manifest_path)}


def normalize_website_type(value: str | None) -> str:
    key = str(value or "unknown").strip().lower()
    return FAMILY_ALIASES.get(key, key)


def classify_website_text(text: str) -> str | None:
    text = text.lower()
    if text_has(text, "dashboard", "admin", "analytics", "metric", "kpi", "inventory ops", "ops board"):
        return "dashboard_admin"
    if text_has(text, "ecommerce", "e-commerce", "storefront", "product detail", "cart", "checkout", "orders", "fulfillment"):
        return "commerce"
    if re.search(r"\b(chat|chatbot|assistant|llm|gpt|gemini|prompt)\b|chatgpt|geminiprochat|agent console", text):
        return "ai_chat_app"
    if text_has(text, "canvas", "3d", "webgl", "map", "geospatial", "interactive demo", "motion"):
        return "interactive_canvas"
    if text_has(text, "publication", "research", "article", "blog", "journal", "editorial", "post"):
        return "editorial_blog"
    if text_has(text, "gallery", "showcase", "showroom", "archive", "casebook", "preview grid"):
        return "gallery_showcase"
    if text_has(text, "portfolio", "resume", "cv", "case study", "project case"):
        return "portfolio"
    if text_has(
        text,
        "docs",
        "documentation",
        "guide",
        "reference",
        "api",
        "component",
        "components",
        "library",
        "framework",
        "storybook",
        "design-system",
        "design system",
        "autocomplete",
        "scully",
        "lion",
    ):
        return "docs"
    if text_has(text, "landing", "launch", "agency", "service", "saas", "church", "visitor", "campaign", "proof system"):
        return "marketing"
    return None


def repo_website_type(record: dict[str, Any]) -> str:
    text = " ".join(
        str(record.get(key, ""))
        for key in ("owner", "name", "repo_id", "framework")
    ).lower()
    classified = classify_website_text(text)
    if classified:
        return classified
    alias = normalize_website_type(family_label(record))
    if alias in WEBSITE_TYPE_KEYS:
        return alias
    if text_has(text, "ui", "component", "library", "kit", "storybook", "mermaid", "design-system", "design system"):
        return "docs"
    if text_has(text, "photo", "image", "media", "album", "lightbox"):
        return "gallery_showcase"
    if media_total(record) >= 80 and not text_has(text, "admin", "dashboard"):
        return "gallery_showcase"
    return "marketing"


def infer_website_type(task: dict[str, Any], repo_index: dict[str, dict[str, Any]]) -> str:
    explicit = task.get("website_type")
    if explicit:
        normalized = normalize_website_type(str(explicit))
        if normalized in WEBSITE_TYPE_KEYS:
            return normalized
    task_text = " ".join(
        [
            str(task.get("task_id", "")),
            str(task.get("repo_id", "")),
            str(task.get("problem_statement", "")),
            str(task.get("author_notes", "")),
            " ".join(str(value) for value in task.get("required_content", [])),
            " ".join(str(value) for value in task.get("design_constraints", [])),
            " ".join(str(value) for value in task_file_patterns(task)),
        ]
    )
    classified = classify_website_text(task_text)
    if classified:
        return classified
    repo = repo_index.get(task.get("repo_id", ""))
    if not repo:
        return "marketing"
    return repo_website_type(repo)


def repeat_slots(targets: dict[str, int], current_counts: Counter[str], remaining_total: int) -> list[str]:
    remaining: dict[str, int] = {
        key: max(0, int(target) - int(current_counts.get(key, 0)))
        for key, target in targets.items()
    }
    sequence: list[str] = []
    keys = list(targets)
    while len(sequence) < remaining_total and any(remaining.values()):
        best_key = max(
            keys,
            key=lambda key: (
                remaining[key] / max(1, targets[key]),
                remaining[key],
                -keys.index(key),
            ),
        )
        if remaining[best_key] <= 0:
            break
        sequence.append(best_key)
        remaining[best_key] -= 1
    while len(sequence) < remaining_total:
        sequence.append(keys[len(sequence) % len(keys)])
    return sequence


def accepted_entry(
    task: dict[str, Any],
    slot_index: int,
    repo_index: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    facet = normalize_continuity_facet(task.get("family") or task.get("continuity_facet"))
    website_type = infer_website_type(task, repo_index)
    return {
        "slot_id": f"slot_{slot_index:03d}",
        "status": "accepted",
        "stage": "S8_package_freeze",
        "source": "existing_task",
        "task_id": task["task_id"],
        "repo_id": task["repo_id"],
        "continuity_facet": facet,
        "continuity_label": CONTINUITY_FACET_LABELS[facet],
        "website_type": website_type,
        "intent": task.get("intent"),
        "scope": task.get("scope"),
        "difficulty": task.get("difficulty"),
        "required_states": task.get("required_states", []),
        "hidden_states": task.get("hidden_states", []),
        "accepted_at": utc_now(),
        "agent_decision": "seed_accept",
        "agent_notes": [
            "Imported from the current vetted task split.",
            "Keep this entry immutable unless the source task itself is revised.",
        ],
    }


def open_entry(
    slot_index: int,
    facet: str,
    website_type: str,
    intent: str,
    scope: str,
    difficulty: str,
) -> dict[str, Any]:
    return {
        "slot_id": f"slot_{slot_index:03d}",
        "status": "open",
        "stage": "S0_candidate_intake",
        "source": "target_plan",
        "task_id": None,
        "repo_id": None,
        "continuity_facet": facet,
        "continuity_label": CONTINUITY_FACET_LABELS[facet],
        "website_type": website_type,
        "intent": intent,
        "scope": scope,
        "difficulty": difficulty,
        "design_goal": FACET_PROMPTS[facet],
        "agent_decision": "needs_agent_design",
        "agent_notes": [
            "Select a repo whose existing UI naturally supports this slot.",
            "Do not freeze a task until source evidence, captured states, verifier spec, and reference sanity all pass.",
        ],
    }


def build_slot_ledger(
    current_tasks_path: Path,
    manifest_path: Path,
    total: int = TOTAL_TARGET_TASKS,
) -> list[dict[str, Any]]:
    repo_index = load_repo_index(manifest_path)
    current_tasks = list(read_jsonl(current_tasks_path)) if current_tasks_path.exists() else []
    if len(current_tasks) > total:
        raise ValueError(f"current task count {len(current_tasks)} exceeds total target {total}")

    entries = [
        accepted_entry(task, slot_index=index + 1, repo_index=repo_index)
        for index, task in enumerate(current_tasks)
    ]
    remaining_total = total - len(entries)
    counts = {
        "facet": Counter(entry["continuity_facet"] for entry in entries),
        "website_type": Counter(entry["website_type"] for entry in entries),
        "intent": Counter(entry["intent"] for entry in entries),
        "scope": Counter(entry["scope"] for entry in entries),
        "difficulty": Counter(entry["difficulty"] for entry in entries),
    }
    facet_slots = repeat_slots(FACET_TARGETS, counts["facet"], remaining_total)
    website_slots = repeat_slots(WEBSITE_TYPE_TARGETS, counts["website_type"], remaining_total)
    intent_slots = repeat_slots(INTENT_TARGETS, counts["intent"], remaining_total)
    scope_slots = repeat_slots(SCOPE_TARGETS, counts["scope"], remaining_total)
    difficulty_slots = repeat_slots(DIFFICULTY_TARGETS, counts["difficulty"], remaining_total)

    for offset in range(remaining_total):
        entries.append(
            open_entry(
                slot_index=len(current_tasks) + offset + 1,
                facet=facet_slots[offset],
                website_type=website_slots[offset],
                intent=intent_slots[offset],
                scope=scope_slots[offset],
                difficulty=difficulty_slots[offset],
            )
        )
    return entries


def count_field(entries: list[dict[str, Any]], field: str) -> dict[str, int]:
    return dict(sorted(Counter(str(entry.get(field)) for entry in entries).items()))


def ledger_summary(entries: list[dict[str, Any]], current_tasks_path: Path, manifest_path: Path) -> dict[str, Any]:
    return {
        "generated_at": utc_now(),
        "current_tasks_path": str(current_tasks_path),
        "manifest_path": str(manifest_path),
        "total": len(entries),
        "accepted": sum(1 for entry in entries if entry["status"] == "accepted"),
        "open": sum(1 for entry in entries if entry["status"] == "open"),
        "continuity_facets": count_field(entries, "continuity_facet"),
        "website_types": count_field(entries, "website_type"),
        "intents": count_field(entries, "intent"),
        "scopes": count_field(entries, "scope"),
        "difficulties": count_field(entries, "difficulty"),
        "targets": {
            "continuity_facets": FACET_TARGETS,
            "website_types": WEBSITE_TYPE_TARGETS,
            "intents": INTENT_TARGETS,
            "scopes": SCOPE_TARGETS,
            "difficulties": DIFFICULTY_TARGETS,
        },
    }


def render_ledger_markdown(entries: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    lines = [
        "# SiteContinuum 400-Task Authoring Ledger",
        "",
        f"- Total slots: `{summary['total']}`",
        f"- Accepted slots: `{summary['accepted']}`",
        f"- Open slots: `{summary['open']}`",
        f"- Generated at: `{summary['generated_at']}`",
        "",
        "## Target Distribution",
        "",
    ]
    for key in ("continuity_facets", "website_types", "intents", "scopes", "difficulties"):
        lines.append(f"### {key}")
        for item, count in summary[key].items():
            lines.append(f"- `{item}`: {count}")
        lines.append("")
    lines.extend(["## Next Open Slots", ""])
    for entry in [item for item in entries if item["status"] == "open"][:30]:
        lines.append(
            f"- `{entry['slot_id']}` `{entry['continuity_facet']}` / `{entry['website_type']}` / "
            f"`{entry['intent']}` / `{entry['scope']}` / `{entry['difficulty']}`"
        )
    lines.extend(["", "## Accepted Seed Slots", ""])
    for entry in [item for item in entries if item["status"] == "accepted"]:
        lines.append(
            f"- `{entry['slot_id']}` `{entry['task_id']}` "
            f"({entry['continuity_facet']}, {entry['intent']}, {entry['scope']}, {entry['difficulty']})"
        )
    lines.append("")
    return "\n".join(lines)


def write_slot_ledger(
    entries: list[dict[str, Any]],
    output_root: Path,
    current_tasks_path: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    assert_not_under_formal_task_root(output_root, purpose="Target slot planning ledger")
    ensure_dir(output_root)
    summary = ledger_summary(entries, current_tasks_path, manifest_path)
    write_jsonl(output_root / "tasks_400_ledger.jsonl", entries)
    write_json(output_root / "tasks_400_slots.json", {"summary": summary, "items": entries})
    write_json(output_root / "tasks_400_summary.json", summary)
    (output_root / "tasks_400_ledger.md").write_text(render_ledger_markdown(entries, summary), encoding="utf-8")
    return summary


def repo_matches_website_type(record: dict[str, Any], website_type: str) -> bool:
    return repo_website_type(record) == website_type


def repo_slot_score(record: dict[str, Any], slot: dict[str, Any]) -> tuple[int, int, int, str]:
    score = 0
    if repo_matches_website_type(record, slot["website_type"]):
        score += 60
    if is_authoring_candidate(record):
        score += 25
    if record.get("dev_command"):
        score += 10
    if record.get("build_command"):
        score += 8
    if record.get("has_routes"):
        score += 8
    if record.get("has_tests"):
        score += 4
    media = media_total(record)
    score += min(media, 100) // 8
    if slot["continuity_facet"] == "content_asset_continuity":
        score += min(media, 120) // 4
    if slot["continuity_facet"] == "implementation_continuity" and record.get("has_tests"):
        score += 8
    if slot["continuity_facet"] == "state_continuity" and record.get("has_routes"):
        score += 6
    file_count = int(record.get("file_count") or 0)
    if file_count > 2500:
        score -= 10
    if file_count < 50:
        score -= 6
    return (score, media, -file_count, record.get("repo_id", ""))


def candidate_reason(record: dict[str, Any], slot: dict[str, Any]) -> list[str]:
    reasons = [
        f"target website_type={slot['website_type']}; inferred repo type={repo_website_type(record)}",
        f"target continuity_facet={slot['continuity_facet']} ({slot['continuity_label']})",
        f"framework={record.get('framework')}, package_manager={record.get('package_manager')}",
        f"media_count={record.get('media_count', {})}, file_count={record.get('file_count')}",
    ]
    if record.get("has_routes"):
        reasons.append("repo exposes route/page structure for browser-state capture")
    if record.get("dev_command"):
        reasons.append(f"dev command available: {record['dev_command']}")
    if record.get("build_command"):
        reasons.append(f"build command available: {record['build_command']}")
    return reasons


def used_repo_ids(entries: list[dict[str, Any]]) -> set[str]:
    return {entry["repo_id"] for entry in entries if entry.get("repo_id")}


def select_slot_candidates(
    slot: dict[str, Any],
    repos: list[dict[str, Any]],
    unavailable_repos: set[str],
    candidates_per_slot: int,
) -> list[dict[str, Any]]:
    pool = [
        record
        for record in repos
        if record.get("repo_id") not in unavailable_repos and is_authoring_candidate(record)
    ]
    preferred = [record for record in pool if repo_matches_website_type(record, slot["website_type"])]
    ranked = sorted(preferred, key=lambda record: repo_slot_score(record, slot), reverse=True)
    return ranked[:candidates_per_slot]


def render_candidate_review_card(slot: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
    lines = [
        f"# Candidate Review Card: {slot['slot_id']}",
        "",
        "> Generated triage aid only. This is not a formal design brief, not a task record, and must not be copied into `task.jsonl` without repo-specific inspection.",
        "",
        "## Slot Target",
        "",
        f"- Continuity facet: `{slot['continuity_facet']}` ({slot['continuity_label']})",
        f"- Website type: `{slot['website_type']}`",
        f"- Intent/scope/difficulty: `{slot['intent']}` / `{slot['scope']}` / `{slot['difficulty']}`",
        f"- Design goal: {slot['design_goal']}",
        "",
        "## Candidate Repos",
        "",
    ]
    if not candidates:
        lines.extend(
            [
                "No same-family candidate is available after current reservations.",
                "The slot should be retried with a larger source manifest or released reservations from lower-priority backup candidates.",
                "",
            ]
        )
    for index, record in enumerate(candidates, start=1):
        role = "primary" if index == 1 else f"backup_{index - 1}"
        lines.extend(
            [
                f"### {role}: {record['repo_id']}",
                "",
                f"- Name: `{record.get('owner')}/{record.get('name')}`",
                f"- Framework: `{record.get('framework')}`",
                f"- Package manager: `{record.get('package_manager')}`",
                f"- Zip: `{record.get('zip_path')}`",
                f"- Build command: `{record.get('build_command') or '-'}`",
                f"- Dev command: `{record.get('dev_command') or '-'}`",
                f"- Media count: `{json.dumps(record.get('media_count', {}), sort_keys=True)}`",
                "",
                "Evidence cues:",
            ]
        )
        lines.extend(f"- {reason}" for reason in candidate_reason(record, slot))
        lines.append("")
    lines.extend(
        [
            "## Agent Execution Protocol",
            "",
            "1. Extract the primary candidate and run build/runability checks.",
            "2. Capture desktop, mobile, and at least one task-specific state before designing the problem.",
            "3. Inspect source files and assets that explain the captured UI.",
            "4. Write a separate agent-authored design brief after inspection; this generated card is only triage input.",
            "5. Draft exactly one repo-grounded task for this slot, with required states and hidden states tied to evidence.",
            "6. Write verifier specs that check completion, continuity, regression, and source-fit signals.",
            "7. Run reference sanity; reject or redesign if the verifier cannot distinguish a real solution from a shortcut.",
            "8. Fill the agent audit notes with concrete pass/fail reasons and freeze only after all checks pass.",
            "",
            "## Required Outputs",
            "",
            "- separate agent-authored design brief",
            "- task JSONL record",
            "- verifier spec record",
            "- rationale/provenance records",
            "- captured state report",
            "- package export",
            "- reference verification report",
            "",
        ]
    )
    return "\n".join(lines)


def draft_candidate_review_cards(
    ledger_path: Path,
    manifest_path: Path,
    output_root: Path,
    limit: int,
    candidates_per_slot: int,
    updated_ledger_path: Path | None = None,
) -> dict[str, Any]:
    assert_not_under_formal_task_root(output_root, purpose="Candidate review cards")
    if updated_ledger_path:
        assert_not_under_formal_task_root(updated_ledger_path, purpose="Updated candidate planning ledger")
    entries = list(read_jsonl(ledger_path))
    repos = list(read_jsonl(manifest_path))
    ensure_dir(output_root)
    unavailable = used_repo_ids(entries)
    drafted: list[dict[str, Any]] = []
    for slot in [entry for entry in entries if entry["status"] == "open"][:limit]:
        candidates = select_slot_candidates(slot, repos, unavailable, candidates_per_slot)
        candidate_ids = [record["repo_id"] for record in candidates]
        unavailable.update(candidate_ids)
        card_path = output_root / f"{slot['slot_id']}.md"
        card_path.write_text(render_candidate_review_card(slot, candidates), encoding="utf-8")
        sidecar = {
            "slot": slot,
            "card_path": str(card_path),
            "artifact_type": "candidate_review_card",
            "formal_design_brief": False,
            "formal_task_record": False,
            "warning": "Generated triage aid only. Formal benchmark tasks must be authored one by one after repo inspection.",
            "candidate_repos": [
                {
                    "repo_id": record["repo_id"],
                    "owner": record.get("owner"),
                    "name": record.get("name"),
                    "framework": record.get("framework"),
                    "package_manager": record.get("package_manager"),
                    "zip_path": record.get("zip_path"),
                    "score": repo_slot_score(record, slot)[0],
                    "why_candidate": candidate_reason(record, slot),
                }
                for record in candidates
            ],
            "candidate_shortage": max(0, candidates_per_slot - len(candidates)),
        }
        write_json(output_root / f"{slot['slot_id']}.json", sidecar)
        slot["status"] = "review_card_ready"
        slot["stage"] = "S1_repo_triage"
        slot["candidate_review_card_path"] = str(card_path)
        slot["candidate_repo_ids"] = candidate_ids
        slot["primary_candidate_repo_id"] = candidate_ids[0] if candidate_ids else None
        slot["agent_decision"] = "candidate_review_card_ready"
        drafted.append(sidecar)
    if updated_ledger_path:
        write_jsonl(updated_ledger_path, entries)
    summary = {
        "generated_at": utc_now(),
        "ledger_path": str(ledger_path),
        "manifest_path": str(manifest_path),
        "output_root": str(output_root),
        "limit": limit,
        "candidates_per_slot": candidates_per_slot,
        "drafted": len(drafted),
        "artifact_type": "candidate_review_card",
        "formal_design_briefs": 0,
        "formal_task_records": 0,
        "cards": [item["card_path"] for item in drafted],
        "warning": "Candidate review cards are generated triage aids. They are not accepted tasks.",
    }
    write_json(output_root / "summary.json", summary)
    return summary


def draft_agent_briefs(
    ledger_path: Path,
    manifest_path: Path,
    output_root: Path,
    limit: int,
    candidates_per_slot: int,
    updated_ledger_path: Path | None = None,
) -> dict[str, Any]:
    return draft_candidate_review_cards(
        ledger_path=ledger_path,
        manifest_path=manifest_path,
        output_root=output_root,
        limit=limit,
        candidates_per_slot=candidates_per_slot,
        updated_ledger_path=updated_ledger_path,
    )


def add_build_slots_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--current-tasks", type=Path, default=DEFAULT_OUTPUT_ROOT / "tasks" / "dev_next.jsonl")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_OUTPUT_ROOT / "manifest" / "repos.jsonl")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "authoring_ledger")
    parser.add_argument("--total", type=int, default=TOTAL_TARGET_TASKS)


def run_build_slots_from_args(args: argparse.Namespace) -> None:
    try:
        entries = build_slot_ledger(
            current_tasks_path=args.current_tasks,
            manifest_path=args.manifest,
            total=args.total,
        )
        summary = write_slot_ledger(
            entries=entries,
            output_root=args.output_root,
            current_tasks_path=args.current_tasks,
            manifest_path=args.manifest,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(
        f"built {summary['total']} SiteContinuum authoring slots "
        f"({summary['accepted']} accepted, {summary['open']} open)"
    )


def add_draft_candidate_review_cards_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ledger", type=Path, default=DEFAULT_OUTPUT_ROOT / "authoring_ledger" / "tasks_400_ledger.jsonl")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_OUTPUT_ROOT / "manifest" / "repos.jsonl")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "candidate_review_cards" / "wave_001")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--candidates-per-slot", type=int, default=3)
    parser.add_argument("--updated-ledger", type=Path, default=None)


def run_draft_candidate_review_cards_from_args(args: argparse.Namespace) -> None:
    try:
        summary = draft_candidate_review_cards(
            ledger_path=args.ledger,
            manifest_path=args.manifest,
            output_root=args.output_root,
            limit=args.limit,
            candidates_per_slot=args.candidates_per_slot,
            updated_ledger_path=args.updated_ledger,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(
        f"wrote {summary['drafted']} candidate review cards under {summary['output_root']} "
        "(0 formal design briefs, 0 formal task records)"
    )


def add_draft_briefs_args(parser: argparse.ArgumentParser) -> None:
    add_draft_candidate_review_cards_args(parser)


def run_draft_briefs_from_args(args: argparse.Namespace) -> None:
    try:
        summary = draft_agent_briefs(
            ledger_path=args.ledger,
            manifest_path=args.manifest,
            output_root=args.output_root,
            limit=args.limit,
            candidates_per_slot=args.candidates_per_slot,
            updated_ledger_path=args.updated_ledger,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(
        f"deprecated alias wrote {summary['drafted']} candidate review cards under {summary['output_root']} "
        "(not formal design briefs or task records)"
    )
