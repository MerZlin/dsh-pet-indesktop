# PR 报告：项目目录识屏 Worker 启动与 API 连接反馈（2026-10-10）

> **验收补记（2026-10-10，最新）**：用户已反馈“实际体验确认没问题”，对应本文 4.2.4 候选。下文“待用户确认”为反馈前的工程快照；现用户体验门通过。源码检查点 M00 正重跑门禁，尚未提交/推送；不据此宣称正式发行或新 MOD 功能已完成。

> **状态：S01–S04 工程交付完成；真实 Provider/屏幕效果与用户安装体验仍待确认，Phase5A 不关闭。** 无提交、推送或正式发布；这是连续Phase5A工作中的S修复，不是新架构设计。
>
> **基线**：任务开始dirty快照（`baseline.json`，769文件）/分支`codex/phase3-worker`，HEAD `70ff464f`。以下增删与该快照比较，不把此前T/R/U/A未提交修改归入本轮；全分支numstat另外保存在证据根。
>
> **关联**：[阶段设计](plugin-phase-05-distribution/PHASE5A-LOCAL-DISTRIBUTION-DESIGN.md) · [计划](../.scratch/phase5a-local-distribution/PLAN.md) · [停点](../.scratch/phase5a-local-distribution/HANDOFF.md) · [状态](../.scratch/phase5a-local-distribution/STATUS.md) · [工作日志](../.scratch/phase5a-local-distribution/WORKLOG.md) · [总结](../.scratch/phase5a-local-distribution/SUMMARY.md) · [A简易API报告](PR-REPORT-SIMPLE-API-2026-10-09.md) · [U安装器报告](PR-REPORT-SETUP-PROJECT-DIRECTORY-2026-10-09.md)。

## M00 用户验收后提交门禁

**M00 新门禁已通过（2026-10-10）**：Ruff、工作树/暂存 `git diff --check`；聚焦 **123 passed in 189.57s**；全量 **4509 passed, 15 skipped, 14 warnings in 1910.76s**，exit 0，643 个源码输入摘要前后一致。26 个相关时序族在 20 个自有逐核负载进程下复跑：三轮各 **292 passed**，pytest **198.76 / 143.17 / 137.04s**（调度总时长 **201.656 / 146.203 / 138.828s**），CPU 中位均 **100.0%**，全部负载进程自然退出、残留为空。159 文件敏感模式扫描零命中；540 个相对链接无缺失，报告均入索引。暂存检查发现并修正文档的两行尾随空格；没有更改产品代码来通过门禁。

Windows 11 build26100、Python3.11.1。命令：`python -m ruff check pet features scripts tests packaging`、`python -X utf8 -m pytest -q`（隔离 APPDATA/LOCALAPPDATA、offscreen 和独立 basetemp）、Git 工作树/暂存差异检查。负载族列表与每轮 CPU 样本/退出码由 `m00-checkpoint-20261010/run_highload.py` 和 `delivery-highload-release.json` 留在本机证据目录；不把日志/缓存/脚本化临时数据纳入提交。历史冻结与性能实测仍见 S/U/A 报告，不宣称本轮重新构建或重新使用真实 Provider。

**准确停点**：以上结果已收齐，尚未提交/推送；下一步是最后核对暂存范围和远端未分叉，提交当前源检查点，再正常推送并核对 SHA。新 MOD 功能尚未开始。

## 一、核心特性

| 问题 | 修复与验证结论 |
|---|---|
| portable项目目录内Worker启动前被拒绝，误提示服务暂停 | 仅允许可信协调器data_root下feature-runtime；不开放Core/DLC程序树，先记录脱敏原因再发布FAULT。目录/失败/重试已有red→green。 |
| 真正冻结Core接入后READY仍意外退出 | 冻结试跑暴露签名验证耗时误入运行心跳；新红灯证明根因，运行心跳只从READY开始，独立握手超时保留。 |
| API连接反馈不明显 | 测试按钮旁固定“测试中/连接成功/连接失败 · HTTP状态码”，无HTTP响应使用TIMEOUT/NETWORK_ERROR/TLS_ERROR；备注沿用解释，测试当前草稿且不保存。 |
| 用户担心安装后的EXE图标仍不对 | 核对最终Core/Setup及隔离安装副本PE全部10帧等于既有鲸鱼娘ICO；用Windows Shell读取两个EXE和私有快捷方式实显，均目视确认鲸鱼娘。未清用户图标缓存或修改用户快捷方式。 |

