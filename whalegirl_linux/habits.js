'use strict';
(() => {
  const panels = $('habits-panels').content.cloneNode(true);
  document.querySelector('.content-grid').insertBefore(panels, $('stability-panel'));
  const assocRow = document.createElement('div'); assocRow.className = 'two-fields';
  const salienceLabel = document.createElement('label'); salienceLabel.textContent = '情绪刻骨度星级';
  const salienceSelect = document.createElement('select'); salienceSelect.id = 'memory-salience';
  for (const [val, txt] of [
    ['1', '★☆☆☆☆ 随口一提（随时间较快淡忘）'],
    ['2', '★★☆☆☆ 轻微印象'],
    ['3', '★★★☆☆ 日常事实 / 偏好（默认）'],
    ['4', '★★★★☆ 重要约定 / 鲜明特征'],
    ['5', '★★★★★ 刻骨铭心 / 重大名场面（免时间衰减）'],
  ]) {
    const opt = document.createElement('option'); opt.value = val; opt.textContent = txt;
    if (val === '3') opt.selected = true;
    salienceSelect.append(opt);
  }
  salienceLabel.append(salienceSelect);
  const trigLabel = document.createElement('label'); trigLabel.textContent = '联想触发词（逗号分隔，命中即跨话题唤醒）';
  const trigInput = document.createElement('input'); trigInput.id = 'memory-triggers'; trigInput.type = 'text';
  trigInput.maxLength = 300; trigInput.placeholder = '例如：火锅,微辣,熬夜,戒奶茶';
  trigLabel.append(trigInput);
  assocRow.append(salienceLabel, trigLabel);
  $('memory-content').parentElement.before(assocRow);
  const nav = document.querySelector('nav');
  for (const [id, title] of [['web-search-panel', '联网搜索'], ['habits-panel', '聊天习惯'], ['category-limits-panel', '文字与表情额度'], ['memory-panel', '长期记忆']]) {
    const a = document.createElement('a'); a.href = '#' + id; a.textContent = title;
    a.onclick = () => { for (const item of nav.querySelectorAll('a')) item.classList.toggle('active', item === a); };
    nav.insertBefore(a, nav.querySelector('[href="#stability-panel"]'));
  }
  let editing = null, loaded = [], lastFetch = 0, lastAutonomyFetch = 0;
  const numeric = ['debounce_single_seconds','debounce_burst_seconds','debounce_max_seconds','followup_wait_seconds','reply_max_parts',
    'reply_part_interval','quiet_start_hour','quiet_end_hour','text_per_hour','text_per_day','sticker_per_hour','sticker_per_day'];
  const field = name => $(name.replaceAll('_','-'));
  function populate() {
    for (const name of numeric) field(name).value = cfg[name];
    $('chat-habits-enabled').checked = cfg.chat_habits_enabled;
    $('autonomy-enabled').checked = cfg.autonomy_enabled ?? false;
    text('autonomy-mode', cfg.autonomy_enabled ? '当前使用新版自主聊天' : '当前使用旧版回复逻辑');
    $('followup-wait-enabled').checked = cfg.followup_wait_enabled ?? true;
    $('editable-memory-enabled').checked = cfg.editable_memory_enabled;
    $('web-search-enabled').checked = cfg.web_search_enabled;
    $('web-search-result-count').value = cfg.web_search_result_count ?? 5;
    $('web-search-timeout').value = cfg.web_search_timeout ?? 35;
    $('participation').value = cfg.participation;
    $('interest-keywords').value = (cfg.interest_keywords || []).join('\n');
    $('repeat-probability').value = Math.round(cfg.repeat_probability * 100);
    chatLabels();
    document.querySelector('label[for="send-interval"]').textContent = '所有会话两轮回复最短间隔 / 秒';
  }
  function chatLabels() {
    const select = $('memory-chat'); const value = select.value; select.replaceChildren();
    for (const id of cfg.allowed_chat_ids) {
      const option = document.createElement('option'); option.value = id; option.textContent = chatName(id); select.append(option);
    }
    if (cfg.allowed_chat_ids.includes(value)) select.value = value;
    window.improvements?.chatLabels();
  }
  function button(title, fn) {
    const b = document.createElement('button'); b.type = 'button'; b.className = 'secondary'; b.textContent = title;
    b.onclick = () => action(b, fn); return b;
  }
  async function refreshAutonomy() {
    lastAutonomyFetch = Date.now();
    const result = await api('autonomy');
    text('autonomy-mode', '当前使用' + result.mode + (result.busy ? ' · 正在整理新增聊天' : '') + (result.last_error ? ' · ' + result.last_error : ''));
    const intents = $('autonomy-intents'), events = $('autonomy-events');
    intents.replaceChildren(); events.replaceChildren();
    if (!result.intents.length) intents.textContent = '暂时没有待接续的话题';
    for (const item of result.intents) {
      const row = document.createElement('article'); row.className = 'record';
      const title = document.createElement('p'); title.textContent = chatName(item.chat) + ' · ' + item.topic;
      const when = document.createElement('p'); when.className = 'hint';
      when.textContent = (result.enabled ? '可考虑时间：' : '已暂停 · 可考虑时间：') + new Date(item.due * 1000).toLocaleString('zh-CN', {hour12:false,timeZone:'Asia/Shanghai'});
      row.append(title, when, button('取消接续', async () => {
        await api('autonomy/intents/' + item.id, 'POST', {op:'cancel'}); await refreshAutonomy();
      })); intents.append(row);
    }
    const labels = {speak:'选择参与',silent:'选择旁观',observe:'合并观察',growth:'整理经验',wait:'等待补充'};
    if (!result.events.length) events.textContent = '开启新版后显示参与依据和成长记录';
    for (const item of result.events) {
      const row = document.createElement('article'); row.className = 'record';
      const title = document.createElement('strong'); title.textContent = (labels[item.kind] || item.kind) + ' · ' + chatName(item.chat);
      const reason = document.createElement('p'); reason.textContent = item.reason;
      const when = document.createElement('p'); when.className = 'hint';
      when.textContent = new Date(item.created * 1000).toLocaleString('zh-CN', {hour12:false,timeZone:'Asia/Shanghai'});
      row.append(title, reason, when); events.append(row);
    }
  }
  $('autonomy-enabled').onchange = async () => {
    const control = $('autonomy-enabled'); const value = control.checked; control.disabled = true;
    try {
      await saveSubset({autonomy_enabled:value});
      notice(value ? '已切换到新版自主聊天。' : '已切回旧版回复逻辑，接续意图与新版旁听学习已暂停。');
      if ($('autonomy-details').open) await refreshAutonomy();
    } catch (error) {
      control.checked = cfg.autonomy_enabled ?? false; notice(error.message, true);
    } finally { control.disabled = false; }
  };
  $('refresh-autonomy').onclick = () => action($('refresh-autonomy'), refreshAutonomy);
  $('autonomy-details').ontoggle = () => {
    if ($('autonomy-details').open) refreshAutonomy().catch(error => notice(error.message, true));
  };
  function reset() {
    editing = null; $('memory-subject').value = ''; $('memory-content').value = ''; $('memory-kind').value = 'fact';
    $('memory-joke-ttl').value=''; updateExpiry();
    if($('memory-scope')) $('memory-scope').value='chat';
    if($('memory-salience')) $('memory-salience').value='3';
    if($('memory-triggers')) $('memory-triggers').value='';
    text('memory-editor-title', '新增记忆'); text('memory-source', '网页手动新增，来源标为管理员');
    if ($('memory-editor-summary')) text('memory-editor-summary', '✍️ 新增 / 编辑长期记忆');
  }
  function updateExpiry() {$('memory-joke-ttl-label').hidden=$('memory-kind').value!=='joke';}
  function badge(txt, cls) {
    const s = document.createElement('span'); s.className = 'ui-badge ' + cls; s.textContent = txt; return s;
  }
  function render(result) {
    loaded = result.memories; const box = $('memory-list'); box.replaceChildren();
    if (!loaded.length) { const p = document.createElement('p'); p.className = 'hint'; p.textContent = '暂无匹配记忆。明确偏好或要求记住的内容会在这里显示。'; box.append(p); }
    const kinds = {fact:'事实',preference:'偏好',impression:'暂时印象',relationship:'关系',event:'本群事件',joke:'群内共同梗',
      self_stance:'机器人自身言行/Flag',shared_moment:'群内共同名场面'};
    for (const m of loaded) {
      const article = document.createElement('article'); article.className = 'record'; article.dataset.memoryId = m.id;
      const salienceNum = Math.max(1, Math.min(5, Number(m.salience || 3)));
      const stars = '★'.repeat(salienceNum) + '☆'.repeat(5 - salienceNum);
      const badges = document.createElement('div'); badges.className = 'badge-row';
      badges.append(
        badge('#' + m.id + ' · v' + m.version, 'badge-muted'),
        badge(kinds[m.kind] || m.kind, m.kind === 'self_stance' || m.kind === 'shared_moment' ? 'badge-purple' : 'badge-info'),
        badge(stars, 'badge-gold'),
        badge(m.scope === 'profile' ? '共享人物档案' : '本会话独立', 'badge-teal')
      );
      if (m.archived) badges.append(badge('已删除（可恢复）', 'badge-danger'));
      if (m.triggers) badges.append(badge('🔗 联想词：' + m.triggers, 'badge-purple'));
      const top = document.createElement('strong');
      top.textContent = m.subject + ' · ' + chatName(m.chat);
      const p = document.createElement('p'); p.className = 'message'; p.textContent = m.content;
      const source = document.createElement('p'); source.className = 'hint';
      source.textContent = (m.actor === 'model' ? '🤖 AI 工具整理' : '👤 管理员录入') + ' · 来源 ' + (m.source_key || '网页手动录入');
      if(m.expires_at) source.textContent+=' · '+(m.expires_at*1000<=Date.now()?'已到期，停止自动召回':'到期时间')+' '+new Date(m.expires_at*1000).toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'});
      const actions = document.createElement('div'); actions.className = 'row';
      actions.append(button('编辑', async () => {
        editing = m; $('memory-chat').value = m.chat; $('memory-subject').value = m.subject;
        $('memory-kind').value = m.kind; $('memory-content').value = m.content;
        $('memory-joke-ttl').value='';updateExpiry();
        if($('memory-scope')) $('memory-scope').value=m.scope || 'chat';
        if($('memory-salience')) $('memory-salience').value=String(m.salience || 3);
        if($('memory-triggers')) $('memory-triggers').value=m.triggers || '';
        text('memory-editor-title', '编辑记忆 #' + m.id + ' · v' + m.version); text('memory-source', '原始来源：' + (m.source_key || '管理员'));
        if ($('memory-editor-summary')) text('memory-editor-summary', '✍️ 正在编辑记忆 #' + m.id + ' · v' + m.version);
        if ($('memory-editor-drawer')) {
          $('memory-editor-drawer').open = true;
          $('memory-editor-drawer').scrollIntoView({behavior:'smooth', block:'nearest'});
        }
        $('memory-content').focus();
      }), button(m.archived ? '恢复' : '删除', async () => {
        if (!m.archived && !window.confirm('删除长期记忆 #' + m.id + '？\n来源：' + chatName(m.chat) +
            '\n人物：' + m.subject + '\n内容：' + m.content +
            '\n\n确认后，这条记录不再参与长期记忆调用。可在“含已删除”中恢复；原始聊天和修订记录保留。')) return;
        await api('memory','POST',{op:m.archived ? 'restore':'archive',id:m.id,version:m.version});
        if (editing?.id === m.id) reset();
        await refresh(); notice(m.archived ? '记忆已恢复，下一轮可调用。' : '记忆已删除，不再参与长期记忆调用；可勾选“含已删除”恢复。');
      }));
      article.append(badges,top,p,source,actions); box.append(article);
    }
    const revisions = $('memory-revisions'); revisions.replaceChildren();
    for (const r of result.revisions) {
      const row = document.createElement('article'); row.className = 'record';
      const p = document.createElement('p'); p.className = 'hint';
      const operation = {create:'新增',update:'修改',archive:'删除（可恢复）',restore:'恢复',undo:'撤销'}[r.operation] || r.operation;
      p.textContent = '#' + r.memory_id + ' · ' + operation + ' · ' + (r.actor==='model'?'AI':'管理员') + ' · ' + new Date(r.created*1000).toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'}) + (r.evidence_key ? ' · 依据 '+r.evidence_key : '');
      const undo = button('撤销这次修改', async () => { await api('memory/revisions/'+r.id+'/undo','POST'); await refresh(); notice('已撤销，并保留撤销记录。'); });
      const latest = loaded.find(m => m.id===r.memory_id);
      undo.disabled = !latest || latest.version!==r.version;
      row.append(p,undo); revisions.append(row);
    }
  }
  async function refresh() {
    const query = new URLSearchParams({q:$('memory-search').value,include_archived:String($('memory-show-archived').checked),chat:$('memory-filter-chat')?.value || ''});
    const result = await api('memory?'+query); render(result); lastFetch=Date.now();
    const people = await api('memory/subjects'); const list=$('memory-subject-options'); list.replaceChildren();
    for (const person of people) { const o=document.createElement('option');o.value=person.id;o.label=person.name;list.append(o); }
  }
  function stateUpdated(s) {
    if ($('autonomy-details').open && Date.now() - lastAutonomyFetch > 10000) {
      refreshAutonomy().catch(error => notice(error.message, true));
    }
    const web=s.web_search || {}, last=web.last || {};
    text('web-search-status',!web.enabled?'联网已关闭':!last.checked_at?'联网已启用，按需调用':
      (last.ok?'上次联网成功':'上次联网未完成')+' · '+(last.seconds||0).toFixed(2)+' 秒'+
      (last.result_count!=null?' · '+last.result_count+' 个结果':'')+(last.error?' · '+last.error:''));
    const c=s.safety?.counts || {}, l=s.safety?.limits || {};
    text('category-counts','文字：小时 '+(c.text_hour||0)+' / '+(l.text_hour||'不限')+' · 今日 '+(c.text_day||0)+' / '+(l.text_day||'不限')+
      '；表情：小时 '+(c.sticker_hour||0)+' / '+(l.sticker_hour||'不限')+' · 今日 '+(c.sticker_day||0)+' / '+(l.sticker_day||'不限'));
    if (Date.now()-lastFetch>30000 && location.hash==='#memory-panel') {lastFetch=Date.now();refresh().catch(e=>notice(e.message,true));}
    window.improvements?.stateUpdated(s);
  }
  $('save-habits').onclick = () => action($('save-habits'),async () => {
    const values={chat_habits_enabled:$('chat-habits-enabled').checked,
      followup_wait_enabled:$('followup-wait-enabled').checked,
      participation:$('participation').value,
      interest_keywords:$('interest-keywords').value.split('\n').map(x=>x.trim()).filter(Boolean),repeat_probability:Number($('repeat-probability').value)/100};
    for (const name of numeric.filter(n=>!n.includes('_per_'))) values[name]=Number(field(name).value);
    await saveSubset(values);notice('聊天习惯已保存，下一轮生效。');
  });
  $('save-web-search').onclick = () => action($('save-web-search'),async () => {
    await saveSubset({web_search_enabled:$('web-search-enabled').checked,
      web_search_result_count:Number($('web-search-result-count').value),web_search_timeout:Number($('web-search-timeout').value)});
    notice('联网设置已保存，下一轮生效。');
  });
  $('save-category-limits').onclick = () => action($('save-category-limits'),async () => {
    const values={};for(const name of numeric.filter(n=>n.includes('_per_'))) values[name]=Number(field(name).value);
    await saveSubset(values);notice('文字和表情独立额度已保存。');
  });
  $('save-memory-switch').onclick = () => action($('save-memory-switch'),async () => {
    await saveSubset({editable_memory_enabled:$('editable-memory-enabled').checked});notice('记忆权限已保存。');
  });
  $('refresh-memory').onclick = () => action($('refresh-memory'),refresh);
  $('find-memory').onclick = () => action($('find-memory'),refresh);
  $('memory-show-archived').onchange = () => refresh().catch(e=>notice(e.message,true));
  $('new-memory').onclick = reset;
  if ($('open-memory-editor')) {
    $('open-memory-editor').onclick = () => {
      reset();
      if ($('memory-editor-drawer')) {
        $('memory-editor-drawer').open = true;
        $('memory-editor-drawer').scrollIntoView({behavior:'smooth', block:'nearest'});
      }
      $('memory-subject').focus();
    };
  }
  window.addEventListener('hashchange', () => {
    if (location.hash === '#memory-panel') refresh().catch(e => notice(e.message, true));
  });
  $('memory-kind').onchange=updateExpiry;
  $('save-memory').onclick = () => action($('save-memory'),async () => {
    if (!$('memory-subject').value.trim() || !$('memory-content').value.trim()) throw new Error('请填写人物 ID 和记忆内容。');
    const ttl=$('memory-joke-ttl');
    if($('memory-kind').value==='joke' && !ttl.checkValidity()) throw new Error('共同梗有效期需为1～90天，留空使用默认或原期限。');
    await api('memory','POST',{op:editing?'update':'create',...(editing?{id:editing.id,version:editing.version}:{}),
      chat:$('memory-chat').value,subject:$('memory-subject').value.trim(),kind:$('memory-kind').value,
      ...($('memory-kind').value==='joke' && ttl.value!==''?{ttl_days:Number(ttl.value)}:{}),
      content:$('memory-content').value.trim(),scope:$('memory-scope')?.value || 'chat',
      salience:Number($('memory-salience')?.value || 3),triggers:$('memory-triggers')?.value.trim() || '',
      source_key:editing?.source_key || ''});
    reset();await refresh();notice('记忆已保存，下一轮检索生效。');
  });
  window.habits={populate,refresh,stateUpdated,chatLabels};
  if(cfg) {populate();refresh().catch(e=>notice(e.message,true));}
})();
