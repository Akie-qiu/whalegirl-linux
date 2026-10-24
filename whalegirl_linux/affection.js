'use strict';
(() => {
  const el=(tag,cls,content)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(content)e.textContent=content;return e;};
  const button=(title,fn)=>{const b=el('button','secondary',title);b.type='button';b.onclick=()=>action(b,fn);return b;};
  const panel=el('section','panel span-all');panel.id='affection-panel';
  panel.append(el('h2','','人物好感度'),el('p','muted','同一微信 ID 在所有已授权群和私聊共用好感度。群聊只表达朋友式关心；私聊可更亲近，个人事实与偏好继续按原规则共享。分数仅后台可见。'));
  const enabled=el('input');enabled.id='affection-enabled';enabled.type='checkbox';enabled.className='toggle';enabled.setAttribute('aria-label','好感度系统总开关');
  const enabledLabel=el('div','setting-row'),enabledCopy=el('div');enabledCopy.append(el('strong','','好感度系统总开关'),el('p','','切换后自动保存。关闭后停止自动计分，也不按好感度调整语气和记忆优先级；已有分数与记录保留。'));
  enabledLabel.append(enabledCopy,enabled);
  const enabledStatus=el('p','hint');enabledStatus.id='affection-enabled-status';enabledStatus.setAttribute('aria-live','polite');
  const privateStyle=el('select');for(const [v,t] of [['close','熟悉后可更亲近，跟随对方语气'],['warm','始终保持温暖朋友语气']]){const o=el('option','',t);o.value=v;privateStyle.append(o);}
  const privateLabel=el('label','','私聊表达');privateLabel.append(privateStyle);
  const settingsBox=el('details','record');settingsBox.append(el('summary','','增长速度与初始值'));
  const inputs={};for(const [name,title,min,max] of [
    ['affection_initial_score','未建立记录的新人物初始好感度',0,100],
    ['affection_daily_gain','每人每天最多增加',0,20],['affection_daily_loss','每人每天最多降低',0,20],
    ['affection_cooldown_seconds','同一人物互动计分最短间隔 / 秒',60,86400]]){
    const label=el('label','',title),input=el('input');input.type='number';input.min=min;input.max=max;input.step=1;inputs[name]=input;label.append(input);settingsBox.append(label);
  }
  settingsBox.append(el('p','hint','只累计新发生的有效交流，不按消息条数刷分；离线和久未聊天不扣分。初始值只影响尚未建立记录的人物。'));
  panel.append(enabledLabel,enabledStatus,privateLabel,settingsBox,button('保存好感度规则',async()=>{
    const values={affection_private_style:privateStyle.value};
    for(const [n,input] of Object.entries(inputs))values[n]=Number(input.value);
    await saveSubset(values);await load();notice('好感度规则已保存');
  }));
  const toolbar=el('div','chat-toolbar'),query=el('input');query.type='search';query.placeholder='搜索昵称或真实微信 ID';query.setAttribute('aria-label','搜索好感度人物');
  toolbar.append(query,button('查询 / 刷新',load));const count=el('p','hint'),list=el('div');panel.append(toolbar,count,list);$('memory-panel').after(panel);
  const nav=el('a','','人物好感度');nav.href='#affection-panel';document.querySelector('nav').append(nav);
  let loaded=false,loading=false,toggling=false;
  function syncEnabled(){
    if(!cfg)return;enabled.checked=!!cfg.affection_enabled;
    const quick=$('quick-affection-enabled');if(quick)quick.checked=enabled.checked;
    enabledStatus.textContent=enabled.checked?'当前：已启用 · 自动计分及好感度个性化生效':'当前：已关闭 · 不自动计分或使用好感度权重；普通聊天与记忆继续工作，重新开启可沿用原有分数';
  }
  async function toggle(control){
    if(toggling)return;toggling=true;
    const value=control.checked,controls=[enabled,$('quick-affection-enabled')].filter(Boolean);
    for(const input of controls)input.disabled=true;
    try{await saveSubset({affection_enabled:value});syncEnabled();notice(value?'好感度系统已开启':'好感度系统已关闭，已有分数和记录保留');}
    catch(error){syncEnabled();notice(error.message,true);}
    finally{toggling=false;for(const input of controls)input.disabled=false;}
  }
  enabled.onchange=()=>toggle(enabled);
  function populate(){if(!cfg)return;syncEnabled();privateStyle.value=cfg.affection_private_style;for(const [n,input] of Object.entries(inputs))input.value=cfg[n];}
  async function history(person,box){
    const r=await api('affection/revisions?subject='+encodeURIComponent(person.subject));box.replaceChildren();
    if(!r.revisions.length)box.append(el('p','hint','还没有变化记录'));
    for(const revision of r.revisions){
      const row=el('article','record');row.append(el('p','',revision.before_score+' → '+revision.after_score+' · '+revision.reason),
        el('p','hint',new Date(revision.created*1000).toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'})+' · '+(revision.chat?chatName(revision.chat):'后台调整')));
      row.append(button('撤销这次变化',async()=>{await api('affection/revisions/'+revision.id+'/undo','POST',{});await load();notice('已撤销，历史记录仍保留');}));box.append(row);
    }
  }
  async function load(){
    if(loading)return;loading=true;
    try{
      const r=await api('affection?q='+encodeURIComponent(query.value.trim()));loaded=true;list.replaceChildren();
      count.textContent='显示 '+r.people.length+' 位最近参与已授权会话的人物 · 好感度跨群 / 私聊共用';
      for(const person of r.people){
        const card=el('article','record');card.append(el('strong','',person.name+' · '+person.stage),el('p','hint',person.subject+' · 出现在 '+person.chat_count+' 个已授权会话'));
        const row=el('div','two-fields'),score=el('input');score.type='number';score.min=0;score.max=100;score.step=1;score.value=person.score;score.setAttribute('aria-label',person.name+'的好感度');
        const scoreLabel=el('label','','好感度 / 100');scoreLabel.append(score);
        const locked=el('input');locked.type='checkbox';locked.checked=!!person.locked;const lockLabel=el('label','check-label');lockLabel.append(locked,document.createTextNode('锁定分数，暂停自动变化'));row.append(scoreLabel,lockLabel);card.append(row);
        card.append(button('保存这个人物',async()=>{await api('affection','POST',{subject:person.subject,version:person.version,score:Number(score.value),locked:locked.checked,reason:'后台手动调整'});await load();notice('人物好感度已保存');}));
        const details=el('details');details.append(el('summary','','最近变化原因'));const revisions=el('div');details.append(revisions);details.ontoggle=()=>{if(details.open&&!details.dataset.loaded){details.dataset.loaded='true';history(person,revisions).catch(e=>{details.dataset.loaded='';notice(e.message,true);});}};card.append(details);list.append(card);
      }
    }finally{loading=false;}
  }
  nav.addEventListener('click',()=>load().catch(e=>notice(e.message,true)));
  window.affection={populate,toggle,stateUpdated(){if(!loaded&&cfg&&location.hash==='#affection-panel')load().catch(e=>notice(e.message,true));}};
  if(cfg)populate();
})();
