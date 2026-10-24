# 鲸鱼娘 Linux 微信机器人交接文档

**交接时间：** 2026-10-24 18:48（北京时间，含下午完整迭代与四层内存清理机制）  
**目的：** 让接手的 agent 能从当前代码、Git 和服务器状态继续工作，避免重复部署、重复登录或误判已验收范围。  
**项目范围：** 仅 `D:\appData\qqbot\wechat-linux` 里的全 Linux 微信机器人。

> 这份文档包含 2026-10-24 上午的稳定性修复与 2026-10-24 下午（12:45–18:48）完成的六项功能/智能度/内存守护迭代更新，最新生产状态快照核对于 2026-10-24 18:48（北京时间）。服务可能在之后发生变化，接手时应先短暂复核服务器，不要把快照当作实时状态。  
> 本文不含 API Key、管理密码、SSH 私钥、微信登录数据或私人聊天原文。

## 1. 交接摘要

- 微信固定使用 **4.1.1.8**，运行在 Linux Docker 容器中；回复核心、原生读取辅助服务和管理入口也在 Linux 服务器常驻。
- GitHub 仓库为 `Akie-qiu/whalegirl-linux`（已按用户要求设为 **Public 公开仓库**，全库已完成凭据扫描无敏感信息），`main` 分支与 `backup/local-snapshot-20261024` 分支保持同步；最新代码基线为 `7875449`。
- **2026-10-24 下午会话完成的六项核心更新**（均已部署至远端并推送到 GitHub）：
  1. **Prefix Cache（前缀缓存）拼贴顺序重构（`7b05faa`、`4776344`）**：在**零改动用户人设提示词原文**的前提下，将所有静态规则提示词与基础图库列表前置到 `static_system`，将单轮变化的候选差集、群成员 `@` 花名册、检索记忆、贴吧召回、表情候选及好感度移至 `【本轮动态上下文】` 尾部，并引入历史对话窗口步进锚定（stride anchoring）。多轮对话 Prefix Cache 命中率由 ~18% 提升至 **78%–93%**。
  2. **微信 `4.1.1.8` 退出后无法恢复重登故障修复（`8b6ed1a`）**：修复网关 `wechat.js` 中英文界面 `"Enter Weixin"` 按钮未归一化为 `"进入微信"` 导致快捷复登点击失效的问题；修复微信退出后残留“你已退出微信”良性提示弹窗被误判为风控拦截从而阻断 `getLoginQr()` 刷新二维码的问题；修复宿主机 `wechat-home` 目录属主（`1000:1000`）。
  3. **本地登录频次保护调整（`a8c1f3c`）**：将本地 10 分钟内允许发起的登录/重登请求上限从 `3` 次调整为 **`7` 次**（同步更新 `safety.py`、`app.py`、`web.html`）。
  4. **朋友圈自主发送分时段随机排期优化（`4b7daa6`）**：将每日活跃窗口（如 `09:00–23:00`）按 `moments_publish_per_day` 均分为等长时段（Slot），在每个时段内部通过确定性随机种子挑选发送时间点，解决“刚进活跃窗口就扎堆连发”问题；修复单次发圈失败后 `moments_actions.source` 唯一键锁死全天重发的问题；增强 `moments.js` 动态文本容错匹配。
  5. **群聊回复智能度与发图闭环修复（`85a91e6`）**：
     - 修复 `context.py` 在用户向机器人索图（如 `发几张碗粥角色图` / `有没有收藏好看的图片？发一下` / `发啊`）时误将前几分钟群友发的旧表情包再次挂载为视觉附件、导致模型连续多轮对着旧表情包“看图说话”的缺陷；
     - 修复 `runtime.py` 中 `image_question` 正则将 `有没有.*图` 误判为看图识别题的问题，并增加被群友否定/追问时禁止原样复读错话的系统约束；
     - 新增“空口发图（文字答应发图但 `image_request` 为 `null`）”服务端拦截与二次跟进补救机制；
     - 升级 `images.py` 与 `image_tools.py`：图库候选容量从 `12` 扩至 `24`，分词升级为重叠二元词（Overlapping Bigrams）+ 关键单字权重 + 同音别称映射（`碗粥/瑞粥/晚粥 -> 挽昼`），且 `send_library_image` 支持 `asset_ids` 单轮连发最多 3 张图片。
  6. **四层全自动内存清理与 `WeChatAppEx` 命名空间压制机制（`7875449`）**：
     - 解决群内收到网页链接触发 11 个 `WeChatAppEx` Chromium 子进程吞噬 `400+ MiB` 内存并阻塞 AT-SPI 总线导致 `a11y-dump` 8 秒超时的问题；
     - `core_watchdog.py` 每分钟自动在容器挂载命名空间将轻量休眠桩绑定至 `/opt/wechat/RadiumWMPF/runtime/WeChatAppEx`（不删原文件），自动收割超时 `a11y-dump` / `xclip` / `unittest` 孤儿进程，并在宿主机 `MemAvailable < 320 MiB` 时自动回收干净页缓存；
     - `runtime.py` 每 30 秒空闲周期执行 `PRAGMA shrink_memory` + `gc.collect()` + `libc.malloc_trim(0)`，将宿主机可用物理内存稳定恢复至 **`597 MiB`**，负载回落至 **`0.89`**。
