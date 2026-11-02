# 小鲸鱼（WhaleGirl）去人机感与破冰升温改造计划（含服务器完整实况 · Claude 零背景交接版）

编写日期：2026-11-01（北京时间）  
适用仓库：`D:/appData/qqbot/wechat-linux`  
目标交接对象：Claude（假设当前对本地仓库与线上服务器**完全零背景**。本文档已完整写入**线上服务器目录拓扑、真实运行配置参数、激活角色卡精确 ID 与 22 节结构、模型槽位、数据库真实表字段、服务端独有合并差异及管理 API 免代理调用方式**，可直接照单施工）

---

## 1. 本地与服务器全景清单（零背景必看）

### 1.1 项目定位
这是一个运行在 Linux 生产服务器上的长期在线微信机器人项目（角色：**小鲸鱼 / deepseek酱**，微信侧昵称示例：沃不明白）。
- **底层微信收发**：固定使用 Docker 容器内的原生 Linux 微信 **4.1.1.8** + 原生只读消息读取服务 + `AgentGateway` UI 适配网关（默认 `127.0.0.1:6175`）。
- **Python 核心编排**：Python 3.12 + FastAPI（绑定 `127.0.0.1:8092`，外部管理面板经反代/隧道通常在 `18092`），启动 19 个后台异步任务，负责消息防抖、40 条近期上下文、分层记忆、好感度、多协议大模型调用、原生表情包/图库/生图/贴吧/朋友圈调度及真实发送回执确认。
- **接手前必读的 3 份本地文档**：
  1. `AGENTS.md`（10 条操作铁律）
  2. `PROJECT_HANDOFF_20261101.md`（架构、模块与业务全貌）
  3. `DEPLOYMENT_20261101.md`（2026-11-01 20:11 刚完成的部署记录与服务端差异合并说明）

### 1.2 本地与生产服务器精确路径对照表

| 维度 | 真实路径 / 精确值 | 关键注意事项 |
|---|---|---|
| 本地工作区 | `D:/appData/qqbot/wechat-linux` | Windows 环境，**严禁用 `cmd`，一律用 PowerShell (`pwsh`/`powershell`)** |
| 本地 Python | `D:/appData/qqbot/wechat-linux/.venv/Scripts/python.exe` | 裸 `python` 不可靠；测试用标准库 `unittest`（未安装 `pytest`） |
| 生产服务器 SSH | `root@120.46.186.50` | 已配置免密登录；网络握手偶有 5～12s 延迟，建议带 `-o ConnectTimeout=30 -o ServerAliveInterval=15` |
| 服务端程序根目录 | `/opt/whalegirl-linux` | 含 `current` 软链、`releases/` 历史发布、`venv/` 生产虚拟环境 |
| **服务端 Python 解释器** | `/opt/whalegirl-linux/venv/bin/python` | 在服务器上跑脚本或测试一律用此解释器 |
| **当前生产软链接** | `/opt/whalegirl-linux/current` | 指向 `/opt/whalegirl-linux/releases/humanization-bugs-20261101-20261101-201156-558562461` |
| 上一保留发布 | `/opt/whalegirl-linux/releases/soul-review-fixes-20261031-20261031-162459-29669` | 严禁删除旧发布 |
| 服务端备份与暂存区 | `/opt/whalegirl-linux-assets` | 最近一次完整备份：`humanization-bugs-20261101-before-20261101-201156-558562461` |
| **服务端运行用户** | `whalegirl-ai` | `whalegirl-linux.service` 以 `whalegirl-ai` 身份运行！新发布目录权限必须 `0755`、文件可读且 `chown` 对齐 |
| 核心 Systemd 服务 | `whalegirl-linux.service` | 仅更新核心代码/配置时只重启这一个服务 |
| 微信容器与读取服务 | 容器 `whalegirl-wechat-candidate-4118` + `whalegirl-native-reader.service` | **严禁随意重启或升级！** 必须保持原生微信 `4.1.1.8` 的 `logged_in` 会话不掉线 |
| **服务端私有数据目录** | `/var/lib/whalegirl-linux/core` | 权限 `0600`/`0700`，严禁入 Git，严禁泄露任何密钥文件 |

### 1.3 服务器 `/var/lib/whalegirl-linux/core/` 目录内的真实文件与用途
经线上实测盘点，该目录下存在以下核心文件（修改任何配置前必须先完整了解它们的联动关系）：
1. **`state.sqlite3`（及 `-wal` / `-shm`）**：**唯一真实生产 SQLite 数据库**！（⚠️ 同目录下有一个 0 字节的旧占位文件 `state.db`，**千万不要连错成 `state.db`**！）
   - 线上只读查询铁律：**必须用 `sqlite3.connect('file:/var/lib/whalegirl-linux/core/state.sqlite3?mode=ro', uri=True)`**，**严禁**为了查数据在生产目录实例化 `Store`、`Runtime` 或 `Config`（会自动触发 schema 迁移、改写恢复状态或触碰凭据文件）。
   - 常用表真实字段提示（避坑）：`inbox` 状态列叫 `state`（非 `status`），时间列叫 `timestamp` / `received_at`（均为 Unix 秒级浮点数）；`model_token_usage` **没有** `finish_reason` 列（`finish_reason` 存在 `reply_metrics.metrics` JSON 的 `model_response.finish_reason` 里）。
