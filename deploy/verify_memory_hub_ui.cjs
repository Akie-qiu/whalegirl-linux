const fs=require('fs'),path=require('path');
const {chromium}=require('C:/Users/bgya7/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:'C:/Users/bgya7/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe'});
  const results=[];
  try{
    for(const width of [1280,390]){
      const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      await page.addInitScript(()=>sessionStorage.setItem('whalegirl-access','ui-test-only'));
      await page.goto('http://127.0.0.1:18109/#memory-panel');
      await page.waitForFunction(()=>document.querySelector('#hub-list .record'));
      await page.locator('#hub-new').click();
      await page.waitForSelector('#hub-editor[open]');
      const modalOverflow=await page.locator('#hub-editor').evaluate(e=>e.scrollWidth>e.clientWidth+3);
      await page.locator('#hub-edit-close').click();
      await page.getByRole('button',{name:'来源与修订',exact:true}).first().click();
      await page.waitForSelector('#hub-detail[open]');
      await page.locator('#hub-detail-close').click();
      await page.locator('#hub-tier').selectOption('stable');
      await page.waitForFunction(()=>document.querySelectorAll('#hub-list .record').length===1);
      const overflow=await page.locator('#memory-panel').evaluate(e=>e.scrollWidth>e.clientWidth+3);
      const hiddenLearning=!(await page.locator('nav a[href="#learning-panel"]').isVisible());
      await page.screenshot({path:path.join(__dirname,'..','artifacts','memory-hub','ui-'+width+'.png'),fullPage:true});
      if(errors.length||overflow||modalOverflow||!hiddenLearning)throw Error(JSON.stringify({width,errors,overflow,modalOverflow,hiddenLearning}));
      results.push({width,errors,overflow,modalOverflow,hiddenLearning,dialogs:true,filter:true});
      await page.close();
    }
    fs.writeFileSync(path.join(__dirname,'..','artifacts','memory-hub','ui-acceptance.json'),JSON.stringify(results,null,2));
    console.log(JSON.stringify(results));
  }finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
