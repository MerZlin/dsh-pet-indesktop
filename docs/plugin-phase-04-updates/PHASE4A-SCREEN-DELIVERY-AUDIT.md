# Phase 4A：屏幕理解交付边界只读审计

> 日期：2026-09-27；源码基线 `b97112d`。**本次仅完成拆包审计，不代表 Phase 4A 设计冻结、最小 Core 构建或可卸载样板完成。**
> 承接 [Phase 4 计划](README.md)、[Phase 3C 风险评估](../plugin-phase-03-worker/PHASE3C-ISOLATION-ASSESSMENT.md)；功能主要归属以 [功能交付总表](../plugin-roadmap/PLUGIN-FEATURE-DELIVERY-MATRIX.md) 为准。

## 1. 证据边界与结论分级

- **已证实事实**：实际阅读下列源码和测试，并解析已有 PyInstaller `Analysis-00.toc` 与构建输入清单。函数名是定位锚点，行号仅针对本基线。
- **建议边界**：后续拆包应保留或拆开的责任，不表示已经搬迁、已冻结最终接口或可以执行第三方代码。
- **待验证事项**：必须通过后续设计、缺包测试和独立产物验证，不能用 import 懒加载、功能开关或现有完整包替代。

本轮没有修改 `pet/`、构建配置、安装器、Worker 协议或自动更新，没有删除模块、实现加载器或冻结包格式/ID/安装目录；也没有重建安装包、截图或请求真实模型。