**不变量**：不改变FeatureHostContext.api，不恢复Core内视觉执行，不特判官方factory/owner；保留简易主/视觉Key、自动/手动业务策略、授权/租约/环境隔离、Setup安装卸载行为。真实安装目录/用户Key/真实截图/代理VPN/正式密钥不触碰；旧候选不覆盖。

## 二、修改文件说明

命令：`git diff --no-index --numstat <证据根>/baseline/<文件> <工作区>/<文件>`。新增文件按从空文件统计；完整分支另外执行`git diff --numstat`，不能拿后者冒称本轮增量。下面逐文件写职责及原因，没有删除文件；生成物/截图/日志不纳入源码提交。

<!-- S_FILE_TABLE_START -->
| 文件 | 本轮增删 | 改了什么、为什么 |
|---|---:|---|
| `.scratch/phase5a-local-distribution/HANDOFF.md` | +42 / −0 | 准确停点、唯一release候选、下一步命令/用户动作与保护边界。 |
| `.scratch/phase5a-local-distribution/PLAN.md` | +41 / −0 | S01–S04编号、依赖与绿门/用户门分开。 |
| `.scratch/phase5a-local-distribution/STATUS.md` | +38 / −0 | 实施/自动化/实机/用户/发布状态分层，不拿旧轮绿灯充数。 |
| `.scratch/phase5a-local-distribution/SUMMARY.md` | +38 / −0 | 跨对话精简现行合同、候选、坑点和不允许的回退。 |
| `.scratch/phase5a-local-distribution/WORKLOG.md` | +40 / −0 | 记录目录/心跳/UI red-green、两次full失败及最终复验。 |
| `LOG-INDEX.md` | +1 / −0 | 登记S主题入口，不把日志复制为第二套权威。 |
| `LOG.md` | +13 / −0 | 追加S工程/失败/人工边界记录，保留此前R/U/A历史。 |
| `docs/INDEX.md` | +2 / −0 | 登记本报告，给出内容/必读时机。 |
| `docs/PHASE5A-CLOSEOUT-IMPLEMENTATION-PLAN-2026-10-07.md` | +17 / −1 | 原计划追加S现行合同和证据，U/T/R标明历史。 |
| `docs/PHASE5A-CODE-IMPLEMENTATION-HANDOFF-2026-10-07.md` | +17 / −1 | 交付追加最新候选/版本/人工步骤，防止沿用旧Setup。 |
| `docs/PR-REPORT-PORTABLE-SCREEN-WORKER-2026-10-10.md`（新增） | +193 / −0 | 新增：逐文件增删、实测性能、实机/测试与失败历史、hash及人工验收。 |
| `docs/PROJECT-ENTRY.md` | +9 / −7 | 入口纠正旧R用途授权说法，指向本轮唯一候选和准确工程/人工状态。 |
| `docs/plugin-phase-05-distribution/PHASE5A-LOCAL-DISTRIBUTION-DESIGN.md` | +36 / −0 | 登记S目录合同、心跳及追加UI要求，不拆出另一套Phase5A设计。 |
| `features/screen_understanding/host/runtime.py` | +10 / −3 | 同步失败读取已脱敏原因，FAULT后手动重试；限量INFO记录真实生命周期，以便冻结链路可审计。 |
| `features/screen_understanding/host/worker_diagnostics.py` | +18 / −1 | 扩充固定启动/目录/包/授权/心跳原因白名单和操作提示，不反射任意异常或秘密。 |
| `pet/__init__.py` | +1 / −1 | Core候选版本4.2.4，避免以旧号覆盖不同字节。 |
| `pet/api_probe.py` | +20 / −6 | 不可变ProbeResult携带真实HTTP状态；HTTP鉴权、TLS、超时、网络分类且不读/显示Provider正文。 |
| `pet/plugins/worker_launch.py` | +32 / −10 | 严格runtime边界增加可信portable豁免；验证失败释放已预留租约，不放开程序树/reparse。 |
| `pet/settings_api.py` | +64 / −7 | 按钮旁结果/备注与pending状态；Signal传递结果对象、草稿变更清旧状态、明暗主题和accessibleName保持。 |
| `pet/workers/supervisor.py` | +16 / −5 | 固定脱敏原因先于FAULT；READY后启动心跳，不把签名校验耗时计入运行预算。 |
| `scripts/build_screen_delivery.py` | +4 / −4 | Screen1.0.3及>=Core4.2.4的可重建元数据，AI1.0.3维持既有要求。 |
| `scripts/feature_release_materials.py` | +2 / −2 | 统一Screen/Core交付元数据，签名输入和实际候选一致。 |
| `scripts/validate_phase5a_delivery.py` | +1 / −1 | 交付验证器识别Screen1.0.3，拒绝把旧包视为新候选。 |
| `tests/test_api_connection_feedback.py`（新增） | +137 / −0 | 新增：真实本地HTTP状态及Qt按钮结果、超时/网络/TLS、测试不保存/草稿/主题回归。 |
| `tests/test_core_api_migration_ui.py` | +2 / −2 | 最小探针断言改为有HTTP200的ProbeResult，仍验证测试不保存。 |
| `tests/test_external_worker_launch.py` | +39 / −0 | 公开supervisor边界验证诊断早于FAULT及异常脱敏。 |
| `tests/test_feature_package_probe.py` | +3 / −3 | Core4.2.4/Screen1.0.3兼容及冻结清单版本断言同步。 |
| `tests/test_feature_packages.py` | +3 / −1 | 原目录拒绝继续生效，断言固定StateError而非未分类ValueError。 |
| `tests/test_feature_runtime_ports.py` | +81 / −1 | 真实Qt/QProcess下同步失败、修复后手动重试、自动关闭/配置刷新/取消回收。 |
| `tests/test_phase5a_repair_regressions.py` | +5 / −5 | AI/Screen分别要求正确Core下限与交付版本，修正过期固定值而不放宽验证。 |
| `tests/test_portable_worker_launch.py`（新增） | +114 / −0 | 新增：真实文件/租约验证portable允许区、跨Core/_internal/DLC/未授权/reparse拒绝、验证失败释放reservation。 |
| `tests/test_screen_delivery_build.py` | +4 / −0 | Screen新版本与Core最低版本构建断言。 |
| `tests/test_simple_api_settings.py` | +4 / −3 | 简单主/视觉Key探针预期结构适配，仍不修改保存或绑定语义。 |
| `tests/test_workers_lifecycle.py` | +42 / −7 | 真实子进程模拟慢验证，READY前不得误触心跳；失心跳用例先等READY，LF协议替代Windows CRLF偶然失败。 |
<!-- S_FILE_TABLE_END -->

