# 视图回切 / 数据与缓存目录可搬 / 致谢独立页 / 素材包仓库与应用内下载

> **基线**：`590fbecd`（对话窗深色模式 + `chat_view` 插件缝 + 网页 AI v2）
> **分支**：`feat/petcore-vpet-architecture`　**日期**：2026-11-01
> **范围**：30 个改动文件 + 16 个新增文件（实现 14、测试 8、文档 5、脚本 4、仓库内容 1）
> **关联**：`docs/PETCORE-LOG.md` L154、`docs/SETTINGS-CHANGE-GATES.md`（2026-11-01 条）、
> [`PETCORE-SPEC.md`](PETCORE-SPEC.md) §30

## 一、核心特性

四条用户诉求一起落地，互相之间只用「存储位置」与「下载」两处耦合：

1. **再点当前视图按钮 = 回到 AI 对话**。此前 `set_view()` 在 `previous == key` 时整段
   no-op，用户点开「网页 AI」后只能去侧栏挑会话才能回去。
2. **数据目录 / 缓存目录可选、可搬**。数据根此前只有 `--root/--portable/--instance`
   三个 CLI 参数，桌面用户没法把几百 MiB 缓存从 C 盘挪走。现在设置页里改，自动复制、
   校验、写指针、重启生效，下次启动自动删旧目录。
3. **致谢独立成页**（与「关于与诊断」同级，排最后），补 7 条特别鸣谢（含深度求索
   DeepSeek）、作者栏加 `@炎圣人但丁`、粉丝名单刷新脚本就绪。
4. **素材包仓库 + 应用内下载**：新建 `MerZlin/Animations` 仓库（索引 + Release 资产），
   「常规」两个素材包行改为「应用内下载（进度条 + 实时网速 + 取消）/ 打开 GitHub / 夸克网盘」，
   默认落到 `<桌宠目录>/tmp`，完成后一键导入角色。

| # | 能力 | 说明 |
|---|---|---|
| 1 | 视图回切 | 内联按钮与 `»` 溢出菜单共用 `_activate_view_button()`；tooltip 补「再次点击回到 AI 对话」 |
| 2 | 数据目录可搬 | 引导指针 `location.json` 放在默认根**之外**；复制 → 校验 → 写指针 → 重启 → 删旧根 |
| 3 | 缓存目录可搬 | 接管 `cache/tts`、`market/downloads`、`updates/`、`packs/`；未拆分时路径逐字节不变 |
| 4 | 致谢页 | 侧边栏第 10 页（图标 `sparkles`），复用 `ThanksPanel`，关于页只剩「版本与诊断」 |
| 5 | 素材包下载 | 4 连接分段 → 不支持 Range 回退单流；sha256 校验；默认目录 `install_root()/tmp` |
| 6 | 共享进度面板 | `SpeedMeter` + `describe_progress` + `DownloadPanel` 由市场页与素材包行共用 |

**红线 / 不变量**

- 不写 `location.json` = 今天的行为：数据根 `%APPDATA%\dsh-pet-core`，缓存根 = 数据根，
  三者路径与拆分前逐字节一致（`tests/test_petcore_roots.py::test_everything_stays_in_the_data_root_when_split_is_off`）。
- 指针只在**校验通过后**写；校验失败/取消时旧目录原样不动。
- 缓存根**绑定数据根**：按 A 根设置的缓存根不许被 B 根取走（否则 `--check` 指向别处会把
  缓存写进别的根；第一版没绑，直接打红 12 个市场测试）。
- 素材包下载：`parse_index` 白名单之外的地址一律拒绝；摘要不符删产物并换镜像，不重试同一
  坏文件；`download_segmented` 的「200 回退单流一次」语义不变。
- 致谢数据只读：静态条目仍只从 `petcore/community/data/credits.json` 读，运行期不回写。

## 二、修改文件说明

### 实现

