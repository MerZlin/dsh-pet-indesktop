# Phase 4A 第四步：功能 host 迁移与自包含 Worker 独立构建报告

> 日期：2026-09-29；分支：`codex/phase3-worker`；Git HEAD：`b97112d`。
> 本轮基线不是干净 HEAD，而是前三个切片已经存在的未提交工作树。未暂存、未提交、未推送；不启动子智能体。
> 结论：第四步 Windows 自动化及独立验证产物闭环已建立；**并非正式安装卸载或人工验收完成**。
> 设计：[Phase 4A 最小拆包合同](plugin-phase-04-updates/PHASE4A-SCREEN-PACKAGE-DESIGN.md)；[阶段入口](plugin-phase-04-updates/README.md)；[文档索引](INDEX.md)。

## 一、核心特性与完成边界

- 屏幕理解的 host/common/worker 成为一份权威源码；旧路径为兼容 facade。host 在 GUI 进程内运行，但代码随外置功能包交付。
- Core 向 host 提供窗口快照、配置/数据事务、凭据、贡献、展示及 Worker 端口，不传整个 PetWindow、AppShell 或 Config。
- 聊天拥有 `chat.external-turn/v1` 的会话业务；有聊天时传全文、按窗口/实例/角色路由及幂等持久化；缺少聊天不影响识屏。
- 官方功能目录经 Ed25519/完整清单/路径/兼容校验后才可加载，生成 `VerifiedFeatureDescriptor`；加载固定 factory，不接受任意第三方入口。
- 两个真实验证 Core 都不含识屏专属实现，其中一个也不含聊天；外置 Worker 自含解释器/依赖，不从 Core 的 `_internal` 补齐。
- 外置模式重启耗尽后暂停、报错并允许显式重试，不回退 Core 执行截图/网络；源码与原默认完整构建仍保留已有回滚路径。
- **本步不提供应用内安装/卸载、安装状态权威或正式签名信任锚，不替换原默认构建。**手测状态仍为当前独立视觉配置版本未验收。

## 二、修改文件说明

### 统计口径

下表相对本步开始时 `.scratch/phase4a-host-build/baseline/` 的文件快照统计，使用 `git diff --no-index --numstat <快照文件> <当前文件>`；新增文件与空文件比较。Git `diff --numstat` 相对 HEAD 还包括前三切片，不能冒充本步增量。快照摘要见 `baseline-hashes.json`。

除本报告外，**104 个文件，57 个本步新增、47 个修改，+10821 / −4297 行；没有删除文件**。本报告是另一个新增文件，自引用行数不计入这组统计。原兼容文件保留，不把内容迁移标为用户数据删除。

### 实现与构建

