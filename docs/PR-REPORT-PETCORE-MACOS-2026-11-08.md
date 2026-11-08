# petcore macOS 适配与原生验收记录

基线：`8e82fb0ccbc0b1b2c4f40436764fc253f4130a20`，分支 `feat/petcore-vpet-architecture`。
Linux 里程碑已按用户要求推送；本报告从其后的 macOS 修改起算。
Linux 候选、真实桌面与 24 组性能见 [POSIX 报告](PR-REPORT-PETCORE-POSIX-2026-11-07.md)。
操作说明见 [PETCORE-POSIX](PETCORE-POSIX.md)。**macOS 原生验收尚未完成。**

当前结论：Intel macOS 15.8.2 / Qt 6.12.0 的 H 源码累计 3639 passed / 19 skipped
（两条命令），strict 与 Cocoa 原生策略门通过。真实 GIF 输入 29 步、系统拼音、
设置/聊天、托盘设置/隐藏恢复/退出、灵动岛及全屏进入/退出已有实测。
Windows 最终整仓 11760 passed / 24 skipped / 7 failed，七项均与基线一致。
“托盘开启再关闭鼠标穿透”自动注入验收未通过，但用户人工操作已确认恢复，观察器
捕获了恢复后的真实按下/释放；真实重新登录自启也已通过（见第二十节）。Windows
高负载布局族在原生退出阶段超时，仍不记为通过。24 个有效短性能样本已齐；arm64
安装包因 VM 为 Intel 尚未交付。30 分钟观察按用户要求取消。
下文保留各轮原始失败和入口排查，早期“待连接/锁屏”只描述对应阶段。

## 一、修改文件说明

<!-- MACOS-FILE-STATS-BEGIN -->

| 文件 | 增删 | 修改与原因 |
| --- | --- | --- |
| `petcore/ui/app.py` | +2/-3 | 托盘穿透提示改为跨平台措辞，避免在 Linux/macOS 上承诺仅左键转发与右键保留。 |
| `petcore/ui/tray.py` | +38/-2 | Cocoa 状态图标打开共用 QMenu 并登记会话，避免动态重建让原生菜单立即关闭。 |
| `tests/test_petcore_tray.py` | +45/-0 | 真实 Qt 菜单回归覆盖左/右激活、首次选择、延后执行与会话清理。 |
| `petcore/media/clip.py` | +4/-1 | 实际 GIF 返回 QPixmap，统一转换为 QImage 以支持透明像素命中，其他媒体路径不变。 |
| `tests/test_petcore_gif_input.py` | 新增 +59/-0 | 新增真实 GIF 解码与 Qt 事件循环回归，验证透明角落、实心主体和输入图像一致。 |
| `petcore/platform/desktop.py` | +8/-3 | 无窗口 macOS 能力查询执行只读权限预检，探测失败也明确报告不可用。 |
| `petcore/platform/macos.py` | +8/-4 | 共享不依赖 QApplication 的录屏权限预检，保持原生窗口句柄安全边界。 |
| `tests/test_petcore_posix_platform.py` | +34/-0 | 覆盖无 Cocoa 应用时的允许/拒绝权限与系统 API 失败，保证诊断不请求授权。 |
| `petcore/community/model.py` | +7/-7 | 保留带时区时间的实际时刻，统一 UTC 比较，兼容历史无时区本地时间。 |
| `petcore/ui/bubble.py` | +4/-1 | 以 QLabel 实际文字排版高度兜底，避免字体回退时气泡文字被压低。 |
| `petcore/ui/settings/page_mods.py` | +2/-0 | 筛选控件使用已有纵排行，降低窄内容区对系统字体宽度的依赖。 |
| `tests/test_petcore_bubble.py` | +3/-1 | 将既有文字完整性回归扩展到四档缩放。 |
| `tests/test_petcore_community_store.py` | +21/-0 | 增加跨时区相同时刻、合并顺序及无时区历史兼容回归。 |
| `tests/test_petcore_stream_mode.py` | +8/-5 | 断言 macOS 使用普通窗口，其余平台继续使用 Tool；保留直播模式往返。 |
| `tests/test_petcore_extensions_geometry.py` | +15/-0 | 原生截图发现复选框裁切后，新增 720px 零横向滚动及主要控件边界回归。 |
| `tests/test_petcore_window_layers.py` | +10/-7 | 观察并执行被动置前公共分派，避免依赖单一平台 raise_ 实现。 |
| `petcore/ui/screen/overlay.py` | +5/-0 | 截图层安装交互窗口策略，激活前同步应用 Spaces 属性，避免跳回应用旧 Space。 |
| `tests/test_petcore_posix_windows.py` | +39/-0 | 新增先红后绿回归，真实 Qt show/句柄重建/排队事件验证策略时机与键盘保留。 |
| `tests/test_petcore_native_desktop.py` | +50/-9 | 原生桌宠改验自动重放，新增 Cocoa 截图层激活前属性及 Esc 取消门。 |
| `docs/PR-REPORT-PETCORE-MACOS-2026-11-08.md` | 新增 +706/-0 | 新增独立 Mac 批次的逐文件、成本、原生连接障碍与验证报告。 |
| `docs/INDEX.md` | +2/-0 | 登记 macOS 专项报告，提供原生验收入口。 |
| `docs/PETCORE-POSIX.md` | +4/-3 | 纠正 Linux 证据已存在后的过时开头，链接 Mac 当前进度。 |
| `docs/PETCORE-LOG.md` | +135/-0 | 追加 L186–L194，记录截图策略、字体、时区、权限、GIF、托盘、性能、真实登录与推送范围。 |
| `docs/PETCORE-LOG-INDEX.md` | +9/-0 | 登记 L186–L194，便于后续按主题查证。 |

<!-- MACOS-FILE-STATS-END -->

## 二、行为与历史问题

