# Phase5A 连续记录：HANDOFF

## 2026-10-10 M00 已推送（最新）

已提交并正常推送 `origin/codex/phase3-worker`，完整 SHA `0a299612714e5fad55a24a5506dfce938b9eeb1c`，远程核对一致；159 文件，14196 新增/826 删除。全量 4509 passed、15 skipped；26 族三轮各 292 passed；Ruff、文档与敏感检查通过。下面“待提交”是检查点之前的历史快照。新任务见 [MOD 计划](../mod-authoring-v1/PLAN.md)，不自动发布后续改动。


## 2026-10-10 用户验收与 M00 检查点（当前）

**M00 新门禁已通过（2026-10-10）**：Ruff、工作树/暂存 `git diff --check`；聚焦 **123 passed in 189.57s**；全量 **4509 passed, 15 skipped, 14 warnings in 1910.76s**，exit 0，643 个源码输入摘要前后一致。26 个相关时序族在 20 个自有逐核负载进程下复跑：三轮各 **292 passed**，pytest **198.76 / 143.17 / 137.04s**（调度总时长 **201.656 / 146.203 / 138.828s**），CPU 中位均 **100.0%**，全部负载进程自然退出、残留为空。159 文件敏感模式扫描零命中；540 个相对链接无缺失，报告均入索引。暂存检查发现并修正文档的两行尾随空格；没有更改产品代码来通过门禁。

Windows 11 build26100、Python3.11.1。命令：`python -m ruff check pet features scripts tests packaging`、`python -X utf8 -m pytest -q`（隔离 APPDATA/LOCALAPPDATA、offscreen 和独立 basetemp）、Git 工作树/暂存差异检查。负载族列表与每轮 CPU 样本/退出码由 `m00-checkpoint-20261010/run_highload.py` 和 `delivery-highload-release.json` 留在本机证据目录；不把日志/缓存/脚本化临时数据纳入提交。历史冻结与性能实测仍见 S/U/A 报告，不宣称本轮重新构建或重新使用真实 Provider。

**准确停点**：以上结果已收齐，尚未提交/推送；下一步是最后核对暂存范围和远端未分叉，提交当前源检查点，再正常推送并核对 SHA。新 MOD 功能尚未开始。


- 用户已明确反馈 **“实际体验确认没问题”**；对应当前交付 Core **4.2.4** / AI **1.0.3** / Screen **1.0.3** 与 `_s01b/setup-release/dsh-pet-core-webm-setup.exe`。S 修复的用户体验验收通过，不再列作等待同一轮反馈。
- 自动化/冻结程序证据沿用下方注明日期的历史结果；本轮提交前另行重跑 Ruff、全量 pytest 和受影响时序族满 CPU 三遍，结果已收齐，见上方新门禁。用户反馈不扩张为未测平台、正式签名发布或新 MOD 功能已验收。
- 用户授权当前修改提交并正常推送至 **origin/codex/phase3-worker**；基线 `70ff464f84793f6ea3a342079dcbfb2d991fcf4e`。当前尚未提交/推送，先完成 M00，成功核对远端 SHA 后才开始 MOD 管理中心重做。
- 159 个明确文件（126 已跟踪修改 + 33 新源码/文档）纳入审核；13 个构建/试装生成目录不提交、不删除。原始日志/缓存/测试配置/真实密钥不入库；不触碰真实安装目录。
- 新功能计划尚未实施；其后产生的改动不在本次推送授权内。准确下一步：使用已收齐的 M00 门禁结果，最终审阅暂存、提交、推送并核对。

**实际效果与限制**：4.2.4 的简易 API、项目内 Worker 和已有 Setup 体验已由用户确认；本轮先保存远程回滚点，不提前宣称市场式 MOD 列表或公开 v1 接口可用。


<!-- S04_CURRENT_START -->
## S01–S04 工程交付快照（2026-10-10；用户反馈见上方）

> **S01–S04 工程交付完成；真实 Provider/屏幕效果与用户安装体验仍待确认，Phase5A 不关闭。** 下方 T/R/U/A 和早期 S04 快照保留历史；其旧产物、旧测试数量不是本轮最终结果。

- 唯一待验候选：`_s01b/setup-release/dsh-pet-core-webm-setup.exe`；Core **4.2.4** / AI **1.0.3** / Screen **1.0.3**（Screen 要求 Core ≥4.2.4）。SHA-256 `7282fc05ac0b14138f00d4dbb71edf73af82aee261500f59f2217e8a1756a40d`。`_s01b/setup`、`setup-final` 是中间产物，不交付用户。
- 已修复 portable 项目内 `data/feature-runtime` 误拒绝；仍拒绝 Core/DLC 程序树和 reparse。故障原因先记录再发布状态，手动重试可恢复。真实冻结链路额外暴露“包验证耗时计入运行心跳”缺陷，已改为 READY 后计时；握手超时仍保留。
- 追加用户要求已落实：测试按钮旁显示“连接成功/失败 · HTTP nnn”，超时/网络/TLS 用独立结果码，后方备注解释且明确测试不保存。明暗 × 720/1100、失败保留草稿验证；无 API 架构/Setup 行为改动。
- 冻结最终 Core→项目内已安装 Screen→生产 Worker→租约 HELLO/READY→Core/Worker 自然退出 **0/0**，退出后占用 free；未抓真实屏幕、未读真实 Key、未请求真实服务。Core/Setup/隔离安装副本 PE 10 帧匹配鲸鱼娘 ICO，Windows Shell 私有快捷方式实显一致。
- 最终全量：**4507 passed, 15 skipped, 14 warnings in 802.39s (0:13:22)；exit 0，643 个输入前后摘要一致**。
- 受影响 17 族高负载：**第1轮 169 passed in 47.59s、exit 0、CPU 中位 100.0%（含调度49.875s）；第2轮 169 passed in 47.06s、exit 0、CPU 中位 99.75%（含调度48.656s）；第3轮 169 passed in 47.69s、exit 0、CPU 中位 100.0%（含调度49.813s）；三轮全部通过且自有负载进程自然退出**。
- Ruff、21 个改动 Python format、git diff --check 通过；报告纪律 65 passed，13 份文档 471 个相对链接/尾随空白检查通过；见 closeout-quality.json。相关当前 96 passed（26.59s）；最初目录 red 5 failed、启动诊断 red、心跳/连接反馈 red 15 failed、主题 red 2 failed、已有布局可访问名称 red 4 failed 均保留。
- [工程报告](../../docs/PR-REPORT-PORTABLE-SCREEN-WORKER-2026-10-10.md) 提供逐文件增删、启动/内存/界面实测、冻结/图标原始证据和人工步骤。证据根 `s01-portable-worker-20261010/`；旧候选保留，不提交/推送，不改 `E:/dsh-pet-core-webm`，未清图标缓存或修改用户桌面快捷方式。

### 准确停点与下一步

工程门已完成；下一步仅安排下列用户验收。

1. 只交付 `_s01b/setup-release`，不要用早期 `_s01b/setup-final`。自然退出桌宠后覆盖原 portable 项目；勾选 Screen 确保更新至 1.0.3（不勾选按既有 Setup 合同保留旧包）。
2. 配置无需为本地启动故障重填；点测试看按钮旁实际 HTTP/错误码，修改草稿后旧成功标记失效，保存后才生效。
3. 自动识屏关闭、白名单空时手动“看看屏幕”，确认不再“已暂停”；Provider 不支持时按提示补视觉 Key，不把 Worker 故障说成 Key 错。
4. 查看安装目录 EXE 与桌面快捷方式是否为鲸鱼娘；本轮只验证隔离副本与私有快捷方式，没有改用户快捷方式或清缓存。
5. 未用真实 Key/截图，不重复改 Setup/安装卸载，不恢复 Core 内识屏，不提交/推送。

完整失败史、性能限制和跨层覆盖边界见报告；冻结 READY 51.062s 是并行全量下单样本，不是常态启动承诺。实机冻结验证没有发送识屏请求；合成图/本地 HTTP 请求链由独立真实 Qt/QProcess 集成测试覆盖，两者不能混称同一个端到端用例。

## 本次实际可体验的效果与限制

更新新候选后保留原配置；识屏能从项目内数据目录启动，连接按钮旁直接看成功/失败与结果码。Setup 行为不改；真实 Provider、真实屏幕和用户桌面图标仍需最后确认，Phase5A 不关闭。
<!-- S04_CURRENT_END -->

# 简易 API 与鲸鱼图标恢复：A01–A05（2026-10-10 收尾）

> 更新：2026-10-10T00:05:34+08:00。**A01–A05 工程执行完成；真实 Provider/真实屏幕与新候选安装体验待用户最终确认。Phase5A 未正式关闭。** A 取代 R 的复杂 API 用户表单；U 项目目录安装/卸载合同不变。下方 T/R/U 保留历史，不当作 A 的验证。

[设计](../../docs/plugin-phase-05-distribution/PHASE5A-LOCAL-DISTRIBUTION-DESIGN.md) · [PLAN](PLAN.md) · [STATUS](STATUS.md) · [HANDOFF](HANDOFF.md) · [WORKLOG](WORKLOG.md) · [SUMMARY](SUMMARY.md) · [本轮报告](../../docs/PR-REPORT-SIMPLE-API-2026-10-09.md)

## 当前交付与完成状态

- Core **4.2.3** / AI **1.0.3** / Screen **1.0.2**；新候选 `_a01b/setup-delivery/dsh-pet-core-webm-setup.exe`，SHA-256 `fd27589902adca15900c6e02b9678832d0d1e70f00072dc2eb64730c0bb129d8`。不要使用 `_a01b/setup` 或 `setup-final` 中间产物。
- 恢复 API 列表/地址/模型/主 Key；新增可选视觉 Key，视觉地址/模型默认折叠。测试当前草稿、保存才生效；无需 ID/用途勾选，已有聊天新请求读取最新配置。
- 视觉 Key 留空时只请求主服务；高级视觉地址不同也不转发主 Key。有专用视觉 Key 才使用独立地址；鉴权/模型与网络/限流/Worker 错误分别提示。未自动开识屏、未删旧配置/密钥。
- 原鲸鱼 ICO 的 10 个图标帧与旧 standalone、最终 Core 和 Setup 全部一致。Setup `.iss` 对 A 前输入只有一行图标变更，安装/卸载逻辑不改；已验收 U06 Setup 哈希未变。

## 当前版本验证（不是历史结果）

- 初始 red 10 failed；冻结验收揭示 Qt 所有权问题，新增真实 reparent/DeferredDelete red 3 failed → 相关 **60 passed**。失败日志保留，最初“可打开但报错”的冻结结果明确为失败。
- 最终全量 **4472 passed / 15 skipped / 15 warnings，611.95s**，641 输入前后 SHA 一致；三轮高负载各 **185 passed**，CPU 中位 **99.9% / 100.0% / 99.1%**，自有负载进程全部自然退出。
- Ruff 0.16.6、38 个改动 Python format、git diff --check 通过；报告纪律 63 passed。最终文档链接/输入摘要见 `a01-simple-api-20261009/A01-final-closeout.json`。
- 原生 Windows 明/暗 × 720/1100 UI；自有冻结 portable 数据根无 DLC 与双 DLC API 页都正常、自然退出 0、最终日志无 ERROR/Traceback。冻结 maintenance 安装两个新 DLC code 0，13.125s。
- 生产 Worker 正常 HELLO/READY/租约/自然退出 0；0 屏幕/0 Provider。Core 230 sources/1010 resources、两个包全部清单、Worker 输入和 PE 图标校验通过。
- 性能数字、命令及局限见报告：交错 500/路径旧绑定中位 0.83525ms、简易选择 0.84775ms（+0.0125ms），1000 次解析 retained +233B；不是 Provider 性能结论。

## 保护、限制与下一步

- 工作区 `E:\AI\DSH\dsh-pet-indesktop`；分支 `codex/phase3-worker`，HEAD `70ff464f84793f6ea3a342079dcbfb2d991fcf4e`。无暂存/提交/推送/正式发布；旧 dirty 和已验收产物保留；新 `_a01b` 3,447,898,528B，未超约5GiB预算。
- 本轮候选沿用 manual-acceptance-only 策略，不当作正式签名或自启动发布证明。Core EXE 依赖完整 onedir。
- **工程已完成，无剩余产品修改或重建步骤。** 最后待用户运行本次 Setup 更新 Core，勾选 AI/识屏以更新 DLC；填主 Key→测试→保存，原聊天窗口发送、文件解读、余额；视觉 Key 留空试手动识屏，再按服务需要填/清视觉 Key；检查鲸鱼图标。
- 本轮没有再次写系统安装/卸载状态；U 行为已获用户确认，A 证明其源码未变并测试冻结 DLC 安装。没有读取真实用户 Key、请求真实 Provider、捕获真实屏幕或改代理/VPN；这些体验不可由假凭据/握手推断。

## 实际使用效果与限制

回到适合普通用户的填 Key、测试、保存流程，视觉 Key 可选；图标恢复、已验收 Setup 流程不变。真实服务需支持相应视觉模型/余额协议，用户确认前不关闭 Phase5A。

---

# U01–U06 最终交接／准确停点

> 更新：2026-10-09T21:18:00+08:00。U01–U06 工程实现、自动化、重建和隔离实机验收完成；待用户确认真实功能体验。

设计：[Phase5A 设计](../../docs/plugin-phase-05-distribution/PHASE5A-LOCAL-DISTRIBUTION-DESIGN.md) · [PLAN](PLAN.md) · [STATUS](STATUS.md) · [HANDOFF](HANDOFF.md) · [WORKLOG](WORKLOG.md) · [SUMMARY](SUMMARY.md) · [报告](../../docs/PR-REPORT-SETUP-PROJECT-DIRECTORY-2026-10-09.md)

## 工作区、Git 与下一步

- 工作区 `E:/AI/DSH/dsh-pet-indesktop`；分支 `codex/phase3-worker`；HEAD `70ff464f84793f6ea3a342079dcbfb2d991fcf4e`。
- 大量既有 T/R 与用户 dirty 内容保留；文件表是累计差异，不能全部归本轮。不开子智能体，无暂存/提交/推送；不要清理未知 staging、真实 profile 或 Key。
- 准确停点：授权范围内无剩余产品实现/构建步骤；接下来用户按本轮报告第八节验收。收到失败反馈后先复现并补 red，不能先关闭 Phase5A。
- 最后新增 red：统一冻结入口/警告布局 `4 failed`；删除预检 `1 failed`。相关 green 分别 `62 passed` 与 `17 passed`；真实 Core 占用、junction 预检已在最终 Setup 验证。
- 不重用中间产物/失败全量：第一次全量 4437 passed 属旧入口；第二次全量 2 failed /4439 passed 且 source_unchanged=false，包含修复前 Inno API/旧静态断言，不作为最终门。

## 当前合同与保护边界

- Setup 每次显示路径页，旧路径只预填；新安装选择非根、无重解析点的 NTFS 空目录。合法本产品目录可更新；本次卸载留下的 data 由 Setup 自有 `.setup-project.json` 收据识别后可原目录重装，不能接纳任意陌生非空目录。
- Core、`portable.json` 与 `data\plugins` 同项目；RuntimeLayout 唯一 portable 身份仍为根 marker。未发布的旧 APPDATA 组合布局不迁移。
- 更新不勾选 DLC 不会删除已有 DLC 或覆盖配置；安装时只有勾选的官方包从内嵌 ZIP 导入。
- 卸载删除程序、根目录其他内容与全部 `data\plugins`；默认保留个人 data，选择删除需不可恢复确认。确认期间取消不加锁、不运行 maintenance、不改文件。
- **按用户最新要求：关联／桥接／自启动清理 best-effort，返回 false 或异常不再阻止卸载。** 可能留下该可选集成的旧引用；占用、路径边界、重解析和真实删除失败仍提示并中止。不强杀进程。
- 不加载 DLC 工厂、不读取 ledger、不恢复事务、不清理未知外部 staging；不触碰项目外源 ZIP/目录。不恢复官方 owner/factory 特判。
- 无暂存、提交、推送、正式签名或稳定产物覆盖。实机安装／卸载只操作自有生成的 `_u06-*` 目录；不读取真实 Key、不发收费 Provider 请求、不采集真实屏幕内容、不改代理/VPN。

## 当前交付与验证

- 新 Setup：`.scratch/phase5a-local-distribution/u06-delivery-20261009/setup-verified/dsh-pet-core-webm-setup.exe`；SHA-256 `7cecfde7854fa70cbfa98641457b2ade8c8a547887254ff609204ad1815f6e22`。Core 4.2.2 / AI 1.0.2 / Screen 1.0.1，Core 候选为 manual-acceptance-only。旧 r422、r3、u06-final 的 Setup 及 setup（无 verified）目录不作当前交付。
- 最新全量：4442 passed, 15 skipped, 14 warnings in 1594.26s (0:26:34)；最终634个输入核对 `source_unchanged=true`。
- 高负载：第1轮 242 passed, 1 skipped, 1 warning in 156.61s (0:02:36)，CPU中位99.9%；第2轮 242 passed, 1 skipped, 1 warning in 157.20s (0:02:37)，CPU中位99.95%；第3轮 242 passed, 1 skipped, 1 warning in 155.64s (0:02:35)，CPU中位98.9%。
- 真实 Windows：新装两 DLC、路径页预填/完整文案、根/陌生非空拒绝、空目录接受、冻结设置使用项目 data、占用保护、junction 外部目标保护、三处取消不改字节、未勾 DLC 更新、保留 data 卸载及原目录重装、全 data 卸载均通过。
- 收尾只读核对：Ruff／122个改动Python格式／diff通过，报告纪律61通过，11份Markdown的316个相对链接无错误；最终输入、产物哈希和未暂存状态见生成收据 `U06-final-closeout.json`。
- 工程证据详见 [本轮报告](../../docs/PR-REPORT-SETUP-PROJECT-DIRECTORY-2026-10-09.md)；原始日志/JSON 位于同阶段目录，仅作为生成证据，不纳入提交。