人工事实：用户只确认手动“看看屏幕”正常；自动识屏因为没有继续等待而**未验收，不判定故障**；停用和托盘退出未测试。测试边界修复及本轮自动化证据见 [3B 收尾 §10](../plugin-phase-03-worker/PHASE3B-STABILITY-CLOSEOUT.md#10-前台窗口测试边界修复与验收澄清)。

## 2. 功能代码与导入边界

| 审计项 | 已证实事实与源码定位 | 建议边界 | 待验证事项 |
|---|---|---|---|
| 主程序装配 | [app.py](../../pet/app.py) `PetApp` 装配中约 467–473 行，`on_look_screen` / `on_look_synced` 与 `enable_chat` 绑定；[window_optional_services.py](../../pet/window_optional_services.py) `WindowFeatureGateMixin._ensure_proactive_watcher()` 懒装配直接创建 `ProactiveScreenWatcher` | Core 只提供宿主、授权及展示原语；识屏装配由已安装功能贡献，不再由聊天开关决定 | 没有识屏代码时能导入/启动主窗口，且不会构造 watcher、adapter、定时器或 fallback |
| 自动策略 | [proactive.py](../../pet/proactive.py) `ProactiveScreenWatcher` 保留开关、8 秒调度、白名单、dwell/idle、Agent 忙碌、limiter、generation 和结果展示决定；含旧执行路径 | 策略仍在 GUI 主进程执行，但专属代码随屏幕理解包交付；状态权威留在宿主侧，而非 Worker | 包停用/卸载授权能阻断所有执行入口，不能只停止一个 QProcess |
| 手动入口 | [window.py](../../pet/window.py) `look_at_screen()`、`_on_look_screen()`、`_on_look_done()`、`_look_worker()`；约 3522–3598 行读取聊天 Provider/Key，优先 watcher，失败保留旧线程路径 | PetWindow 保留可替换的命令/展示接缝；专属策略、凭据选择和旧执行实现随功能交付 | 无包时不留菜单/快捷键/直接调用后门；有包时忙碌、冷却、同步语义不变 |
| 宿主 adapter | [proactive_screen_adapter.py](../../pet/workers/proactive_screen_adapter.py) 管 request ID、generation、pending、预算反向请求和凭据筛选 | 与识屏包共同交付；通用 QProcess/协议/诊断可留 Core | 未安装不得通过 adapter 或 fallback 复活；超时和关闭继续撤销 pending |
| Worker 执行 | [proactive_screen_worker.py](../../pet/workers/proactive_screen_worker.py) 管前台、截图、单帧 TTL、视觉请求；`_snapshot()` 约 609 行从 `pet.proactive` 导入 `image_dhash` | 截图/哈希/视觉执行属于识屏；将纯哈希从策略模块分出，避免 Worker 反向依赖整份宿主策略 | 包独立运行的 import 图不包含 PetWindow、Chat UI 或策略装配；不能只检查未创建 QApplication |
| Worker 启动 | [worker_entry.py](../../pet/workers/worker_entry.py) 静态官方 allowlist 包含 `proactive-screen`，由同一 EXE 早期分流 | 保留受控启动及信任检查；官方独立产物和注册方式另行设计 | 同一 EXE 的模块收集如何去除；外置产物、更新和版本协商必须实测 |
| 多窗口 | [multi_window_shared.py](../../pet/multi_window_shared.py) `SharedProactiveWatcher` / `SharedSubsystems` 与 [proactive_worker_integration 测试](../../tests/test_proactive_worker_integration.py) 已覆盖进程内共享 Worker、来源路由与窗口关闭 | 功能服务按宿主租约管理；窗口关闭只取消本窗口请求，最后一个使用者释放才停服务 | 现有 shared 是同进程复用，不是跨进程安装/卸载锁；多实例占用、待重启卸载仍需设计 |

### 不能把整份 vision.py 当作识屏专属代码

**已证实事实：**[vision.py](../../pet/vision.py) 同时含 OS 查询和截图/视觉请求。除识屏外：

- [platform_win.py](../../pet/platform_win.py) `_fg_fullscreen_probe()` 约 193–241 行用 `foreground_window_info()` 做 Core 全屏窗口判定。
- [window_screen.py](../../pet/window_screen.py) `fs_watch_loop()` 使用光标可见性和全屏判断；[multi_window_shared.py](../../pet/multi_window_shared.py) 也复用光标信息。
- [test_cursor_visibility.py](../../tests/test_cursor_visibility.py) 验证光标/循环异常行为；本轮 [test_vision_foreground.py](../../tests/test_vision_foreground.py) 直接执行真实前台函数体，确定性验证 OS 边界。

**建议边界：**Core 平台适配保留基础行为必需的“只读窗口/光标/空闲状态查询”；截图、dHash、JPEG、视觉网络请求与专属策略归识屏包。可以拆出平台查询模块，不能把整个视觉模块留 Core，也不能整份移走破坏全屏避让。

**待验证事项：**移除识屏模块后的全屏避让、光标策略和 shared 生命周期回归；新增依赖检查确认 Core 平台模块不反向导入截图、Provider 或识屏包。具体模块名与接口本轮不冻结。

## 3. 聊天依赖：已有独立视觉 Key，不等于独立交付

| 已证实事实 | 建议边界 | 待验证事项 / 已有测试 |
|---|---|---|
| `window._on_look_screen()`、`proactive._resolve_vision_provider()` 使用 `cfg.chat_settings`、`active_config()`、聊天提示/角色上下文和 `resolve_api_key()` | 识屏具有自己的可持久化视觉服务配置和凭据授权；与聊天同步是可选联动 | 不安装聊天仍能配置、重启后恢复并调用识屏；安装聊天不能隐式授予截图权限 |
| [vision.py](../../pet/vision.py) `post_vision_request()` 复用 [chat/models.py](../../pet/chat/models.py) `ProviderConfig`、[chat/providers.py](../../pet/chat/providers.py) 的 TLS/请求头/endpoint 辅助代码 | 抽取小而明确的数据/HTTP边界或由功能包组合依赖；不要为共享模型把全部 ChatService/Chat UI 放回 Core | 依赖方向、序列化、超时、代理及重试等保持原语义；本轮不改网络实现 |
| [config.py](../../pet/config.py) `resolve_api_key()` 及 adapter `_provider_payload()` 依赖聊天 `SecretStore`；Worker 拒绝自行解析 keyring 引用 | Core 提供受限凭据端口，功能包拥有凭据用途；只发送授权单次请求临时 Key，不把 Key 放入 `config_push` | 密钥迁移/撤销、共享引用所有者、异常日志脱敏、取消后的引用释放，不以 SHA-256 代替执行来源信任 |
| [chat/ai_settings_page.py](../../pet/chat/ai_settings_page.py) 保存视觉 URL/模型/独立 Key 到聊天 Provider 配置 | 专属视觉设置由识屏包贡献，聊天可引用但不是安装依赖 | 已有 [test_vision.py](../../tests/test_vision.py) 的 `test_independent_vision_empty_key_never_uses_chat_key` / `test_independent_vision_prefers_own_key_over_chat_key` 要保留；新增无聊天模块的配置/迁移测试 |

迁移不能静默把现有独立视觉 Key 替换成聊天 Key；也不能要求用户重新建聊天账户才能保留识屏。现有行为是重建验收基线，不等于最终配置目录已确定。

## 4. 菜单、设置、命令与搜索贡献

**已证实事实：**

- [context_menus/registry.py](../../pet/context_menus/registry.py) 约 301–377 行：手动项依赖 `on_look_screen`；主动项在 Windows 上借 `on_open_chat` 判断可用性。当前是静态动作表和回调条件，而不是按已安装包 owner 注册。
- [context_menus/shared.py](../../pet/context_menus/shared.py) 的主动菜单构建直接导入专属配置逻辑。
- [modern_settings_dialog.py](../../pet/modern_settings_dialog.py) 约 877–881、933、1032、1844–1863、2320 行：`include_ai` 同时约束聊天/识屏动作、主动页/卡片、搜索和保存路径。只隐藏右键菜单不能移除全部设置入口。
- Phase 2 [plugins/ports.py](../../pet/plugins/ports.py) `CommandRegistry` 已有 owner 注册与 `unregister_owner()`；[plugins/runtime.py](../../pet/plugins/runtime.py) `PluginContext.dispose()` 会清理命令、订阅和定时器。但只接受显式官方 factory，**没有完整的可执行包加载器和设置页/搜索/快捷键贡献事务**。
- [test_menu_layout.py](../../tests/test_menu_layout.py)、[test_proactive.py](../../tests/test_proactive.py) 的菜单用例覆盖已有静态/配置行为，不证明缺包和卸载后的贡献消失。

**建议边界：**沿用 owner/command 接缝，补齐菜单、托盘、设置页、搜索项与快捷键的成组注册/撤销。贡献可在 GUI 进程中执行，但专属 UI 文件属于包。用户排序偏好属于宿主保存的布局引用；暂时卸载不清掉用户排序。

**待验证事项：**未安装不显示功能入口；安装启用后注册；停用保留管理/偏好但不执行；不兼容有诊断而非无响应动作；卸载撤销所有 owner 贡献。仅打开设置不得启动 Worker、截图或联网。必须同时检查托盘、右键、搜索和快捷键，不能仅用 `include_ai=False` 证明可拔除。

## 5. 配置、数据、权限与卸载责任

| 数据/生命周期 | 已证实事实 | 建议边界与待验证事项 |
|---|---|---|
| 配置 | [config.py](../../pet/config.py) 保留扁平 `proactive_screen` 默认值、规范化及访问；视觉配置属于聊天 Provider | 迁移到明确功能 owner；备份、幂等、失败恢复、旧字段兼容。已存在插件命名空间不等于识屏已经迁入 |
| 频控 | [proactive.py](../../pet/proactive.py) 将 `cfg.dir/proactive_screen_state.json` 交给 [ProactiveLimiter](../../pet/proactive_limiter.py)；dry-run 使用单独状态文件；Core 侧每次网络尝试审批预算 | 保留共享额度与 dry-run 隔离语义；安装/卸载不能重置或重复消费额度。多进程原子性、配置目录与安装租约需联合验证，不把状态文件当安装锁 |
| 记忆 | [ProactiveMemory.record()](../../pet/proactive_memory.py) 在 `cfg.dir/proactive_screen_memory.json` 记录时间、进程名、活动；`title` 参数为兼容保留，不写入新记录；不存截图 | 默认卸载保留用户数据，另行确认清除；迁移旧文件需验证历史字段与隐私，不直接将所有旧数据发送 Worker |
| 帧与 pending | Worker 有单帧内存 TTL；adapter 有 pending/request generation；既有 source/adapter 测试验证清理与过期丢弃 | 停止任务、撤销响应/预算许可、停 Worker、释放贡献后再移除包。文件占用应明示待重启，不声称 Python 模块热卸载 |
| 自动与手动 | 自动关闭并不禁止手动；`request_manual_look()` 可按需启动 adapter，且不计自动每日额度 | 保留这一区别；“卸载整个识屏包”则两条路径都不可执行；功能关闭与包不存在不能复用同一布尔含义 |
| fallback | 当前有 `auto/in_process/disabled` 内部模式，失败可回旧线程；没有包安装状态授权 | 旧实现也属于识屏包，只能为已安装、启用、获授权的功能兜底；卸载不得偷偷截图/联网 |
| 多实例 | 当前 per-instance 配置、进程内 shared Worker 和用户数据目录是不同边界 | 明确包版本占用/激活权威、最后使用者退出、pending uninstall 和重新启动恢复；不能误杀其他实例或用户自建进程 |
| 密钥 | keyring 位于 Core 侧聊天存储边界，Worker 不访问 | 卸载默认不清共享凭据；单独确认清除并核对引用所有者，日志/配置备份不得包含明文 Key |

未来安装器不能直接复用 Phase 1 的资源执行模型：[content/manifest.py](../../pet/content/manifest.py) 明确 `kind=content`、`entrypoint=null`；[ContentManager](../../pet/content/manager.py) 的角色资源事务与路径校验不是任意代码加载器。官方功能包仍须在执行前验证可信来源、完整性和兼容性，沿用 [更新/安装合同](PLUGIN-UPDATE-PROTOCOL.md)，本次不实现这些能力。

## 6. 依赖与现有构建清单

### 6.1 使用方核对

| 依赖 | 已证实的使用方 | 暂定归属 / 不可提前下的结论 |
|---|---|---|
| Pillow | `vision` 截图，`proactive` / Worker 的图像缩放与 dHash；[make_icon.py](../../scripts/make_icon.py) 也用于构建图标 | 识屏运行时依赖候选，但构建依赖与传递依赖另算；不能只删总 requirements 或凭一个 import 判定包体收益 |
| keyring | `chat.models.SecretStore`，聊天/视觉配置及配置密钥解析 | 共用安全存储能力，不可整体判为识屏专属；后续审计凭据端口与包声明 |
| certifi | `chat.providers._make_ssl_context()`，视觉请求复用；[balance.py](../../pet/balance.py) 也使用 | 共享 TLS 信任资源，不得移除识屏时破坏聊天/余额，也不改既有代理策略 |
| PySide6 | Core UI、QProcess 宿主；识屏 GUI 策略/专属设置也在主进程 | Core 必需共享运行时，不随识屏卸载；功能专属 UI 源码仍需从 Core 分离 |
| urllib / ssl / ctypes | 标准库网络、OS 查询；多个功能共用 | 不是每个包重复配送整套 Python 的依据；Worker 产物布局需要单独实验 |

依据包括 [requirements.txt](../../requirements.txt) 与本地构建生成的 `dsh-pet-standalone-webm.spec` / `dsh-pet-standalone-webm-chat.spec`。这两份规格是审计时的生成物，被 Git 忽略，不是仓库文件；可追溯的生成入口见 [onedir 构建脚本](../../scripts/build_onedir.ps1)。本轮不安装新依赖、不提供未经测量的瘦身数字。

### 6.2 已有产物的时间与对应关系

本轮只读取已有产物，没有重建或重新执行 frozen smoke：

| 清单 | 时间 / 来源 | 观察到的模块 | 能证明 / 不能证明 |
|---|---|---|---|
| `build-onedir/dsh-pet-standalone-webm-chat/Analysis-00.toc` | 本地 mtime 2026-09-27 14:56:48 +08:00；当日 15:06:42 的构建记录；TOC SHA-256 `1b3d22e403677ca3db7aeceee751ce08f570c6911aa3b2074f92d2d8fad121ee` | vision、proactive、limiter/memory、识屏 adapter/worker、chat.models/providers、PIL.ImageGrab、keyring、certifi 均存在 | 完整构建收集了识屏和聊天；不是最小 Core 证据，也不单凭 TOC 宣称 frozen 运行正确 |
| `build-onedir/dsh-pet-standalone-webm/Analysis-00.toc` | 旧 mtime 2026-09-25 20:15:54 +08:00；TOC SHA-256 `3f4bc9aca5faf8e5c12f473f6a933be06624f8c00426c9a101bbecd745171fb6` | 有 vision、proactive、limiter/memory、PIL.ImageGrab、certifi；无新识屏 adapter/worker、chat.models/providers、keyring | 只作旧构建参考，没有当前输入对应证明，不能冒充当前缺包 smoke 或安装包 |

`webm-chat` 既有记录包含 468 项输入，其清单摘要为 `bf670d9fd615787a213278548cdf0d46e3c31e14e6d59905c54ae30221029aac`。本轮逐项重算 SHA-256：**468/468 与记录一致，0 项变化**。该记录起点为 `9834612` 加当时未提交的 3B 输入；不能仅凭那个旧 HEAD 认定构建缺少 3B。原构建/Qt/3A/3B smoke 结果沿用 [3B 收尾 §5](../plugin-phase-03-worker/PHASE3B-STABILITY-CLOSEOUT.md#5-构建来源与冻结验证)，不冒充本轮重跑。

[pet_entry_no_chat.py](../../packaging/pet_entry_no_chat.py) 仍走 `app.main(enable_chat=False)`，且保留 Worker 早期分流；规格排除聊天不等于剥离识屏。**无聊天变体、关闭功能、独立 Worker、真正无识屏产物，必须分开验收。**

## 7. Phase 4A 移交清单与新增验收门

以下是后续设计输入，不是本轮已完成的实现：

| 优先项 | 必须交付的设计/验证 | 可复用证据 | 缺口及通过标准 |
|---|---|---|---|
| A1 平台与专属执行拆开 | Core OS 查询端口、识屏图像/网络代码单向依赖图 | cursor visibility、前台确定性回归、vision 单元测试 | 缺少整个识屏代码/专属依赖时，Core 全屏/光标/拖拽等仍能运行；只做 monkeypatch 开关不够 |
| A2 视觉独立配置/安全存储 | 不安装聊天仍能配置视觉；配置/Key 迁移备份与恢复 | vision 独立 Key、adapter 配置筛选、proactive limiter/memory 测试 | 实际无 chat 模块环境导入/配置/重启；Key 不进入普通配置或 Worker 快照；迁移可重复执行 |
| A3 安装状态与 UI 所有权 | 单一安装/启用/授权权威，菜单/托盘/搜索/设置/快捷键 owner 贡献 | plugin runtime dispose、menu layout、现有 proactive 菜单测试 | 缺包无入口，装包可用，停用不执行，卸载全部撤销，重装恢复偏好；打开设置不启动任务 |
| A4 独立构建与信任 | 可信官方包受控加载，GUI/Worker 代码和共享依赖产物清单 | 当前 build 输入及 TOC，只作完整包基线 | 最小 Core 的 Analysis/PYZ/文件与运行 smoke 均无专属功能；分别测量体积与启动成本；格式/ID/路径/版本协商另行冻结 |
| A5 停止/占用/回滚 | 多实例租约、卸载状态机、升级自检与旧版本恢复 | Worker lifecycle/app_shutdown/integration、角色事务测试 | 卸载不残留 Worker、pending、命令或 fallback；占用时明确待重启；配置保留/清除分别验证 |
| A6 真实可拔除交付 | Phase 4B 本地安装卸载样板；5A Setup 选装/ZIP/便携 | [功能总表](../plugin-roadmap/PLUGIN-FEATURE-DELIVERY-MATRIX.md)统一合同 | 不安装也能启动 → 安装出现入口 → 停用无执行 → 卸载文件/入口消失 → 重装恢复保留配置 |

建议先设计 A1–A4 的最小接缝，再实施屏幕理解样板；不同时重写全部 AI 服务。聊天 UI 可继续在主进程，但随后随 AI 包交付。其余 Worker 评估不成为样板前置条件。自动识屏和托盘退出人工门仍保留，审计文档不能替代它们。

## 8. 给使用者的最终效果

现在桌宠的操作、菜单和安装目录都不变。识屏仍随主程序发布，用本机 QProcess + stdin/stdout JSONL 与 Core 连接，**本轮没有生成可安装或可卸载的识屏 DLC**。

这份审计明确了下一步不能遗漏的工作：保住不依赖识屏的全屏/光标能力；让识屏不再要求先安装聊天；把专属 UI、配置、旧执行路径与 Worker 一起归到功能包。最终目标仍是“不装识屏也能用桌宠，装了才出现菜单和设置，卸载后确实不再执行”。包名、文件格式、路径和安装按钮还需后续设计验证，本轮不编造已经可用的安装方法。
