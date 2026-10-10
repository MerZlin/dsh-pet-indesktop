"""U01 red regressions for the portable Setup project-directory contract."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_core_setup_gate import native_gate as native_gate

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "packaging/core_webm.iss"
ISCC = Path("E:/tools/InnoSetup6/ISCC.exe")
MARKER = json.dumps({"format_version": 1, "product_id": "dsh-pet-core-webm", "data": "data"}, separators=(",", ":"), sort_keys=True)


def _section(text: str, name: str) -> str:
    match = re.search(r"(?ms)^\[" + re.escape(name) + r"\]\s*$(.*?)(?=^\[|\Z)", text)
    assert match
    return match.group(1)


def _write_product(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "portable.json").write_text(MARKER, encoding="utf-8", newline="")
    (root / "dsh-pet-core-webm.exe").write_bytes(b"generated inert core")
    (root / "data").mkdir(exist_ok=True)
    (root / "data" / "config.json").write_text("generated user data", encoding="utf-8")


def test_setup_always_shows_directory_page_and_embeds_portable_marker():
    text = SCRIPT.read_text(encoding="utf-8")
    setup = _section(text, "Setup")
    assert "DisableDirPage=no" in setup
    assert "UsePreviousAppDir=yes" in setup
    assert "UsePreviousTasks=no" in setup
    files = _section(text, "Files")
    assert "PortableMarker" in files
    assert 'DestName: "portable.json"' in files
    assert "data\\*" in files


def test_setup_uses_project_data_and_uninstall_does_not_run_dlc_transactions():
    text = SCRIPT.read_text(encoding="utf-8")
    code = _section(text, "Code")
    uninstall = code[code.index("function InitializeUninstall") :]
    assert "AcquireSetupBarrier" in code
    assert "AcquireRemovalBarrier(ExpandConstant('{app}\\data')" in uninstall
    assert "DeleteInstalledPackages" in uninstall
    assert "data\\plugins" in text
    assert "install-packages" not in uninstall
    assert "FeaturePackage" not in uninstall
    assert "staging" not in uninstall.lower()
    assert "personal" in text.lower() or "个人数据" in text


def test_builder_emits_exact_marker_define_before_invoking_iscc(tmp_path, monkeypatch):
    import scripts.build_core_webm_setup as builder

    package_dir = tmp_path / "packages"
    package_dir.mkdir()
    for owner in ("official.ai-chat", "official.screen-understanding"):
        (package_dir / f"{owner}.zip").write_bytes(b"generated package")
    core = tmp_path / "core"
    core.mkdir()
    compiler = tmp_path / "ISCC.exe"
    compiler.write_bytes(b"generated compiler")
    seen = {}

    def fake_run(command, cwd, check):
        seen["command"] = command
        marker_arg = next(item for item in command if item.startswith("/DPortableMarker="))
        seen["marker"] = Path(marker_arg.split("=", 1)[1])
        seen["bytes"] = seen["marker"].read_text(encoding="utf-8")
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setattr(builder, "validate_official_package_inputs", lambda _: {})
    monkeypatch.setattr(builder.subprocess, "run", fake_run)
    assert (
        builder.build_setup(
            core_dir=core,
            package_dir=package_dir,
            core_output_dir=tmp_path / "out",
            core_version="5.0.0",
            iscc=compiler,
        )
        == 0
    )
    assert seen["bytes"] == MARKER
    assert not seen["marker"].exists()


@pytest.mark.skipif(sys.platform != "win32" or not ISCC.is_file(), reason="local Windows/Inno compiler unavailable")
def test_native_setup_accepts_empty_and_valid_product_but_rejects_unknown_nonempty(native_gate, tmp_path):
    from tests.test_core_setup_gate import spawn, wait_result

    empty = tmp_path / "empty-project"
    result, release = tmp_path / "empty-result.txt", tmp_path / "empty-release.txt"
    child = spawn(native_gate, empty, result, release, mode="setup")
    try:
        assert wait_result(result) == "acquired"
    finally:
        release.write_text("release")
        child.communicate(timeout=30)
    assert (empty / ".core-files.lock").exists()

    unknown = tmp_path / "unknown-project"
    unknown.mkdir()
    (unknown / "important.txt").write_text("preserve")
    result, release = tmp_path / "unknown-result.txt", tmp_path / "unknown-release.txt"
    child = spawn(native_gate, unknown, result, release, mode="setup")
    try:
        assert wait_result(result) == "install_target_not_empty"
    finally:
        release.write_text("release")
        child.communicate(timeout=30)
    assert (unknown / "important.txt").read_text() == "preserve"

    product = tmp_path / "existing-product"
    _write_product(product)
    result, release = tmp_path / "product-result.txt", tmp_path / "product-release.txt"
    child = spawn(native_gate, product, result, release, mode="setup")
    try:
        assert wait_result(result) == "acquired"
    finally:
        release.write_text("release")
        child.communicate(timeout=30)
    assert (product / "data" / "config.json").read_text() == "generated user data"


def test_runtime_removal_uses_portable_project_data_root(tmp_path, monkeypatch):
    import sys as real_sys
    import types

    from pet import feature_state_io as io
    from pet import runtime_layout as api

    exe = tmp_path / "project" / "dsh-pet-core-webm.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"generated core")
    (exe.parent / "portable.json").write_text(MARKER, encoding="utf-8")
    data = exe.parent / "data"
    data.mkdir()
    monkeypatch.setattr(api, "filesystem_name", lambda path: "NTFS")
    monkeypatch.setattr(real_sys, "frozen", True, raising=False)
    monkeypatch.setattr(real_sys, "executable", str(exe))
    monkeypatch.setattr(real_sys, "argv", [str(exe), "--core-maintenance", "uninstall"])
    monkeypatch.setitem(sys.modules, "build_variant", types.SimpleNamespace(VARIANT="core-webm"))
    monkeypatch.setattr(api, "_current_layout", None)
    monkeypatch.setattr(api, "_runtime_session", None)
    monkeypatch.setattr(api, "_runtime_removal_mode", False)
    with io.open_kernel_lock(data / "core-removal.lock"):
        layout = api.initialize_for_current_build(for_core_removal=True)
        assert layout is not None
        assert layout.mode == "portable"
        assert layout.data_root == data
        assert api._runtime_session is not None
        api._runtime_session.close()


def test_uninstall_copy_does_not_blame_optional_integrations():
    text = SCRIPT.read_text(encoding="utf-8")
    messages = _section(text, "CustomMessages")
    blocked = "\n".join(line for line in messages.splitlines() if ".RemovalBlocked=" in line)
    assert "系统集成" not in blocked and "system-integration" not in blocked
    assert "空目录" in messages and "根目录" in messages


@pytest.mark.skipif(sys.platform != "win32" or not ISCC.is_file(), reason="local Windows/Inno compiler unavailable")
def test_setup_reopens_owned_retained_data_but_not_arbitrary_data(native_gate, tmp_path):
    from tests.test_core_setup_gate import spawn, wait_result

    for owned in (False, True):
        root = tmp_path / ("retained-product" if owned else "unrelated-data")
        data = root / "data"
        data.mkdir(parents=True)
        (data / "personal.txt").write_text("preserve personal data")
        if owned:
            (data / ".setup-project.json").write_text(MARKER, encoding="utf-8", newline="")
        result, release = tmp_path / f"result-{owned}", tmp_path / f"release-{owned}"
        child = spawn(native_gate, root, result, release, mode="setup")
        try:
            assert wait_result(result) == ("acquired" if owned else "install_target_not_empty")
        finally:
            release.write_text("release")
            child.communicate(timeout=30)
        assert (data / "personal.txt").read_text() == "preserve personal data"


@pytest.mark.skipif(sys.platform != "win32" or not ISCC.is_file(), reason="local Windows/Inno compiler unavailable")
def test_setup_can_retry_after_an_empty_directory_barrier(native_gate, tmp_path):
    from tests.test_core_setup_gate import spawn, wait_result

    root = tmp_path / "empty-retry"
    for attempt in range(2):
        result, release = tmp_path / f"result-{attempt}", tmp_path / f"release-{attempt}"
        child = spawn(native_gate, root, result, release, mode="setup")
        try:
            assert wait_result(result) == "acquired"
        finally:
            release.write_text("release")
            child.communicate(timeout=30)


@pytest.mark.skipif(sys.platform != "win32" or not ISCC.is_file(), reason="local Windows/Inno compiler unavailable")
def test_setup_rejects_relative_path_before_creating_it(native_gate, tmp_path):
    from tests.test_core_setup_gate import spawn, wait_result

    result, release = tmp_path / "result-relative", tmp_path / "release-relative"
    child = spawn(native_gate, "relative-generated-project", result, release, mode="setup")
    try:
        assert wait_result(result) == "install_target_invalid"
    finally:
        release.write_text("release")
        child.communicate(timeout=30)


def test_uninstall_cancellation_precedes_locks_and_maintenance():
    text = SCRIPT.read_text(encoding="utf-8")
    code = _section(text, "Code")
    initialize = code[code.index("function InitializeUninstall") : code.index("function PrepareProjectRemoval")]
    assert "ConfirmUninstallPlan" in initialize
    assert "Acquire" not in initialize and "RunCoreRemovalMaintenance" not in initialize
    execute = code[code.index("procedure CurUninstallStepChanged") : code.index("procedure DeinitializeUninstall")]
    assert execute.index("PrepareProjectRemoval") < execute.index("DeleteProjectProgramContent")
    confirmation = code[code.index("function ConfirmUninstallPlan") : code.index("function IsUninstallerArtifact")]
    assert "MsgBox(FinalQuestion, mbConfirmation, MB_YESNO or MB_DEFBUTTON2) <> IDYES then Exit" in confirmation
    assert 'DestName: ".setup-project.json"; Flags: ignoreversion uninsneveruninstall' in text


def test_directory_warning_wraps_and_reflows_controls():
    text = SCRIPT.read_text(encoding="utf-8")
    initialize = text.split("procedure InitializeWizard();", 1)[1].split("function NextButtonClick", 1)[0]
    assert "SelectDirLabel.WordWrap := True" in initialize
    assert "SelectDirLabel.AdjustHeight()" in initialize
    for control in ("SelectDirBrowseLabel", "DirEdit", "DirBrowseButton"):
        assert f"WizardForm.{control}.Top :=" in initialize


def test_uninstall_checks_selected_data_before_deleting_programs_and_aborts_on_failure():
    text = SCRIPT.read_text(encoding="utf-8")
    body = text.split("procedure CurUninstallStepChanged", 1)[1].split("procedure DeinitializeUninstall", 1)[0]
    assert body.index("CodeDeletionTreeSafe") < body.index("DeleteProjectProgramContent")
    post = body.split("CurUninstallStep = usPostUninstall", 1)[1]
    assert "Abort;" in post