## 使用效果与人工剩余项

安装与卸载围绕一个项目目录，不再因为 DLC 状态或关联功能清理失败卡住。用户仍需使用本次新 Setup 在实际选择的空目录确认 UI 体验，并用真实 Provider/余额与真实屏幕完成验收；不能把生产 Worker 握手成功写成真实识屏通过。**Phase5A 尚未获得本轮用户确认，不正式关闭。**

---

<!-- CURRENT_R_START -->
# R01–R07 历史快照（2026-10-08；安装器合同已由 U 替代）

> 本段仅记录 2026-10-08 历史，当前状态与安装器合同以上方 U 记录为准。工程自动化/本机冻结运行与真实服务/系统操作/用户确认是不同的门；**Phase5A 未正式收尾**。更新时间：2026-10-08T17:07:52+08:00。

## 目标与已确认合同

- Core **4.2.2** 管理多服务 API、OS 安全存储引用、owner/purpose 授权与显式迁移；AI **1.0.2** / Screen **1.0.1** 要求 Core >=4.2.2。AI/文件请求仍由 AI DLC 执行，视觉仍由独立 Worker 执行。
- 通用可选 FeatureHostContext.api 返回授权元数据/版本/单次快照/订阅；不开放全局 Config、服务/密钥枚举，不恢复官方 owner/factory 执行特判。受信任 Python DLC 不是沙箱。
- 四类聊天与文件新请求解析最新已提交配置；普通保存不污染在飞快照，撤权/删服务/停用取消并拒绝迟到结果；失效输入和未保存草稿保留。测试连接不等于保存。
- 手动识屏与自动策略分离：自动关闭、空白名单或无关刷新不取消手动；生产 Worker 真实租约/HELLO/READY/自然退出是交付硬门，不回退 Core。
- 系统卸载只删 Core 及其拥有的集成，保留 DLC 已安装副本、原始 ZIP/源目录、配置、Key/个人数据；不运行 DLC 工厂/逐包事务，不被 staging/ledger/pending 阻塞。Core 占用仍要求自然退出；单包卸载独立、只删安装副本。

## 当前验证与版本对应

- 最新未插桩默认单进程全量：**4417 passed /15 skipped /14 warnings /1391.19 s**（wall 1392.541 s），exit0；pet/features/scripts/tests 共 **621** Python 源码前后/最终快照相同，无新 skip、过滤、全局 Qt 刷事件或保活补丁。之前分组与 AV 记录仅作历史。
- 生命周期既有根因真实公开 seam red 1 failed /6.63 s、子进程 0xC0000005；创建时 owner-thread 身份替代借用 QObject.thread()，green-v3 **23 passed /8.48 s**。新增回归实际 GC 三次 cyclic endpoint，再进新 QEventLoop，不是全局 keeper。
- 最新 **15 时序族 ×3轮，每轮152 passed**、20 logical CPUs/20 自有逐核负载 worker，CPU 中位全100%；Event 停止、自然 join、全部exit0、残留[]。v4第三轮20s ready失败与44.040s迟达证据保留；只将测试 Event/自然 join 有界预算改为90s，产品/租约断言不变；最新全量和v5均含该测试变化。
- 当前 c09 Core SHA-256：`1413d5ef5aeb43de9aa693c3d7060a5849fa9b1c7ae8902c24a6c511a11c7264`；**新 Setup 仅用 r422/setup-lifecycle**，SHA-256：`53fe18eff3e2b45acf0a23c1732675316241b9b84fe7d7ee8214b2429e854f38`。旧 setup-final/旧 c07/c08 不作当前交付。Worker、两 ZIP、helper 由本轮 R 重建且输入复核仍相符。
- 审计 root207/stage212（5生成策略源）/resources1000/bundle2103，PYZ2188、GUI PE=2；新 Core 的 empty/AI/Screen/both 保留 profile 均正常菜单/自然退出0、DLC payload与ledger不变；no-DLC API 服务设置可见/自然退出0。不是系统卸载重装证明。
- API 性能 n1000/n50/n200 与约3×5s短窗口；生命周期 guard 7×10000次/每路径预热1000；实测数字、样本边界与线程/RSS归因限制见 [同一报告 §8](../../docs/PR-REPORT-PHASE5A-LOCAL-DISTRIBUTION-2026-10-07.md)。末次 Ruff check、119 个改动 Python format --check、diff check 均 exit0；报告纪律 59 passed in 0.66s；19 份 Markdown 的 424 个相对链接无断链/尾随空白。

## 保护与人工边界

- 工作区 E:/AI/DSH/dsh-pet-indesktop，分支 codex/phase3-worker，HEAD 70ff464f84793f6ea3a342079dcbfb2d991fcf4e；进入 R 前78个dirty路径保留。累计差异含历史 T/用户改动，不全算本轮新增；无暂存/提交/推送/远端验证/子智能体。
- 两个生成根合计封顶 **11.25 GiB（12,079,595,520 B）**：原6 GiB + 用户约5g授权的有界5.25 GiB；保留旧包/Setup/失败证据，没有删除旧生成物或清理未知 staging。
- 不读取真实 Key、不自动截图/收费请求、不改代理/VPN、不自动开识屏、不运行系统 Setup/卸载器、不强杀进程、不正式签名发布。
- 下一步是用户按 [报告 §8.7](../../docs/PR-REPORT-PHASE5A-LOCAL-DISTRIBUTION-2026-10-07.md) 人工验收：先备份、自然退出，用**新 Setup 覆盖安装更新旧 unins000.exe**；再测一次Key/用途、四旧聊天与文件/余额、自动关时手动识屏、两来源Core-only卸载保留/重装和单包范围。真实协议、费用、屏幕与系统行为不能由自动结果代替。

## 准确停点与下一条操作

工程产品代码停止于已核验源快照，没有授权范围内新的源码实现步骤。当前版本只能作为人工验收候选，尚未用户确认/正式分发。

用户下一条操作：自然退出已安装Core，备份数据与源包，覆盖安装新 setup-lifecycle 的4.2.2；不要直接运行旧卸载器验收新版。反馈时记来源/版本/步骤/实际结果，不提供Key。收到反馈后更新同组记录；出现缺陷先复现/红绿，不能先关闭Phase5A。

继续前优先读 PROJECT-ENTRY → STATUS → PLAN → 本HANDOFF → SUMMARY → 设计/报告§8。原生AV已按公开seam根因修复，不要重复旧最小前缀诊断、全局保活/清队列、恢复官方factory特判或删除旧密钥/源包。
<!-- CURRENT_R_END -->

---

# 原 T01–T07 历史状态（2026-10-07，不代表当前 R 修复通过）

- 工作区：`E:\AI\DSH\dsh-pet-indesktop`；分支 `codex/phase3-worker`；HEAD `70ff464f84793f6ea3a342079dcbfb2d991fcf4e`。累计工作树保留既有与本轮 dirty diff；无暂存/提交/推送。
- 用户授权当前主对话自主完成 T01–T07 与收尾；不开子智能体，不运行真实系统 Setup，不接触真实 profile/密钥，不调用 Provider，不强杀进程，不正式发布。
- 设计：[收尾计划](../../docs/PHASE5A-CLOSEOUT-IMPLEMENTATION-PLAN-2026-10-07.md)；证据：[本轮报告](../../docs/PR-REPORT-PHASE5A-LOCAL-DISTRIBUTION-2026-10-07.md)；同组 [PLAN](PLAN.md) / [STATUS](STATUS.md) / [HANDOFF](HANDOFF.md) / [WORKLOG](WORKLOG.md) / [SUMMARY](SUMMARY.md)。历史快照保留在分隔线后，不作为当前状态。

## 精确停点与下一条操作

- T01–T06 产品与测试改动已完成；全量/高负载/当前构建/自有实机均已经退出并记录。没有待修产品红灯。
- 本轮收尾已通过 `final_document_check.py`，新报告、相对链接、文件表、负载退出及未暂存范围均核对。授权范围内无剩余代码/留档项；脚本/原始结果留自有生成目录，不纳入 Git。
<!-- FINAL_DOCS_VERIFICATION -->
最后新报告专项 `59 passed`，exit 0；13 份相关 Markdown 的 267 个相对链接无断链、无尾随空白。累计 69 修改 + 9 新增的 78 项文件说明/行数已核对，无删除、无暂存；20 负载 worker 全 exit 0、残留 []，5 个已记录自有进程身份复核无残留，未枚举/操控其他用户进程。结果 `implementation-20261007/final-document-verification.json`。
<!-- FINAL_DOCS_VERIFICATION_END -->
- 不要重跑真实系统安装，不回到官方 owner/factory 硬编码；若后续产品源码改变，必须重建并重新跑所需门禁，不能沿用下列哈希。

## 最新可复核验证

- T01 产品修改前：`4 failed in 5.24s`；中性注册/路由、Setup 内嵌、GUI spec 均先红后绿。
- 最终全量：`4334 passed, 15 skipped, 15 warnings in 759.16s`，exit 0；原始日志 `implementation-20261007/pytest-final-current.log`。首次完整的 17 个旧合同预期失败已分开记录并修正，不跳过测试；最终全量后没有产品/测试源码改动。
- 20 CPU worker 满负载三轮相关族：各 `97 passed`，pytest 200.99s / 182.02s / 177.87s；全机 CPU 中位均为 100.0%。20 worker 协作停止、自然 join，全 exit 0、残留 []。
- 最终 Ruff、63 个改动 Python format --check、git diff --check、当前 Core/source/resource 审计均 exit 0；保留 2395 行预算，现代设置实际 2377 行。
- 当前 Core root201/stage206/resources1021/bundle2126 均匹配，PE subsystem=2；helper 与非 synthetic Worker 输入核验。

## 当前构建入口

- Core（manual-acceptance-only、非正式签名）：`c07/dsh-pet-core-webm.exe`，SHA-256 `0f5de807f1f758a0ab97e645fe9c252ad30ebf42a9c234ab25384f3444cc8eed`。
- Setup（已编译、未运行系统安装）：`implementation-20261007/setup-current/dsh-pet-core-webm-setup.exe`，SHA-256 `cbb08a9b21eb267464ca1f8bf8c264952cb0059815f54d2f4e8bb2dfd3fc03e4`，版本 4.2.1。
- helper manifest digest `376a30a368c6051660f166eb5ce555cc7c679be2825263d8911a6b6b63665562`；AI 1.0.1 / Screen 1.0.0 ZIP 内嵌到本次 Setup。Core 禁系统自启动注册，其他正常产品启动/管理逻辑保留。

## 最近实机与失败归类

- 冻结 empty/AI/Screen/both 四种自有 profile：无界面维护（empty 不执行）→正常 GUI Core→startup receipt 清除→对应菜单出现/缺席→自然退出 0；不冒充 Inno Setup 四种人工勾选。
- third-party.example：真冻结 LPAC helper 的目录/ZIP 安装、真实双进程启动/重启、停用/卸载全过。额外当前冻结 Core ZIP 首次启动/重启均通过，revision=4、pending=null；自然退出后停用/卸载 completed、revision=7、versions={}，没有模拟热卸载。
- 默认创建参数（creationflags=0）下自有进程树 62 次观测无可见终端，ffmpeg ConsoleWindowClass visible=false；自然菜单退出 0。不等同 Explorer 人工双击。
- 原生 UI 的 720/1100、浅/深色四张截图已审查；ZIP/目录入口可见可用、横向滚动 0；宽布局滚动遮罩略裁节标题是已记限制。
- 路由/完整验证 50 样本及 empty/third 两组 30s settle 后 3×5s Core PID 数据已落报告；third 15.043481s 的 RSS 净 +892928 B，只是短样本，不能据此宣称长期零泄漏/可归因性能改善。

- 第一版额外探针 Config(base=...) 叠加 source APP_DIR_NAME，装到另一目录；改 RuntimeLayout 后冻结首次/重启通过，没有改产品。标题-only 窗口匹配误把小浮层当 pet，补高度判据后通过；失败日志保留。
- 自有失败探针进程经 PID/createTime/exe 校验后向 Qt 正常结束端点发关闭消息，exit 0；没有强杀、没有接触真实用户进程。
- ISCC 第一次长路径失败只移动本轮新 Core 到 c07；旧 Worker 输入不匹配正确拒绝后重建；cp1252 stdout 故障只修自有诊断环境。

## 后续人工验收（非当前执行项）

1. 真实系统 Setup 的四种勾选、注册表/快捷方式、卸载/重装；不在本轮执行授权内。
2. 冻结文件选择器人工导入、真实用户第三方业务、Explorer 双击、主观文案/视觉确认。
3. 真实 Provider 文字/视觉请求（密钥、费用、真实截图）；第三方 localhost-worker 复杂业务及 macOS/Linux 实机。
4. Authenticode/SmartScreen、正式发布以及 Git 暂存/提交/推送/远端验证；必须后续独立授权。

- 无新增确认项。本轮授权范围无剩余执行步骤；下一对话先读 STATUS/报告，再独立确认具体隔离系统安装、Provider/用户操作或发布授权。

## 实际使用效果与限制

受信任本地 ZIP/目录按 manifest 自身 owner/factory 分流，不再必须进入官方集合；统一入口保留完整校验、事务、租约和启动确认。Setup 已编译为内嵌官方两包，主 Core 是 GUI PE。Python 包不是沙箱，已加载版本等原进程自然退出再升级/卸载；本轮工程收尾不等于所有系统安装和人工发布门通过。

---

## 以下为历史快照，不代表当前实施状态

## 以下为先前施工与文档准备快照（历史，不是当前状态）

# 2026-10-07 当前交接：T01 红测试完成，下一步 T02/T03

## 准确停点

- 已完成指定文档阅读和工作树保护核对。
- 已写入并运行 `tests/test_phase5a_t01_regressions.py`；T01 当前为 `4 failed in 5.24s`。
- 失败测试没有被临时跳过或改成旧 official owner/factory 期望；红因果符合 Phase5A 计划。
- 下一条具体动作：阅读/修改 `pet/official_features.py`、`pet/plugins/package_trust.py`，抽出中性 registration，使 `allow_local_packages=True` 时 owner/factory/execution kind 来源于受限 manifest，而非 `OFFICIAL_FEATURES`。

## 当前验证状态

- T01 red：已运行，失败结果已记录。
- T01 green：未运行。
- 相关测试、全量 pytest、Ruff、构建、实机：未运行。
- 当前分支 `codex/phase3-worker`，保留所有既有 dirty changes；不提交、不推送。

## 重要边界

- 正式签名模式必须继续拒绝 unknown owner；local user-trust 模式只取消 publisher authentication，不取消 manifest、路径、资源、兼容性、SHA-256、transaction、lease、startup receipt 等检查。
- 不读真实密钥/用户数据；测试使用 `tmp_path` 和项目已有的 AI/Screen package fixture。
- `packaging/core_webm.iss`、`scripts/build_screen_delivery.py` 的 T01 改动要与新测试一起验证，不将旧 Setup 产物当作当前构建证据。

## 用户可见效果与限制

下一轮继续后，才能逐步得到未知 owner 本地包可导入、AI ZIP 自动路由和无终端 Core；当前仍停在红测试。

---

# 2026-10-07 当前交接：下一对话实现 Phase5A，当前仅完成文档与清理

> 这是当前准确停点。下方旧内容为历史交接，不删除；新对话以本节为准，不能把历史提交、测试或旧 Setup 产物当作新需求已实现。

## 工作区与授权

- 工作区：`E:\AI\DSH\dsh-pet-indesktop`
- 分支：`codex/phase3-worker`
- 本轮授权范围：写入计划/设计/交付文档，重写相关说明，清理 `.scratch/phase5a-local-distribution` 中确认过时的生成物。
- 本轮未授权/未执行：产品代码改动、测试源码改动、构建、真实安装、Provider 请求、提交、推送、发布。
- 保留规则：已有脏工作树全部保留；不 `reset --hard`，不删除源码、测试、真实用户数据或其他阶段 `.scratch`。

## 必须先读的文档

1. `docs/PROJECT-ENTRY.md`
2. `.scratch/phase5a-local-distribution/STATUS.md`
3. `.scratch/phase5a-local-distribution/PLAN.md`
4. `.scratch/phase5a-local-distribution/HANDOFF.md`
5. `.scratch/phase5a-local-distribution/SUMMARY.md`
6. `docs/PHASE5A-CLOSEOUT-IMPLEMENTATION-PLAN-2026-10-07.md`
7. `docs/PHASE5A-CODE-IMPLEMENTATION-HANDOFF-2026-10-07.md`
8. `docs/plugin-phase-05-distribution/PHASE5A-LOCAL-DISTRIBUTION-DESIGN.md`
9. `docs/ONEDIR_PACKAGING.md`、`docs/SETTINGS-CHANGE-GATES.md`、`.agents/skills/qt-ui-review/SKILL.md`

