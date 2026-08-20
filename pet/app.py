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
import shutil
import sys
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import autostart as autostart_mod
from . import catalog
from .config import Config
from .library import MovieLibrary
from .window import PetWindow


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


def _cleanup_stale_runtime_dirs() -> None:
    """清理 PyInstaller onefile 遗留的 _MEI* 临时目录。

    注意：多开桌宠时，每个实例都有自己的 _MEI 目录，不能删除其他正在运行的实例目录。
    这里只清理“很久没有被修改”的目录，避免误删其他桌宠的运行缓存。
    """
    if not getattr(sys, "frozen", False):
        return
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return
    current = Path(meipass).resolve()
    parent = current.parent
    stale_age = 24 * 3600  # 只清理超过 24 小时未变化的目录
    now = time.time()
    for child in parent.glob("_MEI[0-9]*"):
        if not child.is_dir() or child.resolve() == current:
            continue
        try:
            mtime = child.stat().st_mtime
        except OSError:
            continue
        if now - mtime < stale_age:
            continue
        try:
            shutil.rmtree(child)
            logging.info("已清理遗留缓存目录: %s", child)
        except OSError:
            logging.warning("清理遗留缓存目录失败（可能被占用）: %s", child)


class PetApp:
    """管理桌宠窗口、托盘与角色热切换。"""

    def __init__(self, app: QApplication, config: Config) -> None:
        self.app = app
        self.config = config
        self.win: PetWindow | None = None
        self.tray: QSystemTrayIcon | None = None

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

        self.app.aboutToQuit.connect(win._save_position)

    # ------------------------------------------------------------ 托盘
    def _build_tray(self, win: PetWindow) -> QSystemTrayIcon:
        tray = QSystemTrayIcon(QIcon(win.icon_pixmap()))

        def toggle_visible() -> None:
            if win.isVisible():
                win.hide()
            else:
                win.show()

        menu = QMenu()
        menu.addAction('显示 / 隐藏', toggle_visible)

        m_char = menu.addMenu('切换角色')
        current = str(self.config.get('character', catalog.DEFAULT_CHARACTER))
        for cid in catalog.list_available_characters():
            act = m_char.addAction(cid)
            act.setCheckable(True)
            act.setChecked(cid == current)
            act.triggered.connect(lambda checked=False, cid=cid: self.switch_character(cid))

        menu.addSeparator()

        auto = menu.addAction('开机自启')
        auto.setCheckable(True)
        auto.setChecked(autostart_mod.is_enabled())
        auto.toggled.connect(autostart_mod.set_enabled)

        menu.addSeparator()
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


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName('dsh-pet-standalone')
    app.setQuitOnLastWindowClosed(False)

    config = Config()
    _setup_logging(config)
    logging.info('dsh-pet-standalone 启动')
    _cleanup_stale_runtime_dirs()

    controller = PetApp(app, config)
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
