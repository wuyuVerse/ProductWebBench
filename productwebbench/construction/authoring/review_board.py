from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
from typing import Any

from ...core.config import DEFAULT_OUTPUT_ROOT
from ...core.io_utils import ensure_dir, write_json
from ...core.task_files import task_file_patterns


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_path(path_text: str, base: Path) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    cwd_candidate = path.resolve()
    if cwd_candidate.exists():
        return cwd_candidate
    return (base / path).resolve()


def href(path: Path, output_root: Path) -> str:
    resolved = Path(path).resolve()
    return Path(os.path.relpath(resolved, output_root.resolve())).as_posix()


def text_list(items: list[str], limit: int = 6) -> str:
    if not items:
        return "<li>None</li>"
    return "\n".join(f"<li>{html.escape(str(item))}</li>" for item in items[:limit])


def markdown_list(items: list[str], limit: int = 6) -> str:
    if not items:
        return "- None"
    return "\n".join(f"- {item}" for item in items[:limit])


def load_package(package_dir: Path) -> dict[str, Any]:
    return {
        "package_dir": package_dir,
        "task": load_json(package_dir / "task.json"),
        "reference": load_json(package_dir / "reference_verifier.json"),
        "submission": load_json(package_dir / "submission_verifier.json"),
        "anchors": load_json(package_dir / "design_anchors.summary.json"),
        "provenance": load_json(package_dir / "provenance.json"),
        "evidence": load_json(package_dir / "evidence.json"),
        "rationale": load_json(package_dir / "rationale.json"),
        "asset_gallery": load_json(package_dir / "asset_gallery.json"),
        "reference_artifacts": load_json(package_dir / "reference_artifacts.json"),
    }


def state_cards(package: dict[str, Any], output_root: Path, max_states: int, max_crops: int) -> tuple[list[dict[str, Any]], list[str]]:
    cards: list[dict[str, Any]] = []
    missing: list[str] = []
    for state in package["reference_artifacts"].get("states", [])[:max_states]:
        screenshot = Path(state.get("screenshot", ""))
        if not screenshot.exists():
            missing.append(str(screenshot))
        crops = []
        crops_path = Path(state.get("crops", ""))
        if crops_path.exists():
            for crop in load_json_array(crops_path)[:max_crops]:
                crop_path = Path(crop.get("path", ""))
                if crop_path.exists():
                    crops.append(
                        {
                            "kind": crop.get("kind"),
                            "text": crop.get("text", ""),
                            "path": href(crop_path, output_root),
                        }
                    )
        else:
            missing.append(str(crops_path))
        cards.append(
            {
                "state_id": state.get("state_id"),
                "screenshot": href(screenshot, output_root),
                "crops": crops,
            }
        )
    return cards, missing


