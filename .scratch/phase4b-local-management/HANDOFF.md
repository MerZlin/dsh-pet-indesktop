## 当前准确停点（2026-10-04 16:33；发布门禁通过，准备显式提交与正常推送）

- 用户当前授权：将本次新修改提交并推送到 `origin/codex/phase3-worker`。基线 `bd048d57518902532ea82b6b4ba277e79b16871a`；85 文件白名单，保留原 WIP，无产品源码再次修改。仅源代码、测试、公开文档/许可证和本组五份 Markdown，不含生成物/日志/私钥/个人数据。
- 新验证：Ruff 全仓、format-check 518、affected mypy 38 修改源文件及默认 26 源文件通过；专项 223 passed /1 skipped /1 warning（139.93s）；未过滤全量 3841 passed /13 skipped /14 warnings（571.34s）。app/settings 既有类型债、平台/原生 helper 条件跳过均如实保留，不声称全仓 mypy 或所有实机门新通过。
- 压力门：20 个自有 CPU fixture 下，Qt/IPC/native GUI/启动/内核锁/租约/Worker/session-end/reader 21 显式 target 连续三遍均 154 passed；wrapper 272.010/267.890/319.995s；各遍系统 CPU median/p95 均100.0%，采样 245/239/286。runner 已正常结束并通过 Event/finally 回收所有自有压力 fixture；未强退用户/Core 进程。
- 精确执行点：本次报告与同组记录已更新，随后复验文档门、核对85文件精确暂存及敏感字面量，创建源码提交并正常 push；以 `ls-remote`/fetch 独立比较远端 SHA，再封存最终交接。当前尚未提交/推送，不能从门禁通过推断已经远程发布。
- 本轮 TDD 不适用：仅为已有源修改的授权提交/推送与留档，不更改产品行为；既有 red/green、双冻结与人工证据按原日期保存。工程报告逐85文件列说明与基线 numstat，不冒充所有行是本轮所写。
- 存储注意：未重建大型冻结产物。删除本轮五个临时测试目录的 exec_command 被环境策略拒绝，未绕过；只读盘点共 132,573,841 字节（126.432 MiB），另跳过2个reparse条目，当前仍保留且不入库。先前31.184 GiB旧生成物清理仍为已完成历史事实。
- 保护/限制：现用 Core03 双变体、原 APPDATA/profile/凭据和已安装包保留原路径；不启动截图/模型请求、不代确认、不强退、不改真实 ACL。正式信任锚/分发 T0～T3按用户决定暂缓；UX-M3原因未证实及自动/草稿/多实例/chat等未测人工门、其他平台/Setup/正式发行不冒充完成。
- 当前步骤：[x]用户授权/远端基线/白名单 [x]静态与专项 [x]全量与三遍满负载 [ ]最终文档门/精确暂存 [ ]源码提交与正常推送 [ ]远端SHA核验/最终留档。所有终端操作由助手执行，完成后直接汇报，用户无需中途回应。

## 历史检查点（2026-10-04 16:06；用户已授权提交并推送，发布前门禁进行中）

- 当前目标：将既有 Phase 4B 新修改提交并正常推送到 `origin/codex/phase3-worker`，不发布安装器、不合并、不强推，不实施暂缓的正式信任锚/分发 T0～T3。
- 基线：本地与 fetch 后远程均为 `bd048d57518902532ea82b6b4ba277e79b16871a`；当前白名单 85 个源码、测试与 Markdown/许可证文件（1,739,707 字节）。源 WIP 保留，不提交生成物、原始日志、私钥、个人配置、凭据或聊天数据。
- 本次新门禁：Ruff 全仓通过；format-check 518 文件通过；专项 223 passed /1 skipped /1 warning（139.93s，warning 为重复 ZIP 测试夹具）；affected mypy 38 个修改源文件及默认 26 个文件均通过。`pet/app.py` / `pet/modern_settings_dialog.py` 的既有类型债务未纳入 affected mypy，不声称全仓 mypy 清零。
- 准确停点：未过滤全量 pytest 已通过（3841 passed /13 skipped /14 warnings，571.34s）。21 个显式 file/test target 覆盖 Qt/IPC/native GUI/启动/内核锁/租约/Worker/session-end/reader，满 CPU 连续三遍运行中；之后更新报告、核验显式暂存并提交正常推送，再独立比对远程 SHA。当前尚未暂存、提交或推送。
- 证据：同组 ignored `publication-20261004/` 内保存 85 文件白名单/源摘要、仅公开文档 before-image、有限敏感字面量审计与本次测试日志；这些生成证据不入库。文档记录更新不更改产品行为，故本轮 TDD 不适用；既有实现的失败测试和人工证据按日期保留。
- 保护与限制：刚清理的旧生成物不重建；Core03 两变体、原 `manual-session-01` 数据、保留包/helper/Worker 和既有证据保持原路径。不代人工确认、不启动识屏或收费请求、不强退进程。人工门/UX-M3 未证实原因、其他平台、正式信任与 Setup/发布仍单列。
- 当前步骤：[x]用户授权/远程核对/文件白名单与敏感排除 [x]静态与专项门 [ ]全量与三遍高负载 [ ]报告与精确暂存 [ ]本地提交 [ ]远程 SHA 核验。用户无需自行执行终端命令；全部操作完成后一次汇报结果。

## 历史检查点（2026-10-04 15:50；已确认范围清理完成，正式信任/分发继续暂缓）

