# Phase5A 收尾实现计划（Setup 官方选装 + 本地第三方 DLC）

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

> **原 T 轮历史执行状态（2026-10-07；R01–R07 当前状态见文末）：T01–T07 本轮授权范围实现、验证与留档收尾完成。** 本计划的产品方向保持不变；代码已按 red→green 顺序落地，当前 helper/Core/Worker/官方包/Setup 已重新构建，独立资料目录的实机矩阵通过；最终全量 `4334 passed, 15 skipped`、满负载三轮各 `97 passed` 和未授权人工门以 [本轮 PR 报告](PR-REPORT-PHASE5A-LOCAL-DISTRIBUTION-2026-10-07.md) 及五份阶段记录为准。下方根因表是 T01 之前的基线，不是当前未修问题清单。
>
> 相关交接：[代码实现交付文档](PHASE5A-CODE-IMPLEMENTATION-HANDOFF-2026-10-07.md) · [阶段设计](plugin-phase-05-distribution/PHASE5A-LOCAL-DISTRIBUTION-DESIGN.md) · [阶段记录](../.scratch/phase5a-local-distribution/PLAN.md) · [项目入口](PROJECT-ENTRY.md)

## 1. 本轮要解决的产品问题

Phase5A 收敛为两个互不混淆的入口：

| 场景 | 用户动作 | 信任/分发边界 | 预期结果 |
|---|---|---|---|
| Setup 内选装 | 在安装向导中勾选 AI、Screen | Setup 只携带项目明确提供的两个官方包；不要求用户提供密钥，不依赖 Setup 同目录的 `packages` | 安装过程自动导入、提交并启用；安装后第一次启动桌宠即可加载 |
| Setup 外导入 | 用户明确选择 ZIP 或目录 | 选择文件本身就是本地信任授权；按 manifest 注册信息接受第三方 DLC，不要求进入官方集合，不要求 `manifest.sig` | 自动识别 owner/factory，导入后启用；升级/替换已加载代码时提示重启 |

“用户主动信任”只取消发布者身份认证，不取消工程正确性检查。路径穿越、重解析点、文件数量/大小、manifest 清单和 SHA-256、兼容性、事务、版本租约、启动确认仍然保留。包内 Python host/Worker 可以执行，因此本地第三方包**不是 Python 沙箱**，不能向用户宣称为不可信代码隔离环境。

本轮不做：在线目录、自动下载、签名撤销、公钥轮换、社区审核、任意 ZIP 脚本扫描、Python 热卸载、正式 Authenticode/SmartScreen 发布门。

## 2. T01 开始前的根因判断（历史基线）

| T01 前基线现象 | 已定位的根因 | 本轮已落地的修正方向 |
|---|---|---|
| Core EXE 启动时出现终端窗口 | `scripts/build_screen_delivery.py` 生成 PyInstaller `EXE(..., console=True)` | Core 改为 `console=False`；保留 Worker/诊断/维护入口各自需要的窗口策略；对 spec 和最终 PE subsystem 加回归门 |
| Setup 勾选后没有直接在桌宠中生效 | `packaging/core_webm.iss` 依赖 `{src}\\packages`，且 `local_package_intents` 只预检/弹确认，没有 Setup 专用无界面 apply | 官方 ZIP 内嵌 Setup；安装后解到临时目录；调用无 QApplication 的维护入口自动 preflight + apply；正常启动再确认 startup receipt |
| 选择 AI ZIP 时出现 `unsupported official feature/factory` | `pet/plugins/package_trust.py`、事务、生命周期等边界仍调用 `official_feature()`；UI widget 绑定单一 manager，AI 包可能被送到 Screen manager | 把官方注册表和通用本地注册解耦；受限读取 manifest 后自动路由；未知 owner 在 local mode 下按 manifest 合同注册 |
| 计划过度偏向正式签名 | 旧设计把发布者身份、包结构和本地用户主动导入混在一起 | Phase5A 正常路径移除签名依赖；历史签名工具只保留兼容/未来正式发布路线；文档不再把公钥/私钥写成当前前置 |

## 3. 目标架构与详细实现规则

### 3.1 中性注册模型，官方注册只是一个来源

优先复用现有 `pet/official_features.py` 的数据形状，必要时抽出一个中性 `FeatureRegistration`（不要再创建第二套平行的 owner 模型）。最小字段：

