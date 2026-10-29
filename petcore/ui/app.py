from __future__ import annotations

import inspect
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QLockFile, QRect, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPalette
from PySide6.QtWidgets import QApplication

from .. import paths
from ..api.credentials import KeyringCredentials
from ..api.store import ApiStore
from ..account.presenter import BalancePresenter
from ..account import pricing
from ..account.service import AccountService
from ..config import Config
from ..content.collect import collect_characters as _collect
from ..contracts.host import AnimationState
from ..log import setup_logging
from ..net.errors import describe as describe_net_error
from ..platform import hotkey_spec
from ..platform.hotkeys import GlobalHotkeyService
from ..platform.session import SessionWatcher
from ..platform.worker import WorkerHost
from ..update import UpdateResult, UpdateService, describe_state
from .. import __version__
from ..mods.feature import CORE_FEATURE_IDS, FeatureManager
from . import presentation_gate
from .market_jobs import MarketJobRegistry
from .menu import install_core_menu
from .menu_layout import (
    DEFAULT_LAYOUT_ID,
    merge_user_layout,
    migrate_default_size_visibility,
    migrate_legacy,
    migrate_p8_menu,
    migrate_p9_menu,
    migrate_p10_menu,
    migrate_p11_menu,
    resolve,
)
from .chat import ChatController, QuickChatController
from .menu_model import MenuItem, MenuModel, add_submenu
from .pet_manager import PetManager, SHARED_KEYS
from .pet_window import PetWindow
from .ports import QtScheduler
from .settings.credits_job import cancel_all as cancel_community_jobs
from .tray import TrayIcon

logger = logging.getLogger("petcore.ui.app")

#: 全局快捷键：配置键 + 动作（AI 三条都走 ai.chat 的 feature command）。
HOTKEY_CONFIGS = {
    "toggle_pet": "hotkey_toggle_pet",
    "open_chat": "hotkey_open_chat",
    "look_screen": "hotkey_look_screen",
    "screen_capture": "hotkey_screen_capture",
}
HOTKEY_COMMANDS = {
    "open_chat": ("ai.chat", "open"),
    "look_screen": ("ai.chat", "look_screen"),
    "screen_capture": ("ai.chat", "screen_capture"),
}


def _mod_diagnostics(result) -> list[str]:
    diagnostics = getattr(result, "diagnostics", None) or []
    return [str(item) for item in diagnostics]


def _invoke_feature_command(command, slot: int | None) -> object:
    """调用扩展命令；命令声明了 ``slot`` 参数就带上发起者。"""
    if slot is None:
        return command()
    try:
        parameters = inspect.signature(command).parameters
    except (TypeError, ValueError):
        return command()
    if "slot" in parameters:
        return command(slot=int(slot))
    return command()


class _PresentationAdapter:
    def __init__(self, host, target_window=None):
        self._host = host
        self._target_window = target_window

    def _window(self):
        if self._target_window is not None:
            if not hasattr(self._target_window, "isVisible"):
                return None
            pets = getattr(self._host, "pets", None)
            if pets is not None and self._target_window not in pets.each():
                return None
            return self._target_window
        active = getattr(self._host, "_active_window", None) or getattr(
            self._host, "window", None
        )
        pets = getattr(self._host, "pets", None)
        if pets is not None and callable(getattr(pets, "each", None)):
            visible = [window for window in pets.each() if window.isVisible()]
            if active is not None and active.isVisible():
                return active
            if visible:
                return visible[0]
            return active
        return active

    def for_slot(self, slot):
        pets = getattr(self._host, "pets", None)
        windows = pets.each() if pets is not None else [getattr(self._host, "window", None)]
        window = next((w for w in windows if w is not None and w.slot == slot), None)
        # A missing identity stays missing; never route to the active pet.
        return _PresentationAdapter(self._host, window if window is not None else object())

    def for_window(self, window):
        """Return a presentation port bound to one pet window."""
        return _PresentationAdapter(self._host, window)

    def say(self, text: str, *, duration_ms: int = 4000, force: bool = False,
            subtitle: str = "", owner: str = "") -> bool:
        quiet = getattr(self._host, "quiet", None)
        if not force and quiet is not None and quiet.quiet_active():
            return False
        if not force and not presentation_gate.allows(owner):
            # 别人正占着气泡（歌词这类连续展示）：不弹、不报错，调用方按「没显示」处理。
            return False
        window = self._window()
        settings = getattr(self._host, "settings_dialog", None)
        if window is None or not window.isVisible() or (settings and settings.isVisible()):
            return False
        kwargs = {"subtitle": subtitle} if subtitle else {}
        shown = window.say(text, duration_ms, **kwargs) is not False
        island = getattr(self._host, "island", None)
        if shown and island is not None:
            island.add_message(text)
        return shown

    def say_sequence(self, items, *, force: bool = False, on_show=None,
                     owner: str = "") -> bool:
        quiet = getattr(self._host, "quiet", None)
        if not force and quiet is not None and quiet.quiet_active():
            return False
        if not force and not presentation_gate.allows(owner):
            return False
        window = self._window()
        settings = getattr(self._host, "settings_dialog", None)
        if window is None or not window.isVisible() or (settings and settings.isVisible()):
            return False
        lines = []
        for item in items or ():
            if not item:
                continue
            text = str(item[0] or "").strip()
            if not text:
                continue
            subtitle = str(item[1] or "").strip() if len(item) > 1 else ""
            duration = int(item[2]) if len(item) > 2 else 4000
            lines.append((text, subtitle, max(800, duration)))
        if not lines:
            return False
        island = getattr(self._host, "island", None)

        def notify(text: str) -> None:
            if island is not None:
                island.add_message(text)
            if on_show is not None:
                try:
                    on_show(text)
                except Exception:  # noqa: BLE001 - 回调失败不影响后续气泡
                    logger.debug("say_sequence on_show failed", exc_info=True)

        runner = getattr(window, "say_sequence", None)
        if not callable(runner):
            first = lines[0]
            return self.say(
                first[0], duration_ms=first[2], force=force, subtitle=first[1],
                owner=owner,
            )
        shown = runner(lines, on_show=notify) is not False
        return shown

    # -- 展示会话闸门（歌词这类连续展示占着气泡时，别人的自动气泡让路） ------

    def begin_scene(self, owner: str) -> bool:
        return presentation_gate.begin_scene(owner)

    def end_scene(self, owner: str) -> None:
        presentation_gate.end_scene(owner)

    def refresh_scene(self, owner: str) -> bool:
        return presentation_gate.refresh_scene(owner)

    def hide(self, owner: str = "") -> None:
        """收起气泡；给了 ``owner`` 就只在它仍持有展示会话时收（别人的不动）。"""
        name = str(owner or "").strip()
        if name and presentation_gate.owner() != name:
            return
        window = self._window()
        hider = getattr(window, "hide_bubble", None)
        if not callable(hider):
            return
        try:
            hider()
        except Exception:  # noqa: BLE001 - 收气泡失败不该影响调用方状态机
            logger.debug("hide bubble failed", exc_info=True)

    def animation_state(self):
        """当前动画名 + 循环标志 + 接管态；读不到（窗口没有动画状态）返回 None。"""
        window = self._window()
        if window is None or not hasattr(window, "_loop_active"):
            return None
        try:
            current = getattr(getattr(window, "state", None), "current", None)
            motion = getattr(window, "motion", None)
            effects = getattr(window, "effects", None)
            locked = bool(
                getattr(window, "probe_active", False)
                or getattr(effects, "throw_egg_active", False)
                or getattr(window, "dragging", False)
                or getattr(motion, "thrown", False)
            )
            return AnimationState(
                name=str(getattr(current, "name", "") or ""),
                loop=bool(getattr(window, "_loop_active", False)),
                locked=locked,
            )
        except Exception:  # noqa: BLE001 - 极老/替身窗口读不到就当未知
            return None

    def play_animation(self, name: str, *, loop: bool = False) -> bool:
        window = self._window()
        return bool(window is not None and window.play_animation(name, loop=loop))

    def release_loop(self) -> bool:
        window = self._window()
        return bool(window is not None and window.release_loop())

    def show_image(
        self,
        path,
        caption: str = "",
        *,
        duration_ms: int = 4000,
        scale: float = 1.0,
        force: bool = False,
        owner: str = "",
    ) -> bool:
        if not force and not presentation_gate.allows(owner):
            # 与 say() 同一条闸门：别人占着气泡时，图片气泡也要让路。
            return False
        window = self._window()
        return bool(
            window is not None
            and window.show_image(path, caption, duration_ms, image_scale=scale)
        )

    def play_event(self, group: str, variant: str | int | None = None) -> bool:
        window = self._window()
        return bool(window is not None and window.play_event(group, variant))

    def notify(self, title: str, message: str, *, force: bool = False, on_click=None) -> bool:
        # Preserve explicit command feedback; automatic reminders never force.
        text = f"{title}：{message}" if title else message
        if self.say(text, force=force):
            return True
        from PySide6.QtWidgets import QSystemTrayIcon
        quiet = getattr(self._host, "quiet", None)
        window = self._window()
        tray = getattr(self._host, "tray", None)
        if window is None or (quiet and quiet.quiet_active()):
            return False
        if tray is None or not QSystemTrayIcon.supportsMessages() or not tray.isVisible():
            logger.warning("system notification unavailable")
            return False
        previous = getattr(self._host, "_notification_click", None)
        if previous:
            try:
                tray.messageClicked.disconnect(previous)
            except (RuntimeError, TypeError):
                pass
        def clicked():
            if self._window() is not None and callable(on_click):
                on_click()
        self._host._notification_click = clicked
        tray.messageClicked.connect(clicked)
        tray.showMessage(title, message)
        return True

    def visible(self) -> bool:
        """桌宠是否可见：隐藏态下不要切联动动画（同主分支的零功耗语义）。"""
        window = self._window()
        return bool(window is not None and window.isVisible())

    def current_character(self) -> str:
        window = self._window()
        return str(getattr(window, "character_id", "") if window is not None else "")


