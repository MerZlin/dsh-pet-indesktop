"""Query contracts at the OS seam, plus fresh-process dependency gates."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pet import desktop_query as query
from tests.desktop_query_fakes import WinCall, WindowApi

pytestmark = pytest.mark.unit


@pytest.fixture
def api(monkeypatch):
    boundary = WindowApi()
    monkeypatch.setattr(query, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(ctypes, "windll", boundary.dlls, raising=False)
    return boundary


def test_foreground_metadata_and_64_bit_handles(api):
    result = query.get_desktop_query().foreground_window()
    assert result.status == "ok"
    assert result.value == query.ForegroundInfo(api.hwnd, 1234, "editor.exe", "fixture title", (-20, 30, 800, 600))
    assert api.closed == [api.hproc]
    assert api.user32.GetForegroundWindow.restype == ctypes.wintypes.HWND
    assert api.kernel32.OpenProcess.restype == ctypes.wintypes.HANDLE
    assert not api.user32.GetWindowRect.calls
    assert len(api.user32.GetForegroundWindow.calls) == 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.value.pid = 99
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.status = "error"


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("hwnd", 0, "no_foreground"),
        ("visible", False, "not_visible"),
        ("iconic", True, "minimized"),
        ("cloaked", True, "cloaked"),
    ],
)
def test_foreground_normal_absence(api, field, value, reason):
    setattr(api, field, value)
    result = query.get_desktop_query().foreground_window()
    assert (result.status, result.value, result.reason) == ("no_window", None, reason)
    assert not api.kernel32.OpenProcess.calls


@pytest.mark.parametrize("mode", ["missing", "failure", "exception", "empty"])
def test_dwm_fallback(api, mode):
    if mode == "missing":
        del api.dlls.dwmapi
    elif mode == "failure":
        api.dwm_result = 1
    elif mode == "empty":
        api.rect = (0, 0, 0, 0)
    else:
        api.dwmapi.DwmGetWindowAttribute.callback = _error
    result = query.get_desktop_query().foreground_window()
    assert result.status == "ok"
    assert result.value.rect == (-25, 25, 810, 610)
    assert len(api.user32.GetWindowRect.calls) == 1


@pytest.mark.parametrize("raw,ok", [((0, 0, 0, 5), True), ((5, 5, 0, 0), True), ((0, 0, 5, 0), True), ((0, 0, 5, 5), False)])
def test_invalid_bounds(api, raw, ok):
    api.rect = (0, 0, 0, 0)
    api.raw_rect, api.rect_ok = raw, ok
    result = query.get_desktop_query().foreground_window()
    assert (result.status, result.value, result.reason) == ("no_window", None, "invalid_rect")
    assert not api.kernel32.OpenProcess.calls


def _error(*args):
    raise OSError("secret title /private/process/path")


@pytest.mark.parametrize(
    "library,name,closed",
    [
        ("user32", "GetForegroundWindow", False),
        ("user32", "GetWindowTextW", False),
        ("kernel32", "OpenProcess", False),
        ("kernel32", "QueryFullProcessImageNameW", True),
    ],
)
def test_foreground_error_is_safe_and_closes_handle(api, library, name, closed):
    getattr(getattr(api, library), name).callback = _error
    result = query.get_desktop_query().foreground_window()
    assert (result.status, result.value, result.reason) == ("error", None, "foreground_query_failed")
    assert api.closed == ([api.hproc] if closed else [])


@pytest.mark.parametrize("mode", ["pid_zero", "denied", "no_name"])
def test_optional_process_metadata(api, mode):
    if mode == "pid_zero":
        api.pid = 0
    elif mode == "denied":
        api.hproc = 0
    else:
        api.image_ok = False
    result = query.get_desktop_query().foreground_window()
    assert result.status == "ok"
    assert result.value.process_name == ""
    assert api.closed == ([api.hproc] if mode == "no_name" else [])


@pytest.mark.parametrize("flags,state", [(0, "hidden"), (1, "showing"), (2, "suppressed"), (3, "showing"), (4, "hidden")])
def test_cursor_states_with_real_ctypes(api, flags, state):
    def cursor(pointer):
        info = ctypes.cast(pointer, ctypes.POINTER(query.CursorInfo)).contents
        assert info.cbSize == ctypes.sizeof(query.CursorInfo)
        info.flags = flags
        return 1

    api.user32.GetCursorInfo = WinCall(cursor)
    result = query.get_desktop_query().cursor_state()
    assert (result.status, result.value) == ("ok", state)
    assert len(api.user32.GetCursorInfo.calls) == 1


@pytest.mark.parametrize("callback,status,reason", [(lambda p: 0, "unknown", "cursor_unavailable"), (_error, "error", "cursor_query_failed")])
def test_cursor_failure_is_not_hidden(api, callback, status, reason):
    api.user32.GetCursorInfo = WinCall(callback)
    result = query.get_desktop_query().cursor_state()
    assert (result.status, result.value, result.reason) == (status, None, reason)


@pytest.mark.parametrize("last,tick,expected", [(1000, 1000, 0.0), (1000, 3750, 2.75), (2000, 1000, 0.0)])
def test_idle_math_and_call_count(api, last, tick, expected):
    def last_input(pointer):
        info = ctypes.cast(pointer, ctypes.POINTER(query.LastInputInfo)).contents
        assert info.cbSize == ctypes.sizeof(query.LastInputInfo)
        info.dwTime = last
        return 1

    api.user32.GetLastInputInfo = WinCall(last_input)
    api.kernel32.GetTickCount = WinCall(lambda: tick)
    result = query.get_desktop_query().idle_time()
    assert (result.status, result.value) == ("ok", expected)
    assert isinstance(result.value, float)
    assert len(api.user32.GetLastInputInfo.calls) == len(api.kernel32.GetTickCount.calls) == 1


@pytest.mark.parametrize("callback", [lambda p: 0, _error])
def test_idle_failure_not_confused_with_zero(api, callback):
    api.user32.GetLastInputInfo = WinCall(callback)
    result = query.get_desktop_query().idle_time()
    assert result.status == "error"
    assert result.value is None


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_unsupported_queries_do_not_call_os(api, monkeypatch, platform):
    monkeypatch.setattr(query, "sys", SimpleNamespace(platform=platform))
    port = query.get_desktop_query()
    for result in (port.foreground_window(), port.cursor_state(), port.idle_time()):
        assert (result.status, result.value) == ("unsupported", None)
    assert not api.user32.GetForegroundWindow.calls


def test_legacy_facades_delegate_and_preserve_shape(monkeypatch):
    from pet import vision

    calls = []

    class Port:
        def foreground_window(self):
            calls.append("foreground")
            return query.QueryResult("ok", query.ForegroundInfo(123, 45, "editor.exe", "title", (-1, 2, 3, 4)))

        def cursor_state(self):
            calls.append("cursor")
            return query.QueryResult("ok", "suppressed")

        def idle_time(self):
            calls.append("idle")
            return query.QueryResult("ok", 1.25)

    monkeypatch.setattr(query, "get_desktop_query", lambda **kw: Port())
    monkeypatch.setattr(vision, "sys", SimpleNamespace(platform="win32"))
    assert vision.foreground_window_info() == {"hwnd": 123, "pid": 45, "process": "editor.exe", "title": "title", "rect": (-1, 2, 3, 4)}
    assert vision.get_foreground_window_rect() == (-1, 2, 3, 4)
    assert vision.foreground_app_info() == "editor.exe | title"
    assert vision.get_cursor_visibility() == "SUPPRESSED"
    assert vision.get_system_idle_seconds() == 1.25
    assert calls == ["foreground", "foreground", "foreground", "cursor", "idle"]


@pytest.mark.parametrize("status", ["no_window", "unsupported", "unknown", "error"])
def test_legacy_failure_defaults(monkeypatch, status):
    from pet import vision

    monkeypatch.setattr(vision, "sys", SimpleNamespace(platform="win32"))

    port = SimpleNamespace(**{name: lambda: query.QueryResult(status, None, "safe") for name in ("foreground_window", "cursor_state", "idle_time")})
    monkeypatch.setattr(query, "get_desktop_query", lambda **kw: port)
    assert vision.foreground_window_info() is None
    assert vision.get_foreground_window_rect() is None
    assert vision.foreground_app_info() == ""
    assert vision.get_cursor_visibility() == "UNKNOWN"
    assert vision.get_system_idle_seconds() == 0.0


def test_query_import_is_pure_and_has_no_os_queries():
    code = """
