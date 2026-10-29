from __future__ import annotations

import logging
import os

from PySide6.QtCore import QAbstractNativeEventFilter, QObject

logger = logging.getLogger("petcore.platform.session")

_WM_QUERYENDSESSION = 0x0011
_WM_ENDSESSION = 0x0016
_WM_WTSSESSION_CHANGE = 0x02B1
_WM_POWERBROADCAST = 0x0218
_WTS_SESSION_LOCK = 0x7
_WTS_SESSION_UNLOCK = 0x8
_PBT_APMSUSPEND = 0x0004
_PBT_APMRESUMEAUTOMATIC = 0x0012
_NOTIFY_FOR_THIS_SESSION = 0


class SessionWatcher(QObject, QAbstractNativeEventFilter):
    def __init__(
        self,
        on_session_end,
        parent: QObject | None = None,
        *,
        on_lock=None,
        on_unlock=None,
        hwnd_provider=None,
    ):
        super().__init__(parent)
        self._on_session_end = on_session_end
        self._on_lock = on_lock
        self._on_unlock = on_unlock
        self._hwnd_provider = hwnd_provider
        self._fired = False
        self._registered = 0
        self._locked = False

    def install(self) -> None:
        app = _qapp()
        if app is not None:
            app.installNativeEventFilter(self)
        self._register()

    def remove(self) -> None:
        self._unregister()
        app = _qapp()
        if app is not None:
            app.removeNativeEventFilter(self)

    def _hwnd(self) -> int:
        if not callable(self._hwnd_provider):
            return 0
        try:
            return int(self._hwnd_provider() or 0)
        except Exception:
            return 0

    def _register(self) -> None:
        if os.name != "nt" or self._registered:
            return
        hwnd = self._hwnd()
        if not hwnd:
            return
        try:
            import ctypes
            from ctypes import wintypes

            api = ctypes.windll.wtsapi32
            api.WTSRegisterSessionNotification.argtypes = [
                wintypes.HWND,
                wintypes.DWORD,
            ]
            api.WTSRegisterSessionNotification.restype = wintypes.BOOL
            if api.WTSRegisterSessionNotification(hwnd, _NOTIFY_FOR_THIS_SESSION):
                self._registered = hwnd
        except Exception:
            logger.debug("WTSRegisterSessionNotification failed", exc_info=True)

    def _unregister(self) -> None:
        if not self._registered:
            return
        hwnd, self._registered = self._registered, 0
        try:
            import ctypes
            from ctypes import wintypes

            api = ctypes.windll.wtsapi32
            api.WTSUnRegisterSessionNotification.argtypes = [wintypes.HWND]
            api.WTSUnRegisterSessionNotification.restype = wintypes.BOOL
            api.WTSUnRegisterSessionNotification(hwnd)
        except Exception:
            logger.debug("WTSUnRegisterSessionNotification failed", exc_info=True)

    def _set_locked(self, locked: bool) -> None:
        locked = bool(locked)
        if locked == self._locked:
            return
        self._locked = locked
        callback = self._on_lock if locked else self._on_unlock
        if callable(callback):
            try:
                callback()
            except Exception:
                logger.debug("session pause/resume callback failed", exc_info=True)

    def nativeEventFilter(self, event_type, message):
        if os.name != "nt" or self._fired:
            return False, 0
        try:
            import ctypes

            from ctypes import wintypes

            # HWND is pointer-sized: indexing uint32 words reads the high half
            # of HWND, not MSG.message, on 64-bit Windows.
            msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
            message_id = msg.message
            wparam = int(msg.wParam)
        except Exception:
            return False, 0
        if message_id in (_WM_QUERYENDSESSION, _WM_ENDSESSION):
            if not self._fired:
                self._fired = True
                try:
                    self._on_session_end()
                except Exception:
                    logger.debug("session end callback failed", exc_info=True)
            return False, 0
        if message_id == _WM_WTSSESSION_CHANGE:
            if wparam == _WTS_SESSION_LOCK:
                self._set_locked(True)
            elif wparam == _WTS_SESSION_UNLOCK:
                self._set_locked(False)
            return False, 0
        if message_id == _WM_POWERBROADCAST:
            if wparam == _PBT_APMSUSPEND:
                self._set_locked(True)
            elif wparam == _PBT_APMRESUMEAUTOMATIC:
                self._set_locked(False)
        return False, 0


def _qapp():
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:
        return None
    return QApplication.instance()