- 用户确认后仅删除原清单294个目录/132,245个文件；全部完成，无失败/跳过、无剩余目标。移除旧冻结构建、重复依赖与生成夹具31.184 GiB。
- 清理后全根只读盘点：34.987 → 3.809 GiB，119,485文件；跳过原有28个reparse point，无盘点错误。E盘可用空间实测增加31.435 GiB（33,752,743,936字节）；文件逻辑大小与磁盘净释放分开报告，后者含并发系统活动。
- 保护核验：9项原文件摘要、3个保留ZIP摘要一致；所有保护根与21份Git跟踪任务记录存在；原APPDATA公开state仍revision17/active1.0.0/enabledTrue/previous=null/pending=null。不读取私人配置/凭据/截图/聊天内容，不迁移数据域。
- 当前Core03两变体dist/源码、完整manual-session-01及其安装功能包、manual-core-02/packages、最终helper20/Worker06、before-image、原WIP与公开验收证据均保留。清理前后未采样到scratch EXE；本轮没有终止进程，不将此称为持续运行验收。
- 短证据：scratch-cleanup-approved-scope-20261004.json、scratch-cleanup-result-20261004.json、scratch-cleanup-after-20261004.json及逐目标journal；计划原SHA256不变。原清单/预检中的deletion_performed=false是当时事实，实际已删除以result/after为准。
- 准确交接：已授权删除没有待处理目标；清理、保护复核、文档门与同组交接均已完成，无待执行清理步骤；文档链接/PR报告纪律/报告门101 passed（3.96秒），git diff --check通过、暂存为空。当前使用仍沿用manual-run-entry-03.py和原APPDATA，不自动启动或重建、不生成新数据路径。
- 用户下一步无需终端操作；需要桌宠时继续原启动方式。正式T0～T3继续暂缓；没有源码改动、暂存、提交、推送或发布，不新增人工通过结论。

## 历史检查点（2026-10-04 15:31；用户已确认清理范围，执行安全复核与删除）

- 用户明确授权：“确认按这个范围清理”。只执行原 `scratch-cleanup-plan-20261004.json` 的294个目录/132245个文件/31.184 GiB，不扩大范围；正式信任锚和分发继续暂缓。
- 权威授权与只读before证据：`scratch-cleanup-preflight-20261004.json`，绑定原清单SHA256与294个目标；全部目标不与任何Git受跟踪文件、保护根或采样到的live引用相交。当前公开账本仍revision17/active1.0.0/enabledTrue/previous=null/pending=null。
- 清理前最新扫描没有在运行的scratch EXE；本轮没有终止任何进程。当前Core03两变体dist/源码、完整原manual-session-01与已安装功能包、manual-core-02/packages、最终helper20/Worker06、任务记录/原始WIP/before-image/evidence保持保护。
- 准确执行点：再次检查每个绝对路径及所有子项，无链接、无保护/运行引用且与原盘点一致后，原生PowerShell LiteralPath删除；不跟随链接、不改ACL、不强退。进度与逐目标结果落到同组cleanup日志，失败记录后继续处理其他已授权目标，不扩大删除边界。
- 完成后再全根只读盘点、核验保护文件摘要/公开state与实际进程、实测磁盘净释放；更新本组最终交接。当前尚未开始物理删除，不宣称已回收。无提交/推送/发布。

## 历史检查点（2026-10-04 15:20；当时等待具体删除范围确认）

- 用户要求：正式信任锚和分发验收不影响当前桌宠使用就先不做，优先清理 `.scratch`。本轮不生成正式key、不重建、不提交/推送/发布。
- 只读盘点：`.scratch` 合计 37,567,386,254 字节（34.987 GiB），其中 Phase 4B 30.209 GiB、旧 Phase 4A 4.032 GiB；共251,719文件。遍历跳过28个reparse point，无读取错误。体积主要来自多轮冻结构建、依赖和测试夹具未及时清理。
- 已形成唯一明确删除清单 `scratch-cleanup-plan-20261004.json`：294个生成目录、132,245个文件，按逻辑文件大小预计回收31.184 GiB、剩余3.803 GiB。此为估计，不是实际已释放量；目前尚未删除，等用户确认该具体范围。
- 保护：当前manual-core-03两变体dist与源码副本；完整manual-session-01（原profile/配置/凭据与已安装功能包，不迁移路径）；manual-core-02/packages；最终helper20/Worker06；所有任务Markdown、报告、evidence、before-image基线、原始WIP、启动脚本及未知目录。含reparse point的候选不纳入删除。
- 只读身份与状态核验：Core PID30256/原创建时间/EXE一致仍运行；实际引用仅当前Core03与manual-session-01。公开state revision17/active1.0.0/enabledTrue/previous=null/pending=null。未读取私人profile/凭据/聊天/截图/识屏结果或raw日志。
- 本轮验证：文档链接/PR报告纪律/报告门101 passed /3.59s；git diff --check通过、暂存为空。仅文档与元数据清单，无产品代码修改，未重复全量测试或冻结构建。
- 准确下一步：用户确认清单后，再核验绝对路径、链接、运行引用与保护范围；仅以原生PowerShell LiteralPath删除清单，不强退进程、不改系统ACL/安全策略；分类记录失败、实测剩余/释放并确认当前Core与安装状态不变。保留既有用户人工通过结论；首次Core pending加载原因及其他人工门仍按历史记录未证实/未执行。

## 历史检查点（2026-10-04 14:52；ZIP卸载重装数据保留人工门通过，当时正式T0方案待确认）

