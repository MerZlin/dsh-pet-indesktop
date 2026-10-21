# macOS 多屏、设置 Dock 图标与焦点验证

基线：`c3862d3`；日期：2026-10-21。
构建和安装均获用户明确授权。文档入口：[INDEX.md](INDEX.md)；打包合同：
[ONEDIR_PACKAGING.md](ONEDIR_PACKAGING.md)。

多个桌面切换的后续修复与验收见 [Spaces 可见性报告](PR-REPORT-MACOS-SPACES-2026-10-21.md)。

## 核心行为

macOS 开启允许跨屏且连接多屏时，复用已有每屏一窗路径，避免跨显示器的大窗口
在副屏上实际不可见。关闭跨屏、单屏和其它平台的选择条件保持既有语义。
设置独立进程在 bundle 启动阶段即为 agent，创建 QApplication 后继续设置为
Accessory；主进程按用户的 `show_dock_icon` 选择是否显示 Dock 图标。

用户安装第二版后确认：**“副屏可见，Dock 只有一个”**。
不能用窗口缓冲区截图替代桌面可见性，也不能用 Accessory 策略返回值替代 Dock 验收。

## 修改文件说明

增删以本次修复提交的 `git show --numstat` 为准；新增文件按实际行数计。

| 文件 | 增 / 删 | 改动与原因 |
|---|---:|---|
| `pet/__main__.py` | +4 / −0 | 独立设置入口创建 Qt 应用后设置 Accessory，避免第二个运行图标。 |
| `pet/app.py` | +2 / −31 | 从平台模块导入原有 Dock helper，保留调用接口和主程序的显示偏好。 |
| `pet/platform_mac.py` | +31 / −0 | 搬入原有 helper，使设置入口可复用而不导入完整主程序。 |
| `pet/overlay_shell.py` | +14 / −14 | Mac 双屏走已有多宿主路径；注释先说明通用拓扑职责，再解释缩放与平台条件。 |
| `scripts/build_macos.sh` | +11 / −0 | 正式签名前写入 LSUIElement，防止设置进程启动瞬间登记最近应用。 |
| `tests/conftest.py` | +8 / −0 | 单窗算法族显式选择非 Mac 平台，避免依赖测试机器平台。 |
| `tests/test_overlay_hosts_multiscreen.py` | +37 / −0 | 覆盖 Mac 双屏、关闭跨屏、单屏、负原点屏双向移交及真实 Qt 鼠标事件的往返拖拽收尾。 |
| `tests/test_overlay_cross_screen_switch.py` | +2 / −0 | 显式选择原有单窗合同的测试环境。 |
| `tests/test_overlay_independent_screens.py` | +2 / −0 | 同上，保持逐宠归属和保存语义的原测试覆盖。 |
| `tests/test_overlay_seam_passage.py` | +2 / −0 | 同上，保留单窗接缝轨迹算法测试。 |
| `tests/test_overlay_virtual_desktop.py` | +2 / −0 | 同上，保留并集窗口几何算法测试。 |
| `tests/test_overlay_screen_migration.py` | +1 / −0 | 换家不移动的单窗并集合同显式选择非 Mac，不将旧拓扑强加给 Mac 双屏。 |
| `tests/test_desktop_pet_features.py` | +1 / −1 | Dock 原生 helper 的源码合同跟随搬移位置。 |
| `tests/test_settings_process_isolation.py` | +21 / −0 | 在设置窗构建前断言已设置 Accessory。 |
| `tests/test_macos_bundle_policy.py`（新增） | +19 / −0 | 执行正式构建的 plist 片段，验证 agent 标志、签名顺序及其它字段保留。 |
| `tests/test_harness_launcher.py` | +18 / −14 | 隔离真实可执行文件与全局包目录，分别验证 node 与 npx 回退的完整命令。 |
| `tests/test_windows_node_env.py` | +2 / −4 | Windows 根目录用例显式选择平台，避免在 Mac 上扫描本机 POSIX 全局包。 |
| `tests/test_island_bevel_fade.py` | +2 / −1 | 使用已有图标提供稳定实心取样点，保留余额逐像素比较及原取样门槛。 |
| `docs/ONEDIR_PACKAGING.md` | +5 / −0 | 说明 bundle 与运行时 Dock 策略的职责。 |
| `docs/INDEX.md` | +2 / −0 | 登记本报告和读取条件。 |
| 本报告（新增） | +140 / −0 | 汇集修改、性能、真实运行与验证证据。 |

