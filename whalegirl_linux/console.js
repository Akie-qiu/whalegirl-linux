'use strict';
const $ = id => document.getElementById(id);
let token = sessionStorage.getItem('whalegirl-access') || '';
let cfg = null, chats = [], selected = new Set(), modes = new Map();
let state = null, pollTimer = null, updating = false;
let modelProfiles = {}, activeModelProfile = 'custom', draftModelKeys = {};
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
async function api(path, method = 'GET', body, timeoutMs = 45000) {
  let response;
  try {
    const headers = { 'Content-Type': 'application/json' };
    if (path !== 'auth/login') headers.Authorization = 'Bearer ' + token;
    response = await fetch('/api/' + path, {
      method, headers,
      body: body === undefined ? undefined : JSON.stringify(body), cache: 'no-store',
      signal: AbortSignal.timeout(timeoutMs)
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
  $('context-count').value = Math.max(10, Math.min(100, Number(cfg.context_messages) || 100));
  populateModelProfiles(cfg);
  $('cooldown').value = cfg.chat_cooldown; $('send-interval').value = cfg.min_send_interval;
  $('quick-enabled').checked = cfg.enabled;
  $('safety-enabled').checked = cfg.safety_enabled ?? true;
  $('safety-send-limits-enabled').checked = cfg.safety_send_limits_enabled ?? true;
  for (const [name,fallback] of Object.entries({safety_per_minute:8,safety_per_hour:120,
      safety_chat_per_hour:40,safety_per_day:500,safety_duplicate_seconds:180})) {
    $(name.replaceAll('_','-')).value = cfg[name] ?? fallback;
  }
  // Each selected model's secret status is rendered by model-manager.js.
  text('wechat-version', '微信固定版本 ' + cfg.expected_wechat_version);
  renderChats();
  if (window.habits) window.habits.populate();
  window.learning?.populate();
  window.memoryHub?.populate();
  window.growthConsole?.populate();
  window.bilibiliConsole?.populate();
  window.improvements?.populate();
  window.affection?.populate();
  window.stickerLibrary?.populate();
  window.moments?.populate();
  window.pictureLibrary?.populate();
  window.tieba?.populate();
  window.proactiveChat?.populate();
  window.tokenUsage?.refresh();
}
function rememberCurrentModelDraft() { window.modelManager?.capture(); }
function loadModelProfile(id) { window.modelManager?.select(id); }
function populateModelProfiles(config) { window.modelManager?.populate(config); }
function syncModelPreset() { window.modelManager?.sync(); }
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
async function saveSubset(values, apiKey = '', searchKey = '') {
  const current = await api('config'); delete current.api_key_configured; delete current.image_api_key_configured; delete current.image_unrestricted_api_key_configured; delete current.search_api_key_configured; delete current.model_profiles; delete current.active_model_profile;
  cfg = await api('config', 'POST', { settings: { ...current, ...values }, api_key: apiKey, search_api_key: searchKey });
  await update();
  return cfg;
}
const recordLabels = { queued: '等待处理', waiting: '等待对方补充', generating: '生成中', merged: '已合并', handled: '图片任务已受理，发送结果见图片任务', sending: '正在提交微信', sent: '已确认发送', partial: '部分已发送，其余失败或被拦截', failed: '明确未发送', uncertain: '回执待核实 · 自动回复继续', skipped: '已跳过', blocked: '本地保护拦截', ignored: '安静旁观', baseline: '历史基线' };
function renderRecords(rows) {
  const signature = JSON.stringify(rows);
  if (signature === recordsSignature) return;
  recordsSignature = signature; const box = $('records'); box.replaceChildren();
  if (!rows.length) { const p = document.createElement('p'); p.className = 'hint'; p.textContent = '尚无处理记录，新消息到达后会显示在这里。'; box.append(p); }
  for (const row of rows) {
    const article = document.createElement('article'); article.className = 'record';
    const top = document.createElement('div'); top.className = 'record-top';
    const who = document.createElement('strong'); who.textContent = row.sender + ' · ' + chatName(row.chat);
    const mediaWaiting = row.state === 'queued' && (row.error || '').startsWith('图片加载中');
    const status = document.createElement('span'); status.textContent = (mediaWaiting ? '等待图片传输' : recordLabels[row.state] || row.state) + ' · ' + new Date(row.updated * 1000).toLocaleString('zh-CN', { hour12: false });
    top.append(who, status); article.append(top);
    if (row.content) {
      const message = document.createElement('div'); message.className = 'message'; message.textContent = row.content; article.append(message);
    }
    if (row.reply) {
      const hint = document.createElement('p'); hint.className = 'hint';
      hint.textContent = ['sent', 'partial'].includes(row.state) ? '已确认发送的内容' : row.state === 'uncertain' ? '发送结果待核对的内容' : '生成内容（请以发送状态为准）';
      article.append(hint);
      const message = document.createElement('div'); message.className = 'message reply'; message.textContent = row.reply; article.append(message);
    }
    for(const item of row.actions || []){
      const detail=document.createElement('p');detail.className=['sent','uncertain','sending'].includes(item.state)?'hint':'error-text';
      const d=item.delivery || {};
      detail.textContent=(item.kind==='sticker'?'表情包':item.kind==='image'?'图片':'文字')+'：'+(recordLabels[item.state] || item.state)+(d.attempts?' · 尝试 '+d.attempts+' 次':'')+(d.confirmation==='native_poll'?' · 已由后续消息确认':'')+(item.error?' · '+item.error:'');
      article.append(detail);
    }
    if (row.error) { const error = document.createElement('p'); error.className = mediaWaiting || ['ignored','baseline','uncertain'].includes(row.state) ? 'hint' : 'error-text'; error.textContent = row.error; article.append(error); }
    if (row.state === 'uncertain') {
      const actions = document.createElement('div'); actions.className = 'row';
      const button = document.createElement('button'); button.className = 'secondary'; button.textContent = '在顶部“异常与恢复”统一核对';
      button.onclick = () => window.recoveryCenter?.openIssues(); actions.append(button);
      article.append(actions);
    }
    box.append(article);
  }
}
function renderState(s) {
  state = s;
  if (window.habits) window.habits.stateUpdated(s);
  window.learning?.stateUpdated(s);
  window.memoryHub?.stateUpdated(s);
  window.growthConsole?.stateUpdated(s);
  window.bilibiliConsole?.stateUpdated(s);
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
  text('sent-count', s.counts.sent || 0); text('queue-count', (s.counts.queued || 0) + (s.counts.waiting || 0) + (s.counts.generating || 0)); text('uncertain-count', s.counts.uncertain || 0);
  text('paused', s.paused_reason); text('last-error', s.last_error);
  $('runtime-alert').hidden = !s.paused_reason && !s.last_error;
  const memory = s.memory || {};
  text('memory-count', Number(memory.messages || 0).toLocaleString('zh-CN') + ' 条');
  text('memory-detail', (memory.archived_chats || 0) + ' 个会话归档 · ' + (memory.image_descriptions || 0) + ' 条图片描述');
  const timing = s.last_reply_timing || {};
  text('reply-timing', timing.total_seconds == null ? '—' : timing.total_seconds.toFixed(1) + ' 秒');
  const memoryRecall = timing.long_term_memory;
  const memoryNote = memoryRecall ? ' · 分级记忆 ' + memoryRecall.count + ' 条' + (memoryRecall.ids.length ? '（#' + memoryRecall.ids.join('、#') + '）' : '') + (memoryRecall.estimated_tokens != null ? ' · 记忆输入约 ' + memoryRecall.estimated_tokens + '/' + memoryRecall.budget + ' Token' : '') + (memoryRecall.skipped_for_budget ? ' · 预算未载入 ' + memoryRecall.skipped_for_budget + ' 条' : '') : '';
  text('timing-detail', timing.total_seconds == null ? '等待下一次实际回复，文字和表情的耗时会分别统计。' : '发现 ' + (timing.detected_after_seconds || 0).toFixed(1) + 's · 上下文 ' + (timing.context_seconds || 0).toFixed(1) + 's · 模型 ' + (timing.model_seconds || 0).toFixed(1) + 's · 发送确认 ' + (timing.send_readback_seconds || 0).toFixed(1) + 's' + memoryNote);
  if(timing.first_reply_seconds!=null)$('timing-detail').append(document.createTextNode(' · 首条确认 '+timing.first_reply_seconds.toFixed(1)+'s'));
  if(timing.reply_ready_to_submit_seconds!=null)$('timing-detail').append(document.createTextNode(' · 生成后到'+(timing.first_submit_kind==='text'?'文字发送':'提交')+' '+timing.reply_ready_to_submit_seconds.toFixed(2)+'s'));
  if(timing.total_seconds!=null)$('timing-detail').append(document.createTextNode(' · 整轮确认 '+timing.total_seconds.toFixed(1)+'s'+(timing.delivery_queue_seconds!=null?' · 待发 '+timing.delivery_queue_seconds.toFixed(1)+'s':'')+(timing.pre_model_seconds!=null?' · 准备 '+timing.pre_model_seconds.toFixed(1)+'s':'')));
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
  if ($('compact-conn-badge')) {
    text('compact-conn-badge', online ? '🟢 微信在线' : '🟠 待登录');
    $('compact-conn-badge').className = 'ui-badge ' + (online ? 'badge-success' : 'badge-warning');
    text('compact-reply-badge', s.paused_reason ? '⏸️ 已暂停' : s.enabled ? '🤖 自动回复开启' : '⏹️ 自动回复关闭');
    $('compact-reply-badge').className = 'ui-badge ' + (s.paused_reason || !s.enabled ? 'badge-warning' : 'badge-teal');
    const mood = s.persona_live?.mood;
    text('compact-mood-badge', (mood?.label || '🌊 平静摸鱼') + ' · 活力 ' + (mood?.energy ?? 58));
    text('compact-timing-badge', '⏱️ 耗时 ' + (timing.total_seconds == null ? '—' : timing.total_seconds.toFixed(1) + 's'));
  }
  window.recoveryCenter?.stateUpdated(s);
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
    quote_replies_enabled: $('quotes-enabled').checked,
    group_mentions_enabled: $('group-mentions-enabled').checked,
    global_memory: $('global-memory').checked,
    vision_enabled: $('vision-enabled').checked, context_messages: Number($('context-count').value) });
  text('chat-save-note', '会话设置已保存 · ' + new Date().toLocaleTimeString('zh-CN')); notice('会话与唤醒设置已保存。');
});
/* Model actions are owned by model-manager.js. */
$('save-timing').onclick = () => action($('save-timing'), async () => { await saveSubset({ chat_cooldown: Number($('cooldown').value), min_send_interval: Number($('send-interval').value) }); notice('回复节奏已保存。'); });
$('save-safety').onclick = () => action($('save-safety'), async () => {
  const values={safety_enabled:$('safety-enabled').checked};
  values.safety_duplicate_seconds=Number($('safety-duplicate-seconds').value);
  await saveSubset(values);notice('本地运行保护设置已保存。');
});
$('save-limits').onclick = () => action($('save-limits'), async () => {
  for (const input of $('send-limits-panel').querySelectorAll('input, select')) {
    if (!input.disabled && !input.checkValidity()) {
      input.reportValidity?.();
      throw new Error('请检查「发送与限制」中的数值范围');
    }
  }
  const values = {
    safety_send_limits_enabled: $('safety-send-limits-enabled').checked,
    safety_enabled: $('safety-enabled').checked,
    safety_duplicate_seconds: Number($('safety-duplicate-seconds').value),
    chat_cooldown: Number($('cooldown').value),
    min_send_interval: Number($('send-interval').value),
  };
  if ($('chat-short-replies-enabled')) {
    values.chat_short_replies_enabled = $('chat-short-replies-enabled').checked;
    if ($('chat-short-replies-enabled').checked) {
      values.chat_short_reply_sentences = Number($('chat-short-reply-sentences').value);
      values.chat_short_reply_chars = Number($('chat-short-reply-chars').value);
    }
  }
  if ($('style-sticker-cooldown')) values.sticker_cooldown_seconds = Number($('style-sticker-cooldown').value);
  for (const [id, key] of [
    ['reply-max-parts', 'reply_max_parts'],
    ['sticker-max-per-reply', 'sticker_max_per_reply'],
    ['reply-part-interval', 'reply_part_interval'],
    ['debounce-single-seconds', 'debounce_single_seconds'],
    ['debounce-burst-seconds', 'debounce_burst_seconds'],
    ['debounce-max-seconds', 'debounce_max_seconds'],
    ['proactive-per-day', 'proactive_per_day'],
    ['proactive-chat-per-day', 'proactive_chat_per_day'],
    ['proactive-cooldown-minutes', 'proactive_cooldown_minutes'],
    ['quiet-start-hour', 'quiet_start_hour'],
    ['quiet-end-hour', 'quiet_end_hour'],
    ['quiet-default-minutes', 'quiet_default_minutes'],
    ['safety-per-minute', 'safety_per_minute'],
    ['safety-per-hour', 'safety_per_hour'],
    ['safety-chat-per-hour', 'safety_chat_per_hour'],
    ['safety-per-day', 'safety_per_day'],
    ['text-per-hour', 'text_per_hour'],
    ['text-per-day', 'text_per_day'],
    ['sticker-per-hour', 'sticker_per_hour'],
    ['sticker-per-day', 'sticker_per_day'],
  ]) {
    if ($(id)) values[key] = Number($(id).value);
  }
  await saveSubset(values);
  populateSettings();
  window.dispatchEvent(new CustomEvent('whalegirl-panel-saved', { detail: 'send-limits-panel' }));
  text('limits-save-note', '已保存 · ' + new Date().toLocaleTimeString('zh-CN'));
  notice('全部发送、拆条、冷却与额度限制已统一保存！');
});
$('safety-check').onclick = () => action($('safety-check'), async () => { const result=await api('safety/check','POST');await update();notice(result.check_error || (result.hold_reason?'检测到需要人工处理的提示，已停止发送。':'客户端提示检查完成；结果不代表平台风控安全。'),!!result.check_error || !!result.hold_reason); });
$('safety-resume').onclick = () => action($('safety-resume'), async () => { await api('safety/resume','POST');await update();notice('已恢复发送；历史跳过消息不会补发，现有发送限额继续生效。'); });
$('quick-enabled').onchange = () => action($('quick-enabled'), async () => { try { await saveSubset({ enabled: $('quick-enabled').checked }); notice(cfg.enabled ? '自动回复已开启。' : '自动回复已关闭。'); } catch (error) { $('quick-enabled').checked = cfg.enabled; throw error; } });
/* Model actions are owned by model-manager.js. */
$('login').onclick = () => action($('login'), async () => { text('login-message', '正在请求微信二维码，请稍候…'); await api('gateway/login', 'POST'); await update(); notice('登录请求已提交，请按页面提示在手机确认。'); });
for (const link of document.querySelectorAll('nav a')) link.onclick = () => { for (const item of document.querySelectorAll('nav a')) item.classList.toggle('active', item === link); };
if (token) connect().catch(error => { text('auth-error', error.message); $('auth').hidden = false; $('dashboard').hidden = true; });

