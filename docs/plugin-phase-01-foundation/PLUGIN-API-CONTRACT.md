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

## 5. 菜单、设置、命令与快捷键贡献（待实现）

**权威安装状态在宿主管理层**，不得用配置键存在、Worker 存活或遗留文件单独推断。贡献描述至少表达所属包/版本、稳定贡献 ID、类型/入口、显示分组与排序偏好、启用条件、命令/页面绑定及可撤销句柄；这是语义合同，具体类名与 manifest 字段待 Phase 4A 设计。

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

## 7. capability、故障与 fallback

能力由宿主再次检查，不因 manifest 声明自动授权。节日提醒的通知、语音、配置、调度、菜单能力不意味着获准网络、截图、外部进程或读取密钥；具体端口与命名参照 Phase 2 实现。

插件异常不阻塞主窗口/退出；停用必须清理计时器、订阅与命令。Worker 有界重启和 in-process fallback 仅服务于**仍安装、启用且获授权**的功能。卸载、停用、撤销截图授权后不能由 Core 静态 import 或旧 fallback 重新执行；旧实现若保留，也归该功能包。

## 8. 文件与数据边界及验收

资源安装与可执行功能安装分类型验证；共用事务原则但不混用执行权限。专属代码/依赖/UI/资源归包，Core 保留通用宿主。普通/便携路径、包格式/ID/Worker 产物与共享依赖规则是 Phase 4A/5A 的必交设计，本文不凭空固定。

卸载默认保留配置/会话等用户数据，清除另行确认；Core 更新不能删除 DLC 或重新装回已卸载功能。逐项以“未装可启动→安装可用→停用不执行→卸载无文件/入口→重装恢复”验收，不能以 UI 隐藏或启动进程数替代。

## 9. 最终效果

同一官方功能包可以装进主进程 UI，也启动独立 Worker；Core 通过受限端口/JSONL 管理它，不直接把功能永久打进本体。未安装就没有专属菜单和设置，安装后自动注册，卸载后统一撤销并保留偏好。当前具备资源和运行时基础，上述功能包加载、贡献与物理卸载闭环仍待 Phase 4/5 实现；第三方 SDK 不影响这项必做工作。
