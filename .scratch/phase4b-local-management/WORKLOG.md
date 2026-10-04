# 工作记录：最新检查点（2026-10-03）

## 当前权威检查点（2026-10-03；主机跨日产物 20261004，最新双冻结验收进行中）

- 深路径根因已由真实 LPAC 证据复核：仅 EXE longPathAware 不足，verifier 的 stat/scandir/open 使用受限 Windows extended-path I/O，逻辑 authority 不变。公开 seam 先红3项后95 passed / 1 skipped；helper19 真实深路径 host 与 Worker 均完成隔离自检。
- 当前 helper20 摘要 `0715b4a6bc3a3392b36ba8dfcebd1dff3978e709e5ab75aa0c221bc68d8e2d53`；原生完整权限矩阵1 passed / 15 deselected（57.07s）。没有普通 subprocess 回退、广泛用户文件授权、网络/桌面授权或管理员依赖。
- 最新 UI/原生 High DPI/性能观察器相关56 passed（75.87s）；16项原生控件只捕获本应用窗口。47个受影响 source mypy 已通过，最新验证入口反向额度修复后仍需最终静态复验。
- Core07 的空根、真实目录安装、生产 receipt、独立设置、实际配置/安全存储保存与 ZIP 幂等已通过；第三行实际 Worker 暴露 validation-only 反向额度协议错误。按真实 budget_check / generation / parent request / send_response 合同先红后绿，相关28 passed（1.79s），未改生产额度策略。
- Core08 两种最新冻结 Core 已构建；当前推进 no-chat 的七行矩阵，随后 chat。全量03正在进行，不能提前写通过。观察器500样本 median3.498ms/p95 5.373ms；parent process I/O 不等于全部系统调用，嵌套阶段不能求和。
- 4B-3/4B-4/4B-5仍未宣布工程完成：必须完成双 Core、最终全量/静态、高负载三遍、实测性能和最终报告。普通故障继续根因回归，没有发现方向错误。
- 原 WIP/明确白名单基线保留；本轮无暂存、提交、推送、发布或子智能体。真实凭据/识屏/托盘体验、其他平台、正式信任锚与 Setup/发布门单列未验收。

## 历史检查点（2026-10-03；主机跨日产物标识 20261004，深路径根因复核）

- Core06 两种冻结变体已重新构建；no-chat 的真实管理 UI 目录安装仍被 LPAC host 校验安全拒绝。helper18 精确证据：`_checked_stat`、WinError3、262字符的候选 DLL 路径。仅补 EXE longPathAware 声明不足，旧“根因已解决”结论已撤回；没有放宽 ACL、读取真实用户数据或缩短验证路径。
- verifier 在原有 local/absolute/no-traversal 边界之后，只有文件系统 I/O seam 使用 Windows extended-path；逻辑 root、manifest、祖先身份及执行授权不改。stat/scandir/open 的公开 verifier 负向回归先红3项；完整 package 族95 passed / 1 skipped（9.65s）。helper19 正进行真实深路径 host/Worker 门，尚不能宣称通过。
- 原生权限矩阵 helper16：1 passed / 15 deselected（75.88s）；不是深路径候选通过证据。后续 source 改动要求新 helper/双 Core 快照，Core06 不作为最终产物。
- 实际控件截图发现通用长 ASCII 标识的横向 paint overflow；新增宽度断言先红后绿，显示层插入换行机会，原 OperationPlan/摘要/令牌及 accessible 原文不变。最新 UI/build 52 passed（21.67s）；原生16 cases 48.35s，最终 ASCII-only 显示修复还须复跑。原确认摘要已有分组，不能误报成确认令牌被修改。
- 仅显式 validation frozen entry 的被动真实阶段性能观察器：原方法执行一次、异常原样传播；5 passed（0.44s）。自身校准500样本 median3.498ms/p95 5.373ms；Windows process I/O counters 不是所有内核系统调用，不掩盖观察开销。
- 4B-3/4B-4/4B-5 **仍未完成**。准确下一步：深路径真实 LPAC host+Worker -> 最新 helper 原生权限门 -> 新双 Core 七行矩阵 -> 最终高负载三遍/全量/静态门/性能/报告。普通故障继续定位；未发现方向错误。
- 原 WIP 与明确白名单基线保留；本轮无暂存、提交、推送、发布或子智能体。本人真实凭据/识屏/托盘自然退出、其他平台与正式发布门单列未验收。

## 连续实施检查点（2026-10-03；主机跨日产物 20261004，长路径构建修复）

- Core05 双变体已构建；no-chat 矩阵空根启动和目录预检通过，但真实 LPAC host 深层 staging 校验被安全拒绝（WinError3），没有提交 pending、没有启用候选。保留 frozen-matrix-05 与 probe-deep-path-diagnostic-02 证据。
- 当时根因假设（helper18 深路径证据已证明不充分）：自有 GUI-free bootloader 替换时丢失 EXE 的 longPathAware manifest；真实候选最大路径263字符，旧 helper EXE 无资源。没有改系统 ACL/长路径策略，也没有缩短测试路径。
- 新公开构建 seam 先红后绿：13 passed（0.86s）。只给自有 EXE 嵌入无 GUI 依赖的 longPathAware manifest，再追加并读取 CArchive；安装的 PyInstaller/Python 未改。helper16、normal/synthetic Worker06 已重建，Core06 双变体与新 helper 原生权限矩阵正在执行。
- Windows 原生 UI/High DPI 16 passed（34.21s）：布局断言改为事件同步等待 QLabel 的实际重排，不改产品或放宽裁切断言。实际 1.75 DPI 下1100逻辑宽请求受到屏幕宽度限制，证据记录实际 viewport；不宣称不存在的宽屏。
- 4B-3/4B-4/4B-5 尚未完成。准确下一步：helper16 原生权限门 -> Core06 双冻结七行矩阵 -> 高负载三遍/最终全量/性能/报告。无方向错误；继续普通故障根因回归，不提交/推送/子智能体。


设计：[事务](../../docs/plugin-phase-04-updates/PHASE4B-3-LOCAL-TRANSACTIONS-DESIGN.md) / [全阶段](../../docs/plugin-phase-04-updates/PHASE4B-LOCAL-MANAGEMENT-DESIGN.md)；持续记录：[PLAN](PLAN.md) / [HANDOFF](HANDOFF.md) / [STATUS](STATUS.md) / [WORKLOG](WORKLOG.md) / [SUMMARY](SUMMARY.md)。

- 日期：2026-10-03；产物中的 `20261004` 保留主机跨日标识，不改写历史。
- WIP 保留，HEAD 基线 `bd048d5`；无提交、推送、发布或子智能体。白名单快照 `baseline-20261004-014342` 未动。
- S1 实机：`native-pytest-20261004-14` **1 passed / 14 deselected / 92.32s**。生成权限 canary、明确继承句柄、阻塞 stdin、输出/内存/超时、父端异常退出/Job 回收及 profile 重复恢复通过；非真实个人数据/桌面。
- S2 host：`native-real-host-10` 真实签名 factory、完整 verifier、LPAC 成功，6.790s；Worker sentinel 不计 Worker 证据。
- S2 Worker：`native-real-worker-02` 新冻结 Worker，真实 LPAC 下 HELLO → SHUTDOWN → exit 0，8.460s，父端五项隔离核验均 true。
- S2 组合：`native-real-adapter-01` 真实生产 adapter 在两个独立 LPAC/只读候选快照中完成 host/Worker，46.723s（单样本，包含复制/重复哈希）；签名有效但 factory 返回 None 时 host 拒绝且未启动 Worker，错误签名在创建运行目录前拒绝。
- Adapter TDD 13 failed（缺模块）→ 13 passed / 2.16s；cleanup TDD 1 failed → 28 passed / 1 deselected / 2.43s（adapter + launcher 合同）。mock 仅 native launch seam，不冒充 OS 门。
- 新增官方 libsodium 1.0.22 固定摘要 DLL + license；同一 verifier/trust policy，native leaf 仅调用成熟验签。父端完整 ancestry 校验前后包围隔离自检；子端只在严格绑定的只读快照中接受完整父端 ancestor identity receipt，不放宽正常 verifier。
- 冻结 Worker 正常入口先租约接管再 import runtime；probe 无真实租约，仅沙箱中最小握手/退出。未使用 multiprocessing 的 runtime hook 在零 capabilities 下触发 Winsock 10107，按依赖根因排除，不开放网络。
- 生产事务/Qt 生命周期/可信启动 receipt、管理 UI、双新冻结 Core 矩阵仍未完成。当前新的全量/Ruff/format/mypy/CPU 三遍及最终性能报告未验收；旧全量 3634 仅历史。

## 失败根因与经验

- CFFI/native crypto 的 GUI 静态依赖不适合 Win32k-disabled probe；改成熟 upstream libsodium 的无 GUI DLL 叶接口，不改变 trust policy。
- snapshot 根外 ancestor 的 LPAC Win32 5 是预期隔离，不该开放祖先 ACL；父端前后完整核验与 readonly root identity receipt 绑定解决。
- PE finalizer 的 resource 循环变量覆盖 component 分类是构建逻辑 bug，已修正确职责变量，不扩大文件例外名单。
- Worker 隐式 unused multiprocessing hook 是启动期网络初始化，按依赖路径消除。
- 组合 adapter 与 cleanup 测试追加；没有生产闭环/UI/frozen Core 假通过。

## 历史工作记录（原文保留）

## 持续执行检查点：父端退出回收与 headless host 合同（2026-10-03）

- native-pytest-20261004-13：无 debug 的冻结 helper 完整生成权限矩阵 **1 passed / 13 deselected / 83.39s**，新增父端异常退出、Job 回收、PID/creation-time 绑定清理与重复恢复。目录名为主机产物标识，不改写历史日期。
- S1 原生 canary 门已有实测证据；S2 真实 host/Worker 自检、生产事务、管理 UI、双冻结 Core 仍未验收。生产默认自检仍 fail-closed。
- headless probe 导入合同先 red 后 green：租约/事务仅在实际生产使用处导入；真实签名 host factory 合同发现桌面/网络 eager import，改为实际 context 创建时惰性导入，**2 passed / 1.20s**。此处 Worker 为明确不可执行 sentinel，仅证明 host 合同，不是 Worker 成功。
- 当前准确下一步：构建含完整 verifier 的独立冻结 host helper，实测 LPAC 下真实 factory；为独立冻结 Worker 补最小 sandbox-only 握手及正常入口租约接管，并重建验证。尚无本轮全量、静态与最终性能交付证据。
- 未提交、推送或使用子智能体；原 WIP 与白名单基线快照保留。

## 持续执行检查点：LPAC 权限矩阵首次通过（2026-10-03；主机产物目录保留跨日日志标识）

- 未提交/推送/使用子智能体；原 WIP 与白名单快照保留，生产默认自检仍 fail-closed。
- 加载根因已分离：Console 低权限初始化、旧 MinGW 错误 CRT entry、CPython DLL 的 SxS activation resource。仅删除 Common-Controls 依赖仍失败；仅在自有 headless helper 的 CPython DLL/.pyd 副本移除完整、已校验模板的 activation resource，记录原 XML/前后 SHA256/许可证，不修改安装的 Python、Core 或系统文件。
- 空 capabilities 下 Winsock 初始化返回 10107：记录为网络初始化拒绝而非跳过检查；其他权限检查继续完成，未授予网络/桌面能力。
- 首次原生矩阵 native-pytest-20261004-11：1 passed / 35.64s，验证生成文件、AAA canary、生成用户目录文件、测试凭据/DPAPI/注册表、IPv4/IPv6、专用桌面、其他自有进程句柄、子进程，授权材料只读及 scratch 可写；崩溃/超时/累计输出超限回收。
- 扩展矩阵 native-pytest-20261004-12：1 passed / 67.97s；新增加 helper 不可写、显式生成继承文件句柄负对照、64KiB 阻塞 stdin 的真实超时、单条超限、512MiB Job 内存限制，父端 QueryInformationJobObject 核对真实限制及独有 profile 清理记录。
- 管道合同两项先 red 后 green；writer 在线程拥有阻塞 WriteFile，父端超时保持可运行。新增固定矩阵 schema，缺检查/非布尔值均拒绝，ownership write-before-create 绑定 root、bundle digest、profile、PID/creation time。
- 最新合同测试：21 passed / 1 deselected / 0.78s；尚未代表本轮全量/Ruff/mypy/高负载门通过。
- 准确当前停点：无 debug 的 native-build-03 已构建，probe-build-08 正在形成，下一步实测父端异常退出/原生恢复，再实现真实 host factory 与候选 Worker 的隔离握手。S1 完整门（父端退出尚未验收）、S2、4B-3 生产、4B-4、4B-5 均未勾完。

## 持续执行检查点：原生启动根因与可重建 probe（2026-10-03；主机日志跨日 2026-10-04）

- 仍保留原 WIP；无提交/推送/子智能体，生产自检仍 fail-closed，未把 canary launcher 接为安装自检。
- 最小 Kernel32 canary 的 Console 子系统在低权限初始化前失败；相同 LPAC/空 capabilities/Win32k/Job 下改为 Windows 子系统后执行成功，生成 DPAPI 数据无法解密、User32 无法初始化。此局部诊断不是完整权限矩阵通过。
- 第二次根因：旧 MinGW `--pic-executable` 未选择 CRT entry，退出值来自错误入口；显式 WinMainCRTStartup 后冻结 canary 普通正对照可执行。
- 新增可重建原生构建脚本：固定 PyInstaller 6.20.0 sdist SHA256，仅提取 bootloader/许可证，自有源码去除 console/window/onefile-child 控制，显式 Windows 子系统/CRT 入口/重定位；不修改已安装 PyInstaller。构建合同 3 red → 3 green。
- SDK TokenIsLessPrivilegedAppContainer=46 在本机 documented GetTokenInformation 被拒（Win32 87）；不使用未公开 NT API，也不信任子进程布尔值。改为恢复主线程前对真实 token 做两次内核 AccessCheck：own AppContainer SID 正对照允许，ALL_APPLICATION_PACKAGES 负对照拒绝。合同测试 1 red → 6 green / 1 deselected。
- 新一轮冻结 LPAC 已进入 bootloader，但 Python DLL 加载失败；实际 ACL/token/Win32k/Job 父端检查通过不等于权限矩阵通过。正在保留数字 Win32 错误并核查依赖，不开放 capabilities/桌面/用户数据权限。
- S1/S2、生产闭环、UI、双冻结验收均未完成；历史全量测试不能算当前变更已全量验证。
- 当前下一步：固定构建 native-build-02 + probe-build-04 的原生错误复现 → 依赖根因 → canary 矩阵 → 真实 host/Worker，然后沿既定顺序推进。

## 本轮原生门进展（本机 2026-10-04 02:16；计划日期 2026-10-03）

- C0 已完成：同组设计与五份持续记录落盘，白名单 WIP 快照保留；未提交/推送/使用子智能体。
- S0 公开 seam TDD：新增 launcher 测试先 5 failed（模块缺失），实现后 5 passed / 0.72s。仅为合同测试，不代表原生隔离通过。
- S1 实机权限矩阵目前 RED：正常进程生成夹具正对照通过；冻结 helper 在候选/canary 代码执行前退出 0xC0000142，无 stdout/stderr。原生测试 1 failed / 5 deselected / 14.82s。
- 已核对冻结入口静态 USER32 依赖，但仅依赖 Kernel32、无 CRT 的最小 canary 也同样失败；不能把 USER32 当作已证明的唯一根因。自有进程调试仅见 ntdll/kernel32/KernelBase 后初始化失败。
- 父端创建检查不是权限矩阵；lpac_attribute 当前表示属性提交成功，不等于实际 LPAC token 证明，生产仍 fail-closed，默认自检入口没有接入此实验 launcher。
- 下一步：单变量自有 canary 诊断原生启动根因，保持正式安全合同不变；原生门未通过前不宣布 S1/S2、4B-3/4/5 完成。

# 2026-10-03 连续收尾施工

4B-C0：核对HEAD和16项WIP，读取工程/security/memory/continuity协议；落盘已授权计划，保存明确白名单基线：E:\AI\DSH\dsh-pet-indesktop\.scratch\phase4b-local-management\baseline-20261004-014342。无提交推送。接下来4B3-S0/S1。

---

## 既有带日期记录（保留，不作为当前状态）

# Phase 4B 本地安装与管理：施工记录

## 当前权威检查点：Phase 4B-3 部分实现（基线 2026-10-03，恢复实施跨日）

- 用户授权：继续实施，自行复盘普通失败；发现方向问题再汇报。不开子智能体，不提交、不推送；HEAD/远程追踪基线仍为 `bd048d5`。
- 关键方向问题：普通同用户子进程不能提供“无真实用户权限”。生成文件实机探针 `OUTSIDE_HOME_READ=True`；未读取真实凭据/个人数据。默认未提供 OS 沙箱 runner 时明确拒绝，功能保持 uninstalled，不运行 factory/Worker。
- 已实现但未接生产：Qt-free plan/token/journal/CAS；安全目录/ZIP staging 与完整 verifier；管理/租约 admission 锁序；install/upgrade 等待与 active/previous/GC；草稿阻塞端口；卸载、部分删除恢复与旧 active 回滚。
- 尚未实现：OS 沙箱和真实 Worker 自检 executor、Qt queued 生命周期 adapter、Core/设置真实启动确认、常驻 host 加载失败后的重启回滚闭环、完整故障/权限/磁盘/杀软矩阵。内部摘要确认与 StubChecker 不算生产证据。
- 最近专项：事务 51 项 + PR 纪律 51 项 = 102 passed / 1 warning（36.78s）；关联七族此前 245 passed / 1 skipped（57.77s，最后两项回归之前）。
- 全量首轮：1 failed / 3629 passed / 12 skipped / 15 warnings（395.23s），失败是品牌文案门（既有入口 + 新设计中完整分支名）。分支证据移入工程报告，入口更新为部分实现，原测试未放宽；针对该门与 PR 纪律 52 passed（0.95s），文档链接 125 files passed。
- Windows 实机：生成版本文件被真实 CreateFileW 句柄占用时，卸载返回 recovery_required/file_in_use 且 disabled；关闭本探针自有句柄后 recover completed。10 份约 0.55MB fixture 的预检/应用中位 144.366/225.176ms；应用使用替身 self-check，不是生产安装性能。
- 静态检查：全库 Ruff、受影响六文件 format-check、五实现 mypy 均通过，diff-check exit 0。租约/Worker 交接/Qt 监控在 20 个自有计算进程下连续三遍各 13 passed（18.05/15.75/12.47s，系统 CPU 100.00/100.00/99.99%）。最终全量 3634 passed / 12 skipped / 15 warnings（368.67s）。自动化全绿不等于 OS 沙箱/生产启动闭环通过，整体交付门仍未通过。
- 证据：[设计](../../docs/plugin-phase-04-updates/PHASE4B-3-LOCAL-TRANSACTIONS-DESIGN.md) · [部分实现报告](../../docs/PR-REPORT-FEATURE-PACKAGE-TRANSACTIONS-2026-10-03.md) · [清单](PLAN.md) · [准确停点](HANDOFF.md) · [状态](STATUS.md) · [工作记录](WORKLOG.md) · [跨对话摘要](SUMMARY.md)。

