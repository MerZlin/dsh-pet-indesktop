# petcore Linux / macOS 桌面适配

基线：`feat/petcore-vpet-architecture`，2026-11-07。这是**源码与候选构建入口**说明，
不是完整 Linux/macOS 桌面认证。2026-11-08 已完成 Ubuntu GNOME 候选安装与原生
短样本验收；Intel macOS 源码的真实交互、人工穿透恢复、登录自启及 24 组短样本已验，
arm64 包与未覆盖项见 [专项报告](PR-REPORT-PETCORE-MACOS-2026-11-08.md)。已执行证据见
[交付报告](PR-REPORT-PETCORE-POSIX-2026-11-07.md)，总体架构见
[PETCORE-ARCHITECTURE](PETCORE-ARCHITECTURE.md)，旧版打包说明见
[ONEDIR_PACKAGING](ONEDIR_PACKAGING.md)。

## 支持边界

| 平台 | 本轮目标 | 边界 |
| --- | --- | --- |
| Linux x86_64 | Ubuntu 22.04+；X11 或 Wayland 会话内的 XWayland；GNOME/KDE | 原生 Wayland 无绝对定位支持，启动会说明如何切到 xcb。Hyprland 单列验收。 |
| macOS arm64 | macOS 14.4+，普通无边框 NSWindow、菜单栏图标、`.app` | Qt 6.12 最低 14.4；Intel 保留源码运行，不构建 Intel 包。 |
| Windows | 保留现有桌宠路径 | 全局快捷键、GPU/FPS、系统麦克风、系统媒体识别等专有能力不移植。 |

设置保留跨平台配置值；不支持的控件不可操作，诊断页说明原因。截图权限属于暂时
不可用，入口仍可请求授权。非 Windows 手动穿透是**整窗穿透**，从托盘或恢复窗口关闭；
没有 Windows 的左键转发与右键保留语义。

冻结 POSIX 包的角色资源保持只读，用户拥有写权限也不会触发包内帧序列生成。
随包已有的合法帧序列照常读取；`frameseq=auto` 缺少帧序列时使用 WebM。用户数据目录
中的外部角色包保留原有供给能力。需要随包分发的帧序列应在构建期准备并校验档位。

## 历史缺陷对应

