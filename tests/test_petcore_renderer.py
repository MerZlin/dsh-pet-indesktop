# -*- coding: utf-8 -*-
"""petcore 帧渲染：pixmap 只做格式转换，缩放/镜像留给 paintEvent。"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from petcore.ui.renderer import frame_pixmap


def _qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def test_frame_pixmap_does_not_scale_or_set_dpr() -> None:
    _qapp()
    image = QImage(100, 50, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)

    pixmap = frame_pixmap(image)

    assert pixmap is not None
    assert (pixmap.width(), pixmap.height()) == (100, 50)
    assert pixmap.devicePixelRatio() == 1.0


def test_frame_pixmap_keeps_unmirrored_pixels() -> None:
    _qapp()
    image = QImage(4, 2, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    image.setPixelColor(0, 0, Qt.GlobalColor.red)

    pixmap = frame_pixmap(image)

    assert pixmap is not None
    assert pixmap.toImage().pixelColor(0, 0).red() > 200
    assert pixmap.toImage().pixelColor(3, 0).alpha() == 0
