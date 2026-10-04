# Phase 4B 真实用户人工验收（2026-10-04；持续记录）

> 分支：`codex/phase3-worker`；HEAD：`bd048d57518902532ea82b6b4ba277e79b16871a`。
> 当前报告为持续更新的人工门记录，不是已通过声明；本轮未提交、推送或发布。
> 历史工程验收另见[Windows闭环报告](PR-REPORT-PHASE4B-MANAGEMENT-CLOSEOUT-2026-10-04.md)，其Core10模拟边界不能代替本轮人工体验。

## 一、核心特性与验收合同

用户授权终端指令由Codex执行，用户负责实际界面、真实识屏、凭据重启保留和自然退出效果。
沿用[总设计](plugin-phase-04-updates/PHASE4B-LOCAL-MANAGEMENT-DESIGN.md)H0～H4/T0～T3、[准确停点](../.scratch/phase4b-local-management/HANDOFF.md)和同一组任务记录。

- 新冻结人工entry使用真实生产bootstrap、UI、事务、租约、Worker、网络与Windows安全存储，不安装模拟图像/HTTP/vault边界。
- 所有配置使用E盘新APPDATA；Vault服务域绑定该新配置文件绝对路径，不读取旧用户凭据。凭据只由用户输入本地GUI，不输入聊天或终端。
- 临时签名key只在构建进程内存中；此公钥只写入自有人工构建快照，不修改仓库正式信任策略。正式密钥归属/保管/备份须先确认。
- 开机自启系统集成不在本次验收范围；人工entry禁止查询/写入/清理真实Run项，避免正常启动清理旧自启项产生系统副作用。
- 不自动截图、不触发模型、不关闭合法Core/设置进程，不用人工口述替代内核租约证据；最终真实效果仅以用户回执记录。

## 二、修改文件说明

**范围口径**：增删行数对比本轮白名单WIP快照`manual-baseline-20261004-102529`，不是将此前80文件成果重新算作本轮修改。快照保留原WIP。 五个锁故障修复文件另与 `manual-lock-baseline-20261004-122513` 比较；这些文件在本轮之前已有 WIP，不能将其整文件算作本轮新增。已有 meta 正对照测试单独保存补充 before-image `manual-lock-ffmpeg-fixture-baseline-20261004-132942`，不改变原两个基线。

<!-- MANUAL-FILE-TABLE-START -->
| 文件 | 类型 | + / - | 修改内容与原因 |
|---|---|---:|---|
| `scripts/build_screen_delivery.py` | 修改 | +16 / -5 | 固定人工入口白名单、人工快照标记与无模拟边界；保留正式策略和旧自动化路径。 |
| `scripts/build_feature_management_manual.py` | 新增 | +92 / -0 | 新增真实Worker双Core构建器；三版本目录/ZIP仅由内存临时key签名，拒绝synthetic Worker。 |
| `packaging/phase4b_manual_entry.py` | 新增 | +55 / -0 | 新增冻结人工生产入口；先Worker租约/独立设置分流，禁止源码回退及真实自启系统修改。 |
| `tests/test_feature_manual_acceptance.py` | 新增 | +187 / -0 | 新增公开合同负向/正向测试，覆盖入口、helper、非模拟Worker、自启保护与参数隔离。 |
| `docs/plugin-phase-04-updates/PHASE4B-LOCAL-MANAGEMENT-DESIGN.md` | 修改 | +48 / -0 | 追加H0～H4/T0～T3人工与正式信任合同；历史设计不改写。 |
| `docs/INDEX.md` | 修改 | +2 / -0 | 登记持续人工验收报告，保留历史工程报告入口。 |
| `README.md` | 修改 | +2 / -0 | 说明真实人工验收进度与正式信任/分发限制，不冒充所有门通过。 |
| `LOG.md` | 修改 | +58 / -0 | 追加本次人工准备、实际回执及保留数据/未发布边界。 |
| `LOG-INDEX.md` | 修改 | +1 / -0 | 登记人工体验及正式信任推进记录。 |
| `.scratch/phase4b-local-management/PLAN.md` | 修改 | +86 / -0 | 刷新同一任务后续人工/信任门及准确下一步；保留工程历史。 |
| `.scratch/phase4b-local-management/HANDOFF.md` | 修改 | +86 / -0 | 记录当前自身进程/配置域、人工回执、验证状态与授权边界。 |
| `.scratch/phase4b-local-management/STATUS.md` | 修改 | +86 / -0 | 区分准备、自动化、部分人工通过及未实施正式门。 |
| `.scratch/phase4b-local-management/WORKLOG.md` | 修改 | +246 / -0 | 追加RED→GREEN、普通失败复盘、真实构建/启动和人工回执证据。 |
| `.scratch/phase4b-local-management/SUMMARY.md` | 修改 | +86 / -0 | 更新跨对话有效事实、保护范围、当前停点与用户任务分工。 |
| `docs/PR-REPORT-PHASE4B-MANUAL-ACCEPTANCE-2026-10-04.md` | 新增 | +288 / -0 | 新增人工验收三份证据报告：逐文件增删、实测性能、实机/人工反馈及未验收门。 |
| `pet/feature_package_transactions.py` | 修改 | +66 / -32 | 区分管理/租约/状态锁，保留可安全重试计划；只标记获取异常，不掩盖临界区错误，不修改锁与删除政策。 |
| `pet/feature_management.py` | 修改 | +7 / -0 | 将操作结果与只读 Inspection 分离，保留进程内最新确认计划，不持久化确认令牌。 |
| `pet/feature_management_ui.py` | 修改 | +26 / -8 | 失败清除旧摘要；同进程重新打开恢复失败/重试；卸载列出全部版本，不伪称验签或允许回滚重新启用。 |
| `tests/test_feature_package_transactions.py` | 修改 | +40 / -0 | 真实三类内核锁在生命周期准备后竞争，证明未提交、未删除，并沿原确认计划安全重试。 |
| `tests/test_feature_management_ui.py` | 修改 | +196 / -0 | 真实异步 Qt/生命周期撤销与租约等待测试，覆盖错误来源、UI 重新打开、卸载摘要及保留文件直到释放。 |
| `tests/test_session_end_ffmpeg_guard.py` | 修改 | +15 / -5 | 修复已有正对照测试立即断言异步探测的时序竞态；用Event证明read/count调用完成，always cleanup自有clip，不改生产ffmpeg/会话闸门。 |
<!-- MANUAL-FILE-TABLE-END -->

