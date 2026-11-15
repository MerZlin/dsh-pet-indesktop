# PR 报告：petcore 反馈批次 5（气泡 / 手机版 / 回收站 / 工作动画 / 键盘 / WebP / 设置接口）

日期：2026-11-13
范围：`petcore/`、`examples/petcore-mods/`、`scripts/`、`tests/`、`docs/`
不包含：`pet/` 旧壳、用户未提交的 `docs/PETCORE-ARCHITECTURE-MAP.*`

## 1. 修改文件说明

### 1.1 外观与倾斜

- `petcore/contracts/bubble_styles.py`（87 行，plain 预设 +9/−9）：`classic_top` 改为 plain 白底/蓝灰描边/半径 12，
  去掉三角尾巴与透明边。
- `petcore/ui/bubble_style.py`（309 行，上一批新增、本批改 plain/阴影）：`is_plain()`、`shadow_pen_layers()` 对无 shadow 返回空；
  `panel_qss()` 统一透明（plain 也自绘，避免 QSS 换色残留白底）。
- `petcore/ui/bubble.py` +171/−75：`_surface_band()` plain=0，`_paint_surface()` 用
  `addRoundedRect()` 画 plain，`_column()` 把 band 计入屏宽预算。
- `petcore/ui/motion.py` +136/−1：`LEAN_FULL_DEG=24`、`_lean_limit()` 读
  `drag_lean_strength`（0–100）；0 关闭，默认 30% ≈ 7.2°。
- `petcore/config.py` +109/−1：新增 `drag_lean_strength`、`work_animations_only`、
  `chat_layout`、`chat_trash_retention_days`、`hotkey_keyboard_move`；`Config.reload()`；
  `save()` 的 `os.replace` 5 次退避重试。

### 1.2 对话窗

- `petcore/ui/chat/window.py` +58：标题栏齿轮/手机图标、`settings_requested`/
  `layout_requested`、`session_title()`、`set_trash_count()`、清空确认；回收站按钮。
- `petcore/ui/chat/controller.py` +97/−5：深链 `_open_chat_settings()`、布局切换
  `_on_layout_requested()`、删除改 `trash()`、建窗 `purge()`、手机版选择。
- 新 `petcore/ui/chat/phone_window.py` 188 行：单栏手机骨架，复用 `ChatComposer`/
  `MessageBubble`/`view_stack`/`ChatController` 信号；顶栏返回/新建/更多、会话抽屉、
  底部 AI/DSH/扩展 chips、`⋮` 菜单覆盖桌面全部按钮。
- 新 `petcore/ui/chat/trash_dialog.py` 176 行：回收站恢复/永久删除/清空，永久删除与清空
  二次确认。
- 新 `petcore/ui/chat/contrast.py` 95 行：WCAG 对比度、`ensure_contrast`、`readable_ink`；
  chips 的强调色自动调暗/调亮。
- `petcore/ui/chat/icons.py` +34：gear/phone/desktop/trash/restore；`styles.py` +35/−8：
  齿轮/手机按钮、手机 chips 的浅色/深色 QSS。
- `petcore/chat/session_store.py` +61/−1：`trashed_at`、`trash/restore/list_trashed/
  purge`，`list_sessions` 默认过滤回收站。
- `petcore/ui/settings/dialog.py` +35/−8：`select_page()` 支持 `页键#行文案` 并返回 bool；
  注册「使用手册」页。

### 1.3 工作动画与键盘

- `petcore/ui/pet_window.py` +367/−11：`set_work_state()`/`_work_lock_active()`/
  `_work_spec()`；键盘驱动 `drive_keyboard()`/`_start_keyboard_drive()`/
  `_advance_keyboard_drive()`/`_release_keyboard_drive()`；`_on_frame()` 分派；
  拖拽/探头/隐藏/锁屏取消。
- 新 `petcore/engine/keyboard_drive.py` 144 行：`MoveSegments`/`move_segments()`/
  `build_drive_plan()`/`drive_position_at_frame()`；前导 0=前摇、尾部 1=收尾。
- 新 `petcore/platform/keyboard_state.py` 55 行：Windows `GetAsyncKeyState` 只读
  WASD/方向键；非 Windows 明确不支持。
- 新 `petcore/ui/keyboard_drive.py` 60 行：16ms 轮询表，只在模式开启时运行。
- `petcore/ui/app.py` +177/−5：`notify_agent_state()` 扇出工作状态；`toggle_keyboard_drive()`/
  `keyboard_drive_target`；`start_config_watch()`（2s mtime/size 检查，设置窗打开时延后）。
