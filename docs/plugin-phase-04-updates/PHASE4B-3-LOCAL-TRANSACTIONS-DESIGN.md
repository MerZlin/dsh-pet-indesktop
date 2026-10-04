# Phase 4B-3：官方功能包安装、升级、卸载事务设计

> 基线：2026-10-03，基线提交 `bd048d5`；精确分支见 [事务检查报告](../PR-REPORT-FEATURE-PACKAGE-TRANSACTIONS-2026-10-03.md)。
> 当前状态（2026-10-04）：OS LPAC 沙箱、真实生产事务/生命周期/加载 receipt 与管理 UI 已实现；最新Core10双冻结七行、性能实测及最终全量/高负载三遍/静态门均通过，Windows工程闭环完成，尚未提交或推送。
> 上游设计：[Phase 4B 本地管理总设计](PHASE4B-LOCAL-MANAGEMENT-DESIGN.md)；前置实现：[Phase 4B-2 跨进程版本租约](PHASE4B-2-CROSS-PROCESS-VERSION-LEASE-DESIGN.md)。


## 历史检查点与方向修正（2026-10-03；先于本轮连续实施，不是当前状态）

本节保留首次发现普通子进程无法兑现权限合同的原始事实和当时停点；后续经用户批准实施的LPAC、production receipt与管理UI见[2026-10-04连续收尾报告](../PR-REPORT-PHASE4B-MANAGEMENT-CLOSEOUT-2026-10-04.md)，不要用下文的历史“未实现”覆盖当前证据。

原计划中的“隔离子进程”不能自动兑现“无真实用户权限”。本机使用完全生成的测试文件证明：清空环境并替换 HOME/USERPROFILE/APPDATA 后，普通 Python 子进程仍能读取 cwd/HOME 外的测试文件（`OUTSIDE_HOME_READ=True`）；没有读取真实凭据、聊天或个人 profile。

**安全边界没有降低**：新增 `ProbeSandbox` 端口要求 host 与原生 Worker 同时被 OS 隔离，禁止用户数据、网络、桌面访问，并证明合法 factory、Worker hello 与优雅退出全部成立。默认没有 runner 时返回 `self_check_sandbox_unavailable`，确认安装被拒绝，state 仍为 uninstalled，factory/Worker 均不执行。不能用干净环境、Python audit hook、假 Worker 或 `StubChecker` 当成生产沙箱。

**尚待确认的方向**：建议追加 Windows OS 沙箱及专用冻结 probe 交付设计（例如经权限验证的 AppContainer runner），先验证 Core/Qt 依赖可读、用户数据/网络/桌面确实不可用、进程树可回收，再实现 production 启动确认与 queued 生命周期。此处仅记录建议，尚未实现 launcher、变更系统 ACL、安装系统组件或扩大冻结产物范围。

**已实现且仍需整体审查**：不可变 plan/token 持久绑定、账本初始化、目录/ZIP staging、签名与兼容性复验、management/leases/state 锁序、install/upgrade/uninstall 状态切换、前后镜像恢复、旧 active 回滚、草稿阻塞端口、删除恢复、延迟 GC 和拒绝孤立版本。验证测试注入 `StubChecker`/加载摘要，是状态机合同证据，不是真正生产 host/Worker 执行证据。

**尚未实现/通过**：OS sandbox runner；真实 Worker 自检执行；Qt queued 生命周期适配器；Core/独立设置启动确认接入；生产加载凭据及失败后常驻 host 的重启回滚策略；全部故障点/真实权限、磁盘、杀软门；最终冻结 Core 人工验收。`confirm_startup()` 目前只是内部验证摘要端口，不能把测试提供摘要冒充实际 Core 加载确认。

**与初版方案的可见取舍**：fresh 预检先用公开 `commit()` 建立空账本，revision 从 0 变 1，但不启用功能；ZIP 多卷及 ZIP64 central directory 当前明确拒绝（小包中单文件 force-ZIP64 头不改变 EOCD 时不在此拒绝范围）；ZIP central-directory 在解析分配前检查条目数和大小；卸载把 ledger 版本与 journal 已证明的延迟 GC 目标一起绑定，未知目录只阻塞恢复，不推断安装状态；事务 journal 使用严格 `tx-<32hex>.json` 命名，与 `op-<child-id>.json` 账本收据分开。

