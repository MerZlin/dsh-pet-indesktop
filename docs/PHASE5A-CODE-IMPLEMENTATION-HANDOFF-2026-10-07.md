# Phase5A 代码实现交付文档（实现后交接）

<!-- S04_CURRENT_START -->
## S01–S04 当前修复交付（2026-10-10）

**S01–S04 工程交付完成；真实 Provider/屏幕效果与用户安装体验仍待确认，Phase5A 不关闭。** 下方 U/T/R 为历史版本，不能沿用其中旧 Setup 作为当前验收产物。当前有效 API 合同已由 A 改为简易主/可选视觉 Key，不再要求用户输入服务 ID 或勾用途；U 的项目目录安装/卸载合同保持且用户已确认，本轮不再修改。

最终候选 `_s01b/setup-release/dsh-pet-core-webm-setup.exe`；Core4.2.4 / AI1.0.3 / Screen1.0.3（Screen ≥Core4.2.4），Setup SHA-256 `7282fc05ac0b14138f00d4dbb71edf73af82aee261500f59f2217e8a1756a40d`。更新原 portable 目录时勾选屏幕理解，以实际安装1.0.3；保留原配置，不改真实安装目录或数据。

本轮修复项目内 worker runtime、同步失败提示/重试、READY前心跳；追加按钮旁实际 HTTP/传输结果码。最终冻结 Core→已安装生产 Worker→租约/HELLO/READY→自然退出0/0已通过，Core/Setup及私有Windows快捷方式实显鲸鱼娘；0真实Key/0真实屏幕/0Provider请求。

全量：4507 passed, 15 skipped, 14 warnings in 802.39s (0:13:22)；exit 0，643 个输入前后摘要一致。高负载：第1轮 169 passed in 47.59s、exit 0、CPU 中位 100.0%（含调度49.875s）；第2轮 169 passed in 47.06s、exit 0、CPU 中位 99.75%（含调度48.656s）；第3轮 169 passed in 47.69s、exit 0、CPU 中位 100.0%（含调度49.813s）；三轮全部通过且自有负载进程自然退出。三份证据/逐文件/hash/限制与人工顺序见 [S 工程报告](PR-REPORT-PORTABLE-SCREEN-WORKER-2026-10-10.md)；准确停点见 [HANDOFF](../.scratch/phase5a-local-distribution/HANDOFF.md)。无提交、推送、稳定版覆盖或正式发布。

### 本次实际可体验的效果与限制

新候选沿用原API配置，手动识屏能从项目内启动；测试按钮旁可见成功/失败与结果码。真正视觉服务、屏幕内容和用户实际图标仍待确认；不能以本轮工程门代签用户或关闭Phase5A。
<!-- S04_CURRENT_END -->

<!-- CURRENT_U_DELIVERY_START -->
## U01–U06 历史修订（2026-10-09；现行候选见上方 S）

U01–U06 工程实现、自动化、重建和隔离实机验收完成；待用户确认真实功能体验。新合同已获用户批准，替代本文历史“Core-only 保留 DLC”安装器描述；API/手动识屏业务合同不倒退。路径页总显示、项目内 portable data/DLC、卸载默认保留个人数据但删除所有项目内 DLC；可选集成清理失败不再阻止卸载。

当前交付只使用 `.scratch/phase5a-local-distribution/u06-delivery-20261009/setup-verified/dsh-pet-core-webm-setup.exe`（SHA-256 `7cecfde7854fa70cbfa98641457b2ade8c8a547887254ff609204ad1815f6e22`）。旧未发布布局不迁移，首次验收选择新空目录；若已有本次 portable 目录可更新。全量：4442 passed, 15 skipped, 14 warnings in 1594.26s (0:26:34)；高负载：3轮通过。真实 Windows 独立项目的安装、更新、取消、占用、两种卸载和保留数据重装已经通过；真实 Provider/余额/屏幕仍由用户验收，Phase5A 未正式关闭。