**刻意不改**：`packaging/core_webm.iss`及Setup构建行为字节与S基线相同；`assets/icon.ico`不更换素材；不改简易API保存/凭据规则、DLC事务、Core系统卸载。Worker源闭合清单没有本轮业务修改，但Core版本文件属于其输入，初次复用被校验拒绝后已重建；本轮最终Core/UI重建时再校验并复用当前S Worker，不复用输入不匹配的A Worker。

## 三、实现要点与失败历史

### 可信运行区与诊断

先检查绝对路径及lexical祖先reparse，再resolve；拒绝descriptor程序树和Core程序树。唯一例外是安装选择与descriptor一致、`lease_coordinator.data_root == core_root/data`且runtime在其feature-runtime内。这个例外不是任意data目录白名单。启动前再次验证完整包和执行授权，reservation已拿到但acquire_worker失败时也释放。启动失败只发布固定code；host保存该原因后再处理FAULT，手动请求不被通用“已暂停”覆盖，主动重试可以重新启动。

### 心跳与Qt线程

原慢校验发生于launch_factory，运行心跳却从启动前的monotonic开始；Windows冻结包真实校验使其超过预算，READY后立刻误判。现在仅READY后启动heartbeat timer、重置基准；HANDSHAKING仍由既有独立timer负责。真实Qt/QProcess红灯通过注入模块时钟偏移而非sleep猜时间；运行后失去心跳的旧用例先断言READY，再断言FAULT。Windows子进程fixture改用二进制LF协议（原text print产生CRLF，根本未READY，曾偶然测试了错误路径），不改变生产协议。

连接探针仍在既有后台job中，queued Qt信号回GUI；新增对象只携带reason/可选HTTP数值，不传响应正文/URL/Key。结果文字有PlainText、自动换行和明暗主题token，按钮accessibleName不因嵌套布局丢失。草稿修改清除旧成功状态；200不表示已经保存，也不代表视觉/余额可用。

### 保留的失败而非只展示最后绿灯

