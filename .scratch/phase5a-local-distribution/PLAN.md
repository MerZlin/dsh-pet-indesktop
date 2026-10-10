# Phase5A 连续记录：PLAN

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

### 稳定任务与收尾门

- [x] S01：同组设计/五记录先落盘，目录边界、同步故障、主动重试 red；保护原 dirty。
- [x] S02：基于可信 lease_coordinator.data_root 放行唯一 owned runtime；事务/授权/环境隔离不降级。
- [x] S03：白名单脱敏诊断先于 FAULT，气泡可操作，手动重试与租约清理。
- [x] S04-a：真实冻结链路发现并 test-first 修复 READY 前心跳；追加按钮旁结果码和主题/可访问性。
- [x] S04-b：Core/Screen/Setup 重建与闭合输入/版本/hash/图标验证；当前 Worker 输入不变后复用，AI ZIP 不变。
- [x] S04-c：最终全量及高负载三轮；不以失败或中途改码的前两次全量算通过。
- [x] S04-d：逐文件/性能/实机报告、五记录/入口/索引和最终文档检查。
- [ ] 用户门：新 Setup 同目录更新时勾选“屏幕理解”；真实手动识屏、Provider、API 反馈及实际快捷方式确认。该门不由工程自动化代签。

## 本次实际可体验的效果与限制

更新新候选后保留原配置；识屏能从项目内数据目录启动，连接按钮旁直接看成功/失败与结果码。Setup 行为不改；真实 Provider、真实屏幕和用户桌面图标仍需最后确认，Phase5A 不关闭。
<!-- S04_CURRENT_END -->

> 追加授权已完成：恢复原鲸鱼 ICO；Core/Setup PE 10 帧核验一致，安装/卸载不变。

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


## 最终执行结果（2026-10-10）



- 初始 red 10 failed；冻结验收揭示 Qt 所有权问题，新增真实 reparent/DeferredDelete red 3 failed → 相关 **60 passed**。失败日志保留，最初“可打开但报错”的冻结结果明确为失败。
- 最终全量 **4472 passed / 15 skipped / 15 warnings，611.95s**，641 输入前后 SHA 一致；三轮高负载各 **185 passed**，CPU 中位 **99.9% / 100.0% / 99.1%**，自有负载进程全部自然退出。
- Ruff 0.16.6、38 个改动 Python format、git diff --check 通过；报告纪律 63 passed。最终文档链接/输入摘要见 `a01-simple-api-20261009/A01-final-closeout.json`。
- 原生 Windows 明/暗 × 720/1100 UI；自有冻结 portable 数据根无 DLC 与双 DLC API 页都正常、自然退出 0、最终日志无 ERROR/Traceback。冻结 maintenance 安装两个新 DLC code 0，13.125s。
- 生产 Worker 正常 HELLO/READY/租约/自然退出 0；0 屏幕/0 Provider。Core 230 sources/1010 resources、两个包全部清单、Worker 输入和 PE 图标校验通过。
- 性能数字、命令及局限见报告：交错 500/路径旧绑定中位 0.83525ms、简易选择 0.84775ms（+0.0125ms），1000 次解析 retained +233B；不是 Provider 性能结论。


A01–A05 工程执行完成，原始失败保留；最终候选和准确用户步骤见 [本轮报告](../../docs/PR-REPORT-SIMPLE-API-2026-10-09.md)。无剩余实现/重建步骤，无提交推送；用户真实服务/屏幕确认前 Phase5A 不关闭。

## 完成后的实际使用效果

主 Key 测试并保存即可，视觉 Key 可选；原鲸鱼图标恢复，Setup 安装卸载步骤不变。服务能力和真实屏幕仍待用户验收。

---

# U01–U06 计划：Setup 项目目录安装与卸载

> 更新：2026-10-09T21:18:00+08:00。U01–U06 工程实现、自动化、重建和隔离实机验收完成；待用户确认真实功能体验。本合同替代下方 R/T 的安装器合同，保留历史证据，不代表 Phase5A 已关闭。

设计：[Phase5A 设计](../../docs/plugin-phase-05-distribution/PHASE5A-LOCAL-DISTRIBUTION-DESIGN.md) · [PLAN](PLAN.md) · [STATUS](STATUS.md) · [HANDOFF](HANDOFF.md) · [WORKLOG](WORKLOG.md) · [SUMMARY](SUMMARY.md) · [报告](../../docs/PR-REPORT-SETUP-PROJECT-DIRECTORY-2026-10-09.md)

## 任务状态

- [x] U01：路径页/空目录/非空陌生目录/portable marker/运行时 data/卸载边界先红；初始 `4 failed /5 passed /8 errors` 留档。
- [x] U02：构建时生成 Setup-owned marker；程序与 data 分离；内嵌 ZIP 按任务安装到项目内。
- [x] U03：绝对路径、根目录、重解析、NTFS、陌生非空目录门禁；保留数据重装收据；程序门不遍历个人数据。
- [x] U04：合法 portable removal；普通设置和桌宠入口也先获得生产代码/数据租约，再创建 Config/UI。
- [x] U05：确认后执行最小维护、best-effort 可选集成清理；默认保留 data / 可选删除 data；先校验删除树，始终删除项目内 DLC。
- [x] U06：专项、全量、三轮高负载、Ruff/diff、重建、包/hash/PYZ/正常 Worker、隔离可见桌面安装/更新/卸载、报告/阶段交接完成。

## 停止条件

任何最终全量、负载或产物核验失败都保留失败记录并调查；不能用中间绿灯替代最终源码。真实 Provider、真实屏幕与用户重要数据目录只由用户决定/验收。不提交、不推送。

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

## 任务完成条件

- [x] R01：人工缺陷登记、四根缺陷 red→green；保留原T历史。
- [x] R02：Core中央API/安全引用/用途授权/通用端口/显式CAS迁移与恢复。
- [x] R03：设置、四聊天/文件/余额接入，提交即时读取、草稿/授权快照。
- [x] R04：冻结Worker闭合依赖、真实进程握手，手动/自动生命周期分离。
- [x] R05：Core-only卸载与单包范围文案；保留状态/数据断言。
- [x] R06运行门：最新默认全量、三轮满CPU、新Core/Setup和所有交付输入/哈希审计。
- [x] R07工程证据：当前冻结四保留profile与无DLC设置、逐文件/性能/实机/人工步骤。
- [ ] R07用户确认：真实Provider/画面、两来源新系统安装卸载/重装、用户反馈；确认后才正式关闭Phase5A。

停止条件：任何新的产品/测试输入变化须重新决定全量、受影响三轮和构建门；不得引用旧hash或放松断言。
<!-- CURRENT_R_END -->

---

# 原 T01–T07 历史状态（2026-10-07，不代表当前 R 修复通过）

- 工作区：`E:\AI\DSH\dsh-pet-indesktop`；分支 `codex/phase3-worker`；HEAD `70ff464f84793f6ea3a342079dcbfb2d991fcf4e`。累计工作树保留既有与本轮 dirty diff；无暂存/提交/推送。
- 用户授权当前主对话自主完成 T01–T07 与收尾；不开子智能体，不运行真实系统 Setup，不接触真实 profile/密钥，不调用 Provider，不强杀进程，不正式发布。
- 设计：[收尾计划](../../docs/PHASE5A-CLOSEOUT-IMPLEMENTATION-PLAN-2026-10-07.md)；证据：[本轮报告](../../docs/PR-REPORT-PHASE5A-LOCAL-DISTRIBUTION-2026-10-07.md)；同组 [PLAN](PLAN.md) / [STATUS](STATUS.md) / [HANDOFF](HANDOFF.md) / [WORKLOG](WORKLOG.md) / [SUMMARY](SUMMARY.md)。历史快照保留在分隔线后，不作为当前状态。

## 稳定任务与完成证据

- [x] T01：先建立四个公开 seam 红回归，证据见报告 §3.1。
- [x] T02：中性 FeatureRegistration 与 local/official/signed 分流；未知 local owner 不走官方硬编码。
- [x] T03：通用事务/状态/凭据命名空间/租约/启动 receipt；不引入平行安装器或 Python 热卸载。
- [x] T04：独立 ZIP/目录、bounded router、exact token 自动 apply、动态第三方卡片及草稿隔离。
- [x] T05：官方两包编译时内嵌、无 QApplication 维护、重复 pending 安装重入、编译前输入验证。
- [x] T06：Core console=False 与实 PE=2；默认进程树观测、正常显示/退出通过。
- [x] T07：全量/高负载、当前 helper/Core/Worker/Setup、性能与实机报告、索引、文件表/链接/新报告门和五份终态记录均完成。

## 最新验证与本次产物

