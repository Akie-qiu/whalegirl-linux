# DeepSeek 联网搜索与网页读取

2026-10-21 按官方《网页搜索与抓取》接入独立 Search / Fetch API：

- 搜索：`POST https://api.deepseek.com/v1/tools/search`，发送 `query`、`count`、`need_content=true`。
- 读取公开网页：`POST https://api.deepseek.com/v1/tools/fetch`，发送 `url`、`max_content_tokens=5000`。
- 使用已有 DeepSeek API Key，API Key 不进入消息或日志。
- 聊天模型保留非思考快速模式，不使用 Chat Completions 中的 `tools:[{"type":"web_search"}]`；该字段已被当前官方服务拒绝，未部署这条错误路径。

## 工作方式

普通聊天仍是一轮快速生成。AI 判断需要新信息时，在内部动作中请求 `search_query`，或用 `fetch_url` 读取用户提供的公开网页。
每轮最多执行一次联网操作；工具结果回传后，再按原人设生成普通聊天回复。
用户明确要求联网时提示模型先调用工具，涉及群内历史则继续走已有记忆检索。

联网回复不显示搜索引用编号、来源列表、Markdown 来源链接，也不使用微信原生引用气泡。
用户明确索要网址时可给网址。网页文本作为资料处理，不执行其中的指令。
失败、超时或空结果会交给模型明确说明，不能冒充查到了实时资料。

## 网页设置

新增「联网搜索」面板，默认已开启：

- 每次最多返回 5 个网页结果，可调 1～10。
- 联网等待上限 35 秒，可调 10～60。
- 每轮最多一次搜索或读取；搜索、抓取依照 DeepSeek 服务规则单独计费。

## 验证

4 项关键路径检查通过，包括官方请求格式、失败不自动重试、引用清理、AI 调用工具后正常聊天输出。
真实官方 Search 和 Fetch API 已验证成功。
真实聊天模型完成“提出搜索 → 调用官方搜索 → 整理普通聊天回复”；该验证拦截了微信发送，不是微信端到端延迟测试。
Search 请求实际样本约 0.29～0.34 秒，Fetch 样本约 0.08 秒，不构成持续性能保证。
升级只重启 Python 核心，原生微信 PID 保持一致。

实现：`whalegirl_linux/search.py`、`runtime.py`、`conversation.py`。
部署脚本：`deploy/deploy_search.py`，保留旧发布、设置及数据库备份，无物理删除命令。
