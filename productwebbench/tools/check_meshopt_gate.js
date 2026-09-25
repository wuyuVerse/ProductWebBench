const fs = require('node:fs');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const path = require('node:path');
const {validateExport} = require('./validate_gltf_export');
const {NodeIO} = require('@gltf-transform/core');
const {ALL_EXTENSIONS} = require('@gltf-transform/extensions');
const {MeshoptDecoder} = require('meshoptimizer');

async function main() {
  const [input, output] = process.argv.slice(2);
  fs.mkdirSync(output, {recursive: false});
  const hash = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  const contract = {require_skin: true, animation_names: ['idle','walk','attack'], require_skin_motion: true, require_meshopt: true};
  const normal = await validateExport(input, hash(input), contract);
  assert(normal.format_passed && normal.contract_passed && normal.compression.decoded, JSON.stringify(normal));
  await MeshoptDecoder.ready;
  const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({'meshopt.decoder':MeshoptDecoder});
  const document = await io.readBinary(fs.readFileSync(input));
  document.getRoot().listExtensionsUsed().find(e=>e.extensionName==='EXT_meshopt_compression').dispose();
  const plain = path.join(output, 'uncompressed.glb');
  fs.writeFileSync(plain, await io.writeBinary(document));
  const plainResult = await validateExport(plain, hash(plain), {...contract, require_meshopt:false});
  assert(plainResult.format_passed && plainResult.contract_passed);
  await assert.rejects(()=>validateExport(plain, hash(plain), contract), /Missing actual required meshopt/);
  const report = {passed:true, normal, uncompressed_rejected:true, accepted:false};
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify(report));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