- T01 产品修改前：`4 failed in 5.24s`；中性注册/路由、Setup 内嵌、GUI spec 均先红后绿。
- 最终全量：`4334 passed, 15 skipped, 15 warnings in 759.16s`，exit 0；原始日志 `implementation-20261007/pytest-final-current.log`。首次完整的 17 个旧合同预期失败已分开记录并修正，不跳过测试；最终全量后没有产品/测试源码改动。
- 20 CPU worker 满负载三轮相关族：各 `97 passed`，pytest 200.99s / 182.02s / 177.87s；全机 CPU 中位均为 100.0%。20 worker 协作停止、自然 join，全 exit 0、残留 []。
- 最终 Ruff、63 个改动 Python format --check、git diff --check、当前 Core/source/resource 审计均 exit 0；保留 2395 行预算，现代设置实际 2377 行。
- 当前 Core root201/stage206/resources1021/bundle2126 均匹配，PE subsystem=2；helper 与非 synthetic Worker 输入核验。

- Core（manual-acceptance-only、非正式签名）：`c07/dsh-pet-core-webm.exe`，SHA-256 `0f5de807f1f758a0ab97e645fe9c252ad30ebf42a9c234ab25384f3444cc8eed`。
- Setup（已编译、未运行系统安装）：`implementation-20261007/setup-current/dsh-pet-core-webm-setup.exe`，SHA-256 `cbb08a9b21eb267464ca1f8bf8c264952cb0059815f54d2f4e8bb2dfd3fc03e4`，版本 4.2.1。
- helper manifest digest `376a30a368c6051660f166eb5ce555cc7c679be2825263d8911a6b6b63665562`；AI 1.0.1 / Screen 1.0.0 ZIP 内嵌到本次 Setup。Core 禁系统自启动注册，其他正常产品启动/管理逻辑保留。

## 授权实机与性能

- 冻结 empty/AI/Screen/both 四种自有 profile：无界面维护（empty 不执行）→正常 GUI Core→startup receipt 清除→对应菜单出现/缺席→自然退出 0；不冒充 Inno Setup 四种人工勾选。
- third-party.example：真冻结 LPAC helper 的目录/ZIP 安装、真实双进程启动/重启、停用/卸载全过。额外当前冻结 Core ZIP 首次启动/重启均通过，revision=4、pending=null；自然退出后停用/卸载 completed、revision=7、versions={}，没有模拟热卸载。
- 默认创建参数（creationflags=0）下自有进程树 62 次观测无可见终端，ffmpeg ConsoleWindowClass visible=false；自然菜单退出 0。不等同 Explorer 人工双击。
- 原生 UI 的 720/1100、浅/深色四张截图已审查；ZIP/目录入口可见可用、横向滚动 0；宽布局滚动遮罩略裁节标题是已记限制。
- 路由/完整验证 50 样本及 empty/third 两组 30s settle 后 3×5s Core PID 数据已落报告；third 15.043481s 的 RSS 净 +892928 B，只是短样本，不能据此宣称长期零泄漏/可归因性能改善。

## 当前终态与后续边界

<!-- FINAL_DOCS_VERIFICATION -->
最后新报告专项 `59 passed`，exit 0；13 份相关 Markdown 的 267 个相对链接无断链、无尾随空白。累计 69 修改 + 9 新增的 78 项文件说明/行数已核对，无删除、无暂存；20 负载 worker 全 exit 0、残留 []，5 个已记录自有进程身份复核无残留，未枚举/操控其他用户进程。结果 `implementation-20261007/final-document-verification.json`。
<!-- FINAL_DOCS_VERIFICATION_END -->

无新增确认项；当前授权范围无剩余执行步骤。后续只能按下列独立授权的人工矩阵继续，不是本轮未完代码：

1. 真实系统 Setup 的四种勾选、注册表/快捷方式、卸载/重装；不在本轮执行授权内。
2. 冻结文件选择器人工导入、真实用户第三方业务、Explorer 双击、主观文案/视觉确认。
3. 真实 Provider 文字/视觉请求（密钥、费用、真实截图）；第三方 localhost-worker 复杂业务及 macOS/Linux 实机。
4. Authenticode/SmartScreen、正式发布以及 Git 暂存/提交/推送/远端验证；必须后续独立授权。

## 实际使用效果与限制

受信任本地 ZIP/目录按 manifest 自身 owner/factory 分流，不再必须进入官方集合；统一入口保留完整校验、事务、租约和启动确认。Setup 已编译为内嵌官方两包，主 Core 是 GUI PE。Python 包不是沙箱，已加载版本等原进程自然退出再升级/卸载；本轮工程收尾不等于所有系统安装和人工发布门通过。

---

## 以下为历史快照，不代表当前实施状态

## 以下为先前施工与文档准备快照（历史，不是当前状态）

# 2026-10-07 当前权威计划：T01 红测试已建立，进入中性注册/验证实现

> 本节覆盖文档清理后的代码实现续接点；下方历史计划保留，不作为当前完成证明。

## 当前停点

- 已按用户要求先阅读 `docs/PROJECT-ENTRY.md`、Phase5A 五份阶段记录及两份 2026-10-07 实施文档。
- 已新增 `tests/test_phase5a_t01_regressions.py`，以新合同建立 T01 失败回归：unknown owner local v2、AI ZIP 从 Screen 入口按 manifest 路由、Setup 不依赖 `{src}\packages`、Core spec 使用 `console=False`。
- 红测试命令：`$env:QT_QPA_PLATFORM='offscreen'; python -m pytest -q tests/test_phase5a_t01_regressions.py`。
- 实际结果：`4 failed in 5.24s`；失败原因分别暴露 official owner 硬校验、缺少 manifest router、Setup 仍走外部 `packages`、生成 spec 仍为 `console=True`。
- 当前未提交、未推送；没有覆盖原有脏改动，也没有创建子智能体。

## 当前执行顺序

1. T02：引入/复用中性 `FeatureRegistration`，让 local verifier 从 manifest registration 取得 owner/factory/execution kind；official/signature 策略继续保留官方白名单。
2. T03：贯通 transaction、install state、version lease、startup receipt、loader/host/worker 的 descriptor 驱动路径，并把 unknown owner 的 verify/install/start 回归转绿。
3. T04：实现 ZIP/目录统一的 bounded manifest router；AI 包无论从 Screen 入口还是专用入口都按 manifest 自动路由。
4. T05：重写 Setup 内嵌官方 ZIP、无 QApplication 的维护 apply 和返回码，移除同级 `packages` 依赖。
5. T06：Core spec `console=False`，再验证最终 PE GUI subsystem。
6. T07：聚焦/相关/全量测试、Ruff、高负载时序测试、Core/Setup 构建、实机短路径和三份交付证据。

## 边界

- 保留当前 dirty worktree；不使用 `reset --hard`、不覆盖用户改动、不提交、不推送。
- 不读取或保存真实 API/private key/profile/data；只用测试夹具和项目自有 staging。
- Setup 官方包仍可使用官方 registry；普通本地导入不得再由 `official_feature()` 决定 owner 是否存在。

## 用户可见效果与限制

完成后，用户选择的本地 ZIP/目录会按自身 manifest 自动进入对应功能管理路径，未知 owner 可作为本地可信第三方 DLC；当前仍只有 T01 红测试，功能尚未宣称完成。

---

# 2026-10-07 当前权威计划：文档/清理已交付，代码实现留给下一对话

> 本节是当前可继续工作的唯一有效停点；下方内容保留为历史施工记录，不能覆盖本节的产品边界、任务顺序和未完成状态。
>
> 本轮用户明确要求：先完成计划文档、阶段文档同步和 `.scratch` 旧产物清理；具体代码实现、测试、Core/Setup 重建与真实安装验收交给新对话。本轮没有修改产品源码、测试源码或构建脚本。

## 当前产品合同

- **Setup 内选装**：只提供官方 AI / Screen 两个包。包直接嵌入 Setup，安装向导勾选后由无界面维护入口自动导入、apply、写入启用状态；不依赖 Setup 同级 `packages`，不要求公钥、私钥或 `manifest.sig`。
- **Setup 外导入**：用户明确选择 ZIP 或目录后读取受限大小的 `manifest.json`，按 `id`、`factory`、`execution_kind` 和兼容字段自动路由。未知 owner 只要满足本地包格式、Core/API/平台兼容、文件清单/摘要、路径和运行时契约，也作为第三方 DLC 接受。
- **信任边界**：主动选择本地包代表用户信任其 Python host/Worker；这不是 Python 沙箱，也不是发布者认证。路径穿越、重解析点、文件数量/大小、manifest 清单 SHA-256、事务、版本 lease 和启动确认仍保留。
- **Phase6 边界**：在线目录、公开生态、签名、撤销、审核、公钥轮换和权限模型不在本轮；Phase5A 的本地可信 DLC 不得写成公开生态已完成。

## 当前任务状态

### 已完成（本轮）