| 文件 | 本步状态及增删 | 改了什么、为什么 |
|---|---|---|
| `features/__init__.py` | 新增 +1/−0 | 声明规范包边界；导入不启动 UI、Worker、系统查询或网络任务。 |
| `features/screen_understanding/__init__.py` | 新增 +1/−0 | 声明规范包边界；导入不启动 UI、Worker、系统查询或网络任务。 |
| `features/screen_understanding/common/__init__.py` | 新增 +1/−0 | 声明规范包边界；导入不启动 UI、Worker、系统查询或网络任务。 |
| `features/screen_understanding/common/hashing.py` | 新增 +43/−0 | 纯 dHash 与距离计算；Worker 不再反向导入含 GUI 的策略模块。 |
| `features/screen_understanding/common/models.py` | 新增 +132/−0 | 独立视觉配置及请求模型的唯一来源，兼容路径仅重导出。 |
| `features/screen_understanding/common/policy.py` | 新增 +103/−0 | 纯策略判断与常量，可在 host/Worker 共享而不引入 Qt。 |
| `features/screen_understanding/host/__init__.py` | 新增 +1/−0 | 声明规范包边界；导入不启动 UI、Worker、系统查询或网络任务。 |
| `features/screen_understanding/host/config.py` | 新增 +147/−0 | 独立视觉配置解析只依赖限定配置和凭据端口。 |
| `features/screen_understanding/host/contribution_settings.py` | 新增 +105/−0 | 设置组件和草稿生命周期随功能交付，不向页面传入全局配置。 |
| `features/screen_understanding/host/contributions.py` | 新增 +62/−0 | 功能拥有菜单、设置、搜索等描述，Core 只挂载和撤销。 |
| `features/screen_understanding/host/factory.py` | 新增 +82/−0 | 固定官方 factory 按 FeatureHostContext 创建 host，并将 Qt bridge 与业务运行时分开。 |
| `features/screen_understanding/host/limiter.py` | 新增 +187/−0 | 额度与限流策略归功能 host，通过限定状态文件端口沿用原持久化。 |
| `features/screen_understanding/host/manual.py` | 新增 +199/−0 | 手动识屏忙碌/冷却/取消、请求与结果展示迁入 host，支持来源窗口文字同步。 |
| `features/screen_understanding/host/memory.py` | 新增 +78/−0 | 记忆读写归功能，用受限数据端口保留现有内容与路径。 |
| `features/screen_understanding/host/migration.py` | 新增 +142/−0 | 确认迁移和恢复改用授权来源/事务端口，不在正常执行读取聊天配置。 |
| `features/screen_understanding/host/presentation.py` | 新增 +30/−0 | 统一结果展示与可选文字同步；聊天不可用不阻断识屏气泡。 |
| `features/screen_understanding/host/runtime.py` | 新增 +1172/−0 | 自动策略、generation、限流、配置变化和退出的权威 host 实现，只使用受限端口。 |
| `features/screen_understanding/host/runtime_context.py` | 新增 +42/−0 | 将 host 内部策略状态与外部窗口/配置对象分开，显式维护兼容偏好映射。 |
| `features/screen_understanding/host/settings.py` | 新增 +183/−0 | 独立视觉编辑和确认迁移 UI 随功能交付，延续显式保存与安全存储语义。 |
| `features/screen_understanding/host/settings_context.py` | 新增 +16/−0 | 设置组件仅接收配置/凭据/迁移需要的受限上下文。 |
| `features/screen_understanding/host/strategy_settings.py` | 新增 +275/−0 | 自动识屏策略设置随功能交付，保留字段、平台限制与搜索行为。 |
| `features/screen_understanding/host/worker_adapter.py` | 新增 +376/−0 | 请求路由、预算、取消及故障适配归 host；外置模式禁止 in-process fallback。 |
| `features/screen_understanding/worker/__init__.py` | 新增 +1/−0 | 声明规范包边界；导入不启动 UI、Worker、系统查询或网络任务。 |
| `features/screen_understanding/worker/__main__.py` | 新增 +6/−0 | 独立冻结 Worker 入口，避免导入 host 或 GUI。 |
| `features/screen_understanding/worker/runtime.py` | 新增 +714/−0 | Worker 控制循环、帧缓存、请求线程与临时凭据处理的唯一实现。 |
| `features/screen_understanding/worker/vision.py` | 新增 +320/−0 | 截图、JPEG、前台描述与视觉 HTTP 执行归 Worker，不读取配置/keyring/聊天。 |
| `packaging/phase4a_synthetic_worker.py` | 新增 +46/−0 | 只用于隔离验证产物的确定性图像/前台替身，不进入普通 Worker 或新增生产测试操作。 |
| `packaging/phase4a_validation_entry.py` | 新增 +410/−0 | 只用于独立验证 Core：真实 AppShell/窗口/设置/外部 Worker、聊天有无及本地 HTTP，退出排空异步进程。 |
| `pet/app.py` | 修改 +59/−40 | 通过受限 host 绑定接入功能；退出不强制导入可选聊天会话模块，避免无聊天冻结程序退出失败。 |
| `pet/chat/external_turns.py` | 新增 +77/−0 | 聊天拥有外部文字接收、会话选择、繁忙判断及幂等处理，不让 Core 保存聊天业务状态。 |
| `pet/chat/legacy_widgets.py` | 修改 +8/−0 | 保留 legacy Chat UI 的外部问答追加兼容行为并委托聊天接收逻辑。 |
| `pet/chat/session_store.py` | 修改 +23/−0 | 为外部问答增加可持久化结果身份，重复结果不再次插入会话。 |
| `pet/chat/widgets.py` | 修改 +8/−0 | 现代 Chat UI 接入聊天侧外部文字服务，兼容窗口未打开时的持久化。 |
| `pet/feature_bindings.py` | 修改 +3/−1 | 贡献绑定走受控功能设置 factory，不直接创建识屏专属页面。 |
| `pet/feature_config.py` | 新增 +111/−0 | 限定功能配置命名空间、版本检查及事务保存；不向 host 传全局 Config。 |
| `pet/feature_data.py` | 新增 +123/−0 | 将额度、记忆、迁移日志等现有路径包装为限定读写/锁定端口，不迁移用户数据。 |
| `pet/feature_distribution.py` | 新增 +3/−0 | 为构建变体提供外置功能分发模式常量，默认完整构建行为不变。 |
| `pet/feature_host_bindings.py` | 新增 +232/−0 | Core 内部对象到窗口快照、配置、凭据、展示及聊天服务的适配桥，不向功能暴露 PetWindow。 |
| `pet/feature_ports.py` | 新增 +96/−0 | 定义不可变窗口快照、受限窗口端口和 FeatureHostContext，统一官方功能入口。 |
| `pet/modern_settings_dialog.py` | 修改 +3/−1 | 设置宿主按可用功能绑定创建页面，不直接依赖屏幕理解 UI。 |
| `pet/multi_window_shared.py` | 修改 +46/−18 | 共享模式通过 host 绑定维护单 Worker、窗口路由与引用生命周期，关闭一窗不误停其他窗口。 |
| `pet/official_features.py` | 修改 +5/−2 | 显式区分默认内置 factory 与验证构建外置功能，缺包不创建专属实现。 |
| `pet/plugins/feature_host.py` | 修改 +36/−1 | 扩展现有贡献宿主与受限功能状态，复用既有注册/撤销机制。 |
| `pet/plugins/feature_packages.py` | 新增 +311/−0 | 验证后快照加载、版本命名空间和进程使用租约，不修改全局 sys.path 或执行任意 entrypoint。 |
| `pet/plugins/package_binding.py` | 新增 +92/−0 | 已验证功能绑定 settings/host/Worker 同一版本，尊重设置草稿撤销否决和 QObject 生命周期。 |
| `pet/plugins/package_trust.py` | 新增 +461/−0 | Ed25519 验证 manifest 原始字节及闭合文件清单，拒绝逃逸、链接、未知信任和不兼容包。 |
| `pet/plugins/services.py` | 新增 +118/−0 | owner 授权、窗口/实例/角色路由与注册身份保护的最小文字服务，JSON 有界且接收异常隔离。 |
| `pet/plugins/worker_launch.py` | 新增 +46/−0 | 将已验证包内 Worker 变为受控启动描述，启动前复核并保持版本占用。 |
| `pet/proactive.py` | 修改 +30/−1175 | 旧自动识屏路径改成薄兼容 facade；真实策略归 feature host，保留旧测试/调用入口。 |
| `pet/proactive_limiter.py` | 修改 +37/−362 | 策略和额度实现迁到功能包，旧入口保留兼容映射与导出。 |
| `pet/proactive_memory.py` | 修改 +7/−80 | 记忆实现迁到功能包，旧导入路径仍兼容，不改变既有数据路径。 |
| `pet/screen_understanding/config.py` | 修改 +13/−141 | 将配置解析改为权威 feature 实现的薄兼容导出，保留既有源码调用，不复制业务。 |
| `pet/screen_understanding/contribution_settings.py` | 修改 +5/−98 | 将贡献设置组件改为权威 feature 实现的薄兼容导出，保留既有源码调用，不复制业务。 |
| `pet/screen_understanding/contributions.py` | 修改 +19/−52 | 将贡献描述改为权威 feature 实现的薄兼容导出，保留既有源码调用，不复制业务。 |
| `pet/screen_understanding/host_binding.py` | 新增 +26/−0 | 源码/default 模式的显式 host factory 桥；无识屏 Core 不收集此兼容层。 |
| `pet/screen_understanding/migration.py` | 修改 +4/−149 | 将确认迁移改为权威 feature 实现的薄兼容导出，保留既有源码调用，不复制业务。 |
| `pet/screen_understanding/models.py` | 修改 +4/−130 | 将请求模型改为权威 feature 实现的薄兼容导出，保留既有源码调用，不复制业务。 |
| `pet/screen_understanding/presentation.py` | 修改 +5/−26 | 将结果展示改为权威 feature 实现的薄兼容导出，保留既有源码调用，不复制业务。 |
| `pet/screen_understanding/settings.py` | 修改 +5/−177 | 将独立视觉设置改为权威 feature 实现的薄兼容导出，保留既有源码调用，不复制业务。 |
| `pet/screen_understanding/strategy_settings.py` | 修改 +5/−271 | 将策略设置改为权威 feature 实现的薄兼容导出，保留既有源码调用，不复制业务。 |
| `pet/screen_understanding/window_actions.py` | 修改 +5/−61 | 将手动执行改为权威 feature 实现的薄兼容导出，保留既有源码调用，不复制业务。 |
| `pet/vision.py` | 修改 +3/−316 | 视觉执行迁到功能 worker，旧入口仅兼容导出；基础桌宠不为平台查询导入它。 |
| `pet/window.py` | 修改 +30/−39 | 手动识屏、状态变化和关闭委托 host，窗口继续负责通用展示。 |
| `pet/window_optional_services.py` | 修改 +6/−2 | 可选服务入口尊重功能可用状态，缺包不导入识屏执行。 |
| `pet/workers/__init__.py` | 修改 +8/−1 | 内置 Worker allowlist 路由到权威实现，保持 Agent Link 与既有 CLI 兼容。 |
| `pet/workers/launch.py` | 新增 +69/−0 | 独立 Worker 程序/参数数组、工作目录和隔离子环境，不通过 shell 或更改 GUI DLL 搜索状态。 |
| `pet/workers/proactive_screen_adapter.py` | 修改 +4/−370 | 仅兼容导出 feature host 的请求适配器，避免两份实现。 |
| `pet/workers/proactive_screen_worker.py` | 修改 +9/−709 | 仅兼容路由独立 feature worker，不改变 pet-worker/v1。 |
| `pet/workers/supervisor.py` | 修改 +70/−10 | 接受受控外部启动描述；连接回调使用弱引用和进程身份，修复 QObject 销毁时的强引用生命周期环。 |
| `requirements.txt` | 修改 +3/−0 | 声明 cryptography 用于官方包 Ed25519 验证；不引入自制密码算法。 |
| `scripts/build_screen_delivery.py` | 新增 +400/−0 | 独立构建两个无识屏 Core、组装测试签名功能包并审计 PYZ/文件清单，不修改默认发布构建。 |
| `scripts/build_screen_worker.py` | 新增 +225/−0 | 独立 onedir Worker 构建及依赖审计；测试图像变体单独标记，拒绝 Qt/chat/keyring/host 混入。 |
| `scripts/verify_screen_worker.py` | 新增 +235/−0 | 真实冻结 Worker 握手/心跳/取消/退出、RSS/CPU 与 native DLL 来源验证，保存结构化证据。 |

