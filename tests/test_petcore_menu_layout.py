from __future__ import annotations

import copy

from petcore.ui import menu_layout
from petcore.ui.menu_layout import (
    BUILTIN_DEFAULT_LAYOUT,
    DEFAULT_LAYOUT_ID,
    LEGACY_ID_MAP,
    load_default,
    load_default_with_source,
    merge_user_layout,
    migrate_default_size_visibility,
    migrate_legacy,
    migrate_p8_menu,
    migrate_p9_menu,
    migrate_p10_menu,
    migrate_p12_menu,
    normalize_separators,
    resolve,
)

REGISTERED = {
    "animations_hub",
    "character",
    "playback_speed",
    "size",
    "drag_physics",
    "no_move",
    "hide_pet",
    "on_top",
    "edge_probe",
    "autostart",
    "return_corner",
    "spawn_pet",
    "clear_spawned_pets",
    "golden_spin",
    "move_to_primary",
    "reload_mods",
    "open_data",
    "extensions_hub",
    "modern_settings",
    "quit",
}


#: Everything the real application registers: core actions plus the tool/mod
#: actions that ship in the default template.
CORE_REGISTERED = REGISTERED | {
    "tools_help",
    "api_settings",
    "easter_egg",
    "tool.quick-launch:open_menu",
    "tool.links:deepseek_web",
    "tool.links:github_project",
    "tool.links:quark_download",
    "tool.network-info:show",
    "tool.memory-trim:trim",
    "tool.quiet-mode:quiet_30",
}

#: The shape a user config written by an older build actually has: no
#: animations_hub / playback_speed / tools_help and an older pet_controls list.
OLD_USER_LAYOUT = {
    "schema_version": 1,
    "layout_id": DEFAULT_LAYOUT_ID,
    "nodes": [
        {"type": "action", "id": "character", "visible": True},
        {"type": "separator", "id": "default.separator-profile", "visible": True},
        {"type": "action", "id": "size", "visible": False},
        {"type": "action", "id": "hide_pet", "visible": True},
        {"type": "separator", "id": "default.separator-playback", "visible": True},
        {
            "type": "submenu",
            "id": "pet_controls",
            "label": "桌宠控制",
            "visible": True,
            "children": [
                {"type": "action", "id": "on_top", "visible": True},
                {"type": "action", "id": "click_through", "visible": True},
                {"type": "action", "id": "edge_probe", "visible": True},
                {"type": "action", "id": "autostart", "visible": True},
                {"type": "action", "id": "return_corner", "visible": True},
                {"type": "action", "id": "move_to_primary", "visible": True},
                {"type": "separator", "id": "default.separator-pet-tools", "visible": True},
                {"type": "action", "id": "reload_mods", "visible": True},
                {"type": "action", "id": "open_data", "visible": True},
            ],
        },
        {
            "type": "submenu",
            "id": "extensions_hub",
            "label": "扩展",
            "visible": True,
            "children": [{"type": "action", "id": "mod.demo:hello", "visible": True}],
        },
        {"type": "separator", "id": "default.separator-extensions", "visible": True},
        {"type": "action", "id": "modern_settings", "visible": True},
        {"type": "action", "id": "quit", "visible": True},
    ],
}


P7_DEFAULT_LAYOUT = {
    "schema_version": 1,
    "layout_id": DEFAULT_LAYOUT_ID,
    "nodes": [
        {"type": "action", "id": "character", "visible": True, "section": "profile"},
        {"type": "separator", "id": "default.separator-profile", "visible": True},
        {"type": "action", "id": "animations_hub", "visible": True, "section": "playback"},
        {"type": "action", "id": "playback_speed", "visible": True, "section": "playback"},
        {"type": "action", "id": "size", "visible": False, "section": "playback"},
        {"type": "separator", "id": "default.separator-playback", "visible": True},
        {
            "type": "submenu",
            "id": "pet_controls",
            "label": "桌宠控制",
            "visible": True,
            "children": [{"type": "action", "id": "on_top", "visible": True}],
        },
        {"type": "separator", "id": "default.separator-pet", "visible": True},
        {
            "type": "submenu",
            "id": "tools_help",
            "label": "工具与帮助",
            "visible": True,
            "children": [
                {"type": "action", "id": "api_settings", "visible": True},
                {"type": "action", "id": "easter_egg", "visible": True},
            ],
        },
        {"type": "action", "id": "tool.quiet-mode:quiet_30", "visible": True, "section": "tools"},
        {"type": "submenu", "id": "extensions_hub", "label": "扩展", "visible": True, "children": []},
        {"type": "separator", "id": "default.separator-extensions", "visible": True},
        {"type": "action", "id": "modern_settings", "visible": True, "section": "system"},
        {"type": "action", "id": "quit", "visible": True, "section": "system"},
    ],
}


