'use strict';
(() => {
  const panels = $('habits-panels').content.cloneNode(true);
  document.querySelector('.content-grid').insertBefore(panels, $('stability-panel'));
  const nav = document.querySelector('nav');
  for (const [id, title] of [['web-search-panel', '联网搜索'], ['habits-panel', '聊天习惯'], ['category-limits-panel', '文字与表情额度'], ['memory-panel', '长期记忆']]) {
    const a = document.createElement('a'); a.href = '#' + id; a.textContent = title;
    a.onclick = () => { for (const item of nav.querySelectorAll('a')) item.classList.toggle('active', item === a); };
    nav.insertBefore(a, nav.querySelector('[href="#stability-panel"]'));
  }
  let editing = null, loaded = [], lastFetch = 0;
  const numeric = ['debounce_single_seconds','debounce_burst_seconds','debounce_max_seconds','reply_max_parts',
    'reply_part_interval','quiet_start_hour','quiet_end_hour','text_per_hour','text_per_day','sticker_per_hour','sticker_per_day'];
  const field = name => $(name.replaceAll('_','-'));
  function populate() {
    for (const name of numeric) field(name).value = cfg[name];
    $('chat-habits-enabled').checked = cfg.chat_habits_enabled;
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
  function reset() {
    editing = null; $('memory-subject').value = ''; $('memory-content').value = ''; $('memory-kind').value = 'fact';
    $('memory-joke-ttl').value=''; updateExpiry();
    if($('memory-scope')) $('memory-scope').value='chat';
    text('memory-editor-title', '新增记忆'); text('memory-source', '网页手动新增，来源标为管理员');
  }
  function updateExpiry() {$('memory-joke-ttl-label').hidden=$('memory-kind').value!=='joke';}
  function render(result) {
    loaded = result.memories; const box = $('memory-list'); box.replaceChildren();
    if (!loaded.length) { const p = document.createElement('p'); p.className = 'hint'; p.textContent = '暂无匹配记忆。明确偏好或要求记住的内容会在这里显示。'; box.append(p); }
    const kinds = {fact:'事实',preference:'偏好',impression:'暂时印象',relationship:'关系',event:'本群事件',joke:'群内共同梗'};
    for (const m of loaded) {
      const article = document.createElement('article'); article.className = 'record'; article.dataset.memoryId = m.id;
      const top = document.createElement('strong'); top.textContent = '#' + m.id + ' · ' + m.subject + ' · ' + kinds[m.kind] + ' · ' + (m.scope==='profile'?'共享人物档案':'本会话独立') + (m.archived ? ' · 已删除（可恢复）' : '');
      const p = document.createElement('p'); p.className = 'message'; p.textContent = m.content;
      const source = document.createElement('p'); source.className = 'hint'; source.textContent = chatName(m.chat) + ' · v' + m.version + ' · ' + (m.actor === 'model' ? 'AI 工具' : '管理员') + ' · 来源 ' + (m.source_key || '网页手动录入');
      if(m.expires_at) source.textContent+=' · '+(m.expires_at*1000<=Date.now()?'已到期，停止自动召回':'到期时间')+' '+new Date(m.expires_at*1000).toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'});
      const actions = document.createElement('div'); actions.className = 'row';
      actions.append(button('编辑', async () => {
        editing = m; $('memory-chat').value = m.chat; $('memory-subject').value = m.subject;
        $('memory-kind').value = m.kind; $('memory-content').value = m.content;
        $('memory-joke-ttl').value='';updateExpiry();
        if($('memory-scope')) $('memory-scope').value=m.scope || 'chat';
        text('memory-editor-title', '编辑记忆 #' + m.id + ' · v' + m.version); text('memory-source', '原始来源：' + (m.source_key || '管理员'));
        $('memory-content').focus();
      }), button(m.archived ? '恢复' : '删除', async () => {
        if (!m.archived && !window.confirm('删除长期记忆 #' + m.id + '？\n来源：' + chatName(m.chat) +
            '\n人物：' + m.subject + '\n内容：' + m.content +
            '\n\n确认后，这条记录不再参与长期记忆调用。可在“含已删除”中恢复；原始聊天和修订记录保留。')) return;
        await api('memory','POST',{op:m.archived ? 'restore':'archive',id:m.id,version:m.version});
        if (editing?.id === m.id) reset();
        await refresh(); notice(m.archived ? '记忆已恢复，下一轮可调用。' : '记忆已删除，不再参与长期记忆调用；可勾选“含已删除”恢复。');
      }));
      article.append(top,p,source,actions); box.append(article);
    }
    const revisions = $('memory-revisions'); revisions.replaceChildren();
    for (const r of result.revisions) {
      const row = document.createElement('div'); row.className = 'record';
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
    const values={chat_habits_enabled:$('chat-habits-enabled').checked,participation:$('participation').value,
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
  $('memory-kind').onchange=updateExpiry;
  $('save-memory').onclick = () => action($('save-memory'),async () => {
    if (!$('memory-subject').value.trim() || !$('memory-content').value.trim()) throw new Error('请填写人物 ID 和记忆内容。');
    const ttl=$('memory-joke-ttl');
    if($('memory-kind').value==='joke' && !ttl.checkValidity()) throw new Error('共同梗有效期需为1～90天，留空使用默认或原期限。');
    await api('memory','POST',{op:editing?'update':'create',...(editing?{id:editing.id,version:editing.version}:{}),
      chat:$('memory-chat').value,subject:$('memory-subject').value.trim(),kind:$('memory-kind').value,
      ...($('memory-kind').value==='joke' && ttl.value!==''?{ttl_days:Number(ttl.value)}:{}),
      content:$('memory-content').value.trim(),scope:$('memory-scope')?.value || 'chat',source_key:editing?.source_key || ''});
    reset();await refresh();notice('记忆已保存，下一轮检索生效。');
  });
  window.habits={populate,refresh,stateUpdated,chatLabels};
  if(cfg) {populate();refresh().catch(e=>notice(e.message,true));}
})();
