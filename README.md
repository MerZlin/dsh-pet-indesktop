# dsh-pet-indesktop

面向开发者的项目入口与工程导航。产品目标是供其他用户使用；本 README 仅服务于开发、交接和工程验证，不等于产品只面向开发者。这里不提供普通用户安装教程；新贡献者和继续施工的对话请先阅读 [`docs/PROJECT-ENTRY.md`](docs/PROJECT-ENTRY.md)，再按任务状态进入对应阶段文档。

## 项目定位

`dsh-pet-indesktop` 是 Python + PySide6 桌面宠物应用。当前架构和路线将功能分成：

- **Core**：窗口、动画、基础交互、配置、生命周期、通用平台查询、菜单/设置宿主和扩展管理基础；
- **资源 DLC**：角色、动画和其他纯内容包；资源包不执行代码；
- **官方功能 host**：可信官方功能在 Core 进程中运行，但代码和配置按功能包交付；
- **Worker**：高风险或阻塞执行逻辑在独立进程中，通过 `QProcess` 和 `pet-worker/v1` JSONL 与 Core/host 连接；
- **第三方生态**：按路线条件开放，当前不开放任意 Python `entrypoint`。

Worker 的独立进程边界不等于 DLC 已可安装、停用或卸载；Python host 进入解释器后也不承诺即时热卸载。资源硬门与跨进程版本租约已完成；当前正连续验收 Windows 沙箱、本地生产事务、管理 UI 和双冻结 Core。

## 从哪里开始

1. [`docs/PROJECT-ENTRY.md`](docs/PROJECT-ENTRY.md)：渐进式项目入口、目录树、状态词汇和按问题导航；
2. [`docs/INDEX.md`](docs/INDEX.md)：完整文档索引；
3. 当前任务 `.scratch/<任务名>/STATUS.md`、`PLAN.md`、`HANDOFF.md`、`SUMMARY.md`；
4. 对应阶段设计和验收文档；
5. 需要代码细节时，再读源码和测试，不用 README 代替实现证据。

当前路线位置：

```text
Phase 4B-1       唯一安装状态账本：已完成
Phase 4B-1.5     资源 DLC 硬门：代码、自动化、文档、保护门与人工验收已通过；公开稳定 API 仍待后续开放门
Phase 4B-2       跨进程版本租约：内部实现与自动化/实机验收完成；已推送至当前远程分支
Phase 4B-3       安装/升级/卸载事务：Windows生产闭环与自动化验收完成
Phase 4B-4/4B-5  管理 UI 与双冻结 Core Windows工程验收完成；人工/发布门单列
Phase 6          资源接口、外部 Worker 和第三方生态：按门条件开放，当前未公开
```

**2026-10-04 人工门推进**：no-chat真实目录安装/手动识屏、自然退出重启、包级启停、ZIP同摘要幂等、1.0.1升级与1.0.0回滚均已确认。两版本物理卸载及revision14未安装核验后，从ZIP新安装真实加载完成revision17/pending=null；重启Core实际host租约通过，用户现确认“入口恢复，原设置保留，未重填密钥，结果正确”，卸载重装后的数据可用性与手动识屏人工门通过。首次Core未完成pending加载原因仍未证实；自动识屏、草稿/多实例、chat变体及其他平台未全验收。正式T0密钥归属/加密保管/备份/轮换候选未实施；2026-10-04按用户要求暂缓正式信任锚与分发验收，优先清理旧构建，不影响现用人工版桌宠运行；未生成正式key，当前仍非正式发行构建。锁修复全量3841 passed、两族满CPU三遍为历史通过，前轮仅清理历史生成物并复核公开状态/受保护摘要；2026-10-04 用户现已授权将源修改提交推送到对应远程分支，全量/静态与满CPU三遍新通过；源提交 `98bbfba` 已正常推送到 `origin/codex/phase3-worker` 并独立核对远端SHA。不读取私人数据、不正式发布。见[人工验收记录](docs/PR-REPORT-PHASE4B-MANUAL-ACCEPTANCE-2026-10-04.md)。

**2026-10-04 存储清理完成**：按用户确认的294目录清单删除132,245个旧构建/重复依赖/生成夹具，`.scratch`逻辑大小由34.987降至3.809 GiB；E盘空闲实测增加31.435 GiB。当前Core03两变体、原用户数据与已安装1.0.0、人工包源、任务记录及公开验收证据保留，程序/安装包摘要与公开state核验一致。没有强退进程、产品源码修改或提交/推送；正式信任/分发继续暂缓。详见[同组最终交接](.scratch/phase4b-local-management/HANDOFF.md)。

## 本地开发环境

项目主要在 Windows + PowerShell 上验证。请使用项目现有 Python 环境或独立虚拟环境，不把用户密钥、构建产物和缓存提交到 Git。

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
```

如果项目环境已由维护者准备好，不需要重复安装依赖。修改网络、Qt、打包或平台行为前，先查 [`docs/INDEX.md`](docs/INDEX.md) 中的专项指针和 `AGENTS.md`。

## 启动与常用验证

开发启动：

```powershell
python -m pet
```

无 GUI 的 Qt/测试环境：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
```

常用测试和静态检查：

```powershell
python -m pytest -q
python -m ruff check pet tests scripts
python -m ruff format --check pet tests scripts
python -m mypy pet
python scripts/check_docs.py
python -m pytest -q tests/test_pr_report_discipline.py
D:\DELL\Git\cmd\git.exe diff --check
```