开始编辑前执行：

```powershell
git status --short
rg -n "official_feature\(" pet
```

## 不可重新打开的产品决策

- Setup 只携带官方 AI / Screen，并把选中的包内嵌到安装器；安装时无界面维护入口自动 preflight + apply，正常启动完成 receipt。
- Setup 外 ZIP/目录由用户明确选择后导入；未知 owner 只要满足 manifest、兼容性、文件清单/摘要、路径和 host/worker 契约就作为第三方 DLC。
- 本地导入不要求公钥、私钥或 `manifest.sig`；主动选择意味着信任包内 Python，文档不能宣称沙箱。
- 公开目录、签名/撤销、社区审核、在线下载等仍是 Phase6/7，不在本轮实现。

## 下一对话执行顺序

1. T01：保护现有脏改动，补 unknown-owner local v2、官方签名拒绝未知 owner、无/旧 `manifest.sig` 本地通过等失败回归。
2. T02–T03：中性 registration；local/official verifier 分流；descriptor 驱动 transaction/state/lease/startup/loader。
3. T04：统一 manifest router 和 DLC 管理；验证从 Screen 入口选择 AI 时不再落到 Screen manager。
4. T05：重写 `packaging/core_webm.iss` 与无界面维护入口，移除 `{src}\\packages` 旁置依赖。
5. T06：重写 `scripts/build_screen_delivery.py` Core spec 为 GUI subsystem，并加 PE 检查。
6. T07：测试、Ruff、全量、Core/Setup 重建、短路径真实矩阵、报告和阶段记录回填。

## 当前 TDD/验证状态

本轮没有新增红测试或绿测试；文档/清理任务不适用代码 TDD。旧测试数字、旧 manual20/v13 和旧 Setup 编译只能作为历史基线，不能写成新合同已验收。

## 清理记录

- 清理前：`53,298` 文件，`9,876,078,289 B`。
- 清理动作：删除 38 个明确未被当前阶段文档/记录引用的旧构建/诊断目录。
- 实际释放：`4,905,969,506 B`、`9,404` 文件。
- 清理后：`43,894` 文件，`4,970,108,783 B`（约 `4.629 GiB`）。
- 未删除：`acceptance-night-20261006` 中三组最终证据、`delivery-candidate-200`、`delivery-set-293`、`publication-20261006`、`setup-acceptance-20261007-shortpath`、五份阶段记录，以及其他阶段 `.scratch`。

## 修改与未提交状态

- 新增：`docs/PHASE5A-CLOSEOUT-IMPLEMENTATION-PLAN-2026-10-07.md`、`docs/PHASE5A-CODE-IMPLEMENTATION-HANDOFF-2026-10-07.md`。
- 同步：`docs/PROJECT-ENTRY.md`、Phase5A/Phase6 文档、`docs/INDEX.md`、PR 报告和本目录五份记录。
- 产品源码/测试/build script 的其他脏改动属于任务开始前已有内容，本轮未清理、未覆盖。
- 当前无 commit/push；下一对话不得默认提交或推送。

## 准确停点

文档与清理已完成；下一动作是新对话按阅读顺序执行 T01，而不是继续本轮等待。未完成项和人工项已经列明，用户无需在当前对话补充信息。

## 完成后的实际使用效果

现在新对话能直接知道“要实现什么、先改哪里、什么不能误称为完成”；桌宠当前运行行为没有因本轮文档任务改变。

---

## 2026-10-06 续接实施：推送检查点后自动验收

### 已完成

- 已按本轮明确授权，将推送前已有白名单内容提交并推送到 `origin/codex/phase3-worker`：提交 `70ff464f84793f6ea3a342079dcbfb2d991fcf4e`，远端 `ls-remote`、fetch 后远端跟踪引用与本地 HEAD 一致，`HEAD...origin/codex/phase3-worker` 为 `0 0`。该提交不是正式发布，也不包含生成物、原始日志、私钥或个人数据。
- 对 `scripts/build_feature_probe_native.py` 先补失败断言，再增加 `Py_SetPath` 分号失败关闭：候选路径含 `;` 时 native bootstrap 返回失败，不允许被解释为多个路径。聚焦测试先为 `20 passed, 1 failed`，修复后为 `20 passed in 0.72s`。
- 使用既有 `Worker04` 冻结产物完成真实非 probe 入口验收：子进程先通过交接 token 接管 Worker lease，发送 `HELLO`，返回 `lease_claimed=true`，再发送 `SHUTDOWN`，退出码 `0`；父端关闭自身 reservation 后版本占用为 `free`、剩余 lease 为 `0`。去掉交接 token 时退出码为 `77` 且没有协议输出。该证据证明冻结入口的租约顺序和失败关闭，不代表 Worker04 已包含本次尚未重建的分号补丁。
- 受影响专项回归：`267 passed, 2 skipped in 37.29s`。随后全量回归：`4298 passed, 15 skipped, 146 warnings in 712.39s`，exit `0`；该次显式设置 `PYTHONWARNINGS=default`，警告数包含已有 `ResourceWarning`，不把警告伪报为零。
- 本地质量复核：Ruff 通过、两份修改文件已格式化、`scripts/build_feature_probe_native.py` 受影响 mypy 通过、24 项聚焦测试通过。新文档记录写入后仍需再次执行文档链接、报告纪律、`git diff --check`。

### 当前边界

- 本次推送授权只覆盖提交 `70ff464` 之前已经存在的检查点；新增的分号失败关闭源码/测试及本节记录目前是**本地未推送修改**，不得把远端分支描述为已包含它们。
- 生成总量当前约 `9.728 GiB`；若按既定 `2.5 GiB` 构建预留直接重建 Core/Worker，理论峰值会超过当前 `12 GiB` 上限。因此本轮没有擅自启动新的大体积生产构建，也没有触碰旧人工验收产物。`worker-04`、`core-04` 仍按其各自构建时间和摘要记录。
- 双包 Core04 完整四组合矩阵此前仍有 `both` 组合因 `management_confirmation_timeout` 未完成；该失败已保留原始诊断，不宣称通过。新版正式 DLC 重签、Setup/普通 ZIP/便携 ZIP重建、真实安装更新卸载、迁移/干净环境和人工模型体验仍未完成。
- 未读取仓库外私钥，未执行正式签名解锁、真实数据导入或个人数据操作；没有强制关闭 Core、设置或外部进程。

### 准确续接步骤

1. 先执行本节记录后的文档链接、报告纪律和 `git diff --check`；确认工作树只包含本轮两份源码/测试及同组记录变更。
2. 在不超过空间合同、且用户明确允许新的构建材料后，重新构建带分号修复的 native/helper/Worker/Core，再跑正式 trust/probe 和四组合矩阵；旧 Worker04 只能作为历史验收证据。
3. 最后集中等待用户执行私钥可信解锁、真实 Setup/便携/导入/模型/干净环境人工门；未执行门逐项保持未验收。

### 用户可见效果与限制

当前源码的路径封闭性更严格，正常冻结 Worker 不能在没有父端租约交接时运行；既有已推送检查点远端可复核。正式新版分发物和真实安装/迁移体验尚未产生，不能把当前结果当成 Phase 5A 完整发布验收。

## 当前停点：推送前全部验证门通过，正在保存远端检查点（2026-10-06）

本轮用户明确授权当前已有内容提交、推送到 `origin/codex/phase3-worker`，随后继续剩余实施。推送不是正式发布，不自动授权后续新源码推送。183文件明确白名单只含源/测试/文档及5份任务Markdown；已逐项暂存，生成物/原始日志/私钥/个人数据不入库。基线HEAD与独立ls-remote均为 `e2687be0866db2915af13447fb49bb278ffdf202`；此处仍为提交前状态，不写尚未产生的SHA。

- 新全量：**4298 passed, 15 skipped, 15 warnings in 929.75s (0:15:29)**；整次 931.253s，exit0，源码SHA前后不变。
- 21个受影响Qt/进程族、20个自有CPU负载进程的连续三遍：第1遍 361 passed, 1 skipped, 1 warning in 332.06s (0:05:32)，wall 336.500s /CPU median 100.0% /p95 100.0%；第2遍 361 passed, 1 skipped, 1 warning in 260.70s (0:04:20)，wall 264.844s /CPU median 100.0% /p95 100.0%；第3遍 361 passed, 1 skipped, 1 warning in 259.37s (0:04:19)，wall 263.437s /CPU median 100.0% /p95 100.0%；源码SHA不变，自有负载均回收。
- Ruff、162文件format、配置mypy26/受影响60/AI host26通过；167项报告/构建回归通过。最终文档补记后还要复跑文档链接/报告纪律与暂存diff检查。
- 证据保存在本轮拥有 `publication-20261006`，不作为Git提交内容。后续独立ls-remote/fetch核对远端SHA和ahead/behind；未核对前不写远程成功。

后续先补分号路径失败关闭及正常冻结Worker租约/握手，再推进最新Core矩阵和性能。新版正式重签、真实安装/导入、模型体验和干净环境仍集中留用户最后确认。12GiB上限、旧拒绝清理目标、用户数据与已验收构建保护不变；不强退用户进程、不使用子智能体。

## 以下为本轮推送准备及旧停点历史

## 当前停点：本轮获授权推送检查点，然后继续剩余验收（2026-10-06）

用户明确要求先把已有本地内容提交推送到 `origin/codex/phase3-worker`，然后继续原计划剩余工作。本次授权覆盖当前检查点；不是正式发布，不自动涵盖后续源码的新推送。基线本地及ls-remote均为 `e2687be0866db2915af13447fb49bb278ffdf202`，暂存为空。183文件明确白名单及封存前快照在 `publication-20261006`，只含源码/测试/文档和5份任务Markdown，不含生成物、私钥、profile或原始日志。推送前重新运行质量/全量/真实CPU高负载三遍；尚未推送不得写成功。

后续先补正常Worker与分号路径封闭性，再推进最新冻结矩阵。正式重签需本地解锁，真实安装/导入与人工/干净环境门仍集中留后。原12GiB生成物上限和旧拒绝清理保护不变，不使用子智能体、不强退用户进程。

## 以下为2026-10-06暂停交接历史

## 当前停点：按用户要求暂停追加工作（2026-10-06）

**本节为当时暂停状态；已被文件顶部的本轮授权与当前停点取代，保留为历史。** 用户要求先总结交接，尚未开始的任务不再启动。分支 `codex/phase3-worker`，HEAD `e2687be0866db2915af13447fb49bb278ffdf202`；保留原 WIP，无暂存、提交、推送、发布、子智能体。

### 本轮已经完成
- V1：仅 AI 的 Core03 无参数正常启动完成真实加载 receipt，实际菜单正确，原生“退出”自然退出 0；9 项身份绑定/UIA 驱动回归通过。驱动改用 Qt 接受的键盘 `WM_CONTEXTMENU(-1)`，不移动全局鼠标、不强退。
- 确认卡片缺少可见包 owner 标题：公开 `create_local_package_dialog()` seam 先红后绿，23 项本地意图相关测试通过；720/1100、长文案/字体检查。物理 DPI 截图仅本应用，未因逻辑/物理坐标误判改布局。
- V3：定位并修复冻结路径三层问题（MSVCRT 归档打开、CPython3.11 getpath 拼接、PYZ/native 路径），59 项相关测试通过；新增 seam 红测为 4 failed /25 deselected，绿测 59 passed /3.96s。
- 生产构建 native-05、helper probe-08、Worker-04 成功。**生成验证签名候选**在真实 LPAC 长/短路径各一次完成 host factory + Worker HELLO→SHUTDOWN→exit0；父端五项隔离均 true，无 ACL/能力/Job/Win32k 放宽，无源码或普通 subprocess 回退。不是正式 DLC 重签。
- 新 helper 完整原生权限 canary 通过：授权只读、scratch读写，未授权文件/账本/lease/凭据/DPAPI/注册表/桌面/网络/子进程拒绝；超时、崩溃、输出/内存限制、父端退出及 profile 恢复通过。网络负对照在 Winsock 初始化阶段被拒绝（10107），不谎称抵达 connect；正常 IPv4/IPv6 正对照连接成功。
- Core04 用已核对的正式公钥策略与 helper08 构建并审计：455,783,226B /1,424文件 /2,260 PYZ 模块；无 AI/识屏实现。**未执行 Core04 冻结启动矩阵，未重新签名或打包其分发产物。**

### 最新验证及边界
- full-01：4294 passed /15 skipped /14 warnings /796.84s，exit0；它早于最后的显式 runtime path 补丁。
- full-02 已收齐：**4298 passed /15 skipped /14 warnings /924.58s，exit0**；命令 `python -X utf8 -m pytest -q`（生成的 basetemp/cache 均在本轮拥有根），证据为 `acceptance-night-20261006/full-02.log` 与 `.exit`。已覆盖最终原生路径修复；运行启动后仅给验收驱动补了类型注解，该文件另经 mypy/9项回归通过，未重新运行整个受影响60文件 mypy。
- quality-02：Ruff 全范围、162文件 format、配置 mypy26、AI host mypy26、129文档链接、167报告/构建测试、tracked diff-check 通过。受影响 mypy60 初次在新验收驱动报7个类型推断错误；补显式 dict/list 注解后该文件 mypy 与9项专项通过。**未重跑整个60文件集合，不把 quality-02 初次总体失败改成通过。**
- 最新源码的 CPU 高负载三遍、Core04 四组合/业务/升级回滚/卸载重装、便携/导入实机、Setup真实安装更新卸载与完整性能样本均未完成；旧结果只为历史。

交接封存文档验证（2026-10-06）：仅复核13份本次交接文档、322个本地文件链接及LOG索引锚点，全部通过（0.0842s）；报告纪律57 passed /0.67s；git diff --check exit0、暂存为空。不是新增Phase5A运行验收，也不覆盖此前受影响mypy60未复跑的事实。证据 `acceptance-night-20261006/handoff-doc-check.json`、`handoff-report-check.log`。

### 准确续接步骤（本次不执行）
1. 先做正常非 probe Worker-04 的真实租约接管/业务启动回归，以及显式 `Py_SetPath` 特殊路径合同：含分号的目录会遇到 Python 的分隔规则，必须证明拒绝或保持封闭三路径，不能增加源路径；本轮只验长/短本地目录，不能写该门已通过。
2. 复核受影响 mypy60，完成新源码高负载三遍；之后用新材料跑完整冻结矩阵、各性能样本门及故障恢复。
3. 要产出正式新版 screen DLC，需要用户在可信本地界面解锁私钥；重新生成 manifest/payload 后签名，不沿用旧签名。Core04/新DLC 再生成普通/便携ZIP、Setup和分发总签名。不得改写 `delivery-set-293` 的平面签名根。
4. 人工统一留后：确认真实安装/卸载目标、真实模型和设置效果、显式旧数据导入、密钥备份恢复、新Windows用户/另一机器、Authenticode/SmartScreen及其他平台。此轮不索要密码、不安装、不导入、不改系统组件。

### 材料与保护
- 本轮拥有根：`E:/AI/DSH/dsh-pet-indesktop/.scratch/phase5a-local-distribution/acceptance-night-20261006`。`ownership.json`、`wip-baseline.zip`、逐项日志/receipt 保留。部分原有 tracked 文件未列入该白名单快照，回滚还须核对 Git diff，**禁止把快照整目录覆盖当完整回滚**。
- 最新 no-follow 抽样：Phase5A合计10,415,149,951B（9.699864GiB），本夜2,044,590,486B（1.904173GiB）；分别43/7个链接/reparse剪枝。上限12GiB；是保留文件快照，不是持续监控峰值，不是已清理。未清理旧验收 Core、真实profile/凭据/事故根；213/254旧拒绝未重试或绕过。
- 正式私钥本轮未读、未解锁、未覆盖；原正式公钥政策、旧签名 DLC、delivery-set-293 均保持。最后材料：helper08摘要 `2e039a80ae474aaa3b65ab08264d8c9d362f3ee3da1be8d12c75e26ba5a0f492`。
- 构建入口与材料：`scripts/build_feature_release.py`；`run/builds/{native-05,probe-08,worker-04,core-04}`（run即上面的本轮拥有根）。原sealed候选仍是旧版，**不能作为本轮修复的最终交付包**。

### 实际可体验效果与限制
仅AI正常启动/退出已证实，确认卡片能辨识包身份，冻结自检长路径问题已通过新材料复验；这些修复尚未形成新版正式分发。Phase5A/5B-1仍未完整验收，原桌宠与个人数据保持不变。现在不要求用户立即做人工步骤，后续恢复实施时再提供一次性操作清单。

---
## 以下为此前历史记录

## 无人值守继续：2026-10-06 长路径故障定位与最新全量门