- 用户明确回执：“入口恢复，原设置保留，未重填密钥，结果正确”。限定no-chat manual-core-03/官方1.0.0，在两版本物理卸载后从ZIP新安装、普通设置真实加载、Core自然重启的实际使用；本项入口、原设置与凭据可用性、真实手动识屏已用户确认通过。未读取私人数据、未声称所有数据字节一致或全部人工门完成。证据manual-user-zip-reinstall-confirmed-01.json。
- 14:52公共只读复核：revision17/active1.0.0/enabledTrue/previous=null/pending=null，ZIP事务tx-3a1a2a78ddf949a295bfd96dcb4f23ee completed；当前Core30256/creation1791095122.0622613身份匹配，1.0.0/revision17的真实native occupied host租约仍在。14:30正常execution resolver通过为前轮证据，未代理执行factory/提交receipt，没有读取模型内容/日志、清理租约或写账本。
- 用户偏好仍为终端操作与核验完成后一次汇报，不在操作过程中逐步等待。无需再安装、确认或退出当前Core；原APPDATA继续保留。草稿/多实例专项体验、自动识屏和chat变体人工门未测，其他平台/Setup/发布不在已通过口径。
- 进入正式T0准备：源feature_build_policy仍OFFICIAL_FEATURE_TRUST_ANCHORS=()/PROBE_BUNDLE_MANIFEST_SHA256=None，fail-closed。现有prepare_core只允许manual/validation entry、注入manual-acceptance-only或validation-only且VALIDATION_BUILD=True；没有正式签名密钥管理/生产分发入口，不能把人工产物或测试公钥改名当正式。候选T0附在同一总设计，待用户确认私钥归属/加密保管/离线备份/轮换边界；当前不生成正式key、不写信任锚、不改构建入口。
- 建议T0：正式Ed25519 key由项目维护者（用户）持有；加密PKCS8保存在仓库外专用目录，密码只在本机交互工具输入，不进入聊天/argv/env/日志；离线独立备份由用户保管。Core只带正式公钥与helper摘要，独立签包不分发私钥；泄露撤销须更新Core并移除旧锚，不能声称旧离线Core即时撤销。这是待确认方案而不是已执行或已备份。
- UX-M3首Core未完成pending加载原因仍未证实，不能将随后普通设置确认/自然重启成功冒充根因修复。正式T0～T3、自动/第二变体等仍未完成；Windows工程历史全量3841 passed/13 skipped与两族满CPU各三遍证据保留。
- 本轮先保存10份公开文档before-image，再更新同一组记录/设计/报告/README/LOG；产品和测试源未修改。前置关联复验：python -m pytest -q tests/test_feature_manual_acceptance.py tests/test_feature_packages.py tests/test_screen_delivery_build.py → 119 passed/1 skipped/22.40s；这是既有验收/验签/构建合同门，不是正式分发通过。无暂存/提交/推送/发布、无子智能体、无正式私钥操作。
- 本轮记录门：python -m pytest -q tests/test_check_docs.py tests/test_pr_report_discipline.py tests/test_report_gates.py →101 passed/3.60s；21文件白名单/19 before-image/2保护文件审计通过，tracked及明确untracked范围diff检查通过、暂存空。三产品源码hash保持03已验收快照；本轮无产品/测试变更，TDD不适用，既有119项前置关联测试已实际复跑。

## 历史检查点（2026-10-04；full04未过滤运行中）

- 锁分源/真实queued撤销→失败→安全重试/卸载文案与UI重建测试完成；67项管理Qt/IPC/真实Worker/锁时序族满负载三遍已通过。已有meta正对照的Event同步修正后，42项session-end/reader族另满负载三遍也通过。产品动画/ffmpeg/GUI线程模型未改。
- 静态Ruff/format518/affected mypy3源通过；21文件范围、19 before-image、2保护文件、空暂存审计通过；新人工Core03两变体产品源码hash一致。完整无过滤full04正在执行，不宣称完成。
- 13:21旧Settings已结束，精确02/03 EXE只剩原Core34672，public state revision12/enabledTrue/pending=null，卸载journal未接受。新03管理专用Settings尚未启动。
- 最后步骤：full04与文档报告门→重新核验无旧Settings/原Core身份→代打开03管理专用Settings同原profile→用户卸载新预检/最终确认，观察实际占用→用户自然退出原Core→安全重试与公共状态/安装文件核验。不能代确认、强退、重置账本或删除个人数据。
- 人工卸载/ZIP重装/自动等余项以及正式T0～T3信任分发未完成。无暂存/提交/推送/发布、无子智能体。

## full03累计检查点（2026-10-04；Qt原生门通过，已有meta测试同步修正中）

- 无过滤full03：1 failed /3840 passed /13 skipped /14 warnings /488.47s，未发生先前Qt原生AV。唯一已有session-end正对照立即断言后台metadata调用，实际生产GUI seam本来就是异步。
- 补充before-image且只改该旧测试的read/count Event与finally cleanup，不改生产ffmpeg/动画/GUI异步逻辑。相关42项14.94s通过，meta时序族满负载三遍正在执行；随后无过滤full04。full03不算全量通过，原67项满负载三遍证据仍有效。
- 全部产品源码及03双冻结快照未变；新03仍未启动。13:21公开核验旧Settings已退出、只剩身份匹配Core34672；账本revision12/enabledTrue/pending=null，卸载未接受。最终门通过后重新确认无旧Settings，直接代打开03管理专用窗口，不再让用户关闭已结束的Settings。
- 无代理确认、强退、state重置、安装删除或私人数据读取，无暂存/提交/推送。人工卸载/ZIP重装/正式信任分发仍待执行。

## 满负载复验检查点（2026-10-04；无过滤全量运行中）