- [x] 计划与详细实现蓝图已写入 [`docs/PHASE5A-CLOSEOUT-IMPLEMENTATION-PLAN-2026-10-07.md`](../../docs/PHASE5A-CLOSEOUT-IMPLEMENTATION-PLAN-2026-10-07.md)。
- [x] 下一对话代码交付说明已写入 [`docs/PHASE5A-CODE-IMPLEMENTATION-HANDOFF-2026-10-07.md`](../../docs/PHASE5A-CODE-IMPLEMENTATION-HANDOFF-2026-10-07.md)。
- [x] `docs/PROJECT-ENTRY.md`、Phase5A 设计/README、Phase6 生态说明、`docs/INDEX.md` 和 PR 报告已同步新边界，并明确历史证据不等于新实现通过。
- [x] `.scratch/phase5a-local-distribution` 中明确过时的旧构建/诊断根已清理；五份阶段记录保留并已补本节。
- [x] 未提交、未推送、未发布；已有脏工作树的源码/测试改动全部保留。

### 下一对话按稳定编号执行

- [ ] **T01**：建立源码/产物保护基线，先补失败回归。
- [ ] **T02**：抽出/复用中性 `FeatureRegistration`，拆分 official 与 local verifier 策略。
- [ ] **T03**：贯通 transaction、install state、version lease、startup receipt、loader/host/worker 的 descriptor 驱动路径。
- [ ] **T04**：统一 ZIP/目录 manifest router，自动选择 manager，接入第三方 DLC 列表、启用、停用、卸载、回滚和诊断。
- [ ] **T05**：重写 Setup 内嵌官方 ZIP、无界面维护 apply、返回码和首次启动 receipt；移除同级 `packages` 依赖。
- [ ] **T06**：Core PyInstaller `console=False`，并验证最终 PE 为 Windows GUI subsystem。
- [ ] **T07**：专项/相关/全量测试、Ruff/format、Core/Setup 重建、真实矩阵、PR 报告和阶段记录收口。

### 本轮没有执行的门

- 代码红绿测试、Ruff、全量 `python -m pytest -q`；
- Core/Worker/Helper 新方案重建、Inno Setup 新方案重建；
- Setup 向导、首次启动、ZIP/目录导入、第三方 DLC 启停卸载、无终端窗口的实机人工验收；
- 真实 AI Provider 请求、屏幕理解 API Key、截图识别和视觉体验。

## 保护与提交边界

- 工作区：`E:\AI\DSH\dsh-pet-indesktop`；当前分支：`codex/phase3-worker`。
- 不使用 `git reset --hard`、不覆盖用户脏改动、不删除源码/测试/真实用户数据；不提交、不推送。
- `.scratch/phase4b-local-management`、`.scratch` 其他阶段、当前 Setup/最终验收证据和五份阶段记录均不作为清理对象。
- 继续实施前先读项目入口、新计划、新交付文档和本目录 `STATUS.md` / `HANDOFF.md` / `SUMMARY.md`，再核对 `git status --short`。

## 完成后的实际使用效果

本轮交付的是“可被新对话直接执行的 Phase5A 重构合同和干净的阶段记录”，不是功能已完成声明。用户下一次启动实现对话后，应从 T01 开始，把 Setup 官方选装、Setup 外第三方 ZIP/目录导入和隐藏 Core 终端逐项落地并重新验收。

---

## 2026-10-06 续接进度（推送后）

- [x] 已有检查点按白名单提交并推送：`70ff464f84793f6ea3a342079dcbfb2d991fcf4e`，远端复核一致。
- [x] `Py_SetPath` 分号路径失败关闭：先红后绿，`20 passed in 0.72s`。
- [x] 既有冻结 Worker04 真实 lease handoff：HELLO/SHUTDOWN/exit0、无 token 77 拒绝、退出后占用 free。
- [x] 受影响专项 `267 passed, 2 skipped`；全量 `4298 passed, 15 skipped, 146 warnings`（显式 `PYTHONWARNINGS=default`）。
- [ ] 本地新增源码/测试尚未重新提交/推送；新 helper/Core/Worker 正式重建受当前 12 GiB 峰值合同约束，未擅自扩大或删除受保护材料。
- [ ] Core04 双包 `both` 组合、正式重签/Setup/ZIP/便携、真实安装更新卸载、迁移、干净环境和人工模型门仍未完成。

## 2026-10-06 最新子门

- [x] V1a：身份绑定驱动与实际 AI 正常启动／自然退出；9 项驱动测试。
- [x] V1b：可见 owner 标题公开 seam red/green；23 项 Qt 布局相关测试。
- [x] V6a：上述累计源码全量 4294 passed / 15 skipped（796.84s）。
- [ ] V3a：真实深目录 Worker getpath 故障根因与修复；诊断 native-home-debug 构建进行中。
- [ ] V2/V4/V5/V6：其他完整矩阵、Setup 人工、新性能样本与高负载门仍按原计划，未伪报完成。

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

# Phase 5A / 5B-1 任务清单

2026-10-04；基线 e2687be，分支 codex/phase3-worker。设计：[正式合同](../../docs/plugin-phase-05-distribution/PHASE5A-LOCAL-DISTRIBUTION-DESIGN.md)。

- [x] 5A-0 复核干净基线、保存白名单快照、落盘记录和保护范围
- [ ] 5A-1 双包身份、manifest v2、host-only、receipt及UX-M3
- [ ] 5A-2 RuntimeLayout、凭据稳定身份、便携和显式导入
- [ ] 5B-1 AI完整拆包和线程生命周期
- [ ] 5A-3 正式签名工具、LPAC和生产构建
- [ ] 5A-4 Setup、更新、Core卸载
- [ ] 5A-5 双包管理与布局
- [ ] 5A-6 冻结/实机/人工/干净环境
- [ ] 5A-CLOSE 全量质量、性能、报告与交接

当前生成峰值8GiB（2026-10-05用户追加授权，原6GiB保留历史）；旧人工构建/数据保护；不提交/推送/发布/子智能体。

## 实际效果
5A-1/5A-2 基础实现及专项测试已有证据，完整阶段未完成；已有正式签名候选与冻结构建，最终累计运行及分发门待验收。

## 2026-10-04 进行中切片
- [x] 双包 v2/host-only/owner 合同专项红绿
- [x] RuntimeLayout 与生成夹具的稳定凭据访问专项红绿
- [x] 正常 Core 短暂管理锁竞争自动加载重试专项红绿
- [x] Core 双 owner 接线与加载 receipt 锁竞争恢复（普通设置/UI 未齐）
- [x] 普通 JSON 导入后端红绿（凭据授权迁移/附件/入口未齐）
- [x] 请求非阻塞取消/代际隔离及实际 QApplication Quit 门专项红绿
- [x] 会话非阻塞排空接口及写盘失败专项红绿（实际 host 接线未完成）
- [ ] 完整 AI 物理拆包和生命周期接线

证据参见 WORKLOG.md；专项通过不代表 5A-1/5A-2 完成。

## 2026-10-04 连续实施补充
会话关闭异常与重叠恢复专项已转绿（30 passed /5.73s）；双包管理 UI 已接入独立 owner、结果与草稿隔离（54 passed /92.63s）。AI 实现源码已迁至 features/ai_chat/host，旧模块仅保留明确 BUILTIN_AI 门控的兼容别名，不移动旧冻结程序或数据。迁移及相关专项 53 passed /1 deselected /8.50s；被排除的是新增 lazy factory 合同，尚未实现，不能标记完整拆包或生产可用。
下一步：实现真实惰性 factory、owner 配置/凭据适配和请求/会话生命周期；运行 Ruff 与相关回归。正式密钥、生产构建、Setup、实际导入/人工/干净环境和完整质量门仍未执行。

## 2026-10-04 当前追加进展（AI host/设置接入）
已实现 Qt 无关的惰性 AI factory、owner 配置 CAS/安全凭据适配、真实请求/会话生命周期和普通设置贡献挂载；小 Core 禁止旧源码回退。独立设置子进程的原生退出定位为测试未关闭调用方拥有的管理监控线程，补真实关闭后 5 passed /2.75s，不改产品所有权合同。
新增四个失败回归（4 failed /4 passed /1.20s）后修正：重挂设置行不重建 QObject、聊天/文件设置一次 CAS、停用 AI 的新输入 Key 不能绕过测试授权、延迟布局贴底且保留用户上翻。当前专项 15 passed /6.79s。
相关累计回归曾为 282 passed、1 skipped、1 failed /352.55s，失败为聊天贴底，现 focused 转绿；该相关族仍需重新全跑。实际 AI 全窗口/快捷/灵动岛和文件理解路由、正式签名/构建/Setup、人工和干净环境仍未完成。正在补写盘排空中重新启用及共享排空恢复回归；全量、mypy、高负载和冻结验收尚未执行。
无新增实际密钥/真实导入/真实安装/卸载；全部未提交 WIP，保护范围和 6 GiB 上限不变。

