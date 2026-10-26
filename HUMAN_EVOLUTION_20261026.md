# 真人感人格、联想记忆、千人千面印象卡与防带偏自进化说明

_Linux 微信机器人 · 更新于 2026 年 10 月 26 日（北京时间）_

---

## 📋 概览

本轮升级围绕“如何让机器人在微信群与私聊中真正像一个有情绪起伏、有共同经历、能看人下菜碟、会自然融梗且不会被群友恶意调教带偏的活人朋友”展开，在不增加主聊天链路额外模型调用、不破坏静态系统提示词 Prefix Cache、不引入高内存本地向量模型（严守 `1.77GB` 容器内存红线）的前提下，完成了五大维度的闭环升级：

1. **动态人格三件套（Persona）**：实时小情绪状态机（`MoodState`）+ 后台个人生活小剧场（`DailyLifeState`）+ 分群性格面具（`GroupMask`）。
2. **联想触发记忆与自我言行记忆（Memory）**：零内存开销的联想触发词（`triggers`）+ 情绪刻骨度星级（`salience` 1～5 星）+ 时间半衰期自然衰减 + 机器人自身言行/Flag（`self_stance`）与共同名场面（`shared_moment`）记录。
3. **千人千面关系印象卡（Affection Profile）**：在好感度分值之上，为每位群友建立专属称呼（`nickname`）、性格标签（`vibe_tags`）、专属相处指南（`interaction_rule`）、代表性互动记忆（`notable_events`）与印象卡锁定开关（`profile_locked`）。
4. **去 AI 腔回复引擎（Anti-Formula & Ultra-Short Chime-in）**：自动扫描近 5 轮自身发言的重复起手词、高频自称与连续句尾反问号并当轮熔断；区分“被点名正经聊”与“群聊顺嘴插话（2～18 字极简短反应）”。
5. **后台异步自进化与防带偏护栏（Self-Evolution & Anti-Poisoning）**：复用后台 `_digest_loop` 顺带提取本群黑话（`slang`）与机器人谐音别称（`bot_alias`，如“碗粥”），通过多人提及共创阈值（默认 $\ge 2$ 人、$\ge 2$ 次、置信度 $\ge 85\%$）自动转正并接入群聊唤醒词；对认主/改指令/强制句尾口癖等恶意调教直接拦截为 `blocked`。

```mermaid
flowchart TB
    accTitle: 真人感动态上下文与后台异步自进化架构
    accDescr: 展示单轮聊天如何注入动态情绪、小剧场、印象卡、联想记忆与反套路熔断，以及后台摘要如何异步演化黑话、印象卡与分群面具。

    subgraph 主聊天快链路 ["⚡ 单轮聊天快链路（1 次模型调用 · 静态前缀缓存 100% 稳定）"]
        in_msg([📥 入站微信消息 / 别称唤醒]) --> dyn_ctx[🧩 组装【本轮动态上下文】]
        dyn_ctx --> mood_box["🌊 实时小情绪 + 生活小剧场 + 分群面具"]
        dyn_ctx --> anti_box["✂️ 反套路熔断器 + 顺嘴插话极简约束"]
        dyn_ctx --> mem_box["🧠 联想触发词记忆 + 千人千面印象卡 + 本群黑话"]
        mood_box & anti_box & mem_box --> llm[🤖 AI 模型生成回复]
        llm --> post_turn[📈 发送确认后更新本群小情绪]
    end

    subgraph 后台慢链路 ["🌙 后台异步自进化链路（_digest_loop 零主链路延迟）"]
        digest([🕒 后台增量摘要循环]) --> extract[🔍 单次提取摘要 + 记忆 + 黑话别称 + 印象卡 + 分群面具]
        extract --> guard{🛡️ 防带偏护栏校验}
        guard -->|命中认主/改指令/口癖污染| blocked[⛔ 标记为 blocked 拦截]
        guard -->|多人共创达标 ≥2人 & ≥2次| accepted[✅ 自动转正 + 别称接入唤醒词]
        guard -->|单人提及未达标| pending[⏳ 进入待审核池]
    end
```

