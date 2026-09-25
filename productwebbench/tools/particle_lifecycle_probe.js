// Interactive probe: one declared click, with live observations on both sides.
async function collectParticleLifecycle(page, probe) {
  if (probe.handle !== '__reference' || typeof probe.selector !== 'string' || !probe.selector ||
      typeof probe.hits_selector !== 'string' || !probe.hits_selector ||
      !Number.isInteger(probe.timeout_ms) || probe.timeout_ms < 1000 || probe.timeout_ms > 30000)
    throw new Error('invalid particle lifecycle capture contract');
  const observe = () => page.evaluate(hitsSelector => {
    const scene = window.__reference?.scene;
    if (!scene?.isScene) throw new Error('missing live Three.js scene');
    const layers = [];
    scene.traverse(object => {
      if (!object.isPoints || !object.material?.uniforms?.uLife) return;
      if (layers.length >= 16) throw new Error('too many particle layers');
      const u = object.material.uniforms;
      layers.push({id:object.uuid,visible:object.visible,count:object.geometry.attributes.position.count,
        life:u.uLife.value,time:u.uTime.value,intensity:u.uIntensity.value,
        shape:u.uShape.value,size:u.uSize.value});
    });
    const hud = document.querySelector(hitsSelector);
    if (!hud || !/^\d+$/.test(hud.textContent.trim())) throw new Error('invalid hit counter');
    return {observed_at_ms:Date.now(),hits:Number(hud.textContent),layers};
  }, probe.hits_selector);
  const initial = await observe();
  await page.locator(probe.selector).click({timeout:probe.timeout_ms});
  await page.waitForFunction(({selector,hits}) =>
    Number(document.querySelector(selector)?.textContent) === hits + 1,
    {selector:probe.hits_selector,hits:initial.hits},{timeout:probe.timeout_ms});
  const hit = await observe();
  // Wait on elapsed shader time, not invisibility: a non-expiring mutant must be observed.
  await page.waitForFunction(() => {
    const layers=[];
    window.__reference.scene.traverse(o=>{if(o.isPoints && o.material?.uniforms?.uLife) layers.push(o);});
    return layers.length>0 && layers.every(o=>o.material.uniforms.uTime.value>=o.material.uniforms.uLife.value);
  }, null, {timeout:probe.timeout_ms});
  return {initial,hit,finished:await observe(),action_status:'passed',
    action:{type:'click',selector:probe.selector,status:'passed'}};
}
module.exports={collectParticleLifecycle};