### 测试

| 文件 | 本步状态及增删 | 改了什么、为什么 |
|---|---|---|
| `tests/screen_runtime_probe.py` | 修改 +1/−1 | 独立进程 probe 的系统替身移到权威模块，不 mock 最终查询结果。 |
| `tests/test_external_worker_launch.py` | 新增 +223/−0 | 外部程序/环境/工作目录和启停；真实子进程回归 Supervisor 回调不保留父对象的弱引用边界。 |
| `tests/test_feature_config_ports.py` | 新增 +142/−0 | 命名空间、事务、迁移数据、限流与记忆端口不越权且保留旧状态。 |
| `tests/test_feature_gating.py` | 修改 +2/−1 | 更新缺功能 import guard，确认默认可选行为与新边界一致。 |
| `tests/test_feature_package_binding.py` | 新增 +123/−0 | 真实设置组件、版本占用、撤销/销毁与重新注册边界。 |
| `tests/test_feature_packages.py` | 新增 +582/−0 | 签名、清单、路径/链接/大小写、篡改、加载前拒绝、命名空间及 lease 安全边界。 |
| `tests/test_feature_runtime_ports.py` | 新增 +121/−0 | 真实 Qt 窗口经 snapshot/ports 工作；显式 dispose，避免测试遗留 QObject。 |
| `tests/test_feature_settings_ports.py` | 新增 +99/−0 | 设置无全局 Config、保存/迁移端口及无执行副作用；清理真实组件。 |
| `tests/test_feature_text_service.py` | 新增 +198/−0 | 无聊天、繁忙、关闭窗口、幂等持久化、路由身份及接收失败不影响识屏。 |
| `tests/test_proactive.py` | 修改 +2/−2 | 旧兼容入口委托的 monkeypatch 位置适配，不放宽策略结果断言。 |
| `tests/test_proactive_worker_adapter.py` | 修改 +17/−0 | 外置模式失败不回退 Core，默认兼容模式仍允许旧 fallback。 |
| `tests/test_proactive_worker_source.py` | 修改 +2/−2 | Worker 系统/网络替身指向单份权威实现。 |
| `tests/test_screen_configuration.py` | 修改 +1/−1 | 配置执行边界替身改为权威模型路径，保留独立视觉语义断言。 |
| `tests/test_screen_contributions.py` | 修改 +12/−7 | 贡献迁移后的断言及真实 menu/dialog/watcher 清理，防止跨测试对象残留。 |
| `tests/test_screen_delivery_build.py` | 新增 +314/−0 | 独立构建输入、无屏幕/无聊天 PYZ、验证入口隔离、退出排空与源码对齐。 |
| `tests/test_screen_host_factory.py` | 新增 +239/−0 | 固定 host factory、受限上下文、无功能导入阻断器及 QObject 清理。 |
| `tests/test_screen_manual_host.py` | 新增 +163/−0 | 手动忙碌/冷却、授权、结果及关闭窗口的窄端口回归。 |
| `tests/test_screen_runtime.py` | 修改 +7/−3 | 运行回归使用 dispose 而非仅暂停，关闭 Qt bridge，避免新 fixture 污染后续进程族。 |
| `tests/test_screen_settings.py` | 修改 +2/−1 | 设置系统边界替身改到权威 feature 模块，保留页面行为。 |
| `tests/test_screen_worker_boundary.py` | 新增 +344/−0 | 真实 Worker 进程+本地 HTTP+确定性图像验证，不读取屏幕或实际模型。 |
| `tests/test_screen_worker_build.py` | 新增 +112/−0 | Worker 收集清单、独立依赖和验证产物安全边界。 |
| `tests/test_screen_worker_verifier.py` | 新增 +69/−0 | 冻结探针参数、证据分类、DLL 归属及失败退出判断。 |
| `tests/test_single_process_shared.py` | 修改 +5/−1 | 共享功能经受限端口继续单服务，更新测试 seam 而非伪造共享结果。 |
| `tests/test_single_process_spawn.py` | 修改 +2/−1 | 单进程创建的可选功能路径改为宿主接口，保持窗口生命周期断言。 |
| `tests/test_todo_reminder.py` | 修改 +3/−1 | 提醒回归中的识屏可选服务隔离点随 host 迁移，避免无关后台任务。 |
| `tests/test_vision.py` | 修改 +16/−9 | 视觉执行迁移后的系统/HTTP 边界替身，保留原网络/模型/截图逻辑回归。 |

