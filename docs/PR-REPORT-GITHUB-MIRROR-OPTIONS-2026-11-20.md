# GitHub 公益加速镜像与自定义前缀选项 PR 报告

> **基线**：`f62b51aa`（`docs(petcore): 对齐 L200 索引行的文件行数`，工作区保留既有 L201/feiyu 未提交改动）
> **分支**：当前工作分支　**日期**：2026-11-20
> **范围**：18 个实现文件（含 1 个新增）、9 个测试文件（含 1 个新增）、6 份文档（含本报告）
> **关联**：[`PETCORE-LOG.md`](PETCORE-LOG.md)（`L202`）、[`PETCORE-SPEC.md`](PETCORE-SPEC.md)（§30）、[`SETTINGS-CHANGE-GATES.md`](SETTINGS-CHANGE-GATES.md)、[`PR-REPORT-STORAGE-THANKS-PACKS-2026-11-01.md`](PR-REPORT-STORAGE-THANKS-PACKS-2026-11-01.md)

## 一、核心特性

在不改变默认行为、不影响任何既有可用下载方式的前提下，将 `gh-proxy.com` 等常用公益 GitHub 加速镜像（`gh-proxy.com`、`ghfast.top`、`ghproxy.net`、`ghproxy.cc`、`ghproxy-auto` 轮询）以及一个用户可配置的自定义 HTTPS 镜像前缀（`custom`）接入 `petcore` 全局设置 `pack_mirror_policy`（「常规 → 数据 → 下载镜像」+「自定义镜像前缀」）。

| # | 能力 | 说明 |
|---|---|---|
| 1 | 零破坏保留既有路径 | 默认值仍为 `auto`，原有的 `auto` / `mirror`（jsDelivr 分片）/ `github`（只用 GitHub）执行路径、主机白名单与候选地址逐字节不变 |
| 2 | 公益站点 + 轮询 + 自定义前缀 | 新增 `gh-proxy` / `ghfast` / `ghproxy-net` / `ghproxy-cc` / `ghproxy-auto` / `custom`，并新增全局键 `pack_mirror_custom_url`（默认 `""`） |
| 3 | 全链路自动兜底 | 所有代理策略仅把加速 URL 置前，原始官方 URL 一律保留在后方；超时、HTTP 异常、非法内容或 SHA-256 不符时自动删临时文件并回退原地址 |
| 4 | 一处设置全站复用 | 统一覆盖：动画素材包、aboutme curated 角色包、Core 更新安装包、插件市场包体与 `url_install.fetch_tree` 精灵目录（同步修复 `settings_schema.py` 旧枚举漂移） |

**红线 / 不变量**：
1. 更新清单（`UpdateService.check`）与插件市场索引（`MarketSource`）**绝不走公益代理**，防止恶意反代同时篡改下载地址与 SHA-256；
2. 代理主机只在启用代理策略的当次下载临时进入允许主机集，默认/既有策略白名单零放宽；
3. 无固定 SHA-256 锚定的任意 ZIP（`url_install.fetch_zip`）不做自动代理改写；
4. 所有下载落盘前均通过 `size` + `sha256`（或树目录 JSON/WebP 魔数）核验；
5. `urllib.parse` 仅留在 `petcore/net/`（守住 `test_no_network_stack_in_core`）。

## 二、修改文件说明

> 注：工作区基线包含尚未提交的 L201 与 feiyu 文档/代码改动；下表 `git diff --numstat`（相对 `f62b51aa`）在共享文件上反映工作区累计行数，并在「改动意图」列明确标出本批职责。

### 实现

