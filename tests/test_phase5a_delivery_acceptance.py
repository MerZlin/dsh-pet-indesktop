"""Bounded production acceptance driver: no ledger writes or foreign UI actions."""

import pytest

from scripts import validate_phase5a_delivery as driver


def test_clean_environment_discards_source_and_test_overrides(tmp_path):
    env = driver.clean_environment(
        {
            "PATH": "safe",
            "SystemRoot": "Windows",
            "PYTHONPATH": "repo",
            "DSH_PET_TEST": "1",
            "QT_QPA_PLATFORM": "offscreen",
            "QML_IMPORT_PATH": "repo",
            "PYSIDE_TEST": "1",
        },
        tmp_path,
    )
    assert env["QT_QPA_PLATFORM"] == "windows"
    assert env["APPDATA"] == str(tmp_path / "APPDATA")
    assert env["PATH"] == "safe"
    assert not any(key in env for key in ("PYTHONPATH", "DSH_PET_TEST", "QML_IMPORT_PATH", "PYSIDE_TEST"))


def test_context_menu_coordinates_preserve_negative_screen_position():
    assert driver.screen_lparam(-20, -4) == ((-4 & 65535) << 16) | (-20 & 65535)


@pytest.mark.parametrize("coordinates", [(32768, 0), (0, -32769)])
def test_context_menu_coordinates_reject_truncation(coordinates):
    with pytest.raises(ValueError, match="screen_coordinate_out_of_range"):
        driver.screen_lparam(*coordinates)


def test_process_identity_rejects_reused_pid_and_foreign_executable(tmp_path):
    identity = driver.RunIdentity(123, 10.5, str(tmp_path / "core.exe"))
    identity.check(123, 10.5, str(tmp_path / "core.exe"))
    with pytest.raises(RuntimeError, match="owned_process_identity_mismatch"):
        identity.check(123, 11.5, str(tmp_path / "core.exe"))
    with pytest.raises(RuntimeError, match="owned_process_identity_mismatch"):
        identity.check(123, 10.5, str(tmp_path / "other.exe"))


def test_loaded_states_require_every_selected_owner_and_no_pending():
    state = {"active": "1.0.1", "enabled": True, "pending_transaction": None, "revision": 4}
    assert driver.loaded_states({"official.ai-chat": state}, {"official.ai-chat": "1.0.1"})
    assert not driver.loaded_states({"official.ai-chat": dict(state, pending_transaction="tx")}, {"official.ai-chat": "1.0.1"})
    assert not driver.loaded_states({}, {"official.ai-chat": "1.0.1"})


def test_menu_owner_checks_do_not_count_loading_as_full_case_success():
    driver.check_menu({"退出", "AI 对话"}, ("official.ai-chat",))
    with pytest.raises(RuntimeError, match="menu_owner_mismatch"):
        driver.check_menu({"退出", "看看屏幕"}, ("official.ai-chat",))


def test_case_root_cannot_escape_owned_run(tmp_path):
    assert driver.case_root(tmp_path, "ai") == tmp_path / "ai"
    for name in ("../escape", "a/b", "", "C:\\outside"):
        with pytest.raises(ValueError, match="invalid_case_id"):
            driver.case_root(tmp_path, name)


def test_native_context_request_uses_qt_keyboard_route_not_ignored_mouse_route():
    calls = []

    class Target:
        def activate(self, handle):
            calls.append(("activate", handle))

        def post(self, *arguments):
            calls.append(arguments)

    driver.OwnedUI.request_context_menu(Target(), 123)
    assert calls == [("activate", 123), (123, 0x007B, 123, -1)]
