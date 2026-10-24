const fs=require('fs'),path=require('path'),cp=require('child_process');
const {chromium}=require('C:/Users/bgya7/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
  const root=path.resolve(__dirname,'..'),out=path.join(root,'artifacts','tieba');
  fs.mkdirSync(out,{recursive:true});
  const data=fs.mkdtempSync(path.join(root,'test-runs','tieba-ui-'));
  const log=fs.openSync(path.join(out,'ui-server.log'),'a');
  const process=cp.spawn(path.join(root,'.venv','Scripts','python.exe'),[path.join(__dirname,'preview_tieba_ui.py'),data],{
    cwd:root,windowsHide:true,stdio:['ignore',log,log],env:{...global.process.env,LOGURU_LEVEL:'ERROR'}});
  let browser;
  try{
    for(let i=0;i<40;i++){
      try{if((await fetch('http://127.0.0.1:18102/health')).ok)break;}catch{}
      if(process.exitCode!==null)throw Error('UI fixture stopped');
      await new Promise(r=>setTimeout(r,150));
    }
    const access=fs.readFileSync(path.join(data,'admin.token'),'utf8').trim();
    browser=await chromium.launch({headless:true,executablePath:'C:/Users/bgya7/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe'});
    const result=[];
    for(const width of [1280,390]){
      const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      await page.route('http://127.0.0.1:18102/api/**',async route=>{
        const headers={...route.request().headers(),origin:'http://127.0.0.1'};
        await route.continue({headers});
      });
      await page.addInitScript(access=>sessionStorage.setItem('whalegirl-access',access),access);
      await page.goto('http://127.0.0.1:18102/#tieba-panel');
      await page.waitForFunction(()=>document.getElementById('tieba-tieba_interval_minutes')?.value==='30');
      const panel=page.locator('#tieba-panel');await panel.scrollIntoViewIfNeeded();
      const overflow=await panel.evaluate(e=>e.scrollWidth>e.clientWidth+3);
      const group=await page.locator('a[href="#tieba-panel"]').evaluate(e=>e.closest('[data-nav-group]')?.dataset.navGroup);
      const targetCount=await page.locator('#tieba-targets input:checked').count();
      const forum=await page.locator('#tieba-tieba_forums').inputValue();
      const loginCount=await panel.locator('input[type="password"]').count();
      await panel.screenshot({path:path.join(out,'settings-'+width+'.png')});
      await page.evaluate(()=>location.hash='#images-panel');
      const imageTab=page.getByRole('tab',{name:'贴吧素材',exact:true});
      await imageTab.click();await page.waitForTimeout(150);
      const imageTabSelected=await imageTab.getAttribute('aria-selected');
      if(overflow||errors.length||group!=='interaction'||targetCount!==1||forum!=='绝区零内鬼'||loginCount!==2||imageTabSelected!=='true')
        throw Error(JSON.stringify({width,overflow,errors,group,targetCount,forum,loginCount,imageTabSelected}));
      result.push({width,overflow,errors,group,targetCount,forum,loginCount,imageTabSelected});
      await page.unrouteAll({behavior:'ignoreErrors'});await page.close();
    }
    fs.writeFileSync(path.join(out,'ui-acceptance.json'),JSON.stringify(result,null,2));
    console.log(JSON.stringify(result));
  }finally{if(browser)await browser.close();process.kill();fs.closeSync(log);}
})().catch(e=>{console.error(e.message);global.process.exitCode=1;});
