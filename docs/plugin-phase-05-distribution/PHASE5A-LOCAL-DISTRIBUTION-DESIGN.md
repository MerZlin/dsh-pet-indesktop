# Phase5A 本地分发设计与验收

> **2026-10-10 验收补记**：用户确认 Core4.2.4 / AI1.0.3 / Screen1.0.3 当前候选实际体验无问题。先复验并提交当前修复检查点至已确认分支，再实施独立的 MOD 管理中心/公开 v1 教程计划；本设计历史阶段和验证记录保留。

<!-- S04_CURRENT_START -->
## S 修订：项目内 Worker 与反馈（2026-10-10）

**S01–S04 工程交付完成；真实 Provider/屏幕效果与用户安装体验仍待确认，Phase5A 不关闭。** 工程证据：[S 报告](../PR-REPORT-PORTABLE-SCREEN-WORKER-2026-10-10.md)；连续计划与停点：[PLAN](../../.scratch/phase5a-local-distribution/PLAN.md) / [HANDOFF](../../.scratch/phase5a-local-distribution/HANDOFF.md)。

### 合同、不变量与根因

1. portable marker 的 data_root 仍由 RuntimeLayout/租约协调器提供。worker runtime 可以位于 `<core>/data/feature-runtime` 及其子目录；仅在 data_root 恰为该 Core 的 data 且 selection/descriptor 相符时成立。不删除原有 Core/DLC 程序树拒绝，不跟随 reparse，不增加公共 API。
2. 启动前继续验证签名、文件清单、执行授权并预留版本租约；验证失败必须回收 reservation；每次启动重新验证，绝不回退 Core 内执行。host/Worker 环境隔离和 HELLO token 交接保持。
3. diagnostic 的白名单原因先于 FAULT 发布；同步手动入口读取原因，用户主动重试可重新启动。原始 stderr/环境/秘密不作为气泡或 INFO 日志。
4. 运行心跳从 READY 开始；昂贵包验证不消耗运行心跳预算。握手超时、真正 READY 后失去心跳、自然关闭和进程代际仍独立处理。
5. 按钮旁连接状态采用不可变 ProbeResult(reason, http_status)；HTTP200/401/403/429/503 等展示真实码，无 HTTP 响应时明确 TIMEOUT/NETWORK_ERROR/TLS_ERROR，备注解释。测试只用当前草稿，不保存、不截图、不等同视觉或余额测试；Core API/Key 路由、简易主视觉规则不变。
6. Setup 布局/路径/卸载逻辑不改，官方 factory/owner 不特判；鲸鱼娘 ICO 没有换成别的素材，最终 Core/Setup/隔离副本及私有 Shell 快捷方式核验。

### 验收与交付

目录/诊断/重试/心跳/UI 均有 red→green；真实 Qt/进程边界覆盖取消和迟到结果。最终全量：4507 passed, 15 skipped, 14 warnings in 802.39s (0:13:22)；exit 0，643 个输入前后摘要一致。高负载：第1轮 169 passed in 47.59s、exit 0、CPU 中位 100.0%（含调度49.875s）；第2轮 169 passed in 47.06s、exit 0、CPU 中位 99.75%（含调度48.656s）；第3轮 169 passed in 47.69s、exit 0、CPU 中位 100.0%（含调度49.813s）；三轮全部通过且自有负载进程自然退出。最终冻结 Core→真实安装 Screen→生产 Worker→租约 HELLO/READY→自然退出0/0已通过。合成图/本地HTTP请求返回由独立真实进程集成测试覆盖；冻结链路本身不采集屏幕、不请求服务。

Core4.2.4/Screen1.0.3(>=4.2.4)/AI1.0.3，`_s01b/setup-release/dsh-pet-core-webm-setup.exe`，SHA `7282fc05ac0b14138f00d4dbb71edf73af82aee261500f59f2217e8a1756a40d`；候选使用 manual_acceptance_only 验收入口，不是正式信任发布。保留全部旧候选与用户安装，需回滚时按本轮 baseline 逐文件定点审查，不能整树覆盖既有 dirty。

### 完成后的实际使用效果与限制

配置无需重填，手动识屏不再被项目内 data 拒绝；按钮旁直接看到联通与结果码，后面补备注。更新时勾选屏幕理解才替换旧 Screen 包；真实 Provider、真实屏幕与用户实际桌面图标最后验收，Phase5A 不关闭。
<!-- S04_CURRENT_END -->

> 2026-10-09 用户追加：恢复 Core EXE 与 Setup 的既有鲸鱼角色图标。仅构建资源关联，不修改 Setup 安装／卸载行为。以 assets/icon.ico（与旧 standalone EXE 图标帧一致）作为唯一图标输入，并核验冻结 PE 资源。

# A01–A05：恢复旧版简易 API 设置（2026-10-09）

> 用户已确认实施；Setup 安装卸载已获用户确认，本轮不改。取代 R02/R03 面向用户的复杂服务 ID/用途授权合同；历史 R/U 验证保留，不代表本轮验证。

## 目标与合同

恢复旧 API 列表、地址、模型、主 Key、测试连接；只增加可选视觉 Key，视觉地址/模型放折叠高级项。不显示 ID、owner、用途授权或迁移 journal；同一设置不重复挂载。测试只检查当前输入，保存/完成才生效。

主 Key 自动用于文字/文件/余额；视觉 Key 未配置时向主服务发起真实视觉请求，沿用旧模型推导。有专用视觉 Key 才允许向独立视觉地址发送；不把主 Key 转给不同地址。视觉 Key 可清除。鉴权/视觉模型失败提示配置视觉 Key，高级项可改地址模型；网络/限流/截图/Worker 失败单独提示。不自动启用识屏或收费探测。

复用现有安全存储和 FeatureHostContext.api，默认主/视觉选择适配到当前 DLC；不恢复 Core 内 AI/视觉执行或官方 factory 特判。保存后新请求立即生效，在飞快照、撤销/停用取消、草稿保护不退化。已有配置优先聊天主绑定与手动（其次自动）视觉绑定；保存后统一手动/自动视觉，其他配置和旧密钥不删不合并。API Key 不落明文，不读真实用户 Key。

## 顺序与门

- [x] A01：落盘合同、基线输入快照；简易 UI/自动消费/视觉 fallback 失败回归，保存 red。
- [x] A02：简单配置适配与原子安全提交；无 ID/用途表单；连接/保存、旧入口统一。
- [x] A03：主/视觉请求接入、模型推导与分类提示；真实 Qt/进程生命周期回归。
- [x] A04：相关/全量 pytest、Ruff/diff；时序族三轮高负载；真实 GUI/性能；重建受影响 Core/DLC/Worker/Setup 与 hash/启动验证。
- [x] A05：逐文件说明/性能/实机报告与索引、阶段记录；真实 Provider/屏幕人工项列明，不先关闭 Phase5A。

## 风险、保护与恢复

保留既有 dirty 和已验收 Setup；仅新增候选产物。不提交、不推送，不改代理/VPN，不请求真实收费服务。源码快照在 a01-simple-api-20261009/baseline；仅作本轮逐文件差异/定点恢复参考，不能整树覆盖用户修改。失败门保留准确证据，空间不足不删除未知生成物或用户数据；本轮改动不自动闭合 Phase5A。

