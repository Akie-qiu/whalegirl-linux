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

## dsh-bili-humanism

- 来源：[InariNGT-Ofiicial/dsh-bili-humanism](https://github.com/InariNGT-Ofiicial/dsh-bili-humanism)，参考提交 `2ec50417e64a72a28b9445c06872d7f14d9dbf24`。
- 借鉴真实材料覆盖、来源核对、事实/观点/推测分离、可修正反思和风险暂停；`bilibili_client.py` 的公开 WBI 签名算法按 `lib/wbi.mjs` 适配为 Python。
- MIT 原文保留在 `whalegirl_linux/vendor/humanism/LICENSE`。没有引入 DSH、其浏览器凭据提取器或 B 站写入执行器。

## fishtalk

- 来源：[Nozzi2455/fishtalk](https://github.com/Nozzi2455/fishtalk)，固定 `0.3.6 / bb621c3da31052e7e52b6307db3e27c508e4d502`。
- 授权、固定来源及上游 CLI 保留在 `whalegirl_linux/vendor/fishtalk/`。Python 适配保留角色人设、会话隔离和零额外模型检测。
