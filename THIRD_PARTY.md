# 上游来源

## dsh-see-now / dsh-video-storyboard

- [SAOS-3624/dsh-see-now](https://github.com/SAOS-3624/dsh-see-now)，提交93342e6122b278139281d71346314048170a034b。
- [SAOS-3624/dsh-video-storyboard](https://github.com/SAOS-3624/dsh-video-storyboard)，提交11d8b8f057a56b3e27b8a740a6f8d29e3848de87。
- video_scene.py的低分辨率HSV差按See Now方法改写；镜头变化确认、运动累计、时间编号及分镜组织参考两项目，适配为有上限的Python/Pillow离线抽样。video_media.py仅处理合法取得且体积受限的官方媒体。
- Apache-2.0原文及两份原始NOTICE保留于whalegirl_linux/vendor/scene-vision/；来源与改动见UPSTREAM.json。未引入DSH、完整实时扫描、ASR、OCR或外部凭据提取器。

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

## GSV-TTS-Lite（仅本机隔离运行环境）

- 项目：[chinokikiss/GSV-TTS-Lite](https://github.com/chinokikiss/GSV-TTS-Lite)，本机安装包版本 `0.4.7`，PyPI 项目声明 MIT。
- 仅在用户电脑的 `voise/gsv-lite-env` 安装并调用其 GPT-SoVITS V2 CUDA 图推理与 safetensors 转换接口；项目源码不复制其推理实现，服务器不安装该依赖或模型。
- 上游 MIT 许可证随本机 Python 安装包保留。模型权重及参考音频由用户提供，原始文件不修改。

## dsh-bili-humanism

- 来源：[InariNGT-Ofiicial/dsh-bili-humanism](https://github.com/InariNGT-Ofiicial/dsh-bili-humanism)，参考提交 `2ec50417e64a72a28b9445c06872d7f14d9dbf24`。
- 借鉴真实材料覆盖、来源核对、事实/观点/推测分离、可修正反思和风险暂停；`bilibili_client.py` 的公开 WBI 签名算法按 `lib/wbi.mjs` 适配为 Python。
- MIT 原文保留在 `whalegirl_linux/vendor/humanism/LICENSE`。没有引入 DSH、其浏览器凭据提取器或 B 站写入执行器。

## fishtalk

- 来源：[Nozzi2455/fishtalk](https://github.com/Nozzi2455/fishtalk)，固定 `0.3.6 / bb621c3da31052e7e52b6307db3e27c508e4d502`。
- 授权、固定来源及上游 CLI 保留在 `whalegirl_linux/vendor/fishtalk/`。Python 适配保留角色人设、会话隔离和零额外模型检测。

## 人格成长与生活自治机制研究（2026-11-06）

本轮研究参考以下具体源码，并独立适配为现有 Python、Config、MemoryBook/MemoryHub 与 SQLite 服务。没有复制这些项目源码进入生产模块，没有安装其运行时或数据库；本地研究快照在忽略的 `artifacts/personality-life-20261106/references/` 中，附固定提交和 SHA-256 清单。机制与未移植范围见 `PERSONALITY_LIFE_AUTONOMY_20261106.md`。

| 来源 | 核对提交 / 许可证 | 实际研究内容 |
| --- | --- | --- |
| [SurfCore Life](https://github.com/nefelibata5136-code/surfcore-life) | `f6991f33bdc1209010e6b68380fd4fb54619b538`；MIT，2026 Derpyu520 and qq-bridge / QSH contributors | life-settings 的 catalog、constraints、manager、runtime、tools 及设计文档；参数白名单、预览、版本、采用证据与冲突回滚 |
| [Hindsight](https://github.com/vectorize-io/hindsight) | `595b79f939b136943e28588a3e3e80811d10b569`；MIT，2025 Vectorize AI, Inc. | observations、retractions、consolidation/triage；带来源的认识、时间证据、来源撤回与重复事实处理 |
| [Letta 历史 V1](https://github.com/letta-ai/letta/tree/archive) | archive `56ba9c25552605eec89de8ed3dc6394b625c1993`；Apache-2.0 | memory/block schema、block_manager、base memory tools；只读核心块、有限上下文、版本、checkpoint 与事务恢复 |
| [Generative Agents](https://github.com/joonspk-research/generative_agents) | `fe05a71d3e4ed7d10bf68aa4eda6dd995ec070f4`；Apache-2.0 | reflect、retrieve、associative_memory；经历/思考分离、反思证据和相关性检索 |

Letta 当前 main 核对为 `8816d3a8516893bfdc0fa14bfb1235226012e295`，已将活动源码指向 letta-code；本轮明确研究历史 archive，不把它标成最新运行时。SurfCore 根 LICENSE 的 MIT 不覆盖其所有第三方依赖，本项目未引入 SnowLuma 或 DSH。今后若直接复制上述项目代码或实质片段，需随分发保留各自 LICENSE、版权、NOTICE 及修改说明。
