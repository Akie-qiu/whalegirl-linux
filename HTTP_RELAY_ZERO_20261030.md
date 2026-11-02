# HTTP中转接口与零等待间隔

2026-10-30，用户要求处理模型设置中的JSONDecodeError，接入`http://154.9.235.108:8080/v1`，并把“同一会话最短回复间隔”和“所有会话两轮回复最短间隔”改为0。

- 模型地址原来只允许HTTPS，导致HTTP中转无法保存。api_url现在允许HTTP/HTTPS、主机及端口，基础地址/v1继续由现有引擎补为/v1/chat/completions；图像接口原协议要求保持。原有禁止地址内嵌凭据、查询参数及片段的规则保留。
- 两个间隔字段的后端下限和网页输入下限均改为0。0取消额外的跨轮等待；消息分段、AI等待对方补充和发送额度各自保持现有设置。
- HTTP200返回网页的接口不再显示原始JSONDecodeError：非流式普通请求与工具请求都明确提示“返回了网页，不是模型数据，检查API地址”；不输出上游正文，也不重复请求同一个错误网页。正常JSON、Token和缓存用量记录不变。
- 首次新接口鉴权成功，后续后台切到官方Flash配置后，当前保存Key在新接口得到401。用户随后提供新Key，已验证其有权访问模型列表，包含deepseek-v4.1-flash。密钥不写入本文件、源码或Git；部署后仅通过既有配置API加密保存。
- 本地20项定向检查通过，涵盖HTTP及端口、零值保存、保留其他配置、网页响应错误、正常及工具请求、缓存用量和原有模型协议；1280/390宽度的模型保存、0间隔保存、刷新回填检查通过。
- 增量文件仅5个：settings.py、upstream/ai_engine.py、console.js、web.html、tests/test_http_relay.py；本轮基线artifacts/http-relay-zero/local-before/。不覆盖其他未提交工作，不推送GitHub，不删除数据。
- 部署脚本deploy/stage_http_relay_zero.py、deploy/deploy_http_relay_zero.py；只切换核心，保留原生微信4.1.1.8、读取进程及登录；等待在途后台成长任务完成。

## 发布与验收

- 当前发布 `/opt/whalegirl-linux/releases/http-relay-zero-20261030-162349-287296134`；回退前版本sticker-initiative-20261030-132257-653761446保留。
- 首次部署的校验要求表情目录非空；因用户当前关闭机器人，目录按设计未加载而误判，已自动回退。修正部署包装器：总开关关闭时保留原登录状态，不要求启动表情目录。随后服务器20项检查及部署核对通过。
- 原生微信PID、读取PID、容器、原配置和凭据均在部署期间保持；随后按本轮授权通过既有配置API保存新HTTP地址、新密钥、OpenAI协议和deepseek-v4.1-flash，将chat_cooldown/min_send_interval保存为0。其他设置比较一致，主开关仍为false。
- 后台“测试已保存模型”实际成功，返回“连接正常”，5576ms，非流式；这只是模型连接耗时，不是微信群完整回复耗时。微信logged_in，health=ok，18个后台任务、0失败、无暂停。
- 未发送额外微信测试聊天，保留用户关闭机器人总开关的状态；开启后才会自动回复。
- 部署报告 `/opt/whalegirl-linux-assets/http-relay-zero-deployment.json`；连接及设置报告 `/opt/whalegirl-linux-assets/http-relay-zero-20261030/connection-verification.json`。
- 新密钥只经stdin传给维护脚本，再通过本机配置API加密保存；未写入维护脚本、源码、报告或Git。部署与配置脚本deploy/configure_http_relay.py不内置密钥。
