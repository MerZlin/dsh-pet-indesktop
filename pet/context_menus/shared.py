# -*- coding: utf-8 -*-
"""Stable leaf-menu primitives shared by the two independent layouts."""
from __future__ import annotations

import sys

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QActionGroup, QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import QMenu

from .. import autostart as autostart_mod
from .. import catalog
from ..harness_launcher import launch_harness_gui
from ..updater import QUARK_PAN_URL, REPO_URL
from .icons import fitted_pet_pixmap_icon, pet_avatar_menu_icon, vector_menu_icon
from .menu_styles.common import inherit_menu_style

DEEPSEEK_WEB_URL = "https://chat.deepseek.com/"


class _AnimationIconSignals(QObject):
    ready = Signal(object)


class _AnimationIconWorker(QRunnable):
    def __init__(self, loader, animation_name: str):
        super().__init__()
        self.loader = loader
        self.animation_name = animation_name
        self.signals = _AnimationIconSignals()

    def run(self) -> None:
        self.signals.ready.emit(self.loader(self.animation_name))


def _root_menu(menu: QMenu) -> QMenu:
    root = menu
    while isinstance(root.parent(), QMenu):
        root = root.parent()
    return root


def take_deferred_menu_callbacks(menu: QMenu) -> list:
    """Take commands that must run after the native menu tracking loop."""
    callbacks = list(getattr(menu, "_deferred_callbacks", ()))
    menu._deferred_callbacks = []
    return callbacks


def connect_action(action, callback) -> None:
    def invoke(_checked=False, action=action, callback=callback) -> None:
        parent = action.parent()
        if (
            bool(action.property("closeOnTrigger"))
            and isinstance(parent, QMenu)
            and parent.isVisible()
        ):
            root = _root_menu(parent)
            root._deferred_callbacks = list(
                getattr(root, "_deferred_callbacks", ())
            ) + [callback]
            root.close()
            return
        callback()

    action.triggered.connect(invoke)


def add_action(
    menu: QMenu, text: str, icon_name: str | None, callback=None, *,
    close_on_trigger: bool = False,
):
    action = menu.addAction(vector_menu_icon(menu, icon_name) if icon_name else QIcon(), text)
    action.setProperty("closeOnTrigger", bool(close_on_trigger))
    if callback is not None:
        connect_action(action, callback)
    return action


def add_submenu(menu: QMenu, text: str, icon_name: str | None = None) -> QMenu:
    submenu = QMenu(text, menu)
    menu.addMenu(submenu)
    menu._owned_submenus = getattr(menu, "_owned_submenus", []) + [submenu]
    inherit_menu_style(menu, submenu)
    if icon_name:
        submenu.setIcon(vector_menu_icon(menu, icon_name))
    return submenu


