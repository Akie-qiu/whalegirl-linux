# DeepSeek 酱 / 鲸鱼娘 Linux 项目完整交接说明

更新日期：2026-11-05（北京时间）  
用途：交给 Claude，作为项目架构、线上状态、功能边界、测试证据与后续工作的总入口。  
本地项目：D:/appData/qqbot/wechat-linux  
GitHub：Akie-qiu/whalegirl-linux  
生产平台：Linux 云服务器、固定版 Linux 微信 4.1.1.8、Python/FastAPI、SQLite。

> 本文区分线上生产、Git 提交、本地未提交工作和仅有计划的项目。文件存在不代表功能已上线。

---

## 0. 一页摘要

这是一个运行在 Linux 云服务器上的微信 AI 机器人。Linux 微信客户端负责真实收发；Python 服务负责消息理解、模型/工具编排、记忆成长、媒体能力、管理控制台和运行状态。代码与长期数据、凭据分开保存。

### 当前线上快照

- 当前 release：/opt/whalegirl-linux/releases/bilibili-spoken-learning-20261105-185540-772344278
- 回滚目标：/opt/whalegirl-linux/releases/internet-growth-20261105-exploration-tools-1793844500411647187
- 最近部署只发布口语学习相关的有限文件，没有把本地全部修改整体部署。
- 部署后核验：health=ok；后台任务失败数=0；微信已登录、机器人开启、没有回复暂停；B站已认证且可用；微信进程、读取器、容器及受保护配置保持。
- 部署时磁盘可用约3.331GiB，是当时的时点值，不代表当前容量。
- Git HEAD 为 d76599b（2026-11-05 10:30 +08:00）。生产 release 包含 Git HEAD 之后的口语学习补丁。
- 本地工作区存在大量未提交文件。禁止直接 reset、覆盖工作区、把整个目录部署到服务器，或把所有本地代码视为已验证。

### B站原始需求完成度

账号登录、只读访问、探索任务、字幕/画面/评论/弹幕读取、成长记忆与后台管理的主要基础已实现并上线。确实参考了用户指定的人文成长仓库，具体提交、借用内容与未引入的部分记载于 THIRD_PARTY.md 及 vendor 目录。

实际效果仍未达到“看完视频稳定学到真人说话，并明显改善日常聊天”。最近真实材料隔离复测生成3条一般认识、0条核验通过的字幕口语表达。无音频ASR，字幕不能证明说话人身份或双人对话。现在不能宣称已经因此像群友一样说话。

原始要求希望机器人主动谈起观看经历；用户后来明确要求B站/贴吧学习记忆与微信私人事实隔离。当前只允许经证据、权限与独立开关控制的泛化表达参考跨域，观看经历、互联网人物和事件不会自动进入日常聊天。

### 建议接下来做的事

1. 逐文件比较本地未提交代码、Git HEAD 与线上 release。
2. 继续寻找确实有口语字幕的视频材料，不降低证据规则。
3. 用留出聊天验证“表达确实被投影并改善回复”，不能只测试数据库写入。
4. B站评论@及微信指定视频目前未上线；先验证真实账号只读接口。
5. 部署前重新检查磁盘和备份空间，采用精确文件清单。

---

## 1. 目标与生效中的产品边界

DeepSeek酱/鲸鱼娘以用户提供的核心角色卡为身份基础。模型负责理解、判断是否参与、措辞和选择工具；代码提供来源可靠的资料、权限边界、队列、费用与资源保护、真实发送回执。学习不能任意改写角色卡或管理配置。

用户对设计做过多次后续调整，后续明确要求优先于旧计划：

- 当前话题优先，避免旧记忆把回复拉回无关旧事。
- 用户最近要求最近100条完整聊天；此相关更改在本地有未提交工作，是否生效要读生产设置确认，不能当作已上线事实。
- B站、贴吧互联网内容与微信私人事实隔离；两来源成长记忆可统一，但不把外部经历伪装成群友事实。
- B站表达经验、贴吧表达经验有独立跨聊天开关和逐项权限。
- 重点是从视频材料学习真实语料、情绪与表达适用场景，不靠随机卖萌、强制口头禅或堆提示词。
- B站账号只读；禁止自动评论、点赞、投币、关注、收藏、发布。
- 不恢复已撤销的自主聊天旧引擎。
- 测试群合成内容不能当作真人经历。