- 初始目录/拒绝边界red：`red2.log` **5 failed /17 passed /1 skipped**；`diagnostic-red.log`单独证明缺少脱敏INFO阶段记录。
- 最初相关green **213 passed /1 skipped (39.28s)**，这是最终UI/心跳改动前的阶段证据，不能冒充最终full。
- 第一轮冻结完整链路到READY后Worker非零退出，`frozen-chain-v2.json`保留；追查得到READY前心跳误计时。新增心跳/按钮结果red `extra-red.log` **15 failed**；修复后`extra-green3.log` **87 passed**。
- 浅色实机结果白字不显眼，主题红灯 **2 failed**后修复；`feedback-theme-green.log` **16 passed**。嵌套按钮丢accessibleName，既有布局门 **4 failed**后修复，`related-complete.log` **96 passed (26.59s)**。
- 首次full **4 failed /4486 passed /15 skipped /15 warnings (800.37s)**，四个新版本元数据断言未同步；不是产品运行错误，但失败未忽略。
- 第二次full **4 failed /4503 passed /15 skipped /14 warnings (785.59s)**，四个既有布局accessibleName断言；修复在该轮运行中发生，`source_unchanged=false`，这轮不作为最终通过。
- 最终第三轮固定源码重新跑；准确结果见测试门。失败日志全部保留，未过滤默认套件，未通过重试掩盖时序失败。
- 最后静态runner误给Ruff套用隔离APPDATA，导致用户站点不可见而报“No module named ruff”；这是工具环境而非代码检查失败。已使用原工具环境运行Ruff/format通过；pytest继续保持隔离APPDATA，失败log保留。

## 四、性能分析

环境：Windows 11 build26100（Python platform报告Windows-10-10.0.26100-SP0）、Python3.11.1、PySide6/Qt6.11.1，20逻辑CPU；工作区E盘。性能不是Provider速度测试。命令均在仓库根执行，`$ev='.scratch/phase5a-local-distribution/s01-portable-worker-20261010'`；Python使用本机`E:/Program Files (x86)/Dev-Cpp/python.exe -X utf8`。

| 路径/命令与样本 | 实测 | 解释 |
|---|---|---|
| `python $ev/measure_launch_final.py`，旧external/新external/新portable各40次 | 中位54.67265 /51.57635 /51.65090ms；p95 63.05710 /59.77370 /62.83590ms | 5文件真实签名生成包、真实验证和文件租约、不启动子进程；旧portable直接拒绝无可比成功时长。并行full带来噪声，不能声称加速。 |
| 同路径80次launch/release | retained+981,703B、peak2,151,894B、线程1→1、残留Worker租约0 | 增量内存不是零；无新增网络请求。 |
| `python $ev/measure_memory.py`，分别旧/新80次，GC+tracemalloc | 旧retained992,411B/new986,888B；peak2,159,004/2,155,814B | 两者主要都是Python3.11 pathlib:71 intern表939KiB，另有JSON decoder约25–30KiB；未观测到修复独有的持续积累，但这不是长期泄漏证明。 |
| `python $ev/verify_feedback_gui.py`，明/暗×720/1100每组合500次格式更新 | 中位0.00490/0.00170/0.00270/0.00150ms；p95 0.00560/0.00180/0.00300/0.00170ms | 是已构造组件的状态文字赋值，不包含网络或整帧渲染；真实loopback交互另跑8次200/401。 |
| `python $ev/run_frozen_release.py`，最终冻结1次启动 | 双包maintenance安装43.250s；Core启动至READY 51.062s | 与全量并发，真实完整包验证及初始化成本；单样本不是常态或优化承诺。没有额外视觉探测。 |
| READY后20×250ms进程采样 | Core RSS 138.61–147.79MiB，CPU 93.8–129.7%（100%=单逻辑CPU）；Worker RSS 24.83–24.85MiB，CPU全0.0% | Core仍有动画/预热，只能算早期空闲Worker样本，不是长期稳态对照。Core/Worker随后自然退出，租约free。 |
| 最终构建 | Core137.734s，Setup90.297s | Worker/Probe输入复核后复用；真实Worker独立正常启动HELLO939.275ms/READY952.964ms/总992.663ms，不代替Core链路。 |

