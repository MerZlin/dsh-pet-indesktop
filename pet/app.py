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
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import autostart as autostart_mod
from . import catalog
from .config import APP_DIR_NAME, Config
from .harness_launcher import launch_harness_gui
from .library import MovieLibrary
from .window import PetWindow
from .runtime_cleanup import cleanup_stale_runtime_dirs


def _setup_logging(config: Config) -> None:
    config.dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(config.dir / 'pet.log'),
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        encoding='utf-8',
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

    # ------------------------------------------------------------ 启动
    def start(self) -> None:
        character_id = str(self.config.get('character', catalog.DEFAULT_CHARACTER))
        logging.info('当前形象: %s', character_id)
        self._create_ui(character_id)

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
            dialog = PetSettingsDialog(self.config, self.win)
            dialog.finished.connect(self._pet_settings_finished)
            self.pet_settings_dialog = dialog
        self._present_dialog(self.pet_settings_dialog)

    def _pet_settings_finished(self, result: int) -> None:
        self.pet_settings_dialog = None
        if result and self.win is not None:
            self.win.refresh_pet_settings()

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
        auto.toggled.connect(autostart_mod.set_enabled)

        menu.addSeparator()
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
