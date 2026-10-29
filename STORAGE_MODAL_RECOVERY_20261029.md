# 存储提示遮挡与恢复回执修复

## 原因

最近三次明确发送失败均发生在文字提交前，错误为 `Native chat selection was not confirmed by both selected row and header`。两次重试均未提交，界面恢复后仍失败，触发已有保护暂停。

实际界面中有一个微信的 `Insufficient storage space` 模态提示。磁盘清理后提示仍未关闭，拦截了会话列表点击。该提示没有被当前 Qt AT-SPI 树暴露，底下的聊天列表仍可读取，旧恢复代码因此能把被遮挡的界面误判为就绪。

修复原生恢复脚本后还观察到：任务文件已保存 `state=recovered`、`main_ready=true`、`storage_warning_dismissed=true`，上层接口却返回 `No action selected`，把实际完成的恢复判成了失败。

## 改动

`deploy/native_recovery.py`：恢复时补充检查微信所属的 X11 模态窗口。对于实际确认的 4.1.1.8 存储提示，在可用磁盘不少于 1 GiB、尺寸及完整 RGB 像素指纹均匹配时，点击“不再提醒今天”的按钮。未知提示或空间仍不足时保留弹窗并返回原因；恢复结束后再次确认没有模态窗口遮挡。

原生脚本已热更新至 `/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools/native_recovery.py`，更新前源文件保留在服务器 assets 目录中。没有重启微信或消息读取进程。

`whalegirl_linux/gateway.py`：仅当上层错误恰为 `No action selected`，且本次唯一任务文件的会话、操作及成功状态均匹配时，接受已完成的 `reset_ui` 或 `open_chat` 结果。文字、图片、表情提交仍沿用原有发送及读回确认，不把发送异常当成导航成功，也不重发旧消息。

正常回复不新增截图、模型判断或网络调用；视觉指纹检查仅在恢复时遇到微信模态窗口才执行。提示词、工具定义、上下文及缓存构建未更改。

## 验证与发布

- 本地及服务器 34 项定向检查通过，覆盖存储弹窗、低空间、未知弹窗、任务身份、完成状态、非幂等发送隔离、原有自动恢复、独立搜索及缓存布局。
- 实际模态窗口 RGB 指纹匹配，原生恢复回执确认存储提示已关闭。
- 对此前发生错误的真实群聊执行不发送消息的打开验证，会话选中及标题校验通过，耗时约 3.032 秒。
- 恢复后观察到正常业务中 20 条文字、3 个原生表情的确认发送记录，连续失败计数为 0、恢复状态为 `recovered`；未发送额外测试聊天。
- 微信在线、无暂停、56 个原生表情可用；核心健康检查为 17 个后台任务、0 个失败。

发布：`/opt/whalegirl-linux/releases/storage-modal-20261029-221907-742751787`。仅切换核心，微信、读取进程、容器身份与重启计数均保持；现有配置和密钥保留。

服务器元数据：`/opt/whalegirl-linux-assets/storage-modal-deployment.json`、`storage-modal-live-verification.json`。发布入口为 `deploy/stage_storage_modal.py`、`deploy/deploy_storage_modal.py`，原生脚本应与仓库中的对应文件保持一致。