### 文档

| 文件 | 本步状态及增删 | 改了什么、为什么 |
|---|---|---|
| `LOG-INDEX.md` | 修改 +1/−0 | 登记本步实现与验证记录，保留此前各切片索引。 |
| `LOG.md` | 修改 +12/−0 | 固化 host/build 施工、真实产物与 Qt 生命周期修复记录，不改写前三步历史。 |
| `docs/INDEX.md` | 修改 +4/−3 | 登记本报告并同步 Phase 4A 实现状态，不将安装器标为完成。 |
| `docs/plugin-phase-04-updates/PHASE4A-SCREEN-PACKAGE-DESIGN.md` | 修改 +34/−28 | 用实际端口、加载描述、文字服务与构建结果收口设计，区分进程内租约和后续安装状态。 |
| `docs/plugin-phase-04-updates/README.md` | 修改 +6/−4 | 阶段导航更新为第四切片已验证，保留用户手测、安装卸载与发布限制。 |

| 新增报告 | 说明 |
|---|---|
| `docs/PR-REPORT-SCREEN-HOST-BUILD-2026-09-29.md` | 本报告汇总逐文件增量、实测与限制；不重写历史报告。 |

## 三、实现与安全边界

### 宿主、状态和可选联动

`FeatureHostContext` 包含限定的 configuration、credentials、preferences、documents/state_documents、desktop、window、worker_launch_factory 和 allow_in_process。窗口侧仅快照和受限动作，旧扁平策略字段由明确映射访问。配置版本检查、锁内重读/合并、实例凭据作用域、确认迁移、额度/记忆/日志路径保持原语义；本步不再次迁移用户数据。

