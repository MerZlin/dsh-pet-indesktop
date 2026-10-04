# Phase 4B Windows 连续收尾工程报告（2026-10-04）

> 完成结论：Phase 4B的本机Windows工程自动化闭环已通过；本人实机/其他平台/正式信任与发布门单列，不冒充验收。基线 `codex/phase3-worker@bd048d57518902532ea82b6b4ba277e79b16871a`，包含保留原WIP与本轮修改；初次工程封存时源码未暂存、未提交、未推送、未发布（历史事实）。2026-10-04 用户已授权提交推送，最新状态见下方「本次分支发布复验」，不能从旧封存口径推断当前发布状态。2026-10-03中间检查与旧Core不是本报告最终门。

关联：[总设计](plugin-phase-04-updates/PHASE4B-LOCAL-MANAGEMENT-DESIGN.md) · [事务设计](plugin-phase-04-updates/PHASE4B-3-LOCAL-TRANSACTIONS-DESIGN.md) · [计划](../.scratch/phase4b-local-management/PLAN.md) · [准确交接](../.scratch/phase4b-local-management/HANDOFF.md) · [状态](../.scratch/phase4b-local-management/STATUS.md) · [历史事务检查](PR-REPORT-FEATURE-PACKAGE-TRANSACTIONS-2026-10-03.md) · [索引](INDEX.md)。

## 一、结论、目标与范围

实现目标：官方 `official.screen-understanding` 的本地目录/ZIP 安装、升级、启停、retained previous 主动回滚、卸载、崩溃恢复和延迟 GC；实际设置中的“常规 / 扩展管理”；LPAC 隔离安装自检；带聊天/不带聊天的无识屏 Core 真冻结验收。

不包含 Setup、远程下载、第三方包、用户数据迁移、个人数据清空或正式发布。默认内置识屏 Core 的既有行为保留，“内置功能”不提供假的物理卸载。生产信任锚仍 fail-closed；验证构建的内存生成测试私钥和标记测试公钥不代表正式分发信任策略已交付。

## 二、修改文件说明

<!-- FILES_START -->
本表对用户已授权的 85 文件白名单逐项使用 `git diff --numstat -- <path>`；未跟踪文本使用 `git diff --no-index --numstat -- NUL <path>`。基线为 `bd048d5`，包含原有 WIP，**不是声称全部行由本次推送任务新写**。生成物、原始日志、截图、安装包、私钥与用户数据不入库；无删除文件。当前数字按本次发布留档复算，后续仅记录发布结果的 Markdown 变更单列于 Git 提交。

