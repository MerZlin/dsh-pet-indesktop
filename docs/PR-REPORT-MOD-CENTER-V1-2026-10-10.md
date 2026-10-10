# MOD 管理中心与作者接口 v1 交付报告

> 日期：2026-10-10；分支：`codex/phase3-worker`；M00 远程检查点：`0a299612714e5fad55a24a5506dfce938b9eeb1c`。
> 本报告对应 M01–M05 的可运行本地检查点。用户已授权“先提交能运行的版本，小问题留档”，本报告不把未通过的满负载压力族改写为通过；检查点先在本地提交，随后按用户当轮授权正常推送到 `origin/codex/phase3-worker`（提交号以 `git log -1` 为准）。
> [实施计划](modding/MOD-CENTER-IMPLEMENTATION-PLAN-2026-10-10.md) · [教程入口](modding/README.md) · [连续状态](../.scratch/mod-authoring-v1/STATUS.md)。

## 一、当前结论与范围

已完成本地 MOD 管理中心、`pet.mod_api.v1` 薄接口、官方/第三方样例、角色副本和教程；新包默认停用，启用后列表、菜单和设置贡献即时刷新，角色“启用”不自动换装，“使用”只影响当前实例。Setup 安装/卸载逻辑未在本轮改变。

本检查点交付的是可运行候选，不是正式签名发布，也不等于用户已完成真实 Setup 安装/升级/卸载或真实 Provider/余额/屏幕验收。

## 二、修改文件说明

下表覆盖本次显式交付文件（含五份连续记录）；生成物、缓存、日志、临时测试数据、真实安装目录和敏感凭据均未纳入。`+/-` 为最终暂存差异中的新增/删除行；报告自身在暂存后再核对。

