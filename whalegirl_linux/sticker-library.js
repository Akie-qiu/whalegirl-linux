'use strict';
(() => {
  const el=(tag,cls,content)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(content!=null)n.textContent=content;return n;};
  const button=(title,fn)=>{const b=el('button','secondary',title);b.type='button';b.onclick=()=>action(b,fn);return b;};
  const select=(values)=>{const s=el('select');for(const [value,title] of values){const o=el('option','',title);o.value=value;s.append(o);}return s;};
  const stamp=t=>t?new Date(t*1000).toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'}):'尚未处理';
  const panel=el('section','panel span-all sl-panel');panel.id='sticker-library-panel';
  const heading=el('div','sl-heading');heading.append(el('div','',null),el('span','pill','原生表情 · 可恢复管理'));
  heading.firstChild.append(el('span','section-index','STICKER STUDIO'),el('h2','','表情工作室'),el('p','muted','看懂每张表情，也决定它从哪里学、在哪里用'));
  panel.append(heading);
  const status=el('p','sl-status','正在读取表情库');panel.append(status);
  const rules=el('details','sl-section'),ruleSummary=el('summary','','自主性与处理规则');rules.append(ruleSummary);
  const controls={};
  const check=(name,title,parent)=>{const label=el('label','check-label'),input=el('input');input.type='checkbox';controls[name]=input;label.append(input,document.createTextNode(title));parent.append(label);};
  for(const [name,title] of [['sticker_self_manage_enabled','允许 AI 管理表情库'],['sticker_auto_collect_enabled','自动挑选新收到的表情'],['sticker_command_collect_enabled','允许根据聊天指令申请入库'],['sticker_command_disable_enabled','允许根据聊天指令停用 / 恢复本会话表情'],['sticker_auto_disable_enabled','允许 AI 自主停用全局重复表情'],['sticker_cache_cleanup_enabled','识别结果保存后清理临时原件缓存']])check(name,title,rules);
  const grid=el('div','settings-grid');
  for(const [name,title,min,max] of [['sticker_library_limit','可发送表情容量',10,200],['sticker_collect_per_day','每天最多自动 / 指令收藏',0,30],['sticker_disable_per_day','每天最多 AI 停用',0,30],['sticker_failed_cache_hours','失败缓存保留小时',1,168]]){
    const label=el('label','',title),input=el('input');input.type='number';input.min=min;input.max=max;input.step=1;controls[name]=input;label.append(input);grid.append(label);
  }
  const aggression=select([['low','谨慎 · 精选补缺'],['normal','适中 · 贴合人设'],['high','积极 · 多样表达']]);controls.sticker_collect_aggressiveness=aggression;
  const aggressionLabel=el('label','','入库积极性');aggressionLabel.append(aggression);grid.append(aggressionLabel);rules.append(grid);
  rules.append(el('p','hint','聊天指令只调整所在会话；管理员可选择全局停用。保护后的表情不允许 AI 停用。临时缓存清理不涉及微信聊天媒体、原生收藏或持久预览。'));
  rules.append(button('保存自主性规则',async()=>{const values={};for(const [n,input] of Object.entries(controls))values[n]=input.type==='checkbox'?input.checked:input.type==='number'?Number(input.value):input.value;await saveSubset(values);dirty=false;await load();notice('表情自主性规则已保存');}));panel.append(rules);
  const permissions=el('details','sl-section');permissions.append(el('summary','','来源会话权限'),el('p','hint','分别控制群聊、私聊。未单独修改的会话继承全局开关；禁止从此会话入库，不会阻止发送已有表情。管理员在后台手动选择入库仍可操作。'));
  const permissionList=el('div','sl-permissions');permissions.append(permissionList);
  let permissionInputs=new Map(),dirty=false;
  permissions.append(button('保存会话权限',async()=>{const values={...(cfg.sticker_chat_permissions||{})};for(const [chat,fields] of permissionInputs)values[chat]=Object.fromEntries(Object.entries(fields).map(([k,v])=>[k,v.checked]));await saveSubset({sticker_chat_permissions:values});dirty=false;await load();notice('会话权限已保存，正在排队的任务也会遵守新规则');}));panel.append(permissions);
  const toolbar=el('div','sl-toolbar'),filter=select([['all','全部表情'],['enabled','可发送'],['disabled','已停用'],['unavailable','尚未收藏']]),scope=select([['','全局发送库']]);
  const query=el('input');query.type='search';query.placeholder='搜索含义、画面或表情 ID';query.setAttribute('aria-label','搜索表情');scope.setAttribute('aria-label','发送库作用范围');
  toolbar.append(query,filter,scope,button('刷新',load));panel.append(el('h3','','表情库'),toolbar);
  const list=el('div','sl-grid');panel.append(list);
  const queueHead=el('div','sl-heading');queueHead.append(el('h3','','新表情处理队列'));
  const stateFilter=select([['active','待处理 / 失败'],['all','全部处理记录'],['pending','等待识别'],['selected','等待收藏'],['failed','失败'],['skipped','未入库'],['collected','已收藏']]);queueHead.append(stateFilter);panel.append(queueHead);
  const batchbar=el('div','sl-toolbar'),selectedIds=new Set(),batchCount=el('span','hint','未选择');
  async function batch(op){if(!selectedIds.size)throw new Error('请先选择候选表情');if(selectedIds.size>40)throw new Error('每次最多选择 40 个');const r=await api('sticker-library/batch','POST',{op,ids:[...selectedIds]});const failures=r.results.filter(x=>!x.ok);selectedIds.clear();await load();notice('处理成功 '+r.results.filter(x=>x.ok).length+' 个'+(failures.length?'；未完成 '+failures.length+' 个：'+failures[0].error:''),!!failures.length);}
  batchbar.append(batchCount,button('选中可处理项',async()=>{pending.querySelectorAll('input[type=checkbox]').forEach(i=>{i.checked=true;selectedIds.add(i.value);});batchCount.textContent='已选 '+selectedIds.size+' 个';}),button('批量选择入库 / 重试',()=>batch('retry')),button('批量跳过',()=>batch('skip')));panel.append(batchbar);
  const pending=el('div','sl-grid');panel.append(pending);
  const history=el('details','sl-section');history.append(el('summary','','最近管理变化 · 可撤销'));const revisions=el('div');history.append(revisions);panel.append(history);
  const old=$('sticker-profile-panel');old.hidden=true;old.after(panel);
  const nav=el('a','','表情工作室');nav.href='#sticker-library-panel';document.querySelector('nav').append(nav);
  let loaded=false,loading=false,lastLoad=0,snapshot=null;
  const labels={pending:'等待识别',analyzing:'正在识别',selected:'已识别 · 等待收藏',collecting:'原生收藏核对中',collected:'已确认收藏',skipped:'本次不入库',failed:'未完成'};
  const fieldNames={meaning:'简洁含义',visual:'画面与动作',text:'实际图中文字',emotion:'情绪',intensity:'情绪强度',tone:'语气与反讽',use_when:'适用场景',avoid_when:'避免使用的场景',example:'适合回应的话',persona_fit:'人设契合点',usage_scope:'群聊 / 私聊使用边界'};
  const previewCache=new Map(),previewJobs=[];let previewsRunning=0;
  async function runPreviews(){if(previewsRunning>=2||!previewJobs.length)return;previewsRunning++;const {id,img,placeholder}=previewJobs.shift();try{let data=previewCache.get(id);if(!data){data=(await api('sticker-library/preview/'+id)).data;if(data)previewCache.set(id,data);}if(data){img.src=data;img.hidden=false;placeholder.hidden=true;}}catch{placeholder.textContent='预览暂不可用';}finally{previewsRunning--;runPreviews();}}
  const observer=new IntersectionObserver(entries=>{for(const item of entries)if(item.isIntersecting){observer.unobserve(item.target);previewJobs.push(item.target.slPreview);runPreviews();}},{rootMargin:'120px'});
  function preview(id){const box=el('div','sl-preview'),img=el('img'),placeholder=el('span','hint','加载预览');img.alt='原生表情预览';img.hidden=true;box.append(img,placeholder);box.slPreview={id,img,placeholder};observer.observe(box);return box;}
  async function manage(item,op,chat=''){await api('sticker-library/manage','POST',{id:item.md5,op,chat,version:item.version,reason:'后台手动'+({enable:'恢复',disable:'停用',pin:'保护',unpin:'取消保护',skip:'跳过'}[op]||'选择入库')});await load();notice('表情状态已更新');}
  function editor(item){
    const details=el('details','sl-editor');details.append(el('summary','','编辑语义档案'));
    const fields=el('div','sl-fields'),inputs={};
    for(const [key,title] of Object.entries(fieldNames)){const label=el('label','',title),input=el('textarea');input.rows=2;input.maxLength=400;input.value=item.profile?.[key]||(key==='meaning'?item.meaning:'');inputs[key]=input;label.append(input);fields.append(label);}
    details.append(fields);
    const lockLabel=el('label','check-label'),locked=el('input');locked.type='checkbox';locked.checked=item.profile?.locked??true;lockLabel.append(locked,document.createTextNode('锁定管理员设定，AI 不覆盖'));details.append(lockLabel);
    details.append(button('保存档案',async()=>{const values=Object.fromEntries(Object.entries(inputs).map(([k,v])=>[k,v.value]));await api('stickers/'+item.md5,'POST',{...values,locked:locked.checked,version:item.profile?.version||0});dirty=false;await load();notice('表情档案已保存，可在管理变化中撤销');}));return details;
  }
  function render(){
    if(!snapshot)return;const r=snapshot;observer.disconnect();previewJobs.length=0;list.replaceChildren();pending.replaceChildren();revisions.replaceChildren();
    const waiting=r.candidates.filter(c=>!['skipped','collected'].includes(c.state)).length;
    status.textContent='可发送 '+r.active+' 个 · 待处理 '+waiting+' 个 · 今日自动收藏 '+r.collected_today+' / '+(cfg?.sticker_collect_per_day??6)+' · '+(r.worker_busy?'后台处理中':'最近处理 '+stamp(r.last_work));
    const states=new Map((r.chat_states||[]).filter(x=>x.chat===scope.value).map(x=>[x.md5,x]));let shown=0;
    for(const item of r.items){const local=scope.value?(states.get(item.md5)||{...item,chat:scope.value,enabled:1,version:0}):item;const enabled=!!item.enabled&&!!local.enabled&&item.native_available;
      if(filter.value==='enabled'&&!enabled||filter.value==='disabled'&&item.enabled&&local.enabled||filter.value==='unavailable'&&item.native_available)continue;
      if(query.value&&!JSON.stringify([item.meaning,item.profile,item.md5]).toLowerCase().includes(query.value.trim().toLowerCase()))continue;
      const card=el('article','sl-card'),top=el('div','sl-card-top'),intro=el('div');intro.append(el('strong','',item.meaning),el('span','sl-badge',!item.enabled?'全局已停用':!local.enabled?'本会话已停用':!item.native_available?'已标注 · 未收藏':'可发送'));if(item.pinned)intro.append(el('span','sl-badge','已保护'));top.append(preview(item.md5),intro);card.append(top,el('p','sl-id',item.md5));
      if(item.profile?.use_when)card.append(el('p','hint','适合：'+item.profile.use_when));
      const actions=el('div','sl-actions');actions.append(button(local.enabled?'停用，可恢复':'恢复到发送库',()=>manage({...local,md5:item.md5},local.enabled?'disable':'enable',scope.value)),button(item.pinned?'取消保护':'保护',()=>manage(item,item.pinned?'unpin':'pin')));card.append(actions,editor(item));list.append(card);shown++;
    }
    if(!shown)list.append(el('p','muted','没有符合条件的表情'));
    for(const item of r.candidates){if(stateFilter.value==='active'&&['collected','skipped'].includes(item.state)||!['active','all'].includes(stateFilter.value)&&stateFilter.value!==item.state)continue;
      const card=el('article','sl-card'),top=el('div','sl-card-top'),intro=el('div');intro.append(el('strong','',item.profile?.meaning||labels[item.state]||item.state),el('span','sl-badge',labels[item.state]||item.state));top.append(preview(item.md5),intro);card.append(top,el('p','hint',chatName(item.chat)+' · '+({auto:'自主发现',command:'聊天指令',admin:'管理员选择'}[item.origin]||item.origin)),el('p','sl-id',item.md5));
      if(item.wait_reason)card.append(el('p','hint',item.wait_reason));if(item.reason)card.append(el('p','',item.reason));if(item.error)card.append(el('p','error-text',item.error));
      if(!['analyzing','collecting','collected'].includes(item.state)){const actions=el('div','sl-actions'),label=el('label','check-label'),input=el('input');input.type='checkbox';input.value=item.md5;input.checked=selectedIds.has(item.md5);input.onchange=()=>{input.checked?selectedIds.add(item.md5):selectedIds.delete(item.md5);batchCount.textContent='已选 '+selectedIds.size+' 个';};label.append(input,document.createTextNode('选择'));actions.append(label,button('选择入库 / 重试',()=>manage(item,'retry')),button('跳过',()=>manage(item,'skip')));card.append(actions);}pending.append(card);
    }
    if(!pending.children.length)pending.append(el('p','muted','当前筛选下没有候选表情'));batchCount.textContent=selectedIds.size?'已选 '+selectedIds.size+' 个':'未选择';
    for(const item of r.revisions){const card=el('article','record');card.append(el('p','',({collect:'原生收藏',disable:'停用',enable:'恢复',pin:'保护',unpin:'取消保护',profile:'编辑档案',undo:'撤销'}[item.operation]||item.operation)+' · '+item.reason),el('p','sl-id',item.md5+' · '+stamp(item.created)));if(item.operation!=='collect')card.append(button('撤销这次变化',async()=>{await api('sticker-library/revisions/'+item.id+'/undo','POST',{});await load();notice('已撤销');}));revisions.append(card);}
  }
  async function load(){if(loading)return;loading=true;try{snapshot=await api('sticker-library');loaded=true;lastLoad=Date.now();render();}finally{loading=false;}}
  function populate(){if(!cfg)return;for(const [name,input] of Object.entries(controls)){if(input.type==='checkbox')input.checked=cfg[name];else input.value=cfg[name];}
    const previous=scope.value;scope.replaceChildren();for(const [value,title] of [['','全局发送库'],...(cfg.allowed_chat_ids||[]).map(id=>[id,chatName(id)])]){const o=el('option','',title);o.value=value;scope.append(o);}scope.value=previous;
    permissionList.replaceChildren();permissionInputs=new Map();for(const chat of cfg.allowed_chat_ids||[]){const card=el('article','sl-permission');card.dataset.chat=chat;card.append(el('strong','',chatName(chat)),el('span','hint',chat.endsWith('@chatroom')?'群聊':'私聊'));const fields={};for(const [key,title] of [['auto_collect','自动入库'],['command_collect','指令入库'],['command_disable','指令停用 / 恢复']]){const label=el('label','check-label'),input=el('input');input.type='checkbox';input.checked=cfg.sticker_chat_permissions?.[chat]?.[key]??true;fields[key]=input;label.append(input,document.createTextNode(title));card.append(label);}permissionInputs.set(chat,fields);permissionList.append(card);}}
  panel.addEventListener('input',e=>{if(![query,filter,scope,stateFilter].includes(e.target))dirty=true;});
  nav.addEventListener('click',()=>load().catch(e=>notice(e.message,true)));filter.onchange=scope.onchange=stateFilter.onchange=render;query.oninput=render;
  window.stickerLibrary={populate,load,refreshChatLabels(){for(const card of permissionList.children)card.querySelector('strong').textContent=chatName(card.dataset.chat);for(const option of scope.options)if(option.value)option.textContent=chatName(option.value);},stateUpdated(){if(!cfg||location.hash!=='#sticker-library-panel')return;if(!loaded||(!dirty&&Date.now()-lastLoad>20000&&!panel.querySelector('.sl-editor[open]')))load().catch(e=>notice(e.message,true));}};
  if(cfg)populate();
})();