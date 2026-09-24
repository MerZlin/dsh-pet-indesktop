# 工程规范化与可重复验证基线 PR 报告

> **基线**：`9d294ea`（Phase 2 Core 插件运行时 checkpoint）　**分支**：当前维护分支　**日期**：`2026-09-24`
> **范围**：341 个已跟踪文件，累计 `+12115 / -9726`；本报告及其索引登记在生成后作为证据文档新增。
> **非目标**：不实现 Worker 插件、不重构 Core 自动更新协议、不删除或移动旧文档、不纳入 `plugin-roadmap-demo.html`。

## 核心特性

本轮把已经完成的 Phase 2 checkpoint 之后的工程规范化工作封存为可复现门禁：行为测试分类和覆盖率开发依赖、Ruff E4/E7/E9/F/I 与格式基线、mypy 核心边界、pre-commit、Markdown 链接检查、跨平台统一检查入口，以及 Python 3.11–3.13 质量矩阵和 Windows/macOS/Linux 桌面 CI 门禁。运行时公共 API、CLI、DLC 协议和 Core 自动更新行为没有被重写。

**红线 / 不变量**：`python -m pet`、现有测试行为、DLC 协议、`pet/updater.py`、`pet/update_settings.py`、Starter DLC、公共配置/CLI 兼容性保持不变；未跟踪的 `plugin-roadmap-demo.html` 不进入提交。

## 修改文件说明

本节的逐文件增删数字由以下命令从基线生成：

```powershell
& 'D:\DELL\Git\cmd\git.exe' diff --numstat 9d294ea HEAD -- . ':!docs/PR-REPORT-ENGINEERING-NORMALIZATION-2026-09-24.md'
```

下面的机器生成清单覆盖基线到最终 HEAD 的已跟踪变更，并排除本报告自身；报告文件自身在新增文件清单中单列。增删数字不是行为覆盖率数字。

