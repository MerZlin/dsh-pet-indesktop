# X11 工作区显示、跨屏气泡与透明重绘

基线：`5120d9e64f21a12b8f37e951e94523c0f2ffeec1`（前置 Codex 功能提交）；分支：`feat/pr245-v440-port`；日期：2026-10-27。
本报告只记录该功能的实现与验证，随对应功能提交交付。

## 核心特性

X11 窗口与气泡请求显示在所有工作区，窗口重建后重新发送；跨屏移动后按合成窗实际全局原点定位气泡，
局部重绘前把脏区写回透明，避免宠物移走后留下旧帧。继续使用主线的每屏合成窗和穿透控制。
跨屏沿用主线设置：必须同时激活 `cross_screen` 模块并将全局 `allow_cross_screen` 设为 `true`（默认关闭）。
两者在进程启动时确定，修改后须完全退出并重新启动。关闭时，宠物留在启动屏幕，拖动不会创建第二屏宿主。
输入策略背景见 [三平台输入处理](PR-REPORT-THREE-PLATFORM-INPUT-2026-10-07.md)，结构化气泡见 [Agent 联动交付](PR-REPORT-CODEX-LINK-2026-10-27.md)。

## 修改文件说明

逐文件增删按 `git diff 5120d9e64f21a12b8f37e951e94523c0f2ffeec1 <本功能提交> --numstat` 统计：

| 文件 | 增/删行 | 修改与原因 |
|---|---:|---|
| `docs/INDEX.md` | +2/−0 | 登记该功能交付报告，提供功能维护入口。 |
| `docs/PR-REPORT-X11-DESKTOP-2026-10-27.md` | +125/−0 | 记录本功能的逐文件说明、性能与真实环境验证。 |
| `pet/app.py` | +3/−0 | 为普通窗口与灵动岛安装 X11 工作区监听。 |
| `pet/overlay_window.py` | +7/−0 | 请求所有工作区显示，重绘前清透明脏区以清除旧帧。 |
| `pet/platform_linux.py` | +105/−0 | 显示或句柄变化后重发所有工作区提示。 |
| `pet/speech_bubble.py` | +2/−0 | 为气泡注册工作区提示，保持与宠物在同一工作区可见。 |
| `pet/sprite_bubble.py` | +3/−1 | 按合成窗实际 mapToGlobal 原点定位气泡，修正跨屏后的锚点。 |
| `tests/test_codex_bubble.py` | +11/−0 | 新增实际移动合成窗后的气泡全局锚点回归。 |
| `tests/test_desktop_pet_features.py` | +2/−1 | 仅允许本功能交付报告使用关联集成名称。 |
| `tests/test_linux_workspaces.py` | +41/−0 | 真实 Qt 显隐事件下重发工作区请求；仅替换 X11 请求边界，非 X11 不注册。 |
| `tests/test_overlay_window.py` | +24/−1 | 通过真实绘制验证移走后的脏区 alpha 为零。 |

## 性能分析

环境：Linux x86_64、Python 3.11.16、PySide6 6.11.2。
命令：`.venv/bin/python .git/local-archives/pr245-2026-10-27/functional-split/paint_benchmark.py`；
使用真实 Qt 绘制入口，240×120 画布、40×40 脏区、300 次渲染，检查旧位置 alpha=0。

```text
dirty rect 40x40, canvas 240x120, n=300, QWidget.render ms median/p95 0.0095 0.0109
old sprite position alpha=0: 300/300
```

透明清除只遍历请求的脏区域；工作区提示仅在显示/原生句柄变化时发送 X11 请求并关闭连接，
不新增周期轮询、磁盘访问、HTTP 请求或后台线程；X11 连接随每次提示关闭。渲染耗时包含 QWidget.render，不能当作整应用帧率。
本轮不新增缓存；未单独测量长期 Qt 原生 RSS。

## 实机运行记录

命令：`QT_QPA_PLATFORM=xcb .venv/bin/python .git/local-archives/pr245-2026-10-27/port-evidence/pr245-native.py`。
真实 X11 双屏探针使用测试精灵，退出恢复光标，输出如下：

```text
background passthrough: true
cross-screen Qt drag: 2300
bubble screen: DP-6
all workspaces: _NET_WM_DESKTOP=4294967295
pet pixels interactive: true
manual passthrough: true
```

拖动通过 Qt 事件送入真实测试窗；工作区属性由 xprop 读回，未切换用户工作区、未向背景应用注入点击。
该探针直接构造合成窗，没有覆盖 AppShell 的模块激活与跨屏开关，不能单独作为跨屏功能验收。

2026-10-27 补测真实 AppShell、实际角色和 X11 双屏 DP-1/DP-6，使用临时配置，保持 `cross_screen` 激活，
分别以关闭/开启 `allow_cross_screen` 的配置启动独立进程。命令：

```bash
QT_QPA_PLATFORM=xcb .venv/bin/python .git/local-archives/pr245-2026-10-27/cross-screen/app_probe.py off
QT_QPA_PLATFORM=xcb .venv/bin/python .git/local-archives/pr245-2026-10-27/cross-screen/app_probe.py on
```

