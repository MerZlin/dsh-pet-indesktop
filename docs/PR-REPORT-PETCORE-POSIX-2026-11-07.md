# petcore Linux/macOS 源码适配与候选构建入口

**基线**：`ed58acdac3a22b48c153474adf5a16e3dd638a98`；分支 `feat/petcore-vpet-architecture`。
**日期**：2026-11-07；2026-11-08 追加 Ubuntu VM 首轮证据（第九节）。**状态**：原生验收进行中，
Linux 第六轮候选已构建并完成 GNOME 原生交互、安装与自启，24 组性能样本完成；用户取消 30 分钟内存观察。整仓仍有 7 项既有失败。用户随后授权推送 Linux 里程碑并继续 macOS，后者原生验收尚未完成。当前结果见第十节；前九节保留过程记录。
操作说明、完整历史 issue/PR 对应表与 VM 验收表见 [PETCORE-POSIX](PETCORE-POSIX.md)；
本批日志 L172–L185 见 [日志索引](PETCORE-LOG-INDEX.md)。

## 一、核心特性

增加 Linux xcb 启动诊断、XFixes 轮廓输入、逻辑位置/原生视口分离、macOS 被动窗口
策略、菜单关闭后执行、无托盘恢复入口、截图权限预检，以及用户级 XDG/LaunchAgent 自启。
补专用候选构建入口、共享资源清单、ELF/Mach-O 检查与手动 artifacts 流水线。
保持单进程多宠独立窗口；未引入 overlay/碰撞 IPC，未迁移 Windows 专有探针。
GPU/FPS、系统语音、录音和聊天话筒入口使用平台能力限制；跨平台打开设置或修改音量，
不会清空原有选择、失联麦克风编号或设备名。

## 二、修改文件说明

以下为本任务文件，增删相对开始时 HEAD。原有 `docs/PETCORE-ARCHITECTURE-MAP.html`
及未跟踪 `.md` 完整保留，不计入本批；没有删除文件。新文件按空文件 diff 计数。

<!-- FILE_TABLE_START -->
| 文件 | 增删 | 意图 |
| --- | --- | --- |
| `.github/workflows/petcore-posix-candidate.yml` | 新增 +89/−0 | 手动三端回归及原生检查后上传候选 artifacts，不发布 Release。 |
| `THIRD_PARTY_NOTICES.md` | +11/−0 | 登记新增 Fcitx 插件来源、署名、许可与源码快照，供候选包分发核对。 |
| `docs/INDEX.md` | +6/−0 | 登记平台操作文档与交付报告，建立构建/验收入口。 |
| `docs/ONEDIR_PACKAGING.md` | +3/−0 | 关联独立 petcore POSIX 构建入口，避免与旧版发布混淆。 |
| `docs/PETCORE-LOG-INDEX.md` | +14/−0 | 登记 L172–L185，便于检索平台实现、候选修复、原生验收与提交范围。 |
| `docs/PETCORE-LOG.md` | +243/−0 | 追加 L172–L185 实现、候选构建修复、原生运行、性能及提交范围记录。 |
| `docs/PETCORE-POSIX.md` | 新增 +154/−0 | 平台边界、历史 issue/PR 映射、命令与 VM 验收表。 |
| `docs/PR-REPORT-PETCORE-POSIX-2026-11-07.md` | 新增 +595/−0 | 逐文件说明、实测性能、本机记录和限制，防止误报原生交付。 |
| `packaging/install_petcore_linux.py` | 新增 +41/−0 | 注册/移除用户应用菜单入口，正确引用带空格路径，不删程序或数据。 |
| `packaging/petcore-posix-constraints.txt` | 新增 +4/−0 | 固定 Linux/Mac Qt 与 PyInstaller，避免依赖漂移越过兼容基线。 |
| `petcore/README.md` | +3/−0 | 指向 POSIX 上手命令并标明未完成原生验收。 |
| `petcore/cli.py` | +19/−3 | 接入启动预检、平台/资源诊断和冻结 strict 门，CLI 自启保留根与实例。 |
| `petcore/engine/viewport.py` | 新增 +21/−0 | 纯逻辑原生视口钳制与绘制偏移，不混入 DPR 或 Qt。 |
| `petcore/media/provision.py` | +8/−0 | 拒绝修改冻结 POSIX 随包资源，保留已有帧序列/WebM 与外部角色包供给。 |
| `petcore/paths.py` | +25/−1 | 支持 XDG 配置根，区分 POSIX 只读包资源与外部可写目录。 |
| `petcore/platform/autostart.py` | +115/−0 | 增加用户 XDG/LaunchAgent 原子读写、argv 转义与启动上下文。 |
| `petcore/platform/bundle.py` | 新增 +95/−0 | 共享资源清单、动态模块及 Linux Fcitx ABI/许可检查。 |
| `petcore/platform/desktop.py` | 新增 +107/−0 | 统一平台能力，QApplication 前选 xcb；冻结 Linux 隔离 Qt/GIO 插件，避免跨版本 ABI 混载。 |
| `petcore/platform/input_region.py` | 新增 +231/−0 | 共享绘制轮廓、区域缓存/失败降级，Mac 共享轮询与隐藏停表。 |
| `petcore/platform/macos.py` | 新增 +108/−0 | GUI 线程上的类型化 Objective-C 策略与录屏权限 API。 |
| `petcore/platform/screenshot.py` | +7/−0 | 在抓屏前预检 Mac 录屏权限，拒绝时返回可见原因。 |
| `petcore/platform/window_policy.py` | 新增 +72/−0 | 被动窗口显示/重建避免抢焦点；冻结 Linux 使用随包 Qt 文件选择器避开宿主 GTK 输入法。 |
| `petcore/platform/x11_input.py` | 新增 +104/−0 | 私有 XCB/XFixes 输入区域后端，初始化同步、帧异步、释放资源。 |
| `petcore/ui/app.py` | +34/−15 | 初始化平台/自启上下文，托盘缺失恢复窗，屏幕工作区/DPI 监听，菜单延后执行。 |
| `petcore/ui/bubble.py` | +6/−2 | 气泡使用被动显示和重建策略，Mac 不直接 raise_。 |
| `petcore/ui/chat/quick.py` | +5/−0 | 快速聊天的录音按钮共用相同能力边界。 |
| `petcore/ui/chat/widgets.py` | +5/−0 | 普通聊天的录音按钮共用平台能力，非 Windows 保留原因但不可操作。 |
| `petcore/ui/island.py` | +6/−2 | 灵动岛使用被动显示和窗口策略，保持交互入口正常。 |
| `petcore/ui/menu_actions.py` | +12/−11 | 菜单回调统一分发，自启可用性改为共享平台能力。 |
| `petcore/ui/menu_dispatch.py` | 新增 +61/−0 | Qt/原生菜单会话状态、信号参数兼容及关闭后回调。 |
| `petcore/ui/menu_model.py` | +3/−1 | 注册菜单动作接入会话结束后分发。 |
| `petcore/ui/pet_surface.py` | 新增 +92/−0 | 对运动/碰撞保留逻辑坐标，转换原生窗口与鼠标局部坐标。 |
| `petcore/ui/pet_window.py` | +79/−17 | 接平台输入/视口/策略，公共绘制与 Alpha8 命中、右键去重和恢复。 |
| `petcore/ui/recovery.py` | 新增 +42/−0 | 无托盘时提供任务栏恢复入口；普通关闭转最小化，显式退出接受关闭。 |
| `petcore/ui/screen/selection.py` | +3/−0 | 用户截图入口先请求权限，再抓图和创建遮罩。 |
| `petcore/ui/settings/page_about.py` | +16/−0 | 能力与输入故障进入诊断；长说明独占一行，修复窄窗裁切。 |
| `petcore/ui/settings/page_voice.py` | +35/−6 | 按能力禁用系统语音与录音，保留失联麦克风编号和原名，防止跨平台保存丢配置。 |
| `petcore/ui/settings/pages_core.py` | +25/−8 | 自启/全屏/快捷键共用能力，保留原值，注明整窗穿透恢复方式。 |
| `scripts/bench_petcore_input.py` | 新增 +92/−0 | 可复现的绘制/区域计算与静态缓存微基准，明确排除服务端开销。 |
| `scripts/build_petcore_fcitx.sh` | 新增 +70/−0 | 同版本 Qt 构建 Fcitx，主次版本元数据加精确来源/摘要校验；验证缓存并携带许可/源码。 |
| `scripts/build_petcore_linux.sh` | 新增 +4/−0 | Linux 构建便捷入口，复用统一构建器。 |
| `scripts/build_petcore_macos.sh` | 新增 +4/−0 | Mac 构建便捷入口，复用统一构建器。 |
| `scripts/build_petcore_posix.py` | 新增 +248/−0 | 统一候选构建、源码逐文件摘要与漂移检查、原生审计、Mac 签名及归档。 |
| `tests/test_petcore_animated_image.py` | +11/−4 | 动图回归等待次帧信号，不在固定 300ms 后误把循环回首帧判作停播。 |
| `tests/test_petcore_asr.py` | +16/−11 | 取消测试同时等待 worker 状态和 Qt 排队结果，用真实事件循环避免线程先收尾导致误判。 |
| `tests/test_petcore_chat_ui.py` | +4/−1 | 录音信号回归显式注入 Windows 能力，POSIX 不误要求启用系统麦克风。 |
| `tests/test_petcore_community_bundle.py` | +3/−3 | 资源门检查受版本控制的构建入口，去除本机生成 spec 的依赖。 |
| `tests/test_petcore_desktop_shell.py` | 新增 +147/−0 | 菜单可见/原生会话结束、托盘恢复与窄窗诊断布局。 |
| `tests/test_petcore_dsh.py` | +10/−2 | POSIX 验证真实符号链接；失败回滚同时注入 symlink 和 Windows runner 故障。 |
| `tests/test_petcore_fullscreen_hide.py` | +2/−0 | 接线测试注入操作系统支持探针，使其与 FakeProbe 一致。 |
| `tests/test_petcore_interop_memory.py` | +3/−1 | POSIX 权限错误须立即抛出，Windows 才重试；保留两端断言。 |
| `tests/test_petcore_island_visuals.py` | +2/−1 | 空内容绘制回归不再依赖当前时间的数字字形。 |
| `tests/test_petcore_island_window.py` | +7/−2 | 给定屏幕预算和足够长的内容，跨字体确定触发卡片折叠。 |
| `tests/test_petcore_market_hardening.py` | +1/−1 | 回滚路径按 Path 比较，去除 Windows 分隔符假设。 |
| `tests/test_petcore_menu.py` | +5/−3 | 行高按真实中文字形度量，保留最小高度、上下留白和紧凑菜单约束。 |
| `tests/test_petcore_music_audio_device.py` | +3/−1 | COM 边界使用同一假 AudioUtilities，无需在 POSIX 安装 Windows 依赖。 |
| `tests/test_petcore_music_bounded_diagnostics.py` | +9/−0 | 假音频/窗口读取器配套假可用性，继续验证超时和降级语义。 |
| `tests/test_petcore_music_mod.py` | +1/−0 | Windows startfile 回归注入模块局部平台，避免实际启动测试 exe。 |
| `tests/test_petcore_music_smtc_timeout.py` | +3/−1 | 降级传播回归显式启用被注入的假操作系统来源。 |
| `tests/test_petcore_native_desktop.py` | 新增 +154/−0 | 真实 X 服务端跨进程输入和 Cocoa NSWindow 策略，非原生环境显式跳过。 |
| `tests/test_petcore_platform_win.py` | +7/−0 | 模拟 user32 的测试配套模块局部 Windows 分派，避免改全局 os.name。 |
| `tests/test_petcore_posix_packaging.py` | 新增 +142/−0 | 资源缺失、构建 OS/Qt/架构/glibc 门、导出源码摘要、输出保护与 bundle 可写边界。 |
| `tests/test_petcore_posix_platform.py` | 新增 +175/−0 | 启动环境、缺库原因、能力、XDG/LaunchAgent/转义及纯视口边界。 |
| `tests/test_petcore_posix_settings.py` | 新增 +75/−0 | 实 Qt 验证 GPU/FPS/SAPI/录音入口禁用和跨平台保存值不丢失。 |
| `tests/test_petcore_posix_windows.py` | 新增 +302/−0 | 真实 Qt 下的绘制命中、偏移/DPR/四边、输入恢复与生命周期、右键/权限。 |
| `tests/test_petcore_provision_idle.py` | +71/−0 | 可写 bundle 也不生成帧序列或启动线程；外部角色包对照保留供给。 |
| `tests/test_petcore_settings_voice.py` | +4/−1 | 录音设置保存/取消回归显式使用 Windows 能力。 |
<!-- FILE_TABLE_END -->