def _ids(nodes) -> list[str]:
    found = []
    for node in nodes:
        if node["type"] in {"action", "submenu"}:
            found.append(node["id"])
    return found


def _all_ids(nodes) -> set[str]:
    found: set[str] = set()
    for node in nodes:
        if node["type"] in {"action", "submenu"}:
            found.add(node["id"])
        found.update(_all_ids(node.get("children", [])))
    return found


def test_default_layout_has_the_main_branch_order() -> None:
    resolved = resolve(None, registered=REGISTERED, available=REGISTERED)
    assert resolved.source == "default"
    assert _ids(resolved.nodes) == [
        "character",
        "animations_hub",
        "pet_controls",
        "modern_settings",
        "quit",
    ]
    controls = next(node for node in resolved.nodes if node.get("id") == "pet_controls")
    assert [child["id"] for child in controls["children"] if child["type"] == "action"] == [
        "drag_physics",
        "no_move",
        "on_top",
        "edge_probe",
        "autostart",
        "return_corner",
        "hide_pet",
        "spawn_pet",
        "clear_spawned_pets",
        "golden_spin",
        "move_to_primary",
        "reload_mods",
        "open_data",
    ]


def test_default_template_uses_the_p8_top_level_order() -> None:
    raw = load_default()
    top = [node["id"] for node in raw["nodes"] if node.get("type") != "separator"]
    assert top == [
        "easter_egg",
        "character",
        "animations_hub",
        "ai_hub",
        "ai.agent:enabled",
        "ai.chat:screen_capture",
        "playback_speed",
        "size",
        "pet_controls",
        "tools_help",
        "tool.quiet-mode:quiet_30",
        "extensions_hub",
        "modern_settings",
        "quit",
    ]
    assert raw["nodes"][0]["id"] == "easter_egg"
    assert raw["nodes"][-3]["id"] == "modern_settings"
    assert raw["nodes"][-2]["type"] == "separator"
    assert raw["nodes"][-1]["id"] == "quit"
    speed = next(node for node in raw["nodes"] if node.get("id") == "playback_speed")
    assert speed["visible"] is False
    tools = next(node for node in raw["nodes"] if node.get("id") == "tools_help")
    assert "easter_egg" not in [child["id"] for child in tools["children"]]

    resolved = resolve(None, registered=CORE_REGISTERED, available=CORE_REGISTERED)
    assert _ids(resolved.nodes) == [
        "easter_egg",
        "character",
        "animations_hub",
        "pet_controls",
        "tools_help",
        "tool.quiet-mode:quiet_30",
        "modern_settings",
        "quit",
    ]


def test_size_is_hidden_by_default_but_kept_for_the_editor() -> None:
    resolved = resolve(None, registered=REGISTERED, available=REGISTERED)
    assert "size" not in _ids(resolved.nodes)
    raw = load_default()
    size = next(node for node in raw["nodes"] if node.get("id") == "size")
    assert size["visible"] is False
    editable = resolve(raw, registered=REGISTERED, available=REGISTERED, include_hidden=True)
    assert "size" in _ids(editable.nodes)


def test_user_order_and_hidden_are_respected() -> None:
    raw = load_default()
    by_id = {node.get("id"): node for node in raw["nodes"] if node.get("type") != "separator"}
    by_id["character"]["visible"] = False
    by_id["size"]["visible"] = True
    raw["nodes"] = [
        by_id["size"],
        by_id["character"],
        by_id["animations_hub"],
        by_id["pet_controls"],
        by_id["modern_settings"],
        by_id["quit"],
    ]
    resolved = resolve(raw, registered=REGISTERED, available=REGISTERED)
    ids = _ids(resolved.nodes)
    assert ids[0] == "size"
    assert "character" not in ids
    assert ids[-1] == "quit"