## 2026-10-05 当前切片（历史未执行表述以上述日期为准）
- [x] AI 惰性 factory、owner 配置、真实窗口路由与请求/会话生命周期接线；专项/相关证据见 WORKLOG。
- [x] 双包管理及普通设置贡献挂载；不是完整 5A-5。
- [x] 离线签名/分发/本地密码 GUI 工具的生成夹具红绿。
- [x] 全量六失败复现、根因修复与相关 213 passed；全量曾失败，待复跑。
- [ ] 生产材料组装与闭集 Core 依赖审计（当前 10 failed，缺实现）。
- [ ] opaque AI 配置与独立余额边界、生产冻结及其余阶段。
当前验收权威见 STATUS.md，准确下一条操作见 HANDOFF.md。

## 2026-10-05T00:58:51+08:00 当前切片进度（不覆盖原验收门）

- [x] 无执行 unsigned 材料/闭合来源/预算 seam。
- [x] 小 Core 旧 AI 配置 opaque，AI 默认策略归属 owner。
- [x] Core 余额独立 vault/CAS/UI 与 AgentLink 路由，AI 背景 owner 资源。
- [ ] 生产 helper/Worker/Core 实际构建和审计；正式信任须另行确认。
- [ ] 全量41结果、最终 Ruff/mypy/高负载和交付矩阵。


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



## 当前实施状态（UTC 本机观测 2026-10-04T19:07:11+00:00）

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


## 实施增量（UTC 2026-10-04T19:50:29+00:00）

- [x] 卸载跨副本持续冻结边界及维护父端固定根证明：green107/114。
- [x] 会话映射对接真实 AI 数据根，结构化秘密字段拒绝：green112。
- [x] 明确来源凭据引用迁移公开 seam：red115 7 failed（未实现）→ green116 4 failed/64 passed（JSON journal 误共享 dataclass 数据）→ green117 68 passed；复制 plan 字典后保留 JSON 序列化合同。red118 2 failed/9 passed（来源 UUID 未在接受前绑定、余额引用尚不支持）→ green119 81 passed/8.88s。
- [ ] 导入 UI/实际入口、附件迁移及新代码 Ruff/mypy，进行中。
- [ ] full108 不是最终门：1 failed/4111 passed/14 skipped，测试执行中改动造成混合源码版本；稳定后重跑。
- 限制：旧原始 vision 引用兼容保留，不等于所有旧屏幕 profile 已自动重映射；不认识的秘密 schema 安全拒绝。未生成正式密钥，未真实导入/安装/卸载，无提交/推送。


## 实施增量（UTC 2026-10-04T20:32:05+00:00）

- [x] 5A-2/5A-5 显式导入界面、凭据授权提示、后台排空与关闭窗口后异步安全的公开 seam 专项。
- [x] 新冻结 Core 专用 --import-local-data 入口与常规数据交付命令；搜索/真实 data-import 深链。
- [x] 管理锁/普通数据锁分离：正常 Core 在运行时也能取消未接受预检；取消管理锁竞争后重试只取消，不重新确认；接受后禁止撤回。
- [x] red126→127→green128（121/12.07s）；red129→green130（104/13.37s）；修正错误夹具 red131 后产品 red132→green133（179/62.46s）。Ruff/format134 139文件，mypy134 56文件通过。
- [ ] 固定源码全量134运行中，未计通过；之后高负载三遍、性能实测与正式生产材料审计仍待执行。
- [ ] 正式密钥/正式锚/生产 Core+两包+Setup+ZIP/完整数据与附件导入/真实安装及人工、干净环境门尚未完成。
- 当前空间134为1,490,975,073B/6GiB；有界 no-follow 计数，不沿负向测试链接走出根。没有自动清理旧产物；red91事故仍保留，不以新通过掩盖。

### 本步实际效果与限制
普通设置可以找到显式数据导入工具，用户预检后看到单一来源、映射与凭据授权；未确认不写目标普通数据，已接受中断可恢复。当前是源码/生成夹具验证，不是正式冻结分发或真实个人数据迁移验收；自定义媒体与托管附件尚未交付。


## 验证与准确停点增量（UTC 2026-10-04T20:54:56+00:00）

- [x] 固定源码全量134：4171 passed /14 skipped /14 warnings /701.35s，退出0；执行结束后未改产品/测试Python。
- [x] Qt/IPC/进程族真实CPU高负载136连续三遍：178 passed各遍，87.67/99.89/92.87s；CPU median/p95各100%，20个自有负载进程已完整回收。
- [x] 源码生成数据基准135：预检20样本41.052/115.257ms，apply10样本168.159/382.651ms，恢复10样本79.154/121.167ms；计数/RSS/线程/句柄附报告，不冒充冻结性能。
- [x] 报告和入口/路线图当前状态保存；report139 101 passed/0.76s，docs139 129文件通过，diff139退出0。
- [ ] 正式密钥、正式生产材料/完整冻结验收尚未进行；再次确认目标后才执行创建，不把测试锚转为正式可信。
- [ ] 自定义媒体、托管附件、外部路径提示及新产品自启动清理仍需补齐；真实安装、用户/干净环境门仍未验收。
- 空间138 1,645,640,384B/6,442,450,944B，未清理旧产物或真实数据；red91事故和网络负项阶段限制持续公开保留。

### 本步实际效果与限制
源码工程门与生成夹具高负载测试已通过，证据报告和准确交接已保存；这不等于Phase5正式交付已完成。用户下一步只需确认外仓密钥/公开政策创建目标，不发送密码；正式构建和剩余功能/人工步骤继续由代理负责。


## 正式密钥创建授权（UTC 2026-10-04T21:55:26+00:00）

用户在本轮明确回复“确认”，授权仅限新建 E:/AI/DSH/release-signing/feature-release-ed25519.pem、E:/AI/DSH/release-signing/feature-release-public-policy.json，key_id official-release-2026；不覆盖既有文件，不授权真实数据导入/安装卸载/提交推送发布。两目标及release-signing父目录在启动前均不存在；父路径E:/AI/DSH为普通目录。库写入采用独占创建，私钥加密PKCS#8，密码只在本地masked Qt窗口输入，无密码argv/env/log。

启动前生成夹具专项 key-preflight141：20 passed/1.62s（签名CLI、加密密钥、分发政策），显式自有APPDATA和独立pytest根。此记录只证明授权和预检，尚未启动密码窗口，不标记正式密钥已创建。后续只读取公开政策/公钥指纹和进程结果，不把私钥内容输出到聊天或日志。

### 当前实际效果与限制
正式目标已获确认，下一动作是启动本地密码界面；用户仍需在本机输入并确认密码，未完成该步骤前不标记创建成功。


## 本地签名窗口启动（UTC 2026-10-04T21:58:40+00:00）

- [x] 已取得上述两目标和key_id的本次明确授权；生成夹具专项141 20 passed/1.62s。
- [x] 签名CLI PID 37572 的自身密码窗口 visible已核验；首次隐藏窗口只恢复该窗口可见，不重启进程或读取密码。
- [ ] 用户本地密码输入、准确进程完成/公开策略指纹核验尚待执行；窗口核验时两目标尚不存在，不标记创建成功。
- 证据 signing-launch-142/launcher-receipt.json、window-receipt.json；只记录公开元数据，密码不入聊天/argv/env/log。没有授权真实安装迁移或发布。

### 当前实际效果与限制
本地窗口可以完成新密钥创建，代理不代填密码。用户完成后，代理核验公共结果继续实施；创建成功不是生产Core/分发验收成功。


## 当前停点：正式密钥创建已核对（UTC 2026-10-05T01:14:39.201695+00:00）

- 用户本轮回复“已输入”，已完成之前授权的本地密码操作。可信CLI公开成功记录与正式公开策略一致，诊断日志0字节；私钥文件302B、创建UTC 2026-10-05T01:09:19.881520+00:00。仅检查私钥元数据，没有读取/展示私钥内容。
- key_id `official-release-2026`，公钥指纹 `dd18cbd51d2c2367e45efe7ec697a5e27e9b1654e54137f5f2734eec80f4d9ab`；公开策略限定两个官方owner及现有能力，未撤销。证据：`signing-launch-142/key-creation-receipt.json`。脱离式启动没有捕获退出码，不能补写退出0；可信CLI仅在两个文件成功写入后输出该成功记录。
- 正式加密密钥/公开政策位于仓库外 `E:/AI/DSH/release-signing/`；备份副本、解密/恢复检查、正式签包/冻结Core/分发验收尚未执行。创建授权不延伸到真实数据导入、安装卸载或发布。
- 下一步先补生产PYZ必须包含导入/维护真实入口的失败回归，以及LPAC网络canary必须实际抵达Winsock连接调用的证据，保留安全边界；随后继续剩余资源导入、新产品自启动清理和正式构建。
- Phase5A/5B-1仍未完整交付。既有full134/负载136为修改前源码历史；无提交/推送/发布/子智能体。

