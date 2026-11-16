# AnifLive-TTS 独立试用记录

2026-11-11，北京时间。主服务的语音参数与朋友圈修复见 [VOICE_DYNAMICS_AND_MOMENTS_20261111.md](VOICE_DYNAMICS_AND_MOMENTS_20261111.md)。本文只记录新引擎的实际试用，不把原 GPT-SoVITS 的音频当作 AnifLive 成果。

## 来源、环境与隔离

- 参考并实际读取 https://github.com/Hiruynk/AnifLive-TTS 的 README、安装文档、转换器、运行时、API 和情感参考实现。
- 使用官方 v1.5.0-beta 完整源码发布包，SHA256：`c4c8fe714e52fc57495fecaecbac9f7cf021584380b6bcef349cccede33b7d21`。
- 许可为 PolyForm Noncommercial 1.0.0；上游第三方许可保留在试用源码中，不能称该项目整体为 MIT。
- 所有新依赖、模型导出、临时文件和测试结果放在 `D:/appData/qqbot/voise/aniflive-trial`。未把模型、凭据或缓存放入主项目 Git。
- 复用现有 Cyrene V2ProPlus 权重和本机共享特征模型，只读使用原文件；导出模型存到新目录。
- 硬件为 RTX 5070 Laptop 8GB、约 15.2GB 系统内存。作者宣称的延迟基于另一台 5070 Ti 16GB，不能直接套用。
- 原 GPT-SoVITS 环境保持 Torch 2.7.1+cu128 / Transformers 4.50.0。独立导出环境使用 Torch 2.10.0+cpu / ORT 1.22.0；GPU 试用环境使用原 CUDA Torch 加独立 TensorRT 11.2.1.2，不宣称完全符合官方锁定依赖。
- 本轮允许按需启动试用。GPU 阶段暂时通过原有启停接口释放旧引擎显存，并保留带请求 ID 校验的恢复操作，避免覆盖用户后来更改的启停选择。
- 试用端口仅绑定 `127.0.0.1:9883`，不接微信发送、不改服务器语音上游、不开放公网接口。

## 已解决的本机兼容问题

1. `jieba_fast` 缺 Windows wheel：使用电脑现有 MinGW 编译器编译，独立环境安装通过，未安装系统编译套件。
2. 完整源码要求 Torch 2.10，原有环境为 2.7：新建 CPU 导出环境，未升级原有环境。
3. Transformers 5.5 在此导出器遇到半精度载入及 mask tracing 错误：导出使用 4.50；试用副本的 SSL/BERT 导出显式转换 FP32，权重和校验容差未变。
4. 默认 CPU 指令路径的 GPT cache 与 ORT 存在微小数值偏差：8 个 seed、4 种 ORT 优化级别对照后，使用 `MKL_CBWR=COMPATIBLE` / `ATEN_CPU_CAPABILITY=default` 通过原有严格校验；保留 oneDNN，未放宽门槛或跳过失败项。

原始失败日志、未通过报告和未完成导出均保留。成功的完整导出为 `export-onnx/portable210`：9 个阶段、105 项输出校验，105 通过、0 失败。混合精度转换依照上游策略保留 GPT/SSL/VQ 等 FP32 阶段；缺可选 onnxsim 时保留可校验图。

## 实际运行结果

9 个 TensorRT 引擎已构建完成，耗时 576.68 秒。原始 `models/cyrene` 的声码器在本机存在原生运行崩溃，保留作为失败对照；兼容候选 `models/cyrene-win-opt0` 已通过官方九引擎完整验证和真实中文合成，不使用 PyTorch 回退。两者不能混同。

Windows 启动容量判断需要适配：NVML 报告 8151MiB，CUDA 报告 8123.44MiB；官方严格要求至少 8192MiB。只在试用源码中允许名义 8GiB 显卡至多 128MiB 的驱动保留差，另外要求至少 4GiB 实际可用显存。未伪造 CUDA 容量，也没有启用 CPU/PyTorch 回退。

进一步的真实失败：Windows 事件日志记录 `nvinfer_11.dll` 中的 `0xc0000005`；启用 faulthandler 后定位为 `TRTModule.__call__` 的 `execute_async_v3`，由普通 SoVITS 预热触发。仅加载这一个声码器、低显存占用和显式同步输入时仍复现，所以不能认定只是总显存不足。

试用副本还保留了 Runtime 对象，确保其存活覆盖 Engine 使用期；这项生命周期修正没有消除上述崩溃，不能记作根因已经修复。相同图和精度下，仅将两个声码器改用优化级别 0、512MiB workspace、无辅助流重建后，单模块执行及完整运行均通过。其他七个引擎维持原构建设置。候选包独立记录覆盖参数、哈希和指纹 `bcad74142564b6a9f8d8f32f`；未覆盖原包或放宽数值校验。尚未进一步定位是哪一条优化导致原生崩溃。