### 普通失败复盘

- 账本 fresh read 的 state 不为空不代表已有落盘证据；先经 commit 初始化再创建 staging，不直接写 state。
- 文件内容指纹应排除 atime；Windows 读取本身可改变它，保留内容/hash/identity/mtime 门。
- 4 项升级/GC 失败：两项是测试误用不存在的 store.data_root，修正测试；另两项是生产删除没有覆盖已证明的延迟 GC、允许孤立目录收编，修复状态机，不放宽断言。
- ZIP 限制应在 ZipFile 分配 central directory 前检查；新增回归先红后绿。accepted journal 写失败的失败记录也会失败，外层保守返回 recovery，不得变成成功或隐藏已提交状态。
- 普通 subprocess 即使 HOME 与环境干净仍有当前用户 OS 权限；默认跳过 Worker/弱隔离属于方向问题，已移除，保持 fail-closed。
- 全量失败定位到文案元数据；保留完整分支到工程报告，不为新文档添加测试豁免。既有 PROJECT-ENTRY 旧引用同样修正，并把 4B-3 从“尚未开始”更新为“部分实现”。

## 历史实施记录（保留原日期、原版本与当时结论）

## Phase 4B-3 恢复实施（2026-10-03）

用户要求持续实现并自行复盘普通失败；当前恢复为实施中。先修复 fresh 账本初始化和 journal 命名合同，再按安全边界重构事务状态机；不提交、不推送、不使用子智能体。以下暂停记录保留为历史事实，8 failed / 1 passed 不是当前验收通过。


## Phase 4B-3 暂停记录（2026-10-03）

- 用户中断本轮；已停止产品实现，保留全部未提交工作树改动，不提交、不推送、不启动子智能体。
- 基线：`codex/phase3-worker` / `bd048d5`；本轮开始时工作树干净。
- 已落盘：设计文档、文档索引及五份持续记录；新增事务服务和隔离探针的未完成初稿、9 项合同测试；修改状态账本以区分事务 journal 与状态提交收据。
- 实际验证：`py_compile` 曾通过；专项测试首轮 `9 failed`，最近一次为 `8 failed / 1 passed`（1.76s，exit code 1）。Ruff 命令不在 PATH，尚未尝试 `python -m ruff`；相关族、全量、mypy、性能、实机验收及交付报告均未完成。
- 已知首要根因：fresh read 返回 uninstalled 且 state 非空；预检在写 staging/journal 前没有建立合法初始状态，之后账本按 `missing_state_with_traces` 拒绝。这不是包签名问题。当前 `_ensure_empty_state` 分支不会在 fresh read 触发，直接写 state 的做法也尚未具备锁/CAS 与收据证据；恢复实现必须遵守账本合同，不能把损坏状态当空状态。
- 其他未完成安全边界：确认摘要不可变性/持久绑定、源与 ZIP 大小边界、链接/reparse 删除检查、锁与租约竞态、升级等待和 previous/回滚、卸载草稿与生命周期、journal 恢复与 GC 权威校验、真实 Worker 握手（初稿默认跳过）、Core/设置启动加载确认接入。
- 测试纪律：初稿早于本轮测试写入，尚未形成项目要求的完整 test-first red/green 证据，不得宣称验收通过。
- 准确下一步：先阅读本记录和相关账本合同，新增 fresh preflight/账本证据回归测试，重新设计安全初始化及事务 journal 分类；再逐门实现并验证。当前代码不可视为可用安装器，不应接入生产启动。

> 本记录覆盖 4B 总任务当前仍可能继续的阶段；历史原始日志保留在原目录，不在此处复制。

## 4B-0/准备：范围和保护边界

- **目标**：建立本地功能包安装、启停、卸载和恢复闭环，但按门逐步推进。
- **状态**：已完成计划落盘和本地备份检查点；后续实现必须先通过 4B-1、4B-1.5，再进入 4B-2。
- **证据**：`docs/plugin-phase-04-updates/PHASE4B-LOCAL-MANAGEMENT-DESIGN.md`、本目录 `PLAN.md`/`HANDOFF.md`/`STATUS.md`。
- **保护范围**：不修改自动更新实现、演示 HTML、默认构建和用户已有改动；不使用 `reset --hard` 或宽泛暂存。
- **准确停点**：通用管理闭环尚未完成。

## 4B-1：唯一安装状态账本

- **目标**：建立唯一 `state.json` 权威、revision、操作幂等和损坏恢复。
- **状态**：已完成实现与自动化验收。
- **证据**：`docs/PR-REPORT-FEATURE-INSTALL-STATE-2026-09-30.md`；相关 `216 passed / 1 skipped`，全量对应代码状态 `3547 passed / 12 skipped / 13 warnings`，真实进程族三次各 `10 passed`。
- **未解决问题**：状态账本不提供版本租约、不执行安装文件事务、不提供管理 UI。
- **准确停点**：状态服务可以作为后续门的前置，但不能被当作完整安装器。

## 4B-1.5：资源 DLC 硬门

- **目标**：修复资源 Registry/播放/冲突/fallback/cache/恢复硬门。
- **状态**：代码、自动化、文档和保护门已通过；本地封存就绪基础已具备。
- **证据**：`.scratch/phase4b-1-5-resource-hard-gate/WORKLOG.md`、`SUMMARY.md`、阶段设计和 PR 报告；资源专项 `43 passed`，硬门族连续三次各 `28 passed`。
- **未解决问题**：人工资源安装/重启播放、跨进程租约、通用事务、管理 UI、真实冻结产物流程未完成。
- **准确停点**：4B-2 尚未开始。

## 4B-2：跨进程版本租约与状态同步

- **目标**：host、settings、worker 真实租约；加载/删除共用协调边界；handoff 保守保护；GUI 非阻塞状态授权。
- **状态**：未开始。
- **证据**：当前 `PLAN.md` 只保留计划，尚无本阶段实现或测试报告。
- **准确停点**：须在 4B-1.5 独立封存后再实施。
- **下一步**：先进行独立本地备份授权确认，再落盘 4B-2 计划/任务记录并先写失败测试。

## 4B-3：本地安装、升级、卸载与恢复事务

- **状态**：未开始；依赖 4B-2 和资源硬门。
- **准确停点**：不接入管理 UI。

## 4B-4：应用内扩展管理

- **状态**：未开始；必须等待 4B-3 通过。
- **准确停点**：UI 只能调用统一服务，不能自行写状态或复制文件。

## 4B-5：真实构建端到端

- **状态**：未开始；必须验证“不安装→安装→停用→卸载→重装”。
- **准确停点**：不把源码测试当作冻结产物验收。

## 连续性补档（2026-10-02）

- 本目录现在同时维护 `PLAN.md`、`WORKLOG.md`、`HANDOFF.md`、`STATUS.md`、`SUMMARY.md`。
- `WORKLOG.md` 记录过程证据；`SUMMARY.md` 供新对话快速接续；`STATUS.md` 只保留当前摘要。
- 当前工作树中 4B-1/4B-1.5 的改动仍未在本轮创建新提交或推送；历史提交和历史远程状态不倒写。
- 后续应先读 `docs/PROJECT-ENTRY.md`，再读本目录 `STATUS.md`、`PLAN.md`、`HANDOFF.md`、`SUMMARY.md` 和 4B 设计文档。
## 2026-10-03：启动 Phase 4B-3 实施

- 目标：实现官方功能包本地安装、升级、卸载事务；仅修改工作区，不提交或推送。
- 事实：基线为 `codex/phase3-worker` / `bd048d5`；工作树启动时干净；前置 4B-2 租约已存在。
- 已做：读取工程协议、项目记忆、Git 规则、状态账本、租约、验证器、加载器和资源事务模式；完成 4B-3 设计文档及五份持续记录更新。
- 当前阶段：4B3-1/4B3-2，先写失败测试和安全 staging。
- 验证：记录更新后将执行文档链接/格式检查；代码测试尚未开始。

## 2026-10-03 当前状态纠偏

- 用户确认 4B-1.5 人工验收门已通过；公开稳定 API/SDK、管理 UI 与完整管理闭环仍未开放。
- 4B-2 已完成并推送到 `origin/codex/phase3-worker`。
- 4B-3 安装/升级/卸载事务尚未开始；本轮只同步文档，不提前实现。

## 历史顶部检查点迁移（2026-10-03；不作为当前状态）

### 迁移的旧 PLAN.md 顶部检查点（保留原文）

## 持续执行检查点：父端退出回收与 headless host 合同（2026-10-03）

- native-pytest-20261004-13：无 debug 的冻结 helper 完整生成权限矩阵 **1 passed / 13 deselected / 83.39s**，新增父端异常退出、Job 回收、PID/creation-time 绑定清理与重复恢复。目录名为主机产物标识，不改写历史日期。
- S1 原生 canary 门已有实测证据；S2 真实 host/Worker 自检、生产事务、管理 UI、双冻结 Core 仍未验收。生产默认自检仍 fail-closed。
- headless probe 导入合同先 red 后 green：租约/事务仅在实际生产使用处导入；真实签名 host factory 合同发现桌面/网络 eager import，改为实际 context 创建时惰性导入，**2 passed / 1.20s**。此处 Worker 为明确不可执行 sentinel，仅证明 host 合同，不是 Worker 成功。
- 当前准确下一步：构建含完整 verifier 的独立冻结 host helper，实测 LPAC 下真实 factory；为独立冻结 Worker 补最小 sandbox-only 握手及正常入口租约接管，并重建验证。尚无本轮全量、静态与最终性能交付证据。
- 未提交、推送或使用子智能体；原 WIP 与白名单基线快照保留。

## 持续执行检查点：LPAC 权限矩阵首次通过（2026-10-03；主机产物目录保留跨日日志标识）

- 未提交/推送/使用子智能体；原 WIP 与白名单快照保留，生产默认自检仍 fail-closed。
- 加载根因已分离：Console 低权限初始化、旧 MinGW 错误 CRT entry、CPython DLL 的 SxS activation resource。仅删除 Common-Controls 依赖仍失败；仅在自有 headless helper 的 CPython DLL/.pyd 副本移除完整、已校验模板的 activation resource，记录原 XML/前后 SHA256/许可证，不修改安装的 Python、Core 或系统文件。
- 空 capabilities 下 Winsock 初始化返回 10107：记录为网络初始化拒绝而非跳过检查；其他权限检查继续完成，未授予网络/桌面能力。
- 首次原生矩阵 native-pytest-20261004-11：1 passed / 35.64s，验证生成文件、AAA canary、生成用户目录文件、测试凭据/DPAPI/注册表、IPv4/IPv6、专用桌面、其他自有进程句柄、子进程，授权材料只读及 scratch 可写；崩溃/超时/累计输出超限回收。
- 扩展矩阵 native-pytest-20261004-12：1 passed / 67.97s；新增加 helper 不可写、显式生成继承文件句柄负对照、64KiB 阻塞 stdin 的真实超时、单条超限、512MiB Job 内存限制，父端 QueryInformationJobObject 核对真实限制及独有 profile 清理记录。
- 管道合同两项先 red 后 green；writer 在线程拥有阻塞 WriteFile，父端超时保持可运行。新增固定矩阵 schema，缺检查/非布尔值均拒绝，ownership write-before-create 绑定 root、bundle digest、profile、PID/creation time。
- 最新合同测试：21 passed / 1 deselected / 0.78s；尚未代表本轮全量/Ruff/mypy/高负载门通过。
- 准确当前停点：无 debug 的 native-build-03 已构建，probe-build-08 正在形成，下一步实测父端异常退出/原生恢复，再实现真实 host factory 与候选 Worker 的隔离握手。S1 完整门（父端退出尚未验收）、S2、4B-3 生产、4B-4、4B-5 均未勾完。

## 持续执行检查点：原生启动根因与可重建 probe（2026-10-03；主机日志跨日 2026-10-04）

- 仍保留原 WIP；无提交/推送/子智能体，生产自检仍 fail-closed，未把 canary launcher 接为安装自检。
- 最小 Kernel32 canary 的 Console 子系统在低权限初始化前失败；相同 LPAC/空 capabilities/Win32k/Job 下改为 Windows 子系统后执行成功，生成 DPAPI 数据无法解密、User32 无法初始化。此局部诊断不是完整权限矩阵通过。
- 第二次根因：旧 MinGW `--pic-executable` 未选择 CRT entry，退出值来自错误入口；显式 WinMainCRTStartup 后冻结 canary 普通正对照可执行。
- 新增可重建原生构建脚本：固定 PyInstaller 6.20.0 sdist SHA256，仅提取 bootloader/许可证，自有源码去除 console/window/onefile-child 控制，显式 Windows 子系统/CRT 入口/重定位；不修改已安装 PyInstaller。构建合同 3 red → 3 green。
- SDK TokenIsLessPrivilegedAppContainer=46 在本机 documented GetTokenInformation 被拒（Win32 87）；不使用未公开 NT API，也不信任子进程布尔值。改为恢复主线程前对真实 token 做两次内核 AccessCheck：own AppContainer SID 正对照允许，ALL_APPLICATION_PACKAGES 负对照拒绝。合同测试 1 red → 6 green / 1 deselected。
- 新一轮冻结 LPAC 已进入 bootloader，但 Python DLL 加载失败；实际 ACL/token/Win32k/Job 父端检查通过不等于权限矩阵通过。正在保留数字 Win32 错误并核查依赖，不开放 capabilities/桌面/用户数据权限。
- S1/S2、生产闭环、UI、双冻结验收均未完成；历史全量测试不能算当前变更已全量验证。
- 当前下一步：固定构建 native-build-02 + probe-build-04 的原生错误复现 → 依赖根因 → canary 矩阵 → 真实 host/Worker，然后沿既定顺序推进。

## 本轮原生门进展（本机 2026-10-04 02:16；计划日期 2026-10-03）

- C0 已完成：同组设计与五份持续记录落盘，白名单 WIP 快照保留；未提交/推送/使用子智能体。
- S0 公开 seam TDD：新增 launcher 测试先 5 failed（模块缺失），实现后 5 passed / 0.72s。仅为合同测试，不代表原生隔离通过。
- S1 实机权限矩阵目前 RED：正常进程生成夹具正对照通过；冻结 helper 在候选/canary 代码执行前退出 0xC0000142，无 stdout/stderr。原生测试 1 failed / 5 deselected / 14.82s。
- 已核对冻结入口静态 USER32 依赖，但仅依赖 Kernel32、无 CRT 的最小 canary 也同样失败；不能把 USER32 当作已证明的唯一根因。自有进程调试仅见 ntdll/kernel32/KernelBase 后初始化失败。
- 父端创建检查不是权限矩阵；lpac_attribute 当前表示属性提交成功，不等于实际 LPAC token 证明，生产仍 fail-closed，默认自检入口没有接入此实验 launcher。
- 下一步：单变量自有 canary 诊断原生启动根因，保持正式安全合同不变；原生门未通过前不宣布 S1/S2、4B-3/4/5 完成。

# 当前执行计划：连续收尾（2026-10-03）

设计：../../docs/plugin-phase-04-updates/PHASE4B-3-LOCAL-TRANSACTIONS-DESIGN.md

- [x] 4B-C0 已落盘、已保存明确白名单基线快照。
- [ ] 4B3-S0/S1 合同 red/green、原生权限验证。
- [ ] 4B3-S2 新 helper/真实自检。
- [ ] 4B3-3/4/5/6 生产闭环和回归。
- [ ] 4B4-0~2 UI。
- [ ] 4B5-0~2 双冻结验收。
- [ ] 4B-CLOSE 证据/最终交接。

---


### 迁移的旧 HANDOFF.md 顶部检查点（保留原文）

## 持续执行检查点：父端退出回收与 headless host 合同（2026-10-03）

- native-pytest-20261004-13：无 debug 的冻结 helper 完整生成权限矩阵 **1 passed / 13 deselected / 83.39s**，新增父端异常退出、Job 回收、PID/creation-time 绑定清理与重复恢复。目录名为主机产物标识，不改写历史日期。
- S1 原生 canary 门已有实测证据；S2 真实 host/Worker 自检、生产事务、管理 UI、双冻结 Core 仍未验收。生产默认自检仍 fail-closed。
- headless probe 导入合同先 red 后 green：租约/事务仅在实际生产使用处导入；真实签名 host factory 合同发现桌面/网络 eager import，改为实际 context 创建时惰性导入，**2 passed / 1.20s**。此处 Worker 为明确不可执行 sentinel，仅证明 host 合同，不是 Worker 成功。
- 当前准确下一步：构建含完整 verifier 的独立冻结 host helper，实测 LPAC 下真实 factory；为独立冻结 Worker 补最小 sandbox-only 握手及正常入口租约接管，并重建验证。尚无本轮全量、静态与最终性能交付证据。
- 未提交、推送或使用子智能体；原 WIP 与白名单基线快照保留。

