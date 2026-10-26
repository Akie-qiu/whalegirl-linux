'use strict';
const $ = id => document.getElementById(id);
let token = sessionStorage.getItem('whalegirl-access') || '';
let cfg = null, chats = [], selected = new Set(), modes = new Map();
let state = null, pollTimer = null, updating = false;
let qrShownAt = 0, lastQr = '', recordsSignature = '', noticeTimer;
let lastStabilityFetch = 0;
if ($('records-scope')) {
  $('records-scope').value = localStorage.getItem('whalegirl-records-scope') || 'all';
  $('records-scope').addEventListener('change', () => {
    localStorage.setItem('whalegirl-records-scope', $('records-scope').value);
    recordsSignature = ''; update();
  });
}
const fragment = new URLSearchParams(location.hash.slice(1));
if (fragment.has('access')) {
  token = fragment.get('access');
  history.replaceState(null, '', location.pathname);
}
function text(id, value) { $(id).textContent = value == null ? '' : String(value); }
function notice(message, error = false) {
  clearTimeout(noticeTimer);
  text('notice', message); $('notice').className = error ? 'error' : ''; $('notice').hidden = false;
  noticeTimer = setTimeout(() => { $('notice').hidden = true; }, error ? 9000 : 4000);
}
async function api(path, method = 'GET', body) {
  let response;
  try {
    const headers = { 'Content-Type': 'application/json' };
    if (path !== 'auth/login') headers.Authorization = 'Bearer ' + token;
    response = await fetch('/api/' + path, {
      method, headers,
      body: body === undefined ? undefined : JSON.stringify(body), cache: 'no-store',
      signal: AbortSignal.timeout(45000)
    });
  } catch (error) {
    throw new Error(error.name === 'TimeoutError' ? '请求超时，请稍后重试。' : '无法连接服务器，请重新运行一键入口，恢复 SSH 连接。');
  }
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = result.detail;
    const error = new Error(Array.isArray(detail) ? detail.map(x => x.loc.slice(1).join('.') + '：' + x.msg).join('；') : detail || '请求未完成，请重试。');
    error.status = response.status; throw error;
  }
  return result;
}
async function action(button, fn) {
  button.disabled = true;
  try { await fn(); } catch (error) { notice(error.message, true); }
  finally { button.disabled = false; }
}
function populateSettings() {
  if($('quick-affection-enabled'))$('quick-affection-enabled').checked=!!cfg.affection_enabled;
  selected = new Set(cfg.allowed_chat_ids);
  const ids = new Set([...cfg.allowed_chat_ids, ...(cfg.trigger_only_chat_ids || []), ...chats.filter(c => c.isGroup).map(c => c.id)]);
  modes = new Map([...ids].map(id => [id, (cfg.trigger_only_chat_ids || []).includes(id) || (!cfg.natural_group_replies && cfg.require_mention_or_keyword) ? 'trigger' : 'natural']));
  $('keywords').value = cfg.keywords.join('\n');
  $('stickers-enabled').checked = cfg.stickers_enabled;
  $('sticker-aggressiveness').value = cfg.sticker_aggressiveness || 'normal';
  $('sticker-aggressiveness').disabled = !cfg.stickers_enabled;
  $('quotes-enabled').checked = cfg.quote_replies_enabled;
  $('group-mentions-enabled').checked = cfg.group_mentions_enabled ?? true;
  $('global-memory').checked = cfg.global_memory;
  $('vision-enabled').checked = cfg.vision_enabled;
  $('context-count').value = cfg.context_messages;
  $('api-url').value = cfg.api_url; $('model').value = cfg.model;
  $('api-style').value = cfg.api_style || 'auto';
  $('reasoning-effort').value = cfg.reasoning_effort || 'default';
  $('model-stream-enabled').checked = cfg.model_stream_enabled ?? false;
  syncModelPreset();
  $('max-tokens').value = cfg.max_tokens;
  $('cooldown').value = cfg.chat_cooldown; $('send-interval').value = cfg.min_send_interval;
  $('quick-enabled').checked = cfg.enabled;
  $('safety-enabled').checked = cfg.safety_enabled ?? true;
  $('safety-send-limits-enabled').checked = cfg.safety_send_limits_enabled ?? true;
  for (const [name,fallback] of Object.entries({safety_per_minute:8,safety_per_hour:120,
      safety_chat_per_hour:40,safety_per_day:500,safety_duplicate_seconds:180})) {
    $(name.replaceAll('_','-')).value = cfg[name] ?? fallback;
  }
  text('key-status', cfg.api_key_configured ? '服务器已保存 API Key；留空不会覆盖。' : '尚未配置 API Key。');
  text('wechat-version', '微信固定版本 ' + cfg.expected_wechat_version);
  renderChats();
  if (window.habits) window.habits.populate();
  window.learning?.populate();
  window.improvements?.populate();
  window.affection?.populate();
  window.stickerLibrary?.populate();
  window.moments?.populate();
  window.pictureLibrary?.populate();
  window.tieba?.populate();
  window.proactiveChat?.populate();
  window.tokenUsage?.refresh();
}
const MODEL_PRESETS = {
  flash: { url: 'https://api.deepseek.com/chat/completions', model: 'deepseek-flash', style: 'openai' },
  pro: { url: 'https://api.deepseek.com/chat/completions', model: 'deepseek-v4-pro', style: 'openai' }
};
function syncModelPreset(force = '') {
  const url=$('api-url').value.trim().replace(/\/$/,'');
  const model=$('model').value.trim();
  const style=$('api-style').value;
  const responses=style==='responses'||(style==='auto'&&/\/responses$/i.test(url));
  const match=Object.entries(MODEL_PRESETS).find(([,preset])=>preset.url===url&&preset.model===model);
  const active=force||(responses?'responses':match?.[0])||'custom';
  for(const button of document.querySelectorAll('[data-model-preset]'))
    button.setAttribute('aria-pressed',String(button.dataset.modelPreset===active));
  text('api-style-hint',responses?
    '使用 Responses 协议；支持 /responses 完整地址或 /v1 基础地址。Token 上限包含推理消耗，过低时可能没有正文。':
    '自动识别会根据地址选择协议；中转站需提供对应接口。切换在保存后生效。');
}
for (const button of document.querySelectorAll('[data-model-preset]')) {
  button.addEventListener('click', () => {
    const name=button.dataset.modelPreset,preset=MODEL_PRESETS[name];
    if(preset){$('api-url').value=preset.url;$('model').value=preset.model;$('api-style').value=preset.style;$('reasoning-effort').value='default';}
    if(name==='responses'){
      $('api-style').value='responses';
      $('api-url').value=$('api-url').value.trim().replace(/\/(?:chat\/completions|messages)\/?$/i,'/responses');
    }
    if(name==='custom')$('api-style').value='auto';
    syncModelPreset(name);
    text('test-result','有未保存的模型修改，点击“保存模型”后生效');
    if(name==='custom')$('model').focus();
  });
}
for (const id of ['api-url','model','api-style','reasoning-effort','model-stream-enabled']) $(id).addEventListener('input', () => {
  syncModelPreset();
  text('test-result','有未保存的模型修改，点击“保存模型”后生效');
});
function chatName(id) { const c = chats.find(c => c.id === id); return c ? c.remark || c.name || id : id; }
function renderChats() {
  const query = $('chat-search').value.trim().toLocaleLowerCase();
  const known = new Map(chats.map(c => [c.id, c]));
  for (const id of selected) if (!known.has(id)) known.set(id, { id, name: id, isGroup: id.endsWith('@chatroom') });
  const rows = [...known.values()].filter(c => (c.remark || c.name || c.id).toLocaleLowerCase().includes(query))
    .sort((a, b) => Number(selected.has(b.id)) - Number(selected.has(a.id)) || (a.name || a.id).localeCompare(b.name || b.id, 'zh-CN'));
  const box = $('chatlist'); box.replaceChildren();
  if (!rows.length) { const p = document.createElement('p'); p.className = 'hint'; p.textContent = query ? '没有匹配的会话。' : '暂无会话，登录后点击刷新会话。'; box.append(p); }
  for (const chat of rows) {
    const row = document.createElement('div'); row.className = 'chat-row';
    const input = document.createElement('input'); input.type = 'checkbox'; input.checked = selected.has(chat.id);
    input.id = 'chat-' + chat.id; input.setAttribute('aria-label', '启用 ' + (chat.remark || chat.name || chat.id));
    const label = document.createElement('label'); label.htmlFor = input.id;
    const title = document.createElement('span'); title.className = 'chat-name'; title.textContent = chat.remark || chat.name || chat.id;
    const subtitle = document.createElement('span'); subtitle.className = 'chat-kind'; subtitle.textContent = chat.isGroup ? '群聊' : '私聊';
    label.append(title, subtitle); row.append(input, label);
    let mode;
    if (chat.isGroup) {
      mode = document.createElement('select'); mode.setAttribute('aria-label', (chat.name || chat.id) + ' 回复方式');
      for (const [value, name] of [['natural', '自然接话'], ['trigger', '仅唤醒回复']]) { const option = document.createElement('option'); option.value = value; option.textContent = name; mode.append(option); }
      if (!modes.has(chat.id)) modes.set(chat.id, (!cfg.natural_group_replies && cfg.require_mention_or_keyword) ? 'trigger' : 'natural');
      mode.value = modes.get(chat.id); mode.disabled = !input.checked;
      mode.onchange = () => { modes.set(chat.id, mode.value); text('chat-save-note', '有未保存的修改'); };
      row.append(mode);
    } else { const note = document.createElement('span'); note.className = 'private-note'; note.textContent = '收到消息时回复'; row.append(note); }
    input.onchange = () => {
      if (input.checked) selected.add(chat.id); else selected.delete(chat.id);
      if (mode) mode.disabled = !input.checked;
      text('chat-summary', '已启用 ' + selected.size + ' 个会话'); text('chat-save-note', '有未保存的修改');
    };
    box.append(row);
  }
  text('chat-summary', '已启用 ' + selected.size + ' 个会话 · 共 ' + known.size + ' 个');
}
async function refreshChats(silent = false) {
  try {
    chats = await api('gateway/chats');
    sessionStorage.setItem('whalegirl-chat-labels', JSON.stringify(chats.map(c => ({ id: c.id, name: c.name, remark: c.remark, isGroup: c.isGroup }))));
    renderChats();
    if (window.habits) window.habits.chatLabels();
    window.stickerLibrary?.refreshChatLabels();
    window.moments?.refreshChatLabels();
    window.pictureLibrary?.refreshChats();
    window.proactiveChat?.chatLabels();
      window.tieba?.refreshChatLabels();
    if (!silent) notice('会话列表已更新。');
  } catch (error) { renderChats(); if (!silent) throw error; }
}
async function saveSubset(values, apiKey = '') {
  const current = await api('config'); delete current.api_key_configured; delete current.image_api_key_configured;
  cfg = await api('config', 'POST', { settings: { ...current, ...values }, api_key: apiKey });
  await update();
  return cfg;
}
const recordLabels = { queued: '等待处理', generating: '生成中', merged: '已合并', handled: '图片任务已受理，发送结果见图片任务', sending: '等待微信服务器回执', sent: '已获微信服务器回执', partial: '部分已发送，其余失败或被拦截', failed: '未发送', uncertain: '需要核对', skipped: '已跳过', blocked: '本地保护拦截', ignored: '安静旁观', baseline: '历史基线' };
function renderRecords(rows) {
  const signature = JSON.stringify(rows);
  if (signature === recordsSignature) return;
  recordsSignature = signature; const box = $('records'); box.replaceChildren();
  if (!rows.length) { const p = document.createElement('p'); p.className = 'hint'; p.textContent = '尚无处理记录，新消息到达后会显示在这里。'; box.append(p); }
  for (const row of rows) {
    const article = document.createElement('article'); article.className = 'record';
    const top = document.createElement('div'); top.className = 'record-top';
    const who = document.createElement('strong'); who.textContent = row.sender + ' · ' + chatName(row.chat);
    const status = document.createElement('span'); status.textContent = (recordLabels[row.state] || row.state) + ' · ' + new Date(row.updated * 1000).toLocaleString('zh-CN', { hour12: false });
    top.append(who, status); article.append(top);
    for (const [content, isReply] of [[row.content, false], [row.reply, true]]) {
      if (!content) continue;
      if(isReply){
        const hint=document.createElement('p');hint.className='hint';
        hint.textContent=['sent','partial'].includes(row.state)?'已确认发送的内容':row.state==='uncertain'?'发送结果待核对的内容':'生成内容（请以发送状态为准）';
        article.append(hint);
      }
      const message = document.createElement('div'); message.className = 'message' + (isReply ? ' reply' : ''); message.textContent = content; article.append(message);
    }
    for(const item of row.actions || []){
      const detail=document.createElement('p');detail.className=item.state==='sent'?'hint':'error-text';
      detail.textContent=(item.kind==='sticker'?'表情包':'文字')+'：'+(recordLabels[item.state] || item.state)+(item.error?' · '+item.error:'');
      article.append(detail);
    }
    if (row.error) { const error = document.createElement('p'); error.className = ['ignored','baseline'].includes(row.state) ? 'hint' : 'error-text'; error.textContent = row.error; article.append(error); }
    if (row.state === 'uncertain') {
      const actions = document.createElement('div'); actions.className = 'row';
      for (const [label, received] of [['微信已收到', true], ['确认未发送', false]]) {
        const button = document.createElement('button'); button.className = 'secondary'; button.textContent = label;
        button.onclick = () => action(button, async () => { await api('review', 'POST', { key: row.key, received }); await update(); notice('已记录核对结果，不会自动重发。'); }); actions.append(button);
      }
      article.append(actions);
    }
    box.append(article);
  }
}
function renderState(s) {
  state = s;
  if (window.habits) window.habits.stateUpdated(s);
  window.learning?.stateUpdated(s);
  window.affection?.stateUpdated(s);
  window.stickerLibrary?.stateUpdated(s);
  window.proactiveChat?.refresh().catch(() => {});
  renderSafety(s.safety);
  const online = s.gateway.status === 'logged_in';
  const login = s.login || {};
  text('connection', online ? '已登录' : s.gateway.status === 'disconnected' ? '连接异常' : '已掉线');
  text('account', s.account_id || '等待微信确认登录');
  text('reply-status', s.paused_reason ? '已暂停' : s.enabled ? '已开启' : '已关闭');
  text('activity', s.processing || (s.enabled && online ? '正在监听已授权会话' : !online ? '微信重新登录后恢复' : '已停止自动回复'));
  if (!$('quick-enabled').disabled) $('quick-enabled').checked = s.enabled;
  text('side-status', online ? '微信在线' : '等待重新登录'); $('side-dot').className = 'online-dot' + (online ? ' online' : '');
  text('sync-status', '已同步 · ' + new Date().toLocaleTimeString('zh-CN', { hour12: false }));
  $('sync-status').className = 'pill online';
  text('sent-count', s.counts.sent || 0); text('queue-count', (s.counts.queued || 0) + (s.counts.generating || 0)); text('uncertain-count', s.counts.uncertain || 0);
  text('paused', s.paused_reason); text('last-error', s.last_error);
  $('runtime-alert').hidden = !s.paused_reason && !s.last_error;
  const memory = s.memory || {};
  text('memory-count', Number(memory.messages || 0).toLocaleString('zh-CN') + ' 条');
  text('memory-detail', (memory.archived_chats || 0) + ' 个会话归档 · ' + (memory.image_descriptions || 0) + ' 条图片描述');
  const timing = s.last_reply_timing || {};
  text('reply-timing', timing.total_seconds == null ? '—' : timing.total_seconds.toFixed(1) + ' 秒');
  const memoryRecall = timing.long_term_memory;
  const memoryNote = memoryRecall ? ' · 长期记忆 ' + memoryRecall.count + ' 条' + (memoryRecall.ids.length ? '（#' + memoryRecall.ids.join('、#') + '）' : '') : '';
  text('timing-detail', timing.total_seconds == null ? '等待下一次实际回复，文字和表情的耗时会分别统计。' : '发现 ' + (timing.detected_after_seconds || 0).toFixed(1) + 's · 上下文 ' + (timing.context_seconds || 0).toFixed(1) + 's · 模型 ' + (timing.model_seconds || 0).toFixed(1) + 's · 发送确认 ' + (timing.send_readback_seconds || 0).toFixed(1) + 's' + memoryNote);
  if(timing.first_reply_seconds!=null)$('timing-detail').append(document.createTextNode(' · 首条确认 '+timing.first_reply_seconds.toFixed(1)+'s'));
  const stickers = s.stickers || {};
  if (timing.quoted) $('timing-detail').append(document.createTextNode(' · 原生引用回复'));
  else if (timing.quote_fallback) $('timing-detail').append(document.createTextNode(' · 引用不可用，已直接回复'));
  text('sticker-status', (stickers.known || 0) + ' 个已标注 / ' + (stickers.available || 0) + ' 个可调用');
  text('usage', '累计模型请求 ' + (s.model_usage.model_requests || 0) + ' 次 · ' + Number(s.model_usage.total_tokens || 0).toLocaleString('zh-CN') + ' tokens');
  text('login-badge', online ? '连接正常' : login.state === 'starting' ? '正在获取' : '需要登录');
  $('login-badge').className = 'pill' + (online ? ' online' : ' warning');
  text('login-title', online ? '微信已连接，随时待命' : login.state === 'attention' ? '请查看微信提示' : login.state === 'phone_confirmation' ? '请在手机确认登录' : '扫码恢复微信连接');
  text('login-message', online ? '已保持当前登录。后续掉线时，在这里获取二维码重新登录即可。' : login.message || '点击下方按钮获取二维码，然后在手机微信确认。');
  $('login').disabled = online || login.state === 'starting';
  text('login', online ? '微信已登录' : login.state === 'starting' ? '正在获取登录请求…' : login.state === 'phone_confirmation' ? '重新发起手机确认' : '获取 / 刷新二维码');
  const qr = !online && /^data:image\/(png|jpeg|webp);base64,/.test(login.qrDataUrl || '') ? login.qrDataUrl : '';
  if (qr && qr !== lastQr) { $('qr').src = qr; qrShownAt = Date.now(); }
  lastQr = qr; $('qr').hidden = !qr; $('qr-placeholder').hidden = !!qr;
  $('qr-placeholder').querySelector('strong').textContent = online ? '微信已登录' : login.state === 'starting' ? '正在获取登录请求' : login.state === 'phone_confirmation' ? '等待手机确认' : '等待扫码连接';
  text('qr-age', qr ? '已显示 ' + Math.floor((Date.now() - qrShownAt) / 1000) + ' 秒；如手机提示过期，请刷新。' : '');
}
function renderSafety(risk) {
  if (!risk) return;
  text('safety-status',risk.hold_reason ? '已暂停发送' : !risk.enabled ? '保护已关闭' : risk.rate_limited ? '正在限流' : '本地保护已启用');
  $('safety-status').className = 'pill' + (risk.hold_reason || risk.rate_limited || !risk.enabled ? ' warning' : ' online');
  const limits = risk.limits, counts = risk.counts;
  const summary='过去1分钟 '+counts.minute+' / '+(limits.minute || '不限')+' 条 · 过去1小时 '+counts.hour+' / '+(limits.hour || '不限')+' 条 · 今日 '+counts.day+' / '+(limits.day || '不限')+' 条';
  text('safety-counts',summary);text('send-limit-counts',summary);
  text('send-limits-status',!risk.enabled?'运行保护总开关已关闭':!risk.limits_enabled?'发送上限已关闭':risk.rate_limited?'已触发限额':'限额已生效');
  $('send-limits-status').className='pill'+(risk.rate_limited || !risk.enabled?' warning':' online');
  text('send-limits-hint',!risk.enabled?'需要在“运行保护”开启总开关，发送上限才会生效。':'设为0表示该项不限制；关闭条数上限仍保留异常暂停和重复回复保护。历史计数不会因调整或重启而清零。');
  const limited = (risk.limited_chats || []).map(row => chatName(row.chat)+'（'+row.count+' / '+limits.chat_hour+' 条/小时）');
  text('safety-reason',risk.hold_reason || (limited.length ? '当前限流会话：'+limited.join('、') : '限额按时间窗口恢复；被跳过的消息不会集中补发。'));
  text('safety-check-status',risk.check_error || (risk.last_check ? '客户端提示最近检查：'+new Date(risk.last_check*1000).toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'}) : '等待首次客户端提示检查'));
  $('safety-resume').disabled = !risk.hold_reason && !state?.paused_reason;
  const box=$('safety-events'); box.replaceChildren();
  for (const event of risk.events.slice(0,8)) {
    const line=document.createElement('p');line.className=event.level==='danger'?'error-text':'hint';
    line.textContent=new Date(event.created*1000).toLocaleTimeString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'})+' · '+(event.chat?chatName(event.chat)+' · ':'')+event.reason;box.append(line);
  }
  if (!risk.events.length) { const line=document.createElement('p');line.className='hint';line.textContent='尚无本地保护事件；这不代表微信平台没有账号风险。';box.append(line); }
}
function logTime(value) {
  return value ? new Date(value * 1000).toLocaleString('zh-CN', {hour12:false, timeZone:'Asia/Shanghai'}) : '暂无';
}
function logDuration(seconds) {
  const minutes = Math.floor((seconds || 0) / 60);
  return minutes < 1 ? '不足 1 分钟' : Math.floor(minutes / 60) + ' 小时 ' + minutes % 60 + ' 分钟';
}
async function refreshStability() {
  lastStabilityFetch = Date.now();
  const log = await api('stability');
  if (!log.available) { text('stability-badge', '等待采样'); text('stability-detail', log.message); return; }
  const summary = log.summary, sample = summary.sample || {};
  text('stability-badge', log.stale ? '采样已中断' : summary.status === 'online' ? '微信在线' : summary.status === 'offline' ? '微信已掉线' : '状态待核对');
  $('stability-badge').className = 'pill' + (!log.stale && summary.status === 'online' ? ' online' : ' warning');
  text('stability-uptime', logDuration(summary.observed_online_seconds));
  text('stability-disconnects', summary.disconnect_count ?? '—');
  text('stability-memory', sample['memory.current'] == null ? '—' : Math.round(Number(sample['memory.current']) / 1048576) + ' MiB');
  text('stability-processes', sample.zombies == null ? '—' : sample.zombies + ' / ' + (sample.native_readers ?? '—'));
  text('stability-detail', '最近采样：' + logTime(summary.latest_at) + ' · 最近掉线：' + logTime(summary.last_disconnect_at) + ' · 24 小时观察：' + ((summary.observed_online_seconds || 0) / 3600).toFixed(1) + ' / 24 小时（截至采样）');
  const box = $('stability-events'); box.replaceChildren();
  for (const event of log.events) {
    const row = document.createElement('article'); row.className = 'record';
    const date = document.createElement('div'); date.className = 'hint'; date.textContent = logTime(event.at) + ' · ' + event.code;
    const message = document.createElement('p'); message.className = event.level === 'danger' ? 'error-text' : 'message'; message.textContent = event.message;
    row.append(date, message); box.append(row);
  }
  if (!log.events.length) { const row = document.createElement('p'); row.className = 'hint'; row.textContent = '暂未记录状态变化'; box.append(row); }
}
async function update() {
  if (updating || !cfg) return;
  updating = true;
  try {
    const [snapshot, records] = await Promise.all([api('state'), api('recent?include_ignored=' + ($('records-scope')?.value !== 'actions'))]);
    renderState(snapshot); renderRecords(records);
    if (Date.now() - lastStabilityFetch > 15000) {
      await refreshStability().catch(error => { text('stability-detail', '日志暂时读取失败：' + error.message); });
    }
  } catch (error) {
    text('sync-status', '连接中断'); $('sync-status').className = 'pill warning';
    text('last-error', error.message); $('runtime-alert').hidden = false;
    if (error.status === 401) disconnect();
    throw error;
  } finally { updating = false; }
}
async function connect() {
  cfg = await api('config');
  try { chats = JSON.parse(sessionStorage.getItem('whalegirl-chat-labels') || '[]'); } catch { chats = []; }
  populateSettings();
  sessionStorage.setItem('whalegirl-access', token);
  $('auth').hidden = true; $('dashboard').hidden = false;
  if (window.personas) await window.personas.refresh();
  await update();
  if (!pollTimer) pollTimer = setInterval(() => update().catch(() => {}), 3000);
  if (window.habits) window.habits.refresh().catch(error => notice(error.message,true));
  refreshChats(true).then(() => { recordsSignature = ''; }).catch(() => {});
}
function disconnect() {
  if (token) api('auth/logout','POST').catch(() => {});
  clearInterval(pollTimer); pollTimer = null;
  sessionStorage.removeItem('whalegirl-access'); sessionStorage.removeItem('whalegirl-chat-labels');
  token = ''; cfg = null; $('access').value = '';
  $('dashboard').hidden = true; $('auth').hidden = false;
}
$('auth-form').onsubmit = event => { event.preventDefault(); action($('connect'), async () => { text('auth-error', ''); try { const result=await api('auth/login','POST',{password:$('access').value});token=result.token;$('access').value='';await connect(); } catch (error) { text('auth-error', error.message); throw error; } }); };
$('disconnect').onclick = disconnect;
$('refresh-state').onclick = () => action($('refresh-state'), update);
$('refresh-stability').onclick = () => action($('refresh-stability'), refreshStability);
$('export-stability').onclick = () => action($('export-stability'), async () => {
  const log = await api('stability/export');
  if (!log.available) throw new Error(log.message);
  const url = URL.createObjectURL(new Blob([JSON.stringify(log, null, 2)], {type:'application/json;charset=utf-8'}));
  const link = document.createElement('a'); link.href = url; link.download = 'whalegirl-runtime-log.json'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
$('refresh-chats').onclick = () => action($('refresh-chats'), () => refreshChats());
$('chat-search').oninput = renderChats;
$('stickers-enabled').onchange = () => { $('sticker-aggressiveness').disabled=!$('stickers-enabled').checked;text('chat-save-note','有未保存的修改'); };
$('save-chats').onclick = () => action($('save-chats'), async () => {
  if (!selected.size && $('quick-enabled').checked) throw new Error('启用自动回复时，请至少选择一个会话。');
  const keywords = $('keywords').value.split('\n').map(x => x.trim()).filter(Boolean);
  const restricted = [...modes].filter(([id, mode]) => id.endsWith('@chatroom') && mode === 'trigger').map(([id]) => id);
  await saveSubset({ allowed_chat_ids: [...selected], trigger_only_chat_ids: restricted,
    natural_group_replies: true, require_mention_or_keyword: true, keywords,
    stickers_enabled: $('stickers-enabled').checked, quote_replies_enabled: $('quotes-enabled').checked,
    group_mentions_enabled: $('group-mentions-enabled').checked,
    sticker_aggressiveness: $('sticker-aggressiveness').value,
    global_memory: $('global-memory').checked,
    vision_enabled: $('vision-enabled').checked, context_messages: Number($('context-count').value) });
  text('chat-save-note', '会话设置已保存 · ' + new Date().toLocaleTimeString('zh-CN')); notice('会话与唤醒设置已保存。');
});
$('save-model').onclick = () => action($('save-model'), async () => {
  await saveSubset({ api_url: $('api-url').value.trim(), api_style: $('api-style').value, model: $('model').value.trim(), max_tokens: Number($('max-tokens').value),
    reasoning_effort: $('reasoning-effort').value, model_stream_enabled: $('model-stream-enabled').checked }, $('api-key').value.trim());
  syncModelPreset();text('test-result','模型设置已保存');
  $('api-key').value = ''; text('key-status', cfg.api_key_configured ? '服务器已保存 API Key；留空不会覆盖。' : '尚未配置 API Key。'); notice('模型设置已保存。');
});
$('save-timing').onclick = () => action($('save-timing'), async () => { await saveSubset({ chat_cooldown: Number($('cooldown').value), min_send_interval: Number($('send-interval').value) }); notice('回复节奏已保存。'); });
$('save-safety').onclick = () => action($('save-safety'), async () => {
  const values={safety_enabled:$('safety-enabled').checked};
  values.safety_duplicate_seconds=Number($('safety-duplicate-seconds').value);
  await saveSubset(values);notice('本地运行保护设置已保存。');
});
$('save-limits').onclick = () => action($('save-limits'), async () => {
  const values={safety_send_limits_enabled:$('safety-send-limits-enabled').checked};
  for(const name of ['safety_per_minute','safety_per_hour','safety_chat_per_hour','safety_per_day']) values[name]=Number($(name.replaceAll('_','-')).value);
  await saveSubset(values);text('limits-save-note','已保存 · '+new Date().toLocaleTimeString('zh-CN'));notice('发送上限已保存。');
});
$('safety-check').onclick = () => action($('safety-check'), async () => { const result=await api('safety/check','POST');await update();notice(result.check_error || (result.hold_reason?'检测到需要人工处理的提示，已停止发送。':'客户端提示检查完成；结果不代表平台风控安全。'),!!result.check_error || !!result.hold_reason); });
$('safety-resume').onclick = () => action($('safety-resume'), async () => { await api('safety/resume','POST');await update();notice('已恢复发送；历史跳过消息不会补发，现有发送限额继续生效。'); });
$('quick-enabled').onchange = () => action($('quick-enabled'), async () => { try { await saveSubset({ enabled: $('quick-enabled').checked }); notice(cfg.enabled ? '自动回复已开启。' : '自动回复已关闭。'); } catch (error) { $('quick-enabled').checked = cfg.enabled; throw error; } });
$('quick-affection-enabled')?.addEventListener('change',event=>window.affection?.toggle(event.currentTarget));
$('test-model').onclick = () => action($('test-model'), async () => {
  text('test-result', '正在测试已保存的模型…');
  const result = await api('model/test', 'POST');
  const effort = {default:'服务商默认强度',low:'低强度',medium:'中强度',high:'高强度'}[result.reasoning_effort];
  const detail = [result.stream_enabled == null ? '' : result.stream_enabled ? '流式' : '非流式', effort,
    result.elapsed_ms == null ? '' : (result.elapsed_ms / 1000).toFixed(1) + ' 秒'].filter(Boolean).join(' · ');
  text('test-result', result.message + (detail ? '（' + detail + '）' : ''));
  if (!result.ok) throw new Error(result.message);
});
$('login').onclick = () => action($('login'), async () => { text('login-message', '正在请求微信二维码，请稍候…'); await api('gateway/login', 'POST'); await update(); notice('登录请求已提交，请按页面提示在手机确认。'); });
for (const link of document.querySelectorAll('nav a')) link.onclick = () => { for (const item of document.querySelectorAll('nav a')) item.classList.toggle('active', item === link); };
if (token) connect().catch(error => { text('auth-error', error.message); $('auth').hidden = false; $('dashboard').hidden = true; });

// Run after the feature scripts have appended their navigation links. Move the
// existing anchors so feature-specific click handlers and deep links survive.
document.addEventListener('DOMContentLoaded', () => {
  const sidebar=document.querySelector('.sidebar'),nav=sidebar.querySelector('nav');
  const groups=[
    ['connection','连接与会话',['#login-panel','#chats','#send-limits-panel','#safety-panel']],
    ['persona','角色与记忆',['#persona','#habits-panel','#category-limits-panel','#memory-panel','#learning-panel','#affection-panel']],
    ['interaction','互动与内容',['#proactive-panel','#sticker-library-panel','#images-panel','#moments-panel','#tieba-panel']],
    ['model','模型与用量',['#model-panel','#web-search-panel','#token-usage-panel']],
    ['activity','日志与记录',['#stability-panel','#records-panel','#inbox-panel']]
  ];
  const links=[...nav.querySelectorAll('a[href^="#"]')],byHref=new Map(links.map(a=>[a.getAttribute('href'),a]));
  for(const a of links){const label=a.querySelector('span');if(label)a.textContent=label.textContent.trim();a.classList.add('nav-page');}
  nav.id='console-nav';nav.classList.add('console-nav');nav.setAttribute('aria-label','功能导航');
  const overview=byHref.get('#overview');if(overview){overview.classList.add('nav-overview');nav.append(overview);}
  const sections=[];let updating=false;
  function group(key,title,hrefs){
    const items=hrefs.map(h=>byHref.get(h)).filter(Boolean);if(!items.length)return;
    const details=document.createElement('details');details.className='nav-group';details.dataset.navGroup=key;
    const summary=document.createElement('summary'),name=document.createElement('span'),count=document.createElement('span');
    name.textContent=title;count.className='nav-count';count.textContent=items.length;count.setAttribute('aria-hidden','true');
    summary.append(name,count);const children=document.createElement('div');children.className='nav-submenu';
    for(const a of items){children.append(a);byHref.delete(a.getAttribute('href'));}
    details.append(summary,children);nav.append(details);sections.push(details);
    details.addEventListener('toggle',()=>{
      if(updating)return;
      if(details.open)for(const other of sections)if(other!==details)other.open=false;
      try{localStorage.setItem('whalegirl-nav-group',sections.find(d=>d.open)?.dataset.navGroup||'');}catch{}
    });
  }
  if(overview)byHref.delete('#overview');
  for(const [key,title,hrefs] of groups)group(key,title,hrefs);
  if(byHref.size)group('more','更多功能',[...byHref.keys()]);
  const toggle=document.createElement('button');toggle.type='button';toggle.className='nav-toggle';
  toggle.setAttribute('aria-controls',nav.id);toggle.setAttribute('aria-expanded','false');
  const toggleTitle=document.createElement('span'),current=document.createElement('span');toggleTitle.textContent='功能导航';current.className='nav-current';toggle.append(toggleTitle,current);sidebar.insertBefore(toggle,nav);
  const mobile=window.matchMedia('(max-width:680px)');
  function closeMenu(){sidebar.classList.remove('nav-expanded');toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-label','展开功能导航');}
  toggle.onclick=()=>{const open=sidebar.classList.toggle('nav-expanded');toggle.setAttribute('aria-expanded',String(open));toggle.setAttribute('aria-label',open?'收起功能导航':'展开功能导航');};
  function active(href){
    const target=links.find(a=>a.getAttribute('href')===href)||overview;
    updating=true;
    for(const a of links){a.classList.toggle('active',a===target);if(a===target)a.setAttribute('aria-current','location');else a.removeAttribute('aria-current');}
    const selected=target?.closest('.nav-group');
    for(const section of sections){section.classList.toggle('contains-active',section===selected);if(selected)section.open=section===selected;}
    if(selected)try{localStorage.setItem('whalegirl-nav-group',selected.dataset.navGroup);}catch{}
    current.textContent=target?.textContent.trim()||'运行概览';updating=false;
  }
  let remembered=null;try{remembered=localStorage.getItem('whalegirl-nav-group');}catch{}
  updating=true;for(const section of sections)section.open=section.dataset.navGroup===(remembered??'connection');updating=false;
  nav.addEventListener('click',event=>{const link=event.target.closest('a[href^="#"]');if(!link)return;active(link.getAttribute('href'));if(mobile.matches)closeMenu();});
  window.addEventListener('hashchange',()=>active(location.hash||'#overview'));
  document.addEventListener('click',event=>{if(mobile.matches&&!sidebar.contains(event.target))closeMenu();});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&sidebar.classList.contains('nav-expanded')){closeMenu();toggle.focus();}});
  mobile.addEventListener('change',closeMenu);sidebar.classList.add('navigation-ready');active(location.hash||'#overview');
});
