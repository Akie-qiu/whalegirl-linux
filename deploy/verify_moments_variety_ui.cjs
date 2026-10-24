const fs=require('fs'),path=require('path'),cp=require('child_process');
const {chromium}=require('C:/Users/bgya7/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
  const root=path.resolve(__dirname,'..'),out=path.join(root,'artifacts','moments-variety');fs.mkdirSync(out,{recursive:true});
  const data=fs.mkdtempSync(path.join(root,'test-runs','moments-variety-ui-')),log=fs.openSync(path.join(out,'ui-server.log'),'a');
  const fixture=cp.spawn(path.join(root,'.venv','Scripts','python.exe'),[path.join(__dirname,'preview_tieba_ui.py'),data],{cwd:root,windowsHide:true,stdio:['ignore',log,log],env:{...process.env,LOGURU_LEVEL:'ERROR',WHALEGIRL_PUBLIC_ORIGINS:'http://127.0.0.1:18102'}});
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
      await page.goto('http://127.0.0.1:18102/#moments-panel');
      const input=page.getByLabel('主动生成配图积极性 / 0～100',{exact:true});await input.waitFor();
      if(await input.inputValue()!==(width===1280?'75':'90'))throw Error('Unexpected generation eagerness');
      await input.fill(width===1280?'90':'75');
      await page.getByLabel('选题与配图参考最近 / 天',{exact:true}).fill('7');
      const [saved]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/config')&&r.request().method()==='POST'),page.getByRole('button',{name:'保存朋友圈规则',exact:true}).click()]);
      if(!saved.ok())throw Error('Moments save failed '+saved.status());
      const config=await saved.json();if(config.moments_image_generate_eagerness!==(width===1280?90:75)||config.moments_variety_days!==7)throw Error('Roundtrip failed');
      await page.locator('#moments-panel').screenshot({path:path.join(out,'panel-'+width+'.png')});
      const overflow=await page.locator('#moments-panel').evaluate(e=>e.scrollWidth>e.clientWidth+3);
      if(overflow||errors.length)throw Error(JSON.stringify({width,overflow,errors}));
      results.push({width,eagerness:config.moments_image_generate_eagerness,variety_days:config.moments_variety_days,overflow,errors});
      await page.unrouteAll({behavior:'ignoreErrors'});await page.close();
    }
    fs.writeFileSync(path.join(out,'ui-acceptance.json'),JSON.stringify(results,null,2));console.log(JSON.stringify(results));
  }finally{if(browser)await browser.close();fixture.kill();fs.closeSync(log);}
})().catch(e=>{console.error(e);process.exitCode=1;});
