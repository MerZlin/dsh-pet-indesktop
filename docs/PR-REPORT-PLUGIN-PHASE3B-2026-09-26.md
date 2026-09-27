# Phase 3B 主动识屏 Worker 实现与验证报告

> 基线：`9834612`；分支：`codex/phase3-worker`。
> 报告日期：2026-09-26；本机验证日志使用 Asia/Shanghai，日志时间为 2026-09-27 凌晨（UTC 为 2026-09-26）。
> 状态：实现与本地自动化验证通过；真实截图、远程模型和完整人工交互仍待验收。未提交、未推送，不能据此宣布 Phase 3B 全部封存。
> 设计入口：[Phase 3B 边界设计](plugin-phase-03-worker/PHASE3B-PROACTIVE-SCREEN-DESIGN.md)。
> 文档入口：[INDEX](INDEX.md)。

## 一、修改文件说明

本轮同时迁移自动识屏与手动“看看屏幕”的执行通道，不改公共 UI/CLI、不重写 Core 策略。
Core 仍掌握白名单、dwell/idle、额度、generation、keyring 和展示；官方 Worker 只执行前台查询、截图、dHash 和现有视觉 HTTP 请求。
没有新增依赖、持久化设置、Worker DLC、第三方代码、远程安装或长期 soak。

以下统计相对基线，由 `git diff --numstat` 与新增文件实际行数生成；不包含被忽略的临时探针、日志和构建产物。
没有删除或移动文件。每个“新增”文件均为 `+N/-0`。

<!-- FILE-STATS-START -->
| 文件 | 类型 | 增/删行 | 改了什么、为什么 |
|---|---|---:|---|
| `docs/INDEX.md` | 修改 | +3/-1 | 登记设计与证据报告，区分本地通过和剩余人工验收。 |
| `docs/plugin-phase-03-worker/README.md` | 修改 | +15/-18 | 同步阶段状态与入口，不改动历史收口报告。 |
| `docs/plugin-phase-03-worker/PHASE3B-PROACTIVE-SCREEN-DESIGN.md` | 新增 | +178/-0 | 新增双通道、协议、密钥、边界、复建顺序与用户最终效果合同。 |
| `docs/PR-REPORT-PLUGIN-PHASE3B-2026-09-26.md` | 新增 | +248/-0 | 新增逐文件、实测性能、构建/桌面证据及未完成门记录。 |
| `pet/app.py` | 修改 | +15/-1 | 退出先停 watcher，事件循环结束后有界收口 Worker，避免活进程析构。 |
| `pet/chat/__init__.py` | 修改 | +12/-1 | ChatService 保留公共名称但懒加载，隔离数据模型与 Qt 服务导入。 |
| `pet/multi_window_shared.py` | 修改 | +5/-2 | 共享宿主显式启用 auto；进程级 stop_all 取消并停止一个共享 Worker。 |
| `pet/proactive.py` | 修改 | +549/-19 | Core 保留策略/limiter/记忆；接入异步 observe/capture/analyze、手动 owner、generation、取消与旧路径回退。 |
| `pet/window.py` | 修改 | +21/-0 | 保持手动 UI/冷却/入口，改用 adapter；关闭时取消本窗请求并拒绝已销毁窗口回调。 |
| `pet/window_optional_services.py` | 修改 | +1/-1 | 正式懒创建 watcher 显式选择 auto，不改变旧构造调用方默认行为。 |
| `pet/workers/protocol.py` | 修改 | +8/-1 | 增加 request/response 与 request_id 校验，兼容既有 Agent Link 消息。 |
| `pet/workers/supervisor.py` | 修改 | +136/-2 | 增加 RPC/能力握手/状态校验和有界退出收口，不阻塞常态 GUI。 |
| `pet/workers/worker_entry.py` | 修改 | +5/-1 | 显式 allowlist 增加 proactive-screen；仍在 UI 导入前分流。 |
| `pet/workers/proactive_screen_adapter.py` | 新增 | +388/-0 | 新增 Core pending/超时/代数与额度路由，筛选配置，不缓存请求秘密。 |
| `pet/workers/proactive_screen_worker.py` | 新增 | +723/-0 | 新增无 Qt 的前台/截图/单帧/视觉执行，有限队列、心跳、取消和凭据清理。 |
| `scripts/verify_phase3b_frozen_worker.py` | 新增 | +181/-0 | 新增源码/冻结握手、能力/观察/关闭复验与有界测量，不截图/上传。 |
| `tests/test_single_process_shared.py` | 修改 | +4/-0 | 旧策略 mock 用例明确选 legacy；真实共享 Worker 由新增进程测试覆盖。 |
| `tests/test_workers_protocol.py` | 修改 | +32/-0 | 新增 RPC request_id、消息形状与兼容性回归。 |
| `tests/test_proactive_watcher_worker.py` | 新增 | +474/-0 | 新增 Core 策略、阈值、额度、手动/自动隔离、fallback 与 stale 回归。 |
| `tests/test_proactive_worker_adapter.py` | 新增 | +102/-0 | 新增配置/凭据筛选、pending/response、budget 与关闭测试。 |
| `tests/test_proactive_worker_app_shutdown.py` | 新增 | +91/-0 | 新增真实 python -m pet 优雅退出、Worker finished 与无残留进程断言。 |
| `tests/test_proactive_worker_integration.py` | 新增 | +245/-0 | 新增真实 QProcess+HTTP 重试/额度/Key、共享窗口 owner 与无 Qt 导入验证。 |
| `tests/test_proactive_worker_lifecycle.py` | 新增 | +234/-0 | 新增真实子进程握手/心跳/取消/超时/EOF与故障回归。 |
| `tests/test_proactive_worker_smoke.py` | 新增 | +38/-0 | 新增独立脚本源码模式成功与无效命令非零退出回归。 |
| `tests/test_proactive_worker_source.py` | 新增 | +164/-0 | 新增执行层截图窗口校验、TTL/代数、帧上限、错误/密钥边界测试。 |
<!-- FILE-STATS-END -->

