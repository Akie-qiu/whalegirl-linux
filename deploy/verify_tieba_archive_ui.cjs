const fs=require('fs'),path=require('path'),cp=require('child_process');
const {chromium}=require('C:/Users/bgya7/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
  const root=path.resolve(__dirname,'..'),out=path.join(root,'artifacts','tieba-archive');fs.mkdirSync(out,{recursive:true});
  const data=fs.mkdtempSync(path.join(root,'test-runs','tieba-archive-ui-')),log=fs.openSync(path.join(out,'ui-server.log'),'a');
  const fixture=cp.spawn(path.join(root,'.venv','Scripts','python.exe'),[path.join(__dirname,'preview_tieba_ui.py'),data,'--archive'],{cwd:root,windowsHide:true,stdio:['ignore',log,log],env:{...process.env,LOGURU_LEVEL:'ERROR'}});
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
      await page.goto('http://127.0.0.1:18102/#tieba-panel');await page.locator('#tieba-archives').getByRole('button',{name:'查看原文与图片',exact:true}).waitFor();
      const panel=page.locator('#tieba-panel'),toggle=page.locator('#tieba-tieba_archive_enabled');
      const panelOverflow=await panel.evaluate(e=>e.scrollWidth>e.clientWidth+3),enabled=await toggle.isChecked();
      await panel.scrollIntoViewIfNeeded();await panel.screenshot({path:path.join(out,'panel-'+width+'.png')});
      await page.locator('#tieba-archives').getByRole('button',{name:'查看原文与图片',exact:true}).click();
      const dialog=page.locator('dialog.tieba-archive-dialog');await dialog.waitFor();
      const literal=await dialog.locator('.tieba-original-text').textContent();
      await page.waitForFunction(()=>document.querySelector('dialog .tieba-archive-picture img')?.naturalWidth>0);
      const dialogOverflow=await dialog.evaluate(e=>e.scrollWidth>e.clientWidth+3);
      const endPresent=literal.includes('原文结束  '),whitespace=literal.includes('  这是一段原文\n\n保留换行与空格  \n');
      const scope=await dialog.getByText('已读取主楼完整正文 · 本次读取 1 个楼层 · 还有后续楼层未读取',{exact:true}).count();
      await dialog.screenshot({path:path.join(out,'original-'+width+'.png')});
      await dialog.getByRole('button',{name:'查看原始图片',exact:true}).click();
      const imageDialog=page.locator('dialog.tieba-archive-dialog').last();
      await page.waitForFunction(()=>document.querySelector('.tieba-original-image')?.naturalWidth>0);
      const rawImage=await imageDialog.locator('img').evaluate(e=>({width:e.naturalWidth,height:e.naturalHeight}));
      await imageDialog.getByRole('button',{name:'关闭',exact:true}).click();
      await dialog.getByRole('button',{name:'关闭',exact:true}).click();
      if(panelOverflow||dialogOverflow||!enabled||!endPresent||!whitespace||scope!==1||errors.length||rawImage.width!==24)throw Error(JSON.stringify({width,panelOverflow,dialogOverflow,enabled,endPresent,whitespace,scope,errors,rawImage}));
      results.push({width,panelOverflow,dialogOverflow,enabled,endPresent,whitespace,scope,errors,rawImage});
      await page.unrouteAll({behavior:'ignoreErrors'});await page.close();
    }
    fs.writeFileSync(path.join(out,'ui-acceptance.json'),JSON.stringify(results,null,2));console.log(JSON.stringify(results));
  }finally{if(browser)await browser.close();fixture.kill();fs.closeSync(log);}
})().catch(e=>{console.error(e);process.exitCode=1;});
