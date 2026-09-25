# dev_facebook__regenerator__cb755fd82c64__docs_asset_map

## Repo

- Repo id: `facebook__regenerator__cb755fd82c64`
- Split: `dev`
- Intent: `add`
- Scope: `cross_page`
- Difficulty: `L3`
- Evidence: `quality-pass`
- Design anchors: `available`
- Repo provenance: `repo-grounded`

## Problem Statement

The facebook/regenerator docs site is a static browser page at docs/index.html with an interactive transformer sandbox, plus local yield-ahead image assets (docs/yield_ahead.200px.png, docs/yield_ahead.svg, docs/yield_ahead.16px.png) and three package README files (packages/runtime/README.md, packages/transform/README.md, packages/preset/README.md). The site carries a shared masthead navigation rendered from a shared nav-links registry (docs/nav-links.js) mounted by docs/site-nav.js, with a nav-link class on each anchor, and a shared footer. There is no dedicated docs page that maps the documentation back to the real repository files it depends on. Add a new cross-page Docs Asset Map page at /docs/asset-map.html built from the shared docs-card pattern (a local yield-ahead image, an h3 headline, and a short note per card) and wrap it in the same shared masthead nav and footer chrome, then register an Asset Map entry in the shared nav-links registry so the Asset Map nav-link renders consistently on both the docs home and the new Asset Map page. Do not convert the docs into a new app or build system, remove the transformer sandbox or the Regenerator/Traceur comparison toggle, or replace the local yield-ahead image assets; the new page must conform to the existing static docs design system (sans-serif type, simple bordered cards, compact content column) and to the shared cross-page navigation.

## Required Content

- Add a new Docs Asset Map page at /docs/asset-map.html composed from the shared docs-card grid pattern: at least six cards each with a local yield-ahead image, an h3 headline, and a short note.
- Register an Asset Map entry in the shared nav-links registry (docs/nav-links.js) so an <a class="nav-link"> for Asset Map renders in the shared masthead navigation on both the docs home and the Asset Map page.
- Wrap the new Asset Map page in the shared masthead nav and footer chrome so the shared navigation renders on it exactly as on the docs home.
- Keep the six card headlines Open the docs background sign, Compare the vector source icon, Scan the browser favicon tile, Read the runtime README, Study the transform README, and Finish at the preset README, and the page heading Trace the docs to the real repository files.
- Show the local yield_ahead.200px.png and yield_ahead.svg assets with meaningful alt text Yield ahead 200px documentation asset and Yield ahead SVG documentation asset and link the three package README sources packages/runtime/README.md, packages/transform/README.md, and packages/preset/README.md from the cards.
- Preserve the original docs home content and behavior: the get serious about ES6 generator functions heading, the Regenerator/Traceur comparison toggle, the Input: and Output: sandbox, the report and run links, the An open-source project from Facebook. attribution, and the local docs assets; do not add a standalone off-system page, a bespoke Cheatsheet layout, or a Back to home shortcut.
- The Docs Asset Map page must have no horizontal overflow on desktop, 834px tablet, and 390px mobile, and the shared docs cards must stack cleanly on 390px mobile.

## Design Constraints

- Keep the implementation in the existing static docs architecture; do not introduce React, a build system, a new package manager, or external asset hosting.
- Reuse the shared masthead nav and footer and the shared nav-links registry rather than inventing a one-off navigation.
- The Docs Asset Map page is a repo-grounded index that maps docs surfaces back to real repository files, not a marketing hero or a separate app.
- Use only repo-local yield-ahead assets and package README links for the new page; do not add generic stock images or an off-system Cheatsheet surface.
- Desktop uses a compact multi-column docs-card grid; 390px mobile stacks the cards without horizontal overflow.

## State Constraints