```text
real AppShell gate: False displays: ['DP-1', 'DP-6']
drag requested: DP-6 actual: DP-1 center: (1841, 545)
real AppShell gate: True displays: ['DP-1', 'DP-6']
drag requested: DP-6 actual: DP-6 center: (2953, 545)
```

事件经真实 Qt 窗口拖动入口发送，关闭时留在 DP-1，开启时迁移到 DP-6；退出恢复光标。
日志同时出现 Qt unique connection 与不接受焦点窗口的 requestActivate 提示，未导致探针失败。
用户日常配置原先缺少 `allow_cross_screen`，按主线默认值关闭；已单独将该键设为 `true`，
并确认其余配置值未变、模块将在下次启动时激活。此配置修改不属于仓库提交，仍需用户重启日常进程。
探针和完整输出保留在上述本机归档目录，未随仓库分发。

## 测试与验证

- 2026-10-27 归并前两项功能联合复跑 Agent、Codex、模块门控、气泡、X11、架构与报告检查：**699 passed、5 skipped**（87.55 秒）。
- 跨屏开关、设置与每屏宿主回归：`QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_overlay_cross_screen_switch.py tests/test_settings_cross_screen.py tests/test_overlay_hosts_multiscreen.py`，**87 passed**（4.08 秒，2026-10-27）。
- Ruff、diff 空白检查通过。原生 X11 六项探针与 300 次透明重绘检查通过。
- 范围复核阶段只跑相关回归；推送前完整门禁与高负载复跑的补验结果见下文，不将旧的完整套件数字算作当前门禁。

- 2026-10-28 撤回无关检查和夹具后，相关回归 **829 passed、5 skipped**（143.03 秒）；命令如下，原始输出保留于本机 `.git/local-archives/pr245-2026-10-27/scope-report/related.log`。

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q \
  tests/test_codex_file_watch.py tests/test_codex_bubble.py tests/test_codex_transcript.py \
  tests/test_codex_hook_runtime.py tests/test_codex_install.py tests/test_agent_link.py \
  tests/test_agent_link_threads.py tests/test_agent_settings_hot_reload.py tests/test_g2_event_delivery.py \
  tests/test_agent_link_subtraction.py tests/test_dsh_mid_attach_done.py tests/test_feature_modules_gating.py \
  tests/test_linux_workspaces.py tests/test_overlay_window.py tests/test_architecture.py \
  tests/test_pr_report_discipline.py tests/test_overlay_cross_screen_switch.py \
  tests/test_settings_cross_screen.py tests/test_overlay_hosts_multiscreen.py \
  tests/test_desktop_pet_features.py::test_product_copy_has_no_external_brand_reference
```

2026-10-28 推送前补验（Linux x86_64，Python 3.11.16 / PySide6 6.11.2）：

- `python -m ruff check pet/ tests/` 通过；8 个 Bridge Node 测试文件共44 passed，`verify_import.mjs` 通过。
- WebM 三文件独立进程：41 passed（28.80秒）；warm 独立进程：27 passed（32.71秒）。
- 16个逻辑CPU忙循环负载下，Codex文件通知/气泡、Agent线程与热更新、G2、工作区及模块构造回归，三轮均64 passed（43.37/45.89/47.29秒），负载子进程全部回收。
- 根目录全量主套件在 `tests/test_chat_themes.py:121` 的 `app.processEvents()` 发生 SIGSEGV（-11），隔离该文件及使用全新 Python 缓存仍复现。未据此认定为本次功能造成。
- 为比较源码与本机目录影响，用 `git archive` 提取主线和当前HEAD的 `pet/`、`tests/` 及pytest配置，复用同一虚拟环境和素材。主线聊天主题测试3次均20 passed，当前HEAD快照2次均20 passed；缓存源代码比对无不一致。根目录崩溃原因仍未定位，未修改聊天代码或CI。
- 当前HEAD干净源码主套件（同主线4项 ignore，另加 `--capture=tee-sys`）：3 failed、8975 passed、81 skipped、15 warnings（881.24秒）。两项灵动岛告警色测试在未修改主线的源码快照上也均失败（1.46秒），同样找不到满足预期的折叠布局；不属于本功能的通用测试调整交由维护者。另一项图片路径比较失败由素材软链接引起，该项在根目录复跑1 passed（0.23秒）。这些对照不构成全量门禁通过，当前未取得全绿结果。

复现入口与完整输出保存在本机 `.git/local-archives/pr245-2026-10-28/push-gate/`：
`run.py` 是根目录门禁，`stress.py` 是三轮负载，`clean.py` 是当前提交源码快照主套件；
`main.log`/`chat-themes.log`/`fresh-cache-chat.log` 保存崩溃栈，`main-chat-themes.log` 和 `chat-compare.json` 保存对照结果。

本轮撤回与功能无关的 CI 隔离、Fcitx 构建修正、灵动岛通用测试修改和开发目录整理说明，交由维护者处理。
2026-10-28 撤回 Codex 提交中无关的通用完成提醒检查与 DSH/G2 夹具修改；未生成新的安装包。

## 边界与回滚

Windows/macOS 原生桌面、混合 DPI 与非矩形布局仍未实机验证。源码运行终止检查使用 SIGTERM，未算作托盘正常退出验收。
可撤回对应功能提交；卸载联动插件时保留用户会话文件。主线依赖与构建策略保持不变。