验证与限制见 [当前部分实现报告](../PR-REPORT-FEATURE-PACKAGE-TRANSACTIONS-2026-10-03.md)。本检查点不等于 4B3-4/4B3-6 通过；仍无管理 UI、正式 CLI、远程下载、Setup 或个人数据清理。

### OS runner 官方依据与待证实项（不是本机沙箱验收）

已查阅 Microsoft Learn 的 `AppContainer isolation`、`Launch an AppContainer` 与 `CreateAppContainerProfile`。官方启动说明使用 package/capability SID、DACL 交集及 STARTUPINFOEX 创建隔离进程，不是给普通 subprocess 更换环境即可实现。AppContainer/LPAC 只能作为候选：具体文件、网络、桌面/截屏、Qt 加载及子进程继承边界仍须生成数据实测，不以技术名称代替验收；profile/工作目录的创建和清理也须明确授权范围。当前没有执行这些系统操作。

官方来源（URL 仅作证据，不依赖外部链接检查）：

```text
Microsoft Learn / AppContainer isolation
https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-isolation
Microsoft Learn / Launch an AppContainer
https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer
Microsoft Learn / CreateAppContainerProfile
https://learn.microsoft.com/en-us/windows/win32/api/userenv/nf-userenv-createappcontainerprofile
```

## 1. 范围与非目标

本阶段为 `official.screen-understanding` 建立 Qt 无关、可恢复、可幂等且不会误执行或误删除的本地功能包事务服务。输入仅限本地目录和 ZIP；事务数据继续位于现有安装根：

```text
plugins/official.screen-understanding/
  state.json
  versions/<version>/
  staging/<operation_id>/
  transactions/<operation_id>.json
  locks/management.lock
  leases/
```

本阶段不做管理 UI、正式 CLI、远程下载、Setup、第三方包、用户数据迁移、个人数据清空或运行中的 host 热替换。`state.json` 仍是唯一安装状态权威，不能以 `active.json`、目录扫描或孤立目录恢复安装状态。

## 2. 事务合同

新增 `pet/feature_package_transactions.py`，暴露：

```text
inspect()
preflight_install(source)
preflight_upgrade(source)
preflight_uninstall()
apply(operation_plan)
recover_pending()
collect_garbage()
```

`OperationPlan` 固定绑定 operation id、操作类型、读取到的 revision、目录/ZIP 源指纹、staging 验证摘要、目标版本与 manifest 摘要、active/previous/enabled 快照、占用与回滚动作、确认摘要和不可伪造的确认令牌。`OperationResult` 使用明确状态：`completed`、`idempotent`、`awaiting_confirmation`、`awaiting_release`、`awaiting_startup_confirmation`、`rejected`、`recovery_required`、`failed`。

预检不持有事务锁，也不执行 factory、Worker 或 GUI/Qt 对象。`apply()` 重新读取状态，校验 revision、源摘要、staging 摘要、信任和兼容性后才可以进入管理锁。每次 `FeatureInstallStateStore.commit()` 使用独立子操作 ID；父事务 ID只写入 `pending_transaction`，详细阶段和补偿证据写入事务 journal。

## 3. 安全 staging 与完整验证

- staging 与 `versions/` 必须同卷；目标目录只通过同卷原子重命名一次性落盘，不原地覆盖已有版本。
- 目录复制和 ZIP 解压拒绝绝对路径、`..`、符号链接、Windows reparse point、硬链接逃逸、重复条目、大小写冲突、未登记文件和解压/文件/总大小超限。
- 完整复用 `FeaturePackageVerifier`：manifest、签名、payload 摘要、Core/API/platform/capability 和官方信任全部通过后，才允许隔离自检。
- staging、候选版本、事务记录和 GC 只处理本事务拥有且可证明位于安装根内的路径；失败只清理本 operation 的 staging。

## 4. 安装、升级、启动确认与回滚

统一顺序：目录/ZIP → staging → 完整预检 → 用户确认 → 隔离 host/Worker 自检 → 不可覆盖版本落盘 → pending state → active/previous 切换 → Core/设置启动加载确认 → 延迟 GC → 完成。

首次安装成功自动启用；同版本同 manifest 摘要重复安装返回 `idempotent` 且不改变启停状态；同版本不同摘要拒绝覆盖。升级保留原 `enabled` 状态，无法证明降级兼容时拒绝。pending transaction 存在时解析器禁止执行，启动确认成功才清除 pending；失败优先回滚到可验证 previous，否则保持禁用并返回 `recovery_required`。旧 host 已进入解释器时不热替换。

