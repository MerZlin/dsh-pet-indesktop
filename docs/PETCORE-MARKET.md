# petcore 插件市场

> 基线：2026-10-22，`petcore` 0.1.0。本文描述 v1 已落地的市场客户端：静态索引、下载校验、安装/更新/回滚与设置页。数字签名、CDN 镜像和 npm 通道**尚未实现**，边界见 §8。

## 1. 为什么不是 npm

Coopanion/Cortico 用 npm 分发扩展，是因为它本身是 Electron，Node/pnpm 是现成运行时。我们的情况不同：

| 维度 | npm | petcore |
|---|---|---|
| 客户端运行时 | Node + pnpm | PySide6 打包版，不能要求用户装 Node |
| 包体积 | 面向小代码包、单次上传、无断点续传 | 角色包实测 93MB 级，需要 Range 续传 |
| 市场元数据 | packument 只有 description/keywords/readme | 截图、中文名、认证、封禁、替代、平台矩阵、Core 兼容都要有 |
| 依赖模型 | `node_modules` | `requires` 是 mod id，feature 是 Python 文件 |
| 信任根 | 包名可抢注/转移 | 必须 pin `sha256`，索引才是信任根 |

结论：**npm 可以当 artifact 存放处，不能当市场模型。** v1 的 `versions[].urls` 是有序列表，未来若接入 npm，只需把 `https://registry.npmmirror.com/<pkg>/-/<pkg>-<version>.tgz` 作为其中一个 URL，并给客户端补 `.tgz` + `package/` 前缀支持；索引契约不变。当前没有任何 Node/npm 依赖。

## 2. 架构

市场是 Core 基础设施，不是 mod：市场被停用或损坏时，用户仍需要它来恢复其他扩展。实现分三层：

```text
petcore/net/market.py        HTTP：索引 GET（ETag/304）、artifact 下载（Range/续传）
petcore/market/              纯逻辑：模型、源与缓存、校验、下载编排、安装/回滚、CLI
petcore/ui/settings/page_market.py   设置页「插件市场」
```

- 联网库只出现在 `net/`，`market/` 通过 `IndexFetcher` / `ArtifactFetcher` 协议拿传输能力；`tests/test_petcore_architecture.py` 守住。
- 索引与下载在后台线程；GUI 线程只接收信号并调用 `host.reload_mods()`。
- 入口在「扩展管理」左侧导航：`扩展管理 → 插件市场 → 基础扩展 → 加装扩展`。

## 3. 索引契约（`index.json`，schema_version 1）

```json
{
  "schema_version": 1,
  "generated_at": "2026-10-22T12:00:00Z",
  "packages": [
    {
      "id": "fun.divination",
      "kind": "feature",
      "name": "Divination",
      "name_zh": "每日一签",
      "summary_zh": "一天一支签，可语音播报",
      "author": "MerZlin",
      "homepage": "https://github.com/MerZlin/dsh-pet-extensions",
      "categories": ["fun"],
      "latest": "1.0.0",
      "api_version": "1",
      "core_requires": ">=0.1.0,<1.0.0",
      "execution": "host-only",
      "capabilities": ["presentation"],
      "platforms": ["win32", "darwin", "linux"],
      "certified": true,
      "blocked": false,
      "blocked_reason": "",
      "superseded_by": null,
      "screenshots": [],
      "versions": [
        {
          "version": "1.0.0",
          "released_at": "2026-10-22",
          "size": 13245,
          "sha256": "64 个小写十六进制字符",
          "urls": [
            "https://github.com/MerZlin/dsh-pet-extensions/releases/download/fun.divination-1.0.0/fun.divination-1.0.0.zip"
          ],
          "changelog": "首个版本",
          "yanked": false
        }
      ]
    }
  ]
}
```

规则：

- `id` 必须通过 `mods/manifest.py::is_valid_id`；命中 `CORE_FEATURE_IDS` 的条目会被丢弃并记诊断。
- `latest` 必须指向一个存在且未 `yanked` 的版本；指向不存在的版本 → 整个包丢弃；版本条目存在但字段不合法 → 保留包但显示「没有可安装版本」。
- `size` 与 `sha256` 必填；缺任一项的版本不可安装。
- `urls` 至少一个，只接受 `http/https/file`；按顺序尝试。
- `platforms` 接受 `win32/darwin/linux`，也接受 `windows/macos/linux` 别名；空列表表示不限制。
- `blocked=true` 阻止新安装，**不自动删除已安装包**；`superseded_by` 只提示替代包。
- 未知字段只产生诊断，不因索引新增字段让旧客户端整体失败。

JSON Schema 见 [`market-index.schema.json`](market-index.schema.json)。