- 本轮最新源码全量：`python -m pytest -q --basetemp=acceptance-night-20261006/pytest-full-01`（`QT_QPA_PLATFORM=offscreen`，TEMP/TMP 指向自有目录）**4294 passed / 15 skipped / 14 warnings，796.84s，exit 0**。历史 4274 通过不替代此证据。
- V1 驱动根因已定位：Qt 需要键盘语义的 `WM_CONTEXTMENU(-1)`；已修自有 PID 身份绑定驱动并通过 9 个专项测试。仅 AI 正常 Core 无参数启动自行清除 pending、实际菜单正确、自然退出 0 已实测；单次启动 4071.0195ms，不冒充 10 次性能门。
- 实际界面缺陷：双包确认卡片缺少可见 owner 标题；公开 QDialog seam 先红后绿，添加标题；DPI 物理截图证实按钮未裁切，不因错误逻辑坐标捕获而修改布局。
- V3 真实深目录 LPAC host 已通过；Worker 的归档读取与 Python getpath 初始化仍有故障。native-04/probe-07/worker-03 生产构建及依赖审计完成，但生成验证签名候选的真实 Worker 在 getpath:635 失败，**不可标工程门通过**。
- 同类失败后已停止追加生产补丁，改用固定 CPython/PyInstaller 源码及自有原生 PathCch canary 查实际路径/初始化参数。隔离五项均为父端核验 true；不放宽 ACL/能力/Job/Win32k、不回退源码。
- 精确停点：构建仅诊断的 `builds/native-home-debug` 与 `builds/worker-home-debug`，用于打印自有测试目录的 home 长度和拼写；完成定位后再写公开 seam 回归、生产修复、真实 LPAC 复验。
- 本轮私钥未读取、未解锁；新正式 DLC 重签／Core 分发更新仍留到用户本地确认。人工安装／新账户／真实模型门未执行。无提交、推送、子智能体。

## 当前行动：无人值守剩余验收（2026-10-06 本机时间）

用户已授权休息期间独立测试、修复和自验，所有人工／敏感确认统一留到最终汇报。生成物临时合计上限 **12 GiB**；保留 Core 2 GiB、probe 512 MiB 预留，跨盘合并计量；旧清理拒绝不绕过。无提交、推送、发布、子智能体。

### 同一轮执行顺序与验收门
- [x] V0：核对原 WIP、保护范围、材料，创建仅源码／文档白名单基线快照与自有目录。
- [ ] V1：仅 AI 菜单／自然退出根因；驱动问题只修驱动，产品问题先红后绿；不盲点击、不强退。
- [ ] V2：官方冻结四组合、Core 自确认、双包启停／卸载／重装、并发及恢复；验证构建的合成截图和故障单列。
- [ ] V3：受影响生产链重新构建／审计；若正式重新签名需密码则留待人工，不保留密码或弱化信任。
- [ ] V4：Setup 静态／生成边界与可独立验证的安装合同；新 Windows 用户真实安装／更新／卸载需用户登录，留到最后。
- [ ] V5：普通 ZIP、NTFS 便携及生成数据导入／中断；仅操作本轮自有目录，真实数据不自动导入。
- [ ] V6：专项、相关、全量／静态／高负载、性能与报告；不把历史通过变成新通过。

正式产物用于真实启动、UI、factory、receipt、租约和事务；必要验证边界明确标记。所有证据记录 passed／failed／blocked／not_run 及材料摘要。UI 操作必须绑定 PID＋创建时间＋exe＋自有根，不沿用旧 PID。只自然退出本次启动的应用。

人工留项：新账户创建／登录、正式密钥解锁／加密备份目标确认、真实 Setup 目标确认、真实模型／截图观察与干净环境效果；不在夜间等待用户回复。

当前停点：开始读现有冻结驱动与公开 Qt seam，未启动真实安装／导入／密钥操作。旧证据仍以其日期／摘要为准。精确证据：`acceptance-night-20261006/ownership.json` 与白名单 `wip-baseline.zip`，不含生成物或私钥。

### 完成后的实际使用效果
补齐双包本地交付可复验的证据和实际缺陷；人工门仍明确待确认，不宣称正式发布、Authenticode 或全新系统已验收。

## 当前停点：8GiB空间门、小Core与五产物签名通过，完整验收未完成（UTC 2026-10-05T14:39:49.608Z）

- 分支 `codex/phase3-worker` / HEAD `e2687be`，原WIP保留；暂存为空，无提交、推送、发布或子智能体。Phase5A/5B-1仍实施中。
- 2026-10-05用户追加授权峰值**8GiB（8,589,934,592B）**，不取消预算、不扩大清理。305末次只读no-follow实测**8,374,960,188B（7.799789GiB）**，136,776常规文件、36链接/reparse剪枝，余**214,974,404B（205.016MiB）**；E盘余211,506,638,848B。随后仅文本/小证据补记，不把保留量说成连续构建峰值。2GiB仍为新Core构建预留；213/254删除被拒绝、释放0B，未重试绕过，旧人工构建/真实profile/凭据/事故AppData根/密钥保护。
- Core03生产构建/审计通过：455,780,427B /1,424文件 /2,260实际PYZ，构建服务180.321s；helper05摘要`3087700e533095c4a7fc8cd1923baf463f9414108c134cace1426ab5a3ff5e6a`。没有AI/屏幕业务实现、测试锚或源码回退；Authenticode仍未具备。
- **五产物平面签名目录 `delivery-set-293` 已形成**：Setup222,182,771B；普通ZIP259,368,713B；便携ZIP259,368,880B；AI1.0.1 ZIP2,334,658B；screen1.0.0 ZIP24,455,042B；合计767,710,064B。293逐项独占复制并复核SHA256；保留旧候选，不覆盖/删除。Setup279编译119.579s退出0，仍不是安装验收。
- **总分发正式签名294通过**：UTC2026-10-05T13:58:26.426093+00:00，7.809986s/n=1；`distribution.json`摘要`fc7157ead52bc73146be20e1662515e46719eb8fc795767e76cafba0ca46ec5e`。既有`official-release-2026`加密密钥未创建/覆盖；本地masked窗口可见且签名进程退出0，密码没有进入聊天/argv/env/log。296/305从外部已核对公钥政策**独立验证通过（各n=1，1.249367s/1.963210s）**，不是使用产物自带公钥自证可信。签名密钥备份/恢复、Authenticode和正式发布仍未验收。
- AI1.0.1正式manifest摘要`e4faefde007278aa16ae82ee6b984e0b208bea37ad68afbb4b29392f0cfbbc5a`，281正式签名及完整Verifier通过。screen1.0.0保持`8229887fd9d097a7c6f89ec85ff565badbdff6a0a89c81a9e28c249e312d7198`；旧AI1.0.0与旧正式目录未覆盖。
- **最新产品Python质量仍是273/275/276**：全量4274 passed /15 skipped /15 warnings /657.90s；Ruff、155文件format、配置mypy26/受影响57/AI26及165相关通过。20个自有below-normal负载、17族连续三遍各251 passed /1 skipped，118.76/121.54/115.61s，CPU median/p95各100%/100%，负载已回收。后续只有文档和忽略目录驱动脚本，302再次证明产品Python未变；不以这些门冒充安装/人工/干净环境。
- 271真实LPAC自检各10样本通过、拥有材料安全回收1,874,358,751B；AI median/p95 3552.794/3814.0633ms，screen16583.77145/19648.1784ms。原271末尾NameError退出1保留，独立验证核对20有效样本/30原生启动；RSS/IO未采集，不编造。
- **当前冻结证据分开计**：278空包/仅screen的普通Core、自身加载确认/菜单隔离/自然退出0通过。287仅AI的UI安装和普通Core真实加载确认通过（active1.0.1、enabled、revision4、pending=null），但菜单/自然退出驱动退出1，因此仅AI整行和both仍未通过；不借设置替代Core、不覆写287原失败。290/291/292/295/297/298均保留失败；291的未核实Qt源码推断已撤回。295真实卡片按钮是“隐藏桌宠”，297实际该按钮使本进程窗口恢复可见但脚本成功条件仍失败；298无自有像素命中，未发送右键/退出、不强退。304只读身份/命中元数据再次看到目标原生hidden，未移动鼠标、截图、发消息或终止进程，不能反推298时的原因。下一次先定位本测试进程实际Qt可见性/渲染与点击边界，不追加盲猜。
- **文本门302已通过**：129文档链接202.207596s、101报告测试1.63s、tracked diff-check退出0，所有新增文本空白/EOF、176逐文件覆盖及产品Python与273一致性均通过。301在文档遍历240s时超时、父端240.87s退出1原记录保留；302只是把同一文档检查的驱动预算改600s，未跳门/改产品源码/放松probe限制。逐文件计数的旧闭包映射已改显式新numstat参数并复核0差异。最终补记不改链接目标，追加文本/报告/source/行数复核306已退出0（101 passed /1.43s、diff-check退出0、176文件覆盖/空白/源码一致性通过），公开证据归于quality-306，不把289/301说成全绿。所有五MD沿用本组，历史日期/原失败保留。
- **准确下一步**：继续解决Core03仅AI菜单/自然退出及both实际加载；再准备真实Setup/更新/卸载、冻结请求/升级/回滚/便携、>=10启动/各状态性能、用户/干净Windows验收。仅余约205MiB，任何后续解压/构建/复制先估算，不自动提高8GiB。真实安装/导入/密钥备份须再次展示新目标与影响。
- 分发签名根必须保持五文件+json/sig的平面合同，**不能在其内新增packages或证据文件**。真实Setup选包使用安装器旁独立`packages`，准备时先验证签名根，再将相同DLC字节复制到另一个已确认的Setup验收根；不是修改平面信任合同，也不自动安装。

### 当前实际效果与限制
空间阻挡已经解除，最新小Core、两种ZIP、正式双包及总分发签名均已产生并核验；这不是Phase5A交付完成。已验收旧桌宠和个人数据保持不动；自动化测试进程只允许实际菜单自然退出。尚无真实Setup安装、真实模型体验或干净环境通过记录，不需要用户提供聊天密码。

---

## 历史停点：8GiB授权后Core03/ZIP/Setup生成，全量与满CPU三轮通过（UTC 2026-10-05T13:16:35.263Z）

- 分支`codex/phase3-worker` / HEAD `e2687be`，原WIP保留；无暂存、提交、推送、发布或子智能体。Phase5A/5B-1仍在实施，未宣布交付完成。
- 用户2026-10-05追加授权：本轮生成物峰值从6GiB调至**8GiB（8,589,934,592B）**，Core构建预留仍为2GiB。280只读no-follow实测7,599,215,563B（136,544常规文件；36链接/reparse剪枝），余990,719,029B；E盘212,282,949,632B可用。254/213删除被工具拒绝、实际0B释放，未重试或绕过；不扩大清理权限，不触碰旧4B/真实profile/凭据/事故AppData根/密钥。
- Core03已构建并审计：455,780,427B /1,424文件 /2,260实际PYZ模块；PyInstaller133.113s、构建服务180.321s、含预算扫描父端219.955s。嵌入helper05摘要`3087700e533095c4a7fc8cd1923baf463f9414108c134cace1426ab5a3ff5e6a`及正式信任策略；Core无AI/屏幕理解实现，无测试公钥或源码回退。Authenticode仍未具备。
- 最新稳定全量273：**4274 passed /15 skipped /15 warnings /657.90s**（命令父端660.976s），退出0；pet/features/scripts/tests运行期源码哈希未变。275全范围Ruff、155文件format、配置mypy26/受影响57/AI26、129文档链接、165报告/构建用例及tracked diff-check通过。最新文档修改后另行复验。
- 276真实20个自有below-normal负载进程下，17个Qt/IPC/生命周期族连续三遍各**251 passed /1 skipped**：118.76、121.54、115.61s；每遍CPU median/p95均100%/100%，全部负载回收，源码哈希未变。与273共同覆盖当前产品Python；后续只有文档和忽略目录验收脚本修改，不冒称全量覆盖未来变更。
- 267相关专项95 passed /2 skipped；270真实新父端LPAC双方各1次通过。271生成夹具20次自检/30次原生启动及拥有材料GC全部有效，AI median/p95=3552.794/3814.0633ms，屏幕=16583.77145/19648.1784ms；各10次累计回收198,911,947B/1,675,446,804B。原271汇总NameError退出1，独立验证保留失败并核实样本；RSS/IO未采集，不填造。
- 冻结278空包/仅屏幕两组合通过：Core03自行实际加载确认，screen修订4/active1.0.0/enabled/pending=null；真实菜单owner隔离、自然退出0，不借普通设置替代Core。最终仅AI/双包仍待新AI正式签名；旧Core02四组合只为历史证据。
- 277普通ZIP259,368,713B、便携ZIP259,368,880B、正式screen ZIP24,455,042B已审计。首次Setup因验收脚本字符串转义破坏ISCC路径、WinError2未启动编译器；279只重试未创建的Setup，编译退出0：222,182,771B /119.579s，SHA256 `1dbbe87cbf21abebb81825d46c5632e4667a0d0f632e0b4069959cee7a6d7233`。**编译不是安装验收**；未执行真实Setup安装，Authenticode/SmartScreen/Inno许可单列。
- 新AI **1.0.1** 于281正式签名并完整验证通过（UTC2026-10-05T13:14:16.163926+00:00，0.726302s/n=1）；manifest摘要`e4faefde007278aa16ae82ee6b984e0b208bea37ad68afbb4b29392f0cfbbc5a`，目标`production-builds/ai-signed-02`。旧AI1.0.0及既有加密私钥未覆盖；密码只在可信本地遮罩窗口输入，无argv/env/日志/聊天秘密。窗口初查30秒短于预算扫描，284确认同一PID可见，没有重复启动。签名不执行factory、不等于生产加载成功；287最终仅AI/双包真实矩阵正在执行，未先计通过。
- 准确下一步：收齐287最终仅AI/双包真实Core矩阵与最新文档门→核对预算并整理五产物平面分发目录→本地解锁签总分发清单。真实Setup安装/更新/卸载、冻结请求/升级/回滚/便携、>=10启动/状态操作性能、用户与干净Windows门仍未完成；真实安装/导入/密钥备份目标须再次展示确认。

### 当前实际效果与限制
空间上调已让最新小Core及ZIP/Setup产出，而不是跳过安全门。全量、滿CPU三轮和空包/仅识屏真实启动通过；AI1.0.1已完成正式签名，最新双包矩阵未结束，不影响已验收旧桌宠。最终交付及人工/新用户环境验收仍未完成。
---

## 历史停点：生产自检材料有界回收已接入，正在专项复验（UTC 2026-10-05T12:22:13.985163+00:00）

- `codex/phase3-worker` / `e2687be`，原WIP保留；无提交、推送、发布或子智能体。Phase5A/5B-1仍未完成。
- 256公开seam 3 failed，257实现后258暴露VerifiedFeatureDescriptor使用id而非feature_id，修正后259：39 passed /2 skipped /18.49s。不是最终累计门。
- 260生产启动/重试/独立警告先红3 failed /51 deselected /22.70s。已接入按owner分区的父端材料账本、专用维护锁、启动queued后台回收与安全重试；清理警告不改state/启停、不伪报事务失败，Qt对象仍属GUI线程。
- 262相关族176 passed /2 skipped /1 warning /1 failed /315.85s；唯一失败是新UI断言未剥离既有显示专用零宽折行符，原因码原文在tooltip/accessibleDescription中。修正测试保持折行功能；未把失败记为通过。
- 264/265为新增边界夹具错误（不可变verifier、确认策略已绑定），回溯后在创建夹具前统一limits，266真实回归1 failed：一个包的清单上限不足以回收两套快照。现使用有界聚合GC清单，候选执行verifier上限不变。267专项+受影响mypy进行中，未回收结果不计通过。
- 新增`pet/feature_probe_materials.py`、`tests/test_feature_probe_materials.py`；修改adapter/native launcher、management/runtime/UI及公开回归。仅回收本次新材料且有父端intent/PID/profile证据；未知、链接、硬链接、证据冲突及仍活进程保留。真实生成子进程释放回归已在262通过，未强退。
- 254八项清理已获用户授权但exec工具在创建命令前拒绝，实际释放0B，全八項仍在；不重试、不绕过。Core01此前拒绝也不处理。保护旧4B、现有Core/正式签包/密钥、真实profile/凭据与事故根。
- 空间268：5,929,833,411B，余512,617,533B；Core03的2GiB预留仍未满足。最终冻结Core/AI重签/Setup/总分发清单受此门限制；旧Core02不能冒充最终产物。
- 准确下一步：回收267，失败先归因；专项转绿后用已存在的正式签包/helper05验证新父端真实LPAC和自动回收，满足空间门才执行10次probe基准。随后稳定源码全量/类型/Ruff/高负载三遍，更新报告证据。真实安装卸载/导入/密钥备份新目标仍须另行确认。

### 用户可见效果与限制

扩展管理可单独展示自检材料清理警告，不会把成功安装或已启用功能误标为失败。此改动尚未进入新的冻结Core，不宣称用户正在运行的桌宠已包含修复。

