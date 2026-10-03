# 本次准确停点（2026-10-02，4B-1.5 本地封存就绪）

工作区 `E:/AI/DSH/dsh-pet-indesktop`，分支 `codex/phase3-worker`，起点/HEAD `ce164847dc1f3553a2e62d0cb5dc13c2fb9bd339`。本轮增量未提交、未推送；未开子智能体。**4B-1 已实现并完成自动化回归；4B-1.5 代码、自动化、文档与保护门已通过，本地封存就绪；4B-2 尚未开始。**

权威入口：[设计 §2.1](../../docs/plugin-phase-04-updates/PHASE4B-LOCAL-MANAGEMENT-DESIGN.md)、[PLAN](PLAN.md)、[STATUS](STATUS.md)、[完整实施证据](../../docs/PR-REPORT-FEATURE-INSTALL-STATE-2026-09-30.md)。

## 本轮实际验证与准确停点

- 新增两个状态模块与 86 项测试；可收集骨架初始 24 failed，随后逐层 red/green；恢复与 ctypes 类型复用缺陷均有先失败的断言。
- `python -m pytest -q tests/test_feature_install_state.py tests/test_feature_packages.py tests/test_feature_config_ports.py tests/test_feature_settings_ports.py tests/test_screen_configuration.py`：216 passed、1 skipped / 14.73s。
- `python -m pytest -q tests/test_feature_install_state.py -k "two_real_processes or kernel_lock_busy or real_process_interruption"`：三次各 10 passed、76 deselected / 3.67、3.76、3.82s。
- `QT_QPA_PLATFORM=offscreen` 全量：3547 passed、12 skipped、13 warnings / 343.74s。
- Ruff lint 通过；format 463 files already formatted；mypy 两个新增模块通过。文档链接 114 份通过；PR 报告纪律 + desktop features 140 passed / 12.62s；`git diff --check` 通过。updater、update_settings、HTML 的 SHA-256 与起点一致；暂存区为空，无提交/推送。
- 当前较历史全量新增 88 个通过项：86 个状态测试、2 个新报告纪律参数项；skip 仍为 12。warning 少一次原有 `test_try_move_success_still_builds_plan_and_moves` 的 QImage.mirrored 弃用提示，其测试和产品代码未修改，也未过滤 warning。其余 13 项均为既有 Qt 弃用提示，不将提示数量变化认作产品修复。
- 本机 E 盘 50 次提交的中位 67.219ms、50 条收据下 100 次读取中位 38.119ms；锁持有分别 65.924/37.399ms。完整命令、分位数、磁盘与 traced memory 见报告。

## 本次文档与保护复核（2026-10-02）

- `python scripts/check_docs.py`：`Markdown link check passed: 119 files scanned`。
- `python -m pytest -q tests/test_pr_report_discipline.py`：`47 passed（pytest exit code 0；运行耗时随环境变化，不作为固定基线）`。
- `D:\\DELL\\Git\\cmd\\git.exe diff --check`：退出码 0；仅有换行规范提示，无 whitespace error。
- `D:\\DELL\\Git\\cmd\\git.exe diff -- pet/updater.py pet/update_settings.py`：无差异；`plugin-roadmap-demo.html` 未出现在状态中。
- 以上复核针对本轮文档/记录编辑；全量运行时测试仍以文档编辑前的 `3562 passed, 12 skipped, 13 warnings in 356.90s` 为最后结果，本轮未因文档变更重跑全量。

## 继续前必须保留的边界

- `state.json` 唯一权威；op 收据和 frontier 仅作证据。未知后续操作/未来 schema/缺少证明时安全拒绝，不猜测恢复。
- 当前返回的已验证描述不是租约；4B-1.5 资源硬门 → 4B-2 在同一协调边界下接续真实版本占用与授权。
- 状态读取遍历历史；不在 GUI 高频循环同步调用。4,096 记录上限，无自动证据压缩。POSIX 分支和普适断电持久性未实机验证。
- 不接生产启动，不动版本目录、配置、凭据、记忆和聊天历史；无用户操作变化。
- 当前版本识屏、原生安全存储、自动识屏及托盘退出人工门仍未验收，不是故障。
- 本轮不提交/推送、不重建产物、不扩大到安装器或 UI。原始日志/测量工具留本地忽略目录；权威结果在已登记报告。

## 下一步（不是本轮自动开工）

从设计 §3 与 PLAN 的 4B-1.5 资源硬门 → 4B-2 开始：先设计并测试 host/设置/Worker 的跨进程租约、协调锁及启动交接；处理同步读取成本，保持错误时保守占用。不得跳到管理界面，也不能用 PID/超时猜测可以删除版本。

## 实际可体验效果

本轮没有新增菜单或安装卸载操作。只建立可靠的安装状态基础，尚未交付完整可拔除流程。历史检查点如下，保留当时事实，不用其中“未实施”覆盖当前进度。

---

# 历史检查点交接（不是当前实施状态）

更新：2026-09-30（续写 2026-09-29 的检查点任务）。工作区 `E:/AI/DSH/dsh-pet-indesktop`，分支 `codex/phase3-worker`。