def gui_dispatch(scheduler):
    """把工作线程的结果送回 GUI 线程的唯一通道。

    ``QtScheduler`` 用信号跨线程排队；不能用无接收者的 ``QTimer.singleShot``
    两参重载——定时器会挂在没有事件循环的工作线程上，回调永不触发。
    """
    return lambda callback: scheduler.call_later(0, callback)


class CoreHost:
    def __init__(self, app: QApplication, config: Config, root: Path, mod_dirs):
        self.app = app
        self.config = config
        self.root = root
        self.mod_dirs = mod_dirs
        self.scheduler = QtScheduler()
        self.api_store = ApiStore(self.root)
        self.api_credentials = KeyringCredentials()
        self.account = AccountService(self.api_store, self.api_credentials)
        self.balance_presenter = BalancePresenter(
            self.account,
            service_id_provider=self._balance_service_id,
            on_result=self._on_balance_result,
            dispatch=gui_dispatch(self.scheduler),
        )
        self.update_service: UpdateService | None = None
        self._update_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="petcore-update")
        self._update_future = None
        self._update_generation = 0
        self._closing = False
        from ..speech import SpeechService

        self.speech = SpeechService(
            self.root, config=self.config, credentials=self.api_credentials
        )
        from ..community import CommunityService

        self.community = CommunityService(self.root, credentials=self.api_credentials)
        self.chat = ChatController(self, self.root)
        self.quick_chat = QuickChatController(self)
        from .screen import ScreenAskController

        self.screen_ask = ScreenAskController(self, self.root)
        self.menu_model = MenuModel()
        self.load_result = None
        self._characters: list = []
        self.window: PetWindow | None = None
        self.pets: PetManager | None = None
        self._active_window: PetWindow | None = None
        self.tray: TrayIcon | None = None
        self.island = None
        self.island_field = None
        self.fullscreen_hide = None
        self.probe_hub = None
        self.settings_dialog = None
        self.hotkeys: GlobalHotkeyService | None = None
        self.diagnostics: list[str] = []
        self.menu_diagnostics: list[str] = []
        self.features: FeatureManager | None = None
        self.quiet = None
        self.worker_host = WorkerHost()
        self.market_jobs = MarketJobRegistry()
        self.ports = None
        from .ports import QuietMode

        self.quiet = QuietMode()
        self.load_content()

    def _balance_service_id(self, slot: int) -> str | None:
        window = self.pets.window_for_slot(int(slot)) if self.pets is not None else None
        config = getattr(window, "config", None) or self.config
        preferred = str(config.get("api_service_id") or "").strip()
        if preferred and self.api_store.get(preferred) is not None:
            return preferred
        for service in self.api_store.services():
            if getattr(service, "balance_protocol", "none") != "none":
                return service.service_id
        return None

    def _on_balance_result(self, slot: int, result, error) -> None:
        window = self.pets.window_for_slot(int(slot)) if self.pets is not None else self.window
        if window is None or getattr(window, "host", self) is not self:
            return
        island = getattr(self, "island", None)
        if result is not None:
            message = result.format_text()
            if island is not None:
                island.show_balance(result=result)
            self._notify(slot, message, subtitle=self._balance_subtitle(window))
            self._click_balance_presented(window)
            return
        reason = str(getattr(error, "reason", "network") or "network")
        detail = str(error) if str(error) and str(error) != reason else ""
        message = f"余额查询失败：{describe_net_error(reason)}"
        if detail and reason != detail:
            message = f"{message}（{detail}）"
        if island is not None:
            island.show_balance(error=message)
        self._notify(slot, message)
        self._click_balance_presented(window)

    @staticmethod
    def _click_balance_presented(window) -> None:
        """余额气泡已经冒出来：告诉窗口可以接续排队的点击台词了。"""
        hook = getattr(window, "notify_click_balance_presented", None)
        if not callable(hook):
            return
        try:
            hook()
        except Exception:  # noqa: BLE001 - 点击台词接续坏了不许影响余额展示
            logger.debug("click balance presented hook failed", exc_info=True)

    def _balance_subtitle(self, window=None) -> str:
        """余额气泡副标题：DeepSeek 峰谷提示（高峰红/空闲绿，文案可自定义）。

        峰谷是服务商计价事实，键在全局配置里；`window.config` 是 ScopedConfig，
        读全局键会直通主配置，所以这里用窗口配置也能读到。
        """
        config = getattr(window, "config", None) or self.config
        try:
            return pricing.subtitle_for(config)
        except Exception:  # noqa: BLE001 - 副标题坏了不许吞掉余额本体
            logger.debug("balance tier subtitle failed", exc_info=True)
            return ""

    def _ensure_update_service(self) -> UpdateService:
        if self.update_service is None:
            self.update_service = UpdateService(
                self.root,
                current_version=__version__,
                frozen=bool(getattr(sys, "frozen", False)),
            )
        return self.update_service

    def request_balance(self, slot: int | None = None, *, force: bool = True) -> None:
        target = self._active_window or self.window
        resolved = int(slot if slot is not None else getattr(target, "slot", 0) or 0)
        cached = self.balance_presenter.cached(resolved)
        if cached is not None and self.island is not None:
            self.island.show_balance(result=cached)
        self.balance_presenter.request(resolved, force=force)

    def check_for_updates(self) -> None:
        if self._closing:
            return
        if self._update_future is not None and not self._update_future.done():
            self._notify(None, "正在检查更新，请稍候")
            return
        service = self._ensure_update_service()
        self._update_generation += 1
        generation = self._update_generation
        self._notify(None, "正在检查更新…")
        service.reset_cancel()
        self._update_future = self._update_executor.submit(service.check, force=True)
        self._update_future.add_done_callback(
            lambda future, token=generation: QTimer.singleShot(
                0, self.app, lambda: self._finish_update_check(token, future)
            )
        )
        self.open_settings("about")

    def _finish_update_check(self, generation: int, future) -> None:
        if self._closing or generation != self._update_generation:
            return
        try:
            result = future.result()
        except Exception as exc:  # noqa: BLE001 - worker boundary
            result = UpdateResult(False, "check_failed", f"更新检查失败：{type(exc).__name__}")
        if not isinstance(result, UpdateResult):
            result = UpdateResult(False, "check_failed", "更新检查返回了无效结果")
        self._publish_update_result(result)

    def _publish_update_result(self, result: UpdateResult) -> None:
        dialog = getattr(self, "settings_dialog", None)
        page = getattr(dialog, "page_for_key", lambda _key: None)("about") if dialog else None
        setter = getattr(page, "set_update_status", None)
        if callable(setter):
            setter(result)
        # 岛的消息队列由 _notify 统一写入，这里再写一次会让每条状态出现两遍。
        self._notify(None, result.message or describe_state(result.state))

    def _install_available(self) -> bool:
        """只有冻结的 Windows 进程才能原地更新，源码运行不给装。"""
        service = self.update_service
        return bool(service is not None and service.can_auto_install())

    def update_status(self) -> UpdateResult:
        return self._ensure_update_service().status()

    def download_update(self) -> None:
        service = self._ensure_update_service()
        asset = service.select_asset()
        if asset is None:
            self._notify(None, "没有可用的更新安装包")
            return
        if self._update_future is not None and not self._update_future.done():
            self._notify(None, "已有更新任务正在进行")
            return
        self._update_generation += 1
        generation = self._update_generation
        manifest = service.manifest
        self._publish_update_result(
            UpdateResult(
                False,
                "downloading",
                "正在下载更新…",
                version=manifest.version if manifest is not None else "",
                manifest=manifest,
            )
        )
        service.reset_cancel()
        self._update_future = self._update_executor.submit(service.download, asset)
        self._update_future.add_done_callback(
            lambda future, token=generation: QTimer.singleShot(
                0, self.app, lambda: self._finish_update_download(token, future)
            )
        )

    def _finish_update_download(self, generation: int, future) -> None:
        if self._closing or generation != self._update_generation:
            return
        try:
            path = future.result()
            result = self._ensure_update_service().status()
            if result.state != "downloaded" or result.path is None:
                result = UpdateResult(
                    True,
                    "downloaded",
                    "更新包下载完成",
                    path=path,
                    manifest=self._ensure_update_service().manifest,
                )
        except Exception as exc:  # noqa: BLE001 - worker boundary
            result = UpdateResult(False, "download_failed", f"更新下载失败：{type(exc).__name__}")
        self._publish_update_result(result)

    def install_update(self, package_path=None) -> None:
        service = self._ensure_update_service()
        if not service.can_auto_install():
            self._publish_update_result(
                UpdateResult(
                    False,
                    "unsupported_runtime",
                    "源码运行或非 Windows 环境不支持自动更新，请用安装包覆盖安装",
                    manifest=service.manifest,
                )
            )
            return
        path = package_path or service.status().path
        if path is None:
            self._notify(None, "请先下载更新包")
            return
        result = service.install(Path(path))
        self._publish_update_result(result)
        if result.ok:
            self.quit_app()

    def cancel_update(self) -> None:
        service = self.update_service
        if service is None:
            return
        service.cancel()
        self._update_generation += 1
        manifest = service.manifest
        self._publish_update_result(
            UpdateResult(
                False,
                "cancelled",
                "已取消更新下载",
                version=manifest.version if manifest is not None else "",
                manifest=manifest,
            )
        )

    def presentation_port(self):
        return _PresentationAdapter(self)

    def attach_window(self, window: PetWindow) -> None:
        self.window = window
        self._active_window = window
        from .ports import build_ports

        ports = build_ports(self, window)
        self.ports = ports
        self.features = FeatureManager(ports, host=self)
        self.diagnostics.extend(self.features.load_builtin_features())
        self.diagnostics.extend(self.features.load_builtin_demo())
        # 是否允许加载已由 loader 判定（全局开关或安装器授信的 `.trusted` 包），
        # 这里只认 ``mod.enabled``，否则随安装包投放的扩展永远激活不了。
        user_mods_roots = tuple(path.resolve() for path in paths.user_mods_dirs(self.root))
        for mod in getattr(self.load_result, "mods", ()) or ():
            if mod.kind != "feature" or not mod.enabled:
                continue
            mod_path = mod.path.resolve()
            if not any(
                mod_path == base or base in mod_path.parents for base in user_mods_roots
            ):
                continue
            if mod.manifest.execution != "host-only":
                self.diagnostics.append(f"{mod.id}: worker feature is not supported yet")
                continue
            if not mod.manifest.entrypoint:
                self.diagnostics.append(f"{mod.id}: missing entrypoint")
                continue
            self.diagnostics.extend(
                self.features.load(
                    pack=mod.path,
                    owner=mod.id,
                    entrypoint=mod.manifest.entrypoint,
                    config={},
                )
            )
        for message in self.diagnostics:
            logger.warning("feature/mod diagnostic: %s", message)
        self._register_feature_menus()
        self.apply_island_settings()

    def _register_feature_menus(self) -> None:
        if self.features is None:
            return
        labels: dict[str, str] = {}
        for mod in getattr(self.load_result, "mods", ()) or ():
            manifest = getattr(mod, "manifest", None)
            name = str(getattr(manifest, "name_zh", "") or getattr(manifest, "name", "") or "")
            labels[str(getattr(mod, "id", "") or "")] = name or str(getattr(mod, "id", ""))
        # 内置接缝包（core.feature-demo 等）不在 loader 结果里，单独从包内 manifest 取名字。
        from ..mods.manifest import load_manifest

        for feature in getattr(self.features, "features", {}).values():
            owner = str(getattr(feature, "owner", "") or "")
            if not owner or owner in labels:
                continue
            try:
                manifest, _problems = load_manifest(feature.path)
            except Exception:
                continue
            if manifest is None:
                continue
            labels[owner] = (
                str(getattr(manifest, "name_zh", "") or getattr(manifest, "name", "") or owner)
            )
        self.menu_model.set_owner_labels(labels)
        self.menu_model.set_extension_skip_owners(CORE_FEATURE_IDS)
        for feature, contribution in self.features.menu_contributions():
            label = str(contribution.data.get("label") or contribution.id)
            command_id = str(contribution.data.get("command") or contribution.id)
            order = int(contribution.data.get("order") or 500)
            icon_name = str(contribution.data.get("icon") or "")
            checked = contribution.data.get("checked")
            self.menu_model.register(
                MenuItem(
                    id=f"{feature.owner}:{contribution.id}",
                    label=label,
                    owner=feature.owner,
                    order=order,
                    icon=icon_name,
                    checked=checked if callable(checked) else None,
                    callback=(
                        lambda owner=feature.owner, command=command_id: self._run_feature_command(
                            owner, command, self._menu_slot()
                        )
                    ),
                )
            )

    def _menu_slot(self) -> int | None:
        """当前右键菜单属于哪只桌宠（子宠菜单的命令要落在它自己身上）。"""
        window = self._active_window or self.window
        return getattr(window, "slot", None)

    def active_topmost(self) -> bool:
        """当前桌宠是否置顶：扩展弹出的菜单要跟它同层（见 ``ui.menu``）。"""
        window = self._active_window or self.window
        return bool(getattr(window, "always_on_top", False))

    def show_context_menu(self, menu, pos) -> None:
        """扩展也能弹「和桌宠同层」的菜单（扩展不许 import ``petcore.ui``）。"""
        from .menu import show_context_menu as _show_context_menu

        _show_context_menu(menu, pos, topmost=self.active_topmost())

    def _run_feature_command(
        self, owner: str, command_id: str, slot: int | None = None
    ) -> None:
        if self.features is None:
            return
        command = self.features.command(owner, command_id)
        if command is None:
            logger.warning("feature command missing: %s/%s", owner, command_id)
            return
        try:
            _invoke_feature_command(command, slot)
        except Exception:
            logger.exception("feature command failed: %s/%s", owner, command_id)

    def run_feature_command(
        self, owner: str, command_id: str, *, slot: int | None = None
    ) -> bool:
        """菜单/热键的统一入口：扩展缺失时明确提示，不静默失败。

        ``slot`` 是发起命令的桌宠；扩展命令若声明了 ``slot`` 参数就带上它，
        子宠菜单里的 AI 对话/截图提问才会落在子宠自己的窗口与会话上。
        """
        command = None
        if self.features is not None:
            command = self.features.command(owner, command_id)
        if command is None:
            self._notify_missing_feature(owner)
            return False
        try:
            _invoke_feature_command(command, slot)
        except Exception:
            logger.exception("feature command failed: %s/%s", owner, command_id)
            self._notify(slot, "扩展执行失败，详情见日志")
            return False
        return True

    def open_quick_chat(self, slot: int = 0) -> bool:
        """点桌宠气泡：打开该宠的快速对话；没有 handler 时明确提示。"""
        owner = "ai.chat"
        controller = getattr(self, "quick_chat", None)
        if controller is None:
            return False
        if not controller.has_handler(owner):
            self._notify_missing_feature(owner)
            return False
        return bool(controller.open(owner, pet_slot=int(slot)))

    def _notify_missing_feature(self, owner: str) -> None:
        label = "AI 扩展" if owner == "ai.chat" else f"扩展 {owner}"
        self._notify(None, f"{label}未安装：可在「扩展管理 → 插件市场」安装")

    def _notify(self, slot: int | None, message: str, *, subtitle: str = "") -> None:
        window = self.window
        if slot is not None and self.pets is not None:
            window = self.pets.window_for_slot(int(slot)) or window
        window = window or self._active_window
        say = getattr(window, "say", None)
        if callable(say):
            try:
                say(message, 6000, subtitle=subtitle)
            except TypeError:
                # 老签名（无 subtitle）：副标题丢失也不该让提示消失。
                try:
                    say(message, 6000)
                except Exception:  # noqa: BLE001
                    logger.debug("notify failed", exc_info=True)
            except Exception:  # noqa: BLE001
                logger.debug("notify failed", exc_info=True)
        island = getattr(self, "island", None)
        if island is not None:
            island.add_message(message)

    def settings_pages(self):
        return self.features.settings_pages() if self.features is not None else []

    def load_content(self) -> None:
        self._characters, result = _collect(
            root=self.root,
            mod_dirs=self.mod_dirs,
            frameseq_mode=str(self.config.get("frameseq") or "auto"),
            allow_feature_mods=bool(self.config.get("allow_feature_mods")),
        )
        self.load_result = result
        self.diagnostics = _mod_diagnostics(result)

    def _display_spec(self, spec):
        """角色显示名统一走这里：有别名用别名，没有就是目录显示名。

        ``CharacterSpec`` 是 frozen 的，别名只能换一个新的对象出去；目录层那份
        ``self._characters`` 保持原样，别名的撤销/切换才不需要重新扫盘。
        """
        alias = self.config.character_alias(spec.id)
        if not alias or alias == spec.name:
            return spec
        try:
            return replace(spec, name=alias)
        except TypeError:  # 非 dataclass 的替身（测试注入）就原样返回
            return spec

    def characters(self) -> list:
        return [self._display_spec(spec) for spec in self._characters]

    def character_for(self, character_id: str):
        for character in self._characters:
            if character.id == character_id:
                return self._display_spec(character)
        return self._display_spec(self._characters[0])

    # ------------------------------------------------------------ 角色别名
    def character_alias(self, character_id: str) -> str:
        return self.config.character_alias(character_id)

    def set_character_alias(
        self, character_id: str, name: str, *, persist: bool = True
    ) -> None:
        """写角色别名（菜单命令这类显式用户动作立即落盘）。"""
        self.config.set_character_alias(character_id, name)
        if not persist:
            return
        try:
            self.config.save()
        except OSError:
            # 落盘失败不该让内存里的名字失效：气泡已经报了新名字。
            logger.warning("failed to persist character alias", exc_info=True)

    def character_default_name(self, character_id: str) -> str:
        """目录里的原始显示名（别名撤掉后回落到它）。"""
        for spec in self._characters:
            if spec.id == character_id:
                return str(spec.name or spec.id)
        return str(character_id or "")

    def character_display_name(self, character_id: str) -> str:
        return self.character_alias(character_id) or self.character_default_name(
            character_id
        )

    def refresh_character_names(self) -> None:
        """别名变化后刷新正在显示的消费点（菜单每次弹出重建，不用管这里）。"""
        dialog = getattr(self, "settings_dialog", None)
        if dialog is None or getattr(dialog, "_disposed", False):
            return
        page_for = getattr(dialog, "page_for_key", None)
        page = page_for("general") if callable(page_for) else None
        refresh = getattr(page, "refresh_characters", None)
        if not callable(refresh):
            return
        try:
            refresh()
        except RuntimeError:
            logger.debug("settings page is already gone", exc_info=True)

    def build_menu(self, parent=None):
        if isinstance(parent, PetWindow):
            self._active_window = parent
        self.reload_menu(parent if isinstance(parent, PetWindow) else None)
        return self.menu_model.build(parent)

    def reload_menu(self, window: PetWindow | None = None) -> None:
        target = window or self._active_window or self.window
        if target is None:
            return
        install_core_menu(self.menu_model, target)
        self.menu_model.set_font_size(int(self.config.get("ui_font_size") or 13))
        raw = self.config.get("menu_layout")
        if isinstance(raw, dict):
            changed = migrate_p8_menu(raw)
            changed = migrate_p9_menu(raw) or changed
            changed = migrate_p10_menu(raw) or changed
            changed = migrate_p11_menu(raw) or changed
            if changed:
                self.config.set("menu_layout", raw)
                self.config.save()
        if not isinstance(raw, dict):
            hidden = self.config.get("menu_hidden") or []
            order = self.config.get("menu_order") or []
            if hidden or order:
                raw = migrate_legacy(hidden, order)
                self.config.set("menu_layout", raw)
                self.config.save()
            else:
                raw = None
        elif migrate_default_size_visibility(raw):
            self.config.set("menu_layout", raw)
            self.config.save()
        resolved = resolve(
            raw,
            registered=self.menu_model.registered_ids(),
            available=self.menu_model.available_ids(),
        )
        if resolved.source == "user" and any(
            item.startswith(("default-action-added:", "mod-action-added:"))
            for item in resolved.diagnostics
        ):
            self._persist_merged_menu_layout(raw)
        self.menu_model.apply_layout(resolved.nodes)
        self.menu_diagnostics = list(resolved.diagnostics)

    def _persist_merged_menu_layout(self, raw: dict) -> bool:
        """Write merged defaults back so the fix survives the next reload.

        Only ``nodes`` changes: ``layout_id``/``schema_version`` and every node
        the user ordered or hid stay as they are.
        """
        merged, notes = merge_user_layout(raw, registered=self.menu_model.registered_ids())
        if not any(
            note.startswith(("default-action-added:", "mod-action-added:")) for note in notes
        ):
            return False
        merged["layout_id"] = str(raw.get("layout_id") or DEFAULT_LAYOUT_ID)
        merged["schema_version"] = raw.get("schema_version", 1)
        try:
            self.config.set("menu_layout", merged)
            self.config.save()
        except OSError:
            logger.warning("failed to persist the merged menu layout", exc_info=True)
            return False
        return True

    def apply_live_settings(self) -> None:
        pets = getattr(self, "pets", None)
        targets = pets.each() if pets is not None else ([self.window] if getattr(self, "window", None) else [])
        for window in targets:
            self._apply_live_to(window)
        self._apply_live_fonts()
        self.apply_island_settings()
        chat = getattr(self, "chat", None)
        apply_appearance = getattr(chat, "apply_appearance", None)
        if callable(apply_appearance):
            try:
                apply_appearance()
            except Exception:
                logger.exception("failed to refresh chat appearance")

    def _apply_live_fonts(self) -> None:
        """界面字号实时生效：菜单下次构建生效，打开中的设置页立即重排。"""
        size = int(self.config.get("ui_font_size") or 13)
        model = getattr(self, "menu_model", None)
        if model is not None:
            model.set_font_size(size)
        dialog = getattr(self, "settings_dialog", None)
        if dialog is None or getattr(dialog, "_disposed", False):
            return
        refresh = getattr(dialog, "refresh_style", None)
        if callable(refresh):
            try:
                refresh()
            except RuntimeError:
                logger.debug("settings dialog is already gone", exc_info=True)
            return
        from .settings.style import is_dark, stylesheet

        try:
            dialog.setStyleSheet(stylesheet(is_dark(), size))
        except RuntimeError:
            logger.debug("settings dialog is already gone", exc_info=True)

    # ------------------------------------------------------------ 全屏自动隐藏

    def start_fullscreen_hide(self) -> None:
        """创建进程内唯一的全屏监视器并按配置门启停（多宠共用一个）。

        只在这里 new，重复调用只做一次「按当前配置重放」——``attach_window`` 在
        重载扩展时会被再次调用，重复建影子实例等于每只宠一个探测表。
        """
        if self.fullscreen_hide is None:
            from .fullscreen_hide import FullscreenHideController

            self.fullscreen_hide = FullscreenHideController(
                self._windows, parent=self.app
            )
        self.apply_fullscreen_hide()

    def apply_fullscreen_hide(self, enabled: bool | None = None) -> None:
        """把「开关 + 当前配置」作用到监视器（设置页实时预览与取消回滚共用）。"""
        controller = getattr(self, "fullscreen_hide", None)
        if controller is None:
            return
        if enabled is None:
            enabled = bool(self.config.get("auto_hide_fullscreen", True))
        controller.set_enabled(bool(enabled))

    def set_auto_hide_fullscreen(self, enabled: bool, *, persist: bool = True) -> None:
        """全屏自动隐藏开关（菜单/设置/命令共用入口）。"""
        enabled = bool(enabled)
        self.config.set("auto_hide_fullscreen", enabled)
        if persist:
            try:
                self.config.save()
            except Exception:  # noqa: BLE001 - 落盘失败不该让开关失效
                logger.debug("auto hide config save failed", exc_info=True)
        self.apply_fullscreen_hide(enabled)

    def stop_fullscreen_hide(self) -> None:
        controller = getattr(self, "fullscreen_hide", None)
        if controller is not None:
            controller.stop()

    # ------------------------------------------------------------ 灵动岛

    def _island_character_name(self) -> str:
        character_id = str(
            getattr(self.window, "character_id", "") or self.config.get("character") or ""
        )
        spec = self.character_for(character_id) if character_id else None
        return str(getattr(spec, "name", "") or character_id)

    def ensure_island(self):
        if self.island is not None:
            return self.island
        from .island import DynamicIsland

        self.island = DynamicIsland(
            self.config, character_name=self._island_character_name()
        )
        self.island.balance_requested.connect(self.request_balance)
        self.island.settings_requested.connect(lambda: self.open_settings("api"))
        self.island.set_avoidance_callback(self._avoid_island_overlap)
        self.island.set_pet_visible(self._any_pet_visible())
        self.island_field = self._build_island_field(self.island)
        self.probe_hub = self._build_probe_hub(self.island)
        if self.probe_hub is not None:
            self.island.set_probe_sync_callback(self.probe_hub.sync)
        return self.island

    def _island_pets(self):
        """碰撞侧要的「所有桌宠」：宠物表可能比岛晚建（懒加载），用回调现取。"""
        manager = getattr(self, "pets", None)
        if manager is not None:
            return manager.each()
        window = getattr(self, "window", None)
        return [window] if window is not None else []

    def _build_island_field(self, island):
        """岛 × 桌宠碰撞：唯一 Qt 协调器，事件驱动、不建定时器。"""
        from .island_collision import IslandField

        try:
            field = IslandField(island, self.config, self._island_pets, parent=self.app)
        except Exception:  # noqa: BLE001 - 碰撞接线失败只影响撞岛手感
            logger.debug("island collision unavailable", exc_info=True)
            return None
        island.set_geometry_changed_callback(field.on_geometry_changed)
        return field

    def _build_probe_hub(self, island):
        """GPU/FPS 探针只在这里创建：采样器零 Qt，宿主负责启停与推送。"""
        from .probes import IslandProbeHub

        def gpu_factory():
            from ..platform.gpu_monitor import GpuMonitor

            return GpuMonitor()

        def fps_factory():
            from ..platform.fps import FpsProbe

            return FpsProbe()

        try:
            return IslandProbeHub(
                island, gpu_factory=gpu_factory, fps_factory=fps_factory, parent=self.app
            )
        except Exception:  # noqa: BLE001 - 探针接线失败只影响 gpu/fps 两段
            logger.debug("island probe hub unavailable", exc_info=True)
            return None

    def _avoid_island_overlap(self, island) -> None:
        """避开当前可见桌宠**身体框**；避让失败时保持桌宠位置不变。

        不能用 ``frameGeometry()``：桌宠窗口四周是大片透明画布，按整窗判重叠
        会让胶囊被远处走位的桌宠反复推开。
        """
        field = getattr(self, "island_field", None)
        if field is not None and field.enabled():
            # 碰撞开启时宠物进不了岛区，避让只会在用户拖着宠物压过来时把胶囊弹到
            # 屏幕角落（主分支没有这个行为）。关掉碰撞则保留旧的「岛让位」语义。
            return
        manager = self.pets
        windows = manager.each() if manager is not None else [self.window]
        obstacles = [
            QRect(*window.body_rect())
            for window in windows
            if window is not None and window.isVisible()
        ]
        island.avoid_overlaps(obstacles)

    def apply_island_settings(self) -> None:
        """按配置显示/刷新/隐藏灵动岛（设置页实时预览与菜单开关共用）。"""
        enabled = bool(self.config.get("island_enabled"))
        island = getattr(self, "island", None)
        if not enabled:
            if island is not None:
                island.stop()
                island.hide()
            field = getattr(self, "island_field", None)
            if field is not None:
                field.reset()
            return
        island = self.ensure_island()
        logger.debug(
            "island apply: metrics=%s style=%s character=%s",
            self.config.get("island_metrics"),
            self.config.get("island_style"),
            self._island_character_name(),
        )
        try:
            island.settings = island.settings.__class__.from_config(
                self.config, character_name=self._island_character_name()
            )
            island.refresh_from_config()
            island.set_visible(True)
            # 岛可能在后来的某一轮才被启用：启用时按当前可见性补一次，否则状态点
            # 会停在「隐藏」直到下一次显隐变化。
            self._sync_island_pet_visible()
        except Exception:  # noqa: BLE001 - 灵动岛坏了不许拖垮桌宠
            logger.exception("failed to apply island settings")

    def toggle_dynamic_island(self) -> None:
        value = not bool(self.config.get("island_enabled"))
        self.config.set("island_enabled", value)
        try:
            self.config.save()
        except Exception:  # noqa: BLE001
            logger.debug("island config save failed", exc_info=True)
        self.apply_island_settings()
        self._notify(
            None,
            "灵动岛已开启（点胶囊展开，拖动能换位置）" if value else "灵动岛已关闭",
        )

    def _apply_live_to(self, window) -> None:
        window_config = getattr(window, "config", None)
        # Global values are read from the process config; pet-local values are
        # read from the window's own profile.  The old implementation copied
        # every key from the primary profile, which made a child settings
        # window silently edit the wrong pet.
        global_config = self.config
        pet_config = window_config or self.config
        if window_config is not None:
            for key in SHARED_KEYS:
                if key in global_config.data:
                    window_config.set(key, global_config.get(key))
        character = str(pet_config.get("character") or "")
        if character and character != getattr(window, "character_id", ""):
            window.set_character(character, persist=False)
        scale = float(pet_config.get("scale") or 0.72)
        state = getattr(window, "state", None)
        current_scale = float(getattr(state, "scale", scale))
        if abs(scale - current_scale) > 1e-9:
            window.set_scale(scale, persist=False)
        on_top = bool(pet_config.get("always_on_top"))
        if on_top != bool(getattr(window, "always_on_top", on_top)):
            window.set_always_on_top(on_top, persist=False)
        click_through = bool(global_config.get("click_through"))
        if click_through != bool(getattr(window, "click_through", click_through)):
            window.set_click_through(click_through, persist=False)
        edge_probe = bool(pet_config.get("edge_probe_enabled"))
        if hasattr(window, "set_edge_probe_enabled"):
            window.set_edge_probe_enabled(edge_probe, persist=False)
        stream = bool(global_config.get("stream_capture_mode"))
        if stream != bool(getattr(window, "stream_capture_mode", stream)):
            window.set_stream_capture_mode(stream, persist=False)

    def install_hotkeys(self) -> None:
        if self.hotkeys is None:
            self.hotkeys = GlobalHotkeyService(self.app)
            self.hotkeys.install()
        for kind, key in HOTKEY_CONFIGS.items():
            self.set_hotkey(str(self.config.get(key) or ""), persist=False, kind=kind)

    def set_hotkey(
        self, text: str, *, persist: bool = True, kind: str = "toggle_pet"
    ) -> tuple[bool, str]:
        key = HOTKEY_CONFIGS.get(str(kind))
        if key is None:
            return False, "未知的快捷键类型"
        normalized = hotkey_spec.normalize(text)
        if persist:
            self.config.set(key, normalized)
            self.config.save()
        if self.hotkeys is None:
            return True, ""
        ok, message = self.hotkeys.apply(
            normalized, self._hotkey_callback(kind), name=str(kind)
        )
        if not ok:
            logger.warning("无法注册全局快捷键 %s(%s)：%s", normalized, kind, message)
        return ok, message

    def _hotkey_callback(self, kind: str):
        if kind == "toggle_pet":
            return self._toggle_pet_visible
        command = HOTKEY_COMMANDS.get(str(kind))
        if command is None:
            return lambda: None
        owner, command_id = command
        return lambda: self.run_feature_command(owner, command_id)

    def _toggle_pet_visible(self) -> None:
        if self.pets is not None:
            visible = not self.pets.primary.isVisible() if self.pets.primary is not None else True
            self.pets.set_visible_all(visible)
            self._sync_island_pet_visible()
            return
        if self.window is not None:
            self.window.set_visible(not self.window.isVisible())
            self._sync_island_pet_visible()

    def open_settings(self, initial_page: str | None = None, *, window=None) -> None:
        from .settings.dialog import SettingsDialog

        dialog = self.settings_dialog
        target = window or self._active_window or self.window
        if dialog is not None and getattr(dialog, "target_window", None) is not target:
            # One non-modal dialog remains the single source of truth.  Commit
            # the old target before rebuilding it for the pet that was clicked.
            dialog.close()
            if self.settings_dialog is dialog:
                self.settings_dialog = None
            dialog.deleteLater()
            dialog = None
        if dialog is None:
            dialog = SettingsDialog(self, initial_page=initial_page, target_window=target)
            dialog.finished.connect(lambda _result, d=dialog: self._settings_closed(d))
            self.settings_dialog = dialog
        elif initial_page:
            dialog.select_page(initial_page)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _settings_closed(self, dialog) -> None:
        if self.settings_dialog is dialog:
            self.settings_dialog = None
        dialog.deleteLater()

    def uninstall_mod(self, mod_id: str) -> tuple[bool, str]:
        """卸载一个扩展：先撤运行时与菜单贡献，再删受管文件，最后重扫。

        个人数据（``feature-data/<id>``、配置键、钥匙串）一律保留；安装器投放的
        ``.trusted`` 包同样可以卸载（只删 ``{app}\\mods\\<id>``）。
        """
        from ..mods import install as mod_install

        target = next(
            (
                mod
                for mod in (getattr(self.load_result, "mods", ()) or ())
                if str(getattr(mod, "id", "")) == str(mod_id)
            ),
            None,
        )
        if target is None:
            return False, f"扩展未安装：{mod_id}"
        path = Path(getattr(target, "path", "") or "")
        if self.features is not None:
            self.features.stop_owner(str(mod_id))
        self.menu_model.unregister_owner(str(mod_id))
        self._drop_modules_under(path)
        ok, message = mod_install.remove_at(path, root=self.root)
        if ok:
            self.reload_mods()
        return ok, message

    @staticmethod
    def _drop_modules_under(path: Path) -> None:
        """丢掉 ``__file__`` 落在该扩展目录下的模块，避免重装后复用旧模块对象。"""
        try:
            root = Path(path).resolve()
        except OSError:
            return
        for name, module in list(sys.modules.items()):
            location = getattr(module, "__file__", None)
            if not location:
                continue
            try:
                file_path = Path(location).resolve()
            except OSError:
                continue
            if file_path == root or root in file_path.parents:
                sys.modules.pop(name, None)

    def reload_mods(self) -> None:
        if self.features is not None:
            # 换 adapter 前先摘掉旧监听：否则一次点击会同时打到新旧两个运行时。
            ports = getattr(self.features, "ports", None)
            detach_all = getattr(getattr(ports, "interaction", None), "detach_all", None)
            if callable(detach_all):
                try:
                    detach_all()
                except Exception:  # noqa: BLE001 - 摘监听失败不该阻止重载
                    logger.warning("interaction detach failed", exc_info=True)
            for owner in list(self.features.features):
                self.menu_model.unregister_owner(owner)
            self.features.stop()
            self.features = None
        # 丢掉扩展目录下的模块：不丢的话 `from .runtime import X` 仍命中旧对象，
        # 「已重新加载」实际跑的还是旧代码（审计 AUD-14）。
        for base in (*paths.user_mods_dirs(self.root), paths.builtin_features_dir()):
            try:
                children = sorted(Path(base).iterdir())
            except OSError:
                continue
            for child in children:
                if child.is_dir():
                    self._drop_modules_under(child)
        self.load_content()
        targets = self.pets.each() if self.pets is not None else ([self.window] if self.window else [])
        for window in targets:
            target = window.config.get("character") or self.config.get("character")
            window.reload_characters(self.character_for(target))
            window.config.set("character", target)
        if self.window is not None:
            self.attach_window(self.window)
            self.reload_menu()
        if self.tray is not None:
            self.tray.refresh()

    def open_data_dir(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.root)))

    def set_all_visible(self, visible: bool) -> None:
        pets = getattr(self, "pets", None)
        if pets is not None:
            pets.set_visible_all(bool(visible))
        elif getattr(self, "window", None) is not None:
            self.window.set_visible(bool(visible))
        # 状态点五态里的「隐藏」由宠物可见性派生（托盘隐藏 → 空心中性色）。
        self._sync_island_pet_visible()
        if getattr(self, "tray", None) is not None:
            QTimer.singleShot(0, self.tray.refresh)

    def _sync_island_pet_visible(self) -> None:
        """把「还有没有桌宠露着」推给灵动岛（隐藏态的派生来源）。

        可见性有四条改法：托盘「显示/隐藏桌宠」、全局快捷键、托盘逐宠开关、桌宠右键
        隐藏。全部收口到本方法，否则状态点的「隐藏」态只在其中一条路上成立。
        """
        island = getattr(self, "island", None)
        setter = getattr(island, "set_pet_visible", None)
        if not callable(setter):
            return
        try:
            setter(self._any_pet_visible())
        except Exception:  # noqa: BLE001 - 状态点坏了不许挡住显隐
            logger.debug("island pet visibility failed", exc_info=True)

    def _any_pet_visible(self) -> bool:
        pets = getattr(self, "pets", None)
        windows = pets.each() if pets is not None else [getattr(self, "window", None)]
        return any(window is not None and window.isVisible() for window in windows)

    def notify_agent_state(self, state: str, *, source: str = "core") -> None:
        """把「AI 工作中/完成/出错」推给灵动岛状态点（未启用灵动岛时静默）。

        ``source`` 是来源名（chat / screen / ...）：岛按来源归并（错误 > 待确认 > 工作），
        所以截图收尾发 idle 不会把仍在流式回答的对话一起关掉。
        """
        island = getattr(self, "island", None)
        setter = getattr(island, "set_agent_state", None)
        if not callable(setter):
            return
        try:
            setter(state, source=source)
        except TypeError:
            try:
                setter(state)
            except Exception:  # noqa: BLE001
                logger.debug("island agent state failed", exc_info=True)
        except Exception:  # noqa: BLE001 - 状态点坏了不许影响主流程
            logger.debug("island agent state failed", exc_info=True)

    def toggle_all_visible(self) -> None:
        pets = getattr(self, "pets", None)
        windows = pets.each() if pets is not None else ([self.window] if self.window else [])
        visible = any(window.isVisible() for window in windows if window is not None)
        self.set_all_visible(not visible)

    def build_tray_menu(self, _parent=None):
        """托盘右键菜单：隐藏后只靠鼠标也能把桌宠叫回来。"""
        from PySide6.QtWidgets import QMenu

        from .menu_style import install_check_layer, menu_stylesheet

        menu = QMenu(_parent)
        menu.setStyleSheet(menu_stylesheet())
        self.populate_tray_menu(menu)
        # 每次弹出前原地重建动作（不替换 QMenu 对象，避免 Qt 正要显示时菜单被销毁）。
        menu._petcore_refresh = lambda: self.populate_tray_menu(menu)
        menu.aboutToShow.connect(menu._petcore_refresh)
        # 勾选标记由行尾层画（QSS 的 indicator 只剩 1px）：必须装在重建钩子**之后**，
        # 这样每次重建出来的新 QAction 才会在下一次 aboutToShow 里补接 `toggled`。
        # 托盘是「开启鼠标穿透后唯一能关回来的入口」，勾选丢了等于状态不可见。
        install_check_layer(menu)
        return menu

    def populate_tray_menu(self, menu) -> None:
        # clear() removes actions, not the QMenus explicitly parented to this
        # persistent tray menu. Retire only its previous submenu generation.
        retired = [action.menu() for action in menu.actions() if action.menu() is not None]
        menu.clear()
        for submenu in retired:
            if submenu.parent() is menu:
                submenu.deleteLater()
        pets = getattr(self, "pets", None)
        windows = pets.each() if pets is not None else ([self.window] if self.window else [])
        visible = any(window.isVisible() for window in windows if window is not None)
        toggle = menu.addAction("隐藏桌宠" if visible else "显示桌宠")
        toggle.triggered.connect(lambda _checked=False: self.toggle_all_visible())
        click_through = menu.addAction("鼠标穿透")
        click_through.setCheckable(True)
        click_through.setChecked(self.all_click_through())
        click_through.setToolTip("鼠标点击穿过桌宠；关闭也要从这里操作")
        click_through.triggered.connect(
            lambda checked=False: self.set_all_click_through(bool(checked))
        )
        if len(windows) > 1:
            all_visible = menu.addAction("显示全部")
            all_visible.triggered.connect(lambda _checked=False: self.set_all_visible(True))
            all_hidden = menu.addAction("隐藏全部")
            all_hidden.triggered.connect(lambda _checked=False: self.set_all_visible(False))

        menu.addSeparator()
        balance = menu.addAction("显示余额")
        balance.triggered.connect(lambda _checked=False: self.request_balance(force=True))
        update = menu.addAction("检查更新")
        update.triggered.connect(lambda _checked=False: self.check_for_updates())
        settings = menu.addAction("桌宠设置")
        settings.triggered.connect(lambda _checked=False: self.open_settings())
        data = menu.addAction("打开数据目录")
        data.triggered.connect(lambda _checked=False: self.open_data_dir())
        reload_action = menu.addAction("重新加载扩展")
        reload_action.triggered.connect(lambda _checked=False: self.reload_mods())

        if pets is not None and len(windows) > 1:
            menu.addSeparator()
            for slot, window in sorted(pets.windows.items()):
                label = "主桌宠" if slot == 0 else f"桌宠 {slot}"
                submenu = add_submenu(menu, label)
                state = submenu.addAction(
                    "隐藏" if window.isVisible() else "显示"
                )
                state.triggered.connect(
                    lambda _checked=False, w=window: self._toggle_one(w)
                )
                corner = submenu.addAction("回到右下角")
                corner.triggered.connect(lambda _checked=False, w=window: w.return_corner())
                if slot != 0:
                    quit_one = submenu.addAction("退出这只")
                    quit_one.triggered.connect(
                        lambda _checked=False, w=window: w.quit_from_menu()
                    )

        menu.addSeparator()
        quit_action = menu.addAction("退出")
        quit_action.triggered.connect(lambda _checked=False: self.quit_app())

    def _toggle_one(self, window) -> None:
        window.set_visible(not window.isVisible())
        self._sync_island_pet_visible()
        tray = getattr(self, "tray", None)
        if tray is not None:
            QTimer.singleShot(0, tray.refresh)

    def _windows(self):
        pets = getattr(self, "pets", None)
        if pets is not None:
            try:
                return [window for window in pets.each() if window is not None]
            except Exception:
                logger.debug("listing pets failed", exc_info=True)
        return [self.window] if self.window is not None else []

    def all_click_through(self) -> bool:
        """全部桌宠都开了鼠标穿透才算勾选（部分开启按未勾选处理）。"""
        windows = self._windows()
        if not windows:
            return False
        return all(bool(getattr(window, "click_through", False)) for window in windows)

    def set_all_click_through(self, value: bool) -> None:
        """托盘菜单唯一的「鼠标穿透」开关：一次作用到所有桌宠。"""
        windows = self._windows()
        for window in windows:
            setter = getattr(window, "set_click_through", None)
            if not callable(setter):
                continue
            try:
                setter(bool(value))
            except Exception:
                logger.exception("failed to set click-through on %r", window)
        target = getattr(self, "_active_window", None) or self.window
        say = getattr(target, "say", None)
        if callable(say):
            message = (
                "已开启鼠标穿透（仅左键）：左键点下层/长按拖动，右键单击互动、右键长按菜单。"
                "可从托盘菜单关闭"
                if value
                else "已关闭鼠标穿透（恢复左键点击）"
            )
            try:
                say(message, 5000)
            except Exception:
                logger.debug("click-through notice failed", exc_info=True)
        # 不在托盘动作里 refresh：菜单弹出期间 clear() 会删掉正在发信号的 QAction。
        # 勾选状态由下一次 aboutToShow 的原地重建刷新。

    def close_dialogs(self) -> None:
        for attribute in ("settings_dialog",):
            dialog = getattr(self, attribute, None)
            if dialog is None:
                continue
            try:
                dialog.close()
            except Exception:
                logger.debug("failed to close dialog", exc_info=True)
            if getattr(self, attribute, None) is dialog:
                setattr(self, attribute, None)

    def pause_for_session(self) -> None:
        """锁屏/睡眠：停播放与碰撞（窗口不隐藏，解锁后原地续播）。"""
        manager = getattr(self, "pets", None)
        if manager is not None:
            try:
                manager.pause_for_session()
            except Exception:  # noqa: BLE001 - 挂起路径不许被单只宠拖垮
                logger.debug("pause for session failed", exc_info=True)

    def resume_after_session(self) -> None:
        manager = getattr(self, "pets", None)
        if manager is not None:
            try:
                manager.resume_after_session()
            except Exception:  # noqa: BLE001
                logger.debug("resume after session failed", exc_info=True)

    def quit_app(self) -> None:
        self._closing = True
        self.cancel_update()
        self.close_dialogs()
        self.stop_fullscreen_hide()
        try:
            cancel_community_jobs(0.5)
        except Exception:  # noqa: BLE001 - 退出路径不能被刷新线程拖住
            logger.debug("community refresh cancel failed", exc_info=True)
        hub = getattr(self, "probe_hub", None)
        if hub is not None:
            try:
                hub.shutdown()
            except Exception:  # noqa: BLE001
                logger.debug("probe hub shutdown failed", exc_info=True)
        if self.island is not None:
            try:
                self.island.stop()
                self.island.hide()
            except Exception:  # noqa: BLE001
                logger.debug("island shutdown failed", exc_info=True)
        if self.island_field is not None:
            self.island_field.reset()
        self.balance_presenter.shutdown()
        self._update_executor.shutdown(wait=False, cancel_futures=True)
        self.worker_host.shutdown_all()
        report = self.market_jobs.shutdown_all(2000)
        if report.timed_out:
            logger.warning("market jobs still running during quit: %s", report)
        if self.features is not None:
            self.features.stop()
            self.features = None
        ports = self.ports
        if ports is not None:
            for service in (ports.reminders, ports.speech, ports.audio):
                close = getattr(service, "close", None)
                if callable(close):
                    close()
        self.app.quit()


