# -*- coding: utf-8 -*-
"""Phase 4 D2 HiDPI 渲染归一化 offscreen 单测（PHASE4_DESIGN.md §4 D2 / §11 v1.2）。

口径：DPR 只影响渲染采样与命中图生成——pixmap 按 CANVAS*scale*dpr 物理像素
生成并 setDevicePixelRatio(dpr)，绘制逻辑尺寸不变；pos/rect/body_box 钳制/
碰撞/命中输入全部留在每屏逻辑坐标（v1.2 裁决），DPR 不得渗进坐标系。

覆盖：
- DPR 扫描 1.0/1.25/1.5/2.0：物理尺寸 = 逻辑尺寸×DPR、携带 DPR、
  device-independent 尺寸恒等于逻辑 rect、命中图与 pixmap 同物理尺寸；
- 逻辑坐标口径不变：DPR 1↔2 下 pos/rect/body_rect/钳制/velocity 积分逐位一致；
- DPR≠1 命中正确：alpha_at 输入逻辑坐标、覆盖全幅逻辑矩形、越界返回 0、镜像翻转；
- DPR 变化 → 旧 pixmap 与旧命中图立即重建 + 报脏（不许命中错缓存）；
- 重建失败（首帧未就绪）不提前记账：sprite 侧等价物 = _frame_sig 只在成功后写入；
- scale setter 与帧重建路径同步（scale 进签名，命中图不残留旧尺寸）；
- overlay 侧能力断言（对照 tests/test_window_dpr_signals.py 改写的 sprite 等价物）：
  add_sprite/showEvent/geometryChanged 喂 DPR；DPI 信号静止也重喂**全部** sprite；
  screenChanged 换屏重挂信号并按新 DPR 重建；showEvent 接线 / 摘线后不再触发。

纪律：全 offscreen；假屏用 QObject + Signal 直发，不赌时序、不固定 sleep。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, QRect, Qt, Signal
from PySide6.QtGui import (
    QColor, QGuiApplication, QImage, QPainter, QPixmap, QShowEvent)
from PySide6.QtWidgets import QApplication

from pet import catalog
from pet.overlay_window import OverlayWindow
from pet.pet_sprite import PetSprite

app = QApplication.instance() or QApplication([])

DPRS = [1.0, 1.25, 1.5, 2.0]
SCALE = 0.5
LOGICAL_W = int(round(catalog.CANVAS_W * SCALE))   # 320
LOGICAL_H = int(round(catalog.CANVAS_H * SCALE))   # 180
HALF_SRC_X = catalog.CANVAS_W // 2                 # 源像素对半 = 逻辑半幅


# ---------------------------------------------------------------- 假件
class FakeClip(QObject):
    """接口对齐 WebMClip 的假 clip：frameChanged 信号 + 可置空的当前帧。

    currentImage() 返回 None（首帧未就绪）+ currentPixmap() 返回 null QPixmap，
    让 library.clip_current_image 的空帧语义可复现（重建失败路径）。
    """

    frameChanged = Signal(int)

    def __init__(self, image):
        super().__init__()
        self._image = image
        self.frame = 0
        self.started = False

    def currentFrameNumber(self):
        return self.frame

    def currentImage(self):
        return self._image

    def currentPixmap(self):
        return QPixmap()

    def frameCount(self):
        return 1

    def start(self):
        self.started = True
        return True

    def stop(self):
        self.started = False

    def set_playback_speed(self, speed):
        pass


class FakeLibrary:
    def __init__(self, clip, character_id="fake_char"):
        self._clip = clip
        self.no_mirror: set[str] = set()
        self.character_id = character_id

    def movie(self, name, **_kw):
        return self._clip


class CountingPetSprite(PetSprite):
    """统计实际重建次数（签名快路径不得被绕过，也不得该重建却不重建）。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.build_count = 0

    def _rebuild_pixmap(self):
        rebuilt = super()._rebuild_pixmap()
        if rebuilt:
            self.build_count += 1
        return rebuilt


