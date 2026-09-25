# v4 → v5 配置与资源迁移合同

> **状态：迁移基线（2026-09-25）**
>
> 迁移是插件化路线的一等能力。任何 Phase 2/3/4 的实现都不得把“配置能读出来”当成迁移完成；必须可备份、可校验、可重复执行并可恢复。

## 1. 目标

- 保留角色选择、位置、缩放、朝向、透明度、基础交互和多实例配置。
- 将旧角色别名、角色档案和资源路径迁移到对应 Content DLC 命名空间。
- 将 Core 配置与 `plugins.<plugin_id>` 配置分离。
- Chat 会话继续保留；Provider 密钥、token、API Key 仍由 keyring/安全存储管理。
- 无法迁移的旧字段保留原始值并标记 `legacy`，不静默丢弃。

## 2. v5 数据分层

```text
data/
  core.json
  instances/
    config-slot-N.json
  plugins/
    <plugin-id>/
      config.json
      data/
      cache/
      logs/
  sessions/
  secrets/
```

Core 只保存 Core 状态和实例边界；插件通过自己的 `PluginConfigStore` 访问自己的命名空间；Worker 只收到经过筛选的配置摘要，不收到普通配置中的密钥。

## 3. 迁移前保护

迁移开始前：

1. 识别 v4 数据文件、实例 slot、角色别名和旧插件字段。
2. 生成带版本和时间戳的只读备份，记录源文件哈希。
3. 将迁移计划写入诊断报告，包含成功、跳过、legacy 和人工处理项。
4. 先写 staging，验证通过后再原子切换正式文件。

迁移不直接覆盖唯一原始文件。写入失败、进程中断或校验不一致时，旧版本仍可恢复。

## 4. 重复执行和恢复

迁移必须满足：

```text
迁移中断
→ 恢复旧备份
→ 重新执行
→ 不重复破坏用户数据
```

要求：

- 每个迁移步骤有输入版本、输出版本和幂等判定。
- 已完成步骤再次运行不得重复复制会话、重复追加数组或覆盖用户后来修改。
- 迁移后重新读取并校验角色、实例、插件配置和 session 索引。
- 恢复操作保留失败报告，不删除原始备份。
- 迁移失败时 Core 可以以旧配置或最小 fallback 启动。

## 5. 兼容映射

| v4 数据 | v5 目标 | 规则 |
|---|---|---|
| 全局角色选择/别名 | Core 实例 + Content provider | 解析别名；找不到时保留原值并使用 fallback。 |
| 位置、缩放、朝向、透明度 | `instances/config-slot-N.json` | 每个 slot 独立迁移，不合并实例。 |
| `festival_reminder_*` 等扁平字段 | `plugins.<plugin_id>.settings` | 首次读取迁移；旧字段放入 `legacy`。 |
| Chat 会话 | `sessions/` | 保留会话数据，不复制密钥。 |
| Provider/API Key/token | `secrets/` / keyring | 不进入普通 JSON，不推送给 Worker。 |
| 未知旧字段 | 对应 `legacy` | 可诊断、可导出、不得静默删除。 |

## 6. 验收矩阵

- 基础配置、角色别名、多实例和 Chat session 可迁移。
- 缺失 DLC、旧路径、坏 JSON、半写入文件和未知字段有明确诊断。
- 迁移中断后可重复执行，不重复破坏数据。
- 迁移失败恢复后，Core、角色 Registry 和插件配置仍可读取。
- Worker 只收到允许的配置摘要；keyring 内容不出现在日志、快照和普通配置中。
- 迁移报告记录成功、跳过、legacy 和需要人工处理的项目。

## 7. 阶段关系

Phase 1 先保证资源 Registry 和 fallback；Phase 2 用命名空间适配旧字段；Phase 3 在进程边界推送摘要；Phase 4 再考虑迁移后的 DLC 版本兼容。若任一后续阶段失败，保留备份、legacy 和 v4 读取适配，从最近一次验证通过的边界重启。
