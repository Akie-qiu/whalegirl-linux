const fs=require('fs'),path=require('path');
const {chromium}=require('C:/Users/bgya7/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const out=path.resolve(process.argv[2]);
(async()=>{
  const fixture=JSON.parse(fs.readFileSync(path.join(out,'preview.json'),'utf8'));
  const base='http://127.0.0.1:'+fixture.port+'/';
  const browser=await chromium.launch({headless:true,executablePath:'C:/Users/bgya7/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe'});
  const results=[];
  try{
    for(const width of [1280,390]){
      const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];
      page.on('pageerror',error=>errors.push(error.message));
      await page.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
      await page.addInitScript(token=>sessionStorage.setItem('whalegirl-access',token),fixture.token);
      await page.goto(base+'#web-search-panel');
      await page.waitForFunction(()=>document.querySelector('#web-search-result-count')?.value);
      const input=page.locator('#web-search-api-key');
      if(await input.getAttribute('type')!=='password')throw Error('Search key is not a password input');
      await input.fill('fixture-search-ui');
      const saved=page.waitForResponse(response=>response.url().endsWith('/api/config')&&response.request().method()==='POST');
      await page.getByRole('button',{name:'保存联网设置',exact:true}).click();
      const response=await saved,config=await response.json();
      await page.waitForFunction(()=>document.querySelector('#web-search-api-key').value==='');
      if(response.status()!==200||!config.search_api_key_configured||JSON.stringify(config).includes('fixture-search-ui'))throw Error('Independent search key save failed');
      await page.evaluate(()=>location.hash='#model-panel');
      const modelSaved=page.waitForResponse(response=>response.url().endsWith('/api/config')&&response.request().method()==='POST');
      await page.locator('#save-model').click();
      const afterModel=await (await modelSaved).json();
      if(!afterModel.search_api_key_configured)throw Error('Saving the chat model lost the search credential');
      await page.evaluate(()=>location.hash='#web-search-panel');
      await page.waitForFunction(()=>document.querySelector('#web-search-key-status').textContent.includes('独立搜索密钥'));
      const overflow=await page.locator('#web-search-panel').evaluate(element=>element.scrollWidth>element.clientWidth+3);
      if(overflow||errors.length)throw Error(JSON.stringify({width,overflow,errors}));
      await page.screenshot({path:path.join(out,'search-'+width+'.png'),fullPage:true});
      results.push({width,passwordInput:true,keySavedPrivately:true,clearedAfterSave:true,overflow,errors});
      await page.close();
    }
    fs.writeFileSync(path.join(out,'ui-verification.json'),JSON.stringify(results,null,2));
    console.log(JSON.stringify(results));
  }finally{await browser.close();}
})().catch(error=>{console.error(error.message);process.exitCode=1;});
