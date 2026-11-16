'use strict';
(() => {
  const host = $('growth-console');
  if (!host) return;

  const panel = document.createElement('details');
  panel.className = 'settings-drawer bili-workbench';
  panel.id = 'bilibili-console';
  panel.innerHTML = `
    <summary>B站账号、探索与成长</summary>
    <div class="bili-shell">
      <header class="bili-command-deck">
        <div class="bili-deck-top">
          <div class="bili-identity-row">
            <div id="bili-master-slot" class="bili-master-slot"></div>
            <button id="bili-account-pill" type="button" class="bili-status-pill" data-tone="muted" title="点击切换到账号与读取配置">
              <span class="bili-dot" aria-hidden="true"></span>
              <span id="bili-account-pill-text">读取账号中</span>
            </button>
            <span id="bili-dirty-pill" class="bili-dirty-pill" hidden>0 项未保存</span>
          </div>
          <div class="bili-deck-actions">
            <button id="bili-poll-mentions" type="button" class="secondary">检查 @ 与追评</button>
            <button id="bili-explore" type="button" class="secondary">探索一次</button>
            <button id="bili-refresh" type="button" class="secondary">刷新</button>
            <button id="bili-save" type="button" class="primary">保存设置</button>
          </div>
        </div>
        <p id="bili-account-state" class="bili-account-sub" role="status">正在读取账号状态</p>
        <div id="bili-alert-banner" class="bili-alert-banner" hidden role="alert">
          <div id="bili-alert-copy" class="bili-alert-copy"></div>
          <div class="bili-alert-actions">
            <button id="bili-resume" type="button" class="secondary" hidden>恢复读取</button>
            <button id="bili-reply-resume" type="button" class="secondary" hidden>恢复评论发送</button>
            <button id="bili-goto-login" type="button" class="secondary" hidden>去扫码重登</button>
          </div>
        </div>
        <div class="bili-kpi-grid" aria-label="B站运行概览">
          <div class="bili-kpi-card">
            <div class="bili-kpi-label">今日评论回复</div>
            <div id="bili-kpi-sent" class="bili-kpi-value">0 / 0</div>
            <div class="bili-kpi-sub bili-kpi-links">
              <button type="button" class="bili-kpi-link" data-jump-tab="replies" data-jump-filter="drafted">待审 <strong id="bili-kpi-drafted">0</strong></button>
              <button type="button" class="bili-kpi-link" data-jump-tab="replies" data-jump-filter="active">排队 <strong id="bili-kpi-queued">0</strong></button>
            </div>
          </div>
          <button type="button" class="bili-kpi-card bili-kpi-btn" data-jump-tab="policy">
            <div class="bili-kpi-label">回复触发策略</div>
            <div id="bili-kpi-mode" class="bili-kpi-value">已关闭</div>
            <div id="bili-kpi-scope" class="bili-kpi-sub">点击配置白名单与追评</div>
          </button>
          <button type="button" class="bili-kpi-card bili-kpi-btn" data-jump-tab="settings">
            <div class="bili-kpi-label">@ 与追评监听</div>
            <div id="bili-kpi-monitor" class="bili-kpi-value">待读取</div>
            <div id="bili-kpi-monitor-sub" class="bili-kpi-sub">每 5 分钟检查通知</div>
          </button>
          <button type="button" class="bili-kpi-card bili-kpi-btn" data-jump-tab="explore">
            <div class="bili-kpi-label">自主探索与配额</div>
            <div id="bili-kpi-reads" class="bili-kpi-value">今日 0 支</div>
            <div id="bili-kpi-budget" class="bili-kpi-sub">24h 请求 0 / 0</div>
          </button>
        </div>
        <div id="bili-tabs" class="bili-tabs" role="tablist" aria-label="B站管理分区">
          <button id="bili-tab-replies" type="button" role="tab" class="bili-tab" data-bili-tab="replies" aria-controls="bili-panel-replies" aria-selected="true">
            <span>@回复与记录</span>
            <span id="bili-tab-badge-replies" class="bili-tab-badge" hidden>0</span>
          </button>
          <button id="bili-tab-policy" type="button" role="tab" class="bili-tab" data-bili-tab="policy" aria-controls="bili-panel-policy" aria-selected="false" tabindex="-1">
            <span>策略与白名单</span>
            <span id="bili-tab-badge-policy" class="bili-tab-badge is-subtle">全部</span>
          </button>
          <button id="bili-tab-explore" type="button" role="tab" class="bili-tab" data-bili-tab="explore" aria-controls="bili-panel-explore" aria-selected="false" tabindex="-1">
            <span>探索与作品</span>
            <span id="bili-tab-badge-explore" class="bili-tab-badge is-subtle" hidden>0</span>
          </button>
          <button id="bili-tab-watch" type="button" role="tab" class="bili-tab" data-bili-tab="watch" aria-controls="bili-panel-watch" aria-selected="false" tabindex="-1">
            <span>三游追更与排期</span>
            <span id="bili-tab-badge-watch" class="bili-tab-badge is-subtle" hidden>待命</span>
          </button>
          <button id="bili-tab-settings" type="button" role="tab" class="bili-tab" data-bili-tab="settings" aria-controls="bili-panel-settings" aria-selected="false" tabindex="-1">
            <span>账号与读取配置</span>
            <span id="bili-tab-badge-settings" class="bili-tab-badge is-subtle" hidden></span>
          </button>
        </div>
      </header>

      <section id="bili-panel-replies" class="bili-tabpanel" role="tabpanel" aria-labelledby="bili-tab-replies" data-bili-panel="replies">
        <div class="bili-inline-summary">
          <p id="bili-reply-state" class="bili-inline-status" role="status">B站评论@与追评回复默认关闭。</p>
          <button type="button" class="quiet bili-jump-policy" data-jump-tab="policy">调整触发开关与白名单 →</button>
        </div>
        <div class="bili-replies-toolbar">
          <div id="bili-replies-pills" class="bili-filter-pills" role="group" aria-label="按回复状态筛选"></div>
          <div class="bili-replies-right">
            <select id="bili-replies-filter" class="bili-sr-select" aria-label="筛选评论回复状态">
              <option value="all">全部状态</option>
              <option value="active">排队 / 深看中</option>
              <option value="drafted">待发送草稿</option>
              <option value="sent">已回复</option>
              <option value="ignored">已忽略</option>
              <option value="failed">失败</option>
            </select>
            <input id="bili-replies-search" type="search" placeholder="搜索视频标题、昵称、UID 或评论…" aria-label="搜索评论回复记录">
            <span class="bili-count-meta">显示 <strong id="bili-replies-count">0</strong> 条<span id="bili-replies-cap-hint"></span></span>
            <button id="bili-replies-prune" type="button" class="quiet">清理已完结</button>
          </div>
        </div>
        <div id="bili-replies-list" class="bili-replies-list"></div>
      </section>

      <section id="bili-panel-policy" class="bili-tabpanel" role="tabpanel" aria-labelledby="bili-tab-policy" data-bili-panel="policy" hidden>
        <section id="bili-reply-settings" class="bili-card">
          <div class="bili-card-head">
            <div>
              <h3>评论区 @ 与追评回复规则</h3>
              <p class="bili-card-sub">控制谁可以触发大肥鱼深看视频并回复，以及单日与同楼频控。</p>
            </div>
          </div>
          <div class="bili-switch-grid">
            <label class="bili-switch-item"><input id="bili-comment-reply" type="checkbox"><span><strong>启用评论区自动回复</strong><small>收到 @ 提及或追评时按下方规则深度看片并回复</small></span></label>
            <label class="bili-switch-item"><input id="bili-reply-to-replies" type="checkbox"><span><strong>回复楼内追评</strong><small>有人直接回复大肥鱼已发评论时继续跟进对话</small></span></label>
            <label class="bili-switch-item"><input id="bili-reply-special" type="checkbox"><span><strong>同步 B 站「特别关注」</strong><small>将特别关注分组账号自动纳入置顶好友名单</small></span></label>
            <label class="bili-switch-item"><input id="bili-reply-record-ignored" type="checkbox"><span><strong>保留已忽略 @ 记录</strong><small>关闭后，非白名单或模型判断跳过的内容不再写入回复列表</small></span></label>
          </div>
          <div class="bili-select-grid">
            <label class="bili-field">
              <span class="bili-field-label">触发范围</span>
              <select id="bili-reply-scope">
                <option value="all">任何人 @ 或回复我皆可触发（推荐）</option>
                <option value="whitelist">仅白名单与特别关注触发</option>
              </select>
            </label>
            <label class="bili-field">
              <span class="bili-field-label">发送模式</span>
              <select id="bili-reply-mode">
                <option value="auto">命中规则自动发送</option>
                <option value="review">先审后发（草稿）</option>
              </select>
            </label>
          </div>
          <div class="bili-num-grid">
            <label class="bili-field"><span class="bili-field-label">深度看片上限</span><span class="bili-input-unit"><input id="bili-reply-deep-min" type="number" min="5" max="120"><span>分钟</span></span></label>
            <label class="bili-field"><span class="bili-field-label">最多读取分段</span><span class="bili-input-unit"><input id="bili-reply-deep-win" type="number" min="1" max="12"><span>段</span></span></label>
            <label class="bili-field"><span class="bili-field-label">每日回复上限</span><span class="bili-input-unit"><input id="bili-reply-per-day" type="number" min="1" max="50"><span>条</span></span></label>
            <label class="bili-field"><span class="bili-field-label">发评最小间隔</span><span class="bili-input-unit"><input id="bili-reply-interval" type="number" min="15" max="3600"><span>秒</span></span></label>
            <label class="bili-field"><span class="bili-field-label">同评论楼最多回复</span><span class="bili-input-unit"><input id="bili-reply-thread-cap" type="number" min="1" max="10"><span>次</span></span></label>
            <label class="bili-field"><span class="bili-field-label">有效 @ 时效</span><span class="bili-input-unit"><input id="bili-reply-age" type="number" min="1" max="168"><span>小时</span></span></label>
            <label class="bili-field"><span class="bili-field-label">列表最多保留</span><span class="bili-input-unit"><input id="bili-reply-retention-limit" type="number" min="5" max="200"><span>条</span></span></label>
            <label class="bili-field"><span class="bili-field-label">完结记录保留</span><span class="bili-input-unit"><input id="bili-reply-retention-days" type="number" min="1" max="90"><span>天</span></span></label>
          </div>
        </section>

        <section id="bili-allowed-picker" class="bili-card">
          <div class="bili-card-head">
            <div>
              <h3 id="bili-picker-title">允许触发回复的人（白名单与特别关注）</h3>
              <p id="bili-picker-scope-hint" class="bili-card-sub">勾选或添加后请点击顶部「保存设置」生效</p>
            </div>
            <button id="bili-sync-directory" type="button" class="secondary">刷新关注与候选人</button>
          </div>
          <div id="bili-allowed-chips" class="bili-chips-box"><span class="hint">尚未选择允许回复的人。</span></div>
          <div class="bili-search-row">
            <input id="bili-user-query" type="search" placeholder="输入 B 站 UID、space.bilibili.com 主页链接或昵称搜索添加">
            <button id="bili-user-lookup" type="button" class="secondary">查找 / 添加</button>
          </div>
          <div id="bili-lookup-results" class="bili-picker-list" hidden></div>
          <input id="bili-picker-filter" type="search" placeholder="快速筛选下方「我的关注」或「最近 @ 我的人」">
          <div class="bili-picker-cols">
            <div class="bili-picker-col">
              <h4>我关注的 B 站好友 <small id="bili-following-count" class="hint"></small></h4>
              <div id="bili-following-list" class="bili-picker-list"><p class="hint">登录后点「刷新关注与候选人」加载关注列表。</p></div>
            </div>
            <div class="bili-picker-col">
              <h4>最近在评论区 @ 我的人 <small class="hint">历史触发过的账号</small></h4>
              <div id="bili-recent-senders" class="bili-picker-list"><p class="hint">暂无评论区 @ 记录。</p></div>
            </div>
          </div>
          <details id="bili-mids-advanced" class="bili-fold">
            <summary>手动批量编辑白名单 UID（一行一个，支持 # 备注）</summary>
            <label class="bili-field bili-fold-field">
              <span class="bili-field-label">主人与置顶好友 UID 列表</span>
              <textarea id="bili-reply-mids" rows="3" placeholder="例如：1009745567 # 主人"></textarea>
            </label>
          </details>
          <details class="bili-fold bili-rule-note">
            <summary>回复安全策略说明</summary>
            <p class="hint">默认「任何人 @ 或回复我皆可触发」，也可随时切回「仅白名单与特别关注触发」。仅在明确开启「评论区自动回复」时才会在深看后在同一评论楼回复；绝不自动点赞、投币、关注、收藏或主动发评，不会回复自己，并受同楼回复上限与时效保护。看完只保留轻观看与回复记录，不留存续看进度、帧图与学习细节。</p>
          </details>
        </section>
      </section>

      <section id="bili-panel-explore" class="bili-tabpanel" role="tabpanel" aria-labelledby="bili-tab-explore" data-bili-panel="explore" hidden>
        <div class="bili-inline-summary">
          <p id="bili-usage" class="bili-inline-status">正在读取今日探索统计…</p>
        </div>
        <div class="bili-explore-grid">
          <section class="bili-card">
            <div class="bili-card-head">
              <h3>探索任务（含跳过与失败）</h3>
              <span id="bili-tasks-count" class="bili-count-pill">0</span>
            </div>
            <div id="bili-tasks" class="bili-task-list"></div>
          </section>
          <section class="bili-card">
            <div class="bili-card-head">
              <h3>接触过的作品</h3>
              <span id="bili-readings-count" class="bili-count-pill">0</span>
            </div>
            <div id="bili-readings" class="bili-readings-list"></div>
          </section>
        </div>
        <div class="bili-explore-grid">
          <section class="bili-card">
            <div class="bili-card-head">
              <h3>B站与贴吧的统一兴趣</h3>
              <span id="bili-interests-count" class="bili-count-pill">0</span>
            </div>
            <div id="bili-interests" class="bili-subcard-grid"></div>
          </section>
          <section class="bili-card">
            <div class="bili-card-head">
              <h3>B站来源认识</h3>
              <span id="bili-insights-count" class="bili-count-pill">0</span>
            </div>
            <div id="bili-insights" class="bili-subcard-grid"></div>
          </section>
        </div>
      </section>

      <section id="bili-panel-settings" class="bili-tabpanel" role="tabpanel" aria-labelledby="bili-tab-settings" data-bili-panel="settings" hidden>
        <div class="bili-settings-grid">
          <section class="bili-card">
            <div class="bili-card-head">
              <div>
                <h3>B站账号与登录授权</h3>
                <p id="bili-account-card-note" class="bili-card-sub">使用个人账号登录后，可访问关注动态、收藏及评论区 @ 与追评通知。</p>
              </div>
            </div>
            <div class="bili-btn-row">
              <button id="bili-login" type="button" class="primary">扫码登录B站</button>
              <button id="bili-check" type="button" class="secondary">核对登录状态</button>
              <button id="bili-logout" type="button" class="secondary">退出并使用匿名模式</button>
            </div>
            <div id="bili-login-box" class="bili-qr-box" hidden>
              <img id="bili-qr" alt="B站登录二维码，仅供管理员本人扫码">
              <div class="bili-qr-copy">
                <strong>等待扫码授权</strong>
                <p id="bili-login-status" class="hint">请用B站手机客户端扫码并确认</p>
              </div>
            </div>
          </section>

          <section class="bili-card">
            <div class="bili-card-head">
              <div>
                <h3>外部触发与通知监听</h3>
                <p class="bili-card-sub">微信视频卡片解析与B站评论区 @ 轮询独立开关。</p>
              </div>
            </div>
            <div class="bili-switch-stack">
              <label class="bili-switch-item"><input id="bili-wechat-video" type="checkbox"><span><strong>允许微信 @ 后读取指定视频</strong><small>群聊真实 @ 机器人并附上B站链接或引用含链接消息时自动观看</small></span></label>
              <label class="bili-switch-item"><input id="bili-comment-mentions" type="checkbox"><span><strong>轮询 B 站评论区 @ 我的通知</strong><small>每 5 分钟检查新 @ 通知与追评；首次开启仅建立基线，不回读旧通知</small></span></label>
            </div>
            <p id="bili-mention-state" class="bili-mention-line" role="status">B站@通知监听默认关闭。</p>
          </section>
        </div>

        <section class="bili-card">
          <div class="bili-card-head">
            <div>
              <h3>视频读取维度与资源限额</h3>
              <p class="bili-card-sub">单次阅读窗口、采样关键帧、评论分页与本地缓存配额设置。</p>
            </div>
          </div>
          <div class="bili-switch-grid">
            <label class="bili-switch-item"><input id="bili-visual" type="checkbox"><span><strong>读取少量真实画面</strong><small>按关键帧抽取画面尝试理解视频内容</small></span></label>
            <label class="bili-switch-item"><input id="bili-comments" type="checkbox"><span><strong>读取有限评论</strong><small>结合热评与楼中楼把握讨论风向</small></span></label>
            <label class="bili-switch-item"><input id="bili-danmaku" type="checkbox"><span><strong>读取有限弹幕</strong><small>采集视频时间轴实时弹幕反应</small></span></label>
            <label class="bili-switch-item"><input id="bili-media" type="checkbox"><span><strong>按需使用轻量 FFmpeg</strong><small>合法媒体可取得时辅助拆帧采样</small></span></label>
          </div>
          <div class="bili-num-grid">
            <label class="bili-field"><span class="bili-field-label">有效评论最多</span><span class="bili-input-unit"><input id="bili-comment-limit" type="number" min="20" max="80"><span>条</span></span></label>
            <label class="bili-field"><span class="bili-field-label">评论最多分页</span><span class="bili-input-unit"><input id="bili-comment-pages" type="number" min="1" max="5"><span>页</span></span></label>
            <label class="bili-field"><span class="bili-field-label">楼中楼最多展开</span><span class="bili-input-unit"><input id="bili-replies" type="number" min="0" max="5"><span>组</span></span></label>
            <label class="bili-field"><span class="bili-field-label">关键帧最多</span><span class="bili-input-unit"><input id="bili-frames" type="number" min="6" max="24"><span>帧</span></span></label>
            <label class="bili-field"><span class="bili-field-label">单次阅读窗口</span><span class="bili-input-unit"><input id="bili-segment" type="number" min="60" max="600"><span>秒</span></span></label>
            <label class="bili-field"><span class="bili-field-label">探索机会间隔</span><span class="bili-input-unit"><input id="bili-interval" type="number" min="30" max="1440"><span>分钟</span></span></label>
            <label class="bili-field"><span class="bili-field-label">24小时请求上限</span><span class="bili-input-unit"><input id="bili-requests" type="number" min="20" max="5000"><span>次</span></span></label>
            <label class="bili-field"><span class="bili-field-label">画面缓存上限</span><span class="bili-input-unit"><input id="bili-cache" type="number" min="8" max="256"><span>MiB</span></span></label>
            <label class="bili-field"><span class="bili-field-label">画面缓存保留</span><span class="bili-input-unit"><input id="bili-days" type="number" min="1" max="30"><span>天</span></span></label>
          </div>
        </section>

        <section class="bili-card">
          <div class="bili-card-head">
            <div>
              <h3>允许的探索来源与兴趣提示</h3>
              <p class="bili-card-sub">匿名可使用推荐、热门、榜单与搜索；关注动态、收藏与历史需要已登录账号。</p>
            </div>
          </div>
          <div id="bili-feeds" class="bili-feeds-grid"></div>
          <label class="bili-field">
            <span class="bili-field-label">起始兴趣提示（一行一个，可留空）</span>
            <textarea id="bili-seeds" rows="3" placeholder="例如：独立游戏评测&#10;科普动画"></textarea>
          </label>
          <label class="bili-field">
            <span class="bili-field-label">自主刷视频屏蔽题材 / 板块（一行一个，命中标题、简介、分区或 UP 主名时跳过；不影响微信点名直读）</span>
            <textarea id="bili-excluded-topics" rows="3" placeholder="例如：恐怖&#10;克苏鲁&#10;鸣潮"></textarea>
          </label>
          <details class="bili-fold bili-rule-note">
            <summary>隔离与学习边界说明</summary>
            <p class="hint">B站与贴吧共用互联网兴趣、反思和成长认识；来源内容默认与微信私人事实隔离，情境表达在「兴趣与成长」页独立控制。空闲时才触发后台探索，动态帧、字幕窗口、评论及未覆盖部分如实留存，不冒充完整观影；不设累计 Token 限额，退出登录不会自动解除风控暂停。</p>
          </details>
        </section>
      </section>

      <section id="bili-panel-watch" class="bili-tabpanel" role="tabpanel" aria-labelledby="bili-tab-watch" data-bili-panel="watch" hidden>
        <div class="bili-inline-summary">
          <div>
            <p id="bili-watch-mode-line" class="bili-inline-status" role="status">正在读取三游版本排期与追更状态…</p>
            <p class="hint">按北京时间自动根据 42 天周期切换「高峰蹲守 / 黄金整点踩点 / 平时省额」，捕获官号新版动态后自动校准周期基准日。</p>
          </div>
          <button id="bili-watch-probe" type="button" class="secondary">立即追更探测一次</button>
        </div>

        <div id="bili-watch-calendars" class="bili-watch-cal-grid"></div>

        <section class="bili-card">
          <div class="bili-card-head">
            <div>
              <h3>主动推群与四层防剧透保护</h3>
              <p class="hint">仅对以下勾选的微信群主动发送官宣立绘与 PV 开聊；「剧情总结 / 主线解析」条目物理禁止群发。群聊内随时说出「别剧透 / 还没过主线」会自动开启避剧透刹车锁。</p>
            </div>
            <label class="bili-switch">
              <input id="bili-watch-enabled" type="checkbox">
              <span>启用三游追更</span>
            </label>
          </div>
          <div class="bili-num-grid">
            <label>群主动推送冷却 / 分钟<input id="bili-watch-cooldown" type="number" min="0" max="720"></label>
            <label>防剧透刹车锁时长 / 分钟<input id="bili-watch-lock-minutes" type="number" min="15" max="1440"></label>
          </div>
          <div class="bili-block-label">
            <span class="bili-label-title">允许接收官宣立绘 / PV 主动推送的群聊（默认不勾选，请按需勾选）</span>
            <div id="bili-watch-chats" class="bili-watch-chat-grid"></div>
          </div>
          <div class="bili-btn-row">
            <button id="bili-watch-save" type="button" class="primary">保存追更与防剧透配置</button>
          </div>
        </section>

        <section class="bili-card">
          <div class="bili-card-head">
            <div>
              <h3>追更 B 站账号与总结 UP 主</h3>
              <p class="hint">已预置三游官号，前瞻或版本更新时还会自动搜 3~25 分钟高赞总结短片；也可手动添加常看的攻略/前瞻/剧情总结 UP 主。</p>
            </div>
          </div>
          <div class="bili-watch-add-row">
            <input id="bili-watch-add-mid" type="text" placeholder="输入 UP 主数字 UID（如 12345678）">
            <input id="bili-watch-add-name" type="text" placeholder="备注名称（可选，留空自动查昵称）">
            <select id="bili-watch-add-game" aria-label="对应游戏">
              <option value="genshin">原神</option>
              <option value="starrail">崩坏：星穹铁道</option>
              <option value="zzz">绝区零</option>
              <option value="all">三游通用总结 UP</option>
            </select>
            <button id="bili-watch-add-btn" type="button" class="secondary">添加到追更列表</button>
          </div>
          <div id="bili-watch-targets" class="bili-watch-target-list"></div>
        </section>

        <section class="bili-card">
          <div class="bili-card-head">
            <div>
              <h3>群聊避剧透保护锁</h3>
              <p class="hint">群友在群里说过“别剧透/还没过主线”后生效，锁定期物理屏蔽剧情事实与剧透联网搜索，到期或管理员可手动解锁。</p>
            </div>
          </div>
          <div id="bili-watch-locks" class="bili-list"></div>
        </section>

        <section class="bili-card">
          <div class="bili-card-head">
            <div>
              <h3>捕获动态与剧透分层审阅</h3>
              <p class="hint">每条动态在入库时即硬性拆分为「无剧透表层情报」与「隔离剧透要素」（后者折叠显示防误看）。</p>
            </div>
          </div>
          <div id="bili-watch-items" class="bili-list"></div>
        </section>
      </section>
    </div>

    <dialog id="bili-reading-detail" class="tieba-archive-dialog bili-dialog">
      <div class="section-top">
        <h3 id="bili-detail-title">作品与理解范围</h3>
        <button id="bili-detail-close" class="secondary" type="button">关闭</button>
      </div>
      <div id="bili-detail-body"></div>
    </dialog>
    <dialog id="bili-insight-editor" class="tieba-archive-dialog bili-dialog">
      <div class="section-top">
        <h3>编辑B站来源认识</h3>
        <button id="bili-insight-cancel-top" class="secondary" type="button">关闭</button>
      </div>
      <p class="hint">编辑统一记忆；学习、微信使用与锁定权限在“兴趣与成长”页调整。</p>
      <textarea id="bili-insight-content" maxlength="700" rows="5"></textarea>
      <div class="bili-btn-row">
        <button id="bili-insight-cancel" class="secondary" type="button">取消</button>
        <button id="bili-insight-save" class="primary" type="button">保存认识</button>
      </div>
    </dialog>`;
  host.append(panel);

  const feeds = {
    recommend: '推荐内容',
    popular: '热门内容（播放与点赞）',
    ranking: '全站热榜（热议爆款）',
    weekly: '每周必看（近期出圈）',
    search: '按兴趣搜索',
    following: '关注动态',
    favorites: '自己的收藏',
    history: '自己的历史'
  };
  const states = {
    anonymous: '匿名模式',
    unconfirmed: '登录待核验',
    logged_in: '已登录',
    expired: '登录已失效，请重新扫码',
    credential_error: '本地登录状态需要重新授权'
  };
  const qrStates = {
    waiting_scan: '请用 B 站手机客户端扫码',
    waiting_confirm: '已扫码，请在手机上确认',
    expired: '二维码已过期，请重新点击「扫码登录B站」',
    logged_in: '登录已保存到服务器'
  };
  const readingMeta = {
    reading: { label: '读取中', tone: 'info' },
    done: { label: '已理解部分材料', tone: 'ok' },
    material_only: { label: '仅取得材料', tone: 'warn' },
    unconfirmed: { label: '材料不足', tone: 'warn' },
    failed: { label: '未完成', tone: 'danger' },
    interrupted: { label: '已中止', tone: 'danger' }
  };
  const replyMeta = {
    queued: { label: '排队深看中', tone: 'info', group: 'active' },
    reading: { label: '深看视频中', tone: 'info', group: 'active' },
    sending: { label: '正在发送', tone: 'info', group: 'active' },
    uncertain: { label: '结果待核对', tone: 'warn', group: 'active' },
    drafted: { label: '待审草稿', tone: 'warn', group: 'drafted' },
    sent: { label: '已回复', tone: 'ok', group: 'sent' },
    ignored: { label: '已忽略', tone: 'muted', group: 'ignored' },
    failed: { label: '失败', tone: 'danger', group: 'failed' }
  };
  const filterDefs = [
    ['all', '全部'],
    ['active', '排队 / 深看'],
    ['drafted', '待审草稿'],
    ['sent', '已回复'],
    ['ignored', '已忽略'],
    ['failed', '失败']
  ];
  const numericSettingIds = [
    'bili-interval', 'bili-requests', 'bili-cache', 'bili-days',
    'bili-comment-limit', 'bili-comment-pages', 'bili-replies', 'bili-frames',
    'bili-segment', 'bili-reply-deep-min', 'bili-reply-deep-win', 'bili-reply-per-day',
    'bili-reply-interval', 'bili-reply-thread-cap', 'bili-reply-age',
    'bili-reply-retention-limit', 'bili-reply-retention-days'
  ];

  let loading = false;
  let lastFetch = 0;
  let poll = null;
  let qrObject = '';
  let current = null;
  let lastAlert = '';
  let editingInsight = null;
  let directoryLoaded = false;
  let directoryLoading = false;
  let lastLookupResult = null;
  let lastSavePromise = null;
  let directoryData = { allowed_users: [], special_users: [], followings: [], recent_senders: [], known_names: {} };
  const draftInputs = new Map();

  const TAB_STORAGE_KEY = 'whalegirl-bili-tab';
  const tabButtons = [...panel.querySelectorAll('[data-bili-tab]')];
  const tabPanels = [...panel.querySelectorAll('[data-bili-panel]')];
  let activeTab = (() => {
    try {
      const saved = sessionStorage.getItem(TAB_STORAGE_KEY);
      if (['replies', 'policy', 'explore', 'watch', 'settings'].includes(saved)) return saved;
    } catch {}
    return 'replies';
  })();

  const visible = () => panel.open && !panel.closest('[hidden]') && (!host.contains(panel) || host.open);
  const node = (tag, cls = '', value = '') => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (value !== '') n.textContent = value;
    return n;
  };
  const pill = (text, tone = 'muted') => {
    const el = node('span', 'bili-pill', text);
    el.dataset.tone = tone;
    return el;
  };
  const when = value => value
    ? new Date(value * 1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
    : '尚未安排';
  const shortWhen = value => value
    ? new Date(value * 1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
    : '未安排';
  function button(label, fn, cls = 'secondary') {
    const b = node('button', cls, label);
    b.type = 'button';
    b.onclick = () => action(b, fn);
    return b;
  }

  function switchTab(nextTab, focusButton = false) {
    if (!['replies', 'policy', 'explore', 'watch', 'settings'].includes(nextTab)) nextTab = 'replies';
    activeTab = nextTab;
    try { sessionStorage.setItem(TAB_STORAGE_KEY, nextTab); } catch {}
    for (const tabBtn of tabButtons) {
      const on = tabBtn.dataset.biliTab === nextTab;
      tabBtn.classList.toggle('is-active', on);
      tabBtn.setAttribute('aria-selected', String(on));
      tabBtn.tabIndex = on ? 0 : -1;
      if (on && focusButton) tabBtn.focus();
    }
    for (const tabPanel of tabPanels) {
      tabPanel.hidden = tabPanel.dataset.biliPanel !== nextTab;
    }
  }
  switchTab(activeTab);

  for (const tabBtn of tabButtons) {
    tabBtn.addEventListener('click', () => switchTab(tabBtn.dataset.biliTab));
    tabBtn.addEventListener('keydown', event => {
      const index = tabButtons.indexOf(tabBtn);
      let next = -1;
      if (event.key === 'ArrowRight') next = (index + 1) % tabButtons.length;
      else if (event.key === 'ArrowLeft') next = (index - 1 + tabButtons.length) % tabButtons.length;
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = tabButtons.length - 1;
      if (next >= 0) {
        event.preventDefault();
        switchTab(tabButtons[next].dataset.biliTab, true);
      }
    });
  }

  panel.addEventListener('click', event => {
    const jump = event.target.closest('[data-jump-tab]');
    if (!jump) return;
    switchTab(jump.dataset.jumpTab);
    if (jump.dataset.jumpFilter && jump.dataset.jumpTab === 'replies') {
      const select = $('bili-replies-filter');
      if (select && select.value !== jump.dataset.jumpFilter) {
        select.value = jump.dataset.jumpFilter;
        select.dispatchEvent(new Event('change'));
      }
    }
  });
  $('bili-account-pill').addEventListener('click', () => switchTab('settings'));
  $('bili-goto-login').addEventListener('click', () => {
    switchTab('settings');
    $('bili-login')?.focus();
  });

  const extractMid = line => {
    const m = String(line || '').trim().match(/^([1-9]\d{0,19})(?=$|\s|#)/);
    return m ? m[1] : '';
  };
  function parseMids() {
    const ordered = [], set = new Set(), lines = [];
    for (const raw of $('bili-reply-mids').value.split(/\r?\n/)) {
      const line = raw.trim();
      if (!line) continue;
      const mid = extractMid(line);
      if (mid) {
        if (set.has(mid)) continue;
        set.add(mid);
        ordered.push(mid);
      }
      lines.push(line);
    }
    return { ordered, set, lines };
  }
  function mergeDirectory(payload) {
    if (!payload || typeof payload !== 'object') return;
    const names = { ...(directoryData.known_names || {}), ...(payload.known_names || {}) };
    for (const listKey of ['allowed_users', 'special_users', 'followings', 'recent_senders']) {
      if (Array.isArray(payload[listKey])) {
        directoryData[listKey] = payload[listKey];
        for (const item of payload[listKey]) {
          const mid = extractMid(item?.mid);
          if (mid && item?.name) names[mid] = String(item.name);
        }
      }
    }
    directoryData.known_names = names;
  }
  function nameForMid(mid, fallback = '') {
    const clean = extractMid(mid);
    if (!clean) return fallback;
    if (directoryData.known_names?.[clean]) return directoryData.known_names[clean];
    for (const listKey of ['followings', 'recent_senders', 'allowed_users', 'special_users']) {
      const found = (directoryData[listKey] || []).find(item => extractMid(item?.mid) === clean && item?.name);
      if (found) return String(found.name);
    }
    return fallback;
  }
  function setMidAllowed(mid, allow, name = '') {
    const clean = extractMid(mid);
    if (!clean) return;
    const cleanName = String(name || '').replace(/[\r\n#]+/g, ' ').trim();
    if (cleanName) directoryData.known_names = { ...(directoryData.known_names || {}), [clean]: cleanName };
    const nextLines = $('bili-reply-mids').value.split(/\r?\n/).map(v => v.trim()).filter(v => v && extractMid(v) !== clean);
    if (allow) nextLines.push(cleanName ? `${clean} # ${cleanName}` : clean);
    const area = $('bili-reply-mids');
    area.value = nextLines.join('\n');
    area.dispatchEvent(new Event('input', { bubbles: true }));
    area.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function renderUserRow(item, { manualSet, specialSet, sourceBadge = '' }) {
    const mid = extractMid(item?.mid);
    const label = document.createElement('label');
    label.className = 'bili-pick-item';
    const main = node('div', 'bili-pick-main');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = manualSet.has(mid);
    cb.onchange = () => setMidAllowed(mid, cb.checked, item?.name || nameForMid(mid));
    const copy = node('div', 'bili-pick-copy');
    const title = node('div', 'bili-pick-title', item?.name || nameForMid(mid) || ('UID ' + mid));
    if (item?.special || specialSet.has(mid)) title.append(node('span', 'bili-badge', '特别关注'));
    if (sourceBadge) title.append(node('span', 'bili-badge', sourceBadge));
    const parts = ['UID ' + mid];
    if (item?.count) parts.push(`@过 ${item.count} 次`);
    if (item?.last_title) parts.push(item.last_title);
    else if (item?.sign) parts.push(item.sign);
    copy.append(title, node('div', 'bili-pick-sub', parts.join(' · ')));
    main.append(cb, copy);
    label.append(main);
    return label;
  }

  function renderLookup(res) {
    const box = $('bili-lookup-results');
    if (!res) {
      box.hidden = true;
      box.replaceChildren();
      return;
    }
    const { set: manualSet } = parseMids();
    const specialSet = new Set((directoryData.special_users || []).map(u => extractMid(u?.mid)).filter(Boolean));
    const candidates = Array.isArray(res.candidates) ? res.candidates : [];
    if (!candidates.length && !res.warning) {
      box.hidden = true;
      box.replaceChildren();
      return;
    }
    box.hidden = false;
    box.replaceChildren();
    if (res.warning) box.append(node('p', 'hint', '查询提示：' + res.warning));
    const sourceLabels = { uid: 'UID 查询', following: '我的关注', recent: '最近@过', search: 'B站搜索' };
    for (const item of candidates) {
      box.append(renderUserRow(item, { manualSet, specialSet, sourceBadge: sourceLabels[item.source] || '' }));
    }
    if (!candidates.length) {
      box.append(node('p', 'hint', '没有找到匹配的 B 站用户，可以直接输入纯数字 UID 或个人主页链接添加。'));
    }
  }

  function renderPickers() {
    const scope = $('bili-reply-scope').value || 'all';
    const titleEl = $('bili-picker-title');
    const hintEl = $('bili-picker-scope-hint');
    const pickerCard = $('bili-allowed-picker');
    if (pickerCard) pickerCard.dataset.scopeMode = scope;
    if (titleEl) {
      titleEl.textContent = scope === 'all'
        ? '备选白名单与特别关注（切回「仅白名单触发」时生效）'
        : '允许触发回复的人（白名单与特别关注生效中）';
    }
    if (hintEl) {
      hintEl.textContent = scope === 'all'
        ? '当前为「任何人可触发」，下方名单暂作备选保存'
        : '当前仅下方勾选成员与特别关注可触发，调整后点击「保存设置」生效';
    }

    const { ordered: manualOrdered, set: manualSet } = parseMids();
    const specialEnabled = $('bili-reply-special').checked;
    const specialUsers = Array.isArray(directoryData.special_users) ? directoryData.special_users : [];
    const specialSet = new Set(specialUsers.map(u => extractMid(u?.mid)).filter(Boolean));
    const chips = $('bili-allowed-chips');
    chips.replaceChildren();

    for (const mid of manualOrdered) {
      const uname = nameForMid(mid);
      const chip = node('span', 'bili-chip');
      chip.append(node('span', '', uname ? `${uname}（${mid}）` : `UID ${mid}`));
      if (specialSet.has(mid)) chip.append(node('span', 'bili-badge', '特别关注'));
      const x = node('button', 'bili-chip-x', '×');
      x.type = 'button';
      x.title = '从白名单移除';
      x.setAttribute('aria-label', `移除 ${uname || ('UID ' + mid)}`);
      x.onclick = () => setMidAllowed(mid, false);
      chip.append(x);
      chips.append(chip);
    }
    if (specialEnabled) {
      for (const u of specialUsers) {
        const mid = extractMid(u?.mid);
        if (!mid || manualSet.has(mid)) continue;
        const uname = u.name || nameForMid(mid);
        const chip = node('span', 'bili-chip special');
        chip.append(node('span', '', uname ? `${uname}（${mid}）` : `UID ${mid}`), node('span', 'bili-badge', '特别关注自动允许'));
        chips.append(chip);
      }
    }
    if (!chips.children.length) {
      chips.append(node('span', 'hint', scope === 'all'
        ? '当前已允许任何人 @ 或回复我；若后续切回「仅白名单触发」，将使用此处选中的白名单。'
        : '尚未选择允许回复的人；可直接在下方勾选好友、搜索 UID/昵称添加，或开启“同步特别关注”。'));
    }

    const policyBadge = $('bili-tab-badge-policy');
    if (policyBadge) {
      policyBadge.textContent = scope === 'all' ? '全局' : `白名单 ${manualOrdered.length}`;
    }

    const needle = ($('bili-picker-filter').value || '').trim().toLowerCase();
    const matchNeedle = item => {
      if (!needle) return true;
      const mid = extractMid(item?.mid);
      const uname = (item?.name || nameForMid(mid) || '').toLowerCase();
      const extra = (item?.sign || item?.last_title || '').toLowerCase();
      return mid.includes(needle) || uname.includes(needle) || extra.includes(needle);
    };

    const fBox = $('bili-following-list');
    const fScroll = fBox.scrollTop;
    const followings = Array.isArray(directoryData.followings) ? directoryData.followings : [];
    const filteredFollowings = followings.filter(matchNeedle);
    $('bili-following-count').textContent = followings.length ? (needle ? `${filteredFollowings.length} / ${followings.length} 人` : `共 ${followings.length} 人`) : '';
    fBox.replaceChildren();
    for (const item of filteredFollowings) fBox.append(renderUserRow(item, { manualSet, specialSet }));
    if (!followings.length) {
      fBox.append(node('p', 'hint', current?.account?.state === 'logged_in'
        ? '暂无关注记录，可点右上方「刷新关注与候选人」同步，或直接在上方输入 UID/昵称查找。'
        : '登录后可自动拉取关注列表，也可直接在上方输入 UID 添加。'));
    } else if (!filteredFollowings.length) {
      fBox.append(node('p', 'hint', '没有符合筛选条件的关注好友。'));
    }
    fBox.scrollTop = fScroll;

    const rBox = $('bili-recent-senders');
    const rScroll = rBox.scrollTop;
    const recent = Array.isArray(directoryData.recent_senders) ? directoryData.recent_senders : [];
    const filteredRecent = recent.filter(matchNeedle);
    rBox.replaceChildren();
    for (const item of filteredRecent) {
      rBox.append(renderUserRow(item, {
        manualSet,
        specialSet,
        sourceBadge: item.allowed ? '已在白名单' : item.special ? '特别关注' : '未在白名单'
      }));
    }
    if (!recent.length) rBox.append(node('p', 'hint', '暂无评论区 @ 记录。'));
    else if (!filteredRecent.length) rBox.append(node('p', 'hint', '没有符合筛选条件的 @ 记录。'));
    rBox.scrollTop = rScroll;

    if (!$('bili-lookup-results').hidden && lastLookupResult) renderLookup(lastLookupResult);
  }

  function renderFilterPills(all) {
    const hostPills = $('bili-replies-pills');
    const currentFilter = $('bili-replies-filter').value || 'all';
    const counts = { all: all.length, active: 0, drafted: 0, sent: 0, ignored: 0, failed: 0 };
    for (const item of all) {
      const meta = replyMeta[item.state];
      if (meta && counts[meta.group] != null) counts[meta.group]++;
    }
    hostPills.replaceChildren();
    for (const [key, label] of filterDefs) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'bili-filter-pill';
      btn.dataset.filter = key;
      btn.setAttribute('aria-pressed', String(currentFilter === key));
      btn.append(document.createTextNode(label + ' '), node('span', 'bili-filter-count', String(counts[key] || 0)));
      btn.onclick = () => {
        $('bili-replies-filter').value = key;
        $('bili-replies-filter').dispatchEvent(new Event('change'));
      };
      hostPills.append(btn);
    }

    const pendingCount = counts.active + counts.drafted;
    const repliesBadge = $('bili-tab-badge-replies');
    if (repliesBadge) {
      repliesBadge.hidden = pendingCount === 0;
      repliesBadge.textContent = String(pendingCount);
      repliesBadge.dataset.tone = counts.drafted > 0 ? 'warn' : 'info';
    }
  }

  function renderReplies(items) {
    const all = Array.isArray(items) ? items : [];
    const aliveIds = new Set(all.map(item => item && item.id).filter(Boolean));
    for (const key of draftInputs.keys()) {
      if (!aliveIds.has(key)) draftInputs.delete(key);
    }
    renderFilterPills(all);

    const filter = $('bili-replies-filter').value || 'all';
    const needle = ($('bili-replies-search').value || '').trim().toLowerCase();
    const shown = all.filter(item => {
      const meta = replyMeta[item.state] || { group: item.state };
      if (filter !== 'all' && meta.group !== filter) return false;
      if (!needle) return true;
      const senderMid = extractMid(item.sender_mid);
      const senderName = item.sender_name || nameForMid(senderMid);
      const hay = [
        item.title, item.bvid, senderMid, senderName,
        item.mention_text, item.target_text, item.reply_text,
        item.reason, item.error, item.reply_rpid
      ].filter(Boolean).join('\n').toLowerCase();
      return hay.includes(needle);
    });

    $('bili-replies-count').textContent = shown.length !== all.length ? `${shown.length} / ${all.length}` : String(all.length);
    const rs = current?.reply_monitor || {};
    const capLimit = rs.retention_limit || cfg?.bilibili_reply_retention_limit || 20;
    const capDays = rs.retention_days || cfg?.bilibili_reply_retention_days || 7;
    const capHint = $('bili-replies-cap-hint');
    if (capHint) capHint.textContent = ` · 上限 ${capLimit} 条/${capDays} 天`;
    const replyList = $('bili-replies-list');
    replyList.replaceChildren();
    const { set: manualSet } = parseMids();
    const specialEnabled = $('bili-reply-special').checked;
    const specialSet = new Set((directoryData.special_users || []).map(u => extractMid(u?.mid)).filter(Boolean));

    for (const item of shown) {
      const meta = replyMeta[item.state] || { label: item.state, tone: 'muted' };
      const isReply = item.trigger_kind === 'reply';
      const senderMid = extractMid(item.sender_mid);
      const senderName = item.sender_name || nameForMid(senderMid);

      const card = node('article', 'bili-reply-card');
      card.dataset.state = item.state;

      const head = node('div', 'bili-reply-head');
      const titleWrap = node('div', 'bili-reply-title-wrap');
      const link = node('a', 'bili-video-link', item.title || item.bvid || ('av' + item.oid));
      if (item.url) {
        link.href = item.url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
      }
      titleWrap.append(link);
      const headBadges = node('div', 'bili-badge-group');
      headBadges.append(pill(isReply ? '追评回复' : '@ 提及', isReply ? 'info' : 'muted'));
      headBadges.append(pill(meta.label, meta.tone));
      head.append(titleWrap, headBadges);

      const metaRow = node('div', 'bili-reply-meta');
      const authorEl = node('span', 'bili-reply-author', senderName || (senderMid ? 'UID ' + senderMid : '未知用户'));
      metaRow.append(authorEl);
      if (senderMid) metaRow.append(node('code', 'bili-mono-tag', 'UID ' + senderMid));
      metaRow.append(node('span', 'bili-meta-sep', '·'), node('span', '', when(item.created)));
      if (item.reply_rpid) metaRow.append(node('span', 'bili-meta-sep', '·'), node('code', 'bili-mono-tag', 'rpid ' + item.reply_rpid));
      card.append(head, metaRow);

      const quotes = node('div', 'bili-quote-stack');
      if (item.target_text) {
        const orig = node('div', 'bili-quote bili-quote-original');
        orig.append(node('span', 'bili-quote-tag', '我的原评'), node('p', 'message', item.target_text));
        quotes.append(orig);
      }
      const trig = node('div', 'bili-quote bili-quote-trigger');
      trig.append(node('span', 'bili-quote-tag', isReply ? '对方追评' : '@ 原话'), node('p', 'message', item.mention_text || ''));
      quotes.append(trig);
      card.append(quotes);

      if (item.reading_summary) {
        const ev = node('div', 'bili-evidence-line');
        ev.append(node('span', 'bili-quote-tag', '看片依据'), node('span', '', item.reading_summary));
        card.append(ev);
      }

      if (['drafted', 'failed'].includes(item.state)) {
        const editBox = node('div', 'bili-draft-box');
        const editHead = node('div', 'bili-draft-head');
        const box = document.createElement('textarea');
        box.rows = 2;
        box.maxLength = 500;
        box.value = draftInputs.has(item.id) ? draftInputs.get(item.id) : (item.reply_text || '');
        box.setAttribute('aria-label', '评论回复内容');
        const counter = node('span', 'bili-char-counter', `${box.value.length} / 500`);
        box.addEventListener('input', () => {
          draftInputs.set(item.id, box.value);
          counter.textContent = `${box.value.length} / 500`;
        });
        editHead.append(node('span', 'bili-quote-tag', item.state === 'failed' ? '重试回复内容' : '待发草稿内容'), counter);
        const actions = node('div', 'bili-btn-row');
        actions.append(
          button('发送评论', async () => {
            await api('bilibili/replies/' + item.id, 'POST', { action: 'send', text: box.value });
            draftInputs.delete(item.id);
            await refresh();
          }, 'primary'),
          button('忽略', async () => {
            await api('bilibili/replies/' + item.id, 'POST', { action: 'ignore' });
            draftInputs.delete(item.id);
            await refresh();
          }, 'secondary')
        );
        if (item.state === 'failed') {
          actions.append(button('重新深看', async () => {
            await api('bilibili/replies/' + item.id, 'POST', { action: 'requeue' });
            draftInputs.delete(item.id);
            await refresh();
          }, 'secondary'));
        }
        editBox.append(editHead, box, actions);
        card.append(editBox);
      } else {
        if (item.reply_text) {
          const rep = node('div', 'bili-quote bili-quote-reply');
          rep.append(node('span', 'bili-quote-tag', '大肥鱼回复'), node('p', 'message', item.reply_text));
          card.append(rep);
        }
        if (item.state === 'ignored') {
          const actions = node('div', 'bili-btn-row');
          actions.append(button('重新深看', async () => {
            await api('bilibili/replies/' + item.id, 'POST', { action: 'requeue' });
            await refresh();
          }, 'secondary'));
          const alreadyAllowed = senderMid && (manualSet.has(senderMid) || (specialEnabled && specialSet.has(senderMid)));
          if (senderMid && !alreadyAllowed) {
            actions.append(button('＋加入白名单并深看', async () => {
              setMidAllowed(senderMid, true, senderName);
              $('bili-save').click();
              if (lastSavePromise) await lastSavePromise;
              const latest = (current?.comment_replies || []).find(r => r.id === item.id);
              if (!latest || ['ignored', 'failed'].includes(latest.state)) {
                await api('bilibili/replies/' + item.id, 'POST', { action: 'requeue' });
                await refresh();
              }
              notice(`已将 ${senderName || ('UID ' + senderMid)} 加入白名单并排队深看`);
            }, 'primary'));
          }
          card.append(actions);
        }
      }

      if (item.reason) {
        const note = node('p', 'bili-card-note');
        note.append(node('strong', '', '处理说明：'), document.createTextNode(item.reason));
        card.append(note);
      }
      if (item.error) {
        const err = node('p', 'bili-card-error');
        err.append(node('strong', '', '状态说明：'), document.createTextNode(item.error));
        card.append(err);
      }
      replyList.append(card);
    }

    if (!shown.length) {
      replyList.append(node('p', 'bili-empty', all.length
        ? '当前筛选条件或关键词下没有匹配的评论 @ / 追评记录。'
        : '暂无评论区 @ / 追评回复记录。'));
    }
  }

  for (const [id, label] of Object.entries(feeds)) {
    const l = node('label', 'bili-feed-item');
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.dataset.feed = id;
    l.append(input, node('span', '', label));
    $('bili-feeds').append(l);
  }

  function updateKPIs(value) {
    const acc = value.account || {};
    const monitor = value.mention_monitor || {};
    const rmon = value.reply_monitor || {};
    const rf = rmon.reply_feed || {};

    $('bili-kpi-sent').textContent = `${rmon.sent_today ?? 0} / ${rmon.daily_limit ?? 0}`;
    $('bili-kpi-drafted').textContent = String(rmon.drafted ?? 0);
    $('bili-kpi-queued').textContent = String(rmon.queued ?? 0);

    const modeEl = $('bili-kpi-mode');
    if (!rmon.enabled) {
      modeEl.textContent = '已关闭';
      modeEl.dataset.tone = 'muted';
    } else {
      modeEl.textContent = rmon.mode === 'review' ? '先审后发' : '自动发送';
      modeEl.dataset.tone = rmon.reply_paused ? 'danger' : 'ok';
    }
    const scopeText = (rmon.scope || 'all') === 'all'
      ? '任何人可触发'
      : `白名单 ${rmon.allowed_mids_count ?? 0} 人` + (rmon.use_special_following ? ` + 特关 ${rmon.special_following_count ?? 0}` : '');
    const replyFeedText = rmon.reply_to_replies_enabled ? '追评开' : '追评关';
    $('bili-kpi-scope').textContent = `${scopeText} · ${replyFeedText}`;

    const monEl = $('bili-kpi-monitor');
    const monSub = $('bili-kpi-monitor-sub');
    const monMap = {
      disabled: ['监听关闭', 'muted'],
      active: [monitor.baseline_ready ? '每5分钟监听中' : '待建立基线', 'ok'],
      login_required: ['等待登录', 'warn'],
      risk_paused: ['访问受限暂停', 'danger'],
      bilibili_disabled: ['总开关关闭', 'muted']
    };
    const [monLabel, monTone] = monMap[monitor.state] || [monitor.state || '未知', 'muted'];
    monEl.textContent = monLabel;
    monEl.dataset.tone = monTone;
    monSub.textContent = monitor.last_check ? `上次检查 ${shortWhen(monitor.last_check)}` : '尚未执行检查';

    $('bili-kpi-reads').textContent = `今日 ${value.reads_today ?? 0} 支`;
    $('bili-kpi-budget').textContent = `24h 请求 ${acc.requests_today ?? 0} / ${acc.request_limit ?? 0} · 下次 ${shortWhen(value.schedule?.next_at)}`;
  }

  function updateAlerts(value) {
    const acc = value.account || {};
    const monitor = value.mention_monitor || {};
    const rmon = value.reply_monitor || {};
    const rf = rmon.reply_feed || {};

    const reasons = [];
    if (acc.paused) reasons.push(`访问限制已暂停（${acc.pause_code || '风控'}）`);
    if (acc.reply_paused || rmon.reply_paused) reasons.push(`评论发送已暂停（${acc.reply_pause_code || rmon.reply_pause_code || '风控'}）`);
    if (acc.state === 'expired' || acc.state === 'credential_error') reasons.push(states[acc.state]);
    if (monitor.last_error) reasons.push(`@监听异常：${monitor.last_error}`);
    if (rf.last_error) reasons.push(`追评异常：${rf.last_error}`);

    const banner = $('bili-alert-banner');
    const copy = $('bili-alert-copy');
    const needLogin = acc.state === 'expired' || acc.state === 'credential_error';
    $('bili-resume').hidden = !acc.paused;
    $('bili-reply-resume').hidden = !(acc.reply_paused || rmon.reply_paused);
    $('bili-goto-login').hidden = !needLogin;

    if (reasons.length) {
      copy.textContent = reasons.join(' · ');
      banner.hidden = false;
    } else {
      copy.textContent = '';
      banner.hidden = true;
    }

    const settingsBadge = $('bili-tab-badge-settings');
    if (settingsBadge) {
      if (acc.paused || acc.reply_paused || needLogin) {
        settingsBadge.hidden = false;
        settingsBadge.textContent = '需处理';
        settingsBadge.dataset.tone = 'danger';
      } else {
        settingsBadge.hidden = true;
      }
    }
  }

  function account(value) {
    const pillEl = $('bili-account-pill');
    const pillText = $('bili-account-pill-text');
    let tone = 'muted';
    if (value.paused || value.reply_paused || value.state === 'expired' || value.state === 'credential_error') tone = 'danger';
    else if (value.state === 'logged_in') tone = 'ok';
    else if (value.state === 'unconfirmed') tone = 'warn';
    pillEl.dataset.tone = tone;
    pillText.textContent = (states[value.state] || value.state) + (value.account_name ? ' · ' + value.account_name : '');

    $('bili-account-state').textContent = (states[value.state] || value.state)
      + (value.account_name ? ' · ' + value.account_name : '')
      + (value.paused ? ' · 访问限制已暂停（' + value.pause_code + '）' : '')
      + (value.reply_paused ? ' · 评论发送暂停（' + value.reply_pause_code + '）' : '')
      + (value.last_check ? ' · 上次核验 ' + when(value.last_check) : '');
    $('bili-account-card-note').textContent = value.state === 'logged_in'
      ? `当前已登录账号「${value.account_name || '已授权'}」，可读取关注动态并监听评论区通知。`
      : '使用个人账号登录后，可访问关注动态、收藏及评论区 @ 与追评通知。';

    $('bili-resume').hidden = !value.paused;
    $('bili-reply-resume').hidden = !value.reply_paused;
    $('bili-login-status').textContent = qrStates[value.qr_state] || '请用 B 站手机客户端扫码并确认';
    if (value.qr_state === 'logged_in' || value.qr_state === 'expired') {
      stopPoll();
      if (value.qr_state === 'logged_in') {
        $('bili-login-box').hidden = true;
        clearQR();
      }
    }
  }

  function populate() {
    if (!cfg) return;
    for (const [id, key] of [
      ['bili-enabled', 'bilibili_enabled'],
      ['bili-wechat-video', 'bilibili_wechat_video_enabled'],
      ['bili-comment-mentions', 'bilibili_comment_mentions_enabled'],
      ['bili-comment-reply', 'bilibili_comment_reply_enabled'],
      ['bili-reply-to-replies', 'bilibili_reply_to_replies_enabled'],
      ['bili-reply-special', 'bilibili_reply_use_special_following'],
      ['bili-reply-record-ignored', 'bilibili_reply_record_ignored'],
      ['bili-visual', 'bilibili_visual_enabled'],
      ['bili-comments', 'bilibili_comments_enabled'],
      ['bili-danmaku', 'bilibili_danmaku_enabled'],
      ['bili-media', 'bilibili_media_enabled']
    ]) $(id).checked = !!cfg[key];

    for (const [id, key, fallback] of [
      ['bili-interval', 'bilibili_interval_minutes', 120],
      ['bili-requests', 'bilibili_requests_per_day', 1200],
      ['bili-cache', 'bilibili_cache_mb', 64],
      ['bili-days', 'bilibili_cache_days', 7],
      ['bili-comment-limit', 'bilibili_comments_limit', 60],
      ['bili-comment-pages', 'bilibili_comment_pages', 4],
      ['bili-replies', 'bilibili_reply_threads', 3],
      ['bili-frames', 'bilibili_keyframes_limit', 16],
      ['bili-segment', 'bilibili_segment_seconds', 300],
      ['bili-reply-deep-min', 'bilibili_reply_deep_max_minutes', 30],
      ['bili-reply-deep-win', 'bilibili_reply_deep_max_windows', 6],
      ['bili-reply-per-day', 'bilibili_reply_per_day', 12],
      ['bili-reply-interval', 'bilibili_reply_min_interval_seconds', 60],
      ['bili-reply-thread-cap', 'bilibili_reply_max_per_thread', 3],
      ['bili-reply-age', 'bilibili_reply_max_age_hours', 24],
      ['bili-reply-retention-limit', 'bilibili_reply_retention_limit', 20],
      ['bili-reply-retention-days', 'bilibili_reply_retention_days', 7]
    ]) $(id).value = cfg[key] ?? fallback;

    $('bili-reply-scope').value = cfg.bilibili_reply_scope || 'all';
    $('bili-reply-mode').value = cfg.bilibili_reply_mode || 'auto';
    $('bili-reply-mids').value = (cfg.bilibili_reply_allowed_sender_mids || []).join('\n');
    $('bili-seeds').value = (cfg.bilibili_interest_seeds || []).join('\n');
    $('bili-excluded-topics').value = (cfg.bilibili_excluded_topics || []).join('\n');
    for (const input of $('bili-feeds').querySelectorAll('input')) {
      input.checked = (cfg.bilibili_feeds || Object.keys(feeds)).includes(input.dataset.feed);
    }
    if (current?.reply_monitor) mergeDirectory(current.reply_monitor);
    renderPickers();
    if (visible()) refresh().catch(error => notice(error.message, true));
  }

  async function authedImage(path) {
    const response = await fetch('/api/' + path, {
      headers: { Authorization: 'Bearer ' + token },
      cache: 'no-store',
      signal: AbortSignal.timeout(15000)
    });
    if (!response.ok) throw new Error('图片暂不可用，请刷新记录');
    return URL.createObjectURL(await response.blob());
  }
  function clearQR() {
    if (qrObject) URL.revokeObjectURL(qrObject);
    qrObject = '';
    $('bili-qr').removeAttribute('src');
  }
  function stopPoll() {
    clearTimeout(poll);
    poll = null;
  }
  function queuePoll() {
    stopPoll();
    if (visible() && !$('bili-login-box').hidden) {
      poll = setTimeout(async () => {
        if (!visible()) return;
        try {
          const value = await api('bilibili/account', 'POST', { action: 'poll' });
          account(value);
          if (!['logged_in', 'expired'].includes(value.qr_state)) queuePoll();
          else await refresh();
        } catch (error) {
          stopPoll();
          $('bili-login-status').textContent = error.message;
          await refresh().catch(() => {});
        }
      }, 3500);
    }
  }

  async function detail(reading) {
    $('bili-detail-title').textContent = reading.metadata.title || reading.bvid;
    const box = $('bili-detail-body');
    box.replaceChildren(node('p', 'hint', when(reading.created)));
    if (reading.coverage?.ephemeral) {
      box.append(node('p', 'hint', '该记录为评论区 @ 深度看片后的轻量观看记录；按策略仅保留观看记录，未留存续看进度、分镜缓存与原文细节。'));
    }
    box.append(node('pre', 'message', JSON.stringify(reading.coverage, null, 2)));
    $('bili-reading-detail').showModal();
    if (reading.journal_id) {
      const journal = await api('growth/journals/' + reading.journal_id);
      for (const [key, label] of Object.entries({
        encounter: '接触到的具体内容',
        facts: '材料事实',
        author_view: '作者观点',
        inference: '自己的推测',
        revision: '理解的修正',
        missing: '没有覆盖的部分'
      })) {
        if (journal.body[key]) box.append(node('h3', '', label), node('p', 'message', journal.body[key]));
      }
      for (const source of journal.sources) {
        box.append(node('h3', '', '实际保存的来源片段'), node('pre', 'message', source.body));
      }
    } else if (reading.source_id) {
      const source = await api('growth/sources/' + reading.source_id);
      box.append(node('h3', '', '实际读取材料（未强行形成正式心得）'), node('pre', 'message', source.body));
    }
    for (let index = 0; index < Math.min(2, reading.coverage.board_count || 0); index++) {
      try {
        const url = await authedImage('bilibili/readings/' + reading.id + '/boards/' + index);
        const picture = document.createElement('img');
        picture.className = 'bili-board';
        picture.src = url;
        picture.alt = '带格号与时间点的实际分镜';
        picture.onload = () => URL.revokeObjectURL(url);
        box.append(picture);
      } catch {
        box.append(node('p', 'hint', '分镜缓存已过期，文字证据和覆盖记录仍保留'));
      }
    }
    const times = reading.coverage.sampled_frame_times || [];
    for (let index = 0; index < Math.min(24, times.length); index++) {
      try {
        const url = await authedImage('bilibili/readings/' + reading.id + '/frames/' + index);
        const image = document.createElement('img');
        image.src = url;
        image.alt = '实际选帧 ' + times[index] + '秒';
        image.onload = () => URL.revokeObjectURL(url);
        box.append(image);
      } catch {
        box.append(node('p', 'hint', '快照缓存已过期，来源与理解记录仍保留'));
        break;
      }
    }
  }

  async function syncDirectory(refreshRemote = false) {
    if (directoryLoading && !refreshRemote) return;
    directoryLoading = true;
    try {
      const dir = await api('bilibili/reply-directory' + (refreshRemote ? '?refresh=true' : ''));
      directoryLoaded = true;
      mergeDirectory(dir);
      renderPickers();
      if (current?.comment_replies) renderReplies(current.comment_replies);
      return dir;
    } finally {
      directoryLoading = false;
    }
  }

  async function refresh() {
    if (loading) return;
    loading = true;
    try {
      const value = await api('bilibili');
      current = value;
      account(value.account);
      updateKPIs(value);
      updateAlerts(value);
      lastFetch = Date.now();

      const monitor = value.mention_monitor || {};
      const labels = {
        disabled: 'B站@通知监听关闭',
        active: monitor.baseline_ready ? 'B站@通知监听中 · 每5分钟检查' : 'B站@通知已启用 · 首次检查将建立基线',
        login_required: 'B站@通知等待有效登录',
        risk_paused: 'B站@通知已因访问限制暂停',
        bilibili_disabled: '请先开启B站总开关'
      };
      $('bili-mention-state').textContent = (labels[monitor.state] || 'B站@通知状态：' + (monitor.state || '未知'))
        + (monitor.last_check ? ' · 上次检查 ' + when(monitor.last_check) : '')
        + (monitor.last_error ? ' · ' + monitor.last_error : '');

      const rmon = value.reply_monitor || {};
      const rf = rmon.reply_feed || {};
      const scopeLabel = (rmon.scope || 'all') === 'all'
        ? '触发范围：任何人'
        : ('触发范围：白名单 ' + rmon.allowed_mids_count + ' 人' + (rmon.use_special_following ? ' + 特别关注 ' + rmon.special_following_count + ' 人' : ''));
      const replyFeedLabel = rmon.reply_to_replies_enabled
        ? (' · 追评回复：开启' + (rf.baseline_ready === false ? '（待建基线）' : ''))
        : ' · 追评回复：关闭';
      $('bili-reply-state').textContent = rmon.enabled
        ? ('评论回复已开启（' + (rmon.mode === 'review' ? '先审后发' : '自动发送') + '）· ' + scopeLabel + replyFeedLabel + ' · 今日已发 ' + rmon.sent_today + '/' + rmon.daily_limit + ' · 排队 ' + rmon.queued + ' · 待审 ' + rmon.drafted + (rmon.reply_paused ? ' · 写入风控暂停（' + rmon.reply_pause_code + '）' : '') + (rf.last_error ? ' · 追评异常：' + rf.last_error : ''))
        : '评论区 @ 与追评回复关闭';

      mergeDirectory(rmon);
      renderPickers();
      renderReplies(value.comment_replies || []);
      if (value.account?.state === 'logged_in' && !directoryLoaded && !directoryLoading && !(rmon.followings || []).length) {
        directoryLoaded = true;
        syncDirectory(false).catch(() => {});
      }

      const tasks = value.tasks || [];
      window.growthConsole?.taskRows($('bili-tasks'), tasks);
      $('bili-tasks-count').textContent = String(tasks.length);
      $('bili-usage').textContent = '今日已探索视频 ' + value.reads_today + ' · 不设视频数量上限 · 24小时请求 ' + value.account.requests_today + '/' + value.account.request_limit + ' · 下次机会 ' + when(value.schedule.next_at);

      const interests = $('bili-interests');
      interests.replaceChildren();
      const interestList = value.interests || [];
      $('bili-interests-count').textContent = String(interestList.length);
      for (const item of interestList) {
        const card = node('article', 'bili-item-card');
        card.append(
          node('strong', 'bili-item-title', item.topic),
          node('p', 'message', item.thought),
          node('p', 'hint', '来自 ' + item.encounters + ' 次有证据变化 · ' + (item.strength > 0 ? '仍感兴趣' : item.strength < 0 ? '兴趣减弱' : '保留观察') + ' · ' + when(item.last_change))
        );
        interests.append(card);
      }
      if (!interestList.length) interests.append(node('p', 'bili-empty', '有真正值得延续的兴趣时会出现在这里，不按关键词硬凑。'));

      const insights = $('bili-insights');
      insights.replaceChildren();
      const insightList = value.insights || [];
      $('bili-insights-count').textContent = String(insightList.length);
      for (const item of insightList) {
        const card = node('article', 'bili-item-card');
        const top = node('div', 'bili-item-head');
        top.append(pill(item.archived ? '已停用' : '活跃认识', item.archived ? 'muted' : 'ok'), node('span', 'hint', when(item.updated)));
        card.append(top, node('p', 'message', item.content));
        if (item.conditions) card.append(node('p', 'hint', '适用条件：' + item.conditions));
        const actions = node('div', 'bili-btn-row');
        actions.append(
          button('编辑', () => {
            editingInsight = item;
            $('bili-insight-content').value = item.content;
            $('bili-insight-editor').showModal();
          }, 'secondary'),
          button(item.archived ? '恢复认识' : '停用认识', async () => {
            await api('growth/insights/' + item.id, 'POST', { op: item.archived ? 'restore' : 'archive', version: item.version });
            await refresh();
          }, 'secondary')
        );
        card.append(actions);
        insights.append(card);
      }
      if (!insightList.length) insights.append(node('p', 'bili-empty', 'B站形成的认识会单独出现在这里，日常聊天不会读取。'));

      const readings = $('bili-readings');
      readings.replaceChildren();
      const readingList = value.readings || [];
      $('bili-readings-count').textContent = String(readingList.length);
      const exploreBadge = $('bili-tab-badge-explore');
      if (exploreBadge) {
        exploreBadge.hidden = readingList.length === 0;
        exploreBadge.textContent = String(readingList.length);
      }
      for (const item of readingList) {
        const row = node('article', 'bili-item-card');
        const top = node('div', 'bili-item-head');
        const title = node('a', 'bili-video-link', item.metadata.title || item.bvid);
        title.href = 'https://www.bilibili.com/video/' + item.bvid + '/';
        title.target = '_blank';
        title.rel = 'noopener noreferrer';
        const rmeta = readingMeta[item.state] || { label: item.state, tone: 'muted' };
        const badges = node('div', 'bili-badge-group');
        badges.append(pill(rmeta.label, rmeta.tone));
        if (!item.enabled) badges.append(pill('已停用', 'muted'));
        top.append(title, badges);
        row.append(top, node('p', 'hint', when(item.created)));
        if (item.metadata.question || item.error) row.append(node('p', 'message', item.metadata.question || item.error));
        const actions = node('div', 'bili-btn-row');
        actions.append(
          button('来源、反思与画面', () => detail(item), 'secondary'),
          button(item.enabled ? '停用材料及相关认识' : '恢复材料', async () => {
            await api('bilibili/readings/' + item.id, 'POST', { enabled: !item.enabled });
            await refresh();
          }, 'secondary')
        );
        row.append(actions);
        readings.append(row);
      }
      if (!readingList.length) {
        readings.append(node('p', 'bili-empty', '还没有视频记录。开启成长与B站探索后，空闲时会自行选择；也可点击右上角「探索一次」。'));
      }

      const watchSnap = await api('bilibili/watch').catch(() => null);
      if (watchSnap) renderWatch(watchSnap);
    } finally {
      loading = false;
    }
  }

  let watchDraft = null;

  function renderWatch(snap) {
    watchDraft = {
      enabled: !!snap.config?.bilibili_watch_enabled,
      chats: new Set(snap.config?.bilibili_watch_chat_ids || []),
      cooldown: Number(snap.config?.bilibili_watch_push_cooldown_minutes ?? 15),
      lockMinutes: Number(snap.config?.bilibili_watch_spoiler_lock_minutes ?? 120),
      targets: (snap.config?.bilibili_watch_targets || []).map(t => ({ ...t })),
      calendars: JSON.parse(JSON.stringify(snap.config?.bilibili_watch_calendars || {}))
    };

    const sched = snap.schedule || {};
    const modeTexts = {
      peak: '🔥 高峰蹲守（每 2.5 分钟探测一次）',
      mini_check: '⏱ 黄金整点踩点（每 5 分钟探测一次）',
      quiet: '🌿 平时省额兜底（每 40 分钟探测一次）'
    };
    const modeLabel = modeTexts[sched.mode] || '正常监测';
    const counts = snap.counts || {};
    $('bili-watch-mode-line').textContent = `${modeLabel} · 北京时间 ${sched.shanghai_time || '—'} · 队列 ${counts.queued || 0} / 已拆包 ${counts.done || 0}`
      + (snap.last_probe_at ? ` · 上次探测 ${shortWhen(snap.last_probe_at)}` : '')
      + (snap.last_probe_error ? ` · 异常：${snap.last_probe_error}` : '');
    const watchBadge = $('bili-tab-badge-watch');
    watchBadge.hidden = false;
    watchBadge.textContent = sched.mode === 'peak' ? '蹲守中' : (counts.done ? String(counts.done) : '待命');
    watchBadge.className = 'bili-tab-badge' + (sched.mode === 'peak' ? ' is-accent' : ' is-subtle');

    const calBox = $('bili-watch-calendars');
    calBox.replaceChildren();
    const gameMap = sched.games || {};
    for (const gameKey of ['genshin', 'starrail', 'zzz']) {
      const g = gameMap[gameKey];
      if (!g) continue;
      const card = node('article', 'bili-watch-cal-card');
      const head = node('div', 'bili-item-head');
      const titleRow = node('strong', 'bili-item-title', `${g.label} ${g.version ? 'v' + g.version : ''}`);
      head.append(
        titleRow,
        pill(g.peak_now ? '高峰蹲守中' : g.in_event_window ? '事件窗内' : '42天周期追踪', g.peak_now ? 'ok' : g.in_event_window ? 'warn' : 'muted')
      );
      const prevInfo = g.days_to_preview === 0 ? '今天预计前瞻' : g.days_to_preview > 0 ? `${g.days_to_preview} 天后前瞻 (${g.next_preview_date})` : `前瞻已过 (${g.next_preview_date})`;
      const patchInfo = g.days_to_patch === 0 ? '今天预计更新开服' : g.days_to_patch > 0 ? `${g.days_to_patch} 天后更新 (${g.next_patch_date})` : `版本期中 (${g.next_patch_date})`;
      const dates = node('p', 'hint', `${prevInfo} · ${patchInfo}`);
      const editRow = node('div', 'bili-watch-cal-edit');
      const verInput = document.createElement('input');
      verInput.type = 'text';
      verInput.value = watchDraft.calendars[gameKey]?.version || g.version || '';
      verInput.placeholder = '版本号';
      verInput.setAttribute('aria-label', g.label + ' 版本号');
      verInput.oninput = () => {
        watchDraft.calendars[gameKey] = { ...(watchDraft.calendars[gameKey] || {}), version: verInput.value.trim() };
      };
      const anchorInput = document.createElement('input');
      anchorInput.type = 'date';
      anchorInput.value = watchDraft.calendars[gameKey]?.anchor_date || g.anchor_date || '';
      anchorInput.setAttribute('aria-label', g.label + ' 更新基准日');
      anchorInput.onchange = () => {
        watchDraft.calendars[gameKey] = { ...(watchDraft.calendars[gameKey] || {}), anchor_date: anchorInput.value };
      };
      editRow.append(verInput, anchorInput);
      card.append(head, dates, editRow);
      calBox.append(card);
    }

    $('bili-watch-enabled').checked = watchDraft.enabled;
    $('bili-watch-cooldown').value = watchDraft.cooldown;
    $('bili-watch-lock-minutes').value = watchDraft.lockMinutes;

    const chatsBox = $('bili-watch-chats');
    chatsBox.replaceChildren();
    const availChats = snap.available_chats || [];
    for (const c of availChats) {
      const lbl = document.createElement('label');
      lbl.className = 'bili-feed-item';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = watchDraft.chats.has(c.chat);
      cb.onchange = () => {
        if (cb.checked) watchDraft.chats.add(c.chat);
        else watchDraft.chats.delete(c.chat);
      };
      const txt = node('span', '', `${c.name}${c.is_group ? '（群聊）' : '（私聊）'}`);
      lbl.append(cb, txt);
      chatsBox.append(lbl);
    }
    if (!availChats.length) {
      chatsBox.append(node('p', 'bili-empty', '尚未配置允许机器人回复的微信群，请先在主台「会话与唤醒」勾选群聊。'));
    }

    renderWatchTargets(snap.cursors || {});
    renderWatchLocks(snap.spoiler_locks || [], availChats);
    renderWatchItems(snap.items || []);
  }

  function renderWatchTargets(cursors) {
    const box = $('bili-watch-targets');
    box.replaceChildren();
    if (!watchDraft?.targets?.length) {
      box.append(node('p', 'bili-empty', '暂无追更账号，可上方输入 UP 主 UID 添加。'));
      return;
    }
    const gameLabels = { genshin: '原神', starrail: '星铁', zzz: '绝区零', all: '三游通用' };
    for (let idx = 0; idx < watchDraft.targets.length; idx++) {
      const t = watchDraft.targets[idx];
      const row = node('div', 'bili-watch-target-row');
      const left = node('label', 'bili-watch-target-main');
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = t.enabled !== false;
      cb.onchange = () => { t.enabled = cb.checked; };
      const cur = cursors[t.mid];
      const info = node('div', 'bili-pick-copy');
      const head = node('div', 'bili-pick-title', t.name || ('UID ' + t.mid));
      head.append(
        node('span', 'bili-badge', gameLabels[t.game] || t.game),
        node('span', 'bili-badge', t.kind === 'official' ? '官方账号' : '总结UP')
      );
      const sub = 'UID ' + t.mid + (cur?.last_checked ? ` · 上次检查 ${shortWhen(cur.last_checked)}` : '');
      info.append(head, node('div', 'bili-pick-sub', sub));
      left.append(cb, info);
      const acts = node('div', 'bili-btn-row');
      if (t.kind !== 'official') {
        const rm = node('button', 'quiet', '移除');
        rm.type = 'button';
        rm.onclick = () => {
          watchDraft.targets.splice(idx, 1);
          renderWatchTargets(cursors);
        };
        acts.append(rm);
      }
      row.append(left, acts);
      box.append(row);
    }
  }

  function renderWatchLocks(locks, availChats) {
    const box = $('bili-watch-locks');
    box.replaceChildren();
    const chatNameMap = Object.fromEntries((availChats || []).map(c => [c.chat, c.name]));
    if (!locks.length) {
      box.append(node('p', 'bili-empty', '目前没有微信群处于避剧透保护锁状态。'));
      return;
    }
    for (const l of locks) {
      const card = node('article', 'bili-item-card');
      const head = node('div', 'bili-item-head');
      head.append(
        pill(`🔒 ${l.game_label || l.game} · 剩约 ${l.remaining_minutes} 分钟`, 'warn'),
        node('span', 'hint', chatNameMap[l.chat] || l.chat)
      );
      card.append(head, node('p', 'hint', `触发人：${l.source_sender || '群友'} · 原因：${l.reason || '防剧透保护'}`));
      const acts = node('div', 'bili-btn-row');
      acts.append(button('提前解除该群锁定', async () => {
        const res = await api('bilibili/watch/spoiler-locks', 'POST', { chat: l.chat, game: l.game, action: 'unlock' });
        if (res?.snapshot) renderWatch(res.snapshot);
        notice('已解除防剧透锁定');
      }, 'secondary'));
      card.append(acts);
      box.append(card);
    }
  }

  function renderWatchItems(items) {
    const box = $('bili-watch-items');
    box.replaceChildren();
    if (!items.length) {
      box.append(node('p', 'bili-empty', '还没有捕获到三游动态，可点击右上角「立即追更探测一次」验证官号动态。'));
      return;
    }
    const kindLabels = {
      character_art: '新角色立绘',
      pv: '官方 PV / 演示',
      preview: '前瞻速览 / 兑换码',
      story_summary: '剧情总结',
      official_notice: '官方公告',
      livestream_notice: '直播索引'
    };
    const stateLabels = { queued: '待读', reading: '理解中', done: '已拆包', failed: '未完成' };
    for (const item of items) {
      const card = node('article', 'bili-item-card');
      const top = node('div', 'bili-item-head');
      top.append(
        pill(`${item.game_label}${item.version ? ' ' + item.version : ''} · ${kindLabels[item.kind] || item.kind}`, item.is_story ? 'muted' : 'ok'),
        pill(stateLabels[item.state] || item.state, item.state === 'done' ? 'ok' : item.state === 'failed' ? 'warn' : 'muted'),
        item.is_story ? pill('🚫 剧情向 · 禁主动推群', 'warn') : pill('✨ 表层可推群', 'muted'),
        node('span', 'hint', shortWhen(item.pub_ts))
      );
      card.append(top);
      const titleLine = node('strong', 'bili-item-title', item.title || item.bvid || item.dyn_id);
      card.append(titleLine);
      if (item.error) card.append(node('p', 'error-text', item.error));

      if (item.image_count > 0) {
        const imgWrap = node('div', 'bili-watch-thumbs');
        for (let i = 0; i < Math.min(2, item.image_count); i++) {
          authedImage(`bilibili/watch/items/${item.id}/images/${i}`).then(url => {
            const img = document.createElement('img');
            img.src = url;
            img.alt = item.title || '官宣立绘';
            img.onload = () => URL.revokeObjectURL(url);
            imgWrap.append(img);
          }).catch(() => {});
        }
        card.append(imgWrap);
      }

      if (item.safe_facts?.length) {
        const safeBox = node('div', 'bili-watch-safe');
        safeBox.append(node('strong', 'bili-watch-fact-label', '✅ 无剧透安全亮点（日常/问外观可用）'));
        for (const f of item.safe_facts) safeBox.append(node('p', 'message', '· ' + f));
        card.append(safeBox);
      }
      if (item.spoiler_facts?.length) {
        const details = document.createElement('details');
        details.className = 'bili-fold bili-watch-spoiler';
        const sum = node('summary', '', `🔒 隔离剧透事实（${item.spoiler_facts.length} 条 · 点击展开，防误剧透）`);
        details.append(sum);
        for (const sf of item.spoiler_facts) details.append(node('p', 'message', '· ' + sf));
        card.append(details);
      }
      if (item.deliveries?.length) {
        const sentTxt = item.deliveries.map(d => `${d.chat}:${d.text_state}${d.image_job_id ? '+图' : ''}`).join('，');
        card.append(node('p', 'hint', '群推回执：' + sentTxt));
      }

      const acts = node('div', 'bili-btn-row');
      acts.append(
        button(item.is_story ? '解除剧情限制' : '设为剧情禁推', async () => {
          const res = await api('bilibili/watch/items/' + item.id, 'POST', { action: item.is_story ? 'unmark_story' : 'mark_story' });
          if (res?.snapshot) renderWatch(res.snapshot);
        }, 'secondary'),
        button(item.enabled ? '停用此条' : '恢复启用', async () => {
          const res = await api('bilibili/watch/items/' + item.id, 'POST', { action: item.enabled ? 'disable' : 'enable' });
          if (res?.snapshot) renderWatch(res.snapshot);
        }, 'secondary'),
        button('重置投递', async () => {
          const res = await api('bilibili/watch/items/' + item.id, 'POST', { action: 'redeliver' });
          if (res?.snapshot) renderWatch(res.snapshot);
          notice('已重置该条目的群推送去重记录');
        }, 'quiet')
      );
      if (item.url) {
        const a = document.createElement('a');
        a.href = item.url;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        a.className = 'quiet';
        a.textContent = '原动态 ↗';
        acts.append(a);
      }
      card.append(acts);
      box.append(card);
    }
  }

  async function saveBiliSettings() {
    for (const id of numericSettingIds) {
      const el = $(id);
      if (!el.checkValidity()) {
        const ownerPanel = el.closest('[data-bili-panel]');
        if (ownerPanel) switchTab(ownerPanel.dataset.biliPanel);
        el.focus();
        el.reportValidity();
        throw new Error('请检查B站数值设置是否在允许范围内');
      }
    }
    await saveSubset({
      bilibili_enabled: $('bili-enabled').checked,
      bilibili_wechat_video_enabled: $('bili-wechat-video').checked,
      bilibili_comment_mentions_enabled: $('bili-comment-mentions').checked,
      bilibili_comment_reply_enabled: $('bili-comment-reply').checked,
      bilibili_reply_scope: $('bili-reply-scope').value,
      bilibili_reply_to_replies_enabled: $('bili-reply-to-replies').checked,
      bilibili_reply_use_special_following: $('bili-reply-special').checked,
      bilibili_reply_mode: $('bili-reply-mode').value,
      bilibili_reply_allowed_sender_mids: $('bili-reply-mids').value.split(/\r?\n/).map(value => value.trim()).filter(Boolean),
      bilibili_reply_deep_max_minutes: Number($('bili-reply-deep-min').value),
      bilibili_reply_deep_max_windows: Number($('bili-reply-deep-win').value),
      bilibili_reply_per_day: Number($('bili-reply-per-day').value),
      bilibili_reply_min_interval_seconds: Number($('bili-reply-interval').value),
      bilibili_reply_max_per_thread: Number($('bili-reply-thread-cap').value),
      bilibili_reply_max_age_hours: Number($('bili-reply-age').value),
      bilibili_reply_record_ignored: $('bili-reply-record-ignored').checked,
      bilibili_reply_retention_limit: Number($('bili-reply-retention-limit').value),
      bilibili_reply_retention_days: Number($('bili-reply-retention-days').value),
      bilibili_visual_enabled: $('bili-visual').checked,
      bilibili_comments_enabled: $('bili-comments').checked,
      bilibili_danmaku_enabled: $('bili-danmaku').checked,
      bilibili_interval_minutes: Number($('bili-interval').value),
      bilibili_requests_per_day: Number($('bili-requests').value),
      bilibili_cache_mb: Number($('bili-cache').value),
      bilibili_cache_days: Number($('bili-days').value),
      bilibili_media_enabled: $('bili-media').checked,
      bilibili_comments_limit: Number($('bili-comment-limit').value),
      bilibili_comment_pages: Number($('bili-comment-pages').value),
      bilibili_reply_threads: Number($('bili-replies').value),
      bilibili_keyframes_limit: Number($('bili-frames').value),
      bilibili_segment_seconds: Number($('bili-segment').value),
      bilibili_interest_seeds: $('bili-seeds').value.split(/\r?\n/).map(value => value.trim()).filter(Boolean),
      bilibili_excluded_topics: $('bili-excluded-topics').value.split(/\r?\n/).map(value => value.trim()).filter(Boolean),
      bilibili_feeds: [...$('bili-feeds').querySelectorAll('input:checked')].map(input => input.dataset.feed)
    });
    window.dispatchEvent(new CustomEvent('whalegirl-panel-saved', { detail: 'bilibili-console' }));
    notice('B站设置已保存');
    await refresh();
  }

  window.addEventListener('whalegirl-studio-dirty', event => {
    const info = event.detail;
    if (!info || info.id !== 'bilibili-console') return;
    const dirtyPill = $('bili-dirty-pill');
    const saveBtn = $('bili-save');
    if (!dirtyPill || !saveBtn) return;
    if (info.busy) {
      dirtyPill.hidden = false;
      dirtyPill.textContent = '正在保存…';
      dirtyPill.dataset.tone = 'info';
      saveBtn.textContent = '保存中…';
      saveBtn.classList.remove('is-dirty');
    } else if (info.count > 0) {
      dirtyPill.hidden = false;
      dirtyPill.textContent = `${info.count} 项更改待保存`;
      dirtyPill.dataset.tone = 'warn';
      saveBtn.textContent = `保存更改 (${info.count})`;
      saveBtn.classList.add('is-dirty');
    } else if (info.phase === 'error') {
      dirtyPill.hidden = false;
      dirtyPill.textContent = '保存失败';
      dirtyPill.dataset.tone = 'danger';
      saveBtn.textContent = '重试保存';
      saveBtn.classList.add('is-dirty');
    } else {
      dirtyPill.hidden = true;
      saveBtn.textContent = '保存设置';
      saveBtn.classList.remove('is-dirty');
    }
  });

  $('bili-save').onclick = () => {
    lastSavePromise = action($('bili-save'), saveBiliSettings);
    return lastSavePromise;
  };
  $('bili-poll-mentions').onclick = () => action($('bili-poll-mentions'), async () => {
    const res = await api('bilibili/poll-mentions', 'POST');
    await refresh();
    notice('已检查 B 站 @ 与追评通知' + (res?.promoted ? ` · 新晋级 ${res.promoted} 条待看记录` : ''));
  });
  $('bili-sync-directory').onclick = () => action($('bili-sync-directory'), async () => {
    const dir = await syncDirectory(true);
    notice(`已同步 ${dir?.followings?.length || 0} 位关注好友 · ${(dir?.special_users || []).length} 位特别关注`);
  });

  async function runUserLookup() {
    const q = $('bili-user-query').value.trim();
    if (!q) {
      lastLookupResult = null;
      renderLookup(null);
      notice('请输入 B 站 UID、个人主页链接或昵称', true);
      return;
    }
    const res = await api('bilibili/reply-lookup', 'POST', { query: q });
    lastLookupResult = res;
    const foundNames = {};
    for (const c of res.candidates || []) if (c.mid && c.name) foundNames[c.mid] = c.name;
    mergeDirectory({ known_names: foundNames });
    renderLookup(res);
  }
  $('bili-user-lookup').onclick = () => action($('bili-user-lookup'), runUserLookup);
  $('bili-user-query').addEventListener('keydown', event => {
    if (event.key === 'Enter') {
      event.preventDefault();
      $('bili-user-lookup').click();
    }
  });
  $('bili-picker-filter').addEventListener('input', renderPickers);
  $('bili-reply-mids').addEventListener('input', () => {
    renderPickers();
    if (current?.comment_replies) renderReplies(current.comment_replies);
  });
  $('bili-reply-scope').addEventListener('change', () => renderPickers());
  $('bili-reply-special').addEventListener('change', () => {
    renderPickers();
    if (current?.comment_replies) renderReplies(current.comment_replies);
  });
  $('bili-replies-filter').addEventListener('change', () => {
    if (current?.comment_replies) renderReplies(current.comment_replies);
  });
  $('bili-replies-search').addEventListener('input', () => {
    if (current?.comment_replies) renderReplies(current.comment_replies);
  });
  $('bili-replies-prune').onclick = () => action($('bili-replies-prune'), async () => {
    const res = await api('bilibili/replies-prune', 'POST', {});
    const total = Number(res?.deleted_replies || 0) + Number(res?.deleted_readings || 0);
    notice(total > 0
      ? `已清理 ${res.deleted_replies || 0} 条已完结回复、${res.deleted_readings || 0} 条深看记录`
      : '没有可清理的已完结评论回复记录');
    await refresh();
  });

  $('bili-login').onclick = () => action($('bili-login'), async () => {
    stopPoll();
    clearQR();
    switchTab('settings');
    const state = await api('bilibili/account', 'POST', { action: 'login' });
    account(state);
    qrObject = await authedImage('bilibili/account/qr');
    $('bili-qr').src = qrObject;
    $('bili-login-box').hidden = false;
    queuePoll();
  });
  $('bili-check').onclick = () => action($('bili-check'), async () => {
    account(await api('bilibili/account', 'POST', { action: 'check' }));
    await refresh();
  });
  $('bili-logout').onclick = () => action($('bili-logout'), async () => {
    stopPoll();
    clearQR();
    $('bili-login-box').hidden = true;
    account(await api('bilibili/account', 'POST', { action: 'logout' }));
    await refresh();
  });
  $('bili-resume').onclick = () => action($('bili-resume'), async () => {
    account(await api('bilibili/account', 'POST', { action: 'resume', acknowledge: true }));
    await refresh();
  });
  $('bili-reply-resume').onclick = () => action($('bili-reply-resume'), async () => {
    await api('bilibili/reply-resume', 'POST', { acknowledge: true });
    await refresh();
    notice('已恢复B站评论回复发送');
  });
  $('bili-explore').onclick = () => action($('bili-explore'), async () => {
    const result = await api('bilibili/explore', 'POST');
    notice('探索任务 ' + result.id + ' · ' + result.state + (result.reason ? ' · ' + result.reason : ''));
    await refresh();
    window.growthConsole?.refresh().catch(() => {});
  });
  $('bili-refresh').onclick = () => action($('bili-refresh'), refresh);

  $('bili-watch-probe').onclick = () => action($('bili-watch-probe'), async () => {
    const res = await api('bilibili/watch/probe', 'POST', { force: true, digest: true });
    if (res?.snapshot) renderWatch(res.snapshot);
    notice(res?.ok === false
      ? ('追更探测失败：' + (res.error || res.reason || '未知原因'))
      : (`探测完成（${res.probed || 0} 个账号 · 新增待读 ${res.queued || 0} 条${res.calibrated ? ' · 已自动校准版本排期' : ''}）`));
  });

  $('bili-watch-add-btn').onclick = () => action($('bili-watch-add-btn'), async () => {
    const rawMid = $('bili-watch-add-mid').value.trim();
    const mid = rawMid.match(/([1-9][0-9]{0,19})/)?.[1] || '';
    if (!mid) throw new Error('请输入有效的 B 站 UP 主数字 UID');
    let name = $('bili-watch-add-name').value.trim();
    if (!name) {
      const looked = await api('bilibili/reply-lookup?q=' + encodeURIComponent(mid)).catch(() => null);
      name = looked?.candidates?.[0]?.name || ('UID ' + mid);
    }
    const game = $('bili-watch-add-game').value;
    if (!watchDraft) watchDraft = { targets: [], chats: new Set(), calendars: {}, enabled: true, cooldown: 15, lockMinutes: 120 };
    if (watchDraft.targets.some(t => t.mid === mid)) throw new Error('该 UID 已在追更列表中');
    watchDraft.targets.push({ mid, name, game, kind: 'summary_up', enabled: true });
    $('bili-watch-add-mid').value = '';
    $('bili-watch-add-name').value = '';
    const snap = await api('bilibili/watch/config', 'POST', { bilibili_watch_targets: watchDraft.targets });
    renderWatch(snap);
    notice('已添加追更 UP 主：' + name);
  });

  $('bili-watch-save').onclick = () => action($('bili-watch-save'), async () => {
    const cooldown = Number($('bili-watch-cooldown').value || 15);
    const lockMinutes = Number($('bili-watch-lock-minutes').value || 120);
    const payload = {
      bilibili_watch_enabled: $('bili-watch-enabled').checked,
      bilibili_watch_chat_ids: watchDraft ? [...watchDraft.chats] : [],
      bilibili_watch_push_cooldown_minutes: cooldown,
      bilibili_watch_spoiler_lock_minutes: lockMinutes,
      bilibili_watch_targets: watchDraft ? watchDraft.targets : undefined,
      bilibili_watch_calendars: watchDraft ? watchDraft.calendars : undefined
    };
    const snap = await api('bilibili/watch/config', 'POST', payload);
    renderWatch(snap);
    notice('三游追更与防剧透配置已保存');
  });

  $('bili-detail-close').onclick = () => $('bili-reading-detail').close();
  $('bili-insight-cancel').onclick = () => $('bili-insight-editor').close();
  $('bili-insight-cancel-top').onclick = () => $('bili-insight-editor').close();
  for (const dialog of [$('bili-reading-detail'), $('bili-insight-editor')]) {
    dialog.addEventListener('click', event => {
      if (event.target === dialog) {
        const rect = dialog.getBoundingClientRect();
        if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) {
          dialog.close();
        }
      }
    });
  }
  $('bili-insight-save').onclick = () => action($('bili-insight-save'), async () => {
    if (!editingInsight) return;
    const content = $('bili-insight-content').value.trim();
    if (!content) throw new Error('认识内容不能为空');
    await api('growth/insights/' + editingInsight.id, 'POST', { op: 'update', version: editingInsight.version, content });
    $('bili-insight-editor').close();
    editingInsight = null;
    await refresh();
    notice('B站独立认识已保存');
  });

  panel.addEventListener('toggle', () => {
    if (visible()) {
      refresh().catch(error => notice(error.message, true));
      if (current?.account?.qr_expires > Date.now() / 1000) queuePoll();
    } else {
      stopPoll();
    }
  });
  host.addEventListener('toggle', () => {
    if (!host.contains(panel)) return;
    if (!visible()) stopPoll();
    else refresh().catch(error => notice(error.message, true));
  });

  window.bilibiliConsole = {
    populate,
    openTab: switchTab,
    stateUpdated(state) {
      const alert = state?.bilibili_alert;
      if (alert && alert.code !== lastAlert) {
        lastAlert = alert.code;
        notice(alert.message, true);
      }
      if (!alert) lastAlert = '';
      if (visible() && Date.now() - lastFetch > 5000) {
        const activeEl = document.activeElement;
        if (activeEl && $('bili-replies-list')?.contains(activeEl) && activeEl.matches('textarea,button')) return;
        refresh().catch(error => notice(error.message, true));
      }
    }
  };
  if (location.hash === '#bilibili-console') {
    for (let element = panel; element; element = element.parentElement) {
      if (element.tagName === 'DETAILS') element.open = true;
    }
    setTimeout(() => panel.scrollIntoView({ block: 'start' }), 250);
  }
  if (cfg) populate();
})();