- 补齐新增重建夹具自有manager/Qt子对象的实际销毁后，67项受影响时序族CPU满負载连续三遍全部通过；每遍CPU median/p95 100%，20个自有burner均正常回收。证据manual-lock-high-load-02/performance.json；之前01 AV保留、不改写为通过。
- 静态门：Ruff、518文件format-check、affected mypy3源通过；报告纪律相关99 passed；文档EOF审计错误已更正，18 before-image/2保护文件/20文件范围/空暂存审计通过。未过滤全量manual-lock-full-03正在执行，当前不能登记全量通过。
- manual-core-03双变体已重建审计，与3个产品源码hash匹配。03未启动，旧Core/Settings不强退，原公共state revision12/pending=null/卸载journal未接受。
- 下一步：完整全量结果→最终文档与审计→用户正常关闭旧设置，桌宠先保留→代启03管理专用设置→用户新预检确认→真实租约等待和自然退出→安全重试核验实际删除/未安装。人工卸载、ZIP重装、正式T0～T3未通过；无暂存/提交/推送。

## 当前验证检查点（2026-10-04；锁失败修复仍待累计门）

- 锁分类/安全重试/卸载文案产品实现和manual-core-03已准备，但03尚未启动，人工卸载尚未接受/完成；不把入口撤销当卸载成功。
- 高负载01第1遍真实QProcess事件循环仍原生AV（3221225477/85.38s）；前一轮48通过不能作为最终门。已回溯页面重建夹具的真实拥有者：仅关闭manager不代表其Qt子对象实际销毁；新增只处理夹具自有manager的DeferredDelete及Cpp invalid断言。生产源码/03冻结快照不变。
- 原48项组合随后48 passed /26.73s；67项时序族高负载02三遍正在执行。本快照完整无过滤pytest未完成，不用排除新测试的诊断3836通过替代。
- 准确下一步：高负载结果与无过滤全量→最终静态/文档/白名单审计→用户只正常关闭旧设置，代启动03管理专用设置，重新预检/确认，再按占用提示自然退出旧host并安全重试。禁止自动确认、强退、重置state或读取私人数据。

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

## 人工卸载故障修复切片（2026-10-04；实施前）

- 新证据：用户明确点过“确认本次操作”，后台返回“操作失败，未宣称完成 / management_lock_busy”。公共账本仍 revision12/enabledTrue/pending=null；两份卸载 journal 均未接受。不能反推用户未点击。
- 已读代码证明：当前 catch 将任一 StateError(lock_busy) 都标记 management_lock_busy；历史具体竞争锁未被记录，无法追溯。准备阶段可撤销运行时入口，故入口消失并不证明卸载已接受。
- 范围：仅上述五个产品/测试文件；先保存 WIP before-image，再用自有生成包、真实内核锁和 Qt 事件循环写 RED 回归。区分锁来源，保留确认后的安全重试与失败结果，清除误导性旧摘要；同时修正 UX-M2 卸载无回滚/不声称验签。
- 不改变锁顺序、非阻塞管理/租约政策、CAS、租约与删除合同；不自动接受或取消卸载，不改人工 profile/凭据，不读用户日志，不强退。
- 检查：专项 -> 相关 -> 全量；Ruff/format/mypy、实测成本、文档与差异；新冻结包尚未构建前，不能声称用户正在运行的 manual-core-02 已修复。
- 准确停点：先复现锁误分类及重新打开 UI 丢失失败/安全重试，再实现，最后给用户单步操作。正式信任/分发尚未开始。

# 准确停点：Phase 4B 连续收尾（2026-10-04）

## 当前人工验收推进（2026-10-04）

