# Phase 4A 通用平台查询与兼容适配实施报告

> 日期：2026-09-27；分支：`codex/phase3-worker`。基准提交：`b97112d2a65d1af1a8d3396875afac0cdb984337`，开始时工作树已有修改，不能把整个 `git diff HEAD` 当作本轮工作量。**未提交、未推送、未重建。**
> 完成的仅是平台查询接缝，不是完整屏幕理解拆包或最小 Core 安装包验收。

导航：[文档索引](INDEX.md) · [Phase 4 入口](plugin-phase-04-updates/README.md) · [最小拆包设计](plugin-phase-04-updates/PHASE4A-SCREEN-PACKAGE-DESIGN.md) · [原只读审计](plugin-phase-04-updates/PHASE4A-SCREEN-DELIVERY-AUDIT.md)

## 1. 修改文件说明

### 1.1 范围与兼容合同

- 新 `DesktopQueryPort`/`QueryResult[T]`/`ForegroundInfo` 仅依赖标准库与系统 API；导入不查询系统或启动线程，前台/光标/idle 的失败、不支持状态明确区分。
- 前台矩形保持 `(x, y, width, height)` 和负坐标，仍过滤不可见/最小化/cloaked 窗口，优先 DWM，回退 User32；进程句柄由后端关闭。无法读取进程名不抹掉有效窗口信息。
- `vision` 旧入口保留字段、字符串、默认值、`user32` 注入和光标常量；只委托查询，不改变截图、视觉请求和自动/手动策略。保留原 idle 算法，不夹带 tick 回绕修正。
- 普通/共享全屏与光标监视、设置前台白名单查询直接使用平台模块。信号、文案、配置、判断规则与轮询频率不变；不增加设置打开时的截图或网络。
- 异常转成固定原因码；诊断不含原始异常、标题/完整进程路径。成功 DTO 的标题仍供原功能使用，不作为日志输出。

### 1.2 逐文件增量

`+/-` 为**本轮开始快照 → 当前文件**的 `git diff --no-index --numstat` 结果。既有设计文档、前台测试仍显示 Git 未跟踪，但不是本轮从零创建；不将其原有内容算入本轮增量。未移动、删除文件。

<!-- TASK_NUMSTAT_START -->
| 文件 | 新增 | 删除 | 修改与理由 |
|---|---:|---:|---|
| `pet/desktop_query.py` | +202 | -0 | 新增纯平台查询接口、不可变结果及 Windows 后端，明确不支持/失败，避免基础查询加载识屏。 |
| `pet/vision.py` | +18 | -152 | 将旧查询函数改为薄委托并保留常量/注入兼容；截图、编码与网络代码不动。 |
| `pet/platform_win.py` | +13 | -12 | 基础全屏检测直接使用前台 DTO，保持原全屏规则。 |
| `pet/window_screen.py` | +4 | -3 | 普通窗口光标监视使用查询端口，保留信号及轮询。 |
| `pet/multi_window_shared.py` | +3 | -3 | 共享监视不再导入 vision，避免只修普通窗口。 |
| `pet/modern_settings_dialog.py` | +5 | -5 | 白名单添加使用前台 DTO，保留交互/文案且不触发识屏。 |
| `tests/desktop_query_fakes.py` | +113 | -0 | 提取可设置 ctypes 签名的 WinAPI 替身，保留真实结构与句柄检查。 |
| `tests/desktop_query_core_probe.py` | +154 | -0 | 新增全新子进程探针；阻断 vision，运行真实普通/共享 Qt 窗口、监视及退出。 |
| `tests/test_desktop_query.py` | +259 | -0 | 新增后端、兼容、依赖与真实 Qt 边界回归，不伪造查询最终结果。 |
| `tests/test_desktop_query_consumers.py` | +154 | -0 | 验证全屏、轮询次数/频率和设置白名单消费者行为。 |
| `tests/test_vision_foreground.py` | +3 | -80 | 复用 OS 替身、迁移替换点到新后端，保留既有回归/探针分类断言。 |
| `tests/test_cursor_visibility.py` | +3 | -3 | 瞬态错误替换点移到通用端口，不放宽重试/信号断言。 |
| `docs/plugin-phase-04-updates/PHASE4A-SCREEN-PACKAGE-DESIGN.md` | +16 | -8 | 只更新查询切片的真实状态与边界，其他接口和拆包仍为计划。 |
| `docs/plugin-phase-04-updates/README.md` | +2 | -2 | 登记第一步完成及报告，保留后续配置/构建等未完成状态。 |
| `docs/INDEX.md` | +4 | -3 | 登记新报告并同步查询切片状态，不移动历史证据。 |
| `LOG.md` | +13 | -0 | 追加本轮执行、验证、性能和限制记录，保留原日志。 |
| `LOG-INDEX.md` | +1 | -0 | 增加本轮记录入口。 |
| `docs/PR-REPORT-DESKTOP-QUERY-2026-09-27.md` | +184 | -0 | 新增本轮逐文件、性能、真实查询、测试和保护证据。 |
<!-- TASK_NUMSTAT_END -->