| 文件 | + | - | 改动目的 |
|---|---:|---:|---|
| `LOG-INDEX.md` | 2 | 1 | 登记 MOD 管理中心记录与检查点入口，便于回溯。 |
| `LOG.md` | 10 | 1 | 记录本地管理中心、公共接口及验收范围，区分历史交付与新功能。 |
| `README.md` | 2 | 2 | 增加本地 MOD 能力和分类教程入口，避免将其描述成联网商店。 |
| `SPEC.md` | 4 | 3 | 落地已确认的列表管理、即时启用、角色语义和 v1 兼容合同。 |
| `docs/INDEX.md` | 9 | 0 | 登记设计、教程和 PR 报告，满足文档入场规则。 |
| `docs/PROJECT-ENTRY.md` | 4 | 4 | 更新阶段入口及 MOD 作者导航，保留 Phase5A 历史证据。 |
| `pet/__init__.py` | 1 | 1 | 将候选 Core 版本提升到 4.2.5，不覆盖旧候选身份。 |
| `pet/app.py` | 24 | 11 | 挂接通用 MOD 与资源 IPC 生命周期；角色使用限定当前实例，回退释放实际素材。 |
| `pet/content/manager.py` | 33 | 8 | 支持资源导入默认停用、状态保留和受管理版本删除，保留外部原件。 |
| `pet/content/manifest.py` | 4 | 0 | 解析可选 description，兼容旧资源包。 |
| `pet/content/models.py` | 1 | 0 | 为资源模型补充纯文本说明字段。 |
| `pet/content/registry.py` | 3 | 1 | 保存资源启停和说明，资源仍使用自己的注册表。 |
| `pet/context_menu.py` | 6 | 0 | 将通用 MOD 命令挂到现有菜单，不按官方 ID 特判。 |
| `pet/feature_management.py` | 99 | 22 | 区分后台发现与显式命令完成；锁忙有限重试、停用导入、授权恢复，避免结果串线。 |
| `pet/feature_management_ui.py` | 22 | 27 | 旧管理组件订阅独立 load_changed，保持旧入口可用。 |
| `pet/feature_package_probe.py` | 2 | 2 | 让现有验证器理解通用 MOD 挂载声明，不将非官方样例当成官方私有包。 |
| `pet/feature_package_startup.py` | 16 | 3 | 保留当前选择解析时的锁忙原因，避免误报授权失效；真实解锁后可恢复。 |
| `pet/feature_package_transactions.py` | 41 | 5 | 支持默认停用导入；操作拥有锁阻止其他进程恢复仍在执行的事务。 |
| `pet/feature_probe_adapter.py` | 6 | 4 | 在现有 probe 边界传递通用包策略，不增加官方工厂分支。 |
| `pet/feature_state_io.py` | 18 | 2 | 对 Windows 短暂 sharing violation 做 250ms 有界原子替换重试，持续失败保留原文件。 |
| `pet/modern_settings_dialog.py` | 12 | 16 | 用统一列表替换旧卡片，局部更新导航与设置贡献，保留其他页草稿。 |
| `pet/plugins/feature_host.py` | 5 | 0 | 支持通用定义/挂载合同，保留旧 DLC 契约及命令撤销生命周期。 |
| `pet/plugins/package_trust.py` | 8 | 1 | 校验可选展示元数据和通用挂载声明，旧包仍按旧字段兼容。 |
| `pet/plugins/worker_launch.py` | 1 | 0 | 为通用 Worker 提供现有校验和租约启动路径，不绕过路径边界。 |
| `pet/settings_feature_lifecycle.py` | 24 | 2 | 公开设置组件草稿、保存、放弃和释放协议，保证局部刷新可退出。 |
| `pet/settings_widgets.py` | 3 | 0 | 补充适配新列表和说明的共享设置组件行为，减少重复表单。 |
| `pet/workers/lease_bootstrap.py` | 8 | 1 | 复用经验证的 Worker 租约交接，支持第三方示例入口。 |
| `scripts/build_feature_release.py` | 4 | 0 | 为官方新包提供名称/说明和 1.0.4 元数据，维持现有构建链。 |
| `scripts/build_screen_delivery.py` | 12 | 6 | 将通用 MOD 源码纳入 Core 冻结闭包，保留生产 Worker 输入核验。 |
| `scripts/feature_release_materials.py` | 2 | 0 | 更新官方包材料清单与展示信息，用新版本区分旧交付。 |
| `pet/mod_api/__init__.py` | 1 | 0 | 提供公开 API 包入口，明确版本化范围。 |
| `pet/mod_api/v1.py` | 47 | 0 | 提供定义、菜单、设置、私有配置/数据及按请求 API 解析薄封装。 |
| `pet/mod_api/worker_client.py` | 88 | 0 | 提供经过现有校验的 Worker 客户端、取消与自然退出。 |
| `pet/mod_api/worker_v1.py` | 118 | 0 | 提供离线 Worker 握手/消息/取消循环，供可运行样例复用。 |
| `pet/mod_catalog.py` | 80 | 0 | 以目录监视和轻量令牌通知发现新包，账本仍是唯一权威。 |
| `pet/mod_center_controller.py` | 253 | 0 | 串行执行导入/启停/批量操作，保留同笔确认，准确显示失败和待退出。 |
| `pet/mod_center_ui.py` | 273 | 0 | 实现搜索、类型筛选、单行操作与批量栏的简洁本地列表。 |
| `pet/mod_core.py` | 44 | 0 | 集中挂载 Core 的通用运行时、目录通知及角色 IPC。 |
| `pet/mod_management.py` | 166 | 0 | 为功能包和资源包提供薄列表适配，不另建统一账本。 |
| `pet/mod_resource_ipc.py` | 234 | 0 | 跨进程定位当前实例及实际素材源，安全切换和回退。 |
| `pet/mod_runtime.py` | 88 | 0 | 按声明挂载第三方命令与运行时；停用后撤销旧句柄。 |
| `pet/mod_settings.py` | 159 | 0 | 局部挂载 MOD 设置，保护其他页面草稿及保存/放弃语义。 |
| `scripts/benchmark_mod_center.py` | 88 | 0 | 提供可复现的空闲检查和列表构建时间/RSS测量。 |
| `scripts/build_character_mod_example.py` | 55 | 0 | 复制角色并修改一项可见动作，原素材不变，生成物不进仓库。 |
| `scripts/build_mod_echo.py` | 74 | 0 | 构建带真实离线子进程的 Echo Worker 示例包。 |
| `scripts/build_mod_example.py` | 87 | 0 | 把示例目录打成可验证 ZIP，支持 host-only/host-worker 同一入口。 |
| `docs/PR-REPORT-MOD-CENTER-V1-2026-10-10.md` | 186 | 0 | 逐文件、性能、实机和验证证据；记录未通过压力门，不作无缺陷发布声明。 |
| `docs/modding/API-V1.md` | 88 | 0 | 新增公共方法、生命周期、数据归属与禁止依赖的私有接口。 |
| `docs/modding/CHARACTER-PACKS.md` | 69 | 0 | 新增素材格式、manifest、手动/受管理路径及角色副本实测。 |
| `docs/modding/COMPATIBILITY.md` | 30 | 0 | 新增v1稳定边界、未来拆分影响矩阵及破坏性v2迁移约定。 |
| `docs/modding/HOST-ONLY.md` | 43 | 0 | 新增非官方离线菜单/设置/配置样例的可执行步骤。 |
| `docs/modding/HOST-WORKER.md` | 53 | 0 | 新增Echo握手、请求返回、取消和自然退出教程。 |
| `docs/modding/MOD-CENTER-IMPLEMENTATION-PLAN-2026-10-10.md` | 85 | 0 | 新增M00–M05范围、授权、验收合同及可运行检查点策略。 |
| `docs/modding/OFFICIAL-DLC.md` | 51 | 0 | 新增AI/识屏调用链，区分公共合同与内部接线。 |
| `docs/modding/PERSONA-TEMPLATES.md` | 30 | 0 | 新增persona-phrases/v1模板导入导出，不伪装成独立功能包。 |
| `docs/modding/README.md` | 19 | 0 | 新增用户/作者分类教程总入口。 |
| `docs/modding/USER-GUIDE.md` | 42 | 0 | 新增导入、启停、使用、更新和批量删除的直接操作说明。 |
| `examples/mods/README.md` | 8 | 0 | 新增示例：说明三个示例的用途、构建命令和生成物位置。 |
| `examples/mods/hello-local/host/__init__.py` | 0 | 0 | 新增示例：标明轻量包边界，导入不主动启动 GUI。 |
| `examples/mods/hello-local/host/factory.py` | 57 | 0 | 新增示例：用非官方 ID 演示通用菜单与启停。 |
| `examples/mods/hello-local/host/settings.py` | 47 | 0 | 新增示例：演示配置、草稿和设置保存协议。 |
| `examples/mods/hello-local/mod.json` | 8 | 0 | 新增示例：声明 host-only 身份、版本和功能说明。 |
| `examples/mods/echo-worker/host/__init__.py` | 0 | 0 | 新增示例：保持 host 包初始化无副作用。 |
| `examples/mods/echo-worker/host/factory.py` | 86 | 0 | 新增示例：演示通用 Worker 客户端及菜单调用。 |
| `examples/mods/echo-worker/host/settings.py` | 47 | 0 | 新增示例：演示请求文本草稿、取消与结果展示。 |
| `examples/mods/echo-worker/mod.json` | 8 | 0 | 新增示例：声明 host-worker 身份、执行入口和展示元数据。 |
| `examples/mods/echo-worker/worker-src/entry.py` | 13 | 0 | 新增示例：实现真实离线 Echo 子进程，不依赖 Provider。 |
| `examples/mods/persona-phrases.json` | 20 | 0 | 新增示例：提供可导入的台词/人格模板。 |
| `tests/_feature_ui_child.py` | 17 | 25 | 测试：修正旧界面子进程验证入口，保持既有流程覆盖。 |
| `tests/test_ai_delivery_boundaries.py` | 2 | 1 | 测试：更新新版本/通用接线断言，保持 AI 不回迁 Core。 |
| `tests/test_feature_management_ui.py` | 18 | 45 | 测试：覆盖旧组件状态通知兼容，防止新列表破坏遗留入口。 |
| `tests/test_feature_package_probe.py` | 3 | 3 | 测试：验证通用样例工厂及 probe 边界。 |
| `tests/test_feature_probe_adapter.py` | 10 | 1 | 测试：验证通用策略封装与沙箱调用边界。 |
| `tests/test_phase5a_repair_regressions.py` | 1 | 1 | 测试：调整当前候选契约断言，保留已验收修复门。 |
| `tests/test_phase5a_t01_regressions.py` | 6 | 6 | 测试：延续无硬编码官方工厂的回归边界。 |
| `tests/test_portable_worker_launch.py` | 1 | 0 | 测试：覆盖项目内数据根和通用 Worker 启动的安全边界。 |
| `tests/test_screen_delivery_build.py` | 2 | 2 | 测试：验证冻结闭包/新版本及 Worker 材料。 |
| `tests/test_single_process_spawn.py` | 11 | 0 | 测试：使角色切换替身符合实际素材释放契约。 |
| `tests/test_feature_state_io.py` | 68 | 0 | 测试：真实 Windows 共享冲突红灯/恢复/持续拒绝保护旧文件。 |
| `tests/test_feature_transaction_live_owner.py` | 61 | 0 | 测试：真实子进程不能恢复仍由安装者执行的事务。 |
| `tests/test_mod_center_contracts.py` | 178 | 0 | 测试：通用包元数据、导入停用、资源状态和删除边界。 |
| `tests/test_mod_center_integration.py` | 197 | 0 | 测试：即时启用、局部设置刷新及草稿保护。 |
| `tests/test_mod_center_ui.py` | 106 | 0 | 测试：筛选/选择/批量、主题和窄宽布局。 |
| `tests/test_mod_live_core.py` | 181 | 0 | 测试：实际 Core/设置进程发现、启停、旧菜单撤销及锁竞争。 |
| `tests/test_mod_management_contention.py` | 144 | 0 | 测试：真实内核锁下发现/命令/授权通知的回归与恢复。 |
| `tests/test_mod_resource_ipc.py` | 82 | 0 | 测试：角色单实例使用、实际来源识别和回退。 |
| `tests/test_mod_worker_api.py` | 164 | 0 | 测试：真实 Echo 消息、取消、过期结果和自然退出。 |
| `.scratch/phase5a-local-distribution/PLAN.md` | 12 | 0 | 持续记录：任务与验收状态；不把历史通过覆盖当前失败。 |
| `.scratch/phase5a-local-distribution/HANDOFF.md` | 12 | 0 | 持续记录：精确停点和复验入口；不把历史通过覆盖当前失败。 |
| `.scratch/phase5a-local-distribution/STATUS.md` | 12 | 0 | 持续记录：当前验证/提交/剩余问题；不把历史通过覆盖当前失败。 |
| `.scratch/phase5a-local-distribution/WORKLOG.md` | 12 | 0 | 持续记录：历史操作、红绿灯和用户决策；不把历史通过覆盖当前失败。 |
| `.scratch/phase5a-local-distribution/SUMMARY.md` | 12 | 0 | 持续记录：跨对话摘要与保护边界；不把历史通过覆盖当前失败。 |

