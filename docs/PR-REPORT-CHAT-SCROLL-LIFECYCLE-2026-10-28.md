# 聊天窗延迟滚动生命周期与窄屏测试边界

基线：`da497e3c7ce8da1cd5c94b858ea47952144e6744`；分支：`feat/pr245-v440-port`；日期：2026-10-28。
本报告记录推送前发现的两版聊天窗行为修复；不含 Qt 运行库、依赖或 CI 策略调整。
生命周期背景见 [Qt 收口记录](QT-LIFECYCLE-FULL-SUITE-STABILIZATION-2026-09.md)，此前门禁失败见 [X11 功能报告](PR-REPORT-X11-DESKTOP-2026-10-27.md)。

## 核心特性

两版聊天窗的延迟贴底改由窗口持有单次 QTimer，仍在80ms后补偿布局；新版还保留由窗口持有的0ms补偿计时器。连续请求重启同一计时器，关窗时立即停止。
后台请求尚未退出、窗口暂未销毁时，也不再收到这批延迟滚动回调。现有请求取消、等待worker退出及窗口销毁语义保持一致。
修复前回归观察到：经典版关闭后滚动条仍从0跳到1947，新版跳到2284；修复后不再发生。原生崩溃曾定位到Qt定时器派发，但不据此宣称已查明Qt库内部缺陷。

另修正灵动岛告警色回归的屏宽搜索：原 `range(1600, 200, -4)` 排除了200px，而本机恰在200px首次同时折叠CPU/MEM。
现在沿原4px步长搜索到正数屏宽，不改产品布局或告警逻辑；该测试前提问题已在未修改的主线上复现。

## 修改文件说明

以下按 `git diff da497e3c7ce8da1cd5c94b858ea47952144e6744 <本功能提交> --numstat` 统计；新增文件为本报告及滚动回归。

| 文件 | 增/删行 | 修改与原因 |
|---|---:|---|
| `docs/INDEX.md` | +3/−1 | 登记修复报告，并在既有Qt收口记录入口关联本次聊天修复。 |
| `docs/PR-REPORT-CHAT-SCROLL-LIFECYCLE-2026-10-28.md` | +76/−0 | 记录逐文件说明、性能、真实X11回归与完整门禁结果。 |
| `pet/chat/legacy_widgets.py` | +10/−4 | 用窗口持有的单次计时器合并延迟贴底；关窗时停止，避免销毁阶段遗留匿名回调。 |
| `pet/chat/widgets.py` | +11/−2 | 将新版0ms/80ms贴底回调改为窗口所有的单次计时器，关窗时一并停止。 |
| `tests/test_chat_scroll_lifecycle.py` | +66/−0 | 通过真实Qt窗口、事件循环和QThread验证关窗后等待网络退出期间不再滚动；仅替换网络provider。 |
| `tests/test_island_alert_colors.py` | +3/−2 | 扩展测试搜索到正数屏宽，覆盖当前字体200px的全折叠阈值。 |

## 性能分析

命令：`QT_QPA_PLATFORM=offscreen .venv/bin/python .git/local-archives/pr245-2026-10-28/fix-gate/benchmark_v2.py`。
环境：Linux x86_64、Python3.11.16、PySide6/shiboken6 6.11.2。基线类从上述基线提交读取到独立模块，与当前类复用同一解释器、真实Qt和临时配置。
各1000次 `_bottom()` 调用计时，再各1000次调用测量未派发请求的Python堆分配，随后在窗口存活期间实际派发回调。

| 实现 | 调用中位数/P95（μs） | 额外1000次请求保留/峰值分配（B） |
|---|---:|---:|
| 经典版修复前 | 6.239 / 8.067 | 536032 / 536614 |
| 经典版修复后 | 1.053 / 1.103 | 32 / 224 |
| 新版修复前 | 6.438 / 10.896 | 512152 / 512633 |
| 新版修复后 | 2.168 / 3.612 | 32 / 224 |

修复后1000次连续请求只保持同一个窗口所有的计时器活动，关窗后 `isActive=False`。每次请求仅重启80ms计时器，
到期一次贴底；该路径由开窗、切会话或消息更新触发，不新增空闲轮询。每个经典聊天窗持有一个、新版持有两个QTimer，随窗口销毁，
替代每请求创建的匿名回调。没有新增网络、磁盘访问或线程；Qt计时器沿用现有事件分发器，不新增外部定时任务。
以上堆数据是待派发突发请求的Python分配，未测Qt原生分配或长期进程RSS，不能据此推断长期内存总量。
灵动岛改动只影响测试，无运行期开销。

## 实机运行记录

命令：`QT_QPA_PLATFORM=xcb .venv/bin/python -m pytest -q tests/test_chat_scroll_lifecycle.py`。
本机真实X11窗口回归输出：`2 passed in 0.90s`。临时配置下分别创建两版聊天窗并显示长回复，网络边界由事件同步provider替代，
真实QThread保留在退出阶段；reject后窗口按原协议等待worker，真实Qt事件循环跨过80ms截止时间，确认滚动条保持0，最后释放请求并销毁窗口。
本次没有人工鼠标交互或托盘退出验收；不把自动化窗口回归当作全部用户流程验收。

## 测试与验证

- 新回归先红：原实现关窗后滚动条值为经典版1947、新版2284；两版修复后通过。最初经典版修复及告警色文件27 passed（2.67秒）。
- 聊天关闭、主题、历史分页、时间线、子系统及告警色相关回归：230 passed、1 skipped（18.81秒）。
- 根目录按上游现有分组跑完整测试：主套件8982 passed、81 skipped、15个Qt弃用警告（856.36秒）；WebM41 passed（25.88秒）；warm27 passed（61.56秒），合计9050 passed、81 skipped。命令见下文；未新增ignore或隔离项。
- 经典版修复后的第一次完整运行仍在共享媒体测试事件循环发生SIGSEGV；随后新版同类关闭回归先红，再完成新版计时器收口。最终上述完整运行未再出现原生崩溃，不据此推断全部平台或Qt内部缺陷已根治。
- 16逻辑CPU忙循环下三轮均112 passed（50.07/49.08/49.12秒），覆盖联动/X11、两版聊天关窗、主题及告警色；全部负载子进程已回收。命令：`.venv/bin/python .git/local-archives/pr245-2026-10-28/fix-gate/stress.py`。
- Ruff、Bridge的44项Node测试及零依赖检查通过；最终报告/产品文案检查236 passed。

完整测试命令（`QT_QPA_PLATFORM=offscreen`，临时XDG目录保留本机字体设置）：

```bash
.venv/bin/python -m pytest -q --capture=tee-sys --ignore=tests/test_webm_reader_lifecycle.py --ignore=tests/test_webm_clip_lifecycle.py --ignore=tests/test_webm_first_frame_lock.py --ignore=tests/test_low_priority_warm_interaction_yield.py
.venv/bin/python -m pytest -q tests/test_webm_reader_lifecycle.py tests/test_webm_clip_lifecycle.py tests/test_webm_first_frame_lock.py
.venv/bin/python -m pytest -q tests/test_low_priority_warm_interaction_yield.py
```

- 未修改CI配置、依赖或Qt运行库，也未生成新的安装包。验证日志与本机脚本保存在 `.git/local-archives/pr245-2026-10-28/fix-gate/`。

## 边界与回滚

可单独撤回本功能提交。Windows/macOS原生桌面、安装包、混合DPI及托盘正常退出仍未实机验收；真实联动客户端完整流程仍未验收。
