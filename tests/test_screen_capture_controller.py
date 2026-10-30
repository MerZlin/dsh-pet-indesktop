# -*- coding: utf-8 -*-
"""「快速识屏」P0 宿主采集闭环（真实信号 + 假宿主，offscreen）。

闭环 = 藏自家窗口 → 冻结一帧 → 框选 → 裁出选区像素 → 还原窗口。这里用注入的
假 hider / 假抓帧器 / 假覆盖层把**调用顺序**与**异常出口**钉死，因为本片最贵的
bug 不是画错，而是：

- 中途失败（抓帧拿到空图、选区越界）后**窗口没还**——用户的桌宠从此消失；
- 还没还原就先通知下游——下游弹窗时桌宠还在"消失"状态；
- 重复触发时二次隐藏把第一次的账冲掉。

动作（提取文字 / 翻译 / 截图问答 / 复制图像）**不在入口定死**：覆盖层动作条在
松手后给出来，随 ``captured`` 的第三个参数原样带回编排层。控制器只做一件事——
不认识的动作**在门口挡住**（``unknown-action:...``），不让它流到下游分发表里
被 else 兜成一个"看起来能跑"的动作。
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, QRect, Signal  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pet.screen_capture.controller import ScreenCaptureController  # noqa: E402
from pet.screen_capture.overlay import (  # noqa: E402
    ACTION_ASK,
    ACTION_COPY,
    ACTION_OCR,
    ACTION_TRANSLATE,
)
from pet.screen_capture.pixels import SRGB8_FORMAT  # noqa: E402
from pet.screen_capture.session import CapturePhase, SelectionRect  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


class _FakeScreen:
    def __init__(self, width: int = 60, height: int = 60, dpr: float = 1.0) -> None:
        self._rect = QRect(0, 0, width, height)
        self._dpr = dpr

    def geometry(self) -> QRect:  # noqa: D102
        return QRect(self._rect)

    def devicePixelRatio(self) -> float:  # noqa: N802 - 对齐 Qt 命名
        return self._dpr


class _FakeHider:
    def __init__(self, log, *, fail_hide: bool = False, fail_restore: bool = False) -> None:
        self._log = log
        self._fail_hide = fail_hide
        self._fail_restore = fail_restore

    def hide_all(self) -> int:  # noqa: D102
        self._log.append("hide")
        if self._fail_hide:
            raise RuntimeError("boom")
        return 1

    def restore_all(self) -> int:  # noqa: D102
        self._log.append("restore")
        if self._fail_restore:
            raise RuntimeError("restore boom")
        return 1


class _FakeOverlay(QObject):
    """假覆盖层：按新契约只提供两个信号（``action_requested`` / ``selection_cancelled``）。"""

    action_requested = Signal(str, object)   # (动作 id, SelectionRect)
    selection_cancelled = Signal(str)        # 取消理由（"esc" / "toolbar"）

    def __init__(self, frame, screen, log) -> None:
        super().__init__()
        self._log = log
        self.frame = frame
        self.screen = screen
        self.closed = False
        log.append("overlay.create")

    def show(self) -> None:  # noqa: D102
        self._log.append("overlay.show")

    def close(self) -> None:  # noqa: D102
        self.closed = True
        self._log.append("overlay.close")

    def pick(self, action, selection) -> None:  # noqa: D102
        """等价于用户在动作条上选了 ``action``（或按了 Enter = ocr）。"""
        self.action_requested.emit(action, selection)

    def cancel(self, reason: str = "esc") -> None:  # noqa: D102
        self.selection_cancelled.emit(reason)


def _frame(width: int = 60, height: int = 60, *, dpr: float = 1.0) -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(255, 255, 255))
    painter = QPainter(image)
    painter.fillRect(0, 0, width // 2, height // 2, QColor(220, 30, 30))
    painter.end()
    image.setDevicePixelRatio(dpr)
    return image


class _Setup:
    """一次闭环的装配结果：控制器 + 观测量 + 已创建的覆盖层。

    ``settler`` 默认注入一个**静默**的假等帧器（除非 ``record_settle=True``）：
    真的那条会去问 DWM（``DwmFlush``），耗时由系统决定——单测不该悬在它上面，
    真机对比交给 ``.scratch/screen-capture/probe_hide_settle_grab.py``。
    """

    def __init__(self, *, dpr: float = 1.0, grab_result=None, fail_hide: bool = False,
                 fail_restore: bool = False, raise_on_grab: bool = False,
                 record_settle: bool = False, settler=None) -> None:
        self.log: list[str] = []
        self.frames: list[QImage] = []
        self.overlays: list[_FakeOverlay] = []
        self.captured: list[tuple] = []
        self.cancelled: list[str] = []
        self.failed: list[str] = []
        self.settles: list = []
        self.screen = _FakeScreen(dpr=dpr)
        self.hider = _FakeHider(self.log, fail_hide=fail_hide, fail_restore=fail_restore)

        def _grab(_screen):
            self.log.append("grab")
            if raise_on_grab:
                raise RuntimeError("grab boom")
            image = _frame() if grab_result is None else grab_result
            self.frames.append(image)
            return image

        def _overlay_factory(frame, screen):
            overlay = _FakeOverlay(frame, screen, self.log)
            self.overlays.append(overlay)
            return overlay

        def _settle(screen):
            self.settles.append(screen)
            if record_settle:
                self.log.append("settle")

        self.controller = ScreenCaptureController(
            session=None, hider=self.hider, grabber=_grab,
            overlay_factory=_overlay_factory, screen=self.screen,
            settler=settler if settler is not None else _settle)
        self.controller.captured.connect(self._on_captured)
        self.controller.cancelled.connect(self.cancelled.append)
        self.controller.failed.connect(self.failed.append)

    def _on_captured(self, image, selection, action):
        self.log.append("captured")
        self.captured.append((image, selection, action))

    # 便捷入口：本轮已创建的覆盖层（用例里读起来不至于满屏下标）

    @property
    def overlay(self) -> _FakeOverlay:
        return self.overlays[-1]


# ------------------------------------------------------------------ 正常闭环


def test_happy_path_order_and_payload(qapp):
    setup = _Setup()
    assert setup.controller.start() is True

    assert setup.log[:3] == ["hide", "grab", "overlay.create"]
    assert setup.log[3] == "overlay.show"
    assert setup.controller.phase is CapturePhase.SELECTING
    assert setup.overlay.frame is setup.frames[0]  # 覆盖层画的就是刚冻的那一帧

    setup.overlay.pick(ACTION_OCR, SelectionRect(10, 10, 20, 20))

    # 还原必须早于通知下游：下游弹窗时桌宠不该还在"消失"状态
    assert setup.log.index("restore") < setup.log.index("captured")
    assert setup.log[-3:] == ["overlay.close", "restore", "captured"]
    assert setup.cancelled == [] and setup.failed == []

    image, selection, action = setup.captured[0]
    assert selection == SelectionRect(10, 10, 20, 20)
    assert action == ACTION_OCR
    assert image.format() == SRGB8_FORMAT
    assert (image.width(), image.height()) == (20, 20)
    assert image.pixelColor(0, 0).getRgb() == (220, 30, 30, 255)
    assert setup.controller.phase is CapturePhase.IDLE


def test_fractional_dpr_crops_device_pixels(qapp):
    setup = _Setup(dpr=1.5)
    setup.controller.start()

    setup.overlay.pick(ACTION_OCR, SelectionRect(4, 4, 8, 8))

    image, _, _ = setup.captured[0]
    assert (image.width(), image.height()) == SelectionRect(4, 4, 8, 8).device_rect(1.5)[2:]


@pytest.mark.parametrize("action", [ACTION_OCR, ACTION_TRANSLATE, ACTION_ASK, ACTION_COPY])
def test_every_action_bar_choice_rides_along_untouched(qapp, action):
    """动作条的四个动作控制器一律原样带回（分发在编排层，不在这里猜）。"""
    setup = _Setup()
    setup.controller.start()

    setup.overlay.pick(action, SelectionRect(1, 1, 5, 5))

    assert len(setup.captured) == 1
    assert setup.captured[0][2] == action
    assert setup.failed == []


def test_user_cancel_restores_windows(qapp):
    setup = _Setup()
    setup.controller.start()

    setup.overlay.cancel("esc")

    assert setup.cancelled == ["esc"]
    assert setup.captured == [] and setup.failed == []
    assert setup.log[-2:] == ["overlay.close", "restore"]
    assert setup.controller.phase is CapturePhase.IDLE


def test_toolbar_cancel_carries_its_own_reason(qapp):
    """动作条「取消」与 Esc 走同一条收口，但理由要能区分（排查时看得到用户从哪退出）。"""
    setup = _Setup()
    setup.controller.start()

    setup.overlay.cancel("toolbar")

    assert setup.cancelled == ["toolbar"]
    assert setup.failed == []
    assert setup.log[-2:] == ["overlay.close", "restore"]
    assert setup.controller.phase is CapturePhase.IDLE


def test_overlay_is_closed_before_windows_come_back(qapp):
    """覆盖层（置顶独占）必须先消失，否则还原的窗口被它压在下面看不到。"""
    setup = _Setup()
    setup.controller.start()

    setup.overlay.cancel("esc")

    assert setup.log.index("overlay.close") < setup.log.index("restore")


# ---------------------------------------------- 不认识的动作挡在门口（新增契约）


def test_an_unknown_action_aborts_instead_of_flowing_downstream(qapp):
    """覆盖层报到不认识的动作 id = 契约破裂，宁可失败也不许瞎猜一个动作执行。"""
    setup = _Setup()
    setup.controller.start()

    setup.overlay.pick("delete-everything", SelectionRect(1, 1, 5, 5))

    assert setup.captured == []
    assert setup.failed == ["unknown-action:delete-everything"]
    assert setup.log[-1] == "restore"
    assert setup.controller.phase is CapturePhase.IDLE


def test_an_unknown_action_is_aborted_before_the_crop(qapp):
    """挡在裁剪之前：连像素都不必动（下游拿不到东西，也就没有"偷偷执行"的余地）。"""
    setup = _Setup()
    setup.controller.start()

    setup.overlay.pick("", SelectionRect(1, 1, 5, 5))

    assert setup.failed == ["unknown-action:"]
    assert setup.controller.phase is CapturePhase.IDLE


# ------------------------------------------------------------------ 失败出口


def test_hide_failure_restores_and_never_grabs(qapp):
    setup = _Setup(fail_hide=True)

    assert setup.controller.start() is False

    assert setup.failed and setup.failed[0].startswith("hide-failed")
    assert "grab" not in setup.log
    assert "restore" in setup.log      # 可能已藏了一半 ⇒ 必须照样收口
    assert setup.controller.phase is CapturePhase.IDLE


def test_empty_grab_restores_and_never_shows_overlay(qapp):
    setup = _Setup(grab_result=QImage())

    assert setup.controller.start() is False

    assert setup.failed == ["grab-empty"]
    assert setup.overlays == []
    assert setup.log[-1] == "restore"
    assert setup.controller.phase is CapturePhase.IDLE


def test_grab_exception_restores(qapp):
    setup = _Setup(raise_on_grab=True)

    assert setup.controller.start() is False

    assert setup.failed and setup.failed[0].startswith("grab-failed")
    assert setup.log[-1] == "restore"
    assert setup.controller.phase is CapturePhase.IDLE


def test_empty_selection_from_overlay_is_not_captured(qapp):
    setup = _Setup()
    setup.controller.start()

    setup.overlay.pick(ACTION_OCR, SelectionRect(5, 5, 0, 0))

    assert setup.captured == []
    assert setup.failed == ["empty-selection"]
    assert setup.log[-1] == "restore"
    assert setup.controller.phase is CapturePhase.IDLE


def test_crop_failure_is_reported_and_restored(qapp):
    setup = _Setup()
    setup.controller.start()

    setup.overlay.pick(ACTION_OCR, SelectionRect(500, 500, 20, 20))

    assert setup.captured == []
    assert setup.failed and setup.failed[0].startswith("crop-failed")
    assert setup.log[-1] == "restore"
    assert setup.controller.phase is CapturePhase.IDLE


# -------------------------------------------------------------- 重复触发防护


def test_second_start_while_covering_is_refused(qapp):
    setup = _Setup()
    assert setup.controller.start() is True

    assert setup.controller.start() is False

    assert setup.log.count("hide") == 1
    assert len(setup.overlays) == 1


def test_controller_recovers_after_cancel(qapp):
    setup = _Setup()
    setup.controller.start()
    setup.overlay.cancel("esc")

    assert setup.controller.start() is True

    assert setup.log.count("hide") == 2
    assert len(setup.overlays) == 2
    setup.overlay.pick(ACTION_OCR, SelectionRect(1, 1, 5, 5))
    assert len(setup.captured) == 1


def test_double_action_request_does_not_emit_twice(qapp):
    """双击/重复上报只算一次：第二次那条在 phase 检查上被丢掉。"""
    setup = _Setup()
    setup.controller.start()

    setup.overlay.pick(ACTION_OCR, SelectionRect(1, 1, 5, 5))
    setup.overlay.pick(ACTION_COPY, SelectionRect(1, 1, 5, 5))

    assert len(setup.captured) == 1
    assert setup.captured[0][2] == ACTION_OCR


def test_overlay_destroyed_while_covering_still_restores_windows(qapp):
    """覆盖层被 Qt 直接销毁时没有 Esc/动作可等；必须自己收口还窗口。"""
    import shiboken6

    setup = _Setup()
    setup.controller.start()

    shiboken6.delete(setup.overlay)

    assert setup.failed and setup.failed[0].startswith("overlay-destroyed")
    assert setup.captured == [] and setup.cancelled == []
    assert setup.log[-1] == "restore"
    assert setup.controller.phase is CapturePhase.IDLE


def test_restore_failure_does_not_wedge_the_controller(qapp):
    """还窗口失败仍须把会话收回 idle——卡在中间态会让此后每次触发都被拒。"""
    setup = _Setup(fail_restore=True)
    setup.controller.start()

    setup.overlay.cancel("esc")

    assert setup.cancelled == ["esc"]
    assert setup.controller.phase is CapturePhase.IDLE
    assert setup.controller.start() is True


# ------------------------------------------------- hide → 等帧 → grab（2026-10-13 B7/M1）


def test_settle_runs_between_hide_and_grab(qapp):
    """顺序断言：hide → 等帧 → grab（等帧必须夹在中间，不能挪到 grab 之后）。

    为什么不在这里断言"抓到的帧里没有自家窗口"：那要靠真显示器与真合成器，offscreen
    下没有可断言的对象；真机结论（**本机不复现**这个故障）见
    ``pet/screen_capture/controller.py::settle_before_grab`` 的说明与
    ``.scratch/screen-capture/probe_hide_settle_grab.out``。
    """
    setup = _Setup(record_settle=True)

    assert setup.controller.start() is True

    assert setup.log[:3] == ["hide", "settle", "grab"]
    assert setup.settles == [setup.screen], "等帧必须发生在解析出的那一块屏上"


def test_settle_failure_never_breaks_the_capture(qapp):
    """等帧失败＝少等一帧，绝不能让一次截图变成"点了没反应"。"""
    def _boom(_screen):
        raise RuntimeError("no compositor today")

    setup = _Setup(settler=_boom)

    assert setup.controller.start() is True

    assert setup.frames, "等帧失败之后必须照常抓帧"
    assert setup.failed == []


def test_hide_failure_still_skips_the_settle_and_the_grab(qapp):
    """隐藏失败的出口不许白等一帧、也不许抓（顺序断言的反面）。"""
    setup = _Setup(fail_hide=True, record_settle=True)

    assert setup.controller.start() is False

    assert setup.log[:1] == ["hide"]
    assert "settle" not in setup.log and setup.settles == []
    assert setup.frames == []


def test_the_default_settler_is_the_real_one():
    """默认必须是真实现（注入件只在测试与真机对照里用）。"""
    from pet.screen_capture.controller import ScreenCaptureController, settle_before_grab

    assert ScreenCaptureController()._settler is settle_before_grab


def test_settle_prefers_the_compositor_flush_and_skips_the_sleep(qapp, monkeypatch):
    from pet.screen_capture import controller as controller_mod

    calls: list[str] = []
    monkeypatch.setattr(controller_mod, "_process_pending_events",
                        lambda: calls.append("events"))
    monkeypatch.setattr(controller_mod, "_flush_dwm",
                        lambda: (calls.append("dwm"), True)[1])
    monkeypatch.setattr(controller_mod, "_sleep_one_frame",
                        lambda _screen: calls.append("sleep"))

    elapsed = controller_mod.settle_before_grab(None)

    assert calls == ["events", "dwm"], "合成器等到了就不该再睡一帧"
    assert isinstance(elapsed, float) and elapsed >= 0


def test_settle_falls_back_to_one_refresh_frame(qapp, monkeypatch):
    from pet.screen_capture import controller as controller_mod

    calls: list[str] = []
    monkeypatch.setattr(controller_mod, "_process_pending_events",
                        lambda: calls.append("events"))
    monkeypatch.setattr(controller_mod, "_flush_dwm", lambda: False)
    monkeypatch.setattr(controller_mod, "_sleep_one_frame",
                        lambda screen: (calls.append(("sleep", screen)), True)[1])

    controller_mod.settle_before_grab("screen-token")

    assert calls == ["events", ("sleep", "screen-token")]


def test_settle_swallows_every_broken_waiter(qapp, monkeypatch):
    """三层都得吞异常：调用方只关心"等过没等过"，不关心谁坏了。"""
    from pet.screen_capture import controller as controller_mod

    def _boom(*_args, **_kwargs):
        raise RuntimeError("waiter boom")

    monkeypatch.setattr(controller_mod, "_process_pending_events", _boom)
    monkeypatch.setattr(controller_mod, "_flush_dwm", _boom)
    monkeypatch.setattr(controller_mod, "_sleep_one_frame", _boom)

    assert isinstance(controller_mod.settle_before_grab(None), float)


def test_one_frame_sleep_follows_the_screen_refresh_rate(monkeypatch):
    from pet.screen_capture import controller as controller_mod

    slept: list[float] = []
    monkeypatch.setattr(controller_mod.time, "sleep", slept.append)

    class _Screen:
        def refreshRate(self):  # noqa: N802 - 对齐 Qt 命名
            return 120.0

    assert controller_mod._sleep_one_frame(_Screen()) is True
    assert slept == [1000.0 / 120.0 / 1000.0]


def test_one_frame_sleep_is_clamped_and_survives_a_bogus_refresh_rate(monkeypatch):
    """读不到/不合理/离谱慢的刷新率都不许把一次截图卡住：夹到上限或退回 60 Hz。"""
    from pet.screen_capture import controller as controller_mod

    slept: list[float] = []
    monkeypatch.setattr(controller_mod.time, "sleep", slept.append)

    class _Slow:
        def refreshRate(self):  # noqa: N802
            return 1.0            # 1 Hz → 1000 ms，必须夹到上限

    assert controller_mod._sleep_one_frame(_Slow()) is True
    assert slept[-1] == controller_mod._SETTLE_MAX_MS / 1000.0

    class _Broken:
        refreshRate = "not a rate"

    assert controller_mod._refresh_hz(_Broken()) == controller_mod._DEFAULT_REFRESH_HZ
    assert controller_mod._refresh_hz(object()) == controller_mod._DEFAULT_REFRESH_HZ

    class _Negative:
        def refreshRate(self):  # noqa: N802
            return -30.0

    assert controller_mod._refresh_hz(_Negative()) == controller_mod._DEFAULT_REFRESH_HZ


def test_dwm_flush_is_a_noop_off_windows(monkeypatch):
    from pet.screen_capture import controller as controller_mod

    monkeypatch.setattr(controller_mod.sys, "platform", "linux")

    assert controller_mod._flush_dwm() is False


def test_start_stops_before_hiding_when_screen_recording_permission_missing(qapp):
    """macOS 权限没生效时：不藏窗口、不建覆盖层，只发一次明确失败。

    这条是"空白桌面"的止血门：旧行为会先 hide 自家窗口、抓一张黑图，再把
    全屏覆盖层 activate 到前台；用户看到的就是一块空白桌面。
    """
    calls: list[str] = []
    controller = ScreenCaptureController(
        hider=_FakeHider(calls),
        grabber=lambda _screen: _frame(),
        overlay_factory=lambda frame, screen: _FakeOverlay(frame, screen, calls),
        screen=_FakeScreen(),
        settler=lambda _screen: 0,
        permission_check=lambda: False,
        permission_request=lambda: (calls.append("permission-request"), False)[1],
    )
    failed: list[str] = []
    controller.failed.connect(failed.append)

    assert controller.start() is False
    assert failed == ["screen-permission"]
    assert calls == ["permission-request"], "权限检查必须发生在 hide/抓帧/覆盖层之前"


def test_start_can_capture_a_chosen_screen_and_keep_the_pet(qapp, monkeypatch):
    """多屏显式选屏 + 「包含桌宠」：只保留带 dshPetOverlay 属性的窗口。"""
    from PySide6.QtWidgets import QWidget

    from pet.screen_capture import controller as controller_mod

    pet_window = QWidget()
    pet_window.setProperty("dshPetOverlay", True)
    other_window = QWidget()
    pet_window.show()
    other_window.show()
    try:
        monkeypatch.setattr(controller_mod, "visible_top_level_windows",
                            lambda: [pet_window, other_window])
        chosen = _FakeScreen(80, 60)
        frames: list = []
        controller = ScreenCaptureController(
            screen=None,
            grabber=lambda screen: (frames.append(screen), _frame())[1],
            overlay_factory=lambda frame, screen: _FakeOverlay(frame, screen, []),
            settler=lambda _screen: 0,
            permission_check=lambda: True,
            permission_request=lambda: False,
        )
        assert controller.start(screen=chosen, keep_pet=True) is True
        assert frames == [chosen]
        assert controller._overlay.screen is chosen
        assert pet_window.isVisible() is True, "包含桌宠时不能把桌宠一起藏掉"
        assert other_window.isVisible() is False, "其它自家窗口仍要藏"

        controller._overlay.cancel("esc")
        assert other_window.isVisible() is True, "结束后必须还原被藏的其它窗口"
    finally:
        pet_window.close()
        other_window.close()