| `.scratch/mod-authoring-v1/PLAN.md` | 40 | 0 | 持续记录：任务与验收状态；不把历史通过覆盖当前失败。 |
| `.scratch/mod-authoring-v1/HANDOFF.md` | 43 | 0 | 持续记录：精确停点和复验入口；不把历史通过覆盖当前失败。 |
| `.scratch/mod-authoring-v1/STATUS.md` | 41 | 0 | 持续记录：当前验证/提交/剩余问题；不把历史通过覆盖当前失败。 |
| `.scratch/mod-authoring-v1/WORKLOG.md` | 91 | 0 | 持续记录：历史操作、红绿灯和用户决策；不把历史通过覆盖当前失败。 |
| `.scratch/mod-authoring-v1/SUMMARY.md` | 37 | 0 | 持续记录：跨对话摘要与保护边界；不把历史通过覆盖当前失败。 |

## 三、实现与兼容边界

- 管理中心以本地列表呈现扩展，支持全部/角色资源/功能扩展筛选、搜索、导入 ZIP/目录、单项启停/设置/更新以及批量启用/停用/删除。导入默认停用；删除保留外部 ZIP/源目录和个人数据，不删除 Core 内置素材。
- 功能包和资源包通过薄适配接入现有账本与租约，不按官方 owner/factory 写特判。`pet.mod_api.v1` 公开定义、菜单、设置、包内配置/数据、按请求 API 快照和受校验 Worker 生命周期；受信任 Python/Worker 不宣称为沙箱。
- 教程和样例包含 host-only、Echo Worker、角色资源、persona-phrases、AI 对话与识屏调用链，以及后续音乐/语音/Agent 拆包的 v1 兼容约定。
- API 简易设置、项目目录 Setup、Core-only 系统集成清理和既有 AI/识屏调用边界保持不变。

