// Live GETs; all setting mutations are browser-local mocks. No chat is sent.
const cp=require('child_process'),fs=require('fs'),path=require('path');
const {chromium}=require('C:/Users/bgya7/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
  const out=path.resolve('artifacts/tieba-tools-ui-20261025');fs.mkdirSync(out,{recursive:true});
  const token=cp.execFileSync('ssh',['-o','BatchMode=yes','root@120.46.186.50','cat /var/lib/whalegirl-linux/core/admin.token'],{encoding:'utf8',windowsHide:true}).trim();
  const tunnel=cp.spawn('ssh',['-N','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-L','127.0.0.1:18112:127.0.0.1:8092','root@120.46.186.50'],{stdio:'ignore',windowsHide:true});
  let browser;
  try{
    browser=await chromium.launch({headless:true,executablePath:'C:/Users/bgya7/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe'});
    const reports=[];
    for(const width of (process.argv.includes('--inspect')||process.argv.includes('--mobile')?[390]:[1280,390])){
      const page=await browser.newPage({viewport:{width,height:950}}),errors=[];let config=null,saved=null;
      page.on('pageerror',e=>errors.push(e.message));
      await page.route('**/api/**',async route=>{
        const req=route.request(),url=new URL(req.url());
        if(req.method()==='POST'){
          if(url.pathname==='/api/tieba/config'){
            saved=req.postDataJSON();config={...config,...saved.values};
            await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(config)});
          }else if(url.pathname.startsWith('/api/tieba/memes/')){
            saved=req.postDataJSON();await route.fulfill({status:200,contentType:'application/json',body:'{"ok":true}'});
          }else await route.abort();
          return;
        }
        try{
          const response=await route.fetch({headers:{...req.headers(),Origin:'http://127.0.0.1'}});
          if(url.pathname==='/api/config'){
            config=config||await response.json();await route.fulfill({response,body:JSON.stringify(config)});
          }else await route.fulfill({response});
        }catch{await route.abort().catch(()=>{});}
      });
      await page.addInitScript(value=>sessionStorage.setItem('whalegirl-access',value),token);
      await page.goto('http://127.0.0.1:18112/#tieba-panel');
      try{await page.waitForFunction(()=>typeof cfg!=='undefined'&&cfg&&document.querySelector('#tieba-archives .record'));}
      catch(e){
        await page.screenshot({path:path.join(out,'load-failure-'+width+'.png')});
        const diag=await page.evaluate(()=>({config:typeof cfg!=='undefined'&&!!cfg,tieba:!!window.tieba,
          archiveRows:document.querySelectorAll('#tieba-archives .record').length,
          notice:document.querySelector('#notice')?.textContent,auth:document.querySelector('#auth-error')?.textContent}));
        throw Error(JSON.stringify({stage:'load',width,diag,errors}));
      }
      const panel=page.locator('#tieba-panel');await panel.scrollIntoViewIfNeeded();
      if(process.argv.includes('--inspect')){
        console.log(JSON.stringify(await page.evaluate(()=>({inner:innerWidth,root:document.documentElement.scrollWidth,body:document.body.scrollWidth,
          panel:{width:document.querySelector('#tieba-panel').clientWidth,scroll:document.querySelector('#tieba-panel').scrollWidth},
          wide:[...document.querySelectorAll('body *')].filter(e=>e.getClientRects().length&&e.scrollWidth>e.clientWidth+8).slice(0,30).map(e=>({tag:e.tagName,id:e.id,cls:e.className,width:e.clientWidth,scroll:e.scrollWidth,overflow:getComputedStyle(e).overflowX}))}))));
        break;
      }
      const levels=['tieba_meme_eagerness','tieba_recall_eagerness','tieba_share_eagerness'];
      for(const key of levels)await page.locator('#tieba-'+key).fill('88');
      const shareBefore=await page.locator('#tieba-tieba_share_enabled').isChecked();
      await panel.getByRole('button',{name:'保存贴吧设置',exact:true}).click();
      await page.waitForFunction(()=>document.querySelector('#notice')?.textContent?.includes('贴吧设置已保存'));
      if(!saved||levels.some(k=>saved.values[k]!==88)||saved.values.tieba_share_enabled!==shareBefore)throw Error('Setting payload mismatch');
      await panel.getByRole('searchbox',{name:'搜索贴吧原文',exact:true}).fill('绝区零');
      await panel.getByRole('button',{name:'搜索原文库',exact:true}).click();
      await panel.getByRole('button',{name:'读取原文',exact:true}).first().waitFor();
      await panel.getByRole('button',{name:'读取原文',exact:true}).first().click();
      await page.locator('dialog pre').first().waitFor();
      const original=await page.locator('dialog pre').first().textContent();
      const dialogOverflow=await page.locator('dialog').evaluate(e=>e.scrollWidth>e.clientWidth+3);
      await page.locator('dialog').getByRole('button',{name:'关闭',exact:true}).click();
      const memes=panel.locator('details').filter({has:page.locator('summary',{hasText:'学到的梗'})}).first();
      await memes.locator('summary').first().click();
      await panel.getByRole('button',{name:'编辑这个梗',exact:true}).first().click();
      await page.getByRole('textbox',{name:'来龙去脉与原文背景',exact:true}).fill('测试编辑背景，仅浏览器模拟');
      await page.getByRole('textbox',{name:'自然接话示例',exact:true}).fill('用于轻松接话');
      const editOverflow=await page.locator('dialog').evaluate(e=>e.scrollWidth>e.clientWidth+3);
      await page.locator('dialog').screenshot({path:path.join(out,'editor-'+width+'.png')});
      await page.locator('dialog').getByRole('button',{name:'保存修改',exact:true}).click();
      await page.locator('dialog').waitFor({state:'detached'});
      if(saved.background!=='测试编辑背景，仅浏览器模拟'||saved.examples!=='用于轻松接话')throw Error('Detail payload mismatch');
      await panel.getByText('闲置回收站与清理记录',{exact:true}).click();
      await panel.scrollIntoViewIfNeeded();
      const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+3);
      await page.screenshot({path:path.join(out,'panel-'+width+'.png')});
      if(overflow||dialogOverflow||editOverflow||!original||errors.length){
        const offenders=await page.evaluate(()=>[...document.querySelectorAll('body *')].filter(e=>e.getClientRects().length&&e.getBoundingClientRect().right>innerWidth+3).slice(0,12).map(e=>({tag:e.tagName,id:e.id,cls:e.className,right:Math.round(e.getBoundingClientRect().right)})));
        const dimensions=await page.evaluate(()=>({root:document.documentElement.scrollWidth,body:document.body.scrollWidth,inner:innerWidth,
          panel:document.querySelector('#tieba-panel').scrollWidth,
          wide:[...document.querySelectorAll('body *')].filter(e=>e.getClientRects().length&&e.scrollWidth>e.clientWidth+8).slice(0,18).map(e=>({id:e.id,cls:e.className,width:e.clientWidth,scroll:e.scrollWidth,overflow:getComputedStyle(e).overflowX}))}));
        throw Error(JSON.stringify({width,overflow,dialogOverflow,editOverflow,errors,offenders,dimensions}));
      }
      reports.push({width,settings:true,search:true,read_original:true,edit_background:true,share_switch_preserved:true,overflow:false,errors});
      await page.unrouteAll({behavior:'ignoreErrors'});await page.close();
    }
    fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(reports,null,2));console.log(JSON.stringify(reports));
  }finally{if(browser)await browser.close();tunnel.kill();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
