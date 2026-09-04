# -*- coding: utf-8 -*-
"""
应用入口 —— QApplication + 桌宠窗口 + 系统托盘。

支持运行时切换角色：
- 右键桌宠 →「切换角色」
- 托盘菜单 →「切换角色」
切换后会热加载对应形象的 webm，并保留位置/朝向等配置。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import shiboken6
from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import autostart as autostart_mod
from . import balance as balance_mod
from . import catalog
from . import slot_manager as slot_manager_mod
from . import updater
from .click_sound import warm_click_sound_effects
from .config import APP_DIR_NAME, Config, _default_base
from .dsh_state import DshStateTracker
from .harness_launcher import launch_harness_gui
from .instance_launcher import launch_new_pet
from .library import MovieLibrary
from .window import PetWindow
from .persona_phrases import PhrasePicker
from .fun_image_popup import restore_ojingjing_windows
from .runtime_cleanup import cleanup_stale_runtime_dirs
from .collision_ipc import CollisionIpcSession


class _BackgroundResult(QObject):
    done = Signal(bool, object)


class _BalanceBridge(_BackgroundResult):
    def __init__(self, win, owner=None):
        super().__init__()
        self.win = win
        self.owner = owner
        self.done.connect(self._show)

    def _show(self, ok: bool, payload) -> None:
        # 异步回调可能晚于窗口销毁（切角色/退出），先探活再触碰 Qt 对象
        if self.win is None or not shiboken6.isValid(self.win):
            return
        if not ok:
            text = str(payload)
            if (getattr(self.win, "_alert_current", None) is not None
                    or getattr(self.win, "_bubble_suppressed", False)):
                self.win.show_alert(text, duration_ms=6000, sticky=False,
                                    priority=2, alert_type="balance")
            else:
                self.win.show_bubble(text, duration_ms=6000)
            return
        _show_balance_payload(self.win, payload)
        if self.owner is not None and hasattr(self.owner, "_update_island_balance"):
            self.owner._update_island_balance(payload)


_PERSONA_PICKER = PhrasePicker()


def _persona_text(win, key: str, fallback: str, **values) -> str:
    cfg = getattr(win, "cfg", None)
    if cfg is None:
        return fallback
    mode = str(cfg.get("dialogue_mode", "legacy") or "legacy")
    if mode == "custom":
        return _PERSONA_PICKER.custom(cfg.get("dialogue_phrases", {}), key, fallback, **values)
    return _PERSONA_PICKER.get(mode, key, fallback, **values)


def _show_balance_payload(win, payload) -> None:
    """展示余额气泡（含峰谷副标题）并按余额档位触发余额动画。

    网络查询、内存缓存、文件缓存三条路径统一走这里，避免缓存命中时
    没有副标题/不播动画导致的行为不一致。
    """
    if win is None or not shiboken6.isValid(win):
        return
    if isinstance(payload, dict):
        text = str(payload.get("text") or "余额信息为空")
        info = payload.get("info") or {}
    else:
        text = str(payload)
        info = {}
    cfg = getattr(win, "cfg", None)
    mode = str(cfg.get("balance_tier_labels_mode", "default") or "default") if cfg is not None else "default"
    custom_peak = str(cfg.get("balance_tier_label_peak", "") or "") if cfg is not None else ""
    custom_idle = str(cfg.get("balance_tier_label_idle", "") or "") if cfg is not None else ""
    peak_label, idle_label = balance_mod.resolve_tier_labels(mode, custom_peak, custom_idle)
    color_enabled = bool(cfg.get("balance_tier_color_enabled", True)) if cfg is not None else True
    if color_enabled:
        subtitle = balance_mod.deepseek_pricing_hint_html(
            peak_label=peak_label, idle_label=idle_label,
        )
    else:
        subtitle = balance_mod.deepseek_pricing_hint(
            peak_label=peak_label, idle_label=idle_label,
        )
    text = _persona_text(win, "balance.result", text, text=text)
    if (getattr(win, "_alert_current", None) is not None
            or getattr(win, "_bubble_suppressed", False)):
        win.show_alert(
            text, subtitle=subtitle, duration_ms=6000, sticky=False,
            priority=2, alert_type="balance",
        )
    else:
        win.show_bubble(
            text, duration_ms=6000,
            subtitle=subtitle,
        )
    # 按余额档位播放上游余额动画（仅当素材存在时静默跳过）
    p = balance_mod.balance_percent(info.get("total"))
    if p is not None:
        idx = balance_mod.balance_event_index(p)
        name = balance_mod.BALANCE_EVENT_NAMES[idx]
        if name and hasattr(win, "request_link_anim"):
            win.request_link_anim(name)


class _UpdateBridge(_BackgroundResult):
    def __init__(self, parent):
        super().__init__()
        self.parent = parent
        self.done.connect(self._show)

    def _show(self, ok: bool, payload) -> None:
        # 异步回调可能晚于窗口销毁，先探活再触碰 Qt 对象
        alive = self.parent is not None and shiboken6.isValid(self.parent)
        if not ok:
            if alive:
                self.parent.show_bubble(f"检查更新失败：{payload}", duration_ms=7000)
            return
        release = payload
        tag = str(release.get("version", ""))
        if not updater.is_newer(tag):
            if alive:
                self.parent.show_bubble(f"已经是最新版本（{updater.APP_VERSION}）啦")
            return
        if alive:
            self.parent.show_bubble(
                f"发现新版本 v{tag}（当前 {updater.APP_VERSION}）。"
                "可从“更新与帮助”打开项目页下载。",
                duration_ms=9000,
            )


def _setup_logging(config: Config) -> None:
    config.dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        handlers=[RotatingFileHandler(
            str(config.dir / f'pet-{os.getpid()}.log'),  # 多开实例日志按 PID 隔离，避免互相覆盖
            maxBytes=1_000_000, backupCount=2, encoding='utf-8',
        )],  # 滚动日志：1MB×2，不再无限增长
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        encoding='utf-8',
    )


def _show_startup_error(title: str, message: str) -> None:
    QMessageBox.critical(None, title, message)


def _cleanup_stale_runtime_dirs() -> None:
    """清理 PyInstaller onefile 遗留的 ``_MEI*`` 临时目录。

    只扫描系统临时目录中超过 24 小时的目录，并始终跳过当前进程的
    ``sys._MEIPASS``。删除失败只记录日志，不接管 ACL，也不影响启动。
    """
    if not getattr(sys, "frozen", False):
        return
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return

    current = Path(meipass).resolve(strict=False)
    result = cleanup_stale_runtime_dirs(current_dir=current)
    for directory in result.removed:
        logging.info("已清理遗留 PyInstaller 缓存目录: %s", directory)
    for directory, error in result.failed.items():
        logging.warning("清理 PyInstaller 缓存目录失败: %s (%s)", directory, error)

class PetApp:
    """管理桌宠窗口、托盘与角色热切换。"""

    def __init__(self, app: QApplication, config: Config, enable_chat: bool = True, slot_handle=None, slot_id: int | None = None) -> None:
        self.app = app
        self.config = config
        self.enable_chat = bool(enable_chat)
        self.slot_handle = slot_handle
        self.slot_id = slot_id
        self.win: PetWindow | None = None
        self.tray: QSystemTrayIcon | None = None
        self.chat_window = None
        self.legacy_chat_window = None
        self.modern_chat_window = None
        self.chat_settings_dialog = None
        self.modern_settings_dialog = None
        self.watchdog_settings_dialog = None
        self.island = None
        self.quick_chat = None
        self._spawned_pet_count = 0
        self._pending_dialog_opens: set[str] = set()
        self._balance_busy = False
        self._balance_cache = None
        self._balance_bridge = None
        self._on_about_to_quit_connected = False
        self._balance_timer = QTimer()
        self._balance_timer.timeout.connect(self.show_balance)
        self._update_bridge = None
        self._balance_cache_path = config.dir / 'balance_cache.json'  # 跨实例共享余额缓存（按 provider 绑定）
        self.collision_ipc = CollisionIpcSession(config, self)
        # DSH 统一状态跟踪：轻量常驻，接收 bridge 事件收敛为 offline/idle/thinking/
        # working/waiting_approval/success/error。状态日志由 dsh_state 内部按
        # edge-trigger 写出；这里只订阅 state_changed，供后续状态动画/通知等消费。
        # 不把 self.app 当 QObject parent（测试会传假对象），靠自身引用保活。
        self._dsh_state_tracker = DshStateTracker(config.dir)
        self._dsh_state_tracker.state_changed.connect(self._on_dsh_state_changed)

    # ------------------------------------------------------------ 启动
    def start(self) -> None:
        # aboutToQuit 只在控制器层绑定一次：角色热切换会重建窗口，逐个
        # connect win._save_position 会在旧窗口延迟销毁后残留失效引用。
        # 统一走 _on_about_to_quit，在信号触发时读取当前有效窗口。
        if not self._on_about_to_quit_connected:
            self.app.aboutToQuit.connect(self._on_about_to_quit)
            self._on_about_to_quit_connected = True
        self.collision_ipc.start()
        character_id = str(self.config.get('character', catalog.DEFAULT_CHARACTER))
        logging.info('当前形象: %s', character_id)
        self._create_ui(character_id)
        self._sync_dynamic_island()
        self._apply_spawn_offset()
        self._apply_balance_timer()
        # 启动 DSH 状态跟踪（DSH 未运行也不影响桌宠启动，只切 offline）
        self._dsh_state_tracker.start()
        QTimer.singleShot(3500, self._check_autostart_wanted)

    def _sync_dynamic_island(self) -> None:
        """按配置创建/隐藏灵动岛；桌宠隐藏后灵动岛仍可常驻。"""
        island_cfg = self.config.get("dynamic_island", {})
        enabled = bool(island_cfg.get("enabled", False)) if isinstance(island_cfg, dict) else False
        if not enabled:
            if getattr(self, "island", None) is not None:
                self.island.hide()
            return
        if getattr(self, "island", None) is None:
            from .dynamic_island import DynamicIsland

            self.island = DynamicIsland(self.config)
            self.island.clicked.connect(self._toggle_pet_from_island)
        self.island.refresh_from_config()
        pet_visible = True
        if self.win is not None:
            is_visible = getattr(self.win, "isVisible", None)
            pet_visible = bool(is_visible()) if callable(is_visible) else True
        self.island.set_pet_visible(pet_visible)
        self.island.show()

    def _toggle_pet_from_island(self) -> None:
        win = self.win
        if win is None:
            return
        if win.isVisible():
            win.hide(notify=False)
        else:
            win.show()
        if getattr(self, "island", None) is not None:
            self.island.set_pet_visible(win.isVisible())

    def _on_dsh_state_changed(self, from_state: str, to_state: str) -> None:
        """订阅 DSH 统一状态变化。

        第一版只做订阅 + 关键兜底（状态日志已由 dsh_state 在 transition 时写出）；
        后续的状态动画映射 / Windows Toast / 系统声音等在此挂接，不塞进 dsh_state。
        """
        if to_state == "offline" and self.win is not None:
            # DSH 断开/重启：审批/问题等阻塞交互必然失效，收掉一直挂着的常驻气泡；
            # 卡住检测的旧评分与行为模式检测的旧窗口也一并清零（重启后是全新任务，
            # 不沿用旧状态）。
            alm = getattr(self.win, "agent_link_manager", None)
            if alm is not None:
                if hasattr(alm, "dismiss_all_interactions"):
                    alm.dismiss_all_interactions()
                detector = getattr(alm, "_stuck_detector", None)
                if detector is not None and hasattr(detector, "reset_all"):
                    detector.reset_all()
                pattern = getattr(alm, "_behavior_detector", None)
                if pattern is not None and hasattr(pattern, "reset_all"):
                    pattern.reset_all()

    def _on_about_to_quit(self) -> None:
        """退出前保存当前有效窗口的位置并释放资源。

        aboutToQuit 只绑定一次自本控制器；切换角色会重建桌宠窗口，信号
        触发时读取当前窗口（self.win），避免调用已延迟销毁的旧窗口。
        """
        if self.win is not None:
            self.win._save_position()
        self.collision_ipc.stop()
        if self.slot_handle is not None:
            try:
                slot_manager_mod._unlock_file(self.slot_handle)
            except Exception:
                pass
            self.slot_handle = None

    def _set_autostart(self, enabled: bool, win=None) -> bool:
        ok = autostart_mod.set_enabled(bool(enabled))
        self.config.set("autostart_wanted", bool(enabled))
        self.config.save()
        target = win or self.win
        if target is not None and not ok:
            target.show_bubble("开机自启写入失败，请检查系统登录项或安全软件设置。", duration_ms=6000)
        return ok

    def _check_autostart_wanted(self) -> None:
        if self.config.get("autostart_wanted", False) and not autostart_mod.is_enabled() and self.win is not None:
            self.win.show_bubble("检测到开机自启已被系统或安全软件关闭，可在设置中重新启用。", duration_ms=7000)

    def _apply_balance_timer(self) -> None:
        self._balance_timer.stop()
        minutes = max(0, int(self.config.get("balance_refresh_minutes", 0) or 0))
        if minutes:
            self._balance_timer.start(minutes * 60000)

    def _update_island_balance(self, payload) -> None:
        """把余额文本/峰谷提示同步给灵动岛（若有）。"""
        if getattr(self, "island", None) is None:
            return
        text = "余额 --"
        info = {}
        if isinstance(payload, dict):
            text = str(payload.get("text") or "余额 --")
            info = payload.get("info") or {}
        peak_label, idle_label = balance_mod.resolve_tier_labels(
            str(self.config.get("balance_tier_labels_mode", "default") or "default"),
            str(self.config.get("balance_tier_label_peak", "") or ""),
            str(self.config.get("balance_tier_label_idle", "") or ""),
        )
        hint = balance_mod.deepseek_pricing_hint(
            peak_label=peak_label, idle_label=idle_label,
        )
        self.island.set_balance_info(hint, text)

    def show_balance(self, parent=None) -> None:
        win = parent or self.win
        if win is None or self._balance_busy or not win.isVisible():
            return
        now = time.monotonic()
        # 余额缓存绑定 provider 身份（id + base_url + key 摘要）：同地址不同账号也不串号；
        # 摘要不可逆推原 key，不落敏感信息。
        import hashlib
        settings = self.config.chat_settings()
        provider = settings.active_config
        provider.api_key = self.config.resolve_api_key(provider)
        key_digest = hashlib.sha256(str(provider.api_key or '').encode()).hexdigest()[:12]
        provider_key = '|'.join([
            str(getattr(provider, 'id', '') or ''),
            str(provider.base_url or ''),
            key_digest,
        ])
        if self._balance_cache is not None and now - self._balance_cache[0] < 30.0 \
                and self._balance_cache[2] == provider_key:
            self._update_island_balance(self._balance_cache[1])
            _show_balance_payload(win, self._balance_cache[1])
            return
        file_payload = self._read_balance_file_cache(provider_key)
        if file_payload is not None:
            self._balance_cache = (now, file_payload, provider_key)
            self._update_island_balance(file_payload)
            _show_balance_payload(win, file_payload)
            return
        self._balance_busy = True
        # 延迟到事件循环空闲再冒泡：macOS 菜单跟踪会话内新建/显示窗口会被
        # AppKit 抑制；回调再次探活，避免窗口销毁后触碰 Qt 对象。
        def window_is_visible() -> bool:
            try:
                return win is not None and shiboken6.isValid(win) and win.isVisible()
            except (TypeError, RuntimeError, AttributeError):
                return False

        def show_loading() -> None:
            if window_is_visible():
                win.show_bubble(
                    _persona_text(win, "balance.loading", "让我看看余额…"), duration_ms=6000
                )
        QTimer.singleShot(0, show_loading)
        bridge = _BalanceBridge(win, owner=self)
        self._balance_bridge = bridge
        try:
            threading.Thread(
                target=self._balance_worker,
                args=(bridge, provider.base_url, provider.api_key, provider.verify_ssl, provider_key),
                daemon=True, name='pet-balance',
            ).start()
        except Exception as exc:  # 线程创建/启动失败也不能永久 busy
            self._balance_busy = False
            if window_is_visible():
                QTimer.singleShot(0, lambda error_text=f"余额查询失败：{exc}": bridge.done.emit(
                    False, error_text
                ))

    def _balance_worker(self, bridge, base_url: str, api_key: str, verify_ssl: bool, provider_key: str = '') -> None:
        try:
            info = balance_mod.fetch_balance(base_url, api_key, verify_ssl=verify_ssl)
            text = balance_mod.format_balance(info)
            payload = {"text": text, "info": info}
            self._balance_cache = (time.monotonic(), payload, provider_key)
            self._write_balance_file_cache(payload, provider_key)
            bridge.done.emit(True, payload)
        except Exception as exc:  # noqa: BLE001 - 任何失败走气泡提示
            bridge.done.emit(False, f'余额查询失败：{exc}')
        finally:
            self._balance_busy = False

    def _read_balance_file_cache(self, provider_key: str = '') -> dict | None:
        """读取跨实例共享的余额缓存（30s 内有效，且必须是同一 provider 的缓存）。

        返回 {"text": ..., "info": {...}}；兼容旧版只存 text 字符串的缓存。
        """
        try:
            data = json.loads(self._balance_cache_path.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                return None
            if str(data.get('provider', '') or '') != provider_key:
                return None
            if time.time() - float(data.get('ts', 0) or 0) >= 30.0:
                return None
            text = str(data.get('text', '') or '')
            if not text:
                return None
            info = data.get('info')
            return {
                'text': text,
                'info': info if isinstance(info, dict) else {},
            }
        except (OSError, ValueError, TypeError):
            pass
        return None

    def _write_balance_file_cache(self, payload: dict, provider_key: str = '') -> None:
        """写入跨实例共享的余额缓存（原子替换，绑定 provider）。

        同时保存 text 和 info，使缓存命中时也能显示峰谷副标题并播放余额动画。
        """
        try:
            self._balance_cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._balance_cache_path.with_suffix(f'.{os.getpid()}.tmp')
            tmp.write_text(
                json.dumps({
                    'ts': time.time(),
                    'text': str(payload.get('text') or ''),
                    'info': payload.get('info') or {},
                    'provider': provider_key,
                }, ensure_ascii=False),
                encoding='utf-8',
            )
            tmp.replace(self._balance_cache_path)
        except OSError:
            pass

    def check_update(self, parent=None) -> None:
        target = parent or self.win
        if target is not None:
            target.show_bubble("正在检查更新…", duration_ms=6000)
        bridge = _UpdateBridge(target)
        self._update_bridge = bridge

        def worker() -> None:
            release = updater.latest_release()
            bridge.done.emit(bool(release), release or "无法连接更新服务，请稍后重试。")

        threading.Thread(target=worker, daemon=True, name="pet-update-check").start()

    def sync_look_to_chat(self, user_text: str, reply: str) -> None:
        """把「看看屏幕/主动识屏」的问答同步进 AI 对话记录（issue #24）。

        聊天窗口已创建 → 走窗口内同步（含界面即时刷新）；
        聊天窗口从未打开 → 直接写入当前角色最新会话（无则新建），之后再打开
        聊天窗口即可在历史里回看全文——气泡里被省略/分页的内容不再无处可查。
        """
        if not self.enable_chat or not str(reply or "").strip():
            return
        if self.chat_window is not None and hasattr(self.chat_window, "append_look_sync"):
            self.chat_window.append_look_sync(user_text, reply)
            return
        try:
            from .chat.models import ChatMessage
            from .chat.session_store import SessionStore

            store = SessionStore(self.config.dir, getattr(self.config, "instance_id", ""))
            character_id = str(self.config.get("character", catalog.DEFAULT_CHARACTER))
            sessions = store.list(character_id)
            if sessions:
                session = sessions[0]
            else:
                settings = self.config.chat_settings()
                session = store.create(
                    character_id,
                    settings.active_provider,
                    settings.default_system_prompt,
                )
            session.messages.append(ChatMessage("user", str(user_text)))
            session.messages.append(ChatMessage("assistant", str(reply)))
            store.save(session)
        except Exception:
            logging.exception("同步识屏问答到会话记录失败")

    def _apply_spawn_offset(self) -> None:
        """让新孵化的桌宠与母桌宠错开，避免两个窗口完全重叠。"""
        if self.win is None:
            return
        try:
            index = max(0, int(os.environ.get('DSH_PET_SPAWN_OFFSET_INDEX', '0')))
        except ValueError:
            index = 0
        if index <= 0:
            return
        scr = self.win._screen_available()
        if scr is None:
            return
        available = scr.availableGeometry()
        horizontal = -1 if self.win.geometry().center().x() > available.center().x() else 1
        vertical = -1 if self.win.geometry().center().y() > available.center().y() else 1
        x = self.win.x() + horizontal * 48 * index
        y = self.win.y() + vertical * 32 * index
        # 小屏（可用区比窗口还窄/矮）时上界 < 下界，min/max 会互相打架把
        # 窗口推出屏幕外；先判边界再钳制。
        max_x = available.right() - self.win.width() + 1
        max_y = available.bottom() - self.win.height() + 1
        x = available.left() if max_x < available.left() else min(max(x, available.left()), max_x)
        y = available.top() if max_y < available.top() else min(max(y, available.top()), max_y)
        self.win.move(x, y)

    def _create_library(self, character_id: str) -> MovieLibrary:
        lib = MovieLibrary(character_id=character_id)
        # UI 就绪后统一调度预热：高优先级立即后台跑（带 0~0.5s 错峰），
        # 随机动作池延迟 2s 补全，避免多开启动时 ffmpeg 进程洪峰。
        lib.schedule_high_priority_warm()
        lib.schedule_low_priority_warm()
        logging.info('素材加载完成：%s %d 段动画', character_id, len(lib.names()))
        return lib

    def _create_ui(self, character_id: str) -> None:
        lib = self._create_library(character_id)
        win = PetWindow(lib, self.config, collision_session=self.collision_ipc)
        win.on_switch_character = self.switch_character
        win.on_open_chat = self.open_chat if self.enable_chat else None
        win.on_open_quick_chat = self.open_quick_chat if self.enable_chat else None
        win.on_open_chat_settings = self.open_chat_settings if self.enable_chat else None
        win.on_show_balance = self.show_balance if self.enable_chat else None
        win.on_check_update = self.check_update
        win.on_look_synced = self.sync_look_to_chat if self.enable_chat else None
        win.on_look_screen = win._on_look_screen if self.enable_chat and hasattr(win, "_on_look_screen") else None
        win.on_open_legacy_settings = None
        win.on_open_modern_settings = self.open_modern_settings
        win.on_open_watchdog_settings = self.open_watchdog_settings
        win.on_spawn_pet = self.spawn_pet
        win.on_restore_fun_windows = restore_ojingjing_windows
        win.on_hidden = self._notify_pet_hidden
        # 预热点击音效：首次创建 QSoundEffect/QMediaPlayer 池并等待加载完成，
        # 在显示窗口前完成，避免窗口出现后主线程被音频初始化阻塞、
        # 首次点击 Q 弹卡顿。
        warm_click_sound_effects(
            self.config.get("click_sound_pack"),
            data_dir=self.config.dir,
        )
        win.show()

        tray = self._build_tray(win)

        # 清理旧对象（热切换时使用）
        old_win = self.win
        old_tray = self.tray
        self.win = win
        self.tray = tray

        if old_win is not None:
            old_win.hide(notify=False)
            old_tray.hide() if old_tray is not None else None
            QTimer.singleShot(0, old_win.deleteLater)
            if old_tray is not None:
                QTimer.singleShot(0, old_tray.deleteLater)

    # ------------------------------------------------------------ 角色切换
    def switch_character(self, character_id: str) -> None:
        if self.win is None:
            return
        current = str(self.config.get('character', catalog.DEFAULT_CHARACTER))
        if character_id == current:
            return

        # 先保存配置，即使后续加载失败也记住用户选择
        self.config.set('character', character_id)
        self.config.save()

        try:
            # 预创建新库，失败则保留当前角色
            lib = self._create_library(character_id)
        except Exception as exc:
            logging.exception('切换角色失败: %s', character_id)
            _show_startup_error('切换角色失败', str(exc))
            return

        logging.info('切换角色: %s -> %s', current, character_id)

        # 用新库创建新窗口/托盘，旧对象延迟销毁
        old_win = self.win
        old_win.detach_collision_session()
        self.collision_ipc.stop()
        self.collision_ipc = CollisionIpcSession(self.config, self)
        self.collision_ipc.start()
        win = PetWindow(lib, self.config, collision_session=self.collision_ipc)
        win.on_switch_character = self.switch_character
        win.on_open_chat = self.open_chat if self.enable_chat else None
        win.on_open_quick_chat = self.open_quick_chat if self.enable_chat else None
        win.on_open_chat_settings = self.open_chat_settings if self.enable_chat else None
        win.on_show_balance = self.show_balance if self.enable_chat else None
        win.on_check_update = self.check_update
        win.on_look_synced = self.sync_look_to_chat if self.enable_chat else None
        win.on_look_screen = win._on_look_screen if self.enable_chat and hasattr(win, "_on_look_screen") else None
        win.on_open_legacy_settings = None
        win.on_open_modern_settings = self.open_modern_settings
        win.on_open_watchdog_settings = self.open_watchdog_settings
        win.on_spawn_pet = self.spawn_pet
        win.on_restore_fun_windows = restore_ojingjing_windows
        win.on_hidden = self._notify_pet_hidden
        # 预热点击音效：首次创建 QSoundEffect/QMediaPlayer 池并等待加载完成，
        # 在显示窗口前完成，避免窗口出现后主线程被音频初始化阻塞、
        # 首次点击 Q 弹卡顿。
        warm_click_sound_effects(
            self.config.get("click_sound_pack"),
            data_dir=self.config.dir,
        )
        win.show()

        tray = self._build_tray(win)

        old_tray = self.tray
        self.win = win
        self.tray = tray

        old_win.hide(notify=False)
        if old_tray is not None:
            old_tray.hide()
        QTimer.singleShot(0, old_win.deleteLater)
        if old_tray is not None:
            QTimer.singleShot(0, old_tray.deleteLater)
        if self.enable_chat:
            for chat_window in (self.legacy_chat_window, self.modern_chat_window):
                if chat_window is not None:
                    chat_window.set_pet_window(self.win)
                    chat_window.switch_character(character_id)
        if getattr(self, "island", None) is not None:
            self.island.refresh_from_config()

    def open_chat(self) -> None:
        """Open the configured chat UI; menus only need this stable dispatcher."""
        if str(self.config.get("chat_ui_style", "modern")) == "classic":
            self.open_legacy_chat()
        else:
            self.open_modern_chat()

    def open_quick_chat(self) -> None:
        """打开快速对话气泡；与完整聊天窗共用会话历史。"""
        if not self.enable_chat or self.win is None:
            return
        from .quick_chat import QuickChatBubble

        if self.quick_chat is None:
            self.quick_chat = QuickChatBubble(self.config, pet_window=self.win)
            self.quick_chat.open_chat_callback = self.open_chat
        else:
            self.quick_chat.pet_window = self.win
            self.quick_chat.settings = self.config.chat_settings()
            self.quick_chat.session = self.quick_chat._get_session()
        self.quick_chat.show_for_pet(self.win)

    def open_legacy_chat(self) -> None:
        if not self.enable_chat or self.win is None:
            return
        if self._defer_while_popup_active("legacy-chat", self.open_chat):
            return
        from .chat.legacy_widgets import ChatWindow
        if self.legacy_chat_window is None:
            self.legacy_chat_window = ChatWindow(self.config, str(self.config.get('character', catalog.DEFAULT_CHARACTER)), pet_window=self.win)
        else:
            self.legacy_chat_window.set_pet_window(self.win)
        self.chat_window = self.legacy_chat_window
        self._present_dialog(self.legacy_chat_window, lambda: self.legacy_chat_window.position_near_pet(self.win))

    def open_modern_chat(self) -> None:
        if not self.enable_chat or self.win is None:
            return
        if self._defer_while_popup_active("modern-chat", self.open_modern_chat):
            return
        from .chat.widgets import ChatWindow
        if self.modern_chat_window is None:
            self.modern_chat_window = ChatWindow(self.config, str(self.config.get('character', catalog.DEFAULT_CHARACTER)), pet_window=self.win)
        else:
            self.modern_chat_window.set_pet_window(self.win)
        self.chat_window = self.modern_chat_window
        self._present_dialog(self.modern_chat_window, lambda: self.modern_chat_window.position_near_pet(self.win))

    def spawn_pet(self) -> None:
        """启动一个完全独立的新桌宠进程。"""
        try:
            self._spawned_pet_count += 1
            launch_new_pet(self._spawned_pet_count)
        except OSError as exc:
            self._spawned_pet_count = max(0, self._spawned_pet_count - 1)
            logging.exception('生小肥鱼失败')
            _show_startup_error('生小肥鱼失败', str(exc))

    def _defer_while_popup_active(self, key: str, callback) -> bool:
        """Avoid constructing a heavy dialog inside QMenu.exec()."""
        if QApplication.activePopupWidget() is None:
            self._pending_dialog_opens.discard(key)
            return False
        if key in self._pending_dialog_opens:
            return True
        self._pending_dialog_opens.add(key)

        def retry() -> None:
            if QApplication.activePopupWidget() is not None:
                QTimer.singleShot(50, retry)
                return
            self._pending_dialog_opens.discard(key)
            callback()

        QTimer.singleShot(50, retry)
        return True

    def _present_dialog(self, dialog, before_present=None, attempt: int = 0) -> None:
        """延迟呈现非模态窗口，直到任何弹出菜单关闭。

        macOS 的右键/托盘菜单是原生 NSMenu 跟踪会话（menu.exec 阻塞期间），
        菜单项动作触发时会话尚未结束，此时新建窗口的 show/raise/activate
        会被 AppKit 抑制——表现为首次点击「AI 设置 / 桌宠设置」无反应，
        需要再点一次（此时窗口实例已存在，直接 show 成功）。
        延迟到菜单关闭后再呈现即可稳定弹出；Qt 自绘菜单（Windows）同样
        覆盖：弹窗仍显示时重试等待。重试 60 次（约 3.6 秒）后放弃，
        防止弹窗长期不消失时无限空转。
        """
        if attempt > 60:
            return
        if QApplication.activePopupWidget() is not None:
            QTimer.singleShot(60, lambda: self._present_dialog(dialog, before_present, attempt + 1))
            return
        if before_present is not None:
            before_present()
        if dialog.isMinimized():
            dialog.showNormal()
        else:
            dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def open_chat_settings(self) -> None:
        """Open settings without blocking the desktop pet window.

        QDialog.exec() makes the dialog application-modal, which prevents the
        user from dragging or interacting with the pet while editing settings.
        Keep one modeless dialog alive instead, and refresh the chat window
        after the dialog reports an accepted save.
        """
        if not self.enable_chat:
            return
        from .chat.settings_dialog import ChatSettingsDialog
        if self.chat_settings_dialog is None:
            dialog = ChatSettingsDialog(self.config, self.chat_window)
            dialog.setModal(False)
            dialog.setWindowModality(Qt.WindowModality.NonModal)
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            dialog.finished.connect(self._chat_settings_finished)
            self.chat_settings_dialog = dialog
        self._update_bubble_suppression_for_settings()
        self._present_dialog(self.chat_settings_dialog)

    def _chat_settings_finished(self, result: int) -> None:
        dialog = self.chat_settings_dialog
        self.chat_settings_dialog = None
        self._update_bubble_suppression_for_settings()
        if result:
            self._refresh_chat_windows()

    def _refresh_chat_windows(self) -> None:
        """Refresh both independently styled chat windows after shared settings change."""
        for chat_window in (self.legacy_chat_window, self.modern_chat_window):
            if chat_window is not None:
                chat_window.refresh_settings()

    def _update_bubble_suppression_for_settings(self) -> None:
        """任一设置窗口打开时暂停桌宠气泡，避免气泡盖住设置界面。"""
        if getattr(self, "win", None) is None:
            return
        any_open = (
            getattr(self, "modern_settings_dialog", None) is not None
            or getattr(self, "chat_settings_dialog", None) is not None
        )
        self.win.set_bubble_suppressed(any_open)

    # ------------------------------------------------------------ 托盘
    def open_modern_settings(self) -> None:
        from .modern_settings_dialog import ModernSettingsDialog
        if self.modern_settings_dialog is None:
            dialog = ModernSettingsDialog(
                self.config,
                self.win,
                include_ai=self.enable_chat,
            )
            dialog.finished.connect(self._modern_settings_finished)
            self.modern_settings_dialog = dialog
        self._update_bubble_suppression_for_settings()
        # 在 show 之前定位，避免 Windows 上窗口先显示默认位置再跳走（闪现小窗）
        self._present_dialog(
            self.modern_settings_dialog,
            before_present=self.modern_settings_dialog.move_away_from_pet,
        )

    def open_watchdog_settings(self) -> None:
        from .exploration_watchdog_settings import WatchdogSettingsDialog
        if self.watchdog_settings_dialog is None:
            dialog = WatchdogSettingsDialog(self.config, self.win)
            dialog.set_pet_instance(self.win)
            dialog.settings_saved.connect(self._watchdog_settings_finished)
            self.watchdog_settings_dialog = dialog
        self._update_bubble_suppression_for_settings()
        self._present_dialog(
            self.watchdog_settings_dialog,
            before_present=self.watchdog_settings_dialog.move_away_from_pet,
        )

    def _watchdog_settings_finished(self) -> None:
        self.watchdog_settings_dialog = None
        self._update_bubble_suppression_for_settings()
        if self.win is not None:
            self.win.refresh_pet_settings()

    def _modern_settings_finished(self, result: int) -> None:
        self.modern_settings_dialog = None
        self._update_bubble_suppression_for_settings()
        # 新版设置在关闭时一律落盘（closeEvent 自动保存，「保存并退出」同样走
        # _write_config），因此无论 Accepted/Rejected 都把改动应用到桌宠。
        # 此前只有 Accepted 才刷新：直接 X 关闭时保存生效但桌宠不更新。
        if self.win is not None:
            self.win.refresh_pet_settings()
        self._sync_dynamic_island()
        self._apply_balance_timer()
        self._refresh_chat_windows()
        _mac_set_dock_icon_visible(bool(self.config.get("show_dock_icon", True)))

    def _notify_pet_hidden(self) -> None:
        """用户主动隐藏桌宠后弹托盘提示，指明恢复入口。"""
        if getattr(self, "island", None) is not None:
            self.island.set_pet_visible(False)
        if self.tray is None:
            return
        self.tray.showMessage(
            "桌宠已隐藏",
            "点击托盘图标或 Dock 图标即可恢复。",
            QSystemTrayIcon.MessageIcon.Information,
            4000,
        )

    def _build_tray(self, win: PetWindow) -> QSystemTrayIcon:
        tray = QSystemTrayIcon(QIcon(win.icon_pixmap()))

        def toggle_visible() -> None:
            if win.isVisible():
                win.hide()
            else:
                win.show()
            if getattr(self, "island", None) is not None:
                self.island.set_pet_visible(win.isVisible())

        menu = QMenu()
        # 气泡是置顶 Tool 窗口（层级高于原生菜单 popup），托盘菜单弹出前
        # 先隐藏气泡，避免气泡盖住菜单
        menu.aboutToShow.connect(lambda: win._speech_bubble.hide())
        menu.addAction('显示 / 隐藏', toggle_visible)

        island_action = menu.addAction('灵动岛')
        island_action.setCheckable(True)
        island_action.setChecked(bool(
            self.config.get("dynamic_island", {}).get("enabled", True)
        ))

        def toggle_island(enabled: bool) -> None:
            island_cfg = dict(self.config.get("dynamic_island", {}) or {})
            island_cfg["enabled"] = bool(enabled)
            self.config.set("dynamic_island", island_cfg)
            self.config.save()
            self._sync_dynamic_island()

        island_action.toggled.connect(toggle_island)

        if self.enable_chat:
            menu.addAction('AI 对话', self.open_chat)
            menu.addAction('快速对话（气泡）', self.open_quick_chat)
            menu.addAction('AI 设置', self.open_chat_settings)
        menu.addAction('桌宠设置', self.open_modern_settings)

        m_char = menu.addMenu('切换角色')
        current = str(self.config.get('character', catalog.DEFAULT_CHARACTER))
        for cid in catalog.list_available_characters():
            act = m_char.addAction(cid)
            act.setCheckable(True)
            act.setChecked(cid == current)
            act.triggered.connect(lambda checked=False, cid=cid: self.switch_character(cid))

        mouse_through = menu.addAction('鼠标穿透')
        mouse_through.setCheckable(True)
        mouse_through.setChecked(bool(self.config.get('mouse_through', False)))
        mouse_through.toggled.connect(win.set_mouse_through)

        menu.addSeparator()

        auto = menu.addAction('开机自启')
        auto.setCheckable(True)
        auto.setChecked(autostart_mod.is_enabled())
        auto.toggled.connect(lambda enabled: self._set_autostart(enabled, win))

        def sync_tray_checks() -> None:
            # 设置对话框/右键菜单里改过的开关，弹出托盘菜单前同步复选状态
            #（托盘菜单在 _build_tray 时一次性构建，不复用则不刷新会过期）
            mouse_through.setChecked(bool(self.config.get('mouse_through', False)))
            auto.setChecked(autostart_mod.is_enabled())
            island_action.setChecked(bool(
                self.config.get("dynamic_island", {}).get("enabled", True)
            ))

        menu.aboutToShow.connect(sync_tray_checks)

        menu.addSeparator()
        if self.enable_chat:
            menu.addAction('DeepSeek 余额', lambda: self.show_balance(win))
        menu.addAction('检查更新', lambda: self.check_update(win))
        menu.addAction('启动 DeepSeek Harness', lambda: launch_harness_gui(win))
        menu.addAction('退出', self.app.quit)

        tray.setContextMenu(menu)
        tray.setToolTip('dsh-pet 独立桌宠')
        tray.activated.connect(
            lambda reason: toggle_visible()
            if reason == QSystemTrayIcon.ActivationReason.DoubleClick
            else None
        )
        tray.show()
        return tray


def _mac_set_dock_icon_visible(visible: bool) -> None:
    """Switch the macOS application policy without restarting the pet.

    The speech bubble itself owns the non-activating window flags; application
    activation policy must not be used as a focus workaround because Accessory
    Regular (0) displays a Dock item; Accessory (1) keeps the application out
    of the Dock. Pet tool windows own their independent visibility/focus flags.
    """
    if sys.platform != 'darwin':
        return
    try:
        import ctypes
        import ctypes.util

        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library('objc') or '/usr/lib/libobjc.A.dylib')
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.objc_getClass.restype = ctypes.c_void_p
        msg = objc.objc_msgSend
        msg.restype = ctypes.c_void_p
        msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        shared = msg(
            objc.objc_getClass(b'NSApplication'),
            objc.sel_registerName(b'sharedApplication'),
        )
        # NSApplicationActivationPolicyRegular = 0; Accessory = 1
        msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
        msg(shared, objc.sel_registerName(b'setActivationPolicy:'), 0 if visible else 1)
    except Exception:
        pass


def _default_xcb_platform_on_wayland() -> None:
    """Linux Wayland 会话下把 Qt 平台插件默认设为 xcb（XWayland）。

    Wayland 协议不允许客户端自行移动顶层窗口，桌宠拖动依赖的
    QWidget.move() 会被合成器静默忽略（表现为无法拖动）；透明无边框
    窗口在原生 wayland 插件下还存在重绘残留（拖影）。须在创建
    QApplication 之前调用。用户显式设置 QT_QPA_PLATFORM 时尊重其选择。
    """
    if not sys.platform.startswith("linux"):
        return
    if "QT_QPA_PLATFORM" in os.environ:
        return
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "wayland":
        os.environ["QT_QPA_PLATFORM"] = "xcb"


def main(argv: list[str] | None = None, enable_chat: bool = True) -> int:
    _default_xcb_platform_on_wayland()
    argv = list(argv if argv is not None else sys.argv)
    preferred_slot = None

    if "--instance" in argv:
        logging.error("参数 --instance 已弃用并移除，多开实例请改用 --slot <0-127>")
        return 1

    if "--slot" in argv:
        index = argv.index("--slot")
        if index + 1 < len(argv):
            try:
                preferred_slot = int(argv[index + 1])
                if preferred_slot < 0 or preferred_slot > 127:
                    logging.error("无效的 --slot 参数 (必须在 0~127 范围内): %s", argv[index + 1])
                    return 1
            except ValueError:
                logging.error("无效的 --slot 参数: %s", argv[index + 1])
                return 1
        else:
            logging.error("缺少 --slot 参数值")
            return 1

    app = QApplication(argv)
    app.setApplicationName(APP_DIR_NAME)
    app.setQuitOnLastWindowClosed(False)

    # 确定配置根目录
    config_dir = _default_base() / APP_DIR_NAME

    # 执行槽位竞争取得排他锁
    slot_handle = None
    slot_id = None

    try:
        try:
            slot_id, slot_handle = slot_manager_mod.acquire_pet_slot(
                config_dir,
                preferred_slot=preferred_slot,
            )
        except Exception as exc:
            logging.exception("获取桌宠槽位锁失败")
            _show_startup_error("dsh-pet-standalone", str(exc))
            return 1

        instance_id = slot_manager_mod.slot_to_instance_id(slot_id)
        os.environ["DSH_PET_INSTANCE"] = instance_id

        # 迁移旧 spawn 实例
        if slot_id == 0:
            slot_manager_mod.migrate_legacy_spawns(config_dir)

        config = Config(instance_id=instance_id)

        _mac_set_dock_icon_visible(bool(config.get("show_dock_icon", True)))

        _setup_logging(config)

        try:
            source_path = Path(__file__).resolve()
            source_mtime = source_path.stat().st_mtime
        except Exception:
            source_path = "unknown"
            source_mtime = 0

        logging.info(
            "dsh-pet-standalone 启动 "
            "slot=%s instance=%s runtime_version=%s "
            "app_version=%s source=%s executable=%s build_mtime=%s",
            slot_id,
            instance_id,
            "pet-runtime-2026-09-01.1",
            getattr(updater, "APP_VERSION", "unknown"),
            source_path,
            sys.executable,
            source_mtime,
        )

        _cleanup_stale_runtime_dirs()

        stale_removed = autostart_mod.cleanup_stale_entries()
        if stale_removed:
            logging.info(
                "已清理 %d 个指向不存在路径的开机自启项",
                stale_removed,
            )

        controller = PetApp(
            app,
            config,
            enable_chat=enable_chat,
            slot_handle=slot_handle,
            slot_id=slot_id,
        )

        try:
            controller.start()
        except Exception as exc:
            logging.exception("启动失败")
            _show_startup_error("dsh-pet-standalone", str(exc))
            return 1

        logging.info("进入事件循环")
        return app.exec()
    finally:
        if slot_handle is not None:
            try:
                slot_manager_mod._unlock_file(slot_handle)
            except Exception:
                pass
            slot_handle = None


if __name__ == '__main__':
    sys.exit(main())
