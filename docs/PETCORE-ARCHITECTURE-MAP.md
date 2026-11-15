# dsh-pet 项目架构说明

> 基线：2026-11-06，分支 `feat/petcore-vpet-architecture`。`petcore` 版本 0.5.0，API 1，schema 1；`pet/` 发布线为 v4.3.4。

仓库同时维护两条运行时：

- `pet/`：旧版 `python -m pet`，当前发布线，约 220 个 `.py` 文件、13.0 万行。
- `petcore/`：新版 `python -m petcore`，core + mod 重构线，约 254 个 `.py` 文件、80,022 行。

两条运行时共享 `assets/`、`resources/`、`tests/`、`docs/`、`packaging/` 和 `integrations/`，入口、配置目录与打包方式各自独立。

## 1. 仓库结构

| 项目 | `pet/` 旧运行时 | `petcore/` 新运行时 |
|---|---|---|
| 入口 | `python -m pet` → `pet/__main__.py` → `pet.app.main` | `python -m petcore` → `petcore/__main__.py` → `petcore.cli.main` |
| 启动对象 | `PetApp` 管理应用服务，`PetWindow` 管理窗口与交互 | `CoreHost` 管理进程级服务，`PetManager` 管理多宠 |
| 多宠与屏幕 | `PET_RENDER_TOPOLOGY=overlay` 时使用 `OverlayShell`、`PetSprite`、`ScreenHost` | `PetManager`；每个 slot 一份 `config-pet-N.json` |
| 配置 | 全局 `config.json`、每宠 `config-slot-N.json`、成员 `overlay-active-pets.json` | 全局 `config.json`、每宠 `config-pet-N.json`、停用集合 `mods-state.json` |
| 扩展 | 功能固定在包内 | feature / content 包；`mods/` 与 `feature-data/` |
| 发布状态 | README 标注的正式发布线 v4.3.4（2026-10-19） | 0.5.0 候选 |

共享目录：

| 路径 | 内容 |
|---|---|
| `assets/` | 角色素材、动画、音效、聊天素材、图标 |
| `resources/` | 随包第三方资源：`presentmon/`、`rapidocr_models/` |
| `tests/` | 600+ 个 `.py` 文件；架构约束、设置覆盖、时序与真机回归 |
| `docs/` | 文档入口是 `docs/INDEX.md`；含分阶段文档、PR 报告与架构说明 |
| `packaging/` | `dsh-pet.iss` + `pet_entry.py`；`petcore.iss` + `petcore_entry.py` |
| `integrations/` | `dsh-pet-bridge`、`hermes-pet-bridge`；另有 `android/` 客户端 |

扩展与打包位置：

| 路径 | 内容 |
|---|---|
| `petcore/builtin/features/` | 7 个 Core 内置 feature：`fun.click-talk`、`fun.self-talk`、`tool.links`、`tool.memory-trim`、`tool.network-info`、`tool.quick-launch`、`tool.quiet-mode` |
| `petcore/builtin/feature_demo/` | 扩展接缝示例，归入「基础扩展」 |
| `examples/petcore-mods/` | 12 个可导入示例：AI 对话、Agent、占卜、节日、QQ、音乐、待办、浏览器、文件解读、OCR、天气、提示音 |
| `dsh-pet-core.spec` | petcore 主程序 PyInstaller 配置；`dsh-pet-core-worker.spec` 对应 worker 入口 |

## 2. petcore 分层

依赖方向从下往上：上层可以依赖下层，下层不依赖具体业务扩展。UI 层通过契约与端口接触扩展。

| 层 | 目录 | 职责 |
|---|---|---|
| 入口 | `petcore/cli.py`、`petcore/__main__.py` | 解析 `--root`、`--portable`、`--instance`、`--allow-feature-mods`、`mods` 子命令和 `--check`；创建 `QApplication` 与 `CoreHost` |
| UI 层 | `petcore/ui/` | 桌宠窗口、多宠管理、气泡、菜单、托盘、设置页、对话窗、截图、灵动岛、`build_ports()` |
| 契约与扩展管理 | `petcore/contracts/`、`petcore/mods/` | 端口 Protocol、`FeatureDefinition`、`Contribution`、能力声明、manifest 校验、发现、启停、安装、授信、`FeatureManager` |
| 领域与基础设施 | `engine`、`island`、`media`、`content`、`platform`、`storage`、`speech`、`chat`、`net`、`api`、`account`、`market`、`update`、`community`、`interop`、`migration` | 动画、行为、碰撞、几何、媒体解码、内容收集、Win32 与屏幕、会话与锁、语音、会话存储、HTTP 出口、API 规格与钥匙串、市场、更新、致谢、记忆、旧数据迁移 |
| 数据落点 | `<data_root>` = `%APPDATA%/dsh-pet-core` | 全局配置、每宠配置、停用集合、已安装包、扩展私有数据、日志、缓存 |