## 4. 索引源与缓存

默认源按顺序：

1. `https://raw.githubusercontent.com/MerZlin/dsh-pet-extensions/main/index.json`
2. `https://api.github.com/repos/MerZlin/dsh-pet-extensions/contents/index.json`（Contents API，base64 解码）

设置项 `market_index_url` 非空时排第一，官方源仍作为后备。缓存位置：

```text
<data>/market/index-cache.json
<data>/market/index-cache.meta.json   # url / etag / fetched_at
```

- 内存 TTL 5 分钟；`ETag` 命中 304 时复用磁盘缓存。
- 所有源失败 → 使用磁盘缓存并显示「索引可能过期」；没有缓存 → 空列表 + 明确错误，不崩溃。
- 索引请求超时 8 秒；artifact 连接超时 15 秒，分块读取、可取消。
- 网络请求走 `petcore/net/http.py` 的「系统代理优先、传输失败改直连」，与 Core 其他联网能力同口径。

## 5. 下载与安装流水线

```text
读索引 → 选择版本（Core/平台/API/阻止状态）
→ 下载到 market/downloads/<id>-<version>.zip.part
   · market_download_connections=1：逐 URL 单流（Range 续传，旧路径）
   · 2..8：预检 Range → 分段（≤8 MiB/段）→ 每段独立续传 → 按序装配
   · market_download_sources=auto：多 URL 时先 TTFB 竞速；ordered：按索引顺序
→ 校验 size + SHA-256
→ 解包到 market/staging（复用 mods/install.py 的 zip-slip 防护）
→ 读 mod.json，交叉校验 id / kind / version
→ 备份现有 mods/<id> 到 market/backup/<id>/<version>（硬链接优先）
→ mods.install.install(..., force=True) 原子替换
→ 写 market-lock.json（原子替换）
```

分段细节见 [`PETCORE-LOG.md`](PETCORE-LOG.md) L143：200（服务端忽略 Range）只回退单流一次；
206 的 `Content-Range` 起止/总长必须与索引一致；429/`Retry-After` 不重试；取消每 256 KiB 检查并保留
可续传段；进度 `total` 恒为索引大小、`done` 单调、末值 `(total,total)`。设置页「索引与下载」
可调连接数与源策略；单流分支的 `.part`/`download.json` 语义保持不变。

- 任一步失败：删除无用的 `.part`/staging，保留旧版本，错误里带阶段与镜像尝试记录。
- `market-lock.json` 只记录市场来源，不复制「当前安装了什么」（真值仍是 `mods/<id>/mod.json`）：

```json
{
  "schema": 1,
  "packages": {
    "fun.divination": {
      "version": "1.0.0",
      "sha256": "…",
      "source_url": "https://…",
      "installed_at": "2026-10-22T12:00:00+00:00",
      "backup": "market/backup/fun.divination/0.9.0"
    }
  }
}
```

- 手动 zip 安装的包没有 lock 记录，市场页标「已安装（非市场来源）」：可以更新，但没有回滚入口。
- 备份保留最近 2 份；回滚前先把当前版本挪到 staging，装完再放入备份树，避免「恢复源被清理」。
- feature 包仍受 `allow_feature_mods` 约束：市场安装不会自动授信，未开启时安装按钮置灰并说明原因。

## 6. 生效语义

| 包类型 | 安装后行为 | 文案 |
|---|---|---|
| content（角色/音效/主题/台词） | 调 `host.reload_mods()` | 「已安装并生效」 |
| feature，`execution=host-only` | 调 `host.reload_mods()`（先停旧 feature 再重建） | 「已更新并重新加载」 |
| feature，`execution=worker` 或无法确认 | 只写盘 | 「下载完成，重启后生效」 |

`reload_mods()` 会中断正在进行的 mod 任务；这是 host-only 重载的已知代价。

## 7. CLI

```bash
python -m petcore market list [--json]
python -m petcore market show <id> [--json]
python -m petcore market install <id> [--version V] [--allow-feature] [--json]
python -m petcore market update [<id>] [--allow-feature] [--json]
python -m petcore market rollback <id> [--json]
python -m petcore market doctor
```

全局选项（写在 `market` 之后、子命令之前）：`--root <data>`, `--index-url <url>`, `--offline`。

状态值：`available` / `installed` / `update-available` / `incompatible` / `unsupported` / `blocked` / `superseded`。

## 8. 信任、签名与治理（当前边界）

