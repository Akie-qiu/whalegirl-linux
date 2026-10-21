'use strict';
(() => {
  const el=(tag,cls,content)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(content)n.textContent=content;return n;};
  const button=(title,fn)=>{const b=el('button','secondary',title);b.type='button';b.onclick=()=>action(b,fn);return b;};
  const panel=el('section','panel span-all');panel.id='sticker-library-panel';
  panel.append(el('h2','','表情自管理'),el('p','muted','AI 可挑选新收到的原生表情，后台收藏并建立含义标签；“停用”仅移出机器人发送库，微信收藏保留，随时可以恢复。'));
  const controls={};
  for(const [name,title] of [['sticker_self_manage_enabled','允许 AI 管理表情库'],['sticker_auto_collect_enabled','自动挑选和收藏新收到的原生表情'],['sticker_auto_disable_enabled','允许 AI 停用重复或不适合的表情']]){
    const label=el('label','check-label'),input=el('input');input.type='checkbox';controls[name]=input;label.append(input,document.createTextNode(title));panel.append(label);
  }
  const grid=el('div','settings-grid');
  for(const [name,title,min,max] of [['sticker_library_limit','发送库容量',10,200],['sticker_collect_per_day','每天最多自动收藏',0,30],['sticker_disable_per_day','每天最多自动停用',0,30]]){
    const label=el('label','',title),input=el('input');input.type='number';input.min=min;input.max=max;input.step=1;controls[name]=input;label.append(input);grid.append(label);
  }
  panel.append(grid,button('保存自管理规则',async()=>{const values={};for(const [n,input] of Object.entries(controls))values[n]=input.type==='checkbox'?input.checked:Number(input.value);await saveSubset(values);notice('表情自管理规则已保存');}));
  const toolbar=el('div','chat-toolbar'),filter=el('select');for(const [v,t] of [['all','全部表情'],['enabled','可发送'],['disabled','已停用']]){const o=el('option','',t);o.value=v;filter.append(o);}
  const query=el('input');query.type='search';query.placeholder='搜索含义或表情 ID';toolbar.append(filter,query,button('查询 / 刷新',load));panel.append(toolbar);
  const count=el('p','hint'),list=el('div'),pending=el('div'),revisions=el('div');panel.append(count,list,el('h3','','新表情处理记录'),pending,el('h3','','最近管理变化'),revisions);$('sticker-profile-panel').after(panel);
  const nav=el('a','','表情自管理');nav.href='#sticker-library-panel';document.querySelector('nav').append(nav);let loaded=false,loading=false;
  const labels={pending:'等待判断',selected:'等待空闲收藏',collecting:'原生收藏核对中',collected:'已确认收藏',skipped:'本次不入库',failed:'未完成'};
  async function manage(item,op){await api('sticker-library/manage','POST',{id:item.md5,op,version:item.version,reason:'后台手动'+({enable:'恢复',disable:'停用',pin:'保护',unpin:'取消保护'}[op]||'选择入库')});await load();notice('表情状态已更新');}
  async function load(){
    if(loading)return;loading=true;
    try{
      const r=await api('sticker-library');loaded=true;list.replaceChildren();pending.replaceChildren();revisions.replaceChildren();
      count.textContent='可发送 '+r.active+' 个 · 今日自动收藏 '+r.collected_today+' 个 · 自动停用 '+r.disabled_today+' 个';
      for(const item of r.items){
        if(filter.value==='enabled'&&(!item.enabled||!item.native_available))continue;if(filter.value==='disabled'&&item.enabled)continue;
        if(query.value&&!((item.meaning+' '+item.md5).toLowerCase().includes(query.value.trim().toLowerCase())))continue;
        const card=el('article','record');card.append(el('strong','',item.meaning),el('p','hint',item.md5+' · '+(item.enabled?(item.native_available?'可发送':'原生收藏未同步'):'已停用')+(item.pinned?' · 已保护':'')));
        if(item.reason)card.append(el('p','hint','原因：'+item.reason));card.append(button(item.enabled?'停用，可恢复':'恢复到发送库',()=>manage(item,item.enabled?'disable':'enable')),button(item.pinned?'取消保护':'保护，禁止 AI 停用',()=>manage(item,item.pinned?'unpin':'pin')));list.append(card);
      }
      for(const item of r.candidates){const card=el('article','record');card.append(el('strong','',labels[item.state]||item.state),el('p','hint',chatName(item.chat)+' · '+item.md5),el('p','',item.error||item.reason||'新收到的原生表情'));
        if(['failed','skipped'].includes(item.state))card.append(button('选择入库 / 重试',()=>manage(item,'retry')));pending.append(card);}
      for(const item of r.revisions){const card=el('article','record');card.append(el('p','',({collect:'原生收藏',disable:'停用',enable:'恢复',pin:'保护',unpin:'取消保护',undo:'撤销'}[item.operation]||item.operation)+' · '+item.reason),el('p','hint',item.md5+' · '+new Date(item.created*1000).toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'})));
        if(item.operation!=='collect')card.append(button('撤销这次变化',async()=>{await api('sticker-library/revisions/'+item.id+'/undo','POST',{});await load();notice('已撤销，微信收藏保留');}));revisions.append(card);}
    }finally{loading=false;}
  }
  function populate(){if(!cfg)return;for(const [name,input] of Object.entries(controls)){if(input.type==='checkbox')input.checked=cfg[name];else input.value=cfg[name];}}
  nav.addEventListener('click',()=>load().catch(e=>notice(e.message,true)));filter.onchange=()=>load().catch(e=>notice(e.message,true));
  window.stickerLibrary={populate,stateUpdated(){if(!loaded&&cfg&&location.hash==='#sticker-library-panel')load().catch(e=>notice(e.message,true));}};if(cfg)populate();
})();
