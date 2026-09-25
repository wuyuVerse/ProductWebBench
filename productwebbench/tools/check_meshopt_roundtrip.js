// Dependency qualification only; this is not a generated application's export gate.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {Document, NodeIO, Accessor} = require('@gltf-transform/core');
const {EXTMeshoptCompression} = require('@gltf-transform/extensions');
const {MeshoptEncoder, MeshoptDecoder} = require('meshoptimizer');

function compressedViews(json) {
  assert(json.extensionsRequired?.includes('EXT_meshopt_compression'));
  const views = (json.bufferViews || []).filter(v => v.extensions?.EXT_meshopt_compression);
  assert(views.length > 0, 'An extension name alone is not compression');
  for (const view of views) {
    const ext = view.extensions.EXT_meshopt_compression;
    assert(Number.isInteger(ext.byteLength) && ext.byteLength > 0);
    assert(Number.isInteger(ext.count) && ext.count > 0);
  }
  return views.length;
}

async function main() {
  const output = process.argv[2];
  fs.mkdirSync(output, {recursive: false});
  await Promise.all([MeshoptEncoder.ready, MeshoptDecoder.ready]);
  const io = new NodeIO().registerExtensions([EXTMeshoptCompression]).registerDependencies({
    'meshopt.encoder': MeshoptEncoder, 'meshopt.decoder': MeshoptDecoder,
  });
  const doc = new Document();
  const buffer = doc.createBuffer();
  const accessor = (name, type, array) => doc.createAccessor(name).setType(type).setArray(array).setBuffer(buffer);
  const positions = accessor('positions', Accessor.Type.VEC3, new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]));
  const joints = accessor('joints', Accessor.Type.VEC4, new Uint16Array(12));
  const weights = accessor('weights', Accessor.Type.VEC4, new Float32Array([1,0,0,0,1,0,0,0,1,0,0,0]));
  const primitive = doc.createPrimitive().setAttribute('POSITION', positions).setAttribute('JOINTS_0', joints).setAttribute('WEIGHTS_0', weights);
  const bone = doc.createNode('joint');
  const skin = doc.createSkin('skin').addJoint(bone).setSkeleton(bone);
  const mesh = doc.createNode('mesh').setMesh(doc.createMesh().addPrimitive(primitive)).setSkin(skin);
  doc.createScene().addChild(bone).addChild(mesh);
  const time = accessor('time', Accessor.Type.SCALAR, new Float32Array([0, 1]));
  const motion = accessor('motion', Accessor.Type.VEC3, new Float32Array([0,0,0,0,0.5,0]));
  for (const name of ['idle', 'walk', 'attack']) {
    const sampler = doc.createAnimationSampler().setInput(time).setOutput(motion);
    doc.createAnimation(name).addSampler(sampler).addChannel(doc.createAnimationChannel()
      .setTargetNode(bone).setTargetPath('translation').setSampler(sampler));
  }
  const before = Object.fromEntries(doc.getRoot().listAccessors().map(a => [a.getName(), Array.from(a.getArray())]));
  doc.createExtension(EXTMeshoptCompression).setRequired(true)
    .setEncoderOptions({method: EXTMeshoptCompression.EncoderMethod.QUANTIZE});
  const bytes = await io.writeBinary(doc);
  const jsonDoc = await io.binaryToJSON(bytes);
  const count = compressedViews(jsonDoc.json);
  const decoded = await io.readBinary(bytes);
  const after = Object.fromEntries(decoded.getRoot().listAccessors().map(a => [a.getName(), Array.from(a.getArray())]));
  assert.deepEqual(after, before, 'Compression must preserve all sampled accessor data');
  assert.deepEqual(decoded.getRoot().listAnimations().map(a => a.getName()), ['idle', 'walk', 'attack']);
  assert.equal(decoded.getRoot().listSkins().length, 1);
  assert.throws(() => compressedViews({extensionsRequired: ['EXT_meshopt_compression'], bufferViews: []}));
  const corrupted = await io.binaryToJSON(bytes);
  const view = corrupted.json.bufferViews.find(v => v.extensions?.EXT_meshopt_compression);
  view.extensions.EXT_meshopt_compression.byteOffset = 2147483647;
  await assert.rejects(() => io.readJSON(corrupted));
  fs.writeFileSync(path.join(output, 'roundtrip.glb'), bytes);
  const report = {passed: true, compressed_buffer_views: count, bytes: bytes.length,
    sha256: crypto.createHash('sha256').update(bytes).digest('hex'),
    accessor_roundtrip_exact: true, fake_extension_rejected: true, corrupted_stream_rejected: true,
    scope: 'Synthetic library roundtrip; not browser integration or animation semantics', accepted: false};
  fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
}
main().catch(error => {console.error(error); process.exitCode = 1;});
