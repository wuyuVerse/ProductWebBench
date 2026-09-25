// Discover, for one rendered site, which element carries a DECLARED typography
// value -- and name it with a selector a `computed_style` gate can grade.
//
// Why a second probe instead of a flag on palette_anchor_probe.js: that one is
// driven by a hex list ("find who paints #e84545"). Typography has no such
// input -- nobody hands us a font size to look for -- so the enumeration runs
// the other way round: read every visible element, keep the ones whose value
// some stylesheet rule actually DECLARES, and report what it is.
//
// THE INHERITANCE TRAP, and why `rule` is mandatory here in a way it was not
// for colour. `font-size` inherits. A site that sets `body { font-size: 16px }`
// makes several hundred elements report "16px" with no declaration of their
// own, and every one of them looks like a uniform anchor. Two stages minted
// from two such elements would both be satisfied by one edit to the body rule
// -- exactly the free pass that suspended T-C01. So an anchor is emitted only
// when CSSOM shows a rule that matches this element and sets this property; the
// winning rule's selectorText is reported so the minter can refuse two stages
// that would be served by editing one declaration.
//
// The visibility predicate is copied verbatim from palette_anchor_probe.js
// (itself from playwright_capture.js `isVisible`): a gate is graded against
// those exact rules, so an anchor the capture would discard is worthless.
//
// Usage: node typography_anchor_probe.js <spec.json> <output.json>
//   spec.json = {"routes": ["http://127.0.0.1:PORT/", ...]}
const { chromium } = require("playwright");
const fs = require("fs");

// Only properties whose RESOLVED form is unambiguous, because the gate compares
// the computed string exactly. `font-family` is excluded on purpose: its
// resolved value is a whole stack and browser_probes has to special-case it.
// `line-height` is excluded because it resolves to "normal" on elements that
// never declare it, which reads as a uniform anchor that is really a default.
const DEFAULT_PROPERTIES = ["font-size", "font-weight", "letter-spacing", "text-transform"];
// The property set is what separates one axis from another; everything else in
// this file -- the declared-not-inherited rule, the uniformity check, the
// stable-selector rule -- is identical for both. T-C06 passes the spacing set
// through `spec.properties` rather than forking the probe, so a fix to the
// inheritance trap applies to both axes at once.
const DEFAULT_VIEWPORT = { width: 1280, height: 800 };
// Own-text floor. Sound for TYPE -- an element with no text of its own cannot
// show a font change -- but this probe is also the SPACING probe (T-C06 passes
// its own property set through `spec.properties`), and `padding-top` /
// `margin-bottom` on a wrapper with no own text is perfectly visible. The same
// inherited-floor mistake cost T-R05 more than half its material until
// 2026-09-23, when relaxing it took usable repos from 40.1% to 85.2%.
// Overridable via `spec.min_text`; the default keeps T-C05 byte for byte.
const DEFAULT_MIN_TEXT = 3;

(async () => {
  const [specPath, outputPath] = process.argv.slice(2);
  const spec = JSON.parse(fs.readFileSync(specPath, "utf8"));
  const routes = spec.routes || [];
  if (!routes.length) throw new Error("spec.json needs a non-empty routes[]");
  const PROPERTIES = (Array.isArray(spec.properties) && spec.properties.length)
    ? spec.properties : DEFAULT_PROPERTIES;
  const MIN_TEXT = Number.isFinite(spec.min_text) ? spec.min_text : DEFAULT_MIN_TEXT;
  // Optional, exactly like `spec.properties`: T-C07/T-C08 measure the same
  // declarations at the mobile viewport, where the winning rule for a selector
  // is often a `@media (max-width: ...)` override rather than the base
  // declaration. Omitting the key keeps T-C05/T-C06 at 1280x800 byte for byte.
  const VIEWPORT = (spec.viewport && spec.viewport.width && spec.viewport.height)
    ? spec.viewport : DEFAULT_VIEWPORT;
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
        const found = await page.evaluate(({ PROPERTIES, MIN_TEXT }) => {
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
          // The rule that WINS the cascade for this property on this element:
          // last matching rule that sets it. Same walk palette_anchor_probe
          // uses for `source`, but the selectorText is kept too -- it is the
          // independence key.
          const declaringRule = (element, property) => {
            let hit = null;
            for (const sheet of Array.from(document.styleSheets)) {
              let rules = null;
              try { rules = sheet.cssRules; } catch (error) { continue; }
              for (const rule of Array.from(rules || [])) {
                if (!rule.selectorText || !rule.style) continue;
                if (!String(rule.style.getPropertyValue(property) || "").trim()) continue;
                let matches = false;
                try { matches = element.matches(rule.selectorText); }
                catch (error) { continue; }
                if (matches) hit = { rule: rule.selectorText, source: sheet.href || "(inline)" };
              }
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
              if (rows.has(key)) { rows.get(key).hits += 1; continue; }
              const declared = declaringRule(element, property);
              if (!declared) continue;   // inherited or UA default -- not an anchor
              // Gradeable only if EVERY visible element the selector matches
              // reports this value: `computed_style` compares all of them.
              const matched = Array.from(document.querySelectorAll(selector));
              const visible = matched.filter(isVisible);
              const uniform = visible.length > 0 && visible.every(
                (other) => String(getComputedStyle(other).getPropertyValue(property) || "").trim() === value);
              const rect = element.getBoundingClientRect();
              rows.set(key, {
                value, selector, property, hits: 1,
                rule: declared.rule, source: declared.source,
                matched: matched.length, visible: visible.length, uniform,
                tag: element.tagName.toLowerCase(),
                text_len: ownText(element),
                area: Math.round(rect.width * rect.height),
              });
            }
          }
          return Array.from(rows.values());
        }, { PROPERTIES, MIN_TEXT });
        out.routes.push({ url, anchors: found });
      } catch (error) {
        out.errors.push({ url, error: String(error) });
      } finally {
        await page.close();
      }
    }
    fs.writeFileSync(outputPath, JSON.stringify(out, null, 2));
    console.log(JSON.stringify({
      routes: out.routes.length,
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
