# v4 → v5 插件化数据迁移说明

> 状态：迁移设计基线，日期 2026-09-24。分层见 [`PLUGIN-DLC-ARCHITECTURE.md`](PLUGIN-DLC-ARCHITECTURE.md)，API/配置边界见 [`PLUGIN-API-CONTRACT.md`](PLUGIN-API-CONTRACT.md)。

## 1. 原则

1. v4 原始数据只读解析，迁移不直接破坏原文件。
2. 先写 staging，再原子替换生成 v5 数据。
3. 迁移可重复执行，不重复破坏或复制会话。
4. 未识别字段保留到 `legacy`，不能静默丢弃。
5. 密钥不复制到普通 JSON，继续通过 keyring/安全存储读取。
6. 首次启动生成迁移报告，区分成功、跳过、警告和人工处理。

## 2. 目标数据

```text
data/
  core.json
  instances/config-slot-N.json
  plugins/<plugin-id>/{config.json,data,cache,logs}
  sessions/
  secrets/
```

## 3. 迁移矩阵

| v4 数据 | v5 目标 | 策略 |
|---|---|---|
| 角色选择 | 实例配置 `character` | 保留稳定 ID；若进入 DLC，写入 provider ID 映射 |
| 位置、缩放、朝向、透明度 | 实例配置 | 逐字段复制并范围校验，非法值回退默认 |
| 基础交互、碰撞和鼠标行为 | Core/实例配置 | 不放入角色插件 |
| 多实例 `config-slot-N.json` | `instances/` | 保持编号和隔离关系 |
| 角色别名、档案 | 角色 DLC 命名空间 | 找不到 DLC 时保留 `legacy.character_profile` |
| 台词、人格、点击绑定 | 角色/功能 DLC | 只迁移可识别字段，未知策略写 legacy |
| Chat Provider 设置 | Chat DLC + keyring | 迁移非敏感设置，密钥只迁移引用 |
| Chat 会话 | `sessions/` 或 Chat DLC | 保留会话，即使 Chat DLC 未安装 |
| 网络、歌词、Agent 开关 | 对应插件 | 不兼容时保留待处理状态 |
| 缓存和临时文件 | 插件 cache | 默认重建，不作为用户数据迁移 |
| 未识别字段 | `legacy` | 保留原始片段和原因 |

## 4. 流程

```text
读取 v4 → 备份 → 识别实例/角色 → 迁移 Core 字段
→ 迁移插件字段 → 迁移会话/keyring 引用 → 保留 legacy
→ 写 staging → 校验 schema/路径 → 原子提交 → 生成报告
```

`migration-report.json` 至少包含迁移时间、源/目标版本、成功字段、跳过字段、警告、人工处理项、备份路径和可重试状态。

## 5. 角色与 Chat

角色资源可能仍位于 `assets/characters/<id>/videos/` 或旧外部 `characters/`。迁移优先使用兼容 DLC，缺失时使用 fallback/内置资源；`body_box`、`head_box`、动作分类和步幅按 manifest 读取；资源损坏只记录警告，不阻塞 Core；角色别名必须保留映射。

Chat UI 第一阶段仍是官方懒加载插件，但迁移器只能搬运非敏感 Provider 设置、模型、端点和显示项，通过 keyring 恢复 secret 引用。secret 无法恢复时要求重新登录或录入；禁止把明文 key 写入插件配置、日志或迁移报告。

## 6. 回滚与验收

迁移前创建带时间戳的只读备份。任一步骤失败时保留 staging 和错误报告，原 v4 数据保持可用。部分成功不得覆盖未校验目标；修复缺失 DLC 或 secret 后可重试；迁移完成后保留 legacy，直到后续版本明确清理策略。

验收包括：基础配置、多实例、角色别名和档案、Chat 会话与 keyring、未知字段报告、可重复迁移、缺失 DLC/坏 manifest/非法路径/无效 secret 的可操作诊断，以及迁移失败时旧版本仍可启动。
