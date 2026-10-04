# Phase 4B-3 功能包事务：部分实现与安全前提检查报告

> 基线：`codex/phase3-worker` / `bd048d5`（2026-10-03）。
> 状态：**未完成交付**，不提交、不推送。记录连续实施及跨日实机检查；下述性能不是生产安装验收。
> 关联：[设计与验收](plugin-phase-04-updates/PHASE4B-3-LOCAL-TRANSACTIONS-DESIGN.md) · [4B 总设计](plugin-phase-04-updates/PHASE4B-LOCAL-MANAGEMENT-DESIGN.md) · [文档索引](INDEX.md)。

> 历史快照说明：本报告“未完成”和“尚无OS沙箱”均是2026-10-03当时事实，不倒写为已通过。经批准继续实施后的最新状态、双冻结验收与人工/发布边界见[2026-10-04连续收尾报告](PR-REPORT-PHASE4B-MANAGEMENT-CLOSEOUT-2026-10-04.md)。

## 一、核心特性与当前判断

本轮由暂停的 8 failed / 1 passed 初稿恢复实施，先增加回归（11 failed / 1 passed），再重构安全文件操作和 journal 状态机。fresh staging 必须先经公开账本 commit 建立空状态；文件读取引起 atime 更新不是内容变化；已确认事务重试必须与持久 expected_state/intent 一致，不允许较高 revision 被覆盖；未登记的孤立版本不可执行或被新安装收编。

合同测试覆盖目录/ZIP 安装、启停幂等、升级 enabled/disabled、previous/GC、实际租约、回滚、草稿阻塞、部分删除、提交/rename 中断恢复、签名/兼容性失败无执行、硬链接/ZIP 链接/路径/重复/大小写拒绝、Worker hello 协议合同以及无沙箱拒绝。**自检和 startup 摘要在这些测试中由替身提供，不能视为真正 host/Worker/GUI 接入。**

发现需要调整的前提：计划中的“干净子进程”不能保证“无真实用户权限”；本机生成测试文件证明可越出假 HOME 读取。默认实现现在缺少 OS 沙箱就拒绝执行，保留该安全要求，不用环境变量或 Python hooks 冒充隔离。

## 二、修改文件说明

下表为当前基线差异：已跟踪文件使用 `git diff --numstat`，新文件按实际行数记录为 +N/-0（未暂存）；无删除文件，没有暂存或提交构建产物、日志、私钥或真实个人数据。

| 文件 | +行 / -行 | 改了什么 + 为什么 |
|---|---:|---|
| `.gitignore` | +9 / -1 | 修改：只显式开放本任务五份 Markdown，防止中间产物进入提交。 |
| `.scratch/phase4b-local-management/HANDOFF.md` | +41 / -1 | 修改：保存安全前提、实测与精确下一步，防止误接生产。 |
| `.scratch/phase4b-local-management/PLAN.md` | +57 / -1 | 修改：逐门区分部分实现与未完成，保留原计划历史。 |
| `.scratch/phase4b-local-management/STATUS.md` | +50 / -0 | 修改：分开代码/自动化/实机/未提交与未发布状态。 |
| `.scratch/phase4b-local-management/SUMMARY.md` | +141 / -0 | 新增受版本管理记录：跨对话摘要，旧结论明确标历史。 |
| `.scratch/phase4b-local-management/WORKLOG.md` | +109 / -0 | 新增受版本管理记录：失败根因和 red/green 过程，不纳入原始日志。 |
| `docs/INDEX.md` | +2 / -1 | 修改：登记设计及部分实现报告，并放入 PR 报告存档。 |
| `docs/PR-REPORT-FEATURE-PACKAGE-TRANSACTIONS-2026-10-03.md` | +125 / -0 | 新增：逐文件/性能/实机与未通过门证据，避免假交付。 |
| `docs/PROJECT-ENTRY.md` | +4 / -2 | 修改：4B-3 改为部分实现，精确分支移入报告，修复既有文案门失败。 |
| `docs/plugin-phase-04-updates/PHASE4B-3-LOCAL-TRANSACTIONS-DESIGN.md` | +102 / -0 | 新增：原批准设计落盘，补安全前提、证据、限制与待确认方向。 |
| `pet/feature_install_state.py` | +2 / -1 | 修改：仅严格 tx journal 名称与状态收据分流，保留账本安全校验。 |
| `pet/feature_package_files.py` | +256 / -0 | 新增：bounded 复制/哈希/ZIP 与链接路径硬门、安全删除。 |
| `pet/feature_package_probe.py` | +125 / -0 | 新增：强制 OS runner 的 self-check 合同，禁止无沙箱降级。 |
| `pet/feature_package_transactions.py` | +822 / -0 | 新增：Qt-free plan/journal/CAS/状态机、等待、卸载恢复与 GC。 |
| `pet/feature_version_lease.py` | +70 / -51 | 修改：新增持锁 admission guard，复用全部 revision 占用判定。 |
| `tests/test_feature_package_transactions.py` | +660 / -0 | 新增：51 项公有接口、安全失败与中断回归。 |

