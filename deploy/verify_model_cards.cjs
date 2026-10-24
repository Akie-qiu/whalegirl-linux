const fs=require('fs');
const path=require('path');
const {chromium}=require('C:/Users/bgya7/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..','whalegirl_linux');
const output=path.resolve(__dirname,'..','artifacts');

(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:'C:/Users/bgya7/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe'});
  const results=[];
  try{
    for(const width of [1280,390]){
      const page=await browser.newPage({viewport:{width,height:900}});
      let writes=0;const errors=[];
      page.on('pageerror',error=>errors.push(error.message));
      await page.route('**/*',async route=>{
        const request=route.request(),url=new URL(request.url());
        if(request.method()!=='GET'){writes++;return route.fulfill({status:409,body:'{}'});}
        const filename=url.pathname==='/'?'web.html':path.basename(url.pathname);
        const file=path.join(root,filename);
        if(!fs.existsSync(file))return route.fulfill({status:404,body:'{}'});
        const contentType=filename.endsWith('.js')?'text/javascript':filename.endsWith('.css')?'text/css':'text/html';
        await route.fulfill({status:200,body:fs.readFileSync(file),contentType});
      });
      await page.goto('http://whalegirl.local/#model-panel');
      await page.evaluate(()=>{
        document.getElementById('auth').hidden=true;document.getElementById('dashboard').hidden=false;
        document.getElementById('api-url').value='https://api.deepseek.com/chat/completions';
        document.getElementById('model').value='deepseek-flash';syncModelPreset();
      });
      const card=name=>page.locator('[data-model-preset="'+name+'"]');
      if(await card('flash').getAttribute('aria-pressed')!=='true')throw Error('Current model was not selected');
      await page.locator('#api-key').fill('draft-key-check');
      await card('pro').click();
      if(await page.locator('#model').inputValue()!=='deepseek-v4-pro')throw Error('Pro preset did not fill model');
      if(await page.locator('#api-key').inputValue()!=='draft-key-check')throw Error('Preset changed key draft');
      await card('custom').click();
      if(await page.locator('#model').inputValue()!=='deepseek-v4-pro')throw Error('Custom erased existing fields');
      await page.locator('#model').fill('custom-model');
      if(await card('custom').getAttribute('aria-pressed')!=='true')throw Error('Manual model not recognized');
      await card('flash').click();
      await page.locator('#api-key').fill('');
      const overflow=await page.locator('#model-panel').evaluate(panel=>{
        const b=panel.getBoundingClientRect();return [...panel.querySelectorAll('.model-preset,.model-preset-copy')].some(el=>{
          const r=el.getBoundingClientRect();return r.right>b.right+1||r.left<b.left-1||el.scrollWidth>el.clientWidth+1;
        });
      });
      if(overflow||errors.length||writes)throw Error(JSON.stringify({overflow,errors,writes}));
      await page.locator('#model-panel').screenshot({path:path.join(output,'model-cards-'+width+'.png')});
      results.push({width,presets:3,originalForm:true,keyPreserved:true,noAutomaticSave:true,overflow:false,scriptErrors:0});
      await page.close();
    }
    console.log(JSON.stringify(results));
  }finally{await browser.close();}
})().catch(error=>{console.error(error.message);process.exitCode=1;});