统计命令：`git diff --no-index --numstat --ignore-cr-at-eol OLD NEW`。初始人工切片历史审计为13个before-image/2个保护文件/15修改或新增文件，证据`manual-delivery-audit-01.json`；追加本次锁修复及已有meta夹具后，当前为19个before-image摘要匹配、2个保护文件未变、表内21个修改或新增文件数值收敛并独立重验，证据`manual-delivery-audit-lock-01.json`。tracked及白名单untracked的`diff --check`通过，暂存为空。只核验本轮白名单，不冒称整个旧WIP为本轮成果。

刻意未动：仓库`pet/feature_build_policy.py`正式策略、原Core安装目录、真实profile、系统ACL、代理/杀软、冻结Worker生产执行链及此前工程报告的证据。

## 三、实现要点

固定人工entry与原自动化entry并列白名单，任意entry继续拒绝；helper须通过pin与每文件完整性校验后才生成输出。
人工builder要求独立Worker为`synthetic=False`，逐源输入/实际产物摘要匹配；三正常版本和目录/ZIP只由一次内存临时key签名。
人工入口禁止源码回退或用自动化配置启动。独立设置先分流，不导入桌宠App；Worker先走生产lease交接，不进入GUI分支。
本轮不是管理UI重写，不改变安装状态权威、revision协议或用户数据合同。

## 四、性能分析

环境：本机Windows 64位build26100，Python3.11.1；命令`python -m scripts.build_feature_management_manual ...`。
实测命令（工作目录为仓库）：

```powershell
python -m scripts.build_feature_management_manual .scratch/phase4b-local-management/manual-core-02 --worker-build .scratch/phase4b-local-management/standalone-probe-worker-06 --probe-bundle .scratch/phase4b-local-management/probe-host-build-20/dist/dsh-feature-probe --probe-manifest-sha256 0715b4a6bc3a3392b36ba8dfcebd1dff3978e709e5ab75aa0c221bc68d8e2d53
python .scratch/phase4b-local-management/manual-frozen-audit.py
python .scratch/phase4b-local-management/manual-preflight-audit.py
python .scratch/phase4b-local-management/manual-run-entry.py --variant no-chat --mode settings
python .scratch/phase4b-local-management/manual-run-entry.py --variant no-chat --mode core
```

| 实测路径 | n | 实际数字 |
|---|---:|---|
| 双Core整体人工构建 | 1 | 450.429s；no-chat Core168.705s，chat187.507s，其余含3目录/3ZIP、资源复制、helper验证与产物审计 |
| 冻结大小 | 各1 | no-chat459227311B/2103文件；chat461649754B/2114文件 |
| 目录预检 | 5 | median5.673s/p955.971s |
| ZIP预检 | 5 | median3.937s/p954.081s |
| 初始独立设置可见 | 1 | 2.826s；RSS139026432B、10线程、CPU1.500s，read25106061B/write73B |
| 初始真实Core可见 | 1 | 4.406s；RSS97095680B、18线程、CPU1.875s，read614200013B/write12154B |
| 同配置重启Core可见 | 1 | 3.373s；RSS112615424B、14线程、CPU1.546875s，read592684597B/write2617B |
| 升级后Core可见 | 1 | 3.923s；RSS114835456B、14线程、CPU2.234375s，read740077784B/write38697B |
| 回滚后Core可见 | 1 | 4.137s；RSS113954816B、14线程、CPU2.296875s，read740098026B/write38607B |

n=5的p95采用nearest-rank，等于最大观测；n=1启动不伪造median/p95。构建/回归并行影响磁盘和CPU，数字不是独立发布性能基线。
初始设置自身子进程只有conhost、无识屏Worker；真实识屏请求成本在用户触发前不可宣称测过。
稳态watcher/协议未改，本轮未重复长idle测量，不能写“稳态无成本”；历史工程性能仅参考旧报告。
初始RSS为不同生命周期时刻的一次样本，不能据此称Core比设置省内存或证明无增长。
本轮新增构建路径只在验收构建时触发，生成3目录+3ZIP+2冻结Core；没有新运行时轮询、网络替代服务或系统常驻线程。
真实Worker/网络/安全存储未改变；初始管理页不得启动识屏Worker或请求模型。


### 卸载人工失败及修复（2026-10-04）

- 用户明确点击“确认本次操作”，返回“操作失败，未宣称完成 / management_lock_busy”；用户同时观察到识屏入口撤销，卡片后来仍“已启用 · 1.0.0”。公共 state revision12/enabledTrue/pending=null，两份 journal awaiting_confirmation/accepted=false；证据 `manual-user-uninstall-lock-busy-01.json`。**人工卸载未通过**，不能归咎于用户没确认或没重启。
- 历史错误来源未记录：当时管理锁采样空闲只代表该瞬间，不能证明历史持有者。代码已证明原 catch 将管理、租约、状态三种竞争统一标记 management_lock_busy。真实生成夹具与 queued 生命周期复现“入口先撤销，随后锁竞争阻止接受”，不是从历史日志推测。
- 失败测试：修正一个测试夹具的主线程 queued 调用后，RED **7 failed / 114 deselected，6.22s**；首次 GREEN 2 failed / 5 passed 原因是 KernelLock 在函数调用时立即获取，不能在 enter 之后才包错误。改为惰性获取，GREEN **7 passed / 114 deselected，7.64s**。真实 queued 卸载＋内核 host pin 的闭环 **1 passed / 40 deselected，2.74s**；准备后失败不写 state、不删版本，安全重试接受为 pending，再等自有 pin 释放才完成。
- 相关回归首次 **5 failed / 240 passed，172.54s**：回滚 multiline 调用遗漏 lazy factory，未改变数据合同；根据失败回溯修正这一调用并加 Callable 注解，原五项 **5 passed / 86 deselected，12.97s**。这是本轮普通实现失败，保留记录，不算环境问题。
- 修复只分类真实锁来源，不新增重试忙轮询，不移除 management→leases→state 顺序、不扩大删除边界。操作结果保留在 manager 内存且与 Inspection 分开；重开同进程页面恢复错误与确认计划；进程关闭后不持久化 token，不会自动接受未确认/未接受卸载。
- UX-M2 已修正源码：卸载列出全部安装版本，只声称删除合同，不声称签名重新验证，不列错误回滚目标，明确已接受卸载不能取消并重新启用。当前旧 manual-core-02 不因此自动修复；新的双 manual-core-03 正重新构建，沿用旧人工公钥和已 pin 的 helper，不读取签名私钥。正式策略未改，人工 profile/凭据未读取或写入。
- 验证进行中：全量、双冻结重建、高负载/原生布局；最终真实卸载仍须用户在新管理页显式确认、自然退出占用进程与观察完成。