class FakeScreen(QObject):
    """可编程假屏：DPI/几何信号 + DPR/几何/刷新率（真实 QScreen 不可新建）。"""

    logicalDotsPerInchChanged = Signal(float)
    physicalDotsPerInchChanged = Signal(float)
    geometryChanged = Signal(QRect)
    availableGeometryChanged = Signal(QRect)

    def __init__(self, geo=(0, 0, 1920, 1080), *, dpr=1.0, refresh=60.0):
        super().__init__()
        self._geo = QRect(*geo)
        self._dpr = float(dpr)
        self._refresh = float(refresh)

    def set_geometry(self, geo):
        self._geo = QRect(*geo)

    def set_dpr(self, dpr):
        self._dpr = float(dpr)

    def geometry(self):
        return QRect(self._geo)

    def availableGeometry(self):
        return QRect(self._geo)

    def devicePixelRatio(self):
        return self._dpr

    def refreshRate(self):
        return self._refresh


def _block_frame(x0=0, x1=HALF_SRC_X):
    """640×360 假帧：源像素 x∈[x0, x1) 不透明，其余透明（非对称，可验镜像）。

    半幅块 [0, 320) 在全部被测 DPR 下都落在整数物理边界上（640→400/480/640
    的缩放系数 0.625/0.75/1.0），边缘无平滑过渡，命中探针可以贴边取样。
    """
    img = QImage(catalog.CANVAS_W, catalog.CANVAS_H, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    painter = QPainter(img)
    painter.fillRect(x0, 0, x1 - x0, catalog.CANVAS_H, QColor(0, 255, 0, 255))
    painter.end()
    return img


def _opaque_frame():
    return _block_frame(0, catalog.CANVAS_W)


def _make_sprite(*, dpr=1.0, scale=SCALE, clip=None, pos=(0, 0), character_id="fake_char"):
    """建 sprite 并走真实 DPR 喂入路径：先 bind_clip 再 set_dpr（立即重建）。"""
    clip = clip if clip is not None else FakeClip(_block_frame())
    sprite = CountingPetSprite(FakeLibrary(clip, character_id),
                               pos=QPointF(*pos), scale=scale)
    sprite.bind_clip("fake")
    if dpr != 1.0:
        sprite.set_dpr(dpr)
    sprite._rebuild_pixmap()   # dpr==1.0 时 set_dpr 不触发重建：补首帧
    return sprite


# ================================================================ D2：渲染像素随 DPR
@pytest.mark.parametrize("dpr", DPRS)
def test_pixmap_physical_size_and_device_pixel_ratio(dpr):
    """物理像素 = 逻辑尺寸×DPR；pixmap 携带 DPR；绘制逻辑尺寸不变。"""
    sprite = _make_sprite(dpr=dpr)

    pm = sprite._pixmap
    assert pm is not None
    assert (pm.width(), pm.height()) == (
        round(catalog.CANVAS_W * SCALE * dpr),
        round(catalog.CANVAS_H * SCALE * dpr),
    )
    assert pm.devicePixelRatio() == dpr
    # 归一化：DPR 变了但绘制逻辑尺寸不变（Qt 按 device-independent 尺寸绘制）
    assert round(pm.width() / pm.devicePixelRatio()) == LOGICAL_W
    assert round(pm.height() / pm.devicePixelRatio()) == LOGICAL_H
    assert sprite.rect() == QRect(0, 0, LOGICAL_W, LOGICAL_H)
    # 命中图与 pixmap 同物理尺寸（同一份缩放后预乘图）
    assert (sprite._hit_image.width(), sprite._hit_image.height()) == (pm.width(), pm.height())


@pytest.mark.parametrize("dpr", DPRS)
def test_alpha_hit_uses_logical_coords_at_dpr(dpr):
    """命中输入恒为逻辑坐标：左半命中/右半穿透/越界 0，镜像后翻转。"""
    sprite = _make_sprite(dpr=dpr)
    mid_y = LOGICAL_H // 2

    assert sprite.alpha_at(QPoint(0, 0)) == 255
    assert sprite.alpha_at(QPoint(5, mid_y)) == 255
    assert sprite.alpha_at(QPoint(HALF_SRC_X // 2 - 1, mid_y)) == 255   # 逻辑末列仍命中
    assert sprite.alpha_at(QPoint(HALF_SRC_X // 2, mid_y)) == 0         # 对半边界起透明
    assert sprite.alpha_at(QPoint(LOGICAL_W - 1, LOGICAL_H - 1)) == 0   # 全幅逻辑右上角
    assert sprite.alpha_at(QPoint(LOGICAL_W, mid_y)) == 0               # 越界右
    assert sprite.alpha_at(QPoint(-1, mid_y)) == 0                      # 越界左
    assert sprite.alpha_at(QPoint(5, LOGICAL_H)) == 0                   # 越界下

    sprite.facing = "right"     # 未登记 no_mirror：镜像烘焙进命中图
    sprite._rebuild_pixmap()
    assert sprite.alpha_at(QPoint(5, mid_y)) == 0
    assert sprite.alpha_at(QPoint(LOGICAL_W - 6, mid_y)) == 255


@pytest.mark.parametrize("dpr", DPRS)
def test_fully_opaque_frame_maps_whole_logical_rect(dpr):
    """全不透明帧：逻辑矩形四角都必须映射到命中图内（不因取整越界丢列/丢行）。"""
    sprite = _make_sprite(dpr=dpr, clip=FakeClip(_opaque_frame()))
    for probe in ((0, 0), (LOGICAL_W - 1, 0), (0, LOGICAL_H - 1),
                  (LOGICAL_W - 1, LOGICAL_H - 1), (LOGICAL_W // 2, LOGICAL_H // 2)):
        assert sprite.alpha_at(QPoint(*probe)) == 255


def test_logical_coordinates_unchanged_by_dpr(monkeypatch):
    """v1.2 裁决：每屏逻辑坐标——DPR 1 与 2 的 pos/rect/body_rect/钳制/位移逐位一致。"""
    monkeypatch.setattr(catalog, "character_body_box", lambda _cid, *a, **k: (100, 60, 400, 330))

    def _sprites():
        bounds = QRect(0, 0, 800, 600)
        a = _make_sprite(dpr=1.0, pos=(100, 100))
        b = _make_sprite(dpr=2.0, pos=(100, 100))
        for s in (a, b):
            s.set_bounds(bounds)
            s.set_pos(QPointF(5000, 5000))     # body_box 钳制
        return a, b

    a, b = _sprites()
    assert (a.pos, a.rect(), a.body_rect()) == (b.pos, b.rect(), b.body_rect())
    assert a.pos != QPointF(5000, 5000)        # 确实被钳（不是没走到钳制）

    # 同样的速度积分同样的 dt：逻辑位移一致（DPR 不参与积分）
    a, b = _sprites()
    for s in (a, b):
        s.set_velocity(QPointF(-100.0, 0.0))
        s.advance(0.5)
    assert (a.pos, a.rect()) == (b.pos, b.rect())


# ================================================================ D2：DPR 变化重建
def test_dpr_change_rebuilds_pixmap_and_hit_image():
    """DPR 变化后旧 pixmap/旧命中图立即作废重建，且新命中图按新 DPR 正确。"""
    sprite = _make_sprite(dpr=1.0)
    pm1, hit1 = sprite._pixmap, sprite._hit_image
    assert pm1.devicePixelRatio() == 1.0

    sprite.set_dpr(1.5)

    assert sprite.dpr == 1.5
    assert sprite._pixmap is not pm1                    # 旧成品不得留在缓存
    assert sprite._hit_image is not hit1
    assert sprite._pixmap.devicePixelRatio() == 1.5
    assert sprite._pixmap.width() == round(catalog.CANVAS_W * SCALE * 1.5)
    assert sprite._hit_image.width() == sprite._pixmap.width()
    assert sprite._frame_sig[-1] == 1.5                 # DPR 进签名（缓存键）
    assert sprite.alpha_at(QPoint(5, LOGICAL_H // 2)) == 255
    assert sprite.alpha_at(QPoint(LOGICAL_W // 2, LOGICAL_H // 2)) == 0

    # 同值重喂：签名不变，不重建（屏幕信号会重复上报，必须零开销）
    pm2 = sprite._pixmap
    sprite.set_dpr(1.5)
    assert sprite._pixmap is pm2

    sprite.set_dpr(2.0)                                 # 再变一次：仍重建
    assert sprite._pixmap.devicePixelRatio() == 2.0
    assert sprite._pixmap.width() == round(catalog.CANVAS_W * SCALE * 2.0)


def test_dpr_change_reports_dirty_for_repaint():
    """屏 DPR 变化时宠物可能静止：必须报脏要求重绘，不等下一个 tick。"""
    sprite = _make_sprite(dpr=1.0)
    dirty = []
    sprite._dirty_cb = lambda old, new: dirty.append((old, new))

    sprite.set_dpr(2.0)

    assert dirty == [(sprite.rect(), sprite.rect())]
    sprite.set_dpr(2.0)                                 # 同值：不重复报
    assert len(dirty) == 1


def test_scale_change_rebuilds_with_same_dpr():
    """scale setter 与帧重建路径同步：scale 进签名，命中图不得残留旧尺寸。"""
    sprite = _make_sprite(dpr=1.5)
    hit1 = sprite._hit_image
    assert sprite._pixmap.width() == round(catalog.CANVAS_W * SCALE * 1.5)   # 480

    sprite.scale = 0.25

    assert sprite.rect() == QRect(0, 0, 160, 90)
    assert sprite._hit_image is not hit1
    assert sprite._pixmap.width() == round(catalog.CANVAS_W * 0.25 * 1.5)    # 240
    assert sprite._pixmap.devicePixelRatio() == 1.5                          # DPR 不变
    assert sprite._hit_image.width() == 240
    assert sprite.alpha_at(QPoint(79, 45)) == 255
    assert sprite.alpha_at(QPoint(80, 45)) == 0
    assert sprite.alpha_at(QPoint(160, 45)) == 0        # 旧命中图尺寸下的越界点


def test_rebuild_failure_does_not_poison_cache():
    """等价 test_window_dpr_signals「记账只在成功后」：空帧不写签名，恢复后按新 DPR 重建。"""
    clip = FakeClip(_block_frame())
    sprite = _make_sprite(dpr=1.0, clip=clip)
    pm1 = sprite._pixmap

    clip._image = None                                  # 首帧未就绪/解码失败
    sprite.set_dpr(2.0)

    assert sprite._frame_sig is None                    # 失败路径不提前记账
    assert sprite._pixmap is pm1                        # 旧成品保留（失败不破坏显示）
    assert sprite._hit_image is None                    # 命中图已作废，不给错缓存
    assert sprite.alpha_at(QPoint(5, LOGICAL_H // 2)) == 0

    clip._image = _block_frame()                        # 恢复解码
    assert sprite._rebuild_pixmap() is True             # 仍按新 DPR 重试（未被记账跳过）
    assert sprite._pixmap.devicePixelRatio() == 2.0
    assert sprite._pixmap.width() == round(catalog.CANVAS_W * SCALE * 2.0)
    assert sprite.alpha_at(QPoint(5, LOGICAL_H // 2)) == 255


# ================================================================ overlay 侧喂 DPR
def test_overlay_add_sprite_feeds_screen_dpr():
    """add_sprite 即按所在屏 DPR 喂（构造期 windowHandle 可能为 None，取 QScreen）。"""
    screen = FakeScreen(dpr=1.5)
    overlay = OverlayWindow(screen=screen)
    sprite = _make_sprite(dpr=1.0)

    overlay.add_sprite(sprite)

    assert sprite.dpr == 1.5
    assert sprite._pixmap.devicePixelRatio() == 1.5
    assert sprite._pixmap.width() == round(catalog.CANVAS_W * SCALE * 1.5)


def test_overlay_show_event_refeeds_all_sprites():
    """showEvent 重喂：全部 sprite（含 4.2b 子宠）都按当前屏 DPR 重建。"""
    screen = FakeScreen(dpr=1.0)
    overlay = OverlayWindow(screen=screen)
    main = _make_sprite(dpr=1.0)
    child = _make_sprite(dpr=1.0)
    overlay.add_sprite(main)
    overlay.add_sprite(child)

    screen.set_dpr(2.0)
    overlay.showEvent(QShowEvent())

    for sprite in (main, child):
        assert sprite.dpr == 2.0
        assert sprite._pixmap.devicePixelRatio() == 2.0
        assert sprite._pixmap.width() == round(catalog.CANVAS_W * SCALE * 2.0)


def test_screen_dpi_signal_refeeds_when_overlay_stationary():
    """对照 test_screen_dpi_signals_force_rebuild_when_stationary：静止也重喂。

    Qt 6.11 无 devicePixelRatioChanged：显示缩放变化由 logical/physical
    DotsPerInchChanged 上报（devicePixelRatio() 随之变化）。
    """
    screen = FakeScreen(dpr=1.0)
    overlay = OverlayWindow(screen=screen)
    main = _make_sprite(dpr=1.0)
    child = _make_sprite(dpr=1.0)
    overlay.add_sprite(main)
    overlay.add_sprite(child)
    overlay.showEvent(QShowEvent())                     # 接线（无 window handle 也要挂屏信号）
    assert overlay._dpr_watch_screen is screen

    screen.set_dpr(1.5)
    screen.logicalDotsPerInchChanged.emit(144.0)
    assert main.dpr == 1.5 and child.dpr == 1.5
    assert main._pixmap.width() == round(catalog.CANVAS_W * SCALE * 1.5)

    screen.set_dpr(2.0)
    screen.physicalDotsPerInchChanged.emit(192.0)       # 同屏再上报：仍接线
    assert main.dpr == 2.0 and child.dpr == 2.0


def test_screen_geometry_changed_refeeds_dpr_and_syncs_geometry():
    """geometryChanged（分辨率/模式切换）→ overlay 跟随屏几何 + 重喂 DPR。"""
    screen = FakeScreen((0, 0, 1920, 1080), dpr=1.0)
    overlay = OverlayWindow(screen=screen)
    sprite = _make_sprite(dpr=1.0)
    overlay.add_sprite(sprite)
    overlay.showEvent(QShowEvent())

    screen.set_geometry((0, 0, 2560, 1440))
    screen.set_dpr(1.25)
    screen.geometryChanged.emit(screen.geometry())

    assert overlay.geometry() == QRect(0, 0, 2560, 1440)
    assert sprite.dpr == 1.25
    assert sprite._pixmap.width() == round(catalog.CANVAS_W * SCALE * 1.25)


def test_watch_rewires_dpi_signals_to_new_screen():
    """对照 test_watch_rewires_dpi_signals_to_new_screen：换屏重挂，旧屏不再触发。"""
    scr_a = FakeScreen(dpr=1.0)
    scr_b = FakeScreen(dpr=1.0)
    overlay = OverlayWindow(screen=scr_a)
    sprite = _make_sprite(dpr=1.0)
    overlay.add_sprite(sprite)

    overlay._wire_screen_dpi_signals(scr_a)
    assert overlay._dpr_watch_screen is scr_a
    scr_a.set_dpr(1.5)
    scr_a.logicalDotsPerInchChanged.emit(144.0)
    assert sprite.dpr == 1.5

    overlay._wire_screen_dpi_signals(scr_b)
    assert overlay._dpr_watch_screen is scr_b
    scr_a.set_dpr(2.0)
    scr_a.physicalDotsPerInchChanged.emit(192.0)        # 旧屏已断开
    assert sprite.dpr == 1.5
    scr_b.set_dpr(2.0)
    scr_b.logicalDotsPerInchChanged.emit(192.0)
    assert sprite.dpr == 2.0


def test_screen_changed_adopts_new_screen_and_refeeds(monkeypatch):
    """对照 test_screen_changed_signal_forces_rebuild_with_new_dpr：跨屏换源+重建。

    tick 刷新率取**屏枚举**里最高的那一档（单合成窗铺满整个虚拟桌面，窗口自己
    那块屏不等于宠所在屏），故这台"机器"上只有 scr_b 这块屏。
    """
    scr_a = FakeScreen(dpr=1.0)
    scr_b = FakeScreen((0, 0, 2560, 1440), dpr=2.0, refresh=144.0)
    monkeypatch.setattr(QGuiApplication, "screens", staticmethod(lambda: [scr_b]))
    overlay = OverlayWindow(screen=scr_a)
    sprite = _make_sprite(dpr=1.0)
    overlay.add_sprite(sprite)
    overlay._wire_screen_dpi_signals(scr_a)

    overlay._on_window_screen_changed(scr_b)

    assert overlay._screen is scr_b
    assert overlay._dpr_watch_screen is scr_b
    assert sprite.dpr == 2.0
    assert sprite._pixmap.width() == round(catalog.CANVAS_W * SCALE * 2.0)
    assert overlay._timer.interval() == OverlayWindow._tick_interval_ms(144.0)

    scr_a.set_dpr(3.0)                                  # 旧屏不再影响
    scr_a.logicalDotsPerInchChanged.emit(288.0)
    assert sprite.dpr == 2.0


def test_show_event_arms_and_disarm_stops_signals(monkeypatch):
    """对照 test_show_event_arms_and_disarm_stops_signals：真实 showEvent 接线/摘线。"""
    overlay = OverlayWindow()
    overlay.show()
    app.processEvents()
    try:
        win = overlay.windowHandle()
        assert win is not None
        assert overlay._dpr_watch_window is win
        assert overlay._dpr_watch_screen is overlay._screen

        calls: list[int] = []
        monkeypatch.setattr(overlay, "_feed_dpr", lambda: calls.append(1))
        screen = overlay._dpr_watch_screen
        screen.logicalDotsPerInchChanged.emit(144.0)    # 每次上报都必须仍接线
        assert len(calls) >= 1

        overlay._disarm_dpr_change_watch()
        assert overlay._dpr_watch_window is None
        assert overlay._dpr_watch_screen is None
        base = len(calls)
        screen.logicalDotsPerInchChanged.emit(96.0)
        screen.geometryChanged.emit(screen.geometry())
        assert len(calls) == base                       # 摘线后不再触发
    finally:
        overlay.close()


def test_overlay_sprite_hit_at_dpr_uses_logical_coords():
    """端到端命中：overlay 局部逻辑坐标 → sprite 局部逻辑坐标 → 命中图物理像素。"""
    screen = FakeScreen(dpr=2.0)
    overlay = OverlayWindow(screen=screen)
    sprite = _make_sprite(dpr=2.0, pos=(100, 100))
    overlay.add_sprite(sprite)

    mid_y = 100 + LOGICAL_H // 2
    assert overlay.sprite_at(QPoint(105, mid_y)) is sprite          # 左半不透明
    assert overlay.sprite_at(QPoint(100 + LOGICAL_W // 2 + 5, mid_y)) is None
    assert overlay.sprite_at(QPoint(100 - 1, mid_y)) is None        # 矩形粗筛外


# ================================================================ O1：帧重建链恒等短路
class _TransformProbe(QImage):
    """记录重建链上每个变换调用的真实 QImage（子类即 seam，不做像素级替身）。

    产品代码 ``_rebuild_pixmap`` 调的是这张图的公开方法；断言"该跳的整条链都跳、
    该做的仍然按序做"，而不是数像素或 mock 产品对象。变换结果继续用探针包装
    （同尺寸浅拷贝）并共享同一份日志，因此 convert→scaled 的**顺序**也能钉住。
    """

    def __init__(self, *args, **kwargs):
        self.calls: list[str] = []
        if args and isinstance(args[0], QImage):
            super().__init__(args[0])
        else:
            super().__init__(*args, **kwargs)

    def _derive(self, image):
        probe = _TransformProbe(image)
        probe.calls = self.calls                           # 子图继续写同一份日志
        return probe

    def convertToFormat(self, fmt, *a, **kw):
        self.calls.append("convert")
        return self._derive(super().convertToFormat(fmt, *a, **kw))

    def scaled(self, *a, **kw):
        self.calls.append("scaled")
        return self._derive(super().scaled(*a, **kw))

    def flipped(self, *a, **kw):
        self.calls.append("flipped")
        return self._derive(super().flipped(*a, **kw))

    def mirrored(self, *a, **kw):
        self.calls.append("mirrored")           # 废弃 API：重建链不该再走它
        return self._derive(super().mirrored(*a, **kw))


def _premultiplied_frame(w, h):
    """已是 ARGB32_Premultiplied 的帧（GifClip/预乘来源素材的形态）。"""
    img = _TransformProbe(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    painter = QPainter(img)
    painter.fillRect(0, 0, w, h, QColor(0, 255, 0, 255))
    painter.end()
    return img


def test_identity_frame_skips_convert_and_scale():
    """O1：格式已是 ARGB32_Premultiplied 不 convert；目标尺寸==源尺寸不 scaled。

    scale=0.5 × dpr=1.0 的目标物理尺寸恰为 320×180（=源尺寸），格式也已预乘：
    整条链都是恒等变换。两条短路的成本收益在真素材上量不出（Qt 自己对同格式
    convert / 同尺寸 smooth scaled 已各短路到 ~1-2µs，见证据目录微基准），
    保留它们是为了不再白调那两次 API；这里钉的是**语义不变**：结果尺寸/格式/
    命中图仍与逐级变换一致，且命中图绝不与帧缓冲别名。
    """
    frame = _premultiplied_frame(LOGICAL_W, LOGICAL_H)      # 320×180 预乘
    sprite = _make_sprite(scale=SCALE, dpr=1.0, clip=FakeClip(frame))
    frame.calls.clear()

    sprite._frame_sig = None                                # 作废签名 → 强制整链重建
    assert sprite._rebuild_pixmap() is True

    assert frame.calls == [], f"恒等帧不得走任何变换：{frame.calls}"
    assert (sprite._pixmap.width(), sprite._pixmap.height()) == (LOGICAL_W, LOGICAL_H)
    # V-15 别名面：整链短路后 img 就是帧本身，命中图必须是独立缓冲（深拷贝）
    assert sprite._hit_image.cacheKey() != frame.cacheKey()
    assert sprite._hit_image.format() == QImage.Format.Format_ARGB32_Premultiplied
    assert sprite._hit_image is not None
    assert sprite.alpha_at(QPoint(5, LOGICAL_H // 2)) == 255


def test_needed_transforms_still_run_in_order():
    """O1 反向守门：源格式/尺寸不匹配时链条逐级照旧（顺序 = 先预乘再缩放）。"""
    raw = _TransformProbe(catalog.CANVAS_W, catalog.CANVAS_H,
                          QImage.Format.Format_ARGB32)      # 640×360 直通 alpha
    raw.fill(Qt.GlobalColor.transparent)
    sprite = _make_sprite(scale=SCALE, dpr=1.0, clip=FakeClip(raw))

    raw.calls.clear()
    sprite._frame_sig = None                                # 320×180 目标：两级都不恒等
    assert sprite._rebuild_pixmap() is True
    assert raw.calls == ["convert", "scaled"]               # 顺序不可交换（防暗边）
    assert (sprite._pixmap.width(), sprite._pixmap.height()) == (LOGICAL_W, LOGICAL_H)

    raw.calls.clear()
    sprite.set_dpr(2.0)                                     # 640×360 目标：缩放恒等
    assert raw.calls == ["convert"]
    assert (sprite._pixmap.width(), sprite._pixmap.height()) == (LOGICAL_W * 2, LOGICAL_H * 2)

    sprite.facing = "right"                                 # 镜像分支：flipped 在最前
    raw.calls.clear()
    sprite._frame_sig = None
    sprite._rebuild_pixmap()
    assert raw.calls == ["flipped", "convert"]


def test_mirror_uses_flipped_horizontal():
    """O1：mirrored(True, False) → flipped(Qt.Horizontal)，轴向不变（左右镜像）。

    左上角小块镜像后必须落在右上角；上下镜像/不镜像都过不了这组断言。
    """
    img = _TransformProbe(catalog.CANVAS_W, catalog.CANVAS_H,
                          QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    painter = QPainter(img)
    painter.fillRect(0, 0, 64, 32, QColor(0, 255, 0, 255))  # 源像素左上 64×32
    painter.end()

    sprite = _make_sprite(scale=SCALE, dpr=1.0, clip=FakeClip(img))
    sprite.facing = "right"
    img.calls.clear()
    sprite._frame_sig = None
    assert sprite._rebuild_pixmap() is True

    assert img.calls == ["flipped", "convert", "scaled"], \
        f"镜像必须走 flipped（mirrored 已废弃）：{img.calls}"
    out = sprite._pixmap.toImage()
    # 64×32 源块 ×0.5 → 物理 32×16，镜像后贴右边缘
    assert out.pixelColor(LOGICAL_W - 8, 8) == QColor(0, 255, 0, 255)
    assert out.pixelColor(8, 8).alpha() == 0                 # 左半已空（确实镜像了）
    assert out.pixelColor(8, LOGICAL_H - 8).alpha() == 0     # 不是上下镜像
    assert out.pixelColor(LOGICAL_W - 8, LOGICAL_H - 8).alpha() == 0

    sprite.facing = "left"                                   # 回正：不镜像
    img.calls.clear()
    sprite._frame_sig = None
    sprite._rebuild_pixmap()
    assert img.calls == ["convert", "scaled"]                # 无 flipped
    assert sprite._pixmap.toImage().pixelColor(8, 8) == QColor(0, 255, 0, 255)