无删除文件，无新增配置键、第三方依赖或配置迁移。构建生成的 icon.ico 已恢复，
帧序列和构建输出留在既有忽略目录。

## 性能分析

环境：macOS 26.6.2（25G83）、Python 3.12.10、PySide6 6.12.0、PyInstaller 6.22.3。
真实两屏 Q2790PQ / Q2790，均 2560×1440、DPR 1，副屏原点 −2560。
以下原始记录位于本次工作区的 `build/diagnostics/`（不入库）。

| 测量 | 命令、样本 | 结果 |
|---|---|---|
| 拓扑判据 | `PYTHONPATH=. .venv/bin/python build/diagnostics/topology_bench.py`；真实 QScreen，100000 次 × 5 轮 | 基线中位 2.209 µs/次，本次 0.201 µs/次。仅测判据，不代表渲染加速。 |
| 主进程运行 | `.venv/bin/python build/diagnostics/perf_round2.py`；PID 49742，0.5 秒 × 20 | CPU 均值 23.425%、最大 24.5%；RSS 111.03–113.92 MiB；18 线程。 |
| 设置进程运行 | 同命令；PID 49830，0.5 秒 × 20 | CPU 均值 0.19%、最大 1.1%；RSS 33.89–38.91 MiB；4–5 线程。 |

稳态渲染共用同一个 TickDriver，但鼠标穿透轮询并不共用。本机从一张跨屏窗变为
两张屏内窗，多一个 Qt 窗口、宿主控制器及独立的 `QtPerPixelInputController`
周期定时器。每个可见窗口按光标在窗内 / 窗外 / 拖拽中分别以 10 / 50 / 100ms
间隔轮询，每拍调用 `QCursor.pos()` 查询系统光标，必要时更新原生输入穿透标志；
窗口隐藏或关闭时停表。上述间隔是代码配置值，并非实测唤醒频率，不能将共用
动画时钟表述为没有新增周期轮询或系统光标查询。
窗口建销和宿主移交另发生在启动、屏变化及跨屏交互时；本次未新增工作线程、
联网或配置持久化机制。新增窗口和轮询带来持续开销，现有采样不能量化其增量。
设置启动新增一次原生激活策略调用；构建每个变体新增一次 plist 读写，不在运行热路径。
本轮资源采样与全量测试并行，且没有同条件旧版采样，**不能量化 CPU/RSS 增量**，
短时 RSS 范围也不能证明无长期内存增长。判据耗时降低不抵消额外窗口对象的内存成本。

## 实机运行记录

1. 前一轮单窗的副屏身体运行标记为 `x=-333,y=1223,w=156,h=194`，窗口缓冲图中
   有宠物，但用户实际副屏看不到。该证据只定位到跨显示器呈现链，未证明具体 Cocoa
   内部裁剪机理。每屏窗口已是项目现有支持路径，无需另造渲染系统。
2. Dock 的 `recent-apps` 中发现两条相同 `.app` URL，`persistent-apps` 无对应项。
   已备份 recent-apps，仅移除两条目标记录并刷新 Dock；其它记录保持。打开新版设置后
   目标 recent-apps 为 1；主进程 Regular（0），设置进程 Accessory（1）。
   清理是本机一次性维护，产品不会在启动时修改用户 Dock 偏好。
3. 正式构建：`PYTHON_BIN="$PWD/.venv/bin/python" bash scripts/build_macos.sh
   --dist build/macos-fixes --variants webm-chat`。106 段 v6 无损素材门、OCR 模型与
   配置门、TTS、中文编码、bridge 零依赖门均通过，构建退出 0。
4. 原 APP、用户数据和第一版候选均保存在 `build/macos-fixes-backup/`。第二版装到
   `/Applications/dsh-pet-standalone-webm-chat.app`；构建与安装可执行文件 SHA-256
   同为 `3f99a8b1c92743530902a950a53828fbd273ca923a294a8880a1812350c7c88c`，
   `LSUIElement=True`；`codesign --verify --deep --strict` 通过。
5. `swift build/diagnostics/window_state.swift` 读原生窗口：副屏 overlay
   `(-2560,30,2560,1410)`，主屏 overlay `(0,30,2540,1311)`，二者 layer=8、onscreen=1。
   用户现场确认第二版副屏可见，打开设置 Dock 只有一个图标；这是可见行为验收依据。