新增文件：`pet/desktop_query.py`、`tests/desktop_query_fakes.py`、`tests/desktop_query_core_probe.py`、`tests/test_desktop_query.py`、`tests/test_desktop_query_consumers.py`、本报告。其他均在本轮基线上局部调整。

## 2. 性能分析

### 2.1 环境、命令与样本

本机 Windows / Python **3.11.1**，同一解释器/机器，提取前后各查询 **20 次预热 + 500 次计时**，另做各 **500 次** `tracemalloc` 分配采样，采样前后 GC，不与计时混跑。沿用旧 `vision` API 测量兼容通道，无截图、模型调用、抢焦点或长期 soak。

实测命令：`python .scratch/phase4a-desktop-query/measure.py`，前后分别运行，原始结果留在本地 `perf-before.json` / `perf-after.json`。以下代码可在仓库根目录的 Python 会话复现；命令、方法和表格进入版本控制，不以忽略目录为唯一证据。

```python
import gc, json, statistics, sys, time, tracemalloc
from pet import vision
result = {"python": sys.version.split()[0], "platform": sys.platform, "samples": 500}
for name in ("foreground_window_info", "get_cursor_visibility", "get_system_idle_seconds"):
    query = getattr(vision, name)
    for _ in range(20):
        query()
    times = []
    for _ in range(500):
        start = time.perf_counter_ns()
        query()
        times.append((time.perf_counter_ns() - start) / 1000)
    gc.collect()
    tracemalloc.start()
    before = tracemalloc.get_traced_memory()[0]
    for _ in range(500):
        query()
    gc.collect()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    result[name] = {"median_us": round(statistics.median(times), 1),
                    "p95_us": round(sorted(times)[474], 1),
                    "retained_bytes": current - before, "peak_bytes": peak - before}
print(json.dumps(result, indent=2))
```

| 旧接口（兼容通道） | 前 median / P95（µs） | 后 median / P95（µs） | 前后 GC 后留存（B） | 前后分配峰值（B） |
|---|---:|---:|---:|---:|
| `foreground_window_info` | 44.9 / 127.2 | 36.1 / 99.4 | 2752 → 2752 | 8877 → 9007 |
| `get_cursor_visibility` | 1.8 / 2.0 | 2.6 / 2.9 | 160 → 160 | 632 → 752 |
| `get_system_idle_seconds` | 6.2 / 8.0 | 1.2 / 1.4 | 4656 → 32 | 311848 → 568 |

### 2.2 开销与解释边界