文字服务由 Core 的 `ServiceRegistry.register/bind` 和绑定客户端 `call` 做 owner/route/能力及注册身份检查，聊天接收方负责会话选择、持久化和幂等。实际会话在交付时由聊天侧确定，不伪称实现了尚不存在的 `resolve_route`/会话预绑定 API。聊天繁忙不插入、不排队、不补发；结果失败不阻断气泡；关闭聊天窗口不等于停用聊天服务。

host 是可信官方进程内代码，端口约束和代码审计**不是 Python 强沙箱**。不承诺任意 Python 代码热卸载，已加载版本的进程使用句柄一直保留到进程退出。多实例跨进程安装/升级锁和 `state.json` 权威留给 Phase 4B，当前只有显式描述和进程内使用生命周期。

### 校验与加载

- Ed25519 覆盖 manifest 原始字节；闭合清单包含 host/common、Worker 及资源摘要。
- 拒绝重复 JSON 字段、非有限数、路径逃逸、符号链接/reparse/hardlink、大小写冲突、额外文件、未知签名和不兼容 API/Core/platform。
- 固定官方 ID、factory、能力；校验失败不 import、自检或启动包代码。host 快照加载到版本命名空间，不改全局 `sys.path`；Worker 启动前复核、保持版本占用。
- 测试信任锚只进入带 `VALIDATION_ONLY` 标记的验证构建；私钥内存生成后丢弃，包自带公钥不构成信任。没有生成正式发布密钥。
- 文件校验/快照不等同于抵御同账户恶意进程任意改写的操作系统沙箱；本步不扩大到第三方可执行插件或安全隔离承诺。

### Worker、构建和 DLL

独立 Worker 保持 `proactive-screen`、`pet-worker/v1`，正常构建不含 Qt GUI、聊天、keyring 或 host。程序路径与参数数组直接交给 QProcess，不使用 shell。子环境清除 Core 的 Python/PyInstaller/Qt 搜索污染，代理/TLS 语义不改；不临时修改 GUI 的全局 DLL 搜索状态。Windows 实测枚举 native 模块，系统 DLL 来自 System32，非系统模块来自 Worker 自有目录，不依赖 Core `_internal`。

普通 Worker 与 synthetic 测试 Worker 是两个明确分开的产物；后者只使用程序生成的图像与本地 HTTP。生产包没有无鉴权截图替身操作。无聊天 Core 仍收集宿主 keyring 服务，不能沿用“只有聊天才需要凭据”的假设。

## 四、性能分析

### 环境、方法和样本量

本机 Windows NT 10.0.26100 x64，Python 3.11.1、PySide6 6.11.1、PyInstaller 6.20.0、cryptography 49.0.0、keyring 25.7.0。真实冻结验证使用 `QT_QPA_PLATFORM=offscreen`；每个构建/运行场景一个有界样本，不是长期 soak，也不是冷缓存性能对比。

核心命令（产物输出目录要求新建，重跑请换新的 `--output`，不覆盖证据）：

```powershell
# 各 --output 必须使用不存在的新目录；下面是本轮实际目录名。
python scripts/build_screen_worker.py `
  --output .scratch/phase4a-host-build/worker-build-20260929-01
python scripts/build_screen_worker.py --synthetic `
  --output .scratch/phase4a-host-build/synthetic-worker-build-20260929-01
python scripts/build_screen_delivery.py `
  --output .scratch/phase4a-host-build/delivery-build-20260929-03 `
  --worker-build .scratch/phase4a-host-build/worker-build-20260929-01 `
  --synthetic-worker-build .scratch/phase4a-host-build/synthetic-worker-build-20260929-01
```

前两份 Core 在最后一次 Supervisor 修复后重新构建；复用的 Worker 输入未变化并核对输入摘要，不把旧 Core 当当前产物。构建参数、源码/资源摘要、依赖版本、完整文件清单与 PYZ 模块表记录于各产物 `evidence/build-input.json`、`artifact.json`。单 Worker 实际本轮输出保存在 `worker-build-20260929-01/evidence/smoke.json`。复验使用新的运行目录和报告文件，避免覆盖原始证据：

```powershell
python scripts/verify_screen_worker.py `
  .scratch/phase4a-host-build/worker-build-20260929-01/dist/proactive-screen-worker/proactive-screen-worker.exe `
  --runtime-directory .scratch/phase4a-host-build/worker-runtime-recheck `
  --report .scratch/phase4a-host-build/worker-smoke-recheck.json
```