def build_animation_categories(
    menu: QMenu, pet, *, icons: bool, legacy_labels: bool = False,
    leaf_role_icons: bool = False,
) -> None:
    categories = (
        ("待机", pet.idles, pet._switch),
        ("转向", pet.turns, pet._switch),
        ("移动", pet.moves, pet._trigger_move),
        ("点击回应", pet.clicks, pet._switch),
        ("随机动作", pet.acts, pet._switch),
    )
    for label, entries, callback in categories:
        if not entries:
            continue
        submenu = QMenu(f"动画 · {label}" if legacy_labels else label, menu)
        menu.addMenu(submenu)
        menu._owned_submenus = getattr(menu, "_owned_submenus", []) + [submenu]
        inherit_menu_style(menu, submenu)
        if icons:
            submenu.setIcon(vector_menu_icon(menu, "play"))
        icon_actions = []
        lazy_actions = []
        for name in entries:
            action = submenu.addAction(QIcon(), name) if leaf_role_icons else submenu.addAction(name)
            connect_action(action, lambda name=name, callback=callback: callback(name))
            if leaf_role_icons:
                icon_actions.append((action, name))
                cached_loader = getattr(pet, "animation_icon_cached_image", None)
                cached = cached_loader(name) if callable(cached_loader) else None
                if cached is not None and not cached.isNull():
                    action.setIcon(fitted_pet_pixmap_icon(submenu, QPixmap.fromImage(cached)))
                else:
                    # A neutral loading glyph avoids a blank/jumping text
                    # column while the representative frame is decoded.
                    action.setIcon(vector_menu_icon(submenu, "loading"))
                    lazy_actions.append((action, name))
        if lazy_actions:
            # Decoding dozens of WebM frames while the root menu is being
            # constructed made the very first right-click block for seconds.
            # Load only the category the user actually opens in a two-thread
            # pool and keep completed icons on their QAction for later opens.
            def refresh_cached_icons(
                submenu=submenu, icon_actions=tuple(icon_actions), pet=pet,
            ) -> None:
                """Only alter QAction geometry before show or after hide."""
                if submenu.isVisible():
                    return
                cached_loader = getattr(pet, "animation_icon_cached_image", None)
                if not callable(cached_loader):
                    return
                for action, animation_name in icon_actions:
                    image = cached_loader(animation_name)
                    if image is not None and not image.isNull():
                        action.setIcon(fitted_pet_pixmap_icon(submenu, QPixmap.fromImage(image)))

            def start_loading(submenu=submenu, lazy_actions=tuple(lazy_actions), pet=pet) -> None:
                loader = getattr(pet, "animation_icon_image", None)
                if not callable(loader):
                    return
                if not hasattr(submenu, "_animation_icon_pending"):
                    submenu._animation_icon_pending = list(lazy_actions)
                    submenu._animation_icon_requested = set()

                def pump() -> None:
                    while (
                        len(submenu._animation_icon_workers) < 2
                        and submenu._animation_icon_pending
                    ):
                        action, animation_name = submenu._animation_icon_pending.pop(0)
                        if animation_name in submenu._animation_icon_requested:
                            continue
                        submenu._animation_icon_requested.add(animation_name)
                        launch(action, animation_name)

                def launch(action, animation_name) -> None:
                    worker = _AnimationIconWorker(loader, animation_name)

                    def apply_image(
                        image, action=action, worker=worker, submenu=submenu,
                    ) -> None:
                        if worker in submenu._animation_icon_workers:
                            submenu._animation_icon_workers.remove(worker)
                        # setIcon() invalidates QMenu's action geometry. Doing
                        # that dozens of times on a visible, scrollable menu
                        # corrupts its scroll layout and blocks hover events.
                        if (
                            not submenu.isVisible()
                            and image is not None
                            and not image.isNull()
                        ):
                            action.setIcon(fitted_pet_pixmap_icon(submenu, QPixmap.fromImage(image)))
                            submenu.update()
                        pump()

                    worker.signals.ready.connect(apply_image)
                    submenu._animation_icon_workers.append(worker)
                    submenu._animation_icon_pool.start(worker)

                pump()

            submenu.aboutToShow.connect(refresh_cached_icons)
            submenu.aboutToShow.connect(start_loading)
            submenu.aboutToHide.connect(
                lambda refresh=refresh_cached_icons: QTimer.singleShot(0, refresh)
            )
            pool = QThreadPool(submenu)
            pool.setMaxThreadCount(2)
            submenu._animation_icon_pool = pool
            submenu._animation_icon_workers = []


def build_speed_menu(menu: QMenu, pet, *, icons: bool = True) -> QMenu:
    submenu = add_submenu(menu, "播放速率", "speed" if icons else None)
    group = QActionGroup(submenu)
    group.setExclusive(True)
    for i in range(10, 21):
        value = i / 10.0
        action = submenu.addAction(f"{value:.1f}x")
        action.setCheckable(True)
        action.setChecked(abs(pet.playback_speed - value) < 0.01)
        group.addAction(action)
        connect_action(action, lambda value=value: pet.set_playback_speed(value))
    return submenu