| 文件（仓库相对路径） | 类型 | 增删行 | 改了什么 + 为什么 |
|---|---|---|---|
| `.gitignore` | 修改 | +9 / -1 | 只显式开放同组 WORKLOG/SUMMARY Markdown 记录；构建、日志、私钥及生成用户数据继续忽略。 |
| `.scratch/phase4b-local-management/HANDOFF.md` | 修改 | +287 / -1 | 同组准确停点/下一命令、源快照与最终交接。 |
| `.scratch/phase4b-local-management/PLAN.md` | 修改 | +320 / -1 | 同组稳定任务编号/依赖/门与勾选状态，不另建计划。 |
| `.scratch/phase4b-local-management/STATUS.md` | 修改 | +300 / -0 | 当前实现/自动化/实机/人工/提交/远程状态严格区分。 |
| `.scratch/phase4b-local-management/SUMMARY.md` | 新增 | +389 / -0 | 跨对话有效决策、保护边界、源改动及下一步。 |
| `.scratch/phase4b-local-management/WORKLOG.md` | 新增 | +969 / -0 | 保留连续实施故障、根因及日期证据；普通失败不导致掩盖或重建记录。 |
| `LOG-INDEX.md` | 修改 | +4 / -0 | 给本轮事实日志增加日期/主题入口，避免交接找不到。 |
| `LOG.md` | 修改 | +84 / -0 | 保存本轮实施和验收事实，不覆盖既有日期的历史成果。 |
| `README.md` | 修改 | +9 / -5 | 修正尚未开始的过时状态，链接本轮验收/发布限制；避免把验证构建当正式安装器。 |
| `SPEC.md` | 修改 | +4 / -4 | 同步已获确认的Phase4B完成口径和边界，正式信任/人工/发布验收仍单列，不改变个人数据保留合同。 |
| `docs/INDEX.md` | 修改 | +6 / -0 | 登记事务设计、连续收尾报告与同组记录，提供唯一文档入口。 |
| `docs/PR-REPORT-FEATURE-PACKAGE-TRANSACTIONS-2026-10-03.md` | 新增 | +127 / -0 | 本阶段设计/交付证据与限制，明确当前或历史快照，关联唯一索引和同组任务。 |
| `docs/PR-REPORT-PHASE4B-MANAGEMENT-CLOSEOUT-2026-10-04.md` | 新增 | +293 / -0 | 本阶段设计/交付证据与限制，明确当前或历史快照，关联唯一索引和同组任务。 |
| `docs/PR-REPORT-PHASE4B-MANUAL-ACCEPTANCE-2026-10-04.md` | 新增 | +288 / -0 | 真实用户验收与生成物清理、公开账本证据；将通过项、未测人工门和待诊断问题区分，避免自动化代替本人体验。 |
| `docs/PROJECT-ENTRY.md` | 修改 | +7 / -3 | 同步工程停点及信任/人工门，历史中间快照不代替最新闭环。 |
| `docs/plugin-phase-04-updates/PHASE4B-3-LOCAL-TRANSACTIONS-DESIGN.md` | 新增 | +229 / -0 | 本阶段设计/交付证据与限制，明确当前或历史快照，关联唯一索引和同组任务。 |
| `docs/plugin-phase-04-updates/PHASE4B-LOCAL-MANAGEMENT-DESIGN.md` | 修改 | +186 / -1 | 本阶段设计/交付证据与限制，明确当前或历史快照，关联唯一索引和同组任务。 |
| `features/screen_understanding/host/contribution_settings.py` | 修改 | +30 / -1 | 公开草稿 dirty/save/discard 生命周期；discard 不写配置或凭据，不自动保存草稿。 |
| `features/screen_understanding/host/factory.py` | 修改 | +6 / -3 | 合法工厂定义/配置入口与事务生命周期边界对齐，不让安装预检执行 factory。 |
| `packaging/licenses/LIBSODIUM-LICENSE.txt` | 新增 | +18 / -0 | 随独立 trusted helper 分发的 upstream 许可/来源声明，保留改造与再分发责任。 |
| `packaging/phase4b_manual_entry.py` | 新增 | +55 / -0 | 专用真实人工验收入口，禁用验证用截图/网络替换；使用正常生产 bootstrap 和安全存储。 |
| `packaging/phase4b_validation_boundaries.py` | 新增 | +73 / -0 | 明确标记生成图像/本地 HTTP边界；不替换工厂、事务、租约、Qt/进程和状态实现。 |
| `packaging/phase4b_validation_entry.py` | 新增 | +492 / -0 | 真实 AppShell/settings/管理 UI 驱动、受限 quota reply、实际生产 receipt 与 passive 性能观察；仅显式 validation frozen 生效。 |
| `pet/app.py` | 修改 | +7 / -0 | AppShell 在窗口/功能贡献初始化之前接入生产管理 bootstrap，恢复先于候选导入。 |
| `pet/feature_build_policy.py` | 新增 | +13 / -0 | Core-owned 空正式 trust anchors / pinned helper 策略；不从用户环境注入信任。 |
| `pet/feature_install_state.py` | 修改 | +19 / -8 | resolver 增加 execution/configuration/startup 用途与受 permit 约束的 pending 加载；账本仍唯一 authority。 |
| `pet/feature_lifecycle.py` | 新增 | +105 / -0 | queued GUI 准备、草稿保护、拒绝任务/撤销贡献/停止所属 Worker；不让后台访问 Qt 对象。 |
| `pet/feature_lifecycle_contract.py` | 新增 | +80 / -0 | 有界 operation/revision/live-owner/nonce 请求和持久化授权；不能成为第二状态 authority。 |
| `pet/feature_lifecycle_ipc.py` | 新增 | +181 / -0 | same-user 有界 QLocal 跨进程准备、意图/owner 验证、有限连接与帧；回执不当租约证明。 |
| `pet/feature_management.py` | 新增 | +184 / -0 | 真实 bootstrap 与后台服务 facade、closed 窗口回调保护；monitor 延后到初始 factory/端口准备后启动。 |
| `pet/feature_management_ui.py` | 新增 | +338 / -0 | 常规域目录/ZIP确认、启停/回滚/卸载/草稿/重试、内置保护与响应式可达布局；只改显示换行不改 immutable token。 |
| `pet/feature_package_files.py` | 新增 | +256 / -0 | 同卷目录/ZIP staging、大小/重复/大小写/链接安全检查与本 operation 自有目录清理。 |
| `pet/feature_package_probe.py` | 新增 | +129 / -0 | 强制可信 OS 隔离的 host/Worker 自检合同；不把同用户 subprocess 当沙箱或允许 fallback。 |
| `pet/feature_package_startup.py` | 新增 | +215 / -0 | 生产 factory/端口/租约产生 receipt、candidate/previous 加载确认、运行授权刷新；创建安全自有 runtime。 |
| `pet/feature_package_transactions.py` | 新增 | +1123 / -0 | immutable plans、确认/revision/source CAS、写前 journal、不可覆盖版本切换、租约等待、卸载恢复和安全 GC。 |
| `pet/feature_probe_adapter.py` | 新增 | +136 / -0 | operation-owned sealed helper/candidate 副本、host/Worker 父端分别启动、隔离证据与自有恢复/清理。 |
| `pet/feature_probe_crypto.py` | 新增 | +41 / -0 | 只适配 pinned upstream libsodium 验签以移除 GUI 依赖；不另造密码学或降低正式 trust policy。 |
| `pet/feature_probe_windows.py` | 新增 | +750 / -0 | public ctypes Win32 LPAC/ACL/Job/mitigations/stdio handles、父端真实属性检查、bounded I/O与独立清理；fail-closed。 |
| `pet/feature_startup_contract.py` | 新增 | +44 / -0 | 封闭 StartupLoadPermit/Receipt、事务/版本/hash/revision/进程/租约证据绑定；UI success bool 不能清 pending。 |
| `pet/feature_state_io.py` | 修改 | +33 / -19 | Windows/POSIX 共享读取、排他 CAS 内核锁及有界提交等待；杜绝 monitor 与设置只读竞争误拒。 |
| `pet/feature_version_lease.py` | 修改 | +93 / -53 | 配置/加载确认租约、revision 授权刷新及冻结入口交接；保持 kernel lease 为删除证据。 |
| `pet/modern_settings_dialog.py` | 修改 | +51 / -2 | 在常规域接入扩展管理和稳定深链/搜索；独立管理不启动完整桌宠或识屏 Worker。 |
| `pet/plugins/feature_host.py` | 修改 | +15 / -3 | 增加 authority、prepare-release 与实际贡献撤销边界；保持 Qt owning thread 与导入 pin。 |
| `pet/plugins/feature_packages.py` | 修改 | +14 / -3 | 合法 FeatureDefinition 与完整 verifier 后的实际加载；lease/关闭失败不假装安全。 |
| `pet/plugins/package_binding.py` | 修改 | +30 / -5 | 按用途绑定 host/settings/Worker 授权与 generation，设置租约随 QObject 销毁，不允许热替换。 |
| `pet/plugins/package_trust.py` | 修改 | +54 / -14 | 长 Windows local path 仅在 stat/scandir/open seam 用 extended-path，保留逻辑 authority 和 no-follow 祖先检查。 |
| `pet/workers/screen_entry.py` | 新增 | +58 / -0 | 正常冻结入口在执行模块导入前接管4B-2租约；无交接只允许被真实沙箱证明的最小 probe 协议。 |
| `scripts/build_feature_management_delivery.py` | 新增 | +111 / -0 | 内存私钥生成签名版本/失败候选、带/不带聊天双冻结验证 Core；重验 Worker 输入摘要。 |
| `scripts/build_feature_management_manual.py` | 新增 | +92 / -0 | 重建带聊天/不带聊天的真实人工 Core，并封存源/产物摘要；明确临时测试信任，不伪装正式发行。 |
| `scripts/build_feature_probe.py` | 新增 | +329 / -0 | 构建 GUI-free onedir helper、已拥有 PE 材料激活资源处理/longPathAware 资源、清单摘要/许可证；不改安装的 Python。 |
| `scripts/build_feature_probe_native.py` | 新增 | +164 / -0 | pinned native bootloader/extension 构建封装；显式导出来源与安全 build 条件。 |
| `scripts/build_screen_delivery.py` | 修改 | +91 / -16 | 新无识屏 Core 源闭包/标记 trust policy/helper 收集及 PYZ/原生依赖审计；不借仓库源码救场。 |
| `scripts/build_screen_worker.py` | 修改 | +62 / -7 | 冻结 Worker 入口与 lease bootstrap、独立原生材料、minimal longPathAware 资源；测试/正常 Worker 源闭包区分。 |
| `scripts/feature_probe_canary.py` | 新增 | +124 / -0 | 仅生成夹具的正常正对照与隔离权限测试，禁止真实用户凭据枚举/桌面截图。 |
| `scripts/feature_probe_entry.py` | 新增 | +124 / -0 | 可信冻结 helper 验签后的 host factory 自检和受限诊断；不传候选异常/真实秘密。 |
| `scripts/feature_probe_native_canary.c` | 新增 | +55 / -0 | 原生 canary 验证 LPAC/文件/凭据/网络/桌面/进程负权限，避免 Python 层模拟安全边界。 |
| `scripts/feature_probe_native_extension.c` | 新增 | +121 / -0 | 可信父端/探针的最小 native 权限查询；候选自报 isolation_enforced 不被采信。 |
| `scripts/measure_feature_management_delivery.py` | 新增 | +217 / -0 | 可复现阶段聚合/有界 idle/20 CPU子进程三遍时序族；只观察生成验证进程，报告样本/p95/IO/RSS边界。 |
| `scripts/validate_feature_management_delivery.py` | 新增 | +226 / -0 | 严格双 Core 七行生产/UI控制；pending不冒充成功，生成 owner 只按 stdin 正常退出。 |
| `scripts/validate_feature_probe_windows.py` | 新增 | +288 / -0 | 真实 Windows 完整 canary 与清理/超时/异常门，所有测试材料 operation-owned。 |
| `tests/_feature_lifecycle_child.py` | 新增 | +43 / -0 | 真实 lifecycle 子进程验收入口；生成夹具和事件同步，不用 mock 代替跨进程/Qt 边界。 |
| `tests/_feature_startup_child.py` | 新增 | +100 / -0 | 真实 startup 子进程验收入口；生成夹具和事件同步，不用 mock 代替跨进程/Qt 边界。 |
| `tests/_feature_ui_child.py` | 新增 | +126 / -0 | 真实 ui 子进程验收入口；生成夹具和事件同步，不用 mock 代替跨进程/Qt 边界。 |
| `tests/test_feature_install_state.py` | 修改 | +49 / -0 | 增加公开 seam/失败路径回归：pending用途与sealed permit、旧 revision不覆盖；先红后绿，防止安全门退化。 |
| `tests/test_feature_lifecycle.py` | 新增 | +88 / -0 | 增加公开 seam/失败路径回归：真实事件循环queued准备、草稿、贡献撤销和 Worker停止；先红后绿，防止安全门退化。 |
| `tests/test_feature_lifecycle_ipc.py` | 新增 | +115 / -0 | 增加公开 seam/失败路径回归：真实跨进程 same-user 请求、nonce/owner/revision/边界；先红后绿，防止安全门退化。 |
| `tests/test_feature_management.py` | 新增 | +84 / -0 | 增加公开 seam/失败路径回归：生产bootstrap/异步后台/关闭回调/内置保护；先红后绿，防止安全门退化。 |
| `tests/test_feature_management_build.py` | 新增 | +92 / -0 | 增加公开 seam/失败路径回归：双变体和标记验证策略，不复用过期源码；先红后绿，防止安全门退化。 |
| `tests/test_feature_management_delivery_validation.py` | 新增 | +171 / -0 | 增加公开 seam/失败路径回归：严格 pending/receipt证据、actual method观察、quota与boundedretry合同；先红后绿，防止安全门退化。 |
| `tests/test_feature_management_native_ui.py` | 新增 | +42 / -0 | 增加公开 seam/失败路径回归：16原生Windows HiDPI/主题/语言/宽度组合、键盘和OWN widget截图；先红后绿，防止安全门退化。 |
| `tests/test_feature_management_performance.py` | 新增 | +44 / -0 | 增加公开 seam/失败路径回归：sample median/nearest-rank p95、nested/error/IO/RSS汇总合同；先红后绿，防止安全门退化。 |
| `tests/test_feature_management_ui.py` | 新增 | +577 / -0 | 增加公开 seam/失败路径回归：实际服务/queued窗口、布局/深链/可达/内置保护和真实kernel锁重试；先红后绿，防止安全门退化。 |
| `tests/test_feature_manual_acceptance.py` | 新增 | +187 / -0 | 人工构建无合成边界、源快照/租约与启动合同回归，防止将验证产物用于真实体验。 |
| `tests/test_feature_package_probe.py` | 新增 | +172 / -0 | 增加公开 seam/失败路径回归：无OS边界拒绝、自检顺序/worker握手、不允许普通subprocess回退；先红后绿，防止安全门退化。 |
| `tests/test_feature_package_startup.py` | 新增 | +186 / -0 | 增加公开 seam/失败路径回归：真实生产receipt/失败候选/重启previous确认、runtime路径与DLL借用边界；先红后绿，防止安全门退化。 |
| `tests/test_feature_package_transactions.py` | 新增 | +1117 / -0 | 增加公开 seam/失败路径回归：全阶段故障/CAS/安全ZIP/staging/锁/租约/卸载/GC/实际Windows占用及ACL失败；先红后绿，防止安全门退化。 |
| `tests/test_feature_packages.py` | 修改 | +44 / -0 | 增加公开 seam/失败路径回归：长路径I/O/stat/scan/open、logical authority与验签后执行；先红后绿，防止安全门退化。 |
| `tests/test_feature_probe_adapter.py` | 新增 | +129 / -0 | 增加公开 seam/失败路径回归：自有sealed拷贝/父端隔离证据、probe恢复和不越界清理；先红后绿，防止安全门退化。 |
| `tests/test_feature_probe_build.py` | 新增 | +173 / -0 | 增加公开 seam/失败路径回归：无GUI依赖/manifest资源/overlay完整性及可信材料摘要；先红后绿，防止安全门退化。 |
| `tests/test_feature_probe_windows.py` | 新增 | +243 / -0 | 增加公开 seam/失败路径回归：nativeLPAC权限门/句柄/Job/token/timeout/output/crash/cleanup；先红后绿，防止安全门退化。 |
| `tests/test_feature_settings_ports.py` | 修改 | +16 / -0 | 增加公开 seam/失败路径回归：未保存草稿discard不落盘/不改凭据；先红后绿，防止安全门退化。 |
| `tests/test_screen_delivery_build.py` | 修改 | +17 / -0 | 增加公开 seam/失败路径回归：无识屏 Core/可信helper/源码闭包与资源审计；先红后绿，防止安全门退化。 |
| `tests/test_screen_worker_build.py` | 修改 | +32 / -1 | 增加公开 seam/失败路径回归：冻结Worker租约入口与完整独立材料；先红后绿，防止安全门退化。 |
| `tests/test_screen_worker_entry.py` | 新增 | +75 / -0 | 增加公开 seam/失败路径回归：冻结正常租约接管必须先于执行模块导入与受限probe入口；先红后绿，防止安全门退化。 |
| `tests/test_session_end_ffmpeg_guard.py` | 修改 | +15 / -5 | 已有 metadata 正对照用 Event 等待异步公共 seam，并在 finally 清理；修复 CI 时序猜测，不改产品动画逻辑。 |
<!-- FILES_END -->

