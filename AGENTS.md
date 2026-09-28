# Engineering guide

## Project shape

This is a PySide6 desktop-pet application. `python -m pet` enters through
`pet/__main__.py`; `PetApp` owns application-level services, `PetWindow` owns
interactive pet behavior, and `MovieLibrary` owns animation media. Keep Qt
objects on their owning thread and communicate across threads with queued
signals.

```mermaid
classDiagram
    class PetApp {
      +start()
      +switch_character()
    }
    class Config
    class PetWindow {
      +request_link_anim(name)
      +show_bubble()
    }
    class MovieLibrary {
      +movie(name)
      +movies()
    }
    class CollisionIpcSession {
      +start()
      +submit_state(state)
      +submit_leave()
      +stop()
    }
    class ChatService

    PetApp *-- Config
    PetApp *-- PetWindow
    PetApp *-- CollisionIpcSession
    PetWindow --> MovieLibrary
    PetApp ..> ChatService : optional UI
```

The IPC facade belongs to the GUI thread; `_CollisionWorker` and every
`QLocalServer`, `QLocalSocket`, and IPC timer belong to its dedicated `QThread`.
The shared kernel file lock is the coordinator authority. On POSIX, a lock
holder that fails to `listen()` because of a stale Unix socket first probes
for a live listener, and only removes the stale endpoint when nobody answers.

```mermaid
sequenceDiagram
    participant GUI as PetWindow / GUI thread
    participant IPC as CollisionIpcSession
    participant W as _CollisionWorker / QThread
    participant L as coordinator file lock
    participant Q as QLocalServer

    GUI->>IPC: submit_state(state)
    IPC-->>W: queued signal
    W->>L: try acquire
    alt lock acquired
        W->>Q: listen
        alt listen fails (stale POSIX endpoint)
            W->>Q: probe live server
            alt no live listener
                W->>Q: remove stale endpoint, listen
            end
        end
        W-->>IPC: role_changed(true, epoch)
    else lock busy
        W->>Q: connect as client, hello
        Q-->>W: welcome / snapshot / impulse
    end
```

## Change discipline

- Preserve user changes in a dirty worktree and keep generated build output out
  of commits.
- Fix behavior test-first at a public seam. For Qt/IPC regressions, use real
  event loops and process boundaries; mock only operating-system or network
  boundaries that cannot run deterministically.
- A fix is complete when the focused regression is red before the product
  change, green afterward, and verification matches the risk gate below.
- Run the full suite for shared models/config migrations, application lifecycle,
  threading/IPC, packaging/dependencies, platform branches, changes spanning
  multiple test domains, or the final accumulated branch before merge.
- Focused plus related tests are sufficient for a local presentation token or
  isolated widget behavior when interfaces, persisted data, lifecycle, and
  platform dispatch are unchanged. Record why the full suite was skipped.
- Keep `CollisionIpcSession.stop()` ordering intact: stop producers, send leave,
  close local endpoints and timers, then quit/wait for the worker thread.
- Keep QLocal test server names short. POSIX converts names to Unix socket paths,
  whose limit includes the system temporary-directory prefix.

## Delivery evidence discipline (2026-09-22 起，硬要求)

每个 PR 必须交付**三份证据**，缺一即视为未完成，审查时按缺陷提出
（模板：`docs/PR-REPORT-TEMPLATE.md`；细则：`docs/DEV-HANDOVER.md` §8.2/§8.3）：

1. **修改文件说明**：逐文件写「改了什么 + 为什么」，含增删行数（`git diff
   --numstat`）与新增/删除文件。只写「修了 bug」不算说明。
2. **性能分析**：受影响路径的**实测**数字（命令 + 环境 + 样本量），并逐条回答
   稳态开销、新增路径成本与触发频率、有无新的系统调用/网络/磁盘/线程、内存有无
   增长。**形容词不算分析**——用「可忽略 / 更快 / 优化了」代替数字视为没写。
3. **实机运行记录或报告**：在本机真实环境（不是 CI、不是 mock）跑过什么的记录：
   命令、真实输出、以及用户可见行为的确认。无法自动验证的能力，必须给出
   「为什么不能自动」的排查证据（探针结果），不许用沉默代替结论。

落地形式：报告写入 `docs/PR-REPORT-<主题>-<YYYY-MM-DD>.md`，在 `docs/INDEX.md`
的「PR 报告存档」登记（新文档入场规则），PR 描述放摘要 + 链接。
`tests/test_pr_report_discipline.py` 对本日期之后的报告强制校验四个必备章节与索引
登记；历史报告豁免。**豁免**：纯文档/文案/依赖版本号这类不改变运行行为的改动，
可只保留第 1 条，但必须在 PR 描述里写明豁免理由。

