# 模型思考强度与流式接收交接

## 已部署

- 当前发布：`/opt/whalegirl-linux/releases/model-controls-20261025-125007-30621392`
- 上一个发布：`/opt/whalegirl-linux/releases/tieba-tools-20261025-114557-78576617`
- 部署与配置备份：`/opt/whalegirl-linux-assets/model-controls-before-20261025-125007-30621392`
- 部署报告：`/opt/whalegirl-linux-assets/model-controls-deployment.json`
- 当前服务器配置：`reasoning_effort=low`、`model_stream_enabled=true`。其余设置与密钥保留。
- 最终检查：核心健康，14 个后台任务，0 失败，微信 `logged_in`。微信 PID、读取服务 PID、容器 ID/重启计数和资源限制保持一致。

## 原因与限制

截图中本机服务器 IP 的 `/v1/responses` 请求为非流式，约 8～18 秒。原来的 `chat()` 使用 HTTP POST 一次取完整响应；代码没有传 High 思考强度，因此 High 的来源推测是渠道默认或参数转换，尚未从上游用量日志核实。

截图顶部另一个 IP 的长耗时 `/v1/messages` 请求不是这台机器人服务器的请求，不能计入本项目耗时。

当前模型名被管理员改为 `claude-haiku-5-5`。该密钥的模型列表没有这个名称，实测返回 404。列表提供 `claude-sonnet-5-5` 和 `claude-haiku-4-5-20251001` 等。此次保留当前模型选择，已提醒用户修正名称，没有擅自替换模型。

以截图的 `claude-sonnet-5-5` 做一次最小 JSON 请求：Responses、`reasoning.effort=low`、`stream=true`，HTTP 200、`response.completed`、JSON 有效，约 1678ms，首段正文约 1657ms。请求没有真实私人聊天内容。响应没有回显 reasoning effort，也没有 reasoning tokens，故只能证明接口接受该组合，不能证明渠道内部实际按 low 执行，更不能把此耗时当作真实长上下文聊天耗时。

## 实现

- `Settings.reasoning_effort`：default / low / medium / high。默认 default，不额外传参数。
- `Settings.model_stream_enabled`：默认 false；本次给当前 Claude 配置设为 true。
- Responses：`reasoning: {effort: ...}`。
- Chat Completions：`reasoning_effort`。
- Anthropic Messages：`output_config: {effort: ...}`。
- 不强行传 `thinking.disabled`，也不以低强度冒充关闭思考。
- 所有五处引擎创建点均继承设置：常规聊天、图片标签/挑图、贴吧、朋友圈、主动聊天。模型测试复用运行时引擎。
- DeepSeek 快捷卡片切换时思考强度恢复 default，原来 Flash/Pro 的禁用 thinking 逻辑保留。
- 流式收齐正文和完成事件后，`chat()` 才交付完整内容。不会把思考过程或半截 JSON 交给动作执行器。
- 处理 Responses / Anthropic / Chat Completions 的完成事件、缓存与推理用量；保留终止帧中的用量。
- 已开始的响应出现截断、错误、Token 上限时返回失败，不自动重发已开始的付费请求，不静默降级为其他协议。
- 修复 `max_retries=0` 原来完全不发请求的问题；现在表示至少执行一次，不额外重试。
- 模型测试展示请求的流式开关、思考强度和总耗时；这是请求配置，不是上游执行强度的证明。
- 控制台入口仍为“模型设置”，新增两个控件，无新增侧栏项目。

## 验证

20 项协议/参数/流式处理检查通过（`test_model_controls`、`test_responses`）。1280px 和 390px 页面验证通过：可保存新设置，模型和密钥保留，无 JS 错误或横向溢出。浏览器的配置 POST 与模型测试 POST 均为本地模拟，没有发送真实验收聊天。

本地记录：`test-runs/model-controls-20261025/`。部署脚本 `deploy/deploy_model_controls.py` 复用 `deploy/deploy_responses.py` 的发布与回退逻辑。部署完成后的配置调整另备份为 `settings-before-controls.json`，通过 API 写入并核对其余字段及密钥未改变。

## 后续

1. 用户选择渠道支持的模型名后，再观察真实聊天耗时和渠道记录中的思考等级。
2. 若显式传 low 后渠道仍显示 High，需要核对中转站的 Responses→Claude 参数转换；不能重复增加本地检查来解决。
3. 手动回退到旧代码前须一并恢复配置备份，因为旧 Settings 拒绝新增字段。
4. 此次没有推送 GitHub，没有删除数据，没有重启原生微信或读取服务。

## 查阅文档

实现前读取了 OpenAI reasoning / streaming-responses 与 Claude effort 官方文档；中转站的实际支持仍单独通过连接测试核对。文档路径如下：

```text
https://developers.openai.com/api/docs/guides/reasoning
https://developers.openai.com/api/docs/guides/streaming-responses
https://platform.claude.com/docs/en/build-with-claude/effort
```
