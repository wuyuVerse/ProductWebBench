// Discover, for one rendered site, which element actually CARRIES a given brand
// colour -- and name it with a selector a `computed_style` gate can grade.
//
// Why this exists: T-C01 (palette swap) is suspended because every one of its
// stages gates on `forbidden_text`, which no evaluator implements, and the only
// colour-aware gate the verifier does implement is
// `computed_style(selector, property, expect)` -- which needs a CSS SELECTOR.
// data/repo_profiles/*.json records hex FREQUENCIES and nothing else: no
// selector, no property. Guessing one offline is the failure mode
// selector_contract.py documents (slot 80001). So the triple is measured here,
// in the browser, against the same vendored Chromium the capture uses.
//
// The visibility predicate is copied verbatim from anchor_visibility_probe.js
// (itself copied from playwright_capture.js `isVisible`) for the same reason it
// was copied there: a gate is graded against those exact rules, so an anchor
// the capture would discard is worthless however well it matches the colour.
//
// Usage: node palette_anchor_probe.js <spec.json> <output.json>
//   spec.json = {"routes": ["http://127.0.0.1:PORT/", ...], "hex": "#e84545"}
const { chromium } = require("playwright");
const fs = require("fs");

const PROPERTIES = ["color", "background-color", "border-top-color",
                    "border-bottom-color", "border-left-color", "border-right-color"];

(async () => {
  const [specPath, outputPath] = process.argv.slice(2);
  const spec = JSON.parse(fs.readFileSync(specPath, "utf8"));
  const routes = spec.routes || [];
  // A LIST of colours, not one. Measured on 2026-09-20: a C-02 task whose four
  // stages all swapped the same colour was cleared with ONE edit in 15 turns,
  // because every route drew that colour from the same stylesheet. Stages have
  // to ask for independent changes, and independent colours are the cheapest
  // honest way to get them -- each is a separate declaration to find and move.
  const hexes = (spec.hexes || (spec.hex ? [spec.hex] : []))
    .map((h) => String(h).toLowerCase())
    .filter((h) => /^#[0-9a-f]{6}$/.test(h));
  if (!routes.length || !hexes.length) {
    throw new Error("spec.json needs a non-empty routes[] and hexes[]");
  }
  const browser = await chromium.launch();
  const out = { hexes, routes: [], errors: [] };
  try {
    for (const url of routes) {
      const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
      try {
        const response = await page.goto(url, { waitUntil: "networkidle", timeout: 45000 });
        if (!response || !response.ok()) {
          out.errors.push({ url, error: `status ${response && response.status()}` });
          continue;
        }
        const found = await page.evaluate(({ hexes, PROPERTIES }) => {
          const isVisible = (element) => {
            const rect = element.getBoundingClientRect();
            const style = getComputedStyle(element);
            return (
              rect.width > 0 && rect.height > 0 &&
              style.visibility !== "hidden" && style.display !== "none" &&
              Number(style.opacity || "1") > 0.05
            );
          };
          const toHex = (value) => {
            const m = /^rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)$/.exec(value || "");
            if (!m) return null;
            if (m[4] !== undefined && Number(m[4]) < 0.9) return null;  // near-transparent
            return "#" + [m[1], m[2], m[3]]
              .map((c) => Number(c).toString(16).padStart(2, "0")).join("");
          };
          // A class is usable in a gate only if it is stable across builds.
          // Hashed/utility-generated names (css-1q2w3e, sc-AbCdEf, index__x__a1b2)
          // change whenever the site rebuilds, so a gate anchored on one grades
          // a different element after any correct edit.
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
          const rows = new Map();
          for (const element of Array.from(document.querySelectorAll("*"))) {
            if (!isVisible(element)) continue;
            const style = getComputedStyle(element);
            for (const property of PROPERTIES) {
              const painted = toHex(style.getPropertyValue(property));
              if (!painted || !hexes.includes(painted)) continue;
              const hex = painted;
              const selector = selectorFor(element);
              if (!selector) continue;
              const key = hex + "|" + selector + "|" + property;
              if (rows.has(key)) { rows.get(key).hits += 1; continue; }
              // An anchor is only gradeable if EVERY element the selector
              // matches carries the colour: `computed_style` reads the first
              // match, and a selector that also covers untouched elements makes
              // the gate's verdict depend on document order.
              const matched = Array.from(document.querySelectorAll(selector));
              const visible = matched.filter(isVisible);
              const uniform = visible.length > 0 && visible.every(
                (other) => toHex(getComputedStyle(other).getPropertyValue(property)) === hex);
              const rect = element.getBoundingClientRect();
              // WHICH stylesheet paints this colour. T-R02 breaks a stylesheet
              // reference and needs the render to change; a canary that broke
              // the last <link> blindly left 19/120 repos looking identical
              // afterwards, because the colour came from a different sheet.
              // CSSOM gives the answer exactly: the last matching rule that
              // sets this property wins the cascade for this element.
              let source = null;
              for (const sheet of Array.from(document.styleSheets)) {
                let rules = null;
                try { rules = sheet.cssRules; } catch (error) { continue; }
                for (const rule of Array.from(rules || [])) {
                  if (!rule.selectorText || !rule.style) continue;
                  let matches = false;
                  try { matches = element.matches(rule.selectorText); }
                  catch (error) { continue; }
                  if (!matches) continue;
                  if (toHex(rule.style.getPropertyValue(property)) === hex) {
                    source = sheet.href || "(inline)";
                  }
                }
              }
              rows.set(key, {
                hex, selector, property, hits: 1, source,
                matched: matched.length, visible: visible.length, uniform,
                tag: element.tagName.toLowerCase(),
                area: Math.round(rect.width * rect.height),
              });
            }
          }
          return Array.from(rows.values());
        }, { hexes, PROPERTIES });
        out.routes.push({ url, anchors: found });
      } catch (error) {
        out.errors.push({ url, error: String(error) });
      } finally {
        await page.close();
      }
    }
    fs.writeFileSync(outputPath, JSON.stringify(out, null, 2));
    console.log(JSON.stringify({
      hexes, routes: out.routes.length,
      anchors: out.routes.reduce((n, r) => n + r.anchors.filter((a) => a.uniform).length, 0),
      errors: out.errors.length,
    }));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