## 精确停点

用户要求先补文档并对截至目前所有修改做本地备份，因此暂停 Phase 4B 代码实施。设计、任务清单已落盘，并以 `5f04a99` 完成本地备份；4B-1 至 4B-5 尚未开始。当前 TDD：无新测试，无 red/green。不得宣称安装闭环完成。

## 权威入口与决策

- [设计与验收](../../docs/plugin-phase-04-updates/PHASE4B-LOCAL-MANAGEMENT-DESIGN.md)；[逐步清单](PLAN.md)；[当前状态](STATUS.md)。
- 同一数据根共享安装/整包启停/卸载；每实例配置/凭据/自动开关不合并。
- 占用时等待用户自然退出；已加载 host 不承诺热卸载。
- 先状态 → 跨进程租约 → 可恢复事务 → UI → 两种冻结 Core 完整使用链，前门通过再下一步。
- 不开子智能体。先前本地备份未获推送授权；2026-09-30 用户另行授权[规范与成果同步](../workflow-standardization/HANDOFF.md)提交并推送已有成果。当前同步状态见 STATUS；不授权未来 Phase 4B 自动提交或推送。
- 保护 updater/update_settings、演示 HTML、原默认构建/工作流；不读真实 Key、截图、付费模型或长期 soak。

## 继承状态与历史证据（不是本轮复验）

起点 HEAD `b97112d`，ahead 3；146 份未暂存/未跟踪的 Phase 4A 源码、测试、文档。baseline 哈希/差异/状态保存在本目录未入库证据文件中。

Phase 4A 查询、独立配置/凭据、贡献、host/独立构建已有基线，详见[第四步报告](../../docs/PR-REPORT-SCREEN-HOST-BUILD-2026-09-29.md)。历史最终全量 3459 passed、12 skipped、14 warnings；冻结 10/10；这些不能当作本次或 4B 结果。独立验证产物位于 `.scratch/phase4a-host-build/delivery-build-20260929-03/`，被忽略，不进入提交。

当前版本用户手测、安全存储实测、自动识屏与托盘退出、其他平台及正式信任锚均未验收。先前手动识屏正常不替代当前版，不将自动未等待判为故障。

## 历史备份验证（2026-09-29 执行，2026-09-30 留档）

检查执行于 2026-09-29，2026-09-30 续作时读取原始日志及退出码，并核对源码仍与该次检查一致；不是重建冻结包。环境：Windows、Python 3.11.1、`QT_QPA_PLATFORM=offscreen`。

| 检查 | 实际结果 |
|---|---|
| `python -m pytest -q` 最终全量 | **3459 passed、12 skipped、14 warnings，321.65 秒**；退出码 0，外层计时 324.08 秒 |
| 相关运行时、贡献、打包绑定、Worker 生命周期及 PR 纪律 10 文件专项 | **108 passed，20.32 秒** |
| Ruff check（pet/features/tests/scripts 及两份 packaging 验证入口） | 通过 |
| 相同范围 Ruff format --check | **460 files already formatted** |
| 下列核心范围 mypy | **59 个源文件通过** |
| `python scripts/check_docs.py` | **111 份文档通过** |
| `git diff --check` | 通过；SPEC 现有 LF/CRLF 提示不是空白错误 |
| 提交候选与链接检查 | 149 份明确文件；337 个相对文档链接的目标均在提交范围或已有 Git 跟踪范围，无生成物依赖 |
| 保护门 | 9 项基线摘要未变：自动更新两文件、演示 HTML、原 onedir 脚本及五份工作流 |

