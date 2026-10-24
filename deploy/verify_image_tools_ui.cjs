const fs=require('fs'),path=require('path'),cp=require('child_process');
const {chromium}=require('C:/Users/bgya7/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
  const access=cp.execFileSync('ssh',['-o','BatchMode=yes','root@120.46.186.50','cat /var/lib/whalegirl-linux/core/admin.token'],{encoding:'utf8',windowsHide:true}).trim();
  const tunnel=cp.spawn('ssh',['-N','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-L','127.0.0.1:18097:127.0.0.1:8092','root@120.46.186.50'],{stdio:'ignore',windowsHide:true});
  let browser;
  try{
    await new Promise(r=>setTimeout(r,1000));if(tunnel.exitCode!==null)throw Error('Preview tunnel failed');
    browser=await chromium.launch({headless:true,executablePath:'C:/Users/bgya7/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe'});
    const results=[];
    for(const width of [1280,390]){
      const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      await page.route('http://127.0.0.1:18097/api/**',async route=>{try{const response=await route.fetch({headers:{...route.request().headers(),origin:'http://127.0.0.1'}});await route.fulfill({response});}catch{await route.abort().catch(()=>{});}});
      await page.addInitScript(access=>sessionStorage.setItem('whalegirl-access',access),access);
      await page.goto('http://127.0.0.1:18097/#images-panel');
      await page.waitForFunction(()=>document.querySelectorAll('.reference-version').length===3);
      await page.waitForFunction(()=>{const imgs=[...document.querySelectorAll('.reference-versions img,.reference-gallery img')];return imgs.length>=4&&imgs.every(i=>i.complete&&i.naturalWidth>0);});
      await page.getByText('聊天中的自身外观设定',{exact:true}).click();
      const identity=await page.getByLabel('共同身份外观').inputValue(),variant=await page.getByLabel('当前版本外观文字').inputValue();
      const scopes=await page.getByLabel('素材使用范围',{exact:true}).count();
      const budget=await page.getByText(/北京时间今日生图：已提交/).textContent();
      const panel=page.locator('#images-panel');const overflow=await panel.evaluate(e=>e.scrollWidth>e.clientWidth+3);
      const result={width,identityPresent:!!identity,variantPresent:!!variant,scopeEditors:scopes,budgetVisible:!!budget,overflow,errors};
      if(!identity||!variant||!scopes||!budget||overflow||errors.length)throw Error(JSON.stringify(result));
      const reference=page.locator('details.record').filter({has:page.getByText('角色与参考图',{exact:true})});
      await reference.screenshot({path:path.join(__dirname,'..','artifacts','image-tools-ui-'+width+'.png')});
      results.push(result);await page.unrouteAll({behavior:'ignoreErrors'});await page.close();
    }
    fs.writeFileSync(path.join(__dirname,'..','artifacts','image-tools-ui-acceptance.json'),JSON.stringify(results,null,2));
    console.log(JSON.stringify(results));
  }finally{if(browser)await browser.close();tunnel.kill();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
