/* Real console JS + isolated fixture. Switch lifecycle is simulated; no model or WeChat sends. */
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
const {chromium}=require('C:/Users/bgya7/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const origin='http://127.0.0.1:18118',output='D:/appData/qqbot/maintenance/voice-selection-20261112';
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'C:/Users/bgya7/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe'});
 const results=[];
 try{for(const width of [1280,390]){
  const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',r=>new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort());
  await page.addInitScript(()=>sessionStorage.setItem('whalegirl-access','audio-ui-local-only'));
  const headers={Authorization:'Bearer audio-ui-local-only'};
  let snapshot=await (await page.request.get(origin+'/api/audio',{headers})).json();
  snapshot.settings.providers.pc={kind:'speech',backend:'pc_bridge',api_url:'http://127.0.0.1:19881',model:'cyrene-aniflive-v2proplus'};
  snapshot.settings.speech_profile='pc';snapshot.settings.default_preset='cyrene';
  snapshot.settings.presets.cyrene={name:'昔涟',voice:'cyrene',pitch:0,speed:1,gain_db:0};
  snapshot.settings.presets.yachiyo={name:'八千代',voice:'yachiyo',pitch:0,speed:1,gain_db:0};
  snapshot.settings.presets.xiaofei={name:'小菲',voice:'xiaofei',pitch:0,speed:1,gain_db:0};
  await page.request.post(origin+'/api/audio/config',{headers,data:{revision:snapshot.revision,settings:snapshot.settings,keys:{pc:'synthetic-pc-only'}}});
  let active='cyrene',phase='completed',calls=0,checks=0,identity='';
  const voices=[{id:'cyrene',name:'昔涟',backend:'aniflive'},{id:'yachiyo',name:'八千代',backend:'gpt-sovits'},
   {id:'xiaofei',name:'小菲',backend:'gsv-lite'}];
  await page.route('**/api/audio/pc-status',async route=>{
   let connected=true,available=voices;
    if(phase==='queued'&&++checks<2){connected=false;available=[];}
   else if(phase==='queued'){
    phase='completed';active='xiaofei';
    const state=await (await page.request.get(origin+'/api/audio',{headers})).json();
    state.settings.default_preset='xiaofei';state.settings.providers.pc.model='xiaofei-gsv-lite-v2';
    await page.request.post(origin+'/api/audio/config',{headers,data:{revision:state.revision,settings:state.settings}});
   }
   const backend={cyrene:'aniflive',yachiyo:'gpt-sovits',xiaofei:'gsv-lite'}[active];
   await route.fulfill({json:{connected,active_voice:active,presets:[active],voices:available,backend,voice_switch:{id:identity,voice_id:active,phase},reason:'正在重连'}});
  });
  await page.route('**/api/audio/pc-select',async route=>{
   const body=route.request().postDataJSON();assert.equal(body.voice_id,'xiaofei');assert.match(body.request_id,/^[0-9a-f]{32}$/);
   calls++;identity=body.request_id;phase='queued';
   await route.fulfill({json:{id:identity,voice_id:'xiaofei',phase}});
  });
  await page.goto(origin+'/#audio-console');
  await page.locator('#audio-pc-check').click();
  await page.getByRole('button',{name:'小菲 · CUDA 图加速',exact:true}).waitFor();
  await page.getByRole('button',{name:'小菲 · CUDA 图加速',exact:true}).click();
  await page.waitForFunction(()=>document.getElementById('audio-pc-state').textContent.includes('正在切换'));
  assert.equal(await page.locator('#audio-pc-voices button').count(),3);
  for(const b of await page.locator('#audio-pc-voices button').all())assert.equal(await b.isDisabled(),true);
  await page.locator('#audio-pc-check').click();
  await page.getByRole('button',{name:'小菲 · CUDA 图加速 · 使用中',exact:true}).waitFor();
  assert.equal(await page.locator('#audio-speech-model').inputValue(),'xiaofei-gsv-lite-v2');
  assert.match(await page.locator('#audio-default-note').textContent(),/小菲/);
  assert.equal(calls,1);assert.deepEqual(errors,[]);
  await page.locator('#audio-console').screenshot({path:path.join(output,'switch-ui-'+width+'.png')});
  const rowOverflow=await page.locator('#audio-pc-voices').evaluate(e=>e.scrollWidth>e.clientWidth+1);
  assert.equal(rowOverflow,false);
  results.push({width,switchRequests:calls,buttonsRetainedDuringReconnect:true,disabledDuringSwitch:true,defaultAndModelSynced:true,errors,simulatedSwitch:true});
  await page.close();
 }}finally{await browser.close();}
 fs.writeFileSync(path.join(output,'ui-tests.json'),JSON.stringify(results,null,2));console.log(JSON.stringify(results));
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