| 文件 | 增删 | 改动意图 |
|---|---|---|
| `petcore/storage/roots.py`（新） | +460 | 引导指针读写/解析、`resolve_roots` 优先级、`validate_target` 六条拒绝、`plan_move`/`copy_tree`（1 MiB 分块 + 进度 + 取消）/`verify_copy`、`write_journal`/`cleanup_moved_source`（删旧根前保存并回写旧根里的指针） |
| `petcore/storage/__init__.py`（新） | +45 | 包门面 |
| `petcore/paths.py` | +98 / −1 | `default_data_root`/`resolve_roots`/`pointer_path`/`set_cache_root(data_root=)`/`cache_dir(root, …)`；`data_root()` 改为读指针。**缓存根绑定数据根**是第二轮修正的核心 |
| `petcore/cli.py` | +18 / −4 | `_resolve_root` 走 `resolve_roots` 并下发缓存根；`--check` 报告加 `cache_root`；`run(..., cache_root=)` |
| `petcore/ui/app.py` | +23 / −1 | `run(cache_root=)` 设进程缓存根；启动成功后后台线程 `cleanup_moved_source` |
| `petcore/speech/cache.py` | +3 / −2 | TTS 缓存目录走 `paths.cache_dir` |
| `petcore/market/download.py` | +2 / −1 | 市场下载包目录走 `paths.cache_dir` |
| `petcore/update/service.py` | +2 / −1 | 更新包目录走 `paths.cache_dir` |
| `petcore/ui/chat/window.py` | +18 / −4 | `_activate_view_button()`；内联按钮与溢出菜单改接它；tooltip/accessibleName 补「再次点击回到 AI 对话」 |
| `petcore/ui/settings/page_thanks.py`（新） | +47 | `ThanksPage(SettingsPage)`，复用 `ThanksPanel`，`dirty()` 恒 False，`dispose()` 转调 |
| `petcore/ui/settings/page_about.py` | +0 / −29 | 摘掉「致谢」标签与 `_build_thanks_section` 死代码 |
| `petcore/ui/settings/dialog.py` | +4 | 页面注册三处（import / `_PAGE_ICONS` / `_PAGE_KEYS` / `_load_pages` 顺序） |
| `petcore/ui/settings/pages_core.py` | +295 / −20 | 数据/缓存两行（只读 `QLineEdit` + 更改位置…/打开/清空缓存 + 搬迁进度面板）、两行素材包改 `PackRow`、`pack_download_dir()`、`dispose()` 取消任务 |
| `petcore/ui/settings/roots_job.py`（新） | +168 | 搬迁守护线程任务（进度节流 10 Hz、取消、`done/failed/cancelled` 三信号） |
| `petcore/ui/settings/pack_download.py`（新） | +377 | 索引磁盘缓存（6 小时 + 坏缓存不炸）、`PackJob`（下载）、`_ImportJob`（导入）、`PackRow`（三按钮 + 进度面板 + 导入按钮） |
| `petcore/ui/download_progress.py`（新） | +204 | `format_bytes`/`SpeedMeter`/`describe_progress`/`DownloadPanel`（可关「N 连接」） |
| `petcore/ui/settings/page_market.py` | +11 / −51 | 改为共用 `download_progress`（删掉本地三份实现，`MarketPage.describe_progress` 保留为薄委托） |
| `petcore/ui/external_links.py` | +8 | `ANIMATIONS_REPO_URL` / `ANIMATIONS_RELEASE_TAG` / `PACK_INDEX_URL` |
| `petcore/net/stream_download.py`（新） | +334 | 单流 + `.part` 续传 + sha256/大小校验 + 取消保留 `.part` + `pollute=False` |
| `petcore/net/packs.py`（新） | +464 | 索引解析（schema/id/filename/https/主机白名单/64 位 sha256）、`fetch_index`、`download_pack`（分段优先，`RangesUnsupported` 同址回退单流一次，摘要不符换镜像） |
| `petcore/community/data/credits.json` | +47 | 7 条特别鸣谢 + 作者 `@炎圣人但丁` |
| `petcore/community/model.py` | +13 | `DEFAULT_SPECIAL_THANKS` / `DEFAULT_AUTHOR_ACCOUNTS` 兜底同步 |
| `petcore/ui/settings/folds.py` / `page_about_thanks.py` | +1 / −1 各 | 文档串：致谢分页 → 致谢页 |

