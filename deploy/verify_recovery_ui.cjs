const fs=require('fs'),path=require('path');
const {chromium}=require('C:/Users/bgya7/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const out=path.resolve(process.argv[2]);
(async()=>{
  const fixture=JSON.parse(fs.readFileSync(path.join(out,'preview.json'),'utf8')),base='http://127.0.0.1:'+fixture.port+'/';
  const browser=await chromium.launch({headless:true,executablePath:'C:/Users/bgya7/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe'});
  const checks=[];
  try{
    for(const width of [1280,390]){
      const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      await page.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
      await page.addInitScript(token=>sessionStorage.setItem('whalegirl-access',token),fixture.token);
      const headers={Authorization:'Bearer '+fixture.token};
      await page.request.post(base+'api/recovery/config',{headers,data:{enabled:true}});
      await page.request.post(base+'api/fixture/reset',{headers});
      await page.goto(base+'#model-panel');
      await page.waitForFunction(()=>document.querySelector('#recovery-badge')?.textContent==='需要处理').catch(()=>{throw Error('Initial recovery state did not render: '+errors.join(';'));});
      for(const id of ['safety-resume','safety-check']){
        if(await page.locator('#'+id).count()!==1||await page.locator('#'+id).evaluate(e=>e.closest('section').id)!=='recovery-center')throw Error('Control not centralized: '+id);
      }
      const recovered=page.waitForResponse(r=>r.url().endsWith('/api/recovery/run'));
      await page.locator('#safety-resume').click();
      if((await recovered).status()!==200)throw Error('Manual recovery endpoint rejected the UI request');
      await page.waitForFunction(()=>document.querySelector('#recovery-badge')?.textContent==='等待发送确认');
      await page.locator('#recovery-open-issues').click();
      await page.getByRole('button',{name:'微信已收到',exact:true}).click();
      await page.waitForFunction(()=>!document.querySelector('#recovery-issue-list button'));
      const saved=page.waitForResponse(r=>r.url().endsWith('/api/recovery/config'));
      await page.locator('#recovery-auto').uncheck(); await saved;
      await page.waitForFunction(()=>!document.querySelector('#recovery-auto').disabled);
      const config=await(await page.request.get(base+'api/config',{headers})).json();
      const overflow=await page.locator('#recovery-center').evaluate(e=>e.scrollWidth>e.clientWidth+3);
      if(config.send_auto_recovery_enabled||overflow||errors.length)throw Error(JSON.stringify({width,overflow,errors}));
      await page.screenshot({path:path.join(out,'recovery-'+width+'.png'),fullPage:true});
      checks.push({width,centralControls:true,manualRecovery:true,receiptReview:true,automaticToggle:true,overflow,errors});
      await page.close();
    }
    fs.writeFileSync(path.join(out,'ui-verification.json'),JSON.stringify(checks,null,2));console.log(JSON.stringify(checks));
  }finally{await browser.close();}
})().catch(error=>{console.error(error.message);process.exitCode=1;});
