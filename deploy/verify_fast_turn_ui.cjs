const {chromium}=require('C:/Users/bgya7/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:'C:/Users/bgya7/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe'}),results=[];
  try{
    for(const width of [1280,390]){
      const page=await browser.newPage({viewport:{width,height:1100}}),errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      await page.addInitScript(()=>sessionStorage.setItem('whalegirl-access','ui-test-only'));
      await page.goto(process.argv[2]+'/#proactive-panel');
      await page.waitForFunction(()=>!!document.querySelector('#sticker-usage-target')?.value);
      const control=page.locator('#fast-reply-enabled');
      if(await control.count()!==1||!await control.isChecked())throw Error('Fast-send control default missing');
      for(const checked of [false,true]){
        await control.setChecked(checked);
        const saved=page.waitForResponse(r=>r.url().endsWith('/api/proactive/config'));
        await page.locator('#save-proactive').click();const response=await saved,cfg=await response.json();
        if(response.status()!==200||cfg.fast_reply_enabled!==checked)throw Error('Switch save failed');
        await page.reload();await page.waitForFunction(()=>!!document.querySelector('#sticker-usage-target')?.value);
        if(await control.isChecked()!==checked)throw Error('Switch reload failed');
      }
      if(errors.length||await page.locator('#proactive-panel').evaluate(e=>e.scrollWidth>e.clientWidth+3))throw Error(JSON.stringify(errors));
      results.push({width,legacy_switch:true,fast_switch:true,errors});await page.close();
    }
    console.log(JSON.stringify(results));
  }finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