依赖约束：

- `engine/` 不导入 PySide6；动画、几何、物理、行为可在无窗口环境下测试。
- `contracts/` 只用标准库；端口用 Protocol 声明，实现放在 UI、平台或领域模块。
- `pet.*` 只允许在 `petcore/media/` 与 `petcore/content/legacy.py` 中导入。
- 网络库（`urllib`、`socket`、`requests` 等）只允许出现在 `petcore/net/`、`petcore/api/`、`petcore/account/`。
- `ui/` 单向依赖 `engine/contracts/media/content/mods/platform`；core 不反向依赖具体业务扩展。
- 密钥只进系统钥匙串；`config.json` 与 `api.json` 不写 API Key；第三方偏好落 `config.extra`。

约束由 `tests/test_petcore_architecture.py` 检查。

## 3. 启动路径

正常启动与 `--check` 走同一条内容加载链路；`--check` 不创建窗口，不写配置。

1. **解析参数**（`petcore/cli.py`）：`--root` 指定数据根，`--portable` 与 `--instance` 切换数据根，`--allow-feature-mods` 打开功能扩展总开关，`mods` 子命令走命令行分支。
2. **加载配置**（`Config.load(data_root)`）：读取 `config.json`、各 slot 的 `config-pet-N.json`、`mods-state.json`；`--check` 使用 `persist=False`，不落盘，也不创建迁移备份。
3. **单实例锁**（`QLockFile`）：同一数据根只允许一个进程；`--instance NAME` 使用独立数据根与独立锁。
4. **创建 CoreHost**（`petcore/ui/app.py:406`）：组装 `QtScheduler`、音频路由、记忆、Webhook、API 规格与钥匙串、账户与余额、语音与 ASR、DSH 客户端、社区、聊天控制器、快速对话、截图问答、菜单模型、免打扰、WorkerHost、市场任务。
5. **加载内容与扩展**（`CoreHost.load_content`、`mods/loader.py`）：只读加载 `assets/characters`，然后扫描 mod 目录；content 包进入角色列表，feature 包通过 manifest 与授信检查后由 `FeatureManager` 装配。
6. **创建宠物窗口**（`PetManager`、`PetWindow`）：动画目录、播放器、行为决策、`ClipLibrary`（frameseq → webm → gif 回退）；恢复位置、屏幕、缩放；首帧渲染后显示。
7. **注入端口并启动 runtime**（`petcore/ui/ports.py:981`）：`HostPorts` 声明 27 个字段，这里注入 22 个；7 个 Core 内置 feature 无条件加载，用户 feature 通过 `runtime.start()` 启动。
8. **托盘、菜单与监听器**（`TrayIcon`、`MenuModel`、`SessionWatcher`）：屏幕变化、锁屏/睡眠、全屏检测、灵动岛采样按需启停。
9. **进入事件循环**（`QApplication.exec()`）：所有 GUI 操作在主线程；扩展的跨线程回调经 `QtScheduler` 排队。

`--check` 输出角色、每段动画实际采用的后端、mod 列表与诊断、配置快照；构建门使用 `--check --strict` 并检查 `check.json`。

## 4. 扩展机制

feature 包提供代码与 UI 贡献；content 包提供角色与素材。加载步骤固定，单步失败只产生诊断，不阻断 core 启动。

### 4.1 两类包

| 类型 | 内容 | 安装位置 | 约束 |
|---|---|---|---|
| feature | `mod.json`、`entrypoint`、Python 代码；可提供菜单、设置页、对话窗页与命令 | `<data_root>/mods/<id>`，或 `{app}/mods/<id>` | 受 `allow_feature_mods` 与授信检查约束 |
| content | 角色与素材，id 形如 `character.<名字>` | 同上 | 禁止可执行文件；manifest 校验会拒绝 |

