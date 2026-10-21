# macOS 多屏与 Spaces 切换可见性修复

日期：2026-10-21；源码基线：`b3842c0`。本报告补充
[macOS 多屏与 Dock 修复](PR-REPORT-MACOS-DESKTOP-2026-10-21.md)，
文档入口为 [INDEX.md](INDEX.md)。

## 核心行为

macOS 合成窗加入所有 Spaces：每块物理屏幕仍用自己的窗口；用户拖宠跨屏，
或在屏幕上切换普通桌面后，桌宠持续显示。重新显示时重放原生策略，覆盖置顶
开关等原生窗口重建路径。仅修改 Spaces 的两个策略位，保留其余窗口行为。

Qt 6.12.0 默认给工具窗口设置 `MoveToActiveSpace`，并不设置
`CanJoinAllSpaces`，见 [Qt 窗口创建源码](https://github.com/qt/qtbase/blob/v6.12.0/src/plugins/platforms/cocoa/qcocoawindow.mm#L2082)。
本机未显示的真实 Cocoa 合成窗读回值为 `258`，修正后为 `257`。
此前“禁止失活隐藏”与“每屏一窗”均不能代替 Spaces 归属策略。

## 修改文件说明

增删按本轮差异统计；无删除文件，无配置或依赖变更。

| 文件 | 增 / 删 | 改了什么与原因 |
|---|---:|---|
| `pet/platform_mac.py` | +41 / −2 | 增加原生 Spaces 设置，清除 MoveToActiveSpace、设置 CanJoinAllSpaces；限定真实 Cocoa，使用 NSUInteger 签名；失败记录日志。更新模块说明以反映当前职责。 |
| `pet/overlay_window.py` | +2 / −0 | 每次显示都应用 Spaces 策略，使所有屏幕宿主及窗口重建得到同一处理。 |
| `tests/test_macos_spaces.py`（新增） | +99 / −0 | 覆盖策略位保留、平台安全门、双窗口重放，以及真实 Cocoa 窗口置顶切换后的策略读回。 |
| `docs/INDEX.md` | +2 / −1 | 登记本报告并补充既有 Mac 报告的关联读取入口。 |
| `docs/PR-REPORT-MACOS-DESKTOP-2026-10-21.md` | +2 / −0 | 关联本次独立 Spaces 验收，不把既有副屏验收扩大为多桌面已验。 |
| 本报告（新增） | +95 / −0 | 记录源码依据、本地测试、原生探针、性能与用户实际桌面验收。 |

## 性能分析

环境：本机 macOS 26.6.2、Python 3.12.10、PySide6 6.12.0；两个 2560×1440、
DPR=1 的显示器，左屏原点 −2560。测量与全量 pytest 同时运行，不能用作独占资源基准。

原生调用测量命令为 `QT_QPA_PLATFORM=cocoa .venv/bin/python -`，输入脚本创建
不显示的真实 `OverlayWindow`，以 `perf_counter_ns()` 逐次计时
`_mac_set_window_all_spaces(int(window.winId()))`，调用 500 次并校验返回值。
输出：`before=258 after=257`；每次耗时中位数 **57.625 µs**，P95 **93.667 µs**，
最大 **2905.958 µs**。窗口 `isVisible=False`，此测量不包含窗口绘制。

安装版 PID 38178 使用 `.venv/bin/python -` 调用 psutil，
`cpu_percent(interval=0.5)` 连续取样 20 次，每次再读 RSS 和线程数：
CPU 均值 **34.925%**、最大 **47.7%**；RSS **164.656–174.438 MiB**；
线程 **19–26**。无同条件旧版本采样，不能量化 CPU/RSS 增量或证明长期内存无增长。

- 稳态：没有新增 tick、轮询或 Python 帧处理；Spaces 归属由系统窗口管理器维护。
- 新增路径：每个合成窗显示/恢复时，加载 objc 库句柄并读写一次 collectionBehavior；
  触发频率由窗口显示及重建决定，不是周期任务。
- 新增原生调用：每次策略应用取得 NSWindow、读策略、写策略；不激活应用、不改变坐标。
- 没有新增网络、配置读写、工作线程或长期缓存；库查找/加载可能触及文件系统，异常路径会写警告日志。
- 系统为跨 Spaces 窗口维护的合成资源没有单独测量，不能从 Python 对象数推断其成本为零。

## 实机运行记录

1. 用户复现：左屏桌面 3 拖到右屏桌面 1 后宠物消失；重启恢复，切换主屏桌面后再消失。
   只读命令 `swift build/diagnostics/window_state.swift` 显示旧进程仍存在，
   两张合成窗都是 `onscreen=false`。此记录不能单独确定窗口位于哪一个 Space。
2. 真实原生边界检查：
   `QT_QPA_PLATFORM=cocoa .venv/bin/python -m pytest -q tests/test_macos_spaces.py -k native_spaces_policy_on_hidden_overlay`，
   **1 passed, 7 deselected**；不显示窗口，通过实际显示事件入口和置顶标志切换，
   读回 CanJoinAllSpaces 已置位、MoveToActiveSpace 已清除，其余位保留。
3. 正式构建命令：
   `PYTHON_BIN="$PWD/.venv/bin/python" bash scripts/build_macos.sh --dist build/macos-spaces --variants webm-chat`。
   素材、OCR、TTS、编码、bridge 校验通过，退出 0；构建后恢复生成的 icon.ico。
4. 已备份原安装到 `build/macos-spaces-backup/installed-before-spaces.app`，
   通过应用菜单正常退出旧版，在 `/Applications/dsh-pet-standalone-webm-chat.app`
   安装并启动新版。构建与安装可执行文件 SHA-256 一致：
   `003c327ad35cccfc005dbed733cb95368b805195cfccfebfb76dea87725655f2`。
   `codesign --verify --deep --strict` 对构建与安装两份均退出 0。
5. 新进程 PID 38178；同一只读窗口命令显示两张 overlay 为 `onscreen=1`：
   左屏 `(-2560,30,2560,1410)`，右屏 `(0,30,2540,1311)`。
   在屏标志只是系统状态证据，不代替视觉验收。
6. **用户现场重试后确认：“两种操作都正常，桌宠持续可见。”**
   此确认覆盖跨屏拖拽与主屏切换桌面；没有将窗口缓冲截图当作桌面截图证据。

## 测试与验证

- 修正前聚焦测试：**5 failed, 1 skipped**，失败为缺失 Spaces helper/显示入口。
- offscreen 窗口、跨屏、穿透与阴影相关族：**140 passed, 3 skipped**。
- 最终 Spaces、跨屏、迁移、穿透、阴影与激活族：**129 passed, 3 skipped**。
- `.venv/bin/ruff check pet tests scripts`、`git diff --check`：通过。
- 报告纪律与最终 Spaces 检查：**226 passed, 1 skipped**。
- 全量 `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`：
  **8757 passed, 82 skipped, 20 warnings**，耗时 **1029.03 s**，退出 0；
  原始输出在 `build/diagnostics/macos-spaces-full.log`。
- 推送前高负载回归：10 个 CPU 压力进程下，连续三轮运行
  `tests/test_macos_spaces.py tests/test_platform_passthrough.py tests/test_overlay_hosts_multiscreen.py`，
  每轮 **111 passed, 2 skipped**；18 次 CPU 采样均值 **97.1%**、最大 **100%**。

## 风险与未验证项

没有操作显示器热插拔、物理主屏重设、混合缩放、Stage Manager 或全屏应用专属
Space；这些场景没有本轮真实桌面验收。Windows/Linux 仅有安全 no-op 本地检查。
legacy/直播捕获拓扑不在本次修改范围。无数据迁移；回滚可恢复已备份 APP。