| 文件 | 增删（相对 `f62b51aa`） | 改动意图 |
|---|---|---|
| `petcore/net/github_mirrors.py` | `[NEW]` 292 行 | 单一真值：`GitHubProxyMirror`、`ALL_MIRROR_POLICIES`、`MIRROR_POLICY_OPTIONS`、`normalize_policy()`、`normalize_custom_prefix()`、`proxy_hosts_for_policy()`、`wrap_github_url()`、`expand_github_urls()`、`read_mirror_config()` |
| `petcore/config.py` | +44 / −6（含 L201） | `GLOBAL_CONFIG_KEYS` 与 `DEFAULTS` 新增 `pack_mirror_custom_url`；`_normalize()` 改调 `normalize_policy` / `normalize_custom_prefix` |
| `petcore/settings_schema.py` | +7 / −1 | 修复 `pack_mirror_policy` 过时枚举为 `ALL_MIRROR_POLICIES`；补录 `pack_mirror_custom_url` 中文标签 |
| `petcore/net/packs.py` | +14 / −6 | `download_pack(..., custom_mirror_prefix="")` 用 `expand_github_urls` 展开单文件源；`DigestMismatch` 同步清理分段侧车后回退；`DEFAULT_PACK_HOSTS` 不放宽 |
| `petcore/ui/settings/pack_download.py` | +21 / −9 | `fetch_first_index` 按策略展开索引候选（解析失败不落缓存并回退）；`PackJob` / `PackRow.start_download` 通过 `read_mirror_config` 透传配置；优化失败提示 |
| `petcore/net/aboutme_packs.py` | +37 / −15 | `download_pack` 逐文件展开候选地址并在 HTTP/截断/大小/SHA-256 异常时自动回退直连 |
| `petcore/ui/settings/page_pets.py` | +22 / −2 | `_AboutmeDownloadJob` 与 `PetsPage._start_download` 读取并透传镜像配置 |
| `petcore/update/service.py` | +65 / −13 | `check()` 保持官方只读；`download()` 先核验原地址在 `TRUSTED_HOSTS` 且具备 64 位 `sha256` 后再临时放行代理主机、检查跳转主机并回退 |
| `petcore/ui/app.py` | +8 / −1 | `CoreHost.download_update()` 提交下载任务时传入全局 `pack_mirror_policy` 与 `pack_mirror_custom_url` |
| `petcore/cli.py` | +8 / −1 | `petcore update download` 子命令读取并传入全局镜像设置 |
| `petcore/market/download.py` | +28 / −2 | `download_artifact(..., github_mirror, custom_mirror_prefix)` 在原有 `_ranked_urls` 之后按需扩展代理地址与 `TransferSpec.allowed_hosts` |
| `petcore/market/install.py` | +4 / −0 | `install_package()` 透传 `github_mirror` 与 `custom_mirror_prefix` |
| `petcore/market/cli.py` | +7 / −0 | `petcore market install` 从 `Config.load(root)` 读取并透传镜像配置 |
| `petcore/ui/settings/page_market.py` | +12 / −0 | `_InstallJob` 与 `MarketPage.install_selected()` 读取并透传镜像配置 |
| `petcore/mods/url_install.py` | +339 / −62（含 L201） | 新增 `_fetch_tree_resource` / JSON dict / `RIFF....WEBP` 校验；`fetch_tree()` 支持代理回退，`fetch_zip()` 保持不改写 |
| `petcore/ui/settings/page_mods.py` | +127 / −17（含 L201） | `_UrlPreviewJob` 与 `ModsPage._install_from_url()` 透传镜像配置给 `fetch_tree()` |
| `petcore/ui/settings/pages_core.py` | +113 / −8 | 「下载镜像」改用 `MIRROR_POLICY_OPTIONS` + `expand=True`；新增「自定义镜像前缀」输入行、校验提示、`dirty()`/`confirm_save()`/`discard_changes()` 完整联动与搜索关键词 |
| `petcore/ui/settings/style.py` | +6 / −2 | `#customMirrorPrefix` 接入标准 32 px 输入框、蓝色焦点边框与禁用态底色/文字 token |

### 测试与文档