| 历史证据 | 本次处理 | 原生待验 |
| --- | --- | --- |
| [#38 / PR #39](https://github.com/MerZlin/dsh-pet-indesktop/pull/39)：Wayland 拖动与拖影 | QApplication 前在 Wayland 会话默认选 xcb，保留显式 QPA；检查 DISPLAY 与 xcb 动态库并给出安装提示。 | GNOME/KDE 的拖动、残影。 |
| [PR #383](https://github.com/MerZlin/dsh-pet-indesktop/pull/383)：穿透自锁；调研时未合并 | 采用服务端 ShapeInput 思路，独立 XCB 连接；Linux 不轮询全局光标。绘制变换生成 alpha≥8 区域；拖动保留完整输入，句柄重建重放。 | 跨应用投递、真实拖放、依赖可用性。 |
| [PR #137](https://github.com/MerZlin/dsh-pet-indesktop/pull/137)：GNOME 无法贴边 | 行为/碰撞/存档读逻辑画布，原生视口留在工作区，绘制与事件使用同一偏移。 | 四边贴边、探头、缩放、抛掷、跨屏。 |
| [PR #321](https://github.com/MerZlin/dsh-pet-indesktop/pull/321)、[PR #335](https://github.com/MerZlin/dsh-pet-indesktop/pull/335)：抢焦点、Spaces、Dock | 桌宠/气泡/岛共用被动策略；普通 NSWindow、无键盘焦点/阴影、CanJoinAllSpaces/FullScreenAuxiliary；应用 Accessory。 | 前台输入不中断；主动设置/聊天可输入；Dock。 |
| [#354](https://github.com/MerZlin/dsh-pet-indesktop/issues/354)：Retina 错位 | 保留 Qt 逻辑坐标；只在输入区域原生像素边界使用 DPR；新屏幕也监听工作区与 DPI。 | 双 Retina、混合缩放、负坐标、热插拔。 |
| [PR #418](https://github.com/MerZlin/dsh-pet-indesktop/pull/418)：Space 转场；已关闭未合并 | 独立实现普通 NSWindow 路径，未当作已验证上游；截图权限预检在采集/遮罩之前。 | 全屏 Space、转场动画、录屏权限拒绝/授权。 |
| [PR #71](https://github.com/MerZlin/dsh-pet-indesktop/pull/71)：Fcitx ABI | 隔离系统 Qt 插件目录；以完整 Qt 6.11.3 头文件/工具构建 Fcitx，冻结检查版本与动态加载。 | 完整构建、中文输入与候选框。 |
| [#432](https://github.com/MerZlin/dsh-pet-indesktop/issues/432)：glibc | Ubuntu 22.04 构建硬门；每个 ELF 检查 x86_64、glibc≤2.35、DT_RELR、动态依赖。 | 干净 Ubuntu 安装启动。 |
| [PR #22](https://github.com/MerZlin/dsh-pet-indesktop/pull/22)：资源漏包 | 构建与 strict 共用清单；补桥接/音频/许可/动态模块门，沿用角色/图片/菜单/台词检查。 | 冻结目录与安装后的首次启动。 |

旧碰撞 IPC、#434 的 TickGovernor 没有 petcore 对应实现，不移植修复。
历史 PR 的合并状态不能替代 petcore 的原生验收。

## 实现接缝

- `platform/desktop.py` 统一启动环境/能力；`window_policy.py` / `macos.py` 处理
  被动显示与句柄事件。交互窗口仍正常激活，没有全进程 activation swizzle。
- `engine/viewport.py` 是纯逻辑；`ui/pet_surface.py` 暴露逻辑坐标。
  `PetWindow.input_image()` 与 `paintEvent()` 共用 `_paint_content()`，包含缩放、
  镜像、旋转、Q 弹和偏移，Qt 对象/native calls 都在 GUI 线程。
- `input_region.py` 用 Alpha8→位图→Qt raster region，revision 相同不提交；
  `updates` / `processing_ns` / `max_processing_ns` 供原生采样。XFixes 只在创建句柄时
  同步，帧更新异步；隐藏清空，关闭断开私有连接。失败可见，降级矩形输入，保留托盘恢复。
- macOS 只改现有 NSWindow 的 `ignoresMouseEvents`；共享 10/50 ms 光标定时器，
  全部隐藏后停止；动画不靠改 flags 重建窗口。
- 菜单会话结束再执行动作，Cocoa 等 `menuDidClose` 对应的 `aboutToHide` 信号，
  不猜测固定淡出时长；右键按一次按压去重。
  无托盘的 `RecoveryWindow` 提供显示全部、设置、退出，关闭转最小化。
  托盘宿主运行中消失/重启的动态变化仍需桌面验收。

## 运行与自启

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -c packaging/petcore-posix-constraints.txt
.venv/bin/python -m petcore --check --strict
.venv/bin/python -m petcore
# 显式在 Wayland 会话使用 XWayland：
QT_QPA_PLATFORM=xcb .venv/bin/python -m petcore
.venv/bin/python -m petcore --root '/home/me/桌宠 数据' autostart on
.venv/bin/python -m petcore --root '/home/me/桌宠 数据' autostart status
.venv/bin/python -m petcore --root '/home/me/桌宠 数据' autostart off
```

Linux 缺库按诊断安装 `xwayland libxcb-cursor0 libxcb-xfixes0 libxkbcommon-x11-0 libegl1 libgl1 libpulse0`；
无 DISPLAY 先进入图形桌面。源码中文输入也要匹配 PySide Qt，不能注入整棵系统 Qt 插件；
候选构建负责提供 Fcitx 插件。

Linux 数据在 `$XDG_CONFIG_HOME/dsh-pet-core`（默认 `~/.config`），自启在
`$XDG_CONFIG_HOME/autostart/`。macOS 数据在 `~/Library/Application Support/dsh-pet-core`，
自启为 `~/Library/LaunchAgents/io.dsh.petcore[-instance].plist`。
只写用户文件，LaunchAgent 下次登录生效，不装系统服务、不执行 `launchctl bootstrap`。
源码保留解释器/模块/CWD，冻结包直接启动程序，命令保留数据根与实例名。
设置取消不写自启；保存失败恢复原勾选并提示。

`.app/Contents/Resources` 是随包资源，用户导入扩展在数据根 `mods/`。
macOS `--portable` 数据在 `.app` 旁的 `data/`，不写签名目录。
升级替换 `.app` 应保留外部数据；修改包内资源后不能沿用旧签名。

## 候选构建

在目标系统构建，不支持 Windows 交叉构建；输出目录必须为空，不删除旧产物。

```bash
.venv/bin/python -m pip install PyInstaller -c packaging/petcore-posix-constraints.txt
# Linux：先安装 workflow 列出的 Qt/Fcitx 编译与 xcb 运行依赖
PYTHON_BIN="$PWD/.venv/bin/python" bash scripts/build_petcore_linux.sh --output .scratch/linux-candidate-1
# Mac：先核对 sw_vers、uname -m、xcode-select -p
PYTHON_BIN="$PWD/.venv/bin/python" bash scripts/build_petcore_macos.sh --output .scratch/macos-candidate-1
```

Linux 输出 `dsh-pet-core-linux-x86_64.tar.gz`；解压到最终位置后执行
`python3 dsh-pet-core/install.py` 注册用户应用菜单。`--remove` 只移除入口，不删程序/数据。
移动目录后重新注册入口与自启。macOS 输出 `dsh-pet-core-macos-arm64.app.zip`，配置
LSUIElement/最低 14.4/高分屏，ad-hoc 签名并 `codesign --verify --deep --strict`。
测试签名不是 Developer ID 或公证，不能承诺 Gatekeeper 分发通过。
最低版本依据 [Qt 6.12 官方支持表](https://doc.qt.io/qt-6/macos.html)。

输出 `check.json`、`native-audit.json`、`build.json`（系统/Qt/源码及产物 SHA256）和
`source-files.json`（实际构建输入的逐文件摘要）。Git HEAD/dirty 与文件内容摘要分开；
无 `.git` 的源码快照明确记空提交号，构建前后输入有变化则停止归档。
`petcore-posix-candidate.yml` 手动运行三端验证，再生成 Linux/macOS artifacts，
无 Release 写权限，旧发布流程保持不变。Linux 第六轮候选已在 Ubuntu 22.04 容器构建、通过原生依赖审计，并在 Ubuntu 24.04 GNOME 验证安装、中文输入、文件选择与重新登录自启。
冻结 Linux 使用包内 GIO 模块目录，避免旧随包 GLib 误载新系统 GVfs；
本地文件访问保留，宿主 GVfs 远程卷扩展不在本轮支持范围。
冻结 Linux 的文件选择器使用 Qt 自带界面，避免 GTK 继续载入宿主 IBus 模块；部分内置按钮当前显示英文。
元数据只含 Qt 主/次版本，精确补丁版本通过构建来源清单与插件摘要核对。
可用 `--fcitx-build-dir` 指定编译缓存；缓存来源及干净状态必须通过复核。
构建依赖以 workflow 为准，包含 GTK、Xrandr、Wayland、speech-dispatcher 运行库，
避免 PyInstaller 在最小容器中漏收依赖；脚本通过静态检查不等于产物通过。

## VM 验收

记录源码 SHA、Python/Qt、OS/桌面/QPA/DPR、产物 SHA256。Linux 记录
`echo "$XDG_SESSION_TYPE / $XDG_CURRENT_DESKTOP / $DISPLAY"`，Mac 记录 `sw_vers`、`uname -m`。

| 项 | 操作与成功条件 |
| --- | --- |
| 输入 | 透明角落落到其他应用，实体可点击；拖出窗口仍收到释放；手动穿透/隐藏可通过托盘恢复。 |
| 位置 | 四边/探头/10% 与 300% 缩放/镜像/Q 弹/旋转/抛掷/气泡；保存重启位置一致。 |
| 多屏 | 负坐标/混合缩放/双 Retina/跨屏拖动/屏幕移除；VM 不具备的硬件标未验证。 |
| 菜单与输入法 | 托盘/右键首次点击打开设置；中文和候选框正常；取消设置保留原值。 |
| macOS | 被动显示不抢焦点，主动窗口可输入；Spaces/全屏；拒绝录屏权限不先隐藏桌宠、不黑屏。 |
| 安装自启 | 中文/空格路径、重复启停、实际注销登录、替换包保留数据、只有用户级入口。 |
| 性能 | 1/4 宠 × 待机/动画/拖动/隐藏，各 3×60 秒；CPU/RSS/线程/区域更新与耗时；本轮 30 分钟内存观察按用户要求取消。 |

```bash
QT_QPA_PLATFORM=xcb xvfb-run -a .venv/bin/python -m pytest -q tests/test_petcore_native_desktop.py
QT_QPA_PLATFORM=cocoa .venv/bin/python -m pytest -q tests/test_petcore_native_desktop.py
.venv/bin/python scripts/bench_petcore_input.py --samples 1000 --out input-benchmark.json
```

Xvfb 检查真实 X 服务端跨进程投递，不代表桌面合成器验收。最后一条是计算微基准，
counter backend **不含 XFixes 服务端开销**，不能替代 60 秒应用采样/30 分钟观察。
完成原生验收后在交付报告追加原始记录与限制清单。

2026-11-08 首轮已在 Ubuntu 24.04.5 / GNOME Wayland + xcb / Qt 6.11.3 验证跨进程
输入、菜单首次打开、IBus 中文提交及三档缩放四边视口，并修复无托盘恢复窗口拦截应用
退出的问题；中文/空格路径的真实注销重登自启通过，测试条目已移除。
后续冻结候选的 IBus/Fcitx 实际中文输入、中文/空格路径安装升级、Qt 文件选择取消、
重新登录自启、原生托盘隐藏/穿透恢复和包内文件运行后完整性也已通过。
L182 Linux 累计回归 3620 passed/18 skipped，当前 GNOME 原生输入 1 passed/1 Cocoa skipped。
单/四宠四场景各三组 60 秒性能采样已完成；30 分钟内存观察按用户要求取消，长期增长未验证。
Windows 整仓仍有 7 项既有失败；用户随后授权推送 Linux 里程碑并继续 macOS 原生验收。
当前候选、数据和限制见交付报告第十节，过程记录见第九节。
