# 插件 / DLC 内部接口与可选交付合同

> **修订：2026-09-27。Phase 1/2 已有内部基线；官方功能包、UI 贡献完整生命周期为 Phase 4/5 待实现合同。**
>
> 本文不是第三方稳定 SDK。归属见[功能总表](../plugin-roadmap/PLUGIN-FEATURE-DELIVERY-MATRIX.md)，阶段见[总路线图](../plugin-roadmap/PLUGIN-DLC-ROADMAP-v5.md)，数据见[迁移合同](PLUGIN-MIGRATION-v4-to-v5.md)，安装见[事务合同](../plugin-phase-04-updates/PLUGIN-UPDATE-PROTOCOL.md)。

## 1. 已实现接口与未来合同分开

已有内部边界：`CharacterRegistry`、`ContentManager`、`ContentProviderRegistry`、Phase 2 `PluginContext`/`CoreEventBus`/配置命名空间/受限端口、官方 allowlist/factory，以及 Phase 3 Worker 宿主。实际签名以实现和测试为准，不根据类名推定公开 SDK。

待实施：可信官方功能包的受控加载、包所有权与完整菜单/设置/搜索/快捷键贡献合同、独立构建/依赖分发与物理卸载。现有命令注册或菜单动作模型不等于上述闭环都已有。

第三方 content/Worker SDK、社区目录和 Workshop 条件化；**官方功能包不等待它们**。任何对外兼容承诺都需真实官方包迁移、错误语义和版本迁移验证。

## 2. 数据型 content manifest

示意如下；版本范围是 v5 设计示例，不代表当前应用已发布 v5：

```json
{
  "id": "official.character.shenshen",
  "name": "深深角色包",
  "version": "1.0.0",
  "kind": "content",
  "api_version": "1",
  "core_requires": ">=5.0.0,<6.0.0",
  "platforms": ["windows", "macos", "linux"],
  "dependencies": [],
  "capabilities": ["character", "animation", "phrases"],
  "entrypoint": null,
  "content": {"characters": ["shenshen"]},
  "integrity": {"sha256": "...", "signature": null}
}
```

- `kind=content`、空 `entrypoint`；ID/角色 ID/版本/相对路径受限，兼容失败不能激活。
- 目录与 ZIP 使用同一逻辑内容 SHA-256，按 POSIX 相对路径排序；拒绝绝对路径、`..`、符号链接、越界解压与缺失资源。
- 当前默认校验需要声明 SHA-256；现有 `allow_unsigned=True` 是显式开发宽松入口。代码中的命名不能解释为已实现发布者签名认证：`signature`/`verify_signature` 仍是扩展点。
- **SHA-256 验证内容，不认证发布者。** 数据包现有能力不作为可执行包的安全担保；可信发布签名另行落地。

## 3. 内容 provider

```python
class CharacterRegistry:
    def scan(self) -> list[CharacterPackage]: ...
    def get(self, character_id: str) -> CharacterPackage | None: ...
    def list_available(self) -> list[CharacterPackage]: ...
    def resolve(self, character_id: str) -> CharacterPackage: ...
```

Core 管理来源与资源 fallback；消费者通过 provider 查询，不绕过 Registry 拼安装路径、修改全局配置、操作 PetWindow 私有字段或执行内容包代码。角色资源 fallback 不适用于复活已卸载的功能。

## 4. 官方功能运行时与交付边界

`PluginContext` 提供自己的配置命名空间、事件、能力与 logger，以及展示、通知/音频、调度、命令和内容端口。不暴露 `PetApp`/`AppShell`/`PetWindow` 私有对象、`Config.data`、内部 Qt 信号或安全存储实现。

官方功能可以拥有在 GUI 线程创建的 QWidget/设置页及其生命周期；这些 UI、策略和 adapter **随功能包交付**，不能只因为在 Core 进程运行就留入最小 Core。禁止的是反向依赖 Core 私有实现，不是禁止功能拥有自己的 UI。Chat UI 暂不要求独立 Qt 进程。

当前仍由显式 allowlist/factory 注册，不执行未知 `entrypoint`。Phase 4A 必须先确定官方包的信任根、许可范围、兼容/权限、独立构建与受控加载，才能从包激活功能；本地 ZIP 与 Setup 旁置包同样执行前校验。资源 ContentManager 不能直接升级为任意 Python 代码加载器，也不承诺热卸载模块。

### 4.1 屏幕理解最小拆包端口（已确定设计，待实现）

详细合同以 [Phase 4A 最小拆包设计](../plugin-phase-04-updates/PHASE4A-SCREEN-PACKAGE-DESIGN.md)为准；下面的名称是内部接口设计，不是已可调用的第三方 SDK。