## 5. 卸载与延迟 GC

卸载顺序固定为：草稿保护 → 拒绝新任务 → 撤销贡献 → 停 Worker → 检查所有版本租约 → 再次确认删除边界 → 删除版本 → 提交未安装状态。任何 host、settings 或 Worker 未释放时返回 `awaiting_release`，不强制关闭进程；删除失败保持 pending/recovery，不提交“未安装”。用户 profile、凭据、安全存储、记忆、额度和聊天历史不在删除边界内。

成功升级只在权威状态保留 active 与 previous；更旧且无租约版本先从权威版本列表移除，再进入延迟 GC。GC 失败不能影响 active，也不能让目录扫描重新建立安装状态。恢复只允许继续安全清理，不能让已接受卸载的功能复活。

## 6. 运行时适配边界

事务服务只依赖 Qt 无关的生命周期端口：草稿查询/处理、拒绝新任务、撤销贡献、停止 Worker、加载确认。实际 Qt 操作必须由所属线程通过 queued 调用完成；没有 UI 时只返回草稿阻塞信息，不自动保存或丢弃。4B-2 的 host/settings/Worker 版本租约继续由 `FeatureVersionLeaseCoordinator` 管理，事务服务不以 PID、TTL 或目录枚举代替 OS 锁。

## 7. 验收门

测试必须覆盖不安全路径与 ZIP、签名/兼容性、预检无执行、幂等/冲突、revision 抢占、事务锁竞争、租约占用、升级/自动回滚、上一版本不可用、草稿保护、删除中断、重启恢复、重复恢复和孤立目录不复活。实施完成后另行生成 `docs/PR-REPORT-FEATURE-PACKAGE-TRANSACTIONS-2026-10-03.md`，登记到 `docs/INDEX.md`；报告中的计划/实现/自动化/实机/用户确认/提交/推送状态严格分开。

## 8. 用户可见效果与限制

本阶段完成后，后台具备安全处理官方本地目录/ZIP 的安装、升级、卸载、回滚和恢复能力，但暂时没有新的管理 UI 或正式 CLI。功能包被进程占用时不会被强制删除，事务中断不会把半成品显示为成功，也不会从孤立目录误复活；Setup、远程分发、第三方包和完整人工冻结验收仍留在后续阶段。


## 2026-10-03 已授权连续收尾计划（当前权威，已落盘）

### 目标与事实
Windows LPAC + Core-owned probe -> 4B-3 生产事务 -> 4B-4 常规域扩展管理 -> 4B-5 双冻结 Core。
保留现有 WIP / 4B-1 / 4B-1.5 / 4B-2；bd048d5 基线。此前 51 passed 是历史事务测试，不是沙箱或生产闭环证明。
工程完成需要本机原生安全门、真实事务/Qt/UI、带聊天/不带聊天的无识屏 Core 矩阵全部通过。
人工真实识屏/凭据/托盘、其他平台、正式信任锚、Setup/发布单列。不开子智能体，不自动提交或推送。

### 安全架构与硬门
- 可信父端 ctypes + Core-owned 独立无 GUI PyInstaller onedir helper；校验完整摘要清单，禁止源码/PYTHONPATH 回退。
- LPAC ALL_APPLICATION_PACKAGES opt-out、零 capabilities、创建时 Win32k disable、显式继承 stdio。
- suspended 创建 -> 专用 Job (active process 1 / no breakaway / kill on close / 512MiB) -> 可信父端检查 token、属性、Job -> resume。
- 每 probe 默认 30s，单条 64KiB、累计有界；不信任子端 isolation 布尔值，不降级普通 subprocess。
- operation-owned 只读 helper/candidate/policy、独立可写 scratch。只创建/删除自己的 AppContainer profile；不授予账本、leases、真实用户目录。
- 正/负对照 canary：RO/RW、根外文件、ALL_APPLICATION_PACKAGES、独立用户目录、生成凭据/DPAPI/注册表、IPv4/6 loopback、生成桌面对象、子进程/进程句柄、超时/崩溃/超量输出/父退出。
- canary 原生权限矩阵及实际 host FeatureDefinition / Worker HELLO-SHUTDOWN-优雅退出通过前，不推进生产安装入口。
- 普通 HOME/audit hook 不是安全边界；需广泛文件/网络/桌面或管理员授权才可执行，属于方向错误，暂停汇报。