## 三、设计取舍与根因修正

- `state.json` 是唯一安装 authority；journal 写前意图、revision CAS、独立子提交 ID、恢复 before-image，不从目录枚举猜安装状态。已接受卸载永不因恢复而启用。
- Qt 对象保持 owning thread；后台事务通过 queued 生命周期请求拒绝任务、撤销 generation/贡献、保护草稿和停止 Worker。IPC 回执不是租约释放证据；内核租约始终是删除与切换的门。
- factory 执行前完成完整 verifier；Core-owned helper 和 Worker 由可信父端用 LPAC/空 capabilities/Win32k disable/suspended+Job 启动，父端检查真实 token/Job/缓解策略后恢复线程。单进程、512 MiB、30 秒默认预算和有界协议；失败不退回普通同用户 subprocess。
- StartupLoadPermit 仅允许事务指定 host/settings 的非执行性生产加载；实际生产 factory/端口/租约生成封闭 receipt。pending 期间普通执行 resolver 禁止执行；失败候选/回滚自身都要求真实加载确认。导入过 host 的进程自然退出，不热换 Python 代码。
- Windows 长路径失败的最初“补 EXE manifest”假设不足：真实 LPAC `_checked_stat` 报 WinError3、262字符候选 DLL。公开 verifier 的 stat/scandir/open seam 回归先红3项；修复只对已校验绝对 local drive 的长路径使用 extended-path I/O，逻辑 root/manifest/祖先 identity 不变，不改系统长路径策略、不缩短验证目录、不放宽 ACL。
- 真实冻结 Worker 反向额度请求是 `budget_check`，并要求 operation/generation/parent request 匹配；验证入口曾用错反向协议，已按真实 supervisor 合同回归修复。生产额度实现未改。
- 无聊天冻结卸载遇到明确 lock_busy，安全拒绝本身正确；真实 UI 曾丢失已确认、尚未接受 plan，导致重试只查空 pending。回归以真实 kernel lock 先红后绿，重试保留同一 immutable plan/token，并重新走服务全部检查。验证入口只通过真实重试按钮最多重试3次，不制造成功回执、不隐藏失败。
- 等待升级时 `enabled` 保留用户意图；实际执行由 pending 和撤销后的 generation 拒绝，不能把这一合法状态误判为卸载的 `enabled=False` 合同。
- 全量05出现测试客户端线程没有处理 Windows Qt queued pipeClosed 的失败（1 failed / 3820 passed / 13 skipped / 15 warnings，611.56s）。同线程真实事件处理立即发出断线，证明不是生产帧上限失效；将测试改为 QEventLoop＋断线信号，不扩大15s预算、不修改产品64KiB限制。相关5 passed（9.19s），高负载第一遍也通过；最终全量仍要重新验证，不能引用旧全量04代替。

