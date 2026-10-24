# 2026-10-21 无障碍读取崩溃修复

时间均为北京时间，目标为现有 Linux 微信 4.1.1.8；未升级微信。

## 现场证据

- 14:05:30、18:25:34 两次主程序 SIGSEGV，微信相对指令位置均为 `0x4879a90`，访问地址均为 `0x60`。
- 两次现场的首个调用者均为 `0x3809bad`，微信传入的对象指针为零；对应指令为读取对象 `+0x60` 字段。
- 两次栈上均出现 Qt `AtSpiAdaptor` 的 `GetChildAtIndex` 分支。该分支调用微信自定义列表的子项获取函数；函数取不到子项后，在错误处理路径继续解引用空对象。
- 旧脚本按 `get_child_count()` 获取一次数量，再跨多个 D-Bus 请求逐项 `get_child_at_index(i)`。动态列表更新后，下标可能已经失效。
- 以上证据能定位危险调用路径；原始代码闭源，没有确定是哪个具体聊天列表更新事件触发，不能将引用回复认定为唯一原因。
- 两次临近采样均为任务数 283/1024、单个原生读取进程，未发生新增 OOM 或任务上限拒绝。两次间隔不能解释为固定登录时限。
- 排查期间 18:48:11 另一次崩溃位于 `0x8198bfa`，栈同样经过无障碍分发代码，地址与前两次不同。候选实现当时探测了此版未支持的 Properties.GetAll；正式版本已改回独立读取 Name，未保留 GetAll 调用。该次因果仍有不确定性，不能混记为相同下标崩溃。

## 已部署修改

1. `deploy/a11y_safe.py` 替代 `/opt/tools/a11y-dump`：通过单次 GetChildren 得到一批对象引用，随后按引用读取；移除客户端按旧数量逐项索引的路径。
2. 对象消失时跳过该引用，不使用索引重试。公共文件锁串行化各个界面快照。
3. 保持原有 JSON/ARIA 字段供登录、选群、输入框、表情及图片界面适配使用。
4. 以 bind mount 覆盖只读镜像中的单个脚本；原脚本保留备份。`start_native_reader.py` 在下次启动时重新挂载，当前微信无需为部署重启。
5. 服务器每分钟日志增加 `a11y_reader`，记录挂载状态及不含聊天内容的读取统计。
6. 主动引用暂时关闭，继续作为对照；接收引用和合并转发的消息解析保留。

## 实际验收与边界

- 两个针对“子项消失”和“仅请求批量子项”的回归检查通过。
- 手机确认后，真实登录、选中“临时测试”、消息列表和输入框读取正常，微信 PID 未因本次部署更换。
- 用户于 18:55:19 发出“修复后能收到吗”，机器人完成实际 AI 回复并读回，状态 sent，约 7.905 秒。
- 该验收证明当前登录和文字收发正常，不能证明之后不会再崩溃；需继续服务器持续采样。新的表情发送、带图识别尚未在本次修复后重新做专门验收。

服务器证据与备份：`/opt/whalegirl-linux-assets/a11y-race-fix-20261021/`。转储与原始日志仅在服务器保留，不在本文包含聊天、令牌或密钥。

## 2026-10-24 回复核心内存节流与进程自愈

- 现场 `whalegirl-linux.service` 实际常驻约 171 MiB，但 `MemoryHigh` 被单独设成 160 MiB；内核累计记录超过 370 万次高水位事件。进程处于 `mem_cgroup_handle_over_high`，8092 队列积压，管理页与核心健康接口超时。微信容器当时仍登录，重启数为 0，两个 cgroup 的 OOM kill 均为 0。
- 将回复核心限制调整为 `MemoryHigh=384M`、`MemoryMax=512M`、`MemorySwapMax=512M`，使正常工作集不再一直跨过节流线，同时保留硬上限。
- Uvicorn 设置 32 个并发请求上限、128 个连接 backlog、5 秒 keep-alive 和 30 秒优雅退出，给公网扫描与突发请求设置边界。
- `/health` 同时报告后台任务是否异常退出。新增每分钟 watchdog：连续三次探测失败后重启 Python 回复核心，最短 15 分钟才会再次尝试；不会重启微信容器或发起微信登录。
- watchdog 状态文件每分钟记录回复核心内存、交换区、任务数、memory.high 与 OOM 数据。
- 这些保护能降低“核心被内存节流后长期无响应”的概率，不能承诺微信服务端、网络或其他外部故障永不发生。