- `id`、`version`、`api_version`；
- `core_requires`、`platforms`；
- `capabilities`；
- `factory`（包 ABI/功能族标识，不是任意 Python import path）；
- `execution_kind`（例如 `host-only`、`host-worker`）；
- `worker`（可选路径和参数合同）；
- UI/运行时贡献合同所需的受限元数据。

`OFFICIAL_FEATURES` 继续保存官方 AI/Screen 的默认配置、正式发布工具和官方上下文；但 local import 不再用它判断“owner 是否存在”。正式签名模式仍可要求官方注册表和信任锚，不能被 local mode 的放宽反向影响。

### 3.2 验证器分成“受限预读”和“完整验证”两步

1. **预读**：对 ZIP 或目录只读取大小受限的 `manifest.json`，解析 JSON object，取得 `id`、`factory`、`execution_kind` 等路由字段；不导入 Python、不执行包代码。
2. **创建验证上下文**：以 manifest 的 owner/factory 生成通用本地注册，或命中官方注册后使用官方额外约束。
3. **完整验证**：继续校验 schema、Core/API/平台兼容、capability、文件清单 SHA-256、路径/重解析点、文件数/单文件/总大小、host/factory.py、worker 合同和事务输入。
4. **认证策略**：`allow_local_packages=True` 时不要求 `manifest.sig`，忽略旧签名字段对授权的影响，不读取发布私钥；正式签名模式保留原有严格路径。
5. **错误信息**：错误至少包含实际 `id`、`factory`、`execution_kind` 和预期/失败原因，不能再次只报 `unsupported official feature/factory`。

入口仍固定为包合同中的 `host/factory.py:create_host`，可选 Worker 只按 manifest 声明的受控路径启动；不通过扫描 ZIP 中任意 `.py` 文件来猜入口，也不新增任意 `entrypoint` 字段。

### 3.3 安装状态和 DLC 注册索引不重复造账本

复用现有 `FeatureInstallStateStore`、事务 journal、版本 lease 和 startup receipt。若现有字段不足，扩展同一权威记录而不是再造一套互相竞争的索引。注册信息至少保留：

- owner/id、version、factory、execution kind；
- manifest 摘要、安装版本根和来源摘要；
- `enabled`、最近错误、安装/升级时间和需要重启标记。

注册索引只负责发现与 UI 路由；每次启动和加载仍重新验证 manifest 与文件树，不能把“索引里存在”当作执行授权。配置、偏好、文档、状态、凭据按 owner 隔离；第三方包不得取得 `AppShell`、`PetWindow` 私有对象或 Core 全局配置对象。

### 3.4 Loader、Host、Worker 和启动确认改为 descriptor/registration 驱动

需要消除所有对 `official_feature(context.owner)` 的运行时硬依赖，至少复核：

- `pet/plugins/feature_packages.py`；
- `pet/plugins/package_binding.py`；
- `pet/feature_package_startup.py`；
- `pet/feature_package_transactions.py`；
- `pet/feature_install_state.py`、`pet/feature_version_lease.py`；
- `pet/feature_management.py`、`pet/feature_lifecycle_contract.py`、`pet/feature_startup_contract.py`；
- `pet/runtime_layout.py`、`pet/credentials.py`、`pet/feature_probe_materials.py`。

`FeatureDefinition.owner` 必须等于 descriptor owner；execution kind 决定 in-process host 或 Worker 端口，不能由官方 owner 猜测。`ProductionFeatureStartup` 在首次正常 Core 启动后完成 receipt 确认；Setup 维护流程只负责把事务推进到 `awaiting_startup_confirmation`，不伪造生产启动确认。

### 3.5 管理 UI 统一入口并自动路由

扩展管理页增加统一入口：

> **选择本地功能包（ZIP/目录，自动识别）**

流程：

1. 用户选择 ZIP 或目录；
2. 受限读取 manifest，仅展示 id/factory/version/execution kind；
3. 按 owner 命中官方 manager 或创建/取得本地 DLC manager；
4. preflight 通过后，因为“用户明确选择文件”已经构成导入授权，直接 apply，不再弹二次确认窗口；
5. 首次安装默认启用；替换当前已加载版本时显示“重启后生效”；
6. 页面显示已导入的第三方包，提供启用、停用、卸载、回滚/诊断；
7. Screen 专用入口、AI 专用入口都调用同一 router，不能把 AI 包固定提交给 Screen manager。