---

## 2. 参考仓库和第三方来源

### 用户指定的人文成长仓库

InariNGT-Ofiicial/dsh-bili-humanism，固定参考提交 2ec50417e64a72a28b9445c06872d7f14d9dbf24，MIT。来源记录在 THIRD_PARTY.md 和 whalegirl_linux/vendor/humanism/UPSTREAM.json，许可文本在 vendor/humanism/LICENSE。

实际借鉴：
- 核对材料来源与真实覆盖范围；
- 区分材料事实、作者观点、推测与未覆盖部分；
- 带证据、可修订的反思；
- 风险时持久暂停；
- 把公开 WBI 签名算法 lib/wbi.mjs 适配到 Python bilibili_client.py。

没有引入 DSH 运行时、浏览器凭据提取器或B站写入执行器。

### 其他来源

- SAOS-3624/dsh-see-now 与 dsh-video-storyboard：参考 HSV 差异、镜头切换、运动累计、关键帧和分镜组织；有限资源 Python 适配。固定提交与 Apache-2.0/NOTICE 见 THIRD_PARTY.md、vendor/scene-vision。
- Nozzi2455/fishtalk：固定版本、提交和许可证见 THIRD_PARTY.md、vendor/fishtalk。适配的是已有口语节奏/套话辅助；不能把依赖本身说成已证明自然度提升。
- thisnick/agent-wechat：以固定提交的 Git 子模块 references/agent-wechat-source 提供 Linux 微信网关和 GUI guard 基础。
- linxisama/deepseekgirl：早期 AIEngine、StyleManager 与角色基础来源，改造范围见 THIRD_PARTY.md。
- 仓库需递归克隆子模块。Python依赖在 requirements.txt 与 lock 文件中。

---

## 3. 架构

~~~mermaid
flowchart LR
    WX[Linux 微信 4.1.1.8] --> GW[原生网关与 UI 适配]
    GW --> RD[只读消息读取器]
    RD --> CORE[Python / FastAPI 核心]
    CORE --> CTX[消息、身份、引用、媒体上下文]
    CTX --> MODEL[模型与工具编排]
    MODEL --> MEM[MemoryHub / Growth / 记忆]
    MODEL --> STICKER[原生表情]
    MODEL --> IMAGE[图库、生图、视觉资料]
    MODEL --> WEB[联网搜索与贴吧]
    MODEL --> BILI[B站只读探索]
    MODEL --> MOM[朋友圈]
    MODEL --> SEND[发送队列与回执]
    SEND --> GW
    CORE --> DB[(SQLite 与私有文件)]
    ADMIN[管理网页] --> CORE
    WATCH[health / watchdog / 采样] --> CORE
~~~

运行时是多个有界组件：Linux微信容器；原生收发网关；只读消息读取器；Python FastAPI核心；SQLite与私有配置/媒体；管理网页；健康检查和watchdog。微信在线、读取器在线、核心健康、模型可用、消息送达是不同状态，不能互相替代。

---

## 4. 消息链路

1. 网关/读取器读取新消息、会话、发送者唯一标识、服务器时间、@/唤醒、引用/转发、图片/表情元数据。
2. Runtime根据会话授权、总开关、静默、唤醒条件和暂停状态决定是否入队。
3. 连续消息缓冲合并，减少半句话就触发模型。
4. Context组装近期上下文、相关历史、人物偏好、会话记忆、图片/引用资料及获准成长参考。
5. 主模型产生回复动作，或选择联网/记忆/图片/贴吧/B站等工具。额外模型续答只发生在模型确实调用工具时。
6. 格式适配和发送保护执行平台动作。
7. 原生微信发送后读取本机消息记录核验本人新消息和媒体类型。
8. 已确认结果记入任务和对话状态；发送结果不确定时记录服务事件并人工核对，不盲目重发。
9. 明确失败有恢复/暂停流程。恢复动作与微信客户端登录不是同一件事。

