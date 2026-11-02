# 模型卡片独立配置

## 目的

模型设置页的四张卡片现在分别保存自己的：

- API 地址和协议
- API Key
- 模型名称
- 回复 Token 上限
- 思考强度
- 流式接收开关

切换卡片只加载该卡片的配置，不会把当前卡片的地址、Key 或模型名复制到另一张卡片。

## 存储方式

- 非敏感模型参数和活动卡片 ID 保存在服务器数据目录的 `model-profiles.json`。
- 各卡片的 API Key 保存在 `model-api-keys.enc`，使用现有 Fernet 密钥加密，权限为 `0600`。
- 旧版单一 `api-key.enc` 会在首次启动时迁移到当前活动卡片，旧文件继续保留以兼容旧辅助脚本。
- 活动卡片不写入旧版 `settings.json`，旧版本回滚时不会因为新增字段无法读取配置。
- 管理网页只收到 `api_key_configured` 布尔值，不返回密钥内容。

当前支持的卡片 ID：`flash`、`pro`、`responses`、`custom`。

## API

- `GET /api/model-profiles`：读取活动卡片和四张卡片的非敏感配置。
- `POST /api/model-profiles`：保存指定卡片；`activate=true` 时同时切换运行时使用的卡片。

普通 `/api/config` 保存其他设置时，会同步当前活动卡片的模型字段，但不会覆盖其他卡片。

## 发布

- 当前发布：`/opt/whalegirl-linux/releases/model-profiles-20261031-20261031-141254-961045296`
- 发布前备份：`/opt/whalegirl-linux-assets/model-profiles-20261031-before-20261031-141254-961045296`
- 部署报告：`/opt/whalegirl-linux-assets/model-profiles-20261031-deployment.json`

部署后健康检查通过，后台任务 19 个、失败任务 0 个，微信仍为 `logged_in`。微信 PID、消息读取服务 PID、容器 ID 和凭据均保持不变；没有发送真实测试消息。

## 复查结果

- 新增 6 项卡片配置测试，与 10 项模型协议测试共 16 项通过。
- 未配置 Key 的卡片不会借用旧全局 Key；重启后仍然隔离。
- 普通设置保存兼容公共配置中的只读卡片元数据，不会误覆盖卡片。
- 当前已有单模型配置迁移到活动卡片；旧系统没有保存过的其他接口与密钥不能凭空恢复，需要在对应卡片分别配置一次。
- 验证范围包括本地 API 和模拟前端切换、生产页面读取、健康及微信登录状态，没有把它等同于真实群聊或长期稳定性验收。
