async function installWebAudioFixture(page,fixture) {
  if (!fixture || fixture.engine!=='web_audio' || fixture.version!==1 ||
      Object.keys(fixture).sort().join(',')!=='engine,version') throw new Error('invalid Web Audio fixture');
  const events=[];
  let invalid=false;
  await page.exposeFunction('__pwb_audio_start',kind=>{
    if(kind!=='oscillator_start' || events.length>=1000) {invalid=true;return;}
    events.push({kind,observed_at_ms:Date.now()});
  });
  await page.addInitScript(()=>{
    const original=OscillatorNode.prototype.start;
    OscillatorNode.prototype.start=function(...args) {
      const result=Reflect.apply(original,this,args);
      window.__pwb_audio_start('oscillator_start');
      return result;
    };
  });
  return ()=>({fixture,status:invalid?'invalid':'observed',events:events.slice()});
}
module.exports={installWebAudioFixture};