### 锁分类修复的性能实测

命令 `python .scratch/phase4b-local-management/manual-lock-measure.py`，Windows build26100 / Python3.11.1 64-bit；只用自有生成签名包（OS probe 为测试 stub，不是原生沙箱证据），前后交替采样，相关回归可能并行，证据 `manual-lock-perf-01.json`。

| 路径 | 样本 | 修复前 median/p95 ms | 修复后 median/p95 ms |
|---|---:|---:|---:|
| 已确认 apply 遇管理锁竞争 | 每版本100 | 2.6272 / 3.3009 | 2.6453 / 3.6801 |
| 自有真实内核锁获取释放 | 每版本500 | 0.9743 / 1.5925 | 0.9990 / 1.4977 |

新增 Python 分类/上下文包装，真实锁 acquisition、journal/state 读写路径及触发频率不变；没有新增网络、磁盘写入、轮询或线程。该混合采样过程线程4→4、RSS41,205,760→41,746,432 B（+540,672 B），read_count745→1145、write_count29→29；这是测量进程总量，不是按产品归因的内存泄漏结论。GUI新增一个最新 OperationResult 的强引用（替代而不累积），它会延长同一确认 plan 的内存寿命至下一操作/进程退出，未持久化 token；尚无长时间 soak 证据，不声称内存完全不增长。

## 五、实机运行记录

- Windows原有自动化Core10使用专用driver和synthetic Worker；源码检查证明确实不适合作为真实人工入口。
- 本轮真实Worker06的`synthetic_boundary=False`及全部当前输入/产物digest已校验；helper20 pin重新验证，未执行真实截图/模型请求。
- manual-core-02重新冻结成功；187/203当前源逐字节匹配，实际EXE/PYZ/native与2103/2114文件重读，无自动driver或模拟边界，helper pin重新验证。
- 真实Windows“桌宠设置”已打开在扩展管理，PID28320、own rect[351,88,1165,686]；只查询自有PID窗口标题/矩形，不截图其他窗口。
- 用户GUI已接受v1安装（代理只读state公共元数据）：revision3、active1.0.0、pending。随后代启动真实Core PID15196，自身桌宠窗口可见rect[1075,535,1536,816]；实际生产factory/绑定/receipt清pending至revision4。这不是用户体验回执。
- 配置根为`.scratch/phase4b-local-management/manual-session-01/no-chat/APPDATA/dsh-pet-standalone-webm`；未读配置内容、凭据/记忆/聊天数据。系统观察文件只含revision/版本/pending、自身进程资源与自有窗口标题。
- 负向真实文件边界：伪签名和不同公钥均被verifier拒绝，原因`signature has no explicitly trusted Core signer`；10次预检不增加factory generation，独立审计账本无active。
- 两Core `Get-AuthenticodeSignature`实际输出`NotSigned`。功能包临时签名通过不代表Windows可信发布者或正式信任锚通过。
- **用户已明确确认“设置显示已启用”**，仅登记H1启用状态显示的部分人工通过；入口、ZIP幂等、GUI整体易用性、自动识屏、凭据重启与托盘自然退出仍待分别确认。11:03自身Core PID15196创建时间匹配且仍运行，账本revision4/active1.0.0/enabledTrue/pending=null；最初管理窗口PID28320已不在运行，未查原因，不宣称退出体验通过。证据`manual-user-enabled-01.json`不含配置内容或秘密。
- 用户随后反馈“结果符合”，依据此前一次真实手动识屏的上下文登记为该次结果符合预期；证据`manual-user-screen-01.json`仅含回执与自身进程身份，不保存截图/模型回答。自动识屏与重启保留不能据此扩大认定。
- 用户“已退出”，原Core PID15196/Settings PID28320均结束，内核租约free/0 leases/0 cleaned，未强退任何进程；同配置代重启Core PID21032并保留初始/重启身份记录。要求不重填密钥再手动识屏后，用户反馈“得到了正确结果”：登记这一次自然退出和重启后凭据可用的行为门通过，不推断底层秘密内容。证据`manual-user-exit-01.json`、`manual-user-restart-01.json`。
- 停用后用户“观察识屏入口已撤销”：账本revision5/disabled，内核无Worker/预留租约，只保留Core21032的host pin。再启用后用户“入口恢复且结果仍正确”：账本revision6/active1.0.0/enabledTrue/pending=null，同一Core身份匹配，没有重启/热替换。证据`manual-user-disabled-01.json`、`manual-user-enabled-02.json`。
- 用户UI导入v1.zip反馈“状态已一致，无需重复操作”；公共账本前后完全一致：revision6/active1.0.0/previous=null/enabledTrue/pending=null，同一Core身份匹配。证据`manual-user-zip-idempotent-01.json`；不能将幂等ZIP导入扩大为全新ZIP安装通过。
- 升级UI等待：用户报告1.0.0两host（PID21032/31448、revision4/6）occupied及安全重试警告。后续实际检查时内核已free，账本revision7/pending保留，不把用户粘贴内容当当前活性或31448已核实身份。用户“已退出”后再次确认本次EXE无存活实例、租约全部free，代重启Core26260，真实生产加载至revision9/active1.0.1/previous1.0.0/enabledTrue/pending=null；旧版free、新版host租约。证据`manual-user-upgrade-awaiting-01.json`、`manual-user-upgrade-exit-01.json`、`manual-upgrade-startup-01.json`。当时升级后的UI/识屏仍待人工回执。
- 11:29用户明确反馈“版本显示和识屏结果正确”；再次只读核对自身Core26260身份匹配，revision9/active1.0.1/previous1.0.0/enabledTrue/pending=null，旧版free、新版host。升级显示和真实手动识屏的人工子项通过，证据`manual-user-upgrade-completed-01.json`。未采集截图、模型回答、凭据或用户日志，未代理发起请求。