这些是项目常用命令；每次汇报必须写实际执行了哪些，不得把未运行命令写成通过。

## Worker 验证

Worker 使用显式入口运行，不通过任意 Python 模块字符串加载：

```powershell
python -m pet --worker agent-link-events
python scripts/verify_phase3a_frozen_worker.py <frozen-worker-or-app-path>
```

主动识屏 Worker 仍属于官方内置能力的实施路线，不能从“能握手/能退出”推导为已完成可选卸载交付。Worker 不读取 Core 全局配置、`PetWindow`、keyring 或完整聊天会话；需要的单次授权通过受控 IPC 边界传递。

## DLC 与功能包开发边界

资源包和可执行功能包分开处理：

- 资源包不执行代码，必须经过 manifest、路径、摘要、兼容性和播放链验证；
- 官方 host 使用固定 allowlist/factory 和受限宿主端口；
- Worker 是故障隔离和执行边界，不是权限沙箱；
- 不执行未知来源 `entrypoint`，不开放任意第三方 Python host；
- “有 Worker”“可停用”“物理卸载”分别验收；
- Phase 4B 的实际顺序是：唯一状态 → 资源硬门 → 跨进程租约 → 本地事务 → 管理界面 → 真实构建端到端；
- 资源接口正式公开依赖 4B-1.5 的稳定性结论及后续开放门。

## 重要目录

```text
pet/            Core、GUI、配置、插件运行时和 Worker
features/       功能 host 迁移目标和功能实现
content/        内容管理公共目录
assets/         内置角色和动画素材
scripts/        构建、检查和真实环境探针
packaging/      PyInstaller/安装器和构建入口
tests/          单元、Qt、进程、打包和 DLC 回归
docs/           路线、设计、API、验收和报告
.scratch/       任务 PLAN/WORKLOG/HANDOFF/STATUS/SUMMARY
```

修改某个目录前，先确认对应任务的写入范围；不要把生成的 `dist-*`、`build-*`、缓存、密钥或原始日志加入提交。

## 施工记录和交接规则

每个持续任务保留：

- `docs/` 下的设计与验收详情；
- `.scratch/<任务名>/PLAN.md`；
- `.scratch/<任务名>/WORKLOG.md`；
- `.scratch/<任务名>/HANDOFF.md`；
- `.scratch/<任务名>/STATUS.md`；
- `.scratch/<任务名>/SUMMARY.md`。

计划和汇报正文前面采用正式工程结构，最后必须写“完成后的实际使用效果与限制”。新对话优先读 `docs/PROJECT-ENTRY.md`，再读当前任务状态、计划、交接和总结。详细规则见 [`docs/agents/WORKFLOW-CONTINUITY-AND-PROJECT-ENTRY.md`](docs/agents/WORKFLOW-CONTINUITY-AND-PROJECT-ENTRY.md)。

## Git、提交和远程发布

- 默认不开子智能体；只有用户明确要求时才使用。
- 本地 Git 提交可以在用户明确要求或阶段需要备份时使用，作为回滚点；本地提交不等于远程发布。
- `git push` 必须得到当前任务中的明确授权；一次授权不自动延续到后续任务。
- 提交前显式暂存文件，检查保护文件、敏感文件和完整 diff；不使用 `git add -A`、`git add .`、`reset --hard`、强推或覆盖用户改动。
- 施工记录必须分开写“已提交”和“已推送”；没有远程核验不得宣称已推送。

重点保护：

```text
pet/updater.py
pet/update_settings.py
plugin-roadmap-demo.html
用户已有未提交改动、历史证据、真实 Key、用户数据、构建产物
```

## 当前已知限制

- 当前版本仍有自动识屏等待、通用托盘退出和真实安全存储等其他手测待完成；4B-1.5 资源 DLC 人工验收门已通过。未手测不等于产品故障。
- “常规 / 扩展管理”与本地事务已实现，当前最终冻结闭环仍在验收；源码/正式构建保持信任策略 fail-closed，验证构建不等于正式可分发安装器。
- 4B-1.5 的代码、自动化、文档、保护门与人工验收均已通过，但资源接口仍不能直接当作公开稳定 SDK。
- 4B-2 跨进程租约已完成；4B-3/4B-4/4B-5 的准确工程门见 [连续收尾报告](docs/PR-REPORT-PHASE4B-MANAGEMENT-CLOSEOUT-2026-10-04.md)。
- 三平台正式发布、远程 catalog、Workshop、第三方 Worker 正式开放和 Python host 热卸载均不是当前能力。

## 贡献前检查清单

- [ ] 先读 `docs/PROJECT-ENTRY.md`、`docs/INDEX.md` 和当前任务 scratch 状态；
- [ ] 明确本次修改是否越过 Core/资源/host/Worker 边界；
- [ ] 先补或运行对应行为测试，不用文件名/注释命中替代验证；
- [ ] 区分计划、实现、自动化、实机、用户确认、提交和推送；
- [ ] 运行受影响测试、ruff/format 和必要的全量门；
- [ ] 检查 `git diff --check`、保护文件和暂存范围；
- [ ] 更新 WORKLOG、STATUS、HANDOFF、SUMMARY；
- [ ] 如需本地备份，明确提交范围；如需推送，重新取得当前任务授权。

## License

请以仓库现有 `LICENSE` 和发布说明为准。具体发布和用户使用信息不在本开发者 README 中展开。
