# -*- coding: utf-8 -*-
"""P1 复审：DPR 变化改为 Qt 信号驱动 + 记账只在成功后。

覆盖：
- QWindow.screenChanged → 强制 _rebuild_frame（含缓存 key 用新 DPR），
  窗口静止（无 moveEvent）时跨屏也能重建；
- 所在屏显示缩放变化（QScreen logicalDotsPerInchChanged /
  physicalDotsPerInchChanged；Qt 6.11 无 devicePixelRatioChanged）→
  强制 _rebuild_frame：窗口不动时系统改缩放也能重建；
- 跨屏后 DPI 信号重挂新屏（旧屏信号不再触发）；
- showEvent 自动接线、摘线后信号不再触发；
- _last_frame_dpr 只在重建成功后更新：失败路径不提前记账，
  后续信号/移动仍会按新 DPR 重试（moveEvent 兜底）。
"""
from __future__ import annotations

import os
from types import SimpleNamespace

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from pet import catalog
from pet import window as window_mod


def _qapp() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _frame_image(variant: int = 0) -> QImage:
    """640x360：左上 100x100 纯红块，其余透明（非对称，可检验镜像/串帧）。"""
    img = QImage(640, 360, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.fillRect(0, 0, 100, 100, QColor(255, 0, 0, 255))
    p.end()
    return img


class _Clip:
    """最小解码桩：currentPixmap 返回当前帧；记录解码次数。"""

    def __init__(self, images, frame_number: int = 0):
        self._images = list(images)
        self._frame_number = frame_number
        self.pixmap_requests = 0

    def currentPixmap(self):
        self.pixmap_requests += 1
        return QPixmap.fromImage(self._images[self._frame_number])

    def currentFrameNumber(self):
        return self._frame_number

    def jumpToFrame(self, n):
        self._frame_number = max(0, min(n, len(self._images) - 1))
        return self._frame_number <= 0

    def stop(self):
        pass

    def start(self):
        pass

    def set_playback_speed(self, speed):
        pass

    def frameCount(self):
        return len(self._images)

    def duration(self):
        return 1.0

    def currentTimeSeconds(self):
        return 0.0


class _FailingClip(_Clip):
    """currentPixmap 返回空 QPixmap：模拟解码失败（重建失败路径）。"""

    def currentPixmap(self):
        self.pixmap_requests += 1
        return QPixmap()  # null


class _Lib:
    def __init__(self, clips, no_mirror=frozenset()):
        self._clips = dict(clips)
        self.no_mirror = set(no_mirror)

    def names(self):
        return list(self._clips)

    def movies(self):
        return dict(self._clips)

    def movie(self, name):
        return self._clips[name]

    def frames(self, name):
        return self._clips[name].frameCount()

    def duration(self, name):
        return 1.0

    def clip_path(self, name):
        return None


class _SignalPet(window_mod.PetWindow):
    """真实 QWidget 子类：跳过 PetWindow 繁重初始化，只保留信号路径。

    - 真实 showEvent / windowHandle() / 信号接线来自 PetWindow 基类；
    - _rebuild_frame 挂真实实现（含缓存 key / _last_frame_dpr 记账）；
    - _screen_available 返回可控制的 _screen_dpr，模拟屏幕 DPR 变化；
    - showEvent 依赖项换成桩（本测试只关心 DPR 信号接线）。
    """

    _rebuild_frame = window_mod.PetWindow._rebuild_frame
    _frame_cache_key = window_mod.PetWindow._frame_cache_key
    _refresh_frame_for_screen_dpr = window_mod.PetWindow._refresh_frame_for_screen_dpr

    def __init__(self, movie, lib, scale=0.5, anim="idle", dpr=1.0):
        QWidget.__init__(self)
        self.cfg = SimpleNamespace(get=lambda key, default=None: default)
        self.movie = movie
        self.lib = lib
        self.facing = "left"
        self.scale = scale
        self.anim = anim
        self._w = max(1, int(round(catalog.CANVAS_W * scale)))
        self._h = max(1, int(round((catalog.CANVAS_H + catalog.PAD) * scale)))
        self._frame_pixmap = None
        self._hit_alpha_image = None
        self._mask_bounds = None
        self._collision_local_bounds = None
        self._frame_key = None
        self._last_frame_dpr = None
        self._screen_dpr = dpr
        self._squash_active = False
        self._squash_progress = 1.0
        self._slingshot_rebound_progress = 0.0
        self._hidden_paused = False
        self._interaction_state = "IDLE"
        self._position_sync_pending = False
        self._dpr_watch_window = None
        self._dpr_watch_screen = None
        self.update_calls = 0

    def _screen_available(self):
        return SimpleNamespace(devicePixelRatio=lambda: self._screen_dpr)

    def _sync_mask(self):
        pass

    def update(self):
        self.update_calls += 1

    # PetWindow.showEvent 依赖桩（本测试不关心碰撞/透明度/层级）；
    # moveEvent 的位置同步在 offscreen show() 期间也会触发，一并桩掉。
    def _submit_collision_state(self, *_a, **_k):
        pass

    def _schedule_macos_window_level(self, *_a, **_k):
        pass

    def _apply_opacity(self):
        pass

    def _restore_dock_icon_preference(self):
        pass

    def _schedule_position_sync(self):
        pass


class _FakeScreen(QObject):
    """带 QScreen DPI 信号的假屏：验证跨屏重挂（真实 QScreen 不可新建）。"""

    logicalDotsPerInchChanged = Signal(float)
    physicalDotsPerInchChanged = Signal(float)


def _make_pet(dpr: float = 1.0):
    clip = _Clip([_frame_image()])
    lib = _Lib({"idle": clip})
    return clip, _SignalPet(clip, lib, dpr=dpr)


# ================================================================ Qt 信号驱动 DPR 变化

def test_screen_changed_signal_forces_rebuild_with_new_dpr():
    """P1 复审：窗口静止时跨屏（screenChanged 信号）→ 强制按新 DPR 重建。

    旧实现只在 moveEvent 检测 DPR：窗口不移动时跨屏（如副屏 DPI 配置
    变化）不会重建。信号路径不依赖移动事件；新 DPR 进入缓存 key，
    帧号/朝向等未变也不会被快路径跳过。
    """
    _qapp()
    clip, pet = _make_pet()
    window_mod.PetWindow._rebuild_frame(pet)   # 先按 DPR=1.0 建一帧
    assert pet._last_frame_dpr == 1.0
    pm1 = pet._frame_pixmap

    pet._screen_dpr = 2.0                      # 窗口所在屏 DPR 变化（窗口未移动）
    pet.winId()                                # 创建原生窗口，得到 QWindow
    win = pet.windowHandle()
    assert win is not None
    window_mod.PetWindow._arm_dpr_change_watch(pet)
    assert pet._dpr_watch_window is win

    win.screenChanged.emit(win.screen())       # 模拟窗口跨屏信号
    assert pet._frame_pixmap is not pm1                 # 强制重建，非旧成品
    assert pet._frame_pixmap.width() == round(catalog.CANVAS_W * 0.5 * 2.0)
    assert pet._last_frame_dpr == 2.0
    assert pet.update_calls == 1

    # 同屏再发一次（DPR 未变）：_rebuild_frame 快路径跳过，但信号仍被接线
    win.screenChanged.emit(win.screen())
    assert pet._frame_pixmap.width() == round(catalog.CANVAS_W * 0.5 * 2.0)
    assert pet.update_calls == 2
    window_mod.PetWindow._disarm_dpr_change_watch(pet)
    pet.deleteLater()


def test_screen_dpi_signals_force_rebuild_when_stationary():
    """P1 复审：静止窗口发生系统显示缩放变化（DPI 信号）→ 强制重建。

    Qt 6.11 的 QScreen 没有 devicePixelRatioChanged；显示缩放变化由
    logical/physicalDotsPerInchChanged 上报（devicePixelRatio() 随之变化）。
    """
    _qapp()
    clip, pet = _make_pet()
    window_mod.PetWindow._rebuild_frame(pet)
    pm1 = pet._frame_pixmap

    pet._screen_dpr = 2.0
    pet.winId()
    win = pet.windowHandle()
    window_mod.PetWindow._arm_dpr_change_watch(pet)
    scr = win.screen()
    assert pet._dpr_watch_screen is scr

    scr.logicalDotsPerInchChanged.emit(144.0)  # 显示缩放从 100% → 150%
    assert pet._frame_pixmap is not pm1
    assert pet._frame_pixmap.width() == round(catalog.CANVAS_W * 0.5 * 2.0)
    assert pet._last_frame_dpr == 2.0
    assert pet.update_calls == 1

    scr.physicalDotsPerInchChanged.emit(144.0)  # 同屏再上报 → 仍接线
    assert pet.update_calls == 2
    window_mod.PetWindow._disarm_dpr_change_watch(pet)
    pet.deleteLater()


def test_watch_rewires_dpi_signals_to_new_screen():
    """跨屏后 DPI 信号重挂新屏：旧屏的 DPI 变化不再触发重建。"""
    _qapp()
    clip, pet = _make_pet()
    scr_a = _FakeScreen()
    scr_b = _FakeScreen()

    window_mod.PetWindow._wire_screen_dpi_signals(pet, scr_a)
    assert pet._dpr_watch_screen is scr_a
    scr_a.logicalDotsPerInchChanged.emit(144.0)
    assert pet.update_calls == 1

    # 模拟 screenChanged 换挂新屏
    window_mod.PetWindow._wire_screen_dpi_signals(pet, scr_b)
    assert pet._dpr_watch_screen is scr_b
    scr_a.physicalDotsPerInchChanged.emit(144.0)  # 旧屏已断开
    assert pet.update_calls == 1
    scr_b.logicalDotsPerInchChanged.emit(144.0)
    assert pet.update_calls == 2


def test_show_event_arms_and_disarm_stops_signals():
    """真实 showEvent 自动接线（QWindow + 所在屏 DPI 信号）；
    摘线后信号不再触发重建。"""
    _qapp()
    clip, pet = _make_pet()
    pet.show()
    _qapp().processEvents()
    try:
        win = pet.windowHandle()
        assert win is not None
        assert pet._dpr_watch_window is win          # showEvent 已接线
        assert pet._dpr_watch_screen is win.screen()

        pet._screen_dpr = 2.0
        win.screenChanged.emit(win.screen())
        assert pet._frame_pixmap.width() == round(catalog.CANVAS_W * 0.5 * 2.0)
        assert pet.update_calls == 1

        # 摘线（closeEvent 路径调用的同一方法）→ 信号不再触发
        window_mod.PetWindow._disarm_dpr_change_watch(pet)
        assert pet._dpr_watch_window is None
        assert pet._dpr_watch_screen is None
        pet._screen_dpr = 1.0
        win.screenChanged.emit(win.screen())
        assert pet.update_calls == 1
        win.screen().logicalDotsPerInchChanged.emit(96.0)
        assert pet.update_calls == 1
    finally:
        pet.deleteLater()


# ================================================================ _last_frame_dpr 记账

def test_last_frame_dpr_only_updated_after_successful_rebuild():
    """P1 复审：重建失败（解码返回空图）时 _last_frame_dpr 不提前记账；
    后续 moveEvent 兜底/信号仍会按新 DPR 重试重建。"""
    _qapp()
    clip, pet = _make_pet()
    window_mod.PetWindow._rebuild_frame(pet)
    assert pet._last_frame_dpr == 1.0
    pm1 = pet._frame_pixmap

    # 素材损坏/解码失败：DPR=2.0 下重建失败 → 不得记成 2.0
    bad = _FailingClip([_frame_image()])
    pet.movie = bad
    pet._screen_dpr = 2.0
    window_mod.PetWindow._rebuild_frame(pet)
    assert pet._last_frame_dpr == 1.0        # 失败路径不提前记账
    assert pet._frame_pixmap is pm1          # 旧成品保留（失败不破坏显示）

    # moveEvent 兜底路径：失败后仍按新 DPR 重试（不因提前记账被跳过）
    window_mod.PetWindow._refresh_frame_for_screen_dpr(pet)
    assert pet._last_frame_dpr == 1.0

    # 恢复解码 → 重建成功 → 记账
    pet.movie = clip
    window_mod.PetWindow._refresh_frame_for_screen_dpr(pet)
    assert pet._last_frame_dpr == 2.0
    assert pet._frame_pixmap is not pm1
    assert pet._frame_pixmap.width() == round(catalog.CANVAS_W * 0.5 * 2.0)