## 实际使用效果与限制

普通用户填主 Key、测试并保存即可；视觉 Key 可不填，需要独立视觉服务再展开高级项。Core 仍存配置，DLC/Worker 仍执行请求；Setup 不改。真实 Provider、真实屏幕留人工验收。

## 实施与交付更新（2026-10-10）

A01–A05 已实现并完成工程验证：4472p/15s全量、3×185p满CPU、Ruff/格式/diff、原生UI/性能、最终冻结DLC安装/API页与Worker正常启动；原鲸鱼图标10帧一致。逐文件/失败分类/产物hash/人工步骤见[本轮报告](../PR-REPORT-SIMPLE-API-2026-10-09.md)。Setup安装卸载仅图标关联新增，逻辑不变。真实Provider/屏幕与新候选体验待用户确认；Phase5A不提前关闭。

## 完成后的实际使用效果

主Key测试保存即可，视觉Key可选；两EXE鲸鱼图标恢复。网络/模型能力受服务约束，真实识屏不能由Worker握手证明。

---

# Phase 5A：Setup 官方选装与本地第三方 DLC

> **当前权威状态（2026-10-09）**：U01–U06 工程实现、自动化、重建和隔离实机验收完成；待用户确认真实功能体验。U 项目目录合同优先于下方 T/R 历史；当前证据见[本轮报告](../PR-REPORT-SETUP-PROJECT-DIRECTORY-2026-10-09.md)。Phase5A 未正式关闭。
>
> 1. **Setup 内选装**：只提供官方 AI / Screen；官方 ZIP 编译时嵌入 Setup，用户勾选后安装阶段无界面自动 preflight/apply，首次正常 Core 启动完成 receipt。Setup 不依赖同目录 `packages`，不要求公钥、私钥或 `manifest.sig`。
> 2. **Setup 外导入**：用户明确选择 ZIP/目录；程序只预读受限大小的 `manifest.json`，按 `id` / `factory` / `execution_kind` 自动路由。符合本地包格式、Core/API/平台兼容和固定 `host/factory.py:create_host`/Worker 合同的未知 owner，也作为第三方 DLC 接受。
> 3. **信任边界**：显式选择意味着用户信任包内代码；host/Worker Python 可执行且不是沙箱。发布者签名、在线目录、撤销、公钥轮换和社区审核不属于本轮。工程正确性边界（路径、重解析点、大小/数量、清单 SHA-256、事务、租约、启动确认）不能删除。
> 4. **Core 产物**：主桌宠 EXE 使用 GUI subsystem，不显示终端；Worker、诊断和维护入口按用途单独决定窗口策略。
>
> 导航：[Phase 5 README](README.md) · [详细实现计划](../../docs/PHASE5A-CLOSEOUT-IMPLEMENTATION-PLAN-2026-10-07.md) · [代码交付](../../docs/PHASE5A-CODE-IMPLEMENTATION-HANDOFF-2026-10-07.md) · [文档索引](../INDEX.md) · [阶段记录](../../.scratch/phase5a-local-distribution/STATUS.md)。下方旧日期章节保留作为审计历史，不能作为新合同已实现的证明。

## 1. 目标、范围与事实

交付统一小 Core、Setup 可选的两个功能包、本地旁置包、普通 ZIP/目录激活、NTFS 便携，以及更新、卸载和重装闭环。AI 拆包计入 5B-1，不宣布其余 5B 完成。已有 4B 事务、LPAC probe、状态账本和租约是复用基线；UX-M3 的正常 Core 启动加载确认仍需公开 seam 回归证明，旧普通设置通过不能代替 Core 证据。

- 新 WebM 产品 `dsh-pet-core-webm` 不含 AI 或识屏实现；旧 chat/no-chat 构建及人工验收目录保持不动。
- 官方身份固定 `official.ai-chat` 与 `official.screen-understanding`，不动态发现第三方代码。
- AI 保留现代/旧式聊天、快捷对话、灵动岛接入、Provider/流式、会话/附件和现有文本/代码文件理解；不新增 PDF、通用二进制或远程工具。
- AI 保留 QThread；独立 AI Worker 不作前置，但取消、排空、迟到响应和退出安全必验。
- Windows 是交付平台，其他平台明确未验收；远程下载、Workshop、第三方市场、其他领域拆包、正式发布不在范围。
- 实现、自动化、本机安装、人工确认、干净环境、提交、远程验证、发布分别记录，未测试不等于失败。

## 2. 权威状态与接口

两包各自 state.json / revision / journal / leases 是唯一权威。状态存储、事务、resolver、生命周期、启动确认和管理参数化 feature_id，默认屏幕身份保持兼容；结果路由绑定包。保留八种 OperationResult。不同包独立完成，不引入跨包原子事务。

manifest 仍是闭合 schema；屏幕为 host-worker，AI 为 host-only 且 worker=null；不伪造 Worker 成功。Core-owned 注册表约束 ID、factory、执行类型和能力上限；AI 无截图/桌面能力。Phase5A 本地路径可读取 v1/v2，但不把 `key_id` 当作信任来源，也不查找或验证发布者密钥；正常 verifier 和原生 LPAC verifier 共享同一结构/兼容性/完整性合同。

preflight 绝不执行候选；确认后完整复验、沙箱自检、生命周期准备、pending/落盘/切换、正常 Core/设置真实 receipt、清除 pending。管理页不能提交加载成功。AI probe 只验证惰性 factory，不创建窗口/线程/请求。

## 3. AI 拆包与生命周期

UI、请求、Provider、专属设置、会话策略、快捷/灵动岛聊天接入、文件理解归 AI 包。Core 仅通用贡献、服务、窗口承载、网络工具、安全存储；余额等领域不得依赖 Provider 实现。chat.external-turn/v1 为可选文字联动，任一包单独可用。

停用/卸载：拒绝新任务 → 失效 generation → 撤销贡献 → 取消流式响应 → 排空会话写入。GUI 不等待后台线程，不 terminate QThread；已导入 host 仍持租约等待自然退出。未安装时 Core 保留不解释的 AI 数据，重装恢复。设置草稿不自动保存/丢弃。

## 4. RuntimeLayout、便携与迁移

普通 Setup/ZIP 使用 %APPDATA%/dsh-pet-core-webm；显式便携以 exe 旁 portable.json 指定固定相对 data 目录。所有配置/资源/锁/IPC/设置/包服务初始化先统一解析。异常标记、链接/逃逸、只读、不支持文件系统报错，不暗中降级 APPDATA。首版 NTFS，不支持 exFAT/FAT。

稳定 data_root_id + instance + feature owner 区分 OS 凭据；锁/IPC 同时绑定规范化实际路径。相同 Windows 用户/机器移动后保留凭据；跨机器/用户重新授权，秘密不写 ZIP/备份/便携。系统管理的凭据与 AppContainer 元数据并非全随 E 盘业务目录移动。