- 授权：终端操作由执行助手代办，用户负责真实效果；人工门之后补正式信任/分发。不提交/推送/发布，不开子智能体。
- H0实物已就绪：manual-core-02双冻结Core（真实Worker、非synthetic、固定人工entry），187/203当前源和2103/2114实际产物重验；临时key仅内存、公钥仅自有快照，仓库正式信任策略未变。白名单before-image为manual-baseline-20261004-102529。
- H1部分人工确认：用户反馈“设置显示已启用”；目录安装后实际生产加载完成，账本revision4/active1.0.0/enabledTrue/pending=null。仅登记启用状态显示，不扩大为ZIP幂等或全部H1通过。
- H2部分人工确认：用户首次“结果符合”；正常退出、代执行重启后又反馈“得到了正确结果”（此前要求不重填密钥）。真实手动识屏及重启后凭据仍可用的行为门通过；自动识屏及其他项目未测，不推断底层凭据内容。
- H4 no-chat一次自然退出：用户“已退出”；11:08核对原Core PID15196和初始Settings PID28320均结束，内核版本租约free/0 leases/0 cleaned，未强退进程。随后同一配置域重启Core PID21032/creation_time1791083285.5161836，3.373s自身桌宠窗口可见。第二变体退出仍未验收。
- 自有配置：manual-session-01/no-chat/APPDATA/dsh-pet-standalone-webm；不读取原配置/真实凭据/截图/模型回答，不主动请求模型，不改系统ACL/代理/自启/杀软。保留用户当前输入，重启不迁移或清理个人数据。
- H3启停部分人工通过：用户停用后“观察识屏入口已撤销”，公共账本revision5/disabled、Worker/预留租约均无，仅Core host保留pin；重新启用后“入口恢复且结果仍正确”，公共账本revision6/active1.0.0/enabledTrue/pending=null。同一Core21032未重启，无热替换。
- H1 ZIP幂等通过：用户提示“状态已一致，无需重复操作”，公共账本前后完全一致（revision6/active1.0.0/previous=null/enabledTrue/pending=null），没有偷偷改变启停或重复提交。
- H3升级等待部分人工通过：用户报告1.0.0两个host租约（PID21032/31448，revision4/6）的occupied阻塞与“只允许安全重试”；后续只读核验时已free，不把口述owner当仍然存活的证明。用户“已退出”后确认本次EXE无存活实例、全部版本内核租约free，再代重启。
- H3升级人工通过：用户随后明确反馈“版本显示和识屏结果正确”；11:29再次核对Core PID26260/creation_time1791084100.2544065身份匹配，公共账本revision9/active1.0.1/previous1.0.0/enabledTrue/pending=null，旧版free/新版host租约。升级占用等待、自然退出、实际生产加载及升级后显示/手动效果已形成闭环；证据manual-user-upgrade-completed-01.json。只登记用户明确结果，不读取凭据或模型回答。
- H3回滚等待已核验（11:37）：用户报告1.0.0 free、1.0.1 occupied和安全重试提示；公共账本revision10/active1.0.1/previous1.0.0，pending=tx-bafbb0d46e8d4b12b70526051fe7f9b7。对应journal accepted=true/phase=pending_runtime_release/kind=rollback/target1.0.0；真实内核占用及精确自有EXE核对为Settings PID21804（creation_time1791084950.749558）与Core PID26260（creation_time1791084100.2544065），两host lease。不是回滚完成，执行resolver在pending期间禁止执行。证据manual-user-rollback-awaiting-01.json。
- H3回滚恢复系统证据（11:41）：用户“已退出”；本次精确EXE无存活实例、1.0.0/1.0.1内核租约均free/0 leases/0 cleaned。随后代同配置启动Core PID34672/creation_time1791085260.8862312，4.137s自身窗口可见；生产恢复/加载已完成，账本revision12/active1.0.0/previous1.0.1/enabledTrue/pending=null，回滚journal completed。新1.0.0由该Core host revision11占用，1.0.1 free；没有手写状态或伪造receipt。证据manual-user-rollback-exit-01.json、manual-rollback-startup-01.json。
- H3回滚人工通过（11:46）：用户对上一条1.0.0版本显示/保留密钥手动识屏要求明确“确认通过”；再次核对Core34672身份匹配，账本revision12/active1.0.0/previous1.0.1/enabledTrue/pending=null。两权威版本目录仍存在，1.0.0有该Core host、1.0.1 free。证据manual-user-rollback-completed-01.json，不采集密钥/截图/回答。
- 卸载预检已观察（11:48）：用户粘贴确认摘要，与唯一journal tx-835a6591234543efb4ca73f07eebe2fd匹配；摘要body及plan_digest重算一致，plan_revision12、delete_versions=[1.0.0,1.0.1]、awaiting_confirmation、accepted=false，账本pending仍null。只记录预检，不把摘要粘贴当接受；证据manual-user-uninstall-preflight-01.json。
- 卸载入口变化与持久状态不一致待诊断（11:57）：用户反馈“识屏入口已撤销，但官方功能包还显示1.0.0”；公共账本仍revision12/active1.0.0/previous1.0.1/enabledTrue/pending=null，原卸载journal tx-835a6591234543efb4ca73f07eebe2fd仍awaiting_confirmation/accepted=false。1.0.0实际由已核对身份的Core34672 host revision11占用、1.0.1 free，两版本目录存在，cleaned_records均0。只登记用户观察到入口撤销，不当作已接受卸载或等待释放提交成功；证据manual-user-uninstall-awaiting-01.json。真实后台返回原因尚需管理卡片状态文字确认，不能从journal推测草稿/准备失败原因。
- 卸载状态文字/重启原因复核（12:08）：用户补充卡片“已启用 · 1.0.0”，并询问是否因为没有重启桌宠。公共复验仍revision12/enabledTrue/pending=null，原卸载journal awaiting_confirmation/accepted=false；1.0.0有已核对精确自有EXE身份的Core34672 host和独立Settings15936 host/settings租约，1.0.1 free、cleaned_records均0。未退出解释了版本占用，但不能解释/替代卸载接受记录；重启也不能代替最终确认。证据manual-user-uninstall-status-01.json。不能从未落盘反推用户未点击，下一步核对是否执行了摘要下方显式最终确认。
- 当前下一步：向用户说明“驻留进程占用”和“最终卸载接受”是两件事，先核对点击卸载后是否还点击摘要下方“确认本次操作”；不把未重启当唯一原因、不责怪用户、不在无accepted/pending时直接recover或重启。保持现有进程，明确最终确认交互后再据真实生产结果定位/推进；不重复代理确认或删除。accepted后仍正常关闭设置/托盘退出Core，证明全部版本内核释放后再管理专用设置恢复清理、验证未安装及ZIP重装。
- 非核心遗留UX-M1：阻塞信息直接展示内部字典（lease_id/digest等），可读性差；已向用户说明并登记。后续改为PID/用途/版本/自然退出指引，诊断折叠；不削弱内核租约或隐藏阻塞，不因此中断人工功能验收。
- UX-M2（正式分发前必须修复）：通用确认模板把卸载的active_before写成“回滚目标”，并混用签名/兼容性成功文案；真实卸载只预检删除边界、接受后不能取消重新启用。当前人工先明确澄清合同后继续，后续按操作类型重写摘要并回归；不是放宽删除或恢复证据。
- 新验证门：Ruff全工程、4Python format、affected mypy3文件通过；focused+相关文档106 passed/11.19s、链接127文件通过。首轮全量唯一新设计品牌文字失败已修正并保留失败历史；第二轮全量3833 passed/13 skipped/15 warnings（546.99s，wrapper548.35s）通过。
- 正式门：T0密钥归属、离线保管/备份待人工后确认；T1～T3未实施。两Core实测Authenticode=NotSigned，不能当正式可信发布者；此临时公钥版本不得正式分发。
- 证据：[人工验收报告](../../docs/PR-REPORT-PHASE4B-MANUAL-ACCEPTANCE-2026-10-04.md)、manual-user-enabled/screen/exit/restart-01.json、manual-user-upgrade-completed-01.json、manual-user-uninstall-awaiting-01.json、manual-user-uninstall-status-01.json。准确停点：用户卡片“已启用 · 1.0.0”；12:08账本revision12/enabledTrue/pending=null、卸载journal仍accepted=false/awaiting_confirmation。Core34672和Settings15936占用1.0.0；等待用户核对是否点击摘要下方显式最终确认，不误判为只差重启、不假报已接受/卸载通过。保留个人配置/凭据/输入，不代理确认、退出、重启、state改写或删除。