- **2026-10-24 上午完成的稳定性优化**：回复核心有效限制为 `MemoryHigh=384M`、`MemoryMax=512M`、`MemorySwapMax=512M`；Uvicorn 有界并发；每分钟独立检查核心健康。
- 最近检查（18:48）时核心与网页健康接口均返回 200（`status=ok`，13 个后台协程全部正常，失败任务 0，`MemoryCurrent=72.7 MiB`），微信已扫码登录在线。

## 2. 首要操作规则

开始工作前先读：

- [AGENTS.md](AGENTS.md)：命令、删除、服务器、微信版本和数据处理要求。
- [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md)：项目功能概览与已知边界。
- 与要修改功能对应的专题文档；本文件第 11 节列出索引。

必须保留以下约束：

1. 本地使用 PowerShell / `pwsh`，不使用 Windows `cmd`。
2. 删除任何文件、容器、旧发布、备份或数据前，先向用户说明具体目标并逐项检查删除命令；没有明确授权时保留。
3. 不重启或重建微信容器来修 Python 核心问题。优先只处理 `whalegirl-linux.service`。
4. 固定使用微信 4.1.1.8；不自动升级、不盲目重复扫码或请求手机确认。
5. 未指定验收会话前，不向真实群聊、私聊或朋友圈发送测试内容。
6. 访问在线 SQLite 数据时使用只读连接；不要用会执行迁移或恢复写操作的 Store 对生产库做检查。
7. 不把密钥、管理凭据、登录令牌、二维码、聊天原文或图片 base64 写入 Git、日志或交接文档。
8. **严禁改动用户的人设提示词原文**（`style.system_prompt` / `system_prompt_override`）。
9. 不修改 QQ 项目或 QQ 服务器部署；它们不属于本仓库任务范围。

## 3. 仓库与 Git 状态

### 本机位置

- 项目 Git 根目录：`D:\appData\qqbot\wechat-linux`
- `D:\appData\qqbot` 是工作区父目录，**不是 Git 根目录**。
- 本机的临时部署归档位于父目录 `D:\appData\qqbot\artifacts\`，该目录被忽略，不属于源码仓库。

### GitHub 仓库状态

- 仓库：`Akie-qiu/whalegirl-linux`（Public 公开仓库，`https://github.com/Akie-qiu/whalegirl-linux`）
- 当前同步分支：`main`（默认分支）与 `backup/local-snapshot-20261024`
- 2026-10-24 提交演进记录（由新到旧）：
  - `7875449` — `feat(stability): add 4-layer automatic memory cleanup and WeChatAppEx bind-mount suppression`（四层自动内存清理、WeChatAppEx 挂载压制与发送前置容错）
  - `1220d08` — `更新-10/24`（下午阶段性交接文档归档）
  - `85a91e6` — `fix(chat): prevent stale image re-attachment, support homophone/multi-image library send, and guard empty image promises`（群聊防旧图误挂、同音别称与多图连发、空口发图拦截补救）
  - `4b7daa6` — `feat(moments): schedule autonomous posts randomly across evenly divided daily time slots`（朋友圈分时段随机排期与失败重试修复）
  - `a8c1f3c` — `chore(safety): increase local login request limit from 3 to 7 per 10 minutes`（本地登录限制调至 10 分钟 7 次）
  - `8b6ed1a` — `fix(login): normalize Enter Weixin button for 4.1.1.8 and dismiss benign sign-out popup on QR refresh`（修复微信 4.1.1.8 复登按钮识别与退出弹窗阻塞）
  - `4776344` — `perf(cache): move base library image catalog into static system prefix`（基础图库并入静态前缀）
  - `7b05faa` — `perf(cache): reorder static and dynamic prompt blocks for prefix cache stability`（Prefix Cache 静态/动态提示词拼贴顺序重构）
  - `706a4c7` — `Add detailed agent handoff for current deployment`
  - `d0e2bac` — `Repair readability on interrupted stability deploy`
  - `604e5bf` — 原子部署时保留目标所有权和可读性
  - `8c10aa2` — 修正登录状态校验地址
  - `9aa7b74` — 核心资源限制、健康检查和 watchdog 初版
  - `2ee9de5` — 2026-10-24 源码快照基线
- `main` 分支停留在较早的项目归档快照；继续当前工作应从 `backup/local-snapshot-20261024` 分支开始，不要把较旧 `main` 当成生产现状，也不要改写快照提交。

克隆并包含上游网关子模块：

```powershell
git clone --branch backup/local-snapshot-20261024 --recurse-submodules https://github.com/Akie-qiu/whalegirl-linux.git
```