- `petcore/ui/menu_actions.py` +8、`menu_layout.py` +1、模板 +1、`coverage.py` +1：
  「桌宠控制 → 控制移动」勾选项；快捷键注册失败回填可见提示。
- `petcore/ui/settings/pages_core.py` +208：键盘控制快捷键行；`page_interaction.py` +29：
  倾斜强度、工作动画行。

### 1.4 WebP / 角色包 / 致谢 / B 站

- `petcore/contracts/content.py` +14：`SheetSpec` + `AnimationSpec.sheet`。
- `petcore/media/backends.py` +7：注册 `webp`；`media/webp_clip.py` 103 行：QMovie、真实
  帧延时；`media/sheet_clip.py` 156 行：一次解码、8M 像素预算、4 帧缓存、QTimer；
  `media/library.py` +31：sheet 优先于 path。
- `petcore/content/legacy.py` +171/−8：`MEDIA_SUFFIXES` 加 `.webp`、`canvas` 从 manifest、
  `clips`/sidecar 解析、`media_kind=sheet`；`character_pack.py` +15/−9：webp/画布。
- 新 `petcore/content/sprite_pack.py` 238 行：aboutme 单角色目录 → `character.<id>`；
  新 `petcore/net/aboutme_packs.py` 122 行：curated 索引逐文件下载 + sha256；
  `petcore/content/data/aboutme_packs.json`（2 个角色、13.9 MiB 素材清单）。
- `petcore/community/model.py` +18、`community/data/credits.json` +18、`page_about_thanks.py`
  +50/−7：致谢加 MerZlin/Animations、VPet、aboutme-pets，按 kind 分组；
  B 站 `_get_json` 直连优先、`store.save_fans_count`、`service.live_fan_total`、
  无凭据不再报 auth、UI 显示实时总数 + 打开粉丝页。

### 1.5 设置公开接口 / 使用手册

- 新 `petcore/settings_schema.py` 509 行：278 键 `SettingSpec`；`settings_schema_ranges.py`
  185 行（脚本从 `_normalize` 生成）；`settings_cli.py` 282 行：
  `list|describe|get|set|export|path`；`storage/config_lock.py` 74 行：msvcrt/fcntl 目录锁。
- `petcore/cli.py` +14：`settings` 子命令分派；`settings_cli.set` 锁 → 重读 → 白名单 →
  set → 回读，状态/敏感/未知键拒绝，越界默认报错、`--force` 夹取。
- 新 `petcore/ui/settings/page_manual.py` 153 行 + `manual_content.py` 179 行：
  常见问题/使用说明/给 AI 的提示词；提示词动态填路径、可复制、可导出 schema + guide。
- 新 `scripts/gen_settings_meta.py` 95 行、`scripts/gen_aboutme_packs.py` 207 行：
  可复现生成元数据与 aboutme 索引。

### 1.6 测试与文档

- 新增 12 个测试文件（约 1600 行）：`test_petcore_keyboard_drive.py` 208、
  `test_petcore_work_animations.py` 153、`test_petcore_chat_phone.py` 139、
  `test_petcore_chat_trash.py` 91、`test_petcore_media_webp.py` 179、
  `test_petcore_sprite_pack.py` 163、`test_petcore_settings_cli.py` 125、
  `test_petcore_config_reload.py` 79、`test_petcore_manual_page.py` 90。
- 更新 `test_petcore_settings.py`（页数 11、倾斜/工作锁/快捷键行）、
  `test_petcore_ai_mod.py`（chat_layout/回收站天数）、`test_petcore_bubble.py`（半径 12/24）、
  `test_petcore_bubble_styles.py`（plain 默认 + 靠边）、`test_petcore_chat_ui.py`
  （齿轮深链/清理确认）、`test_petcore_community_*`（致谢 18 条/无凭据不报错）、
  `test_petcore_agent_state_wiring.py`（工作状态扇出）。
- 文档：`docs/PETCORE-SPEC.md` §32 + 页数 11、`docs/PETCORE-ARCHITECTURE.md` 页数、
  `docs/PETCORE-MOD-FORMAT.md` WebP clips、`docs/PETCORE-CHARACTER-PACKS.md` §6a、
  `docs/SETTINGS-CHANGE-GATES.md` 5 键、`docs/PETCORE-LOG.md` L197、
  `docs/PETCORE-LOG-INDEX.md`、`petcore/AGENTS.md`、`THIRD_PARTY_NOTICES.md`。