未知 owner 不应被送入任意默认 manager；如果 manifest 不符合通用合同，显示实际字段和修复提示。

### 3.6 Setup 选装改为嵌入式官方包 + 无界面维护

`packaging/core_webm.iss` 的目标合同：

- 编译必须传入 `/DPackageDir=...`；缺少两个官方 ZIP 或 manifest id 不匹配时构建失败；
- 用 `[Files]` 将 `official.ai-chat.zip`、`official.screen-understanding.zip` 作为临时文件嵌入 Setup，不能在运行时读取 `{src}\\packages`；
- 安装向导的 `ai` / `screen` 任务仍默认不选；勾选即代表用户同意安装并启用对应官方包；
- `ssPostInstall` 或等价安装后阶段只解出所选 ZIP，调用 Core 的无界面维护参数；该入口不创建 `QApplication`、不显示本地包确认对话框；
- 维护入口接受 `completed`、`idempotent`、`awaiting_startup_confirmation` 为成功；其他结果写入 Setup 日志并以非零状态终止选装；
- 安装结束后按现有 Run 逻辑正常启动 Core，由生产启动流程完成 startup receipt；
- Setup 只安装官方两个任务，不负责第三方 DLC；Setup 外第三方仍走应用内显式导入。

首次正常启动看到入口/状态已通过当前冻结维护矩阵验证，而非只复制 ZIP；实际系统安装器中的勾选行为仍是另获授权的人工门。

### 3.7 Core 构建产物不显示终端

修改 `scripts/build_screen_delivery.py` 生成的 Core spec 使用 `console=False`。日志写入受控日志目录，不用终端作为正常观测接口。增加：

- 生成 spec 的文本回归，禁止 Core spec 出现 `console=True`；
- 构建后用 `pefile` 或同等可靠 PE 解析检查 `OPTIONAL_HEADER.Subsystem == 2`（Windows GUI）；
- 真实 Windows 启动检查无额外终端窗口；
- Worker、维护入口、probe/helper 仍分别按交互/诊断需求决定是否隐藏，不能一刀切。

## 4. T01–T07 实施顺序（执行结果见阶段记录与本轮报告）

### T01：建立基线和失败回归

- 读取本计划、交付文档和阶段记录；核对 dirty diff，不重置已有用户改动。
- 先写/运行未知 owner local v2 包、AI 从 Screen 入口路由、Setup 不依赖外部 packages、Core spec subsystem 的失败测试；保存 red 证据。
- 明确当前模块实际接口后再决定 `FeatureRegistration` 放置位置，避免预先创建重复抽象。

### T02：拆分官方身份与通用本地注册

- 扩展中性注册对象和 manifest 解析；保留 `OFFICIAL_FEATURES` 作为官方策略来源。
- 改 `FeaturePackageVerifier` 的 local/official 两条策略，覆盖无签名和旧签名字段。
- 将错误消息改成实际字段可诊断文本。

### T03：贯通事务、状态、lease 和启动

- 消除交易/安装状态/生命周期/版本租约对 `official_feature()` 的不必要调用。
- 复用同一状态账本记录第三方注册信息；保持原子 stage、rollback、startup receipt 和重启语义。
- 让 loader/binding/startup 根据 descriptor 运行，不允许 owner mismatch。

### T04：统一 UI router 和本地 DLC 管理

- 增加 bounded manifest pre-read 与自动 owner router。
- 让 ZIP/目录两种来源进入同一事务；应用内选择后自动 apply。
- 增加第三方列表、启停、卸载、回滚/诊断的最小可用入口；不做远程目录。

### T05：重写 Setup 选装

- 修改 Inno 脚本和维护入口参数；官方 ZIP 内嵌，删除旁置 packages 依赖。
- 增加编译前官方 manifest 检查和安装后返回码/日志合同。
- 运行无 QApplication 的维护测试，再进行真实 Setup 编译。

### T06：隐藏 Core 终端并补 PE 门

- 改 Core spec 为 GUI subsystem。
- 构建 Core，验证 PE subsystem、启动进程树和日志路径。

### T07：文档/测试/构建/交接收口

- 更新 Phase5A 设计、README、项目入口、Phase6 边界和 PR 报告；本轮已先完成文档口径，代码完成后只需补结果。
- 先专项，再相关族，再 Ruff/格式，再全量 pytest；包装改动必须重跑全量。
- 重新构建 Core 和 Setup，写入实机报告、文件清单、性能实测和准确停点。

