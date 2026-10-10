# 当前状态

2026-10-10 15:18；[设计](../../docs/modding/MOD-CENTER-IMPLEMENTATION-PLAN-2026-10-10.md)。
M00 已提交/正常推送并核对 `0a299612714e5fad55a24a5506dfce938b9eeb1c`；M01 后新功能未提交/推送。

- M01–M04：管理中心、公开v1、通用挂载、样例和教程已实现；最终验证尚未闭合。
- full11 历史通过：273完整测试文件各新进程、同时2文件，4553 passed/15 skipped，675.438s，无缺失节点。之后源码已改变，须重跑，不能当最终通过。
- final3 冻结 Echo 导入/启用/往返/删除待退出/自然退出/重开物理清理通过；外部源与个人数据哈希不变。官方生产Worker真实Core启动/租约/READY/自然退出通过。此后源码已改变，候选须更新。
- highload11 第一轮失败：首次发现时状态锁忙但 startup 尚未创建，无重试；CPU中位77.6%也未达门槛。用正常优先级20个自有负载进程复现（CPU100%），均自然退出。
- 2个真实锁红灯补齐后修复重试；第一次复验14 passed/1 failed，跨进程发现又暴露未完成安装被另一进程恢复，事务journal出现revision_conflict。
- 已用真实子进程补第二处红灯1 failed：活跃安装暂停在生命周期边界，另进程抢先推进同笔事务。新增非阻塞操作拥有锁，相关回归正在复验。
- Ruff当前通过；全量、三轮负载、最新Core/Setup、最终报告未完成。

## 实际效果与限制
不触碰真实安装/Key/屏幕，4.2.4保持不变。现有final3不能视为最新修复的最终交付；本轮尚不能标M05完成。

## 2026-10-10 15:46 验证续跑
- 元数据竞争红灯 `metadata-red3.log`（2 failed）；修复异步命令的有限元数据重试、锁忙时保留发现项。后台发现与显式操作共用完成信号红灯 `result-routing-red.log`（1 failed/2 passed），改用独立 load_changed；旧界面 44 passed。仅元数据准备可重试，不重放生命周期 apply/recover。
- 跨进程等待改为真实 Qt 事件循环/宽预算；满 CPU 六轮：前五轮各 4 passed，CPU 中位 100%；第六轮 3 passed/1 failed，导入/启用/停用状态均达到目标，失败在测试退出 communicate 等待 60 秒。诊断日志在 faulthandler 输出中截断，仍须定位退出或继承管道问题，不能视为通过。
- `final_gates12.py` 按失败门禁停止；full12/highload12 尚未开始。4574 节点已收集。所有 20 个自有负载进程 exit=0。当前没有新候选。
- 下一步：增强跨进程测试退出证据，区别 Core 进程未退出与 stdout 管道 EOF 未到达；不先修改产品关闭路径，不重复启动已失败的流水线。

## 2026-10-10 16:31 用户中断时停点（当前权威状态）

- 最新要求：优先可运行版本、本地检查点提交；非阻塞问题留档，尽快收尾。本轮尚未暂存或提交，不推送。
- 最新全量：full12 按完整测试文件隔离进程，4578 collected = 4563 passed + 15 skipped，275 文件、528.922s，source_unchanged=true。不能称单进程 pytest 全绿。之后仅测试 import 空行经 Ruff 修正；MOD 专项 checkpoint-focused.log 为47 passed/30.52s。
- 满CPU highload12 第一轮未通过：识屏8s握手超时、子宠清理时序3项、跨进程停用未达预期1项；第二/三轮未继续。原因与影响仍须核实，不用另一个3轮4项诊断通过替代整族门。
- 新候选 final4 Core/Setup 构建成功：199.344s /90.609s；artifact-final4-audit.json complete=true，源码/文件清单/两个ZIP/鲸鱼图标/旧4.2.4保留校验通过。生产Worker输入未变化，复用并校验。
- 已将最新Core增量复制到自有 _m05b/runtime3（2114文件核对，data保留）。冻结生产链在用户中断时未完成；frozen-final4-production-chain.json 无complete字段，不得写成实机通过。中断后只读进程检查未发现该运行目录的自有进程或验证脚本。
- 待做：完成final4最小冻结复验；补97文件报告和已知限制，更新本组记录；Ruff/diff/文档门；按file-review-list.json的92文件+本组5份Markdown逐项审核暂存、敏感检查、**只做本地提交**。不得git add -A/./推送。
- 报告辅助：report-file-reasons.json 97项已生成；PR报告仍为未完成草稿。新Setup：_m05b/final4/setup/dsh-pet-core-webm-setup.exe；不修改真实安装 E:\dsh-pet-core-webm。


## 2026-10-10 16:40 M05 可运行检查点收尾

