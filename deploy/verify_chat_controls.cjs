const fs=require('fs'),path=require('path');
const {chromium}=require('C:/Users/bgya7/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const out=path.resolve(process.argv[2]);
(async()=>{
  const f=JSON.parse(fs.readFileSync(path.join(out,'preview.json'),'utf8')),base='http://127.0.0.1:'+f.port+'/';
  const browser=await chromium.launch({headless:true,executablePath:'C:/Users/bgya7/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe'});
  const results=[];
  try{
    for(const width of [1280,390]){
      const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      await page.route('**/*',r=>new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort());
      await page.addInitScript(t=>sessionStorage.setItem('whalegirl-access',t),f.token);
      await page.goto(base+'#proactive-panel');
      await page.waitForFunction(()=>document.querySelector('#quiet-default-minutes')?.value==='30');
      for(const id of ['stickers-enabled','sticker-aggressiveness','reply-max-parts','reply-part-interval','style-sticker-cooldown']){
        if(await page.locator('#'+id).count()!==1 || await page.locator('#'+id).evaluate(e=>e.closest('section').id)!=='proactive-panel')throw Error('Scattered control: '+id);
      }
      await page.locator('#sticker-aggressiveness').selectOption('high');
      await page.locator('#quiet-default-minutes').fill('30');
      const saved=page.waitForResponse(r=>r.url().endsWith('/api/proactive/config'));
      await page.getByRole('button',{name:'保存全部聊天设置',exact:true}).click();
      if((await saved).status()!==200)throw Error('Consolidated save failed');
      const quiet=page.waitForResponse(r=>r.url().endsWith('/api/chat-control'));
      await page.getByRole('button',{name:'让此会话安静一会儿',exact:true}).click();
      if((await quiet).status()!==200)throw Error('Quiet action failed');
      const resume=page.waitForResponse(r=>r.url().endsWith('/api/chat-control'));
      await page.getByRole('button',{name:'恢复此会话聊天',exact:true}).click();
      if((await resume).status()!==200)throw Error('Resume action failed');
      const overflow=await page.locator('#proactive-panel').evaluate(e=>e.scrollWidth>e.clientWidth+3);
      if(overflow||errors.length)throw Error(JSON.stringify({width,overflow,errors}));
      await page.screenshot({path:path.join(out,'chat-controls-'+width+'.png'),fullPage:true});
      results.push({width,centralized:true,save:true,quiet:true,resume:true,overflow,errors});
      await page.close();
    }
    console.log(JSON.stringify(results));
    fs.writeFileSync(path.join(out,'chat-controls-verification.json'),JSON.stringify(results,null,2));
  }finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
