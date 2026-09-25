const crypto = require('crypto');

function tone() {
  const count = 8000 * 4, buffer = Buffer.alloc(44 + count * 2);
  buffer.write('RIFF'); buffer.writeUInt32LE(buffer.length - 8, 4); buffer.write('WAVEfmt ', 8);
  buffer.writeUInt32LE(16, 16); buffer.writeUInt16LE(1, 20); buffer.writeUInt16LE(1, 22);
  buffer.writeUInt32LE(8000, 24); buffer.writeUInt32LE(16000, 28); buffer.writeUInt16LE(2, 32);
  buffer.writeUInt16LE(16, 34); buffer.write('data', 36); buffer.writeUInt32LE(count * 2, 40);
  for (let i=0;i<count;i++) buffer.writeInt16LE(Math.round(3000*Math.sin(i*2*Math.PI*440/8000)),44+i*2);
  return buffer;
}

async function installMetingFixture(page, fixture, base) {
  if (!fixture || fixture.engine!=='meting' || fixture.version!==1 ||
      Object.keys(fixture).sort().join(',')!=='engine,version') throw new Error('invalid fixed Meting fixture');
  const origin = new URL(base).origin;
  const wav = tone(), calls = [];
  let audioRequests=0, invalid=false;
  await page.route(url=>url.origin===origin && url.pathname==='/fixture.wav', async route=>{
    audioRequests++;
    return route.fulfill({status:200,contentType:'audio/wav',body:wav});
  });
  await page.route(url=>url.origin==='https://api.i-meto.com' && url.pathname==='/meting/api',async route=>{
    const url = new URL(route.request().url());
    if(calls.length>=100 || route.request().method()!=='GET') {invalid=true;return route.abort();}
    const request = Object.fromEntries(['server','type','id'].map(k=>[k,url.searchParams.get(k)]));
    calls.push(request);
    const track = {name:'Local test tone',artist:'Deterministic fixture',url:origin+'/fixture.wav',cover:'',lrc:''};
    const tracks = request.type==='album'?[track,{...track,name:'Second local test tone'}]:[track];
    return route.fulfill({status:200,contentType:'application/json',headers:{'access-control-allow-origin':'*'},body:JSON.stringify(tracks)});
  });
  return ()=>({fixture,status:invalid?'invalid':'observed',calls:calls.slice(),audio_requests:audioRequests,
    fixture_sha256:crypto.createHash('sha256').update(wav).digest('hex'),audio_path:'/fixture.wav'});
}
module.exports = {tone,installMetingFixture};