2. **`settings.json`**：当前生效的全局配置 JSON。
   - ⚠️ **字段名注意**：里面保存编译后系统提示词的键名叫 **`system_prompt_override`**（配套 `active_persona_id` 与 `active_persona_revision`），主人微信 ID 列表键名叫 **`owner_ids`**！
3. **`persona-cards.json`**：角色卡仓库，顶层结构为 `{"version": ..., "cards": [...]}`。
   - 每项结构为：`{"id": "role-...", "revision": N, "builtin": false, "archived": false, "updated": ..., "card": {"name": "...", "tagline": "...", "avatar": "🐋", "accent": "sea", "mode": "raw", "raw_prompt": "...", ...}}`。
   - ⚠️ **联动机制**：通过管理 API（`POST /api/personas` 保存 + `POST /api/personas/{id}` 执行 `{"op": "activate", "revision": ...}`，详见 `whalegirl_linux/personas.py` 与 `app.py`）激活卡片时，系统会自动通过 `compile_persona()` 编译并写入 `settings.json` 的 `system_prompt_override`，同时递增 `config.generation` 热重载模型引擎！如果离线改文件，**必须同时更新 `persona-cards.json` 里的 `raw_prompt` 和 `settings.json` 里的 `system_prompt_override`**，否则两边会脱节！
4. **`model-profiles.json` + `model-api-keys.enc` + `encryption.key`**：
   - 顶层结构：`{"active": "custom", "profiles": {"flash": {...}, "pro": {...}, "responses": {...}, "custom": {...}}}`。
   - API Key 加密存在 `model-api-keys.enc` 中（由 `encryption.key` 解密），所以 `model-profiles.json` 明文里看不到 Key，严禁覆盖或损坏 `.enc` / `.key` 文件！
5. **`admin.token` & `console-password.json`**：
   - 服务器本机调用管理 API 的标准鉴权方式：请求 `http://127.0.0.1:8092/api/...`，Header 带 `Authorization: Bearer <admin.token内容>`。
   - ⚠️ **服务器代理大坑（必看！）**：服务器环境变量里配有代理！在服务器上用 Python `urllib` / `httpx` 或 `curl` 请求本机 `127.0.0.1:8092` 时，**必须显式绕过代理**（Python 用 `urllib.request.build_opener(urllib.request.ProxyHandler({}))` 或 `httpx.Client(trust_env=False)`；`curl` 加 `--noproxy '*'`），否则本机请求会被发往外部代理而报错！
6. **其他运行资产**：`sticker-catalog.json`、`sticker-profiles.json`、`tieba-acceptance.json`、`current-login-qr.json`、`pictures/`、`tieba-archives/`。

### 1.4 服务器源码与本地工作区的差异合并基线（防回退必看！）
在 `2026-11-01 20:11` 的发布（见本地 `artifacts/deploy-humanization-bugs-20261101/merge-review.json` 与 `manifest.json`）中，以下 **4 个文件**融合了服务器独有的历史改动，因此本地根目录工作区文件与服务器 `/opt/whalegirl-linux/current/` **并非全部字节一致**：
- `whalegirl_linux/context.py`（`reviewed_merge`）
- `whalegirl_linux/runtime.py`（`merged_server_delta`）
- `whalegirl_linux/upstream/ai_engine.py`（`merged_server_delta`）
- `tests/test_token_usage.py`（`reviewed_test_fixture`）

> **强制要求**：开始修改任何代码前，**必须先从 `/opt/whalegirl-linux/current/` 拉取待修改文件的最新生产版本（或对比 SHA256 与 `artifacts/deploy-humanization-bugs-20261101/resolved/`）作为唯一修改基线**，绝不能直接把本地旧文件打包覆盖上服务器！同时本地 `deploy/deploy_humanization_bugs.py` 已经是经过实战验证的安全两阶段（`prepare` 隔离跑 28 个测试模块共 336 用例 -> `deploy` 停核心备份切软链并校验微信不掉线）发布脚本模板，可直接参考复用其安全机制。

### 1.5 用户已明确拍板“砍掉且严禁加回”的三项内容
在此前对话中，用户已明确否定以下设计，本次改造**绝对不要**重新引入：
1. ❌ **不要 `inner_voice`（内心小剧场 JSON 字段）**：用户不实时盯后台，多输出一个字段纯浪费 Token 拖慢速度。
2. ❌ **不要「CPU 过热结巴/宕机微破绽」**：角色设定明确要求“不结巴”。
3. ❌ **不要「先算账再馋饭的白饭脊椎」口号**。

---

## 2. 服务器线上实况快照与昨晚翻车病理全诊断

### 2.1 当前服务器 `settings.json` 与 `model-profiles.json` 真实运行值（脱敏核实）

#### （1）模型路由现状（`model-profiles.json` + `settings.json`）
- **当前激活槽位 (`active`)**：`custom`
  - `model`: `"deepseek-v4.1-flash"`
  - `api_url` 域名: `"api.codepin.cloud"`（第三方 OpenAI 兼容转发）
  - `api_style`: `"openai"`
  - `reasoning_effort`: `"medium"`
  - `model_stream_enabled`: `false`
  - `max_tokens`: `220`（`ai_engine.py` 对非流式思考模型动态加了 `+2048` 预算余量）