主要代码职责：

- `pet/feature_package_transactions.py`：Qt 无关合同、随机确认 token 的持久 hash、journal 前后镜像与子操作 CAS、管理/租约锁序、等待占用、不可覆盖版本落盘、保留 active+previous、草稿保护端口、回滚、卸载/恢复/GC；只从 state 选版本。
- `pet/feature_package_files.py`：流式 bounded source hash、目录/ZIP staging、ZIP EOCD 解析前资源门、路径/大小写/链接门、严格子目录删除边界；不用 ContentManager 安装代码。
- `pet/feature_package_probe.py`：verified loader host 合同、bounded Worker hello transcript 合同、必须提供 OS 沙箱的 launcher 端口；移除初稿中的任意 sys.path import 与默认跳过 Worker 行为。
- `pet/feature_install_state.py`：仅排除严格事务 journal 文件名，账本收据仍独立校验，不新增 active 权威。
- `pet/feature_version_lease.py`：提供 management_guard 冻结 admission、所有 revision 占用重查，保留既有公有租约行为。
- `tests/test_feature_package_transactions.py`：公有接口回归、安全失败及故障注入；现有 Qt/跨进程测试族继续运行。
- `.gitignore`：只开放同一任务目录中的五份 Markdown 持续记录；其余临时输出、探针脚本、测试包与日志仍忽略。
- 设计、INDEX、PROJECT-ENTRY 与五份持续记录：保留历史失败，明确已实现/验证/真实未通过/未提交/未发布的区别。

刻意未改：AppShell/ModernSettingsDialog、冻结规格、真实凭据/profile/额度/记忆/聊天历史、既有默认内置功能交付。没有把尚缺失的沙箱和 queued 生命周期接进生产启动。

## 三、性能分析

方法：`python .scratch/phase4b-local-management/benchmark_transactions.py`，Windows native build `10.0.26100`、Python `3.11.1`、20 个逻辑 CPU，E 盘真实 NTFS 文件操作（`Get-Volume -DriveLetter E` 确认 Fixed/NTFS）。脚本及生成输出不入库；测试源为签名 fixture 共 **550,938 bytes**。安装路径明确注入 `StubChecker`，startup confirmation 摘要也由 fixture 提供；因此只能比较事务算法与磁盘成本，**没有测到真实沙箱/Worker/Qt 成本**。

| 路径 | 样本 | 中位数 ms | 最小/最大 ms |
|---|---:|---:|---:|
| fresh preflight（含 bootstrap、copy、hash、签名、journal） | 10 | 144.366 | 133.705 / 224.807 |
| apply（fixture checker，无真实 Worker） | 10 | 225.176 | 215.136 / 257.122 |
| startup 摘要确认（fixture receipt） | 10 | 102.706 | 94.892 / 147.059 |
| inspect（单完成事务，无功能执行） | 100 | 7.235 | 最大 40.781 |

