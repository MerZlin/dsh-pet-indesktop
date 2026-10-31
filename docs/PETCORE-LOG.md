# petcore 变更日志

> **读取方式**：先看 [`PETCORE-LOG-INDEX.md`](PETCORE-LOG-INDEX.md)，只打开命中的
> `LNNN` 条目；不要把整份 log 一次读进上下文。
>
> **写入方式**：每次代码/测试/文档改动追加一条 `## LNNN`，编号单调递增，不覆盖
> 历史。`tests/test_petcore_docs.py` 校验每条 `LNNN` 在索引里有对应行。

---

## L001 · 2026-10-20 · M0–M6 最小 core 与 mod 宿主

**范围**：从 `main`（`147170f`）新建 `feat/petcore-vpet-architecture`，建立可独立
运行的 `petcore` 最小 core。

**改动**

- 新增 `petcore/`：`engine/`（枚举、几何、动画链、行为、位移、状态）、
  `contracts/`（content/host/events/feature/contributions/capabilities/worker）、
  `media/`（Clip 注册、库、fallback、音效、WebM/GIF/帧序列适配）、
  `content/`（旧素材只读、mod 内容、收集、打包）、`mods/`（manifest、发现、启停、
  安装、feature、CLI）、`platform/`（Windows 输入、屏幕、会话、自启）、`ui/`
  （窗口、渲染、气泡、托盘、菜单、设置）。
- 新增 `petcore/menu_templates/petcore-default-v1.json`、内置 demo feature、
  `petcore/cli.py`、`--check`、`pack-legacy`。
- 新增 `tests/test_petcore_*.py` 与结构守卫 `tests/test_petcore_architecture.py`。
- CI ruff 行改为 `ruff check pet/ tests/ petcore/`；`.gitignore` 增加 `.venv/`、
  `/data/`、`/mods/`。

**验证**

- `tests/test_petcore_*.py`：222 passed（M6 基线）。
- `python -m petcore --check`：shenshen 106 段全部 webm，`body_box`/`head_box`
  正确；`pack-legacy` 生成的 content 包可被 `--check --mods-dir` 识别。
- 实机窗口截图 692×389（0.72 缩放 × 1.5 DPR）。
- 首帧中位：WebM 190.3 ms；WebP 帧序列 13.7 ms。idle CPU：WebM 16.01%，
  帧序列 21.85%。

**遗留**：真实双屏未验（本机 1 块物理屏）；拖拽/点击/穿透只有无头测试；
macOS/Linux 菜单外观未验；热卸载/沙箱不支持。

详细证据：[`PR-REPORT-PETCORE-M0-M6-2026-10-20.md`](PR-REPORT-PETCORE-M0-M6-2026-10-20.md)。

---

## L002 · 2026-10-20 · P1-A 菜单/设置风格对齐

**范围**：把右键/托盘菜单与设置页的视觉/骨架对齐主分支。

**改动**

- `petcore/ui/menu_style.py`：浅/深两套 QSS，26px 行高、图标列、勾选/子菜单
  箭头、分隔符、跟随系统深浅。
- `petcore/ui/icons.py`：菜单与设置侧栏图标绘制。
- `petcore/ui/settings/style.py`：设置页侧栏 34px 行高、3px 选中强调条、页面标题
  22px、卡片/行度量、搜索状态行。
- `petcore/ui/menu_model.py` 改为布局驱动；保留 `register/unregister/
  apply_preferences` 兼容层。
- 设置 →「菜单」`MenuEditor`：树形拖拽、显隐、子菜单、分割线、别名、内置/本地
  图片图标、重置默认、预览；保存写 `config.menu_layout` 并重建菜单。
- 旧 `menu_hidden/menu_order` 迁移到新布局；mod 动作自动落入「扩展」子菜单。

**验证**

- `tests/test_petcore_*.py`：222 passed。
- 浅/深菜单与设置截图：`.scratch/petcore-evidence/{menu,settings-menu}-*.png`。
- `resolve()` 中位 0.481 ms（默认，n=5000）、0.506 ms（用户布局）；
  `MenuModel.build()` 0.315 ms（n=200）。

**遗留**：图片图标未做真实图片手工验证；菜单预览/图标选择未走完整 QInputDialog
路径；旧配置重复迁移逻辑幂等但未专测。

---

## L003 · 2026-10-20 · P1-C 窗口可移动、10–300% 大小、设置实时预览、菜单/设置一致性、文档体系

**范围**：设置/关于不再阻塞桌宠；大小滑条与手输百分比；设置实时预览 + 取消回滚；
大小默认移出右键菜单；Core 菜单项与设置入口一一对应；新增 spec/log/log-index/
README/AGENTS 文档。

**改动**

- `petcore/ui/settings/dialog.py`：`NonModal`、`show()` 复用、页面 key 跳转、
  16 ms 实时应用、打开时配置快照、取消回滚、`place_beside_pet()`。
- 新增 `petcore/ui/about.py`：非模态关于窗口；`petcore/ui/placement.py`：窗口放到
  桌宠旁边并夹在可用屏幕内。
- `petcore/ui/app.py`：`CoreHost` 持有设置/关于单例；新增 `apply_live_settings()`
  按差异应用角色/缩放/置顶/穿透。
- `petcore/ui/settings/pages_core.py`：新增「大小」卡片（10–300% 滑条 + 数字框 +
  四档预设 + 恢复默认）、「显示桌宠」、 「回到右下角」、「退出桌宠」；角色/外观
  控件变化写内存配置并发出 `changed`。
- `petcore/ui/settings/base.py`：`SettingsPage.changed`、`make_slider()`、
  `make_spin()`、`add_row(expand=)`。
- `petcore/ui/settings/style.py`：补 `QSpinBox` 与 `QSlider` 的浅/深样式。
- `petcore/engine/geometry.py`、`pet_state.py`、`config.py`：`SCALE_MIN=0.10`、
  `SCALE_MAX=3.00`、`clamp_scale()`、`Config.restore()`。
- `petcore/ui/pet_window.py`：`set_scale/set_character/set_always_on_top/
  set_click_through` 支持 `persist=False`；`set_visible()`；`_remember_geometry`
  可跳过落盘。
- `petcore/menu_templates/petcore-default-v1.json`：`size` 默认 `visible:false`；
  `petcore/ui/menu_actions.py` 增加「自定义大小…」；`petcore/ui/menu_layout.py`
  新增只迁移「未改过的旧默认布局」的 `migrate_default_size_visibility()`。
- 新增 `petcore/ui/settings/coverage.py` 与 `tests/test_petcore_settings_coverage.py`。
- 新增 `tests/test_petcore_live_settings.py`、`tests/test_petcore_about.py`；
  更新 `tests/test_petcore_settings.py`、`tests/test_petcore_menu_layout.py`。
- 新增 `docs/PETCORE-SPEC.md`、`docs/PETCORE-LOG.md`、
  `docs/PETCORE-LOG-INDEX.md`、`petcore/README.md`、`petcore/AGENTS.md`、
  `docs/PR-REPORT-PETCORE-P1C-2026-10-20.md`；更新 `docs/INDEX.md`、
  `docs/PETCORE-ARCHITECTURE.md`、`docs/PETCORE-ROADMAP.md`、根 `AGENTS.md`、
  根 `README.md`。

**验证**

- `tests/test_petcore_*.py`：**243 passed**；连同 `test_architecture.py`、
  `test_pr_report_discipline.py`、`test_ci_workflows.py`：**520 passed, 5 skipped**。
- `ruff check pet/ tests/ petcore/`：All checks passed。
- `.venv/Scripts/python -m petcore --check`：shenshen 106 段全部 webm，无诊断。
- 实机（KimiCU 真实鼠标）：设置打开时桌宠从 `left=480` 拖到 `380`；滑条设
  150% 后窗口变 `1440×810`（150% × 1.5 DPR）；取消回到 `692×389`；数字框
  237% 保存后 `config.json scale=2.37`；默认菜单无「大小」，菜单编辑器里
  `checked=off`；关于窗口打开时桌宠从 `left=0` 拖到 `99`。
- 性能：`set_scale(persist=False)` 中位 0.010 ms / P95 0.014 ms；
  `persist=True` 中位 1.415 ms / P95 2.676 ms；100 次实时缩放后 config mtime
  不变；`apply_live_settings` 空 diff/单差异中位 0.000/0.001 ms；640×360→
  1920×1080 单帧缩放中位 3.437 ms。
- 证据截图：`.scratch/petcore-evidence/p1c-{settings-size,pet-150,menu-no-size,about,desktop}.png`。

**遗留/边界**

- 本机 1 块物理屏，非模态窗口跨屏摆放未实机验证。
- macOS/Linux 焦点/窗口层级未实机验证。
- 旧用户自定义 `menu_layout`（改过顺序/显隐）不会被强行隐藏 size；只迁移
  「去掉 mod 动作后 Core 结构与旧默认完全一致」的布局。
- 237%/300% 会超出 1080p/1200p 屏幕；这是允许 300% 的直接结果。
- 设置/关于不置顶，桌宠拖动时可能盖住它们。
- 热卸载、mod 沙箱仍不支持。

---

## L004 · 2026-10-20 · P1-E 贴边按角色身体框

**范围**：修复「动画画布边框与屏幕边界碰撞」——贴边、落位、恢复、比例、跨屏迁移
全部改用 manifest `body_box × scale` 的稳定身体框，不再用整窗 640×360 画布。

**改动**

- `petcore/engine/geometry.py`：新增 `body_local_rect()`、`body_at()`、
  `clamp_body()`、`clamp_body_to_screens()`、`body_position_ratio()`；多屏用
  「身体逐点落在各屏并集」判定，屏间空洞才推到最近屏；保留旧
  `clamp_position()` / `band_bounds()` / `position_ratio()`。
- `petcore/engine/motion.py`：`clamp_to_screens(..., body_local=)` 透传；新增
  `clamp_to_screen()`。
- `petcore/platform/screens.py`：`restore_position/migrate_layout/adopt_position`
  支持 `body_local`；优先 `body_rx/body_ry`，旧 `rx/ry` 回退；默认右下角改为
  身体右缘留 `CORNER_MARGIN`、身体底贴屏底。
- `petcore/config.py`：新增顶层与 positions 的 `body_rx/body_ry`。
- `petcore/ui/pet_window.py`：缓存 `_body_local`（`_activate`/`set_scale` 更新）；
  `body_local_rect()` / `body_rect()`；`_clamp_position`、`_restore_geometry`、
  `_remember_geometry`、`handle_screens_changed`、`move_to_primary`、
  `return_corner` 全部改用身体框。
- 测试：新增 `tests/test_petcore_geometry.py`；扩展
  `tests/test_petcore_screens.py` 与 `tests/test_petcore_window_smoke.py`。

**验证**

- `tests/test_petcore_*.py`：**257 passed**（加入 body 测试后）。
- 实机（KimiCU 真实鼠标，shenshen，scale 0.72，150% DPI，逻辑屏 1280×752）：
  - 拖到左缘：窗口 x=-153 逻辑、身体左缘=0；截图 `p1d-left-edge.png`。
  - 拖到右缘：窗口 x=971 逻辑（物理 1457）、身体右缘=1280；截图
    `p1d-right-edge.png`。
  - 重启：`body_rx=1.0` 还原到同一位置（物理 left=1457），不跳位。
- 性能：`clamp_body` n=20000 中位 0.0006ms / P95 0.0009ms；
  `clamp_body_to_screens` 中位 0.002ms / P95 0.0035ms；`_body_local` 缓存后无
  每帧文件读取。

**遗留/边界**

- 本机单屏，多屏跨缝/空洞只有伪造几何单测；真实双屏未验。
- 无 `body_box` 的角色仍回退整窗（与改造前一致），需要补 manifest 才能贴边。
- 旧配置首次运行可能仍停在旧窗口位置（不会自动吸到身体边缘）；拖动一次后写入
  `body_rx/body_ry`。
- 边缘探头（P1-F）本次只写设计与路线图，代码未实现；见
  `PETCORE-ROADMAP.md` §8。

---

## L005 · 2026-10-20 · P1-F 边缘探头

**范围**：贴左/右缘后 45° 只露头/脸；点击拉直、5s 回探头；拖拽/关闭开关取消。

**改动**

- 新增 `petcore/engine/edge.py`：贴边判定、旋转投影、目标窗口 x、探头放宽域；
  `engine/` 仍零 Qt。
- 新增 `petcore/ui/edge_probe.py`：`OFF/ENTERING/PEEKING/STRAIGHTENING/
  STRAIGHTENED/RETURNING`，16ms timer + 可注入时钟。
- `petcore/ui/pet_window.py`：`set_probe_pose()`/`move_probe_to()`/`probe_bounds()`/
  `stop_for_probe()`；`paintEvent` 绕帧中心旋转；`is_transparent_at()` 反向旋转；
  拖拽/点击/自动移动/隐藏/换角色/换缩放/屏幕变化接线。
- `petcore/config.py` 新增 `edge_probe_enabled`；设置「外观与交互」复选框；
  右键「桌宠控制」勾选项；`coverage.py` 登记；默认菜单模板加节点。
- 测试：`tests/test_petcore_edge.py`、`tests/test_petcore_edge_probe.py`；
  settings/menu coverage 更新。

**验证**

- `tests/test_petcore_edge*.py`：9 passed。
- 实机（shenshen，0.72，150% DPI，单屏）：贴右缘自动 45° 探头，窗口部分出屏，
  只露头/上半身；点击后拉直，5s 后回到探头。截图
  `.scratch/petcore-evidence/p1f-right-probe.png`、`p1f-return.png`。
- 未验证：macOS/Linux 窗口出屏；非 shenshen 大身体框的旋转裁切；真实左缘
  仅由单测覆盖（本轮实机主要看右缘）。

---

## L006 · 2026-10-20 · P1-B 快捷键/自启/多实例/打包入口/首跑供给

**改动**

- `petcore/platform/hotkey_spec.py`（移植纯文本解析）、`hotkeys.py`
  （Windows `RegisterHotKey` + `WM_HOTKEY`，只绑定 `toggle_pet`）。
- `petcore/config.py` 新增 `hotkey_toggle_pet`；设置「显示/隐藏快捷键」实时重注册。
- `petcore/platform/autostart.py` 新增 `command_text()`（`sys.frozen` 区分）；
  CLI `petcore autostart on/off/status`。
- `petcore/paths.py` 支持 `instance` 数据根；CLI `--instance NAME`；`run()` 应用名带实例。
- 新增 `packaging/petcore_entry.py`、`scripts/build_petcore.ps1`（未实际构建）。
- 新增 `petcore/media/provision.py`：包装 `pet.frameseq_provision.provision_once`
  （首跑供给 + 世代退役/GC）；`run()` 启动后 daemon 线程跑一轮；CLI
  `petcore provision <character>`。
- 测试：`tests/test_petcore_platform.py`；CLI/设置测试补充。

**验证**

- `petcore autostart status` → `off`。
- `--instance test --check` → 数据根 `...\dsh-pet-core-test`，与默认隔离。
- 实机启动 petcore 后后台供给把 shenshen 的 idle/move 两段转成 frameseq；
  `--check` 显示 `{'webm': 104, 'frameseq': 2}`，无诊断。
- 未验证：真实全局按键（只测了 fake user32 的注册/失败路径）；PyInstaller
  实际构建；macOS/Linux 自启。

---

## L007 · 2026-10-20 · P1-D 角色资源包导入

**改动**

- 新增 `petcore/content/character_pack.py`：`validate_character_source()` +
  `build_character_pack()`；生成 `character.<id>` content mod，写
  `body_box/head_box/canvas/feet_y` 与可选 `move_strides/text_clips/phrases`。
- `petcore/content/pack.py` 的 `pack_legacy()` 改为复用同一构建器。
- CLI `petcore pack-character <source> --out <dir> [--id] [--name] [--version] [--copy]`。
- 测试：`tests/test_petcore_character_pack.py`、`tests/test_petcore_cli.py`。

**验证**

- 合成 source（空 `idle.webm` + manifest）→ `pack-character` 生成
  `character.clipet`，`body_box` 正确；`mods` 收集能识别该角色。
- 缺媒体、非法 body_box、非法 id、非空输出目录均返回失败。
- 未验证：真实大素材（shenshen 全量）导入；只用了合成包。

---

## L008 · 2026-10-20 · P2-A 交互、多宠、关于与打包

**改动**

- 关于：删除 `petcore/ui/about.py` / `AboutDialog` / 右键 `about`；`AboutPage` 增加桌宠数、复制诊断、项目主页；设置页仍是唯一关于入口。
- 交互：新增 `engine/physics.py`、`engine/squash.py`、`engine/spin.py`、`engine/collision.py`、`engine/feeding.py`；新增 `ui/effects.py`、`ui/motion.py`、`ui/slingshot.py`、`ui/feeding.py`；`PetWindow` 接入 Q 弹、拖动物理、弹弓、黄金回旋、探头击飞、拖文件投喂、碰撞与统一旋转/命中逆变换。
- 多宠：新增 `ui/pet_manager.py`；slot 0 主宠 + slot 1–15 子宠；`config-pet-N.json` 独立配置；生成/收回/恢复/碰撞；新增「多宠」设置页与「生小肥鱼/退出子肥鱼」菜单。
- 菜单/设置：默认模板按主分支顺序重排；新增「互动与物理」页；`coverage.py` 登记新 Core 动作；`animations_hub`、`playback_speed`、`animation_gap_seconds` 生效。
- 打包：`scripts/setup_packaging.ps1` 安装 PyInstaller 6.22.3 与 Inno Setup 6.7.3（`tools/InnoSetup`）；`scripts/build_petcore.ps1` 支持 `-Clean/-Installer/-Console` 与 `--check --out`；`packaging/petcore.iss` 使用本地中文语言文件。
- 测试：新增/更新 `tests/test_petcore_interaction.py`、`test_petcore_multipet.py`、`test_petcore_about_page.py`；更新菜单/设置/coverage/CLI 测试。

**验证**

- `pytest tests/test_petcore_*.py tests/test_architecture.py tests/test_pr_report_discipline.py tests/test_ci_workflows.py -q` → **579 passed, 5 skipped**。
- `ruff check pet/ tests/ petcore/` → All checks passed。
- `python -m petcore --check --out .scratch/petcore-evidence/p2-check.json` → 无诊断，active_pets=[0]。
- PyInstaller onedir 构建成功；`dist/dsh-pet-core/dsh-pet-core.exe --check --out dist/check.json` 通过；Inno Setup 编译成功，产物 `dist/dsh-pet-core-setup.exe`（约 153 MB）。
- offscreen 实机脚本生成主宠+2 子宠截图 `.scratch/petcore-evidence/p2-pet-{0,1,2}.png`；多宠槽位为 `[0,1,2]`。

**未验证**

- Q 弹：`tests/test_petcore_interaction.py` 覆盖几何/状态；实机截图 `.scratch/petcore-evidence/p2-click-squash-80ms.png`（点击后 80ms，progress≈0.64）。
- 安装包只验证构建，未做静默安装/卸载端到端。
- macOS/Linux 的窗口出屏、旋转命中与多宠碰撞未实机验证。
- 真实全局快捷键按键仍未自动按下验证。

---

## L009 · 2026-10-20 · P2-B1 内置离线 mod 与端口

**改动**

- `FeatureManager.load_builtin_features()` 扫描 `petcore/builtin/features`；内置 feature 受信任，不受 `allow_feature_mods` 限制；第三方 feature 仍受控。
- `contracts/host.py` 新增 `PetHandle/PetRegistryPort/InteractionPort/QuietPort` 与 `HostPorts.pets/interaction/quiet`；`contracts/feature.py` 的 `FeatureContext` 增加 `host`。
- 新增 `ui/ports.py`：QtScheduler、AudioAdapter、StorageAdapter、PetRegistry、InteractionAdapter、QuietMode；`CoreHost` 用 `build_ports` 注入。
- 新增内置 mod：`fun.self-talk`、`fun.click-talk`、`fun.divination`、`fun.festival`、`reminder.todo`、`voice.chime`、`media.music`、`tool.quick-launch`、`tool.quiet-mode`、`tool.links`、`tool.memory-trim`、`tool.network-info`；每个有菜单的 mod 都有设置页。
- 默认菜单模板加入 `tool.quick-launch:open_menu`、`tools_help`（links/network/memory）、`tool.quiet-mode:quiet_30`、`fun.divination:draw`。
- 测试：`tests/test_petcore_builtin_mods.py` 校验加载、菜单唯一、菜单 owner 有设置页。

**验证**

- 内置加载诊断为空；`CoreHost` 共 13 个 feature（含 demo）、16 个官方菜单贡献 + 1 个 demo 贡献；12 个内置 mod 设置页。
- 全量门禁同上：579 passed, 5 skipped；ruff 绿。

**未验证**

- `media.music` 只提供 Windows 系统媒体键，歌词/歌名/SMTC 未接入；`fun.festival` 只含公历固定日期，农历节日待数据包。
- `tool.memory-trim` 只在 Windows 代码路径可用；未实机回收验证。
- `tool.quick-launch` 未实机启动外部程序；`tool.links` 未在浏览器打开验证。

---

## L010 · 2026-10-20 · P3 设置聚合与可导入 mod 样板

**改动**

- 新增 `petcore/ui/settings/tabs.py`（`SettingsTabContainer`）与 `page_extensions.py`（`ExtensionsPage`）：侧边栏固定 7 页，「扩展管理」页内用标签承载原 `ModsPage` + 所有 mod `settings_page`。
- `SettingsPage` 增加 `prepare_embedded()`；`ModsPage` 增加「允许功能扩展」复选框与「重新加载扩展」按钮。
- `SettingsDialog` 不再把 contributed page 追加成侧边栏项；`ExtensionsPage` 统一委托 `dirty/draft/confirm_save/discard_changes/dispose`。
- 把 `fun.divination`、`fun.festival`、`reminder.todo`、`voice.chime`、`media.music` 从 `petcore/builtin/features/` 移到 `examples/petcore-mods/`；新增 README 与 `scripts/install_example_mods.ps1`。内置只留 7 个。
- 默认菜单删除这 5 个示例 mod 的显式节点；`tool.quick-launch:open_menu` 移入「工具与帮助」。
- 新增 `tests/test_petcore_example_mods.py`；更新内置 mod/设置页测试。

**验证**

- `pytest tests/test_petcore_*.py tests/test_architecture.py tests/test_pr_report_discipline.py tests/test_ci_workflows.py -q` → **586 passed, 5 skipped**。
- `ruff check pet/ tests/ petcore/` → All checks passed。
- `petcore mods validate examples/petcore-mods/<id>` 5/5 通过。
- 实机/无头检查：设置侧边栏 7 项；「扩展管理」标签 = 扩展管理 + 7 个内置设置页。
- 直接调用 `install()` 安装 `fun.divination` 到临时数据根成功，`load_mods(allow_feature_mods=True)` 识别为启用的 feature。

**未验证**

- 安装 5 个示例后的完整 UI 点击/卸载恢复未逐项实机操作。
- `petcore --root X mods ...` 的全局选项顺序不支持（需 `petcore mods --root X ...`），未在本轮修。

---

## L011 · 2026-10-20 · 移动朝向与位移曲线同步

**改动**

- `CharacterSpec` 新增 `move_curves`；`content/legacy.py` 的 `_move_data` 同时解析 `stride`/`curve`，`content/modcontent.py` 合并 manifest 声明路径的 curve。
- `engine/motion.py` 新增 `body_reach`、`choose_move_direction`、`quantize_move`、`move_position_at_frame`、`wander_target_y`。
- `PetWindow._begin_move`：按屏幕可达空间选方向，先设置 `state.facing` 再重建首帧；用 `quantize_move` 算整圈位移，plan 保存 curve/frames_per_loop。
- `PetWindow._on_frame`：按 clip 帧号调用 `move_position_at_frame` 写窗口位置；`_on_finished` 处理多圈重播与末拍提交；`_tick_move` 退化为守卫。
- 点击/拖拽/探头/缩放/换角色打断时统一 `_cancel_move_plan()`。

**验证**

- `tests/test_petcore_motion.py` 新增朝向/量化/curve 单测；`tests/test_petcore_move_sync.py` 用 shenshen 断言 facing 与 target 方向一致、curve 长度=帧数。
- 实机（offscreen，shenshen，0.72）：左移 plan `start_x=467 → target_x=294`，curve 241 帧；前 ~2s 静态段窗口不动，约 11s 到 target，facing 始终 left。
- 全量门禁同上：586 passed, 5 skipped；ruff 绿。

**未验证**

- 真实桌面逐帧录屏未做；曲线手感以 offscreen 位置采样与单测为准。
- 多屏/混合 DPI 下的移动空间未实机验证。

---

## L012 · 2026-10-20 · P3 收口补齐（CLI 序、扩展页兜底、重打包）

**改动**

- `petcore/cli.py`：新增 `_value_options` / `_subcommand_index` / `_resolve_root`。`main()` 先定位
  `mods` 子命令（跳过选项值，`--character mods` 不会被误判），再把前置 `--root` / `--portable` /
  `--instance` / `--mods-dir` 解析出的根目录下发给 `mods.cli`。根 `_parser()` 增加全局 `--root`，
  GUI 与 `--check` 路径同样走 `_resolve_root`。
- `petcore/ui/settings/page_extensions.py`：非 `SettingsPage` 的第三方控件不再被静默跳过，改为包进
  `_HostedWidgetPage` 并显示“不参与保存”的提示；`_pages` 类型保持 `SettingsPage`，保存/释放语义不变。
- `tests/test_petcore_cli.py::test_mods_subcommand_accepts_global_root`；
  `tests/test_petcore_settings.py::test_plain_widget_contribution_is_hosted_with_hint`；
  `tests/test_petcore_example_mods.py::test_all_example_mods_install_into_fresh_root`（5 个示例包全部
  装机到临时数据根后 `load_mods` 全部启用、无诊断）。
- 文档：`docs/PETCORE-MOD-FORMAT.md` 记录 `--root` 前置写法；`docs/PR-REPORT-PETCORE-P3-2026-10-20.md`
  同步门禁数字与新测试。

**行为变化**

- `petcore --root D:/pets mods list|doctor|install ...` 现在等价于 `petcore mods --root D:/pets ...`，
  不再落到 GUI 启动。`petcore --portable mods doctor` / `--instance foo mods doctor` 同样可用。
- 扩展作者如果只贡献了裸 `QWidget`，用户能在「扩展管理」看到它，也能看到“改动不会随设置保存”的原因。

**验证**

- `pytest tests/test_petcore_*.py tests/test_architecture.py tests/test_pr_report_discipline.py tests/test_ci_workflows.py -q`
  → **591 passed, 5 skipped**。
- `ruff check pet/ tests/ petcore/` → All checks passed；`git diff --check` 干净。
- `python -m petcore --check` 退出码 0，`data_root` 跟随 `--root`。
- 手工：`python -m petcore --root .scratch/cli-order mods doctor` 打印 `root: .scratch\cli-order`；
  `--portable mods doctor` 退出码 0；`--instance testinst mods doctor` 落在对应数据根。
- 打包：`scripts/build_petcore.ps1 -Clean -Console` 成功（内置 `--check` 通过），
  `tools/InnoSetup/ISCC.exe` 重新出包，`dist/dsh-pet-core-setup.exe` 180,473,181 字节 / 19:49:48。
  打包后的 `dist/dsh-pet-core/dsh-pet-core.exe --root .scratch/pkg-root mods doctor` 输出正确根目录，
  说明 CLI 修复已进二进制。

**未验证**

- 嵌套滚动（`SettingsPage` 自带滚动区放进设置对话框的滚动区）没有和主分支逐项并排比对；截图看着可用。
- `media.music` 仍只有 Windows 系统媒体键，无歌词/SMTC；`voice.chime` 只验证了加载，未验证真实发声。
- 多屏/混合 DPI 的移动空间、真实桌面逐帧录屏仍未实机验证。

---

## L013 · 2026-10-20 · P4 交互缺陷修复与安装器收口

**改动**

- 朝向：`engine/pet_state.py` 默认 `facing="left"`；`ui/pet_window.py::_mirrored()`
  改成 `facing == "right"` 才镜像（素材原生朝左，同主分支）。修复「位移方向和面
  朝向相反」。
- 回旋：`ui/effects.py::_begin_spin()` 补 `_ensure_timer()` + `update()`。此前点击
  动画结束后角度冻结在 0°，看起来像没触发。
- 碰边：`ui/motion.py` 用四向接触沿触发 `_on_motion_bounce()`，新增
  `BOUNCE_TRIGGER_SPEED`；删掉整个抛掷只响一次的 `_throw_bounced`。
- 探头：`engine/edge.py` 加 `EDGE_ENGAGE_TOLERANCE_PX=28` 与 `edge_snap_x()`；
  `ui/edge_probe.py` 进入前先 `stop_for_probe()` 再吸附压边。
- 多宠：`ui/pet_manager.py` 分离结果走 `_clamp_position()`；碰撞取消双方移动计划；
  拖拽中不参与碰撞结算。新增 `engine/spawn.py` 做落座候选，`_place_spawned` 改用它。
- 设置：`settings/base.py` 行控件返回 + `sub` 缩进 + `page_label_texts/reveal_text`；
  `dialog.py` 搜索纳入行文案并跳转滚动；`tabs.py` 标签条进滚动区 + 翻页箭头；
  `page_interaction.py` 子选项联动显隐；`page_mods.py` 显示「随安装包」与授权详情。
- 授信：新增 `mods/trust.py`（`.trusted` 标记）；`mods/loader.py` 支持逐包授信；
  `ui/app.py` 只认 `mod.enabled`。
- 打包：`packaging/petcore.iss` 5 个扩展改成 `[Tasks]` 可勾选、装到
  `%APPDATA%\dsh-pet-core\mods\<id>` 并投放 `.trusted`，排除 `__pycache__`；默认
  窗口化构建；`cli.py::_ensure_streams()` 兜底 `sys.stdout is None`。
- 预热：`media/sound.py` 缓存 `QSoundEffect`；`pet_window._warm_interaction_assets()`
  预热全部点击 clip 与点击音效。

**验证**

- 全量门禁：`607 passed, 5 skipped`；`ruff check pet/ tests/ petcore/` 绿；
  `petcore --check` 退出码 0；`git diff --check` 干净。
- 探针（offscreen）：`left→不镜像 / right→镜像`；点击后 10.0s 起转；
  一次抛掷触发 2 次落地反馈；开拖动物理贴边松手后吸附并 PEEKING；
  两只重叠 100ms 分开且都在屏内，拖拽时另一只不动；贴右缘生成子宠落到空位。
- 打包：`-Clean` windowed 构建 + ISCC 成功；exe `Subsystem=2`（无终端窗口）；
  `dist/dsh-pet-core-setup.exe` 180,500,361 字节，含 5 个可勾选扩展。

**未验证**

- 安装向导勾选流程未真机跑安装（避免污染本机 `%APPDATA%`）。
- 真实桌面录屏、多屏/混合 DPI、首次点击的帧时间测量都没做。
- 点击动画本身是 241 帧 / 10.04s（VPet 素材原长），所以「点击→回旋」等待偏长。

---

## L014 · 2026-10-20 · P5 气泡/设置易用性与扩展反馈、碰撞圆链

**改动**

- 气泡锚点：`PetWindow.bubble_anchor_rect()` 优先 `head_box`（回退 `body_box`/整窗）；
  `SpeechBubble._place()` 改为「上居中 → 上偏左右 → 两侧 → 下方」候选，`gap=6`，
  且不与角色相交。此前用整窗 `frameGeometry()`，气泡飘在画布顶端上方 100+px。
- 台词候选：新增 `ui/settings/text_lines.py`（`parse_lines` 兼容旧 `a | b` 单行）；
  `fun.self-talk` / `fun.click-talk` 的 `QLineEdit` 换成 `QPlainTextEdit`（一行一条），
  250ms 防抖写入；`discard_changes` 同时恢复数字与文本控件。
- Enter：`SettingsDialog.keyPressEvent` —— `Return/Enter` 直接 `accept()`（保存退出），
  焦点在多行文本框时放行换行；`Esc` 仍是取消。
- 免打扰：`QuietPage` 重写为「默认时长下拉（`quiet_minutes_default`）+ 当前状态 +
  恢复提醒 + 立即静默四档按钮」并补说明文案；`QuietMode.remaining_seconds()`；
  菜单项加 `checked`（静默中打勾）。
- **依赖缺口**：`psutil` 在 `requirements.txt` 有声明但 `.venv` 没装，打包产物里也没有
  （内置 mod 以数据分发，PyInstaller 看不到它们函数内的 `import psutil`）——所以
  「网络信息」只会回一句「缺少 psutil」，「回收内存」直接 0/0 无反馈。已装 `psutil 7.2.2`
  并在 `scripts/build_petcore.ps1` 加 `--hidden-import psutil`、`--hidden-import
  PySide6.QtTextToSpeech`（示例 mod voice.chime 同理）。
- 扩展反馈：`PresentationPort.say/notify` 增加 `force`；`_PresentationAdapter` 只在
  `force=False` 时受免打扰抑制。免打扰/回收内存/网络信息的命令反馈全部 `force=True`；
  网络信息文案重排为「流量 / 网卡 / 本机信息不联网」，设置页加「现在查看一次」。
- 菜单对勾：QSS 菜单项由样式表引擎绘制，`PE_IndicatorMenuCheckMark` 不会回调样式，
  所以改为 `menu_style.check_image_path()` 运行时生成 14px 对勾 PNG，并注入
  `QMenu::indicator:checked { image: url(...) }`；勾选项不再设置 icon（QSS 菜单里
  图标与勾选共用左边槽位，有图标就看不见勾）。`_register_feature_menus` 支持
  contribution 的 `checked` 可调用对象。
- 碰撞：`engine/collision.py` 重写为**内切三圆链** + 主分支分离规则（0.5px slop、
  每次最多 60% 重叠、1..12px、连续 3 tick 强制完整分离且上限 4×12px、稳定哈希方向）；
  `PetManager` 维护 `_overlap_streak`，每 tick 最多两轮分离；反馈阈值提到 300px/s。

**验证**

- 全量门禁：`636 passed, 5 skipped`；`ruff check pet/ tests/ petcore/` 绿；
  `petcore --check` 退出码 0。
- 探针（offscreen）：气泡底边距头部锚点 6px；贴屏幕上沿时改到侧/下方且不压角色；
  免打扰页三段式布局与「静默中，剩余约 15 分钟」状态；菜单勾选行可见 ✓；
  两只 shenshen 重叠后 30 tick 内三圆链不再相交。

**未验证**

- 真实桌面观感（菜单勾选在 Windows 11 主题下的清晰度、气泡与毛发边缘的相对位置）
  需要实机确认。
- 圆链碰撞的「贴合距离」与旧椭圆不同（中间更早碰、四角更晚碰），手感待实机确认。

---

## L015 · 2026-10-20 · 多宠碰撞瞬移与被撞斜修复

**症状**：多宠碰撞时被撞的桌宠偶尔「闪现到屏幕另一端」，有时还会保持 45° 探头姿态。

**根因**

1. **瞬移**：`MotionController.set_velocity()` 把模式切到 `thrown` 时没有把内部坐标
   `_x/_y` 锚到窗口当前位置。`_x/_y` 只在拖拽/抛掷里维护，`normal` 期间是构造时的
   (0,0) 或上一次抛掷的旧值；下一个 tick `_tick_throw()` 从旧坐标出发并 `window.move()`，
   整只宠物就瞬移到那个旧位置（通常是出生点/屏幕另一角）。
2. **被撞斜**：`PetManager._collide_pair()` 对探头姿态（`probe_active`，身体按设计探出
   屏幕且整帧旋转 45°）的宠物照常做位置分离。分离把窗口推回屏内，而 `_probe_angle`
   仍停在 45°、`_probe_exposure` 仍是 0.55 —— 看起来就是「被撞斜成探头的样子」。

**改动**

- `petcore/ui/motion.py::set_velocity()`：切 `thrown` 前 `_x/_y = window.x()/y()`，
  并重置 `_last_tick`；注释写明不允许省这一步。
- `petcore/ui/pet_manager.py::_collide_pair()`：先按圆链判是否真的重叠；确认撞上后，
  对探头中的一方 `_edge_probe.cancel("collision", restore=True)`（回到探头前的屏内 x、
  姿态复位直立），然后用新姿态重算一次分离。只是「靠近」不触发退出。
- 测试：`tests/test_petcore_p5.py` 新增 3 条——`set_velocity` 重锚、真实窗口 6 个事件循环
  不跳变、探头被撞后 `probe_active=False`/角度归零/身体贴回屏内；另一条 30 tick 逐帧
  位移 <120px 的跳变护栏。

**验证**

- 探针（offscreen，shenshen 0.5）：窗口在 (400,400) 收到 `set_velocity(-300,0)` 后内部
  坐标即 400,400（修复前是 0,0，下一 tick 直接跳到左上）；探头中的桌宠在
  `(-146,300)/45°` 被撞后变成 `(-106,235)/0°`、exposure 1.0、body.x=0。
- 全量门禁：`640 passed, 5 skipped`；`ruff check pet/ tests/ petcore/` 绿。

**未验证**

- 真实桌面下的手感（碰撞把宠物推开的速度、探头退出时机）需要实机确认。

---

## L016 · 2026-10-20 · petcore/net 传输层单测

**改动**

- 新增 `tests/test_petcore_net.py`（243 行，新增文件）：覆盖 `urlopen` 的「代理失败→直连」兜底与进程级固化、直连固化后只试一次、`reset_transport()` 复位、`HTTPError` 不重试、双路失败抛最后一次异常、自定义 `opener=` 不固化结论；`classify_http_status` / `http_error`（含 `Retry-After`）/ `classify_exception` / `describe` 的分类矩阵。33 条测试，纯标准库、不联网；autouse fixture 前后 `reset_transport()`。
- 未改 `petcore/net/*.py` 语义。

**行为说明**

- 需求里的「假 opener 工厂 + 断言 `force_direct()` 变 True」与产品口径冲突：只有 `opener is None`（走默认 `_opener`）才固化。因此兜底相关用例改为 `monkeypatch.setattr(petcore.net.http, "_opener", factory)` 走默认路径；自定义 `opener=` 不固化单独成一条。

**验证**

- `.venv/Scripts/python -m pytest tests/test_petcore_net.py -q` → `33 passed`。
- `.venv/Scripts/ruff check tests/test_petcore_net.py` → 绿。

**未验证 / 风险**

- `tests/test_petcore_architecture.py::test_no_network_stack_in_core` 仍红：守卫禁止全包 import `urllib`/`http`，而新增的 `petcore/net/` 正是唯一允许的联网出口，守卫未给 `net/` 豁免。与本次测试无关，属既有分支问题。

---

## L017 · 2026-10-20 · 待机掷骰、可调概率与手动播放菜单

**症状**：桌宠几乎一直待机，随机动作（random/ 89 个）从不触发，偶尔才见到转向或漫游。

**根因（真实素材探针）**：`PetWindow._on_finished()` 每次都先 `player.advance()`；
IDEL 素材是 `B_LOOP + loop=True`，`AnimationPlayer.advance()` 原样返回同一个 idle spec，
`_play_spec()` 又播一遍待机 ⇒ 永远到不了 `director.choose()`。主分支是「待机播完一圈
→ `_roll_next()` 掷骰」，掷骰链只在点击/拖拽/手动播动画结束后才会被走到。

**改动**

- `ui/pet_window.py`：`_on_finished()` 对 IDEL B_LOOP 不再走 `advance()`，圈末
  `_roll_next()`；新增 `_is_idle_dwell/_finish_action/_roll_next/_behavior_weights/_gap_active`。
  `animation_gap_seconds` 从「QTimer 停住等 gap」改为「gap 期间继续播待机、到点掷骰」，
  删掉 `_gap_timer/_gap_action/_gap_token`。同一 clip 重播只 `clip.start()` re-arm，不
  `stop()`。`play_animation()` 按 GraphType 映射动作类型（idle/turn/move/click/act），
  并先取消 gap。
- `engine/behavior.py`：新增 `DEFAULT_WEIGHTS=(0.30,0.10,0.40,0.20)`、`normalize_weights()`；
  `BehaviorDirector` 支持 `weights`/`weights_provider`，每次 `choose()` 读权重。
- `config.py`：新增 `anim_weight_idle/turn/act/move`（30/10/40/20，0–100）。
- `ui/settings/page_interaction.py`：「动画」卡片新增「自主行为概率」四个百分比；
  改控件即写 config，下一次掷骰生效。
- `ui/menu_actions.py`：「播放动画」按待机/转向/移动/点击回应/随机动作/事件分组，
  叶子播一次，播完回掷骰链。
- 测试：新增 `tests/test_petcore_playback.py`（10 条）；`tests/test_petcore_behavior.py`
  追加权重/归一化 5 条；`tests/test_petcore_settings.py` 的页数与搜索索引跟随 8 页。

**验证**

- `tests/test_petcore_*.py`：`442 passed`；全量门禁 `733 passed, 5 skipped`；ruff 绿。
- 真实素材 60s offscreen 探针（`playback_speed=2.0`）：9 次状态切换
  `idle 3 / act 3 / move 1 / turn 2`，随机动作「原地小跳」等实际播放（修复前只会 idle）。
- 掷骰分布（shenshen，2000 次）：idle 622 / turn 211 / act 774 / move 393 ≈ 30/10/40/20。

**未验证**

- 真实桌面下 WebMClip 的圈末软停/re-arm 是否完全不换代（探针为 offscreen）；
  gap 时长的手感需实机确认。

---

## L018 · 2026-10-20 · 事件动画与 play_event

**改动**

- `engine/enums.py`：新增 `GraphType.EVENT = "Event"`。
- `contracts/content.py`：`AnimationSpec.event_group: str = ""`。
- `content/legacy.py`：`events/` 映射为 EVENT/SINGLE，`events/balance/x.webm` 取
  `event_group="balance"`（此前整棵 events 树落进 DEFAULT，被当随机动作播）。
- `engine/catalog.py`：`specs_for_event()/event_groups()/event_names()`。
- `ui/pet_window.py`：`play_event(group, variant)`：名字（可带目录前缀）或排序索引，
  留空随机；按 act 播放，播完回掷骰链。
- 端口：`PresentationPort.play_event`、`PetHandle.play_event`；`_PresentationAdapter`
  与 `PetHandleImpl` 转发。

**验证**

- 真实角色包：`Default 89 / Event 6 / Touch_Body 5 / Move 4 / Idel 1 / Raised_Static 1`
  （修复前 Event 不存在，6 个余额动画混在 Default）。
- `event_groups() == ("balance",)`，`play_event("balance", 名字/0)` 均命中；
  `act()` 不再返回余额动画。测试随 `test_petcore_playback.py` 与 catalog/contracts 用例。

**未验证**

- 余额档位到动画名的映射仍由后续 account/余额 mod 决定；本批只提供语义入口。

---

## L019 · 2026-10-20 · core 联网 allowlist、API 能力与设置页、keyring 打包

**改动**

- 结构守卫（`tests/test_petcore_architecture.py`）：`test_no_network_stack_in_core`
  改为 allowlist（`net/`、`api/`、`account/` 之外仍禁网络库）；新增
  `test_mods_do_not_import_core_network_or_api`（builtin + examples 不得 import
  `petcore.net/api/account`）。L016 里记录的既有红灯由此收口。
- `petcore/net/`：`http.py`（移植主分支 `pet/http_util.py`：代理先试、传输失败改直连并
  固化、`HTTPError` 不重试）、`errors.py`（auth/rate_limit/network/timeout/protocol 五类）。
- `petcore/contracts/api.py`：`ApiMetadata/ApiRequest(repr 不含 key)/ApiPort`；
  `contracts/host.py` 的 `HostPorts` 新增 `api`。
- `petcore/api/`：`spec.py`（endpoint/服务校验）、`store.py`（`api.json` 原子写、
  坏文件备份、订阅、版本号）、`credentials.py`（keyring 惰性导入 + 内存兜底 +
  `persisted()` 回读）、`probe.py`（文本探测，不读响应体）。
- 设置页 `ui/settings/page_api.py`（「API 与账户」）：服务列表/表单/密钥/测试连接
  （QThreadPool + 信号回主线程）；`settings/dialog.py` 侧边栏 7→8 页；菜单
  「工具与帮助 → API 与账户」+ `coverage.py` 登记。
- `ui/ports.py`：`CoreApiPort`（metadata/resolve/subscribe/open_settings），
  `build_ports()` 注入 `api` 与 `credentials`；`CoreHost` 持有共享的 `ApiStore`/
  `KeyringCredentials`。
- `ui/settings/dialog.py`：`_save_pages()` 改为返回布尔——任一页面 `confirm_save()`
  显式返回 `False`（API 表单校验失败）时弹提示并**不关闭**设置窗口，不再静默丢弃输入。
- 依赖与打包：`.venv` 安装 `keyring 25.7.0`（`requirements.txt` 已声明）；构建脚本补
  `--hidden-import keyring.backends.Windows`、`--collect-submodules keyring/win32ctypes`。
- 文档：SPEC §3/§4（规则 18–21）/§10、AGENTS 红线、ROADMAP §16、ANIMATION-BACKLOG、
  ARCHITECTURE 分层、README；`petcore/AGENTS.md` 侧边栏页数同步。

**验证**

- `tests/test_petcore_net.py` 34 条、`tests/test_petcore_api.py` 41 条全绿；全量
  `733 passed, 5 skipped`；ruff 绿；`petcore --check` 退出码 0。
- 真实 Windows 钥匙串回读：`available=True`、`set→get→persisted=True→delete→None`。
- `api.json` 探针：只含 base_url/model/timeout 等服务字段；`ApiRequest.resolve` 能取到
  9 位密钥但 `repr` 不含密钥。
- offscreen 播放探针见 L017（不是本条的 API 证据，单独列出）。

**未验证 / 风险**

- 打包已重出：`dist/dsh-pet-core-setup.exe` 185,100,188 字节，主 exe Subsystem=2；
  `--check` 退出码 0；冻结包 PYZ 内已核对 `keyring.backends.Windows` 与
  `win32ctypes.pywin32.win32cred`。
- 仍未验证：打包版在真实桌面里实际读写钥匙串；真实 HTTPS 端点与自签握手。
- 真实 HTTPS 端点（如 DeepSeek）与 `verify_ssl=False` 的自签握手未端到端验证；
  设置页嵌入设置对话框后的搜索/Enter/取消回滚只有页面级与对话框级测试。

---

## L020 · 2026-10-21 · 探头击飞旋转与飞行接管态

**症状**：探头状态下被撞飞没有整帧旋转；击飞到稳定前会切到别的动画；稳定前点击桌宠会出现两个位置的桌宠在闪。

**根因（对照主分支）**

1. `arm_throw_egg()` 只在 `MotionController.begin_throw()` 且 `probe_active` 时调用；探头一被拖拽
   `edge_probe.on_drag_started()` 先 `cancel("drag_away")`，松手时 `probe_active` 已 False。
   主分支的授权口是碰撞 `edge_probe.cancel("collision_throw")`（`pet/edge_probe.py:283-287`），
   petcore 的 `_collide_pair()` 只 cancel、没有 arm。
2. 主分支把拖拽/飞行当接管态：不掷骰、点击返回 False、`_effects_filter_switch` 只允许
   idle/turn。petcore 的 `_on_finished()` 不看 `motion.thrown`、`_on_click()` 不看飞行态、
   `_play_spec()` 没有素材闸门。

**改动**

- `ui/pet_manager.py`：碰撞确认圆链重叠后，先对探头窗口 `effects.arm_throw_egg()`，再
  `edge_probe.cancel("collision", restore=True)`；低速碰撞没进入抛掷时立即 `end_throw_egg()`。
- `ui/motion.py`：删掉 `_throw_egg_armed` 单一来源；`_tick_throw()` 只在
  `effects.throw_egg_active` 时更新角度；`stop()` 无条件回正；进入 `thrown` 时回调
  `window._on_thrown()`（碰撞抛掷也绑飞行/拖拽素材）。
- `ui/edge_probe.py` / `ui/pet_window.py`：`on_drag_started()` 返回「拖拽前确实在探头」，
  松手抛掷时据此授权击飞旋转（拖出去甩飞也能触发；不只碰撞）。
- `ui/pet_window.py`：
  - `_play_spec()` 加 `_effects_lock_active()` 闸门：探头/彩蛋激活时非 idle/turn 一律降级待机，
    且不翻朝向；
  - `_on_finished()` 在 `motion.thrown` 时只 re-arm 当前素材、不掷骰；
  - `_on_click()` 在 `motion.thrown` / 彩蛋激活时直接返回；
  - `_play_throw_animation()` 回退顺序 RAISED_DYNAMIC → RAISED_STATIC → idle；
  - 新增 `_on_thrown()`（停 gap/移动计划、绑飞行素材）。

**验证**

- 新增 5 条用例（`tests/test_petcore_playback.py`）：探头只播 idle/turn 且不翻朝向、
  彩蛋激活时 act 降级、thrown 圈末不掷骰且 re-arm、飞行中点击不改素材、碰撞后
  `throw_egg_active=True` 且角度 90°、`stop()` 后归零。
- 全量：`775 passed, 5 skipped`；ruff 绿；`petcore --check` 退出码 0。

**打包/图标**

- `-Clean -Installer` 重出：`dist/dsh-pet-core-setup.exe` 185,740,254 字节；
  `ExtractAssociatedIcon` 取 setup 与主 exe 图标均为鲸鱼娘；主 exe `--check` 退出码 0，
  `mod_dirs` 含 `<安装目录>/mods`。

**未验证**

- 真实桌面手感：碰撞撞飞的速度阈值、旋转跟随的观感；offscreen 只到状态与角度。

---

## L021 · 2026-10-21 · 安装器图标/扩展安装位置与扩展管理重排

**改动**

- `packaging/petcore.iss`：新增 `SetupIconFile=..\assets\icon.ico`、
  `UninstallDisplayIcon={app}\dsh-pet-core.exe`；5 个随附扩展的 `DestDir` 由
  `{userappdata}\dsh-pet-core\mods\<id>` 改为 `{app}\mods\<id>`（`.trusted` 同目录）。
- `scripts/build_petcore.ps1`：PyInstaller 加 `--icon assets/icon.ico`。
- `petcore/paths.py`：新增 `is_frozen/install_root/install_mods_dir/user_mods_dirs`；
  `mods_dirs()` 在冻结包中纳入 `{app}/mods`。
- `petcore/mods/loader.py`：feature 的「用户 mod 根」= 数据目录 + 安装目录；
  `.trusted` 逐包授权不变。
- `petcore/ui/app.py`：加载 feature 的 allowed roots 同步为 `paths.user_mods_dirs()`。
- `petcore/ui/settings/page_mods.py`：加「全部/基础/加装」过滤；列表与详情优先中文名；
  `.trusted` 包禁用卸载并提示「随安装程序卸载」。
- `petcore/ui/settings/page_extensions.py`：横向页内标签改为**左侧竖向导航**
  （`#extensionsNav`，基础/加装分组，独立滚动）+ 右侧 `QStackedWidget`；`tabs` 保留为
  兼容门面（`labels/currentKey/setCurrentKey`）。
- `petcore/mods/manifest.py` + `contracts/contributions.py` + `mods/feature.py`：
  `name_zh` 字段与 `SettingsPageSpec.owner/label/group`；13 个内置/示例 `mod.json` 补中文名。

**验证**

- 新增 `tests/test_petcore_installer.py`（4 条）：setup 含鲸鱼娘图标、模组路径不含
  `{userappdata}` 且都在 `{app}\mods`、frozen 模拟下 `install_mods_dir()` 生效、
  安装目录 `.trusted` feature 在全局开关关闭时仍加载。
- 新增 `tests/test_petcore_extensions_nav.py`（14 条）：分组/中文名/过滤/兼容门面/卸载禁用。
- 全量：`775 passed, 5 skipped`；ruff 绿；`petcore --check` 退出码 0。

**未验证**

- 真实安装到自定义目录（含 Program Files 权限）与卸载清理未跑；仅做路径/加载逻辑测试。

---

## L022 · 2026-10-21 · 彩蛋图片与点击音效包

**改动**

- `petcore/ui/easter_egg.py`：`EasterEggController` + 无边框置顶弹窗；目录默认
  `assets/big_blue_fat_fish`（24 张），点击/Esc 关闭，连续打开按 24px 错位。
- `petcore/ui/pet_window.py`：`show_easter_egg()`；初始化 `EasterEggController`，
  `shutdown()` 统一关闭。
- 菜单「工具与帮助 → 彩蛋图片」`easter_egg` + `coverage.py` 登记；设置 →「互动与物理」
  新增「彩蛋」卡片（开关/标题/目录/清空/预览）。
- `petcore/media/click_sound.py`：`click_sound_pack` 解析（builtin default/duck、
  自定义文件/文件夹）与随机选择；duck 的 `Ya1/Ya2.mp3` 用 imageio-ffmpeg 转为
  44.1kHz/16-bit 单声道 wav（0.238s / 0.108s）。
- `petcore/media/sound.py`：wav 继续 `QSoundEffect`；非 wav 增加 `QMediaPlayer +
  QAudioOutput` 缓存兜底。
- `petcore/ui/pet_window.py`：点击/碰边/碰撞音效统一走 `click_sound_pack` 随机候选；
  `_warm_interaction_assets()` 预热全部候选。
- 设置页「点击反馈」加「音效音源」下拉 + 路径 + 选择文件/文件夹 + 试听。

**验证**

- 新增 `tests/test_petcore_click_sound.py`（10 条）与 `tests/test_petcore_easter_egg.py`（6 条）。
- 真实资产探针：`duck` 候选 `Ya1.wav/Ya2.wav`；`EasterEggController` 在真实
  `assets/big_blue_fat_fish` 上找到 24 张图并成功弹窗。
- 全量：`775 passed, 5 skipped`；ruff 绿。

**未验证**

- 真实声卡试听 duck/自定义 mp3（测试与 conftest 静音）；弹窗在多屏/真实主题下的层级。

## L023 · 2026-10-21 · P8 多宠推动、气泡跟随、设置重组与打包回归

**改动**

- **多宠碰撞/移动**：`pet_manager.py` 轻碰不再 `_cancel_move_plan()`、不再写速度；
  只有 `impact_speed >= HIT_FEEDBACK_MIN_SPEED` 才 `motion.set_velocity()`（切 thrown）。
  分离后按实际位移 `shift_move_plan(dx,dy)` 继续漫游；探头宠物轻碰时按
  `static=True`（无限质量）处理，只推开对方，真撞击才 `arm_throw_egg()` +
  `cancel("collision")`。`pet_window.py` 新增 `shift_move_plan/abort_move_plan`。
- **子宠退出**：`quit_from_menu()`：主宠退应用，子宠只 `PetManager.close_slot()`；
  菜单文案子宠为「退出这只」。
- **气泡**：`bubble.py` 重写，新增 `reposition()` 与 `show_image()`；`PetWindow.moveEvent`
  同帧合并后跟随，`set_scale()` 后重排。彩蛋 `show_easter_egg()` 默认进气泡，
  `easter_egg_display=window` 才用独立弹窗。`config.py` 新增该键。
- **彩蛋菜单行**：`easter_egg_entry.py` 27px 头像 + 标题 + 「请点击」；`MenuItem.builder`
  支持 `QWidgetAction`。
- **菜单模板打包回归**：`menu_layout.py` 新增 `BUILTIN_DEFAULT_LAYOUT` /
  `load_default_with_source()` / `merge_user_layout()`；`app.py` 把合并结果落盘；
  `cli.py --check` 输出 `menu_template`；`build_petcore.ps1` 补
  `--add-data petcore/menu_templates`。根因是旧产物 `_internal/petcore/` 只有
  `builtin/`，旧 `menu_layout` 无模板可合并，右键缺「播放动画/彩蛋图片」。
- **安装向导路径**：`petcore.iss` 加 `DisableDirPage=no`、`UsePreviousAppDir=yes`、
  `DefaultDirName={code:GetDefaultDir}`，`[Code]` 读卸载键 `InstallLocation`
  预选旧路径。
- **设置 IA**：`SettingsPage.tab()/embed_page()` 页内分页基础设施；
  「互动与物理」拆 8 页（点击/点击台词/自言自语/拖拽/碰撞/动画/彩蛋/免打扰），
  「菜单」拆菜单编排/快捷启动，「关于与诊断」拆版本诊断/链接/网络/回收内存。
- **Core 内置特性**：`CORE_FEATURE_IDS` + `SettingsPageSpec.host_page`；7 个内置
  feature 归 core，设置页内嵌到对应 Core 页，不再进扩展导航。
- **扩展管理**：导航顺序 扩展管理 → 基础扩展 → 加装扩展，组头可折叠；
  setup 的 `.trusted` 与用户导入都归加装；Core 内置项标「已并入 Core」并禁用启停/卸载。
- **文档**：新增 `docs/PETCORE-MAIN-PARITY-BACKLOG.md`；更新 SPEC/ROADMAP/
  ARCHITECTURE/MOD-FORMAT/INDEX/README/petcore README/AGENTS。

**验证**

- `QT_QPA_PLATFORM=offscreen pytest tests/test_petcore_*.py tests/test_architecture.py tests/test_pr_report_discipline.py tests/test_ci_workflows.py -q`
  → **823 passed, 5 skipped**。
- `ruff check pet/ tests/ petcore/` → 全绿。
- `.venv/Scripts/python -m petcore --check` → exit 0，`menu_template=json`，
  `menu_layout_diagnostics=[]`。
- 真实旧 `menu_layout` 合并探针：补回 `animations_hub`、`playback_speed`、
  `drag_physics`、`spawn_pet`、`golden_spin`、`tools_help`（含 `easter_egg`）。
- 模板缺失模拟：`menu_template=builtin`，菜单仍完整；warning 只输出一次。
- 性能探针（offscreen）：`collide()` 6.9µs/对、`bubble.reposition` no-op 1.4µs、
  `load_default+resolve` 282.8µs、QMenu(12 项) 271.4µs（见 P8 PR 报告）。
- Inno `ISCC.exe` 编译成功（72.890s）；`build_petcore.ps1 -Clean -Installer` 产物：
  setup **178,532,356 字节**，主 exe `Subsystem=2`，
  `_internal/petcore/menu_templates/petcore-default-v1.json` 存在，打包后 `--check`
  exit 0 且 `menu_template=json`。图标提取为鲸鱼娘（32×32 目视）。

**未验证**

- 真实桌面手感：多宠互推、探头被轻碰、气泡跟随拖拽/漫游、子宠单独退出。
- 安装向导每次显示路径页与重装预选旧路径（会真装机，未自动执行）。
- 真实声卡音效与多屏彩蛋层级。

**后续**

- P9 从账户/额度开始（`petcore/account/`），再进 worker/AI；差距清单见
  [`PETCORE-MAIN-PARITY-BACKLOG.md`](PETCORE-MAIN-PARITY-BACKLOG.md)。

## L024 · 2026-10-21 · P8.1 字号/彩蛋图片、菜单结构、弹弓与直播兼容

**改动**

- **字号/图片大小**：`ui_font_size`(10–18)、`bubble_text_scale`(50–300)、
  `easter_egg_image_scale`(50–300)、`self_talk_image_scale`(50–300)；
  `bubble.set_text_scale()/show_image(image_scale=)`；菜单/设置/彩蛋菜单行跟随字号。
- **自言自语配图**：`self_talk_image_chance`(默认 30)、`self_talk_image_dir`；
  `PresentationPort.show_image()`；设置页新增配图三行；出图失败回退文字。
  点击台词改 `say(force=True)`。
- **菜单结构**：`easter_egg` 第一项；`playback_speed` 默认隐藏；尾部固定
  `modern_settings → separator → quit`；新增幂等 `migrate_p8_menu()` 并在
  `CoreHost.reload_menu()` 写盘。
- **播放动画预览**：分类子菜单 `aboutToShow` 懒加载；新增
  `petcore/media/thumbnails.py`（帧序列/GIF/WebM 代表帧、LRU 128、磁盘缓存、2 线程）；
  随机动作按前缀分组；`menu_animation_icons` 可关。
- **弹弓右键**：`SlingshotController.suppress_until` + `PetWindow._suppress_context_menu`；
  瞄准/发射后 0.35s 内不构建右键菜单。
- **点击监听**：`InteractionAdapter.attach_window()/detach_window()`；`PetManager`
  在 spawn/restore 后补挂，子宠点击台词立即生效。
- **设置 X 保存**：`dialog._commit()`；`closeEvent` 保存关闭；取消/Esc 仍恢复快照。
- **直播捕获兼容**：`stream_capture_mode`；宠物窗口与气泡从 Tool 切普通顶层
  `Window`，`apply_stream_window()` 清 Win32 Tool/NoActivate 位；设置「外观与交互」
  新增开关；多宠每窗可捕获。
- **文档**：SPEC 规则 33–40 + §13、ROADMAP §19、AGENTS 新红线、
  parity backlog、PR 报告 P8.1。

**验证**

- `QT_QPA_PLATFORM=offscreen pytest tests/test_petcore_*.py tests/test_architecture.py tests/test_pr_report_discipline.py tests/test_ci_workflows.py -q`
  → **875 passed, 5 skipped**。
- `ruff check pet/ tests/ petcore/` → 全绿；`petcore --check` exit 0，`menu_template=json`。
- 真实旧 config 副本迁移后：顶层第一项 `easter_egg`，播放速率隐藏，尾部
  `modern_settings/separator/quit`；动画分类展开前 0 项，展开后 1 项且图标非空。
- 缩略图实测：帧序列 18.2ms/张，WebM 668ms/张（后台 2 线程）。
- 打包：`-Clean -Installer` 见 PR 报告 §5.3。

**未验证**

- 真机目视：字号 10/18、气泡 300%、彩蛋图片 300% 与 DPI。
- OBS/直播姬真实捕获（需选 Windows 10 1903+）。
- 真实 WebM 角色首展开缩略图等待手感。
- 安装向导旧路径预选与卸载清理（会真装机）。

**后续**

- P9：账户/额度 → worker/AI；其余差距见 parity backlog。

## L025 · 2026-10-21 · P8.2 隐藏态气泡、配图合并、托盘菜单

**改动**

- **隐藏态不再冒泡**：`PetWindow.say()` / `show_image()` 先判 `isVisible()`；
  `set_visible(False)` 收起气泡。`_PresentationAdapter._window()` 优先选可见桌宠，
  避免自言自语/提醒打到隐藏窗口上。
- **配图设置合并**：「互动与物理 → 彩蛋与配图」同时收纳彩蛋图片（目录/大小/标题/
  显示方式）与自言自语配图（目录/大小/出图概率）；`fun.self-talk` 设置页只保留
  节奏与候选台词，避免两个入口互相覆盖。
- **托盘菜单**：新增 `CoreHost.build_tray_menu()`：显示/隐藏桌宠、显示全部/隐藏全部
  （多宠时）、桌宠设置、打开数据目录、重新加载扩展、逐宠「显示/隐藏、回到右下角、
  退出这只」、退出。`TrayIcon` 左键/双击切换显示；菜单替换延迟到下一事件循环，
  旧菜单延后释放，避免在菜单动作执行中销毁菜单。
- 文档：SPEC §13、AGENTS 隐藏态/托盘/配图规则、本 log、P8.1 PR 第二轮改进。

**验证**

- `pytest tests/test_petcore_*.py tests/test_architecture.py -q` → **598 passed**。
- 新增 `tests/test_petcore_tray.py`（3 条：隐藏后菜单显示「显示桌宠」并能唤回、
  可见时「隐藏桌宠」、逐宠子菜单与退出）与隐藏态气泡测试。
- `ruff check` 相关文件全绿；最终门禁 `879 passed, 5 skipped`。
- 重打包：`dist/dsh-pet-core-setup.exe` **185,805,307 字节**（ISCC 99.860s），
  主 exe `Subsystem=2`，打包后 `--check` exit 0、`menu_template=json`。

**未验证**

- 真实 Windows 托盘单击/右键与菜单替换的实机手感（offscreen 只验证对象与动作）。
- 隐藏态下各 mod 的自定义气泡是否都走 presentation 端口（第三方导入包需自查）。

**后续**

- 重打包安装包并做一次托盘/隐藏态实机验收。

## L026 · 2026-10-21 · P8.3 托盘菜单、自语/点击出图

**改动**

- **托盘右键菜单**：`TrayIcon` 收到 `Context` 不再 `refresh()`（原先会在 Qt 正要
  弹出时替换菜单，导致什么都不显示）；托盘菜单改为**原地重建动作**
  （`QMenu.aboutToShow` → `CoreHost.populate_tray_menu()`），不再替换 context menu；
  QSystemTrayIcon 父对象改为 None，避免桌宠窗口隐藏/销毁影响托盘。
- **自语/点击出图**：新增 `petcore/media/talk_images.py`（目录解析、可读图过滤、
  `pick_choice()` 先决定出图/出文字、概率/大小读取）。`fun.self-talk` 与
  `fun.click-talk` 共用；出图失败回退文字；`_speak()` 用 try/finally 保证任何异常
  后仍排下一次；日志记录 `kind/dir/images/shown`。
- **诊断**：`--check` 新增 `self_talk_images`（目录、文件数、可读图数、内置目录），
  可在安装后的 exe 上直接验证素材与 Qt 图片插件是否正常；feature 加载诊断写入
  `petcore.log`。
- **设置**：「彩蛋与配图」新增「当前目录」只读信息与「预览配图」按钮；`PetWindow`
  在 `show_image()` 返回 True 后再检查气泡可见性，不可见则回退文字。
- 文档：SPEC/AGENTS 规则、P8.1 PR「第三轮改进」。

**验证**

- `pytest tests/test_petcore_*.py tests/test_architecture.py tests/test_pr_report_discipline.py tests/test_ci_workflows.py -q`
  → **887 passed, 5 skipped**；ruff 全绿。
- 新增/更新测试：自语 100% 出图、坏目录回退内置、异常后仍排下一次、点击台词出图、
  托盘 Context 不替换菜单、`--check` 图片字段。
- 源码探针（真实 config 副本）：chance=100 出 160×160 图片；坏目录回退内置仍出图；
  点击出图；托盘隐藏后 `aboutToShow` 首项变「显示桌宠」且能唤回。
- 打包后 `dist/dsh-pet-core/dsh-pet-core.exe --check`：`self_talk_images.readable=24`
  （内置目录 24 张全部可读），说明安装包素材路径与 Qt 图片插件正常；
  setup **185,797,398 字节**（ISCC 100.859s），主 exe `Subsystem=2`。

**未验证**

- 真实 Windows 托盘右键弹出与菜单动作（offscreen 只验证对象/信号）。
- 安装后 `--check` 的 `self_talk_images.readable` 是否仍为 24（本轮构建后实测）。

**后续**

- 若安装包 `readable=0`，说明是 Qt 图片插件/打包路径问题，按 `--check` 输出继续定位。

## L027 · 2026-10-21 · P8.4 鼠标穿透归位托盘、彩蛋/配图高清

**改动**

- **右键菜单摘掉「点击穿透」**：`menu_templates/petcore-default-v1.json` 与
  `menu_layout.BUILTIN_DEFAULT_LAYOUT` 删除 `click_through`；`menu_actions.core_items`
  与 `settings/coverage.py` 同步移除；新增 `migrate_p9_menu()`（任意层级摘除该项 +
  清理子菜单多余分割线，幂等），`CoreHost.reload_menu()` 串行跑 p8 → p9 迁移。
- **托盘菜单接管**：`populate_tray_menu()` 新增可勾选「鼠标穿透」（紧跟显示/隐藏），
  `CoreHost.all_click_through()` / `set_all_click_through()` 一次作用到所有桌宠；
  开启后主宠气泡提示「已开启鼠标穿透，可从托盘菜单关闭」；不在托盘动作里
  `refresh()`（菜单弹出期间 `clear()` 会删掉正在发信号的 QAction），勾选态由下一次
  `aboutToShow` 重建刷新。设置页「外观与交互 → 点击穿透」保留为第二入口。
  注意 `PetWindow.say()` 只接受 `(text, duration_ms)`，没有 `force`（提示别照抄
  feature 端口的 `say(force=True)`）。
- **图片装载新模块** `petcore/media/images.py`：`sanitize_dpr` / `screen_dpr` /
  `physical_side` / `scale_pixmap` / `load_pixmap` / `logical_size`。规则＝逻辑上限 ×
  屏幕 DPR = 物理解码尺寸，pixmap 写回 DPR；小图按 DPR 预放大以保持逻辑尺寸。
- **彩蛋弹窗**：`load_scaled_pixmap(path, max_side, dpr)` 改走新模块；
  `EasterEggPopup(..., dpr=)` 记录 `device_pixel_ratio`，图片 label 与窗口尺寸改用
  逻辑尺寸；`EasterEggController.show()` 传宠物所在屏的 DPR。
- **气泡配图**：`_scaled_pixmap(image, max_side, dpr)`、`show_image(..., dpr=)`；
  `image_label` 尺寸用 `logical_size()`；`PetWindow.show_image()` 传
  `screen_dpr(self)`。
- **菜单头像**：`easter_egg_entry._round_pixmap(pixmap, dpr)` 物理边长 27×DPR，
  `avatar_pixmap()` 按 DPR 预缩放后裁圆。
- **气泡同图层**：`SpeechBubble` 新增 `_window_flags()` / `_apply_window_flags()` /
  `set_host_on_top()`（默认置顶），并补 `WindowDoesNotAcceptFocus`；
  `PetWindow._apply_flags()` 调 `bubble.set_host_on_top(self.always_on_top)`；
  `EasterEggPopup(..., on_top=)` 跟随宿主置顶开关。修「图片气泡和桌宠不在一个图层」
  —— 桌宠默认置顶而气泡不是，图片气泡比文字气泡大、会压到桌宠身上就露馅。
  同主分支 `pet/speech_bubble.set_host_on_top`。
- 测试：新增 `tests/test_petcore_images.py`（7 条：DPR 兜底、物理上限、大图/小图、
  已有 pixmap、弹窗逻辑尺寸、气泡逻辑尺寸）；`test_petcore_tray.py` 增加托盘穿透
  一次作用全部桌宠；`test_petcore_menu_layout.py` 增加 p9 迁移幂等与模板不含该项。

**行为变化与理由**

- 主分支 README 一直写「鼠标穿透从托盘菜单关闭」：穿透开启后右键菜单本身点不到，
  唯一稳定的恢复入口只有托盘。放进右键菜单 = 用户开了就回不来。
- 「彩蛋图发虚」的根因不是源图不够大，而是旧路径把图缩到 520/320 **逻辑**像素、
  pixmap DPR=1，再交给 QLabel；1.5 倍缩放的屏幕上 QLabel 又要放大到 780/480 物理
  像素 = 两次重采样。新路径直接解码到物理像素，QLabel 1:1 贴图。
- 弹窗逻辑边长仍由 `clamp_popup_side()` 决定（520 × 用户百分比，280–1200），
  不改变用户已经调好的显示大小；本次只改清晰度。

**验证**

- `QT_QPA_PLATFORM=offscreen pytest tests/test_petcore_*.py tests/test_architecture.py
  tests/test_pr_report_discipline.py tests/test_ci_workflows.py -q`
  → **897 passed, 5 skipped in 39.09s**；`ruff check pet/ tests/ petcore/` 全绿。
- 锐度实测（offscreen + `QT_SCALE_FACTOR=1.5`，`assets/big_blue_fat_fish/big_blue_fat_fish.jpg`，
  弹窗整体 grab 816×900）：旧路径（dpr=1）梯度能量 **10.99**，新路径（dpr=1.5）
  **16.37**，+49%。
- 真实机器屏幕探针：`TM140VDXP02 geom 1280×800 dpr 1.5 logicalDpi 96`——本机就是
  1.5 倍缩放，旧路径必然发虚。
- 源码探针（真实 config 副本 + `QT_SCALE_FACTOR=1.5`，模拟本机 1.5 倍缩放）：
  - 旧布局里带 `click_through` → 启动迁移后配置与注册表都不再含该项；
  - 托盘菜单可勾选「鼠标穿透」：触发后 `all_click_through=True`，桌宠气泡
    「已开启鼠标穿透，可从托盘菜单关闭」；重开菜单为勾选态；再触发关闭并提示；
  - 气泡配图：物理 `480×480`、DPR 1.5、label 逻辑 `336×336`（320 + 2×8）；
  - 彩蛋弹窗：窗口 `544×600`、图片 label 逻辑 `520×520`、pixmap 物理 `780×780`、
    DPR 1.5、`popup.device_pixel_ratio=1.5`；
  - 图层：桌宠 `always_on_top=True` 时气泡与彩蛋窗都带 `WindowStaysOnTopHint`，
    关掉置顶后两者同步失去（`test_bubble_keeps_the_pet_layer` /
    `test_popup_follows_the_host_topmost_flag`），源码探针输出
    `pet top True bubble top True` → `after off: pet False bubble False` →
    `after on : pet True bubble True`。

- 打包（增量，ISCC 56.234s）：`dist/dsh-pet-core-setup.exe` **185,809,452 字节**；
  主 exe `Subsystem=2`；`--check` exit 0、`menu_template=json`、
  `self_talk_images.readable=24`；包内模板已无 `click_through`；直接读 PYZ 确认
  `petcore.media.images`、`migrate_p9_menu`、`set_host_on_top`、
  `set_all_click_through` 与新提示文案都进包。
- 全量门禁：**900 passed, 5 skipped**；ruff 全绿。

**未验证**

- 真实桌面（非 offscreen）弹窗/气泡的肉眼观感，以及真实系统托盘勾选状态。
- 跨屏 DPR 不同时，已经显示中的气泡/弹窗不会立刻按新屏重解码（下次出图才生效）。

**后续**

- 需要时监听 `QWindow.screenChanged`，在跨屏后按新 DPR 重新装载当前图片。
## L028 · 2026-10-22 · P0 主分支对齐审查与迁移契约冻结

**改动**

- 新增 `docs/PETCORE-FUNCTION-MATRIX-2026-10-22.md`，逐项对照主分支 v4.3.4 的用户功能域，
  标记已对齐、部分、未做、有意差异和目标阶段。
- 新增 `docs/PETCORE-HANDOFF-PLAN-2026-10-22.md`，把 P0–P6 拆成可交接阶段，固定依赖、
  禁止事项、验收和本地提交标题。
- 新增本审查报告，记录当前 petcore/架构/报告/CI 门禁结果和真实环境限制。
- 更新 SPEC、ROADMAP、MAIN-PARITY-BACKLOG、ARCHITECTURE，冻结每宠配置作用域、WorkerHost、
  迁移/回滚、Core/mod 边界、默认扩展和发布切换门。

**验证**

- `QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest <test_petcore 文件列表> -q`
  → `603 passed`。
- `QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest <petcore + guards> -q`
  → `900 passed, 5 skipped`。
- `.venv/Scripts/ruff check pet/ tests/ petcore/` → `All checks passed`。
- `.venv/Scripts/python -m petcore --check` → exit 0。

**未验证**

- 本阶段只改文档，没有改变运行时行为；真实双屏、安装器 E2E、非 Windows 窗口和完整大素材
  导入继续留给 P1–P6 的实机阶段。
---

## L029 · 2026-10-22 · P1 每宠配置作用域与基础迁移

**改动**

- `ScopedConfig` 路由全局键与当前宠物键；设置窗口绑定当前窗口并保存到正确配置文件。
- `CoreHost._apply_live_to()` 不再把主宠的角色、缩放、动画、物理和台词覆盖到所有子宠；
  `PetManager` 只同步真正全局键。
- 点击台词、自言自语按具体宠物读取配置；新增宠物生命周期钩子收口 timer。
- 旧 standalone 配置和 slot 档案迁移到新目录，先写 `migration-backup/`，不修改旧源。
- 新增配置作用域、设置窗口和迁移回归测试。

**验证**

- petcore：`608 passed`；petcore + 架构/报告/CI 守卫：`907 passed, 5 skipped`。
- ruff：通过；`python -m petcore --check`：exit 0。
- 作用域性能：get 0.14 µs/op（n=50,000），set 12.75 µs/op（n=5,000），snapshot 34.08 µs/op（n=500）。

**未完成**

- 会话、待办独立文件和 API/凭据引用需要对应业务消费者，留到 P3/P4；真实双屏和安装器
  E2E 留到 P5/P6。
---

## L030 · 2026-10-22 · P2 WorkerHost/离线扩展/角色导入

**改动**

- 新增 `petcore/platform/worker.py`，提供真实子进程 WorkerHost/WorkerSession、JSONL READY、
  配置推送、取消、错误回调、owner 替换、心跳超时和有界关闭。
- `CoreHost` 持有 WorkerHost，`HostPorts.worker` 注入；退出时统一收口 worker。
- 设置常规页增加角色目录导入；旧素材 Graph 映射补齐 Touch_Head/Say/Sleep/StartUP/Work/Raised。
- 安装器 5 个官方离线 feature 默认选中，README 与安装器测试同步。
  （**L034 起已回退为默认不勾选**：该默认值与「不勾选就真不装」的用户口径冲突。）

**验证**

- Worker/角色/安装器/架构：`21 passed in 1.54s`。
- petcore + 架构/报告/CI：`915 passed, 5 skipped in 27.15s`。
- ruff：通过。

**未完成**

- 业务 worker、旧 `pet` 媒体后端解耦、真实安装升级卸载和声卡/双屏人工验收留给后续阶段。
---

## L031 · 2026-10-22 · P3 Core 账户/余额基础

**改动**

- 新增 `petcore/account/AccountService` 与 `BalanceResult`：服务协议校验、钥匙串读取、
  DeepSeek 余额解析、多币种选择、30 秒内存缓存和稳定错误分类。
- `ApiPort` 增加 `fetch_balance()`，CoreApiPort 转发到账户服务；CoreHost 持有进程级服务。
- 新增账户/API/net/架构回归测试，响应体和密钥不进入日志。

**验证**

- `pytest test_petcore_account.py test_petcore_api.py test_petcore_net.py test_petcore_architecture.py -q`
  → `84 passed in 1.89s`。
- `ruff check petcore tests/test_petcore_account.py` → 通过。

**未完成**

- 余额菜单、自动刷新、档位动画、灵动岛展示和真实远程账户请求留给后续 P3 UI/worker 阶段。

---

## L032 · 2026-10-22 · P3 每宠会话存储接缝

**改动**

- 新增 `petcore/chat/SessionStore` 和 `ChatSession`，按 slot/角色隔离目录，限制消息数量和
  单条长度，原子写入，拒绝路径逃逸。
- 扩展 worker 协议的 request/delta/result/done 常量，并为 WorkerSession 增加 request 接缝。

**验证**

- `pytest tests/test_petcore_sessions.py` → `3 passed in 0.83s`。
- `ruff check pet/ tests/ petcore/` → 通过。

**未完成**

- 会话尚未接入 AI worker 或聊天 UI；业务接入继续属于 P3 后续。

---

## L033 · 2026-10-22 · 插件市场 v1（索引/下载/安装/回滚/UI/CLI）

**改动**

- 新增 `petcore/market/`：索引模型与状态、ETag + 磁盘缓存的多源读取、大小/SHA-256 校验、
  `.part` 续传编排、staging → 备份 → 原子安装、`market-lock.json`、回滚与 CLI。
- 新增 `petcore/net/market.py`：索引 GET（含 GitHub Contents API base64）与 Range 下载，
  复用 `net/http.urlopen` 的「系统代理优先、失败改直连」。
- `petcore/mods/install.py` 暴露 `open_pack()` 供市场复用解包；`petcore/cli.py` 接入
  `petcore market list/show/install/update/rollback/doctor`。
- 新增设置页 `page_market.py`，注册到「扩展管理」左导航（顺序：扩展管理 → 插件市场）；
  新增全局键 `market_index_url`（默认空 = 官方索引），feature 安装仍受 `allow_feature_mods`。
- 文档：新增 `PETCORE-MARKET.md` 与 `market-index.schema.json`，更新 INDEX / ROADMAP /
  MOD-FORMAT §6.2 / Phase 4 设计文档指针。

**文件（`git diff --numstat` + 新增行数）**

| 文件 | 变化 |
|---|---|
| `petcore/market/{__init__,errors,verify,download,source,model,install,cli}.py` | 新增 1730 行 |
| `petcore/net/market.py` | 新增 178 行 |
| `petcore/ui/settings/page_market.py` | 新增 536 行 |
| `petcore/ui/settings/page_extensions.py` | +5 |
| `petcore/cli.py` | +18 |
| `petcore/config.py` | +2 |
| `petcore/mods/install.py` | +7 |
| `docs/{PETCORE-MARKET.md,market-index.schema.json}` | 新增 283 行 |
| `docs/INDEX.md` / `PETCORE-MOD-FORMAT.md` / `PETCORE-ROADMAP.md` / Phase 4 文档 | +28 / −1 |
| `petcore/AGENTS.md` | +5 / −2（导航顺序与市场边界） |
| `tests/test_petcore_market_{model,source,net,install,cli,ui}.py` | 新增 1266 行 |
| `tests/test_petcore_extensions_nav.py` | +26 / −16（导航新增「插件市场」契约） |
| `docs/PETCORE-LOG.md` / `PETCORE-LOG-INDEX.md` | +50 / +1（L033） |

**验证**

- `pytest tests/test_petcore_market*.py -q` → `53 passed in 24.90s`。
- `pytest tests/test_petcore_*.py tests/test_architecture.py -q` → `689 passed in 56.23s`。
- `ruff check pet/ tests/ petcore/` → 通过；`python -m petcore --check` → `diagnostics: []`。
- 实测（`.scratch/market-bench.py`，本机 Win11 / Py3.12.10）：500 包索引解析中位
  **12.4 ms**、峰值 **1.9 MB**；索引冷读 18.8 ms、内存命中 <0.1 ms；1 MB artifact
  下载 31.8 ms、SHA-256 1.15 ms、安装 31.2 ms；50 包列表首次刷新 8.6 ms、重建 0.49 ms。
- 真实 `windows` 平台截图（`.scratch/market-page-real.png`）：状态标签、可更新/阻止、
  feature 与索引源设置均正常渲染；本地 HTTP 端到端覆盖安装/更新/回滚/离线缓存。
- 全量 pytest：`9092 passed / 18 skipped / 2 failed`（21m32s）；两条失败均为测试顺序/
  负载问题（见报告 §五.4），修复 palette 泄漏后相关两族 `178 passed / 1 skipped`。

**未完成**

- `MerZlin/dsh-pet-extensions` 索引仓库尚未创建，未对真实 GitHub raw 做端到端；
- 数字签名、CDN/OSS 镜像、npm 通道（只保留 URL 层接口）均按计划留到后续。

---

## L034 · 2026-10-22 · 官方扩展恢复「默认不勾选」

**改动**

- `packaging/petcore.iss`：5 个 `mod_*` 任务加 `Flags: unchecked`，注释改为
  「默认不勾选；勾选才写入安装目录，不勾选完全不落盘」。
- `tests/test_petcore_installer.py`：`test_official_feature_tasks_are_default_selected`
  → `test_official_feature_tasks_are_opt_in`，断言由「不得出现 `Flags: unchecked`」
  改为「每个任务都必须有」。
- `examples/petcore-mods/README.md`：措辞回到「可勾选项（默认不勾选）」；顺带修正
  安装位置（`%APPDATA%\dsh-pet-core\mods\<id>` → `{app}\mods\<id>`，与 iss 一致）。
- 口径同步：`PETCORE-FUNCTION-MATRIX`、`PETCORE-HANDOFF-PLAN`、`PETCORE-SPEC` §14、
  `PETCORE-ROADMAP` §20、`PETCORE-MAIN-PARITY-BACKLOG`、`petcore/AGENTS.md` 全部改成
  「可选安装（默认不勾）」。
- `docs/PR-REPORT-PETCORE-P2-WORKER-OFFLINE-2026-10-22.md` 追加修正章节。

**理由**

用户最初口径是「setup 里可勾选，不勾选就真不会安装到本地」；P2 把默认值改成了勾选，
功能上仍可选、但默认行为反了。恢复默认不勾选，勾选后才写 `{app}\mods\<id>` 与 `.trusted`。

**验证**

- `pytest tests/test_petcore_installer.py -q` → 7 passed。
- `pytest tests/test_petcore_*.py tests/test_architecture.py
  tests/test_pr_report_discipline.py tests/test_ci_workflows.py -q` → **985 passed**。
- `ruff check pet/ tests/ petcore/` → 全绿。
- `python -m petcore --check` → 退出 0。
- 重新出包（增量 `-Installer`，ISCC 完成后）：`dist/dsh-pet-core-setup.exe`
  **192,675,082 字节**；主 exe `Subsystem=2`；打包后 `--check` exit 0、
  `menu_template=json`、`diagnostics=[]`、`self_talk_images.readable=24`；
  冻结包内 `market --offline list` / `market doctor` 均可运行（doctor 对未创建的
  索引仓库返回 HTTP 404 诊断，属预期）。

**未验证**

- 安装向导里 5 个勾选框的**实际初始状态**尚未用截图确认（iss `Flags: unchecked`
  是 Inno 文档语义，测试只断言源码文本）。下次安装时人工看一眼。

---

## L035 · 2026-10-22 · 阶段编号统一：历史批次 H1–H8.4 vs 交付阶段 P0–P6

**改动**

- `docs/PETCORE-ROADMAP.md`：新增 §0「编号约定」与换算表；§3、§6–§19 的标题与 §2
  批次表由 `P1-A…P8.4` 改名为 `H1-A…H8.4`；§20 的 `P0–P6` 保持不变。
- `docs/PETCORE-SPEC.md`：§10–§14 标题改 `H6/H7/H8/H8.1/H8.4`，并在 §10 前加一行
  说明「历史批次 vs 当前交付阶段」。
- `docs/PETCORE-LOG-INDEX.md`：读取规则后补编号约定（历史行里的 P = H）。
- `docs/INDEX.md`：ROADMAP 条目改写，强调 §0 编号约定。
- 历史文档（`PETCORE-LOG.md` 既有条目、`docs/PR-REPORT-PETCORE-P1…P8…` 文件名）
  保持原样，不做回填重命名。

**理由**

GPT 的 P0–P6 计划与历史 P1-A…P8.4 重名不同义（新 P1=配置作用域，旧 P1=UI 风格），
两份文档已经并存。用 H 系列承接历史、P0–P6 只表示未来阶段，避免继续误读。

**验证**

- `pytest tests/test_petcore_docs.py tests/test_pr_report_discipline.py -q` → 237 passed。
- 全量 petcore 门禁 985 passed、ruff 全绿（同 L034 那一轮）。
- `grep "^## " docs/PETCORE-ROADMAP.md`：§3/§6–§19 均为 H 前缀，§20/§21 未受影响的
  P0–P6 与市场章节保持原样。

**未验证 / 风险**

- `docs/PR-REPORT-PETCORE-P1-SCOPE-2026-10-22.md` 等**新**报告仍叫 P1/P2/P3（指新轴），
  与历史 `P1C/P1F/P2/P3` 文件名并存；文件名不改，正文已在 ROADMAP §0 有换算口径。

## L036 · 2026-10-22 · P3 流式对话、聊天宿主、截图与热键

**改动**

- `petcore/api/chat.py`（新，194 行）：`stream_chat()` —— SSE 逐段解析、一次性 JSON
  回退、`cancel` 轮询、超时、`net/http` 代理兜底；错误统一成 `net/errors` 的稳定
  reason；日志不记正文与密钥。
- `petcore/contracts/api.py`：`ApiPort.stream_chat(...)`；`petcore/ui/ports.py::CoreApiPort`
  转发并注入 keyring 解析出的 `ApiRequest`。
- `petcore/contracts/chat.py`（新）+ `petcore/ui/chat/`（新，window 289 / controller 296 行）：
  `ChatPort` 实现、Markdown 流式合帧（50ms）、停止/清空/历史下拉、按 `(owner, slot)`
  隔离；跨线程只用 Qt 信号，worker 线程不碰控件。
- `petcore/contracts/screen.py`（新）+ `petcore/platform/screenshot.py`（新，75 行）：
  `ScreenPort.capture()` 用 `QScreen.grabWindow(0)` 抓整屏到内存 PNG；不写盘。
  `contracts/screen.py` 只留数据类（`contracts` 必须 stdlib-only）。
- `petcore/platform/hotkeys.py`：单条 → 多条命名注册（每条独立 Win32 id），保留
  `apply(text, cb)` 旧签名；`active_texts()`、`clear(name)`。
- `petcore/ui/ports.py`：`WorkerNoticePort` —— worker 出错/异常退出转成用户可见提示
  （补齐 P3 验收「Worker 取消/超时/崩溃有用户反馈」）。
- `petcore/ui/app.py`：`HOTKEY_CONFIGS`/`HOTKEY_COMMANDS` 四条映射；
  `set_hotkey(text, kind=...)`；`run_feature_command()` 统一分发与「扩展未安装」提示。
- `petcore/config.py`：新增 `hotkey_open_chat` / `hotkey_look_screen` /
  `hotkey_screen_capture` / `ai_service` / `ai_model` / `ai_system_prompt`（全局键）。
- `petcore/ui/menu_actions.py` + 模板 + `menu_layout` + `coverage.py`：右键新增
  「AI 对话 / 看看屏幕 / 截图提问」；`icons.py` 增加 chat 图标。
- `petcore/ui/settings/pages_core.py`：常规页新增「AI 快捷键」三条 + 截图隐私说明。
- `tests/`：新增 `test_petcore_api_chat.py`(6)、`test_petcore_chat_ui.py`(6)、
  `test_petcore_screenshot.py`(4)、`test_petcore_hotkeys.py`(5)；
  `test_petcore_architecture.py` 增加 3 条守卫（chat Qt-free、mod 不 import 传输、
  engine 不 import ui）；`test_petcore_worker.py` 增加 worker 失败反馈；
  `test_petcore_menu_layout.py` 顺序断言更新。

**行为**

- AI 对话窗口归 core：mod 通过 `ChatPort` 发事件；流式逐字显示、停止即时生效、
  历史按宠/角色落盘。`petcore/chat/` 仍零网络、零 Qt。
- 截图提问：整屏抓取 → 内存 PNG → 多模态 `image_url`，全程不落盘。
- 热键：四条并存，失败可见。

**验证**

- `pytest tests/test_petcore_*.py tests/test_architecture.py tests/test_pr_report_discipline.py
  tests/test_ci_workflows.py -q` → **1019 passed**。
- `ruff check pet/ tests/ petcore/` → 全绿。
- 性能探针（`.scratch/p3_probe.py`，本机）：本地 SSE 100 段首段 **38.3ms**、总
  **45.8ms**；窗口 300 段/50 次合帧 **21.8ms（0.4ms/次）**；真实屏幕 1920×1200@1.5
  抓屏 **204.7ms**、PNG **367KB**。

**打包受阻（环境）**

- `-Installer` 的 PyInstaller 成功，但 `--check` 冒烟启动被系统拦截：
  `WinError 4551 = "An Application Control policy has blocked this file."`，
  `HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy\VerifiedAndReputablePolicyState = 1`
  → 本机 **Smart App Control** 强制开启，拦截未签名的新构建 exe。
- 对照：复制到仓库目录的 notepad 可启动；PyInstaller 构建的空 hello 程序同样被拦；
  旧 setup 同样被拦。与 petcore 代码无关。
- 结论：本轮**没有产出 P3 安装包**；`dist/dsh-pet-core-setup.exe` 仍是 14:59 的旧产物。
  解除需用户关闭 Smart App Control 或给产物签名。**（L038 已用本机开发证书签名解决。）**

**未验证**

- 真实 DeepSeek 端到端的首 token 延迟（本机无可用密钥，探针只测传输开销）。
- 抓屏在 GUI 线程同步执行（约 205ms），用户按热键会有一次可感知停顿。

---

## L037 · 2026-10-22 · ai.chat 参考扩展与安装口径

**改动**

- `examples/petcore-mods/ai.chat/`（新）：`mod.json`（host-only、capabilities
  network.request/screenshot.capture/speech.present/settings.read/write）+
  `host/factory.py`（399 行）：`open`/`look_screen`/`screen_capture` 三条命令、
  `chat.on_submit/on_stop`、系统提示词 + 历史组装、截图内联、错误回退、设置页
  （服务/模型/提示词/视觉开关）。
- `packaging/petcore.iss`：新增 `mod_aichat` 任务（`Flags: unchecked`）+ 文件与
  `.trusted` 投放，和其余 5 个官方扩展同口径。
- `examples/petcore-mods/README.md`：从 5 个改 6 个，补 ai.chat 安装命令。
- 测试：`test_petcore_ai_mod.py`(7)；`test_petcore_installer.py` 两个清单补 ai.chat。

**验证**

- 全量 petcore 门禁 1019 passed（含新增 7 条 + worker 反馈 1 条）；ruff 全绿。
- 打包后验证见 PR 报告「实机运行记录」。

**未验证**

- 真实模型下的多轮对话质量；视觉模型对 367KB 截图的接受度（不同服务上限不同）。

## L038 · 2026-10-22 · 构建产物签名与 P3 打包收尾

**改动**

- `scripts/make_dev_signing_cert.ps1`（新）：创建本机开发用代码签名证书
  （`CN=DSH Pet Core Dev`，RSA 3072 / SHA256，3 年），公钥导入
  `CurrentUser\Root` + `CurrentUser\TrustedPublisher`；`-Remove` 可撤销信任。
- `scripts/build_petcore.ps1`：新增 `-SignThumbprint` 与 `-TimestampServer`；
  exe 在 `--check` 冒烟前签名，setup 在 ISCC 之后签名；时间戳失败退回无时间戳，
  签名状态非 Valid 直接报错。
- `petcore/README.md`：补「签名后构建」两条命令。
- `petcore/AGENTS.md`：新增红线（未签名产物会被 Smart App Control 拦截；签名在
  `--check` 之前）。
- `docs/PETCORE-SPEC.md` §16：补打包签名口径。
- `docs/PR-REPORT-PETCORE-P3-AI-2026-10-22.md`：「打包验证（受阻）」改写为
  「打包与签名（已完成）」，附签名状态与安装路径验证。

**验证**

- 签名状态：`dsh-pet-core.exe` / `dsh-pet-core-setup.exe` 均 `Valid`，
  `CN=DSH Pet Core Dev`。
- 打包：ISCC 112.953s，setup **194,139,120 字节**；打包后 `--check` exit 0、
  `menu_template=json`、`diagnostics=[]`。
- 扩展安装路径：`--root <临时目录> mods install examples/petcore-mods/ai.chat`
  → `installed ai.chat 1.0.0`；`--check` 输出 `mods=['ai.chat']`、`diagnostics=[]`。
- 源码门禁：1019 passed（未改代码，签名只动构建脚本/文档）。

**未验证 / 边界**

- 开发证书只在本机可信；正式分发必须换成 CA 证书。
- 完整安装向导 E2E（安装/升级/卸载）未在本机跑，避免影响现有 `E:\dsh-pet-core` 安装。
- 真实 DeepSeek / 视觉模型端到端仍未跑（无可用密钥）。

## L039 · 2026-10-22 · P3 收口：对话窗 / 看看屏幕 / 截图提问

**范围**：把主分支已经验证过的对话窗与识屏交互搬进 Core，`ai.chat` 继续只负责
prompt/路由；新增 `ScreenAskPort`，并让扩展命令按发起桌宠派发。

**改动**

- `petcore/ui/chat/`（新增 `themes.py` 244、`markdown_view.py` 85、`styles.py` 162、
  `icons.py` 129、`widgets.py` 905；重写 `window.py` 926+/197−、`controller.py`
  138+/40−）：移植主分支现代双栏对话窗（侧栏会话、Markdown 气泡、附件、浮动输入框、
  置顶/最小化、无边框拖拽/缩放、紧凑抽屉），新增 `record()` 静默写历史与
  `take_attachments()`。
- `petcore/ui/screen/`（新增 6 个文件、1603 行）：框选覆盖层、DPR 裁剪、
  `ScreenAskWindow`（多轮、图片仅内存、DS 鲸鱼标题、10% 鲸蛙加载彩蛋）与
  `ScreenAskController`；保持 Qt 在 Core、网络在 mod。
- `petcore/contracts/screen.py`：`ScreenCapture.app_info`、`ScreenError`、
  `ScreenSelectionCancelled`、`ScreenPort.capture_region/foreground_app`、`ScreenAskPort`。
- `petcore/contracts/host.py` / `petcore/ui/ports.py` / `petcore/ui/app.py`：
  `HostPorts.screen_ask`、`QtScreenPort` 框选与前台应用；`CoreHost` 持有
  `ScreenAskController`；`run_feature_command(slot=)` 对声明 `slot` 的扩展命令按宠派发。
- `petcore/platform/foreground.py`（新 92 行）+ `petcore/platform/screenshot.py`：
  抓屏附带「进程名 | 窗口标题」，框选裁切复用同一编码路径。
- `examples/petcore-mods/ai.chat/host/factory.py`（371+/81−，版本 1.1.0）：看看屏幕
  改为气泡答复 + `ChatPort.record()` 静默入历史（默认不打开对话窗，
  `ai_look_screen_auto_open` 才唤出）；4 秒冷却/忙碌提示；截图提问改走框选 +
  `ScreenAskPort.on_ask(question, png, history, slot) -> str`；对话提交去重当前问题并
  支持附件图片。
- `petcore/config.py`：持久化 `ai_vision_enabled` / `ai_temperature` /
  `ai_look_screen_auto_open` / `chat_always_on_top` / `chat_follow_pet` /
  `chat_background` / `chat_accent`（此前 `ai_vision_enabled` 会被 normalize 丢掉）。
- `petcore/AGENTS.md`：新增对话窗移植、看看屏幕静默入历史、`ScreenAskPort` 边界、
  截图彩蛋独立、命令按宠派发五条红线。
- `docs/PETCORE-SPEC.md` §16/§19、`docs/PETCORE-ROADMAP.md` §20/§22、
  `docs/PETCORE-MAIN-PARITY-BACKLOG.md` §3.1 同步。
- 测试：`tests/test_petcore_chat_ui.py` 重写（15 条）、`tests/test_petcore_screen_ask.py`
  新增 23 条、`tests/test_petcore_ai_mod.py` 扩到 13 条、
  `tests/test_petcore_feature_command_slot.py` 新增 1 条。

**验证**

- `QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest tests/test_petcore_*.py
  tests/test_architecture.py tests/test_pr_report_discipline.py tests/test_ci_workflows.py -q`
  → **1058 passed in 41.05s**。
- `.venv/Scripts/ruff check pet/ tests/ petcore/` → 全绿。
- `QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m petcore --check` → exit 0，
  `diagnostics=[]`、`menu_template=json`。
- 性能探针（offscreen，n=20/50/200）：首次打开对话窗 23.7ms；`record()` 中位 1.72ms
  / p95 2.78ms；追加一条气泡 1.18ms；`ScreenAskWindow.start` 中位 13.99ms / p95 15.31ms；
  `begin_waiting` 0.02ms；`pick_loading_icon` <0.01ms；`foreground_app_info` 0.02ms。
- 视觉：offscreen `grab()` 渲染对话窗（双栏、右对齐用户气泡、Markdown、底部输入区）与
  截图问答窗（鲸鱼标题、图片 chip、用户气泡、加载鲸鱼 + 状态、输入行）。

**未验证 / 边界**

- 真实 DeepSeek / 视觉模型端到端未跑（无可用密钥）；真实桌面框选只做了 offscreen
  事件循环验证，未在本机真实桌面拖选。
- 未移植主分支的思考/联网过程面板、会话批量管理/重命名/删除、跟随桌宠定位、
  OCR/翻译/复制动作条、主动识屏与每日预算；已记入 parity backlog。
- `chat_follow_pet` 已持久化但窗口暂不跟随桌宠移动；`chat_background` 只支持
  `builtin:<theme>` 或绝对路径，不含主分支的裁切/透明度/填充键。

## L040 · 2026-10-22 · AI 边界收口：mod 拆分 / 线程调度 / 配置端口 / 外观跟随

**范围**：把 L039 留下的 6 个边界缺口全部落成代码或明确契约。
行为目标不变，重点是「运行时无 Qt、配置访问分层、聊天窗外观可配且实时生效」。

**改动**

- `petcore/mods/feature.py`（+39/−7）：`import_entrypoint()` 公开；包入口
  `host/__init__.py:create_host` 以 `submodule_search_locations` 加载并注册
  `sys.modules`，支持相对 import；异常时清理模块；普通 `.py` 入口行为不变。
- `petcore/contracts/host.py`（+13/−5）：新增只读 `ConfigPort` 与
  `HostPorts.config`；删除未实现的 `SettingsPort` / `HostPorts.settings`；
  `SchedulerPort` 明确「任意线程可调、回调在 GUI 线程」。
- `petcore/ui/ports.py`（+66/−29）：`_SchedulerBridge` 用 queued signal 统一在
  GUI 线程创建/销毁 `QTimer`；`QtScheduler` 的 `call_later/call_every/cancel`
  可从 worker 线程调用；新增 `QtConfigPort`；`build_ports` 接 `config`。
  定时器不挂 Qt parent（避免 bridge 析构时悬空 DeferredDelete 导致进程 abort）。
- `examples/petcore-mods/ai.chat/host/`：删除 689 行的 `factory.py`，拆成
  `__init__.py`（create_host）、`constants.py`、`prompts.py`（消息组装纯函数）、
  `runtime.py`（430 行，无 PySide6 / 无 `petcore.net` / 无 `petcore.api`）、
  `settings_page.py`（313 行，受信 UI）。`mod.json` 入口改为包入口，版本 1.1.0→1.2.0。
- `runtime.py`：配置只经 `ports.config`；worker 回 GUI 只经
  `ports.scheduler.call_later()`；看看屏幕/对话/截图问答逻辑不变。
- `settings_page.py`：新增「对话窗外观」分区——背景（默认/9 个内置主题/自定义图片）、
  强调色（hex 校验 + 颜色选择 + 恢复默认）、跟随桌宠、对话窗置顶；改动即时调用
  `host.chat.apply_appearance()`。
- `petcore/ui/pet_window.py`（+6/−1）：新增 `pet_moved` 信号，`moveEvent` 末尾 emit。
- `petcore/ui/chat/window.py`（+128/−1）：新增 `pet_window/follow_pet` 构造参数、
  `set_pet_window()` / `set_follow_pet()` / `position_near_pet()`；40ms 合并定位，
  右侧优先、放不下切左侧、再放下方，最后钳在可用区；`showEvent` 首次定位。
  顺带修复 `set_accent()` 只改 `_base_accent` 而 `_style()` 读 `accent_color`
  导致实时换色无效的问题。
- `petcore/ui/chat/controller.py`（+83）：`apply_appearance()` 把背景/强调色/置顶/
  跟随实时推到已打开窗口（不建窗、不唤窗）；创建窗口时接当前宠与 follow 配置。
- `petcore/ui/app.py`（+7）：`apply_live_settings()` 末尾刷新聊天窗外观。
- `petcore/ui/screen/controller.py`（+4）：明确截图问答历史「每次采集独立、只存内存、
  不落盘」。
- `tests/`：`test_petcore_ai_mod.py` 改包入口 + prompt/静态守卫/设置页外观；
  `test_petcore_scheduler.py` 新增 6 条；`test_petcore_chat_ui.py` 增 follow/外观；
  `test_petcore_feature.py` 增包入口；`test_petcore_live_settings.py` 增 chat 刷新；
  `test_petcore_screen_ask.py` 锁定不写盘。
- 文档：`PETCORE-SPEC.md` §19、`PETCORE-ROADMAP.md` §22、`PETCORE-ARCHITECTURE.md`
  契约清单、`PETCORE-MOD-FORMAT.md` 包入口与参考实现、`PETCORE-MAIN-PARITY-BACKLOG.md`
  AI 行、`examples/petcore-mods/README.md` 端口/包入口说明、`petcore/AGENTS.md` 五条红线。

**验证**

- `QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest tests/test_petcore_*.py
  tests/test_architecture.py tests/test_pr_report_discipline.py tests/test_ci_workflows.py -q`
  → **1083 passed in 52.62s**。
- `.venv/Scripts/ruff check pet/ tests/ petcore/ examples/petcore-mods/ai.chat`
  → 全绿。
- `QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m petcore --check` → exit 0。
- 端到端 mod 加载：`petcore --root .scratch/ai-boundary mods install
  examples/petcore-mods/ai.chat` → `installed ai.chat 1.2.0`；`--check` 列出
  `ai.chat 1.2.0 enabled=true`，`diagnostics=[]`。
- 性能探针（offscreen）：`call_later(0)` 端到端中位 **0.027ms** / p95 0.061ms
  （n=50）；`ConfigPort.get` <0.001ms（n=200）；首次打开对话窗 24.7ms；
  `apply_appearance()` 无变化时 0.003ms（n=50）；`position_near_pet()` <0.001ms
  （n=100）。
- 视觉：offscreen 渲染对话窗与截图问答窗仍正常；设置页外观控件为普通
  combo/line edit/checkbox。

**未验证 / 边界**

- 真实桌面拖选、真实双屏/混合 DPI 下的 follow 定位未实机跑（offscreen 仅验证几何
  与事件）。
- `chat_background` 仍只支持 `builtin:<theme>` 或绝对路径，不含主分支的取景裁切、
  透明度和 cover/contain 独立开关。
- `cancel()` 改为 queued：已到点的定时器可能在 stop 处理前再触发一次；GUI 调用方
  （自言自语、报时）不受影响。
- 截图问答历史明确不落盘；这不是待办，而是隐私与语义约束。

## L041 · 2026-10-22 · 截图蒙层灰屏修复 + 深度思考/联网搜索

**范围**：修「截图提问整屏发灰」的生产环境 bug，并把主分支的深度思考与联网搜索
按「传输在 core、编排在 mod」的口径接进 petcore。

**改动**

- `petcore/platform/screenshot.py`（+17/−4）：`grab_screen_image()` 接受 Qt6
  真实返回的 `QPixmap`，`toImage()` 后 `setDevicePixelRatio(1.0)`。根因：覆盖层
  `QPainter.drawImage(QRect, QPixmap)` 抛 TypeError，`paintEvent` 首行就中断，
  整层不画 → 默认灰底；裁切/编码走 `QPixmap.copy/save` 恰好可用，所以框出来的图正常。
- `petcore/ui/screen/overlay.py`（+9/−1）：构造函数 `_as_image()` 兜底非 QImage。
- `petcore/config.py`（+29）：`ai_thinking`/`ai_reasoning_effort`/`ai_web_search`/
  `ai_web_engine`/`ai_web_results`/`ai_web_fetch` 六个全局键 + 归一化。
- `petcore/contracts/api.py`（+17）：`WebSearchOptions`；`ApiPort` 增
  `on_reasoning/on_status/thinking/reasoning_effort/web_search` 与搜索 Key 读写。
- `petcore/contracts/chat.py`（+8）：`ChatPort.reasoning()/status()`。
- `petcore/api/chat.py`（+417/−10）：`thinking_payload()`；`_extract_delta()` 兼容
  `reasoning_content`/`reasoning`；`ChatResult.reasoning`；只出思维链且
  `finish_reason=length` 抛 `ChatTruncated`；`research()` / `inject_search_context()` /
  `_run_tool()` / `_post_json()`；`stream_chat(web_search=)` 先检索后回答。
- `petcore/net/websearch.py`（+407，新）：百度/DuckDuckGo（免 Key，互为兜底）、
  博查/Tavily（需 Key）、`fetch_page_text()`、`format_results()`、稳定错误分类；
  复用 `net.http.urlopen` 的代理兜底。
- `petcore/ui/ports.py`（+43/−1）：`CoreApiPort` 透传参数并从钥匙串取搜索 Key
  （owner `core.websearch`）；`web_search_key_status()` / `set_web_search_key()`。
- `petcore/ui/chat/controller.py`（+48/−1）：`reasoning_received`/`status_received`
  排队信号；窗口不存在时丢弃；done 时把 `reasoning`/`activity` 写入 assistant 记录。
- `petcore/ui/chat/window.py`（+38/−1）：`append_reasoning()`/`add_activity()`/
  `last_thinking()`；`load_messages()` 回显思考面板。
- `petcore/chat/session_store.py`（+12/−3）：`from_dict()` 保留可选
  `reasoning`/`activity`（旧会话无字段照常加载）。
- `examples/petcore-mods/ai.chat/host/runtime.py`（+42）：读 6 个配置键，把
  `thinking`/`reasoning_effort`/`on_reasoning`/`on_status`/`web_search` 传给端口。
- `examples/petcore-mods/ai.chat/host/settings_page.py`（+225）：「深度思考」
  （模式 + 强度，强度仅在开启时显示）与「联网搜索」（开关/引擎/Key/条数/读正文）；
  Key 走端口进钥匙串，取消时控件复位，搜索关键词补齐。
- `examples/petcore-mods/ai.chat/mod.json`：版本 1.2.0 → 1.3.0（新增思考/联网设置与运行时接线）。
- 测试：`test_petcore_screenshot.py`（+38/−2）、`test_petcore_screen_ask.py`
  （+14/−1）、`test_petcore_api_chat.py`（+212/−2）、`test_petcore_websearch.py`
  （+150，新）、`test_petcore_chat_ui.py`（+48）、`test_petcore_ai_mod.py`（+145）。
- 文档：`PETCORE-SPEC.md` §20、`PETCORE-MAIN-PARITY-BACKLOG.md` AI/截图两行、
  `petcore/AGENTS.md` 四条红线、`PR-REPORT-PETCORE-P7-THINKING-SEARCH-2026-10-22.md`。

**验证**

- 全量门禁：`pytest tests/test_petcore_*.py tests/test_architecture.py
  tests/test_pr_report_discipline.py tests/test_ci_workflows.py -q`
  → **1114 passed in 71.69s**。
- `ruff check pet/ tests/ petcore/ examples/petcore-mods/ai.chat` → 全绿。
- `python -m petcore --check --out .scratch/p7-check.json` → exit 0，
  `version 0.1.0`、`diagnostics []`、`self_talk_images` 24/24 可读。
- 真实桌面抓屏（非 offscreen）：`screen TM140VDXP02 1280×800 dpr1.5` →
  `QImage dpr1.0 1920×1200`；覆盖层 `paint ok, sample #1d1f26`（桌面深色，非灰底）。
- 真实联网：百度免 Key 搜索 0.83s / 3 条；DuckDuckGo 40.80s 后由百度兜底。
- 性能：抓屏中位 49.1ms（n=5）、覆盖层绘制 4.22ms（n=20）、
  `_extract_delta` 1.0µs（n=20000）、`build_payload` 0.6µs（n=5000）。

**未验证 / 边界**

- 真实 DeepSeek 思考模型与博查/Tavily 检索未跑（无 Key，未消耗用户额度）。
- 真实桌面拖选只验证了抓帧与绘制，拖拽交互由 offscreen 事件测试覆盖。
- HDR 屏冻结帧仍走 Qt `grabWindow`，未移植 DXGI FP16（可能发灰）。
- 思考/联网设置是全局键（同 `ai_model`/`ai_temperature`），不是主分支的 per-provider。

## L042 · 2026-10-22 · 截图动作条（OCR 归 mod）+ 快速对话气泡

**范围**：把截图提问补成主分支的四个动作，并按「业务在 mod、core 只留端口与窗口」
的口径加入快速对话气泡。

**改动**

- `petcore/contracts/screen.py`（+54/−2）：`ACTION_*`、`RegionPick`、
  `ScreenPort.capture_region(ai_available=)`、`ScreenAskPort.open_action/on_action
  (priority, actions)`。
- `petcore/contracts/chat.py`（+25）：`QuickChatPort`。
- `petcore/contracts/host.py`（+9/−1）：`ResourcesPort`、`HostPorts.resources/
  quick_chat`。
- `petcore/ui/screen/overlay.py`（+69/−14）：四动作条、capabilities/reasons、
  `accepted(action, rect)`、Enter 默认动作（ocr→ask→copy）。
- `petcore/ui/screen/selection.py`（+60/−11）：返回 `RegionPick`、`copy` 写剪贴板、
  兼容旧单参数 accepted。
- `petcore/ui/screen/controller.py`（+148/−1）：`on_action` 注册表、
  `supports_action`、`open_action` 后台调度与 generation 校验。
- `petcore/ui/screen/ask_window.py`（+6/−2）：`start(chip=)`、`status_text()`。
- `petcore/ui/chat/quick.py`（+286，新）：快速对话气泡（标题/状态/Markdown/输入/
  去聊天窗/贴桌宠定位与 40ms 跟随）。
- `petcore/ui/chat/quick_controller.py`（+238，新）：按 `(owner, slot)` 隔离、
  排队信号、共用会话。
- `petcore/ui/chat/controller.py`（+14/−6）：`append_to_session()`。
- `petcore/ui/bubble.py`（+12/−1）：`clicked` 信号 + 内部 QLabel 透传鼠标。
- `petcore/ui/pet_window.py`（+7）：气泡点击 → `host.open_quick_chat(slot)`。
- `petcore/ui/app.py`（+13/−1）：`QuickChatController` 与 `open_quick_chat()`。
- `petcore/ui/ports.py`（+42/−2）：`ResourcesAdapter`、能力表、`quick_chat`。
- `examples/petcore-mods/tool.ocr/`（+291，新）：本地 OCR（rapidocr、显式
  onnxruntime session、模型来自 `resources/rapidocr_models`、失败回退 AI）。
- `examples/petcore-mods/ai.chat/host/prompts.py`（+44）：OCR/翻译消息与目标语言。
- `examples/petcore-mods/ai.chat/host/runtime.py`（+149/−2）：`RegionPick` 分派、
  `_on_action`、快速对话提交与流式。
- `examples/petcore-mods/ai.chat/host/constants.py`（+3）：`QUICK_HISTORY`。
- `examples/petcore-mods/ai.chat/mod.json`（+1/−1）：1.3.0 → 1.4.0。
- `packaging/petcore.iss`（+3）：`mod_ocr` 任务与 `tool.ocr` 文件。
- `scripts/build_petcore.ps1`（+18）：rapidocr/模型进包。
- 测试：`test_petcore_screen_ask.py`（+83/−15）、`test_petcore_screen_actions.py`
  （+128，新）、`test_petcore_quick_chat.py`（+188，新）、`test_petcore_ocr_mod.py`
  （+163，新）、`test_petcore_ai_mod.py`（+131/−3）、`test_petcore_example_mods.py`
  与 `test_petcore_installer.py`（+11/−2）。
- 文档：`PETCORE-SPEC.md` §21、`PETCORE-MAIN-PARITY-BACKLOG.md` AI/截图两行、
  `petcore/AGENTS.md` 五条红线、P8 PR 报告。

**验证**

- 全量门禁：`pytest tests/test_petcore_*.py tests/test_architecture.py -q`
  → **1153 passed in 89.74s**；`ruff` 全绿；`python -m petcore --check` exit 0。
- 真实本地 OCR：`HELLO 123` 冷启动 **1954ms**、暖 **791ms**、进程 RSS **145MB**。
- 动作调度：priority 回退、原文传递、异常降级、迟到结果丢弃均有测试。
- 打包与静默安装：setup **257,472,704 字节**、签名 Valid；安装目录 733MB；
  静默安装 exit 0，`--check` 显示 `ai.chat 1.4.0` / `tool.ocr 1.0.0` 均 enabled、
  `diagnostics []`；`{app}\mods\tool.ocr\.trusted` 存在，应用已重启。

**未验证 / 边界**

- rapidocr/onnxruntime/cv2 随 exe 打包（体积显著增大）；不装 `tool.ocr` 时功能
  不启用，但依赖仍在包里。
- OCR 引擎常驻进程内（约 145MB），与主分支「一次一个子进程」不同。
- 真实桌面动作条点击的肉眼验收、混合 DPI 下的快速气泡定位未实机确认。
- 主动识屏未做。

## L043 · 2026-10-22 · 截图动作条文字对比度

**范围**：用户反馈四动作条里「提取文字/翻译/复制图像」文字发白，与浅色条混在一起。

**根因**：覆盖层是 Tool 窗口，按钮未显式指定 `color`，深色系统主题下继承白色
`ButtonText`；浅色条 + 白字 = 看不清。禁用态用的 `rgba(29,33,41,90)` 也偏淡。

**改动**

- `petcore/ui/screen/overlay.py`（+13/−9）：条内所有 `QPushButton` 显式
  `color: #1d2129`；禁用态 `#8a8f99`（不透明灰）；问问肥鱼禁用态提亮为
  `rgba(255,255,255,210)`；条背景提高到 250 不透明度、边框加深到 70。
- `tests/test_petcore_screen_ask.py`（+46）：新增 QSS 断言与「深色 palette 下
  按钮文字仍是深色」的像素级回归（对照组把颜色换成白色后深色像素显著减少）。

**验证**

- `pytest tests/test_petcore_screen_ask.py -q` → **29 passed**；`ruff` 通过。
- 像素回归：深色 palette 下「翻译」按钮矩形内深色像素 > 10；改回白字后 < 5。
- 重建签名 setup（257,472,704 字节）并静默升级 `E:\dsh-pet-core`（exit 0）；
  安装后 `--check`：`ai.chat 1.4.0` / `tool.ocr 1.0.0` enabled、`diagnostics []`；
  像素回归覆盖真实渲染路径，真实桌面目视由用户确认。

**未验证 / 边界**

- 仅覆盖浅色操作条 + 深色系统主题这一组合；如果以后做深色操作条，需要重新配文字色。

## L044 · 2026-10-22 · AI 对话模块分布与 core 占用盘点

**范围**：回答「AI 对话代码分布 / 对 core 有没有占用」，并把这批事实写进架构文档。

**改动**

- `docs/PETCORE-ARCHITECTURE.md`（+113）：新增 §23——core/mod 代码表（行数）、
  core 里明确没有的业务、实测内存/导入成本（启动 +7.1MB/63.5ms、传输层首次
  对话 +6.4MB/77.9ms、快速气泡 +1.8MB、对话窗 +4.9MB、OCR 引擎 +145MB）、
  四项已知耦合与结论。
- `docs/PETCORE-SPEC.md`（+15）：新增 §22 摘要并指向 §23。
- 无代码改动（纯文档批次）。

**验证**

- 行数：`wc -l` / `git diff --numstat` 口径。
- 占用：`QT_QPA_PLATFORM=offscreen` 单次采样（psutil RSS + `perf_counter`），
  数值与 P8 报告的 OCR 145MB 一致。
- `grep` 确认 core 无 `ocr_messages`/提示词实现，唯一用户向文案是
  `api/chat.py` 的 `ChatTruncated`。

**未验证 / 边界**

- 内存是单次采样，不是多次中位数；真实桌面与混合 DPI 下数值会有差异。
- 若以后把 `ChatController` 改成首次使用时惰性创建，启动的 7.1MB/63.5ms 还能再降。

## L045 · 2026-10-22 · P4-a 通用取数端口 + 天气 + 文件解读

**范围**：`PETCORE-HANDOFF-PLAN` §P4 的第一段。Core 只加一条 `HttpPort` 与四个
`weather_*`/`file_reader_*` 配置键；两个业务域都以 `examples/petcore-mods/` 的
host-only feature 落地，默认不装。

**改动**（`git diff --numstat` 口径）

- `petcore/contracts/http.py`（新，110）：`HttpRequest/HttpResponse/HttpError`、
  `HttpPort`。`params` 由 core 编码；`prefer_direct` / `retry_statuses` / `max_bytes`。
- `petcore/net/http.py`（+135）：`build_request`、`fetch_url`——单请求显式路由、
  两次尝试共享 deadline、只在传输失败或 500/502/504 换路由、按块读取带体积上限，
  **不改写**进程级「固定直连」结论；`net/__init__.py` 导出两者。
- `petcore/ui/ports.py`（+70）：`CoreHttpPort`（`request/get_json`），
  `build_ports` 装配 `HostPorts.http`；`contracts/host.py` 增字段；
  `ui/app.py` 保存 `self.ports` 供设置页取用。
- `petcore/config.py`（+40）：`weather_enabled/weather_city/weather_city_list/
  weather_city_codes`、`file_reader_enabled/file_reader_progress_seconds/
  file_reader_record`，全部全局键 + 归一化。
- `petcore/contracts/capabilities.py`（+1）：新增 `files.read`（读取用户拖入的文本文件）。
- `petcore/ui/icons.py`（+3）：菜单/设置页的 `weather` 图标。
- `examples/petcore-mods/tool.weather/`（新，971）：`logic.py`（城市名/地区码/两源解析/
  WMO 与 Beaufort 码表/失败文案）、`client.py`（CN 直连 → open-meteo 代理，6s/请求、
  15s 总预算）、`runtime.py`（后台线程 + 气泡）、`settings_page.py`（启用/城市列表/
  当前城市/立即查看）、`host/__init__.py`、`mod.json`。
- `examples/petcore-mods/tool.file-reader/`（新，502）：`reader.py`（后缀白名单、
  20000 文件/2s 扫描预算、200000 字符有界读取）、`runtime.py`（`interaction.on_drop`
  → 后台 `api.stream_chat` → 气泡 + `ChatPort.record`）、`settings_page.py`。
- `tests/test_petcore_http_port.py`（新，312）、`tests/test_petcore_weather_mod.py`
  （新，338）、`tests/test_petcore_file_reader_mod.py`（新，314）。
- `tests/test_petcore_example_mods.py`（+2）、`tests/test_petcore_installer.py`（+5/-1）：
  新包进可加载清单与安装器任务断言。
- `packaging/petcore.iss`（+6）：`mod_weather` / `mod_filereader`，`Flags: unchecked`，
  `{app}\mods\<id>` + `.trusted`。
- 文档：`PETCORE-SPEC.md` §23、`PETCORE-ROADMAP.md` §23、`PETCORE-MOD-FORMAT.md` §11、
  `PETCORE-ARCHITECTURE.md` §14/§16/§17、`PETCORE-MAIN-PARITY-BACKLOG.md` §3.2/§2/§11、
  `examples/petcore-mods/README.md`、`petcore/AGENTS.md` 四条红线。

**行为变化与理由**

- 天气：国内源直连（本机实测代理慢 7~11 倍，主分支 `docs/NETWORK-PROXY-AND-VPN-2026-09-22.md`
  的结论）、海外源跟随系统代理；失败点名两源，不静默。城市名存储不剥后缀
  （剥离不幂等），只在查询期做变体匹配。
- 文件解读：只读文本类文件，绝不改/移/删；扫描与字符都有硬预算；结果默认进会话，
  可关；菜单提供「解读刚才拖入的文件」，对应主分支没有菜单入口的缺口。
- `files.read` 是新增能力声明；能力仍只是声明 + 用户授权，不是沙箱。

**验证**

- `QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest tests/test_petcore_*.py -q`
  → **881 passed**。本段新增测试 **50 条**（`test_petcore_http_port.py` 17、
  `test_petcore_weather_mod.py` 18、`test_petcore_file_reader_mod.py` 15）。
- `.venv/Scripts/ruff check petcore/... examples/petcore-mods/tool.* tests/test_petcore_*`
  → All checks passed。
- 真实取数（本机，`CoreHttpPort`）：鹿城 0.26s、北京 0.24s、open-meteo 上海 4.51s、
  不存在城市 1.86s 且两源原因都出现。
- `python -m pytest tests/test_petcore_example_mods.py tests/test_petcore_installer.py
  tests/test_petcore_architecture.py tests/test_petcore_settings_coverage.py -q` → 通过。

**未验证 / 边界**

- 天气「明天」与逐城市子菜单未做：主分支右键也只有「显示天气」，逐城市需要 mod 动态
  子菜单端口，留 P4-c 菜单模板批次一起评估。
- 冻结包已重建并静默安装到 `E:\dsh-pet-core`（setup 257,488,664 字节、签名有效）；
  `--check`（安装版 exe + 临时 `--root`）显示两个新 mod `enabled=True`、`diagnostics []`，
  安装后进程存活（PID 23652，RSS 98.9 MB）。真实桌面上的「点菜单查天气 / 拖文件解读」
  需要在用户使用时观察日志，自动化只覆盖到离屏与端口层。
- 文件解读依赖 Core 的 `file_feeding_enabled`（关闭时不会收到 drop 事件），已在
  MOD-FORMAT §8 写明。
- 两个 mod 的设置页只做了离屏测试，真实窗口在 900×620 / 125% DPI 下未实机对照。

## L046 · 2026-10-22 · P4-b 语音端口 + 报时 + 音乐歌词 + 动画循环

**范围**：`PETCORE-HANDOFF-PLAN` §P4 第二段。Core 新增 `SpeechPort`（合成/缓存/预检/
密钥在 core）与动画循环接缝；`voice.chime` 重写、`media.music` 从「媒体键」升级为
Now Playing + 歌词 + 唱跳。两个 mod 仍是可导入包（安装向导默认不勾）。

**改动**（`git diff --cached --numstat` 口径）

- `petcore/contracts/speech.py`（新，111）：`SpeechRequest/SpeechResult/
  SpeechProviderInfo/SpeechError/SpeechPort`，7 类失败 reason。
- `petcore/net/tts.py`（新，603）：edge-tts(WSS)、豆包 SSE、MiMo(OpenAI 兼容)、
  `PROVIDER_SPECS`/`DEFAULT_VOICES`；第三方网络栈只在这里 import。
- `petcore/speech/{__init__,cache,service}.py`（新，13/112/341）：后端链、无网预检、
  非空缓存、2 次重试 + 默认音色兜底、失败删产物、按 mtime 裁剪缓存。
- `petcore/platform/sapi.py`（新，212）：Windows SAPI5（ctypes COM + 线程级 CoInitialize）。
- `petcore/contracts/host.py`（+4/-1）：`PresentationPort.play_animation(loop=)` /
  `release_loop()`、`HostPorts.speech`。
- `petcore/ui/ports.py`（+132/-2）：`CoreSpeechPort`（后台合成 → 共享 `AudioAdapter` →
  回调，`stop/playing`），`build_ports` 装配。
- `petcore/ui/pet_window.py`（+19/-1）：`_loop_active`；`_play_spec()` 唯一清除点；
  `_on_finished()` 在探头/击飞/拖拽接管时解除循环；`release_loop()`。
- `petcore/ui/app.py`（+11/-2）：`_PresentationAdapter` 转发 loop/release；
  `CoreHost.speech = SpeechService(...)`（惰性 import）。
- `petcore/config.py`（+176）：`speech_*` 8 键、`voice_chime_*` 11 键、`music_*` 10 键，
  全部全局作用域 + 归一化（日程白名单、`HH:MM` 清洗、速率/音量夹取、播放器路径清理）。
- `examples/petcore-mods/voice.chime/`（1.1.0）：删除旧 `factory.py`（-134），新增
  `schedule.py`/`quotes.py`/`runtime.py`/`settings_page.py`/`host/__init__.py`；
  运行时无 PySide6。
- `examples/petcore-mods/media.music/`（1.1.0）：`now_playing.py`(450)/`lyrics.py`(519)/
  `players.py`(74)/`runtime.py`(496)/`settings_page.py`(376)/`host/__init__.py`，
  `factory.py` 变包入口 shim（+38/-140）。
- `tests/test_petcore_speech.py`（464 / 16）、`tests/test_petcore_chime_mod.py`
  （478 / 25）、`tests/test_petcore_music_mod.py`（683 / 29）、
  `tests/test_petcore_interaction.py`（+54 / 3 条循环接缝）。
- `petcore/cli.py`（+34）：`--check` 新增 `speech` 字段（backend/fallback/cache_dir/
  providers 可用性），冻结包是否真带 edge-tts/winrt/pycaw 的第一证据。
- `scripts/build_petcore.ps1`（+8/-1）：收集 `edge_tts`、`winrt.windows.media.*`、`pycaw`。
- 文档：SPEC §24、ROADMAP §24、MOD-FORMAT §12、ARCHITECTURE §14、ANIMATION-BACKLOG、
  parity backlog §2/§3.2/§4、examples README、`petcore/AGENTS.md` 三条红线。

**行为变化与理由**

- 合成与传输归 core：`voice.chime` 不再 `import QTextToSpeech`，四家后端共享缓存与
  失败兜底；密钥只进钥匙串（owner `speech.<provider>`）。
- 缓存命中要求非空文件：0 字节产物在边缘 TTS 上曾造成「永久静音」。
- 唱跳用循环接缝：`play_animation(loop=True)` + `release_loop()`，探头/击飞/拖拽
  接管时自动解除，不绕既有素材闸门。
- 歌词直连优先、最多切一次代理（同一 deadline，仅传输失败或 500/502/504，
  带 `Retry-After` 不切）；不重试、不落盘音频、不记录 URL/代理/密钥。

**验证**

- 全量门禁：`pytest tests/test_petcore_*.py tests/test_architecture.py
  tests/test_pr_report_discipline.py tests/test_ci_workflows.py -q` → **1282 passed**；
  `ruff check pet/ tests/ petcore/ examples/petcore-mods` 全绿；
  `python -m petcore --check` 退出码 0。
- 新增测试 74 条（speech 17 / chime 25 / music 29 / loop 3）。
- 真实合成（子代理实测）：edge 26,208 B / 3.39s，缓存命中 0.0s；SAPI 97,234 B wav；
  `speech_backend="sapi"` 118,834 B、缓存命中 0.25s。
- 真实合成（新配置 + `SpeechService` 复测）：providers `edge=True/sapi=True/
  doubao=False/mimo=False`（无 Key）；edge 20,304 B / 2.99s，二次命中 0.000s。
- 真实歌词：`CoreHttpPort` 抓到 33 行（酷狗源，0.66s，网易云只给翻唱被拒后回退）。
- SMTC：本机无播放器时 47ms 返回「系统里没有正在播放的媒体会话」；transport 命令给明确原因。
- 性能（单次采样）：`import petcore.net.tts` +5.3MB/46.6ms（首次合成才加载）；
  `petcore.speech.service` +1.9MB/9.1ms；`SpeechService()` +0.0MB/0.0ms；
  加载 `voice.chime` 包 +14.5MB/86.3ms（含设置页 Qt 控件，仅安装并启用时）。
- 冻结包：setup 258,499,016 B、签名有效；静默安装 exit 0；安装版 `--check`
  `diagnostics []`、`voice.chime`/`media.music` 均 1.1.0 enabled、
  `speech.providers` edge/sapi Available、doubao/mimo `auth`；安装后进程存活
  （PID 20528，RSS 108.6 MB）。冻结包内真实发声未点过（留用户验收）。

**未验证 / 边界**

- 真实播放器下的唱跳与逐行歌词同步未验证（本机没有正在播放的会话）。
- 豆包/MiMo 真实合成本机无 Key；两者报 `auth`，错误分类与字段已测。
- 豆包音调（pitch）未移植 ffmpeg `asetrate` 链，`pitch` 字段对豆包无效（其余后端生效）。
- 口型主分支也未实现，不在本轮。
- 循环解除后会等当前这圈播完才回行为链（最长一个 clip 时长），不是硬切。
- `voice.chime` 的预缓存线程是 daemon、`stop()` 不 join（阻塞的网络合成不能拖住退出）。

## L047 · 2026-10-23 · P4-b2 扩展分组 / 可卸载 / 气泡分页 / 音乐识别

**范围**：Core UI（settings base/style、menu model、bubble、app、page_mods、page_extensions、mods install/state/feature、contracts、config、installer）+ 9 个示例 mod 设置页 + 测试与文档。

**改了什么**

- `petcore/ui/settings/base.py`：新增 `group_heading()`（H2）与 `section(..., level=3)`（H3），H3 卡片左缩进 12px。
- `petcore/ui/settings/style.py`：新增 `QLabel#groupTitle` / `QLabel#subsectionTitle`。
- 7 个示例 mod 的设置页（ai.chat / fun.divination / fun.festival / reminder.todo / tool.file-reader / tool.weather / voice.chime）：一个 H2 + 原分组降为 H3。
- `media.music` 设置页：H2「音乐」+ H3「播放与唱歌 / 歌词显示 / 识别范围 / 切歌与播放控制 / 播放器」，新增过滤开关、额外识别名、四个传输按钮，检测结果显示 `app_id`。
- `petcore/ui/menu_model.py`：`_populate_extensions()` 按 owner 分组；新增 `set_owner_labels()` 与 `set_extension_skip_owners()`；`petcore/ui/app.py` 从 manifest 取 `name_zh` 并跳过 Core 内置特性。
- `petcore/mods/install.py`：新增 `read_install_files()`、`_remove_listed()`、`remove_at()`；按 `install.json` 清单删除，`__pycache__/*.pyc` 一并清，未登记文件保留并报数量；无清单的 `.trusted` 包整目录删除（仍做边界与 `mod.json` 校验）。
- `petcore/mods/state.py`：新增 `clear()`；`petcore/mods/feature.py`：新增 `stop_owner()`。
- `petcore/ui/app.py`：新增 `CoreHost.uninstall_mod()` 与 `_drop_modules_under()`；UI 卸载顺序为「停 runtime → 撤菜单 → 清模块 → 删文件 → 重扫」。
- `petcore/ui/settings/page_mods.py`：trusted 包不再禁用卸载，确认文案说明保留个人数据；删除请求交给 `ExtensionsPage.uninstall_mod()`。
- `petcore/ui/settings/page_extensions.py`：新增 `uninstall_mod()` / `_drop_page()`，删除前对未保存的设置页询问保存/放弃/取消。
- `petcore/ui/bubble.py`：列宽 `248→360`（`column_for_text`）、`paginate_text()`、页码点、滚轮翻页、副标题与正文同 `bubblePanel`；高度按屏幕夹取。
- `petcore/contracts/host.py`、`ui/app.py`、`ui/ports.py`、`ui/pet_window.py`：`say()` 透传 `subtitle`。
- `examples/petcore-mods/media.music`：`allowed_app_ids()` + `accepts_singing/pick_singing_session/transport` 的 `allowed_ids`；`music_filter_enabled` / `music_filter_extra_ids` 两键；歌词气泡副标题「我在唱《…》」，纯音乐「正在听《…》」。
- `petcore/config.py`：两键进 GLOBAL/DEFAULTS/归一化。
- `packaging/petcore.iss`：新增 `[UninstallDelete] {app}\mods`。
- 文档：SPEC §25、ROADMAP §25、MOD-FORMAT §13、parity §12、AGENTS 红线、INDEX/LOG。

**验证**：`ruff check pet/ tests/ petcore/ examples/petcore-mods` 全绿；petcore 全量门禁与实机记录见 `docs/PR-REPORT-PETCORE-P4B2-2026-10-23.md`。

**未验证 / 风险**：真实播放器下的识别范围过滤（本机无播放器）；安装版卸载（构建后复验）；Python 模块不热重载，卸载后仍需重启才彻底释放；`__pycache__` 保留在用户包目录时会被当作生成物删除。

## L048 · 2026-10-23 · P4-b2.1 菜单分组落地 / 设置宽行 / 气泡显式尺寸

**范围**：`petcore/ui/menu_model.py`、`menu_layout.py`、`petcore/ui/app.py`、`petcore/ui/settings/base.py`、`petcore/ui/bubble.py` + 测试与文档。

**改了什么**

- 菜单分组：`MenuModel._append_node()` 对 `extensions_hub` 不再平铺 children，只把 children 的 id 作为 `allowed` 传给 `_populate_extensions()`；`allowed` 为空时回退全部可见项，避免旧 layout 把扩展菜单变成空。
- 旧 layout 迁移：新增 `migrate_p10_menu()`，把模板放在 hub 之外的 Core action（links / 快捷启动 / 网络信息 / 回收内存 / 免打扰）从 `extensions_hub` 搬回模板位置；`reload_menu()` 在 p8/p9 之后调用并持久化，幂等。
- 内置包名：`_register_feature_menus()` 对不在 loader 结果里的内置接缝包补读 manifest 的 `name_zh`，扩展菜单不再显示 `core.feature-demo`。
- 设置宽行：`SettingsCard.add_row(expand=True)` 改为纵向布局（文字块整行、控件另起一行）；非 expand 行的文字块包成 `settingTextBlock`，最小宽 180px。
- 气泡：`label.setWordWrap(False)` + `QFontMetrics` 显式算宽高（最长行 + 4px，下限 96；行数 × lineSpacing + 2），`setFixedSize()`；副标题同宽并 `heightForWidth()`；图片说明改固定宽 + 按宽算高；`show_text()` 在 `show()` 后再 `adjustSize()+_place()` 一次，消除「慢一句」；分页末页孤行从前页借一行。

**验证**：`ruff` 全绿；聚焦 348 passed（含 docs）；全量 1302 passed；`python -m petcore --check` exit 0。离线复现探针从「4 字 26×13 / hfw 28」变为「96×17 / hfw 17」，5/10 字均单行，长文 8 页 0 条超宽行。安装包复验见 PR 报告「第二轮修正」。

**未验证 / 风险**：真实桌面右键菜单截图（需启动安装版人工确认）；迁移会改写用户 layout 中 Core 项的位置（只搬模板明确放在 hub 之外的项，幂等）；`allowed` 为空时回退全部可见项，用户「隐藏所有 mod 项」的极端情况会重新出现。

## L049 · 2026-10-23 · 设置窗口标题栏最小化/最大化

**范围**：`petcore/ui/settings/dialog.py`、`tests/test_petcore_settings.py` + 文档。

**改了什么**：`SettingsDialog.__init__` 在原生旗标上补 `WindowMinMaxButtonsHint`，标题栏出现最小化/最大化按钮；非模态、X 保存、取消/Esc 回滚、Enter 保存的语义不变；最大化/还原由系统处理，首次显示仍按宠物位置放置。

**验证**：`test_settings_dialog_has_minimize_and_maximize` 断言旗标含 MinMax/Close、非模态，并真实走 `showMinimized/showMaximized/showNormal` 状态；真实桌面抓图确认「— / □ / ×」三个原生按钮（`.scratch/settings-minmax.png`）；聚焦 settings 族与全量 1303 passed。

**环境限制**：新 setup 在本机被 Smart App Control 拦截（签名有效、CodeIntegrity 3077/3118），改用 `dist/dsh-pet-core` 覆盖安装目录部署并 `--check` 通过。

## L050 · 2026-10-23 · P4-c1 窗口级抓屏 + ai.chat 主动识屏

**范围**：`petcore/platform/{foreground,screenshot,image_hash}.py`、
`petcore/contracts/{screen,host}.py`、`petcore/ui/ports.py`、`petcore/config.py`、
`petcore/menu_templates/petcore-default-v1.json`、`petcore/ui/menu_layout.py`、
`examples/petcore-mods/ai.chat/host/{proactive,runtime,settings_page,__init__}.py`、
测试 4 个文件 + 新增 `tests/test_petcore_proactive.py` + 四份文档。

**改了什么**（`git diff --numstat`，含新增文件）：

- `petcore/platform/foreground.py` +98/−4：`foreground_window_info()` 补 `hwnd/rect`
  （DWM `EXTENDED_FRAME_BOUNDS`，失败回退 `GetWindowRect`）、`DWMWA_CLOAKED` 过滤、
  `idle_seconds()`。
- `petcore/platform/image_hash.py`（新增 60 行）：9×8 差分哈希 + Hamming；先缩放
  再转灰度（1200×800：9.87 ms → 1.58 ms），平滑重采样避免采样抖动造假差异。
- `petcore/platform/screenshot.py` +150/−1：Windows 上用 GDI `BitBlt` / `StretchBlt`
  按窗口**物理像素矩形**直接抓（`_grab_desktop_rect`），不再做逻辑/物理换算——
  150% 缩放下「Qt 逻辑几何 + DPR 裁剪」实测会 100% 失败。两段式：
  `sample_window()`（192 边缩略图 → 指纹，不编码 PNG）与 `capture_window()`
  （768 边 + PNG + `dh_hash`）；窗口大半在屏外判 `outside_screen`；`grabber=`
  只给测试注入。
- `petcore/contracts/screen.py` +31：`ScreenCapture.hwnd/dh_hash`、`WindowInfo`、
  `ScreenPort.foreground_window()/capture_foreground()`（docstring 写明双复核与内存语义）。
- `petcore/contracts/host.py` +11/−1：`SystemPort.idle_seconds()`、`ConfigPort.set()`
  （只给显式用户动作）。
- `petcore/ui/ports.py` +65/−1：`QtScreenPort` 两个窗口采样方法（前后各查前台
  `hwnd`）、新 `SystemAdapter` 并接进 `build_ports(system=...)`、`QtConfigPort.set()`
  写全局配置并落盘。
- `petcore/config.py` +64：12 个 `ai_proactive_*` 键（默认值/钳制/白名单 50 条 ×120 字）
  全部进 `GLOBAL_CONFIG_KEYS`。
- `petcore/menu_templates/petcore-default-v1.json` +1、`petcore/ui/menu_layout.py` +1：
  在 `look_screen` 之后加 `ai.chat:proactive` 节点。旧布局不需要单独的迁移函数：
  `_merge_template_actions()` 已按模板补注册项并写回（诊断
  `default-action-added:ai.chat:proactive`），幂等。
- `examples/petcore-mods/ai.chat/host/proactive.py`（新增 470 行）：白名单/停留/
  `ProactiveLimiter`（状态文件原子写、日上限/间隔/冷却/3 次失败熔断）/
  `ProactiveWatcher`（8 s 心跳、每 tick 现读配置、代次作废迟到回调、dry-run、
  请求 180 s 不回调则自愈清忙碌）。
- `ai.chat/host/runtime.py` +71/−17：命令 `proactive_toggle`、`proactive_cue/ask/answer`、
  `_start_vision(on_fail=)`、`_record_look()` 复用；`start/stop` 接管看门狗。
- `ai.chat/host/settings_page.py` +327/−1：「主动识屏」H3 分节（12 键 + 从当前前台
  窗口添加，按软件/按标题二选一）、`_sync_proactive()` 取消回滚、搜索关键词。
- `ai.chat/host/__init__.py` +22/−1：菜单贡献 `proactive`（`checked` 读配置）。
- 测试：`test_petcore_proactive.py`（新增 17 条）、`test_petcore_screenshot.py`
  （dHash/裁剪/裁剪越界 +6）、`test_petcore_ai_mod.py`（运行时/设置页 +6）、
  `test_petcore_config_scope.py`（+2）、`test_petcore_menu_layout.py`（+2，并更新
  模板顺序断言）。

**行为变化**：右键多一个「主动识屏」勾选项（默认关；`ai.chat` 未安装时不存在）；
设置 →「AI 对话」多一个「主动识屏」分节；开关走 `ConfigPort.set` 立即落盘。默认
不工作——白名单为空即不触发。答复只冒气泡，`ai_proactive_sync_chat` 为真时静默写
会话，绝不打开对话窗。

**验证**：

- `ruff check pet/ tests/ petcore/ examples/petcore-mods`：All checks passed。
- 全量门禁：`pytest tests/test_petcore_*.py tests/test_architecture.py
  tests/test_pr_report_discipline.py tests/test_ci_workflows.py -q` →
  **1345 passed, 1 skipped**。唯一 skip 是 `tool.ocr` 的真实引擎用例：本机 Smart App
  Control（`VerifiedAndReputablePolicyState=1`）在 16:49 起以 WinError 4551 拦截
  `shapely/_geos*.pyd`，测试按「确切的 4551」跳过并在报告里点名，不是放宽断言。
- `python -m petcore --check` → exit 0，`diagnostics: []`，`menu_template: json`。
- 性能（本机 150% 缩放，`QT_QPA_PLATFORM=offscreen`）：
  dHash 1920×1080 中位 3.2 ms / 800×600 0.74 ms（改序前 1200×800 要 9.87 ms）；
  `sample_window` 1200×800 中位 **33.8 ms**（只缩到 192 边算指纹，不编码 PNG）；
  确认变化后的 `capture_window`（768 边 + PNG）中位 66 ms；关闭态 tick 配置读
  0.0002 ms。稳态 = 每 8 s 一次采样 ≈ 34 ms（0.42% 占空）。
- **真机发现的缺陷**：最初实现是「Qt 抓整屏 + 按 DPR 裁剪」，本机 150% 缩放
  （逻辑 1280×800 / 物理 1920×1200）下窗口矩形被二次放大，裁剪只剩 24% 直接判
  `outside_screen`——功能 100% 不工作。改为 GDI `BitBlt`/`StretchBlt` 按物理矩形
  直接抓之后，真机取到 960×1128 / 239 KB 的窗口 PNG。
- 安装/真机 dry-run 见 PR 报告 `docs/PR-REPORT-PETCORE-P4C1-2026-10-23.md` §3。

**未验证 / 风险**：

- offscreen 平台 `grabWindow` 返回空图，窗口抓图只用注入的 `FakeScreen` 验证裁剪
  数学；真实桌面抓图在安装版上人工验证。
- 混合 DPI / 负坐标副屏：按「交集最大的屏 + 物理像素比例」换算，数学上成立，
  未在真机双屏复核。
- 抓图在 GUI 线程执行（Qt `grabWindow` 不保证跨线程安全），单次 ≈18 ms；
  若后续出现拖动卡顿，考虑移到 core 的专用线程或 printwindow 方案。
- 主分支的陪伴记忆（`proactive_memory`）、免费视觉 provider、HDR 抓屏未移植，
  记入 parity backlog §13。
- **环境 blocker（已解决）**：17:00 前后本机一度以 WinError 4551 拦截未签名的
  原生扩展（`shapely/_geos*.pyd`）和新构建的 exe。用户关闭火绒相关监测后复测，
  同一份新构建可直接运行，`shapely` 也恢复正常——拦截来自第三方安全软件的驱动层，
  不是 SAC 对开发证书的硬性拒绝（`VerifiedAndReputablePolicyState` 仍为 1）。
  19:40 已用 setup 静默升级安装到 `E:\dsh-pet-core` 并跑通 `--check` 与主动识屏
  dry-run。

## L051 · 2026-10-23 · 系统缩放（DPI）适配：模型图像封顶 / 图标与缩略图物理绘制

**范围**：`petcore/contracts/screen.py`、`petcore/platform/screenshot.py`、
`petcore/ui/ports.py`、`petcore/ui/icons.py`、`petcore/ui/menu_actions.py`、
`examples/petcore-mods/ai.chat/host/runtime.py`、测试 3 个文件、SPEC §27、
MOD-FORMAT §14、`petcore/AGENTS.md`、PR 报告。

**改了什么**：

- `contracts/screen.py` +22：新常量 `VISION_MAX_EDGE = 1024` 与
  `ScreenPort.shrink_for_vision(png, max_edge=1024)`；`screenshot.shrink_png()`
  按物理像素等比缩小并重新编码，空图/坏图/未超限原样返回。
- `platform/screenshot.py` +26 / `ui/ports.py` +8：`capture_desktop()` 抓虚拟桌面
  （所有显示器并集），`QtScreenPort.capture()` 改走它——与主分支 `all_screens=True`
  对齐，多屏/混合 DPI 不再只抓宠物所在屏；`QtScreenPort.shrink_for_vision()`。
- `ai.chat/runtime.py` +25/−4：新增 `_vision_bytes()`；「看看屏幕」「主动识屏」
  「截图提问」「提取文字/翻译的 AI 兜底」四处送模型的图统一先过它。本地 OCR 与
  「复制图像」仍拿原图。
- `ui/icons.py` +40/−12：`icon()` / `file_icon()` / `icon_from_spec()` 接受 `dpr`，
  按 `screen_dpr()` 用物理尺寸绘制并写回 DPR（1x/1.5x/2x → 36/54/72 像素）。
- `ui/menu_actions.py` +18/−5：动画缩略图按 `physical_side(64, screen_dpr(window))`
  解码，回调里写回 DPR；高分屏菜单不再把 64px 小图放大。
- 测试：`test_petcore_screenshot.py`（封顶/原样返回/DPR 无关 +3）、
  `test_petcore_menu.py`（图标物理尺寸 +2）、`test_petcore_ai_mod.py`
  （视觉载荷走端口封顶 +2）。

**行为变化**：高 DPI 屏上「看看屏幕 / 截图提问」送给模型的图不再随缩放翻倍
（150% 下 1920×1200 → 1024×640，287 KB → 165 KB）；菜单图标与动画缩略图更清晰；
其它布局/位置/钳制逻辑未变（本来就是逻辑像素）。

**验证**：

- `ruff check pet/ tests/ petcore/ examples/petcore-mods`：All checks passed。
- 全量门禁 **1356 passed, 0 skipped**（上一批唯一 skip 的 OCR 真实引擎用例本机已恢复：
  用户关掉火绒相关监测后，`shapely/_geos*.pyd` 不再被 WinError 4551 拦截）。
- 真机探针（`.scratch/probe_dpi.py`，本机 150%）：屏幕逻辑 1280×800 / dpr 1.5；
  整屏 1920×1200（288 KB）→ 模型输入 1024×640（166 KB）；前台窗口 1920×1128 →
  768×451（111 KB）；框选 400×200 逻辑 → 600×300 物理。
- 菜单图标尺寸：`dpr 1.0/1.5/2.0 → availableSizes 36/54/72`（真实 Qt 断言）。
- 打包/安装：19:39 构建（exe SHA-256 `0d3169b2…c1f`、setup `37366e58…f31`，签名
  Valid），静默升级到 `E:\dsh-pet-core`；安装版隔离 APPDATA `--check` exit 0，
  dry-run 日志两条（窗口采样在冻结包内可用）。

**未验证 / 风险**：

- 菜单对勾图仍是 14px QSS 资源（Qt 对 QSS `url()` 的 @2x 支持随版本而异，未实机
  核对），高 DPI 下可能略软；不影响功能。
- 混合 DPI 多屏只做了逻辑层测试，未在真机双屏复核。
- 图片封顶是物理像素口径（与主分支一致）；若视觉模型对小字识别不稳，再考虑改成
  逻辑像素口径（成本随 DPI 上升）。

## L052 · 2026-10-23 · P4-c2：Agent 联动 `ai.agent` 扩展（只读联动）

**范围**：`petcore/contracts/host.py`、`petcore/ui/ports.py`、`petcore/ui/app.py`、
`petcore/ui/icons.py`、`petcore/config.py`、`petcore/menu_templates/petcore-default-v1.json`、
`petcore/ui/menu_layout.py`、`packaging/petcore.iss`、新包
`examples/petcore-mods/ai.agent/`（`mod.json` + `host/{__init__,constants,protocol,tailer,links,runtime,settings_page}.py`）、
新测试 `tests/test_petcore_agent_mod.py`、`tests/test_petcore_installer.py`、
`tests/test_petcore_menu_layout.py`、`examples/petcore-mods/README.md`、
`docs/PETCORE-SPEC.md` §28、`docs/PETCORE-MOD-FORMAT.md` §15、
`docs/PETCORE-ARCHITECTURE.md` §25、`docs/PETCORE-ROADMAP.md` §27、
`docs/PETCORE-MAIN-PARITY-BACKLOG.md`、`docs/AGENT_LINK_PROTOCOL.md` §7、
`petcore/AGENTS.md`、PR 报告 `PR-REPORT-PETCORE-P4C2-2026-10-23.md`。

**改动说明**

1. Core 只加三条通用接缝，不认识任何 Agent：
   - `StoragePort.data_root()`（`StorageAdapter` 返回数据根）——mod 才能定位
     `<data>/agent-events/` 与 `<data>.parent/dsh-pet-bridge/`；之前只有
     `feature-data/<owner>`。
   - `PresentationPort.visible()`（`_PresentationAdapter` 读 `window.isVisible()`）——
     隐藏态不切联动动画。
   - `PetHandle.animations()`（`PetHandleImpl` 走 `catalog.names_for(GraphType.ACT)`）——
     联动动作池按角色真实动作名挑选。
   - `ui/icons.py` 新增 `agent` 图标（菜单/设置页共用）。
2. 13 个 `ai_agent_*` 全局键（`GLOBAL_CONFIG_KEYS` + `DEFAULTS` + `_normalize`）：
   总开关/两个来源/自定义列表/动画/过程气泡/完成通知/切换间隔/音效四键；
   自定义条目清洗 `_agent_links()`（key 校验、去重、≤8、保留 `dsh` 键）。
3. `ai.agent` v1.0.0（host-only）：
   - `protocol.py`：`state` 优先、`event` 查表（Claude hooks + DSH 事件 + 裸状态名）、
     `turn/end` 带 `aborted/blocked/max_tokens/unknown` 收敛 idle、BOM/半行/4KB 上限。
   - `tailer.py`：`ByteTailer`（首次见到已存在文件跳到末尾、监视后新增文件从头读、
     单次 64KB、半行 ≤8KB、截断/轮转重读一代）、`GlobTailer`（`dsh*.jsonl`，
     启动快照判历史，≤8 文件按 mtime）。
   - `links.py`：`LinkEngine`，折叠多写者、去抖+节流、800 ms 完成确认、
     5 s 完成冷却、错误回合报「这轮好像没能跑完」、动作池 `写代码/吃Token` 交替 +
     每 3 次插播摸鱼、过程气泡 10/8/60 s、隐藏态不切动画、三档音效带冷却。
   - `runtime.py`：`Runtime` 协议（start/stop/close），单条轮询线程 1.5 s，
     配置每轮现读（含来源签名重建），解析结果经 `scheduler.call_later(0, …)` 回 GUI；
     关闭期间继续推进偏移（重新开启不回放）；`commands={"agent_toggle"}`。
   - `settings_page.py`：H2 + 5×H3（总开关 / 事件来源 / 动画与气泡 / 音效 / 诊断），
     控件改动即时写全局配置，`discard_changes()` 从打开时快照回滚，诊断面板列出
     来源目录与最近一行事件，「发送测试事件」写 demo 事件走完整链路。
   - 菜单贡献 `ai.agent:enabled`（label「Agent 联动」，`checked` 读 `ai_agent_enabled`）。
4. 菜单模板在两个副本（JSON + `BUILTIN_DEFAULT_LAYOUT`）都插在 `ai.chat:proactive`
   之后，旧布局由既有 `_merge_template_actions()` 幂等补入。
5. 安装器：`mod_agent` 任务 `Flags: unchecked`，勾选才写 `{app}\modsi.agent` + `.trusted`。
6. 已知取舍：完成态不回「待机」动画（Core 没有 `request_link_idle` 等价 API），只
   `release_loop()`；一次性动作播完由行为链自然接管。

**验证**

- `QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest tests/test_petcore_*.py tests/test_architecture.py tests/test_pr_report_discipline.py tests/test_ci_workflows.py -q`
  → **1426 passed**（基线 1356，新增 70）。
- `.venv/Scripts/ruff check pet/ tests/ petcore/ examples/petcore-mods/` → All checks passed。
- `.venv/Scripts/python -m petcore --check` → exit 0，`menu_template=json`。
- 隔离 root 安装冒烟：`--root .scratch/agent-root --portable mods install examples/petcore-mods/ai.agent`
  → 装好后 `allow_feature_mods=false` 时如实报 `ai.agent: feature mods disabled`；
  打开 `allow_feature_mods` 后 `mods=[ai.agent 1.0.0 enabled]`、`diagnostics: []`。
- 源码树端到端（真实 `Config` + 真实 `QtScheduler` + 真实文件 + 真实 mod 包，offscreen，
  `.scratch/probe_agent_e2e.py`）：三条事件（`UserPromptSubmit` / `PreToolUse(bash)` / `idle`）
  → `animations=['写代码']`、`bubbles=[('正在跑命令', False), ('干完活啦', True)]`、
  `states={'demo': 'idle'}`。`thinking→working` 间隔 1 s 被 2 s 节流，只切一次动作（预期）。
- 开销（`.scratch/probe_agent.py`）：`parse_line` 2.86 µs/行、`LinkEngine.handle` 4.19 µs/事件、
  追加 10 行的 tail 读取 0.18 ms、loader 导入 95 ms（启动一次性）。
- 真机 GUI 冒烟（`--root .scratch/agent-root --portable --allow-feature-mods --verbose`，
  15 秒后 kill）：四条事件全部按 `state=thinking → working → working → idle` 落到日志，
  无异常、无 `animations() failed`。**这一轮抓到两个只在真机才暴露的问题**：
  ① `_log(level=…)` 参数不存在（单元测试用替身 ctx 没调这条路径）；
  ② `PetHandleImpl.animations()` 用了不存在的 `GraphType.ACT`——异常被端口吞掉后
     mod 拿到空动作池，联动永远不切动作。两处都已修，并补 `test_pet_handle_animations_*
     use_the_act_graph` 两条回归（用真实 `GraphCatalog`）。

**未验证 / 风险**

- 未在真实 DSH 会话下长时间跑（本机桥接目录是否存在取决于用户是否装过桥接插件）；
  诊断面板会如实显示「目录缺失」。
- 未真机验收音效三档（需要勾选音效并触发一次完成/报错）。
- 未做写方安装（DSH 插件 / Claude hooks / Cursor / OpenCode 直读）、审批卡片、成本统计，
  见 ROADMAP §27「未做（c2.1）」。
- 打包/签名未重跑（本批次只改源码树与 iss）。

## L053 · 2026-10-23 · P5-a1：灵动岛 v1（胶囊 + 3 段 + 展开卡片）

**范围**：新增 `petcore/island/{__init__,segments,layout}.py`、
`petcore/platform/{sysmon,netmon}.py`、`petcore/ui/island.py`；改
`petcore/config.py`、`petcore/ui/{app,pet_window,menu_actions}.py`、
`petcore/ui/settings/{pages_core,coverage}.py`、
`petcore/menu_templates/petcore-default-v1.json`、`petcore/ui/menu_layout.py`；
新增测试 `tests/test_petcore_island_{segments,layout,sampling,window}.py`；
文档 SPEC §29、ARCHITECTURE §26、ROADMAP §28、parity、function matrix、
`petcore/AGENTS.md`、本 log + 索引、PR 报告 `PR-REPORT-PETCORE-ISLAND-2026-10-23.md`。

**改动说明**

1. **零 Qt 契约**（`island/segments.py`）：直接从主分支 `pet/island_segments.py`
   移植宿主侧那半（状态词表、`SegmentSnapshot`、盒宽自测量 + 字体指纹缓存、
   字段盒右省略护栏、`split_value_unit`、四态 `render_compact`、两级告警 + 3pt 滞回、
   `highest_severity`），砍掉第三方注册面（`SegmentDeclaration`/`register_segment`/
   `seed_thresholds`/`drop_thresholds`）。主分支同名单测 40 条原样移植、全绿。
2. **零 Qt 布局**（`island/layout.py`）：移植 `_InfoSeg`/`_SegPlacement`/
   `place_info_segments`（19px 槽位、竖线、双盒单位）、`elide_to_width`、
   `plan_info_row`（名字省略→隐藏→尾部折 `+N`→保底）、`apply_shrink`（按 id 折、
   chip 告警取当刻最高级）、`chip_segment`、`anchored_x`（三分区锚点）。
   差异：`_NETWORK_BOX_SAMPLE` 改成读 `netmon.NETWORK_BOX_SAMPLE`（本批网络段无延迟）；
   `_InfoSeg` 新增 `detail` 字段（卡片行素材，例如 `error_reason`），布局不看它。
3. **采样**（`platform/sysmon.py`、`platform/netmon.py`）：`CpuMeter` 首拍只建基线；
   CPU/内存共用一条 1 Hz 线程（间隔硬上限 1 Hz）；网络用
   `psutil.net_io_counters()` 差分 + EMA(0.5)，首拍返回 None、计数器回绕按 0，
   `format_rate` 5 列定宽。**刻意不做 UDP 延迟探测**：那需要 socket，而架构红 line
   把网络库锁在 `petcore/net/`；`format_network_slot` 只输出 `↓ … ↑ …`。
4. **窗口**（`ui/island.py`）：44px 高固定胶囊（内缩 6、圆角 = 高/2、宽 = 行自然宽、
   下限 120），深/浅/玻璃三套配色 + 五档强调色，自绘胶囊与面板（QSS 不参与）；
   点击展开/收起卡片、拖动即收起并夹回屏幕、松手写 `island_x/island_y`、
   展开悬停暂停 15s 自动收起；卡片一行一段（段名 / 值 · 详情），错误段写原因标 `crit`，
   展开态按**卡片宽**重排（放不下折 `+N`，不画到窗外）；隐藏/停用时停掉采样线程。
5. **配置**（13 个 `island_*`）：总开关、三个显示开关、信息位（时间/自定义）、配色、
   不透明度（0.4–1.0）、强调色、单击行为、指标白名单（`network/cpu/mem/gpu/fps`，
   去重保序 ≤8）、坐标（有限值 ±100000，否则自动定位）。
6. **接线**：`CoreHost.ensure_island()/apply_island_settings()/toggle_dynamic_island()`；
   `apply_live_settings()` 顺带刷新（设置页实时预览）；`quit_app()` 停线程；
   `PetWindow.toggle_dynamic_island()` 转发；菜单项 `dynamic_island`（`pet_controls`
   内、`edge_probe` 之后，默认可见）；设置页「外观与交互 → 灵动岛」（7 行）；
   `coverage.py` 登记落点。
7. **真机渲染修正（抓图发现，已修）**：状态点原来按「窗口右边 − 内缩」反推，
   比内容右缘多退一个间距 → 压到末段上；改成按行计划预留的 trail 槽（`end + STATUS_GAP`）。
   同时补上展开态整改：面板背景（原来透明）、行按卡片宽重排、卡片文字颜色跟随配色、
   纯 glyph 段在卡片里显示中文名。

**验证**

- `QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest tests/test_petcore_*.py tests/test_architecture.py tests/test_pr_report_discipline.py tests/test_ci_workflows.py -q`
  → **1541 passed**（基线 1430，本轮 +111：segments 40、layout 17+2、sampling 33、
  window 21）。
- `.venv/Scripts/ruff check pet/ tests/ petcore/ examples/petcore-mods/` → All checks passed。
- `.venv/Scripts/python -m petcore --check` → exit 0。
- 真机渲染探针（`.scratch/probe_island_real.py`，真字体 + 150% DPI）：收起 434×44 逻辑
  （651×66 物理），内容 `深深 | 22:30 | ↓1.2M ↑340K | CPU 87%(warn) | 内存 61% •`；
  展开 349×205，行 `('primary','network','cpu','chip')`、level 4（末尾折 `+1`）。
- 整程序冒烟（`--root .scratch/island-app --portable --verbose`，9 秒）：
  `island apply: metrics=['network','cpu','mem'] style=dark`，0 错误 0 traceback。

**未验证 / 风险**

- 混合 DPI 双屏、真实多屏迁移未复核（只按 `screen.availableGeometry()` 做逻辑像素）。
- 卡片行未做 tooltip/无障碍描述（主分支有），长错误原因会被截断显示。
- 网络段没有延迟读数（架构红线），要延迟得先在 `petcore/net/` 加探测端口。
- 打包未重跑：新增的是纯 Python 模块，PyInstaller 静态分析能收到；`psutil` 早已在构建脚本。


## L054 · 2026-10-24 · 已复现问题收口：安装、菜单、采样与 Agent

**范围/理由**：执行既有能力质量计划；Python3.11清理失败不能吞、Qt子菜单不能失主、
GUI停用不能等阻塞读取也不能丢线程引用。新增世代隔离采样器，布局折叠同步采样需求；
Agent 同类阻塞/迟到回调已复现后修复，不改变默认开关，不启动 P4-c3。

**文件（相对本轮基线累计 numstat；含后续托盘刷新回归）**

| 文件 | 增/删 |
|---|---|
| `petcore/mods/install.py` | +27 / -14 |
| `petcore/platform/sampling.py` | +135 / -0 |
| `petcore/platform/sysmon.py` | +25 / -32 |
| `petcore/platform/netmon.py` | +28 / -33 |
| `petcore/ui/menu_model.py` | +11 / -4 |
| `petcore/ui/menu_actions.py` | +3 / -3 |
| `petcore/ui/app.py` | +8 / -2 |
| `petcore/ui/island.py` | +7 / -3 |
| `examples/petcore-mods/ai.agent/host/runtime.py` | +55 / -22 |
| `tests/test_petcore_install.py` | +92 / -0 |
| `tests/test_petcore_monitor_lifecycle.py` | +261 / -0 |
| `tests/test_petcore_island_sampling.py` | +2 / -0 |
| `tests/test_petcore_island_window.py` | +49 / -4 |
| `tests/test_petcore_menu.py` | +33 / -0 |
| `tests/test_petcore_tray.py` | +26 / -0 |
| `tests/test_petcore_agent_mod.py` | +101 / -0 |

**验证**：menu red11→green32；tray刷新 red1→green33；install red7→green35；
island red2→UI green88；monitor red12→related140；Agent red5+2→green79。
准确命令与输出文件见 [质量审查报告](PR-REPORT-PETCORE-QUALITY-REVIEW-2026-10-24.md)。
**限制**：取消不是强杀 OS 读取；永久阻塞只保留旧 daemon，不启动重叠线程；整仓门禁另记 L056。

## L055 · 2026-10-24 · 逐域后台生命周期与资源审查修复

**范围/理由**：HTTP/HTTPError 确保 close；Speech 错误回调回GUI；天气/文件停止后不再发布，
线程列表剪枝，文件请求传 cancel；音乐停用保留阻塞SMTC线程且旧歌词不得跨代。
AI 请求按 cancel 身份发布，旧完成不能删新取消句柄；vision 停用可取消；proactive旧结果不清新busy。
Windows MSG 用真实结构读取，修64位HWND高位误当消息ID。没有添加网络/常驻线程。

**文件（相对本轮基线累计 numstat）**

| 文件 | 增/删 |
|---|---|
| `examples/petcore-mods/ai.chat/host/proactive.py` | +4 / -2 |
| `examples/petcore-mods/ai.chat/host/runtime.py` | +67 / -38 |
| `examples/petcore-mods/media.music/host/runtime.py` | +41 / -20 |
| `examples/petcore-mods/tool.file-reader/host/runtime.py` | +36 / -7 |
| `examples/petcore-mods/tool.weather/host/runtime.py` | +25 / -9 |
| `petcore/net/errors.py` | +19 / -12 |
| `petcore/net/http.py` | +11 / -9 |
| `petcore/platform/session.py` | +6 / -3 |
| `petcore/ui/ports.py` | +7 / -6 |
| `tests/test_petcore_ai_mod.py` | +120 / -0 |
| `tests/test_petcore_file_reader_mod.py` | +84 / -0 |
| `tests/test_petcore_http_port.py` | +21 / -0 |
| `tests/test_petcore_music_mod.py` | +62 / -0 |
| `tests/test_petcore_net.py` | +11 / -0 |
| `tests/test_petcore_proactive.py` | +23 / -0 |
| `tests/test_petcore_speech.py` | +70 / -0 |
| `tests/test_petcore_weather_mod.py` | +42 / -0 |
| `tests/test_petcore_session.py` | +37 / -0 |

**验证**：HTTP red4→green151；Speech red3→green66；天气/文件 red5→green38，
随后后台相关176 passed；音乐 red2→green37；AI身份/vision各red2、proactive red1→相关98passed；
MSG red3→green3，关联59 passed/1 skipped。所有原始输出在 `.scratch/petcore-quality-review/`。
**限制**：真实网络服务/TTS/SMTC播放器未使用账号验收；没有触发真实系统退出。

## L056 · 2026-10-24 · 五域审查证据、主题测试隔离与阶段口径

**文件**：`tests/test_petcore_p5.py` +9/-5：像素断言原先只适用于亮色，暗色探针red1，
显式亮/暗两主题后菜单相关63passed；没有改产品QSS。文档更新 `petcore/AGENTS.md`、
`petcore/README.md`、`examples/petcore-mods/README.md`、`docs/PETCORE-SPEC.md`、
`PETCORE-ROADMAP.md`、`PETCORE-MAIN-PARITY-BACKLOG.md`、`PETCORE-ANIMATION-BACKLOG.md`、
`PETCORE-LOG.md`/`PETCORE-LOG-INDEX.md`/`INDEX.md`；两份历史PR报告仅追加范围纠正，保留原文。
新增 `PR-REPORT-PETCORE-QUALITY-REVIEW-2026-10-24.md`；逐文件原因及完整增删行数以该报告为准。

**口径**：P4-c2只读Agent、P5-a1已实现；待办调度/农历/48签/Agent写端/灵动岛二阶段未做。
设置X保存，取消/Esc恢复；不把选定petcore测试说成仓库全量；不启动下一阶段。

**验证/风险**：15s预热+60s×3采样前后实测；Windows125%单屏隔离源码运行与30轮资源检查；
Python3.12仅stdlib安装/覆盖/卸载/只读清理冒烟（无Qt/pytest）。CPU高负载三遍与最终测试数字
见报告；整仓存在旧路径兼容/菜单/编码失败与设置退出顺序相关原生崩溃，尚未全绿。
不得把本条作为合并/分发验收通过。准确未闭环项和继续断点在 `.scratch/petcore-quality-review/HANDOFF.md`。

## L057 · 2026-10-24 · P4-c3 A：整仓退出阻断与兼容性收口

**边界**：主代理串行，保留上一轮48个未提交文件。开始快照与当前原件校验在
`.scratch/petcore-p4c3/baseline/` / `preserved-baseline.json`；原48文件均保留，
其中仅5份阶段/索引文档追加本批内容。无提交、推送、发布或系统配置变更。

**根因与改动**：`pet/app.py`对子窗口直接`deleteLater()`，去除可在父对象销毁后调用
失效QObject的零延时singleShot；`pet/sprite_menu.py` / `pet/overlay_shell.py`子菜单显式绑定父菜单。
`pet/frameseq_provision.py`私有L8掩膜用字节读取兼容Pillow11；新增像素测试辅助器，保留5族原断言。
新增独立进程销毁/真实两测试顺序回归与菜单GC/释放回归。历史品牌门禁仅精确允许旧报告两行真实
分支引用，新增锁范围测试；没有修改该报告正文、全局Qt补丁、版本降级或新增skip。
逐文件原因及本批/对HEAD两种增删行数见[本批PR报告](PR-REPORT-PETCORE-P4C3-A-2026-10-24.md)第二节。

**环境**：项目内`.venv`为Python3.11.1，Qt6.11.1/Pillow11.2.1；从原requirements安装，
约束/freeze/pip-check保存在scratch，未改全局Python或提高最低版本。原环境OCR失败因缺
omegaconf与OpenCV发行名不匹配，独立环境依赖/OCR族68passed，pip check无冲突。

**验证**：旧失败族25failed→关联Pillow/文档260passed；原退出码0xC0000005，新增回归4failed→
设置/菜单92passed。完整无路径过滤pytest：**9716passed、20skipped、15warnings，1315.19s、exit0**；
原始full-a-1.txt/XML/JSON保留，没有用分段结果拼接全量。20skip逐项记录；其中bash/ffmpeg
仅PATH缺失的5项通过隔离工具补测5passed（27.38s），不改写原全量统计。
20个自有CPU负载进程下连续三遍各143passed（97.00/100.87/98.81s），负载99.6–99.9%，均exit0。
15s预热+60s×3菜单前后实测：构造三轮均值0.6277→0.6328ms，销毁0.07942→0.07904ms，
旧子菜单180/180失效→0/180，遗留菜单均0；不宣称性能更快。最终Ruff通过，
架构/文档/公共回归门禁387passed（15.58s），包含本轮新增报告，均exit0。

**实机与风险**：Windows原生隔离设置/菜单30轮及100轮均正常退出；petcore源码菜单/托盘、
设置保存取消、双宠、岛折叠/隐藏停止采样与30轮资源检查通过，退出后Python线程仅MainThread。
旧设置100轮RSS约145→182MiB，但活QWidget为0、Qtwrapper恒423；tracemalloc追加60轮RSS下降，
既有构造位置有约342KiB Python留存。没有把“无活窗口增长”说成“已排除内存泄漏”。
其他平台/真实TTS/安装包/分发仍未验收，详见报告限制。此前质量报告仍保留当时失败记录。

**后续**：A功能退出门已过；B通用接口、C待办、D节日生日、E48签及候选分发尚未实现。
不得用本条为新功能或候选包验收背书；交接在`.scratch/petcore-p4c3/HANDOFF.md`，后续从B开始，不跳到发布。


## L058 — 2026-10-24 — P4-c3 B通用提醒与共享语音端口

- 实现按slot绑定展示、真实气泡/托盘交付、DND有界跨扩展单摘要，以及有界单语音通道、真实结束回调、按token/owner/slot取消和可撤销让位规则。
- 不将业务日程放Core；保留旧on_done/手动force语义。LoadedMedia门控修复独立进程实际复现的立即播放取消阻塞。
- 修改文件/增删与证据见[本批报告](PR-REPORT-PETCORE-P4C3-B-2026-10-24.md)；保留baseline-b原62文件差异。
- 最终重点68passed；整仓9729passed/20skipped/14warnings、pytest exit0；高负载三遍32passed。Windows隔离双宠/隐藏通知/汇总/设置/30轮/退出及15+60秒前后各三遍性能通过。scratch回显编码错误单列说明，不改写测试原始结果。
- 限制：无真实TTS、物理通知点击、其他Python/平台/双屏/候选包证据。后续C→D→E串行推进，无发布。


## L059 — 2026-10-24 — P4-c3 C待办调度与独立持久化

- 实现slot隔离、一次性/每日/提前/到期、10分钟宽限与高水位去重、无损旧清单迁移、非模态CRUD和设置取消不回滚触发状态。
- Core0.2.0/API1，todo1.1.0；逐文件增删、TDD与后续性能/实机见[C报告](PR-REPORT-PETCORE-P4C3-C-2026-10-24.md)。
- 模型11passed、功能18passed、关联95passed/13.05s/exit0；Ruff通过。累计全仓、高负载与Windows综合仍待C–E冻结，不沿用B结果。
- 下一批D历法/生日，随后E48签；无子代理/提交/发布。


## L060 — 2026-10-24 — P4-c3 D节日、节气与生日

- 本地tracked纯规则/文案移植，46节日/节气规则、slot版本化调度、默认关闭、独立生日MM-DD与让位规则；不import旧pet业务、不加动画开关。
- fun.festival1.1.0；逐文件增删/来源/风险与证据见[D报告](PR-REPORT-PETCORE-P4C3-D-2026-10-24.md)。
- d-red1collectionerror→60passed；关联126passed/14.54s0、Ruff通过。综合性能/Windows/全仓待最终C–E冻结；进入E48签及chime最小适配。


## L061 — 2026-10-24 — P4-c3 E：48签、语音联动与C–E联合验收

- 实现稳定48签/档位权重/无放回轮次、全宠同日共享与旧daily迁移；每宠设置与目标展示不全局化。
- 新增真实播放结束后的2.8秒揭签/6秒等待上限、完整签卡、首次互动提示、最多两条可取消预热；只改chime共享语音/让位与晚回调所需部分。
- TDD同时关闭同步语音拒绝导致todo回调未绑定token、失效目标让抽签任务卡住、quiet期间继续第二条预热、720px新增页裁切，以及动态历法依赖遗漏。无全局Qt补丁。
- 关联179passed；追加布局6passed、跨进程与配置回归15passed；Windows隔离源码30/120循环和真实PCM结束时序正常退出。最终整仓9837passed / 20skipped / 15warnings / 1565.27s / exit0，petcore/公共门1662passed，CPU高负载3×86passed、15+60秒前后各三轮性能完成；精确数字见[E报告](PR-REPORT-PETCORE-P4C3-E-2026-10-24.md)。
- 逐文件/增删见E报告，C/D原始切片表分别回填其报告。原48文件和各批快照保留，无提交/发布/真实账号/系统设置修改。
- 交付止于E；分发包/远程清单/下载加固/公开发布不在本轮，缺口不得标成完成。

- E收尾追加：真实FeatureManager卸载/新建runtime复现阻塞预热重复线程，单owner进程级准入修复；红1failed→45passed，随后重新原生/压力/性能/全部最终门禁，未复用修复前结果。


## L062 — 2026-10-24 — 分发阶段详细交接与本批提交交付

- 用户要求为较弱继任模型准备下一阶段计划，允许继任使用多智能体，并将A–E及本次交接提交到对应远程分支。本模型仍串行，不启动分发实现。
- 新增[详细计划](superpowers/plans/2026-10-24-petcore-p4c3-distribution.md)：T00–T09、公共接口、三组不重叠worker写集、真实源码缺口、TDD种子、安装恢复/资源预算/构建与发布边界。更新市场/路线图/文档索引并建立本地scratch任务卡。
- 本批为文档及一处测试文件末尾空行清理（AST不变）；逐文件增删、复验结果与提交号追踪见[交接报告](PR-REPORT-PETCORE-P4C3-HANDOFF-2026-10-24.md)。同一提交包含既有质量及A–E产品改动，其性能与Windows证据仍以各批原报告为准，不能扩大纯文档豁免。
- 远程核对：origin对应MerZlin/dsh-pet-indesktop，分支feat/petcore-vpet-architecture；fetch后HEAD与FETCH_HEAD无分叉。提交前复跑整仓9837passed / 20skipped / 14warnings / 1215.63s / exit0，高负载三遍各86passed（CPU99.9/100/100%），文档/公共架构300passed与Ruff通过；最终文档另复验。真实输出见报告；不强推、不合并、不上传Release。

- 提交前仅清理新历法测试末尾两行空行（AST一致、52passed）；用户要求取消重复全仓，已停止自有测试/等待进程。沿用本轮已完整运行的全仓/三遍负载，取消运行不计为通过。

- 主交付提交 `b77d4436ff623594eb4419ae9cbb2e1143657bd9`：130文件，+9423/−801，含原有质量修复、A–E及本批交接；本行与索引/报告为同批提交号回填。精确推送目标仍为origin对应仓库的 `feat/petcore-vpet-architecture`，不创建Release或合并main。

## L063 — 2026-10-24 — 动画画质评估与串行执行任务书

**授权与边界**：用户仅要求记录可交给较弱模型执行的计划；本批不实施 DPR 修复、
不新增产品测试/性能探针，不改源码、配置或素材，不提交、推送或发布。

**改动**：新增[评估与任务书](PETCORE-ANIMATION-QUALITY-ASSESSMENT-2026-10-24.md)，
固定本地 `7ab09683` 与对方 `9ce91ec8`；保留前轮会话的 105/106 素材同源、150% DPR、
Q70 单帧误差与缩放微基准，明确不是本批重测，也未核验对方发行二进制。
T00–T06 写明文件/函数、前置条件、命令、预期结果、停止条件与交接模板；未来任务全部未开始。
同步总索引、动画 backlog、素材流水线互链及日志索引；不改变现行生产编码档位或既有规范。

**逐文件增删**：`docs/PETCORE-ANIMATION-QUALITY-ASSESSMENT-2026-10-24.md`（新增）+381/−0；
`docs/ASSET-PIPELINE.md` +4/−0；`docs/INDEX.md` +3/−2；`docs/PETCORE-ANIMATION-BACKLOG.md` +4/−0；
`docs/PETCORE-LOG.md` 与 `docs/PETCORE-LOG-INDEX.md` 为本条目自身追加（+17/−0、+2/−0）。

**验证**：同批文档/架构门 `pytest -q tests/test_petcore_docs.py tests/test_pr_report_discipline.py
tests/test_architecture.py tests/test_petcore_architecture.py tests/test_ci_workflows.py` = **363 passed**；
`ruff check .` All checks passed。纯文档不改变运行行为，未跑全量 pytest、性能与 GUI；
整仓复跑见 L064。风险与下一步：任务书尚未执行，继任必须重新核对环境与基线，
不能以历史数字宣称修复完成；完整原始调查输出仅在会话中，后续须生成自己的文件证据。

## L064 — 2026-10-24 — 分发计划本机环境校正 + 整仓复跑

**授权与边界**：用户指出分发 T00–T09 计划里的环境事实来自另一台电脑，要求按本机适当优化，并重跑一次整仓；同时明确动画任务书（L063）保留但不实施。本批只改文档中的环境事实，不改分发实现、协议、版本、构建脚本或任何产品/测试代码；L063 的「逐文件增删 / 验证」两处占位按同批实际证据补齐（见 L063），动画任务本身仍未启动。

**改动**：
- `docs/superpowers/plans/2026-10-24-petcore-p4c3-distribution.md`：Global Constraints 换成实测本机事实——工作区 `E:\Work\dsh-pet-0`、Shell `pwsh` 7（禁 5.1）、`git` 走 PATH、`.venv` = Python 3.12.10 / PySide6 6.12.0 / pytest 9.1.1 / psutil 7.2.2；补 PyInstaller 6.22.3、`tools\InnoSetup\ISCC.exe`、rapidocr 3.10.0 已就绪，删除"venv 无 PyInstaller / rapidocr 不可导入"旧前提；§0.3 命令块路径与解释器探针同步；T00 依赖探针、T08 冻结包前提、Tech Stack、§0.1 的 A 行、§12 矩阵统一改成"本机 3.12.10 主跑、最低支持 3.11 不抬、3.11 专有语义另补跑"。
- 未改 `scripts/build_petcore.ps1`、`packaging/`、`petcore/`、`examples/`、`tests/`。

**为什么不能只改路径**：`rapidocr` 可导入后，整仓 skip 清单里「rapidocr not installed」不再成立；PyInstaller 已在 `.venv` 意味着 T08 不必新建独立构建 venv。这两条会直接改变 T00 基线口径与 T08 的退出门，因此必须写进计划而不是留在会话里。

**逐文件增删**：`docs/superpowers/plans/2026-10-24-petcore-p4c3-distribution.md` +14/−11；`docs/PETCORE-LOG.md` / `docs/PETCORE-LOG-INDEX.md` 为本条目自身追加。

**验证**：纯文档批次。产品/测试零改动，故性能实测与 Windows 实机行为无变化，不重复采集。同机门禁：`ruff check .` 全绿；`python -m petcore --check` exit 0；文档/架构/PR 纪律/CI 门 **363 passed**；`tests/test_petcore*.py` + 架构/PR 门 **1664 passed**（Python 3.12.10 / PySide6 6.12.0）。

**整仓复跑（本批文档落盘后）**：`PYTHONUTF8=1 QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest -q --tb=short --junitxml=.scratch/petcore-p4c3-rerun/full-rerun-20261024-162458.xml`，实际 **9841 passed / 18 skipped / 20 warnings / 1247.57s / exit 0**，XML 9859 例、failure/error 均 0。相对 E 报告（9837/20/15warnings）：收集 +2 例，skip −2 —— 那两条是 `test_agent_link`/`test_final_cleanup` 之外的「缺 bash」条件项，本机 Git Bash 可用后已实际执行并通过；本机 `rapidocr` 可导入，因此不再出现「rapidocr not installed」跳过。18 条 skip 全部来自旧 `pet/` 与平台/素材条件（符号链接权限 3、缺 ffmpeg 3、GIF 素材未生成 1、需真实前台窗口 2、素材未供给 1、POSIX 2、macOS 2、Windows 无 `tzset` 1、Cocoa 1、headless 截图 1、opt-in 声卡 1），petcore 域无 skip。原始输出与 JUnit 见 `.scratch/petcore-p4c3-rerun/`（gitignored）。

**未验证/风险**：计划里的发布链路、候选 ZIP、SAC 拦截与 3.11 子集仍未执行；本机 SAC 仍会拦未签名新构建。下一步按 T00 基线开始，先复核环境探针再派 worker。

## L065 — 2026-10-24 — P4-c3-D1/T01 分发契约冻结

**授权与边界**：用户批准 D1（T00–T07）。本批只冻结跨模块契约，不改默认市场源、不改 Core/API/mod 版本、
不改任何业务行为；T02/T03/T05 由 worker 并行、T04/T06/T07 随后。

**改动**：新增 `petcore/contracts/archive.py`（`ArchiveLimits` / `BudgetExceeded` / `MARKET_ARCHIVE_LIMITS` = 64MiB / 256MiB / 10000）、
`petcore/contracts/market.py`（`PackageOrigin` + `writes_trusted_marker()`）、`petcore/net/market_transport.py`
（`fetch_artifact` 签名接通既有下载，下载后复核压缩体积）；扩展 `petcore/contracts/http.py`
（`TransferSpec` / `TransferReport` / `ResumePolicy`，HTTP 默认只允许 https）；`petcore/market/install.py`
增加 `origin` 参数并在来源无授信资格时剔除包内 `.trusted`（防标记走私）。文档：MOD-FORMAT §18、MARKET D1/T01 节、SPEC 边界表。

**逐文件增删**：`petcore/contracts/archive.py` +54、`contracts/market.py` +28、`contracts/http.py` +52、
`net/market_transport.py` +66、`market/install.py` +18、`tests/test_petcore_distribution_contracts.py` +211、
`docs/PETCORE-MOD-FORMAT.md` +47、`docs/PETCORE-MARKET.md` +14、`docs/PETCORE-SPEC.md` +13（均为 +/−0，除文档外无删除）。

**验证**：契约测试 **11 passed / 1.73s**；市场相关 7 个测试文件 **63 passed / 11.09s**；`ruff check petcore tests` 全绿。
loopback 下载 4096 字节落盘且 `.part` 清理；2048 字节对 1024 上限抛 `BudgetExceeded(kind=compressed)` 且不留文件；
`market`/`local-zip` 装完无 `.trusted` 且消息含「已移除」，`bundled` 保留。

**性能**：无稳态路径变化（预算对象只在市场调用时构造，`origin` 分支仅一次 `exists()`）；T00 已采
`before-perf.json`，T01 标「新增路径无旧对应」，after 对照留 T09。

**未验证/风险**：`fetch_artifact` 的体积复核在下载之后，挡不住大文件先落盘（T02 移进读流）；
`open_pack`/`install` 的 `limits` 尚未落地（T03），**解包预算此刻不生效**；`origin` 只做标记剔除，
授权与事务仍归 T04。不得据此声称分发已可用。

## L066 — 2026-10-24 — P4-c3-D1/T02+T03+T05（三 worker 并行）

**授权与边界**：主模型串行完成 T00/T01 后，按批准计划派 3 个不重叠写集 worker 并行；主模型复核 diff、
补契约口径并集成。worker 未提交、未推送、未跑整仓、未改文档。本批不改默认源、不改版本、不改 UI。

**改动**：
- T02（`94d80650`）：`net/market.py` +378/−48、`net/market_transport.py` +25/−14、新增安全回归 +670、
  `contracts/http.py` +7/−2。206 按 `Content-Range` 起点对齐才追加、416 需本地长度与总长一致、压缩预算移入读流、
  跳转受跳数/协议/主机三重约束、所有路径关闭响应、取消保留 `.part`。
- 契约修正（主模型）：发现「同主机」会拒绝 GitHub Release 的 `github.com → objects.githubusercontent.com`，
  增加 `TransferSpec.allowed_hosts`（默认空 = 只同主机），并把 `resumed_from` 收窄为「仅区间对齐时非零」；MOD-FORMAT §18.2 同步。
- T03（`359446b5`）：`mods/install.py` +55/−10、新增 +245。`open_pack/install(limits=)`、解包前预检条目与声明展开、
  写入按实际字节计数（防声明造假）、失败清 staging、`limits=None` 行为不变。
- T05（`34a247cb`）：新增 `scripts/build_petcore_extensions.py` +549、`scripts/verify_petcore_extensions.py` +373、
  测试 +398、`.gitignore` +2。白名单 + 固定元数据 + 排序，两次构建 sha 相同；校验器独立重算并拒绝篡改与 `.trusted`。

**验证**：T02+T03+T05 与关联族 `46 passed / 18.04s`、`62 passed / 8.91s`；`ruff check petcore tests scripts` 全绿；
真实构建 4 包（19169 / 41454 / 9655 / 17522 字节，`excluded unknown=0`），`verify` **4/4** 独立复跑通过。
worker 红证据：旧实现 18 failed、缺 `limits` 关键字 8 failed、缺模块 exit 2。

**性能**：无新增线程/定时器/轮询；新增成本仅在市场调用路径（每 206 一次头解析、每 64KiB 一次计数、每次解包一次
`infolist()`、每次 3xx 一次比较）。T00 before 基线已在 `.scratch/.../before-perf.json`；after 对照按计划留 T09，
本批不声称更快。

**未验证/风险（关键）**：① `market/download.py` 仍走 `download_to(policy=None)`，**逐跳加固尚未进生产路径**，T04 必须切到
`fetch_artifact`；② `install_package` 的 `open_pack` 仍未传 limits，**市场解包预算尚未生效**；③ 多 URL 回退未实现；
④ 无真实 HTTPS/代理/CDN 实机；⑤ `install()` 仍以 `(False, msg)` 代替抛异常，T04 若要分类需在 `open_pack` 层拦。

## L067 — 2026-10-24 — P4-c3-D1/T04 身份·授权·事务

**边界**：只把市场安装接到 T02/T03 的加固上并补齐交叉校验与事务一致性；不改版本、不改默认源、不改 UI。

**改动**：
- 接线：`market/download.py` 的 `_fetch_one` 在 fetcher 暴露 `fetch` 时走 `HttpFetcher.fetch → fetch_artifact`，
  构造 `TransferSpec`（size / GitHub CDN 允许列表 / 超时）；只实现 `download` 的替身保持旧路径。
  `download_artifact` 与 `install_package` 新增 `limits`（默认 `MARKET_ARCHIVE_LIMITS`）并下传到 `open_pack`。
- 交叉校验：`verify.verify_manifest` 增加「能力在已知表内」「能力 ⊆ 索引声明」「api_version 一致」
  「manifest.core_requires 满足当前 Core」四项，错误点名不一致字段。
- 事务：锁写失败由「已安装但写锁失败」改为 `_rollback_failed_commit()`——有备份换回旧版，无备份删除新装目录，
  消息如实写「已回滚」/「回滚失败」；`BudgetExceeded` 被捕获并转成 `InstallOutcome(False, …)`。
- 契约：新增 `TransferSpec.allow_loopback_http`（默认真），回环判断放进 `net/market_transport.py`
  ——`market/` 不能 import `urllib`（架构门），这也顺带修掉了首版实现踩红的那条。

**逐文件增删**：`market/download.py` +60/−3、`net/market.py` +8/−0、`net/market_transport.py` +13/−1、
`contracts/http.py` +3/−0、`market/install.py` +32/−8、`market/verify.py` +35/−2、
`tests/test_petcore_market_install.py` +180/−0、`tests/test_petcore_market_transport_security.py` +17/−1、
`docs/PETCORE-MOD-FORMAT.md` +4/−4。

**验证**：市场族 58 passed / 13.78s；指定四文件 57 passed / 23.70s；全量 petcore + 公共门 **1738 passed / 123.00s**；
`ruff check petcore tests` 全绿。行为实测：本地 http 索引 + 真实 `HttpFetcher.fetch` 的 CLI `list→install→update→rollback`
通过；`allow_loopback_http=False` 时同一回环 URL 在连接前被拒且服务端 0 命中。

**性能**：无新增线程/定时器；安装路径多一次 `getattr` 探测与 `TransferSpec` 构造，下载多一次临时 staging
创建/删除与 `os.replace`；校验为集合差 + 一次版本比较。无旧同工作量可对照，after 留 T09。

**未验证/风险**：真实 HTTPS/CDN/代理下载未跑；设置页与 CLI 尚未暴露 `limits`；`install()` 对本地目录仍返回 `(False, msg)`
不抛异常；目录来源不套预算；锁失败后的 UI 重载留 T06/T07。

## L068 — 2026-10-24 — P4-c3-D1/T06 根索引·file: 链路·市场页销毁

**边界**：只加待发布根索引与离线读取链路、修设置页销毁时的线程生命周期；不改版本、不建 tag、不上传资产、不改 UI 版式。

**改动**：
- 新增根 `extensions-index.json`（137 行）：由 T05 `build_index(local=False)` 从真实候选清单生成，
  四包 `blocked=true`（理由「待发布：候选产物尚未公开」），每个版本带真实 size/sha256 与计划中的 Release URL。
- `market/model.py`：`normalize_index_url` 接受 `file://`；`market/source.py`：显式 `file:` 源**不回落**公网源。
- `net/market.py`：`fetch_index_file()`（`url2pathname` 在 net/ 内，守住架构红线）+ `HttpFetcher.fetch_index` 分支。
- `net/market_transport.py`：`file:` artifact 直接拷贝并按大小校验压缩预算，T07 本地验收由此可行。
- `ui/settings/page_market.py`：模块级 `_ORPHAN_JOBS` 持有运行中的索引线程到 `finished`；`refresh()` 在 `_disposed` 后短路。

**逐文件增删**：`extensions-index.json` +137、`market/model.py` +1/−1、`market/source.py` +3/−0、
`net/market.py` +22/−0、`net/market_transport.py` +29/−1、`ui/settings/page_market.py` +8/−1、
`tests/test_petcore_extension_index.py` +123、`tests/test_petcore_market_ui.py` +41。

**验证**：索引族 **6 passed / 0.67s**；索引+源+UI **22 passed / 1.15s**；UI 7 passed / 0.94s；`ruff check .` 全绿。
行为实测：`file:` 索引 CLI list 4 包 state=blocked exit 0；`install reminder.todo --allow-feature` exit 1、
消息含「待发布」且磁盘无 `mods/reminder.todo`；索引读取中被 dispose 后线程自行结束、迟到结果不写回、孤儿集合清空。

**性能**：`file:` 索引从「误走公网回落、约 42s」变为本地解析（测试集 0.67s）；artifact 为一次拷贝，无 socket；
销毁路径多一次 set 增删；无新增线程/定时器。

**未验证/风险**：索引四包仍 blocked，公开索引要等 Release；`RELEASE_REPO/TAG` 是拟定常量待 T09 确认；
阻塞式索引读取仍不可取消（8s 上限）；dispose 只保证不销毁运行中线程，不等同取消。

## L069 — 2026-10-24 — P4-c3-D1/T07 真实候选装卸·升级回滚·资源循环（D1 收口）

**边界**：验收批次。产品代码零改动；用 T05 真实 ZIP + `file:` 索引跑端到端与 60 次资源循环；不发布、不建 tag。

**改动**：新增 `tests/test_petcore_extension_candidates.py`（201 行，5 条）：本地索引可装、未授权被拒且零落盘、
四包 ZIP 安装后无 `.trusted` 且真实 `FeatureManager.load` 返回空 diagnostics、合成 `1.1.1` 的
`update → rollback → install` 三步与用户数据 SHA 不变、`doctor` 与 lock 一致。
scratch 探针 `.scratch/petcore-p4c3-distribution/t07/resource_loop.py` 记录 30× 装卸 + 30× 市场页开关。

**验证**：用例 **5 passed / 3.20s**（含一次四包构建）。资源循环 **2.54s**：RSS 70,651,904 → 81,752,064
（install 段 +1.63 MB 且收敛；page 段 +9.68 MB 但每 5 轮增量由 14.5 MB 递减到 1.2 MB），线程恒 4，
存活 QWidget 恒 0，staging/`.part` 恒 0。
探针自身曾误判「每页泄漏 69 个 QWidget」——补 `sendPostedEvents(DeferredDelete)` 后归零，属探针缺陷而非产品问题。

**未验证/风险**：全程 `file:`，无真实 HTTPS/CDN/断流；升级是改版本号的合成包，不是真旧版兼容矩阵；
`doctor` 不带 `--index-url` 仍回落默认源（既有语义）；原生设置窗手工点击、双屏/混合 DPI、非 Windows 未验。

**D1 状态**：T00–T07 全部完成。下一步 D2 = T08（Windows 冻结包 + 签名/SAC 决策）与 T09（性能 before/after、整仓门禁、发布准备）。

## L070 — 2026-10-24 — D2/T08 冻结候选包与开发证书签名

**授权**：用户选择「开发证书签名」并批准做 T08。本批不装系统、不发布、不改安全策略、不删既有构建产物。

**改动**：
- `scripts/build_petcore.ps1`：新增 `-OutputRoot` / `-PythonPath`；`--distpath/--workpath/--specpath`、`--check` 输出、
  exe 路径、Inno 源与输出全部跟随；拒绝仓库根与已含 setup 的目录；`-Clean` 仍只传 `--clean`。
- `packaging/petcore.iss`：新增 `MySourceDist` / `MyOutputDir` 覆盖变量（默认值保持旧行为）。
  **修复真实缺陷**：此前只重定向 Inno 输出，源目录仍指向根 `dist/`（本机有 832 MB 旧构建），
  候选包会打进旧代码；修后日志 3,816 行确认打包的是隔离构建。
- 新增 `tests/test_petcore_candidate_packaging.py`（6 条文本契约）；`docs/ONEDIR_PACKAGING.md` 补参数小节；
  `petcore/AGENTS.md` 补打包红线；新增 `docs/PETCORE-D1-ACCEPTANCE-2026-10-24.md`（D1 对外验收说明）。

**逐文件增删**：`scripts/build_petcore.ps1` +29/−8、`packaging/petcore.iss` +10/−2、
`tests/test_petcore_candidate_packaging.py` +63、`docs/ONEDIR_PACKAGING.md` +19、`petcore/AGENTS.md` +3、
`docs/PETCORE-D1-ACCEPTANCE-2026-10-24.md` +149、`docs/INDEX.md` +1。

**验证**：构建 exit 0；exe 15,079,368 B sha256 `dc40aa268ceb3f4c…`、setup 261,718,360 B `bc26e800044a2f14…`，
两者 `Get-AuthenticodeSignature` 均 **Valid**（`CN=DSH Pet Core Dev`）；Inno 编译 96.296 s，onedir 582 MB。
冻结包 `--check` **exit 0 / 715 ms / diagnostics [] / menu_template json**；冻结 `market` CLI 用 `file:` 索引
安装四个候选包全部 exit 0，`mods/` 四目录齐全；GUI 启动进程存活（96 MB）后按流程终止。打包契约测试 6 passed。

**未验证/风险**：安装向导只构建未安装（装机会写用户目录/注册表/自启，属 T09）；SAC 结论只对本次签名构建有效；
根 `dist/` 的 832 MB 旧构建未删；setup 内 `examples/petcore-mods` 勾选/不勾选的落盘差异未实测。

## L071 — 2026-10-24 — D2/T09 性能对照·整仓门禁·安装器验收·发布准备

**边界**：只做验收与发布文档；产品代码零改动；不建 tag、不传资产、不切默认源、不装到真实系统目录。

**改动**：新增 `docs/PETCORE-EXTENSIONS-RELEASE.md`（发布手册：产物摘要/三个先决确认/五步流程/逐步回滚/明确不做）
与 `docs/PR-REPORT-PETCORE-DISTRIBUTION-T09-2026-10-24.md`；`docs/INDEX.md`、`docs/PETCORE-ROADMAP.md`、
`docs/PETCORE-D1-ACCEPTANCE-2026-10-24.md` 补 T09 状态。探针（`after_perf.py`、`installer_acceptance.ps1`、
`task_matrix.ps1`、`stress.py`）只在 `.scratch/petcore-p4c3-distribution/t09/`。

**性能（before/after 同探针，3 轮 15s+60s）**：下载 32.739→32.485 ms、续传 33.141→33.214、解包 12.993→12.689、
本地安装 38.512→37.328、市场页 13.369→13.865；p95 下载 38.774→34.230、解包 15.060→13.630；CPU 中位 R1/R2 33.3%→33.3%，
R3 40.95%→33.3%，循环数 170–180→178–180。结论：无稳态回退，部分 p95 改善。

**验证**：整仓未过滤 `pytest -q` **9941 passed / 18 skipped / 20 warnings / 1220.48s / exit 0**（JUnit 9959，failure/error 0）；
安装器隔离矩阵：`/TASKS=` 空 → 0 包、`mod_todo` → 1 包 + `.trusted`、`mod_todo,mod_festival` → 恰好 2 包、
不传 `/TASKS`（两次）→ 0 包；四种用例卸载 exit 0、目录删除、HKCU 无残留。
高负载：7 个受影响文件在 20 个自有负载进程、每轮开机 CPU 100% 下三遍 **92 passed**（105.10 / 146.78 / 110.94 s，exit 0）。

**排查留痕**：最早一次手工调用装出 9 个带 `.trusted` 的包；改用 `.ps1` 固定参数后 5 次均为上表结果。
根因是 Git Bash/MSYS 把 `/TASKS=...` 改写成 `C:/Program Files/Git/TASKS=...`，参数未到 Inno——调用方式问题，非安装器缺陷。

**未验证/风险**：真实 HTTPS/CDN/断流未跑；安装器只验静默模式（向导点击、自启、覆盖升级未验）；
性能探针为 loopback 合成负载；整仓未在 20 负载进程下跑三遍；开发证书不能用于公开分发。

## L072 — 2026-10-25 — petcore 市场生命周期、授权与候选索引修复

**边界**：按当前审查计划关闭市场线程退出、断点续传、远程功能包预算、`file://` 授权、回滚语义和候选/发布索引混用六类阻断；不上传 Release，不改变默认公开源。

**改动**：应用级 `MarketJobRegistry` 持有索引/安装/回滚 `QThread`，页面销毁只取消并断开信号，退出阶段做 2 秒有界等待；下载把 `.part` 与元数据稳定存入市场缓存并保留可恢复状态；`effective_archive_limits()` 只给远程 `feature` 包套 64 MiB/256 MiB/10000 条目上限；`file://` 仅在显式本地索引、授权根目录和同根路径同时满足时可用，网络索引与重定向不能借机访问本机文件；回滚返回结构化 `RollbackResult`，恢复失败不再伪报成功；构建器默认生成 blocked 的 `candidate` 索引，只有显式 `published` 才解除 blocked。同步补充架构红线与故障回归测试。

**逐文件增删**：`git diff --cached --numstat` 实测：实现 `petcore/contracts/archive.py` +41/−0、`contracts/http.py` +5/−0、`market/cli.py` +11/−2、`market/download.py` +90/−6、`market/install.py` +160/−38、`market/source.py` +30/−5、`net/market.py` +37/−6、`net/market_transport.py` +103/−27、`ui/app.py` +8/−0、`ui/market_jobs.py` +82/−0、`ui/settings/page_market.py` +82/−18、`scripts/build_petcore_extensions.py` +25/−6；测试 `tests/test_petcore_extension_build.py` +6/−0、`tests/test_petcore_market_hardening.py` +311/−0、`tests/test_petcore_market_install.py` +17/−4。

**验证**：市场传输/安装/UI/远程预算/索引/构建/本批 hardening 联合 **90 passed / 19.56s**；其中新增市场阻断族 **51 passed / 8.46s**；后续完整门禁仍待本批全部代码与文档稳定后重跑。

**限制**：本批没有真实 HTTPS/CDN、真实 Release、冻结包或外部代理验收；网络、PySide6 与文件源的确定性回归不等同公开分发已完成。`_ORPHAN_JOBS` 仅保留为旧探针兼容符号，实际任务已由应用级 registry 持有。

## L073 — 2026-10-25 — WorkerHost 与 petcore DPR 渲染边界收口

**边界**：在市场阻断关闭后，补齐本轮计划中仍有明确证据缺口的两个运行时边界：真实 worker 会话握手/心跳/取消/代次和帧渲染物理像素/DPR；不扩展主分支不存在的动画语义，不删除旧 `pet` 兼容实现。

**改动**：`WorkerSession` 增加 READY/退出事件、显式握手等待、心跳超时收口和有界退出；`WorkerHost` 为同一 owner 维护代次，替换旧 worker 后拒收迟到消息、错误和退出回调。`petcore.ui.renderer.frame_pixmap()` 按窗口所在屏的 DPR 将逻辑尺寸换算为物理像素、写回 pixmap DPR，并保留镜像行为；`PetWindow` 帧入口传入 `screen_dpr(self)`。新增子进程 worker 及 offscreen Qt DPR 回归测试。

**逐文件增删**：`petcore/platform/worker.py` +79/−23、`petcore/ui/pet_window.py` +1/−0、`petcore/ui/renderer.py` +19/−7、`tests/test_petcore_renderer.py` +43/−0、`tests/test_petcore_worker.py` +90/−109。

**验证**：worker + renderer 聚焦族 **5 passed / 4.20s**；与市场 hardening 联合运行 **56 passed / 11.83s**；`ruff` 通过。真实 worker 使用当前 Python 子进程和 JSON 管道，Qt 测试使用 `QT_QPA_PLATFORM=offscreen`；未将旧 `pet` 的历史 DPR 测试冒充本次 petcore 实机验证。

**限制**：本机没有双屏/混合 DPI 实机条件，尚未验证真实窗口跨屏、Windows GPU/解码性能和冻结包运行；这些保留在最终报告的验证缺口中。

## L074 — 2026-10-25 — 重构收尾状态与最终交付证据

**边界**：记录本轮两个可独立回滚提交的实际收口范围，不把市场/Worker/DPR 的自动化测试误报为完整主分支 parity、双屏实机、候选安装器或公开 Release 验收。

**改动**：补充 `PETCORE-EXTENSIONS-RELEASE.md` 的 candidate/published 显式状态转换；更新 `PETCORE-MAIN-PARITY-BACKLOG.md`、`PETCORE-ROADMAP.md`、`PETCORE-SPEC.md` 的当前状态快照；新增 `PR-REPORT-PETCORE-REFACTOR-FINAL-2026-10-25.md`，并登记 `docs/INDEX.md` 与 `.scratch/petcore-refactor-final/HANDOFF.md`。产品代码对应提交为 `9e973b53` 与 `e371f92c`。

**证据**：市场聚焦族 90 passed / 19.56s；Worker + renderer 5 passed / 4.20s；联合市场/Worker/renderer/架构族 56 passed / 11.83s；Ruff `All checks passed!`；架构/文档门禁 316 passed / 4.60s；CPU 高负载三遍每次 12 passed（4.98s/4.97s/4.95s）；整仓 9951 passed / 20 skipped / 14 warnings / 1289.12s / exit 0；隔离根 `python -m petcore --check` 返回 0。

**交付**：`9e973b53`、`e371f92c`、`5c2ba786` 已按独立提交推送至 `origin/feat/petcore-vpet-architecture`，推送输出为 `6326e31c..5c2ba786`，返回码 0；未上传 Release、未推送 tag、未合并 main。

**限制**：PyInstaller、双屏/混合 DPI、macOS/Linux、真实外部 TTS、真实 HTTPS/CDN 和公开 Release 未在本机完成；默认 C 盘旧数据迁移的 `os.replace` 探针返回 WinError 17，已记录为环境/迁移缺口；不沿用历史报告冒充本轮通过。

## L075 — 2026-10-25 — 运行时迁移诊断与 Worker 生命周期收口

**边界**：只改 petcore/Core 与 reminder.todo 的已存在运行时接缝；补齐迁移结果诊断、schema 识别、原子写入失败留痕和 Worker 子进程的有界退出/非法消息拒收。不新增业务 worker，不改变旧 `pet` 实现。

**改动**：`petcore/config.py` 增加 `MigrationRecord`、状态集合、备份优先迁移和失败阶段诊断；`petcore/chat/session_store.py` 增加 schema/version 校验与诊断；`examples/petcore-mods/reminder.todo/host/store.py` 保留旧待办源并区分迁移/备份/写入/校验失败；`petcore/platform/worker.py` 增加 shutdown 锁、退出码、读管道线程有界收口和未知消息拒收；新增 `tests/test_petcore_refactor_final.py` 的迁移与 Worker 回归。

**逐文件增删**：`examples/petcore-mods/reminder.todo/host/store.py` +57/−13、`petcore/chat/session_store.py` +26/−4、`petcore/config.py` +169/−22、`petcore/platform/worker.py` +66/−21、`tests/test_petcore_refactor_final.py` +156/−0（新文件）。

**验证**：Ruff 全目录通过；编译通过；新增回归 6 passed / 1.00s；本轮受影响 petcore 联合族 136 passed / 23.24s；整仓本次门禁 9957 passed、20 skipped、15 warnings、3048.17s、exit 0。高负载三遍联合族均 136 passed，pytest 分别 29.44s、24.65s、27.28s；驱动脚本总耗时分别 31.15s、38.63s、28.45s。

**限制**：默认用户数据根的旧迁移仍需独立处理 `WinError 17` 环境探针；隔离数据根 `python -m petcore --check` 可通过。没有把旧 `pet` 测试结果当作 petcore 迁移验收。

## L076 — 2026-10-25 — 角色导入、媒体边界与点击生命周期收口

**边界**：补齐已有角色目录/ZIP 导入、媒体点击按下/释放 token、彩蛋头像/提示配置和关闭时音频取消；不引入新媒体格式、不删除旧 `pet` 兼容层、不新增 VPet LPS/PNG。

**改动**：`petcore/content/importer.py` 使用临时目录、有界 ZIP 解压、路径/符号链接拒绝、单根目录规范化和 no-force 原子安装；`petcore/media/click_sound.py` 增加 `ClickSoundPair` token 状态；`petcore/ui/pet_window.py` 将点击/拖拽/探头/关闭接入 token 生命周期；`petcore/ui/easter_egg.py` 使用配置头像并回退首图；新增回归覆盖 ZIP 路径穿越、重复导入和点击 token 幂等。

**逐文件增删**：`petcore/content/importer.py` +71/−9、`petcore/media/click_sound.py` +46/−0、`petcore/ui/easter_egg.py` +25/−3、`petcore/ui/pet_window.py` +36/−7。

**验证**：角色、彩蛋、点击音效、设置和新增 refactor 测试均纳入联合 petcore 族；联合结果 136 passed / 23.24s；Ruff 与 compileall 通过。ZIP 导入在真实本机文件系统执行，重复 ID 不覆盖原角色，恶意 `../` 条目不落盘。

**限制**：没有真实双屏/混合 DPI、冻结候选包或外部媒体服务条件；本批不声称媒体跨屏实机通过。

## L077 — 2026-10-25 — 已有 parity 设置、余额入口与 GraphType 菜单收口

**边界**：只补已有 Core 账户、设置和动画入口，不新增 provider、不扩展 Agent 写端、不改变默认开关和设置作用域。

**改动**：`petcore/ui/settings/pages_core.py` 增加 ZIP 导入入口；`page_interaction.py` 增加 `easter_egg_hint`/`easter_egg_avatar` 的实时预览、保存、取消和搜索；`page_api.py` 增加异步余额查询按钮、稳定错误反馈和 disposed 后迟到结果抑制；`menu_actions.py` 使用同一 `GraphType` 枚举增加摸头、说话、睡觉、启动、工作、悬空/抛掷的可选懒加载分组。

**逐文件增删**：`petcore/ui/menu_actions.py` +11/−0、`petcore/ui/settings/page_api.py` +73/−1、`petcore/ui/settings/page_interaction.py` +64/−0、`petcore/ui/settings/pages_core.py` +28/−8。

**验证**：API/账户/设置/覆盖 petcore 族 95 passed / 52.08s；与运行时、媒体和角色回归合计 136 passed / 23.24s；架构、文档和设置覆盖门禁 318 passed / 4.46s；Ruff 通过。余额失败只显示分类原因，不将失败伪装成 0；真实外部服务未调用。

**限制**：GraphType 分组按角色资源懒加载，缺资源时保持隐藏并回退已有动作；未把“所有角色都具备全部动画资源”写成通过。候选包、双屏、多平台和公开发布仍未验收。

## L078 — 2026-10-25 — 重构收尾文档、petcore-only 最终门禁与交接

**边界**：按用户最新要求，最终验证不再重复旧 `pet` 实现整仓门禁，只运行 `petcore` 测试、Ruff 和 petcore 专项文档/架构/设置覆盖/交付纪律门禁；不切换默认入口、不构建候选包、不上传 Release。

**改动**：更新 `PR-REPORT-PETCORE-REFACTOR-FINAL-2026-10-25.md`、`PETCORE-MAIN-PARITY-BACKLOG.md`、`PETCORE-ROADMAP.md`、`PETCORE-SPEC.md`、`docs/INDEX.md`，补齐实现/自动化/Windows 源码/待验证状态；新增 `.scratch/petcore-refactor-final/HANDOFF.md` 精确记录提交、命令、缺口和 SSH over 443 推送断点。

**逐文件增删（相对代码提交 `42856889` 的当前工作区实测）**：`docs/INDEX.md` +1/−1、`docs/PETCORE-LOG-INDEX.md` +1/−0、`docs/PETCORE-LOG.md` +11/−0、`docs/PETCORE-MAIN-PARITY-BACKLOG.md` +18/−0、`docs/PETCORE-ROADMAP.md` +13/−0、`docs/PETCORE-SPEC.md` +11/−0、`docs/PR-REPORT-PETCORE-REFACTOR-FINAL-2026-10-25.md` +103/−108；最终提交前再次核对 `git diff --numstat`。

**验证**：`tests/test_petcore_*.py` 由 PowerShell 显式展开后 **1479 passed in 132.94s (0:02:12)**；`tests/test_petcore_architecture.py tests/test_petcore_docs.py tests/test_petcore_settings_coverage.py tests/test_pr_report_discipline.py` **302 passed in 4.03s**；`.venv\Scripts\python.exe -m ruff check petcore tests scripts` → `All checks passed!`；`git diff --check` 在清理报告 Markdown 尾随空格后通过。此前已完成的一次整仓 **9957 passed / 20 skipped / 15 warnings / 3048.17s / exit 0** 仅保留为历史证据，不再重复。

**限制与下一断点**：本机探针为单屏/DPR 1.0，PyInstaller 未安装；双屏混合 DPI、候选包、macOS/Linux、真实 TTS/CDN/余额服务和公开 Release 仍未验收。下一步只做最终文档提交、SSH over 443 `fetch` + `merge --ff-only`，若远端无新提交则推送 `origin/feat/petcore-vpet-architecture`。

## L079 — 2026-10-25 — 候选包构建交接与弱模型执行说明

**边界**：将 PyInstaller/Inno Setup、Windows 候选包构建、隔离数据目录验收和证据留存交给另一台电脑执行；本机不安装打包工具、不构建冻结包、不上传 Release。后续测试范围继续按用户要求限定为 `petcore` 与必要的 petcore 专项门禁，不重复旧 `pet` 实现整仓测试。

**改动**：新增 `docs/PETCORE-CANDIDATE-PACKAGING-TASK-2026-10-25.md`（详细任务说明）和 `docs/PETCORE-CANDIDATE-PACKAGING-HANDOFF-2026-10-25.md`（可推送交接文档），按环境探针、独立 venv、petcore-only 门禁、onedir、候选 `--check`、Inno 安装器、实机冒烟、SHA/文件清单、停止条件和最终回传格式编排；另在 `.scratch/petcore-packaging-candidate/HANDOFF.md` 保留本地交接副本；更新 `docs/INDEX.md` 登记两份文档。

**逐文件增删**：本次提交前以 `git diff --numstat` 为准；产品代码、构建脚本和依赖声明均未修改。构建二进制、个人配置和探针输出只允许留在被忽略的 `.scratch` 路径。

**验证**：本次仅做文档/交接改动；提交前执行 `git diff --check`、`tests/test_petcore_docs.py`、`tests/test_petcore_architecture.py`、`tests/test_petcore_settings_coverage.py`、`tests/test_pr_report_discipline.py` 及 Ruff。既有 petcore 终态证据为 1479 passed、专项门禁 302 passed、Ruff 通过；PyInstaller、候选包、安装器、双屏和跨平台验证由继任电脑按任务说明实际执行。

**下一断点**：继任模型完成或阻断候选包构建后，回填真实工具版本、退出码、SHA、安装/卸载结果和验证缺口；候选验收不等于公开 Release。

## L080 — 2026-10-25 — petcore 摸头互动

**边界**：新增 petcore 桌宠的鼠标摸头互动，不修改旧 `pet` 实现，不新增动画引擎、线程、定时器、网络请求或菜单入口。仅在窗口可见、可交互且动作空闲时，使用角色 `head_box`（缺失时回退 `body_box`）检测头部内连续往返移动。

**改动**：新增纯逻辑 `HeadPatTracker`，按 4 px 单段、14 px 累计、反向向量点积 `<= -0.25`、1.2 秒轨迹窗口和 0.65 秒冷却判定；新增默认点击、自定义名称、全部合格单次动画三种选择及连续不重复选择。`PetWindow` 接入鼠标跟踪、逻辑坐标还原、透明像素过滤、忙碌/拖拽/投掷/click-through/隐藏/关闭/换角清理；`InteractionPage` 增加启用开关、模式、多选动画列表、全选/恢复点击/清空操作，配置按宠物 slot 保存并遵守 Save/Cancel/Esc 契约。

**逐文件增删**：`petcore/engine/head_pat.py` +167/−0（新文件）；`petcore/config.py` +31/−0；`petcore/ui/pet_window.py` +103/−1；`petcore/ui/settings/page_interaction.py` +202/−0；`tests/test_petcore_head_pat.py` +137/−0（新文件）；`tests/test_petcore_interaction.py` +71/−1；`tests/test_petcore_settings.py` +43/−1。以上为本轮代码/测试变更的 `git diff --numstat` 加新文件实际行数；最终文档与报告增删以提交前 `git diff --numstat` 为准。

**验证**：摸头/交互聚焦族 **24 passed / 1.10s**；设置聚焦族 **34 passed / 6.37s**；petcore 全部 `test_petcore_*.py` **1490 passed / 130.18s / exit 0**；`ruff check petcore tests scripts` 输出 **All checks passed!**；`python -m petcore --check` 返回 **0**。100,000 次纯逻辑更新耗时 **0.042175s**（约 **2,371,062 events/s，0.422 µs/event**）；设置开关/模式循环 30 次耗时 **0.235223s**（约 **7.841 ms/cycle**）。

**性能与资源**：实现只在已有 `mouseMoveEvent` 中执行轻量计算；不新增后台线程、Qt 定时器、网络或磁盘写入。设置列表只在页面刷新/角色变化时重建，不为每次鼠标移动分配持久任务。未进行旧 `pet` 测试。

**实机与限制**：本机本轮只执行 offscreen Qt 和 Core 检查，未执行真实 Windows 桌面鼠标摸头、click-through、拖拽/投掷和双 slot 可视化验证；因此“Windows 实机通过”仍为未验证，不能用自动化结果替代。动画资源缺失时由现有 catalog 结果决定不触发并节流记录诊断。

**提交**：实现与测试提交为 `2a5f80e9`；候选构建、安装器、Release 和 CDN 交由另一台电脑继续。

## L081 — 2026-10-25 — P5 灵动岛、余额、UI、性能与 Core 更新收口

**范围与设计**：本批只修改 `petcore`、`tests/test_petcore_*.py` 和必要文档，不运行旧 `pet` 整仓测试。实现 Dynamic Island P5-a2（停靠/展开/消息/余额/第三方段/折叠停采样/避让）、按 slot 的 `BalancePresenter` 与左键/右键/灵动岛共用余额入口、菜单/设置图标与 QComboBox 字体度量安全边距、1 Hz 采样与聚合性能计数、HTTPS 更新 manifest/ETag/镜像/Range/断点/校验，以及仅 frozen Windows 启动独立安装器的 Core 自更新边界。传输失败保留可复用 `.part` 状态，协议/大小/摘要失败清除断点；源码运行和非 Windows 不原地替换。

**逐文件增删口径**：实现文件包括 `petcore/account/__init__.py`、新增 `account/presenter.py`、新增 `performance.py`、新增 `update/{__init__,model,service}.py`、`cli.py`、`config.py`、`island/segments.py`、`net/http.py`、`platform/{netmon,sysmon}.py`、`ui/{app,island,menu_actions,menu_model,menu_style,pet_window}.py`、`ui/settings/{coverage,dialog,page_about,page_api,style}.py`；测试包括新增 `test_petcore_{balance,island_p5,menu_icons,performance,update}.py` 及 CLI/岛/设置相关扩展；文档同步 `PETCORE-ROADMAP.md`、`PETCORE-SPEC.md`、`PETCORE-MAIN-PARITY-BACKLOG.md`、`ONLINE-UPDATE.md`、`ONEDIR_PACKAGING.md`、`STABLE_BUILDS.md`、`INDEX.md`、本报告和 `.scratch/petcore-p5-complete/HANDOFF.md`。完整 `git diff --numstat` 见 `docs/PR-REPORT-PETCORE-P5-COMPLETE-2026-10-25.md`。

**验证**：`ruff check petcore tests scripts` 输出 `All checks passed!`；P5 聚焦族 `111 passed`，CPU 压力进程下连续三遍分别 `8.22s/7.29s/6.73s`；全部 `tests/test_petcore_*.py` 为 `1511 passed in 124.28s (0:02:04)`、exit 0；`python -m petcore --check` exit 0，报告 `frozen=false/can_auto_install=false` 且 HTTPS trusted host 检查通过。性能探针按 15 秒预热、60 秒采集、active/hidden/off 各 3 次执行，原始 JSON 为 `.scratch/petcore-p5-complete/performance-2026-10-25.json`。

**状态与限制**：本机只做 Windows 进程级 `--check` 和 offscreen Qt；PySide6 6.11.1、Python 3.11.1、psutil 7.2.2 可用，项目 venv 无 PyInstaller 模块，未启动真实安装器。GPU/FPS 无原生探针时显示具体 unavailable 原因，不伪造 0。真实桌面菜单/高 DPI/双屏、冻结候选包、真实余额服务、macOS/Linux、CDN/Release 仍是环境缺口；本轮不上传、不覆盖安装、不切换默认入口。


## L082 — 2026-10-25 — P5 更新取消与可选探针收尾

**改动**：在 P5 初始实现基础上收口 `petcore/ui/settings/page_about.py` 的下载中状态和取消按钮、`petcore/ui/app.py` 的取消结果与 generation 拒收；`petcore/ui/island.py` 增加 `set_probe_snapshot()` / `clear_probe_snapshot()` 作为可选 GPU/FPS 平台探针接缝；`petcore/config.py` 与 `petcore/platform/sysmon.py` 修正不可用能力口径；新增取消和真实 probe snapshot 回归。当前工作区相对基线的相关增删为：`petcore/ui/island.py` +258/−10、`petcore/config.py` +5/−1、`petcore/ui/settings/page_about.py` +68/−0、`petcore/ui/app.py` +213/−2、`petcore/platform/sysmon.py` +4/−2、`tests/test_petcore_island_p5.py` +97/−0（新增）、`tests/test_petcore_about_page.py` +15/−0。

**行为**：更新下载现在先进入 `downloading` 状态，设置页可取消；取消会使本代回调失效，不会在取消后更新 UI、启动安装或写入完成状态。GPU/FPS 有真实平台快照时显示注入的值和单位，无探针时继续显示明确 unavailable 原因，不创建第二采样线程、不伪造 0。

**验证**：`ruff check petcore tests scripts` 输出 `All checks passed!`；P5 聚焦族 `121 passed in 13.08s`，CPU 压力下 3 次分别 `13.08s / 13.29s / 12.96s`；`tests/test_petcore_*.py` 全部 `1513 passed in 126.19s (0:02:06)`、exit 0；`python -m petcore --check` exit 0；`git diff --check` 无空白错误（仅报告 LF/CRLF 转换提示）。

**限制**：本机无真实桌面、冻结安装器或原生 GPU/FPS 探针；未使用真实余额服务、CDN/Release、真实 API key，也未修改旧 `pet`。


## L083 — 2026-10-26 — P5 提交前复验与远端交付

**范围**：提交已有 P5 工作，不扩大功能范围。实现提交 `ce8c67fb` 共 43 文件、+2351/−28；未修改旧 `pet/`，未纳入 `.scratch`、配置、凭据或构建产物。暂存检查清理报告两处 Markdown 行尾空白及岛测试末尾空行，不改运行行为；校正报告两项文件行数与测试命令排版。

**验证**：本次重新执行 `.venv\Scripts\python.exe -m ruff check petcore tests scripts`，`All checks passed!`；枚举全部 `tests/test_petcore_*.py` 后执行 pytest，`1513 passed in 122.25s (0:02:02)`、exit 0；架构/文档/设置覆盖/PR 报告门禁 `306 passed in 3.88s`；`git diff --cached --check` 通过。未运行旧实现测试。上一批高负载三遍及性能实测保留原始日期和数据，本次未重复测量；仅文档回填不改变这些路径。

**交付**：先经 SSH 443 fetch 核对远端无新增提交，再普通 push 到 `origin/feat/petcore-vpet-architecture`；`ls-remote` 实际返回 `ce8c67fb1e626686c8e5553bcb5957289f798b55`。本条与索引/报告为独立文档回填，不 force push、不合并 main、不上传 Release。可使用 `git revert ce8c67fb` 回滚实现，不删除新格式缓存或用户配置。

**回填文件与增删**：`docs/PETCORE-LOG-INDEX.md` +3/−2；`docs/PETCORE-LOG.md` +11/−0；`docs/PR-REPORT-PETCORE-P5-COMPLETE-2026-10-25.md` +8/−0。行为未变化，文档交付免新增性能/实机测量；Windows 可见 UI、GPU/FPS 真读数、冻结安装器和真实服务验证缺口继续保留，不以推送成功替代验收。


## L084 — 2026-10-26 — P5 硬伤修复（投递/死锁/入口/口径）

**背景**：复核 P5 实现时发现「报告说做完、实际没接线」。三条是功能实质失效，四条是口径错误；本条只修这些，不扩功能。

**逐项**：

1. `petcore/ui/app.py` 新增 `gui_dispatch(scheduler)`，余额投递改走 `QtScheduler`。原 `QTimer.singleShot(0, callback)` 是无接收者的两参重载，从工作线程调用时定时器挂在没有事件循环的线程上，回调永不触发——实测 `two-arg from worker thread -> []`。后果是首次点「显示余额」毫无反应、之后每次显示上一次的缓存、失败永不提示。
2. `petcore/account/presenter.py` 的 `cancel()` 改为锁内摘 future、锁外 `future.cancel()`。`Future.cancel()` 对 PENDING 任务会在调用线程同步执行 done_callback，进 `_done` 再抢同一把非重入锁 → 永久阻塞（实测 25 秒超时、线程仍存活）。这条路径正是 `quit_app` 退出时会走的。
3. 菜单：`show_balance` / `check_update` 早已注册但不在 `petcore/menu_templates/petcore-default-v1.json`，而自动追加只收含 `:` 的 id，故永远不会渲染。现在分别落在「桌宠控制 → 动态岛之后」与「工具与帮助 → API 与账户之后」，`BUILTIN_DEFAULT_LAYOUT` 同步。
4. 岛避让：障碍物从 `frameGeometry()` 改为桌宠 `body_rect()`（整窗四周是大片透明画布，按整窗判重叠会被远处走位反复推开）；自动挪动**不再 `_save_position()`**，并记住原位、连续 3 拍无遮挡后回原位；用户拖动后作废该记录。
5. 更新：新增 `reset_cancel()` 只在提交任务时清理；`check()` 在每个源之前看取消；`download()` 不再自己 `clear()`（排队期间的取消会被开场吞掉）；`_finalize_download()` 写 `<asset>.verify.json` 侧车，`install()` 每次重算 size+sha256（支持重启后安装，挡住下载后被替换的包）；`install_update()` 与关于页按钮都按 `can_auto_install()` 门禁；`_publish_update_result()` 不再重复 `island.add_message()`（`_notify` 已写），状态码经 `describe_state()` 翻中文。
6. `--check` 增加 `performance` 段：计数器原来只写不读，`PerformanceSnapshot.as_dict()` 是其读出口；`_tick()` 里两个分支都 `inc("coalesced_updates")` 导致该计数恒等于 `ui_updates`，改为只在「宽度没变」的拍计数。
7. `petcore/ui/settings/page_api.py` 新增「余额服务」下拉（`api_service_id` 此前只读不写，永远退化成「第一个带余额协议的服务」），并显示当前生效的服务或「自动」的落点。
8. 岛段语义对齐主分支：`set_probe_snapshot()` 收到 `unsupported` 时记原因并**不占位**（原来 pop 后会被渲染成 `--` + `ALERT_CRIT` 的错误段），卡片新增「该指标不可用：<原因>」行；`clear_probe_snapshot()` 同步清原因。

**验证**：`ruff check petcore tests scripts` → `All checks passed!`；`tests/test_petcore_*.py` → **1522 passed / 127.96s / exit 0**（较上批 1513 +9，全是本批新增回归）。新增回归：取消死锁（daemon executor，失败只报断言不挂进程）、工作线程投递真落到 GUI 线程、避让不落盘且回原位、`unsupported` 不占位、两个新动作必须在模板里、取消不被吞、换包被摘要拦下、无侧车拒装。

**未验证**：真实桌面下的岛避让手感与菜单渲染只有 offscreen 覆盖；本批未重建安装包。


## L085 — 2026-10-26 — 旧数据全量迁移（七域 + CLI + 诊断）

**范围**：新增 `petcore/migration/`（框架 + 配置/slot/活跃集合/会话/待办/API/凭据七域），`petcore/cli.py` 增加 `migrate` 子命令与 `--check` 的 `migration` 段。

**设计**：幂等只认 `<root>/migration.json` 的 `completed`，**不认**「目标文件不存在」——`Config.load()` 原来只在目标缺失时迁，第一次启动后永久失效。`MigrationContext` 统一负责备份（`migration-backup/<kind>-<旧根>-<原名>`）、原子写、失败记账、回滚映射（目标原本不存在时 backup 记空串，回滚即删除）。配置域**只补缺不覆盖**，并直接写合并后的原始字典——若走 `Config.save()`，`DEFAULTS` 会把每个键填满，后续域再也补不进去（自查发现并修正）。`slot0` 是主宠，键并入全局 `config.json`；`config-slot-N`/`config-pet-N`（N≥1）写 `config-pet-N.json`。会话域把 ISO 时间转 epoch float（新 schema 只认 float）、丢弃 `provider_id/system_prompt/pinned` 并计数；待办域把 `fired_*_slot` 换算成 `marks` 时间戳，写出前用扩展自己的 `validate()` 校验两遍，写后复读再校验；API 域把 `chat.providers` 转成 `ApiService`（`api.deepseek.com` 才推 `deepseek` 余额协议），密钥一律丢弃；凭据域只做钥匙串到钥匙串（`provider/<id>`→`core.api:<id>`、`websearch/api`→`core.websearch:<engine>`、`tts/mimo`→`speech.mimo:api_key`），值不进报告与日志，keyring 不可用整域记 `unsupported`。

**命令**：`petcore migrate [--dry-run] [--force] [--rollback] [--legacy-root P]`，输出 JSON 报告，有 `failed_*`/`unsupported` 时退出码 1；`--check` 只读标记与备份目录，**不跑迁移**。

**逐文件增删**：新增 `petcore/migration/__init__.py` 379、`config_data.py` 238、`sessions.py` 236、`todos.py` 386、`accounts.py` 513；修改 `petcore/cli.py` +52/−0；新增 `tests/test_petcore_migration.py` 206、`_sessions.py` 249、`_todos.py` 373、`_accounts.py` 311。

**验证**：迁移族 `41 passed in 1.26s`（四文件全绿）；整合测试 `10 passed`（含七域整链：配置+slot+活跃集合+会话+待办都搬、凭据域在无旧凭据时不写钥匙串、二次运行全 `already`）；`ruff` 通过；`--check` 实跑输出 `migration: {marker: false, completed: {}, backups: ["dsh-pet-standalone-config.json"]}`、`diagnostics: []`。

**未验证 / 风险**：本机没有真实 `todo_items.json` 样本（三个旧根都没有），待办迁移只有构造数据覆盖；`overlay-active-pets.json` 同样无真实样本。旧 `config.json` 的 99 个未映射键（`chat_*`、`weather_*`、`voice_chime_*`、`festival_reminder_*`、`dialogue_*`、`music_*` 等）**仍未进别名表**，本轮只完成会话/待办/API/凭据与 slot0，键级扩表留作后续（属"只补缺"的同一机制，补表即生效）。发布入口未切换。


## L086 — 2026-10-26 — GPU(PDH)/FPS(PresentMon) 真实探针与接线

**范围**：新增 `petcore/platform/gpu.py`（1098）、`gpu_monitor.py`（112）、`fps.py`（1962）、`fps_permission.py`（513）、`petcore/ui/probes.py`（217）；`petcore/ui/app.py` 接探针 hub；`pages_core.py` 放开 GPU/帧率两段并加「游戏帧率权限」行；`scripts/build_petcore.ps1` 打包 PresentMon 并过 `verify_presentmon.py`；`petcore/performance.py` 增 `gpu_samples`/`fps_samples`。

**口径**：GPU 走 ctypes PDH（`\GPU Engine(*)\Utilization Percentage`）+ DXGI 名字，聚合与主分支一致（引擎级跨进程求和 → 适配器 max → 跨适配器 max，报最忙引擎）；query 属于创建线程，跨线程 `read` 拒绝（owner 已退出时由新线程接管并重建基线，否则「停采→恢复」永久失效）；首拍/长停只建基线（`sampling`）。FPS 走随包 `PresentMon-2.6.0-x64.exe` 子进程，1 秒**事件时间**窗按 `(pid, swapchain)` 分窗取最忙活链，命令行禁 `--stop_existing_session/--restart_as_admin/--v1_metrics/--v2_metrics`，停止用官方 `--terminate_existing_session`，并用 Job `KILL_ON_JOB_CLOSE` 防孤儿。两段都只有 `ok` 带读数，其余 `None` + 具体原因，**不伪造 0**。岛只在「可见 + 段启用 + 未被折进 `+N`」时报需求，宿主据此 start/stop；空集同时清段快照。

**本机实测**：GPU 三个适配器（AMD Radeon 780M ×2 + Microsoft Basic Render Driver），冷启首拍 283.6 ms，稳态单拍 2.14/3.16/3.49 ms（min/median/max，10 拍），第 3 拍起 `ok`（5.26/5.58/5.86/7.00%）——真实读数。FPS：`platform_supported=True`、后端文件与 sha256 通过、`permission check=missing`，PresentMon 退出 `access denied`，样本 `state=error` + 明确原因；**不宣称 FPS 验收通过**（与主分支 Game-FPS 报告一致，需 Performance Log Users 且重新登录）。

**验证**：`ruff check petcore tests scripts` → `All checks passed!`；探针族（GPU 13 + FPS 29 + hub 4）全绿；petcore 全量最终门禁 **1613 passed / 117.81s / exit 0**（较 L084 的 1522 +91）。架构红线复核：`platform/*` 无 PySide6、无网络库、无 `import pet.*`。

**未验证**：冻结包内 PresentMon 的实际落点（构建脚本有二次校验，本轮未重建安装包）；授权后的真实帧率读数；真实桌面下的岛段交互。

## L087 — 2026-10-26 — petcore 全量代码审计（只读，不改产品代码）

**范围**：无代码改动。新增 `docs/PETCORE-CODE-AUDIT-2026-10-26.md`，并在 `docs/INDEX.md` 登记。审计覆盖 `petcore/` 全部 194 个模块（48,716 行）、`tests/test_petcore_*.py`（118 文件 / 31,007 行 / 1613 用例）、`scripts/build_petcore.ps1`、`packaging/petcore.iss`、`docs/PETCORE-MOD-FORMAT.md`、`docs/PETCORE-MARKET.md`，逐条对照 `petcore/AGENTS.md` 的 429 行红线。

**方法**：九个方向并行深读 → 每条 P0/P1 写最小实验复现（临时目录 / offscreen / localhost 服务端，不接受「看代码觉得会」）→ 另开两个独立红队 agent 专做证伪，逐条要求 `CONFIRMED / 降级 / FALSE POSITIVE / UNVERIFIABLE` 并附自己的实验输出 → 冲突处以实验为准。

**结论**：28 条发现（P0 ×1、P1 ×9、P2 ×12、P3 ×6）。最严重的是 AUD-01：本地导入的 ZIP 自带空文件 `.trusted` 即被授信，绕过 `allow_feature_mods` 在用户进程里执行任意 Python（实测带标记得到 `[('evil.feature', True)]`、对照组 `False`，且执行发生在 `install` 的同一会话内、无任何确认框）；市场通道会剥标记，所以这是「本地导入路径独有的缺口」。P1 全在接缝上：`rollback()` 不是回滚（覆盖用户改动、多域共享 target 留下半迁移态、`not_found` 不计失败仍删标记）、`Config` 从 `DEFAULTS` 重建而丢弃所有未登记键（第三方 mod 的 `host.config.set` 存不住）、旧配置 159 个出厂键里 41 个同名可无损搬却没搬、「代理失败→直连」兜底因复用被 `ProxyHandler` 改写的 `Request` 而空转、`reload_mods()` 不清 `sys.modules`（「已重新加载」实际跑旧代码）、SSE 断流被当成回答完成、展开灵动岛卡片导致 GPU/FPS 1Hz 启停振荡并把两段报成「未接入探针」、720 宽下 API 页「保存服务」不可达、打包 `-OutputRoot` 用进程 CWD 解析且尾部反斜杠绕过守卫、冒烟只验退出码而不读 `check.json`。

**红队纠错（写进报告 §四）**：推翻 1 条——「岛隐藏后探针跑到进程退出」是假阳性，hub 的 1Hz `_poll → set_probe_snapshot → _update_size → _sync_sampling` 会在 1.0s 内自愈（实测 `hide+0.6s wanted=[] hub_timer=False`）；降级 6 条（FPS 重试 P1→P2、设置整组回写 P1→P2 且「滚轮缩放」归因错误、菜单提前落盘 P1→P2 但额外发现 `is_dirty()` 恒 True、聊天壁纸 P1→P3 且症状是发虚不是放大、`SystemExit` P1→P2、interaction 订阅 P1→P2）；修正 4 处数字/机制（旧键是 41/159 不是 16/138；`_imports` 盲区不是 `continue` 分支放行而是相对 import 在第一层就被丢；rollback 不删用户新写的文件、只无声覆盖同一文件上的修改；待办备份格式冲突不会让扩展永久加载失败，只是待办功能不可用）。

**验证**：审计开始时 `ruff check petcore tests scripts` → `All checks passed!`；`pytest tests/test_petcore_*.py` → **1613 passed in 204.70s**（即全部缺陷都在绿灯下存活，这是本轮最值得记录的事实）。审计后复跑 `tests/test_petcore_docs.py` 与 `tests/test_pr_report_discipline.py`，文档门禁保持绿。

**未验证**：真实 PresentMon 帧率（本机无 Performance Log Users）、真实安装器全流程（SAC 拦截新构建）、真实钥匙串（实验全部注入内存假后端）、多屏混合 DPI、真实声卡、真实外网 API（全部 localhost）、真实 GUI 点击流程；覆盖空洞用静态引用分析代替行覆盖率（未装 coverage，19 个模块真空白）。

**后续动作**：按报告 §六 的 S0–S8 批次排期。S0（AUD-01，约 5 行）与 S1（AUD-07 + AUD-08，同一文件）可立即做且互不冲突。修 AUD-36（守卫解析相对 import）之前必须先处理 `contracts/content.py:7 → ..engine.enums`，否则守卫修好即红。

## L088 — 2026-10-26 — 安全与授权（审计修复）

**范围**：`petcore/mods/{install,trust,manifest}.py`、`petcore/market/install.py`、`petcore/update/service.py`、`petcore/net/{http,errors,websearch}.py`、`petcore/ui/ports.py`。

**改了什么**：
- **AUD-01 授信双闸**：`mods.install.install(keep_trust_marker=False)` 落盘前剥掉包内 `.trusted`（市场 `BUNDLED` 来源显式传 True）；`trust.is_trusted()` 只认 `paths.install_mods_dir()` 下的标记。伪造标记的 feature 包现在 `allow_feature_mods=False` 时 `enabled=False` + 诊断。
- **AUD-02 安装信任锚**：`UpdateService.install()` 除自洽侧车外，还必须与 `select_asset(manifest)` 的文件名/大小/摘要一致；清单缺失时回退到下载时写下的缓存清单，重启后仍可安装。换包+同步改侧车现在被拒。
- **AUD-03/04 取数安全**：`fetch_url` 增 `allowed_schemes`（默认 http/https，`file://` 直接拒）与 `allow_private`；`fetch_page_text`（URL 来自模型）显式 `allow_private=False`；新增 `SafeRedirectHandler`（限 3 跳、禁 https→http 降级、跨源剥 `Authorization`/`X-Api-Key`/`Cookie`）。
- **AUD-05 压缩包条目**：拒绝 NTFS 备用数据流（含 `:`）、Windows 保留设备名、大小写折叠后重名；`is_valid_id` 排除保留设备名。

**验证**：新增 `tests/test_petcore_mods_trust.py`(13)、`tests/test_petcore_http_safety.py`(13)、`tests/test_petcore_update_anchor.py`(6)；`test_petcore_update.py`/`test_petcore_example_mods.py`/`test_petcore_extensions_nav.py` 按新语义更新（原用例把「侧车自证」「用户目录标记即授信」当成期望）。`ruff` 通过。

**未做**：`HttpPort` 默认仍允许内网地址（本地 LLM/局域网设备是合法用法），只拦 `file://` 等非 http(s) 协议；内网拦截只加在模型驱动的 `fetch_page_text`。

## L089 — 2026-10-26 — 配置与迁移的数据完整性（审计修复）

**范围**：`petcore/config.py`、`petcore/migration/{__init__,config_data,todos}.py`、`petcore/cli.py`、`petcore/ui/pet_manager.py`。

**改了什么**：
- **AUD-07 `extra` 区块**：`config.json` 顶层仍只允许 `DEFAULTS` 键；扩展偏好落 `extra`（`get/set` 透明）。键名白名单、单值 ≤4KB、总量 ≤64KB、命中密钥样式直接拒绝。`ScopedConfig` 把未登记键路由到全局（共享运行时只有一份配置）。
- **AUD-08 旧键不再静默丢**：别名表补 41 条同名键（`weather_city`/`todo_reminder_lead_minutes`/`voice_chime_voice`/`hotkey_open_chat`/`music_*`/`festival_*`/`island_metrics` 等）；config/slots 域 detail 增 `dropped:<n>:…`。
- **AUD-11 `--check` 只读**：`Config.load(persist=False)` 不备份、不写 `config.json`、不迁 slot。
- **AUD-12 坏配置留档**：读到坏 JSON 先改名 `config.json.bad-<时间戳>`，并在 `--check` 报 `config_corrupt_backup`。
- **AUD-06 回滚可追溯**：覆盖/删除前写 `<target>.pre-rollback-<时间戳>`；同一 target 多条 restore 只按最早那条恢复；`not_found`/`failed_write` 计入失败；只有全部成功才删标记；目标是目录时拒绝。
- **AUD-09 `completed` 判据**：同批有失败状态时不封存。
- **AUD-10 待办历史格式**：`todo-slot-0-legacy-v1.json` 改写成扩展期望的 `[title, …]`（原文件仍在 migration-backup）。

**验证**：新增 `tests/test_petcore_config_extra.py`(19)、`tests/test_petcore_migration_safety.py`(6)；`test_petcore_migration_todos.py` 的历史断言按新格式更新。`--check` 前后快照比对：新增文件里没有 `config.json`/`migration-backup`。

**未做**：`unsupported` 仍计入 `FAILURE_STATUSES`（未装待办扩展时 `petcore migrate` 退出 1）——不在本轮审计清单内，留待产品决定。

## L090 — 2026-10-26 — 网络与对话正确性（审计修复）

**范围**：`petcore/net/{http,errors,tts}.py`、`petcore/api/chat.py`、`petcore/account/presenter.py`、`petcore/speech/service.py`、`examples/petcore-mods/ai.chat/host/runtime.py`。

**改了什么**：
- **AUD-13 代理兜底**：`urlopen` 每次尝试 `clone_request()` 重建请求（`ProxyHandler` 会就地改写 host/selector），兜底不再是空转。
- **AUD-18 Retry-After**：带 `Retry-After` 的 500/502/504 不换路由；`http_error` 支持 HTTP-date 形态。
- **AUD-17 分类**：新增 `tls`（证书校验失败，不再报「网络不可达」）；`http.client.HTTPException`（IncompleteRead/BadStatusLine）归 `protocol` 而不是 `None`。
- **AUD-16 截断信号**：`fetch_url` 返回 `Body(bytes)`，带 `truncated`/`declared_length`；TTS 的 `_require_audio(truncated=)` 拒绝半截音频。
- **AUD-15 SSE 完整性**：`ChatResult` 增 `incomplete`/`end_reason`；无 `[DONE]`/无 `finish_reason`/空体都要标记；HTTP 200 + `data: {"error":…}` 按状态码抛错；支持多行 `data:`；ai.chat 在半条回答后追加可见提示。
- **AUD-23 检索预算**：`_RESEARCH_BUDGET_SECONDS = 45`，轮次与工具调用前都检查。
- **AUD-24 取消打断读**：读超时压到 0.5s 并循环，取消/总预算在循环里生效；取消返回 `cancelled=True` 而不是「连接超时」。
- **AUD-19 单飞**：`_done()` 只摘自己那条 future。
- **AUD-20 兜底音色缓存键**：按**实际使用**的音色算路径，第二次命中也不会谎报音色。

**验证**：新增 `tests/test_petcore_api_chat_stream.py`(7，裸 socket 覆盖断流/空体/错误帧/多行 data/取消)；`test_petcore_balance.py` 增单飞用例；`test_petcore_speech.py` 增兜底音色与截断用例。

**未做**：真实外网 SSE 形状（DeepSeek/豆包/MiMo）仍未实测，全部 localhost。

## L091 — 2026-10-26 — 运行时卫生（审计修复）

**范围**：`petcore/ui/app.py`、`petcore/mods/feature.py`、`petcore/ui/ports.py`。

**改了什么**：
- **AUD-14 重载真的重载**：`reload_mods()` 换 adapter 前 `detach_all()`，并对用户 mod 根与内置特性目录逐个 `_drop_modules_under()`；`from .runtime import X` 现在读到新代码。
- **AUD-21 SystemExit 不再打断启动**：`FeatureManager.load()` 捕 `BaseException`（`KeyboardInterrupt` 重抛），转成诊断。
- **AUD-22 订阅可撤**：`stop_owner()` 增加 `interaction.cancel_owner()`；`QtScheduler` 按 owner 记句柄并实现 `cancel_owner()`；`InteractionAdapter.on_tick()` 返回注销函数（对齐契约），新增 `detach_all()`。

**验证**：新增 `tests/test_petcore_mods_reload.py`(7)：改子模块后先 v1、`_drop_modules_under` 后 v2；`sys.exit(3)` 只产出诊断；`cancel_owner` 清定时器；`on_tick` 返回可调用注销函数。

## L092 — 2026-10-26 — UI 可达性与采样口径（审计修复）

**范围**：`petcore/ui/{island,probes}.py`、`petcore/ui/settings/{base,dialog,page_interaction,menu_editor,coverage,pages_core}.py`、`petcore/AGENTS.md`。

**改了什么**：
- **AUD-25 岛不再自激**：`_sync_sampling()` 只在**胶囊态**把 folded 段排除，卡片态按启用段继续采样；`_card_width()` 改用胶囊态自然宽，消除 420→340 的棘轮。同步改写 AGENTS 的「看不清就不采」红线。
- **AUD-26 最小窗可达**：根滚动区横向策略改 `AsNeeded`（720×500 下右侧按钮可滚动到）。
- **AUD-27 差异写入**：互动页 `_on_changed` 只写「与上次写出值不同」的键，不再整组回写覆盖右键菜单的改动。
- **AUD-28 菜单编排**：`MenuEditor.confirm_save()` 不再自己 `config.save()`；`_initial_value` 与 `value()` 同结构（`is_dirty()` 不再恒真）。
- **AUD-29 搜索覆盖**：`page_search_texts()` 含 QLabel + 按钮 + 勾选 + 下拉项 + 列表项；过滤时实时构建 haystack；`reveal_text` 支持按钮。
- **AUD-30 覆盖表**：`ai_chat/look_screen/screen_capture` 指到 `appearance`，`api_settings` 指到真实存在的「服务」；测试改成「落点文案必须能在该页搜索文案里找到」。
- **AUD-31 探针重试**：hub 记住 `_wanted`，`_poll()` 每拍 `_reconcile()`（只在监控器没在跑时重试 start）。

**验证**：`test_petcore_island_window.py`（卡片态不停采、胶囊折叠仍停采）、`test_petcore_probe_hub.py`（拒绝后下一拍重试、卡片态不振荡）、`test_petcore_settings*.py`（720 宽可达、菜单不 dirty、整组回写不覆盖）通过。

**未做**：`page_api` 左右两列在窄窗下仍是横向滚动（未改上下排布）。

## L093 — 2026-10-26 — 结构守卫与构建门（审计修复）

**范围**：`tests/test_petcore_architecture.py`、`petcore/contracts/{enums,content}.py`、`petcore/engine/enums.py`、`petcore/cli.py`、`scripts/build_petcore.ps1`、`tests/test_petcore_{candidate_packaging,installer,pages_smoke}.py`。

**改了什么**：
- **AUD-36 守卫看得见相对 import**：`_imports()` 按 `node.level` 还原绝对名；`contracts` 白名单改成「标准库 + 自身」；新增 `engine` 只依赖 stdlib+contracts、`island/speech/chat` 零 Qt、平台探针零 Qt 三组守卫；合并两条重复的 mod 守卫，并明确「设置页是受信 UI」的例外。为让 contracts 不再依赖 engine，把 `AnimatType/GraphType/ModeType` 移到 `contracts/enums.py`（`engine/enums.py` re-export）。
- **AUD-37 OutputRoot 收束**：相对路径按仓库根解析、比较前 TrimEnd 分隔符；新增 `-DryRun` 打印最终绝对路径。
- **AUD-38 构建门**：`petcore --check --strict` 在 `menu_template != json`、`builtin_readable == 0`、`characters` 空或 diagnostics 非空时退出 1；构建脚本改用它并 `ConvertFrom-Json` 复核四项。
- **AUD-40 先验后打**：`verify_presentmon`（源文件）移到 PyInstaller 之前，产物侧校验保留。
- **AUD-41 有读出口**：`--check` 输出 `capabilities`；新增 `test_petcore_pages_smoke.py`（常规页/多宠页/采样循环）。

**验证**：`tests/test_petcore_architecture.py` 9 项全绿；`build_petcore.ps1 -DryRun` 实测相对路径落在仓库内、尾部反斜杠被拒；新增 3 条打包 dry-run 用例与「每条 mod `[Files]` 行必须带 `Tasks:`」用例。

## L094 — 2026-10-26 — 画质、动画与路径（审计修复）

**范围**：`petcore/ui/pet_window.py`、`petcore/ui/chat/{window,themes}.py`、`petcore/media/{images,thumbnails}.py`、`petcore/paths.py`、`petcore/cli.py`。

**改了什么**：
- **AUD-34 命中与绘制互逆**：`is_transparent_at` 改成「先反旋转、再反 Q 弹」，与 `paintEvent` 的 R∘S 对齐（原顺序在 Q 弹+旋转同时生效时偏移 5–20px）。
- **AUD-33 冷 meta 与断流**：移动计划记录 `duration`/`started_at`；`frames_per_loop<=1` 时按墙钟插值（不再首帧即到终点）；帧龄超过 `duration*2+1s` 由看门狗收口。
- **AUD-32 壁纸 DPR**：`scale_background_pixmap(dpr=)` 按 DPR 解到物理像素并写回，缓存键含 DPR，几何用 `deviceIndependentSize()`。
- **AUD-35 解码开销**：`load_pixmap` 用 `QImageReader.setScaledSize`（12MP 约 92ms → 9ms），预缩放后不再二次放大。
- **AUD-42 路径**：portable 数据根改用 `install_root()`；`--root ""` 按未指定处理；`--instance` 设置 `PETCORE_INSTANCE`，缩略图磁盘缓存按实例分目录。

**验证**：`test_petcore_images.py`(8)、`test_petcore_move_sync.py`（冷 meta 半程、看门狗收口）、`test_petcore_chat_ui.py`(25) 全绿。

**未做**：缩略图的 PNG 编码/落盘仍在 GUI 线程；缩略图磁盘缓存没有 LRU 上限（只做了实例隔离）。

## L095 — 2026-10-26 — 点击显示余额验收与构建冒烟等待修复

**范围**：`tests/test_petcore_click_balance.py`（新增）、`scripts/build_petcore.ps1`、
`tests/test_petcore_candidate_packaging.py`、产物 `rel-balance-2026-10-26/`。

**背景一（功能本身）**：用户问「点击显示余额做回来了吗」。该特性自 P5（`ce8c67fb`）起已在源码里
全链路实现：`config.click_show_balance`（按 slot）→ 设置「API 与账户 → 点击显示余额」→
`PetWindow._on_click()` → `_maybe_request_click_balance()` → `CoreHost.request_balance()`
→ `BalancePresenter` 单飞 → `_on_balance_result()` 同时写气泡与灵动岛；右键「桌宠控制 →
显示余额」也走同一入口。用户手上是 **10-23 19:39** 构建的旧 setup，早于 P5 提交（10-26 00:35），
所以界面里没有这一项——属分发滞后，不是功能缺失。本轮不改产品代码，只补点击回归测试并重建候选包。

**背景二（构建门真缺陷）**：首轮重建时 `--check --strict` 冒烟报
「找不到路径 …\check.json」，但 dist 已生成、`check.json` 稍后出现。实测根因：

```text
& $exe --check --strict --out <path>   → elapsed_ms=52, exists_now=False, $LASTEXITCODE=(空)
& $exe --check --out <path> | Out-Null → elapsed_ms=698, exists_now=True,  exit=0
```

`-Windowed` 产物是 **GUI 子系统**，PowerShell 用 `&` 调用时**不等待进程退出**，所以
`$LASTEXITCODE` 是上一条命令（python）的残留值、报告还没落盘就被读取。上一轮同样的脚本能过，
是因为当时 `check.json` 恰已存在——即这道门一直在「读旧报告或直接崩」，并没有真正校验。

**改了什么**：
- `scripts/build_petcore.ps1`：冒烟改为
  `Start-Process -FilePath $exe -ArgumentList @("--check --strict","--out","…") -Wait -PassThru`，
  按 `$smoke.ExitCode` 判红；新增「报告必须存在」「报告必须是本次新写（LastWriteTime 前进）」
  两条断言；构建成功后打印 `menu_template / characters / builtin_images / strict` 摘要。
  不引入任何 `Remove-Item`，保持「`-Clean` 只传 `--clean`、脚本不删目录」的既有约定。
- `tests/test_petcore_candidate_packaging.py`：新增
  `test_smoke_waits_for_the_windowed_exe`（断言 `Start-Process -FilePath $exe` + `-Wait -PassThru`
  存在，且旧的 `& $exe --check` 不再出现）。
- `tests/test_petcore_click_balance.py`（4 例）：点击按被点那只的 slot 发请求；关掉选项一次都不请求；
  余额端口抛错不打断点击动画与监听者；飞行中（`motion.thrown`）不消费点击故不发请求。

**验证**：
- `pytest tests/test_petcore_click_balance.py -q` → **4 passed**；
- `pytest tests/test_petcore_candidate_packaging.py -q` → **10 passed**（含新例）；
- `pytest -q $(ls tests/test_petcore*.py)` 全量 → **1713 passed in 131.57s（exit 0）**；
- `pytest tests/test_petcore_docs.py tests/test_pr_report_discipline.py -q` → **301 passed**；
- `ruff check tests/test_petcore_click_balance.py tests/test_petcore_candidate_packaging.py` → 通过；
- 真机计时探针见上表（同一台机器、同一个已签名 exe）；
- 重建产物：`rel-balance-2026-10-26/dsh-pet-core-setup.exe`（签名主体 `CN=DSH Pet Core Dev`），
  构建日志 `.scratch/build-l095-b.log`，`--check --strict` 摘要与 Inno 结果见当轮汇报。

**未做 / 风险**：本机 Smart App Control 仍是强制模式（`VerifiedAndReputablePolicyState=1`），
新 setup 能否直接双击运行取决于签名信任链；真机安装验收仍需用户执行。余额查询本身依赖
「API 与账户」里配置了支持余额协议的服务，未配置时点击只会提示「没有配置余额服务」。
冒烟等待的回归只做脚本结构断言（PowerShell 的 GUI 子系统等待语义不适合单测复现），
真实验证靠每次构建的真机日志。

## L096 — 2026-10-26 — 图标槽位、动画缩略图与灵动岛停靠/动效移植

**范围**：`petcore/ui/icons.py`、`petcore/ui/menu_style.py`、`petcore/ui/menu_actions.py`、
`petcore/ui/settings/{dialog,pages_core}.py`、`petcore/ui/island.py`、
`petcore/island/layout.py`、`petcore/config.py`、`tests/test_petcore_icons.py`（新增）、
`tests/test_petcore_island_dock.py`（新增）、`tests/test_petcore_menu.py`、
`tests/test_petcore_settings.py`。

**用户报的三件事**
1. 设置与右键菜单图标过大；2. 动画预览图标过小；3. 灵动岛拖不动，且主分支的
   动画效果与「吸附」都没做过来。

**改了什么**

*图标（1、2）*
- `icons._pixmap()`：把「36 单位设计稿」与「显示槽位」分开。以前 `icon(name, 18)` 是
  把 36 单位坐标硬画进 18px 画布（直接裁掉一半），所以没人敢改尺寸、只能让 QSS 用 24px
  盒子；现在按 `size/36` 缩放并反向补偿笔宽，最终笔画恒为 1.6 逻辑像素。
- `MENU_ICON_SIZE = 18`（主分支 `small_icon_size(menu)=18`、桌面版设置侧栏 18）：
  菜单 QSS 的 `QMenu::icon` 从 24px 收到 18px，`QMenu::item` 左内边距 38→30
  （4 图标 + 18 + 8 间隙），设置侧栏 `nav.setIconSize` 24→18。
- `icons.fitted_icon()`：整帧素材先裁 alpha 包围盒、再按短边适配进槽位（主分支
  `fitted_pet_pixmap_icon` 同款）。**这是「预览图标过小」的根因**：动画帧是 640×390
  的视频画布，角色只占中间 100×150，直接缩进 18px 槽位只剩 3×7 像素。实测同素材
  不透明像素 20 → 164。同时处理两个坑：无 alpha 通道的帧（RGB32）`createAlphaMask()`
  返回空图，不能当「全透明」丢掉；整帧全透明才返回空图标（保留占位）。
- `menu_actions._request_thumbnail()` 改用 `fitted_icon`，不再 `setDevicePixelRatio`
  后直接塞 `QIcon`（DPR 会让 Qt 按物理像素当逻辑像素，图标再小一半）。

*灵动岛（3）*
- **拖不动的真根因**：`mouseMoveEvent` 调 `self.startDragDistance()`，而 `QWidget`
  没有这个方法（实测 `AttributeError`），每次拖拽在第一帧抛异常 → 退化成单击。
  改 `QApplication.startDragDistance()`，并补 `buttons()` 守卫与起拖三清
  （停悬停动效、清 `_geo_from/_geo_to`、收卡片/退出停靠）。
- **停靠（吸附）**：`petcore/island/layout.py` 新增零 Qt 纯函数
  `dock_edge_for()`（四边等权、阈值 40px、取 `abs`）、`strip_rect_for()`
  （16px 厚、左右 64 长、竖直中心用 `rect.top() + h // 2` 防 1px 漂移）、
  `ease_out_cubic()`。窗口层：`_rest_size/_apply_fixed_size/_target_rect` 四种目标
  几何、`_docked/_hover_peek` 两个状态、细条绘制（含靠屏侧 2px 强调线）。
- **释放顺序**（主分支 issue #154 口径）：按**松手光标**选屏 → 夹回屏幕 → 判停靠
  （看夹后落点）→ 翻 `_dragging` → `_update_size()` → 存位置/停靠边 → 切模式动画。
- **悬停滑出**：可悬停区 = 滑出胶囊 + 细条两张矩形逐条 `contains`（左右停靠上下各有
  10px 死区，取包围盒会振荡）；离开 800ms 收回，收回前复检，光标仍在区内就延后一拍。
- **几何动画**：`_animate_to()` + 16ms 表（仅动画期间运行）+ 每帧
  `_set_free_geometry` + `set_geometry_changed_callback`；不可见时直接落位（否则
  `_geo_to` 永远停在半路）。拖拽期间 `_update_size()` 直接 return（几何冻结）。
- **显示即收口**：`showEvent` 先把 `_geo_from/_geo_to` 清空、停悬停弹簧、
  `_apply_fixed_size()` 后 `_set_free_geometry(_target_rect())`——隐藏可能打断
  停靠/滑出动画，不先收口会让岛以一个中途几何重新出现。
- **动效**：悬停 1.02 缩放弹簧（钳 0.97–1.03）、事件反馈（余额/消息）果冻弹簧
  （竖压横鼓、绕胶囊中心、钳 0.85–1.12），都写在 `_on_anim_tick`，停止时连速度归零。
- **看不清就不采**：`_strip_visible()` 时 `_sync_sampling()` 停掉全部采样器；
  细条态 `_clamp_to_screen()` 直接 return；避让在拖拽/停靠时不参与（否则与拖拽抢
  `move()`、把细条推离边缘）。
- **配置**：新增 `island_edge_dock`（默认 True）与 `island_dock_edge`
  （`none/top/bottom/left/right`，白名单校验）；设置「外观与交互 → 灵动岛」加
  「贴边停靠」一行；`island_x/island_y` 在停靠时存**胶囊滑出位**且跳过 12px 吸附。
- **宽度锚点**：`_update_size()` 接上已有的 `layout.anchored_x()`，但**首次定型跳过**
  ——`__init__` 里 `self.width()` 还是默认值，锚点会把 `_apply_position()` 刚恢复的
  位置挪走（测试实测 200 → 330）。

**验证**
- `QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe -m pytest tests/test_petcore_icons.py -q` → **6 passed**
  （字形不裁边且占槽位 ≥55%、整帧裁透明后角色撑满、宽高比不变、全透明返回空）；
- `pytest tests/test_petcore_island_dock.py -q` → **18 passed**（拖动真的移动窗口、
  40px 停靠、非边缘不停靠、开关关闭不停靠、拖出停靠恢复胶囊、悬停滑出/800ms 收回/
  光标在区内不收回、细条停采滑出恢复、动画插值到终点、隐藏即落位、拖拽冻结宽度）；
- `pytest tests/test_petcore_island_window.py tests/test_petcore_island_p5.py
  tests/test_petcore_settings.py tests/test_petcore_menu.py tests/test_petcore_menu_icons.py
  tests/test_petcore_pages_smoke.py -q` → 全绿（含把「占位图标 36px」「缩略图 64×51」
  两条旧断言改成 18px 槽位与「cacheKey 变化」）；
- `pytest -q $(ls tests/test_petcore*.py)` 全量 → **1737 passed in 125.59s（exit 0）**；
- `pytest tests/test_petcore_docs.py tests/test_pr_report_discipline.py -q` → **303 passed**；
- `ruff check petcore tests examples scripts` → 通过；
- 离屏渲染核对：菜单项图标 18px、文本左内边距 30px；整帧素材裁剪后缩略图撑满 18px。

**未做 / 风险**
- 状态点五态、声纳环、回复后的呼吸浮动、内容淡化（需要内容位图缓存）仍未移植；
  主分支的视觉装饰（三层投影 / sheen / 软陶笔）不逐像素对齐。
- 停靠边切换的观感只在离屏环境验证，真实多屏（跨屏拖动落点、混合 DPI）需人工验收。
- 设置侧栏图标 18px 与行高 42px 的观感需真机确认；若嫌小可只调
  `nav.setIconSize`（图标本身按槽位缩放，不会再被裁）。

## L097 — 2026-10-26 — 图标重量与菜单/侧栏几何对齐

**范围**：`petcore/ui/icons.py`、`petcore/ui/menu_style.py`、`petcore/ui/menu_actions.py`、
`petcore/ui/settings/dialog.py`、`tests/test_petcore_icons.py`（新增）、
`tests/test_petcore_menu.py`、`tests/test_petcore_settings.py`。

**背景（先量后改）**：用户第二次反馈「设置和右键菜单的图标还是过大、动画预览还是过小」，
并提示「这个重写任务刚开始的时候图标是正常的」。用同一块 18px 画布量不透明像素（alpha>40）：
`settings` 初版 `0922b13a` **68** / 上一版 **120** / 主分支 **121**；`chat` 初版 20（被裁）
/ 上一版 131；`network` 70 / 104 / 84。结论：L096 把笔宽钉成**恒 1.6 逻辑像素**
（`_STROKE_PX / scale` 反向补偿），18px 下墨量是初版的 1.8–6 倍——「过大」与槽位无关，
是笔画重量。初版「正常」的机制是：36 单位设计稿不缩放，笔画随画布缩到 ≈0.8px。

菜单几何也量了：上一版行高 **32px**、图标墨迹 12×12 起于 **x=9**、菜单高 106；
主分支 modern **24px** / 14×14 / x=17 / 高 86。

**改了什么**
- **笔画口径**：`_pixmap()` 改成 `pen = max(1.6 设计单位, 0.75px / scale)` ——
  小槽位下笔画自然变细，14px 以下有 0.75px 地板不糊。
- **侧栏**：新增 `SIDEBAR_ICON_SIZE = 16`；行高 42→**34**、图标按 16 绘制、视图槽位仍 18
  （主分支 `SIDEBAR_ROW_HEIGHT` / `SIDEBAR_ICON_SIZE`）。
- **菜单 QSS**：对齐主分支 modern —— `padding 6`(+1px 边框=7)、圆角 12、
  **`icon-size: 18px`**（删掉被 Qt 忽略的 `QMenu::icon { width/height }`）、
  `QMenu::icon { left: 9px }`、item `3px 29px 3px 13px` + 圆角 9、分隔线 `4px 4px`、
  右箭头 `7×11 margin-right 10`、`min-height = max(18, 字号+5)`。
- **动画缩略图**：`fitted_icon()` 填充率 0.96→**1.04**、y −0.2（主分支
  `fitted_pet_pixmap_icon`），配合 24px 行高，18px 缩略图占行比 56%→75%。

**验证**：新增 6 例图标回归（墨量上限、不裁边、14/16px 档仍有笔画、24 字形不出界、
QSS 用 `icon-size`）；`test_petcore_menu.py` 增「行高 24 / 菜单高 86 / 图标贴左缘」。
`pytest tests/test_petcore_{icons,menu,settings}.py -q` → **68 passed**；
离屏对照图 `.scratch/icons-compare.png`（now / 初版 / 主分支 三行）。

## L098 — 2026-10-26 — 外观页内分页、点击查看余额与峰谷提示

**范围**：`petcore/account/pricing.py`（新增）、`petcore/config.py`、`petcore/ui/app.py`、
`petcore/ui/settings/{pages_core,page_interaction,page_api,coverage}.py`、
`tests/test_petcore_pricing.py`（新增）、`tests/test_petcore_balance_subtitle.py`（新增）、
`tests/test_petcore_settings.py`。

**改了什么**
- **外观页内分页**：`AppearancePage` 用 `ExitStack` 包出四个 `SettingsPage.tab()`：
  `size` 大小与外观 / `island` 灵动岛 / `text` 文字与快捷键 / `screen` 屏幕与位置
  （不缩进 200 行控件代码；`section()` 自动落进当前分页；搜索仍跨页命中，
  `reveal_text` 会自动切页）。
- **点击查看余额换位置**：从「API 与账户」搬到「互动与物理 → 点击与音效 → 余额提示」
  （与主分支同页），API 页留一行说明；`coverage.show_balance` 落点同步改
  `("interaction", "点击查看余额")`。
- **峰谷提示**：新模块 `petcore/account/pricing.py` 按主分支逐字移植 —— 北京时间、
  周末/法定节假日空闲、工作日 09–12/14–18 高峰、40 天扫描下一档位、今天/明天/ISO 周
  三种措辞、`default/liangwen/custom` 文案模式、HTML 转义、颜色 `#e5484d`/`#30a46c`。
  节假日由调用方注入（core 不反向依赖 `fun.festival`）；时区用固定 +08:00，免 tzdata。
- **接线**：`_on_balance_result` 成功时把 `pricing.subtitle_for(config)` 作为副标题，
  `_notify(..., subtitle=)` → `say(..., subtitle=)`（老签名降级为无副标题）。
- **配置**：4 个全局键 `balance_tier_labels_mode/label_peak/label_idle/color_enabled`。

**验证**：pricing 13 例 + 副标题 5 例 + 设置页新增 4 例；`click_show_balance` 仍是
按宠保存（取消回滚用例保留）。合并 L097 后同批 **115 passed**。

## L099 — 2026-10-26 — 灵动岛五项视觉动效收尾

**范围**：`petcore/island/layout.py`、`petcore/ui/island.py`、
`petcore/ui/chat/controller.py`、`petcore/ui/screen/{ask_window,controller}.py`、
`petcore/ui/app.py`、`tests/test_petcore_island_visuals.py`（新增）。

**改了什么**
- **五态状态点**：`layout.dot_state()`（错误 > 待确认 > 工作 > 隐藏 > 完成 > 空闲）+
  `dot_style()`（实心/空心/感叹 × accent/muted/done/error）；完成是 4s 瞬态（单发表）；
  空心环 pen 1.5 内缩 0.75（RoundJoin）；感叹笔画 `8×0.17`、比例 .18/.46/.82、
  白 α235；珠感高光只给实心/感叹（空心环里是空的，高光会把环画回实心）。
- **声纳环**：`sonar_ring_params(t)`（半径 5→13 线性、α = 90·(1−t)³），独立 33ms 表，
  只在 `working` + 可见 + 非展开 + 非停靠 + `show_status` 时跑，画在内容之上。
- **回复后呼吸**：`notify_event("reply")` 设 5s 窗口，`paintEvent` 整窗
  `translate(0, round(1.5·sin(2.6t)))`；呼吸期间计入 `_animating()`，到点归零。
- **130ms 内容淡化 + 位图缓存**：`_paint_content` 拆成 `_render_content_pixmap()`
  （画进 `width×44` 的 QPixmap，DPR 自带）+ 内容层 blit；缓存键含段文案/状态/severity、
  名字、开关、配色、accent、dot(形状,色)、字体键、DPR、宽度；**结构键**单独记账
  （段 id 序列 + 折叠级 + 名字布局 + 宽度），只有结构变化才播淡化 —— 时间/百分比
  换字不闪。淡化用旧图按新图透明区遮罩（`createMaskFromColor`）+ `opacity = 1−t`。
- **外壳质感**：三层投影（(1,+1,α28)(2,+2.5,α16)(3,+4,α8)）、顶部 sheen
  （top+3、42%、α14/0/34）、软陶 bevel（内缩 1.5、上白 26/下黑 46，light 230/20，
  glass 90/(35,50,60,26)）、glass 四停点渐变（214/168/146/196 × opacity）+ 白 α130 keyline、
  段间竖线竖直 alpha 渐变、头标内环（白 α46 / 黑 α20）、单位盒 α×0.7。
- **真实来源**：chat 起流→`working`、`_apply_done`→`done`、`_apply_error`→`error`；
  截图提问 `waiting_changed`→`working/idle`；`set_all_visible`→`hidden` 派生；
  余额失败→`error`。
- **边界**：`waiting` 态**只提供 API 与渲染**——petcore 目前没有审批/追问来源，
  不臆造触发点（等 `ai.agent` 接审批后再上行）。

**子 agent 审查后的修复（同一提交内）**
- **状态按来源归并**：`set_agent_state(state, *, source=)` + `layout.resolve_agent_state()`
  （错误 > 待确认 > 工作）。原来单标量后写覆盖：截图收尾发 idle 会把仍在流式回答的
  working 关掉；反过来一次余额刷新会把 working 打回 idle。chat→`source="chat"`、
  截图→`source="screen"`。`error` 加 8s TTL（petcore 没有 DSH 收敛器，否则永久红感叹）。
- **余额不再动状态点**：`show_balance()` 只写余额面（卡片行 + 告警级 + 气泡）；
  一次「没配余额服务」的点击曾把红感叹永久钉住。
- **结构键去掉窗口宽度**：原来 `_structure_key()` 含 `width`，几何动画每帧都变 →
  每帧重起淡化（180ms ≈ 11 帧），且新旧宽度不同时 `setMask` 尺寸不匹配被 Qt 静默丢掉 →
  旧图新图各画一遍 = **重影**。改「行预算 + 段 id 序列」，`_fade_mask_for()` 尺寸不符
  返回 None，`_content_layer()` 只在同尺寸时起淡化。
- **位图内容平滑重采样**：`paintEvent` 开 `SmoothPixmapTransform`（悬停 1.02 缩放时
  不开就是最近邻，正文发毛）。
- **可见性四条路收口**：新增 `_sync_island_pet_visible()`，`PetWindow.set_visible()` 回调
  宿主；快捷键、托盘全部、托盘逐宠、右键隐藏都能触发 hidden 派生。
- **其余口径**：缓存键补 `box_text`/`elide_px`（GPU/FPS 换样本时盒宽才更新）、去掉多余的
  `opacity`；`_sync_sonar()` 补到展开/收起、停靠落定、细条收回、滑出四个切换点；
  展开态不再让 16ms 表为空跑的呼吸空转；投影下伸按剩余高度收敛（果冻+悬停+呼吸会推过
  44px 窗高）；声纳环裁剪到胶囊路径。
- **测试补强**：错误感叹改采竖杠（原采样点在红底上，`#f2777a` 亮度 181 让断言恒真）、
  新增多来源归并、余额不动点、`stop()` 停四张表、声纳在展开/停靠被抑制、
  缓存键含状态点、**宽度变化不淡化**、结构变化（指标换序）才淡化 —— 共 **30 例**。

**验证**：新增 23 例（纯函数 + 离屏像素 + 缓存/淡化状态机）；全量
`pytest -q $(ls tests/test_petcore*.py)` → **1784 项（1783 passed）**，唯一失败是
`test_petcore_websearch.py::test_fetch_page_text_is_truncated` 的环境问题（本机 fake-ip DNS
把 `e.test` 解析到 `198.18.0.0/15`），已修。性能实测：内容位图重建 mean 0.151ms、
缓存命中 0.0044ms（约 34×）。

## L100 — 2026-10-26 — 本轮文档、门禁、打包与提交

**范围**：`docs/PETCORE-SPEC.md`、`docs/PETCORE-MAIN-PARITY-BACKLOG.md`、
`petcore/AGENTS.md`、`docs/INDEX.md`、三份 PR 报告、`.gitignore`。

**改了什么**
- `.gitignore` 补 `rel-*/`：`scripts/build_petcore.ps1 -OutputRoot rel-<tag>` 的整包产物
  是安装器输出（数千文件），不允许被 `git add -A` 捡进提交（本轮第一次提交时踩到，
  已回滚暂存并加规则）。
- SPEC 新增「2026-10-26 视觉与设置收尾」小节：图标口径、菜单几何、设置分页、
  4 个峰谷键、灵动岛五项动效与缓存纪律。
- `petcore/AGENTS.md` 加红线：笔画随画布缩放（不许钉死逻辑像素）、菜单行高 24、
  内容缓存键与结构键分离、声纳只在 working、呼吸 5s 整数像素。
- PARITY-BACKLOG：五态/声纳/呼吸/淡化/投影-sheen 从「未做」改「已做」，余项列明。

**验证**
- `pytest -q $(ls tests/test_petcore*.py)` → **1795 passed in 132.02s**（新增 34 例：
  岛视觉 30 + 状态来源接线 4）；
- `ruff check petcore tests examples scripts` → 通过；`python -m petcore --check` → 退出码 0；
- 构建：`pwsh -File scripts/build_petcore.ps1 -Clean -Installer -SignThumbprint
  217337C099EC8624AC481BFC9F3E1664D0A442E1 -OutputRoot rel-visual-2026-10-26`
  → 安装器 **268,556,584 B**、sha256 `d9a43c575ddc22629e970cbdc331302736a16b4989b80dd240d8a79bc8011ca1`、
  `Get-AuthenticodeSignature` = Valid（CN=DSH Pet Core Dev）；打包内 `--check --strict`
  的 `check.json` 为 `strict.enabled=true, problems=[]`（冒烟用 `Start-Process -Wait` 取真实退出码）；
- 离屏证据：`.scratch/icons-compare.png`、`.scratch/menu-now.png`、`.scratch/sidebar.png`、
  `.scratch/page-{appearance,interaction}.png`、`.scratch/island-visuals.png`；
- 提交并推送 `feat/petcore-vpet-architecture`。

## L101 — 2026-10-26 — 菜单/侧栏图标 DPR 修复 + 灵动岛去头标与消息开关

**范围**：`petcore/ui/icons.py`、`petcore/ui/menu_style.py`、`petcore/ui/menu_model.py`、
`petcore/ui/menu_actions.py`、`petcore/ui/settings/{menu_editor,pages_core}.py`、
`petcore/ui/island.py`、`petcore/island/layout.py`、`petcore/config.py`、
`examples/petcore-mods/ai.chat/host/__init__.py`、`tests/test_petcore_{icons,menu,menu_icons,p5,settings,island_p5,island_window,island_visuals,probe_hub}.py`。

**改了什么**
- **图标「变大 + 缺右下角」的真根因：DPR 乘了两次**。`icons._pixmap()` 先按
  `logical × dpr` 建画布并 `setDevicePixelRatio`，随后又 `painter.scale(dpr*scale)`；
  Qt6 的 QPainter 在带 DPR 的 QPixmap 上坐标系已经是逻辑像素，于是 150% 屏上字形按
  1.5 倍绘制、贴死画布右下并被裁：实测 18px 槽位（27px 画布）play (14,8,**26,26**)、
  screen (5,8,**26,26**)、quit (10,5,**26,26**)，显示器底座整段画到画布外（截图里
  「看看屏幕/截图提问」只剩一个空框）。改为 `painter.scale(scale, scale)`，
  口径对齐主分支 `_new_icon_canvas`（`scale(size/16)`，不再乘 dpr）。100% 屏无变化
  （ratio==1），非整数 DPR 屏恢复正常留白。
- **补齐字形**：新增 `pet`/`tools`/`web`/`todo`/`download`/`sparkles`/`capture`/`eye`
  八枚 36 单位线条字形。`web`(5 处)、`todo`(4 处)、`download`(1 处)、`sparkles`(2 处)
  在 builtin 与示例 mod 里早就在用，之前全部落到兜底的「小圆点」。
- **勾选项恢复图标（勾选挪到行尾）**：`MenuModel._append_item` 去掉
  `if not item.checkable`——那一行把 `主动识屏/Agent 联动/免打扰` 声明的
  `screen/agent/pause` 静默丢掉了。勾选标记改由 `menu_style.MenuCheckLayer`
  画在行右缘 21px（13px 强调色圆 + 白勾，主分支 `ModernCheckLayer` 移植），QSS 的
  `QMenu::indicator` 只留 1px 且 `image: none`；`aboutToShow` 同步几何并补接
  `toggled`/`triggered`，勾选/取消即时生效。QSS 临时对勾图片（`_render_check` /
  `check_image_path` / `_CHECK_*`）整块退役。
- **托盘菜单也装勾选层**：QSS 对勾退役后勾选只由 `MenuCheckLayer` 提供，而它原先只装在
  `MenuModel.build()` 建的菜单上；托盘菜单（`CoreHost.build_tray_menu`，裸 QMenu + QSS）
  于是把「鼠标穿透」的勾选状态画没了——托盘正是开启穿透后唯一能关回来的入口。
  改为在重建钩子 `aboutToShow` **之后**装层，每次重建出的新 QAction 由层的
  `aboutToShow → _sync_menu_checks` 补接 `toggled`。
- **子菜单图标**：`_append_node` 的图标回退链改成「节点 icon → 已注册 MenuItem.icon →
  `SUBMENU_ICONS`」——`扩展` 之前明明注册了 `application` 却不显示；`桌宠控制`/`工具与帮助`
  是纯模板节点，由 `SUBMENU_ICONS` 兜底（老 layout 没有 icon 键也生效，不依赖模板迁移）。
- **图标语义去重**：`截图提问` 改 `capture`（屏幕 + 内框）、`主动识屏` 改 `eye`
  （`examples/petcore-mods/ai.chat` 的菜单贡献），原来三行连着三个一模一样的显示器。
- **灵动岛去掉头标**：胶囊左侧强调色圆点与内环整块删除，`IslandConfig.show_icon`、
  `island_show_icon` 键、设置页「显示头标」勾选、`layout._ICON_PX/_ICON_GAP_PX/
  ICON_RING_COLORS/icon_ring_color()` 一并退役；内容（名字/信息位）从
  `CONTENT_PAD = 19px` 直接起排，胶囊自然宽度相应收窄。老配置里的该键**读入即弃**
  （同 `spawn_inherit_dynamic_island` 的退役先例，不写清洗代码）。
- **灵动岛消息显示开关**：新增 `island_show_messages`（默认 `False`）。
  `add_message()` 关闭时直接 return（不记录、不弹事件果冻）；`_card_rows()` 关闭时
  不出「最近消息」行；`_IslandCard.set_show_messages()` 同时收起「清除消息」按钮。
  设置落点在「外观与交互 → 灵动岛 → 消息显示」。

**验证**
- 先红后绿：`tests/test_petcore_icons.py::test_icons_stay_inside_the_canvas_at_every_device_pixel_ratio`
  在 1.25 dpr 就红（play 右侧 21/22），修 `_pixmap` 后 1.0/1.25/1.5/2.0 全绿。
- `QT_QPA_PLATFORM=offscreen pytest tests/test_petcore_*.py tests/test_architecture.py
  tests/test_pr_report_discipline.py -q` → **2122 passed in 154.49s**；
  全量 `pytest -q` → **10303 passed, 18 skipped in 1632.73s**。
- `ruff check petcore/ tests/` → All checks passed。
- 像素验收（真实平台插件，`.scratch/accept/*.png`）：菜单 1x/1.5x/2x 深浅两套，
  14 个顶层行全部有图标、字形四周留白、勾选标记只在勾选行行尾；
  侧栏 16px 图标 14 枚完整；灵动岛胶囊无头标、卡片 messages=0 无历史行且只有两个按钮、
  messages=1 出现两条历史 + 三个按钮。
- 性能（`.scratch/bench_icons.py`，同一进程 7 轮 × 50 次均值）：`icon()` ×14
  @dpr1.5 **0.4712 → 0.4455 ms**（−5%）；`model.build()` 8 行 0.4196 → **0.5754 ms**
  （勾选层 = 1 个子控件 + 几条信号，一次性）；`MenuCheckLayer.sync()` 0.0017 ms、
  `render()` 0.1456 ms（只在菜单开着时随重绘跑）；岛内容层重建 0.1417 ms（口径未变）。

**未验证/风险**
- offscreen（fusion）下带 QSS 的 QMenu **根本不画图标**（实测 `QMenu::item` padding 一加
  图标列就没墨），所以像素断言只能在 `icons.py` 的 pixmap 层做；菜单观感用真实平台
  插件截图验收（`QT_SCALE_FACTOR` 1/1.5/2）。
- 展开卡片三个按钮在深色卡片上仍是系统默认浅色底（既有观感问题，本轮未动）。
- 头标退役后老配置里的 `island_show_icon` 不再回写，但也不会被清理（Config 只保留
  `DEFAULTS` 键）。

## L102 — 2026-10-26 — 点击余额先冒气泡，点击台词排队到它收起

**范围**：`petcore/ui/pet_window.py`、`petcore/ui/bubble.py`、`petcore/ui/app.py`、
`petcore/builtin/features/fun.click-talk/host/factory.py`（只改设置文案）、
`petcore/ui/settings/page_interaction.py`、`tests/test_petcore_click_listeners.py`、
`tests/test_petcore_bubble.py`。

**改了什么**
- **根因**：`PetWindow._on_click()` 先 `_notify_click()`（同步回调 `fun.click-talk`，
  立刻冒一条 3.2s 台词）再 `_maybe_request_click_balance()`（异步，结果回来才冒余额）。
  余额气泡 6s 一到就把台词盖掉，观感就是「台词闪一下」。主分支是 `if click_show_balance
  … elif` 的二选一；本轮按用户要求改成**排队**（主分支没有这个顺序）。
- **气泡新增 `closed` 信号**：`SpeechBubble.hideEvent` 里「原本可见才发」——进 hideEvent 时
  Qt 已清 visible 标志，所以用 `showEvent` 记的 `_was_shown` 判定；自动到时、手动 hide、
  桌宠隐藏三条路都覆盖。
- **窗口层排队**：`_maybe_request_click_balance()` 改为返回 bool（配置开 + 宿主
  `request_balance` 可调用且提交成功才算「余额接管点击」）。接管时走
  `_begin_click_talk_wait()`（连点=重新计时、只留一条，`CLICK_TALK_FALLBACK_MS = 9000` 兜底），
  否则行为与以前一致立刻 `_notify_click()`。新增公开钩子
  `notify_click_balance_presented()`：气泡可见→等 `closed`；没有气泡（免打扰/隐藏）→ 立刻放行。
  缓存命中时余额气泡在 `request()` 里**同步**冒出（presented 早于排队建立），用
  `_click_balance_serial` 识别并直接进入「等它收起」。
- **宿主接线**：`CoreHost._on_balance_result()` 成功与失败两条分支都在 `_notify(...)`
  之后调 `window.notify_click_balance_presented()`（callable 探测，异常只记日志）。
- **文案**：余额提示行与点击台词页都写清「余额气泡先冒，台词排在它收起之后」。

**验证**
- 新增 6 例（先红后绿）：余额先冒/台词等收起、连点只排一条、缓存同步路径、9s 兜底、
  宿主无 `request_balance` 时立刻回落、`SpeechBubble.closed` 只在原本可见时发一次。
  修复前前四例必红（台词已经抢在余额前面）。
- 端到端实测（`.scratch/verify_click_balance_sequence.py`，真 `CoreHost` + `PetManager` +
  真实 click-talk runtime，`QTest.mouseClick` 点桌宠）：
  ```
  [ 0.17s] click requested=[(0, True)] talk_now=''
  [ 2.05s] bubble='余额 ¥3.00' visible=True
  [ 6.20s] bubble='余额看完啦，今天也要加油' visible=True
  [ 9.41s] bubble='' visible=False
  ```
  （第二行的时间戳是首轮 `processEvents` 解码动画阻塞后第一次采样；余额气泡实际在结果到达
  那一刻就冒出来了，6.2s 收起后立刻接台词，9.4s 台词到点收起。）
- `QT_QPA_PLATFORM=offscreen pytest tests/test_petcore_click_listeners.py
  tests/test_petcore_click_balance.py tests/test_petcore_bubble.py
  tests/test_petcore_self_talk.py tests/test_petcore_settings.py
  tests/test_petcore_click_sound.py -q` → **90 passed**；`ruff check petcore/ tests/` → 通过；
  全量 `pytest -q` → **10308 passed, 1 failed, 18 skipped**（2081s）。唯一失败是
  `tests/test_chat_subsystem.py::test_append_message_atomic_across_store_instances`：
  `pet/chat/session_store.py:514` 用 `str(updated_at)` 比较判断「吸收外部新消息」，
  Windows 的 `datetime.now()` 只有 ~15.6ms 粒度，同一 tick 内创建+追加就判不出更新；
  该文件本轮未改，单跑 10 次 0 失败、高负载下偶发（约 1/5–1/20），属既有 flake。

**未验证/风险**
- 兜底 9s 期间如果余额气泡超过 9s 还没收起（多页长文本），台词会覆盖它——余额文案很短，
  实际不会触发；这是「不永久憋住」与「绝不覆盖」之间的取舍。
- `_notify_click()` 是通用点击接缝：排队后**所有**点击监听者都晚于余额气泡，不只点击台词。
  目前只有 `fun.click-talk` 用它。

## L103 — 2026-10-26 — 灵动岛 × 桌宠碰撞回归 + C 盘剩余段

**范围**：新增 `petcore/engine/island_field.py`、`petcore/platform/diskmon.py`、
`petcore/ui/island_collision.py`；改 `petcore/ui/island.py`、`petcore/ui/pet_window.py`、
`petcore/ui/motion.py`、`petcore/ui/app.py`、`petcore/island/segments.py`、
`petcore/config.py`、`petcore/performance.py`、`petcore/ui/settings/pages_core.py`、
`petcore/AGENTS.md`；测试新增 `tests/test_petcore_island_field.py`、
`tests/test_petcore_island_collision.py`、`tests/test_petcore_disk_segment.py`，
改 `tests/test_petcore_island_window.py`。

**改了什么**
- **岛 × 桌宠碰撞（主分支的「撞岛被弹 / 拖岛拍飞」）**。主分支有两套实现
  （`pet/island_collision.py` 单窗同步硬墙、`pet/island_bridge.py` overlay 静态成员）；
  petcore 只有一套拓扑（每只桌宠一个顶层窗口），于是取两者之长：**墙机制照 legacy
  （同步钳制）+ 岛速采样照桥（闸门更完整）**。纯数学进 `engine/island_field.py`
  （零 Qt：胶囊体育场 `min(高,44)`、矩形径向半径、中心在轴上强制向上、冲量
  `-(1+e)vn`、`tracked_velocity`、`sample_island_velocity`），Qt 接线进
  `ui/island_collision.py`（`host.island_field`，事件驱动、零定时器）。
- **三个挂接点**：① `PetWindow._clamp_position` 末尾接 `field.clamp()`（拖拽/漫游/
  碰撞分离/缩放/换屏共用的位置出口，身体框永远进不了岛区——这就是不抽搐的根因）；
  ② `MotionController._tick_throw` 接 `field.reflect_throw()`（抛掷绕开位置出口，
  必须在物理步里反射，`e=physics.RESTITUTION=0.68`，且**落岛当落地**：法线朝上 +
  竖直速度 <120 时当帧 `stop()`，否则 is_at_rest 只认屏幕底边、motion 会永远卡在
  thrown）；③ 岛的几何回调（`moveEvent`/`resizeEvent`/动画落定/显隐都 emit）把被压住的
  桌宠经 `window._clamp_position()` 推出，真撞沿用同一条 `clamp` 业务链。
- **闸门**：不可见 / 停靠细条 / 几何动画中 → 无墙；探头宠豁免；拖拽中的桌宠只推出；
  抛掷中只推出（速度由物理步反射）。**碰撞开启时关掉 1Hz 自动避让**（两套语义同时开会
  把胶囊弹到屏幕角落；`avoid_overlaps()` 保留，关掉碰撞即恢复）。
- **撞岛反馈统一一个闸**：`IslandField.notify_hit()`（0.25s/只）覆盖挤压 + 音效 +
  岛果冻，非抛掷撞岛与抛掷撞墙共用；岛新增 `bump()`（果冻冲量 + 定向 kick 弹簧，
  kick 钳 ±3px、并进 `_animating()` 的停止判定）。
- **C 盘剩余段（默认关）**：`platform/diskmon.py` 零 Qt、5 秒一拍（钳 [5,60]）、
  只做一次 `shutil.disk_usage`（GetDiskFreeSpaceEx，无磁盘 IO）、读不到报 error 不给 0；
  `island.DISK` 进 `BUILTIN_SEGMENT_IDS` + `config.ISLAND_METRIC_IDS`（双白名单），
  胶囊上是单盒 `C 盘 412G`（盒样本 `888G`，单位不随读数跳），卡片详情「总 2.0T · 已用 1.6T」；
  设置页「指标段」加「C 盘剩余」复选；**不进告警表**（现表语义是「越高越危险」）。
- **新增设置键 1 个**：`island_collision`（全局、bool、默认 `True`），设置 →「灵动岛 →
  与桌宠碰撞」；关闭后胶囊不阻挡桌宠、岛恢复旧的自动让位。`settings/coverage.py` 不变
  （只登记菜单项）。

**验证**
- 新增 112 例（33 纯几何/采样 + 39 协调器/挂接点 + 40 磁盘段），先红的判据：纯几何模块
  不存在时 `tests/test_petcore_island_field.py` 直接 collection error；松手闸一条做过
  **变异验证**（把 `released=released` 改成 `released=False`，该例立刻红）。
- 行为序列 `.scratch/verify_island_collision.py`（真 QApplication + 真 `DynamicIsland` +
  真 `PetWindow` + 真 16ms motion 定时器），`QT_QPA_PLATFORM=windows` 与 `offscreen`
  两轮 **A1–A6 全 PASS**：
  - A1 拖宠进岛：被挡住的帧 = 78，进入重叠的帧数 = **0**，`thrown=False`，音效/挤压 0；
  - A2 岛 255–387px/s 扫向静止宠：第 1 步切 thrown，速度沿法线向外，音效/挤压各 1，
    岛 `_squish_v=0.32`、kick=(0, −1.4)；
  - A3 900px/s 抛向胶囊：反射帧 6、出射 629px/s（≤0.7 入射）、重叠帧 **0**、
    4.1s 回 `normal`；
  - A4 从墙外小速度落下：第 5 帧收手、`mode=normal`、收手后不重叠；
  - A5 `island_collision=False` 与「没有碰撞层」的位置出口序列逐像素一致；
  - A6 隐藏 / 细条 / 几何动画中三态墙均不生效（返回输入原值）。
- 性能实测 `.scratch/bench_island_collision.py`：`push_out` 命中 1.53µs、未命中
  0.84µs；`clamp` 命中 6.2µs、关闭/隐藏路径 0.75µs；`on_geometry_changed` × 4 宠
  18.6µs；**新增 QTimer 0、新增线程 0**；`shutil.disk_usage` 33µs/次，25s 内磁盘采样
  4 次、进程 CPU 0.000s。
- 视觉：`.scratch/verify_island_disk_visual.py`（**必须 `QT_QPA_PLATFORM=windows`**：
  offscreen 后端在本机渲染不出字形，全是缺字方框）产出
  `.scratch/accept/island-disk-{capsule,card}-{dark,light}.png` 与 `-real.png`；
  真实读数 `C 盘 118G`，卡片行 `C 盘 | 412G | 总 2.0T · 已用 1.6T`。
- 测试门：`QT_QPA_PLATFORM=offscreen pytest tests/test_petcore_*.py
  tests/test_architecture.py tests/test_pr_report_discipline.py -q` → **2247 passed**
  （208.7s，含本轮 112 例）；
  `ruff check pet/ tests/ petcore/` → 通过；`python -m petcore --check` → 退出码 0；
  全量 `QT_QPA_PLATFORM=offscreen pytest -q` → **10424 passed, 18 skipped**（1829s，
  0 失败；上一轮那条 `test_chat_subsystem::test_append_message_atomic_across_store_instances`
  时钟粒度 flake 本轮未复现）。

**复核修复（子代理只读复核后补的 6 条）**
- **P1 预钳目标被当真撞**：`_begin_move` 会先把 60–240px 外的**目标点**喂进位置出口，旧实现
  拿它算接近速度（`dt` 在 0.12–0.5s 之间时躲过 2000px/s 守卫），站着不动的桌宠会凭空切
  thrown 并与移动计划并存。新增 `clamp(..., settle=False)` / `_clamp_position(..., settle=False)`：
  **只算几何、不结算**，`_begin_move` 与 `_finish_move` 的目标钳改用这个入口。
- **落岛后悬空**：`rested` 收手后 normal 态没有重力，岛一被拖走桌宠就浮在半空。新增
  `IslandField._resting`（记停稳时的 y），岛几何变化且该宠已不在胶囊上时补一次自由落体
  （`set_velocity(0, GRAVITY*0.1)`）；用户自己挪走的不补。
- **动画期间节流表被清**：几何动画每帧都会走到「无墙」分支，旧 `reset()` 把冷却/反馈表一起清
  → 0.25s 单闸等于被 60 次/秒关掉。拆成 `reset_velocity()`（只清速度/基线/接触）与
  `reset()`（岛关闭/退出时才全清）。
- **宠-宠分离覆盖岛冲量**：`PetManager._apply_body` 在 `clamp` 已写过冲量之后又用分离速度
  `set_velocity` 覆盖；加 `not window.motion.thrown` 守卫。
- **岛速的"停手但没松手"幽灵**：`dt<0.01` 的密集上报里补一条「非拖拽且 rect 没动 → 清零」，
  否则快速拖岛停住后，旁边走进来的桌宠会被停手前的速度拍飞。
- **两处 5 列格式**：`9.96 GiB → "10.0G"`、`9.99 TiB → "10.0T"` 会撑破 `888G` 的段盒；两条 `.0f`
  分支的门改 9.95，T 档起点改 0.975T（`999G → "1.0T"`、`972G → "972G"` 仍 4 列）。
  另：`DiskMonitor.snapshot()` 保留最后一份样本，折叠→展开/隐藏→显示不再闪 5 秒 `--`。
- 顺手：`_prune_stale_keys()` 清掉已关桌宠的 `id()` 键（新窗口可能复用 id）；
  设置页补 `island_collision` / `disk` 两条用例。新增 13 例（预钳不结算、站位目标不弹飞、
  动画不清节流、`reset` 全清、落岛补重力、自己走开不补、陈旧键清理、分离不覆盖冲量）。

**实测补充（量出来才敢写）**
- 漫游实际步速（`.scratch/measure_move_speed.py`，shenshen 全部 MOVE 素材）：
  scale 0.5/0.72/1.0 下 **4.5–24 px/s**，远低于真撞门 80px/s —— 所以走路撞岛只会被
  挡住，不会被弹飞（这一条决定了不需要给「自主移动计划」加第二道闸）。

**未验证/风险**
- 岛从下方**跨过身体中线**继续推时，法线会从下翻到上，桌宠会被「甩」到另一侧（主分支
  同样的几何退化，没有扫掠解算）。实机手感是「拖到岛中间会被弹到上/下边」，不是穿模。
- 真实鼠标手感的最终确认需要用户实机拖一次；自动化只覆盖到「几何 + 速度 + 反馈计数」。
- 磁盘段读数按「系统盘（`%SystemDrive%`，默认 C:）」，不支持用户自选盘符；非 Windows
  退化为 `/`。
- `island_metrics` 的盘符概念是 `disk`，「C 盘剩余」是文案口径；将来要支持多盘需要新键。

## L104 — 2026-10-26 — 设置「扩展管理」两轴弹性（宽 / 高随窗口与字号，当前范围作下限）

**范围**：改 `petcore/ui/settings/page_extensions.py`、`petcore/ui/settings/base.py`、
`petcore/ui/settings/dialog.py`、`petcore/ui/settings/style.py`；测试新增
`tests/test_petcore_extensions_geometry.py`（12 例）。

**改了什么**
- **宽度轴**：左侧页面导航原来是「`NAV_MIN_WIDTH=190` 写死在 `_NavList.sizeHint()` +
  `Policy.Fixed`」，`NAV_MAX_WIDTH=260` 是死代码，长 mod 名只能省略号。现在
  `nav_width(widest_label_px, font_px) = clamp(最宽标签 + 35, 190, round(260×字号系数))`：
  **下限恒 190（当前范围作下限）**，上限随 `ui_font_size` 放大（18 字号 → 360），
  实际宽度由 `QFontMetrics(nav.font())` 量出来；标签变化（装载/卸载扩展）与样式
  polish 都会重算。
- **高度轴**：`page_extensions.py` 原来是 `stack.setMinimumHeight(420)` + 无 stretch
  插入 + `base.py` 尾部 `addStretch(1)` → 浏览器高度恒 420，1600×1000 窗口下方空 464px。
  改为容器 `stretch=1` 且把尾部 spacer 的 stretch 置 0：浏览器吃满整页剩余高度，
  **下限仍是 420**（实测把下限也按字号放大到 582 会让默认窗口多出一层外层滚动条，
  收益为负，故下限固定）。
- **内容宽**：`SettingsPage` 新增类属性 `content_max_width = 860`（默认不变），
  `ExtensionsPage` 置 `None` → 本页宽轴跟视口（1600 窗口下 860 → 1400），其它页仍按
  860 阅读宽度居中。
- **字号同源**：`style.nav_row_heights(font)` 同时供 QSS（`#settingsNav` /
  `#extensionsNav` 的 `min-height` 与 `font-size`）与 `dialog._load_pages` 的
  `QSize(0, …)` 使用：字号 13 → 34/32（逐像素等于改前），18 → 39/37。此前
  `ui_font_size` 只放大标签，导航文字不动，行高写死 34 会在放大字号时切字。
- **重排钩子**：`SettingsPage.reflow()`（默认空）+ `SettingsDialog.refresh_style()`
  在样式真的变了之后调用它——字号热切换不必重建对话框。

**验证**
- 先红：该文件第一版 11 例在改前 **10 红 1 绿**（纯函数与几何断言全红，唯一绿的是
  "其它页仍 860"——因为那时还没解除封顶）；改后全绿。关键判据是
  `720×500` 下 `nav.width()==190`、`stack.minimumHeight()>=420`、Mods 页
  `horizontalScrollBar().maximum() <= 110`（不回退）。
- 实测几何（offscreen，日志见 PR 报告）：900×620 时浏览器 484 高（改前 420、下方空 84）；
  1600×1000 时内容宽 1400（改前 860）、浏览器 864 高（改前 420、下方空 464）；
  900×620 + 字号 18 时外层纵向滚动为 0。
- 回归：`tests/test_petcore_settings.py`、`test_petcore_extensions_nav.py`、
  `test_petcore_market_ui.py`、`test_petcore_settings_close.py`、
  `test_petcore_p4_fixes.py` 共 81 例全绿。

## L105 — 2026-10-26 — 音乐识别重写：三源合成 + 状态机修复 + 歌词候选打分

**范围**：`examples/petcore-mods/media.music/`（新增 `audio_sessions.py`、
`window_source.py`、`title_clean.py`、`recognizer.py`、`sampling.py`、`diagnostics.py`；
重写 `runtime.py`；改 `now_playing.py`、`lyrics.py`、`players.py`、`settings_page.py`、
`mod.json` 1.1.0→1.2.0）；`petcore/config.py`（新键 `music_title_format` + 迁移别名）；
`scripts/music_probe.py`；测试新增 6 个文件、改 `tests/test_petcore_music_mod.py`。

**改了什么**
- **三源识别**（判定表见 `docs/PETCORE-MOD-FORMAT.md` §13.3）：SMTC 主源 +
  WASAPI 音频会话（逐进程 Active/静音/峰值）裁判"在不在出声" + 已登记播放器的窗口
  标题兜底（foobar2000/AIMP/Winamp）。原先只有 SMTC 一条源：不注册 SMTC 的播放器永远
  识别不到，播放器静音时又照唱。
- **修掉 8 个可复现缺陷**：①单拍采样失败就 `_forget_track()` → 现在区分"读失败"与
  "没在放"，缺席只冻结时钟、不换歌（只有换歌才替换歌词）；②唱歌重试 4 次后永久放弃 →
  2^n 拍退避、永不放弃；③取词一次失败拉黑整首 → 60 秒冷却、每曲最多 3 次、首次失败
  说清原因；④切宠后循环残留 → 逐 slot lease/release（`presentation.for_slot`）；
  ⑤无采样令牌 → 每拍一个，迟到结果丢弃；⑥纯音乐先唱 1–8 秒 → 曲目开始先观察一拍；
  ⑦`say` 失败仍推进行号 → 不推进（同一行仍只冒一次）；⑧`_post` 抛错时 `_inflight`
  永久为 True → 复位。
- **歌词质量**：取词前用移植来的 `title_clean.clean_web_title` 清洗查询词（只用于搜索，
  不改显示）；候选按「歌名 1.0 / 歌手 1.0 / 专辑 0.4 / 时长 1.0」加权、缺失字段重归一、
  阈值 0.55，取分最高者而不是"第一个歌手名命中的"——这是"认错歌"的根因（翻唱同名曲
  的歌手字段里也常带原唱名）；新增 lrclib 源（海外曲目 + 显式 instrumental 标记）。
- **诊断**：设置页「识别诊断」卡（按钮 + 只读多行文本 + 复制）与 `scripts/music_probe.py`
  共用 `host/diagnostics.py`；文本只有 app_id / 可执行名 / 曲名 / 状态与数值，无 URL、
  路径、代理与歌词正文。
- **新设置键 1 个**：`music_title_format`（全局，默认 `""`），旧壳 `foobar_title_format`
  按别名迁移。音频会话与窗口标题源**不设开关**（可由应用推断的实现参数），不可用自动降级。

**验证**
- 测试：新增 6 个文件共 **102 例**（判定表 22 / 探针 22 / 标题真值 28 / 取词质量 14 /
  状态机 11 / 诊断 5），改 `test_petcore_music_mod.py` 后 38 例；
  与主分支 `pet/music_title.py` 做过**差分验证**（17 条真值 × 3 种 artist + 4000 组随机
  输入，diffs=0）。
- 实机（本机 Windows 11，2026-10-26，`.scratch/verify_music_runtime.py`，只读）：
  ```text
  A. 真实三源（网易云正在播）
  SMTC：1 个会话  cloudmusic.exe MUSIC(1) PLAYING
  音频会话：2 个（Active 1）  cloudmusic.exe active mute=no peak=1.00
  判定：在播 · 来源=系统媒体会话 · 曲目=《开不了口》—周杰伦 · 置信度=有音频证据
  运行时 4 拍：status=playing source=smtc track=周杰伦 - 开不了口 singing=True；事件 [('anim','悠闲哼歌',True)]
  B. 窗口标题兜底（SMTC 置空，窗口枚举与音频会话都是真的）
  播放器窗口标题：1 个  cloudmusic.exe 「开不了口 - 周杰伦」
  判定：在播 · 来源=播放器窗口标题 · 置信度=有音频证据
  运行时 4 拍：status=playing source=window track=开不了口 - 周杰伦 singing=True
  ```
  这轮验收抓到一个真实缺陷并已修：窗口源在无格式串时把整条标题同时当歌名与歌手，
  气泡显示成《开不了口 - 周杰伦》—开不了口 - 周杰伦。
- 开销（本机实测，每拍，200/100 次取中位与 p95）：SMTC 3.08ms、音频会话 median 1.73
  / p95 2.48 / max 3.27ms（缓存 endpoint manager；不缓存是 24ms）、窗口枚举 median 0.57
  / p95 1.34ms（仅 SMTC 无在播会话时）。生产走后台线程，GUI 线程每拍只做判定。
- **全量门禁**：`1 failed, 10558 passed, 18 skipped in 1537.91s`。唯一失败是主分支
  chat store 的既有时序 flake（`tests/test_chat_subsystem.py::test_append_message_atomic_across_store_instances`）：
  本批未触碰 `pet/chat`，在 `git worktree` 的干净 HEAD 上同样 6 次里 1 次失败，
  已登记进 PR 报告 §3.6，不在本批顺手改。
- **独立审查（只读子代理 + 变异测试）后修复的 9 条**：①`control_playback` 写成类属性
  被绑成方法，四个播放命令 100% 失败（改成实例属性 + 后台线程执行，补 2 例并做变异验证）；
  ②SMTC 会话暂停时窗口源白采不采信 → 「在播」证据优先（补 3 例）；③`exe_name` 没取
  basename（路径形状 app_id 让静音证据失配、泄漏安装路径）；④删掉没人用的
  `MusicRuntime.diagnose()`；⑤删掉 `lyrics._get_json` 的重复定义；⑥设置页一次性线程补
  `close_thread_loop()/audio_sessions.close()` 收尾；⑦"认不出曲目"按播放器说明一次；
  ⑧取词在飞时最多再等 3 拍再唱（纯音乐少唱 2 秒，普通曲目不受影响）；⑨端口纪律测试
  从 2 个模块扩到 10 个（变异验证会红）。

**未验证 / 风险**
- 蓝牙/独占输出（音频会话不可见 → 按 SMTC 判）、浏览器视频（开关关闭时整体否决）、
  多宠同时唱歌的真机观感，均只有单测/静态论证，未实机复现。
- 标题启发式「视频站点特征否决」**刻意没做**：B 站的 MV 标题同样带站点后缀，按标题否决
  会把正经音乐视频也挡掉，代价大于收益；仍由"浏览器会话开关默认关闭"兜住。
- Windows 以外的平台：`audio_sessions`/`window_source` 返回空，识别退化为 SMTC。

## L106 — 2026-10-27 — 语音全链路 + champion 彩蛋 + 关于页致谢/签名

**主题**：petcore 的语音真正可用（自语/点击/彩蛋出声、本地部署模型、语音页），关于页补上
完整的致谢体系（作者、贡献者、特别鸣谢、issue/PR 协作者、B 站粉丝、签名），并把「自言自语」
做成会连说 5 句 champion 台词的彩蛋。

**实现（新增文件）**
- `petcore/community/{__init__,model,store,service}.py`（+71/+352/+191/+338）：致谢数据模型、
  随包快照/运行期缓存、刷新策略（GitHub 贡献者 + issue/PR 作者；B 站免登录总数 + SESSDATA
  全量；失败/半截不覆盖）、B 站凭据与签名。
- `petcore/net/{github,bilibili}.py`（+138/+203）：唯一联网出口；限流/风控/截断分类；
  Cookie 只走请求头。
- `petcore/ui/settings/{page_voice,folds,signature_pad,credits_job,page_about_thanks}.py`
  （+722/+58/+322/+91/+579）：语音页、折叠区、手写签名、守护线程刷新、六区致谢面板。
- `scripts/fetch_community_credits.py`（+173）：生成 `petcore/community/data` 快照；partial 拒绝写入；
  Cookie 只从环境变量/文件读。
- `petcore/community/data/credits.json`（+745，17 贡献者/124 协作者/4 作者账号/4 特别鸣谢）、
  `petcore/community/data/fans.json`（+3955 / 86KB，3948 位粉丝，vmid 293026389，
  `fetched_at=2026-10-27T08:43:44+08:00`）。
- 测试：`test_petcore_speech_local.py`（+350）、`settings_voice`（+331）、`say_sequence`
  （+185）、`community_store`（+176）、`community_service`（+298）、`net_credits`（+198）、
  `about_thanks`（+349）、`credits_job`（+80）。

**实现（修改）**
- `petcore/net/tts.py`（+179/−0）：`local` provider、`local_synthesize`（OpenAI 兼容 /
  通用 JSON；`prefer_direct=True`、`http/https`、base64 响应）。
- `petcore/speech/service.py`（+91/−6）：local 可用性（endpoint 为空即未配置）、音色、后缀、
  flavor 含 endpoint/protocol/path/model/format；`optional_secrets` 允许本地 Key 留空。
- `petcore/config.py`（+108/−5）：7 个 `speech_local_*` + 4 个自语/点击语音与彩蛋键；归一化；
  **不再 import `urllib.parse`**（架构门抓到后改手写前缀校验）。
- `petcore/ui/settings/dialog.py`（+44/−5）、`page_about.py`（+80/−6）、`base.py`（+69/−16，
  `reveal_text` 展开折叠区）、`style.py`（+56/−2）、`icons.py`（+64/−6，`voice` 字形）。
- `petcore/contracts/host.py`（+9/−1）、`ui/app.py`（+103/−1）、`ui/pet_window.py`（+189/−16）、
  `ui/bubble.py`（+25/−0）：`say_sequence` 端口/适配器/定时器链与取消点、`full_text()`。
- `fun.self-talk`（+175/−7，mod 1.1.0）、`fun.click-talk`（+67/−3，mod 1.1.0）、
  `tool.links`（+1/−1，夸克分享地址）、`dsh-pet-core.spec` 与
  `scripts/build_petcore.ps1`（datas/--add-data 增加 `petcore/community/data`）。
- 文档：SPEC/ARCHITECTURE/MAIN-PARITY/SETTINGS-CHANGE-GATES/CONTEXT/README/AGENTS；
  测试：`test_petcore_settings.py`、`test_petcore_about_page.py`、`test_petcore_self_talk.py`。

**行为**
- 自语/点击台词默认朗读（各自开关）；彩蛋默认 8%，命中时 `say_sequence` 连冒 5 条
  （英文主文案 + 中文副标题），**每出现一条才朗读该条**（`on_show`），不在开头就把 5 个
  合成任务全塞进队列；任何新 `say/show_image/隐藏/关闭` 取消剩余；隐藏态不朗读。
- 语音页统一 `speech_*` 与本地模型；`SpeechRequest.rate/volume` 留空时用 `speech_rate`/
  `speech_volume`（页面滑条对自语/点击/报时都生效）；SAPI 独立 `speech_sapi_voice`；
  本地服务未配置/未安装时回退 edge→sapi；服务已配置但连接失败时状态行给稳定原因，不自动换云端。缓存键包含本地服务身份。
- 本地 TTS 用 `direct_only=True`（失败也不切系统代理）；endpoint 只收 http(s) 且拒绝
  `?`/`#`。
- 关于页始终有「版本与诊断 / 致谢」两个分页；六个折叠区；粉丝只渲染当前 60 条，
  搜索/翻页不联网；打开关于页 10 分钟冷却、`credits` 6h/`fans` 24h 才后台刷新；
  失败保留旧名单，半截名单不落盘；SESSDATA 只进钥匙串（保存时校验、只存值），
  界面/日志/诊断不回显。
- 签名固定 `<data_root>/community/signature.png` + `signature.json`，原子覆盖，不参与设置回滚。
- 夸克网盘入口改为 `https://pan.quark.cn/s/68fc681ae486`。

**独立审查（只读子代理）后修复**
- P1：隐藏态下自语/彩蛋仍出声 → `_speak`/`_play_champion` 按 `say` 结果与 `visible()` 闸门；
  补「隐藏不出声」两例。
- P1：语音页语速/音量滑条没人读 → `SpeechRequest.rate/volume` 默认 None，服务端用
  `speech_rate/volume` 填充；补一例断言合成器收到 1.4/0.6。
- P2：彩蛋开头排队 5 条语音 → 改为 `say_sequence(on_show=...)` 逐条朗读；测试同步改为
  按条触发。
- P2：保存后 `dirty()` 不消（音色被截断 64 字）→ `confirm_save` 同步音色控件；补一例。
- P2：`_on_job_done/_on_job_failed` 先碰控件后判 `_disposed` → 守卫提前；`_clear_layout`
  先 `setParent(None)`。
- P2：刷新线程与 Qt 退出无协调 → 新增 `cancel_all()`（守护线程 + 0.5s join），在
  `quit_app`/`aboutToQuit` 调用；`_report` 在无 QApplication 时不再 emit；`_thread` 在
  finally 清空。
- P3：SAPI 与 Edge 共用音色键 → 新增 `speech_sapi_voice`；`sessdata_value` 拒绝不含
  SESSDATA 的整段 Cookie；`fetch_url(direct_only=True)` 杜绝本地请求走代理；
  endpoint 拒绝 `?`/`#`；`reveal_text` 只展开选中命中并延后一拍滚动；刷新只在结束时
  reload 一次 memo；credits 6h TTL 真正生效（避免每次开关于页都打 5 次 GitHub）。

**验证**
- `.venv/Scripts/ruff check pet/ tests/ petcore/` → All checks passed。
- 聚焦（审查修复后）：语音/自语/时序/社区/致谢/架构共 131 passed；`credits_job` 2 passed。
- 架构门：`test_petcore_architecture.py` 通过（曾抓到 `config.py -> urllib.parse`，已修）。
- 全量 petcore：`2148 passed in 183.37s (0:03:03)`（审查修复后重跑）；粉丝快照批次
  （含 `Fans.total` 修复与 3 个新用例）后重跑为 `2154 passed in 281.43s (0:04:41)`；
  第二轮审查修复后终跑 **`2160 passed in 278.84s (0:04:38)`**。
- 全仓：`.venv/Scripts/python -m pytest -q` → `10652 passed, 19 skipped in 1375.53s (0:22:55)`。
- `python -m petcore --check`：输出完整 JSON，`strict.problems=[]`。
- 性能（Windows 11 26200 / Python 3.12.10 / PySide6 6.12.0，`.scratch/bench_thanks.py`）：
  关于页构造 median 12.09ms / p95 17.20ms；致谢面板 10.70/15.78ms；粉丝翻页
  3.80/6.41ms；搜索 0.65/1.11ms；内存增量 0.26MB。
- 同一基准换成**随包真实快照**（3948 粉丝 / 17 贡献者 / 124 协作者，`.scratch/bench_thanks_real.py`，
  每项 200/1000 次）：关于页构造 median 24.62ms / p95 42.43ms；致谢面板 12.91/21.27ms；
  66 页里随机翻页 5.99/8.93ms；3948 条搜索 0.78/1.20ms；面板内存增量 0.26MB。
  真实名字比合成名长，所以构造与翻页比合成数据略高，仍在目标线内。
- 真实数据渲染（windows 平台，`.scratch/shots/thanks_real_*.png`）：第 1/66 页 60 个名字
  4 列排布正常；搜「鱼」筛出 90 位、分 2 页；第 66 页正常；SESSDATA 行与签名区无重叠。
- 实机：`python -m petcore --root .scratch/gui-app-root` 启动日志正常；真实平台渲染
  `.scratch/about_thanks_full.png` / `voice.png` 目视通过（折叠头左对齐、卡片/按钮正常）。

**B 站粉丝快照（2026-10-27 补抓）**
- 用户装好 Kimi WebBridge 扩展后，用其登录态在 `space.bilibili.com/293026389/relation/fans`
  页面内同源 `fetch` 抓 `x/relation/fans`：79 页 × 50，**0 报错、0 重试**，共 3950 条记录；
  3 条「账号已注销」占位名按 `normalize_fan_name` 去重后为 **3948** 个唯一用户名，
  与接口返回的 `data.total=3950` 对得上（差 2 即这两条重复占位）。
- 落盘 `petcore/community/data/fans.json`（`{schema:1, vmid, fetched_at, total, names}`，与
  `scripts/fetch_community_credits.py --fans` 写出的形状一致），UTF-8 无 BOM，86KB。
- **真数据一到就暴露的缺陷（已修）**：「有没有新粉丝」原本拿 `len(去重后名字)` 和 B 站
  `total` 比，而快照天然差 2（3 条「账号已注销」→ 1 条），于是配了 SESSDATA 的用户每次
  打开关于页都会判定「名单变了」并重拉 79 页。改为把 B 站口径的 `total` 一起持久化：
  `Fans.total`（`parse_fans` 读、`save_cached_fans` 写、`fans.json` 记 3950），
  新增 `CommunityService.known_total()` 供 `refresh` 比较；`fans_changed` 从比长度改为
  比名字元组（长度相同但内容变了也算更新）。回归用例
  `test_refresh_skips_full_fans_when_duplicate_names_collapse`、
  `test_full_pull_records_bilibili_total_so_the_next_check_is_cheap`。
- 新增验收测试 `tests/test_petcore_community_bundle.py`（+4）：随包 `credits.json`/`fans.json`
  必须存在、可解析、`vmid` 正确、名字非空/无重复/不超 64 字/过得了 normalize、
  `total ≥ 名字数且 >0`、`fetched_at` 可解析、上游项目与三位画师在特别鸣谢里、
  文件 UTF-8 无 BOM 且 ≤2MB。以后谁把快照退回空占位，这条测试会红。

**第二轮审查（真数据到位后，只读子代理）后修复**
- P2：比较用的数来自 `x/relation/stat`（`follower`），持久化的却是 `x/relation/fans`
  的 `total`，两个接口一旦差 1，「拉完还是不等」会永久循环 → `_refresh_fans` 增加
  `stat_total` 参数，优先记录 stat 的数（名单接口的 total 只作兜底）。
- P2：名单接口缺 `total` 时 `total or 0` 会被 `parse_fans` 夹到名字数，等于把上面那个
  P1 原样恢复 → 同上，改用 stat 的数；`parse_fans` 的夹取只用于防御坏数据。
- P2：`fetch_fan_count` 在 `data` 缺 `follower` 时静默返回 `0`（假 0）→ 改为必填校验，
  缺失/非数字一律 `NetworkError(reason=protocol)`；补 3 例参数化测试。
- P2：`scripts/fetch_community_credits.py --fans` 不去重，按文档重跑会写出 3950 条，
  `tests/test_petcore_community_bundle.py` 立刻红且报错指向「normalize」→ 脚本改为
  与 `parse_fans` 同样的保序去重；测试报错文案拆开。
- P3：`fans_changed` 用元组比较（页序抖动也算变了）→ 改为集合比较；`fan_total()`
  这个返回名字数的旧方法删掉，只留 `known_total()`；`_refresh_fans` 不再可能报 `fan_total=0`。
- P3（测试加严）：`total-len(names)` 落在 0..20、`fetched_at` 不在未来一年外、每个名字
  `normalize_fan_name(name) == name`、`dsh-pet-core.spec` 与 `scripts/build_petcore.ps1`
  都带 `petcore/community/data`（打包漏了源码树测试不会红）；新增「老缓存无 total →
  一次全量自愈 → 之后不再全量」与「stat 口径优先于名单 total」两例。
- P3（文案）：关于页粉丝数补「· B 站显示 3950 位（含同名占位）」，状态行同步，
  避免同一页出现两个说不清的数字。

**未验证 / 风险**
- **粉丝快照是一个时间点**：`petcore/community/data/fans.json` 抓于 2026-10-27 08:43，
  之后新关注的粉丝要等关于页「刷新名单」（需 SESSDATA）或重跑
  `scripts/fetch_community_credits.py --fans` 才会出现；抓取本身需要登录态，
  因此在 CI 里不可复现（本次是通过用户浏览器登录态抓的）。
- 本地 TTS 只在注入传输上验证请求形状/缓存/回退，**真实本地服务发声未验证**。
- 全仓 `pytest -q` 已通过（10652 passed / 19 skipped）；跑完后仅追加了「无效本地地址保存时给出状态」
  这一条 UI 文案改动，已由聚焦用例覆盖。
- 非 Windows 平台、真实 B 站刷新在 CI 中不可复现（需要登录态）。

## L107 — 2026-10-27 — Smart App Control 拦截 setup/卸载器：安装时签 unins000.exe

**主题**：用户报「之前装的桌宠 setup 和 uninstall 都被微软的应用安全控制拦」。定性为本机
Smart App Control（`VerifiedAndReputablePolicyState=1`）拦下 CodeIntegrity 3077/3118，
被拦对象是安装时才生成、从未签名的 `unins000.exe`；Defender 0 检出。修复是给安装器加
「安装时签卸载器」，不关闭任何安全策略。

**实现**
- `packaging/petcore.iss`（+10）：`#ifdef SignUninstaller` → `SignTool=devsign` +
  `SignedUninstaller=yes`。
- `scripts/build_petcore.ps1`（+18/−2）：带 `-SignThumbprint` 时给 ISCC 传 `/Sdevsign=…`
  与 `/DSignUninstaller=1`；不带时与原流程一致。
- `scripts/sign_artifact.ps1`（+49，新）：单文件 Authenticode 签名（SHA256 + 时间戳，
  时间戳失败退回无时间戳），充 Inno 的 SignTool（本机无 Windows SDK 的 signtool.exe）。
- `scripts/install_dev_cert.ps1`（+74，新）：把公钥 `.cer` 装进 `CurrentUser\Root` +
  `TrustedPublisher`，`-Check` 只校验；拒绝含私钥的证书文件。
- `tests/test_petcore_sac_signing.py`（+76，新，4 例）：锁定 iss 的开关块、构建脚本的
  `/Sdevsign=`/`/DSignUninstaller=1`、签名与装证书工具存在且不发私钥、以及
  「先签名 exe 再做冒烟」的顺序。

**证据（本机实测）**
- 定向：`dsh-pet-core.exe`（`CN=DSH Pet Core Dev`）在 SAC 强制下跑 `--check` 退出 0，
  3077 增量 0——说明本机开发证书能过 SAC，问题只在未签的卸载器。
- 端到端（`.scratch/verify_sac_install.ps1`）：静默装到 `.scratch/sac-install` →
  `dsh-pet-core.exe`/`unins000.exe` 均 `Valid / CN=DSH Pet Core Dev` → 已安装 exe
  `--check` exit 0 → 静默卸载 exit 0、目录删除 → **3077 新拦截 0**。
- 产物：`dist-sac-2026-10-27/dsh-pet-core-setup.exe`（285MB，已签）、
  `dsh-pet-core-dev.cer`（公钥，换机器测试时装它）。
- 回归门：`tests/test_petcore_candidate_packaging.py` + `test_packaging_keyring_notices.py`
  26 passed；`tests/test_petcore_sac_signing.py` 4 passed；`ruff` 全绿。

**未做（有意）**
- 不关闭 Smart App Control（关了只能重置系统才能开回来），也不给 SAC 加「例外」——它不支持。
- 自签证书只覆盖本机测试；对外分发仍需 CA 证书（EV 有即时信誉），文档
  `docs/ONEDIR_PACKAGING.md`「Smart App Control 拦截 setup / 卸载器（2026-10-27）」写了边界。
- 未在第二台机器上复验「装证书后放行」，只在本机与脚本 `-Check` 上验证。

## L108 — 2026-10-27 — AI 三项改为扩展注册项 + 气泡层级跟随桌宠

**主题**：用户报「全新安装、什么都没勾，右键菜单却出现 AI 对话/看看屏幕/截图提问，
加装扩展里还列出 4 个没装过的扩展」。查证后是两件事：菜单三项是 core 写死的特例
（没扩展也在），扩展列表是用户数据目录里的旧副本（不是 setup 装的）。顺带修掉「气泡压住
右键菜单」。

**根因（有证据）**
- AI 三项在 `menu_actions.core_items()` 里是 `owner="core"`，默认布局
  （`menu_templates/petcore-default-v1.json` 与 `menu_layout.BUILTIN_DEFAULT_LAYOUT`）
  用裸 id `ai_chat/look_screen/screen_capture` 摆位；点下去才 `run_feature_command("ai.chat", …)`。
  所以没装 ai.chat 也在。扩展的常规通道是 `Contribution(kind="menu")` → 注册成
  `<owner>:<id>`，没装就不注册——三项没走这条路。
- 那 4 个扩展在 `%APPDATA%\dsh-pet-core\mods`（2026-10-21 09:07，voice.chime 1.0.0，
  随包已 1.1.1），当天日志有
  `duplicate mod id 'voice.chime': …AppData… overridden by E:\dsh-pet-core\mods\voice.chime`
  ——旧安装目录与用户目录两份并存，安装目录那份随 `E:\dsh-pet-core` 一起删了。安装包本身
  每条 mod 的 `Source` 都带 `Tasks: mod_*`、任务全部 `Flags: unchecked`。
- 气泡是**无父窗口的顶层** `Qt.Tool`：非置顶时它自己 `raise_()` 会排到所有普通窗口之上，
  于是压住其它应用与右键菜单。置顶标志本来就跟着 `always_on_top` 走，问题在「独立顶层窗口」。

**实现**
- `examples/petcore-mods/ai.chat/host/__init__.py`（+30）：新增 `open` / `look_screen` /
  `screen_capture` 三条菜单 contribution（label/icon/order 与旧 core 项一致）。
- `petcore/ui/menu_actions.py`（−22）：删掉三个 core `MenuItem`；`run_ai_command` 保留给热键。
- `petcore/ui/menu_layout.py`（+42/−6）：`BUILTIN_DEFAULT_LAYOUT` 改用 `ai.chat:*` id；
  新增 `migrate_p11_menu()`（节点 id 改写，幂等）与 `LEGACY_ID_MAP` 三条映射（老 hidden/order 仍生效）。
- `petcore/menu_templates/petcore-default-v1.json`（3 处 id）。
- `petcore/ui/app.py`（+30/−6）：`_register_feature_menus` 的 callback 带 `_menu_slot()`；
  新增 `active_topmost()` / `show_context_menu()`（扩展不许 import `petcore.ui`，走 host 鸭子类型）；
  `reload_menu` 接上 `migrate_p11_menu`。
- `petcore/ui/pet_window.py`（+8/−4）：`SpeechBubble(self)` —— 气泡成为桌宠窗口的从属窗口；
  右键菜单改走 `show_context_menu(..., topmost=self.always_on_top)`。
- `petcore/ui/menu.py`（+41）：`prepare_menu()` / `show_context_menu()` 统一层级规则。
- `petcore/ui/settings/page_mods.py`（+27）：详情新增「来源：安装目录 / 用户数据目录 / 源码树」
  （判据复用 `trust.is_trusted`，与「授权」同源）。
- `petcore/ui/settings/coverage.py`（−3）：三项不再是 core 菜单项，移出覆盖表。
- `examples/.../tool.quick-launch/host/factory.py`（+8/−1）：快捷启动菜单也走 host 的同层入口。

**测试**
- 新增 `tests/test_petcore_ai_menu_registration.py`（4 例）：没注册 → 菜单无三项；注册后按原位
  回来且不重复；`migrate_p11_menu` 保留 hidden 记忆；老 `hidden=["ai_chat"]` 仍生效。
- 新增 `tests/test_petcore_context_menu_layer.py`（3 例）：置顶时菜单带
  `WindowStaysOnTopHint`、非置顶不带、`build_menu` 返回 None 不炸。
- 新增 `tests/test_petcore_installer_mods_opt_in.py`（5 例）：`{app}\mods` 的 Source 必须带
  `Tasks: mod_*`；每个 `mod_*` 必须 `unchecked`；构建脚本不把 examples 打进包；卸载只删
  `{app}\mods`；每个示例 mod 有安装入口。
- 扩充：`test_petcore_bubble.py`（气泡的 parent 就是桌宠窗口）、`test_petcore_feature_command_slot.py`
  （菜单命令带打开菜单那只桌宠的 slot）、`test_petcore_extensions_nav.py`（来源三态）、
  `test_petcore_ai_mod.py` / `test_petcore_menu_layout.py` / `test_petcore_slingshot_menu.py` 同步新 id 与
  `MenuStub` 的层级接口。

**实机验证**（`.scratch/verify_l108_menu.py`，真实 CoreHost + Config + paths.mods_dirs）
- 干净 root：菜单 = 彩蛋图片/切换角色/播放动画/桌宠控制/工具与帮助/免打扰/扩展/桌宠设置/退出
  —— **没有 AI 三项**，`load_result.mods` 为空；截图 `.scratch/shots/l108_menu_clean.png`。
- 装上 `examples/petcore-mods/ai.chat`：菜单 = …播放动画/**AI 对话/看看屏幕/主动识屏/截图提问**/桌宠控制…
  位置与旧版一致、不重复；截图 `.scratch/shots/l108_menu_with_chat.png`。
- 用户机器上那 4 个旧副本已删除（`%APPDATA%\dsh-pet-core\mods` 现为空）。

**未验证 / 风险**
- 「气泡不再压住菜单」只做到 **层级根治（从属窗口）+ 菜单同层**：自动化里无法制造
  「两个置顶窗口互相覆盖」的真实合成，只能靠单测（parent/flag）与实机观察。实机请重点看两条：
  桌宠取消置顶后气泡是否跟着退；右键时菜单是否总在气泡之上。
- 老用户布局迁移只覆盖 `menu_layout` 的节点 id 与 legacy hidden/order；若有人手改过
  `menu_templates` 之外的私有布局文件（不存在此路径），不在覆盖范围。

**门禁与产物（L108 收尾）**
- `ruff check pet/ tests/ petcore/ examples/` → All checks passed。
- 全量 petcore：**2180 passed in 248.09s**（L107 时 2164，+16 例：AI 注册 4、菜单层级 3、
  安装器 opt-in 5、来源 1、slot 2、气泡从属 1）。
- 文档门：`tests/test_petcore_docs.py tests/test_pr_report_discipline.py` → 317 passed。
- `python -m petcore --check` → exit 0，`strict.problems=[]`。
- 重新出包：`dist-sac-2026-10-27b/dsh-pet-core-setup.exe`（已签名 + 时间戳，附 `dsh-pet-core-dev.cer`）。
  端到端 SAC 验收：installer exit 0 → `dsh-pet-core.exe` / `unins000.exe` 均
  `Valid / CN=DSH Pet Core Dev` → 已安装 exe `--check` exit 0 → 静默卸载 exit 0、目录删除 →
  **3077 新拦截 0**。

## L109 — 2026-10-27 — 新增《petcore 扩展制作指南》（动手向）

**主题**：用户要「各种 mod 的制作/开发方法、素材包放哪、功能型 mod 怎么做怎么接」。
现有 `PETCORE-MOD-FORMAT.md` 是字段级参考（632 行）、`examples/README.md` 是样板清单，
缺一份从零到能装进设置页的动手指南，尤其缺「东西放哪个目录」和「哪些声明其实没接线」。

**实现（纯文档）**
- 新增 `docs/PETCORE-MOD-DEVELOPMENT.md`（约 500 行）：
  1. 三类产物（feature / 角色包 / 内容块）与「内置 feature 不算扩展」的边界；
  2. **放哪**：仓库开发树 vs `<data_root>/mods` vs `<install>/mods` vs `--mods-dir`，
     以及 `repo/mods` 与 `--mods-dir` 里 feature **永远 disabled** 的判据；
  3. 最小可运行骨架（`mod.json` + `host/{__init__,factory,runtime,settings_page}.py`），
     每个文件给完整代码与职责；
  4. 接线点：`menu` / `settings_page` 的 `data` 键真实效力、命令面是 `runtime.commands`
     （不是 `Contribution(kind="command")`）、`slot` 语义、端口选型表；
  5. 线程铁律（只有 `scheduler` 跨线程安全；`http`/`synthesize` 阻塞；`on_ask` 在后台线程）；
  6. 配置与数据（第三方键落 `extra` 的限额、`storage.data_dir` 原子写）；
  7. 本地闭环（`mods validate/install/list/doctor/remove`、重载扩展、日志位置、两个已知坑）；
  8. 打包与分发（单层 wrapper、ZIP 安全扫描、`install.json`、`.trusted` 只在安装目录、
     无签名无校验）；
  9. 角色包：两种来源、`manifest.json` 字段、15 类动画目录映射、`body_box` 的几何意义、
     三条导入入口、`pack-character` 的真实行为与**不支持项**；
  10. 内容块 `sounds/themes/phrases/seasonal` 当前无消费方（别照抄）；
  11. 红线与门禁表、排错对照表（错误原文→原因→修法）、提交 checklist。
- 互链：`docs/INDEX.md` 登记新行并同步 `PETCORE-MOD-FORMAT` / `PETCORE-CHARACTER-PACKS` /
  `examples/petcore-mods/README.md` 的「何时必读」；上述三份 + `ASSET-PIPELINE.md` 正文首/尾
  各加一条指向新指南的说明。

**验证（不是"写了就算"）**
- `.scratch/verify_mod_guide.py` 直接**解析文档**里的 5 个代码块生成 `hello.timer`，然后：
  `mods validate → ok`；`mods install → installed hello.timer 1.0.0`；
  真实 `CoreHost` 加载 → `loaded features` 含 `hello.timer`；右键菜单 →
  `扩展 → 整点打招呼 → 打个招呼`；`runtime.commands == ['greet']`；设置导航出现「整点打招呼」。
  文档里写的骨架因此是**可执行的最小实现**，不是示意。
- 顺带纠正了三处容易抄错的 API：`ports.pets` 没有 `pet(slot)`（只有 `pets()/primary()/active()`）、
  `value_field()` 是只读展示（可编辑要用 `QLineEdit`）、菜单项默认进「扩展 → <manifest 名>」子菜单。

**未覆盖 / 风险**
- 这是文档变更，不改运行行为；`tests/test_petcore_docs.py` 与索引互链规则已跑过。
- 指南里标注了三处「代码与文档/注解不一致」：`settings_factory` 实收 host 而非 `FeatureContext`、
  10 个 contribution kind 只有 2 个被消费、`execution: worker` 是死路。**只记录，不在这批修**。

## L110 — 2026-10-27 — 新增 `tool.ai-browser`：只开国模 AI 站的轻量浏览器 + 接口文档探针

**主题**：用户要求「按 mod 开发文档做一个只开 DeepSeek/豆包/千问/Kimi 等站点的轻量浏览器扩展，
打包 ZIP 导入，用来验证接口文档是否真的可行」。

**实现（新扩展，未改 core）**
- 新增 `examples/petcore-mods/tool.ai-browser/` 1.0.0（9 个文件、约 1073 行）：
  `mod.json`、`host/{__init__,factory,sites,engine,external,runtime,browser_window,settings_page}.py`。
  - `sites.py`：8 站站点表（含实测重定向的 `qianwen.com`、`baidu.com`）+ 关键词/URL 解析 +
    后缀边界白名单（纯函数，零 Qt/零网络）。
  - `browser_window.py`：`QMainWindow` + `QWebEngineView` + 工具栏/站点按钮/拦截横幅；
    `acceptNavigationRequest` 只放行主框架白名单、`createWindow` 回落本页；命名 profile
    （持久 cookie/cache + Chrome UA）、单例窗口、几何写 `feature-data` 并按屏幕可用区钳制。
  - `engine.py`：`find_spec` 探测宿主是否有 QtWebEngine（安装包没有）。
  - `external.py`：纯标准库降级——Edge `--app` 窗口（复用登录态）→ 系统默认浏览器。
  - `runtime.py`：无 Qt；`commands={"open"}`、开关闸门、逐级降级气泡；`slot` 形参按契约声明。
  - `settings_page.py`：开关 / 默认站点 / 额外白名单 / 当前内核档位 / 打开按钮；
    `dirty/confirm_save/discard_changes/changed.emit()` 按契约。
- 测试：新增 `tests/test_petcore_ai_browser_mod.py`（14 例，无 Qt 无网络）覆盖站点解析、
  域名伪装、`extra` 归一化、清单/加载、menu+settings 配对、运行时代码红线、开关闸门与两条降级；
  `tests/test_petcore_example_mods.py` 的 `IDS` 加入新 id。
- 打包：`packaging/petcore.iss` 新增 opt-in 任务 `mod_aibrowser`（默认不勾选）+ 源目录/`.trusted`
  两行；`test_petcore_installer_mods_opt_in.py` 要求每个 example 包都有安装入口。
- 文档：`PETCORE-MOD-DEVELOPMENT.md` 新增 §4.1「宿主能力与窗口：实测边界」；
  `PETCORE-MOD-FORMAT.md` §7 补 6 行（capabilities 不是闸门 / 窗口事实可行 / 宿主模块差异 /
  CLI 接受 ZIP）；`examples/petcore-mods/README.md` 加包说明与安装命令。

**验证（真实环境，非 mock）**
- `mods validate → ok`；目录安装 → `enabled`；`dist-extensions/tool.ai-browser-1.0.0.zip`
  （9 files / 14756 bytes / sha256 701969107fd4）→ 装进全新 root 也是 `enabled`。
- 真 `CoreHost` + 真扩展 + 真 WebEngine：`features` 含 `tool.ai-browser`、`diagnostics == []`、
  命令返回 true、窗口创建、Kimi 首屏可读 2188 ms、`https://example.com` 导航被拦
  （横幅「已阻止跳转到 example.com…」）、地址栏 `example.org` 被拒。
- 真 `SettingsDialog`：「扩展管理 → 加装扩展 → AI 浏览器」渲染正常；取消勾选 → 内存 false →
  `_commit()` → 磁盘 false → 勾回 → 磁盘 true；「内核」行显示「内嵌 Chromium（源码运行环境）」。
- 降级实机：把 `PySide6.QtWebEngine*` 从 import 系统摘掉后命令不抛异常、不导入窗口模块，
  真的拉起 `msedge.exe --app=https://chat.deepseek.com`（pid 36172，测试后关闭）。
- 性能（三次样本）：命令 1360–1985 ms、DeepSeek 首屏 4891 ms、Kimi 2188–2328 ms；进程树 RSS
  72.6 → 409.9 MB（主 350.0 + QtWebEngineProcess 59.8），关窗后 390.5 MB **不释放**。
- 门禁：`ruff check …` 全过；`pytest tests/test_petcore_ai_browser_mod.py
  tests/test_petcore_example_mods.py tests/test_petcore_extension_build.py
  tests/test_petcore_extension_candidates.py -q` → 35 passed。
  相关套件 `pytest tests/test_petcore_*.py tests/test_architecture.py -q` → 2207 passed in 262.51s
  （首轮红 1 条：新 example 包缺 `packaging/petcore.iss` 安装入口，补 `mod_aibrowser` 后全绿）。

**暴露的文档问题（已写进 PR 报告 §六）**
1. 「自定义 Qt 窗口做不到」与官方样板 `fun.divination/host/panel.py` 矛盾——窗口事实上能做，
   缺的是端口级治理；
2. 文档没写「宿主打包决定扩展能力」：安装包无 QtWebEngine，同一份 mod 行为不同；
3. `settings_page` 是唯一 Qt 面的说法与 `panel.py` 不符；
4. `capabilities`（`process.spawn`/`network.request`）无运行期强制，易被误读为授权；
5. CLI `mods install` 直接接受 `.zip`（文档只写了目录与 GUI）。

**提交范围**
- 本条只提交 `docs/` 文档；扩展包、测试、`packaging/petcore.iss` 与 `examples/README` 的改动
  是本地验证产物，未随本次提交推送。

**未覆盖 / 风险**
- 没做真实登录（无账号），登录 SSO 跳转域未全量验证，靠设置页「额外白名单」补。
- Edge 应用窗口模式无法限制窗口内跳转；内嵌 Chromium 常驻 ≈330 MB 且关窗不释放。
- 仅 Windows 11 本机验证；非 Windows 的 `webbrowser` 分支未测。

## L111 — 2026-10-28 — 联网检索不再污染进程路由 + 断流保留半条回答

**主题**：用户报「明明判断了不需要联网，AI 对话也会失败」（红色错误卡：联网检索过程 /
请求失败：网络不可达 / 本次回复未保存 / 顶部「出错：网络不可达」）。

**根因（静态核实，三条叠加）**
1. `petcore/net/http.py` 的 `urlopen()` 把「代理失败 → 直连成功」固化成**进程级**
   `_force_direct`；`petcore/net/websearch.py` 的 `_open()` 正走这条路径。于是检索轮里
   baidu/duckduckgo 直连成功一次，进程就改成「只直连」，紧随其后的正式回答请求不再尝试代理，
   在必须走代理的网络里抛 `NetworkError(network)`。这违反 `petcore/AGENTS.md`
   「显式路由请求不得改写进程级『固定直连』结论」。
2. 读流中断时 `stream_chat` 直接抛错，已累积的正文随异常丢失；mod 的失败分支手里握着
   `partial` 却一律 `chat.error(...)`，界面把流式气泡覆盖成「请求失败」并标注「本次回复未保存」。
3. 附带：判定轮（planner）用满额 `timeout`（默认 60s），离线时要拖到 60s 才降级。

**实现**
- `petcore/net/http.py`（+4/−1）：`urlopen(..., pollute: bool = True)`；只有
  `opener is None and pollute and not use_proxy and not _force_direct` 才写 `_force_direct`。
- `petcore/net/websearch.py`（+2/−0）：搜索/读页传 `pollute=False`（一处覆盖全模块）。
- `petcore/api/chat.py`（+24/−2）：planner 改用 `net_timeout`（≤20s）；读流已有增量时不抛错，
  关掉响应后返回 `ChatResult(incomplete=True, end_reason="interrupted")` 并先发状态行；
  一个字都没收到仍抛 `NetworkError`。`ChatResult`/`stream_chat` docstring 补该取值。
- `examples/petcore-mods/ai.chat/host/runtime.py`（+14/−0）：`partial` 非空时
  `done(半条 + "（{detail}：这次回答没有正常结束，已保留前面的内容。）")`；空正文仍 `error`。

**验证**
- `QT_QPA_PLATFORM=offscreen pytest tests/test_petcore_api_chat.py
  tests/test_petcore_api_chat_stream.py tests/test_petcore_websearch.py
  tests/test_petcore_ai_mod.py -q` → 84 passed（改前 4 failed，含 `assert True is False`
  的路由污染复现与 `assert not [('error','network','网络不可达')]`）。
- `ruff check petcore/ examples/petcore-mods/ai.chat tests/` → All checks passed。

**未覆盖 / 风险**
- 本机没有「必须走代理才能连 API」的网络，无法端到端复现原故障；证据到单元层（假 opener 的
  `use_proxy` 序列 `[True, False]` + `force_direct() is False`）。需用户在出问题的机器上复跑，
  并确认日志里不再出现「网络请求改走直连：系统代理不可用」紧跟检索轮。
- 断流保留半条后，会话里会多一条带括注的不完整回答——这是有意的（AUD-15 口径），不是新 bug。

## L112 — 2026-10-28 — 图片彩蛋支持 GIF 动图（气泡 + 弹窗，按 DPR 解码）

**主题**：用户要求「让显示图片彩蛋可以支持 GIF 格式」。

**根因**：`petcore/media/images.py::load_pixmap()` 用 `QImageReader.read()`，对 GIF 只取第 0 帧；
petcore 全仓没有 `QMovie`，所以 `.gif` 能显示但永远是静态首帧。

**实现**
- `petcore/media/images.py`（+44/−0）：新增 `is_animated()`（先看 `imageCount()`：1 = 静态、
  >1 = 动图、-1 才退 `supportsAnimation()`——Qt 对**单帧 GIF** 也报 supportsAnimation）与
  `logical_size_for()`（只读图片头尺寸，不解码像素）。
- `petcore/ui/animated_image.py`（新建 199 行）：`AnimatedImage(QWidget)`。`QMovie` 帧的
  `devicePixelRatio()` 恒为 1，所以不用 `QLabel.setMovie()`；改为 `setScaledSize(逻辑 × dpr)`、
  `paintEvent` 里 `setDevicePixelRatio(dpr)` + `drawPixmap(0, 0, pm)`，与静态图的
  「按物理像素解码」口径一致。`show/hide` 起停播，`set_path` 同路径复用，`close` 停播 +
  断 `frameChanged` + `deleteLater`。
- `petcore/ui/easter_egg.py`（+33/−6）：`EasterEggPopup` 按 `is_animated` 分叉；静态路径原样
  （`load_scaled_pixmap` + `setPixmap`），动图用 `AnimatedImage`，窗口尺寸公式形状不变；
  新增 `closeEvent` 停播（`WA_DeleteOnClose` 只删 widget）。
- `petcore/ui/bubble.py`（+69/−6）：`show_image()` 路径入参且为动图时，动图作为 `image_label`
  （白色圆角底板）的子控件贴在 8px 内边距里；`show_text()` 与 `hideEvent` 停播。

**验证**
- `QT_QPA_PLATFORM=offscreen pytest tests/test_petcore_images.py
  tests/test_petcore_easter_egg.py tests/test_petcore_bubble.py tests/test_petcore_self_talk.py
  tests/test_click_talk_dialog.py tests/test_petcore_animated_image.py -q` → 79 passed（新增 18 例；
  既有 PNG 用例一条未改）。
- 实机探针（offscreen，480×288 五帧 GIF，`PYTHONPATH=. .venv/Scripts/python
  .scratch/probe_docs_perf.py`）：`is_animated` 中位 0.045 ms（n=50）、`AnimatedImage` 构造
  1.076 ms（n=20）、重绘 0.012 ms / p95 0.021 ms（n=200）；静态 `show_image` 1.262 ms 对
  动图 1.282 ms（n=20），差异在噪声内。
- 真实对象抓帧（`.scratch/probe_docs_live.py`）：弹窗 504×368、`is_running == True`、抓帧中心
  像素 `#28c83c`；`close()` 后停播；气泡 `show_image(gif)` 在播、`show_text()` 后停播。
- `ruff check petcore/ tests/` → All checks passed。

**未覆盖 / 风险**
- 没有在 150%/200% DPI 真机上目视确认清晰度（offscreen 的 DPR 恒为 1，只断言
  `movie.scaledSize() == 逻辑 × dpr` 与 DPR 写回）。
- 只测到 480×288 五帧 GIF；10 MB 级 / 几百帧的 GIF 未测（`imageCount()` 扫描成本未知）。
- 若某格式声称多帧却让 `QMovie` 打不开，当前没有「退回静态首帧」兜底（未构造出该场景）。

## L113 — 2026-10-28 — API 页出厂预填 DeepSeek（只填 Key）+ 开放平台跳转按钮

**主题**：用户要求「供应商和模型的初始值先设成 deepseek 官网的内容，减少小白填写步骤，
只需填 api；下面放 deepseek api 官网 + 跳转默认浏览器的按钮」。

**现状**：`ApiService` 无预设，`ApiStore` 无种子，`ApiSettingsPage._start_draft()` 起的是
`name=""/base_url=""` 的空草稿——名称、地址、模型全要手填。

**实现**
- `petcore/ui/external_links.py`（新建 12 行）：`REPO_URL` / `QUARK_URL` / `DEEPSEEK_API_URL`
  （`https://platform.deepseek.com/api_keys`）/ `CHARACTER_PACKS_URL`；`page_about.py` 改为复用
  （+1/−2，去掉重复字面量）。
- `petcore/ui/settings/page_api.py`（+54/−9）：出厂默认常量（`DeepSeek` /
  `https://api.deepseek.com` / `/v1/chat/completions` / `deepseek-v4-flash` /
  `deepseek-v4-flash-vision-exp` / 余额协议 `deepseek`，与主分支 `pet/chat/models.py:60`、
  `pet/vision.py:70` 同值）；`_start_draft()` 用它们构造草稿并改状态文案；新增「API 申请」行
  （`value_field(DEEPSEEK_API_URL)` + 「打开 DeepSeek 开放平台」按钮 → `QDesktopServices.openUrl`）；
  模型行 hint 写清「如账号不支持可改成 deepseek-chat」；`keywords()` 补别名。
- 不改 `spec.py` / `store.py`：预填只发生在设置页草稿，`_populate()` 走同一批值，所以
  `_baseline` 一致、`dirty()` 在未改动时仍为 False。

**验证**
- `pytest tests/test_petcore_api.py tests/test_petcore_settings.py -q` → 82 passed
  （新增 3 例：预填值与 `dirty() is False`；只填 Key 能保存且 `api.json` 不含 Key、钥匙串有值；
  开放平台按钮 URL）。720×500 下「保存服务」可达的既有用例仍绿。
- `ruff check petcore/ tests/` → All checks passed。

**未覆盖 / 风险**
- 本机无真实 Key，`deepseek-v4-flash` / `deepseek-v4-flash-vision-exp` 与余额接口均未实测。
- 外链按钮只验证到 `QDesktopServices.openUrl` 入参，未真开浏览器。

## L114 — 2026-10-28 — 常规页「数据」新增两个素材包外链模块

**主题**：用户要求「『角色资源包』下方添加两个模块（完整角色素材包 / 高清完整角色素材包），
每个模块两个横排按钮，一个跳 GitHub 素材包页，一个跳夸克网盘（用现在的地址）」。

**实现**
- `petcore/ui/settings/pages_core.py`（+36/−0）：`data_card` 在 `角色资源包` 之后追加两行；
  新增 `GeneralPage._pack_links_row(pairs)`（`QWidget` + `QHBoxLayout`，margins 0、spacing 8，
  每项 `make_button` + `QDesktopServices.openUrl`）；URL 取 `external_links`；`keywords()` 补
  `素材包/下载/高清/网盘/github`。没有新增配置键、没有 `setFixedWidth`、没有动 `coverage.py`。
- `tests/test_petcore_settings_assets_links.py`（新建 160 行）：两行都在「数据」卡片且排在
  角色资源包之后、每行恰 2 个按钮、pack 行 `sizeHint ≤ 280`、`page_search_texts` 命中、
  点击 URL 依次为 `CHARACTER_PACKS_URL/QUARK_URL`、`dirty()` 仍 False。

**验证**
- `pytest tests/test_petcore_settings_assets_links.py tests/test_petcore_pages_smoke.py
  tests/test_petcore_settings.py tests/test_petcore_settings_coverage.py
  tests/test_petcore_extensions_geometry.py -q` → 62 passed（新增 5 例改前全红）。
- 搜索唯一性实测：`外观 → ['外观与交互']`、`DIAGNOSTICS → ['关于与诊断']`、
  `生成子宠 → ['多宠']` 仍各 1 页；`素材包/高清/完整角色 → ['常规']`。
- `ruff check petcore/ tests/` → All checks passed。
- 四项合并后的全量门禁：`QT_QPA_PLATFORM=offscreen pytest -q` →
  10734 passed, 18 skipped, 19 warnings in 1592.84s（本机单进程）。

## L115 — 2026-10-28 — 气泡/桌宠/右键菜单的窗口层级修复

**主题**：用户报两个层级 bug：①「窗口置顶关着，打开别的应用时鱼还是会突然跳出来」；
②「右键菜单开着时桌宠冒泡，桌宠与气泡会刷新盖过菜单」。前提是**气泡与桌宠同图层要保留**
（owned window 不许回退成独立顶层窗口）。

**根因（真机 `GW_HWNDPREV` / `WS_EX_TOPMOST` 实测）**
1. 裸 `show()` 就会把整条 owner 链抬到普通层最上面——`SpeechBubble` 的 `show(); raise_()`
   三处（`_apply_window_flags` / `show_text` / `show_image`）在 `always_on_top` 为假时也照抬。
2. `petcore/ui/menu.py` 的 `prepare_menu` 只在 `topmost=True` 时才抬菜单；**置顶层才是破口**
   （默认配置下桌宠/气泡带 `WindowStaysOnTopHint`，气泡再抬一次就压住菜单）。
3. 桌宠自己的 `set_visible(True)` 无条件 `raise_()`。

**实现**
- `petcore/ui/bubble.py`（+160/−5，文件 numstat 229/11 含上一批 GIF 改动）：新增 Win32 z 序
  原语 `_user32/_window_above/_place_after/_is_above`（`SetWindowPos(insertAfter,
  NOACTIVATE|NOOWNERZORDER)`；实测 `QWidget.stackUnder()` 对顶层窗口是空操作），以及统一出口
  `_present()`：菜单会话 → 沉到菜单下方；宿主置顶/直播 → 照旧 `raise_()`；非置顶 → 记住宿主的
  上邻居，`show()` 之后把宿主塞回原位、气泡贴宿主下方。`_window_flags()`、`SpeechBubble(self)`
  的 parent、`WA_ShowWithoutActivating` 一行未改。
- `petcore/ui/menu.py`（+65/−7）：`menu_open()` / `active_menu()` / `menu_session()`
  （可重入计数，异常也恢复），`show_context_menu()` 用会话包住整段 `exec`；`prepare_menu`
  的 `topmost` 规则不变（没把菜单永久置顶）。docstring 纠正「菜单需要 hint 才置顶」的错误
  认知（实测 Windows 上 Qt 弹出菜单本来就带 `WS_EX_TOPMOST`）。
- `petcore/ui/pet_window.py`（+7/−4）：`set_visible(True)` 的 `raise_()` 加 `menu_open()` 闸；
  删掉「`raise_()` 不会越过别的应用或右键菜单」这句已经不成立的注释。
- 测试：新增 `tests/test_petcore_window_layers.py`（374 行 / 11 例），真机用例在 offscreen 下 skip。

**验证**
- `.scratch/probe_layers.py` 真机（Windows，单屏，非 offscreen）修复前：冒泡后
  `z(other)=57 z(pet)=55 z(bubble)=54`（桌宠/气泡压在别的窗口上）、置顶层
  `z(menu)=12 z(pet)=11 z(bubble)=10`（气泡压菜单）→ 4 项 FAIL；修复后
  `z(other)=55 z(pet)=56 z(bubble)=57`、`z(menu)=10 z(pet)=11 z(bubble)=12`、
  `foreground 0x1A0AD0 -> 0x1A0AD0`（没抢前台）→ 全 PASS。
- `pytest tests/test_petcore_bubble.py tests/test_petcore_menu*.py
  tests/test_petcore_window_layers.py tests/test_petcore_settings.py -q` → 142 passed, 1 skipped。
- `ruff check petcore/ tests/` → All checks passed。

**未覆盖 / 风险**
- 单屏 + 单 DPI 实测；跨屏、DWM/独占全屏、150% DPI 下的 z 序未测。
- 非 Windows 走「不 raise」分支，未在 macOS/Linux 真机验证。
- **托盘右键菜单**由 Shell 原生弹出（`QSystemTrayIcon.setContextMenu`），不经
  `show_context_menu`，不在会话保护内；气泡理论上仍可能盖住托盘菜单（已知边界，未改
  `ui/tray.py`）。

## L116 — 2026-10-28 — 全屏自动隐藏（搬主分支能力）

**主题**：用户要求「常规设置里补上主分支的全屏自动隐藏」。

**实现**
- 新建 `petcore/platform/fullscreen.py`（329 行，零 Qt）：搬 `pet/platform_win.py` 的
  `_fullscreen_geometry_hit` / `_fs_user_busy_state` / `_fg_fullscreen_probe` 口径，前台信息
  复用 `platform/foreground.py`。纯几何 `fullscreen_geometry_hit()`（2px 容差）、
  `monitor_rect_for()`（按相交面积选窗口自己那块屏，跨屏不误判）、`user_busy_state()`
  （QUNS 只认 3/4）、`probe_fullscreen() -> (hit, reason)`：永不抛异常，非 Windows 报
  「不支持」，排除本进程/桌宠自身/Progman/WorkerW/任务栏/CoreWindow/截图工具/`WS_EX_TOOLWINDOW`，
  原因摘要只含类名/进程名/样式位/矩形数字，**不含标题正文**。
- 新建 `petcore/ui/fullscreen_hide.py`（222 行）：`FullscreenHideController`，进程内唯一、
  1Hz `QTimer`；状态机 `_known/_auto_hidden/_overridden`：命中只隐藏当时**可见**的宠并记账，
  离开只恢复仍在隐藏的已记录宠；全屏期间用户手动显示/隐藏以用户为准；重复命中幂等；
  配置关/平台不支持时**不建定时器**（探测 0 次），关闭那一刻立即恢复。
- `petcore/config.py`（+8）：`auto_hide_fullscreen=True`（`DEFAULTS` + `_normalize` +
  `GLOBAL_CONFIG_KEYS`），`SCHEMA_VERSION` 未动。
- `petcore/ui/settings/pages_core.py`：「常规 → 桌宠」加「全屏时自动隐藏」行（提示
  「全屏游戏或视频期间自动隐藏桌宠。」），勾选实时生效，dirty/confirm/discard 语义与其它行
  一致；非 Windows 置灰并说明。
- `petcore/ui/app.py`（+46）：`start_fullscreen_hide()` / `apply_fullscreen_hide()` /
  `set_auto_hide_fullscreen()` / `stop_fullscreen_hide()`，`run()` 启动、`finish()/quit_app()`
  停表。
- 测试：新增 `tests/test_petcore_fullscreen_hide.py`（492 行 / 22 例）。

**验证**
- 真机探测（`.scratch/probe_fullscreen.py`，Windows，独立进程开真全屏窗）：
  `baseline hit=False`（Kimi Code 1920×1128、任务栏在内 → 不误判，`busy=5` 也不命中）→
  `fullscreen detected=True after=0.20s` → `pets before: [True, True]` →
  `after enable: visible=[False, False]`（两只真 QWidget 桌宠被隐藏）→ 再探 3 拍仍隐藏 →
  关掉全屏窗后 `after leave: visible=[True, True]`；本进程自己的整屏窗口按 pid 自排除；
  `timer_active_after_disable=False`。
- `pytest tests/test_petcore_fullscreen_hide.py tests/test_petcore_settings.py
  tests/test_petcore_pages_smoke.py tests/test_petcore_architecture.py -q` → 72 passed。
- 变异验证：把「离开只恢复自动隐藏的」改成「全恢复」→ 3 红；去掉 `supported` 闸门 → 1 红；
  去掉「用户刚显示的让位」首轮全绿（无覆盖）→ 补用例后再变异 1 红。

**未覆盖 / 风险**
- 独占全屏 D3D 游戏、钉住/永在最前的 UWP 与 cloaked 窗口、真双屏、macOS/Linux 真机未实测；
  `SHQueryUserNotificationState` 的「命中」路径只走单测（基线只出现 busy=5）。
- 灵动岛不隐藏：与主分支一致（主分支全屏只按 `quiet_also_on_fullscreen` 抑制通知），
  petcore 暂无该开关，属后续项。

## L117 — 2026-10-28 — 音乐 mod 三修：唱歌续播 / 歌词不被自语打断 / 单曲循环继续出词

**主题**：用户报 `media.music`（只在网易云上测过）三个问题：①唱歌动作被打断后不再唱、
只剩正常轮询；②歌词播放期间被自语气泡打断；③单曲循环第二遍不出歌词。

**根因**
1. `PetWindow._play_spec()` 是循环的唯一清除点，探头/击飞/拖拽/换动作都会清 `_loop_active`；
   旧 runtime 只要租约还在（`current <= self._leases`）就直接 return，于是「租约在、循环没了」
   永远不再请求动画。
2. 歌词与自言自语共用同一个 `SpeechBubble`，presentation 端口没有「谁在占用」的概念。
3. 曲目身份 `Playback.key` 不变，网易云不上报 position（恒 `None`），本地时钟只增不减 →
   `_lyric_index` 停在末句，第二遍不再出词。

**实现**
- `examples/petcore-mods/media.music/host/runtime.py`（+265/−18）：`_reap_lost_loops()` 每拍用
  `animation_state()` 复核（`loop=True`/`locked=True`/`None` 什么都不做；`loop=False` 只丢租约、
  **不发** `release_loop()`），补播走 2^n 退避（`_rearm_pending` 保证永不放弃，切歌清零）；
  `_sync_scene()` 在「在放歌 + 有歌词或唱起来过」时 `begin_scene("media.music")` 并每拍
  `refresh_scene`，停止/暂停/纯音乐/关歌词时 `end_scene`；`_is_rewind`（回退 ≥5s、新位置
  ≤15s、上一拍已过 0.75 曲长）/`_advance_round`（不上报进度按估算曲长回绕）/`_restart_after_pause`
  三路判「新一圈」，只重置行号与节流、**不重新取词**（60s 冷却与每曲 3 次不受影响）。
- `petcore/contracts/host.py`（+39）：`PresentationPort` 增量面 `say/say_sequence(owner=)`、
  `begin_scene/end_scene/refresh_scene/animation_state` + `AnimationState`；核心只认 owner 字符串。
- 新建 `petcore/ui/presentation_gate.py`（149 行）：进程级可重入闸门（RLock、同 owner 重入计数、
  **45s 无续期自动释放**），`allows(owner)` 判定。
- `petcore/ui/app.py`（+55/−3）：`_PresentationAdapter` 在 `say/say_sequence` 里查闸门（非持有者
  且非 `force` → 返回 False，不弹不报错），新增四个端口方法；`animation_state` 读
  `window._loop_active` / `state.current.name` / 探头·击飞·拖拽·抛掷等锁定位。
- 主代理收口：`fun.self-talk/host/factory.py`（+3/−1）把「气泡被拒仍朗读」改成
  `if shown:`；`petcore/ui/reminders.py`（+3/−1）定时提醒改 `force=True`（用户设的提醒要压过歌词，
  拿不到气泡才退系统通知）；`PresentationPort.show_image`（`contracts/host.py`）也加
  `force/owner` 并在适配器（`ui/app.py`）过同一道闸门——否则自言自语 30% 概率的**配图**照样会
  盖掉歌词；`fun.click-talk/host/factory.py`（+1）点击台词的配图传 `force=True`（用户动作）。
- 测试：`tests/test_petcore_music_mod.py` +367/−3（8 例）、新增
  `tests/test_petcore_presentation_gate.py`（358 行 / 13 例，含 mod×真实适配器×真实闸门的端到端）。

**验证**
- mod 侧红→绿：8 failed, 39 passed → 48 passed（`SCENE_OWNER` 不存在、循环不补、第二遍不出词）。
- `pytest tests/test_petcore_music_mod.py tests/test_petcore_music_lyric_quality.py
  tests/test_petcore_presentation_gate.py tests/test_petcore_self_talk.py
  tests/test_click_talk_dialog.py tests/test_petcore_architecture.py -q` → 104 passed；
  补上 `show_image` 闸门用例后同批（gate/click-talk/self-talk/music）→ 82 passed。
- `scripts/music_probe.py --seconds 2` 本机真跑通（SMTC 无会话、枚举 9 个会话、220ms）。
- `ruff check petcore/ examples/petcore-mods/media.music tests/` → All checks passed。

**未覆盖 / 风险**
- **本机没有真实播放器在放**：网易云/酷狗的真实 SMTC 行为、真实动画被打断、真有歌时的第二遍
  循环都未实机验证；不上报进度路径的每一圈有「尾奏长度」级的漂移（估算曲长 = 末句 + 6s），
  连续多圈会比声音略早，尾奏里手动暂停再播也会被当成新一圈。
- 歌词期间被拒的自语气泡现在**连朗读一起跳过**（主代理收口）；提醒仍显示但形态可能变成
  系统通知之外的路径（已改 force，实际以气泡为准）。
- 灵动岛上不显示歌词，不受影响。

## L118 — 2026-10-28 — 角色重命名（别名）

**主题**：用户要求「主分支可以修改动画角色的名称，本分支也加上」。

**实现**
- `petcore/config.py`（+75）：`ALIAS_MAX_CHARS=24`；`DEFAULTS["character_aliases"]={}`；
  `GLOBAL_CONFIG_KEYS` 加键（用户级偏好，所有宠共用一份，`config-pet-N.json` 不出现）；
  `_normalize` 里逐项清洗（非 dict → 空表、值 strip 后截断 24、空白/含控制字符的项丢弃）；
  `Config.character_alias()/set_character_alias()`（空名删键、不抛异常）+ `ScopedConfig` 转发。
- `petcore/ui/app.py`（+72）：`_display_spec()` 统一「别名 or 目录名」（`CharacterSpec` 是
  frozen，用 `dataclasses.replace`），`characters()/character_for()/available_characters()`
  全部经它，目录层 `_characters` 不被写回；新增 `character_alias/set_character_alias/
  character_default_name/character_display_name/refresh_character_names`。
- `petcore/ui/pet_window.py`（+47）：`rename_character()`（输入 → 写主配置并立即落盘 → 刷新
  设置页下拉 → 气泡「角色名：…」）。
- `petcore/ui/menu_actions.py`（+5）：「切换角色」子菜单末尾 `addSeparator()` +
  「重命名当前角色…」。**没新增菜单项 id**，`coverage.py` / 菜单模板 JSON /
  `migrate_pNN_menu` 全未动，因此不需要布局迁移。
- 新建 `petcore/ui/rename_dialog.py`（75 行）：两入口共用的 `prompt_alias()` 与提示/气泡文案。
- `petcore/ui/settings/pages_core.py`（+95）：角色行改成 `QComboBox + 重命名…`；
  `dirty()/confirm_save()/discard_changes()` 都覆盖别名（取消/Esc 恢复打开时的名字）；
  `keywords()` 补「重命名/改名/别名」。
- 测试：新增 `tests/test_petcore_character_alias.py`（438 行 / 25 例）。

**验证**
- 红证据（`.scratch/alias_red.py`，逐个 `delattr` 掉本次新增接口后跑同一份测试）：
  13 failed, 2 passed, 10 errors；实现后 25 passed。
- `pytest tests/test_petcore_character_alias.py tests/test_petcore_settings.py
  tests/test_petcore_settings_coverage.py tests/test_petcore_pages_smoke.py
  tests/test_petcore_architecture.py -q` → 79 passed。
- `ruff check petcore/ tests/` → All checks passed。
- 本批四项合并后的全量门禁：`QT_QPA_PLATFORM=offscreen pytest -q` →
  10819 passed, 19 skipped, 18 warnings in 1224.93s（本机单进程，最终代码）。

**未覆盖 / 风险**
- 没在真桌面鼠标点菜单/设置按钮（`QInputDialog.getText` 被 monkeypatch）；真窗口模态输入框
  观感与菜单开着时的气泡落位未目视确认。
- 灵动岛胶囊上的角色名只在建岛时取一次，重命名后要等重建/重启才换（与切角色同一条既有边界，
  本次未修）。

## L119 — 2026-10-28 — 清单收 `summary` / `summary_zh`；首次出未签名安装包

**主题**：用户要求「编一个 setup，不签名生成试试」。`-Installer` 构建在冒烟门
（`--check --strict`）被顶红，诊断是 `dsh.channel: unknown field: summary_zh`。

**根因**：`petcore/mods/manifest.py` 的 `KNOWN_FIELDS` 没有 `summary` / `summary_zh`，而市场
索引（`petcore/market/model.py:63`）用的就是 `summary_zh`；第三方扩展在 `mod.json` 里写简介
就会被记成 unknown field 警告。警告进 `diagnostics`，而构建脚本要求「零诊断」才放行。

**实现**
- `petcore/mods/manifest.py`（+3/−0）：`KNOWN_FIELDS` 补 `summary` 与 `summary_zh`（只用于
  展示，缺省不影响加载，不新增必填项、不改 `SCHEMA_VERSION`）。
- 验证：`load_manifest(<数据目录>/mods/dsh.channel)` → 无诊断；
  `python -m petcore --check --strict` → `diagnostics: []`、`strict.problems: []`、
  `characters: 1`、`menu_template: json`。

**未签名安装包（实测记录，产物不入库）**
- 命令：`pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/build_petcore.ps1 -Clean -Installer
  -OutputRoot dist-unsigned-2026-10-28`（不传 `-SignThumbprint`）。
- 产物：`dist-unsigned-2026-10-28\dsh-pet-core-setup.exe`，275 MB，
  sha256 `ad2074ac287844845425fe4037df3292e1da73da750b0b0e83795cf1bcd68c01`，
  `Get-AuthenticodeSignature` → `NotSigned`。
- 本机 SAC `VerifiedAndReputablePolicyState=0`（未强制），未签名产物可以启动：
  setup 起来后弹出「选择安装语言」向导（实测窗口标题），打包后的
  `dsh-pet-core\dsh-pet-core.exe --check --strict` 退出码 **0**、`diagnostics: []`。
- 安装器是 `PrivilegesRequired=lowest`（按用户安装、不弹 UAC）；本次只验证「能启动」，
  没有点完向导，因此**没有真正安装到本机**（重复启动留下的 3 个 `dsh-pet-core-setup.tmp`
  子进程已 `Stop-Process` 清掉）。

**未覆盖 / 风险**
- 未签名产物在 `SAC=1`（强制模式）或 SmartScreen 的机器上仍会被拦（`AGENTS.md` 的构建纪律
  要求正式分发签名，本次是用户明确要求的未签名试跑）。
- 没有走完安装流程，所以「安装后首次启动、快捷方式、卸载器」未验证。

## L120 — 2026-10-28 — 联网失败不再谎报「网络不可达」；路由污染面清零

**主题**：用户报「刚打开桌宠时『看看屏幕』和『AI 对话』都报网络不可达，打开设置点一次
『测试连接』再关掉就正常了」。

**根因（两条，第一条确定，第二条排除）**
1. **mod 的「说谎式兜底」**：`ai.chat` 的四条失败路径都写着
   `reason = str(getattr(exc, "reason", "") or "network")`（看看屏幕那处更糟：`or exc` 直接把异常
   对象当 reason），于是 `KeyError("unknown api service")`、`ValueError`、`json`/`ssl` 这类
   **根本不是链路问题**的异常全被显示成「网络不可达」，而且失败路径**一条日志都没有**。
2. **不是路由污染**：真机探针（`.scratch/probe_ai_startup.py`、`.scratch/probe_startup_urlopen.py`）
   在真实数据目录、无 warm 的情况下首次调用即成功（`force_direct` 全程 False）；30 秒真实启动
   （11 个扩展全加载）期间 `urlopen` 调用数 **0**，启动窗口内没有任何请求会碰
   `petcore/net/http.py` 的 `_force_direct` 闩；`ssl.create_default_context()` 在 worker 线程正常
   （0–47 ms）；首启 `credentials.get("core.api","api1")` 有值；`metadata()` 只在服务 ID 不存在时抛
   `KeyError`（`.reason=None`）——那正是过去被兜底成「网络不可达」的真实路径。

**实现**
- `petcore/net/errors.py`（+9/−1）：新增稳定分类 `INTERNAL="internal"` +
  `REASON_TEXT` 文案「内部错误（不是网络问题，详情见日志）」。既有六类一字未改。
- `examples/petcore-mods/ai.chat/host/runtime.py`（约 +119/−41）：统一 `self._failure_reason(exc, service_id)`
  ——有 `.reason` 用它，没有就 `internal`，并**只记一条** `ai call failed: type=… reason=… service=… cause=…`
  （只打异常类型名，不打 Key/URL/响应体/异常文本）；`AiChatFailure(reason, detail)` 包住截图问答与
  动作回退；新增 `_require_service()`：发请求前发现没有服务就直接 `internal` + 「还没有可用的 API
  服务」，**一次请求都不发**。
- 主代理顺手清零同类污染面：`petcore/net/market.py`（两处）与 `petcore/update/service.py` 的
  `urlopen` 加 `pollute=False`（市场索引/下载、更新检查是第三方 CDN 的一次性取数，不该决定整个
  进程是否只走直连），并新增源码守卫
  `tests/test_petcore_http_safety.py::test_only_api_hosts_may_pin_the_process_route`：除
  `api/chat.py`/`api/probe.py`/`account/service.py` 三个 API 主机外，任何 `urlopen` 调用点必须传
  `pollute=False`，否则红。
- 测试：新增 `tests/test_petcore_ai_startup.py`（451 行 / 15 例）。

**验证**
- 红→绿：把 `runtime.py` 单独 stash 后 9 例失败（断言都是 `assert 'network' == 'internal'` /
  「日志为空」），恢复后 15 passed。
- `pytest tests/test_petcore_ai_mod.py tests/test_petcore_api_chat.py
  tests/test_petcore_api_chat_stream.py tests/test_petcore_ai_startup.py -q` → 87 passed。
- 相关族 `net/screen_ask/screen_capture_ask_dialog/example_mods/api` → 157 passed；
  `architecture/docs` → 13 passed；市场与更新的 6 个测试文件 → 83 passed。
- 真机探针：`chat_first ok=True 844ms end_reason=done`、`probe ok HTTP 200 219ms`、
  `chat_after_probe ok=True 1328ms`，三次 `urlopen` 的 `force_direct_after` 都是 False。

**未覆盖 / 风险**
- **用户机器上的复现没做到**（本机无系统代理，首启就成功）。现在的保证是：失败时日志会多一行
  `ai call failed: type=… reason=… service=… cause=…`，`cause=URLError/ConnectionRefusedError` 表示
  「代理/加速器还没就绪」，`cause=-` 且 `reason=internal` 表示服务或 Key 缺失。
- 若确认是「加速器比桌宠晚起来」，正确的修法是给启动窗口一次有界重试或延后首请求——本次**没有**
  给 chat 加通用重试（那会掩盖真实故障）。

## L121 — 2026-10-28 — 音乐暂停：立即停唱 + 暂停期间不取词不报错

**主题**：用户报「歌曲暂停后会出现『歌词获取失败』的报错，并且会持续播放唱歌动画一小段时间」。

**根因**
1. `_silence_limit()` 对除 `absent` 以外的状态一律返回 `music_sing_grace_seconds`（默认 5 秒），
   于是「明确暂停」也要等满 5 秒才 `_release()`；宽限本来是给**不确定的静音**（切歌抖动、
   播放器被关掉）用的。
2. 暂停分支走 `_adopt_track(..., playing=False)`；暂停期间识别结果若换了身份（SMTC 会话掉线 →
   窗口标题源兜底，`duration/album` 丢失、`key` 变化）就进 `_begin_track()`，把
   `_lines/_lyric_index/_fetch_key/_fetch_attempts/_lyric_reported` 全清掉，恢复播放后重新联网取词，
   失败时 `_apply_lyrics` 冒泡「歌词获取失败」，还白吃一次「每曲 ≤3 次」的配额。

**实现**（`examples/petcore-mods/media.music/host/runtime.py`，约 +67/−16）
- `_silence_limit()`：`paused → 0.0`（下一拍就 `_release()`）；`absent` 逻辑一字未改。
- 新增 `_track_aliases`：暂停期间见过的「同名降级身份」记为当前曲目的别名，恢复后仍认作同一首；
  `_adopt_track` 的暂停分支不再 `_begin_track`、不动歌词与取词状态；`_same_song()` 按歌名比较。
- `_apply_lyrics` 失败：不在播时静默并**退回配额**（不设 60s 冷却、不置 `_lyric_reported`）；
  在播时原规则不变。
- 主代理同步设置页文案：`music_sing_grace_seconds` 的 hint 改成「没声音（切歌抖动、播放器被关掉）
  超过这个时间才停止唱歌动画；暂停会立即停唱，歌与歌之间的空隙不会打断。」
- 测试：`tests/test_petcore_music_mod.py` +196/−4（1 例改写 + 5 例新增）。

**验证**
- 红→绿：改前 5 例失败（`released == 0`、暂停后仍冒「歌词获取失败」、「降级身份」被当成换歌），
  恢复后 8 passed。
- `pytest tests/test_petcore_music_mod.py tests/test_petcore_music_lyric_quality.py
  tests/test_petcore_music_runtime_state.py tests/test_petcore_music_sources.py -q` → 108 passed；
  相关族（含 `test_music_sing_grace.py`/诊断/标题/示例 mod/展示会话）→ 187 passed。
- 真机：`scripts/music_probe.py --seconds 4` 在本机（网易云**正处于暂停**）判定
  `暂停 · 系统媒体会话 · cloudmusic.exe ·《开不了口》—周杰伦`；另用真实
  `sampling.collect` + `recognizer.decide` 喂进运行时，13 项断言改前 6/13、改后 **13/13**。
- 性能（20 万次 × 5 轮取中位）：`pause_tick_ns` 525.9 → 205.2、`silence_limit_ns` 368.2 → 47.7；
  每拍本地开销仍是亚微秒级（真实采样 8–177 ms/拍），无新增系统调用/网络/磁盘/线程。

**未覆盖 / 风险**
- 没有真实「播放中」的采样（本机播放器是暂停状态），「播放 → 暂停」边沿用合成判定 + 真实曲目身份
  模拟；播放中身份降级（非暂停）仍按换歌处理，是刻意保留的边界。

**未覆盖 / 风险**
- 没有目视截图（offscreen 无 CJK 字体）；720×500 下 pack 行实际 224px、行宽 466px 不溢出，
  但把字号抬到 19pt 时约 591px，会走页面既有的横向滚动（不裁切，观感未确认）。
- 两个 GitHub 按钮当前指向仓库根（`CHARACTER_PACKS_URL = REPO_URL`）；换独立素材页只改一行。

## L122 — 2026-10-29 — 对话传输瞬时失败自动重试一次（零输出才算「没发生」）

**主题**：用户报 petcore 版 AI 对话偶发「请求失败：网络不可达」，下一轮又正常；「看看屏幕」
气泡里还会显示英文 `network`。

**证据**
- 文案唯一来源 `petcore/net/errors.py:36` + `NetworkError.__init__`（空消息才显示「网络不可达」）；
  `api/chat.py` 只在「打开连接失败」或「读流中断且一个正文/思维链字节都没收到」时抛它。
- 真机：`getproxies()` 为空、`http_proxy/https_proxy/all_proxy` 全空、`ProxyEnable=0`；
  对 `api.deepseek.com` 连续 3 次 curl 全部 0.2–0.4s 成功 ⇒ 是**瞬时**传输失败（TLS 重置 /
  连接被掐 / DNS 抖动），不是持续不可达。旧 `urlopen` 的「代理→直连」两次尝试在无代理时
  是同一个直连、紧挨着、无退避，等于没有重试。
- 放大因素：`net/http.py` 的 `_force_direct` 一旦置真永不复位（`reset_transport()` 只有测试调用）。
- 「屏幕 network」：`ai.chat` 的 `_REASON_TEXT` 只映射 `internal`，`_look_failed` 用
  `_REASON_TEXT.get(text, text)` 把 `network` 原样贴到气泡；`_on_ask` 还会拼成 `network：网络不可达`。

**实现**（`petcore/api/chat.py` +113/−28、`petcore/net/http.py` +18/−5、`petcore/ui/ports.py`
+14/−3、`contracts/api.py` 1/1、`ai.chat/host/runtime.py` +16/−2）
- `_Progress`（正文/思维链计数）+ `_stream_once()`：`stream_chat` 最多 2 次尝试，仅当失败分类是
  `network`/`tls`、未取消、且 `progress` 全为 0 时重发一次（`RETRY_DELAY_S=0.6`，状态行
  「网络抖动，正在重试…」）。`HTTPError`、`timeout`、`protocol` 从不重试；已吐出半条正文的
  断流仍返回 `interrupted`（不重发，避免重复）。
  > 与 L120「不给 chat 加通用重试」的关系：L120 拒绝的是「掩盖真实故障的通用重试」；
  这次只在**用户什么都没看到**的传输层抖动上重发一次并显式告知，任何服务器答复都不重试。
- `_open_response()`：`_post_json`（联网检索 planner）与正式请求共用的打开阶段重试（≤2 次），
  `HTTPError` 立刻转成稳定分类抛出。
- `net/http.py`：`_force_direct` 带 TTL（`DEFAULT_DIRECT_PIN_SECONDS=300`），过期后重新试代理；
  `force_direct()`/`reset_transport()` 同步。
- `CoreApiPort.stream_chat(timeout=None)` 改用 `api.json` 的 `metadata.timeout`（钳 5–300s），
  设置页那行不再恒 60s 骗人。
- `ai.chat` 的 `_REASON_TEXT` 补全六类中文；`AiChatFailure` 不再把同一句拼两遍。

**验证**
- 新增/改写 `tests/test_petcore_api_chat_stream.py`（+113/−27）：首连 RST、次连正常 → 返回完整
  回答且只吐一次正文；两次都失败 → `network` 且服务端只看到 2 次连接；半截分块 → `protocol`
  且**不重试**（`accepted == 1`）；已有 delta 的断流不重试。
- `tests/test_petcore_http_safety.py` +31：钉子 10s 内只走直连、过期后重新试代理。
- `tests/test_petcore_api_chat.py` +33：`api.json` 的 `timeout=59` 真的传到传输层，显式传参优先。
- `tests/test_petcore_ai_mod.py` +19：`AiChatFailure("network", "网络不可达") == "网络不可达"`。
- 性能（本机，样本见 PR 报告）：`_open_response` 无重试时固定开销 **0.47 µs/次**；同一请求
  里 `ssl.create_default_context()` 本身 14.5–26.3 ms。`decide()` 未受影响（见 L123）。

**未覆盖 / 风险**
- 没有抓到真机上「同一秒的失败前后」日志（用户那次失败不在当前日志窗口内）；重试是否必然救回
  取决于抖动时长（超过一次重试仍失败会照旧报错）。
- 重试会让同一轮最多多一次 POST（只在零输出时）；DeepSeek 是否对未返回的连接计费未核实。

## L123 — 2026-10-29 — 音乐暂停：同源窗口会话不再翻盘 + 暂停边沿立刻收气泡

**主题**：用户报「音乐暂停后，桌宠还会播放一段时间歌词」。

**根因**
- `recognizer.decide()` 在 SMTC 报暂停时仍问窗口标题源，只要该 exe 的 WASAPI 会话是 `active`
  就判「在播」并**直接返回**；而同一台播放器暂停后会话常仍挂 `active`（`audio_sessions` 自己
  写了这条事实）。窗口源的 `Track` 没有 `position`，运行时改用本地时钟累加
  （`runtime._position`），于是暂停期间每 ≥2 秒继续换一句歌词。
- 暂停只 `end_scene`，**没人收气泡**：最后一句按 2–6 秒继续挂着。

**实现**（`media.music/host/recognizer.py` +13/−1、`runtime.py` +16、`petcore/ui/app.py` +14、
`contracts/host.py` +7、`ui/pet_window.py` +6）
- 同源排除：窗口源判在播、且 `normalized_exe(window.exe) == normalized_exe(smtc.app_id)` 时
  **不接受**它翻盘（记 reason「同一台播放器…残留的 Active 会话不算在播」）；另一台播放器仍按
  原契约以窗口源为准（AGENTS 的「SMTC 暂停仍要问窗口源」本意保留）。
- 新增 `PresentationPort.hide(owner="")`：`owner` 非空时只在闸门仍归它时生效（绝不收别人的
  气泡）；`_PresentationAdapter.hide` → `PetWindow.hide_bubble()`。
- 运行时在「上一拍在播 → 这一拍 paused」的**边沿**调用 `hide(SCENE_OWNER)`。

**验证**
- `tests/test_petcore_music_sources.py` +29：同 exe 窗口 active → `paused` 且带理由；
  另一 exe 在出声 → 仍 `playing`（以窗口源为准）。
- `tests/test_petcore_music_mod.py` +6：暂停边沿 `hidden == ["media.music"]`。
- `tests/test_petcore_presentation_gate.py` +18：`hide(owner)` 只收自己持有的气泡。
- 性能（本机，5 轮取中位，每轮 2000 次）：`decide()` 暂停无窗口源 **4.27 µs**；暂停 + 同源窗口
  源 **9.05 µs**（多出的 4.8 µs 只出现在「paused 且采到窗口」的拍子上，1 Hz）。

**未覆盖 / 风险**
- 真机没有复现「暂停但会话仍 active」的那一刻（`scripts/music_probe.py` 抓到的 cloudmusic 当时
  是 `inactive`）；同源分支由判定表用例覆盖，未在真实播放器上验证。
- 没有动 `absent` 的宽限语义：切歌间隙仍保留上一句，故意不 hide。

## L124 — 2026-10-29 — 移植主分支「重置本宠设置 / 重置全局设置」到设置侧栏

**主题**：把 4.4.0 的两个重置按钮搬进 petcore，按 petcore 的存储布局重新定义作用域。

**设计**
- petcore 与主分支存储布局不同：主宠的全局设置与自己的偏好同在 `config.json`，子宠偏好在
  `config-pet-N.json`。所以两个按钮是**两组键**（`GLOBAL_CONFIG_KEYS`），不是两个文件。
- 永不重置：`active_pets`、`positions`；本宠重置另保 `character`/`rx`/`ry`/`screen_name`
  （主分支 `reset_slot_profile` 的保留口径）。未登记键（`extra`）不动。
- 不碰 `api.json` / 钥匙串 / 开机自启（petcore 后者是纯注册表状态，没有配置键）。

**实现**（新 `petcore/settings_reset.py` +110、`petcore/ui/settings/dialog.py` +100）
- `reset_keys(scope)` / `reset_values(scope)` / `apply_reset(config, scope)`：先 `snapshot()`，
  逐键写 `DEFAULTS`（深拷贝），`ScopedConfig.save()` 落盘；`OSError` → `restore()` 回滚 + `ResetError`。
- 侧栏底部两枚按钮（`resetPetButton` / `resetGlobalButton`，非主按钮 + accessibleName + tooltip）；
  确认框 `QMessageBox.Warning`，「重置」是 DestructiveRole、默认键与 Esc 都落「取消」。
- 重置落盘后 `_close_after_reset()`：停实时预览定时器、**跳过 `_commit()` 与快照回滚**、
  `done(Accepted)` —— petcore 的 X 关闭=保存、取消=回滚快照，任何一个都会把旧控件值写回去。
- 全局作用域额外 `pets.sync_shared_settings()` + `reload_menu()`；两者都 `apply_live_settings()`。

**验证**
- 新 `tests/test_petcore_settings_reset.py`（8 例）：两个作用域覆盖的键集合互斥、保留项、
  全局重置不动位置/身份/本宠偏好、本宠重置不动全局键与身份位置、落盘、主宠同一文件拆半、
  子宠只写自己的文件、写盘失败回滚内存。
- `tests/test_petcore_settings.py` +89/−2：两个按钮存在且不可达搜索索引、取消不写、确认后
  `_commit` 一次都没调、本宠重置保身份与全局字号；另有两条**几何**用例（720×500 最小窗口 ×
  13/18px 字号）：两枚按钮都在侧栏视口内且可见、导航仍有高度。
- 性能（本机，5 轮 × 每轮 3 次，取中位）：`apply_reset(config, global)` = **2.92 ms**
  （含原子写盘；作用域 129 键 / 本宠 81 键，只写取值为默认的键）。

**未覆盖 / 风险**
- 没有做真机点击录屏；`QMessageBox` 的三个按钮在 offscreen 下由注入 `_confirm_reset` 跳过，
  确认框本身只在 `--check`/手工路径里出现。
- 重置后不做「按新配置重建设置页控件」——窗口直接关闭；若以后要求留在设置页，需要补重建路径。

## L125 — 2026-10-29 — 点击台词/自言自语真的出声：播放音量取合成结果

**主题**：用户报「点击的自言自语和自言自语的语音现在还是没声音」。

**证据**
- 真机 `%APPDATA%/dsh-pet-core/config.json`：`click_talk_enabled/self_talk_enabled/voice_enabled`
  全 true、`speech_backend=edge`；日志每下点击都有 `click-talk kind=...`；`cache/tts` 里已有 43 个
  mp3/wav ⇒ 合成成功、**播放**失败且无日志。
- 断点：`CoreSpeechPort._play` 把 `job.request.volume`（`SpeechRequest.volume` 默认 `None`）交给
  `AudioAdapter.play_tracked`，后者 `float(volume)` → `TypeError` → 收敛成 `SpeechError` →
  `on_error`（feature 没传）→ 无声无日志。

**实现**（`contracts/speech.py` +7、`speech/service.py` +8/−1、`ui/speech_channel.py` +6/−2、
两个内置 feature 各 +14）
- `SpeechResult` 增 `volume: float = 1.0`（合成时实际采用的值，含 `speech_volume`）；
  三处 return 都带上它。`_play` 用 `result.volume`，不再回读请求里的 `None`。
- `fun.click-talk` / `fun.self-talk` 调 `speak()` 带 `on_error`，只记 `reason`（不记台词/URL）。

**验证**
- `tests/test_petcore_speech.py` +52：`TrackedAudio` 复刻真机 `float(volume)` 语义——请求留空时
  播放音量必须是 `1.0`（改前 TypeError → on_error）；`speech_volume=0.4` 时播放音量 0.4。
- 真机探针 `.scratch/tts_click_probe.py`（真实 `SpeechService` + `CoreSpeechPort` + `AudioAdapter`
  + 真 Qt 事件循环）：合成 3.5s 的 mp3 并**播完**（`finished: True`、`errors: []`），本机听到声音。
- 性能：音量解析 **0.26 µs/次**（每次播放一次）；无新增线程/网络/磁盘。

**未覆盖 / 风险**
- 只在真机上验证了「点击台词」这一句文本；`self_talk` 用同一条 `_play` 路径，未单独听。
- 免打扰期间 `speak()` 仍按既有语义拒绝（现在会留下一条 `reason=unavailable` 的 warning）。

## L126 — 2026-10-29 — 桌宠身份上限 16 → 99（含碰撞粗筛）

**主题**：用户要求「子桌宠生成上限调为无上限，或 99 只」。

**为什么不是「无上限」**：每个身份背后是一个真实 `PetWindow`（各自的动画播放器、配置、
碰撞结算）。身份空间没有上限就只剩内存与 CPU 兜底，失败形态是系统卡死而不是一条可读提示。
99 是「够大且仍守得住 16ms 碰撞拍」的实测上限（见下）。

**实现**
- `petcore/config.py` 新增 `MAX_PETS = 99` 作为**单一真值**；`_pets()`（`active_pets` 归一化）
  与旧 `config-pet-N.json` 迁移的 slot 校验从硬编码 `16` 改为读它。
- `petcore/ui/pet_manager.py`：删掉本地 `MAX_PETS = 16`，改从 config 导入（同一常量）；
  上限提示改 f-string；`range(1, MAX_PETS)` 覆盖 slot 1..98（0..98 共 99 只）。
- `petcore/ui/settings/page_pets.py`：「当前 N 只（最多 16 只）」改读常量。
- **碰撞粗筛**（不放宽上限就必然卡帧）：99 只 = 4851 对/拍，旧路径每对 2×`_body()`（1.5µs）
  + `collide()`（2.85µs）≈ 28.4ms，单拍就超过 16ms 预算。
  - `engine/collision.py` 新增 `rects_may_touch()`（轴对齐、1px 容差）并在 `collide()` 入口调用：
    内切三圆链只在矩形内/相切处相交 ⇒ 矩形分离必有 `None`，结果不变、省掉 9 对圆心距离。
  - `PetManager._tick_collision` / `_collide_pair`：按拍缓存身体框（`_rect_cache`，
    `_apply_body` 移动即时失效），只有可能接触的配对才构造 `CollisionBody`。

**验证**
- 新/改测试：`tests/test_petcore_multipet.py` +17/−3（上限改为注入 `MAX_PETS=4` 验边界，
  另加「`active_pets` 归一化接受 98、丢弃 99」）；`tests/test_petcore_p5.py` +27
  （`rects_may_touch` 的相交/相切/分离/退化矩形、`collide()` 远端返回 None、重叠仍报深度）。
- 性能（本机，2,000,000 次）：粗筛 **0.21 µs**/对；新热路径（两次缓存查表 + 粗筛）
  **0.25 µs**/对 ⇒ 99 只 × 4851 对 = **1.2 ms/拍**（旧路径 28.4 ms/拍，约 24×）。
  `collide()` 远距对从 2.85 µs 降到 0.96 µs。
- `pytest tests/test_petcore_multipet.py tests/test_petcore_p5.py tests/test_petcore_pet_push.py
  tests/test_petcore_click_listeners.py tests/test_petcore_config_extra.py -q` 全绿。

**未覆盖 / 风险**
- 没有真机开满 99 只：99 个真实窗口的内存/解码开销远大于碰撞（每只一个动画播放器），
  本刀只保证「上限放行 + 碰撞拍不炸」；满编时的实际帧率未测。
- 上限提示只在两个入口（生成失败的气泡、设置 →「多宠」的状态行）；托盘/菜单没有单独的
  「已达上限」提示（沿用既有静默返回 None）。
- `MAX_PETS` 改了名字的含义（含主宠），`active_pets` 里仍是从 0 开始的身份列表。

## L127 — 2026-10-29 — 语音首次 import 串行化 + 测试禁止真实联网合成

**主题**：跑全量 petcore 套件时反复出现 Windows 原生崩溃（access violation）。

**证据**：4 次全量跑崩 2 次，faulthandler dump 每次都停在
`tests/test_petcore_click_listeners.py` 的 `QTest.qWait`，同时有 3–4 条播报线程卡在
`edge_tts/communicate.py:47` 的模块级 `ssl.create_default_context()`，
其余线程在 `importlib._bootstrap.acquire` 等 import 锁。崩溃与本次改动无关
（不在 `_play` 路径上），但它让「全量绿」不可复现，必须收口。

**实现**
- `petcore/net/tts.py`：新增 `_EDGE_IMPORT_LOCK`，`edge_synthesize()` 的惰性
  `import edge_tts` 串行化（只有首次 import 会等锁）。生产里一个进程一个播报端口，
  但 mod 可以直接调 `SpeechPort.synthesize()`（预缓存），多线程并发首次 import 是真实姿势。
- `tests/conftest.py`：新增**会话级** autouse `_no_real_tts_synthesis`——把
  `petcore.net.tts.synthesize` 替换成「立刻抛 `NetworkError(network)`」，并把
  `service.RETRY_DELAY_S` 压到 0。必须是会话级：函数级 monkeypatch 在用例结束就还原，
  而播报线程常活到用例之后（实测崩溃残留线程正是这样拿着真实实现去联网）。
  需要真实合成的用例本来就注入 `synthesizer=`（`SpeechService` 的接缝），不受影响。

**验证**
- 全量套件（`pytest tests/test_petcore_*.py tests/test_architecture.py -q`）：加桩后连续两跑
  **2372 passed, 1 skipped**（每跑约 3 分 52 秒），不再出现 access violation；加桩前 4 跑崩 2 次。
- `pytest tests/test_petcore_speech.py tests/test_petcore_speech_local.py
  tests/test_petcore_click_listeners.py tests/test_petcore_self_talk.py -q` → 66 passed。
- 真机：`.scratch/tts_click_probe.py`（不走 pytest 夹具）仍然合成并播完 3.5s —— 桩只在测试进程里。

**未覆盖 / 风险**
- 原生崩溃的根因在 CPython/Windows 的 `ssl` 首次初始化，本刀只把测试里的「并发首次
  import」消除，并给生产加了串行；没有直接复现「单线程首次 import 崩」的路径。
- 测试里不再覆盖真实 edge-tts 合成（网络边界，符合「只 mock 不可确定的外部边界」）。

## L128 — 2026-10-29 — 联网检索失败一律退回纯 API 回答

**主题**：用户要求「联网失败也不要不回答，而是只走纯 API 回答」；先看主分支口径再对齐。

**主分支现状**（`pet/chat/providers.py`，`stream()` → `_research()` → `_stream_answer()`）：
planner 失败在第 0 轮发一条状态行「联网检索不可用（…），将直接回答」并返回空串，工具失败
把「搜索失败：…」当工具结果喂回模型；`stream()` 无论如何都会继续 `_stream_answer()`。
petcore 的 `research()` 逻辑与它一致——**但调用点没有保险**：`stream_chat()` 里那句
`notes = research(...)` 裸调用，检索阶段任何非预期异常（回调/解析/以后新增的分支）
都会直接冒泡成「本次回复未保存」。

**实现**（`petcore/api/chat.py`）
- `stream_chat()` 的检索调用包 `try/except Exception`：失败记一条
  `web search degraded to a plain answer: <异常类型>`（**只记类型**，异常文本可能带 URL/查询串），
  发状态行「联网检索不可用，将直接回答」，`notes=""`，照常发正式请求。
- `inject_search_context()` 也包一层：拼接失败发「联网资料无法并入提问，将直接回答」，
  用**原始会话**发正式请求。
- 取消语义不变：检索失败时若用户已点停止，返回 `cancelled=True` 而不是继续发请求。

**验证**
- `tests/test_petcore_api_chat.py` +128：planner 500 → 仍拿到「你好！」且正式请求的 messages
  与原文一模一样；planner 返回烂 JSON → 仍回答；`research` 抛 `RuntimeError` → 仍回答且状态行
  不含异常文本；`inject_search_context` 抛错 → 仍回答且用原始会话。
- `pytest tests/test_petcore_api_chat.py -q` → 20 passed；全量见本轮最终结果。

**未覆盖 / 风险**
- 只保证「检索失败不阻断回答」；如果**正式回答**自身两次都传输失败，仍会报「网络不可达」
  （那是聊天链路，不是检索，按 L122 的重试口径处理）。

## L129 — 2026-10-29 — 探针 churn 收口：隐藏即停采、停采不缩行、FPS 目标生命周期、残留会话清扫

**主题**：FPS/ETW 探针在「隐藏 / 折叠 / 换目标」三条路径上白烧；岛的 GPU/FPS 段还会随采样状态翻转折叠计划（1Hz 振荡）。

**根因**
1. `DynamicIsland.stop()` 不通知宿主：隐藏/停用只停了 net/sys/disk，GPU/FPS 由 `IslandProbeHub` 持有，要等下一次 1Hz 心跳才收到空集——隐藏后白采满一拍（FPS 是一整场 ETW 会话 + 子进程）。
2. 停采会缩行：`hub.sync(set())` 清掉两段快照 → 行变窄 → 折叠计划重排 → 段不在 `+N` 里 → 下一拍重新起采 → 快照回来 → 变宽 → 折叠……；「已启用但还没有第一份快照」走同一条跳变。
3. FPS 目标没有记忆：目标正常退出（退出码 0）后仍对同一个死 pid 重开会话；`set_target` 连名字变了都算换目标；没读数时每 5 秒解析一次前台，目标刚退出时最密集。

**实现**
- `ui/island.py`：`stop()` 末尾 `_sync_sampling()`；`set_visible(False)` 无条件收口；新增 `set_probe_sampling()`（定宽占位，盒样本/单位与真实读数同一套）；`_metric_segments` 在「探针已接上但还没有快照」时渲染同宽占位，`unsupported`/未接探针仍不占位；`set_probe_sync_callback` 接上宿主时重建行。
- `island/segments.py`：`PROBE_BOX_TEXT` / `PROBE_UNITS` 成为占位宽度的单一真值。
- `ui/probes.py`：停掉的段推 `set_probe_sampling` 而不是清快照（`clear_probe_snapshot` 只留给关停/停用）；`unsupported` 缓存 60s 且只推一次；`_retarget_fps` 改成「活着就不读前台 / 没读数 ≤5s 一次 / 目标退出后 30s」，同 pid 不重复 `set_target`，前台是桌宠自己时清成无目标；`shutdown(wait_seconds=1.0)`。
- `platform/fps.py`：`target_gone`/`gone_pid`（退出码 0 = 目标没了，同 pid 不再重开，只有 `set_target(新 pid)` 解除）；`set_target` 同 pid 幂等；`MIN_SESSION_SECONDS=3` + `TARGET_CHANGE_DEBOUNCE=2`；`shutdown(1.0)`；进程级一次残留 ETW 会话清扫（`logman query -ets` / `logman stop <name> -ets`，守护线程，只停属主已死的 `dsh-pet-fps-*`，解析是纯函数，查询失败不关闸，logman 不可用只记原因）。
- `petcore/AGENTS.md`：两条探针口径改成「停采推定宽占位」「已接探针但还没首读占同宽占位」，并新增 FPS 目标生命周期/残留清扫一条。

**验证**
- 全量 `pytest tests/test_petcore_*.py -q` → 2462 passed, 1 skipped（248.5s；本批最终全量 2478 passed）。
- 定向 5 个文件 → 160 passed；`fps_probe` 连跑 5×（42 passed/次）；`ruff check pet/ tests/ petcore/` → All checks passed；`python -m petcore --check --strict` → exit 0。
- 实测（`.scratch/fps-probe-churn-2026-10-29/measure.py`，offscreen，200 次中位）：`hub._poll()` 377µs；`set_probe_snapshot()` 177µs；`set_probe_sampling()` 176µs；`island.stop()` 空闲 9.6µs / 首次 0.60ms；停采前后 plan 完全一致（width 636）；`parse_logman_sessions()` 7.0µs；真机 `logman query -ets` 43/179ms（rc=0，0 个我们前缀的会话）。

**未覆盖 / 风险**
- 真机 ETW 仍因权限（access denied）采不到任何真实 FPS；`logman stop` 路径没有在真机上命中过残留会话。
- `MIN_SESSION_SECONDS=3` 比主分支 1.0s 保守：换游戏最多延迟约 3s 才换会话，属刻意的反 churn 取舍。
- 探针段只要宿主接了探针且段启用就总占一行定宽；整机 unsupported 依赖推一次 `unsupported` 快照退出占位。

## L130 — 2026-10-29 — 音乐取词单飞 + 身份稳定 + 失败负缓存 + 采样降频

**主题**：用户报「用 QQ 音乐同步歌词时线程泄漏、CPU 持续提升」。根因不是永久泄漏，而是
每次取词新开 `music-runtime` 线程 × 每曲 3 次 × 每次 6 跳 HTTP，且播放中曲目身份抖动
（艺人字段晚一拍）会 `_begin_track` 清歌词/重置配额；失败不缓存，SMTC 管理器每拍重建、
暂停时每拍整桌面 `EnumWindows`。

**实现**（`examples/petcore-mods/media.music/host/`）
- `runtime.py`：新增 `LyricFetchWorker`（唯一常驻 `lyric-fetch` 线程、单槽队列、每个
  job 一个 cancel event；`submit()` 覆盖待处理并取消在飞；`stop()` 有界 join 1s）与
  `_LyricJob`；`poll()` 不再在 GUI 线程内联采样（worker 死掉只记一次原因并尝试重启）；
  播放分支同名降级身份记别名（保留曲目/歌词/配额，只重锚进度）；失败按**歌名**记
  60s 负缓存（容量 64、按过期清理）；`_cache()` 的 mkdir 移到取词线程；传输命令改用
  `spawn_task` 接缝，`background` 保留给取词。
- `lyrics.py`：新增 `LyricsCancelled`，`cancelled=` 贯穿 `fetch_lyrics` → 三源 → `_get_json`，
  每跳 HTTP 前检查；取消结果不记失败/冷却/负缓存。
- `sampling.py`：`WINDOW_PAUSED_INTERVAL=2.0` + `WindowCache`（按目标缓存、时钟可注入）；
  暂停时窗口枚举 2s 一次，SMTC/WASAPI 仍 1s；`absent`/`playing` 行为不变。
- `now_playing.py`：`_ThreadCache`（每线程 manager + 会话列表，`SESSION_REFRESH_SECONDS=2.0`）；
  选中会话的曲目/进度仍每拍读；选中会话失效时下一拍重列。
- 测试：`tests/test_petcore_music_mod.py` +305、`test_petcore_music_sources.py` +299，
  覆盖 100 次快速切歌并发取词 ≤1、同名别名不重建、60s 负缓存、stop join、
  暂停窗口枚举降频、`music_enabled=false` 不唤醒 worker、取词取消 1 跳内收手。

**验证**
- 定向：96 passed（music_mod + music_sources，6.86s）；加相关 5 个文件 → 176 passed（7.64s）。
- `ruff check` 改动文件 → All checks passed；本批最终全量 2478 passed, 1 skipped。
- 计数口径：取词并发峰值 1、待处理 ≤1、每首 1 条 `lyric-fetch` 线程；暂停时窗口枚举
  3/5 拍（SMTC+WASAPI 5/5）；取消在 1 跳内结束。

**未覆盖 / 风险**
- 真机 SMTC/WASAPI 计时未测（无播放器会话）；只有假端口/计数验证。
- `stop()` 仍可能留一条在飞 HTTP 调用 ≤8s（有界 join + 结果按 generation 丢弃，记 warning）。
- 负缓存按歌名，60s 内同名不同曲共用一次封锁（刻意的降抖动取舍）。
- `runtime.background` 语义改为取词接缝、传输命令用 `spawn_task`；仓外 fake 需要跟着改。
- `media.music` 版本仍是 1.2.0（未发新扩展包）；`dist-extensions/media.music` 未重打包。

## L131 — 2026-10-29 — DSH 联动动画不再每 2 秒重启

**主题**：用户报「和 DSH 联动后，DSH 一思考桌宠 CPU 就很高」。DSH 忙碌期每 ~1.5s
一条 busy 事件，`ai.agent` 每 `ai_agent_min_interval_seconds`（默认 2s）请求一次
`play_animation`；联动动作是 10s 级单次片段，于是每 2s 打断一次：WebM 终止旧
ffmpeg + 新起一个，FrameSeq 被 `start()` 拉回第 0 帧，整轮思考都在重启解码。

**实现**（`examples/petcore-mods/ai.agent/host/`）
- `links.py`：新增 `AGENT_ANIMATION_MIN_SECONDS=8.0` 旋转地板；`LinkEngine` 记
  `_last_anim_name/_last_anim_at`；`_on_busy` 先过 `_should_request(now)`：
  当前动画名 == 上次请求名且 `locked=False` → 让 clip 播完，不重发；名字已换/被接管
  也要等地板过。旧 Core 无 `animation_state()` 时退回旧节流，不猜。
- `runtime.py`：`_Bridge/AgentLinkRuntime` 增加 `animation_state()`（端口缺失/异常
  返回 None）；`reset_idle()` 只在**本 runtime 确实请求过 loop**（`_requested_loop`）
  且 Core 仍报 loop 时才 `release_loop()`，一次性动作不再误清唱歌/报时的循环。
- 测试：`tests/test_petcore_agent_mod.py` +173，覆盖「仍在播 → 只 play 一次」、
  「名字换了 → 地板后才请求」、「locked 仍受地板约束」、旧桥无方法退回节流、
  done 不误清别人的 loop。

**验证**
- `pytest tests/test_petcore_agent_mod.py tests/test_petcore_agent_state_wiring.py -q`
  → 95 passed；`ruff` 通过；本批最终全量 2478 passed。
- 纯状态机模型（60s 忙碌、1.5s 事件、10s clip）：HEAD 20 次 `play_animation`
  （每 3s）→ 新 5 次（每 12s）。

**未覆盖 / 风险**
- 8s 地板先于 `locked` 判断：探头/击飞接管期间也最多 8s 一次请求（Core 会降级到
  idle），有界但非立即。
- `state.loop` 不能证明 loop 是本 mod 的；用 `_requested_loop` 补了所有权判断，
  但仍不是跨进程租约。
- `ai.agent` 版本号未 bump，设置页「最小间隔」文案仍指请求间隔（实际轮换有 8s 地板）。

## L132 — 2026-10-29 — 全屏连拍确认 + 任务管理器排除

**主题**：1Hz 全屏探测单拍命中就 hide/show 全部宠；Task Manager 置顶最大化、UWP
覆盖层、焦点闪动会让状态在 10s 内翻 4 次，每次都是一轮隐藏/恢复 + clip 停/起。

**实现**
- `ui/fullscreen_hide.py`：`confirm_polls`（默认 2）连拍确认；候选结果要连续 N 拍
  一致才翻转，中途抖动作废；配置关闭/显式恢复仍立即执行；`stop()`/关闭清候选。
- `platform/fullscreen.py`：`_SKIP_PROCESSES` 增加 `taskmgr.exe`。
- 测试：`tests/test_petcore_fullscreen_hide.py` 27 passed；新增 20 拍 hit/no-hit
  交替 → 只 hide 1 次、restore 1 次；原即时语义用例显式传 `confirm_polls=1`。

**验证**
- 真机脚本（`.scratch/fullscreen-flap/probe_flap.py`）：HEAD 20 拍交替 21 次
  `set_visible` → 新版 1 次。
- `ruff` 通过；本批最终全量 2478 passed。

**未覆盖 / 风险**
- 进入/离开真全屏晚一拍（1Hz 下 ~1-2s）；启动时已在全屏最多多露 1s。
- 独占全屏 D3D 游戏、钉住的 UWP/永在最前窗口未逐一实机验证。

## L133 — 2026-10-29 — 对话「网络不可达」修复：5xx 换路 + 钉直连可撤销 + 联网工具失败说明

**主题**：用户报「AI 对话仍偶发网络不可达，但 API 一直能连」。根因是
`net/http.py::urlopen` 在 `HTTPError` 分支直接抛：系统代理/VPN 返回 502 时不会换直连，
502 又被分类为 `network`；`_force_direct` 钉 300s 且钉住后不再试代理；流式请求重试
仍走同一代理优先路由。另 `research()` 只回 notes，无法让模型知道联网工具失败。

**实现**
- `petcore/net/http.py`：`urlopen(..., routes=None)` 单次调用路由计划（True=代理，
  False=只直连）；500/502/503/504（无 Retry-After）在确有另一条路时用
  `clone_request()` 换路重试；`DEFAULT_DIRECT_PIN_SECONDS` 300→45；钉住直连失败
  会再试一次代理，成功/两路都失败都清钉子；`pollute` 只作用于默认调用。
- `petcore/api/chat.py`：`STREAM_RETRY_ATTEMPTS=3`、`STREAM_ROUTE_PLANS=(默认, 只直连,
  只代理)`；仅在 `progress.emitted` 为假时重试，`HttpStatusError`/取消不重试；
  `research()` 返回 `ResearchOutcome(notes, searched, failed)`（planner 失败/工具失败/
  预算超时都算 failed）；`failed and not notes` 时给 system 消息补
  「联网检索暂时不可用，请直接回答并在开头说明未联网核实」+ 状态行。
- `petcore/AGENTS.md`：补 5xx 换路/钉直连 TTL/流式路由计划/ResearchOutcome 四条口径。
- 测试：`test_petcore_http_safety.py` +187、`test_petcore_api_chat_stream.py` +311、
  `test_petcore_api_chat.py` +211、`test_petcore_ai_mod.py` +163，覆盖代理 502→直连 200、
  200 后断流→强制直连、IncompleteRead 换路、Retry-After 不换、钉直连失败回代理、
  检索失败→纯 API + system 提示且不触发 `chat.error`。

**验证**
- 定向 4 个文件 → 115 passed；`ruff` 通过；本批最终全量 2478 passed。
- 本机无真实代理/VPN 502 与真实 SSE 断流，全部用假 opener/响应验证，未产生真实网络流量。

**未覆盖 / 风险**
- 超时现在也是可重试分类：无 token 的流最多可等 3×timeout（默认 180s）才报错。
- 配置了代理时，真 5xx 会多一次请求（planner 与正式回答各一次）才失败。
- `urlopen` 最终仍抛原始 `urllib.error.HTTPError`（市场 304/探测需要 `code`），
  `_open_response` 再统一转 `NetworkError`。

## L134 — 2026-10-29 — 仅左键穿透/右键交互 + 可调 Q 弹 + 帧路径/碰撞表/锁屏收口

**主题**：用户要「点击穿透只穿透左键，右键代替旧左键单击；右键长按菜单，左键长按
拖动；弹性系数/次数/时长可调、可关闭」；同批收口单宠常驻 16ms 碰撞表、隐藏宠输入
轮询、每帧中间 pixmap 与锁屏/睡眠不停播。

**实现**
- `engine/pointer.py`（新，零 Qt）：`PointerArbiter` + `POINTER_DRAG_HOLD_MS=250` /
  `POINTER_MENU_HOLD_MS=350`；左键快速单击→转发，长按→拖动；右键快速单击→点击反馈，
  长按→菜单；拖拽中右键→弹弓。
- `platform/win.py`：`forward_left_click()` 临时置 `WS_EX_TRANSPARENT` + `SendInput`
  左键 down+up，80ms 后恢复；`_user32()` 声明 64 位安全 argtypes；逐像素轮询不再因
  `click_through` 整窗穿透，按住/转发期间不抢写样式；`stop()/resume()` 随显隐停/开。
- `ui/pet_window.py`：穿透模式左右长按计时、右键菜单松手弹出、`contextMenuEvent`
  吞掉；`_finish_drag_release/_launch_slingshot` 抽出复用；`pause_animation/resume_animation`
  （锁屏/睡眠，保留当前帧）；`_play_spec` 锁屏期间不拉新动画；隐藏时停输入轮询。
- `engine/squash.py` + `ui/effects.py`：`squash_geometry(strength=1.0,bounces=1)`；
  `click_squash_bounces`（1–5，默认 1）/`click_squash_strength`（30–200，默认 100）/
  `click_squash_ms`（80–1000，默认 220）三键；关闭开关时所有 Q 弹入口直接 return。
- `ui/renderer.py` + `ui/pet_window.py`：`frame_pixmap` 只做 QImage→QPixmap；缩放/
  镜像改到 `paintEvent` 的目标矩形（按原比例适配、左上对齐、镜像绕目标中心）；不再
  每帧 `transformed()` 造中间 pixmap。
- `ui/pet_manager.py`：碰撞表只在 ≥2 只宠时启动（Coarse 16ms），单宠 0 次唤醒；
  `collision_enabled` 由 `_tick_collision` 每拍早退（按配置停表会让实时开关起不来）。
- `platform/session.py` + `ui/app.py`：`SessionWatcher` 处理 `WM_WTSSESSION_CHANGE`
  （lock/unlock）与 `WM_POWERBROADCAST`（suspend/resume），回调
  `CoreHost.pause_for_session/resume_after_session`；`WTSRegisterSessionNotification`
  声明 HWND argtypes。
- `pet/frameseq_clip.py` + `petcore/media/clip.py`：`FrameSeqClip` 增加可选
  `timer_type`（默认 Precise 不变）与 `rearm_same()`；petcore 帧表用 Coarse 并传
  `rearm_same`，同 clip 重播不再拉回第 0 帧。
- 设置页：互动与物理「点击与音效」新增弹跳次数/弹性强度/持续时间三行（跟随开关
  置灰），差量写入与取消回滚；`pages_core` 穿透 hint 改为仅左键语义；托盘气泡文案同步。
- 测试：新增 `test_petcore_pointer.py`（11）、`test_petcore_platform_win.py`（3）、
  `test_petcore_paint.py`（2）；扩展 interaction（+6）、renderer（改写）、settings
  （+1）、config_scope（+1）、multipet（+1）、session（+1）。

**验证**
- 本批相关 360 passed（window/settings/tray/menu/agent/fullscreen/multipet/playback/
  interaction/renderer/platform/session/config/pointer/frameseq）。
- 全量 `pytest tests/test_petcore_*.py tests/test_architecture.py -q` → 2478 passed,
  1 skipped（211.67s）；`ruff check pet/ tests/ petcore/ examples/petcore-mods/` 全绿；
  `git diff --check` 无空白错误；`python -m petcore --check` 通过。
- 微基准（QPixmap 绘制路径，不含 DWM 合成，80 帧均值）：461×259 0.73→0.47 ms/帧
  （−36%），1920×1080 5.99→2.93 ms/帧（−51%）。

**未覆盖 / 风险**
- 左键转发是「松手时一次 down+up」：下层拖拽/框选/滚动条拖动不支持，hover/光标形状
  不穿透，提权窗口（UIPI）收不到合成输入；非 Windows 仍是旧整窗穿透（右键不可用）。
- 右键菜单在松手时弹出（长按只做闸门），不是按住期间就弹；这是为避免菜单把 release
  当选中。
- 锁屏停播只覆盖宠物播放/碰撞/输入轮询；灵动岛探针、音乐采样仍按各自节拍运行。
- 真机鼠标端到端（下层按钮/右键菜单/长按拖动）需本机实机验收；本机微基准不能代表
  弱机独显/DWM 合成路径。

## L135 — 2026-10-29 — 帧表节拍回到 PreciseTimer（"2 倍速仍偏慢"）

**主题**：用户报「新版本动画播放看着有点慢，开到 2 倍速还是慢」。定位为 L134 把
帧表定时器改成 `CoarseTimer` 的回归。

**实现**
- `petcore/media/clip.py`：`create_frame_clip()` 改回 `FrameSeqClip(frames_dir)`
  （默认 `PreciseTimer`），删掉"Precise 会把系统计时器精度钉在 1ms"的注释——该理由
  经 A/B 实测站不住（`timeBeginPeriod(1)` 前后 QTimer 均值 41.8 / 42.1ms 相同；
  能观测到的是节拍均值，不是系统分辨率）。
- 新增 `tests/test_petcore_frame_pacing.py`（+2）：实现用的必须是 `PreciseTimer`、
  间隔 24fps 1x=42ms / 2x=21ms。

**验证**
- 真素材（`shenshen/frameseq/click/…`，241 帧 / 24fps，4 秒窗口，offscreen）：
  Precise 1x **23.84fps**、2x **47.62fps**；Coarse（改前）1x **21.33fps**、
  2x **32.00fps**。QTimer 基准：Windows 颗粒 15.625ms，Coarse 21ms→31.3ms、
  Coarse 42ms→47.2ms。
- `tests/test_petcore_frame_pacing.py`、`test_frameseq_clip.py`、`test_frame_path_waste.py`
  全绿；`ruff check` 干净。

**未覆盖 / 风险**
- Precise 的到达抖动仍在（mean 41.8ms，单拍 31/47ms 交替），与旧版主分支逐位一致；
  没有引入"按截止时间追帧"的调度器（会改变"未到货不跳帧"的既有语义）。
- 帧率是 offscreen 计时器统计，不含 DWM 合成与真实桌面负载；真机观感需用户确认。

## L136 — 2026-10-29 — SMTC 硬超时降级（音乐识别卡死自愈）+ peak 判据

**主题**：用户报「音乐播放识别不到了」。实机取证：Windows SMTC 服务的
`GlobalSystemMediaTransportControlsSessionManager.RequestAsync()` **永不返回**
（纯 Python 与 PowerShell 5.1 的 .NET WinRT 投影都超时 → 系统级），而我们的"超时"
是 `asyncio.wait_for(_run_sync(...))`——卡的却是同步 COM 调用，`wait_for` 不会触发。
后果链：采样线程卡在 `_collect()` → `_inflight` 永远 True → 此后每一拍 `poll()` 直接
return → 识别永久停摆；设置页「识别诊断」也一直转圈。

**实现**
- `now_playing.py`：`SmtcSample.degraded`；新增 `SmtcReader`（常驻
  `music-smtc-reader` 线程 + 单槽请求 + `SMTC_READ_TIMEOUT=0.5s` 硬超时（首读 ×4，
  `SMTC_FIRST_READ_TIMEOUT=2s`，避免把冷启动误报成卡死）+ `SMTC_DEGRADED_SECONDS=30s`
  降级窗 + 窗到期仍卡则退役旧线程重探一次（`SMTC_MAX_STUCK_THREADS=2` 次上限，
  之后 `SMTC_GIVEUP_REASON` 明说"已停止重试"）+ 读取线程空出来即自动解除 +
  `stop()` 有界、线程内 `close_thread_loop()`）。每条线程一对独立事件，退役线程
  不会和新线程抢请求。
- `sampling.py`：`Sample.smtc_degraded`；退化不短路 audio/window 两源。
- `recognizer.py`：`smtc_degraded` 不走 `STATUS_ERROR`（那是"本拍抖动、保留上一拍"），
  直接判窗口标题源；新增 `AUDIBLE_PEAK=0.005`——同播放器 paused 证据要看 peak：
  有声音（SMTC 状态落后）→ 认在播；peak≈0（残留 Active）→ 判暂停并标
  `Decision.soft_paused`。
- `runtime.py`：`start()` 建 `SmtcReader`、`_collect()` 在**采集时**解析接缝
  （`_smtc_callable()`：有 reader 走有界读取，否则用测试替身/同步接缝），`stop()` 先
  停采样线程再收 reader；`poll()` 的 `INFLIGHT_LIMIT_SECONDS=3` 看门狗命中时把
  "采样线程无响应"的退化样例**直接交给判定层**（只放开标志的话，worker 真卡死时
  状态机会永远停在最后一拍）；`_collect_safely()` 由 worker 与同步路径共用；
  退化日志开始/恢复各一条。
- `runtime.py` 的 `SOFT_PAUSE_CONFIRM_TICKS=2`：peak 判据的迟滞——PAUSED 的缺席
  宽限是 0，单拍静音（淡出/间奏）不许立刻 release + 藏气泡（否则 1Hz 释放—重播）。
- `settings_page.py` 的识别诊断改用**模块级共用**的一条 reader（每次点击新建会在
  卡死机器上每次漏一条卡在 COM 里的线程）；`scripts/music_probe.py` 也走
  `SmtcReader`；`diagnostics.py` 在退化时显示「SMTC：读取超时（已降级…）」。
- 测试：新增 `test_petcore_music_smtc_timeout.py`（8，含窗到期重探与重试上限）、
  `test_petcore_music_sampling_watchdog.py`（6，含 reader 接管链与 soft-pause 迟滞）；
  `test_petcore_music_sources.py` 新增降级 2 条 + 同播放器 peak 2 条（原「残留 Active」
  用例改为 peak=0.0）。

**验证**
- 本机 SMTC 卡死现状下 `scripts/music_probe.py --all --seconds 4`：第 1 拍
  2155.9ms（首读宽限 2s）返回并在诊断里写「读取超时（已降级：用播放器窗口标题 +
  音频会话识别）」，第 2 拍 6.2ms（降级窗内立即返回），**不再挂死**。
- 同一现场下 `MusicRuntime` 全链路（`.scratch/verify_music_runtime.py`）：第 1 拍
  2000ms 等满首读宽限并降级，第 2–7 拍 15–31ms 全部 `absent / degraded=True`；
  退出时 `stop()` 对卡死 reader 记一条 "still stuck"（有界等待 0.2s），不阻塞 GUI。
- 全量 `pytest tests/test_petcore_*.py tests/test_architecture.py
  tests/test_pr_report_discipline.py tests/test_petcore_docs.py -q` → **2826 passed,
  1 skipped（199.95s，最终代码）**；`ruff check` 干净；
  `python -m petcore --check --strict` → `strict.problems=[]`。

**未覆盖 / 风险**
- SMTC 卡死是系统级的：代码只能绕过去并自愈，不能修 Windows 媒体服务本身。窗到期
  会重探（最多 2 条卡死线程），但"服务恢复后能否切回"只由单元测试证明，未在真机
  （重启播放器/电脑后）复验。
- 降级期只有窗口标题 + WASAPI 两源：浏览器内播放/标题无歌名的场景识别弱于 SMTC。
- `peak ≥ 0.005` 是经验门限；迟滞只给"同播放器 + 窗口源在播"这一条路径（2 拍），
  真静音 2 秒后仍会判暂停。
- 卡死的 reader daemon 线程无法回收（Python 不能杀线程）；重试上限内最多 2 条，
  进程退出即释放。

## L137 — 2026-10-30 — 用户反馈八组需求：动画治理 / 聊天语义 / 语音 / API / DSH / 记忆

**主题**：把 2026-10-30 的用户反馈拆成八组落地：动画随机开关与分类、聊天语义动作、
聊天字体/人设/表情随机、语音输入与语音对话、API 本地模型与 opencode 预设、
DSH 双向通道与同窗 DSH 页、长期记忆与联网口径。v4.4.0 发布线保持冻结，全部落在
`petcore/`。

**实现**
- 动画治理：新增 `petcore/engine/animation_policy.py`（12 类、`auto_off`/`blocked`、
  `anim_random_auto`）；`BehaviorDirector` 按策略过滤四类候选；`PetWindow` 的待机/
  点击/拖拽/摸头/手动/事件入口统一走闸门；菜单按 `manual_allowed` 过滤；设置页
  「互动与物理 → 动画」加总开关、概率、动画树与「全部允许/仅手动/禁用」。
- 聊天语义：新增 `ai.chat/host/animation_semantics.py`（关键词规则 + 中文名匹配 +
  防重复）；首个 delta 播思考动作、done 按完整回复播语义动作，受
  `ai_animation_enabled` / `ai_animation_thinking` / `ai_animation_min_seconds` 控制，
  隐藏/循环/接管态不抢；`ai.agent` 六态动作池（thinking/attention/success/error）
  保留 8s 地板与「还在播就不重发」。
- 聊天 UI：`chat/styles.py` 的字号改为基准字号比例派生 + 字体族占位，新增
  `chat_font_size`/`chat_font_family`（只作用对话窗，`apply_appearance` 实时推）；
  新增 6 个人设预设 JSON + `ai_persona` 解析（预设 / custom / 未知回退）；
  `easter_egg.selected_image()` 改用注入 RNG 的 `rng.choice`（修「随机只放第一张」）。
- 语音：新增 `platform/mic.py`（WinMM waveIn，零新依赖）、`net/asr.py`（OpenAI 兼容
  multipart）、`speech/asr.py`（HTTP / Windows System.Speech 兜底）、
  `ui/asr_channel.py`（单次录音线程 + 信号）；对话窗/快速气泡加话筒，
  `asr_auto_send` 控制是否直接提交；`ai_voice_reply` 用现有 TTS 朗读回复；
  `page_voice.py` 加「语音输入」段；TTS 本地段标题改为「自定义 / 本地部署 TTS」。
- API：`ApiService` 增 `models_path`/`extra_headers`/`allow_lan_http`/`preset`；
  `api/headers.py` 对 opencode 主机补自己的 UA + 稳定 `x-opencode-session`；
  `api/models.py` 拉 `/v1/models`；内置 DeepSeek 默认 + OpenCode 免费（可选）+
  Go/Ollama/LM Studio/llama.cpp/vLLM 预设；首次启动 `ApiStore` 空文件时播种
  DeepSeek + OpenCode 免费；页内可拉模型、填自定义头、允许私网 http。
- DSH：新增 `integrations/dsh-pet-channel`（HTTP+SSE+审批+`pet_control` 工具与
  `/pet say|play|notify` 兜底，命令写 `dsh-petcmd-*.jsonl`）；`petcore/net/dsh.py`
  客户端；`ui/chat/dsh_controller.py` + `dsh_view.py`，聊天窗内「AI / DSH」切换；
  历史只读镜像 + 「带入 AI 对话」按钮；`ai.agent` 消费 `pet/command`（动作名校验、
  1s 限流、隐藏态不播）；设置页「常规 → DSH 连接」安装/检查；`dsh_install.py`
  备份 `package.json` 后写 dependencies/bundles 并建目录联接。
- 记忆：新增 `ai.chat/host/memory.py`（global/character 两域 JSONL、中文 bigram
  检索、上限淘汰）；用户说「记住…」写入，提交时按问题取 top-K 注入 system；
  设置页「长期记忆」开关/注入条数/上限/打开目录/清空当前角色。

**验证**
- 聚焦：`test_petcore_animation_policy.py`（12）、`test_petcore_playback.py` 策略 4 条、
  `test_petcore_ai_animation.py`（10）、`test_petcore_ai_persona.py`（4）、
  `test_petcore_asr.py`（12）、`test_petcore_api_presets.py`（10）、
  `test_petcore_dsh.py`（8）、`test_petcore_ai_memory.py`（6）全绿；
  `test_petcore_agent_mod.py` 102、`test_petcore_chat_ui.py` 等既有套件全绿。
- 全量 petcore 子套件：`pytest -q tests/test_petcore_*.py` = **2560 passed / 1 skipped**
  （196.60 s）。全量 `pytest -q` 在 66% 处原生崩溃（exit 139，崩点旧版
  `test_quick_chat.py::test_switch_character...`）；该用例单独跑通过，
  `test_petcore_music_* + test_quick_chat` 组合 216 passed，按旧套件顺序性环境问题登记。
- `ruff check petcore/ examples/petcore-mods tests/`、`python -m petcore --check`、
  docs/architecture/CI 门禁通过。
- 真机：WinMM 录音 2 秒得到 63,662 字节 RIFF/WAV；系统识别对已知 TTS 语音
  （“你好今天天气怎么样”，UTF-8 hex `e4bda0…e6a0b7`）识别正确；opencode 免费端点
  带官方要求的头仍返回 `429 FreeUsageLimitError`（Retry-After 34556s）；
  Ollama/LM Studio/llama.cpp/vLLM 端口均未启动。
- 微基准：106 段素材行为掷骰 27.9µs/次；语义匹配 59.2µs/次；500 条记忆检索
  ≈4.8ms/次（每轮一次）。

**未验证 / 风险**
- DSH 真机双向未连测：本机 DSH 桌面端未运行；通道插件只做了 `node --check` +
  `verify_import.mjs`，Python 客户端对本地 fake HTTP/SSE 服务验证。
- opencode 免费档按用户选择保留为**可选**预设；DeepSeek 仍默认。上游若继续 429，
  UI 文案会说明「仅限 OpenCode 客户端/需 Go」。
- 本地 ASR HTTP 端点未起服务；只验证了 Windows System.Speech 兜底。
- 未构建安装包（SAC 强模式下新 exe 会被拦）；PyInstaller spec/build 脚本已加
  `integrations` 数据目录。

## L138 — 2026-10-31 — 安装包构建与实机启动（petcore 0.2.0）

**主题**：把 L137 的用户反馈批次打成可分发的 Windows 安装包，并补上 L137 登记为
「未验证」的打包与启动证据。只出产物，不改产品代码。

**产物**（输出目录被 `.gitignore` 的 `rel-*/` 忽略，不进版本库）

- `rel-user-feedback-2026-10-31/dsh-pet-core-setup.exe`：305,172,872 字节，
  SHA256 `0d84c6f0a2775221945e8622e4784b2997bb2a2653bb22ed24360d7720fe5c54`，
  ProductVersion `0.2.0`，未签名。
- 同目录 onedir `dsh-pet-core/`（633 MB，`dsh-pet-core.exe` 15,686,539 字节）、
  `check.json`、Inno 用 spec。
- 这是**最终产物**：早一版 `rel-user-feedback-2026-10-30b` 打于 L139/L140 之前
  （设置页彩蛋提示与聊天语义词表都还是旧版），已删除，避免装错。

**构建命令与环境**

```
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/build_petcore.ps1 \
  -Clean -Installer -OutputRoot rel-user-feedback-2026-10-31
```

PyInstaller 6.22.3 / PySide6 6.12.0 / CPython 3.12.10；Inno 压缩 120.7 s，整条构建
约 9 min（Qt + OCR 收集 + 852 MB lzma2）。本机
`VerifiedAndReputablePolicyState=0`（SAC 关闭），所以 `-SignThumbprint` 未传，
未签名产物可直接运行。

**随包内容核对**（列目录实测，不是推断）

- `_internal/integrations/{dsh-pet-bridge,dsh-pet-channel,hermes-pet-bridge}` 齐全，
  `dsh-pet-channel` 的 `index.js` / `cordis.patch.yml` / `locale` 都在。
- `_internal/petcore/menu_templates/petcore-default-v1.json`、
  `_internal/assets`（166 MB，角色 106 段动画）、
  `_internal/resources/{presentmon,rapidocr_models}`。

**打包冒烟**（`build_petcore.ps1` 内置 `--check --strict`，构建门已通过）

`check.json`：`version=0.2.0`、`menu_template=json`、`characters[0].animations=106`、
`diagnostics=[]`、`strict.problems=[]`、`self_talk_images.builtin_readable=24`。

**实机启动记录**（打包产物，不是源码树）

```
dsh-pet-core\dsh-pet-core.exe --instance smoke1031b
```

- 12 s 后进程存活（PID 10624，WorkingSet 96.2 MiB）。
- 枚举到 1 个可见窗口：标题 `dsh-pet-core-smoke1031b`，矩形 `1285,675 461x259`
  —— GUI 真正渲染出桌宠窗口，不是「进程活着但没有窗口」。
- `logs/petcore.log` 只有 `starting petcore 0.2.0`，无 error/traceback。
- 首次启动播种生效：`api.json` 同时生成 `deepseek`（默认）与 `opencode-free`，
  `preset`/`models_path`/`extra_headers`/`allow_lan_http` 四个新字段都在。
- 测完删除 `%APPDATA%\dsh-pet-core-smoke1031b`，未触碰用户真实数据目录。
- 同一套冒烟对 `rel-user-feedback-2026-10-30b`（更早一版）也做过一次，数字一致
  （PID 6764 / 100.9 MB / 同一矩形）。

**未验证 / 风险**

- DSH 真机双向仍未连测（本机 DSH 桌面端未启动），安装包只保证插件随包分发。
- 安装向导本身没跑完一遍：目录页预选、可选扩展勾选、卸载器（`unins000.exe`
  由安装时现场生成）都没有真机交互验证。
- 未签名：对外分发会触发 SmartScreen 提示；本机 SAC 关闭所以能直接运行。

## L139 — 2026-10-31 — 彩蛋「指定头像」提示文案跟随随机抽图

**主题**：L137 把 `EasterEggCatalog.selected_image()` 的兜底从「取首张」改成
`rng.choice`，但设置页同一行的提示仍写「无效或留空时回退到图片目录中的首张可读
图片」，与新行为相反。只改文案，不改行为。

**改动**
- `petcore/ui/settings/page_interaction.py`：`指定头像` 行 hint 改为
  「无效或留空时从图片目录里随机抽一张。」（+1 行 / −1 行）。

**验证**
- `QT_QPA_PLATFORM=offscreen pytest -q tests/test_petcore_easter_egg.py
  tests/test_petcore_settings.py tests/test_petcore_settings_coverage.py`
  → 66 passed（16.79 s）。
- 代码路径复核：头像有效 → 用头像；无效/留空 → `picker.choice(candidates)`。

**未验证 / 风险**：无（纯文案）。

## L140 — 2026-10-31 — 聊天语义动作：补齐随包素材词表 + 修生气误命中

**主题**：准备安装包验收时，拿随包角色（`shenshen`，89 个随机动作）真实素材名跑了一遍
`choose_animation`，发现 12 条规则里有 4 条在这套素材上永远落空、1 条会误播、
1 条漏词。

**修改前实测**（素材名取自 `assets/characters/shenshen/videos/random`）

- `开心` / `你好` / `谢谢` / `哇` → `None`：规则词表里没有这套素材的用词
  （雀跃庆祝、宅舞、摇摆舞、旋转展示、屈膝礼仪、被吓一跳）。
- `我很生气` → `悠闲哼歌`：`angry` 词表里有单个「哼」，把「哼歌」判成生气。
- `唱首歌吧` → `None`：词表只有「唱歌」，没有「唱首歌」。

**改动**（`examples/petcore-mods/ai.chat/host/animation_semantics.py`，+20 / −7）

- `happy` names 加 `雀跃/庆祝/宅舞/摇摆/旋转`。**不写「跳舞」**：那是 `music` 的
  专属名，两边都认会让「开心」误播唱歌类素材，
  `test_choose_animation_returns_none_without_material` 正是在守这条。
- `angry` 关键词与 names 都去掉「哼」，加 `气急败坏`。
- `greet` / `thanks` names 加 `礼仪` / `屈膝`；`surprise` 加 `吓` / `吓一跳` / `被吓`。
- `music` 关键词加 `唱首歌` / `唱一曲`。
- 解析顺序、打分与「绝不编造素材名」的契约不变：仍然只从
  `PetHandle.animations()` 给的真实名字里挑，落不到就返回 `None`。

**改后实测（同一套 89 个素材名）**

| 用户说 | 播 |
|---|---|
| 今天好开心啊，笑一个 | 可爱宅舞 |
| 困了想睡觉 | 打瞌睡被惊醒 |
| 我饿了想吃点东西 | 吃Token |
| 帮我看看这段代码 | 写代码 |
| 你好呀 / 谢谢你 | 女仆屈膝礼仪 |
| 哇居然是这样 | 被吓一跳 |
| 摸摸头 | 可爱宅舞 |
| 唱首歌吧 | 悠闲哼歌 |
| 有点难过 | 工作状态-垂头叹气冒汗 |
| 我很生气 | 玩游戏气急败坏 |

**验证**

- 新增 `tests/test_petcore_ai_animation.py::test_pack_vocabulary_actually_resolves`
  与 `::test_angry_does_not_use_humming_material`——样本用的是随包素材名，不是自造词。
- `pytest -q test_petcore_ai_animation.py test_petcore_ai_mod.py
  test_petcore_easter_egg.py test_petcore_settings.py` → 124 passed（17.92 s）。
- `ruff check examples/petcore-mods/ai.chat tests/test_petcore_ai_animation.py`：通过。

**未验证 / 风险**：规则仍是关键词表，不是语义模型；换角色或换素材包后，若词表里没有
对应用词仍会落空（返回 `None`，不猜一个不相关的动作）。

## L141 — 2026-10-31 — 反馈批次 2：聊天窗 / 语音 / 直播 / 设置搜索 / DSH / 人格 / 会话 / 自语

**主题**：用户十项反馈的整批实现。范围：60 个已跟踪文件（+3792 / −257）与 9 个新文件。
本轮不触碰 `pet/` 产品代码，`origin/main`（v4.4.0）保持冻结。

**改动（按用户可见能力分组）**

1. **聊天输入区**：`petcore/ui/chat/window.py` 把 `setStretchFactor` 从错误的
   `chat_main_layout` 移到 `ai_layout`，输入区/工具条固定自然高度；短会话时间线
   改为底部对齐（顶部 Expanding spacer）。`widgets.py` 在输入法 preedit 期间清空
   placeholder，commit/取消/FocusOut 恢复。实测 1100×800：composer 125px、
   toolbar 34px、scroll 575px（修复前 agent 实测 286/195/314）。
2. **音频设备**：`config.py` 新增 `music_audio_device(_name)`、`asr_device_name`；
   `media.music/audio_sessions.py` 支持端点枚举/默认端点短缓存/按 `(线程,设备)` 重建
   manager；runtime 每 5s 跟随默认、每 30s 确认手选设备，拔掉回退默认；
   设置页新增「音频输出设备」（异步枚举 + 刷新），语音页新增「麦克风」下拉。
3. **直播兼容 v2**：`config.py` 新增 `stream_capture_keycolor` / `stream_key_color`；
   `pet_window.py` 色键模式不透明、整窗填底色、标题带 slot、`_apply_native_style`
   每次改旗标后重放 exstyle、直播模式置顶用 `SetWindowPos` 不重建 HWND；
   `bubble.py` 色键模式同样铺底色；新增 `platform/capture_probe.py`（PrintWindow/
   BitBlt 非黑占比 + exstyle 报告）；设置页三选一 + 色键颜色 + 捕获自检。
4. **设置搜索**：`settings/base.py` 的 `page_search_texts` 增加 QTreeWidget 项、
   `searchKeys` 注册与 accessibleName；`reveal_text` 支持树项展开/选中、下拉/列表项
   滚动定位（不改值）、页内标签切换；`page_extensions.py` 搜索命中折叠组自动展开。
   `SettingsCard.add_row(search_keys=...)` + 公开 `register_search_terms`。
5. **DSH 审批**：`dsh_view.py` 审批卡状态机（提交后置灰、resolved 终态、404/断线
   恢复按钮、同 id 去重、上限 20）；`dsh_controller.py` 新增 `DshHub`（进程内单条
   SSE，多窗订阅）+ generation guard；`net/dsh.py` `close_active()` 停止读循环；
   `integrations/dsh-pet-channel/index.js` 同会话只留一个 pending、10 分钟审批超时。
6. **语音实时预览**：`platform/mic.py` `peek_wav()`；`ui/asr_channel.py` 新增
   `partial` 信号与增量识别循环（同刻最多一个在飞，sapi ≥1.5s）；聊天窗/快速对话
   显示预览且不覆盖用户已输入文本；语音设置页新增实时开关与刷新间隔。
7. **人格卡**：6 个包内预设扩写为多段（默认 3–6 句）；`persona_presets.py` 新增
   `PersonaStore`（用户目录、同 id 覆盖内置、保存/删除/恢复/校验），runtime 按
   mtime 缓存合并池；设置页新增新建/复制/重命名/保存/恢复内置/删除与名称栏。
8. **会话分节/归档**：`session_store.py` 新增 `archived_at`（schema 仍 1）、
   `list_sessions(include_archived=)`、`archive()`；新 `history_buckets.py`（今天/
   昨天/本周/本月/更早，零 Qt）；侧栏组头可折叠、三点菜单归档/删除（删除确认默认
   取消）、「已归档」开关；controller 在归档/删除/隐藏当前会话时切到下一个或新建。
9. **右键菜单**：模板新增 `ai_hub`「AI 助手」，收 `ai.chat:open/look_screen/proactive`；
   `migrate_p12_menu` 幂等迁移，且不会在没有任何 AI 项的精简布局里凭空注入 hub。
10. **自言自语**：默认候选台词 7 句；新增 `self_talk_daily_*` 三键与 `daily.py`
    （日期确定性种子 + 连说切段）；runtime 每天每 slot 第一句用每日句，状态落
    `feature-data/fun.self-talk/daily.json`；设置页新增每日池/连说/今天预览/
    恢复内置/试听连说。

**验证**

- `QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest tests/test_petcore_*.py -q`
  → **2609 passed, 1 skipped**（219.14 s）。
- `.venv/Scripts/ruff check pet/ tests/ petcore/ examples/petcore-mods/ai.chat
  examples/petcore-mods/media.music` → 通过。
- `.venv/Scripts/python -m petcore --check --strict` → `strict.problems=[]`。
- `node --check integrations/dsh-pet-channel/index.js` → 通过。
- 真机色键窗口探针（`capture_probe.probe_window`，Windows）：
  `exstyle=8`（仅 TOPMOST）、`toolwindow=false`、`noactivate=false`、
  `layered=false`、`printwindow=1.0`、`bitblt=1.0`。
- 同机透明直播窗口对照：`layered=true`、`printwindow=0.2454`、`bitblt=0.0`
  （证明 BitBlt 黑屏、PrintWindow 有画面，色键模式两条读回都有画面）。
- 真机音频端点：`list_endpoints()` 冷 **149.36ms** / 暖 **0.01ms**；默认端点
  **3.34ms/次**（仅跟随默认时每 5s 一次）；麦克风枚举到 1 个输入设备。
- 新增/更新测试：`test_petcore_chat_ui.py`（布局/IME/分节/归档/预览）、
  `test_petcore_settings_search.py`、`test_petcore_music_audio_device.py`、
  `test_petcore_asr.py`、`test_petcore_ai_persona.py`、`test_petcore_sessions.py`、
  `test_petcore_history_buckets.py`、`test_petcore_self_talk_daily.py`、
  `test_petcore_stream_mode.py`、`test_petcore_capture_probe.py`、
  `test_petcore_dsh.py`、`test_petcore_menu_layout.py`、`test_petcore_extensions_nav.py`。

**未验证 / 风险**

- DSH 桌面端本机未运行：审批/超时/单连接只经 fake HTTP/SSE 与静态审计；
  真机双向仍需用户环境复测。
- OBS/直播姬未安装：色键窗口只做了 GDI 读回与 exstyle 探针，未做真实 OBS 抓屏；
  OBS 端需选「Windows 10 (1903 及以上)」并加色度键滤镜。
- ASR 真实麦克风只验证设备枚举与替身 partial；未把真实语音内容写入日志或报告。
- 音频设备插拔只经 fake COM 测试与默认端点比较；未做真实耳机插拔。
- 安装包构建与安装向导交互在下一节/PR 报告记录。

**复审修正（同日）**：独立只读复审发现 2 高 / 7 中 / 5 低，逐条修正：
设置页 `confirm_save` 先关 keycolor 再关 mode；运行期色键切换显式重建 HWND 并在
`apply_stream_window(keycolor=True)` 清 `WS_EX_LAYERED`（气泡 show 后同步宿主样式，
真机复测 layered=false / bitblt=1.0 / exstyle=0x8）；手选音频设备回退标志保持到
30s 检查；ASR 间隔改为识别返回后再计时；识别诊断传手选设备；DSH 历史回放清
`_approvals`、hub 回放连接状态、审批按钮 RuntimeError 保护；p12 hub 插到最靠前
AI action 原位置；会话流式守卫 `stream_session_id` 防迟到 done 写进新会话；人格卡
整窗保存自动落卡；捕获自检移 QThreadPool。最终全量 **2614 passed, 1 skipped**。

## L142 — 2026-10-31 — 反馈批次 2 安装包（0.2.0）构建与实机冒烟

**主题**：批次 2 全部代码/文档后的最终安装包。构建输出隔离在
`rel-feedback2-2026-10-31`（`rel-*/` 已被 `.gitignore` 忽略，不进版本库）。

**命令**

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/build_petcore.ps1 \
  -Clean -Installer -OutputRoot rel-feedback2-2026-10-31
```

**产物**

- `rel-feedback2-2026-10-31/dsh-pet-core-setup.exe`：305,200,979 字节；
  SHA256 `b2ae9d148423df8339f1853d6fc9189652185579bc3a0363afef943e472d1c67`；
  ProductVersion 0.2.0，未签名（本机 SAC 关闭，可直接运行）。
- 同目录 onedir `dsh-pet-core/dsh-pet-core.exe`：15,727,438 字节。
- 随包内置特性含新增 `petcore/builtin/features/fun.self-talk/host/daily.py`
  （打包后实际路径已确认）。

**构建门**

- `verify presentmon`：文件存在、大小 980,320、sha256
  `b2a706bc6ad475749e3b7e3409263aa1e6906d45bdcf993f6dbc0f660188f1af`、x86-64 通过。
- `smoke --check --strict`：`menu_template=json`、`characters=1`、
  `builtin_images=24`、`strict=True`，`strict.problems=[]`。
- Inno Setup `Successful compile (120.969 sec)`，输出 setup。

**打包产物实机冒烟**

- 临时数据根 `%TEMP%\petcore-pkgprobe-*` 写入 `stream_capture_mode=true`、
  `stream_capture_keycolor=true`，启动 onedir exe（`--instance pkgprobe1031`）。
- 枚举到 `dsh-pet core #0`；`capture_probe.probe_window`：
  `exstyle=8`（仅 TOPMOST）、`toolwindow=false`、`noactivate=false`、
  `layered=false`、`printwindow=1.0`、`bitblt=1.0`。
- 日志只有 `starting petcore 0.2.0`，无 traceback；测完删除临时数据根。

**未验证 / 风险**

- 安装向导 GUI 交互（目录页、扩展勾选、卸载器）未在用户机器上逐页点击；
  setup 只验证了编译产物与 onedir 冒烟。
- OBS/直播姬、DSH 桌面端、真实麦克风内容仍未真机验证（见 L141 / PR 报告）。