## 四、性能分析

环境：Windows 11 build 26100，Python 3.11.1；命令均在本仓库工作区执行，生成物不纳入提交。

1. `python -m scripts.benchmark_mod_center .scratch/mod-authoring-v1/performance-1`：空闲目录检查 1000 次，中位 **1.1335 ms**、均值 **1.1626 ms**、最大 **1.8860 ms**；列表构建 10 次的中位数为 2/20/100 项 **4.6513/21.7317/111.2925 ms**。
2. 资源占用样本：2/20/100 项列表构建后的 RSS 样本约从 **65,589,248→65,736,704**、**67,690,496→68,460,544**、**73,572,352→77,283,328 bytes**；报告保留原始数组，不声称零增长或无泄漏。
3. `atomic-performance.json`：100 次 16-byte 原子写，中位 **5.4295 ms**、均值 **5.5147 ms**、最大 **8.0332 ms**。新增 Windows sharing violation 仅做 250 ms 有界重试，持续失败保留旧文件。
4. 操作 owner lock 1000 次未竞争 acquire/release，中位 **0.53095 ms**、均值 **0.60287 ms**、最大 **9.53030 ms**；每次事务生命周期只持有短锁，不新增常驻线程、网络请求或持续轮询。
5. 实际冻结链稳定采样 20 次：Core/Worker 保持独立进程，Worker RSS 约 25–26 MiB；生产 Worker 只按识屏生命周期按需启动，链路没有真实 Provider 请求或截图。