- 冻结候选 `_m05b/final4` 的 Core 与 Setup 构建完成。`artifact-final4-audit.json` 校验 Core 2114 文件、两个 1.0.4 ZIP、Setup/Core 鲸鱼图标资源、旧 4.2.4 Setup 未改变；生产 Worker 输入未变化，沿用并核验 SHA-256。
- 在全新隔离数据根 `_m05b/runtime3` 完成真实冻结链：Core 维护安装两个官方包 → 项目内 `data/feature-runtime` 启动生产 Screen Worker → HELLO/READY → 20 次稳定采样 → 自然退出。结果：Core/Worker exit 0、READY 6.844s、请求假服务 0、租约退出后 `free`；不读取真实 Key、不截图。证据：`frozen-final4-clean-production-chain.json`。
- 同一 final4 Core 的独立 Settings `extensions` 页面原生 Windows 烟测通过：显示「导入 ZIP」「导入目录」「全部」「角色资源」「功能扩展」，设置与 Core 均自然退出；证据：`frozen-final4-ui-smoke.json`。
- full12 保持 4578 collected = 4563 passed + 15 skipped（文件隔离进程，275 文件，528.922s）；MOD 专项 47 passed；Ruff 与 `git diff --check` 通过。
- 满 CPU highload12 第一轮的识屏短时握手、子宠清理时序、跨进程停用问题仍作为 `known pressure limitations` 留档，未将其伪装为通过；按用户“可运行版本先提交”授权，不再扩展修复范围。
- 当前状态：M01–M04 implemented/automated and final4 real-machine smoke passed；M05 runnable checkpoint ready for local commit；Setup 实际安装/升级/卸载、真实 Provider/余额/屏幕和用户 UI 体验仍保留给人工验收，Phase5A 不因本地提交自动关闭。


## 2026-10-10 16:57 本地提交完成

- 可运行检查点已在当前分支创建本地提交；未推送远程。提交 SHA 以 `git log -1` 为准。
- 97 个显式交付文件已纳入；构建目录、日志、缓存、旧候选和真实安装目录仍未纳入。
- 用户验收边界不变：真实 Setup 安装/升级/卸载、真实 Provider/余额/屏幕，以及高负载限制的后续复验仍单独区分。

## 2026-10-10 22:35 ZIP 缺陷留档 + 教程入口收口 + 推送

- 用户报告（症状已于 2026-10-10 23:xx 按实机信息更正）：用 ZIP 导入 `examples/mods` 的两个示例功能包（`hello-local`、`echo-worker`）时**操作未完成，报 `worker_probe_failed`**；同一内容用「导入目录」正常运行；**角色资源包走 ZIP 正常**。本轮**只记录、不修复**。
- 失败阶段：探针（`feature_package_transactions._continue` 的 `self_checker.check`），不是打包 / 校验 / 落盘。`worker_probe_failed` 只可能来自 host-worker 探针分支（`feature_probe_adapter._run_attempt`），host-only 走 `worker_status="not_applicable"` 提前返回、正常不产生该 reason，故 `hello-local` 的准确 reason 待单独复现确认。
- 未复现原因已定位：探针沙箱只在冻结版创建（`feature_management.py` 要求 `sys.frozen` 且存在 `feature-probe` bundle）；开发 / offscreen 下目录与 ZIP 都只得 `self_check_sandbox_unavailable`，差异被掩盖。
- 本地 offscreen 探针（替换 `self_checker` 桩，仅作边界证据，不能证明缺陷不存在）：根目录 ZIP 功能包 / 角色包与目录接入均导入 + 启用成功；**ZIP 内多套一层文件夹**判 `导入失败：…manifest missing`（`_read_zip_manifest` 只认压缩包根的 `manifest.json`），属另一个独立问题。
- 证据：`repro-zip-enable.json`、`repro-zip-wrapper.json`；脚本 `repro_zip_enable.py`、`repro_zip_wrapper.py`（临时探针，不随产品发布）。
- 缺陷已落档四处文档：`docs/modding/USER-GUIDE.md` 常见问题、`docs/modding/README.md` §四、实施计划「已知未解决缺陷」、PR 报告「已知未通过/未完成门」第 5 条。
- `docs/modding/*` 教程此前未登记 `docs/INDEX.md`，本轮按入场规则补登记；入口扩写为「加 MOD / 做 MOD」分流 + 按类型教程 + v1 接口 + 两个官方 DLC 案例。
- 纯文档 / 记录改动（无产品行为变化），按 AGENTS.md 走聚焦门；然后按用户当轮授权推送到 `origin/codex/phase3-worker` 并核对远端 SHA。
- **未解决**：ZIP 根因定位与修复；待用户补充 ZIP 来源与打包工具、包类型、界面报错文案。
