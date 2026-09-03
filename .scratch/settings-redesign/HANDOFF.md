# Settings redesign handoff

Updated: 2026-09-03 (after cross-platform CI regression fixes)

## Workspace

- Worktree: `/Users/ushio/github/dsh-pet-indesktop-settings-redesign`
- Branch: `feature/codex-settings-redesign`
- Upstream merge commit: `8a2c8e4` (checkpoint before merge: `f5f998c`).
- Post-merge fixes and evidence are committed on this feature branch; use `git log -3` for the exact hash.
- Do not edit the original dirty worktree at `/Users/ushio/github/dsh-pet-indesktop`.

## Fixed decisions

- Seven stable settings capability domains; platform is availability, not navigation.
- Versioned `modern-default-v1`; legacy menu remains a compatibility mode.
- One-level user submenus; settings and quit are always recoverable.
- Draft preview and runtime menu resolve the same Menu Layout Tree.
- Advanced settings use at most one disclosure layer.
- Delivery requires full TDD, current macOS GUI evidence, capability-fake coverage for Windows/Linux, docs, and `git diff --check`.

## Completed

- Tickets 01/02 core: default tree, resolver, safety fallback, registry, tree-driven real QMenu.
- Ticket 03 core: draft editor, visibility/order/root-submenu controls, custom submenu, responsive split, availability state, validation before commit.
- Seven-domain settings navigation and representative ownership tests.
- Shared advanced disclosure for colors, collision parameters, and model generation.
- AI domain flattened into the shared page shell; the previous nested page no longer collapses to a narrow Cocoa column.
- Native advanced toggles replaced by the QSS-backed `SettingsDisclosureHeader`; expanded/collapsed Cocoa captures pass without the former custom-paint crash.
- Menu editor uses settings-card tree styling, semantic column sizing, and a headerless menu-like preview.
- Shared typography, spacing, card radius, initial focus, and semantic sidebar icons aligned across all seven domains.
- Setting labels/descriptions are exposed on their controls; generic buttons now have a visible keyboard focus ring.
- `docs/SETTINGS-CHANGE-GATES.md`, Qt UI review skill, concise AGENTS pointers, research and implementation log.
- Project skills validated with `/opt/miniconda3/envs/voice-picker-dev/bin/python`.
- `git diff --check` passed after the final handoff refresh.
- macOS Cocoa screenshots cover every page in light wide, light compact, and dark wide matrices. See `docs/SETTINGS-REDESIGN-UI-ACCEPTANCE.md`.
- `origin/main` at `4eb8c37` merged with Provider list, notification, custom-agent and issue-42/LTR changes retained; conflict surface passed.
- Future-default actions migrate into old user trees at deterministic template anchors and remain editable.
- Menu preview refreshes after cross-parent remove/insert/move/reset events; preview/runtime structural equivalence is tested.
- Reorder, promote, reset, draft-before-save and migration editor contracts are covered.
- Reusable UI development skill: `.agents/skills/desktop-pet-ui-style/`; validated with `voice-picker-dev`.
- Real Cocoa accessibility matrix covers all seven pages at 720×760, 125% fonts and extreme copy under `docs/screenshots/settings-redesign/iteration-3-accessibility/`.
- Responsive copy and Provider composite controls were fixed from screenshot-discovered failures.
- Related regression: `49 passed`; full suite: `745 passed, 8 skipped` (`753 collected`).
- macOS `webm-chat` package passed PyInstaller, encoding check and ad-hoc codesign in `/private/tmp/dsh-pet-macos-build.0ZBbuF`; `imageio-ffmpeg 0.6.0` is present.
- ToggleSwitch dependency rows now hide instead of merely disabling: self-talk, menu translucency, island, easter egg, collision, Agent event details, and Windows proactive-screen settings all have explicit parent/child contracts.
- Composite visibility reasons prevent nested controls from reappearing while an ancestor remains off; empty advanced sections hide with their rows.
- Real Cocoa default, expanded, and compact-expanded matrices are in `docs/screenshots/settings-redesign/iteration-4-toggle-*`.
- Screenshot-driven Agent sound overflow is fixed by `ResponsiveToggleActionRow` and a 720px geometry contract.
- Wide page headings share the centered content-width contract; the menu domain expands to 1240px while compact mode stacks the editor and preview.
- Menu commands are grouped into four dropdowns. Submenus have confirmed explicit deletion, and empty source submenus are pruned after move/promote/drag.
- Quick launch uses content-sized two-line rows, grouped add actions, bounded list growth, and a compact empty state.
- Real Cocoa layout evidence is in `docs/screenshots/settings-redesign/iteration-5-layout-*`; the focused quick-launch image confirms the single-row scrollbar regression is gone.
- Quick-launch check indicators accept real pointer clicks despite custom item widgets.
- Explicit menu appearance themes now apply to the open Settings System immediately; custom-painted controls inherit the same resolved state.
- Quick Chat opens on the next event turn, waits for detectable Qt popups to close,
  ignores Cocoa's transitional deactivation during first activation, then closes
  on genuine top-level window deactivation. This avoids both native menu teardown
  and an active Settings System window leaving a blank native Quick Chat surface,
  while preserving the existing request-stop close path. Real Cocoa evidence is
  in `docs/screenshots/settings-redesign/quick-chat-focus-regression-macos.png`
  and `docs/screenshots/settings-redesign/quick-chat-self-talk-activation-macos.png`.
