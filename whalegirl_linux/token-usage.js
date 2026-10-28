'use strict';
(() => {
  const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
  const panel=el('section','panel span-all');panel.id='token-usage-panel';
  const header=el('div','section-top'),heading=el('div');heading.append(el('span','section-index','TOKEN USAGE'),el('h2','','Token 统计'));
  const toolbar=el('div','row'),period=el('select');period.setAttribute('aria-label','Token 统计时间范围');
  for(const [value,name] of [['today','今天'],['7d','最近7天'],['30d','最近30天'],['all','累计']]){const o=el('option','',name);o.value=value;period.append(o);}
  const refresh=el('button','secondary','刷新');refresh.type='button';toolbar.append(period,refresh);header.append(heading,toolbar);panel.append(header);
  const grid=el('div','stat-grid'),cards={};
  for(const [field,name] of [['total_tokens','总 Token'],['input_tokens','输入 Token'],['output_tokens','输出 Token'],['cached_tokens','缓存命中 Token'],['uncached_tokens','缓存未命中 Token'],['cache_hit_rate','缓存命中率']]){
    const card=el('div','stat');cards[field]=el('strong','','—');card.append(el('span','',name),cards[field]);grid.append(card);
  }
  grid.style.marginTop='18px';panel.append(grid);
  const extra=el('p','timing-detail'),coverage=el('p','hint'),status=el('p','hint','读取统计中…'),error=el('p','error-text');panel.append(extra,coverage,status,error);
  const tables=el('div','settings-grid');tables.style.alignItems='start';tables.style.gridTemplateColumns='repeat(auto-fit,minmax(min(100%,300px),1fr))';
  const models=el('details'),operations=el('details'),days=el('details');models.append(el('summary','','按模型查看'));operations.append(el('summary','','按功能查看'));days.append(el('summary','','按天查看'));const modelRows=el('div'),operationRows=el('div'),dayRows=el('div');models.append(modelRows);operations.append(operationRows);days.append(dayRows);tables.append(operations,models,days);panel.append(tables);
  panel.append(el('p','hint','输入已包含缓存命中，输出已包含推理 Token，均不会重复相加。只统计接口实际返回的用量；未提供的字段不会按 0 猜测。失败或中断且未返回用量的请求，实际消耗可能无法获知。'));
  $('records-panel').before(panel);const nav=el('a','','Token 统计');nav.href='#token-usage-panel';document.querySelector('nav').append(nav);
  let loading=false;
  const number=(value,requests)=>value==null?(requests?'未提供':'0'):Number(value).toLocaleString('zh-CN');
  function rows(box,items,label){
    box.replaceChildren();
    for(const r of items){const card=el('article','record');card.append(el('strong','',label(r)),el('p','hint',
      '输入 '+number(r.input_tokens,r.requests)+' · 输出 '+number(r.output_tokens,r.requests)+' · 缓存命中 '+number(r.cached_tokens,r.requests)+' · 命中率 '+(r.cache_hit_rate==null?'未提供':r.cache_hit_rate+'%')+' · '+r.requests+' 次请求'));box.append(card);}
    if(!items.length)box.append(el('p','hint','这个时间范围还没有请求记录'));
  }
  async function load(){
    if(loading||!cfg)return;loading=true;refresh.disabled=true;period.disabled=true;
    try{
      const s=await api('token-usage?period='+encodeURIComponent(period.value)),r=s.summary;
      for(const [field,n] of Object.entries(cards))n.textContent=field==='cache_hit_rate'?(r[field]==null?'未提供':r[field]+'%'):number(r[field],r.requests);
      extra.textContent='接口请求 '+r.requests+' 次（含重试） · 成功 '+r.successful+' / 失败 '+r.failed+' · 联网搜索与抓取 '+r.search_requests+' 次 · 平均耗时 '+(r.average_ms==null?'—':(r.average_ms/1000).toFixed(2)+'s');
      coverage.textContent='缓存写入 '+number(r.cache_write_tokens,r.requests)+' · 推理 '+number(r.reasoning_tokens,r.requests)+' · 总量回执 '+r.total_tokens_reports+' / '+r.requests+' 次 · 缓存字段回执 '+r.cached_tokens_reports+' / '+r.model_requests+' 次模型请求';
      status.textContent='从 '+new Date(s.started_at*1000).toLocaleString('zh-CN')+' 开始记录 · 北京时间 · 重启及切换模型后保留 · 更新于 '+new Date().toLocaleTimeString('zh-CN');error.textContent='';
      rows(operationRows,s.operations||[],r=>({chat:'普通聊天',tool_followup:'工具结果整理',memory:'记忆整理',proactive:'主动聊天',moments:'朋友圈',tieba:'贴吧相关',image:'图片与识图',sticker:'表情包',connection_test:'模型连接测试',search:'联网搜索',fetch:'网页抓取'}[r.operation]||r.operation));
      rows(modelRows,s.models,r=>({search:'联网搜索',fetch:'网页抓取',tieba:'贴吧浏览 · '+r.model,tieba_image:'贴吧图片识别 · '+r.model}[r.operation]||r.operation)+' · '+r.provider);
      rows(dayRows,s.days,r=>r.day);
    }catch(e){error.textContent='用量读取失败：'+e.message;}finally{loading=false;refresh.disabled=false;period.disabled=false;}
  }
  refresh.onclick=load;period.onchange=load;nav.onclick=load;
  window.tokenUsage={refresh:load};if(cfg)load();
  setInterval(()=>{if(cfg&&!$('dashboard').hidden&&location.hash==='#token-usage-panel')load();},15000);
})();
