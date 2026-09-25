async function collectThreeSkinProbe(page,probe) {
  if(probe.handle!=='__reference' || !Number.isInteger(probe.interval_ms) || probe.interval_ms<100 || probe.interval_ms>1000)
    throw new Error('unsupported live skin capture contract');
  const frames=[];
  for(let sample=0;sample<6;sample++) {
    frames.push(await page.evaluate(()=>{
      const root=window.__reference?.alien;
      if(!root?.isObject3D) throw new Error('missing live Three.js root');
      root.updateWorldMatrix(true,true);
      const meshes=[];
      root.traverse(mesh=>{
        if(!mesh.isSkinnedMesh) return;
        const position=mesh.geometry?.attributes?.position;
        const weights=mesh.geometry?.attributes?.skinWeight;
        if(!position || !weights || position.count!==weights.count || position.count>2048 || meshes.length>=16)
          throw new Error('unsupported skinned geometry bounds');
        const vertices=[],point=mesh.position.clone();
        for(let i=0;i<position.count;i++) {
          point.fromBufferAttribute(position,i);
          mesh.applyBoneTransform(i,point);
          vertices.push(point.toArray());
        }
        meshes.push({id:mesh.uuid,bones:mesh.skeleton.bones.length,vertices});
      });
      if(!meshes.length) throw new Error('no live skinned meshes');
      return {observed_at_ms:Date.now(),meshes};
    }));
    if(sample<5) await page.waitForTimeout(probe.interval_ms);
  }
  return {frames};
}
module.exports={collectThreeSkinProbe};