### 脚本与仓库

| 文件 | 行数 | 用途 |
|---|---|---|
| `scripts/make_animation_pack.py`（新） | 185 | 打 ZIP（角色目录内容入包）+ size/sha256 + 合并 `packs-index.json` |
| `scripts/publish_animation_pack.ps1`（新） | 95 | 克隆/推送 Animations 仓 + `gh release create/upload` |
| `scripts/bench_pack_download.py`（新） | 133 | 1 / 4 连接各跑 N 次，输出耗时、平均/峰值速度、加速比 |
| `scripts/refresh_community_snapshot.py`（新） | 200 | 读钥匙串 SESSDATA → 拉全量粉丝 → 原子写回 `fans.json`；无凭据退出码 2，partial 不写 |
| `packaging/animations-repo/README.md`（新） | 60 | 素材包仓库的包格式/索引格式/发布流程 |

### 测试

| 文件 | 增删 | 覆盖 |
|---|---|---|
| `tests/test_petcore_chat_views.py` | +57 | 再点当前视图回 AI（内联 + 溢出菜单）、首次点击仍进入 |
| `tests/test_petcore_roots.py`（新） | +359 | 指针优先级/坏指针/缓存绑定、六条拒绝、复制+校验、取消、任务写指针与 journal、清理保指针、设置页两行 |
| `tests/test_petcore_settings_assets_links.py` | +61 / −22 | 三按钮、下载不进浏览器、外链仍对、无草稿差异、索引 URL |
| `tests/test_petcore_thanks_page.py`（新） | +75 | 页面身份/关键词、搜索命中、新鸣谢条目、`dispose` |
| `tests/test_petcore_about_page.py` | +13 / −1 | 关于页只剩「版本与诊断」，致谢由 `ThanksPage` 托管 |
| `tests/test_petcore_about_thanks.py` | +5 / −4 | 关于页保留 community 诊断段 |
| `tests/test_petcore_settings.py` | +1 / −1 | `CORE_TITLES` 加「致谢」 |
| `tests/test_petcore_community_store.py` | +2 / −1 | 兜底条目 4→11、作者账号加人 |
| `tests/test_petcore_pack_index.py`（新） | +231 | 22 组参数化：schema/id/filename/https/白名单/sha/size/部分跳过/by_id |
| `tests/test_petcore_pack_download.py`（新） | +468 | 206 分段、200 回退单流、摘要不符删产物、取消保留 `.part`、缓存命中、镜像回退、预算超限 |

### 文档

`docs/PETCORE-LOG.md`（L154）、`docs/PETCORE-LOG-INDEX.md`、`docs/PETCORE-SPEC.md`（§30）、
`docs/SETTINGS-CHANGE-GATES.md`（2026-11-01 条）、`docs/INDEX.md`、`petcore/AGENTS.md`（4 条新红线）。

## 三、实现要点

**引导指针为什么必须在外**。指针写在 `<data_root>/location.json` 里时，数据根一搬，
指针自己也跟着搬走了；下次启动只能看到默认根（一个空目录），用户的会话与配置等于消失。
所以固定放 `%APPDATA%\dsh-pet-core\location.json`（绿色版放 exe 旁边），并且
`cleanup_moved_source()` 删旧根前先把指针内容读出来，删完再写回——否则「搬走」这一步
自己会把回程路拆了。

**缓存根为什么要绑定数据根**。`paths` 是模块级的，`set_cache_root()` 一旦全局生效，
`--check` 指向另一个根（或测试里换了 `tmp_path`）就会把缓存写进上一个根。第一版就是这样
打红了 12 个市场测试。现在 `set_cache_root(path, data_root=root)` 记一对，
`cache_dir(root, …)` 只在 `root` 命中绑定根时用缓存根，否则回落 `<root>/…`。

