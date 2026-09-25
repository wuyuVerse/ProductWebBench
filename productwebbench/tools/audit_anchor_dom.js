const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const {chromium} = require('playwright');

(async () => {
  const manifest = process.argv[2];
  const cases = JSON.parse(fs.readFileSync(manifest, 'utf8'));
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  const results = [];
  try {
    for (const item of cases) {
      const page = await browser.newPage({javaScriptEnabled:false});
      try {
        await page.route('**/*', route => route.abort());
        const raw = fs.readFileSync(item.copy);
        await page.setContent(raw.toString('utf8'), {waitUntil:'domcontentloaded'});
        results.push({name:item.name, html_sha256:crypto.createHash('sha256').update(raw).digest('hex'),
          nested_dom_count:await page.locator('a a').count(),
          anchor_count:await page.locator('a').count()});
      } finally { await page.close(); }
    }
  } finally { await browser.close(); }
  fs.writeFileSync(path.join(path.dirname(manifest),'browser.json'),JSON.stringify(results,null,2));
})().catch(error => {console.error(error);process.exit(1);});
