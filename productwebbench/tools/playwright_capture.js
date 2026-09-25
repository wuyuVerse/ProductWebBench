const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

async function withTimeout(promise, timeoutMs, fallback) {
  let timer;
  try {
    return await Promise.race([
      promise,
      new Promise((resolve) => {
        timer = setTimeout(() => resolve(fallback), timeoutMs);
      }),
    ]);
  } finally {
    if (timer) {
      clearTimeout(timer);
    }
  }
}

async function runAction(page, action, artifactDir = null) {
  const record = {
    type: action.type,
    selector: action.selector || null,
    x: Number.isFinite(action.x) ? action.x : null,
    y: Number.isFinite(action.y) ? action.y : null,
    optional: Boolean(action.optional),
    status: "unknown",
  };
  // Resolve a Playwright locator from the many selector spellings the reference
  // engine accepts: an explicit CSS selector (selector / target / target_selector)
  // or a text match (text / click_text), optionally scoped to a tag and matched
  // exactly. Reference state plans mix all of these; a handler that reads only
  // `action.selector` would throw on the text-based clicks (slot_012/slot_221) and
  // on `target`-keyed clicks (slot_316), causing false interaction failures.
  const resolveLocator = () => {
    const css = action.selector || action.target || action.target_selector;
    if (css) {
      return page.locator(css).first();
    }
    const text = action.text || action.click_text;
    if (text) {
      const scope = action.tag ? page.locator(action.tag) : page;
      return scope.getByText(text, { exact: Boolean(action.exact) }).first();
    }
    return null;
  };
  try {
    if (action.type === "download") {
      record.download = await require("./capture_download").captureDownload(page, action, artifactDir);
    } else if (action.type === "wait") {
      await page.waitForTimeout(action.wait_ms || action.ms || 500);
    } else if (action.type === "wait_for_selector") {
      await page.waitForSelector(action.selector, {
        state: action.state || "visible",
        timeout: action.timeout_ms || 5000,
      });
    } else if (action.type === "scroll") {
      await page.evaluate((y) => {
        const candidates = [
          document.scrollingElement,
          document.documentElement,
          document.body,
          ...Array.from(document.querySelectorAll(".scrollable-content, .ps, [data-scroll], main, #root > div")),
        ].filter(Boolean);
        const target =
          candidates.find((element) => element.scrollHeight > element.clientHeight + 10) ||
          document.scrollingElement ||
          document.documentElement;
        target.scrollTo(0, y || 0);
        window.scrollTo(0, y || 0);
      }, action.y || 0);
      await page.waitForTimeout(action.wait_ms || 500);
    } else if (action.type === "scroll_to_selector") {
      // Framing action (not a D4 verdict — the doc treats scroll as "取景"): if the
      // target anchor is absent (e.g. the solver did not build that section), do NOT
      // abort the whole state. Degrade to a best-effort scroll so a full-page
      // screenshot is still captured and the visual/DOM gates judge the miss —
      // instead of the entire state vanishing and cascading required_states failure.
      try {
        await page.locator(action.selector).first().scrollIntoViewIfNeeded({ timeout: action.timeout_ms || 5000 });
        await page.waitForTimeout(action.wait_ms || 500);
      } catch (scrollErr) {
        record.status = "framing_missed";
        record.error = String((scrollErr && scrollErr.message) || scrollErr);
        return record;
      }
    } else if (action.type === "hover") {
      await page.hover(action.selector, { timeout: action.timeout_ms || 5000 });
      await page.waitForTimeout(action.wait_ms || 500);
    } else if (action.type === "click") {
      const target = resolveLocator();
      if (!target) {
        throw new Error("click requires selector/target/target_selector or text/click_text");
      }
      try {
        await target.click({ timeout: action.timeout_ms || 5000, force: Boolean(action.force) });
      } catch (error) {
        const message = String((error && error.message) || error || "");
        const loadingBodyIntercept =
          message.includes("intercepts pointer events") &&
          (message.includes('class="loading"') || message.includes("body.loading"));
        if (!loadingBodyIntercept || action.force === false) {
          throw error;
        }
        await page.waitForSelector("body:not(.loading)", {
          timeout: action.loading_timeout_ms || 8000,
        }).catch(() => {});
        await target.click({
          timeout: action.retry_timeout_ms || action.timeout_ms || 5000,
          force: true,
        });
        record.fallback = "force_after_body_loading_intercept";
      }
      await page.waitForTimeout(action.wait_ms || 500);
    } else if (action.type === "click_at") {
      const x = Number(action.x);
      const y = Number(action.y);
      if (!Number.isFinite(x) || !Number.isFinite(y)) {
        throw new Error("click_at requires finite x and y coordinates");
      }
      if (action.selector) {
        const box = await page.locator(action.selector).first().boundingBox({ timeout: action.timeout_ms || 5000 });
        if (!box) {
          throw new Error(`click_at selector has no visible box: ${action.selector}`);
        }
        await page.mouse.click(box.x + x, box.y + y, { button: action.button || "left" });
      } else {
        await page.mouse.click(x, y, { button: action.button || "left" });
      }
      await page.waitForTimeout(action.wait_ms || 500);
    } else if (action.type === "fill") {
      await page.fill(action.selector, action.value || "", { timeout: action.timeout_ms || 5000 });
      await page.waitForTimeout(action.wait_ms || 300);
    } else if (action.type === "press") {
      await page.press(action.selector || "body", action.key, { timeout: action.timeout_ms || 5000 });
      await page.waitForTimeout(action.wait_ms || 300);
    } else if (action.type === "focus") {
      await page.focus(action.selector, { timeout: action.timeout_ms || 5000 });
      await page.waitForTimeout(action.wait_ms || 300);
    } else if (action.type === "evaluate") {
      await page.evaluate(action.expression);
      await page.waitForTimeout(action.wait_ms || 300);
    } else if (action.type === "select") {
      // Reference: {selector, value}. Match by value first, then label, then index
      // so a solver's <option> whose value differs but label matches still selects.
      const value = action.value;
      const opts = [];
      if (value !== undefined && value !== null) {
        opts.push({ value: String(value) });
        opts.push({ label: String(value) });
      }
      if (Number.isFinite(action.index)) {
        opts.push({ index: Number(action.index) });
      }
      let done = false;
      for (const opt of opts) {
        try {
          await page.selectOption(action.selector, opt, { timeout: action.timeout_ms || 5000 });
          done = true;
          break;
        } catch (selErr) {
          /* try next spelling */
        }
      }
      if (!done) {
        // last resort: let Playwright raise the real error against the primary spelling
        await page.selectOption(action.selector, value !== undefined ? String(value) : "", {
          timeout: action.timeout_ms || 5000,
        });
      }
      await page.waitForTimeout(action.wait_ms || 500);
    } else if (action.type === "type") {
      // Reference: {selector, value?}. When value is absent it is a focus-then-type
      // no-op probe (slot's .cm-content); guard so it never throws.
      const target = page.locator(action.selector).first();
      await target.click({ timeout: action.timeout_ms || 5000 }).catch(() => {});
      if (action.value) {
        await target.type(String(action.value), { timeout: action.timeout_ms || 5000 });
      }
      await page.waitForTimeout(action.wait_ms || 300);
    } else if (action.type === "keypress") {
      // Reference: {key} (no selector) — a global key press.
      await page.keyboard.press(action.key);
      await page.waitForTimeout(action.wait_ms || 300);
    } else if (action.type === "dispatch") {
      // Reference: {selector, event} — dispatch a raw DOM event (e.g. mousedown for
      // a drag start) on the element.
      await page.locator(action.selector).first().dispatchEvent(action.event || "click", {}, {
        timeout: action.timeout_ms || 5000,
      });
      await page.waitForTimeout(action.wait_ms || 300);
    } else if (action.type === "navigate") {
      // Reference: {selector: "/path"} — the "selector" is actually a same-origin
      // path to navigate to (client-side route / new page). Resolve against the
      // current origin and reuse the same readiness wait as the initial goto.
      const dest = new URL(action.url || action.selector, page.url()).href;
      await page.goto(dest, {
        waitUntil: action.wait_until || "networkidle",
        timeout: action.timeout_ms || 45000,
      }).catch(async () => {
        await page.goto(dest, { waitUntil: "domcontentloaded", timeout: action.timeout_ms || 45000 });
      });
      await page.waitForTimeout(action.wait_ms || 500);
    } else if (action.type === "reload") {
      await page.reload({waitUntil: action.wait_until || "domcontentloaded", timeout: action.timeout_ms || 45000});
      await page.waitForTimeout(action.wait_ms || 500);
    } else if (action.type === "scroll_to" || action.type === "scrollTo") {
      // Reference: {selector} — scroll the element into view. Framing action, so a
      // missing anchor degrades to best-effort (same policy as scroll_to_selector)
      // rather than aborting the whole state.
      try {
        await page.locator(action.selector).first().scrollIntoViewIfNeeded({ timeout: action.timeout_ms || 5000 });
        await page.waitForTimeout(action.wait_ms || 500);
      } catch (scrollErr) {
        record.status = "framing_missed";
        record.error = String((scrollErr && scrollErr.message) || scrollErr);
        return record;
      }
    } else if (action.type === "scrollIntoView") {
      // Reference: {selector} — identical intent to scroll_to; same degrade policy.
      try {
        await page.locator(action.selector).first().scrollIntoViewIfNeeded({ timeout: action.timeout_ms || 5000 });
        await page.waitForTimeout(action.wait_ms || 500);
      } catch (scrollErr) {
        record.status = "framing_missed";
        record.error = String((scrollErr && scrollErr.message) || scrollErr);
        return record;
      }
    } else if (action.type === "scroll_frac" || action.type === "scroll_fraction") {
      // Reference: {value: 0..1} — scroll to a fraction of the scrollable height.
      // Reuse the same scroll-target resolution as the `scroll` action so nested
      // scroll containers are handled identically.
      const frac = Number.isFinite(action.value) ? action.value : 0;
      await page.evaluate((f) => {
        const candidates = [
          document.scrollingElement,
          document.documentElement,
          document.body,
          ...Array.from(document.querySelectorAll(".scrollable-content, .ps, [data-scroll], main, #root > div")),
        ].filter(Boolean);
        const target =
          candidates.find((element) => element.scrollHeight > element.clientHeight + 10) ||
          document.scrollingElement ||
          document.documentElement;
        const max = Math.max(0, target.scrollHeight - target.clientHeight);
        const y = Math.round(max * f);
        target.scrollTo(0, y);
        window.scrollTo(0, y);
      }, frac);
      await page.waitForTimeout(action.wait_ms || 500);
    } else {
      throw new Error(`unsupported action type: ${action.type}`);
    }
    record.status = "passed";
  } catch (error) {
    record.status = action.optional ? "skipped" : "failed";
    record.error = String(error.message || error);
    if (!action.optional) {
      throw error;
    }
  }
  return record;
}

