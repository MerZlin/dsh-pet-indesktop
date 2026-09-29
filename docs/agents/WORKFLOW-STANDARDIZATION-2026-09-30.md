# 协作规范固化与成果同步：设计及验收

基线日期：本机 2026-09-30（UTC+08:00）。这是本次文档与同步任务的设计，不是 Phase 4B 的实现报告。

相关入口：[文档索引](../INDEX.md)、[工程指南](../../AGENTS.md)、[既有交接规则](handoff.md)、[任务清单](../../.scratch/workflow-standardization/PLAN.md)、[交接记录](../../.scratch/workflow-standardization/HANDOFF.md)、[当前状态](../../.scratch/workflow-standardization/STATUS.md)。

## 1. 目标、事实与非目标

- 统一计划和汇报：前半部为正式工程说明，最后直白说明实际可体验效果及限制。
- 每个正式计划保留设计与验收、PLAN、HANDOFF、STATUS 四份记录，并明确纳入 Git。
- 起点工作树干净，本地有五个待推送提交；具体分支、SHA 与范围见交接。HTML 路线图已跟踪，起点本地和远程内容相同，不为重复提交而修改。
- 仅新增协作规范及记录；保留 Phase 4A 成果。Phase 4B 只有计划，状态层、租约、事务及管理 UI 都未实施。
- 不改运行时、测试、打包、自动更新、HTML 或仓库外全局指令；不归档、移动或删除资料；不建 PR、不合并主分支、不用子智能体。

## 2. 方案与文档接口

简明强制规则写入 AGENTS，完整模板统一写入 `docs/agents/planning-and-reporting.md`。交接规则只补准确停点和恢复步骤，避免复制两套模板。SPEC 的文档治理条款、索引和日志引用统一规则。

设计位于对应阶段的 docs 目录；PLAN、HANDOFF、STATUS 位于同一 `.scratch/<任务名>/`。完成后保留最终记录。被忽略目录只显式暂存这些 Markdown，不提交日志、密钥、用户数据、生成构建。

本任务先写四份记录，再修改规范。Phase 4B 复用原设计、PLAN、HANDOFF，并增加 STATUS；历史“当时未推送”保留，当前授权改为仅本次成果同步可推送。

本次没有产品接口或行为变化，也不将安装卸载体验提前写成已可用。

## 3. 实施顺序与停止条件

| 编号 | 动作 | 依赖/出口 |
|---|---|---|
| W01 | 核对工作树、五提交范围、保护文件和 HTML | 出现意外文件或敏感材料先停止；不改写历史 |
| W02 | 先留档本任务四文件，再固化规范并补齐 4B 状态 | 模板单一、互链、当前状态与历史记录不冲突 |
| W03 | 文档、产品文案、静态及类型检查 | 任一失败分类，不推送 |
| W04 | 全量回归 | 无失败；skip/warning 数量变化有解释 |
| W05 | 九文件 Qt/Worker 组合有界高负载三遍 | 3/3 通过；只停止本任务自己的负载进程；不是长期 soak |
| W06 | 核对增量、显式暂存、独立提交 | 不混入代码、构建或密钥；随后独立记录实际 SHA |
| W07 | 刷新远程、检查祖先关系并正常推送 | 分叉停止，不强推、不 reset、不自动合并 |
| W08 | 核验远程 SHA、HTML 和工作树，再记录真实结果 | 未核验不能写“已推送” |

当前授权仅覆盖本次文档提交和已有成果推送，不成为后续自动发布授权。

## 4. 验收矩阵与可复现命令

全部在仓库根执行。设置 `QT_QPA_PLATFORM=offscreen`，不截图、不读真实 Key、不请求真实模型。

| 门 | 命令/方法 | 期望 | 失败处理 |
|---|---|---|---|
| 文档 | `python scripts/check_docs.py`；另检查 Git 跟踪的四份记录互链 | 无死链、无漏登记 | 修文档并重跑；不删除链接逃避 |
| 报告与文案 | `python -m pytest -q tests/test_pr_report_discipline.py tests/test_desktop_pet_features.py::test_product_copy_has_no_external_brand_reference` | 全部通过 | 真实分支元数据归入交接，不修改扫描边界 |
| Ruff | 下列 check / format | exit 0 | 分类，未通过停止推送 |
| mypy | 下列受影响范围 | exit 0 | 不夹带未经确认的运行时重构 |
| 全量 | `python -m pytest -q` | 无失败、明确 skip/warning | 记录失败与原因，不借用旧绿灯 |
| 高负载 | 下列九文件同进程组合连续三次 | 3/3，无原生崩溃 | 记录负载、退出码；失败停止推送 |
| 范围 | `git diff --check`、暂存检查、保护文件摘要 | 仅规范/记录，保护不变 | 纠正暂存，不回退用户工作 |
| 同步 | fetch、正常 push、ls-remote/跟踪 SHA 和 HTML 对照 | 本地与目标远程一致 | 分叉/网络错误如实停止，禁止强推 |

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
python -m ruff check pet features tests scripts packaging/phase4a_synthetic_worker.py packaging/phase4a_validation_entry.py
python -m ruff format --check pet features tests scripts packaging/phase4a_synthetic_worker.py packaging/phase4a_validation_entry.py
python -m mypy pet/plugins pet/workers pet/feature_ports.py pet/feature_host_bindings.py pet/feature_bindings.py pet/feature_config.py pet/feature_data.py pet/credentials.py pet/desktop_query.py features/screen_understanding pet/chat/external_turns.py
python -m pytest -q
python -m pytest -q tests/test_feature_package_binding.py tests/test_feature_runtime_ports.py tests/test_feature_settings_ports.py tests/test_external_worker_launch.py tests/test_screen_runtime.py tests/test_screen_contributions.py tests/test_workers_lifecycle.py tests/test_agent_link_worker_integration.py tests/test_proactive_worker_lifecycle.py
```

最后一条在有界高 CPU 负载下复跑三遍，记录本机环境、负载进程数、实际 CPU 样本、耗时及退出码。Git 在本机使用绝对程序路径，准确命令在 HANDOFF 保存。

本轮不重建冻结产物、不做新的人工识屏/keyring/托盘验收、不增长期 soak。已有构建证据必须带原版本及日期，不能替代本次运行记录。

## 5. 提交与回滚

五个既有提交保持原样。本次文档独立提交，显式暂存；记录实际 SHA 的后续文档提交不 amend 前一次。只执行正常推送，目标见交接。若远程分叉或出现敏感材料，停止并说明，不擅自改写历史。

后续经授权可用 `git revert <本次规范提交>` 回滚规范，不回滚此前 Phase 4A 代码；记录提交亦可单独 revert。没有本轮生产实现可回滚。

## 6. 完成后的实际使用效果

桌宠界面与操作不变，暂时没有新增用户操作，也没有 Phase 4B 安装/卸载按钮。

用户可以从 STATUS 看阶段状态、PLAN 看剩余任务、HANDOFF 找续作停点；每次计划和汇报最后都说明到底能怎么用。核验推送成功后，现有代码、测试、设计和 HTML 在同一远程分支可追溯。下一步仍从 Phase 4B-1 的唯一安装状态开始，不跳到管理 UI。
