'use strict';
(() => {
  const element=(tag,cls,content) => {const e=document.createElement(tag);if(cls)e.className=cls;if(content)e.textContent=content;return e;};
  const button=(title,fn) => {const b=element('button','secondary',title);b.type='button';b.onclick=()=>action(b,fn);return b;};
  const styleFields={reply_warmth:'回复温度',sticker_after_text:'文字后配表情',colloquialism:'口语程度',teasing:'吐槽强度',persona_density:'人设浓度',followup_questions:'反问频率',reply_length:'默认句长'};
  const styleOptions={reply_warmth:[['low','平和简洁'],['normal','自然亲切'],['high','温暖热络']],sticker_after_text:[['off','关闭主动搭配'],['normal','适中 · 合适时搭配'],['high','积极 · 优先文字后配表情']],reply_length:[['short','简短'],['normal','适中'],['detailed','充分解释']]};
  const styleBox=element('div','settings-grid');
  for(const [name,title] of Object.entries(styleFields)) {
    const label=element('label','',title), select=element('select');select.id='style-'+name;
    for(const [value,text] of styleOptions[name] || [['low','低'],['normal','适中'],['high','高']]) {
      const o=element('option','',text);o.value=value;select.append(o);
    }
    label.append(select);styleBox.append(label);
  }
  const stickerCooldownLabel=element('label','','同一表情候选冷却 / 秒'),stickerCooldown=element('input');
  stickerCooldown.type='number';stickerCooldown.id='style-sticker-cooldown';stickerCooldown.min=0;stickerCooldown.max=3600;stickerCooldown.step=1;
  stickerCooldownLabel.append(stickerCooldown,element('span','hint','近期发过的同一表情暂不参与候选；运行保护中的重复内容限制仍独立生效。'));styleBox.append(stickerCooldownLabel);
  const pairingHint=element('p','hint','回复温度控制亲切感，群聊仍保持朋友语气。文字后配表情控制搭配意愿，不是保证每条都发；每轮上限至少为 2 才能同时发文字和表情。');
  styleBox.append(pairingHint);
  styleBox.append(button('保存说话风格',async()=>{
    const values={};for(const n of Object.keys(styleFields))values[n]=$('style-'+n).value;
    if(!stickerCooldown.checkValidity())throw new Error('同一表情候选冷却需在 0～3600 秒之间');
    values.sticker_cooldown_seconds=Number(stickerCooldown.value);
    await saveSubset(values);notice('说话风格和配表情设置已保存，下一轮生效');
  }));
  $('save-habits').parentElement.before(styleBox);
  const contextLabel=element('label','','上下文字符预算');const budget=element('input');budget.type='number';budget.id='context-char-budget';budget.min=4000;budget.max=40000;contextLabel.append(budget);
  const summaryLabel=element('label','check-label');const summary=element('input');summary.type='checkbox';summary.id='digest-enabled';summaryLabel.append(summary,document.createTextNode('后台增量整理话题摘要'));
  styleBox.append(contextLabel,summaryLabel,button('保存上下文策略',async()=>{await saveSubset({context_char_budget:Number(budget.value),background_digest_enabled:summary.checked});notice('上下文策略已保存');}));
  const shared=$('global-memory').parentElement;shared.lastChild.textContent='共享相同微信 ID 的个人事实与偏好（群事件独立）';
  $('memory-panel').querySelector('.muted').textContent='群内事件、梗、关系按会话独立；相同微信 ID 的明确个人事实与偏好可跨群和私聊调用。每条保留来源和修订。';
  const filter=element('select');filter.id='memory-filter-chat';filter.setAttribute('aria-label','记忆来源会话');$('memory-search').before(filter);
  filter.onchange=()=>window.habits.refresh().catch(e=>notice(e.message,true));
  const scopeLabel=element('label','','记忆使用范围'),scope=element('select');scope.id='memory-scope';
  for(const [v,t] of [['chat','仅本群 / 本私聊'],['profile','共享人物事实与偏好']]){const o=element('option','',t);o.value=v;scope.append(o);}scopeLabel.append(scope);$('memory-content').parentElement.before(scopeLabel);
  const event=element('option','','本群事件 / 梗');event.value='event';$('memory-kind').append(event);
  const autoLabel=element('label','check-label'),auto=element('input');auto.type='checkbox';auto.id='memory-auto-capture';autoLabel.append(auto,document.createTextNode('后台主动整理明确偏好和事实'));
  $('save-memory-switch').before(autoLabel);
  $('save-memory-switch').onclick=()=>action($('save-memory-switch'),async()=>{await saveSubset({editable_memory_enabled:$('editable-memory-enabled').checked,memory_auto_capture:auto.checked,memory_scope_mode:'shared_profiles'});notice('记忆策略已保存');});
  $('refresh-memory').after(button('导出所选群记忆',async()=>{
    const chat=filter.value || $('memory-chat').value;if(!chat)throw new Error('请先选择会话');
    const data=await api('memory/export?chat='+encodeURIComponent(chat));download(data,'wechat-memory.json');
  }));
  const inbox=element('section','panel span-all');inbox.id='inbox-panel';
  inbox.append(element('h2','','完整收件记录'),element('p','muted','按群查看已收到的全部消息，包含旁观、历史、图片与引用；已同步到 Linux 的内容才可读取。'));
  const tools=element('div','chat-toolbar'),chat=element('select'),query=element('input');chat.id='inbox-chat';query.placeholder='搜索发言人或内容';query.id='inbox-query';
  const list=element('div');list.id='inbox-list';const status=element('p','hint');status.id='inbox-reading';let next=null,lastInbox=0;
  tools.append(chat,query,button('查询 / 刷新',()=>loadInbox(false)));inbox.append(tools,status,list,button('加载更早消息',()=>loadInbox(true)));
  $('memory-panel').after(inbox);
  const nav=element('a','','完整收件记录');nav.href='#inbox-panel';document.querySelector('nav').append(nav);
  async function loadInbox(more=false){
    const params=new URLSearchParams({chat:chat.value,q:query.value,limit:'50'});
    if(more){if(!next)return;for(const [k,v] of Object.entries(next))params.set(k,v);}else list.replaceChildren();
    const result=await api('messages?'+params);next=result.next;lastInbox=Date.now();
    status.textContent=result.reading.map(r=>chatName(r.chat)+'：'+(r.error?r.error:r.completed?'已完成本轮归档':'正在补档')).join(' · ');
    for(const m of result.messages){
      const card=element('article','record');card.append(element('strong','',m.sender+' · '+chatName(m.chat)),element('p','hint',new Date(m.timestamp*1000).toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'})+' · '+(recordLabels[m.state]||m.state)));
      card.append(element('p','message',m.content));if(m.description)card.append(element('p','hint','图片描述：'+m.description));
      if(m.media_status)card.append(element('p','hint','图片：'+m.media_status+' · '+m.media_reason));
      if([3,47].includes(m.kind)){
        const show=element('button','secondary','显示已缓存图片');show.type='button';
        show.onclick=()=>action(show,async()=>{const image=await api('messages/image?key='+encodeURIComponent(m.key));
          const preview=element('img','message-image');preview.alt='微信原始消息附件';preview.loading='lazy';
          preview.src='data:image/'+(image.format==='png'?'png':'jpeg')+';base64,'+image.data;card.append(preview);show.remove();});card.append(show);
      }
      const quoted=m.payload?.reply;
      if(quoted?.type===3 && /^\d+$/.test(String(quoted.serverId||''))){
        const referenced=m.key.slice(0,m.key.lastIndexOf('/')+1)+quoted.serverId;
        const show=element('button','secondary','查看引用原图');show.type='button';
        show.onclick=()=>action(show,async()=>{const image=await api('messages/image?key='+encodeURIComponent(referenced));
          const preview=element('img','message-image');preview.alt='微信引用消息的原图';preview.loading='lazy';
          preview.src='data:image/'+(image.format==='png'?'png':'jpeg')+';base64,'+image.data;card.append(preview);show.remove();});card.append(show);
      }
      if(m.error)card.append(element('p','error-text',m.error));list.append(card);
    }
  }
  const stickers=element('section','panel span-all');stickers.id='sticker-profile-panel';stickers.append(element('h2','','表情语义目录'),element('p','muted','每个原生表情只识别一次，记录适用与不适用场景；可直接修改，后续选择复用标签。'));
  const stickerList=element('div');stickers.append(button('查看 / 刷新目录',loadStickers),stickerList);inbox.after(stickers);
  async function loadStickers(){
    const r=await api('stickers');stickerList.replaceChildren();
    const names={meaning:'简洁含义',visual:'画面动作',text:'图片文字',emotion:'情绪',tone:'语气',use_when:'适用场景',avoid_when:'不适用场景',example:'适合回应的群友话'};
    for(const [id,meaning] of Object.entries(r.stickers)){
      const details=element('details','record'),heading=element('summary','',meaning);details.append(heading);const controls={};
      for(const [k,title] of Object.entries(names)){const label=element('label','',title),input=element('textarea');input.rows=2;input.maxLength=400;input.value=r.profiles[id]?.[k] || (k==='meaning'?meaning:'');controls[k]=input;label.append(input);details.append(label);}
      details.append(button('保存这个表情',async()=>{const data={};for(const [k,input] of Object.entries(controls))data[k]=input.value;await api('stickers/'+id,'POST',data);heading.textContent=data.meaning;notice('表情语义已保存');}));stickerList.append(details);
    }
  }
  function download(data,name){const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json;charset=utf-8'})),a=element('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function chatLabels(){
    for(const select of [filter,chat]){const previous=select.value;select.replaceChildren();const all=element('option','','全部已授权会话');all.value='';select.append(all);for(const id of cfg?.allowed_chat_ids||[]){const o=element('option','',chatName(id));o.value=id;select.append(o);}select.value=previous;}
  }
  function populate(){if(!cfg)return;for(const n of Object.keys(styleFields))$('style-'+n).value=cfg[n] ?? (['reply_warmth','sticker_after_text'].includes(n)?'high':'normal');stickerCooldown.value=cfg.sticker_cooldown_seconds ?? 180;budget.value=cfg.context_char_budget;summary.checked=cfg.background_digest_enabled;auto.checked=cfg.memory_auto_capture;chatLabels();}
  function stateUpdated(s){
    if(!styleBox.dataset.ready){populate();styleBox.dataset.ready='true';}
    if(location.hash==='#inbox-panel' && Date.now()-lastInbox>10000){lastInbox=Date.now();loadInbox(false).catch(e=>notice(e.message,true));}
  }
  window.improvements={stateUpdated,chatLabels,populate};if(cfg)populate();
})();
