// Live assets/GETs; all configuration and model-test POSTs are browser-local mocks.
const cp = require('child_process');
const path = require('path');
const fs = require('fs');
const {chromium} = require('C:/Users/bgya7/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

(async () => {
  const access = cp.execFileSync('ssh', ['-o','BatchMode=yes','root@120.46.186.50',
    'cat /var/lib/whalegirl-linux/core/admin.token'], {encoding:'utf8',windowsHide:true}).trim();
  const tunnel = cp.spawn('ssh', ['-N','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes',
    '-L','127.0.0.1:18110:127.0.0.1:8092','root@120.46.186.50'], {stdio:'ignore',windowsHide:true});
  let browser;
  try {
    browser = await chromium.launch({headless:true,
      executablePath:'C:/Users/bgya7/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe'});
    const results=[];
    for(const width of [1280,390]){
      const page=await browser.newPage({viewport:{width,height:1050}}),errors=[];
      let publicConfig=null,submitted=null,modelTests=0;
      page.on('pageerror',e=>errors.push(e.message));
      await page.route('**/api/**',async route=>{
        const request=route.request(),url=request.url();
        const headers={...request.headers(),Origin:'http://127.0.0.1'};
        if(request.method()==='POST'){
          if(url.endsWith('/api/config')){
            submitted=request.postDataJSON();
            publicConfig={...publicConfig,...submitted.settings};
            await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(publicConfig)});
          }else if(url.endsWith('/api/model/test')){
            modelTests++;
            await route.fulfill({status:200,contentType:'application/json',
              body:JSON.stringify({ok:true,message:'模拟连接正常',api_style:'responses'})});
          }else await route.abort();
          return;
        }
        try{
          const response=await route.fetch({headers});
          if(url.endsWith('/api/config')){
            publicConfig=publicConfig||await response.json();
            await route.fulfill({response,body:JSON.stringify(publicConfig)});
          }else await route.fulfill({response});
        }catch{await route.abort().catch(()=>{});}
      });
      await page.addInitScript(value=>sessionStorage.setItem('whalegirl-access',value),access);
      await page.goto('http://127.0.0.1:18110/#model-panel');
      await page.waitForFunction(()=>typeof cfg!=='undefined'&&cfg);
      const panel=page.locator('#model-panel');
      const original=await panel.locator('#model').inputValue();
      const keySet=await panel.locator('#api-key').inputValue();
      await panel.locator('#api-url').fill('https://example.test/proxy/v1/chat/completions');
      await panel.locator('[data-model-preset=responses]').click();
      if(await panel.locator('#model').inputValue()!==original)throw Error('Responses card changed model');
      if(await panel.locator('#api-key').inputValue()!==keySet)throw Error('Responses card changed key');
      if(await panel.locator('#api-url').inputValue()!=='https://example.test/proxy/v1/responses')throw Error('Endpoint translation');
      if(await panel.locator('#api-style').inputValue()!=='responses')throw Error('Protocol not selected');
      await panel.locator('#save-model').click();
      await page.waitForFunction(()=>!document.getElementById('save-model').disabled);
      if(!submitted||submitted.settings.api_style!=='responses'||submitted.settings.model!==original)throw Error('Protocol save missing');
      if(submitted.api_key)throw Error('Unexpected key submission');
      await panel.locator('#test-model').click();
      await page.waitForFunction(()=>!document.getElementById('test-model').disabled);
      if(modelTests!==1)throw Error('Test action not submitted');
      await panel.locator('[data-model-preset=flash]').click();
      if(await panel.locator('#api-style').inputValue()!=='openai')throw Error('Legacy card did not restore protocol');
      await panel.locator('[data-model-preset=responses]').click();
      const overflow=await panel.evaluate(e=>e.scrollWidth>e.clientWidth+2);
      if(overflow||errors.length)throw Error(JSON.stringify({width,overflow,errors}));
      const folder=path.resolve('test-runs/responses-build-20261024');
      await panel.screenshot({path:path.join(folder,'model-'+width+'.png')});
      results.push({width,cards:await panel.locator('[data-model-preset]').count(),protocolSave:true,
        originalModelPreserved:true,originalKeyPreserved:true,overflow,errors});
      await page.close();
    }
    fs.writeFileSync('test-runs/responses-build-20261024/ui-report.json',JSON.stringify(results,null,2));
    console.log(JSON.stringify(results));
  }finally{if(browser)await browser.close();tunnel.kill();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
