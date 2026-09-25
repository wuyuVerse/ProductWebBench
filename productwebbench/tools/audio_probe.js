async function collectAudioProbe(page, probe) {
  const interval = probe.interval_ms ?? 500;
  if (!Number.isInteger(interval) || interval < 100 || interval > 2000) throw new Error('invalid audio sample interval');
  const target = page.locator(probe.selector);
  if(await target.count()!==1) throw new Error('audio target must be unique');
  const sample = () => target.evaluate((element, engine) => {
    const audio = engine==='aplayer' ? element.aplayer?.audio : engine==='native' ? element : null;
    if(!(audio instanceof HTMLAudioElement)) throw new Error('missing native audio element');
    const read = name => Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype,name).get.call(audio);
    return {native:true,paused:read('paused'),time:read('currentTime'),duration:read('duration'),
      src:read('currentSrc'),ready_state:read('readyState'),ended:read('ended'),
      error:read('error')?.code ?? null,observed_at_ms:Date.now(),page_origin:location.origin};
  },probe.engine);
  const first = await sample();
  await page.waitForTimeout(interval);
  return {samples:[first,await sample()]};
}
module.exports = {collectAudioProbe};