仓库通过 `.gitmodules` 锁定 `references/agent-wechat-source/` 上游源码。不要把子模块改为漂移的未固定上游版本。

## 4. 服务器架构与生产路径

### 组件概览

| 组件 | 当前用途 | 运行位置/入口 |
| --- | --- | --- |
| `whalegirl-wechat-candidate-4118` | 原生 Linux 微信客户端及网关 | Docker；主机回环端口 `6175` 转发至容器 `6174` |
| `whalegirl-native-reader.service` | 原生消息库只读读取辅助进程 | 主机到容器的私有 socket；只读 SQLCipher |
| `whalegirl-linux.service` | Python/FastAPI 回复核心和管理 API | `/opt/whalegirl-linux/current`，回环 `127.0.0.1:8092` |
| `caddy.service` | 管理网页入口反向代理 | 回环 `8093`、手机 HTTP 入口 `18092` |
| `whalegirl-web-tunnel.service` | Cloudflare Quick Tunnel | HTTPS 临时地址，重启后可能变化 |
| `whalegirl-stability.timer` | 微信状态及资源采样 | 每分钟一次，服务器端常驻 |
| `whalegirl-core-watchdog.timer` | 回复核心健康监控 | 每分钟一次，服务器端常驻 |

服务器通过免密 SSH 连接 `root@120.46.186.50`。本文件不记录也不需要 SSH 私钥内容。

### 重要目录

- 活动发布软链接：`/opt/whalegirl-linux/current`
- 最近复核时软链接指向：`/opt/whalegirl-linux/releases/group-mentions-20261022-232824`；稳定性修复在这个活动发布上做了受控原子更新。
- 历史发布：`/opt/whalegirl-linux/releases/`
- 部署备份和验收材料：`/opt/whalegirl-linux-assets/`
- 回复核心配置、SQLite、令牌及用户数据：`/var/lib/whalegirl-linux/core/`
- 微信容器持久数据：`/var/lib/whalegirl-linux/candidate-4.1.1.8/`
- 连续稳定性观测：`/var/lib/whalegirl-linux/stability/`
- 核心 watchdog 状态：`/var/lib/whalegirl-linux/stability/core-watchdog.json`
- 管理员认证材料和网关令牌存于服务器数据目录，读取时不要将值打印出来。

端口和在线 URL 会受服务器配置、网络策略和隧道状态影响。手机入口当前由 `18092` 提供；Quick Tunnel 的实际 HTTPS URL 应在服务器上查询 `whalegirl-web-tunnel.service` 日志，不要复用历史文档里的旧临时 URL。

## 5. 最近故障、修复和生产状态

### 5.1 故障原因

2026-10-24 现场检查发现：

- 微信容器仍运行，版本校验为 4.1.1.8，登录状态正常，容器 `RestartCount=0`、`OOMKilled=false`。
- Python 核心仍有 PID 并监听端口，但 `/health` 和管理入口超时。
- 核心 cgroup 使用约 163–171 MiB，而当时有效 `MemoryHigh` 约 160 MiB、`MemoryMax` 192 MiB；cgroup `memory.events high` 已累积大量触发，memory PSI 很高。
- 因此主要问题发生在**主机上的回复核心内存节流**，没有证据表明那一刻是微信容器 OOM 或微信登录过期。

### 5.2 已部署的改动

- 有效回复核心资源预算：
  - `MemoryHigh=384M`
  - `MemoryMax=512M`
  - `MemorySwapMax=512M`
- Uvicorn：
  - 并发上限 32
  - backlog 128
  - keep-alive 5 秒
  - 优雅退出上限 30 秒
- `/health` 会报告后台任务数和已结束任务数；后台任务失败时返回 degraded。
- 新增独立 systemd watchdog：
  - 每分钟请求 `127.0.0.1:8092/health`。
  - 连续 3 次检查失败后才尝试恢复。
  - 两次自动恢复至少间隔 15 分钟；采样中断超过 3 分钟会清空旧失败计数。
  - 只重启 `whalegirl-linux.service`，不会重启微信容器或触发重新登录。
  - 记录最后探测、连续失败、恢复次数和核心 cgroup 内存/交换区/任务数/OOM 计数。
- 部署脚本先确认微信容器状态/版本并以只读方式检查在途发送；保存变更前文件和配置备份。

### 5.3 部署期间发生过的二次问题

首次执行部署脚本时，原子写入把 Python `__main__.py` 变成了 `root:root 0640`。服务用户 `whalegirl-ai` 因而无法读取模块，产生启动 `PermissionError` 和连续 systemd 重试；这不是微信掉线，也不是内存限制再次触发。

修复已提交并再次部署：

- 原子替换保留目标 UID/GID；新文件保持服务用户可读。
- 对“代码已经打补丁、但权限不可读”的中断状态会补修权限。
- 第二次部署健康检查成功，活动核心于 **2026-10-24 12:33:34（北京时间）** 启动。