const CROP_SPECS = [
  { kind: "focus", selector: "[data-sitecontinuum-crop]", limit: 8, minWidth: 80, minHeight: 24 },
  { kind: "heading", selector: "h1, h2, h3", limit: 4, minWidth: 40, minHeight: 18 },
  { kind: "button", selector: "button, a[role='button'], .btn, [class*='btn'], [class*='button']", limit: 6, minWidth: 28, minHeight: 18 },
  { kind: "card", selector: "article, [class*='card'], [class*='Card'], [class*='tile'], [class*='panel']", limit: 6, minWidth: 80, minHeight: 50 },
  { kind: "nav", selector: "header, nav, aside", limit: 4, minWidth: 120, minHeight: 28 },
  { kind: "media", selector: "img, video, canvas", limit: 6, minWidth: 48, minHeight: 48 },
];

async function captureElementCrops(page, stateDir) {
  const cropDir = path.join(stateDir, "component_crops");
  ensureDir(cropDir);
  const crops = [];
  for (const spec of CROP_SPECS) {
    const locator = page.locator(spec.selector);
    const count = Math.min(await locator.count().catch(() => 0), spec.limit);
    let savedForKind = 0;
    for (let index = 0; index < count; index += 1) {
      const item = locator.nth(index);
      const box = await withTimeout(item.boundingBox(), 1000, null);
      if (!box || box.width < spec.minWidth || box.height < spec.minHeight) {
        continue;
      }
      const filename = `${spec.kind}_${String(savedForKind).padStart(2, "0")}.png`;
      const cropPath = path.join(cropDir, filename);
      const text = await withTimeout(
        item.evaluate((element) => (element.innerText || element.getAttribute("aria-label") || element.getAttribute("alt") || "").trim().slice(0, 120)),
        1000,
        ""
      );
      try {
        await item.screenshot({ path: cropPath, timeout: 3000 });
        crops.push({
          kind: spec.kind,
          path: cropPath,
          text,
          rect: {
            x: Math.round(box.x),
            y: Math.round(box.y),
            width: Math.round(box.width),
            height: Math.round(box.height),
          },
          size_bytes: fs.statSync(cropPath).size,
        });
        savedForKind += 1;
      } catch (error) {
        crops.push({
          kind: spec.kind,
          path: cropPath,
          text,
          rect: {
            x: Math.round(box.x),
            y: Math.round(box.y),
            width: Math.round(box.width),
            height: Math.round(box.height),
          },
          error: String(error.message || error),
        });
      }
    }
  }
  if (crops.filter((crop) => !crop.error).length === 0) {
    const viewport = page.viewportSize() || { width: 1024, height: 768 };
    const width = Math.max(120, Math.min(640, viewport.width));
    const height = Math.max(120, Math.min(420, viewport.height));
    const clip = {
      x: Math.max(0, Math.round((viewport.width - width) / 2)),
      y: Math.max(0, Math.round((viewport.height - height) / 2)),
      width,
      height,
    };
    const cropPath = path.join(cropDir, "viewport_center_00.png");
    try {
      await page.screenshot({ path: cropPath, clip, timeout: 3000 });
      crops.push({
        kind: "viewport_center",
        path: cropPath,
        text: "",
        rect: clip,
        size_bytes: fs.statSync(cropPath).size,
      });
    } catch (error) {
      crops.push({ kind: "viewport_center", path: cropPath, rect: clip, error: String(error.message || error) });
    }
  }
  fs.writeFileSync(path.join(stateDir, "crops.json"), JSON.stringify(crops, null, 2));
  return crops;
}