### 当前实际效果与限制
密码操作已完成，本轮不需要在聊天提供任何秘密；尚不能把新小Core视为正式交付。以下密码等待状态为当时的历史事实，不是当前状态。

---



## 当前停点：正式构建候选与待签名材料（UTC 2026-10-05T02:01:59.460059+00:00）

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



## 当前停点：正式双包签名与原生自检已通过（UTC 2026-10-05T02:27:29.577861+00:00）

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


## 2026-10-05 当前累计里程碑（203）
- [x] 正式双包签名178及原生LPAC185
- [x] core-02实际冻结审计；四组合真实UI安装/普通启动确认/贡献与自然退出195、201
- [x] 流式档案构建合同红绿199及普通/便携/两DLC ZIP200
- [x] 新产品Setup生产候选真实编译202（非安装验收）
- [x] Agent全局注册拥有边界与Core收尾源码回归212（267passed/1skip）；冻结候选仍需重建
- [ ] 总分发清单签名、累计全量/负载/性能、Setup/便携/请求/人工/干净环境门
空间203余1,273,032,471B；任何后续复制前预估。阶段顶层仍未完整勾选。

## 2026-10-05 累计切片212
- [x] Agent scoped hooks/bridge、Core收尾公开seam红绿及3源mypy
- [ ] 清理本轮core-01后重建Core03；当前full134/高负载136仅为历史

当前仍非完整分发验收，不操作真实用户安装或凭据。


## 2026-10-05 12:20 累计验证补充（仍未完整交付）

- [x] 5A-CLOSE 源码稳定全量214：4258 passed /14 skipped /15 warnings /816.22s。
- [x] 5A-CLOSE 受影响时序族满CPU三遍223：3×227 passed，CPU median/p95均100%。
- [x] 5A-3 新冻结helper05源码一致性、原生canary224与正式双包LPAC225通过。
- [x] 5A-2 生成NTFS夹具同卷移动、稳定数据根身份与真实OS生成凭据恢复218；非最终冻结便携门。
- [x] 5A-CLOSE 正式目录/ZIP安全预检4×20次及80次未接受取消217；其余性能样本尚未齐。
- [ ] 5A-3 最终Core03与新helper嵌入：213清理政策拒绝且未删除，2GiB构建预留不足；不能绕过6GiB门。
- [ ] 5A-6 冻结启动10次：219/220自动菜单未找到，仅自有PID36460待自然退出，0个通过样本。
- [ ] 扩大受影响mypy、更新后文档/报告纪律；最终Core/Setup矩阵、业务请求、便携及人工门继续。

### 当前实际效果与限制
新增证据只补源码、沙箱和生成夹具验证；最新生产产物、真实安装和用户体验尚不能宣称完成。正式签名已完成，不再等待创建密钥；私钥备份新目标与真实安装/导入仍要单独确认。


## 2026-10-05 12:35 最新源码门

- [x] 卸载窗原生scroll公开接口229红→230相关186 passed/53.93s；回执可空类型明确，布局和事务不变。
- [x] 230受影响pet/scripts 56源mypy与231 AI host全26源mypy通过。
- [ ] 窗口修改后稳定全量234与满CPU233三遍正在运行，不能借用214/223宣布最终累计通过。
- [ ] Core03含第4个过期模块core_uninstall_ui修复；空间门未解决，不构建或绕过清理拒绝。


## 2026-10-05 最新累计收齐237/238（仍未正式交付）

- [x] 卸载窗原生Qt接口229红→230绿；56+26受影响源mypy通过。
- [x] 全量234：4258 passed /14 skipped /14 warnings /861.35s，执行期间源码哈希未变。
- [x] 真满载233三遍：各227 passed，109.71/122.16/128.58s，CPU median/p95均100%，自有负载回收。
- [x] 新helper05权限矩阵224及正式双包LPAC225；端到端probe各n=1，仅阶段证据。
- [ ] 最终Core03：空间237余570,548,000B，不满足2GiB预留；不得绕过213清理拒绝或扩大清理范围。
- [ ] 最终冻结请求/升级/自然退出/卸载/便携、Setup实装、分发总签名及性能样本门。
- [ ] 用户真实体验、干净用户/机器、签名备份恢复与发布者门，未执行不得勾选。

实际效果：最新源码已通过累计回归与真实CPU满载三遍；最终安装交付仍待环境/空间条件和生产验收，不把旧Core02当新成果。


## 241/242 新清理白名单提案（UTC 2026-10-05T05:00:38.497955+00:00；仅盘点、待确认）

只用于释放本轮重复生成物；不自动执行。不是被拒绝213/core-01的重试，没有绕过工具政策。下列8项全部位于本任务拥有根，metadata检查未见symlink/reparse/hardlink；这只是必要条件，不能替代拥有证据、进程/句柄检查及目标授权。删前保留结果/manifest/摘要证据，来源不明或被占用即停止该项；只在工具允许时以同一原生PowerShell和LiteralPath操作。

| 绝对目标 | bytes | 用途与影响 |
|---|---:|---|
| `E:\AI\DSH\dsh-pet-indesktop\.scratch\phase5a-local-distribution\delivery-candidate-200\dsh-pet-core-webm.zip` | 259,350,992 | Core02历史ZIP/Setup候选，源码已落后，后续需由Core03重新生成；两DLC ZIP不删 |
| `E:\AI\DSH\dsh-pet-indesktop\.scratch\phase5a-local-distribution\delivery-candidate-200\dsh-pet-core-webm-portable.zip` | 259,351,159 | Core02历史ZIP/Setup候选，源码已落后，后续需由Core03重新生成；两DLC ZIP不删 |
| `E:\AI\DSH\dsh-pet-indesktop\.scratch\phase5a-local-distribution\delivery-candidate-200\dsh-pet-core-webm-setup.exe` | 222,165,149 | Core02历史ZIP/Setup候选，源码已落后，后续需由Core03重新生成；两DLC ZIP不删 |
| `E:\AI\DSH\dsh-pet-indesktop\.scratch\phase5a-local-distribution\production-builds\host-integration-02` | 172,770,402 | 已完成的旧生成host/探针材料副本；不含用户profile，不是当前helper05或正式签名源包 |
| `E:\AI\DSH\dsh-pet-indesktop\.scratch\phase5a-local-distribution\production-builds\host-integration-03` | 256,542,857 | 已完成的旧生成host/探针材料副本；不含用户profile，不是当前helper05或正式签名源包 |
| `E:\AI\DSH\dsh-pet-indesktop\.scratch\phase5a-local-distribution\production-builds\probe-canary-02` | 138,127,050 | 已完成的旧生成host/探针材料副本；不含用户profile，不是当前helper05或正式签名源包 |
| `E:\AI\DSH\dsh-pet-indesktop\.scratch\phase5a-local-distribution\production-builds\probe-canary-04` | 138,155,519 | 已完成的旧生成host/探针材料副本；不含用户profile，不是当前helper05或正式签名源包 |
| `E:\AI\DSH\dsh-pet-indesktop\.scratch\phase5a-local-distribution\formal-lpac-185` | 187,437,760 | 已完成的旧生成host/探针材料副本；不含用户profile，不是当前helper05或正式签名源包 |

合计1,633,900,888B（约1.52GiB）。空间237余570,548,000B，若全部安全释放才有2,204,448,888B；仅比2GiB预留多56,965,240B，且后续新文件需再扣减，必须在实际构建前重新盘点。任何提案数字都不是实际已清理结果。

明确保留：所有源码/任务记录、报告/原始结果与摘要、正式签名两包及其ZIP、helper05、Worker02、当前Core02、拒绝213的core-01、旧Phase4B人工验收目录、真实AppData/profile/凭据/事故根、仓库外加密私钥与公钥策略。PID36460仅为本轮空包测试Core，等待用户用它自己的退出菜单自然关闭，不强退。

### 最新质量与实际使用效果

239/240已收齐Ruff、151format、mypy26/56/26、129文档、165报告/构建回归及diff-check；239 user-site Ruff路径失败保留，240原生既有工具通过。full234与满载233已收齐。Phase5A/5B-1仍未交付：没有实际清理、最终Core03或正式Setup安装，用户现在不应安装旧候选。下一步是明确安全清理条件与最终冻结闭环，不是再次输入密码。

## 243～248 收尾复核与下一切片（UTC 2026-10-05T05:22:53.605378+00:00）

## 当前停点：文本复验完成，等待限定空间清理确认（UTC 2026-10-05T05:22:53.605378+00:00）