def load_json_array(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def asset_cards(package: dict[str, Any], output_root: Path, max_assets: int) -> list[dict[str, Any]]:
    cards = []
    for item in package["asset_gallery"].get("items", [])[:max_assets]:
        path = Path(item.get("gallery_path", ""))
        cards.append(
            {
                "repo_path": item.get("repo_path"),
                "kind": item.get("kind"),
                "tags": item.get("semantic_tags", []),
                "path": href(path, output_root) if path.exists() else str(path),
                "is_image": path.suffix.lower() in IMAGE_SUFFIXES,
            }
        )
    return cards


def package_review_item(package: dict[str, Any], output_root: Path, max_states: int, max_crops: int, max_assets: int) -> dict[str, Any]:
    state_items, missing = state_cards(package, output_root, max_states, max_crops)
    task = package["task"]
    rationale = package["rationale"]
    provenance = package["provenance"]
    return {
        "task_id": task["task_id"],
        "repo_id": task["repo_id"],
        "intent": task["intent"],
        "scope": task["scope"],
        "difficulty": task["difficulty"],
        "problem_statement": task.get("problem_statement", ""),
        "required_content": task.get("required_content", []),
        "design_constraints": task.get("design_constraints", []),
        "state_constraints": task.get("state_constraints", []),
        "suggested_files": task_file_patterns(task),
        "required_states": task.get("required_states", []),
        "hidden_states": task.get("hidden_states", []),
        "completion_signals": package["submission"].get("completion_text_signals", []),
        "asset_signals": package["submission"].get("asset_path_signals", []),
        "why_this_repo": rationale.get("why_this_repo", ""),
        "natural_change_location": rationale.get("natural_change_location", ""),
        "asset_grounding": rationale.get("asset_grounding", ""),
        "anti_shortcut_checks": rationale.get("anti_shortcut_checks", []),
        "source_evidence": provenance.get("source_patterns", [])[:4],
        "asset_evidence": provenance.get("asset_patterns", [])[:4],
        "states": state_items,
        "assets": asset_cards(package, output_root, max_assets),
        "quality_pass": package["evidence"].get("quality_pass", False),
        "design_anchor_states": package["anchors"].get("state_ids", []),
        "missing_paths": missing,
        "package_dir": href(package["package_dir"], output_root),
    }


def render_asset(asset: dict[str, Any]) -> str:
    label = html.escape(str(asset["repo_path"]))
    tags = html.escape(", ".join(asset.get("tags", [])) or "-")
    path = html.escape(asset["path"])
    if asset["is_image"]:
        media = f'<img src="{path}" alt="{label}" loading="lazy">'
    else:
        media = f'<a href="{path}">{html.escape(str(asset["kind"]))}</a>'
    return f'<figure>{media}<figcaption>{label}<br><span>{tags}</span></figcaption></figure>'


def render_state(state: dict[str, Any]) -> str:
    screenshot = html.escape(state["screenshot"])
    crops = "\n".join(
        f'<figure><img src="{html.escape(crop["path"])}" alt="{html.escape(str(crop["kind"]))}" loading="lazy">'
        f'<figcaption>{html.escape(str(crop["kind"]))}: {html.escape(str(crop.get("text", ""))[:80])}</figcaption></figure>'
        for crop in state.get("crops", [])
    )
    return f"""
<section class="state-card">
  <h4>{html.escape(str(state["state_id"]))}</h4>
  <a href="{screenshot}"><img class="screenshot" src="{screenshot}" alt="{html.escape(str(state["state_id"]))}" loading="lazy"></a>
  <div class="crop-grid">{crops}</div>
</section>
"""


def render_html(items: list[dict[str, Any]]) -> str:
    task_sections = []
    for item in items:
        states = "\n".join(render_state(state) for state in item["states"])
        assets = "\n".join(render_asset(asset) for asset in item["assets"])
        source_evidence = "\n".join(
            f'<li><code>{html.escape(source.get("pattern", ""))}</code>: {html.escape(str(source.get("match_count", 0)))} matches</li>'
            for source in item["source_evidence"]
        )
        asset_evidence = "\n".join(
            f'<li><code>{html.escape(asset.get("pattern", ""))}</code>: {html.escape(str(asset.get("match_count", 0)))} matches</li>'
            for asset in item["asset_evidence"]
        )
        missing = ""
        if item["missing_paths"]:
            missing = "<p class=\"warning\">Missing artifact paths: " + html.escape(", ".join(item["missing_paths"][:5])) + "</p>"
        task_sections.append(
            f"""
<article class="task" id="{html.escape(item["task_id"])}">
  <header>
    <h2>{html.escape(item["task_id"])}</h2>
    <p><code>{html.escape(item["repo_id"])}</code></p>
    <p><span>{html.escape(item["intent"])}</span> <span>{html.escape(item["scope"])}</span> <span>{html.escape(item["difficulty"])}</span> <span>quality={html.escape(str(item["quality_pass"]))}</span></p>
  </header>
  <p class="statement">{html.escape(item["problem_statement"])}</p>
  <div class="columns">
    <section><h3>Required Content</h3><ul>{text_list(item["required_content"])}</ul></section>
    <section><h3>Design Constraints</h3><ul>{text_list(item["design_constraints"])}</ul></section>
    <section><h3>State Constraints</h3><ul>{text_list(item["state_constraints"])}</ul></section>
  </div>
  <div class="columns">
    <section><h3>Repo Grounding</h3><p>{html.escape(item["why_this_repo"])}</p><p>{html.escape(item["natural_change_location"])}</p></section>
    <section><h3>Asset Grounding</h3><p>{html.escape(item["asset_grounding"])}</p></section>
    <section><h3>Anti-Shortcut Checks</h3><ul>{text_list(item["anti_shortcut_checks"])}</ul></section>
  </div>
  <div class="columns">
    <section><h3>Source Evidence</h3><ul>{source_evidence}</ul></section>
    <section><h3>Asset Evidence</h3><ul>{asset_evidence}</ul></section>
    <section><h3>Machine Signals</h3><ul>{text_list(item["completion_signals"] + item["asset_signals"])}</ul></section>
  </div>
  <h3>Reference States</h3>
  <div class="state-grid">{states}</div>
  <h3>Curated Assets</h3>
  <div class="asset-grid">{assets}</div>
  {missing}
  <p><a href="{html.escape(item["package_dir"])}">Open task package</a></p>
</article>
"""
        )
    body = "\n".join(task_sections)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ProductWebBench Review Board</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; color: #1f2933; background: #f5f7fa; }}
    main {{ max-width: 1320px; margin: 0 auto; padding: 32px 20px 80px; }}
    h1 {{ font-size: 32px; margin: 0 0 8px; }}
    h2 {{ font-size: 22px; margin: 0 0 4px; }}
    h3 {{ font-size: 16px; margin: 20px 0 10px; }}
    h4 {{ margin: 0 0 8px; font-size: 14px; }}
    .task {{ background: #fff; border: 1px solid #d8dee9; border-radius: 8px; margin: 24px 0; padding: 20px; }}
    .statement {{ font-size: 16px; line-height: 1.55; }}
    .columns {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }}
    .columns section {{ border: 1px solid #e4e7eb; border-radius: 6px; padding: 12px; background: #fbfcfd; }}
    ul {{ padding-left: 20px; }}
    li, p {{ line-height: 1.5; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }}
    .state-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }}
    .state-card {{ border: 1px solid #e4e7eb; border-radius: 6px; padding: 12px; background: #fbfcfd; }}
    img.screenshot {{ width: 100%; max-height: 360px; object-fit: contain; background: #eef2f7; border: 1px solid #d8dee9; }}
    .crop-grid, .asset-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 10px; }}
    figure {{ margin: 0; border: 1px solid #e4e7eb; border-radius: 6px; background: white; padding: 8px; }}
    figure img {{ width: 100%; height: 96px; object-fit: contain; background: #f5f7fa; }}
    figcaption {{ font-size: 11px; line-height: 1.35; overflow-wrap: anywhere; color: #52606d; }}
    .warning {{ color: #9a3412; font-weight: 600; }}
    @media (max-width: 820px) {{ .columns {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
<main>
  <h1>ProductWebBench Review Board</h1>
  <p>Agent audit surface for repo-grounded website-change tasks. Each item shows the problem, evidence, reference states, component crops, curated assets, and anti-shortcut rationale.</p>
  {body}
</main>
</body>
</html>
"""


def render_markdown(items: list[dict[str, Any]]) -> str:
    sections = ["# ProductWebBench Review Board\n"]
    for item in items:
        sections.append(
            f"""## {item['task_id']}

- Repo: `{item['repo_id']}`
- Intent/scope/difficulty: `{item['intent']}` / `{item['scope']}` / `{item['difficulty']}`
- Package: `{item['package_dir']}`

{item['problem_statement']}

### Required Content

{markdown_list(item['required_content'])}

### Design Constraints

{markdown_list(item['design_constraints'])}

### State Evidence

{markdown_list([state['state_id'] for state in item['states']], limit=20)}

### Asset Evidence

{markdown_list([asset['repo_path'] for asset in item['assets']], limit=20)}

### Anti-Shortcut Checks

{markdown_list(item['anti_shortcut_checks'])}
"""
        )
    return "\n".join(sections)


def build_review_board(
    package_manifest_path: Path,
    output_root: Path,
    max_states: int,
    max_crops: int,
    max_assets: int,
) -> dict[str, Any]:
    manifest = load_json(package_manifest_path)
    ensure_dir(output_root)
    items = []
    for package_item in manifest.get("packages", []):
        package_dir = resolve_path(package_item["package_dir"], package_manifest_path.parent)
        package = load_package(package_dir)
        items.append(package_review_item(package, output_root, max_states, max_crops, max_assets))

    summary = {
        "package_manifest_path": str(package_manifest_path),
        "output_root": str(output_root),
        "total": len(items),
        "items": items,
        "missing_path_count": sum(len(item["missing_paths"]) for item in items),
        "html_path": str(output_root / "index.html"),
        "markdown_path": str(output_root / "index.md"),
    }
    write_json(output_root / "manifest.json", summary)
    (output_root / "index.html").write_text(render_html(items), encoding="utf-8")
    (output_root / "index.md").write_text(render_markdown(items), encoding="utf-8")
    return summary


def add_review_board_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--package-manifest", type=Path, default=DEFAULT_OUTPUT_ROOT / "bench" / "dev" / "manifest.json")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "review" / "dev")
    parser.add_argument("--max-states", type=int, default=4)
    parser.add_argument("--max-crops", type=int, default=8)
    parser.add_argument("--max-assets", type=int, default=12)


def run_review_board_from_args(args: argparse.Namespace) -> None:
    summary = build_review_board(
        package_manifest_path=args.package_manifest,
        output_root=args.output_root,
        max_states=args.max_states,
        max_crops=args.max_crops,
        max_assets=args.max_assets,
    )
    print(
        f"built review board for {summary['total']} tasks at {summary['html_path']} "
        f"({summary['missing_path_count']} missing paths)"
    )
