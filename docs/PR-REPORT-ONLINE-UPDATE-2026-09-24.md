# 在线更新功能 PR 报告（2026-09-24）

> **基线**：工作树 `v4.2.1` 代码基线（未创建提交）　**日期**：2026-09-24
> **范围**：17 个本次功能相关文件（实现、测试、打包配置和文档）；保留工作区原有的 4 个插件化未跟踪文档和 `.workbuddy/`。
> **关联**：[`ONLINE-UPDATE.md`](ONLINE-UPDATE.md)、[`NETWORK-PROXY-AND-VPN-2026-09-22.md`](NETWORK-PROXY-AND-VPN-2026-09-22.md)

## 一、核心特性

新增统一在线更新链路：更新检查支持 GitHub API 与 jsDelivr 多源 manifest；结构化资产支持镜像回退、大小/SHA-256 校验；Windows 冻结版可从设置页下载并启动 Inno Setup 静默安装器，安装器关闭旧进程后重新启动程序。设置侧栏新增“更新”页，左下角显示当前版本，右键菜单发现新版本时可深链进入该页。

**红线 / 不变量**：源码运行和非 Windows/非冻结运行方式不启动未知下载文件；下载失败或校验失败不覆盖当前安装；不新增持久设置键；旧版 `assets: {name: url}` manifest 仍可解析。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---:|---|
| `pet/updater.py` | +219 / −38 | 增加结构化 manifest、可信 HTTPS、镜像回退、下载校验、安装器启动和缓存目录。 |
| `pet/update_settings.py` | +220 / −0 | 新增“更新”设置页、异步检查、下载进度、人工下载兜底和安装器启动。 |
| `pet/modern_settings_dialog.py` | +27 / −1 | 注册更新页、版本号页脚和深链选择。 |
| `pet/settings_widgets.py` | +1 / −0 | 将“更新”加入设置侧栏。 |
| `pet/settings_theme_qss.py` | +8 / −0 | 为版本页脚、状态卡片、说明卡片和进度条补充主题样式。 |
| `pet/app.py` | +27 / −10 | 更新气泡、右键菜单和独立设置进程支持直接打开“更新”页。 |
| `pet/__main__.py` | +18 / −3 | 增加 `--settings-page` 深链参数。 |
| `packaging/dsh-pet.iss` | +5 / −1 | 允许安装器关闭占用进程，并在静默更新结束后重新启动程序。 |
| `docs/ONLINE-UPDATE.md` | +68 / −0 | 固化 manifest、镜像、校验、发布和回滚合同。 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---:|---|
| `tests/test_updater.py` | +89 / −0 | 结构化资产、镜像/摘要/大小解析、可信源拒绝、安装包选择。 |
| `tests/test_update_feature.py` | +78 / −0 | 更新页注册、版本显示、深链、无持久化写入和设置进程转发。 |
| `tests/test_desktop_pet_features.py`、`tests/test_menu_layout.py`、`tests/test_settings_interaction_tabs.py` | +7 / −6 | 更新侧栏加入后既有设置导航断言同步。 |

## 三、实现要点

更新页不直接写 `Config`：它是命令和状态页，而不是持久偏好。网络与下载在线程中执行，通过 Qt signal 回到 GUI 线程更新控件。安装器在当前进程外 detached 启动，当前窗口关闭后由 Inno Setup 负责关闭占用进程和重启。当前默认兼容旧 manifest；新发布应使用结构化 `urls/size/sha256` 资产，以便在 GitHub 不可达时配置真实直链镜像。

## 四、性能分析

**方法（可复现）**：`$env:QT_QPA_PLATFORM='offscreen'; python -m pytest -q tests/test_updater.py tests/test_update_feature.py`；Windows 工作树、Python 3、Qt offscreen，13 个用例。另以 `python -m pytest -q` 做最终全量验证。网络下载测试使用本地测试服务器/夹具，不计入稳态启动路径。