## 四、性能分析

环境：本机 Windows build 26100，64 位；`platform.platform()` 返回 `Windows-10-10.0.26100-SP0`（不是按该字符串推断发行名称）。Python 3.11.1，PySide6 6.11.1，PyInstaller 6.20.0，psutil 6.1.0；20 logical / 14 physical CPU。

复现入口：`python -m scripts.measure_feature_management_delivery phases|idle|stress ...`。阶段观察器仅编入明确标记的 validation-only 冻结入口，不进入正常 release。原方法调用一次，返回/异常原样传递；观察自身500样本 median **3.498ms** / nearest-rank p95 **5.373ms**。短路径必须计入这一测量开销，不直接减掉校准数字。

### 阶段实测（最新 Core10 双矩阵）

两种构建分别完成27个真实步骤，非CPU压力运行；表为合并原方法观测样本，nearest-rank p95。`apply` 混合安装/升级/回滚/拒绝与等待，不能当单次安装承诺。每个探针进程30s预算不含父端复制/哈希/ACL/恢复清理，所以父端沙箱方法总耗时可以超过30s。

| 原方法 | N | 耗时 median / p95（ms） | CPU median（ms） | RSS增量 median / p95（MiB） | parent读/写字节 median（MiB） |
|---|---:|---:|---:|---:|---:|
| `FeaturePackageTransactionService.apply` | 16 | 53578.689 / 89891.252 | 5460.938 | 2.547 / 6.188 | 1305.488 / 159.955 |
| `FeaturePackageTransactionService.preflight_install` | 4 | 1311.542 / 1802.189 | 1078.125 | 4.781 / 5.184 | 193.687 / 63.427 |
| `FeaturePackageTransactionService.preflight_rollback` | 2 | 1783.083 / 1968.216 | 1507.812 | 5.486 / 6.000 | 253.869 / 63.427 |
| `FeaturePackageTransactionService.preflight_uninstall` | 2 | 96.293 / 137.465 | 117.188 | 0.818 / 0.852 | 0.009 / 0.005 |
| `FeaturePackageTransactionService.preflight_upgrade` | 8 | 1539.963 / 3404.644 | 1281.250 | 4.676 / 5.734 | 253.842 / 63.427 |
| `FeaturePackageTransactionService.recover_pending` | 36 | 60.546 / 522.330 | 62.500 | 0.734 / 4.047 | 0.029 / 0.000 |
| `ProductionFeatureStartup.load_current` | 32 | 844.607 / 1148.805 | 843.750 | 3.861 / 4.660 | 380.678 / 0.000 |
| `ProductionFeatureStartup.load_pending` | 12 | 1003.196 / 1123.429 | 953.125 | 3.891 / 4.660 | 444.172 / 0.010 |
| `WindowsFeatureProbeSandbox.run` | 13 | 56874.495 / 87941.050 | 4343.750 | 2.410 / 4.500 | 1039.972 / 159.936 |