## 5. 文件影响面和预期责任

| 文件/目录 | 本轮实施责任（原计划） | 关键验证 |
|---|---|---|
| `pet/official_features.py` 或新增中性注册模块 | 抽出/复用 `FeatureRegistration`，官方注册保持独立 | official/local 策略互不污染 |
| `pet/plugins/package_trust.py` | local mode 接受非官方 owner；保留结构/兼容/清单保护 | unknown owner、无 sig、旧 sig、坏 manifest |
| `pet/plugins/feature_packages.py`、`pet/plugins/package_binding.py` | descriptor 驱动 loader/binding/worker | host owner mismatch、第三方 host/worker 启动 |
| `pet/feature_package_transactions.py`、`pet/feature_install_state.py`、`pet/feature_version_lease.py` | 状态/事务/租约泛化且不重复造账本 | install/upgrade/rollback/disable/uninstall/restart |
| `pet/feature_package_startup.py`、`pet/feature_management.py`、`pet/feature_lifecycle_contract.py` | 去除不必要 official lookup | startup receipt、生命周期隔离 |
| `pet/feature_management_ui.py`、`pet/local_package_intents.py` | 统一导入入口、manifest router、auto apply | AI 从 Screen 入口、ZIP/目录一致、无二次确认 |
| `pet/app.py`、`pet/modern_settings_dialog.py`、`pet/feature_host.py`/contexts | 动态注册/管理第三方 DLC，保留受限 context | owner 隔离、菜单/设置撤销 |
| `packaging/core_webm.iss` | 内嵌官方 ZIP、无旁置依赖、维护入口返回码 | Setup 静态检查、安装矩阵 |
| `scripts/build_screen_delivery.py` | `console=False` | spec/PE/真实窗口 |
| `tests/` Phase5A、package、UI、build 相关测试 | 先红后绿补回归 | focused + full |

## 6. 自动化验证清单

必须至少覆盖：

- 本地 v2 未知 owner 可验证、安装、启动；
- official/signature mode 仍拒绝未知 owner；
- local mode 没有 `manifest.sig` 可以安装，有旧 `manifest.sig` 也不要求密钥；
- AI ZIP 从 Screen 专用入口选择会自动路由到 AI；
- 第三方 ZIP 与目录进入相同事务结果；
- 非法 id/factory/execution kind、路径穿越、重解析点、清单缺失/摘要错误、越界文件仍拒绝；
- `FeatureDefinition.owner` 不匹配时拒绝；
- 停用、卸载、升级、回滚和重启后状态正确；
- Setup 维护入口没有 QApplication/确认对话框，返回状态合同正确；
- Setup 选择 AI/Screen/both 首次 Core 启动即加载，外部旁置 `packages` 删除后仍可完成；
- Core spec 不含 `console=True`，最终 PE subsystem 为 GUI；
- 生成 Setup 的 `[Files]`/命令不依赖 `{src}\\packages`。

测试顺序：受影响族 → 相关包/Qt/生命周期/打包族 → `ruff check`、格式检查 → `python -m pytest -q` → Core 构建 → Inno Setup 构建。包装/生命周期/线程改动不得只跑旧 focused 结果。

## 7. 真实机器验收矩阵

本轮已从当前源码生成独立短路径 Core/Setup，记录命令、SHA、进程、日志、返回码和用户可见行为。下列为原完整人工矩阵：冻结维护/正常启动等已做的替代证据与尚未执行的真实系统安装、Provider、人工 picker/用户确认逐项区分，见本轮报告 §6；不能把替代证据填成原人工项通过。

1. 直接双击 Core：无终端窗口，桌宠正常出现；
2. Setup 不选装：只有 Core，扩展管理可用；
3. Setup 只选 AI；
4. Setup 只选 Screen；
5. Setup 同时选 AI + Screen；
6. 删除/重命名 Setup 旁置 `packages` 后重复 2–5 仍成功；
7. 扩展管理导入官方 AI ZIP；
8. 导入一个未知 owner 的第三方 ZIP；
9. 导入同一第三方目录；
10. 导入坏 manifest/错误摘要并确认错误可读；
11. 重启后第三方 DLC 仍在正确 owner 下加载；
12. 停用/卸载后入口和执行均消失；
13. 全流程不弹私钥/公钥输入，不生成签名要求。