**搬迁为什么是「复制 + 重启 + 下次启动删旧根」**。搬迁期间进程仍持有旧根的日志句柄与
`petcore.lock`，工作线程也在写配置；直接删/改名必然出现半搬状态。复制完成、校验通过后
立刻写指针并要求重启，旧根的删除放到下一次启动（那时没有句柄、且旧根里可能只有指针）。

**素材包为什么先分段再回退**。GitHub Release 资产会 302 到 `release-assets.githubusercontent.com`，
支持 Range；`net/market.download_segmented` 已经做了 `bytes=0-0` 预检 + `Content-Range` 校验 +
段级续传。只有服务端回 200（不支持 Range）或文件太小时才回退 `download_stream`，且只回退一次。

**进度显示为什么抽公共件**。市场页的 `_SpeedMeter`/`describe_progress` 是模块私有的，
素材包行再抄一份就会出现「两处文案不同步、一处有 ETA 一处没有」。抽出
`petcore/ui/download_progress.py` 后，市场页只剩薄委托，测试 `MarketPage.describe_progress`
仍可用。

**路径为什么用只读 `QLineEdit`**。第一版用 `QLabel` + 自动换行：长路径没有断点，
`QLabel.minimumSizeHint()` 撑到 516px，「数据」卡片整体 1010px，设置页出现横向滚动条
（`.scratch/batch-general.png` 第一版就是这个样子）。换成只读输入框后卡片最小宽 654px，
横向滚动消失，还能选中复制。

## 四、性能分析

### 4.1 分段下载加速（实测）

同一台机器、同一时段、同一 GitHub Release 资产（126 MiB，5675 个文件），
`scripts/bench_pack_download.py` 各跑 2 轮取中位数：

**结论先说**：第一版测出来「4 连接没有加速」，第二轮定位到真正的瓶颈是
**`github.com` 那一跳被吞**（不是 CDN、也不是引擎），修好后同一份 126 MiB 包在本机
裸连就能下完（45.9 s / 2.75 MiB·s⁻¹）。下面是两轮的完整数字与定位过程。

#### 0. 现场定位（2026-11-01 追加）

用户反馈「素材下载的本地连接不可用」。逐段测：

| 目标 | DNS | TCP | TLS | 8 秒窗口吞吐 |
|---|---|---|---|---|
| `github.com`（20.205.243.166） | 0.03 s | **超时** | — | — |
| `api.github.com` | 0.02 s | 0.06 s | 0.07 s | 0.48 s 返回 release JSON |
| `release-assets.githubusercontent.com` | 0.01 s | 0.08 s | 0.09 s | **3.2 MiB/s**（206，ttfb 0.52 s） |
| `raw.githubusercontent.com` | 0.01 s | 0.08 s | **RST（10054）** | 时通时断（索引那次 0.9 s 成功） |

`https://github.com/.../releases/download/...` 必须先从 `github.com` 拿 302；这一跳在
本机是 TCP 直接超时，于是进度条永远停在「正在连接…」。而 **CDN 本身是通的**，
`api.github.com` 也是通的——所以修法是把「连不上的那一跳」换成「连得上的那一跳」：
`net/packs.resolve_github_asset()` 用 api 域取一次 302 的 `Location`（签名直链），
再对 CDN 做分段下载；签名地址只放内存，侧车记原链接（`market.download_segmented(state_url=)`）。

**顺带回答「PCL 不是能裸连吗」**：PCL 下的是 Mojang/BMCLAPI 那套地址（国内有镜像、
CDN 可达），并不是 GitHub Release；它对 GitHub 资源同样有代理/镜像设置。所以
「PCL 裸连能跑」不能推出「这台机器能裸连 GitHub Release」。

#### A. 修复后（真机、GitHub Release、126 MiB）

`scripts/bench_pack_download.py`（走 `download_pack` → 自动解析直链）：

两组各 1 轮（链路在 10 分钟内本身有波动，所以两组都照实列）：

| 连接数 | 耗时 | 平均速度 | 相对 1 连接 |
|---|---|---|---|
| 1 | 48.9 s | 2.57 MiB/s | 1.00× |
| 4 | 40.0 s | 3.15 MiB/s | **1.23×** |