## 持续执行检查点：LPAC 权限矩阵首次通过（2026-10-03；主机产物目录保留跨日日志标识）

- 未提交/推送/使用子智能体；原 WIP 与白名单快照保留，生产默认自检仍 fail-closed。
- 加载根因已分离：Console 低权限初始化、旧 MinGW 错误 CRT entry、CPython DLL 的 SxS activation resource。仅删除 Common-Controls 依赖仍失败；仅在自有 headless helper 的 CPython DLL/.pyd 副本移除完整、已校验模板的 activation resource，记录原 XML/前后 SHA256/许可证，不修改安装的 Python、Core 或系统文件。
- 空 capabilities 下 Winsock 初始化返回 10107：记录为网络初始化拒绝而非跳过检查；其他权限检查继续完成，未授予网络/桌面能力。
- 首次原生矩阵 native-pytest-20261004-11：1 passed / 35.64s，验证生成文件、AAA canary、生成用户目录文件、测试凭据/DPAPI/注册表、IPv4/IPv6、专用桌面、其他自有进程句柄、子进程，授权材料只读及 scratch 可写；崩溃/超时/累计输出超限回收。
- 扩展矩阵 native-pytest-20261004-12：1 passed / 67.97s；新增加 helper 不可写、显式生成继承文件句柄负对照、64KiB 阻塞 stdin 的真实超时、单条超限、512MiB Job 内存限制，父端 QueryInformationJobObject 核对真实限制及独有 profile 清理记录。
- 管道合同两项先 red 后 green；writer 在线程拥有阻塞 WriteFile，父端超时保持可运行。新增固定矩阵 schema，缺检查/非布尔值均拒绝，ownership write-before-create 绑定 root、bundle digest、profile、PID/creation time。
- 最新合同测试：21 passed / 1 deselected / 0.78s；尚未代表本轮全量/Ruff/mypy/高负载门通过。
- 准确当前停点：无 debug 的 native-build-03 已构建，probe-build-08 正在形成，下一步实测父端异常退出/原生恢复，再实现真实 host factory 与候选 Worker 的隔离握手。S1 完整门（父端退出尚未验收）、S2、4B-3 生产、4B-4、4B-5 均未勾完。

## 持续执行检查点：原生启动根因与可重建 probe（2026-10-03；主机日志跨日 2026-10-04）

- 仍保留原 WIP；无提交/推送/子智能体，生产自检仍 fail-closed，未把 canary launcher 接为安装自检。
- 最小 Kernel32 canary 的 Console 子系统在低权限初始化前失败；相同 LPAC/空 capabilities/Win32k/Job 下改为 Windows 子系统后执行成功，生成 DPAPI 数据无法解密、User32 无法初始化。此局部诊断不是完整权限矩阵通过。
- 第二次根因：旧 MinGW `--pic-executable` 未选择 CRT entry，退出值来自错误入口；显式 WinMainCRTStartup 后冻结 canary 普通正对照可执行。
- 新增可重建原生构建脚本：固定 PyInstaller 6.20.0 sdist SHA256，仅提取 bootloader/许可证，自有源码去除 console/window/onefile-child 控制，显式 Windows 子系统/CRT 入口/重定位；不修改已安装 PyInstaller。构建合同 3 red → 3 green。
- SDK TokenIsLessPrivilegedAppContainer=46 在本机 documented GetTokenInformation 被拒（Win32 87）；不使用未公开 NT API，也不信任子进程布尔值。改为恢复主线程前对真实 token 做两次内核 AccessCheck：own AppContainer SID 正对照允许，ALL_APPLICATION_PACKAGES 负对照拒绝。合同测试 1 red → 6 green / 1 deselected。
- 新一轮冻结 LPAC 已进入 bootloader，但 Python DLL 加载失败；实际 ACL/token/Win32k/Job 父端检查通过不等于权限矩阵通过。正在保留数字 Win32 错误并核查依赖，不开放 capabilities/桌面/用户数据权限。
- S1/S2、生产闭环、UI、双冻结验收均未完成；历史全量测试不能算当前变更已全量验证。
- 当前下一步：固定构建 native-build-02 + probe-build-04 的原生错误复现 → 依赖根因 → canary 矩阵 → 真实 host/Worker，然后沿既定顺序推进。

## 本轮原生门进展（本机 2026-10-04 02:16；计划日期 2026-10-03）

- C0 已完成：同组设计与五份持续记录落盘，白名单 WIP 快照保留；未提交/推送/使用子智能体。
- S0 公开 seam TDD：新增 launcher 测试先 5 failed（模块缺失），实现后 5 passed / 0.72s。仅为合同测试，不代表原生隔离通过。
- S1 实机权限矩阵目前 RED：正常进程生成夹具正对照通过；冻结 helper 在候选/canary 代码执行前退出 0xC0000142，无 stdout/stderr。原生测试 1 failed / 5 deselected / 14.82s。
- 已核对冻结入口静态 USER32 依赖，但仅依赖 Kernel32、无 CRT 的最小 canary 也同样失败；不能把 USER32 当作已证明的唯一根因。自有进程调试仅见 ntdll/kernel32/KernelBase 后初始化失败。
- 父端创建检查不是权限矩阵；lpac_attribute 当前表示属性提交成功，不等于实际 LPAC token 证明，生产仍 fail-closed，默认自检入口没有接入此实验 launcher。
- 下一步：单变量自有 canary 诊断原生启动根因，保持正式安全合同不变；原生门未通过前不宣布 S1/S2、4B-3/4/5 完成。

# 当前停点（2026-10-03）

E:\AI\DSH\dsh-pet-indesktop / codex/phase3-worker / bd048d5，保留16项原WIP。
已保存白名单基线：E:\AI\DSH\dsh-pet-indesktop\.scratch\phase4b-local-management\baseline-20261004-014342
下一步：sandbox launcher公开seam失败测试，再LPAC/ACL/Job原生canary验证；无普通subprocess回退。
TDD：尚未运行本轮red。此前51passed为历史证据；无本轮提交/推送。

---


### 迁移的旧 STATUS.md 顶部检查点（保留原文）

## 持续执行检查点：父端退出回收与 headless host 合同（2026-10-03）

- native-pytest-20261004-13：无 debug 的冻结 helper 完整生成权限矩阵 **1 passed / 13 deselected / 83.39s**，新增父端异常退出、Job 回收、PID/creation-time 绑定清理与重复恢复。目录名为主机产物标识，不改写历史日期。
- S1 原生 canary 门已有实测证据；S2 真实 host/Worker 自检、生产事务、管理 UI、双冻结 Core 仍未验收。生产默认自检仍 fail-closed。
- headless probe 导入合同先 red 后 green：租约/事务仅在实际生产使用处导入；真实签名 host factory 合同发现桌面/网络 eager import，改为实际 context 创建时惰性导入，**2 passed / 1.20s**。此处 Worker 为明确不可执行 sentinel，仅证明 host 合同，不是 Worker 成功。
- 当前准确下一步：构建含完整 verifier 的独立冻结 host helper，实测 LPAC 下真实 factory；为独立冻结 Worker 补最小 sandbox-only 握手及正常入口租约接管，并重建验证。尚无本轮全量、静态与最终性能交付证据。
- 未提交、推送或使用子智能体；原 WIP 与白名单基线快照保留。

## 持续执行检查点：LPAC 权限矩阵首次通过（2026-10-03；主机产物目录保留跨日日志标识）

- 未提交/推送/使用子智能体；原 WIP 与白名单快照保留，生产默认自检仍 fail-closed。
- 加载根因已分离：Console 低权限初始化、旧 MinGW 错误 CRT entry、CPython DLL 的 SxS activation resource。仅删除 Common-Controls 依赖仍失败；仅在自有 headless helper 的 CPython DLL/.pyd 副本移除完整、已校验模板的 activation resource，记录原 XML/前后 SHA256/许可证，不修改安装的 Python、Core 或系统文件。
- 空 capabilities 下 Winsock 初始化返回 10107：记录为网络初始化拒绝而非跳过检查；其他权限检查继续完成，未授予网络/桌面能力。
- 首次原生矩阵 native-pytest-20261004-11：1 passed / 35.64s，验证生成文件、AAA canary、生成用户目录文件、测试凭据/DPAPI/注册表、IPv4/IPv6、专用桌面、其他自有进程句柄、子进程，授权材料只读及 scratch 可写；崩溃/超时/累计输出超限回收。
- 扩展矩阵 native-pytest-20261004-12：1 passed / 67.97s；新增加 helper 不可写、显式生成继承文件句柄负对照、64KiB 阻塞 stdin 的真实超时、单条超限、512MiB Job 内存限制，父端 QueryInformationJobObject 核对真实限制及独有 profile 清理记录。
- 管道合同两项先 red 后 green；writer 在线程拥有阻塞 WriteFile，父端超时保持可运行。新增固定矩阵 schema，缺检查/非布尔值均拒绝，ownership write-before-create 绑定 root、bundle digest、profile、PID/creation time。
- 最新合同测试：21 passed / 1 deselected / 0.78s；尚未代表本轮全量/Ruff/mypy/高负载门通过。
- 准确当前停点：无 debug 的 native-build-03 已构建，probe-build-08 正在形成，下一步实测父端异常退出/原生恢复，再实现真实 host factory 与候选 Worker 的隔离握手。S1 完整门（父端退出尚未验收）、S2、4B-3 生产、4B-4、4B-5 均未勾完。

## 持续执行检查点：原生启动根因与可重建 probe（2026-10-03；主机日志跨日 2026-10-04）

- 仍保留原 WIP；无提交/推送/子智能体，生产自检仍 fail-closed，未把 canary launcher 接为安装自检。
- 最小 Kernel32 canary 的 Console 子系统在低权限初始化前失败；相同 LPAC/空 capabilities/Win32k/Job 下改为 Windows 子系统后执行成功，生成 DPAPI 数据无法解密、User32 无法初始化。此局部诊断不是完整权限矩阵通过。
- 第二次根因：旧 MinGW `--pic-executable` 未选择 CRT entry，退出值来自错误入口；显式 WinMainCRTStartup 后冻结 canary 普通正对照可执行。
- 新增可重建原生构建脚本：固定 PyInstaller 6.20.0 sdist SHA256，仅提取 bootloader/许可证，自有源码去除 console/window/onefile-child 控制，显式 Windows 子系统/CRT 入口/重定位；不修改已安装 PyInstaller。构建合同 3 red → 3 green。
- SDK TokenIsLessPrivilegedAppContainer=46 在本机 documented GetTokenInformation 被拒（Win32 87）；不使用未公开 NT API，也不信任子进程布尔值。改为恢复主线程前对真实 token 做两次内核 AccessCheck：own AppContainer SID 正对照允许，ALL_APPLICATION_PACKAGES 负对照拒绝。合同测试 1 red → 6 green / 1 deselected。
- 新一轮冻结 LPAC 已进入 bootloader，但 Python DLL 加载失败；实际 ACL/token/Win32k/Job 父端检查通过不等于权限矩阵通过。正在保留数字 Win32 错误并核查依赖，不开放 capabilities/桌面/用户数据权限。
- S1/S2、生产闭环、UI、双冻结验收均未完成；历史全量测试不能算当前变更已全量验证。
- 当前下一步：固定构建 native-build-02 + probe-build-04 的原生错误复现 → 依赖根因 → canary 矩阵 → 真实 host/Worker，然后沿既定顺序推进。

## 本轮原生门进展（本机 2026-10-04 02:16；计划日期 2026-10-03）

- C0 已完成：同组设计与五份持续记录落盘，白名单 WIP 快照保留；未提交/推送/使用子智能体。
- S0 公开 seam TDD：新增 launcher 测试先 5 failed（模块缺失），实现后 5 passed / 0.72s。仅为合同测试，不代表原生隔离通过。
- S1 实机权限矩阵目前 RED：正常进程生成夹具正对照通过；冻结 helper 在候选/canary 代码执行前退出 0xC0000142，无 stdout/stderr。原生测试 1 failed / 5 deselected / 14.82s。
- 已核对冻结入口静态 USER32 依赖，但仅依赖 Kernel32、无 CRT 的最小 canary 也同样失败；不能把 USER32 当作已证明的唯一根因。自有进程调试仅见 ntdll/kernel32/KernelBase 后初始化失败。
- 父端创建检查不是权限矩阵；lpac_attribute 当前表示属性提交成功，不等于实际 LPAC token 证明，生产仍 fail-closed，默认自检入口没有接入此实验 launcher。
- 下一步：单变量自有 canary 诊断原生启动根因，保持正式安全合同不变；原生门未通过前不宣布 S1/S2、4B-3/4/5 完成。

# 当前状态（2026-10-03）

连续收尾已授权并落盘；4B-C0 完成，4B3-S0/S1 开始。4B-3/4/5 均未完成。
沙箱权限、生产闭环、UI、双冻结新构建未验证；历史事务51 passed不代替本轮门。
不开子智能体、不提交、不推送、真实用户数据不触碰；人工/其他平台/Setup发布单列。

---


### 迁移的旧 SUMMARY.md 顶部检查点（保留原文）

## 持续执行检查点：父端退出回收与 headless host 合同（2026-10-03）

- native-pytest-20261004-13：无 debug 的冻结 helper 完整生成权限矩阵 **1 passed / 13 deselected / 83.39s**，新增父端异常退出、Job 回收、PID/creation-time 绑定清理与重复恢复。目录名为主机产物标识，不改写历史日期。
- S1 原生 canary 门已有实测证据；S2 真实 host/Worker 自检、生产事务、管理 UI、双冻结 Core 仍未验收。生产默认自检仍 fail-closed。
- headless probe 导入合同先 red 后 green：租约/事务仅在实际生产使用处导入；真实签名 host factory 合同发现桌面/网络 eager import，改为实际 context 创建时惰性导入，**2 passed / 1.20s**。此处 Worker 为明确不可执行 sentinel，仅证明 host 合同，不是 Worker 成功。
- 当前准确下一步：构建含完整 verifier 的独立冻结 host helper，实测 LPAC 下真实 factory；为独立冻结 Worker 补最小 sandbox-only 握手及正常入口租约接管，并重建验证。尚无本轮全量、静态与最终性能交付证据。
- 未提交、推送或使用子智能体；原 WIP 与白名单基线快照保留。

## 持续执行检查点：LPAC 权限矩阵首次通过（2026-10-03；主机产物目录保留跨日日志标识）

- 未提交/推送/使用子智能体；原 WIP 与白名单快照保留，生产默认自检仍 fail-closed。
- 加载根因已分离：Console 低权限初始化、旧 MinGW 错误 CRT entry、CPython DLL 的 SxS activation resource。仅删除 Common-Controls 依赖仍失败；仅在自有 headless helper 的 CPython DLL/.pyd 副本移除完整、已校验模板的 activation resource，记录原 XML/前后 SHA256/许可证，不修改安装的 Python、Core 或系统文件。
- 空 capabilities 下 Winsock 初始化返回 10107：记录为网络初始化拒绝而非跳过检查；其他权限检查继续完成，未授予网络/桌面能力。
- 首次原生矩阵 native-pytest-20261004-11：1 passed / 35.64s，验证生成文件、AAA canary、生成用户目录文件、测试凭据/DPAPI/注册表、IPv4/IPv6、专用桌面、其他自有进程句柄、子进程，授权材料只读及 scratch 可写；崩溃/超时/累计输出超限回收。
- 扩展矩阵 native-pytest-20261004-12：1 passed / 67.97s；新增加 helper 不可写、显式生成继承文件句柄负对照、64KiB 阻塞 stdin 的真实超时、单条超限、512MiB Job 内存限制，父端 QueryInformationJobObject 核对真实限制及独有 profile 清理记录。
- 管道合同两项先 red 后 green；writer 在线程拥有阻塞 WriteFile，父端超时保持可运行。新增固定矩阵 schema，缺检查/非布尔值均拒绝，ownership write-before-create 绑定 root、bundle digest、profile、PID/creation time。
- 最新合同测试：21 passed / 1 deselected / 0.78s；尚未代表本轮全量/Ruff/mypy/高负载门通过。
- 准确当前停点：无 debug 的 native-build-03 已构建，probe-build-08 正在形成，下一步实测父端异常退出/原生恢复，再实现真实 host factory 与候选 Worker 的隔离握手。S1 完整门（父端退出尚未验收）、S2、4B-3 生产、4B-4、4B-5 均未勾完。

## 持续执行检查点：原生启动根因与可重建 probe（2026-10-03；主机日志跨日 2026-10-04）

- 仍保留原 WIP；无提交/推送/子智能体，生产自检仍 fail-closed，未把 canary launcher 接为安装自检。
- 最小 Kernel32 canary 的 Console 子系统在低权限初始化前失败；相同 LPAC/空 capabilities/Win32k/Job 下改为 Windows 子系统后执行成功，生成 DPAPI 数据无法解密、User32 无法初始化。此局部诊断不是完整权限矩阵通过。
- 第二次根因：旧 MinGW `--pic-executable` 未选择 CRT entry，退出值来自错误入口；显式 WinMainCRTStartup 后冻结 canary 普通正对照可执行。
- 新增可重建原生构建脚本：固定 PyInstaller 6.20.0 sdist SHA256，仅提取 bootloader/许可证，自有源码去除 console/window/onefile-child 控制，显式 Windows 子系统/CRT 入口/重定位；不修改已安装 PyInstaller。构建合同 3 red → 3 green。
- SDK TokenIsLessPrivilegedAppContainer=46 在本机 documented GetTokenInformation 被拒（Win32 87）；不使用未公开 NT API，也不信任子进程布尔值。改为恢复主线程前对真实 token 做两次内核 AccessCheck：own AppContainer SID 正对照允许，ALL_APPLICATION_PACKAGES 负对照拒绝。合同测试 1 red → 6 green / 1 deselected。
- 新一轮冻结 LPAC 已进入 bootloader，但 Python DLL 加载失败；实际 ACL/token/Win32k/Job 父端检查通过不等于权限矩阵通过。正在保留数字 Win32 错误并核查依赖，不开放 capabilities/桌面/用户数据权限。
- S1/S2、生产闭环、UI、双冻结验收均未完成；历史全量测试不能算当前变更已全量验证。
- 当前下一步：固定构建 native-build-02 + probe-build-04 的原生错误复现 → 依赖根因 → canary 矩阵 → 真实 host/Worker，然后沿既定顺序推进。

