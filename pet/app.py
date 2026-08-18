# -*- coding: utf-8 -*-
"""
应用入口 —— QApplication + 桌宠窗口 + 系统托盘。

托盘提供"显示/隐藏"与"退出"；桌宠窗口本身不可关闭
（setQuitOnLastWindowClosed(False)），退出统一走托盘/右键菜单。
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import autostart as autostart_mod
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


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName('dsh-pet-standalone')
    app.setQuitOnLastWindowClosed(False)

    config = Config()
    _setup_logging(config)
    logging.info('dsh-pet-standalone 启动')

    try:
        character_id = str(config.get('character', catalog.DEFAULT_CHARACTER))
        logging.info('当前形象: %s', character_id)
        lib = MovieLibrary(character_id=character_id)
    except FileNotFoundError as exc:
        logging.exception('素材缺失')
        _show_startup_error('dsh-pet-standalone', str(exc))
        return 1
    except RuntimeError as exc:
        logging.exception('素材探测失败')
        _show_startup_error('dsh-pet-standalone', str(exc))
        return 1

    logging.info('素材加载完成：%d 段动画', len(lib.names()))

    win = PetWindow(lib, config)
    win.show()

    # ---- 系统托盘 ----
    tray = QSystemTrayIcon(QIcon(win.icon_pixmap()), win)

    def toggle_visible() -> None:
        if win.isVisible():
            win.hide()
        else:
            win.show()

    menu = QMenu()
    menu.addAction('显示 / 隐藏', toggle_visible)

    auto = menu.addAction('开机自启')
    auto.setCheckable(True)
    auto.setChecked(autostart_mod.is_enabled())
    auto.toggled.connect(autostart_mod.set_enabled)

    menu.addSeparator()
    menu.addAction('退出', app.quit)
    tray.setContextMenu(menu)
    tray.setToolTip('dsh-pet 独立桌宠')
    tray.activated.connect(
        lambda reason: toggle_visible()
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick
        else None
    )
    tray.show()

    app.aboutToQuit.connect(win._save_position)
    logging.info('进入事件循环')
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