需要解释的边界调整：

- `vision` 复用 `chat.models` 时原本会经过包初始化提前导入 Qt 服务，因此将 `ChatService` 改为懒导出；保留原公共 import 名称，不改变 Chat UI 架构。
- 应用退出时先停止 watcher；`app.exec()` 返回后在 QObject 仍存活时最多处理 4 秒 Qt 事件，收口已有异步 Worker 关闭，避免 QProcess 析构早于子进程退出。不在运行中的 GUI 线程调用阻塞式进程 wait。
- 原 shared subsystem 测试中直接 mock Core 前台查询的用例，显式选择 `in_process` 来继续验证旧策略；新增真实 QProcess/双窗口测试验证默认 Worker 通道，不能用切回旧路径代替新通道验收。

## 二、性能分析

### 环境、命令和样本

Windows 10 build 26100，Python 3.11.1，PySide6 6.11.1，Pillow 11.2.1，psutil 6.1.0。
构建为现有 `webm-chat` onedir；源码/冻结 Worker 各 3 个新进程样本。
使用 `perf_counter` 测墙钟时间，psutil 测 RSS/累计 CPU/线程；不是冷文件缓存测试，不是长期稳定态或远程模型性能结论。

```powershell
python scripts/verify_phase3b_frozen_worker.py --source
python scripts/verify_phase3b_frozen_worker.py dist-onedir/dsh-pet-standalone-webm-chat/dsh-pet-standalone-webm-chat.exe
```

上述两条交替各执行 3 次。只做 hello/config/ready、一次前台查询、约 1 秒空闲采样与 shutdown；不截图、不上传，不打印窗口标题。
原始本地记录：`.scratch/phase3b-proactive/metrics-final.log`。关键数字完整保留如下，复现不依赖临时日志存续。

| 通道/样本 | 启动至 ready（ms） | 前台查询往返（ms） | 关闭（ms） | 查询后 RSS（bytes） | 空闲采样（秒） | CPU 增量（秒） | OS 线程 |
|---|---:|---:|---:|---:|---:|---:|---:|
| source 1 | 87.60 | 41.89 | 17.03 | 24748032 | 1.001 | 0.0 | 5 |
| source 2 | 92.70 | 40.56 | 14.89 | 24506368 | 1.000 | 0.0 | 5 |
| source 3 | 82.86 | 40.62 | 24.63 | 24727552 | 1.012 | 0.0 | 5 |
| frozen 1 | 341.12 | 8.26 | 62.37 | 53948416 | 1.016 | 0.0 | 5 |
| frozen 2 | 352.59 | 11.33 | 62.92 | 52989952 | 1.008 | 0.0 | 5 |
| frozen 3 | 341.84 | 11.07 | 63.05 | 53080064 | 1.004 | 0.0 | 5 |

所有样本退出码 0。CPU 的 0.0 仅表示短窗口中未达到 OS 累计计时分辨率，不能称为永远零开销。
较早单次首次 frozen 启动为 718 ms；不与上表重复采样混算，缓存/机器负载影响未控制。
Windows Python 3.11 的粗粒度单调时钟曾产生 0 ms 读数，验证脚本已改用高分辨率时钟并复跑以上样本。

