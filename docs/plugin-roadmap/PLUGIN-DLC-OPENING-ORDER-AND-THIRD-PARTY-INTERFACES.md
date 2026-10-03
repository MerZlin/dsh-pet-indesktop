# DLC 开放顺序与第三方接口路线

> **状态：已确认的路线决策（2026-10-02）**
>
> 本文记录一次中期 grill 后形成的 DLC 开放顺序、第三方接口边界、数据存储和卸载语义。它补充[ v5 插件化 / DLC 总路线图](PLUGIN-DLC-ROADMAP-v5.md)，不替代阶段计划、API 合同或实际交付报告。
>
> 当前确认的第一批用户自制功能边界是：**资源 DLC + 外部 Worker**。暂不开放任意 Python host 直接进入 Core；暂不接入 Steam Workshop 或其他复杂社区分发平台。

## 1. 决策摘要

### 1.1 可以并行推进，但不能一次性平级开放

后续允许三条线并行准备：

1. **资源 DLC 线**：角色、主题、台词、音效等纯数据扩展；最早面向用户开放。
2. **官方功能 DLC 线**：屏幕理解、AI 对话、Agent、音乐、语音等官方能力，先由项目完成完整的安装、停用、卸载和恢复样板。
3. **用户自制功能线**：先提供外部 Worker 接口和示例，让用户可以制作独立功能；功能 host 不直接注入 Core。

并行不代表同时宣布所有接口稳定。推荐的公开顺序是：

```text
资源 DLC 接口先稳定
    → 官方功能 DLC 完成一个真实可拔除样板
        → 外部 Worker 接口进入开发者预览
            → 用户自制 Worker 正式开放
                → 条件评估独立 Feature Host
```

### 1.2 不复制 VPet 的深度耦合代码插件模式