| 文件 | 增删（相对 `f62b51aa`） | 覆盖内容 |
|---|---|---|
| `tests/test_petcore_github_mirrors.py` | `[NEW]` 173 行 | 策略枚举与选项一致性、归一化、自定义 HTTPS 前缀严格清洗、安全路径白名单、API/签名 CDN 拒收、轮询与回退 |
| `tests/test_petcore_config_scope.py` | +19 / −0 | 新增镜像策略归一化、`pack_mirror_custom_url` 全局作用域与 `ScopedConfig` 隔离 |
| `tests/test_petcore_settings_cli.py` | +24 / −0 | `settings_schema` 枚举对齐与 CLI `set pack_mirror_policy` / `pack_mirror_custom_url` 往返 |
| `tests/test_petcore_pack_download.py` | +161 / −0 | 代理优先且跳过 `api.github.com` 解析、摘要不符自动回退 GitHub 直链与 jsDelivr 分片、`ghproxy-auto` 轮询、`custom` 前缀 |
| `tests/test_petcore_settings_assets_links.py` | +113 / −4 | `mirror_combo` 全项验证、自定义输入启用/提示/保存/回滚/保留文本、720 px `SettingsDialog` 几何安全、`fetch_first_index` 坏载荷不落缓存回退 |
| `tests/test_petcore_sprite_pack.py` | +59 / −0 | `aboutme_packs.download_pack` 代理优先与摘要不符/异常回退直连 |
| `tests/test_petcore_update.py` | +62 / −0 | `UpdateService.download` 代理与不可信重定向拦截回退；`UpdateService.check()` 永不走代理 |
| `tests/test_petcore_market_install.py` | +47 / −0 | `install_package` 扩展 `allowed_hosts` 与失败回退原地址 |
| `tests/test_petcore_url_install.py` | +293 / −0（含 L201） | `fetch_tree` 代理返回 HTML/坏 WebP 自动回退直连；`fetch_zip` 保持不改写 |
| `docs/PETCORE-LOG.md` / `PETCORE-LOG-INDEX.md` / `PETCORE-SPEC.md` / `SETTINGS-CHANGE-GATES.md` / `INDEX.md` | +185 / −3（含 L201/feiyu） | 登记 `L202`、更新设置契约与 `SPEC §30`、登记本报告 |
| `docs/PR-REPORT-GITHUB-MIRROR-OPTIONS-2026-11-20.md` | `[NEW]` | 本交付报告 |

### 未改动（故意不动）

- `pet/updater.py`：遗留单文件壳无全局下载镜像设置项，按确认范围不改动。
- `petcore/market/source.py`：市场索引拉取不经第三方反代，防止索引被篡改注入恶意代码。
- `petcore/net/packs.py::DEFAULT_PACK_HOSTS` 与 `petcore/update/service.py::TRUSTED_HOSTS`：静态信任锚保持纯官方/jsDelivr，不将公益反代写入静态白名单。

## 三、实现要点

1. **单一真值 + 零重复**：所有镜像列表、UI 显示文案、策略归一化、自定义前缀合法性校验与 URL 包装逻辑集中于 `petcore/net/github_mirrors.py`。`config.py`、`settings_schema.py`、5 条下载链路与 `GeneralPage` 共用同一份常量与函数，消除此前 `settings_schema.py`（含历史 `jsdelivr`/`gitee`）与 `config.py` 的枚举漂移。
2. **自定义前缀输入安全清洗**：`normalize_custom_prefix()` 仅允许 `https://`、拒绝用户名/密码凭据、拒绝 `?`/`#`、拒绝空白/控制字符/`..` 路径与非法端口、总长限 512 字符并统一输出尾 `/` 规范前缀。
3. **跨链行为对齐**：
   - `packs.py` 对代理地址跳过 `resolve_github_asset()`（`release_parts` 只认 `github.com`）并用代理 URL 作分段 `state_url`，避免与官方直连分片混写；
   - `market/download.py` 先在原地址上完成 `_ranked_urls()` 竞速再扩展代理 URL，防止代理地址被 `GITHUB_ARTIFACT_HOSTS` 预检拦下或被竞速重排覆盖用户显式选择；
   - `update/service.py` 先验证清单原地址属于 `TRUSTED_HOSTS` 且资产存在 64 位 `sha256`，仅在下载阶段临时扩展 `allowed_hosts`，`check()` 全程隔离；
   - `pages_core.py` 将「下载镜像」改为 `expand=True` 并精简下拉文案，使 `SettingsDialog._fit_combo_widths()` 计算出的 `mirror_combo.minimumWidth()` 落在 720 px 窄窗卡片安全宽度内（本页横向滚动条最大值从 130 px 降至 50 px）。

## 四、性能分析

### 4.1 本地纯函数开销实测

