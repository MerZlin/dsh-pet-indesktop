from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from petcore.config import Config
from petcore.ui.pet_window import PetWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def make_pet(tmp_path):
    host = SimpleNamespace(characters=lambda: [], build_menu=lambda *_: None)
    window = PetWindow(Config(tmp_path), tmp_path, host)
    window.setFixedSize(120, 100)
    image = QImage(120, 100, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    for y in range(20, 80):
        for x in range(40, 100):
            image.setPixelColor(x, y, QColor(100, 150, 200, 255))
    window._image = image
    window._pixmap = QPixmap.fromImage(image)
    return window


def test_input_region_matches_paint_and_keeps_transparent_corners(app, tmp_path):
    from petcore.platform.input_region import alpha_rectangles
    window = make_pet(tmp_path)
    try:
        image = window.input_image()
        rectangles = alpha_rectangles(image)
        def hit(x, y):
            return any(l <= x < l + w and t <= y < t + h for l, t, w, h in rectangles)
        assert hit(50, 50)
        assert not hit(5, 5)
        for x, y in [(40, 20), (99, 79), (100, 79), (39, 20)]:
            assert hit(x, y) == (not window.is_transparent_at(QPoint(x, y)))
        window.state.facing = "right"
        window._probe_angle = 25
        rotated = window.input_image()
        assert rotated != image
    finally:
        window.shutdown()
        window.close()


def test_viewport_paint_and_hit_share_offset(app, tmp_path):
    from petcore.platform.screens import ScreenInfo, ScreenLayout
    window = make_pet(tmp_path)
    try:
        window._viewport_enabled = True
        window._layout = ScreenLayout((ScreenInfo("test", 0, 0, 800, 600),), "test")
        window.move(-40, 0)
        assert window.x() == -40
        assert QWidget.pos(window).x() == 0
        assert window.mapFromGlobal(QPoint(10, 40)) == QPoint(50, 40)
        assert window.input_image().pixelColor(10, 40).alpha() == 255
        assert window.input_image().pixelColor(65, 40).alpha() == 0
        assert window.body_rect()[0] == window.x() + window.body_local_rect()[0]
    finally:
        window.shutdown()
        window.close()


def test_x11_input_restores_after_handle_change_and_hidden_state(app, tmp_path):
    from petcore.platform.input_region import RegionInputController
    window = make_pet(tmp_path)
    calls = []
    backend = SimpleNamespace(apply=lambda xid, rects: calls.append((xid, rects)), close=lambda: calls.append("close"))
    controller = RegionInputController(window, backend=backend)
    try:
        window.show()
        controller.resume()
        assert calls[-1][1]
        count = len(calls)
        controller.refresh()
        assert len(calls) == count
        window.click_through = True
        controller.refresh()
        assert calls[-1][1] == ()
        window.click_through = False
        controller.refresh()
        assert calls[-1][1]
        controller.native_handle_changed()
        assert len(calls) > count
        window.hide()
        controller.stop()
        assert calls[-1][1] == ()
    finally:
        controller.close()
        window.shutdown()
        window.close()
    assert calls[-1] == "close"


def test_cocoa_helpers_never_treat_offscreen_ids_as_objc_pointers(app, monkeypatch):
    from petcore.platform import macos
    monkeypatch.setattr(macos.sys, "platform", "darwin")
    assert not macos.apply_window(1, on_top=True)
    assert not macos.set_ignores_mouse(1, True)


def test_passive_policy_installs_no_native_event_filter_without_cocoa(app, monkeypatch):
    from petcore.platform import macos, window_policy
    monkeypatch.setattr(macos, "is_cocoa", lambda: False)
    window = QWidget()
    try:
        window_policy.install(window)
        assert window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        assert getattr(window, "_desktop_policy", None) is None
    finally:
        window.close()


def test_mac_capture_permission_fails_before_grabbing(app, monkeypatch):
    from petcore.platform import screenshot
    calls = []
    monkeypatch.setattr(screenshot, "ensure_capture_permission", lambda: (_ for _ in ()).throw(screenshot.ScreenshotError("permission_denied", "授权")))
    screen = SimpleNamespace(grabWindow=lambda _: calls.append("grab"))
    with pytest.raises(screenshot.ScreenshotError, match="授权"):
        screenshot.grab_screen_image(screen)
    assert calls == []


def test_mac_capture_overlay_applies_spaces_before_activation_and_replays(app, monkeypatch):
    from PySide6.QtCore import QEventLoop, QTimer
    from petcore.platform import macos
    from petcore.ui.screen.overlay import SelectionOverlay

    native_calls, activation_checks = [], []
    monkeypatch.setattr(macos, "is_cocoa", lambda: True)
    monkeypatch.setattr(macos, "apply_window", lambda handle, **policy: native_calls.append((handle, policy)))

    class ObservedOverlay(SelectionOverlay):
        def activateWindow(self):  # noqa: N802 - observe the real Qt activation path
            activation_checks.append(bool(native_calls))
            super().activateWindow()

    overlay = ObservedOverlay()
    loop = QEventLoop()
    try:
        overlay.show()
        assert activation_checks == [True]
        assert all(call[1]["passive"] is False for call in native_calls)
        assert not overlay.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        assert not overlay.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
        # Flush the queued Show policy before exercising a fresh native handle.
        QTimer.singleShot(0, loop.quit)
        loop.exec()
        overlay.hide()
        overlay.destroy()
        native_calls.clear()
        overlay.show()
        assert activation_checks == [True, True]
        assert native_calls[-1][0] == int(overlay.winId())
        before_queued = len(native_calls)
        QTimer.singleShot(0, loop.quit)
        loop.exec()
        assert len(native_calls) > before_queued
    finally:
        overlay.close()


def test_x11_disconnect_during_hide_does_not_escape_qt_event(app, tmp_path):
    from petcore.platform.input_region import RegionInputController
    window = make_pet(tmp_path)
    backend = SimpleNamespace(apply=lambda *_: (_ for _ in ()).throw(RuntimeError("disconnected")), close=lambda: None)
    controller = RegionInputController(window, backend=backend)
    window.winId()
    try:
        controller.stop()
        controller.close()
    finally:
        window.shutdown()
        window.close()


def test_mac_hidden_window_removes_itself_from_shared_poll_timer(app, tmp_path, monkeypatch):
    from petcore.platform.input_region import MacInputController, macos
    monkeypatch.setattr(macos, "set_ignores_mouse", lambda *_: True)
    window = make_pet(tmp_path)
    window.show()
    controller = MacInputController(window)
    try:
        assert controller in controller._members
        window.hide()
        assert controller not in controller._members
        window.show()
        app.processEvents()
        assert controller in controller._members
    finally:
        controller.close()
        window.shutdown()
        window.close()


def test_input_sync_reentrancy_does_not_resubmit_same_frame(app, tmp_path, monkeypatch):
    from petcore.platform.input_region import RegionInputController, QGuiApplication
    window = make_pet(tmp_path)
    window.show()
    calls = []
    controller = RegionInputController(window, backend=SimpleNamespace(
        apply=lambda *_: calls.append("apply"), sync=lambda: None, close=lambda: None))
    monkeypatch.setattr(QGuiApplication, "sync", controller.refresh)
    try:
        controller.resume()
        assert calls == ["apply"]
    finally:
        controller.close()
        window.shutdown()
        window.close()


@pytest.mark.parametrize("context_first", [True, False])
def test_mac_right_click_opens_one_menu_for_both_cocoa_event_orders(app, tmp_path, monkeypatch, context_first):
    from PySide6.QtCore import QPointF, QEvent, QTimer
    from PySide6.QtGui import QMouseEvent, QContextMenuEvent
    from PySide6.QtWidgets import QMenu
    from petcore.ui import pet_window
    window = make_pet(tmp_path)
    calls = []
    menus = []
    def menu_for(*_):
        menu = QMenu(window)
        menu.addAction("设置")
        menus.append(menu)
        calls.append("menu")
        QTimer.singleShot(0, menu.hide)
        return menu
    window.host.build_menu = menu_for
    monkeypatch.setattr(pet_window.sys, "platform", "darwin")
    def mouse(kind):
        return QMouseEvent(kind, QPointF(60, 50), QPointF(100, 100), Qt.MouseButton.RightButton,
                           Qt.MouseButton.RightButton if kind == QEvent.Type.MouseButtonPress else Qt.MouseButton.NoButton,
                           Qt.KeyboardModifier.NoModifier)
    context = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(60, 50), QPoint(100, 100))
    try:
        window.mousePressEvent(mouse(QEvent.Type.MouseButtonPress))
        if context_first:
            window.contextMenuEvent(context)
        window.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease))
        if not context_first:
            window.contextMenuEvent(context)
        assert calls == ["menu"]
    finally:
        window.shutdown()
        window.close()


