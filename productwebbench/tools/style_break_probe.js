// Find declarations whose REMOVAL changes what the browser renders -- the
// evidence a Repair axis needs before it is allowed to delete anything.
//
// WHY A THIRD PROBE. `typography_anchor_probe.js` answers "which value does a
// stylesheet declare here", which is enough for T-C05/T-C06: those axes ask the
// solver to CHANGE a value, so the delivered workspace fails by construction.
// A repair axis inverts that. It deletes the declaration and then asserts the
// ORIGINAL value, so the whole task rests on a claim the earlier probe never
// checked: that this declaration is the one holding the value up. If a second
// rule declares the same thing, deleting the first changes nothing, the gate
// passes on the delivered workspace with zero edits, and the slot is T-R01's
// free pass again (344 of them).
//
// SO THE PROOF IS MADE HERE, IN THE BROWSER. For every candidate the probe
// removes the property from the winning rule through CSSOM, re-reads the
// computed value on every visible element the selector matches, and restores
// the declaration byte-for-byte (value AND priority). An anchor survives only
// when the value actually moved for all of them. Nothing about the source text
// is inferred -- that part is `style_break_scan.py`'s job, and it re-measures
// the tree after the real deletion anyway.
//
// WHAT IS DELIBERATELY NOT REACHED. Rules inside `@media` are invisible here,
// exactly as in the typography probe: `cssRules` yields a CSSMediaRule with no
// `selectorText` and the walk skips it. That is a safety property for this
// axis rather than a gap -- if the value at 1280x800 really comes from a media
// block, the non-media rule this probe finds will not move the computed value
// when removed, and the anchor is dropped instead of minted.
//
// Usage: node style_break_probe.js <spec.json> <output.json>
//   spec.json = {"routes": [...], "properties": [...]}
const { chromium } = require("playwright");
const fs = require("fs");

// The union of the T-C05 and T-C06 sets. A repair slot needs three anchors in
// three DIFFERENT rules; drawing on type and spacing together is what lifts a
// repo over that floor, and both sets are already known to serialise
// unambiguously (`verifier/computed_value_contract.py`).
const DEFAULT_PROPERTIES = [
  "font-size", "font-weight", "letter-spacing", "text-transform",
  "padding-top", "padding-left", "margin-bottom",
  "border-radius", "border-top-width",
];
// Own-text floor. Inherited from the TYPOGRAPHY probe, where it is sound: an
// element with no text of its own cannot show a font change. For the LAYOUT
// keyword set it is simply wrong -- a wrapper with no own text is exactly what
// `flex-direction: column` or `display: block` is applied to, and a media query
// restyles containers far more often than it restyles text nodes. Measured
// 2026-09-23 with the census below: 258 of 20,722 flattened rules came from
// matching @media blocks, yet 0 of 283 anchors did, because every element those
// rules target was filtered out here before the cascade was ever consulted.
// Overridable via `spec.min_text`; the default keeps T-C05/T-C06/T-R04 exact.
const DEFAULT_MIN_TEXT = 3;

// Viewport. The default is the 1280x800 desktop every axis before T-R06 used,
// which is also why the media-query note above holds: at 1280 a rule inside
// `@media (max-width: 768px)` does not apply, so an anchor measured there can
// never come from one. T-R06 passes {"width":390,"height":844} -- the same
// numbers `productwebbench/execution/runtime/browser_state.py` maps to the
// "mobile" viewport, so what the scan measures is what the capture will later
// render. Measured 2026-09-23 over 70 repos: 93% ship at least one width
// media query and 79% ship three or more, and none of that material was
// reachable from the desktop viewport.
const DEFAULT_VIEWPORT = { width: 1280, height: 800 };
// Descend into `@media` blocks whose condition currently matches. OFF by
// default, so T-R04 and T-R05 keep the exact walk they were measured on.
// T-R06 turns it on: at 390x844 the winning declaration for a selector is
// frequently a media override, and without this the probe finds the top-level
// rule instead -- which is why 82% of the T-R06 anchors measured on 139 shared
// repositories on 2026-09-23 were byte-identical to T-R05's. The axis was
// re-reading the same declarations under a different layout, not reading the
// responsive half of the stylesheet.