低频管理动作成本主要来自约63.43MiB候选、完整验签/多次哈希、可信 helper及其只读运行副本/ACL/子进程。非在每次识屏请求触发；升级等待重试不重复导入host。实际安全等待卸载 `apply` N3：399.436/781.902/793.382ms；自然退出后卸载恢复 N2：601.601/522.330ms；升级释放后切换恢复 N2：496.733/521.510ms。这些都仍经过真实租约/账本CAS，没有通过UI写成功回执。

真实延迟GC只处理两份生成matrix夹具的journal证明、无租约失败候选1.0.4，清理前后权威state逐字段不变。非空N2：median **411.311ms** / p95 **433.027ms**；重复无工作N4：median **110.131ms** / p95 **209.343ms**；每次线程4→4，RSS增量4KiB～592KiB。GC01因错误传入data外层，被before-image断言在删除前停止；GC02使用实际config/app-id根且匹配真实startup receipt，不按目录猜测状态。

### 冻结管理页空闲实测

每种状态3×10s，独立新进程、生成数据、stdin正常退出；采样时不运行CPU压力/冻结自检。CPU是单核百分比；RSS是进程工作集，短窗口不是长期soak。

| Core / 生成状态 | CPU median / p95（单核%） | RSS开始 median（MiB） | 10s RSS增量 median / p95（KiB） | 线程前→后 | 每10s读字节 / 写字节 |
|---|---:|---:|---:|---:|---:|
| no-chat / empty | 0.312 / 0.624 | 140.398 | 28.0 / 148.0 | 11→11 | 0 / 0 |
| no-chat / installed | 3.120 / 3.745 | 140.918 | 20.0 / 28.0 | 12→12 | 206360 / 0 |
| chat / empty | 0.312 / 0.468 | 145.879 | 20.0 / 24.0 | 13→13 | 0 / 0 |
| chat / installed | 3.435 / 3.741 | 146.184 | 20.0 / 20.0 | 14→14 | 206360 / 0 |

安装状态管理页保留既有4B-2 QFileSystemWatcher＋1s轮询兜底、一个专用monitor QThread；空状态未产生额外monitor线程。生成安装账本每10s读取480次/206360字节、无内容写入；安装态约3.12～3.44%单核CPU（20逻辑核机器约0.16～0.17%整机），不是声称零稳态开销。每个窗口最多一个事务后台线程，probe host/Worker各自一个短命沙箱进程且禁止再派子进程；本阶段无远程下载/新外网依赖，IPC使用same-user本地管道。Win32 token/Job/ACL/句柄/文件原子切换是新增低频系统调用。

新增路径读取/复制/落盘量已在上表及原始I/O counters记录；method前后线程范围6～14含Qt/加密库初始化，不能用它推断无限增长。12个空闲样本全部线程不增长、RSS增量16～148KiB；这只证明短时观测，没有做长时soak。已安装账本轮询有约3%单核成本，留作后续性能优化问题，不影响安全/事务正确性，也不在本轮通过削弱验签或状态证据来降低数字。

复现（所有输出目录必须是新的自有目录）：

```powershell
python -m scripts.measure_feature_management_delivery phases --matrix .scratch/phase4b-local-management/frozen-matrix-10-no-chat/matrix.json .scratch/phase4b-local-management/frozen-matrix-10-chat/matrix.json --output .scratch/phase4b-local-management/phase-performance-10
python -m scripts.measure_feature_management_delivery idle --matrix .scratch/phase4b-local-management/frozen-matrix-10-no-chat/matrix.json --artifacts .scratch/phase4b-local-management/management-delivery-10/validation-artifacts.json --output .scratch/phase4b-local-management/idle-performance-10-no-chat --samples 3 --seconds 10
# chat 使用相应chat matrix及新输出；真实GC见WORKLOG中的gc-performance-02，非私有状态写入。
```


计量边界：方法有嵌套不能相加；I/O 为 parent process counters，不是完整 kernel syscall trace；RSS 是采样点，不冒充全生命周期 peak；沙箱 Job 512 MiB 是强制上限，不是实测使用量。安装自检的网络/桌面是禁止项；正常 Worker 的生成图像+本地 HTTP 属有明确标记的验证边界，不代表真实识屏/真实模型人工门。

## 五、实机运行记录