function buildUnifiedSendLimitsPanel() {
  const panel = $('send-limits-panel');
  if (!panel || panel.dataset.unified === 'true') return;
  panel.dataset.unified = 'true';
  const el = (tag, cls, txt) => { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; };
  const top = panel.querySelector('.section-top');
  const h2 = top?.querySelector('h2'); if (h2) h2.textContent = '发送与频次限制';
  const idx = top?.querySelector('.section-index'); if (idx) idx.textContent = 'SENDING / UNIFIED LIMITS CENTER';
  const desc = top?.querySelector('.muted');
  if (desc) desc.textContent = '单轮拆条上限、短句字数、回复冷却、收发缓冲、账号总发送上限、文字/表情独立额度与防刷屏保护，全部集中在此处统一调节。';

  const limitsToggleLabel = $('safety-send-limits-enabled')?.closest('label');
  const accountGrid = panel.querySelector('.settings-grid');
  const sendLimitCounts = $('send-limit-counts');
  const sendLimitsHint = $('send-limits-hint');
  const footer = panel.querySelector('.panel-footer');
  if ($('save-limits')) text('save-limits', '保存全部发送与限制设置');

  // Card 1: Per-turn bubble & length limits
  const card1 = el('section', 'record span-all');
  card1.append(
    el('h3', '', '💬 一、单轮拆条与篇幅限制（防刷屏太吵）'),
    el('p', 'hint', '控制每次回复最多拆成几条消息气泡、单轮闲聊目标句数与字数，以及同轮多条消息之间的发送间隔。')
  );
  const presetBar = el('div', 'chat-toolbar');
  const applyBurstPreset = (parts, sentences, chars, interval, labelText) => {
    if ($('reply-max-parts')) $('reply-max-parts').value = parts;
    if ($('sticker-max-per-reply')) $('sticker-max-per-reply').value = 1;
    if ($('reply-part-interval')) $('reply-part-interval').value = interval;
    if ($('chat-short-replies-enabled')) {
      $('chat-short-replies-enabled').checked = true;
      $('chat-short-replies-enabled').dispatchEvent(new Event('change', { bubbles: true }));
    }
    if ($('chat-short-reply-sentences')) $('chat-short-reply-sentences').value = sentences;
    if ($('chat-short-reply-chars')) $('chat-short-reply-chars').value = chars;
    if ($('reply-max-parts')) $('reply-max-parts').dispatchEvent(new Event('input', { bubbles: true }));
    text('limits-save-note', `已套用「${labelText}」档位，点击右下角保存后生效`);
  };
  for (const [title, parts, sentences, chars, interval, shortName] of [
    ['🟢 清爽自然（推荐：最多3条 · 2句/56字 · 间隔0.9s）', 3, 2, 56, 0.9, '清爽自然'],
    ['🌿 极简安静（最多2条 · 1～2句/38字 · 间隔1.0s）', 2, 2, 38, 1.0, '极简安静'],
    ['✨ 活泼连发（最多4条 · 3句/75字 · 间隔0.7s）', 4, 3, 75, 0.7, '活泼连发'],
  ]) {
    const btn = el('button', 'secondary', title);
    btn.type = 'button';
    btn.onclick = () => applyBurstPreset(parts, sentences, chars, interval, shortName);
    presetBar.append(btn);
  }
  const grid1 = el('div', 'settings-grid');
  for (const id of ['reply-max-parts', 'sticker-max-per-reply', 'reply-part-interval', 'chat-short-replies-enabled', 'chat-short-reply-sentences', 'chat-short-reply-chars']) {
    const lbl = $(id)?.closest('label');
    if (lbl) grid1.append(lbl);
  }
  const shortSec = $('expression-short-controls');
  if (shortSec) {
    shortSec.replaceChildren(
      el('h3', '', '短句与单轮条数限制'),
      el('p', 'hint', '短句字数、句数与每轮最多发送条数已统一收纳至「发送与限制」板块，方便集中调节。')
    );
    const jump = el('a', 'expression-link', '↥ 前往「发送与限制」统一调整条数、字数与间隔 →');
    jump.href = '#send-limits-panel';
    shortSec.append(jump);
  }
  card1.append(presetBar, grid1);

  // Card 2: Cooldowns & debounce buffers
  const card2 = el('section', 'record span-all');
  card2.append(
    el('h3', '', '⏱️ 二、回复冷却与收发缓冲（控制两轮间隔与等待合并）'),
    el('p', 'hint', '控制同一会话或全局两轮回复之间的最短冷却间隔，以及对方连续打字时的合并等待缓冲（填 0 表示不额外等待）。')
  );
  const grid2 = el('div', 'settings-grid');
  for (const [id, fallbackTitle] of [['cooldown', '同一会话最短回复间隔 / 秒'], ['send-interval', '所有会话两轮回复最短间隔 / 秒']]) {
    const input = $(id);
    if (input) {
      const oldLbl = document.querySelector(`label[for="${id}"]`);
      const titleText = oldLbl ? oldLbl.textContent : fallbackTitle;
      oldLbl?.remove();
      const wrap = el('label');
      wrap.setAttribute('for', id);
      const title = el('span', '', titleText);
      title.dataset.controlLabel = id;
      wrap.append(title, input);
      grid2.append(wrap);
    }
  }
  for (const id of ['style-sticker-cooldown', 'debounce-single-seconds', 'debounce-burst-seconds', 'debounce-max-seconds']) {
    const lbl = $(id)?.closest('label');
    if (lbl) grid2.append(lbl);
  }
  card2.append(grid2);
  if ($('timing-detail')) card2.append($('timing-detail'));

  // Card 3: Account total limits & text/sticker independent quotas
  const card3 = el('section', 'record span-all');
  card3.append(
    el('h3', '', '📊 三、账号总发送上限与文字/表情独立额度（填 0 表示不限制）'),
    el('p', 'hint', '直接控制机器人的总发送条数及文字、原生表情独立配额。表情不占文字额度，两类均计入账号总上限。')
  );
  if (limitsToggleLabel) card3.append(limitsToggleLabel);
  if (accountGrid) {
    for (const id of ['text-per-hour', 'text-per-day', 'sticker-per-hour', 'sticker-per-day']) {
      const lbl = $(id)?.closest('label');
      if (lbl) accountGrid.append(lbl);
    }
    card3.append(accountGrid);
  }
  if (sendLimitCounts) card3.append(sendLimitCounts);
  if ($('category-counts')) card3.append($('category-counts'));
  if (sendLimitsHint) card3.append(sendLimitsHint);

  // Card 4: Proactive & quiet-hour limits
  const card4 = el('section', 'record span-all');
  card4.append(
    el('h3', '', '🌙 四、主动开聊频次与安静免打扰限制'),
    el('p', 'hint', '控制每天主动找人开聊的次数上限、会话冷却间隔，以及夜间减少无唤醒插话的安静时段（开始和结束相同表示关闭夜间安静）。')
  );
  const grid4 = el('div', 'settings-grid');
  for (const id of ['proactive-per-day', 'proactive-chat-per-day', 'proactive-cooldown-minutes', 'quiet-start-hour', 'quiet-end-hour', 'quiet-default-minutes']) {
    const lbl = $(id)?.closest('label');
    if (lbl) grid4.append(lbl);
  }
  card4.append(grid4);

  // Card 5: Local safety guards & duplicate filter
  const card5 = el('section', 'record span-all');
  const card5Top = el('div', 'section-top');
  const card5Copy = el('div');
  card5Copy.append(el('h3', '', '🛡️ 五、本地运行保护与重复拦截'), el('p', 'hint', '本地防刷屏总开关、相同回复拦截窗口与微信客户端异常检测。'));
  card5Top.append(card5Copy);
  if ($('safety-status')) card5Top.append($('safety-status'));
  card5.append(card5Top);
  const grid5 = el('div', 'settings-grid');
  for (const id of ['safety-enabled', 'safety-duplicate-seconds']) {
    const lbl = $(id)?.closest('label');
    if (lbl) grid5.append(lbl);
  }
  card5.append(grid5);
  for (const id of ['safety-counts', 'safety-reason', 'safety-check-status']) {
    if ($(id)) card5.append($(id));
  }
  const safetyBtnRow = el('div', 'row');
  if ($('safety-check')) safetyBtnRow.append($('safety-check'));
  if ($('safety-resume')) safetyBtnRow.append($('safety-resume'));
  card5.append(safetyBtnRow);
  if ($('safety-events')) card5.append($('safety-events'));

  panel.replaceChildren();
  if (top) panel.append(top);
  panel.append(card1, card2, card3, card4, card5);
  if (footer) panel.append(footer);

  // Clean up emptied legacy panels & nav links, and add jump links in habits/proactive
  for (const oldPanelId of ['timing-panel', 'safety-panel', 'category-limits-panel']) {
    const oldPanel = $(oldPanelId);
    if (oldPanel) { oldPanel.hidden = true; oldPanel.remove(); }
  }
  for (const href of ['#safety-panel', '#category-limits-panel', '#timing-panel']) {
    const link = document.querySelector(`nav a[href="${href}"]`);
    if (link) { link.hidden = true; link.remove(); }
  }
  for (const targetPanelId of ['habits-panel', 'proactive-panel']) {
    const tp = $(targetPanelId);
    if (tp) {
      const banner = el('p', 'hint');
      const a = el('a', 'expression-link', '↥ 单轮拆条条数、缓冲间隔、安静时段与发送额度已统一集中到「发送与限制」板块 →');
      a.href = '#send-limits-panel';
      banner.append(a);
      tp.querySelector('.section-top')?.after(banner);
    }
  }
}

// Consolidate existing controls before the studio shell registers all pages.
document.addEventListener('DOMContentLoaded', buildUnifiedSendLimitsPanel);
