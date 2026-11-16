# B站口语学习发布记录

已按本次授权部署；微信与B站账号在线，自动回复启用，无发送暂停，后台健康检查正常。

## 发布范围

- 当前发布：`/opt/whalegirl-linux/releases/bilibili-spoken-learning-20261105-185540-772344278`
- 稳定回滚目标：`/opt/whalegirl-linux/releases/internet-growth-20261105-exploration-tools-1793844500411647187`
- 只更新5个代码文件：growth.py的材料抽样与反思方法、growth_memory.py的表达证据校验、expression_reference.py、bilibili.py的选片提示与经验输入、growth.js的总结展示。另附2个测试文件。
- 普通聊天入口、GrowthService的任务入口和聊天召回方法按生产原样保留；本地待发布的聊天改造和B站评论@代码不在本包中。
- 原配置、角色卡、模型档案、密钥和B站会话校验一致。没有重启微信客户端、原生读取器或容器。

## 备份与存储

- 配置及凭据备份：`/var/backups/whalegirl/spoken-learning-config-20261105-185538-809688879`，目录0700、文件0600。
- 一致性数据库快照：`/opt/whalegirl-linux-assets/bilibili-spoken-learning-before-20261105-185540-772344278/state-before.sqlite3`，约443.93MiB、文件0600。
- 快照在激活前通过SQLite quick_check。后续额度补丁复用此快照，没有再复制第二份全库。
- 原发布保留，可切回；回滚只切换代码，保留新聊天、新记忆和现配置，不整库恢复。
- 最后核验磁盘可用约3.33GiB。未删除备份、图库、聊天或记忆。

## 验证与修复

- 生产源码快照与本地选定补丁合并后，本地及服务器针对性47项测试通过。单次反思额度补丁再通过15项相关检查。
- 页面脚本HTTP200，包含“已核验的字幕口语总结”及“综合表达观察（含评论与弹幕）”的展示支持。
- 部署后原排队任务恢复，读取了实质材料，但反思输出被截断。事件与用量定位为reflection阶段：5120输出Token中4313用于思考，未完成JSON。
- 最终单次反思额度调整为8192，为推理和结构化结果留空间；没有每日或累计Token额度，没有新增常驻模型调用，没有改聊天模型参数。
- 使用同一缓存视频材料做隔离实例的实际模型复测：1次请求正常结束，约18.4秒，输入10049、输出2413、总12462 Token，形成3条认识。该片段未形成已核验字幕口语条目，口语成果为0，未将讨论表达冒充台词成果。
- 此复测不重新请求B站接口，不发送微信消息，不回写生产聊天或记忆。原失败任务保持失败，后续按正常调度继续学习；不声称完成了一次新的全程线上浏览验收。
- 最终源码哈希与发布包一致，后台无失败任务协程；微信、B站登录与B站读取可用状态均正常，累计Token限制仍为取消状态。

## 回滚命令

在本机PowerShell运行：

```powershell
pwsh -File "D:\appData\qqbot\wechat-linux\deploy\rollback_spoken_learning.ps1"
```

脚本会校验当前发布、等待在途收发和学习任务完成，再切回本次发布前的稳定代码；不恢复数据库或更改角色卡。

服务端部署工具与最终包：`/opt/whalegirl-linux-assets/spoken-learning-20261105-1793876138684272350`。本机机器记录保存在 `artifacts/spoken-learning-deployment-20261105/`，包括清单、部署结果、备份核验和模型复测数据。GitHub未推送。
