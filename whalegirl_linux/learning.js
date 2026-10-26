'use strict';
(() => {
  const panel = document.createElement('section');
  panel.id = 'learning-panel'; panel.className = 'panel span-all';
  panel.style.overflowWrap = 'anywhere';
  panel.innerHTML = `
    <div class="section-top"><div><span class="section-index">LEARNING & EVOLUTION</span><h2>学习与自进化</h2><p class="muted">后台静默吸收本群黑话、机器人别称与相处经验；单人恶意调教自动拦截，核心人设锁死不跑偏。</p></div><button id="learning-refresh" class="secondary">刷新</button></div>
    <div class="model-presets" role="group" aria-label="自进化速率预设" style="margin-bottom:12px">
      <button type="button" class="model-preset" data-evo-preset="conservative" aria-pressed="false"><span class="model-preset-mark">稳</span><span class="model-preset-copy"><strong>保守稳健</strong><small>≥3人提及 · 90%置信</small></span><span class="model-preset-check" aria-hidden="true">✓</span></button>
      <button type="button" class="model-preset" data-evo-preset="balanced" aria-pressed="true"><span class="model-preset-mark pro">均</span><span class="model-preset-copy"><strong>均衡活人（推荐）</strong><small>≥2人共创 · 85%置信</small></span><span class="model-preset-check" aria-hidden="true">✓</span></button>
      <button type="button" class="model-preset" data-evo-preset="aggressive" aria-pressed="false"><span class="model-preset-mark pro">速</span><span class="model-preset-copy"><strong>极速融梗</strong><small>≥1人提及2次 · 78%置信</small></span><span class="model-preset-check" aria-hidden="true">✓</span></button>
      <button type="button" class="model-preset" data-evo-preset="custom" aria-pressed="false"><span class="model-preset-mark custom">定</span><span class="model-preset-copy"><strong>自定义参数</strong><small>按下方细分项控制</small></span><span class="model-preset-check" aria-hidden="true">✓</span></button>
    </div>
    <details class="settings-drawer" open>
      <summary>⚙️ 学习积极性与基础召回设置</summary>
      <div class="two-fields" style="margin-top:10px">
        <label class="check-label"><input id="learning-enabled" type="checkbox">启用聊天学习与经验召回</label>
        <label class="check-label"><input id="learning-auto-accept" type="checkbox">自动采纳高置信度普通经验</label>
        <label>学习积极性 <output id="learning-eagerness-value"></output><input id="learning-eagerness" type="range" min="0" max="100" step="1"></label>
        <label>默认有效期 / 天<input id="learning-default-ttl-days" type="number" min="1" max="365"></label>
        <label>每轮最多召回条数<input id="learning-recall-limit" type="number" min="1" max="8"></label>
      </div>
    </details>
    <details class="settings-drawer" id="evolution-guardrail-box">
      <summary>🛡️ 后台异步自进化与防带偏护栏（点击展开细项）</summary>
      <div class="two-fields" style="margin-top:10px">
        <label class="check-label"><input id="evolution-async-digest" type="checkbox">后台摘要顺带提取群黑话与印象演化（主链路零延迟）</label>
        <label class="check-label"><input id="evolution-slang-auto-accept" type="checkbox">本群黑话 / 谐音别称（如“碗粥”）达标自动转正并联动唤醒词</label>
        <label class="check-label"><input id="evolution-profile-auto-accept" type="checkbox">千人千面人物印象卡（称呼/标签/相处指南）自动微调</label>
        <label class="check-label"><input id="evolution-group-mask-auto-accept" type="checkbox">分群性格面具在 ±1 档边界内自适应本群氛围</label>
        <label>黑话转正最少提及人数<input id="evolution-min-distinct-users" type="number" min="1" max="5" step="1"></label>
        <label>黑话转正最少出现次数<input id="evolution-min-occurrences" type="number" min="1" max="10" step="1"></label>
        <label>自动转正最低置信度 / %<input id="evolution-confidence-threshold" type="number" min="50" max="99" step="1"></label>
      </div>
      <p class="hint" style="margin-top:8px">🔒 防恶意调教红线（永久开启）：凡涉及强迫认主/喊爹、篡改底层系统指令、强制句尾口癖（每句带喵/汪）的提议，系统自动打入「护栏已拦截」，绝不进入召回池。</p>
    </details>
    <div class="panel-footer"><span id="learning-status" class="hint" role="status"></span><button id="learning-save" class="primary">保存学习设置</button></div>
    <p id="learning-counts" class="timing-detail"></p>
    <div class="chat-toolbar">
      <input id="learning-query" type="search" placeholder="搜索经验、黑话别称、证据或人物 ID" aria-label="搜索学习记录">
      <select id="learning-filter-chat" aria-label="来源会话"></select>
      <select id="learning-filter-kind" aria-label="学习类型">
        <option value="">所有类别</option>
        <option value="slang_all">🗣️ 本群黑话与别称</option>
        <option value="bot_alias">🔔 机器人唤醒别称</option>
        <option value="slang">💬 群内黑话/新梗</option>
        <option value="chat_style">聊天习惯</option>
        <option value="meme_usage">梗的用法</option>
        <option value="preference">偏好</option>
        <option value="fact">事实</option>
        <option value="relationship">相处关系</option>
      </select>
      <select id="learning-filter-state" aria-label="学习状态">
        <option value="">所有状态</option><option value="pending">待审核</option><option value="accepted">已采纳</option>
        <option value="blocked">🛡️ 护栏已拦截</option><option value="rejected">已拒绝</option><option value="revoked">已撤回</option><option value="expired">已过期</option>
      </select><button id="learning-search" class="secondary">搜索</button>
    </div>
    <div id="learning-list" class="bento-grid" aria-live="polite"></div>
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
  const states = {pending:'待审核', accepted:'已采纳', blocked:'已拦截', rejected:'已拒绝', revoked:'已撤回', expired:'已过期'};
  const kinds = {fact:'事实', preference:'偏好', chat_style:'聊天习惯', meme_usage:'梗的用法',
    tool_learning:'工具经验', expression:'表达习惯', relationship:'相处关系', persona:'角色相关',
    bot_alias:'机器人别称', slang:'本群黑话'};
  const operations = {propose:'提交', auto_accept:'自动采纳', accept:'采纳', reject:'拒绝', revoke:'撤回', revise:'编辑', expire:'到期停用', block:'护栏拦截'};
  const presetTable = {
    conservative: {eagerness:35, min_users:3, min_occ:3, conf:90},
    balanced: {eagerness:68, min_users:2, min_occ:2, conf:85},
    aggressive: {eagerness:90, min_users:1, min_occ:2, conf:78},
  };
  let editing = null, lastFetch = 0, fetching = false, currentPreset = 'balanced';
  function syncPresetButtons(preset) {
    currentPreset = preset || 'custom';
    for (const btn of panel.querySelectorAll('[data-evo-preset]')) {
      btn.setAttribute('aria-pressed', String(btn.dataset.evoPreset === currentPreset));
    }
  }
  for (const btn of panel.querySelectorAll('[data-evo-preset]')) {
    btn.onclick = () => {
      const p = btn.dataset.evoPreset;
      syncPresetButtons(p);
      if (presetTable[p]) {
        $('learning-eagerness').value = presetTable[p].eagerness;
        $('learning-eagerness').oninput();
        $('evolution-min-distinct-users').value = presetTable[p].min_users;
        $('evolution-min-occurrences').value = presetTable[p].min_occ;
        $('evolution-confidence-threshold').value = presetTable[p].conf;
      } else {
        $('evolution-guardrail-box').open = true;
      }
    };
  }
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
    $('evolution-async-digest').checked = cfg.evolution_async_digest !== false;
    $('evolution-slang-auto-accept').checked = cfg.evolution_slang_auto_accept !== false;
    $('evolution-profile-auto-accept').checked = cfg.evolution_profile_auto_accept !== false;
    $('evolution-group-mask-auto-accept').checked = cfg.evolution_group_mask_auto_accept !== false;
    $('evolution-min-distinct-users').value = cfg.evolution_min_distinct_users ?? 2;
    $('evolution-min-occurrences').value = cfg.evolution_min_occurrences ?? 2;
    $('evolution-confidence-threshold').value = Math.round((cfg.evolution_confidence_threshold ?? 0.85) * 100);
    syncPresetButtons(cfg.evolution_preset || 'balanced');
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
        chat:$('learning-filter-chat').value, kind:$('learning-filter-kind').value});
      const result = await api('learning?' + query); showStatus(result);
      text('learning-counts', Object.entries(states).map(([key, title]) => title + ' ' + (result.counts[key] || 0)).join(' · '));
      const list = $('learning-list'); list.replaceChildren();
      for (const row of result.proposals) {
        const entry = document.createElement('article'); entry.className = 'record';
        const active = row.state === 'accepted' && row.expires_at * 1000 > Date.now();
        const isSlang = row.kind === 'bot_alias' || row.kind === 'slang';
        const userCount = Array.isArray(row.distinct_users_list) && row.distinct_users_list.length
          ? row.distinct_users_list.length : 1;
        const occCount = row.occurrences || 1;
        const badges = node('div', '', 'badge-row');
        const stateCls = active ? 'badge-success' : row.state === 'pending' ? 'badge-warning'
          : (row.state === 'blocked' || row.state === 'rejected') ? 'badge-danger' : 'badge-muted';
        badges.append(
          node('span', '#' + row.id + ' · v' + row.version, 'ui-badge badge-muted'),
          node('span', (states[row.state] || row.state) + (active ? ' · 可召回' : ''), 'ui-badge ' + stateCls),
          node('span', kinds[row.kind] || row.kind, 'ui-badge ' + (isSlang ? 'badge-purple' : 'badge-info')),
          node('span', row.scope === 'profile' ? '共享人物偏好/事实' : '仅来源会话', 'ui-badge badge-teal')
        );
        if (row.kind === 'bot_alias' && active) {
          badges.append(node('span', '🔔 已接入本群唤醒词', 'ui-badge badge-gold'));
        }
        if (isSlang) {
          badges.append(node('span', `👥 ${userCount}人提及 / 累计${occCount}次`, 'ui-badge badge-purple'));
        }
        entry.append(badges);
        entry.append(node('p', row.content, 'message'));
        entry.append(node('p', chatName(row.chat) + ' · ' + row.subject + ' · 置信度 ' + Math.round(row.confidence * 100) +
          '% · 注入上下文 ' + row.recall_count + ' 次 · 到期 ' + time(row.expires_at), 'hint'));
        if (row.guardrail_reason) {
          entry.append(node('p', '🛡️ 护栏拦截原因：' + row.guardrail_reason, 'error-text'));
        }
        const evidence = document.createElement('details');
        evidence.append(node('summary', '来源证据'), node('p', row.evidence, 'message')); entry.append(evidence);
        const buttons = document.createElement('div'); buttons.className = 'row';
        const editor = node('button', '详情与编辑', 'secondary'); editor.type = 'button';
        editor.onclick = () => action(editor, () => edit(row)); buttons.append(editor);
        if (!active && row.expires_at * 1000 > Date.now()) buttons.append(command('采纳', 'accept', row));
        if (row.state === 'pending') buttons.append(command('拒绝', 'reject', row));
        if (active) buttons.append(command('撤回', 'revoke', row));
        if (row.state !== 'blocked') buttons.append(command('拉黑词条', 'block', row));
        entry.append(buttons); list.append(entry);
      }
      if (!result.proposals.length) list.append(node('p', '暂无匹配学习记录', 'hint'));
      lastFetch = Date.now();
    } finally { fetching = false; }
  }
  $('learning-eagerness').oninput = () => {
    text('learning-eagerness-value', $('learning-eagerness').value + ' / 100');
    const p = presetTable[currentPreset];
    if (p && Number($('learning-eagerness').value) !== p.eagerness) syncPresetButtons('custom');
  };
  $('learning-save').onclick = () => action($('learning-save'), async () => {
    for (const id of ['learning-default-ttl-days', 'learning-recall-limit',
      'evolution-min-distinct-users', 'evolution-min-occurrences', 'evolution-confidence-threshold'])
      if (!$(id).reportValidity()) return;
    await saveSubset({
      learning_enabled:$('learning-enabled').checked,
      learning_auto_accept:$('learning-auto-accept').checked,
      learning_eagerness:Number($('learning-eagerness').value),
      learning_default_ttl_days:Number($('learning-default-ttl-days').value),
      learning_recall_limit:Number($('learning-recall-limit').value),
      evolution_preset:currentPreset,
      evolution_async_digest:$('evolution-async-digest').checked,
      evolution_slang_auto_accept:$('evolution-slang-auto-accept').checked,
      evolution_profile_auto_accept:$('evolution-profile-auto-accept').checked,
      evolution_group_mask_auto_accept:$('evolution-group-mask-auto-accept').checked,
      evolution_min_distinct_users:Number($('evolution-min-distinct-users').value),
      evolution_min_occurrences:Number($('evolution-min-occurrences').value),
      evolution_confidence_threshold:Number($('evolution-confidence-threshold').value) / 100,
    });
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
  for (const id of ['learning-filter-state', 'learning-filter-chat', 'learning-filter-kind'])
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