## 五、实机运行记录

### 5.1 final4 产物审计

命令：`python -X utf8 .scratch/mod-authoring-v1/audit_final4.py`。结果：`artifact-final4-audit.json` `complete=true`；Core 文件清单 2114 项，Core/Setup 资源组均与 `assets/icon.ico` 的鲸鱼图标帧匹配；两个官方包均为 1.0.4，Core 要求 `>=4.2.5,<6.0.0`；旧 4.2.4 Setup SHA-256 保持 `7282fc05ac0b14138f00d4dbb71edf73af82aee261500f59f2217e8a1756a40d`。

主要候选产物：

- Core：`_m05b/final4/c/dist/dsh-pet-core-webm/dsh-pet-core-webm.exe`，SHA-256 `7c02776f3a8635d76e49acfe7305e2e63a1b8ca9c7cda39eb243c82815bf07bd`。
- Setup：`_m05b/final4/setup/dsh-pet-core-webm-setup.exe`，SHA-256 `d896b7bd776416a3fdb35e3db790a84699456ae8a91bee9b51b2261cade652f9`。
- Screen Worker：沿用未变化输入，SHA-256 `52f2b0785e34b07dfbc7997faed1d81c6bbc77d7608910a6418d9d58e6d249cb`。

### 5.2 冻结 Core → 生产 Worker 完整链路

在独立 `_m05b/runtime3` 数据根执行，不触碰真实 `E:\dsh-pet-core-webm`。`frozen-final4-clean-production-chain.json` 记录：Core maintenance 安装两个官方包成功；Worker 从 `<项目目录>\data\feature-runtime` 启动，真实父子进程完成 starting → handshaking → ready → stopping → stopped；READY **6.844 s**；Core/Worker exit **0**；退出后租约状态 **free**；20 次稳定采样；假服务 HTTP 请求 **0**；不读取真实 Key、不截图。中断留下的旧隔离现场另存为 `data-interrupted-20261010-1634`，未用删除残留记录的方式伪造 free。