- 11:37用户回滚UI显示1.0.0 free、1.0.1 occupied并只允许安全重试。实测公共账本revision10/active1.0.1/previous1.0.0，pending=tx-bafbb0d46e8d4b12b70526051fe7f9b7；对应journal accepted=true/phase=pending_runtime_release/kind=rollback/target1.0.0。内核仍有两个1.0.1 host租约：PID21804已核对为本次精确EXE的独立Settings，PID26260为自身Core且与launch creation_time匹配；1.0.0 free，两版本cleaned_records均0。证据`manual-user-rollback-awaiting-01.json`。回滚等待机制得到真实确认，回滚尚未完成；下一步用户正常关闭Settings及托盘退出Core后再核验释放/代重启，不按粘贴PID强退。

- 11:41用户“已退出”；重启前本次精确EXE无存活实例，两版本内核租约均free/0 leases/0 cleaned，账本revision10仍保留已接受回滚pending，未猜测完成。才代同配置启动Core34672/creation_time1791085260.8862312；4.137s本应用窗口可见，真实生产恢复及加载完成后revision12/active1.0.0/previous1.0.1/enabledTrue/pending=null，原回滚journal completed。1.0.0由新Core的host revision11占用、1.0.1 free。证据`manual-user-rollback-exit-01.json`、`manual-rollback-startup-01.json`及保留的`core-pre-rollback-launch.json`/`core-rollback-01-launch.json`；当时回滚后的显示/手动效果仍待用户回执，不代理真实截图或模型请求。

- 11:46用户针对1.0.0显示与保留密钥后的手动识屏两项要求回复“确认通过”；再次核验Core34672身份匹配、revision12/active1.0.0/previous1.0.1/enabledTrue/pending=null，已登记回滚显示/真实效果通过。两权威安装版本目录仍存在，作为后续卸载前公开路径证据；不读取个人配置/凭据或请求内容。证据`manual-user-rollback-completed-01.json`。
- 11:48用户粘贴卸载确认页摘要。与唯一公共journal tx-835a6591234543efb4ca73f07eebe2fd匹配，确认body及plan_digest重算一致；delete_versions为1.0.0和1.0.1，plan_revision12、phase=awaiting_confirmation、accepted=false，账本pending=null。尚未接受或执行卸载；证据`manual-user-uninstall-preflight-01.json`。通用模板“回滚目标1.0.0”及签名/兼容性文案含义不准确，登记UX-M2并在用户接受前明确澄清卸载合同；正式分发前必须修复操作特定摘要并回归。
- 11:57用户反馈“识屏入口已撤销，但官方功能包还显示1.0.0”。公开复验与标准accepted等待释放不同：账本仍revision12/active1.0.0/previous1.0.1/enabledTrue/pending=null，同一卸载journal仍awaiting_confirmation/accepted=false；实际Core34672 identity匹配并持1.0.0 host revision11，1.0.1 free，两版本目录仍在，两版本cleaned_records均0。证据`manual-user-uninstall-awaiting-01.json`。入口撤销本身不是卸载已接受/完成的证明；源码流程显示生命周期准备可能先于accepted提交，准备结果/草稿原因未写入该journal，必须由用户提供管理卡片状态/原因后定位。没有读取个人日志、代理确认、重复apply、关闭进程、删除文件或写state。
- 12:08用户补充管理卡片状态“已启用 · 1.0.0”，询问是否仅因未重启。公开状态/journal复验仍revision12/enabledTrue/pending=null及awaiting_confirmation/accepted=false；1.0.0内核占用为Core34672 host和Settings15936 host/settings，两PID均匹配本次精确自有EXE，Core创建时间匹配launch，Settings creation_time1791086803.4317422；1.0.1 free，cleaned_records均0。证据`manual-user-uninstall-status-01.json`。进程未退出会阻止安全删除，但不能代替尚未落盘的最终确认；先核对是否点击摘要下方“确认本次操作”，不从未落盘反推未点击、不无条件重启/恢复/强退。用户可见卡片确无卸载等待/原因文字，未扩大为卸载通过。

## 六、测试与验证

| 门 | 实测状态 |
|---|---|
| 新公开合同RED | 6 failed / 1 passed；固定人工entry未实现/未允许、builder缺失；原始log保留 |
| focused+related初轮 | 25 passed / 10.72s；随后补充系统保护与Worker分流合同 |
| 最新专项 | focused+related49 passed /12.44s；品牌+人工入口回归11 passed /3.76s；focused+相关文档106 passed /11.19s；此前docs/PR discipline/product-copy58 passed /0.90s，链接127文件通过 |
| 静态 | Ruff全工程通过、4修改Python format通过、affected mypy3文件通过 |
| 首轮全量 | 1 failed /3832 passed /13 skipped /14 warnings，788.77s；唯一为新设计外部品牌称谓；保留原始日志 |
| 失败修正 | 根据完整traceback将新设计文字改为中性称谓；不改业务或弱化测试 |
| 第二轮全量 | 3833 passed /13 skipped /15 warnings，pytest546.99s、wrapper548.35s，returncode0；日志`manual-full-tests-02.log` |
| H1～H4真实体验 | no-chat启用显示、真实手动识屏、自然退出/内核释放、重启后凭据可用、停用撤销/Worker释放及重启用恢复通过；ZIP同摘要幂等也通过；升级占用等待/自然退出恢复、1.0.1版本显示与升级后真实手动效果通过；回滚占用等待、自然退出/内核释放、重启生产加载及回滚后显示/真实效果通过；卸载首次锁失败已修复，新管理窗口接受后等待真实host自然退出，用户安全重试完成；14:01已核验两个版本文件删除与revision14未安装提交；ZIP新安装经普通设置真实加载已完成revision17/pending=null；自然重启后的Core30256真实host租约及execution resolver核验通过；用户明确确认入口/原设置/未重填密钥/结果正确，卸载ZIP重装的数据可用性与手动效果通过；首个Core未生成host绑定原因仍未证实，未称修复；自动/其他人工门待验收 |
| 正式信任锚/分发 | 已核对空锚fail-closed与现有manual/validation构建边界；T0候选方案待用户确认，尚未生成正式key或实施正式生产构建/分发门 |

本轮改变构建入口，必须重跑全量；此前CPU满负载三遍针对历史生产实现，仅作历史证据，不宣称为新人工构建的人工通过。

