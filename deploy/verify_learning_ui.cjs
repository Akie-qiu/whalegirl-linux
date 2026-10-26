// Local fixture only. All changes affect its synthetic data.
'use strict';
const fs = require('fs'), path = require('path'), assert = require('assert/strict');
const {chromium} = require('C:/Users/bgya7/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async () => {
  const output = path.resolve('artifacts/learning-ui-20261026');
  fs.mkdirSync(output, {recursive:true});
  const token = fs.readFileSync(path.join(process.argv[2], 'admin.token'), 'utf8').trim();
  const browser = await chromium.launch({headless:true, executablePath:
    'C:/Users/bgya7/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe'});
  try {
    const reports = [];
    for (const width of [1280, 390]) {
      const page = await browser.newPage({viewport:{width, height:950}}), errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.route('**/api/**', async route => {
        const response = await route.fetch({
          headers:{...route.request().headers(), Origin:'http://127.0.0.1'}});
        await route.fulfill({response});
      });
      await page.addInitScript(value => sessionStorage.setItem('whalegirl-access', value), token);
      await page.goto('http://127.0.0.1:18118/#learning-panel');
      const panel = page.locator('#learning-panel');
      await panel.locator('article.record').first().waitFor();
      await page.locator('#learning-eagerness').fill('83');
      await page.locator('#learning-save').click();
      await page.waitForFunction(() => document.querySelector('#notice').textContent.includes('学习设置已保存'));
      assert.equal(await page.locator('#learning-eagerness').inputValue(), '83');
      await panel.getByRole('button', {name:'详情与编辑', exact:true}).first().click();
      await page.locator('#learning-content').fill('喜欢猫，也喜欢安静地聊天');
      await page.locator('#learning-edit-save').click();
      await page.waitForFunction(() => !document.querySelector('#learning-editor').open);
      await panel.getByRole('button', {name:'采纳', exact:true}).first().click();
      await panel.getByText(/已采纳 · 可召回/).first().waitFor();
      await panel.getByRole('button', {name:'详情与编辑', exact:true}).first().click();
      const editor = page.locator('#learning-editor');
      await editor.waitFor({state:'visible'});
      assert.equal(await editor.locator('#learning-history details').count() >= 3, true);
      assert.equal(await editor.evaluate(e => e.scrollWidth > e.clientWidth+3), false);
      await editor.screenshot({path:path.join(output, 'editor-'+width+'.png')});
      await page.locator('#learning-close').click();
      await panel.scrollIntoViewIfNeeded();
      await panel.screenshot({path:path.join(output, 'panel-'+width+'.png')});
      assert.equal(await panel.evaluate(e => e.scrollWidth > e.clientWidth+3), false);
      assert.deepEqual(errors, []);
      const nav = page.locator('.nav-group', {has:page.locator('a[href="#learning-panel"]')});
      assert.equal(await nav.count(), 1);
      reports.push({width, settings:true, edit:true, accept:true, history:true, panel_overflow:false, errors});
      await page.close();
    }
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(reports, null, 2));
    console.log(JSON.stringify(reports));
  } finally {
    await browser.close();
  }
})().catch(e => {console.error(e.stack); process.exitCode=1;});
