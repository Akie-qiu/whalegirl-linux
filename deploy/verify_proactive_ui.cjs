// Read-only live UI check. Configuration POSTs are fulfilled in the browser;
// they never reach the server and never enable proactive contact.
const cp = require('child_process');
const {chromium} = require('C:/Users/bgya7/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

(async () => {
  const access = cp.execFileSync('ssh', ['-o','BatchMode=yes','root@120.46.186.50',
    'cat /var/lib/whalegirl-linux/core/admin.token'], {encoding:'utf8', windowsHide:true}).trim();
  const tunnel = cp.spawn('ssh', ['-N','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes',
    '-L','127.0.0.1:18108:127.0.0.1:8092','root@120.46.186.50'], {stdio:'ignore', windowsHide:true});
  let browser;
  try {
    browser = await chromium.launch({headless:true,
      executablePath:'C:/Users/bgya7/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe'});
    const results = [];
    for (const width of [1280, 390]) {
      if (tunnel.exitCode !== null) throw Error('UI tunnel unavailable');
      const page = await browser.newPage({viewport:{width, height:1000}});
      const errors = [];
      let submitted = null;
      page.on('pageerror', error => errors.push(error.message));
      await page.route('http://127.0.0.1:18108/api/**', async route => {
        const request = route.request();
        if (request.method() === 'POST' && request.url().endsWith('/api/proactive/config')) {
          submitted = request.postDataJSON().values;
          const response = await page.request.get('http://127.0.0.1:18108/api/config',
            {headers:{Authorization:'Bearer '+access, Origin:'http://127.0.0.1'}});
          const config = await response.json();
          await route.fulfill({status:200, contentType:'application/json', body:JSON.stringify({...config, ...submitted})});
          return;
        }
        if (request.method() !== 'GET') { await route.abort(); return; }
        try {
          const response = await route.fetch({headers:{...request.headers(), Origin:'http://127.0.0.1'}});
          await route.fulfill({response});
        } catch { await route.abort().catch(() => {}); }
      });
      await page.addInitScript(value => sessionStorage.setItem('whalegirl-access', value), access);
      await page.goto('http://127.0.0.1:18108/#proactive-panel');
      try {
        await page.waitForFunction(() => document.getElementById('proactive-per-day')?.value === '12', null, {timeout:10000});
      } catch {
        const diagnostic = await page.evaluate(() => ({
          control:!!document.getElementById('proactive-per-day'),
          value:document.getElementById('proactive-per-day')?.value,
          module:!!window.proactiveChat, config:typeof cfg !== 'undefined' && !!cfg,
          defaultValue:typeof cfg !== 'undefined' && cfg?.proactive_per_day,
          authenticated:document.getElementById('auth')?.hidden
        }));
        throw Error(JSON.stringify({diagnostic, errors}));
      }
      const panel = page.locator('#proactive-panel');
      if (!(await panel.isVisible())) throw Error('Autonomy panel is hidden');
      const overflow = await panel.evaluate(e => e.scrollWidth > e.clientWidth + 3);
      const limits = {};
      for (const name of ['chat-per-day','cooldown-minutes','idle-minutes']) {
        limits[name] = await page.locator('#proactive-'+name).inputValue();
      }
      if (Object.values(limits).some(v => v !== '0')) throw Error('Optional pacing limits unexpectedly enabled');
      if (await page.locator('#proactive-enabled').isChecked()) throw Error('Autonomy unexpectedly enabled on server');
      await page.locator('#save-proactive').click();
      await page.waitForFunction(() => document.getElementById('notice')?.textContent.includes('自主聊天设置已保存'));
      if (!submitted || submitted.proactive_enabled || submitted.proactive_chat_ids.length) throw Error('Default save changed permissions');
      if (overflow || errors.length) throw Error(JSON.stringify({width, overflow, errors}));
      results.push({width, overflow, optional_limits_disabled:true, config_save_simulated:true, browser_errors:errors.length});
      await page.unrouteAll({behavior:'ignoreErrors'});
      await page.close();
    }
    console.log(JSON.stringify(results));
  } finally {
    if (browser) await browser.close();
    tunnel.kill();
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