## 本轮原生门进展（本机 2026-10-04 02:16；计划日期 2026-10-03）

- C0 已完成：同组设计与五份持续记录落盘，白名单 WIP 快照保留；未提交/推送/使用子智能体。
- S0 公开 seam TDD：新增 launcher 测试先 5 failed（模块缺失），实现后 5 passed / 0.72s。仅为合同测试，不代表原生隔离通过。
- S1 实机权限矩阵目前 RED：正常进程生成夹具正对照通过；冻结 helper 在候选/canary 代码执行前退出 0xC0000142，无 stdout/stderr。原生测试 1 failed / 5 deselected / 14.82s。
- 已核对冻结入口静态 USER32 依赖，但仅依赖 Kernel32、无 CRT 的最小 canary 也同样失败；不能把 USER32 当作已证明的唯一根因。自有进程调试仅见 ntdll/kernel32/KernelBase 后初始化失败。
- 父端创建检查不是权限矩阵；lpac_attribute 当前表示属性提交成功，不等于实际 LPAC token 证明，生产仍 fail-closed，默认自检入口没有接入此实验 launcher。
- 下一步：单变量自有 canary 诊断原生启动根因，保持正式安全合同不变；原生门未通过前不宣布 S1/S2、4B-3/4/5 完成。

# 当前跨对话摘要（2026-10-03）

用户授权连续完成Windows沙箱前置、4B3生产闭环、4B4 UI、4B5双冻结自动验收；人工门单列。
已落盘用户计划并保护原WIP；当前4B3-S0/S1，真实隔离可行性尚未证明。
支持仅retained previous主动回滚；保持唯一ledger、跨进程lease、禁止hot replace/强退/真实secret/个人数据删除。
未提交/未推送。下一步权限canary；若必须削弱核心安全边界则停止汇报。

---



## 生产合同推进（2026-10-03；沿用主机跨日产物标识）

- 事务 child CAS：独立 `txc-<uuid>.<step>`，64 字符合同；写前 intent 崩溃重放同 ID，不再复用父事务 ID。
- purpose resolver：execution/configuration/startup；pending 普通执行拒绝，启动 permit 仅 host/settings，Worker 拒绝。
- 实际 `ProductionFeatureStartup`：完整验证 → factory/FeatureDefinition → 真实 host pin → Core ports → sealed receipt；UI 的 digest/success 参数不能确认。previous 也须实际加载确认才恢复 enabled。
- 回滚故障测试捕获旧外层 journal 覆盖新 intent：修成管理锁内读最新 journal 后记录 error，保留 write-ahead；before/after CAS 故障 2 项通过。
- 新 `load_current`、同版本授权刷新与执行 seam fail-closed authority；监控尚未送达时已停用账本不能执行，不能热换版本。
- Qt queued endpoint：真实 GUI 线程撤销与 Worker stop callbacks；dirty query 只返回阻塞，不保存/丢弃；超时后晚到 queued 请求不撤销。此最初 3 项先写实现与测试同批，**未留 red-before 证据**，不声称严格 TDD。后续合同/跨进程均先 red。
- QLocal 同用户端点绑定共享根 + lease owner identity，有界消息/连接/超时；每次 prepare nonce 与 revision/versions/live-owner-set 绑定，本次自有授权 intent 才允许请求，回执不等于内核租约释放。初版跨进程真实 Qt peer 1 项通过，仍须补并发/恶意帧/关闭门。
- 175 项关联（118.74s；1 重复 ZIP fixture warning）通过；新增 startup/binding 14 项（23.35s）、本地生命周期 3 项（4.36s）、IPC 1 项（2.44s）、新版 request 合同/draft 2 项（3.65s）。这些不是全量/最终工程门。
- 尚未接 AppShell/独立设置生产 bootstrap、trusted build policy、draft UI、状态监控、管理页或双冻结 Core 验收；全量/Ruff/format/mypy/高 CPU 三遍与新性能/最终报告仍未验收。
- 不提交、推送、发布、子智能体；原 WIP/基线快照不动。

下一步精确停点：先复验新 request + IPC + startup 全族，补 owner-set 变化及 IPC 有界失败测试，收短锁区；再接 Core/设置 bootstrap（内置默认行为保持）与 dirty query，随后扩展管理 UI → 双新冻结矩阵。

### PLAN.md 前一个当前块（留作历史）

# 当前执行计划：Phase 4B 连续收尾（2026-10-03）

设计：[事务](../../docs/plugin-phase-04-updates/PHASE4B-3-LOCAL-TRANSACTIONS-DESIGN.md) / [全阶段](../../docs/plugin-phase-04-updates/PHASE4B-LOCAL-MANAGEMENT-DESIGN.md)；持续记录：[PLAN](PLAN.md) / [HANDOFF](HANDOFF.md) / [STATUS](STATUS.md) / [WORKLOG](WORKLOG.md) / [SUMMARY](SUMMARY.md)。

- [x] 4B-C0 同一组记录落盘、原 WIP 白名单快照。
- [x] 4B3-S0/S1 native launch 合同、完整原生权限门（含父端退出）。
- [x] 4B3-S2 专用新冻结 helper、真实 host/Worker 与组合 probe（负向 factory/signature）。
- [ ] 4B3-3 补齐接受窗口/CAS/受限回滚/启停/取消；先隔离自检再生命周期影响。
- [ ] 4B3-4 生产 bootstrap、permit/receipt、Qt queued 生命周期、跨进程准备及实际加载失败/重启回滚。
- [ ] 4B3-5 草稿保护、卸载所有权/部分删除恢复/GC 生产闭环。
- [ ] 4B3-6 事务专项/相关/全量/原生故障验证。
- [ ] 4B4-0～2 常规内扩展管理；异步安全/布局/搜索/深链/可访问性。
- [ ] 4B5-0～2 双新冻结 Core 的七行矩阵与最终静态/高 CPU/性能/实机门。
- [ ] 4B-CLOSE 完整逐文件报告/INDEX/准确最终交接；人工、其他平台与正式发布门单列。

## 最近验证与下一步

- 日期：2026-10-03；产物中的 `20261004` 保留主机跨日标识，不改写历史。
- WIP 保留，HEAD 基线 `bd048d5`；无提交、推送、发布或子智能体。白名单快照 `baseline-20261004-014342` 未动。
- S1 实机：`native-pytest-20261004-14` **1 passed / 14 deselected / 92.32s**。生成权限 canary、明确继承句柄、阻塞 stdin、输出/内存/超时、父端异常退出/Job 回收及 profile 重复恢复通过；非真实个人数据/桌面。
- S2 host：`native-real-host-10` 真实签名 factory、完整 verifier、LPAC 成功，6.790s；Worker sentinel 不计 Worker 证据。
- S2 Worker：`native-real-worker-02` 新冻结 Worker，真实 LPAC 下 HELLO → SHUTDOWN → exit 0，8.460s，父端五项隔离核验均 true。
- S2 组合：`native-real-adapter-01` 真实生产 adapter 在两个独立 LPAC/只读候选快照中完成 host/Worker，46.723s（单样本，包含复制/重复哈希）；签名有效但 factory 返回 None 时 host 拒绝且未启动 Worker，错误签名在创建运行目录前拒绝。
- Adapter TDD 13 failed（缺模块）→ 13 passed / 2.16s；cleanup TDD 1 failed → 28 passed / 1 deselected / 2.43s（adapter + launcher 合同）。mock 仅 native launch seam，不冒充 OS 门。
- 新增官方 libsodium 1.0.22 固定摘要 DLL + license；同一 verifier/trust policy，native leaf 仅调用成熟验签。父端完整 ancestry 校验前后包围隔离自检；子端只在严格绑定的只读快照中接受完整父端 ancestor identity receipt，不放宽正常 verifier。
- 冻结 Worker 正常入口先租约接管再 import runtime；probe 无真实租约，仅沙箱中最小握手/退出。未使用 multiprocessing 的 runtime hook 在零 capabilities 下触发 Winsock 10107，按依赖根因排除，不开放网络。
- 生产事务/Qt 生命周期/可信启动 receipt、管理 UI、双新冻结 Core 矩阵仍未完成。当前新的全量/Ruff/format/mypy/CPU 三遍及最终性能报告未验收；旧全量 3634 仅历史。

下一步：先补公开 seam 测试，修 transaction 接受前 self-check 顺序与 journal-before-pending 恢复窗口；再推进生产可信加载 receipt。保持短锁区，禁止目录扫描收编/普通 subprocess 回退/强退用户进程/热替换驻留 host。

---


### HANDOFF.md 前一个当前块（留作历史）

# 准确停点：Phase 4B 连续收尾（2026-10-03）

设计：[事务](../../docs/plugin-phase-04-updates/PHASE4B-3-LOCAL-TRANSACTIONS-DESIGN.md) / [全阶段](../../docs/plugin-phase-04-updates/PHASE4B-LOCAL-MANAGEMENT-DESIGN.md)；持续记录：[PLAN](PLAN.md) / [HANDOFF](HANDOFF.md) / [STATUS](STATUS.md) / [WORKLOG](WORKLOG.md) / [SUMMARY](SUMMARY.md)。

- 日期：2026-10-03；产物中的 `20261004` 保留主机跨日标识，不改写历史。
- WIP 保留，HEAD 基线 `bd048d5`；无提交、推送、发布或子智能体。白名单快照 `baseline-20261004-014342` 未动。
- S1 实机：`native-pytest-20261004-14` **1 passed / 14 deselected / 92.32s**。生成权限 canary、明确继承句柄、阻塞 stdin、输出/内存/超时、父端异常退出/Job 回收及 profile 重复恢复通过；非真实个人数据/桌面。
- S2 host：`native-real-host-10` 真实签名 factory、完整 verifier、LPAC 成功，6.790s；Worker sentinel 不计 Worker 证据。
- S2 Worker：`native-real-worker-02` 新冻结 Worker，真实 LPAC 下 HELLO → SHUTDOWN → exit 0，8.460s，父端五项隔离核验均 true。
- S2 组合：`native-real-adapter-01` 真实生产 adapter 在两个独立 LPAC/只读候选快照中完成 host/Worker，46.723s（单样本，包含复制/重复哈希）；签名有效但 factory 返回 None 时 host 拒绝且未启动 Worker，错误签名在创建运行目录前拒绝。
- Adapter TDD 13 failed（缺模块）→ 13 passed / 2.16s；cleanup TDD 1 failed → 28 passed / 1 deselected / 2.43s（adapter + launcher 合同）。mock 仅 native launch seam，不冒充 OS 门。
- 新增官方 libsodium 1.0.22 固定摘要 DLL + license；同一 verifier/trust policy，native leaf 仅调用成熟验签。父端完整 ancestry 校验前后包围隔离自检；子端只在严格绑定的只读快照中接受完整父端 ancestor identity receipt，不放宽正常 verifier。
- 冻结 Worker 正常入口先租约接管再 import runtime；probe 无真实租约，仅沙箱中最小握手/退出。未使用 multiprocessing 的 runtime hook 在零 capabilities 下触发 Winsock 10107，按依赖根因排除，不开放网络。
- 生产事务/Qt 生命周期/可信启动 receipt、管理 UI、双新冻结 Core 矩阵仍未完成。当前新的全量/Ruff/format/mypy/CPU 三遍及最终性能报告未验收；旧全量 3634 仅历史。

## 准确下一步与修改边界

1. `pet/feature_probe_adapter.py` 已通过真实组合 probe，但生产 service 默认仍 fail-closed，未全局绑定 build trust pin。
2. 在 `tests/test_feature_package_transactions.py` 增加 self-check 在 runtime.prepare 前、接受 journal 写入/首 pending 之间故障、多个冲突意图不猜测的公开 seam red 测试；实现后 green。
3. 随后补 restricted rollback/set_enabled/cancel 与 resolver purpose、真实启动 permit/receipt。UI 不能提交 success 布尔清 pending。
4. `feature_probe_windows.py` cleanup 已修为先尝试所有独立资源释放后再报告错误；最新 native 完整矩阵是在此小改之前，需最终复跑。
5. 现有新源码/构建脚本/test/license 以及原 16 WIP 全保留；生成目录不入库，私钥仅内存。未暂存/提交/推送。先 Ruff/format 再里程碑回归。

当前没有新增用户确认项。安全方向未发现错误；不能将剩余核心门登记为一般遗留后宣称完工。

---


### STATUS.md 前一个当前块（留作历史）

# 当前状态：Phase 4B 工程闭环仍在实施（2026-10-03）

设计：[事务](../../docs/plugin-phase-04-updates/PHASE4B-3-LOCAL-TRANSACTIONS-DESIGN.md) / [全阶段](../../docs/plugin-phase-04-updates/PHASE4B-LOCAL-MANAGEMENT-DESIGN.md)；持续记录：[PLAN](PLAN.md) / [HANDOFF](HANDOFF.md) / [STATUS](STATUS.md) / [WORKLOG](WORKLOG.md) / [SUMMARY](SUMMARY.md)。

- 日期：2026-10-03；产物中的 `20261004` 保留主机跨日标识，不改写历史。
- WIP 保留，HEAD 基线 `bd048d5`；无提交、推送、发布或子智能体。白名单快照 `baseline-20261004-014342` 未动。
- S1 实机：`native-pytest-20261004-14` **1 passed / 14 deselected / 92.32s**。生成权限 canary、明确继承句柄、阻塞 stdin、输出/内存/超时、父端异常退出/Job 回收及 profile 重复恢复通过；非真实个人数据/桌面。
- S2 host：`native-real-host-10` 真实签名 factory、完整 verifier、LPAC 成功，6.790s；Worker sentinel 不计 Worker 证据。
- S2 Worker：`native-real-worker-02` 新冻结 Worker，真实 LPAC 下 HELLO → SHUTDOWN → exit 0，8.460s，父端五项隔离核验均 true。
- S2 组合：`native-real-adapter-01` 真实生产 adapter 在两个独立 LPAC/只读候选快照中完成 host/Worker，46.723s（单样本，包含复制/重复哈希）；签名有效但 factory 返回 None 时 host 拒绝且未启动 Worker，错误签名在创建运行目录前拒绝。
- Adapter TDD 13 failed（缺模块）→ 13 passed / 2.16s；cleanup TDD 1 failed → 28 passed / 1 deselected / 2.43s（adapter + launcher 合同）。mock 仅 native launch seam，不冒充 OS 门。
- 新增官方 libsodium 1.0.22 固定摘要 DLL + license；同一 verifier/trust policy，native leaf 仅调用成熟验签。父端完整 ancestry 校验前后包围隔离自检；子端只在严格绑定的只读快照中接受完整父端 ancestor identity receipt，不放宽正常 verifier。
- 冻结 Worker 正常入口先租约接管再 import runtime；probe 无真实租约，仅沙箱中最小握手/退出。未使用 multiprocessing 的 runtime hook 在零 capabilities 下触发 Winsock 10107，按依赖根因排除，不开放网络。
- 生产事务/Qt 生命周期/可信启动 receipt、管理 UI、双新冻结 Core 矩阵仍未完成。当前新的全量/Ruff/format/mypy/CPU 三遍及最终性能报告未验收；旧全量 3634 仅历史。

## 完成口径

S0/S1/S2 实机前置有证据；4B-3 整体、4B-4/5 仍 pending。用户目前没有管理 UI，不能通过真实应用完成安装/升级/卸载，不显示工程已完成。真实凭据/真实识屏/托盘自然退出用户体验、其他平台及正式信任锚/Setup/发布均未在本轮验收。

---


### SUMMARY.md 前一个当前块（留作历史）

# 跨对话摘要：Phase 4B 连续收尾（2026-10-03）

设计：[事务](../../docs/plugin-phase-04-updates/PHASE4B-3-LOCAL-TRANSACTIONS-DESIGN.md) / [全阶段](../../docs/plugin-phase-04-updates/PHASE4B-LOCAL-MANAGEMENT-DESIGN.md)；持续记录：[PLAN](PLAN.md) / [HANDOFF](HANDOFF.md) / [STATUS](STATUS.md) / [WORKLOG](WORKLOG.md) / [SUMMARY](SUMMARY.md)。