## 七、已知限制与后续

H0实物已准备；H1目录安装和新Core生产加载已由系统元数据观察到，用户另明确确认“设置显示已启用”。这一人工回执仅对应状态显示；H1的入口/ZIP幂等及H2～H4不据此自动通过。
用户已反馈真实手动识屏“结果符合”、正常退出“已退出”，同配置重启后不重填密钥又反馈“得到了正确结果”。这些子项人工通过；自动识屏仍未验收。包级停用/重新启用的入口撤销/恢复、Worker释放与真实请求恢复也已确认。用户ZIP同摘要幂等提示与账本不变也通过。升级已发生占用等待，用户自然退出后系统恢复至active1.0.1/previous1.0.0且完成生产加载确认；用户随后明确“版本显示和识屏结果正确”，已登记升级后显示/手动效果通过；未据此推断任何凭据内容。retained previous回滚等待的Settings21804/Core26260两个host owner已自然退出，核验内核free后代生产启动完成回滚；当前Core34672/active1.0.0/previous1.0.1/revision12/pending=null。用户随后针对1.0.0显示与真实手动效果明确“确认通过”，回滚人工子项通过；没有热替换或强退。卸载后续已由用户明确点击“确认本次操作”，返回 management_lock_busy；journal尚未接受是后台锁竞争的结果，不能反推用户漏点。入口撤销发生在准备阶段，不是卸载完成。
卸载/重装、全新ZIP安装、多进程/草稿和第二变体仍未人工验收，不能因为一次state或识屏成功勾全部通过。
非核心遗留UX-M1：当前阻塞提示直接展示内部lease字典，用户不易理解；后续改为PID/用途/版本/退出指引与折叠诊断。此为可读性缺陷，不降级内核安全门，也不隐瞒阻塞。
UX-M2：卸载确认页复用安装/升级通用文本，错误显示“回滚目标”及宽泛“官方签名与兼容性已验证”。实际本次只检查删除边界；接受卸载后不能取消恢复启用。本修复切片已改为操作特定摘要并用实际预检回归；新Core03尚待人工观察，旧运行程序仍有原文案。该项不降为最终可接受缺陷。
仅用户明确回执的项目标为人工通过，其余保持待验收。正式key归属/离线保管/备份和轮换需T0确认后再实施。
本轮临时构建不得正式分发。Setup、代码签名证书/可信发布者、其他平台和正式发布授权均未包含在自动完成结论中。

## 八、风险与回滚

无Git暂存/提交/推送；仅按本轮明确文件白名单与before-image回滚，不覆盖历史WIP。
配置和生成产物在自有E盘目录；停止人工进程优先正常关窗/托盘退出。目录中出现用户输入后不得自动删除其配置或安全存储记录。
正式私钥未产生，因此没有以明文私钥留在仓库或临时日志的风险。本次临时公钥不是正式信任锚。

### 新快照验证进行中（2026-10-04）

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

### 高负载失败后的所有权复核（2026-10-04）

- `manual-lock-high-load-01` 第1遍原生退出码3221225477，85.38s；栈仍在真实Worker handoff的QEventLoop。前一次48 passed只是一轮组合，不证明高负载门通过；该结果已撤回为未完成。
- 回溯新增页面重建夹具的所有权：已显式删除页面，但夹具仅关闭管理器，未证明该拥有者及其Qt子对象实际销毁。追加夹具自有manager的DeferredDelete投递与Cpp invalid断言，生产实现不变，不通过全局清空事件、强退用户进程或扩大超时处理。
- 精确原组合随后48 passed /26.73s，`manual-lock-recreation-manager-destroy-result.json`；这是局部结果，67项时序族高负载三遍正在重跑。全量须在本快照重新执行，无跳过、无诊断插件。

### 修复快照累计门（2026-10-04；人工复验仍待执行）

- `manual-lock-high-load-02`：20个自有CPU负载进程、67项受影响Qt/IPC/真实Worker/锁回归连续三遍均通过，pytest89.06s/78.89s/82.86s，wrapper91.373s/80.936s/84.533s；各遍CPU样本median/p95均100%，min100%/99.9%/99.9%。进程全部按Event退出，不涉及用户进程。命令与样本保存在同目录`performance.json`及pass日志。
- 最新Ruff全工程通过，format-check518文件通过，affected mypy3源文件通过；报告纪律与相关门99 passed /3.03s。文档审计初次捕获WORKLOG末尾空行，已仅规范本次记录EOF；20文件白名单、18 before-image、2保护文件、空暂存审计通过。误写不存在的copy测试路径导致no-tests已更正，不算产品失败。
- `manual-lock-full-03`正在执行标准未过滤全量：没有诊断插件、PYTHONPATH附加或用例排除。只有退出码0且全部结果生成后登记通过。修复后产品源hash与双03快照逐项相同，测试所有权修正不要求重建产品。
- 当前人工卸载仍未通过：03未启动、不代理确认、不手改账本、不删除用户安装目录或个人数据。下一步仅用户正常关闭旧Settings，桌宠先不退出，再代启03管理专用设置。

### 13:21 公开人工状态复验（2026-10-04）

仅核对公开state/journal和精确自有EXE身份：revision12/active1.0.0/previous1.0.1/enabledTrue/pending=null，两份卸载journal仍未接受；Core34672身份匹配仍运行。原Settings15936已自然退出，精确02/03 no-chat EXE枚举仅Core34672，没有其他Settings、没有03进程。无需再要求用户关闭已经结束的Settings。无自动apply/recover/restart、无安装目录删除、无config/真实日志/凭据读取。证据manual-lock-pre-handoff-readonly-01.json、manual-lock-owned-programs-01.json。

下一步在full03通过和最终静态文档门后，重新核对Settings仍无存活，代打开03管理专用Settings，同一原profile；桌宠先保持运行，用户新预检并确认，再由用户正常退出Core释放驻留host。

### 无过滤全量03及已有异步探测夹具（2026-10-04）