- helper20 `TrustedProbeBundle` manifest SHA256：`0715b4a6bc3a3392b36ba8dfcebd1dff3978e709e5ab75aa0c221bc68d8e2d53`。真实 Windows LPAC 权限矩阵：**1 passed / 15 deselected，57.07s**。覆盖生成的授权/只读材料、scratch、越界/AAA canary、凭据/DPAPI/registry、IPv4/IPv6、本应用生成桌面对象、额外进程/敏感句柄、超时/输出/崩溃/父退出与清理；只操作专门生成夹具，不枚举真实凭据、不截真实桌面。
- 深路径真实 helper19 host factory / Worker HELLO→SHUTDOWN→graceful exit 均通过，隔离证据来自父端。helper20 在最新Core10两种冻结七行矩阵中已完成真实候选 host/Worker 自检。
- 自有 E 盘夹具的真实文件占用及 ACL 删除失败：**2 passed，3.99s**；权限失败保留 pending，删除未完成不提交未安装。Win32 磁盘不足/杀软错误分类7项是边界注入，**不是填满磁盘或关闭杀软后的实机故障**。
- 最新真实 Windows 控件 UI/High DPI 与相关合同：**69 passed，63.92s**（包含16项原生参数组合）。720/1100请求、light/dark、zh/en长标识、18px字体、Tab可达/深链/搜索/accessibility；截图仅 QWidget.grab 本应用控件。175%环境实际 DPR 2.1875，可用宽878logical，1100请求被屏幕限制至880；如实记录 viewport，不冒充1100 HiDPI宽屏。
- Core08 无聊天已通过实际 Worker 两次本地 HTTP 请求、正常 Worker 映射审计、启停及任务拒绝，但卸载锁竞争失败；聊天已通过卸载、多 owner 自然退出、ZIP重装与 profile/vault 保留，升级等待断言曾错误要求 enabled=False。两份 **不是最终通过证据**。修复后的Core09两变体完整七行均通过；最后validation类型声明改变冻结输入，已重新构建Core10并完整再验，两种当前矩阵均passed=True，不引用旧产物冒充最新通过。


### 最新双冻结 Core 实机矩阵

| 必过行 | 无聊天 Core10 | 带聊天 Core10 | 实际证据 |
|---|---|---|---|
| 空根启动 | 通过 | 通过 | 实际PetApp/窗口，识屏入口缺席 |
| 目录/ZIP与设置 | 通过 | 通过 | 真实管理UI→预检/确认/LPAC→pending；生产factory/端口/receipt；独立设置 |
| Worker请求 | 通过 | 通过 | 真实Worker＋生成图像＋本地HTTP2次请求；实际租约入口与映射DLL审计 |
| 包级停用 | 通过 | 通过 | 所属Worker停止，手动/自动任务公开seam拒绝 |
| 占用卸载 | 通过 | 通过 | 两Core/独立设置lease等待，自有进程正常退出后安全删除，未安装最终state |
| 重装保留 | 通过 | 通过 | ZIP重装、真实加载receipt、生成profile/binding/vault保持，配置无明文凭据 |
| 升级/回滚/失败 | 通过 | 通过 | 自然释放、保留previous主动回滚、自检失败不影响旧active、候选导入后生产加载失败、重启previous真实receipt |

| 构建 | build seconds | 目录总字节 | PYZ模块 / native模块 / 文件 | Core识屏实现命中 |
|---|---:|---:|---:|---:|
| no-chat Core10 | 184.368 | 459238201 | 2163 / 82 / 2103 | 0 |
| chat Core10 | 125.254 | 461660644 | 2179 / 82 / 2114 | 0 |

最终重读实际EXE嵌入PYZ和磁盘native inventory，与构建artifact一致；不是只信source注释。每种27步，步骤进程耗时合计no-chat **432.80s**、chat **503.00s**（两种可并行；不包括构建及所有控制器间隔）。运行cwd为自有无源码目录，清理PYTHONPATH；测试公钥只编入明确validation-only构建，私钥内存生成不落盘；正式源码策略仍空trust anchors/fail-closed。源码可在另一个构建快照目录保留作为证据，不在冻结运行目录内，也不作为 import fallback。

## 六、测试与验证

| 门 | 真实结果 / 当前状态 |
|---|---|
| 完整 package verifier 长路径 seam | 95 passed / 1 skipped，9.65s；先红3项 |
| kernel lock UI 重试与 validation 合同 | 54 passed，23.97s；真实锁回归先红1项 |
| 新 measurement tools 与 validation | 19 passed，0.71s；缺失工具/受限 retry seam 先红 |
| 最新 UI/原生 High DPI/validation | 69 passed，63.92s |
| 全量04 | 3821 passed / 13 skipped / 14 warnings，598.32s；类型修正前历史快照 |
| 类型边界/构建回归 | 54 passed，12.09s |
| 全量03 | 3810 passed / 13 skipped / 15 warnings，597.98s；晚于长路径修复、早于最后 UI retry/测量工具，不代替最终门 |
| 受影响 mypy | 最新46 source files passed；扩大48文件剩87项 app/settings 既有诊断，与独立HEAD逐项对照完全相同，新增诊断0。此前“48 files passed”属于另一个较窄命令范围，不混同 |
| 双冻结 Core 七行 | 最新 Core10 两变体各7行/27步骤 passed；实际EXE的PYZ/native重读审计通过，旧Core09仅为历史 |
| 高 CPU 负载时序族连续三遍 | 20个自有burner，3×166 passed / 1 warning；各遍系统CPU median/p95=100%，无失败，采样与命令如下 |
| 最终全量06 | 3821 passed, 13 skipped, 15 warnings in 514.51s；实际积累状态，不沿用全量04 |
| 最终静态 | Ruff全仓check通过，Python源码format 515 files通过，受影响mypy46 files通过；最终文档链接126文件、报告纪律/产品文案54 passed、diff及80文件白名单/原WIP/保护范围均通过，原始命令结果归档 |

最终全量使用 `QT_QPA_PLATFORM=offscreen; python -m pytest -q -ra --basetemp .scratch/phase4b-local-management/full-suite-06-tmp`，native UI子进程明确还原Windows原生平台。13项跳过（如平台条件/显式原生helper门）不伪装通过；本轮权限/双冻结/原生控件另有真实专项证据。

### 最终满负载连续三遍

- 第1遍 166 passed, 1 warning in 462.46s；CPU采样N425 median/p95 100.0/100.0%，min 99.2%；wall 464.565s
- 第2遍 166 passed, 1 warning in 463.56s；CPU采样N424 median/p95 100.0/100.0%，min 99.8%；wall 467.063s
- 第3遍 166 passed, 1 warning in 464.31s；CPU采样N424 median/p95 100.0/100.0%，min 99.8%；wall 466.274s