完整文件说明、性能、实机证据及人工步骤：[本轮报告](PR-REPORT-SETUP-PROJECT-DIRECTORY-2026-10-09.md)。连续记录：[PLAN](../.scratch/phase5a-local-distribution/PLAN.md) / [STATUS](../.scratch/phase5a-local-distribution/STATUS.md) / [HANDOFF](../.scratch/phase5a-local-distribution/HANDOFF.md) / [SUMMARY](../.scratch/phase5a-local-distribution/SUMMARY.md)。
<!-- CURRENT_U_DELIVERY_END -->

> 原 2026-10-08 R 证据保留在本文，当前状态以上方 U 修订和本轮报告为准。

## 1. 交付结论

**原 T 轮历史结论（不代表 R 修复已收尾）**：T01–T06 曾按以下边界实现，T07 工程留档曾通过；后续人工验收暴露产品缺陷，需完成本文末 R 修复门。继续使用通用 owner/factory 路由，不恢复旧硬编码。

- **Setup 内**：只嵌入官方 `official.ai-chat`、`official.screen-understanding`，安装向导勾选即自动导入/启用；不依赖旁置 `packages`，不要求公钥、私钥或 `manifest.sig`。
- **Setup 外**：用户明确选择 ZIP/目录后，受限读取 `manifest.json`，按注册的 `id`/`factory`/`execution_kind` 自动路由；非官方 owner 只要符合包合同也作为第三方 DLC 接受。
- **执行边界**：用户选择本地包就是信任授权；包内 host/Worker Python 可以执行，不能称为 sandbox。结构、兼容性、文件清单、路径、事务和启动确认仍必须保留。
- **Core**：最终桌宠 EXE 使用 GUI subsystem，不显示终端窗口。

完整细节见 [实现计划](PHASE5A-CLOSEOUT-IMPLEMENTATION-PLAN-2026-10-07.md)。

## 2. 必读顺序

1. `docs/PROJECT-ENTRY.md`
2. `docs/PHASE5A-CLOSEOUT-IMPLEMENTATION-PLAN-2026-10-07.md`
3. `docs/plugin-phase-05-distribution/PHASE5A-LOCAL-DISTRIBUTION-DESIGN.md`
4. `.scratch/phase5a-local-distribution/STATUS.md`
5. `.scratch/phase5a-local-distribution/PLAN.md`
6. `.scratch/phase5a-local-distribution/HANDOFF.md`
7. `docs/ONEDIR_PACKAGING.md`（改 PyInstaller 时）
8. `docs/SETTINGS-CHANGE-GATES.md`（改设置/入口时）
9. `.agents/skills/qt-ui-review/SKILL.md`（改 Qt 设置页/菜单时）

## 3. 当前工作树与保护边界

- 工作目录：`E:\AI\DSH\dsh-pet-indesktop`。
- 当前工作树已存在大量未提交 Phase5A 改动；必须先 `git status --short` 和 `git diff --stat`，保留既有改动，不 reset、不 checkout 覆盖、不提交、不推送。
- 工作树包含之前阶段的 dirty diff 与本轮实施，不把全部差异归为本轮新增；逐文件归因与 numstat 见本轮报告，禁止覆盖、误删或整体暂存。
- 不读取、复制、输出用户真实 API key、私钥、真实 profile 或真实数据；测试只使用新建的 staging/data 根。
- 不使用历史签名根把本地导入重新改回发布者认证；历史签名工具可保留，但不能成为 Phase5A 正常路径依赖。

## 4. 施工起点的失败回归与文件责任（历史基线）

### 首先写失败测试

- unknown owner 的 local v2 包：验证、安装、启动；
- official/signature mode：unknown owner 仍拒绝；
- local mode：无 `manifest.sig` 和带旧 `manifest.sig` 均可；
- AI ZIP 从 Screen 入口选择自动路由；
- ZIP/目录结果一致；
- Setup 维护入口无 QApplication/二次确认；
- Setup 生成不依赖 `{src}\\packages`；
- Core spec/最终 PE 不使用 console subsystem。