def test_unknown_and_unavailable_are_dropped_with_diagnostics() -> None:
    raw = load_default()
    raw["nodes"].insert(0, {"type": "action", "id": "nope", "visible": True})
    raw["nodes"].insert(0, {"type": "action", "id": "ghost", "visible": True})
    registered = REGISTERED | {"ghost"}
    available = REGISTERED
    resolved = resolve(raw, registered=registered, available=available)
    assert "nope" not in _ids(resolved.nodes)
    assert "ghost" not in _ids(resolved.nodes)
    assert "unknown-action:nope" in resolved.diagnostics
    assert "unavailable-action:ghost" in resolved.diagnostics


def test_duplicate_action_falls_back_to_settings_and_quit() -> None:
    raw = {
        "schema_version": 1,
        "layout_id": "petcore-default-v1",
        "nodes": [
            {"type": "action", "id": "quit", "visible": True},
            {"type": "action", "id": "quit", "visible": True},
        ],
    }
    resolved = resolve(raw, registered=REGISTERED, available=REGISTERED)
    assert resolved.source == "fallback"
    assert _ids(resolved.nodes) == ["modern_settings", "quit"]


def test_submenu_depth_is_limited_to_one_level() -> None:
    raw = {
        "schema_version": 1,
        "layout_id": "petcore-default-v1",
        "nodes": [
            {
                "type": "submenu",
                "id": "a",
                "label": "A",
                "children": [
                    {
                        "type": "submenu",
                        "id": "b",
                        "label": "B",
                        "children": [{"type": "action", "id": "quit"}],
                    }
                ],
            }
        ],
    }
    resolved = resolve(raw, registered=REGISTERED, available=REGISTERED)
    assert resolved.source == "fallback"


def test_separators_are_normalized() -> None:
    nodes = [
        {"type": "separator", "id": "a"},
        {"type": "separator", "id": "b"},
        {"type": "action", "id": "quit"},
        {"type": "separator", "id": "c"},
    ]
    compact = normalize_separators(nodes)
    assert [node["id"] for node in compact] == ["quit"]


def test_new_default_action_is_merged_near_its_template_anchor() -> None:
    raw = {
        "schema_version": 1,
        "layout_id": "petcore-default-v1",
        "nodes": [
            {"type": "action", "id": "size", "visible": True},
            {"type": "action", "id": "quit", "visible": True},
        ],
    }
    registered = REGISTERED | {"new_action"}
    default = {
        "schema_version": 1,
        "layout_id": "petcore-default-v1",
        "nodes": [
            {"type": "action", "id": "character"},
            {"type": "action", "id": "size"},
            {"type": "action", "id": "new_action"},
            {"type": "action", "id": "quit"},
        ],
    }
    resolved = resolve(raw, registered=registered, available=registered, default=default)
    assert _ids(resolved.nodes) == ["character", "size", "new_action", "quit"]
    assert "default-action-added:character" in resolved.diagnostics
    assert "default-action-added:new_action" in resolved.diagnostics


def test_tools_help_contains_web_links() -> None:
    layout = load_default()
    tools = next(node for node in layout["nodes"] if node.get("id") == "tools_help")
    ids = [child.get("id") for child in tools["children"]]
    for expected in (
        "tool.links:deepseek_web",
        "tool.links:github_project",
        "tool.links:quark_download",
    ):
        assert expected in ids


def test_balance_and_update_actions_are_reachable_from_the_default_menu() -> None:
    """注册了不等于有入口：P5 的两个动作必须真的落进模板。"""
    layout = load_default()
    controls = next(node for node in layout["nodes"] if node.get("id") == "pet_controls")
    assert "show_balance" in [child.get("id") for child in controls["children"]]
    tools = next(node for node in layout["nodes"] if node.get("id") == "tools_help")
    assert "check_update" in [child.get("id") for child in tools["children"]]