`tracemalloc` 10 次事务采样 Python peak 为 **2,485,033 bytes**。这不是工作集/RSS 或长期泄漏测量；尚未测大包与 journal 长期增长。版本与文件复制/哈希按块执行，新增文件系统调用、锁、签名校验、journal/receipt 写入由用户操作触发；服务不启动周期轮询、不创建常驻线程、不发网络请求。未调用时没有新增 steady-state 热路径；`inspect` 的成本随收据/journal 数增长，必须在后续多事务长跑实测，而不能用“可忽略”替代数字。

默认无沙箱 apply 被拒绝，其成功安装成本不适用；OS runner、生产加载和 Qt adapter 的性能门仍未完成。

## 四、实机运行记录

### 4.1 同权限子进程的隔离失败证据

同一 benchmark 脚本只生成 `outside-fake-home.txt`（内容为 `GENERATED-FIXTURE-NOT-A-SECRET`）；以 `python -I`、空白最小环境、fake HOME/USERPROFILE/APPDATA、fake cwd 启动 child，再读取该生成文件。真实输出：

```text
same_user_isolation_probe: OUTSIDE_HOME_READ=True
default_without_sandbox: status=rejected
reason=self_check_sandbox_unavailable
state=uninstalled
```

没有读取真实用户目录、截图或模型。该结果证实“空环境/独立进程”不足以证明文件权限隔离；原生 Worker 更不能仅靠 Python 约束。建议保留安全前提，另行确认 Windows OS 沙箱/专用 probe 交付设计。

### 4.2 真实 Windows 文件占用与恢复

同一脚本对生成安装版本的 `resources/defaults.json` 调用真实 `CreateFileW`，只允许 share-read、不允许 share-delete，再走卸载（未 mock Win32 或文件系统）。输出：

```text
real_windows_file_lock: status=recovery_required
reason=file_in_use
enabled=false
real_windows_file_lock_recovery: completed
```

关闭测试句柄后 recover 继续清理，最终才提交 uninstalled。此记录验证文件占用分类和部分删除恢复，不表示已有真实 Core/设置/Worker 装卸人工确认。

### 4.3 自动化命令与结果

- 恢复前新回归：11 failed / 1 passed；重构后初版 13 passed。
- 升级/GC/删除/孤立目录新合同首轮：4 failed / 26 passed（两项是测试错误使用不存在的 `store.data_root`，已纠正为 root.parent.parent；另两项修复生产的 GC 残留与孤立目录错误收编）。
- 补齐故障注入后专项：49 passed / 1 warning；新增 ZIP central-directory/journal 写失败回归首轮 2 failed，修复后 2 passed。
- 相关七族：245 passed / 1 skipped / 1 warning，57.77s（最后两项新合同及最终文档之前的检查点）。
- 最终专项 + PR 纪律：`python -m pytest -q tests/test_feature_package_transactions.py tests/test_pr_report_discipline.py --tb=short`，102 passed / 1 warning，36.78s；其中 51 项为事务专项。唯一新增 warning 为构造重复 ZIP 条目时 Python zipfile 的明确告警，不过滤。
- Ruff 全库：`python -m ruff check .` → `All checks passed!`。
- format：`python -m ruff format --check pet/feature_package_transactions.py pet/feature_package_files.py pet/feature_package_probe.py pet/feature_install_state.py pet/feature_version_lease.py tests/test_feature_package_transactions.py` → `6 files already formatted`。
- mypy：`python -m mypy pet/feature_package_transactions.py pet/feature_package_files.py pet/feature_package_probe.py pet/feature_install_state.py pet/feature_version_lease.py` → `Success: no issues found in 5 source files`。此前 11 项错误通过收窄类型修复，不以 broad ignore 掩盖。
- 全量首轮：`QT_QPA_PLATFORM=offscreen python -m pytest -q --tb=short` → 1 failed / 3629 passed / 12 skipped / 15 warnings，395.23s；唯一失败是 `test_product_copy_has_no_external_brand_reference`，HEAD 原有 PROJECT-ENTRY 引用和本轮新设计元数据均命中。仅移动完整分支名到工程报告、修正当前阶段入口，未放宽或豁免测试。针对文案门 + PR 纪律 52 passed / 0.95s。
- 最终全量复验：`QT_QPA_PLATFORM=offscreen python -m pytest -q --tb=short` → **3634 passed / 12 skipped / 15 warnings / 368.67s**（exit 0）。较首轮增加两个故障回归与新增报告的两个纪律参数项，并修正文案门；没有删除测试、增加 skip 或过滤 warning。14 项为既有 Qt 弃用提示，1 项为本轮构造恶意重复 ZIP 的明确告警。
- 高负载命令：`python .scratch/phase4b-local-management/stress_transactions.py`（本地忽略探针）在本机启动 20 个自有计算子进程，三个连续 pytest 子进程运行 `tests/test_feature_version_lease.py tests/test_feature_worker_handoff.py tests/test_feature_state_monitor.py`，每次均设 `QT_QPA_PLATFORM=offscreen`。真实 GetSystemTimes 测量 CPU 为 100.00%、100.00%、99.99%；三次均 13 passed，pytest 用时 18.05、15.75、12.47s（端到端 20.005、18.667、14.760s）。finally 仅关闭本探针自有计算进程，没有关闭 Core/设置/用户进程。
- 文档链接：`python scripts/check_docs.py` → `Markdown link check passed: 125 files scanned`。
- diff-check：`D:\DELL\Git\cmd\git.exe diff --check` → exit 0（纠正记录 EOF 多余空行后）。保护文件 diff 无差异、暂存为空；仅五份任务 Markdown 被 ignore 例外开放，bench/stress 脚本与日志仍 ignored。