- Dark-menu easter-egg title and hint inherit the configured foreground; related coverage is `84 passed` plus one real Cocoa render test, with full suite intentionally skipped under the documented low-risk gate.
- Easter-egg hover inherits the configured theme hover surface; four focused tests and one real Cocoa render test pass, with no full-suite rerun for this isolated token fix.
- The Menu domain keeps the seven-domain sidebar intact and uses in-page `SettingsTabContainer` tasks for Menu Layout, Quick Launch, and Appearance. Search activates the owning tab before scrolling to a result.
- Menu Layout now owns aliases, semantic icon overrides, and explicit separator nodes. Legacy section boundaries materialize as editable separators; runtime feature toggles retain their nodes and render them disabled with a reason instead of filtering or reordering the tree.
- The editor retains native tree semantics and adds an enabled-state column plus responsive `排序 / 移动到 / 插入 / 自定义 / 更多` command groups. Focused menu coverage is `54 passed`.

## Current TDD state

- Last RED: a wide `ResponsiveActionRow` reserved the height of its three-row
  compact layout (`98px` instead of `32px`), and a WebM reader cancelled while
  metadata was being returned still requested the first frame.
- Last GREEN: the two focused regressions plus the four original CI regressions
  pass (`6 passed`); complete Menu Layout/WebM lifecycle coverage is
  `69 passed`; the full suite passes in one process outside the restricted
  loopback sandbox (`785 passed, 9 skipped`).

## Exact next step

Rerun GitHub Actions on Windows and macOS. Do not infer real Windows visual
acceptance from the local macOS run. Full-suite
loopback HTTP tests require sandbox/network permission; keep real credentials
isolated for any rerun:

```bash
PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring PYTEST_ADDOPTS='-p no:cacheprovider' QT_QPA_PLATFORM=offscreen /opt/miniconda3/envs/mobility_client/bin/python -m pytest -q
```

Before PR, rerun `git diff --check` and inspect `git status --short` for unrelated local files.

## Remaining acceptance work

1. Validate the resolver-backed preview and seven settings pages on real Windows/Linux hosts; do not create a second semantic model for platform-specific visuals.
2. Report clearly that Windows/Linux currently have model/capability coverage only.

## Risks

- The editor preview is a resolver-backed hierarchy, not an embedded native `QMenu`; visual parity must be assessed during GUI QA.
- Internal `QTreeWidget` drag/drop is constrained by flags, but keyboard alternatives remain the authoritative accessible path.
- Full-suite Qt process can abort after a test failure if a background `QThread` is still alive; use `-x` to diagnose first failures.
- Reusing the provenance-marked workspace `build/macos` directory can fail during PyInstaller BUNDLE creation; a fresh dist directory under `/private/tmp` passed.