- 光标兼容调用增加结果对象和映射，此样本 median 增加 **0.8 µs**；前台峰值增加 **130 B**，不以“可忽略”代替记录。
- idle 结构由每次调用声明改为模块级类型，算法/系统调用保持原样，本样本临时分配减少。前台耗时受真实桌面/调度影响，不能保证普遍提速。
- 这是 Python 分配采样，**不是 RSS**，不证明长期内存无增长；未新增线程/队列/缓存/定时器，不增加长期内存测试。
- 后端仅在调用时执行原组 WinAPI，没有新增网络、磁盘写入、模型请求或后台扫描，兼容层没有重试。
- 确定性消费者测试中，普通/共享监视分别 **40 个循环 → 40 次光标查询、2 次全屏查询、41 次 0.05 秒 wait 参数、2 次句柄关闭**（含退出判断）。固定 API 计数验证拆分没有增加查询；替代等待边界而非真实 sleep 猜时序。
- 当前未构建，不由源码或微基准推算 Core/DLC 包体缩减。

## 3. 实机运行记录

### 3.1 真实前台探针（与确定性套件分开）

命令：`python scripts/verify_foreground_window.py`。实际输出：

```text
{"attempts": 1, "exit_code": 0, "reason": "foreground_verified"}
returncode=0
```

脚本属于前轮已有改动，本轮未改。真实调用 Windows 查询并验证稳定有效窗口，不输出标题/完整路径、不抢焦点、不截图或联网。`0` 才是通过；`2` 为环境前提不满足、不算通过；`1` 为前提满足下的错误，不能降为跳过。

### 3.2 真实 Qt / 子进程依赖验证

命令：`python -m pytest -q tests/test_desktop_query.py -k real_core_queries`（`QT_QPA_PLATFORM=offscreen`）。普通/共享各一个全新子进程，在导入 `pet.app` 前阻断 `pet.vision`；隔离 Config，使用真实 `QApplication`、`MovieLibrary`/`PetWindow`、监视线程、Qt 信号和事件循环，仅用真实 ctypes 结构的 WinAPI 替身隔离桌面不确定性。

两种模式均通过。断言：普通 1 窗/共享 2 窗，前台/光标查询发生，信号投递，句柄关闭，无识屏 watcher，**Worker 启动次数 0**，无运行中 QProcess，退出后监视线程结束，`pet.vision` 未加载。另测纯查询模块不导入 Qt/Pillow/聊天/配置/平台窗口，不在导入时查询系统。

这是真实 Qt offscreen 边界验证，**不是完整 `PetApp.start()`、可见桌面交互、最小冻结包或托盘自然退出的证据**。本轮没有再次触发真实识屏。

### 3.3 用户可见行为与限制

用户已确认手动“看看屏幕”正常；自动识屏未继续等待、未验收，不判为故障。停用/托盘退出人工验收仍待完成。本轮仅查询，无新增 UI、安装路径或卸载操作；手动识屏/聊天联动既有自动化回归通过，不伪造新增人工确认。

## 4. 测试与验收

### 4.1 先失败、后实现

- 提取前旧前台/光标专项 **56 passed**；新测试先因缺少 `pet.desktop_query` 失败。
- 提取前独立进程阻断 `pet.vision` 后，平台窗口/共享窗口/应用入口因依赖它失败。新回归不只查源码字符串/导入，还执行真实窗口与监视路径。
- 旧前台/光标有效断言保留，只迁移 OS 替换点；新增 54 个用例覆盖状态、错误、句柄、兼容委托、导入与消费者，不新增 skip/xfail 规避桌面问题。
- 首次跨域回归发现两项本轮问题：移除 `vision.Path` 导入影响文件解释；设置文件多一行超过 2395 行预算。恢复原导入、移除冗余局部变量，未改业务/测试上限；随后重跑专项及全量通过。分类为**新引入且已修复**，不是环境问题。

### 4.2 实际结果与命令

| 门 | 实际结果 |
|---|---|
| 查询/兼容/消费者/既有前台和光标专项 | **110 passed，8.04s** |
| Qt/多窗口、识屏、Worker、架构/文案相关域 | **435 passed、1 skipped，44.22s** |
| 实现完成后全量 `python -m pytest -q` | **3177 passed、11 skipped、13 warnings，308.84s** |
| 真实前台探针 | **0 / foreground_verified** |
| 静态、类型、文档及保护门 | 见下方最终复核 |

