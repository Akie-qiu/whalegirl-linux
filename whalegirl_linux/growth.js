'use strict';
(() => {
  const section=document.createElement('details');section.className='settings-drawer';section.id='growth-console';
  section.innerHTML='<style>#growth-console .message{overflow-wrap:anywhere}#growth-console .two-fields>*{min-width:0}#growth-detail,#growth-editor{max-height:85vh;overflow:auto}#growth-console pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'+
    '<summary>互联网探索、表达与成长记忆</summary>'+
    '<p class="hint">B站与贴吧共用互联网成长记忆。来源原文、人物和事件默认不进入微信；表达经验可按来源开关及逐条权限共享。角色卡和微信私人记忆保持独立。</p>'+
    '<div class="two-fields">'+
    '<label class="check-label"><input id="growth-enabled" type="checkbox">启用自主成长与分层记忆</label>'+
    '<label class="check-label"><input id="life-enabled" type="checkbox">允许她在白名单内自主调整探索方式</label>'+
    '<label class="check-label"><input id="growth-traits-chat" type="checkbox">成熟兴趣可影响相关微信回复</label>'+
    '<label class="check-label"><input id="growth-auto" type="checkbox">自动启用有效新认识（关闭后待审核）</label>'+
    '<label class="check-label"><input id="bili-enabled" type="checkbox">启用B站自主学习</label>'+
    '<label class="check-label"><input id="growth-tieba" type="checkbox">启用贴吧自主学习</label>'+
    '<label class="check-label"><input id="growth-bili-expression" type="checkbox">B站表达经验可自动用于微信</label>'+
    '<label class="check-label"><input id="growth-tieba-expression" type="checkbox">贴吧表达经验可自动用于微信</label>'+
    '<label>贴吧每日最多阅读轮次（不限制B站）<input id="growth-reads" type="number" min="0" max="20"></label>'+
    '<label>闲时探索间隔 / 分钟<input id="growth-interval" type="number" min="30" max="1440"></label></div>'+
    '<label>互联网探索与统一反思所用模型卡片<select id="growth-worker"><option value="flash">Flash（默认DeepSeek）</option><option value="pro">Pro</option><option value="responses">Responses</option><option value="custom">自定义</option><option value="current">跟随当前聊天模型</option></select></label>'+
    '<p id="growth-worker-state" class="hint">复用模型设置中的独立API、Key和模型名，不切换聊天模型；私聊记忆整理仍使用当前聊天模型。</p>'+
    '<details><summary>兴趣与探索倾向</summary><label>探索新主题积极性<input id="growth-explore" type="range" min="0" max="100"><output id="growth-explore-value"></output></label>'+
    '<label>起始兴趣（一行一个，可留空）<textarea id="growth-interests" rows="3"></textarea></label></details>'+
    '<details id="life-settings" open><summary>生活自治参数与事务记录</summary><p class="hint">白名单只开放探索开放度和探索间隔。每次调整先生成预览，再由她显式提交应用；页面分开展示配置写入、调度器采用和后续真实探索证据。账号、权限、费用保护、人格底色和代码不能由此修改。</p><div id="life-parameters" class="bento-grid"></div><div id="life-transactions" class="bento-grid"></div></details>'+
    '<p class="hint">没有累计Token限额。以下仅统计真实或明确标记的估算消耗；单次请求、上下文、频率、超时、资源与平台限制仍生效。关闭学习不等于撤销已批准的表达共享；两个表达开关可分别关闭。</p>'+
    '<div class="panel-footer"><span id="growth-status" class="hint" role="status"></span><button id="growth-save" class="primary" type="button">保存成长设置</button></div>'+
    '<div class="row"><button id="growth-run" class="primary" type="button">给她一次探索机会</button><button id="growth-compact" class="secondary" type="button">整理同类记忆</button><button id="growth-refresh" class="secondary" type="button">刷新记录</button></div>'+
    '<details id="growth-tasks" open><summary>探索任务与执行记录</summary><div id="growth-runs"></div></details>'+
    '<h3>统一互联网认识与情境表达</h3><p class="hint">下方为最近240条，可翻阅更早记录。</p><button id="growth-earlier" class="secondary" type="button">翻阅全部认识</button><div id="growth-insights" class="bento-grid"></div>'+
    '<h3>人格成长状态与来源证据</h3><p class="hint">兴趣与认识经历多次、跨来源验证后才进入成熟态；反证先标记为受挑战，不会单条反向材料就翻转。</p><p id="growth-personality-window" class="hint"></p><div id="growth-personality-states" class="bento-grid"></div>'+
    '<h3>表达习惯卡</h3><p class="hint">按来源分别积累；至少有3条可核验互动证据才会生成。聊天只在匹配场景且对应来源开关开启时使用。</p><div id="growth-style-cards" class="bento-grid"></div>'+
    '<details><summary>来源、阅读范围与反思</summary><div id="growth-journals"></div></details>'+
    '<details><summary>近30天用量（不限制学习）</summary><div id="growth-usage-history"></div></details>'+
    '<details><summary>微信记忆概括与深层细节</summary><div id="growth-summaries"></div></details>'+
    '<dialog id="growth-detail" class="tieba-archive-dialog"><div class="section-top"><h3 id="growth-detail-title">来源与反思</h3><button id="growth-detail-close" type="button" class="secondary">关闭</button></div><div id="growth-detail-body"></div></dialog>'+
    '<dialog id="growth-editor" class="tieba-archive-dialog"><form id="growth-edit-form"><div class="section-top"><h3>编辑互联网成长认识</h3><button id="growth-edit-close" class="secondary" type="button">关闭</button></div>'+
    '<label>内容<textarea id="growth-edit-content" rows="5" maxlength="700" required></textarea></label>'+
    '<label class="check-label"><input id="growth-edit-locked" type="checkbox">锁定，后台不能替代</label>'+
    '<label class="check-label"><input id="growth-edit-learning" type="checkbox">参与后续自主学习</label>'+
    '<label class="check-label"><input id="growth-edit-chat" type="checkbox">管理员允许用于微信聊天</label>'+
    '<p class="hint">两个使用权限独立。共享表达只提供泛化场景、语气和改写例，不自动输入原始评论、人物资料或原帖全文；表达仍受对应来源开关控制。</p>'+
    '<div class="row"><button id="growth-edit-save" class="primary" type="submit">保存修改</button><button id="growth-edit-lifecycle" class="secondary" type="button">停用认识</button></div></form></dialog>';
  $('memory-panel').append(section);
  let loading=false,lastFetch=0,editing=null,lifePreview=null;
  const el=(tag,cls='',value='')=>{const n=document.createElement(tag);n.className=cls;n.textContent=value;return n;};
  const button=(label,fn)=>{const b=el('button','secondary',label);b.type='button';b.onclick=()=>action(b,fn);return b;};
  const when=value=>value?new Date(value*1000).toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'}):'—';
  const number=value=>value==null?'未上报':Number(value).toLocaleString('zh-CN');
  const states={queued:'排队',planning:'选择材料',reading:'读取',reflecting:'理解与反思',done:'完成',failed:'失败',skipped:'跳过',interrupted:'中止'};
  const kinds={bilibili:'B站',tieba:'贴吧',world:'历史网页流程',memory:'记忆整理',explore:'探索'};
  const categories={expression:'情境表达',interest:'兴趣',knowledge:'知识',persona:'可修正认识'};
  const maturityLabels={candidate:'候选',emerging:'形成中',stable:'成熟',stable_challenged:'成熟（有新反证，继续观察）',challenged:'受反证挑战',weakened:'已弱化',withdrawn:'来源已撤回'};
  function populate(){
    if(!cfg)return;
    for(const [id,key] of [['growth-enabled','growth_enabled'],['life-enabled','life_autonomy_enabled'],['growth-traits-chat','growth_mature_traits_chat_enabled'],['growth-auto','growth_auto_apply'],['bili-enabled','bilibili_enabled'],['growth-tieba','growth_tieba_enabled'],['growth-bili-expression','growth_bilibili_expression_shared'],['growth-tieba-expression','growth_tieba_expression_shared']])$(id).checked=!!cfg[key];
    $('growth-reads').value=cfg.growth_reads_per_day??3;$('growth-interval').value=cfg.growth_interval_minutes??180;
    const worker=$('growth-worker');worker.replaceChildren();
    for(const [id,name] of [['current','跟随聊天模型'],...Object.entries(cfg.model_profiles||{}).map(([id,p])=>[id,p.name||p.model||id])]){
      const option=document.createElement('option');option.value=id;option.textContent=name;worker.append(option);
    }
    worker.value=cfg.growth_internet_model_profile??'flash';
    $('growth-explore').value=cfg.growth_exploration??25;$('growth-explore-value').textContent=$('growth-explore').value+'/100';$('growth-interests').value=(cfg.growth_interests||[]).join('\n');
    if(section.open)refresh().catch(error=>notice(error.message,true));
  }
  async function showJournal(id){
    const journal=await api('growth/journals/'+encodeURIComponent(id));$('growth-detail-title').textContent=journal.topic||'来源与反思';const box=$('growth-detail-body');box.replaceChildren();
    const labels={encounter:'实际接触的内容',speech_summary:'已核验的字幕口语总结',expression_summary:'综合表达观察（含评论与弹幕）',facts:'材料事实',author_view:'作者观点',inference:'推测与不确定性',previous_judgment:'原来的理解',revision:'修正',missing:'未覆盖部分'};
    for(const [key,label] of Object.entries(labels))if(journal.body[key])box.append(el('h3','',label),el('p','message',journal.body[key]));
    for(const source of journal.sources){const d=el('details','record'),a=el('a','',source.title||'来源');a.href=source.url;a.target='_blank';a.rel='noopener noreferrer';d.append(el('summary','',source.title||'实际读取材料'),a,el('pre','hint',JSON.stringify(source.coverage||{},null,2)),el('pre','message',source.body));box.append(d);}
    $('growth-detail').showModal();
  }
  function edit(row){editing=row;$('growth-edit-content').value=row.content;$('growth-edit-locked').checked=!!row.locked;$('growth-edit-learning').checked=!!row.learning_enabled;$('growth-edit-chat').checked=!!row.chat_enabled;$('growth-edit-lifecycle').textContent=row.status==='forgotten'?'恢复认识':'停用认识';$('growth-editor').showModal();}
  async function manageTraitMemory(id){
    const page=await api('growth/insights?limit=1&before='+(Number(id)+1));
    const row=page.items.find(item=>item.id===Number(id));
    if(!row)throw new Error('这条成长认识已不在当前角色范围内，请刷新记录');
    edit(row);
  }
  async function personalityHistory(traitKey='',before=0){
    const page=await api('growth/personality/revisions?limit=40&trait_key='+encodeURIComponent(traitKey)+'&before='+before);
    $('growth-detail-title').textContent='人格认识的变化历史';const box=$('growth-detail-body');box.replaceChildren();
    for(const item of page.items){
      const card=el('article','record'),previous=item.before||{},current=item.after||{};
      card.append(el('strong','',(categories[current.category]||current.category||'认识')+' · '+(current.topic||item.trait_key)),
        el('p','hint',when(item.created)+' · '+(maturityLabels[previous.status]||previous.status||'尚未形成')+' → '+(maturityLabels[current.status]||current.status||'—')),
        el('p','message',item.reason||'未记录原因'),
        el('p','hint','支持 '+(previous.support_count??0)+' → '+(current.support_count??0)+' · 反证 '+(previous.challenge_count??0)+' → '+(current.challenge_count??0)+' · 来源 '+(previous.source_count??0)+' → '+(current.source_count??0)));
      box.append(card);
    }
    if(!page.items.length)box.append(el('p','hint','当前角色还没有这项认识的生命周期变更记录。'));
    if(page.next)box.append(button('更早变化',()=>personalityHistory(traitKey,page.next)));
    if(before)box.append(button('返回最新变化',()=>personalityHistory(traitKey)));
    if(!$('growth-detail').open)$('growth-detail').showModal();
  }
  async function lifeEvents(identity,before=0){
    const page=await api('life-autonomy/'+encodeURIComponent(identity)+'/events?limit=40&before='+before);
    $('growth-detail-title').textContent='生活参数的完整事件历史';const box=$('growth-detail-body');box.replaceChildren();
    box.append(el('p','hint','共 '+page.event_total+' 条事件，本页 '+page.items.length+' 条；数据库保留完整历史。'));
    for(const event of page.items)box.append(el('p','hint',when(event.created)+' · '+event.stage+' · '+JSON.stringify(event.details)));
    if(page.next)box.append(button('更早事件',()=>lifeEvents(identity,page.next)));
    if(before)box.append(button('返回最新事件',()=>lifeEvents(identity)));
    if(!$('growth-detail').open)$('growth-detail').showModal();
  }
  async function earlier(before=0){
    const page=await api('growth/insights?limit=80&before='+before);$('growth-detail-title').textContent='互联网认识历史';const box=$('growth-detail-body');box.replaceChildren();
    for(const row of page.items){const card=el('article','record');card.append(el('p','hint','#'+row.id+' · '+(kinds[row.source_kind]||'历史来源')+' · '+row.status),el('p','message',row.content),button('编辑与使用权限',()=>{$('growth-detail').close();edit(row);}),button('来源与覆盖',()=>showJournal(row.journal_id)),button('历史修订',()=>revisions(row)));box.append(card);}
    if(page.next)box.append(button('下一页',()=>earlier(page.next)));if(before)box.append(button('返回最新',()=>earlier()));$('growth-detail').showModal();
  }
  async function summaryDetails(summary){
    const details=await Promise.all(summary.detail_ids.slice(0,8).map(id=>api('memory-hub/'+id)));
    $('growth-detail-title').textContent='概括与原始深层记忆';const box=$('growth-detail-body');box.replaceChildren(el('p','message',summary.summary));
    for(const value of details){box.append(el('h3','','#'+value.memory.id),el('p','message',value.memory.content));for(const evidence of value.evidence||[])if(evidence.excerpt)box.append(el('p','hint',evidence.excerpt));}
    $('growth-detail').showModal();
  }
  $('growth-earlier').onclick=()=>action($('growth-earlier'),()=>earlier());
  async function revisions(row){const value=await api('growth/insights/'+row.id+'/revisions');$('growth-detail-title').textContent='认识 #'+row.id+' 的修订';const box=$('growth-detail-body');box.replaceChildren();for(const item of value.memory){const content=JSON.parse(item.after_json||'{}');box.append(el('p','hint',when(item.created)+' · '+item.actor+' · '+item.operation),el('p','message',content.content||''));}for(const item of value.permissions)box.append(el('p','hint','权限修订 · '+when(item.created)),el('pre','message',item.before_json+' → '+item.after_json));$('growth-detail').showModal();}
  function taskRows(box,runs){
    box.replaceChildren();for(const run of runs){const row=el('article','record');row.append(el('strong','',(kinds[run.kind]||run.kind)+' · '+(states[run.state]||run.state)),el('p','hint','任务ID：'+run.id),el('p','hint','创建 '+when(run.created)+' · 结束 '+when(run.finished)),el('p','hint','探索尝试 1 · 取得材料 '+run.actual_readings+' · 形成认识 '+(run.result.learned||0)+' · '+number(run.tokens)+' Token'+(run.estimated?'（含估算）':'')),el('p','message',run.error||run.result.reason||run.events?.[0]?.reason||'等待执行'));
      const events=el('details');events.append(el('summary','','执行过程'));for(const event of [...(run.events||[])].reverse())events.append(el('p','hint',when(event.created)+' · '+event.stage+' · '+event.reason));row.append(events);box.append(row);}
    if(!runs.length)box.append(el('p','hint','尚无任务；点击探索会先保存任务ID，未读取时不制造观看记录。'));
  }
  async function refresh(){
    if(!cfg||loading)return;loading=true;
    try{const [state,life]=await Promise.all([api('growth'),api('life-autonomy')]),usage=state.usage;
      if(state.internet_worker)$('growth-worker-state').textContent='当前互联网学习模型：'+state.internet_worker.model+'（'+state.internet_worker.effective_profile+'） · '+(state.internet_worker.configured?'密钥已配置':'该模型卡片缺少密钥，请到模型设置填写')+'。与聊天模型独立，复用对应API和Key。';
      $('growth-status').textContent=(state.enabled?(state.busy?'后台学习中':'等待空闲'):'学习关闭')+' · 无累计Token限额 · 逐次统计：输入 '+number(usage.input_tokens)+' / 输出 '+number(usage.output_tokens)+' / 总量 '+number(usage.reported_total_tokens)+(usage.estimated_requests?'（含估算）':'')+' · 旧版累计口径 '+number(usage.tokens)+(usage.estimated?'（含旧估算）':'')+' · 尝试 '+usage.attempts+' / 实际阅读 '+usage.reads+' · 贴吧轮次 '+usage.tieba_reads+'/'+usage.read_limit+' · B站视频数量不设上限'+(state.last_error?' · '+state.last_error:'');
      taskRows($('growth-runs'),state.runs);
      const maturity={candidate:'候选',emerging:'形成中',stable:'成熟',stable_challenged:'成熟（有新反证，继续观察）',challenged:'受反证挑战',weakened:'已弱化',withdrawn:'来源已撤回'};
      $('growth-personality-window').textContent='共有 '+(state.personality_total??(state.personality_states||[]).length)+' 项成长认识，本页'+(state.personality_truncated?'显示最近 ':'显示 ')+(state.personality_states||[]).length+' 项；每项最多显示最近 '+(state.personality_evidence_limit??8)+' 条有效证据。完整证据保留在数据库，变化历史可单独查看。';
      const traits=$('growth-personality-states');traits.replaceChildren();for(const item of state.personality_states||[]){const card=el('article','record');card.append(el('strong','',(categories[item.category]||item.category)+' · '+item.topic+' · '+(maturity[item.status]||item.status)),el('p','hint','支持 '+item.support_count+' · 反证 '+item.challenge_count+' · 不同来源 '+item.source_count+' · 最近 '+when(item.last_seen)));for(const proof of item.evidence||[])card.append(el('p','hint',when(proof.created)+' · '+(kinds[proof.source_kind]||proof.source_kind)+' · '+proof.stance+' · 来源 '+String(proof.source_id).slice(0,12)+'… · '+String(proof.excerpt||'')));const controls=el('div','row');controls.append(button('变化历史',()=>personalityHistory(item.trait_key)));for(const id of [...new Set((item.evidence||[]).map(proof=>proof.memory_id))])controls.append(button('管理认识 #'+id,()=>manageTraitMemory(id)));card.append(controls);traits.append(card);}if(!traits.childElementCount)traits.append(el('p','hint','还没有带可核验证据的兴趣或人格认识。'));
      const lifeParameters=$('life-parameters');lifeParameters.replaceChildren();for(const item of life.parameters||[]){const card=el('article','record');card.append(el('strong','',item.label+' · 当前 '+item.value+item.unit),el('p','hint',item.effect));const input=document.createElement('input');input.type='number';input.min=item.minimum;input.max=item.maximum;input.value=item.value;input.id='life-value-'+item.key;const reason=document.createElement('input');reason.type='text';reason.maxLength=180;reason.placeholder='说明与实际经历或兴趣的关系';reason.id='life-reason-'+item.key;card.append(input,reason,button('预览调整',async()=>{lifePreview=await api('life-autonomy/preview','POST',{parameter:item.key,value:Number(input.value),reason:reason.value});await refresh();}));if(lifePreview?.parameter===item.key)card.append(el('p','hint',lifePreview.label+'：'+lifePreview.from+' → '+lifePreview.to+' '+lifePreview.unit+'；'+lifePreview.risk),button('应用这项预览',async()=>{const applied=await api('life-autonomy/apply','POST',{id:lifePreview.id});lifePreview=null;notice(applied.meaning);await refresh();}));lifeParameters.append(card);}if(!lifeParameters.childElementCount)lifeParameters.append(el('p','hint','没有可调整参数。'));
      const transactionStates={previewed:'已预览',proposed:'待应用',applied:'配置已写入',adopted:'调度器已采用',observed:'行为已观察',rolled_back:'已回滚',conflict:'冲突未应用',rollback_conflict:'回滚冲突',failed:'失败'};
      const lifeTransactions=$('life-transactions');lifeTransactions.replaceChildren();for(const tx of life.transactions||[]){const card=el('article','record');card.append(el('strong','',(life.parameters.find(item=>item.key===tx.parameter)?.label||tx.parameter)+' · '+(transactionStates[tx.state]||tx.state)),el('p','hint','预览 '+tx.previous_value+' → '+tx.target_value+' · '+(tx.initiator==='ai'?'她发起':'管理员发起')+' · '+when(tx.created)),el('p','message',tx.reason||tx.error||''));if(tx.state==='previewed'||tx.state==='proposed')card.append(button('应用预览',async()=>{const result=await api('life-autonomy/apply','POST',{id:tx.id});notice(result.meaning);await refresh();}));if(['applied','adopted','observed'].includes(tx.state)){card.append(button('核对运行状态',async()=>{const result=await api('life-autonomy/'+tx.id+'/observe','POST',{});notice('配置'+(result.configured?'已读回':'不一致')+' · 匹配的真实行为 '+result.behavior.count+' 次');await refresh();}),button('回滚',async()=>{const result=await api('life-autonomy/'+tx.id+'/rollback','POST',{reason:'管理员在后台请求回滚'});notice('已回滚到 '+result.value);await refresh();}));}card.append(el('p','hint','共 '+(tx.event_total??(tx.events||[]).length)+' 条事件，显示最近 '+(tx.events||[]).length+' 条'+(tx.events_truncated?'；完整历史可分页查看':'。')));for(const event of tx.events||[])card.append(el('p','hint',when(event.created)+' · '+event.stage+' · '+JSON.stringify(event.details)));if(tx.events_next)card.append(button('更早事件',()=>lifeEvents(tx.id,tx.events_next)));lifeTransactions.append(card);}if(!lifeTransactions.childElementCount)lifeTransactions.append(el('p','hint','还没有生活参数变更记录。'));
      const notes=$('growth-insights');notes.replaceChildren();for(const row of state.insights){const card=el('article','record');card.append(el('p','hint','#'+row.id+' · '+(kinds[row.source_kind]||'历史来源')+' · '+(categories[row.category]||row.category)+' · '+row.status+(row.locked?' · 已锁定':'')),el('p','message',row.content),el('p','hint','后续学习：'+(row.learning_enabled?'允许':'不使用')+' · 微信使用：'+(row.chat_enabled?'已授权':'隔离')));
        if(row.conditions)card.append(el('p','hint','适用：'+row.conditions));if(row.counterexamples)card.append(el('p','hint','例外：'+row.counterexamples));if(row.examples?.length)card.append(el('p','message','泛化改写例：'+row.examples.join(' / ')));
        const tools=el('div','row');tools.append(button('编辑与使用权限',()=>edit(row)),button('来源与覆盖',()=>showJournal(row.journal_id)),button('历史修订',()=>revisions(row)));
        if(['active','pending','forgotten'].includes(row.status))tools.append(button(row.status==='forgotten'?'恢复':row.status==='pending'?'审核启用':'停用',async()=>{await api('growth/insights/'+row.id,'POST',{op:row.status==='forgotten'?'restore':row.status==='pending'?'update':'archive',version:row.version,...(row.status==='pending'?{status:'active'}:{})});await refresh();}));card.append(tools);notes.append(card);}
      if(!state.insights.length)notes.append(el('p','hint','真正有用、具备来源证据的认识才会保留，不要求每次学习凑数。'));
      const styleCards=$('growth-style-cards');styleCards.replaceChildren();for(const item of state.style_cards||[]){const card=el('article','record');card.append(el('strong','',(kinds[item.source_kind]||item.source_kind)+' · '+item.evidence_count+'条互动依据 · '+when(item.updated)),el('p','hint',!item.enabled?'此来源开关关闭，暂不进入微信':item.evidence_valid?'原始证据有效，可在匹配场景下参考':'原始证据已失效，暂停参考并等待重新归纳'),el('p','message',item.card.summary||''));for(const pattern of item.card.patterns||[])card.append(el('p','hint',pattern.situation+' · '+[pattern.rhythm,...(pattern.conversation_moves||[]),pattern.emotion_response].filter(Boolean).join('；')));styleCards.append(card);}if(!styleCards.childElementCount)styleCards.append(el('p','hint','目前还没有达到证据数量要求的表达习惯卡。'));
      const journals=$('growth-journals');journals.replaceChildren();for(const journal of state.journals){const row=el('article','record');row.append(el('strong','',journal.topic||'一次互联网阅读'),el('p','hint',when(journal.created)),el('p','message',journal.body.revision||journal.body.encounter||''),button('来源与反思',()=>showJournal(journal.id)));journals.append(row);}
      const history=$('growth-usage-history');history.replaceChildren();const totals=state.history_usage;history.append(el('p','hint','费用：'+(totals.costs.length?totals.costs.map(item=>Number(item.value).toPrecision(6)+' '+item.currency+(item.estimated_reports?'（含估算）':'')).join('；'):'接口未上报金额')+' · '+totals.cost_unreported+'次请求未上报费用；不以0猜测。'));
      for(const day of totals.history)history.append(el('p','hint',day.day+' · 输入 '+number(day.input_tokens)+' · 输出 '+number(day.output_tokens)+' · 总量 '+number(day.total_tokens)+' · '+day.requests+' 次请求'));
      const summaries=$('growth-summaries');summaries.replaceChildren();for(const summary of state.summaries){const row=el('article','record');row.append(el('p','hint',(summary.valid?'概括有效':'来源已变更')+' · '+chatName(summary.chat)),el('p','message',summary.summary),el('p','hint','深层ID：'+summary.detail_ids.join('、')),button('查看原始细节',()=>summaryDetails(summary)));summaries.append(row);}
      lastFetch=Date.now();
    }finally{loading=false;}
  }
  $('growth-save').onclick=()=>action($('growth-save'),async()=>{
    for(const id of ['growth-reads','growth-interval'])if(!$(id).reportValidity())return;
    await saveSubset({growth_enabled:$('growth-enabled').checked,growth_mature_traits_chat_enabled:$('growth-traits-chat').checked,growth_auto_apply:$('growth-auto').checked,growth_tieba_enabled:$('growth-tieba').checked,growth_bilibili_expression_shared:$('growth-bili-expression').checked,growth_tieba_expression_shared:$('growth-tieba-expression').checked,growth_internet_model_profile:$('growth-worker').value,growth_reads_per_day:Number($('growth-reads').value),growth_interval_minutes:Number($('growth-interval').value),growth_exploration:Number($('growth-explore').value),growth_interests:$('growth-interests').value.split('\n').map(value=>value.trim()).filter(Boolean)});notice('成长设置已保存，累计Token不限制学习');await refresh();
  });
  $('growth-explore').oninput=()=>$('growth-explore-value').textContent=$('growth-explore').value+'/100';
  for(const [id,kind] of [['growth-run','explore'],['growth-compact','memory']])$(id).onclick=()=>action($(id),async()=>{const result=await api('growth/run','POST',{kind});$('growth-tasks').open=true;notice('任务 '+result.id+'：'+(states[result.state]||result.state)+(result.reason?' · '+result.reason:''));await refresh();});
  $('growth-refresh').onclick=()=>action($('growth-refresh'),refresh);$('growth-detail-close').onclick=()=>$('growth-detail').close();$('growth-edit-close').onclick=()=>$('growth-editor').close();
  $('growth-edit-form').onsubmit=event=>{event.preventDefault();action($('growth-edit-save'),async()=>{await api('growth/insights/'+editing.id,'POST',{op:'update',version:editing.version,content:$('growth-edit-content').value.trim(),locked:$('growth-edit-locked').checked,learning_enabled:$('growth-edit-learning').checked,chat_enabled:$('growth-edit-chat').checked});$('growth-editor').close();await refresh();});};
  $('growth-edit-lifecycle').onclick=()=>action($('growth-edit-lifecycle'),async()=>{await api('growth/insights/'+editing.id,'POST',{op:editing.status==='forgotten'?'restore':'archive',version:editing.version});$('growth-editor').close();await refresh();});
  section.addEventListener('toggle',()=>{if(section.open&&cfg)refresh().catch(error=>notice(error.message,true));});
  window.growthConsole={populate,refresh,taskRows,stateUpdated(){if(section.open&&Date.now()-lastFetch>5000)refresh().catch(error=>notice(error.message,true));}};
  if(cfg)populate();
})();
