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


def test_process_identity_rejects_reused_pid_and_foreign_executable(tmp_path):
    identity = driver.RunIdentity(123, 10.5, str(tmp_path / "core.exe"))
    identity.check(123, 10.5, str(tmp_path / "core.exe"))
    with pytest.raises(RuntimeError, match="owned_process_identity_mismatch"):
        identity.check(123, 11.5, str(tmp_path / "core.exe"))
    with pytest.raises(RuntimeError, match="owned_process_identity_mismatch"):
        identity.check(123, 10.5, str(tmp_path / "other.exe"))


def test_next_owner_confirmation_waits_for_prior_acceptance():
    assert not driver.ready_for_next_confirmation(("official.ai-chat",), {"official.ai-chat": {"pending_transaction": None}})
    assert driver.ready_for_next_confirmation(("official.ai-chat",), {"official.ai-chat": {"pending_transaction": "tx-ai"}})
    assert driver.ready_for_next_confirmation((), {})


def test_management_dialog_accepts_current_and_legacy_product_titles():
    assert "本地功能包确认" in driver.MANAGEMENT_DIALOG_TITLES
    assert "安装本地扩展：分别预检和确认" in driver.MANAGEMENT_DIALOG_TITLES
    assert "本地官方功能包确认" in driver.MANAGEMENT_DIALOG_TITLES
    assert "安装本地官方扩展：分别预检和确认" in driver.MANAGEMENT_DIALOG_TITLES


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


def test_native_context_request_uses_qt_keyboard_route_without_activation():
    calls = []

    class Identity:
        def verify(self):
            calls.append(("verify",))

    class Target:
        identity = Identity()

        def windows(self):
            return [{"hwnd": 123}]

        def post(self, *arguments):
            calls.append(arguments)

    driver.OwnedUI.request_context_menu(Target(), 123)
    assert calls == [("verify",), (123, 0x007B, 123, -1)]


def test_keyboard_context_menu_uses_stable_body_anchor():
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtGui import QContextMenuEvent

    from pet.window import PetWindow

    calls = []

    class Window:
        _context_menu_suppressed = False
        _interaction_state = "IDLE"
        _press_global = None
        _draw_delta = QPoint(3, 4)
        _context_menu_keyboard_position = PetWindow._context_menu_keyboard_position

        def _stable_body_local_rect(self):
            return QRect(10, 20, 40, 60)

        def _is_in_interactive_area(self, point):
            calls.append(("interactive", QPoint(point)))
            return True

        def mapToGlobal(self, point):
            calls.append(("global", QPoint(point)))
            return QPoint(point) + QPoint(100, 200)

        def _show_context_menu(self, point):
            calls.append(("show", QPoint(point)))

    class Event:
        def reason(self):
            return QContextMenuEvent.Reason.Keyboard

        def accept(self):
            calls.append(("accept",))

        def pos(self):
            raise AssertionError("keyboard context must not use the invalid event position")

    PetWindow.contextMenuEvent(Window(), Event())
    assert calls == [
        ("interactive", QPoint(32, 53)),
        ("accept",),
        ("global", QPoint(32, 53)),
        ("show", QPoint(132, 253)),
    ]