VPet 的 MOD 生态值得学习，但其代码插件模型不能直接作为本项目的安全边界。VPet 官方代码中存在从 MOD 目录加载程序集、构造插件并把 `MainWindow` 传给插件的做法；这使插件开发简单，但也意味着插件与主窗口和内部实现强耦合。参考：[VPet `CoreMOD.cs`](https://github.com/LorisYounger/VPet/blob/main/VPet-Simulator.Windows/Function/CoreMOD.cs)。

本项目采用更严格的边界：

- 资源包不执行代码；
- 第三方功能优先使用独立 Worker；
- Core 只提供受限端口，不暴露 `PetApp`、`PetWindow` 或全局 `Config.data`；
- 第三方功能不能直接读取其他功能的数据和凭据；
- 停用、卸载和进程退出的语义必须分别验收；
- 不把“停止功能”宣传为“Python 模块已经热卸载”。

## 2. 从 VPet 思路中学习什么

### 2.1 值得学习的产品模型

VPet 官方项目把动画、物品、文本、主题等资源扩展和代码插件都纳入 MOD 生态，并提供插件示例。这个产品模型对我们有三个启发：

- 用户需要一个容易理解的“扩展包”概念，而不是面对一堆内部模块；
- 官方示例比抽象接口更容易帮助作者开始创作；
- 资源扩展和代码扩展可以共存，但必须区分风险和安装权限。

参考：

- [VPet 官方 README](https://github.com/LorisYounger/VPet/blob/main/README.md)
- [VPet 插件示例仓库](https://github.com/LorisYounger/VPet.Plugin.Demo)

### 2.2 不能直接复制的部分

VPet 的代码插件示例适合展示能力，但不适合作为本项目第三方插件的默认执行模型。直接把主窗口传给外部代码会带来：

- 插件依赖主窗口私有字段和方法；
- Core、插件和 UI 的版本耦合；
- 菜单、信号、定时器和线程容易残留；
- 停用不等于模块卸载；
- 插件异常可能影响主进程；
- 很难证明卸载后没有旧对象继续执行。

因此我们借鉴 VPet 的“统一扩展体验”，不复制“任意代码进入主进程”的实现方式。

### 2.3 对代码扩展提高信任门槛

VPet 后续对代码插件增加了证书、校验和加载限制，这验证了一个重要原则：代码扩展与资源扩展不能使用同一安全等级。参考：[VPet 设置中的 MOD/插件校验逻辑](https://github.com/LorisYounger/VPet/blob/main/VPet-Simulator.Windows/WinDesign/winGameSetting.xaml.cs)。

本项目未来也会要求官方功能包和第三方 Worker 分别经过：

- manifest 和完整文件清单校验；
- Core/API 兼容性校验；
- 能力和权限声明；
- 来源信任及签名校验；
- 真实启动、停止、停用、卸载和恢复测试。

SHA-256 只证明内容身份，不单独证明发布者身份。签名、公钥信任和发布流程留到对应分发阶段实施。

## 3. 扩展类型与开放级别

### 3.1 资源 DLC

资源 DLC 只包含数据，不执行 Python 或其他代码。首批范围包括：

- 角色动画和角色素材；
- 主题；
- 非执行型台词和人格素材；
- 预录音效和语音；
- 节日素材；
- 后续确认的其他纯数据包。

资源包可以最早开放给用户创作，因为它不需要访问窗口、网络、凭据或外部进程。

### 3.2 官方功能 DLC

官方功能 DLC 可以包含：

- Core 进程中的功能 host；
- 设置页和菜单贡献；
- 独立配置、数据和迁移；
- 一个或多个外部 Worker；
- 受控的聊天或其他官方服务联动。

第一批官方功能目标保持不变：

1. 屏幕理解；
2. AI 对话与文件理解；
3. Agent 与外部工具联动；
4. 之后再按风险评估音乐、语音、账户、灵动岛等功能。

官方功能 DLC 先由我们自己验证“安装—启用—停用—卸载—重装”闭环，不能只用代码文件拆分或 Worker 数量证明完成。

### 3.3 用户自制功能 DLC

第一批用户自制功能采用：

```text
用户自己的 manifest
+ 用户自己的 Worker 程序
+ 受限菜单/设置贡献
+ 自己的数据命名空间
+ 通过 pet-worker/v1 与 Core 通信
```

用户可以使用 Python、C#、Rust、Go、C++、Node.js 或其他能读写 stdin/stdout 的语言实现 Worker，但推荐交付为独立可执行文件，而不是把用户的 `.py` 文件直接 import 进 Core。

示例：用户制作番茄钟功能时，可以交付：

```text
tomato-timer/
  manifest.json
  worker/
    tomato_timer.exe
  resources/
    icons/
  schemas/
    config.json
```

它可以声明菜单、设置、通知、定时器和数据存储需求，但不能直接获得 `PetWindow`、`PetApp`、完整配置、keyring 或其他插件目录。

## 4. 推荐的阶段顺序

### Phase 4B：先完成本地安装管理基础

当前优先级仍是：

- 唯一安装状态；
- revision 和并发冲突；
- 跨进程版本租约；
- Worker/host/settings 的版本占用；
- 安装、停用、卸载、重装和事务恢复。

这些能力先内部使用，不直接宣称第三方生态已经开放。资源包和功能包可以共用安装状态、信任和事务基础，但不能共用“资源包禁止执行代码”的安全规则。

### Phase 5A：本地资源 DLC 和作者模板

目标：

- 本地目录和 ZIP 导入；
- `manifest` 示例和校验工具；
- 角色/主题/台词等资源作者模板；
- 安装、启用、停用、卸载和重装；
- 无 Steam 的本地开发流程；
- 一个可运行的官方示例资源包。

这一阶段之后，用户可以自己制作资源 DLC，但还不能因此认为第三方功能代码 API 已经稳定。

### Phase 5B：官方功能选装和外部 Worker 预览

目标：

- 屏幕理解成为首个完整的官方功能包；
- AI 对话与文件理解作为下一项主要拆包目标；
- 官方功能包使用受限 host 和外部 Worker；
- 提供 Worker 开发预览、协议样例和确定性测试工具；
- 保持本地目录/ZIP，不接入 Steam；
- 先验证官方 Worker 能否独立停止、卸载和恢复。

此阶段可以让少量开发者尝试自制 Worker，但应标记为开发预览，不承诺长期 API 兼容。

### Phase 6A：正式开放资源 SDK

目标：

- 发布资源 manifest 规范；
- 发布资源目录结构和验证工具；
- 发布资源包示例；
- 明确 Core 版本兼容矩阵；
- 允许用户使用本地目录或 ZIP 制作和安装资源 DLC。

### Phase 6B：正式开放第三方 Worker SDK

进入条件：

- 官方 Worker 已通过真实安装、停用、卸载、重装和失败恢复；
- `pet-worker/v1` 的握手、能力、请求、响应、取消、心跳和关闭语义稳定；
- Worker 崩溃不拖垮 Core；
- 权限、数据、凭据和贡献撤销边界可测试；
- 版本租约和文件占用可以可靠处理；
- 有最小 Worker 示例和兼容性测试套件。

正式开放后，第三方可以制作外部 Worker 功能，但仍必须遵循 manifest、能力声明、数据隔离和签名/信任规则。

### Phase 6C：条件评估 Feature Host SDK

只有在用户确实需要更深的 UI 和主进程能力时，才评估 Feature Host SDK。默认仍不让任意 Python host 进入 Core。

如果最终需要 Python host，应优先设计为独立 Feature Host Process，而不是在 Core 中 import 第三方模块。这个阶段不是第三方 Worker SDK 的前置条件。

### Phase 7：正式发布门

正式发布阶段检查：

- 最小 Core；
- 已承诺的官方选装范围；
- 资源 DLC 和官方功能包的安装/停用/卸载/重装；
- 三平台启动和目录权限；
- 配置与用户数据迁移；
- Worker 清理、故障恢复和版本回滚；
- 兼容矩阵、签名和公钥轮换策略；
- 文档、示例和作者工具。

第三方生态未开放不阻塞发布，但如果宣传“支持用户自制功能”，必须完成对应的 Worker SDK 和信任门，不能用内部接口冒充公开 API。

## 5. 可并行与不可并行的工作

### 5.1 可以并行

以下工作可以同时准备，彼此不会造成接口方向冲突：

| 工作 | 说明 | 对外状态 |
|---|---|---|
| 4B 安装状态/租约/事务 | 统一扩展生命周期基础 | 内部实施 |
| 资源 manifest 和作者模板 | 纯数据包规范、样例和检查工具 | 可先进入设计/预览 |
| 官方屏幕理解包 | 验证第一个可拔除功能样板 | 官方功能 |
| 官方 AI 对话包设计 | 审计依赖、会话、附件和凭据边界 | 官方规划 |
| Worker 协议文档和示例 | 提前验证跨语言通信和错误语义 | 开发预览 |
| 贡献接口设计 | 菜单、设置、命令、搜索和撤销 | 内部接口 |

### 5.2 不能同时对外承诺

以下组合不能在同一时间被当成稳定完成：

- 任意 Python host 直接进入 Core；
- 无重启的 Python 模块热卸载；
- 第三方 Worker 正式 SDK；
- 自动远程下载和复杂社区分发；
- Workshop/Steam 集成；
- 没有安装事务和版本租约的“可卸载 DLC”。

推荐做法是：允许它们在设计和实验层并行，但按依赖门逐级公开。

## 6. 对外提供的接口层级

### 6.1 `pet-content/v1`

面向纯资源 DLC：

```text
manifest
资源文件
资源校验
Core 版本兼容
角色/主题/台词注册
```

资源包不执行代码。

### 6.2 `pet-worker/v1`

面向外部 Worker：

```text
hello
ready
config_push
request
response
event
heartbeat
error
shutdown
```

协议使用本机 stdin/stdout JSONL。Worker 由 Core 通过显式程序路径和参数数组启动，不使用 shell 拼接命令。

推荐的能力声明包括：

```text
notifications.show
scheduler.create
data.read
data.write
menu.register
settings.register
```

截图、网络、外部进程、密钥、文件系统等高风险能力必须单独声明和审核，默认不授予。

### 6.3 `feature-contribution/v1`

控制功能在桌宠里的入口：

```text
菜单项
子菜单
设置页
搜索项
命令
快捷键
通知类型
```

每个贡献都带 owner 和稳定 ID，例如：

```text
owner = user.tomato-timer
contribution_id = tomato-timer.menu.open
```

卸载时按照 owner 撤销：

```text
撤销菜单
撤销设置
撤销搜索
撤销快捷键
撤销信号连接
撤销订阅
```

### 6.4 `feature-data/v1`

每个 DLC 只访问自己的命名空间，不能通过路径访问其他功能：

```text
data/instances/config-slot-1/user.tomato-timer/
data/instances/config-slot-2/user.tomato-timer/
data/shared/user.tomato-timer/
cache/user.tomato-timer/
```

### 6.5 `feature-credentials/v1`

插件不直接访问系统 keyring。Core 提供：

```text
保存凭据
查询可用状态
申请一次请求凭据
删除自己的凭据
```

授权绑定：

```text
plugin_id
instance_id
profile_id
operation
target_endpoint
```

Worker 只收到当前请求需要的临时凭据，不获得完整 keyring 访问权。

## 7. 第三方 DLC 的文件与数据存储

建议目录结构：

```text
<platform-data>/dsh-pet-standalone/
  plugins/
    user.tomato-timer/
      state.json
      versions/
        1.0.0/
          manifest.json
          manifest.sig
          worker/
          resources/
          schemas/
        1.1.0/
          ...
      staging/
      transactions/
      locks/
      data/
        instances/
          config-slot-1/
            config.json
          config-slot-2/
            config.json
        shared/
          shared.json
      cache/
      diagnostics/
```

### 7.1 安装包目录

`versions/<version>/` 只放不可变的包内容。运行中的 DLC 不应修改这些文件；升级通过安装新版本并切换 `state.json` 完成。

### 7.2 用户数据目录

存放配置、用户创建的数据、会话、记忆、统计和偏好。实例相关数据必须包含实例 ID，不能让多个 `config-slot-N` 互相覆盖。

### 7.3 缓存目录

只放可安全清除的下载、解析、生成和中间缓存。缓存丢失不能破坏用户配置。

### 7.4 密钥

密钥通过 Core 的 `CredentialVaultPort` 和操作系统安全存储管理，不进入 JSON、manifest、备份、诊断或普通 Worker 配置快照。

### 7.5 卸载默认行为

普通卸载：

```text
删除包版本和执行文件
撤销菜单、设置、搜索和命令
停止 Worker/host
保留用户数据
```

重装后恢复：

```text
读取 data/
恢复配置和偏好
重新注册贡献
```

只有用户明确选择“清除数据”时，才清理 `data/`、`cache/` 和对应凭据；清除数据必须是单独的确认动作。

## 8. 关于“Python host 可以即时热卸载”

### 8.1 当前阶段不能这样宣传

如果 Python host 在 Core 进程中 import：

```text
Core
  └─ import feature_host
```

即使执行了：

```text
stop()
dispose()
撤销菜单
取消信号
停止定时器
```

也不能严谨地说 Python 模块已经即时热卸载，因为 `sys.modules`、Qt 对象、回调、定时器、线程和第三方库全局状态可能仍保留引用。

当前和 Phase 4B/5A/5B 更准确的说法是：

```text
功能已停用
执行任务已停止
贡献已撤销
版本进入待释放
```

### 8.2 外部 Worker 能够声称什么

对于外部 Worker，可以在通过验证后声称：

```text
Worker 已停止
Worker 进程已退出
版本租约已释放
功能包文件可移除
```

这叫“进程级卸载”或“功能包卸载”，不叫 Python 模块热卸载。

### 8.3 真正的无重启功能卸载条件

如果未来希望功能无需重启 Core 即可移除，host 需要独立进程化：

```text
Core
  └─ Feature Host Process
       └─ Python host
            └─ Worker
```

只有以下条件全部通过，才能宣传“无需重启 Core 的进程级功能卸载”：

1. Core 不 import 第三方 host；
2. host 单独进程运行；
3. host 退出后没有残留子进程；
4. Core 没有 host 对象、模块和回调引用；
5. 菜单、设置、搜索、快捷键全部撤销；
6. Worker 和 host 版本租约全部释放；
7. 包文件确实可以删除；
8. 重装后配置可以恢复；
9. Windows 文件占用、Qt 对象、线程、子进程和崩溃路径均有证据。

因此：

- Phase 4B：不能声称 Python host 热卸载；
- Phase 5A/5B：即使 Worker 可卸载，也不能声称 Python host 热卸载；
- Phase 6C：可以评估独立 Feature Host Process；
- Phase 7：只有实际实现并通过发布验收，才能宣传无需重启 Core 的功能卸载。

对外优先使用：

```text
可停用
可卸载
进程级卸载
无需重启 Core 的功能卸载
```

避免使用“Python 模块热卸载”，除非完成独立 host 进程方案并通过完整验收。

## 9. 最终路线与用户体验目标

### 9.1 路线总览

```text
Phase 4B
  完成本地状态、租约、事务
       │
       ├── 并行设计资源 DLC SDK
       ├── 并行准备 Worker SDK 示例
       └── 官方屏幕理解继续作为首个功能包

Phase 5A
  本地资源 DLC、目录/ZIP、作者模板

Phase 5B
  官方功能选装、屏幕理解、AI 对话与文件理解、外部 Worker 预览

Phase 6A
  正式开放资源 SDK

Phase 6B
  正式开放第三方 Worker SDK

Phase 6C（条件）
  独立 Feature Host Process，评估 Python host 和无需重启 Core 的功能卸载

Phase 7
  正式发布、签名、兼容矩阵、迁移、升级、卸载和作者文档
```

### 9.2 用户最终能做什么

在不依赖 Steam 的情况下，用户可以：

1. 下载或自己编写一个资源 DLC 目录/ZIP；
2. 在本地导入并安装；
3. 在桌宠中看到对应角色、主题或内容入口；
4. 停用后不再执行，卸载后入口和包文件消失；
5. 重装后恢复保留的配置和偏好。

后续用户还可以制作外部 Worker 功能：

1. 按 manifest 声明功能和权限；
2. 用任意语言实现独立 Worker；
3. 通过 `pet-worker/v1` 与 Core 通信；
4. 通过贡献接口注册菜单和设置；
5. 只访问自己的数据和获授权的服务；
6. 停用或卸载时停止自己的进程并撤销自己的入口。

### 9.3 当前没有提前实现的部分

本文是路线和接口边界记录，不代表以下能力现在已经可用：

- 用户自制 Worker 正式 SDK；
- 用户自制功能包的正式签名发布；
- Steam/Workshop；
- 远程 DLC catalog；
- Python host 无重启热卸载；
- 默认完整构建已经变成最小 Core；
- 应用内扩展管理已经完成；
- 三平台实机发布验收。

## 10. 关联文档

- [v5 插件化 / DLC 总路线图](PLUGIN-DLC-ROADMAP-v5.md)
- [功能归属与交付总表](PLUGIN-FEATURE-DELIVERY-MATRIX.md)
- [Phase 4B 本地管理设计](../plugin-phase-04-updates/PHASE4B-LOCAL-MANAGEMENT-DESIGN.md)
- [本轮 grill 记录](../grill-2026-10-02-dlc开放顺序与第三方接口.md)
- [文档索引](../INDEX.md)

## 14. 外部基线评审后的加固顺序（2026-10-02）

外部评审意见与本路线的差异、P0 资源问题和未复现项见[DLC 基线评审响应](DLC-BASELINE-REVIEW-REMEDIATION-2026-10-02.md)。在向用户开放自制 DLC 前，必须先通过：

1. 角色资源安装后由 Registry 解析并实际播放；
2. 冲突安装不删除旧 active，缓存不进入内容摘要；
3. Starter/Core 更新成功、失败、回滚都保留可用 fallback；
4. 本地状态、租约、事务、延迟 GC 和 Windows 文件占用有证据；
5. 资源包、官方 host、官方 Worker 的激活/卸载语义分别定义。

因此“先开放资源 DLC、随后开放受控 Worker”仍成立，但资源工具和示例不能绕过 P0 资源链。第三方作者先接触 manifest、资源 schema、贡献合同和本地验证工具，不获得任意 Python `entrypoint` 或 Core 私有对象。

## 15. 4B-1.5 之前的公开承诺门（2026-10-02）

资源 DLC 的公开稳定承诺必须等待 Phase 4B-1.5 通过：资源包根路径可解析、安装后实际可播放、冲突不误删、Starter/Core 更新保护、派生 cache 与发布内容隔离、卸载中断可恢复。4B-1.5 之前可以继续制作内部样例、作者文档草稿和验证工具，但必须标记为内部验证，不要求外部作者依赖未封存的 path/manifest 细节。

开放顺序仍为：纯资源文档/示例 → 本地资源制作与校验工具 → 受控官方 Worker 包格式 → 第三方外部 Worker 预览 → 条件性社区目录/平台分发。官方资源硬门通过前，不把已有 manifest 或 Worker 协议说成“可安装、可卸载的稳定 SDK”；第三方 `in_process` Python host、任意 entrypoint 和 Python host 即时热卸载仍不开放。
