"""Read-only Win32 boundary for the verified CloudMusic playback object."""
import ctypes as c
from ctypes import wintypes as w
from dataclasses import dataclass
from functools import lru_cache
import math
from pathlib import Path
import struct
import time

from .netease_layout import file_layout

READ_ACCESS = 0x0400 | 0x0010  # QUERY_INFORMATION | VM_READ
MAX_DURATION = 7 * 24 * 60 * 60


class SnapshotUnavailable(ValueError):
    """The player is between valid states; retry on the next normal sample."""


class ModuleInfo(c.Structure):
    _fields_ = [('base', c.c_void_p), ('size', w.DWORD), ('entry', c.c_void_p)]


@dataclass(frozen=True)
class NativeSample:
    token: str
    position: float
    duration: float
    playing: bool
    sampled_at: float

    @property
    def song_id(self):
        return self.token.split('_', 1)[0]


class Native:
    """No Qt objects or COM apartments; declarations are shared across readers."""
    def __init__(self):
        if c.sizeof(c.c_void_p) != 8:
            raise OSError('unsupported_architecture')
        self.k = c.WinDLL('kernel32', use_last_error=True)
        self.p = c.WinDLL('psapi', use_last_error=True)
        self.u = c.WinDLL('user32', use_last_error=True)
        signatures = [
            (self.k.OpenProcess, [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            (self.k.CloseHandle, [w.HANDLE], w.BOOL),
            (self.k.ReadProcessMemory, [w.HANDLE, c.c_void_p, c.c_void_p, c.c_size_t, c.POINTER(c.c_size_t)], w.BOOL),
            (self.k.ProcessIdToSessionId, [w.DWORD, c.POINTER(w.DWORD)], w.BOOL),
            (self.k.GetCurrentProcessId, [], w.DWORD),
            (self.k.GetExitCodeProcess, [w.HANDLE, c.POINTER(w.DWORD)], w.BOOL),
            (self.k.QueryFullProcessImageNameW, [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)], w.BOOL),
            (self.p.EnumProcessModulesEx, [w.HANDLE, c.POINTER(w.HMODULE), w.DWORD, c.POINTER(w.DWORD), w.DWORD], w.BOOL),
            (self.p.GetModuleFileNameExW, [w.HANDLE, w.HMODULE, w.LPWSTR, w.DWORD], w.DWORD),
            (self.p.GetModuleInformation, [w.HANDLE, w.HMODULE, c.POINTER(ModuleInfo), w.DWORD], w.BOOL),
            (self.u.GetClassNameW, [w.HWND, w.LPWSTR, c.c_int], c.c_int),
            (self.u.GetWindowThreadProcessId, [w.HWND, c.POINTER(w.DWORD)], w.DWORD),
        ]
        for function, args, result in signatures:
            function.argtypes, function.restype = args, result
        self.session = w.DWORD()
        if not self.k.ProcessIdToSessionId(self.k.GetCurrentProcessId(), c.byref(self.session)):
            raise OSError('unknown_session')
        self.callback_type = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        self.u.EnumWindows.argtypes = [self.callback_type, w.LPARAM]
        self.u.EnumWindows.restype = w.BOOL

    def player_pid(self):
        """Hidden windows still count; independent player windows are ambiguous."""
        pids = set()
        failed = False
        name = c.create_unicode_buffer(256)

        @self.callback_type
        def visit(hwnd, _):
            nonlocal failed
            if self.u.GetClassNameW(hwnd, name, len(name)) and name.value == 'OrpheusBrowserHost':
                pid, session = w.DWORD(), w.DWORD()
                if (not self.u.GetWindowThreadProcessId(hwnd, c.byref(pid))
                        or not self.k.ProcessIdToSessionId(pid.value, c.byref(session))):
                    failed = True
                elif session.value == self.session.value:
                    pids.add(pid.value)
            return True

        if not self.u.EnumWindows(visit, 0) or failed:
            raise OSError('window_query_failed')
        return next(iter(pids)) if len(pids) == 1 else None

    def open(self, pid):
        handle = self.k.OpenProcess(READ_ACCESS, False, pid)
        if not handle:
            raise OSError('process_unavailable')
        return handle

    def close(self, handle):
        self.k.CloseHandle(handle)

    def alive(self, handle):
        code = w.DWORD()
        return bool(self.k.GetExitCodeProcess(handle, c.byref(code)) and code.value == 259)

    def module(self, handle):
        image = c.create_unicode_buffer(32768)
        length = w.DWORD(len(image))
        if not self.k.QueryFullProcessImageNameW(handle, 0, image, c.byref(length)):
            raise OSError('image_unavailable')
        executable = Path(image.value)
        if executable.name.lower() != 'cloudmusic.exe':
            raise OSError('wrong_process')
        modules = (w.HMODULE * 1024)()
        needed = w.DWORD()
        if (not self.p.EnumProcessModulesEx(handle, modules, c.sizeof(modules), c.byref(needed), 3)
                or needed.value > c.sizeof(modules)):
            raise OSError('modules_unavailable')
        matches = []
        for module in modules[:needed.value // c.sizeof(w.HMODULE)]:
            count = self.p.GetModuleFileNameExW(handle, module, image, len(image))
            if not count or count >= len(image):
                continue
            path = Path(image.value)
            if path.name.lower() != 'cloudmusic.dll' or path.parent != executable.parent:
                continue
            info = ModuleInfo()
            if self.p.GetModuleInformation(handle, module, c.byref(info), c.sizeof(info)):
                matches.append((str(path), info.base, info.size))
        if len(matches) != 1:
            raise OSError('module_unavailable')
        return matches[0]

    def read(self, handle, address, size):
        buffer = c.create_string_buffer(size)
        count = c.c_size_t()
        if (not self.k.ReadProcessMemory(handle, address, buffer, size, c.byref(count))
                or count.value != size):
            raise OSError('short_read')
        return buffer.raw


@lru_cache(maxsize=1)
def native_api():
    return Native()


class Reader:
    """One owner on the sampling thread; the process handle closes with that owner."""
    def __init__(self, native, pid):
        self.native, self.pid, self.handle = native, pid, None
        self.handle = native.open(pid)
        try:
            path, self.base, size = native.module(self.handle)
            self.layout = file_layout(path)
            if self.layout is None or size != self.layout.image_size:
                raise ValueError('unsupported_build')
            for offset, code in self.layout.instructions:
                if native.read(self.handle, self.base + offset, len(code)) != code:
                    raise ValueError('loaded_code_mismatch')
        except Exception:
            self.close()
            raise

    def close(self):
        if self.handle is not None:
            self.native.close(self.handle)
            self.handle = None

    def _read(self, address, size):
        data = self.native.read(self.handle, address, size)
        if len(data) != size:
            raise OSError('short_read')
        return data

    def _identity(self):
        pointer, = struct.unpack('<Q', self._read(self.base + self.layout.player_rva + 0x50, 8))
        if pointer < 0x10000 or pointer >= 0x0000800000000000:
            raise ValueError('invalid_track_pointer')
        obj = self._read(pointer + 0x10, 32)
        length, capacity = struct.unpack_from('<QQ', obj, 16)
        if not 0 < length <= 256 or capacity < length:
            raise ValueError('invalid_track_string')
        if capacity < 16:
            raw = obj[:length]
        else:
            address, = struct.unpack_from('<Q', obj)
            if not 0x10000 <= address < 0x0000800000000000:
                raise ValueError('invalid_string_pointer')
            raw = self._read(address, length)
        token = raw.decode('utf-8')
        if not token or '\0' in token:
            raise ValueError('invalid_track_token')
        return token

    def sample(self):
        token = self._identity()
        position, = struct.unpack('<d', self._read(self.base + self.layout.position_rva, 8))
        duration, = struct.unpack('<d', self._read(self.base + self.layout.player_rva + 0xa8, 8))
        state, = struct.unpack('<i', self._read(self.base + self.layout.player_rva + 0x60, 4))
        if token != self._identity():
            raise SnapshotUnavailable('track_changed_during_read')
        if (not math.isfinite(position) or not math.isfinite(duration)
                or not 0 < duration <= MAX_DURATION or not 0 <= position <= duration
                or state not in (1, 2)):
            raise SnapshotUnavailable('invalid_playback')
        return NativeSample(token, position, duration, state == 1, time.monotonic())
