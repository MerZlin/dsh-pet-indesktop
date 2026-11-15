# PR 报告：petcore 反馈批次 6（手机版窄窗 / 模式返回 / 朗读 / 抛掷探头 / 键盘成本 / 朋友的酒 / 致谢 / URL 安装）

日期：2026-11-14
范围：`petcore/`、`examples/petcore-mods/`、`scripts/`、`tests/`、`docs/`
不包含：`pet/` 旧壳、用户未提交的 `docs/PETCORE-ARCHITECTURE-MAP.*`

## 1. 修改文件说明

### 1.1 手机版窄窗与常驻模式栏

- `petcore/ui/chat/phone_window.py`：最小宽度 340 → **380**；手机版
  `message_horizontal_margin=16`、composer 左右 12px、空态 24px；视图 chips 从
  `ai_layout` 移到 `chat_main` 的 `view_stack` 下方（DSH/记忆/网页 AI 页常驻）；
  更多菜单加「回到对话」。
- `petcore/ui/chat/window.py` +118/−12：`_update_bubble_widths()` 去掉 320 下限、
  assistant 的 `body.setMinimumWidth()` 改为 `min(360, available)`；删除侧栏
  「清空当前会话」按钮、`_on_clear()` 与接线；新增 `speech_requested` /
  `speech_stop_requested` 信号、`_speaking_bubble` 与 `speech_started/finished/failed`。
- `petcore/ui/chat/widgets.py` +65：assistant 气泡复制按钮旁新增喇叭按钮
  （`speak_requested` / `speak_stop_requested`）、`set_speaking()`、`set_speech_status()`、
  `set_dark()`；用户气泡不显示该按钮。
- `petcore/ui/chat/controller.py` +200/−5：`_speak_reply()` / `_stop_speech()`；
  `host.ports.speech` + `SpeechRequest(owner="chat", slot=…)`；代数作废旧回调；
  关窗/切会话取消；错误按 `SpeechError.reason` 转成可读文案。
- 新 `petcore/ui/chat/speech_text.py` 45 行：`clean_for_speech()`（去 Markdown、
  4000 字上限，与 `reply_voice` 同口径）。
- `petcore/ui/chat/icons.py` +64：`speaker` / `speaker_off` 自绘图标；
  `petcore/ui/chat/styles.py` +39/−8：喇叭行与朗读状态行浅/深色 QSS。

### 1.2 抛掷撞边与探头

- `petcore/ui/edge_probe.py` +32/−1：`_throw_or_moving()`（`motion.thrown` /
  `throw_egg_active` / 速度 > 8 px/s）；`maybe_enter()`、`_enter_if_at_edge()`、
  `on_release()`、`_tick()` 都走同一守卫，抛掷中不排/不进探头。
- `petcore/ui/pet_window.py` +406/−12（含本批与上一批累计）：`_on_thrown()` 取消
  已激活探头（`restore=False`）；键盘落点/漫游落点未变化时不调 `move()`；
  `squash()` 公共方法（接管态 no-op，force 绕过 `click_squash_enabled`）。
- `petcore/ui/effects.py` +19/−6：`start_squash(force=, bounces=, strength=)`。

### 1.3 键盘控制成本

- `petcore/ui/keyboard_drive.py`：自适应轮询——按住 16ms，连续 6 个空拍后 50ms，
  检测到按键立即回 16ms；目标不可见不读键；目标缺少 `isVisible` 的测试替身按可见处理。
- `petcore/ui/pet_window.py`：`_advance_keyboard_drive()` 与
  `_advance_move_from_frame()` 的 `move()` 都加「坐标未变化跳过」。

### 1.4 朋友的酒模式

- `petcore/contracts/host.py` +13：`PresentationPort.squash(duration_ms=0,
  bounces=0, strength=0)` 契约。
- `petcore/ui/app.py` +205/−5（累计）：`_PresentationAdapter.squash()` 转发到
  `PetWindow.squash()`，非 GUI 线程排队，失败只记 debug。
- `petcore/ui/pet_window.py`：`squash()`；`petcore/ui/effects.py` 支持显式次数/强度。
- `examples/petcore-mods/media.music/host/runtime.py` +27：`BUDDY_WINE_INTERVAL_SECONDS=1.0`、
  `DEFAULTS["music_buddy_wine_mode"]=False`、`_tick_buddy_wine()`（playing 拍逐宠发
  `squash()`，暂停不发）。
- `examples/petcore-mods/media.music/host/settings_page.py` +21：设置页
  「播放与唱歌 → 朋友的酒模式」复选框、快照/回滚、关键词；`mod.json` 1.2.0 → **1.3.0**。
