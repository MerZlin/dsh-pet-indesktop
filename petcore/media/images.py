"""图片装载：按设备像素比解码，避免高 DPI 屏上「先缩到逻辑像素再放大」发虚。

主分支彩蛋/气泡配图直接用原始大图让 painter 缩放到设备分辨率；这里把这套
画质口径固定成一条规则：

- 逻辑显示尺寸上限仍是调用方给的 ``max_side``（像素不变）；
- 实际解码尺寸乘上窗口所在屏的 ``devicePixelRatio``，写回 pixmap 的 DPR；
- 小图不会被 DPR 缩小逻辑尺寸（按 DPR 预放大，视觉大小与 100% 缩放时一致）。

QLabel/绘制端拿到带 DPR 的 pixmap 后就是 1:1 物理像素贴图，不再二次插值。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QGuiApplication, QImageReader, QPixmap

FALLBACK_DPR = 1.0


def sanitize_dpr(value) -> float:
    try:
        dpr = float(value)
    except (TypeError, ValueError):
        return FALLBACK_DPR
    if dpr != dpr or dpr <= 0:  # NaN / 非正数
        return FALLBACK_DPR
    return dpr


def screen_dpr(window=None) -> float:
    """窗口所在屏的设备像素比；窗口还没显示时退回主屏。"""
    screen = None
    if window is not None:
        try:
            screen = window.screen()
        except Exception:
            screen = None
    if screen is None:
        try:
            screen = QGuiApplication.primaryScreen()
        except Exception:
            screen = None
    if screen is None:
        return FALLBACK_DPR
    try:
        return sanitize_dpr(screen.devicePixelRatio())
    except Exception:
        return FALLBACK_DPR


def physical_side(max_side: int, dpr: float = FALLBACK_DPR) -> int:
    """逻辑上限换算成物理像素上限。"""
    return max(1, int(round(max(1, int(max_side)) * sanitize_dpr(dpr))))


def scale_pixmap(
    pixmap: QPixmap, max_side: int, dpr: float = FALLBACK_DPR
) -> QPixmap:
    """把现成 pixmap 按同一条 DPR 规则缩放（物理像素上限 + 写回 DPR）。"""
    if pixmap is None or pixmap.isNull():
        return pixmap
    dpr = sanitize_dpr(dpr)
    limit = physical_side(max_side, dpr)
    width, height = pixmap.width(), pixmap.height()
    if width > limit or height > limit:
        pixmap = pixmap.scaled(
            limit,
            limit,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    elif dpr != 1.0:
        # 小图：按 DPR 预放大，逻辑尺寸保持原样（否则 QLabel 会把它缩小）。
        pixmap = pixmap.scaled(
            max(1, int(round(width * dpr))),
            max(1, int(round(height * dpr))),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def load_pixmap(
    path: Path | str, max_side: int, dpr: float = FALLBACK_DPR
) -> QPixmap | None:
    """读图并缩放到 ``max_side`` 逻辑像素以内，返回带 DPR 的 ``QPixmap``。

    **先**用 ``setScaledSize`` 让解码器按目标尺寸出图：整图解码一张 12MP 照片要
    92ms（48MP 约 350ms、峰值内存 ~190MB），而按目标解码只要 9ms（审计 AUD-35）。
    """
    reader = QImageReader(str(path))
    if not reader.canRead():
        return None
    physical_limit = physical_side(max_side, dpr)
    size = reader.size()
    pre_scaled = False
    if size.isValid() and max(size.width(), size.height()) > physical_limit:
        scaled = QSize(size)
        scaled.scale(physical_limit, physical_limit, Qt.AspectRatioMode.KeepAspectRatio)
        if scaled.isValid() and not scaled.isEmpty():
            reader.setScaledSize(scaled)
            pre_scaled = True
    image = reader.read()
    if image.isNull():
        return None
    pixmap = QPixmap.fromImage(image)
    if pixmap.isNull():
        return None
    if pre_scaled:
        # 已经在解码阶段缩到物理上限：不要再交给 `scale_pixmap`，
        # 否则会按 DPR 再放大一次（780 → 1170）。
        pixmap.setDevicePixelRatio(sanitize_dpr(dpr))
        return pixmap
    return scale_pixmap(pixmap, max_side, dpr)


def logical_size(pixmap: QPixmap) -> tuple[int, int]:
    """pixmap 在逻辑坐标里的显示尺寸。"""
    size = pixmap.deviceIndependentSize()
    return max(1, int(round(size.width()))), max(1, int(round(size.height())))


def is_animated(path: Path | str) -> bool:
    """``path`` 是否是多帧动图；读不出或只有一帧返回 False，不抛异常。

    Qt 的 GIF handler 对**单帧** GIF 也报 ``supportsAnimation() == True``，
    所以先看 ``imageCount()``：1 = 静态图（含单帧 GIF），>1 = 动图，读不出
    （-1）才退到 ``supportsAnimation()``。
    """
    try:
        reader = QImageReader(str(path))
        if not reader.canRead():
            return False
        count = int(reader.imageCount())
        if count > 1:
            return True
        if count == 1:
            return False
        return bool(reader.supportsAnimation())
    except (OSError, RuntimeError, ValueError):
        return False


def logical_size_for(path: Path | str, max_side: int) -> tuple[int, int] | None:
    """``path`` 按 ``max_side`` 逻辑像素等比缩放的显示尺寸；读不出返回 None。

    只读图片头里的原始尺寸，不解码像素（动图用它在建 ``QMovie`` 前定版面）。
    """
    try:
        reader = QImageReader(str(path))
        if not reader.canRead():
            return None
        size = reader.size()
    except (OSError, RuntimeError, ValueError):
        return None
    if not size.isValid() or size.isEmpty():
        return None
    limit = max(1, int(max_side))
    width, height = size.width(), size.height()
    if width > limit or height > limit:
        factor = min(limit / width, limit / height)
        width = min(limit, max(1, int(round(width * factor))))
        height = min(limit, max(1, int(round(height * factor))))
    return width, height
