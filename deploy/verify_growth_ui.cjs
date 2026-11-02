const path=require('path');
const {chromium}=require('C:/Users/bgya7/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:'C:/Users/bgya7/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe'}),results=[];
  try{
    for(const width of [1280,390]){
      const page=await browser.newPage({viewport:{width,height:1100}}),errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      page.on('response',async response=>{if(response.status()>=400&&response.url().includes('/api/'))console.error(response.status(),await response.text());});
      await page.addInitScript(()=>sessionStorage.setItem('whalegirl-access','ui-test-only'));
      await page.goto(process.argv[2]+'/#memory-panel');
      await page.locator('#growth-console > summary').click();
      await page.waitForSelector('#growth-insights .record');
      if(await page.locator('#growth-tieba').isChecked())throw Error('Tieba learning should default off');
      const budget=page.locator('#growth-budget');
      if(await budget.getAttribute('max')!=='500000')throw Error('Budget maximum should be 500000');
      await budget.fill('500000');
      if(!await budget.evaluate(e=>e.checkValidity()))throw Error('500000 budget should be valid');
      await budget.fill('20000');
      await page.locator('#growth-insights').getByRole('button',{name:'编辑',exact:true}).first().click();
      await page.waitForSelector('#growth-editor[open]');
      await page.locator('#growth-edit-content').fill('演示：倾听时先理解处境，再决定是否给建议 '+width);
      await page.locator('#growth-edit-save').click();
      await page.waitForFunction(()=>!document.querySelector('#growth-editor').open,{},{timeout:5000});
      await page.locator('#growth-insights').getByRole('button',{name:'停用',exact:true}).first().click();
      await page.locator('#growth-insights').getByRole('button',{name:'恢复',exact:true}).first().click();
      await page.locator('#growth-insights').getByRole('button',{name:'来源与反思',exact:true}).first().click();
      await page.waitForSelector('#growth-detail[open]');await page.locator('#growth-detail-close').click();
      const overflow=await page.locator('#growth-console').evaluate(e=>e.scrollWidth>e.clientWidth+3);
      await page.locator('#growth-console').screenshot({path:path.join(__dirname,'..','artifacts','autonomous-growth','ui-'+width+'.png')});
      if(errors.length||overflow)throw Error(JSON.stringify({width,errors,overflow}));
      results.push({width,errors,overflow,budget_500000:true,tieba_default_off:true,edit:true,disable_restore:true,source_dialog:true});
      await page.close();
    }
    console.log(JSON.stringify(results));
  }finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