## CI cost discipline (learned 2026-09-06, PR76 CI loop)

CI 反复红的代价极高（每轮 5-10 分钟 + 诊断烧调用额度）。硬性规矩：

- **推送前必过三道本地门**：ruff、全量 pytest、受影响时序测试族的高负载
  复跑（本地 CPU 打满跑 3 遍）。缝合/脚本化改动后必须重跑 ruff——
  重复定义/残留 import 这类一眼问题不许交给 CI 去发现。
- **写时序测试 = CI 优先纪律**：新测试涉及真实线程/Qt 事件循环时，一律
  事件同步（Event/Condition）+ 宽预算（CI 慢 runner 是本地数倍慢），
  禁止固定 sleep 猜测时序、禁止赌目录枚举顺序、禁止用 monotonic 绝对值
  做回拨算术（CI runner 是新开机的，uptime 可能只有几百秒）。
- **CI 红先读日志再动手**：连续两轮修同一族测试不绿，停止重试，把族
  隔离出主套件（对齐 webm 生命周期族先例），别在 PR 门禁里赌时序。
- 诊断类排查能本地复现就不派付费子代理；派子代理必须给齐已知排除项，
  避免重复劳动烧额度。

Run focused tests before `python -m pytest -q`. Set
`QT_QPA_PLATFORM=offscreen` in headless environments. A restricted macOS
sandbox may deny Unix socket creation; rerun QLocalServer tests with local IPC
permission rather than treating errno 1 as a product failure.

## 协作经验（2026-09-29 复盘，硬规矩）

一次通宵改造（免打扰 + 今日汇总 + 天气 + 修测试）踩了 20 个坑，完整复盘见
[`docs/AI-LESSONS-2026-09-29.md`](docs/AI-LESSONS-2026-09-29.md)。最容易让用户
白跑、白等的是这五条：

1. **交付"可以让用户去跑"的东西之前，自己先跑通一次最小验证**；跑不通就先说还差什么
   （本次踩坑：稀疏检出没补 `assets/` 与 `docs/`，用户连跑三次测试全是 `FileNotFoundError`）。
2. **动有契约测试的框架，先照抄现成同类实现**。菜单里「免打扰」第一版写成**空 submenu**，
   被 `resolve_menu_layout()` 过滤 → 菜单里根本不会出现；同类实现是「播放动画」
   （模板放 action、子菜单由注册表动态生成）。
3. **改一处、扫一片**：动菜单模板 / 配置键 / 设置行 / 公开文案时，同批更新对应断言
   （`tests/test_menu_layout.py`、`tests/test_config_schema.py`、
   `tests/test_settings_interaction_tabs.py`、文案红线用例）与 `docs/INDEX.md` 登记。
4. **归因逐条看 traceback，不按"同类"打包推断**；报告里每个根因都要有原始输出支撑
   （本次踩坑：把 `MAX_PATH` 的 `FileNotFoundError` 误报成"桌面几何问题"）。
5. **长测试先问再跑**：全量一次约 4 分钟，反复跑是烧用户时间。默认只跑聚焦集 + 相邻文件；
   全量交给用户，或先问一句。
6. **测试执行权归用户（2026-09-29 起，硬约定）**：agent **不自己跑 pytest**（含聚焦集）——
   只写代码、写用例、跑静态检查（`ruff`、`py_compile`），然后把"建议的验证命令"写进交接说明；
   由用户在终端跑全量并把报告发回来，agent 按报告改。理由：用户的时间预算 + 用户机器才是
   真实环境（沙箱禁网、禁写 `%APPDATA%`，本机跑不出真结论）。
7. **生成的文件只许放一个地方（2026-09-29 起，硬约定）**：agent 产生的**非交付文件**
   （探针脚本、临时目录、日志、下载缓存、对照用工作树…）一律放**会话的 `work/`**，
   不许散落到用户仓库根目录、桌面或家目录；仓库里只允许出现"该进仓库的改动"
   （源码、测试、文档报告，以及上游本就有、检出时缺了的 `assets/`、`docs/`）；
   要交给用户看的产物放 `outputs/`。跑完测试或提交后**要清掉自己留下的临时目录**，
   并在交接里列出"我放了什么、在哪、能不能删"。
   （`.venv/` 是跑测试用的开发环境，属于例外：保留，但已在 `.gitignore` 里忽略。）