同一时段对**已解析出的 CDN 直链**直接跑（绕过 `download_pack` 的一次解析）：

| 连接数 | 耗时 | 平均速度 | 相对 1 连接 |
|---|---|---|---|
| 1 | 108.9 s | 1.16 MiB/s | 1.00× |
| 4 | 52.4 s | 2.40 MiB/s | **2.07×** |

两次的绝对速度差一个档，说明这条链路本身在抖（单连接 1.16 ↔ 2.57 MiB/s）；
但**两组都是 4 连接更快**，方向一致：GitHub CDN 对单连接有限速，多连接确实能叠加。


单次端到端：`OK shenshen-1.zip 132036744 B in 45.9s = 2.75 MiB/s connections=4 resumed=0`，
506 次进度回调，末值 `(132036744, 132036744)`。

#### B. 修复前（同一个链接，不解析直链）

| 指标 | 1 连接 | 4 连接 |
|---|---|---|
| 30 秒窗口内拿到的字节 | **2 KiB** | **2 KiB** |
| 首字节耗时（`Range: bytes=0-0`） | 20.71 s（HTTP 206） | 同左 |

#### C. 国内镜像（`registry.npmmirror.com`，29.7 MB，分段引擎本身的能力）

| 连接数 | 耗时（中位数） | 平均速度 |
|---|---|---|
| 1 | 4.633 s | 6.12 MiB/s |
| 4 | 4.706 s | 6.17 MiB/s |

镜像上单流已跑满约 55 Mbps，4 连接没有额外收益（1.01×）；**加速收益只在「单连接被限速、
多连接不被限速」时出现**，GitHub CDN 恰好属于这一类（见 A）。

### 4.2 稳态开销

- **未搬迁、未下载时**：`cache_dir()` 只多一次路径比较（初始化时各调一次：`SpeechCache`、
  `download_dir()`、`UpdateService.__init__`）；启动路径没有新增定时器、线程或文件句柄。
- **搬迁**：只有用户点「更改位置…」并确认后才起 1 条守护线程（1 MiB 块复制、10 Hz 进度信号），
  结束后线程退出。
- **素材包下载**：只有点「GitHub 下载」才起 1 条守护线程 + 4 条分段连接；完成/取消即退，
  不驻留。索引读盘 1 次（6 小时 TTL）。
- **启动清理**：只有存在 `root-move.json` 时才起 1 条后台线程删旧根，删完即退。
- **直链解析**：每次下载前多两次请求（release JSON + 302），实测 0.48 s / 0.51 s；
  失败只多一次超时并被回落原链接兜住。

### 4.3 内存

- `MovePlan` 只存相对路径与大小（5675 个文件 ≈ 数百 KB），不读文件内容。
- 下载按 256 KiB 块推进，不整包进内存；sha256 按 4 MiB 块流式计算。
- 设置页新增两个只读 `QLineEdit` + 一个默认隐藏的 `DownloadPanel`。

## 五、实机运行记录

| 场景 | 命令 / 操作 | 结果 |
|---|---|---|
| 视图回切 | `.scratch/batch-shot.py`（真 ChatWindow + 真按钮 `.click()`） | `click1='tool.ai-browser:web' click2='ai'`，截图 `batch-chat-web.png` / `batch-chat-back-to-ai.png` |
| 致谢独立页 | 真 `SettingsDialog` 切页 | 侧栏 10 项、末项「致谢」；截图 `batch-thanks.png`（11 条特别鸣谢、作者栏 5 行） |
| 常规页两行 | 真 `SettingsDialog` | 截图 `batch-general.png`：数据目录 / 缓存目录两行 + 路径框 + 按钮，无横向滚动条 |
| 索引可达 | `packs.fetch_index(raw.githubusercontent.com/MerZlin/Animations/main/packs-index.json)` | `schema 1 / packs ['shenshen'] / 132036744 B / c6d17c49e57c` |
| Release | `gh release view packs-v1 --repo MerZlin/Animations --json assets` | `shenshen-1.zip 132036744 https://github.com/MerZlin/Animations/releases/download/packs-v1/shenshen-1.zip` |
| 数据根搬迁 E2E | `.scratch/roots-e2e.py`（真 `RootsMoveJob` + 真线程 + 真 `petcore --check`） | `搬迁：3 个文件 / 2090 字节 / 0.01s` → 指针写入 → `重启解析 … source pointer` → `--check exit: 0 \| data_root: …/new-root` → `旧根清理： True \| 剩下： []` |
| 素材包下载 + 导入 E2E | `.scratch/pack-e2e.py`（真 `PackJob` + 本地 `http.server` + 真 importer） | `下载：shenshen-1.zip 132036744 字节`、504 次进度回调、按设计回退单流；`导入：True installed character.shenshen` |
| 真实 GitHub 下载 | `packs.download_pack(真 entry, connections=4)` | `132036744 B in 45.9s = 2.75 MiB/s`，506 次进度回调 |
| 全量测试 | `pytest tests/test_petcore_*.py tests/test_pr_report_discipline.py tests/test_architecture.py -q` | 见 5.1 |
| ruff | `ruff check pet/ tests/ petcore/ integrations/ examples/ scripts/` | All checks passed |