### 包体和依赖

| 产物 | 构建秒数 | 字节数 | 文件数 | PYZ 模块数 |
|---|---:|---:|---:|---:|
| Core webm + chat，排除屏幕理解 | 147.044 | 444,135,048 | 2,050 | 2,161 |
| Core webm，无 chat、无屏幕理解 | 154.618 | 441,712,605 | 2,039 | 2,145 |
| 普通独立 Worker | 47.616 | 66,784,778 | 94 | 475 |

完整普通功能包为 **66,949,863 字节**；host 135,123，common 11,032，Worker 66,784,778，余项为清单、签名和包标记。按 SHA-256 比较，Worker 与任一 Core 存在 **92 个相同文件、60,112,941 字节**重复，主要是 OpenBLAS、Python DLL、OpenSSL 和 NumPy。接受首版自包含重复，不据此声称瘦身；依赖共享优化需另做运行/DLL 证据。

### 启动、配置和常驻样本

下表“验证+host 加载”和“设置创建”均为单次真实冻结进程内部测量。RSS 为采样点快照，不是稳态差分或峰值。

| 场景 | 验证+host ms | 设置 ms | Worker 启动 ms | Worker RSS 字节 | Core RSS 字节 |
|---|---:|---:|---:|---:|---:|
| 无聊天、缺包 | — | 325.454 | — | — | 114,085,888 |
| 无聊天、有包 | 684.634 | 735.761 | 1,764.100 | 21,696,512 | 145,592,320 |
| 无聊天、shared、有包 | 469.861 | 837.186 | 309.506 | 21,594,112 | 142,553,088 |
| 有聊天、缺包 | — | 265.626 | — | — | 116,043,776 |
| 有聊天、有包 | 423.606 | 731.850 | 421.356 | 21,876,736 | 145,965,056 |
| 有聊天、shared、有包 | 359.146 | 878.611 | 314.722 | 21,696,512 | 143,065,088 |

单 Worker 独立 smoke：启动 **1,920.37 ms**，RSS **22,020,096 字节**，采样 **5.117 秒**内 CPU 增量 **0.0 秒**（受计时精度影响，不代表绝对零开销），线程 **5**，关闭 **20.76 ms**。真实 Core 场景中也为单 Worker/5 线程，关闭均确认进程退出。

确定性图像 + 本地 HTTP 的两份真实冻结 Core 各执行一次自动链和一次手动链，分别产生两次 HTTP 请求、一次自动预算确认；不是实际模型延迟：

| 操作 | 无聊天 ms | 有聊天 ms |
|---|---:|---:|
| observe_foreground | 1.894 | 2.371 |
| capture_foreground + dHash | 12.615 | 10.108 |
| analyze_frame（本地服务） | 26.614 | 15.334 |
| manual_look（本地服务） | 30.718 | 29.145 |

整个冻结探针持续约 2.36–10.51 秒，包含设置创建、主动等候心跳和退出排空，**不能当 Core 冷启动时间**。

### 成本结论

1. **稳态**：有包被使用时一个外置 Worker，保留既有有界线程/心跳与请求上限；无包没有识屏 Worker。没有长期 RSS/泄漏趋势数据，不宣称稳态更快或更省。
2. **新增路径**：首次校验/加载和每次受控启动需要读取清单/文件摘要、校验签名及版本占用；表中列出实际绝对成本。设置创建不会截图或向视觉模型发请求。
3. **系统/网络/磁盘/线程**：新进程与管道为有意隔离；保留既有截图和视觉请求语义。本轮正常 Worker smoke 的 `runtime_writes=[]`，截图未落盘；Core 仍会在隔离数据目录保存普通配置。没有新增后台下载、全局 DLL 修改或默认构建自动替换。
4. **内存/包体**：自包含解释器和依赖增加磁盘与进程开销，不能从这些非配对快照推断“增加恰好多少 RSS”。host 加载后保留到进程退出，不保证 Python 模块热卸载。

## 五、实机运行记录

### Windows 独立产物，而非源码救场

最终产物位于 `.scratch/phase4a-host-build/delivery-build-20260929-03/`。验证进程工作目录为空目录，移除 `PYTHONPATH/PYTHONHOME`，使用隔离数据目录与 `CREATE_NO_WINDOW`。不是 CI、不是只 mock 构建；使用真实 exe、AppShell、Qt 事件循环及 QProcess。图像和 HTTP 是明确的确定性测试边界，**不冒充真实桌面截图或收费模型验收**。

```text
CORE_VALIDATION_BUILD_OK ...core-webm-no-chat-no-screen.exe
CORE_VALIDATION_BUILD_OK ...core-webm-chat-no-screen.exe
Qt DLL chain（两份 Core）：ALL OK；ICU 来自 C:\Windows\System32
FROZEN_CORE_MATRIX_OK 10/10
```

