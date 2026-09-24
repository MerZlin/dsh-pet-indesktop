# -*- coding: utf-8 -*-
"""零拷贝取帧通道 clip_current_image 回归（GUI 减负 Step1b）。

WebMClip 的 _process_frame 已持有 _current_image（Format_RGBA8888），窗口
重建不该再走 currentPixmap()→toImage() 绕一次全画布往返。本文件覆盖：

- clip_current_image 三分支：clip 有 currentImage() 且返回非空 QImage →
  直取该对象（零拷贝）；无该方法/返回空图 → 回退 currentPixmap().toImage()；
  两者皆空 → None；
- 像素一致性：带 alpha=0/128/255 边缘的 RGBA 测试图，旧链
  （QPixmap.fromImage→toImage）与新链（QImage 直取）经
  convertToFormat(ARGB32_Premultiplied)+Smooth 缩放后逐字节一致；
- GifClip 无 currentImage：回退路径行为不变（真实 QMovie 首帧）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QApplication

from pet.library import GifClip, clip_current_image

# 1x1 不透明黑 GIF89a（无 GCE 透明表）：QMovie 可确定性加载，不依赖外部素材。
_MIN_GIF = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b"


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _rgba_test_image(width: int = 9, height: int = 5) -> QImage:
    """RGBA8888 测试图：逐列轮换 alpha 0/128/255，制造半透明边缘与全透明像素。"""
    img = QImage(width, height, QImage.Format.Format_RGBA8888)
    for y in range(height):
        for x in range(width):
            img.setPixelColor(x, y, QColor(10 * x + 1, 20 * y + 3, 200 - 7 * x, [0, 128, 255][x % 3]))
    return img


class _ClipWithCurrentImage:
    """WebMClip 形状的替身：提供 currentImage() 零拷贝通道。"""

    def __init__(self, img, pm=None):
        self._img = img
        self._pm = pm

    def currentImage(self):
        return self._img

    def currentPixmap(self):
        return self._pm


class _ClipWithoutCurrentImage:
    """GifClip 形状的替身：只有 currentPixmap()（无 currentImage 能力）。"""

    def __init__(self, pm):
        self._pm = pm

    def currentPixmap(self):
        return self._pm


# ============================================================================
# 三分支
# ============================================================================


def test_current_image_preferred_zero_copy():
    """有 currentImage() 且返回非空 QImage：直取原对象，不做任何转换/拷贝。"""
    _qapp()
    img = _rgba_test_image()
    got = clip_current_image(_ClipWithCurrentImage(img))
    assert got is img
    assert got.format() == QImage.Format.Format_RGBA8888


def test_falls_back_to_pixmap_to_image():
    """无 currentImage 能力：回退 currentPixmap().toImage()（GifClip 路线）。"""
    _qapp()
    img = _rgba_test_image()
    pm = QPixmap.fromImage(img)
    got = clip_current_image(_ClipWithoutCurrentImage(pm))
    assert got is not None and not got.isNull()
    assert got.size() == pm.size()
    assert bytes(got.constBits()) == bytes(pm.toImage().constBits())


def test_null_current_image_falls_back_to_pixmap():
    """currentImage() 返回空图（无当前帧）：不直取空图，回退 pixmap 通道。"""
    _qapp()
    img = _rgba_test_image()
    pm = QPixmap.fromImage(img)
    got = clip_current_image(_ClipWithCurrentImage(QImage(), pm=pm))
    assert got is not None and not got.isNull()
    assert bytes(got.constBits()) == bytes(pm.toImage().constBits())


def test_none_when_no_frame():
    """两路皆空（首帧未就绪/素材损坏）：返回 None，调用方跳过本帧。"""
    _qapp()
    assert clip_current_image(_ClipWithoutCurrentImage(None)) is None
    assert clip_current_image(_ClipWithoutCurrentImage(QPixmap())) is None
    assert clip_current_image(_ClipWithCurrentImage(None)) is None
    assert clip_current_image(_ClipWithCurrentImage(QImage(), pm=None)) is None


# ============================================================================
# 像素一致性：旧链（QPixmap 往返）vs 新链（QImage 直取）
# ============================================================================


def test_new_chain_pixels_identical_to_pixmap_roundtrip():
    """两条链在预乘转换 + Smooth 缩放后必须逐字节一致（行为等价铁证）。"""
    _qapp()
    src = _rgba_test_image(9, 5)
    w_c, h_c = 4, 3
    # 旧链：QPixmap 往返后再预乘 + Smooth 缩放
    old = QPixmap.fromImage(src).toImage()
    old = old.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    old = old.scaled(w_c, h_c, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    # 新链：clip_current_image 直取的 QImage 直接预乘 + Smooth 缩放
    new = clip_current_image(_ClipWithCurrentImage(src))
    new = new.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    new = new.scaled(w_c, h_c, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    assert new.format() == QImage.Format.Format_ARGB32_Premultiplied
    assert new.size() == old.size()
    assert bytes(new.constBits()) == bytes(old.constBits())
    # 非平凡样本护栏：缩放后 alpha 仍有多档（确有半透明边缘参与插值）
    alphas = {new.pixelColor(x, y).alpha() for x in range(w_c) for y in range(h_c)}
    assert len(alphas) > 1


def test_mirror_then_scale_matches_old_chain_bytes():
    """镜像 + 预乘 + Smooth 缩放整段：两条链逐字节一致（window 重建实链）。"""
    _qapp()
    src = _rgba_test_image(9, 5)
    w_c, h_c = 6, 2
    old = QPixmap.fromImage(src).toImage().mirrored(True, False)
    old = old.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    old = old.scaled(w_c, h_c, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    new = clip_current_image(_ClipWithCurrentImage(src)).mirrored(True, False)
    new = new.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    new = new.scaled(w_c, h_c, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    assert bytes(new.constBits()) == bytes(old.constBits())


# ============================================================================
# GifClip：无 currentImage 时回退路径行为不变
# ============================================================================


def test_gif_clip_falls_back_without_current_image(tmp_path):
    """真实 GifClip 不提供 currentImage：仍能取到 QMovie 当前帧。"""
    app = _qapp()
    path = tmp_path / "dot.gif"
    path.write_bytes(_MIN_GIF)
    clip = GifClip(path)
    try:
        assert not hasattr(clip, "currentImage")  # 零拷贝通道只加在 WebMClip 上
        pm = clip.currentPixmap()
        assert pm is not None and not pm.isNull()
        got = clip_current_image(clip)
        assert got is not None and not got.isNull()
        assert got.size() == pm.size()
        assert bytes(got.constBits()) == bytes(pm.toImage().constBits())
        assert got.pixelColor(0, 0).getRgb() == (0, 0, 0, 255)
        app.processEvents()
    finally:
        clip.stop()
