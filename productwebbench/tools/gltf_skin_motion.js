// Execute exported clips with Three.js; this does not judge creature appearance or clip semantics.
const path = require("path");
const {pathToFileURL} = require("url");

async function checkSkinMotion(bytes, names) {
  const root = path.dirname(path.dirname(require.resolve("three")));
  const THREE = await import(pathToFileURL(path.join(root, "build/three.module.js")).href);
  const {GLTFLoader} = await import(pathToFileURL(path.join(root, "examples/jsm/loaders/GLTFLoader.js")).href);
  // Node does not expose browser progress events; embedded buffer loading uses this event only for progress.
  globalThis.ProgressEvent ||= class ProgressEvent { constructor(type, data) { this.type = type; Object.assign(this, data); } };
  const manager = new THREE.LoadingManager();
  manager.setURLModifier(url => {
    if (!url.startsWith("data:")) throw new Error("Only embedded runtime resources are supported");
    return url;
  });
  const parsed = await new GLTFLoader(manager).parseAsync(
    bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength), "");
  const meshes = [];
  parsed.scene.traverseVisible(object => { if (object.isSkinnedMesh) meshes.push(object); });
  const count = meshes.reduce((n, mesh) => n + mesh.geometry.attributes.position.count, 0);
  if (!count || count > 100000) throw new Error("Default scene must contain 1..100000 skinned vertices");
  const sample = () => {
    parsed.scene.updateMatrixWorld(true);
    const values = [], worldValues = [];
    for (const mesh of meshes) {
      mesh.skeleton.update();
      for (let i = 0; i < mesh.geometry.attributes.position.count; i++) {
        const vertex = mesh.getVertexPosition(i, new THREE.Vector3());
        values.push(vertex.x, vertex.y, vertex.z);
        vertex.applyMatrix4(mesh.matrixWorld);
        worldValues.push(vertex.x, vertex.y, vertex.z);
      }
    }
    if (values.concat(worldValues).some(v => !Number.isFinite(v))) throw new Error("Non-finite skinned position");
    return {local:values, world:worldValues};
  };
  const checks = [];
  for (const name of names) {
    const clips = parsed.animations.filter(clip => clip.name === name);
    if (clips.length !== 1 || !(clips[0].duration > 0) || clips[0].duration > 300) {
      checks.push({name, passed:false, reason:"missing, ambiguous, or invalid-duration clip"});
      continue;
    }
    const mixer = new THREE.AnimationMixer(parsed.scene);
    const action = mixer.clipAction(clips[0]);
    action.setLoop(THREE.LoopOnce, 1); action.clampWhenFinished = true; action.play();
    mixer.setTime(0);
    const baseline = sample();
    let maxDelta = 0, maxWorldDelta = 0, maxJointDelta = 0;
    // Deterministic finite sampling is evidence of movement, not continuous correctness.
    for (const fraction of [0.125, 0.25, 0.5, 0.75, 0.875, 1]) {
      mixer.setTime(clips[0].duration * fraction);
      const current = sample();
      for (let i = 0; i < baseline.local.length; i += 3) {
        const delta = space => Math.hypot(...[0, 1, 2].map(j => current[space][i + j] - baseline[space][i + j]));
        const local = delta("local"), world = delta("world");
        maxDelta = Math.max(maxDelta, local); maxWorldDelta = Math.max(maxWorldDelta, world);
        maxJointDelta = Math.max(maxJointDelta, Math.min(local, world));
      }
    }
    mixer.stopAllAction(); mixer.uncacheRoot(parsed.scene);
    checks.push({name, duration:clips[0].duration, sampled_max_local_vertex_delta:maxDelta,
      sampled_max_world_vertex_delta:maxWorldDelta, sampled_joint_local_world_delta:maxJointDelta,
      passed:maxJointDelta > 1e-6});
  }
  return {engine:"three", revision:THREE.REVISION, skinned_vertices:count, clips:checks,
    passed:checks.length > 0 && checks.every(check => check.passed),
    scope:"sampled_default_scene_skinned_vertex_motion_not_visual_or_clip_semantic_acceptance"};
}

module.exports = {checkSkinMotion};