def build_character_menu(menu: QMenu, pet, *, icons: bool = True) -> QMenu:
    submenu = add_submenu(menu, "切换角色", "character" if icons else None)
    group = QActionGroup(submenu)
    group.setExclusive(True)
    current = str(pet.cfg.get("character", catalog.DEFAULT_CHARACTER))
    for character_id in catalog.list_available_characters():
        alias_fn = getattr(pet.cfg, 'character_alias', None)
        alias = alias_fn(character_id) if callable(alias_fn) else ''
        label = alias or catalog.character_display_name(character_id)
        action = submenu.addAction(label)
        action.setCheckable(True)
        action.setChecked(character_id == current)
        group.addAction(action)
        action.setProperty("closeOnTrigger", True)
        connect_action(action, lambda character_id=character_id: pet._request_switch_character(character_id))
    # 角色显示名别名（空名恢复默认）
    rename = getattr(pet, "_rename_character", None)
    if callable(rename):
        submenu.addSeparator()
        add_action(submenu, "重命名当前角色…", None, rename, close_on_trigger=True)
    return submenu


def add_proactive_menu(menu: QMenu, pet) -> None:
    """主动识屏二级菜单（仅 Windows 且有聊天/视觉能力时显示）。"""
    import sys as _sys
    if _sys.platform != 'win32':
        return
    if getattr(pet, 'on_open_chat', None) is None:
        return
    from ..proactive import effective_proactive_config

    sub = add_submenu(menu, "主动识屏", None)
    pro_cfg = effective_proactive_config(pet.cfg.get('proactive_screen', {}))

    def _toggle(text, checked, handler):
        act = sub.addAction(text)
        act.setCheckable(True)
        act.setChecked(bool(checked))
        act.toggled.connect(handler)
        return act

    _toggle('开启主动识屏', pro_cfg.get('enabled', False), pet._toggle_proactive_enabled)
    _toggle('鼠标穿透时仍允许主动识屏', pro_cfg.get('allow_when_mouse_through', True),
            lambda on: pet._set_proactive_option('allow_when_mouse_through', on))
    _toggle('触发前先兆提示', pro_cfg.get('pre_cue', True),
            lambda on: pet._set_proactive_option('pre_cue', on))
    _toggle('仅当我闲置时触发', pro_cfg.get('require_idle', False),
            lambda on: pet._set_proactive_option('require_idle', on))
    _toggle('dry-run 验证模式', pro_cfg.get('dry_run', False),
            lambda on: pet._set_proactive_option('dry_run', on))
    sub.addSeparator()
    if getattr(pet, 'on_open_settings', None) is not None:
        add_action(sub, '打开设置…', None, pet.on_open_settings, close_on_trigger=True)


def add_agent_link_menu(menu: QMenu, pet) -> None:
    """Agent 联动二级菜单（5 个 Agent 独立开关，失败/拒绝自动回滚勾选）。"""
    sub = add_submenu(menu, "Agent 联动", None)
    agent_cfg = dict(pet.cfg.get('agent_link', {}))
    for agent_key, agent_label in (
        ('dsh', 'DeepSeek Harness (DSH)'),
        ('claude', 'Claude Code'),
        ('cursor', 'Cursor'),
        ('opencode', 'OpenCode'),
    ):
        act = sub.addAction(agent_label)
        act.setCheckable(True)
        act.setChecked(bool(agent_cfg.get(agent_key, False)))
        act.toggled.connect(lambda on, k=agent_key, a=act: pet._toggle_agent_link(k, on, a))


def build_size_menu(menu: QMenu, pet, *, icons: bool = True) -> QMenu:
    submenu = add_submenu(menu, "大小", "size" if icons else None)
    group = QActionGroup(submenu)
    group.setExclusive(True)
    for scale in catalog.SCALE_STEPS:
        px = int(round(catalog.CANVAS_W * scale))
        action = submenu.addAction(f"{px}px")
        action.setCheckable(True)
        action.setChecked(abs(pet.scale - scale) < 0.02)
        group.addAction(action)
        connect_action(action, lambda scale=scale: pet.change_scale(scale))
    return submenu


def add_drag_physics(menu: QMenu, pet, *, icons: bool = True):
    action = add_action(menu, "拖动物理", "physics" if icons else None)
    action.setCheckable(True)
    action.setChecked(pet.drag_physics)
    action.toggled.connect(lambda enabled, pet=pet: pet.set_drag_physics(enabled))
    return action


def add_return_corner(menu: QMenu, pet, *, icons: bool = True):
    return add_action(menu, "回到右下角", "corner" if icons else None, pet._go_default_corner)