可复现命令（在仓库根执行，Git 使用本机绝对路径）：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q tests/test_pr_report_discipline.py tests/test_feature_package_binding.py tests/test_feature_runtime_ports.py tests/test_feature_settings_ports.py tests/test_external_worker_launch.py tests/test_screen_runtime.py tests/test_screen_contributions.py tests/test_workers_lifecycle.py tests/test_agent_link_worker_integration.py tests/test_proactive_worker_lifecycle.py
python -m pytest -q
python -m ruff check pet features tests scripts packaging/phase4a_synthetic_worker.py packaging/phase4a_validation_entry.py
python -m ruff format --check pet features tests scripts packaging/phase4a_synthetic_worker.py packaging/phase4a_validation_entry.py
python -m mypy pet/plugins pet/workers pet/feature_ports.py pet/feature_host_bindings.py pet/feature_bindings.py pet/feature_config.py pet/feature_data.py pet/credentials.py pet/desktop_query.py features/screen_understanding pet/chat/external_turns.py
python scripts/check_docs.py
& 'D:\DELL\Git\cmd\git.exe' diff --check
```

首轮全量为 **1 failed、3458 passed、12 skipped、13 warnings（335.33 秒）**：新增设计正文的分支名称触发既有产品文案扫描，属于本次文档引入的问题。只将分支/提交元数据归入本交接，不改扫描规则或运行时代码；该回归随后 **1 passed**，再完整运行得到上表结果。12 项 skip 未增加；最终 14 项均为已有 Qt 弃用警告（`QImage.mirrored` / `QHoverEvent`），两轮警告位置相同、触发次数相差一次，未屏蔽警告。

另将审计中的两条未入库生成 `.spec` 链接改为其已跟踪构建入口，并明确生成文件名，避免新检出出现死链。除此之外没有修改产品、测试或构建代码。本目录 `checkpoint-*.log/json` 保存实际检查过程但被忽略；上表在 Git 中保留结论、命令和失败原因，不将原始日志、构建产物或测试私钥提交。

本次没有运行新的可见桌面验收、真实 keyring/模型、冻结构建或长期 soak；这些不能由全量 pytest 通过推定完成。

## Git 与文件范围

备份前暂存为空。纳入全部继承源码/测试/文档与本次设计、索引、日志；只将被忽略目录的 `PLAN.md`、`HANDOFF.md` 两份文本显式纳入。构建包、临时测试密钥、缓存和日志不入库。已创建本地检查点 `5f04a99`（`chore(checkpoint): 备份 Phase 4A 成果与 Phase 4B 实施计划`）：149 个文件，16922 行新增、3843 行删除。创建时未推送；本条及索引以随后独立文档提交回填，不改写备份提交。之后的同步结果见 STATUS，不倒写当时事实。

## 下一步准确动作

1. 恢复时先核对本交接及 Git 状态，保留之后用户改动。
2. 阅读 `pet/plugins/package_trust.py`、`feature_packages.py`、`package_binding.py`、`pet/config_transaction.py` 与锁实现，确认可复用边界；不要把资源 ContentManager 改成代码加载器。
3. 为 4B-1 新增 `tests/test_feature_install_state.py`，先覆盖缺失/损坏状态、revision 冲突、幂等与并发；文件现在尚不存在。
4. 再执行 `python -m pytest -q tests/test_feature_install_state.py` 记录真实失败，随后实现状态层，不先接 UI 或创建实际用户安装状态。
5. 本阶段证据写清命令、退出码和日志；遇真实缺陷/环境失败分类，不能沿用历史绿灯。

## 回滚与限制

本次备份恢复点将包含 Phase 4A 四步，不能用 reset 到 b97112d 丢弃它们。后续按主题独立提交并使用 git revert；当前没有 Phase 4B 可撤销的代码。实际卸载/升级恢复尚待实现，不要手工删用户目录验证。

## 路线调整记录（2026-10-02）

准确下一步已从“进入 4B-1.5 资源硬门 → 4B-2”调整为：先执行 `.scratch/phase4b-1-5-resource-hard-gate/PLAN.md`，完成资源 DLC 硬门；4B-1.5 资源硬门 → 4B-2 暂缓，不得用状态账本结果代替资源安装→解析→播放证据。

## 4B-1.5 资源硬门状态（2026-10-02）

当前准确停点为“4B-1.5 代码、自动化、文档与保护门通过；本地封存就绪，尚未提交或推送”。本轮资源专项证据为：相关资源链 43 passed；硬门测试连续三次 28 passed；最终文档编辑前全量为 3562 passed、12 skipped、13 warnings。Ruff、格式检查和 `mypy pet/content` 通过；文档链接、PR 纪律、`git diff --check` 和保护文件复核随后也已通过。上述全量结果对应文档编辑前代码状态，本轮文档编辑后未重跑全量。

下一条准确动作：完成 `check_docs.py`、PR 报告纪律、`diff --check` 和保护文件复核；通过后等待用户授权独立封存提交。不得进入 4B-2 租约、4B-3 事务或管理 UI，也不得把资源接口称为公开稳定。人工资源包安装、重启播放、可见桌面及托盘验收仍未完成。

## 连续性补档（2026-10-02）

- **准确停点**：4B-1 已完成；4B-1.5 的代码、自动化、文档与保护门已通过并具备本地封存基础；4B-2 尚未开始。
- **新增记录**：本目录已补充 `WORKLOG.md` 和 `SUMMARY.md`，与既有 `PLAN.md`、`HANDOFF.md`、`STATUS.md` 配套使用。
- **当前任务关系**：文档连续性任务正在补齐 `AGENTS.md`、项目入口、开发者 README、索引和日志；这些文档变更不改变 4B 生产实现。
- **最后一次阶段证据**：资源专项 43 passed；硬门测试连续三次各 28 passed；文档编辑前全量 3562 passed、12 skipped、13 warnings。该结果不是本轮文档修改后的全量结果。
- **未完成人工门**：资源包人工安装/重启播放、真实安全存储、可见桌面、托盘退出和三平台发布门仍未完成。
- **保护边界**：不得修改 `pet/updater.py`、`pet/update_settings.py`、`plugin-roadmap-demo.html`，不得覆盖用户已有工作树改动，不得跳过 4B-1.5 直接实施 4B-2。
- **下一步**：完成文档连续性任务的 `check_docs.py`、PR 报告纪律、`diff --check` 和保护文件复核；通过后等待用户对本地备份或下一阶段实施的单独授权。
