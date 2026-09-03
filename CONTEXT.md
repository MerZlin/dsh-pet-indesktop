# Desktop Pet Experience

The user-facing desktop-pet experience shared by settings, menus, overlays, and
their platform-specific presentations.

## Language

**Shared UX Contract**:
The cross-platform agreement for information architecture, state semantics,
interaction meaning, spacing roles, and brand roles. Platform presentation may
vary without changing this contract.
_Avoid_: Pixel parity, identical native chrome

**Settings System**:
The single product surface that owns every persistent user preference. Legacy
settings may temporarily redirect into it but do not define a second contract.
_Avoid_: Modern settings, legacy settings

**Menu Action Model**:
The shared set of menu commands, canonical labels/icons, callbacks, capability
availability, and runtime enablement semantics, independent of platform
presentation.
_Avoid_: Modern menu behavior, legacy menu behavior

**Menu Layout Tree**:
A versioned tree of stable action IDs and one-level submenus. It owns order and
user visibility, explicit separators, and optional alias/icon presentation
overrides, while the Menu Action Model owns canonical presentation, callbacks,
runtime state, and platform capability. Missing actions from newer versioned
defaults are inserted beside their nearest template sibling; explicit hidden
nodes and user ordering remain authoritative.
_Avoid_: Serialized QAction, platform-specific menu order

**Menu Presentation Override**:
An optional Menu Layout Tree decoration. Aliases replace canonical labels only
at runtime while the editor retains the canonical label for orientation. Icon
overrides may select a semantic icon, suppress the icon, or reference a
validated local image with contain/cover rendering; an unreadable local image
falls back to the Menu Action Model icon.
_Avoid_: Renamed command, embedded image bytes

**Capability Unavailable**:
An action cannot exist on the current platform or build. The Menu Layout Tree
retains its position for cross-platform editing, while the current runtime may
omit it and the editor explains the capability reason.
_Avoid_: Disabled feature, hidden action

**Runtime Disabled**:
An action exists on the current platform but its owning feature is currently
off or has no configured content. It keeps its Menu Layout Tree position and is
rendered with a disabled style and explanation; changing the feature toggle
never rewrites or filters the tree.
_Avoid_: Capability unavailable, user-hidden

**Dock Recovery Menu**:
The native macOS Dock context menu that keeps core recovery actions, especially
opening the Settings System, reachable when the pet window is hidden or mouse
input passes through it.
_Avoid_: Pet context menu, tray menu

**Settings Popup Surface**:
The shared settings-menu presentation used by selectors and command buttons.
It owns popup anchoring, minimum trigger width, row geometry, disabled state,
submenu arrows, and trailing selection checks; callers only provide actions.
_Avoid_: Copied menu stylesheet, native button menu indicator

**Image Directory Preview**:
An on-demand right-side drawer for image-directory settings. It uses three
shortest-height columns, preserves each thumbnail's useful aspect ratio, labels
every card with an elided filename and full-name tooltip, and defers directory
scanning and image decoding until the user presses Preview.
_Avoid_: Inline settings grid, eager gallery

**Settings Capability Domain**:
A stable sidebar destination organized by user intent. Every persistent setting
has one owning domain; platform support changes availability, not ownership.
_Avoid_: Module page, Windows settings page
