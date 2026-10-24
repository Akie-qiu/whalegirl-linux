'use strict';
(() => {
  const panel = $('persona');
  const fields = ['name','tagline','avatar','accent','mode','identity','personality','speaking_style',
    'scenario','interests','private_style','group_style','boundaries','examples','extra','raw_prompt'];
  const tabs = ['details','relationship','preview'];
  let library = null, selectedId = '', draft = null, saved = '', busy = false, tab = 'details';
  let filter = 'all', pending = null, previewSequence = 0;
  const clone = value => JSON.parse(JSON.stringify(value));
  const node = (tag, cls, value) => { const n = document.createElement(tag); if (cls) n.className = cls; if (value != null) n.textContent = value; return n; };
  const field = key => $('pc-' + key.replaceAll('_','-'));
  const chosen = () => library?.cards.find(row => row.id === selectedId);
  const dirty = () => draft && JSON.stringify(read()) !== saved;
  function read() {
    const value = {};
    for (const key of fields) value[key] = field(key).value.trim();
    return value;
  }
  function error(message = '') { text('pc-error-message',message); $('pc-error').hidden = !message; }
  async function run(fn) {
    if (busy) return;
    busy = true; error(); renderButtons();
    try { await fn(); } catch (e) { error(e.message); }
    finally { busy = false; renderButtons(); }
  }
  function renderActive() {
    const row = library.cards.find(c => c.id === library.active_id);
    const card = row?.card;
    text('pc-active-avatar',card?.avatar || '✦');
    text('pc-active-name',card?.name || '当前自定义人设');
    text('pc-active-caption',row && row.revision !== library.active_revision ? '使用之前启用的版本 · 编辑后的设定还未应用' : (card?.tagline || '下一次回复会使用这张角色卡'));
    text('pc-library-count',library.cards.filter(r => !r.archived).length + ' 张可用');
  }
  function renderCards() {
    const box = $('pc-cards'); box.replaceChildren();
    if (!library) return;
    const query = $('pc-search').value.trim().toLocaleLowerCase();
    const rows = library.cards.filter(row => (filter==='archived' ? row.archived : !row.archived) &&
      (filter!=='custom' || !row.builtin) && (!query || (row.card.name+' '+row.card.tagline).toLocaleLowerCase().includes(query)));
    for (const row of rows) {
      const card = row.card, button = node('button','pc-card'); button.type = 'button';
      button.setAttribute('aria-pressed',String(row.id===selectedId));
      button.setAttribute('aria-label',card.name + (row.id===library.active_id ? '，正在使用' : '，查看设定'));
      const top = node('span','pc-card-top'), avatar = node('span','pc-avatar',card.avatar || '✦');
      avatar.dataset.accent = card.accent;
      const title = node('span'); title.append(node('strong','',card.name),node('small','',row.builtin ? '内置预设' : '我的角色'));
      top.append(avatar,title); button.append(top,node('span','pc-card-desc',card.tagline || '留一点自己的小习惯，慢慢丰富这个角色'));
      if (row.id===library.active_id) button.append(node('span','pc-card-status',row.revision===library.active_revision ? '正在使用' : '有待应用的修改'));
      if (row.archived) button.append(node('span','hint','已收起 · 可恢复'));
      button.onclick = () => navigate(() => select(row.id)); box.append(button);
    }
    if (!rows.length) box.append(node('p','pc-empty',query ? '没有找到匹配的角色卡' : filter==='archived' ? '还没有收起的角色卡' : '点右上角，创建你的第一个角色'));
    document.querySelectorAll('[data-pc-filter]').forEach(b => b.setAttribute('aria-pressed',String(b.dataset.pcFilter===filter)));
  }
  function select(identity) {
    const row = library.cards.find(c => c.id===identity);
    if (!row) return;
    selectedId = identity; draft = clone(row.card); saved = JSON.stringify(draft);
    populate(); renderCards();
  }
  function populate() {
    if (!draft) return;
    for (const key of fields) field(key).value = draft[key] || '';
    text('pc-editor-title',selectedId ? '编辑角色设定' : '创建你的角色');
    text('pc-editor-caption',chosen()?.builtin ? '内置预设可直接启用，修改后保存为自己的角色卡' : '先保存，再启用 · 每张卡独立保存在服务器');
    $('pc-unsaved').hidden = true; pending = null; error();
    setTab('details'); renderButtons();
  }
  function renderButtons() {
    if (!draft) return;
    const row = chosen(), changed = dirty(), active = library?.active_id===selectedId;
    $('pc-save').disabled = busy || !!row?.archived || (!changed && !!selectedId);
    text('pc-save',row?.builtin ? '保存为我的角色' : '保存角色卡');
    $('pc-activate').disabled = busy || changed || !row || row.archived || (active && row.revision===library.active_revision);
    text('pc-activate',active && row?.revision===library.active_revision && !changed ? '当前正在使用' : '启用这个角色');
    $('pc-new').disabled = busy; $('pc-copy').disabled = busy; $('pc-refresh').disabled = busy;
    $('pc-archive').hidden = !row || row.builtin;
    $('pc-archive').disabled = busy || changed || (active && !row?.archived);
    $('pc-archive').title = active ? '先切换到其他角色，再收起这张卡' : '收起后可以从「已收起」恢复';
    text('pc-archive',row?.archived ? '恢复角色卡' : '收起');
    const count = field('mode').value==='raw' ? field('raw_prompt').value.length : fields.filter(k=>!['raw_prompt','mode','accent','avatar','tagline'].includes(k)).reduce((n,k)=>n+field(k).value.length,0);
    text('pc-save-state',changed ? '有未保存修改 · '+count.toLocaleString()+' 字' : row?.archived ? '已收起，恢复后可以编辑' : active && row?.revision!==library.active_revision ? '已保存，点击启用应用修改' : '已保存到服务器 · '+count.toLocaleString()+' 字');
    $('pc-save-state').dataset.dirty = String(!!changed);
    const raw = field('mode').value==='raw';
    $('pc-raw-fields').hidden = !raw || tab!=='details';
    $('pc-structured-fields').hidden = raw || tab!=='details';
    $('pc-relationship-fields').hidden = raw;
    $('pc-relationship-raw-note').hidden = !raw;
    $('pc-mode').disabled = busy || !!row?.archived;
    for (const key of fields.filter(k=>k!=='mode')) field(key).disabled = busy || !!row?.archived;
  }
  function navigate(fn) {
    if (busy) return;
    if (dirty()) {
      pending = fn; $('pc-unsaved').hidden = false;
      text('pc-unsaved-message','这张卡还有未保存的修改，可以先保存，或放弃修改后继续');
      $('pc-unsaved').scrollIntoView({block:'nearest',behavior:'smooth'}); return;
    }
    fn();
  }
  async function preview() {
    const sequence = ++previewSequence;
    text('pc-preview-text','正在合成角色设定…'); text('pc-preview-count','');
    try {
      const result = await api('personas/preview','POST',read());
      if (sequence!==previewSequence) return;
      text('pc-preview-text',result.prompt); text('pc-preview-count',result.length.toLocaleString()+' / 16,000 字');
    } catch (e) { if (sequence===previewSequence) text('pc-preview-text',e.message); }
  }
  function setTab(value) {
    tab = value;
    for (const name of tabs) {
      $('pc-tab-'+name).setAttribute('aria-selected',String(name===tab));
      $('pc-tab-'+name).tabIndex = name===tab ? 0 : -1;
      $('pc-panel-'+name).hidden = name!==tab;
    }
    renderButtons(); if (tab==='preview') preview();
  }
  function accept(result, identity) {
    library = result; renderActive();
    if (identity) select(identity); else renderCards();
  }
  async function save() {
    const row = chosen(), value = read();
    const result = await api('personas/save','POST',{
      id:row && !row.builtin ? row.id : '', revision:row && !row.builtin ? row.revision : 0, card:value});
    filter = 'all'; accept(result,result.saved_id);
    notice('角色卡已保存，点击「启用这个角色」后生效');
  }
  async function refresh() {
    error();
    try {
      const result = await api('personas');
      accept(result, result.cards.some(c=>c.id===selectedId && !c.archived) ? selectedId : result.active_id);
    } catch (e) { error(e.message); }
  }
  $('pc-new').onclick = () => navigate(() => {
    selectedId = ''; draft = Object.fromEntries(fields.map(k=>[k,'']));
    Object.assign(draft,{name:'新角色',avatar:'✦',accent:'sea',mode:'structured',
      group_style:'公开场合像朋友一样自然接话，尊重他人边界，不披露私聊内容。'});
    saved = ''; populate(); renderCards(); field('name').focus();
  });
  $('pc-copy').onclick = () => {
    if (!draft || busy) return;
    draft = read(); draft.name = (draft.name.slice(0,34) || '角色')+' · 副本';
    selectedId = ''; saved = ''; populate(); renderCards(); field('name').focus();
  };
  $('pc-save').onclick = () => run(save);
  $('pc-activate').onclick = () => run(async () => {
    const row = chosen();
    if (!row || dirty()) throw new Error('先保存这张角色卡，再启用');
    const result = await api('personas/'+encodeURIComponent(row.id)+'/actions','POST',{op:'activate',revision:row.revision});
    cfg = await api('config'); accept(result,row.id);
    notice('已切换为「'+row.card.name+'」，下一次回复生效，无需重新登录微信');
  });
  $('pc-archive').onclick = () => run(async () => {
    const row = chosen(), op = row.archived ? 'restore' : 'archive';
    const result = await api('personas/'+encodeURIComponent(row.id)+'/actions','POST',{op,revision:row.revision});
    filter = op==='restore' ? 'custom' : 'archived'; accept(result,row.id);
    notice(op==='restore' ? '角色卡已恢复' : '角色卡已收起，随时可以恢复');
  });
  $('pc-refresh').onclick = () => navigate(() => run(refresh));
  $('pc-error-retry').onclick = () => run(refresh);
  $('pc-unsaved-stay').onclick = () => { pending = null; $('pc-unsaved').hidden = true; };
  $('pc-unsaved-discard').onclick = () => { const next = pending; pending = null; saved = JSON.stringify(read()); $('pc-unsaved').hidden = true; if (next) next(); };
  $('pc-unsaved-save').onclick = () => run(async () => { const next = pending; await save(); if (next) next(); });
  $('pc-search').oninput = renderCards;
  document.querySelectorAll('[data-pc-filter]').forEach(b => b.onclick = () => { filter = b.dataset.pcFilter; renderCards(); });
  for (const name of tabs) $('pc-tab-'+name).onclick = () => setTab(name);
  $('pc-tabs').onkeydown = event => {
    if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    const index = tabs.indexOf(tab), next = event.key==='Home' ? 0 : event.key==='End' ? 2 : (index+(event.key==='ArrowRight'?1:2))%3;
    event.preventDefault(); setTab(tabs[next]); $('pc-tab-'+tabs[next]).focus();
  };
  for (const key of fields) field(key).addEventListener('input',renderButtons);
  field('mode').addEventListener('change',() => {
    if (field('mode').value==='raw' && !field('raw_prompt').value.trim()) {
      run(async () => {
        const value = read(); value.mode = 'structured';
        const result = await api('personas/preview','POST',value);
        field('raw_prompt').value = result.prompt;
      });
    }
    setTab('details'); renderButtons();
  });
  window.addEventListener('beforeunload',event => { if (dirty()) { event.preventDefault(); event.returnValue = ''; } });
  window.personas = {refresh};
  // A cold mobile connection can finish login before this deferred asset loads.
  if (cfg) refresh();
})();