---

## 5. 源码地图

### 核心与对话
- app.py：FastAPI、生命周期、认证和管理接口。
- runtime.py：服务容器、消息处理、异步任务、队列与事件循环。
- conversation.py：对话、模型/工具往返和动作编排。
- context.py、message_payload.py、visible_media.py：上下文、消息身份/时间、引用/转发/媒体内容。
- autonomy.py、proactive.py、proactive_tools.py：参与、主动聊天和意图工具。必须区分线上行为与本地候选修改。
- model_reply.py、upstream/ai_engine.py：模型适配。
- chat_control.py、topic_relevance.py：会话规则与话题控制。

### 记忆、学习、人设
- memory.py、memory_hub.py、memory_layers.py：记忆读写、范围、来源、分层与修订。
- experience.py、learning.py：聊天经验学习与经验投影。
- growth.py、growth_memory.py：互联网成长队列、来源反思、认识和权限。
- expression_reference.py：核验后的表达经验筛选与聊天参考；检查release清单确定具体线上版本。
- fishtalk.py、fishtalk_rules.py：Fishtalk表达工具。
- affection.py：关系与人物好感度。
- personas.py、styles/：角色卡和人设管理。

### 发送、微信、保护
- gateway.py：Linux微信网关客户端。
- delivery.py、fast_send.py、reply_pipeline.py：动作发送、格式和状态流转。
- recovery.py、safety.py、stability.py：失败恢复、发送安全、健康状态。
- store.py：SQLite持久化、消息和任务数据。生产检查数据库用只读方式，不对运行库启动写入型Store初始化。

### 媒体与其他能力
- stickers.py、sticker_library.py、sticker_assets.py：原生表情索引、含义和发送。
- images.py、image_tools.py、image_references.py、image_canvas.py、image_retention.py：图库、生图、参考图、缓存保留。
- moments.py、moments_creativity.py、moments_world.py、moments_payload.py：朋友圈、剧情世界和素材。
- search.py、time_context.py：事实搜索及服务器时间。
- tieba.py、tieba_tools.py、tieba_archive.py、tieba_retention.py：贴吧只读、分享、工具和材料。
- bilibili_client.py、bilibili_reader.py、bilibili.py、bilibili_tools.py：B站登录、读取、成长选择及聊天工具。
- video_scene.py、video_media.py：视频关键帧/分镜和限资源媒体处理。

### 管理网页
web.html为主体；console.js/css管管理框架；growth.js、bilibili.js、tieba.js、personas.js、proactive.js、images.js、moments.js、memory-hub.js、fishtalk.js、sticker-library.js等负责面板。

成长与B站API可从app.py检查 /api/growth、/api/growth/run、/api/growth/insights、/api/growth/journals、/api/growth/sources、/api/bilibili、/api/bilibili/account、/api/bilibili/readings。请求使用认证会话。交接文档不保存任何token。

---

## 6. 主要功能现况

### 6.1 微信聊天与上下文
- 授权私聊及群聊；按会话设置自然接话或仅唤醒、@、关键词、静默和工具权限。
- 处理文字、图片、原生表情、引用和合并转发；发送者尽可能按真实微信ID识别，不以显示名作为唯一身份。
- 每条消息使用服务器时间。
- 当前话题优先；历史用来理解指代和相关记忆，不应盖过当前问题。
- 最近上下文长度由配置控制。最近100条完整消息的需求存在未提交改动，生产现值需要安全读取确认。
- 普通联网搜索独立于聊天模型设置，换模型不应把其他模型API URL/Key/名称重置。
- 回复温度和其他参数需核实供应商接口是否真正接受；界面显示值不等于生效值。

### 6.2 人设与自然表达
- 角色卡管理核心身份、性格、关系和世界设定；学习材料不能覆写核心人设。
- 关系类偏好可按真实微信ID区分；群聊保持群体语境，不将所有人叫“主人”。
- 有消息格式适配、表情动作、人设浓度/短句相关设置和套话处理。
- Fishtalk和成长经验不是模型微调，也不保证一键让回复像真人。
- 自然度结论需基于同输入留出对照与盲评；不能把一次漂亮回复作为验证。
- 本地有自然聊天、上下文、记忆等多个未提交文件；不等于已部署。

