# Settings redesign handoff

Updated: 2026-09-02

## Workspace

- Worktree: `/Users/ushio/github/dsh-pet-indesktop-settings-redesign`
- Branch: `feature/codex-settings-redesign`
- No commits created in this task.
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
- Project skill validated with `/opt/miniconda3/envs/voice-picker-dev/bin/python`.
- Related regression: `110 passed`.
- Full suite outside sandbox with null keyring: `680 passed, 10 skipped, 3 existing deprecation warnings`.
- `git diff --check` passed before this final handoff refresh.
- macOS Cocoa screenshots cover every page in light wide, light compact, and dark wide matrices. See `docs/SETTINGS-REDESIGN-UI-ACCEPTANCE.md`.

## Current TDD state

- Last red: menu editor retained default interactive column sizing and truncated action labels.
- Last green: accessibility/visual contracts, 110 related tests, 680-pass full suite, and all current macOS screenshot matrices.

## Exact next step

Add the future-default-action migration test first (ticket 01): a customized v1 layout that predates a newly registered default action must gain that action at a deterministic template position without disturbing explicit user order/visibility. Observe red before choosing the migration metadata/algorithm.

After that change, rerun outside the restricted sandbox because tests bind loopback. Keep real macOS credentials isolated:

```bash
PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring PYTEST_ADDOPTS='-p no:cacheprovider' QT_QPA_PLATFORM=offscreen /opt/miniconda3/envs/mobility_client/bin/python -m pytest -q
```

Then run `git diff --check` and inspect `git status --short`.

## Remaining acceptance work

1. Decide and implement migration insertion for actions added after a customized v1 layout; current unknown retired actions are normalized, but future default additions are not merged into overrides.
2. Add/confirm focused tests for editor reorder, promote, reset, cancel/draft, and preview/runtime structural equivalence; do not assert private calls.
3. Extend visual QA to enlarged font and extreme long labels. Current macOS light wide/compact, dark wide, unavailable action and expanded disclosure evidence is complete.
4. Validate the current resolver-backed preview on real Windows/Linux hosts; do not create a second semantic model for platform-specific visuals.
5. Run a final diff-level Qt UI review after the remaining work, update ticket statuses and this handoff, then report clearly that Windows/Linux have model coverage only unless real hosts were used.

## Risks

- The editor preview is a resolver-backed hierarchy, not an embedded native `QMenu`; visual parity must be assessed during GUI QA.
- Internal `QTreeWidget` drag/drop is constrained by flags, but keyboard alternatives remain the authoritative accessible path.
- Full-suite Qt process can abort after a test failure if a background `QThread` is still alive; use `-x` to diagnose first failures.
