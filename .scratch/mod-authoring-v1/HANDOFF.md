# 交接

2026-10-10 15:18；[设计](../../docs/modding/MOD-CENTER-IMPLEMENTATION-PLAN-2026-10-10.md)。
M00 已提交/正常推送并核对 `0a299612714e5fad55a24a5506dfce938b9eeb1c`；M01 后新功能未提交/推送。

## 当前停点
M05：修复高负载首次发现与事务并发恢复；最近命令 `pytest -q tests/test_feature_transaction_live_owner.py tests/test_feature_package_transactions.py tests/test_feature_package_startup.py tests/test_feature_management.py`，输出 `operation-owner-green.log`，开始于15:16左右。先核对进程和结果，不能重复启动。

1. `live-startup-red2.log` 2 failed →修复 `FeatureManagementRuntime._retry_bootstrap` 允许startup未创建时沿同一加载路径重试；`live-startup-green.log` 14 passed/1 failed，剩余journal竞态，不是仍缺重试。
2. `operation-owner-red.log` 1 failed：实际子进程恢复了仍由安装者执行的事务。`feature_package_transactions.py` 为 apply/recover/confirm/failure/GC 增加短生命周期非阻塞operation.lock，读锁和Qt生命周期回调不持有该锁。
3. 当前源码未构建。先通过相关测试、跨进程负载，再全收集全量+三轮满CPU。旧full11/高负载和Qt崩溃记录保留。
4. final3历史审计通过；最终包/生产Worker在 `_m05b/final`，probe在p2。对比闭合源码后决定重建范围。Core修改至少需要重建Core/Setup；保留 `_s01b`4.2.4，不修改Setup逻辑。
5. `_m05b/runtime3`所有之前自有桌面程序均自然退出，保留data，含AI/识屏1.0.4。新候选冻结复验仅在这里进行，不碰 `E:\dsh-pet-core-webm`。
6. 最后完成逐文件numstat、性能/Windows记录、教程产物链接与五份阶段记录。只有M00有推送授权，不再提交推送。

## 实际效果与限制
不以旧通过覆盖新失败。生成物已约7.84GiB，比约5GiB估计大；不擅自删除，不继续复制无关媒体。若仍失败先定位，不堆重试次数。

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

- 用户报告：ZIP 接入的外接 MOD 无法启用，同一内容走路径（导入目录）正常。本轮**只记录、不修复**。
- 本地探针（offscreen Qt + 真实 `ModCenterController` / 真实包校验与事务；本机无 OS 沙箱，功能包按现有测试只替换 `self_checker`）：根目录 ZIP 的功能包与角色包导入 + 启用都成功，与目录接入一致；**只有 ZIP 内多套一层文件夹时**判 `导入失败：…manifest missing`（解压后选内层目录正常）。「导入成功但点启用失败」**未复现**。
- 证据：`.scratch/mod-authoring-v1/repro-zip-enable.json`、`repro-zip-wrapper.json`；脚本 `repro_zip_enable.py`、`repro_zip_wrapper.py`。
- 缺陷已落档：`docs/modding/USER-GUIDE.md`、`docs/modding/README.md` §四、实施计划「已知未解决缺陷」、PR 报告「已知未通过/未完成门」第 5 条。
- 下一步（未完成）：① 等用户补充 ZIP 来源与打包工具、包类型、界面报错文案后复现并定位根因；② 若确认是「压缩包根必须放 `manifest.json`」这一差异，再决定是让 ZIP 路径容忍外层文件夹还是只改提示文案——**本轮不修**，不得表述为已修复。
- 教程文档本轮已补登记 `docs/INDEX.md`；入口扩写完成。按用户当轮授权推送到 `origin/codex/phase3-worker` 并核对远端 SHA。