### Core 加载与 RSS（有界，非长期 soak）

本地隔离探针 `python .scratch/phase3b-proactive/core_metrics.py`：通过临时 sitecustomize 注入观测点，
执行真实 `python -m pet --slot 0`，关闭 Agent Link/自言自语/语音/节日等可选活动，隔离数据目录。
交替测试 Core-only 与 Core+Worker，各 3 次；后者在 Core 启动后显式启动 Worker，但不观察/截图/调用模型。
ready 后约 2 秒取样，再调用 QApplication.quit；6/6 Core 退出码 0，Worker PID 均不存在。
没有新增正式 CLI 参数。临时探针及 stderr 保留在 `.scratch/phase3b-proactive/`，不进入提交。

| 样本 | 启动到观测点（ms） | Core RSS（bytes） | Worker RSS（bytes） | Core CPU 增量（秒） | Worker CPU 增量（秒） | 采样（秒） |
|---|---:|---:|---:|---:|---:|---:|
| Core-only 1 | 3121.39 | 124682240 | 0 | 0.875000 | 0 | 1.994 |
| Core+Worker 1 | 2901.59 | 125927424 | 17534976 | 0.828125 | 0.0 | 2.008 |
| Core-only 2 | 2458.70 | 115806208 | 0 | 0.421875 | 0 | 2.006 |
| Core+Worker 2 | 2655.30 | 123518976 | 17547264 | 0.625000 | 0.0 | 2.012 |
| Core-only 3 | 2771.96 | 122961920 | 0 | 0.671875 | 0 | 2.005 |
| Core+Worker 3 | 2837.71 | 122388480 | 17453056 | 0.531250 | 0.0 | 1.995 |

Core-only 观测点为 AppShell.start 返回；Core+Worker 为 Worker ready，含额外握手阶段，不能直接解释为架构优化/退化量。
Core RSS 中位数分别 117.27/117.80 MiB；Worker ready 后另占约 16.64–16.73 MiB，首次前台查询后会加载更多模块（见上表约 23.4–23.6 MiB）。
采样期间 Core 仍有动画和预热，不是纯静止 CPU；既非旧版本对照，也非内存泄漏证明。

### 成本、频率与未测项

- 关闭自动识屏且无手动请求：不启动识屏 Worker，不产生截图/视觉网络访问。手动请求可按需启动，结束后无自动职责时停止。
- 自动模式沿用 8 秒 Core 定时策略；先过本地策略再发观察 RPC，白名单/dwell/idle 不通过就不截图。自动/手动各至多一个执行任务，无无限请求队列。
- 新增进程与 stdin/stdout 管道、心跳、500 ms Core pending 检查、Worker 控制循环；工作线程最多 2 个，但 OS 总线程数还包含 stdin/父进程监视等，不能说整个 Worker 只有 2 个线程。
- 前台查询/截图是迁移已有 OS 调用，HTTP 仍复用原视觉重试/代理逻辑；每次自动 HTTP 尝试重新经 Core budget_check，手动不计自动额度。增加的是 IPC 控制往返，不是额外模型请求。
- 截图只在内存中，自动帧最多 1 张且有 TTL；Core 不收图片。图像对象/编码副本与网络请求仍有瞬时内存成本，不能以一帧上限宣称固定 RSS。
- 未引入业务截图磁盘写入；真实进程生成图像测试验证临时目录无截图产物。Python import/正常诊断等已有磁盘活动不等于零磁盘访问。
- 真实截图+dHash、远程视觉端到端延迟、长时间 RSS/CPU 与重启时延分布本轮未测。重启/超时/取消有功能测试，不冒充性能基线。没有延长 soak。
- 同一 EXE 的 Worker 不是包体瘦身；未做安装/下载体积前后对照，未验证其他构建 variant 或三平台产物。

## 三、实机运行记录

### 本机 onedir 与冻结 Worker

在工作区内建立隔离 APPDATA/LOCALAPPDATA/TEMP/TMP 后运行（不覆盖真实用户配置）：

```powershell
& D:/DELL/Powershell/7/pwsh.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build_onedir.ps1 -Variant webm-chat -SkipZip
python scripts/verify_bundle_qt.py --internal dist-onedir/dsh-pet-standalone-webm-chat/_internal
python scripts/verify_phase3a_frozen_worker.py dist-onedir/dsh-pet-standalone-webm-chat/dsh-pet-standalone-webm-chat.exe
python scripts/verify_phase3b_frozen_worker.py dist-onedir/dsh-pet-standalone-webm-chat/dsh-pet-standalone-webm-chat.exe
```

