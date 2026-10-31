"""AI 三项（AI 对话 / 看看屏幕 / 截图提问）必须是 ai.chat 注册出来的菜单项。

L108 之前它们是 `menu_actions.core_items()` 里写死的 core 项，模板用裸 id
`ai_chat/look_screen/screen_capture` 摆位——没装 ai.chat 也会出现，点了才提示
「AI 扩展未安装」。这里锁住三件事：

1. 没注册 `ai.chat:*` 时，默认布局解析后不含这三项（也不会变成不可用占位）；
2. 注册了以后按原顺序回到「播放动画」下面；
3. 旧配置里的裸 id 会被 `migrate_p11_menu` 改写，且隐藏记忆不丢。
"""

from __future__ import annotations

import copy

from petcore.ui.menu_layout import (
    DEFAULT_LAYOUT_ID,
    LEGACY_ID_MAP,
    load_default,
    migrate_legacy,
    migrate_p11_menu,
    resolve,
)

AI_IDS = ("ai.chat:open", "ai.chat:look_screen", "ai.chat:screen_capture")
OLD_IDS = ("ai_chat", "look_screen", "screen_capture")

#: 不装 ai.chat 时的注册集（core 项 + 模板里其它已注册项）。
CORE_ONLY = {
    "easter_egg",
    "character",
    "animations_hub",
    "playback_speed",
    "size",
    "pet_controls",
    "drag_physics",
    "no_move",
    "on_top",
    "edge_probe",
    "dynamic_island",
    "show_balance",
    "autostart",
    "return_corner",
    "hide_pet",
    "spawn_pet",
    "clear_spawned_pets",
    "golden_spin",
    "move_to_primary",
    "reload_mods",
    "open_data",
    "tools_help",
    "tool.quick-launch:open_menu",
    "api_settings",
    "check_update",
    "tool.links:deepseek_web",
    "tool.links:github_project",
    "tool.links:quark_download",
    "tool.network-info:show",
    "tool.memory-trim:trim",
    "tool.quiet-mode:quiet_30",
    "extensions_hub",
    "modern_settings",
    "quit",
}


def _ids(nodes) -> list[str]:
    found: list[str] = []
    for node in nodes if isinstance(nodes, (list, tuple)) else ():
        if not isinstance(node, dict):
            continue
        if node.get("type") == "action":
            found.append(str(node.get("id") or ""))
        elif node.get("type") == "submenu":
            found.extend(_ids(node.get("children", [])))
    return found


def test_default_layout_hides_the_ai_items_when_the_extension_is_missing() -> None:
    resolved = resolve(
        load_default(), registered=CORE_ONLY, available=CORE_ONLY
    )

    ids = _ids(resolved.nodes)
    for ai_id in AI_IDS:
        assert ai_id not in ids, f"没装 ai.chat 却出现了 {ai_id}"
    assert not any(
        item.startswith("unavailable-action:ai.chat") for item in resolved.diagnostics
    ), "缺扩展应该是「不注册」，不是「注册了但不可用」"


def test_ai_items_come_back_in_the_original_order_once_registered() -> None:
    registered = CORE_ONLY | set(AI_IDS) | {"ai.chat:proactive"}
    resolved = resolve(load_default(), registered=registered, available=registered)

    ids = _ids(resolved.nodes)
    assert ids.count("ai.chat:open") == 1
    assert ids.count("ai.chat:look_screen") == 1
    assert ids.count("ai.chat:screen_capture") == 1
    # 位置：播放动画 → AI 对话 → 看看屏幕 → 主动识屏 → 截图提问
    assert ids.index("animations_hub") < ids.index("ai.chat:open")
    assert ids.index("ai.chat:open") < ids.index("ai.chat:look_screen")
    assert ids.index("ai.chat:look_screen") < ids.index("ai.chat:proactive")
    assert ids.index("ai.chat:proactive") < ids.index("ai.chat:screen_capture")
    assert ids.index("ai.chat:screen_capture") < ids.index("modern_settings")


def test_p11_migration_renames_saved_ids_and_keeps_hidden_memory() -> None:
    layout = {
        "schema_version": 1,
        "layout_id": DEFAULT_LAYOUT_ID,
        "nodes": [
            {"type": "action", "id": "animations_hub", "visible": True},
            {"type": "action", "id": "ai_chat", "visible": False},
            {"type": "action", "id": "look_screen", "visible": True},
            {
                "type": "submenu",
                "id": "tools_help",
                "label": "工具与帮助",
                "visible": True,
                "children": [
                    {"type": "action", "id": "screen_capture", "visible": False},
                ],
            },
            {"type": "action", "id": "quit", "visible": True},
        ],
    }
    before = copy.deepcopy(layout)

    assert migrate_p11_menu(layout) is True

    ids = _ids(layout["nodes"])
    for old in OLD_IDS:
        assert old not in ids, f"{old} 没被迁移"
    for new in AI_IDS:
        assert new in ids
    nodes = {node.get("id"): node for node in layout["nodes"] if node.get("type") == "action"}
    assert nodes["ai.chat:open"]["visible"] is False, "隐藏记忆要跟着新 id 走"
    child = layout["nodes"][3]["children"][0]
    assert child["id"] == "ai.chat:screen_capture" and child["visible"] is False

    # 幂等：迁移过的布局不再改写，也不该把别的东西动掉
    assert migrate_p11_menu(layout) is False
    assert layout["nodes"][0]["id"] == before["nodes"][0]["id"]


def test_legacy_hidden_list_still_targets_the_new_ids() -> None:
    """老版本用 ``menu_hidden=["ai_chat"]`` 的配置，迁移后仍应隐藏 AI 对话。"""
    for old, new in zip(OLD_IDS, AI_IDS, strict=True):
        assert LEGACY_ID_MAP[old] == new

    layout = migrate_legacy(hidden=["ai_chat"], order=None)
    nodes: dict[str, dict] = {}

    def walk(items) -> None:
        for node in items:
            if node.get("type") == "action":
                nodes[str(node.get("id") or "")] = node
            walk(node.get("children", ()))

    walk(layout["nodes"])
    assert nodes["ai.chat:open"]["visible"] is False
