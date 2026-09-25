// Browser observations; particle_lifecycle includes one declared interaction.
async function collectStateProbes(page, probes, outputDir = null) {
  if (!Array.isArray(probes) || probes.length > 128) throw new Error("invalid state probes");
  const ids = new Set();
  const records = [];
  for (const probe of probes) {
    if (typeof probe.id !== "string" || !/^[A-Za-z0-9_-]+$/.test(probe.id) || ids.has(probe.id)) throw new Error("invalid or duplicate probe id");
    ids.add(probe.id);
    try {
      if(probe.kind==='dom_transition') {
        records.push({probe,status:'observed',observation:await require('./dom_transition_probe').collectDomTransition(page,probe)});
        continue;
      }
      if(probe.kind==='particle_lifecycle') {
        records.push({probe,status:'observed',observation:await require('./particle_lifecycle_probe').collectParticleLifecycle(page,probe)});
        continue;
      }
      if(probe.kind==='three_skin_motion') {
        records.push({probe,status:'observed',observation:await require('./three_skin_probe').collectThreeSkinProbe(page,probe)});
        continue;
      }
      if (probe.kind === 'audio_playback_probe') {
        const {collectAudioProbe} = require('./audio_probe');
        records.push({probe,status:'observed',observation:await collectAudioProbe(page,probe)});
        continue;
      }
      if (probe.kind === "rendered_html_markup_audit") {
        if (!outputDir || typeof probe.file !== 'string') throw new Error('raw HTML probe requires output and file');
        const url = new URL(page.url());
        const documentPath = decodeURIComponent(url.pathname).replace(/^\//,'');
        const expected = documentPath.endsWith('/') || !documentPath ? documentPath+'index.html' : documentPath;
        if (!['http:','https:'].includes(url.protocol) || expected !== probe.file) throw new Error('raw HTML document path mismatch');
        const response = await page.request.get(url.href,{maxRedirects:0,timeout:10000});
        try {
          if(response.status()!==200 || !response.headers()['content-type']?.includes('text/html')) throw new Error('raw HTML response is not successful HTML');
          const data = await response.body();
          if(!data.length || data.length>16*1024*1024) throw new Error('raw HTML response exceeds limit');
          const file = `probe_${probe.id}_response.html`;
          require('fs').writeFileSync(require('path').join(outputDir,file),data);
          records.push({probe,status:'observed',observation:{url:url.href,http_status:200,
            file,sha256:require('crypto').createHash('sha256').update(data).digest('hex'),bytes:data.length}});
        } finally {await response.dispose();}
        continue;
      }
      if (probe.kind === "svg_text_size") {
        if (!outputDir) throw new Error("text probe requires artifact directory");
        const labels = await page.locator(probe.selector).evaluateAll(elements => {
          if (!elements.length || elements.length > 1000) throw new Error("invalid text count");
          return elements.map(e => {
            if (!(e instanceof SVGTextElement) || e.children.length || !e.textContent.trim()) throw new Error("requires nonempty leaf SVG text");
            for (let parent = e; parent; parent = parent.parentElement) {
              const style = getComputedStyle(parent);
              if (style.display === "none" || style.visibility !== "visible" || Number(style.opacity) < 0.99) throw new Error("hidden text");
            }
            const style = getComputedStyle(e);
            if (style.fill === "none" || Number(style.fillOpacity) < 0.99) throw new Error("unpainted text");
            const color = document.createElement("canvas").getContext("2d");
            color.fillStyle = style.fill; color.fillRect(0,0,1,1);
            if (color.getImageData(0,0,1,1).data[3] < 253) throw new Error("transparent text");
            const matrix = e.getScreenCTM();
            if (!matrix) throw new Error("missing text transform");
            // Smallest singular value avoids inflating size with rotation or skew.
            const {a,b,c,d} = matrix;
            const trace = a*a+b*b+c*c+d*d;
            const determinant = a*d-b*c;
            const scale = Math.sqrt(Math.max(0, (trace-Math.sqrt(Math.max(0,trace*trace-4*determinant*determinant)))/2));
            const box = e.getBBox();
            const rect = e.getBoundingClientRect();
            return {text:e.textContent, glyph_height_px:box.height*scale, css_font_size:style.fontSize,
              x:rect.x+scrollX,y:rect.y+scrollY,width:rect.width,height:rect.height};
          });
        });
        const data = await page.screenshot({timeout:10000, fullPage:probe.require_visible_paint===true, scale:'css',animations:'disabled'});
        const file = `probe_${probe.id}_text.png`;
        require("fs").writeFileSync(require("path").join(outputDir,file),data);
        const observation = {labels,image:{file,
          sha256:require("crypto").createHash("sha256").update(data).digest("hex")}};
        if (probe.require_visible_paint === true) {
          const target = page.locator(probe.selector);
          const styles = await target.evaluateAll(elements=>elements.map(e=>e.getAttribute('style')));
          try {
            await target.evaluateAll(elements=>elements.forEach(e=>e.style.setProperty('visibility','hidden','important')));
            const hidden = await page.screenshot({timeout:10000,fullPage:true,scale:'css',animations:'disabled'});
            const hiddenFile = `probe_${probe.id}_without_text.png`;
            require('fs').writeFileSync(require('path').join(outputDir,hiddenFile),hidden);
            observation.without_text = {file:hiddenFile,sha256:require('crypto').createHash('sha256').update(hidden).digest('hex')};
          } finally {
            await target.evaluateAll((elements,styles)=>elements.forEach((e,i)=>styles[i]===null?e.removeAttribute('style'):e.setAttribute('style',styles[i])),styles);
          }
        }
        records.push({probe,status:"observed",observation});
        continue;
      }
      if (probe.kind === "svg_bar_values") {
        if (!outputDir) throw new Error("chart probe requires artifact directory");
        const target = page.locator(probe.selector);
        if (await target.count() !== 1) throw new Error("chart must be unique");
        await target.scrollIntoViewIfNeeded();
        const observation = await target.evaluate((svg, p) => {
          if (svg.tagName.toLowerCase() !== "svg") throw new Error("chart must be SVG");
          const root = svg.getBoundingClientRect();
          if (root.width < 32 || root.height < 32) throw new Error("chart is too small");
          const marks = Array.from(svg.querySelectorAll(p.mark_selector));
          if (!marks.length || marks.length > 1000) throw new Error("invalid bar count");
          const color = document.createElement("canvas").getContext("2d");
          const bars = marks.map(mark => {
            if (mark.tagName.toLowerCase() !== "rect") throw new Error("only rectangular bars supported");
            for (let el = mark; el; el = el.parentElement) {
              const s = getComputedStyle(el);
              if (s.display === "none" || s.visibility !== "visible" || Number(s.opacity) < 0.99) throw new Error("hidden chart mark");
            }
            const style = getComputedStyle(mark);
            if (style.fill === "none" || Number(style.fillOpacity) < 0.99) throw new Error("unpainted chart mark");
            color.clearRect(0,0,1,1); color.fillStyle = style.fill; color.fillRect(0,0,1,1);
            const fill = Array.from(color.getImageData(0,0,1,1).data);
            const r = mark.getBoundingClientRect();
            return {x:r.x-root.x, y:r.y-root.y, width:r.width, height:r.height, fill};
          });
          return {width:root.width, height:root.height, bars:bars.sort((a,b)=>a.x-b.x)};
        }, probe);
        const data = await target.screenshot({timeout:10000, animations:"disabled"});
        const file = `probe_${probe.id}_chart.png`;
        require("fs").writeFileSync(require("path").join(outputDir,file),data);
        observation.image = {file, sha256:require("crypto").createHash("sha256").update(data).digest("hex")};
        records.push({probe,status:"observed",observation});
        continue;
      }
      if (probe.kind === "canvas_pixels" || probe.kind === "webgl_canvas") {
        const fs = require("fs");
        const path = require("path");
        const crypto = require("crypto");
        if (!outputDir) throw new Error("canvas probe requires artifact directory");
        const target = page.locator(probe.selector);
        if (await target.count() !== 1 || await target.evaluate(e => e.tagName.toLowerCase()) !== "canvas") {
          throw new Error("canvas probe requires one canvas");
        }
        let context = null;
        if (probe.kind === "webgl_canvas") {
          context = await target.evaluate(canvas => {
            const gl = canvas.getContext("webgl2") || canvas.getContext("webgl");
            if (!gl || gl.isContextLost()) throw new Error("missing live WebGL context");
            return {version:gl.getParameter(gl.VERSION), width:gl.drawingBufferWidth, height:gl.drawingBufferHeight};
          });
        }
        const interval = probe.interval_ms || 500;
        if (!Number.isInteger(interval) || interval < 100 || interval > 2000) throw new Error("invalid frame interval");
        const frames = [];
        for (let index = 0; index < 2; index++) {
          if (index) await page.waitForTimeout(interval);
          const data = await target.screenshot({timeout: 10000});
          const file = `probe_${probe.id}_frame${index}.png`;
          fs.writeFileSync(path.join(outputDir, file), data);
          frames.push({file, sha256: crypto.createHash("sha256").update(data).digest("hex"), observed_at_ms: Date.now()});
        }
        records.push({probe, status: "observed", observation: context ? {frames, context} : {frames}});
        continue;
      }
      const observation = await page.evaluate((p) => {
        const elements = Array.from(document.querySelectorAll(p.selector));
        if (elements.length > 1000) throw new Error("probe result exceeds 1000 elements");
        const visible = (e) => {
          const rect = e.getBoundingClientRect();
          const style = getComputedStyle(e);
          return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
        };
        if (p.kind === "dom_selector") {
          if(p.include_hidden!==undefined && typeof p.include_hidden!=='boolean') throw new Error('include_hidden must be boolean');
          return { elements: elements.filter(e=>p.include_hidden || visible(e)).map((e) => ({ text: e.textContent || "", tag: e.tagName.toLowerCase() })) };
        }
        if (p.kind === "computed_style") {
          if (typeof p.property !== "string" || !p.property) throw new Error("missing CSS property");
          return { values: elements.filter(visible).map((e) => getComputedStyle(e).getPropertyValue(p.property).trim()) };
        }
        if (p.kind === "dom_nonoverlap") {
          const after = Array.from(document.querySelectorAll(p.after_selector));
          if(elements.length!==1 || after.length!==1 || !visible(elements[0]) || !visible(after[0])) throw new Error('requires two unique visible elements');
          const rect=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};};
          return {first:rect(elements[0]),second:rect(after[0]),viewport:{width:innerWidth,height:innerHeight}};
        }
        if (p.kind === "dom_order") {
          const after = document.querySelectorAll(p.after_selector);
          if (elements.length !== 1 || after.length !== 1 || !visible(elements[0]) || !visible(after[0])) {
            throw new Error("order probe requires two unique visible elements");
          }
          return { before: Boolean(elements[0].compareDocumentPosition(after[0]) & Node.DOCUMENT_POSITION_FOLLOWING) };
        }
        throw new Error("unsupported probe kind");
      }, probe);
      records.push({ probe, status: "observed", observation });
    } catch (error) {
      records.push({ probe, status: "error", error: String(error.message || error) });
    }
  }
  return records;
}

module.exports = { collectStateProbes };
