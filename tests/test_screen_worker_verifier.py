"""Deterministic classification for the explicit, bounded frozen Worker probe."""

from pathlib import Path

import pytest

from scripts import verify_screen_worker as verify


def roots(tmp_path):
    bundle, system, core = (tmp_path / name for name in ("worker", "windows", "core"))
    return bundle, system, core


def test_actual_loaded_modules_accept_worker_and_system_only(tmp_path):
    bundle, system, core = roots(tmp_path)
    modules = [bundle / "proactive-screen-worker.exe", bundle / "_internal/python311.dll", system / "System32/kernel32.dll"]
    result = verify.check_loaded_modules(modules, bundle=bundle, system_roots=(system,), forbidden_roots=(core,))
    assert result == sorted(str(path.resolve()) for path in modules)


@pytest.mark.parametrize("relative", ["_internal/python311.dll", "_internal/PIL/_imaging.pyd", "Qt6Core.dll"])
def test_loaded_core_library_is_rejected_even_when_it_also_matches_an_allowed_root(tmp_path, relative):
    bundle, system, core = roots(tmp_path)
    modules = [bundle / "_internal/python311.dll", core / relative]
    with pytest.raises(ValueError, match="forbidden"):
        verify.check_loaded_modules(modules, bundle=bundle, system_roots=(tmp_path,), forbidden_roots=(core,))


def test_missing_python_from_own_bundle_is_not_independence_evidence(tmp_path):
    bundle, system, core = roots(tmp_path)
    with pytest.raises(ValueError, match="Python runtime"):
        verify.check_loaded_modules([system / "python311.dll"], bundle=bundle, system_roots=(system,), forbidden_roots=(core,))


def test_external_runtime_and_qt_are_rejected(tmp_path):
    bundle, system, core = roots(tmp_path)
    for extra in [tmp_path / "other/ssl.dll", bundle / "_internal/Qt6Core.dll", bundle / "_internal/PySide6/QtCore.pyd"]:
        with pytest.raises(ValueError):
            verify.check_loaded_modules([bundle / "_internal/python311.dll", extra], bundle=bundle, system_roots=(system,), forbidden_roots=(core,))


def test_runtime_directory_must_be_new_and_outside_the_bundle(tmp_path):
    bundle = tmp_path / "worker"
    bundle.mkdir()
    with pytest.raises(ValueError, match="outside"):
        verify.prepare_runtime_directory(bundle / "scratch", bundle=bundle, forbidden_roots=())
    existing = tmp_path / "runtime"
    existing.mkdir()
    with pytest.raises(FileExistsError):
        verify.prepare_runtime_directory(existing, bundle=bundle, forbidden_roots=())
    assert list(existing.iterdir()) == []


def test_runtime_directory_cannot_be_within_core(tmp_path):
    with pytest.raises(ValueError, match="outside"):
        verify.prepare_runtime_directory(tmp_path / "core/work", bundle=tmp_path / "worker", forbidden_roots=(tmp_path / "core",))


def test_expected_response_checks_correlation_and_generation():
    from pet.workers.protocol import build_message

    correct = dict(operation="cancel", generation=1, status="ok", result={"cancelled": False})
    verify.check_response(build_message("proactive-screen", "response", correct, request_id="cancel-1"), "cancel", "cancel-1")
    for field, value in [("generation", 2), ("operation", "manual_look"), ("status", "error")]:
        with pytest.raises(ValueError):
            verify.check_response(build_message("proactive-screen", "response", {**correct, field: value}, request_id="cancel-1"), "cancel", "cancel-1")
    with pytest.raises(ValueError):
        verify.check_response(build_message("proactive-screen", "response", correct, request_id="old"), "cancel", "cancel-1")
