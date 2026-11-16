# 电脑语音推理与云端微信发送：现状核对

本文保留实施前的可行性检查记录。随后已完成真实推理、云端接通、微信语音条发送及 AI 工具接入；最新结果见 [PC_VOICE_BRIDGE_20261111.md](PC_VOICE_BRIDGE_20261111.md)。下文的“未验证”描述指当时的检查阶段。

## 已确认

- 新角色权重在`D:/appData/qqbot/voise`：GPT约155MB、SoVITS约173MB，文件名为CyreneV3.7。
- SoVITS文件头为06，按GPT-SoVITS官方规则属于v2ProPlus。其文件头经过官方版本标记处理，普通ZIP读取报错不能据此认定模型损坏；本次仅按官方格式处理元数据，没有执行checkpoint pickle代码。
- 参考音频ZIP含2567段WAV和1份XLSX，未全量解压。
- 可复用的程序在`D:/yachiyo/voice`，具备`api_v2.py`；已有启动脚本`D:/yachiyo/start-gpt-sovits-api.ps1`。
- Python环境为`D:/anconda/envs/GPTSoVits/python.exe`；真实导入核验为PyTorch2.7.1+cu128、CUDA12.8，能识别RTX5070LaptopGPU，显存约8GiB。另已完成小型FP16矩阵运算并同步返回正确结果，证明实际CUDA计算可用；这不等于角色模型已成功推理。
- 中文BERT和HuBERT目录存在；该组权重及全部辅助依赖能否成功加载仍待实际测试。
- 检查时没有9880端口的语音服务在运行。电脑总内存约15.2GiB、空闲约3.4GiB，真实加载显存与合成速度尚未测量。

## 推荐接法

云端沿用现有音频任务与send_voice工具。电脑运行独立的昔涟推理配置，只收必要的朗读文本和已授权预设，不接收完整聊天历史、角色卡或云端密钥。

电脑主动建立受限加密通道，服务器通过该通道请求本地合成API；无需向公网暴露GPT-SoVITS原始端口、控制端点或家庭路由器端口。正式实现需配置专用、受限凭据，避免给音频连接Shell或部署权限。

电脑返回WAV后，云端继续使用现有下载/格式核验、转码、时长控制、串行发送和微信回执。复用既有异步任务与幂等来源，超时或离线不会自动重复生成或误报已发送。

电脑关机、休眠或推理服务退出时，本地声线不可用；文字聊天继续运行。是否使用Gemini备用需作为独立设置，不默认产生额外付费调用。模型权重与大量参考音频留在电脑，云端只保留有界音频成品缓存。

下一步应先以独立配置加载Cyrene的GPT/SoVITS权重，选取有准确对应台词的短参考音频做一次真实本地合成，再测试受限连接与端到端微信语音。不得把当前硬件检测当作这两步已通过。

官方接口：[GPT-SoVITS api_v2.py](https://github.com/RVC-Boss/GPT-SoVITS/blob/main/api_v2.py)，提供`/tts`并返回音频；版本标记依据[process_ckpt.py](https://github.com/RVC-Boss/GPT-SoVITS/blob/main/GPT_SoVITS/process_ckpt.py)。
