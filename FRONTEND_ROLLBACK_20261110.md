# 前端回退记录

2026-11-10（北京时间），按管理员要求撤销 Gemini 在 21:33 上线的深海蓝界面，恢复同日 20:34 上线的蓝粉莫兰迪与清晰字体版本。

## 范围

仅恢复 `web.html`、`studio.js`、`studio.css`、`console.css`、`personas.css`、`sticker-library.css`、`images.css`、`recovery.css`。HTML 给恢复的资源添加 `20261110-ui-rollback` 缓存标记。

本地工作树和线上八个文件已同步。其余源码、机器人配置、角色卡及生产数据未修改；没有重启核心、原生 reader 或微信容器，没有发送测试消息。

## 备份与来源

- 线上回退前八个文件：`/opt/whalegirl-linux-assets/frontend-rollback-20261110/before/`。
- 本地回退前八个文件：`artifacts/frontend-rollback-20261110/before-local/`。
- HTML 与工作室样式来自 `blue-rose-font-20261110-r2/payload/`，工作室脚本来自 `blue-rose-ui-20261110-r1/payload/`。
- 其余五个 CSS 来自历史发布；哈希在多个既有发布中一致，console.css 也与本地 UI 改版前备份一致。
- 回退工具在写入前检查线上八个文件、来源、当前发布、其余源码及私有配置的哈希，拒绝覆盖并发修改。没有切换整个发布目录或回灌数据库。

## 验证

- `node --check` 恢复的 studio.js 通过。
- 本地 `tests.test_studio_console` 一项通过，覆盖静态资源和鉴权边界；有现有依赖的弃用提示。
- 线上八个资源均 HTTP 200，响应 SHA-256 与恢复载荷逐个一致。
- 未恢复的源码及 settings.json、persona-cards.json 哈希不变。
- 核心、reader PID 与微信容器启动时间均不变。
- 健康状态 ok，23 个后台任务、失败 0；自动回复开启、无暂停原因、微信 logged_in。
- 无鉴权访问管理配置继续返回 401。

证据：`artifacts/frontend-rollback-20261110/server-manifest.json` 与 `verification.json`。本轮没有重新执行生产页面全流程交互、微信发送或模型调用；不将静态检查称为这些功能的真实发送验收。