逐项结论：① 未修改每帧/稳态算法；心跳频率仍1s，仅启用时点改变。没有长期同场景旧新稳态对照，不宣称“零开销”。② 新增路径检查只在启动/重启触发，成功请求内没有多发视觉探测；连接结果每点击/回包/草稿修改更新，实测如表。③ runtime检查增加有界祖先文件属性读取，启动/退出增加5条白名单INFO阶段记录（本次观察），没有新业务网络/线程；连接仍一次最小探针/同一个既有后台job。④ 80次启动内存约0.94MiB增长旧新皆有，主要为路径intern缓存；线程/租约无累积，真实Worker自然退出后进程内存由OS回收。不能拿这组短测保证Provider请求后的长期内存。

## 五、实机运行记录

证据根：`E:/AI/DSH/dsh-pet-indesktop/.scratch/phase5a-local-distribution/s01-portable-worker-20261010/`，下述脚本/JSON/截图在本机保留，不混入源码提交。没有在用户`E:/dsh-pet-core-webm`执行安装、编辑配置、截屏或卸载。

### 5.1 最终冻结Core到实际安装的生产Worker（硬门）

命令：`python -X utf8 $ev/run_frozen_release.py`。脚本复制**最终**`c-release/dist`到新`_s01b/runtime-release`，放置与Setup相同的portable marker；真实冻结`--core-maintenance install-packages _s01b/packages-final official.ai-chat official.screen-understanding` exit0。仅在该独立data中生成测试设置，并使用独立临时vault引用；退出后删掉自己的生成凭据（非用户Key）。

输出：`FROZEN_CORE_INSTALLED_PRODUCTION_WORKER_FULL_CHAIN_PASSED`。Core SHA `727f7903da2272be0b5219fb8c36b975ca4919324ae1bbec8920227cc5f5a1fe`；Worker SHA `528aa96cf950ba4e93507c3ebfbb765fb83ad0490b391f03c4952caa2fec4480`。真实Worker路径位于`_s01b/runtime-release/data/plugins/official.screen-understanding/versions/1.0.3/worker/`，cwd是项目内`data/feature-runtime`，ppid为该Core；不是Core内执行或手工直接启Worker。HELLO租约handoff通过才有READY，运行时host/worker各一条active租约。

```text
INFO 主动识屏 Worker 状态: starting
INFO 主动识屏 Worker 状态: handshaking
INFO 主动识屏 Worker 状态: ready
INFO 主动识屏 Worker 状态: stopping
INFO 主动识屏 Worker 状态: stopped
core_exit=0; worker_exit=0; occupancy_after_exit=free
real_key_read=false; real_screen_capture=false; http_requests=0
own_generated_credential_removed=true
```

通过仅属于该Core的菜单Quit自然退出，不强杀；正常退出0/0和租约free均来自真实进程。使用不可能命中的白名单保证自动Worker启动但不采集屏幕。这证明**最终冻结启动链**，不声称已完成真实Provider识屏请求。自动关闭/空白名单的手动入口、重复点击、配置刷新、失败重试、取消/迟到结果由真实Qt/QProcess相关测试分别覆盖。

### 5.2 请求/返回与错误边界（分层，不混称冻结端到端）

`python -m pytest -q tests/test_proactive_worker_integration.py tests/test_feature_runtime_ports.py tests/test_api_connection_feedback.py`：使用真实Qt事件循环、真实子进程/本地HTTP服务，合成截图与生成Key；仅操作系统前台/截图这类不宜确定执行的边界替身。覆盖请求格式、响应回传、鉴权/模型不支持、网络/限流、取消、Worker退出。此组中的Python生产Worker进程不是5.1的冻结EXE，二者职责和证据独立，不声称一条测试同时涵盖全部层。

目录拒绝测试继续拒绝Core `_internal`、DLC代码区及Windows junction；包篡改/撤销时无启动并回收lease，白名单诊断不泄漏原始异常。最终全量的15个skip是既有跨平台/环境门，不等同失败或通过；本轮原生Windows junction分支实际通过，旧创建symlink需要额外权限的跳过不靠放开边界“修复”。

### 5.3 原生Windows API界面

`python $ev/verify_feedback_gui.py` 使用`QT_QPA_PLATFORM=windows`、独立Config与生成凭据，在浅/深色720/1100宽度点击按钮；实际loopback返回[200,401]×4，挂起阶段用事件同步，GUI仍显示“测试中…”。结果紧邻按钮，HTTP401备注“检查主API Key和模型权限”；成功提示仍写未保存。每次config字节不变，退出后本地server线程自然停止。