### 事务合同
保留 inspect/preflight_install/upgrade/uninstall/apply/recover_pending/collect_garbage 和八种明确状态。
增加 retained-previous-only preflight_rollback、revision-bound set_enabled、仅未接受预检可取消。
LifecyclePrepareRequest / StartupLoadPermit / StartupLoadReceipt 绑定 op、revision、version、manifest、owner 与真实租约。
确认后重验 -> 沙箱自检 (失败不影响旧功能) -> 草稿/生命周期准备 -> 管理锁序列化 accepted journal + pending -> 释放 -> 不覆盖落盘/切换 -> 真实加载 receipt -> GC。
management -> leases -> state；不在锁内等待 GUI/进程、哈希、复制。
修复 accepted-before-pending 恢复：只认唯一且 before-image 匹配的意图，冲突不按时间/目录猜测。
每新提交独立子 ID、崩溃重放同意图 ID；更高 revision 不被覆盖。
resolver 分 execution / disabled configuration / pending load permit；permit 仅 host/settings，Worker pending 恒禁。
加载注册准备不可执行；真实 receipt 后清 pending；失败 rollback 也必须真实加载确认。导入后 pin 等自然退出，不热替换。
同版本启停刷新 generation/授权不重复导入；新安装启用、升级保持 enabled、同摘要幂等不启用、外部降级拒绝。
主动回滚仅重新验证账本 previous；active/previous 保留；更旧先离开权威列表，再无租约 GC。

### 进程与卸载
GUI facade + 事务后台线程 + queued 生命周期；同用户短 QLocal 端点绑定 root/owner。
有界消息仅草稿/准备释放/刷新授权/加载确认；IPC 回执不替代内核租约。
owner 集合变化重新准备；草稿由所属 UI 保存/丢弃/取消，无 UI 返回阻塞。
拒绝任务 -> generation 失效 -> 撤销菜单/设置/搜索/命令/订阅 -> 停所属 Worker -> 全版本租约释放。
管理专用设置不启动桌宠/Worker，不无意义 pin host。
冻结 Worker 租约接管必须在实现导入前；独立 probe 模式须已隔离，只 HELLO/SHUTDOWN。
卸载接受后不能取消复活；边界重验 -> 全文件安全删除 -> 未安装提交。任一删除失败保持 pending/disabled/recovery。
用户 profile/配置/凭据/记忆/额度/聊天保持；孤立目录不收编、不删除，不作为状态来源。

### UI 合同
现有常规域增加扩展管理，不改变当前十个导航域/顺序；复用 page/section/card/row 与语义图标。
官方唯一包 + 稳定深链/搜索：扩展、插件、屏幕理解、安装、升级、卸载。
UI 只调用服务；区分全实例包启停/单实例设置。所有八类结果明确显示，pending 不显示成功。
未安装支持目录/ZIP；启停/升级/retained rollback/卸载；等待列 PID/用途/版本/重启原因与安全重试；recovery 不强制删除。
内置属于 Core，只读物理管理，不虚假卸载。确认摘要绑定 immutable plan/source/trust/version/compat/bytes/impact/rollback/data retention。
720/常规/1100px、明暗、中英文长文、字体/HighDPI、Tab/accessibility、深链、窗口关闭后异步安全。
管理/预检不截图/请求模型/启动 Worker；截图仅自己应用控件。

### 稳定任务编号、顺序与门
- 4B-C0 留档 + 白名单 WIP 快照。
- 4B3-S0 合同/Win32 wrapper 负向测试：无非隔离回退。
- 4B3-S1 原生 LPAC/ACL/Job/句柄/canary 矩阵。
- 4B3-S2 新冻结 helper + 真 host/Worker 自检，无源码依赖。
- 4B3-3 补齐 accepted 恢复 / CAS / rollback / purpose resolver。
- 4B3-4 Qt 生命周期/跨进程准备/真实 receipt/新冻结 Worker 入口。
- 4B3-5 草稿/卸载/部分删除/重启/GC/数据保留。
- 4B3-6 事务专项、相关、全量门。
- 4B4-0~2 UI 合同、实现、布局/生命周期。
- 4B5-0~2 新构建、双变体矩阵、最终性能/验收。
- 4B-CLOSE 报告/索引/当前状态/最终交接。
每切片公开 seam red -> green。两轮相同失败先回溯根因，不堆补丁。