### 5.1 测试与静态检查

```
$ QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest tests/test_petcore_*.py tests/test_pr_report_discipline.py tests/test_architecture.py -q
3314 passed, 1 skipped in 312.15s (0:05:12)

$ .venv/Scripts/ruff check pet/ tests/ petcore/ integrations/ examples/ scripts/
All checks passed!

$ QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest tests/test_petcore_pack_index.py tests/test_petcore_pack_download.py -q
77 passed in 10.36s

$ QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest tests/test_petcore_roots.py -q
30 passed in 5.31s

$ QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest tests/test_petcore_chat_views.py -q
20 passed in 4.74s
```

第一轮全量跑出 **12 个市场测试红**（`market_download_segmented` / `market_hardening` /
`market_install` / `market_ui`），定位是 `set_cache_root()` 的进程级全局被
`cli._resolve_root()` 按「另一个根」写过之后泄漏到后续用例；修法是缓存根绑定数据根
（见第三节），复跑全绿。

### 5.1.1 端到端跑出来的三个真问题（已修）

1. **角色 id 取错**：`install_character_source()` 默认拿 ZIP 文件名当 id，`shenshen-1.zip`
   装出来是 `character.shenshen-1`。修法：`PackJob` 记住索引条目，`PackRow.character_id()`
   优先用索引里的 `character`，导入时显式传 `character_id=`；缺索引时退回包 id。
   回归用例 `test_import_uses_the_index_character_id_not_the_zip_name` 与
   `test_import_falls_back_to_the_pack_id_without_an_index_entry`。
2. **缓存根全局泄漏**：`set_cache_root()` 只记一个路径，被 `cli` 按别的根写过后，
   同一进程里所有 `cache_dir()` 都跟着跑（12 个市场测试红）。修法见第三节。
3. **GitHub Release 第一跳不可达**：见 §4.1 的现场定位与 `resolve_github_asset()`。

### 5.2 未验证 / 已知边界

- 跨盘搬迁的真实耗时与「目标盘空间不足」的失败路径未逐一实测；失败时旧目录不动、
  指针不写，有单测覆盖校验失败分支。
- 搬迁期间用户改的设置不会带去新根（界面文案已写「搬迁期间请不要改其它设置」）。
- 缓存根只接管三处大件 + `packs/`，其余仍在数据根。
- 粉丝名单快照未刷新（本机没有 SESSDATA）；`scripts/refresh_community_snapshot.py` 已就绪，
  用户在「设置 → 致谢」配好凭据后运行一次即可。
- 两个素材包行当前都指向 `shenshen`（用户要求的测试口径）；索引结构本身是多包设计。
- 直链解析依赖 `api.github.com`（未登录 60 次/小时）。当前每次下载调 2 次，正常使用够；
  批量下载时会被限流，届时回落到原链接（即回到「可能连不上」的状态）。

### 5.3 构建产物与安装包冒烟

