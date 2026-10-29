const fs=require('fs'),path=require('path');
const {chromium}=require('C:/Users/bgya7/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const out=path.join(__dirname,'..','artifacts','chat-control-merge');
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
      await page.addInitScript(value=>sessionStorage.setItem('whalegirl-access',value),fixture.token);
      const headers={Authorization:'Bearer '+fixture.token};
      await page.request.post(base+'api/proactive/config',{headers,data:{values:{autonomy_enabled:true,participation:'balanced',proactive_per_day:12,proactive_chat_ids:[]}}});
      await page.goto(base+'#proactive-panel');
      await page.waitForFunction(()=>document.querySelector('#autonomy-enabled')?.closest('.panel')?.id==='proactive-panel'&&document.querySelector('#autonomy-enabled').checked);
      await page.locator('#proactive-per-day').fill('17');
      await page.locator('#participation').selectOption('quiet');
      await page.locator('#proactive-chatlist input[type=checkbox]').first().check();
      let saved=page.waitForResponse(response=>response.url().endsWith('/api/proactive/config')&&response.request().method()==='POST');
      await page.locator('#autonomy-enabled').uncheck();
      if((await saved).status()!==200)throw Error('Unified save failed');
      await page.waitForFunction(()=>!document.querySelector('#autonomy-enabled').disabled);
      const first=await (await page.request.get(base+'api/config',{headers})).json();
      if(first.autonomy_enabled||first.proactive_per_day!==17||first.participation!=='quiet'||!first.proactive_chat_ids.includes('room@chatroom'))throw Error('Switch lost edited settings');
      saved=page.waitForResponse(response=>response.url().endsWith('/api/proactive/config')&&response.request().method()==='POST');
      await page.locator('#autonomy-enabled').check();await saved;
      await page.waitForFunction(()=>!document.querySelector('#autonomy-enabled').disabled);
      const second=await (await page.request.get(base+'api/config',{headers})).json();
      const placement=await page.locator('#participation').evaluate(element=>element.closest('.panel').id);
      const overflow=await page.locator('#proactive-panel').evaluate(element=>element.scrollWidth>element.clientWidth+3);
      const count=await page.locator('#autonomy-enabled').count();
      if(!second.autonomy_enabled||second.proactive_per_day!==17||placement!=='proactive-panel'||count!==1||overflow||errors.length)throw Error(JSON.stringify({width,placement,count,overflow,errors}));
      await page.screenshot({path:path.join(out,'ui-'+width+'.png'),fullPage:true});
      results.push({width,modeSwitchBothDirections:true,editsPreserved:true,singleModeControl:count===1,participationPlacement:placement,overflow,errors});
      await page.close();
    }
    fs.writeFileSync(path.join(out,'ui-verification.json'),JSON.stringify(results,null,2));
    console.log(JSON.stringify(results));
  }finally{await browser.close();}
})().catch(error=>{console.error(error.message);process.exitCode=1;});