- 日期：2026-10-03；产物中的 `20261004` 保留主机跨日标识，不改写历史。
- WIP 保留，HEAD 基线 `bd048d5`；无提交、推送、发布或子智能体。白名单快照 `baseline-20261004-014342` 未动。
- S1 实机：`native-pytest-20261004-14` **1 passed / 14 deselected / 92.32s**。生成权限 canary、明确继承句柄、阻塞 stdin、输出/内存/超时、父端异常退出/Job 回收及 profile 重复恢复通过；非真实个人数据/桌面。
- S2 host：`native-real-host-10` 真实签名 factory、完整 verifier、LPAC 成功，6.790s；Worker sentinel 不计 Worker 证据。
- S2 Worker：`native-real-worker-02` 新冻结 Worker，真实 LPAC 下 HELLO → SHUTDOWN → exit 0，8.460s，父端五项隔离核验均 true。
- S2 组合：`native-real-adapter-01` 真实生产 adapter 在两个独立 LPAC/只读候选快照中完成 host/Worker，46.723s（单样本，包含复制/重复哈希）；签名有效但 factory 返回 None 时 host 拒绝且未启动 Worker，错误签名在创建运行目录前拒绝。
- Adapter TDD 13 failed（缺模块）→ 13 passed / 2.16s；cleanup TDD 1 failed → 28 passed / 1 deselected / 2.43s（adapter + launcher 合同）。mock 仅 native launch seam，不冒充 OS 门。
- 新增官方 libsodium 1.0.22 固定摘要 DLL + license；同一 verifier/trust policy，native leaf 仅调用成熟验签。父端完整 ancestry 校验前后包围隔离自检；子端只在严格绑定的只读快照中接受完整父端 ancestor identity receipt，不放宽正常 verifier。
- 冻结 Worker 正常入口先租约接管再 import runtime；probe 无真实租约，仅沙箱中最小握手/退出。未使用 multiprocessing 的 runtime hook 在零 capabilities 下触发 Winsock 10107，按依赖根因排除，不开放网络。
- 生产事务/Qt 生命周期/可信启动 receipt、管理 UI、双新冻结 Core 矩阵仍未完成。当前新的全量/Ruff/format/mypy/CPU 三遍及最终性能报告未验收；旧全量 3634 仅历史。

决策：保持 Win32k disable/LPAC 空 capabilities/单进程 Job 与 build-pinned helper；不采用普通同用户 subprocess、PyNaCl CFFI（原生 USER32 冲突）、管理员权限或未知 NT API。构建对 OWNED 的已知来源/摘要/模板 activation resource 副本处理并记录前后证据，不修改已安装 Python/Core/系统文件。

新 helper：`probe-host-build-13/dist/dsh-feature-probe`，bundle SHA256 `7d6e6f456c6d6dc7395082393c7f1b68115a07f17b559c7501db08681a29b881`；native `probe-native-build-20261004-06`；Worker `standalone-probe-worker-04`。对应代码未提交，只作为本轮版本化实机证据。

后续关键路径：接受前自检/恢复窗口 → 真实生产加载 receipt + Qt/IPC 生命周期 → 草稿/卸载/GC → UI → 双新冻结 Core 七行矩阵 → 最终全套证据。无新确认项、不使用子智能体、未经授权不提交推送。

---


## 连续实施检查点（2026-10-03；主机跨日标识 20261004）

- 已接入 AppShell 与独立设置的生产 bootstrap、不可由用户环境注入的 build policy、状态监控与真实加载 receipt；默认内置构建保持原行为。正式信任锚尚未配置，源码安装保持 fail-closed。
- 常规域内增加“扩展管理”及 extensions 深链/搜索；独立管理入口不导入识屏 host。后台线程执行服务，UI 不提交加载成功。已确认的等待释放操作重试复用原 plan。
- 耗时完整验签/哈希移出 management 锁；最终仅快照 identity 核对。卸载/GC 删除在释放 management 后保留 admission guard；重新按正向锁序核对并提交，不能覆盖较高 revision。
- 最近局部门：管理/UI 7 passed（3.00s），设置关联 69 passed（25.09s），短锁/卸载/GC 17 passed（43.00s）。不是最终全量门。
- 新红门证明 IPC read buffer 无上限与冻结 Worker 缺少 startup contract；正在修复并验证。另发现 settings dirty 是方法，当前草稿 guard 误以方法对象为 dirty，待回归修复。
- helper14 已构建，SHA256 5a2305a3a6f3d974f490b22603caa2a819b9f72484a28ddbf0bfc559399c3650；后续快照 identity 改动要求再重建。旧 Worker/旧 Phase4A Core 不作为最终冻结证据。
- 4B-3/4B-4/4B-5 未完成：草稿显式处理、并发及布局门、新冻结 Worker/helper/双 Core 七行矩阵、最终静态/全量/高负载三遍/实机/性能和报告仍待完成。
- 不提交、推送、发布或使用子智能体；保留原 WIP 与白名单基线快照。

下一步：先完成 IPC/Worker 闭包回归与草稿保护，复验生产族，重建冻结材料；再从真实生产入口完成双 Core 矩阵与最终验证。未发现必须削弱安全合同的方向错误。


## 连续实施检查点（2026-10-03；主机跨日标识 20261004，06:58）

- helper15 的原生 LPAC 权限矩阵实测通过：1 passed / 15 deselected，44.22s；SHA256 `e62dae17b640b1cc003ac8ead4b313751fcc22f06aa78ca1a3cf1f1b8317e4cc`。不是整体 Phase 4B 完成证据。
- 生产相关族 134 passed / 1 warning，169.41s；扩展 UI 38 passed，22.44s；Ruff 源码门通过，受影响 mypy 41 source files 通过。英文长摘要/18px/720 宽布局已先红后绿。
- 重新构建 helper15、normal/synthetic Worker05、双 Core02。Core02 为中间诊断产物：后续源改动已使输入快照过期，不能作为最终冻结交付。
- Core02 实机：管理 UI 安装目录包 -> LPAC host/Worker 自检 -> awaiting_startup_confirmation；新 Core 实际 bootstrap/factory/ports/receipt -> revision4、pending 清除；真实 Core 设置保存生成 profile/安全存储通过。
- 冻结独立设置发生 `selection_unresolved`（源码同路径生产设置正常）：正在保留原行为加入只读诊断并构建 Core03，未掩盖该必过门。
- 全量01在新增清理测试中原生 abort：共享 QCoreApplication 无法升级为 QWidget 应用；改为独立真实 QApplication 子进程和定向 DeferredDelete，4 项构建/清理测试通过；全量02进行中。
- 4B-3/4B-4/4B-5 尚未完成：双冻结七行矩阵、独立设置根因、最终全量/高负载/Windows 故障与性能/报告仍在推进。没有方向错误，不削弱任何沙箱或数据保留合同。
- 保留原 WIP；不提交、推送、发布或使用子智能体。

下一准确动作：获取 Core03 的独立设置只读诊断，根因回归先红后绿；完成双冻结真实生产/UI矩阵，再积累最终门和证据。


## 连续实施检查点（2026-10-03；主机跨日产物标识 20261004，Core05 构建中）

- Core03 的独立设置只读诊断证实 `lock_busy`；延后监控启动回归先红后绿。真实 Worker 启动发现自有 runtime 目录未创建，已由生产 startup 在安全边界内准备，并传入冻结 Core 的 DLL 根；相关 18 项回归通过（29.38s）。
- 全量02：3765 passed / 13 skipped / 14 warnings，533.07s；这是修复累积前的中间快照，不能替代最终门。
- Core04 双变体重新构建；矩阵04 的目录预检仍触发监控读锁竞争，保留失败证据。根因修为共享只读状态锁、排他 CAS 有界等候；管理锁/租约仍排他且非阻塞。新合同先红后绿，相关状态/监控/事务/管理 165 passed / 1 warning，112.44s。
- 本机 Windows 自有夹具的真实文件占用和 ACL 删除失败：2 passed，3.99s。Win32 磁盘/杀软原因码只做边界注入，不填满磁盘、不关闭杀软；7 项原因码回归通过（1.67s）。
- Core05 正按当前生产修复重新构建；下一步运行双冻结七行矩阵。没有方向错误，不弱化任何沙箱/保留数据合同。
- 4B-3/4B-4/4B-5 仍未完成：最终双冻结矩阵、高负载三遍、High DPI 实机、性能和最终全量/报告尚未通过。原 WIP 保留；没有提交、推送或子智能体。

下一准确动作：读取 `management-delivery-05-command.log`，Core05 no-chat 就绪后运行 `python -m scripts.validate_feature_management_delivery --artifacts .scratch/phase4b-local-management/management-delivery-05/validation-artifacts.json --output .scratch/phase4b-local-management/frozen-matrix-05 --variant no-chat`，再验 chat；普通失败继续根因回归。


## 连续实施记录（2026-10-04；Core09 双冻结完整验收进行中）

- helper20 原生 LPAC 权限矩阵通过：1 passed / 15 deselected，57.07s；manifest SHA256 `0715b4a6bc3a3392b36ba8dfcebd1dff3978e709e5ab75aa0c221bc68d8e2d53`。深路径 stat/scandir/open 的受限 extended-path 修复已有真实 host/Worker 证据；没有普通 subprocess 回退或扩大用户权限。
- Core08 的真实 Worker 与停用通过；无聊天在卸载时安全拒绝管理锁竞争，暴露 UI 丢失已确认计划的重试缺陷，真实内核锁回归先红后绿。聊天卸载/重装/数据保留通过，升级等待 enabled=True 是保留启用意图，validator 原断言已修正。旧失败记录保留，不作为最终通过证据。
- 新 UI 在 management_lock_busy 时保留不可变 confirmed plan，实际重试仍重新校验源、revision、签名、锁、租约。验证入口仅允许该原因最多3次，通过实际按钮/inspection 事件推进；不跳过安全门、不伪造成功。
- Core09 两种变体已按最新源重新构建；当前在 frozen-matrix-09-no-chat / frozen-matrix-09-chat 执行七行完整生产矩阵，尚未全部通过。全量04正在执行；全量03 3810 passed / 13 skipped / 15 warnings（597.98s）仅为最后 UI/测量改动前的历史证据。
- 最新 UI/原生 High DPI/validation 69 passed（63.92s）；最新 validation/measurement 19 passed（0.71s）。Ruff06、format06 485 files、受影响 mypy 48 files 通过；最终文档更新后还需静态复验。
- 4B-3/4B-4/4B-5 尚未宣告工程完成。下一步：双冻结矩阵 → 高 CPU 三遍 → 最终实际阶段/空闲性能 → 最终全量/静态/报告；普通失败继续根因回归，目前未发现方向错误。
- 原 WIP/明确白名单基线保留；不暂存、提交、推送、发布或使用子智能体。本人真实凭据/识屏/托盘体验、其他平台、正式信任锚与 Setup/发布门单列未验收。


## 连续实施记录（2026-10-04；Core09 双变体七行通过，Core10 再冻结中）

- Core09 无聊天和带聊天均完成真实冻结七行矩阵，matrix.json passed=True；包含目录/ZIP实际管理 UI、生产 receipt、实际 Worker/本地HTTP、启停、多 owner 自然退出卸载、重装保留 profile/vault、升级、主动回滚、自检失败及生产加载失败后的重启 previous receipt。候选导入后失败不热替换、不跳过回滚加载。
- helper20 完整 LPAC 原生权限门1 passed / 15 deselected（57.07s），摘要 `0715b4a6bc3a3392b36ba8dfcebd1dff3978e709e5ab75aa0c221bc68d8e2d53`；源预检从不执行代码，自检不开放真实用户数据、网络或桌面。
- 全量04 3821 passed / 13 skipped / 14 warnings，598.32s。54项构建/validation/measurement 回归12.09s；新 UI 69项63.92s（16项本机原生 DPI）。类型边界修正前扩大检查报98项；隔离 HEAD 原文90项，其中 app/settings87项与当前消息逐项相同。新增8项修正，顺带清除构建脚本3项原债；当前新/影响模块46 source files mypy通过，扩大48文件仍87项既有债，不冒充全部绿。
- 最后 TypedDict 与构建 Optional 证据仅修正类型边界、不改变运行顺序或权限。因为 validation boundary 属冻结输入，Core10双变体正在重建；最终矩阵必须使用Core10，不沿用Core09证明新快照。全量05正在执行。
- 尚需Core10双冻结、CPU高负载三遍、最终阶段/空闲/真实 GC 性能、最终静态/文档/报告。本轮没有方向错误；普通故障继续根因回归。4B工程状态仍为实施/最终验收中，不提前宣告完成。
- 原 WIP/明确白名单基线保留；没有暂存、提交、推送、发布或子智能体。本人真实识屏/真实凭据/实际托盘体验、其他平台、正式信任锚和 Setup/发布门单列未验收。


## 2026-10-04：最后累积门复验（进行中，不用历史 green 遮盖）

- Core09 双变体各7行/27真实生产步骤通过。类型边界扩大检查98项→独立HEAD90项，其中app/settings87项诊断逐条相同；修正新增8项和构建旧债3项，最新受影响46 source files通过。原app/settings没有修改类型债、不放宽mypy配置；扩大48文件仍87项原债，详见mypy-baseline-comparison.json。
- 最新 Ruff07通过，format07 515 files already formatted；文档链接126文件通过。Core10在最后validation TypedDict输入后重新构建，不使用Core09冒充新快照。
- 全量05进度出现1项失败，收集顺序定位 `tests/test_feature_lifecycle_ipc.py::test_real_socket_has_bounded_buffer_and_rejects_oversize_without_revocation`；等待完整traceback后分析，尚不擅自定性为环境或放宽断言/安全帧上限。全量04的3821通过仅是前一快照，不能覆盖这次失败。

## 2026-10-04：最终 Core10 双冻结通过；IPC 夹具根因与 GC 证据

- Core10 带聊天/无聊天均真实七行、每种27步通过；当前冻结输入包含最新 TypedDict 边界，未沿用Core09结果。
- 全量05：1 failed / 3820 passed / 13 skipped / 15 warnings（611.56s），单独复现 IPC 2项时1 failed（35.34s）。服务端已安全拒绝超限并释放 socket，host/state未撤销。
- Qt6.11.1 upstream qlocalsocket_win.cpp 第85行将 pipeClosed queued 到 socket 所属线程。只读 trace：两次blocking wait各15s返回False、仍Connected；同线程 processEvents立即产生Closing/Unconnected/disconnected（无需sleep）。根因为测试Python客户端线程没有事件循环，不是协议限制或生产撤销失败。
- 测试改为socket所属线程的真实 QEventLoop + disconnected信号 + 有界QTimer；不扩大既有15s预算、不改产品64KiB边界。局部5 passed（9.19s），Ruff/format通过；需最终全量/高CPU三遍。产品冻结输入未改，无需用新Core替换Core10。
- GC采样01传入生成data外层而非冻结应用实际 config/app-id 根；before-image active断言立即停止，未执行任何删除。采样02显式绑定两种生成config根并与真实startup receipt逐字段匹配；两种GC都只清除journal证明的失败候选1.0.4，无租约且账本完全不变。非空N2 median411.311ms/p95433.027ms，幂等N4 median110.131ms/p95209.343ms；不存在凭据/profile/用户数据删除。

## 2026-10-04 最终高负载三遍已通过，全量06开始

- 20个自有CPU burn进程的Event/readyQueue同步，三遍各166 passed /1warning（462.46/463.56/464.31s），真实整机CPU采样N425/424/424，median/p95均100%，min99.2/99.8/99.8%；退出时只回收自有burner，无遗留压力。警告是故意重复ZIP条目的负向夹具。
- 最新Core10真实冻结来源已逐字节复核：无聊天185/带聊天201个生产及validation source匹配；3个明确生成的trust/variant配置按合同替换，无源回退或旧构建冒充。
- Ruff全仓check通过；Python源码format515通过；受影响mypy46通过。第一次扩大format .时发现7份未修改历史Markdown代码围栏格式债，全部与HEAD原文（仅CRLF标准化）一致，保存markdown-format-baseline-01.json，不擅自改写旧报告。
- 最终全量06在高负载结束后启动：QT_QPA_PLATFORM=offscreen，python -m pytest -q -ra --basetemp 自有新full-suite-06-tmp；日志full-suite-06.log，结束写final-full-suite-06-result.json；当前未结束不声称通过。普通故障仍根因修复；无方向错误，无Git提交/推送/子智能体。

- 最终文档复核修复INDEX内设计表的字面量`r`n，改为真实换行，不改变链接目标；2026-10-03部分实现报告和索引明确标为历史快照并互链当前报告，保留当时失败与未完成原文。

## 2026-10-04 最终累积验收与工程交接

- Core10双矩阵、实际PYZ/native重读、185/201个live源输入逐字节匹配；仅明确生成validation trust/variant材料不同，产品输入无后续变更。
- CPU满负载连续三遍：第1遍 166 passed, 1 warning in 462.46s；CPU采样N425 median/p95 100.0/100.0%，min 99.2%；wall 464.565s；第2遍 166 passed, 1 warning in 463.56s；CPU采样N424 median/p95 100.0/100.0%，min 99.8%；wall 467.063s；第3遍 166 passed, 1 warning in 464.31s；CPU采样N424 median/p95 100.0/100.0%，min 99.8%；wall 466.274s；20个自有burner全部Event正常停止。
- 最终全量06：3821 passed, 13 skipped, 15 warnings in 514.51s；全量05测试客户端queued pipeClosed根因已用真实同线程QEventLoop修复，未改产品或扩大预算。
- Ruff全仓check通过；Pythonformat515通过；受影响mypy46通过。扩大Markdownformat范围失败7份历史文档逐字节对照HEAD一致，存量债不改写报告；app/settings87项类型债无新增，保留比较证据。
- 更新同组PLAN/HANDOFF/STATUS/SUMMARY、两份设计、README/SPEC/项目入口/LOG索引和最终报告，逐文件numstat待文档稳定后重算；最终文档链接/纪律/差异/保护范围门在最后一轮复验归档。
- 4B-3/4/5 Windows工程自动化完成；人工/正式信任/Setup/其他平台和发布仍单列。无暂存、提交、推送、发布、子智能体。

## 2026-10-04 最终文档、逐文件证据与保护范围关闭

- 文档链接126文件通过；PR报告纪律/产品文案54 passed（1.03s）；Ruff全仓check、515 Python源码format、46文件受影响mypy再通过。命令、实际输出和耗时逐项归档final-delivery-checks-01.json / delivery-*-01.log；最终文字稳定后独立复核02，不沿用历史doc门。
- 白名单80文件逐项what/why/numstat与实际Git改动匹配；保留17文件原WIP快照，HEAD/分支未变、暂存为空；8个保护文件未改，生成产物不进入Git可见范围。改动文本的已知私钥/token头匹配为空，不枚举真实凭据或扩大声称安全审计。
- 最终证据脚本的Windows换行诊断曾将Git LF→CRLF提示误判为空白缺陷；试用autocrlf=false又把合法CRLF误报成trailing whitespace。根因是证据脚本忽略仓库现有autocrlf=true的规范化边界。改为单次命令safecrlf=false，仅静默转换提示，保留现有autocrlf和原空白规则；未改全局Git配置或产品文件字节。真正的PLAN末尾多余空行已单独修复。
- STATUS首行同步最终完成状态；HANDOFF准确停点为工程完成/证据齐全，下一步仅人工体验/正式信任/Setup等独立门。报告和同组记录最终增删行复算；无子智能体、暂存、提交、推送或发布。

