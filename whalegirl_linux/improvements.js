'use strict';
(() => {
  const element=(tag,cls,content) => {const e=document.createElement(tag);if(cls)e.className=cls;if(content)e.textContent=content;return e;};
  const button=(title,fn) => {const b=element('button','secondary',title);b.type='button';b.onclick=()=>action(b,fn);return b;};
  const conversationFields={banter_level:'玩梗程度',play_along:'顺着演的意愿',callback_frequency:'旧梗回忆频率',assertiveness:'主见程度'};
  const styleFields={reply_warmth:'回复温度',sticker_after_text:'文字后配表情',colloquialism:'口语程度',teasing:'吐槽强度',persona_density:'人设浓度',followup_questions:'反问频率',reply_length:'默认句长',...conversationFields};
  const styleOptions={reply_warmth:[['low','平和简洁'],['normal','自然亲切'],['high','温暖热络']],sticker_after_text:[['off','关闭主动搭配'],['normal','适中 · 合适时搭配'],['high','积极 · 优先文字后配表情']],reply_length:[['short','简短'],['normal','适中'],['detailed','充分解释']]};
  Object.assign(styleOptions,{
    banter_level:[['low','偶尔轻松一句'],['normal','自然接梗'],['high','更爱嘴贫']],
    play_along:[['low','点到为止'],['normal','愿意接一两轮'],['high','愿意继续演']],
    callback_frequency:[['off','不主动提旧梗'],['low','很相关时偶尔提'],['normal','相关时自然提起'],['high','更愿意回忆共同梗']],
    assertiveness:[['low','柔和表达'],['normal','有自己的喜恶'],['high','更坦率地表达意见']]
  });
  const styleBox=element('div','settings-grid');
  const conversationBox=element('div','settings-grid');
  for(const [name,title] of Object.entries(styleFields)) {
    const label=element('label','',title), select=element('select');select.id='style-'+name;
    for(const [value,text] of styleOptions[name] || [['low','低'],['normal','适中'],['high','高']]) {
      const o=element('option','',text);o.value=value;select.append(o);
    }
    label.append(select);(name in conversationFields ? conversationBox : styleBox).append(label);
  }
  const stickerCooldownLabel=element('label','','同一表情候选冷却 / 秒'),stickerCooldown=element('input');
  stickerCooldown.type='number';stickerCooldown.id='style-sticker-cooldown';stickerCooldown.min=0;stickerCooldown.max=3600;stickerCooldown.step=1;
  stickerCooldownLabel.append(stickerCooldown,element('span','hint','近期发过的同一表情暂不参与候选；运行保护中的重复内容限制仍独立生效。'));styleBox.append(stickerCooldownLabel);
  const pairProbabilityLabel=element('label','','合适候选的自动搭配概率 / %'),pairProbability=element('input');
  pairProbability.type='number';pairProbability.id='style-sticker-pair-probability';pairProbability.min=0;pairProbability.max=100;pairProbability.step=5;
  pairProbabilityLabel.append(pairProbability,element('span','hint','AI 同一轮选出贴切表情后，按此概率附在文字后；90 表示合适候选有 90% 概率发送，不代表所有消息的 90%。0 关闭自动补配，不影响 AI 已明确安排的表情。'));styleBox.append(pairProbabilityLabel);
  const pairingHint=element('p','hint','回复温度控制亲切感，群聊仍保持朋友语气。文字后配表情控制搭配意愿，不是保证每条都发；每轮上限至少为 2 才能同时发文字和表情。');
  styleBox.append(pairingHint);
  const conversationSection=element('section','span-all');
  conversationSection.style.gridColumn='1 / -1';conversationSection.style.minWidth='0';
  conversationSection.append(element('h3','','接话与玩梗'),element('p','hint','玩梗控制自嘲、找补和顺势发挥；吐槽强度单独控制互损。共同梗只在原群使用，认真求助时收住玩笑。所有选择跟随当前角色，在同一次回复中完成。'),conversationBox);
  styleBox.append(conversationSection);
  const humanLikeSection=element('section','record span-all');
  humanLikeSection.style.gridColumn='1 / -1';humanLikeSection.style.minWidth='0';
  humanLikeSection.append(element('h3','','🌊 真人感动态状态与去人机感引擎'),
    element('p','hint','实时跟踪当前会话的小情绪起伏、后台生活小剧场、反套路熔断器与分群性格面具，全部注入动态上下文，不破坏提示词前缀缓存。'));
  const liveToolbar=element('div','chat-toolbar'),liveChatSelect=element('select');
  liveChatSelect.id='persona-live-chat';liveChatSelect.setAttribute('aria-label','查看实时状态的会话');
  const liveSummary=element('div','record');liveSummary.id='persona-live-summary';liveSummary.style.margin='8px 0';
  liveSummary.textContent='正在读取实时情绪、生活小剧场与分群面具…';
  function renderPersonaLive(data){
    if(!data)return;
    liveSummary.replaceChildren();
    const mood=data.mood||{};
    const af=data.anti_formula||{};
    const mask=data.group_mask||{};
    const deltaText=v=>v>0?'+'+v:String(v||0);
    const guardNames={relaxed:'熟人放松',normal:'正常边界',guarded:'稍微收敛'};
    liveSummary.append(
      element('strong','',`当前情绪：${mood.label||'🌊 平静摸鱼'}（活力 ${mood.energy??58}/100 · ${mood.reason||'日常平稳'}）`),
      element('p','message',`后台生活小剧场：${data.life_scene||'正捧着小鲸鱼碗在深海待命'}`),
      element('p','hint',`分群性格面具：吐槽偏移 ${deltaText(mask.teasing_delta)} 档 · 玩梗偏移 ${deltaText(mask.banter_delta)} 档 · 防备度「${guardNames[mask.guard_level]||'正常边界'}」`+(mask.vibe_summary?` · 氛围：${mask.vibe_summary}`:'')),
      element('p','hint','反套路熔断器：'+((af.banned_openers?.length||af.banned_self_refs?.length||af.ban_end_question)
        ?`本轮禁用起手词 [${(af.banned_openers||[]).join('/')||'无'}] · 暂停自称 [${(af.banned_self_refs||[]).join('/')||'无'}]`+(af.ban_end_question?' · 禁句尾反问号':'')
        :'近 5 轮句式自然，未触发熔断'))
    );
  }
  async function refreshPersonaLive(){
    const data=await api('persona-state?chat='+encodeURIComponent(liveChatSelect.value||''));
    renderPersonaLive(data);
  }
  liveChatSelect.onchange=()=>refreshPersonaLive().catch(e=>notice(e.message,true));
  liveToolbar.append(
    liveChatSelect,
    button('刷新状态',refreshPersonaLive),
    button('重置情绪为平静',async()=>{const d=await api('persona-state','POST',{op:'reset_mood',chat:liveChatSelect.value||''});renderPersonaLive(d);notice('该群小情绪已重置为平静摸鱼状态');}),
    button('随机切换小剧场',async()=>{const d=await api('persona-state','POST',{op:'refresh_life_scene',chat:liveChatSelect.value||''});renderPersonaLive(d);notice('已切换新的后台生活小剧场');}),
    button('重置本群面具',async()=>{const d=await api('persona-state','POST',{op:'reset_group_mask',chat:liveChatSelect.value||''});renderPersonaLive(d);notice('本群性格面具已恢复默认');})
  );
  const humanGrid=element('div','two-fields');
  const moodCheck=element('input');moodCheck.type='checkbox';moodCheck.id='mood-engine-enabled';
  const moodCheckLabel=element('label','check-label');moodCheckLabel.append(moodCheck,document.createTextNode('启用实时小情绪波动引擎（被夸得瑟、被怼炸毛、半夜犯困）'));
  const lifeCheck=element('input');lifeCheck.type='checkbox';lifeCheck.id='daily-life-theater-enabled';
  const lifeCheckLabel=element('label','check-label');lifeCheckLabel.append(lifeCheck,document.createTextNode('启用后台个人生活小剧场（随北京时间与贴吧动态自然带出近况）'));
  const antiCheck=element('input');antiCheck.type='checkbox';antiCheck.id='anti-formula-guard-enabled';
  const antiCheckLabel=element('label','check-label');antiCheckLabel.append(antiCheck,document.createTextNode('启用反套路熔断器（自动禁用近5轮重复起手词、自称与连续反问）'));
  const chimeCheck=element('input');chimeCheck.type='checkbox';chimeCheck.id='chime-in-ultra-short';
  const chimeCheckLabel=element('label','check-label');chimeCheckLabel.append(chimeCheck,document.createTextNode('群聊顺嘴插话强制极简模式（未被点名时控制在 2～18 字短反应，拒绝解释腔）'));
  const volSelect=element('select');volSelect.id='mood-volatility';
  for(const [v,t] of [['low','沉稳内敛'],['normal','自然波动（推荐）'],['high','易开心也易炸毛']]){const o=element('option','',t);o.value=v;volSelect.append(o);}
  const volLabel=element('label','','小情绪敏感度');volLabel.append(volSelect);
  const halfLifeInput=element('input');halfLifeInput.type='number';halfLifeInput.id='mood-half-life-minutes';halfLifeInput.min=5;halfLifeInput.max=180;halfLifeInput.step=1;
  const halfLifeLabel=element('label','','小情绪自动平复半衰期 / 分钟');halfLifeLabel.append(halfLifeInput);
  humanGrid.append(moodCheckLabel,lifeCheckLabel,antiCheckLabel,chimeCheckLabel,volLabel,halfLifeLabel);
  humanLikeSection.append(liveToolbar,liveSummary,humanGrid);
  styleBox.append(humanLikeSection);
  styleBox.append(button('保存说话风格',async()=>{
    const values={};for(const n of Object.keys(styleFields))values[n]=$('style-'+n).value;
    if(!stickerCooldown.checkValidity())throw new Error('同一表情候选冷却需在 0～3600 秒之间');
    if(!pairProbability.checkValidity())throw new Error('自动搭配概率需在 0～100 之间');
    if(!halfLifeInput.checkValidity())throw new Error('小情绪半衰期需在 5～180 分钟之间');
    values.sticker_cooldown_seconds=Number(stickerCooldown.value);
    values.sticker_pair_probability=Number(pairProbability.value);
    values.mood_engine_enabled=moodCheck.checked;
    values.daily_life_theater_enabled=lifeCheck.checked;
    values.anti_formula_guard_enabled=antiCheck.checked;
    values.chime_in_ultra_short=chimeCheck.checked;
    values.mood_volatility=volSelect.value;
    values.mood_half_life_minutes=Number(halfLifeInput.value);
    await saveSubset(values);notice('说话风格、真人感状态与配表情设置已保存，下一轮生效');
  }));
  $('save-habits').parentElement.before(styleBox);
  const contextLabel=element('label','','上下文字符预算');const budget=element('input');budget.type='number';budget.id='context-char-budget';budget.min=4000;budget.max=40000;contextLabel.append(budget);
  const summaryLabel=element('label','check-label');const summary=element('input');summary.type='checkbox';summary.id='digest-enabled';summaryLabel.append(summary,document.createTextNode('后台增量整理话题摘要'));
  styleBox.append(contextLabel,summaryLabel,button('保存上下文策略',async()=>{await saveSubset({context_char_budget:Number(budget.value),background_digest_enabled:summary.checked});notice('上下文策略已保存');}));
  const shared=$('global-memory').parentElement;shared.lastChild.textContent='共享相同微信 ID 的个人事实与偏好（群事件独立）';
  $('memory-panel').querySelector('.muted').textContent='群内事件、梗、关系按会话独立；相同微信 ID 的明确个人事实与偏好可跨群和私聊调用。每条可编辑或删除，删除后不再参与长期记忆调用，可在“含已删除”中恢复。原始聊天和修订记录保留。';
  const filter=element('select');filter.id='memory-filter-chat';filter.setAttribute('aria-label','记忆来源会话');$('memory-search').before(filter);
  filter.onchange=()=>window.habits.refresh().catch(e=>notice(e.message,true));
  const scopeLabel=element('label','','记忆使用范围'),scope=element('select');scope.id='memory-scope';
  for(const [v,t] of [['chat','仅本群 / 本私聊'],['profile','共享人物事实与偏好']]){const o=element('option','',t);o.value=v;scope.append(o);}scopeLabel.append(scope);$('memory-content').parentElement.before(scopeLabel);
  for(const [v,t] of [['event','本群事件'],['self_stance','机器人自身言行 / Flag'],['shared_moment','群内共同名场面']]){
    const opt=element('option','',t);opt.value=v;$('memory-kind').append(opt);
  }
  const autoLabel=element('label','check-label'),auto=element('input');auto.type='checkbox';auto.id='memory-auto-capture';autoLabel.append(auto,document.createTextNode('后台主动整理明确偏好和事实'));
  const selfCapLabel=element('label','check-label'),selfCap=element('input');selfCap.type='checkbox';selfCap.id='memory-self-capture-enabled';selfCapLabel.append(selfCap,document.createTextNode('允许记录机器人自身立过的 Flag 与共同名场面（防前后打脸）'));
  const halfLifeMemLabel=element('label','','普通记忆时间衰减半衰期 / 天（5星刻骨记忆免衰减）'),halfLifeMem=element('input');
  halfLifeMem.type='number';halfLifeMem.id='memory-half-life-days';halfLifeMem.min=7;halfLifeMem.max=365;halfLifeMem.step=1;halfLifeMemLabel.append(halfLifeMem);
  $('save-memory-switch').before(autoLabel,selfCapLabel,halfLifeMemLabel);
  $('save-memory-switch').onclick=()=>action($('save-memory-switch'),async()=>{
    if(!halfLifeMem.checkValidity())throw new Error('记忆半衰期需在 7～365 天之间');
    await saveSubset({
      editable_memory_enabled:$('editable-memory-enabled').checked,
      memory_auto_capture:auto.checked,
      memory_self_capture_enabled:selfCap.checked,
      memory_half_life_days:Number(halfLifeMem.value),
      memory_scope_mode:'shared_profiles'
    });
    notice('记忆策略与联想衰减参数已保存');
  });
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
    const prevLive=liveChatSelect.value;liveChatSelect.replaceChildren();
    for(const id of cfg?.allowed_chat_ids||[]){const o=element('option','',chatName(id));o.value=id;liveChatSelect.append(o);}
    if((cfg?.allowed_chat_ids||[]).includes(prevLive))liveChatSelect.value=prevLive;
  }
  function populate(){
    if(!cfg)return;
    for(const n of Object.keys(styleFields))$('style-'+n).value=cfg[n] ?? (['reply_warmth','sticker_after_text'].includes(n)?'high':'normal');
    stickerCooldown.value=cfg.sticker_cooldown_seconds ?? 180;
    pairProbability.value=cfg.sticker_pair_probability ?? 90;
    moodCheck.checked=cfg.mood_engine_enabled !== false;
    lifeCheck.checked=cfg.daily_life_theater_enabled !== false;
    antiCheck.checked=cfg.anti_formula_guard_enabled !== false;
    chimeCheck.checked=cfg.chime_in_ultra_short !== false;
    volSelect.value=cfg.mood_volatility || 'normal';
    halfLifeInput.value=cfg.mood_half_life_minutes ?? 25;
    budget.value=cfg.context_char_budget;
    summary.checked=cfg.background_digest_enabled;
    auto.checked=cfg.memory_auto_capture;
    selfCap.checked=cfg.memory_self_capture_enabled !== false;
    halfLifeMem.value=cfg.memory_half_life_days ?? 30;
    chatLabels();
    refreshPersonaLive().catch(()=>{});
  }
  function stateUpdated(s){
    if(!styleBox.dataset.ready){populate();styleBox.dataset.ready='true';}
    if(s?.persona_live && (!liveChatSelect.value || s.persona_live.chat===liveChatSelect.value)){renderPersonaLive(s.persona_live);}
    if(location.hash==='#inbox-panel' && Date.now()-lastInbox>10000){lastInbox=Date.now();loadInbox(false).catch(e=>notice(e.message,true));}
  }
  window.improvements={stateUpdated,chatLabels,populate};if(cfg)populate();
})();