| 项 | 值 |
|---|---|
| setup | `rel-storage-2026-11-01b/dsh-pet-core-setup.exe` |
| 大小 / SHA-256 | 301,271,121 B / `041e8eb72e1b4e6ffc483239bc76b6f12f5262f33c28ff785a368d4563626a56` |
| onedir | `rel-storage-2026-11-01b/dsh-pet-core/dsh-pet-core.exe` |
| 版本 | `0.5.0`（`petcore/__init__.py`） |
| `--check --strict` | exit **0**、`strict.problems=[]`、`characters=shenshen:106`、`menu_template=json`、`diagnostics=[]`、`cache_root` 已进报告 |
| 真实启动 | 进程 10 秒存活、窗口标题 `dsh-pet-core` |
| 首轮产物 | `rel-storage-2026-11-01/`（14:08 构建，早于角色 id 修正与直链解析，**请勿安装**；只作对照留档） |
| 次轮产物 | `rel-storage-2026-11-01c/dsh-pet-core-setup.exe`（含直链解析与失败提示），见 5.4 |

> `gh` 2.103.3 已装好并完成设备码授权（账号 `MerZlin`，scopes `repo,gist,read:org`）；
> 素材包仓库 = <https://github.com/MerZlin/Animations>，Release = `packs-v1`。

### 5.4 最终交付产物

| 项 | 值 |
|---|---|
| setup | `rel-storage-2026-11-01f/dsh-pet-core-setup.exe` |
| 大小 / SHA-256 | 301,308,486 B / `634d373e768f4a4afcd3d700e23bf91a38cd0c2ced31641bf19c4b5ea17d1e34` |
| 包内 `--check --strict` | exit **0**、`strict.problems=[]`、`characters=shenshen:106` |
| 覆盖 | 直链解析 + 分包（`parts` 7×18 MiB）+ 索引三源重试 + 坏索引自愈（§6.1）+ 下载镜像策略（§6.2）|

**装这个**；`rel-storage-2026-11-01`（缺角色 id 修正）、`-b`（缺直链解析）、`-c`（缺分包）是过程产物。

## 六、免费下载方案实测对比（2026-11-01 追加）

用户问「国内还有没有更好的免费快速下载方式」。同一台机器、同一时段逐个实测，
目标都是**同一份 126 MiB 素材包**或它的一份 18 MiB 分片：

| 方案 | 可达 | 实测 |
|---|---|---|
| **GitHub Release 经 `api.github.com` 换直链**（本批实现） | ✅ | **2.75 MiB/s → 126 MiB / 45.9 s**；4 连接相对 1 连接 1.23×（另一次直链直测 2.07×） |
| GitHub Release 经 `github.com`（默认写法） | ❌ | 首字节 20.7 s，之后 30 秒只有 2 KiB（TCP 被吞） |
| `raw.githubusercontent.com` | 时通时断 | RST 10054；2 KB 的索引偶尔 0.9 s 成功 |
| jsDelivr（`fastly.` / `gcore.`，吃 GitHub 仓库、单文件 ≤20 MB） | ✅ | 206 正常、支持 Range，但只有 **~0.25 MiB/s** |
| GitHub Pages（Fastly，`*.github.io`） | ✅ | 206 正常，**0.11 MiB/s**（实测后已关闭测试站点） |
| ghproxy 类公益镜像（ghfast / gh-proxy / ghproxy.net / ghproxy.cc / moeyy / ghhack） | ❌ | 全部不可达或 6 KiB/s |
| Gitee | ✅ 可达（TCP 56 ms） | 未测速：需账号；免费仓库单文件 100 MB，126 MB 必须分包 |

**结论**：本机可用的免费通道里，**GitHub Release + api 直链是唯一跑到 MiB/s 级的**，
其余要么不通，要么慢一个数量级。所以最终形态是：

- **主源**：`packs-v1` 里的两个 Release 资产（`shenshen-1.zip` 与 `packs-index.json`），
  一律经 `resolve_github_asset()` 换签名直链；
- **兜底**：同一份包按 18 MiB 切成 7 片放进仓库的 `packs/`，由 jsDelivr 提供
  （`parts` + 每片两个 jsDelivr 镜像主机），只有主源失败时才会用到；