systemd 的历史 `NRestarts=89` 包含上述部署权限错误期间的自动重试。随后两次相隔约 45 秒的检查均显示核心 PID 不变、`NRestarts` 保持 89、健康接口正常。不要把 89 误解为目前仍每几秒重启；但也应先检查现场再作判断。

### 5.4 最近生产快照

只读检查时间约为 **2026-10-24 12:41（北京时间）**：

- `whalegirl-linux.service`：active/running。
- 核心 `/health` 和 `18092/health`：HTTP 200，`status=ok`、微信版本 `4.1.1.8`、13 个后台任务、失败任务 0。
- 管理 API 状态：`enabled=true`，未暂停。
- 网关认证状态：`logged_in`，账号存在。
- `whalegirl-core-watchdog.timer` 与 `whalegirl-stability.timer`：均 active/waiting。
- 核心 RSS/cgroup 使用约 89 MiB；核心 `memory.high`、`memory.max` 事件及 OOM 计数均为 0。
- `CLOSE-WAIT` 数为 0；部署后核心日志未再看到 PermissionError、MemoryError 或 OOM。
- 微信容器 ID `0f7b7415251f`，状态 running，重启 0 次，容器内 OOM kill 为 0。
- 观测器最近一次样本：微信主 PID `469762`，5 个僵尸进程，1 个原生读取进程；`pids.current=286/1024`，`pids.events max=0`。
- 同一样本里微信容器内存约 750 MiB、交换区约 582 MiB；cgroup `memory.events max=19925` 是累计上限触达记录，`oom=0`、`oom_kill=0`。请持续观察容器余量和交换区，但不要因该累计值直接重建容器。
- 观测从 2026-10-21 12:06 开始；记录的当前在线段从 2026-10-23 14:05 开始，到 2026-10-24 12:41 约 22 小时 36 分，未达到 24 小时。累计记录有 5 次掉线事件，因此仅凭当前在线不能说明历史掉线已消失。

上述数据是一次服务器快照，不是 24 小时验收或未来服务保证。

### 5.5 当前稳定性修复文件

- `deploy/core_watchdog.py`
- `deploy/whalegirl-core-watchdog.service`
- `deploy/whalegirl-core-watchdog.timer`
- `deploy/deploy_core_stability.py`
- 对活动发布进行精确源码替换的入口：`whalegirl_linux/app.py`、`whalegirl_linux/__main__.py`
- 相关状态采样：`deploy/collect_stability.py`
- 故障历史：`STABILITY_FIX.md`、本文件

服务器上这次部署的变更前备份：

- 首次尝试前：`/opt/whalegirl-linux-assets/core-process-stability-before-20261024-121746-592048181/`
- 成功部署前：`/opt/whalegirl-linux-assets/core-process-stability-before-20261024-123334-26212216/`
- 部署文件暂存：`/opt/whalegirl-linux-assets/core-stability-deploy-20261024-r3/`

全部保留；没有删除旧发布或备份。遇到回滚需求时，先比较备份内容、权限和当前 unit 的生效值，再制定恢复操作；不要直接恢复过低的旧内存阈值，也不要用删除 drop-in 的方式盲目回退。

### 5.6 2026-10-24 下午功能与智能度更新详情（`7b05faa` ~ `85a91e6`）

1. **Prefix Cache 提示词拼贴顺序重构（`7b05faa`、`4776344`）**
   - 涉及文件：`whalegirl_linux/runtime.py`、`whalegirl_linux/image_tools.py`、`whalegirl_linux/conversation.py`、`whalegirl_linux/mentions.py`、`whalegirl_linux/memory.py`、`whalegirl_linux/search.py`、`whalegirl_linux/sticker_library.py`、`whalegirl_linux/affection.py`、`whalegirl_linux/context.py`。
   - 核心改动：保持用户人设提示词原文一字不改，将工具固定说明与基础图库列表（`base_candidates`）聚合至 `static_system` 前缀，将每轮动态变化的图库差集、群成员 `@` 列表、当轮记忆检索、贴吧召回、表情候选及好感度实时值移至 `【本轮动态上下文】` 尾部；`context.py` 对会话历史窗口引入步进锚定（stride anchoring），减少窗口滑动引起的缓存失效。
2. **微信 `4.1.1.8` 退出后无法恢复重登修复（`8b6ed1a`）**
   - 涉及文件：`references/agent-wechat-source/packages/agent-wechat/src/wechat.js`（及容器内 `/opt/agent-wechat/dist/wechat.js`）。
   - 核心改动：`normalizeText` 将英文版快捷登录按钮 `"Enter Weixin"` 归一化为 `"进入微信"`；`getLoginQr()` 与 `pressQuickLoginButton()` 遇到“你已退出微信 / 已退出登录”良性弹窗时自动点击 `确定 / OK` 关闭弹窗并继续切换二维码，不再被 `detectLoginWarning` 误判为风控拦截；修正宿主机 `/var/lib/whalegirl-linux/candidate-4.1.1.8/wechat-home` 属主为 `1000:1000`。
