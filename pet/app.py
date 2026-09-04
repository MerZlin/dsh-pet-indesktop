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
from . import click_sound
from . import slot_manager as slot_manager_mod
from . import updater
from .config import APP_DIR_NAME, Config, _default_base
from .context_menus.shared import open_deepseek_web
from .desktop_notify import DesktopNotification, position_stack
from .harness_launcher import launch_harness_gui
from .instance_launcher import launch_new_pet
from .library import MovieLibrary
from .window import PetWindow
from .fun_image_popup import restore_ojingjing_windows
from .runtime_cleanup import cleanup_stale_runtime_dirs
# 可选服务模块按需在 _ensure_* 中局部导入：功能关闭时不让这些模块
# 进入启动 import 路径（Phase 1 开关式加载门控）。


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
            self.win.show_bubble(str(payload), duration_ms=6000)
            return
        _show_balance_payload(self.win, payload)
        if self.owner is not None and hasattr(self.owner, "_update_island_balance"):
            self.owner._update_island_balance(payload)


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
    _cleanup_old_pet_logs(config.dir)


def _cleanup_old_pet_logs(log_dir, *, max_age_days: float = 7.0) -> int:
    """启动时清理过期的 pet-<pid>.log（含滚动备份 .log.1/.2）。

    每实例每次启动都产生新文件，不清理会无界累积（审查 GLM-M2）。
    只删本变体命名空间下超龄文件；失败静默（清理不影响启动）。
    """
    removed = 0
    try:
        cutoff = time.time() - max_age_days * 86400
        for path in Path(log_dir).glob('pet-*.log*'):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                pass
    except OSError:
        pass
    return removed


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
        self.dock_menu: QMenu | None = None
        self._notification_click_callback = None
        self._toast_windows: list[DesktopNotification] = []
        self.chat_window = None
        self.legacy_chat_window = None
        self.modern_chat_window = None
        self.chat_settings_dialog = None
        self.modern_settings_dialog = None
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
        # Phase 1 开关式加载：可选服务按配置懒创建/懒启动；关闭的功能不构造
        # CollisionIpcSession / TodoReminderService / BrokerFacade 对象。
        self.collision_ipc = None
        self.todo_service = None
        self.broker_facade = None
        self.todo_panel = None
        if self._collision_wanted():
            self._ensure_collision_ipc()
        if self._todo_wanted():
            self._ensure_todo_service()
        if self._collision_wanted() and self._broker_wanted():
            self._ensure_broker_facade()

    # ------------------------------------------------------------ 功能门控
    def _collision_wanted(self) -> bool:
        return bool(self.config.get("collision_enabled", True))

    def _todo_wanted(self) -> bool:
        return bool(self.config.get("todo_reminder_enabled", True))

    def _broker_wanted(self) -> bool:
        return bool(self.config.get("decode_broker_enabled", False))

    def _ensure_collision_ipc(self):
        """懒创建碰撞 IPC 会话（仅在碰撞功能开启时创建）。"""
        if getattr(self, "collision_ipc", None) is None:
            from .collision_ipc import CollisionIpcSession
            self.collision_ipc = CollisionIpcSession(self.config, self)
        return self.collision_ipc

    def _ensure_broker_facade(self):
        """懒创建多开共享解码 facade（仅在 decode_broker 开启时创建）。"""
        if getattr(self, "broker_facade", None) is None:
            from .decode_broker import BrokerFacade
            self.broker_facade = BrokerFacade(enabled=True)
        return self.broker_facade

    def _ensure_todo_service(self):
        """懒创建待办提醒服务（仅在使用待办/打开面板时创建）。"""
        if getattr(self, "todo_service", None) is None:
            from .todo_reminder import TodoReminderService
            self.todo_service = TodoReminderService(self)
        return self.todo_service

    def _stop_collision_ipc(self) -> None:
        """停止并释放碰撞 IPC 与 broker facade（两者同生命周期）。"""
        broker = getattr(self, "broker_facade", None)
        if broker is not None:
            try:
                broker.shutdown()
            except Exception:
                logging.exception("退出时关闭 broker facade 失败")
            self.broker_facade = None
        collision = getattr(self, "collision_ipc", None)
        if collision is not None:
            try:
                collision.stop()
            except Exception:
                logging.exception("停止碰撞 IPC 失败")
            self.collision_ipc = None

    def _attach_window_collision(self) -> None:
        """把当前有效窗口绑定到当前碰撞 IPC（仅当两者都存在且未绑定）。"""
        win = self.win
        if win is None or self.collision_ipc is None:
            return
        same_session = getattr(win, "_collision_session", None) is self.collision_ipc
        broker = getattr(self, "broker_facade", None)
        same_broker = getattr(win, "_broker_facade", None) is broker
        if same_session and same_broker:
            return
        win._broker_facade = broker
        try:
            win.attach_collision_session(self.collision_ipc)
        except Exception:
            logging.exception("窗口绑定碰撞会话失败")

    def _sync_collision_service(self, *, attach_window: bool = True) -> None:
        """按配置启停碰撞 IPC / broker：启动前保证会话已创建。"""
        if self._collision_wanted():
            self._ensure_collision_ipc()
            if self._broker_wanted():
                self._ensure_broker_facade()
            thread = getattr(self.collision_ipc, "_thread", None)
            if thread is None or not callable(getattr(thread, "isRunning", None)) or not thread.isRunning():
                start = getattr(self.collision_ipc, "start", None)
                if callable(start):
                    start()
            if attach_window:
                self._attach_window_collision()
        else:
            if attach_window and self.win is not None:
                try:
                    self.win.detach_collision_session()
                except Exception:
                    logging.exception("窗口解绑碰撞会话失败")
            self._stop_collision_ipc()

    def _sync_todo_service(self) -> None:
        """按配置启停待办提醒服务；关闭且无面板打开时释放服务对象。"""
        if self._todo_wanted():
            service = self._ensure_todo_service()
            timer = getattr(service, "_timer", None)
            if timer is not None and callable(getattr(timer, "isActive", None)) and timer.isActive():
                # 已在运行：设置保存只刷新偏好/条目，不重置 30s tick。
                service.apply_config()
            elif callable(getattr(service, "start", None)):
                service.start()
        elif getattr(self, "todo_service", None) is not None:
            try:
                self.todo_service.stop()
            except Exception:
                logging.exception("停止待办提醒服务失败")
            # 面板持有 app 引用并动态读取 todo_service；面板还开着时保留对象。
            if getattr(self, "todo_panel", None) is None:
                self.todo_service = None

    def _sync_feature_services(self) -> None:
        """设置保存/启动等公共入口：按配置同步所有可选服务生命周期。"""
        self._sync_collision_service(attach_window=True)
        self._sync_todo_service()

    # ------------------------------------------------------------ 启动
    def start(self) -> None:
        # aboutToQuit 只在控制器层绑定一次：角色热切换会重建窗口，逐个
        # connect win.save_position 会在旧窗口延迟销毁后残留失效引用。
        # 统一走 _on_about_to_quit，在信号触发时读取当前有效窗口。
        if not self._on_about_to_quit_connected:
            self.app.aboutToQuit.connect(self._on_about_to_quit)
            self._on_about_to_quit_connected = True
        # 碰撞会话必须先于窗口创建启动，PetWindow 构造时才能 attach 到实际会话。
        self._sync_collision_service(attach_window=False)
        character_id = str(self.config.get('character', catalog.DEFAULT_CHARACTER))
        logging.info('当前形象: %s', character_id)
        self._create_ui(character_id)
        self._install_macos_dock_menu()
        self._sync_dynamic_island()
        self._apply_spawn_offset()
        self._apply_balance_timer()
        self._sync_todo_service()
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

    def _on_about_to_quit(self) -> None:
        """退出前保存当前有效窗口的位置并释放资源。

        aboutToQuit 只绑定一次自本控制器；切换角色会重建桌宠窗口，信号
        触发时读取当前窗口（self.win），避免调用已延迟销毁的旧窗口。
        """
        if self.win is not None:
            self.win.save_position()
        # 退出前暂停动画预热：低优预热队列（ThreadPoolExecutor 的 worker 非
        # daemon）在解释器退出期会被排空执行——不暂停的话退出会被拖住数秒
        # 并继续拉起 ffmpeg（审查 DS-M1）
        try:
            if self.win is not None and getattr(self.win, 'lib', None) is not None:
                self.win.lib.pause_warm()
        except Exception:
            logging.exception("退出时暂停预热失败")
        # 停掉 Agent 监视器 worker 线程（不依赖 closeEvent 是否来得及触发）
        if self.win is not None and getattr(self.win, 'agent_link_manager', None) is not None:
            self.win.agent_link_manager.shutdown()
        # P3 broker + 碰撞：同生命周期停止并释放（功能关闭时可能从未创建）
        self._stop_collision_ipc()
        if getattr(self, "todo_service", None) is not None:
            self.todo_service.stop()
        # 会话异步写盘（B8）：退出前先把各聊天窗口的当前会话提交保存，
        # 再永久关闭写盘 worker（关掉后迟到的 queued 回调提交会被明确拒绝）。
        try:
            from .chat import session_store as _session_store
            for _w in (self.legacy_chat_window, self.modern_chat_window, self.quick_chat):
                _session = getattr(_w, 'session', None)
                _store = getattr(_w, 'store', None)
                if _session is not None and _store is not None:
                    try:
                        _store.save(_session)
                    except Exception:
                        logging.exception("退出前保存会话失败")
            if not _session_store.close_all_writers(permanent=True):
                logging.warning("退出时会话写盘 worker 未干净关闭")
        except Exception:
            logging.exception("退出时关闭会话写盘 worker 失败")
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
        # AppKit 抑制（与设置对话框首次点击无反应同源），singleShot 在 macOS
        # 上要等菜单关闭后才派发，Windows 上立即派发也无害。
        QTimer.singleShot(0, lambda: win.show_bubble('让我看看余额…', duration_ms=6000))
        bridge = _BalanceBridge(win, owner=self)
        self._balance_bridge = bridge
        threading.Thread(
            target=self._balance_worker,
            args=(bridge, provider.base_url, provider.api_key, provider.verify_ssl, provider_key),
            daemon=True, name='pet-balance',
        ).start()

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
        # 重入防护（审查 GLM-L3）：连点不应起多个检查线程/叠气泡
        if getattr(self, "_update_checking", False):
            return
        self._update_checking = True
        target = parent or self.win
        if target is not None:
            target.show_bubble("正在检查更新…", duration_ms=6000)
        bridge = _UpdateBridge(target)
        self._update_bridge = bridge
        # 完成后放行下一次检查（无论成败）
        bridge.done.connect(lambda *_: setattr(self, "_update_checking", False))

        def worker() -> None:
            try:
                release = updater.latest_release()
            except Exception as exc:
                # 后台线程异常必须收口回 GUI，否则更新提示永远停在
                # 「正在检查更新」（审查 P1-01）
                logging.debug("检查更新失败", exc_info=True)
                bridge.done.emit(False, str(exc))  # 前缀由 _UpdateBridge._show 统一加
                return
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
            msgs = [ChatMessage("user", str(user_text)), ChatMessage("assistant", str(reply))]
            synced, _absorbed = store.append_messages(session, msgs)
            if synced is None:
                # 会话已被并发删除等边界：本地兜底（保持旧行为）
                session.messages.extend(msgs)
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
        scr = self.win.screen_available()
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

    def _wire_window(self, win: PetWindow) -> None:
        """绑定新窗口的回调接线（创建与角色切换共用，两处历史逐行重复）。

        两段原始代码逐行一致（并集 = 该段本身，未发现任一方多设回调），
        后续新增回调只改这一处即可保证两个入口同步。
        """
        win.on_switch_character = self.switch_character
        win.on_open_chat = self.open_chat if self.enable_chat else None
        win.on_open_quick_chat = self.open_quick_chat if self.enable_chat else None
        win.on_open_chat_settings = self.open_chat_settings if self.enable_chat else None
        win.on_show_balance = self.show_balance if self.enable_chat else None
        win.on_check_update = self.check_update
        win.on_look_synced = self.sync_look_to_chat if self.enable_chat else None
        win.on_look_screen = win.look_at_screen if self.enable_chat and hasattr(win, "look_at_screen") else None
        win.on_open_legacy_settings = None
        win.on_open_modern_settings = self.open_modern_settings
        win.on_spawn_pet = self.spawn_pet
        win.on_restore_fun_windows = restore_ojingjing_windows
        win.on_open_todo_panel = self.open_todo_panel
        win.on_hidden = self._notify_pet_hidden

    def _build_window(self, character_id: str, lib: MovieLibrary | None = None) -> PetWindow:
        """创建新窗口/托盘并完成接线、音效预热与旧对象延迟销毁（创建与切换共用）。

        从 _create_ui 与 switch_character 两处历史逐行重复的公共序列（约 25 行）
        抽出：步骤顺序与 deleteLater / QTimer.singleShot 时序与原实现完全一致。
        lib 可预传入（switch_character 先预创建、失败则保留当前角色），
        缺省时按 character_id 创建（_create_ui 启动路径）。
        """
        if lib is None:
            lib = self._create_library(character_id)
        win = PetWindow(lib, self.config, collision_session=self.collision_ipc,
                        broker_facade=self.broker_facade)
        self._wire_window(win)
        # 预热点击音效：首次创建 QSoundEffect/QMediaPlayer 池并等待加载完成，
        # 在显示窗口前完成，避免窗口出现后主线程被音频初始化阻塞、
        # 首次点击 Q 弹卡顿。Phase 1：点击音效关闭时不预加载 QtMultimedia。
        if bool(self.config.get("click_sound_enabled", True)):
            click_sound.warm_click_sound_effects(
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
            if old_tray is not None:
                old_tray.hide()
            QTimer.singleShot(0, old_win.deleteLater)
            if old_tray is not None:
                QTimer.singleShot(0, old_tray.deleteLater)
        return win

    def _create_ui(self, character_id: str) -> None:
        self._build_window(character_id)

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
            # 预创建新库，失败则保留当前角色（在动旧窗口之前完成）
            lib = self._create_library(character_id)
        except Exception as exc:
            logging.exception('切换角色失败: %s', character_id)
            _show_startup_error('切换角色失败', str(exc))
            return

        logging.info('切换角色: %s -> %s', current, character_id)

        # 先停旧窗口的碰撞会话与 Agent 监视器 worker，再按当前配置重建 IPC：
        # 否则旧窗口 deleteLater 后其 worker 线程仍经引用链保活并继续轮询
        # （B9 一审发现）。新窗口/托盘由 _build_window 创建（含旧对象延迟销毁）。
        # Phase 1：碰撞关闭时不创建 IPC；开启时由 _sync_collision_service 懒建。
        old_win = self.win
        old_win.detach_collision_session()
        if getattr(old_win, 'agent_link_manager', None) is not None:
            old_win.agent_link_manager.shutdown()
        self._stop_collision_ipc()
        self._sync_collision_service(attach_window=False)
        self._build_window(character_id, lib=lib)
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
        # Cocoa 原生 QMenu 跟踪期间 activePopupWidget() 可能为 None，且其
        # 嵌套事件循环会把这个 singleShot 留到菜单关闭后再派发。若是 Qt
        # 自绘 popup，下一层仍通过 _defer_while_popup_active 等待其关闭。
        QTimer.singleShot(0, self._show_quick_chat)

    def _show_quick_chat(self) -> None:
        if not self.enable_chat or self.win is None:
            return
        if self._defer_while_popup_active("quick-chat", self._show_quick_chat):
            return
        from .quick_chat import QuickChatBubble

        if self.quick_chat is None:
            self.quick_chat = QuickChatBubble(self.config, pet_window=self.win)
            self.quick_chat.open_chat_callback = self.open_chat
        else:
            self.quick_chat.pet_window = self.win
            self.quick_chat.settings = self.config.chat_settings()
            self.quick_chat.refresh_session()
        self.quick_chat.show_for_pet(self.win)

    def open_legacy_chat(self) -> None:
        if not self.enable_chat or self.win is None:
            return
        if self._defer_while_popup_active("legacy-chat", self.open_chat):
            return
        from .chat.legacy_widgets import ChatWindow
        if self.legacy_chat_window is None:
            self.legacy_chat_window = ChatWindow(
                self.config,
                str(self.config.get('character', catalog.DEFAULT_CHARACTER)),
                pet_window=self.win,
                notifier=self.system_notify,
                auth_callback=self.open_chat_settings,
            )
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
            self.modern_chat_window = ChatWindow(
                self.config,
                str(self.config.get('character', catalog.DEFAULT_CHARACTER)),
                pet_window=self.win,
                notifier=self.system_notify,
                auth_callback=self.open_chat_settings,
            )
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
    def _install_macos_dock_menu(self) -> QMenu | None:
        """Install the native Dock context menu as an independent recovery path."""
        if sys.platform != "darwin":
            self.dock_menu = None
            return None
        menu = QMenu()

        def show_pet() -> None:
            win = self.win
            if win is None:
                return
            win.show()
            win.raise_()
            if getattr(self, "island", None) is not None:
                self.island.set_pet_visible(True)

        menu.addAction("显示桌宠", show_pet)
        menu.addAction("桌宠设置", self.open_modern_settings)
        if self.enable_chat:
            menu.addAction("AI 对话", self.open_chat)
        menu.addSeparator()
        quit_callback = getattr(self.app, "quit", None)
        if callable(quit_callback):
            menu.addAction("退出", quit_callback)
        install_dock_menu = getattr(menu, "setAsDockMenu", None)
        dock_menu_installed = callable(install_dock_menu)
        if dock_menu_installed:
            install_dock_menu()
        menu.setProperty("dockMenuInstalled", dock_menu_installed)
        self.dock_menu = menu
        return menu

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

    def _modern_settings_finished(self, result: int) -> None:
        self.modern_settings_dialog = None
        self._update_bubble_suppression_for_settings()
        # 新版设置在关闭时一律落盘（closeEvent 自动保存，「保存并退出」同样走
        # _write_config），因此无论 Accepted/Rejected 都把改动应用到桌宠。
        # 此前只有 Accepted 才刷新：直接 X 关闭时保存生效但桌宠不更新。
        # Phase 1：先按配置启停可选服务，再让窗口刷新碰撞/其它设置。
        self._sync_feature_services()
        if self.win is not None:
            self.win.refresh_pet_settings()
        self._sync_dynamic_island()
        self._apply_balance_timer()
        if getattr(self, "todo_service", None) is not None:
            self.todo_service.apply_config()
        self._refresh_chat_windows()
        _mac_set_dock_icon_visible(bool(self.config.get("show_dock_icon", True)))

    def open_todo_panel(self) -> None:
        """打开待办管理面板（非模态单例；条目增删改即时落盘）。"""
        from .todo_panel import TodoPanelDialog

        # Phase 1：即使总开关关闭，用户主动打开面板也需要服务对象（懒创建）。
        self._ensure_todo_service()
        if self.todo_panel is None:
            dialog = TodoPanelDialog(self, parent=self.win)
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            dialog.finished.connect(self._todo_panel_finished)
            self.todo_panel = dialog
        self._present_dialog(self.todo_panel)

    def _todo_panel_finished(self, _result: int) -> None:
        self.todo_panel = None

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

    def system_notify(self, title: str, message: str, *, on_click=None, duration_ms: int = 5000) -> None:
        """Show a bottom-right desktop notification (self-drawn, tray-independent)."""
        self._prune_toasts()
        toast = DesktopNotification(
            str(title),
            str(message),
            on_click=on_click,
            duration_ms=int(duration_ms),
        )
        self._toast_windows.append(toast)
        toast.destroyed.connect(lambda _obj=None: self._prune_toasts())
        toast.show()
        position_stack(self._toast_windows)

    def _prune_toasts(self) -> None:
        self._toast_windows = [
            w for w in self._toast_windows
            if not (hasattr(w, "is_closed") and w.is_closed())
        ]
        position_stack(self._toast_windows)

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
        menu.aboutToShow.connect(lambda: win.hide_speech_bubble())
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
            menu.addAction('启动 DeepSeek Harness', lambda: launch_harness_gui(win))
        else:
            # 纯桌宠版本不提供本地 DSH 启动入口，只保留网页版入口
            menu.addAction('打开网页版 DeepSeek', open_deepseek_web)
        menu.addAction('检查更新', lambda: self.check_update(win))
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


def _configure_linux_fcitx_input_method() -> None:
    """为 PySide6 冻结版选择与内置 Qt ABI 兼容的 Fcitx 输入法前端。"""
    # Linux 成品随包携带按 PySide6 Qt ABI 编译的 Fcitx 插件；未指定时默认选中 fcitx 上下文。
    if not sys.platform.startswith("linux"):
        return
    if os.environ.get("XMODIFIERS", "").strip() != "@im=fcitx":
        return
    if not os.environ.get("QT_IM_MODULE", "").strip():
        os.environ["QT_IM_MODULE"] = "fcitx"


def main(argv: list[str] | None = None, enable_chat: bool = True) -> int:
    _default_xcb_platform_on_wayland()
    # 必须在 QApplication 构造前设置，Qt 才会按随包 Fcitx 插件创建输入法上下文。
    _configure_linux_fcitx_input_method()
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
            slot_id, slot_handle = slot_manager_mod.acquire_pet_slot(config_dir, preferred_slot=preferred_slot)
        except Exception as exc:
            logging.exception("获取桌宠槽位锁失败")
            _show_startup_error("dsh-pet-standalone", str(exc))
            return 1

        instance_id = slot_manager_mod.slot_to_instance_id(slot_id)
        os.environ["DSH_PET_INSTANCE"] = instance_id

        # 迁移旧 spawn 实例（主槽或无并发运行旧实例时触发）
        if slot_id == 0:
            slot_manager_mod.migrate_legacy_spawns(config_dir)

        config = Config(instance_id=instance_id)
        _mac_set_dock_icon_visible(bool(config.get("show_dock_icon", True)))
        _setup_logging(config)
        logging.info("dsh-pet-standalone 启动 (slot: %s, instance: %s)", slot_id, instance_id)
        _cleanup_stale_runtime_dirs()
        stale_removed = autostart_mod.cleanup_stale_entries()
        if stale_removed:
            logging.info("已清理 %d 个指向不存在路径的开机自启项", stale_removed)

        controller = PetApp(app, config, enable_chat=enable_chat, slot_handle=slot_handle, slot_id=slot_id)
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