---
- `codex/phase3-worker` / `e2687be`；原 WIP 全部保留，无提交、推送、发布或子智能体。
- 最新稳定累计 full234：4258 passed /14 skipped /14 warnings /861.35s（父端863.204s），退出0，pet/features/scripts/tests 的哈希复核无运行中源码修改。14 skip 未执行不计通过，warnings 为实际Qt弃用与重复ZIP负向夹具，不过滤。
- 最新真实高负载233：20个本轮自有 below-normal CPU进程，三遍分别227 passed /109.71、122.16、128.58s；每遍 CPU median/p95 100.0%/100.0%；父端114.500、127.578、133.625s。退出0且负载进程已回收。不是模拟满载，不修改系统设置。
- 229→230卸载窗公开Qt回归先红后绿：不再用scroll控件属性遮蔽QWidget.scroll；回执使用CoreRemovalEvidence可空类型，186 passed /53.93s。56受影响pet/scripts源以及231 AI host26源mypy通过；此前214/223是修复前历史，不代替最新门。
- 正式加密PKCS#8及公钥策略已成功创建，双包签名178完成，不再等待密码、不得重建密钥。可信新helper05为17,267,820B/63文件/176PYZ，摘要3087700e533095c4a7fc8cd1923baf463f9414108c134cace1426ab5a3ff5e6a；原生权限矩阵224与正式双包LPAC225通过。AI host-only不伪造Worker；screen完成HELLO→SHUTDOWN→优雅退出。沙箱网络负对照为真实WSAStartup10107且未connect，不写成已测防火墙connect拒绝。
- 217正式AI/screen目录/ZIP合计80预检+80未接受取消，无factory/Worker执行；218真实NTFS生成夹具同卷移动与同用户OS凭据恢复通过。最终冻结便携/跨卷/干净用户仍未验收；probe目前正式端到端各n=1，不满足n>=10。
- Core02已有真实四组合普通启动确认195/201，未借设置提交成功。但Core02落后于pet/agent_link.py、pet/core_maintenance.py、pet/model_access_tracker.py、pet/core_uninstall_ui.py四源，并携带旧helper04；必须重建Core03和后续ZIP/Setup，不将旧候选冒充最终交付。Worker02/正式两包材料不因这四处Core改动变化。
- 空间237：本轮拥有根5,871,902,944B（113953文件，28 reparse剪枝且不跟随），6GiB上限6,442,450,944B，余570,548,000B。Core03的2,147,483,648B预留不满足，差1,576,935,648B。213清理被工具策略拒绝，未删除且不重试绕过；不降低预留、不超6GiB，不清理旧Phase4B或真实数据。
- 219启动基准首个样本及220有界退出诊断失败，0有效启动样本；仅本轮隔离Core02 PID36460仍驻留，APPDATA为bench-frozen-core-219/APPDATA。不强退、不注入退出、不冒充旧常用桌宠。停止第三次猜测，待实际输入路径诊断或用户使用这个测试Core自己的退出菜单自然结束。
- 准确下一步：等待新清理白名单目标/影响确认与安全可执行条件，重新核对拥有/占用/边界，操作后重测空间；不能自动重试213或绕过工具拒绝。空间满足2GiB后重建Core03并嵌入helper05，重新冻结业务/分发；旧Core02的4处源不一致不得忽略。正式总清单签名、完整请求/升级/卸载/最终便携/真实Setup/人工与干净环境仍未完成。真实安装卸载、数据导入、密钥备份新目标须另行确认；Authenticode/SmartScreen/Inno许可未验收。

- 最新综合质量239/240已收齐：Ruff pet/features/scripts/tests通过（0.249s），151文件format通过（0.079s）；配置mypy26（4.702s）、受影响56（2.649s）、AI host26（0.785s）通过；文档129（61.411s）、构建/信任/注册/报告165 passed（5.94s）通过；tracked diff-check退出0。239首次ruff/format因隔离USERPROFILE使Python user-site模块不可见而失败；240使用已安装绝对路径原生ruff0.16.6通过，不重装依赖、不注入PYTHONPATH、不把239退出1涂成0。
- 241只读新清理提案共8项存在的旧生成物/1,633,900,888B，无删除命令/实际删除。不是重试213被拒绝的core-01目标；保留此前拒绝及所有保护项。必须独立核对拥有证据/占用，明确目标和影响获确认且工具允许后才可处理。空间门和最终Core03仍未解除，不因提出清理方案而计通过。

### 当前实际效果与限制
正式两包和可信自检已有证据，最新源码累计与满载回归通过；仍未完成最终冻结交付。不要安装旧候选冒充最终成果，不宣布Phase5A或5B-1完成。密码已输入且已成功用于签名，本轮无需再提供秘密。

---

## 历史停点：注册拥有边界回归已通过，准备累计回归与 Core03（UTC 2026-10-05T03:34:55.460339+00:00）

- 分支 codex/phase3-worker，HEAD e2687be；所有 WIP 保留；无提交、推送、发布或子智能体。
- 正式密钥及双包签名178已成功，真实 LPAC185、Core02四组合195/201、流式ZIP200、Setup编译202已有历史证据；没有新增真实安装或导入。
- Agent全局注册采用新产品数据根ID与当前Core路径的明确拥有边界：不认领旧hook/外国bridge，不自动pnpm或递归清理未知依赖；Core收尾失败保持可恢复而非宣称卸载。
- Agent公开seam red204/206→green210（259passed/1skip/8.31s）；208巨大参数ID/原生junction夹具及209脚本缝合失败已复盘，不是产品权限回退。修正短ID、原生reparse断言及精确方法编辑。
- 受影响mypy211失败后，212修正Connection可空类型、避免SemanticEvent/str变量混用、tracker接受实际语义事件并使dict类型可判定；3源mypy通过，4文件Ruff/format通过。
- 累计相关212：267passed/1skipped/8.08s，真实Qt及生成HOME夹具；不访问真实Agent配置。新增档案合同199：67passed/7.88s。
- 空间测量：5,183,206,893B，余1,259,244,051B；测试reparse剪枝未跟随。计划只清理本轮旧候选core-01（先保留证据/查边界和占用），保留Core02；并非旧Phase4B运行目录清理。
- 下一步：全量pytest及最终质量门；Core03源码一致性重建/冻结矩阵；Setup/便携/请求/性能及分发签名。真实安装/卸载、真实数据导入、密钥备份新目标另行确认。

### 当前实际效果与限制
正式签名双包与交付候选已生成，真实Core02四组合与自身加载确认已验证；新注册拥有边界只在源码回归中验证，Core02尚不包含它。Phase5A/5B-1未完成，没有发布或给用户安装新候选。

---

## 历史停点：新 Core 四组合启动与分发候选已生成（UTC 2026-10-05T03:08:38.470993+00:00）

- codex/phase3-worker / e2687be，原 WIP 保留；无提交、推送、发布或子智能体。正式双包签名178与原生 LPAC185已通过；不再次创建密钥或读取私钥内容，备份恢复仍待确认。
- Core02真实生产构建：455,763,782B /1,424文件 /2,259PYZ模块 /199.044s；实际 PYZ、原生依赖与可信 helper 摘要审计通过，没有 AI/识屏业务实现或源码/测试信任回退。helper probe-04固定摘要66b94b18f3b971879bb73c4948d2e62c80048bba99659e49d0d55da2146c808f；最终源代码一致性需再审计。
- 真实 UI 安装191：两包分别完成预检/用户界面确认/正式 LPAC/切换，退出3且等待启动确认，未冒报已可用。正常 Core195自行清除两包 pending（revision4），未打开设置；菜单有AI对话/看看屏幕/主动识屏，实际“退出”菜单自然退出0。192～194只是探针窗口过早查询/短暂UIA句柄失效，修正事件与句柄重取后195成功；不强杀。
- 冻结四组合：none/AI/screen（201）及both（195）均普通Core实际启动、对应贡献准确、自然退出0。none账本为空，不存在加载receipt，201日志的固定“BOTH/TWO_PACKAGE”标签不作为事实证据；以receipt states与菜单树为准。没有真实模型/截图/用户数据导入验收。
- 新档案构建公开seam：red196（18failed，接口缺失）→197/198（实际descriptor/stage API纠正）→green199（67passed/7.88s）；新增scripts/build_local_archives.py与测试，Core审计复用只读inspect_core_bundle，不覆盖既有artifact。流式写入、源码哈希复核、独占输出、体积预算；未知用户数据与非空代码锁不入ZIP。2脚本mypy/3format通过，最终全量/Ruff仍需累计重跑。
- archive200退出0：AI ZIP2,334,659B/0.332s；screen ZIP24,455,042B/4.049s；普通ZIP259,350,992B/27.117s；便携ZIP259,351,159B/27.685s。便携标记显式固定data，无用户数据。实际Setup202编译成功95.000s、222,165,149B；仅编译，未安装，Windows发布者认证/编译工具许可未验收。
- 空间203：5,169,418,473B，余1,273,032,471B，上限6,442,450,944B；12测试reparse剪枝且未跟随。Core186预算初次被pytest测试链接拒绝，改为生产拥有根的独立预算并扣除其他占用，未削弱路径安全。下一次复制/构建前估算；未清理任何旧验收目录。
- 准确下一步：补Agent全局hook/bridge新产品拥有边界与Core删除前收尾，后续累计回归；分发总清单签名、Setup更新/卸载实测、便携移动/导入、请求体验/人工/干净环境与性能门仍待执行。新真实安装/卸载、真实导入、密钥备份目标须另行展示确认。既有APPDATA事故及其保护根保留，不抹去历史。

### 当前实际效果与限制
正式签名双包和五项交付候选已生成，真实冻结Core可自行确认两个功能包加载、四组合入口正确。但尚不是完整分发验收，不自动操作真实用户安装，也没有发布。

---

## 历史停点：正式双包签名与原生自检已通过（UTC 2026-10-05T02:27:29.577861+00:00）

- 工作区 E:/AI/DSH/dsh-pet-indesktop，分支 codex/phase3-worker，HEAD e2687be；保留原 WIP。本轮未提交、推送、发布或使用子智能体。
- 正式双包本地签名178已完成，可信进程 PID30964/退出0；公开策略 official-release-2026，指纹 dd18cbd51d2c2367e45efe7ec697a5e27e9b1654e54137f5f2734eec80f4d9ab。没有再次创建密钥，未读取或展示私钥/密码；备份/恢复检查未验收。
- 独立正式验证185：AI manifest SHA256 1084919fa1a4b5afb693ee33a382c72e030e3e058e26a52edf34c50f825acb10（host-only），screen 8229887fd9d097a7c6f89ec85ff565badbdff6a0a89c81a9e28c249e312d7198（host-worker）；外部已核对公钥策略，不用候选自带信任。验证无候选执行，分别26.980/174.190ms，样本各1，非最终性能门。
- 真实正式 LPAC185通过：AI host有效/Worker不适用/父端隔离为真，6.000s；screen host有效、冻结Worker HELLO→SHUTDOWN→优雅退出、父端隔离为真，64.339s。耗时含材料复制/哈希/清理，子探针预算仍30s；三个进程退出0，无普通子进程回退。
- 新增 pet/runtime_resource_import.py：仅已验证 content/characters 资源，manifest哈希/兼容性/角色与版本/active-previous指针完整校验；单媒体128MiB、资源总512MiB，拒绝代码/未登记资源/逃逸。凭据适配器仅处理普通JSON，不读取媒体为JSON。部分删除/写入后恢复仍有pending执行围栏，完整验证后才完成。red179→green181（156 passed/37.89s）；均生成夹具，不是用户真实导入。
- 导入窗口 scroll 属性遮蔽 QWidget.scroll 原生方法已公开seam红绿：red182 1failed→green183 87passed/24.73s；改 scroll_area，已有两处布局用例同步。10受影响源mypy通过；全 pet/scripts/tests Ruff通过；9变更format通过。
- 本地签名长目标窗口采用有界只读滚动文本，不增加权限。red174→green175/177，67/28相关历史已留日志。
- 生成空间184（本次LPAC前）：2,787,736,082B，12 reparse剪枝；硬上限6,442,450,944B。下一次构建/复制前再次估算，不清理旧人工验收目录或其他任务文件。
- core-01/worker-02已构建审计，但core-01早于新资源导入/UI修复，不能当最终累计交付。下一步：核对新产品Agent全局注册拥有边界，再构建core-02，并继续冻结四组合/真实启动确认/Setup与ZIP。旧源角色目录、外部角色/附件提示和大资源内存成本仍待核实，不冒充所有旧资源迁移已完成。
- full134/负载136为新改动前的源码历史，最终全量/负载/性能/真实安装、便携、用户及干净环境门尚未执行。真实数据导入、安装卸载、密钥备份新目标须另行展示并确认。保护意外新AppData根和Phase4B目录；既有APPDATA隔离事故不能抹去。

### 当前实际效果与限制
两个正式签名DLC已有独立验签和原生安全自检证据，但尚未安装或发布。Phase5A/5B-1仍在实施，不能宣称分发与用户体验验收完成。

---

## 历史停点：正式构建候选与待签名材料（UTC 2026-10-05T02:01:59.460059+00:00）

- 正式密钥创建已核对：key_id official-release-2026，公开指纹 dd18cbd51d2c2367e45efe7ec697a5e27e9b1654e54137f5f2734eec80f4d9ab。私钥仅检查元数据；创建窗口的分离启动退出码未捕获，不虚填 0。备份恢复未验收。
- 新生产 LPAC helper probe-04 原生矩阵通过（native153，13,950.618 ms）。网络负向抵达原生 Winsock 初始化，10107/未尝试连接；不冒充测到连接层或防火墙拒绝，不以 Python 导入失败充当隔离通过。
- 冻结小 Core core-01 构建/产物审计通过：455,759,283 B/1,424 文件/2,258 PYZ 模块/142.202 s；独立 Worker worker-02：66,339,336 B/97 文件/459 模块/17.557 s。它们仍是累计修改前的候选，非最终分发验收。
- AI/screen 各一份 v2 未签名材料已生成：ai-unsigned-01（37 文件/2,617,564 B），screen-unsigned-01（117 文件/66,487,121 B）；尚未安装，不能执行为可信包。冻结 Core 显式导入界面在自有 APPDATA 中真实打开并自然关闭，退出 3；未选真实来源、未导入，不能据此宣称完整冻结运行通过。
- 生产 Core 原构建误递归收集桥接 node_modules 硬链接已修：改为现有发布合同的三个文件白名单，仍拒绝发布文件链接。公开 seam red157→green158（53 passed）；正式 Core159 随后构建通过。
- 增加双包离线签名 CLI：一次本地解锁、完整目标预检、确认后再次校验两包、独立结果；部分失败保留首包结果但不报总体成功。red161/red163→green164（67 passed/8.97 s）。待补长目标窗口布局，再展示目标并启动本地解锁，不再次创建密钥。
- 最新回归172：88 passed/1 skipped，11.16 s。跳过为非当前平台用例；最新 full134/负载136 是本次新改动前历史，不能代替最终累计验证。
- 接下来：长目标本地密码窗口红绿→签两正式包→正式 LPAC 自检；并继续自有资源导入/新产品注册清理/冻结四组合及 Setup/ZIP。真实数据导入、安装卸载、备份副本仍另行确认。无提交/推送/发布/子智能体。

### 当前实际效果与限制
正式公钥与小 Core 的构建边界已有真实证据；两个 DLC 还未正式签名，Phase5A/5B-1 仍未完整交付。旧人工验收目录、凭据及意外新 AppData 根仍保护，未删除。

---

## 历史停点：正式密钥创建已核对（UTC 2026-10-05T01:14:39.201695+00:00）

- 用户本轮回复“已输入”，已完成之前授权的本地密码操作。可信CLI公开成功记录与正式公开策略一致，诊断日志0字节；私钥文件302B、创建UTC 2026-10-05T01:09:19.881520+00:00。仅检查私钥元数据，没有读取/展示私钥内容。
- key_id `official-release-2026`，公钥指纹 `dd18cbd51d2c2367e45efe7ec697a5e27e9b1654e54137f5f2734eec80f4d9ab`；公开策略限定两个官方owner及现有能力，未撤销。证据：`signing-launch-142/key-creation-receipt.json`。脱离式启动没有捕获退出码，不能补写退出0；可信CLI仅在两个文件成功写入后输出该成功记录。
- 正式加密密钥/公开政策位于仓库外 `E:/AI/DSH/release-signing/`；备份副本、解密/恢复检查、正式签包/冻结Core/分发验收尚未执行。创建授权不延伸到真实数据导入、安装卸载或发布。
- 下一步先补生产PYZ必须包含导入/维护真实入口的失败回归，以及LPAC网络canary必须实际抵达Winsock连接调用的证据，保留安全边界；随后继续剩余资源导入、新产品自启动清理和正式构建。
- Phase5A/5B-1仍未完整交付。既有full134/负载136为修改前源码历史；无提交/推送/发布/子智能体。

### 当前实际效果与限制
密码操作已完成，本轮不需要在聊天提供任何秘密；尚不能把新小Core视为正式交付。以下密码等待状态为当时的历史事实，不是当前状态。

---

## 历史停点：正式密钥本地密码界面（UTC 2026-10-04T21:58:40+00:00）