3. **本地登录限制调整为 10 分钟 7 次（`a8c1f3c`）**
   - 涉及文件：`whalegirl_linux/safety.py`、`whalegirl_linux/app.py`、`whalegirl_linux/web.html`。
   - 核心改动：`SafetyGuard.allow_login_action` 的 600 秒滚动窗口上限由 `3` 提升为 `7`，前后端提示同步更新。
4. **朋友圈自主发送分时段随机排期优化（`4b7daa6`）**
   - 涉及文件：`whalegirl_linux/moments.py`、`whalegirl_linux/moments.js`、`tests/test_moments.py`。
   - 核心改动：将每日活跃窗口（`moments_active_start_hour` 至 `moments_active_end_hour`）按 `moments_publish_per_day` 均分为等长时段，在各时段内按确定性随机种子挑选触发时刻，避免扎堆连发；单次发圈失败后自动为 `source` 追加 `#retryN`，防止唯一索引锁死当日后续重试；`moments.js` 动态校验升级为最长公共子序列 / 字符覆盖度容错匹配。
5. **群聊回复智能度与发图闭环修复（`85a91e6`）**
   - 涉及文件：`whalegirl_linux/context.py`、`whalegirl_linux/images.py`、`whalegirl_linux/image_tools.py`、`whalegirl_linux/runtime.py`、`tests/test_images.py`。
   - 核心改动：
     - `context.py` 新增 `asking_bot_image` 判定，群友索图或催促 `发啊/发一下/图呢` 时绝不回溯挂载前几分钟的群聊旧表情包；收紧 `caption`（45 秒内且在机器人上次回复之后）与 `visual_reference`（180 秒内且不再因单个“图”字触发）。
     - `runtime.py` 修正 `image_question` 正则（将 `有没有.*图` 改为 `图.{0,4}有没有`），区分看图问答与普通带图聊天；新增“空口发图（回复声称发图但 `image_request` 为 `null`）”服务端拦截与二次跟进补救；补充被群友指出“错误的/不对/你不懂”时禁止原样复读错话的系统约束。
     - `images.py` 与 `image_tools.py` 将候选图库容量扩至 `24`，支持中文重叠二元词（Overlapping Bigrams）+ 单字权重 + 同音别称扩展（`碗粥/瑞粥/晚粥 -> 挽昼`），`send_library_image` 支持 `asset_ids` 单轮连发最多 3 张图。
6. **四层自动内存清理与消息发送容错机制**
   - 涉及文件：`whalegirl_linux/gateway.py`、`whalegirl_linux/runtime.py`、`deploy/core_watchdog.py`、`tests/test_core_watchdog.py`。
   - 核心改动：
     - **第一层（微信内置浏览器 `WeChatAppEx` 永久压制）**：`core_watchdog.py` 每分钟检查容器挂载命名空间（`/proc/<cpid>/mountinfo`），自动通过 `nsenter ... mount -n --bind` 将轻量休眠桩绑定至 `/opt/wechat/RadiumWMPF/runtime/WeChatAppEx`，防止群内收到网页链接/小程序时拉起 6～11 个 Chromium 进程吞噬 `400+ MB` 内存并阻塞 AT-SPI 无障碍总线（`bus_99`）。
     - **第二层（超时挂起子进程自动收割）**：`core_watchdog.py` 每分钟自动清理超时卡死的 `a11y-dump`（>20s）、`xclip`（>90s）、残留 `unittest/pytest`（>300s）与 `crashpad_handler`。
     - **第三层（Python 核心进程内堆内存与缓存回收）**：`runtime.py` 新增 `trim_memory()`，每 30 秒空闲周期自动清理过期群成员缓存、过期 UI 任务 JSON 文件，并执行 SQLite `PRAGMA shrink_memory`、`gc.collect()` 及 `libc.malloc_trim(0)` 强制归还 glibc 堆页给操作系统；同时将 `safety_notice()` 全树探测降频至每 5 分钟一次。
     - **第四层（宿主机低内存水位回收与发送状态容错）**：`core_watchdog.py` 将 `vm.swappiness` 控制在 `20`，当宿主机 `MemAvailable < 320 MB` 时自动触发 `sync` + `drop_caches=1` 释放干净页缓存；`gateway.py` 将 `status_for_send` 缓存有效期扩至 30 秒并支持在瞬时超时时回退使用 180 秒内已知的 `logged_in` 状态。

## 6. 故障检查和安全发布流程

### 6.1 推荐检查顺序

先用 SSH 读取状态，**不先重启**：