def test_p10_moves_core_actions_out_of_the_extensions_hub() -> None:
    layout = {
        "schema_version": 1,
        "layout_id": DEFAULT_LAYOUT_ID,
        "nodes": [
            {
                "type": "submenu",
                "id": "tools_help",
                "label": "工具与帮助",
                "visible": True,
                "children": [{"type": "action", "id": "api_settings", "visible": True}],
            },
            {
                "type": "submenu",
                "id": "extensions_hub",
                "label": "扩展",
                "visible": True,
                "children": [
                    {"type": "action", "id": "tool.links:deepseek_web", "visible": True},
                    {"type": "action", "id": "tool.links:github_project", "visible": True},
                    {"type": "action", "id": "tool.links:quark_download", "visible": True},
                    {"type": "action", "id": "tool.quick-launch:open_menu", "visible": True},
                    {"type": "action", "id": "tool.network-info:show", "visible": True},
                    {"type": "action", "id": "tool.memory-trim:trim", "visible": True},
                    {"type": "action", "id": "tool.quiet-mode:quiet_30", "visible": True},
                    {"type": "action", "id": "media.music:pause", "visible": True},
                ],
            },
            {"type": "action", "id": "modern_settings", "visible": True},
            {"type": "separator", "id": "system", "visible": True},
            {"type": "action", "id": "quit", "visible": True},
        ],
    }

    assert migrate_p10_menu(layout) is True

    tools = next(node for node in layout["nodes"] if node.get("id") == "tools_help")
    assert [child.get("id") for child in tools["children"]] == [
        "tool.quick-launch:open_menu",
        "api_settings",
        "tool.links:deepseek_web",
        "tool.links:github_project",
        "tool.links:quark_download",
        "tool.network-info:show",
        "tool.memory-trim:trim",
    ]
    top_ids = [
        node.get("id") for node in layout["nodes"] if node.get("type") == "action"
    ]
    assert "tool.quiet-mode:quiet_30" in top_ids
    hub = next(node for node in layout["nodes"] if node.get("id") == "extensions_hub")
    assert [child.get("id") for child in hub["children"]] == ["media.music:pause"]
    assert migrate_p10_menu(layout) is False


def test_mod_action_lands_in_extensions_hub() -> None:
    resolved = resolve(
        None,
        registered=REGISTERED | {"official.demo:hello"},
        available=REGISTERED | {"official.demo:hello"},
    )
    hub = next(node for node in resolved.nodes if node.get("id") == "extensions_hub")
    assert "official.demo:hello" in [child["id"] for child in hub["children"]]
    assert "mod-action-added:official.demo:hello" in resolved.diagnostics


def test_untouched_old_default_layout_migrates_size_to_hidden() -> None:
    layout = load_default()
    size = next(node for node in layout["nodes"] if node.get("id") == "size")
    size["visible"] = True
    hub = next(node for node in layout["nodes"] if node.get("id") == "extensions_hub")
    hub["children"].append({"type": "action", "id": "mod.demo:hello", "visible": True})

    assert migrate_default_size_visibility(layout) is True
    assert size["visible"] is False


def test_customized_layout_keeps_size_visible() -> None:
    layout = load_default()
    size = next(node for node in layout["nodes"] if node.get("id") == "size")
    size["visible"] = True
    character = next(node for node in layout["nodes"] if node.get("id") == "character")
    character["visible"] = False

    assert migrate_default_size_visibility(layout) is False
    assert size["visible"] is True


def test_legacy_preferences_migrate_with_old_id_map() -> None:
    assert LEGACY_ID_MAP["scale"] == "size"
    layout = migrate_legacy(["hide", "settings"], ["quit", "scale"])
    ids = [node["id"] for node in layout["nodes"]]
    assert ids[:2] == ["quit", "size"]
    hidden: set[str] = set()

    def walk(nodes) -> None:
        for node in nodes:
            if node.get("visible") is False:
                hidden.add(node.get("id"))
            walk(node.get("children", ()))

    walk(layout["nodes"])
    assert {"hide_pet", "modern_settings"} <= hidden


def test_include_hidden_keeps_nodes_for_the_editor() -> None:
    raw = {
        "schema_version": 1,
        "layout_id": "petcore-default-v1",
        "nodes": [
            {"type": "action", "id": "size", "visible": False},
            {"type": "submenu", "id": "extensions_hub", "label": "扩展", "visible": True, "children": []},
        ],
    }
    resolved = resolve(raw, registered=REGISTERED, available=REGISTERED, include_hidden=True)
    size = next(node for node in resolved.nodes if node.get("id") == "size")
    assert size["visible"] is False
    assert any(node.get("id") == "extensions_hub" for node in resolved.nodes)


def test_builtin_default_matches_json() -> None:
    assert BUILTIN_DEFAULT_LAYOUT == load_default()
    layout, source = load_default_with_source()
    assert source == "json"
    assert layout == BUILTIN_DEFAULT_LAYOUT