- [#321](https://github.com/MerZlin/dsh-pet-indesktop/pull/321) 已合并：被动岛层不能用普通
  `raise_()` 抢前台。petcore 已通过 `raise_passive` 分流，仍需真实前台应用验证。
- [#335](https://github.com/MerZlin/dsh-pet-indesktop/pull/335) 已合并：跨 Spaces 策略和
  Accessory 应用主动打开设置时的激活都要保留，不能全进程屏蔽激活。
- [#354](https://github.com/MerZlin/dsh-pet-indesktop/issues/354) 仍开放：双 2x 屏下旧 overlay
  混用逻辑原点与物理尺寸，导致副屏左半边被判到主屏。petcore 的 ScreenLayout 使用
  Qt 逻辑几何，现有 DPR 不改变位置的回归保留；没有照抄 issue 中“给原点乘 DPR”
  的建议，也不把单屏 VM 当作双 Retina 验证。
- [#418](https://github.com/MerZlin/dsh-pet-indesktop/pull/418) 在 2026-11-08 复查仍为关闭未合并。
  普通 NSWindow、Retina 逻辑坐标与录屏权限不是已获 petcore 原生认证的结论。
  本轮发现截图 `SelectionOverlay` 未接入窗口策略，在 `showEvent` 直接激活；
  这保留了该 PR 描述的跳回旧 Space 风险，**尚未在当前 Mac VM 复现**。

截图层现在安装 `passive=False` 策略，先应用 CanJoinAllSpaces / FullScreenAuxiliary /
hidesOnDeactivate，再置前并激活；句柄重建后重放。它仍可接收键盘，不能套桌宠的
WindowDoesNotAcceptFocus。没有新设置、迁移或全局激活拦截。

新增回归先红后绿：真实 Qt show/隐藏/销毁/重建/事件循环，只替换不可在 Windows
调用的 Cocoa 边界。原生测试也改为通过安装的事件过滤器检查重建，而不是直接调用
apply 后宣称“重放通过”；另加截图层激活前的原生策略、键盘能力和 Esc 取消检查。

## 三、性能分析

命令：`$env:QT_QPA_PLATFORM='windows'; $env:PYTHONPATH=(Get-Location).Path; .venv/Scripts/python.exe .scratch/petcore-posix/mac_policy_cost.py`。
Windows 11 build 26200 / Python 3.12.10 / PySide6 6.12.0，真实 windows QPA；
每组 100000 次 `install(passive=False)` + `apply(passive=False)`，共三组，启用 tracemalloc。

| 样本 | 每对调用 ns | 组内 RSS 增量 B | Python 当前内存增量 B |
| --- | ---: | ---: | ---: |
| 1 | 523.544 | 0 | 120 |
| 2 | 528.216 | 0 | 28 |
| 3 | 532.952 | 0 | 0 |

这是非 Cocoa 提前返回路径的成本，不能代表 macOS 原生调用成本。进程线程实测 6→7，
无法仅凭该样本归因；本次源码没有创建线程、网络请求或磁盘写入。非 Cocoa 两个入口
都立即返回。Cocoa 每个截图窗口持有一个 QObject 事件过滤器，Show/WinIdChange 合并
排队，激活前另同步应用一次；没有新增动画帧或稳态轮询。原生系统调用耗时、RSS 和
稳态 CPU 在该初始阶段尚未采样；后续原生调用成本见第九、十一、十三节。短样本不能证明长期无增长；用户取消的
30 分钟观察不重启。原始记录：`.scratch/petcore-posix/mac-policy-windows-cost.json`。

## 四、实机运行记录

2026-11-08，Linux 推送后正常挂起 Ubuntu、启动 Mac。Windows VMware 控制台显示
eager 登录页，未进入桌面；系统版本与 CPU 架构未实测，不能用 VM 名称代替探针。

| 探针 / 操作 | 真实结果 |
| --- | --- |
| `vmrun getGuestIPAddress .../macOS-Sequoia/macos.vmx` | `The VMware Tools are not running`，退出 1 |
| Python TCP connect_ex `192.168.197.128:22`，3 秒超时 | 10061，主动拒绝连接 |
| VMware “可移动设备”提示框 | 普通点击被工具拒绝；对话框 Raise 后 Return 成功关闭 |
| Mac 控制台点击登录框 / Ctrl+G / 输入 | 截图仍为空登录框，未观察到输入送达；未反复提交密码 |

已请求用户手动登录并为 eager 开启远程登录。凭据只使用会话中已授权的值，不写文件。
这些是无法继续原生自动验收的具体入口证据；offscreen 通过不替代 Cocoa/Spaces/
中文输入/首次菜单/托盘/自启结果。未安装 Tools、未修改 Mac 安全授权或系统服务。

Qt 构建边界复核：[Qt 6.12 官方支持表](https://doc.qt.io/qt-6/macos.html) 为 macOS
14.4+；继续保持 PySide6 6.12.0 / native arm64 候选构建门。若 VM 是 Intel，只做
源码与原生行为验收，不把 x86_64 包标成 arm64。`macos-15` 的 arm64 runner 标签按
[GitHub 官方表](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)核对；
尚未触发远端候选构建，不把 CI 和 VM 桌面验收混为一谈。

## 五、测试与验证

| 检查 | 结果 |
| --- | --- |
| 新回归，修复前 | 1 failed：激活前策略检查 `[False] != [True]` |
| 新回归，修复后 | 1 passed，0.99s |
| 窗口 / 截图 / 动作 / native 文件，offscreen | 90 passed / 3 skipped，4.02s |
| 受影响时序族，16 个 CPU worker 满载三遍 | 每遍 66 passed；4.23 / 3.93 / 4.03s；采样 CPU 均值/最小/最大均 100% |
| ruff | 当前产品与两份测试通过，收尾再检查 |
| 文档 / 架构守卫 | 396 passed，8.31s |
| Windows 整仓 | 11743 passed / 24 skipped / 8 failed，1581.50s，正常退出 1；详见第八节 |

原始日志在 `.scratch/petcore-posix/mac-overlay-{red,green}.*`、`mac-related.*`、
`mac-overlay-stress/`。上轮 Windows 整仓的 7 项旧失败保留在 POSIX 报告，不能预设
本轮全部通过。当前没有 macOS `.app.zip`，没有正式 Release。

## 六、限制与后续

环境和原生策略已补测，具体结果见第七节起；单显示器 VM 不能验证双 Retina、
混合缩放、物理热插拔。真实中文、菜单和全屏进入/退出及截图已通过；独立 Space 的
Ctrl+方向键切换未得到成功证据，真实重登仍待收口，24 个短性能样本见第十八节。新增本批不提交或推送；
回滚应按本报告逐文件清单处理，不影响已推送 Linux 提交与用户架构图。

## 七、远程登录开启后的环境记录

用户确认已开启远程登录后，SSH 成功连接（同一测试 VM、eager 控制台会话）；复用
本机验收专用公钥，私钥未离开宿主，密码未落盘。实测 macOS **15.8.2 / 24H232**，
Darwin 24.6.0、**x86_64**、4 逻辑 CPU、8 GiB 内存、可用磁盘约 61 GiB。
因此这台 VM 按已定边界执行 Intel 源码/Cocoa 验收；arm64 候选构建门不变。

`xcode-select -p` 返回无开发目录，CommandLineTools Python 不存在。测试工具均放在
`/Users/eager/petcore-acceptance/2026-11-08-1`；按 [uv 官方安装说明](https://docs.astral.sh/uv/getting-started/installation/)
下载 0.13.7 的 Darwin x86_64 压缩包，SHA256 与发布校验文件一致：
`20c47e4c1f630b8abaff8c291fa9b9aa300fef92c4fbff95adf198b55287cda7`。
由 uv 创建 Python 3.12.16 venv，不改系统 Python 或 shell PATH。
由于未装 CLT，uv 提示未能改写 libpython install name，可能影响编译扩展；解释器
实际运行与环境探针正常，后续是否影响运行以实测为准。

本轮源码快照 1988 文件 / 150957806 B，压缩包 125450235 B，排除用户两个架构图；
SHA256 `d9625a9de9f5c3b4aa8f9fa42ddd2057cda66af6521c988a88d783196cf45e68`，
上传后远端摘要一致。基线 8e82fb0c，加本批未提交截图策略修改；安装 requirements
与 POSIX constraints（PySide6 6.12.0）进行中，原生测试还未开始。

同批后续：依赖安装完成，Qt/PySide 6.12.0；补入此前导出脚本遗漏的未跟踪 Mac 报告，
形成 B 快照（运行代码与 A 相同），1989 文件 / 150966777 B，压缩 125452955 B，
SHA256 `db7e9e6ccccb54ca36ab695c8cdc905387ad8969245c0d931f1d2da548fe1e72`。
远端解压到 `source-b/petcore-source`，`current` 指向它，摘要核对一致。

原生结构门命令（在 `current`）：

```sh
QT_QPA_PLATFORM=cocoa PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring \
  /Users/eager/petcore-acceptance/2026-11-08-1/.venv/bin/python \
  -m pytest -q tests/test_petcore_native_desktop.py
```

结果 **2 passed / 1 skipped，6.80s，退出 0**；验证 QNSWindow/焦点/重建与截图策略，
跳过 Linux X11 用例。探针确认 Cocoa、1440×900、工作区 (0,25,1440,794)、DPR 1；
Accessory 策略应用成功，录屏、PostEvent、Accessibility 权限均 false。

SSH 的 launchctl manager 为 Background，因此额外用临时 `gui/501` LaunchAgent
运行同一探针（RunAtLoad，退出 0 后 bootout，不放入持久自启目录）。两种启动的
frontmost_bundle 都是 `com.apple.loginwindow`。进一步 ioreg 读到当前 eager 会话
`CGSSessionScreenIsLocked=true`（顶层 IOConsoleLocked=false，不能只看后者）。
VMware 截图仍显示旧的共享设置画面，故不能以该截图推断解锁。已请求用户解锁，
临时 `caffeinate -d -i -u -t 5400` PID 1927 保持唤醒，验收后终止，不改永久偏好。
**这些原生窗口属性通过不等于可见桌面交互通过**。

Mac 时钟也有偏差：宿主 UTC 12:51:13 时 VM UTC 为 10:09:25；记录两端时间，性能
使用单机 monotonic 区间，不混算墙上时钟。未擅自修改时区或时间服务。

## 八、Windows 累计回归补充

完整命令：`QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest -q --junitxml=.scratch/petcore-posix/mac-full-repository.xml`，
日志同名前缀 `.log`。真实结果 8 failed / 11743 passed / 24 skipped，1581.50s，退出 1。
旧的四个 bundle_slim 假目录缺 QtWebView、历史品牌文案、旧构建脚本 BOM、HTTP 环境
依赖七项仍失败，没有修改这些断言。

新增观察到 `test_stream_budget_does_not_trust_content_length` 在本地 HTTP 读流时
抛 `NetworkUnreachable: ConnectionResetError`。该测试及 `petcore/net/market.py`、
`petcore/net/http.py` 相对本批基线无变化；单独运行整个
`tests/test_petcore_market_transport_security.py` 得 **26 passed，13.17s**。
记录为本轮额外的间歇连接重置，未证明其根因或修复；不把整仓结果改写成通过。
跟进原始结果在 `mac-full-network-followup.log/xml`。Mac offscreen 累计正在运行。

## 九、Cocoa 原生成本与源码启动

命令：在 VM 验收目录运行 `QT_QPA_PLATFORM=cocoa PYTHONPATH=current .venv/bin/python
mac_native_cost.py mac-native-policy-cost.json`。macOS 15.8.2 x86_64、Python 3.12.16、
Qt 6.12.0；持有一个真实但隐藏的 NSWindow，预热 100 次，每组 1000 次交互策略 apply。
采样时另有 offscreen 回归和源码应用运行，因此不视为机器空闲基准。

| 样本 | 单次 apply µs | 组内 RSS 增量 B | 线程数前后 |
| --- | ---: | ---: | --- |
| 1 | 46.578 | 57344 | 2 → 2 |
| 2 | 72.988 | 0 | 2 → 2 |
| 3 | 95.513 | 0 | 2 → 2 |

这些是原生策略调用成本，不是全应用 CPU 或拖动性能。窗口 Show/WinIdChange 触发
合并重放，截图层激活前再同步设置；不增加稳态定时器、磁盘、网络或线程。原生调用
经 Objective-C 更新现有 NSWindow 属性；短样本不能推断长期内存增长。

源码 `QT_QPA_PLATFORM=cocoa .venv/bin/python -m petcore --check --strict` 实际退出 0，
`strict.problems=[]`。临时 GUI LaunchAgent 在独立中文/空格数据根启动完整 petcore，
PID 2020，原生枚举见状态图标和桌宠窗口；启动日志 attach 11752ms、restore 13473ms、
features 211ms。首次素材供给 144.2s 完成 converted=1/deferred=105。
用户会话仍锁定，故这里只记录真实进程、窗口和日志，不确认肉眼显示、鼠标或菜单行为。
该 LaunchAgent 放在验收目录，未写持久登录启动目录；收尾必须 bootout。

## 十、首轮 Mac 累计失败与修正

Mac B 快照 offscreen 累计实际 **3614 passed / 19 skipped / 9 failed，392.36s**。
六项失败来自旧测试把 Tool / QWidget.raise_ 当成跨平台契约；现改验 Mac 普通窗口，
并观察仍执行真实平台分派的 `raise_passive`。没有跳过 Cocoa 行为或把窗口策略改回去。

其余三项：

- 气泡使用主字体 lineSpacing，CJK 字体回退后 QLabel 需要 19px 而控件只有 18px。
  新增四档缩放后 Mac offscreen 1.0/1.5/3.0 三项失败，改用实际 heightForWidth 兜底。
  相同四项在修正前的真实 Cocoa 已通过（19.61s）；不混淆两种字体环境。
- 扩展页筛选行保留 180px 文字块加下拉框，使 720×500 横向滚动达 118px，超过既有
  110px 门。使用已有 expand 行把筛选放在说明下方，不修改设置键、筛选语义或保存策略。
- 社区快照解析直接丢弃时区；Mac 本地时区下旧缓存年龄变成 0，相同时刻的不同 offset
  排序也错误。统一为 UTC 实际时刻，无 offset 历史值继续按本地时间解释。
  新回归在 Windows 先红 3 failed/2 passed，覆盖 Z/+08/-07、合并顺序和历史兼容。

Mac 字体/窄窗修正前 focused 为 4 failed/1 passed；修正后上述六族 **89 passed /
1 skipped，16.34s**，Windows 同族 **89 passed / 1 skipped，9.45s**。跳过项是既有
仅 Windows 原生窗口层检查；日志保存，不用重跑结果抹去第一轮失败。完整门禁继续。

本批设置准出适用范围：仅布局使用现有纵排行；无新默认值、配置迁移、依赖、平台
能力或保存/取消变化。配色、图标、主题均未改；暗色新增证据不适用。可访问名称和
焦点仍由原 add_row/下拉框提供；Cocoa 720/1100 布局截图单独留证，真实桌面操作待解锁。

## 十一、Cocoa 布局、自启文件与新增路径成本

实际 Cocoa 控件截图复核发现 L187 虽满足旧的 110px 门，720×500 仍横向溢出 21px，
复选框右侧被裁。新增“零横向滚动 + 五个主要控件均在视口横向边界内”回归，先红
1 failed（29.64s）；功能扩展开关也使用既有纵排行后绿 1 passed（29.58s）。
最终 720×500 视口/内容均宽 226px，1100×760 均宽 621px，横向 maximum 都为 0。
两幅图均人工检查过，设置/关闭/导航/几何相关 Windows **85 passed，45.56s**。
图片为真实 Cocoa 自有 QWidget.grab，用户会话锁定，不能证明前台焦点或跨应用输入。
路径：`.scratch/petcore-posix/mac-2026-11-08d/layout-final/`。

Mac 自启验证用实际 CLI，分别验证显式中文根与独立 instance，各执行
`status/on/on/status/off/off/status`。14 次调用均退出 0，真实 LaunchAgent 为 0600，
`plutil -lint` 均 OK，中文/空格解释器路径保留，WorkingDirectory 是实际源码目录。
显式根保留「自启 中文数据」；instance 按既有规则选择独立默认根，ProgramArguments
保留实例参数。两组 finally 再 off，文件均不存在；没有注销当前会话或留下自启项。
第一次测试驱动错误地期待 instance 同时使用自定义根，断言失败后清理正常；修正的
是探针预期，产品根目录规则没有改变。原始结果 `mac-2026-11-08d/autostart-cli*-v2.json`。
**文件生成与幂等通过，不等于真实重新登录自启通过。**

成本命令：VM 中 `QT_QPA_PLATFORM=cocoa PYTHONPATH=. .venv/bin/python mac_fix_cost.py
fix-cost.json`（从源码目录调用时使用脚本/解释器绝对路径）。同一 Intel Mac/Qt 6.12.0，
每路径预热 100 次、三组各 10000 次，其他回归/系统动态壁纸仍运行：

| 路径 | 三组单次 µs | 三组 RSS 增量 B | 线程前后 |
| --- | --- | --- | --- |
| QLabel.heightForWidth | 11.396 / 10.692 / 15.828 | 45056 / 0 / 0 | 每组 6 → 6 |
| 带 offset 的缓存年龄 | 1.763 / 2.286 / 2.447 | 12288 / 0 / 0 | 每组 6 → 6 |

高度计算只在正文显示/翻页/缩放重排时发生，不进入动画帧；时间换算用于既有缓存年龄
与新旧数据比较，不增加轮询。新逻辑没有显式新增磁盘/网络访问或线程；QLabel 调用
Qt 字体排版，历史 naive 时间归一化需要本地时区信息。未做系统调用追踪，不声称
底层 syscall 次数为零。样本只说明短调用成本与组内 RSS，不证明长期无增长。
30 分钟内存观察仍取消，完整 1/4 宠可见桌面短样本未执行。

未授权录屏的实际 Cocoa 探针调用 `grab_screen_image(window=pet)`，返回
`ScreenshotError.reason=permission_denied`，自有 PetWindow 的 isVisible 前后均 true。
没有请求授权、抓取桌面或返回黑图；这是平台拒绝分支证据，菜单入口和授权后恢复
仍待可见会话验收。临时 app1 LaunchAgent 已 bootout，PID 2020 消失；这是 launchd
停止测试进程，不记作用户菜单正常退出。临时保持唤醒在最终收尾时清理。

L187 C 源码累计过程中加入的 L188 新几何断言仍对旧功能扩展行执行，结果
3631 passed / 19 skipped / 1 failed（379.26s），唯一失败是同一行溢出（offscreen 38px，
前述 Cocoa 为 21px）。
该诊断运行保留原始结果；最终门改从包含 L188 产品与测试的独立 D 快照执行，
运行期间不再修改其源码，避免把不同批次的测试/产品结果混报。

## 十二、最终 D 源码门禁与可见会话恢复

D 快照 1989 文件 / 150982804 B，压缩包 125459561 B，SHA256
`4789f1a3f972f1d8c3df687939f45c0e2a49b2c0b2f170e9bf87d1030967e373`。
Mac 实际运行目录 `source-d/petcore-source`；测试完成后逐一核验导出清单，1989 文件
changed=[]。本地原始记录在 `.scratch/petcore-posix/mac-2026-11-08d/`。

- D 累计 offscreen：**3632 passed / 19 skipped，234.19s，退出 0**。
- Mac 4 个 CPU worker 满载三遍：每遍 **117 passed / 1 skipped**，pytest
  34.99 / 20.42 / 15.80s；进程墙时 36.553 / 21.498 / 16.529s。
  CPU 149 样本，均值 99.329%，最小 0%，最大 100%；原始最小值保留。
- D 源码 `--check --strict` 退出 0；源文件复核、strict JSON、压力日志随上述目录留证。
- Windows 最终文档/架构/流水线守卫 **459 passed，10.35s**；ruff、diff check 通过。
  L187 Windows 整仓仍在运行，结果另补；L188 的单行布局后续由 85 项相关测试覆盖，
  不改接口、配置、生命周期或平台分派。L189 新增平台诊断路径后，安排最终累计门。

用户随后手动解锁，CGSession 探针不再含 ScreenIsLocked=true；原生会话已恢复。
但录屏 / PostEvent / AX 权限仍 false。通过 Apple 正常请求 API 和临时 GUI helper
引导授权，不修改 TCC 数据库。辅助窗口 PID 2918，原生前台 PID 与其一致，窗口
isOnscreen=true；需用户完成权限确认后继续跨进程鼠标/中文/菜单/Spaces 验收。

## 十三、无窗口权限诊断与实际成本

D 的 `--check` 在没有 QApplication 时误报录屏可用，而同一机器的真实 TCC 预检为
false。根因是诊断借用了只适合原生 QWidget 的 `is_cocoa()` 判断。L189 新增独立的
只读 CoreGraphics 预检：CLI 不创建 GUI 也查询权限，失败时报告暂不可用；不弹授权。
offscreen/minimal 仍保留虚拟后端行为，不尝试操作 NSView。

五项新增回归先红 **5 failed，1.09s**，修复后平台/窗口/设置/截图相关 **73 passed，
3.80s**，ruff 通过。真实 Mac E 源码 `QT_QPA_PLATFORM=cocoa python -m petcore
--check --strict --root <独立验收根>` 退出 0，`strict.problems=[]`，录屏能力为
`supported=true / available=false`，说明需在系统设置授权，与原生探针一致。

E 快照 1989 文件 / 150988205 B，压缩包 125462129 B，SHA256
`f9ddd610c4ea513b2b718eb4608bb174a2f698449caf35c228e7f37ad2c3f962`。
原始日志保存在 `.scratch/petcore-posix/mac-2026-11-08e/`。

成本命令：在 E 源码目录以 `PYTHONPATH=.` 运行验收环境的 `mac_diagnostic_cost.py`。
同一 Intel VM，无 QCoreApplication；冷调用 22.448ms，RSS 增量 966656 B，线程 4→4。
预热 20 次后，三组各 1000 次完整能力查询分别 **8.783 / 8.064 / 8.104ms**，RSS 增量
40960 / 24576 / 0 B，线程 4→3、3→3、3→3。测量包含其他能力对象构建及系统 TCC
预检；系统服务调用并非纯内存查询，没有做 syscall/IPC 追踪。没有新增定时器、网络、
文件写入或工作线程；调用发生在诊断、启动和相关界面构建，未接入动画帧。GUI 原本
就查询权限，本次新增的是无 QApplication 时的同等查询。每次读取当前权限，避免把
授权前的结果永久缓存。短样本不能推断长期 RSS，30 分钟观察仍按用户要求取消。

E 累计 offscreen **3637 passed / 19 skipped，234.90s**，JUnit 无失败；运行后 1989
源码文件逐一复核 changed=[]。SSH 外层驱动在 pytest 完成后给 zsh 只读变量 `status`
赋值失败而退出 1；未把该工具错误归为产品失败，也没有重新运行来掩去记录。
后续命令改用独立变量名。源码不在测试期间变更。

Windows L187 整仓实际正常结束：**11752 passed / 24 skipped / 7 failed，1741.26s**。
失败仍为 bundle_slim 四项假 DLL fixture、历史品牌文案、旧版 build 脚本 BOM、HTTP
代理环境一项；没有新增失败。包含 L189 的最终 Windows 整仓另行执行。

实际执行 E 源码构建入口，在 PyInstaller 和产物目录创建前拒绝该 Intel VM：
`RuntimeError: macOS candidates require native arm64 and macOS >=14.4`。
系统版本已满足，阻塞原因是 x86_64 架构；不降低候选门，也未生成伪 arm64 包。
该探针外层同样遇到上述 zsh 变量名问题，保留原始 traceback，不混淆为构建成功。

## 十四、授权后的原生输入与 GIF 修复

用户确认开启两项隐私权限后，重启临时 GUI 验收工具，录屏和 PostEvent 均返回 true。
SSH 后台 Python 的两个预检仍 false，不能用后台身份替代桌面授权身份。后续原生输入
通过同一 Python 的临时 GUI LaunchAgent 执行；权限窗口已关闭，旧请求服务已卸载，
没有修改 TCC 数据库。许可前/后 JSON 原始证据保留。

native-input-1 使用未完整启动的合成 PetWindow：独立进程收到透明角落点击，桌宠和
气泡被动显示保留前台 PID，按下/拖动/释放也到达；但释放路径因 fixture 缺少 catalog /
player 报错，手动穿透断言失败，**不计为整体验收通过**。

native-input-2 改为完整启动的真实 GIF 角色，发现 `is_transparent_at` 对 QPixmap 调用
pixelColor，造成 AttributeError、Cocoa 输入轮询失败。该帧类型问题也在 Windows
offscreen 的实际 GIF 回归复现：**1 failed，1.65s**。在媒体兼容层把 currentPixmap
返回值转为 QImage 后，GIF/输入/帧节拍 **34 passed，1.54s**；没有绕过动画或命中处理。
WebM/帧序列已有 currentImage 分支不变，GIF 转换成本和原生重跑结果另补。

E 的 Windows 最终全量已执行到约 65%，因这一新产品修复主动中止；保留 partial log
和 `mac-full-repository-final-interrupted.json`，不当作完整门禁。前述 L187 整仓仍是已
正常结束的基线。所有原生问题收口后，再跑最终积累代码的完整门禁。

F 快照：1990 文件 / 150998444 B，压缩包 125469131 B，SHA256
`fb069be2ecf8ce81410480790015c13773db31df7a0cd752c98199ac2a9f496b`。
原生 Cocoa 窗口策略与 GIF 回归 **3 passed / 1 skipped，2.77s**。
`mac_native_input_async.py` 使用真实 GIF PetWindow、独立 Qt 接收进程、CGEventPost：
**29 步通过**，接收进程正常退出 0。桌宠/气泡被动显示保持外部前台 PID；透明角落
和手动整窗穿透均由另一进程收到点击；主体按下、连续拖动、释放、关闭穿透恢复、
隐藏停止轮询及原生句柄重建后再点击均通过，事件 spontaneous=true。
用户点击桌宠可激活应用；不把“被动显示不抢焦点”扩大成“所有点击都不激活”。

早期 input-3 至 input-8 的驱动把多阶段验收放在长 Python 回调/嵌套事件循环里，
且曾错误等待非前台窗口的 hover 回调。Qt Cocoa 明确跳过非活动应用 hover；最终
驱动改为每步返回根 QApplication.exec 的异步状态机，未增加其他输入产品补丁。
保留所有失败原始结果，不将其列为产品修复。参考
[Qt Cocoa 鼠标实现](https://raw.githubusercontent.com/qt/qtbase/6.12/src/plugins/platforms/cocoa/qnsview_mouse.mm)。

GIF 调用成本：同一 VM 的真实 QMovie/PetClip，`mac_gif_cost.py` 三组各 10000 次。
原 currentPixmap 为 **0.600 / 0.744 / 0.608µs**；统一 current_image 为
**2.482 / 2.381 / 2.325µs**，三组 RSS 增量均 0 B，线程均 6→6。
转换为 QImage 的成本发生在 GIF 取帧，不增加定时器、网络、磁盘写入或线程；不代表
整应用 CPU 或长期内存稳定性。用户取消的 30 分钟观察没有恢复。

## 十五、完整应用输入与 Cocoa 托盘修复

完整 `run()`、内置服务、shenshen WebM、中文空格数据根、真实 Cocoa 桌面下：

- 桌宠右键第一次选择“桌宠设置”即显示并获得键盘焦点，菜单已关闭。
- 通过 NSTextInputContext 选择系统 `com.apple.inputmethod.SCIM.ITABC`，真实按键
  `zhongwen` + 空格产生 8 次预编辑及一次“中文”提交；不是剪贴板粘贴或直接 setText。
  结束后恢复原 `com.apple.keylayout.ABC`。聊天框真实键盘输入 abc，没有发送消息。
- 设置打开前后 NSApplication.activationPolicy=Accessory；无额外产品 Dock 实例。
- full-ui-4 暴露托盘菜单刚打开即关闭。Qt Cocoa 给 NSStatusItem 分配 NSMenu 后，
  会先进入原生跟踪再发 aboutToShow；原有动态清空重建动作使菜单关闭。左击还会
  同时执行原来的显隐切换。参考
  [Qt Cocoa 托盘实现](https://raw.githubusercontent.com/qt/qtbase/6.12/src/plugins/platforms/cocoa/qcocoasystemtrayicon.mm)。
- L191 保留原生状态图标，Cocoa 左/右击改为打开共用 QMenu popup，复用既有动态
  动作和延后分派；并登记菜单会话，抑制被动气泡/桌宠在菜单上方抬窗。
  其他平台的托盘激活语义不变。首次选择及会话回归分别先红 1 failed（各 1.02s），
  最终相关 **81 passed / 1 skipped，3.22s**，ruff 通过。
- G 原生 full-ui-6 已验证托盘“显示桌宠”、右键“设置”和“退出”：首次选择成功，
  退出 action triggered、menu hidden、host closing 均记录，`run()` 返回 **0**。
  其窗口截图保存在本机原始证据目录；新增会话登记仍需最终快照复验。

G 快照 SHA256 `72a14c351526d7dea1008abd8ca3cc2ab63c3d324b6cfb8ac6e34f3f7b332174`，
1990 文件 / 151001976 B，归档 125470777 B。临时工具 full-ui-1 因启动器分支错误
只跑了策略测试，不计 UI 验收；full-ui-2 查找输入法 ID 错误；full-ui-3 在设置销毁后
再次使用旧 NSTextInputContext 指针而崩溃，修正工具只恢复一次后 full-ui-4 正常退出。
full-ui-5 未在退出菜单完成绘制后注入点击，保留其未退出记录并 bootout；full-ui-6
保存实际菜单画面再点击，验证正常退出。未为这些工具问题修改产品。

性能冒烟仅为 1/4 宠 × 待机/动画/拖动/隐藏各一组 3–4 秒，八场景完成、退出 0，
隐藏场景输入 refresh=0、update=0。不能把冒烟替代最终三组 60 秒数据。

## 十六、H 累计门禁、全屏与菜单成本

H 快照 1990 文件 / 151007738 B，归档 125474243 B，SHA256
`419e3d8cd282bb584e6e9e597f19cc2d0ef1095df08557bc016e8c8f34722871`。
命令在 `source-h/petcore-source` 使用同一验收 `.venv/bin/python`，不在运行期间修改
该源码。F、G 各 1990 个清单文件运行后逐一 SHA256 核验 changed=[]。

| H 检查 | 命令范围与真实结果 |
| --- | --- |
| Mac petcore 累计 | `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_petcore_*.py -q`：3193 passed / 19 skipped，271.70s，退出 0 |
| Mac 其他守卫 | `tests/test_architecture.py tests/test_pr_report_discipline.py tests/test_ci_workflows.py`：446 passed，3.42s，退出 0；与上一行合计 3639 passed / 19 skipped，分两次执行 |
| Mac 4 核高负载三遍 | `stress_mac_final.py`：每遍 127 passed / 1 skipped，包含 GIF、托盘、窗口、截图、气泡、窄窗；进程墙时 18.705 / 18.308 / 17.215s，三次均退出 0 |
| 压力环境 | 108 个 CPU 样本，平均 99.074%，最小 0%，最大 100%；初始最小值不删去 |
| Cocoa 原生门 | GUI LaunchAgent 执行窗口策略、GIF 和托盘首次选择：4 passed / 1 skipped，2.67s；跳过的是 X11 专属项 |
| H 完整应用 | `mac_full_ui.py` 的 full-ui-7：菜单、拼音、聊天、恢复、托盘设置与实际菜单退出通过，`run()` 返回 0 |
| 源码 strict | `QT_QPA_PLATFORM=cocoa python -m petcore --check --strict --root <独立根>` 退出 0；此 SSH 身份仍与已授权 GUI 身份区分 |
| Windows 文档等守卫 | 459 passed，8.80s；完整 `ruff check pet/ tests/ petcore/` 通过；最终整仓 11760 passed / 24 skipped / 7 failed，1561.88s，七项与基线一致 |

G 的 `mac_spaces_capture.py` 使用独立进程的普通 Qt 窗口进入原生全屏，监听真正的
`NSWindowDidEnterFullScreenNotification` / `NSWindowDidExitFullScreenNotification`，
并等待屏幕边界实际绘制完成。前台 PID 始终是外部接收进程，原生桌宠在活动 Space，
截图有约 1220 个采样像素区别于纯色全屏背景。退出全屏回到桌面、再进入全屏，桌宠
仍显示。随后真实鼠标框选 **300×200**，点击复制得到 **672 B PNG**；第二次截图 Esc
取消，桌宠仍可见；最后回到普通窗口，两进程退出均 0。
参考 [Apple 全屏完成通知](https://developer.apple.com/documentation/appkit/nswindow/didenterfullscreennotification)。

这是全屏 Space 进入/退出证据。独立桌面 Space 的 Ctrl+左/右验证未完成：系统快捷键
79/81 均 enabled=1；无论仅设置 Control flags，还是补真实 Control down/up，接收
窗口的 isOnActiveSpace 都未出现预期变化，45 秒条件预算超时。没有把超时冒充成功，
也没有为此修改产品；保留 spaces-capture-2/3/4 的探针。首轮错误地在转场中截屏，
后续改为上述通知和绘制条件同步；成功记录是 spaces-capture-5。

菜单成本命令为 H 的 GUI `mac_tray_cost.py`：完整应用实际托盘激活至第一次 QMenu
Paint，三组各 20 次。**Mac 累计 pytest 和宿主 Windows 整仓同时运行**，因此这不是
隔离空载测量，也不能与后续场景稳态样本混用。

| 组 | 首次绘制中位 / 最大 ms | 组内主进程 CPU s | RSS 增量 B | 线程 |
| --- | ---: | ---: | ---: | --- |
| 1 | 59.236 / 404.515 | 0.923 | 14389248 | 13→18 |
| 2 | 43.364 / 281.490 | 0.860 | -233472 | 18→18 |
| 3 | 41.476 / 94.832 | 0.766 | 196608 | 18→18 |

第一组与完整应用的服务/素材启动重叠，不能把其线程和 RSS 增量全归给菜单。L191
没有增加稳态轮询、网络、磁盘写入或工作线程；新会话只在菜单显示/关闭触发，Qt
负责原生 popup 创建/显示，未进行 syscall 计数。仍不能据短样本证明长期无增长。

26 个已结束的临时 GUI 验收任务已卸载；原始退出码和结果保留，没有终止活动测试。
正式 24 组短样本从 H 执行，Mac 高负载 worker 已退出；宿主 Windows 整仓仍在后台。
真实登录自启使用 CLI 生成的用户 LaunchAgent 已准备：解释器和数据根均含中文及
空格，0600、plutil OK，工作目录指向 H；尚未手动 bootstrap，等待性能完成后重登。

## 十七、L192 补充验收与未通过项

- `spaces-island-1` 在 H 的完整应用启用灵动岛，再隐藏、恢复。三个阶段外部前台 PID
  不变；普通 NSWindow、canBecomeKeyWindow=false、hasShadow=false；全屏时宠物和岛
  都在活动 Space。随后完成全屏退回桌面/再进入、300×200 复制（672 B PNG）及 Esc
  取消，两进程正常退出 0。这不覆盖独立桌面 Space 的快捷键切换。
- H 的 1990 个清单文件运行后逐个 SHA256 复核，changed=[]。Windows 整仓 XML 与
  已记录基线比较 introduced=[]、resolved=[]，原始记录 `mac-final-failure-comparison.json`。
- 原生补测看到穿透提示仍在说 Windows 的“仅左键/右键长按”。把现有托盘回归补上
  文案约束，先红 1 failed（2.91s），更换两条提示字符串后 9 passed（5.67s）。
  不改输入、设置、接口或生命周期，因此只跑相关测试，不为这次纯文案再次跑整仓。
  H 的原生和性能证据对应文案修改前；最终工作区另有这两个字符串及断言的差异。

### 托盘穿透恢复：自动化未通过

`full-ui-8/9/10/11` 均能从真实菜单启用穿透，原生 ignoresMouseEvents=true；再次打开
菜单后取消动作未触发。窗口可见、alpha=1、ignoresMouseEvents=false，NSApp active，
QMenu 坐标为 (1104,30,138,224)，光标确实到 (1172,72/73)，但 activeAction=None。
原生事件过滤器记录移动事件的 windowNumber 仍指向菜单栏状态按钮，而不是弹出的
菜单。后续探针确认 NSWindow.parentWindow=0、Qt transientParent=None、
NSEvent.pressedMouseButtons=0、run loop 已回默认模式。`full-ui-11` 无宿主压力负载，
仍复现；不能简单归因于 CPU 饥饿。

这些记录尚不能证明人工鼠标也会失败。临时工具试过激活、exec、等待原生跟踪结束、
左键恢复、持续移动、重新创建 QMenu 等诊断，没有据此修改产品。append-menu 与
detach-menu 的两个诊断任务曾重叠，已停止，**不作为有效对照**；启动器随后增加
“同一时刻只能有一个活动 GUI 验收任务”的硬检查。后续用只观察、完全不注入输入的
工具进行人工交叉验证，未得到结果前不宣称托盘穿透恢复通过。
原生事件格式依据 [Qt QAbstractNativeEventFilter 文档](https://doc.qt.io/qt-6/qabstractnativeeventfilter.html)，
状态按钮按下即发 activation 的实现见 [Qt Cocoa tray 源码](https://raw.githubusercontent.com/qt/qtbase/6.12/src/plugins/platforms/cocoa/qcocoasystemtrayicon.mm)。

### Windows 高负载：退出门未通过

16 个 CPU worker，首轮 127 passed / 1 skipped（66.18s）后进程未退出，180 秒超时。
加入正常退出探针（保留 pytest 返回码，不调用 os._exit）后第一遍正常退出 0，
第二遍在原生清理再次卡住；第二遍还与新文案的先红测试编辑重叠，不能作为最终源码
门禁。随后按仓库规则隔离测试族：窗口/GIF/托盘/截图/气泡/层级七族都正常退出，
布局族 13 passed（13.64s）后仍卡住。faulthandler 只见主线程、没有 Python 栈；
显式调用 QApplication.shutdown 也停在该原生调用，未绕过退出。

改用真实 Windows QPA 的布局诊断在既有 `_dialog` 构造路径原生崩溃，记录了
0xc000070a 与进程退出码 3221226525。这不是 Cocoa 失败，也没有证据证明是本批
布局两行变更引起；仍必须保留“Windows 三遍高负载退出门未完成”的结论，不能拿
断言统计冒充正常退出。已停止无依据重跑。以上原始日志在
`.scratch/petcore-posix/mac-win-stress-l191*`、`mac-win-stress-l192-*`。

### 正式性能中止记录

`perf-full-1` 留下 18 个有效 ≥60 秒样本：单宠四场景各三组，四宠待机/动画各三组。
四宠拖动未收到按下事件后中止，completed=false；应用 run 返回 0，但验收脚本和
launchd 正确退出 1，不能据应用返回码判通过。失败样本不计入已完成统计。
后续四宠短冒烟通过，正式剩余两场景使用独立进程补测；缓存与 RSS 起点不同，
汇总时必须保留各自进程身份，不能当成一条连续内存曲线。未重新开启 30 分钟观察。

## 十八、24 个有效短性能样本

H 源码，macOS 15.8.2 x86_64 VM，4 vCPU / 8 GiB，Qt 6.12.0，1440×900 / DPR 1。
GUI LaunchAgent 运行 `mac_full_performance.py`：完整 `run()` 与内置服务，shenshen WebM、
0.72 缩放，声音和随机动作关闭，frameseq=off；原有碰撞与服务保留。一次采样约 1 Hz，
拖动由原生 CGEventPost 驱动。每组 ≥60 秒，保留所有采样点，没有删除 CPU/RSS 离群值。

**18+6，来自两个进程**：首轮 `perf-full-1` 在四宠拖动之前失败，保留其前 18 个有效
场景；`perf-tail-full-1 --tail` 独立运行四宠拖动/隐藏各三组，completed=true、脚本及
launchd 退出 0。后六组没有宿主 pytest 或 CPU worker；前十八组与 Windows 整仓部分
重叠（该整仓 UTC 14:50:39.917 结束）。两次都没有并行运行其他 Mac GUI 验收任务。
补测选择身体中部的实心点并确认真实按下/持续拖动；没有为此修改产品。

CPU 只统计应用主进程（单核 100%），包含采样器与输入计时；**不含解码子进程 CPU**，
子进程 RSS 单列。Mac 的“状态提交”统计 ignoresMouseEvents 状态改变，不能和 Linux
XFixes 轮廓更新数混为一谈；静态状态命中缓存，隐藏后刷新与提交都归零。
以下 RSS 均为 MiB（2²⁰ B），同一场景的最大子进程 RSS 与最大主进程 RSS 不保证同时发生。

| 宠数 / 场景 | CPU %（三组，单核 100%） | 主进程 RSS 峰值 MiB（三组） | 子进程 RSS 峰值 MiB | 最大线程 | 输入刷新 / 状态提交（合计） | 平均输入处理 µs/次 |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| 1 / 待机 | 9.38 / 8.28 / 9.82 | 113.86 / 116.21 / 116.27 | 57.87 | 16 | 5803 / 0 | 99.55 |
| 1 / 动画 | 12.29 / 12.34 / 12.74 | 124.19 / 124.20 / 124.20 | 29.92 | 13 | 7902 / 0 | 86.64 |
| 1 / 拖动 | 22.47 / 22.58 / 22.95 | 134.07 / 134.36 / 135.00 | 58.73 | 17 | 7973 / 0 | 84.06 |
| 1 / 隐藏 | 4.29 / 4.15 / 3.55 | 134.62 / 134.49 / 134.49 | 0.00 | 11 | 0 / 0 | 0.00 |
| 4 / 待机 | 28.50 / 29.16 / 29.59 | 199.15 / 201.93 / 202.07 | 225.29 | 28 | 22911 / 0 | 57.37 |
| 4 / 动画 | 46.54 / 47.38 / 48.05 | 220.37 / 220.50 / 220.34 | 201.35 | 24 | 31495 / 0 | 73.92 |
| 4 / 拖动 | 46.16 / 44.66 / 45.85 | 182.12 / 190.43 / 193.32 | 201.73 | 28 | 24480 / 0 | 85.81 |
| 4 / 隐藏 | 4.50 / 4.53 / 4.57 | 193.19 / 193.06 / 193.06 | 0.00 | 11 | 0 / 0 | 0.00 |

| 进程 / 宠数 / 场景 / 组 | 秒 | RSS 首→尾 MiB | 输入处理 ms | 拖动样本 / 总样本 |
| --- | ---: | ---: | ---: | ---: |
| 4824 / 1 / 待机 / 1 | 61.001 | 112.84 → 113.83 | 181.569 | 0 / 62 |
| 4824 / 1 / 待机 / 2 | 60.998 | 113.83 → 116.20 | 198.755 | 0 / 62 |
| 4824 / 1 / 待机 / 3 | 60.001 | 116.20 → 116.21 | 197.379 | 0 / 61 |
| 4824 / 1 / 动画 / 1 | 60.003 | 124.15 → 124.16 | 222.340 | 0 / 61 |
| 4824 / 1 / 动画 / 2 | 60.998 | 124.16 → 124.18 | 227.148 | 0 / 62 |
| 4824 / 1 / 动画 / 3 | 60.000 | 124.18 → 124.20 | 235.117 | 0 / 61 |
| 4824 / 1 / 拖动 / 1 | 61.000 | 133.30 → 133.66 | 220.331 | 62 / 62 |
| 4824 / 1 / 拖动 / 2 | 61.004 | 133.66 → 133.66 | 223.176 | 62 / 62 |
| 4824 / 1 / 拖动 / 3 | 60.996 | 133.66 → 134.58 | 226.670 | 62 / 62 |
| 4824 / 1 / 隐藏 / 1 | 60.001 | 134.62 → 134.49 | 0.000 | 0 / 61 |
| 4824 / 1 / 隐藏 / 2 | 60.998 | 134.49 → 134.49 | 0.000 | 0 / 62 |
| 4824 / 1 / 隐藏 / 3 | 60.000 | 134.49 → 134.49 | 0.000 | 0 / 61 |
| 4824 / 4 / 待机 / 1 | 60.998 | 189.79 → 198.69 | 423.218 | 0 / 62 |
| 4824 / 4 / 待机 / 2 | 60.000 | 198.69 → 201.55 | 459.279 | 0 / 61 |
| 4824 / 4 / 待机 / 3 | 60.000 | 201.55 → 201.66 | 432.009 | 0 / 61 |
| 4824 / 4 / 动画 / 1 | 60.002 | 216.68 → 219.46 | 783.613 | 0 / 61 |
| 4824 / 4 / 动画 / 2 | 61.001 | 219.46 → 219.50 | 769.940 | 0 / 62 |
| 4824 / 4 / 动画 / 3 | 60.996 | 219.50 → 219.88 | 774.427 | 0 / 62 |
| 5991 / 4 / 拖动 / 1 | 60.990 | 168.01 → 182.12 | 726.711 | 62 / 62 |
| 5991 / 4 / 拖动 / 2 | 61.030 | 182.12 → 188.61 | 678.025 | 62 / 62 |
| 5991 / 4 / 拖动 / 3 | 60.975 | 188.61 → 192.42 | 695.856 | 62 / 62 |
| 5991 / 4 / 隐藏 / 1 | 60.001 | 193.19 → 193.06 | 0.000 | 0 / 61 |
| 5991 / 4 / 隐藏 / 2 | 60.001 | 193.06 → 193.06 | 0.000 | 0 / 61 |
| 5991 / 4 / 隐藏 / 3 | 60.998 | 193.06 → 193.06 | 0.000 | 0 / 62 |

一宠待机主进程约 8.28–9.82% 单核 CPU，四宠待机约 28.50–29.59%；四宠动画约
46.54–48.05%。隐藏后主进程仍约 3.55–4.57%，因此没有宣称“隐藏零开销”：输入轮询
和解码子进程已经停下，应用原有后台服务与采样器仍存活，线程最终为 7。

单次输入刷新平均 57.37–99.55 µs；除场景切换外，24 组测量区间内原生状态提交均为 0，
没有逐帧重建窗口。计数包含画面刷新与共享定时器触发；未采集系统调用总数，不能据此
宣称没有系统调用。平台输入没有新增网络、落盘或独立工作线程；完整应用原有服务不算
本次新增路径。菜单、原生属性、GIF 转换等一次性成本见前文各实测表。

内存存在增长：首进程跨动画/多宠加载阶段的 RSS 增加；补测进程的三组拖动中 RSS
从约 168 MiB 增到约 192 MiB。隐藏末两组均为约 193.06 MiB，短时没有继续增加。
这些数字不能区分缓存、分配器保留与泄漏，也不能把两个进程连接成一条内存曲线。
用户取消了 30 分钟观察，因此长期内存稳定性仍未验证。

原始 `result.json` / `trace.jsonl` / 启动日志分别保存在验收目录 `perf-full-1` 和
`perf-tail-full-1`；本机汇总脚本校验 24 个唯一场景/组号、时长与拖动连续性，并保留
第一轮 completed=false 与失败原因，没有把中止任务改写为成功。

## 十九、当前源码、证据归档与人工交叉验证

I 源码快照包含 1990 文件 / 151024320 B，源码压缩包 125480656 B，SHA256
`3ca09e3bd861200eada3dac9a50dd06427a42ac3da1beb5c49d2d0fd531f9b2f`。
相对 H 只变更四份文档、`petcore/ui/app.py` 两条提示和托盘测试的一条文案断言；
平台/输入/媒体/菜单实现相同。H 与 I 的 1990 个清单文件最终复核均 changed=[]。
报告后续追加证据，不回写这些已冻结的源码目录。

I 在 Mac 的托盘相关 offscreen **9 passed（3.67s）**，strict **退出 0**；没有将这次
offscreen 结果代替未完成的真实鼠标恢复。最终本机架构/报告/CI 守卫 **455 passed
（7.56s）**，文档内容守卫另跑 **4 passed（0.74s）**；ruff 与 diff --check 通过。
纯文案后的相关检查与 H 的累计门分开记数，不重复宣称跑过一套新的整仓。

已归档 362 个完成任务的日志、XML、JSON、截图与辅助脚本，共 30602126 B，SHA256
`bcc33c4685705177285635ad10690c67720b2ab8ae5fc4d8d136da57426a671e`。
本机路径 `.scratch/petcore-posix/mac-2026-11-08i/mac-evidence-l192.tar.gz`，已重新校验摘要
并解包到同级 `evidence/mac-evidence`。其中保留失败记录；未把仍在等待人工输入的
`manual-recovery-1` 混入“完成任务”归档。Windows 门禁原始文件仍在本机 scratch。

22 个结束的临时 GUI jobs 已卸载，活动的人工观察工具保留。该工具基于 I，**不注入
鼠标或按键**：只记录真人从菜单开启/关闭穿透，再点击宠物后的原生 press/release。
当时仍等待用户操作；工具设有 15 分钟超时，不会无限常驻。最终人工结果见第二十节。
自动注入改用显式 HID event source 的诊断也未解决菜单问题，尚未据此修改产品。
截图中曾出现“后台项目已添加”的系统提示；不能仅凭菜单自身坐标/可见性排除系统
遮挡，因此人工交叉验证也不能省略。只读窗口探针不读取窗口标题或私人内容。

真实登录自启的用户 LaunchAgent 已通过 CLI 更新为 I（先确认仍是本次创建的原条目），
中文解释器和独立数据根参数不变、0600，未手动 bootstrap。已请用户完成穿透交叉验证
后注销并重新登录。该阶段尚未验证，最终实测与清理见第二十节。
旧保持唤醒进程已停止，当前临时保持唤醒最多 30 分钟，测试结束时也应主动关闭。

当前不提交/推送 Mac 修改，不发布 Release；用户原有架构图修改仍保留。

## 二十、人工恢复、真实登录自启与清理

用户确认“恢复正常，并已重新登录”。I 的 `mac_manual_recovery.py` 不注入任何鼠标
或按键，只观察完整应用；`manual-recovery-1/result.json` 记录穿透 false→true→false，
随后在关闭穿透的状态下捕获 spontaneous 鼠标 press/release，completed=true、退出 0。
恢复截图显示宠物及菜单栏图标正常，Finder 仍为前台。观察器未记录到其早先绑定菜单
对象的 show/hide 信号，因此“从托盘操作”的路径来源是用户确认，不冒充动作级自动
跟踪。透明区域下原生 ignoresMouseEvents=true 是自动 alpha 穿透状态，不能把状态
切换瞬间的该值误读为手动穿透仍打开；最终真实身体点击已经收到。

这证明人工托盘恢复路径可用，不等于修复了第十七节的 CGEventPost 自动化异常；
没有为此添加猜测性产品改动，也没有删除失败记录。

登录期间只读探针先观察到 `/dev/console=root`、GUI domain 不存在；用户重新登录后
变为 eager。`launchctl print gui/501/io.dsh.petcore` 返回 running、runs=1、PID 525、
PPID 1。该正式测试登录项从准备到重新登录**没有手动 bootstrap**。读取实际进程
argv/cwd 与 CLI 生成的 plist 逐项一致：

```text
/Users/eager/petcore-acceptance/2026-11-08-1/运行 环境/bin/python
-m petcore --root /Users/eager/petcore-acceptance/2026-11-08-1/登录 自启验收 H
cwd=/Users/eager/petcore-acceptance/2026-11-08-1/source-i/petcore-source
```

数据根名称仍带 H，实际源码是 I。第一次探针时窗口尚未建成，保留 windows=[] 的
原始结果；第二次确认桌宠窗口 `(1107,578,461,259)` 和状态图标 `(1104,0,38,24)`
均 on-screen。随后独立 GUI 截图工具不输入任何事件，capture=true、退出 0；截图
确认宠物和图标实际可见，没有把“进程存在”当作桌面启动完成。独立数据根保留
shenshen / active_pets=[0] / frameseq=off / 声音关闭的验收配置。

清理前比对测试 plist 和进程启动时间，避免误碰外部变更或复用 PID。实际 CLI
`autostart off` 返回 disabled / 退出 0，文件已不存在；卸载该测试登录项和已结束的
截图 helper 后，owned pids=[]、acceptance jobs=[]，临时 caffeinate 也已结束。
这次 bootout 是测试环境清理，**不计作正常菜单退出证据**；菜单退出的证据仍是
full-ui-7。SSH 测试访问保留供后续验收，没有留下测试自启项或后台输入工具。

登录与清理后，I 源码清单 **1990 文件 changed=[]**。补充证据归档含 17 文件，
285172 B，SHA256 `b85b0ed1d02ef1c2b4a49c6034a047127d970ad9af99b4cb7df8fd45c400a9fd`，
本机路径 `.scratch/petcore-posix/mac-2026-11-08i/mac-evidence-l193.tar.gz`。
其中包括人工观察结果/脚本/截图、登录前后探针、清理结果与源码摘要复核；与 L192
归档分别保留，不覆盖已有证据。

本批只有证据文档更新，无运行行为变化，不重复整仓或性能采样；仍保留前述整仓
七项既有失败、Windows 压力退出超时、独立桌面 Space/混合 Retina/物理热插拔未验，
以及 arm64 构建机缺失的限制。当前只能交付 Intel 源码运行验收，不能宣称 arm64
`.app`、签名或安装后启动已通过。Mac 修改尚未提交或推送。

最终文档/架构/报告/CI 守卫命令 `python -m pytest -q tests/test_petcore_docs.py
tests/test_pr_report_discipline.py tests/test_architecture.py tests/test_petcore_architecture.py
tests/test_ci_workflows.py`：**459 passed（9.05s）**；完整 `ruff check pet/ tests/ petcore/`
与 `git diff --check` 通过。逐文件清单已按本批最终差异刷新；用户架构图未纳入。

## 二十一、用户授权推送范围

用户在获知上述验证结果和限制后要求“mac 的改动也推送”。本次提交本报告清单中的
24 个 Mac 相关代码、回归与文档文件，推送当前 `feat/petcore-vpet-architecture` 分支；
保留用户架构图的未提交改动，不提交 scratch、VM 凭据、截图或构建产物，不发布 Release。
前文“尚未提交/推送”描述授权前的历史阶段。

推送前按 I 源码清单核对当前 `petcore/` 与 `tests/` 文件摘要，没有新的代码差异。
复用已记录的整仓与 Mac 三轮压力结果，不因提交操作重复长测；再次执行 ruff、相关
托盘测试及文档/架构守卫。Windows 七项既有失败、压力退出超时和 arm64 包缺失仍按
原样记录，此次授权推送不表示这些门禁或交付项已经完成。

本次复核：`ruff check pet/ tests/ petcore/` 通过；托盘、文档、报告、架构和 CI 守卫
合计 **468 passed（8.69s）**，进程正常退出 0；`git diff --check` 通过。I 清单中
`petcore/` 与 `tests/` 的 **909 文件摘要一致**，用户架构图摘要独立记录并保持不变。