def test_posix_fallback_manual_input_keeps_pet_visible(app, tmp_path, monkeypatch):
    from petcore.ui import pet_window
    window = make_pet(tmp_path)
    monkeypatch.setattr(pet_window.sys, "platform", "linux")
    window.show()
    try:
        window.set_click_through(True, persist=False)
        assert window.isVisible()
        assert window.windowFlags() & Qt.WindowType.WindowTransparentForInput
        window.set_click_through(False, persist=False)
        assert window.isVisible()
        assert not window.windowFlags() & Qt.WindowType.WindowTransparentForInput
    finally:
        window.shutdown()
        window.close()


@pytest.mark.parametrize("width", [9, 17, 31])
def test_input_bits_preserve_odd_row_padding_holes_and_alpha_threshold(app, width):
    from petcore.platform.input_region import alpha_rectangles
    image = QImage(width, 7, QImage.Format.Format_ARGB32)
    for y in range(image.height()):
        for x in range(width):
            image.setPixelColor(x, y, QColor(100, 150, 200, (0, 7, 8, 255)[(x + y) % 4]))
    rectangles = alpha_rectangles(image)
    for y in range(image.height()):
        for x in range(width):
            hit = any(l <= x < l + w and t <= y < t + h for l, t, w, h in rectangles)
            assert hit == (image.pixelColor(x, y).alpha() >= 8)


