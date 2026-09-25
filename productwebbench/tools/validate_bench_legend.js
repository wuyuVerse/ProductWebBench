const fs=require('fs');
const path=require('path');
const {chromium}=require('playwright');
const {collectStateProbes}=require('./state_probes');

(async()=>{
  const [base,input,output]=process.argv.slice(2);
  const spec=JSON.parse(fs.readFileSync(input,'utf8'));
  const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  const cases=[];
  try {
    for(const mode of ['original','positive_dom_control','swapped_labels','shared_color']) {
      for(const [state,names] of Object.entries(spec.names)) {
        const page=await browser.newPage({viewport:{width:1280,height:800}});
        const directory=path.join(output,mode,'repo',state);
        fs.mkdirSync(directory,{recursive:true});
        const record={mode,state,action_status:'pending'};
        try {
          await page.route('**/*',route=>new URL(route.request().url()).origin===new URL(base).origin?route.continue():route.abort());
          await page.goto(base+'/compare/',{waitUntil:'networkidle'});
          for(const [side,name] of [['a',names[0]],['b',names[1]]]) {
            await page.locator(`[data-testid='search-${side}']`).fill(name);
            await page.locator(`[data-testid='suggestions-${side}'] li`).filter({hasText:name}).click();
          }
          await page.locator("a[href='/dashboard']").click();
          await page.waitForURL('**/dashboard/**');
          if(mode!=='original') await page.evaluate(({mode,names,colors})=>{
            for(const chart of ['demographic','economic']) {
              const container=document.getElementById('legend-'+chart);
              if(!container) throw new Error('missing original legend container');
              container.replaceChildren();
              names.forEach((name,index)=>{
                const item=document.createElement('span');
                item.className='legend-item'; item.dataset.series=name.toLowerCase();
                item.style.cssText='display:inline-flex;align-items:center;gap:6px;margin-right:12px';
                const swatch=document.createElement('span'); swatch.className='swatch';
                swatch.style.cssText='display:inline-block;width:12px;height:12px';
                swatch.style.backgroundColor=colors[mode==='shared_color'?0:index];
                const label=document.createElement('span'); label.className='legend-label';
                label.textContent=names[mode==='swapped_labels'?1-index:index];
                item.append(swatch,label); container.append(item);
              });
            }
          },{mode,names,colors:spec.colors});
          const records=await collectStateProbes(page,spec.probes[state],directory);
          fs.writeFileSync(path.join(directory,'probes.json'),JSON.stringify(records));
          await page.screenshot({path:path.join(directory,'page.png'),fullPage:true});
          record.action_status='passed';
        } catch(error) {record.action_status='error';record.error=String(error);}
        finally {cases.push(record);await page.close();}
      }
    }
  } finally {await browser.close();}
  fs.writeFileSync(path.join(output,'browser.json'),JSON.stringify(cases));
})().catch(error=>{console.error(error);process.exitCode=1;});