1. systemd：核心 `ActiveState`、`MainPID`、`NRestarts`、`MemoryCurrent/High/Max/SwapMax`。
2. `http://127.0.0.1:8092/health`，必要时再看 `http://127.0.0.1:18092/health`。
3. 微信网关 `6175` 的登录状态 API；令牌只能在服务器进程内读取，输出只显示登录状态和账号是否存在。
4. Docker inspect：容器 ID、running、OOMKilled、RestartCount、固定版本。
5. 稳定性 `latest.json`、`tracking.json`、`observations.jsonl`，以及内核 OOM/cgroup 事件。
6. 最近 10–30 分钟 journal 错误的**类别和时间**，不要打印聊天内容或认证值。
7. 在线数据库只读检查是否有 `state='sending'` 的动作；有在途发送时不进行会中断该动作的核心重启。

### 6.2 更新代码时

- 当前部署脚本带特定前置条件，不是通用的一键升级工具。执行前阅读脚本、目标发布源码、生产 unit 的完整 drop-in 以及本节说明。
- 正常部署优先发布新不可变 release，再切换 `current`。本次稳定性脚本是精确修改活动发布的专项脚本，不要随意用作通用发布范式。
- 部署过程中先保留当前配置和源码备份，确认没有在途发送；改动范围限制在 Python 回复核心和监控。
- 必须保持微信容器 ID、版本、登录会话不变。此类核心更新一般只需重启 Python 服务。
- 部署后至少核对核心与入口健康、后台失败任务数、手机登录状态、容器重启数和 watchdog timer。
- 如果部署失败，立即看 `journalctl -u whalegirl-linux.service` 的异常类别和活动文件所有者/权限；不要重复重启或刷二维码。

### 6.3 观察与边界

- `whalegirl-stability.timer` 每分钟采样，支持区分微信掉线与机器人管理核心不可用。
- `whalegirl-core-watchdog.timer` 每分钟恢复核心；单次 HTTP 超时不会触发重启。
- watchdog 只提供有限的自动恢复，不是永久在线机制。它不能修复微信服务端限制、主机故障、网络中断、磁盘满或长期外部模型/API 失败。
- 不运行重负载压测，也不发送额外测试聊天；用户明确指定验收会话后再做真人收发验收。

## 7. 主要功能和当前实现范围

这部分描述仓库已有实现；具体生产开关以管理后台和服务器配置为准，不能由代码存在推定为线上已开启。

### 7.1 微信消息和自然聊天

- 按会话授权群聊/私聊；支持自然接话以及仅 @、关键词、对机器人拍一拍触发。
- 群聊连续发言做短暂防抖和合并；旁观消息进入上下文但不会必然触发回复。
- AI 可决定参与、保持安静、选择文字/原生收藏表情/引用等动作；回复节奏、人设温度、口语程度、吐槽、长度和表情积极性可在网页调整。
- 针对“拷打牢达”类严格唤醒群，遵守会话本身配置的 @、关键词和拍一拍条件。
- 不发送未经确认的重复回复；发送结果不确定时保存状态并等待人工核对。

专题：[CHAT_HABITS.md](CHAT_HABITS.md)、[CHAT_PERSONALITY.md](CHAT_PERSONALITY.md)、[REPLY_EXPRESSION.md](REPLY_EXPRESSION.md)、[AFFECTION.md](AFFECTION.md)。

### 7.2 消息读取、引用、转发和图片

- 当前 Linux 客户端提供的已同步消息由只读读取辅助进程查询；包括适用会话中可见的群消息、历史基线、引用和微信合并转发内容。
- 引用图按原消息 ID/会话绑定，尝试原始图片或客户端可见图像；取不到原图时保留失败状态，不用相邻图片冒充。
- 不是 Windows OCR 方案，也不能读取尚未同步、已删除或客户端没有保留的图片/消息。
- 群图片通常仍需 @ 或关键词等会话规则唤醒，且图像读取和完整回复耗时随媒体和模型而变。

专题：[CONVERSATION_V2.md](CONVERSATION_V2.md)、[QUOTES_AND_FORWARDS.md](QUOTES_AND_FORWARDS.md)、[MESSAGE_RECOVERY.md](MESSAGE_RECOVERY.md)、[MEDIA_DELIVERY_FIX.md](MEDIA_DELIVERY_FIX.md)。

### 7.3 长期记忆与好感度

- 群事件、关系和印象按会话分开；明确个人事实/偏好按真实微信 sender ID 共享，不能按相同昵称合并。
- 原始聊天、短期上下文、历史摘要和长期记忆分层；管理员后台可查询、编辑、归档、恢复或撤销修订。
- AI 只能在已授权记忆工具范围内检索与维护记忆，不能因此访问服务器文件或更改系统配置。
- 好感度可总开关控制，并按真实人物 ID 共享；可影响温和程度和个性化记忆优先级。群聊保持朋友边界，不因为好感度转成私聊式暧昧。

专题：[MEMORY_RECALL.md](MEMORY_RECALL.md)、[AFFECTION.md](AFFECTION.md)、[CHAT_HABITS.md](CHAT_HABITS.md)。

### 7.4 原生表情包