- 用户“已输入”已用于正式加密密钥与双DLC签名，未再次创建密钥、不读取私钥、不通过聊天索取秘密。分支codex/phase3-worker / e2687be，原WIP全部保留，暂存为空；无提交、推送、发布或子智能体。
- 最新完整Python累计门仍为full234：4258 passed /14 skipped /14 warnings /861.35s，运行期间源码不变。真实满CPU233三遍各227 passed；Ruff/151 format、配置26/受影响56/AI host26 mypy、129文档及165相关门已通过。239 Ruff模块路径失败保留，240使用既有绝对路径原生工具通过，未注入PYTHONPATH或重装。
- 243最终补充检查：文档129、报告101、tracked diff-check通过，但发现新文件3处空白（AI QSS空EOF与Setup英文消息2处行末空格），因此该追加审计总体退出1，不能写成整体通过。244首次修正按LF检查原始CRLF而失败，发生在所有写入之前；245保留原行尾方式，只修上述文本，并把消息分隔符写在Pascal字符串连接处。
- 246文本相关回归190 passed /1 skipped /140.61s；文档129、报告101、tracked diff-check与所有新增文本空白复核通过。pet/features/scripts/tests的Python源码与full234哈希一致。没有新增Python逻辑、配置、线程或持久化变更，故这次纯空白/提示分隔符切片采用专项+相关验证，不重复14分钟全量；不能把full234说成测试了未来改动。
- 已签名AI源包和ZIP均未改写；AI仓库QSS少1空行使最终payload哈希需要重新生成并签名，现有正式包作为历史候选保留，不原地修签名。Core02落后4处Core源且带旧helper04，Core03必须重建并携带helper05；Worker02与screen签包未因文本修正变化。最终统一签名另走可信本地解锁，当前不再索要密码。
- 空间247只读测量：5,901,946,176B，余540,504,768B，29个reparse剪枝未跟随；不足2,147,483,648B的Core03预留。241八项目标1,633,900,888B只是提案，未删除，须删前再核对边界/拥有/占用、保留结果并获得目标确认且工具允许。213/core-01拒绝不重试、不绕过，旧Phase4B/真实profile/凭据/事故根始终保护。
- 247确认仅本轮空包测试Core PID36460仍驻留，exe与创建时间匹配，APPDATA为bench-frozen-core-219/APPDATA。不强退；用户可使用这个测试Core自己的退出菜单自然退出。219启动性能仍0有效样本，不能写达标。
- OPS-PROBE-MATERIALS（Status: ready-for-agent）：生产run保留自检文件快照，现有cleanup_owned_probe只恢复记录profile，未被pet生产调用；事务GC仅处理已退役versions。实际成功225材料仍在，确认文件积累缺口，但未做生产崩溃profile泄漏实测，也不等于隔离突破。下一切片先在公开seam补红测：活进程不得清理、已释放的明确拥有材料有界回收、未知/证据冲突/链接不删除、清理失败留可恢复记录；接入恢复只信拥有记录和真实释放，不能全根扫描猜测归属。新逻辑会要求重跑全量/高负载及重建。
- 准确下一步：请求用户确认PLAN241八项明确白名单；仅在原生工具允许、安全边界/占用证明成立时处理，并重新核对6GiB/2GiB空间门。同步补有界probe材料恢复后再最终Core03、双包和分发签名、冻结请求/升级/卸载/便携/Setup闭环。真实安装卸载/数据导入/密钥备份新目标另行确认；人工/干净环境、Authenticode/SmartScreen/Inno许可仍未验收。

### 当前实际效果与限制

本轮只完善源码文本与证据，没有新增安装、删除或真实数据操作。正式签名与安全自检已有阶段证据，但最终Core、安装分发和Phase5A/5B-1仍未交付，不能安装旧候选冒充完成。

## 250～253 最后证据与未授权清理停点（UTC 2026-10-05T05:31:29.892754+00:00）

- 最后文档复验250（UTC 2026-10-05T05:26:20.978759+00:00）：129文件链接通过67.059s，报告101 passed /0.70s；tracked diff-check退出0，所有新增文本空白问题0，Python哈希仍与full234一致。249已按实际四列表刷新172文件证据；248统计解析失败保留，不重复追加记录。
- 更晚空间251：本轮拥有根5,909,529,950B，余532,920,994B，29 reparse剪枝未跟随；尚不足2GiB。仅假设241八项全部安全释放才有2,166,821,882B，比预留多19,338,234B（约18.44MiB）；不是已清理结果，后续每次复制/构建前再测。252对枚举出的真实packages/两DLC ZIP复核SHA256与200一致，未修改签名材料。251第一次按错误假设名称查找为not_found，不当成校验通过；252以实际目录/名称修正。

准确下一步不变：先确认241八项白名单和安全条件，再过空间门；probe材料有界恢复、最终Core03/AI重签/分发与真实验收仍待完成。没有删除或新秘密操作。

## 2026-10-05 PLAN241清理授权与拒绝（255）

用户已确认八项限定目标。254检查通过并保留54份小证据；原生删除命令被工具政策拒绝，0B释放，八项均在。不重试、不绕过，不扩大到213/core-01。当前空间5,910,508,198B，Core03空间门未通过。下一独立切片OPS-PROBE-MATERIALS先红后绿，最终构建仍未执行。

## 2026-10-05 OPS-PROBE-MATERIALS（256–268）

- [x] 公开合同与失败回归；父端拥有intent、私有kernel维护锁、真实PID/创建身份/profile释放判据。
- [x] 生产启动queued后台回收、安全重试和单独清理警告；不改变每包账本权威或启停。
- [ ] 267完整专项复验及最新受影响mypy结果；真实LPAC新父端与10次probe基准。
- [ ] 新稳定源码全量、高负载三遍、最终报告逐文件/性能/实机证据。
- [ ] Core03空间门、最终生产构建与分发矩阵仍未满足；工具拒绝的8项没有删除。

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

## 2026-10-06 用户要求先交接：停止追加

- [x] V1 本夜仅AI正常启动/菜单/自然退出证据；双包确认标题公开seam红绿。
- [x] V3-S 长/短路径真实LPAC host+Worker自检与 helper08 权限canary。
- [x] V3-B native05/probe08/Worker04/Core04生产构建及审计（构建不代表运行/正式分发）。
- [ ] V3-N 正常Worker04租约与业务、分号等特殊路径封闭性；本轮未追加。
- [ ] V2 最新Core04完整四组合/生命周期矩阵；没有用旧结果勾选。
- [ ] V4/V5 Setup、便携、迁移实机；人工及敏感目标以后集中确认。
- [x] V6-FULL 已收齐最新全量：4298 passed /15 skipped /14 warnings /924.58s，exit0；full-02日志保留。
- [ ] V6 剩余：mypy60整体复验、最新高负载三遍及完整性能门；本次不再启动。
- [ ] V3-R 新正式DLC、ZIP/Setup/总分发签名；需后续本地解锁，旧签名根不覆盖。

按最新用户要求，未开始的工作暂停，不自动继续实施；无提交、推送或发布。

## 2026-10-06 恢复：授权先推送检查点再继续

- [ ] P1 183文件白名单与敏感核对，质量/全量/高负载三遍，提交当前检查点。
- [ ] P2 正常push到对应远端、ls-remote及fetch独立核对。
- [ ] V3-N 继续正常Worker接管/业务与分号路径安全合同，按公开seam先红后绿。
- [ ] V2/V4/V5/V6及V3-R 其余冻结构建、事务、性能、分发和人工门按原计划推进，敏感操作后置。

### P1 本次重新验证已完成（2026-10-06）

- [x] 完整白名单/敏感/暂存检查，183文本文件，0二进制。
- [x] 新全量 4298 passed, 15 skipped, 15 warnings in 929.75s (0:15:29)，源码冻结一致。
- [x] 高负载三遍：第1遍 361 passed, 1 skipped, 1 warning in 332.06s (0:05:32)，wall 336.500s /CPU median 100.0% /p95 100.0%；第2遍 361 passed, 1 skipped, 1 warning in 260.70s (0:04:20)，wall 264.844s /CPU median 100.0% /p95 100.0%；第3遍 361 passed, 1 skipped, 1 warning in 259.37s (0:04:19)，wall 263.437s /CPU median 100.0% /p95 100.0%；源码冻结一致。
- [x] 新Ruff/format/三组mypy及报告构建专项。
- [ ] 当前检查点提交/推送及独立远端SHA核对，后续补记录。

实际效果：当前源改动可保存回滚点，不代表新正式分发/人工/干净环境已完成。

## 2026-10-06 最新自动验收增量（不改写历史计划）

