const fs=require('fs'),path=require('path'),cp=require('child_process');
const {chromium}=require('C:/Users/bgya7/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
  const access=cp.execFileSync('ssh',['-o','BatchMode=yes','root@120.46.186.50','cat /var/lib/whalegirl-linux/core/admin.token'],{encoding:'utf8',windowsHide:true}).trim();
  const tunnel=cp.spawn('ssh',['-N','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-L','127.0.0.1:18098:127.0.0.1:8092','root@120.46.186.50'],{stdio:'ignore',windowsHide:true});
  let browser;
  try{
    await new Promise(r=>setTimeout(r,1000));if(tunnel.exitCode!==null)throw Error('UI preview tunnel failed');
    browser=await chromium.launch({headless:true,executablePath:'C:/Users/bgya7/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe'});
    const results=[];
    for(const width of [1280,390]){
      const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      await page.route('http://127.0.0.1:18098/api/**',async route=>{
        if(route.request().method()!=='GET'){await route.abort();return;}
        try{const response=await route.fetch({headers:{...route.request().headers(),origin:'http://127.0.0.1'}});await route.fulfill({response});}
        catch{await route.abort().catch(()=>{});}
      });
      await page.addInitScript(access=>sessionStorage.setItem('whalegirl-access',access),access);
      await page.goto('http://127.0.0.1:18098/#habits-panel');
      await page.waitForFunction(()=>document.getElementById('style-banter_level')?.value==='normal');
      const names={banter_level:'玩梗程度',play_along:'顺着演的意愿',callback_frequency:'旧梗回忆频率',assertiveness:'主见程度'},values={};
      for(const [id,name] of Object.entries(names)){
        const control=page.locator('#style-'+id);values[name]=await control.inputValue();
        if(!(await control.locator('..').textContent()).startsWith(name))throw Error('Control label mismatch: '+id);
      }
      const section=page.locator('section').filter({has:page.getByRole('heading',{name:'接话与玩梗',exact:true})}).last();
      const overflow=await section.evaluate(e=>e.scrollWidth>e.clientWidth+3);
      const fillsRow=await section.evaluate(e=>e.clientWidth>=e.parentElement.clientWidth-4);
      await section.screenshot({path:path.join(__dirname,'..','artifacts','chat-personality','settings-'+width+'.png')});
      await page.locator('#memory-kind').selectOption('joke');
      const expiryVisible=await page.locator('#memory-joke-ttl').isVisible();
      await page.locator('#memory-joke-ttl').fill('7');
      const expiryValid=await page.locator('#memory-joke-ttl').evaluate(e=>e.checkValidity());
      await page.locator('#memory-kind').selectOption('fact');
      const hiddenForFact=!(await page.locator('#memory-joke-ttl').isVisible());
      if(overflow||!fillsRow||errors.length||!expiryVisible||!expiryValid||!hiddenForFact)throw Error(JSON.stringify({width,overflow,fillsRow,errors,expiryVisible,expiryValid,hiddenForFact}));
      results.push({width,values,overflow,fillsRow,errors,expiryVisible,expiryValid,hiddenForFact});
      await page.unrouteAll({behavior:'ignoreErrors'});await page.close();
    }
    fs.writeFileSync(path.join(__dirname,'..','artifacts','chat-personality','ui-acceptance.json'),JSON.stringify(results,null,2));
    console.log(JSON.stringify(results));
  }finally{if(browser)await browser.close();tunnel.kill();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
