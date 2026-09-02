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
The shared set of menu commands, grouping, availability, and state semantics,
independent of visual presentation or operating system.
_Avoid_: Modern menu behavior, legacy menu behavior

**Menu Layout Tree**:
A versioned tree of stable action IDs and one-level submenus. It owns order and
user visibility, while the Menu Action Model owns labels, callbacks, runtime
state, and platform capability. Missing actions from newer versioned defaults
are inserted beside their nearest template sibling; explicit hidden nodes and
user ordering remain authoritative.
_Avoid_: Serialized QAction, platform-specific menu order

**Settings Capability Domain**:
A stable sidebar destination organized by user intent. Every persistent setting
has one owning domain; platform support changes availability, not ownership.
_Avoid_: Module page, Windows settings page
