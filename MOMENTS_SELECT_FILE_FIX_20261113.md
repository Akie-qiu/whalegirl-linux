# 最新朋友圈选图故障修复

日期：2026-11-13。状态：已上线，未推送GitHub。

针对任务 `3c349d0dac2b452fb95f12c988ca56f1`：16:37:31创建，16:40:05失败，文案71字、配图1张；原生任务确认 `failed_before_send`。故障是 `Native Moments control did not become ready: image file dialog`，发生在微信选图阶段，本次没有围绕历史429诊断或改动模型接口。

真实不发布探测复现：点击朋友圈Post后，微信4.1.13.26弹出的活动窗口名称是 **Select File**。现有白名单只列Open、Open File及中文名称，导致每次都把已经出现的窗口判为未就绪。

生产变更只有 `deploy/moments_ui.py` 中的一行窗口名白名单：加入Select File；同步至实际运行的 `/data/native-tools/moments_ui.py`。唯一窗口检查、活动窗口判定、隐藏及失效对象过滤、屏幕内定位、文件名字段和Open按钮检查均保留。没有改文案、人设、配图、模型设置、429处理或发送权限。

验证：

- 本地90项、Linux90项相关回归通过，增加4项Select File及歧义窗口回归。
- 使用该任务原有配图做真实原生检查，约7.343秒成功选图并进入带图片的发布编辑器；截图保留在维护目录。
- 未填写文案、未点击发布；原生新增朋友圈数量0。没有把编辑器就绪描述成实际朋友圈送达。
- 实际容器可见修复后的文件哈希；上线本身不需重启服务。诊断期间仅在空闲时短暂停核心，微信客户端和原生reader未重启。
- 微信在线、未暂停，23个后台任务、0失败；设置、角色卡和凭据哈希保持一致。

原失败记录、71字文案及配图均保留，没有修改为“成功”或自动重新发布。之后重试会经过修复后的窗口定位。

备份、原生探测、候选实测、清单和验证结果：

- `/opt/whalegirl-linux-assets/moments-latest-send-20261113`
- `D:/appData/qqbot/maintenance/moments-latest-send-20261113`

仅回滚本次两个镜像源码文件，不回灌数据库或旧配置：

```powershell
ssh root@120.46.186.50 'python3 /opt/whalegirl-linux-assets/moments-latest-send-20261113/install_picker_fix.py rollback'
```

回滚带源码哈希和空闲检查；没有为验收而在生产执行回滚。