(async () => {
  const [specPath, outputPath] = process.argv.slice(2);
  const spec = JSON.parse(fs.readFileSync(specPath, "utf8"));
  const routes = spec.routes || [];
  if (!routes.length) throw new Error("spec.json needs a non-empty routes[]");
  const PROPERTIES = (Array.isArray(spec.properties) && spec.properties.length)
    ? spec.properties : DEFAULT_PROPERTIES;
  // Re-measure mode: the caller passes the (selector, property) pairs it gated
  // and this run just reports what the page shows now. Used on the BROKEN tree,
  // so the scan can state that the delivered workspace fails -- rather than
  // trusting that a CSSOM removal predicted the real deletion.
  const RECHECK = Array.isArray(spec.recheck) ? spec.recheck : null;
  const VIEWPORT = (spec.viewport && spec.viewport.width && spec.viewport.height)
    ? spec.viewport : DEFAULT_VIEWPORT;
  const INCLUDE_MEDIA = !!spec.include_media;
  const MIN_TEXT = Number.isFinite(spec.min_text) ? spec.min_text : DEFAULT_MIN_TEXT;
  const browser = await chromium.launch();
  const out = { routes: [], errors: [] };
  try {
    for (const url of routes) {
      const page = await browser.newPage({ viewport: VIEWPORT });
      try {
        const response = await page.goto(url, { waitUntil: "networkidle", timeout: 45000 });
        if (!response || !response.ok()) {
          out.errors.push({ url, error: `status ${response && response.status()}` });
          continue;
        }
        const found = await page.evaluate(({ PROPERTIES, MIN_TEXT, RECHECK, INCLUDE_MEDIA }) => {
          const isVisible = (element) => {
            const rect = element.getBoundingClientRect();
            const style = getComputedStyle(element);
            return (
              rect.width > 0 && rect.height > 0 &&
              style.visibility !== "hidden" && style.display !== "none" &&
              Number(style.opacity || "1") > 0.05
            );
          };
          const ownText = (element) => Array.from(element.childNodes)
            .filter((n) => n.nodeType === 3).map((n) => n.textContent.trim()).join("").length;
          const readAll = (selector, property) => {
            let matched = [];
            try { matched = Array.from(document.querySelectorAll(selector)); }
            catch (error) { return null; }
            const visible = matched.filter(isVisible);
            const values = visible.map((e) =>
              String(getComputedStyle(e).getPropertyValue(property) || "").trim());
            return { matched: matched.length, visible: visible.length, values };
          };
          if (RECHECK) {
            // Report only: the caller compares against what it gated. Same
            // {rows, rule_census} envelope as the measuring pass -- returning a
            // bare array here made the summary line read `r.anchors.length` off
            // `undefined` and killed 5 of 30 repos in the 2026-09-23 census.
            return {
              rows: RECHECK.map((want) => {
                const seen = readAll(want.selector, want.property);
                return {
                  recheck: true, selector: want.selector, property: want.property,
                  matched: seen ? seen.matched : 0, visible: seen ? seen.visible : 0,
                  values: seen ? Array.from(new Set(seen.values)) : [],
                };
              }),
              rule_census: { rules: 0, media_rules: 0 },
            };
          }
          const stableClasses = (element) => Array.from(element.classList).filter(
            (c) => c.length >= 2 && c.length <= 40 && !/\d{4,}/.test(c)
              && !/^(css|sc|jsx)-/.test(c) && !/__[A-Za-z0-9]{5,}$/.test(c));
          const selectorFor = (element) => {
            if (element.id && /^[A-Za-z][\w-]*$/.test(element.id)
                && document.querySelectorAll(`#${CSS.escape(element.id)}`).length === 1) {
              return `#${element.id}`;
            }
            const tag = element.tagName.toLowerCase();
            const classes = stableClasses(element);
            for (let take = Math.min(classes.length, 3); take >= 1; take -= 1) {
              const selector = tag + "." + classes.slice(0, take)
                .map((c) => CSS.escape(c)).join(".");
              try {
                if (document.querySelectorAll(selector).length >= 1) return selector;
              } catch (error) { /* invalid selector, try a shorter one */ }
            }
            return null;
          };
          // The rule that WINS the cascade, kept as an OBJECT so the removal
          // test below can mutate and restore it.
          // Style rules in document order, optionally descending into `@media`
          // blocks whose condition matches RIGHT NOW at this viewport. Order is
          // preserved so the "last match wins" approximation below still lines
          // up with the cascade; specificity is still ignored, and the removal
          // proof is what catches a wrong pick (take it away, and if the value
          // does not move for every matched element the anchor is dropped).
          const flatten = (rules, media, out) => {
            for (const rule of Array.from(rules || [])) {
              if (rule.selectorText && rule.style) {
                out.push({ rule, media });
                continue;
              }
              if (!INCLUDE_MEDIA) continue;
              const condition = rule.conditionText
                || (rule.media && rule.media.mediaText) || "";
              if (!condition || !rule.cssRules) continue;
              let applies = false;
              try { applies = window.matchMedia(condition).matches; }
              catch (error) { continue; }
              if (!applies) continue;
              flatten(rule.cssRules, media || condition, out);
            }
            return out;
          };
          // Flattened ONCE per page, not per (element, property): the walk is
          // now recursive and allocating, and the element loop below calls this
          // for every property of every visible element -- rebuilding it each
          // time ran into the 240s scan timeout on large bundles. Sheet and
          // rule order are preserved exactly, so "the last match wins" still
          // approximates the cascade the same way it always did.
          const ALL_RULES = [];
          for (const sheet of Array.from(document.styleSheets)) {
            let rules = null;
            try { rules = sheet.cssRules; } catch (error) { continue; }
            for (const entry of flatten(rules, "", [])) {
              entry.sheet = sheet;
              ALL_RULES.push(entry);
            }
          }
          const declaringRule = (element, property) => {
            let hit = null;
            for (const entry of ALL_RULES) {
              const rule = entry.rule;
              if (!String(rule.style.getPropertyValue(property) || "").trim()) continue;
              let matches = false;
              try { matches = element.matches(rule.selectorText); }
              catch (error) { continue; }
              if (matches) hit = { rule, sheet: entry.sheet, media: entry.media };
            }
            return hit;
          };
          const rows = new Map();
          for (const element of Array.from(document.querySelectorAll("*"))) {
            if (!isVisible(element)) continue;
            if (ownText(element) < MIN_TEXT) continue;
            const style = getComputedStyle(element);
            for (const property of PROPERTIES) {
              const value = String(style.getPropertyValue(property) || "").trim();
              if (!value) continue;
              const selector = selectorFor(element);
              if (!selector) continue;
              const key = selector + "|" + property;
              if (rows.has(key)) continue;
              const declared = declaringRule(element, property);
              if (!declared) continue;               // inherited or UA default
              const before = readAll(selector, property);
              if (!before || before.visible === 0) continue;
              const uniform = before.values.every((v) => v === value);
              if (!uniform) continue;                // `computed_style` grades all of them
              const authored = String(
                declared.rule.style.getPropertyValue(property) || "").trim();
              const priority = declared.rule.style.getPropertyPriority(property) || "";
              if (!authored) continue;
              // THE PROOF: take the declaration away and look again.
              let after = null;
              try {
                declared.rule.style.removeProperty(property);
                const seen = readAll(selector, property);
                after = seen ? Array.from(new Set(seen.values)) : null;
              } finally {
                declared.rule.style.setProperty(property, authored, priority);
              }
              const restored = readAll(selector, property);
              const restored_ok = !!restored
                && restored.values.every((v) => v === value);
              const breaks = !!after && after.length > 0
                && after.every((v) => v !== value);
              rows.set(key, {
                value, selector, property,
                authored, priority,
                rule: declared.rule.selectorText,
                media: declared.media || "",
                sheet: declared.sheet.href || "(inline)",
                after, breaks, restored_ok,
                matched: before.matched, visible: before.visible,
                tag: element.tagName.toLowerCase(),
                text_len: ownText(element),
                area: Math.round(element.getBoundingClientRect().width
                                 * element.getBoundingClientRect().height),
              });
            }
          }
          // `rule_census` answers the one question a zero result cannot: were
          // there media rules at all, and did any of them WIN a cascade? Without
          // it "no media anchors" is indistinguishable from "no media blocks".
          return {
            rows: Array.from(rows.values()),
            rule_census: {
              rules: ALL_RULES.length,
              media_rules: ALL_RULES.filter((e) => e.media).length,
            },
          };
        }, { PROPERTIES, MIN_TEXT, RECHECK, INCLUDE_MEDIA });
        out.routes.push({ url, anchors: found.rows,
                          rule_census: found.rule_census });
      } catch (error) {
        out.errors.push({ url, error: String(error) });
      } finally {
        await page.close();
      }
    }
    fs.writeFileSync(outputPath, JSON.stringify(out, null, 2));
    console.log(JSON.stringify({
      routes: out.routes.length,
      anchors: out.routes.reduce((n, r) => n + r.anchors.length, 0),
      breaking: out.routes.reduce(
        (n, r) => n + r.anchors.filter((a) => a.breaks && a.restored_ok).length, 0),
      errors: out.errors.length,
    }));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
