const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const {chromium} = require("playwright");
const {collectStateProbes} = require("./state_probes");
const {tone,installMetingFixture} = require('./meting_fixture');

async function main() {
  const [base, output] = process.argv.slice(2);
  fs.mkdirSync(output, {recursive:false});
  const wav = tone(), digest = crypto.createHash("sha256").update(wav).digest("hex");
  const browser = await chromium.launch({headless:true, args:["--no-sandbox"]});
  const results = [];
  try {
    for (const [name, query, broken] of [["default", "autoplay=false", false],
      ["override", "server=tencent&type=song&id=123&autoplay=false", false],
      ["ignored_query", "server=tencent&type=song&id=123&autoplay=false", true]]) {
      const context = await browser.newContext({viewport:{width:800,height:600}});
      const page = await context.newPage();
      let calls = [];
      const errors = [];
      let mutationApplied = false;
      page.on("pageerror", error => errors.push(error.message));
      await page.route("**/*", async route => {
        const url = new URL(route.request().url());
        if (url.origin === new URL(base).origin) {
          if (broken && url.pathname === "/met/") {
            const response = await route.fetch();
            const html = await response.text();
            const target = "t.get(n)||defaultConfig[n]";
            if (html.split(target).length !== 2) {
              errors.push("Ambiguous query-override mutation in recovered minified artifact");
              return route.abort("failed");
            }
            mutationApplied = true;
            return route.fulfill({response, body:html.replace(target, "defaultConfig[n]")});
          }
          return route.continue();
        }
        return route.abort("blockedbyclient");
      });
      const observeFixture = await installMetingFixture(page,{engine:'meting',version:1},base);
      let result;
      try {
        await page.goto(base + "/met/?" + query, {waitUntil:"networkidle", timeout:30000});
        await page.waitForFunction(() => document.querySelector("meting-js")?.aplayer?.audio?.readyState >= 2, null, {timeout:15000});
        const snapshot = () => page.evaluate(() => {
          const audio = document.querySelector("meting-js").aplayer.audio;
          return {native:audio instanceof HTMLAudioElement, paused:audio.paused, time:audio.currentTime,
            duration:audio.duration, src:audio.currentSrc, error:audio.error?.code || null};
        });
        const before = await snapshot();
        if (!before.paused) await page.locator(".aplayer-pause").click();
        const captureAudio = async state => {
          const directory = path.join(output, name, state);
          fs.mkdirSync(directory, {recursive:true});
          const records = await collectStateProbes(page, [{id:"playback",kind:"audio_playback_probe",
            selector:"meting-js",engine:"aplayer",interval_ms:500}], directory);
          fs.writeFileSync(path.join(directory,"probes.json"), JSON.stringify(records,null,2));
          return records.length===1 && records[0].status==="observed";
        };
        const pausedObserved = await captureAudio("paused");
        await page.locator(".aplayer-play").click();
        await page.waitForTimeout(150);
        const first = await snapshot();
        await page.waitForTimeout(600);
        const second = await snapshot();
        const playingObserved = await captureAudio("playing");
        const loadingVisible = await page.locator("#loading").isVisible();
        calls = observeFixture().calls;
        fs.writeFileSync(path.join(output,name+'_fixture.json'),JSON.stringify(observeFixture(),null,2));
        await page.screenshot({path:path.join(output, name + ".png")});
        const expected = name === "default" ? {server:"netease", type:"album", id:"137470856"} : {server:"tencent", type:"song", id:"123"};
        const matches = calls.length === 1 && Object.keys(expected).every(k => calls[0][k] === expected[k]);
        const nativePlayback = first.native && second.native && !first.paused && !second.paused && !second.error
          && second.src === base + "/fixture.wav" && second.time - first.time > 0.3 && Math.abs(second.duration - 4) < 0.1;
        const wrongDefault = calls.length === 1 && calls[0].server === "netease" && calls[0].type === "album" && calls[0].id === "137470856";
        result = {name, calls, expected, first, second, errors, loading_visible:loadingVisible,
          audio_probes_observed:pausedObserved && playingObserved,
          ui_quality_passed:!loadingVisible, native_playback:nativePlayback, query_matches:matches,
          mutation_applied:mutationApplied, verified:nativePlayback && !errors.length && (broken ? mutationApplied && !matches && wrongDefault : matches)};
      } catch (error) {
        result = {name, calls, errors, verified:false, failure:error.message};
      }
      results.push(result);
      fs.writeFileSync(path.join(output, name + ".json"), JSON.stringify(result, null, 2));
      await context.close();
    }
  } finally { await browser.close(); }
  const report = {fixture_sha256:digest, results, all_cases_verified:results.every(r => r.verified),
    normal_case_ui_passed:results.filter(r => r.name !== "ignored_query").every(r => r.ui_quality_passed === true), accepted:false,
    scope:"Original player with local API/audio fixtures; no external music service or full-chain acceptance"};
  fs.writeFileSync(path.join(output,"report.json"), JSON.stringify(report,null,2));
  console.log(JSON.stringify(report));
  if (!report.all_cases_verified) process.exitCode = 1;
}
main().catch(error => {console.error(error); process.exitCode = 1;});