- **完整性边界 = 索引里的 `sha256` + HTTPS + 对索引仓库的控制权**。索引被篡改等价于市场被接管。
- **没有数字签名**：项目当前没有 Ed25519 依赖。不手写密码学实现；需要作者私钥签名时再引入 `cryptography`，届时索引增加 `public_key_id` / `signature` 并保持向后兼容。文档与 UI 都不许写成「已签名」。
- `certified` 只作展示与筛选；`blocked` 阻止新安装。举报、评论、GitHub 登录、官方认证申请均为后续工作。
- 下载/安装日志不记录任何密钥；`.part`、staging、lock 都只落在数据目录。

## 9. 测试与验收

```bash
QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest tests/test_petcore_market*.py -q
QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest tests/test_petcore_*.py tests/test_architecture.py -q
.venv/Scripts/ruff check pet/ tests/ petcore/
.venv/Scripts/python -m petcore --check
```

端到端用例使用真实 `http.server` + 真实 `urllib` + 真实 `mods.install`，只把地址指向 localhost；覆盖 ETag/304、断点续传、多镜像回退、哈希失败、manifest 版本不符、阻止包、feature 闸门、更新与回滚、离线缓存。

## 10. 明确不做（v1）

- npm / Node / `.tgz`。
- 数字签名与公钥轮换。
- CDN/OSS 自动同步、评论、评分、举报 Issue 自动化。
- 任意 Python 模块热卸载、worker 运行时改造。
- 远程删除已安装包（`blocked` 只阻止新安装）。

## 11. 与既有规划的关系

[`plugin-phase-04-updates/PLUGIN-UPDATE-PROTOCOL.md`](plugin-phase-04-updates/PLUGIN-UPDATE-PROTOCOL.md) 与本文件描述同一条流水线；petcore 的落地契约以本文件为准，旧文保留为设计历史。[`plugin-phase-05-distribution/`](plugin-phase-05-distribution/) 的「GitHub Release / CDN / 本地源」由 `versions[].urls` 的多源列表承接，后续加 CDN 只需改索引，不需要改客户端。

官方索引仓库建议独立为 `MerZlin/dsh-pet-extensions`，只存元数据：

```text
index.json
market-index.schema.json
README.md
CONTRIBUTING.md
.github/workflows/validate.yml
```


## 2026-10-24 后续分发交接（尚未实施）

A–E 提醒域已完成，下一阶段按[分发准备详细计划](superpowers/plans/2026-10-24-petcore-p4c3-distribution.md)实施。该计划允许继任模型按不重叠写集并行网络、解包与候选构建，公共接口和集成由主模型负责。当前默认源、Range行为、manifest交叉校验和解包预算仍以现有代码为准；不能把计划中的HTTPS逐跳校验、预算、根清单或候选包标成已落地。生产源切换和公开Release需候选验收及用户另行确认。

## 2026-10-24 D1/T01：跨模块契约已冻结

T01 只冻结契约，不改变默认源与现有行为：

| 契约 | 位置 | 状态 |
|---|---|---|
| 预算 `ArchiveLimits` / `BudgetExceeded` / `MARKET_ARCHIVE_LIMITS`（64MiB / 256MiB / 10000） | `petcore/contracts/archive.py` | 已落地，仅市场通道使用 |
| 传输 `TransferSpec` / `TransferReport` / `ResumePolicy` | `petcore/contracts/http.py` | 已落地，HTTP 默认只允许 https |
| 来源 `PackageOrigin` / `writes_trusted_marker()` | `petcore/contracts/market.py` | 已落地；市场与本地包内 `.trusted` 被剔除 |
| 下载入口 `fetch_artifact(...)` | `petcore/net/market_transport.py` | 签名已生效，逐跳与读流预算由 T02 加固 |
| 安装入口 `install_package(..., origin=)` | `petcore/market/install.py` | 已生效；授权/事务加固由 T04 完成 |

逐跳校验、Range 语义修正、解包预算、根索引与候选 ZIP 仍属 T02–T09，**不得**按本节声称已实现。默认源切换与公开 Release 未发生。

## 2026-10-24 D1/T06：根索引与离线链路

| 项 | 状态 |
|---|---|
| 根 `extensions-index.json` | 已提交：四包全部 `blocked=true`（理由「待发布：候选产物尚未公开」），版本带真实 size/sha256 与计划 Release URL |
| `--index-url file://…` | 可用；读取走 `net/market.py::fetch_index_file`，上限 8 MiB，**不回落**公网源 |
| `file:` artifact | `fetch_artifact` 直接拷贝并校验压缩预算 |
| 市场页销毁 | 运行中的索引 `QThread` 由模块级集合持有到 `finished`；销毁后 `refresh()` 不启动、迟到结果丢弃 |
| 未做 | Release 上传、`RELEASE_REPO/TAG` 确认、公开索引生成（均属 D2） |