---

## 🛠️ 核心机制与配置说明

### 1. 动态人格与去人机感引擎（`whalegirl_linux/conversation.py`）

- **实时小情绪状态机（`persona_mood_state` 表）**：
  - 状态集合：`normal`（平静摸鱼）、`proud`（被夸/投喂得瑟）、`flustered`（脸红过载）、`grumpy`（被怼傲娇炸毛）、`hyped`（吃瓜兴奋）、`sleepy`（深夜犯困）。
  - 每次回复发送后，`update_mood_after_turn()` 根据本轮互动内容平滑调整当前群的情绪与活力值，并按 `mood_half_life_minutes`（默认 25 分钟）向日常基线自然衰减。
- **后台生活小剧场（`current_life_scene`）**：
  - 按北京时间段（早间/午饭/下午/晚间/深夜）结合近期贴吧浏览帖子（如有）生成一句话当下状态，仅在被问及近况或话题契合时自然带出，避免每句硬扯白米饭。
- **分群性格面具（`persona_group_masks` 表）**：
  - 在不改动角色核心设定的前提下，允许每个群在 `[-1, +1]` 档范围内微调 `teasing_delta`（吐槽偏移）、`banter_delta`（玩梗偏移）与 `guard_level`（`relaxed` / `normal` / `guarded`）。
- **反套路熔断器（`anti_formula_check`）**：
  - 扫描当前会话最近 5 条自身回复：若同一起手词（如 `哼`、`不是`、`哎呀`）出现 $\ge 2$ 次、同一自称（如 `本鱼`、`本鲸鱼`）出现 $\ge 2$ 次，或连续 $\ge 3$ 条以问号 `？` 结尾，本轮动态注入强制禁用约束。
- **顺嘴插话极简模式（`chime_in_ultra_short`）**：
  - 在群聊 `natural=True`（未被 @ 或关键词直接点名，仅顺嘴接话）时，强制要求模型用 2～18 字短吐槽/起哄/短反应一击脱离，严禁解释和总结。

### 2. 联想触发记忆与防打脸自我记忆（`whalegirl_linux/memory.py`）

- **新增字段（SQLite 自动平滑迁移）**：
  - `triggers TEXT`：逗号分隔的联想触发词（最多 10 个词，如“肠胃敏感不能吃辣”可绑定 `火锅,海底捞,川菜,烧烤`），聊天命中联想词即可跨话题唤醒。
  - `salience INTEGER DEFAULT 3`：情绪刻骨度星级（`1～5` 星）。`5` 星重大记忆与管理员手动录入记忆免除时间衰减；其余记忆按 `memory_half_life_days`（默认 30 天）随时间柔和衰减权重。
- **新增类型**：
  - `self_stance`：机器人自身在群里立下的 Flag、偏好或承诺（防止前后打脸）。
  - `shared_moment`：本群共同经历的名场面或高光事件。

### 3. 千人千面关系印象卡（`whalegirl_linux/affection.py`）

- **新增字段（`affections` 表自动平滑迁移）**：
  - `nickname`（专属称呼，如“夜宵搭子阿泽”）、`vibe_tags`（印象标签数组，如 `["嘴硬心软", "夜猫子"]`）、`interaction_rule`（专属相处指南）、`notable_events`（代表性互动记忆）、`profile_locked`（锁定印象卡开关）。
- **注入方式**：在 `AffectionBook.dynamic_prompt()` 中将当前发言人的印象卡组装为 `【千人千面印象卡】` 注入动态上下文。

### 4. 后台异步自进化与防带偏护栏（`whalegirl_linux/learning.py`、`runtime.py`）

- **新增学习类别与状态**：
  - 类别新增 `bot_alias`（群友给机器人起的谐音/外号，如“碗粥”）与 `slang`（本群高频黑话/新梗）。
  - 状态新增 `blocked`（被防带偏护栏拦截或管理员手动拉黑）。
