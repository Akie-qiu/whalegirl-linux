# 双声音模型、八千代加速与后台切换交付记录

日期：2026-11-12。本文记录本次实际安装状态；早期昔涟试唱与性能记录见 `ANIFLIVE_INTEGRATION_20261112.md`，不能作为八千代测速结果。

## 当前可用方式

服务器管理网页 → **语音与唱歌** → **电脑声音模型**，可点击：

- **昔涟 · 加速**
- **八千代 · 加速**

按钮切换实际运行的模型，状态就绪后用于后续语音。电脑本机控制台也提供相同选择；两端查询同一实际状态。切换影响所有使用电脑语音的会话。电脑需要开机并启动语音服务，未连接时后台显示离线并禁用切换。

两套模型共用本机显卡，同一时间只加载一套；切换等待正在进行的合成结束，关闭旧模型后加载新模型。后台显示切换进度、失败原因及最终使用中的模型，失败时恢复此前模型。HTTP 请求只接受已安装的模型 ID，不能指定模型文件、任意路径或执行命令。

**本次完成时，实际使用的是用户最近选择的昔涟。** 八千代已安装加速包并独立加载核对，随后恢复昔涟。没有为了交付而改变用户当前声音选择。

原有 AI `send_voice` 工具继续使用当前加载的声线。语速、音高、音量及支持的停顿控制继续沿用原链路。切换声线不需要重新填写 Key。

## 实际改动

1. 接入八千代原始 GPT/SoVITS V2ProPlus 权重，保留原文件，转换出独立的 AnifLive TensorRT 11 包。昔涟的现有加速包保留。
2. 统一电脑和服务器的模型目录、状态握手与串行切换协议；自动把实际就绪的声线同步到云端默认声线。
3. 后台增加清晰的“电脑声音模型”入口，支持离线展示；修复公网 HTTP 页面没有 `crypto.randomUUID()` 时无法提交切换的问题，使用 `crypto.getRandomValues()` 生成请求 ID。
4. 本机控制台从目录刷新“加速 / 兼容”标记。已经打开的旧控制台需要关闭后重新打开才能读取更新的程序代码；关闭窗口不停止语音服务。
5. 保留此前日语链路修复：显式传递语言参数；自动识别不再因参考音频语言而强制把日语按中文处理。保留中文、粤语、英语、日语、韩语及自动识别入口。

## 八千代转换与加载证据

| 项目 | 实际结果 |
| --- | --- |
| 原始权重 | `D:/yachiyo/voice/yachiyo/yachiyo/yachiyo-new-e20.ckpt`、`yachiyo-new_e12_s684.pth`，原文件保留 |
| 参考音频 | 日语，单声道 PCM16，32 kHz，约 5.2 秒 |
| ONNX 转换校验 | 9 个阶段、105 项输出比较全部通过，沿用原 `rtol=1e-3` / `atol=1e-5`，未放宽阈值 |
| 数值适配 | 使用原模型 PyTorch 2.7.1 及 Transformers 4.50 作为参考，调整导出 LayerNorm 的等价 FP32 运算顺序；未修改权重或参考输出 |
| TensorRT 编译 | 9 个引擎全部完成，串行构建、512 MiB workspace、optimization level 0，编译约 366 秒 |
| 独立加载 | `/health` 实际报告 TensorRT-11、9 个引擎、指纹 `5a4b78ec575ffbb0885b9e24` |
| 加载隔离 | 仅本机回环 9883，无云端桥接、无微信投递；完成后停止该加载器及其拥有的子进程 |
| 恢复原选择 | 本机昔涟重新就绪，服务器确认 connected=true、active_voice=cyrene，两套目录均为 aniflive |

加载包含引擎正常启动预热。按用户要求，没有再提交试听合成、公开测试消息或性能基准；两条此前排队的试听已经取消。上述转换校验和健康检查不代表八千代主观音质、自由日语发音或实际微信发送速度已经验收。没有宣称加速倍数。

完整本机证据位于 `D:/appData/qqbot/voise/aniflive-trial/`：

- `export-status-yachiyo27native.json`、`export-onnx/yachiyo27native/validation_report.json`
- `build-status-yachiyo27native.json`、`yachiyo-build-main.log`
- `yachiyo-package-readiness.json`、`yachiyo-package-load.log`
- `before-yachiyo-acceleration-20261112/operation.json`
- `server-console-final-check.json`

## 运行与部署核对

实际模型在电脑运行，服务器不保存大型权重或 TensorRT 包。本机目录：

- 模型选择：`D:/appData/qqbot/voise/runtime/engine.json`
- 可切换目录：`D:/appData/qqbot/voise/runtime/voices.json`
- 八千代加速包：`D:/appData/qqbot/voise/aniflive-trial/models/yachiyo-win-opt0`
- 昔涟加速包：`D:/appData/qqbot/voise/aniflive-trial/models/cyrene-win-opt0`

服务器当前发布仍为 `/opt/whalegirl-linux/releases/pc-voice-20261111-183639`。最后一次后台更新仅替换核验过哈希的 `whalegirl_linux/audio.js` 和 `deploy/pc_voice_control.py`，部署前保留原文件。没有整树覆盖其他代理的修改。

最后核对：核心 health=ok，23 个后台任务、0 失败；微信 4.1.13.26 在线，回复未暂停；原生 reader PID 未变；本次界面补丁前后的 settings、角色卡、admin.token、gateway.token 哈希一致。界面补丁没有重启核心、微信或 reader。原有声音切换流程会通过音频配置接口同步当前默认声线，这是预期行为。

服务器根盘约余 909 MiB，本次只传输少量源码，不传模型、不制作整库快照、不删除备份。没有推送 Git。

## 备份与回滚

本机转换及选择基线：

`D:/appData/qqbot/voise/aniflive-trial/before-yachiyo-acceleration-20261112`

回退八千代到原 GPT-SoVITS 后端，保留当前昔涟选择：

```powershell
& 'D:/anconda/envs/GPTSoVits/python.exe' -X utf8 'D:/appData/qqbot/voise/aniflive-trial/manage_yachiyo_acceleration.py' rollback
```

脚本校验目录与当前引擎；若之后有新修改，会停止而非覆盖。若正在使用八千代，会通过正常切换流程重新加载原后端；昔涟保持选择。

服务器声音切换接口基线：

`/opt/whalegirl-linux-assets/voice-selection-20261112`

本次后台显示补丁基线：

`/opt/whalegirl-linux-assets/voice-model-console-20261112`

只回退本次两处后台显示文件：

```powershell
ssh -o BatchMode=yes root@120.46.186.50 'python3 /opt/whalegirl-linux-assets/voice-model-console-20261112/stage/deploy/deploy_voice_model_console.py rollback'
```

该命令带发布路径和文件哈希保护，不恢复数据库、不恢复旧聊天和角色卡。早期单模型回滚脚本的基线已过时，不应用来覆盖当前双模型配置。以上回滚代码已准备，本次没有为了验证而执行正式回滚。
