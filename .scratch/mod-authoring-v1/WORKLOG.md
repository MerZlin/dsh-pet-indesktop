# 工作日志

基线：2026-10-10；[设计](../../docs/modding/MOD-CENTER-IMPLEMENTATION-PLAN-2026-10-10.md)。M00 远程检查点 `0a299612714e5fad55a24a5506dfce938b9eeb1c`。新功能尚未提交/推送。

## 2026-10-10 M00 → M01

先审查 159 个明确文件，排除 13 个生成目录。Ruff、全量、3×高负载、敏感检查、文档索引与 staged diff 均通过；提交 `0a29961` 正常推送至 origin/codex/phase3-worker 并核对完整 SHA。随后才创建新设计与五份记录；尚未修改 MOD 产品行为。

## 实际效果与限制
已建立可恢复源码检查点，未发布新功能。

## M01 / M02 初始接缝
- 先运行新增契约回归：7 failed / 3 passed（m01-red.log）；通用 runtime 挂载单独 red：1 failed / 1 passed（mount-red.log）。
- 已实现可选展示元数据、导入默认停用参数、资源启停、v1 声明与通用 runtime 初稿。
- 基础回归 106 passed / 1 warning / 274.45s；该轮不涵盖其后新写的挂载与目录通知，不能代表整个 MOD 功能通过。
- UI、跨进程角色释放、教程及冻结交付仍待实现，未提交/推送。

## M02–M04 进行中
统一列表、实时设置接入与角色跨进程释放已实现。基础 106、契约 11、UI 4、Worker 2、完整导入 2 项通过。旧 UI 断言已按真实新界面调整；集成剩余 2 个窄屏缺陷已定向修复。新功能全量/冻结尚未开始，教程待补。

## M04–M05 首轮冻结验证（2026-10-10）
- 两个离线样例、角色副本生成器及九份分类教程已落盘；副本/ZIP 仅在测试目录生成，不重复提交媒体。
- 首轮 Core/Worker/两个 DLC/Setup 构建成功（build-receipt.json），但不构成交付通过。
- 冻结 Core 启动揭示 AppShell 不是 QObject：shell-parent-red.log 1 failed，CoreModSupport 自持 QObject 后 shell-parent-green.log 1 passed。
- 冻结导入揭示 probe PYZ 缺少 pet.mod_api.v1；旧 archive 真实断言失败保留 probe-archive-red.log。补充显式收集和硬门。
- 通用 Worker 探针身份、无业务/无租约探针和受阻操作收尾补充 frozen-gaps-red.log：4 failed/14 passed；修复及 native 重建进行中。
- 首轮全量 full-1.log 在约58%原生退出 -1073741819（test_music_sing_timer），此前失败未得到最终汇总；不宣称通过。已改用逐测试 JSONL 留证，第二轮诊断执行中。部分失败确定为候选版本断言仍指向4.2.4/1.0.3。
- 新功能未提交、未推送；旧4.2.4候选和真实安装未改动。

- full-2 验证脚本缺少 `__main__` 保护，被 Windows multiprocessing 重复执行，污染同一临时目录；该轮作废。已仅终止所属测试 runner 21348（无存活子进程），修复脚本后使用 tmp-full-3 独立目录重跑。此问题属于测试执行脚本，不计为产品失败。首个 architecture 行数门确实失败，已把释放贡献逻辑移入既有 settings_feature_lifecycle，未增加预算。

## 2026-10-10 M05 冻结链路与全量缺口
- 修复源与冻结 AppShell 非 QObject parent、probe 缺公开 SDK、通用 Worker probe/native 输入契约；真实 Windows native probe hello/Echo 通过。
- c2 冻结 Core + 独立设置：host-only 从 ZIP 导入默认停用，点击启用后菜单/设置即时出现，离线消息真实弹窗。UIA 留证，不读取真实桌面或 Key。
- usability-red / lease-red / qt-life-red / echo-ui-red 保留：重复资源提示、设置未展开、锁竞争、非官方 Worker 租约、已删除对话框晚到回调、样例静默启动失败。修复按公共 seam 验证。
- full-3 原生 UI 旧选择器失败，改为检查新列表，不降低16组合原生UI门；16 passed。full-4 在运行中触发 QObject use-after-delete；不是绿色。保留事件和原生栈。
- 未覆盖旧构建，未修改真实安装，未再次提交/推送。

### 2026-10-10 c3 冻结闭环与全量诊断
Echo真实Worker返回成功；角色副本启用不自动换装，使用后日志素材路径在副本内，停用恢复内置。full-5到79%原生崩溃，四个行为失败继续修复；不能计作全量通过。标题4px误差复现为scrollbar布局晚一事件，搜索同步改为只在行集变化时刷新，重复列表不再销毁重建。隔离设置族25 passed/19.78s。