### 4.2 feature 加载顺序

1. **扫描**：数据目录 `mods` → 安装目录 `mods` → 仓库 `mods` → 额外目录；同 id 后者覆盖。
2. **校验**：`mod.json` schema、id、version、`api_version`、路径逃逸、content 包可执行文件检查。
3. **授信**：feature 必须在用户 `mods` 内，且 `allow_feature_mods` 开启，或安装目录带 `.trusted`。
4. **导入入口**：`entrypoint` 必须是包内相对路径；`host/__init__.py` 按包加载，支持相对 import。
5. **create_host**：收到 `FeatureContext`，包含 owner、core_version、api_version、ports、host、log。
6. **FeatureDefinition**：返回 contributions、runtime_factory、settings_factory、view_factories。
7. **runtime.start()**：订阅、定时器、加载数据；命令表 `runtime.commands` 在这里暴露。
8. **贡献落地**：菜单项、设置页、对话窗页进入 core 的 UI 槽位。

### 4.3 贡献类型

`petcore/contracts/contributions.py` 现有 11 个 kind。运行期有消费点的是 3 个；角色内容走 content 收集器，不走 contribution。

| kind | 用途 | 运行期消费点 | 状态 |
|---|---|---|---|
| `menu` | 右键菜单项 | `FeatureManager.menu_contributions()` → `MenuModel` | 生效 |
| `settings_page` | 设置页 | `FeatureManager.settings_pages()` → `ExtensionsPage` | 生效 |
| `chat_view` | 对话窗里的一页 | `FeatureManager.chat_views()` + `view_factories` | 生效 |
| `command`、`hotkey`、`card`、`install_hook`、`phrases`、`sound`、`theme`、`seasonal` | 清单允许的声明 | 当前没有消费方；命令表使用 `runtime.commands` | 无消费点 |

### 4.4 端口

`HostPorts` 声明 27 个字段，`build_ports()` 注入 22 个。未注入的 5 个在运行期为 `None`。

已注入的 22 个：

- 呈现与交互：`presentation`、`pets`、`interaction`、`quiet`、`reminders`、`audio`
- 数据与配置：`storage`、`credentials`、`resources`、`config`
- 通信与模型：`http`、`api`、`chat`、`quick_chat`
- 屏幕与系统：`screen`、`screen_ask`、`system`
- 运行时：`scheduler`、`worker`、`speech`、`memory`、`webhook`

未注入的 5 个：

- `commands`：命令表使用 `runtime.commands`。
- `menu`：菜单使用 contribution 注册。
- `windows`：没有窗口端口；扩展自建窗口。
- `input`：拖文件使用 `interaction.on_drop`。
- `capabilities`：能力只做声明，没有运行期检查。

### 4.5 线程规则

1. Qt 控件、窗口、气泡、菜单、托盘只在 GUI 线程操作。
2. `scheduler.call_later` 是唯一允许从任意线程调用的端口；回调在 GUI 线程执行。
3. `http.request`、`http.get_json`、`speech.synthesize` 会阻塞；调用方自己开线程，结果经 `scheduler` 回 GUI。
4. `stop()` 里取消定时器、订阅和监听器；`Runtime.close()` 不由 core 调用。
5. 迟到的结果用代次号、取消事件或 guard 丢弃，避免写入下一轮状态。

## 5. 运行时与数据

core 提供窗口、端口、配置与数据目录；具体业务能力由内置 feature 或示例 mod 提供。

- **动画与行为**：`GraphCatalog` 读取动画目录；`AnimationPlayer` 推进帧；`BehaviorDirector` 选择待机、转向、动作、移动；`ClipLibrary` 按 frameseq → webm → gif 选择后端。
- **多宠与碰撞**：`PetManager` 管理所有 slot；主宠使用 `config.json`，子宠使用 `config-pet-N.json`；碰撞几何在 `engine/collision.py`。
- **设置与菜单**：设置页在 `ui/settings/`；扩展设置页聚合到「扩展管理」；菜单项与设置落点的对应关系由 `ui/settings/coverage.py` 与测试约束。
- **聊天、语音、截图、灵动岛**：对话窗、快速对话、截图动作条、灵动岛窗口在 core；AI 对话、OCR、音乐识别、天气等业务能力由 `petcore/builtin` 或 `examples/petcore-mods` 提供。
- **网络**：访问统一经 `net/`；API 规格与钥匙串在 `api/` 与 `account/`。