- [x] 修复冻结 `WS_EX_NOACTIVATE` 窗口的键盘语义上下文菜单验收：产品 stable body anchor + 驱动 `WM_CONTEXTMENU(-1)`，不激活、不抢焦点。
- [x] 重新构建 `core-11b` 并完成 screen v13、empty v14 真实 Windows 验收；生产加载确认、菜单隔离和自然退出均有证据。
- [x] 收齐本轮代码质量门：66 项受影响测试、Ruff、format-check、`git diff --check`、最新全量 `4300 passed /15 skipped /14 warnings /671.83s`。
- [ ] 正式私钥解锁、正式总分发签名与产物清单。
- [ ] Setup/普通 ZIP/便携真实安装、更新、卸载与旧数据导入。
- [ ] 干净 Windows/新用户/另一台机器及真实模型、截图人工门。

### 本次停点

本地可自动验证的菜单、空安装和全量质量门已完成；代码与证据仍为未提交 WIP。继续实现前先核对剩余差异、空间和正式分发边界，不读取私钥、不把旧人工验收目录当新产物。

## 2026-10-06 质量门结果补记

- [x] 全量 Ruff 与 format-check。
- [x] 文档链接检查 129 文件、报告纪律 57 项、`git diff --check`。
- [ ] `mypy pet`：既有 439 errors / 46 files；不属于本轮菜单修复的方向性阻塞，保留为项目技术债并在最终报告单列。


## 2026-10-06 自动实施增量：生产冻结 probe 路径根因修复与最终 Core 验收

- [x] 复现并定位新生产 Core 下 `official.screen-understanding` probe 自检失败：PyInstaller helper 被放入受环境变量覆盖的深路径，legacy bootloader 无法读取 embedded PKG；未执行候选 factory/Worker，事务安全门正确拒绝并停留在 `host_probe_failed`。
- [x] 修复 Windows probe 运行根：深路径时由可信父端通过 `SHGetKnownFolderPath(FOLDERID_LocalAppData)` 获取系统 LocalAppData，并以 `dshpet-probes/<data-root-identity>/<feature-id>` 绑定自有材料；不使用普通 subprocess 回退、不放宽 LPAC/Win32k/Job/句柄合同。
- [x] 增加路径回归测试；受影响专项 `20 passed / 2.51s`，Ruff、受影响 Python format、目标模块 mypy 通过。
- [x] 重新构建最终本机 Core `core-05-final-20261006`：构建 `127.475s`，dist `455,783,648B`；生产 helper 摘要已绑定，Core 仅为本机验收产物，未读取私钥、未声明 Authenticode。
- [x] 使用真实冻结 Core 完成独立 AI 与 screen 验收：两次均 `CASE_PASSED`，正式 LPAC probe、生产加载 receipt、菜单、自然退出和 pending 清除均通过。
- [x] 最新全量 `python -m pytest -q`：`4301 passed, 15 skipped, 14 warnings in 745.31s`，exit 0。
- [ ] 真实 Setup/普通 ZIP/便携安装、更新、卸载、旧数据导入、干净环境、另一台机器和用户模型/截图人工门仍未执行。
- [ ] 正式 DLC/Setup/总分发签名、Windows Authenticode/SmartScreen、发布与远端推送仍未宣称完成。

实际效果：新生产冻结 Core 的 screen probe 不再因验收根路径过深而失败；失败仍会安全拒绝，不把普通路径降级为安全边界。当前结果是本机自动化/真实冻结产物门，不等同于最终分发或人工验收完成。

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


## 2026-10-06 屏幕理解 UX 修复收尾

- [x] 定位为独立屏幕 profile 的 `credential_ref` 为空，而不是迁移/事务未完成。
- [x] 先写失败测试，再修正手动识屏失败原因提示和设置页状态分层。
- [x] 受影响专项 53 项通过；Ruff、format-check、脚本解析及脱敏 fixture 合同通过。
- [x] 构建新的 `core-06-screen-ux-20261006` 人工验收包；旧 Core、旧人工根、真实配置和凭据未改动。
- [x] 生成 v4 隔离人工验收夹具，验证“已确认迁移但无屏幕凭据”这条实际路径。
- [ ] 用户人工在新 v4 Core 中输入屏幕视觉 API Key，并完成一次真实屏幕请求。
- [ ] 不把本次全量回归中的 6 个 `test_drag_move_coalescing.py` 失败标为产品修复；另行处理该既有测试/环境族。

下一步只需按 v4 `README.md` 操作：安装屏幕包、关闭确认窗口、启动 Core、打开屏幕理解设置确认提示已改为“无需重复迁移/补填 Key”，再由用户在 UI 中录入 Key 并执行一次真实识屏。


## 2026-10-06 最终复核：屏幕 UX 验收包证据

- 使用 Python `zipfile` 直接读取 4 个 screen 包 ZIP，均确认包含修正后的 `host/manual.py` 与 `host/settings.py`，并确认包内含“补填视觉 API Key”“无需重复迁移”文案；未依赖未安装的 `7z` 命令。
- `screen-config-fixture.json` 的原始字节无 UTF-8 BOM，JSON 合同断言通过；不会因 PowerShell 编码导致产品配置读取失败。
- v4 安装脚本 PowerShell AST 解析通过，fixture 只在配置不存在时复制；不会覆盖用户后来在隔离 UI 中保存的凭据引用。
- 受影响源码/测试范围的 `git diff --check` 通过。整个 WIP 工作树仍有历史任务记录中的尾随空白告警，未进行大范围格式化，避免改写既有证据。
- 尚未代替用户输入真实 screen Key 或发起真实 screen 请求；这仍是唯一需要人工执行的本次修复验收门。


## 2026-10-06 用户决策修正：screen 首装默认配置

- 用户明确要求：屏幕理解首次安装/无有效 screen 自有配置时直接使用默认初始配置，不再主动迁移旧 AI 配置，不显示“迁移/补齐”按钮；之后由用户在屏幕理解设置中自行填写或修改。
- 兼容规则：已有有效 `official.screen-understanding` 自有配置正常继承；这不等于跨包迁移。显式兼容迁移代码与历史记录暂保留，但不由首装、启动或设置 UI 自动调用。
- 本轮已实现并验证：`VisionSettings.default()`、屏幕设置默认字段、独立缺凭据提示；相关专项 `51 passed`。
- 当前状态：源码 WIP 已修改，尚未提交/推送；需要重建人工验收包并由用户完成真实设置/识屏确认。
## 2026-10-06 产品口径重置：Phase5A 本地激活收尾计划

### 已确认的产品合同

- Setup 安装向导中的 `ai` / `screen` 是唯一的安装时选装入口；选装只传递本地安装意图，不由安装器下载、签名或伪造完成回执。
- 离开 Setup 后，用户可以选择普通 ZIP 或本地功能包目录；Phase5A 不要求 `manifest.sig`、`key_id`、公钥、私钥或发布者签名。
- Core 仍执行固定 feature/factory 白名单、兼容性、路径/大小/文件数量、manifest 文件清单 SHA-256、事务状态及 Windows LPAC/Worker probe。该层是结构、完整性和运行隔离校验，不是发布者身份认证。
- 既有 Ed25519 正式签名工具和签名测试只保留为未来正式发布/兼容模式，不再作为本轮 Phase5A 收尾门。

### 本轮执行顺序

- [x] 将本地激活策略贯通 Python verifier、事务、probe、版本租约、构建器和 Setup/管理 UI。
- [x] 移除 Phase5A 主构建路径的密钥生成、`manifest.sig` 生成和签名硬门；保留历史签名 fixture 的显式兼容参数。
- [x] 增加“无签名仍通过结构与完整性校验、篡改仍拒绝、signed-only 仍拒绝”的回归测试。
- [x] 更新 Phase5A 设计、README、项目入口、UI 文案和验收脚本，明确当前有效合同与历史正式发布路线的边界。
- [ ] 用 `worker-06` / `probe-08` 重建新的无密钥 GUI Core 与普通本地 ZIP，核对无 `manifest.sig` / 无公钥元数据。
- [ ] 在真实 Windows 上运行 Setup/本地 ZIP/本地目录验收；先自动化探针，再尽可能完成 Core、设置、管理页和自然退出的人工可见验收。
- [ ] 在新证据落盘后，仅删除 `.scratch/phase5a-local-distribution` 下已确认过时的构建输出，保留当前记录、manifest、日志和失败证据。
- [ ] 运行 Ruff、受影响专项、全量 pytest 和受影响时序族；更新本报告及五份交接记录，明确仍需用户操作的真实 Provider/视觉请求或安装器体验项。


## 2026-10-06 收尾执行结果：Phase5A 本地激活合同落地

### 合同最终状态

- Setup 安装向导保留 `ai` / `screen` 可选任务；Setup 只传递选装意图，不承担下载、发布者签名或伪造完成回执。
- 离开 Setup 后，用户显式选择普通 ZIP 或本地功能包目录，再由扩展管理页导入/激活。
- 当前本地激活不要求 `manifest.sig`、发布者公钥、私钥或发布者签名。v2 manifest 的 `key_id`（若存在）只是兼容 schema 的非密码学标识，不参与信任判断。
- 结构与运行安全门仍保留：固定 feature/factory、版本/能力兼容性、安全路径、文件数/大小上限、manifest 文件清单 SHA-256、事务状态、Windows LPAC/Worker probe。

