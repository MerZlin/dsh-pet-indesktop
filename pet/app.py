# -*- coding: utf-8 -*-
"""
应用入口 —— QApplication + 桌宠窗口 + 系统托盘。

支持运行时切换角色：
- 右键桌宠 →「切换角色」
- 托盘菜单 →「切换角色」
切换后会热加载对应形象的 webm，并保留位置/朝向等配置。
"""

from __future__ import annotations

import logging
import sys
import threading
import time
import webbrowser
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QMenu, QMessageBox, QProgressDialog, QSystemTrayIcon,
)

from . import autostart as autostart_mod
from . import balance as balance_mod
from . import catalog
from . import updater
from .config import APP_DIR_NAME, Config
from .harness_launcher import launch_harness_gui
from .library import MovieLibrary
from .window import PetWindow
from .runtime_cleanup import cleanup_stale_runtime_dirs


class _BalanceBridge(QObject):
    """余额查询线程 → 主线程信号桥（气泡显示）。"""

    done = Signal(bool, str)

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self._win = win
        self.done.connect(self._on_done)

    def _on_done(self, ok: bool, message: str) -> None:
        if self._win is not None and hasattr(self._win, 'show_bubble'):
            self._win.show_bubble(message, duration_ms=6000)


class _UpdateBridge(QObject):
    """检查更新/下载线程 → 主线程的信号桥。

    关键：所有信号都连接到本对象（主线程 QObject）自身的槽方法，
    跨线程 emit 会被 Qt 自动 QueuedConnection 排队回主线程执行。
    不能 connect(lambda...)——lambda 没有 receiver，PySide6 会在
    发射线程（后台）直接调用，QMessageBox 等 GUI 操作在非主线程
    执行会导致弹窗不显示。
    """

    failed = Signal(str)                    # 查询失败（网络/API）
    uptodate = Signal(str)                  # 已是最新（当前版本号）
    update_available = Signal(str, str, str, str, str)  # tag, html_url, body, asset_url, asset_name
    progress = Signal(int, int)             # received, total
    download_done = Signal(bool, str)       # ok, 路径或错误信息

    def __init__(self, parent_window=None, parent=None):
        super().__init__(parent)
        self._parent = parent_window
        self._download_dialog = None
        self._cancel_event = None
        self.failed.connect(self._on_failed)
        self.uptodate.connect(self._on_uptodate)
        self.update_available.connect(self._on_update_available)
        self.progress.connect(self._on_progress)
        self.download_done.connect(self._on_download_done)

    # ------------------------------------------------------------ 查询结果
    def _on_failed(self, message: str) -> None:
        if self._parent is not None and hasattr(self._parent, 'show_bubble'):
            self._parent.show_bubble('网络不太好，检查更新失败了…', duration_ms=5000)
        QMessageBox.warning(self._parent, '检查更新', message)

    def _on_uptodate(self, version: str) -> None:
        if self._parent is not None and hasattr(self._parent, 'show_bubble'):
            self._parent.show_bubble(f'已经是最新版本（{version}）啦', duration_ms=4000)
        else:
            QMessageBox.information(self._parent, '检查更新', f'已是最新版本（{version}）')

    def _on_update_available(self, tag: str, url: str, body: str,
                             asset_url: str, asset_name: str) -> None:
        box = QMessageBox(self._parent)
        box.setWindowTitle('发现新版本')
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(f'发现新版本 v{tag}（当前 {updater.APP_VERSION}）')
        box.setInformativeText(body.strip() or '请前往 Release 页面查看更新内容。')
        dl = box.addButton('下载安装包', QMessageBox.ButtonRole.AcceptRole)
        page = box.addButton('打开下载页', QMessageBox.ButtonRole.ActionRole)
        box.addButton('取消', QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is page:
            webbrowser.open(url)
        elif clicked is dl:
            if asset_url:
                self._start_download(asset_url, asset_name)
            else:
                QMessageBox.warning(
                    self._parent, '下载安装包',
                    '当前平台/变体没有匹配的安装资产，请打开下载页手动选择。',
                )

    # ------------------------------------------------------------ 下载
    def _start_download(self, url: str, name: str) -> None:
        dest = updater.download_dir() / name
        dialog = QProgressDialog(
            f'正在准备下载…\n保存到：{dest}', '取消', 0, 100, self._parent,
        )
        dialog.setWindowTitle('下载更新')
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        self._download_dialog = dialog
        self._download_name = name
        self._download_dest = dest
        self._last_progress_ts = 0.0
        self._last_progress_received = 0
        self._cancel_event = threading.Event()
        dialog.canceled.connect(self._cancel_event.set)
        threading.Thread(
            target=self._download_worker, args=(url, dest), daemon=True,
            name='pet-update-download',
        ).start()
        dialog.exec()
        self._download_dialog = None

    def _download_worker(self, url: str, dest: Path) -> None:
        ok, info = updater.download(
            url, dest,
            progress_cb=lambda received, total: self.progress.emit(received, total),
            cancel_event=self._cancel_event,
        )
        self.download_done.emit(ok, info)

    def _on_progress(self, received: int, total: int) -> None:
        dialog = self._download_dialog
        if dialog is None:
            return
        dialog.setValue(int(received * 100 / total) if total > 0 else 0)
        # 节流更新文本（路径 + 大小 + 实时速度），避免每块都刷新 UI
        now = time.monotonic()
        if now - self._last_progress_ts < 0.3:
            return
        speed = max(0.0, (received - self._last_progress_received) / 0.3 / 1024.0)
        self._last_progress_ts = now
        self._last_progress_received = received
        mb = received / 1048576.0
        total_mb = total / 1048576.0 if total > 0 else 0.0
        dialog.setLabelText(
            f'正在下载：{self._download_name}\n'
            f'保存到：{self._download_dest}\n'
            f'{mb:.1f} / {total_mb:.1f} MB　{speed:.0f} KB/s'
        )

    def _on_download_done(self, ok: bool, message: str) -> None:
        if self._download_dialog is not None:
            self._download_dialog.close()
        if ok:
            box = QMessageBox(None)
            box.setWindowTitle('下载完成')
            box.setIcon(QMessageBox.Icon.Information)
            box.setText(
                f'安装包已保存到：\n{message}\n\n'
                '关闭桌宠后运行安装即可升级。'
            )
            open_folder = box.addButton('打开所在文件夹', QMessageBox.ButtonRole.AcceptRole)
            box.addButton('关闭', QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is open_folder:
                _reveal_in_file_manager(Path(message))
        else:
            QMessageBox.warning(
                None, '下载失败',
                f'下载失败：{message}\n\n'
                'GitHub 下载源可能被网络屏蔽，可改用右键菜单\n'
                '「夸克网盘下载」获取安装包。',
            )


def _reveal_in_file_manager(path: Path) -> None:
    """在文件管理器中定位文件（Windows explorer /select，macOS open -R，Linux xdg-open 父目录）。"""
    try:
        import subprocess
        if sys.platform == 'win32':
            subprocess.Popen(['explorer', '/select,', str(path)])
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', '-R', str(path)])
        else:
            subprocess.Popen(['xdg-open', str(path.parent)])
    except Exception:
        pass


def _setup_logging(config: Config) -> None:
    config.dir.mkdir(parents=True, exist_ok=True)
    from logging.handlers import RotatingFileHandler
    logging.basicConfig(
        handlers=[RotatingFileHandler(
            str(config.dir / 'pet.log'),
            maxBytes=1_000_000, backupCount=2, encoding='utf-8',
        )],  # 滚动日志：1MB×2，不再无限增长
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
    )


def _show_startup_error(title: str, message: str) -> None:
    QMessageBox.critical(None, title, message)


def _check_ffmpeg_available() -> bool:
    """检测视频解码组件（imageio_ffmpeg 自带的 ffmpeg）是否可用。

    杀毒软件可能隔离/删除 ffmpeg.exe：直接启动会因首帧解码失败触发
    'NoneType' object has no attribute 'isNull' 崩溃，这里提前给出
    明确提示并降级为占位显示（见 window._make_placeholder_pixmap）。
    """
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        ok = bool(exe) and Path(exe).is_file()
        if not ok:
            logging.error('ffmpeg 不可用: %s', exe)
        return ok
    except Exception as exc:
        logging.error('ffmpeg 检测失败: %s', exc)
        return False


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

    def __init__(self, app: QApplication, config: Config, enable_chat: bool = True) -> None:
        self.app = app
        self.config = config
        self.enable_chat = bool(enable_chat)
        self.win: PetWindow | None = None
        self.tray: QSystemTrayIcon | None = None
        self.chat_window = None
        self.chat_settings_dialog = None
        self.pet_settings_dialog = None
        self._balance_busy = False
        self._balance_bridge = None
        self._balance_timer = None
        self._balance_cache = None  # (monotonic_ts, 文本)：30s 内复用，避免重复点击慢查询

    # ------------------------------------------------------------ 启动
    def start(self) -> None:
        character_id = str(self.config.get('character', catalog.DEFAULT_CHARACTER))
        logging.info('当前形象: %s', character_id)
        self._create_ui(character_id)
        self._apply_balance_timer()
        # 延迟自检：开机自启被安全软件清理时提醒（只提醒一次）
        QTimer.singleShot(3500, self._check_autostart_wanted)

    def _set_autostart(self, on: bool, win=None) -> bool:
        """切换开机自启：记录期望状态，写入失败/需系统授权时提示用户。"""
        win = win or self.win
        ok = autostart_mod.set_enabled(bool(on))
        self.config.set('autostart_wanted', bool(on))
        self.config.save()
        if win is None:
            return ok
        if not ok:
            win.show_bubble(
                '开机自启写入失败：可能被安全软件拦截，'
                '可稍后在托盘菜单重试或检查安全软件设置。',
                duration_ms=6000,
            )
        elif on and sys.platform == 'darwin':
            # macOS 新版系统（Ventura+）对未签名 LaunchAgent 需要用户授权
            win.show_bubble(
                '已开启开机自启；如重启未生效，请到'
                '「系统设置 → 通用 → 登录项」中允许桌宠。',
                duration_ms=8000,
            )
        return ok

    def _check_autostart_wanted(self) -> None:
        """启动自检：用户曾开启自启但系统里已不存在（被安全软件/优化工具清理）时提醒。"""
        if not self.config.get('autostart_wanted', False):
            return
        if autostart_mod.is_enabled():
            return
        if self.win is not None:
            self.win.show_bubble(
                '检测到开机自启被禁用或清理（可能来自安全软件/优化工具），'
                '可重新勾选开机自启。',
                duration_ms=7000,
            )

    def sync_look_to_chat(self, user_text: str, reply: str) -> None:
        """把「看看屏幕」的对话内容同步到 AI 对话当前会话。"""
        if not self.enable_chat or self.chat_window is None:
            return
        try:
            self.chat_window.append_look_sync(user_text, reply)
        except Exception as exc:
            logging.exception('同步看看屏幕到对话失败: %s', exc)

    # ------------------------------------------------------------ 余额
    def show_balance(self, parent=None) -> None:
        """菜单/定时/点击入口：查询 DeepSeek 余额，气泡显示（30s 缓存复用）。"""
        win = parent or self.win
        if win is None or self._balance_busy:
            return
        if not win.isVisible():
            return  # 桌宠隐藏时静默跳过（自动刷新场景）
        now = time.monotonic()
        if self._balance_cache is not None and now - self._balance_cache[0] < 30.0:
            win.show_bubble(self._balance_cache[1], duration_ms=6000)
            return
        self._balance_busy = True
        # 延迟到事件循环空闲再冒泡：macOS 菜单跟踪会话内新建/显示窗口会被
        # AppKit 抑制（与设置对话框首次点击无反应同源），singleShot 在 macOS
        # 上要等菜单关闭后才派发，Windows 上立即派发也无害。
        QTimer.singleShot(0, lambda: win.show_bubble('让我看看余额…', duration_ms=6000))
        settings = self.config.chat_settings()
        provider = settings.active_config
        provider.api_key = self.config.resolve_api_key(provider)
        bridge = _BalanceBridge(win)
        self._balance_bridge = bridge
        threading.Thread(
            target=self._balance_worker,
            args=(bridge, provider.base_url, provider.api_key, provider.verify_ssl),
            daemon=True, name='pet-balance',
        ).start()

    def _balance_worker(self, bridge, base_url: str, api_key: str, verify_ssl: bool) -> None:
        try:
            info = balance_mod.fetch_balance(base_url, api_key, verify_ssl=verify_ssl)
            text = balance_mod.format_balance(info)
            self._balance_cache = (time.monotonic(), text)
            bridge.done.emit(True, text)
        except Exception as exc:  # noqa: BLE001 - 任何失败走气泡提示
            bridge.done.emit(False, f'余额查询失败：{exc}')
        finally:
            self._balance_busy = False

    def _apply_balance_timer(self) -> None:
        """按设置启停余额自动刷新（分钟，0=关闭）。"""
        minutes = max(0, int(self.config.get('balance_refresh_minutes', 0) or 0))
        if self._balance_timer is None:
            self._balance_timer = QTimer()
            self._balance_timer.timeout.connect(self.show_balance)
        self._balance_timer.stop()
        if minutes > 0:
            self._balance_timer.start(minutes * 60000)

    def _create_library(self, character_id: str) -> MovieLibrary:
        lib = MovieLibrary(character_id=character_id)
        logging.info('素材加载完成：%s %d 段动画', character_id, len(lib.names()))
        return lib

    def _create_ui(self, character_id: str) -> None:
        lib = self._create_library(character_id)
        win = PetWindow(lib, self.config)
        win.on_switch_character = self.switch_character
        win.on_open_chat = self.open_chat if self.enable_chat else None
        win.on_open_chat_settings = self.open_chat_settings if self.enable_chat else None
        win.on_open_settings = self.open_pet_settings
        win.on_check_update = self.check_update
        win.on_show_balance = self.show_balance if self.enable_chat else None
        win.on_look_synced = self.sync_look_to_chat if self.enable_chat else None
        win.show()

        tray = self._build_tray(win)

        # 清理旧对象（热切换时使用）
        old_win = self.win
        old_tray = self.tray
        self.win = win
        self.tray = tray

        if old_win is not None:
            old_win.hide()
            old_tray.hide() if old_tray is not None else None
            QTimer.singleShot(0, old_win.deleteLater)
            if old_tray is not None:
                QTimer.singleShot(0, old_tray.deleteLater)

        self.app.aboutToQuit.connect(win._save_position)

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
        win = PetWindow(lib, self.config)
        win.on_switch_character = self.switch_character
        win.on_open_chat = self.open_chat if self.enable_chat else None
        win.on_open_chat_settings = self.open_chat_settings if self.enable_chat else None
        win.on_open_settings = self.open_pet_settings
        win.on_check_update = self.check_update
        win.on_show_balance = self.show_balance if self.enable_chat else None
        win.on_look_synced = self.sync_look_to_chat if self.enable_chat else None
        win.show()

        tray = self._build_tray(win)

        old_win = self.win
        old_tray = self.tray
        self.win = win
        self.tray = tray

        old_win.hide()
        if old_tray is not None:
            old_tray.hide()
        QTimer.singleShot(0, old_win.deleteLater)
        if old_tray is not None:
            QTimer.singleShot(0, old_tray.deleteLater)
        if self.enable_chat and self.chat_window is not None:
            self.chat_window.set_pet_window(self.win)
            self.chat_window.switch_character(character_id)

        self.app.aboutToQuit.connect(win._save_position)

    def open_chat(self) -> None:
        if not self.enable_chat or self.win is None:
            return
        from .chat.widgets import ChatWindow
        if self.chat_window is None:
            self.chat_window = ChatWindow(self.config, str(self.config.get('character', catalog.DEFAULT_CHARACTER)), pet_window=self.win)
        else:
            self.chat_window.set_pet_window(self.win)
        self._present_dialog(self.chat_window, lambda: self.chat_window.position_near_pet(self.win))

    def _present_dialog(self, dialog, before_present=None, attempt: int = 0) -> None:
        """延迟呈现非模态窗口，直到任何弹出菜单关闭。

        macOS 的右键/托盘菜单是原生 NSMenu 跟踪会话（menu.exec 阻塞期间），
        菜单项动作触发时会话尚未结束，此时新建窗口的 show/raise/activate
        会被 AppKit 抑制——表现为首次点击「AI 设置 / 桌宠设置」无反应，
        需要再点一次（此时窗口实例已存在，直接 show 成功）。
        延迟到菜单关闭后再呈现即可稳定弹出；Qt 自绘菜单（Windows）同样
        覆盖：弹窗仍显示时重试等待。
        """
        if QApplication.activePopupWidget() is not None and attempt < 8:
            QTimer.singleShot(60, lambda: self._present_dialog(dialog, before_present, attempt + 1))
            return
        if before_present is not None:
            before_present()
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
        self._present_dialog(self.chat_settings_dialog)

    def _chat_settings_finished(self, result: int) -> None:
        dialog = self.chat_settings_dialog
        self.chat_settings_dialog = None
        if result and self.chat_window is not None:
            self.chat_window.refresh_settings()

    # ------------------------------------------------------------ 托盘
    def open_pet_settings(self) -> None:
        from .settings_dialog import PetSettingsDialog
        if self.pet_settings_dialog is None:
            dialog = PetSettingsDialog(self.config, self.win, enable_chat=self.enable_chat)
            dialog.finished.connect(self._pet_settings_finished)
            self.pet_settings_dialog = dialog
        self._present_dialog(self.pet_settings_dialog)

    def _pet_settings_finished(self, result: int) -> None:
        self.pet_settings_dialog = None
        if result and self.win is not None:
            self.win.refresh_pet_settings()
            self._apply_balance_timer()  # 余额刷新间隔可能已修改
            # 全屏自动隐藏开关可能在设置里改了：同步到窗口（启停 watcher）
            if sys.platform == 'win32':
                self.win.set_auto_hide_fullscreen(
                    bool(self.config.get('auto_hide_fullscreen', True))
                )

    # ------------------------------------------------------------ 检查更新
    def check_update(self, parent=None) -> None:
        """菜单入口：后台查询 GitHub 最新 release，弹窗告知结果。

        点击后立即用气泡反馈"检查中"，避免网络慢时用户以为没反应；
        有新版时弹窗可选「直接下载」（按平台/变体选资产，流式下载带进度）
        或「打开下载页」；无更新用气泡轻量提示。
        """
        parent = parent or self.win
        if parent is not None and hasattr(parent, 'show_bubble'):
            # 延迟冒泡：macOS 菜单跟踪会话内显示窗口会被 AppKit 抑制
            QTimer.singleShot(0, lambda: parent.show_bubble('让我看看有没有新版本…', duration_ms=8000))
        bridge = _UpdateBridge(parent)
        self._update_bridge = bridge  # 持有引用，防止线程运行期间被 GC
        threading.Thread(
            target=self._update_worker, args=(bridge,), daemon=True,
            name='pet-update-check',
        ).start()

    def _update_worker(self, bridge: _UpdateBridge) -> None:
        release = updater.latest_release()
        if release is None:
            bridge.failed.emit(
                '无法检查更新（网络或 GitHub API / 镜像均不可用）。\n'
                f'可手动访问：{updater.REPO_URL}/releases\n'
                '或使用右键菜单「夸克网盘下载」。'
            )
            return
        tag = str(release.get('version', ''))
        if not tag or not updater.is_newer(tag):
            bridge.uptodate.emit(updater.APP_VERSION)
            return
        asset = updater.pick_asset(release)
        bridge.update_available.emit(
            tag,
            str(release.get('html_url', updater.REPO_URL)),
            str(release.get('notes') or '')[:600],
            str(asset.get('browser_download_url', '')) if asset else '',
            str(asset.get('name', '')) if asset else '',
        )

    def _build_tray(self, win: PetWindow) -> QSystemTrayIcon:
        tray = QSystemTrayIcon(QIcon(win.icon_pixmap()))

        def toggle_visible() -> None:
            if win.isVisible():
                win.hide()
            else:
                win.show()

        menu = QMenu()
        menu.addAction('显示 / 隐藏', toggle_visible)
        if self.enable_chat:
            menu.addAction('AI 对话', self.open_chat)
            menu.addAction('AI 设置', self.open_chat_settings)
        menu.addAction('桌宠设置', self.open_pet_settings)

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
        auto.toggled.connect(lambda on: self._set_autostart(on, win))

        menu.addSeparator()
        menu.addAction('DeepSeek 余额', lambda: self.show_balance(win))
        menu.addAction('检查更新', lambda: self.check_update(win))
        menu.addAction('GitHub 项目页', lambda: webbrowser.open(updater.REPO_URL))
        if sys.platform == 'win32':
            menu.addAction('夸克网盘下载', lambda: webbrowser.open(updater.QUARK_PAN_URL))
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


def _mac_set_accessory_activation() -> None:
    """macOS：把应用设为 accessory 激活策略。

    桌宠的气泡等窗口是定时器驱动的，普通应用策略下任何窗口出现都会
    激活应用、抢走用户正在输入应用的焦点。Accessory 策略下应用：
    - 不出现在 Dock、无菜单栏，窗口出现不激活应用、不抢焦点；
    - 点击应用窗口仍可正常激活（聊天窗输入不受影响）。
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
        # NSApplicationActivationPolicyAccessory = 1
        msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
        msg(shared, objc.sel_registerName(b'setActivationPolicy:'), 1)
    except Exception:
        pass


def main(argv: list[str] | None = None, enable_chat: bool = True) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_DIR_NAME)
    app.setQuitOnLastWindowClosed(False)
    _mac_set_accessory_activation()

    config = Config()
    _setup_logging(config)
    logging.info('dsh-pet-standalone 启动')
    _cleanup_stale_runtime_dirs()

    # GIF 变体用 QMovie 播放不依赖 ffmpeg；WebM 变体需要视频解码组件。
    # 组件不可用（如被杀毒软件隔离）时提前提示，程序降级为占位显示而非崩溃。
    if 'gif' not in APP_DIR_NAME and not _check_ffmpeg_available():
        QMessageBox.warning(
            None,
            '视频解码组件不可用',
            '未找到可用的 ffmpeg 视频解码组件（可能被杀毒软件隔离或删除）。\n'
            '桌宠将以占位样式运行，动画无法正常播放。\n'
            '请在杀毒软件中恢复/信任 ffmpeg 后重启本程序。',
        )

    controller = PetApp(app, config, enable_chat=enable_chat)
    try:
        controller.start()
    except Exception as exc:
        logging.exception('启动失败')
        _show_startup_error('dsh-pet-standalone', str(exc))
        return 1

    logging.info('进入事件循环')
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
