'use strict';
(() => {
  const section = document.createElement('section');
  section.id = 'proactive-panel'; section.className = 'panel span-all';
  section.innerHTML = `
    <div class="section-top"><div><span class="section-index">CHAT / AUTONOMY</span><h2>自主与主动聊天</h2>
    <p class="muted">日常接话、主动开聊和后续话题，在这里一起管理。</p></div>
    <span class="pill" id="proactive-status">读取中</span></div>
    <h3>聊天方式</h3><div id="proactive-mode-slot"></div>
    <div id="proactive-participation-slot" class="settings-grid"></div>
    <label class="check-label"><input id="experience-growth-enabled" type="checkbox">体验成长：从真实相处积累经历与习惯</label>
    <p class="hint">结合群聊节奏自主选择参与，留下值得记住的互动和未说出口的想法；核心人设保留。关闭后经历仍保存，可在上方展开记录管理。公开阅读与贴吧继续使用“自主成长”里的原有开关和预算。</p>
    <h3>文字与表情</h3>
    <div id="proactive-expression-slot" class="settings-grid"></div>
    <p class="hint">AI 统一决定说话、只发表情或穿插发送，积极性影响偏好，不保证固定比例；条数是上限，不是每次必须发满。</p>
    <div class="settings-grid">
      <label>文字搭配表情意愿 / 100<input id="sticker-text-pair-eagerness" type="number" min="0" max="100"><span class="hint">高：说完具体内容后，更愿意用表情补充语气。</span></label>
      <label>只发表情意愿 / 100<input id="sticker-only-eagerness" type="number" min="0" max="100"><span class="hint">高：一个表情能接住时，少凑一句文字解释。</span></label>
      <label>接表情、拍一拍的表情意愿 / 100<input id="sticker-reaction-eagerness" type="number" min="0" max="100"><span class="hint">高：更愿意用表情回应轻松的表情互动。</span></label>
      <label>每轮最多原生表情 / 张<input id="sticker-max-per-reply" type="number" min="1" max="3"><span class="hint">仍受本轮总条数和账号额度限制，不要求每轮发满。</span></label>
    </div>
    <div class="chat-toolbar"><button id="sticker-preset-rich" class="secondary" type="button">更丰富</button><button id="sticker-preset-first" class="secondary" type="button">表情优先</button></div>
    <p class="hint">快捷档位填入上方设置，点击“保存全部聊天设置”生效。0～100 调整 AI 的表达偏好，不是程序随机发送的概率；意愿为0时不主动采用该方式，明确要求时仍可正常响应。</p>
    <details><summary>群聊记录整理的表情风格参考 · 可编辑</summary>
      <label>给 AI 的表达参考<textarea id="sticker-style-note" rows="5" maxlength="1200" placeholder="由管理员根据聊天记录整理的表达习惯，不放私人原话"></textarea></label>
      <p class="hint">这段参考与上方设置一同保存，进入固定提示词；不启动后台学习任务，不要求 AI 统计次数或照抄群友台词。</p>
    </details>
    <h3>临时安静</h3>
    <div class="settings-grid"><label>私聊未指定时长时，安静多久 / 分钟<input id="quiet-default-minutes" type="number" min="1" max="1440"></label></div>
    <p class="hint">群里明确说“闭嘴”“先安静一会儿”，本群立即静默，不发送文字或表情，也不主动聊天；直到下次 @机器人或命中唤醒词，恢复原来的回复规则，拍一拍不解除。私聊仍按设置的时长临时安静。各会话互不影响，聊天仍留作上下文。</p>
    <p class="hint" id="proactive-pacing-note"></p>
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
      <button id="save-proactive" class="primary">保存全部聊天设置</button></div>
    <div class="section-top"><h3>最近主动判断</h3><button id="refresh-proactive" class="secondary">刷新记录</button></div>
    <p class="hint">记录显示简短依据、使用的消息／记忆编号和实际发送状态。主动调度不冒充收到的微信消息，也不写成人物事实。</p>
    <div id="proactive-runs"></div>`;
  document.querySelector('.content-grid').insertBefore(section, $('stability-panel'));
  $('proactive-mode-slot').append($('autonomy-controls'));
  const participation = $('participation').closest('label'), previousRow = participation.parentElement;
  $('proactive-participation-slot').append(participation);
  if (previousRow.children.length === 1) previousRow.firstElementChild.style.gridColumn = '1 / -1';
  for (const id of ['stickers-enabled','sticker-aggressiveness','reply-max-parts','reply-part-interval','style-sticker-cooldown']) {
    const label = $(id).closest('label'), previous = label.parentElement;
    $('proactive-expression-slot').append(label);
    if (!previous.children.length) previous.hidden = true;
  }
  for(const [value,label] of [['very_high','很积极 · 轻松互动多用表情'],['sticker_first','表情优先 · 能用表情就先用']]){
    $('sticker-aggressiveness').add(new Option(label,value));
  }
  function stickerPreset(first){
    $('stickers-enabled').checked=true;$('sticker-aggressiveness').disabled=false;
    $('sticker-aggressiveness').value=first?'sticker_first':'very_high';
    $('sticker-text-pair-eagerness').value=100;$('sticker-only-eagerness').value=first?100:90;
    $('sticker-reaction-eagerness').value=100;
    text('proactive-save-note','已选择'+(first?'表情优先':'更丰富')+'，保存后生效');
  }
  $('sticker-preset-rich').onclick=()=>stickerPreset(false);
  $('sticker-preset-first').onclick=()=>stickerPreset(true);
  const link = document.createElement('a'); link.href = '#proactive-panel'; link.textContent = '自主与主动聊天';
  document.querySelector('nav').append(link);
  const names = ['experience_growth_enabled','participation','proactive_enabled','proactive_private_enabled','proactive_mentions_enabled','proactive_level',
    'proactive_per_day','proactive_chat_per_day','proactive_cooldown_minutes','proactive_decision_minutes',
    'proactive_idle_minutes','proactive_continuation_minutes','proactive_start_hour','proactive_end_hour','proactive_pause_hours',
    'stickers_enabled','sticker_aggressiveness','sticker_cooldown_seconds','reply_max_parts','reply_part_interval','quiet_default_minutes',
    'sticker_text_pair_eagerness','sticker_only_eagerness','sticker_reaction_eagerness','sticker_max_per_reply','sticker_style_note'];
  let chosen = new Set(), strict = new Set(), latest = null, lastFetch = 0;
  function field(name) { return name==='sticker_cooldown_seconds' ? $('style-sticker-cooldown') : $(name.replaceAll('_','-')); }
  function populate() {
    for (const name of names) {
      const input = field(name);
      if (input.type === 'checkbox') input.checked = !!cfg[name];
      else input.value = cfg[name] ?? (name==='sticker_usage_target'?0:'');
    }
    chosen = new Set(cfg.proactive_chat_ids || []); strict = new Set(cfg.proactive_strict_chat_ids || []);
    chatLabels();
    text('autonomy-mode','统一自主聊天 · 人格卡与本群表达决定语气');
    text('proactive-save-note', '已保存 · 自主聊天 · ' +
      (cfg.proactive_enabled ? '允许主动开聊' : '主动开聊已关闭'));
    text('proactive-pacing-note', '文字、表情及发送顺序统一由 AI 决定。' +
      (Number(cfg.proactive_idle_minutes) > 0 ? '当前主动开聊要等会话安静 ' + cfg.proactive_idle_minutes + ' 分钟，可能较晚才重提话题；希望及时接话可把下方安静时长设为 0，普通自动回复不受此等待影响。' : '主动开聊没有额外安静时长要求；判断机会不代表每次都要说话。'));
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
        '无人回应时不反复追发旧话题；有新发言或已安排的后续事情，再由 AI 判断';
      const button = document.createElement('button'); button.className = 'secondary'; button.textContent = paused ? '恢复主动联系' : '暂时停止主动联系';
      button.onclick = () => action(button, async () => {
        latest = await api('proactive/pause', 'POST', {chat:id, resume:paused});
        chatLabels(); notice(paused ? '主动联系暂停已解除，仍遵守授权、冷却和额度' : '已暂停此会话的主动联系');
      });
      const quiet = latest?.quiet?.[id];
      const quietLine = document.createElement('p'); quietLine.className = 'hint';
      quietLine.textContent = quiet?.active ? (quiet.until < 0 ? '持续静默' : '临时静默至 ' + new Date(quiet.until*1000).toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'})) + (id.endsWith('@chatroom') ? ' · 下次 @或唤醒词恢复正常回复' : ' · 明确唤醒只答本次') : '正常参与 · 按当前唤醒和自主聊天规则';
      const quietButton = document.createElement('button'); quietButton.className = 'secondary';
      quietButton.textContent = quiet?.active ? '恢复此会话聊天' : id.endsWith('@chatroom') ? '让此群持续静默' : '让此会话安静一会儿';
      quietButton.onclick = () => action(quietButton,async () => {
        latest = await api('chat-control','POST',{chat:id,op:quiet?.active?'resume':'quiet',minutes:id.endsWith('@chatroom')?0:(cfg.quiet_default_minutes ?? 30)});
        chatLabels(); notice(quiet?.active ? '已解除静默，恢复原来的聊天规则' : id.endsWith('@chatroom') ? '本群已持续静默，下次 @或唤醒词恢复正常回复' : '已临时静默；明确唤醒仍可回答本次');
      });
      const advanced = document.createElement('details'), summary = document.createElement('summary');
      summary.textContent = '单独暂停主动联系';advanced.append(summary,p,button);
      row.append(quietLine,quietButton,advanced); box.append(row);
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
      if (!input.checkValidity()) throw new Error('请检查聊天设置中的数值范围');
    }
    values.proactive_chat_ids = [...chosen].filter(id => cfg.allowed_chat_ids.includes(id));
    values.proactive_strict_chat_ids = [...strict].filter(id => values.proactive_chat_ids.includes(id));
    cfg = await api('proactive/config', 'POST', {values});
    populate(); await refresh(true);
    window.dispatchEvent(new CustomEvent('whalegirl-panel-saved', {detail:'proactive-panel'}));
    if (announce) notice('聊天参与、文字表情、静默时长和主动开聊设置已统一保存');
  }
  $('save-proactive').onclick = () => action($('save-proactive'), () => save());
  $('refresh-proactive').onclick = () => action($('refresh-proactive'), () => refresh(true));
  for (const name of names) field(name).addEventListener('input',() => text('proactive-save-note','有未保存的修改'));
  $('stickers-enabled').onchange = () => {
    $('sticker-aggressiveness').disabled = !$('stickers-enabled').checked;
    text('proactive-save-note','有未保存的修改');
  };
  window.proactiveChat = {populate, chatLabels, refresh, save};
  // This deferred script can arrive after the initial config request finishes.
  if (cfg) {
    populate();
    refresh().catch(error => notice(error.message, true));
  }
})();