数据路径：

| 路径 | 内容 |
|---|---|
| `%APPDATA%/dsh-pet-core/config.json` | 全局配置：角色、缩放、位置、跨屏、音量、菜单偏好、`menu_layout` |
| `%APPDATA%/dsh-pet-core/config-pet-N.json` | 子宠配置；主宠使用 `config.json` |
| `%APPDATA%/dsh-pet-core/mods-state.json` | 停用集合 |
| `%APPDATA%/dsh-pet-core/mods/<id>/` | 用户安装的 feature 或 content 包 |
| `%APPDATA%/dsh-pet-core/feature-data/<owner>/` | 扩展私有数据；卸载不删除 |
| `%APPDATA%/dsh-pet-core/logs/petcore.log` | 轮转日志 |
| `{app}/mods/<id>/` | 安装器投放的包，带 `.trusted` |
| `<repo>/mods/<id>/` | 开发期包源 |

`pet/` 使用 `config-slot-N.json` 与 `overlay-active-pets.json`；`petcore/` 使用 `config-pet-N.json` 与 `mods-state.json`。两套文件不要互相覆盖。

## 6. 构建与验证

```bash
python -m pytest -q
ruff check .
python -m petcore --check --strict --out dist/check.json
python -m petcore --root ""
```

- `--check` 只读；`--strict` 用于构建门。
- PyInstaller 使用 onedir；入口为 `packaging/petcore_entry.py` 或 `packaging/pet_entry.py`。
- 安装包使用 Inno Setup：`packaging/petcore.iss`、`packaging/dsh-pet.iss`。
- 安装器投放的官方 feature 包带 `.trusted`，用户导入的包不带。

关键检查：

| 检查 | 内容 |
|---|---|
| `tests/test_petcore_architecture.py` | engine 不导入 Qt；contracts 只用标准库；`pet.*` 只在两个适配层；网络库只在三个包 |
| `tests/test_petcore_settings_coverage.py` | Core 菜单项必须有设置落点 |
| `tests/test_petcore_docs.py` | SPEC、LOG、LOG-INDEX、README、AGENTS 存在并登记；LNNN 编号一致 |
| `scripts/verify_presentmon.py` | 随包 PresentMon 的摘要校验；构建期执行 |

## 7. 边界与常见误判

| 边界 | 实际行为 |
|---|---|
| 同进程 | 扩展与 core 共用 Python 解释器；扩展的死循环会阻塞桌宠 tick，原生崩溃会终止整个应用。没有沙箱。 |
| `capabilities` | manifest 中的声明，用于展示与安装提示；运行期没有权限检查。 |
| `execution: "worker"` | manifest 允许该值，当前加载器不加载；`ui/app.py:750` 记录 `worker feature is not supported yet`。 |
| 窗口 | 没有窗口端口。扩展需要在菜单命令里自建 `QDialog` 或 `QWidget`，并自己管理显示、置顶、关闭与定时器。 |
| 重新加载 | `stop_owner()` 撤销订阅，`runtime.stop()` 清理，清掉包目录下的 `sys.modules` 后重新导入；没有模块级热卸载。 |
| 可选端口 | 5 个端口恒为 `None`；调用前先判空，缺失在精简包或非 Windows 环境中属于正常状态。 |
| 配置文件 | `pet/` 与 `petcore/` 使用不同的文件名；迁移或排障时先确认运行时。 |

`capabilities` 只描述扩展打算使用什么。真正决定扩展能否运行的是安装位置、`.trusted` 标记与 `allow_feature_mods` 开关。

## 参考

- `docs/PETCORE-ARCHITECTURE.md`：分层、契约、启动路径
- `docs/PETCORE-MOD-FORMAT.md`：manifest、端口、贡献字段
- `docs/PETCORE-MOD-DEVELOPMENT.md`：扩展开发步骤
- `docs/ONEDIR_PACKAGING.md`：PyInstaller 与安装包
- `docs/INDEX.md`：文档入口