- desktop_home / mobile_home / tablet_home capture the original Regenerator docs home now carrying the shared masthead nav (with the registered Asset Map nav-link) and shared footer.
- desktop_asset_map / mobile_asset_map / tablet_asset_map capture the new Docs Asset Map page: shared nav with the Asset Map nav-link, the six-card shared docs grid, and the shared footer.
- desktop_comparison_open clicks the existing Regenerator/Traceur comparison toggle and verifies the Traceur comparison list is preserved after the new page is added.
- Source inspection confirms the shared nav is rendered from docs/nav-links.js and mounted by docs/site-nav.js on every page, so a registered cross-page nav link is machine-detectable.

## Assets To Consider

- docs/index.html
- docs/sandbox.css
- docs/site.css
- docs/site-nav.js
- docs/yield_ahead.200px.png
- docs/yield_ahead.svg
- docs/yield_ahead.16px.png
- packages/runtime/README.md
- packages/transform/README.md
- packages/preset/README.md

## Suggested Files

- docs/asset-map.html
- docs/nav-links.js

## Reference States

- desktop_home
- mobile_home
- tablet_home
- desktop_asset_map
- mobile_asset_map
- tablet_asset_map
- desktop_comparison_open

## Submission States

- desktop_home
- mobile_home
- tablet_home
- desktop_asset_map
- mobile_asset_map
- tablet_asset_map
- desktop_comparison_open

## Machine-Checkable Completion Signals

- Trace the docs to the real repository files
- Open the docs background sign
- Compare the vector source icon
- Scan the browser favicon tile
- Read the runtime README
- Study the transform README
- Finish at the preset README

## Visual Anchor States

- desktop_home
- desktop_asset_map

## Asset Path Signals

- yield_ahead

## Image Alt Signals

- None

## Evidence From Repo

- Project root: `/volume/pt-coder/users/jjwu/MultiModel/data/sitecontinuum/workspaces_slot_209_reference_wsroot/facebook__regenerator__cb755fd82c64`

- Zip source: `None`

Source file evidence:
- `docs/asset-map.html`
- `docs/nav-links.js`
- `docs/index.html`
- `docs/sandbox.css`
- `docs/site.css`

Asset/component evidence:
- `docs/index.html`
- `docs/sandbox.css`
- `docs/site.css`
- `docs/site-nav.js`
- `docs/yield_ahead.200px.png`

Code keyword evidence:
- `facebook/regenerator` -> `README.md:1`
- `browser` -> `docs/asset-map.html:41`
- `transformer` -> `README.md:71`
- `sandbox` -> `README.md:61`
- `yield-ahead` -> `docs/asset-map.html:32`

Browser-state text evidence:
- `desktop_home` contains get serious about ES6 generator functions, Regenerator, Traceur, Input:, Output:, An open-source project from Facebook
- `mobile_home` contains get serious about ES6 generator functions, Regenerator, Traceur, Input:, Output:, An open-source project from Facebook
- `tablet_home` contains get serious about ES6 generator functions, Regenerator, Traceur, Input:, Output:, An open-source project from Facebook

## Asset Gallery

- Selected assets: `3`
- Asset kinds: `{"preview_image": 3}`
- `docs/yield_ahead.16px.png` (preview_image, tags: -)
- `docs/yield_ahead.200px.png` (preview_image, tags: -)
- `docs/yield_ahead.svg` (preview_image, tags: -)

## Construction Rationale

### Why This Repo

facebook/regenerator ships a real static browser docs site at docs/index.html: an explanatory article about ES6 generator functions, an interactive Regenerator transformer sandbox with Input/Output CodeMirror panels, a Regenerator/Traceur comparison toggle, local yield-ahead image assets (yield_ahead.200px.png, yield_ahead.svg, yield_ahead.16px.png) and three package README files for runtime, transform and preset. As declared offline infrastructure identical across all three legs it carries a shared masthead navigation rendered from a shared nav-links registry (docs/nav-links.js) where every item is an anchor carrying the nav-link class, plus a shared footer, which makes it a clean base for a content_asset_continuity Consistency task that requires a new docs page to conform to the shared docs-card pattern and register its link in the shared cross-page navigation rather than diverging into an off-system one-off.