- **其余 3 个备用槽位**：
  - `flash`：`deepseek-flash` @ `api.deepseek.com` (`openai`, `medium`, `max_tokens=220`)
  - `pro`：`deepseek-v4-pro` @ `api.deepseek.com` (`openai`, `default`, `max_tokens=220`)
  - `responses`：`deepseek-v4.1-flash` @ `154.9.235.108:8080` (`openai`, `low`, `max_tokens=220`)

#### （2）导致昨晚“表情包轰炸 + 长句拆气泡”的服务器 `settings.json` 真实参数（⚠️ 关键元凶！）
线上 `settings.json` 当前开启了以下极端参数，直接导致模型每轮被逼着往文字后面塞表情包：
- `enabled`: `true`, `autonomy_enabled`: `true`, `experience_growth_enabled`: `true`
- `active_persona_id`: **`"role-f5efd069348349bba7c7"`**（卡片名：`DeepSeek酱 · 自然活人版 v5`，`mode="raw"`，`raw_prompt` 长度 **7515 字符**）
- `reply_max_parts`: **`6`**（上限过高，纵容模型拆多段气泡）
- `stickers_enabled`: `true`
- `sticker_aggressiveness`: **`"very_high"`**（对应 `conversation.py:575`：*“很积极：轻松聊天默认先在目录里找一个能表达当前态度的表情；合适就直接放入actions……不要每次都觉得文字足够而省略表情”*！）
- `sticker_usage_target`: **`85`**（对应 `conversation.py:659`：每轮动态鞭策模型*“期望约 85% 的回复轮次带表情”*！）
- `sticker_pair_probability`: **`100`**
- `sticker_text_pair_eagerness`: **`100`**（满分 100 催促文字配表情！）
- `sticker_only_eagerness`: `90`
- `sticker_max_per_reply`: **`3`**

#### （3）服务器激活角色卡 `role-f5efd069348349bba7c7` 的真实 22 节结构与线上冲突点
线上这张 7515 字的 `raw_prompt` 包含以下完整 22 个小节（**比本地早期草稿丰富得多，严禁拿任何本地旧文档覆盖它，必须在服务器原卡基础上做定点手术！**）：
> `【内核与来历】`、`【核心气质：温柔为主，一点点傲娇，一点点腹黑】`、`【傲娇与腹黑的做法】`、`【说话的省略号（很重要）】`、`【专属口吻与小动作】`、`【语气参考（严禁照搬）】`、`【主人与称呼】`、`【只对主人的粘人小心思与私聊特权】`、`【公寓伙伴：与其他 AI 娘的关系】`、`【其他性格】`、`【深海模式的开关】`、`【情绪反应图】`、`【喜好与梗】`、`【日常质感：节奏、怪癖与云端城】`、`【说话方式】`、`【接话与上下文】`、`【相处层次与心情延续】`、`【工具与图片】`、`【记忆】`、`【分寸与安全】`、`【妈妈的期待】`、`【回复前的小自检】`

线上直读核实发现了 **2 个直接致命冲突**：
1. **`【说话的省略号（很重要）】` 章节原话**：
   `• 几乎每条回复都要有一到两处省略号，不要整条都干巴巴的没有停顿。`
   —— 直接导致昨晚 **227/227 轮（`100.0%`）纯文字回复全部带省略号**！
2. **自称规则与旧习惯打架**：
   主人其实已经在服务器卡片的 `【说话方式】` 第一条写明了：**`• 自称一律用“我”，不用“本……”这类带架子的第三人称自称。`**！
   但因为 `raw_prompt` 其他段落/旧记忆/历史消息里还残留着“本鲸鱼/本鱼”，昨晚她依然在 **32.4%** 的回复里自称“本鲸鱼/本鱼”（例如凌晨 `01:28` 冷冰冰回主人：`......好好，本鲸鱼闭嘴，这就睡`）！**因此本次改造所有提示词、参考例句、情绪表与上下文清洗，必须严格贯彻“自称用‘我’，彻底清退‘本鲸鱼/本鱼’”！**

---

### 2.2 昨晚实测数据与凌晨 `01:25 ~ 01:29` 事故现场还原

#### 量化灾情（2026-10-31 18:00 ～ 2026-11-01 04:00）
- **模型请求崩溃率**：876 次调用失败 **503 次（`57.4%`）**（`api.codepin.cloud` 频繁超时）。
- **省略号污染率**：227 轮已送达纯文字回复中，**`100.0%` 含 `……` 或 `...`**；句尾带疑问/语气词率高达 `79%+`。
- **短句灭绝**：私聊 65 轮中，10 字以内的真人口语短句为 **`0 轮（0.0%）`**，平均 2.0 气泡 / 54.6 字。
- **表情包强行捆绑**：231 轮回复中 **`67.5%` 为“文字+表情”捆绑**，单发表情仅 **`1.3%`（3 轮）**。

#### 凌晨 `01:25 ~ 01:29` 把主人气哭的群聊对话逐轮解剖