命令：

```powershell
python -m scripts.measure_feature_management_delivery stress --output .scratch/phase4b-local-management/high-load-final-01 --tests tests/test_feature_lifecycle.py tests/test_feature_lifecycle_ipc.py tests/test_feature_package_startup.py tests/test_feature_management.py tests/test_feature_management_ui.py tests/test_feature_state_monitor.py tests/test_feature_version_lease.py tests/test_feature_worker_handoff.py tests/test_screen_worker_entry.py tests/test_feature_management_native_ui.py tests/test_feature_package_transactions.py
python -m ruff check .
python -m ruff format --check pet features tests scripts packaging
python -m mypy features/screen_understanding/host/contribution_settings.py features/screen_understanding/host/factory.py packaging/phase4b_validation_boundaries.py packaging/phase4b_validation_entry.py pet/feature_build_policy.py pet/feature_install_state.py pet/feature_lifecycle.py pet/feature_lifecycle_contract.py pet/feature_lifecycle_ipc.py pet/feature_management.py pet/feature_management_ui.py pet/feature_package_files.py pet/feature_package_probe.py pet/feature_package_startup.py pet/feature_package_transactions.py pet/feature_probe_adapter.py pet/feature_probe_crypto.py pet/feature_probe_windows.py pet/feature_startup_contract.py pet/feature_state_io.py pet/feature_version_lease.py pet/plugins/__init__.py pet/plugins/capabilities.py pet/plugins/config.py pet/plugins/contributions.py pet/plugins/events.py pet/plugins/feature_host.py pet/plugins/feature_packages.py pet/plugins/manifest.py pet/plugins/package_binding.py pet/plugins/package_trust.py pet/plugins/ports.py pet/plugins/runtime.py pet/plugins/services.py pet/plugins/worker_launch.py pet/workers/screen_entry.py scripts/build_feature_management_delivery.py scripts/build_feature_probe.py scripts/build_feature_probe_native.py scripts/build_screen_delivery.py scripts/build_screen_worker.py scripts/feature_probe_canary.py scripts/feature_probe_entry.py scripts/measure_feature_management_delivery.py scripts/validate_feature_management_delivery.py scripts/validate_feature_probe_windows.py
python -m scripts.check_docs
python -m pytest -q tests/test_pr_report_discipline.py tests/test_desktop_pet_features.py::test_product_copy_has_no_external_brand_reference
git diff --check
```

mypy完整命令如上，原始46个参数也逐项归档到同组 `affected-mypy-07-command.json`，最终重跑 `affected-mypy-08.log`；可通过Python `subprocess.run(json.loads(Path(...).read_text(encoding="utf-8")),check=True)`复现，不声称默认全仓mypy通过。20个burner用Event/readyQueue同步，结束只回收本轮自有进程；整机CPU每1s真实采样。每遍1 warning来自故意构造ZIP重复条目的负向夹具，不是产品允许重复条目。

最终交付复验完整命令、耗时、exit code和逐项原始输出保存在同组 `final-delivery-checks-01.json` / `delivery-*-01.log`；链接126文件、报告纪律及产品文案54 passed（1.03s），Ruff/format/mypy重新通过。报告80个文件的增删行最终复算，原WIP白名单快照17文件SHA256保持不变；tracked/untracked空白检查无错误，保护文件未动，暂存为空，没有生成物或已知私钥/token头进入Git可见修改。敏感头检查仅扫描80个改动文本，不枚举真实vault/环境或冒充全面安全扫描。最终稳定文字后已独立重复文档和保护门：链接126文件通过、54 passed（0.77s），diff及保护范围均通过，结果归档 `final-delivery-checks-02.json`；最终逐文件80行numstat与实际Git结果逐项匹配。

扩大 `ruff format --check .` 额外发现7份历史Markdown的Python围栏格式问题（639已格式、7待格式）；逐份与HEAD原文核对一致，未改写旧报告。全部Python源码515项及本轮修改Markdown均无新增格式诊断；历史债记录，不称扩大范围全绿。中间失败root/red/green保留，不以历史green替换新快照。

### 本次分支发布复验（2026-10-04）

用户明确授权「将新修改推送到远程分支」。目标仅为 `origin/codex/phase3-worker`；原 `bd048d5` 本地与远程经 fetch 比对相同。85 文件白名单保留原 WIP，不合并、不强推、不正式发布、不重建大型冻结产物。当前门禁通过，尚未提交/推送；最终源提交与远端状态封存于同组交接。

环境：Windows build26100、Python3.11.1、PySide6 6.11.1、psutil6.1.0，20逻辑CPU；主测试设置 `QT_QPA_PLATFORM=offscreen`，原生 UI 子进程自行还原 Windows 平台。

| 新门禁 | 本次真实结果 |
|---|---|
| `python -m ruff check .` | passed；压力结束后再次通过 |
| `python -m ruff format --check pet features tests scripts packaging` | 518 files already formatted；压力结束后再次通过 |
| affected mypy / default mypy | 38个修改source target /默认26个source target均通过；app/settings既有类型债未纳入affected，不称全仓诊断清零 |
| 事务/probe/启动/生命周期/管理UI/Worker/人工入口专项 | 223 passed /1 skipped /1 warning，139.93s；warning为重复ZIP测试夹具 |
| `python -m pytest -q -ra --basetemp <publication>/full-tmp` | 3841 passed /13 skipped /14 warnings，571.34s；wrapper 572.587s，exit0 |
| 满CPU三遍 | 3 ×154 passed；21显式file/test target、20自有fixture，实际统计如下 |

| 遍次 | 结果 | wrapper wall | CPU样本数 | 系统CPU median / p95 |
|---|---|---|---|---|
| 1 | 154 passed | 272.010s | 245 | 100.0% / 100.0% |
| 2 | 154 passed | 267.890s | 239 | 100.0% / 100.0% |
| 3 | 154 passed | 319.995s | 286 | 100.0% / 100.0% |

本次压力与类型检查的完整复现命令（重跑须改为自有新output目录）：

