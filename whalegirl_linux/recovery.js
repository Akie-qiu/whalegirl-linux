/* Manual controls stay together; automatic recovery runs on the server. */
(() => {
  const get = id => document.getElementById(id);
  const host = document.createElement('section');
  host.id = 'recovery-center'; host.className = 'recovery-center'; host.setAttribute('aria-label','异常与恢复');
  host.innerHTML = `<div class="recovery-heading"><div><strong>异常与恢复</strong><p id="recovery-summary">读取状态…</p></div><span id="recovery-badge" class="pill">读取中</span></div>
    <div class="recovery-toolbar"><div id="recovery-buttons" class="row"><button id="recovery-refresh" class="secondary" type="button">返回微信主页面</button><button id="recovery-open-issues" class="quiet" type="button">失败与待核对记录</button><a href="#login-panel">微信登录</a><a href="#model-panel">模型设置</a></div><label class="recovery-toggle"><input id="recovery-auto" type="checkbox">发送失败后自动恢复</label></div>
    <p id="recovery-detail" class="hint"></p><details id="recovery-issues"><summary>查看需要处理的记录</summary><div id="recovery-issue-list"></div></details>`;
  const alert = get('runtime-alert'); alert.parentNode.insertBefore(host, alert); host.append(alert);
  const buttons = get('recovery-buttons');
  for (const id of ['safety-check','safety-resume']) if (get(id)) buttons.prepend(get(id));
  get('safety-resume').textContent = '一键恢复发送'; get('safety-resume').className = 'primary';
  get('safety-check').textContent = '检查微信状态';
  let loading = false, issueSignature = '';

  async function loadIssues() {
    if (loading) return;
    loading = true;
    try {
      const data = await api('recovery/issues'), box = get('recovery-issue-list'); box.replaceChildren();
      const rows = [...data.pending, ...data.failed];
      if (!rows.length) box.textContent = '没有待核对或近期失败记录';
      for (const row of rows) {
        const article = document.createElement('article'); article.className = 'recovery-issue';
        const title = document.createElement('strong'); title.textContent = chatName(row.chat)+' · '+(row.state==='uncertain'?'回执待核对':'发送失败')+' · '+new Date(row.updated*1000).toLocaleString('zh-CN',{hour12:false}); article.append(title);
        for (const content of [row.state==='uncertain' ? row.reply || row.content : '', row.error]) {
          if (content) { const paragraph = document.createElement('p'); paragraph.textContent = content; article.append(paragraph); }
        }
        if (row.state==='uncertain') {
          const controls = document.createElement('div'); controls.className = 'row';
          for (const [label,received] of [['微信已收到',true],['确认未发送',false]]) {
            const button = document.createElement('button'); button.className = 'secondary'; button.textContent = label;
            button.onclick = () => action(button,async()=>{
              await api('review','POST',{key:row.key,received}); await loadIssues(); await update(); notice('已记录结果，不会自动补发旧消息');
            }); controls.append(button);
          }
          article.append(controls);
        }
        box.append(article);
      }
    } finally { loading = false; }
  }
  function stateUpdated(s) {
    const r=s.recovery || {}, busy=r.busy || ['pending','recovering'].includes(r.status), online=s.gateway?.status==='logged_in';
    get('recovery-summary').textContent = busy ? '正在自动返回聊天主界面并刷新表情位置，完成后继续处理新消息' : s.paused_reason || (!online ? '微信需要重新登录，点击右侧“微信登录”' : !s.enabled ? '自动回复总开关已关闭' : r.status==='awaiting_send' ? '界面已恢复，继续监听；下一次实际发送成功后清零失败计数' : r.enabled===false ? '自动恢复已关闭，可在这里手动恢复' : '正常运行，连续两次明确发送失败时会先自动恢复一次');
    get('recovery-badge').textContent = busy ? '自动恢复中' : s.paused_reason ? '需要处理' : !online ? '等待登录' : r.status==='awaiting_send' ? '等待发送确认' : !s.enabled ? '回复已关闭' : '运行正常';
    get('recovery-badge').className = 'pill'+(s.paused_reason && !busy ? ' warning' : ' online');
    if (!get('recovery-auto').disabled) get('recovery-auto').checked = r.enabled!==false;
    get('safety-resume').disabled = busy || !online; get('recovery-refresh').disabled = busy || !online;
    const parts = ['连续明确失败 '+(r.consecutive_failures || 0)+' 次'];
    if (r.completed_at) parts.push('最近整理 '+new Date(r.completed_at*1000).toLocaleString('zh-CN',{hour12:false}));
    if (r.error) parts.push(r.error);
    if (s.counts?.uncertain) parts.push('待核对 '+s.counts.uncertain+' 条，自动回复可继续');
    get('recovery-detail').textContent = parts.join(' · ');
    if (busy) { get('reply-status').textContent='自动恢复中'; get('activity').textContent='正在整理微信界面，随后继续处理新消息'; }
    const signature=JSON.stringify([r.completed_at,s.counts?.uncertain,s.counts?.failed,r.error]);
    if (get('recovery-issues').open && issueSignature!==signature) { issueSignature=signature; loadIssues().catch(error=>notice(error.message,true)); }
  }
  function openIssues() { get('recovery-issues').open=true; host.scrollIntoView({behavior:'smooth',block:'start'}); }
  get('recovery-open-issues').onclick=openIssues;
  get('recovery-issues').ontoggle=()=>{if(get('recovery-issues').open)loadIssues().catch(error=>notice(error.message,true));};
  get('safety-resume').onclick=()=>action(get('safety-resume'),async()=>{
    await api('recovery/run','POST',{resume:true}); await update(); notice('已整理微信并恢复发送，旧消息不会补发');
  });
  get('recovery-refresh').onclick=()=>action(get('recovery-refresh'),async()=>{
    await api('recovery/run','POST',{resume:false}); await update(); notice('已返回聊天主界面并刷新表情位置');
  });
  get('recovery-auto').onchange=async()=>{
    const input=get('recovery-auto'), enabled=input.checked; input.disabled=true;
    try { await api('recovery/config','POST',{enabled}); notice(enabled?'已开启发送失败自动恢复':'已关闭自动恢复'); }
    catch(error) { input.checked=!enabled; notice(error.message,true); }
    finally { input.disabled=false; await update(); }
  };
  window.recoveryCenter={stateUpdated,openIssues};
})();
