"""Compile the real new-product script against generated inert files only."""

from __future__ import annotations

import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "packaging/core_webm.iss"
ISCC = Path("E:/tools/InnoSetup6/ISCC.exe")


def script_text():
    assert SCRIPT.is_file(), "new product Setup has no safe independent template"
    return SCRIPT.read_text(encoding="utf-8")


def section(text, name):
    return re.search(r"(?ms)^\[" + re.escape(name) + r"\]\s*$(.*?)(?=^\[|\Z)", text).group(1)


def test_new_product_never_restores_packages_or_closes_processes():
    text = script_text()
    setup = section(text, "Setup")
    options = dict(re.findall(r"(?m)^([A-Za-z0-9]+)=(.*)$", setup))
    assert options["UsePreviousSetupType"] == "no"
    assert options["DisableDirPage"] == "no"
    assert options["UsePreviousAppDir"] == "yes"
    assert options["UsePreviousTasks"] == "no"
    assert options["CloseApplications"] == "no"
    assert options["RestartApplications"] == "no"
    assert options["PrivilegesRequired"] == "lowest"
    assert "dsh-pet-standalone" not in text
    tasks = section(text, "Tasks")
    for task in ("ai", "screen"):
        assert re.search(r'Name: "' + task + r'";.*Flags: unchecked', tasks)
    assert "--core-maintenance install-packages" in text
    assert "{src}\\packages" not in text
    assert "official.ai-chat" in text and "official.screen-understanding" in text
    assert "--core-maintenance uninstall" in text
    assert not any(value in text for value in ("taskkill", "TerminateProcess", "--worker", "https://", "CodeGateTest"))


def test_overwrite_install_removes_stale_core_owned_probe_tree():
    install_delete = section(script_text(), "InstallDelete")
    assert re.search(
        r'Type: "?filesandordirs"?;\s*Name: "\{app\}\\_internal\\feature-probe"',
        install_delete,
    )
    entries = "\n".join(line for line in install_delete.splitlines() if not line.strip().startswith(";"))
    assert "data" not in entries.lower()


def test_install_and_uninstall_hold_real_barrier_before_code_changes():
    code = section(script_text(), "Code")
    assert '#include "core_code_gate.iss.inc"' in code
    install = code[code.index("function PrepareToInstall") : code.index("procedure CurStepChanged")]
    assert "AcquireSetupBarrier" in install
    post = code[code.index("procedure CurStepChanged") : code.index("procedure DeinitializeSetup")]
    assert "ssPostInstall" in post and "ReleaseCodeBarrier" in post
    uninstall = code[code.index("function InitializeUninstall") : code.index("procedure DeinitializeUninstall")]
    assert uninstall.index("AcquirePortableCodeBarrier") < uninstall.index("AcquireRemovalBarrier") < uninstall.index("RunCoreRemovalMaintenance")
    assert "Exec(" in code
    assert "ewWaitUntilTerminated" in code
    assert "ExitCode = 0" in code
    assert "ReleaseCodeBarrier" in code
    uninstall_delete = section(script_text(), "UninstallDelete")
    assert "{app}\\.core-files.lock" in uninstall_delete
    assert "{app}\\data\\core-removal.lock" in uninstall_delete
    assert "DeleteInstalledPackages" in uninstall
    assert "data\\plugins" in code
    assert "staging" not in uninstall.lower()


def test_new_template_compiles_without_running_installer(tmp_path):
    script_text()
    if sys.platform != "win32" or not ISCC.is_file():
        pytest.skip("local Windows/Inno compiler unavailable")
    core = tmp_path / "生成 Core with spaces"
    core.mkdir()
    (core / "dsh-pet-core-webm.exe").write_bytes(b"generated inert executable placeholder")
    native = core / "_internal"
    native.mkdir()
    (native / "generated.dll").write_bytes(b"generated inert native dependency")
    package_dir = tmp_path / "official packages"
    package_dir.mkdir()
    for owner in ("official.ai-chat", "official.screen-understanding"):
        with zipfile.ZipFile(package_dir / f"{owner}.zip", "w") as package:
            package.writestr("manifest.json", b"{}")
    output = tmp_path / "compile-output-only"
    marker = tmp_path / "portable.json"
    marker.write_text('{"data":"data","format_version":1,"product_id":"dsh-pet-core-webm"}', encoding="utf-8", newline="")
    command = [
        str(ISCC),
        "/Qp",
        f"/DCoreDir={core}",
        f"/DPackageDir={package_dir}",
        f"/DCoreOutputDir={output}",
        "/DCoreVersion=5.0.0",
        f"/DPortableMarker={marker}",
        str(SCRIPT),
    ]
    compiled = subprocess.run(command, capture_output=True, timeout=90)
    assert compiled.returncode == 0, (compiled.stdout + compiled.stderr).decode(errors="replace")
    assert (output / "dsh-pet-core-webm-setup.exe").is_file()
    # Deliberately never execute this generated installer: no product install,
    # registry registration, shortcuts or real package transaction is claimed.


def test_uninstaller_holds_project_barriers_through_core_cleanup():
    code = section(script_text(), "Code")
    uninstall = code[code.index("function InitializeUninstall") : code.index("procedure DeinitializeUninstall")]
    assert uninstall.index("AcquirePortableCodeBarrier") < uninstall.index("AcquireRemovalBarrier") < uninstall.index("RunCoreRemovalMaintenance")
    assert "Exec(" in code
    assert "{userappdata}\\dsh-pet-core-webm" not in uninstall
    assert "install-packages" not in uninstall
    assert "FeaturePackage" not in uninstall
    assert "staging" not in uninstall.lower()
    assert "ReleaseRemovalBarrier" in code
    teardown = code[code.index("procedure DeinitializeUninstall") :]
    assert "ReleaseRemovalBarrier" in teardown