### 主要修改入口

- `pet/official_features.py`：复用/抽出中性 `FeatureRegistration`，不要创造第二套平行身份表；
- `pet/plugins/package_trust.py`：local 与 official 验证策略分离；错误信息带实际注册字段；
- `pet/plugins/feature_packages.py`、`pet/plugins/package_binding.py`：descriptor 驱动 host/worker 加载；
- `pet/feature_package_transactions.py`、`pet/feature_install_state.py`、`pet/feature_version_lease.py`：移除非必要 `official_feature()` 硬编码，复用权威状态/事务；
- `pet/feature_package_startup.py`、`pet/feature_management.py`、`pet/feature_lifecycle_contract.py`：按通用 registration 完成启动/生命周期；
- `pet/feature_management_ui.py`、`pet/local_package_intents.py`：bounded manifest router、统一导入、自动 apply；
- `pet/app.py`、`pet/feature_host.py`、`pet/feature_ports.py`、`pet/modern_settings_dialog.py`：动态 DLC manager/context 与可见入口；
- `packaging/core_webm.iss`：`PackageDir`、内嵌两个官方 ZIP、无旁置依赖、维护返回码；
- `scripts/build_screen_delivery.py`：Core `console=False`；
- `tests/` 相关 package/transaction/UI/build/acceptance 测试：红绿回归。

执行时用 `rg -n "official_feature\(" pet` 复核没有遗漏的运行时硬依赖；正式签名/release 工具可以继续保留官方白名单。

## 5. 已遵循的施工顺序（原实施计划）

1. 基线/保护范围/红测试。
2. 中性 registration + verifier local/official 分流。
3. transaction/state/lease/startup/loader 泛化。
4. manifest router + 统一 UI + 第三方 DLC 列表和启停。
5. Setup 内嵌官方包 + 无界面维护 apply + 正常启动 receipt。
6. Core GUI subsystem + PE 门。
7. 专项/相关/全量测试，Ruff/format，Core/Setup 重建，真实短路径验收。
8. 回填 `docs/PR-REPORT-<topic>-2026-10-07.md`、`docs/INDEX.md` 和五份阶段记录。

每一步先确认接口和红测试，再改实现；不要一次性大规模重写所有 `official_feature()` 调用。

## 6. 旧产物与先前文档清理结果（历史）

本轮清理前，`phase5a-local-distribution` 为：

- `53,298` 个文件；`9,876,078,289 B`（约 `9.197 GiB`，按十进制/二进制需以命令为准）；
- 删除 38 个明确未被文档/阶段记录引用的旧构建/诊断目录，预估/实删释放 `4,905,969,506 B`、`9,404` 个文件。

清理后复核：

- `43,894` 个文件；`4,970,108,783 B`，约 `4.629 GiB`；
- 保护路径仍存在：
  - `acceptance-night-20261006/builds/manual-20-runtime-fix-20261006`；
  - `acceptance-night-20261006/builds/probe-10-runtime-fix-20261006`；
  - `acceptance-night-20261006/builds/worker-08-local-activation-20261006`；
  - `delivery-candidate-200`、`delivery-set-293`、`publication-20261006`；
  - `setup-acceptance-20261007-shortpath`；
  - `PLAN.md`、`STATUS.md`、`HANDOFF.md`、`WORKLOG.md`、`SUMMARY.md`。

没有触碰 `.scratch/phase4b-local-management`、`.scratch` 根部其他阶段、源码、真实用户数据和当前 Setup 产物。清理只针对 Phase5A 目录内过时生成物。

## 7. 已知可引用的历史构建证据（仅作基线，不是新实现证明）

