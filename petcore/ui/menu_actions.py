from __future__ import annotations

import logging
import sys

from PySide6.QtGui import QActionGroup, QGuiApplication, QIcon
from PySide6.QtWidgets import QMenu

from ..engine.enums import AnimatType, GraphType
from ..engine.geometry import SCALE_STEPS
from ..platform import autostart as autostart_mod
from .menu_model import MenuItem, MenuModel, add_submenu

logger = logging.getLogger("petcore.ui.menu_actions")

#: 前缀分组只在随机动作很多时才值得做（P8：106 个动画）。
RANDOM_GROUP_THRESHOLD = 20
#: 同一前缀至少这么多项才收进子菜单，否则平铺。
GROUP_MIN_ITEMS = 4
GROUP_PREFIX_MIN = 2
GROUP_PREFIX_MAX = 6

_placeholder_icon: QIcon | None = None


def _toggle_autostart() -> None:
    if autostart_mod.is_enabled():
        autostart_mod.disable()
    else:
        autostart_mod.enable()


def _multi_screen() -> bool:
    return len(QGuiApplication.screens() or ()) > 1


def _action_icon() -> QIcon:
    """动画占位图标（真实缩略图到位前先给个播放符号）。"""
    global _placeholder_icon
    if _placeholder_icon is None:
        try:
            from .icons import icon

            _placeholder_icon = icon("play")
        except Exception:
            _placeholder_icon = QIcon()
    return _placeholder_icon


def _unique_names(names) -> list[str]:
    seen: set[str] = set()
    return [name for name in names if not (name in seen or seen.add(name))]


def _name_prefix(name: str) -> str | None:
    """取第一个 ``-``/``_``/空格前的 2–6 字前缀；没有分隔符就不分组。"""
    text = str(name or "")
    end = len(text)
    for separator in ("-", "_", " "):
        index = text.find(separator)
        if index > 0:
            end = min(end, index)
    if end == len(text) or not GROUP_PREFIX_MIN <= end <= GROUP_PREFIX_MAX:
        return None
    return text[:end]


def _group_names(names) -> list[tuple[str, list[str] | None]]:
    """按前缀分组；凑不够数量或前缀不合规的项保持平铺。"""
    buckets: dict[str, list[str]] = {}
    for name in names:
        prefix = _name_prefix(name)
        if prefix is not None:
            buckets.setdefault(prefix, []).append(name)
    grouped = {prefix: items for prefix, items in buckets.items() if len(items) >= GROUP_MIN_ITEMS}
    ordered: list[tuple[str, list[str] | None]] = []
    emitted: set[str] = set()
    for name in names:
        prefix = _name_prefix(name)
        if prefix in grouped:
            if prefix in emitted:
                continue
            emitted.add(prefix)
            ordered.append((prefix, grouped[prefix]))
            continue
        ordered.append((name, None))
    return ordered


def _animation_icons_enabled(window) -> bool:
    config = getattr(window, "config", None)
    if config is None:
        return True
    try:
        value = config.get("menu_animation_icons", True)
    except Exception:
        return True
    return True if value is None else bool(value)


def _spec_for(window, name: str):
    catalog = getattr(window, "catalog", None)
    if catalog is None:
        return None
    find = getattr(catalog, "find", None)
    if callable(find):
        try:
            spec = find(name, AnimatType.SINGLE)
        except Exception:
            spec = None
        if spec is not None:
            return spec
    for spec in getattr(catalog, "animations", ()) or ():
        if getattr(spec, "name", None) == name:
            return spec
    return None


def _apply_thumbnail(action, icon: QIcon) -> None:
    if icon is None or icon.isNull():
        return
    try:
        action.setIcon(icon)
    except RuntimeError:  # 菜单已销毁
        pass