- full03正常完成（无Qt原生崩溃）：1 failed /3840 passed /13 skipped /14 warnings，pytest488.47s、wrapper489.48s、退出码1；唯一失败为`test_control_group_spawns_normally_without_session_end`，不登记全量通过。完整日志`manual-lock-full-03.log`。
- 读实际失败栈及`WebMClip._ensure_meta()`：GUI线程分支明确把元数据探测提交后台预热，旧正对照测试在返回后立即断言`spy.count_calls`。没有Event/Condition完成证据，故是已有测试的异步时序竞态；日志中的read_frames返回None为该spy原有约定，不改生产reader或ffmpeg门。
- 保存补充白名单before-image`manual-lock-ffmpeg-fixture-baseline-20261004-132942`，只修改`tests/test_session_end_ffmpeg_guard.py`：read/count各自Event，宽15s预算等待真实调用，finally始终cleanup夹具clip。保留GUI异步行为、不固定sleep、不mock线程、不更改产品动画代码。
- 相关session-end/reader生命周期42 passed /14.94s；该新增受影响时序族满负载三遍正在执行，再重跑无过滤full04。原67项满负载三遍仍对应未改变的产品源码/管理UI测试快照。
- Python外层验证runner打印中文失败摘要曾触发cp1252编码异常；pytest退出码和原日志已独立落盘，不把shell退出误认为pytest成功。仅为ignored自有runner显式UTF-8输出，生产构建不变。

### 已有meta夹具修正的最终专项门（2026-10-04）

- `manual-lock-meta-high-load-01`：20个自有CPU负载进程，42项session-end/reader生命周期连续三遍通过；pytest19.26 / 19.96 / 18.25s，wrapper21.462/21.670/19.697s；三遍CPU median/p95均100%，min100%/99.9%/100%。Event完成证据消除立即断言竞态，未扩大生产超时、未把GUI元数据改同步。
- 最新静态重新通过：Ruff全工程、format-check518文件、affected mypy3源。补充已有测试before-image后，审计范围21文件/19 before-image/2保护文件/空暂存，通过；原5文件before-image不被覆盖。
- 完整无过滤`manual-lock-full-04`正在执行。full03的一项已有夹具失败与早期原生AV都保留为历史失败，不修改日志、不用单测或排除测试的对照结果替代全量。

### 锁失败修复最终验证与人工窗口交接（2026-10-04 13:48）

- 完整标准无过滤命令：`python .scratch/phase4b-local-management/manual-lock-test-runner.py manual-lock-full-04`，内部为`python -m pytest -q --basetemp <自有临时目录>`；3841 passed /13 skipped /14 warnings，pytest498.82s，wrapper499.795s，退出码0。日志与原生返回码分别保存在manual-lock-full-04.log/result.json。没有附加排除条件/诊断plugin；full01/02原生失败、full03旧测试时序失败与修正保留，不将历史失败改写为通过。
- 受影响Qt/IPC/Worker/锁族67项及session-end/reader族42项，各在20个自有CPU负载进程下三遍通过；CPU median/p95每遍100%，原生负载与父端均正常回收。最新Ruff、format-check518文件、affected mypy3源、报告门99项、文档链接和git diff --check通过。白名单21文件/19 before-image/2保护文件与空暂存审计通过；没有git add/commit/push。
- 13:47公开只读核验：仅原Core34672（identity1791085260.8862312）还在运行，旧Settings已退出；revision12/active1.0.0/previous1.0.1/enabledTrue/pending=null，两卸载journal仍未接受，两版本目录仍在。没有让用户再次关闭已结束的旧设置。
- 已实际执行`python .scratch/phase4b-local-management/manual-run-entry-03.py --variant no-chat --mode settings`：新Settings PID7060/creation1791092844.339965，真实可见“桌宠设置”，4.968s冷启动；RSS140451840字节、11线程、CPU1.296875s、read25159646/write72字节（单次启动样本n=1，不代表稳态或泄漏分析）。使用原APPDATA，不重填/读取凭据，没有源码/Qt路径回退。13:48再次只读检查身份匹配、公共state仍revision12/pending=null、新设置无所属版本租约，证明管理窗口本身没有无意义host占用；尚未代替用户确认或物理卸载。
- 证据：manual-lock-pre-handoff-readonly-02.json、manual-session-01/no-chat/settings-launch.json、manual-lock-new-settings03-public-01.json。仅读取公开安装状态/自有进程和窗口元数据；不读取新窗口原始stdout、真实配置/凭据/模型内容或桌面图像。原02产物不覆盖，03沿用已有公开人工测试锚，正式私钥未创建/加载。
- 准确人工下一步：用户在新窗口卸载→确认本次操作，反馈实际等待/失败原因，先保留原Core；接受后若租约占用，由用户自然退出原Core，再安全重试并核验安装版本文件消失、未安装状态和个人数据保留；之后ZIP新安装。人工卸载/重装、其余人体验及正式T0～T3仍未验收，不提前报告完成。

### 新管理窗口的卸载接受与真实占用等待（2026-10-04 13:55）

- 用户实际确认后回执“等待进程自然释放；不会强制关闭 / version_in_use”。公共账本revision13/active1.0.0/previous1.0.1/enabledFalse/pending=tx-56514a949d454d6792cbc34de9234d83；journal accepted=true、phase=pending_runtime_release，delete_versions=[1.0.0,1.0.1]、deleted_versions=[]。与旧锁失败时revision12/pending=null/accepted=false明确区分：此次卸载已接受，执行已禁用，物理删除尚未开始。
- 仅读公开state/journal、验证后的租约元数据与精确自有进程；在生产leases协调锁下直接调用existing kernel lock probe（create=False），不删除死记录、不提交state、不调用事务apply/recover。1.0.0仅Core34672的host仍由内核证明占用（旧租约revision11不因账本revision13失效）；1.0.1 free，两物理版本目录仍在。Core34672/creation1791085260.8862312与Settings7060/creation1791092844.339965身份匹配；新设置不持有功能host租约。完整公开证据manual-user-uninstall-await-release-01.json。
- 已请用户仅通过托盘正常退出桌宠、保持新设置打开，退出后先核验内核释放，再由用户安全重试。不会强制关闭合法进程、热卸载驻留host、代替UI删除版本或取消已接受卸载。只有实际版本文件消失和未安装提交均有证据后，才登记卸载完成；ZIP新安装与保留凭据真实效果仍待后续。
- 本次没有产品/测试改动，仅人工回执、公开诊断及当前记录更新；13:48全量/压力/静态结果为上一修复快照证据，不冒充本次重新运行。当前文档/报告及21文件白名单审计续验；未stage/commit/push/release，无正式私钥生成或信任策略变更。

### 卸载中的自然退出与内核释放（2026-10-04 13:58）