**方法（可复现）**：`.\.venv\Scripts\python.exe -c "import time; from petcore.net.github_mirrors import expand_github_urls, normalize_custom_prefix; u=('https://github.com/MerZlin/Animations/releases/download/packs-v1/shenshen-1.zip',); N=10000; ..."`
环境：Windows / Python 3.12.10，单样本 `N = 10,000` 次调用取均值。

| 指标 | 实测 | 归属 |
|---|---|---|
| `expand_github_urls(..., "auto")` | **1.036 µs/call** | 稳态热路径（直接返回原元组） |
| `expand_github_urls(..., "gh-proxy")` | **4.667 µs/call** | 新增路径（仅发起 GitHub 下载时触发 1 次） |
| `expand_github_urls(..., "ghproxy-auto")` | **14.486 µs/call** | 新增路径（展开 4 个公益前缀 + 原 URL） |
| `normalize_custom_prefix("https://gh-proxy.com/sub")` | **7.659 µs/call** | 设置输入 / 配置归一化（事件驱动） |
| `normalize_custom_prefix("http://bad.example")` | **3.635 µs/call** | 设置输入 / 非法拒绝路径 |

### 4.2 本机真实网络首字节对比（单次探针样本）

**方法（可复现）**：在关闭/保留系统代理两条路由下对 `MerZlin/Animations` 的 Release 索引（3,697 B）、raw 索引（3,609 B）与 126 MiB 素材包前 1 KiB `Range: bytes=0-1023` 实测（每节点每路由各 1 次请求）：

| 源 | `with_system_proxy`（TTFB / 状态） | `direct_no_proxy`（TTFB / 状态） |
|---|---|---|
| 官方 GitHub 直链 | 1155.9–2222.3 ms（200 / 206） | 失败（`URLError`，连接被拒/不可达） |
| `gh-proxy.com` | **1126.2–1232.7 ms**（200 / 206 `bytes 0-1023/132036744`） | **927.8–989.9 ms**（200 / 206 `bytes 0-1023/132036744`） |
| `ghfast.top` | 1276.8–1548.8 ms（200 / 206） | 1299.8–1612.8 ms（200 / 206） |
| `ghproxy.net` | 8105.4–9468.8 ms（200 / 206） | 1327.2–7201.0 ms（200 / 206） |
| `ghproxy.cc` | 1036.2–1281.0 ms 失败（`URLError`，证书主机名不匹配） | 1042.6–1299.8 ms 失败（`URLError`，证书主机名不匹配） |

**结论**：
1. **稳态开销**：默认 `auto` 模式下每次下载仅增加约 `1.0 µs` 的策略判断与元组直返，无定时器、无轮询、无常驻线程，稳态 CPU 与下载吞吐零变化。
2. **新增路径成本与频率**：仅在用户点击下载或编辑「自定义镜像前缀」时触发，`expand_github_urls` 与 `normalize_custom_prefix` 单次耗时 `1–14.5 µs`，相较 0.9–9.5 s 级别的网络往返可忽略；无额外预检握手。
3. **系统调用 / 网络 / 磁盘 / 线程**：零新增常驻线程或背景探测请求；只有选中公益/自定义镜像且实际发起下载时才向对应镜像域发 HTTP(S) 请求；配置仅在既有全局 `config.json` 多 1 个短字符串键 `pack_mirror_custom_url`。
4. **内存与缓存**：模块级常量仅为 4 条小型 `GitHubProxyMirror` dataclass 实例（< 2 KiB）；不新增磁盘缓存文件（失败/坏索引仍严格沿用原有「不落缓存」口径）。

## 五、实机运行记录

1. **真实网络连通性探针（`probe_github_mirrors.py`，非 mock、非测试替身）**：
   - `direct_no_proxy` 路由下直连官方 GitHub 失败（`URLError`，约 2.0 s 返回连接拒绝）；同期 `gh-proxy.com` 在**无系统代理直连**下 927.8–989.9 ms 返回有效 JSON（3,697 B / 3,609 B）且支持 `206 Partial Content`（`bytes 0-1023/132036744`），验证了用户反馈「直连 `gh-proxy.com` 更快」的真实前提。
   - **边界与失败路径实机观察**：`ghproxy.cc` 在两条路由下均因服务端 TLS 证书主机名与 `ghproxy.cc` 不匹配触发 `SSLCertVerificationError`（1036–1300 ms），被 `urllib` 严格 TLS 校验证明能够安全拦截并在轮询/自动回退链路中被跳过，不会降级关闭校验。