def test_missing_template_falls_back_to_builtin(monkeypatch) -> None:
    def missing(package):
        raise FileNotFoundError(package)

    monkeypatch.setattr(menu_layout.resources, "files", missing)
    layout, source = load_default_with_source()
    assert source == "builtin"
    assert layout == BUILTIN_DEFAULT_LAYOUT

    resolved = resolve(None, registered=CORE_REGISTERED, available=CORE_REGISTERED)
    assert resolved.source == "default"
    ids = _all_ids(resolved.nodes)
    assert {"animations_hub", "quit"} <= ids
    assert _ids(resolved.nodes)[0] == "easter_egg"
    tools = next(node for node in resolved.nodes if node.get("id") == "tools_help")
    assert "easter_egg" not in _ids(tools["children"])

    # The packaged symptom: an old user layout must still regain the defaults.
    old = resolve(
        copy.deepcopy(OLD_USER_LAYOUT),
        registered=CORE_REGISTERED,
        available=CORE_REGISTERED,
    )
    assert old.source == "user"
    assert {"animations_hub", "easter_egg"} <= _all_ids(old.nodes)


def test_old_user_layout_regains_missing_default_actions() -> None:
    raw = copy.deepcopy(OLD_USER_LAYOUT)
    resolved = resolve(raw, registered=CORE_REGISTERED, available=CORE_REGISTERED)
    assert resolved.source == "user"
    ids = _all_ids(resolved.nodes)
    assert {
        "animations_hub",
        "drag_physics",
        "spawn_pet",
        "golden_spin",
        "tools_help",
        "api_settings",
        "easter_egg",
        "quit",
    } <= ids
    tools = next(node for node in resolved.nodes if node.get("id") == "tools_help")
    assert "easter_egg" not in _ids(tools["children"])
    top = _ids(resolved.nodes)
    assert top[0] == "easter_egg"
    assert top.index("character") < top.index("animations_hub") < top.index("pet_controls")
    assert "default-action-added:animations_hub" in resolved.diagnostics
    assert "default-action-added:playback_speed" in resolved.diagnostics
    assert "default-action-added:drag_physics" in resolved.diagnostics

    merged, _notes = merge_user_layout(copy.deepcopy(OLD_USER_LAYOUT), registered=CORE_REGISTERED)
    speed = next(node for node in merged["nodes"] if node.get("id") == "playback_speed")
    assert speed["visible"] is False


def test_merged_user_layout_keeps_hidden_nodes_for_write_back() -> None:
    raw = copy.deepcopy(OLD_USER_LAYOUT)
    merged, notes = merge_user_layout(raw, registered=CORE_REGISTERED)
    assert merged["layout_id"] == DEFAULT_LAYOUT_ID
    assert merged["schema_version"] == 1
    top = [node.get("id") for node in merged["nodes"]]
    assert "size" in top
    size = next(node for node in merged["nodes"] if node.get("id") == "size")
    assert size["visible"] is False
    controls = next(node for node in merged["nodes"] if node.get("id") == "pet_controls")
    assert [child.get("id") for child in controls["children"]][:2] == ["drag_physics", "no_move"]
    assert "default-action-added:animations_hub" in notes
    # The user layout itself is not mutated.
    assert "animations_hub" not in [node.get("id") for node in raw["nodes"]]


def test_reload_menu_writeback_persists_merged_layout(tmp_path) -> None:
    import json

    from petcore.config import Config
    from petcore.ui.app import CoreHost
    from petcore.ui.menu_model import MenuItem, MenuModel

    config = Config(tmp_path, data={"menu_layout": copy.deepcopy(OLD_USER_LAYOUT)})
    host = object.__new__(CoreHost)
    host.config = config
    host.menu_model = MenuModel()
    for action_id in CORE_REGISTERED:
        host.menu_model.register(MenuItem(action_id, action_id))

    assert host._persist_merged_menu_layout(config.get("menu_layout")) is True
    saved = json.loads(config.path.read_text(encoding="utf-8"))["menu_layout"]
    assert saved["layout_id"] == DEFAULT_LAYOUT_ID
    assert saved["schema_version"] == 1
    top = [node.get("id") for node in saved["nodes"]]
    assert "size" in top and "hide_pet" in top
    assert top.index("character") < top.index("animations_hub") < top.index("pet_controls")
    controls = next(node for node in saved["nodes"] if node.get("id") == "pet_controls")
    assert [child.get("id") for child in controls["children"]][:2] == ["drag_physics", "no_move"]
    # Nothing left to add: a second pass must not rewrite the config again.
    assert host._persist_merged_menu_layout(config.get("menu_layout")) is False