async function captureState(page, state, outputDir) {
  const stateDir = path.join(outputDir, state.state_id);
  ensureDir(stateDir);
  if (state.block_external_network) {
    const origin = new URL(state.url).origin;
    await page.route(url => ["http:", "https:"].includes(url.protocol) && url.origin !== origin,
      route => route.abort("blockedbyclient"));
  }
  const analyticsObservation = state.analytics_fixture
    ? await require("./analytics_fixture").installAnalyticsFixture(page, state.analytics_fixture) : null;
  const audioFixtureObservation = state.audio_fixture
    ? await require('./meting_fixture').installMetingFixture(page,state.audio_fixture,state.url) : null;
  const webAudioObservation = state.web_audio_fixture
    ? await require('./web_audio_fixture').installWebAudioFixture(page,state.web_audio_fixture) : null;
  const stateConsoleMessages = [];
  const onConsole = (msg) => {
    if (["error", "warning"].includes(msg.type())) {
      let url = "";
      try {
        url = (msg.location && msg.location() && msg.location().url) || "";
      } catch (e) {
        url = "";
      }
      stateConsoleMessages.push({ type: msg.type(), text: msg.text(), url });
    }
  };
  const onPageError = (error) => {
    stateConsoleMessages.push({ type: "pageerror", text: String(error), url: "" });
  };
  // Product-grade runtime-health evidence: record failed/erroring network
  // requests (URL + status) so the verifier's console/network gate can
  // distinguish benign offline-harness 404s (analytics, favicon) from real
  // application errors (5xx, blocked APIs, missing local assets).
  const networkEvents = [];
  const onResponse = (resp) => {
    try {
      const status = resp.status();
      if (status >= 400) {
        networkEvents.push({
          url: resp.url(),
          status,
          method: resp.request().method(),
          type: resp.request().resourceType(),
        });
      }
    } catch (e) {
      /* ignore response inspection errors */
    }
  };
  const onRequestFailed = (req) => {
    try {
      networkEvents.push({
        url: req.url(),
        status: null,
        method: req.method(),
        type: req.resourceType(),
        failure: (req.failure() && req.failure().errorText) || "failed",
      });
    } catch (e) {
      /* ignore request-failure inspection errors */
    }
  };
  page.on("console", onConsole);
  page.on("pageerror", onPageError);
  page.on("response", onResponse);
  page.on("requestfailed", onRequestFailed);

  await page.setViewportSize({ width: state.viewport.width, height: state.viewport.height });
  const requestedWaitUntil = state.goto_wait_until || state.wait_until || "networkidle";
  let effectiveWaitUntil = requestedWaitUntil;
  let navigationFallback = null;
  try {
    await page.goto(state.url, {
      waitUntil: requestedWaitUntil,
      timeout: state.goto_timeout_ms || 45000,
    });
  } catch (error) {
    const message = String((error && error.message) || error || "");
    if (requestedWaitUntil === "networkidle" && message.includes("Timeout")) {
      effectiveWaitUntil = "domcontentloaded";
      navigationFallback = {
        from: requestedWaitUntil,
        to: effectiveWaitUntil,
        reason: "networkidle_timeout",
      };
      await page.goto(state.url, {
        waitUntil: effectiveWaitUntil,
        timeout: state.goto_timeout_ms || 45000,
      });
    } else {
      throw error;
    }
  }
  await page
    .waitForFunction(
      () => {
        const bodyText = document.body ? document.body.innerText.trim() : "";
        const meaningfulImages = Array.from(document.querySelectorAll("img")).filter((img) => {
          const rect = img.getBoundingClientRect();
          return rect.width > 24 && rect.height > 24;
        });
        const links = document.querySelectorAll("a").length;
        const buttons = document.querySelectorAll("button").length;
        return bodyText.length > 80 || meaningfulImages.length >= 2 || links >= 3 || buttons >= 2;
      },
      undefined,
      { timeout: state.ready_timeout_ms || 15000 }
    )
    .catch(() => {});

  const actionHistory = [];
  await page.evaluate(() => {
    const selectors = ["astro-dev-toolbar", ".stats-gl", ".tp-dfwv"];
    for (const selector of selectors) {
      for (const element of Array.from(document.querySelectorAll(selector))) {
        element.setAttribute("data-sitecontinuum-hidden", "true");
        element.style.pointerEvents = "none";
        element.style.display = "none";
      }
    }
  });
  for (const action of state.actions || []) {
    // A failed interaction (e.g. the solver never built the required control, so
    // a click/scroll target is absent) must NOT abort the whole capture and drop
    // every later, unrelated state. Record the failure on THIS action (so the D4
    // gate for this state fails truthfully via action_history + post-interaction
    // dom/visual assertions on the current-state screenshot) and continue, so
    // subsequent independent states are still captured. Only genuinely transient
    // page/browser errors (handled by captureStateWithRetry) should bubble up.
    let rec;
    try {
      rec = await runAction(page, action, stateDir);
    } catch (error) {
      const message = String((error && error.message) || error || "");
      if (isTransientCaptureError(error)) {
        throw error; // let retry logic handle a crashed page/context
      }
      rec = {
        type: action.type,
        selector: action.selector || null,
        optional: Boolean(action.optional),
        status: action.optional ? "skipped" : "failed",
        error: message,
      };
    }
    actionHistory.push(rec);
  }

  const screenshotPath = path.join(stateDir, "screenshot.png");
  console.error(`[capture] screenshot ${state.state_id}`);
  await page.evaluate(() => {
    for (const video of Array.from(document.querySelectorAll("video"))) {
      try {
        video.pause();
      } catch {
        // Ignore media controls that cannot be paused in headless browsers.
      }
    }
  });
  await page.screenshot({
    path: screenshotPath,
    fullPage: state.full_page !== false,
    animations: state.disable_animations === false ? "allow" : "disabled",
    timeout: state.screenshot_timeout_ms || 90000,
  });

  console.error(`[capture] metrics ${state.state_id}`);
  const metrics = await page.evaluate(() => {
    // Renderable-tag whitelist. Empirically re-derived from the union of tags that
    // task dom_assertions actually name: body/html/svg/g/option/summary/details/
    // dialog/output/iframe/small/cite/mark/audio were asserted by 2254 slots but
    // absent here, so those assertions could not match even for a byte-perfect
    // reproduction of the reference answer. Widening is monotonic-safe (see below).
    const VISIBLE_SELECTOR = [
      "html", "body",
      "header", "nav", "main", "section", "article", "aside", "footer",
      "h1", "h2", "h3", "h4", "h5", "h6", "p", "a", "button", "img",
      "input", "textarea", "select", "canvas", "div", "span",
      "li", "ul", "ol", "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption",
      "form", "fieldset", "legend", "label", "output", "progress", "meter", "datalist",
      "strong", "em", "b", "i", "u", "s", "small", "mark", "cite", "q", "abbr", "sub", "sup",
      "figure", "figcaption", "dl", "dt", "dd", "pre", "code", "time", "kbd", "samp", "var",
      "blockquote", "address", "hr", "br", "picture", "source", "video", "audio",
      "iframe", "embed", "object", "map", "area",
      "details", "summary", "dialog", "menu", "search",
      "svg", "g", "path", "circle", "rect", "ellipse", "polygon", "polyline", "line", "text", "tspan", "use", "image",
      "[data-sitecontinuum-crop]",
    ].join(", ");

    // Structurally non-renderable tags: the UA stylesheet gives these display:none
    // (or, for <option>, a 0x0 client rect inside a closed <select>), so they can
    // NEVER enter `boxes` -- not even on the reference answer. They are still real
    // DOM nodes a task may legitimately require ("the page ships a <style> block").
    // They go in a SEPARATE channel, never merged into `boxes`, so that assertions
    // about *visible* elements keep their full discriminative power: a bad variant
    // that hides an element with CSS must still fail its visible-element assertion.
    const STRUCTURAL_SELECTOR = [
      "style", "script", "title", "noscript", "meta", "link", "base", "template",
      "option", "optgroup", "track", "param",
      "defs", "symbol", "desc", "metadata", "clipPath", "linearGradient", "radialGradient",
    ].join(", ");

    const body = document.body;
    const doc = document.documentElement;
    const text = body ? body.innerText.slice(0, 12000) : "";
    const roundRect = (rect) => ({
      x: Math.round(rect.x),
      y: Math.round(rect.y),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
    });
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
    const styleFor = (element) => {
      const style = getComputedStyle(element);
      return {
        color: style.color,
        background_color: style.backgroundColor,
        font_family: style.fontFamily,
        font_size: style.fontSize,
        font_weight: style.fontWeight,
        line_height: style.lineHeight,
        border_radius: style.borderRadius,
        border_color: style.borderColor,
        box_shadow: style.boxShadow,
        padding: `${style.paddingTop} ${style.paddingRight} ${style.paddingBottom} ${style.paddingLeft}`,
        margin: `${style.marginTop} ${style.marginRight} ${style.marginBottom} ${style.marginLeft}`,
      };
    };
    const sampleElements = (selector, limit) =>
      Array.from(document.querySelectorAll(selector))
        .filter(isVisible)
        .slice(0, limit)
        .map((element) => {
          const rect = element.getBoundingClientRect();
          return {
            tag: element.tagName.toLowerCase(),
            text: (element.innerText || element.getAttribute("aria-label") || "").trim().slice(0, 120),
            class_name: (element.getAttribute("class") || "").slice(0, 240),
            rect: roundRect(rect),
            style: styleFor(element),
          };
        });
    const colorElements = sampleElements(
      "body, header, nav, main, section, article, aside, footer, h1, h2, h3, p, a, button, input, [data-sitecontinuum-crop], [class*='card'], [class*='btn'], [class*='button']",
      180
    );
    // Structural inventory. Deliberately NOT filtered by isVisible (these tags are
    // display:none by UA stylesheet) and NOT merged into `boxes`.
    const structuralBoxes = Array.from(document.querySelectorAll(STRUCTURAL_SELECTOR)).map((element) => ({
      tag: element.tagName.toLowerCase(),
      type: (element.getAttribute("type") || "").toLowerCase(),
      text: (element.textContent || element.getAttribute("label") || element.getAttribute("value") || "").trim().slice(0, 100),
      role: element.getAttribute("role") || "",
      class_name: (element.getAttribute("class") || "").slice(0, 180),
      id: element.getAttribute("id") || "",
      rendered: false,
    }));

    const design = {
      body: body ? styleFor(body) : {},
      headings: sampleElements("h1, h2, h3, h4", 40),
      buttons: sampleElements("button, a[role='button'], input[type='button'], input[type='submit'], .btn, [class*='btn'], [class*='button']", 60),
      cards: sampleElements("article, [class*='card'], [class*='Card'], [class*='tile'], [class*='panel']", 60),
      nav_items: sampleElements("nav a, header a, aside a", 60),
      color_samples: colorElements.map((item) => ({
        tag: item.tag,
        text: item.text,
        rect: item.rect,
        color: item.style.color,
        background_color: item.style.background_color,
        border_color: item.style.border_color,
        box_shadow: item.style.box_shadow,
      })),
    };
    const links = Array.from(document.querySelectorAll("a")).slice(0, 80).map((a) => ({
      text: a.innerText.trim().slice(0, 120),
      href: a.href,
    }));
    const images = Array.from(document.querySelectorAll("img")).slice(0, 120).map((img) => {
      const rect = img.getBoundingClientRect();
      return {
        src: img.currentSrc || img.src,
        alt: img.alt || "",
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      };
    });
    const buttons = Array.from(document.querySelectorAll("button")).slice(0, 80).map((button) => {
      const rect = button.getBoundingClientRect();
      return {
        text: button.innerText.trim().slice(0, 120),
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      };
    });
    const boxes = Array.from(
      // Whitelist aligned to the WIDE reference engine: it must cover every tag the
      // reference engine emits into boxes.json (empirical union across all reference
      // states) so any task's dom_assertions can find their host element. The old,
      // narrow list made assertions on div/span/li/h4-6/table/form/dt/... impossible
      // to satisfy even for a byte-perfect reproduction of the reference answer.
      // All dom rules use min_count (never exact_count) and _box_matches ignores
      // coordinates, so widening only adds candidates: strictly monotonic-safe
      // (can turn fail->pass, never pass->fail).
      document.querySelectorAll(VISIBLE_SELECTOR)
    )
      .filter(isVisible)
      // Reference is uncapped (observed up to ~27.6k visible boxes on the largest
      // state). Any finite cap below the reference count would DROP host elements
      // relative to reference and could flip a reference-passing min_count assertion
      // to fail — breaking monotonicity. So match reference exactly: no cap.
      .map((element) => {
        const rect = element.getBoundingClientRect();
        return {
          tag: element.tagName.toLowerCase(),
          // Plans routinely write the *input type* where a tag belongs
          // (tag: "checkbox" for <input type="checkbox">). Emit it so the
          // verifier can resolve that alias instead of never matching.
          type: (element.getAttribute("type") || "").toLowerCase(),
          text: (element.innerText || element.getAttribute("aria-label") || element.getAttribute("alt") || "").trim().slice(0, 100),
          role: element.getAttribute("role") || "",
          class_name: (element.getAttribute("class") || "").slice(0, 180),
          rect: roundRect(rect),
        };
      });
    return {
      title: document.title,
      url: location.href,
      viewport: { width: innerWidth, height: innerHeight },
      scroll: { x: scrollX, y: scrollY },
      document: {
        width: Math.max(body ? body.scrollWidth : 0, doc.scrollWidth),
        height: Math.max(body ? body.scrollHeight : 0, doc.scrollHeight),
      },
      text,
      links,
      images,
      buttons,
      boxes,
      structural_boxes: structuralBoxes,
      design,
    };
  });
  console.error(`[capture] dom ${state.state_id}`);
  const dom = await withTimeout(page.content(), state.dom_timeout_ms || 5000, "<!-- dom snapshot timed out -->");
  console.error(`[capture] a11y ${state.state_id}`);
  const a11y = page.accessibility && page.accessibility.snapshot
    ? await withTimeout(
        page.accessibility.snapshot({ interestingOnly: true }).catch((error) => ({ error: String(error.message || error) })),
        state.a11y_timeout_ms || 5000,
        { error: "accessibility snapshot timed out" }
      )
    : { error: "playwright accessibility snapshot unavailable" };

  const quality = {
    text_length: metrics.text.trim().length,
    link_count: metrics.links.length,
    image_count: metrics.images.length,
    button_count: metrics.buttons.length,
    document_width: metrics.document.width,
    document_height: metrics.document.height,
    horizontal_overflow_px: Math.max(0, metrics.document.width - metrics.viewport.width),
    action_failure_count: actionHistory.filter((action) => action.status === "failed").length,
    is_loading_like:
      metrics.text.trim().length === 0 &&
      metrics.links.length === 0 &&
      metrics.images.length === 0 &&
      metrics.buttons.length === 0,
  };

  if (state.probes) {
    const { collectStateProbes } = require("./state_probes");
    const probes = await collectStateProbes(page, state.probes, stateDir);
    fs.writeFileSync(path.join(stateDir, "probes.json"), JSON.stringify(probes, null, 2));
  }
  fs.writeFileSync(path.join(stateDir, "metrics.json"), JSON.stringify(metrics, null, 2));
  fs.writeFileSync(path.join(stateDir, "quality.json"), JSON.stringify(quality, null, 2));
  fs.writeFileSync(path.join(stateDir, "dom.html"), dom);
  fs.writeFileSync(path.join(stateDir, "a11y.json"), JSON.stringify(a11y, null, 2));
  fs.writeFileSync(path.join(stateDir, "boxes.json"), JSON.stringify(metrics.boxes, null, 2));
  fs.writeFileSync(path.join(stateDir, "structural_boxes.json"), JSON.stringify(metrics.structural_boxes, null, 2));
  fs.writeFileSync(path.join(stateDir, "console.json"), JSON.stringify(stateConsoleMessages.slice(0, 200), null, 2));
  fs.writeFileSync(path.join(stateDir, "network.json"), JSON.stringify(networkEvents.slice(0, 200), null, 2));
  fs.writeFileSync(path.join(stateDir, "actions.json"), JSON.stringify(actionHistory, null, 2));
  if (analyticsObservation) fs.writeFileSync(path.join(stateDir, "analytics.json"), JSON.stringify(analyticsObservation(), null, 2));
  if (audioFixtureObservation) fs.writeFileSync(path.join(stateDir,'audio_fixture.json'),JSON.stringify(audioFixtureObservation(),null,2));
  if (webAudioObservation) fs.writeFileSync(path.join(stateDir,'web_audio.json'),JSON.stringify(webAudioObservation(),null,2));
  console.error(`[capture] crops ${state.state_id}`);
  const crops = state.capture_crops === false ? [] : await captureElementCrops(page, stateDir);
  if (state.capture_crops === false) {
    fs.writeFileSync(path.join(stateDir, "crops.json"), JSON.stringify(crops, null, 2));
  }
  page.off("console", onConsole);
  page.off("pageerror", onPageError);
  page.off("response", onResponse);
  page.off("requestfailed", onRequestFailed);
  return {
    state_id: state.state_id,
    screenshot: screenshotPath,
    metrics: path.join(stateDir, "metrics.json"),
    dom: path.join(stateDir, "dom.html"),
    a11y: path.join(stateDir, "a11y.json"),
    boxes: path.join(stateDir, "boxes.json"),
    console: path.join(stateDir, "console.json"),
    actions: path.join(stateDir, "actions.json"),
    crops: path.join(stateDir, "crops.json"),
    crop_count: crops.filter((crop) => !crop.error).length,
    action_history: actionHistory,
    title: metrics.title,
    url: metrics.url,
    navigation: {
      requested_wait_until: requestedWaitUntil,
      effective_wait_until: effectiveWaitUntil,
      fallback: navigationFallback,
    },
    viewport: metrics.viewport,
    document: metrics.document,
    quality,
  };
}

