# 朋友圈编辑器生命周期修复（2026-11-13）

修复已部署到 `spicy-banter-20261113-200250-780092586`，保留该发布中的人设、主动私聊、视觉理解、语音及 Gemini 更新。仅替换朋友圈相关代码，没有推送远程仓库。

## 原因与改动

选图窗口 `Select File` 的兼容修复已生效。这次持续阻塞来自退出流程：带图片的编辑器点击 Cancel 后还需要确认放弃编辑，旧代码随后发送 Escape，撤回了退出操作，且吞掉了关闭失败。上次不发布探测的收尾验证漏掉了这一状态。

本次完成：

- 打开选图前先完成素材校验与可读副本准备。点击 Open 后必须确认选图窗口消失、发布编辑器就绪，再填写正文。
- 取消自己创建的编辑器时，识别本次新出现且唯一的放弃编辑按钮，随后确认编辑器与朋友圈窗口都已关闭；不再用盲目 Escape 或忽略异常结束。
- `deploy/moments_session.py` 记录程序创建编辑器的归属。只保存账号范围、进程／窗口身份、编辑器引用和正文哈希，不另存正文。填写后通过 AT-SPI 核对内容与预期一致。
- 原生进程退出后，仅在账号、窗口、微信进程、编辑器引用和正文指纹都匹配、原进程已退出且尚未进入提交阶段时自动清理。人工改动或无法证明归属的草稿保留。
- 读取遇到未知草稿时使用已有同步缓存，不反复最小化、重新打开同一个编辑器。
- `whalegirl_linux/moments_health.py` 在原 SQLite 中增量添加 `moments_ui_health` 小表，保存界面状态及 30～300 秒有上限的退避。界面明确受阻时暂停自动生成、发布及评论提交，保留缓存读取；不消耗发布时段，不循环付费生成。
- 窗口退出失败与发布结果分开记录。已得到原生发布确认的任务继续保持 sent，不因清理失败变成失败或被重新发布。提交结果不明仍不自动重发。
- 后台通过 `ui_health` 和中文原因展示状态，修复后正常读取会清除阻塞状态。

涉及生产源码：

1. `deploy/moments_ui.py`
2. `deploy/moments_session.py`（新增）
3. `whalegirl_linux/moments.py`
4. `whalegirl_linux/moments_health.py`（新增）

原生适配器目录同步部署了前两个文件。没有修改模型协议、流式设置、角色卡、发送授权或账号保护规则。

## 验证

- 原相关本地套件 117 项通过；最后新增的 20 项稳定性专项通过。
- 最终 Linux 独立候选套件 **118 项全部通过**，包括旧朋友圈、评论导航、素材选择、恢复、生活世界和新生命周期检查。Linux 与本地既有测试版本相差一项，报告分别记录，不合并虚增数量。
- 真实微信连续验证：带图并填写检查文字后取消，完整退出；模拟选图阶段失败后清理，完整退出；留下带指纹的本程序编辑器并结束原进程，新进程成功恢复；恢复后再次选图与退出正常。
- 原生探测新增朋友圈 **0 条**，最终没有遗留编辑器或选图窗口。没有把这些探测描述为已发布朋友圈。
- 补丁上线后后台 `ui_health=ready`、错误为空，微信在线；23 个后台任务、0 个失败。
- 不重启微信客户端和原生 reader，保持微信 4.1.13.26；加载核心代码时仅短暂重启核心服务。
- 旧失败记录保留，没有篡改成成功。数据库改动为可向后兼容的小表，回滚不回灌历史聊天、记忆或朋友圈数据。

## 备份与回滚

服务器：`/opt/whalegirl-linux-assets/moments-stability-20261113`。

保留 `before/`、`native-before/`、`manifest.json`、`linux-tests.log`、`native-probe.json` 及源文件哈希。源代码回滚命令：

```powershell
ssh root@120.46.186.50 /opt/whalegirl-linux/venv/bin/python /opt/whalegirl-linux-assets/moments-stability-20261113/deploy_moments_stability.py rollback
```

回滚会检查当前版本和源码、等待空闲，仅恢复旧代码；新增模块可以保留但不被旧入口使用，小表不会删除。发现其他代理修改时拒绝覆盖。

## 本次后续剧情帖

用户明确要求修复完成后发布一条介绍 Gemini 两款新模型的剧情朋友圈。内容依据 Google 官方 11 月 10 日的 [Carbon 发布说明](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-4-carbon-now-available/) 与 11 月 12 日的 [Hydrogen 发布说明](https://blog.google/innovation-and-ai/models-and-research/gemini-models/introducing-gemini-4-hydrogen/)，以同人拟人小剧场呈现，不冒充官方角色。

原生生图中转任务 `fdbf0b6b654045eaaa2620ac5726782f` 返回结果未确认，按既有规则没有重复提交，其原记录保留。两次内置 imagegen 调用也因网络错误失败。用户明确选择继续新漫画后，使用机器人现有生图服务的另一可用模型 `gpt-image-2`，单独文生图一次成功，没有改变日常 `gpt-image-2.6` 设置。生成的三格漫画经过人工视觉检查，以 1024×1024 导入图库；具体提示词保存在 `gemini-image-request.json`，生成记录为 `comic-alternate-attempt.json`。

剧情朋友圈已于 **2026-11-14 12:36（北京时间）** 发布成功，原生回执 `15054456007263335007`，任务 `f9e1e62492345602817330a7bba2d294` 为 `sent`，同一来源仅一条。实际发布证据保存在服务器同目录 `gemini-post-result.json`。配图在本地 `D:/appData/qqbot/maintenance/moments-stability-20261113/gemini-story-final.png`。

当天自动发布额度已用完；本次手动发布按用户明确要求额外保留一条，完成后恢复原每日 3 条配置，不放宽微信账号本身的频率保护。

## 仍有边界

第三方生图或模型接口仍可能超时、限流或返回不确定结果。新流程保留真实状态、不重复提交，不能保证外部服务永远可用。未知人工草稿和结果不明的提交仍会保留并要求核对，避免误删或重复发帖。

本地证据：`D:/appData/qqbot/maintenance/moments-stability-20261113/`。