def _request_thumbnail(window, action, name: str) -> None:
    if not _animation_icons_enabled(window):
        return
    spec = _spec_for(window, name)
    if spec is None:
        return
    try:
        from ..media.images import physical_side, sanitize_dpr, screen_dpr
        from ..media.thumbnails import THUMBNAIL_MAX_SIDE, ThumbnailCache
        from .icons import MENU_ICON_SIZE, fitted_icon

        ratio = sanitize_dpr(screen_dpr(window))
        # 按物理像素解码：菜单里显示的是 THUMBNAIL_MAX_SIDE 逻辑像素的小图，
        # 150%/200% 屏上不预放大就会糊。
        limit = physical_side(THUMBNAIL_MAX_SIDE, ratio)

        def apply(pixmap, target=action, dpr=ratio):
            # 动画帧是整块视频画布，直接当图标用会让角色在 18px 槽位里只剩几个
            # 像素（主分支同款问题）；必须裁透明留白后再适配槽位。
            _apply_thumbnail(target, fitted_icon(pixmap, MENU_ICON_SIZE, dpr=dpr))

        ThumbnailCache.instance().request(spec, apply, max_side=limit)
    except Exception:
        logger.debug("thumbnail request failed for %s", name, exc_info=True)


def _add_actions(submenu: QMenu, window, names) -> None:
    icon = _action_icon()
    for name in names:
        action = submenu.addAction(icon, name)
        action.triggered.connect(
            lambda _checked=False, value=name: window.play_animation(value)
        )
        _request_thumbnail(window, action, name)


def _populate_group(submenu: QMenu, window, entries, *, group_by_prefix: bool = False) -> None:
    """首次展开时填充一个动画分类（同一子菜单只填一次）。"""
    if bool(getattr(submenu, "_petcore_populated", False)):
        return
    submenu._petcore_populated = True
    names = _unique_names(entries)
    if group_by_prefix and len(names) > RANDOM_GROUP_THRESHOLD:
        for label, members in _group_names(names):
            if members is None:
                _add_actions(submenu, window, (label,))
                continue
            _add_actions(add_submenu(submenu, label), window, members)
        return
    _add_actions(submenu, window, names)


def _add_lazy_group(
    menu: QMenu,
    label: str,
    window,
    names,
    *,
    group_by_prefix: bool = False,
) -> bool:
    unique = _unique_names(names)
    if not unique:
        return False
    submenu = add_submenu(menu, label)
    submenu.aboutToShow.connect(
        lambda sub=submenu, entries=tuple(unique), grouped=group_by_prefix: _populate_group(
            sub, window, entries, group_by_prefix=grouped
        )
    )
    return True


def _populate_animations(window, menu: QMenu) -> None:
    catalog = getattr(window, "catalog", None)
    if catalog is None:
        return

    added = _add_lazy_group(menu, "待机", window, catalog.names_for(GraphType.IDEL))
    added |= _add_lazy_group(
        menu,
        "转向",
        window,
        [
            spec.name
            for spec in catalog.specs_for(GraphType.MOVE)
            if spec.locate == "in_place"
        ],
    )
    added |= _add_lazy_group(
        menu,
        "移动",
        window,
        [
            spec.name
            for spec in catalog.specs_for(GraphType.MOVE)
            if spec.locate != "in_place"
        ],
    )
    added |= _add_lazy_group(menu, "点击回应", window, catalog.names_for(GraphType.TOUCH_BODY))
    # Keep menu discovery aligned with PetWindow's GraphType lookup.  These
    # groups are optional per character; missing resources simply stay hidden.
    for graph, label in (
        (GraphType.TOUCH_HEAD, "摸头"),
        (GraphType.SAY, "说话"),
        (GraphType.SLEEP, "睡觉"),
        (GraphType.STARTUP, "启动"),
        (GraphType.WORK, "工作"),
        (GraphType.RAISED_DYNAMIC, "悬空/抛掷"),
    ):
        added |= _add_lazy_group(menu, label, window, catalog.names_for(graph))
    added |= _add_lazy_group(
        menu,
        "随机动作",
        window,
        catalog.names_for(GraphType.DEFAULT),
        group_by_prefix=True,
    )
    event_labels = {"balance": "余额"}
    for group in catalog.event_groups():
        added |= _add_lazy_group(
            menu,
            f"事件·{event_labels.get(group, group)}",
            window,
            catalog.event_names(group),
        )
    if not added:
        empty = menu.addAction("（无可用动画）")
        empty.setEnabled(False)


def _populate_speed(window, menu: QMenu) -> None:
    group = QActionGroup(menu)
    group.setExclusive(True)
    current = float(window.config.get("playback_speed") or 1.0)
    for index in range(10, 21):
        value = index / 10.0
        action = menu.addAction(f"{value:.1f}x")
        action.setCheckable(True)
        action.setChecked(abs(current - value) < 0.01)
        group.addAction(action)
        action.triggered.connect(lambda _checked=False, speed=value: window.set_playback_speed(speed))