证据`feedback-gui.json`与`feedback-{light,dark}-{720,1100}-{pending,200,401}.png`；窗口无横向滚动溢出，文字所需高度与实际高度相符。逐图目视浅色401、深色200和相关字号布局，测试文字可读；这不是仅凭palette值判绿。

### 5.4 Core、Setup与快捷方式鲸鱼娘

`python $ev/audit_icons_release.py`：读取最终Core、最终Setup、隔离安装Core的PE资源，所有10个尺寸图标帧与`assets/icon.ico`一致（source SHA `6f12b92f3daa4a2790aefd0214f653ae27bc842797c4d7891dbdbcd4ec535deb`）。不只是检查spec存在icon参数。

`python $ev/audit_shell_icons.py`：用Windows `QFileIconProvider`读取最终两个EXE，并在证据根创建私有`.lnk`指向隔离安装Core（与Setup默认从EXE取图标语义一致，无额外IconLocation）。`core-shell.png`、`setup-shell.png`和`shortcut-shell.png`均目视蓝发鲸鱼娘；未修改用户桌面/开始菜单/注册表，未清系统图标缓存。最初中文快捷方式路径受COM编码失败，保留log，使用同一私有目录ASCII文件名后成功；不是产品Setup失败。用户实际旧快捷方式若仍不符，需核对其目标与本候选hash，不能无证据归咎缓存。

### 5.5 为什么真实服务/系统操作仍留用户

自动链路证据明确0真实截图/0真实Provider请求；真实API能力、模型权限和用户屏幕语义无法由本地假服务证明。用户已确认U Setup可用，本轮`.iss`/布局/卸载无改动，未再对真实系统安装目录跑安装卸载。最终Core为`manual_acceptance_only`验收构建，正式签名/信任分发不在本轮授权；不能把它称为正式稳定发布。

## 六、测试与验证

| 门 | 实际命令/证据 | 当前结果 |
|---|---|---|
| 相关 | 目录、外部启动、host/runtime、worker lifecycle、真实HTTP、API/simple/layout族，`related-complete.log` | 96 passed，26.59s |
| 最终全量 | `python $ev/run_full_release.py` → `python -X utf8 -m pytest -q --basetemp=<独立E盘目录>`，offscreen/独立APPDATA | 4507 passed, 15 skipped, 14 warnings in 802.39s (0:13:22)；exit 0，643 个输入前后摘要一致 |
| 受影响17族满负载三遍 | `python $ev/run_highload_release.py`，20个自有低优先级pin-CPU进程，事件ready/自然stop | 第1轮 169 passed in 47.59s、exit 0、CPU 中位 100.0%（含调度49.875s）；第2轮 169 passed in 47.06s、exit 0、CPU 中位 99.75%（含调度48.656s）；第3轮 169 passed in 47.69s、exit 0、CPU 中位 100.0%（含调度49.813s）；三轮全部通过且自有负载进程自然退出 |
| 静态/文档 | Ruff、format、diff、报告纪律/链接 | Ruff、21 个改动 Python format、git diff --check 通过；报告纪律 65 passed，13 份文档 471 个相对链接/尾随空白检查通过；见 closeout-quality.json |
| 冻结/资源 | `build_release.py`、`audit_artifacts_release.py`、`run_frozen_release.py`、两个icon审计 | final Core230源/1010资源，AI29文件/Screen118文件，输入/包清单/hash/自然启动通过 |

17族清单与每轮CPU样本、退出码、源码前后hash见`delivery-highload-release.json`。不强杀测试/用户进程；失败时停止后续门，不修改已失败日志。最终全量固定输入不是测试期间修改代码的第二轮。报告在full采集后新增时会额外运行报告纪律门，避免误称新文档已包含于该次collection。

## 七、交付产物、输入与保留边界

只给用户`_s01b/setup-release/dsh-pet-core-webm-setup.exe`；其余下表用于审计或单包分发，不运行`runtime-release`测试副本作用户安装。Core **4.2.4**、Screen **1.0.3 (>=4.2.4,<6.0.0)**、AI **1.0.3 (>=4.2.3,<6.0.0)**。

