// Report, per CSS selector, how many elements exist in the DOM and how many the
// capture would actually count.
//
// The visibility predicate below is copied verbatim from playwright_capture.js
// `isVisible`. That is the point of this probe: a gate is graded against those
// exact rules, so a gate whose selector matches elements that never satisfy
// them can never pass, however correct the model's work is.
//
// Usage: node anchor_visibility_probe.js <url> <selectors.json> <output.json>
const { chromium } = require("playwright");
const fs = require("fs");

(async () => {
  const [url, selectorsPath, outputPath] = process.argv.slice(2);
  const selectors = JSON.parse(fs.readFileSync(selectorsPath, "utf8"));
  if (!Array.isArray(selectors) || selectors.length === 0) {
    throw new Error("selectors.json must be a non-empty array");
  }
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    const response = await page.goto(url, { waitUntil: "networkidle", timeout: 60000 });
    if (!response || !response.ok()) {
      throw new Error(`reference page did not load: ${url} -> ${response && response.status()}`);
    }
    const measured = await page.evaluate((list) => {
      // Identical to playwright_capture.js isVisible.
      const isVisible = (element) => {
        const rect = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return (
          rect.width > 0 &&
          rect.height > 0 &&
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          Number(style.opacity || "1") > 0.05
        );
      };
      return list.map((selector) => {
        try {
          const all = Array.from(document.querySelectorAll(selector));
          const visible = all.filter(isVisible);
          return {
            selector,
            total: all.length,
            visible: visible.length,
            // Why the invisible ones were dropped, so a report is actionable.
            first_hidden_reason: all.length && !visible.length
              ? (() => {
                  const rect = all[0].getBoundingClientRect();
                  const style = getComputedStyle(all[0]);
                  if (rect.width <= 0 || rect.height <= 0) {
                    return `zero_box (${Math.round(rect.width)}x${Math.round(rect.height)}), `
                      + `tag=${all[0].tagName.toLowerCase()}, child_position=`
                      + (all[0].firstElementChild
                          ? getComputedStyle(all[0].firstElementChild).position : "none");
                  }
                  if (style.display === "none") return "display_none";
                  if (style.visibility === "hidden") return "visibility_hidden";
                  return `opacity=${style.opacity}`;
                })()
              : null,
          };
        } catch (error) {
          return { selector, total: null, visible: null, error: String(error) };
        }
      });
    }, selectors);
    fs.writeFileSync(outputPath, JSON.stringify({ url, results: measured }, null, 2));
    console.log(JSON.stringify({ url, checked: measured.length }));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