6. `build/diagnostics/focus_watch` 观察 75.53 秒、151 个样本（0.5 秒采样）：
   前台全部为其它应用，桌宠命中 0 次。覆盖两个 30 秒周期，但不是连续中文输入法测试，
   也无法捕获少于采样间隔的瞬时焦点变化。周期抢焦点的源修复已在基线，本轮重新打包带入。
7. 自动拖拽能力的限制已通过光标探针查明：工具投递副屏窗口点击时，真实系统光标
   仍在主屏 `(1158,1050)`，而目标为副屏约 `(-250,1330)`；Qt 的光标穿透轮询因而
   不接收这组输入。没有把该工具的窗口截图或点击失败当作产品可见性结论。

关闭跨屏、单屏、负原点往返和补窗失败回滚由本地 Qt 回归覆盖；未在用户桌面上
实际拔屏或改缩放，以免扰动当前工作。Windows/Linux 没有本机运行结果。

## 测试与验证

- 注释、报告及测试隔离校正不改变产品运行逻辑；构建、性能和实机结果对应前述
  已安装修复版本，未因注释和测试校正重新构建或安装。
- 新 Mac 双屏用例：修改前 1 failed / 2 passed，修改后通过；打包用例修改前失败、
  写入启动策略后通过。设置入口回归也已先红后绿。
- 聚焦：`QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q` 加以下文件：
  `test_overlay_hosts_multiscreen.py`、`test_overlay_seam_passage.py`、
  `test_overlay_virtual_desktop.py`、`test_overlay_cross_screen_switch.py`、
  `test_overlay_independent_screens.py`、`test_platform_passthrough.py`、
  `test_macos_bundle_policy.py`、`test_settings_process_isolation.py`：**191 passed, 1 skipped**。Mac 负原点往返使用真实 Qt 鼠标事件，验证松手移交与投影清理。
- `.venv/bin/ruff check pet tests scripts`、`bash -n scripts/build_macos.sh`、
  `git diff --check` 通过。
- 全量命令：`QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`，结果为
  **8675 passed, 81 skipped, 21 warnings / 1022.21s**，退出 0；
  日志 `build/diagnostics/premerge-final-full.log`。Mac 修复相关九个完整文件另跑
  **199 passed, 1 skipped / 15.40s**，日志 `build/diagnostics/premerge-focused.log`。
- 整合远端最新 `ba11bdb` 后再次执行同一全量命令：**8752 passed, 81 skipped,
  21 warnings / 1001.34s**，退出 0，日志 `build/diagnostics/postintegrate-full.log`。
  Mac 修复与新增聊天记忆、配置相关十三个文件共 **491 passed, 2 skipped / 31.81s**，
  日志 `build/diagnostics/postintegrate-focused.log`；本次修复补丁在整合前后保持一致。
- 三个既有失败已在未修改 `c3862d3` 的 archive 独立复现（3 failed / 1.19s）：
  harness launcher 的无 DSH 假设被本机全局安装干扰；Windows node root 夹具被
  Mac 全局模块命中；island bevel 像素取样受本机字体渲染影响。
  原始日志 `build/diagnostics/baseline-known-failures.log`。三项在修正前再次复现
  （3 failed / 2.30s），修正测试的环境隔离与稳定取样后，三个完整文件共
  **92 passed, 1 skipped / 2.71s**，日志 `build/diagnostics/premerge-known-green.log`。
- 高负载回归：`QT_QPA_PLATFORM=offscreen .venv/bin/python build/diagnostics/premerge_stress.py`，
  整合后用 10 个 CPU 忙循环子进程复跑多宿主、屏迁移、穿透、设置进程、启动器、
  Node 环境及岛淡化七个文件三遍，每遍 **243 passed, 2 skipped**，耗时
  19.67 / 16.95 / 16.99s。系统 CPU 每秒采样 55 次，均值 99.15%、范围 67.3–100%；
  忙循环仅为测试负载，不进入产品。日志 `build/diagnostics/postintegrate-load-summary.log`。

## 风险与回滚

已确认范围限本机双 1× 屏幕、副屏可见及单 Dock 图标；Retina 混合缩放、
Spaces/全屏切换、热插拔与长时间输入仍需对应环境实测。已有多窗路径复用并不等于
所有这些平台组合均获验证。
回滚可退出 APP 后恢复备份 `.app`，用户配置保留。Dock 历史记录已另存
`build/macos-fixes-backup/dock-recent-apps.plist`，不需恢复重复项才能回滚程序。