- `petcore/config.py` +114/−1（累计）：`music_buddy_wine_mode`（bool，全局，默认
  False）+ `GLOBAL_CONFIG_KEYS` + `_normalize`；`petcore/settings_schema.py` 标签；
  `scripts/gen_settings_meta.py` 重新生成 `settings_schema_ranges.py`（104 kinds）。
- 新 `tests/test_petcore_buddy_wine.py` 73 行：每秒一次、关闭不发、暂停不发、
  `PetWindow.squash` 接管态 no-op。

### 1.5 致谢 / B 站粉丝名单

- `petcore/community/model.py` +19/−1：`@浩瀚星空2005` 的 note 改为
  「参考项目作者；本项目动画素材制作者」。
- `petcore/community/data/credits.json` +19/−1：同步该 note。
- `petcore/ui/settings/page_about_thanks.py` +58/−7：作者账号区新增说明
  「张墨林制作桌宠软件框架，不制作动画；本项目动画素材来自 B 站 @浩瀚星空2005。」
- `petcore/community/data/fans.json` +3/−2：`total` 3950 → **4788**，新增
  `total_fetched_at=2026-11-14T21:52:19+08:00`，保留原 `fetched_at` 与 3948 个 names。
- `scripts/refresh_community_snapshot.py` +39/−7：新增 `--total-only`（免登录只更新
  total，不动 names）；无凭据时提示该参数。

### 1.6 aboutme 来源与从链接安装

- `petcore/ui/settings/page_pets.py` +238/−2（累计）：角色素材包新增「素材来源」按钮
  （打开 `https://github.com/hunmer/aboutme-pets`）；下载确认框显示来源 URL；
  下载菜单 tooltip 显示 `…/tree/main/<pet_dir>`；关键词加「来源/仓库」。
- `petcore/mods/install.py` +30：新增公开 `open_archive()`（任意 zip 安全解包，
  不要求 `mod.json`；复用 `_extract_zip` 的 zip-slip/设备名/大小写/symlink/预算校验）。
- 新 `petcore/mods/url_install.py` 232 行：`normalize_url`（只收 http/https）、
  `fetch_zip`（`net.http.fetch_url`，128 MiB 上限，缓存到 `cache/mods/url-install`）、
  `preview_zip`（mod.json / manifest.json+actions.json / 其余拒绝）、
  `install_zip`（mod → `mods.install.install`；sprite → `build_sprite_pack`；
  feature 未授权拒绝）、`discard`。
- `petcore/ui/settings/page_mods.py` +207/−1：扩展管理新增「从链接安装」卡片
  （`QLineEdit` + 下载并安装 + 状态行）、`_UrlPreviewJob` / `_UrlInstallJob` 两个
  QThread、预览确认框、安装后 `reload_mods()`、`dispose()` 收口。
- 新 `tests/test_petcore_url_install.py` 148 行：URL 校验、非 zip、content mod、
  feature 授权、zip-slip、aboutme 角色包、未知包。
- `tests/test_petcore_extensions_nav.py` +13：URL 输入框/按钮与非法链接提示；
  `tests/test_petcore_sprite_pack.py` +1：来源按钮；`tests/test_petcore_about_thanks.py`
  +37：作者说明断言。

### 1.7 测试与文档

- 修改：`test_petcore_chat_phone.py`（380 宽、常驻 chips、窄窗气泡）、
  `test_petcore_chat_ui.py`（清空入口不存在）、`test_petcore_edge_probe.py`（抛掷守卫）、
  `test_petcore_keyboard_drive.py`（自适应轮询）、`test_petcore_music_mod.py`
  （`_Presentation.squash`）。
- 新：`test_petcore_chat_speech.py` 132 行、`test_petcore_buddy_wine.py`、
  `test_petcore_url_install.py`。
- 文档：`docs/PETCORE-SPEC.md` §33、`docs/PETCORE-ARCHITECTURE.md` 批次 6 节、
  `docs/PETCORE-MOD-FORMAT.md` §6.0 + squash、`docs/PETCORE-CHARACTER-PACKS.md` §6a、
  `docs/SETTINGS-CHANGE-GATES.md` 新键、`petcore/AGENTS.md` 三条。

## 2. 性能分析

环境：本机 Windows，16 逻辑核，Python 3.12，真实 Qt（非 offscreen），psutil 采样进程树。

### 2.1 键盘控制三态（安装版同款源码启动，25s 预热）

| 状态 | 总 CPU%（16 核口径） | 单核% | 线程 |
|---|---|---|---|
| 模式关（预热后） | 0.75–0.93% | 12.1–14.8% | 18–20 |
| 模式开、不按键 | 0.85–1.05% | 13.6–16.9% | 18–20 |
| 首次按住 A（每秒） | 1.35, 0.88, 1.35, 0.88, 1.06, 0.59, 0.87, 0.49 | — | 18–20 |
| 持续按住移动 | 0.72–0.83% | 11.5–13.3% | 18–20 |