## 当前权威检查点（2026-10-04；Phase 4B Windows工程闭环完成）

- **工程完成**：Windows LPAC权限门、4B-3真实生产事务、4B-4管理UI、4B-5两种新冻结Core七行验收及最终全量/满负载门均通过。此结论限定本机Windows工程自动化，不代表正式发布或本人实机确认。
- 基线分支 `codex/phase3-worker@bd048d57518902532ea82b6b4ba277e79b16871a`，当前成果在未提交工作树；保留原WIP和白名单快照 `baseline-20261004-014342`，无暂存、提交、推送、发布或子智能体。
- 最新Core10两种各7行/27步骤passed；步骤耗时累计432.80s/503.00s。实际EXE的PYZ/native重读无识屏实现命中；185/201个生产及validation输入与当前源码逐字节匹配，只有3个明确生成验证源按合同替换。
- helper20原生LPAC权限矩阵1 passed / 15 deselected（57.07s）；pinned manifest SHA256 `0715b4a6bc3a3392b36ba8dfcebd1dff3978e709e5ab75aa0c221bc68d8e2d53`。空capabilities、Win32k禁用、可信父端token/Job校验、进程/内存/协议/超时限制；没有普通subprocess回退。
- 最终全量06：**3821 passed, 13 skipped, 15 warnings in 514.51s**。满负载3遍均166 passed / 1 warning，CPU实测median/p95均100%；不是仅启动burner就称满负载，详情见报告和原始采样。
- 最终Ruff通过、全部Python源码format 515 files通过、受影响mypy46 source files通过。扩大范围的app/settings87项类型债及7份历史Markdown代码围栏格式债均与HEAD完全一致，无本轮新增；不声称更宽范围全绿。
- 最终交付文档门：链接126文件通过、PR报告纪律/产品文案54 passed；tracked及untracked差异均无空白错误，80文件报告白名单匹配，原WIP快照17文件校验通过，保护文件/暂存/生成物/敏感头检查通过。命令和原始输出归档 `final-delivery-checks-01/02.json` 与 `delivery-*-01/02.log`；最终文字稳定后的独立复核也通过，逐文件numstat与报告80行全部匹配。
- 真实WindowsUI/HighDPI与相关合同69 passed（63.92s），包含16原生组合。175%环境实际DPR2.1875/available878logical、1100请求受屏幕限制至880，未伪造宽屏证据。只抓本应用控件。
- 性能：目录预检N4 median1311.542ms/p951802.189ms；生产pending加载N12 median1003.196ms/p951123.429ms；安装态空闲约3.12～3.44%单核CPU，每10s读取206360B/写0、线程不增长；非空GC N2 median411.311ms/p95433.027ms。短窗口不是长期soak，轮询开销作为后续优化债明确记录。
- 完整逐文件增删行/原因、实机命令/输出、性能样本和限制见 [连续收尾报告](../../docs/PR-REPORT-PHASE4B-MANAGEMENT-CLOSEOUT-2026-10-04.md)。普通失败有root/red/green记录；全量05失败的Qt客户端事件循环测试已修复，不通过扩大预算或改变生产帧上限掩盖。

### 准确停点与下一步

本轮工程实施/自动化完成，不存在“等下一轮补生产闭环”的隐藏停点。最终文档链接/报告纪律、差异白名单/原WIP/保护范围均通过并归档；这些门独立于功能/原生/冻结证据，不用旧文档门替代。后续可先人工验证真实识屏/凭据/托盘，再确认正式官方信任锚与Setup分发。如果要提交/推送，需用户明确新授权；逐文件核对暂存与敏感文件，不使用git add -A、reset --hard或强推。

运行证据：`.scratch/phase4b-local-management/management-delivery-10/validation-artifacts.json`、`frozen-matrix-10-no-chat/matrix.json`、`frozen-matrix-10-chat/matrix.json`、`high-load-final-01/performance.json`、`final-full-suite-06-result.json`、`phase-performance-10/performance.json`、`idle-performance-10-{no-chat,chat}/performance.json`、`gc-performance-02/performance.json`。这些是忽略的本地原始证据，不作为生成物提交。

- **人工/发布门单列未验收**：本人真实识屏、真实凭据体验、实际托盘自然退出、其他平台、正式官方信任锚、Setup/签名分发/远程下载/第三方。测试图像/HTTP/生成安全存储是明确标记验证边界，不冒充本人真实数据体验。
- 默认完整内置Core仍标注“属于Core”，禁止假的物理卸载。正常生产信任策略为空且fail-closed；测试公钥只在明确validation构建，测试私钥仅内存，不能将验证产物当正式发行包。
- 使用效果：无识屏Core的验证构建可在“常规 / 扩展管理”安全管理本地官方目录/ZIP，占用等待自然退出，加载失败实际回滚，卸载保留个人数据。正式可分发安装产品仍需上述信任/发布门。