旧数据仅用户明确选择一个来源，先展示映射再确认；source fingerprint、目标 CAS、备份/中断恢复、重复重放、实例/角色/Provider/session IDs、未完成写入均验。目标新编辑不能覆盖，原目录不能删除。凭据经授权的 OS 存储迁移引用，不枚举或备份秘密。安装代码不收编，官方包重新安装。托管相对附件迁移，外部路径失效提示且不复制根外文件。

## 5. 本地激活与构建边界

Phase5A 构建：helper/Worker 最终文件 → manifest 文件清单与 SHA-256 → probe bundle digest → 普通 ZIP/目录 → Setup 可选任务或扩展管理页导入。`scripts/build_feature_management_manual.py`、`scripts/build_feature_management_delivery.py` 和 `scripts/build_screen_delivery.py` 的 Phase5A 主路径不生成密钥、不写 `manifest.sig`，也不把签名作为构建硬门。

本地激活是“用户明确选择来源后由 Core 负责结构与完整性校验”的信任边界，不是发布者认证，也不是 Python 沙箱。Core 不从任意目录自动猜状态；用户选择 ZIP/目录后，事务服务解压/复制到固定版本目录并通过预检、确认、状态账本和启动自检。损坏、越界路径、错误 feature/factory、能力/版本不兼容、文件清单变更和 probe 失败必须拒绝。

正式发布如未来需要发布者身份认证，可独立启用既有 Ed25519 工具和策略；那条路线不应回写为 Phase5A 的必要条件，不应要求用户接触 `E:/AI/DSH/release-signing/` 私钥。

## 6. 分发、管理、更新与卸载

保留一套 Setup、普通ZIP、便携ZIP、两个DLC ZIP和精简证据。Setup当前用户安装，两组件默认不选，从旁置 packages 离线预检；缺包/单包失败不阻止Core、不下载。Setup传意图，Core-owned管理入口生成摘要并确认/事务；各包独立结果、pending不冒充成功。

管理仍在常规域，两个包各自状态/操作，贡献按owner生命周期。新产品不沿用旧组件选择；静默更新Core-only不复装卸载包。新路线禁自动关进程，文件替换须占用门；自然退出重试。便携不自动运行Setup。

Core卸载先逐包真实卸载，全部代码清理后删Core；占用/部分删除/恢复未完成阻止Core删除，保留恢复helper。已接受单包卸载不因整个卸载取消而复活。个人配置/profile/凭据/记忆/额度/聊天历史始终保留。未知文件不自动收编或越界删。

## 7. 实施顺序与验收

|编号|工作与出口|
|---|---|
|5A-0|留档、基线/保护范围；初始6GiB，2026-10-05追加授权8GiB预算|
|5A-1|双包事务、v2/host-only、receipt、UX-M3与屏幕回归|
|5A-2|RuntimeLayout、稳定凭据、NTFS便携、显式导入/恢复|
|5B-1|AI全拆包、服务路由、线程排空、无AI Core审计|
|5A-3|本地包构建/ZIP与目录导入、真实 LPAC 与完整性验收（不以签名为门）|
|5A-4|旁置Setup/Core-only更新/Core卸载事务|
|5A-5|双包UI/导入确认、布局/可访问性/异步安全|
|5A-6|真实冻结Core四组合、ZIP/便携/迁移、用户/干净环境|
|5A-CLOSE|全量质量门、性能/体积/报告/交接|

每切片先公开 seam 红测试再绿。矩阵包含错误 schema/路径/文件清单/版本能力、四种安装组合、独立启停/升级/回滚/卸载/重装、源变化、跨revision、多个Core/settings/Worker占用、草稿、异步取消/排空、实际Core加载失败/previous真实确认、journal/state/rename/receipt/delete/GC前后故障、重复恢复和孤立目录不复活。Setup含缺包/损坏/离线/中文空格路径/文件占用；更新不复装。便携同/跨NTFS卷、marker/只读/reparse、用户凭据及导入冲突/中断/附件。真实LPAC只访问生成canary；磁盘/EDR安全注入与实机故障分开。

专项/相关→全量pytest、Ruff、format-check、受影响mypy、文档链接/报告纪律/diff-check；相关Qt/进程CPU高负载三遍，事件同步宽预算。启动>=10、预检>=20、probe>=10，状态改变每类>=10样本；记录环境/版本/命令/median/p95/RSS/线程/磁盘网络成本及各产物下载/解压体积。

终端操作由代理准备执行，真实模型/界面及新Windows用户或另一机器由用户提供环境确认。独立测试目录不冒充全新Windows。操作后一次汇报步骤，不异步等待聊天。

## 8. 风险、保护、回滚与留档

基线白名单快照保留原文件，禁止reset--hard/强推/覆盖用户改动；本轮不提交/推送/发布/子智能体。普通故障读取日志缩小复现再修；连续两轮同族失败回溯假设。不能绕过结构/完整性/LPAC、强退合法进程、热替换host、猜状态或删除边界。敏感操作前展示目标影响并确认。

本轮生成峰值<=8GiB（2026-10-05用户追加授权；初始6GiB为历史），Core预留2GiB，构建前估算；仅清理由本轮创建且安全边界已核验目录，不动phase4b manual-core/manual-session、真实profile和密钥。超预算先报告，不扩大清理范围。

同一设计及PLAN/HANDOFF/STATUS/WORKLOG/SUMMARY持续更新，历史有日期版本；完成报告逐文件±行数、数字性能、实机证据并登记INDEX。尚未完成不能标工程完成。无发布授权。

## 完成后的实际使用效果

只装桌宠或在 Setup 中离线选装 AI/识屏，两个包独立管理；离开 Setup 后也可选择普通 ZIP/目录，不要求公钥私钥；不安装没有相关实现和后台请求。便携普通数据随NTFS目录迁移，同用户同机器保留OS凭据访问；换机器授权。卸载Core清理代码保留个人数据。当前文件只是已批准实施合同，不是安装指引或验收通过证明；其余5B、远程分发、其他平台和发布仍独立推进。


## 生产构建前置实证 2026-10-05T01:50:31+08:00（本机时间）

可信头部与native叶子模块采用固定PyInstaller 6.20.0源码摘要、独占工作目录和构建来源receipt；最终helper/Worker先审计实际PYZ与PE依赖，之后才生成bundle清单。缺失公共SDK不能用扩大沙箱权限修补。两包实际LPAC自检已经通过，使用仅内存的临时集成签名，不等于正式信任锚/生产Core/本地分发门通过。预算、版本与完整命令输出见同组WORKLOG及64号新helper权限复验；正式密钥仍需再次目标确认。


## Core 文件替换与卸载前置门补充（UTC 2026-10-04T18:40:42+00:00）

新产品正常 Core、设置和本地安装意图入口先持有 `.core-files.lock` 的共享内核锁，再初始化 RuntimeLayout。安装器以原生 Win32 取得同文件 offset=0/length=1 的独占锁，在替换文件和 Core 删除期间保持；锁失败只允许自然退出重试，不终止进程。便携目标、reparse 和硬链接边界不满足时拒绝 Setup。受限 `--core-maintenance uninstall` 只允许可信新冻结 Core，不启动正常 Core/设置/Worker，使用真实双包事务及异步用户确认；只有账本清空且代码/暂存/租约目录没有未知残留时返回成功。各包独立，不承诺跨包回滚；退出窗口不能复活已接受卸载。维护入口不自行删除 Core，其返回值仅供仍持有独占文件锁的卸载器前置门消费。