| 接口边界 | 宿主与功能包责任 |
|---|---|
| `DesktopQueryPort` / 受限窗口状态端口 | 通用平台查询只提供前台、光标、空闲时间及明确的无结果/不支持状态；不含截图、Pillow、模型或网络。Worker 自带纯平台后端，功能 host 不取得 Core 私有对象 |
| `VisionProfile` / `VisionSettings` 与 `CredentialVaultPort` | 视觉配置属于功能包；安全存储按功能、实例、profile 隔离。自动/手动旧行为分别确认迁移，之后不跟随聊天配置；Worker 只收单次临时授权凭据 |
| owner 贡献 | 主进程运行的功能 UI 仍随包交付；菜单、设置、搜索、命令、快捷键和订阅可按 owner 撤销，不把打开设置当作启动业务 |
| 可选外部问答服务 | AI 包注册受限服务，屏幕包定向调用；Core 只做发现、授权、路由与错误隔离，不承接会话业务，详见 §6.1 |
| 安装描述与构建边界 | 屏幕包同一版本交付 host 和自包含 Worker；先校验官方发布者签名、路径、文件哈希和兼容性，再定位受控 factory 与程序/参数数组 |

## 5. 菜单、设置、命令与快捷键贡献（待实现）

**权威安装状态在宿主管理层**，不得用配置键存在、Worker 存活或遗留文件单独推断。贡献描述至少表达所属包/版本、稳定贡献 ID、类型/入口、显示分组与排序偏好、启用条件、命令/页面绑定及可撤销句柄；这是语义合同；屏幕理解的最小端口和注册状态已在 [4A 设计](../plugin-phase-04-updates/PHASE4A-SCREEN-PACKAGE-DESIGN.md)确定，具体序列化 schema 与实现签名仍须在迁移前验证。

| 包状态 | 注册和执行行为 |
|---|---|
| 未安装 | 无功能菜单、专属设置、搜索项、命令/快捷键；扩展管理可展示可安装项 |
| 已安装且启用 | 权限/兼容通过后注册对应入口；仅打开设置不得启动截图、模型请求或业务轮询 |
| 已安装但停用 | 不执行业务；撤销功能执行入口/订阅，保留管理、配置、启用与卸载能力；配置页只编辑数据 |
| 故障/不兼容 | 撤销不可执行动作，管理页给出原因与修复/回滚入口，不留死按钮 |
| 已卸载 | 撤销所属菜单、设置、搜索、命令、快捷键、订阅和任务；偏好可保留给重装，但不再显示功能入口 |

- 托盘和右键菜单共用命令贡献模型，设置导航/搜索同样按 owner 注册/撤销；不提前重排整套菜单。
- 用户排序与快捷键偏好可保留为数据；未安装时不得保留有效绑定，重装处理冲突后再注册。
- 注册失败要撤销本次已注册项；停止/故障/卸载清理具有幂等性，不影响其他包。跨实例窗口与 shared subsystem 不能重复注册或错误路由。
- 生命周期先停止生产者/请求/Worker，再撤销订阅和 UI 贡献、释放资源，最后按事务移除文件。进程内模块不能即时卸载或文件被占用时显示待重启，不谎报物理卸载完成。

## 6. Worker 控制协议、业务语义与凭据

- `pet-worker/v1` 是控制/传输外壳：`hello`、`ready`、`config_push`、`heartbeat`、`error`、`shutdown`、`event`，以及 Phase 3B 的 `request`/`response`；后两者使用 request ID、operation 和 generation 对应请求/响应。
- `agent-event/v1` 是 Agent 业务事件语义，不把 DSH bridge 的文件 tail/WebSocket/外部安装细节变成通用 Worker 协议。
- 当前 JSONL 有单行上限（64 KiB）；无效消息隔离诊断，旧 generation/失效请求不得展示。识屏的反向 `budget_check` 仍由主进程批准额度。
- `config_push` 只含筛选后的非敏感运行摘要；Worker 不直接读全局 `Config.data`、keyring、完整 Provider/会话配置。
- **允许已授权单次请求携带临时凭据**：Core 读取安全存储，在识屏 `analyze_frame`/`manual_look` 等必要请求下发，本次完成/失败/取消/超时后释放引用，不落盘、不进日志/诊断/普通快照。这不等于把 Key 放进 `config_push`；不承诺 Python 内存可被物理擦零。

### 6.1 识屏—聊天的可选联动（待实现）