结论：开模式但不动约 **+0.1% 总 CPU**；首次按方向键有一秒级 **+0.3%** 的预热尖峰
（`FrameSeqClip` 首次创建/预取移动 clip），随后回落；持续移动没有额外线程增长。
启动瞬间另有一次 frame-sequence 供给（79 线程）与键盘控制无关，预热后回到 18–20。

### 2.2 定时器与 squash

| 路径 | 实测 | 说明 |
|---|---|---|
| `KeyboardDriveController._tick()` | **0.8 µs/拍**（20000 次均值，假 reader/target） | 空闲 50ms = 16 µs/s；按住 16ms = 50 µs/s |
| `PetWindow.squash()` | **0.35 µs/次**（20000 次均值，假 effects） | 朋友的酒模式 1 次/s，可忽略 |
| 自适应轮询 | 空闲 62.5 → 20 次/s | 每秒少 42.5 次 Python 回调 + 8 次 `GetAsyncKeyState` |

### 2.3 URL 安装

- 本机回环 HTTP 下载 7 KB zip：`fetch_zip` + `preview_zip` + `install_zip` 总耗时
  **0.87 s**（含进程启动与 Qt 导入；纯安装为毫秒级）；无新增常驻线程，下载/安装
  各一个短命 QThread，结束即回收。
- 下载上限 128 MiB；缓存文件安装后删除；不触碰用户配置。

## 3. 实机运行记录

- **手机版 380 宽**：真实 Windows Qt 窗口 380×760（DPI 125% 抓图 475×950）；视口
  364px，assistant 气泡 340px、`body.minimumWidth` 340px；12 遍长回复完整换行，
  无右侧裁切（`.scratch/phone380-ctrl.png`，不入库）。窗口最小宽 380，最大 520。
- **常驻模式栏**：构造记忆视图后 `set_view("mem")`，底部 chips 仍可见（`对话/DSH/记忆`），
  点「对话」回 AI；手机更多菜单含「回到对话」（单测覆盖）。
- **清空入口**：侧栏 footer 不再有 `clear_button`；`window.clear()` 内部仍可用。
- **逐条朗读**：真实窗口 assistant 气泡显示喇叭按钮；点击后 controller 向
  `host.ports.speech` 发 `SpeechRequest`（文本已去 Markdown）；无语音端口时状态行
  显示「语音不可用…」。真实音频播放未验证（本机未配置语音后端）。
- **抛掷探头**：fake window 设 `motion.thrown=True` + 左缘 + 800ms 定时器触发，
  断言不进入探头、不移动；停稳后仍正常进入（单测）。
- **键盘控制成本**：见 §2.1；实机按住 A 期间窗口 x 从 1138 → 781（`drive.log`），
  松开后停；线程稳定 18–20。
- **朋友的酒模式**：runtime harness 播放中每秒一次 `squash()`，暂停/关闭不发；
  `PetWindow.squash` 在抛掷态返回 False（单测）。真实音乐播放未在本轮点验。
- **B 站粉丝**：`fetch_fan_count(293026389)` 实网返回 **4788**；
  `scripts/refresh_community_snapshot.py --total-only` 写入 total 4788 并保留旧名单。
- **URL 安装**：本机回环 HTTP 提供真实 zip，`fetch_zip` → `preview_zip`
  （`test.real-url 1.0.0`）→ `install_zip` 成功落地 `mods/test.real-url/mod.json`。
- **aboutme**：上一轮已实网下载 dsh1（7.2 MiB，5 个 WebP 精灵表 8×23，640×360）并
  导入 `.scratch/aboutme-real/mods/character.dsh1`；本批新增的来源按钮与 URL 安装
  复用同一 `sprite_pack` 路径。

全量：`QT_QPA_PLATFORM=offscreen python -m pytest -q` = **11896 passed, 22 skipped,
7 failed（22:35）**；6 例为既有失败（`test_bundle_slim` 4、产品文案 1、`build_onedir.ps1`
BOM 1），第 7 例是旧壳 `test_chat_subsystem.py::test_append_message_atomic_across_store_instances`
的时序 flake，单独复跑 **1 passed**（12.88s），本批未改 `pet/chat/session_store.py`。
ruff 全绿；文档/PR 纪律测试 379 passed。

## 4. 审查修复

独立审查子代理按文件审了本批 diff，确认并修复：

