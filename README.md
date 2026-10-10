# dsh-pet-indesktop

<!-- CURRENT_20261010_START -->
> **当前状态（2026-10-10）**：用户已验收 Core **4.2.4** / AI、Screen **1.0.3**。该批修改已在 `origin/codex/phase3-worker` 建立并核对检查点 `0a299612714e5fad55a24a5506dfce938b9eeb1c`。后续 MOD 管理中心与公共 v1 已实现，Core **4.2.5** / 两个 DLC **1.0.4** 已形成可运行候选并完成冻结链路烟测；本地检查点已提交并按当轮授权推送到 `origin/codex/phase3-worker`（提交号以 `git log -1` 为准），不是正式签名稳定版。满 CPU 压力族限制、ZIP 导入缺陷和人工验收项见报告。

[MOD 使用与制作指南](docs/modding/README.md)分成「怎么加 MOD」与「怎么做 MOD」两条线：角色素材、台词模板、host-only、Echo Worker 四类教程，`pet.mod_api.v1` 接口基线与兼容承诺，以及官方 AI / 识屏两个 DLC 的接口用法案例；[交付报告](docs/PR-REPORT-MOD-CENTER-V1-2026-10-10.md)记录实际验证与限制。**已知未解决缺陷**：ZIP 接入的外接 MOD 可能无法启用，规避办法是优先用「导入目录」，详见该指南。简易 API、项目内 `data` 布局和 Setup 安装卸载合同不变；4.2.4 Setup `_s01b/setup-release/dsh-pet-core-webm-setup.exe` 保留。最终新候选以[状态](.scratch/mod-authoring-v1/STATUS.md)为准，不使用旧中间产物。
<!-- CURRENT_20261010_END -->

> 历史状态（2026-10-06；非当前）：用户授权保存已有源改动并推送远端检查点后继续验收。新全量4298 passed, 15 skipped, 15 warnings in 929.75s (0:15:29)、真实CPU高负载三遍、Ruff/format/mypy通过，183文本文件明确暂存；提交/远端核对尚在进行。仅AI正常Core确认/退出、可见包标题、长路径LPAC和权限canary、Core04构建审计已有证据；分号路径、正常Worker、新Core完整矩阵/性能、新版正式分发与真实安装/人工/干净环境仍待完成，不能用旧产物冒充。最新质量结果及准确续接见 [同一实施报告](docs/PR-REPORT-PHASE5A-LOCAL-DISTRIBUTION-2026-10-04.md) 与 [交接](.scratch/phase5a-local-distribution/HANDOFF.md)；历史日期与Phase4B证据不变。

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

## MOD 制作注意事项

想加 MOD 或做 MOD 的完整教程、按类型样例和接口契约见 [MOD 使用与制作指南](docs/modding/README.md)（接口细节 [API-V1.md](docs/modding/API-V1.md)，官方案例 [OFFICIAL-DLC.md](docs/modding/OFFICIAL-DLC.md)）。动手前最容易踩的几条：

- **包布局**：包根必须有 `manifest.json`；做成压缩包时 `manifest.json` 要在**压缩包根**——多套一层文件夹会被判 `manifest missing`。
- **factory 必须无副作用**：`create_host()` 只声明元数据，要能在没有 GUI 的环境下导入。模块顶层不要 import Qt / `QWidget`，也不要在这里 import WorkerClient 和业务依赖——生产探针里没有 Qt，顶层 import QWidget 会让包直接校验失败。Qt 相关放到 `create_settings` / Runtime 构造里。
- **只依赖 v1 薄接口**：只 import `pet.mod_api.v1`。不要 import `pet.app`、`PetWindow`、全局 `Config` 或其他 DLC 的私有模块（如 `pet.context_menus.shared`）——这些不在兼容承诺内，Core 内部一搬家你的 MOD 就会坏。官方 AI / 识屏案例里的内部接线仅供参考，不是可复制模板。
- **生命周期**：`start()` / `stop()` 要可重复调用；`close()` 必须释放定时器、窗口、信号连接和未完成任务；不要阻塞 GUI 线程。停用后旧菜单句柄不能再执行，菜单回调先检查 `handle.active` 再 `invoke()`。
- **设置组件**：实现 `dirty()` / `draft()` / `confirm_save()` / `discard_changes()` / `dispose()`。保存要原子、失败要保留用户编辑；不要用后台刷新覆盖用户正在输入的草稿，也不要覆盖其他页面的草稿。
- **自己的数据别自己拼路径**：用 `context.configuration`（带版本冲突检测）、`context.documents`（单拥有者文档），跨进程读改写用带锁的 `context.state_documents`。**不要把 Key 写进配置命名空间或日志**，也不要把 Key 拼进错误消息。
- **host-only 还是 host-worker**：只有需要跑耗时或容易崩的任务才拆独立进程。Worker 是**进程隔离，不是安全沙箱**；取消是合作式的，handler 必须自己设超时或检查 cancel Event；host-only 定义不允许申请 Worker。运行时目录用项目内 `data/feature-runtime`。
- **资源包不执行代码**：角色 / 动画包只靠 manifest 声明，不会运行你的 Python；`videos/` 加 manifest 要作为一个角色包整体交付，保留相对路径与大小写。
- **版本与兼容**：改了内容必须升 `version`（同版本同内容会被判「已存在，不重复导入」）；用 `core_requires` 声明最低 Core；v1 的破坏性变更走 v2 并提供适配和迁移说明。
- **打包与自检**：`python -X utf8 -m scripts.build_mod_example --check examples/mods/hello-local`；host-worker 还要提供已经冻结的 `--worker-bundle`。可运行样例源码在 [`examples/mods/`](examples/mods/README.md)。
- **信任模型**：本地 MOD 是「受信任的 Python」，**不是安全沙箱**；只安装你信任来源的包，本地安装即代表你信任其代码。

**已知缺陷（2026-10-10，未解决）**：用 ZIP 导入**示例功能包**（`hello-local`、`echo-worker`）会报 `worker_probe_failed`、操作未完成，改用「导入目录」正常；角色资源包走 ZIP 正常。做功能扩展现阶段请优先用目录导入，详见指南的「已知缺陷」一节。

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