@pytest.mark.parametrize("angle,facing", [(0, "left"), (25, "right"), (-45, "left")])
def test_input_region_equals_visible_widget_alpha_after_transform(app, tmp_path, angle, facing):
    import numpy as np
    from petcore.platform.input_region import alpha_rectangles
    window = make_pet(tmp_path)
    window._probe_angle = angle
    window.state.facing = facing
    window.setFixedSize(157, 131)
    window.show()
    try:
        app.processEvents()
        visible = window.grab().toImage().convertToFormat(QImage.Format.Format_Alpha8)
        alpha = np.frombuffer(visible.constBits(), np.uint8).reshape(visible.height(), visible.bytesPerLine())[:, :visible.width()]
        mask = np.zeros(alpha.shape, dtype=bool)
        for x, y, width, height in alpha_rectangles(window.input_image()):
            mask[y:y + height, x:x + width] = True
        assert np.array_equal(mask, alpha >= 8)
    finally:
        window.shutdown()
        window.close()


@pytest.mark.parametrize("edge", ["left", "right", "top", "bottom"])
@pytest.mark.parametrize("origin,dpr", [((0, 0), 1.0), ((-800, -100), 2.0), ((800, 0), 1.5)])
def test_viewport_body_reaches_each_logical_edge_on_scaled_displays(app, tmp_path, edge, origin, dpr):
    from petcore.platform.screens import ScreenInfo, ScreenLayout
    window = make_pet(tmp_path)
    screen = ScreenInfo("target", *origin, 800, 600, dpr=dpr)
    window._viewport_enabled = True
    window._layout = ScreenLayout((screen,), "target")
    window._body_local = (40, 20, 60, 60)
    x, y = origin[0] + 200, origin[1] + 200
    if edge == "left":
        x = origin[0] - 40
    elif edge == "right":
        x = screen.right - 100
    elif edge == "top":
        y = origin[1] - 20
    else:
        y = screen.bottom - 80
    try:
        window.move(x, y)
        native = QWidget.geometry(window)
        assert screen.x <= native.x() <= screen.right - native.width()
        assert screen.y <= native.y() <= screen.bottom - native.height()
        assert window.body_rect() == (x + 40, y + 20, 60, 60)
        assert window.mapToGlobal(QPoint(40, 20)) == QPoint(x + 40, y + 20)
        assert window.mapFromGlobal(QPoint(x + 40, y + 20)) == QPoint(40, 20)
    finally:
        window.shutdown()
        window.close()