旁置包选择仅提交固定官方身份和本地 packages 根，分别从唯一账本选择 install/upgrade，再走实际管理后台预检和不可变确认。未确认不能执行 factory/Worker/请求线程；缺包或单包失败不影响 Core 安装，不宣称 pending 包可用。生成编译夹具和原生锁互验与真实产品安装、正式签名验收分开记录。


## 持续卸载边界补充（UTC 2026-10-04T19:07:11+00:00，实施中）

单个 Core 安装目录的独占文件锁不足以阻止共享 APPDATA 的另一份 Core 在 DLC 清理回执后重新安装。卸载器除 Core 代码锁外，必须在维护入口开始前获取数据根级独占卸载门，直至 Core 删除结束才释放；正常新 Core 和独立设置启动前持有该根共享门。维护入口只在精确冻结卸载路由中豁免共享门，不豁免数据导入 pending 或唯一账本/租约规则。该门不是第二安装权威，不能替代包事务或强退用户进程。尚待公开 seam/原生跨进程回归，不能先标完成。

入口隔离事故已在同一组任务记录登记：新产品直接 Config 也必须禁止隐式旧数据迁移；新冻结 Core 没有布局时启动拒绝，不走 legacy fallback。所有验收与回归数据根必须显式位于本轮拥有根；意外真实 APPDATA 复制目录待用户单独处理，不能擅自删除。

## 实施合同补充（UTC 2026-10-04T19:50:29+00:00）

### 固定卸载根与跨副本屏障
安装器持有固定 Shell APPDATA 数据根独占门直至 Core 文件删除结束；正常 Core/设置取得共享门。闭合维护入口不能使用环境变量选择另一根：必须验证固定根、非便携、既存父端独占门，否则普通数据 I/O 前拒绝。无强退进程，不扩大清理范围。

### 显式凭据引用迁移
预检仅检查配置结构、来源 UUID、实例、owner、profile、endpoint 和非秘密引用；不读取安全存储。确认摘要绑定来源身份、原始文件摘要、净化快照摘要及映射。只有接受后持有数据根独占访问门，才逐项迁移明确绑定的引用；秘密仅在内存经过已注册 owner vault，目标是稳定 data_root_id 分区，不写快照/日志/备份。来源保持不变，目标新编辑拒绝覆盖，中断可幂等恢复。Core 余额不依赖 AI Provider 实现。未知 schema/明文秘密拒绝；这些自动化夹具通过不等于真实用户导入验收。

### 当前交付边界
上述 seam 有自动化证据，导入实际 UI/附件迁移、正式签名、生产产物、Setup、最终性能与人工/干净环境仍待完成；full108 为运行中修改形成的混合版本结果，不作最终门。red91 数据隔离事故保留在任务记录，不以之后通过抹去。


## 显式导入入口、清理与锁合同补充（UTC 2026-10-04T20:32:05+00:00）

- 专用 `--import-local-data` 仅在新冻结产品、互斥参数校验后启动，不初始化普通 Config 或功能执行；父端固定自身 executable 与唯一命令，不提供任意参数/路径执行口。普通设置在既有常规域提供命令及 `data-import` 深链，不新增持久化开关。
- `data-import/management.lock` 序列化接受、恢复与未接受清理；接受/恢复再持独占 `data-access.lock`。普通 Core/设置只 pin 数据访问与 Core-removal 屏障，不阻止未接受快照取消。管理锁竞争的取消重试仍然是取消，不可重新消费旧确认。
- 凭据授权默认关闭；授权描述单列自适应换行，不让720px/放大字体的单行复选框横向溢出。实际异步操作由应用拥有，窗口销毁后无失效 QObject 调用，正常 Quit 非阻塞等待 drain；不能宣称任意外部强制终止同样安全。
- 当前 JSON 配置与会话迁移、明确凭据引用适配、UI 与入口已有专项证据，正式生产与全数据/附件迁移未验收。以任务记录为真实停点，历史 green 不代替新的累计门。

### 当前可见效果与限制
常规设置的数据交付入口能打开独立确认工具；预检、映射和凭据授权均显式，取消不会接受导入。尚未交付新的正式冻结产物，不让用户用测试锚或本轮事故目录作生产验收；正式密钥、真实导入/安装/卸载均另行确认。


## 当前工程证据（UTC 2026-10-04T20:54:56+00:00；尚未完整交付）

固定源码全量134为4171 passed/14 skipped/14 warnings；真实CPU满载136 Qt/IPC/进程族三遍各178 passed，负载进程已回收；Ruff/format139个Python文件、受影响mypy56文件通过。源码导入性能与逐文件说明见[实施证据报告](../PR-REPORT-PHASE5A-LOCAL-DISTRIBUTION-2026-10-04.md)，不能代替正式冻结性能。

正式key/生产Core/两包/Setup/ZIP和完整冻结矩阵尚未执行；自定义媒体/托管附件/外部附件提示、新产品自启动清理仍需补齐。真实导入、安装卸载、用户人工和干净环境分别记录，不能用Phase4B历史回执替代。red91隔离事故和原生network canary初始化阶段限制在报告/任务记录中保留，不因最新全量通过抹去。

### 当前实际效果与限制
已有源码可在生成夹具中管理两个包并显式迁入明确普通数据与已授权凭据引用；未交付新正式安装包。正式密钥创建前再次确认仓库外目标，密码在本地可信界面输入；之后完成剩余功能、生产构建和新的人工验收，不改变本计划安全/数据保留合同。


## 当前停点：正式密钥创建已核对（UTC 2026-10-05T01:14:39.201695+00:00）

- 用户本轮回复“已输入”，已完成之前授权的本地密码操作。可信CLI公开成功记录与正式公开策略一致，诊断日志0字节；私钥文件302B、创建UTC 2026-10-05T01:09:19.881520+00:00。仅检查私钥元数据，没有读取/展示私钥内容。
- key_id `official-release-2026`，公钥指纹 `dd18cbd51d2c2367e45efe7ec697a5e27e9b1654e54137f5f2734eec80f4d9ab`；公开策略限定两个官方owner及现有能力，未撤销。证据：`signing-launch-142/key-creation-receipt.json`。脱离式启动没有捕获退出码，不能补写退出0；可信CLI仅在两个文件成功写入后输出该成功记录。
- 正式加密密钥/公开政策位于仓库外 `E:/AI/DSH/release-signing/`；备份副本、解密/恢复检查、正式签包/冻结Core/分发验收尚未执行。创建授权不延伸到真实数据导入、安装卸载或发布。
- 下一步先补生产PYZ必须包含导入/维护真实入口的失败回归，以及LPAC网络canary必须实际抵达Winsock连接调用的证据，保留安全边界；随后继续剩余资源导入、新产品自启动清理和正式构建。
- Phase5A/5B-1仍未完整交付。既有full134/负载136为修改前源码历史；无提交/推送/发布/子智能体。

