---
name: qt-ui-review
description: Review PySide6 settings, menus, overlays, QSS, and cross-platform desktop UI changes in this repository. Use for UI review, accessibility review, visual QA, or changes to persistent settings and menu presentation; do not apply Web DOM/ARIA rules literally.
---

# Qt UI Review

Review changed PySide6/Qt files and tests against the Shared UX Contract. Read `docs/SETTINGS-CHANGE-GATES.md` when persistent settings or settings-page interaction changed; every applicable exit gate needs evidence or an explicit not-applicable reason.

## Review

Report terse, actionable findings as `file:line - issue`; group by file and write `✓ pass` when no finding exists.

Check:

- one semantic setting/action model feeds every platform presentation and preview;
- keyboard path, Tab order, visible focus, accessible names, and focus restoration;
- long Chinese/English text, system font metrics, High DPI, and `compact/standard/wide` degradation;
- user-hidden, unsupported, temporarily unavailable, disabled, checked, hover, pressed, and focus states remain distinct;
- motion is purposeful, interruptible, and reduced when the platform requests reduced motion;
- capability checks create supported controls/actions only; native appearance may differ without changing semantics;
- tests cross the agreed public seam and cover migration/default recovery, relevant capability matrices, and a real-platform result where available.

Screenshot inspection is evidence for layout and visual state, not a replacement for behavioral tests. Distinguish verified platform results from inferred ones.
