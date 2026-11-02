const {chromium}=require('C:/Users/bgya7/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:'C:/Users/bgya7/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe'}),results=[];
  try{
    for(const width of [1280,390]){
      const page=await browser.newPage({viewport:{width,height:1100}}),errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      await page.addInitScript(()=>sessionStorage.setItem('whalegirl-access','ui-test-only'));
      await page.goto(process.argv[2]+'/#proactive-panel');
      await page.waitForFunction(()=>{const e=document.querySelector('#sticker-usage-target');return e&&e.value!=='';});
      if(await page.locator('#sticker-aggressiveness').count()!==1)throw Error('Duplicate aggressiveness controls');
      await page.locator('#sticker-preset-rich').click();
      if(await page.locator('#sticker-usage-target').inputValue()!=='75'||await page.locator('#sticker-aggressiveness').inputValue()!=='very_high')throw Error('Rich preset failed');
      await page.locator('#sticker-preset-first').click();
      if(await page.locator('#sticker-usage-target').inputValue()!=='90'||await page.locator('#sticker-aggressiveness').inputValue()!=='sticker_first')throw Error('Sticker-first preset failed');
      const saved=page.waitForResponse(r=>r.url().endsWith('/api/proactive/config'));
      await page.locator('#save-proactive').click();
      const response=await saved,config=await response.json();
      if(response.status()!==200||config.sticker_usage_target!==90||config.sticker_aggressiveness!=='sticker_first')throw Error('Unified save failed');
      await page.reload();
      await page.waitForFunction(()=>document.querySelector('#sticker-usage-target')?.value==='90');
      if(await page.locator('#sticker-aggressiveness').inputValue()!=='sticker_first')throw Error('Reload failed');
      const overflow=await page.locator('#proactive-panel').evaluate(e=>e.scrollWidth>e.clientWidth+3);
      if(overflow||errors.length)throw Error(JSON.stringify({width,overflow,errors}));
      await page.locator('#proactive-panel').screenshot({path:require('path').join(__dirname,'..','artifacts','sticker-initiative','ui-'+width+'.png')});
      results.push({width,presets:true,save_reload:true,overflow,errors});await page.close();
    }
    console.log(JSON.stringify(results));
  }finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