10 项为 chat/nochat 各五种：缺包、有包、shared 有包、测试图像+本地 HTTP、shared 缺包。每项退出码 0、`errors=[]`、`worker_stopped=true`。缺包未注册专属菜单/设置、不导入识屏；有包配置页面可用；synthetic 两组各完成两次本地请求；shared 保持单 Worker 与正确窗口路由。详细命令保存在 `evidence/matrix.json`，每项同名 JSON/日志与 `qt-chat.log`、`qt-nochat.log` 留存。

可复现的单项命令（`$build` 为本次输出根；正常有包探针不截图、不请求模型）：

```powershell
$build = (Resolve-Path .scratch/phase4a-host-build/delivery-build-20260929-03).Path
& "$build/core-no-chat/dist/core-webm-no-chat-no-screen/core-webm-no-chat-no-screen.exe" `
  --data-dir "$build/recheck-nochat" --evidence "$build/evidence/recheck-nochat.json" `
  --feature-dir "$build/official.screen-understanding/1.0.0"
# 缺包：省略 --feature-dir；共享：增加 --shared。
# synthetic 只能选择 synthetic-test-only 内单独签名的测试包，并增加 --synthetic-request。
```

这些参数只属于 `packaging/phase4a_validation_entry.py` 的验证产物，不是新增正式产品 CLI。文件审计检查 TOC/PYZ/完整清单，而非仅凭目录名字断言排除模块；native 模块证据确认 Worker 没有借用 Core `_internal`。

产物身份：

| 文件 | SHA-256 |
|---|---|
| Core chat exe | `834b504042c0260099905c4ffdb37184fdd4049a648cbcb56be6f61e0c821931` |
| Core no-chat exe | `7f6369d695df23eebe6a05615b948a2cc4014047e50500bd37057e7d89c1abd9` |
| 普通功能包 manifest | `81f8ca38bfaa0680ec5a0246eb940d9d6dbc00b753a1de18ef665ec7c14ab547` |

### 失败记录、根因和修正

保留失败日志，不以最终绿覆盖过程：

1. **迁移引入的兼容/fixture 问题**：第一轮全量 `10 failed, 3440 passed, 12 skipped, 14 warnings`。六个旧 `window=None` 无头接口及四个 QObject fixture 问题完成窄修，相关 `138 passed`。不是给产品新增容错分支来掩盖测试。
2. **无聊天退出路径**：真实 no-chat Core 暴露 AppShell 退出时无条件导入 `chat.session_store`。独立进程真实 Qt 退出回归先红后绿，改为可选 writer 收尾；不扩大聊天迁移。
3. **探针本身问题**：Supervisor 已消费 heartbeat，不会透传给通用 message_received。验证 harness 改为观察真实接收阶段，且通过现有 finish_app_shutdown 等待异步退出；没有为探针改协议。
4. **Qt 原生生命周期缺陷（不能归类成环境偶然）**：虽然一次全量 `3456 passed`，随后 Qt/进程组合出现原生 exit 3。GDB 显示 `QObjectPrivate::deleteChildren → QObject::~QObject → QObject::event/sendPostedEvents`；有仪表时通过、强制全局 GC/排空仍失败，均不能作为修好证据。
5. **根因修复**：QProcess 信号闭包强持有 Supervisor 及绑定回调，子对象销毁时可能释放父对象最后一个 Python 引用。Supervisor 改为 weakref/WeakMethod，并检查当前 process 身份；fixture 自己负责 dispose，不在全局无条件清空其他测试 Qt 对象。
6. **确定性红绿**：`test_stopped_process_callbacks_do_not_retain_supervisor` 用真实子进程退出及未消费 DeferredDelete 固定这个所有权边界。修复前 `1 failed, 5 deselected`，修复后本文件 `6 passed`；随后三次组合均 65 passed、全量 3457 passed。GDB 返回 0 只是调试器退出，未被写成测试通过。

证据位于 `.scratch/phase4a-host-build/`：`full-pytest-20260929.log`、`qt-gdb-20260929.log`、`process-owner-red-20260929.log`、`process-owner-green-20260929.log`、`qt-owner-cleanup-repeat-{1,2,3}-20260929.log`、`full-pytest-owner-fix-20260929.log`、`build-delivery-owner-fix-20260929.log`。构建 01/02 为历史中间产物，最终接受 03，不混用过期 Core。

### 用户可见行为与人工限制

用户明确**尚未手测当前独立视觉配置版本**；旧版手动“看看屏幕”正常不代替这版验收。自动识屏未继续等待属于未验收而非故障；托盘自然退出、实际 keyring、真实模型及可见桌面交互仍待人工确认。

本轮执行日志/JSON 均标明 offscreen；退出使用真实 Core Qt 生命周期自动触发，不模拟“用户点过托盘”。没有取得读取屏幕、用户 Key、收费模型的授权，因此只使用独立 synthetic 测试包/本地服务。无需再次要求用户为了报告反复等待或付费。

## 六、测试与验证

修复后的实际自动化记录：

