# Phase 3B：稳定性收尾记录与未关闭验收门

> 日期：2026-09-27。**实现检查点可以保存，但尚不能宣布全部稳定性封存通过。**
> 组合回归、重新构建、Qt DLL 链和 Worker smoke 通过；本轮全量有 1 项真实桌面环境相关失败，人工细项仍待确认。
> 原始实现证据：[2026-09-26 PR 报告](../PR-REPORT-PLUGIN-PHASE3B-2026-09-26.md)；当前设计：[3B 设计](PHASE3B-PROACTIVE-SCREEN-DESIGN.md)。本文补充后续证据，不覆写原报告。

> **2026-09-27 后续更新：**以下 §1–§9 保留上一轮收尾时的原始失败和待确认记录，最新结果以 [§10](#10-前台窗口测试边界修复与验收澄清) 为准。本次测试边界修复后全量为 **3123 passed、11 skipped、13 warnings**。用户只确认手动“看看屏幕”正常；自动识屏未等待验收，不判定为故障；停用/托盘退出未测试。**尚不能宣布 Phase 3B 人工验收全部完成。**

## 1. 范围与基线

本轮只复验已有实现、核对构建来源、补充证据和进行 [Phase 3C 风险评估](PHASE3C-ISOLATION-ASSESSMENT.md)。未修改生产代码、测试、Worker 协议或构建逻辑，也未新增功能、长期 soak、用户屏幕截图或真实模型/账户请求。

复验起点 HEAD 为 `983461299a29bf380f1e93ccb96c6ed6ee21f5b9`，叠加当时尚未提交的 Phase 3B 实现与路线文档。执行前保存文件清单、原文和 SHA-256，源码/测试与构建输入在复验期间保持不变。已有路线修订与实现分别提交；本次只做本地检查点，不推送。

环境：Windows 内核版本 `10.0.26100`，Python 3.11.1，PySide6 6.11.1，PyInstaller 6.20.0，pytest 8.3.4，Ruff 0.16.6，mypy 2.3.1。Qt 测试使用 `QT_QPA_PLATFORM=offscreen`；构建窗口 smoke 不等于人工桌面验收。

## 2. 最小稳定性门

| 门 | 本次证据 | 结论 |
|---|---|---|
| 自动/手动识屏正常结果 | 用户曾反馈“识屏无误，手动测试没问题”；未明确区分自动触发与手动入口 | 保留用户反馈，具体覆盖待确认，不自行重复调用模型 |
| 停用、取消、generation、超时、fallback | 识屏 adapter / watcher / integration 与组合回归通过 | 自动化通过；不外推成应用内卸载完成 |
| shared 多窗口 | 单 Worker、来源路由、关闭窗口取消相关回归通过 | 自动化通过；不代表所有桌面场景人工验收 |
| Core 优雅退出 | 真实子进程、AppShell、Qt quit 路径及 Worker PID 清理测试通过 | 自动化通过；托盘点击自然退出细项待用户确认 |
| 冻结程序 | 原脚本重新构建 `webm-chat`，Qt DLL 链、3A/3B Worker smoke 均通过 | 本机此变体通过，不覆盖干净机器、其他变体或其他平台 |
| 组合稳定性 | 3 次均 340 passed、1 skipped；无原生崩溃或退出断言失败 | 通过；短程复跑不是长期 soak，也未声称 CPU 打满压测 |
| 最终全量 | 1 failed、3079 passed、11 skipped、13 warnings | **未通过**；定位为既有测试的前台桌面前提未满足，详见第 4 节 |
| 有界性能 | 原 N=3 样本保留；本次 N=1 frozen smoke 补充 | 有记录，不能据并发运行耗时宣称性能改善 |
| 物理可拔除 | 当前仍为内置 Worker，代码随主程序发布 | 不属于 3B 已完成项，移交 Phase 4A/4B |

**出口判断：**尚缺本轮全量复验通过和最小人工细项。可以保存实现检查点、进行 3C 评估及 4A 只读依赖审计；不能用这些后续文档关闭失败门，也不开展新的大规模 Worker 迁移。

## 3. 本次自动化命令与结果

### 3.1 组合短程回归（同一测试进程）

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q `
  tests/test_workers_protocol.py tests/test_workers_lifecycle.py tests/test_workers_source.py `
  tests/test_agent_link_worker_integration.py tests/test_agent_link.py `
  tests/test_agent_link_threads.py tests/test_agent_link_dep_specs.py `
  tests/test_proactive_worker_adapter.py tests/test_proactive_worker_app_shutdown.py `
  tests/test_proactive_worker_integration.py tests/test_proactive_worker_lifecycle.py `
  tests/test_proactive_worker_smoke.py tests/test_proactive_worker_source.py `
  tests/test_proactive_watcher_worker.py tests/test_single_process_shared.py `
  tests/test_phase3a_app_shutdown.py
```

连续执行 3 次，各为 `340 passed, 1 skipped`，用时分别为 **32.45s、35.17s、35.67s**。这些测试覆盖真实 QProcess、关闭、generation、来源路由和 fallback；网络、截图使用确定性边界替身。不得把该结果解释为真实收费模型连续调用或长期稳定性保证。

### 3.2 静态与最终回归

```powershell
python -m ruff check pet tests scripts
python -m ruff format --check pet tests scripts
python -m mypy pet/workers pet/proactive.py pet/agent_link.py
python -m pytest -q
```

- Ruff：`All checks passed!`
- format：`370 files already formatted`
- mypy：`Success: no issues found in 11 source files`
- 全量：**1 failed, 3079 passed, 11 skipped, 13 warnings in 373.48s**。
- 前次计划中的 `3080 passed / 11 skipped / 14 warnings` 是早先复验，不能替代本次结果。
- skip 数量仍为 11，没有新增 skip 或改动过滤规则。warnings 仍为 Qt `QImage.mirrored` / `QHoverEvent` 弃用警告；相较原实现日志少了 `test_try_move_success_still_builds_plan_and_moves` 触发的一次 mirrored 警告，属于路径触发次数差异，未屏蔽任何警告。

## 4. 全量失败分类、根因与后续门

唯一失败：

```text
tests/test_proactive.py::TestVisionAndWatcherPhase2::test_foreground_window_info_real_call_no_shadow_bug
assert info is not None
```

单独复跑同样失败（`1 failed in 0.55s`），排除了仅组合顺序才能出现的假设。随后执行不截图、不记录标题或 PID 的 WinAPI / 函数返回跟踪，取得：

```text
hwnd_nonzero=True
visible=False
minimized=False
rect_ok=True
rect_positive=True
dwm_hresult=0
cloaked=0
result_present=False
return at pet/vision.py:125; exception_type=None
```

[foreground_window_info](../../pet/vision.py) 的现有合同允许“无有效可见前台窗口”返回 `None`；本次在 `IsWindowVisible=False` 分支正常返回，没有该回归原本防范的 `UnboundLocalError`。[旧测试](../../tests/test_proactive.py) 则假定 Windows 测试会话始终存在可见前台窗口。这两个文件本轮和待封存实现均未修改。

**分类：环境前提未满足 + 既有测试对桌面状态的依赖。**当前证据不支持归因于新 Worker；同样不能因为原因已知就把失败算作通过。

本轮不改生产代码、不加 skip、不放宽断言来凑全绿。下一次先确认真实桌面有可见前台窗口，再运行该单项和完整套件。若需永久消除环境脆弱性，应另开窄范围测试边界任务：将原 shadow-bug 回归做成确定性的 WinAPI 边界测试，将真实桌面探针独立保留；不得借本次收尾悄悄变更验收标准。

## 5. 构建来源与冻结验证

### 5.1 重新构建，而非仅复用旧产物

因旧产物不足以证明对应关系，本次使用原 onedir 流程重新构建：

```powershell
& 'D:\DELL\Powershell\7\pwsh.exe' -NoProfile -ExecutionPolicy Bypass `
  -File scripts/build_onedir.ps1 -Variant webm-chat -SkipZip
```

执行包装层仅将 APPDATA、LOCALAPPDATA、TEMP、TMP 指向仓库内隔离目录，并要求后台 Start-Process 隐藏启动；未修改 spec、构建脚本、产品参数或用户配置。源码、资源及构建输入共 **468** 个文件在构建前后哈希一致。

- 输入集合摘要：`bf670d9fd615787a213278548cdf0d46e3c31e14e6d59905c54ae30221029aac`。
- 算法：按 POSIX 相对路径排序，每项 `相对路径 + NUL + 文件 SHA-256 + LF`，再计算 SHA-256。
- 产物：`dist-onedir/dsh-pet-standalone-webm-chat/dsh-pet-standalone-webm-chat.exe`。
- EXE 大小：**17,471,369 bytes**（只是启动 EXE，不是完整 onedir 包体）。
- EXE SHA-256：`25c161b0d9e0fbd3d24eecf7f27545fb408fd18355ef0f5211ba47315f11a639`。
- 构建原窗口 smoke：主窗口 HWND 在 3.4s 出现、设置窗口在 1.8s 出现；该路径最后强制清理测试进程，**不是托盘自然退出证据**。

这证明当前本地构建与待提交实现输入对应，不声称可按位复现、签名发布、全包依赖完全剥离或干净机器兼容。完整输入逐项清单保存在本机 `.scratch/phase3bc-closeout-2026-09-27/build-provenance.json`；关键结果在此持久记录，不只引用被忽略的临时日志。

### 5.2 DLL 与两个 Worker smoke

```powershell
python scripts/verify_bundle_qt.py --internal dist-onedir/dsh-pet-standalone-webm-chat/_internal
python scripts/verify_phase3a_frozen_worker.py dist-onedir/dsh-pet-standalone-webm-chat/dsh-pet-standalone-webm-chat.exe
python scripts/verify_phase3b_frozen_worker.py dist-onedir/dsh-pet-standalone-webm-chat/dsh-pet-standalone-webm-chat.exe
```

- Qt：Shiboken、QtCore、QtGui、QtWidgets 均 `OK`，最终 `ALL OK`；ICU 来自 `C:\WINDOWS\SYSTEM32\icuuc.dll`。
- 3A：`hello → ready → shutdown_sent → FROZEN_WORKER_SMOKE_OK returncode=0`。
- 3B：`hello/capabilities → ready → observe_foreground response → shutdown → returncode=0`。
- 3B 此处只读取前台元数据，不截图、不请求视觉模型；返回无有效窗口也属于合法观察结果。
- Worker 使用独立入口；无 UI 导入边界由现有启动隔离测试验证，不能只凭握手成功推断所有导入。Core 窗口由前述独立构建 smoke 验证。

## 6. 性能与资源证据的边界

原报告 N=3：源码 Worker 启动 **82.86–92.70ms**、RSS **24.50–24.75MB**；frozen 启动 **341.12–352.59ms**、观察后 RSS **52.99–53.95MB**，约 1 秒空闲样本 CPU 为 0。原 Core + Worker 短程退出样本保留，不追加长期运行测试。

本次冻结验证 N=1：启动 **551.54ms**、观察 **15.72ms**、关闭 **87.67ms**、RSS **53,026,816 bytes**、线程数 **5**，**1.014s** 空闲样本 CPU **0s**。采样与其他测试并行，只记录实测值，不用于前后性能优劣判断，也不能把 1 秒 CPU 为零外推为长期零开销。

本轮只新增文档，未增加生产路径的系统调用、网络、磁盘、线程或常驻内存。识屏原有执行成本、任务槽限制与截图/模型延迟仍以原报告为准；本次没有新的真实截图和模型耗时样本。构建输出与测试日志留在忽略目录，不作为 DLC 发布文件。

## 7. 人工确认与剩余限制

已保留用户原话，不要求重复收费调用。尚需确认最小清单：

1. 原手测是否分别覆盖自动触发和手动“看看屏幕”，结果展示是否正常；忙碌/冷却已有自动化，未单独取得人工确认。
2. 关闭自动识屏后不再自动触发，手动入口仍按原语义工作。
3. 托盘“退出”后桌宠正常结束，对应识屏 Worker 没有残留。

未收到这些细项确认前，本文保持“待确认”。`WM_CLOSE` 隐藏、Qt 定时器调用 quit、构建脚本强制清理，都不能冒充用户托盘自然退出。

封存范围即使后续补齐，也仅限 Windows 当前实现和已验证 `webm-chat` 构建。macOS/Linux 实机、其他变体、干净机器、实际功能包安装/卸载/升级/回滚仍须后续验收。内部 `auto/in_process/disabled` 目前是宿主/测试注入模式，不指导用户修改不存在的设置键。

## 8. 提交、回滚与下一步

实现、官方选装路线、本文与 3C 评估按三个本地主题提交；不推送、不重写已有历史、不改动自动更新或演示 HTML。源码实施报告保留原日期与原结论，不能借提交名称把未通过门改成通过。

| 本地主题 | 提交 / 范围 | 独立校验 |
|---|---|---|
| Phase 3B 实现检查点 | `1d60b89`；25 文件，+3877 / -70；含既有代码、测试、验证脚本、设计与原报告 | 100 份暂存版 Markdown 链接/报告登记通过；暂存实现与本轮验证源码一致 |
| 已有官方选装路线修订 | `2e02067`；21 份文档，+1198 / -746 | 102 份暂存版 Markdown 链接/报告登记通过；不依赖第三个提交的文件 |
| 最新收尾与 3C 评估 | 本报告所在独立文档提交；仅新增两份文档并更新阶段 README、INDEX、LOG、LOG-INDEX | 工作树 104 份 Markdown 链接通过；PR 纪律及产品文案边界合计 36 passed、94 deselected；diff 无空白错误 |

提交拆分通过向 Git 暂存区写入对应文档版本完成，没有以旧快照覆盖工作树。每个提交的链接检查都只允许目标存在于该次暂存树，而非借用后来才加入的文件。`1d60b89` 的“完成”指实现，不指全量/人工门全部通过。所有提交仅在本地，未推送。

回滚使用相应本地提交的 `git revert <commit>`，而不是 reset；只有需要回退实现时才回退实现提交，文档提交可以单独撤回。保留旧进程内回滚接缝；未来真实卸载后不得从 Core 隐式启用 fallback。

后续先关闭第 4、7 节待验收项；[3C 评估](PHASE3C-ISOLATION-ASSESSMENT.md)和 Phase 4A 只读拆包审计可并行，不以继续迁移功能掩盖出口问题。屏幕理解是首个可拔除样板，AI 对话与文件理解是下一主要拆包目标。

## 9. 给使用者的最终效果

桌宠操作本轮不变，也不会多出需要手动管理的窗口。识屏仍通过“Core 决策/授权/展示 → 本机 QProcess + stdin/stdout JSONL → Worker 截图/视觉执行”连接。

当前 Worker 代码在主程序 `pet/workers/` 中，随主程序安装，**不是现在能单独导入或卸载的 DLC**。关闭自动识屏不等于关闭手动入口。真正包目录、菜单/设置按安装状态注册、应用内卸载与重装，由 Phase 4A/4B 落地，Setup/ZIP/便携由 Phase 5A 接续。

你现在获得的是可回滚的本地实现检查点、最新验证事实和剩余清单，而不是未经证实的“全部封存完成”。


## 10. 前台窗口测试边界修复与验收澄清

### 10.1 事实、范围与根因

2026-09-27 后续执行基线为 `b97112d2a65d1af1a8d3396875afac0cdb984337`，工作树起初干净；本轮未暂存、提交或推送。环境沿用 §1，默认 pytest 仍使用 `QT_QPA_PLATFORM=offscreen`。

| 人工项目 | 用户最新说明 / 当前状态 |
|---|---|
| 手动“看看屏幕” | 用户确认正常，不要求为报告重复调用真实模型 |
| 自动识屏 | 用户没有继续等待，尚未验收；没有证据判定是实现故障 |
| 停用、托盘自然退出等 | 用户未测试，继续保留人工门；自动化不能代替 |

§4 的旧失败来自测试要求真实桌面始终存在合格前台窗口，而函数合同允许在不可见等情况下返回 `None`。用户随后复跑旧测试曾得 `1 passed in 0.47s`，这说明测试受环境影响，不能当作边界已经修复。本轮不排查自动识屏、不降低策略阈值，也不改生产函数。

### 10.2 确定性回归与显式探针

- 从 [test_proactive.py](../../tests/test_proactive.py) 移除一项依赖真实可见前台的回归，把其逻辑保护迁入 [test_vision_foreground.py](../../tests/test_vision_foreground.py)。新测试直接执行 `foreground_window_info()`，只替换 WinAPI 和被测模块的平台分支，保留真实 ctypes 类型、结构和缓冲区，不伪造函数结果。
- **21 项函数边界用例**：有效窗口完整元数据、无窗口/不可见/最小化/cloaked、DWM 不可用/失败的 User32 回退、无效矩形、系统异常、进程查询及句柄关闭、非 Windows 行为。成功路径必须得到精确字段，不能用 `None` 也通过的断言。
- **23 项探针分类用例**：稳定成功、环境未就绪、真实函数错误、非法结构、窗口变化最多三次、前后条件变化、隐私输出及退出码。合计 44 项；没有新增 skip/xfail 或改变默认测试过滤。
- [verify_foreground_window.py](../../scripts/verify_foreground_window.py) 仅显式执行，不进入默认 pytest/CI。调用前后独立检查窗口前提；退出码 **0=验证通过、2=环境未就绪（不计验收通过）、1=验证错误（不得降格跳过）**。不抢焦点、不模拟输入、不截图、不联网；仅输出原因码和次数，不输出标题、路径、HWND 或 PID。
- 测试先行记录：新探针文件创建前分类测试因模块不存在报错；补实现后通过。另用内存 AST 变异恢复历史局部 `import ctypes.wintypes` 遮蔽，严格成功断言会失败；该探针未写回 `pet/vision.py`，证明不是把旧回归删掉后放宽断言。

### 10.3 本轮命令与实际结果

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
$env:PYTHONUTF8 = "1"
python -m pytest -q tests/test_vision_foreground.py tests/test_proactive.py tests/test_vision.py `
  tests/test_proactive_worker_adapter.py tests/test_proactive_worker_app_shutdown.py `
  tests/test_proactive_worker_integration.py tests/test_proactive_worker_lifecycle.py `
  tests/test_proactive_worker_smoke.py tests/test_proactive_worker_source.py `
  tests/test_proactive_watcher_worker.py tests/test_workers_protocol.py
python -m pytest -q
python -m ruff check pet tests scripts
python -m ruff format --check pet tests scripts
python -m py_compile scripts/verify_foreground_window.py tests/test_vision_foreground.py
python scripts/check_docs.py
python -m pytest -q tests/test_pr_report_discipline.py tests/test_desktop_pet_features.py
& 'D:\DELL\Git\cmd\git.exe' diff --check
# 真实桌面探针单独执行，不混入上述确定性通过数量
python scripts/verify_foreground_window.py
```

| 检查 | 实际结果 | 证据含义 |
|---|---|---|
| 相关专项 | **174 passed、1 skipped，36.62s** | 真实函数 OS 边界及主动识屏/Worker 回归通过 |
| 全量 | **3123 passed、11 skipped、13 warnings，756.38s** | 无失败，无 Qt 原生崩溃；不是长期 Worker soak |
| Ruff / format | `All checks passed!`；`372 files already formatted` | 比原记录增加新脚本及新测试 2 个 Python 文件 |
| 编译 | 两个新增 Python 文件通过 | 未修改生产代码 |
| 文档链接 | **105 份 Markdown 检查通过** | 新审计已登记，新增链接可解析 |
| PR 纪律 / 产品文案边界 | **130 passed，38.67s** | 对文档及扫描边界复验，不改写旧报告规避检查 |
| Git / 保护检查 | `diff --check` 通过；核对 848 个原受控文件哈希，仅 7 个范围内文件变化，另新增 3 个文件 | 暂存区为空，HEAD 仍为 `b97112d`；生产、打包、更新及 HTML 均未改变，无提交/推送 |
| 独立真实桌面探针（N=1） | `exit_code=0`、`reason=foreground_verified`、`attempts=1`，进程退出码 0，用时 **0.5724s** | 本次有稳定合格前台，真实函数和结构通过；没有截图或模型调用，不等于自动识屏或托盘验收 |

测试总数由旧记录的 `1 failed + 3079 passed + 11 skipped = 3091` 增加至 `3123 passed + 11 skipped = 3134`：删除 1 个环境依赖测试、增加 44 个确定性用例，净增 43。skip 仍为 11；warnings 仍为 13，来自 `QImage.mirrored` 与 `QHoverEvent` 的既有弃用提示。本次不修这些无关路径，也不把测试耗时变化当作产品性能改善/退化。

本次进程探针耗时是单样本工具运行成本，不是 Core/Worker 性能基线。生产稳态路径、调用频率、网络/磁盘/线程及常驻内存均未修改，因此没有新的产品性能结论；§6 的已有有界样本保持原结论。工具只有显式运行时读取 WinAPI 窗口元数据，不创建后台循环；不新增长期 RSS/CPU 测量。

原始本地输出保存在 `.scratch/foreground-boundary-2026-09-27/`（`focused.log`、`related.log`、`full-suite.log`、`probe-red.log`、`mutation-check.txt`、`desktop-probe.json`）。该目录不提交，关键命令和结果已在本文持久记录，不要求读者仅靠忽略的临时文件判断结论。

### 10.4 Phase 4A 只读审计与构建证据

新增 [屏幕理解交付边界审计](../plugin-phase-04-updates/PHASE4A-SCREEN-DELIVERY-AUDIT.md)，区分已证实事实、建议边界、待验证事项。关键结果：

1. Core 全屏/光标行为复用 `vision.py` 的系统查询，不能整文件搬走；截图/网络/专属策略应与通用平台查询分开。
2. 手动入口、主动菜单、视觉设置、Provider 与凭据仍依赖聊天；已有独立视觉 Key 不等于可以不安装聊天独立交付。
3. 专属 UI、配置、adapter、Worker 与旧 fallback 都须纳入包所有权；当前命令 owner 清理不是完整安装/菜单/设置撤销事务。
4. 现有 `webm-chat` 构建记录的 **468/468 输入哈希匹配，0 项变化**，但 TOC 收集了识屏和聊天，只能当完整包基线，不能证明最小 Core。旧 `webm` TOC 无当前对应保证。

**本轮没有重建、没有重新跑 frozen smoke**；§5 既有产物的时间、哈希和对应关系在审计中注明。未搬代码、删除依赖、实现加载器、冻结包格式/目录或声称 Phase 4A/可卸载样板完成。

### 10.5 文件范围、回滚与剩余门

| 文件 | 本轮改变 / 理由 |
|---|---|
| `tests/test_proactive.py` | 只移出真实前台依赖测试，避免默认回归由当前桌面状态决定 |
| `tests/test_vision_foreground.py`（新增） | 直接执行真实函数、覆盖 OS 边界及探针分类，保留原遮蔽 bug 的严格保护 |
| `scripts/verify_foreground_window.py`（新增） | 保留显式、隐私最小化的真实桌面检查入口 |
| 本收尾报告 | 追加 §10 与最新提示，保留 §1–§9 的失败证据与历史结论 |
| `docs/plugin-phase-04-updates/PHASE4A-SCREEN-DELIVERY-AUDIT.md`（新增） | 为未来真正拆包提供代码/测试/构建依据，不替代实施设计 |
| Phase 3 / Phase 4 `README.md`、`docs/INDEX.md` | 同步最新状态、人工事实与审计导航，不重写阶段路线 |
| `LOG.md`、`LOG-INDEX.md` | 追加本轮事实和入口，保留上一轮失败记录 |

未改 `pet/`、Worker 协议、自动更新、打包配置或演示 HTML；不移动/删除文档，不提交推送。回滚时只撤回上述本轮测试/工具/文档增量，不 `reset` 覆盖其他改动；未来需要提交时显式暂存本轮文件形成独立回滚点。

**当前结论：默认全量回归门已通过；自动识屏、停用、托盘退出人工门仍未完成，不能宣布 Phase 3B 全部封存。** 后续可按审计设计 Phase 4A 最小接缝，不启动大规模搬迁或用进程数量冒充物理可拔除。

对使用者：桌宠触发条件、操作和安装目录完全不变。现在常规测试不依赖恰好在前台的窗口；需要桌面证据时另跑显式探针。当前识屏仍为内置 Worker，用本机管道与 Core 连接，没有新增安装/卸载 DLC 功能。