def add_hide_pet(menu: QMenu, pet, *, icons: bool = True):
    # close_on_trigger：隐藏后菜单随之关闭，避免菜单悬空无法找回桌宠
    return add_action(menu, "隐藏桌宠", "hide" if icons else None, pet.hide, close_on_trigger=True)


def add_look_screen(menu: QMenu, pet, *, icons: bool = True):
    callback = getattr(pet, "on_look_screen", None)
    if callback is None:
        return None
    return add_action(menu, "看看屏幕", "screen" if icons else None, callback, close_on_trigger=True)


def add_balance(menu: QMenu, pet, *, icons: bool = True):
    callback = getattr(pet, "on_show_balance", None)
    if callback is None:
        return None
    return add_action(menu, "DeepSeek 余额", "balance" if icons else None, lambda: callback(pet), close_on_trigger=True)


def add_no_move(menu: QMenu, pet, *, icons: bool = True):
    action = add_action(menu, "不移动", "pause" if icons else None)
    action.setCheckable(True)
    action.setChecked(pet.no_move)
    action.toggled.connect(lambda enabled, pet=pet: pet.set_no_move(enabled))
    return action


def add_on_top(menu: QMenu, pet, *, icons: bool = True):
    action = add_action(menu, "窗口置顶", "pin" if icons else None)
    action.setCheckable(True)
    action.setChecked(bool(pet.cfg.get("on_top", True)))
    action.toggled.connect(lambda enabled, pet=pet: pet.set_on_top(enabled))
    return action


def add_autostart(menu: QMenu, pet=None, *, icons: bool = True):
    action = add_action(menu, "开机自启", "autostart" if icons else None)
    action.setCheckable(True)
    action.setChecked(autostart_mod.is_enabled())
    def toggle(enabled: bool) -> None:
        autostart_mod.set_enabled(enabled)
        if pet is not None:
            pet.cfg.set("autostart_wanted", bool(enabled))
            pet.cfg.save()

    action.toggled.connect(toggle)
    return action


def add_spawn_pet(menu: QMenu, pet):
    callback = getattr(pet, "on_spawn_pet", None)
    if callback is None:
        return None
    if str(menu.property("menuStyle") or "") == "modern":
        icon = pet_avatar_menu_icon(menu, pet)
    else:
        icon = QIcon()
    action = menu.addAction(icon, "生小肥鱼")
    action.setProperty("closeOnTrigger", True)
    connect_action(action, callback)
    return action


def add_harness(menu: QMenu, pet, *, icons: bool = True):
    return add_action(menu, "启动 DeepSeek Harness", "harness" if icons else None, lambda: launch_harness_gui(pet), close_on_trigger=True)


def open_deepseek_web() -> bool:
    return bool(QDesktopServices.openUrl(QUrl(DEEPSEEK_WEB_URL)))


def add_deepseek_web(menu: QMenu, *, icons: bool = True):
    return add_action(menu, "打开网页版 DeepSeek", "web" if icons else None, open_deepseek_web, close_on_trigger=True)


def add_update_help(menu: QMenu, pet, *, icons: bool = True):
    submenu = add_submenu(menu, "更新与帮助", "update" if icons else None)
    callback = getattr(pet, "on_check_update", None)
    if callback is not None:
        add_action(submenu, "检查更新", "update" if icons else None, lambda: callback(pet), close_on_trigger=True)
    add_action(submenu, "GitHub 项目页", "web" if icons else None, lambda: QDesktopServices.openUrl(QUrl(REPO_URL)), close_on_trigger=True)
    if sys.platform == "win32":
        add_action(submenu, "夸克网盘下载", "download" if icons else None, lambda: QDesktopServices.openUrl(QUrl(QUARK_PAN_URL)), close_on_trigger=True)
    return submenu


def add_template_switch(menu: QMenu, pet, label: str, target: str, *, icons: bool = True):
    def switch_and_reopen() -> None:
        pet.set_context_menu_template(target)
        reopen = getattr(pet, "reopen_context_menu", None)
        if callable(reopen):
            reopen(menu)

    return add_action(menu, label, "template" if icons else None, switch_and_reopen)


def add_quit(menu: QMenu, pet, *, icons: bool = True):
    return add_action(menu, "退出", "exit" if icons else None, pet._request_quit, close_on_trigger=True)
