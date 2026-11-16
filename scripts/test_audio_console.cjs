/* Local Playwright acceptance. Never reads production settings or submits paid audio. */
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');
const {chromium} = require('C:/Users/bgya7/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const origin = 'http://127.0.0.1:18118';
const directory = path.resolve(__dirname, '../artifacts/audio-implementation-20261108');

(async () => {
  const browser = await chromium.launch({headless: true, executablePath: 'C:/Users/bgya7/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe'});
  const results = [];
  try {
    for (const width of [1280, 390]) {
      const page = await browser.newPage({viewport: {width, height: 1000}});
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
      await page.addInitScript(() => sessionStorage.setItem('whalegirl-access', 'audio-ui-local-only'));
      await page.goto(origin + '/#audio-console');
      await page.waitForFunction(() => document.getElementById('audio-state')?.textContent.includes('v'));
      await page.locator('#audio-console').waitFor({state: 'visible'});
      const panel = page.locator('#audio-console');
      assert.equal(await page.locator('#audio-speech-url').inputValue(), 'https://speech.example');
      await page.locator('#audio-music-url').fill('https://music-custom.example');
      await page.locator('#audio-music-key').fill('synthetic-music-draft');
      await page.locator('#audio-music-profile').selectOption('music-pro');
      assert.equal(await page.locator('#audio-music-url').inputValue(), 'https://pro.example');
      await page.locator('#audio-music-profile').selectOption('music');
      assert.equal(await page.locator('#audio-music-url').inputValue(), 'https://music-custom.example');
      assert.equal(await page.locator('#audio-music-key').inputValue(), 'synthetic-music-draft');

      // Hold a save response, then type another key while the request is running.
      let release, submitted;
      const blocked = new Promise(resolve => { release = resolve; });
      const arrived = new Promise(resolve => { submitted = resolve; });
      await page.route('**/api/audio/config', async route => {
        if (route.request().method() !== 'POST') return route.continue();
        const response = await route.fetch();
        submitted();
        await blocked;
        await route.fulfill({response});
      }, {times: 1});
      await page.locator('#audio-speech-key').fill('synthetic-before-save');
      await page.locator('#audio-save').click();
      await arrived;
      await page.locator('#audio-speech-key').fill('synthetic-new-during-save');
      release();
      await page.waitForFunction(() => !document.getElementById('audio-save').disabled);
      assert.equal(await page.locator('#audio-speech-key').inputValue(), 'synthetic-new-during-save');
      // The changed input is retained and the following automatic save commits it.
      await page.waitForFunction(() => document.getElementById('audio-speech-key').value === '');

      await page.locator('#audio-new-preset').click();
      const preset = await page.locator('#audio-preset').inputValue();
      await page.locator('#audio-preset-name').fill('本地验收声线');
      await page.locator('#audio-speed').evaluate(element => {element.value = '1.12'; element.dispatchEvent(new Event('input', {bubbles: true}));});
      await page.locator('#audio-default-preset').click();
      await page.waitForFunction(() => document.getElementById('audio-save-state').textContent.startsWith('已保存'));
      await page.locator('#audio-refresh').click();
      assert.equal(await page.locator('#audio-preset-name').inputValue(), '本地验收声线');
      // Reset speed to 1 because this fixture intentionally has no FFmpeg on PATH.
      await page.locator('#audio-reset-tuning').click();
      await page.waitForFunction(() => document.getElementById('audio-save-state').textContent.startsWith('已保存'));
      const state = await page.evaluate(async () => (await fetch('/api/audio', {headers: {Authorization: 'Bearer audio-ui-local-only'}})).json());
      assert.equal(state.settings.default_preset, preset);
      assert.equal(state.settings.presets[preset].name, '本地验收声线');

      const prior = await page.evaluate(async () => (await (await fetch('/api/audio', {headers: {Authorization: 'Bearer audio-ui-local-only'}})).json()).jobs[0]?.id || '');
      await page.locator('#audio-preview').click();
      let audition;
      const auditionDeadline = Date.now() + 10000;
      while (!audition && Date.now() < auditionDeadline) {
        const state = await page.evaluate(async () => {
          const response = await fetch('/api/audio', {headers: {Authorization: 'Bearer ' + sessionStorage.getItem('whalegirl-access')}});
          if (!response.ok) throw new Error('Audio fixture state HTTP ' + response.status);
          return response.json();
        });
        audition = state.jobs.find(job => job.id !== prior && job.destination === 'preview' && job.kind === 'speech' && job.state === 'ready' && job.playable && job.duration > 0);
        if (!audition) await new Promise(resolve => setTimeout(resolve, 150));
      }
      assert.ok(audition);
      await page.locator('#audio-refresh').click();
      const auditionCard = page.locator('#audio-jobs .audio-job').filter({hasText: audition.id});
      await auditionCard.getByRole('button', {name: '播放'}).click();
      await page.waitForFunction(() => document.getElementById('audio-player').src.startsWith('blob:'));
      const media = await page.evaluate(async job => {
        const response = await fetch('/api/audio/jobs/' + job.id + '/media');
        return {unauthenticated: response.status, destination: job.destination, duration: job.duration};
      }, audition);
      assert.equal(media.unauthenticated, 401);
      assert.equal(media.destination, 'preview');
      assert.ok(media.duration > 0);

      // A forced reload requested during a running refresh must run afterward.
      let releaseFirstRefresh, markFirstRefresh, markSecondRefresh;
      const refreshGate = new Promise(resolve => { releaseFirstRefresh = resolve; });
      const firstRefresh = new Promise(resolve => { markFirstRefresh = resolve; });
      const secondRefresh = new Promise(resolve => { markSecondRefresh = resolve; });
      let refreshRequests = 0;
      await page.route('**/api/audio', async route => {
        if (route.request().method() !== 'GET') return route.continue();
        refreshRequests++;
        if (refreshRequests === 1) {
          const response = await route.fetch();
          markFirstRefresh();
          await refreshGate;
          await route.fulfill({response});
        } else {
          markSecondRefresh();
          await route.continue();
        }
      });
      await page.locator('#audio-refresh').click();
      await firstRefresh;
      await page.locator('#audio-reload').click();
      releaseFirstRefresh();
      await Promise.race([secondRefresh, new Promise((_, reject) => setTimeout(() => reject(new Error('reload requested during refresh was dropped')), 3000))]);
      await page.unroute('**/api/audio');

      const overflow = await panel.evaluate(element => element.scrollWidth > element.clientWidth + 3);
      assert.equal(overflow, false);
      assert.deepEqual(errors, []);
      await panel.screenshot({path: path.join(directory, 'console-' + width + '.png')});
      results.push({width, separateModels: true, inflightEditsPreserved: true, defaultPresetSaved: true, localPreview: true, mediaPrivate: true, queuedReloadDuringRefresh: refreshRequests >= 2, overflow, errors});
      await page.close();
    }
    fs.writeFileSync(path.join(directory, 'console-tests.json'), JSON.stringify(results, null, 2));
    console.log(JSON.stringify(results));
  } finally { await browser.close(); }
})().catch(error => {console.error(error.stack); process.exitCode = 1;});
