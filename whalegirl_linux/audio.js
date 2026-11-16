'use strict';
(() => {
  const main = document.querySelector('.content-grid');
  if (!main || document.getElementById('audio-console')) return;
  const panel = document.createElement('section');
  panel.id = 'audio-console'; panel.className = 'panel span-all';
  panel.innerHTML = `
    <style>#audio-console .two-fields>*{min-width:0}#audio-console audio{width:100%}#audio-console .audio-job{border-top:1px solid var(--line,#ddd);padding:12px 0}#audio-console .audio-job small{display:block;overflow-wrap:anywhere}#audio-console select[multiple]{min-height:110px}#audio-console .audio-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:18px}#audio-console output{font-variant-numeric:tabular-nums}</style>
    <div class="section-top"><div><span class="section-index">AUDIO / TOOLS</span><h2>语音与唱歌</h2></div><span id="audio-state" class="pill">读取中</span></div>
    <p class="hint">AI根据消息选择语音、原创歌曲或纯音乐工具。声线保存后下一次生成采用，无需重启；正在生成的任务使用其已绑定版本。完整歌曲默认发送音频文件。</p>
    <div class="two-fields">
      <label class="check-label"><input id="audio-enabled" type="checkbox">启用音频工具</label>
      <label class="check-label"><input id="audio-voice-enabled" type="checkbox">允许语音回复</label>
      <label class="check-label"><input id="audio-music-enabled" type="checkbox">允许唱歌和配乐</label>
      <label class="check-label"><input id="audio-ai-voice-dynamics-enabled" type="checkbox">AI按语境微调单句声音</label>
      <label>语音使用方式<select id="audio-voice-mode"><option value="auto">AI按语境自主选择</option><option value="requested">仅用户明确要求时使用</option></select></label>
      <label>音乐使用方式<select id="audio-music-mode"><option value="requested">仅用户明确要求时使用</option><option value="auto">允许AI自主选择创作</option></select></label>
      <label>音频授权会话（不选时沿用现有微信授权）<select id="audio-chats" multiple></select></label>
    </div>
    <p id="audio-tool-state" class="hint"></p>
    <strong>电脑声音模型</strong>
    <div class="row"><button id="audio-pc-check" type="button" class="secondary">检查电脑语音连接</button><span id="audio-pc-state" class="hint">电脑语音通过专用加密通道运行；电脑需保持开机和联网。</span></div>
    <div id="audio-pc-voices" class="row" aria-label="电脑声线切换"></div>
    <p class="hint">点击昔涟或八千代切换实际模型，等待当前语音结束后加载。电脑与后台保持同步；切换会影响所有使用电脑语音的会话。</p>
    <div class="audio-grid">
      <details open><summary>语音模型 · 独立配置</summary><label>配置卡<select id="audio-speech-profile"></select></label><label>API地址<input id="audio-speech-url" type="url"></label><label>模型名称<input id="audio-speech-model"></label><label>API Key（留空保留）<input id="audio-speech-key" type="password" autocomplete="new-password"></label><small id="audio-speech-key-state"></small></details>
      <details open><summary>音乐模型 · 独立配置</summary><label>配置卡<select id="audio-music-profile"></select></label><label>API地址<input id="audio-music-url" type="url"></label><label>模型名称<input id="audio-music-model"></label><label>API Key（留空保留）<input id="audio-music-key" type="password" autocomplete="new-password"></label><small id="audio-music-key-state"></small></details>
    </div>
    <details open><summary>实时声线调节</summary>
      <div class="two-fields"><label>编辑预设<select id="audio-preset"></select></label><label>预设名称<input id="audio-preset-name" maxlength="40"></label><label>模型音色ID<input id="audio-voice" maxlength="80"></label><label class="check-label"><input id="audio-ai-preset" type="checkbox">允许AI选择此预设</label></div>
      <p class="hint">下方为基础声线，滑块停止操作后自动保存。开启单句声音微调后，AI可在每次语音工具中选择语速、音高和音量；电脑模型另支持句片段停顿。语速按引擎能力调节，音高和音量通过本地滤镜处理。固定参考音频仍影响情绪表现。</p>
      <div class="two-fields"><label>音高 / 半音 <output id="audio-pitch-value"></output><input id="audio-pitch" type="range" min="-3" max="3" step="0.1"></label><label>语速 / 倍 <output id="audio-speed-value"></output><input id="audio-speed" type="range" min="0.7" max="1.4" step="0.01"></label><label>音量 / dB <output id="audio-gain-value"></output><input id="audio-gain" type="range" min="-12" max="6" step="0.5"></label></div>
      <div class="row"><button id="audio-new-preset" type="button" class="secondary">另存为新预设</button><button id="audio-default-preset" type="button" class="secondary">设为默认声线</button><button id="audio-reset-tuning" type="button" class="secondary">恢复此预设原音高与语速</button></div><p id="audio-default-note" class="hint"></p>
      <label>歌唱人声偏好<textarea id="audio-singing-style" rows="2" maxlength="400"></textarea></label><p class="hint">歌曲人声风格作为下一首创作的引导，不能保证与朗读声相同。未验证的气声、甜度、声音克隆参数不会伪装成已生效。</p>
    </details>
    <details><summary>资源与长语音</summary><div class="two-fields"><label>原生语音最长秒数<input id="audio-voice-max" type="number" min="5" max="50"></label><label>超过时长<select id="audio-overflow"><option value="file">改为音频文件</option><option value="hold">保留后台，等待处理</option></select></label><label>默认音乐期望秒数<input id="audio-duration" type="number" min="5" max="180"></label><label>请求超时 / 秒<input id="audio-timeout" type="number" min="30" max="600"></label><label>单文件下载上限 / MiB<input id="audio-download" type="number" min="1" max="32"></label><label>音频缓存容量 / MiB<input id="audio-cache" type="number" min="32" max="1024"></label><label>磁盘保留空间 / MiB<input id="audio-reserve" type="number" min="256" max="4096"></label></div><p class="hint">每个音频任务独立生成。确认送达后自动清理文件；后台试听保留24小时。失败、可重试和送达待核对任务保留文件；任务、回执与费用记录保留。低内存或低磁盘时只暂停新音频，不暂停普通聊天。</p></details>
    <div class="panel-footer"><span id="audio-save-state" class="hint" role="status"></span><button id="audio-save" type="button" class="primary">保存设置与密钥</button><button id="audio-reload" type="button" class="secondary">重新读取配置</button></div>
    <details open><summary>试听与作品预览</summary><label>朗读文字<textarea id="audio-preview-text" rows="2" maxlength="1200">早呀，今天想听我说点什么？</textarea></label><div class="row"><button id="audio-preview" type="button" class="primary">生成试音（按秒收费）</button><button id="audio-tune" type="button" class="secondary">复用已有试音调参（不调用模型）</button></div><label>原创歌曲或纯音乐描述<textarea id="audio-music-prompt" rows="2" maxlength="1600" placeholder="曲风、乐器、人声、语言和情绪"></textarea></label><label>歌词（可选）<textarea id="audio-lyrics" rows="3" maxlength="5000"></textarea></label><label class="check-label"><input id="audio-instrumental" type="checkbox">纯音乐，不使用歌词</label><button id="audio-preview-music" type="button" class="secondary">生成音乐预览（按次收费）</button><p class="hint">上述按钮只在后台预览，不发微信。超时可能已扣费，不会自动再生成。</p><audio id="audio-player" controls preload="none"></audio><p id="audio-player-note" class="hint"></p></details>
    <p id="audio-usage" class="hint"></p><div class="row"><button id="audio-refresh" type="button" class="secondary">刷新任务</button><select id="audio-history"></select><button id="audio-restore" type="button" class="secondary">恢复选中配置版本</button></div><div id="audio-jobs"></div>`;
  main.append(panel);
  const nav = document.querySelector('.sidebar nav');
  if (nav) { const a = document.createElement('a'); a.href = '#audio-console'; a.textContent = '♫ 语音与唱歌'; nav.append(a); }
  const get = id => document.getElementById('audio-' + id);
  let snapshot = null, draft = null, revision = 0, presetId = '', dirty = false, editSerial = 0;
  let pendingLoad = false, pendingLoadForce = false, pendingLoadSerial = null, pendingLoadWaiters = [];
  let saving = false, loading = false, lastFetch = 0, timer, playerUrl = '', auditionSource = '';
  const keys = {}, shownProfiles = {};
  const previewRequestIds = new Map();
  let pcState=null, pcFetching=false, pcLastFetch=0, switchingVoice='';
  const states = {queued:'排队',generating:'生成中',downloading:'下载与处理',download_retry:'等待重新下载',ready:'成品就绪',sending:'发送中',sent:'已送达',expired:'试听已过期',cancelled:'已取消',outcome_unknown:'生成结果与费用待核对',needs_review:'送达待核对',download_failed:'下载失败',failed_before_send:'明确发送前失败',failed:'处理失败',held:'待处理'};
  const el = (tag, value='', cls='') => { const node=document.createElement(tag); node.textContent=value; node.className=cls; return node; };
  const options = (node, entries, selected) => { node.replaceChildren(); entries.forEach(([id,name])=>{const o=new Option(name,id);o.selected=Array.isArray(selected)?selected.includes(id):selected===id;node.add(o);}); };
  function rememberProvider(kind) { if (!draft) return; const id=shownProfiles[kind]; if (!draft.providers[id]) return; draft.providers[id].api_url=get(kind+'-url').value;draft.providers[id].model=get(kind+'-model').value;if(get(kind+'-key').value)keys[id]=get(kind+'-key').value;else delete keys[id]; }
  function showProvider(kind) { const id=get(kind+'-profile').value,p=draft.providers[id];if(!p)return;shownProfiles[kind]=id;get(kind+'-url').value=p.api_url;get(kind+'-url').readOnly=p.backend==='pc_bridge';get(kind+'-model').value=p.model;get(kind+'-key').value=keys[id]||'';get(kind+'-key-state').textContent=p.backend==='pc_bridge'?(snapshot.keys_configured[id]?'电脑专用认证已配置；模型和参考音频留在电脑':'尚未配置电脑通道认证'):(snapshot.keys_configured[id]?'密钥已配置':'尚未配置独立密钥');if(kind==='speech')get('preview').textContent=p.backend==='pc_bridge'?'电脑生成试音（不调用收费语音 API）':'生成试音（按秒收费）'; }
  function rememberPreset() { if(!draft||!draft.presets[presetId])return;Object.assign(draft.presets[presetId],{name:get('preset-name').value,voice:get('voice').value,pitch:Number(get('pitch').value),speed:Number(get('speed').value),gain_db:Number(get('gain').value)});const ids=new Set(draft.ai_preset_ids);get('ai-preset').checked?ids.add(presetId):ids.delete(presetId);draft.ai_preset_ids=[...ids]; }
  function showPreset() { const p=draft.presets[presetId];get('preset-name').value=p.name;get('voice').value=p.voice;get('pitch').value=p.pitch;get('speed').value=p.speed;get('gain').value=p.gain_db;get('ai-preset').checked=draft.ai_preset_ids.includes(presetId);showTuning();get('default-note').textContent='当前默认：'+draft.presets[draft.default_preset].name+'；保存后下一次生成采用。'; }
  function showTuning() { ['pitch','speed','gain'].forEach(id=>get(id+'-value').textContent=get(id).value); }
  function renderConfig() {
    const s=draft;
    ['enabled','voice-enabled','music-enabled','ai-voice-dynamics-enabled'].forEach(id=>get(id).checked=!!s[id.replaceAll('-','_')]);
    get('voice-mode').value=s.voice_mode;get('music-mode').value=s.music_mode;
    for(const kind of ['speech','music']) { options(get(kind+'-profile'),Object.entries(s.providers).filter(([,p])=>p.kind===kind).map(([id,p])=>[id,p.model]),s[kind+'_profile']);showProvider(kind); }
    presetId=s.presets[presetId]?presetId:s.default_preset;options(get('preset'),Object.entries(s.presets).map(([id,p])=>[id,p.name]),presetId);showPreset();
    const available=new Set([...(typeof cfg!=='undefined'&&cfg?cfg.allowed_chat_ids:[]),...s.allowed_chat_ids]);
    const labels=new Map((typeof chats!=='undefined'?chats:[]).map(c=>[c.id,c.name]));options(get('chats'),[...available].map(id=>[id,labels.get(id)||id]),s.allowed_chat_ids);
    get('singing-style').value=s.singing_style;
    for(const [id,key] of Object.entries({'voice-max':'voice_max_seconds',overflow:'voice_overflow',duration:'music_duration',timeout:'request_timeout',download:'max_download_mb',cache:'cache_mb',reserve:'disk_reserve_mb'}))get(id).value=s[key];
  }
  function readForm() {
    rememberPreset();rememberProvider('speech');rememberProvider('music');
    for(const id of ['enabled','voice-enabled','music-enabled','ai-voice-dynamics-enabled'])draft[id.replaceAll('-','_')]=get(id).checked;
    draft.voice_mode=get('voice-mode').value;draft.music_mode=get('music-mode').value;draft.speech_profile=get('speech-profile').value;draft.music_profile=get('music-profile').value;
    draft.allowed_chat_ids=[...get('chats').selectedOptions].map(o=>o.value);draft.singing_style=get('singing-style').value;
    for(const [id,key] of Object.entries({'voice-max':'voice_max_seconds',duration:'music_duration',timeout:'request_timeout',download:'max_download_mb',cache:'cache_mb',reserve:'disk_reserve_mb'}))draft[key]=Number(get(id).value);
    draft.voice_overflow=get('overflow').value;return structuredClone(draft);
  }
  async function save() {
    if(!draft)throw new Error('请先读取音频配置');
    if(saving)throw new Error('声线正在保存，请稍后再试');
    clearTimeout(timer);saving=true;const serial=editSerial;get('save-state').textContent='保存中…';
    const body={revision,settings:readForm(),keys:{...keys}};
    try {
      const result=await api('audio/config','POST',body);
      // New input entered while this request ran belongs to the next save.
      rememberProvider('speech');rememberProvider('music');
      snapshot=result;revision=result.revision;
      for(const [id,value] of Object.entries(body.keys))if(keys[id]===value){delete keys[id];for(const kind of ['speech','music'])if(shownProfiles[kind]===id&&get(kind+'-key').value===value)get(kind+'-key').value='';}
      if(serial===editSerial){dirty=false;draft=structuredClone(result.settings);renderConfig();}
      get('save-state').textContent='已保存 v'+revision+'，下一次生成生效'+(dirty?'；新修改待保存':'');renderState(result);
    }
    catch(error){get('save-state').textContent=error.message;throw error;}
    finally{saving=false;if(dirty&&serial!==editSerial)scheduleSave();flushPendingLoad();}
  }
  function changed() { dirty=true;editSerial++;get('save-state').textContent=saving?'保存中；新修改待保存':'有未保存的修改'; }
  function scheduleSave() { clearTimeout(timer);timer=setTimeout(()=>{if(!saving)save().catch(e=>notice(e.message,true));},700); }
  function jobButton(text, fn) { const b=el('button',text,'secondary');b.type='button';b.onclick=()=>action(b,fn);return b; }
  async function play(job) {
    const response=await fetch('/api/audio/jobs/'+encodeURIComponent(job.id)+'/media',{headers:{Authorization:'Bearer '+token},cache:'no-store'});
    if(!response.ok)throw new Error('音频读取失败或登录已失效');const blob=await response.blob();if(playerUrl)URL.revokeObjectURL(playerUrl);playerUrl=URL.createObjectURL(blob);get('player').src=playerUrl;get('player-note').textContent='任务 '+job.id+' · '+job.duration.toFixed(2)+' 秒 · 配置 v'+(job.applied.revision??'—');if(job.kind==='speech')auditionSource=job.render_of||job.id;await get('player').play().catch(()=>{});
  }
  function renderState(result) {
    get('state').textContent=(result.settings.enabled?'已开启':'已关闭')+' · v'+result.revision;
    get('tool-state').textContent=result.tool_support.available?'当前模型接口支持自主选择语音和音乐工具。'+result.tool_support.note:'当前模型接口不支持音频工具，聊天会继续按原文字流程运行。'+result.tool_support.note;
    const u=result.usage,r=result.resources;get('usage').textContent='已提交生成 '+u.submitted_requests+' 次；语音原音频 '+Number(u.speech_seconds||0).toFixed(2)+' 秒；音乐 '+Number(u.music_requests||0)+' 次。已上报Token '+Number(u.total_tokens||0)+'（'+u.token_reported_requests+' 次请求上报，'+Number(u.token_missing_requests||0)+' 次未上报，不计作零）。'+u.billing+'。缓存 '+(r.cached_bytes/1048576).toFixed(1)+' MiB，磁盘剩余 '+(r.disk_free_bytes/1048576).toFixed(0)+' MiB。';
    options(get('history'),result.history.map(h=>[String(h.revision),'配置 v'+h.revision]),get('history').value);
    const list=get('jobs');list.replaceChildren();
    for(const job of result.jobs) { const box=el('div','','audio-job');box.append(el('strong',(job.kind==='speech'?'语音':'音乐')+' · '+(states[job.state]||job.state)+(job.destination==='preview'?' · 后台预览':'')));box.append(el('small',job.id+' · '+new Date(job.created*1000).toLocaleString('zh-CN')+' · '+job.duration.toFixed(2)+' 秒 · 采用配置 v'+(job.applied.revision??'待开始')));if(job.error)box.append(el('p',job.error,'hint'));const row=el('div','','row');if(job.playable)row.append(jobButton('播放',()=>play(job)));if(['queued','generating','downloading','ready','sending','held','download_retry'].includes(job.state))row.append(jobButton('取消本地任务',async()=>{await api('audio/jobs/'+job.id+'/actions','POST',{operation:'cancel'});await load(false);}));if(job.state==='download_failed')row.append(jobButton('重试下载（不重新生成）',async()=>{await api('audio/jobs/'+job.id+'/actions','POST',{operation:'retry_download'});await load(false);}));if(job.can_retry_send)row.append(jobButton('重试发送（不重新生成）',async()=>{await api('audio/jobs/'+job.id+'/actions','POST',{operation:'retry_send'});await load(false);}));if(job.can_check_delivery)row.append(jobButton('仅核对原生回执',async()=>{await api('audio/jobs/'+job.id+'/actions','POST',{operation:'check_delivery'});await load(false);}));if(job.state==='held')row.append(jobButton('改为文件发送',async()=>{await api('audio/jobs/'+job.id+'/actions','POST',{operation:'send_file'});await load(false);}));box.append(row);list.append(box); }
    result.jobs.forEach((job,index)=>{
      if(!job.applied?.delivery||!job.applied?.preset)return;
      const p=job.applied.preset,n=job.applied.native_synthesis;
      const detail='本条实际采用：语速 '+p.speed.toFixed(2)+'× · 音高 '+(p.pitch>=0?'+':'')+p.pitch.toFixed(1)+' 半音 · 音量 '+(p.gain_db>=0?'+':'')+p.gain_db.toFixed(1)+' dB'+(n?.fragment_interval!=null?' · 分句间隔 '+n.fragment_interval.toFixed(2)+' 秒':'');
      const box=list.children[index];box.insertBefore(el('p',detail,'hint'),box.lastElementChild);
    });
  }
  function flushPendingLoad() {
    if (!pendingLoad || loading || saving) return;
    const force = pendingLoadForce, serial = pendingLoadSerial, waiters = pendingLoadWaiters;
    pendingLoad = false; pendingLoadForce = false; pendingLoadSerial = null; pendingLoadWaiters = [];
    Promise.resolve().then(() => load(force, serial)).then(
      () => waiters.forEach(waiter => waiter.resolve()),
      error => waiters.forEach(waiter => waiter.reject(error))
    );
  }
  async function load(force=false, requestedSerial=null) {
    if (loading || saving) {
      const serial = requestedSerial ?? editSerial;
      if (!pendingLoad || force || !pendingLoadForce) pendingLoadSerial = serial;
      pendingLoad = true; pendingLoadForce ||= force;
      return new Promise((resolve, reject) => pendingLoadWaiters.push({resolve, reject}));
    }
    loading = true; const serial = requestedSerial ?? editSerial;
    try {
      const result = await api('audio'); snapshot = result; lastFetch = Date.now();
      if ((force || !draft) && serial === editSerial) {
        clearTimeout(timer); draft = structuredClone(result.settings); revision = result.revision; dirty = false; renderConfig();
      }
      renderState(result);
    } finally { loading = false; flushPendingLoad(); }
  }
  panel.addEventListener('input',event=>{if(!draft)return;if(['audio-preview-text','audio-music-prompt','audio-lyrics','audio-instrumental'].includes(event.target.id))return;changed();if(['audio-pitch','audio-speed','audio-gain'].includes(event.target.id)){showTuning();scheduleSave();}});
  for(const kind of ['speech','music'])get(kind+'-profile').addEventListener('change',()=>{rememberProvider(kind);draft[kind+'_profile']=get(kind+'-profile').value;showProvider(kind);changed();});
  get('preset').onchange=()=>{rememberPreset();presetId=get('preset').value;showPreset();};
  get('new-preset').onclick=()=>{rememberPreset();const id='voice-'+Date.now().toString(36);draft.presets[id]=structuredClone(draft.presets[presetId]);draft.presets[id].name+=' 副本';presetId=id;options(get('preset'),Object.entries(draft.presets).map(([k,p])=>[k,p.name]),id);showPreset();changed();};
  get('default-preset').onclick=()=>{rememberPreset();draft.default_preset=presetId;changed();showPreset();scheduleSave();};
  get('reset-tuning').onclick=()=>{get('pitch').value=0;get('speed').value=1;get('gain').value=0;showTuning();changed();scheduleSave();};
  get('save').onclick=()=>action(get('save'),save);
  function renderPcState(state) {
    const pending=['queued','stopping','loading','rolling_back'].includes(state.voice_switch?.phase);
    if(state.connected&&!pending&&state.active_voice===switchingVoice)switchingVoice='';
    if(['failed','cancelled'].includes(state.voice_switch?.phase))switchingVoice='';
    const busy=pending||!!switchingVoice, names={cyrene:'昔涟',yachiyo:'八千代',xiaofei:'小菲'};
    get('pc-state').textContent=busy?'正在切换声线，等待当前语音结束并重新加载…':state.connected?'电脑已就绪 · '+(names[state.active_voice]||state.presets.join('、'))+(state.busy?' · 正在合成':' · 空闲'):state.reason;
    if(state.voice_switch?.error)get('pc-state').textContent+='；'+state.voice_switch.error;
    const configured=draft?.providers[draft.speech_profile]?.backend==='pc_bridge'?Object.entries({cyrene:'昔涟',yachiyo:'八千代',xiaofei:'小菲'}).filter(([id])=>Object.values(draft.presets).some(p=>p.voice===id)).map(([id,name])=>({id,name})):[];
    const choices=state.voices?.length?state.voices:(pcState?.voices?.length?pcState.voices:configured), row=get('pc-voices');row.replaceChildren();
    for(const voice of choices){
      const selected=state.active_voice===voice.id&&state.connected&&!busy;
      const engineLabel=voice.backend==='aniflive'?'TensorRT 加速':voice.backend==='gsv-lite'?'CUDA 图加速':voice.backend==='gpt-sovits'?'兼容':'等待电脑';
      const button=jobButton(voice.name+' · '+engineLabel+(selected?' · 使用中':''),async()=>{
        if(dirty)await save();
        const id=requestId('voice-switch',voice.id);
        const result=await api('audio/pc-select','POST',{voice_id:voice.id,request_id:id});
        previewRequestIds.delete('voice-switch');switchingVoice=result.phase==='completed'?'':voice.id;
        renderPcState({...state,voice_switch:result});await refreshPc();
      });
      button.disabled=busy||selected||!state.connected;
      if(selected)button.setAttribute('aria-pressed','true');
      row.append(button);
    }
  }
  async function refreshPc(){
    if(pcFetching)return;pcFetching=true;
    try{
      const state=await api('audio/pc-status'), previous=pcState?.active_voice;
      renderPcState(state);pcState={...state,voices:state.voices?.length?state.voices:(pcState?.voices||[])};pcLastFetch=Date.now();
      const differs=draft&&draft.presets[draft.default_preset]?.voice!==state.active_voice;
      if(state.connected&&!dirty&&!saving&&(state.preset_synchronized||previous&&previous!==state.active_voice||differs)){presetId='';await load(true);}
    }finally{pcFetching=false;}
  }
  get('pc-check').onclick=()=>action(get('pc-check'),refreshPc);
  get('reload').onclick=()=>action(get('reload'),()=>load(true));get('refresh').onclick=()=>action(get('refresh'),()=>load(false));
  get('restore').onclick=()=>action(get('restore'),async()=>{if(!get('history').value)throw new Error('没有可恢复版本');await api('audio/restore','POST',{revision,target:Number(get('history').value)});await load(true);});
  const requestId=(key,fingerprint)=>{const previous=previewRequestIds.get(key);if(previous?.fingerprint===fingerprint)return previous.id;const bytes=crypto.getRandomValues(new Uint8Array(16));const value={id:Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join(''),fingerprint};previewRequestIds.set(key,value);return value.id;};
  get('preview').onclick=()=>action(get('preview'),async()=>{await save();const args={text:get('preview-text').value,voice_preset_id:presetId},fingerprint=JSON.stringify(args),id=requestId('speech',fingerprint);const job=await api('audio/preview','POST',{kind:'speech',arguments:args,acknowledge_charge:true,request_id:id});previewRequestIds.delete('speech');auditionSource=job.id;notice(draft.providers[draft.speech_profile].backend==='pc_bridge'?'已排队电脑试音；不调用收费语音 API':'已排队付费试音；生成后可在下方播放');await load(false);});
  get('preview-music').onclick=()=>action(get('preview-music'),async()=>{await save();const args={prompt:get('music-prompt').value,lyrics:get('lyrics').value,instrumental:get('instrumental').checked},fingerprint=JSON.stringify(args),id=requestId('music',fingerprint);await api('audio/preview','POST',{kind:'music',arguments:args,acknowledge_charge:true,request_id:id});previewRequestIds.delete('music');notice('已排队音乐预览，不会发送微信');await load(false);});
  get('tune').onclick=()=>action(get('tune'),async()=>{await save();const source=auditionSource||snapshot.jobs.find(j=>j.kind==='speech'&&j.playable&&!j.render_of)?.id;if(!source)throw new Error('请先生成一段试音');const args={source_id:source,preset_id:presetId},fingerprint=JSON.stringify(args),id=requestId('tune',fingerprint);await api('audio/tune','POST',{...args,request_id:id});previewRequestIds.delete('tune');notice('已排队本地调参，不调用模型');await load(false);});
  setInterval(()=>{const dashboard=document.getElementById('dashboard');if(typeof token==='undefined'||!token||!dashboard||dashboard.hidden||document.hidden)return;const active=snapshot?.jobs.some(j=>['queued','generating','downloading','sending','download_retry'].includes(j.state));if(Date.now()-lastFetch>(active?2500:15000))load(false).catch(()=>{});if(Date.now()-pcLastFetch>(switchingVoice?1800:12000))refreshPc().catch(()=>{});},3000);
})();
