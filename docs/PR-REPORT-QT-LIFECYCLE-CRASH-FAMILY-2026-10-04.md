# PR 报告：Qt 生命周期崩溃家族根治（frameseq 在途交付 UAF + 配图加载线程堆）

> **基线**：`beaa8f5`（上游 main，含 #215 + #223）
> **分支**：`fix/qt-lifecycle-crash-family`　**日期**：2026-10-04
> **范围**：3 个文件（产品 2、测试 1）+ 4 个 workflow（还原隔离）
> **关联**：[`PR-REPORT-FULL-REVIEW-FIXES-2026-10-03.md`](PR-REPORT-FULL-REVIEW-FIXES-2026-10-03.md)（大审批次，本报告的退役制/登记册修复即出自该轮）；fork 诊断分支 `diag/crash-family-probe`（17 轮二分实验全记录）

## 一、核心特性

修复测试套件长期存在的**漂移型原生段错误家族**（套件 ~28%/~81% 处间歇/确定性崩溃、进程退出时崩溃、崩点随套件进度漂移），摘掉了为它设的两道 CI 隔离（macOS 三文件拆进程 + `test_retained_frame_is_not_reused_as_frame_zero` 单条 deselect），全平台恢复同口径门禁。

**根因**（macOS 系统崩溃报告原生栈实锤，不再是推断）：`QThread::exec → sendPostedEvents → QObject::event → PySide qtPythonMetacall → KERN_INVALID_ADDRESS at 0x8`——共享预取线程把在途的 queued 交付投递给**已被 GC 析构**的 clip/worker（Python 槽的包装对象死了，C++ 交付撞尸）。交付何时撞上析构纯看时序：所以崩点漂移、mac（慢 runner，时序窗宽）必现、Windows 偶发。

| # | 层 | 修复 |
|---|---|---|
| 1 | 预取交付撞尸（主雷） | `FrameSeqClip` 与 `_PrefetchWorker` 进程级强钉（`_LIVE_CLIPS`/`_LIVE_WORKERS`）：丢弃未收口的对象存活到进程退出，在途交付安全落地。生产里 clip 本就由库缓存常驻，钉表零增量 |
| 2 | 配图加载并发首用 Qt 图片插件（dump 里每个崩点都有复数加载线程在场） | 自言自语配图加载从「每批一条守护线程」改为**单 worker 队列**：串行 = 无并发首用 + 无线程堆积 |
| 3 | 锁积压饿死活预热（2 的锁版修复在 CI 上把预热拖到超时） | 队列化 + 换代戳复查 + **收口时无条件作废在飞批次**（含未 start 的壳） |
| 4 | 同池重复解码（测试 churn + 生产 3 宠各解一遍同一图池） | 配图缓存进程级共享（按绝对路径；长边变化整表重建）——落地 docstring 声称已久的「按绝对路径共享」语义 |

**红线 / 不变量**：预热语义（代次作废、按绘制尺寸预缩放、内存口径）不变；钉表只兜「丢弃未收口」路径，正常 `close()`/库收口语义不变。

## 二、修改文件说明

`git diff --numstat origin/main`：

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `pet/frameseq_clip.py` | +20/−0 | `_LIVE_CLIPS`/`_LIVE_WORKERS` 强钉表 + 注释（根因修复） |
| `pet/overlay_shell.py` | +109/−29 | 配图加载：线程/批 → 单 worker 队列 + 共享缓存 + 收口无条件作废 + 解码串行锁（`self_talk_image_cache_edge` 语义不变） |
| `tests/test_frameseq_clip.py` | +58/−2 | 新增 `test_dropped_without_close_stays_pinned_for_inflight_delivery`（钉表回归）；`test_retained_frame_is_not_reused_as_frame_zero` 两条时序敏感断言改交付序/帧 0 口径（慢 runner 抖动红修复）；`decode_probe` 计数收窄到本用例目录（钉表后历史 clip 的在途解码不再污染计数） |

### CI 还原（4 个 workflow）

| 文件 | 改动 |
|---|---|
| `pr-test.yml` / `build-macos.yml` | 摘除 macOS 三文件拆进程隔离（主套件恢复同口径）；隔离位注释更新为根因结论 |
| 全部 4 份 | 摘除 `--deselect tests/test_frameseq_clip.py::test_retained_frame_is_not_reused_as_frame_zero`（肇事单条根治后回归主套件） |

## 三、实现要点

- **强钉而不是 deleteLater**：该子系统有跨线程销毁 AV 前科（`frameseq_clip.py` 头部注释）；「对象存活到进程退出」把交付/析构竞态从结构上消掉，代价是每 clip 两个小 QObject 常驻（有界，生产里本就常驻）。
- **队列化取代锁**：先试过全局解码锁（v9 解了并发崩溃、v10 引来锁积压饿死预热超时），单 worker 队列同时满足「无并发」与「无雪崩」两个约束——这是被两轮 CI 失败逼出来的形态。
- **诊断方法论**：fork 诊断分支（不进上游）+ mac 系统 `.ips` 崩溃报告抓取拿到原生栈，是这轮从「猜」变「实锤」的关键仪器。

## 四、性能分析

- 强钉：零热路径成本（构造时一次 append）；内存 = 每 clip 两个小 QObject（生产 106 段 × 3 库 ≈ 318 个，KB 级）。
- 配图队列化：生产行为不变（单壳单池一次预热）；**生产收益** = 多宠同池只解码一次（3 宠省 2/3 配图解码 CPU/IO）。
- 共享缓存：多宠下配图内存从 3 份减到 1 份（池级 ~36.8MB 口径时省 ~24MB 级——按既有实测口径外推，未单独实测）。
- 无新增线程（反而少了"每批一条"）、无新系统调用、无网络/磁盘新增。

## 五、实机运行记录

- **原生栈证据**：macOS runner `DiagnosticReports/*.ips` 抓取（fork 诊断轮 v11），崩溃线程栈见 §一；抓取脚本 `diag-quarters/dump_crash.py`（诊断分支留档）。
- **二分实验**（fork `diag/crash-family-probe`，17 轮）：单文件绿 → 三文件同池必崩 → 四分臂 Q2 假红（时序断言）→ 两半臂 H2 崩 → Q3 对半两臂皆崩（非单文件埋雷）→ stream 相邻区定位 → 配图锁 v9（trio 转绿）→ 锁积压新伤（v10 超时）→ 队列化 + 无条件作废 v15 **三平台全绿** → v16 mac 仅剩帧 0 计数口径红 → 口径修复 v17 **三平台全绿**（同口径、无隔离）。
- **本地**：钉表版 GC 竞态脚本 3000 轮无崩；崩溃组合（spawn+lifecycle+visibility 三文件）修复前 1/8~2/12 崩、修复后 10/10 绿；全量连跑 3/3 绿（含肇事单条，摘隔离）。
- **CI 两轮全绿**（完成判据）：v15 + v17 两轮，三平台，无 trio 拆分、无 deselect。
- **边界**：崩溃家族是测试进程级现象（生产没有每分钟上百壳的建销）；生产侧对应的残余风险是「进程退出窗口的在途交付/解码」——钉表与队列化已覆盖其主要向量，真实退出路径的原生验证仍建议实机观察一段时间。

## 六、回滚与隔离

若后续出现同族新形态：隔离先例（webm 族独立步骤）仍在；本 PR 的 CI 还原只删「已根治对象的隔离」，其余隔离位（webm 生命周期族、低优预热族）原样保留。