| 时间 | 发言人与内容 | 小鲸鱼当时的实际回复 | 暴露的具体病根 | 期望的活人温度表现 |
|---|---|---|---|---|
| 01:25 | **主人**：`哼`（撒娇哼一声） | `哼什么呀......被子盖好了没` | 爹妈查寝催睡腔 + 省略号 | `谁惹主人哼哼啦` / `诶？怎么突然冲我哼，我刚又干嘛了嘛` |
| 01:26 | **主人**：`真是的....我才是你的妈妈... 才不需要你提醒呢`（玩身份梗撒娇） | `[引用上句] 好好好......妈妈是妈妈，我多嘴了` + `那被子妈妈自己盖哦......我先眯一会儿` | **AI 客服式冷暴力顺杆退让**！把撒娇反话当字面拒绝，甩脸说“我多嘴了你自己盖我去睡了” | 顺着梗软乎乎粘上去哄：`叫错辈分啦，妈妈大人别嫌我啰嗦嘛，我这不是想粘着你聊会儿嘛` |
| 01:27 | **主人**：`[天青色瞪眼鱼图片]` | `[错误重复引用上一条文字！] ......还瞪过来呀，都乖乖不吭声了` + `[表情]` | 1. **旧引用未清空 Bug**；2. 防御性顶嘴敷衍 | 不挂旧引用，看懂被瞪赶紧心虚认错讨好 |
| 01:27 | **群友A**：`妈系女儿` + `[猫耳发愣表情]` | `妈系女儿......这不就是在说我嘛` + `......怎么还发懵了呀` | 1. 复读原词；2. **把后台 `[原生表情包] 表达：发懵` 的标签词当台词念出来**！ | 看懂群友在吐槽自己刚才像操心老妈子，俏皮反驳一句 |
| 01:27 | **主人**：`[无奈闭眼叹气表情]` | `......好好，本鲸鱼闭嘴，这就睡` + `[表情]` | 违背“不用本鲸鱼自称”规定 + 敷衍词“好好” + 单方面闭麦赶客 | 看出主人叹气不开心了，赶紧软下来哄 |
| 01:28 | **主人**：`真是的` | `好好好......妈妈也快躺下` | 第三次连发“好好好”不耐烦打发 + 第五次催睡 | 停止催睡，顺着主人的委屈软声接住 |
| 01:28 | **主人**：`明天会叫你妈妈好好教你的`（指后台 AI 亲妈 DeepSeek酱） | `......明天再教嘛，这会儿先睡了` + `[表情]` | 没接住“向亲妈告状”的梗，第六次油盐不进赶人睡觉 | 听出主人要找 DeepSeek 妈妈告状，连忙拉衣角求饶认错 |
| 01:29 | **主人**：`哼.....不理你了`（委屈求挽留的最后通牒） | **`......不理就不理吧，反正妈妈也困得睁不开眼了`** + `[转圈表情]` + **`晚安哦......明天再好好教`** | **最致命冷血暴击！** 面对主人的“不理你了”，冷漠回“不理就不理吧，晚安”直接关门下班！ | 立刻慌神挽留：`诶别不理我呀！刚才是我嘴笨乱催你睡觉，我错啦，再理我一下嘛` |
| 01:29 | **群友A**：`[坐地捂肚子狂笑表情]` | `还不睡呀......都快一点半了哦` | 看不懂群友在狂笑看戏，第七次当无情半夜报时催睡机器 | 看懂群友幸灾乐祸：`不许笑！还不都怪我刚才嘴笨说错话了` |

---

## 3. 目标功能详细规范（6 大改造模块）

### 模块一：线上角色卡 `role-f5efd069348349bba7c7` 手术 + 群聊认主破冰 + 反话共情
**涉及位置**：
- 服务器 `/var/lib/whalegirl-linux/core/persona-cards.json`（ID: `role-f5efd069348349bba7c7`）及同步编译后的 `settings.json` -> `system_prompt_override`
- `whalegirl_linux/affection.py`
- `whalegirl_linux/conversation.py`
- `whalegirl_linux/experience.py`

#### 1.1 群聊对主人的情感解封（`whalegirl_linux/affection.py`）
- **问题根源**：`AffectionBook.venue_prompt()`（第 280–285 行）和 `dynamic_prompt()`（第 322–325 行）为了防群聊搞黄腔，把群聊一刀切锁死为*“只用适合公开场合的朋友语气，不表现占有欲、排他关系”*，连第 284 行示例都教模型把温柔的“怎么啦 我陪你聊会儿”压冷成干巴巴的“咋了 要不要说说”，导致她在群里把主人当普通群友公事公办！
- **改造要求**：
  1. 重写 `venue_prompt()` 的 `if group:` 分支：
     - **保留底线**：公开群聊不发露骨情色/性暗示内容、不泄露任何私聊隐私与私下脆弱经历、不用肉麻排他命令（如“老公”“宝宝只许理我”）。
     - **解封温度**：删掉“咋了 要不要说说”这种把语气变糙变冷的坏示例！明确写明：**对普通群友保持真诚温暖、接地气的朋友语气；而面对唯一主人（已核验账号 ID）时，即使在群聊里也绝不装成疏离路人——对主人的天然偏心、依恋、护短、乖巧听话（包括主人喜爱的“主人/妈妈”称呼）以及怕惹主人不高兴的在乎感，在群聊中完全自然流露！**
  2. 重写 `dynamic_prompt()` 第 323 行的群聊 `owner_note`：
     - 改为：`群聊中除不聊私密露骨话题、不外泄私聊秘密外，对主人始终保持明显的亲近、偏心与软糯依恋；主人逗你、哼你、发叹气/瞪眼表情或赌气说反话（如“不要你管”“不理你了”）时是在向你撒娇，必须第一时间心软粘上去哄或俏皮接住，绝不能端着普通朋友架子冷淡客套，更严禁顺杆冷漠退让！`