### 当前实际效果与限制
密码操作已完成，本轮不需要在聊天提供任何秘密；尚不能把新小Core视为正式交付。以下密码等待状态为当时的历史事实，不是当前状态。

---



## 2026-10-05 最新工程门与交付限制

最新稳定full234 4258 passed /14 skipped /14 warnings /861.35s，源码哈希无执行中修改；真满载233三遍各227 passed，CPU median/p95 100%。56个受影响Core/构建源和AI host全部26源mypy通过；卸载窗scroll接口已公开seam红绿。正式两包、新helper05和原生矩阵已通过阶段门。

Core02仍落后于四处Core源且带旧helper，必须Core03重建；最新空间237余570,548,000B，不足2GiB预留。213清理没有执行，不能绕过工具拒绝或牺牲安全门。Core03、最终冻结业务/Setup安装/总签名/性能/人工/干净环境未完成，不以历史样本填绿。

实际效果：源码稳定性已获得累计证据；用户仍未拿到最终Phase5A新交付，旧Phase4B运行目录和真实数据保持受保护。

## 2026-10-05 实施补充：probe材料有界恢复（待实现，UTC 2026-10-05T05:22:53.605378+00:00）

OPS-PROBE-MATERIALS / Status: ready-for-agent。当前adapter在生产`config.dir / feature-probe-runs`创建host/worker独立运行材料，正常launcher已清理Job/profile/句柄，但文件快照仍保留；唯一事务GC只处理退役versions，`cleanup_owned_probe`没有生产接入。正式成功自检225留下材料是可复验的现象。不能据此推断越权或声称生产崩溃profile泄漏已经发生。

下一切片必须先补公开seam负向/恢复测试，再实现明确拥有材料的有界回收：只信父端持久拥有记录、绑定根/helper/候选摘要和原生进程身份；活进程/证据冲突不删除，未知目录不收编，链接/reparse/hardlink不越界清理。丢失或失败留诊断和恢复证据，不偷偷扩大范围。生产恢复需接入现有profile恢复合同，不能绕过原生LPAC门、强退合法进程或对整个data根做递归清扫。真实Windows释放/失败与性能测量分别记录；源逻辑改变后重新全量、高负载和最终冻结审计。

243补充空白门先红；245只修源QSS空EOF与Setup消息显式空格，246专项/相关和文本门通过，Python源码与full234一致。签名AI材料不修改，最终AI因源QSS哈希变化须重新生成/签名；旧签包只作为历史候选。空间247及241八项清理尚未执行，最终工程与用户分发验收仍未完成。

### 实际使用效果与限制
用户数据与现有运行不受这次文本修正影响；重复自检的文件回收仍要补齐，不能把该待办或旧候选当最终Phase5A交付。

## 2026-10-05 空间预算调整（用户当前明确授权；2026-10-05T12:34:02.262Z）

用户允许适当增加生成物预算。本轮当前上限由6GiB提高至**8GiB（8,589,934,592B）**，保留Core构建**2GiB（2,147,483,648B）**预留；E盘实测余216,251,744,256B。6GiB仍是此前命令/历史结果的实际上限，不改写历史。

不扩大清理权限：254八项及213被工具拒绝的删除不重试、不绕过；旧人工验收目录、真实数据、凭据和密钥不动。新构建/复制前重新测量并估算，预计超出8GiB先报告；不提交、推送、发布或使用子智能体。

267专项已通过95 passed /2 skipped /84.04s，5个受影响源mypy通过。此前269启动命令尚未产生脚本或日志，属执行工具未送达，不能算原生验证成功；改用新的明确拥有脚本270，不复用同一产物路径，也不尝试受拒绝删除。

## 2026-10-05 已实现增量：生产 probe 材料回收合同

- 新材料按官方owner分区，父端在复制/启动前写入 intent，绑定随机attempt、候选摘要、helper摘要和源/父事务诊断。每根最多4个未释放attempt、8条精简回执、64KiB journal；源路径仅诊断，不作删除权威。
- 专用内核maintenance锁覆盖材料复制、原生启动和恢复；不占用management/state锁，不在Qt GUI线程进行哈希/进程等待/删除。每次host/Worker分别拥有只读运行副本与scratch。
- 原生父端关闭自有Job后检查进程退出，持久化实际process_released；恢复再次核对PID/creation identity。缺PID且不能证明释放、活进程、ACL/链接/reparse/hardlink/未知节点/证据冲突均保留，不能仅采信cleaned标签或子进程自报。
- 仅在全部拥有证据通过后提交deleting intent，再删除本attempt目录；部分删除可重放而不要求已删除helper重新验签。未知目录不收编、不扫除；先前被工具拒绝的清理目标不属此机制。
- GC清单按两套candidate+helper有界聚合，候选执行验证上限不变。恢复失败与材料警告独立展示，不改变包state/enabled、不把已完成安装误标失败；重试仍安全，不强退进程。
- 生产初始化queued启动专用后台恢复；recover/gc复用同一入口，回执以queued信号回GUI。关闭窗口后不访问失效QObject，也不取消已接受事务。

公开回归256/260/266分别先红，267转绿95 passed /2 skipped /84.04s及5源mypy；真实正式双包270通过LPAC、优雅退出和文件回收（AI3.126s，screen17.497s，各n=1）。十次基准271进行中；最新累计全量/冻结Core尚未验证，不以此宣称分发完成。

### 当前实际效果与限制
源码安装自检结束会安全回收自己刚创建的材料，启动和安全重试可恢复中断记录；活进程和不明目录仍保留并解释原因。该实现尚未进入用户运行的旧Core，最终Core03正在准备。

## 2026-10-05 五产物总签名与空间门补充

- 用户追加的是本轮生成物总预算 **8GiB（8,589,934,592B）**，不扩大删除授权，不取消2GiB Core预构建预留。`release_distribution.MAX_TOTAL_BYTES` 的每组分发清单6GiB安全限制保持不变；本组五产物767,710,064B同时满足两者。
- 唯一当前签名候选是本组记录目录下的 `delivery-set-293`：五个exe/zip加 `distribution.json` 与 `distribution.sig`，无子目录、公钥、README或额外证据；证据放在签名根外。294用既有加密正式密钥签名，296从已核对的外部政策独立核验，禁止同包附带公钥自证。
- 真实Setup旁置选包仍按现有安装器合同使用安装器旁独立 `packages`。验收时先验证上述签名候选，再将同一DLC字节放入另一处已确认、受预算保护的Setup验收根；不得往平面签名根加目录，也不得未经确认安装。当前只有编译证据。
- 296的no-follow快照是8,374,833,244B /136,746常规文件，36链接/reparse剪枝，余215,101,348B（205.137MiB）。这是已保留生成物的测量，不是持续采样的构建峰值；后续解压、复制、构建都须另估算，不能自动突破8GiB。
- 213/254工具拒绝删除释放0B，不换工具或放大路径绕过。旧人工验收产物、真实profile/凭据、事故AppData根和外部密钥继续保护。

### 当前实际效果与限制

本次空间阻挡已经解除，可核验新的小Core、两种ZIP、两个正式选装包及总分发签名；尚不能以产物存在证明安装、用户体验、干净环境或正式发布完成。原桌宠、旧数据与旧密钥保持不动。