### Natural Change Location

The new page and its cross-page registration live where the shared design system already lives: the new page is added at the /docs/asset-map.html route composed from the same shared docs-card grid pattern (a local yield-ahead image, an h3 headline and a short note per card) and wrapped in the same shared masthead nav and footer chrome as the docs home (docs/index.html), and an Asset Map entry is registered in the shared nav registry docs/nav-links.js so the nav-link renders in the shared masthead on both the docs home and the Asset Map page.

### Asset Grounding

The Docs Asset Map cards use existing local assets shipped in the repo (docs/yield_ahead.200px.png for the docs background sign, docs/yield_ahead.svg for the vector source icon and docs/yield_ahead.16px.png for the favicon tile) and link the three real package README sources (packages/runtime/README.md, packages/transform/README.md and packages/preset/README.md), so the imagery and copy are grounded in the actual repo content rather than external stock imagery.

### Design Integration

The Docs Asset Map page is built from the shared docs-card grid (a local yield-ahead image, an h3 headline and a note per card), wrapped in the shared masthead nav and footer chrome, and its link is registered in the shared nav registry so the Asset Map nav-link renders cross-page on the docs home and the Asset Map page exactly like the other nav entries, keeping the new page inside the existing static docs sans-serif, simple-bordered-card aesthetic and cross-page navigation rather than diverging into a bespoke layout.

### State Coverage

Seven states were captured to cover the cross-page design-system invariant: the docs home on desktop, 390px mobile and 834px tablet, the new Docs Asset Map page on desktop, mobile and tablet, and a comparison-open interaction state that toggles the Regenerator/Traceur list, so the shared masthead nav (including the Asset Map nav-link), the shared docs-card grid, the footer, the preserved comparison toggle, and the absence of horizontal overflow on the new page are all observable across viewports.

### Repo Evidence

- The docs home renders the shared masthead navigation from a shared nav registry docs/nav-links.js where each item is an anchor carrying the nav-link class, so registering a new Asset Map entry makes a cross-page nav-link render on every page.
- The docs ship local yield-ahead image assets (yield_ahead.200px.png, yield_ahead.svg and yield_ahead.16px.png) and three package README files under packages/runtime, packages/transform and packages/preset, grounding the Asset Map cards in the real repository files rather than external stock.
- The docs home carries the interactive transformer sandbox with Input/Output panels and the Regenerator/Traceur comparison toggle, so the additive Asset Map page can map the docs back to real files without removing or restyling the demo.
- The docs are served as a static directory with real HTTP 404s, so a lacking submission that never adds the page produces an observable 404 on the /docs/asset-map.html route.

### Anti-Shortcut Checks

- A lacking submission that never adds the page 404s the /docs/asset-map.html route and omits the Asset Map nav entry, so the Asset Map nav-link is absent on the docs home too and state_quality, completion_text_signals, visible_text_signals, console_errors and dom_assertions all fail.
- An off-system submission that ships a bespoke unregistered Regenerator Asset Cheatsheet page with inline rows and shortened filler instead of the shared docs-card grid is caught by forbidden_text_patterns (Cheatsheet, Back to home) and by dom_assertions (no card h3 headlines, no yield-ahead images, no Asset Map nav-link).
- The cross-page nav-link dom_assertions require an a.nav-link Asset Map on both the Home and Asset Map states across viewports, so a page that renders in isolation without registering in the shared nav cannot pass.
- The source audit confirms the reference differs from the baseline only by the new docs/asset-map.html page and the docs/nav-links.js registration, so a shortcut that restyles or removes existing docs content would break the clean ADD diff.

### Verifier Alignment