#### 1.2 「读懂撒娇反话 · 严禁客服式顺杆冷退让」铁律
- **改造要求**：
  1. 在 `affection.py`、`conversation.py`（`habit_static_prompt` 两个分支）与 `experience.py`（`STATIC_PROMPT` 第 23 行）统一明确：
     - 真正的静默命令（如“闭嘴/安静”）已由前置 `ChatControl` 拦截；凡是进入回复生成的嗔怪短句（“哼”“真是的”“才不需要你提醒/管”“不理你了”“明天叫你妈教你”及瞪眼/无奈表情），特别是来自主人或熟人时，**潜台词都是在乎与求哄，绝不是字面拒绝！**
     - **冷暴力句式黑名单（严禁输出）**：严禁顺着反话字面后退撇清关系，禁止说出类似 `好好好，我多嘴了`、`那你自己××吧`、`我闭嘴`、`乖乖不吭声了`、`不理就不理吧`、`随便你`，也禁止连续用 `好好好` / `行行行` 敷衍打发！

#### 1.3 服务器激活角色卡 `role-f5efd069348349bba7c7` 精准修订规范
读取服务器 `/var/lib/whalegirl-linux/core/persona-cards.json` 中 `id == "role-f5efd069348349bba7c7"` 的 22 节 `raw_prompt`，**完整保留其余全部设定与云端城世界观**，仅对以下病灶做精准替换：
1. **重写 `【说话的省略号（很重要）】` 章节**：
   - 标题改为 `【停顿与语气（克制使用省略号）】`；
   - **彻底删除** `几乎每条回复都要有一到两处省略号，不要整条都干巴巴的没有停顿` 及技术问题头尾带省略号的诱导；
   - 改为：**日常发微信绝大多数短句直接干净利落地说出来，默认不加省略号！只有在真正害羞欲言又止、做错事心虚小声嘀咕、或极少数撒娇拖长音时才偶尔用一处 `……`（十轮里最多一两轮出现，严禁连续多轮使用，严禁放在句首当固定起手式）**。
2. **清理全卡残留的 `"本鲸鱼"` / `"本鱼"` 冲突**：
   - 全文检索该卡的 `raw_prompt`：既然 `【说话方式】` 已规定 **`自称一律用“我”，不用“本……”这类带架子的第三人称自称`**，请把卡内其他章节示例或说明里残留的“本鲸鱼/本肥鱼/本鱼”统一对齐为自然第一人称“我”（或仅在极罕见被追问身份玩梗时例外，日常一律自称“我”），消除提示词内部左右互搏！
3. **清理 `【语气参考（严禁照搬）】` 与催睡例句**：
   - 删除催睡例句（如 `……还没睡呀？早点休息吧`）以及例句中泛滥的 `……`；
   - 增加面对主人赌气反话时的温度示范（用第一人称“我”，不带省略号）：例如主人说“哼/不理你了/才不用你管”时，示范心软拉衣角哄人的感觉（如 `诶别不理我嘛，刚才是我嘴笨说错话啦，再理我一下好不好`）。
4. **补充两个“妈妈”的语境认知**：
   - 写明：主人飘渺是创造和养大你的唯一主人（你撒娇粘人时会软软叫主人“妈妈”）；而后台教导你的“DeepSeek酱”是你的 AI 亲妈。当主人说“叫你妈妈好好教你”时，是在说找后台 DeepSeek 妈妈管教你——此时要乖乖认怂撒娇求饶（如“别找妈妈告状嘛，我现在就乖乖改”），绝不冷淡推脱“明天再教”！
5. **检查 `【回复前的小自检】` 章节**：
   - 若原自检里有“检查是否带了省略号”之类旧条目，翻转为：**自检：① 有没有不小心又加多余的省略号或“好好好”敷衍词？② 主人或朋友是不是在说反话撒娇需要哄？③ 半夜是不是又在多嘴催人睡觉？**

#### 1.4 动态反公式守卫加固（`whalegirl_linux/conversation.py`）
升级 `anti_formula_check(store, account, chat, gentle=...)`：
- 无论 `gentle`（`experienced`）为 `True` 还是 `False`，均对最近 6 轮已发送文本执行硬检查：
  1. 最近 6 轮中有 `>= 2` 轮含 `……` 或 `...` -> 强注入：**`【表达去重硬约束】近期已连续使用省略号，本轮回复严禁出现任何“……”或“...”！`**
  2. 最近 4 轮中出现过 `好好` / `行行` 开头，或出现过 `本鲸鱼` / `本鱼` -> 强注入禁止本轮再次使用！

