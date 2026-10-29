# 独立联网搜索接入（2026-10-29）

## 问题与修复

原有搜索代码检查聊天接口域名，并复用聊天模型的 HTTP 客户端和鉴权。切换到中转接口、其他模型或协议后，搜索会被拦截，或者使用错误的密钥。

现在搜索和网页抓取固定连接 DeepSeek 官方 `/v1/tools/search`、`/v1/tools/fetch`，使用独立加密保存的官方密钥。聊天模型继续使用原有接口和密钥，搜索结果回传给同一个聊天模型组织回答。

- 官方搜索密钥保存在数据目录的 `search-api-key.enc`，沿用现有 Fernet 加密，权限为 `0600`；不写入 Settings JSON、公开配置响应或 Git。
- 后台「按需联网搜索」增加独立密钥输入框和保存状态；留空保留原密钥。修改聊天模型不会覆盖搜索密钥。
- 未单独配置搜索密钥时，仅允许复用 `https://api.deepseek.com` 官方聊天接口的密钥，绝不把中转密钥发给官方搜索。
- 非流式 Chat Completions 可以调用原生搜索函数；Responses、Anthropic 和流式路径继续使用现有 JSON 工具调度。
- 普通回复、主动聊天均使用同一搜索服务；原有引用清理、隐私过滤、超时和用量统计继续有效。

## 速度与缓存

没有新增逐条探测请求或额外模型判断。普通闲聊不增加请求；需要联网时沿用「决策 → 搜索 → 回答」流程。独立 HTTP 客户端复用连接，系统人设和上下文构建保持原样。支持函数工具的中转接口会首次获得固定的搜索工具定义，切换时可能需要重新预热缓存；不能用单次验收保证长期命中率。

## 验证与部署

- 本地及服务器各通过 25 项定向检查，覆盖独立鉴权、密钥轮换、协议切换、模型设置保存、普通回复、主动聊天和缓存布局。
- 前端脚本语法及差异检查通过；1280、390 像素界面完成保存密钥和切换模型的浏览器检查，无脚本错误或面板横向溢出。
- 提供的密钥实际调用官方 Search 和 Fetch，均返回 HTTP 200。
- 当前已配置的中转模型完成公开资料端到端验证：模型请求搜索、官方返回 3 条结果、原模型生成回答；搜索耗时 0.404 秒，总计 7.036 秒。未发送微信测试消息。
- 发布仅更新核心。首次上线校验未通过后自动回退；最终发布已通过健康和配置保留检查。
- 最终服务健康：17 个后台任务、0 个失败；微信在线、无暂停，56 个原生表情可用。微信 PID、读取进程 PID、容器身份和重启计数均保留。
- 配置和聊天／生图密钥未改变；数据、旧发布和备份保留。

服务器发布：`/opt/whalegirl-linux/releases/independent-search-20261029-204530-692241700`

服务器验收元数据：`/opt/whalegirl-linux-assets/independent-search-deployment.json`、`independent-search-live-verification.json`、`independent-search-provider-check.json`。这些文件不含密钥或私人聊天原文。

## 主要代码

`whalegirl_linux/search.py`：独立连接、官方鉴权和跨模型工具注册。

`whalegirl_linux/settings.py`、`app.py`：加密密钥持久化、公开状态及保存入口。

`whalegirl_linux/runtime.py`：注入配置、回收搜索连接。

`web.html`、`habits.js`、`console.js`：后台输入、状态和统一保存。

`tests/test_independent_search.py`：鉴权隔离和工具调度回归。

`deploy/stage_independent_search.py`、`deploy_independent_search.py`：按任务差异合并现网代码，校验后只切换核心；验收脚本不发送微信消息。