| 指标 | 实测 | 归属 |
|---|---:|---|
| 更新相关聚焦测试 | 13 passed / 0 failed / 1.04 s（最终更新专测） | 新增路径验证 |
| 更新页/设置交互相关测试 | 216 passed / 0 failed / 56.14 s | 设置 UI 与进程边界 |
| 常驻线程 | 0 个新增（未点击检查时） | 稳态 |
| 网络请求 | 0 个新增（未点击检查时）；点击检查最多按配置源顺序查询 | 新增路径 |
| 磁盘写入 | 0 个新增（未点击下载时）；下载时写入配置目录 `updates/*.part` 与最终安装包 | 新增路径 |

结论：稳态不启动更新线程、不发网络请求、不新增设置项；只有用户点击检查/下载或应用主动提示时才触发网络、临时文件和后台线程。正式安装包下载耗时取决于网络与安装包大小，当前工作树没有发布产物，不能伪造该数字。

## 五、实机运行记录

### 5.1 已执行

- `QT_QPA_PLATFORM=offscreen; python -m pytest -q tests/test_updater.py tests/test_update_feature.py` → `13 passed in 1.04s`。
- 相关设置/进程测试 → `216 passed in 56.14s`。
- `python -m ruff check ...` → `All checks passed!`。
- 真实工作树已确认当前代码版本为 `4.2.1`，Inno Setup 配置包含 `CloseApplications=yes`、`/CLOSEAPPLICATIONS` 和静默安装后的 `[Run]` 重启路径。

### 5.2 暂未执行的实机步骤

本机当前没有可用于本次变更的全新 Windows 冻结安装包，因此没有宣称“已实机完成安装替换和重启”。本机探针显示 `ISCC.exe not found`，因此需在生成 `dist-onedir/*-setup.exe` 后补跑：启动旧包 → 更新页检查 → 下载 → 安装器关闭旧进程 → 新包重新启动。该限制不会影响解析和 UI 测试，但仍是发布前门禁。

### 5.3 边界观察

源码运行时 `can_auto_install()` 为假，更新页不会启用自动安装按钮而提供下载页；不可信下载域名会在 `download_asset()` 前被拒绝。Windows 冻结版与实际 Inno 安装器重启仍需产物级实测。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `python -m ruff check pet tests scripts` | `All checks passed!`。 |
| 聚焦 | `python -m pytest -q tests/test_updater.py tests/test_update_feature.py` | 13 passed，0.81 s。 |
| 相关设置族 | `python -m pytest -q tests/test_updater.py tests/test_update_feature.py tests/test_desktop_pet_features.py tests/test_menu_layout.py tests/test_settings_interaction_tabs.py tests/test_settings_process_isolation.py` | 216 passed，56.14 s。 |
| 受影响时序族满载 3 遍 | 不适用；本次没有新增真实线程/QLocal 时序测试。 | — |
| 全量 | `python -m pytest -q` | `2957 passed, 11 skipped, 14 warnings in 223.38s (0:03:43)`。 |
| 差异格式 | `git diff --check` | 通过；仅有 Git 的 LF→CRLF 提示，无空白错误。 |

## 七、已知限制与后续

1. 当前仓库的旧 `update.json` 仍是历史平面 URL 格式；下一次发布必须按 [`ONLINE-UPDATE.md`](ONLINE-UPDATE.md) 生成结构化资产并填入真实二进制镜像。代码已兼容旧格式，但旧格式不能提供摘要校验。
2. 自动安装仅面向 Windows 冻结版；源码运行、便携包、非 Windows 保留人工下载路径。
3. Inno Setup 产物级重启和 GitHub 受限网络下的真实镜像下载必须在发布机补验。

## 八、风险与回滚

更新功能不引入配置迁移。回滚代码即可移除新设置页和更新入口；用户配置目录中的 `updates/` 临时/安装包文件不会被自动当作当前安装覆盖，可人工清理。若发布 manifest 错误，先从静态源撤下错误版本或修正摘要/URL，客户端在校验失败时不会启动安装器。