- 发送微信原生收藏表情，读回确认本人新消息、type 47 和 MD5；普通图片不能作为成功表情的证据。
- 表情带语义、画面、真实文字、情绪、语气、场景及示例标签；AI 可从获准会话接收的原生表情中选择收藏。
- AI 停用是从可选库隐藏，可恢复；微信收藏和资料保留。管理员可保护和编辑表情语义。
- 表情、文字分别计数且共用账号总额度；失败或状态不确定遵守发送保护，不自动重发。

专题：[STICKER_LIBRARY.md](STICKER_LIBRARY.md)、[STICKER_STUDIO.md](STICKER_STUDIO.md)。

### 7.5 图片库和 AI 生图

- 图片发送和生图是分开的模型工具；AI可自主选择图库、历史生成图、当前图像编辑或不调用。
- 大肥鱼外观描述和角色参考图分年龄/画风版本，后台可切换；生成本人形象时绑定角色描述与相应参考图。
- 生图供应商较慢，因此有独立任务队列和状态通知；AI 负责按上下文生成自然通知文字。
- 生图有每日配额；图库发送和复用历史生成图不等于新生图，不占生图次数。图片仍受素材范围、来源、幂等和发送确认约束。

专题：[IMAGE_TOOL_PLAN.md](IMAGE_TOOL_PLAN.md)、[IMAGE_TASKS.md](IMAGE_TASKS.md)、[REFERENCE_IMAGES.md](REFERENCE_IMAGES.md)。

### 7.6 朋友圈

- 代码实现朋友圈总开关、发动态、回复自己动态下评论、评论好友动态等独立开关和状态。
- 使用客户端本地同步的朋友圈数据和原生界面交互；数据不等于微信全量朋友圈历史。
- 发帖/评论须有真实动态或评论 ID 读回确认；结果不确定时不自动重发。
- 图库素材与生成图片可用于选择配图；主动生成频率、每日上限和内容去重有独立管理项。
- 评论和发帖所需 GUI 会占用客户端 UI 锁，可能与聊天发送互斥。

专题：[MOMENTS.md](MOMENTS.md)、[MOMENTS_REPLY_TIMELINE_FIX.md](MOMENTS_REPLY_TIMELINE_FIX.md)、[MOMENTS_IMAGE_DIALOG_FIX.md](MOMENTS_IMAGE_DIALOG_FIX.md)。

### 7.7 贴吧、联网搜索、角色卡与用量

- DeepSeek Search/Fetch 按需调用，每轮有限工具调用，聊天输出不显示内部引用标记；工具失败不伪装实时搜索成功。专题：[SEARCH.md](SEARCH.md)。
- 贴吧模块支持兴趣浏览、原文及图片收藏、后台梗/素材编辑和总开关；生产登录/采集状态应在网页另行确认。专题：[TIEBA.md](TIEBA.md)。
- 角色卡支持建立、编辑、预览、激活、归档与恢复；切换角色不清空长期记忆。专题：[PERSONAS.md](PERSONAS.md)。
- Token 用量账本记录供应商实际回报的输入/输出/缓存字段，不存提示词和完整图片请求。专题：[TOKEN_USAGE.md](TOKEN_USAGE.md)。

### 7.8 控制台和本地保护

- 管理网页管理模型、角色、人设提示词、会话/群规则、登录流程、记忆、好感度、表情、图片、朋友圈、贴吧及日志等。
- 本地设置发送额度、频率、重复内容拦截与不确定发送暂停；这些是本地保护，不是微信官方风控阈值，也不能保证账号永不受限。
- 微信手机端最终确认仍由用户在微信客户端完成；掉线后不应由 agent 盲目反复登录。
- 公网管理界面仅用已配置认证；禁止把管理密码写入文档或命令输出。

专题：[README.md](README.md)、[NAVIGATION.md](NAVIGATION.md)、[TOKEN_USAGE.md](TOKEN_USAGE.md)。

## 8. 下一步建议

按优先级接续，不需重做已经完成的部署：

1. **完成稳定性观察。** 持续查看 `tracking.json` 和 `latest.json`；达到实际连续 24 小时后，核对观察样本覆盖、容器 OOM 计数、微信进程身份、核心 health、重启计数与后台失败，再给出有限范围的结论。若中间出现采样缺口或登录/进程变化，按日志重新计算，不补算缺失时间。
2. **继续关注微信容器内存。** 最近容器样本约 750 MiB/1 GiB、交换区约 582 MiB，累计 `memory.events max` 非零但 `oom_kill=0`。短期先观察是否增长、容器使用是否继续逼近上限；不要在没有明确故障证据时重建容器。
3. **完善发布回归。** 对本次原子替换的 UID/GID/0644 行为补充独立单元测试；部署脚本目前重点测试了 watchdog 状态机，5 项测试通过，但权限回归测试尚未纳入自动测试。
4. **评估部署方式。** 当前稳定性专项部署脚本对活动发布作精确原子替换。后续通用功能发布继续用不可变 release + 软链接切换的方式，并在发布前后比较 effective systemd 配置；不要让历史脚本重设过低的资源阈值。
5. **功能改动按用户当前需求排期。** 若继续自然聊天、个人记忆、图像或朋友圈工作，先读相关专题文档和当前生产开关，再做小范围改动和针对性验证。