### 6.3 记忆和成长
- 复用统一记忆存储、来源、范围、修订；当前聊天记忆、人物事实、群事件与互联网经历需分域授权。
- 可在后台检索、编辑、停用、恢复、锁定或查看修订；逻辑归档不代表原始聊天已被物理删除。
- 互联网成长用来源和证据关联，不自动进入私人聊天。
- 扩展阅读/兴趣不能凭一条玩笑固化事实。

### 6.4 B站探索和视频理解

已部署的主要底层：
- 官方扫码登录与匿名备用；服务端加密存储；管理页状态/退出。
- B站访问固定只读GET白名单；风险、登录失效时停止，不自动绕过或写入平台。
- 推荐、搜索、热门、关注、收藏、历史等候选来源在实现中；AI按已取到的目录选择入口、视频、真实分P或跳过；私有入口需登录。代码支持不代表每种入口都已在线逐项验收。
- 视频身份与CID/分P绑定；读取带时间码字幕、稀疏真实快照、评论/楼中楼、弹幕；长视频按时间窗口分段并记录覆盖和缺口。
- 动态关键帧借鉴See Now/storyboard，以HSV、镜头变化和运动差筛帧；受限FFmpeg可用时按需处理，失败回退到快照。
- 不连续播放、不下载无限量视频、不做ASR/OCR。没有字幕不声称听到了语音；标题、封面、评论不等于看懂视频。
- 普通聊天和主动聊天可以调用 explore_bilibili；它创建异步成长任务，不阻塞当前回复；普通聊天并非每轮多一次成长审查调用。
- 成长复用 GrowthService/MemoryHub/MemoryBook；B站和贴吧资料处于互联网成长范围，微信私人事实单独隔离。
- 取消累计Token预算和B站每日视频数上限；依旧保留滚动网络请求频率、任务串行、任务时限、单次模型max_tokens、上下文窗口、资源限制和平台风控。Token消耗继续统计；费用未由供应商回报时应显示未知。
- 后台有来源开关、账号、兴趣/任务/阅读与成长管理以及记忆权限控制。

效果限制：
- 最近5份生产来源材料里，3份有字幕、2份无字幕；它们不等价于5个独立视频完整观看。
- 最新隔离模型复测使用缓存的科普材料，1个请求约18.4秒，输入10049、输出2413、总12462 Token；得到3条一般认识，0条已核验字幕口语表达。
- 一个旧真实任务因length输出截断失败，失败历史保留。反思max_tokens后续增至8192，同缓存材料隔离复测成功；不能称为新的一次全程线上视频浏览。
- 目前没有“口语学习已经让群聊自然度变好”的证据。

原始B站需求矩阵：

| 需求 | 当前状态 |
|---|---|
| 扫码、登录态、状态、退出、匿名、失效处理 | 主体已上线；主动提醒渠道需确认 |
| 推荐/搜索/关注/收藏/历史探索 | 实现存在；并非所有账号私有接口逐一实测 |
| 视频字幕/画面/评论/弹幕理解 | 已实现有限读取和覆盖留档；不是完整音视频理解 |
| 动态兴趣和经历记忆 | 机制接入统一成长；持续效果未验证 |
| 成长对后续聊天的影响 | 受互联网/微信隔离限制；仅允许的表达投影可共享 |
| 自然谈起观看经历 | 按后续隔离要求没有自由跨域注入，未完全满足最初设想 |
| 后台管理 | 主体已上线；生产具体开关以实际查询为准 |
| B站评论@和微信指定视频 | 有本地方案/测试；生产未包含，账号通知接口待实测 |

### 6.5 贴吧
项目保留贴吧工具、阅读/分享/归档与成长资料。贴吧学习与贴吧表达共享分别有开关；早期要求贴吧学习默认关闭，实时值须查服务器。贴吧原文不应伪装成群友发言。正常聊天联网搜索保留，通用网页搜索不再独立作为成长源。

