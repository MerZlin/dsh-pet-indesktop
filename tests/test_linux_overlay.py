import pytest
from pet.linux_overlay import is_x11
from tests.codex_helpers import poll_until

from PySide6.QtCore import QRect, QPoint, QPointF
from tests.test_sprite_menu_facade import app, RichLibrary
from pet.overlay_window import OverlayWindow
from pet.pet_sprite import PetSprite
from pet.sprite_bubble import SpriteBubbleFollower
from pet.overlay_shell import OverlayShell


@pytest.mark.skipif(not is_x11(), reason="Requires native X11")
def test_virtual_desktop_bounds():
    screen = app.primaryScreen()
    if len(app.screens()) < 2:
        pytest.skip("Requires two physical monitors")
    window = OverlayWindow(screen)
    try:
        assert window.geometry() == screen.virtualGeometry()
        assert OverlayShell._local_bounds(screen).width() > screen.geometry().width()
    finally:
        window.close()


def test_bubble_anchor_tracks_actual_overlay_position():
    window = OverlayWindow()
    sprite = PetSprite(RichLibrary(), pos=QPointF(80, 100), scale=1)
    sprite.bind_clip("idle1")
    window.add_sprite(sprite)
    follower = SpriteBubbleFollower(window, sprite)
    try:
        window.move(100, 50)
        assert follower.anchor().topLeft() == window.mapToGlobal(sprite.body_rect().topLeft())
    finally:
        follower.close()
        window.close()


def input_rects(window):
    import ctypes

    x = ctypes.CDLL("libX11.so.6")
    ext = ctypes.CDLL("libXext.so.6")

    class R(ctypes.Structure):
        _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short), ("width", ctypes.c_ushort), ("height", ctypes.c_ushort)]

    x.XOpenDisplay.restype = ctypes.c_void_p
    x.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x.XCloseDisplay.argtypes = [ctypes.c_void_p]
    x.XFree.argtypes = [ctypes.c_void_p]
    ext.XShapeGetRectangles.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
    ext.XShapeGetRectangles.restype = ctypes.POINTER(R)
    d = x.XOpenDisplay(None)
    n = ctypes.c_int()
    order = ctypes.c_int()
    ptr = ext.XShapeGetRectangles(d, int(window.winId()), 2, ctypes.byref(n), ctypes.byref(order))
    result = [QRect(ptr[i].x, ptr[i].y, ptr[i].width, ptr[i].height) for i in range(n.value)]
    x.XFree(ptr)
    x.XCloseDisplay(d)
    return result


@pytest.mark.skipif(not is_x11(), reason="Requires native X11")
def test_background_is_outside_native_input_shape():
    window = OverlayWindow()
    sprite = PetSprite(RichLibrary(), pos=QPointF(80, 100), scale=0.2)
    sprite.bind_clip("idle1")
    window.add_sprite(sprite)
    try:
        window.show()
        app.processEvents()
        assert not any(r.contains(QPoint(1000, 700)) for r in input_rects(window))
        assert input_rects(window)
    finally:
        window.close()


@pytest.mark.skipif(not is_x11(), reason="Requires native X11")
def test_native_shape_moves_and_manual_passthrough():
    if app.primaryScreen().virtualGeometry().width() < 2400:
        pytest.skip("Requires a desktop wider than 2400 pixels")
    window = OverlayWindow()
    sprite = PetSprite(RichLibrary(), pos=QPointF(80, 100), scale=0.2)
    sprite.bind_clip("idle1")
    window.add_sprite(sprite)
    follower = SpriteBubbleFollower(window, sprite)
    try:
        window.show()
        app.processEvents()
        initial = input_rects(window)
        assert initial
        sprite.set_pos(QPointF(2200, 300))
        app.processEvents()
        window.repaint()
        app.processEvents()
        moved = input_rects(window)
        assert moved
        assert all(r.left() >= 2200 for r in moved)
        assert not any(r.contains(QPoint(90, 110)) for r in moved)
        follower.show("跨屏气泡", agent_title="桌宠开发", agent_status="思考中")
        poll_until(lambda: app.screenAt(follower.bubble.geometry().center()) == app.screenAt(follower.anchor().center()))
        assert app.screenAt(follower.bubble.geometry().center()) == app.screenAt(follower.anchor().center())
        window.set_mouse_through(True)
        window.repaint()
        app.processEvents()
        assert input_rects(window) == []
        window.set_mouse_through(False)
        window.repaint()
        app.processEvents()
        assert input_rects(window)
    finally:
        follower.close()
        window.close()


@pytest.mark.skipif(not is_x11(), reason="Requires native X11")
def test_live_bounds_allow_cross_screen_drag():
    if app.primaryScreen().virtualGeometry().width() < 2400:
        pytest.skip("Requires a desktop wider than 2400 pixels")
    from PySide6.QtGui import QMouseEvent

    window = OverlayWindow()
    sprite = PetSprite(RichLibrary(), pos=QPointF(1700, 300), scale=0.2)
    sprite.bind_clip("idle1")
    sprite.set_bounds(OverlayShell._local_bounds(app.primaryScreen()))
    window.add_sprite(sprite)
    try:
        window.show()
        app.processEvents()
        start = sprite.body_rect().center()
        end = QPoint(2300, start.y())

        def send(kind, local, button, buttons):
            event = QMouseEvent(kind, QPointF(local), QPointF(window.mapToGlobal(local)), button, buttons, Qt.KeyboardModifier.NoModifier)
            app.sendEvent(window, event)

        from PySide6.QtCore import QEvent, Qt

        send(QEvent.Type.MouseButtonPress, start, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
        send(QEvent.Type.MouseMove, end, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
        send(QEvent.Type.MouseButtonRelease, end, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
        assert sprite.body_rect().center().x() > 1920
    finally:
        window.close()


@pytest.mark.skipif(not is_x11(), reason="Requires native X11")
def test_transparent_pixels_inside_sprite_do_not_take_input():
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPainter

    library = RichLibrary()
    image = library._clips["idle1"].image
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.fillRect(QRect(30, 30, 30, 30), Qt.GlobalColor.red)
    painter.end()
    window = OverlayWindow()
    sprite = PetSprite(library, pos=QPointF(80, 100), scale=1)
    sprite.bind_clip("idle1")
    window.add_sprite(sprite)
    try:
        window.show()
        app.processEvents()
        rects = input_rects(window)
        assert any(r.contains(QPoint(125, 145)) for r in rects)
        assert not any(r.contains(QPoint(90, 110)) for r in rects)
        sprite.visible = False
        window.repaint()
        app.processEvents()
        assert input_rects(window) == []
    finally:
        window.close()
        assert window._linux_input.native.display is None
