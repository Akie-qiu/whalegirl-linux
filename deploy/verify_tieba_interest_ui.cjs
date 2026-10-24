const fs=require('fs'),path=require('path'),cp=require('child_process');
const {chromium}=require('C:/Users/bgya7/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
  const root=path.resolve(__dirname,'..'),out=path.join(root,'artifacts','tieba-interest');fs.mkdirSync(out,{recursive:true});
  const data=fs.mkdtempSync(path.join(root,'test-runs','tieba-interest-ui-')),log=fs.openSync(path.join(out,'ui-server.log'),'a');
  const fixture=cp.spawn(path.join(root,'.venv','Scripts','python.exe'),[path.join(__dirname,'preview_tieba_ui.py'),data,'--controls'],{cwd:root,windowsHide:true,stdio:['ignore',log,log],env:{...process.env,LOGURU_LEVEL:'ERROR',WHALEGIRL_PUBLIC_ORIGINS:'http://127.0.0.1:18102'}});
  let browser;
  try{
    for(let i=0;i<50;i++){try{if((await fetch('http://127.0.0.1:18102/health')).ok)break;}catch{}if(fixture.exitCode!==null)throw Error('UI fixture stopped');await new Promise(r=>setTimeout(r,120));}
    const access=fs.readFileSync(path.join(data,'admin.token'),'utf8').trim();
    browser=await chromium.launch({headless:true,executablePath:'C:/Users/bgya7/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe'});
    const results=[];
    for(const width of [1280,390]){
      const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
      await page.route('http://127.0.0.1:18102/api/**',route=>route.continue({headers:{...route.request().headers(),origin:'http://127.0.0.1'}}));
      await page.addInitScript(value=>sessionStorage.setItem('whalegirl-access',value),access);
      await page.goto('http://127.0.0.1:18102/#tieba-panel');
      const input=page.locator('#tieba-tieba_interest_sensitivity');await input.waitFor();
      if(await input.inputValue()!==(width===1280?'70':'90'))throw Error('Unexpected sensitivity');
      await input.fill(width===1280?'90':'85');
      const [saved]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/tieba/config')&&r.request().method()==='POST'),page.getByRole('button',{name:'保存贴吧设置',exact:true}).click()]);
      if(!saved.ok())throw Error('Sensitivity save failed '+saved.status());
      const config=await saved.json();if(config.tieba_interest_sensitivity!==(width===1280?90:85))throw Error('Roundtrip failed');
      await page.locator('#tieba-panel').screenshot({path:path.join(out,'panel-'+width+'.png')});
      const overflow=await page.locator('#tieba-panel').evaluate(e=>e.scrollWidth>e.clientWidth+3);
      if(overflow||errors.length)throw Error(JSON.stringify({width,overflow,errors}));
      results.push({width,saved:config.tieba_interest_sensitivity,overflow,errors});
      await page.unrouteAll({behavior:'ignoreErrors'});await page.close();
    }
    fs.writeFileSync(path.join(out,'ui-acceptance.json'),JSON.stringify(results,null,2));console.log(JSON.stringify(results));
  }finally{if(browser)await browser.close();fixture.kill();fs.closeSync(log);}
})().catch(e=>{console.error(e);process.exitCode=1;});