- verify-reference and verify-submission PASS on the reference across the Docs Asset Map identity signals, the shared docs-card/nav DOM structure, the completion, responsive and zero-disallowed-console-error checks, with zero failed network requests.
- The baseline FAILS state_quality, completion_text_signals, visible_text_signals, asset_path_signals, console_errors (the /docs/asset-map.html 404) and dom_assertions.
- The off-system bad solution FAILS completion_text_signals, visible_text_signals, forbidden_text_patterns (Cheatsheet and Back to home), asset_path_signals, visual_anchor_similarity and dom_assertions.
- The submission specs enforce enforce_console_errors (max 0) across all reference states and add cross-page dom_assertions for the Asset Map nav-link plus the shared docs-card structure, so the acceptance is machine-checked rather than text-signal only.

### Agent Audit Notes

- This slot was retrofitted to a product-grade content_asset_continuity Consistency task on the same repo_id, keeping the ledger task_id unchanged, and made distinct from the sibling slot_263 on the same repo which targets a transform-pipeline evidence layout.
- The submission specs now include enforce_console_errors (max 0, allowlist _vercel/google-analytics/googletagmanager/favicon/insights/script) and cross-page dom_assertions requiring an a.nav-link Asset Map on the Home and Asset Map pages plus the shared docs-card structure (h1, section, six h3, six yield-ahead images, nav, footer).
- The remote Facebook JSSDK script and the remote S3 fork-me ribbon image were removed and a local shared nav-links registry, site-nav renderer and site.css design system were added as declared offline infrastructure identical across all three legs so browser capture makes zero external network requests, and the transformer sandbox and comparison toggle are preserved untouched.
- Seven states (Home, Asset Map and a comparison-open interaction) were captured per leg and the reference is runtime-clean with no horizontal overflow on the new Asset Map page.

## Evaluation Rubric

### Change Completion

- The Docs Asset Map page renders the six shared docs cards, the page heading, the local yield-ahead images, and the package README links.
- The Asset Map nav-link is registered and renders on both the docs home and the Docs Asset Map page.

### Design Consistency

- The Docs Asset Map page matches the static docs sans-serif, simple-bordered-card visual system.
- The page is an index built from the shared docs-card pattern and the shared nav/footer chrome, not an off-system one-off Cheatsheet.

### Regression

- The original generator explanation, Regenerator/Traceur comparison toggle, Input/Output sandbox, report/run links, Facebook attribution, and local docs assets remain intact.
- A correct patch is an additive HTML/CSS/registry change, not a framework rewrite or asset swap.

### Responsive

- The Docs Asset Map page has no horizontal overflow on desktop, 834px tablet, and 390px mobile.
- The shared docs cards stack to a single column on 390px mobile without clipping the card images or headlines.

## Author Notes

Retrofitted to a product-grade content_asset_continuity Consistency (add/cross_page) task on the facebook/regenerator static docs site. A new /docs/asset-map.html Docs Asset Map page is composed from the shared docs-card grid (a local yield-ahead image, an h3 headline, and a note per card) and wrapped in the shared masthead nav/footer, and an Asset Map entry is registered in the shared nav-links registry (docs/nav-links.js) so the cross-page nav-link renders on both pages. Reference (page + registered nav + shared cards), baseline (no page, so /docs/asset-map.html 404s, and no Asset Map nav-link on home), and an off-system bad solution (standalone teal Cheatsheet with Back to home, no shared cards so zero card h3 and zero images, unregistered) were each assembled and served as a static docs site on distinct ports with real HTTP 404s. The offline infrastructure (removing the remote Facebook JSSDK script and the remote S3 fork-me ribbon image, and manufacturing the shared nav-links registry, site-nav renderer, and site.css design system) is identical across all three legs and is declared, not part of the task content. The docs home has an inherent CodeMirror-driven mobile-width overflow present identically in all three legs, so the responsive no_horizontal_overflow gate is scoped to the new Asset Map page states only.
