# Core 与 DLC 更新协议

> 状态：设计协议，基线日期 2026-09-24。本文补充 [`PLUGIN-DLC-ARCHITECTURE.md`](../plugin-phase-01-foundation/PLUGIN-DLC-ARCHITECTURE.md)；当前 Core 更新接口仍以 `pet/updater.py` 和 `pet/update_settings.py` 为准，不在本文中推翻。

## 1. 两条独立更新链

### Core 更新

继续沿用当前 Core 更新链：`pet/updater.py`、`pet/update_settings.py`、静态 `update.json`、GitHub API/CDN 多源、HTTPS/大小/SHA-256 校验和 Windows Inno Setup 安装器。后续可抽象为通用 `UpdateService`，但 DLC 更新不得复制逻辑后覆盖现有接口。

### DLC 更新

DLC 使用独立 `plugins-index.json`。DLC 下载、staging、激活和回滚只能写入插件目录，不能写入 Core 安装目录。

## 2. Catalog

```json
{
  "schema_version": 1,
  "catalog_version": "2026.09.24",
  "plugins": [
    {
      "id": "official.character.shenshen",
      "version": "1.0.0",
      "core_requires": ">=5.0.0,<6.0.0",
      "platforms": ["windows", "macos", "linux"],
      "urls": ["https://example.invalid/shenshen-1.0.0.zip"],
      "size": 123456,
      "sha256": "...",
      "signature": "...",
      "dependencies": [],
      "allow_overwrite": false,
      "rollback": true,
      "release_notes": "..."
    }
  ]
}
```

条目必须声明插件 ID、版本、Core 范围、平台、至少一个地址、大小、SHA-256、签名、依赖、覆盖策略、回滚能力和发布说明。多镜像按顺序尝试，并记录每个失败原因。

## 3. 安装流水线

```text
获取 catalog
→ 校验来源与格式
→ 过滤平台与 Core 兼容版本
→ 检查依赖
→ 下载到 staging
→ 校验大小 / SHA-256 / 签名
→ 安全解压并校验 manifest
→ 原子切换 active 版本
→ 启动插件自检
→ 失败则回滚
```

下载文件必须进入插件专用 staging，不能直接覆盖 active。中断下载保留可重试文件和诊断信息。推荐使用版本目录加 active 指针：

```text
plugins/<plugin-id>/
  1.0.0/
  1.1.0/
  active.json
  staging/
  logs/
```

active 指针使用同目录临时文件和原子替换。新版本启动自检失败、manifest 校验失败、worker 握手失败或 Core 兼容性变化时，恢复上一版本，并保留失败版本与日志。

## 4. 安全要求

正式模式拒绝缺签名包；开发模式只能显式允许未签名插件，并在诊断中标红。大小、SHA-256、签名、manifest 任一失败都拒绝激活。解压拒绝绝对路径、`..`、越界符号链接和覆盖 Core 文件的目标。DLC 更新不得修改 Core 可执行文件、Python 包或安装器配置。失败日志不得包含 API Key 或 token。

签名算法和公钥轮换策略必须在正式发布渠道确定前单独冻结；本协议不把“存在 signature 字段”误认为已经完成密码学验证。

## 5. 兼容与测试

Core 更新不删除 DLC，DLC 更新不修改 Core；Core 降级后重新检查插件；不兼容插件进入 disabled 并显示原因；缺依赖只禁用受影响插件链；用户配置、旧版本和失败包默认保留。

必须测试多镜像回退、大小/哈希/签名失败、下载中断重试、恶意路径、启动自检回滚、无网络/代理/VPN/CDN 失败，以及 Core/DLC 互不覆盖。