8. **定期复盘，把坑变成规矩（2026-09-29 起；节奏=每天）**：清单与"可直接粘进 App「定时任务」"的提示词见
   [`docs/RETRO-CADENCE.md`](docs/RETRO-CADENCE.md)；机械项跑
   `python scripts/retro_check.py`（仓库根不许有临时产物 / 文案红线 / 菜单中文标签 /
   文档登记 / 行数预算），红了按对应规则修。新踩的坑要先写成
   `docs/AI-LESSONS-*.md` 条目，能机器拦住的再补一条护栏测试或检查项。

## Agent skills

### Issue tracker

Issues and specs use Local Markdown under `.scratch/<feature-slug>/`. See
`docs/agents/issue-tracker.md`.

### Triage labels

Use the five canonical local triage states. See
`docs/agents/triage-labels.md`.

### Domain docs

Use the single-context layout: root `CONTEXT.md` and system ADRs under
`docs/adr/`. See `docs/agents/domain.md`.

### Qt UI review

Use `.agents/skills/qt-ui-review/SKILL.md` when reviewing settings, menus,
overlays, QSS, accessibility, or cross-platform desktop presentation.

### Qt UI development

Use `.agents/skills/desktop-pet-ui-style/SKILL.md` when adding or materially
restyling settings, menus, dialogs, overlays, or desktop widgets.

### Work handoff

For unfinished multi-ticket work, read and refresh the feature's
`.scratch/<feature-slug>/HANDOFF.md` before ending or resuming work. Keep the
exact breakpoint there; see `docs/agents/handoff.md`.

## Context pointers

找文档先查 `docs/INDEX.md`（全文档入口索引：每条一句话 + 何时必读）；
新文档入场必须按其中的规则登记并互链。高频专项指针：

- Read `docs/ISSUE-42-POSIX-COLLISION-IPC-2026-08-31.md` when changing collision
  election, QLocal IPC, coordinator locking, or their process-level tests.
- Read `docs/ISSUE-111-WINDOWS-SESSION-END-FFMPEG-2026-09-12.md` when changing
  ffmpeg spawning (`webm_clip` reader/first-frame/meta/exe probes), warm
  scheduling, or anything that runs during Windows shutdown/logoff
  (`session_watcher`, `match_shutdown`, `AppShell._on_session_end`).
- Read `docs/ONEDIR_PACKAGING.md` when changing PyInstaller specs, bundled
  resources, or platform build scripts.
- Read `docs/STABLE_BUILDS.md` when changing release/build workflows.
- Read `docs/CONTEXT-MENU-RESEARCH-AND-REFACTOR-2026-08-25.md` when changing
  context-menu structure, styling, interaction, or platform behavior.
- Read `docs/SETTINGS-CHANGE-GATES.md` before adding, moving, removing, or
  changing a persistent setting or its settings-page interaction.
- Read `docs/PERSONA-PHRASES-PRESET-STORAGE-2026-09-08.md` before changing
  dialogue preset files (`pet/persona_presets/*.json`), phrase loading
  (`persona_phrases.py`), or expression-style semantics
  (`dialogue_mode`/`dialogue_phrases`).
- Read `docs/PR-MERGE-LESSONS-2026-09-12.md` before merging PRs: stacked-PR
  conflicts after squashing the parent, budget/red-line changes that only break
  when two PRs combine, and timing-test flake discipline (poll state with a wide
  budget instead of fixed sleeps).
- Read `docs/NETWORK-PROXY-AND-VPN-2026-09-22.md` when touching any networked
  feature (music lyrics, edge-tts voice, updater, balance, vision, chat) or when
  a report sounds like "X worked yesterday and now it doesn't": the Windows
  system proxy / VPN is a first-class suspect — it made lyric fetching 20-41s
  (all three sources timed out) on 2026-09-22, while jsdelivr update checks only
  work *through* the proxy. Lyric requests deliberately bypass the proxy
  (`pet/music_lyric.py::_build_opener`); other features keep following it.
- Treat `assets/characters/<id>/videos/` plus its manifest as one character
  package; preserve relative paths and case because packaged platforms differ.
  The manifest may declare `body_box` (`[x1, y1, x2, y2]` in source pixels,
  mirror-symmetrized): the stable body rect used as the placement/edge-clamp
  anchor (`pet/catalog.py character_body_box`); measure it at alpha≥128 to
  match the mask/visual edge, and omit it to fall back to full-canvas behavior.
