const fs=require('fs'),path=require('path'),cp=require('child_process');
const {chromium}=require('C:/Users/bgya7/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
  const root=path.resolve(__dirname,'..'),out=path.join(root,'artifacts','tieba-controls');fs.mkdirSync(out,{recursive:true});
  const data=fs.mkdtempSync(path.join(root,'test-runs','tieba-controls-ui-')),log=fs.openSync(path.join(out,'ui-server.log'),'a');
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
      await page.locator('#tieba-panel').getByText('学到的梗',{exact:true}).click();
      await page.getByRole('button',{name:'编辑这个梗',exact:true}).click();
      const d=page.locator('dialog.tieba-meme-dialog'),phrase='下班摸鱼'+width;
      await d.getByLabel('梗的说法',{exact:true}).fill(phrase);
      await d.getByLabel('含义',{exact:true}).fill('轻松聊天时给自己找点休息时间');
      await d.getByLabel('适用与不适用场景',{exact:true}).fill('闲聊可以用，认真谈事情不用');
      await page.evaluate(()=>window.tieba.refresh());
      if(await d.getByLabel('梗的说法',{exact:true}).inputValue()!==phrase)throw Error('Refresh lost draft');
      const overflow=await d.evaluate(e=>e.scrollWidth>e.clientWidth+3);
      await d.screenshot({path:path.join(out,'meme-editor-'+width+'.png')});
      const [saved]=await Promise.all([page.waitForResponse(r=>r.url().includes('/api/tieba/memes/')&&r.request().method()==='POST'),d.getByRole('button',{name:'保存修改',exact:true}).click()]);
      if(!saved.ok())throw Error('Meme save '+saved.status()+' '+await saved.text());
      await d.waitFor({state:'detached'});
      await page.locator('.tieba-meme strong').filter({hasText:phrase}).waitFor();
      const master=page.getByRole('checkbox',{name:'贴吧功能总开关',exact:true});
      await master.uncheck();await page.waitForFunction(()=>document.querySelector('#tieba-panel .timing-detail').textContent.includes('总开关已关闭'));
      const unchanged=await page.locator('#tieba-tieba_share_enabled').isChecked()&&await page.locator('#tieba-tieba_learn_enabled').isChecked();
      await master.check();await page.waitForFunction(()=>document.querySelector('#tieba-panel .timing-detail').textContent.startsWith('已开启'));
      if(width===1280){
        await page.locator('#tieba-panel').getByText('分享发送记录',{exact:true}).click();
        await page.getByRole('button',{name:'重试这条分享',exact:true}).click();
        await page.locator('.tieba-share strong').filter({hasText:'待分享'}).waitFor();
      }
      await page.locator('#tieba-panel').screenshot({path:path.join(out,'panel-'+width+'.png')});
      const panelOverflow=await page.locator('#tieba-panel').evaluate(e=>e.scrollWidth>e.clientWidth+3);
      if(overflow||panelOverflow||!unchanged||errors.length)throw Error(JSON.stringify({width,overflow,panelOverflow,unchanged,errors}));
      results.push({width,draft_survived_refresh:true,meme_saved:true,master_switch:true,subswitches_preserved:unchanged,overflow,panelOverflow,errors});
      await page.unrouteAll({behavior:'ignoreErrors'});await page.close();
    }
    fs.writeFileSync(path.join(out,'ui-acceptance.json'),JSON.stringify(results,null,2));console.log(JSON.stringify(results));
  }finally{if(browser)await browser.close();fixture.kill();fs.closeSync(log);}
})().catch(e=>{console.error(e);process.exitCode=1;});
