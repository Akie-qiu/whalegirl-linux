// Live GETs only. All deletion/configuration writes are browser-local mocks.
const cp = require('child_process');
const {chromium} = require('C:/Users/bgya7/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async () => {
  const access = cp.execFileSync('ssh', ['-o','BatchMode=yes','root@120.46.186.50',
    'cat /var/lib/whalegirl-linux/core/admin.token'], {encoding:'utf8',windowsHide:true}).trim();
  const tunnel = cp.spawn('ssh',['-N','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes',
    '-L','127.0.0.1:18109:127.0.0.1:8092','root@120.46.186.50'],{stdio:'ignore',windowsHide:true});
  let browser;
  try {
    browser = await chromium.launch({headless:true,
      executablePath:'C:/Users/bgya7/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe'});
    const results=[];
    for (const width of [1280,390]) {
      const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];
      let hiddenId='',deleted=0,configWrites=0,accept=false;
      page.on('pageerror',e=>errors.push(e.message));
      page.on('dialog',dialog=>accept?dialog.accept():dialog.dismiss());
      await page.route('**/api/**',async route=>{
        const request=route.request(),url=request.url();
        const headers={...request.headers(),Origin:'http://127.0.0.1'};
        if(request.method()==='POST'){
          if(url.endsWith('/api/images/config')){
            configWrites++;
            const response=await page.request.get('http://127.0.0.1:18109/api/config',
              {headers:{Authorization:'Bearer '+access,Origin:'http://127.0.0.1'}});
            await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({...await response.json(),...request.postDataJSON().values})});
          }else if(/\/assets\/[a-f0-9]{64}\/delete$/.test(url)){
            hiddenId=request.postDataJSON().confirm_id;deleted++;
            await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({state:'deleted',error:''})});
          }else await route.abort();
          return;
        }
        try {
          const response=await route.fetch({headers});
          if(url.endsWith('/api/images')){
            const state=await response.json();
            state.assets=state.assets.filter(a=>a.id!==hiddenId);
            await route.fulfill({response,body:JSON.stringify(state)});
          }else if(url.endsWith('/deletion')){
            const plan=await response.json();
            await route.fulfill({response,body:JSON.stringify({...plan,blocked:''})});
          }else await route.fulfill({response});
        }catch{await route.abort().catch(()=>{});}
      });
      await page.addInitScript(value=>sessionStorage.setItem('whalegirl-access',value),access);
      await page.goto('http://127.0.0.1:18109/#images-panel');
      await page.waitForFunction(()=>window.pictureLibrary&&typeof cfg!=='undefined'&&cfg);
      await page.evaluate(()=>window.pictureLibrary.load());
      const panel=page.locator('#images-panel'),retention=panel.locator('.picture-retention');
      await retention.locator('summary').click();
      if(await retention.getByRole('spinbutton').count()!==3)throw Error('Retention inputs missing');
      await retention.getByRole('spinbutton',{name:'贴吧素材保留天数'}).fill('7');
      await retention.getByRole('button',{name:'保存保留天数'}).click();
      if(configWrites)throw Error('Cancelled retention confirmation wrote configuration');
      accept=true;
      await retention.getByRole('button',{name:'保存保留天数'}).click();
      await page.waitForFunction(()=>[...document.querySelectorAll('.picture-retention button')].some(b=>b.textContent==='保存保留天数'&&!b.disabled));
      if(configWrites!==1)throw Error('Retention save not submitted');
      await panel.getByRole('tab',{name:'贴吧素材',exact:true}).click();
      const before=await panel.locator('.picture-card').count();
      if(!before)throw Error('Fixture source unavailable');
      accept=false;
      await panel.getByRole('button',{name:'删除图片',exact:true}).first().click();
      await page.waitForFunction(()=>[...document.querySelectorAll('#images-panel .picture-delete')].every(b=>!b.disabled));
      if(deleted)throw Error('Cancelled deletion was submitted');
      accept=true;
      await panel.getByRole('button',{name:'删除图片',exact:true}).first().click();
      await page.waitForFunction(n=>document.querySelectorAll('#images-panel .picture-card').length===n,before-1);
      if(deleted!==1)throw Error('Delete mock not submitted');
      const overflow=await panel.evaluate(e=>e.scrollWidth>e.clientWidth+2);
      if(errors.length||overflow)throw Error(JSON.stringify({errors,overflow}));
      results.push({width,retentionFields:3,mockedConfigWrites:configWrites,mockedDeletes:deleted,cardsRemoved:1,overflow,errors});
      await page.close();
    }
    console.log(JSON.stringify(results));
  }finally{if(browser)await browser.close();tunnel.kill();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