用户明确回执“已退出”。按精确创建时间核验原Core34672身份已结束，管理Settings7060仍运行；在既有协调锁下对版本lease内核锁直接非创建探测，1.0.0/1.0.1均free，未删除租约元数据、未调用apply/recover。账本仍revision13/enabledFalse/pending=tx-56514a949d454d6792cbc34de9234d83，journal已接受/pending_runtime_release，deleted_versions为空，两个安装目录仍存在，物理卸载尚未完成。已请用户在同一新窗口直接点击“安全重试”；不另开预检、不替代用户操作。公开证据manual-user-uninstall-natural-exit-01.json；下一步只有实际文件全部删除与未安装提交均有证据后才登记完成。前次纯记录更新报告门99 passed/1.16s、127文件链接和21文件/19 before-image/2保护文件/空暂存审计通过；本次没有产品或测试修改，不重跑全量或新增性能测量。

### 真实物理卸载完成（2026-10-04 14:01）

- 用户在内核租约已释放后安全重试，回执“操作已完成”。只读公共state revision14/active=null/previous=null/enabledFalse/pending_transaction=null/versions={}；tx-56514a949d454d6792cbc34de9234d83 journal accepted=true/phase=completed/deleted_versions=[1.0.0,1.0.1]。versions目录无任何条目，两个已登记版本路径均不存在。至此登记真实卸载的接受→自然等待→进程退出→安全重试→物理删除→未安装提交闭环；首次锁失败与其修正仍保留，不改写为第一次成功。
- 原Core34672身份已结束、新Settings7060身份匹配仍运行。执行助手未调用apply/recover、未手工删安装文件、未读个人配置/凭据/历史/原始stdout；卸载由用户新UI和真实后台事务完成。公开证据manual-user-uninstall-completed-01.json。
- 下一步ZIP新安装：现有生成v1.zip为24450210字节，SHA-256与原构建证据相符（2964c7c71b031c3f02b900a723488d75e9879a43cc2959a508267a042a8ebf3f），新版仍信任相同人工测试公钥；完整签名/兼容性须再次经管理UI预检，不拿文件hash代替Verifier。不会重填/读取密钥，可能等待新Core真实启动加载确认，由助手在公共pending核验后代启动生产入口。
- 个人设置/profile/凭据保留的用户可用性尚待ZIP新安装后真实识屏确认，不能仅凭state/安装目录删除证明私人数据字节不变。之前ZIP同摘要幂等不是此次ZIP新安装。正式信任/分发、其他人工门仍未通过；本次仅记录更新，无产品/测试改动，文档/报告/21文件白名单审计续验，跳过全量理由是产品、生命周期及平台实现均未修改。

### ZIP新安装真实加载确认与首Core待诊断（2026-10-04 14:19）

- 用户完成ZIP预检/确认并反馈等待启动。先只读核验operation tx-3a1a2a78ddf949a295bfd96dcb4f23ee：kind=install/source_type=zip/revision14 before versions={}，source_digest为原v1 ZIP SHA，self_check_passed=true/accepted=true；账本revision16/active1.0.0/enabledTrue/previous=null/pending该事务，非重复ZIP幂等。产品三源hash与03双冻结快照相符。
- 实际命令`python .scratch/phase4b-local-management/manual-run-entry-03.py --variant no-chat --mode core`：同原APPDATA、真实03正常Core/Qt/生产bootstrap；Core31020创建时间1791094184.485062，自有桌宠窗出现，但30s有界公共监测及后续核验仍pending、无host租约。没有把GUI出现算作加载成功，也没有手写receipt或读原始私人日志。首Core未完成确认原因**未证实**，列UX-M3待复现诊断；后续即时锁free不能反推出启动时不存在竞争。
- 用户关闭管理设置后，精确身份Settings7060已结束，Core31020仍运行；账本仍revision16/pending，说明关闭本身没有提交确认。助手追加生成物专用普通设置launcher并执行`python .scratch/phase4b-local-management/manual-run-normal-settings-03.py --variant no-chat --mode normal-settings`，真实冻结EXE只传`--settings`，无extensions管理深链、无源码/PYTHONPATH回退或合成边界，保留同原APPDATA及真实安全存储。
- 真实普通Settings15792创建时间1791094739.3694797；独立公共核验revision17/active1.0.0/enabledTrue/previous=null/pending=null，原ZIP事务phase=completed。1.0.0 manifest_digest仍94fef52316dfbf8f295f5f8a1e9ed887b550aa9c2551d57297ba14beda231097；该Settings拥有host revision16与settings revision17两个实际内核occupied租约，身份和digest匹配。确认由实际生产加载路径生成，不由代理/管理页认证。证据manual-user-zip-reinstall-await-startup-01.json、manual-user-zip-reinstall-startup-not-confirmed-01.json、manual-user-zip-reinstall-settings-closed-01.json及manual-user-zip-reinstall-normal-settings-load-01.json（均仅公共元数据，不入库原始输出）。
- 冷启动实际样本n=1，各命令与launcher公共输出对应：Core自有窗3.923s、RSS93904896B/线程18/CPU0.9375s/累计读143750764B/写2205B；普通Settings自有窗3.363s、RSS151736320B/线程11/CPU3.1875s/累计读1156367516B/写10505B。单样本非median/p95、非精确receipt耗时；进程确已启动，正常生产路径可能继续IO，不能把首次Core出现窗口当作功能正常。
- 下一步请用户托盘自然退出未绑定功能的Core，再由助手启动同03正常入口，核验Core真实host租约并请用户不重填密钥验证原设置及识屏。当前Settings可保持打开；同版本租约不是强退或热导入的理由。ZIP生产加载门通过不等于数据保留体验/全部人工门通过，正式T0～T3仍未通过。
- 本轮没有产品/测试源改动，生成的终端launcher及公共诊断留在ignored .scratch；同一组记录/README/LOG更新，验证文档/报告/21文件白名单/保护文件与空暂存。完整全量不重跑理由：仅记录和自有程序启动，没有改变产品接口、持久化实现、生命周期或平台分支；既有full04和满CPU结果按日期保存。无提交/推送/发布、无子智能体，无正式私钥操作。

- 本轮记录门：python -m pytest -q tests/test_check_docs.py tests/test_pr_report_discipline.py tests/test_report_gates.py → 101 passed/1.09s；21文件白名单/19 before-image/2保护文件均通过，git diff检查通过、暂存空。产品源未变，不重跑全量。