### 以下为保留的历史检查点

历史未实现/失败/待执行描述均属于相应日期和源快照，不覆盖上述最终状态；过程证据原文保留。

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


## 连续实施检查点（2026-10-03；主机跨日产物标识 20261004，Core05 构建中）

- Core03 的独立设置只读诊断证实 `lock_busy`；延后监控启动回归先红后绿。真实 Worker 启动发现自有 runtime 目录未创建，已由生产 startup 在安全边界内准备，并传入冻结 Core 的 DLL 根；相关 18 项回归通过（29.38s）。
- 全量02：3765 passed / 13 skipped / 14 warnings，533.07s；这是修复累积前的中间快照，不能替代最终门。
- Core04 双变体重新构建；矩阵04 的目录预检仍触发监控读锁竞争，保留失败证据。根因修为共享只读状态锁、排他 CAS 有界等候；管理锁/租约仍排他且非阻塞。新合同先红后绿，相关状态/监控/事务/管理 165 passed / 1 warning，112.44s。
- 本机 Windows 自有夹具的真实文件占用和 ACL 删除失败：2 passed，3.99s。Win32 磁盘/杀软原因码只做边界注入，不填满磁盘、不关闭杀软；7 项原因码回归通过（1.67s）。
- Core05 正按当前生产修复重新构建；下一步运行双冻结七行矩阵。没有方向错误，不弱化任何沙箱/保留数据合同。
- 4B-3/4B-4/4B-5 仍未完成：最终双冻结矩阵、高负载三遍、High DPI 实机、性能和最终全量/报告尚未通过。原 WIP 保留；没有提交、推送或子智能体。

下一准确动作：读取 `management-delivery-05-command.log`，Core05 no-chat 就绪后运行 `python -m scripts.validate_feature_management_delivery --artifacts .scratch/phase4b-local-management/management-delivery-05/validation-artifacts.json --output .scratch/phase4b-local-management/frozen-matrix-05 --variant no-chat`，再验 chat；普通失败继续根因回归。


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

---

设计：[事务](../../docs/plugin-phase-04-updates/PHASE4B-3-LOCAL-TRANSACTIONS-DESIGN.md) / [全阶段](../../docs/plugin-phase-04-updates/PHASE4B-LOCAL-MANAGEMENT-DESIGN.md)；记录：[PLAN](PLAN.md) / [HANDOFF](HANDOFF.md) / [STATUS](STATUS.md) / [WORKLOG](WORKLOG.md) / [SUMMARY](SUMMARY.md)。

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

---

## 既有带日期记录（保留，不作为当前状态）


# Phase 4B 本地安装与管理：准确停点

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

### 精确停点与恢复顺序

1. 保留当前 WIP，不接入生产启动，不自动提交/推送，不把未验证代码与 UI 相连。
2. 向用户说明 OS 沙箱不是普通 subprocess；建议补 Windows AppContainer + 专用冻结 probe 的设计（候选，不是已定方案）。未经确认不改系统 ACL、不装组件、不扩大构建范围。
3. 如确认继续：先在生成数据上证明用户目录/网络/桌面都不可访问、Qt 依赖可读、native 子进程树受控；再实现真实 hello/shutdown executor。
4. 再接 FeatureHost/Core/独立设置 queued 生命周期和真实加载回执；现有 confirm_startup(digest) 只是受信任内部端口，不是不可伪造的实际加载证明。
5. 补最早 accepted-journal/pending-state 间断电恢复、常驻失败候选重启回滚、全部故障点及真实 Windows 失败分类。没有证据就 disabled/recovery_required，不猜目录恢复。

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

## 历史交接记录（2026-09-30 起，非本次当前状态）

工作区 `E:/AI/DSH/dsh-pet-indesktop`，分支 `codex/phase3-worker`，起点/HEAD `ce164847dc1f3553a2e62d0cb5dc13c2fb9bd339`。本轮增量未提交、未推送；未开子智能体。**4B-1 已实现并完成自动化回归；4B-1.5 代码、自动化、文档与保护门已通过，本地封存就绪；4B-2 尚未开始。**

权威入口：[设计 §2.1](../../docs/plugin-phase-04-updates/PHASE4B-LOCAL-MANAGEMENT-DESIGN.md)、[PLAN](PLAN.md)、[STATUS](STATUS.md)、[完整实施证据](../../docs/PR-REPORT-FEATURE-INSTALL-STATE-2026-09-30.md)。

## 本轮实际验证与准确停点

- 新增两个状态模块与 86 项测试；可收集骨架初始 24 failed，随后逐层 red/green；恢复与 ctypes 类型复用缺陷均有先失败的断言。
- `python -m pytest -q tests/test_feature_install_state.py tests/test_feature_packages.py tests/test_feature_config_ports.py tests/test_feature_settings_ports.py tests/test_screen_configuration.py`：216 passed、1 skipped / 14.73s。
- `python -m pytest -q tests/test_feature_install_state.py -k "two_real_processes or kernel_lock_busy or real_process_interruption"`：三次各 10 passed、76 deselected / 3.67、3.76、3.82s。
- `QT_QPA_PLATFORM=offscreen` 全量：3547 passed、12 skipped、13 warnings / 343.74s。
- Ruff lint 通过；format 463 files already formatted；mypy 两个新增模块通过。文档链接 114 份通过；PR 报告纪律 + desktop features 140 passed / 12.62s；`git diff --check` 通过。updater、update_settings、HTML 的 SHA-256 与起点一致；暂存区为空，无提交/推送。
- 当前较历史全量新增 88 个通过项：86 个状态测试、2 个新报告纪律参数项；skip 仍为 12。warning 少一次原有 `test_try_move_success_still_builds_plan_and_moves` 的 QImage.mirrored 弃用提示，其测试和产品代码未修改，也未过滤 warning。其余 13 项均为既有 Qt 弃用提示，不将提示数量变化认作产品修复。
- 本机 E 盘 50 次提交的中位 67.219ms、50 条收据下 100 次读取中位 38.119ms；锁持有分别 65.924/37.399ms。完整命令、分位数、磁盘与 traced memory 见报告。

