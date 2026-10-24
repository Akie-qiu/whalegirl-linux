# 鲸鱼娘 Linux 微信机器人交接文档

**交接时间：** 2026-10-24（北京时间）  
**目的：** 让接手的 agent 能从当前代码、Git 和服务器状态继续工作，避免重复部署、重复登录或误判已验收范围。  
**项目范围：** 仅 `D:\appData\qqbot\wechat-linux` 里的全 Linux 微信机器人。

> 这份文档的生产状态快照来自 2026-10-24 12:41（北京时间）附近的只读检查。服务可能在之后发生变化，接手时应先短暂复核服务器，不要把快照当作实时状态。  
> 本文不含 API Key、管理密码、SSH 私钥、微信登录数据或私人聊天原文。

## 1. 交接摘要

- 微信固定使用 **4.1.1.8**，运行在 Linux Docker 容器中；回复核心、原生读取辅助服务和管理入口也在 Linux 服务器常驻。
- 私有 GitHub 仓库为 `Akie-qiu/whalegirl-linux`，当前工作分支是 `backup/local-snapshot-20261024`；本次稳定性代码基线为 `d0e2bac`，交接文档会作为后续提交保存。
- 2026-10-24 故障的第一原因是回复核心的 cgroup 内存高压阈值低于实际占用，Python 核心因此卡在内存回收路径，健康接口超时。微信容器当时仍正常、仍登录，没有 OOM kill。
- 稳定性优化已部署：回复核心的有效限制为 `MemoryHigh=384M`、`MemoryMax=512M`、`MemorySwapMax=512M`；Uvicorn 有界并发；每分钟独立检查核心健康。
- 部署过程中出现过一次由文件权限造成的启动失败：`__main__.py` 被写为服务用户不可读，造成 `PermissionError` 和 systemd 重启。根因已定位、修复并再次部署；部署脚本现保留原子替换目标的所有者，并保证 Python 源文件服务用户可读。
- 最近检查时核心与网页健康接口都返回 200，微信登录状态为 `logged_in`，机器人启用且未暂停；微信容器 ID 未变、重启计数为 0。
- **不能宣称长期稳定性已验收。** 24 小时连续观测仍未达到；截至 12:41，当前在线段约 22 小时 36 分。服务器持续采样由 systemd timer 执行，不依赖本机电脑。
- 微信容器自身仍有较高内存/交换区使用记录，虽然当时 `oom=0`、`oom_kill=0`，后续应继续观察。

## 2. 首要操作规则

开始工作前先读：

- [AGENTS.md](AGENTS.md)：命令、删除、服务器、微信版本和数据处理要求。
- [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md)：项目功能概览与已知边界。
- 与要修改功能对应的专题文档；本文件第 8 节列出索引。

必须保留以下约束：

1. 本地使用 PowerShell / `pwsh`，不使用 Windows `cmd`。
2. 删除任何文件、容器、旧发布、备份或数据前，先向用户说明具体目标并逐项检查删除命令；没有明确授权时保留。
3. 不重启或重建微信容器来修 Python 核心问题。优先只处理 `whalegirl-linux.service`。
4. 固定使用微信 4.1.1.8；不自动升级、不盲目重复扫码或请求手机确认。
5. 未指定验收会话前，不向真实群聊、私聊或朋友圈发送测试内容。
6. 访问在线 SQLite 数据时使用只读连接；不要用会执行迁移或恢复写操作的 Store 对生产库做检查。
7. 不把密钥、管理凭据、登录令牌、二维码、聊天原文或图片 base64 写入 Git、日志或交接文档。
8. 不修改 QQ 项目或 QQ 服务器部署；它们不属于本仓库任务范围。

## 3. 仓库与 Git 状态

### 本机位置

- 项目 Git 根目录：`D:\appData\qqbot\wechat-linux`
- `D:\appData\qqbot` 是工作区父目录，**不是 Git 根目录**。
- 本机的临时部署归档位于父目录 `D:\appData\qqbot\artifacts\`，该目录被忽略，不属于源码仓库。

### 私有 GitHub

- 仓库：`Akie-qiu/whalegirl-linux`（私有）
- 当前分支：`backup/local-snapshot-20261024`
- 当前 HEAD / origin HEAD：`d0e2bac` — `Repair readability on interrupted stability deploy`
- 前序优化提交：
  - `9aa7b74` — 核心资源限制、健康检查和 watchdog 初版
  - `8c10aa2` — 修正登录状态校验地址
  - `604e5bf` — 原子部署时保留目标所有权和可读性
  - `d0e2bac` — 修复中断部署后已打补丁文件不可读的恢复路径
- `2ee9de5` 是 2026-10-24 的源码快照基线。它和后续稳定性提交都保留在当前备份分支。
- `main` 分支停留在较早的项目归档快照；继续当前工作应从备份分支开始，不要把较旧 `main` 当成生产现状，也不要改写快照提交。
- 当前文档尚未提交；提交前检查 `git status`、差异和敏感文件，随后推送到这个私有备份分支。

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

稳定性改动已执行：

- `tests/test_core_watchdog.py`：5 项通过，覆盖连续失败阈值、15 分钟冷却、健康后清零、连续运行和采样间隔过长。
- Python 编译检查通过。
- `git diff --check` 通过。
- 实际服务器核心健康接口、公开手机入口 health、登录状态及 watchdog 定时器核对通过。
- 更新前确认没有 `inbox` 或 `outbound_actions` 处于 `sending`。
- 部署后的约 45 秒二次观测显示 PID 和重启计数稳定、核心返回正常。

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