### 6.6 原生表情
- 使用微信原生收藏素材及ID/MD5/语义标签；发送后读回确认，不把普通图片算作原生表情。
- AI可选择纯文字、纯表情或混合；积极性是偏好而非每条硬性比例。
- 管理员可管理素材、停用/恢复，发送失败区分未过发送边界与结果不确定。
- 上次部署报告记录当时可用75、已知249，仅为历史快照。

### 6.7 图片、生图、图库
- 图片解析依赖真实消息来源绑定、缓存/文件状态和视觉接口；缺图时不能编造内容。
- 图库与生图是不同工具：查找已有资产不应触发新画图；新图生成是单独任务队列。
- 生图任务包含提示词、模型接口、尺寸/比例、参考图、生成、校验、发送与结果记录。
- 朋友圈多角色参考图要核验请求确实携带对应图像ID，不能只看UI选项。
- 存储需关注原图、参考图、历史生成、临时下载与备份。

### 6.8 朋友圈
- 有第一人称朋友圈文案、世界观角色设定、配图参考与发布/评论回复工具。
- moments_world.py/世界文档负责角色和关系设定，后台可维护内容。
- 发布状态须以真实朋友圈记录核验；“已提交”或UI报错不等于已发/未发。
- 短期示例不代表持续剧情已验证；重复度需用真实历史审计。

### 6.9 模型、搜索、时间、后台
- 模型卡分别保存API接口、Key、模型名及生成参数；密钥绝不输出。
- 独立联网搜索服务用于正常聊天事实核实，不依赖选择的聊天模型。
- 服务器时间进入消息上下文。
- 后台涵盖模型、人设、会话、记忆/成长、B站/贴吧、表情、图片、生图、朋友圈、运行状态和故障处理。
- Health和本地限额不能保证外部模型、微信账号或B站永不掉线/触发风控。

---

## 7. 2026-11-05口语学习发布范围

本次仅把口语证据链相关的有限修改部署：
- growth.py材料抽样与反思；
- growth_memory.py表达证据注册；
- expression_reference.py；
- bilibili.py选片提示与经验输入；
- growth.js摘要展示；
- 两份专项测试。

普通聊天入口、GrowthService任务入口与聊天召回主体保留生产原样；B站评论@及广泛自然聊天/上下文本地改动不在本次部署包内。

学习机制：
- 字幕保留连续片段的原始行号/时间码/邻接关系。
- 只有逐字和时间/顺序检查通过的字幕表达，才能列入口语摘要。
- expression_summary是综合表达观察，可含评论/弹幕，与字幕口语摘要分开展示。
- 不推断说话人身份，不让评论冒充台词。
- 普通微信聊天没有新增审稿模型调用。
- 曾有真实任务length截断，失败记录保留；反思max_tokens提高至8192后，用相同缓存材料做隔离复测。
- 隔离复测结果不是新的线上浏览，也没有发微信或写生产记忆。

备份：
- 配置：/var/backups/whalegirl/spoken-learning-config-20261105-185538-809688879
- 数据库快照：/opt/whalegirl-linux-assets/bilibili-spoken-learning-before-20261105-185540-772344278/state-before.sqlite3，约443.93MiB，0600权限，quick_check正常。
- 本地回滚：deploy/rollback_spoken_learning.ps1
- 回滚不整库回灌，保留发布后新聊天和成长数据。

---

## 8. 数据、安全与范围

- 微信聊天、个人ID、人物关系和群体事件属于微信范围。
- B站/贴吧材料属于互联网成长范围。
- 跨域表达须通过来源证据、权限、独立开关和投影过滤；不传原文、作者身份或私人经历。
- 昵称不能作为唯一身份；机器人自己的消息不能成为真人文风证据。
- API Key、Cookie、登录态、管理Bearer token、二维码、私聊内容、原始数据库和带图片Base64的任务JSON不可交给Claude。
- 生产SQLite仅只读检查；迁移先在副本验证。
- 未经授权不发真实微信测试、不发朋友圈、不对B站写入。
- 删除数据/备份前，先报告删除内容和具体命令，详细检查目标范围；无授权则保留。

