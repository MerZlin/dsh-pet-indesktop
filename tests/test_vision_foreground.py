"""Foreground regression uses real ctypes and fake OS calls, never the desktop.

The explicit desktop probe is tested separately here through the same OS seam.
No test replaces vision.foreground_window_info or permits None on a valid window.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
from types import SimpleNamespace

import pytest

from pet import desktop_query, vision
from tests.desktop_query_fakes import WindowApi

pytestmark = pytest.mark.unit


@pytest.fixture
def api(monkeypatch):
    api = WindowApi()
    # Patch only the module's platform discriminator, not global sys.platform.
    monkeypatch.setattr(vision, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(desktop_query, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(ctypes, "windll", api.dlls, raising=False)
    return api


def test_foreground_function_body_no_ctypes_shadow_regression(api):
    """A local import ctypes.wintypes once caused swallowed UnboundLocalError.

    Execute the original function body, with real ctypes types and buffers.
    A valid API boundary must yield metadata, not None or a mocked result.
    """
    assert vision.foreground_window_info() == {
        "hwnd": api.hwnd,
        "pid": 1234,
        "process": "editor.exe",
        "title": "fixture title",
        "rect": (-20, 30, 800, 600),
    }
    assert api.closed == [api.hproc]
    assert api.user32.GetForegroundWindow.restype is ctypes.wintypes.HWND
    assert api.kernel32.OpenProcess.restype is ctypes.wintypes.HANDLE
    assert api.kernel32.CloseHandle.argtypes == [ctypes.wintypes.HANDLE]
    assert api.user32.GetWindowRect.argtypes == [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.RECT)]
    assert not api.user32.GetWindowRect.calls  # DWM bounds take priority.


@pytest.mark.parametrize("field,value", [("hwnd", 0), ("visible", False), ("iconic", True), ("cloaked", True)])
def test_foreground_rejects_ineligible_windows(api, field, value):
    setattr(api, field, value)
    assert vision.foreground_window_info() is None
    assert not api.kernel32.OpenProcess.calls


@pytest.mark.parametrize("mode", ["missing", "exception", "hresult", "invalid_dwm_rect"])
def test_foreground_dwm_falls_back_to_user32(api, mode):
    if mode == "missing":
        del api.dlls.dwmapi
    elif mode == "exception":
        api.dwmapi.DwmGetWindowAttribute.callback = _raise_os_error
    elif mode == "hresult":
        api.dwm_result = 1
    else:
        api.rect = (0, 0, 0, 0)
    info = vision.foreground_window_info()
    assert info is not None
    assert info["rect"] == (-25, 25, 810, 610)
    assert len(api.user32.GetWindowRect.calls) == 1
    assert api.closed == [api.hproc]


@pytest.mark.parametrize("raw_rect,rect_ok", [((0, 0, 0, 10), True), ((10, 10, 0, 0), True), ((0, 0, 10, 0), True), ((0, 0, 10, 10), False)])
def test_foreground_invalid_bounds(api, raw_rect, rect_ok):
    api.rect = (0, 0, 0, 0)
    api.raw_rect, api.rect_ok = raw_rect, rect_ok
    assert vision.foreground_window_info() is None
    assert not api.kernel32.OpenProcess.calls


def _raise_os_error(*args):
    raise OSError("fixture private title /private/path must not be printed")


@pytest.mark.parametrize(
    "library,name,closed",
    [
        ("user32", "GetForegroundWindow", False),
        ("user32", "GetWindowTextW", False),
        ("kernel32", "OpenProcess", False),
        ("kernel32", "QueryFullProcessImageNameW", True),
    ],
)
def test_foreground_api_errors_follow_none_contract(api, library, name, closed):
    getattr(getattr(api, library), name).callback = _raise_os_error
    assert vision.foreground_window_info() is None
    assert api.closed == ([api.hproc] if closed else [])


@pytest.mark.parametrize("mode", ["pid_zero", "open_denied", "query_false"])
def test_foreground_unavailable_process_name_does_not_leak_handles(api, mode):
    if mode == "pid_zero":
        api.pid = 0
    elif mode == "open_denied":
        api.hproc = 0
    else:
        api.image_ok = False
    info = vision.foreground_window_info()
    assert info is not None
    assert info["process"] == ""
    assert api.closed == ([api.hproc] if mode == "query_false" else [])


def test_foreground_non_windows_does_not_call_api(api, monkeypatch):
    monkeypatch.setattr(vision, "sys", SimpleNamespace(platform="linux"))
    assert vision.foreground_window_info() is None
    assert not api.user32.GetForegroundWindow.calls


@pytest.fixture
def probe(api, monkeypatch):
    from scripts import verify_foreground_window

    monkeypatch.setattr(verify_foreground_window, "sys", SimpleNamespace(platform="win32"))
    return verify_foreground_window


def test_probe_success_uses_real_function_body(api, probe):
    result = probe.verify_foreground()
    assert (result.exit_code, result.reason, result.attempts) == (0, "foreground_verified", 1)
    assert api.closed == [api.hproc]


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("hwnd", 0, "no_foreground"),
        ("visible", False, "not_visible"),
        ("iconic", True, "minimized"),
        ("cloaked", True, "cloaked"),
        ("rect", (0, 0, 0, 0), "invalid_rect"),
    ],
)
def test_probe_environment_not_ready_is_not_pass(api, probe, field, value, reason):
    setattr(api, field, value)
    if field == "rect":
        api.raw_rect = (0, 0, 0, 0)
    result = probe.verify_foreground()
    assert (result.exit_code, result.reason) == (2, reason)
    assert not api.kernel32.OpenProcess.calls


def test_probe_unsupported_platform(probe, monkeypatch):
    monkeypatch.setattr(probe, "sys", SimpleNamespace(platform="linux"))
    result = probe.verify_foreground()
    assert (result.exit_code, result.reason) == (2, "unsupported_platform")


def test_probe_function_failure_on_eligible_window_is_failure(api, probe):
    # The OS boundary makes the real function fail after successful preflight.
    api.user32.GetWindowTextW.callback = _raise_os_error
    result = probe.verify_foreground()
    assert (result.exit_code, result.reason) == (1, "invalid_metadata")


def test_probe_preflight_error_is_not_environment_skip(api, probe):
    api.user32.GetForegroundWindow.callback = _raise_os_error
    result = probe.verify_foreground()
    assert (result.exit_code, result.reason) == (1, "preflight_failed")


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        {},
        {"hwnd": 1},
        {"hwnd": True, "pid": 2, "title": "", "process": "", "rect": (0, 0, 1, 1)},
        {"hwnd": 1, "pid": 2, "title": "", "process": "", "rect": (0, 0, 0, 1)},
    ],
)
def test_probe_metadata_validator_rejects_invalid(invalid, probe):
    assert not probe.valid_metadata(invalid, 1)


def test_probe_window_changes_retry_at_most_three_times(api, probe):
    counter = iter(range(1, 100))
    api.user32.GetForegroundWindow.callback = lambda: next(counter)
    result = probe.verify_foreground()
    assert (result.exit_code, result.reason, result.attempts) == (2, "foreground_unstable", 3)
    assert len(api.user32.GetForegroundWindow.calls) == 9


def test_probe_changed_window_then_stable_retries(api, probe):
    handles = iter([10, 10, 11, 11, 11, 11])
    api.user32.GetForegroundWindow.callback = lambda: next(handles)
    result = probe.verify_foreground()
    assert (result.exit_code, result.reason, result.attempts) == (0, "foreground_verified", 2)


def test_probe_dwm_unavailable_matches_production_fallback(api, probe):
    del api.dlls.dwmapi
    assert probe.verify_foreground().exit_code == 0


@pytest.mark.parametrize("exit_code", [0, 1, 2])
def test_probe_cli_preserves_exit_code_and_redacts_context(probe, monkeypatch, capsys, exit_code):
    result = probe.ProbeResult(exit_code, "fixed_reason", 1)
    monkeypatch.setattr(probe, "verify_foreground", lambda: result)
    assert probe.main() == exit_code
    output = capsys.readouterr().out
    assert "fixed_reason" in output
    assert "fixture" not in output
    assert "editor.exe" not in output
    assert "private" not in output


def test_probe_postflight_error_is_failure_not_skip(api, probe):
    def get_hwnd():
        if len(api.user32.GetForegroundWindow.calls) == 3:
            raise OSError("private context")
        return api.hwnd

    api.user32.GetForegroundWindow.callback = get_hwnd
    result = probe.verify_foreground()
    assert (result.exit_code, result.reason) == (1, "postflight_failed")


def test_probe_mid_call_visibility_change_is_not_accepted(api, probe):
    def visible(hwnd):
        return len(api.user32.IsWindowVisible.calls) == 1

    api.user32.IsWindowVisible.callback = visible
    result = probe.verify_foreground()
    assert (result.exit_code, result.reason) == (2, "not_visible")


def test_probe_error_output_does_not_print_exception(api, probe, capsys):
    api.user32.GetForegroundWindow.callback = _raise_os_error
    assert probe.main() == 1
    output = capsys.readouterr().out
    assert "preflight_failed" in output
    assert "private" not in output
    assert "fixture" not in output
