"""Core 菜单项与设置页入口的一一对应表。

规则（P1-C 起）：右键菜单里能看见、能调整的每个 Core 动作，都必须在设置里
有一个可到达的入口。新增菜单项却没登记这里时，`test_petcore_settings_coverage`
会失败。mod 贡献的菜单项由各 mod 自己提供设置页，不在本表内。
"""

from __future__ import annotations

CORE_MENU_SETTINGS_COVERAGE: dict[str, tuple[str, str]] = {
    "character": ("general", "角色"),
    "autostart": ("general", "开机自动启动"),
    "open_data": ("general", "数据目录"),
    "quit": ("general", "退出"),
    "animations_hub": ("interaction", "动画"),
    "playback_speed": ("interaction", "播放速率"),
    "size": ("appearance", "大小"),
    "on_top": ("appearance", "窗口置顶"),
    "edge_probe": ("appearance", "边缘探头"),
    "dynamic_island": ("appearance", "灵动岛"),
    "hide_pet": ("appearance", "显示桌宠"),
    "return_corner": ("appearance", "回到右下角"),
    "move_to_primary": ("appearance", "重置到主屏"),
    "reload_mods": ("about", "重新加载扩展"),
    "show_balance": ("interaction", "点击查看余额"),
    "check_update": ("about", "检查更新"),
    "api_settings": ("api", "服务"),
    "easter_egg": ("interaction", "彩蛋图片"),
    "extensions_hub": ("mods", "扩展管理"),
    "drag_physics": ("interaction", "拖动物理"),
    "no_move": ("interaction", "不自动移动"),
    "keyboard_move": ("appearance", "键盘控制快捷键"),
    "golden_spin": ("interaction", "黄金回旋"),
    "spawn_pet": ("pets", "生成子宠"),
    "clear_spawned_pets": ("pets", "收回所有子宠"),
}

#: 设置宿主本身不需要在设置里再给自己一个入口。
SETTINGS_EXEMPT: frozenset[str] = frozenset({"modern_settings"})


def missing_settings_entries(menu_ids) -> list[str]:
    known = set(CORE_MENU_SETTINGS_COVERAGE) | SETTINGS_EXEMPT
    return sorted(str(item) for item in menu_ids if str(item) not in known)