真实 Provider 请求、屏幕截图识别、用户主观确认 Setup 文案/入口位置仍留给用户；不得读取、记录或代填用户密钥。

## 8. 完成标准、风险和回滚

### 完成标准

- Setup 官方选装为嵌入式、自动 apply、首次启动可见，且不需要外部 `packages`；
- 非 Setup ZIP/目录能按 manifest 自动识别并作为第三方 DLC 导入；
- local path 不再要求公钥/私钥/`manifest.sig`，但结构和事务保护仍在；
- Core 不创建终端窗口；
- 专项、全量、构建和真实机器证据对应同一源码工作树；
- 文档、五份阶段记录、PR 报告和交接没有把旧签名路线当作当前合同；
- 剩余人工项清楚列出，没有把未测写成通过。

### 主要风险

- 第三方包是用户主动信任的任意 Python 代码，可能以当前用户权限访问文件、网络和进程；这不是安全沙箱。若未来需要不可信代码，必须另开安全架构与威胁模型。
- Python host 不做热卸载；升级/替换已加载版本必须重启。
- manifest 注册只负责路由和 ABI 合同，不等于来源可信；在线目录/签名/撤销属于后续阶段。
- Setup 编译可能再次受 Windows 路径长度影响，必须用短 staging 根并记录实际命令，不修改源码来掩盖路径问题。

### 回滚策略

代码实现过程中每个里程碑先通过红/绿测试；失败时只回滚本轮明确修改，不使用 `git reset --hard`，不覆盖现有 dirty diff。Setup/安装测试只使用新建的自有 staging/data 根，失败包回滚不触碰用户真实数据。

## 9. 文档清理历史与当前实施状态

先前文档清理阶段已完成（历史）：

- 计划与代码实现交付文档落盘；
- Phase5A 设计、README、项目入口、Phase6 边界、索引和 PR 报告补充当前口径；
- `.scratch/phase5a-local-distribution` 删除 38 个未被文档引用的旧构建/诊断目录；释放结果写入阶段记录；
- 保留最终验收根、Setup 编译根/旁置包、引用中的历史证据和五份阶段记录。

文档清理结束时，产品/测试、Core/Setup 重建尚未实施；这是历史停点，不是当前待办。2026-10-07 自主实施已完成 T01–T06、最终全量/高负载、当前构建、授权实机和性能报告；T07 最后文档门和同组五份终态记录已完成。真实系统 Setup、Provider 与用户人工验收仍未执行，需后续独立授权。

## 10. 完成后的实际使用效果与限制

- 扩展管理可选择受信任 ZIP/目录，manifest 自动决定 owner/factory，第三方不再必须冒充官方 Screen。
- Setup 已编译为内嵌官方 AI/Screen 的离线选装物，主 Core 使用 GUI PE；真实系统安装器仍待用户指定隔离环境验收。
- 已加载 Python 的升级/卸载等待自然退出；本地包不是沙箱，不新增社区 SDK/签名撤销，也不宣布正式发布。


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

候选 Core 4.2.2 / AI 1.0.2 / Screen 1.0.1，新包要求 Core >=4.2.2。旧卸载器须覆盖安装新 Setup 后才可验收。保留当前脏树，不提交/推送，不读取真实 Key/截图/收费调用，不运行系统安装/卸载，不改代理/VPN，不自动开识屏、不清理未知 staging。构建空间不足先列确切生成物和影响并确认，用户已授权本轮增加约 5 GiB，阶段生成物预算为 11 GiB（11,811,160,064 B）；保留所有旧产物，不静默删除、不降低 reserve、不跨目录规避总量。硬门失败保留失败证据，不以旧结果冒充新绿灯。

### 留档、验证与回退

延续既有设计和 PLAN/HANDOFF/STATUS/WORKLOG/SUMMARY；原 T 历史保留。专项先 red 后 green，最终全套与产物重建；真实 Provider/屏幕/Setup 系统操作为用户门。回退仅按本轮逐文件差异，不覆盖用户改动、不 reset --hard。

### 完成后的实际使用效果

Core 配置一次 Key 并明确授权所需用途，保存后已有聊天窗口无需重启；手动看看屏幕不受自动开关误伤；退出后系统卸载不先拆 DLC，重装可识别有效保留包。限制：服务须支持对应模型/余额协议，真实服务与系统卸载必须以新 Setup 用户验收；工程自动通过不是 Phase5A 正式关闭。