构建日志中实际输出：

```text
OK: Shiboken
OK: QtCore
OK: QtGui
OK: QtWidgets
OK: icuuc.dll -> C:\WINDOWS\SYSTEM32\icuuc.dll
ALL OK
[smoke] exe window appeared after 3.7s
[smoke] --settings window appeared after 1.6s
hello capabilities=foreground.read,screenshot.capture,vision.request,logging.write
ready generation=1
observe_foreground status=ok (metadata withheld)
shutdown_sent
PROACTIVE_WORKER_SMOKE_OK returncode=0
```

构建返回 0，原 Phase 3A frozen smoke 同样返回 0。Qt DLL 验证已在构建中执行；上面列出的独立命令可用于复验。
只验证窗口出现，不把构建脚本终止探针视为用户托盘自然退出；后者有以下独立证据及限制。

### 真实 Core 优雅退出

`tests/test_proactive_worker_app_shutdown.py` 启动隔离配置下的真实 Python 应用路径：
Worker ready → Qt 定时触发 QApplication.quit → aboutToQuit → Worker stopping/finished → Core 退出码 0 → PID 不残留。
与 Phase 3A 退出测试组合结果 `2 passed in 11.91s`；本次全量再次通过。
这是实际 Core/Qt/子进程生命周期，不是托盘鼠标点击，也不代表远程截图已验证。

### 可见受控截图：环境门未通过，未越界截图

本地 `python .scratch/phase3b-proactive/desktop_probe.py` 用 Windows Qt 后端显示自建安全文本窗口，
只在 OS 返回的 hwnd **和** pid 都属于该窗口时才允许截图。连续最多 20 次带期限查询没有满足条件，第二次独立复验结论相同：

```text
qt_active=true
worker_has_window=true
worker_hwnd_matches=false
worker_pid_matches=false
core_foreground_agrees_with_worker=true
foreground_attempts=20
error=controlled window not foreground: capture NOT requested
samples=0, network_requests=0
shutdown_ms=36.51, worker_remaining=false
```

Core 直接查询与 Worker 返回一致，不能据此认定 Worker 查询错误；Qt 的 active 状态不等于 Windows OS 实际前台。
这是当前自动化桌面焦点条件限制，真实截图验收仍未通过。没有抓取别的前台窗口，没有上传用户屏幕，没有读取真实用户 keyring 发起付费请求。
不把生成 PIL 图片、loopback HTTP 或 offscreen 测试写成真实截图/模型效果。

### 人工验收清单（尚待执行）

1. 打开只含安全测试文字的应用，手动让它成为前台；启动测试版桌宠，确认拖拽、动画、菜单正常。
2. 如要测试真实模型，先在设置中自行选择视觉 Provider/模型/Key，并明确接受可能的网络与额度费用；手动“看看屏幕”应显示旧有忙碌提示，再只向发起窗口展示结果。
3. 开启自动识屏，将安全应用列入白名单，等待原有 dwell/idle 与限流条件；不在白名单/正在交互/Agent Link 忙时不应自动打扰。
4. 多窗口 shared 模式检查只有一个 proactive-screen 子进程；一次手动结果不应落入另一窗口。
5. 关闭自动识屏后不应继续自动观察；不触发手动入口时 Worker 应退出。手动入口仍可按需启动，这不是禁用了所有截图能力。
6. 托盘“退出”后主进程与 Worker 均结束。关闭/隐藏桌宠窗口不是退出应用。

本轮未执行上述人工手势或真实付费 Provider；macOS/Linux 可见桌面同样未验证。

## 四、测试与验证