- 用户已在本轮回复“确认”，授权创建 E:/AI/DSH/release-signing/feature-release-ed25519.pem 和 E:/AI/DSH/release-signing/feature-release-public-policy.json，key_id official-release-2026；只授权此创建，不含真实迁移/安装卸载/提交推送发布。
- 可信签名CLI已启动，PID 37572、进程创建时间 1791150926.158434；准确自身窗口“离线发布签名 · 本地密码”已验证 visible。原隐藏控制台启动同时隐藏了首次Qt窗口：未重启、未强退；仅核对准确PID/命令行/窗口标题后对本窗口 queued ShowWindowAsync，再确认visible。没有读取子控件、密码、截图或其他程序标题。
- 启动/窗口证据位于 .scratch/phase5a-local-distribution/signing-launch-142；public-result.log只收公钥记录/原因码，diagnostic.log不收密码。APPDATA/TEMP是本轮自有根，窗口使用windows QPA，子进程清除PYTHONPATH/PYTHONHOME和Qt插件覆盖，不初始化真实Config/Core/Worker。
- 窗口核验时（UTC 2026-10-04T21:56:51.029328+00:00）正式私钥和公开政策均尚不存在；**未宣称已创建**。用户下一步仅在本地输入并再次确认密码，不能在聊天/argv/env/log提交密码。取消不执行创建；库拒绝覆盖，不能把失败后已有私钥当作可重复新建。
- 创建前签名CLI/密钥/分发公开策略专项141：20 passed/1.62s，均生成夹具、自有APPDATA/独立pytest根。本轮无产品/测试Python变更，full134及负载136证据仍为源码历史，绝非正式分发验收。

### 准确下一步
核对准确签名PID是否退出、退出码与公开结果，验证新公共策略key_id/公钥指纹与可信CLI输出一致；只读取公开材料和私钥文件元数据，不输出任何私钥内容。退出0且两目标完整后才标记创建成功；取消或失败分类记录，不自动覆盖/删除部分私钥。之后继续剩余资源/附件、新产品自启动、正式材料/沙箱及冻结四组合/性能/人工门。真实数据导入/安装卸载和备份副本目标仍另行确认，不能沿用本授权。

### 当前实际效果与限制
本地密码窗口已可见，用户只需在该窗口输入；目前不要求运行终端命令，也不需要发送密码或私钥文件。Phase5A/5B-1未完整交付；基线e2687be/分支codex/phase3-worker，保持既有WIP与保护目录，无提交/推送/发布/子智能体。

---

## 历史实施快照（UTC 本机观测 2026-10-04T20:54:56+00:00）

- 基线 e2687be0866db2915af13447fb49bb278ffdf202 / codex/phase3-worker 未改变；全部为未提交 WIP，无提交、推送、发布、子智能体。Phase5A 与 5B-1 **尚未完整完成**，当前是源码/生成夹具的实施证据，不是正式分发通过。
- 双 owner 事务、manifest v2/host-only、AI 实现物理迁移与 QThread 取消/迟到隔离/排空、RuntimeLayout/稳定凭据身份、双包管理、显式导入确认工具和专用入口、生产材料及原生 Setup/卸载前置门源码已实施。新 Core 启动确认 UX-M3 有实际生产 seam 回归；仍需新正式冻结 Core 验收，不能由普通设置替代。
- 稳定源码 full134：**4171 passed /14 skipped /14 warnings /701.35s**，退出0。此后只修改文档/本轮基准与负载脚本，未修改产品 Python 或测试源码；full108 混合版本失败仅留历史，不借其宣称通过。14 skip 未执行不算通过，warning 未过滤。
- 真实 CPU 高负载136：20个本轮自有 below-normal CPU 进程，每遍178 passed；pytest耗时87.67/99.89/92.87s，CPU median/p95均100%/100%，负载进程全部回收。真实 Qt/IPC/子进程与独立 APPDATA/pytest 根；不是用户模型或冻结安装验收。
- 导入基准135（源码 warm 进程、生成配置+100条消息、不调用真实凭据/模型）：preflight n20 median/p95 41.052/115.257ms；apply n10 168.159/382.651ms；recover n10 79.154/121.167ms。RSS 28,037,120→29,818,880B，线程5→4，句柄178→182；包含生成夹具及进程缓存，不能推断无泄漏或正式冻结性能。
- Ruff/format134：139个本轮Python文件通过；受影响 mypy134 56个源码文件通过。最新报告/记录整理后的 report-gates139：101 passed/0.76s；docs139：129文件链接通过；diff-check139退出0（仅LF→CRLF提示）。随后仅补齐本段结果与报告行数，不改产品/测试Python。
- 空间138：本轮拥有根 1,645,640,384B（1.533GiB），剩余 4,796,810,560B 至6GiB；有界no-follow扫描 132,033节点，负向链接/reparse剪枝，不读取目标。没有本轮清理，更没有清理旧人工验收 Core、其他任务或真实 profile/凭据。
- 当前证据报告：docs/PR-REPORT-PHASE5A-LOCAL-DISTRIBUTION-2026-10-04.md（日期明确为UTC），已在docs/INDEX.md登记。逐文件行数、源码性能、原生实机及事故/限制分别记录，不用测试集成key冒充正式锚。
- 正式私钥与公开政策目标均不存在，正式锚仍为空；尚未构建正式小Core/两包/Setup/普通ZIP/便携ZIP/分发签名。旧自定义媒体、托管附件迁移及外部附件失效提示、新产品自启动清理、完整四组合/冻结性能、真实安装/用户/干净环境门尚未完成。
- 原生 LPAC canary 的网络负项为 socket 初始化阶段 ImportError，IPv4/6未成功；正常正对照可连接。不得将其描述为已到达 Winsock connect 并由内核拒绝，后续正式权限门须保留原因或增加原生连接证据，不能放宽ACL/能力。
- red91 事故仍保留：UTC 2026-10-04T18:41:32（本机+08为2026-10-05 02:41:32）测试入口失去隔离，意外复制旧真实配置/会话到 C:/Users/DELL/AppData/Roaming/dsh-pet-core-webm，约26KB。已回归修正，原来源与事故目录不删除、不作验收；只看元数据不能证明初始化没访问其他服务，不声称整个任务未触碰真实数据。

### 准确停点和下一步
1. 正式密钥创建前向用户再次展示并确认：E:/AI/DSH/release-signing/feature-release-ed25519.pem、拟定公开政策 E:/AI/DSH/release-signing/feature-release-public-policy.json、key_id official-release-2026。不覆盖既有文件；密码仅本地 masked Qt 界面，不经过聊天、日志、argv、环境变量。
2. 获确认后由代理执行离线密钥工具；备份副本/真实导入/真实安装卸载仍须各自确认目标，不把一次授权延续为发布权限。
3. 补齐剩余数据/资源映射与新产品独立自启动处理的公开失败测试；生产Core PYZ白名单新增实际导入/维护模块。正式helper/Worker→两包签名→正式小Core→Setup/ZIP→分发签名，逐次预估6GiB预算，重新验证真实LPAC与无源码/依赖审计。
4. 冻结四组合、Core-only更新/卸载、NTFS移动、完整性能/体积及用户/干净环境门；终端操作由代理执行，操作后一次性给用户验收步骤。不能把required门降为后续修复而称Phase完成。

### 当前实际效果与限制
源码/生成夹具中两个包可独立管理，聊天/文件理解在AI owner，显式导入工具可展示来源/映射/凭据授权并安全接受或恢复。**现在还没有新的正式安装包交给用户**；不建议移动真实数据或重新安装旧已验收产品。先确认正式密钥目标，再继续生产闭环及尚缺功能，最后才交付人工验收包。

---

## 历史实施快照（UTC 本机观测 2026-10-04T20:32:05+00:00）

- 基线 e2687be / codex/phase3-worker；全部为未提交 WIP，无提交、推送、发布、子智能体。
- 新显式导入 Qt 确认页、后台运行对象及专用 --import-local-data 冻结入口已实现：预检只展示明确来源/文件映射/凭据引用；凭据授权默认未勾选。常规“数据交付”命令、搜索别名和 data-import 深链已接通。独立工具不加载普通 Config、factory 或 Worker；启动入口测试采用生成根与受限启动边界。
- 取消后管理锁竞争的安全重试不会重新接受原确认；普通 Core 的数据锁不阻塞未接受快照清理。导入接受、恢复、取消共享 data-import/management.lock，写入另持独占 data-access.lock；已接受意图禁止取消，秘密不入 snapshot/journal。
- TDD：red126 4 failed（长凭据文案没有独立可换行提示）→127 17 passed/1 failed（布局已修，取消重试错误重应用）→green128 121 passed/12.07s；red129 3 failed（取消误依赖普通数据锁）→green130 104 passed/13.37s。red131 是未保存 Config 的夹具错误，不算产品红；正确夹具 red132 1 failed/4 passed（缺真实深链）→green133 179 passed/62.46s。
- Ruff134/format134：139 个本轮 Python 文件通过；mypy134：配置的56个受影响源码文件通过；git diff --check 通过。稳定源码全量 pytest-full-134 正在运行，尚未计通过，执行结束前不修改源码/测试。full108 的混合版本结果仍保留为历史，不能充当最终门。
- 空间134：本轮拥有根 1,490,975,073字节，剩余4,951,475,871字节至6 GiB上限；有界 lstat 不跟随链接/reparse 测试夹具。没有清理旧验收目录、其他任务、真实 profile 或凭据。
- 正式私钥未创建，正式信任锚为空；已通过 LPAC 的两包仅使用生成集成测试密钥。正式生产小 Core/两包/Setup/ZIP、最终冻结审计/性能、真实安装/用户/干净环境门仍未完成。旧自定义媒体和托管附件导入尚未完成，不能把当前 JSON 导入称为全数据迁移。
- red91 曾意外复制旧真实 APPDATA 配置/会话到新产品目录约26KB；根因已回归修正，意外目录保留且不作验收。仅元数据不能证明初始化未访问其他服务；不得以随后测试通过抹去该事故或声称本任务从未触碰真实数据。

### 准确下一步
回收稳定全量134，修复真实失败（若有）后重跑；再以生成数据测导入预检/接受/恢复/取消耗时与进程计数，并跑 Qt/跨进程时序族真实 CPU 高负载三遍。正式密钥生成、真实数据导入、真实安装/卸载仍须再次确认目标；密码仅本地 masked Qt 界面，不经过聊天、日志、argv 或环境变量。

---

## 历史实施快照（UTC 本机观测 2026-10-04T19:50:29+00:00）

- 基线 e2687be / codex/phase3-worker；仍为未提交 WIP，无推送、发布、子智能体。
- 原生安装器数据根卸载屏障已补齐：跨 Core 副本共享/独占门持续到删除结束；维护入口在普通 I/O 前校验 Shell 固定 APPDATA、非便携、父端真实独占门。green107 69 passed /65.11s；green114 69 passed /5.59s，均为自有夹具，不是产品卸载验收。
- 普通数据映射已对接真实 AI 会话目录；缺失凭据字段防泄漏回归 green112 59 passed /5.87s。新增显式凭据引用适配器：预检不读取 OS 秘密，接受后只迁移选定来源明确绑定的 AI/屏幕理解/余额引用；秘密不入 snapshot/journal，原来源不删除，目标新编辑拒绝覆盖。green117 68 passed /7.37s；green119 81 passed /8.88s（新增来源 UUID 绑定与余额独立凭据回归）。
- full108：4111 passed /1 failed /14 skipped /14 warnings /714.01s；执行中会话映射源码/测试被修改造成混合版本，不能计稳定版本最终门。Ruff104/mypy104 为上述新增代码前的历史通过，需重跑。
- 正式私钥未创建，正式信任锚为空；生产小 Core、正式两包、真实 Setup、导入 UI、附件迁移、最终高负载/性能/人工/干净环境门尚未完成。既有 LPAC 通过仅生成集成测试密钥，非正式签名验收。
- red91 曾意外复制旧真实 APPDATA 配置与会话到新产品目录约26KB；事故根因已修，但意外目录保留，不用作验收；仅核对元数据，不能证明初始化未访问其他服务。不得声称整个任务从未触碰真实数据。
- 继续遵守6 GiB生成峰值；构建前复核空间；旧人工验收 Core、真实 profile、凭据和其他任务文件受保护。

### 准确下一步
补用户显式旧数据导入 Qt 界面与受限入口的公开 seam 失败测试，实现后台预检、来源/映射/凭据授权摘要、不可变确认、待释放/恢复、安全关窗排空。先更新新适配器 Ruff/mypy；正式密钥、真实导入及真实安装/卸载仍先确认目标，密码不经过聊天/命令行/日志。

---

## 历史实施快照（UTC 本机观测 2026-10-04T19:07:11+00:00）

- 基线 e2687be / codex/phase3-worker；仍全部为 WIP，无提交、推送、发布或子智能体。
- 旁置包冻结入口已接通真实管理预检/确认；独立 packaging/core_webm.iss 已编译生成夹具。旧 packaging/dsh-pet.iss 及旧验收 Core 保持不动。原生安装器锁增加依赖子树 reparse/hardlink 拒绝。
- red92 10 failed → green93 27 passed /1 skipped；red94 1 failed → green96（原生锁＋模板）8 passed /40.08s；red95 3 failed 后模板转绿。均为自有生成夹具，不是正式 Setup 安装验收。
- related102 完整本次事务/卸载/UI/原生入口回归：133 passed /1 skipped /1 warning /179.92s；跳过项为平台相关测试，警告是重复 ZIP 条目夹具。Ruff104 133文件 check/format-check通过；mypy104 53文件通过。
- 正式私钥未创建，正式信任锚为空；生产 Core/正式 DLC/真实 Setup 尚未完成；授权凭据与附件导入、导入 UI、卸载跨 Core 副本的持续冻结边界仍需补齐。历史 full41 4034 passed 不含新改动，不计最终门。
- 本次测得自有根 1,310,425,785字节（1.220 GiB），低于6 GiB；未清理旧人工验收目录或其他任务。

### 测试隔离事故与更正（不得被后续通过覆盖）
red91 的入口回归缺少正常 Core 防落入 stub，APPDATA 未隔离，意外进入真实 Config；已中止（退出1，不计通过）。在 C:/Users/DELL/AppData/Roaming/dsh-pet-core-webm 新建目录，触发旧 dsh-pet-standalone 配置及会话自动复制，约26KB。仅核对元数据，未阅读私人内容；不能从元数据证明初始化未访问凭据或其他服务。原始数据未删除，意外目录也保留，未擅自清理或用作验收。因此更正此前“仅生成夹具、未触碰真实数据”的笼统结论。
根因回归 red99 2 failed → green101 120 passed /3.44s：新产品直接 Config 入口不自动迁入旧数据；新冻结 Core 缺 RuntimeLayout 必须启动拒绝。入口测试增加正常 Core 防落入 stub；后续所有 pytest 子进程显式 APPDATA 指向本轮自有测试根（related102 已隔离）。red100 误用不存在的测试路径，未收集测试，不计绿灯。

### 下一条操作和保护边界
补公开 seam 回归：Core 卸载回执不能在其他 Core 副本重新安装 DLC 后仍被用于删 Core；安装器必须持续持有数据根级卸载门直到删除结束，正常 Core/设置先取得共享门。再执行新旧布局、原生安装器、维护入口相关回归。正式密钥创建、真实数据导入与真实安装/卸载前另行展示目标并确认；密码只经本地 masked UI。未创建正式密钥，不以测试锚代替正式锚。

---

## 连续实施准确停点（UTC 实测 2026-10-04T18:40:42+00:00）

- 仍以 e2687be / codex/phase3-worker 为基线，所有变更为 WIP；未提交、推送或发布，未使用子智能体。
- 新 probe-02 的原生 canary 权限/资源/父退出矩阵已回收通过（native-canary-64）；两包真实 LPAC 自检通过（two-owner-lpac-63），仅生成集成测试密钥，不是正式信任验收。
- AI 真实 Qt 对话框 result 方法遮蔽回归已修复：green66 56 passed /4.43s。受影响 owner/build/维护入口 mypy83 为52文件通过；Ruff83尚有导入顺序，lambda问题已修，未计最终通过。
- 卸载版本目录中新出现的孤立文件及空账本孤立版本不得误报成功：red73 2 failed → green74 12 passed /71 deselected /26.59s；完整事务族仍待重跑。
- 新真实双包 Core 卸载协调器、异步 Qt 确认窗及受限冻结维护入口：green77 9 passed /7.35s，green80 14 passed /10.81s，green82 31 passed /1 skipped /6.81s。仅生成夹具，未真实卸载产品。
- 原生 Inno 与 Python 共享/独占同一字节范围的文件锁已实际编译并跨进程互验；green88 4 passed /36.95s，包含硬链接拒绝、便携目标拒绝。夹具在 InitializeSetup 退出，不安装文件、注册表或快捷方式，不是正式 Setup 验收。
- 旁置本地 ZIP 意图适配器已实现：green90 4 passed /1.20s；真实后台管理预检，不执行代码或激活账本。正常入口路由和新产品 Setup 尚未接通。
- 私钥未创建；正式信任锚为空，生产 Core、正式 DLC、完整 Setup、凭据/附件授权导入及导入 UI 尚未完成。
- 历史全量41 4034 passed /13 skipped /14 warnings /934.25s 不含上述新改动，不能算最终门；完整全量/高负载三遍/性能与体积/人工/干净环境仍未完成。

### 下一条操作与保护
先补旁置包入口公开 seam 的失败测试，接通新冻结 Core-only 路由，再实现独立 core_webm.iss 并编译自有夹具；不修改旧安装器或旧验收运行目录。继续保持6 GiB上限；正式密钥、真实导入及真实安装/卸载前再次展示目标与影响确认。密码只经本地可信 masked UI。