2. **设置页真实 GUI 渲染确认**（`capture_mirror_ui.py` 使用 Windows 原生 Qt 字体渲染）：
   - `1100×760` 亮色（`auto`）：`下载镜像` 下拉清晰，下方 `自定义镜像前缀` 呈现标准 32 px 禁用灰底与占位提示；
   - `1100×760` 深色（`custom` + 非法 `http://` 输入）：`#customMirrorPrefix` 启用并应用深色输入背景（`#242429`）与边框（`#3f3f48`），下方自动展开两行内联校验提示；
   - `720×600` 亮色（`custom`）：两行控件完整落入卡片、文案正常换行、无文字裁切；既有的 `角色资源包` 行仍使 `GeneralPage` 卡片略宽于 720 px 视口（横向滚动条最大值已由 130 px 降至 50 px）。
3. **无法自动验证的能力**：
   - 完整 126 MiB `shenshen-1.zip` 经各公益站点全量端到端拉取与长稳限速表现未跑实包下载（探针仅取 3.6 KiB 索引与 1 KiB `Range: bytes=0-1023` 响应，避免大流量依赖公益站配额）；
   - macOS 与 Linux 实机 GUI 未在本 Windows 宿主机运行。

## 六、测试与验证

| 门 | 命令 | 结果 |
|---|---|---|
| 静态检查 | `.\.venv\Scripts\python.exe -m ruff check petcore tests` | `All checks passed!`（`exit 0`） |
| 格式检查 | `git diff --check` | 无输出（`exit 0`） |
| 聚焦套件 | `$env:NO_PROXY="127.0.0.1,localhost"; $env:QT_QPA_PLATFORM="offscreen"; .\.venv\Scripts\python.exe -m pytest -q <17 份受影响测试文件>` 与专项文档/架构/纪律门 | 17 份受影响文件 **`362 passed in 119.55s`**（`exit 0`）；专项门 `test_pr_report_discipline.py` + `test_petcore_docs.py` + `test_petcore_architecture.py` **`418 passed`**（`exit 0`） |
| 全量 `petcore` + 架构/纪律套件 | `$env:NO_PROXY="127.0.0.1,localhost"; $env:QT_QPA_PLATFORM="offscreen"; .\.venv\Scripts\python.exe -m pytest -q tests/test_petcore_*.py tests/test_architecture.py tests/test_pr_report_discipline.py` | **`3843 passed, 5 failed, 5 skipped`（`8431.47s`）**：本批改动与全部镜像/配置/UI/下载测试零失败；5 例均系宿主机环境既有失败——4× `tests/test_petcore_agent_mod.py`（检查宿主机用户目录下外部 Agent 会话路径触发 `PermissionError: WinError 5`）与 1× `tests/test_petcore_net.py::test_http_error_is_raised_without_direct_retry`（宿主机 Windows 注册表存在活跃系统代理，未改动的 `petcore/net/http.py` 对 HTTP 503 按设计换路由重试）。本批改动零触及遗留 `pet/`，故未重跑此前因服务端重启中断的全量遗留 `pet/` 用例（沿用 `L078`/`L170` 口径） |

> 注：本机存在活跃系统代理导致环回 `http://127.0.0.1:<port>` 在无 `NO_PROXY` 时被本地代理端口拦截返回 `HttpStatusError`；运行环回集成测试需设置 `NO_PROXY=127.0.0.1,localhost`。

## 七、已知限制与后续

- `ghproxy.cc` 当前证书不匹配无法使用，暂保留在列表末尾（与 2026-11-01 报告中的测点一致，在 `ghproxy-auto` 轮询与单选下均能安全跳过并回退）；用户也可随时通过 `自定义镜像前缀` 填写自建或最新可用的 `https://` 节点而无需等发版。
- 720 px 下 `GeneralPage` 既有的 `角色资源包` / `数据目录` 等行内按钮组合仍使该页有 50 px 横向滚动余量（较改前的 130 px 已大幅收窄，本刀不重构其它既有行）。