---

## 9. 测试、发布、回滚

证据应按版本分别解释：
- 本次合并候选口语补丁47项定向测试通过；输出额度补丁后另有15项相关测试。二者有重叠，不可加成62个独立用例。
- 更早互联网成长整合有196项Linux回归与隔离/界面检查；不是本次口语效果证明。
- 自然聊天回放位于 artifacts/focus-evaluation-20261105/，是隔离评估，不是真实生产群聊或长期人格效果。
- 欠缺是实际样本没有形成可共享口语经验，因此没有验证到“表达输入让回复更自然”的因果链。

本地PowerShell测试：

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest tests/test_video_speech.py tests/test_expression_reference.py -q
~~~

工作树本身有未提交改动，结果只代表当前工作树。

发布流程：
1. 查Git状态、HEAD、子模块、生产release与文件hash。
2. 只发布批准文件清单。
3. 查磁盘和DB快照容量，安全保存源码、配置及一致性快照。
4. 数据迁移先在DB副本验证兼容回滚。
5. 常规只重启Python核心，不重启微信容器/原生读取器。
6. 上线后核对health、失败任务、微信登录/启用/暂停、B站认证/风控、设置hash与磁盘。
7. 分开报告单元测试、真实账号链路与长期聊天效果。

---

## 10. Git与未提交状态

最新HEAD为d76599b。当前工作区脏。之前只读检查到修改文件包括：

- 自主聊天与记忆：autonomy.py、context.py、experience.py、fishtalk.py、growth.py、growth_memory.py、learning.py、memory.py、memory_hub.py、message_payload.py、proactive.py、runtime.py、settings.py、store.py、topic_relevance.py。
- B站和管理网页：bilibili.py、bilibili_client.py、bilibili_reader.py、bilibili_tools.py、bilibili.js、growth.js、console.js、web.html、improvements.js。
- 多份测试以及未跟踪的B站@计划、口语学习/自然度报告、部署与评测脚本。

最新生产部署明确没有包含B站@和广泛聊天改造。本地差异不能一概认为与线上一致。Claude开工前逐文件diff并保护本地工作；不得统一格式化或清理测试产物。

建议阅读顺序：
1. AGENTS.md、README.md、THIRD_PARTY.md；
2. 本文；
3. SPOKEN_LEARNING_DEPLOYMENT_20261105.md；
4. BILIBILI_SPOKEN_LEARNING_20261105.md；
5. INTERNET_GROWTH.md；
6. BILIBILI_MENTION_READ_PLAN_20261105.md；
7. NATURAL_CHAT_FOCUS_20261105.md、CHAT_NATURALNESS_PRESSURE_TEST_20261105.md。

---

## 11. Claude首次接手检查清单

- 只读执行git status --short、git log、git diff --stat和子模块状态。
- 对比生产release、Git HEAD和本地工作树源码hash。
- 只读取白名单生产状态；不要输出settings、env、密钥或聊天正文。
- 核验上下文上限、B站/贴吧学习及表达共享开关、B站风控暂停与任务队列。
- 检查expression_reference是否实际挑选了允许的表达，并进入模型最终请求。
- 用隔离留出输入做有/无表达参考对照，不发微信，保留匿名的输入投影、输出和盲评。
- B站评论@先做账号只读接口核验；不要启用轮询、倒灌旧通知或部署。
- 写生产、部署、推送GitHub、发送消息或平台写入都须符合用户当次授权。
- 报告中区分源码、测试、部署、真实账号验证和长期效果验证。
- 删除前向用户报告对象与命令并详细审查路径；没有授权不删。

### 绝对不要

- 复制/公开/提交API Key、管理token、Cookie、微信登录态。
- 将群聊私聊原文、数据库或Base64媒体粘贴给Claude。
- 对生产库运行写初始化或整库回灌旧备份。
- 未经授权删除备份、数据或release，部署、推GitHub、发真实消息、发朋友圈或写B站。
- 把单元测试、模拟回放、匿名连接或隔离模型探针说成长时间真实效果。
- 仅凭过时文档或本地文件推断生产开关和线上状态。
