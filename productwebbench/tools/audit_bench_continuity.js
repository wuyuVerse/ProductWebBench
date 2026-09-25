// Independent post-rollout quality audit, not the original solver's acceptance gate.
const {chromium} = require("playwright");
const fs = require("fs");
const path = require("path");

async function main() {
  const [base, output] = process.argv.slice(2);
  fs.mkdirSync(output, {recursive:false});
  const browser = await chromium.launch({headless:true, args:["--no-sandbox"]});
  const report = {accepted:false, checks:[], scope:"Independent selection continuity and mobile label audit; not full source acceptance"};
  try {
    const context = await browser.newContext({viewport:{width:1280,height:800}});
    const page = await context.newPage();
    await page.route("**/*", route => new URL(route.request().url()).origin === new URL(base).origin
      ? route.continue() : route.abort("blockedbyclient"));
    try {
      await page.goto(base + "/compare/", {waitUntil:"networkidle"});
      const selected = ["Hillcrest", "Lakeview"];
      for (const [side, name] of [["a",selected[0]],["b",selected[1]]]) {
        await page.locator(`[data-testid='search-${side}']`).fill(name);
        await page.locator(`[data-testid='suggestions-${side}'] li`).filter({hasText:name}).click();
      }
      const comparison = await page.locator("[data-testid='compare-panel']").innerText();
      await page.screenshot({path:path.join(output,"selected.png"),fullPage:true});
      await page.locator("a[href^='/dashboard']").click();
      await page.waitForURL("**/dashboard/**");
      const legends = await page.locator("[data-testid='chart-legend']").allTextContents();
      await page.screenshot({path:path.join(output,"dashboard.png"),fullPage:true});
      report.checks.push({name:"selection_survives_navigation", selected, comparison, legends,
        passed:selected.every(name => comparison.includes(name) && legends.some(text => text.includes(name))),
        action_status:"passed"});
      await page.locator("[data-testid='range-1y']").click();
      const years = await page.locator("[data-testid='chart-demographic'] [data-testid='axis-tick']").allTextContents();
      await page.screenshot({path:path.join(output,"one_year.png"),fullPage:true});
      report.checks.push({name:"one_year_label_matches_data", years, expected:["2023"],
        passed:years.length === 1 && years[0].trim() === "2023", action_status:"passed"});
    } catch (error) {
      report.checks.push({name:"selection_survives_navigation", passed:false, action_status:"error", error:error.message});
    }
    await context.close();
    const mobile = await browser.newContext({viewport:{width:390,height:844}});
    const phone = await mobile.newPage();
    await phone.route("**/*", route => new URL(route.request().url()).origin === new URL(base).origin
      ? route.continue() : route.abort("blockedbyclient"));
    try {
      await phone.goto(base + "/dashboard/", {waitUntil:"networkidle"});
      const labels = await phone.locator("svg text").evaluateAll(elements => elements.map(element => {
        const rect = element.getBoundingClientRect();
        return {text:element.textContent, x:rect.x+scrollX, y:rect.y+scrollY,
          width:rect.width, height:rect.height, css_font_size:getComputedStyle(element).fontSize};
      }));
      await phone.screenshot({path:path.join(output,"mobile.png"),fullPage:true});
      await phone.locator('svg text').evaluateAll(elements=>elements.forEach(e=>e.style.visibility='hidden'));
      await phone.screenshot({path:path.join(output,"mobile_without_labels.png"),fullPage:true});
      report.checks.push({name:"mobile_label_size", labels, min_rendered_height_px:10,
        passed:labels.length > 0 && labels.every(label => label.height >= 10), action_status:"passed",
        policy:"Derived minimum readability audit threshold, not a claim of complete visual quality"});
    } catch (error) {
      report.checks.push({name:"mobile_label_size", passed:false, action_status:"error", error:error.message});
    }
    await mobile.close();
  } finally { await browser.close(); }
  report.quality_passed = report.checks.length === 3 && report.checks.every(check => check.passed);
  fs.writeFileSync(path.join(output,"report.json"), JSON.stringify(report,null,2));
  console.log(JSON.stringify(report));
}
main().catch(error => {console.error(error);process.exitCode=1;});