- **P1 fans.json 语义**：`total=4788` 与旧名单 3948 的差异不再是「同名占位」；`Fans`
  新增 `total_fetched_at`，`page_about_thanks.summary()` 在总数与名单日期不同时显示
  「B 站总数 … 更新于 …；名单更新于 …」，不再写「含同名占位」。随包快照测试改为
  有 `total_fetched_at` 时只校验 `total >= len(names)` 与两个日期的先后；无该字段
  时仍保留原 `0..20` 不变量。`--total-only` 现在有明确语义，不是改完就红的死字段。
- **P1 aboutme 下载回调崩溃**：`PetsPage` 新增 `_disposed`，`_on_download_progress` /
  `_on_download_done` 在页面销毁后不再碰控件，并删除下载临时目录；回归测试
  `test_download_callback_after_page_dispose_does_not_touch_ui`。
- **P1 音乐版本**：`media.music` 1.3.0 的既有断言同步更新，`test_manifest_and_contributions_load` 转绿。
- **P2 朗读状态/多窗**：朗读代数改为**按窗口** `dict[(owner, slot), int]`，owner 改为
  `chat:<owner>:<slot>`；`_stop_speech`/`_on_closed` 主动清窗口气泡状态，两个对话窗
  不再互相 `cancel_owner("chat")`，关窗/停止不会再停在「朗读中…」。
  `test_stopping_clears_the_bubble_even_without_a_callback` 覆盖无回调的停止路径。
- **P2 URL 角色包原子安装**：aboutme 分支先 `build_sprite_pack` 到
  `mods/.staging-url-<id>-<pid>`，成功才替换 `character.<id>`；失败清理 staging，
  旧目录先移到 `.trash-url-*` 再回滚。`test_failed_sprite_install_leaves_no_staging_directory`
  覆盖半成品目录。
- **P2 功能扩展预算**：URL 通道的 `feature` 包安装改用市场硬上限
  `MARKET_ARCHIVE_LIMITS`（64 MiB / 256 MiB / 10000），content/角色包仍用
  128 MiB / 512 MiB / 20000；预览仍用后者以识别包类型。
- **P3 清理**：手机版重复的边距赋值删除（以 `resizeEvent` 的 12/24 为准）；
  `#clear-session-button` QSS 删除；致谢页 vmid 改用 `model.DEFAULT_VMID`；
  `page_pets` 临时目录 `rmtree` 加空路径保护；语音不可用的默认文案改为
  「检查免打扰/静音时段或设置 → 语音后端」。

仍未处理（低危，留作后续）：布局切换仍重建窗口、桌面/手机往返丢窗口几何；
`clean_for_speech` 与 `ai.chat/reply_voice` 仍是两份近似实现；手动朗读与自动朗读
共用一条语音 worker 时手动请求会排在自动朗读后面。

## 5. 未验证 / 风险

- 逐条朗读只验证到「按钮 → 端口 → 合成请求」，未在本机配置语音后端听声；音频播放
  链路沿用现有 `SpeechService`/`CoreSpeechPort`。
- 朋友的酒模式、抛掷探头为单测与状态机验证，未做真实音乐/真实甩飞的长时观察。
- URL 安装的 `feature` 包需要用户先勾选「允许功能扩展」；UI 只做提示，不自动改授权。
- B 站完整名单仍缺 SESSDATA；本轮只把 total 更新到 4788，names 仍是 2026-10-27 快照。
- 未做 30 分钟浸泡；未安装本轮 setup 本体；真实全屏游戏/视频下的岛隐藏仍只有单测。
- 上一批的 `tests/test_bundle_slim.py` 4 例、产品文案 1 例、BOM 1 例为既有失败，
  不在本批范围。

## 6. 交付物

- 安装包：`rel-storage-2026-11-14a/dsh-pet-core-setup.exe`，
  **351,239,836 B（334.9 MiB）**，
  SHA-256 `82d16b93fc6a266f11fc255366068faefa5b05809c13db08ce0112e11b178116`；
  `Get-AuthenticodeSignature` = **NotSigned**（按用户要求不签名）。
- 构建：`pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/build_petcore.ps1
  -Clean -Installer -OutputRoot rel-storage-2026-11-14a`；Inno `Successful compile
  (110.094 sec)`。
- 源码 `--check --strict` 退出 0：`problem=null`、`strict.problems=[]`、
  `aboutme_packs=2`；onedir CLI 冒烟 `dsh-pet-core.exe settings list --json --query
  朋友的酒 --out …` 退出 0，命中 `music_buddy_wine_mode`。
- 扩展包：`rel-storage-2026-11-14a/extensions/media.music-1.3.0.zip`
  （101,597 B，14 files，sha256 `bf8e5f5a9eca…`）；`ai.chat` 本批未改代码，且其
  `persona_presets/` 不在扩展打包白名单，未重打包。

