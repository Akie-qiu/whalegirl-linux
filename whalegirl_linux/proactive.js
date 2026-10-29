'use strict';
(() => {
  const section = document.createElement('section');
  section.id = 'proactive-panel'; section.className = 'panel span-all';
  section.innerHTML = `
    <div class="section-top"><div><span class="section-index">CHAT / AUTONOMY</span><h2>自主与主动聊天</h2>
    <p class="muted">新旧模式、日常接话、主动开聊和后续话题，在这里一起管理。</p></div>
    <span class="pill" id="proactive-status">读取中</span></div>
    <h3>聊天方式</h3><div id="proactive-mode-slot"></div>
    <div id="proactive-participation-slot" class="settings-grid"></div>
    <h3>主动开聊</h3>
    <div class="settings-grid">
      <label class="check-label"><input id="proactive-enabled" type="checkbox">允许 AI 主动发起聊天</label>
      <label class="check-label"><input id="proactive-private-enabled" type="checkbox">允许主动私聊已勾选的联系人</label>
      <label class="check-label"><input id="proactive-mentions-enabled" type="checkbox">允许主动开聊时 @群成员</label>
      <label>主动程度<select id="proactive-level"><option value="quiet">克制 · 有具体话题才开口</option><option value="balanced">适中 · 分享和关心近况</option><option value="active">积极 · 更愿意发起交流</option></select></label>
      <label>每天最多主动开聊 / 次<input id="proactive-per-day" type="number" min="0" max="100"></label>
      <label>活跃开始 / 北京时间<input id="proactive-start-hour" type="number" min="0" max="23"></label>
      <label>活跃结束 / 北京时间<input id="proactive-end-hour" type="number" min="0" max="23"></label>
    </div>
    <p class="hint">每天次数填 0 表示不主动开聊；开始和结束相同表示全天。话题、对象与时机由 AI 判断，可以沉默。
    文字、原生表情和图片仍遵守现有发送保护；新生图另计生图额度。只勾选会话不会自动授权严格群主动开聊。</p>
    <details><summary>可选的节奏设置</summary><p class="hint">默认不增加每会话额度、冷却或安静时长限制；只有你需要时才设置。判断间隔控制后台模型开销，不是定时发消息。</p>
    <div class="settings-grid">
      <label>每个会话每天最多 / 次（0 不额外限制）<input id="proactive-chat-per-day" type="number" min="0" max="100"></label>
      <label>同一会话主动开聊间隔 / 分钟（0 不限制）<input id="proactive-cooldown-minutes" type="number" min="0" max="1440"></label>
      <label>全局判断机会间隔 / 分钟<input id="proactive-decision-minutes" type="number" min="3" max="180"></label>
      <label>会话安静多久后可以开聊 / 分钟（0 不限制）<input id="proactive-idle-minutes" type="number" min="0" max="180"></label>
      <label>开聊后的接话窗口 / 分钟<input id="proactive-continuation-minutes" type="number" min="1" max="30"></label>
      <label>明确拒绝后的暂停时长 / 小时<input id="proactive-pause-hours" type="number" min="1" max="168"></label>
    </div></details>
    <h3>允许主动联系的会话</h3>
    <p class="hint">这里仅列出“会话与唤醒”中已授权的对象。私聊还需开启上方私聊开关；仅唤醒群需额外明确授权。
    群成员说忙时不主动点名；要求暂停时暂不主动开聊，普通被动回复不受此开关影响。</p>
    <div id="proactive-chatlist" class="chatlist"></div>
    <div class="panel-footer"><span id="proactive-save-note" class="hint">新增功能默认关闭</span>
      <button id="save-proactive" class="primary">保存聊天设置</button></div>
    <div class="section-top"><h3>最近主动判断</h3><button id="refresh-proactive" class="secondary">刷新记录</button></div>
    <p class="hint">记录显示简短依据、使用的消息／记忆编号和实际发送状态。主动调度不冒充收到的微信消息，也不写成人物事实。</p>
    <div id="proactive-runs"></div>`;
  document.querySelector('.content-grid').insertBefore(section, $('stability-panel'));
  $('proactive-mode-slot').append($('autonomy-controls'));
  const participation = $('participation').closest('label'), previousRow = participation.parentElement;
  $('proactive-participation-slot').append(participation);
  if (previousRow.children.length === 1) previousRow.firstElementChild.style.gridColumn = '1 / -1';
  const link = document.createElement('a'); link.href = '#proactive-panel'; link.textContent = '自主与主动聊天';
  document.querySelector('nav').append(link);
  const names = ['autonomy_enabled','participation','proactive_enabled','proactive_private_enabled','proactive_mentions_enabled','proactive_level',
    'proactive_per_day','proactive_chat_per_day','proactive_cooldown_minutes','proactive_decision_minutes',
    'proactive_idle_minutes','proactive_continuation_minutes','proactive_start_hour','proactive_end_hour','proactive_pause_hours'];
  let chosen = new Set(), strict = new Set(), latest = null, lastFetch = 0;
  function field(name) { return $(name.replaceAll('_','-')); }
  function populate() {
    for (const name of names) {
      const input = field(name);
      if (input.type === 'checkbox') input.checked = !!cfg[name];
      else input.value = cfg[name];
    }
    chosen = new Set(cfg.proactive_chat_ids || []); strict = new Set(cfg.proactive_strict_chat_ids || []);
    chatLabels();
    text('autonomy-mode', cfg.autonomy_enabled ? '当前使用新版自主决策 · 切换立即保存本页设置' : '当前使用旧版回复逻辑 · 切换立即保存本页设置');
    text('proactive-save-note', '已保存 · ' + (cfg.autonomy_enabled ? '新版自主决策' : '旧版回复逻辑') + ' · ' +
      (cfg.proactive_enabled ? '允许主动开聊' : '主动开聊已关闭'));
  }
  function chatLabels() {
    if (!cfg) return;
    const box = $('proactive-chatlist'); box.replaceChildren();
    for (const id of cfg.allowed_chat_ids) {
      const row = document.createElement('div'); row.className = 'record';
      const label = document.createElement('label'); label.className = 'check-label';
      const check = document.createElement('input'); check.type = 'checkbox'; check.checked = chosen.has(id);
      const title = document.createElement('span'); title.textContent = chatName(id) + (id.endsWith('@chatroom') ? ' · 群聊' : ' · 私聊');
      label.append(check, title); row.append(label);
      check.onchange = () => {
        if (check.checked) chosen.add(id); else { chosen.delete(id); strict.delete(id); }
        if (override) { override.disabled = !check.checked; override.checked = strict.has(id); }
        text('proactive-save-note', '有未保存的修改');
      };
      let override = null;
      if ((cfg.trigger_only_chat_ids || []).includes(id)) {
        const extra = document.createElement('label'); extra.className = 'check-label';
        override = document.createElement('input'); override.type = 'checkbox'; override.checked = strict.has(id); override.disabled = !chosen.has(id);
        override.onchange = () => { if (override.checked) strict.add(id); else strict.delete(id); text('proactive-save-note', '有未保存的修改'); };
        extra.append(override, document.createTextNode('额外允许此“仅唤醒”群主动开聊，并在短暂窗口内接话'));
        row.append(extra);
      }
      const paused = (latest?.chats?.[id]?.paused_until || 0) * 1000 > Date.now();
      const p = document.createElement('p'); p.className = 'hint';
      p.textContent = paused ? '主动聊天暂停至 ' + new Date(latest.chats[id].paused_until*1000).toLocaleString('zh-CN',{hour12:false}) + ' · ' + latest.chats[id].pause_reason :
        '无人回应时当天不追发；对方回应后或第二天再由 AI 判断新话题';
      const button = document.createElement('button'); button.className = 'secondary'; button.textContent = paused ? '恢复主动联系' : '暂时停止主动联系';
      button.onclick = () => action(button, async () => {
        latest = await api('proactive/pause', 'POST', {chat:id, resume:paused});
        chatLabels(); notice(paused ? '主动联系暂停已解除，仍遵守授权、冷却和额度' : '已暂停此会话的主动联系');
      });
      row.append(p, button); box.append(row);
    }
    if (!cfg.allowed_chat_ids.length) box.textContent = '先在“会话与唤醒”授权群聊或联系人';
  }
  function render(result) {
    latest = result;
    text('proactive-status', result.enabled ? result.busy ? '正在判断话题' : '已开启 · 等待合适机会' : '已关闭');
    const box = $('proactive-runs'); box.replaceChildren();
    for (const row of result.runs || []) {
      const article = document.createElement('article'); article.className = 'record';
      const top = document.createElement('strong');
      top.textContent = chatName(row.chat) + ' · ' + ({planning:'判断中',sending:'发送中',sent:'已确认发送',partial:'部分发送',
        ignored:'选择沉默',skipped:'已让路或取消',failed:'未发送',uncertain:'需人工核对',handled:'图片任务已入队'}[row.state] || row.state);
      const reason = document.createElement('p'); reason.textContent = row.reason || row.error || '正在判断';
      const details = document.createElement('p'); details.className = 'hint';
      details.textContent = new Date(row.created*1000).toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'}) +
        (row.sources?.length ? ' · 依据 ' + row.sources.join('、') : '') +
        (row.image_state ? ' · 图片：'+row.image_state : '') + (row.error ? ' · '+row.error : '');
      article.append(top, reason);
      if (row.reply) { const reply = document.createElement('p'); reply.className = 'message'; reply.textContent = row.reply; article.append(reply); }
      article.append(details); box.append(article);
    }
    if (!(result.runs || []).length) box.textContent = '暂无主动判断记录；开启并授权会话后，等待活跃时段和合适的话题';
  }
  async function refresh(force=false) {
    if (!cfg || !token || (!force && Date.now()-lastFetch < 30000)) return;
    lastFetch = Date.now(); render(await api('proactive')); chatLabels();
  }
  async function save(announce=true) {
    const values = {};
    for (const name of names) {
      const input = field(name); values[name] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value) : input.value;
    }
    values.proactive_chat_ids = [...chosen].filter(id => cfg.allowed_chat_ids.includes(id));
    values.proactive_strict_chat_ids = [...strict].filter(id => values.proactive_chat_ids.includes(id));
    cfg = await api('proactive/config', 'POST', {values});
    populate(); await refresh(true);
    window.dispatchEvent(new CustomEvent('whalegirl-panel-saved', {detail:'proactive-panel'}));
    if (announce) notice('聊天模式、参与程度和主动开聊设置已一起保存');
  }
  $('save-proactive').onclick = () => action($('save-proactive'), () => save());
  $('refresh-proactive').onclick = () => action($('refresh-proactive'), () => refresh(true));
  window.proactiveChat = {populate, chatLabels, refresh, save};
  // This deferred script can arrive after the initial config request finishes.
  if (cfg) {
    populate();
    refresh().catch(error => notice(error.message, true));
  }
})();
