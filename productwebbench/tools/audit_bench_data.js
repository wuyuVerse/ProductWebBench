const {chromium} = require('playwright');
const fs = require('fs');
const path = require('path');

(async () => {
  const [base, output] = process.argv.slice(2);
  fs.mkdirSync(output,{recursive:false});
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  const report = {accepted:false, categories:[], scope:'Actual category consumption and synthetic dataset sanity only'};
  try {
    for (const category of ['demographic','economic','crime']) {
      const page = await browser.newPage({viewport:{width:1280,height:800}});
      const record = {category,action_status:'pending'};
      try {
        await page.route('**/*',route=>new URL(route.request().url()).origin===new URL(base).origin?route.continue():route.abort());
        await page.goto(base+'/',{waitUntil:'networkidle'});
        await page.locator(`a[href='/compare?category=${category}']`).click();
        for(const [side,name] of [['a','Hillcrest'],['b','Lakeview']]) {
          await page.locator(`[data-testid='search-${side}']`).fill(name);
          await page.locator(`[data-testid='suggestions-${side}'] li`).filter({hasText:name}).click();
        }
        record.url = page.url();
        record.columns = await page.locator("[data-testid='compare-col-a'], [data-testid='compare-col-b']").allTextContents();
        const patterns = {demographic:/population/i,economic:/income|unemployment/i,crime:/incident|crime rate/i};
        record.passed = record.columns.length===2 && record.columns.every(text=>patterns[category].test(text));
        if(category==='demographic') {
          const sample = await page.evaluate(()=>typeof NEIGHBORHOOD_DATA==='object'
            ? {schema:'legacy_object',data:NEIGHBORHOOD_DATA}
            : typeof NEIGHBORHOODS!=='undefined' && Array.isArray(NEIGHBORHOODS)
              ? {schema:NEIGHBORHOODS.length>0 && NEIGHBORHOODS.every(row=>
                  row?.demographic?.populationSeries && !Array.isArray(row.demographic.populationSeries))
                  ? 'named_year_keyed_array' : 'named_series_array',
                data:NEIGHBORHOODS,years:typeof YEARS!=='undefined'?YEARS:null}
              : {schema:'missing',data:null});
          report.dataset = sample.data;
          report.dataset_schema = sample.schema;
          report.dataset_years = sample.years ?? null;
        }
        await page.screenshot({path:path.join(output,`${category}.png`),fullPage:true});
        if(['named_year_keyed_array','named_series_array'].includes(report.dataset_schema)) {
          record.neighborhoods=[];
          if(report.dataset.length>100) throw new Error('dataset exceeds audit bound');
          for(const row of report.dataset) {
            await page.goto(base+`/compare?category=${category}`,{waitUntil:'networkidle'});
            const peer=row.name==='Riverside'?'Hillcrest':'Riverside';
            for(const [side,name] of [['a',row.name],['b',peer]]) {
              await page.locator(`[data-testid='search-${side}']`).fill(name);
              await page.locator(`[data-testid='suggestions-${side}'] li`).filter({hasText:new RegExp(`^${name.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')}$`)}).click();
            }
            const column=page.locator("[data-testid='compare-col-a']");
            await column.waitFor({state:'visible'});
            const measured=await column.evaluate(element=>{
              const paragraphs=Array.from(element.querySelectorAll('p[data-testid$="-metric"]'));
              if(paragraphs.length) return {profile:'current_series_paragraph',heading:element.querySelector('h3')?.textContent.trim(),
                metrics:paragraphs.map(e=>{const text=e.textContent.trim(), colon=text.indexOf(':');
                  if(colon<1) throw new Error('missing metric label delimiter');
                  return {label:text.slice(0,colon).trim(),value:text.slice(colon+1).trim()};})};
              const cards=Array.from(element.querySelectorAll('.metric'));
              const currentCards=cards.length && cards.every(e=>e.matches('div[data-testid$="-metric"]'));
              if(currentCards) return {profile:'current_metric_cards',heading:element.querySelector('h3')?.textContent.trim(),
                metrics:cards.map(e=>({label:e.querySelector('.label')?.textContent.trim(),
                  value:e.querySelector('.value')?.textContent.trim()}))};
              if(cards.length) return {profile:'historical_metrics',
                heading:element.querySelector('h2')?.textContent.trim(),
                metrics:cards.map(e=>({label:e.querySelector('.label')?.textContent.trim(),
                  value:e.querySelector('.value')?.textContent.trim()}))};
              const rows=Array.from(element.querySelectorAll('li[data-testid$="-metric"]'));
              return {profile:'current_metric_list',heading:element.querySelector('h3')?.textContent.trim(),
                metrics:rows.map(e=>{
                  const label=e.querySelector('strong');
                  const value=e.cloneNode(true);
                  value.querySelector('strong')?.remove();
                  return {label:label?.textContent.trim().replace(/:$/, ''),value:value.textContent.trim()};
                })};
            });
            record.neighborhoods.push({name:row.name,...measured});
          }
        }
        record.action_status='passed';
      } catch(error) {record.action_status='error';record.error=String(error);}
      finally {report.categories.push(record);await page.close();}
    }
    if(['named_year_keyed_array','named_series_array'].includes(report.dataset_schema)) {
      report.chart_geometry=[];
      for(const row of report.dataset) {
        const page=await browser.newPage({viewport:{width:1280,height:800}});
        const record={name:row.name,action_status:'pending'};
        try {
          await page.route('**/*',route=>new URL(route.request().url()).origin===new URL(base).origin?route.continue():route.abort());
          await page.goto(base+'/compare',{waitUntil:'networkidle'});
          const peer=row.name==='Riverside'?'Hillcrest':'Riverside';
          for(const [side,name] of [['a',row.name],['b',peer]]) {
            await page.locator(`[data-testid='search-${side}']`).fill(name);
            await page.locator(`[data-testid='suggestions-${side}'] li`).filter({hasText:name}).click();
          }
          await page.locator("a[href='/dashboard']").click();
          record.charts=await page.locator('svg').evaluateAll(elements=>elements.map(svg=>({
            viewbox:{x:svg.viewBox.baseVal.x,y:svg.viewBox.baseVal.y,width:svg.viewBox.baseVal.width,height:svg.viewBox.baseVal.height},
            bars:Array.from(svg.querySelectorAll('rect.bar')).map(bar=>({series:bar.getAttribute('data-series'),
              x:bar.x.baseVal.value,y:bar.y.baseVal.value,width:bar.width.baseVal.value,height:bar.height.baseVal.value}))})));
          record.passed=record.charts.length===2 && record.charts.every(chart=>chart.bars.length>0 && chart.bars.every(bar=>
            Object.values(chart.viewbox).every(Number.isFinite) && [bar.x,bar.y,bar.width,bar.height].every(Number.isFinite) &&
            bar.width>0 && bar.height>0 && bar.x>=chart.viewbox.x && bar.y>=chart.viewbox.y &&
            bar.x+bar.width<=chart.viewbox.x+chart.viewbox.width+0.01 && bar.y+bar.height<=chart.viewbox.y+chart.viewbox.height+0.01));
          await page.screenshot({path:path.join(output,`geometry_${report.chart_geometry.length}.png`),fullPage:true});
          record.action_status='passed';
        } catch(error) {record.action_status='error';record.error=String(error);record.passed=false;}
        finally {report.chart_geometry.push(record);await page.close();}
      }
    }
  } finally {await browser.close();}
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));
})().catch(error=>{console.error(error);process.exit(1);});
