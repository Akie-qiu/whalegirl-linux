'use strict';
(() => {
  const el=(tag,content='')=>{const node=document.createElement(tag);node.textContent=content;return node;};
  const field=(title,input)=>{const label=el('label',title);if(input.type==='checkbox')label.className='check-label';label.append(input);return label;};
  const select=values=>{const node=el('select');for(const [value,title] of values){const option=el('option',title);option.value=value;node.append(option);}return node;};
  const btn=(title,fn)=>{const node=el('button',title);node.type='button';node.className='secondary';node.onclick=()=>action(node,fn);return node;};
  const section=el('section');section.id='fishtalk-panel';section.className='expression-card span-all';
  section.append(el('h3','真人表达学习 · fishtalk'),el('p','hint'));
  section.lastChild.textContent='学习本会话真人的接话节奏，保留少女感、称呼和情绪。不增加模型调用；文风资料不覆盖角色卡。自动学习遵守现有的学习与记忆总开关。';
  const enabled=el('input');enabled.type='checkbox';enabled.id='fishtalk-enabled';
  const auto=el('input');auto.type='checkbox';auto.id='fishtalk-auto-learn';
  const level=select([['subtle','少量参考'],['normal','适量参考'],['strong','更多真实片段']]);level.id='fishtalk-reference-level';
  const mode=select([['light','温和检测 · 去除独立客服尾句'],['observe','只记录提醒 · 不修改文字'],['off','关闭发送前检测']]);mode.id='fishtalk-check-mode';
  const grid=el('div');grid.className='settings-grid';grid.append(field('启用真人表达参考',enabled),field('后台学习已授权聊天',auto),field('文风参考量',level),field('回复前检测',mode));section.append(grid);
  const details=el('details');details.className='settings-drawer';details.append(el('summary','语料、文风预览与导入'));
  for(const name of ['input','change'])details.addEventListener(name,event=>event.stopPropagation());
  const chat=el('select');chat.id='fishtalk-chat';chat.setAttribute('aria-label','真人语料所属会话');
  const status=el('p');status.className='hint';const preview=el('div');preview.className='message';const samples=el('div');samples.className='bento-grid';
  const format=select([['text','纯文字 · 一行一条'],['json','JSON 聊天记录'],['csv','CSV 聊天记录']]);
  const content=el('textarea');content.maxLength=150000;content.rows=5;content.placeholder='粘贴真人发言或聊天记录。只用于当前选择的会话，不要填密钥、电话等隐私。';
  const upload=el('input');upload.type='file';upload.accept='.txt,.json,.csv';
  upload.onchange=async()=>{const file=upload.files?.[0];if(!file)return;
    if(file.size>600000){notice('文件请控制在600KB以内',true);return;}
    const bytes=new Uint8Array(await file.arrayBuffer());let value;
    if(bytes[0]===255&&bytes[1]===254)value=new TextDecoder('utf-16le').decode(bytes);
    else if(bytes[0]===254&&bytes[1]===255)value=new TextDecoder('utf-16be').decode(bytes);
    else{try{value=new TextDecoder('utf-8',{fatal:true}).decode(bytes);}catch{value=new TextDecoder('gb18030').decode(bytes);}}
    content.value=value;format.value=file.name.endsWith('.json')?'json':file.name.endsWith('.csv')?'csv':'text';};
  const checkText=el('textarea');checkText.rows=2;checkText.maxLength=2000;checkText.placeholder='可以粘贴一句回复，看看检测结果；这里不会发送微信消息。';
  const checkResult=el('p');checkResult.className='hint';
  const names={habitual_acknowledgement:'惯用确认词（提醒，不禁止角色称呼）',contrast:'工整对仗',summary:'总结式开场',report:'客服或报告套话',abstract:'抽象总结',recent_repeat:'与近期已发文字过于相似',corpus_copy:'与真人样本有较长重复'};
  async function refresh(){if(!chat.value)return;
    const result=await api('fishtalk?chat='+encodeURIComponent(chat.value));const item=result.profiles[0],pack=item?.pack;
    status.textContent=pack?'本会话 '+pack.count+' 条样本 · '+pack.people+' 位说话人 · 文风版本 '+item.version+(pack.full_history?' · 全历史整理':''):'还没有文风包，可点“整理现有聊天”或导入样本';
    if(pack?.coverage)status.textContent+='（已扫描 '+pack.coverage.messages+' 条记录，含 '+pack.coverage.human_stickers+' 条真人表情）';
    if(result.last_error)status.textContent+=' · '+result.last_error;
    preview.replaceChildren();samples.replaceChildren();
    if(pack){preview.append(el('p','真人文字中位长度：'+pack.length.median+'字；这是观察值，不是限制。'));
      for(const example of pack.examples){preview.append(el('p',(example.context?'相邻前句：'+example.context+'\n':'')+'真人发言：'+example.text));}}
    for(const item of result.samples||[]){const card=el('article');card.className='record';card.append(el('p',item.text));
      card.append(btn('参考',async()=>{await api('fishtalk/label','POST',{chat:chat.value,key:item.key,label:'good'});await refresh();}),btn('不参考',async()=>{await api('fishtalk/label','POST',{chat:chat.value,key:item.key,label:'blocked'});await refresh();}));samples.append(card);}
    for(const item of result.excluded||[]){const card=el('article');card.className='record';card.append(el('p','已停用的样本 · '+item.key.slice(-18)),btn('恢复参考',async()=>{await api('fishtalk/label','POST',{chat:chat.value,key:item.key,label:'auto'});await refresh();}));samples.append(card);}
  }
  chat.onchange=()=>{refresh().catch(error=>notice(error.message,true));};
  details.append(field('语料所属会话',chat),btn('刷新预览',refresh),btn('整理现有聊天',async()=>{if(!chat.value)throw new Error('请先选择已授权会话');await api('fishtalk/rebuild','POST',{chat:chat.value});await refresh();}),
    btn('整理全部已存历史',async()=>{if(!chat.value)throw new Error('请先选择已授权会话');await api('fishtalk/history','POST',{chat:chat.value});await refresh();notice('全历史整理完成，回复仍只读取精简参考');}),status,preview,
    el('h4','导入真人语料'),field('格式',format),upload,content,btn('导入到所选会话',async()=>{if(!chat.value)throw new Error('请先选择会话');const result=await api('fishtalk/import','POST',{chat:chat.value,format:format.value,content:content.value});notice('已导入 '+result.added+' 条，跳过 '+result.skipped+' 条无效或机器人消息');await refresh();}),
    el('h4','回复检测（不发送）'),checkText,btn('检测这句话',async()=>{const result=await api('fishtalk/check','POST',{chat:chat.value,text:checkText.value});const codes=[...result.issues,...result.notes];checkResult.textContent=codes.length?codes.map(c=>names[c]||c).join('；'):'未发现明显套话或照抄。检测结果不能证明像真人。';}),checkResult,
    el('h4','样本标注'),samples);
  details.ontoggle=()=>{if(details.open)refresh().catch(error=>notice(error.message,true));};section.append(details);
  function populate(){if(!cfg)return;enabled.checked=cfg.fishtalk_enabled!==false;auto.checked=cfg.fishtalk_auto_learn!==false;level.value=cfg.fishtalk_reference_level||'normal';mode.value=cfg.fishtalk_check_mode||'light';
    const old=$('anti-formula-guard-enabled');if(old){old.disabled=enabled.checked;const label=old.closest('label');if(label?.lastChild)label.lastChild.textContent=enabled.checked?'旧版重复提醒（当前由真人表达检测接管）':'减少重复表达';}
    const previous=chat.value;chat.replaceChildren();for(const id of cfg.allowed_chat_ids||[]){const option=el('option',chatName(id));option.value=id;chat.append(option);}if((cfg.allowed_chat_ids||[]).includes(previous))chat.value=previous;}
  function mount(panel){if(!section.isConnected){const footer=panel.querySelector('.panel-footer');panel.insertBefore(section,footer);}populate();}
  enabled.onchange=()=>{const old=$('anti-formula-guard-enabled');if(old)old.disabled=enabled.checked;};
  window.fishtalkPanel={mount,populate,values:()=>({fishtalk_enabled:enabled.checked,fishtalk_auto_learn:auto.checked,fishtalk_reference_level:level.value,fishtalk_check_mode:mode.value})};
  if($('expression-panel'))mount($('expression-panel'));
})();
