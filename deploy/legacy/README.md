# 早期部署脚本

本目录收齐原先放在本机 `artifacts/` 目录中的 Python 部署和检查源码；原件仍保留在本机。

- `deploy-console-options.py`：管理密码、会话登录及表情积极性早期升级。
- `deploy-public-console.py`：公网管理入口配置。
- `deploy-quick-tunnel-origin.py`：临时 HTTPS 入口及来源校验配置。
- `deploy-quote-replies.py`：早期原生引用回复升级。
- `deploy-safety-protection.py`、`check_safety_guards.py`：本地运行保护升级和针对性检查。

原先的密码字面量已改为从 `WHALEGIRL_CONSOLE_PASSWORD` 环境变量读取；仓库不包含现有管理密码。其他凭据仍由脚本读取服务器私有运行文件，不写入源码。

这些脚本保留其历史发布目录与前置条件，不应当作新服务器一键安装器，也不要在已有当前服务上顺序重跑。运行前阅读脚本；删除相关操作遵守根目录 AGENTS.md。
