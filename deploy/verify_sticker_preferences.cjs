const fs=require('fs'),path=require('path');
const {chromium}=require('C:/Users/bgya7/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const out=path.resolve(process.argv[2]);
(async()=>{
  const fixture=JSON.parse(fs.readFileSync(path.join(out,'preview.json'),'utf8'));
  const base='http://127.0.0.1:'+fixture.port+'/';
  const browser=await chromium.launch({headless:true,executablePath:'C:/Users/bgya7/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe'});
  const results=[];
  try{
    for(const width of [1280,390]){
      const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      await page.route('**/*',r=>new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort());
      await page.addInitScript(t=>sessionStorage.setItem('whalegirl-access',t),fixture.token);
      await page.goto(base+'#proactive-panel');
      await page.waitForFunction(()=>!!document.querySelector('#sticker-only-eagerness')?.value);
      for(const [id,value] of [['sticker-text-pair-eagerness','95'],['sticker-only-eagerness','90'],['sticker-reaction-eagerness','95'],['sticker-max-per-reply','2']]){
        if(await page.locator('#'+id).count()!==1 || await page.locator('#'+id).evaluate(e=>e.closest('section').id)!=='proactive-panel')throw Error('Missing centralized control: '+id);
        await page.locator('#'+id).fill(value);
      }
      await page.locator('details').filter({has:page.locator('#sticker-style-note')}).locator('summary').click();
      const note='管理员整理：让表情承担语气，能接住时不用凑文字';
      await page.locator('#sticker-style-note').fill(note);
      const saved=page.waitForResponse(r=>r.url().endsWith('/api/proactive/config'));
      await page.getByRole('button',{name:'保存全部聊天设置',exact:true}).click();
      const response=await saved,config=await response.json();
      if(response.status()!==200||config.sticker_style_note!==note||config.sticker_only_eagerness!==90||config.sticker_max_per_reply!==2)throw Error('Preference save failed');
      const overflow=await page.locator('#proactive-panel').evaluate(e=>e.scrollWidth>e.clientWidth+3);
      if(overflow||errors.length)throw Error(JSON.stringify({width,overflow,errors}));
      await page.screenshot({path:path.join(out,'sticker-preferences-'+width+'.png'),fullPage:true});
      results.push({width,preferenceControls:true,editableReference:true,unifiedSave:true,overflow,errors});
      await page.close();
    }
    console.log(JSON.stringify(results));
    fs.writeFileSync(path.join(out,'verification.json'),JSON.stringify(results,null,2));
  }finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
