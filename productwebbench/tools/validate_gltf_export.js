// File-format validation is separate from creature/animation behavior acceptance.
const fs = require("fs");
const crypto = require("crypto");
const validator = require("gltf-validator");

async function validateExport(file, expectedHash, contract = {}) {
  let bytes = fs.readFileSync(file);
  const digest = crypto.createHash("sha256").update(bytes).digest("hex");
  if (digest !== expectedHash) throw new Error("export digest mismatch");
  if (!bytes.length || bytes.length > 64 * 1024 * 1024) throw new Error("invalid export size");
  let compression = null;
  if (contract.require_meshopt !== undefined && typeof contract.require_meshopt !== 'boolean') throw new Error('invalid meshopt contract');
  if (contract.require_meshopt) {
    const {NodeIO} = require('@gltf-transform/core');
    const {ALL_EXTENSIONS} = require('@gltf-transform/extensions');
    const {MeshoptDecoder} = require('meshoptimizer');
    await MeshoptDecoder.ready;
    const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({'meshopt.decoder': MeshoptDecoder});
    const encoded = await io.binaryToJSON(bytes);
    const json = encoded.json;
    const views = (json.bufferViews || []).filter(v => v.extensions?.EXT_meshopt_compression);
    if (!json.extensionsRequired?.includes('EXT_meshopt_compression') || !views.length) throw new Error('Missing actual required meshopt compressed views');
    let decodedBytes = 0;
    for (const view of views) {
      const ext = view.extensions.EXT_meshopt_compression;
      if (!Number.isSafeInteger(ext.count) || ext.count <= 0 || !Number.isSafeInteger(ext.byteStride) || ext.byteStride <= 0
          || !Number.isSafeInteger(ext.byteLength) || ext.byteLength <= 0) throw new Error('Invalid compressed view bounds');
      decodedBytes += ext.count * ext.byteStride;
      if (!Number.isSafeInteger(decodedBytes) || decodedBytes > 64 * 1024 * 1024) throw new Error('Decoded meshopt data exceeds limit');
    }
    const document = await io.readBinary(bytes);
    const extension = document.getRoot().listExtensionsUsed().find(e => e.extensionName === 'EXT_meshopt_compression');
    if (!extension) throw new Error('Meshopt decoding did not produce an extension');
    extension.dispose();
    bytes = Buffer.from(await io.writeBinary(document));
    compression = {extension: 'EXT_meshopt_compression', decoded: true, compressed_views: views.length, decoded_bytes: decodedBytes};
  }
  const report = await validator.validateBytes(new Uint8Array(bytes), {
    maxIssues: 1000,
    externalResourceFunction: () => Promise.reject(new Error("Export must embed its resources; network/filesystem fetch disabled")),
  });
  const result = {validator:validator.version(), sha256:digest, report,
    format_passed: report.issues.numErrors === 0, contract_passed:false,
    scope:"file_format_and_declared_structure_only", compression, accepted:false};
  if (!result.format_passed) return result;
  // Khronos has already validated the container before extracting its JSON chunk.
  const document = bytes.readUInt32LE(0) === 0x46546c67
    ? JSON.parse(bytes.subarray(20, 20 + bytes.readUInt32LE(12)).toString("utf8"))
    : JSON.parse(bytes.toString("utf8"));
  const meshes = document.meshes || [], nodes = document.nodes || [], skins = document.skins || [];
  const clips = document.animations || [];
  const requiredClips = contract.animation_names || [];
  if (contract.require_skin !== undefined && typeof contract.require_skin !== "boolean") throw new Error("invalid skin contract");
  if (!Array.isArray(requiredClips) || requiredClips.some(n => typeof n !== "string" || !n)) {
    throw new Error("invalid animation-name contract");
  }
  const geometry = meshes.some(m => (m.primitives || []).some(p => Number.isInteger(p.attributes?.POSITION)));
  const skinnedMesh = nodes.some(n => Number.isInteger(n.mesh) && Number.isInteger(n.skin) && skins[n.skin]);
  const clipChecks = requiredClips.map(name => ({name, present: clips.some(c => c.name === name && c.channels?.length && c.samplers?.length)}));
  result.structure = {geometry, skinned_mesh:skinnedMesh, clip_checks:clipChecks};
  result.contract_passed = geometry && (!contract.require_skin || Boolean(skinnedMesh)) && clipChecks.every(c => c.present);
  if (contract.require_skin_motion !== undefined && typeof contract.require_skin_motion !== "boolean") {
    throw new Error("invalid skin motion contract");
  }
  if (contract.require_skin_motion) {
    if (!contract.require_skin || !requiredClips.length) throw new Error("Skin motion requires skin and named clips");
    result.scope = "file_format_structure_and_sampled_skin_motion";
    if (result.contract_passed) {
      const {checkSkinMotion} = require("./gltf_skin_motion");
      result.motion = await checkSkinMotion(bytes, requiredClips);
      result.contract_passed = result.motion.passed;
    }
  }
  return result;
}

module.exports = {validateExport};