import ctypes, importlib.abc, sys, threading
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('PySide6', 'shiboken6', 'PIL', 'pet.vision', 'pet.chat', 'pet.config', 'pet.platform_win')):
            raise AssertionError('forbidden dependency: ' + fullname)
class NoCalls:
    def __getattr__(self, name):
        raise AssertionError('query during import: ' + name)
ctypes.windll = NoCalls()
sys.meta_path.insert(0, Block())
before = set(threading.enumerate())
from pet.desktop_query import get_desktop_query
get_desktop_query()
assert set(threading.enumerate()) == before
print('PURE_QUERY_IMPORT_OK')
"""
    child = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert child.returncode == 0, child.stdout + child.stderr
    assert "PURE_QUERY_IMPORT_OK" in child.stdout


@pytest.mark.integration
@pytest.mark.parametrize("shared", [False, True])
def test_real_core_queries_work_with_vision_import_blocked(tmp_path, shared):
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONIOENCODING": "utf-8"}
    child = subprocess.run(
        [sys.executable, "-m", "tests.desktop_query_core_probe", str(tmp_path), str(int(shared))],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
    )
    assert child.returncode == 0, child.stdout + child.stderr
    result = json.loads(next(line for line in child.stdout.splitlines() if line.startswith('{"query_probe"')))
    assert result["query_probe"] == "ok"
    assert result["foreground_calls"] > 0 and result["cursor_calls"] > 0
    assert result["worker_starts"] == 0
    assert result["windows"] == (2 if shared else 1)