### 验收矩阵
安全路径/ZIP/links/reparse/size/未登记/签名/兼容性失败无代码执行；目录/ZIP/source变更/幂等/启停/重装。
并发锁、多进程多 revision 租约/旧 generation；自检失败/生产加载失败/导入后失败/previous失败/rollback失败。
草稿、部分删除、重启卸载、孤立目录；journal/state/rename/active/receipt/rollback/delete/GC 前后故障注入与重复恢复。
双冻结 Core 带/不带聊天，无识屏，实现 PYZ/原生依赖审计与无源码/PYTHONPATH独立运行；新可信 helper，不借 Core DLL。
验证签名私钥只内存、测试锚仅标记验证构建；驱动生产 bootstrap 与实际 UI，不直写账本/手绑descriptor。
每 Core 七行：空根无入口；UI目录与ZIP安装/真实load/独立设置；生成图+localHTTP真实Worker；停用拒绝自动/手动；卸载等多进程自然退出并文件消失；重装保留profile/绑定/凭据无明文；升级/主动rollback/自检失败/生产加载失败/重启rollback。
截图/网络/安全存储替换须仅明确验证构建边界；factory/事务/租约/Qt/进程/state真实。
最终 focused/related/full pytest、ruff/format/mypy、docs/PR discipline/diffcheck；Qt/进程时序真实CPU高负载3遍、事件同步宽预算。
原生自己目录文件占用/ACL失败；disk/AV 用实际原因分类，不填满磁盘不关闭AV，注入不冒充原生发生。
实测 preflight/probe/apply/load/wait/uninstall/GC median/p95、RSS/线程/IO/系统调用与idle；不长soak。

### 失败、记录、回滚与实际效果
普通失败定位后继续；非核心遗留登记证据/影响/后续；环境人工未测单列。
required安全/core门绝不降为后续。方向错误仅 unsafe isolation/force legitimate exits/hot replace/guess deletion or authority/change data retention。
沿用本设计、父设计和同一 PLAN/HANDOFF/STATUS/WORKLOG/SUMMARY，历史证据保留日期；不新增第二权威套。
报告按完成日期 PR-REPORT-PHASE4B-MANAGEMENT-CLOSEOUT-<YYYY-MM-DD>.md + INDEX，逐文件numstat、性能、原生实机、验证、限制回滚。
白名单基线快照保护原WIP；不提交/推送/reset-hard/forcepush、不宽泛暂存、不读真实secret。
用户届时可以 UI 安全管理本地官方包，等待真实退出，不误删/半成功/复活，数据保留。人工/其他平台/信任锚/Setup发布未测仍单列。

### 原生 probe 的可验证实现细节（2026-10-03，主机日志跨日）

LPAC/空 capabilities/Win32k/Job 不放宽。专用 probe 使用去除 console/window 控制的 Windows 子系统 onedir bootloader；固定上游源码摘要、许可证、显式 CRT entry 与 PE relocation，禁止修改已安装 PyInstaller。GetTokenInformation 对 SDK class 46 在本机返回 error 87，因此实际 LPAC 语义用恢复线程前的 kernel AccessCheck 验证：own package SID 允许、ALL_APPLICATION_PACKAGES 拒绝；不是仅检查已提交 attribute，也不是采信子进程自报。原生权限 canary 矩阵及真实 host/Worker gate 尚未通过，不允许生产回退。


### 原生启动与运行时适配证据补充（2026-10-03）

Core-owned onedir helper 采用 Windows 子系统但保留协议 stdio，不含 USER32/GDI/COMCTL 静态导入；旧 MinGW 使用显式 WinMainCRTStartup 与实际 PE relocations。CPython 3.11.1 自有运行副本的完整 activation resource 会触发低权限 SxS 失败，删除 Common-Controls 依赖不足以解决：仅移除已校验上游模板的 DLL/.pyd activation resource，保存原 XML、前后摘要及许可证，不修改已安装 Python/Core/系统。空 capabilities 中 Winsock 初始化拒绝如实记为初始化拒绝；不增加网络权限。父端使用内核 AccessCheck/mitigation/Job query 验证隔离，Job/管道资源预算不可由子进程自报。阻塞 stdin 由有界 writer 线程拥有；operation-owned write-before-create 文件记录 profile/根/摘要/PID/creation time，恢复不终止任何 PID，仅在原探针释放后清理所记录 profile。上述实测矩阵不是生产 host/Worker 或双冻结 Core 验收。