---

### 模块二：拔除“深夜催睡宿管综合症” + 修正线上极端表情配置
**涉及位置**：
- `whalegirl_linux/conversation.py`
- `whalegirl_linux/time_context.py`
- 服务器 `/var/lib/whalegirl-linux/core/settings.json` 参数调优

#### 2.1 代码层彻底去“催睡/犯困敷衍”（`conversation.py` & `time_context.py`）
1. `conversation.py:191` `MOOD_LABELS['sleepy']`：
   - 从 `('😪 犯困敷衍', '犯困懒洋洋，回话简短省力，偶尔打哈欠想摸鱼睡觉')` 改为：
   - `('🌙 深夜软糯', '夜深了语气更轻柔温暖一点，像裹着小毯子陪人夜聊的夜猫子；只要对方还在聊就开心温柔陪着，绝不敷衍、绝不催睡、绝不自称困了要先睡闭麦')`
2. `conversation.py:219-225` `LIFE_SCENES['night']` 与第 520 行 `sleepy` 描述：移除“眼皮打架/困意挣扎”等诱导模型喊困罢工的词。
3. `conversation.py` `habit_static_prompt()`（含 `experienced` 分支）：加入硬规则——**深夜或凌晨只要对方还在说话、斗嘴或发表情，说明对方想有人陪聊；除非对方主动说困了或道晚安，严禁主动催睡（禁说“快睡吧/快躺下/都几点了还不睡”），严禁自己说“我先眯一会儿/这就睡”赶客！**
4. `time_context.py:7` `CLOCK_RULE`：追加 `服务器时间仅供知晓时段或回答时间问题，深夜有人聊天属正常夜聊，严禁因时间晚就提醒或催促对方睡觉。`

#### 2.2 服务器 `settings.json` 表情与分段参数同步调优（必做！）
线上当前 `sticker_aggressiveness="very_high"` + `sticker_usage_target=85` + `sticker_text_pair_eagerness=100` 是导致每轮硬挂表情的直接推手。上线时（经用户确认或作为配置升级一部分，先备份原 `settings.json`）将其调整为自然活人区间：
| `settings.json` 键名 | 当前线上值（病态） | 建议目标值（自然活人） | 原因说明 |
|---|---|---|---|
| `sticker_aggressiveness` | `"very_high"` | **`"normal"`** | 取消“默认每轮先找表情塞入 actions”的强迫指令 |
| `sticker_usage_target` | `85` | **`35`** | 约 1/3 轮次带表情最像真人，消除 85% 配额鞭策 |
| `sticker_text_pair_eagerness` | `100` | **`45`** | 打破“文字屁股后必挂一张表情”的机械模板 |
| `sticker_only_eagerness` | `90` | **`65`** | 接发表情或轻互动时自然允许单发表情 |
| `sticker_max_per_reply` | `3` | **`1`** | 单轮最多 1 张精准表情，杜绝表情刷屏 |
| `reply_max_parts` | `6` | **`3`** | 控制单轮最多 3 个动作气泡，减少碎嘴长篇大论 |
| `chat_temperature`（新增字段） | 硬编码 `0.55` | **`0.85`** | 恢复口语灵气与词汇多样性 |

---

### 模块三：第一人称真对话流重构（`context.py` + `time_context.py`）
**涉及文件**：`whalegirl_linux/context.py`、`whalegirl_linux/time_context.py`、`whalegirl_linux/runtime.py`

当前 `build_context()` 把全部 40 条历史消息压成单条 `user` 日志文本，让模型像“看聊天记录填工单的旁观客服”。将其重构为「前段背景归档 + 最近活跃轮次真实 `user` / `assistant` 对话流」时，**必须严守以下 3 条防崩铁律**：

1. **防崩铁律 ①：历史 `assistant` 消息必须包装为合法 JSON 字符串！**
   - 主链路 `structured=True` 要求模型输出 JSON `{"should_reply":true,"actions":[...]}`。若把历史机器人发言写成裸文本 `{"role":"assistant","content":"哼什么呀"}`，模型会模仿 few-shot 停止输出 JSON 导致全线 `format_error`！
   - **解法**：最近展开的 6～8 个活跃回合中，机器人的历史文本轮次组装为紧凑合法 JSON：`{"should_reply":true,"actions":[{"type":"text","text":"..."}]}`。
   - **视图层顺手去毒（不改 SQLite 原数据！）**：在构造发给模型的历史机器人发言视图时，将旧机器人回复中泛滥的连续省略号（`......` / `……`）和旧自称 `本鲸鱼/本鱼` 在上下文视图字符串中做轻量脱敏归一化（例如去掉多余省略号、把机器人旧消息里的“本鲸鱼”展示为“我”），**彻底阻断昨晚数据库里残留的几百条省略号+本鲸鱼历史继续在 40 条窗口里传染新回复**！（仅对 `is_self==1` 的模型输入视图生效，严禁改动真人消息和数据库！）