### 已完成事项

- [x] Python verifier、事务、probe、版本租约和安装状态统一使用 `accepts_descriptor()`；本地用户选择产生 `local_user`，历史 signed-only Core 仍拒绝无签名包。
- [x] Phase5A 主构建器停止生成密钥和 `manifest.sig`；Ed25519 工具仅保留为未来正式发布/兼容路线。
- [x] Setup、扩展管理 UI、设计文档、README、项目入口和验收脚本同步改为“Setup 选装 / 普通 ZIP 或目录显式导入”。
- [x] 修复 headless probe 的 PyInstaller `pyi_rth_multiprocessing.py` 在 LPAC 中提前调用 `WSAStartup` 的启动阻断；排除未使用的 `multiprocessing` runtime hook 后，host-only AI probe 通过。
- [x] 真实 Windows 冻结 Core 的 `empty`、`ai`、`screen`、`both` 四条 UI/自然退出验收全部通过。
- [x] 最终静态与自动门通过：focused `144 passed`；全量 `4313 passed, 15 skipped, 14 warnings`；Ruff check/format-check 和 `git diff --check` 通过。
- [x] 清理已核实过时生成物：计划 247 个目标、221,340 个文件、19,215,410,032 字节；保留最终构建、probe、Worker、native 叶子、v8-v13 验收根和五份正式记录。

### 当前未完成 / 需用户最后参与

- [ ] 在安装了 Inno Setup 的干净 Windows 上实际打开并完成 `core_webm.iss` 向导，确认 `ai` / `screen` 可选任务的视觉文案、勾选、旁置包缺失/存在时行为和实际安装结果；本机未找到 `ISCC.exe`，因此不能把静态脚本/测试当作真实 Setup 通过。
- [ ] 用户在真实 Provider/视觉设置中自行录入凭据并发起一次真实 AI/识屏请求；凭据不得经聊天、命令行或日志传递。
- [ ] 若需要正式发布者认证、签名、Authenticode/SmartScreen 或另一台机器/干净用户验收，应另开正式发布门，不回写为当前 Phase5A 本地激活前置条件。

当前工作树仅保留本地修改，未提交、未推送。

## 2026-10-07 计划增补：Setup 编译已补齐

- [x] 在项目文档和历史记录中定位 `packaging/core_webm.iss` 及 `E:\tools\InnoSetup6\ISCC.exe` 的实际编译入口。
- [x] 使用 manual20 最新 Core 编译 Phase5A Setup；直接长路径触发 Windows/Inno legacy path failure 后，使用不改内容的短路径 staging 重试成功。
- [x] 保留 Setup 同级 `packages` 旁置包并固化 Setup、包文件 SHA-256、编译日志和清单。
- [ ] 由用户最后打开 Setup 向导，确认 `ai` / `screen` 可选任务、旁置包缺失行为、安装后状态和卸载体验；本任务不把 Authenticode/正式发布签名重新引入 Phase5A 本地激活合同。

最新 Setup：`dsh-pet-core-webm-setup.exe`，`227,324,912 B`，SHA-256 `3780ec4355e4ab0b0bce6285c8fe4aa07289877af43e5bab333f3965a5c9395b`；编译耗时 `116.031 s`，`ISCC_EXIT=0`。此前“找不到 ISCC.exe”的条目仅是 2026-10-06 历史停点，已由本补记 supersede。


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
## 2026-10-08 计划增补：R08 覆盖安装精确库存修复

本次用户反馈把 R07 交付候选中的一个真实安装路径缺陷转为 R08：新 Setup 覆盖旧 Core 时没有清除旧版本 frozen `feature-probe` 子树，导致严格 `bundle_inventory` 校验把旧文件认定为残留并以 code 2 阻止官方扩展安装。计划边界保持不变：只清理 Core-owned 的冻结 probe 目录，不清理 DLC、配置、凭据或个人数据。

完成条件已达成：

1. 修复前失败回归已保存，产品修改后转绿；
2. Inno `[InstallDelete]` 在隔离目录中实测删除 stale probe 文件并保留新 probe 文件；
3. 新 Setup 重新编译，官方 ZIP 预检通过，哈希已记录；
4. Ruff、diff check、offscreen 全量 pytest 通过；
5. 人工验收边界仍保留：用户必须用新 Setup 覆盖安装并确认 Core 启动、DLC 保留、API/余额/识屏和 Core-only 卸载。

R08 不改变 Phase5A 的关闭条件；`phase5a_closed=false` 直到用户完成最后人工验收并明确确认。

<!-- R09_POLICY_UNINSTALL_20261009 -->
## 2026-10-09 R09 计划完成与人工门保留

R09 是 R08 覆盖安装修复之后的交付收口轮，目标是解决用户退出桌宠后仍遇到 `core_maintenance_incomplete:2` 的诊断歧义，并修正隔离冻结验收中发现的本地 DLC 激活策略遗漏。

### 任务状态

- [x] 卸载失败范围回归：外部 profile bridge 引用不会被当作当前 Core-owned 集成；Core 维护入口记录脱敏失败原因；证据 `repair-20261009-uninstall-diagnosis/R09-foreign-lock-red.log`、`R09-uninstall-diagnostic-red.log` 和 `R09-focused-green.log`。
- [x] 冻结策略失败回归：先确认 PYZ 中缺少 `ALLOW_LOCAL_PACKAGE_ACTIVATION` 的 red，再让构建生成策略明确写入该常量；证据 `R09-policy-red.log`、`R09-policy-green.log`。
- [x] 重建 Core 4.2.2 与 Setup；Setup 编译退出码 0，产物哈希已写入 STATUS/HANDOFF/SUMMARY 和报告。
- [x] 用真实冻结 Core、Qt 事件循环、进程边界和 UIAutomation 跑 `empty/ai/screen/both`；四案通过并保留 `frozen-results.json`。
- [x] 复跑受影响专项、Ruff、格式、diff check 和全量 pytest；结果均通过。
- [ ] 真实 Provider/余额协议/真实屏幕/真实 Setup 覆盖安装、Core-only 卸载和重装保留包：必须由用户执行，Codex 不代替，也不把隔离验收记为用户确认。

### 本轮停止条件与回滚边界

不执行真实用户安装、卸载、删除数据或强杀进程；不读取真实 Key/屏幕；不清理未知 staging；不提交、不推送。若用户验收失败，保留新 Setup、`core-maintenance.log` 脱敏尾部和独立 run root，按具体 reason 回到 R09/R10，而不是恢复旧硬编码 owner/factory 路径。

## 完成后的实际使用效果

- 用户能用新 Setup 覆盖安装后，在 Core 配置一次 API 并授权给 DLC；新聊天请求不再依赖重启，余额走 Core 的余额用途绑定。
- 自动识屏关闭不会误伤手动“看看屏幕”；冻结 Worker/包激活路径不再因构建策略遗漏而把确认按钮禁用。
- Core 卸载只处理 Core 和它确实拥有的系统集成；DLC 副本、源包、配置、凭据和个人数据不因 Core 卸载被删除。
- 当前限制：真实服务、真实屏幕和真实 Windows 安装/卸载仍未由 Codex执行，Phase5A 仍等待用户确认。

## S04 补充（2026-10-10，实施前登记）

- 真实冻结 Core 已从项目内 data/feature-runtime 启动生产 Worker 并完成租约/READY；但退出码 62097，不算通过。日志显示启动前包校验耗时被心跳计时计入，尚未 READY 的新进程被误报 heartbeat timeout。补真实进程 red，心跳只在 READY 后启用，握手仍由独立限时监管；继续验证自然退出。
- 首次全量 4 failed / 4486 passed / 15 skipped / 15 warnings（800.37s）；4 项是旧 4.2.3/Screen1.0.2 版本断言，更新到本次合同后重跑，高负载未启动，不记通过。
- 用户追加：新候选核对 Setup 内 EXE 与快捷方式的鲸鱼娘图标；不清全局图标缓存，不修改真实安装目录。已抽取新 Core/Setup 内10帧与 assets/icon.ico 全相同，仍需最终交付核验。
- 用户追加：API 测试按钮旁显示测试中、成功/失败和实际 HTTP 状态码；网络/TLS/超时没有 HTTP 状态时用明确错误类型，原说明放旁边备注。只改反馈 UI/探针结果携带，不改保存、密钥、API 路由或 Setup 语义；先补 red，明暗720/1100实机检查后重建 Core/Setup。

实际效果：识屏启动不误耗心跳预算；测试连接可一眼看到结果码，不把测试当保存。真实服务最终仍待用户验收。
