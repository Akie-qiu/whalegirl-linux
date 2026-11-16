# AnifLive 接入与《小星星》试唱验收

北京时间 2026-11-12。前期模型转换与原生兼容诊断见 `ANIFLIVE_TRIAL_20261111.md`。

## 当前实际状态

电脑语音已切换为经过本机兼容验证的 AnifLive-TTS 1.5.0-beta，复用 Cyrene V2ProPlus 原权重转换得到的包。服务器继续经受限 SSH 通道连接本机适配器，AI 继续使用既有 `send_voice` 工具。电脑可以按需启动、停止；服务器不承载模型或显卡推理。

正式核心发布仍是 `/opt/whalegirl-linux/releases/pc-voice-20261111-183639`，没有整树覆盖、重启微信或原生 reader。音频设置以 API 修订从 v11 升到 v12，只将当前 PC 提供方的模型标签改为 `cyrene-aniflive-v2proplus`，保留权限、密钥、声线及其他参数。

发送前检测到总回复开关为关闭，通过现有原生网关的显式管理员发送能力发出一条试唱，未打开全局自动回复。发送完成时该开关仍关闭；最后只读检查时已经变为开启，本轮没有改写这一开关。不会用旧快照覆盖后来发生的配置变化。

## 适配内容

- 新增 `deploy/pc_voice_engine.py`：只读管理员本机 `runtime/engine.json`，限定包目录、模型家族、引擎指纹与固定回环端口；HTTP 和 AI 工具不能修改路径、引擎或权重。
- `pc_voice_supervisor.py` 使用所选引擎的启动命令及进程身份，停止时核对并清理拥有的 Python 子进程，避免 Windows venv launcher 退出而 GPU 子进程残留。
- `pc_voice_bridge.py` 保留认证、单任务、请求 ID、持久化不确定状态与缓存上限；增加 AnifLive 协议、九引擎/无神经回退响应核验和实际引擎记录。旧缓存增加 `engine` 列，默认标识旧引擎；旧 ID 不重新生成。
- AnifLive 神经图只接受 `speed=1.0`。适配器实际用 FFmpeg `atempo` 调整语速，保持音高；分句停顿使用真实的 `pause_length`，按最终语速补偿。健康握手标明 `ffmpeg-atempo` / `model-pause_length`，没有伪称神经图支持可变语速。
- 服务器已有音高、音量处理继续生效，已应用的语速不会再次加速。无需额外聊天模型审稿调用。
- 本机面板显示 AnifLive 加速状态，原启停快捷方式继续有效。
- 发现 Conda 的原 FFmpeg 在此环境退出 `0xc0000139`，新增独立静态工具到 `runtime/tools/ffmpeg.exe`，不替换原 Conda 文件。来源 `imageio-ffmpeg==0.6.0` 内置 FFmpeg 7.1，SHA256 记录在试用目录的 `ffmpeg-provenance.json`。

## 唱歌能力的真实边界

AnifLive 的当前接口是 TTS，没有歌词加曲谱的原生唱歌协议。本次没有把念歌词当作唱歌，也没有宣称此引擎能自行演唱任意歌曲。

试唱过程是：AnifLive 用现有声线合成 22 个不重复的歌词音节；按《小星星》前四句的 28 个音符及 128 BPM 节拍，用 Praat PSOLA 处理音高和时长，得到 15.000 秒单声道人声音频。依赖 `praat-parselmouth==0.4.6` 仅安装在独立试用环境，未加入 Linux 核心依赖。核心人物设定及原始 GPT/SoVITS 权重不变。

这是一段明确标记的曲谱辅助试唱，可能有拼接或合成感，不等同于专用歌声模型。生成脚本和音高核验不能替代人工试听、歌词准确性或自然度验收。

## 实际验收

| 项目 | 结果 | 证据 |
| --- | --- | --- |
| 适配器、旧协议、动态参数、认证、缓存回归 | 37 passed、4 skipped；跳过项为该 venv 没有 psutil 的本机控制测试 | `integration-tests-static-ffmpeg.log` |
| 原 PC 环境的本机控制测试 | 7/7 passed；与上项部分重叠，不能相加冒充 44 项独立测试 | 原环境 unittest 输出 |
| 时长与保留音高的真实 DSP | 440Hz 音调经 1.2 倍速处理，时长按比例缩短，音高保持；空间不足明确失败、不截断 | `test_aniflive_bridge.py` |
| 真实云端调速试听 | ready，约 5.89 秒；采用 speed 1.12、pitch +0.6、gain +1dB、pause 0.18 | 预览任务 `0cd681a1ab9e4787aa9fb6ee7ae50525` |
| 试唱文件 | 15 秒，32kHz PCM16；28 音符最大中位音高偏差约 3.50 音分 | `twinkle-render-result.json` |
| 原生语音条 | 临时测试，单条、发送尝试 1 次、state=sent；回执 local ID 997，服务端确认已核验 | `cloud-final-check.json` |
| 原生媒体时长 | 源 15 秒；录音播放含边缘静音 15.5 秒；SILK 包时长 16.02 秒 | 原生任务 `a1b5e848-23f8-4407-85a4-df51eeadb876` |
| 媒体核验边界 | SILK 编码包时长及微信服务端回执通过；`audioContentVerified=false`，未声称已解码逐样本比较或人工试听 | 原生任务 verification 字段 |
| 真实停止与重启 | 9881/9882 端口释放，显存采样约 6736→2348MiB；重启后 AnifLive ready、云端重新确认 | `integrated-lifecycle-result.json` |
| 服务器与微信 | health=ok，23 后台任务、0 失败，微信 4.1.13.26 在线、回复未暂停，朋友圈无 error/uncertain | `cloud-final-check.json` |

原生发送耗时约 33.87 秒，包含实际录音播放与确认，不能拿本机流式首块耗时替代微信语音条的完整发送耗时。手机端主观音质、歌词完整性、长期压力与自由唱歌能力仍未验收。

一次维护调用最初使用系统 Python，因缺 httpx 在任何原生发送 POST 之前失败。核对尚无原生提交记录后改用项目 venv 显式恢复同一任务；只形成一个原生任务和一次投递，没有未知结果后自动重复发送。

## 文件、备份与回滚

本机模型、环境及证据：`D:/appData/qqbot/voise/aniflive-trial`；实际音频：`anif-twinkle-15s.wav`；当前引擎选择：`D:/appData/qqbot/voise/runtime/engine.json`。

原 PC 代码备份：`aniflive-trial/integration-backup-20261111`。激活前配置及请求所有权记录：`integration-activation.json`。服务器专项目录 `/opt/whalegirl-linux-assets/aniflive-20261112` 权限 0700，保留音频配置原值、维护源码和发送证据。主数据库没有结构迁移；原有聊天和学习数据继续保留。本轮没有执行文件删除，也没有推送 Git。

恢复原引擎的一条 PowerShell 命令：

```powershell
& 'D:/appData/qqbot/wechat-linux/deploy/rollback_aniflive_pc.ps1'
```

回滚会先排空当前语音任务，恢复 GPT-SoVITS，继续使用已经修复的桥接代码；仅在当前配置仍属于本次候选时操作。云端只恢复模型标签，保留后来更新的其他设置、凭据、聊天记录及成品，不整库回灌。该回滚脚本已准备并检查，未为验证而执行正式回滚。

上游 AnifLive 源码保留其 PolyForm Noncommercial 1.0.0 及第三方许可；未将运行时、模型、登录材料或静态二进制提交到主项目。
