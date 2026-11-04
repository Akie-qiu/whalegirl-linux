'use strict';
(() => {
  const host=$('growth-console');if(!host)return;
  const panel=document.createElement('details');panel.className='settings-drawer';panel.id='bilibili-console';
  panel.innerHTML=`<summary>B站账号、探索与成长</summary>
    <style>#bilibili-console .message{overflow-wrap:anywhere}#bilibili-console .row{display:flex;flex-wrap:wrap;gap:8px}#bilibili-console .two-fields>*{min-width:0}#bili-qr{width:230px;max-width:100%;height:auto;background:white;padding:8px}#bili-reading-detail{max-height:85vh;overflow:auto}#bili-reading-detail img{max-width:100%;width:240px;margin:6px}#bili-detail-body pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>
    <p class="hint">以自己的兴趣接触作品。B站的反思、兴趣与记忆独立保存，不进入微信日常聊天、主动聊天或朋友圈；B站学习也不读取微信聊天和记忆。共用原始角色卡。仅授权读取，不自动点赞、投币、关注、收藏、评论或发布。</p>
    <p id="bili-account-state" class="hint" role="status">正在读取账号状态</p>
    <div class="row"><button id="bili-login" type="button" class="primary">扫码登录B站</button><button id="bili-check" type="button" class="secondary">核对登录状态</button><button id="bili-logout" type="button" class="secondary">退出并使用匿名模式</button><button id="bili-resume" type="button" class="secondary" hidden>已核对访问条件，恢复读取</button></div>
    <div id="bili-login-box" hidden><p id="bili-login-status" class="hint">请用B站手机客户端扫码并确认</p><img id="bili-qr" alt="B站登录二维码，仅供管理员本人扫码"></div>
    <div class="two-fields" style="margin-top:14px">
      <label class="check-label"><input id="bili-enabled" type="checkbox">启用自主B站探索</label>
      <label class="check-label"><input id="bili-visual" type="checkbox">读取少量真实画面并尝试理解</label>
      <label class="check-label"><input id="bili-comments" type="checkbox">读取有限评论</label>
      <label class="check-label"><input id="bili-danmaku" type="checkbox">读取有限弹幕</label>
      <label>探索机会间隔 / 分钟<input id="bili-interval" type="number" min="30" max="1440"></label>
      <label>每天最多探索视频数<input id="bili-reads" type="number" min="0" max="20"></label>
      <label>滚动24小时网络请求上限<input id="bili-requests" type="number" min="20" max="1000"></label>
      <label>画面缓存上限 / MiB<input id="bili-cache" type="number" min="8" max="256"></label>
      <label>画面缓存保留 / 天<input id="bili-days" type="number" min="1" max="30"></label>
    </div>
    <details><summary>允许的来源与兴趣偏好</summary><div id="bili-feeds" class="two-fields"></div><label>起始兴趣提示（一行一个，可留空）<textarea id="bili-seeds" rows="3"></textarea></label><p class="hint">提示只提供起点；兴趣与后续疑问会随真实阅读变化。匿名时只能使用公开推荐和搜索，关注动态、收藏和历史需要扫码登录。</p></details>
    <p class="hint">沿用上方自主成长的总Token预算与阅读总次数。空闲才探索，AI可以跳过；不会定时往群里发学习报告。字幕和抽样画面均显示覆盖范围，不冒充完整观影。退出登录不会解除风控暂停。</p>
    <div class="panel-footer"><span id="bili-usage" class="hint"></span><button id="bili-save" type="button" class="primary">保存B站设置</button></div>
    <div class="row"><button id="bili-explore" type="button" class="primary">给她一次探索机会</button><button id="bili-refresh" type="button" class="secondary">刷新记录</button></div>
    <h3>逐渐变化的兴趣</h3><div id="bili-interests" class="bento-grid"></div>
    <h3>B站独立认识</h3><div id="bili-insights" class="bento-grid"></div>
    <h3>接触过的作品</h3><div id="bili-readings"></div>
    <dialog id="bili-reading-detail" class="tieba-archive-dialog"><div class="section-top"><h3 id="bili-detail-title">作品与理解范围</h3><button id="bili-detail-close" class="secondary" type="button">关闭</button></div><div id="bili-detail-body"></div></dialog>
    <dialog id="bili-insight-editor" class="tieba-archive-dialog"><h3>编辑B站独立认识</h3><p class="hint">只影响B站范围内的认识，不写入微信日常记忆。</p><textarea id="bili-insight-content" maxlength="700" rows="5"></textarea><div class="row"><button id="bili-insight-cancel" class="secondary" type="button">取消</button><button id="bili-insight-save" class="primary" type="button">保存认识</button></div></dialog>`;
  host.append(panel);
  const feeds={recommend:'推荐内容',search:'按兴趣搜索',following:'关注动态',favorites:'自己的收藏',history:'自己的历史'};
  const states={anonymous:'匿名模式',unconfirmed:'登录待核验',logged_in:'已登录',expired:'登录已失效，请重新扫码',credential_error:'本地登录状态需要重新授权'};
  const qrStates={waiting_scan:'请用B站手机客户端扫码',waiting_confirm:'已扫码，请在手机上确认',expired:'二维码已过期，请重新点击扫码登录',logged_in:'登录已保存到服务器'};
  const readingStates={reading:'读取中',done:'已理解部分材料',unconfirmed:'材料不足，未形成观看认识',failed:'未完成',interrupted:'已中止'};
  let loading=false,lastFetch=0,poll=null,qrObject='',current=null,lastAlert='',editingInsight=null;
  const node=(tag,cls='',value='')=>{const n=document.createElement(tag);n.className=cls;n.textContent=value;return n;};
  const when=value=>value?new Date(value*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'尚未安排';
  function button(label,fn){const b=node('button','secondary',label);b.type='button';b.onclick=()=>action(b,fn);return b;}
  for(const [id,label] of Object.entries(feeds)){const l=node('label','check-label');const input=document.createElement('input');input.type='checkbox';input.dataset.feed=id;l.append(input,document.createTextNode(label));$('bili-feeds').append(l);}
  function populate(){
    if(!cfg)return;
    for(const [id,key] of [['bili-enabled','bilibili_enabled'],['bili-visual','bilibili_visual_enabled'],['bili-comments','bilibili_comments_enabled'],['bili-danmaku','bilibili_danmaku_enabled']])$(id).checked=!!cfg[key];
    for(const [id,key,fallback] of [['bili-interval','bilibili_interval_minutes',120],['bili-reads','bilibili_reads_per_day',4],['bili-requests','bilibili_requests_per_day',200],['bili-cache','bilibili_cache_mb',64],['bili-days','bilibili_cache_days',7]])$(id).value=cfg[key]??fallback;
    $('bili-seeds').value=(cfg.bilibili_interest_seeds||[]).join('\n');
    for(const input of $('bili-feeds').querySelectorAll('input'))input.checked=(cfg.bilibili_feeds||Object.keys(feeds)).includes(input.dataset.feed);
    if(panel.open)refresh().catch(error=>notice(error.message,true));
  }
  function account(value){
    $('bili-account-state').textContent=(states[value.state]||value.state)+(value.account_name?' · '+value.account_name:'')+(value.paused?' · 访问限制已暂停（'+value.pause_code+'）':'')+(value.last_check?' · 核验 '+when(value.last_check):'');
    $('bili-resume').hidden=!value.paused;
    $('bili-login-status').textContent=qrStates[value.qr_state]||'';
    if(value.qr_state==='logged_in'||value.qr_state==='expired'){
      stopPoll();if(value.qr_state==='logged_in'){$('bili-login-box').hidden=true;clearQR();}
    }
  }
  async function authedImage(path){
    const response=await fetch('/api/'+path,{headers:{Authorization:'Bearer '+token},cache:'no-store',signal:AbortSignal.timeout(15000)});
    if(!response.ok)throw new Error('图片暂不可用，请刷新记录');
    return URL.createObjectURL(await response.blob());
  }
  function clearQR(){if(qrObject)URL.revokeObjectURL(qrObject);qrObject='';$('bili-qr').removeAttribute('src');}
  function stopPoll(){clearTimeout(poll);poll=null;}
  function queuePoll(){stopPoll();if(panel.open&&host.open&&!$('bili-login-box').hidden)poll=setTimeout(async()=>{try{const value=await api('bilibili/account','POST',{action:'poll'});account(value);if(!['logged_in','expired'].includes(value.qr_state))queuePoll();else await refresh();}catch(error){stopPoll();$('bili-login-status').textContent=error.message;await refresh().catch(()=>{});}},3500);}
  async function detail(reading){
    $('bili-detail-title').textContent=reading.metadata.title||reading.bvid;
    const box=$('bili-detail-body');box.replaceChildren(node('p','hint',when(reading.created)),node('pre','message',JSON.stringify(reading.coverage,null,2)));
    $('bili-reading-detail').showModal();
    if(reading.journal_id){const journal=await api('growth/journals/'+reading.journal_id);for(const [key,label] of Object.entries({encounter:'接触到的具体内容',facts:'材料事实',author_view:'作者观点',inference:'自己的推测',revision:'理解的修正',missing:'没有覆盖的部分'})){if(journal.body[key])box.append(node('h3','',label),node('p','message',journal.body[key]));}for(const source of journal.sources)box.append(node('h3','','实际保存的来源片段'),node('pre','message',source.body));}
    const times=reading.coverage.sampled_frame_times||[];
    for(let index=0;index<Math.min(4,times.length);index++){try{const url=await authedImage('bilibili/readings/'+reading.id+'/frames/'+index);const image=document.createElement('img');image.src=url;image.alt='视频快照 '+times[index]+'秒';image.onload=()=>URL.revokeObjectURL(url);box.append(image);}catch{box.append(node('p','hint','快照缓存已过期，来源与理解记录仍保留'));break;}}
  }
  async function refresh(){
    if(loading)return;loading=true;
    try{const value=await api('bilibili');current=value;account(value.account);lastFetch=Date.now();
      $('bili-usage').textContent='今日视频 '+value.reads_today+'/'+value.read_limit+' · 24小时请求 '+value.account.requests_today+'/'+value.account.request_limit+' · 下次机会 '+when(value.schedule.next_at);
      const interests=$('bili-interests');interests.replaceChildren();for(const item of value.interests){const card=node('article','record');card.append(node('strong','',item.topic),node('p','message',item.thought),node('p','hint','来自 '+item.encounters+' 次有证据的兴趣变化 · '+(item.strength>0?'仍感兴趣':item.strength<0?'兴趣正在减弱':'保留观察')+' · '+when(item.last_change)));interests.append(card);}
      if(!value.interests.length)interests.append(node('p','hint','有真正值得延续的兴趣时会出现在这里，不按关键词硬凑。'));
      const insights=$('bili-insights');insights.replaceChildren();for(const item of value.insights||[]){const card=node('article','record');card.append(node('p','message',item.content),node('p','hint',item.status+' · '+when(item.updated)+(item.conditions?' · '+item.conditions:'')));
        card.append(button('编辑',()=>{editingInsight=item;$('bili-insight-content').value=item.content;$('bili-insight-editor').showModal();}),button(item.archived?'恢复认识':'停用认识',async()=>{await api('growth/insights/'+item.id,'POST',{op:item.archived?'restore':'archive',version:item.version});await refresh();}));insights.append(card);}
      if(!(value.insights||[]).length)insights.append(node('p','hint','B站形成的认识会单独出现在这里，日常聊天不会读取。'));
      const readings=$('bili-readings');readings.replaceChildren();for(const item of value.readings){const row=node('article','record');const title=node('a','',item.metadata.title||item.bvid);title.href='https://www.bilibili.com/video/'+item.bvid+'/';title.target='_blank';title.rel='noopener noreferrer';row.append(title,node('p','hint',when(item.created)+' · '+(readingStates[item.state]||item.state)+(item.enabled?'':' · 已停用')),node('p','message',item.metadata.question||item.error||''));
        row.append(button('来源、反思与画面',()=>detail(item)),button(item.enabled?'停用材料及相关认识':'恢复材料',async()=>{await api('bilibili/readings/'+item.id,'POST',{enabled:!item.enabled});await refresh();}));readings.append(row);}
      if(!value.readings.length)readings.append(node('p','hint','还没有视频记录。开启成长与B站探索后，空闲时会自行选择；也可先给一次探索机会。'));
    }finally{loading=false;}
  }
  $('bili-save').onclick=()=>action($('bili-save'),async()=>{
    for(const id of ['bili-interval','bili-reads','bili-requests','bili-cache','bili-days'])if(!$(id).reportValidity())return;
    await saveSubset({bilibili_enabled:$('bili-enabled').checked,bilibili_visual_enabled:$('bili-visual').checked,bilibili_comments_enabled:$('bili-comments').checked,bilibili_danmaku_enabled:$('bili-danmaku').checked,
      bilibili_interval_minutes:Number($('bili-interval').value),bilibili_reads_per_day:Number($('bili-reads').value),bilibili_requests_per_day:Number($('bili-requests').value),bilibili_cache_mb:Number($('bili-cache').value),bilibili_cache_days:Number($('bili-days').value),
      bilibili_interest_seeds:$('bili-seeds').value.split('\n').map(value=>value.trim()).filter(Boolean),bilibili_feeds:[...$('bili-feeds').querySelectorAll('input:checked')].map(input=>input.dataset.feed)});
    window.dispatchEvent(new CustomEvent('whalegirl-panel-saved',{detail:'bilibili-console'}));
    notice('B站探索设置已保存');await refresh();
  });
  $('bili-login').onclick=()=>action($('bili-login'),async()=>{stopPoll();clearQR();const state=await api('bilibili/account','POST',{action:'login'});account(state);qrObject=await authedImage('bilibili/account/qr');$('bili-qr').src=qrObject;$('bili-login-box').hidden=false;queuePoll();});
  $('bili-check').onclick=()=>action($('bili-check'),async()=>{account(await api('bilibili/account','POST',{action:'check'}));await refresh();});
  $('bili-logout').onclick=()=>action($('bili-logout'),async()=>{stopPoll();clearQR();$('bili-login-box').hidden=true;account(await api('bilibili/account','POST',{action:'logout'}));await refresh();});
  $('bili-resume').onclick=()=>action($('bili-resume'),async()=>{account(await api('bilibili/account','POST',{action:'resume',acknowledge:true}));await refresh();});
  $('bili-explore').onclick=()=>action($('bili-explore'),async()=>{await api('bilibili/explore','POST');notice('已给她一次探索机会，空闲时自己选择；可以不产生新认识或分享');await refresh();});
  $('bili-refresh').onclick=()=>action($('bili-refresh'),refresh);$('bili-detail-close').onclick=()=>$('bili-reading-detail').close();
  $('bili-insight-cancel').onclick=()=>$('bili-insight-editor').close();
  $('bili-insight-save').onclick=()=>action($('bili-insight-save'),async()=>{if(!editingInsight)return;const content=$('bili-insight-content').value.trim();if(!content)throw new Error('认识内容不能为空');await api('growth/insights/'+editingInsight.id,'POST',{op:'update',version:editingInsight.version,content});$('bili-insight-editor').close();editingInsight=null;await refresh();notice('B站独立认识已保存');});
  panel.addEventListener('toggle',()=>{if(panel.open){refresh().catch(error=>notice(error.message,true));if(current?.account.qr_expires>Date.now()/1000)queuePoll();}else stopPoll();});
  host.addEventListener('toggle',()=>{if(!host.open)stopPoll();else if(panel.open)refresh().catch(error=>notice(error.message,true));});
  window.bilibiliConsole={populate,stateUpdated(state){const alert=state?.bilibili_alert;if(alert&&alert.code!==lastAlert){lastAlert=alert.code;notice(alert.message,true);}if(!alert)lastAlert='';if(panel.open&&host.open&&Date.now()-lastFetch>30000)refresh().catch(error=>notice(error.message,true));}};
  if(location.hash==='#bilibili-console'){for(let element=panel;element;element=element.parentElement)if(element.tagName==='DETAILS')element.open=true;setTimeout(()=>panel.scrollIntoView({block:'start'}),250);}
  if(cfg)populate();
})();