def test_migrate_p8_menu_upgrades_the_p7_default() -> None:
    layout = copy.deepcopy(P7_DEFAULT_LAYOUT)

    assert migrate_p8_menu(layout) is True

    nodes = layout["nodes"]
    assert nodes[0]["id"] == "easter_egg"
    assert nodes[0]["type"] == "action"
    assert _ids(nodes).count("easter_egg") == 1
    tools = next(node for node in nodes if node.get("id") == "tools_help")
    assert "easter_egg" not in [child.get("id") for child in tools["children"]]
    speed = next(node for node in nodes if node.get("id") == "playback_speed")
    assert speed["visible"] is False
    assert [node.get("id") for node in nodes[-3:]] == [
        "modern_settings",
        "default.separator-system",
        "quit",
    ]
    assert [node.get("type") for node in nodes[-3:]] == ["action", "separator", "action"]

    # 幂等：第二次调用不再报告变化。
    assert migrate_p8_menu(layout) is False
    assert nodes[0]["id"] == "easter_egg"


def test_migrate_p8_menu_is_a_no_op_on_the_current_default() -> None:
    layout = load_default()
    assert migrate_p8_menu(layout) is False


def test_migrate_p8_menu_leaves_other_layout_ids_alone() -> None:
    layout = copy.deepcopy(P7_DEFAULT_LAYOUT)
    layout["layout_id"] = "user-custom"
    before = copy.deepcopy(layout)

    assert migrate_p8_menu(layout) is False
    assert layout == before


def test_migrate_p8_menu_restores_missing_settings_and_quit() -> None:
    layout = copy.deepcopy(P7_DEFAULT_LAYOUT)
    layout["nodes"] = [
        node for node in layout["nodes"] if node.get("id") not in ("modern_settings", "quit")
    ]
    layout["nodes"].append({"type": "separator", "id": "tail", "visible": True})

    assert migrate_p8_menu(layout) is True
    tail = [node.get("id") for node in layout["nodes"][-3:]]
    assert tail == ["modern_settings", "default.separator-system", "quit"]
    assert migrate_p8_menu(layout) is False


def test_migrate_p8_menu_moves_a_deeply_nested_easter_egg() -> None:
    layout = copy.deepcopy(P7_DEFAULT_LAYOUT)
    layout["nodes"][0] = {
        "type": "submenu",
        "id": "pet_controls",
        "label": "桌宠控制",
        "visible": True,
        "children": [{"type": "action", "id": "easter_egg", "visible": True}],
    }

    assert migrate_p8_menu(layout) is True
    assert layout["nodes"][0]["id"] == "easter_egg"
    assert "easter_egg" not in [node.get("id") for node in layout["nodes"][1:]]


def test_p9_migration_drops_click_through_from_saved_layouts() -> None:
    layout = copy.deepcopy(OLD_USER_LAYOUT)
    assert migrate_p9_menu(layout) is True
    assert "click_through" not in _all_ids(layout["nodes"])
    controls = next(node for node in layout["nodes"] if node.get("id") == "pet_controls")
    assert [child["id"] for child in controls["children"] if child["type"] == "action"][:3] == [
        "on_top",
        "edge_probe",
        "autostart",
    ]
    # 幂等：已经迁过的布局不再改写。
    assert migrate_p9_menu(layout) is False


def test_default_template_no_longer_contains_click_through() -> None:
    raw = load_default()
    assert "click_through" not in _all_ids(raw["nodes"])
    resolved = resolve(raw, registered=CORE_REGISTERED, available=CORE_REGISTERED)
    assert "click_through" not in _all_ids(resolved.nodes)