## 2026-10-06 无人值守剩余验收补充

沿用 Phase5A／5B-1 合同。用户授权先独立验收和修复，将人工／敏感操作留到最终集中汇报。新生成物合计临时上限12 GiB（跨盘计量），不扩大旧目录清理；Core 2 GiB／probe 512 MiB 预留不变。步骤为V0身份与白名单、V1仅AI菜单自然退出、V2四组合事务、V3生产审计与签名待人工、V4 Setup生成边界及新账户门、V5便携／生成导入、V6质量性能。原正式密钥和产物不覆盖；无提交／推送／发布／子智能体。真实用户数据导入、安装卸载及密钥备份具体目标仍需确认。完成效果为可验证本地交付闭环；尚未执行的人工、干净系统及发布门不能写为通过。

## 2026-10-06 实现证据与暂停边界

用户要求先总结交接，未开始任务暂停。长/短路径新helper/Worker真实LPAC自检与完整权限canary通过；实现只扩展同一密封本地runtime路径，使用固定Python3.11三条已有路径，不放宽安全限制或引入源码回退。正常Worker租约/业务与分号等特殊路径分隔合同仍需验证；probe通过不能替代生产门。Core04完成正式公钥策略构建及审计，最新正式DLC/分发未重新签名；无提交/推送/发布。详见[同一实施报告](../PR-REPORT-PHASE5A-LOCAL-DISTRIBUTION-2026-10-04.md)及既有任务HANDOFF。原数据/秘密及旧验收目录继续保护，后续人工门集中准备，不在本次暂停时要求操作。


## 2026-10-06 实现注记：生产 probe 短根合同已落地

新生产 Core 的冻结验收发现，若验收驱动覆盖 `HOME`/`USERPROFILE`/临时目录，probe helper 复制到过深路径会触发 PyInstaller legacy bootloader 的 embedded PKG 路径限制。此问题不是安全门可降级的理由：管理服务已经正确拒绝候选，factory 与 Worker 未执行。

现行实现由可信父端使用 Win32 `SHGetKnownFolderPath(FOLDERID_LocalAppData)` 获取稳定短根，在 `dshpet-probes/<data-root-identity>/<feature-id>` 下建立本次拥有的材料目录；短路径仍使用数据根下的 feature-probe-runs，无法取得 known folder 时 fail closed。材料目录继续绑定 owner/operation/摘要并经过既有 cleanup/恢复合同；不修改真实 profile、账本或用户凭据，不采用普通同用户 subprocess、源码回退或放宽 `PYTHONPATH`。

2026-10-06 本机证据：修复回归 `20 passed`；重新构建 `core-05-final-20261006` 后，AI 与 screen 真实冻结验收均 `CASE_PASSED`，LPAC/production receipt/natural exit 通过。该注记只更新工程实现事实，不把 Setup、便携、旧数据导入、干净环境、人工体验、Authenticode 或正式发布写为已完成。

## 2026-10-06 用户人工验收：安装后真实 Core 启动确认

时间：2026-10-06 22:29:19 +08:00（Windows 本机，用户人工确认）

- 受控人工根：$root\manual-user-acceptance-20261006-v2。
- 用户关闭本地官方包确认窗口后，续接器仅等待安装进程自然退出，再启动正常生产 Core；没有强制结束进程、没有提交伪造 receipt，也没有放宽隔离策略。
- 用户可见结果：设置中的 official.ai-chat 与 official.screen-understanding 均正常；两个入口恢复；原设置保留；未出现错误或恢复提示。
- 账本复核：AI ctive=1.0.1、nabled=true、
evision=4、pending_transaction=null；屏幕理解 ctive=1.0.0、nabled=true、
evision=4、pending_transaction=null。
- 事务复核：两笔安装事务均为 phase=completed、ccepted=true、self_check_passed=true；生产 Core 当前由同一受控数据根运行（PID 31308，复核时仍在运行）。
- 本条只证明本机用户人工完成了“本地 ZIP 安装 → 确认 → 沙箱自检 → 状态切换 → 正常 Core 启动加载确认 → 入口/设置恢复”门；不替代真实模型请求、真实截图识别、Setup/便携/更新/卸载、干净环境或正式发布者认证门。

## 2026-10-06 用户人工验收补充：冻结 Core 凭据边界

时间：2026-10-06 22:56:43 +08:00（Windows 本机）

- 用户反馈：关闭刚才运行的冻结 Core 后，源码入口可以执行 AI 操作；这不能直接证明冻结 Core 失败，必须以冻结 Core 自己的日志和账本为准。
- 冻结 Core 的受控人工根为 manual-user-acceptance-20261006-v2，启动日志记录的实际异常为 pet.credentials.CredentialError: credential_missing。日志没有暴露任何凭据内容。
- 同一根目录的安装/加载证据仍完整：AI ctive=1.0.1、屏幕理解 ctive=1.0.0，均 nabled=true、pending_transaction=null；两笔事务均 phase=completed、self_check_passed=true。
- 结论修正：冻结 Core 的“安装、自检、状态切换、正常启动加载确认”通过；“使用真实 Provider 凭据完成 AI 请求”尚未通过。源码使用真实数据根时成功，不能替代冻结 Core 的独立验收。
- 不把真实用户凭据复制到验收根，不修改冻结 Core 的 credential resolver，不把 credential_missing 降级为成功。后续如继续人工门，只能由用户在冻结 Core 的隔离设置中通过本地可信界面自行录入测试凭据，或使用明确的本地模拟 Provider；凭据不得通过聊天、命令行或日志传递。


## 2026-10-06 屏幕理解独立配置提示修正

### 结论

本次人工反馈不是迁移事务未完成。受控验收根中的 `official.screen-understanding` 已有独立 profile，`migration_state` 为 `confirmed`，但该 profile 的 `credential_ref` 为空；`VisionConfigService.resolve()` 因此返回 `credential_missing`。AI 包的凭据不复用于屏幕理解，这是两个功能包独立信任与配置边界的既定合同。

此前设置页把“已有 profile”统一显示为“已有独立配置；凭据在执行时校验”，手动识屏失败又统一提示“配置或确认迁移”，导致用户误以为需要重复迁移。

### 本次实现

- `features/screen_understanding/host/manual.py` 按 `credential_missing`、`credential_unavailable`、`configuration_invalid` 显示原因化提示；缺少 Key 时明确写“已有独立配置，无需重复迁移”。
- `features/screen_understanding/host/settings.py` 根据当前用途绑定、profile 和 `credential_ref` 分层显示状态；空凭据不再伪装成“执行时校验”，而是要求补填视觉 API Key。
- `tests/test_screen_manual_host.py`、`tests/test_screen_settings.py` 增加公开 seam 回归，覆盖手动识屏提示和已迁移但未绑定凭据的设置状态。
- 新增隔离人工验收夹具：`.scratch/phase5a-local-distribution/manual-user-acceptance-20261006-v4-screen-ux/screen-config-fixture.json`。夹具只包含脱敏的已确认配置和空 `credential_ref`，不含任何秘密；安装脚本仅在隔离根没有配置时复制，不覆盖之后用户录入的配置。

