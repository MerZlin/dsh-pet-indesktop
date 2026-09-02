# Settings redesign handoff

Updated: 2026-09-02 (after upstream merge and final local acceptance)

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

## Current TDD state

- Last REDs: tree-shape preview stayed stale; future default action was absent; long titles and Provider actions overflowed under enlarged fonts.
- Last GREEN: all new focused contracts, 49 related tests, 745-pass full suite, package build, and all current macOS screenshot matrices.

## Exact next step

No unfinished local implementation ticket remains. If work continues on another host, run the real Windows/Linux visual matrix first; do not infer visual acceptance from capability fakes. Keep real credentials isolated for any full rerun:

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
