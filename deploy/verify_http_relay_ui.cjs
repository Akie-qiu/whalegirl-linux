const {chromium}=require('C:/Users/bgya7/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:'C:/Users/bgya7/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe'}),results=[];
  try{
    for(const width of [1280,390]){
      const page=await browser.newPage({viewport:{width,height:1100}}),errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      await page.addInitScript(()=>sessionStorage.setItem('whalegirl-access','ui-test-only'));
      await page.goto(process.argv[2]+'/#model-panel');
      await page.waitForFunction(()=>!!document.querySelector('#api-url')?.value);
      await page.locator('#api-url').fill('http://154.9.235.108:8080/v1');
      await page.locator('#model').fill('deepseek-v4.1-flash');await page.locator('#api-style').selectOption('openai');
      const saved=page.waitForResponse(r=>r.url().endsWith('/api/config')&&r.request().method()==='POST');
      await page.locator('#save-model').click();const response=await saved,cfg=await response.json();
      if(response.status()!==200||cfg.api_url!=='http://154.9.235.108:8080/v1')throw Error('HTTP API save failed');
      for(const id of ['cooldown','send-interval']){
        await page.locator('#'+id).fill('0');
        if(!await page.locator('#'+id).evaluate(e=>e.checkValidity()))throw Error('Zero interval rejected');
      }
      const timingSaved=page.waitForResponse(r=>r.url().endsWith('/api/config')&&r.request().method()==='POST');
      await page.locator('#save-timing').click();const tr=await timingSaved,tc=await tr.json();
      if(tr.status()!==200||tc.chat_cooldown!==0||tc.min_send_interval!==0)throw Error('Zero interval save failed');
      await page.reload();await page.waitForFunction(()=>document.querySelector('#cooldown')?.value==='0');
      if(await page.locator('#api-url').inputValue()!=='http://154.9.235.108:8080/v1'||await page.locator('#send-interval').inputValue()!=='0')throw Error('Reload values differ');
      for(const id of ['model-panel','timing-panel']){
        const panel=page.locator('#'+id);
        if(await panel.evaluate(e=>e.scrollWidth>e.clientWidth+3))throw Error('Horizontal overflow');
        await panel.screenshot({path:require('path').join(__dirname,'..','artifacts','http-relay-zero','ui-'+id+'-'+width+'.png')});
      }
      if(errors.length)throw Error(errors.join(';'));
      results.push({width,http_save_reload:true,zero_save_reload:true,errors});await page.close();
    }
    console.log(JSON.stringify(results));
  }finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