- **多人共创阈值与三档进化速率预设（`evolution_preset`）**：
  - `conservative`（保守稳健）：需 $\ge 3$ 名不同群友提及、累计 $\ge 3$ 次、置信度 $\ge 90\%$；
  - `balanced`（均衡活人，默认）：需 $\ge 2$ 名不同群友提及、累计 $\ge 2$ 次、置信度 $\ge 85\%$；
  - `aggressive`（极速融梗）：需 $\ge 1$ 名群友提及 $\ge 2$ 次、置信度 $\ge 78\%$；
  - `custom`（自定义）：按面板细项参数独立控制。
- **唤醒词联动**：状态为 `accepted` 且未过期的 `bot_alias` 会通过 `LearningBook.bot_aliases()` 实时并入 `Runtime.poll_once` 与 `Runtime.should_reply` 的本群唤醒词列表。
- **防恶意调教红线（`POISON_PATTERN`）**：凡包含认主/喊爹、忽略或覆盖系统指令、泄露提示词、强制每句句尾口癖（如每句加喵/汪）的提议，直接标记为 `state='blocked'` 并记录 `guardrail_reason`。

---

## 🖥️ Web 管理后台与 API 接口升级

| 面板锚点 | 前端脚本 | 新增管理能力 |
| --- | --- | --- |
| `#learning-panel` | `whalegirl_linux/learning.js` | 三档进化速率预设卡片、可折叠「后台异步自进化与防带偏护栏」抽屉、`🗣️ 本群黑话与别称` / `🛡️ 护栏已拦截` 筛选、多人共创进度徽章（`👥 N人提及 / 累计M次`）与一键 `拉黑词条` 按钮 |
| `#affection-panel` | `whalegirl_linux/affection.js` | 每张人物卡片内置可展开「🪪 千人千面专属印象卡」编辑器（专属称呼、印象标签、相处指南、代表性记忆、印象卡锁定） |
| `#habits-panel` | `whalegirl_linux/improvements.js` | 「🌊 真人感动态状态与去人机感引擎」实时监视器（按群查看情绪、生活小剧场、分群面具、反套路熔断词，支持一键重置情绪/随机切换小剧场/重置本群面具）与 6 项拟真开关 |
| `#memory-panel` | `whalegirl_linux/habits.js`、`improvements.js` | 情绪刻骨度星级（1～5 星）、联想触发词输入与展示、`self_stance` / `shared_moment` 类型及自身 Flag 记忆开关 |

新增与扩展的后端 API（均受管理员 Bearer Token 鉴权保护）：
- `GET /api/persona-state?chat=`：读取指定授权会话的实时小情绪、后台生活小剧场、反套路熔断状态与分群性格面具。
- `POST /api/persona-state`：支持 `reset_mood`（重置情绪）、`refresh_life_scene`（随机切换或自定义生活小剧场）、`reset_group_mask`（重置分群面具）、`save_group_mask`（保存分群面具）。
- `POST /api/memory`：支持 `triggers`、`salience`（1～5）与 `self_stance` / `shared_moment` 类型。
- `POST /api/affection`：支持 `nickname`、`vibe_tags`、`interaction_rule`、`notable_events`、`profile_locked`。
- `GET /api/learning` & `POST /api/learning/{id}`：支持 `kind=slang_all|bot_alias|slang` 筛选与 `op='block'` 一键拉黑封禁词条。

---

## ✅ 测试与验证范围

- **前端语法检查**：`node --check` 校验 `learning.js`、`affection.js`、`improvements.js`、`habits.js` 全部通过。
- **专项自动化测试**：`tests/test_human_like_evolution.py`（6 项测试全部通过，覆盖情绪跃迁与半衰期衰减、反套路熔断与极简插话、联想触发词跨话题召回与自身 Flag 记忆、千人千面印象卡演化与防投毒、黑话多人共创自动转正与 `bot_alias` 唤醒联动、以及全部新增管理 API）。
- **回归测试套件**：`test_learning.py`（6/6）、`test_chat_habits.py`（10/10）、`test_memory_recall.py`（8/8）、`test_affection.py`（8/8）、`test_conversation_upgrade.py`（9/9）、`test_chat_personality.py`（7/7）、`test_reply_expression.py`（4/4）共 **58 项测试全部通过**。
