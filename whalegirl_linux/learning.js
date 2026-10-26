'use strict';
(() => {
  const panel = document.createElement('section');
  panel.id = 'learning-panel'; panel.className = 'panel span-all';
  panel.style.overflowWrap = 'anywhere';
  panel.innerHTML = `
    <div class="section-top"><div><span class="section-index">LEARNING</span><h2>学习与成长</h2></div><button id="learning-refresh" class="secondary">刷新</button></div>
    <div class="two-fields">
      <label class="check-label"><input id="learning-enabled" type="checkbox">启用聊天学习与经验召回</label>
      <label class="check-label"><input id="learning-auto-accept" type="checkbox">自动采纳高置信度普通经验</label>
      <label>学习积极性 <output id="learning-eagerness-value"></output><input id="learning-eagerness" type="range" min="0" max="100" step="1"></label>
      <label>默认有效期 / 天<input id="learning-default-ttl-days" type="number" min="1" max="365"></label>
      <label>每轮最多召回条数<input id="learning-recall-limit" type="number" min="1" max="8"></label>
    </div>
    <div class="panel-footer"><span id="learning-status" class="hint" role="status"></span><button id="learning-save" class="primary">保存学习设置</button></div>
    <p id="learning-counts" class="timing-detail"></p>
    <div class="chat-toolbar">
      <input id="learning-query" type="search" placeholder="搜索经验、证据或人物 ID" aria-label="搜索学习记录">
      <select id="learning-filter-chat" aria-label="来源会话"></select>
      <select id="learning-filter-state" aria-label="学习状态">
        <option value="">所有状态</option><option value="pending">待审核</option><option value="accepted">已采纳</option>
        <option value="rejected">已拒绝</option><option value="revoked">已撤回</option><option value="expired">已过期</option>
      </select><button id="learning-search" class="secondary">搜索</button>
    </div>
    <div id="learning-list" aria-live="polite"></div>
    <dialog id="learning-editor" class="tieba-archive-dialog">
      <form id="learning-edit-form">
        <div class="section-top"><h3 id="learning-edit-title"></h3><button id="learning-close" type="button" class="secondary" aria-label="关闭">关闭</button></div>
        <label>经验内容<textarea id="learning-content" rows="4" maxlength="400" required></textarea></label>
        <label>重新设置有效期 / 天<input id="learning-ttl" type="number" min="1" max="365" placeholder="留空保留当前期限"></label>
        <div class="panel-footer"><span class="hint">修改后回到待审核</span><button class="primary" id="learning-edit-save" type="submit">保存修改</button></div>
        <h3>来源证据</h3><p id="learning-evidence" class="message"></p><p id="learning-source" class="hint"></p>
        <h3>修订记录</h3><div id="learning-history"></div>
      </form>
    </dialog>`;
  $('memory-panel').after(panel);
  const nav = document.querySelector('nav'), link = document.createElement('a');
  link.href = '#learning-panel'; link.textContent = '学习与成长'; nav.append(link);
  const states = {pending:'待审核', accepted:'已采纳', rejected:'已拒绝', revoked:'已撤回', expired:'已过期'};
  const kinds = {fact:'事实', preference:'偏好', chat_style:'聊天习惯', meme_usage:'梗的用法',
    tool_learning:'工具经验', expression:'表达习惯', relationship:'相处关系', persona:'角色相关'};
  const operations = {propose:'提交', auto_accept:'自动采纳', accept:'采纳', reject:'拒绝', revoke:'撤回', revise:'编辑', expire:'到期停用'};
  let editing = null, lastFetch = 0, fetching = false;
  const time = value => new Date(value * 1000).toLocaleString('zh-CN', {hour12:false, timeZone:'Asia/Shanghai'});
  const node = (tag, value, cls='') => {
    const el = document.createElement(tag); el.textContent = value; el.className = cls; return el;
  };
  function labels() {
    const select = $('learning-filter-chat'), current = select.value;
    select.replaceChildren(new Option('所有授权会话', ''));
    for (const id of cfg.allowed_chat_ids) select.add(new Option(chatName(id), id));
    if (cfg.allowed_chat_ids.includes(current)) select.value = current;
  }
  function populate() {
    if (!cfg) return;
    $('learning-enabled').checked = !!cfg.learning_enabled;
    $('learning-auto-accept').checked = !!cfg.learning_auto_accept;
    for (const name of ['learning_eagerness', 'learning_default_ttl_days', 'learning_recall_limit'])
      $(name.replaceAll('_', '-')).value = cfg[name];
    $('learning-eagerness').oninput(); labels();
    if (location.hash === '#learning-panel') refresh().catch(e => notice(e.message, true));
  }
  function showStatus(s) {
    text('learning-status', (s.enabled ? '已开启' : '已关闭') + ' · 待处理 ' + s.queued +
      (s.dropped ? ' · 队列满跳过 ' + s.dropped : '') + (s.last_error ? ' · ' + s.last_error : ''));
  }
  function command(title, op, row) {
    const b = node('button', title, 'secondary'); b.type = 'button';
    b.onclick = () => action(b, async () => {
      await api('learning/' + row.id, 'POST', {op, version:row.version}); await refresh();
      notice('学习记录已更新');
    });
    return b;
  }
  async function edit(row) {
    const detail = await api('learning/' + row.id); editing = detail.proposal;
    text('learning-edit-title', '#' + editing.id + ' · ' + states[editing.state] + ' · v' + editing.version);
    $('learning-content').value = editing.content; $('learning-ttl').value = '';
    text('learning-evidence', editing.evidence);
    text('learning-source', chatName(editing.chat) + ' · ' + editing.subject + ' · 消息 ' +
      editing.source_key + ' · 到期 ' + time(editing.expires_at));
    const history = $('learning-history'); history.replaceChildren();
    for (const r of detail.revisions) {
      const entry = document.createElement('details'); entry.className = 'record';
      entry.append(node('summary', (operations[r.operation] || r.operation) + ' · ' +
        (r.actor === 'admin' ? '管理员' : r.actor === 'model' ? 'AI' : '系统') + ' · ' + time(r.created)));
      if (r.before) entry.append(node('p', '修改前：' + r.before.content + ' · ' + states[r.before.state], 'message'));
      entry.append(node('p', '修改后：' + r.after.content + ' · ' + states[r.after.state], 'message'));
      history.append(entry);
    }
    if (!$('learning-editor').open) $('learning-editor').showModal();
  }
  async function refresh() {
    if (!cfg || fetching) return;
    fetching = true;
    try {
      labels();
      const query = new URLSearchParams({state:$('learning-filter-state').value, q:$('learning-query').value,
        chat:$('learning-filter-chat').value});
      const result = await api('learning?' + query); showStatus(result);
      text('learning-counts', Object.entries(states).map(([key, title]) => title + ' ' + (result.counts[key] || 0)).join(' · '));
      const list = $('learning-list'); list.replaceChildren();
      for (const row of result.proposals) {
        const entry = document.createElement('article'); entry.className = 'record';
        const active = row.state === 'accepted' && row.expires_at * 1000 > Date.now();
        entry.append(node('strong', '#' + row.id + ' · ' + states[row.state] + (active ? ' · 可召回' : '') +
          ' · ' + (kinds[row.kind] || row.kind) + ' · ' + (row.scope === 'profile' ? '共享人物偏好/事实' : '仅来源会话')));
        entry.append(node('p', row.content, 'message'));
        entry.append(node('p', chatName(row.chat) + ' · ' + row.subject + ' · 置信度 ' + Math.round(row.confidence * 100) +
          '% · v' + row.version + ' · 注入上下文 ' + row.recall_count + ' 次 · 到期 ' + time(row.expires_at), 'hint'));
        const evidence = document.createElement('details');
        evidence.append(node('summary', '来源证据'), node('p', row.evidence, 'message')); entry.append(evidence);
        const buttons = document.createElement('div'); buttons.className = 'row';
        const editor = node('button', '详情与编辑', 'secondary'); editor.type = 'button';
        editor.onclick = () => action(editor, () => edit(row)); buttons.append(editor);
        if (!active && row.expires_at * 1000 > Date.now()) buttons.append(command('采纳', 'accept', row));
        if (row.state === 'pending') buttons.append(command('拒绝', 'reject', row));
        if (active) buttons.append(command('撤回', 'revoke', row));
        entry.append(buttons); list.append(entry);
      }
      if (!result.proposals.length) list.append(node('p', '暂无匹配学习记录', 'hint'));
      lastFetch = Date.now();
    } finally { fetching = false; }
  }
  $('learning-eagerness').oninput = () => text('learning-eagerness-value', $('learning-eagerness').value + ' / 100');
  $('learning-save').onclick = () => action($('learning-save'), async () => {
    for (const id of ['learning-default-ttl-days', 'learning-recall-limit'])
      if (!$(id).reportValidity()) return;
    await saveSubset({learning_enabled:$('learning-enabled').checked, learning_auto_accept:$('learning-auto-accept').checked,
      learning_eagerness:Number($('learning-eagerness').value), learning_default_ttl_days:Number($('learning-default-ttl-days').value),
      learning_recall_limit:Number($('learning-recall-limit').value)});
    notice('学习设置已保存，下一轮生效'); await refresh();
  });
  $('learning-close').onclick = () => $('learning-editor').close();
  $('learning-edit-form').onsubmit = event => {
    event.preventDefault();
    action($('learning-edit-save'), async () => {
      if (!editing) return;
      const data = {op:'revise', version:editing.version, content:$('learning-content').value};
      if ($('learning-ttl').value) data.ttl_days = Number($('learning-ttl').value);
      await api('learning/' + editing.id, 'POST', data);
      $('learning-editor').close(); editing = null; await refresh(); notice('已保存为待审核，请确认后采纳');
    });
  };
  for (const id of ['learning-search', 'learning-refresh']) $(id).onclick = () => action($(id), refresh);
  for (const id of ['learning-filter-state', 'learning-filter-chat'])
    $(id).onchange = () => refresh().catch(e => notice(e.message, true));
  $('learning-query').onkeydown = e => { if (e.key === 'Enter') refresh().catch(err => notice(err.message, true)); };
  window.addEventListener('hashchange', () => {
    if (location.hash === '#learning-panel') refresh().catch(e => notice(e.message, true));
  });
  window.learning = {populate, stateUpdated(s) {
    if (s.learning) showStatus(s.learning);
    if (location.hash === '#learning-panel' && Date.now() - lastFetch > 30000)
      refresh().catch(e => notice(e.message, true));
  }};
  if (cfg) populate();
})();