### 验证

- 受影响专项：`python -m pytest -q tests/test_screen_manual_host.py tests/test_screen_settings.py tests/test_screen_configuration.py tests/test_screen_runtime.py` → `53 passed in 16.48s`。
- Ruff：四个源码/测试文件 `python -m ruff check` 通过，`python -m ruff format --check` 报告 4 files already formatted。
- PowerShell 安装脚本解析通过；脱敏 JSON fixture 解析及迁移/绑定/空凭据合同断言通过。
- 新人工验收构建：Worker `worker-03-screen-ux-20261006`，Core `core-06-screen-ux-20261006`；两个 Core 变体均标记 `manual_acceptance_only=true`、`release=false`，不能冒充正式发布。no-chat 产物 `459,275,741` bytes、构建 `106.968918s`；chat 产物 `461,518,673` bytes、构建 `80.814419s`。旧 `core-05-final-20261006` 与 v2 人工验收根保持不动。
- 新包 ZIP 已验证包含修正后的 `host/manual.py` 与 `host/settings.py` 文案。
- 这次全量回归结果不能标为通过：`4298 passed, 14 skipped, 14 warnings, 6 failed`；6 个失败均可在 `tests/test_drag_move_coalescing.py` 单独复现，属于窗口边界夹紧与测试期望不一致的既有环境/测试族问题，不改动该无关路径。日志：`.scratch/phase5a-local-distribution/pytest-full-screen-ux-20261006.log`、`.scratch/phase5a-local-distribution/pytest-drag-recheck-20261006.log`。

### 未完成人工门

用户仍需在新 v4 隔离根中通过设置页自行输入屏幕视觉 API Key，并触发一次真实屏幕请求。凭据不得通过聊天、命令行或日志传递；这一步不能由自动化夹具代替。


## 2026-10-06 最终复核：屏幕 UX 验收包证据

- 使用 Python `zipfile` 直接读取 4 个 screen 包 ZIP，均确认包含修正后的 `host/manual.py` 与 `host/settings.py`，并确认包内含“补填视觉 API Key”“无需重复迁移”文案；未依赖未安装的 `7z` 命令。
- `screen-config-fixture.json` 的原始字节无 UTF-8 BOM，JSON 合同断言通过；不会因 PowerShell 编码导致产品配置读取失败。
- v4 安装脚本 PowerShell AST 解析通过，fixture 只在配置不存在时复制；不会覆盖用户后来在隔离 UI 中保存的凭据引用。
- 受影响源码/测试范围的 `git diff --check` 通过。整个 WIP 工作树仍有历史任务记录中的尾随空白告警，未进行大范围格式化，避免改写既有证据。
- 尚未代替用户输入真实 screen Key 或发起真实 screen 请求；这仍是唯一需要人工执行的本次修复验收门。


## 2026-10-06 产品决策修正：屏幕理解首装使用默认配置，不主动迁移旧配置

用户明确修正了本阶段的屏幕理解产品行为：首次安装或不存在有效 screen 自有配置时，直接准备无凭据的默认独立视觉配置；不要主动读取 AI 对话配置、不要显示“迁移/补齐”操作，也不要把迁移失败变成首装阻塞。用户后续可在屏幕理解设置中自行修改地址、模型、提示词和凭据。

兼容边界：如果已有有效的 `official.screen-understanding` 自有配置，则正常加载并保留；这不是跨包迁移。显式旧数据导入能力仍只作为内部兼容代码保留，不由首装、启动或管理界面自动调用。AI 包与屏幕理解继续使用各自安全存储，屏幕理解没有凭据时保持不可执行并给出手动配置提示。

实现证据：
- `features/screen_understanding/common/models.py` 新增无凭据 `VisionSettings.default()`，默认 profile 为 `shared`，默认 DeepSeek 兼容地址/模型/请求路径和基础提示词。
- `features/screen_understanding/host/settings.py` 首次打开直接展示默认 profile，移除迁移/迁移清理按钮和 AI 配置复制文案；保存仍是唯一写入凭据的动作。
- `features/screen_understanding/host/manual.py` 缺凭据提示改为要求用户在独立屏幕理解设置中填写并保存，不再引导迁移。
- `tests/test_screen_settings.py` 增加默认配置、不读取/复制 AI 配置、无迁移 UI 和默认字段可编辑回归；屏幕设置/配置/人工 host/显式兼容迁移专项共 `51 passed`。

本决策不删除历史迁移实现或历史证据，避免覆盖既有行为记录；它只撤销首装和 UI 的主动迁移路径。下一步需要用新源码重建人工验收 Core/包，再由用户确认屏幕设置初始显示和真实识屏结果。


## R01–R07：人工验收缺陷修复（2026-10-07，用户已授权实施）

**R修复当前状态：2026-10-08：Core 4.2.2 / AI 1.0.2 / Screen 1.0.1 的 R01–R05 修复已实现；最新默认单进程全量 4417 passed /15 skipped /14 warnings、自然 exit0，3×152 项满CPU复跑通过，已重建受影响 Core/新 Setup并复核其余交付输入。工程范围验证完成，待用户人工验收，Phase5A 未正式关闭。** 最新默认全量含90s事件预算，621输入未变；旧分组/原生失败仅保留历史。当前c09/新setup-lifecycle对应生命周期fix，真实Provider/画面/系统操作待用户；末次 Ruff check、119 个改动 Python format --check、diff check 均 exit0；报告纪律 59 passed in 0.66s；19 份 Markdown 的 424 个相对链接无断链/尾随空白。

### 目标、范围与合同

Core 统一拥有多服务 API 配置、系统安全存储和用途授权；通用可选 FeatureHostContext.api 仅暴露已授权元数据、用途有效版本、不可变请求快照与变更订阅。文字/文件请求仍在 AI DLC，视觉仍在独立 Worker，不恢复官方 owner/factory 硬编码，不将受信任 Python 插件说成沙箱。

服务统一保存 endpoint/path/model/TLS/credential_ref；按 owner+用途绑定，可共享凭据但不强制模型一致。改地址须重新授权凭据，安全存储失败不回退明文。保存成功才发布，连接测试是显式最小探针（可能收费），不自动截图。旧 AI/视觉/余额只经用户选择、预览、确认、版本校验、原子提交和可重放记录迁入，不自动删旧 Key，不自动启用旧 DLC。

新请求前检查最新已提交配置；普通变更不污染在飞请求，撤权/删服务/停 DLC 取消并丢弃迟到结果；失败保留输入，刷新不覆盖草稿。分离手动/自动识屏，自动关闭、空白名单、无关刷新不取消手动。Worker 两个构建入口共享闭合依赖清单，正常租约/HELLO/READY/自然退出是硬门，不回退 Core 执行。

系统卸载仅 Core 与其拥有的系统集成，不运行 DLC 工厂/卸载事务，不以 staging/ledger/pending 阻止 Core 删除；保留原生代码占用/数据根删除门及路径限定集成清理，要求自然退出，不强杀。保留安装副本、源 ZIP/目录、配置、Key 与个人数据；单包卸载仍独立且不自动恢复已接受事务。