AI 包注册暂名 `chat.external-turn/v1` 的服务，屏幕包通过受限服务发现取得路由句柄，再追加外部问答。Core 按能力和明确目标路由，**不通过广播 Event Bus 分发屏幕结果**。会话选择、保存、界面刷新与后续聊天上下文归 AI 包；结果有效性、气泡和是否发起同步归屏幕包。

- 只传来源功能、结果 ID、自动/手动来源、实例/角色/窗口及会话路由上下文、请求说明和完整分析文字；不传截图、密钥、完整配置或会话对象。
- 两包启用时保留文字同步；Chat 窗口未打开仍可由 AI 包持久化。Chat 正在生成返回 `busy`，不排队、不补发、不打断、不自动追加一次模型请求。
- 手动结果只给发起窗口；共享自动识屏沿用明确的多窗口目标集合，分别路由，不扩大为全局广播。失效 generation、已关闭窗口或过期路由不得写入新会话。
- AI 包负责按来源/结果/目标幂等处理，返回追加、重复、繁忙、不可用、拒绝、过期或存储失败等明确结果。某目标失败不撤销其他目标已保存的记录。
- 聊天缺失、停用、繁忙或接收失败不影响识屏气泡，不创建待补发记录；卸载识屏不删除聊天拥有的历史。联动不授予聊天截图权限。

## 7. capability、故障与 fallback

能力由宿主再次检查，不因 manifest 声明自动授权。节日提醒的通知、语音、配置、调度、菜单能力不意味着获准网络、截图、外部进程或读取密钥；具体端口与命名参照 Phase 2 实现。

插件异常不阻塞主窗口/退出；停用必须清理计时器、订阅与命令。当前 Phase 3B 内置识屏的 `in_process` 回滚路径本轮不变；**屏幕理解独立交付后，Worker 有限重启耗尽即暂停功能，只提供重试/版本回滚，不回 Core 执行截图或网络逻辑**。其他功能保留 fallback 时也仅服务于仍安装、启用且获授权的功能，不能绕过停用、卸载或权限撤销。关闭自动识屏仍允许手动请求；停用整个包则禁止两者。

## 8. 文件与数据边界及验收

资源安装与可执行功能安装分类型验证；共用事务原则但不混用执行权限。专属代码/依赖/UI/资源归包，Core 保留通用宿主。屏幕理解暂定 `official.screen-understanding`，自包含 Worker 保留 `proactive-screen` ID；[4A 设计](../plugin-phase-04-updates/PHASE4A-SCREEN-PACKAGE-DESIGN.md)确定应用数据根下的 `plugins/<id>/state.json`、同版 host/Worker、版本租约及数据分离。当前角色资源事务不改用这套状态文件；功能包序列化、构建验证和 Phase 5A 显式便携仍待落地。

卸载默认保留配置/会话等用户数据，清除另行确认；Core 更新不能删除 DLC 或重新装回已卸载功能。逐项以“未装可启动→安装可用→停用不执行→卸载无文件/入口→重装恢复”验收，不能以 UI 隐藏或启动进程数替代。

## 9. 最终效果

同一官方功能包可以装进主进程 UI，也启动独立 Worker；Core 通过受限端口/JSONL 管理它，不直接把功能永久打进本体。未安装就没有专属菜单和设置，安装后自动注册，卸载后统一撤销并保留偏好。当前具备资源和运行时基础，上述功能包加载、贡献与物理卸载闭环仍待 Phase 4/5 实现；第三方 SDK 不影响这项必做工作。

## 8. 包身份、版本与签名字段边界（2026-10-02）

以下概念不能混为一个“hash/版本”字段：

- manifest 原始字节的 SHA-256：用于关联状态提交与 manifest 内容；
- 包文件清单摘要：用于检查已登记文件是否被替换；
- 签名：证明持有受信任发布者私钥的一方签署了指定 manifest；
- `key_id`/信任根/撤销：用于选择和管理发布者身份，当前正式轮换与撤销合同仍待 Phase 5 冻结。

最小兼容字段语义：`api_version` 描述接口合同，`core_requires` 描述 Core 兼容范围，`schema_version` 描述持久化数据格式。未知字段默认拒绝执行或按包类型明确忽略；legacy 字段只能进入有来源身份、版本和 sunset 条件的迁移，不得静默覆盖新配置。

包类型的激活语义分开定义：纯 content 只激活数据；官方 in-process host 需要固定 factory、可信加载和进程占用；官方 Worker 还需要程序路径、能力 allowlist、租约和子进程退出。Phase 1 不开放任意 Python `entrypoint`，也不承诺 host 热卸载。

缓存和派生文件不得进入发布 manifest 或内容摘要；配置、缓存、凭据不放入版本目录。