def test_merge_adds_the_proactive_action_to_existing_layouts() -> None:
    import copy

    raw = copy.deepcopy(OLD_USER_LAYOUT)
    registered = REGISTERED | {"ai.chat:proactive"}
    merged, notes = merge_user_layout(raw, registered=registered)

    assert "default-action-added:ai.chat:proactive" in notes
    all_ids = _all_ids(merged["nodes"])
    assert "ai.chat:proactive" in all_ids
    top = _ids(merged["nodes"])
    assert "ai_hub" in top
    assert top.index("ai_hub") > top.index("animations_hub")
    assert top.index("ai_hub") < top.index("pet_controls"), (
        "没见过 AI 项的旧布局，应落在播放段（桌宠控制之前）"
    )
    hub = next(node for node in merged["nodes"] if node.get("id") == "ai_hub")
    assert [child["id"] for child in hub["children"]] == [
        "ai.chat:open",
        "ai.chat:look_screen",
        "ai.chat:proactive",
    ]

    again, notes_again = merge_user_layout(merged, registered=registered)
    assert "default-action-added:ai.chat:proactive" not in notes_again
    again_hub = next(node for node in again["nodes"] if node.get("id") == "ai_hub")
    assert [child["id"] for child in again_hub["children"]].count(
        "ai.chat:proactive"
    ) == 1


def test_merge_places_the_proactive_action_right_after_look_screen() -> None:
    raw = {
        "schema_version": 1,
        "layout_id": DEFAULT_LAYOUT_ID,
        "nodes": [
            {"type": "action", "id": "easter_egg", "visible": True},
            {"type": "action", "id": "ai.chat:look_screen", "visible": True},
            {"type": "action", "id": "ai.chat:screen_capture", "visible": True},
            {"type": "action", "id": "quit", "visible": True},
        ],
    }
    registered = REGISTERED | {"ai.chat:proactive", "ai.chat:look_screen", "ai.chat:screen_capture"}
    merged, notes = merge_user_layout(raw, registered=registered)
    hub = next(node for node in merged["nodes"] if node.get("id") == "ai_hub")
    children = [child["id"] for child in hub["children"]]
    assert children == [
        "ai.chat:open",
        "ai.chat:look_screen",
        "ai.chat:proactive",
    ]
    assert "default-action-added:ai.chat:proactive" in notes


def test_migrate_p12_moves_ai_actions_into_the_hub() -> None:
    layout = {
        "schema_version": 1,
        "layout_id": DEFAULT_LAYOUT_ID,
        "nodes": [
            {"type": "action", "id": "easter_egg", "visible": True},
            {"type": "action", "id": "ai.chat:open", "visible": False},
            {"type": "action", "id": "ai.chat:look_screen", "visible": True},
            {"type": "action", "id": "ai.chat:proactive", "visible": True},
            {"type": "action", "id": "ai.chat:screen_capture", "visible": True},
            {"type": "action", "id": "quit", "visible": True},
        ],
    }
    assert migrate_p12_menu(layout) is True
    top = [node.get("id") for node in layout["nodes"]]
    assert top == ["easter_egg", "ai_hub", "ai.chat:screen_capture", "quit"]
    hub = next(node for node in layout["nodes"] if node.get("id") == "ai_hub")
    assert [child["id"] for child in hub["children"]] == [
        "ai.chat:open",
        "ai.chat:look_screen",
        "ai.chat:proactive",
    ]
    assert hub["children"][0]["visible"] is False, "隐藏记忆跟着节点走"
    assert migrate_p12_menu(layout) is False


def test_migrate_p12_leaves_other_layouts_alone() -> None:
    layout = {
        "schema_version": 1,
        "layout_id": "someone-else",
        "nodes": [{"type": "action", "id": "ai.chat:open", "visible": True}],
    }
    assert migrate_p12_menu(layout) is False
    assert layout["nodes"][0]["id"] == "ai.chat:open"


def test_migrate_p12_keeps_hub_at_the_first_ai_action_position() -> None:
    layout = {
        "schema_version": 1,
        "layout_id": DEFAULT_LAYOUT_ID,
        "nodes": [
            {"type": "action", "id": "modern_settings", "visible": True},
            {"type": "action", "id": "ai.chat:open", "visible": True},
            {"type": "separator", "id": "sep1", "visible": True},
            {"type": "action", "id": "quit", "visible": True},
        ],
    }
    assert migrate_p12_menu(layout) is True
    assert [node.get("id") for node in layout["nodes"]] == [
        "modern_settings",
        "ai_hub",
        "sep1",
        "quit",
    ]
