'use strict';
(() => {
  const section=document.createElement('details');section.className='settings-drawer';section.id='growth-console';
  section.innerHTML=`<style>#growth-console .message{overflow-wrap:anywhere}#growth-console .two-fields>*{min-width:0}#growth-detail,#growth-editor{max-height:85vh;overflow:auto}</style><summary>自主学习与人格成长</summary>
    <p class="hint">自己选题、阅读公开内容，形成有来源的认识；整理长期记忆不会裁掉近期聊天。学习不自动发送消息。</p>
    <div class="two-fields">
      <label class="check-label"><input id="growth-enabled" type="checkbox">启用自主成长与分层记忆</label>
      <label class="check-label"><input id="growth-auto" type="checkbox">自动启用新学到的认识</label>
      <label class="check-label"><input id="growth-web" type="checkbox">从公开网页自主学习</label>
      <label class="check-label"><input id="growth-tieba" type="checkbox">从贴吧原文学习（默认关闭）</label>
      <label>每日学习 Token 预算<input id="growth-budget" type="number" min="0" max="500000" step="1000"></label>
      <label>每日最多自主学习轮次<input id="growth-reads" type="number" min="0" max="20"></label>
    </div>
    <details><summary>兴趣与学习节奏</summary><div class="two-fields">
      <label>后台学习间隔 / 分钟<input id="growth-interval" type="number" min="30" max="1440"></label>
      <label>探索新主题积极性<input id="growth-explore" type="range" min="0" max="100"><output id="growth-explore-value"></output></label>
    </div><label>兴趣方向（一行一个，留空沿用现有兴趣）<textarea id="growth-interests" rows="3"></textarea></label></details>
    <p class="hint">网页来源同时遵守联网搜索总开关。贴吧选项只控制成长取材。预算按实际回执计入，未提供用量时保守估算；关闭自动启用只影响新认识，旧认识可单独停用。</p>
    <div class="panel-footer"><span id="growth-status" class="hint" role="status"></span><button id="growth-save" class="primary" type="button">保存成长设置</button></div>
    <div class="row" style="margin:12px 0"><button id="growth-run" class="primary" type="button">立即自主学习</button><button id="growth-compact" class="secondary" type="button">整理同类记忆</button><button id="growth-refresh" class="secondary" type="button">刷新记录</button></div>
    <h3>逐渐形成的认识</h3><div id="growth-insights" class="bento-grid"></div>
    <details><summary>阅读与反思记录</summary><div id="growth-journals"></div></details>
    <details><summary>概括与深层记忆</summary><div id="growth-summaries"></div></details>
    <details><summary>任务与用量记录</summary><div id="growth-runs"></div></details>
    <dialog id="growth-detail" class="tieba-archive-dialog"><div class="section-top"><h3 id="growth-detail-title">来源与反思</h3><button id="growth-detail-close" type="button" class="secondary">关闭</button></div><div id="growth-detail-body"></div></dialog>
    <dialog id="growth-editor" class="tieba-archive-dialog"><form id="growth-edit-form"><div class="section-top"><h3>编辑成长认识</h3><button id="growth-edit-close" class="secondary" type="button">关闭</button></div>
      <label>认识内容<textarea id="growth-edit-content" rows="5" maxlength="700" required></textarea></label>
      <label class="check-label"><input id="growth-edit-locked" type="checkbox">锁定，后台不能替代这条认识</label>
      <p class="hint">来源与修订保留；停用后立即不再用于聊天。</p><button id="growth-edit-save" class="primary" type="submit">保存修改</button></form></dialog>`;
  $('memory-panel').append(section);
  let loading=false,lastFetch=0,editing=null;
  const el=(tag,cls='',text='')=>{const n=document.createElement(tag);n.className=cls;n.textContent=text;return n;};
  const button=(title,fn)=>{const b=el('button','secondary',title);b.type='button';b.onclick=()=>action(b,fn);return b;};
  const when=n=>n?new Date(n*1000).toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'}):'未记录';
  const states={queued:'排队中',planning:'选择与整理',reading:'读取来源',reflecting:'理解与反思',done:'已完成',failed:'未完成',skipped:'本轮跳过',interrupted:'已中止'};
  function populate(){
    if(!cfg)return;
    $('growth-enabled').checked=cfg.growth_enabled??true;$('growth-auto').checked=cfg.growth_auto_apply??true;
    $('growth-web').checked=cfg.growth_web_enabled??true;$('growth-tieba').checked=cfg.growth_tieba_enabled??false;
    $('growth-budget').value=cfg.growth_daily_tokens??20000;$('growth-reads').value=cfg.growth_reads_per_day??3;
    $('growth-interval').value=cfg.growth_interval_minutes??180;$('growth-explore').value=cfg.growth_exploration??25;
    $('growth-explore-value').textContent=$('growth-explore').value+'/100';$('growth-interests').value=(cfg.growth_interests||[]).join('\n');
    if(section.open)refresh().catch(e=>notice(e.message,true));
  }
  async function showJournal(id){
    const j=await api('growth/journals/'+encodeURIComponent(id));$('growth-detail-title').textContent=j.topic||'来源与反思';
    const box=$('growth-detail-body');box.replaceChildren();
    const labels={encounter:'遇见了什么',facts:'材料事实',author_view:'作者观点',inference:'推测与不确定性',previous_judgment:'原来的理解',revision:'新的认识',missing:'未覆盖的部分'};
    for(const [k,label] of Object.entries(labels))if(j.body[k])box.append(el('h3','',label),el('p','message',j.body[k]));
    for(const s of j.sources){const d=el('details','record'),a=el('a','',s.title||'来源');a.href=s.url;a.target='_blank';a.rel='noopener noreferrer';
      d.append(el('summary','',s.title||'来源正文'),a,el('p','message',s.body));box.append(d);}
    $('growth-detail').showModal();
  }
  function edit(row){editing=row;$('growth-edit-content').value=row.content;$('growth-edit-locked').checked=!!row.locked;$('growth-editor').showModal();}
  async function refresh(){
    if(!cfg||loading)return;loading=true;
    try{
      const s=await api('growth'),u=s.usage;
      $('growth-status').textContent=(s.enabled?(s.busy?'正在后台学习':'等待空闲学习'):'自主成长已关闭')+' · 今日 '+u.tokens.toLocaleString('zh-CN')+' / '+u.budget.toLocaleString('zh-CN')+' Token · 阅读 '+u.reads+' / '+u.read_limit+(u.estimated?' · 含估算用量':'')+(s.last_error?' · '+s.last_error:'')+' · 下次阅读 '+when(s.schedule.world_at);
      const notes=$('growth-insights');notes.replaceChildren();
      for(const r of s.insights){const card=el('article','record');card.append(el('p','hint','#'+r.id+' · '+(r.category==='knowledge'?'新知识':'人格认识')+' · '+({active:'生效',pending:'待启用',forgotten:'已停用',superseded:'已被新认识替代',expired:'已过期'}[r.status]||r.status)+(r.locked?' · 已锁定':'')),el('p','message',r.content));
        if(r.conditions)card.append(el('p','hint','适用：'+r.conditions));if(r.counterexamples)card.append(el('p','hint','例外：'+r.counterexamples));
        const tools=el('div','row');tools.append(button('编辑',async()=>edit(r)),button('来源与反思',()=>showJournal(r.journal_id)));
        if(['active','pending','forgotten'].includes(r.status))tools.append(button(r.status==='forgotten'?'恢复':r.status==='pending'?'启用':'停用',async()=>{
          await api('growth/insights/'+r.id,'POST',{op:r.status==='forgotten'?'restore':r.status==='pending'?'update':'archive',version:r.version,...(r.status==='pending'?{status:'active'}:{})});await refresh();
        }));card.append(tools);notes.append(card);}
      if(!s.insights.length)notes.append(el('p','hint','有值得保留的认识才会出现在这里，不要求每天凑数。'));
      const journals=$('growth-journals');journals.replaceChildren();for(const j of s.journals){const row=el('article','record');row.append(el('strong','',j.topic||'一次外界阅读'),el('p','hint',when(j.created)),el('p','message',j.body.revision||j.body.encounter||'没有强行形成新判断'),button('查看反思与已读来源',()=>showJournal(j.id)));journals.append(row);}
      const summaries=$('growth-summaries');summaries.replaceChildren();for(const r of s.summaries){const row=el('article','record');row.append(el('p','hint',(r.valid?'概括有效':'来源已变化，概括停止使用')+' · '+chatName(r.chat)+' · '+r.subject),el('p','message',r.summary),el('p','hint','深层记忆 ID：'+r.detail_ids.join('、')));
        row.append(button('展开深层细节',async()=>{const box=$('growth-detail-body');box.replaceChildren();$('growth-detail-title').textContent='概括与原始细节';
          for(const id of r.detail_ids){const d=await api('memory-hub/'+id);box.append(el('h3','','记忆 #'+id),el('p','message',d.memory.content),el('p','hint','版本 '+d.memory.version+' · '+when(d.memory.updated)));}
          $('growth-detail').showModal();}));summaries.append(row);}
      const runs=$('growth-runs');runs.replaceChildren();for(const r of s.runs){const row=el('article','record');row.append(el('strong','',(r.kind==='world'?'外界学习':'同类记忆整理')+' · '+(states[r.state]||r.state)),el('p','hint',when(r.created)+' · '+r.tokens+' Token'+(r.estimated?'（含估算）':'')),el('p','hint',r.error||r.result.reason||r.result.topic||('概括 '+(r.result.summaries??0)+' 组 · 新认识 '+(r.result.learned??0)+' 条')));runs.append(row);}
      lastFetch=Date.now();
    }finally{loading=false;}
  }
  $('growth-save').onclick=()=>action($('growth-save'),async()=>{
    for(const id of ['growth-budget','growth-reads','growth-interval'])if(!$(id).reportValidity())return;
    await saveSubset({growth_enabled:$('growth-enabled').checked,growth_auto_apply:$('growth-auto').checked,
      growth_web_enabled:$('growth-web').checked,growth_tieba_enabled:$('growth-tieba').checked,
      growth_daily_tokens:Number($('growth-budget').value),growth_reads_per_day:Number($('growth-reads').value),growth_interval_minutes:Number($('growth-interval').value),
      growth_exploration:Number($('growth-explore').value),growth_interests:$('growth-interests').value.split('\n').map(s=>s.trim()).filter(Boolean)});
    notice('自主成长设置已保存');await refresh();
  });
  $('growth-explore').oninput=()=>$('growth-explore-value').textContent=$('growth-explore').value+'/100';
  for(const [id,kind] of [['growth-run','world'],['growth-compact','memory']])$(id).onclick=()=>action($(id),async()=>{await api('growth/run','POST',{kind});notice('任务已排队，将在空闲时执行');await refresh();});
  $('growth-refresh').onclick=()=>action($('growth-refresh'),refresh);
  $('growth-detail-close').onclick=()=>$('growth-detail').close();$('growth-edit-close').onclick=()=>$('growth-editor').close();
  $('growth-edit-form').onsubmit=e=>{e.preventDefault();action($('growth-edit-save'),async()=>{await api('growth/insights/'+editing.id,'POST',{op:'update',version:editing.version,content:$('growth-edit-content').value.trim(),locked:$('growth-edit-locked').checked});$('growth-editor').close();await refresh();});};
  section.addEventListener('toggle',()=>{if(section.open&&cfg)refresh().catch(e=>notice(e.message,true));});
  window.growthConsole={populate,stateUpdated(){if(section.open&&location.hash==='#memory-panel'&&Date.now()-lastFetch>30000)refresh().catch(e=>notice(e.message,true));}};
  if(cfg)populate();
})();