## 2026-10-10 13:58 M05 最终复验
- 冻结 c4 捕获元数据原子替换 WinError5；真实 Windows 读句柄红灯1 failed/2 passed，有限250ms重试后相关187 passed/124.02s。永久占用不绕过。
- 旧角色测试替身补shutdown并验证目标/其他实例释放边界，39 passed。
- full8 4562节点分进程全量进行中；final Worker/Core/Echo已构建；Setup与三轮负载待闭合。runtime3所有测试进程自然退出。

## 2026-10-10 14:30 M05 最终复验
- final2 冻结 Core：Echo 新导入默认停用、启用、实际 Worker HELLO/READY/返回通过；旧接受删除在下一次 Core 启动完成物理清理。
- 新鲜 Echo 删除现场没有接受事务，原始具体失败码未捕获；Worker 退出时租约 owner 变化是已覆盖的可能竞态，不宣称已证明现场唯一原因。发现单项结果被刷新改回“正在处理”。3 个红灯后修正单项摘要、同计划有限重试与未接受/已接受等待的区别，相关 32 passed。此前症状不宣称通过。
- full9 第6组原生 Qt access violation（主动识屏集成 test event loop），保留失败日志；改 full10 完整4568节点，主动 Worker 文件独立进程，不跳过节点。full9 等待器结束不启动负载。
- final3 仅重建 Core/Setup；final 生产Worker、两个1.0.4包与Echo闭合输入未变，复用并待哈希审计。高负载三轮等待full10。

## 2026-10-10 14:42 最终冻结与全收集结果
- full10 跨文件Qt原生崩溃，不计通过。full11改为273个完整文件各一个新进程（同时2文件），4568节点全部覆盖：4553 passed/15 skipped，675.438s，exit0，输入摘要稳定、零缺失。没有修改测试跳过条件。
- final3 Core/Setup225.594/148.453s；242源码、2114Core文件、两个ZIP全部条目哈希审核通过。最终Core与Setup资源中的鲸鱼10帧与源图标匹配；4.2.4Setup不变。
- runtime3 final3真实Core+独立设置：Echo实际返回；Worker退出码0；删除已接受并显示待退出；Core/设置自然exit0后，下一次Core启动物理清空受管版本。外部ZIP/源manifest/config/个人测试文件摘要不变。
- 同一冻结Core安装AI/识屏1.0.4成功，生产Worker starting→handshaking→ready→stopping→stopped。租约READY占用、退出free，Core/Worker自然exit0；假Key删除，0HTTP、不截图。
- 三轮高负载run_highload11进行中。最终Ruff发现测试文件一个I001空行格式，待当前负载门完成后仅格式修正、验证AST不变并定向复验。产品源码不再变更。
- `_m05b`生成物实测8,416,750,763字节（约7.84GiB），比最初约5GiB估计大；保留诊断/旧候选，不继续构建也不擅自清理。E盘约151GiB可用。

## 2026-10-10 15:18 中断恢复与并发根因
- highload11首次发现失败，CPU中位77.6%不足；正常优先级20负载CPU100%复现，所有负载进程自然退出。旧首次发现state-history停在lock_busy。
- 新增真实管理锁/状态锁回归2红；允许startup不存在时同路径有界重试。第一次复验14通过1失败，日志确认是revision_conflict，非原锁重试仍坏。
- journal期望revision2而真实state已revision3：设置安装处于生命周期等待时，Core恢复并推进同一事务。新增真实子进程重现1红；增加非阻塞operation.lock跨越一次操作，保留原metadata/lease短锁，相关测试进行中。
- 旧全量/native/audit通过保留为final3历史证据；最新源码不能沿用。Ruff通过。已更新五份准确停点。

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

- 用户报告 ZIP 接入的外接 MOD 无法启用、路径接入正常。按「只记录不修复」处理。
- 探针命令：`QT_QPA_PLATFORM=offscreen python -X utf8 .scratch/mod-authoring-v1/repro_zip_enable.py`（4 场景）与 `... repro_zip_wrapper.py`（2 场景）。结果见同目录 `repro-zip-enable.json` / `repro-zip-wrapper.json`。
- 实测：根目录 ZIP 功能包（`scripts.build_mod_example` 产物）与角色包（`scripts.build_character_mod_example` 产物）导入 + 启用均成功，与目录接入无差异；带外层文件夹的 ZIP 两类型都判 `manifest missing` 导入失败。未复现「导入成功但启用失败」。
- 无沙箱环境下功能包若不打 `self_checker` 桩，目录与 ZIP 都同样报 `self_check_sandbox_unavailable` 族失败——这是本机限制，不是 ZIP 差异。
- 文档：`docs/modding/README.md` 扩写为使用/制作分流入口；`USER-GUIDE.md` 加 FAQ；实施计划加「已知未解决缺陷」；PR 报告加第 5 条未过门；`docs/INDEX.md` 补登记 modding 教程 5 行。
- 本轮不改产品代码、不改 `tests/`、不改 `examples/mods/` 源码；探针脚本与 JSON 留在 `.scratch`，不进产品与测试产物。
- 提交并推送到 `origin/codex/phase3-worker`（用户表述的 phase3A-worker 即该 Phase 3A worker 分支）。
