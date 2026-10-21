# 当前服务器中的历史脚本快照

2026-10-22 从 `/opt/whalegirl-linux/releases/reply-expression-20261022-000939/deploy/` 只读取回以下三个源码文件：

- `prepare_candidate.py`
- `rollout_groups.py`
- `qr_control.py`

它们与本机 `deploy/` 的对应历史脚本存在差异，故在此保留运行发布中的原件，不覆盖本机版本。并不表示这三个早期安装脚本应当对当前已登录的微信重新执行。

应用目录的 Python、JavaScript、HTML 和 CSS 均与服务器当前发布核对一致。原生工具中正在使用的 `chat_select_linux.py`、`native_message_reader.py`、`a11y_safe.py`、`media_locator.py` 和 `message_payload.py` 也分别与 `deploy/` 或 `whalegirl_linux/` 中对应文件核对一致。未拉取运行凭据、聊天数据、媒体、登录状态或备份原生工具。

根目录 `requirements.lock` 记录此次读取的服务器虚拟环境实际发行包名称和版本，不含下载地址或认证参数。