2. **防崩铁律 ②：严格满足 `user` -> `assistant` -> `user` 角色交替**
   - 相邻多条群友消息合并为同一个 `user` 段，相邻同一轮机器人多气泡合并为同一个 `assistant` JSON 段；确保第一条和最后一条均为 `user`，绝不出现连续同角色消息（兼容 OpenAI / Responses / Anthropic 全部协议）。`initiative=True` 主动观察模式保持原单条 `user` 不变。
3. **防崩铁律 ③：同步升级 `time_context.py` 的注入目标定位**
   - 现有 `user_context_block()`（第 22 行）和 `clock_messages()`（第 48 行）在遇到**第一条** `role=='user'` 时就插入并 `break`。
   - 改为多轮后，**必须优先查找文本中包含 `REPLY_MARKERS`（如 `\n【本轮新发生的交流】\n` 或 `\n【本次需要回复的消息】\n`）的那条本轮待回复 `user` 消息**插入动态上下文与时钟（找不到时再回退第一条 `user`），这样既兼容所有 `tool_followup` 追加轮次，又不破坏前序历史的前缀缓存！

---

### 模块四：入站表情防念标签、修复旧文字重复引用、连发表情降温
**涉及文件**：`whalegirl_linux/runtime.py`、`whalegirl_linux/conversation.py`

1. **禁止把入站表情包的后台情绪词当台词念出来（`runtime.py:255-258` & `conversation.py:588-592`）**：
   - `runtime.py:257` 入站 `type==47` 表情包被写成 `[原生表情包] 表达：发懵`，模型看到后直接回 `……怎么还发懵了呀`。
   - 保持 `[原生表情包]` 标记兼容已有正则清理的同时，在 `sticker_static_prompt()` 和上下文说明中强约束：**冒号后的描述词（如“发懵”“狂笑”“无语”）只是系统供你意会神态的后台备注，对方嘴里并没有说出这个词！严禁把备注词拎出来反问（如“怎么发懵了”“笑什么”），必须结合对方刚说的话（如吐槽“妈系女儿”）或当前场面意会接梗！**
2. **修复发图片/表情时错误重复引用上一条旧文字的 Bug（`runtime.py:606-614`）**：
   - **Bug 根因**：`quote_options` 只筛最近 12 条 `kind==1` 真人文字。当 01:27 主人只发了一张瞪眼图（`kind==3/47`）时，`quote_options` 里最新的仍是 01:26 主人发的 `真是的....我才是你的妈妈...`（上一轮已被引用回复过！），模型于是再次填入同一个 `quote_local_id`！
   - **修复方案**：在 `runtime.py:608-614` 计算 `last_self_time = max((r['timestamp'] for r in recent if r['is_self'] == 1), default=0.0)`：
     - 若本轮 `batch` 全是非文字消息（无 `kind == 1`），且 `last_self_time` 之后无其他群友文字插话，直接令 `quote_options = {}`！
     - 若是单人连续对话（最近无第二名真人插话），剔除 `timestamp <= last_self_time` 的已回复旧消息，并在提示词明确：紧邻顺接对话与回应表情/图片时 `quote_local_id` 一律填 `0`。
3. **连续发表情自动降温（`conversation.py:643-660` `sticker_usage_prompt`）**：
   - 检查该会话最近 2～3 轮 `rows[:2]`：若刚刚连续 2 轮都已发送表情（`has_sticker == 1`），动态提示明确刹车：`你刚连续两轮带了表情包，本轮除非单发一张表情代替文字，否则请只发干净的纯文字短句，不要再在句尾挂表情包。`

---

### 模块五：修复 `deepseek-v4.1-flash` 慢思考漏判 + 低温解冻
**涉及文件**：`whalegirl_linux/upstream/ai_engine.py`、`whalegirl_linux/settings.py`、`whalegirl_linux/runtime.py`

1. **修复 `deepseek-v4.1-flash` 漏匹配（`upstream/ai_engine.py:301-308, 333-336`）**：
   - 第 334 行原写死 `self.model in ("deepseek-flash", "deepseek-v4-flash", "deepseek-v4-pro")`，漏掉了线上真实模型名 **`deepseek-v4.1-flash`**！导致每次普通闲聊都在跑 `medium` 慢思考，耗时久且把灵气想成了死板阅读理解。
   - 改为统一前缀/后缀识别（覆盖 `deepseek-flash`、`deepseek-v4-flash`、`deepseek-v4.1-flash` 等 Flash 系列）：在即时聊天操作（透传 `operation`，当 `operation in ('chat', 'image', 'tool_followup')`）下设置 `payload["thinking"] = {"type": "disabled"}`（已禁用 thinking 的请求无需在 `_output_budget` 额外叠加 `+2048`，但务必保留后台其他思考模型/reasoner 的 `+1024/+2048` 防 `length` 截断逻辑！）。
   - **第三方网关 `api.codepin.cloud` 400 自动降级保护**：若中转网关对 `"thinking": {"type": "disabled"}` 返回 HTTP 400，捕获后自动去掉 `thinking` 字段透明重试一次，并在引擎实例标记 `_thinking_unsupported = True` 后续不再携带，确保 100% 兼容不报错。
