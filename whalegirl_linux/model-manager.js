/* Model editor state stays in memory; saved secrets never reach the browser. */
(() => {
  'use strict';
  const root=document.getElementById('model-panel');if(!root?.classList.contains('model-manager'))return;
  const get=id=>document.getElementById(id), clone=v=>JSON.parse(JSON.stringify(v));
  const state={saved:{},drafts:new Map(),keys:new Map(),origins:new Map(),selected:'',active:'',serial:0,busy:false};
  const fields=['mm-name','api-url','api-key','model','api-style','max-tokens','model-temperature','reasoning-effort','model-stream-enabled',
    'mm-price-mode','mm-price-currency','mm-price-input','mm-price-output','mm-price-cached','mm-price-write'];
  const defaults=()=>({name:'新模型配置',api_url:'',api_style:'openai',model:'',max_tokens:220,model_temperature:1.2,
    reasoning_effort:'default',model_stream_enabled:false,pricing:{mode:'official',currency:'CNY',input:null,output:null,cached:null,write:null}});
  function profile(value){const d=defaults(),v=value||{};return {...d,...v,pricing:{...d.pricing,...v.pricing}};}
  function clean(value){const p=profile(value);delete p.api_key_configured;return p;}
  function current(){return profile(state.drafts.get(state.selected)||state.saved[state.selected]);}
  function dirty(id){return state.drafts.has(id)&&(!state.saved[id]||JSON.stringify(clean(state.drafts.get(id)))!==JSON.stringify(clean(state.saved[id])))||!!state.keys.get(id);}
  function status(message,error=false){get('test-result').textContent=message;get('test-result').classList.toggle('error',error);}
  const nullable=id=>get(id).value.trim()===''?null:Number(get(id).value);
  function capture(){
    if(!state.selected)return;
    const pricing={mode:get('mm-price-mode').value,currency:get('mm-price-currency').value};
    if(pricing.mode==='custom')for(const key of ['input','output','cached','write'])pricing[key]=nullable('mm-price-'+key);
    state.drafts.set(state.selected,{name:get('mm-name').value.trim(),api_url:get('api-url').value.trim(),api_style:get('api-style').value,
      model:get('model').value.trim(),max_tokens:Number(get('max-tokens').value),model_temperature:nullable('model-temperature'),
      reasoning_effort:get('reasoning-effort').value,model_stream_enabled:get('model-stream-enabled').checked,pricing});
    state.keys.set(state.selected,get('api-key').value);
  }
  function summaries(){
    const mode=get('mm-price-mode').value,custom=mode==='custom';
    get('mm-custom-prices').hidden=!custom;get('mm-currency-field').hidden=!custom;
    get('mm-price-input').required=custom;get('mm-price-output').required=custom;
    get('mm-price-summary').textContent=custom?'自定义 · '+get('mm-price-currency').value:mode==='none'?'只统计用量':'官方参考价';
    get('mm-price-hint').textContent=custom?'单价单位为每百万 Token。输入和输出必须填写；免费项目请明确填 0。缓存留空时按输入单价计算。':mode==='none'?'继续记录完整 Token，用量不受影响；此配置不猜测费用。':'匹配已核验的官方参考价；没有价格的模型会标为未定价，可改用自定义单价。';
    const style=get('api-style').value,url=get('api-url').value;
    const responses=style==='responses'||style==='auto'&&/\/responses\/?$/i.test(url);
    const anthropic=style==='anthropic'||style==='auto'&&/anthropic|\/messages\/?$/i.test(url);
    get('model-temperature').max=anthropic?'1':'2';
    get('model-temperature-hint').textContent=responses?'Responses 接口当前不发送温度参数；设置仍会保存。':
      '普通回复与主动聊天共用。'+(anthropic?'此接口范围 0～1。':'范围 0～2，较高时表达通常更多样。')+'留空使用原默认。';
    const total=[...new Set([...Object.keys(state.saved),...state.drafts.keys()])].filter(dirty).length;
    get('mm-unsaved').textContent=total?total+' 份配置有未保存修改':'';
    get('mm-activate').hidden=state.selected===state.active;
    get('save-model').classList.toggle('primary',state.selected===state.active);
    get('save-model').classList.toggle('secondary',state.selected!==state.active);
    get('save-model').textContent=state.selected===state.active?'保存并生效':'保存配置';
  }
  function renderList(){
    const box=get('mm-profiles');box.replaceChildren();
    const query=get('mm-search').value.trim().toLocaleLowerCase();
    const ids=[...new Set([...Object.keys(state.saved),...state.drafts.keys()])];
    for(const id of ids){
      const p=profile(state.drafts.get(id)||state.saved[id]);
      if(query&&![p.name,p.model,p.api_url].join(' ').toLocaleLowerCase().includes(query))continue;
      const button=document.createElement('button');button.type='button';button.className='mm-profile';button.dataset.profileId=id;
      button.setAttribute('aria-current',String(id===state.selected));
      const top=document.createElement('span');top.className='mm-profile-top';
      const name=document.createElement('span');name.textContent=p.name||p.model||'未命名配置';
      if(dirty(id)){const dot=document.createElement('span');dot.className='mm-draft-dot';dot.textContent='•';dot.title='有未保存修改';name.append(dot);}
      top.append(name);if(id===state.active){const badge=document.createElement('span');badge.className='mm-active-badge';badge.textContent='使用中';top.append(badge);}
      const sub=document.createElement('small');sub.textContent=p.model||'尚未选择模型';button.append(top,sub);
      button.onclick=()=>{capture();select(id);};box.append(button);
    }
    if(!box.childElementCount){const p=document.createElement('p');p.className='mm-empty';p.textContent='没有匹配的配置';box.append(p);}
  }
  function select(id){
    state.selected=id;const p=current();
    const values={'mm-name':p.name,'api-url':p.api_url,'api-style':p.api_style,'model':p.model,'max-tokens':p.max_tokens,
      'model-temperature':p.model_temperature??'','reasoning-effort':p.reasoning_effort,'api-key':state.keys.get(id)||'',
      'mm-price-mode':p.pricing.mode,'mm-price-currency':p.pricing.currency};
    for(const key of ['input','output','cached','write'])values['mm-price-'+key]=p.pricing[key]??'';
    for(const [key,value] of Object.entries(values))get(key).value=value;
    get('model-stream-enabled').checked=p.model_stream_enabled;
    const copied=state.origins.get(id);
    get('key-status').textContent=state.saved[id]?.api_key_configured?'此配置已保存密钥，留空保留。':copied?'保存时会在服务器内复制来源配置的密钥。':'尚未保存此配置的密钥。';
    get('mm-model-options').replaceChildren();get('mm-catalog-status').textContent='支持手动填写；拉取列表不会启用或切换模型。';
    status(state.saved[id]?'选中配置供编辑；当前使用的模型以“使用中”标记为准。':'新配置尚未保存。');
    summaries();renderList();
  }
  function populate(config){
    state.saved=clone(config.model_profiles||{});state.active=config.active_model_profile;
    const selected=state.selected&&(state.saved[state.selected]||state.drafts.has(state.selected))?state.selected:state.active;
    const active=state.saved[state.active];get('mm-current').textContent=active?'当前使用：'+(active.name||active.model)+' / '+config.model:'尚未启用模型配置';
    if(selected)select(selected);
  }
  function add(copy=false){
    capture();const origin=state.saved[state.selected]?state.selected:state.origins.get(state.selected);
    const from=copy?clean(current()):defaults(),key=copy?state.keys.get(state.selected)||'':'';
    if(copy)from.name=(from.name||from.model||'模型').slice(0,76)+' 副本';
    const id='draft-'+(++state.serial);state.drafts.set(id,from);state.keys.set(id,key);
    if(copy&&origin)state.origins.set(id,origin);
    get('mm-search').value='';select(id);get('mm-name').focus();get('mm-name').select();
  }
  function validate(){
    for(const input of root.querySelectorAll('input,select')){
      if(input.closest('#mm-custom-prices')&&get('mm-price-mode').value!=='custom')continue;
      if(!input.checkValidity()){input.reportValidity();throw new Error('请检查 '+(input.closest('label')?.firstChild?.textContent?.trim()||'模型参数'));}
    }
    capture();if(!current().model)throw new Error('请填写或选择模型名称');
  }
  async function run(message,fn){
    if(state.busy)return;state.busy=true;status(message);
    for(const b of root.querySelectorAll('button'))b.disabled=true;
    try{const pending=fn();root.inert=true;root.setAttribute('aria-busy','true');await pending;}catch(error){status(error.message,true);notice(error.message,true);}
    finally{root.inert=false;root.removeAttribute('aria-busy');state.busy=false;for(const b of root.querySelectorAll('button'))b.disabled=false;}
  }
  function sourceId(){return state.saved[state.selected]?state.selected:state.origins.get(state.selected)||null;}
  async function save(activate){
    validate();const oldId=state.selected,p=clean(current()),key=get('api-key').value;
    const enabled=activate||state.selected===state.active;
    let result,id=oldId;
    if(!state.saved[oldId]){
      result=await api('model-profiles/create','POST',{profile:p,api_key:key,activate:enabled,
        copy_from:state.origins.get(oldId)||null,copy_key:!!state.origins.get(oldId)&&!key});
      id=result.profile_id;cfg=result.config;
    }else cfg=await api('model-profiles','POST',{profile_id:oldId,profile:p,api_key:key,activate:enabled});
    state.drafts.delete(oldId);state.keys.delete(oldId);state.origins.delete(oldId);state.selected=id;
    window.dispatchEvent(new CustomEvent('whalegirl-panel-saved',{detail:'model-panel'}));
    populateSettings();status(enabled?'配置已保存并启用。':'配置已保存，当前使用的模型未切换。');notice(enabled?'模型配置已生效。':'模型配置已保存。');
  }
  get('mm-add').onclick=()=>add();get('mm-copy').onclick=()=>add(true);get('mm-search').oninput=renderList;
  get('save-model').onclick=()=>run('正在保存配置…',()=>save(false));
  get('mm-activate').onclick=()=>run('正在保存并启用…',()=>save(true));
  get('mm-fetch-models').onclick=()=>run('正在读取接口模型列表…',async()=>{
    if(!get('api-url').value.trim()||!get('api-url').checkValidity())throw new Error('请先填写有效的接口地址');
    capture();const p=current();
    const result=await api('model-profiles/models','POST',{profile_id:sourceId(),api_url:p.api_url,api_style:p.api_style,api_key:get('api-key').value});
    const box=get('mm-model-options');box.replaceChildren();for(const m of result.models){const o=document.createElement('option');o.value=m.id;box.append(o);}
    get('mm-catalog-status').textContent='已读取 '+result.count+' 个模型'+(result.partial?'（接口仅返回部分列表）':'')+'，点击模型输入框选择或输入筛选。';
    status('模型列表已读取，当前模型未切换。');get('model').focus();
  });
  get('test-model').onclick=()=>run('正在测试这份配置；不会切换当前模型…',async()=>{
    validate();const r=await api('model-profiles/test','POST',{profile_id:sourceId(),profile:clean(current()),api_key:get('api-key').value});
    if(!r.ok)throw new Error('连接未通过：'+r.message);
    status('连接成功 · '+(r.elapsed_ms/1000).toFixed(2)+' 秒。测试使用少量 Token，已计入用量。');
  });
  for(const id of fields){const input=get(id);const changed=()=>{capture();summaries();renderList();
    if(['api-url','api-style','api-key'].includes(id)){get('mm-model-options').replaceChildren();get('mm-catalog-status').textContent='接口设置已变更，可重新拉取模型列表。';}
    status('有未保存的修改。');};input.addEventListener('input',changed);input.addEventListener('change',changed);}
  window.addEventListener('beforeunload',event=>{if([...new Set([...state.drafts.keys(),...state.keys.keys()])].some(dirty)){event.preventDefault();event.returnValue='';}});
  window.modelManager={populate,select,capture,sync:summaries};
  if(typeof cfg!=='undefined'&&cfg)populate(cfg);
})();