## 本次文档与保护复核（2026-10-02）

- `python scripts/check_docs.py`：`Markdown link check passed: 119 files scanned`。
- `python -m pytest -q tests/test_pr_report_discipline.py`：`47 passed（pytest exit code 0；运行耗时随环境变化，不作为固定基线）`。
- `D:\\DELL\\Git\\cmd\\git.exe diff --check`：退出码 0；仅有换行规范提示，无 whitespace error。
- `D:\\DELL\\Git\\cmd\\git.exe diff -- pet/updater.py pet/update_settings.py`：无差异；`plugin-roadmap-demo.html` 未出现在状态中。
- 以上复核针对本轮文档/记录编辑；全量运行时测试仍以文档编辑前的 `3562 passed, 12 skipped, 13 warnings in 356.90s` 为最后结果，本轮未因文档变更重跑全量。

## 继续前必须保留的边界

- `state.json` 唯一权威；op 收据和 frontier 仅作证据。未知后续操作/未来 schema/缺少证明时安全拒绝，不猜测恢复。
- 当前返回的已验证描述不是租约；4B-1.5 资源硬门 → 4B-2 在同一协调边界下接续真实版本占用与授权。
- 状态读取遍历历史；不在 GUI 高频循环同步调用。4,096 记录上限，无自动证据压缩。POSIX 分支和普适断电持久性未实机验证。
- 不接生产启动，不动版本目录、配置、凭据、记忆和聊天历史；无用户操作变化。
- 当前版本识屏、原生安全存储、自动识屏及托盘退出人工门仍未验收，不是故障。
- 本轮不提交/推送、不重建产物、不扩大到安装器或 UI。原始日志/测量工具留本地忽略目录；权威结果在已登记报告。

## 下一步（不是本轮自动开工）

从设计 §3 与 PLAN 的 4B-1.5 资源硬门 → 4B-2 开始：先设计并测试 host/设置/Worker 的跨进程租约、协调锁及启动交接；处理同步读取成本，保持错误时保守占用。不得跳到管理界面，也不能用 PID/超时猜测可以删除版本。

## 实际可体验效果

本轮没有新增菜单或安装卸载操作。只建立可靠的安装状态基础，尚未交付完整可拔除流程。历史检查点如下，保留当时事实，不用其中“未实施”覆盖当前进度。

---

# 历史检查点交接（不是当前实施状态）

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

## 路线调整记录（2026-10-02）

准确下一步已从“进入 4B-1.5 资源硬门 → 4B-2”调整为：先执行 `.scratch/phase4b-1-5-resource-hard-gate/PLAN.md`，完成资源 DLC 硬门；4B-1.5 资源硬门 → 4B-2 暂缓，不得用状态账本结果代替资源安装→解析→播放证据。

## 4B-1.5 资源硬门状态（2026-10-02）

当前准确停点为“4B-1.5 代码、自动化、文档与保护门通过；本地封存就绪，尚未提交或推送”。本轮资源专项证据为：相关资源链 43 passed；硬门测试连续三次 28 passed；最终文档编辑前全量为 3562 passed、12 skipped、13 warnings。Ruff、格式检查和 `mypy pet/content` 通过；文档链接、PR 纪律、`git diff --check` 和保护文件复核随后也已通过。上述全量结果对应文档编辑前代码状态，本轮文档编辑后未重跑全量。

下一条准确动作：完成 `check_docs.py`、PR 报告纪律、`diff --check` 和保护文件复核；通过后等待用户授权独立封存提交。不得进入 4B-2 租约、4B-3 事务或管理 UI，也不得把资源接口称为公开稳定。人工资源包安装、重启播放、可见桌面及托盘验收仍未完成。

## 连续性补档（2026-10-02）

- **准确停点**：4B-1 已完成；4B-1.5 的代码、自动化、文档与保护门已通过并具备本地封存基础；4B-2 尚未开始。
- **新增记录**：本目录已补充 `WORKLOG.md` 和 `SUMMARY.md`，与既有 `PLAN.md`、`HANDOFF.md`、`STATUS.md` 配套使用。
- **当前任务关系**：文档连续性任务正在补齐 `AGENTS.md`、项目入口、开发者 README、索引和日志；这些文档变更不改变 4B 生产实现。
- **最后一次阶段证据**：资源专项 43 passed；硬门测试连续三次各 28 passed；文档编辑前全量 3562 passed、12 skipped、13 warnings。该结果不是本轮文档修改后的全量结果。
- **未完成人工门**：资源包人工安装/重启播放、真实安全存储、可见桌面、托盘退出和三平台发布门仍未完成。
- **保护边界**：不得修改 `pet/updater.py`、`pet/update_settings.py`、`plugin-roadmap-demo.html`，不得覆盖用户已有工作树改动，不得跳过 4B-1.5 直接实施 4B-2。
- **下一步**：完成文档连续性任务的 `check_docs.py`、PR 报告纪律、`diff --check` 和保护文件复核；通过后等待用户对本地备份或下一阶段实施的单独授权。
