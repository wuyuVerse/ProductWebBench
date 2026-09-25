const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

async function main() {
  const [base, output] = process.argv.slice(2);
  const browser = await chromium.launch({headless:true,args:['--no-sandbox','--enable-unsafe-swiftshader']});
  const cases=[];
  try {
    for(const viewport of [{width:1440,height:1000},{width:390,height:844}]) {
      const page=await browser.newPage({viewport});
      const errors=[];page.on('pageerror',error=>errors.push(String(error)));
      try {
        await page.goto(base,{waitUntil:'networkidle'});
        await page.waitForFunction(()=>window.__reference?.scene && window.__reference?.mixer);
        const prefix=viewport.width===390?'mobile':'desktop';
        await page.screenshot({path:path.join(output,prefix+'_idle.png')});
        const layout=await page.evaluate(()=>{
          const box=selector=>{const r=document.querySelector(selector).getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};};
          const title=box('h1'),controls=box('#controls'),hud=box('#hud');
          const overlap=Math.min(title.x+title.width,controls.x+controls.width)>Math.max(title.x,controls.x)
            && Math.min(title.y+title.height,controls.y+controls.height)>Math.max(title.y,controls.y);
          return {title,controls,hud,title_controls_overlap:overlap,horizontal_overflow:document.documentElement.scrollWidth>innerWidth};
        });
        await page.locator('#walk').click();await page.waitForTimeout(500);
        await page.screenshot({path:path.join(output,prefix+'_walk.png')});
        await page.locator('#attack').click();
        await page.waitForFunction(()=>Number(document.querySelector('#hits').textContent)===1);
        await page.screenshot({path:path.join(output,prefix+'_hit.png')});
        cases.push({viewport,layout,page_errors:errors,actions_passed:true});
      } catch(error){cases.push({viewport,actions_passed:false,error:String(error),page_errors:errors});}
      finally {await page.close();}
    }
  } finally {await browser.close();}
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify({cases,accepted:false,
    scope:'Independent viewport evidence; screenshots require visual inspection, no semantic admission'},null,2));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
