const fs=require('fs');
const path=require('path');
const cp=require('child_process');
const {chromium}=require('C:/Users/bgya7/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
  const ssh=['-o','BatchMode=yes','-o','ConnectTimeout=10','root@120.46.186.50'];
  const access=cp.execFileSync('ssh',[...ssh,'cat /var/lib/whalegirl-linux/core/admin.token'],{encoding:'utf8'}).trim();
  const tunnel=cp.spawn('ssh',['-N','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-L','127.0.0.1:18096:127.0.0.1:8092','root@120.46.186.50'],{stdio:'ignore',windowsHide:true});
  let browser,currentPage,stage='starting';
  try{
    await new Promise(r=>setTimeout(r,1100));
    if(tunnel.exitCode!==null)throw Error('SSH preview tunnel failed');
    browser=await chromium.launch({headless:true,executablePath:'C:/Users/bgya7/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe'});
    const results=[];
    for(const width of [1280,390]){
      const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];
      currentPage=page;stage='open-'+width;
      page.on('pageerror',e=>errors.push(e.message));
      // The private test tunnel has a different port from the permitted console origin.
      await page.route('http://127.0.0.1:18096/api/**',async route=>{
        try{
          const response=await route.fetch({headers:{...route.request().headers(),origin:'http://127.0.0.1'}});
          await route.fulfill({response});
        }catch{await route.abort().catch(()=>{});}
      });
      await page.addInitScript(access=>sessionStorage.setItem('whalegirl-access',access),access);
      await page.goto('http://127.0.0.1:18096/#images-panel');
      await page.locator('.reference-versions').waitFor({state:'visible'});
      stage='versions-'+width;
      await page.waitForFunction(()=>document.querySelectorAll('.reference-version').length===3);
      const versions=await page.locator('.reference-version').count();
      const selected=await page.locator('.reference-version.active').getAttribute('data-variant');
      if(selected!=='girl')throw Error('Default variant is not girl');
      if(width===1280){
        stage='switch-mature';
        await page.locator('.reference-version[data-variant="mature"]').getByRole('button',{name:'设为默认',exact:true}).click();
        await page.waitForFunction(()=>document.querySelector('.reference-version.active')?.dataset.variant==='mature');
        stage='switch-girl';
        await page.locator('.reference-version[data-variant="girl"]').getByRole('button',{name:'设为默认',exact:true}).click();
        await page.waitForFunction(()=>document.querySelector('.reference-version.active')?.dataset.variant==='girl');
      }
      const gallery=page.locator('.reference-gallery');
      stage='reference-previews-'+width;
      await page.waitForFunction(()=>document.querySelectorAll('.reference-gallery img').length===3&&[...document.querySelectorAll('.reference-gallery img')].every(i=>i.complete&&i.naturalWidth>0));
      const count=await gallery.locator('.picture-card').count();
      const panel=page.locator('#images-panel');
      const overflow=await panel.evaluate(e=>e.scrollWidth>e.clientWidth+3);
      if(versions!==3||count!==3||overflow||errors.length)throw Error(JSON.stringify({versions,count,overflow,errors}));
      const details=page.locator('details.record').filter({has:page.getByText('角色与参考图',{exact:true})});
      await details.screenshot({path:path.join(__dirname,'..','artifacts','references-'+width+'.png')});
      results.push({width,versions,defaultVariant:'girl',girlReferences:count,overflow,errors});
      await page.unrouteAll({behavior:'ignoreErrors'});
      await page.close();
    }
    console.log(JSON.stringify(results));
  }catch(e){
    const diagnostic=currentPage?await currentPage.evaluate(()=>({active:document.querySelector('.reference-version.active')?.dataset.variant,
      previews:[...document.querySelectorAll('.reference-gallery img')].map(i=>({loaded:i.complete,width:i.naturalWidth})),
      notice:document.getElementById('notice')?.textContent})).catch(()=>({})):{};
    console.error(JSON.stringify({stage,diagnostic}));throw e;
  }finally{if(browser)await browser.close();tunnel.kill();}
})().catch(e=>{console.error(e.message.replace(/Bearer\s+[A-Za-z0-9._-]+/g,'Bearer [redacted]'));process.exitCode=1;});
