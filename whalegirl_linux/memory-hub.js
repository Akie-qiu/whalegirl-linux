'use strict';
(() => {
  const panel = $('memory-panel');
  const legacy = document.createElement('div'); legacy.hidden = true; legacy.id = 'legacy-memory-controls';
  while (panel.firstChild) legacy.append(panel.firstChild);
  panel.append(legacy);
  $('learning-panel').hidden = true;
  for (const link of document.querySelectorAll('nav a')) {
    if (link.getAttribute('href') === '#learning-panel') link.hidden = true;
    if (link.getAttribute('href') === '#memory-panel') link.textContent = '记忆与成长';
  }
  const body = document.createElement('div');
  body.innerHTML = `
    <style>#hub-tabs{display:flex;gap:6px;flex-wrap:wrap}#hub-tabs>button{width:auto;flex:0 0 auto;padding:8px 12px;border-radius:999px}#hub-tabs>button[aria-pressed="true"]{background:#118c7d;color:white;border-color:#118c7d}#hub-editor,#hub-detail{max-height:85vh;overflow:auto}#hub-list .message{overflow-wrap:anywhere}#hub-editor .two-fields>*{min-width:0}</style>
    <div class="section-top"><div><span class="section-index">MEMORY & GROWTH</span><h2>记忆与成长</h2>
      <p class="muted">按微信 ID 认人，将人物偏好、共同经历和成长经验统一管理。等级决定读取方式，来源和适用范围独立保留。</p></div>
      <div class="row"><button id="hub-new" class="primary" type="button">＋ 新增记忆</button><button id="hub-refresh" class="secondary" type="button">刷新</button></div></div>
    <details class="settings-drawer"><summary>记忆设置</summary><div class="two-fields" style="margin-top:12px">
      <label class="check-label"><input id="hub-enabled" type="checkbox">启用记忆召回与 AI 记忆操作</label>
      <label class="check-label"><input id="hub-consolidation" type="checkbox">自动整理与成长（后台增量执行）</label>
      <label>学习积极性 <output id="hub-eagerness-value"></output><input id="hub-eagerness" type="range" min="0" max="100"></label>
      <label>每轮记忆输入预算 / Token（本地估算）<input id="hub-budget" type="number" min="400" max="6000" step="100"></label>
      <label>每轮最多记忆条数<input id="hub-limit" type="number" min="2" max="30"></label>
      <label>普通记忆多久未更新或使用转冷藏 / 天<input id="hub-cold-days" type="number" min="3" max="365"></label>
      <label class="check-label"><input id="hub-ai-editing" type="checkbox">允许 AI 根据真实消息修正或忘记资料</label>
      <label class="check-label"><input id="hub-auto-accept" type="checkbox">自动采纳有依据的普通成长经验</label>
    </div><p class="hint">关闭自动整理仍可使用已有记忆及明确的记住/忘记指令。核心不按时间降级；稳定事实不会只因很久没聊就失效。好感度开关仍在好感度页面。</p>
      <div class="panel-footer"><span class="hint">只影响记忆与成长，近期上下文条数沿用会话设置</span><button id="hub-save-settings" class="primary" type="button">保存记忆设置</button></div></details>
    <div id="hub-tabs" class="model-presets" role="group" aria-label="记忆视图" style="margin:14px 0"></div>
    <p id="hub-counts" class="timing-detail" role="status"></p>
    <div class="chat-toolbar"><input id="hub-query" type="search" placeholder="搜索内容、微信 ID 或关键词" aria-label="搜索记忆">
      <select id="hub-chat" aria-label="来源会话"></select><select id="hub-person" aria-label="人物"></select>
      <select id="hub-tier" aria-label="记忆等级"><option value="">所有等级</option></select>
      <select id="hub-status" aria-label="记忆状态"><option value="">所有状态</option></select>
      <select id="hub-kind" aria-label="记忆类型"><option value="">所有类型</option></select>
      <button id="hub-search" class="secondary" type="button">查询</button></div>
    <div id="hub-list" class="bento-grid" aria-live="polite"></div><div class="panel-footer"><span id="hub-page-note" class="hint"></span><button id="hub-more" class="secondary" type="button" hidden>下一页</button></div>
    <dialog id="hub-editor" class="tieba-archive-dialog"><form id="hub-edit-form">
      <div class="section-top"><h3 id="hub-edit-title">新增记忆</h3><button id="hub-edit-close" class="secondary" type="button">关闭</button></div>
      <div class="two-fields"><label>来源会话<select id="hub-edit-chat" required></select></label>
        <label>人物微信 ID<input id="hub-edit-person" list="hub-person-options" maxlength="200" required><datalist id="hub-person-options"></datalist></label>
        <label>类型<select id="hub-edit-kind"></select></label><label>等级<select id="hub-edit-tier"></select></label>
        <label>使用范围<select id="hub-edit-scope"><option value="chat">仅来源会话</option><option value="profile">该人物跨会话资料</option></select></label>
        <label>适用场合<select id="hub-edit-venue"><option value="any">按范围使用</option><option value="group">公开群聊 / 朋友圈</option><option value="private">仅私聊</option></select></label>
        <label>生效状态<select id="hub-edit-status"></select></label><label class="check-label"><input id="hub-edit-locked" type="checkbox">锁定，AI 不可修改</label></div>
      <label>记忆内容<textarea id="hub-edit-content" rows="4" maxlength="700" required></textarea></label>
      <div class="two-fields"><label>事实属性（可选，同一属性用于版本替代）<input id="hub-edit-property" maxlength="80" placeholder="如 diet.spicy"></label>
        <label>关联关键词（可选）<input id="hub-edit-triggers" maxlength="300"></label></div>
      <p class="hint">事实与偏好应明确描述谁、什么情况、何时有效。跨会话人物资料不包含群内事件和私聊经历。</p>
      <div class="panel-footer"><span id="hub-edit-source" class="hint"></span><button id="hub-edit-save" class="primary" type="submit">保存</button></div>
    </form></dialog>
    <dialog id="hub-detail" class="tieba-archive-dialog"><div class="section-top"><h3 id="hub-detail-title">记忆依据与修订</h3><button id="hub-detail-close" class="secondary" type="button">关闭</button></div>
      <p id="hub-detail-content" class="message"></p><div id="hub-detail-meta" class="hint"></div>
      <h3>来源证据</h3><div id="hub-evidence"></div><h3>修订记录</h3><div id="hub-revisions"></div><p id="hub-legacy-note" class="hint"></p>
    </dialog>`;
  panel.append(body);
  const tiers = {core:'核心',stable:'稳定',ordinary:'普通',cold:'冷藏'};
  const states = {active:'生效',pending:'待确认',superseded:'已被替代',forgotten:'已忘记',expired:'已过期',rejected:'已拒绝',blocked:'已拦截'};
  const kinds = {fact:'事实',preference:'偏好',event:'事件',joke:'群内共同梗',impression:'主观印象',relationship:'相处关系',
    self_stance:'承诺 / 自身立场',shared_moment:'共同经历',chat_style:'会话风格',meme_usage:'梗的用法',tool_learning:'工具经验',
    expression:'表达经验',persona:'角色经验',bot_alias:'机器人别称',slang:'群内黑话',nickname:'人物称呼',interaction_rule:'相处方式'};
  function options(node, values) { for (const [value,label] of Object.entries(values)) {const o=document.createElement('option');o.value=value;o.textContent=label;node.append(o);} }
  options($('hub-tier'),tiers);options($('hub-edit-tier'),tiers);options($('hub-status'),states);options($('hub-edit-status'),states);
  options($('hub-kind'),kinds);options($('hub-edit-kind'),kinds);
  let view='all',next=null,editing=null,lastFetch=0,loading=false,people=new Map();
  function node(tag,cls,value='') {const el=document.createElement(tag);if(cls)el.className=cls;el.textContent=value;return el;}
  function button(label,fn) {const b=node('button','secondary',label);b.type='button';b.onclick=()=>action(b,fn);return b;}
  const when=value=>value?new Date(value*1000).toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'}):'未记录';
  for (const [id,label] of [['all','总览'],['people','人物'],['groups','群聊'],['growth','成长经验'],['cold','冷藏与停用']]) {
    const b=node('button','subtab-pill',label);b.type='button';b.dataset.view=id;b.setAttribute('aria-pressed',String(id===view));
    b.onclick=()=>{view=id;for(const item of $('hub-tabs').children)item.setAttribute('aria-pressed',String(item===b));refresh().catch(e=>notice(e.message,true));};$('hub-tabs').append(b);
  }
  function chatOptions(select,all) {const value=select.value;select.replaceChildren();if(all)options(select,{'':'所有会话'});options(select,Object.fromEntries((cfg?.allowed_chat_ids||[]).map(id=>[id,chatName(id)])));if([...select.options].some(o=>o.value===value))select.value=value;}
  function populate() {
    if(!cfg)return;
    $('hub-enabled').checked=cfg.memory_enabled??true;$('hub-consolidation').checked=cfg.memory_consolidation_enabled??true;
    $('hub-budget').value=cfg.memory_token_budget??1400;$('hub-limit').value=cfg.memory_item_limit??8;$('hub-cold-days').value=cfg.memory_cold_after_days??30;
    $('hub-eagerness').value=cfg.learning_eagerness??70;$('hub-eagerness-value').textContent=$('hub-eagerness').value+'/100';
    $('hub-ai-editing').checked=cfg.editable_memory_enabled;$('hub-auto-accept').checked=cfg.learning_auto_accept;
    chatOptions($('hub-chat'),true);chatOptions($('hub-edit-chat'),false);
  }
  async function loadPeople() {
    const rows=await api('memory/subjects');people=new Map(rows.map(r=>[r.id,r.name]));
    const current=$('hub-person').value;$('hub-person').replaceChildren();options($('hub-person'),{'':'所有人物'});
    $('hub-person-options').replaceChildren();
    for(const r of rows){options($('hub-person'),{[r.id]:r.name+' · '+r.id});const o=node('option','');o.value=r.id;o.label=r.name;$('hub-person-options').append(o);}
    $('hub-person').value=current;
  }
  function edit(row=null) {
    editing=row;chatOptions($('hub-edit-chat'),false);$('hub-edit-title').textContent=row?'编辑记忆 #'+row.id+' · v'+row.version:'新增记忆';
    if(row)$('hub-edit-chat').value=row.chat;$('hub-edit-person').value=row?.subject||'';$('hub-edit-kind').value=row?.kind||'preference';
    $('hub-edit-tier').value=row?.tier||'stable';$('hub-edit-status').value=row?.status||'active';$('hub-edit-scope').value=row?.scope||'chat';
    $('hub-edit-venue').value=row?.venue||'any';$('hub-edit-locked').checked=!!row?.locked;$('hub-edit-content').value=row?.content||'';
    $('hub-edit-property').value=row?.property_key||'';$('hub-edit-triggers').value=row?.triggers||'';
    $('hub-edit-source').textContent=row?.source_key?'保留来源：'+row.source_key:'由管理员录入';$('hub-editor').showModal();
  }
  async function detail(row) {
    const d=await api('memory-hub/'+row.id);const m=d.memory;
    $('hub-detail-title').textContent='#'+m.id+' · '+(tiers[m.tier]||m.tier)+' · '+(states[m.status]||m.status);
    $('hub-detail-content').textContent=m.content;$('hub-detail-meta').textContent='来源 '+chatName(m.chat)+' · 人物 '+(people.get(m.subject)||m.subject)+' · '+m.subject+' · 更新 '+when(m.updated)+' · 载入 '+m.recall_count+' 次 · 模型报告使用 '+m.use_count+' 次（不作晋级证据）';
    $('hub-evidence').replaceChildren();
    if(!d.evidence.length)$('hub-evidence').append(node('p','hint','旧资料或管理员录入；没有可展开的原始证据，不据此提高可信度。'));
    for(const e of d.evidence){const a=node('article','record');a.append(node('p','hint',e.source_type+' · '+when(e.occurred)+' · '+e.source_key));if(e.excerpt)a.append(node('p','message',e.excerpt));$('hub-evidence').append(a);}
    $('hub-revisions').replaceChildren();
    for(const r of d.revisions){const a=node('article','record');a.append(node('p','hint',r.actor+' · '+r.operation+' · '+when(r.created)));
      const after=JSON.parse(r.after_json);const b=button('撤销这次修改',async()=>{await api('memory/revisions/'+r.id+'/undo','POST');$('hub-detail').close();await refresh();});b.disabled=after.version!==m.version;a.append(b);$('hub-revisions').append(a);}
    $('hub-legacy-note').textContent=d.legacy_revisions.length?'另保留 '+d.legacy_revisions.length+' 条原学习系统修订，旧 ID 与来源映射已保存。':'';
    $('hub-detail').showModal();
  }
  async function mutate(row,op) {
    if(op==='archive'&&!window.confirm('停止使用记忆 #'+row.id+'？\n'+row.content+'\n相关来源将停止召回，可在冷藏与停用中恢复。'))return;
    await api('memory-hub','POST',{op,id:row.id,version:row.version});await refresh();notice(op==='archive'?'已停止使用，相关摘要与来源同步失效':'已恢复记忆');
  }
  function render(result) {
    $('hub-list').replaceChildren();
    const counts=Object.fromEntries(Object.keys(tiers).map(k=>[k,result.counts.filter(r=>r.tier===k).reduce((a,r)=>a+r.n,0)]));
    $('hub-counts').textContent=(result.enabled?'记忆已启用':'记忆已关闭，资料保留')+' · 匹配 '+result.total+' 条 · '+Object.keys(tiers).map(k=>tiers[k]+' '+counts[k]).join(' / ');
    if(!result.memories.length)$('hub-list').append(node('p','hint','暂无匹配记忆，可调整人物、等级或会话筛选。'));
    for(const m of result.memories){const card=node('article','record');card.dataset.memoryId=m.id;
      card.append(node('p','hint','#'+m.id+' · '+tiers[m.tier]+' · '+states[m.status]+' · '+(kinds[m.kind]||m.kind)+(m.locked?' · 已锁定':'')),
        node('h3','',(people.get(m.subject)||m.subject)+' · '+chatName(m.chat)),node('p','message',m.content),
        node('p','hint',(m.scope==='profile'?'人物共享资料':'仅来源会话')+' · '+(m.venue==='private'?'仅私聊':m.venue==='group'?'公开场合':'按范围')+' · 更新 '+when(m.updated)));
      const actions=node('div','row');actions.append(button('编辑',async()=>edit(m)),button('来源与修订',()=>detail(m)),
        button(m.status==='forgotten'?'恢复':'停止使用',()=>mutate(m,m.status==='forgotten'?'restore':'archive')));
      if(m.status==='pending')actions.append(button('确认生效',async()=>{await api('memory-hub','POST',{op:'update',id:m.id,version:m.version,status:'active',source_key:m.source_key});await refresh();}));
      card.append(actions);$('hub-list').append(card);
    }
    next=result.next;$('hub-more').hidden=!next;$('hub-page-note').textContent='本页 '+result.memories.length+' 条；按记忆 ID 倒序，来源时间保留';
  }
  async function refresh(before=0) {
    if(!cfg||loading)return;loading=true;
    try{const q=new URLSearchParams({q:$('hub-query').value,chat:$('hub-chat').value,subject:$('hub-person').value,tier:$('hub-tier').value,
      status:$('hub-status').value,kind:$('hub-kind').value,view,before:String(before)});render(await api('memory-hub?'+q));lastFetch=Date.now();}
    finally{loading=false;}
  }
  $('hub-new').onclick=()=>edit();$('hub-edit-close').onclick=()=>$('hub-editor').close();$('hub-detail-close').onclick=()=>$('hub-detail').close();
  $('hub-search').onclick=()=>action($('hub-search'),()=>refresh());$('hub-refresh').onclick=()=>action($('hub-refresh'),async()=>{await loadPeople();await refresh();});
  $('hub-more').onclick=()=>action($('hub-more'),()=>refresh(next));$('hub-query').onkeydown=e=>{if(e.key==='Enter')refresh().catch(error=>notice(error.message,true));};
  for(const id of ['hub-chat','hub-person','hub-tier','hub-status','hub-kind'])$(id).onchange=()=>refresh().catch(e=>notice(e.message,true));
  $('hub-eagerness').oninput=()=>$('hub-eagerness-value').textContent=$('hub-eagerness').value+'/100';
  $('hub-save-settings').onclick=()=>action($('hub-save-settings'),async()=>{
    for(const id of ['hub-budget','hub-limit','hub-cold-days'])if(!$(id).reportValidity())return;
    await saveSubset({memory_enabled:$('hub-enabled').checked,memory_consolidation_enabled:$('hub-consolidation').checked,
      memory_token_budget:Number($('hub-budget').value),memory_item_limit:Number($('hub-limit').value),memory_cold_after_days:Number($('hub-cold-days').value),
      learning_eagerness:Number($('hub-eagerness').value),editable_memory_enabled:$('hub-ai-editing').checked,learning_auto_accept:$('hub-auto-accept').checked});notice('记忆与成长设置已保存');await refresh();});
  $('hub-edit-form').onsubmit=e=>{e.preventDefault();action($('hub-edit-save'),async()=>{
    const status=$('hub-edit-status').value;const values={op:editing?'update':'create',...(editing?{id:editing.id,version:editing.version}:{}),
      chat:$('hub-edit-chat').value,subject:$('hub-edit-person').value.trim(),kind:$('hub-edit-kind').value,scope:$('hub-edit-scope').value,
      tier:$('hub-edit-tier').value,status,venue:$('hub-edit-venue').value,locked:$('hub-edit-locked').checked,content:$('hub-edit-content').value.trim(),
      property_key:$('hub-edit-property').value.trim(),triggers:$('hub-edit-triggers').value.trim(),source_key:editing?.source_key||''};
    if(editing?.status==='forgotten'&&status==='active')throw new Error('请先在列表点“恢复”，再编辑，避免误恢复已忘记资料。');
    await api('memory-hub','POST',values);$('hub-editor').close();await refresh();notice('记忆已保存');});};
  function navigate(){if(location.hash==='#learning-panel')location.hash='#memory-panel';if(location.hash==='#memory-panel')Promise.all([loadPeople(),refresh()]).catch(e=>notice(e.message,true));}
  window.addEventListener('hashchange',navigate);
  window.memoryHub={populate,stateUpdated(){if(location.hash==='#memory-panel'&&Date.now()-lastFetch>30000)refresh().catch(e=>notice(e.message,true));}};
  if(cfg)populate();navigate();
})();
