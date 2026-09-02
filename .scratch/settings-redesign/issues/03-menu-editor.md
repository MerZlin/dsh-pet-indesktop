# Menu content editor and preview

Status: Complete

Add the draft-based tree editor, keyboard alternatives, live preview, validation, commit, reset, and recovery messaging under 菜单 / 内容与布局.

## Completion

- Settings interaction tests cover reorder, hide, nest, promote, reset, cancel/draft, and commit.
- Compact and wide layouts remain usable with long text and enlarged fonts.
- Cross-parent tree changes refresh the resolver-backed preview after Qt's remove/insert phases are complete.
- Preview and the runtime `QMenu` have a direct structural-equivalence test.