### 5.3 final4 Settings 扩展管理页

`frozen-final4-ui-smoke.json`：真实冻结 Core 与独立 Settings 在 Windows 原生窗口启动；扩展页可见「导入 ZIP」「导入目录」「全部」「角色资源」「功能扩展」，设置与 Core 均 exit 0。该烟测不替代完整人工 UI/真实 Setup 验收。

## 六、自动化验证

- Full12：完整收集 4578 项；275 个完整测试文件分别在新进程执行，2 文件并发，**4563 passed / 15 skipped / exit 0 / 528.922 s**；没有遗漏节点。该策略用于规避历史 Qt 原生崩溃，不等同于单进程 `pytest -q` 全绿。
- MOD checkpoint 专项：47 passed / 30.52 s。
- 相关 authority live-load 诊断：3 轮×4 项通过；不替代完整 41 文件满负载门。
- Ruff：`python -X utf8 -m ruff check pet scripts tests` 通过。
- `git diff --check` 通过。

### 已知未通过/未完成门

用户已允许先提交可运行版本，以下事实保留，不写成通过：

1. highload12 第一轮在满 CPU 下出现一次生产识屏短预算握手未在 8 秒内进入 endpoint；普通负载冻结完整链通过，原因是否仅为压力预算仍需后续单独确认。
2. 满 CPU 下 `tests/test_single_process_spawn.py` 有 3 项清理时序未在测试预算内完成；不是本轮正常负载验收失败。
3. 满 CPU 下 `tests/test_mod_live_core.py` 的跨进程停用未达到测试预期；导入/启用成功，但停用结果未由原测试断言成功，不能推断为产品已完全通过。
4. highload12 只执行了第一轮，第二/三轮未执行；不得用 3×4 的诊断族替代 41 文件整族三轮。
5. **ZIP 接入的 MOD 无法启用（用户实机报告，未解决）**：用户报告 ZIP 导入的外接 MOD 启用失败、同一内容走目录接入正常。本地探针（真实控制器/校验/事务，本机无沙箱故功能包仅替换 `self_checker`）**未能复现**该形态；根目录 ZIP 的导入与启用与目录一致，已证实的是「ZIP 内多套一层文件夹」会判 `manifest missing` 导入失败、而解压后选内层目录正常。触发条件待用户补充，规避办法是用「导入目录」；详见[实施计划的已知缺陷](modding/MOD-CENTER-IMPLEMENTATION-PLAN-2026-10-10.md#已知未解决缺陷zip-导入的-mod-无法启用2026-10-10)，证据为 `.scratch/mod-authoring-v1/repro-zip-enable.json` 与 `repro-zip-wrapper.json`。**不得表述为已修复。**

## 七、人工验收保留项

用户仍需用新 Setup（不是旧 4.2.4 Setup）完成：

1. 安装到新的空项目目录，确认路径页、鲸鱼图标、Core/AI/识屏安装和项目内 `data` 布局。
2. 打开扩展管理，导入 Echo/角色示例或其他 ZIP/目录，确认默认停用、启用后无需关闭设置即可看到入口/设置；测试角色“启用”不换装、“使用”只换当前实例。
3. 使用真实主 API/视觉 API 验证聊天、余额、识屏和错误提示；不把本地假服务证据当成真实 Provider 结果。
4. 分别执行保留 data 与删除 data 的卸载，确认目录内重要文件提示、DLC/外部源 ZIP/源目录边界。
5. 在不同窗口宽度、明暗主题和真实桌面进程下确认列表布局、长描述、空列表、失败/待退出状态。

## 八、提交、回滚与当前限制

本轮仅创建本地检查点提交，不推送；提交前会显式审核暂存文件、敏感内容和生成物排除。回滚使用该提交的父提交或反向补丁，不使用 `reset --hard` 覆盖工作区。

当前可运行效果：用户可在本地列表管理扩展，导入后启用并即时看到功能；作者可按 v1 教程制作 host-only/Worker/角色样例。当前限制：不是联网市场、不是安全沙箱；新候选尚未由用户完成真实 Setup/Provider/卸载验收，满 CPU 压力族留档，Phase5A 不自动关闭。
