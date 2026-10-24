/* Tieba has its own discovery, source library and sharing controls. */
(() => {
  const el=(tag,cls='',text='')=>{const e=document.createElement(tag);e.className=cls;e.textContent=text;return e;};
  const button=(text,fn)=>{const b=el('button','secondary',text);b.type='button';b.onclick=()=>action(b,fn);return b;};
  const panel=el('section','panel span-all');panel.id='tieba-panel';
  panel.append(el('h2','','贴吧见闻'),el('p','muted','独立浏览关注的吧，挑有趣的帖子分享。感兴趣的内容可保存原文与配图，梗和聊天素材单独整理。读取采用第三方接口，暂不在贴吧发帖或评论。'));
  const masterRow=el('div','setting-row tieba-master'),masterCopy=el('div'),master=el('input','toggle');
  master.type='checkbox';master.id='tieba-tieba_enabled';master.setAttribute('aria-label','贴吧功能总开关');
  masterCopy.append(el('strong','','贴吧功能总开关'),el('p','hint','立即生效：统一控制浏览、分享、原文收藏、学梗与贴吧素材的自动调用。关闭后保留记录和各项设置，正在提交的请求完成后停止后续任务。'));
  masterRow.append(masterCopy,master);panel.append(masterRow);
  master.onchange=()=>action(master,async()=>{try{const saved=await api('tieba/config','POST',{values:{tieba_enabled:master.checked}});cfg.tieba_enabled=saved.tieba_enabled;notice(master.checked?'贴吧功能已开启':'贴吧功能已关闭，已有收藏和梗已保留');await load();}catch(e){master.checked=!!cfg.tieba_enabled;throw e;}});
  const grid=el('div','settings-grid'),fields={};
  const advanced=el('details','record'),advancedGrid=el('div','settings-grid');
  advanced.append(el('summary','','更多设置：分享时段、筛选门槛与收藏容量'),advancedGrid);
  const basic=new Set(['tieba_share_enabled','tieba_learn_enabled','tieba_images_enabled','tieba_archive_enabled','tieba_forums','tieba_interests','tieba_interest_sensitivity','tieba_interval_minutes','tieba_share_per_day']);
  const defs=[
    ['tieba_share_enabled','允许主动分享链接','checkbox'],
    ['tieba_learn_enabled','学习与调用贴吧梗','checkbox'],
    ['tieba_images_enabled','收藏并允许使用贴吧图片','checkbox'],
    ['tieba_archive_enabled','完整收藏感兴趣的原文与配图','checkbox'],
    ['tieba_forums','关注贴吧 / 每行一个，不必加“吧”','textarea'],
    ['tieba_interests','感兴趣的内容 / 每行一个','textarea'],
    ['tieba_interest_sensitivity','兴趣灵敏度 / 0—100，越高越容易收藏与分享','number',0,100],
    ['tieba_interval_minutes','浏览间隔 / 分钟','number',10,1440],
    ['tieba_share_per_day','每日最多分享条数 / 0不分享','number',0,30],
    ['tieba_share_cooldown_minutes','同一会话分享间隔 / 分钟','number',10,1440],
    ['tieba_share_threshold','有趣程度门槛 / 1至100','number',1,100],
    ['tieba_start_hour','允许分享开始时刻 / 北京时间','number',0,23],
    ['tieba_end_hour','允许分享结束时刻 / 相同表示全天','number',0,23],
    ['tieba_memes_ttl_days','学到的梗有效期 / 天','number',1,90],
    ['tieba_images_per_day','每天最多入库聊天素材图 / 不限制原文配图','number',0,20],
    ['tieba_images_limit','贴吧聊天素材库容量','number',1,500],
    ['tieba_archive_limit_mb','原文收藏总容量 / MB，满后保留已有收藏','number',64,4096]
  ];
  for(const [key,title,type,min,max] of defs){
    const label=el('label',type==='checkbox'?'check-label':'',title);
    const input=el(type==='textarea'?'textarea':'input');input.id='tieba-'+key;
    if(type!=='textarea')input.type=type;else input.rows=3;
    if(min!==undefined){input.min=min;input.max=max;input.step=1;}
    if(type==='checkbox')label.prepend(input);else label.append(input);fields[key]=input;(basic.has(key)?grid:advancedGrid).append(label);
  }
  grid.append(el('p','hint','兴趣词命中的帖子优先阅读，AI 同时识别同义表达和别称。灵敏度默认70；0表示不额外加分。修改兴趣词或灵敏度后，最近40条已读帖子会在后续浏览中分批重判。浏览记录显示命中词及加分；分享还需满足时段、每日条数和会话间隔。'));
  const targets=el('div','chatlist');targets.id='tieba-targets';
  const targetTitle=el('h3','','分享会话');
  const login=el('details','record');login.append(el('summary','','可选：贴吧登录凭据'));
  const loginHint=el('p','hint','当前先匿名读取，无需账号。确需登录时可填写 BDUSS、STOKEN，加密保存且不回显；不要填写百度密码。留空保留原值。');
  const bduss=el('input'),stoken=el('input');bduss.type=stoken.type='password';bduss.autocomplete=stoken.autocomplete='new-password';
  bduss.setAttribute('aria-label','贴吧 BDUSS');stoken.setAttribute('aria-label','贴吧 STOKEN');
  bduss.placeholder='BDUSS / 可选';stoken.placeholder='STOKEN / 可选';login.append(loginHint,bduss,stoken);
  const status=el('p','timing-detail','正在读取浏览状态'),records=el('div'),memes=el('div'),pictures=el('div'),shares=el('div'),archives=el('div');
  archives.id='tieba-archives';let archivePage=0;
  const archiveSummary=el('summary','','原文收藏');
  const tabs=el('div','panel-footer');
  tabs.style.flexWrap='wrap';tabs.style.gap='10px';
  tabs.append(button('保存贴吧设置',async()=>{
    const values={tieba_enabled:master.checked};
    for(const [key,,type] of defs){
      const input=fields[key];if(!input.checkValidity())throw Error('请检查 '+input.parentElement.firstChild.textContent);
      values[key]=type==='checkbox'?input.checked:type==='number'?Number(input.value):input.value.split('\n').map(s=>s.trim()).filter(Boolean);
    }
    values.tieba_share_chat_ids=[...targets.querySelectorAll('input:checked')].map(e=>e.value);
    cfg=await api('tieba/config','POST',{values,bduss:bduss.value,stoken:stoken.value});
    bduss.value=stoken.value='';await load();notice('贴吧设置已保存，按后台计划生效');
  }),button('现在逛一轮',async()=>{await api('tieba/scan','POST');notice('已加入后台浏览任务，聊天继续正常处理');await load();}),
    button('刷新',load));
  for(const b of tabs.children){b.style.minWidth='110px';b.style.flex='1 1 110px';}
  panel.append(grid,advanced,targetTitle,el('p','hint','仅发送到勾选的授权会话。没有值得分享的内容就保持安静，不积压补发。默认09—23点，同值表示全天。'),targets,login,tabs,status);
  for(const [title,box,open] of [['最近浏览与分享',records,true],['学到的梗',memes,false],['收藏的图片',pictures,false],['分享发送记录',shares,false]]){
    const d=el('details','record');d.open=open;d.append(el('summary','',title),box);panel.append(d);
  }
  const archiveSection=el('details','record');archiveSection.id='tieba-archive-section';archiveSection.open=true;
  archiveSection.append(archiveSummary,el('p','hint','保存主楼及本次实际读到的楼层、楼中楼原文和全部配图；不改写、不截断。未读取的楼层不计入完整范围，缺图会标注并在空闲时补存。收藏不占每天素材图入库额度。'),archives);
  records.parentElement.after(archiveSection);
  document.querySelector('main').append(panel);
  const nav=el('a');nav.href='#tieba-panel';nav.append(el('span','','贴吧见闻'));document.querySelector('nav').append(nav);
  const fmt=n=>n?new Date(n*1000).toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'}):'尚未记录';
  const link=(text,url)=>{const a=el('a','',text);a.href=url;a.target='_blank';a.rel='noopener noreferrer';return a;};
  const archiveState={requested:'等候读取完整来源',pending:'等候保存',saving:'正在保存',complete:'已保存所读内容',partial:'尚未完整'};
  async function archiveFetch(route){const r=await fetch('/api/tieba/archives/'+route,{headers:{Authorization:'Bearer '+token},cache:'no-store'});if(!r.ok)throw Error('原文或图片尚未保存成功，请稍后重试');return r;}
  async function download(route,name){const r=await archiveFetch(route),url=URL.createObjectURL(await r.blob()),a=el('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function dialog(title){const d=el('dialog','tieba-archive-dialog'),top=el('div','tieba-archive-top');top.append(el('h3','',title),button('关闭',async()=>d.close()));d.append(top);document.body.append(d);d.addEventListener('close',()=>d.remove(),{once:true});d.showModal();return d;}
  function editMeme(m){
    const d=dialog('编辑学到的梗');d.classList.add('tieba-meme-dialog');
    const inputs={};
    for(const [key,title,cap] of [['phrase','梗的说法',80],['meaning','含义',400],['scene','适用与不适用场景',240]]){
      const label=el('label','',title),input=el(key==='phrase'?'input':'textarea');
      input.value=m[key];input.maxLength=cap;input.setAttribute('aria-label',title);
      if(key!=='scene')input.required=true;if(key!=='phrase')input.rows=4;
      label.append(input);d.append(label);inputs[key]=input;
    }
    d.append(el('p','hint','来源原说法：'+(m.source_phrase||m.phrase)),link('查看来源帖子',m.source_url));
    const tools=el('div','tieba-archive-tools');tools.append(button('保存修改',async()=>{
      for(const input of Object.values(inputs))if(!input.reportValidity())return;
      await api('tieba/memes/'+m.id,'POST',Object.fromEntries(Object.entries(inputs).map(([k,input])=>[k,input.value])));
      d.close();notice('梗已保存，后续聊天按更新后的描述调用');await load();
    }),button('取消',async()=>d.close()));d.append(tools);inputs.phrase.focus();
  }
  async function fullImage(route){const d=dialog('原始图片'),image=el('img','tieba-original-image');d.append(image);try{const r=await archiveFetch(route),url=URL.createObjectURL(await r.blob());if(!d.isConnected){URL.revokeObjectURL(url);return;}image.src=url;d.addEventListener('close',()=>URL.revokeObjectURL(url),{once:true});}catch(e){d.append(el('p','error-text',e.message));}}
  async function viewArchive(id){
    const a=await api('tieba/archives/'+id),d=dialog(a.title),coverage=a.source.coverage||{},urls=[];
    const info=el('p','hint',(archiveState[a.state]||a.state)+' · '+a.forum+'吧 · '+fmt(a.created));
    d.append(info,el('p','hint',(coverage.main_floor_present?'已读取主楼完整正文':'未读到主楼，不视为完整主帖')+' · 本次读取 '+(coverage.posts_read??0)+' 个楼层'+(coverage.has_more?' · 还有后续楼层未读取':'')),link('查看来源帖子',a.url));
    if(a.error)d.append(el('p','error-text',a.error));
    const tools=el('div','tieba-archive-tools');
    if(a.text_bytes)tools.append(button('下载原文 TXT',()=>download(id+'/export?kind=text','tieba-'+a.tid+'.txt')),button('下载原始记录 JSON',()=>download(id+'/export?kind=json','tieba-'+a.tid+'.json')));
    if(a.state!=='complete')tools.append(button('继续保存缺失内容',async()=>{await api('tieba/archives/'+id+'/retry','POST');notice('已安排空闲时继续保存');await load();}));d.append(tools);
    if(a.original_text)d.append(el('pre','tieba-original-text',a.original_text));else d.append(el('p','hint','正在等待完整来源，旧浏览摘要不会当作原文。'));
    const gallery=el('div','tieba-archive-gallery');
    for(const p of a.images){
      const card=el('article','tieba-archive-picture'),route=id+'/images/'+p.image_id;
      card.append(el('p','hint',p.pid+' 楼层ID · '+(p.original_available?'来源提供原图':'来源仅提供大图')));
      if(p.state==='saved'){
        const image=el('img');image.alt='收藏原帖配图';image.onclick=()=>action(image,()=>fullImage(route));card.append(image);
        archiveFetch(route+'?thumb=true').then(r=>r.blob()).then(blob=>{const url=URL.createObjectURL(blob);if(!d.isConnected){URL.revokeObjectURL(url);return;}urls.push(url);image.src=url;}).catch(()=>{image.alt='预览暂不可用，仍可下载原图';});
        const ext=({'image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp','image/gif':'.gif'})[p.mime]||'.bin';
        card.append(button('查看原始图片',()=>fullImage(route)),button('下载原始图片',()=>download(route+'?download=true',p.image_id+ext)));
      }else card.append(el('p','error-text',p.error||'尚未保存成功'));
      card.append(link('来源图片链接',p.source_url));gallery.append(card);
    }
    d.append(gallery);d.addEventListener('close',()=>urls.forEach(u=>URL.revokeObjectURL(u)),{once:true});
  }
  function renderArchives(data){
    archives.replaceChildren();
    for(const a of data.items){const row=el('article','record');row.append(el('strong','',a.title),el('p','hint',a.forum+'吧 · '+(archiveState[a.state]||a.state)+' · 图片 '+a.saved_images+'/'+a.image_count+' · '+fmt(a.created)),el('p','hint',a.reason),button('查看原文与图片',()=>viewArchive(a.id)));if(a.error)row.append(el('p','error-text',a.error));archives.append(row);}
    if(!data.total)archives.append(el('p','hint','暂无原文收藏。AI 遇到感兴趣的内容会自动保存，也可在浏览记录中手动收藏。'));
    const pager=el('div','tieba-archive-tools');if(archivePage)pager.append(button('较新的收藏',async()=>{archivePage--;await load();}));
    if((archivePage+1)*20<data.total)pager.append(button('更早的收藏',async()=>{archivePage++;await load();}));archives.append(pager);
  }
  function populate(){
    master.checked=!!cfg.tieba_enabled;
    for(const [key,,type] of defs){const v=cfg[key];if(type==='checkbox')fields[key].checked=!!v;else fields[key].value=Array.isArray(v)?v.join('\n'):v??'';}
    targets.replaceChildren();
    for(const id of cfg.allowed_chat_ids||[]){
      const c=chats.find(c=>c.id===id),label=el('label','check-label'),check=el('input');check.type='checkbox';check.value=id;check.checked=(cfg.tieba_share_chat_ids||[]).includes(id);
      label.append(check,document.createTextNode(c?.remark||c?.name||id));targets.append(label);
    }
    if(!(cfg.allowed_chat_ids||[]).length)targets.append(el('p','hint','先在“会话与唤醒”里授权会话'));
    if(location.hash==='#tieba-panel')load().catch(e=>notice(e.message,true));
  }
  let loading=false;
  async function load(){
    if(loading)return;loading=true;
    try{
      const s=await api('tieba'),run=s.runs[0];
      if(!master.disabled){master.checked=!!s.enabled;cfg.tieba_enabled=!!s.enabled;}
      status.textContent=(s.enabled?'已开启 · '+(s.busy?'正在浏览':s.requested?'等候空闲浏览':'下次 '+fmt(s.next_at)):'总开关已关闭 · 自动浏览、分享与调用均暂停')+' · 今日分享 '+s.sent_today+' · '+(s.login_configured?'已保存登录凭据':'匿名读取');
      records.replaceChildren();
      if(run)records.append(el('p',run.state==='failed'?'error-text':'hint',fmt(run.started)+' · '+({done:'已完成',running:'浏览中',failed:'失败',interrupted:'已中断'}[run.state]||run.state)+' · 读取 '+run.read_count+' 帖 / 排队分享 '+run.picked_count+' / 收藏图片 '+run.image_count+(run.error?' · '+run.error:'')));
      for(const p of s.posts){const row=el('article','record');row.append(link(p.title,p.url),el('p','hint',p.forum+'吧 · 兴趣 '+p.score+' / 100 · '+(p.analyzed?p.reason:'待分析')));if(p.share_preview||p.share_text)row.append(el('p','tieba-meme-copy',p.share_preview||p.share_text));row.append(p.archive?button('查看原文收藏',()=>viewArchive(p.archive.id)):button('收藏原文与全部配图',async()=>{await api('tieba/posts/'+p.tid+'/archive','POST');notice('已加入收藏队列，旧记录会重新读取完整来源');await load();}));records.append(row);}
      if(!s.posts.length)records.append(el('p','hint','还没有浏览记录，开启后会在空闲时浏览'));
      const usage=s.archive_stats;archiveSummary.textContent='原文收藏 · '+usage.count+' 条 · '+(usage.used_bytes/1024/1024).toFixed(1)+' / '+usage.limit_mb+' MB';
      renderArchives(archivePage?await api('tieba/archives?offset='+(archivePage*20)):s.archives);
      memes.replaceChildren();
      for(const m of s.memes){
        const row=el('article','record tieba-meme');
        row.append(el('strong','',m.phrase),el('p','hint',(!m.enabled?'已停用':m.expires_at<Date.now()/1000?'已过期':!s.enabled||!cfg.tieba_learn_enabled?'已暂停调用':'可调用')+' · 有效至 '+fmt(m.expires_at)),el('p','tieba-meme-copy',m.meaning),el('p','hint tieba-meme-copy','场景：'+(m.scene||'未设置')),link('来源帖子',m.source_url));
        const tools=el('div','tieba-archive-tools');tools.append(button('编辑这个梗',async()=>editMeme(m)),
          button(m.enabled?'停用 / 保留记录':'恢复使用',async()=>{await api('tieba/memes/'+m.id,'POST',{enabled:!m.enabled});await load();}),
          button('续期',async()=>{await api('tieba/memes/'+m.id,'POST',{renew:true});await load();}));row.append(tools);memes.append(row);
      }
      if(!s.memes.length)memes.append(el('p','hint','只收录能够解释且有原帖依据的梗，没有合适的就不收录'));
      pictures.replaceChildren();
      for(const p of s.pictures){const row=el('article','record');row.append(el('strong','',p.name),el('p','hint',p.description||p.state),link('原帖',p.post_url),el('p','hint','在“图片与生图 → 贴吧素材”里预览、编辑和停用'));pictures.append(row);}
      if(!s.pictures.length)pictures.append(el('p','hint','图片识别后进入独立的贴吧素材库，图片文件发送不等于微信原生表情'));
      shares.replaceChildren();for(const sh of s.shares){
        const row=el('article','record tieba-share');row.append(el('strong','',({queued:'待分享',sending:'正在发送',sent:'已确认发送',failed:'发送失败',uncertain:'结果待核对',cancelled:'已取消'}[sh.state]||sh.state)+' · '+chatName(sh.chat)),el('p','tieba-meme-copy',sh.text),el('p','hint',sh.error||fmt(sh.created)));
        if(sh.state==='queued')row.append(el('p','hint',s.enabled?'等待空闲发送，仍遵守分享时段、每日条数和间隔设置':'贴吧总开关关闭，发送已暂停'));
        if(sh.state==='queued')row.append(button('取消这条',async()=>{await api('tieba/shares/'+sh.id,'POST',{op:'cancel'});await load();}));
        if(sh.state==='uncertain')for(const [op,title] of [['received','确认已收到'],['not_received','确认未收到']])row.append(button(title,async()=>{await api('tieba/shares/'+sh.id,'POST',{op});await load();}));
        if(['failed','cancelled'].includes(sh.state))row.append(button('重试这条分享',async()=>{await api('tieba/shares/'+sh.id,'POST',{op:'retry'});notice('已重新排队，按当前分享限制发送');await load();}));
        shares.append(row);
      }
    }finally{loading=false;}
  }
  function refreshChatLabels(){for(const check of targets.querySelectorAll('input'))check.parentElement.lastChild.textContent=chatName(check.value);}
  window.tieba={populate,refresh:load,refreshChatLabels};
  if(cfg)populate();
  window.addEventListener('hashchange',()=>{if(location.hash==='#tieba-panel')load().catch(e=>notice(e.message,true));});
  setInterval(()=>{if(location.hash==='#tieba-panel'&&token)load().catch(()=>{});},12000);
})();
