// Qualify a browser dependency bundle without providing generated application code.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const {NodeIO} = require('@gltf-transform/core');
const {EXTMeshoptCompression} = require('@gltf-transform/extensions');
const {MeshoptDecoder} = require('meshoptimizer');
const {chromium} = require('playwright');

async function main() {
  const [deps, input, output] = process.argv.slice(2);
  fs.mkdirSync(output, {recursive: false});
  const bundle = path.join(output, 'meshopt-browser.mjs');
  await require('esbuild').build({stdin: {contents:
    "export {WebIO} from '@gltf-transform/core'; export {EXTMeshoptCompression} from '@gltf-transform/extensions'; export {MeshoptEncoder,MeshoptDecoder} from 'meshoptimizer';",
    resolveDir: deps}, bundle: true, format: 'esm', platform: 'browser', outfile: bundle, metafile: true,
    legalComments: 'eof'}).then(result => fs.writeFileSync(path.join(output, 'bundle-meta.json'), JSON.stringify(result.metafile, null, 2)));
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  const bytes = fs.readFileSync(input);
  let encoded;
  try {
    const page = await browser.newPage();
    await page.route('**/*', route => {
      const url = route.request().url();
      if (url === 'http://meshopt.test/') return route.fulfill({contentType: 'text/html', body: '<!doctype html><title>Dependency check</title>'});
      if (url === 'http://meshopt.test/meshopt-browser.mjs') return route.fulfill({contentType: 'text/javascript', body: fs.readFileSync(bundle)});
      return route.abort();
    });
    await page.goto('http://meshopt.test/');
    encoded = await page.evaluate(async data => {
      const {WebIO, EXTMeshoptCompression, MeshoptEncoder, MeshoptDecoder} = await import('/meshopt-browser.mjs');
      await Promise.all([MeshoptEncoder.ready, MeshoptDecoder.ready]);
      const io = new WebIO().registerExtensions([EXTMeshoptCompression]).registerDependencies({
        'meshopt.encoder': MeshoptEncoder, 'meshopt.decoder': MeshoptDecoder,
      });
      const doc = await io.readBinary(new Uint8Array(data));
      for (const ext of doc.getRoot().listExtensionsUsed()) ext.dispose();
      doc.createExtension(EXTMeshoptCompression).setRequired(true)
        .setEncoderOptions({method: EXTMeshoptCompression.EncoderMethod.QUANTIZE});
      return Array.from(await io.writeBinary(doc));
    }, Array.from(bytes));
  } finally {await browser.close();}
  const exported = new Uint8Array(encoded);
  await MeshoptDecoder.ready;
  const io = new NodeIO().registerExtensions([EXTMeshoptCompression]).registerDependencies({'meshopt.decoder': MeshoptDecoder});
  const container = await io.binaryToJSON(exported);
  assert(container.json.extensionsRequired.includes('EXT_meshopt_compression'));
  const views = container.json.bufferViews.filter(v => v.extensions?.EXT_meshopt_compression);
  assert(views.length > 0);
  const before = await io.readBinary(bytes), after = await io.readBinary(exported);
  const sample = doc => ({accessors: Object.fromEntries(doc.getRoot().listAccessors().map(a => [a.getName(), Array.from(a.getArray())])),
    clips: doc.getRoot().listAnimations().map(a => a.getName()), skins: doc.getRoot().listSkins().length});
  assert.deepEqual(sample(after), sample(before));
  fs.writeFileSync(path.join(output, 'browser-export.glb'), exported);
  const report = {passed: true, browser: 'chromium', exact_roundtrip: true, compressed_views: views.length,
    bundle_sha256: crypto.createHash('sha256').update(fs.readFileSync(bundle)).digest('hex'),
    scope: 'Browser library integration only; no gji application or semantic acceptance', accepted: false};
  fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
}
main().catch(error => {console.error(error); process.exitCode = 1;});
