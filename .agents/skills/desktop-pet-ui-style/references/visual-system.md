# Desktop UI visual system

## Authority

Use these sources in descending order:

1. component behavior and QSS in `pet/modern_settings_dialog.py`;
2. semantic vectors in `pet/context_menus/icons.py`;
3. accepted renders in `docs/screenshots/settings-redesign/iteration-5-layout-*`;
4. this summary.

When an accepted change intentionally alters a token, update this reference in the same change.

## Composition

```text
settings window
├── 200 px sidebar
│   ├── save and exit
│   ├── search
│   └── stable capability domains
└── scrollable page shell
    ├── page title
    └── section (18 px rhythm)
        ├── section title or one-level disclosure
        └── rounded settings card
            ├── setting row
            ├── inset separator
            └── setting row
```

Keep the sidebar domains stable: 常规、桌宠、互动、菜单、桌面组件、AI 与对话、自动化与联动. A platform changes availability inside a domain, not the domain list.

## Typography

Use the Qt system general font. The window establishes 13 px body text; component roles override only when hierarchy requires it.

| Role | Size | Weight | Color, light | Color, dark |
| --- | ---: | ---: | --- | --- |
| Page title | 22 px | 600 | `#171717` | `#f0f0f5` |
| Section title / disclosure | 13 px | 600 | `#2b2b2b` | `#d8d8e0` |
| Setting label / sidebar | 13 px | 500 | `#252525` | `#e0e0e6` |
| Setting hint | 12 px | 400 | `#777777` | `#9a9aa3` |
| Search status | 11 px | regular | `#777b80` | inherited muted |

Let labels and hints wrap. Increase row height from `sizeHint`; do not assign fixed text heights or shrink fonts to make copy fit.

## Layout and spacing

| Token | Value |
| --- | ---: |
| Minimum settings window | `720 × 500` |
| Sidebar width | `200` |
| Sidebar outer margins | `12, 16, 12, 12` |
| Page shell margins | `30, 24, 28, 20` |
| Page section gap | `18` |
| Section title-to-card gap | `7` |
| Setting row margins | `16, 10, 16, 10` |
| Label-to-control gap | `18` |
| Label-to-hint gap | `2` |
| Card separator inset | `14` per side |

At 720 px, preserve the sidebar and stack complex editors vertically. Toolbars may use a grid. Controls stay reachable without horizontal page scrolling. At standard/wide widths, let cards fill the page rather than forming narrow nested columns.

The page title and scroll content share one maximum-width contract. Center that contract as a unit so the title follows the content edge when a wide window introduces outer whitespace.

## Surfaces and color

| Surface | Light | Dark |
| --- | --- | --- |
| Window/page | `#fcfcfd` | `#202024` |
| Sidebar | `#f7f7f8` | `#26262b` |
| Card | `#ffffff` | `#2a2a30` |
| Card border | `#e2e4e8` | `#3a3a42` |
| Divider | `#eceef1` | `#33333a` |
| Selected sidebar | `#e3e5e8` | `#3a3a46` |
| Hover | `#f4f5f6` | `#303036` |
| Focus/accent | `#0a84ff` | `#0a84ff` |

Cards use a 12 px radius and 1 px border. Fields use a 7 px radius and 32 px total height. Ordinary buttons use a 7 px radius and at least 26 px height. One-level disclosure headers use a 10 px radius and at least 40 px height. Reserve blue for selection, focus, checked controls, and active semantic icons.

## Components

- `SettingRow` owns label, hint, accessibility relation, responsive copy, and right/stacked control placement.
- `ResponsiveActionRow` keeps a primary control and adjacent buttons inline when space permits, then moves actions below without increasing the page's minimum width.
- `SettingsCard` owns the surface and inset separators.
- `SettingsSection` owns section rhythm. `advanced=True` is the only supported disclosure depth.
- `SettingsDisclosureHeader` owns the non-native disclosure look and accessible expanded/collapsed name.
- `ModernSelect`, `BrowserSpinBox`, and `BrowserDoubleSpinBox` own shared field geometry and focus treatment.
- `SettingsTabContainer` is for peer tasks inside one capability domain, not for replacing the seven-domain sidebar. Tabs use a compact segmented surface, retain keyboard focus, and hidden pages must not impose their minimum width on the active page.
- `MenuLayoutEditor` gives its editor and preview symmetric labeled panels. It stacks them at compact widths and expands both at wide widths; command groups live in `排序 / 移动到 / 插入 / 自定义 / 更多` menus. The tree owns order, visibility, submenus, separators, aliases, and icon overrides; the status column distinguishes enabled, runtime-disabled, hidden, and platform-unavailable items.
- `QuickLaunchEditor` uses content-sized two-line rows (name plus source), a count, compact empty state, and one grouped Add menu. Cap list growth and let the page own overflow.

Reuse these classes. Decide responsive placement from font-derived size hints and available width, not platform names or one fixed breakpoint. A new control variant must define light/dark, hover, pressed, focus, disabled, and High DPI behavior before adoption.

## Icons

Use `vector_widget_icon` and the semantic names in `pet/context_menus/icons.py`. Sidebar icons are 18 px monochrome outlines with consistent stroke weight. Color follows the owning widget: muted gray at rest, accent blue when selected. Avoid emoji, platform stock icons, and one-off raster icons for navigation or common actions.

## State and motion

- User-hidden removes an item from runtime presentation but keeps its draft location.
- Platform-unavailable remains visible and explained in the editor, but is absent from runtime and preview.
- Disabled content keeps readable hierarchy at reduced contrast.
- Keyboard focus uses the shared blue border and must remain visible on buttons, fields, selects, trees, and disclosure headers.
- Use motion only to explain spatial change. Keep it interruptible and preserve final geometry; settings forms do not animate routine row changes.

## Visual acceptance

Capture every affected settings domain at the top of its scroll view:

```bash
/opt/miniconda3/envs/mobility_client/bin/python scripts/capture_settings_pages.py \
  docs/screenshots/settings-redesign/<iteration> --width 1100 --height 760
```

Also capture 720 px for layout changes and `--dark` for color/surface changes. Use `--expanded-ai` when disclosure behavior changes. Inspect the images; file existence alone is not acceptance.