- **索引也走 Release 资产**：jsDelivr 对分支文件有 CDN 缓存，实测曾把上一版索引
  （分片 urls 为空）返回给客户端——被 `parse_index` 拦下并报错；换成 Release 资产后
  每次 `--clobber` 上传即为该次内容。

新增/改动：`packs.PackPart` + `entry.parts` + `entry.prefer_parts`（逐片下载、跳过已校验
分片、拼装后核对整包、成功后删分片）；`DEFAULT_PACK_HOSTS` 增加 `api.github.com` 与
jsDelivr 三个域；`fetch_index` 同样先解直链；`external_links.PACK_INDEX_URL` 改为
Release 资产、`PACK_INDEX_FALLBACK_URL` 为 raw。

### 6.1 现场事故：`素材包索引没有可用条目：shenshen: 分片 1 urls 为空`

用户在自己机器上点「GitHub 下载」直接报这条。根因是**索引缓存污染 + 兜底源终止链路**：

1. jsDelivr 对分支文件有 CDN 缓存，实测把**上一版索引**（`parts[].urls` 为空）原样返回；
2. 旧代码 `fetch_first_index()` **先 `save_cached_index(payload)` 再 `parse_index(payload)`**——
   坏内容先落盘，校验失败又把整个循环抛穿，于是既不换下一个源，也没有自愈；
3. 下次启动 `load_cached_index()` 读到这份坏缓存，同样只返回 `None` 但不删除，
   用户被卡到 TTL（6 小时）结束。

**修法（照主分支 `pet/updater.py` 的三条口径）**：

| 主分支做法 | 落到本批 |
|---|---|
| 清单永远现取，失败返回 `None` 试下一个镜像 | `fetch_first_index` 每个源重试 2 次后换下一个 |
| 解析失败只影响那一次请求，不产生副作用 | **先解析、后写缓存**；解析失败只记日志并换源 |
| 下载后才校验，坏产物删掉 | 全部源失败时 `discard_cached_index()` 删掉缓存；坏缓存读到就删（自愈） |

回归用例：`test_stale_payload_from_one_source_falls_through`（主源给旧索引 → 落到兜底源 →
缓存里是可用那份）、`test_stale_only_caches_report_every_source`（全坏 → 不留缓存且报每个主机）、
`test_broken_cache_is_deleted`、`test_legacy_cache_with_empty_part_urls_is_dropped`、
`test_valid_cache_is_reused`。

### 6.2 下载镜像策略（`pack_mirror_policy`）

用户问「能否在下载的时候使用镜像之类的」。答案是**本来就在用**（索引里两套源 + 逐级回退），
但缺两件东西：**用户能决定先走哪套**、**看得见当前走的是谁**。本批补上：

| 层 | 能力 |
|---|---|
| 索引 | 单文件源 `urls`（GitHub Release，走 api 直链）与分片源 `parts`（7×18 MiB，每片两个 jsDelivr 主机） |
| 引擎 | 任意一套失败自动换另一套；同一套内逐 URL 回退；分片已校验的跳过、拼装后核对整包 |
| 用户 | 「常规 → 数据 → 下载镜像」：`自动`（GitHub 优先）/ `优先国内镜像` / `只用 GitHub`，即时生效、取消回滚 |
| 反馈 | 下载完成行写出实际来源主机：`下载完成：shenshen-1.zip · 126.0 MiB · 4 连接 · 来自 release-assets.githubusercontent.com` |

**没有做「按 TTFB 自动竞速」**，原因是实测：jsDelivr 的 TTFB（0.5 s）比 GitHub CDN（0.5 s）不差，
但吞吐只有 0.25 MiB/s vs 2.75 MiB/s —— **首字节时间不能预测吞吐**，用它排序会稳定地选到更慢的源。
真要自动挑，得测吞吐（每个源下 256 KiB），代价是每次下载多 3–5 秒且会消耗配额；本轮先用手动优先 +
自动回退，等有第二个「真快」的镜像再加竞速才有意义。
