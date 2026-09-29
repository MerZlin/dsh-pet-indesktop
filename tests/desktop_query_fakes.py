"""Deterministic WinAPI boundary; no Qt or screen-understanding imports."""

import ctypes
import ctypes.wintypes
from types import SimpleNamespace


class WinCall:
    """ctypes-like callable: signature attributes remain assignable."""

    def __init__(self, callback):
        self.callback = callback
        self.calls = []
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        self.calls.append(args)
        return self.callback(*args)


class WindowApi:
    def __init__(self):
        self.hwnd = 0x123456789
        self.hproc = 0x234567891
        self.pid = 1234
        self.visible = True
        self.iconic = False
        self.cloaked = False
        self.rect = (-20, 30, 780, 630)
        self.raw_rect = (-25, 25, 785, 635)
        self.dwm_result = 0
        self.rect_ok = True
        self.image_ok = True
        self.closed = []
        self.user32 = SimpleNamespace(
            GetForegroundWindow=WinCall(lambda: self.hwnd),
            IsWindowVisible=WinCall(lambda hwnd: self.visible),
            IsIconic=WinCall(lambda hwnd: self.iconic),
            GetWindowRect=WinCall(self.get_rect),
            GetWindowTextLengthW=WinCall(lambda hwnd: len("  fixture title  ")),
            GetWindowTextW=WinCall(self.get_title),
            GetWindowThreadProcessId=WinCall(self.get_pid),
        )
        self.kernel32 = SimpleNamespace(
            OpenProcess=WinCall(lambda *args: self.hproc),
            QueryFullProcessImageNameW=WinCall(self.get_image),
            CloseHandle=WinCall(lambda handle: self.closed.append(handle) or 1),
        )
        self.dwmapi = SimpleNamespace(DwmGetWindowAttribute=WinCall(self.get_attribute))
        self.dlls = SimpleNamespace(user32=self.user32, kernel32=self.kernel32, dwmapi=self.dwmapi)

    @staticmethod
    def put_rect(pointer, values):
        rect = ctypes.cast(pointer, ctypes.POINTER(ctypes.wintypes.RECT)).contents
        rect.left, rect.top, rect.right, rect.bottom = values

    def get_rect(self, hwnd, pointer):
        self.put_rect(pointer, self.raw_rect)
        return self.rect_ok

    def get_attribute(self, hwnd, attribute, pointer, size):
        if attribute == 14:
            assert size == ctypes.sizeof(ctypes.c_int)
            ctypes.cast(pointer, ctypes.POINTER(ctypes.c_int)).contents.value = int(self.cloaked)
        elif attribute == 9:
            assert size == ctypes.sizeof(ctypes.wintypes.RECT)
            self.put_rect(pointer, self.rect)
        else:
            raise AssertionError("unexpected DWM attribute")
        return self.dwm_result

    def get_title(self, hwnd, buffer, size):
        buffer.value = "  fixture title  "
        return len(buffer.value)

    def get_pid(self, hwnd, pointer):
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_ulong)).contents.value = self.pid
        return 1

    def get_image(self, handle, flags, buffer, size):
        buffer.value = "/fixture/editor.exe"
        ctypes.cast(size, ctypes.POINTER(ctypes.c_ulong)).contents.value = len(buffer.value)
        return self.image_ok

    def with_watch_queries(self):
        self.style = 0
        self.exstyle = 0
        self.class_name = "FixtureWindow"
        self.monitor_rect = (-100, 0, 1024, 768)
        self.cursor_flags = 1
        self.busy_state = 1

        def cursor(pointer):
            pointer._obj.flags = self.cursor_flags
            return 1

        def class_name(hwnd, buffer, size):
            buffer.value = self.class_name
            return len(buffer.value)

        def monitor_info(monitor, pointer):
            rc = pointer._obj.rcMonitor
            rc.left, rc.top, rc.right, rc.bottom = self.monitor_rect
            return 1

        self.user32.GetCursorInfo = WinCall(cursor)
        self.user32.MonitorFromWindow = WinCall(lambda *args: 1)
        self.user32.GetWindowLongW = WinCall(lambda hwnd, key: self.style if key == -16 else self.exstyle)
        self.user32.GetClassNameW = WinCall(class_name)
        self.user32.GetMonitorInfoW = WinCall(monitor_info)
        self.dlls.shell32 = SimpleNamespace(SHQueryUserNotificationState=WinCall(lambda p: setattr(p._obj, "value", self.busy_state) or 0))
        return self