## 三、实现与历史依据

- #38/#39、#383：Wayland 会话默认 xcb；Linux 不使用全局光标轮询。XCB 私有连接
  与 Qt 创建窗口做一次同步，随后异步提交输入区域；初始化/隐藏/重建/断连有回归。
- #137、#354：位置/碰撞/存档继续使用逻辑坐标，透明画布可以越过工作区，原生窗口
  留在工作区内，绘制和鼠标事件共享偏移。四边、负坐标、DPR 1/1.5/2 的逻辑回归覆盖。
- #321/#335/#418：普通 NSWindow、被动显示/跨 Spaces 策略，原生 `ignoresMouseEvents`；
  菜单关闭后回调、右键事件去重、录屏权限在抓图前预检。#418 未合并，不能引用为原生验证。
- #71/#432/#22：Qt 版本/构建 OS 硬门、Fcitx 同版本编译、逐个原生二进制和资源门。
  #383 调研时未合并，本次是按机制独立实现，不能继承其验收结论。

逐项来源链接、原生待验证条件见 [历史对应表](PETCORE-POSIX.md#历史缺陷对应)。

## 四、性能分析

**命令**：`.venv/Scripts/python.exe scripts/bench_petcore_input.py --samples 1000 --out .scratch/petcore-posix/input-benchmark-fast.json`。
环境：Windows 11 10.0.26200、Python 3.12.10、Qt 6.12.0、offscreen、DPR=1，
实际 `assets/icon.ico` 作为帧，0–44° 循环旋转，每个尺寸 1000 样本。
运行时另有 pytest 进程，壁钟数据存在主机负载影响；CPU 秒是本进程计数。

| 画布像素 | 初版平均 ms | 优化后平均 / P95 ms | 优化后进程 CPU 秒 / 1000 次 | 采样 RSS 变化 |
| --- | --- | --- | --- | --- |
| 256×256 | 0.911 | 0.512 / 0.814 | 0.828 | +229,376 B |
| 512×512 | 2.900 | 1.079 / 1.727 | 1.578 | +225,280 B |
| 1024×1024 | 10.540 | 3.095 / 4.500 | 4.500 | +217,088 B |

初版使用 RGBA 与 Python run 合并；最终用 Alpha8 与 Qt raster region。
奇数宽行填充、alpha=7/8 边界、透明洞，以及实际 QWidget 抓图与输入掩码在镜像/旋转下
完全一致均有测试；DPR=2 单独进程复跑。该优化**仍不是 Linux 服务端性能结论**。

稳态缓存：10,000 次 `refresh()` 共 **37.125 ms，新增提交 0 次**（counter backend），
约 3.71 µs/次。只缓存最后 revision，不持有历史帧区域；隐藏停止提交，关闭断开连接。

新增路径成本/频率：每次绘制内容变化且可见时生成并提交一次；按角度/镜像/Q 弹/DPR/
视口/按压状态失效。区域在服务端创建、应用后立即销毁；每宠新增一个私有 XCB 连接，
普通更新含 XCB 错误队列检查与 flush，句柄初始化另有一次往返。macOS 一个共享
10/50 ms 光标定时器，仅输入状态变化时写 NSWindow，全部隐藏后停表。
没有新增常驻工作线程、网络请求或逐帧磁盘写；自启仅显式保存/CLI 写用户文件。
截图权限在用户截图时检查；CoreGraphics/Objective-C 库句柄进程内缓存。

上述 RSS 数字是短期分配变化，**不能证明无泄漏**。1/4 宠四场景 3×60 秒 CPU/RSS/
线程/输入提交采样，以及 30 分钟内存观察，按用户决定留待两台 VM 环境部署后执行。

## 五、实机运行记录

本机只有 Windows 原生桌面可用。SSH 配置仅有 `Host github.com`；可见 VMware 后台
服务，无已提供的 Linux/macOS 主机连接。用户回复：「你先做，后面我在 vmware 上装好告诉你」。
因此不运行伪装平台的 native pointer 调用，不把 offscreen 或能力注入当作原生通过。

- `.venv/Scripts/python.exe -m petcore --root .scratch/petcore-posix/check-data --check --strict --out .scratch/petcore-posix/check.json`：退出 0，`strict.problems=[]`。
- `.venv/Scripts/python.exe .scratch/petcore-posix/ui_probe.py`：Windows Qt 平台 `windows`、
  Qt 6.12.0，真实屏幕 TM140VDXP02、逻辑 1280×800、DPR=1.5；创建真实 CoreHost/
  PetManager，桌宠可见、逻辑画布 461×259，关闭后正常退出 0。
- UI 探针在 Windows 注入 Linux 能力矩阵，仅检查禁用说明布局：720/1100×760，浅/深
  四张图。初版窄窗说明 `height=128`、`heightForWidth=256`，抓图确认上下裁切；改为
  独占一行后重新抓图，文字完整。人工查看了浅色 720 与深色 1100 图；恢复窗口也留图。
  这不验证 Linux/macOS 的窗口管理器、中文输入或 Spaces。
- 原始本机文件在 `.scratch/petcore-posix/`：`ui-probe.json`、`ui-*.png`、`recovery.png`、
  `input-benchmark*.json`、`check.json` 和各测试日志；临时数据独立，不接触真实用户配置。
- 新增语音页 Windows 原生探针 `voice_probe.py`，同样四种尺寸/主题，注入 Linux 能力。
  已保存的 SAPI 显示为不支持、音色与试听禁用，在线/HTTP 后端仍可选择；截图
  `voice-*.png`、记录 `voice-probe.json`。这仍只证明本机 Qt 呈现，不是 POSIX 录音测试。

## 六、测试与验证

首次新增平台测试 8 条红、窗口测试 5 条红、菜单/恢复 2 条红、构建 5 条红，随后实现转绿。
后续断连隐藏、macOS 隐藏停表、右键重复菜单、XDG 根、xcb 缺库均先复现再修。
窗口说明裁切来自上面的真实 Windows 数值/截图复现。
补充能力界面 3 项先红后绿；另先复现了保存音量时失联麦克风原名被「当前不可用」
显示后缀污染，再修正显示值与存储值的分离。相关设置/聊天/ASR 组 **145 passed / 25.13 秒**。

本机执行 `ruff check petcore pet tests scripts/bench_petcore_input.py scripts/build_petcore_posix.py packaging/install_petcore_linux.py`
与 `git diff --check`，均通过。源代码 `--check --strict` 退出 0。

`python -m pytest -q tests/test_petcore_*.py tests/test_architecture.py tests/test_pr_report_discipline.py tests/test_ci_workflows.py`
最终累计门禁为 **3613 passed / 3 skipped / 1 failed，269.84 秒**，进程正常退出 1；
唯一失败是下述既有 HTTP 测试。原始日志与 JUnit 在
`.scratch/petcore-posix/final-verification.log` / `final-verification.xml`。
Windows 原生窗口/指针组 **14 passed**；DPR=2 实际绘制/输入一致性 **3 passed**。
更早的较小合跑曾在断言通过后原生收尾挂起，不计为通过门禁，取证与隔离边界见下文。

**高负载三遍**：`.venv/Scripts/python.exe .scratch/petcore-posix/stress_isolated.py`，
16 个独立 CPU worker 通过 Queue 报到后才启动测试，88 个负载样本的平均/最小/最大均为
**100.0%**。两组分别是新增窗口/菜单/恢复/标签回归 34 项，以及托盘/气泡/岛/屏幕 114 项。
每组各三次，六个子进程都返回 0；每轮共 **148 passed**。壁钟秒分别为
新增组 **4.821 / 5.126 / 6.536**，相关组 **12.261 / 17.132 / 14.235**。
日志和完整文件清单在 `stress-isolated.json` / `isolated-stress-*.log`。

保留失败尝试：初次合跑在断言通过后退出挂起；隔离组仍曾在 `QApplication.shutdown()`
原生销毁处超时（120 秒）。标签测试原先构造整套 SettingsDialog，也构造了音频设备和
无关扩展页面；最终改为直接测负责布局的 AboutPage，完整设置窗继续由原生 UI 探针及
既有设置族覆盖。压力 harness 显式结束 QApplication，未跳过断言或强制返回成功。
Cocoa 策略同时收紧为仅真实 Cocoa 安装、弱引用窗口；不把这项修正冒充挂起根因结论。
完整设置窗在压力下的原生销毁问题**仍未被证明修复**，原失败日志留存，不能归为平台
桌面验收已通过；最终三遍结果只覆盖上面明确列出的测试边界。

整仓 `python -m pytest -q` 实际执行至 **98%**，在
`test_webm_first_frame_lock.py::test_cleanup_cancels_inflight_warm_and_discards_result`
原生访问冲突退出（`0xC0000005`），没有完整 pytest 统计，不能称全量通过。
该文件单独运行 **15 passed / 8.45 秒**；测试与 `pet/webm_clip.py` 均等于 HEAD。
这只能证明孤立运行通过，不能据此断定整仓崩溃与本次改动无关；原始堆栈保留在
`.scratch/petcore-posix/full-pytest.log`。

已确认的既有失败：`tests/test_petcore_net.py::test_http_error_is_raised_without_direct_retry`
独立运行仍 `Failed: DID NOT RAISE HTTPError`。其测试与 `petcore/net/http.py` 均与 HEAD
逐字节一致（Git blob 分别为 `62840c62…`、`84a8cf77…`），本批没有修改网络重试规则。
不通过跳过断言或改旧测试伪造全绿。

另外单独复查旧打包组得到 **28 passed / 5 failed，8.66 秒**：
`test_bundle_slim.py` 四项的假包未提供新增 WebView DLL，
`test_packaging_keyring_notices.py` 一项要求 `build_onedir.ps1` 带 UTF-8 BOM。
相关两个测试及 `scripts/slim_bundle.py`、`scripts/build_onedir.ps1` 均与 HEAD 的 Git blob
相同（完整记录 `.scratch/petcore-posix/baseline-blobs.json`），本批未改这些文件。

## 七、已知限制与后续

1. Linux/macOS 包尚未构建；Qt/Fcitx 完整编译、GLIBC/依赖审计、macOS ad-hoc 签名门
   仅实现，未在对应 OS 执行；新 workflow 未推送/运行。
2. GNOME/KDE/Hyprland、原生 Cocoa、菜单首次点击、Spaces、跨应用真实鼠标投递、
   中文输入、实际登录自启、安装/升级，等待 VM。原生测试在 Windows/offscreen 应跳过。
3. VM 未提供的双 Retina/混合物理屏/热插拔单列未验证；不能用逻辑模型测试替代硬件结果。
4. 原生 Wayland、Windows 专有能力移植、正式 Release、公证签名不在本批交付范围。
5. VM 性能证据尚缺。高 DPR/大画布的区域生成仍需实测，不能用本机微基准保证四宠帧率。
6. 本地门禁尚未全绿：HTTP/旧打包断言失败、整仓 WebM 原生崩溃，以及完整设置窗压力
   测试的 Qt 原生收尾挂起均保留记录；不能据此声明可合并或发布。

## 八、风险与回滚

输入/窗口生命周期是主要风险，保留 XFixes 失败时的矩形输入与托盘恢复入口。
Mac Spaces 的普通 NSWindow 方案必须独立实测。没有新增配置键/迁移；用户级自启条目
可用 `autostart off` 移除。源码回滚前关闭候选进程，保留外部数据根。
本轮没有自动提交、推送、合并、发布，也没有更改用户原有的架构图文件。

## 九、2026-11-08 Ubuntu 虚拟机实机运行记录（进行中）

环境：Ubuntu 24.04.5 LTS x86_64 / glibc 2.39 / Python 3.12.3 / PySide6 6.11.3，
GNOME Wayland 会话、Qt xcb/XWayland，VMware 4 vCPU、4 GiB、单屏 Virtual-1/DPR 1。
初始逻辑屏幕 1661×960，工作区 `(0,32,1661,928)`；VMware 调整控制台时可能改变尺寸。
源码是脏工作树快照，HEAD 见报告首段，归档 SHA256：
`f09571f2f34e44f8a1fb8c451942aaed4276bcbe651c8992c0bca328cf33cae5`。
该 SHA 是初始快照，后续恢复窗口补丁及测试修正另行同步，不能冒充已提交的 SHA。

依赖使用隔离 venv + `requirements.txt -c packaging/petcore-posix-constraints.txt`。
通过用户现有桌面 DISPLAY/XAUTHORITY/DBus 运行，未用 offscreen 冒充原生。
原始文件在 `.scratch/petcore-posix/vm-2026-11-08/`，远端工作区
`/home/ubuntu/petcore-acceptance/2026-11-08-1/`；凭据不进入文件或报告。

| 命令/路径 | 实际结果 |
| --- | --- |
| `.venv/bin/python -m petcore --root '测试 数据' --check --strict` | 退出 0，0.786s |
| `QT_QPA_PLATFORM=xcb ... pytest tests/test_petcore_native_desktop.py`（GNOME） | 1 passed/1 Cocoa skipped，2.67s；透明点击下投、实体按下/跨轮廓释放、手动穿透及恢复 |
| 同一 native 测试经 `xvfb-run -a` | 1 passed/1 skipped，1.19s；单列，不算 GNOME 证据 |
| POSIX 适配组与 desktop_shell（offscreen） | 53 passed，1.98s |
| petcore + 架构/文档守卫（offscreen）首轮 | 3579 passed/18 skipped/20 failed，219.98s；失败日志完整保留 |
| 首轮 20 个失败项修正后复核 | 20 passed，19.81s；主要是 Win32/COM 可用性与假读取器不一致、平台路径和字体假设、未跟踪生成 spec |
| Linux 累计 petcore + 架构/文档守卫（offscreen） | 3600 passed/18 skipped，214.11s，退出 0；`petcore-linux-fixed.log/xml` |
| Windows 受影响相关组 | 338 passed，14.07s |
| Windows 累计 petcore + 架构/文档守卫 | 3614 passed/3 skipped/1 既有 HTTP failed，295.17s；正常退出 1 |
| 恢复窗口退出子进程回归 | 修复前退出码 73；修复后 Windows 2 passed/Ubuntu 原生 xcb 1 passed |

完整 `run()` 的首次实际启动没有托盘；桌宠成功出图、设置页正常绘制，但退出挂住。
根因是恢复窗口无条件拒绝 Close，连 QApplication.quit 的关闭请求也被否决。
产品现在仅在 host 的 `_closing` 状态接受关闭；普通关闭仍最小化。后续两次启动时
GNOME 托盘宿主已经可用、均正常退出，首轮失败未删除或归为偶发。
`native-settings.png` 已人工查看：中文平台诊断和按钮完整，无文本裁切。
XTest 经真实右键路径打开菜单，首次点击“桌宠设置”成功；设置窗口实际激活并获得输入
焦点。IBus libpinyin 经真实按键 `zhongwen ` 提交“中文”，Esc 取消关闭；没有直接给
文本框赋值冒充输入法测试。`native-interaction.json` 和截图保存了结果。
同一原生进程检查 10%/72%/300% 三档缩放 × 四边，共 12 项，原生视口均在工作区内，
逻辑坐标及补偿偏移分别记录。此时 VMware 工作区变为 `(66,32,1317,874)`，DPR 仍为 1。

用户级自启 CLI 的 `on/on/status/off/off/status/on` 已实际运行，中文/空格解释器
路径及数据根均保留，文件权限 0600；独立 `--instance` 条目也已读写并移除。
真实注销及重新登录均成功：17:10:50 建立新的 Wayland 桌面会话 158，17:10:52 启动
桌宠（PID 41925），17:10:56 完成启动阶段，桌面实际可见。解释器和数据根仍是上述
中文/空格路径。证据 `native-autostart-login.json`、`autostart-login.png`；之后已通过
CLI 移除本次默认自启条目，确认文件不存在，并用 SIGTERM 清理该隔离测试进程。
没有写系统级自启服务。截图上的 Ubuntu 错误提示对应测试工具
`/usr/bin/ydotool` 的 SIGABRT（无权限执行 help 时），`/var/crash` 元数据已核对；
不能将该提示计作桌宠崩溃。VMware 自动输入无响应后，登录改用 SSH 调用 Linux 本机
虚拟键盘完成，未更改自动登录或认证设置。

性能采样器已用真实单进程 1/4 宠、XTest 拖动和隐藏做短样本冒烟，隐藏期间输入区域
提交次数为 0，拖动样本确实处于 dragging。短样本/中断的试采不能替代计划中的
3×60s 与 30 分钟观察；正式数据待追加。脚本和试采记录保留，避免将其列为正式性能结论。

Ubuntu 24.04 是运行验收目标，不能代替 Ubuntu 22.04 构建基线。macOS 已启动到 eager
登录页，但 SSH 22 端口未开放、VMware Tools 未运行、控制台自动输入无响应，已请求
用户登录并启用远程登录；尚未核验实际架构。双 Retina、混合屏、物理热插拔、KDE/
Hyprland 和候选包均未验证。

本轮恢复窗口补丁后的满载复跑：窗口新组 35 项、相关组 114 项，各三遍，全通过并正常
退出。Linux 4 workers、51 个 CPU 样本均值 99.90%（99.0–100%），六组耗时
3.06/5.33/3.41/6.00/2.75/5.43 秒；Windows 16 workers、81 样本均为 100%，六组
13.39/9.49/4.23/9.04/4.12/9.51 秒。命令及逐轮日志见该目录的
`cpu-stress-20261108/`、`windows-stress/`。不包含已在第六节披露的完整设置窗压力边界。

构建准备中发现导出源码没有 `.git`，旧脚本会在构建末尾读取 HEAD 时失败。新增
`source-files.json` 记录实际代码/资源/构建脚本摘要；`build.json` 将内容 SHA256 与
Git HEAD/dirty 分开，无 Git 时后两者为 null，归档前再次核对内容未变。两个回归先红
后绿：Windows 打包/workflow 71 passed，Ubuntu 打包 8 passed；在累计回归之后新增，
不混入前述统计。该路径只在构建时运行，无桌宠稳态开销，无新常驻线程或网络；新增
文件读取和清单写入。Ubuntu 对 706 文件、119450303 字节扫描三次，分别
0.539/0.171/0.176 秒，证据 `source-hash-probe.json`。

Ubuntu 22.04.5/glibc 2.35 的 rootless Podman 容器已实际启动，基础镜像摘要
`sha256:076e7bf82319619f28dd0aa9c02ab34b055ad37100395b4f49a2ffba04fb5e11`。
构建解释器选 Python 3.12.16 官方源码，按
[Python 发布页](https://www.python.org/downloads/release/python-31216/) 的 SHA256
`b5796915dc45ff745cfb4e990a7e7c93d6f546328a102ce31448d69ebc37575c` 校验后编译。
工具链镜像已经构建成功，ID 为 `c389ba600aba1ce8b8e771a268b1f390a3cdc31ba8eb7f52376d6b575caa3b43`。
这是构建环境准备证据，尚未生成候选包，不替代 Qt/Fcitx 编译和冻结包验收。

源码清单的 Windows 成本补充：Python 3.12.10、与整仓 pytest 并行，10ms RSS 采样，
扫描 6998 文件/209351487 字节三次，耗时 5.212/5.288/5.617 秒。初始 RSS 23052288 B，
峰值 37376000 B，各次释放结果后为 33411072/34414592/35315712 B，不能称内存无增长。
这是短生命周期构建进程的分配保留，不是桌宠的 30 分钟观察；本地含生成素材，文件集
与 Ubuntu 导出快照不同，不能直接比较速度。有 Git 时每次快照额外运行两次 Git 子进程，
无 Git 的导出目录不会启动它们；证据 `source-hash-memory.json`。

L176 补修随包资源只读：仅检查目录写权限会允许用户拥有的 `.app` 在运行时生成
帧序列，破坏签名。现在冻结 POSIX 随包路径不启动供给线程、不写资源；已有帧序列
仍读取，auto 模式缺少帧序列时用 WebM，外部角色包保留供给。回归先 4 failed/2 passed，
修正后供给/打包 16 passed，路径、角色包、渲染、架构及文档相关组 459 passed/9.42s。
这是 OS 边界模拟与真实临时目录检查，尚未执行冻结包的签名及运行前后文件比对。
检查只在启动或供给请求执行，不增加逐帧工作、网络、写盘或常驻线程；实际 VM 成本
仍随候选包验收补充。编码档位与旧 `pet/` 代码未改。

正式性能脚本另准备了 systemd 用户服务 cgroup 版本，统计包括短命解码进程的整棵
进程树 CPU，并将 cgroup 内存（含页缓存）与进程 RSS 分开；聚合状态固定大小，完整
采样流写 JSONL。该版本已完成 GNOME 原生短测，JSON completed=true、进程及 systemd
ExecMainStatus 都为 0，九个场景样本输入无降级；单/四宠拖动样本都持续保持拖动，
两组隐藏输入更新为 0，进程正常收尾。该次与构建依赖下载并行，只验证采样器，不能
当成正式性能数据；证据 `native-performance-cgroup-smoke-20261108T094344.*`。

L176 的 Linux 相关组另得 89 passed/1 skipped（7.25s）。本地只读判断成本探针
`python .scratch/petcore-posix/bench_bundle_readonly.py` 使用 Windows 真实临时目录、
模拟平台边界，3×1000 次：源码分支 0.159–0.197μs/次、Windows 冻结分支
0.109–0.147μs/次，POSIX 的 resolve 分支 Linux 665–676μs、macOS 761–1048μs。
这些是 Windows 文件系统上的边界成本，不能作为原生 POSIX 性能结论；无逐帧调用。

Windows 整仓本次正常退出 1：**11722 passed/23 skipped/8 failed，1801.08s**。
命令 `python -m pytest -q -o faulthandler_timeout=120 --junitxml=.../full-repository.xml`，
日志 `full-repository.log`。本次未发生上轮 WebM 原生崩溃，但一次未复现不等于修复。
采集早于 L176，因此也不能将本次统计冒充最新补丁的最终整仓门禁。
失败为第六节的五项旧打包/一项 HTTP，另有历史文档品牌守卫与 ASR 取消信号测试。
品牌守卫命中的 `PETCORE-V43-V44-PARITY-2026-11-05.md` 等于 HEAD（Git blob
`2346b7bbcfc802026ab0b35d288e6f679b566c92`），本批未改其内容或放松守卫。

ASR 取消测试等 worker 的 recording=false 后立即断言，可能早于 Qt 排队信号送达。
L177 只修测试同步：真实 QEventLoop/QTimer，10 秒预算，同时等待状态和结果信号。
产品 `asr_channel.py` 等于 HEAD（`4b574b573771acf7028021adeee225f77d4d6dbd`）。
原失败保留在整仓日志；修正后 ASR/供给组 22 passed，且各满载三遍都正常退出 0：
Windows 16 workers、23 样本 CPU 100%，4.812/4.904/4.561s；Linux 4 workers、
20 样本平均 99.925%（99.5–100%），4.170/3.158/3.008s。命令是
`stress_asr_provision.py`（PETCORE_TEST_ROOT/PETCORE_STRESS_OUT 指向隔离目录），
逐轮记录在 `asr-stress-windows/` 与 `asr-stress-linux/`；没有跳断言或强制退出成功。

Linux 候选首轮已实际进入 Qt/Fcitx 构建，但 Qt 配置报 OpenGL 检查失败，未生成包。
Qt 源码诊断要求显式 INPUT_opengl=no，单设 FEATURE_opengl=OFF 不够；L177 补齐该
参数。构建容器还经 dpkg-query 确认缺少 Fcitx 必需的 libx11-dev，workflow 补声明，
构建脚本增加依赖前检，在耗时 Qt 编译前报告缺库。新目录继续原生构建；首轮
`candidate-1.log` 和输出目录完整保留，不把编译尝试列为交付成功。

L177 后累计回归：Windows 3622 passed/3 skipped/1 既有 HTTP failed（290.03s）；
Linux 3607 passed/18 skipped/1 动图测试 failed（243.68s）。Linux 与 Qt 编译并行。
动图回归固定等 300ms 后读当前帧，循环动画可能已回到首帧；L178 改为等待实际
frameChanged 次帧事件，10 秒预算，动画产品代码不变。修正后两端相关 31 项通过，
并满载三遍全部正常退出 0：Windows 16 workers/49 样本 CPU 100%，
5.971/19.490/7.344s；Linux 4 workers/54 样本平均 99.931%（98.6–100%），
8.595/14.196/4.614s。Linux 同时有 Qt 编译；压力耗时不能当作稳态性能。命令
`stress_l178.py`，记录在 `l178-stress-windows/`、`l178-stress-linux/`。

最小构建容器的 ldd 又检出 libGL/libpulse 缺失，实际 QtGui import 退出 1。补安装
libgl1/libpulse0 并在 workflow 声明后，QtGui/QtMultimedia import 退出 0，原始
`qtgui-dependencies-{red,install,green}.log` 保留。PipeWire 的可选库警告未隐藏；
这不等同于音频实播验收。修补后的 rootless 工具镜像保存为
`localhost/petcore-builder22:20261108-x11-gl`，ID
`b3a8484779cbf8796dd14fc027f2b87f9026efc52ea81ebbf10fc4d3b9c9e481`。
第二轮使用 E 源码快照（归档 SHA256
`3497d07a0ab0ad04c8dade5f35c61fb016880b5df73bf81747c994447a39de89`），
源码只读挂载，输出另存 candidate-2；GL/Pulse 依赖在这轮容器中补装后才保存新镜像，
不是初始镜像已含这些库。Qt 配置通过并持续编译，候选包仍未生成。

L179：候选第二轮 Qt 1593 项/Fcitx 22 项编译完成，随后发现插件元数据版本为
396032（6.11.0）。已检查实际 v6.11.3 源码 qplugin.h Header 与 qfactoryloader.cpp：
只有主/次版本，补丁号重建为 0。修正构建和冻结诊断的共同校验，完整 Qt 版本仍由
源码标签/提交、qmake 输出和插件摘要清单约束；不会接受缺失来源或被替换的插件。
新增 8 项测试先红，修正后打包/工作流 79 passed（1.03s）；ruff 通过。构建缓存必须
通过来源/干净源码检查并重跑配置和编译。候选第二轮失败证据保留，新输出继续。

外部 AT-SPI 驱动已确认首击设置可以打开，但其搜索框坐标/焦点同步尚需排查；
source6 在搜索焦点处超时并清理测试进程，不能记为完整 UI 验收通过。Qt popup 在
GNOME 的 AT-SPI 树中未暴露，XWayland 的 QScreen 跨进程抓图返回黑图；改用 VMware
整屏截图确认可见菜单与坐标，保留工具限制。先前内嵌原生驱动的中文/12 边缘结果
仍保留，不能代替冻结包外部输入验收。用户现要求优先完成 Linux，Mac 验收暂缓。
VMware 日志确认软盘提示为不存在的主机 A:，设备已自动断开，Ubuntu/SSH 正常。

L180：第三轮原生审计遍历 317 个 ELF，18 条缺库记录归因于最小容器未装
GTK/Pango/Cairo、Wayland cursor/egl、Xrandr、speechd。补 libgtk-3-0/libspeechd2/
libxrandr2/libwayland-cursor0/libwayland-egl1（含传递依赖）后另建第四轮；没有跳过
任何 ELF 或放宽 glibc 上限。工具镜像为 localhost/petcore-builder22:20261108-desktop，
ID b0dde72daa98da9cb15ed632d336f42cc350b6727edf2abdc53a2e8a7e45c8a1。
第三轮冻结目录在 Ubuntu 24.04 实跑 --check --strict 退出 0，Fcitx provenance 与
加载均通过；源码 Qt commit=138f955f0b5e50e4a6c2ee1e78a2279db52ae9e3，
Fcitx commit=46651831dac2520944f573c14214827c0e87d2a6；插件 SHA256
8e83086684051dcb2e6b15145b20cff576abd18bff25a5c6e8f3428b34bf3c84。
Linux 打包回归 16 passed/1.16s；Windows 相关守卫 457 passed/5.86s，后续 workflow/
文档组 448 passed/4.07s，ruff 通过。F 源码快照归档 SHA256
b65ddc78cea0283c44d4ec946d9f26950c16303d844f6559b10e46d5afb4787a，
含 L179 运行码；L180 只追加依赖声明和文档，第四轮使用 F 与新工具镜像。

18:35 用户重启 Ubuntu 后会话变为 2。控制台全黑，GetActive=false、PowerSaveMode=0、
LockedHint=no；GNOME 日志记录 EGL/加速 framebuffer 初始化失败。旧成功启动也有
3D disabled，现有证据不足以确定根因。外部 source7 在无输入区域处超时并清理，
不记为桌面验收或性能结果。黑屏与内核日志另存，待正常重启后复核。

第四轮原生构建成功，345 ELF 全部通过，strict problems=[]，包 412377385 B，
SHA256 574979be8ae25332eaba10649643f6bcfc293b0610fbd2499ae9d3a8f4c857cb；
构建输入 source_sha256=8bdc6fa04e2b0363c91893641fe270b6d50431c484a90c2a03948bba41c57cc1。
实际源码导出没有 .git，build.json 的 sha/dirty 为 null；F 快照清单保留原工作树 HEAD，
不能把该提交当作全部内容身份。包已拷回本机并复核 SHA 一致。

VM 正常关机后仅追加 floppy0.present/startConnected=false，原 VMX 已备份；
重启日志确认无软驱连接错误。授权账户重新登录会话 8 后桌面恢复，无 3D 配置改动。
外部冻结驱动 external-frozen-1 使用真实 XTest/AT-SPI：settings_first_click=true、
活动窗确认、IBus 提交中文、Esc 取消、菜单退出 0、completed=true。截图已检查。
external-frozen-fcitx-2 则加载随包插件，实际候选框与中文提交截图通过，退出 0；
临时 Fcitx 用隔离配置，最后停止，不替换系统 IBus。第一次 --check/-s 合并命令未
切换成功；第二次先观察 keyboard-us，再单独 -s pinyin/-o，状态变为 pinyin 后继续，
纠正过程有记录。第一次云拼音通知未操作，后续显式禁用 cloudpinyin 插件。

L181：冻结 GUI 日志显示宿主 /usr/lib/x86_64-linux-gnu/gvfs/libgvfscommon.so 缺
g_task_set_static_name，因随包旧 GLib 加载新宿主模块。依据
[GIO 官方环境变量说明](https://docs.gtk.org/gio/overview.html)，仅冻结 Linux 设置
包内 GIO_MODULE_DIR 并清除 GIO_EXTRA_MODULES，隔离不兼容插件。保留本地 GIO，
不承诺宿主 GVfs 远程卷。测试先 1 failed，修后平台/打包/工作流 90 passed/1.55s；
ruff 通过，重建后验证日志和本地文件选择入口。

L179 检查成本：Ubuntu24.04/Qt6.11.3，bench_fcitx_check.py 对实际 963248 B 插件
3×100 次校验，每次均值 0.667/0.718/0.650ms，三次 peak RSS 均 34184KiB。仅构建/
--check 触发文件读取，无每帧调用/常驻线程/网络/写盘；这不是整个应用性能数字。

L181 累计 Linux 回归 3617 passed/18 skipped（229.11s，正常退出 0），日志/XML 为
petcore-linux-l181。第五轮包 SHA256 9bef31eee2576d9045c976396eee9e34da8ff0cf9c63eef7e9ad1c970960e39b，
输入摘要 5d521982ad0822c48fdd5c18391a00a6cf9cede92f892d8cf2ba3f610f5ed69e；审计/strict 通过。
install_candidate.py 在「安装 中文路径」实际解包4→5、两次注册/两次移除后重新注册，
包外「升级 保留数据」配置和标记 SHA 均保持不变，两个版本 strict 均退出 0。

L182：第五轮打开 QFileDialog 后，GTK 仍加载宿主 IBus，报 /lib/.../libibus-1.0.so.5
缺 g_task_set_static_name。该轮虽 AT-SPI 显示选择器，但取消按钮未被驱动找到，
强制清理，不能记成完成。冻结 Linux 改用 Qt 内置文件选择器（同一 Qt/Fcitx ABI），
不再进入宿主 GTK 输入法链；源码与其他平台不设置这一属性。3 项分支回归先红，
修后平台/窗口/桌面壳/打包组 65 passed/2.78s，ruff 通过，原生第六轮继续复核。
属性只启动设置一次，无逐帧成本、新线程、网络或写盘。

本轮锁屏解锁后的 VMware 截图为 1×1 黑图（不是有效桌面截图）。授权测试 VM
uinput Shift 唤醒后恢复 1280×800；记录 ubuntu-after-wake.png。之后验收不能只依赖
GetActive=false，必须确认显示已唤醒；黑图不算截图验收成功。

L183（19:17）：第六轮候选已拷回本机并复核摘要，412375065 B，SHA256
`0bf0116acd32d3c449ec11a031645bc893ebf0686edb9e5acfeb6f472a7aab3a`，
构建输入 `8226638f62f7c406e55351549ca2ad4742eac59ffa3f02616e2af6dfe6bfc6d3`。
Ubuntu22.04/glibc2.35/Python3.12.16/Qt6.11.3 构建，原生 ELF 审计与 strict 通过。
源码快照 H 为 b383b25f46887cf6ef569928a581189747fc9c144bd6f886a6ec4869be10f442。
本机产物与 JSON 位于 `.scratch/petcore-posix/vm-2026-11-08/candidate-6/`。

`upgrade_candidate6.py` 在原中文/空格安装路径用第六轮替换第五轮，包外配置和标记
摘要保持不变。`external-installed-3` 外部驱动正常退出 0：首次菜单打开设置并激活，
Qt 文件选择器显示本地中文目录、取消返回，IBus 真正提交“中文”，Esc 取消设置，
右键退出正常结束。VMware 四张截图与 JSON/原始 stderr 保留；未再出现 GVfs/IBus
的 g_task_set_static_name 错误。AT-SPI 仍有辅助功能 warning，不计为无警告运行。
Qt 选择器内置 Look in/Choose/Cancel 等文字为英文，属于当前界面限制。

`frozen_autostart.py` 实跑 status/on/on/status/off/off/status/on 全部退出 0，用户级
desktop 文件 mode=0600，Exec 同时保留“安装 中文路径”和“冻结 自启数据”。随后
GNOME 正常注销并重新登录：session92 于 19:15:20 建立，冻结桌宠 PID26241 于
19:15:22 写启动日志，19:15:24 完成 attach/restore，VMware 截图确认可见。
`frozen_login_verify.py 92` 读取真实进程、窗口和会话后关闭测试自启，确认 entry 不存在，
再通过右键菜单退出。记录 `native-frozen-autostart.json`、`frozen-login-verify.json`
及 `frozen-autostart-visible.png`。这次不是手动启动进程替代登录自启。

安装包运行后以 `verify_bundle_archive.py <candidate6.tar.gz> <installed> <report>`
逐文件复核：1290 文件、866619483 B，changed/extra/missing 全为空，退出 0；
第五轮先前同项 1290 文件、866618797 B 也全空。这些是资源只读与安装证据，
不代替应用 CPU/RSS 长时采样；完整性能仍待独占资源运行。

19:28 补应用入口与托盘：`gio launch ~/.local/share/applications/io.dsh.petcore.desktop`
退出 0，继承隔离的 XDG_CONFIG_HOME，实际窗口可见。第一个驱动把 stdout 接管道，
子应用继承导致 communicate 等 EOF 超时；第二个驱动改日志文件后，psutil 又把
单参数命令按空格切开。已直接读取 /proc/PID/cmdline 和 environ 确认路径与数据根，
在同一运行进程继续验收，不把这两个工具失败记成安装失败或隐去。

GNOME 原生托盘不能用 XTest 点击；测试临时 uinput 绝对鼠标，经真实面板菜单单击
打开设置，取消后隐藏/恢复、手动穿透/恢复，再退出。X 服务端记录：宠物隐藏时
IsUnMapped/0 输入矩形；恢复后 IsViewable/234 矩形；手动穿透时 IsViewable/0，
关闭穿透恢复 234。截图、实际菜单标签和 `launcher-native-final.json` 保存。
菜单退出后 PID 消失；应用由 PID1 接管，未获取 wait 退出码，不能把它写成退出 0。
临时输入设备关闭并核对 /proc/bus/input/devices 已无该设备。

H 源码 Linux 累计回归 **3620 passed/18 skipped，221.45s，正常退出 0**；
当前 GNOME/XWayland 原生跨应用输入 **1 passed/1 Cocoa skipped，1.41s**，与
offscreen 分开记录。Windows 文档/报告守卫 369 passed/3.66s，ruff 和 diff 检查通过；
Windows 整仓最终结果仍待结束。

19:33 整仓结束：`QT_QPA_PLATFORM=offscreen PYTHONUTF8=1 .venv/Scripts/python -m pytest
-q -o faulthandler_timeout=120 --junitxml=.../full-repository-l182.xml`，**11741 passed /
23 skipped / 7 failed，1615.59s，正常退出 1**。失败与前次记录一致（ASR 时序项已消失）：
旧版 bundle_slim 四项缺 Qt6WebView/qtwebview_webview2 假文件，build_onedir.ps1 缺 BOM，
历史 PETCORE-V43-V44-PARITY 文档品牌守卫，以及 Windows HTTP 代理环境回归。相关
测试和旧版实现、petcore/net/http.py 与 HEAD 无 diff；没有跳过失败或修改断言放行。
因此该工作树不能称为整仓全绿或可直接合并，Linux 本轮累计结果仍独立成立。

19:32:53 启动独占资源的正式采样 unit=petcore-perf-full-20261108T113253.service；
先确认 Windows 回归已退出，Ubuntu 无其他 app/test/build/Fcitx，Mac 挂起，显示已唤醒。
实际运行输入摘要 049bc472cc00611d728270fba036939acfe90119a8fb33277a95408009e86d56
（701 文件），同目录 source.json 记录环境、依赖和驱动摘要。运行代码与 H 的 petcore
目录逐文件一致；采样使用源码 run()/WebM，不能冒充冻结程序性能。结果待采完追加。

## 十、Linux 当前交付与性能结果（L184）

**候选**：`dsh-pet-core-linux-x86_64.tar.gz`，412375065 B，SHA256
`0bf0116acd32d3c449ec11a031645bc893ebf0686edb9e5acfeb6f472a7aab3a`。
本机位于 `.scratch/petcore-posix/vm-2026-11-08/candidate-6/`，包含构建、资源、345 ELF
审计和源码文件摘要。用户随后授权提交和推送该 Linux 里程碑；用户架构图修改完整保留，
候选二进制与临时证据留在本机，未合并或发布 Release。

Ubuntu VM 的应用菜单已注册 DSH Pet Core，安装于
`/home/ubuntu/petcore-acceptance/2026-11-08-2/安装 中文路径/dsh-pet-core`。
真实设置首次点击、Qt 文件选择取消、IBus 中文、托盘隐藏恢复/穿透恢复、菜单退出、
源码与冻结包登录自启、升级保留数据、运行后包内 1290 文件不变均通过。Fcitx 原生
候选框/中文验收使用第四轮；第六轮插件摘要相同且加载/来源严格检查通过，不能说
第四轮截图来自第六轮。当前 GNOME 原生跨应用输入 1 passed/1 Cocoa skipped，
Linux 累计 3620 passed/18 skipped；Windows 整仓 11741 passed/23 skipped/7 failed，
失败明细见第九节，**不是全绿状态**。

### 24 组原生应用性能

命令：`bash start_vm_perf.sh full`。环境：Ubuntu24.04.5，GNOME Wayland/xcb，
VMware 4 vCPU/4GiB，Virtual-1 1280×800/DPR1，Python3.12.3/PySide6 6.11.3；
shenshen WebM/scale0.72，内置扩展启用，随机运动/声音关闭。应用运行代码与 H 的
petcore 目录相同；实际输入/依赖/驱动摘要见 source.json。采样期间仅 Ubuntu VM
运行，无构建、pytest 或 CPU 压力任务。只读观察器每分钟读取一次结果。

八种组合各 3 个样本，每个 60.00–61.01 秒；待机先预热 35 秒，其他场景 10 秒。
CPU 一核满载为 100%（四核 VM 总量需除以 4）；应用 CPU 包含 1Hz 采样器。
全树 CPU 使用独立 systemd cgroup 累计量，包含短命 FFmpeg；RSS 是采样值。
应用/解码进程的 RSS 峰值并非同时发生，不可直接相加。全树任务包含所有进程的线程。

<!-- NATIVE_PERF_TABLE_START -->
| 宠数/场景 | 应用 CPU %（三样本） | 全进程树 CPU %（三样本） | 应用/解码进程 RSS 峰值 MiB | 最大应用线程/全树任务 | 区域提交/秒 | 处理 ms/提交 |
| --- | --- | --- | --- | --- | --- | --- |
| 1/idle | 9.61/9.30/9.80 | 15.20/14.14/15.03 | 187.33/133.43 | 20/72 | 11.846 | 1.6292 |
| 1/animation | 16.77/16.64/16.61 | 25.84/25.61/25.77 | 191.75/59.93 | 17/36 | 23.434 | 1.5598 |
| 1/drag | 20.59/20.66/20.82 | 28.95/28.83/29.16 | 214.65/110.02 | 19/56 | 33.956 | 0.1826 |
| 1/hidden | 3.45/3.49/3.43 | 3.45/3.48/3.43 | 205.98/0.00 | 15/16 | 0.000 | 无提交 |
| 4/idle | 33.07/32.40/33.33 | 49.14/48.73/49.96 | 304.90/398.43 | 29/156 | 47.271 | 1.8855 |
| 4/animation | 50.33/50.37/50.61 | 75.44/76.02/76.17 | 336.21/381.57 | 29/156 | 93.565 | 1.8075 |
| 4/drag | 45.20/44.90/45.32 | 66.75/66.08/66.88 | 343.12/332.95 | 27/136 | 81.548 | 1.1645 |
| 4/hidden | 4.08/4.11/4.03 | 4.10/4.10/4.04 | 323.97/0.00 | 15/16 | 0.000 | 无提交 |
<!-- NATIVE_PERF_TABLE_END -->

所有拖动样本均连续捕获（每组 61 或 62 次采样全部 dragging=true）；输入错误为空。
隐藏时窗口始终不可见，输入区域更新/处理耗时为 0，解码进程 RSS 为 0，但应用仍有
约 3.4%–4.1% 单核 CPU 和保留内存，不能称为零开销。短样本中 RSS 有上升和回落，
隐藏阶段首尾稳定；没有改前的同环境对照，不宣称整体性能提升或长期无泄漏。

新增轮廓路径实际提交速率见表：非拖动每宠约 12/23 次每秒，客户端计算和提交平均
约 1.56–1.89ms/次。拖动时使用矩形区域保持事件连续；静止轮廓缓存与隐藏停止更新
仍受回归保护。这个计时不包含异步 X 服务端处理，不等于系统调用耗时。
Linux 输入控制器使用 GUI 线程和本地 XCB 连接，不增加常驻线程、外部网络或磁盘写入；
新增 X 请求/本地 socket 写入随实际更新发生，未另用 strace 统计系统调用总次数。
资源诊断/构建来源校验只在显式 --check/构建触发，实测成本见第九节 L179。

### 用户取消长期观察与证据收尾

24 个短样本完成后进入 memory-idle，用户要求取消 30 分钟观察。20:02:31 通过已核对
PID 的 D-Bus 托盘“退出”正常关闭应用，应用 run() 返回 0，解码进程退出、GNOME
抑制器释放。完整驱动因没有完成第 25 个长样本按原设计退出 1，原始 completed
未写 true，systemd 的 failed 状态如实保存；这表示**按用户要求中止长观察**，不是
产品崩溃，也未把它改写成完整测试成功。保存状态后清理了本次临时 unit。

原始前缀：`.scratch/petcore-posix/vm-2026-11-08/native-performance-cgroup-full-20261108T113253`，
保留 `.json`/`.jsonl`/`.log`/`.source.json`/`.service.txt`/`.cancellation.json`；
`summarize_native_performance.py <json> --short-only` 必须同时验证应用退出 0、24 条
完整记录和用户取消证据才生成 `.summary.md`。部分长观察数据保留在原始 trace，
不拿它替代 30 分钟结果。**长期内存增长未验证**。

仍未验证：Ubuntu22.04 实际桌面（已做其构建/ABI 门）、真实 Xorg 桌面（Xvfb 结果单列）、
KDE、Hyprland、物理热插拔、负坐标/混合 DPR 多屏及 macOS。VM 探针仅给出一个
Virtual-1/DPR1 和 GNOME Wayland 会话，无法提供这些硬件/桌面证据；macOS 按用户要求
暂缓，尚无原生验收或 `.app` 产物。Qt 目录选择器部分文字为英文，宿主 GVfs 远程卷
不在冻结包支持范围，Windows 专有能力仍按平台矩阵明确限制。

收尾验证：文档/报告守卫 369 passed（16.56s），ruff 与 diff 检查通过。L183–L184
只有文档和验收证据更新，产品代码仍是已做累计回归的 L182，因此不重复运行整仓测试。

L185：用户在获知整仓 7 项失败后明确要求推送 Linux 部分，再继续 macOS。提交包含
Linux 所依赖的共用 POSIX 入口、窗口策略与测试，以及其中已有的 macOS 分支；不能
抽走 Cocoa 模块而留下断裂的共用导入。macOS 原生验证和专项修正另行推进，这次提交
不代表 macOS 已验收。原架构图两个文件、`.scratch`、安装包及凭据不进入提交。