### 顺序与完成条件（R01–R05实现/自动化通过；R06工程验证；R07工程证据完成、用户验收未确认）

|任务|产出与通过条件|
|---|---|
|R01|登记缺陷，先写即时配置、手动取消、Worker 依赖、Core-only 卸载失败回归，保存 red 输出|
|R02|中央配置/用途绑定/安全存储/授权端口/显式迁移；隔离、CAS、失败恢复、无明文测试|
|R03|Core API 设置页与 AI/视觉/余额、四类聊天和文件解读接入；即时生效/草稿/在飞行为测试|
|R04|Worker 闭合依赖与独立生命周期；真实 Qt/进程握手、取消、迟到结果、重试、退出|
|R05|Core-only 维护入口与安装器/单包文案；残留/待处理/占用/清理失败下 DLC 数据不变|
|R06|专项/相关/全量 pytest、Ruff、diff check、时序满负载三遍；重建并核验全部交付产物|
|R07|独立数据根冻结实机、逐文件/性能/实机报告和人工清单；用户真实验收后才关闭 Phase5A|

### 默认版本、风险与停止条件

候选 Core 4.2.2 / AI 1.0.2 / Screen 1.0.1，新包要求 Core >=4.2.2。旧卸载器须覆盖安装新 Setup 后才可验收。保留当前脏树，不提交/推送，不读取真实 Key/截图/收费调用，不运行系统安装/卸载，不改代理/VPN，不自动开识屏、不清理未知 staging。构建空间不足先列确切生成物和影响并确认，两个生成根合计封顶11.25GiB（原6GiB+用户约5g授权的有界5.25GiB）；不静默扩大/清理旧生成物。硬门失败保留失败证据，不以旧结果冒充新绿灯。

### 留档、验证与回退

延续既有设计和 PLAN/HANDOFF/STATUS/WORKLOG/SUMMARY；原 T 历史保留。专项先 red 后 green，最终全套与产物重建；真实 Provider/屏幕/Setup 系统操作为用户门。回退仅按本轮逐文件差异，不覆盖用户改动、不 reset --hard。

### 完成后的实际使用效果

Core 配置一次 Key 并明确授权所需用途，保存后已有聊天窗口无需重启；手动看看屏幕不受自动开关误伤；退出后系统卸载不先拆 DLC，重装可识别有效保留包。限制：服务须支持对应模型/余额协议，真实服务与系统卸载必须以新 Setup 用户验收；工程自动通过不是 Phase5A 正式关闭。


<!-- U_PORTABLE_SETUP_20261009 -->
## U01–U06：Setup 项目目录安装与卸载（2026-10-09 当前合同）

### 决策、边界与验收

- 安装路径页每次显示、旧路径仅预填。新路径必须为非根/非重解析/NTFS 空目录；合法本产品目录允许更新。仅本卸载留下的 data 可凭 Setup 自有 `.setup-project.json` 收据原目录重装，不接受任意陌生非空目录，不迁移旧未发布 APPDATA 组合布局。
- Setup 构建生成 canonical `portable.json`，不改已审计 Core 输入；程序和 data 分离复制。RuntimeLayout 唯一 portable 身份仍为根 marker；Core/设置/余额/API/DLC 事务使用项目 data。官方选装落在 `data/plugins/<id>`；更新未选不等于卸载。
- 用户先完成项目风险确认、个人数据选择（默认保留）、可选不可恢复确认与 Inno 确认；任何取消不得改文件。随后获取 Core/data barrier，只运行关闭的最小 Core maintenance；不运行 DLC 工厂、ledger、staging 或事务恢复。
- 卸载总是删除项目程序、根目录其他内容、portable marker、卸载器和 `data/plugins`；用户选择后才删除剩余 data。先检查选中删除树，再执行；重解析/占用/删除失败中止并报告，不越界、不强杀。保留 data 时不能声称项目目录已完全消失。
- **用户追加决策**：bridge/自启动关联清理只是 best-effort；false/exception 不阻止 Core 卸载，允许留未清理引用。这不撤销目录边界与占用保护，也不把它描述成“已保证所有集成清理”。
- 手工验收冻结入口也通过生产 `_main` 引导：先 code lease/portable data discovery，再 Config/UI；Worker/关闭的维护路径不进入普通 GUI 初始化。避免出现设置写到旧 APPDATA 或占用锁缺失。
- 系统安全存储不在 data 内；删除 data 不额外枚举/删除外部凭据。项目外 ZIP/目录不属于卸载对象。外部导入的已安装副本只要在项目 plugins 内就随目录删除。

### 验证、交付与关闭门

U01–U06 工程实现、自动化、重建和隔离实机验收完成；待用户确认真实功能体验。最新全量：4442 passed, 15 skipped, 14 warnings in 1594.26s (0:26:34)。高负载：三轮通过。最终 Setup 在独立 Windows 目录完成路径页/拒绝非法路径/两DLC安装/取消/未选更新/占用/junction/默认保留/原目录重装/全删验收；具体命令、文件边界、真实结果和限制见[本轮报告](../PR-REPORT-SETUP-PROJECT-DIRECTORY-2026-10-09.md)。

新产物仅使用 `.scratch/phase5a-local-distribution/u06-delivery-20261009/setup-verified/dsh-pet-core-webm-setup.exe`；Core4.2.2/AI1.0.2/Screen1.0.1。不提交、不推送、不覆盖稳定产物。用户仍须验收真实 Provider、余额和屏幕，之后才可正式关闭 Phase5A。

### 实际使用效果

每次安装都确认项目路径，DLC 随项目装卸；个人数据可选保留。关联清理失败不再卡住卸载，但运行中的桌宠仍须自然退出，目录中重要文件必须先移出。旧布局不迁移，保留 data 的重装必须由本 Setup 的收据识别。

## S04 补充（2026-10-10，实施前登记）

- 真实冻结 Core 已从项目内 data/feature-runtime 启动生产 Worker 并完成租约/READY；但退出码 62097，不算通过。日志显示启动前包校验耗时被心跳计时计入，尚未 READY 的新进程被误报 heartbeat timeout。补真实进程 red，心跳只在 READY 后启用，握手仍由独立限时监管；继续验证自然退出。
- 首次全量 4 failed / 4486 passed / 15 skipped / 15 warnings（800.37s）；4 项是旧 4.2.3/Screen1.0.2 版本断言，更新到本次合同后重跑，高负载未启动，不记通过。
- 用户追加：新候选核对 Setup 内 EXE 与快捷方式的鲸鱼娘图标；不清全局图标缓存，不修改真实安装目录。已抽取新 Core/Setup 内10帧与 assets/icon.ico 全相同，仍需最终交付核验。
- 用户追加：API 测试按钮旁显示测试中、成功/失败和实际 HTTP 状态码；网络/TLS/超时没有 HTTP 状态时用明确错误类型，原说明放旁边备注。只改反馈 UI/探针结果携带，不改保存、密钥、API 路由或 Setup 语义；先补 red，明暗720/1100实机检查后重建 Core/Setup。

实际效果：识屏启动不误耗心跳预算；测试连接可一眼看到结果码，不把测试当保存。真实服务最终仍待用户验收。