- 最终留档后独立复验02全部通过：链接126文件、报告纪律/文案54 passed（0.77s）、diff/白名单/原WIP/保护范围通过，输出final-delivery-checks-02.json；报告80行最终numstat逐项等于Git实际结果。工程交接已关闭，下一步是单列人工/正式信任/发布门，不再遗留本轮实现停点。

## 2026-10-04 人工体验验收准备开始

用户确认终端操作由Codex执行、其负责人工效果；先真实体验再正式信任/分发。使用writing-plans/TDD方法，计划追加同组总设计与记录，白名单快照manual-baseline-20261004-102529。Core10包含synthetic边界，不能当真人识屏证据；采用新的固定生产入口/真实Worker验收构建。正式key不生成、原用户配置不动、不擅自截图/请求模型，无Git发布。

### 2026-10-04 10:31 人工构建公开合同RED→GREEN实施

- 新增人工构建合同测试，先实测`6 failed / 1 passed`：固定人工entry尚不在allowlist、对应entry/builder未实现；日志`manual-build-contract-red-01.log`，没有用自动化Core替代人工GUI。
- 最小实现仅扩展固定可信entry allowlist，在自有快照标记人工临时信任；真实Worker要求synthetic=False，不生成故障候选，不序列化私钥。
- 读取正常启动发现会清理真实HKCU旧自启项；人工entry明确禁止该系统集成查询/写入，识屏/网络/vault端口不替换，此不属于人工验收通过的能力。
- 运行focused+related；Ruff发现两个import顺序问题，随后修正，不交给CI。

### 2026-10-04 人工构建静态门复盘

- 最新focused+related49 passed /12.44s；Ruff全工程通过，4修改Python format通过。
- affected mypy发现manual自启保护lambda形参名字与原接口keyword合同不一致（`_enabled` vs `on`）。改为`on`，不压制类型诊断、不改变真实业务端口。
- manual-core-01已在修正前快照；不将其作为最终源码一致构建。另起manual-core-02，保留01为历史，01不启动给用户。

### 2026-10-04 10:46 真实人工管理窗口已打开

- manual-core-02 no-chat当前187源逐字节匹配，实际EXE/PYZ/native与2103文件摘要重读，459227311B；build168.705s、readback1.851s，无自动driver/boundaries，helper pin重验通过。
- 真实Windows窗口`桌宠设置` PID28320，2.826s可见，own rect[351,88,1165,686]；启动RSS139026432B、10线程、read25106061B/write73B，不用offscreen，隐藏console但GUI可见。
- APPDATA/LOCALAPPDATA/TEMP/HOME均本次E盘独立目录；Vault范围按真实配置绝对路径隔离。未读取用户配置内容或凭据、未触发截图/模型。
- 已向用户给v1目录操作步骤，只请求H1预检/安装摘要和等待启动提示回执；没有把可见窗口当用户确认。
- 小型忽略目录utility的参数/数据字段笔误已读traceback修复（sanitize需env参数，descriptor摘要由raw_manifest取，state字段active）；不将wrapper失败归为生产事务失败。

### 2026-10-04 新全量门与文档品牌边界复盘

- manual-full-tests-01真实结果：1 failed /3832 passed /13 skipped /14 warnings，788.77s。唯一失败是新总设计文字出现外部品牌名，触发既有product-copy门；生产/人工entry测试无失败。
- 先读完整traceback，确认旧设计快照无该品牌词，只替换本轮新设计责任称谓为“执行助手”；保留报告分支/追溯元数据及原测试，不弱化门禁。
- focused品牌回归转绿后启动新的全量manual-full-tests-02；不把第一轮称为全量通过。

### 2026-10-04 10:55 用户UI安装及真实Core启动观察

- 用户实际GUI接受v1目录安装，非代理apply或手写账本；安全元数据观察到revision3/pending=tx-f037c5b933954977afe7dbc2f166d22d。界面易用性仍待明确用户回执。
- 已代启动同配置域真实Core PID15196，4.406s出现桌宠自身窗口；实际factory/绑定/receipt使revision4、pending=null、active1.0.0/enabledTrue。未关闭管理窗口，因其management-only未导入host，不需要热替换。
- manual-core-02两新Core构建returncode0/450.429s；187/203源、2103/2114文件、459227311/461649754B，双readback与helper pin无误。真实v1/v2/v3 ZIP各24450210/24450212/24450211B并记录SHA256；伪签名和错公钥真实拒绝。
- n=5目录预检median5.673s/p955.971s，ZIP3.937s/4.081s；factory generation新增0，独立审计账本active=null，绝不以其数据代替用户profile。
- 双Core Authenticode实测NotSigned，临时signer与正式OS发布者不同门，正式key未生成。

### 2026-10-04 11:03 首条真实人工回执与全量复跑通过

- 用户原话“设置显示已启用”，登记为H1启用状态显示的部分人工确认；不扩大为入口、ZIP幂等、识屏、凭据重启或全部H1/H2通过。
- 只读自身launcher/process和包公共账本：Core PID15196创建时间匹配且仍运行，revision4/active1.0.0/enabledTrue/pending=null；初始管理窗口PID28320已退出，未读取用户日志/配置，不猜退出原因或代认自然退出通过。证据manual-user-enabled-01.json。
- manual-full-tests-02真实结果3833 passed/13 skipped/15 warnings（pytest546.99s，wrapper548.35s），returncode0；第一轮品牌文字失败及修正历史保留。focused+文档106 passed/11.19s，链接127文件通过。
- 下一步真实视觉profile只在本地GUI输入，用户手动识屏判断结果；终端由执行助手负责，不自动发起截图/模型请求，不生成正式私钥。

### 2026-10-04 真实手动识屏人工回执

- 用户原话“结果符合”，按此前要求的一次真实手动识屏登记为结果符合预期；仅这一子项人工通过，不推断自动识屏/凭据重启/自然退出/全部H2通过。
- 只记录用户回执和自身Core身份，证据manual-user-screen-01.json；不读取截图、模型回答、个人配置或凭据。
- 已请用户保存所需设置、关设置窗口、从托盘正常退出并反馈“已退出”；执行助手之后核对所属进程/租约释放并代执行重启，用户不重填密钥再次手动识屏。没有主动请求模型或强退进程。

### 2026-10-04 11:08 自然退出、代重启与凭据可用人工通过

- 用户“已退出”；只核对原Core/Settings创建身份和当前版本内核租约，结果原PID均结束、free/0 leases/0 cleaned。证据manual-user-exit-01.json。未强退进程，没有遗留所属Worker占用。
- 代执行同一隔离配置域正常Core重启，PID21032/creation_time1791083285.5161836，3.373s自身窗口可见；初始RSS112615424B/14线程/CPU1.546875s，read592684597B/write2617B。保留initial/restart launcher metadata，不读个人配置/日志。
- 明确要求“不重填密钥再次手动识屏”后，用户回执“得到了正确结果”；登记真实手动效果和重启后凭据可用行为通过，不扩大为自动识屏或底层密钥审计。证据manual-user-restart-01.json。
- 下一步包级停用：已请用户点停用后先保持停用，反馈入口/拒绝效果，再核对账本/Worker；没有替用户自动触发识屏。正式信任/分发仍待后续。

### 2026-10-04 11:11～11:13 包级停用/重新启用人工通过

- 用户停用后“观察识屏入口已撤销”；账本revision5/disabled/enabledFalse/pending=null。内核占用只剩Core21032的host（revision4 pin正常），无Worker/worker_reservation，不能误称所有代码已卸载。证据manual-user-disabled-01.json。
- 用户重新启用后“入口恢复且结果仍正确”；同一Core21032创建时间匹配，账本revision6/active1.0.0/enabledTrue/pending=null。没有重导入/热替换、没有重新填写密钥或代理触发模型。证据manual-user-enabled-02.json。
- 已请用户在UI“选择 ZIP”导入v1.zip验证同摘要幂等，预期“状态已一致，无需重复操作”；待回执及revision/版本/启用状态核对，不自动apply。

### 2026-10-04 11:15 ZIP同摘要幂等与最终交付证据复验

- 用户实际UI选择v1.zip，反馈“状态已一致，无需重复操作”；公共账本before/after完全一致，revision6/active1.0.0/previous=null/enabledTrue/pending=null，同一Core身份匹配。证据manual-user-zip-idempotent-01.json。
- manual-delivery-audit.py按本轮13 before-images逐个重验，正式policy/4B3旧设计两保护文件未变，实际15文件增删表收敛并与report一致；tracked+本轮untracked diffcheck通过，暂存为空。没有读取真实用户数据，未复算/改写旧80文件报告。
- 最新docs/PR discipline/product-copy 58 passed/0.83s，链接127文件通过；全工程Ruff、4修改Python format、mypy3source files再次通过。
- 已给用户v2目录，要求Core保持运行、UI预检/确认升级1.0.1并先反馈等待释放/草稿提示；不代点accept，不在用户回执前自动退出或重启。

### 2026-10-04 11:20～11:22 升级占用等待、自然退出恢复与生产加载

- 用户实际UI报告旧1.0.0两host（21032/31448、revision4/6）occupied及安全重试警告。只读核验时已free/0 leases，账本revision7/active1.0.0/pending=tx-e05f58fe8266423cbf2a362f9c922dc6；不信任粘贴内容代替当前内核活性，不误称31448身份已实际分类。证据manual-user-upgrade-awaiting-01.json。
- 用户“已退出”；再次核对本次精确EXE无存活进程、版本租约free/0 leases/0 cleaned，才代启动同配置Core。旧state仍pending，没有手写state或补假receipt。证据manual-user-upgrade-exit-01.json。
- 新Core26260/creation_time1791084100.2544065，3.923s自身窗口可见；初始RSS114835456B/14线程/CPU2.234375s、read740077784B/write38697B。真实生产加载确认后revision9/active1.0.1/previous1.0.0/enabledTrue/pending=null，旧版free/新版host租约。证据manual-upgrade-startup-01.json；用户升级后的效果仍待回执。
- 诊断wrapper第一次对immutable mappingproxy直接JSON序列化失败，未改写state或落盘结果；按类型转dict(state.versions)后复验通过。属于审计辅助脚本错误，不是生产升级失败，不修改/弱化业务模型。
- UX-M1：用户粘贴的阻塞提示直接展示内部lease字典和换行字符；属于可读性遗留，登记后继续功能人工验收。后续human-readable PID/用途/版本/自然退出指引+折叠诊断，不降低安全合同。
- 下一步用户确认1.0.1显示、不重填密钥再手动识屏；未代理截图/调用模型、无Git提交/推送/发布。


### 2026-10-04 11:29 升级显示与真实识屏人工回执

- 用户明确“版本显示和识屏结果正确”；仅记录请求的1.0.1显示/真实手动效果，不采集截图、回答、密钥、配置或个人日志，不代理模型请求。
- 只读核对当前Core26260/creation_time1791084100.2544065与精确自有EXE匹配；账本revision9/active1.0.1/previous1.0.0/enabledTrue/pending=null。1.0.0 free、1.0.1仅该Core host revision8占用，清理记录均0。新证据manual-user-upgrade-completed-01.json，历史startup待回执证据保留不覆盖。
- 同组PLAN/HANDOFF/STATUS/SUMMARY刷新准确停点，README/LOG/索引/持续人工报告同步已确认范围。下一步用户UI选择“回滚 previous”，检查目标1.0.0后接受；先反馈等待/草稿，不在用户接受前代apply或重启。
- 当前无新增产品代码修改，不需要将每条人工反馈重复作为全量代码回归；全量3833 passed等保持原快照日期。文档/报告门与本轮before-image审计会再次核验，未提交/推送/发布。


### 2026-10-04 11:37 retained previous回滚等待与owner身份核验

- 用户反馈1.0.0 free、1.0.1 occupied（21804/26260两host）及只允许安全重试。执行助手只读核对公共账本revision10/active1.0.1/previous1.0.0，pending=tx-bafbb0d46e8d4b12b70526051fe7f9b7；journal accepted=true、pending_runtime_release、rollback目标1.0.0，尚未切换或完成。
- 实际内核占用与用户提示一致。仅对租约owner核对精确自有EXE，确认21804是独立Settings（creation_time1791084950.749558），26260是Core（1791084100.2544065且匹配已存launch）。仅输出分类，不输出cmdline；没有访问其他程序输入/配置/用户日志，两版本cleaned_records均0。证据manual-user-rollback-awaiting-01.json。
- 同组准确停点刷新：请用户正常关闭独立设置、从托盘退出本次桌宠，草稿由拥有窗口的用户处理；收到退出回执后再核验所有版本内核释放，代同配置Core通过生产bootstrap恢复/真实receipt，再请用户验证1.0.0显示与手动效果。没有apply/retry/state改写、强退或提前重启。
- 本轮仅新增公开验收证据/记录，没有产品代码变化；将复验本轮before-images/保护文件/逐文件增删及文档/报告门。UX-M1继续登记，不把原始字典可读性问题当安全边界失败；未暂存、提交、推送或发布。


### 2026-10-04 11:41 回滚正常退出、内核释放与生产启动恢复

- 用户“已退出”；精确自有EXE实例列表为空，1.0.0/1.0.1内核租约均free/0 leases/0 cleaned，账本revision10/active1.0.1/previous1.0.0仍保留tx-bafbb0d46e8d4b12b70526051fe7f9b7 pending，journal accepted=true/pending_runtime_release/rollback目标1.0.0。退出核验证据manual-user-rollback-exit-01.json。没有强退、未把PID消失单独当租约证明。
- 保存core-pre-rollback-launch.json后，使用manual-run-entry.py同一no-chat配置域代启动Core PID34672/creation_time1791085260.8862312，4.137s本应用窗口可见。启动样本n1：RSS113954816B/14线程/CPU2.296875s，read740098026B/write38607B；不伪造median/p95或把I/O字节当系统调用次数。当前launch另存core-rollback-01-launch.json。
- 真实生产恢复/加载完成后账本revision12/active1.0.0/previous1.0.1/enabledTrue/pending=null，回滚journal completed；1.0.0持新Core host revision11、1.0.1 free，两版本cleaned_records均0。系统证据manual-rollback-startup-01.json，回滚后的版本显示/实际识屏仍待用户回执。没有手写state、提交假receipt、执行截图/模型或读取配置/真实凭据/个人日志。
- 同组PLAN/HANDOFF/STATUS/SUMMARY及持续报告已同步准确停点。下一步用户确认1.0.0显示、保留密钥再手动识屏；本轮无产品代码修改，无Git暂存/提交/推送/发布，保留全部历史证据。


### 2026-10-04 11:46～11:48 回滚人工确认与卸载确认摘要核验

- 用户对回滚后1.0.0显示/保留密钥手动识屏两项要求回复“确认通过”；只读核对Core34672身份、revision12/active1.0.0/previous1.0.1/enabledTrue/pending=null，真实1.0.0 host pin和1.0.1 free。两权威版本目录存在，公开路径/存在性记入manual-user-rollback-completed-01.json；没有读取个人配置或凭据。
- 用户粘贴卸载确认页。公共journal唯一匹配confirmation_digest=4d9ebc0c544fc2226c4c21500d537f06a79553eef090b3413c6bf9cef1645263；重算确认body/plan_digest通过，delete_versions=[1.0.0,1.0.1]、plan_revision12、awaiting_confirmation、accepted=false，账本pending=null。证据manual-user-uninstall-preflight-01.json，不把粘贴摘要当接受。
- 根因阅读feature_management_ui.py的真实_result模板发现通用“回滚目标”使用plan.active、“签名与兼容性”无操作分支，造成卸载摘要语义错误UX-M2；真实事务pending卸载将enabled=False、不可取消恢复启用，删除仍依赖租约/边界。接受前明确澄清；正式分发前必须修复操作特定摘要并回归，当前不靠改合同掩盖。
- 同组四记录、README、LOG/索引与人工报告同步；下一步用户理解将删除两功能包版本、保留个人数据后UI确认，保持Core运行先反馈等待/草稿/入口变化。执行助手不代理accept、不提前退出重启、不手写state或删除文件。无产品代码修改，无暂存/提交/推送/发布；UX-M1继续记录。


### 2026-10-04 11:57 卸载入口撤销但事务尚未接受的公开核验

- 用户反馈“识屏入口已撤销，但官方功能包还显示1.0.0”。先查公共账本/目标journal/内核租约，不沿用“已接受等待释放”的预期：实测仍revision12/active1.0.0/previous1.0.1/enabledTrue/pending=null，tx-835a6591234543efb4ca73f07eebe2fd仍awaiting_confirmation/accepted=false。1.0.0 occupied（Core34672 host revision11，EXE/creation_time与自身launch匹配），1.0.1 free；两版本安全安装边界/目录存在，两版本cleaned_records均0。新证据manual-user-uninstall-awaiting-01.json。
- 实读事务_continue、Qt queued生命周期及UI结果路径：生命周期准备先于accepted/pending提交，可先撤销贡献而在准备/草稿返回时仍无持久接受记录。当前原因没有可采信的journal证据，不把用户入口观察扩大为卸载等待已提交，也不擅自认定用户没有点击。只请求管理卡片状态/“原因”原文；先保留进程，不重复确认、recover或提前退出重启。
- 初始公开核验文件的通用interpretation措辞在本次结束前改为实测accepted=false说明，避免留下“已接受”的错误解释。没有产品代码修改，Qt UI review规则已加载供源码复核；没有个人日志/配置/凭据/截图/模型回答访问，没有强退、state改写或安装文件删除。
- 同组PLAN/HANDOFF/STATUS/SUMMARY和持续人工报告已刷新准确停点。仅文档/公开证据变化，沿用未变产品代码的全量3833 passed历史，复验文档/报告/白名单before-image与diff门；无暂存、提交、推送或发布。UX-M2正式分发前修复要求继续保留。


