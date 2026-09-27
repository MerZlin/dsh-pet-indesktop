# Phase 2：官方插件运行时基线与后续贡献合同

> 修订：2026-09-27。**已完成运行时基线；物理选装、菜单/设置按包注册仍是后续目标。**

## 1. 已完成与不能据此宣称完成的事

已有 `PluginRegistry`、`PluginContext`、`CoreEventBus`、配置命名空间、capability、生命周期与错误隔离；官方节日提醒已经用于验证接口。Phase 1 内容 provider 继续管理角色，不在这里重复扫描资源。

这些成果解决运行边界，不证明功能代码已从 Core 安装包移除，也不证明卸载后菜单、设置和专属依赖已经消失。当前使用内置显式 allowlist/factory，不开放任意第三方 Python `entrypoint`，不承诺 Python 模块热卸载。

## 2. 后续必须扩展的合同

- Core 提供通用窗口、展示、配置、安全存储授权、调度、命令及扩展管理宿主；具体功能拥有自己的 UI、策略、配置和适配器。
- **GUI 主进程运行不等于随 Core 安装包交付**。Chat QWidget 和其他官方功能 UI 可随功能包交付，在受控加载后由 GUI 线程运行。
- 菜单、设置页、搜索、命令、快捷键和事件订阅必须能按包所有者登记和撤销；托盘和右键菜单共用贡献模型。
- 设置页可以在功能停用时提供配置与启用入口，但打开页面不能启动截图、网络请求或后台监视。
- 未安装时不展示专属入口；故障时有原因与修复入口；卸载后统一撤销。完整状态合同以 [API 合同](../plugin-phase-01-foundation/PLUGIN-API-CONTRACT.md) 为准，不在本阶段另建一套。
- fallback 只能服务于已安装、启用且获授权的功能，不得绕过停用或卸载。

这些是 Phase 4 屏幕理解样板、Phase 5 官方功能推广需要验证的新合同，**不是对 Phase 2 已完成状态的补写**。扩展管理是本地选装硬目标，不等待远程 catalog 或第三方 SDK。

## 3. 边界与依赖

插件只依赖公开服务端口，不导入 `PetApp`、`PetWindow` 私有实现或操作全局 `Config.data`。拥有插件自身的 Qt UI 不等于持有 Core 私有窗口。Worker 通过 IPC 接收筛选后的数据，不获得插件宿主内部对象。

`config_push` 不含密钥；需要网络授权时，Core 可在已授权的单次请求内临时传递凭据，不能把此例外扩大为完整配置快照。具体协议与 Phase 3B 保持一致。

Phase 2 不解决 PyInstaller 包体瘦身；物理依赖和构建产物由 Phase 4A 审计。不为支持第三方而提前扩展接口。

## 4. 阅读与重启点

- [运行时设计](PLUGIN-RUNTIME-DESIGN.md)：已实现边界与后续贡献扩展。
- [测试计划](PLUGIN-RUNTIME-TEST-PLAN.md)：现有回归和新增选装验收分开。
- [功能归属总表](../plugin-roadmap/PLUGIN-FEATURE-DELIVERY-MATRIX.md)：功能及 UI、依赖和数据的唯一主要归属。
- [总路线图](../plugin-roadmap/PLUGIN-DLC-ROADMAP-v5.md)、[Worker 阶段](../plugin-phase-03-worker/README.md)、[本地可拔除样板](../plugin-phase-04-updates/README.md)。

失败重启：保留 Phase 1 → 禁用官方进程内功能 → 只恢复 Context/EventBus/Config 最小边界 → 重新验证官方插件。不要为重做运行时删除用户资源或配置。

## 5. 给使用者的效果说明

现在已有“功能通过受限接口接入”的基础，还没有因为这一步就变成可选安装。后续功能包可以同时包含在主进程运行的 UI 和独立 Worker；两者通过端口和 JSONL 连接 Core，不需要用户手动开后台窗口。功能包文件与数据将分开管理，安装位置在 Phase 4A/5A 确定；安装、卸载由统一扩展管理处理。最终没装某功能就没有它的专属菜单和设置，而不是只把开关关掉。