完整加载还遇到 Windows 韩语词典的两个发行包接口差异。试用源码兼容 `dictionary_path` / `DICDIR`，校验词典文件存在；真实韩语分词和全部五种语言的前端预热通过。英文组件会检查 corpus 的 zip 路径，已将三个哈希核验过的离线词典压缩包放在对应路径，避免仅有解压目录时启动尝试下载。五种语言前端预热不等于五种语言音质均通过试听。

北京时间 2026-11-11 23:47–23:48，候选服务仅在本机 HTTP 9883 完成以下真实合成：

| 项目 | 实际结果 | 口径 |
| --- | --- | --- |
| 同一句中文完整 WAV，3 次 | 中位 469.20ms，范围 443.44–512.15ms | 输出 2.587 秒，32kHz 单声道 PCM16，非静音 |
| 同一句流式 PCM，3 次 | 首个非静音块中位 153.06ms | HTTP/1.1 复用连接，首块阈值 abs(PCM16)>32 |
| 短句 | 361.75ms 返回 1.677 秒 | HTTP 200 |
| 含数字、时间的句子 | 952.41ms 返回 5.132 秒 | 运行日志确认 2 段 |
| 较长中文 | 3.233 秒返回 16.957 秒 | 运行日志确认 5 段 |
| 不支持的 speed=1.1 | 明确返回 HTTP 400 | 没有悄悄忽略参数 |
| 用后健康 | ready=true，active_requests=0 | 未发送微信 |

六次短句请求后的全系统显存采样约 6626–6665MiB，系统可用内存约 4357–4387MiB。显存包含桌面和其他程序，采样发生在请求后，不能当作本引擎独占占用或峰值。此为固定种子的小样本实际冒烟测试，不是长期负载、音质或作者性能基准复现；流式数据到达也不等于扬声器首音或微信语音条送达。

证据：`official-enqueue-validation.json`、`anif-real-acceptance.json`、`runtime-cases.json`、`serve.stderr.log`。实际试听文件 `anif-plain.wav` 和 `anif-multiple_segments.wav` 已生成，人工音色、发音完整性和情感自然程度尚待试听，不能由非静音检测代替。

## 声音表现力的边界

- AnifLive V2ProPlus 通过带文本的参考录音做情感条件控制，安装运行时本身不会自动产生更丰富的情感。
- 已从用户现有音频和对应文本中整理 6 个候选，存于 `reference-candidates`；尚未人工试听核验，保持 `manual_verified: false`，没有冒充已确认的情绪素材导入。
- 该版本 API 当前固定 `speed=1.0`，不能直接替换接受原生可变语速的旧桥接服务；正式接入前还需适配能力声明、后处理与缓存参数，不能静默忽略 AI 选择的参数。
- 原有语音已启用 AI 按单条语境选择语速、音高、音量和停顿，与此试用独立；主观音质仍需试听。

## 复现与恢复

关键脚本在试用目录：`export_trial.py`、`probe_gpt_parity.py`、`build_trial.py`、`run_anif.py`、`benchmark_trial.py`、`old_voice_trial_control.py`、`trial_runtime.py`、`validate_trial.py`、`probe_sovits.py`、`build_decoder_variant.py`、`assemble_variant.py`、`test_runtime_cases.py`。

- `build_trial.py` 仅复用已通过全部严格检查的 portable ONNX，随后调用官方图检查、引擎构建和模型包逻辑；不会把未通过的旧导出伪装成成功。
- 新输出须使用新标签，不覆盖已有证据。包装脚本保留失败产物，不执行目录清理。
- 旧引擎恢复通过 `old_voice_trial_control.py resume`，只有服务控制仍是本次试用发出的暂停请求时才恢复。
- 无需线上数据库迁移或还原，原有微信、角色卡、聊天和互联网学习系统未因试用改变。

测试后已关闭拥有 PID/创建时间校验的试用服务，恢复旧引擎；本机状态确认 model_ready=true、cloud_confirmed=true，服务器确认 pc_voice.connected=true。服务器健康检查 23 个后台任务、0 个失败，微信 logged_in、回复未暂停，朋友圈 error 为空且 uncertain=false；未为验证发送群消息或发布朋友圈。记录见主项目 `artifacts/voice-dynamics-moments-20261111/health-after-anif-trial.json`。原 GPT/SoVITS 两个权重重新计算 SHA256，与试用前相同。

## 转换与使用边界

转换只需在准备模型包时执行：原 GPT `.ckpt` 和 SoVITS `.pth` → 经严格输出校验的 ONNX → 本显卡的 TensorRT 引擎。无需重新训练，原权重保持不变；平时启动直接加载已构建引擎。换权重、显卡或关键 TensorRT 运行时版本后通常需重新构建，不能把本机引擎当作通用跨设备权重。

目前结论是“现有 Cyrene 模型在本机经过兼容处理后可合成”，不是“对旧接口和全部功能完美兼容”。正式替换前还需要语速能力适配、情感参考试听与授权、云端握手适配和更广泛稳定性验证。本轮未切换正式云端语音提供方。