---

# Phase 5A 精确停点

## 当前停点 2026-10-05T01:50:31+08:00（本机观测时间）

- 基线 e2687be / codex/phase3-worker；所有新修改仍为 WIP，没有提交、推送或发布。
- 全量快照 pytest-full-41 已回收：4034 passed /13 skipped /14 warnings /934.25s。其后新增构建器及类型修复未覆盖在该次全量中，不能当最终门。
- 新 scripts/build_feature_release.py 实现可信 probe、冻结 Worker、正式小 Core 的闭合材料/源码摘要/独占输出/预算/实际 PYZ 与原生依赖审计。专项红绿见42～48、60～61日志；最新 builder 12 passed /3.02s。
- 在本机实际重建 native-01、Worker worker-01、helper probe-02。helper bundle SHA256 f0445bad4ef722c23f6c75038d103d57aa9f9bcdfd7356bc82d79894967b40f3；没有生成正式私钥或生产 Core。
- 两包真实 LPAC 自检通过（two-owner-lpac-63.log）：screen host + Worker HELLO/SHUTDOWN/优雅退出；AI host-only，Worker 明确 not_applicable。仅内存生成的集成测试密钥，不是正式签名验收。
- 失败复盘：probe-01 未包含公共 feature_ports，真实两包 factory 加载失败。先补公开材料测试（red60），加入公共 SDK 后绿61并重建 probe-02，真实重试成功；未扩大 ACL 或放宽 Win32k/Job/LPAC。
- probe-01 的原生权限和崩溃/超时/输出/内存/父退出清理矩阵通过；probe-02 的同矩阵正在 native-canary-64.log 中复验，未回收前不计通过。
- 配置的26文件 mypy 门通过；新增 owner/build 55文件尚有21项类型错误，广义受影响95文件的207项含存量债，尚未最终归因/清零。
- 构建前实测本轮拥有根1,128,741,036字节（1.051 GiB），低于6 GiB；未清理旧验收目录、真实profile或凭据。

### 尚未完成 / 下一步
修复并复验新增类型门与新helper权限；继续 Core文件更新/卸载的自然退出前置门、旁置本地包 Setup、授权凭据/附件导入与UI。正式信任锚仍为空。生产 Core/DLC/Setup、性能/最终全量/高负载、真实安装/人工/干净环境均未完成，不宣称Phase5A或5B-1完成。

### 授权和保护
不使用子智能体；不提交、推送、发布。不触碰旧 manual-core-03、manual-session-01/no-chat/APPDATA。正式密钥创建、真实数据导入及真实安装/卸载前必须再展示目标并确认；密码只经本地可信masked UI。仅本轮自有生成夹具已经用于测试。

## 历史停点（保留原证据，不代表当前状态）

更新时间：2026-10-05T00:58:51+08:00（本机时间）。基线 2026-10-04 / codex/phase3-worker / e2687be，均为未提交 WIP。无提交、推送、发布或子智能体。

## 当前事实与证据
- 固定双 owner、v2/host-only、独立账本/租约/receipt 和 Core 启动恢复已有源码 seam；新冻结 Core 尚未验证。
- unsigned 材料与签名/分发工具：pytest-materials-green-27，34 passed /6.10s。材料有固定来源、独占目标、同次拥有范围和 6 GiB 预算，未执行代码。
- 小 Core 的旧 AI 配置保持 opaque；AI 策略迁入 owner：pytest-opaque-green-31，91 passed /4.31s。
- 余额独立 vault/CAS/请求配置：pytest-balance-green-34，26 passed /0.96s；实际 General 余额授权、异步保存、窗口先关闭、退出排空及相关族：pytest-balance-ui-green-37，73 passed /22.39s。
- AgentLink 余额路由不借 AI；AI 内置背景从版本-owned resources 读取：pytest-owner-routes-green-40，24 passed /0.97s。误写不存在 test_chat_theme.py 的命令只得到 no-tests-run，不计通过；真实文件为 test_chat_themes.py。
- pytest-full-17 历史：6 failed /3988 passed /13 skipped /759.94s；六项 seam 修复与相关族转绿。新的全量 pytest-full-41 正在运行（会话 35548），尚无最终结果。
- Ruff --fix 已修 6 项；format 9 reformatted /108 unchanged，当前白名单117个 Python 文件；仍需后续最终 check。

## 未完成与硬门
5A-1/2/3/5 与 5B-1 均仅部分完成。正式信任锚为空、probe 摘要未生成；没有新生产冻结 helper/Worker/Core/DLC/Setup。新的 Setup/更新/卸载前置门、授权凭据/托管附件导入及 UI、完整布局、mypy/高负载/性能、实机安装/人工/干净环境均未完成。不能宣称 Phase 5A/5B-1 完成。

## 保护与授权
保留 .scratch/phase4b-local-management/manual-core-03 与 manual-session-01/no-chat/APPDATA、旧产物、真实 profile/凭据及其他任务。峰值6 GiB，构建前重新计量。未生成正式私钥、未导入真实数据、未真实安装/卸载；这些目标操作须再次确认。生成夹具和测试密钥不是生产证明。

## 下一条实际操作
为闭合的生产 Core/Worker/helper 材料与构建入口写失败测试：独占拥有根、外部已核对公钥、物理代码排除、非验证入口、无源码回退及预算。先完成独立于正式密钥的实现，再进入需要目标确认的正式签名。并行的全量41须回收实际结果；不要重新做已绿材料/余额。

## 实际效果与限制
目前仍是工程实现停点，不是冻结交付或用户验收。

## 2026-10-05 空间预算调整（用户当前明确授权；2026-10-05T12:34:02.262Z）

用户允许适当增加生成物预算。本轮当前上限由6GiB提高至**8GiB（8,589,934,592B）**，保留Core构建**2GiB（2,147,483,648B）**预留；E盘实测余216,251,744,256B。6GiB仍是此前命令/历史结果的实际上限，不改写历史。

不扩大清理权限：254八项及213被工具拒绝的删除不重试、不绕过；旧人工验收目录、真实数据、凭据和密钥不动。新构建/复制前重新测量并估算，预计超出8GiB先报告；不提交、推送、发布或使用子智能体。

267专项已通过95 passed /2 skipped /84.04s，5个受影响源mypy通过。此前269启动命令尚未产生脚本或日志，属执行工具未送达，不能算原生验证成功；改用新的明确拥有脚本270，不复用同一产物路径，也不尝试受拒绝删除。

## 2026-10-06 当前准确停点：v13/v14 与全量回归已收尾

### 已完成

- 重新构建携带键盘上下文菜单修复的 `core-11b`，返回码 `0`，no-chat Core `1,639,548,973` 字节。
- screen v13 与 empty v14 均在真实 Windows 冻结 Core 上通过；screen 实际菜单、生产加载确认、状态账本及自然退出均正确，empty 没有识屏入口。
- 修复了冻结窗口 `WS_EX_NOACTIVATE` 与原生验收驱动之间的错误假设：不激活窗口，使用 Qt 键盘语义菜单事件和产品稳定身体锚点。
- 受影响测试 `66 passed`；Ruff、format-check、`git diff --check` 通过；最新全量 `4300 passed / 15 skipped / 14 warnings / 671.83s`，exit `0`。
- 已清理本轮拥有根内遗留验收进程；扫描无本轮拥有的 `dsh-pet-core-webm.exe` 进程。

### 未完成 / 不得宣称完成

- 未读取或解锁正式私钥；正式分发总清单尚未签名。
- 未进行真实 Setup 安装/升级/卸载、便携移动、旧数据导入、干净新用户/另一台机器验收、真实模型与截图人工体验。
- 本地 WIP 尚未提交/推送；不把 `.scratch` 证据目录当作版本提交。

### 下一次从这里继续

1. 先核对 `git diff --numstat` 与本轮白名单，审阅剩余源码差异。
2. 如用户继续授权自动工作，先做文档/报告纪律与空间预算核对，再决定是否只构建精简分发验证材料；正式私钥操作必须由用户在本地可信界面输入密码。
3. 将 Setup/普通 ZIP/便携 ZIP/两包正式签名及真实安装验收分开记录；不能用冻结 Core 的通过替代分发验收。

### 质量门补充

Ruff、全量 format、文档链接和报告纪律均通过；项目级 `mypy pet` 仍有 `439 errors / 46 files`，定向本轮模块仅暴露 `pet/window.py` 的既有 19 个位置问题，新增菜单代码没有诊断。下一次如进入提交前质量审阅，应继续把 mypy 作为既有技术债，而不是本轮功能通过证据。


## 2026-10-06 自动实施停点：生产 probe 路径修复后

### 已完成

- 已定位并修复 screen probe 在新生产 Core 中的真实失败：深层验收根导致 PyInstaller embedded PKG 路径过长。修复使用 Win32 `SHGetKnownFolderPath` 的 LocalAppData 短根和 data-root identity 分区；不放宽 LPAC、Win32k、Job、句柄继承或 factory/Worker 执行顺序。
- 已用最终本机 Core `core-05-final-20261006` 重跑冻结 AI 与 screen：均 `CASE_PASSED`，真实 probe、生产加载确认、功能菜单、状态 pending 清除和自然退出均通过。
- 已完成全量测试与质量检查：`4301 passed, 15 skipped, 14 warnings`；Ruff、受影响 format、目标模块 mypy、文档链接和 diff-check 通过。

### 精确证据位置

- Core：`.scratch/phase5a-local-distribution/production-builds/core-05-final-20261006/`
- AI 冻结验收：`.scratch/phase5a-local-distribution/frozen-ai-production-20261006-final/`
- screen 冻结验收：`.scratch/phase5a-local-distribution/frozen-screen-production-20261006-final/`
- screen 深路径诊断：`.scratch/phase5a-local-distribution/frozen-screen-production-20261006-v23/`
- 本次变更源文件：`pet/feature_management.py`、`tests/test_feature_management.py`，以及已有上下文菜单验收 WIP 文件。

### 恢复时的下一步

1. 先阅读本文件、`STATUS.md` 与 `PLAN.md` 的最后一个 2026-10-06 小节。
2. 不再重复生产 Core 构建；先为 Setup/便携/卸载和干净环境准备有界、独立的 acceptance root。
3. 真实安装/卸载、正式签名解锁、旧数据导入、模型/截图、另一台机器和发布前操作必须在最终汇报中单列；没有人工证据不得勾选。
4. 本轮没有提交或推送授权；如需发布，重新核对白名单、敏感文件和远端目标。

### 保护声明

不读取 `E:\\AI\\DSH\\release-signing\\feature-release-ed25519.pem`，不操作真实 profile/凭据/聊天历史，不强制关闭合法 Core/设置/外部进程，不清理旧人工验收目录，不用 `reset --hard`、强推或广泛删除。

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


## 2026-10-06 准确停点：屏幕独立配置提示已修正

- 工作区：`E:\AI\DSH\dsh-pet-indesktop`；分支：`codex/phase3-worker`。
- 本轮已改源码、测试、Phase 5A 记录和 v4 隔离验收脚本；未提交、未推送。保留既有 WIP，不使用 `reset --hard` 或广泛清理。
- 根因已确认：screen profile 存在且迁移已确认，但 `credential_ref` 为空；AI credential 不复用。
- 受影响专项为 `53 passed in 16.48s`；Ruff/format-check、PowerShell 解析、fixture 合同和新人工验收包内容检查通过。
- 全量最新一次针对本修复运行结果为 `4298 passed, 14 skipped, 14 warnings, 6 failed`；失败集中在 `tests/test_drag_move_coalescing.py` 的窗口边界夹紧期望，不属于本次屏幕配置修复。
- 新人工验收入口：`.scratch\phase5a-local-distribution\manual-user-acceptance-20261006-v4-screen-ux\README.md`。该根目录不复制 v2 数据，fixture 不含秘密。

### 用户下一步

1. 运行 `launch-install-screen-only.ps1`；脚本仅在 v4 根没有配置时放置脱敏 fixture 并安装 `official.screen-understanding`。
2. 关闭安装/确认窗口后运行 `launch-core.ps1`。
3. 打开“设置 → 自动化与联动 → 屏幕理解”：应显示已有独立配置但要求补填视觉 API Key，不应再要求重复迁移。
4. 由用户在屏幕理解设置的掩码输入框中录入 Key、保存，并触发一次真实屏幕请求。

不要把旧 v2 的 AI 或屏幕凭据复制到 v4；如果要复用旧人工根，应先另行确认数据根和包信任边界。


## 2026-10-06 最终复核：屏幕 UX 验收包证据

- 使用 Python `zipfile` 直接读取 4 个 screen 包 ZIP，均确认包含修正后的 `host/manual.py` 与 `host/settings.py`，并确认包内含“补填视觉 API Key”“无需重复迁移”文案；未依赖未安装的 `7z` 命令。
- `screen-config-fixture.json` 的原始字节无 UTF-8 BOM，JSON 合同断言通过；不会因 PowerShell 编码导致产品配置读取失败。
- v4 安装脚本 PowerShell AST 解析通过，fixture 只在配置不存在时复制；不会覆盖用户后来在隔离 UI 中保存的凭据引用。
- 受影响源码/测试范围的 `git diff --check` 通过。整个 WIP 工作树仍有历史任务记录中的尾随空白告警，未进行大范围格式化，避免改写既有证据。
- 尚未代替用户输入真实 screen Key 或发起真实 screen 请求；这仍是唯一需要人工执行的本次修复验收门。

## 2026-10-06 准确停点

1. 已完成 AI 凭据迁移代码修复和相关自动测试。
2. 已使用正确的独立 Worker 和固定 probe bundle 完成新的 chat/no-chat 人工验收构建。
3. 已启动可见安装窗口：.scratch/phase5a-local-distribution/manual-user-acceptance-20261006-v5-ai-screen-credential/launch-install-screen-chat.ps1。
4. 下一人工动作：在安装窗口确认 official.screen-understanding；关闭管理窗口后由 Codex 启动同目录的 launch-core.ps1。然后用户只需在 AI 对话设置输入已有 DeepSeek Key，再在屏幕理解页点击“从 AI 对话配置补齐凭据”并确认，最后确认真实识屏请求。
5. 任何 self_check_isolation_not_enforced 仍按安全硬拒绝处理，不能绕过或降级。


## 2026-10-06 准确停点更新

1. `worker_probe_failed` 已定位为旧 Worker 构建缺少 `_dsh_probe_native.pyd`，不是凭据或事务状态损坏。
2. 已在 `scripts/build_screen_delivery.py` 增加真实 Worker 原生隔离证据硬门，并通过新增回归测试。
3. 已用 `worker-06` 重建 `core-13-screen-ai-credential-native-20261006`；屏幕包 SHA-256 为 `98032a5e20b0b5649adc0945e6ccc3059a444dfae1ce1dddca3ceeb4a3ccac49`。
4. v6 隔离人工根：`.scratch/phase5a-local-distribution/manual-user-acceptance-20261006-v6-ai-screen-credential-native`。
5. Codex 已正常关闭管理窗口并启动新 Core；`state.json` 的 `pending_transaction` 已清空，真实启动加载确认通过。
6. 用户下一步只需在当前可见 Core 中完成：AI 对话配置确认 → 屏幕理解状态确认 → 一次真实识屏请求。不要向终端或聊天发送 API Key。
7. 当前 Core 仍在运行；人工识屏请求完成后再决定是否关闭。


## 2026-10-06 用户可见启动反馈

- v6 Core 已完成真实启动确认并显示角色；灵动岛先出现、角色稍后出现，用户反馈有些慢。
- 记录为首次启动时序/性能待量化项；不要把诊断控制台当作正式发布界面。


## 2026-10-06 准确停点：screen 默认配置改动已转绿

最近动作：根据用户反馈撤销屏幕理解首装的主动 AI 配置迁移体验。源码现在只在设置 UI 对空 screen 配置准备一个无凭据默认 profile；不会再显示迁移/补齐按钮，也不会为了打开页面读取或复制 AI Key。已有有效 screen 自有配置保持原样加载。

最近验证：
- `QT_QPA_PLATFORM=offscreen python -m pytest -q tests/test_screen_settings.py tests/test_screen_manual_host.py tests/test_screen_configuration.py tests/test_screen_ai_migration.py`
- 实际结果：`51 passed in 4.86s`。
- 最近失败为布局测试仍按空模型输入，已修正为先清空默认模型后再输入；不是产品故障。

下一步精确动作：用现有 `worker-06`、probe-08 和新的源码重建 screen 包/Core，生成新的隔离人工验收根；先验证设置页显示默认配置且无迁移入口，再等待用户完成真实屏幕请求。当前无新增安全方向阻塞，尚未提交/推送。
## 2026-10-06 产品合同修正：签名不再是 Phase5A 激活前置条件

### 已完成的代码与文档方向