- 旧 `manual-20` Core/AI/Screen 证据：`.scratch/phase5a-local-distribution/acceptance-night-20261006/builds/manual-20-runtime-fix-20261006`。
- 旧 v13 运行证据：`.scratch/phase5a-local-distribution/manual-user-acceptance-20261006-v13-runtime-fix`。
- 2026-10-07 旧 Setup 编译产物：`.scratch/phase5a-local-distribution/setup-acceptance-20261007-shortpath`；Setup SHA-256 `3780ec4355e4ab0b0bce6285c8fe4aa07289877af43e5bab333f3965a5c9395b`。
- 上述产物对应旧实现，当前新需求要求重写 registration、UI router、Setup 维护和 Core subsystem 后，必须重新构建；不能用它们证明本计划已完成。

## 8. 本轮完成回执与后续人工交接格式

结束时必须明确分开：

1. **代码已实现**：逐文件说明和 `git diff --numstat`；
2. **自动化已通过**：命令、环境、样本量、测试输出；
3. **真实机器已通过**：Core/Setup/ZIP/目录矩阵和用户可见行为；
4. **仍未通过/待用户确认**：真实 Provider、视觉请求、Setup 主观文案/入口和其他无法自动完成的门；
5. **未提交/未推送状态**：不能用本地通过替代发布。

原 T 轮同一工作树构建和测试曾通过，只能表示当时 T01–T07 授权范围内的工程结果。当前 R 修复须重新完成全量、负载、产物与实机门，用户确认前不能标 Phase5A 全部正式交付；以 STATUS/HANDOFF 为准。

## 9. 实际使用效果与限制

用户可从统一 ZIP/目录入口导入符合合同的受信任本地 DLC，owner/factory 不再限定官方集合；Setup 内嵌两个官方包，Core 使用 GUI PE。已加载代码卸载仍等待进程自然退出；本地 Python 不是沙箱，当前工程证据不等于系统安装、真实 Provider 或正式发布已完成。

## 10. R 修复公共 API 与兼容合同

- 入口为可选的 `FeatureHostContext.api: FeatureApiPort | None`（字段位于兼容扩展位置）。None 保留旧 DLC 旧接口路径；新 DLC 只维护业务设置，不维护第二份 API 地址和 Key。
- `metadata(purpose)` / `effective_version(purpose)`：仅已授权的不可变服务/模型/TLS/超时元数据和用途有效版本，不返回凭据引用或全局 Config；撤权用 unavailable 表示。
- `resolve(purpose)`：每次执行授权、用途授权与当前提交版本检查后，返回 `ApiRequest` 的该次快照和临时密钥，api_key 从 repr 排除；禁止持久化或日志记录请求对象。
- `subscribe(callback)`：GUI 线程注册，返回 unsubscribe；同进程 queued signals 与跨进程 500 ms 版本轮询。订阅用于展示/取消，请求前解析仍是兜底，不依赖通知必达。`open_settings()` 打开 Core-owned API 编辑页。
- 有效配置版本与授权身份分离：普通保存不污染在飞快照；撤权/重新授权会更换 grant identity，旧快照不会被重新授权“复活”；send、入网前及结果接纳均检查。授权身份不序列化到 AI 业务配置。
- 多服务 namespace 采用 CAS、进程级删除门、prepared journal/原子发布/重放恢复。更换 endpoint 必须重新录 Key 和用途，安全存储失败不回退明文；旧引用不自动删除。普通 Config.save 不覆盖其他插件或中央 API 的新版本。
- 迁移仅显示旧来源的非敏感服务/模型/用途，逐来源显式确认；冲突保留草稿和旧来源，不自动合并 Key，不重新启用 DLC。
- 官方名称仅是设置页便捷勾选，第三方填写同一 owner | purpose | model 授权形状；没有官方 owner/factory 执行特判。这是防误用接口，不是 Python sandbox。

## 11. R 修复卸载与交付边界

新 Setup 版本 4.2.2 的系统卸载不加载 DLC 工厂、不执行逐包卸载/恢复、不受 staging/ledger/pending 阻塞；只检查真实 Core 安装身份、程序占用和数据根删除门，清理路径限定的 Core-owned 集成。Core 占用要求自然退出，不强杀。DLC 管理页单包卸载仍独立，删除已安装副本，保留原始 ZIP/源目录、配置和个人数据。

