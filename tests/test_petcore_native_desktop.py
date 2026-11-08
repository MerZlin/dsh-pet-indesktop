"""Native-only gates; offscreen results must never count as desktop acceptance.

Linux: QT_QPA_PLATFORM=xcb xvfb-run -a python -m pytest ...
Mac:   QT_QPA_PLATFORM=cocoa python -m pytest ...
"""
from __future__ import annotations

import ctypes as C
import subprocess
import sys
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEventLoop, QPoint, QTimer, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication, QWidget


def wait_until(predicate):
    loop = QEventLoop()
    check = QTimer()
    check.timeout.connect(lambda: loop.quit() if predicate() else None)
    budget = QTimer()
    budget.setSingleShot(True)
    budget.timeout.connect(loop.quit)
    check.start(10)
    budget.start(15000)
    if not predicate():
        loop.exec()
    assert predicate()


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_x11_server_delivers_transparent_click_to_underlying_application(app, tmp_path):
    if sys.platform != "linux" or app.platformName() != "xcb":
        pytest.skip("requires a real xcb/XWayland server (Xvfb is also supported)")
    from petcore.config import Config
    from petcore.platform.input_region import RegionInputController
    from petcore.ui.pet_window import PetWindow

    x11, xtest = C.CDLL("libX11.so.6"), C.CDLL("libXtst.so.6")
    x11.XOpenDisplay.argtypes, x11.XOpenDisplay.restype = [C.c_char_p], C.c_void_p
    x11.XSync.argtypes = [C.c_void_p, C.c_bool]
    x11.XCloseDisplay.argtypes = [C.c_void_p]
    xtest.XTestFakeMotionEvent.argtypes = [C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_ulong]
    xtest.XTestFakeButtonEvent.argtypes = [C.c_void_p, C.c_uint, C.c_bool, C.c_ulong]
    display = x11.XOpenDisplay(None)
    assert display

    ready, counter = tmp_path / "ready", tmp_path / "clicks"
    child_code = """
import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication, QWidget
app = QApplication([])
class Receiver(QWidget):
    count = 0
    def mousePressEvent(self, event):
        self.count += 1
        Path(sys.argv[2]).write_text(str(self.count))
window = Receiver()
window.setGeometry(40, 40, 500, 400)
window.show()
QApplication.sync()
Path(sys.argv[1]).touch()
app.exec()
"""
    below = subprocess.Popen([sys.executable, "-c", child_code, str(ready), str(counter)])
    def received(count):
        return counter.exists() and counter.read_text() == str(count)
    pet = PetWindow(Config(tmp_path), tmp_path, SimpleNamespace(characters=lambda: [], build_menu=lambda *_: None))
    pet.setFixedSize(120, 100)
    frame = QImage(120, 100, QImage.Format.Format_ARGB32)
    frame.fill(Qt.GlobalColor.transparent)
    for y in range(20, 80):
        for x in range(40, 100):
            frame.setPixelColor(x, y, Qt.GlobalColor.red)
    pet._image, pet._pixmap = frame, QPixmap.fromImage(frame)
    pet.move(100, 100)
    controller = None
    try:
        wait_until(ready.exists)
        pet.show()
        pet.raise_()
        QApplication.sync()
        controller = RegionInputController(pet)
        pet._input_controller = controller
        controller.resume()
        controller.backend.sync()
        def pointer(x, y, down):
            position = QWidget.mapToGlobal(pet, QPoint(x, y))
            xtest.XTestFakeMotionEvent(display, -1, position.x(), position.y(), 0)
            xtest.XTestFakeButtonEvent(display, 1, down, 0)
            x11.XSync(display, False)

        pointer(5, 5, True)
        pointer(5, 5, False)
        wait_until(lambda: received(1))
        pointer(60, 50, True)
        wait_until(lambda: pet._press_global is not None)
        pointer(5, 5, False)
        wait_until(lambda: pet._press_global is None)
        assert received(1)
        pet.set_click_through(True)
        controller.backend.sync()
        pointer(60, 50, True)
        pointer(60, 50, False)
        wait_until(lambda: received(2))
        pet.set_click_through(False)
        controller.backend.sync()
        pointer(60, 50, True)
        wait_until(lambda: pet._press_global is not None)
        pointer(60, 50, False)
        wait_until(lambda: pet._press_global is None)
    finally:
        pet.shutdown()
        pet.close()
        below.terminate()
        try:
            below.wait(timeout=10)
        except subprocess.TimeoutExpired:
            below.kill()
            below.wait(timeout=10)
        x11.XCloseDisplay(display)


def test_cocoa_pet_is_normal_window_and_replays_policy(app, tmp_path):
    if sys.platform != "darwin" or app.platformName() != "cocoa":
        pytest.skip("requires native Cocoa, not offscreen")
    from petcore.config import Config
    from petcore.platform import macos, window_policy
    from petcore.ui.pet_window import PetWindow
    pet = PetWindow(Config(tmp_path), tmp_path, SimpleNamespace(characters=lambda: [], build_menu=lambda *_: None))
    try:
        pet.show()
        QApplication.sync()
        window_policy.apply(pet)
        window = macos._window(int(pet.winId()))
        panel_class = macos._objc().objc_getClass(b"NSPanel")
        assert not macos._send(window, "isKindOfClass:", C.c_bool, (C.c_void_p,), (panel_class,))
        assert not macos._send(window, "hasShadow", C.c_bool)
        assert not macos._send(window, "canBecomeKeyWindow", C.c_bool)
        assert macos._send(window, "collectionBehavior", C.c_ulong) & 1
        assert macos.set_ignores_mouse(int(pet.winId()), True)
        assert macos._send(window, "ignoresMouseEvents", C.c_bool)
        assert macos.set_ignores_mouse(int(pet.winId()), False)
        assert not macos._send(window, "ignoresMouseEvents", C.c_bool)
    finally:
        pet.shutdown()
        pet.close()