### ZIP重装后的Core实际加载通过（2026-10-04 14:30）

- 用户确认自然退出，并要求“做完操作直接汇报告诉我该怎么办”。先核验同03 EXE原Core/Settings均已结束（没有强退），再执行`python .scratch/phase4b-local-management/manual-run-entry-03.py --variant no-chat --mode core`；同一原APPDATA、无源码回退、正常生产bootstrap，不重复安装或代理提交成功回执。
- 新Core30256/creation1791095122.0622613出现真实自有桌宠窗口；冷启动实测n=1为4.218s，RSS113209344B、线程14、CPU2.5s、累计读550048972B/写2632B。单样本不是median/p95，也不是精确加载receipt耗时；生产进程继续运行，无额外忙轮询或测试请求。
- 14:30公共只读验证：revision17/active1.0.0/enabledTrue/previous=null/pending=null，ZIPinstall事务tx-3a1a2a78ddf949a295bfd96dcb4f23ee保持completed。核验精确EXE/创建时间后，Core30256的host lease c1ea6a78eb1f43f2a07b39c18718ecf8实际内核occupied，绑定版本1.0.0/revision17/manifest digest94fef52316dfbf8f295f5f8a1e9ed887b550aa9c2551d57297ba14beda231097。仅复用verifier与execution resolver重新验签/哈希，不执行factory；resolver resolved/revision17。没有将窗口或PID当租约证明，没有读取私人配置、凭据、截图、请求结果或原始日志，没有清理租约或手写receipt。
- 自有公共证据manual-user-zip-reinstall-core-natural-exit-01.json、manual-user-zip-reinstall-core-restarted-01.json留ignored目录；报告保留命令、身份、数字和必要结果。新版Core真实host加载门通过；原设置/凭据保留与手动识屏效果仍待用户一次确认。首个Core未完成pending加载的UX-M3原因仍未证实，不把自然重启后成功冒充根因修复。
- 本轮仅启动/公共核验/文档记录，产品与测试源未修改，三产品源码hash仍与03双冻结快照对应。只续验文档/报告/白名单与保护文件，未重跑全量；full04及满CPU三遍为明确历史证据。无暂存/提交/推送/发布、无子智能体；未操作正式私钥。

- 本轮记录门：python -m pytest -q tests/test_check_docs.py tests/test_pr_report_discipline.py tests/test_report_gates.py → 101 passed/1.58s；21文件白名单/19 before-image/2保护文件审计通过，git diff --check通过、暂存空。初次审计发现本轮追加LOG末尾多余空行，删除该空行后转绿，不改历史内容或放宽检查。

### ZIP重装数据保留用户通过与正式T0候选（2026-10-04 14:52）

- 用户明确回执：“入口恢复，原设置保留，未重填密钥，结果正确”。限定no-chat manual-core-03/1.0.0在物理卸载后ZIP新安装、普通设置真实加载确认、Core自然重启；据此记录原设置/凭据可用性与真实手动识屏通过。没有读取用户的配置、凭据、截图、请求内容或原始日志，没有把可用性推导成所有私人数据字节一致、自动识屏/第二变体或全部人工门通过。
- 本轮公共只读命令复用FeatureInstallStateStore.read、journal及create=False内核租约锁：14:52仍revision17/active1.0.0/enabledTrue/previous=null/pending=null，原ZIP事务completed，Core30256创建时间/EXE匹配，1.0.0/revision17 host lease仍native occupied。公开元数据保存manual-user-zip-reinstall-confirmed-01.json；检查不触发factory、receipt、截图或模型，不修改账本、不清理租约、不强退。
- 正式T0实读核对：pet/feature_build_policy.py的OFFICIAL_FEATURE_TRUST_ANCHORS=()和helper pin=None，保持fail-closed。scripts/build_screen_delivery.py的prepare_core只接受两种manual/validation入口，注入明确测试锚/VALIDATION_BUILD=True；现有签包器仅临时内存key，尚无正式密钥保管和生产分发入口。当前产物不能改名当正式，临时公钥也不能转正式。
- [同一总设计](plugin-phase-04-updates/PHASE4B-LOCAL-MANAGEMENT-DESIGN.md)补入T0候选：用户独占正式Ed25519 key，仓库外加密PKCS8与独立离线备份；口令只在本机专用交互工具，不进入聊天/argv/env/日志；正式公钥/helper摘要固定Core，签包与构建分离。泄露撤销须更新Core并移除旧锚，不能宣称旧离线Core即时撤销。归属/目录/备份恢复仍待确认，不生成正式key，不注入正式锚，不改现有构建或正式策略，不发布。
- 前置关联命令python -m pytest -q tests/test_feature_manual_acceptance.py tests/test_feature_packages.py tests/test_screen_delivery_build.py →119 passed/1 skipped/22.40s。这只证明既有人工入口、验签与构建合同未回归，不是正式T1～T3通过。产品与测试源未变，文档10份before-image先保存，同组记录/报告/README/LOG续验；完整全量不重跑理由：公开文档与证据更新不改变产品接口/持久化/生命周期/依赖/平台逻辑。历史full04/CPU三遍保留其快照。

- 本轮记录门：python -m pytest -q tests/test_check_docs.py tests/test_pr_report_discipline.py tests/test_report_gates.py →101 passed/3.60s；21文件白名单/19 before-image/2保护文件审计通过，tracked及明确untracked范围diff检查通过、暂存空。三产品源码hash保持03已验收快照；本轮无产品/测试变更，TDD不适用，既有119项前置关联测试已实际复跑。

## 通俗使用效果与边界

你已确认当前no-chat桌宠在物理卸载后从ZIP重装，识屏入口恢复、原设置保留、不重填密钥仍能正确识屏；这一项真实数据可用性/识屏人工门通过，无需再安装或重启来重复验收。正式发行尚未完成：当前Core与包仍采用明确人工测试公钥，不应作为正式产物发给最终使用者。下一步需先确认正式密钥归属、加密保管、离线备份与轮换撤销方案，之后才实施正式签包、双Core生产构建及分发目录/ZIP验收。首次Core未完成pending加载原因尚未证明修复，自动/其他人工门继续单列；不会读取私人内容、强退、改state或擅自生成正式私钥/发布。
