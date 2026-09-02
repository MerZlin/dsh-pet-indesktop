---
name: desktop-pet-ui-style
description: Build or materially restyle this repository's PySide6 settings, menus, dialogs, overlays, or desktop widgets with the established visual hierarchy, component tokens, responsive behavior, and screenshot acceptance. Use qt-ui-review for review-only requests.
---

# Desktop Pet UI Style

Create new UI as an extension of the Shared UX Contract, not as an isolated skin.

Before editing, read [references/visual-system.md](references/visual-system.md). Treat the referenced implementation and latest accepted screenshots as the live source of truth when they differ from prose.

## Build

1. Place the feature in an existing capability domain and reuse the nearest established component. Add a new visual primitive only when existing primitives cannot express the required interaction.
2. Compose settings as `page shell → section → card → setting row → control`. Keep one persistent setting in one owning row; other surfaces may deep-link or invoke commands, not duplicate preference state.
3. Apply the shared type, spacing, radius, color, icon, state, and responsive tokens. Preserve native platform behavior only where it does not change the Shared UX Contract.
4. Expose control name and description, a keyboard path, visible focus, and distinct unavailable/disabled/hidden/checked states. Platform capability decides whether a control exists; it does not create platform-specific information architecture.
5. Develop behavior red → green at a public Qt or domain seam. Visual-only changes need a stable token/component assertion plus a real Cocoa screenshot on the available Mac.

## Complete

The change is complete when all affected pages have:

- no clipping or unreachable controls at 1100 px and 720 px window widths;
- light and dark state evidence when colors or containers changed;
- wrapped Chinese/English copy using font metrics rather than fixed text heights;
- semantic monochrome icons from the shared vector provider;
- related tests, the full suite, and `git diff --check` passing.

Use `scripts/capture_settings_pages.py` for settings-page evidence. Record verified hosts separately from capability-fake or CI coverage.