### 2026-10-04 12:08 卸载卡片仍启用与“是否因未重启”复核

- 用户补充顶部状态“已启用 · 1.0.0”，无卸载等待/原因；随后询问是否因为没有重启桌宠。复验公共账本仍revision12/active1.0.0/previous1.0.1/enabledTrue/pending=null，原卸载journal tx-835a6591234543efb4ca73f07eebe2fd仍awaiting_confirmation/accepted=false。内核1.0.0占用为Core34672 host revision11和新独立Settings15936 host/settings revision12；两PID均精确自有EXE，Core匹配已存creation_time，Settings creation_time1791086803.4317422，1.0.1 free、cleaned_records均0。证据manual-user-uninstall-status-01.json。
- 未正常退出足以阻止版本物理删除，但正常accepted卸载还应有pending/停用持久证据，当前缺少；所以不能简单归因“未重启”。复核真实按钮链：点击“卸载”只preflight，摘要下“确认本次操作”才submit apply；先核对这一步是否执行，不根据journal反推用户未点击，也不在无接受证据时直接重启或recover替代授权。
- 继续遵守隐私边界，只分类自己的租约owner，不输出完整cmdline、不读取配置/凭据/个人日志/截图/模型回答。无产品代码变化、代理确认、强退、state写入、安装文件删除、暂存、提交、推送或发布。同组四记录/持续报告刷新；UX-M2正式分发前修复要求保留。

## 人工卸载故障修复切片（2026-10-04；实施前）

- 新证据：用户明确点过“确认本次操作”，后台返回“操作失败，未宣称完成 / management_lock_busy”。公共账本仍 revision12/enabledTrue/pending=null；两份卸载 journal 均未接受。不能反推用户未点击。
- 已读代码证明：当前 catch 将任一 StateError(lock_busy) 都标记 management_lock_busy；历史具体竞争锁未被记录，无法追溯。准备阶段可撤销运行时入口，故入口消失并不证明卸载已接受。
- 范围：仅上述五个产品/测试文件；先保存 WIP before-image，再用自有生成包、真实内核锁和 Qt 事件循环写 RED 回归。区分锁来源，保留确认后的安全重试与失败结果，清除误导性旧摘要；同时修正 UX-M2 卸载无回滚/不声称验签。
- 不改变锁顺序、非阻塞管理/租约政策、CAS、租约与删除合同；不自动接受或取消卸载，不改人工 profile/凭据，不读用户日志，不强退。
- 检查：专项 -> 相关 -> 全量；Ruff/format/mypy、实测成本、文档与差异；新冻结包尚未构建前，不能声称用户正在运行的 manual-core-02 已修复。
- 准确停点：先复现锁误分类及重新打开 UI 丢失失败/安全重试，再实现，最后给用户单步操作。正式信任/分发尚未开始。

## 卸载失败修复进展（2026-10-04；新 manual-core-03 尚待人工复验）

- 用户已明确执行“确认本次操作”，返回 management_lock_busy；不是用户漏点，也不能简单归因于没有重启。原实现把不同锁竞争都标成管理锁；无法追溯原错误具体锁来源。
- 当时公开账本 revision12/active1.0.0/previous1.0.1/enabledTrue/pending=null，两份卸载 journal 未接受。生命周期准备先撤销入口，随后接受失败，解释了入口消失与卡片仍已启用并存；不等于卸载成功。
- 已实现：管理/租约/状态锁分源；同一进程重建管理页面保留失败和已确认安全重试；清除旧确认摘要；卸载预检明确全部版本、只检查删除合同、已接受卸载不能重新启用。锁顺序/CAS/租约及删除权限不改变。
- 自动化：7项 RED→GREEN，再补1项真实 queued 撤销→锁失败→安全重试→占用等待→释放删除通过。相关初轮5失败/240通过，根因是新增 rollback 包装遗漏惰性锁工厂；修复后原5项通过。UI+真实QProcess组合43 passed /25.98s。
- 首次新全量在42%出现原生 Qt access violation，未完成、不算通过；Worker handoff单独2 passed /1.41s，组合43 passed，当前完整复跑仍在进行。没有为绕过错误改产品或跳过该测试。
- 新错误文案原生窗口8个明暗/720与1100/1.0与1.75 scale组合通过，仅抓自有窗口；生成验证脚本的编码/文案断言/键盘检查顺序错误已修正，不是产品通过证据的替代。
- manual-core-03 两种 Core 重新冻结、源码边界/原生依赖审计通过；复用 manual-core-02 公开测试锚与原 helper pin，不加载私钥，不改人工配置/凭据。manual-run-entry-03.py 已准备但未启动；运行中的02程序仍是旧实现。
- 下一步：完成本快照全量与受影响时序族满负载三遍、文档/静态门；随后用户只关闭旧设置窗口，代启动03管理专用设置（桌宠先保持运行），重新预检并明确确认，再按占用提示自然退出与安全重试。不得自动接受、强退、重置账本或清理个人数据。
- 本阶段还不是卸载人工通过；ZIP重装、其余人工项及正式T0～T3信任/分发未完成。未暂存/提交/推送/发布，不使用子智能体。


### 原生 Qt 回归定位记录（2026-10-04；不绕过测试门）

- 新累计全量两次在同一真实QProcess握手事件循环原生 access violation，第二次退出码3221225477（0xC0000005）/252.74s；均不算通过。
- 不带新UI用例的其余全量：3836 passed /13 skipped /5 deselected /14 warnings（pytest554.82s）；仅为排查对照，不能替代完整门。
- 最小组合：全部管理UI+runtimeports+handoff可复现；ports+handoff14 passed，新5UI+ports组合19 passed，旧UI+ports组合50 passed（另5诊断性排除）。逐组二分定位到3个widget recreation参数用例；单纯全局投递DeferredDelete的诊断插件未解决。
- 查实测试仅deleteLater+processEvents，并在finally重复close旧receiver；它未证明原widget已实际销毁。修正仅这组自有测试：先停止producer，向明确receiver投递DeferredDelete，断言原widget C++无效后再创建新页面，cleanup不访问已删除对象。不改生产Qt路径、不增加全局mock、skip或延时猜测。
- 修正后原最小组合48 passed /29.03s；当前扩大受影响Qt/IPC/真实Worker时序族执行CPU满负载连续三遍，之后重跑未排除的完整套件。只有最终无排除full成功才算完整门通过。
- manual-core-03的三个产品源文件与当前hash逐项一致，EXE摘要和原公钥复用均核对通过；这次后续修正只有测试，未改变已构建产品。

## 当前验证检查点（2026-10-04；锁失败修复仍待累计门）

- 锁分类/安全重试/卸载文案产品实现和manual-core-03已准备，但03尚未启动，人工卸载尚未接受/完成；不把入口撤销当卸载成功。
- 高负载01第1遍真实QProcess事件循环仍原生AV（3221225477/85.38s）；前一轮48通过不能作为最终门。已回溯页面重建夹具的真实拥有者：仅关闭manager不代表其Qt子对象实际销毁；新增只处理夹具自有manager的DeferredDelete及Cpp invalid断言。生产源码/03冻结快照不变。
- 原48项组合随后48 passed /26.73s；67项时序族高负载02三遍正在执行。本快照完整无过滤pytest未完成，不用排除新测试的诊断3836通过替代。
- 准确下一步：高负载结果与无过滤全量→最终静态/文档/白名单审计→用户只正常关闭旧设置，代启动03管理专用设置，重新预检/确认，再按占用提示自然退出旧host并安全重试。禁止自动确认、强退、重置state或读取私人数据。

## 满负载复验检查点（2026-10-04；无过滤全量运行中）

- 补齐新增重建夹具自有manager/Qt子对象的实际销毁后，67项受影响时序族CPU满負载连续三遍全部通过；每遍CPU median/p95 100%，20个自有burner均正常回收。证据manual-lock-high-load-02/performance.json；之前01 AV保留、不改写为通过。
- 静态门：Ruff、518文件format-check、affected mypy3源通过；报告纪律相关99 passed；文档EOF审计错误已更正，18 before-image/2保护文件/20文件范围/空暂存审计通过。未过滤全量manual-lock-full-03正在执行，当前不能登记全量通过。
- manual-core-03双变体已重建审计，与3个产品源码hash匹配。03未启动，旧Core/Settings不强退，原公共state revision12/pending=null/卸载journal未接受。
- 下一步：完整全量结果→最终文档与审计→用户正常关闭旧设置，桌宠先保留→代启03管理专用设置→用户新预检确认→真实租约等待和自然退出→安全重试核验实际删除/未安装。人工卸载、ZIP重装、正式T0～T3未通过；无暂存/提交/推送。

### 13:21 公开人工状态复验（2026-10-04）

仅核对公开state/journal和精确自有EXE身份：revision12/active1.0.0/previous1.0.1/enabledTrue/pending=null，两份卸载journal仍未接受；Core34672身份匹配仍运行。原Settings15936已自然退出，精确02/03 no-chat EXE枚举仅Core34672，没有其他Settings、没有03进程。无需再要求用户关闭已经结束的Settings。无自动apply/recover/restart、无安装目录删除、无config/真实日志/凭据读取。证据manual-lock-pre-handoff-readonly-01.json、manual-lock-owned-programs-01.json。

下一步在full03通过和最终静态文档门后，重新核对Settings仍无存活，代打开03管理专用Settings，同一原profile；桌宠先保持运行，用户新预检并确认，再由用户正常退出Core释放驻留host。

## full03累计检查点（2026-10-04；Qt原生门通过，已有meta测试同步修正中）

- 无过滤full03：1 failed /3840 passed /13 skipped /14 warnings /488.47s，未发生先前Qt原生AV。唯一已有session-end正对照立即断言后台metadata调用，实际生产GUI seam本来就是异步。
- 补充before-image且只改该旧测试的read/count Event与finally cleanup，不改生产ffmpeg/动画/GUI异步逻辑。相关42项14.94s通过，meta时序族满负载三遍正在执行；随后无过滤full04。full03不算全量通过，原67项满负载三遍证据仍有效。
- 全部产品源码及03双冻结快照未变；新03仍未启动。13:21公开核验旧Settings已退出、只剩身份匹配Core34672；账本revision12/enabledTrue/pending=null，卸载未接受。最终门通过后重新确认无旧Settings，直接代打开03管理专用窗口，不再让用户关闭已结束的Settings。
- 无代理确认、强退、state重置、安装删除或私人数据读取，无暂存/提交/推送。人工卸载/ZIP重装/正式信任分发仍待执行。

## 当前准确停点（2026-10-04；full04未过滤运行中）

- 锁分源/真实queued撤销→失败→安全重试/卸载文案与UI重建测试完成；67项管理Qt/IPC/真实Worker/锁时序族满负载三遍已通过。已有meta正对照的Event同步修正后，42项session-end/reader族另满负载三遍也通过。产品动画/ffmpeg/GUI线程模型未改。
- 静态Ruff/format518/affected mypy3源通过；21文件范围、19 before-image、2保护文件、空暂存审计通过；新人工Core03两变体产品源码hash一致。完整无过滤full04正在执行，不宣称完成。
- 13:21旧Settings已结束，精确02/03 EXE只剩原Core34672，public state revision12/enabledTrue/pending=null，卸载journal未接受。新03管理专用Settings尚未启动。
- 最后步骤：full04与文档报告门→重新核验无旧Settings/原Core身份→代打开03管理专用Settings同原profile→用户卸载新预检/最终确认，观察实际占用→用户自然退出原Core→安全重试与公共状态/安装文件核验。不能代确认、强退、重置账本或删除个人数据。
- 人工卸载/ZIP重装/自动等余项以及正式T0～T3信任分发未完成。无暂存/提交/推送/发布、无子智能体。

## 当前准确停点（2026-10-04 13:48；修复验证通过，新管理窗口已打开）

- 锁分源、失败摘要清除、同进程重建后的安全重试、卸载合同文案及真实 queued 撤销→锁失败→接受→占用→释放删除的回归完成。完整无过滤 full04：3841 passed /13 skipped /14 warnings，pytest498.82s，wrapper499.795s，退出码0；不是排除新测试的诊断运行。先前失败和修正保留为历史。
- 67项管理Qt/IPC/真实Worker/锁时序族及42项session-end/reader族，各在20个自有CPU负载进程下连续三遍通过（每遍CPU median/p95 100%）。已有meta测试只补Event同步及finally cleanup，生产动画/ffmpeg/GUI异步模型未改。Ruff、format518、affected mypy3源、报告门、21文件/19 before-image/2保护文件/空暂存审计通过。
- manual-core-03两变体已重新冻结，三产品源码hash与实际产物快照相符，沿用公开人工测试锚，未加载/生成正式私钥。13:47只剩身份匹配旧Core34672，旧Settings已退出；不再要求用户关闭已结束的窗口。
- 已代启动03管理专用设置：PID7060，creation_time1791092844.339965，独立可见“桌宠设置”窗口，启动4.968s。沿用原manual-session-01/no-chat APPDATA；13:48公共核验revision12/active1.0.0/previous1.0.1/enabledTrue/pending=null，新设置无所属版本租约。未读取私人日志/配置/凭据，未代理确认、强退或手改账本。
- 当前等待用户在新窗口执行卸载→确认本次操作，并反馈实际状态/原因；桌宠先保持运行。只有卸载已接受并实际等待后，才由用户自然退出旧Core，再安全重试，核验版本文件消失与未安装提交。人工卸载/ZIP新安装/自动等余项以及正式T0～T3信任分发未完成。
- 下一轮首先只读核验新设置和原Core身份、公共state/journal/内核租约；不因入口消失宣称完成。无暂存/提交/推送/发布，无子智能体。

## 当前准确停点（2026-10-04 13:55；卸载已接受，等待桌宠自然退出）

- 用户在新版管理窗口最终确认后，回执“等待进程自然释放；不会强制关闭 / version_in_use”。公共账本revision13/enabledFalse/pending=tx-56514a949d454d6792cbc34de9234d83；journal accepted=true/phase=pending_runtime_release/delete_versions=[1.0.0,1.0.1]/deleted_versions=[]。这次接受及安全等待已核验，不是此前锁竞争失败，尚未物理卸载完成。
- 13:55只读公共状态并直接探测既有内核租约锁（create=False，无元数据清理）：1.0.0仅原Core34672的host锁仍占用；1.0.1 free。Core34672与新管理Settings7060的创建时间及精确EXE身份吻合，新设置无功能版本占用；两个版本目录仍存在。证据manual-user-uninstall-await-release-01.json，不读取私人配置/凭据/模型内容或stdout日志。
- 已请用户仅通过托盘正常退出桌宠，新版设置窗口保持打开，退出后回复“已退出”。下一步先核验Core身份结束及两个版本的内核租约释放，再请用户在同一管理窗口“安全重试”；不代点确认、不强退、不手改state或删除文件。
- 只有用户安全重试后，实际版本目录全部消失、账本提交active/previous为空且pending清除，才登记物理卸载；随后验收ZIP新安装及保留设置/凭据后的真实识屏。已接受卸载不能取消重新启用，恢复只能安全继续。
- 产品源码/测试未变；沿用13:48修复验证：无过滤3841 passed/13 skipped、67与42时序族各满CPU三遍及静态门。此次仅公开诊断和记录更新，运行文档/报告/白名单审计，不以历史全量当作本次新跑。
- 人工卸载完成/ZIP新安装/其余人工及正式T0～T3仍待验收；无暂存/提交/推送/发布、无子智能体。沿用原APPDATA，保护个人设置/profile/凭据/记忆/额度/聊天历史。

### 2026-10-04 13:58 自然退出与内核释放

用户回执“已退出”；只读核验Core34672身份已结束，新Settings7060仍运行，1.0.0和1.0.1既有内核租约均free，无孤立锁/保留预占不确定性，不清理元数据。账本仍revision13/enabledFalse/pending卸载、两安装目录仍存在，不能宣称卸载完成。已请用户直接在同一窗口点击“安全重试”，不新发起操作；之后只读核验目录删除和未安装提交。证据manual-user-uninstall-natural-exit-01.json。无产品改动、代理确认、强退、手改状态、个人数据读取或提交推送。前一记录更新99项报告门、127文件链接与21文件白名单审计通过。

### 2026-10-04 14:01 真实物理卸载完成

用户安全重试后回执“操作已完成”。只读公共证据：revision14/active与previous均null/enabledFalse/pending=null/versions={}；卸载journal completed，deleted_versions包含1.0.0与1.0.1；versions目录全部为空、两版本路径均不存在。Core34672已结束，Settings7060仍运行，物理卸载门通过。没有agent代理apply/recover、手工删除、强退或读取个人配置/凭据。下一步用户在原管理窗口选择v1.zip并预检确认；ZIP尺寸24450210及SHA匹配现有构建证据，仍是人工测试锚。个人设置/凭据保留的实际效果在ZIP新安装及真实识屏后验收，不提前登记。证据manual-user-uninstall-completed-01.json；只改记录，无产品/测试改动，不提交/推送。

### 2026-10-04 14:19 ZIP新安装启动确认、首Core未确认与普通设置完成

