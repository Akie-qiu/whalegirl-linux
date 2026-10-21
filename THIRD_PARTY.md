# 上游来源

## DeepSeekGirl

- 项目：`https://github.com/linxisama/deepseekgirl`
- 引用源码提交：`58178d26721c50ddb66137182fbaf7e36a2c7f7c`
- 本项目的 `whalegirl_linux/upstream/ai_engine.py`、`style_manager.py` 及鲸鱼娘人设基于该源码改造；改动包括 HTTP 校验、请求及错误处理、Linux 服务集成等。
- 未使用 Windows 1.2.46 反编译源码。

## Agent WeChat

- 项目：`https://github.com/thisnick/agent-wechat`
- 固定提交：`21fe1185a01ec7f90864a529d9bb94da648255d5`
- `references/agent-wechat-source` 作为 Git 子模块保留完整对应上游源码。
- 本项目使用其 Linux 客户端网关、GUI guard 和原生读取接口，并提供独立适配脚本。
- 构建所用固定 OCI 摘要及微信安装包哈希见 README 和 `deploy/` 脚本；安装包、镜像层不纳入源码仓库。

本文件记录来源和修改范围，不改变上游各自的授权条款，也不为上游代码重新指定许可证。