```powershell
python -m scripts.measure_feature_management_delivery stress --output .scratch/phase4b-local-management/publication-20261004/high-load --tests tests/test_feature_lifecycle.py tests/test_feature_lifecycle_ipc.py tests/test_feature_management.py tests/test_feature_management_ui.py tests/test_feature_state_monitor.py tests/test_feature_runtime_ports.py tests/test_feature_settings_ports.py tests/test_feature_worker_handoff.py tests/test_feature_package_startup.py tests/test_feature_version_lease.py tests/test_screen_worker_entry.py tests/test_feature_management_native_ui.py tests/test_session_end_ffmpeg_guard.py tests/test_webm_reader_lifecycle.py tests/test_feature_package_transactions.py::test_management_lock_competition_is_explicit tests/test_feature_package_transactions.py::test_uninstall_waits_for_real_version_lease tests/test_feature_package_transactions.py::test_upgrade_real_host_lease_blocks_switch_then_recovers tests/test_feature_package_transactions.py::test_acceptance_journal_is_written_under_management_lock tests/test_feature_package_transactions.py::test_full_verification_never_holds_management_lock tests/test_feature_package_transactions.py::test_native_windows_file_lock_keeps_uninstall_pending_until_owned_handle_release tests/test_feature_package_transactions.py::test_lock_busy_after_runtime_preparation_names_real_resource_and_retries
python -m mypy features/screen_understanding/host/contribution_settings.py features/screen_understanding/host/factory.py packaging/phase4b_manual_entry.py packaging/phase4b_validation_boundaries.py packaging/phase4b_validation_entry.py pet/feature_build_policy.py pet/feature_install_state.py pet/feature_lifecycle.py pet/feature_lifecycle_contract.py pet/feature_lifecycle_ipc.py pet/feature_management.py pet/feature_management_ui.py pet/feature_package_files.py pet/feature_package_probe.py pet/feature_package_startup.py pet/feature_package_transactions.py pet/feature_probe_adapter.py pet/feature_probe_crypto.py pet/feature_probe_windows.py pet/feature_startup_contract.py pet/feature_state_io.py pet/feature_version_lease.py pet/plugins/feature_host.py pet/plugins/feature_packages.py pet/plugins/package_binding.py pet/plugins/package_trust.py pet/workers/screen_entry.py scripts/build_feature_management_delivery.py scripts/build_feature_management_manual.py scripts/build_feature_probe.py scripts/build_feature_probe_native.py scripts/build_screen_delivery.py scripts/build_screen_worker.py scripts/feature_probe_canary.py scripts/feature_probe_entry.py scripts/measure_feature_management_delivery.py scripts/validate_feature_management_delivery.py scripts/validate_feature_probe_windows.py
python -m mypy
```

已存在output目录不覆盖；再次运行需另选自有新目录。本轮压力命令的完整参数与原始结果见同组ignored证据。13项跳过包括显式native helper、平台/显示/声卡等条件，不冒充原生隔离权限的新通过；本报告前部已有Win32/双冻结/性能证据保持原日期，不用历史绿灯代替本次源发布门。

发布路径没有新增产品逻辑/稳态成本；本轮测量为一次性测试成本，不冒充用户运行性能。产品路径的样本量、median/p95/RSS等实测仍以上文原始工程与人工快照为准。本次不启动真实识屏/收费请求、不读取个人profile或原始模型结果，不更改既有manual Core及APPDATA。有限敏感字面量审计无匹配，不等于完整安全审计。

本轮五个临时测试目录删除被环境策略拒绝，未绕过、未删除。只读盘点 132,573,841 字节（126.432 MiB），跳过2个reparse条目；日志/白名单/before-image和临时夹具均保持ignored，不进入提交。该存储遗留不影响源码发布，先前31.184 GiB生成物清理与当前程序/数据保护保持原事实。

正式T0～T3继续暂缓；UX-M3原因及未执行人工门不冒充修复/通过。最终文档门、精确暂存与远端SHA核验将在封存时记录。

## 七、限制与人工门

本人真实识屏、真实凭据体验、实际托盘自然退出体验、其他平台实机、正式信任锚、Setup/签名发布均未声称验收。生成 profile 的 OS 安全存储往返通过不等于本人凭据使用体验；程序控制生成进程正常退出不等于用户实际托盘体验。既有静态类型债：app/settings 87项诊断与HEAD逐条相同，未新增；不将更宽范围mypy误报全绿，不在本轮无关重构87项旧逻辑。7份未改动历史Markdown围栏格式债已与HEAD核验一致；安装态管理监控约3%单核CPU是后续优化点，未削弱验签/状态证据换性能。本轮未发现方向错误，普通失败均完成根因回归。

## 八、风险与回滚

无子智能体，无 Git stage/commit/push/release。原 WIP 的明确文件白名单基线保存在自有 ignored 目录，回滚不得覆盖用户原改动；当前无可供 git revert 的新提交，只有以后获得独立授权、核对暂存/敏感文件后的提交才能按提交回滚。不得 reset --hard/强推/批量 add。

运行时权限/租约/证据无法证明则拒绝执行或删除。已接受卸载只能继续安全清理；任何版本删除失败保留 pending/recovery；孤立目录不会复活。GC失败只警告不撤销新 active；unknown目录不强制清理；始终保留 profile、凭据、记忆、额度、聊天历史及个人设置。

## 实际使用效果与限制

本轮完成的是 **Phase 4B 的本机 Windows 工程自动化闭环及管理体验**：LPAC权限门、4B-3生产事务、4B-4管理UI、最新Core10双七行以及最终满负载/全量均有真实证据。无识屏Core的明确验证构建可从“常规 / 扩展管理”选择本地官方目录/ZIP安装、升级、包级启停、受限回滚和卸载；占用等待自然退出，自检没有真实用户数据/网络/桌面权限，卸载不删除个人数据。

默认完整内置版本不提供假的物理卸载。正式源码的信任策略仍为空/fail-closed，不能将测试公钥验证产物当作正式发行包；正式可分发安装产品仍需配置官方信任锚、Setup/发布验收。本人真实识屏/凭据/托盘体验、其他平台未代替人工验收，也不把生成夹具成功冒充本人体验。