用户经管理UI确认ZIP新安装并反馈awaiting_startup_confirmation。公共revision16、tx-3a1a2a78ddf949a295bfd96dcb4f23ee accepted install/source_type zip，与revision14空安装before-image一致。代启03正常Core31020，3.923s出现自有窗口，但公共pending持续未清除且无host租约，未判通过。用户关闭管理Settings7060后核验该进程已结束，pending仍未变化；关闭本身不是receipt。追加仅用于自有人工构建的普通设置launcher（--settings，无extensions深链、无源码回退/环境信任override），启普通Settings15792，3.363s出现自有设置窗。再次公共核验revision17/active1.0.0/enabledTrue/pending=null，原install journal completed，Settings真实host/settings原生锁均occupied。无代理确认、没有修改state/receipt、读取私人数据/日志或强退。首Core未完成加载原因未证实，登记UX-M3待复现诊断，不声称关设置修复。

原Core31020仍无host绑定，下一步用户托盘自然退出再代正常重启，核验Core租约并请用户不重填密钥验证原设置与识屏。普通Settings可保持打开。产品和测试无改；全量历史3841/两时序族各满CPU三遍不重跑，纯记录验报告/文档/白名单与diff。正式信任锚/分发未完成，无暂存/提交/推送/发布。

本轮记录门：python -m pytest -q tests/test_check_docs.py tests/test_pr_report_discipline.py tests/test_report_gates.py → 101 passed/1.09s；21文件白名单/19 before-image/2保护文件均通过，git diff检查通过、暂存空。产品源未变，不重跑全量。

## ZIP重装后的Core自然重启与实际host加载（2026-10-04 14:30）

- 用户已自然退出，明确要求终端操作完成后直接汇报，不逐步等待。只读精确EXE核验原Core31020/普通Settings15792均已结束；同一03 EXE没有残留进程，账本revision17保持，未强退或修改state。
- 代执行`python .scratch/phase4b-local-management/manual-run-entry-03.py --variant no-chat --mode core`，沿用原APPDATA；实际Core30256/creation1791095122.0622613及自有桌宠窗口启动。真实冷启动n=1：4.218s，RSS113209344B、线程14、CPU2.5s、累计读550048972B/写2632B；单样本不写median/p95，非精确加载耗时。
- 14:30公共只读验证revision17/active1.0.0/enabledTrue/pending=null，ZIPinstall事务completed；精确身份Core30256持有1.0.0/revision17/manifest digest94fef…1097的真实native occupied host租约c1ea6a78eb1f43f2a07b39c18718ecf8；正常execution resolver resolved。核验不执行factory，不提交receipt，不读取配置/凭据/原始日志，不清理租约，不触发截图或模型。
- 公共证据`manual-user-zip-reinstall-core-natural-exit-01.json`、`manual-user-zip-reinstall-core-restarted-01.json`；生成物留ignored目录，报告保留可复现命令与必要数字。刷新同一组任务记录/README/LOG/报告，产品/测试源及03hash未变，文档相关门续验而不重跑全量。
- 下一步：用户一次检查入口恢复、“已启用 · 1.0.0”、原设置保留、不重填密钥且真实识屏正确；这些真实效果未确认。UX-M3首次Core未完成pending加载原因仍未证实，没有把后续正常加载说成根因已修复。正式T0～T3、自动/第二变体仍待验收，无提交/推送/发布/正式私钥操作。
- 本轮记录门：python -m pytest -q tests/test_check_docs.py tests/test_pr_report_discipline.py tests/test_report_gates.py → 101 passed/1.58s；21文件白名单/19 before-image/2保护文件审计通过，git diff --check通过、暂存空。初次审计发现本轮追加LOG末尾多余空行，删除该空行后转绿，不改历史内容或放宽检查。

## ZIP重装的数据保留体验用户通过与T0前置核对（2026-10-04 14:52）

- 用户明确回执“入口恢复，原设置保留，未重填密钥，结果正确”；限定no-chat人工03/1.0.0物理卸载后ZIP重装、真实加载与Core自然重启。记录为原设置/凭据可用性及真实手动识屏通过，不读取私人配置或请求、不推导字节完全相等，不勾自动/第二变体等其他人工门。
- 公共只读核验state revision17/active1.0.0/enabledTrue/pending=null与原ZIP事务completed；精确Core30256/创建时间及真实occupied host租约匹配。证据manual-user-zip-reinstall-confirmed-01.json（ignored公开元数据）；无强退、state写入、代理receipt、租约清理或额外收费请求。
- 实读feature_build_policy、prepare_core与既有验签/签包代码：正式锚和helper pin仍为空；现有Core构建入口仅manual/validation并固定非发行标记；现有签名key仅构建内存临时值，没有正式密钥保管/签包/生产构建入口。不能把119项前置合同绿灯当作正式key/分发通过。
- 前置测试python -m pytest -q tests/test_feature_manual_acceptance.py tests/test_feature_packages.py tests/test_screen_delivery_build.py →119 passed/1 skipped/22.40s。本轮不改产品/测试源，完整全量不重跑；3841和CPU三遍保持历史快照。公开文档10份before-image保存后更新设计T0候选/同组记录/报告与README、LOG、索引。私钥归属/加密保管/离线备份/轮换边界待用户确认，不生成正式key，不改正式信任。无提交/推送/发布、无子智能体。
- 下一步一次汇报本项人工门通过，并请用户确认T0候选（用户持有、仓库外加密PKCS8、专用本机密码输入、独立离线备份、轮换/泄露通过更新Core锚）；确认后才实施T1～T3。UX-M3仍未根因证明修复，其他人工门继续单列。
- 本轮记录门：python -m pytest -q tests/test_check_docs.py tests/test_pr_report_discipline.py tests/test_report_gates.py →101 passed/3.60s；21文件白名单/19 before-image/2保护文件审计通过，tracked及明确untracked范围diff检查通过、暂存空。三产品源码hash保持03已验收快照；本轮无产品/测试变更，TDD不适用，既有119项前置关联测试已实际复跑。

## 2026-10-04 15:20 用户暂缓正式信任/分发，清理磁盘只读盘点

- 用户提出scratch占用过大；责任归因是多轮冻结构建和夹具未及时GC，不归因用户。当前真实桌宠不因暂缓正式分发合同而停止运行；当前仍非正式发行版本。
- 不跟随链接的元数据扫描157.896秒、251719文件、28个reparse跳过、0读取错误；合计37567386254字节。盘点与明确清单均为小型JSON，清单294目录/132245文件/33483764133字节，尚未删除。
- 保留运行Core03、原人工session与安装、任务记录、回滚before-image和证据；生成源码副本仅清理旧构建，当前Core03副本保留。只读确认PID30256与revision17真实状态未变。
- 下一步须具体范围确认后删除与复核；不运行新的构建或全量产品测试，不读取用户数据、不终止进程、无提交/推送。7个公共记录修改前的压缩before-image仅用于保护原WIP，不复制任何构建大目录。

- 盘点清单/暂缓记录更新后，文档链接、PR报告纪律与报告门101 passed /3.59s，git diff --check通过、暂存为空。纯记录/文件元数据工作，产品源码未改，不再次跑全量或冻结构建；具体删除尚待用户范围确认。

## 2026-10-04 15:31 用户确认范围，进入只读复核与物理清理

- 用户确认原294目录/132245文件清单；preflight将授权绑定SHA256 6e6b710eef3f647a534ae4a811a3db58785205d05d04716281b4be41fbbfd539。路径不与tracked/保护根/live引用相交，公开state17不变。最新没有scratch EXE进程；未停止任何进程，不猜测其退出原因。
- 本轮仅删除该白名单生成物，逐目标复查reparse/包含边界/盘点和live引用，使用原生PowerShell，失败分类记录后继续其余安全目标，不改用户文件/系统ACL/Qt产品逻辑。

## 2026-10-04 15:50 — 已授权scratch清理完成与实际复核

- 用户明确授权‘确认按这个范围清理’；原计划SHA256=6e6b710eef3f647a534ae4a811a3db58785205d05d04716281b4be41fbbfd539。全部294目标先完成重解析/包含边界/保护根/跟踪文件/运行引用/文件数和字节校验，删除每项前再次确认。
- 实际原生PowerShell Remove-Item -LiteralPath执行294目录、132,245文件，0失败/跳过，670.909秒（包含完整双次安全复核）。不强退进程、不改ACL，不使用跨shell字符串删除。删除前E空闲190,323,269,632字节，之后224,076,013,568字节。
- 用户确认后仅删除原清单294个目录/132,245个文件；全部完成，无失败/跳过、无剩余目标。移除旧冻结构建、重复依赖与生成夹具31.184 GiB。
- 清理后全根只读盘点：34.987 → 3.809 GiB，119,485文件；跳过原有28个reparse point，无盘点错误。E盘可用空间实测增加31.435 GiB（33,752,743,936字节）；文件逻辑大小与磁盘净释放分开报告，后者含并发系统活动。
- 保护核验：9项原文件摘要、3个保留ZIP摘要一致；所有保护根与21份Git跟踪任务记录存在；原APPDATA公开state仍revision17/active1.0.0/enabledTrue/previous=null/pending=null。不读取私人配置/凭据/截图/聊天内容，不迁移数据域。
- 当前Core03两变体dist/源码、完整manual-session-01及其安装功能包、manual-core-02/packages、最终helper20/Worker06、before-image、原WIP与公开验收证据均保留。清理前后未采样到scratch EXE；本轮没有终止进程，不将此称为持续运行验收。
- 短证据：scratch-cleanup-approved-scope-20261004.json、scratch-cleanup-result-20261004.json、scratch-cleanup-after-20261004.json及逐目标journal；计划原SHA256不变。原清单/预检中的deletion_performed=false是当时事实，实际已删除以result/after为准。
- 只读复核使用公开FeatureInstallStateStore.read与文件元数据/SHA256，不执行factory/Worker、不代理加载确认，不读取私人数据内容。最终全根盘点108.866秒，含摘要和状态核验共111.657秒；28个原reparse点继续跳过，不触碰链接目标。
- 本轮仅清理生成物和更新记录：产品运行路径、稳态系统调用/网络/磁盘/线程未改变；新开销只在一次性清理/核验时发生，不为清理重建GB级产物。产品全量与满CPU族/双冻结测试不重复，历史通过仍按原日期。文档链接/PR报告纪律/报告门101 passed（3.96秒），git diff --check通过、暂存为空。
- 同组7份public记录before-image和LOG/LOG-INDEX的小型before-image保留；没有新增大备份、源码变更、暂存、提交、推送或发布。后续重复构建应及时清理旧产物，不能再无上限累计dist；这是一条维护教训，不声称自动GC已实现。
- 使用效果：原APPDATA/安装1.0.0和当前程序不变，用户无需运行清理命令；正式信任/分发按最新决定暂缓，UX-M3和未执行人工门保留。


## 2026-10-04 16:06 当前授权发布准备

- 当前目标：将既有 Phase 4B 新修改提交并正常推送到 `origin/codex/phase3-worker`，不发布安装器、不合并、不强推，不实施暂缓的正式信任锚/分发 T0～T3。
- 基线：本地与 fetch 后远程均为 `bd048d57518902532ea82b6b4ba277e79b16871a`；当前白名单 85 个源码、测试与 Markdown/许可证文件（1,739,707 字节）。源 WIP 保留，不提交生成物、原始日志、私钥、个人配置、凭据或聊天数据。
- 本次新门禁：Ruff 全仓通过；format-check 518 文件通过；专项 223 passed /1 skipped /1 warning（139.93s，warning 为重复 ZIP 测试夹具）；affected mypy 38 个修改源文件及默认 26 个文件均通过。`pet/app.py` / `pet/modern_settings_dialog.py` 的既有类型债务未纳入 affected mypy，不声称全仓 mypy 清零。
- 准确停点：未过滤全量 pytest 运行中；随后执行 Qt/IPC/进程相关族满 CPU 连续三遍，更新报告与同组记录，再核验显式暂存、提交并正常推送，最后独立比对远程 SHA。当前尚未暂存、提交或推送，不借用历史绿灯宣布本次门禁通过。
- 证据：同组 ignored `publication-20261004/` 内保存 85 文件白名单/源摘要、仅公开文档 before-image、有限敏感字面量审计与本次测试日志；这些生成证据不入库。文档记录更新不更改产品行为，故本轮 TDD 不适用；既有实现的失败测试和人工证据按日期保留。
- 保护与限制：刚清理的旧生成物不重建；Core03 两变体、原 `manual-session-01` 数据、保留包/helper/Worker 和既有证据保持原路径。不代人工确认、不启动识屏或收费请求、不强退进程。人工门/UX-M3 未证实原因、其他平台、正式信任与 Setup/发布仍单列。
- 当前步骤：[x]用户授权/远程核对/文件白名单与敏感排除 [x]静态与专项门 [ ]全量与三遍高负载 [ ]报告与精确暂存 [ ]本地提交 [ ]远程 SHA 核验。用户无需自行执行终端命令；全部操作完成后一次汇报结果。



## 2026-10-04 16:15 发布全量门通过、高负载门开始

- 当前未过滤全量新通过：3841 passed /13 skipped /14 warnings /571.34s；wrapper 572.587s、exit_code=0。13 跳过/14 warning 原因在 ignored full.log 保留，不等于失败或已验收的原生门。
- 用现有 measurement 工具执行 21 个显式 file/test target 的连续三遍满 CPU 风险门；20 个 fixture 仅由本次 runner 持有 Event 生命周期，不终止普通用户/Core 进程。全量已结束，不与压力门重叠执行。
- 所有源摘要保持白名单基线，只有本次授权文档更新；阶段记录继续同组维护。尚未暂存/提交/推送。


## 2026-10-04 16:25 发布风险门中间证据

- 满 CPU 第1遍真实结果：154 passed /270.32s，wrapper272.010s；245 个系统 CPU 样本 median/p95=100.0/100.0%、min99.9%。第2/3遍仍执行中，未提前宣称完成。当前 fixture 20 个，由 runner 的 Event/finally 持有并回收。
- 本机环境：Windows build26100、Python3.11.1、PySide6 6.11.1、psutil6.1.0、20逻辑CPU；不重建大型冻结产物。
- 可选 Node REPL 等待辅助未启动，工具报 failed to write kernel assets /os error3；未创建/修改项目产物，直接继续使用原 PowerShell runner 与真实日志。该环境辅助错误不是产品测试失败，不改变安全门、源码或整体路线。


## 2026-10-04 16:33 发布门禁完成与临时文件策略限制

- 用户当前授权：将本次新修改提交并推送到 `origin/codex/phase3-worker`。基线 `bd048d57518902532ea82b6b4ba277e79b16871a`；85 文件白名单，保留原 WIP，无产品源码再次修改。仅源代码、测试、公开文档/许可证和本组五份 Markdown，不含生成物/日志/私钥/个人数据。
- 新验证：Ruff 全仓、format-check 518、affected mypy 38 修改源文件及默认 26 源文件通过；专项 223 passed /1 skipped /1 warning（139.93s）；未过滤全量 3841 passed /13 skipped /14 warnings（571.34s）。app/settings 既有类型债、平台/原生 helper 条件跳过均如实保留，不声称全仓 mypy 或所有实机门新通过。
- 压力门：20 个自有 CPU fixture 下，Qt/IPC/native GUI/启动/内核锁/租约/Worker/session-end/reader 21 显式 target 连续三遍均 154 passed；wrapper 272.010/267.890/319.995s；各遍系统 CPU median/p95 均100.0%，采样 245/239/286。runner 已正常结束并通过 Event/finally 回收所有自有压力 fixture；未强退用户/Core 进程。
- 精确执行点：本次报告与同组记录已更新，随后复验文档门、核对85文件精确暂存及敏感字面量，创建源码提交并正常 push；以 `ls-remote`/fetch 独立比较远端 SHA，再封存最终交接。当前尚未提交/推送，不能从门禁通过推断已经远程发布。
- 本轮 TDD 不适用：仅为已有源修改的授权提交/推送与留档，不更改产品行为；既有 red/green、双冻结与人工证据按原日期保存。工程报告逐85文件列说明与基线 numstat，不冒充所有行是本轮所写。
- 存储注意：未重建大型冻结产物。删除本轮五个临时测试目录的 exec_command 被环境策略拒绝，未绕过；只读盘点共 132,573,841 字节（126.432 MiB），另跳过2个reparse条目，当前仍保留且不入库。先前31.184 GiB旧生成物清理仍为已完成历史事实。
- 保护/限制：现用 Core03 双变体、原 APPDATA/profile/凭据和已安装包保留原路径；不启动截图/模型请求、不代确认、不强退、不改真实 ACL。正式信任锚/分发 T0～T3按用户决定暂缓；UX-M3原因未证实及自动/草稿/多实例/chat等未测人工门、其他平台/Setup/正式发行不冒充完成。
- 当前步骤：[x]用户授权/远端基线/白名单 [x]静态与专项 [x]全量与三遍满负载 [ ]最终文档门/精确暂存 [ ]源码提交与正常推送 [ ]远端SHA核验/最终留档。所有终端操作由助手执行，完成后直接汇报，用户无需中途回应。

## 2026-10-04 16:36 精确暂存后的文档空白修正

- 显式85文件暂存及有限敏感字面量审计通过；cached diff门捕获新增WORKLOG末尾空白行（先前未跟踪文件不被普通git diff检查覆盖）。仅规范本轮追加记录的EOF为单换行并复验cached diff，不改变产品或测试源。

## 2026-10-04 16:40 源码提交及独立远端核验完成

- 主实现commit `98bbfba78232bb0881628cbbfb36cfdaa519fcb8`，85文件/+14095/-156；正常push到origin/codex/phase3-worker成功，ls-remote/fetch与HEAD比对相同、ahead/behind0/0、源工作树清洁。
- 精确暂存/有限字面量审计/cached diff通过；127文件链接检查与101项报告门通过。无产品或测试源再次修改，记录收尾为纯文档豁免（仅需文件说明/文档门，既有全量/满CPU源快照不变）。
- 最终同组交接/摘要/状态及README/LOG/工程报告同步实际源码提交与远端状态；本记录封存提交不写自己的SHA，最终HEAD/远端校验另保存ignored receipt。临时夹具126.432 MiB因策略拒绝保留，不绕过；正式信任/分发与人工余门保持暂缓/未验收。