- `FeaturePackageVerifier` 增加显式 `allow_local_packages` 路径：用户选择 ZIP/目录后，包标为 `local_user`，仍经过闭合 schema、官方 feature/factory、版本能力、路径边界、文件数量/大小、SHA-256 清单、事务和 LPAC/Worker probe；不读取或验证发布者密钥。
- Phase5A 主构建器不再生成 Ed25519 密钥、不再写 `manifest.sig`，交付元数据标记为 `package_activation=local-structure`、`signature_required=false`。旧 `public_key` / 签名参数仅供历史 signed fixture 测试，不是产品路径。
- Setup 与扩展管理 UI 的可见文案改为“本地功能包”；设计、README、项目入口和验收脚本已经写明“Setup 选装 + 其他场景 ZIP/目录显式选择”的当前合同。
- 新增 `tests/test_phase5a_local_activation.py`，覆盖无签名本地包、篡改拒绝和 signed-only 拒绝。

### 当前准确停点

- 上述源码修改已落盘，但新的 `manual-17-local-activation-20261006` GUI 构建、真实 Windows 安装/管理页验收、旧产物清理和全量回归仍待执行。
- 不得把历史 `manual-16`、v7 或正式签名证据当作本轮无密钥产品证明。目标 Core 路径应为 `.../manual-17-local-activation-20261006/chat/dist/dsh-pet-core-webm/dsh-pet-core-webm.exe`。
- 下一步：先构建并检查 package manifest/metadata，再运行 `scripts/validate_phase5a_delivery.py`，随后以真实 Core 做可见 UI/自然退出验收；用户只需在最后执行不能代替的真实 Provider/视觉请求和主观安装器体验确认。
- 当前未提交、未推送；未读取或生成正式发布私钥。


## 2026-10-06 最终交接：Phase5A 本地激活收口

### 已冻结的产品决策

1. Setup 安装向导才提供可选安装任务；`ai` / `screen` 的勾选只表达安装意图。
2. 非 Setup 场景由用户选择普通 ZIP 或本地目录；扩展管理页负责预检、确认、事务安装和启动自检。
3. 当前本地激活不查找或验证发布者公钥/私钥，不要求 `manifest.sig`。v2 `key_id`（若存在）只作为非密码学 schema 标识。
4. 不能因为取消签名就放松结构、完整性、事务和 LPAC/Worker 约束。

### 最终实现与证据

- 最终构建根：`.scratch/phase5a-local-distribution/acceptance-night-20261006/builds/manual-20-runtime-fix-20261006/`。
- AI ZIP：`official.ai-chat.zip`，118,378 B，SHA-256 `6912b007483af418a27e43218a3ed4f71d933d80014420453a24f3bb3bfa8d7a`。
- Screen ZIP：`official.screen-understanding.zip`，24,455,393 B，SHA-256 `e0695c68968244353458885f271be41c7c68f4dd21ee02361cf3c42d9ff8e6a7`。
- Probe bundle manifest SHA-256：`915f87620c3a84219bf7310e0ff805ff3c25d0b05e2f0ba2d4b95c5eba9a3232`；`signature_required=false`；构建耗时 295.814 s。
- 验收根：`.scratch/phase5a-local-distribution/manual-user-acceptance-20261006-v13-runtime-fix/`。`empty` / `ai` / `screen` / `both` 全部 `status=passed`、exit code 0、pending transaction 清空、`production_core=true`，菜单项与状态账本均符合选择矩阵。
- v12 AI-only 复验亦通过：`.scratch/phase5a-local-distribution/manual-user-acceptance-20261006-v12-runtime-fix/`。
- 早期 v8-v11 失败根和诊断根保留，作为历史证据；不要把它们当当前失败门。

### 自动门与清理

- 受影响 focused：`144 passed in 47.82s`。
- 全量：`4313 passed, 15 skipped, 14 warnings in 717.00s`，`QT_QPA_PLATFORM=offscreen`，exit code 0。
- `python -m ruff check pet scripts packaging features tests` 通过；`python -m ruff format --check pet scripts packaging features tests` 通过；`git diff --check` 通过。
- 清理清单：`.scratch/phase5a-local-distribution/cleanup-manifest-20261006.json`。247 个目标全部消失；已删除 221,340 个文件、19,215,410,032 B；保留根校验通过，清理后阶段目录为 9,623,836,176 B / 53,299 文件。
- 清理只在 `.scratch/phase5a-local-distribution` 内执行；未触碰源码、用户数据或项目外路径。

### 精确停点与用户动作

- 当前未提交、未推送。
- 本机无 `ISCC.exe`，真实 Inno Setup 向导仍未验证；用户需在有 Inno Setup 的 Windows 环境完成一次视觉/安装验收。
- 用户如需真实 AI/视觉请求，应在本地可信设置界面自行输入凭据；不要向 Codex、终端、日志提供密钥。
- 本次不再要求用户生成或提供 Phase5A 私钥；若未来要做正式发布签名，另建独立发布任务。

## 2026-10-07 交接补记：Setup 已在本机编译

- 编译器：`E:\tools\InnoSetup6\ISCC.exe`，Inno Setup `6.7.3`；无需安装新工具。
- 脚本：`packaging/core_webm.iss`；参数为 `CoreVersion=5.0.0`、短路径 `CoreDir=.scratch/p5a-core-20261007` 和独立 Setup 输出目录。
- 结果：`ISCC_EXIT=0`，`116.031 s`，Setup `227,324,912 B`，SHA-256 `3780ec4355e4ab0b0bce6285c8fe4aa07289877af43e5bab333f3965a5c9395b`。
- 旁置包已复制到 Setup 同级 `packages`，哈希与 manual20 包一致；清单和完整日志在 `.scratch/phase5a-local-distribution/setup-acceptance-20261007-shortpath/`。
- 首次直接编译失败的原因是原始 staging 路径最长 `343` 字符，`649` 条路径超过 `260`；短路径 staging 后最长 `242` 字符并成功。没有改动原始 Core。
- `install_not_run=true`；真实安装向导、任务勾选和卸载仍是用户最后人工门。`NotSigned` 仅表示未做正式 Authenticode，不是当前本地激活失败。

因此后续不应再安装 Inno Setup或把“找不到 ISCC.exe”当作阻塞；只需保留本次 Setup 产物并等待用户人工打开验证。


## 当前修复轮 R01–R07（2026-10-07）

用户已明确授权实施 Core 统一 API、识屏恢复及 Core-only 卸载修复。**R01 进行中，R02–R07 待实施；此前“无待修代码”仅限原 T 轮，不再是当前状态。** 人工验收已暴露缺陷，Phase5A 不可正式关闭。原 T 结果/产物哈希保留作历史，不能用于修复后的验证。

已确认 red 诊断：AI 外部设置保存后运行时旧快照；自动识屏关闭时 unchanged apply_config 取消手动请求；当前生产 Worker ZIP 正常启动缺 pet.official_features（握手前 exit 1）；Core 卸载要求先清两包且被 staging 阻止。相关基线 81 passed 但未覆盖缺陷。

本轮边界：不读真实 Key、不截真实屏幕、不收费调用/系统安装卸载、不更改代理、不清未知 staging、不提交推送、不开子智能体。保留 69 修改+9新增的既有脏树。精确停点：计划已落盘，下一步写失败回归并运行 red，尚无产品修复。

详细合同见原收尾计划末尾 R 章节。目标候选 Core4.2.2/AI1.0.2/Screen1.0.1。真实 Provider/屏幕/新 Setup 用户门仍未执行。


## R 修复轮实施检查点（2026-10-07）

- R01：四项根因回归已先 red（R01-red3.log：4 failed），产品修复后新中央接口/四类窗口合并专项 17 passed / 7.68 s。迁移/设置/余额另先 red：5 failed / 3.64 s。原 T 轮记录不作当前通过证据。
- R02：已实现 Core api_ports/api_config、独立 OS 凭据引用、通用 owner/purpose 授权、CAS+journal、显式 ApiMigration；新增 Core 设置/异步退出门及最小文字 probe。尚需边界/故障测试及全量验证。
- R03：已接入四类窗口请求前解析与校验失败保留输入、中央余额用途解析、Core 常驻入口；DLC 原入口只读/跳转仍在收口。
- R04：Worker 闭合源码已补 neutral official_features；自动配置变更已按请求取消，不旋转共享 generation；生产冻结 HELLO/READY 尚未重建验证。
- R05：系统维护入口已去除逐包事务并保留 CoreRemovalPermit 身份及 scoped 集成清理；尚需新增失败矩阵与安装器实机用户门。
- 最近相关集成 48 passed / 1 failed / 32.77 s：剩余是旧完整 Core 的设置构造接了新 API 导致兼容 probe 失败，正在按 build-owned legacy 能力修复；不能称当前全绿。
- R06/R07：未执行本轮全量/重建/实机/性能/用户验收。候选版本还未 bump，旧交付物不得用于本轮验收。
- 未提交、未推送、未使用子智能体、未读真实凭据/截图/收费调用/实际系统卸载，未清理生成物。

精确下一步：完成 DLC API 编辑跳转及 Screen 用途订阅、文件解读授权；补中央设置/迁移/Worker/卸载矩阵回归，专项通过后执行全量、负载三遍和产物硬门。


## R 修复轮检查点：专项扩展与构建空间待确认（2026-10-07）

- 已通过相关集成：R02-R05-related-5.log，74 passed / 47.50 s。此前 legacy 完整 Core 的 API 兼容错误已修复；不是沿用官方 factory 特判，新冻结 Core 走通用授权端口。
- 新边界先 red：R02-R04-boundary-red2.log，8 failed / 19 passed / 13.14 s（参数类型、迁移编辑后来源不一致、缺少生产正常启动验收入口）。对应 green2：64 passed / 27.52 s。源码 Worker 使用真实 QProcess 与 OS 租约完成 HELLO/READY/自然退出，未发屏幕或网络请求；不能替代冻结 Worker 验收。
- 余额撤权在飞/排队迟到结果先 red：R03-balance-red.log，2 failed / 5.61 s；正在实施 GUI 与后台双重授权检查。经典设置读取旧 Key 先 red：1 failed / 5.94 s；已改只读中央绑定及跳转，待合并验证。
- R06 全量第 1 次仍在运行，已有失败，且运行期间源码仍在收口：只作诊断，不作最终全量 green。Ruff、最终全量、负载三遍、性能/实机报告尚未完成。
- 构建预算检查：阶段生成物 6,191,575,960 B / 50,483 文件，6 GiB 上限仅余约 239 MiB。已向用户请求删除 acceptance-night-20261006（2,090,299,981 B）与 delivery-candidate-200（767,659,787 B）两处旧生成物；尚未得到答复，未执行任何删除。源码/阶段记录/implementation-20261007 旧 Setup/delivery-set-293 保留。不得绕过预算重建。
- R04 冻结正常启动、R06 全重建和 R07 新 Setup 用户验收均未完成；旧 T 轮产物/通过记录仅为历史。Phase5A 未关闭，未提交推送，无子智能体、真实 Key/截图/付费调用/系统卸载/代理修改。

精确下一步：合并新增消费/撤权/异常测试并修复全量实际失败，完成格式/静态门与性能报告；重建需用户明确确认上述两个精确目录的删除。
<!-- R08_OVERWRITE_SETUP_20261008 -->
## 2026-10-08 准确停点：Setup 覆盖安装修复已重建

### 已完成

- 根因已由用户本机 `core-maintenance.log` 的 `bundle_inventory` 与安装目录 85 文件/manifest 64 文件交叉确认。
- `packaging/core_webm.iss` 已加入只针对 `{app}\_internal\feature-probe` 的覆盖安装清理门。
- 红回归、绿回归、真实 Inno 隔离安装测试、ZIP 预检、Ruff、diff check、offscreen 全量 pytest 均有落盘证据。
- 新 Setup 路径与 SHA-256：`E:\AI\DSH\dsh-pet-indesktop\.scratch\phase5a-local-distribution\repair-20261008-setup-overwrite\r422\setup-overwrite-fix\dsh-pet-core-webm-setup.exe` / `8f53f5916285299dfddb7e41b5747aec5e049502722d422110b9ada8fc6fcc9b`。

### 用户下一步（只需人工做这一段）

1. 先自然退出桌宠 Core、设置窗口及可能打开的旧卸载器；不要强杀进程。
2. 备份个人数据；不要手动删除 `E:\dsh-pet-core-webm`，也不要运行旧 `setup-lifecycle`。
3. 双击新 Setup，覆盖现有 4.2.2 安装；按需勾选 AI/屏幕两个官方扩展，等待 Setup 完成并启动 Core。
4. 若 Setup 完成，按既有人工清单验证 API 一次配置、余额用途、立即聊天、手动识屏和 Core-only 卸载；重点确认原始 ZIP/源目录、配置、凭据和个人数据仍在。
5. 若仍失败，提供错误码和 `C:\Users\DELL\AppData\Roaming\dsh-pet-core-webm\core-maintenance.log` 末尾，不要提供 Key。

### 未完成/不能由自动化替代

真实 Provider/余额协议、真实屏幕识别、两种 DLC 来源的系统安装/卸载、重装后包识别仍必须由用户在新产物上确认。当前不提交、不推送、不发布，Phase5A 未关闭。

<!-- R09_POLICY_UNINSTALL_20261009 -->
## 2026-10-09 R09 最终交接点

### 已完成

1. 修正 Core-owned bridge 清理范围和 Core-only 卸载诊断；用户已退出桌宠这一事实与 `core_maintenance_incomplete:2` 不再混为一谈。
2. 先以冻结 UI 验收发现 `ALLOW_LOCAL_PACKAGE_ACTIVATION` 未进入生产 Core PYZ，再补 red/green 回归并重建 Core/Setup。
3. 新冻结交付四案 UIAutomation 全部通过；专项 `107 passed`；全量 `4420 passed, 15 skipped, 15 warnings`；Ruff/format/diff 全部通过。

### 当前可交付物

- Core SHA-256：`34F1FE81E92E2F177D568F52E6919B75CCBD2E496F4FED7A1CB525E9D5207C3F`。
- Setup SHA-256：`7FB5C31657B962F9861745E046323E29DAF22077E314CF1E256352F269C5F98F`。
- 证据目录：`E:\AI\DSH\dsh-pet-indesktop\.scratch\phase5a-local-distribution\repair-20261008-uninstall-diagnosis`；UIA 回执在 `frozen-validation-v1/frozen-results.json`。

### 下一步（仅用户人工门）

用新 Setup 覆盖安装，确认 Core 启动；然后按 API/余额/聊天即时生效、自动关时手动识屏、Setup/外部导入 DLC 保留与 Core-only 卸载顺序验收。失败时提供对话框原文和 maintenance log 脱敏末尾。不要用旧 Setup 或旧 `unins000.exe` 判断本轮结果。

### 未完成/不应误报

真实 Provider、余额数值、真实屏幕内容、系统安装器实际写入和卸载后的文件保留尚未由 Codex执行；隔离 UIAutomation 和全量 pytest 不能替代这些用户门。未提交、未推送，Phase5A 未关闭。

## S04 补充（2026-10-10，实施前登记）

- 真实冻结 Core 已从项目内 data/feature-runtime 启动生产 Worker 并完成租约/READY；但退出码 62097，不算通过。日志显示启动前包校验耗时被心跳计时计入，尚未 READY 的新进程被误报 heartbeat timeout。补真实进程 red，心跳只在 READY 后启用，握手仍由独立限时监管；继续验证自然退出。
- 首次全量 4 failed / 4486 passed / 15 skipped / 15 warnings（800.37s）；4 项是旧 4.2.3/Screen1.0.2 版本断言，更新到本次合同后重跑，高负载未启动，不记通过。
- 用户追加：新候选核对 Setup 内 EXE 与快捷方式的鲸鱼娘图标；不清全局图标缓存，不修改真实安装目录。已抽取新 Core/Setup 内10帧与 assets/icon.ico 全相同，仍需最终交付核验。
- 用户追加：API 测试按钮旁显示测试中、成功/失败和实际 HTTP 状态码；网络/TLS/超时没有 HTTP 状态时用明确错误类型，原说明放旁边备注。只改反馈 UI/探针结果携带，不改保存、密钥、API 路由或 Setup 语义；先补 red，明暗720/1100实机检查后重建 Core/Setup。

实际效果：识屏启动不误耗心跳预算；测试连接可一眼看到结果码，不把测试当保存。真实服务最终仍待用户验收。


## 2026-10-10 16:40 MOD 可运行检查点同步

- Phase5A 当前最新产品变化是本地 MOD 管理中心与作者 v1；本地 M05 检查点已完成 final4 Core/Setup 构建和冻结生产 Worker/Settings 烟测。
- 自动化收口：full12 4578 collected / 4563 passed / 15 skipped；Ruff、diff check 通过。满 CPU highload12 的已知非阻塞失败保留在 `docs/PR-REPORT-MOD-CENTER-V1-2026-10-10.md` 和 `.scratch/mod-authoring-v1/STATUS.md`。
- 本轮只做显式文件本地提交，不推送；用户人工验收仍是新 Setup 安装/升级/卸载、真实 API/余额/识屏和不同 Windows 实机行为。


## 2026-10-10 16:57 本地提交完成

- 可运行检查点已在当前分支创建本地提交；未推送远程。提交 SHA 以 `git log -1` 为准。
- 97 个显式交付文件已纳入；构建目录、日志、缓存、旧候选和真实安装目录仍未纳入。
- 用户验收边界不变：真实 Setup 安装/升级/卸载、真实 Provider/余额/屏幕，以及高负载限制的后续复验仍单独区分。
