const path=require('path');
const {chromium}=require('C:/Users/bgya7/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:'C:/Users/bgya7/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe'});
  const results=[];
  try{
    for(const width of [1280,390]){
      const page=await browser.newPage({viewport:{width,height:1100}}),errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      page.on('response',async r=>{if(r.status()>=400&&r.url().includes('/api/'))console.error(r.status(),r.url(),await r.text());});
      await page.addInitScript(()=>sessionStorage.setItem('whalegirl-access','natural-ui-test-only'));
      await page.goto(process.argv[2]+'/#memory-panel');
      await page.locator('#hub-style > summary').click();
      await page.waitForSelector('#hub-style-notes .record');
      if(await page.locator('#autonomy-enabled').count())throw Error('Old/new engine switch must be absent');
      if(await page.locator('[id^="style-reply_"]').count())throw Error('Duplicate voice controls must be absent');
      if(!await page.locator('#hub-style-notes').textContent().then(s=>s.includes('真人原话')))throw Error('Human source quote missing');
      await page.locator('#hub-style-notes').getByRole('button',{name:'编辑',exact:true}).first().click();
      await page.waitForSelector('#hub-editor[open]');
      await page.locator('#hub-edit-content').fill('事情告一段落后简短接住 · 演示修改 '+width);
      await page.locator('#hub-edit-save').click();
      await page.waitForFunction(()=>!document.querySelector('#hub-editor').open);
      await page.locator('#hub-style-refresh').click();
      await page.waitForFunction(w=>document.querySelector('#hub-style-notes').textContent.includes(String(w)),width);
      await page.locator('#hub-style-use').uncheck();await page.locator('#sticky-save-btn').click();
      await page.waitForFunction(()=>document.querySelector('#hub-style-status').textContent.includes('使用已暂停'));
      await page.locator('#hub-style-use').check();await page.locator('#hub-style-save').click();
      await page.waitForFunction(()=>document.querySelector('#hub-style-status').textContent.includes('正在使用'));
      await page.screenshot({path:path.join(__dirname,'..','artifacts','natural-chat-20261102','ui-'+width+'.png'),fullPage:false});
      const overflow=await page.locator('#hub-style').evaluate(e=>e.scrollWidth>e.clientWidth+4);
      await page.goto(process.argv[2]+'/#proactive-panel');
      await page.waitForSelector('#sticker-only-eagerness');
      if(!await page.locator('#sticker-only-eagerness').isVisible())throw Error('Sticker preferences missing');
      if(errors.length||overflow)throw Error(JSON.stringify({width,errors,overflow}));
      results.push({width,errors,overflow,edit:true,use_switch:true,source_quote:true,single_engine:true,sticker_controls:true});
      await page.close();
    }
    console.log(JSON.stringify(results));
  }finally{await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