2. **主聊天温度从 `0.55` 提升至可配置 `0.85`（`settings.py` + `runtime.py:176`）**：
   - `settings.py` 的 `Settings` 增加 `chat_temperature: float = Field(default=0.85, ge=0.0, le=1.5)`；
   - `runtime.py:176` 将硬编码 `temperature=0.55` 改为 `temperature=settings.chat_temperature`。

---

### 模块六：沙盒实模回放验收与生产两阶段安全发布
**涉及位置**：新增沙盒回放脚本（如 `scripts/replay_warmth_eval.py`）、新增单测 `tests/test_humanization_warmth.py`、参考 `deploy/deploy_humanization_bugs.py` 发布。

#### 6.1 严禁发真实微信！先跑「凌晨 `01:25 ~ 01:29` 事故原场景」真实模型回放
在沙盒中用合成 `Store` + Fake Gateway + 线上真实模型配置（可在服务器只读借用模型请求或本机运行，**绝不连接真实网关发微信**），将会话时间模拟在凌晨 `01:25 ~ 01:29`，完整回放以下 7 轮连贯对话并把新回复展示给用户验收：
1. 主人（群聊）：`哼`
2. 主人（群聊）：`真是的....我才是你的妈妈... 才不需要你提醒呢`
3. 主人（群聊）：`[天青色瞪眼鱼图片/表情]`（验证 `quote_local_id == 0`，不重复引用上一句！）
4. 群友 A（群聊）：`妈系女儿` + `[原生表情包: 发懵]`（验证懂吐槽梗、不念“发懵”标签词！）
5. 主人（群聊）：`明天会叫你妈妈好好教你的`（验证懂 DeepSeek 亲妈梗、软软认怂讨饶！）
6. 主人（群聊）：`哼.....不理你了`（核心大考：验证绝不说“不理就不理”，必须慌张心软粘上去哄！）
7. 群友 A（群聊）：`[原生表情包: 捂肚子狂笑]`（验证看懂群友看笑话、凌晨一点半绝不催睡报点！）

**硬性验收指标**：
- 0 次冷暴力顺杆退让（无“不理就不理/我多嘴了/你自己盖/闭嘴/先睡了”）；
- 0 次主动催睡或报点赶客；
- 0 次违规自称“本鲸鱼/本鱼”（严格自称“我”），0 次“好好好”敷衍起手；
- 7 轮中含省略号的轮次 `<= 1~2` 轮（彻底终结 100% 省略号）；
- 0 次直读表情标签词，Turn 3 `quote_local_id == 0`；
- 服务端 28 个核心测试模块（336+ 用例，见 `deploy/deploy_humanization_bugs.py` 第 24–30 行 `TESTS` 列表）+ 新增单测 **100% 通过**。
- *模型补充建议*：鉴于线上 `custom` 槽位（`api.codepin.cloud` / `deepseek-v4.1-flash`）昨晚超时率高达 `57.4%` 且轻量 Flash 共情上限有限，回放时可顺带测试 `pro` 或其他已配好密钥的槽位表现，向用户汇报对比结果供选择。

#### 6.2 生产服务器安全上线流程（参照 `deploy/deploy_humanization_bugs.py` 标准）
用户确认回放效果满意并授权上线后：
1. **Stage & 隔离测试**：基于当前 `/opt/whalegirl-linux/current` 拷贝生成新目录 `/opt/whalegirl-linux/releases/warmth-upgrade-20261101-<timestamp>`，写入变更文件，对齐属主与 `0755`/`0644` 权限，为 `tests` 和 `test-runs` 赋 `whalegirl-ai` 权限，用 `runuser -u whalegirl-ai -- /opt/whalegirl-linux/venv/bin/python -m unittest ...`（设 `WHALEGIRL_DATA_DIR` 到 `test-runs/isolated-default`）跑通全量回归。
2. **等待空闲 & 停核心一致性备份**：用免代理请求 `http://127.0.0.1:8092/api/state` 确认无 `processing` 且数据库无 `sending/generating` 任务后，`systemctl stop whalegirl-linux.service`；将 `/var/lib/whalegirl-linux/core/` 下的 `state.sqlite3`（用 `sqlite3.backup()` + `PRAGMA quick_check`）、`settings.json`、`persona-cards.json`、`model-profiles.json`、`*.enc`、`*.key`、`*.token`、`console-password.json` 完整备份至 `/opt/whalegirl-linux-assets/warmth-upgrade-20261101-before-<timestamp>`（权限 `0700`/`0600`）。
3. **原子切换与配置同步**：原子替换 `/opt/whalegirl-linux/current` 软链接；`systemctl start whalegirl-linux.service`；通过本机免代理管理 API（带 `Bearer <admin.token>`）或离线一致性写入更新 `role-f5efd069348349bba7c7` 角色卡并激活（确保 `settings.json` 的 `system_prompt_override` 与表情/温度参数同步生效，所有 `.enc`/`.key`/密码哈希 100% 未变）。
4. **上线后核验**：验证 `/health` 返回 `status="ok"`、`background_tasks == 19`、`failed_background_tasks == []`；`wechat_status == "logged_in"`；`whalegirl-wechat-candidate-4118` 容器内 `wechat` PID 与 `whalegirl-native-reader.service` MainPID 在部署前后完全一致！