### 核心命令

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q tests/test_workers_protocol.py tests/test_workers_lifecycle.py tests/test_workers_source.py tests/test_agent_link_worker_integration.py tests/test_vision.py tests/test_phase3a_app_shutdown.py tests/test_proactive_worker_app_shutdown.py
$workerTests = Get-ChildItem tests/test_proactive_worker_*.py | ForEach-Object { $_.FullName }
python -m pytest -q @workerTests tests/test_proactive_watcher_worker.py tests/test_single_process_shared.py
python -m pytest -q -rs
python -m ruff check pet tests scripts
python -m ruff format --check pet tests scripts
python -m mypy pet/workers pet/proactive.py pet/agent_link.py
python scripts/check_docs.py
python -m pytest -q tests/test_pr_report_discipline.py tests/test_desktop_pet_features.py
```

| 验证 | 结果/证据 | 状态 |
|---|---|---|
| 上述 Worker/Agent Link 适配/vision/真实 Core 退出命令 | 50 passed, 1 skipped，15.15s | 通过 |
| 上述 Phase 3B 全部新增测试 + watcher + shared 命令 | 62 passed，15.68s | 通过 |
| frozen 验证脚本源码自测 | 2 passed，0.90s；先缺文件为红，再实现为绿 | 通过 |
| 最终完整套件 | **3080 passed, 11 skipped, 14 warnings，250.95s，exit 0** | 通过 |
| Ruff lint / format | 全仓目标通过；370 files already formatted | 通过 |
| mypy Worker/策略/Agent Link 边界 | Success: no issues found in 11 source files | 通过 |
| Markdown 链接 | 100 个 Markdown 文件通过 | 通过 |
| PR 报告纪律 + 产品文案/桌面功能测试 | 130 passed，11.45s | 通过 |
| 本机 webm-chat 构建、Qt DLL 链、3A/3B frozen smoke | 退出码 0 | 通过 |
| 自动/手动真实进程传输、凭据与取消 | OS 截图边界替换为生成图片，真实 QProcess + 本地 HTTP；没有使用远程模型 | 通过 |
| 实际 Windows 截图、远程视觉质量、托盘人工交互 | 上述焦点探针未达条件；远程/手势未执行 | 待人工验收 |

首次全量为 `1 failed, 3079 passed, 11 skipped, 14 warnings`：新增设计文档中展示分支名触发产品文案扫描。
分类为**本轮新增文档元数据位置问题**，不是运行时失败；将分支元数据保留在 PR 证据报告，设计文档链接报告。
未放宽测试或删真实证据。专项产品文案扫描转绿后重新执行整个套件，得到上述最终全绿结果。

11 个 skip 均有原因：Windows symlink 权限 1、可选 GIF 素材 1、POSIX socket 平台 1、offscreen 真实窗口 2、
macOS/AppKit 2、Windows 无 tzset 1、Cocoa plugin 1、offscreen 真实截图 1、未显式启用声卡 smoke 1。
14 个 warning 为已有 QImage.mirrored/QHoverEvent 弃用提示，类型与此前 Phase 3A 记录一致；不能把 skip 当作对应平台/硬件已验收。

真实进程回归覆盖了：双通道请求、每次 HTTP 重试向 Core 申请额度、手动不占自动额度、独立视觉 Key、
HTTP 等待期间 heartbeat、取消/过期结果、帧 TTL、无 Qt/keyring/GUI 反向导入、异常/fallback、共享窗口 owner 路由和退出。
保护文件与构建产物检查在交付前执行：自动更新、演示 HTML 无差异，dist/.scratch 不纳入修改清单。

## 五、限制与回滚

- 当前是官方内置 Worker，不能单独复制到角色 content 目录安装，也没有独立卸载 UI；关闭功能不等于删除源码。
- 自动开关只控制自动通道，手动请求仍可启动；内部 `disabled` 同时拒绝两者，不新增用户设置页字段。
- 能力握手不是 OS 沙箱。API Key 只由 Core 为一次请求下发，不进 config_push/日志/磁盘；Python 清引用不等于对内存/交换文件安全擦除。
- 网络取消是协作式；底层 HTTP 可能继续到超时，过期结果不得展示，退出期限到达后由宿主终止进程。
- 未验证真实远程代理/VPN/Provider 端到端、其他构建 variant 和三平台可见桌面；不做性能/内存提升宣传。
- 本地自动化已通过，但上述人工/环境门未完成，因此 Phase 3B **尚不作完整稳定性封存**，不直接推进新的网络能力迁移。
- 保留旧 `in_process` 实现用于显式回滚；正式宿主目前传 `auto`。当前没有持久化运行模式开关，不指导用户编辑不存在的配置键。
- 本轮无 commit/push，不改写历史。后续可按协议、Worker、Core/手动接入、验证文档分主题提交；回滚用对应 `git revert`，不 reset/覆盖用户工作。
- `pet/updater.py`、`pet/update_settings.py`、`plugin-roadmap-demo.html`、历史归档文档未修改。

## 六、面向使用者的结果

界面和操作维持原样，后台多了由 Core 管理的独立识屏进程。
Core 决定“允不允许做、结果能不能显示”，Worker 执行“查询窗口、截图、请求视觉服务”。
它通过本机 stdin/stdout 管道连接 Core，不需要手动启动，不是与 Core 断开的独立应用，也不是本轮可安装/卸载的 DLC。
关闭自动识屏且不点手动入口时，不应启动该 Worker；即使它故障，桌宠的基础窗口和交互仍应继续工作。