def _install_tray(host: CoreHost) -> TrayIcon:
    icon = paths.legacy_asset_root() / "icon.ico"
    tray = TrayIcon(
        host.build_tray_menu,
        icon,
        host.toggle_all_visible,
        None,
    )
    tray.show()
    return tray


def _wire_screens(app: QApplication, manager) -> None:
    def refresh(*_args) -> None:
        QTimer.singleShot(0, manager.handle_screens_changed)

    for screen in app.screens():
        screen.geometryChanged.connect(refresh)
        screen.logicalDotsPerInchChanged.connect(refresh)
    app.screenAdded.connect(lambda screen: (screen.geometryChanged.connect(refresh), refresh()))
    app.screenRemoved.connect(refresh)
    app.primaryScreenChanged.connect(refresh)


def run(config: Config, *, root: Path, mod_dirs, instance: str | None = None) -> int:
    setup_logging(paths.logs_dir(root))
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(f"dsh-pet-core-{instance}" if instance else "dsh-pet-core")
    app.setQuitOnLastWindowClosed(False)
    palette = app.palette()
    accent = QColor("#0a84ff")
    palette.setColor(QPalette.ColorRole.Highlight, accent)
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    accent_role = getattr(QPalette.ColorRole, "Accent", None)
    if accent_role is not None:
        palette.setColor(accent_role, accent)
    app.setPalette(palette)
    icon_path = paths.legacy_asset_root() / "icon.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    lock = QLockFile(str(root / "petcore.lock"))
    lock.setStaleLockTime(0)
    root.mkdir(parents=True, exist_ok=True)
    if not lock.tryLock(100):
        logger.warning("another petcore instance is already running")
        return 0

    host = CoreHost(app, config, root, mod_dirs)
    manager = PetManager(host, root, config)
    host.pets = manager
    character = host.character_for(str(config.get("character") or ""))
    window = manager.create_primary(character)
    host.window = window
    host.attach_window(window)
    manager.restore()
    host.reload_menu()
    host.tray = _install_tray(host)
    host.install_hotkeys()
    # 全屏自动隐藏：单实例监视器，1 Hz 探测；配置门关闭时不建定时器。
    host.start_fullscreen_hide()
    if str(config.get("frameseq") or "auto") != "off":
        from ..media.provision import start_background_provision

        start_background_provision(host)

    watcher = SessionWatcher(
        manager.shutdown,
        app,
        on_lock=host.pause_for_session,
        on_unlock=host.resume_after_session,
        hwnd_provider=(
            lambda: int(manager.primary.winId())
            if manager.primary is not None
            else 0
        ),
    )
    watcher.install()
    _wire_screens(app, manager)

    def finish() -> None:
        host._closing = True
        host.cancel_update()
        host.stop_fullscreen_hide()
        if host.hotkeys is not None:
            host.hotkeys.uninstall()
        host.close_dialogs()
        try:
            cancel_community_jobs(0.5)
        except Exception:  # noqa: BLE001
            logger.debug("community refresh cancel failed", exc_info=True)
        manager.shutdown()
        host.worker_host.shutdown_all()
        host.balance_presenter.shutdown()
        host._update_executor.shutdown(wait=False, cancel_futures=True)
        report = host.market_jobs.shutdown_all(2000)
        if report.timed_out:
            logger.warning("market jobs still running during aboutToQuit: %s", report)
        if host.features is not None:
            host.features.stop()
        if host.tray is not None:
            host.tray.hide()
        watcher.remove()

    app.aboutToQuit.connect(finish)
    return app.exec()