跳过项：真实 OS sandbox host/Worker 自检、Core/设置 queued 生命周期与生产 startup receipt、冻结产物人工门、真实磁盘不足/杀软/ACL 门。这些属于未执行/未完成，不伪称环境失败；不能将测试注入的 checker/receipt 包装成这些项目通过。

## 五、准确停点、风险与下一步

**4B-3 尚未做成功。** 4B3-0/事务与 staging 合同有落盘和专项证据；4B3-3/5 状态机有受控验证，但 queued adapter/真实 Worker/生产启动闭环仍不存在；4B3-4 和 4B3-6 未完成。发现的方向问题不是“换几个环境变量”可修复。

建议先补 Windows OS 沙箱 launcher 与专用冻结 probe 的设计和攻击面验证，保持强安全边界，再继续接入 Core/设置 owner-thread 启动与关闭路径。 官方 AppContainer/LPAC 资料已查阅，来源及与当前假 HOME 探针的区别见 [设计中的 OS runner 依据](plugin-phase-04-updates/PHASE4B-3-LOCAL-TRANSACTIONS-DESIGN.md)；没有宣称该候选已满足截图/Qt/进程树隔离。是否采用 AppContainer 需要验证读依赖、禁止用户数据/网络/桌面和 native process-tree 约束；本轮未修改系统 ACL、安装系统组件或做任何外部发布。

其它已知未封闭边界：journal 最早 accepted 写入与第一个 pending commit 之间的恢复；失败候选 host 已在实际 Core 常驻后的重启回滚；所有 journal/rollback/GC 故障点与资源超限测试；跨平台“不覆盖 rename”的实机验收；真实签名产物与用户草稿关闭交互。代码仍应作为未接生产的 WIP 审查，而不是公开安装入口。

## 完成后的实际使用效果与当前限制

- 现在没有新增管理界面、正式 CLI 或下载入口；原默认内置功能的行为未改。
- 受控 fixture 可验证目录/ZIP 的事务、租约等待、回滚、删除恢复与 GC，保留个人数据边界。
- **默认后端仍不能完成生产安装：没有 OS 沙箱会明确拒绝，不会误执行代码或显示成功。**
- 记录和代码已写入同一任务工作区；没有提交或推送，不代表 Setup、第三方包、默认内置物理卸载或最终冻结 Core 人工验收已完成。