function isTransientCaptureError(error) {
  const message = String((error && error.message) || error || "");
  return (
    message.includes("Execution context was destroyed") ||
    message.includes("Cannot find context with specified id") ||
    message.includes("Target closed") ||
    message.includes("Navigation")
  );
}

async function captureStateWithRetry(browser, state, outputDir) {
  const maxAttempts = state.capture_attempts || 2;
  let lastError;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    const page = await browser.newPage({
      locale: state.locale || "en-US",
      extraHTTPHeaders: {
        "Accept-Language": state.accept_language || "en-US,en;q=0.9",
      },
    });
    try {
      const result = await captureState(page, state, outputDir);
      await page.close().catch(() => {});
      return result;
    } catch (error) {
      lastError = error;
      await page.close().catch(() => {});
      if (attempt >= maxAttempts || !isTransientCaptureError(error)) {
        throw error;
      }
      await new Promise((resolve) => setTimeout(resolve, 750 * attempt));
    }
  }
  throw lastError;
}

function writeStateSummary(config, states, status, error = null) {
  const summary = {
    repo_id: config.repo_id,
    base_url: config.base_url,
    status,
    states,
    completed_state_count: states.length,
    requested_state_count: config.states.length,
    quality_pass: states.some((state) => !state.quality.is_loading_like),
    console_messages: states.flatMap((state) => state.action_history || []).filter((action) => action.status === "failed").slice(0, 200),
  };
  if (error) {
    summary.error = error;
  }
  fs.writeFileSync(path.join(config.output_dir, "state_capture.json"), JSON.stringify(summary, null, 2));
}

async function main() {
  const configPath = process.argv[2];
  if (!configPath) {
    throw new Error("Usage: node playwright_capture.js CONFIG.json");
  }
  const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
  ensureDir(config.output_dir);

  const browser = await chromium.launch({ headless: true });

  const states = [];
  for (const state of config.states) {
    console.error(`[capture] start ${state.state_id}`);
    try {
      states.push(await captureStateWithRetry(browser, state, config.output_dir));
      writeStateSummary(config, states, "partial");
      console.error(`[capture] done ${state.state_id}`);
    } catch (error) {
      writeStateSummary(config, states, "failed", {
        state_id: state.state_id,
        message: String((error && error.message) || error),
      });
      throw error;
    }
  }

  writeStateSummary(config, states, "passed");
  void browser.close().catch(() => {});
  process.exit(0);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