def _populate_golden_spin(window, menu: QMenu) -> None:
    action = menu.addAction("立即回旋")
    action.triggered.connect(window.trigger_golden_spin)
    direct = menu.addAction("点击触发回旋")
    direct.setCheckable(True)
    direct.setChecked(bool(window.config.get("golden_spin_on_click")))
    direct.triggered.connect(
        lambda checked=False: window.set_config_flag("golden_spin_on_click", bool(checked))
    )
    skip = menu.addAction("点击时跳过点击动画")
    skip.setCheckable(True)
    skip.setChecked(bool(window.config.get("golden_spin_direct")))
    skip.triggered.connect(
        lambda checked=False: window.set_config_flag("golden_spin_direct", bool(checked))
    )


def _build_easter_egg(menu: QMenu, window) -> None:
    # 延迟 import：彩蛋行要 QWidget 相关样式，菜单模块本身保持轻量。
    from .easter_egg_entry import build_easter_egg_entry

    build_easter_egg_entry(menu, window)


def core_items(window) -> dict[str, MenuItem]:
    def populate_characters(menu: QMenu) -> None:
        group = QActionGroup(menu)
        group.setExclusive(True)
        for character in window.available_characters():
            action = menu.addAction(character.name)
            action.setCheckable(True)
            action.setChecked(character.id == window.character_id)
            group.addAction(action)
            action.triggered.connect(
                lambda _checked=False, cid=character.id: window.set_character(cid)
            )
        # 重命名挂在「切换角色」里面，不新增菜单项 id：菜单模板与菜单/设置
        # 一一对应的账（coverage.py）都不用动。
        menu.addSeparator()
        rename = menu.addAction("重命名当前角色…")
        rename.triggered.connect(lambda _checked=False: window.rename_character())

    def populate_scale(menu: QMenu) -> None:
        group = QActionGroup(menu)
        group.setExclusive(True)
        for step in SCALE_STEPS:
            action = menu.addAction(f"{round(step * 100)}%")
            action.setCheckable(True)
            action.setChecked(abs(window.state.scale - step) < 0.001)
            group.addAction(action)
            action.triggered.connect(lambda _checked=False, s=step: window.set_scale(s))
        menu.addSeparator()
        custom = menu.addAction("自定义大小…")
        custom.triggered.connect(lambda: window.open_settings("appearance"))

    def populate_animations(menu: QMenu) -> None:
        _populate_animations(window, menu)

    def populate_speed(menu: QMenu) -> None:
        _populate_speed(window, menu)

    def populate_spin(menu: QMenu) -> None:
        _populate_golden_spin(window, menu)

    items = {
        "animations_hub": MenuItem(
            "animations_hub", "播放动画", populate=populate_animations, icon="play", order=5
        ),
        "character": MenuItem(
            "character", "切换角色", populate=populate_characters, icon="character", order=10
        ),
        "playback_speed": MenuItem(
            "playback_speed", "播放速率", populate=populate_speed, icon="speed", order=15
        ),
        "size": MenuItem("size", "大小", populate=populate_scale, icon="size", order=20),
        # AI 对话 / 看看屏幕 / 截图提问 是 ai.chat 扩展注册的菜单项
        # （`ai.chat:open` / `ai.chat:look_screen` / `ai.chat:screen_capture`），
        # 没装扩展就不会出现；默认布局按这三个 id 把它们摆在「播放动画」下面。
        "drag_physics": MenuItem(
            "drag_physics",
            "拖动物理",
            checked=lambda: bool(window.config.get("drag_physics_enabled")),
            callback=lambda: window.toggle_config_flag("drag_physics_enabled"),
            icon="physics",
            order=25,
        ),
        "no_move": MenuItem(
            "no_move",
            "不自动移动",
            checked=lambda: bool(window.config.get("no_move")),
            callback=lambda: window.toggle_config_flag("no_move"),
            icon="pause",
            order=28,
        ),
        "hide_pet": MenuItem(
            "hide_pet", "隐藏桌宠", callback=window.toggle_hidden, icon="hide", order=30
        ),
        "on_top": MenuItem(
            "on_top",
            "窗口置顶",
            checked=lambda: window.always_on_top,
            callback=window.toggle_always_on_top,
            icon="pin",
            order=40,
        ),
        "autostart": MenuItem(
            "autostart",
            "开机自启",
            checked=autostart_mod.is_enabled,
            callback=_toggle_autostart,
            icon="autostart",
            order=60,
        ),
        "edge_probe": MenuItem(
            "edge_probe",
            "边缘探头",
            checked=lambda: bool(window.config.get("edge_probe_enabled")),
            callback=window.toggle_edge_probe,
            icon="pointer",
            order=55,
        ),
        "return_corner": MenuItem(
            "return_corner",
            "回到右下角",
            callback=window.return_corner,
            icon="corner",
            order=70,
        ),
        "spawn_pet": MenuItem(
            "spawn_pet",
            "生小肥鱼",
            callback=getattr(window, "spawn_pet", None),
            icon="spawn",
            order=72,
        ),
        "clear_spawned_pets": MenuItem(
            "clear_spawned_pets",
            "退出子肥鱼",
            callback=getattr(window, "clear_spawned_pets", None),
            icon="clear",
            order=74,
        ),
        "golden_spin": MenuItem(
            "golden_spin", "黄金回旋", populate=populate_spin, icon="play", order=76
        ),
        "move_to_primary": MenuItem(
            "move_to_primary",
            "移到主屏",
            callback=window.move_to_primary,
            icon="screen",
            order=80,
        ),
        "dynamic_island": MenuItem(
            "dynamic_island",
            "灵动岛",
            checked=lambda: bool(window.config.get("island_enabled")),
            callback=lambda: window.toggle_dynamic_island(),
            icon="pin",
            order=27,
        ),
        "reload_mods": MenuItem(
            "reload_mods",
            "重新加载扩展",
            callback=window.reload_mods,
            icon="refresh",
            order=90,
        ),
        "api_settings": MenuItem(
            "api_settings",
            "API 与账户",
            callback=lambda: window.open_settings("api"),
            icon="network",
            order=95,
        ),
        "show_balance": MenuItem(
            "show_balance",
            "显示余额",
            callback=lambda: getattr(getattr(window, "host", None), "request_balance", lambda *_args, **_kwargs: None)(getattr(window, "slot", 0), force=True),
            icon="network",
            order=97,
        ),
        "check_update": MenuItem(
            "check_update",
            "检查更新",
            callback=lambda: getattr(getattr(window, "host", None), "check_for_updates", lambda: None)(),
            icon="refresh",
            order=98,
        ),
        "easter_egg": MenuItem(
            "easter_egg",
            "彩蛋图片",
            callback=getattr(window, "show_easter_egg", None),
            icon="play",
            order=96,
            builder=lambda menu: _build_easter_egg(menu, window),
        ),
        "open_data": MenuItem(
            "open_data", "打开数据目录", callback=window.open_data_dir, icon="folder", order=100
        ),
        "extensions_hub": MenuItem("extensions_hub", "扩展", icon="application", order=110),
        "modern_settings": MenuItem(
            "modern_settings", "桌宠设置", callback=window.open_settings, icon="settings", order=120
        ),
        "quit": MenuItem(
            "quit",
            "退出这只" if int(getattr(window, "slot", 0) or 0) else "退出",
            callback=getattr(window, "quit_from_menu", None)
            or getattr(window, "quit_app", None),
            icon="quit",
            order=140,
        ),
    }
    return items


def availability(window) -> dict[str, bool]:
    manager = getattr(getattr(window, "host", None), "pets", None)
    return {
        "autostart": sys.platform == "win32",
        "move_to_primary": _multi_screen(),
        "spawn_pet": manager is not None,
        "clear_spawned_pets": manager is not None,
        "easter_egg": callable(getattr(window, "show_easter_egg", None)),
    }


def install_core_menu(model: MenuModel, window) -> tuple[set[str], set[str]]:
    items = core_items(window)
    for item in items.values():
        model.register(item)
    flags = availability(window)
    model.set_availability({item_id: flags.get(item_id, True) for item_id in items})
    registered = set(items)
    available = {item_id for item_id in items if flags.get(item_id, True)}
    return registered, available
