# -*- coding: utf-8 -*-
"""窗口渲染 / 角色区域 / Windows 逐像素命中测试。"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QBitmap, QColor, QImage, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QApplication

from pet import window as window_mod
from pet import catalog


def _qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _alpha_pixmap(width: int = 64, height: int = 36) -> QPixmap:
    """左半不透明、右半透明的合成帧。"""
    pm = QPixmap(width, height)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.fillRect(0, 0, width // 2, height, QColor(255, 255, 255, 255))
    p.end()
    return pm


class _FakePet:
    """最小 fake 对象，只包含渲染相关方法所需的属性。"""

    _frame_draw_rect = window_mod.PetWindow._frame_draw_rect
    _sync_mask = window_mod.PetWindow._sync_mask
    _is_transparent_at = window_mod.PetWindow._is_transparent_at
    character_local_region = window_mod.PetWindow.character_local_region

    def __init__(self, scale: float = 0.1) -> None:
        self.scale = scale
        self._w = max(1, int(round(catalog.CANVAS_W * scale)))
        self._h = max(1, int(round((catalog.CANVAS_H + catalog.PAD) * scale)))
        self._squash_active = False
        self._squash_progress = 1.0
        self._frame_pixmap = None
        self._mask_bounds = None
        self._collision_local_bounds = None
        self._hit_alpha_image = None


def _fake_pet(scale: float = 0.1) -> _FakePet:
    return _FakePet(scale)


def test_sync_mask_updates_bounds_and_respects_platform(monkeypatch):
    _qapp()
    fake = _fake_pet()
    fake._frame_pixmap = _alpha_pixmap()
    mask_sets = []

    def set_mask(mask):
        mask_sets.append(mask)

    fake.setMask = set_mask

    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="posix"))
    window_mod.PetWindow._sync_mask(fake)
    assert fake._mask_bounds is not None
    assert not fake._mask_bounds.isEmpty()
    assert mask_sets, "非 Windows 仍应使用 setMask"

    # Windows 路径：不再 setMask；已有旧 mask 时清理一次
    mask_sets.clear()
    clear_calls = []
    fake.clearMask = lambda: clear_calls.append(True)
    fake.mask = lambda: QRegion(0, 0, 10, 10)
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))
    window_mod.PetWindow._sync_mask(fake)
    assert not mask_sets, "Windows 不应再调用 setMask"
    assert clear_calls, "Windows 上存在旧 mask 时应 clearMask"


def test_is_transparent_at_uses_frame_alpha():
    _qapp()
    fake = _fake_pet(scale=0.1)
    fake._frame_pixmap = _alpha_pixmap()
    fake._hit_alpha_image = None

    assert window_mod.PetWindow._is_transparent_at(fake, QPoint(10, 10)) is False
    assert window_mod.PetWindow._is_transparent_at(fake, QPoint(50, 10)) is True
    assert window_mod.PetWindow._is_transparent_at(fake, QPoint(10, 2)) is True


def test_input_controller_survives_init():
    """回归：控制器必须由窗口持有，保证轮询持续运行。"""
    _qapp()
    import sys
    if sys.platform != "win32":
        pytest.skip("逐像素命中测试仅 Windows")

    import tempfile
    from pet.config import Config
    from pet.library import MovieLibrary
    lib = MovieLibrary(character_id='shenshen')
    win = window_mod.PetWindow(lib, Config(base=Path(tempfile.mkdtemp())))
    assert win._input_controller is not None
    win.close()


def test_input_controller_returns_pixel_hit_result():
    _qapp()

    class FakeWindow:
        def winId(self):
            return 123

        def mapFromGlobal(self, point):
            return point

        def _is_transparent_at(self, point):
            return point.x() >= 50

    fake = FakeWindow()
    fake.mouse_through = False
    fake._press_global = None
    fake.isVisible = lambda: True
    fake.width = lambda: 100
    fake.height = lambda: 80
    controller = object.__new__(window_mod.WindowsPerPixelInputController)
    controller._window = fake
    assert controller.should_click_through(QPoint(20, 20)) is False
    assert controller.should_click_through(QPoint(70, 20)) is True
    fake._press_global = QPoint(70, 20)
    assert controller.should_click_through(QPoint(70, 20)) is False


def test_windows_click_through_preserves_other_extended_styles():
    class FakeUser32:
        def __init__(self):
            self.style = 0x00080080  # WS_EX_LAYERED | WS_EX_TOOLWINDOW
            self.writes = []

        def GetWindowLongW(self, hwnd, index):
            assert (hwnd, index) == (123, window_mod.GWL_EXSTYLE)
            return self.style

        def SetWindowLongW(self, hwnd, index, style):
            self.style = style
            self.writes.append((hwnd, index, style))

    user32 = FakeUser32()
    assert window_mod._set_windows_click_through(123, True, user32) is True
    assert user32.style == 0x000800A0
    assert window_mod._set_windows_click_through(123, True, user32) is False
    assert len(user32.writes) == 1
    assert window_mod._set_windows_click_through(123, False, user32) is True
    assert user32.style == 0x00080080


def test_visible_content_rect_uses_character_region():
    _qapp()

    class FakeWithGeometry(_FakePet):
        def frameGeometry(self):
            return QRect(100, 100, self._w, self._h)

        def mask(self):
            return QRegion()

    obj = FakeWithGeometry()
    obj._mask_bounds = QRect(4, 3, 20, 20)

    rect = window_mod.PetWindow.visible_content_rect(obj)
    assert rect == QRect(104, 103, 20, 20)


class _FakeBubble:
    def __init__(self) -> None:
        self.hidden = False
        self.shown_text = []

    def hide(self) -> None:
        self.hidden = True

    def show_text(self, text, *args, **kwargs) -> None:
        self.shown_text.append(text)

    def show_image(self, *args, **kwargs) -> None:
        self.shown_text.append("<image>")


class _FakeBubblePet:
    def __init__(self) -> None:
        self._bubble_suppressed = False
        self._speech_bubble = _FakeBubble()
        self.scale = 1.0
        self._self_talk_texts = ["你好"]
        self._self_talk_images = []

    def isVisible(self) -> bool:  # noqa: N802 - Qt 命名
        return True

    def hold_bubble(self, seconds: float) -> None:
        pass

    def visible_content_rect(self) -> QRect:
        return QRect(0, 0, 100, 100)


def test_bubble_suppression_skips_bubbles_while_settings_open():
    _qapp()
    pet = _FakeBubblePet()

    window_mod.PetWindow.set_bubble_suppressed(pet, True)
    assert pet._bubble_suppressed is True
    assert pet._speech_bubble.hidden is True

    window_mod.PetWindow.show_bubble(pet, "不应显示")
    assert pet._speech_bubble.shown_text == []

    assert window_mod.PetWindow._show_random_self_talk(pet) is False
    assert pet._speech_bubble.shown_text == []

    window_mod.PetWindow.set_bubble_suppressed(pet, False)
    window_mod.PetWindow.show_bubble(pet, "恢复显示")
    assert pet._speech_bubble.shown_text == ["恢复显示"]


# ================================================================ B5：每帧重建降本
# _rebuild_frame 缓存缩放后 ARGB32 图 / 同帧跳过；Windows 的 _mask_bounds 从
# 「实际绘制用画布」算起：drawPixmap(rect, frame_pixmap) → createAlphaMask →
# 直接扫描 1bpp 掩码得包围盒（窗口裁剪/阈值/DPR/采样语义与旧路径天然一致）。
# 铁律：mask bounds 与旧 QRegion 路径逐像素一致（旧算法为基准）。


def _make_frame_image(width: int, height: int, opaque_rects) -> QImage:
    """构造 ARGB32 帧图：指定区域画纯白不透明矩形，其余透明。"""
    img = QImage(width, height, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    for (x0, y0, x1, y1) in opaque_rects:
        p.fillRect(x0, y0, x1 - x0, y1 - y0, QColor(255, 255, 255, 255))
    p.end()
    return img


def _reference_mask_bounds(pm, w: int, h: int, rect: QRect) -> QRect:
    """旧实现（canvas→createAlphaMask→QBitmap→QRegion）作为逐像素基准。"""
    canvas = QImage(w, h, QImage.Format.Format_ARGB32)
    canvas.fill(Qt.GlobalColor.transparent)
    p = QPainter(canvas)
    p.drawPixmap(rect, pm)
    p.end()
    mask = QBitmap.fromImage(canvas.createAlphaMask())
    return QRegion(mask).boundingRect()


def _install_nt_mask_stubs(fake):
    """给假窗口装上 Windows 分支需要的 setMask/mask/clearMask 桩。"""
    mask_sets, clear_calls = [], []
    fake.setMask = lambda m: mask_sets.append(m)
    fake.mask = lambda: QRegion(0, 0, 10, 10)
    fake.clearMask = lambda: clear_calls.append(True)
    return mask_sets, clear_calls


def test_windows_mask_bounds_from_frame_alpha_matches_reference(monkeypatch):
    """Windows：_mask_bounds 从实际绘制画布（drawPixmap → createAlphaMask）
    扫描得到，结果与旧 QRegion 路径逐像素一致；不 setMask，旧 mask 仍清理。"""
    _qapp()
    fake = _FakePet(scale=0.5)
    img = _make_frame_image(320, 180, [(60, 30, 260, 150)])
    pm = QPixmap.fromImage(img)
    fake._frame_pixmap = pm
    fake._hit_alpha_image = img
    mask_sets, clear_calls = _install_nt_mask_stubs(fake)
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))

    window_mod.PetWindow._sync_mask(fake)
    rect = window_mod.PetWindow._frame_draw_rect(fake)
    expected = _reference_mask_bounds(pm, fake._w, fake._h, rect)
    assert not expected.isEmpty()
    assert fake._mask_bounds == expected
    assert not mask_sets, "Windows 不应再调用 setMask"
    assert clear_calls, "Windows 上存在旧 mask 时应 clearMask"


def test_windows_mask_bounds_dpr_scaling_matches_reference(monkeypatch):
    """DPR=2：物理帧图按 1:1 逻辑绘制，画布扫描 bounds 与旧 QRegion 路径一致。"""
    _qapp()
    fake = _FakePet(scale=0.5)
    img = _make_frame_image(640, 360, [(120, 60, 520, 300)])  # 物理像素
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(2.0)
    fake._frame_pixmap = pm
    fake._hit_alpha_image = img
    _install_nt_mask_stubs(fake)
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))

    window_mod.PetWindow._sync_mask(fake)
    rect = window_mod.PetWindow._frame_draw_rect(fake)
    expected = _reference_mask_bounds(pm, fake._w, fake._h, rect)
    assert not expected.isEmpty()
    assert fake._mask_bounds == expected


def test_windows_mask_bounds_follows_squash_geometry(monkeypatch):
    """Q 弹变形中：bounds 跟随 squash 绘制矩形（峰值时越出窗口），
    画布扫描结果仍与旧 QRegion 路径一致。"""
    _qapp()
    fake = _FakePet(scale=0.5)
    img = _make_frame_image(320, 180, [(60, 30, 260, 150)])
    pm = QPixmap.fromImage(img)
    fake._frame_pixmap = pm
    fake._hit_alpha_image = img
    fake._squash_active = True
    fake._squash_progress = 0.3
    _install_nt_mask_stubs(fake)
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))

    window_mod.PetWindow._sync_mask(fake)
    rect = window_mod.PetWindow._frame_draw_rect(fake)
    expected = _reference_mask_bounds(pm, fake._w, fake._h, rect)
    assert not expected.isEmpty()
    assert fake._mask_bounds == expected
    assert fake._collision_local_bounds is not None


def test_windows_mask_bounds_semi_transparent_edge_threshold(monkeypatch):
    """半透明边缘阈值：alpha=128 进 mask bounds、alpha=127 不进
    （createAlphaMask 默认阈值，与旧 QRegion 路径一致）。"""
    _qapp()
    fake = _FakePet(scale=0.5)
    img = QImage(320, 180, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.fillRect(0, 0, 60, 180, QColor(255, 255, 255, 255))   # x 0..59
    p.fillRect(60, 0, 1, 180, QColor(255, 255, 255, 127))   # x 60：阈值下
    p.fillRect(61, 0, 1, 180, QColor(255, 255, 255, 128))   # x 61：阈值上
    p.end()
    pm = QPixmap.fromImage(img)
    fake._frame_pixmap = pm
    fake._hit_alpha_image = img
    _install_nt_mask_stubs(fake)
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))

    window_mod.PetWindow._sync_mask(fake)
    rect = window_mod.PetWindow._frame_draw_rect(fake)
    expected = _reference_mask_bounds(pm, fake._w, fake._h, rect)
    assert fake._mask_bounds == expected
    assert fake._mask_bounds.right() == rect.x() + 61, "alpha=128 的列必须纳入"
    assert fake._mask_bounds.left() == rect.x()


def test_windows_collision_local_bounds_union_stable(monkeypatch):
    """碰撞 _collision_local_bounds 语义不变：各帧 bounds 的并集只增不减，
    重置后从当前帧重新累积。"""
    _qapp()
    fake = _FakePet(scale=0.5)
    img = _make_frame_image(320, 180, [(60, 30, 160, 150)])
    fake._frame_pixmap = QPixmap.fromImage(img)
    fake._hit_alpha_image = img
    _install_nt_mask_stubs(fake)
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))

    window_mod.PetWindow._sync_mask(fake)
    first = QRect(fake._mask_bounds)
    assert fake._collision_local_bounds == first

    # 第二帧范围更大 → 并集只增不减
    img2 = _make_frame_image(320, 180, [(60, 30, 260, 150)])
    fake._frame_pixmap = QPixmap.fromImage(img2)
    fake._hit_alpha_image = img2
    window_mod.PetWindow._sync_mask(fake)
    assert fake._collision_local_bounds.contains(first)
    assert fake._collision_local_bounds.contains(fake._mask_bounds)
    assert fake._collision_local_bounds.united(first) == fake._collision_local_bounds

    # 切换动画/缩放会重置 → 从当前帧重新累积
    fake._collision_local_bounds = None
    window_mod.PetWindow._sync_mask(fake)
    assert fake._collision_local_bounds == fake._mask_bounds


def _windows_sync_and_reference(fake, monkeypatch):
    """Windows 分支跑 _sync_mask，返回（新 bounds, 旧 QRegion 基准, 绘制矩形）。"""
    _install_nt_mask_stubs(fake)
    monkeypatch.setattr(window_mod, "os", SimpleNamespace(name="nt"))
    window_mod.PetWindow._sync_mask(fake)
    # 用实例方法取矩形：_ClippingPet 可能覆盖了 _frame_draw_rect（越界几何）
    rect = fake._frame_draw_rect()
    old = _reference_mask_bounds(fake._frame_pixmap, fake._w, fake._h, rect)
    return fake._mask_bounds, old, rect


class _ClippingPet(_FakePet):
    """可强制任意绘制矩形（含越界）的假窗口，用于验证窗口边界裁剪语义。"""

    def __init__(self, draw_rect: QRect, **kwargs):
        super().__init__(**kwargs)
        self._forced_draw_rect = QRect(draw_rect)

    def _frame_draw_rect(self):
        return QRect(self._forced_draw_rect)


def test_windows_mask_bounds_clips_overflow_on_all_sides(monkeypatch):
    """squash 放大把绘制矩形推出窗口时：新算法与旧 QRegion 路径一致地裁剪到
    窗口边界（左/右/上/下分别验证）；完全在窗口外的矩形两边都为空。"""
    _qapp()
    cases = [
        # (强制绘制矩形, 期望非空)
        (QRect(-40, 15, 320, 180), True),   # 左越界
        (QRect(40, 15, 320, 180), True),    # 右越界
        (QRect(0, -25, 320, 180), True),    # 上越界
        (QRect(0, 40, 320, 180), True),     # 下越界
        (QRect(400, 0, 320, 180), False),   # 完全在窗口外
    ]
    for rect, expect_nonempty in cases:
        fake = _ClippingPet(rect, scale=0.5)
        img = _make_frame_image(320, 180, [(0, 0, 320, 180)])  # 整幅不透明
        fake._frame_pixmap = QPixmap.fromImage(img)
        fake._hit_alpha_image = img
        new, old, _ = _windows_sync_and_reference(fake, monkeypatch)
        assert new == old, f"rect={rect}: 新={new} 旧={old}"
        assert new.isEmpty() is not expect_nonempty
        if expect_nonempty:
            # 裁剪语义：bounds 必须完全落在窗口内（0<=x<w, 0<=y<h）
            assert new.left() >= 0 and new.right() < fake._w
            assert new.top() >= 0 and new.bottom() < fake._h
            # 越界矩形本身必须真的越界，否则本测试没有覆盖裁剪
            assert rect.left() < 0 or rect.right() >= fake._w \
                or rect.top() < 0 or rect.bottom() >= fake._h


@pytest.mark.parametrize(
    "rects, expect_nonempty",
    [
        ([(0, 0, 1, 1)], True),                          # 左上单像素
        ([(319, 179, 320, 180)], True),                  # 右下单像素
        ([(160, 0, 161, 180)], True),                    # 单列
        ([(0, 90, 320, 91)], True),                      # 单行
        ([(0, 0, 1, 1), (319, 179, 320, 180)], True),    # 两个远距小块
        ([(0, 0, 1, 1), (319, 0, 320, 1),
          (0, 179, 1, 180), (319, 179, 320, 180)], True),  # 仅四角
        ([(0, 90, 120, 91), (200, 90, 320, 91)], True),  # 中间断裂的两段
        ([(0, 0, 320, 180)], True),                      # 整幅不透明
        ([], False),                                     # 全透明
    ],
)
def test_windows_mask_bounds_sparse_broken_alpha(rects, expect_nonempty, monkeypatch):
    """稀疏/断裂 alpha 轮廓：单像素、单行/列、远距小块、四角、断列、全透明。

    新算法与旧 QRegion 基准必须逐例一致（不只是连续矩形样例）。
    """
    _qapp()
    fake = _FakePet(scale=0.5)
    img = _make_frame_image(320, 180, rects)
    fake._frame_pixmap = QPixmap.fromImage(img)
    fake._hit_alpha_image = img
    new, old, _ = _windows_sync_and_reference(fake, monkeypatch)
    assert new == old
    assert new.isEmpty() is not expect_nonempty


@pytest.mark.parametrize("dpr", [1.0, 1.25, 1.5, 2.0, 3.0])
def test_windows_mask_bounds_dpr_sweep(dpr, monkeypatch):
    """DPR 多档（1/1.25/1.5/2/3）+ squash 越界 + 整幅不透明（含边缘 alpha）。

    旧 QRegion 基准与新画布扫描路径必须在每一档 DPR 下一致。
    """
    _qapp()
    fake = _FakePet(scale=0.5)
    img = _make_frame_image(640, 360, [(0, 0, 640, 360)])  # 物理像素整幅不透明
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    fake._frame_pixmap = pm
    fake._hit_alpha_image = img
    fake._squash_active = True
    fake._squash_progress = 0.5  # 峰值：绘制矩形水平越出窗口
    new, old, rect = _windows_sync_and_reference(fake, monkeypatch)
    assert new == old
    assert rect.left() < 0 or rect.right() >= fake._w, "squash 峰值必须越界"


def test_create_alpha_mask_threshold_boundary():
    """createAlphaMask 默认阈值（B5 铁律）：alpha=127 不进 mask、128 进。

    直接核对 Qt 默认行为（_mono_mask_bounds 内部即 createAlphaMask），
    把「127/128 边界」变成机器验证而非注释假设。
    """
    _qapp()
    low = QImage(2, 1, QImage.Format.Format_ARGB32)
    low.fill(Qt.GlobalColor.transparent)
    low.setPixelColor(0, 0, QColor(255, 255, 255, 127))
    low.setPixelColor(1, 0, QColor(255, 255, 255, 127))
    assert window_mod._mono_mask_bounds(low) is None  # 全 127：不进 mask

    high = QImage(2, 1, QImage.Format.Format_ARGB32)
    high.fill(Qt.GlobalColor.transparent)
    high.setPixelColor(0, 0, QColor(255, 255, 255, 128))
    high.setPixelColor(1, 0, QColor(255, 255, 255, 128))
    assert window_mod._mono_mask_bounds(high) == (0, 0, 1, 0)  # 全 128：进 mask

    mixed = QImage(2, 1, QImage.Format.Format_ARGB32)
    mixed.fill(Qt.GlobalColor.transparent)
    mixed.setPixelColor(0, 0, QColor(255, 255, 255, 127))
    mixed.setPixelColor(1, 0, QColor(255, 255, 255, 128))
    assert window_mod._mono_mask_bounds(mixed) == (1, 0, 1, 0)  # 只有 128 列进


class _RebuildClip:
    """记录 currentPixmap 调用次数的假播放器（帧号可控）。"""

    def __init__(self, pixmap, frame_number: int = 0):
        self._pm = pixmap
        self._frame_number = frame_number
        self.pixmap_requests = 0

    def currentPixmap(self):
        self.pixmap_requests += 1
        return self._pm

    def currentFrameNumber(self):
        return self._frame_number

    def jumpToFrame(self, n):
        self._frame_number = max(0, n)
        return n <= 0

    def stop(self):
        pass

    def start(self):
        pass

    def set_playback_speed(self, speed):
        pass

    def frameCount(self):
        return 1

    def duration(self):
        return 1.0

    def currentTimeSeconds(self):
        return 0.0


class _RebuildLibrary:
    def __init__(self, clips, no_mirror=frozenset()):
        self._clips = dict(clips)
        self.no_mirror = set(no_mirror)
        self.manifest = {}
        self.folder_map = {}
        self.folder_files = None

    def names(self):
        return list(self._clips)

    def movies(self):
        return dict(self._clips)

    def movie(self, name):
        return self._clips[name]

    def frames(self, name):
        return 1

    def duration(self, name):
        return 1.0


class _RebuildPet:
    """只挂载 _rebuild_frame / _is_transparent_at 的假窗口。"""

    _rebuild_frame = window_mod.PetWindow._rebuild_frame
    _is_transparent_at = window_mod.PetWindow._is_transparent_at

    def __init__(self, movie, lib, facing="left", scale=0.5, anim="idle"):
        self.movie = movie
        self.lib = lib
        self.facing = facing
        self.scale = scale
        self.anim = anim
        self._w = max(1, int(round(catalog.CANVAS_W * scale)))
        self._h = max(1, int(round((catalog.CANVAS_H + catalog.PAD) * scale)))
        self._frame_pixmap = None
        self._hit_alpha_image = None
        self._mask_bounds = None
        self._collision_local_bounds = None
        self._frame_key = None
        self._sync_mask_calls = 0
        self._screen_dpr = 1.0

    def _screen_available(self):
        return SimpleNamespace(devicePixelRatio=lambda: self._screen_dpr)

    def _frame_draw_rect(self):
        return QRect(0, int(round(catalog.PAD * self.scale)),
                     int(round(catalog.CANVAS_W * self.scale)),
                     int(round(catalog.CANVAS_H * self.scale)))

    def _sync_mask(self):
        self._sync_mask_calls += 1


def _solid_frame_pixmap():
    """640x360：左上 100x100 纯红方块，其余透明（非对称，可检验镜像）。"""
    img = QImage(640, 360, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.fillRect(0, 0, 100, 100, QColor(255, 0, 0, 255))
    p.end()
    return QPixmap.fromImage(img)


def test_rebuild_frame_text_animation_not_mirrored_when_facing_right():
    """文字动画（no_mirror）朝右时不得镜像：红块仍在画面左侧。"""
    _qapp()
    lib = _RebuildLibrary({"talk": _RebuildClip(_solid_frame_pixmap())},
                          no_mirror={"talk"})
    pet = _RebuildPet(lib.movie("talk"), lib, facing="right", anim="talk", scale=0.5)
    window_mod.PetWindow._rebuild_frame(pet)
    out = pet._frame_pixmap.toImage()
    assert out.pixelColor(20, 20).red() > 200, "no_mirror 动画朝右不得镜像"
    assert out.pixelColor(300, 20).red() < 50


def test_rebuild_frame_mirrors_regular_animation_when_facing_right():
    """普通动画朝右时水平镜像：红块出现在画面右侧。"""
    _qapp()
    lib = _RebuildLibrary({"walk": _RebuildClip(_solid_frame_pixmap())})
    pet = _RebuildPet(lib.movie("walk"), lib, facing="right", anim="walk", scale=0.5)
    window_mod.PetWindow._rebuild_frame(pet)
    out = pet._frame_pixmap.toImage()
    assert out.pixelColor(300, 20).red() > 200, "普通动画朝右必须镜像"
    assert out.pixelColor(20, 20).red() < 50


def test_rebuild_frame_left_facing_never_mirrors():
    """朝左不镜像（含文字动画与普通动画）。"""
    _qapp()
    lib = _RebuildLibrary({"walk": _RebuildClip(_solid_frame_pixmap())})
    pet = _RebuildPet(lib.movie("walk"), lib, facing="left", anim="walk", scale=0.5)
    window_mod.PetWindow._rebuild_frame(pet)
    out = pet._frame_pixmap.toImage()
    assert out.pixelColor(20, 20).red() > 200
    assert out.pixelColor(300, 20).red() < 50


def test_rebuild_frame_skips_when_same_movie_frame_unchanged():
    """同一 movie 同一帧号重复 rebuild：整条重建链跳过（currentPixmap/_sync_mask 不再触发）。"""
    _qapp()
    clip = _RebuildClip(_solid_frame_pixmap(), frame_number=0)
    lib = _RebuildLibrary({"idle": clip})
    pet = _RebuildPet(clip, lib, anim="idle", scale=0.5)
    window_mod.PetWindow._rebuild_frame(pet)
    assert clip.pixmap_requests == 1
    assert pet._sync_mask_calls == 1

    # 同帧重复 → 跳过
    window_mod.PetWindow._rebuild_frame(pet)
    assert clip.pixmap_requests == 1
    assert pet._sync_mask_calls == 1

    # 帧号推进 → 重建
    clip._frame_number = 1
    window_mod.PetWindow._rebuild_frame(pet)
    assert clip.pixmap_requests == 2
    assert pet._sync_mask_calls == 2

    # 朝向变化 → 重建
    pet.facing = "right"
    window_mod.PetWindow._rebuild_frame(pet)
    assert clip.pixmap_requests == 3

    # 缩放变化 → 重建
    pet.scale = 0.8
    window_mod.PetWindow._rebuild_frame(pet)
    assert clip.pixmap_requests == 4

    # DPR 变化 → 重建
    pet._screen_dpr = 2.0
    window_mod.PetWindow._rebuild_frame(pet)
    assert clip.pixmap_requests == 5


def test_rebuild_frame_caches_scaled_image_for_hit_testing():
    """_rebuild_frame 直接把缩放后的 ARGB32 图缓存为 _hit_alpha_image，
    命中测试不再调用 QPixmap.toImage。"""
    _qapp()
    clip = _RebuildClip(_solid_frame_pixmap(), frame_number=0)
    lib = _RebuildLibrary({"idle": clip})
    pet = _RebuildPet(clip, lib, anim="idle", scale=0.5)
    window_mod.PetWindow._rebuild_frame(pet)
    assert pet._hit_alpha_image is not None
    assert pet._hit_alpha_image.width() == round(catalog.CANVAS_W * 0.5)
    assert pet._hit_alpha_image.height() == round(catalog.CANVAS_H * 0.5)

    # 换成不可 toImage 的 pixmap：命中测试必须走 _hit_alpha_image 缓存
    class _NoToImagePixmap:
        def isNull(self):
            return False

        def devicePixelRatio(self):
            return 1.0

    pet._frame_pixmap = _NoToImagePixmap()
    assert window_mod.PetWindow._is_transparent_at(pet, QPoint(20, 20)) is False
    assert window_mod.PetWindow._is_transparent_at(pet, QPoint(300, 20)) is True


def test_is_transparent_at_dpr_mapping_with_cached_image():
    """DPR=2：命中测试把逻辑坐标按 dpr 映射到物理像素（半透明边缘命中）。"""
    _qapp()
    fake = _FakePet(scale=0.5)
    img = QImage(640, 360, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.fillRect(0, 0, 320, 360, QColor(255, 255, 255, 200))   # 左半 alpha 200
    p.fillRect(320, 0, 320, 360, QColor(255, 255, 255, 8))    # 右半 alpha 8（<16 视为透明）
    p.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(2.0)
    fake._frame_pixmap = pm
    fake._hit_alpha_image = img

    assert window_mod.PetWindow._is_transparent_at(fake, QPoint(10, 20)) is False
    assert window_mod.PetWindow._is_transparent_at(fake, QPoint(159, 20)) is False
    assert window_mod.PetWindow._is_transparent_at(fake, QPoint(160, 20)) is True
    assert window_mod.PetWindow._is_transparent_at(fake, QPoint(200, 20)) is True