| 文件 | 字节 | SHA-256 |
|---|---:|---|
| `_s01b/setup-release/dsh-pet-core-webm-setup.exe` | 249,750,682 | `7282fc05ac0b14138f00d4dbb71edf73af82aee261500f59f2217e8a1756a40d` |
| `_s01b/c-release/dist/dsh-pet-core-webm/dsh-pet-core-webm.exe` | 17,659,890 | `727f7903da2272be0b5219fb8c36b975ca4919324ae1bbec8920227cc5f5a1fe` |
| `_s01b/w/dist/proactive-screen-worker/proactive-screen-worker.exe` | 4,863,864 | `528aa96cf950ba4e93507c3ebfbb765fb83ad0490b391f03c4952caa2fec4480` |
| `_s01b/packages-final/official.ai-chat.zip` | 121,859 | `e73df14c825bb22594bfd2495cc012aa2e5bff1bde0fa9ed91ed94d75d775fb6` |
| `_s01b/packages-final/official.screen-understanding.zip` | 24,463,624 | `ffbd6737839146235d83ed1d1b6a375bb83dbc44e26f599abc3ab341d7cb79ec` |

Setup由现有`build_core_webm_setup.py`编译，输入是最新Core及上表两个ZIP；portable marker为`{"data":"data","format_version":1,"product_id":"dsh-pet-core-webm"}`。原Setup安装/卸载脚本字节未变；无更改其“未勾选保留旧DLC”合同。清单/hash核验针对构建输入及最终PE，未谎称本轮已把Setup安装到用户系统。`final-protection-audit.json`确认旧A Setup/Core/双ZIP和已验收U Setup摘要未变，643个最终full输入仍一致；暂存为空、HEAD未变。新构建根4,653,756,507B（4.334GiB），低于5GiB构建预算；**加上保留的回归测试临时目录/证据合计5,630,779,055B（5.244GiB）**，比5GiB多约250MiB。此处明确分别计量，不把测试证据空间隐藏在构建量之外；用户授权“约5g构建空间”，后续不再扩建，未未经确认清理旧候选/测试数据。

## 八、风险、回滚与人工验收

- 当前最大未证实项是真实Provider/真实屏幕，不是Worker冻结启动；Provider不支持视觉仍需要适用模型/可选视觉Key，HTTP200文字测试不能承诺视觉可用。
- 目录边界更加明确，不把reparse或任意Core目录当runtime；有错误时按固定提示更新包/检查data权限而非关闭全部验证。
- 版本升级不迁移/删除个人配置或旧Key；若需代码回滚，从本轮baseline逐文件审查恢复，不能`reset --hard`覆盖早前dirty。若需候选回退，旧A/U产物保留，但旧A仍有本次已知启动缺陷；不建议为了图标回退业务修复。
- 实机启动51.062s带并发full干扰，仅一个样本，不保证常态启动速度；本轮没有为提速放弃完整性校验。

按以下顺序一次性验收：

1. 自然退出桌宠和设置，用**本报告的release Setup**更新原portable项目目录；保留配置，勾选“屏幕理解”确保Screen从1.0.2变1.0.3。AI可保持1.0.3；不勾选不会自动替换旧包。
2. 在安装目录查看Core EXE和快捷方式是否鲸鱼娘；打开设置，现有主Key显示已配置无需重填；点击测试，观察按钮旁`连接成功 · HTTP 200`或服务实际结果码，备注明确尚未保存。
3. 如想测错误提示，仅在未保存草稿里临时填错Key点测试，观察HTTP401/403等服务实际结果，然后重新加载/放弃草稿，**不要保存错误Key**；超时/网络会显示不同码，不伪造HTTP码。
4. 保持自动识屏关闭、白名单空，点击“看看屏幕”：期望Worker能够启动，不再因项目data被拒绝而“服务已暂停”；真实模型可用时返回识别，不支持视觉时才提示视觉Key/高级项。
5. 如有专用视觉Key，再填并保存，重试；配置后已有聊天窗口新请求仍应可用，余额按支持服务工作；这次本地启动修复不要求重填已有Key。
6. 回报真实结果/必要错误码，不发Key或含隐私屏幕；用户确认前Phase5A不正式关闭。Setup安装/卸载已验收且本轮未改，不要求再次重复破坏性卸载流程。

## 九、本次实际可体验的效果与限制

更新同一项目后配置继续保留，识屏Worker可从项目内data正常启动并可重试；连接按钮旁直接看到联通状态和HTTP/传输结果码，说明留在后面。新Core、Setup与测试快捷方式都实际显示鲸鱼娘。真实服务和你的实际桌面/屏幕仍需最终人工确认；未提交、未推送、未正式发布，Phase5A不因工程完成自动关闭。