## 9. 建议的状态检查命令

本机用 PowerShell 调用 SSH。下面只展示非机密状态：

```powershell
ssh -o BatchMode=yes root@120.46.186.50 "systemctl show whalegirl-linux.service -p ActiveState -p MainPID -p NRestarts -p MemoryCurrent -p MemoryHigh -p MemoryMax -p MemorySwapMax"
ssh -o BatchMode=yes root@120.46.186.50 "systemctl show whalegirl-core-watchdog.timer whalegirl-stability.timer -p ActiveState -p SubState"
ssh -o BatchMode=yes root@120.46.186.50 "docker inspect --format '{{.Id}} {{.State.Status}} {{.State.OOMKilled}} {{.RestartCount}}' whalegirl-wechat-candidate-4118"
ssh -o BatchMode=yes root@120.46.186.50 "journalctl -u whalegirl-linux.service --since '30 minutes ago' --no-pager -o short-iso"
```

接口检查、登录状态以及 SQLite 只读核对可以通过服务器端 Python 小脚本完成。令牌从服务器本地读取后只放在请求 header 中；输出只显示结果类型，不打印令牌。

## 10. 测试现状

稳定性与下午功能迭代改动均已执行验证：

- `tests/test_core_watchdog.py`：5 项通过，覆盖连续失败阈值、15 分钟冷却、健康后清零、连续运行和采样间隔过长。
- `tests/test_images.py` 与 `tests/test_moments.py`：共 **45 项单元测试全部通过**（含新增的分时段随机排期、失败槽位重试、重叠分词+同音别称“碗粥->挽昼”召回、`send_library_image` 多图 `asset_ids` 入队、索图指令不回溯绑定历史旧表情包测试）。
- Prefix Cache 真实线上验证：多轮对话 `prompt_cache_hit_tokens / prompt_tokens` 命中率达 **78%–93%**。
- Python 编译检查通过，`git diff --check` 通过。
- 实际服务器核心健康接口（`127.0.0.1:8092/health` 返回 `status=ok, background_tasks=13, failed_background_tasks=0`）、公开手机入口 health、微信登录状态（`logged_in`）及 watchdog 定时器核对通过。

没有进行高负载压测、没有向微信发送真实测试消息、没有重启微信容器，也没有做新的 24 小时稳定性验收。

## 11. 相关文档索引

| 文档 | 内容 |
| --- | --- |
| `AGENTS.md` | 必须遵守的仓库操作要求 |
| `PROJECT_SUMMARY.md` | 项目能力、部署概览、已知限制 |
| `README.md` | 早期实现记录和架构细节；须注意其中按日期变化的历史状态 |
| `STABILITY_FIX.md` | 微信进程/内存/核心 watchdog 的故障与修复过程 |
| `CHAT_HABITS.md` | 消息缓冲、动作顺序、额度、记忆工具 |
| `CHAT_PERSONALITY.md` | 人设和自然语言风格调节 |
| `CONVERSATION_V2.md` | 群消息读取、引用图片和分群记忆 |
| `MEMORY_RECALL.md`、`AFFECTION.md` | 长期记忆召回和好感度 |
| `STICKER_LIBRARY.md`、`STICKER_STUDIO.md` | 表情收藏库、语义编辑、可恢复停用 |
| `IMAGE_TOOL_PLAN.md`、`IMAGE_TASKS.md`、`REFERENCE_IMAGES.md` | 图片工具、任务队列和角色参考图 |
| `MOMENTS.md` 及两个 `MOMENTS_*_FIX.md` | 朋友圈功能与近期修复记录 |
| `TIEBA.md` | 贴吧浏览、兴趣、原文及图片归档 |
| `SEARCH.md` | DeepSeek Search / Fetch |
| `PERSONAS.md` | 角色卡管理与提示词 |
| `TOKEN_USAGE.md` | Token、缓存命中统计口径 |

## 12. 最后提醒

- 生产配置、数据库、记忆、登录状态、API Key 和当前会话数据在服务器，与 Git 源码分离；恢复源码仓库不等于恢复生产数据。
- 不要因为管理入口正常就推断原生微信一定在线；也不要因为微信已登录就推断 Python 回复核心正常。两者分别检查。
- Docker 的容器 `memory.events max`、主机的核心 cgroup `high/max/oom` 是不同指标，须按对应 cgroup 解读。特别是此次已确认的故障发生在回复核心的内存高压线。
- 代码具备自动恢复能力不等于能“确保以后不再发生”。对外只报告实际观测窗口和证据，并清楚说明网络、服务器、模型接口及微信平台的边界。
