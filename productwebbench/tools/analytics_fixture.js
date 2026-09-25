// A deterministic SDK sink: observations stay in Node, never in a page-owned array.
async function installAnalyticsFixture(page, fixture) {
  if (!fixture || fixture.engine !== "umami" || fixture.version !== 1 ||
      Object.keys(fixture).some(k => !["engine","version","blocked_script_urls"].includes(k)) ||
      !Array.isArray(fixture.blocked_script_urls)) throw new Error("invalid analytics fixture");
  for (const url of fixture.blocked_script_urls) {
    if (typeof url !== "string" || !/^https?:\/\//.test(url)) throw new Error("invalid blocked SDK URL");
    await page.route(requestUrl => requestUrl.href === url, route => route.fulfill({
      status:200, contentType:"application/javascript", body:"/* SDK replaced by deterministic capture fixture */",
    }));
  }
  const events = [];
  let invalid = false;
  await page.exposeFunction("__pwb_analytics_sink", name => {
    if (typeof name !== "string" || name.length > 512 || events.length >= 1000) {invalid=true; return;}
    events.push({name, observed_at_ms:Date.now()});
  });
  await page.addInitScript(() => {
    Object.defineProperty(window, "umami", {configurable:false, writable:false,
      value:Object.freeze({track:name=>window.__pwb_analytics_sink(name)})});
  });
  return () => ({fixture, status:invalid ? "invalid" : "observed", events:events.slice()});
}

module.exports = {installAnalyticsFixture};
