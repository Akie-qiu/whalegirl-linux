const fs=require('fs');
const path=require('path');
const cp=require('child_process');
const {chromium}=require('C:/Users/bgya7/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
  const ssh=['-o','BatchMode=yes','-o','ConnectTimeout=10','root@120.46.186.50'];
  const access=cp.execFileSync('ssh',[...ssh,'cat /var/lib/whalegirl-linux/core/admin.token'],{encoding:'utf8'}).trim();
  const tunnel=cp.spawn('ssh',['-N','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-L','127.0.0.1:18095:127.0.0.1:8092','root@120.46.186.50'],{stdio:'ignore',windowsHide:true});
  let browser;
  try{
    await new Promise(r=>setTimeout(r,1300));
    browser=await chromium.launch({headless:true,executablePath:'C:/Users/bgya7/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe'});
    const results=[];
    for(const width of [1280,390]){
      const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      await page.addInitScript(access=>sessionStorage.setItem('whalegirl-access',access),access);
      await page.goto('http://127.0.0.1:18095/#images-panel');
      await page.locator('#images-panel').waitFor({state:'visible'});
      await page.waitForFunction(()=>window.pictureLibrary&&cfg&&cfg.image_model);
      await page.evaluate(()=>window.pictureLibrary.load());
      await page.locator('#images-panel button').filter({hasText:'生成图库'}).click();
      const generated=await page.locator('#images-panel .picture-card').count();
      await page.locator('#images-panel button').filter({hasText:'上传图库'}).click();
      const uploaded=await page.locator('#images-panel .picture-card').count();
      await page.evaluate(()=>{location.hash='#moments-panel';});
      await page.getByRole('button',{name:'从两个图库选择并排列固定配图',exact:true}).click();
      const dialog=page.locator('dialog.picture-dialog');await dialog.waitFor({state:'visible'});
      await dialog.getByRole('button',{name:'上传图库',exact:true}).click();
      const first=dialog.getByRole('button',{name:'选择',exact:true}).first();
      if(await first.count())await first.click();
      await dialog.getByRole('button',{name:'生成图库',exact:true}).click();
      const second=dialog.getByRole('button',{name:'选择',exact:true}).first();
      if(await second.count())await second.click();
      const mixedCount=await dialog.locator('.picture-selection>span').count();
      if(uploaded&&generated&&mixedCount!==2)throw Error('Mixed library selection failed');
      const overflow=await dialog.evaluate(e=>e.scrollWidth>e.clientWidth+2);
      if(overflow||errors.length)throw Error(JSON.stringify({overflow,errors}));
      await dialog.screenshot({path:path.join(__dirname,'..','artifacts','pictures-picker-'+width+'.png')});
      await dialog.getByRole('button',{name:'取消',exact:true}).click();
      results.push({width,uploaded,generated,mixedCount,errors});await page.close();
    }
    console.log(JSON.stringify(results));
  }finally{if(browser)await browser.close();tunnel.kill();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