- 仓库全量：`QT_QPA_PLATFORM=offscreen python -m pytest -q` = **11871 passed,
  22 skipped, 6 failed（26:47 / 1607.41 s）**；6 例失败均为既有问题，且失败涉及的
  `scripts/build_onedir.ps1`（无 BOM）、`docs/PETCORE-V43-V44-PARITY-2026-11-05.md`
  （含外部品牌字样）、`test_bundle_slim` 的 fake bundle（缺 `Qt6WebView.dll`）
  在本批 diff 之外（`git diff --numstat` 为空）。

## 2. 性能分析

环境：本机 Windows，Python 3.12，Qt offscreen（除特别注明），`psutil` 采样。

| 新增路径 | 触发频率 | 实测 | 结论 |
|---|---|---|---|
| 外部配置检查 `_config_signature()` + `_check_external_config()` | 2 s 一次（仅应用运行期） | 无变化 **0.072 ms/次**；真实外部改动 **1.09 ms/次**（500/200 次均值） | 无变化时只做 1 个 `stat` + 几次 dict 比较；不读文件、不起线程。2 s 周期下约 0.0036% 单核。 |
| 手机版开窗 | 用户打开对话窗 | 桌面 **17.1 ms**，手机 **18.9 ms**（各 8 次去极值） | +1.8 ms，来自额外 chips/菜单；无新增线程。 |
| 键盘驱动单拍 | 仅「控制移动」开启时 16 ms 一次 | **0.006 ms/拍**（1000 次均值，假 clip） | 约 0.04% 单核；关闭即停表，无后台线程。 |
| 5120×5400 精灵表解码 | 每段 sheet 首次播放 | 解码 **85.8 ms**、切帧 **0.11 ms**；8M 像素预算后帧 **344×193**，单张约 **32 MB** | 一次性成本；4 帧缓存 + 16 段 LRU + 96MB 总字节预算。大表仍建议转帧序列。 |
| `Config.reload()` | 外部改动时 | 包含在 1.09 ms/次内 | 只读 JSON + normalize，不写盘。 |
| `Config.save()` 重试 | 保存设置 | 正常路径不增加；仅 `PermissionError` 时 50/100/150/200 ms 退避 | 不改变正常保存延迟。 |

新增资源/边界：无新增网络请求（aboutme 下载只在用户点击时发生）；无新增线程
（`KeyboardDriveController` 是 QTimer；`AboutmeDownloadJob` 是用户点击的短命 QThread）；
无新增常驻磁盘 IO（2 s 检查只 `stat`）；`SheetClip` 有 8M 像素硬上限。

## 3. 实机运行记录

（源码实机，Windows 本机；不做 30 分钟浸泡。）

- 启动源码 `python -m petcore`，主宠正常显示；右键菜单出现「控制移动」。
- 气泡：默认白底无尾巴；靠右缘截图确认卡片距边 6px、无三角。
- 拖动倾斜：30% 与 100% 各拖一次，方向跟随拖动，松手回正。
- 设置页：真实打开「设置 · 主宠」，左栏 11 页（含「使用手册」）；点开「使用手册」，
  三个子页（常见问题/使用说明/给 AI 的提示词）可切换，FAQ 行文本可见。
- 手机版：独立源码进程打开 `PhoneChatWindow`，真实窗口 475×950（125% DPI，逻辑 380×760）；
  顶栏返回/新建/更多、AI/DSH chips、输入区可见；返回按钮打开会话抽屉（新建/分组/
  已归档/回收站/清空）；回收站按钮真实打开 `TrashDialog`（空态、恢复/永久删除/清空/关闭）。
  齿轮深链由 `test_title_bar_settings_button_*` 覆盖；ai.chat 安装后菜单出现「AI 助手」，
  但本次实机鼠标点击未成功触发该菜单项，齿轮端到端点击未完成。
- 键盘：安装后热键 `Ctrl+Alt+M` 真实开启；按住 A 1.5s，窗口从 x=1606 移到 x=1409
  （197px），角色朝左并播移动动画；探头状态下按键也能接管（本次实机修复点）。
- 工作动画：`--check` 对导入的 dsh1 报 `work_clips=1`；`test_petcore_work_animations.py`
  覆盖「有 work 只播 work / 无 work 回退 idle / 状态解除 / 探头拖拽不锁」；未逐帧录屏。
- WebP：真实联网下载 aboutme `dsh1`（7.5MB，约 180s），`build_sprite_pack` 导入 5 段
  精灵表；`--check` 报 `backends={'sheet': 5}`、`work_clips=1`、`body_box` 正确。
