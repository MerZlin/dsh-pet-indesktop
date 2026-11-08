"""XFixes input-only regions via a private XCB connection.

Unlike Xlib this never installs a process-global error handler or exits on
BadWindow. Only initial negotiation waits for a reply; frame updates are
asynchronous, and errors are drained without touching Qt's X connection.
"""
from __future__ import annotations

import ctypes as C


class _Cookie(C.Structure):
    _fields_ = [("sequence", C.c_uint)]


class _Rect(C.Structure):
    _fields_ = [("x", C.c_int16), ("y", C.c_int16), ("width", C.c_uint16), ("height", C.c_uint16)]


class XFixesBackend:
    def __init__(self):
        self.connection = None
        self.xcb = C.CDLL("libxcb.so.1")
        self.fixes = C.CDLL("libxcb-xfixes.so.0")
        self.libc = C.CDLL(None)
        self.libc.free.argtypes, self.libc.free.restype = [C.c_void_p], None
        specs = {
            "xcb_connect": ([C.c_char_p, C.POINTER(C.c_int)], C.c_void_p),
            "xcb_disconnect": ([C.c_void_p], None),
            "xcb_connection_has_error": ([C.c_void_p], C.c_int),
            "xcb_generate_id": ([C.c_void_p], C.c_uint32),
            "xcb_flush": ([C.c_void_p], C.c_int),
            "xcb_poll_for_event": ([C.c_void_p], C.c_void_p),
            "xcb_get_input_focus": ([C.c_void_p], _Cookie),
            "xcb_get_input_focus_reply": ([C.c_void_p, _Cookie, C.POINTER(C.c_void_p)], C.c_void_p),
        }
        for name, (args, result) in specs.items():
            fn = getattr(self.xcb, name)
            fn.argtypes, fn.restype = args, result
        for name, args, result in (
            ("query_version", [C.c_void_p, C.c_uint32, C.c_uint32], _Cookie),
            ("query_version_reply", [C.c_void_p, _Cookie, C.POINTER(C.c_void_p)], C.c_void_p),
            ("create_region", [C.c_void_p, C.c_uint32, C.c_uint32, C.POINTER(_Rect)], _Cookie),
            ("set_window_shape_region", [C.c_void_p, C.c_uint32, C.c_uint8, C.c_int16, C.c_int16, C.c_uint32], _Cookie),
            ("destroy_region", [C.c_void_p, C.c_uint32], _Cookie),
        ):
            fn = getattr(self.fixes, "xcb_xfixes_" + name)
            fn.argtypes, fn.restype = args, result
        self.connection = self.xcb.xcb_connect(None, None)
        try:
            if not self.connection or self.xcb.xcb_connection_has_error(self.connection):
                raise RuntimeError("无法连接 X11 DISPLAY")
            error = C.c_void_p()
            cookie = self.fixes.xcb_xfixes_query_version(self.connection, 5, 0)
            reply = self.fixes.xcb_xfixes_query_version_reply(self.connection, cookie, C.byref(error))
            try:
                if error.value or not reply or C.cast(reply, C.POINTER(C.c_uint32))[2] < 2:
                    raise RuntimeError("X11 服务端不支持 XFixes 2 输入区域")
            finally:
                self.libc.free(reply)
                self.libc.free(error)
        except Exception:
            self.close()
            raise

    def _check_errors(self):
        if self.xcb.xcb_connection_has_error(self.connection):
            raise RuntimeError("X11 连接已断开")
        while event := self.xcb.xcb_poll_for_event(self.connection):
            kind, code = C.cast(event, C.POINTER(C.c_uint8))[:2]
            self.libc.free(event)
            if kind == 0 and code != 3:  # BadWindow after native recreation is harmless.
                raise RuntimeError(f"XFixes 输入区域错误 {code}")

    def sync(self):
        """Round trip only at native handle creation or in native acceptance."""
        if not self.connection:
            return
        error = C.c_void_p()
        cookie = self.xcb.xcb_get_input_focus(self.connection)
        reply = self.xcb.xcb_get_input_focus_reply(self.connection, cookie, C.byref(error))
        try:
            if not reply or error.value:
                raise RuntimeError("X11 输入区域同步失败")
            self._check_errors()
        finally:
            self.libc.free(reply)
            self.libc.free(error)

    def apply(self, xid: int, rectangles) -> None:
        if not self.connection or not xid:
            return
        self._check_errors()
        region = self.xcb.xcb_generate_id(self.connection)
        rects = (_Rect * len(rectangles))(*(_Rect(*rect) for rect in rectangles))
        self.fixes.xcb_xfixes_create_region(self.connection, region, len(rectangles), rects)
        self.fixes.xcb_xfixes_set_window_shape_region(self.connection, int(xid), 2, 0, 0, region)
        self.fixes.xcb_xfixes_destroy_region(self.connection, region)
        self.xcb.xcb_flush(self.connection)

    def close(self):
        if self.connection:
            self.xcb.xcb_disconnect(self.connection)
            self.connection = None
