# OpenAI Responses 接口接入

## 使用

刷新后台，在「模型设置」选择「OpenAI Responses」，检查地址后保存。
卡片保留当前模型名称和密钥，将已有的 `/chat/completions` 路径改为同前缀的 `/responses`。
也可用「接口协议」选择自动识别、Chat Completions、Responses 或 Anthropic。
自动识别时，完整地址以 `/responses` 结尾才选择 Responses；旧配置默认 `auto`，继续原有行为。

本次只新增选择能力，没有更换生产模型、密钥或保存的接口协议。
当前中转站的 `/responses` 已用原有模型完成一次独立实际连接，返回「连接正常」。

## 实现范围

- 共享引擎转换 `messages` 为 `input`，系统提示词使用 `instructions`。
- 图片块由 `image_url` 转为 `input_image`，保留 data URL、URL 和 detail。
- `max_tokens` 转为 `max_output_tokens`；可选 temperature 不发送，兼容拒绝采样参数的推理模型。
- 本地继续管理会话历史和记忆，Responses 请求使用 `store=false`。
- JSON 模式转为 `text.format`，支持 json_object 和 json_schema。
- 仅读取 output 中的 assistant message 正文，不把 reasoning item 当聊天内容。
- 支持 Responses 流式文本事件与最终用量；计入输入、输出、缓存和推理 Token。
- 空正文、failed/incomplete 状态不会作为成功结果发送，也不因空正文重复付费请求。
- 参数／权限类 4xx 不重复请求；超时、限流和服务端错误沿用已有重试机制。
- 聊天、识图、表情整理、朋友圈、贴吧和主动聊天创建的模型客户端均传递协议配置。
- 独立生图接口和 DeepSeek 专用联网接口保持各自的原有协议。

Responses 的 Token 上限包含推理消耗，设置过低可能没有可见正文；后台提示会说明这一点。
中转站是否支持具体模型、识图与 JSON 输出，仍由该站能力决定。

## 验证与部署

- `tests/test_responses.py`：11 项专项检查通过。
- 相关旧测试合计运行 107 项，103 项通过、4 项失败；未把该轮报告为全部通过。
- 失败项来自 `test_core` 的收图入队、异常发送状态、重复文本发送、登录界面读取次数断言。
  前两项和登录读取断言已用部署前生产 runtime 重现；重复文本用例只涉及本次未修改的 gateway，耗时约 50 秒。
  本次没有修改这些旧测试或扩展收发逻辑来迎合断言。
- 桌面 1280、手机 390 宽度验证：4 张卡片、协议保存、旧卡片切回、模型和密钥保留、模型测试按钮均通过，无脚本错误及面板横向溢出。
- UI 验证的配置 POST 与模型测试 POST 均在浏览器内模拟，没有改变生产配置。
- 单独实连仅发送「只回复：连接正常」给模型接口，没有发送真实微信消息。
- 部署只重启 Python 核心；微信 4.1.1.8、原生读取服务 PID、容器重启计数、资源限制及配置／密钥文件均保持。
- 核心健康检查正常，14 个后台任务无失败；重启后再次确认微信为 `logged_in`。

部署报告：

```text
/opt/whalegirl-linux-assets/responses-deployment.json
/opt/whalegirl-linux-assets/responses-live-verification.json
```

部署脚本：`deploy/deploy_responses.py`。
UI 检查：`deploy/verify_responses_ui.cjs`。
本地 UI 截图与构建清单：`test-runs/responses-build-20261024/`。
本次没有推送 GitHub，没有删除旧发布、备份或业务数据。

## 官方依据

- https://developers.openai.com/api/docs/guides/migrate-to-responses
- https://developers.openai.com/api/reference/resources/responses/methods/create
