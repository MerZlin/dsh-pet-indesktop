# Phase 4B 本地管理交接

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