**升级前置**：旧 unins000.exe 包含旧逻辑，必须先用新 Setup 覆盖安装更新卸载器；只换源码/ZIP 不会修好旧卸载器。新 DLC 的版本号不覆盖旧内容；旧 DLC 保留兼容路径，但不会强制迁移或自动重启用。

**用户实际效果与限制**：Core 配置一次 Key，勾选文字/视觉/余额用途，保存后已有聊天窗口无需重启；手动看看屏幕不被自动开关误取消；系统卸载不先拆 DLC。服务必须真正支持对应模型/余额协议，真实 Provider、真实屏幕和新 Setup 的安装/卸载仍需用户验收；验收前 Phase5A 不能关闭。


## 12. R修复版最终证据与准确停点（2026-10-08）

2026-10-08：Core 4.2.2 / AI 1.0.2 / Screen 1.0.1 的 R01–R05 修复已实现；最新默认单进程全量 4417 passed /15 skipped /14 warnings、自然 exit0，3×152 项满CPU复跑通过，已重建受影响 Core/新 Setup并复核其余交付输入。工程范围验证完成，待用户人工验收，Phase5A 未正式关闭。

- 最新默认未插桩单进程4432项：4417passed/15skipped/14warnings/1391.19s、wall 1392.541s、自然0，621源码前后/最终快照相同。15族3轮每轮152passed，20逐核worker/CPU中位100%，全部自然0、残留[]。
- HEAD既有借用main QThread wrapper/循环GC原生问题，公开seam red1failed/6.63s→green-v3相关23passed/8.48s；改创建线程身份而非keeper/清事件。lease20s测试ready迟达44.040s证据后改90s事件预算，产品断言/timeout不变；最新全量/负载均覆盖。
- 当前Core SHA `1413d5ef5aeb43de9aa693c3d7060a5849fa9b1c7ae8902c24a6c511a11c7264`；新Setup仅 `.scratch/phase5a-local-distribution/r422/setup-lifecycle/dsh-pet-core-webm-setup.exe`、SHA `53fe18eff3e2b45acf0a23c1732675316241b9b84fe7d7ee8214b2429e854f38`。保留旧setup-final/c07/c08但不可混用；两DLC版本/Worker/helper本轮R重建且源码输入仍相符。Core须完整c09内部目录，不能单拷EXE。
- 审计root207/stage212/resources1000/bundle2103/PYZ2188/PE=2；新Core真实empty/AI/Screen/both保留profile启动/菜单/自然0，payload/ledger不变，no-DLC API设置可见0。不是已执行系统卸载重装。生产Worker真实租约/HELLO/READY/自然0/free，0截图/联网。
- 逐文件140累计路径（115修改+25新增，含原78dirty）、API性能n1000/n50/n200/短窗口、guard7×10000、当前实机/人工清单统一报告§8。末次 Ruff check、119 个改动 Python format --check、diff check 均 exit0；报告纪律 59 passed in 0.66s；19 份 Markdown 的 424 个相对链接无断链/尾随空白。 两根上限11.25GiB，旧产物/失败记录不删除。

**准确下一步**：没有授权范围内的新源码实现步骤；用户先备份/自然退出，用新Setup覆盖更新旧unins000.exe，再按报告§8.7验收一次Key/用途、四旧聊天与文件/余额、手动识屏、两来源Core-only卸载保留/重装和单包范围。用户未确认就不能正式关闭Phase5A；出现缺陷先红绿和受影响验证，不复活旧官方factory路径。无Git发布授权。

**实际效果与限制**：Core统一凭据按用途供DLC使用，保存后新请求不重启；手动识屏不受自动策略误取消；系统卸载不先拆DLC。协议/模型须服务支持，真实Provider/画面/系统修改、macOS/Linux、复杂第三方/正式签名分发仍未证，工程通过不等于用户验收。