## 2026-10-24 18:15 群聊网页链接触发 `WeChatAppEx` 内存枯竭与四层自动内存清理机制

### 现场证据与根因
- 18:12:51 群内收到网页链接后，微信 `4.1.1.8` 在容器内自动拉起 11 个 `/opt/wechat/RadiumWMPF/runtime/WeChatAppEx`（内置 Chromium 浏览器/小程序渲染进程），合计占用约 `350~400 MiB` 内存并接入容器内 AT-SPI 无障碍总线（`bus_99`）。
- 叠加宿主机上残留的一个挂起测试进程（占用 `303.5 MiB`），宿主机 `1775 MiB` 物理内存的 `MemAvailable` 跌至 `24 MiB`，触发内核全局直接内存回收（Direct Reclaim），1 分钟平均负载升至 `80.65`，导致 18:15:26 与 18:16:19 两条消息在执行 `/opt/tools/a11y-dump` 时触发 8 秒超时。
- 若仅对 `WeChatAppEx` 执行 `SIGKILL`，微信主进程 `/usr/bin/wechat` 检测到子进程退出会立即重新拉起 6～11 个 Chromium 进程，造成循环重拉风暴。

### 已部署的四层自动内存清理与守护机制（提交 `7875449`）
1. **第一层（`WeChatAppEx` 挂载命名空间休眠桩永久压制）**：`deploy/core_watchdog.py` 每分钟检查容器挂载命名空间 `/proc/<cpid>/mountinfo`，若未挂载则通过 `nsenter -t <cpid> -m mount -n --bind` 将轻量休眠桩（`#!/bin/sh` + `exec sleep 2147483647`）绑定至只读镜像中的 `/opt/wechat/RadiumWMPF/runtime/WeChatAppEx`（原文件完整保留未删除）。微信拉起该路径时仅驻留单个 `400 KB`、0% CPU、不接入 AT-SPI 总线的 `sleep` 进程，且不会触发微信主进程重拉。
2. **第二层（超时挂起辅助/测试进程自动收割）**：`deploy/core_watchdog.py` 每分钟扫描进程表，精准清理运行超 `20s` 的 `/opt/tools/a11y-dump`、超 `90s` 的 `xclip -selection clipboard`、超 `300s` 的残留 `unittest/pytest`、以及任何 `crashpad_handler` 或带 `--type=` 的 `WeChatAppEx` 渲染子进程。
3. **第三层（Python 核心进程内 glibc 堆内存与缓存归还）**：`whalegirl_linux/runtime.py` 新增 `trim_memory()`，每 30 秒空闲周期自动清理过期群成员缓存与超 10 分钟的临时 UI 任务 JSON 文件，执行 SQLite `PRAGMA shrink_memory`、`gc.collect()` 及 `libc.so.6` 的 `malloc_trim(0)` 强制向内核归还空闲堆页；同时将已登录状态下的 `safety_notice()` 全树探测降频至每 5 分钟一次。
4. **第四层（宿主机低内存水位回收与发送前置容错）**：`deploy/core_watchdog.py` 将 `vm.swappiness` 控制在 `20`，当宿主机 `MemAvailable < 320 MiB`（冷却 10 分钟）时自动执行 `sync` 与 `drop_caches=1` 回收干净页缓存；`whalegirl_linux/gateway.py` 将 `status_for_send` 缓存窗口扩至 30 秒，并在前置状态探测偶发超时时回退使用 180 秒内已知的 `logged_in` 状态。

### 修复后实测指标（18:42 北京时间）
- 宿主机 `MemAvailable` 由 `24 MiB` 恢复至 **`597 MiB`**；
- 系统 1 分钟 `load average` 由 `80.65` 回落至 **`0.89`**；
- `whalegirl-linux.service` 常驻内存 `MemoryCurrent` 降至 **`72.7 MiB`**（`MemorySwapCurrent=0`，`memory.pressure=0.00`）；
- `core-watchdog.json` 中 `appex_stub_mounted=true`，`tests/test_core_watchdog.py` 6 项测试全部通过。
