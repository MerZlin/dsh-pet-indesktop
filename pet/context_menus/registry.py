"""Action registration and tree rendering for the shared Menu Action Model."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QWidgetAction

from ..config import DEFAULT_MENU_EASTER_EGG
from .. import features as features_mod
from ..weather.menu import (
    WEATHER_DISABLED_REASON,
    add_show_weather,
    add_weather_cities,
    weather_menu_enabled,
)
from .fun_entry import add_ojingjing_entry
from .icons import custom_file_menu_icon, vector_menu_icon
from .quick_launch import add_quick_launch_menu, configured_quick_apps
from .shared import (
    MUSIC_ALIGN_DISABLED_REASON,
    NETWORK_INFO_DISABLED_REASON,
    QUARK_PAN_URL,
    REPO_URL,
    add_action,
    add_agent_link_menu,
    add_autostart,
    add_balance,
    add_clear_spawned_pets,
    add_deepseek_web,
    add_drag_physics,
    add_edge_probe,
    add_golden_spin,
    add_harness,
    add_hide_pet,
    add_look_screen,
    add_memory_trim,
    add_agent_cost,
    add_music_lyric_align,
    add_music_next,
    add_music_open_kugou,
    add_music_open_netease,
    add_music_open_qqmusic,
    add_music_open_soda,
    add_music_pause,
    add_music_prev,
    add_music_quit,
    add_no_move,
    add_on_top,
    add_proactive_menu,
    add_quiet_mode_menu,
    add_quit,
    add_return_corner,
    add_screen_capture_menu,
    add_spawn_pet,
    add_submenu,
    add_network_info,
    network_info_menu_enabled,
    build_animation_categories,
    build_character_menu,
    build_size_menu,
    build_speed_menu,
)


ACTION_LABELS = {
    "ojingjing": "厉害了我的鲸", "chat": "AI 对话", "look_screen": "看看屏幕",
    "screen_capture": "截图提问",
    "animations_hub": "播放动画", "character": "切换角色", "playback_speed": "播放速率",
    "size": "大小", "drag_physics": "拖动物理", "no_move": "不移动",
    "on_top": "窗口置顶", "autostart": "开机自启",
    "return_corner": "回到右下角", "hide_pet": "隐藏桌宠",
    "spawn_pet": "生小肥鱼", "clear_spawned_pets": "退出子肥鱼",
    "golden_spin": "黄金回旋", "edge_probe": "边缘探头",
    "music": "音乐", "music_pause": "暂停 / 播放", "music_next": "切歌",
    "music_prev": "上一首",
    "music_quit": "退出音乐模式", "music_open_netease": "打开网易云并播放",
    "music_open_qqmusic": "打开QQ音乐并播放",
    "music_open_kugou": "打开酷狗音乐并播放",
    "music_open_soda": "打开汽水音乐并播放",
    "music_lyric_align": "歌词对齐",
    "agent_cost": "显示本轮消费",
    "quick_launch": "快捷启动", "balance": "DeepSeek 余额",
    "memory_trim": "回收内存",
    "harness": "启动 DeepSeek Harness", "deepseek_web": "打开网页版 DeepSeek",
    "check_update": "检查更新", "github_project": "GitHub 项目页",
    "quark_download": "夸克网盘下载", "agent_link": "Agent 联动",
    "proactive_screen": "主动识屏", "todo_panel": "待办提醒",
    "quiet_mode": "免打扰",
    "weather": "查看天气", "weather_cities": "天气城市",
    "network_info": "网络信息",
    "modern_settings": "桌宠设置", "quit": "退出",
}


Builder = Callable[[QMenu, object], object]
Availability = Callable[[object], bool]
Enablement = Callable[[object], bool]


ACTION_ICONS = {
    "ojingjing": "pet", "chat": "chat", "look_screen": "screen",
    "screen_capture": "size",
    "animations_hub": "play", "character": "character", "playback_speed": "speed",
    "size": "size", "drag_physics": "physics", "no_move": "pause",
    "on_top": "pin", "autostart": "autostart",
    "return_corner": "corner", "hide_pet": "hide",
    "spawn_pet": "spawn", "clear_spawned_pets": "clear",
    "golden_spin": "play", "edge_probe": "corner",
    "music_next": "play", "music_prev": "play", "music_quit": "stop", "agent_cost": "balance",
    "music": "play", "music_pause": "pause", "music_open_netease": "play",
    "music_open_qqmusic": "play", "music_open_kugou": "play",
    "music_open_soda": "play", "music_lyric_align": "play",
    "quick_launch": "application", "balance": "balance", "harness": "harness",
    "memory_trim": "clear",
    "deepseek_web": "web", "check_update": "update", "github_project": "web",
    "quark_download": "download", "agent_link": "automation",
    "proactive_screen": "screen", "todo_panel": "todo",
    "quiet_mode": "pause",
    "weather": "weather", "weather_cities": "weather",
    "network_info": "web",
    "modern_settings": "settings", "quit": "quit",
    "voice_chime_now": "chat", "voice_chime_toggle": "chat",
    "divination_draw": "todo",
    "festival_now": "todo", "festival_toggle": "todo",
}

CUSTOM_ICON_CHOICES = (
    ("默认图标", "default"), ("无图标", "none"),
    ("对话", "chat"), ("屏幕", "screen"), ("播放", "play"),
    ("角色", "character"), ("速度", "speed"), ("尺寸", "size"),
    ("桌宠", "pet"), ("交互", "interaction"), ("置顶", "pin"),
    ("隐藏", "hide"), ("应用", "application"), ("余额", "balance"),
    ("清除", "clear"), ("网页", "web"), ("下载", "download"), ("更新", "update"),
    ("自动化", "automation"), ("设置", "settings"), ("待办", "todo"),
)


@dataclass(frozen=True)
class MenuActionSpec:
    build: Builder
    available: Availability = lambda _pet: True
    enabled: Enablement = lambda _pet: True
    disabled_reason: str = "当前功能未启用"


def _callback_available(name: str) -> Availability:
    return lambda pet: callable(getattr(pet, name, None))


def _music_lyric_configured(pet) -> bool:
    """音乐相关菜单项是否出现：只看设置里有没有开启歌词功能。

    刻意**不**依赖实时播放状态——否则菜单结构会随"此刻有没有在放歌"变来变去
    （测试也会因此依赖机器状态）。没在播时改为置灰（见 enabled）。
    """
    return bool(pet.cfg.get("music_lyric_enabled", False))


def _memory_trim_available(_pet) -> bool:
    """「回收内存」只对 Windows 显示：其它平台没有 EmptyWorkingSet。

    判定走 ``pet/memory_trim.py::is_supported``（与执行层同一判据），不另写一份
    ``sys.platform`` 比较——菜单给不给看和执行能不能跑必须是同一个真相。
    """
    from .. import memory_trim

    return memory_trim.is_supported()


# 「查看天气 / 天气城市」的判据与原因文案收在共享建造器模块（`weather_menu`），
# 这里只做转发：开关真值只有一份，注册表的 enabled 与建造器写的 tooltip 同源。
# （历史背景：旧版扁平菜单曾直调建造器绕过注册表，门只写在注册表上就会漏；
# 旧版菜单已于 2026-10-08 退役，但"判定收在建造器里"的口径保持不动。）
_WEATHER_DISABLED_REASON = WEATHER_DISABLED_REASON
_weather_enabled = weather_menu_enabled


def _music_align_ready(pet) -> bool:
    """「歌词对齐」判据的转发（实现与原因文案在 ``shared.music_align_ready``）。

    门收在共享建造器里（本函数转发的 ``shared.music_align_ready``）：
    "能对齐"的判定与置灰必须同源，否则会出现"入口在、点了没反应"的死项。
    """
    from .shared import music_align_ready

    return music_align_ready(pet)


def _build_chat(menu, pet):
    return add_action(menu, "AI 对话", "chat", pet.on_open_chat, close_on_trigger=True)


def _build_animations(menu, pet):
    submenu = add_submenu(menu, "播放动画", "play")
    build_animation_categories(submenu, pet, icons=False, leaf_role_icons=True)
    return submenu


def _build_settings(menu, pet):
    return add_action(menu, "桌宠设置", "settings", pet.on_open_modern_settings, close_on_trigger=True)


def _build_todo_panel(menu, pet):
    return add_action(menu, "待办提醒", "todo", pet.on_open_todo_panel, close_on_trigger=True)


def _build_voice_chime_now(menu, pet):
    return add_action(menu, "立即报时", "chat", pet.on_voice_chime_now, close_on_trigger=True)


def _flag_toggle_spec(key: str, on_label: str, off_label: str, icon: str,
                      callback: str):
    """布尔开关菜单项的规约工厂：按配置当前值翻转标签，点击回回调。

    语音报时开关与节日提醒开关此前逐字同构，这里把「读配置 → 选标签 →
    add_action」收成一处；标签翻转、图标、回调与 close_on_trigger 语义逐点
    不变。注意 `enabled` 只影响展示标签，不影响回调可用性（可用性仍由
    `MenuActionSpec.available` 的 `_callback_available` 判定）。
    """
    def _build(menu, pet):
        cfg = getattr(pet, "cfg", None)
        enabled = bool(cfg.get(key, False)) if cfg is not None else False
        return add_action(
            menu, on_label if enabled else off_label, icon,
            getattr(pet, callback), close_on_trigger=True,
        )

    return _build


def _network_info_enabled(pet) -> bool:
    """「网络信息」入口的运行时启用态：设置里开着才可用（默认开）。

    **只影响 enabled，不影响 available**：关掉开关时入口保留布局位置、渲染成
    "灰掉 + 说明"（CONTEXT.md 的 Runtime Disabled），不把节点从菜单树里过滤掉——
    过滤只用于"能力不可用"，本例里就是回调缺失（纯桌宠变体）。
    """
    return network_info_menu_enabled(pet)


_build_voice_chime_toggle = _flag_toggle_spec(
    "voice_chime_enabled", "关闭语音报时", "启用语音报时", "chat",
    "on_toggle_voice_chime",
)

_build_festival_toggle = _flag_toggle_spec(
    "festival_reminder_enabled", "关闭节日提醒", "启用节日提醒", "todo",
    "on_toggle_festival",
)


def _build_festival_now(menu, pet):
    return add_action(menu, "今日节日", "todo", pet.on_festival_now, close_on_trigger=True)


def _build_divination_draw(menu, pet):
    """「抽支签」：每天一支，点开即出（当天再点显示同一支）。"""
    return add_action(menu, "抽支签", "todo", pet.on_divination_draw, close_on_trigger=True)


def _build_check_update(menu, pet):
    return add_action(menu, "检查更新", "update", lambda: pet.on_check_update(pet), close_on_trigger=True)


def _build_github(menu, _pet):
    return add_action(
        menu,
        "GitHub 项目页",
        "web",
        lambda: QDesktopServices.openUrl(QUrl(REPO_URL)),
        close_on_trigger=True,
    )


def _build_quark(menu, _pet):
    return add_action(
        menu,
        "夸克网盘下载",
        "download",
        lambda: QDesktopServices.openUrl(QUrl(QUARK_PAN_URL)),
        close_on_trigger=True,
    )


class MenuActionRegistry:
    """Resolve capability-aware action IDs and render a resolved menu tree."""

    def __init__(self) -> None:
        self._specs = {
            "ojingjing": MenuActionSpec(
                lambda menu, pet: add_ojingjing_entry(
                    menu, pet.cfg.get("menu_easter_egg", DEFAULT_MENU_EASTER_EGG)
                ),
                enabled=lambda pet: bool(
                    pet.cfg.get("menu_easter_egg", DEFAULT_MENU_EASTER_EGG).get("enabled", True)
                ),
                disabled_reason="彩蛋入口已在设置中停用",
            ),
            "chat": MenuActionSpec(_build_chat, _callback_available("on_open_chat")),
            "look_screen": MenuActionSpec(add_look_screen, _callback_available("on_look_screen")),
            # 「截图」单一入口（框选识屏）：动作在覆盖层松手后的动作条上选，
            # 注册表这一层只表达"宿主有没有这套面"。
            "screen_capture": MenuActionSpec(
                add_screen_capture_menu, _callback_available("on_screen_capture")
            ),
            "animations_hub": MenuActionSpec(_build_animations),
            "character": MenuActionSpec(lambda menu, pet: build_character_menu(menu, pet)),
            "playback_speed": MenuActionSpec(lambda menu, pet: build_speed_menu(menu, pet)),
            "size": MenuActionSpec(lambda menu, pet: build_size_menu(menu, pet)),
            "drag_physics": MenuActionSpec(add_drag_physics),
            "no_move": MenuActionSpec(add_no_move),
            "on_top": MenuActionSpec(add_on_top),
            "autostart": MenuActionSpec(lambda menu, pet: add_autostart(menu, pet)),
            "return_corner": MenuActionSpec(add_return_corner),
            "hide_pet": MenuActionSpec(add_hide_pet),
            "spawn_pet": MenuActionSpec(add_spawn_pet, _callback_available("on_spawn_pet")),
            "clear_spawned_pets": MenuActionSpec(
                add_clear_spawned_pets,
                _callback_available("on_clear_spawned_pets"),
            ),
            # 始终可用：消费统计与歌词无关，不该被歌词开关卡住。
            "agent_cost": MenuActionSpec(add_agent_cost),
            "music_pause": MenuActionSpec(add_music_pause, _music_lyric_configured),
            "music_next": MenuActionSpec(add_music_next, _music_lyric_configured),
            "music_prev": MenuActionSpec(add_music_prev, _music_lyric_configured),
            "music_quit": MenuActionSpec(add_music_quit, _music_lyric_configured),
            "music_lyric_align": MenuActionSpec(
                add_music_lyric_align,
                _music_lyric_configured,
                enabled=_music_align_ready,
                disabled_reason=MUSIC_ALIGN_DISABLED_REASON,
            ),
            "music_open_netease": MenuActionSpec(add_music_open_netease),
            "music_open_qqmusic": MenuActionSpec(add_music_open_qqmusic),
            # 酷狗 / 汽水与上面两条同源：同一个建造器 + 同一个启动 worker，只有
            # player_key 不同。可用性一律 True（入口位置由布局树保留），装没装由
            # 运行时启用态表达（CONTEXT.md：Runtime Disabled 保留位置 + 解释原因）。
            "music_open_kugou": MenuActionSpec(add_music_open_kugou),
            "music_open_soda": MenuActionSpec(add_music_open_soda),
            # 平台门在 menu_layout 的 available_actions 过滤里生效（非 Windows
            # 整条不出现）；builder 自身也有同一道门，legacy 扁平布局直接调它。
            "memory_trim": MenuActionSpec(add_memory_trim, _memory_trim_available),
            "golden_spin": MenuActionSpec(
                add_golden_spin,
                _callback_available("trigger_golden_spin"),
            ),
            "edge_probe": MenuActionSpec(add_edge_probe),
            "quick_launch": MenuActionSpec(
                lambda menu, pet: add_quick_launch_menu(menu, pet.cfg),
                enabled=lambda pet: bool(configured_quick_apps(pet.cfg)),
                disabled_reason="尚未配置快捷启动应用",
            ),
            "balance": MenuActionSpec(add_balance, _callback_available("on_show_balance")),
            # harness 属系统工具模块：可用性按**构建变体能力**判（no-chat 变体
            # 不出场是既有语义，test_sprite_menu_parity 钉着），不再误绑 chat 的
            # 运行回调——chat 模块停用时它照常出现，模块显隐归功能投影
            # （system_tools）负责。
            "harness": MenuActionSpec(
                add_harness,
                lambda pet: bool(getattr(pet, "chat_build_capable", True)),
            ),
            "deepseek_web": MenuActionSpec(lambda menu, pet: add_deepseek_web(menu)),
            "check_update": MenuActionSpec(_build_check_update, _callback_available("on_check_update")),
            "github_project": MenuActionSpec(_build_github),
            "quark_download": MenuActionSpec(_build_quark, lambda _pet: sys.platform == "win32"),
            "agent_link": MenuActionSpec(add_agent_link_menu),
            "proactive_screen": MenuActionSpec(
                add_proactive_menu,
                lambda pet: sys.platform == "win32" and callable(getattr(pet, "on_open_chat", None)),
            ),
            # 免打扰：进程级状态，任何宿主（PetWindow / SpriteMenuFacade）都可用，
            # 不做能力判定——菜单里永远给得出入口，用户不必先找开关。
            "quiet_mode": MenuActionSpec(add_quiet_mode_menu),
            "modern_settings": MenuActionSpec(
                _build_settings, _callback_available("on_open_modern_settings")
            ),
            "todo_panel": MenuActionSpec(
                _build_todo_panel, _callback_available("on_open_todo_panel")
            ),
            "voice_chime_now": MenuActionSpec(
                _build_voice_chime_now, _callback_available("on_voice_chime_now")
            ),
            "voice_chime_toggle": MenuActionSpec(
                _build_voice_chime_toggle, _callback_available("on_toggle_voice_chime")
            ),
            "festival_now": MenuActionSpec(
                _build_festival_now, _callback_available("on_festival_now")
            ),
            "festival_toggle": MenuActionSpec(
                _build_festival_toggle, _callback_available("on_toggle_festival")
            ),
            "divination_draw": MenuActionSpec(
                _build_divination_draw, _callback_available("on_divination_draw")
            ),
            # 天气：回调缺失 = 能力不可用（隐藏）；开关关掉 = 运行时置灰、位置保留
            # （CONTEXT.md 的 Capability Unavailable / Runtime Disabled 口径）。
            "weather": MenuActionSpec(
                add_show_weather,
                _callback_available("on_show_weather"),
                enabled=_weather_enabled,
                disabled_reason=_WEATHER_DISABLED_REASON,
            ),
            "weather_cities": MenuActionSpec(
                add_weather_cities,
                _callback_available("on_show_weather"),
                enabled=_weather_enabled,
                disabled_reason=_WEATHER_DISABLED_REASON,
            ),
            "network_info": MenuActionSpec(
                add_network_info,
                _callback_available("on_network_info"),
                enabled=_network_info_enabled,
                disabled_reason=NETWORK_INFO_DISABLED_REASON,
            ),
            "quit": MenuActionSpec(add_quit),
        }

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(self._specs)

    def available_ids(self, pet) -> frozenset[str]:
        # 功能模块投影：未激活模块的动作直接从「可用集」剔除——布局解析会
        # 顺带丢空子菜单、规整分隔线，且不写回用户的自定义布局（重开即恢复）。
        # 快照未冻结（测试/工具）时 is_active 恒 True，行为与旧版一致。
        from .. import feature_activation

        return frozenset(
            action_id
            for action_id, spec in self._specs.items()
            if spec.available(pet)
            and feature_activation.is_active(
                features_mod.feature_for_action(action_id) or ""
            )
        )

    def enabled_ids(self, pet) -> frozenset[str]:
        return frozenset(
            action_id
            for action_id, spec in self._specs.items()
            if spec.available(pet) and spec.enabled(pet)
        )

    def disabled_reason(self, action_id: str) -> str:
        spec = self._specs.get(action_id)
        return spec.disabled_reason if spec is not None else "当前不可用"

    def default_icon(self, action_id: str) -> str:
        return ACTION_ICONS.get(action_id, "")

    def label(self, action_id: str) -> str:
        return ACTION_LABELS.get(action_id, action_id)

    def icon(self, widget, action_id: str, override=None) -> QIcon:
        """Resolve semantic or local-file presentation with a safe fallback."""
        if isinstance(override, dict) and override.get("kind") == "file":
            custom = custom_file_menu_icon(widget, override)
            if not custom.isNull():
                return custom
            override = None
        if override == "none":
            return QIcon()
        name = str(override or self.default_icon(action_id) or "")
        return vector_menu_icon(widget, name) if name else QIcon()

    def populate(self, menu: QMenu, pet, nodes, *, enabled_actions=None) -> None:
        enabled = frozenset(enabled_actions) if enabled_actions is not None else self.enabled_ids(pet)
        explicit_separators = any(node.get("type") == "separator" for node in nodes)
        previous_section = None
        rendered = 0
        for node in nodes:
            if node.get("type") == "separator":
                if rendered and menu.actions() and not menu.actions()[-1].isSeparator():
                    menu.addSeparator()
                previous_section = None
                continue
            section = node.get("section")
            if (
                not explicit_separators
                and rendered and section and previous_section and section != previous_section
            ):
                menu.addSeparator()
            built = None
            if node.get("type") == "submenu":
                submenu = add_submenu(
                    menu,
                    str(node.get("alias") or node.get("label") or ""),
                )
                self.populate(
                    submenu, pet, node.get("children", ()), enabled_actions=enabled,
                )
                built = submenu
                if "icon" in node:
                    submenu.menuAction().setIcon(
                        self.icon(menu, str(node.get("id") or ""), node.get("icon"))
                    )
            else:
                action_id = str(node.get("id") or "")
                spec = self._specs.get(action_id)
                if spec is not None:
                    built = spec.build(menu, pet)
                    action = built.menuAction() if isinstance(built, QMenu) else built
                    alias = str(node.get("alias") or "").strip()
                    if alias and action is not None:
                        action.setText(alias)
                        if isinstance(action, QWidgetAction):
                            widget = action.defaultWidget()
                            setter = getattr(widget, "set_title", None)
                            if callable(setter):
                                setter(alias)
                    if action is not None and "icon" in node:
                        action.setIcon(self.icon(menu, action_id, node.get("icon")))
                    if action is not None and action_id not in enabled:
                        action.setEnabled(False)
                        action.setToolTip(self.disabled_reason(action_id))
                        if isinstance(action, QWidgetAction) and action.defaultWidget() is not None:
                            action.defaultWidget().setEnabled(False)
            rendered += 1
            if section:
                previous_section = section


MENU_ACTIONS = MenuActionRegistry()