全量日志保留在本地 `.scratch/phase4a-desktop-query/full-tests.txt`。比前轮 3123 个通过用例新增 **54**；11 个 skip 未增加，13 条 warning 为现有 Qt `QImage.mirrored`/`QHoverEvent` 弃用提示。本报告在全量后新增，会使报告纪律参数化用例多 2 个；文档门单独补跑，不将其计作已重跑的全量。

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q tests/test_desktop_query.py tests/test_desktop_query_consumers.py tests/test_vision_foreground.py tests/test_cursor_visibility.py
python -m pytest -q
python -m ruff check pet tests scripts
python -m ruff format --check pet tests scripts
python -m mypy pet/desktop_query.py
python scripts/check_docs.py
python -m pytest -q tests/test_pr_report_discipline.py
& 'D:\DELL\Git\cmd\git.exe' diff --check
```

<!-- FINAL_CHECKS_START -->
| 最终复核 | 结果 |
|---|---|
| `python -m ruff check pet tests scripts` | All checks passed |
| `python -m ruff format --check pet tests scripts` | 377 files already formatted |
| `python -m mypy pet/desktop_query.py` | Success，1 source file |
| 6 个受影响生产模块 `py_compile` | 退出码 0 |
| `python scripts/check_docs.py` | 107 files scanned，通过 |
| `python -m pytest -q tests/test_pr_report_discipline.py` | 37 passed，0.67s |
| 产品文案扫描专项 | 1 passed、94 deselected，0.70s |
| `git diff --check` | 退出码 0；只有仓库原有换行转换提示 |
| 852 文件 SHA-256 基线 + Git 可见新增文件对比 | 12 个既有文件局部修改 + 6 个新增；共 18 个，范围外变化为 0 |
| HEAD / 暂存区 | 仍为上述基准提交；暂存区为空；无提交或推送 |

保护复核：`pet/updater.py`、`pet/update_settings.py`、`plugin-roadmap-demo.html` 与本轮前一致；既有 `SPEC.md`、Phase 1/3/4 其他文档、`tests/test_proactive.py`、`scripts/verify_foreground_window.py` 与原审计等未被覆盖。文档在全量通过后收尾，仅补跑静态/文档/文案门，不声称再次执行了全量。
<!-- FINAL_CHECKS_END -->

## 5. 保护、回滚与下一步

- 不改 Worker 协议/生命周期、截图与网络执行、配置 schema、自动更新、打包或演示 HTML；不读取密钥、不提交推送。此前未提交的审计、设计、测试、脚本与文档修改保留。
- 对照本轮 852 文件哈希基线及逐文件快照；快照仅为证据，不能整目录恢复覆盖用户工作。允许范围为上述 18 个增量文件，生成缓存/日志不入 Git。
- 当前无提交；后续只暂存本轮增量，尤其不将既有未跟踪文件的原内容混入。撤回须经确认反向应用本轮增量，不用 `reset`/覆盖快照；形成独立提交后可 `git revert`。
- 后续是独立视觉配置/凭据与确认迁移，再做 owner 贡献、功能迁移、独立构建。基础查询解耦只清除一条依赖，不满足完整功能可拔除终门。
- 非 Windows 原生查询尚未实现；旧入口仍按原语义降级，新接口明确 `unsupported`。Windows 探针不代表三平台实机。

## 6. 面向使用者的结果

操作与安装方式不变，内部由“基础桌宠 → 识屏实现 → 系统查询”改成“基础桌宠 → 通用查询”，识屏旧入口也转到相同后端。基础桌宠不必为全屏/光标/前台信息启动识屏 Worker。

没有新 DLC、安装目录或卸载按钮；识屏仍是内置能力，聊天文字联动保留。完整选装/卸载继续等待配置、贡献、构建及事务步骤，自动识屏人工验收也不因本切片变绿而改写。
