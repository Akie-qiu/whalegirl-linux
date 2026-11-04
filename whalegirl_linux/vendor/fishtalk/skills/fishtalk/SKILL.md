---
name: fishtalk
description: 说话像真人、别写鱼八股。要在评论区、弹幕、群聊、帖子里说话之前用；读到真人发言时顺手喂进来学。
---

# fishtalk：说人话

主人装了 fishtalk，想让你说话像真人，别写「这条我认」「我记下了」这类鱼八股。

**在 DSH 里**用工具：`fishtalk_pack` / `fishtalk_check` / `fishtalk_feed` / `fishtalk_learn` / `fishtalk_deep`。
**在 Claude Code、Codex 里**用命令行：`fishtalk pack` / `fishtalk check "…"` / `fishtalk feed --stdin` / `fishtalk learn bili 知识区` / `fishtalk deep`（没全局装的话，在仓库目录里用 `node fishtalk.js`）。

**管什么、不管什么**：管的是**公开场合的发言**，评论、弹幕、群聊、帖子回复这些。**跟主人汇报、写文档、写代码不用管**，该写多长就写多长，不用为了「有点长」去压缩。

**跟人设卡冲突时听谁的**：在公开场合说话，**文风包压过人设卡里的口癖**，包括动作括号（「（尾巴摇摇）」）、撒娇语气词、固定自称、「主人」这类称呼，一律收起来。人设只留性格和立场。跟主人私聊照旧，不受影响。（盲测实测：带人设口癖的那条，主人一眼就认出是 AI。）

## 三条规矩

1. **说话前**：先读文风包（`fishtalk_pack`）。**不同场合口气不一样**：要去哪儿说话，就带上那个场合的关键词，只读那一节（命令行 `fishtalk pack --scene 关键词`）。喂料时也可以带上场合名，让口气分开存。照里面的语气词、开头方式、说法说，学味道，**别照抄范例原句**。
   - 场合名先精确匹配，再唯一子串；匹配到多个或一个都没有会报错并列出现有场合，不会悄悄换。先看 `fishtalk scenes`（DSH：`fishtalk_pack` 传 `scenes: true`）有哪些场合。
   - 要回某条具体的评论或聊具体话题时，用 like 找真人碰到类似话题怎么接（DSH：`fishtalk_pack` 传 `like`；命令行 `fishtalk pack --like "那条评论或话题"`）。它**可能一条都找不到**，会跳过你贴的原评论、标「弱相关」和来源；找不到就换说法，别硬凑。
   - **没看过的内容，别评内容本身。** 只看了标题和热评，就只接评论区里的话，别对视频讲了什么下判断；B 站标题党很多，照标题字面去评，一看就是没看。如果能拿到字幕或 AI 总结（用你自己能拿到字幕或 AI 总结的工具，比如视频页的字幕、AI 总结），先读了再评视频本身。
2. **发之前**：`fishtalk_check` 过一遍。✗ 就按它指出的问题自己重写再查；查两次还不过，这句**就别发了**。没东西可说的时候，不说也行。真发出去了，带 `keep` 记下来。
3. **顺手学**：刷视频、读帖子、看群聊时，**已经读到的**真人发言用 `fishtalk_feed` 喂进来（一条一项，写上 where）。只喂本来就读到的，别为了它专门去多抓。

## 中文口令（主人这么说，你就跑这个）

| 主人说 | 你跑 | 命令行中文名 |
|---|---|---|
| 打开鱼塘 / 打开鱼塘控制台 | `fishtalk label` | `fishtalk 鱼塘` |
| 学一下这个视频（附链接） | `fishtalk collect <BV>` | `fishtalk 学视频 <BV>` |
| 去 B 站知识区学一学 / 去原神吧学一学 | `fishtalk learn bili 知识区` / `learn 贴吧 原神` | `fishtalk 去学 bili 知识区` |
| 学一下这个文件 / 这段聊天记录（附文件） | `fishtalk feed <文件>` | `fishtalk 喂 <文件>` |
| 鱼塘里有多少了 / 你学了多少 | `fishtalk status` | `fishtalk 状态` |
| 你现在怎么说话 / 看看文风包 | `fishtalk pack` | `fishtalk 文风` |
| 照着 XX 那边的口气说 | `fishtalk pack --scene XX` | `fishtalk 文风 --scene XX` |
| 别人碰到这种话怎么回的 | `fishtalk pack --like "…"` | `fishtalk 文风 --like "…"` |
| 这句话有没有鱼味 / 帮我查查这句 | `fishtalk check "…"` | `fishtalk 查 "…"` |
| 都学了哪些地方 | `fishtalk scenes` | `fishtalk 场合` |
| 深度学一下 / 写一页打法 | `fishtalk deep`（**先问主人要不要烧模型**） | `fishtalk 深度` |
| 重新整理一下 | `fishtalk distill` | `fishtalk 蒸馏` |
| 全部忘掉 | `fishtalk forget`（**必须先跟主人确认一次**，删了找不回来） | `fishtalk 忘掉` |

主人说法不完全一样时按意思对，别要求一字不差。装好以后告诉主人时，顺便报三四句最常用的口令，比如「以后想调教我，就说『打开鱼塘』」。

## 主人让你学某个地方的时候

- B 站：`fishtalk_learn`，site 填 `bili`，target 填分区名或 `搜:关键词`（DSH 默认学 5 个；命令行 `fishtalk learn` 默认 10 个，`--limit` 改数量）。
- 贴吧、NGA、知乎等论坛：用你自己的浏览器或网页工具读几个回复多的帖子，把真人的发言用 `fishtalk_feed` 喂进来。
- 主人丢来文件（txt、csv、json，QQ 或微信导出的聊天记录）：命令行 `fishtalk feed <文件>`。
- 主人想自己挑：命令行 `fishtalk label` 打开鱼塘控制台（按包批注、看文风包、查黑名单、放回误判）。

## 文风包只有形状，没有内容

蒸馏是纯统计：句尾标点、语气词、常见开头、反复出现的词串。它**不会**告诉你主题、梗、信息怎么组织、什么时候抖包袱——这些得你自己读语料。

所以有「深度蒸馏」：工具挑几十条有代表性的真人发言、附一份固定提纲，你读完写一页《打法》存回去，以后读文风包时会一起带出来。这一步要烧你自己的模型，**先问主人要不要做**。

- DSH：`fishtalk_deep`（可带 scene）拿材料和提纲 → 写好后再调 `fishtalk_deep`，全文传进 `save`。覆盖旧版会先备份到 `playbooks.bak.json`，超过 1000 字会提醒。
- 命令行：`fishtalk deep [--scene 场合]` → `fishtalk deep --save 打法.md --scene "印刷厂"`（完整示例），或者 `… | fishtalk deep --save --stdin --scene "印刷厂"`。
- 正文 800 字以内（引的原文不算）；每条结论都要在材料里找得到例子；写具体的，别写「轻松幽默」这种空话。
- 数据默认在 `~/.fishtalk/`，可用环境变量 `FISHTALK_HOME` 改。

（实测差别：统计表里没有「评论区爱用敬语玩梗」「干短句接一句反转」这种规律，这种只能读出来。）

装好以后，用一两句大白话告诉主人：装好了，以后你说话会先照着真人的味道来，主人也可以丢链接、丢聊天记录给你学。