## 2026-10-03 沙箱前置实证与生产接入补充

- S1 完整 native 权限 canary 通过（92.32s）；真实签名 host/冻结 Worker 单项及分离 LPAC 的组合 adapter 通过。详见同组 HANDOFF/WORKLOG；这不替代事务/UI/双 Core 工程门。
- Core-owned helper 用固定 SHA256 的 upstream libsodium 1.0.22 DLL 调用成熟 Ed25519 验签，不实现自制密码算法，不引入 unsigned/developer fallback。许可证随运行材料；DLL/依赖/资源变换前后摘要属于 build inventory。
- 标准 verifier 对完整 ancestor/subtree 做前后复验。LPAC 不拥有 snapshot 根外 ancestor 的查询权限；只有 trusted parent 创建的 READONLY snapshot，headless verifier 才接受父端提供的完整路径/device/inode receipt，并核对自身根 identity，继续执行相同 manifest/signature/payload/compatibility 全校验。父端在 launch 前及接受结果前按默认 verifier 重新检查完整 ancestry。正常 Core verifier 不放宽。
- host 与 Worker 使用独立 profile/read-only snapshot/scratch；候选不能替换可信 helper。host 回执绑定版本及 raw manifest 摘要；Worker 仅 probe=true、capabilities=[] HELLO 后接受空 SHUTDOWN，不接配置/任务。隔离结果由可信父端实际 token/AccessCheck/mitigation/Job 核验产生。
- 原生资源清理采用“独立资源都尝试释放，再报告首个安全原因码”，失败不能跳过 profile/SID/attribute 释放；ownership journal 保留 bounded recovery 证据。
- 固定输入上限 64KiB、累计 256KiB、每 probe 30s/512MiB；每轮含复制/重复校验会有成本，当前组合单样本 46.723s 不是 median/p95，不据此虚报最终性能完成。

### 当前实际可体验的效果与限制

当前具备原生隔离自检实现与实机证据，尚未将默认应用事务/管理 UI 接成可交付闭环。安装/升级/卸载生产确认、跨进程草稿与贡献撤销、双新冻结 Core 自动化验收及本人实机门仍按事实单列。


### 2026-10-03 累积生产合同补充（主机跨日产物 20261004）

真实冻结诊断发现监控线程与管理/独立设置的只读状态查询争抢排他锁。状态锁改为读共享、写排他；读状态和 verified resolver 的最终检查使用共享锁，CAS 写入只在获得排他锁后开始 journal/state I/O，后台 CAS 的争用等待上限一秒。management/leases 锁仍是非阻塞排他，锁顺序不变。该改变不允许绕过 pending、revision 或损坏证据；它消除只读观察者彼此竞争，而不是把冲突当成功。

生产 startup 在实际绑定前安全创建自有 runtime 目录（位于用户数据根、不得位于代码安装根、不允许链接/reparse），冻结启动传入实际 Core/`_MEIPASS` DLL 根，避免已验证的 Worker 因缺失目录无法启动或借用 Core 依赖。

原生文件占用和仅自有夹具 ACL 删除拒绝已实测；磁盘满/杀软只按 Win32 39/112/225/226 做边界注入，不能写成宿主真实发生过。


### 长路径与显示/性能验证补充合同（2026-10-03）

自有 headless EXE 保留最小 longPathAware manifest，但它不是 LPAC 深路径访问通过的证明。verifier 只有在绝对本地路径、无 traversal 的原安全边界之后，为 Windows 长路径的 stat/scandir/open 使用 extended-path I/O；不改变账本 root、manifest、祖先身份或执行授权，不依赖读取/修改系统长路径策略。必须在真实深层 staging 中验证 host/Worker，不能靠缩短路径转绿。

管理页的换行仅属于显示层：原不可变 OperationPlan、确认摘要/令牌、源指纹及可访问原文保持不变。布局验收同时检查 Qt 实际重排后的高度与文本 paint 宽度，并记录 native High DPI 的实际 viewport；受屏幕限制的请求宽度不冒充真实宽屏通过。

验证构建可以被动观察真实生产方法的阶段耗时/自身进程 RSS、线程与 I/O counters；原方法只执行一次，返回值/异常不改。观察器不进入正常发布入口；报告必须给观察开销校准，不把进程 I/O counters 当成全部内核系统调用，也不把父进程 RSS 当成探针/Worker 的内存峰值。
