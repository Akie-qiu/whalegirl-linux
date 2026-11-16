/* Harbor: task-led workspaces; original controls and API adapters remain intact. */
'use strict';
(() => {
  const get = id => document.getElementById(id);
  const make = (tag, cls = '', value = '') => {
    const node = document.createElement(tag);
    node.className = cls;
    node.textContent = value;
    return node;
  };
  const glyphs = {
    home: '<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z"/>',
    chat: '<path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-3 2 1.7-5.1A8.5 8.5 0 1 1 21 11.5Z"/><path d="M7 9h9M7 13h6"/>',
    heart: '<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z"/>',
    explore: '<circle cx="12" cy="12" r="9"/><path d="m16 8-2.5 5.5L8 16l2.5-5.5Z"/>',
    palette: '<path d="M12 3a9 9 0 1 0 0 18h1a2 2 0 0 0 0-4 1.5 1.5 0 0 1 0-3h3a5 5 0 0 0 0-10Z"/><path d="M7 10h.01M10 6h.01M15 7h.01"/>',
    settings: '<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="9" cy="6" r="2" fill="currentColor"/><circle cx="15" cy="12" r="2" fill="currentColor"/><circle cx="8" cy="18" r="2" fill="currentColor"/>',
    menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
    close: '<path d="m6 6 12 12M18 6 6 18"/>',
    chevron: '<path d="m9 5 7 7-7 7"/>',
    model: '<rect x="6" y="6" width="12" height="12" rx="3"/><path d="M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4M18 9h4m-4 6h4"/>',
    search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
    arrow: '<path d="M5 12h14m-6-6 6 6-6 6"/>',
    whale: '<path d="M3 13c1 0 2 1 2 3 0 3 3 5 7 5 5 0 9-3 9-7 0-3-2-5-5-5-4 0-5 4-9 4H3Z"/><path d="M6 13C4 7 1 8 2 5c4 0 6 4 4 8ZM14 5V2m-3 3-1-2m7 2 1-2"/><circle cx="17" cy="13" r=".8" fill="currentColor"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>'
  };
  function icon(name) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    node.setAttribute('viewBox', '0 0 24 24');
    node.setAttribute('fill', 'none');
    node.setAttribute('stroke', 'currentColor');
    node.setAttribute('stroke-width', '1.65');
    node.setAttribute('stroke-linecap', 'round');
    node.setAttribute('stroke-linejoin', 'round');
    node.setAttribute('aria-hidden', 'true');
    node.innerHTML = glyphs[name] || glyphs.chat;
    return node;
  }
  // Each route has one owner; aliases retain bookmarks from older consoles.
  const catalogue = [
    ['overview', '运行概览', '微信连接、自动回复和最近处理情况。', 'home'],
    ['chats', '会话与唤醒', '选择一起聊天的人，以及她在群里如何参与。', 'chat'],
    ['expression-panel', '聊天表达', '语气、短句与情绪，调成你们相处时的样子。', 'chat'],
    ['proactive-panel', '主动聊天与表情', '决定主动联系的范围，以及文字和表情的搭配。', 'chat'],
    ['habits-panel', '消息与上下文', '聊天兴趣、等待补充与话题整理。上下文条数和发送缓冲在“发送与保护”中调整。', 'chat'],
    ['persona', '角色设定', '保留她是谁，也留出慢慢丰富自己的空间。', 'heart'],
    ['memory-panel', '聊天记忆', '按人物、来源和有效范围查找真实的共同经历。', 'heart'],
    ['affection-panel', '关系与好感', '管理相处关系和人物好感，查看变化的依据。', 'heart'],
    ['growth-console', '兴趣与成长', '浏览、理解、反思，再让有依据的经历留下来。', 'explore'],
    ['bilibili-console', 'B站探索', '账号、观看方式、探索记录与真实理解范围。', 'explore'],
    ['life-settings', '生活自治', '查看可调整的生活参数、采用证据和回滚记录。', 'explore'],
    ['sticker-library-panel', '表情工作室', '收藏、整理和授权，让表情库保持丰富好用。', 'palette'],
    ['sticker-profile-panel', '表情含义', '查看表情的意思、适用情境和识别结果。', 'palette'],
    ['images-panel', '图片与生图', '角色参考、生成参数、图片库和每次任务的结果。', 'palette'],
    ['moments-panel', '朋友圈', '照看动态、评论、配图与发布记录。', 'palette'],
    ['audio-console', '语音与唱歌', '调节声线、试听与音频工具，保留独立接口设置。', 'palette'],
    ['model-panel', '模型与接口', '管理模型、拉取可用列表，独立保存接口与价格。', 'settings'],
    ['web-search-panel', '聊天联网', '只在需要新信息时，允许她查询和核对事实。', 'settings'],
    ['send-limits-panel', '发送与保护', '上下文条数、短句、缓冲、发送额度和账号保护集中调整。', 'settings'],
    ['login-panel', '微信连接', '查看连接状态；需要时在这里扫码登录。', 'settings'],
    ['system-health-panel', '异常与恢复', '核对异常、确认回执，再按真实状态恢复运行。', 'settings'],
    ['token-usage-panel', 'Token 与费用', '真实用量、官方价参考和接口金额分别展示。', 'settings'],
    ['speech-usage-panel', '发言看板', '收录的真人消息、确认送达的发言和回复轮次。', 'chat'],
    ['records-panel', '处理记录', '查看每条消息的处理过程和实际发送结果。', 'settings'],
    ['inbox-panel', '收件记录', '按会话查阅已收取的消息和历史补录。', 'settings'],
    ['stability-panel', '运行日志', '连接、内存和运行事件，按北京时间查看。', 'settings']
  ];

  const groups = [
    ['now', '运行概览', ['overview', 'token-usage-panel', 'speech-usage-panel', 'records-panel', 'inbox-panel']],
    ['chat', '聊天配置', ['chats', 'expression-panel', 'proactive-panel', 'habits-panel']],
    ['memory', '角色与记忆', ['persona', 'memory-panel', 'affection-panel']],
    ['models', '模型服务', ['model-panel', 'web-search-panel', 'audio-console']],
    ['explore', '自主探索', ['bilibili-console', 'growth-console', 'life-settings']],
    ['create', '素材与创作', ['sticker-library-panel', 'sticker-profile-panel', 'images-panel', 'moments-panel']],
    ['system', '系统管理', ['login-panel', 'send-limits-panel', 'system-health-panel', 'stability-panel']]
  ];
  const aliases = { 'timing-panel': 'send-limits-panel', 'safety-panel': 'send-limits-panel',
    'category-limits-panel': 'send-limits-panel', 'learning-panel': 'memory-panel' };
  const saves = {
    chats: ['save-chats'], 'expression-panel': ['save-style-settings'],
    'proactive-panel': ['save-proactive'], 'habits-panel': ['save-context-strategy', 'save-habits'],
    'memory-panel': ['hub-save-settings'], 'affection-panel': ['save-affection-rules'],
    'growth-console': ['growth-save'], 'bilibili-console': ['bili-save'], 'life-settings': ['studio-save-life'],
    'model-panel': ['save-model'], 'web-search-panel': ['save-web-search'], 'send-limits-panel': ['save-limits'],
    'sticker-library-panel': [], 'images-panel': [], 'moments-panel': []
  };

  function init() {
    const dashboard = get('dashboard');
    if (!dashboard || dashboard.dataset.studioReady) return;
    dashboard.dataset.studioReady = 'harbor';
    document.body.classList.add('tabbed-workspace', 'studio-console', 'harbor-console');
    const main = dashboard.querySelector('main'), grid = main.querySelector('.content-grid');
    const sidebar = dashboard.querySelector('.sidebar');
    const pages = new Map(), metadata = new Map(catalogue.map(row => [row[0], row]));
    const menuPositions = new WeakMap();
    function actionMenu(label, buttons = [], extraClass = '') {
      const root = make('details', 'harbor-action-menu ' + extraClass);
      const summary = make('summary', '', label), body = make('div', 'harbor-menu-body');
      root.append(summary, body);
      for (const button of buttons.filter(Boolean)) body.append(button);
      function position() {
        if (!root.open) return;
        const anchor = summary.getBoundingClientRect(), viewportWidth = document.documentElement.clientWidth;
        const viewportHeight = document.documentElement.clientHeight, width = Math.min(240, viewportWidth - 24);
        body.style.width = width + 'px';
        body.style.left = Math.max(12, Math.min(anchor.left, viewportWidth - width - 12)) + 'px';
        const height = body.getBoundingClientRect().height;
        body.style.top = (anchor.bottom + height + 8 < viewportHeight - 12 ? anchor.bottom + 6 :
          Math.max(12, anchor.top - height - 6)) + 'px';
      }
      root.addEventListener('toggle', () => {
        if (root.open) {
          for (const other of dashboard.querySelectorAll('.harbor-action-menu[open]')) if (other !== root) other.open = false;
          position();
        }
      });
      body.addEventListener('click', event => { if (event.target.closest('button,a')) root.open = false; });
      menuPositions.set(root, position);
      return {root, body};
    }
    function closeActionMenus() {
      for (const menu of dashboard.querySelectorAll('.harbor-action-menu[open]')) menu.open = false;
    }
    window.addEventListener('resize', () => {
      for (const menu of dashboard.querySelectorAll('.harbor-action-menu[open]')) menuPositions.get(menu)?.();
    });
    document.addEventListener('click', event => {
      for (const menu of dashboard.querySelectorAll('.harbor-action-menu[open]'))
        if (!menu.contains(event.target)) menu.open = false;
    });
    document.addEventListener('keydown', event => { if (event.key === 'Escape') closeActionMenus(); });
    function standalone(id) {
      const original = get(id);
      if (!original) return;
      if (original.tagName === 'DETAILS') {
        const wrapper = make('section', 'panel span-all studio-managed');
        wrapper.id = 'studio-page-' + id;
        original.classList.add('studio-root-details');
        wrapper.append(original); grid.append(wrapper); return wrapper;
      }
      original.classList.add('studio-managed'); grid.append(original); return original;
    }
    for (const id of ['bilibili-console', 'life-settings', 'growth-console']) pages.set(id, standalone(id));
    const biliToggle = get('bili-enabled')?.closest('label');
    if (biliToggle) {
      const biliSlot = get('bili-master-slot');
      if (biliSlot) biliSlot.replaceChildren(biliToggle);
      else get('bilibili-console').querySelector(':scope > summary')?.after(biliToggle);
    }
    const contextSave = [...get('habits-panel').querySelectorAll('button')]
      .find(button => button.textContent.trim() === '保存上下文策略');
    if (contextSave) contextSave.id = 'save-context-strategy';
    if (get('life-enabled')) {
      const life = get('life-settings'), toggle = get('life-enabled').closest('label');
      life.querySelector(':scope > summary').after(toggle);
      const save = make('button', 'primary', '保存生活自治开关');
      save.id = 'studio-save-life'; save.type = 'button';
      save.onclick = () => action(save, async () => {
        await saveSubset({life_autonomy_enabled: get('life-enabled').checked});
        window.dispatchEvent(new CustomEvent('whalegirl-panel-saved', {detail: 'life-settings'}));
        notice('生活自治开关已保存');
      });
      toggle.after(save);
    }
    const health = make('section', 'panel span-all studio-managed'); health.id = 'system-health-panel';
    if (get('recovery-center')) health.append(get('recovery-center'));
    grid.append(health);
    for (const [id] of catalogue) if (id !== 'overview' && !pages.has(id)) {
      const panel = standalone(id); if (panel) pages.set(id, panel);
    }
    for (const panel of grid.querySelectorAll(':scope > .panel')) panel.hidden = true;

    // Persistent tool navigation: one active section, all original page owners.
    document.body.classList.add('bot-console');
    const sideNav = make('aside', 'bot-sidebar'); sideNav.id = 'studio-sidebar';
    sideNav.setAttribute('aria-label', '控制台导航');
    const sideBrand = make('div', 'bot-brand-row');
    const brand = sidebar.querySelector('.brand'); brand.className = 'bot-brand';
    brand.querySelector('.brand-mark').replaceChildren(icon('whale'));
    brand.querySelector('small').textContent = 'DeepSeek 酱';
    const drawerClose = make('button', 'bot-drawer-close'); drawerClose.type = 'button';
    drawerClose.append(icon('close')); drawerClose.setAttribute('aria-label', '收起导航');
    sideBrand.append(brand, drawerClose);
    const workspaces = make('nav', 'bot-navigation'); workspaces.setAttribute('aria-label', '主要工作区');
    const groupIcons = {now:'home',chat:'chat',memory:'heart',models:'model',explore:'explore',create:'palette',system:'settings'};
    for (const [key, title, ids] of groups) {
      const link = make('a', 'bot-group-link'); link.href = '#' + ids[0]; link.dataset.workspace = key;
      link.append(icon(groupIcons[key]), make('span', '', title), icon('chevron'));
      workspaces.append(link);
    }
    const header = make('header', 'bot-header');
    const menuToggle = make('button', 'bot-menu-toggle'); menuToggle.type = 'button';
    menuToggle.append(icon('menu'), make('span', '', '菜单')); menuToggle.setAttribute('aria-label', '打开导航');
    menuToggle.setAttribute('aria-controls', sideNav.id); menuToggle.setAttribute('aria-expanded', 'false');
    const breadcrumb = make('div', 'bot-breadcrumb'); breadcrumb.setAttribute('aria-label', '当前位置');
    const crumbGroup = make('span'), crumbTitle = make('strong');
    breadcrumb.append(crumbGroup, icon('chevron'), crumbTitle);
    const controls = make('div', 'bot-utilities');
    const searchButton = make('button', 'harbor-icon-button'); searchButton.type = 'button';
    searchButton.append(icon('search'), make('span', '', '搜索')); searchButton.setAttribute('aria-label', '查找功能');
    searchButton.title = '搜索功能与设置（Ctrl K）';
    const themeButton = make('button', 'harbor-icon-button'); themeButton.type = 'button';
    themeButton.append(icon('sun'), make('span', '', '外观设置')); themeButton.setAttribute('aria-label', '外观'); themeButton.setAttribute('aria-haspopup', 'dialog');
    const draftSwitch = make('button', 'studio-draft-switch'); draftSwitch.type = 'button'; draftSwitch.hidden = true;
    const disconnectButton = get('disconnect'); disconnectButton.textContent = '退出控制台';
    const moreMenu = actionMenu('更多', [themeButton, get('refresh-state'), disconnectButton], 'harbor-global-more');
    controls.append(draftSwitch, searchButton, moreMenu.root);
    header.append(menuToggle, breadcrumb, controls);
    const nav = make('nav', 'bot-subnav');
    nav.id = 'console-nav'; nav.setAttribute('aria-label', '当前工作区');
    for (const [id, title] of catalogue) if (id === 'overview' || pages.has(id)) {
      const link = make('a', 'studio-nav-link', title);
      link.href = '#' + id; link.dataset.page = id; nav.append(link);
    }
    const status = make('div', 'bot-runtime compact-status-bar'); status.id = 'compact-status-bar';
    for (const [id, label] of [['compact-conn-badge', '读取连接'], ['compact-reply-badge', '读取回复状态'],
      ['compact-mood-badge', ''], ['compact-timing-badge', '']]) {
      const badge = make('span', 'ui-badge', label); badge.id = id; status.append(badge);
    }
    const recoveryLink = make('a', 'harbor-health-link', '状态'); recoveryLink.href = '#system-health-panel';
    status.append(recoveryLink);
    sideNav.append(sideBrand, make('p', 'bot-navigation-label', '工作台'), workspaces, status);
    const backdrop = make('button', 'bot-sidebar-backdrop'); backdrop.type = 'button'; backdrop.hidden = true;
    backdrop.tabIndex = -1; backdrop.setAttribute('aria-label', '关闭导航');
    dashboard.prepend(sideNav, backdrop, header); sidebar.hidden = true;
    const sidebarMedia = window.matchMedia('(max-width: 900px)');
    function setMenu(open, focus = true) {
      open = !!open && sidebarMedia.matches;
      document.body.classList.toggle('bot-menu-open', open);
      menuToggle.setAttribute('aria-expanded', String(open));
      sideNav.inert = sidebarMedia.matches && !open; main.inert = open; backdrop.hidden = !open;
      if (focus && open) sideNav.querySelector('[aria-current="page"],a,button')?.focus();
      else if (focus && sidebarMedia.matches) menuToggle.focus();
    }
    function closeMenu() { setMenu(false, document.body.classList.contains('bot-menu-open')); }
    menuToggle.onclick = () => setMenu(!document.body.classList.contains('bot-menu-open'));
    drawerClose.onclick = backdrop.onclick = closeMenu;
    sidebarMedia.addEventListener('change', () => setMenu(false, false)); setMenu(false, false);
    document.addEventListener('keydown', event => {
      if (!document.body.classList.contains('bot-menu-open')) return;
      if (event.key === 'Escape') { event.preventDefault(); closeMenu(); return; }
      if (event.key !== 'Tab') return;
      const items = [...sideNav.querySelectorAll('a,button')].filter(node => node.getClientRects().length && !node.disabled);
      const first = items[0], last = items[items.length-1];
      if (event.shiftKey && (document.activeElement === first || !sideNav.contains(document.activeElement))) {
        event.preventDefault(); last?.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !sideNav.contains(document.activeElement))) {
        event.preventDefault(); first?.focus();
      }
    });
    const syncLine = make('div', 'harbor-sync-line'); syncLine.append(get('sync-status'), get('wechat-version'));
    main.querySelector('footer').append(syncLine);

    // An operational overview: actual state and records, without an artwork hero.
    const hero = main.querySelector('.page-header'); hero.className = 'harbor-home-heading'; hero.replaceChildren();
    const homeIdentity = make('div', 'harbor-home-identity'), homeTitle = make('h1', '', '运行概览');
    const homeStatus = make('span', 'harbor-presence', '正在读取'); homeStatus.setAttribute('role', 'status');
    const homeSummary = make('p', 'muted', '正在读取微信连接与回复状态。');
    const nextAction = make('a', 'harbor-next-action', '管理聊天会话'); nextAction.href = '#chats';
    const homeDate = make('div', 'bot-home-date');
    homeDate.append(make('time', '', new Intl.DateTimeFormat('zh-CN', {
      month:'long',day:'numeric',weekday:'long',timeZone:'Asia/Shanghai'}).format(new Date())));
    const homeActions = make('div', 'bot-home-actions'); homeActions.append(homeStatus, homeDate, nextAction);
    homeIdentity.append(homeTitle, homeSummary); hero.append(homeIdentity, homeActions);
    const syncCharacter = () => {
      const activeName = get('pc-active-name')?.textContent.trim();
      brand.querySelector('small').textContent = activeName && activeName !== '鲸鱼娘' ? activeName : 'DeepSeek 酱';
    };
    if (get('pc-active-name')) new MutationObserver(syncCharacter).observe(get('pc-active-name'), {childList:true,subtree:true,characterData:true});
    syncCharacter();
    const homeWorkspace = make('div', 'harbor-home-layout');
    const live = make('div', 'harbor-live-feed'), homeAside = make('aside', 'harbor-home-aside');
    const activity = get('records-panel'); activity.querySelector('h2').textContent = '最近对话';
    const allRecords = make('a', 'harbor-subtle-link', '全部处理记录'); allRecords.href = '#records-panel';
    activity.querySelector('.section-top').append(allRecords); live.append(activity);
    const overview = get('overview'); overview.className = 'harbor-facts bot-stat-strip';
    overview.setAttribute('aria-label', '运行状态');
    const statTargets = ['login-panel', 'chats', 'records-panel', 'memory-panel'];
    for (const [index, stat] of [...overview.children].entries()) {
      const link = make('a', 'harbor-fact'); link.href = '#' + statTargets[index];
      if (index === 3) stat.querySelector('span').textContent = '已存聊天记录';
      link.append(...stat.childNodes); stat.append(link);
    }
    const quick = get('quick-controls-panel'); quick.querySelector('h2').textContent = '自动回复';
    for (const row of quick.querySelectorAll('.setting-row')) if (row.querySelector('a')) row.hidden = true;
    const hints = quick.querySelectorAll(':scope > p.hint');
    if (hints.length) hints[hints.length-1].textContent = '总开关即时生效，其他设置保存后生效。';
    homeAside.append(quick);
    const shortcuts = make('div', 'harbor-shortcuts'); shortcuts.append(make('h2', '', '常用调节'));
    for (const [id, label, hint] of [['expression-panel','调节聊天语气','短句、亲近与玩笑'],
      ['bilibili-console','看看她在探索什么','视频与学习记录'],['model-panel','管理模型接口','独立配置与温度']]) {
      const a = make('a'); a.href = '#' + id;
      a.append(make('strong','',label),make('span','',hint),icon('arrow')); shortcuts.append(a);
    }
    homeAside.append(shortcuts); homeWorkspace.append(overview, live, homeAside); grid.append(homeWorkspace);
    const pageHeading = make('div', 'harbor-page-heading');
    const title = make('h1'), description = make('p', 'muted'); pageHeading.append(title, description); hero.after(pageHeading);
    const pageBody = make('div', 'harbor-page-body'); pageBody.append(grid); pageHeading.after(pageBody);
    // Authentication is the same original form and endpoint, with fresh presentation.
    const auth = get('auth'); auth.querySelector('h1').textContent = '鲸鱼娘';
    auth.querySelector('.eyebrow').textContent = 'DeepSeek 酱的生活工作室';
    auth.querySelector('.security-line').textContent = '管理密码仅用于连接控制台';
    auth.querySelector('.auth-intro>p:not(.eyebrow)').textContent = '照看她的日常，也留一点自由生长的空间。';
    const searchDialog = make('dialog', 'studio-search-dialog');
    searchDialog.id = 'studio-search-dialog';
    searchDialog.setAttribute('aria-labelledby', 'studio-search-title');
    const searchTitle = make('h2', '', '查找功能'); searchTitle.id = 'studio-search-title';
    const searchInput = make('input'); searchInput.type = 'search'; searchInput.placeholder = '搜索功能，例如：声线、B站、短句、API';
    searchInput.setAttribute('aria-label', '搜索功能');
    const results = make('div', 'studio-search-results');
    const closeSearch = make('button', 'secondary', '关闭'); closeSearch.type = 'button'; closeSearch.onclick = () => searchDialog.close();
    searchDialog.append(searchTitle, searchInput, results, closeSearch); dashboard.append(searchDialog);
    const synonyms = {'audio-console': '声音 声线 音色 音频 音乐 唱歌', 'model-panel': 'api key 密钥 温度 token 接口',
      'expression-panel': '语气 短句 句子 少女 拟人 文风 fishtalk', 'life-settings': '生活 自治 参数 作息',
      'bilibili-console': 'b站 哔哩哔哩 视频 登录 字幕 评论 弹幕', 'growth-console': '学习 成长 兴趣 表达 反思',
      'sticker-library-panel': '表情 收藏 入库', 'proactive-panel': '主动 聊天 表情 积极性'};
    let searchDraftsOnly = false;
    function controlSearchResults(query) {
      const matches = [];
      if (!query || searchDraftsOnly) return matches;
      for (const [id, panel] of pages) {
        for (const control of panel.querySelectorAll('input[id],select[id],textarea[id]')) {
          if (control.type === 'hidden' || control.type === 'search' || control.closest('.chat-row,dialog')) continue;
          const label = control.labels?.[0];
          if (!label) continue;
          let concealed = false;
          for (let parent = control; parent && parent !== panel; parent = parent.parentElement)
            if (parent.hidden || parent.style.display === 'none') { concealed = true; break; }
          if (concealed) continue;
          const copy = label.querySelector('[data-control-label]');
          const name = (copy ? copy.textContent : [...label.childNodes].filter(node =>
            node !== control && !node.matches?.('input,select,textarea')).map(node => node.textContent).join(''))
            .replace(/\s+/g, ' ').trim();
          if (!name || !name.toLowerCase().includes(query)) continue;
          matches.push({id, control, name}); if (matches.length === 12) return matches;
        }
      }
      return matches;
    }
    function renderSearch() {
      const query = searchInput.value.trim().toLowerCase();
      results.replaceChildren();
      const fields = controlSearchResults(query);
      if (fields.length) results.append(make('p', 'studio-search-group', '具体设置'));
      for (const {id, control, name} of fields) {
        const row = metadata.get(id), link = make('a', 'studio-search-result');
        link.href = '#' + id; link.dataset.control = control.id;
        const copy = make('span');
        copy.append(make('strong', '', name.length > 58 ? name.slice(0, 58) + '…' : name), make('small', '', row[1]));
        link.append(icon(row[3]), copy);
        link.onclick = event => {
          event.preventDefault(); searchDialog.close();
          if (location.hash !== link.hash) history.pushState(null, '', link.hash);
          activate(link.hash);
          for (let parent = control.parentElement; parent && parent !== pages.get(id); parent = parent.parentElement)
            if (parent.tagName === 'DETAILS') parent.open = true;
          const target = control.closest('.studio-field-row,label') || control;
          target.scrollIntoView({block: 'center', behavior: 'instant'});
          if (!control.disabled) control.focus({preventScroll: true});
        };
        results.append(link);
      }
      const matching = catalogue.filter(row => (row[1] + row[2] + (synonyms[row[0]] || '')).toLowerCase().includes(query));
      if (fields.length && matching.length) results.append(make('p', 'studio-search-group', '功能页面'));
      for (const row of matching) {
        if (row[0] !== 'overview' && !pages.has(row[0])) continue;
        if (searchDraftsOnly && !dirty.get(row[0])?.size) continue;
        const link = make('a', 'studio-search-result'); link.href = '#' + row[0];
        const copy = make('span'); copy.append(make('strong', '', row[1]), make('small', '', row[2]));
        link.append(icon(row[3]), copy); link.onclick = () => searchDialog.close(); results.append(link);
      }
      if (!results.childElementCount) results.append(make('p', 'hint', searchDraftsOnly ? '没有待保存的设置。' : '没有匹配的功能，试试“表情”“记忆”或“模型”。'));
    }
    searchInput.oninput = renderSearch;
    searchInput.onkeydown = event => {
      if (event.key === 'Enter') { const first = results.querySelector('a'); if (first) first.click(); }
    };
    searchButton.onclick = () => {
      searchDraftsOnly = false; searchTitle.textContent = '查找功能'; searchInput.value = '';
      renderSearch(); searchDialog.showModal(); searchInput.focus();
    };
    draftSwitch.onclick = () => {
      searchDraftsOnly = true; searchTitle.textContent = '未保存的设置'; searchInput.value = '';
      renderSearch(); searchDialog.showModal(); searchInput.focus();
    };

    const themeDialog = make('dialog', 'studio-theme-dialog');
    themeDialog.setAttribute('aria-labelledby', 'studio-theme-title');
    const themeTitle = make('h2', '', '外观'); themeTitle.id = 'studio-theme-title';
    const palettePreview = make('div', 'bot-palette-preview');
    palettePreview.append(make('span', '', '蓝粉莫兰迪'));
    for (const color of ['#1043ab','#aacbef','#eddfdd','#cc8984']) {
      const swatch = make('span', 'bot-palette-swatch'); swatch.setAttribute('aria-hidden', 'true');
      swatch.style.setProperty('--swatch-color', color); palettePreview.append(swatch);
    }
    const backgroundLabel = make('label', 'check-label'), background = make('input');
    background.id = 'studio-background-enabled'; background.type = 'checkbox';
    backgroundLabel.append(background, document.createTextNode('显示图片背景'));
    const sourceLabel = make('label', '', '背景来源'), imageSource = make('select');
    imageSource.id = 'studio-background-source';
    for (const [value, label] of [['default','默认插画'],['custom','自选图片']]) {
      const option = make('option', '', label); option.value = value; imageSource.append(option);
    }
    const customOption = imageSource.querySelector('[value="custom"]'); customOption.disabled = true;
    sourceLabel.append(imageSource);
    const uploadLabel = make('label', '', '选择自己的图片'), backgroundFile = make('input');
    backgroundFile.id = 'studio-background-file'; backgroundFile.type = 'file';
    backgroundFile.accept = 'image/jpeg,image/png,image/webp,image/gif';
    backgroundFile.setAttribute('aria-label', '自定义背景图片'); uploadLabel.append(backgroundFile);
    const imageNote = make('p', 'hint', 'JPG、PNG、WebP、GIF，最大10MB。仅保存在当前浏览器，不上传服务器。');
    const imageStatus = make('p', 'hint studio-background-status', '正在读取本机背景…');
    imageStatus.id = 'studio-background-status'; imageStatus.setAttribute('role','status'); imageStatus.setAttribute('aria-live','polite');
    const imagePreview = make('figure', 'studio-background-preview'), previewImage = make('img'), previewName = make('figcaption');
    previewImage.alt = '当前背景预览'; imagePreview.append(previewImage, previewName);
    const opacityLabel = make('label', '', '背景浓度'), opacity = make('input');
    opacity.id = 'studio-background-opacity'; opacity.type = 'range'; opacity.min = 0; opacity.max = 40; opacity.step = 1;
    const opacityValue = make('output', 'studio-range-value');
    opacityValue.htmlFor = opacity.id; opacityLabel.append(opacityValue, opacity);
    const compactLabel = make('label', 'check-label'), compact = make('input');
    compact.type = 'checkbox'; compact.id = 'studio-density-compact';
    compactLabel.append(compact, document.createTextNode('紧凑布局，显示更多设置'));
    const backgroundDetails = make('details', 'studio-background-details');
    backgroundDetails.append(make('summary', '', '显示细节'));
    const scopeLabel = make('label', '', '显示范围'), imageScope = make('select'); imageScope.id = 'studio-background-scope';
    for (const [value, label] of [['all','整个控制台（所有页面）'],['home','仅运行概览与登录页']]) {
      const option = make('option', '', label); option.value = value; imageScope.append(option);
    }
    const fitLabel = make('label', '', '铺图方式'), imageFit = make('select'); imageFit.id = 'studio-background-fit';
    for (const [value, label] of [['cover','铺满页面（等比裁切）'],['contain','完整显示（可能留白）']]) {
      const option = make('option', '', label); option.value = value; imageFit.append(option);
    }
    const positionLabel = make('label', '', '主体位置'), imagePosition = make('select');
    imagePosition.id = 'studio-background-position';
    for (const [value, label] of [['center','居中'],['left','偏左'],['right','偏右'],['top','偏上']]) {
      const option = make('option', '', label); option.value = value; imagePosition.append(option);
    }
    positionLabel.append(imagePosition);
    scopeLabel.append(imageScope); fitLabel.append(imageFit); backgroundDetails.append(scopeLabel, fitLabel, positionLabel,
      make('p', 'hint', '铺图方式控制图片如何缩放；要在各个设置页显示背景，请将显示范围设为“整个控制台”。'));
    const themeClose = make('button', 'primary', '完成'); themeClose.type = 'button'; themeClose.onclick = () => themeDialog.close();
    themeDialog.append(themeTitle, make('p', 'muted', '外观仅保存在当前浏览器。'), palettePreview,
      backgroundLabel, sourceLabel, uploadLabel, imageNote, imageStatus, imagePreview,
      opacityLabel, backgroundDetails, compactLabel, themeClose); dashboard.append(themeDialog);
    let appearance = {enabled: true, opacity: 12, compact: false, imageSource:'default', imageScope:'home', imageFit:'cover', imagePosition:'center'};
    try { appearance = {...appearance, ...JSON.parse(localStorage.getItem('whalegirl-harbor-appearance') || '{}')}; } catch {}
    let customImageURL = '', customImageName = '', backgroundDatabase = null;
    background.checked = appearance.enabled !== false;
    opacity.value = Math.max(0, Math.min(40, Number(appearance.opacity) || 0));
    compact.checked = !!appearance.compact;
    imageSource.value = appearance.imageSource === 'custom' ? 'custom' : 'default';
    imageScope.value = appearance.imageScope === 'all' ? 'all' : 'home';
    imageFit.value = appearance.imageFit === 'contain' ? 'contain' : 'cover';
    imagePosition.value = ['center','left','right','top'].includes(appearance.imagePosition) ? appearance.imagePosition : 'center';
    backgroundFile.disabled = imageSource.disabled = true;
    function backgroundStatus(message, error = false) {
      imageStatus.textContent = message; imageStatus.classList.toggle('error-text', error);
      imageStatus.setAttribute('role', error ? 'alert' : 'status');
    }
    function applyAppearance(persist = true) {
      const custom = imageSource.value === 'custom' && !!customImageURL;
      document.body.style.setProperty('--studio-background-opacity', background.checked ? Number(opacity.value) / 100 : 0);
      document.body.style.setProperty('--studio-background-image', custom ? 'url("' + customImageURL + '")' : 'url("/studio-background.jpg")');
      document.body.style.setProperty('--studio-background-fit', imageFit.value);
      document.body.style.setProperty('--studio-background-position',
        {center:'50% 50%',left:'25% 50%',right:'75% 50%',top:'50% 20%'}[imagePosition.value] || '50% 50%');
      document.body.dataset.wallpaperScope = imageScope.value;
      document.body.dataset.wallpaperFit = imageFit.value;
      document.body.classList.add('studio-wallpaper-layer');
      document.body.classList.toggle('studio-background-off', !background.checked);
      document.body.classList.toggle('studio-compact', compact.checked);
      opacity.disabled = !background.checked;
      imagePosition.disabled = imageFit.value === 'contain';
      opacityValue.textContent = background.checked ? opacity.value + '%' : '已关闭';
      previewImage.src = custom ? customImageURL : '/studio-background.jpg'; previewImage.style.objectFit = imageFit.value;
      previewImage.style.objectPosition = document.body.style.getPropertyValue('--studio-background-position');
      previewName.textContent = custom ? customImageName : '默认插画';
      appearance = {...appearance, enabled: background.checked, opacity: Number(opacity.value), compact: compact.checked,
        imageSource:custom ? 'custom' : 'default', imageScope:imageScope.value, imageFit:imageFit.value, imagePosition:imagePosition.value};
      if (persist === false) return true;
      try { localStorage.setItem('whalegirl-harbor-appearance', JSON.stringify(appearance)); return true; }
      catch { return false; }
    }
    function openBackgroundDatabase() {
      if (backgroundDatabase) return backgroundDatabase;
      const pending = new Promise((resolve, reject) => {
        if (!window.indexedDB) { reject(new Error('浏览器未允许保存图片，请检查站点存储权限。')); return; }
        const request = indexedDB.open('whalegirl-studio-background', 1); let settled = false;
        const fail = () => { if (!settled) { settled = true; reject(new Error('浏览器未能保存图片，请检查站点存储权限或稍后重试。')); } };
        request.onupgradeneeded = () => {
          if (!request.result.objectStoreNames.contains('images')) request.result.createObjectStore('images');
        };
        request.onerror = request.onblocked = fail;
        request.onsuccess = () => {
          if (settled) { request.result.close(); return; }
          settled = true; const db = request.result;
          db.onversionchange = () => { db.close(); backgroundDatabase = null; };
          resolve(db);
        };
      });
      backgroundDatabase = pending;
      pending.catch(() => { if (backgroundDatabase === pending) backgroundDatabase = null; });
      return pending;
    }
    async function backgroundRecord(mode, record) {
      const db = await openBackgroundDatabase();
      return new Promise((resolve, reject) => {
        const transaction = db.transaction('images', mode), store = transaction.objectStore('images');
        const request = mode === 'readonly' ? store.get('custom') : store.put(record, 'custom');
        transaction.oncomplete = () => resolve(request.result);
        transaction.onerror = transaction.onabort = () => reject(new Error('图片未能保存，原背景保留。请检查浏览器存储空间或换一张较小的图片。'));
      });
    }
    async function validateBackground(blob) {
      if (!(blob instanceof Blob) || !blob.size) throw new Error('请选择一张有效图片。');
      if (blob.size > 10 * 1024 * 1024) throw new Error('图片超过10MB，请选择一张较小的图片。');
      const bytes = new Uint8Array(await blob.slice(0, 12).arrayBuffer());
      const ascii = (start, end) => String.fromCharCode(...bytes.slice(start, end));
      let type = '';
      if (bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255) type = 'image/jpeg';
      else if ([137,80,78,71,13,10,26,10].every((value, index) => bytes[index] === value)) type = 'image/png';
      else if (ascii(0,4) === 'RIFF' && ascii(8,12) === 'WEBP') type = 'image/webp';
      else if (['GIF87a','GIF89a'].includes(ascii(0,6))) type = 'image/gif';
      if (!type) throw new Error('文件不是支持的图片，请选择 JPG、PNG、WebP 或 GIF。');
      const imageBlob = new Blob([blob], {type}), url = URL.createObjectURL(imageBlob);
      try {
        await new Promise((resolve, reject) => {
          const image = new Image();
          const timer = setTimeout(() => { image.onload = image.onerror = null; reject(new Error('图片读取超时，请换一张较小的图片。')); }, 10000);
          image.onload = () => {
            clearTimeout(timer);
            if (Math.max(image.naturalWidth, image.naturalHeight) > 8192 || image.naturalWidth * image.naturalHeight > 24000000)
              reject(new Error('图片像素过大，请使用长边不超过8192像素、总像素不超过2400万的图片。'));
            else resolve();
          };
          image.onerror = () => { clearTimeout(timer); reject(new Error('这张图片无法读取，请换一张图片。')); };
          image.src = url;
        });
        return {blob:imageBlob, url};
      } catch (error) { URL.revokeObjectURL(url); throw error; }
    }
    function useCustomImage(record, url) {
      const previous = customImageURL; customImageURL = url; customImageName = String(record.name || '自选图片').slice(0, 180);
      customOption.disabled = false;
      if (previous) URL.revokeObjectURL(previous);
    }
    backgroundFile.onchange = async () => {
      const file = backgroundFile.files?.[0]; if (!file) return;
      backgroundFile.disabled = imageSource.disabled = true; backgroundStatus('正在保存图片…');
      let pendingURL = '';
      try {
        const firstCustomImage = !customImageURL, validated = await validateBackground(file); pendingURL = validated.url;
        const record = {blob:validated.blob, name:file.name, updated:Date.now()};
        await backgroundRecord('readwrite', record);
        useCustomImage(record, validated.url); pendingURL = '';
        imageSource.value = 'custom'; background.checked = true;
        if (firstCustomImage) imageScope.value = 'all';
        const persisted = applyAppearance();
        backgroundStatus(persisted ? '图片已保存在当前浏览器，刷新后仍会使用。' : '图片已保存，但外观偏好无法保留；刷新后可重新选择“自选图片”。', !persisted);
      } catch (error) { backgroundStatus(error.message, true); }
      finally {
        if (pendingURL) URL.revokeObjectURL(pendingURL);
        backgroundFile.value = ''; backgroundFile.disabled = imageSource.disabled = false;
      }
    };
    imageSource.onchange = () => {
      applyAppearance();
      backgroundStatus(imageSource.value === 'custom' ? '使用已保存的自选图片。' : '已使用默认插画；上次的自选图片仍可切回。');
    };
    background.onchange = opacity.oninput = compact.onchange = imageScope.onchange = imageFit.onchange = imagePosition.onchange = applyAppearance;
    const requestedImageSource = appearance.imageSource;
    applyAppearance(false);
    (async () => {
      try {
        const record = await backgroundRecord('readonly');
        if (record) {
          const validated = await validateBackground(record.blob); useCustomImage(record, validated.url);
          imageSource.value = requestedImageSource === 'custom' ? 'custom' : 'default'; applyAppearance();
          backgroundStatus(imageSource.value === 'custom' ? '已恢复本机保存的背景。' : '默认插画；也可以切回上次的自选图片。');
        } else {
          imageSource.value = 'default'; applyAppearance(); backgroundStatus('选择图片后会自动保存到当前浏览器。');
        }
      } catch (error) { imageSource.value = 'default'; applyAppearance(); backgroundStatus(error.message, true); }
      finally { backgroundFile.disabled = imageSource.disabled = false; }
    })();
    themeButton.onclick = () => themeDialog.showModal();
    for (const dialog of [searchDialog, themeDialog]) dialog.addEventListener('click', event => {
      if (event.target === dialog) {
        const rect = dialog.getBoundingClientRect();
        if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
      }
    });

    // Labels remain labels and controls keep the same parent/ID/listeners.
    // Only the explanatory text is grouped to align names and controls as rows.
    const fieldGroups = '.two-fields,.settings-grid,.audio-grid,.picture-settings,.expression-fine-grid';
    for (const group of dashboard.querySelectorAll(fieldGroups)) group.classList.add('studio-field-list');
    for (const label of dashboard.querySelectorAll('label')) {
      if (label.closest('#model-panel.model-manager')) continue;
      const parent = label.parentElement;
      if (!parent.matches(fieldGroups + ',.studio-managed,.studio-root-details,.expression-card')) continue;
      const controls = [...label.children].filter(node => node.matches('input,select,textarea'));
      if (controls.length !== 1 || controls[0].type === 'range') continue;
      const control = controls[0], copy = make('span', 'studio-field-copy');
      if (control.type === 'checkbox') {
        // Some existing features update the label's trailing text node.
        label.classList.add('studio-field-row');
        continue;
      }
      copy.dataset.controlLabel = '';
      for (const node of [...label.childNodes]) if (node !== control) copy.append(node);
      if (!copy.textContent.trim()) continue;
      if (parent.parentElement.matches('.expression-presets')) {
        const hint = parent.querySelector(':scope > p.hint');
        if (hint) { copy.append(make('span', 'hint', hint.textContent)); hint.hidden = true; }
      }
      label.classList.add('studio-field-row');
      label.replaceChildren(copy, control);
    }
    // Older model forms use explicit label/input siblings. Keep their IDs and
    // associations while giving them the same layout as nested controls.
    for (const id of ['model-panel', 'web-search-panel']) {
      const panel = get(id);
      for (const label of [...panel.querySelectorAll(':scope > label[for]')]) {
        const control = get(label.htmlFor);
        if (!control || control.parentElement !== panel || label.nextElementSibling !== control) continue;
        const row = make('div', 'studio-field-row');
        label.before(row); row.append(label, control);
        const hint = row.nextElementSibling;
        if (hint?.matches('p.hint')) { hint.classList.add('studio-field-help'); row.append(hint); }
      }
    }
    // One page title is enough; section actions and detailed help stay visible.
    for (const [id, panel] of pages) {
      const heading = panel.querySelector(':scope > h2, :scope > .section-top h2, :scope > .pc-heading h2');
      if (heading) heading.classList.add('studio-redundant-title');
    }

    function simplifyLayout() {
      function disclosure(host, label, nodes, before = null) {
        const unique = [...new Set(nodes.filter(node => node && host.contains(node)))];
        const roots = unique.filter(node => !unique.some(parent => parent !== node && parent.contains(node)));
        if (!roots.length) return null;
        const fold = make('details', 'settings-drawer harbor-detail-fold');
        const summary = make('summary', '', label), body = make('div', 'harbor-detail-body');
        fold.append(summary, body);
        if (before?.parentElement === host) host.insertBefore(fold, before); else host.append(fold);
        for (const node of roots) body.append(node);
        return fold;
      }
      function fields(host, label, ids, before = null) {
        return disclosure(host, label, ids.map(id => {
          const input = get(id); return input && host.contains(input) ? input.closest('.studio-field-row,label') || input : null;
        }), before);
      }
      function collectButtons(ids, label) {
        const buttons = ids.map(get).filter(Boolean); if (!buttons.length) return;
        const menu = actionMenu(label, [], 'harbor-toolbar-more');
        buttons[0].before(menu.root); for (const button of buttons) menu.body.append(button);
      }
      for (const sourceId of Object.values(saves).flat()) get(sourceId)?.classList.add('harbor-source-save');

      // Keep common expression controls first; the existing fine view remains available.
      const expression = get('expression-panel');
      const expressionMore = disclosure(expression, '更多表达设置', [
        expression.querySelector('.expression-toolbar'), get('expression-fine-controls'),
        expression.querySelector('.expression-state-grid'), get('expression-relationships'),
        get('fishtalk-panel'), ...expression.querySelectorAll(':scope > details')
      ], expression.querySelector(':scope > .panel-footer'));
      if (expressionMore && get('expression-view-mode')?.value === 'fine') expressionMore.open = true;
      const short = get('expression-short-controls');
      for (const [selector, text] of [['h3','短句与发送条数'],['p.hint','字数、句数和每轮条数在一处调整。'],['a','调整短句上限']]) {
        const node = short.querySelector(selector); if (node) node.textContent = text;
      }

      const model = get('model-panel'), oldPicker = model.querySelector('.model-presets');
      if (oldPicker) {
      const pickerLabel = make('label', 'studio-field-row harbor-model-picker', '模型配置');
      const picker = make('select'); picker.id = 'harbor-model-profile'; pickerLabel.append(picker);
      for (const button of oldPicker.querySelectorAll('[data-model-preset]')) {
        const option = make('option', '', button.querySelector('strong')?.textContent || button.dataset.modelPreset);
        option.value = button.dataset.modelPreset; picker.append(option);
      }
      oldPicker.before(pickerLabel); oldPicker.classList.add('harbor-original-model-picker');
      const syncPicker = () => { picker.value = oldPicker.querySelector('[aria-pressed="true"]')?.dataset.modelPreset || picker.value; };
      syncPicker(); new MutationObserver(syncPicker).observe(oldPicker, {subtree:true,attributes:true,attributeFilter:['aria-pressed']});
      picker.addEventListener('change', () => oldPicker.querySelector('[data-model-preset="' + picker.value + '"]')?.click());
      model.querySelector('.model-preset-hint').textContent = '切换只读取该配置，保存后启用；地址、密钥和模型分别保留。';
      const modelMore = fields(model, '接口高级选项', ['api-style','max-tokens','reasoning-effort','model-stream-enabled'], model.querySelector(':scope > .panel-footer'));
      if (modelMore && get('test-model')) modelMore.lastElementChild.append(get('test-model'), get('test-result'));
      const modelNameRow = get('model')?.closest('.studio-field-row');
      const apiRow = get('api-url')?.closest('.studio-field-row');
      if (modelNameRow && apiRow) apiRow.after(modelNameRow);
      const temperatureRow = get('model-temperature')?.closest('.studio-field-row');
      const temperatureHint = temperatureRow?.nextElementSibling;
      if (temperatureHint?.matches('p.hint')) {
        temperatureHint.classList.add('studio-field-help'); temperatureRow.append(temperatureHint);
      }
      for (const hint of model.querySelectorAll(':scope > p.hint'))
        if (!hint.id && /低强度|流式/.test(hint.textContent)) modelMore?.lastElementChild.append(hint);
      }

      const habits = get('habits-panel');
      const habitNodes = ['followup-wait-enabled','repeat-probability','context-char-budget','digest-enabled'].map(id => {
        const input = get(id); if (!input || !habits.contains(input)) return null;
        return input.closest('details') || input.closest('.studio-field-row,label');
      });
      habitNodes.push(...habits.querySelectorAll(':scope > details'), get('autonomy-controls'));
      const habitsMore = disclosure(habits, '接话与上下文细节', habitNodes);
      const waitDetails = get('followup-wait-enabled')?.closest('details');
      if (habitsMore && waitDetails && habitsMore.contains(waitDetails)) waitDetails.open = true;

      const growth = get('growth-console');
      fields(growth, '探索频率与模型', ['growth-reads','growth-interval','growth-worker'], growth.querySelector(':scope > .panel-footer'));
      collectButtons(['growth-compact','growth-refresh','growth-earlier'], '更多操作');
      collectButtons(['pc-refresh','pc-new','pc-copy','pc-archive'], '角色操作');

      const limits = get('send-limits-panel'), limitSections = [...limits.querySelectorAll(':scope > section.record')];
      if (limitSections[0]) limitSections[0].querySelector('h3').textContent = '短句与单轮条数';
      disclosure(limits, '发送频率与账号保护', limitSections.slice(1), limits.querySelector(':scope > .panel-footer'));

      const filterIds = ['hub-chat','hub-person','hub-tier','hub-status','hub-kind'];
      const filterToolbar = get('hub-query').parentElement;
      const filterFold = disclosure(filterToolbar, '筛选条件', filterIds.map(get));
      if (filterFold) {
        for (const select of [...filterFold.lastElementChild.querySelectorAll('select')]) {
          const label = make('label', '', select.getAttribute('aria-label')); select.before(label); label.append(select);
        }
        const updateFilterCount = () => {
          const count = filterIds.filter(id => get(id)?.value).length;
          filterFold.firstElementChild.textContent = count ? '筛选条件（' + count + ' 项）' : '筛选条件';
        };
        filterFold.addEventListener('change', updateFilterCount);
        const reset = make('button', 'quiet', '清除筛选'); reset.type = 'button';
        reset.onclick = () => { for (const id of filterIds) get(id).value = ''; updateFilterCount(); get('hub-search').click(); };
        filterFold.lastElementChild.append(reset); updateFilterCount();
      }
      collectButtons(['hub-refresh'], '更多操作');

      const imagePanel = get('images-panel');
      for (const detail of imagePanel.querySelectorAll(':scope > details')) {
        detail.classList.add('harbor-detail-fold');
        detail.open = detail.querySelector(':scope > summary')?.textContent.trim() === '生成图片';
      }
      const audio = get('audio-console');
      if (audio) {
        disclosure(audio, '声音模型接口', [audio.querySelector('.audio-grid')], audio.querySelector(':scope > .panel-footer'));
        for (const detail of audio.querySelectorAll(':scope > details')) {
          if (/资源|试听/.test(detail.firstElementChild?.textContent || '')) detail.open = false;
          detail.classList.add('harbor-detail-fold');
        }
        fields(audio, '音频授权与歌唱偏好', ['audio-chats','audio-singing-style'], audio.querySelector(':scope > .panel-footer'));
        collectButtons(['audio-new-preset','audio-default-preset','audio-reset-tuning'], '声线操作');
        const historyRow = get('audio-history')?.parentElement;
        disclosure(audio, '配置历史与任务操作', [get('audio-reload'), historyRow]);
      }
      const moments = get('moments-panel');
      disclosure(moments, '朋友圈配图选项', ['moments-image-pick-enabled','moments-image-generate-enabled']
        .map(id => get(id)?.closest('.setting-row')));

      // Home diagnostics are available without competing with the master switch.
      const diagnostic = make('details', 'settings-drawer harbor-detail-fold harbor-home-diagnostics');
      diagnostic.append(make('summary', '', '运行详情与计数'));
      for (const node of [...quick.children]) if (node.matches('.mini-stats,.catalog-line,p.hint')) diagnostic.append(node);
      quick.after(diagnostic);

      function simplifyRows(list) {
        for (const row of list.querySelectorAll(':scope > article.record')) {
          if (row.dataset.harborSimple) continue; row.dataset.harborSimple = 'true';
          const buttons = [...row.querySelectorAll(':scope > .row > button')];
          if (buttons.length > 1) {
            const menu = actionMenu('更多', [], 'harbor-item-more'); buttons[1].before(menu.root);
            for (const button of buttons.slice(1)) menu.body.append(button);
          }
        }
      }
      for (const id of ['hub-list','affection-list','growth-insights']) {
        const list = get(id); if (!list) continue;
        simplifyRows(list); new MutationObserver(() => simplifyRows(list)).observe(list, {childList:true});
      }
      const previousMemoryUpdate = window.memoryHub?.stateUpdated;
      if (previousMemoryUpdate) window.memoryHub.stateUpdated = (...args) => {
        if (get('hub-list')?.querySelector('.harbor-item-more[open]')) return;
        return previousMemoryUpdate.apply(window.memoryHub, args);
      };
      const records = get('records');
      function compactRecords() {
        for (const row of records.querySelectorAll(':scope > article.record')) {
          if (row.dataset.harborSimple) continue; row.dataset.harborSimple = 'true';
          const notes = [...row.querySelectorAll(':scope > p.hint')]; if (!notes.length) continue;
          const detail = make('details', 'harbor-record-detail'); detail.append(make('summary', '', '处理详情'), ...notes); row.append(detail);
        }
      }
      compactRecords(); new MutationObserver(compactRecords).observe(records, {childList:true});
    }
    simplifyLayout();

    const dirty = new Map(), versions = new WeakMap(), pendingSaves = new Map(), saveResults = new Map();
    const savingPages = new Set(); let submittingOwner = null;
    const pageTools = make('aside', 'studio-page-tools'); pageTools.id = 'sticky-save-bar';
    const sectionNav = make('nav', 'studio-section-nav'); sectionNav.setAttribute('aria-label', '本页设置');
    const saveArea = make('div', 'studio-save-area'), dirtyText = make('span', 'studio-save-state');
    dirtyText.setAttribute('role', 'status'); dirtyText.setAttribute('aria-live', 'polite');
    const saveActions = make('div', 'studio-save-actions');
    const saveError = make('p', 'studio-save-error'); saveError.hidden = true; saveError.setAttribute('role', 'alert');
    dirtyText.id = 'harbor-save-status';
    saveArea.append(dirtyText, saveActions, saveError); pageTools.append(saveArea); pageBody.append(pageTools);
    moreMenu.body.append(sectionNav);
    let activeId = 'overview';
    const contextKeys = ['context_char_budget', 'background_digest_enabled'];
    function fieldCount(id) { return new Set(dirty.get(id)?.values() || []).size; }
    function sourceHasDraft(sourceId, owner = activeId) {
      if (owner !== 'habits-panel') return fieldCount(owner) > 0;
      return [...(dirty.get(owner)?.values() || [])].some(key =>
        sourceId === 'save-context-strategy' ? contextKeys.includes(key) : !contextKeys.includes(key));
    }
    function showDirty() {
      const count = fieldCount(activeId), result = saveResults.get(activeId);
      const sources = (saves[activeId] || []).map(get).filter(Boolean), busy = savingPages.has(activeId) || sources.some(button => button.disabled);
      saveArea.hidden = !sources.length || activeId === 'bilibili-console' || (['memory-panel','affection-panel'].includes(activeId) && !count && !busy && !result);
      dirtyText.dataset.phase = busy ? 'saving' : result?.phase === 'error' ? 'error' :
        count ? 'dirty' : result?.phase === 'saved' ? 'saved' : 'synced';
      dirtyText.textContent = busy ? '正在保存…' : result?.phase === 'error' ? '保存失败，更改已保留' :
        count ? count + ' 项更改待保存' : result?.phase === 'saved' ? '更改已保存' : '修改后点击保存';
      dirtyText.title = result?.phase === 'error' ? result.message : '';
      saveError.hidden = result?.phase !== 'error';
      saveError.textContent = result?.phase === 'error' ? String(result.message || '请检查输入后重新保存。').slice(0, 240) : '';
      for (const mirror of saveActions.children) {
        mirror.disabled = !!savingPages.size || !sources.length || sources.some(button => button.disabled) || !count;
        mirror.textContent = busy ? '正在保存…' : '保存设置';
      }
      const pendingPages = [...dirty].filter(([, fields]) => fields.size);
      draftSwitch.hidden = !pendingPages.length;
      draftSwitch.textContent = pendingPages.length + ' 页未保存';
      draftSwitch.setAttribute('aria-label', '查看未保存的页面');
      for (const a of nav.querySelectorAll('[data-page]')) {
        const changed = !!dirty.get(a.dataset.page)?.size;
        a.classList.toggle('has-draft', changed);
        if (changed) a.title = '本页有未保存的设置'; else a.removeAttribute('title');
      }
      for (const a of workspaces.querySelectorAll('[data-workspace]')) {
        const group = groups.find(group => group[0] === a.dataset.workspace);
        a.classList.toggle('has-draft', group[2].some(id => dirty.get(id)?.size));
      }
      pageTools.hidden = !sources.length || saveArea.hidden || activeId === 'model-panel' && !!window.modelManager;
      window.dispatchEvent(new CustomEvent('whalegirl-studio-dirty', {
        detail: {id: activeId, count, busy, phase: dirtyText.dataset.phase, message: saveError.textContent}
      }));
    }
    function saveFailed(id, message) {
      saveResults.set(id, {phase: 'error', message});
      if (id === activeId) {
        const invalid = pages.get(id)?.querySelector('input:invalid,select:invalid,textarea:invalid');
        if (invalid) {
          for (let parent = invalid.parentElement; parent && parent !== pages.get(id); parent = parent.parentElement)
            if (parent.tagName === 'DETAILS') parent.open = true;
          invalid.setAttribute('aria-invalid', 'true'); invalid.focus();
        }
      }
      showDirty();
    }
    // Only successful, non-overlapping saves clear drafts; errors keep them visible.
    const originalSave = saveSubset;
    const draftSnapshot = () => new Map([...dirty].map(([id, fields]) =>
      [id, new Map([...fields].map(([field]) => [field, versions.get(field)]))]));
    saveSubset = async (values, ...keys) => {
      const savingId = submittingOwner || activeId;
      const inputSnapshot = draftSnapshot();
      let result;
      try { result = await originalSave(values, ...keys); }
      catch (error) { saveFailed(savingId, error.message); throw error; }
      for (const [id, inputs] of inputSnapshot) for (const [input, version] of inputs) {
        // A save may own only some settings on a page. Keep unrelated drafts.
        const owner = dirty.get(id)?.get(input);
        if (versions.get(input) === version && owner && (Object.prototype.hasOwnProperty.call(values, owner) ||
          owner === 'search_api_key' && keys[1])) dirty.get(id).delete(input);
      }
      saveResults.set(savingId, {phase: 'saved'}); showDirty();
      return result;
    };
    // Existing actions own validation and API errors. Surface their actual result
    // beside the save action as well as in the existing toast.
    const originalNotice = notice;
    notice = (message, error = false) => {
      originalNotice(message, error);
      if (error) for (const [id] of pendingSaves) {
        if ((saves[id] || []).some(identity => get(identity)?.disabled)) saveFailed(id, message);
      }
    };
    // Some existing saves refresh all forms. Keep unrelated drafts in memory only.
    const originalPopulate = populateSettings;
    populateSettings = () => {
      const drafts = [...dirty.values()].flatMap(fields => [...fields.keys()].map(input =>
        [input, input.type === 'checkbox' ? input.checked : input.value]));
      const chatDraft = dirty.get('chats')?.size ? {selected: new Set(selected), modes: new Map(modes)} : null;
      let modelDraft = null;
      if (dirty.get('model-panel')?.size && !window.modelManager) {
        rememberCurrentModelDraft();
        modelDraft = {profiles: JSON.parse(JSON.stringify(modelProfiles)), active: activeModelProfile, keys: {...draftModelKeys}};
      }
      originalPopulate();
      if (chatDraft) { selected = chatDraft.selected; modes = chatDraft.modes; renderChats(); }
      if (modelDraft) {
        modelProfiles = modelDraft.profiles; activeModelProfile = modelDraft.active; draftModelKeys = modelDraft.keys;
        loadModelProfile(activeModelProfile);
      }
      for (const [input, value] of drafts) {
        if (input.type === 'checkbox') input.checked = value; else input.value = value;
      }
      if (dirty.get('expression-panel')?.size) window.expressionSettings?.populate();
    };
    const fieldKeys = {
      'model': 'model', 'api-url': 'api_url', 'api-key': 'api_key', 'api-style': 'api_style',
      'model-temperature': 'model_temperature', 'max-tokens': 'max_tokens', 'reasoning-effort': 'reasoning_effort',
      'model-stream-enabled': 'model_stream_enabled', 'life-enabled': 'life_autonomy_enabled',
      'growth-enabled': 'growth_enabled', 'growth-auto': 'growth_auto_apply', 'bili-enabled': 'bilibili_enabled',
      'growth-tieba': 'growth_tieba_enabled', 'growth-bili-expression': 'growth_bilibili_expression_shared',
      'growth-tieba-expression': 'growth_tieba_expression_shared', 'growth-interval': 'growth_interval_minutes',
      'growth-reads': 'growth_reads_per_day', 'growth-explore': 'growth_exploration', 'growth-interests': 'growth_interests',
      'growth-worker': 'growth_internet_model_profile', 'growth-traits-chat': 'growth_mature_traits_chat_enabled',
      'chat-short-replies-enabled': 'chat_short_replies_enabled', 'chat-short-reply-sentences': 'chat_short_reply_sentences',
      'chat-short-reply-chars': 'chat_short_reply_chars', 'context-char-budget': 'context_char_budget',
      'digest-enabled': 'background_digest_enabled', 'context-count': 'context_messages',
      'quotes-enabled': 'quote_replies_enabled', 'cooldown': 'chat_cooldown', 'send-interval': 'min_send_interval',
      'bili-wechat-video': 'bilibili_wechat_video_enabled', 'bili-comment-mentions': 'bilibili_comment_mentions_enabled',
      'bili-comment-reply': 'bilibili_comment_reply_enabled', 'bili-reply-scope': 'bilibili_reply_scope', 'bili-reply-to-replies': 'bilibili_reply_to_replies_enabled', 'bili-reply-special': 'bilibili_reply_use_special_following',
      'bili-reply-mode': 'bilibili_reply_mode', 'bili-reply-mids': 'bilibili_reply_allowed_sender_mids',
      'bili-reply-deep-min': 'bilibili_reply_deep_max_minutes', 'bili-reply-deep-win': 'bilibili_reply_deep_max_windows',
      'bili-reply-per-day': 'bilibili_reply_per_day', 'bili-reply-interval': 'bilibili_reply_min_interval_seconds',
      'bili-reply-thread-cap': 'bilibili_reply_max_per_thread', 'bili-reply-age': 'bilibili_reply_max_age_hours',
      'bili-reply-record-ignored': 'bilibili_reply_record_ignored',
      'bili-reply-retention-limit': 'bilibili_reply_retention_limit', 'bili-reply-retention-days': 'bilibili_reply_retention_days',
      'bili-visual': 'bilibili_visual_enabled', 'bili-comments': 'bilibili_comments_enabled',
      'bili-danmaku': 'bilibili_danmaku_enabled', 'bili-media': 'bilibili_media_enabled',
      'bili-interval': 'bilibili_interval_minutes', 'bili-requests': 'bilibili_requests_per_day',
      'bili-cache': 'bilibili_cache_mb', 'bili-days': 'bilibili_cache_days', 'bili-comment-limit': 'bilibili_comments_limit',
      'bili-comment-pages': 'bilibili_comment_pages', 'bili-replies': 'bilibili_reply_threads',
      'bili-frames': 'bilibili_keyframes_limit', 'bili-segment': 'bilibili_segment_seconds', 'bili-seeds': 'bilibili_interest_seeds',
      'hub-enabled': 'memory_enabled', 'hub-consolidation': 'memory_consolidation_enabled',
      'hub-budget': 'memory_token_budget', 'hub-limit': 'memory_item_limit', 'hub-cold-days': 'memory_cold_after_days',
      'hub-eagerness': 'learning_eagerness', 'hub-ai-editing': 'editable_memory_enabled', 'hub-auto-accept': 'learning_auto_accept',
      'web-search-api-key': 'search_api_key', 'participation': 'chat_participation',
      'repeat-probability': 'repeat_probability', 'affection-owner-ids': 'owner_ids',
      'affection-private-unreserved-enabled': 'private_unreserved_enabled'
    };
    const ignore = /^(hub-(query|chat|person|tier|status|kind)$|memory-(search|filter|show)|persona-live-chat|records-scope|chat-search|inbox-|pc-search|expression-(view-mode|tone|banter|length)$|quick-enabled|audio-|tieba-tieba_enabled|bili-(user-query|picker-filter|replies-filter|replies-search)$)/;
    grid.addEventListener('input', trackDirty); grid.addEventListener('change', trackDirty);
    function trackDirty(event) {
      const input = event.target;
      if (!input.matches('input,select,textarea') || input.closest('dialog') || input.type === 'search' ||
        input.type === 'file' || (!input.id && !input.closest('.chat-row')) || ignore.test(input.id) || !saves[activeId]?.length) return;
      const fields = dirty.get(activeId) || new Map();
      versions.set(input, (versions.get(input) || 0) + 1);
      fields.set(input, input.closest('.chat-row') ? (input.type === 'checkbox' ? 'allowed_chat_ids' : 'trigger_only_chat_ids') :
        fieldKeys[input.id] || (input.id.startsWith('style-') ? input.id.slice(6).replaceAll('-', '_') : input.id.replaceAll('-', '_')));
      input.removeAttribute('aria-invalid');
      saveResults.delete(activeId); dirty.set(activeId, fields); showDirty();
    }
    document.addEventListener('click', event => {
      const button = event.target.closest('button');
      if (button && saves[activeId]?.includes(button.id)) {
        pendingSaves.set(activeId, draftSnapshot().get(activeId));
        saveResults.delete(activeId);
      }
    }, true);
    window.addEventListener('whalegirl-panel-saved', event => {
      const id = event.detail;
      for (const [input, version] of pendingSaves.get(id) || []) {
        if (versions.get(input) === version) dirty.get(id)?.delete(input);
      }
      pendingSaves.delete(id); saveResults.set(id, {phase: 'saved'}); showDirty();
    });
    const saveObserver = new MutationObserver(showDirty);
    for (const id of new Set(Object.values(saves).flat())) {
      const source = get(id);
      if (source) saveObserver.observe(source, {attributes: true, attributeFilter: ['disabled']});
    }
    function buildSaveActions() {
      saveActions.replaceChildren();
      if (!(saves[activeId] || []).some(get)) return;
      const owner = activeId, mirror = make('button', 'primary', '保存设置'); mirror.type = 'button';
      mirror.dataset.savePage = owner; mirror.setAttribute('aria-describedby', dirtyText.id);
      mirror.onclick = async () => {
        if (savingPages.size) return;
        const sources = (saves[owner] || []).map(get).filter(source => source && sourceHasDraft(source.id, owner));
        if (!sources.length) return;
        savingPages.add(owner); saveResults.delete(owner); showDirty();
        try {
          for (const source of sources) {
            pendingSaves.set(owner, draftSnapshot().get(owner));
            if (typeof source.onclick !== 'function') throw new Error('保存入口暂不可用，请刷新页面后重试。');
            let request;
            submittingOwner = owner;
            try { request = source.onclick.call(source, new Event('click')); }
            finally { submittingOwner = null; }
            await request;
            if (saveResults.get(owner)?.phase === 'error') break;
          }
        } catch (error) { saveFailed(owner, error.message); }
        finally { pendingSaves.delete(owner); savingPages.delete(owner); showDirty(); }
      };
      saveActions.append(mirror);
    }
    window.addEventListener('beforeunload', event => {
      if ([...dirty.values()].some(fields => fields.size)) { event.preventDefault(); event.returnValue = ''; }
    });

    let sectionTargets = [];
    const sectionSpecs = {
      'expression-panel': [
        ['.expression-toolbar', '常用语气'], ['#expression-short-controls', '短句设置'],
        ['.expression-state-grid', '情绪与关系'], ['#fishtalk-panel', '真人语料']
      ],
      'memory-panel': [['#hub-tabs', '记忆分类'], ['#hub-query', '检索资料'], ['#hub-list', '记忆列表']],
      'model-panel': [['#mm-search', '配置列表'], ['#api-url', '接口与模型'], ['#mm-price-section', '费用计算']],
      'habits-panel': [['#interest-keywords', '聊天兴趣'], ['details.settings-drawer', '等待补充'], ['#context-char-budget', '话题整理']],
      'bilibili-console': [
        ['#bili-tab-replies', '@回复与记录'],
        ['#bili-tab-policy', '策略与白名单'],
        ['#bili-tab-explore', '探索与作品'],
        ['#bili-tab-settings', '账号与读取配置']
      ]
    };
    function buildSectionNav() {
      sectionNav.replaceChildren(); sectionTargets = [];
      const panel = pages.get(activeId);
      if (!panel) { sectionNav.hidden = true; return; }
      const targets = sectionSpecs[activeId] ? sectionSpecs[activeId].map(([selector, label]) =>
        [panel.querySelector(selector), label]).filter(([node]) => node) :
        [...panel.querySelectorAll('h3,details>summary')].filter(node => {
          if (node.parentElement.classList.contains('studio-root-details') || node.closest('[hidden],article,.record,.pc-card,.sl-card')) return false;
          const details = node.parentElement.closest('details:not([open])');
          return !details || node === details.querySelector(':scope > summary');
        }).slice(0, 7).map(node => [node, node.textContent.trim().replace(/^[^\p{L}\p{N}]+/u, '')]);
      sectionNav.hidden = targets.length < 2;
      for (const [node, label] of targets) {
        const button = make('button', '', label.length > 18 ? label.slice(0, 18) + '…' : label);
        button.type = 'button'; button.title = label;
        button.onclick = () => {
          if (node.matches('[data-bili-tab]')) {
            node.click();
            node.scrollIntoView({block: 'start', behavior: 'instant'});
            return;
          }
          if (node.tagName === 'DETAILS') node.open = true;
          for (let parent = node.parentElement;
            parent && parent !== panel; parent = parent.parentElement) if (parent.tagName === 'DETAILS') parent.open = true;
          const target = node.tagName === 'DETAILS' ? node.querySelector(':scope > summary') :
            node.matches('input,textarea,select') ? node.closest('.studio-field-row,label') || node : node;
          target.scrollIntoView({block: 'start', behavior: 'instant'});
          for (const item of sectionNav.children) item.removeAttribute('aria-current');
          button.setAttribute('aria-current', 'location');
        };
        sectionNav.append(button); sectionTargets.push([node, button]);
      }
      sectionNav.firstElementChild?.setAttribute('aria-current', 'location');
    }
    let scrollPending = false;
    window.addEventListener('scroll', () => {
      if (scrollPending || !sectionTargets.length) return;
      scrollPending = true;
      requestAnimationFrame(() => {
        scrollPending = false;
        const threshold = header.getBoundingClientRect().bottom + 80;
        let current = sectionTargets[0]?.[1];
        for (const [node, button] of sectionTargets)
          if (node.getClientRects().length && node.getBoundingClientRect().top <= threshold) current = button;
        for (const [, button] of sectionTargets) {
          if (button === current) button.setAttribute('aria-current', 'location'); else button.removeAttribute('aria-current');
        }
      });
    }, {passive: true});

    function paginateMemories() {
      const list = get('hub-list');
      if (!list) return;
      let page = 0;
      const pager = make('div', 'studio-memory-pager');
      const before = make('button', 'secondary', '上一组'), after = make('button', 'secondary', '下一组'), info = make('span', 'hint');
      before.type = after.type = 'button'; pager.append(before, info, after); list.after(pager);
      function render() {
        const cards = [...list.querySelectorAll(':scope > article')], total = Math.max(1, Math.ceil(cards.length / 12));
        page = Math.min(page, total - 1);
        cards.forEach((card, index) => { card.hidden = index < page * 12 || index >= (page + 1) * 12; });
        pager.hidden = cards.length <= 12;
        info.textContent = `当前批次 ${page + 1} / ${total} 组，共 ${cards.length} 条`;
        before.disabled = page === 0; after.disabled = page === total - 1;
      }
      before.onclick = () => { page--; render(); list.scrollIntoView({block: 'start'}); };
      after.onclick = () => { page++; render(); list.scrollIntoView({block: 'start'}); };
      new MutationObserver(() => { page = 0; render(); }).observe(list, {childList: true});
      render();
    }
    paginateMemories();


    function activate(raw) {
      closeActionMenus();
      closeMenu();
      const requested = String(raw || '#overview').replace(/^#/, '');
      const id = aliases[requested] || requested;
      activeId = id === 'overview' || pages.get(id) ? id : 'overview';
      const row = metadata.get(activeId), isHome = activeId === 'overview';
      for (const [key, panel] of pages) if (panel) {
        panel.hidden = key !== activeId; panel.classList.toggle('active-panel', key === activeId);
      }
      for (const key of ['bilibili-console', 'growth-console', 'life-settings']) {
        const original = get(key);
        if (original?.tagName === 'DETAILS') original.open = key === activeId || key === 'growth-console' && activeId === 'life-settings';
      }
      hero.hidden = !isHome; pageHeading.hidden = isHome; homeWorkspace.hidden = !isHome;
      overview.hidden = !isHome; quick.hidden = !isHome; quick.classList.toggle('active-panel', isHome);
      activity.querySelector('h2').classList.toggle('studio-redundant-title', !isHome);
      if (isHome) { live.append(activity); activity.hidden = false; activity.classList.add('active-panel'); }
      else if (activeId === 'records-panel') grid.prepend(activity);
      main.classList.toggle('is-overview-view', isHome); main.classList.toggle('is-subpage-view', !isHome);
      main.dataset.studioPage = activeId; title.textContent = row[1]; description.textContent = row[2];
      const group = groups.find(group => group[2].includes(activeId));
      crumbGroup.textContent = group[1]; crumbTitle.textContent = row[1];
      breadcrumb.dataset.single = String(group[1] === row[1]);
      for (const page of group[2]) {
        const link = nav.querySelector('[data-page="' + page + '"]'); if (link) nav.append(link);
      }
      for (const a of nav.querySelectorAll('[data-page]')) {
        a.hidden = !group[2].includes(a.dataset.page);
        const selected = a.dataset.page === activeId; a.classList.toggle('active', selected);
        if (selected) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
      }
      for (const a of workspaces.querySelectorAll('[data-workspace]')) {
        const selected = a.dataset.workspace === group[0]; a.classList.toggle('active', selected);
        a.setAttribute('aria-expanded', String(selected));
        if (selected) a.setAttribute('aria-current', 'true'); else a.removeAttribute('aria-current');
        if (selected) a.after(nav);
      }
      document.title = row[1] + ' · 鲸鱼娘';
      buildSaveActions(); buildSectionNav(); showDirty();
      if (!sidebarMedia.matches) nav.querySelector('[aria-current="page"]')?.scrollIntoView({block:'nearest',inline:'nearest'});
      window.scrollTo({top:0,behavior:'instant'});
      window.dispatchEvent(new CustomEvent('whalegirl-studio-page', {detail:activeId}));
    }
    nav.addEventListener('click', event => {
      const link = event.target.closest('[data-page]'); if (link && location.hash === link.hash) activate(link.hash);
    });
    workspaces.addEventListener('click', event => {
      const link = event.target.closest('[data-workspace]'); if (link && location.hash === link.hash) activate(link.hash);
    });
    window.addEventListener('hashchange', () => activate(location.hash));
    document.addEventListener('keydown', event => {
      if ((event.key === '/' || (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') &&
        !event.target.closest('input,textarea,select,[contenteditable="true"]') && !document.querySelector('dialog[open]')) {
        event.preventDefault(); searchButton.click();
      }
    });
    const previousRecovery = window.recoveryCenter?.stateUpdated;
    if (window.recoveryCenter) window.recoveryCenter.stateUpdated = s => {
      previousRecovery?.(s);
      const online = s.gateway?.status === 'logged_in', authorized = cfg?.allowed_chat_ids?.length || 0;
      const missingModel = cfg && !cfg.api_key_configured;
      const attention = !!s.paused_reason || !online || !!s.last_error || missingModel || !authorized;
      status.classList.toggle('harbor-attention', attention);
      homeStatus.dataset.state = attention ? 'attention' : s.enabled ? 'ready' : 'idle';
      homeStatus.textContent = !online ? '微信未连接' : s.paused_reason ? '回复已暂停' : s.last_error ? '有异常记录' :
        missingModel ? '需要配置模型' : !authorized ? '尚未选择会话' : s.enabled ? '正在相处' : '自动回复关闭';
      homeSummary.textContent = !online ? '先连接微信，再开始新的对话。' : s.paused_reason ?
        '回复已暂停，请核对运行异常与发送结果。' : s.last_error ? '最近有异常记录，请查看原因与当前处理状态。' : missingModel ?
        '微信已经连接，接下来配置聊天模型。' : !authorized ? '选择允许聊天的会话，她才能参与其中。' : !s.enabled ?
        '微信保持在线，自动回复目前关闭。' : `在 ${authorized} 个已授权会话里，继续今天的对话。`;
      const destination = !online ? 'login-panel' : s.paused_reason || s.last_error ? 'system-health-panel' : missingModel ? 'model-panel' : 'chats';
      nextAction.href = '#' + destination;
      nextAction.textContent = !online ? '连接微信' : destination === 'system-health-panel' ? '处理运行异常' : destination === 'model-panel' ? '配置聊天模型' : '管理聊天会话';
      get('compact-conn-badge').textContent = online ? '微信在线' : '微信未连接';
      get('compact-reply-badge').textContent = s.paused_reason ? '回复暂停' : s.enabled ? '回复已开启' : '回复关闭';
      get('compact-timing-badge').textContent = s.last_reply_timing?.total_seconds == null ? '' : s.last_reply_timing.total_seconds.toFixed(1) + ' 秒';
    };
    const originalOpenIssues = window.recoveryCenter?.openIssues;
    if (window.recoveryCenter) window.recoveryCenter.openIssues = () => {
      location.hash = '#system-health-panel'; activate('#system-health-panel'); originalOpenIssues?.();
    };
    window.whalegirlStudio = {activate, routes:catalogue.map(row=>row[0])};
    activate(location.hash);
    if (state) window.recoveryCenter?.stateUpdated(state);
  }
  if (document.readyState !== 'complete') document.addEventListener('DOMContentLoaded', init, {once:true});
  else init();
})();