| 文件 | 新增 | 删除 | 改动意图 |
|---|---:|---:|---|
| `.github/workflows/build-linux.yml` | 8 | 19 | 统一 CI 质量门禁、Python 版本矩阵、pip 缓存、Qt offscreen 环境和构建前 smoke；不改变产品功能。 |
| `.github/workflows/build-macos.yml` | 8 | 20 | 统一 CI 质量门禁、Python 版本矩阵、pip 缓存、Qt offscreen 环境和构建前 smoke；不改变产品功能。 |
| `.github/workflows/build-windows.yml` | 8 | 20 | 统一 CI 质量门禁、Python 版本矩阵、pip 缓存、Qt offscreen 环境和构建前 smoke；不改变产品功能。 |
| `.github/workflows/pr-test.yml` | 60 | 29 | 统一 CI 质量门禁、Python 版本矩阵、pip 缓存、Qt offscreen 环境和构建前 smoke；不改变产品功能。 |
| `.gitignore` | 6 | 4 | 忽略覆盖率与本地质量检查生成物，避免构建/验证产物进入提交。 |
| `.pre-commit-config.yaml` | 35 | 0 | 增加与统一检查入口一致的 Ruff、mypy 和 Markdown 链接本地钩子。 |
| `LOG-INDEX.md` | 16 | 0 | 登记规范化主题日志入口和已验证提交。 |
| `LOG.md` | 33 | 0 | 记录已完成的工具链、门禁、覆盖率合并和未完成的跨平台实机验证。 |
| `README.md` | 21 | 2 | 补齐新人质量入口、Python 支持范围和完整/快速/矩阵检查命令。 |
| `SPEC.md` | 95 | 0 | 记录工程规范化质量门、覆盖率合并策略、插件边界和文档治理规则。 |
| `docs/DSH-BRIDGE-PET-EVENT-CONTRACT-2026-09-02.md` | 5 | 5 | 修复相对路径、索引互链或标记历史文档状态；不删除、不移动历史资料。 |
| `docs/INDEX.md` | 4 | 0 | 登记规范化 PR 报告及已修复/保留的文档入口。 |
| `docs/PET-STATE-MACHINE-AND-REPETITION-2026-09-02.md` | 5 | 5 | 修复相对路径、索引互链或标记历史文档状态；不删除、不移动历史资料。 |
| `docs/SETTINGS-REDESIGN-Q4-CLASSIFICATION-RESEARCH.md` | 6 | 6 | 修复相对路径、索引互链或标记历史文档状态；不删除、不移动历史资料。 |
| `pet/__init__.py` | 1 | 1 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/__main__.py` | 4 | 4 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/agent_cost.py` | 1 | 1 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/agent_event_normalizer.py` | 112 | 18 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/agent_event_protocol.py` | 67 | 12 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/agent_link.py` | 424 | 355 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/animation_thumbnail.py` | 12 | 7 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/app.py` | 247 | 308 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/autostart.py` | 5 | 19 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/balance.py` | 55 | 57 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/behavior_detector.py` | 194 | 81 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/catalog.py` | 122 | 130 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/chat/ai_settings_page.py` | 70 | 41 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/chat/crop_dialog.py` | 14 | 22 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/chat/geometry.py` | 1 | 3 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/chat/legacy_widgets.py` | 35 | 30 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/chat/models.py` | 177 | 45 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/chat/pet_link.py` | 7 | 7 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/chat/prompt.py` | 43 | 21 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/chat/providers.py` | 134 | 72 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/chat/service.py` | 107 | 43 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/chat/session_store.py` | 21 | 20 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/chat/settings_dialog.py` | 146 | 115 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/chat/themes.py` | 104 | 96 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/chat/widgets.py` | 57 | 87 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/child_pet_cleanup.py` | 15 | 25 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/click_sound.py` | 29 | 18 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/click_talk_dialog.py` | 4 | 12 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/collision.py` | 91 | 76 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/collision_client.py` | 144 | 158 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/collision_codec.py` | 25 | 9 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/collision_debug.py` | 1 | 1 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/collision_ipc.py` | 168 | 143 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/config.py` | 15 | 21 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/config_domains.py` | 2 | 5 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/content/__main__.py` | 9 | 8 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/content/manager.py` | 1 | 0 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/content/manifest.py` | 19 | 7 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/content/registry.py` | 24 | 11 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/context_menu.py` | 2 | 3 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/context_menus/fun_entry.py` | 16 | 14 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/context_menus/icons.py` | 97 | 47 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/context_menus/legacy.py` | 6 | 5 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/context_menus/menu_styles/common.py` | 11 | 6 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/context_menus/menu_styles/legacy.py` | 1 | 0 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/context_menus/menu_styles/modern.py` | 6 | 10 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/context_menus/modern.py` | 2 | 3 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/context_menus/quick_launch.py` | 2 | 1 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/context_menus/registry.py` | 131 | 91 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/context_menus/shared.py` | 88 | 94 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/decode_fanout.py` | 29 | 37 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/desktop_notify.py` | 2 | 6 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/dsh_control.py` | 37 | 10 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/dsh_responder.py` | 3 | 1 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/dsh_state.py` | 5 | 4 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/dynamic_island.py` | 77 | 86 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/edge_probe.py` | 27 | 18 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/exploration_watchdog.py` | 150 | 91 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/exploration_watchdog_settings.py` | 84 | 66 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/festival.py` | 3 | 7 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/festival_calendar.py` | 28 | 8 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/festival_data.py` | 12 | 12 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/festival_quotes_west_game.py` | 18 | 18 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/festival_quotes_west_movie.py` | 27 | 27 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/festival_quotes_west_song.py` | 6 | 6 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/festival_settings.py` | 14 | 35 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/file_eater.py` | 16 | 21 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/file_interpret.py` | 40 | 22 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/frame_cache.py` | 1 | 0 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/fun_image_popup.py` | 6 | 12 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/golden_spin.py` | 4 | 6 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/gui_stall_sampler.py` | 11 | 14 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/harness_launcher.py` | 42 | 34 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/instance_launcher.py` | 2 | 4 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/island_chat.py` | 5 | 7 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/island_collision.py` | 47 | 48 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/library.py` | 64 | 79 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/mem_debug.py` | 13 | 10 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/menu_layout.py` | 17 | 37 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/model_access_tracker.py` | 25 | 7 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/modern_settings_dialog.py` | 89 | 77 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/movement.py` | 14 | 19 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/multi_window_shared.py` | 32 | 20 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/music_detect.py` | 3 | 5 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/music_lyric.py` | 17 | 46 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/music_lyric_controller.py` | 14 | 17 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/music_players.py` | 9 | 6 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/node_runtime.py` | 6 | 9 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/now_playing.py` | 3 | 9 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/perfstats.py` | 6 | 5 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/persona_phrases.py` | 20 | 12 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/persona_template.py` | 60 | 49 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/physics.py` | 23 | 28 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/platform_mac.py` | 8 | 7 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/platform_win.py` | 31 | 30 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/plugins/__init__.py` | 1 | 1 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/plugins/builtin/festival_reminder/__init__.py` | 4 | 2 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/plugins/capabilities.py` | 1 | 0 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/plugins/config.py` | 1 | 0 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/plugins/events.py` | 1 | 0 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/plugins/manifest.py` | 1 | 0 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/plugins/ports.py` | 1 | 0 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/plugins/runtime.py` | 2 | 5 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/predictive_prewarm.py` | 8 | 13 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/proactive.py` | 24 | 32 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/proactive_limiter.py` | 5 | 4 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/quick_chat.py` | 12 | 26 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/report_gates.py` | 7 | 7 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/runtime_cleanup.py` | 2 | 1 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/self_talk_voice.py` | 11 | 15 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/session_watcher.py` | 21 | 21 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/settings_file_interpret.py` | 2 | 3 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/settings_interaction.py` | 4 | 3 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/settings_menu_layout_editor.py` | 86 | 86 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/settings_music.py` | 2 | 4 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/settings_pet_controls.py` | 71 | 83 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/settings_theme_qss.py` | 4 | 2 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/settings_widgets.py` | 81 | 77 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/slot_manager.py` | 26 | 19 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/sound_winmm.py` | 19 | 13 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/speech_bubble.py` | 210 | 152 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/speech_bubble_text.py` | 18 | 31 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/stuck_detector.py` | 77 | 47 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/throw_egg.py` | 2 | 3 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/todo_panel.py` | 16 | 17 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/todo_reminder.py` | 26 | 31 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/uninstall_cleanup.py` | 1 | 0 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/vision.py` | 107 | 101 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/voice_chime.py` | 1 | 0 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/voice_chime_quotes.py` | 1 | 0 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/voice_chime_service.py` | 3 | 8 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/voice_chime_settings.py` | 15 | 10 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/webm_clip.py` | 122 | 145 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/win_job.py` | 46 | 38 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/window.py` | 512 | 533 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/window_alerts.py` | 111 | 67 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/window_effects.py` | 1 | 0 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/window_optional_services.py` | 10 | 0 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/window_placement.py` | 54 | 48 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pet/window_screen.py` | 16 | 15 | 按 Ruff 格式/import 基线整理运行时代码；以 Phase 2 checkpoint 为基线，不改变运行语义。 |
| `pyproject.toml` | 24 | 10 | 声明 Ruff E4/E7/E9/F/I 规则、格式基线和有原因的存量豁免。 |
| `pytest.ini` | 9 | 0 | 纳入本轮工程规范化基线，保持现有公共行为。 |
| `requirements-dev.txt` | 9 | 0 | 把 pytest-cov、Ruff、mypy、pre-commit 等工程工具固定到可复现开发依赖。 |
| `requirements.txt` | 4 | 3 | 纳入本轮工程规范化基线，保持现有公共行为。 |
| `scripts/audit_warm_cache.py` | 5 | 12 | 对检查/构建辅助脚本执行格式与 import 基线整理，并纳入统一门禁。 |
| `scripts/bench_idle_decode_throttle.py` | 4 | 2 | 对检查/构建辅助脚本执行格式与 import 基线整理，并纳入统一门禁。 |
| `scripts/bench_music_lyric_align.py` | 10 | 10 | 对检查/构建辅助脚本执行格式与 import 基线整理，并纳入统一门禁。 |
| `scripts/capture_settings_pages.py` | 11 | 36 | 对检查/构建辅助脚本执行格式与 import 基线整理，并纳入统一门禁。 |
| `scripts/check.py` | 133 | 0 | 提供跨平台质量入口；隔离 Qt/媒体时序族并合并覆盖率后执行 83% 门禁。 |
| `scripts/check_bundle_encoding.py` | 6 | 16 | 对检查/构建辅助脚本执行格式与 import 基线整理，并纳入统一门禁。 |
| `scripts/check_docs.py` | 114 | 0 | 提供无第三方依赖的 Markdown 相对链接和索引目标检查。 |
| `scripts/cleanup_mei_cache.py` | 2 | 3 | 对检查/构建辅助脚本执行格式与 import 基线整理，并纳入统一门禁。 |
| `scripts/convert_to_gif.py` | 9 | 22 | 对检查/构建辅助脚本执行格式与 import 基线整理，并纳入统一门禁。 |
| `scripts/diagnose_clipboard.py` | 56 | 49 | 对检查/构建辅助脚本执行格式与 import 基线整理，并纳入统一门禁。 |
| `scripts/fix_bridge_bundle.py` | 6 | 14 | 对检查/构建辅助脚本执行格式与 import 基线整理，并纳入统一门禁。 |
| `scripts/gen_agent_sounds.py` | 1 | 0 | 对检查/构建辅助脚本执行格式与 import 基线整理，并纳入统一门禁。 |
| `scripts/grab_screen.py` | 3 | 3 | 对检查/构建辅助脚本执行格式与 import 基线整理，并纳入统一门禁。 |
| `scripts/make_click_sound.py` | 6 | 5 | 对检查/构建辅助脚本执行格式与 import 基线整理，并纳入统一门禁。 |
| `scripts/make_icon.py` | 2 | 0 | 对检查/构建辅助脚本执行格式与 import 基线整理，并纳入统一门禁。 |
| `scripts/pos_diag.py` | 7 | 8 | 对检查/构建辅助脚本执行格式与 import 基线整理，并纳入统一门禁。 |
| `scripts/probe_multi_screen_area.py` | 12 | 13 | 对检查/构建辅助脚本执行格式与 import 基线整理，并纳入统一门禁。 |
| `scripts/slim_bundle.py` | 3 | 11 | 对检查/构建辅助脚本执行格式与 import 基线整理，并纳入统一门禁。 |
| `scripts/verify_bundle_qt.py` | 1 | 0 | 对检查/构建辅助脚本执行格式与 import 基线整理，并纳入统一门禁。 |
| `scripts/verify_bundle_tts.py` | 2 | 5 | 对检查/构建辅助脚本执行格式与 import 基线整理，并纳入统一门禁。 |
| `scripts/verify_session_end_shutdown.py` | 12 | 13 | 对检查/构建辅助脚本执行格式与 import 基线整理，并纳入统一门禁。 |
| `tests/conftest.py` | 143 | 6 | 注册行为导向 pytest marker 和测试收集边界。 |
| `tests/diagnose.py` | 16 | 16 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/helpers/foreground_holder.py` | 1 | 0 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/manual_ssl_proxy_check.py` | 44 | 35 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/smoke.py` | 1 | 1 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_agent_cost.py` | 11 | 7 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_agent_link.py` | 394 | 334 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_agent_link_dep_specs.py` | 37 | 42 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_agent_link_threads.py` | 56 | 25 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_alert_queue.py` | 36 | 29 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_animation_prewarm_phase2.py` | 2 | 1 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_animation_thumbnail_cache.py` | 2 | 3 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_api_provider_list.py` | 1 | 0 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_app_startup_fallback.py` | 1 | 0 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_architecture.py` | 23 | 14 | 校准 Ruff 格式化后的设置页行数预算；只更新维护基线，不改变业务行为。 |
| `tests/test_autostart.py` | 2 | 6 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_balance.py` | 48 | 42 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_behavior_detector.py` | 34 | 23 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_bubble_text_scale.py` | 15 | 31 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_bundle_encoding_check.py` | 2 | 3 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_bundle_slim.py` | 1 | 3 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_chat_attachments.py` | 8 | 5 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_chat_safe_emit.py` | 2 | 2 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_chat_service.py` | 10 | 6 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_chat_shared_behavior.py` | 4 | 12 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_chat_subsystem.py` | 172 | 141 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_chat_themes.py` | 81 | 66 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_check_docs.py` | 30 | 0 | 覆盖文档链接检查器的合法链接、锚点、代码块和缺失目标边界。 |
| `tests/test_check_script.py` | 49 | 0 | 锁定统一检查入口、隔离测试族和覆盖率合并命令的行为。 |
| `tests/test_child_pet_cleanup.py` | 21 | 35 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_click_self_talk_speech.py` | 7 | 5 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_click_sound.py` | 31 | 19 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_click_talk_dialog.py` | 2 | 3 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_clip_current_image.py` | 9 | 15 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_clip_restart_paths.py` | 11 | 12 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_collision.py` | 279 | 99 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_collision_ipc.py` | 459 | 201 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_collision_settings.py` | 7 | 5 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_collision_window.py` | 114 | 77 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_config_domains.py` | 117 | 84 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_config_instance.py` | 2 | 0 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_config_key_migration.py` | 25 | 12 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_config_schema.py` | 4 | 5 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_config_sound.py` | 29 | 16 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_content_dlc.py` | 30 | 20 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_context_menu_lifecycle.py` | 7 | 9 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_context_menu_position.py` | 8 | 17 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_cursor_visibility.py` | 33 | 34 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_decode_fanout.py` | 5 | 7 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_decode_fanout_integration.py` | 2 | 2 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_desktop_pet_features.py` | 180 | 127 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_drag_move_coalescing.py` | 30 | 14 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_dsh_control_client.py` | 11 | 11 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_dsh_state.py` | 33 | 21 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_dynamic_island_balance_tier_time.py` | 20 | 14 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_dynamic_island_revamp.py` | 86 | 50 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_edge_probe.py` | 7 | 15 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_edge_reachability.py` | 2 | 3 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_effects_integration.py` | 4 | 6 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_exploration_watchdog.py` | 6 | 10 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_feature_gating.py` | 2 | 2 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_festival.py` | 88 | 55 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_ffmpeg_job_object.py` | 34 | 33 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_file_eater.py` | 1 | 0 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_file_interpret.py` | 9 | 5 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_first_batch_features.py` | 67 | 51 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_first_frame_budget.py` | 1 | 0 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_first_frame_no_gui_decode.py` | 1 | 0 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_fix_bridge_bundle.py` | 3 | 5 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_foreground_steal.py` | 23 | 29 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_golden_spin.py` | 3 | 2 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_harness_launcher.py` | 12 | 8 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_harness_lifecycle.py` | 44 | 46 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_idle_low_fps.py` | 16 | 9 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_input_controller_drag.py` | 22 | 24 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_instance_offset.py` | 13 | 10 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_island_chat.py` | 29 | 17 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_island_collision.py` | 44 | 37 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_island_content_cache.py` | 16 | 13 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_island_remote_wall.py` | 209 | 109 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_island_shell_wiring.py` | 12 | 7 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_library_priority_warm.py` | 2 | 1 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_library_warm_priority.py` | 1 | 0 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_low_priority_warm_interaction_yield.py` | 24 | 13 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_macos_activation.py` | 2 | 3 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_mem_debug.py` | 26 | 26 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_menu_layout.py` | 183 | 217 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_menu_toggle_spec.py` | 11 | 13 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_meta_cache_budget.py` | 3 | 3 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_meta_no_gui_probe.py` | 7 | 8 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_move_direction.py` | 1 | 0 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_move_sync.py` | 120 | 115 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_movement.py` | 28 | 28 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_multi_screen_interaction.py` | 12 | 20 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_music_lyric.py` | 58 | 58 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_music_player_cache.py` | 25 | 29 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_music_player_settings.py` | 3 | 8 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_music_sing_grace.py` | 4 | 10 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_music_sing_timer.py` | 3 | 0 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_now_playing_session.py` | 12 | 3 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_orphan_registry_thread.py` | 2 | 2 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_perfstats.py` | 6 | 3 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_persona_presets.py` | 2 | 5 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_persona_settings.py` | 32 | 21 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_persona_template.py` | 56 | 40 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_pet_interaction_locks.py` | 49 | 34 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_physics.py` | 6 | 3 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_plugin_runtime.py` | 5 | 7 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_pr_report_discipline.py` | 8 | 31 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_predictive_prewarm.py` | 24 | 28 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_proactive.py` | 113 | 60 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_quick_chat.py` | 2 | 1 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_recovery_hints.py` | 1 | 0 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_report_gates.py` | 64 | 53 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_requested_regressions.py` | 77 | 118 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_runtime.py` | 1 | 1 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_runtime_dependencies.py` | 3 | 8 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_second_batch.py` | 1 | 0 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_self_talk_voice_precache.py` | 3 | 6 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_semantic_event_field_contract.py` | 10 | 18 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_session_end_ffmpeg_guard.py` | 33 | 37 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_session_store_async.py` | 9 | 8 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_settings_and_resources.py` | 42 | 74 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_settings_event_gating.py` | 10 | 12 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_settings_interaction_tabs.py` | 33 | 21 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_settings_process_isolation.py` | 16 | 4 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_settings_subslot_autostart.py` | 1 | 0 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_single_process_shared.py` | 41 | 39 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_single_process_spawn.py` | 137 | 221 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_slot_and_memory.py` | 6 | 11 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_spawn_toggle_hidden.py` | 9 | 6 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_speech_bubble.py` | 22 | 29 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_stream_capture_compat.py` | 1 | 0 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_stuck_detector.py` | 12 | 16 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_switch_start_failure_window.py` | 10 | 4 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_throw_egg.py` | 2 | 2 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_throw_flight_anim.py` | 43 | 22 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_todo_reminder.py` | 24 | 25 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_tray_icon_ready.py` | 1 | 0 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_uninstall_cleanup.py` | 16 | 34 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_updater.py` | 35 | 30 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_verify_bundle_tts.py` | 2 | 6 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_vision.py` | 71 | 49 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_voice_chime.py` | 0 | 1 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_voice_chime_service.py` | 39 | 34 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_warm_landing_idles.py` | 14 | 18 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_watchdog_control_wiring.py` | 94 | 99 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_watchdog_settings_page.py` | 7 | 8 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_webm_clip_broker_feed.py` | 6 | 10 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_webm_clip_loop.py` | 51 | 31 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_webm_first_frame_lock.py` | 1 | 0 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_webm_meta_cache.py` | 2 | 4 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_webm_reader_lifecycle.py` | 10 | 15 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_window_broker_wiring.py` | 1 | 0 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_window_dpr_signals.py` | 11 | 8 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_window_effects.py` | 2 | 1 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_window_pause.py` | 3 | 2 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_window_position.py` | 4 | 5 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_window_rendering.py` | 37 | 40 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_windows_node_env.py` | 40 | 67 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/test_winmm_sound.py` | 25 | 14 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |
| `tests/win_diag.py` | 20 | 19 | 按 Ruff 格式/import 规则整理测试，并保持原有行为断言与公共 API 不变。 |

**机器统计**：已跟踪文件 `341` 个，新增 `12109` 行，删除 `9725` 行。
**新增文件（基线比较可见）**：`.pre-commit-config.yaml`, `LOG-INDEX.md`, `LOG.md`, `SPEC.md`, `requirements-dev.txt`, `scripts/check.py`, `scripts/check_docs.py`, `tests/test_check_docs.py`, `tests/test_check_script.py`。
**删除文件**：无。
**本报告新增文件**：`docs/PR-REPORT-ENGINEERING-NORMALIZATION-2026-09-24.md`；本报告只追加证据，不删除或移动旧文档。
**明确未改动**：`pet/updater.py`、`pet/update_settings.py` 无 diff；`plugin-roadmap-demo.html` 保持未跟踪。

## 实现要点

1. 质量入口统一由 `scripts/check.py` 驱动，调用当前 Python 解释器，避免本地 shell、虚拟环境和 CI 使用不同解释器。
2. 完整门禁把 WebM reader/clip/first-frame 和低优先级预热测试族放入独立 pytest 进程；三段覆盖率通过 `--cov-append` 合并，最后由 `python -m coverage report --fail-under=83` 判定。
3. `--quality` 只运行静态检查和 `unit` marker，供 Python 3.11–3.13 质量矩阵使用；`--fast` 跳过 slow 测试；`--ci` 使用完整门禁。
4. pre-commit 只挂快速、确定性的 Ruff/mypy/文档检查，不把全量 Qt/IPC/媒体测试塞进每次提交钩子；完整回归由统一入口和 CI 负责。
5. CI 分为 quality 和 desktop 两层：质量层覆盖 Python 3.11–3.13，桌面层覆盖 Windows/macOS/Linux 的 Python 3.11，并统一使用 `requirements-dev.txt` 和 `scripts/check.py`。

## 性能分析

**实测方法**：Windows 11 工作区，Python 3.11.1（解释器路径为 `E:\Program Files (x86)\Dev-Cpp\python.exe`），PySide6/pytest 使用当前 `requirements-dev.txt` 环境；Qt 使用 `QT_QPA_PLATFORM=offscreen`，每条门禁命令至少运行 1 次。

| 路径/指标 | 实测 | 归属 |
|---|---:|---|
| `python scripts/check.py --quality` | `14.75s`；`268 passed, 2738 deselected` | 新增质量入口 / 1 次本机运行 |
| 主回归（隔离 4 个时序文件） | `232.43s`；`2937 passed, 11 skipped` | 既有测试全量主体 / 1 次 |
| WebM 独立族 | `20.02s`；`31 passed` | 既有媒体时序族 / 1 次 |
| 低优先级预热独立族 | `32.21s`；`27 passed` | 既有时序族 / 1 次 |
| 合并覆盖率 | `40117 statements`，`83%`，门禁通过 | 新增覆盖率门禁 / 1 次完整门禁 |
| Ruff 格式扫描 | `347 files already formatted` | 新增格式门禁 / 每次质量检查 1 次遍历 |
| mypy | `19 source files`，无错误 | 核心边界类型门禁 / 每次质量检查 1 次 |

**结论**：

- 运行时稳态开销：本轮未向桌宠运行时增加线程、定时器、网络请求或后台轮询；新增成本发生在开发/CI 检查阶段。
- 新增路径成本：本机 `--quality` 单次 `14.75s`；完整门禁主体与两个隔离族的 pytest 子命令合计约 `284.66s`，覆盖率最终判定和静态检查另计，触发频率为本地显式检查、pre-commit 的快速子集和 CI。
- 系统调用/网络/磁盘：本轮质量脚本新增的是本地子进程、Python 导入、pytest/coverage 文件写入和 CI pip cache；不新增产品网络请求。`coverage.xml` 与 `.coverage` 已加入忽略，避免进入仓库。
- 线程/内存：检查脚本自身不常驻线程；测试仍按既有 Qt/IPC 测试需要启动临时线程/进程，但不改变产品常驻内存。未做可见桌面长期内存基准，因此不虚报桌面内存结论。

## 实机运行记录

以下是在本机 Windows 工作区直接执行的记录，不是 CI，也不是 mock：

1. `QT_QPA_PLATFORM=offscreen; python scripts/check.py --quality`：Ruff、format、mypy、Markdown 链接、compile/import smoke 全部通过；`268 passed, 2738 deselected`；命令退出码 `0`，PowerShell 计时 `14.75s`。
2. `QT_QPA_PLATFORM=offscreen; python scripts/check.py --ci`：主体 `2937 passed, 11 skipped`；WebM `31 passed`；低优先级预热 `27 passed`；覆盖率合并后 `83%`；`coverage.xml` 生成成功；命令退出码 `0`。
3. `python -m pytest -q tests/test_plugin_runtime.py tests/test_festival.py tests/test_voice_chime_service.py tests/test_app_startup_fallback.py`：用于确认 Phase 2 插件生命周期、节日提醒、语音服务和启动 fallback 的真实 Qt/offscreen 回归；结果为 `134 passed in 8.32s`。
4. `QT_QPA_PLATFORM=offscreen; python -m pytest -q`：独立全量入口通过，`2997 passed, 11 skipped, 14 warnings in 227.88s`。
5. `python -m pre_commit run --all-files`：Ruff、format、mypy 核心边界和 Markdown 链接钩子全部通过。
6. 可见桌面行为：本机本轮使用 offscreen 门禁，未把 offscreen 结果冒充为真实可见窗口、鼠标拖拽、系统通知或多屏行为；macOS/Linux 实机未在本环境执行。发布前仍需在对应系统完成手工验收。

## 测试与验证

| 门 | 命令/结果 |
|---|---|
| Ruff lint | `python -m ruff check pet tests scripts`：通过 |
| Ruff format | `python -m ruff format --check pet tests scripts`：`347 files already formatted` |
| mypy | `python -m mypy pet/plugins pet/content pet/catalog.py pet/config.py`：19 个源文件无错误 |
| 文档链接 | `python scripts/check_docs.py`：94 个 Markdown 文件通过 |
| 质量矩阵 | `python scripts/check.py --quality`：通过 |
| 完整统一门禁 | `python scripts/check.py --ci`：通过，覆盖率 83% |
| 独立全量入口 | `QT_QPA_PLATFORM=offscreen; python -m pytest -q`：2997 passed, 11 skipped, 14 warnings |
| pre-commit | `python -m pre_commit run --all-files`：4 个钩子全部通过 |
| PR 报告纪律 | `python -m pytest -q tests/test_pr_report_discipline.py`：32 passed |
| 保护文件 | `git diff --name-only -- pet/updater.py pet/update_settings.py`：为空 |
| 未跟踪演示文件 | `git status --short` 保留 `?? plugin-roadmap-demo.html` |

## 已知限制与后续

- `mypy` 仍只覆盖 `pet/plugins`、`pet/content`、`pet/catalog.py`、`pet/config.py`，未对整个历史代码库启用 strict。
- Ruff 当前启用 E4/E7/E9/F/I；B/UP/SIM 等更严格规则留待独立主题，不能与本轮格式化混合。
- 旧文档没有删除或移动；候选归档和合并必须另行列清单并获得确认。
- 可见桌面、macOS/Linux 实机、真实发布包运行和三平台人工交互仍是发布前验收项。

## 风险与回滚

- 各主题使用独立提交，可按 `git revert <commit>` 回滚；不使用 reset 覆盖用户改动。
- 如果 CI 质量矩阵因新 Python 版本出现环境差异，应先定位依赖/平台原因，不降低覆盖率阈值或使用 `continue-on-error` 掩盖。
- 如果统一检查入口需要调整，应先更新 `tests/test_check_script.py` 和本报告中的命令证据，再修改 CI；自动更新文件仍由并行会话单独维护。\n
