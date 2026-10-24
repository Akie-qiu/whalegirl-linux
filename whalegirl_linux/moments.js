'use strict';
(() => {
  const el=(tag,cls,value)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(value)e.textContent=value;return e;};
  const btn=(title,fn)=>{const b=el('button','secondary',title);b.type='button';b.onclick=()=>action(b,fn);return b;};
  const panel=el('section','panel span-all');panel.id='moments-panel';
  panel.append(el('h2','','朋友圈'),el('p','muted','自主发动态、回复自己动态下的评论、评论好友动态分别控制。默认先生成草稿，可编辑后发布，也可切换自动模式。朋友圈按公开场合表达，不引用私聊或群聊中的未公开事实。'));
  const fields={},switches={moments_enabled:['朋友圈功能总开关','关闭后停止新生成和发布，已有草稿及记录保留'],
    moments_publish_enabled:['自主发朋友圈','AI 根据人设和话题决定是否发动态，允许没有内容时不发'],
    moments_reply_own_enabled:['回复自己朋友圈下的评论','只回应别人评论机器人自己动态的内容'],
    moments_comment_friends_enabled:['评论好友朋友圈','只对下方选中的好友生效，与回复自己评论的开关完全独立'],
    moments_image_pick_enabled:['AI 从两个图库选择配图','可混合选择上传图片和已生成图片，不调用生图接口'],
    moments_image_generate_enabled:['允许为朋友圈生成新配图','AI 可主动选择画新图或使用现成图，后台生成完成后再发布，受每日生图上限约束']};
  for(const [name,[title,description]] of Object.entries(switches)){
    const box=el('div','setting-row'),copy=el('div');copy.append(el('strong','',title),el('p','',description));
    const input=el('input','toggle');input.type='checkbox';input.id=name.replaceAll('_','-');input.setAttribute('aria-label',title);fields[name]=input;box.append(copy,input);panel.append(box);
    input.onchange=()=>action(input,async()=>{try{await saveSubset({[name]:input.checked});await load();notice('朋友圈开关已保存');}catch(e){populate();throw e;}});
  }
  const mode=el('select');for(const [v,t] of [['draft','先生成草稿，后台手动发布'],['auto','自动发布与评论']]){const o=el('option','',t);o.value=v;mode.append(o);}fields.moments_mode=mode;
  const ml=el('label','','处理模式');ml.append(mode);panel.append(ml);
  const grid=el('div','settings-grid');
  for(const [name,title,min,max] of [
    ['moments_publish_per_day','每天最多发动态 / 条',0,10],['moments_reply_own_per_day','每天最多回复自己评论 / 条',0,100],
    ['moments_comment_friends_per_day','每天最多评论好友动态 / 条',0,30],['moments_poll_minutes','动态刷新间隔 / 分钟',5,120],
    ['moments_start_hour','活跃时段开始 / 北京时间',0,23],['moments_end_hour','活跃时段结束 / 北京时间',0,23],
    ['moments_image_generate_eagerness','主动生成配图积极性 / 0～100',0,100],['moments_variety_days','选题与配图参考最近 / 天',1,30]]){
    const label=el('label','',title),input=el('input');input.type='number';input.min=min;input.max=max;input.step=1;fields[name]=input;label.append(input);grid.append(label);
  }
  panel.append(grid,el('p','hint','自己的评论每30秒读取一次已同步缓存，无需等动态刷新；微信尚未同步的内容在刷新动态后可见。三个每日上限分别计数，0表示禁止该类发送。每日自主发动态会将活跃时段均分为多个时间段，在每个时间段内随机挑时间发送，避免堆在一起。每天按北京时间零点计算；开始和结束相同表示全天。聊天忙时朋友圈让行，新开启评论不补评历史。'));
  panel.append(el('p','hint','生图积极性默认75：越高越愿意为合适的动态画新图，不是强制生图概率；0表示不主动生图。已有固定配图优先使用。AI 会参考近期已发和待发文案、配图构思，换话题、表达角度和画面；高度雷同的草稿本轮跳过。'));
  const topics=el('textarea');topics.rows=2;topics.maxLength=3000;fields.moments_topics=topics;
  const tl=el('label','','发动态感兴趣的话题 / 每行一个');tl.append(topics);panel.append(tl);
  const prompt=el('textarea');prompt.rows=5;prompt.maxLength=8000;fields.moments_prompt=prompt;
  const pl=el('label','','朋友圈专用提示词');pl.append(prompt);panel.append(pl);
  const friends=el('select');friends.multiple=true;friends.size=6;friends.setAttribute('aria-label','允许评论朋友圈的好友');
  const fl=el('label','','允许主动评论朋友圈的好友 / 按住 Ctrl 可多选');fl.append(friends);panel.append(fl,el('p','hint','没有选中好友时，不会主动评论任何好友动态。回复自己朋友圈下的评论不受这份名单限制。'));
  const upload=el('input');upload.type='file';upload.accept='image/png,image/jpeg,image/webp';
  const assetsBox=el('div'),assetStatus=el('p','hint','可上传普通图片作为配图。未选素材时发纯文字；AI 不会从聊天中自行拿图。');
  const assetLabel=el('label','','朋友圈配图素材 / 每条最多9张已选素材');assetLabel.append(upload);panel.append(assetLabel,assetStatus,assetsBox);
  let assets=[],selectedAssets=new Set(),populated=false,loading=false,current=null;
  function renderAssets(){assetsBox.replaceChildren();for(const identity of selectedAssets){const item=assets.find(a=>a.id===identity);assetsBox.append(el('p','hint',String([...selectedAssets].indexOf(identity)+1)+'. '+(item?item.name+' · '+(item.source==='generated'?'生成图库':'上传图库'):'图片不可用')));}if(!selectedAssets.size)assetsBox.append(el('p','hint','尚未选择固定配图，可从两个图库混选，最多9张。'));}
  panel.append(btn('从两个图库选择并排列固定配图',async()=>window.pictureLibrary.pick({selected:[...selectedAssets],onSave:async chosen=>{selectedAssets=new Set(chosen);assets=(await api('moments/assets')).assets;renderAssets();notice('图片已选择，请保存朋友圈规则');}})));
  upload.onchange=()=>action(upload,async()=>{
    const file=upload.files[0];if(!file)return;if(file.size>5_500_000)throw new Error('请使用不超过5MB的图片');
    const data=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(String(r.result).split(',')[1]);r.onerror=reject;r.readAsDataURL(file);});
    const result=await api('moments/assets','POST',{data,name:file.name});
    assets=(await api('moments/assets')).assets;selectedAssets.add(result.id);renderAssets();upload.value='';notice('素材已上传，请保存朋友圈规则后使用');
  });
  const toolbar=el('div','row');toolbar.append(btn('保存朋友圈规则',save),btn('生成朋友圈草稿',async()=>{await save();render(await api('moments/draft','POST',{kind:'publish'}));}),btn('读取朋友圈 / 检查入口',async()=>{render(await api('moments/scan','POST'));renderPosts(current.posts||[]);}));panel.append(toolbar);
  const status=el('p','timing-detail'),error=el('p','error-text');panel.append(status,error);
  const postsBox=el('details','record');postsBox.append(el('summary','','最近读取的动态与评论'));const postsList=el('div');postsBox.append(postsList);panel.append(postsBox);
  panel.append(el('h3','','草稿与发布记录'));const records=el('div');panel.append(records);$('records-panel').before(panel);
  const nav=el('a','','朋友圈');nav.href='#moments-panel';nav.onclick=()=>load().catch(e=>notice(e.message,true));document.querySelector('nav').append(nav);
  function populate(){
    if(!cfg)return;for(const [n,input] of Object.entries(fields)){
      if(n in switches)input.checked=!!cfg[n];else input.value=Array.isArray(cfg[n])?cfg[n].join('\n'):(cfg[n]??'');
    }
    renderFriends(new Set(cfg.moments_friend_ids||[]));
    selectedAssets=new Set(cfg.moments_asset_ids||[]);renderAssets();populated=true;
  }
  function renderFriends(selected){
    friends.replaceChildren();
    const choices=new Map(chats.filter(c=>!c.isGroup).map(c=>[c.id,c.remark||c.name||c.id]));
    for(const friend of current?.friends||[])choices.set(friend.id,friend.remark||friend.name||friend.id);
    for(const post of current?.posts||[])if(!post.own&&post.name&&(!choices.has(post.author)||choices.get(post.author)===post.author))choices.set(post.author,post.name);
    for(const id of selected)if(!choices.has(id))choices.set(id,id);
    for(const [id,name] of choices){const o=el('option','',name);o.value=id;o.title=id;o.selected=selected.has(id);friends.append(o);}
  }
  function refreshChatLabels(){renderFriends(new Set([...friends.selectedOptions].map(o=>o.value)));}
  async function save(){
    const values={};for(const [n,input] of Object.entries(fields))values[n]=n in switches?input.checked:n==='moments_topics'?input.value.split('\n').map(x=>x.trim()).filter(Boolean):input.type==='number'?Number(input.value):input.value;
    values.moments_friend_ids=[...friends.selectedOptions].map(o=>o.value);values.moments_asset_ids=[...selectedAssets];
    await saveSubset(values);notice('朋友圈规则已保存');await load();
  }
  const stateNames={draft:'待审草稿',generating:'生成中',awaiting_image:'等待后台生成配图',image_failed:'配图未完成，原文保留',sending:'提交中',sent:'已确认',uncertain:'待核对',failed:'失败',skipped:'本次不发言',cancelled:'已取消'};
  const kindNames={publish:'发朋友圈',reply_own:'回复自己朋友圈的评论',comment_friend:'评论好友朋友圈'};
  async function actionRow(id,op,text){render(await api('moments/actions/'+encodeURIComponent(id),'POST',{op,text:text||''}));}
  function render(s){
    current={...current,...s};const counts=s.counts||{};
    refreshChatLabels();
    status.textContent=(s.enabled?'已开启':'已关闭')+' · '+(s.mode==='auto'?'自动模式':'草稿模式')+' · 今日动态 '+(counts.publish||0)+' / 回复自己的评论 '+(counts.reply_own||0)+' / 评论好友 '+(counts.comment_friend||0)+(s.next_publish_at?' · 下次发动态约 '+new Date(s.next_publish_at*1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}):'')+(s.last_scan?' · 动态刷新 '+new Date(s.last_scan*1000).toLocaleTimeString('zh-CN'):'')+(s.last_cache_scan?' · 评论读取 '+new Date(s.last_cache_scan*1000).toLocaleTimeString('zh-CN'):'');
    error.textContent=s.error||(s.uncertain?'有发布结果需要人工核对，朋友圈发送已暂停':'');
    records.replaceChildren();for(const row of s.actions||[]){
      const card=el('article','record');card.append(el('strong','',kindNames[row.kind]+' · '+(stateNames[row.state]||row.state)),el('p','hint',new Date(row.created*1000).toLocaleString('zh-CN')));
      if(row.target?.post_text)card.append(el('p','muted','来源动态：'+row.target.post_text));if(row.target?.comment)card.append(el('p','muted','来源评论：'+row.target.person+' '+row.target.comment));
      if(row.state==='draft'){
        const text=el('textarea');text.rows=3;text.maxLength=row.kind==='publish'?180:100;text.value=row.text;card.append(text);
        if(row.kind==='publish'){
          const selected=row.target?.assets||[];
          card.append(el('p','hint',selected.length?'已配图 '+selected.length+' 张：'+selected.map(id=>assets.find(a=>a.id===id)?.name||'图片').join('、'):'这条草稿暂无配图'));
          card.append(btn('选择 / 调整这条动态的配图',async()=>window.pictureLibrary.pick({selected,onSave:async chosen=>render(await api('moments/actions/'+row.id,'POST',{op:'edit',text:text.value,assets:chosen}))})));
          if(cfg.moments_image_generate_enabled){const detail=el('details'),imagePrompt=el('textarea');detail.append(el('summary','','为这条动态生成新配图'));imagePrompt.rows=2;imagePrompt.maxLength=3000;imagePrompt.value=row.text;detail.append(imagePrompt,btn('提交配图生成任务',async()=>{if(text.value!==row.text)await actionRow(row.id,'edit',text.value);await api('images/jobs','POST',{kind:'generate',prompt:imagePrompt.value,destination:'moments',moment_id:row.id});render(await api('moments'));notice('配图在后台生成，完成后附加到草稿');}));card.append(detail);}
        }
        const buttons=el('div','row');buttons.append(btn('保存修改',()=>actionRow(row.id,'edit',text.value)),btn(row.kind==='publish'?'发布这条朋友圈':'发送这条评论',async()=>{if(text.value!==row.text)await actionRow(row.id,'edit',text.value);await actionRow(row.id,'publish');}),btn('取消草稿',()=>actionRow(row.id,'cancel')));card.append(buttons);
      }else if(row.text)card.append(el('p','',row.text));
      if(row.error)card.append(el('p','hint',row.error));if(row.receipt)card.append(el('p','hint','确认记录：'+row.receipt));
      if(row.state==='awaiting_image')card.append(el('p','hint','等待期间可以继续聊天。任务进度与取消入口在“图片与生图”。'));
      if(row.state==='image_failed')card.append(btn('保留文字，恢复为草稿',()=>actionRow(row.id,'without_image')));
      if(row.state==='failed'&&row.kind==='reply_own'&&row.text&&row.text.length<=100)card.append(btn('保留原文恢复草稿',()=>actionRow(row.id,'retry')));
      if(row.state==='uncertain'){const buttons=el('div','row');buttons.append(btn('人工确认：已发出',()=>actionRow(row.id,'received')),btn('人工确认：未发出',()=>actionRow(row.id,'not_received')));card.append(buttons);}
      records.append(card);
    }
    if(!(s.actions||[]).length)records.append(el('p','hint','暂无朋友圈任务。开启相应功能后，可先手动生成草稿。'));
  }
  function renderPosts(posts){
    postsList.replaceChildren();for(const post of posts.slice(0,15)){
      const card=el('article','record');card.append(el('strong','',(post.own?'自己的动态 · ':'')+(post.name||post.author)),el('p','',post.text||post.title||'[图片或视频动态，当前仅有文字缓存]'));
      if(!post.own){
        if(!cfg.moments_friend_ids.includes(post.author))card.append(btn('允许评论这位好友',async()=>{await saveSubset({moments_friend_ids:[...cfg.moments_friend_ids,post.author]});renderPosts(posts);notice('好友已加入允许名单，仍受评论好友开关控制');}));
        const b=btn('生成评论草稿',async()=>render(await api('moments/draft','POST',{kind:'comment_friend',post_id:post.id})));b.disabled=!cfg.moments_comment_friends_enabled||!cfg.moments_friend_ids.includes(post.author);card.append(b);
      }
      else for(const comment of post.comments||[]){if(comment.own)continue;const p=el('p','',comment.name+'：'+comment.text);card.append(p,btn('生成这条评论的回复',async()=>render(await api('moments/draft','POST',{kind:'reply_own',post_id:post.id,comment_id:comment.id}))));}
      postsList.append(card);
    }
    if(!posts.length)postsList.append(el('p','hint','当前缓存没有可展示的动态。只读取微信已同步到本机的朋友圈，不代表全部历史。'));
  }
  async function load(){if(loading||!cfg)return;loading=true;try{const results=await Promise.all([api('moments'),api('moments/assets')]);assets=results[1].assets;renderAssets();render(results[0]);}finally{loading=false;}}
  window.moments={populate,refreshChatLabels};if(cfg)populate();
  setInterval(()=>{if(cfg&&!$('dashboard').hidden&&location.hash==='#moments-panel'&&!panel.contains(document.activeElement))load().catch(()=>{});},15000);
})();
