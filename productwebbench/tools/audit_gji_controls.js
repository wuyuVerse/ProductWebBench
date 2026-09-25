const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const {chromium} = require('playwright');

(async () => {
  const [url, output] = process.argv.slice(2);
  fs.mkdirSync(output,{recursive:true});
  const browser = await chromium.launch({headless:true,args:['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const results = [];
  try {
    for (const mode of ['default','changed','mobile']) {
      const page = await browser.newPage({viewport:mode==='mobile'?{width:390,height:844}:{width:1280,height:800}});
      const errors = [];
      page.on('pageerror', error => errors.push(String(error)));
      page.on('console', message => {if(message.type()==='error') errors.push(message.text());});
      const record = {mode, action_status:'pending'};
      try {
        await page.route('**/*', route => new URL(route.request().url()).origin === new URL(url).origin ? route.continue() : route.abort());
        const observeAudio = await require('./web_audio_fixture').installWebAudioFixture(page,{engine:'web_audio',version:1});
        await page.goto(url,{waitUntil:'networkidle',timeout:30000});
        await page.waitForFunction(()=>window.__reference?.scene && window.__reference?.mixer);
        if(mode==='default') {
          const {collectStateProbes}=require('./state_probes');
          const particlePage=await browser.newPage();
          try {
            await particlePage.route('**/*',route=>new URL(route.request().url()).origin===new URL(url).origin?route.continue():route.abort());
            await particlePage.goto(url,{waitUntil:'networkidle'});
            await particlePage.waitForFunction(()=>window.__reference?.scene);
            const directory=path.join(output,'particle_reference','repo','attack');
            fs.mkdirSync(directory,{recursive:true});
            const records=await collectStateProbes(particlePage,[{id:'particles',kind:'particle_lifecycle',
              handle:'__reference',selector:'#attack',hits_selector:'#hits',timeout_ms:15000}],directory);
            fs.writeFileSync(path.join(directory,'probes.json'),JSON.stringify(records));
            for (const state of ['controls_changed','controls_unchanged']) {
              await particlePage.goto(url,{waitUntil:'networkidle'});
              await particlePage.waitForFunction(()=>window.__reference?.scene);
              if(state==='controls_changed') {
                await particlePage.locator('#intensity').fill('1.5');
                await particlePage.locator('#duration').fill('2');
              }
              const controlDirectory=path.join(output,'particle_reference','repo',state);
              fs.mkdirSync(controlDirectory,{recursive:true});
              const controlRecords=await collectStateProbes(particlePage,[{id:'particles',kind:'particle_lifecycle',
                handle:'__reference',selector:'#attack',hits_selector:'#hits',timeout_ms:15000}],controlDirectory);
              fs.writeFileSync(path.join(controlDirectory,'probes.json'),JSON.stringify(controlRecords));
            }
          } finally {await particlePage.close();}
          const probe={id:'skin',kind:'three_skin_motion',handle:'__reference',interval_ms:200};
          for(const state of ['moving','frozen']) {
            const directory=path.join(output,'skin_reference','repo',state);
            fs.mkdirSync(directory,{recursive:true});
            if(state==='frozen') await page.evaluate(()=>window.__reference.mixer.timeScale=0);
            try {
              const records=await collectStateProbes(page,[probe],directory);
              fs.writeFileSync(path.join(directory,'probes.json'),JSON.stringify(records));
            } finally {
              if(state==='frozen') await page.evaluate(()=>window.__reference.mixer.timeScale=1);
            }
          }
        }
        async function observe() {
          const snapshot = await page.evaluate(() => {
            const r = window.__reference;
            const layers = [];
            r.scene.traverse(object => {
              if (object.isPoints && object.material?.uniforms?.uLife) {
                const u = object.material.uniforms;
                layers.push({id:object.uuid,visible:object.visible, count:object.geometry.attributes.position.count,
                  life:u.uLife.value, time:u.uTime.value, intensity:u.uIntensity.value,
                  size:u.uSize.value,shape:u.uShape.value, colors:Array.from(object.geometry.attributes.aColor.array)});
              }
            });
            return {observed_at_ms:Date.now(),state:document.querySelector('#state').textContent,hits:Number(document.querySelector('#hits').textContent),
              position:r.alien.position.toArray(), layers};
          });
          const audio = observeAudio();
          if(audio.status!=='observed') throw new Error('invalid Web Audio audit');
          return {...snapshot,audio:{starts:audio.events.length}};
        }
        record.initial = await observe();
        await page.screenshot({path:path.join(output,`${mode}_idle.png`),timeout:10000});
        if (mode === 'changed') {
          for (const [selector,value] of [['#intensity','1.5'],['#duration','2']]) {
            const slider = page.locator(selector);
            const range = await slider.evaluate(e=>({min:Number(e.min),step:Number(e.step)}));
            const steps = (Number(value)-range.min)/range.step;
            if (!Number.isFinite(steps) || Math.abs(steps-Math.round(steps))>1e-6 || steps<0 || steps>200) throw new Error('unsupported slider range');
            await slider.focus();
            await slider.press('Home');
            for(let i=0;i<Math.round(steps);i++) await slider.press('ArrowRight');
            if(Number(await slider.inputValue())!==Number(value)) throw new Error('slider interaction did not reach value');
          }
          await page.locator('[data-palette="cool"]').click();
          await page.locator('#sound').click();
        }
        await page.locator('#attack').click();
        await page.waitForFunction(() => Number(document.querySelector('#hits').textContent)===1,{},{timeout:10000});
        record.hit = await observe();
        const screenshot = await page.screenshot({timeout:10000});
        const file = `${mode}_hit.png`;
        fs.writeFileSync(path.join(output,file),screenshot);
        record.image = {file,sha256:crypto.createHash('sha256').update(screenshot).digest('hex')};
        await page.waitForFunction(() => {
          const layers=[];
          window.__reference.scene.traverse(o=>{if(o.isPoints && o.material?.uniforms?.uLife)layers.push(o);});
          return layers.length>0 && layers.every(o=>o.material.uniforms.uTime.value>o.material.uniforms.uLife.value+0.1)
            && document.querySelector('#state').textContent==='Idle';
        },{},{timeout:15000});
        record.finished = await observe();
        record.followups = [];
        for (const expectedHits of [2,3]) {
          let walked = null;
          if (expectedHits===3) {
            const before = await observe();
            await page.locator('#walk').click();
            await page.waitForTimeout(700);
            const after = await observe();
            walked = {before:before.position,after:after.position,state:after.state,
              moved:Math.hypot(...after.position.map((x,i)=>x-before.position[i]))>0.1};
          }
          await page.locator('#attack').click();
          await page.waitForFunction(n=>Number(document.querySelector('#hits').textContent)===n,expectedHits,{timeout:10000});
          await page.waitForFunction(()=>document.querySelector('#state').textContent==='Idle',{},{timeout:10000});
          record.followups.push({expectedHits,walked,observation:await observe()});
        }
        if(mode==='default') {
          const pending = page.waitForEvent('download',{timeout:15000});
          await page.locator('#export').click();
          const download = await pending;
          const file = path.join(output,'derived_model.glb');
          await download.saveAs(file);
          if(await download.failure()) throw new Error('export download failed');
          const raw = fs.readFileSync(file);
          const sha256 = crypto.createHash('sha256').update(raw).digest('hex');
          const {validateExport} = require('./validate_gltf_export');
          record.export = await validateExport(file,sha256,{require_skin:true,
            animation_names:['idle','walk','attack'],require_skin_motion:true});
        }
        record.action_status = 'passed';
        record.checks = {
          starts_idle:record.initial.state==='Idle' && record.initial.hits===0,
          four_live_layers:record.hit.layers.length===4 && record.hit.layers.every(l=>l.visible && l.time>=0 && l.time<l.life),
          distinct_shapes:new Set(record.hit.layers.map(l=>l.shape)).size===4,
          control_values:record.hit.layers.every(l=>l.intensity===(mode==='changed'?1.5:1) && l.life===(mode==='changed'?2:1.5)),
          palette_changed:mode!=='changed' || record.hit.layers.every((l,i)=>JSON.stringify(l.colors)!==JSON.stringify(record.initial.layers[i].colors)),
          audio_toggle:mode==='changed'?record.hit.audio.starts>0:record.hit.audio.starts===0,
          single_hit_and_idle:record.finished.hits===1 && record.finished.state==='Idle',
          particles_expired:record.finished.layers.every(l=>!l.visible),
          repeat_and_walk_attack:record.followups.length===2 && record.followups.every(r=>r.observation.hits===r.expectedHits && r.observation.state==='Idle' && (!r.walked || (r.walked.state==='Walk' && r.walked.moved))),
          fresh_export:mode!=='default' || (record.export.format_passed && record.export.contract_passed),
          no_runtime_errors:errors.length===0
        };
      } catch(error) {record.action_status='error';record.error=String(error);}
      finally {record.errors=errors;results.push(record);await page.close();}
    }
  } finally {await browser.close();}
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify({cases:results,accepted:false,
    checks_passed:results.every(r=>r.action_status==='passed' && Object.values(r.checks).every(Boolean)),
    scope:'Real interaction and render-parameter prerequisites; not pixel visibility, audio audibility, source completeness or full-chain acceptance'},null,2));
})().catch(error=>{console.error(error);process.exit(1);});
