// Fingerprint what a page RENDERS, so two renders of the same site can be diffed.
//
// T-R02 needs one thing from the browser: proof that the injected defect
// changes something a `computed_style` gate can grade. Doing that through the
// brand colour alone left 42% of repos unusable -- not because the defect was
// invisible, but because that particular property was not measured. A broken
// stylesheet changes layout, typography and spacing far more often than it
// changes one brand hex.
//
// So this records a fixed property vector for elements with STABLE selectors,
// and the caller keeps only the (selector, property) pairs whose value differs
// between the healthy and the injected render. Those pairs are exactly the
// gates the repair task can be graded on: the pristine value is the oracle.
//
// Usage: node render_fingerprint_probe.js <spec.json> <output.json>
//   spec.json = {"routes": ["http://127.0.0.1:PORT/", ...]}
const { chromium } = require("playwright");
const fs = require("fs");

// Properties whose resolved form computed_value_contract.py accepts, and which
// a missing stylesheet actually moves. No `color` on its own: it is the one
// this replaces, and it is kept only as part of the vector.
// `width`/`height` added 2026-09-20: the three-asset canary showed images
// moving NOTHING in the vector, which was a measurement gap, not a fact about
// broken images -- a 404 image collapses to its alt box, which changes the
// element's rendered size and nothing else in this list. The capture always
// runs a fixed 1280x800 desktop viewport, so these resolve deterministically.
const PROPERTIES = ["color", "background-color", "font-size", "font-weight",
                    "display", "max-width", "width", "height", "padding-top",
                    "margin-top", "border-top-width", "text-align"];
const MAX_ELEMENTS = 40;

(async () => {
  const [specPath, outputPath] = process.argv.slice(2);
  const spec = JSON.parse(fs.readFileSync(specPath, "utf8"));
  const routes = spec.routes || [];
  if (!routes.length) throw new Error("spec.json needs a non-empty routes[]");
  const browser = await chromium.launch();
  const out = { routes: [], errors: [] };
  try {
    for (const url of routes) {
      const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
      try {
        const response = await page.goto(url, { waitUntil: "networkidle", timeout: 45000 });
        if (!response || !response.ok()) {
          out.errors.push({ url, error: `status ${response && response.status()}` });
          continue;
        }
        const measured = await page.evaluate(({ PROPERTIES, MAX_ELEMENTS }) => {
          const isVisible = (element) => {
            const rect = element.getBoundingClientRect();
            const style = getComputedStyle(element);
            return rect.width > 0 && rect.height > 0 &&
              style.visibility !== "hidden" && style.display !== "none" &&
              Number(style.opacity || "1") > 0.05;
          };
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
            for (let take = Math.min(classes.length, 2); take >= 1; take -= 1) {
              const selector = tag + "." + classes.slice(0, take)
                .map((c) => CSS.escape(c)).join(".");
              try {
                if (document.querySelectorAll(selector).length === 1) return selector;
              } catch (error) { /* keep trying */ }
            }
            return null;
          };
          const rows = [];
          const seen = new Set();
          // Biggest painted boxes first: those are the ones a reader would
          // notice, and a gate on them is a gate on the page's actual shape.
          const elements = Array.from(document.querySelectorAll("body *"))
            .filter(isVisible)
            .sort((a, b) => {
              const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
              return (rb.width * rb.height) - (ra.width * ra.height);
            });
          for (const element of elements) {
            if (rows.length >= MAX_ELEMENTS) break;
            const selector = selectorFor(element);
            if (!selector || seen.has(selector)) continue;
            seen.add(selector);
            const style = getComputedStyle(element);
            const values = {};
            for (const property of PROPERTIES) {
              values[property] = style.getPropertyValue(property);
            }
            rows.push({ selector, values, tag: element.tagName.toLowerCase() });
          }
          // Every stable selector on the page, not just the biggest 40. A
          // broken <script> usually changes nothing in the style vector -- it
          // removes the DOM the script would have built -- so presence is the
          // observable for that defect kind, graded later by `dom_selector`.
          const present = [];
          for (const element of Array.from(document.querySelectorAll("body *"))) {
            if (!isVisible(element)) continue;
            const selector = selectorFor(element);
            if (selector) present.push(selector);
          }
          return { rows, present: Array.from(new Set(present)) };
        }, { PROPERTIES, MAX_ELEMENTS });
        out.routes.push({ url, elements: measured.rows, present: measured.present });
      } catch (error) {
        out.errors.push({ url, error: String(error) });
      } finally {
        await page.close();
      }
    }
    fs.writeFileSync(outputPath, JSON.stringify(out, null, 2));
    console.log(JSON.stringify({
      routes: out.routes.length,
      elements: out.routes.reduce((n, r) => n + r.elements.length, 0),
      present: out.routes.reduce((n, r) => n + r.present.length, 0),
      errors: out.errors.length,
    }));
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