| 门 | 命令/范围 | 结果 |
|---|---|---|
| 所有权红绿 | `python -m pytest -q tests/test_external_worker_launch.py` | 修复前窄回归红；修复后 6 passed |
| Qt/进程组合 | 包绑定、runtime/settings 端口、外部启动、贡献和 Agent/识屏 Worker 生命周期九文件；短程 3 遍 | 65 passed，19.95 / 22.41 / 20.68 秒；不是长期 soak 或满载 CPU 性能对比 |
| 全量（新增本报告前） | `python -m pytest -q`，offscreen | **3457 passed, 12 skipped, 13 warnings，367.80 秒** |
| 最终全量（包含新增报告） | `python -m pytest -q`，offscreen | **3459 passed, 12 skipped, 14 warnings，348.90 秒**；退出码 0 |
| lint | `python -m ruff check pet tests scripts features packaging/phase4a_validation_entry.py packaging/phase4a_synthetic_worker.py` | 通过 |
| format | 对上行相同范围执行 `ruff format --check` | 460 files already formatted |
| mypy | plugins、workers、功能 ports/config/data/credentials/desktop、feature、chat external_turns | 59 source files，0 issue |
| 真实构建 | 两份 Core、普通 Worker、独立 synthetic Worker；文件/PYZ/native 清单审计 | 通过，最后 Core 构建 03 |
| 冻结组合 | 无源码搜索救场；chat/nochat 五组合 | 10/10，退出码 0 |
| 文档链接 | `python scripts/check_docs.py` | 110 files scanned，通过 |
| 报告纪律 | `python -m pytest -q tests/test_pr_report_discipline.py` | 43 passed，0.60 秒 |
| 差异/保护 | `git diff --check`、保护文件基线 SHA-256、暂存区核对 | 通过；自动更新/默认构建保持；演示 HTML 无 HEAD 差异；暂存区为空 |

最终全量比新增本报告前多出 2 个参数化报告纪律用例，并非新增运行时功能。12 项 skip 未变，包含当前 Windows 无创建符号链接权限的测试；真实 junction 安全用例通过，没有用放宽断言替代。最终 14 warnings 均为既有 Qt `QImage.mirrored`/`QHoverEvent` 弃用：clip 2、window 9、hover 3；上一轮 13 中 window 为 8，受运行路径计数影响，本步没有新增 warning 过滤或删除测试。两轮均为本机有界回归，耗时不作为性能改善结论。

组合短程复跑的完整命令（每遍单独进程，三遍；不使用仪表化 runner 或全局 GC 补丁）：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q tests/test_feature_package_binding.py tests/test_feature_runtime_ports.py `
  tests/test_feature_settings_ports.py tests/test_external_worker_launch.py `
  tests/test_screen_runtime.py tests/test_screen_contributions.py tests/test_workers_lifecycle.py `
  tests/test_agent_link_worker_integration.py tests/test_proactive_worker_lifecycle.py
```

最终全量输出存于 `full-pytest-closeout-20260929.log`（退出码 0）。收尾静态/文档输出存于 `closeout-static-20260929.log`，保护快照存于 `closeout-protection-20260929.json`，均位于本步 `.scratch` 证据目录。`git diff --check` 的现有 SPEC LF/CRLF 提示不是空白错误，本步不因此重写换行。

完整类型命令：

```powershell
python -m mypy pet/plugins pet/workers pet/feature_ports.py pet/feature_host_bindings.py `
  pet/feature_bindings.py pet/feature_config.py pet/feature_data.py pet/credentials.py `
  pet/desktop_query.py features/screen_understanding pet/chat/external_turns.py
```

## 七、限制、下一步与回滚

- 第四步交付的是 **Windows 独立验证产物**。其他功能尚在 Core，不能将它叫“所有功能已经拆完的最小 Core”。macOS/Linux、其他默认变体与正式发行仍需各自验收。
- 签名只有隔离验证信任锚，无正式公钥轮换、第三方代码加载或发布渠道。
- 未提供唯一安装状态、目录/ZIP 事务、应用内安装/卸载、跨进程版本租约、安装向导或远程下载；Phase 4B 接续，不在本轮补造状态文件。
- 首版接受 60,112,941 字节重复依赖；没有长期 CPU/RSS 泄漏证明。短程证据不替代持续稳定周期。
- 未修改 Core 自动更新、演示 HTML 或默认构建脚本；工作树中继承的前三个切片和文档改动继续保留。
- 未 stage/commit/push。回滚应按本步快照和差异单独审查，不用 `reset` 或批量恢复 HEAD 覆盖前三步。后续授权封存时拆成可独立回滚的提交，再用对应 `git revert`；生成产物在被忽略的 `.scratch`，不入库。

## 八、面向用户的最终效果

目前有真实证据证明：验证版 Core **不带识屏也能启动**；显式加载验证功能包后菜单/设置出现；不带聊天也能使用独立识屏请求；带聊天时可以受控同步文字。host 在 Core GUI 进程中，Worker 是功能包自带的独立程序，仍用本机管道相连。

这不是已经发布给日常使用的新版安装包。当前不能在桌宠里点“安装/卸载”；默认分发没有替换。下一步是 Phase 4B 的本地安装与管理闭环，而不是继续以 Worker 数量证明进度。真实手测与正式发布仍保留明确验收门。