- B 站：`fetch_fan_count(293026389)` 实网返回 **4783**；无凭据不报 auth、UI 实时总数
  文案由 `test_petcore_about_thanks.py` 覆盖；未在 UI 上点「刷新名单」完成整链路。
- 使用手册：真实设置页打开；复制/导出由 `test_petcore_manual_page.py` 覆盖；
  `petcore settings set` 在临时根写入并 `get` 回读成功（含 `--force` 夹取）。

## 4. 审查发现与修复

独立审查子代理按文件审了本批 diff，确认并修复了以下问题（均有聚焦测试）：

- **回收站 0 天误删**：`SessionStore.purge(0)` 现在直接返回 0（0 = 不自动清理），
  controller 建状态时也只在 `days > 0` 时清理；测试改用 controller 同角色的目录。
- **工作锁劫持键盘移动**：`_play_spec` 的 work 白名单加入 `drive`，显式键盘控制压过
  工作锁；`_start_keyboard_drive` 校验 `_action_kind == "drive"`，被其它闸门抢走时不置
  `_drive_active`。
- **SheetClip 内存无上限**：`ClipLibrary` 增加 `MAX_SHEET_BYTES=96MB` 字节预算，
  `SheetClip.estimated_bytes()` 参与 LRU；超预算先淘汰最旧 clip，不只是按 16 段。
- **下载线程随设置页销毁**：`_AboutmeDownloadJob` 不再挂页面 parent，线程由
  `_ACTIVE_DOWNLOADS` 持有，`finished` 才 `deleteLater`；关窗不会析构运行中的 QThread。
- **坏配置热重载不重试**：外部 `config.json` 解析失败时不推进 mtime 签名，下一拍重试；
  不再把半截 JSON 的修改永久吞掉。
- **快捷键需重启**：`hotkey_*` 加入 `restart_required`，`settings set` 会明确提示重启。
- **提示词命令 `--json`**：`settings list` 接受 `--json`（输出本来就是 JSON），提示词
  里的命令不再报 unrecognized arguments。
- **流式中切布局**：`ChatController._on_layout_requested` 在 `state.active` 时拒绝并显示
  「回答中不能切换布局」，避免空气泡/丢思维链。
- **导入 id 路径逃逸**：`sprite_pack.character_id_for()` 统一清洗 + `is_valid_id`，多宠页
  先校验再拼 `mods/character.<id>`；`../../evil` 被压成单段 id 或拒绝。
- **键盘帧重入**：`_advance_keyboard_drive` 在 `_drive_jumping` 时直接返回，循环接缝不再
  多走一帧。

## 5. 打包与安装冒烟

- 命令：`pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/build_petcore.ps1 -Clean -Installer -OutputRoot rel-storage-2026-11-13c`（未传 `-SignThumbprint`）。
- 产物：`rel-storage-2026-11-13c/dsh-pet-core-setup.exe`，**337,411,764 B（321.8 MiB）**，
  SHA-256 `F4B00191704C7871D2484AA0FBB4344CA4CE69D0083CDE99540678093410A43D`；
  `Get-AuthenticodeSignature` = **NotSigned**（按用户要求不签名）。
- 包内 `check.json`：`problem=null`、`strict.problems=[]`、`aboutme_packs=2`、`menu_template=json`；
  `_internal/petcore/content/data/aboutme_packs.json` 存在。
- onedir 冒烟：`dsh-pet-core.exe settings list --json --query 键盘 --out …` 退出 0，
  写出 1 条 `hotkey_keyboard_move`；说明窗式子进程的 CLI 分派与 `--out` 可用。
- 未运行安装器本体（避免改动本机安装目录）；安装包的 Inno 编译成功，setup 可交给用户实机安装。

## 6. 未验证 / 风险

- 真实全屏游戏/视频下的岛隐藏仍只有单测；macOS/Linux 未做同口径验证。
- aboutme 下载只验证索引与本地导入，未在无网/限速网络下实测；raw.githubusercontent 可达性
  依网络而定，失败会保留手动导入路径。
- 手机版全功能只做 offscreen 测试与源码实机抽查，未做长时浸泡。
- `SheetClip` 8M 像素/段 + 96MB 总预算仍可能偏高；大表角色建议转帧序列。
- 全量套件既有失败 6 例（`test_bundle_slim` 4 例、产品文案 1 例、BOM 1 例）不在本批范围；
  对应文件均未出现在本批 diff 中。
