"""Compile/run only a generated no-install gate fixture, not the product Setup.

InitializeSetup exits before installation/registry/file-copy work. The fixture
only touches its generated code lock and result/control files. Production
Setup never contains these test controls.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ISCC = Path("E:/tools/InnoSetup6/ISCC.exe")
INCLUDE = ROOT / "packaging/core_code_gate.iss.inc"
REMOVAL_INCLUDE = ROOT / "packaging/core_removal_gate.iss.inc"


def wait_result(path):
    deadline = time.monotonic() + 30
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)  # Event-file polling, not a fixed ordering assertion.
    assert path.exists(), "native gate fixture produced no result"
    return path.read_text(encoding="utf-8-sig").strip()


@pytest.fixture
def native_gate(tmp_path):
    assert INCLUDE.is_file(), "missing production native code barrier"
    if sys.platform != "win32" or not ISCC.is_file():
        pytest.skip("local Windows/Inno compiler unavailable")
    # No installation, uninstall registration, personal data or shortcuts.
    script = tmp_path / "generated-gate.iss"
    script.write_text(
        f'''[Setup]
AppName=Generated gate fixture only
AppVersion=1.0.0
DefaultDirName={tmp_path.as_posix()}/never-installed
OutputDir={tmp_path.as_posix()}/compiled
OutputBaseFilename=generated-code-gate
PrivilegesRequired=lowest
Uninstallable=no
CreateUninstallRegKey=no
Compression=none
[Code]
#include "{INCLUDE.as_posix()}"
#include "{REMOVAL_INCLUDE.as_posix()}"
function InitializeSetup(): Boolean;
var Root, ResultPath, ReleasePath, Reason, Mode: String; I: Integer; Acquired: Boolean;
begin
  Root := ExpandConstant('{{param:ROOT|}}');
  ResultPath := ExpandConstant('{{param:RESULT|}}');
  ReleasePath := ExpandConstant('{{param:RELEASE|}}');
  Mode := ExpandConstant('{{param:MODE|code}}');
  if Mode = 'removal' then Acquired := AcquireRemovalBarrier(Root, Reason)
  else if Mode = 'setup' then Acquired := AcquireSetupBarrier(Root, Reason)
  else if Mode = 'portable' then Acquired := AcquirePortableCodeBarrier(Root, Reason)
  else Acquired := AcquireCodeBarrier(Root, Reason);
  if Acquired then begin
    SaveStringToFile(ResultPath, 'acquired', False);
    I := 0;
    while (not FileExists(ReleasePath)) and (I < 1500) do begin
      Sleep(20); I := I + 1;
    end;
    ReleaseRemovalBarrier();
    ReleaseCodeBarrier();
  end else SaveStringToFile(ResultPath, Reason, False);
  Result := False;
end;
''',
        encoding="utf-8",
    )
    built = subprocess.run([str(ISCC), "/Qp", str(script)], capture_output=True, timeout=60)
    assert built.returncode == 0, (built.stdout + built.stderr).decode(errors="replace")
    return tmp_path / "compiled/generated-code-gate.exe"


def spawn(exe, root, result, release, *, mode="code"):
    return subprocess.Popen(
        [str(exe), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/ROOT={root}", f"/RESULT={result}", f"/RELEASE={release}", f"/MODE={mode}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_native_installer_and_python_use_same_kernel_range(native_gate, tmp_path):
    from pet.core_code_gate import CoreCodeGate, CoreCodeGateError

    root = tmp_path / "generated-core"
    root.mkdir()
    (root / "dsh-pet-core-webm.exe").write_bytes(b"generated placeholder")
    gate = CoreCodeGate(root)
    result, release = tmp_path / "result-a.txt", tmp_path / "release-a.txt"
    with gate.acquire_runtime():
        child = spawn(native_gate, root, result, release)
        assert wait_result(result) == "core_in_use"
        child.communicate(timeout=30)
    result, release = tmp_path / "result-b.txt", tmp_path / "release-b.txt"
    child = spawn(native_gate, root, result, release)
    try:
        assert wait_result(result) == "acquired"
        with pytest.raises(CoreCodeGateError, match="core_replacement_in_progress"):
            gate.acquire_runtime()
    finally:
        release.write_text("generated release event")
        child.communicate(timeout=30)
    with gate.acquire_runtime():
        pass
    assert not (tmp_path / "never-installed").exists()


def test_native_gate_rejects_hardlinked_lock_without_touching_target(native_gate, tmp_path):
    root = tmp_path / "generated-core"
    root.mkdir()
    fixture = tmp_path / "generated-outside.txt"
    fixture.write_text("preserve generated contents")
    os.link(fixture, root / ".core-files.lock")
    result, release = tmp_path / "result.txt", tmp_path / "release.txt"
    child = spawn(native_gate, root, result, release)
    assert wait_result(result) == "code_boundary_invalid"
    child.communicate(timeout=30)
    assert fixture.read_text() == "preserve generated contents"


def test_native_gate_rejects_hardlinked_core_executable(native_gate, tmp_path):
    root = tmp_path / "generated-core"
    root.mkdir()
    fixture = tmp_path / "generated-outside-core.exe"
    fixture.write_bytes(b"generated executable contents")
    os.link(fixture, root / "dsh-pet-core-webm.exe")
    result, release = tmp_path / "result.txt", tmp_path / "release.txt"
    child = spawn(native_gate, root, result, release)
    try:
        assert wait_result(result) == "code_boundary_invalid"
    finally:
        release.write_text("release generated fixture")
        child.communicate(timeout=30)
    assert fixture.read_bytes() == b"generated executable contents"


def test_native_setup_rejects_drive_root(native_gate, tmp_path):
    root = Path(tmp_path.anchor)
    result, release = tmp_path / "root-result.txt", tmp_path / "root-release.txt"
    child = spawn(native_gate, root, result, release, mode="setup")
    assert wait_result(result) == "install_target_root"
    child.communicate(timeout=30)


def test_native_setup_rejects_reparse_target(native_gate, tmp_path):
    target = tmp_path / "real-project"
    target.mkdir()
    link = tmp_path / "reparse-project"
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        # Junctions exercise the same real reparse boundary without requiring
        # the Windows symbolic-link privilege or developer mode.
        created = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path $env:DSH_TEST_JUNCTION -Target $env:DSH_TEST_TARGET | Out-Null",
            ],
            env=dict(os.environ, DSH_TEST_JUNCTION=str(link), DSH_TEST_TARGET=str(target)),
            capture_output=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert created.returncode == 0, f"symlink unavailable ({exc}); junction creation failed: {created.stderr!r}"
    result, release = tmp_path / "reparse-result.txt", tmp_path / "reparse-release.txt"
    child = spawn(native_gate, link, result, release, mode="setup")
    assert wait_result(result) == "install_target_reparse"
    child.communicate(timeout=30)


def test_native_gate_rejects_malformed_portable_target_without_touching_data(native_gate, tmp_path):
    root = tmp_path / "generated portable 含空格"
    root.mkdir()
    (root / "portable.json").write_text("generated marker")
    (root / "data").mkdir()
    (root / "data/generated.txt").write_text("preserved")
    result, release = tmp_path / "result.txt", tmp_path / "release.txt"
    child = spawn(native_gate, root, result, release)
    try:
        assert wait_result(result) == "portable_marker_invalid"
    finally:
        release.write_text("release generated fixture")
        child.communicate(timeout=30)
    assert not (root / ".core-files.lock").exists()
    assert (root / "data/generated.txt").read_text() == "preserved"


def test_native_gate_rejects_nested_code_hardlink(native_gate, tmp_path):
    root = tmp_path / "generated-core"
    nested = root / "_internal" / "nested"
    nested.mkdir(parents=True)
    (root / "dsh-pet-core-webm.exe").write_bytes(b"generated executable")
    outside = tmp_path / "generated-outside.dll"
    outside.write_bytes(b"preserve generated outside dependency")
    os.link(outside, nested / "dependency.dll")
    result, release = tmp_path / "result.txt", tmp_path / "release.txt"
    child = spawn(native_gate, root, result, release)
    try:
        assert wait_result(result) == "code_boundary_invalid"
    finally:
        release.write_text("release", encoding="utf-8")
        child.communicate(timeout=30)
    assert outside.read_bytes() == b"preserve generated outside dependency"


def test_native_root_fence_blocks_other_core_copy_through_delete_window(native_gate, tmp_path):
    from pet.runtime_layout import RuntimeLayout, RuntimeLayoutError

    exe = tmp_path / "other-copy" / "dsh-pet-core-webm.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"generated inert executable")
    layout = RuntimeLayout.discover(exe, appdata=tmp_path / "generated-appdata")
    result, release = tmp_path / "first-result.txt", tmp_path / "first-release.txt"
    with layout.acquire_session():
        child = spawn(native_gate, layout.data_root, result, release, mode="removal")
        assert wait_result(result) == "data_root_in_use"
        child.communicate(timeout=30)
    result, release = tmp_path / "second-result.txt", tmp_path / "second-release.txt"
    child = spawn(native_gate, layout.data_root, result, release, mode="removal")
    try:
        assert wait_result(result) == "acquired"
        # The maintenance child can finish; this parent-held kernel handle is
        # still present during the subsequent Core-deletion window.
        with pytest.raises(RuntimeLayoutError, match="core_removal_in_progress"):
            layout.acquire_session()
        with pytest.raises(RuntimeLayoutError, match="core_removal_in_progress"):
            RuntimeLayout.discover(exe, appdata=layout.data_root.parent)
    finally:
        release.write_text("generated release event")
        child.communicate(timeout=30)
    with layout.acquire_session():
        pass
    assert not (tmp_path / "never-installed").exists()


def test_native_root_fence_rejects_hardlink_without_data_tree_reads(native_gate, tmp_path):
    root = tmp_path / "generated-data"
    root.mkdir()
    outside = tmp_path / "generated-outside"
    outside.write_text("preserve generated outside file")
    os.link(outside, root / "core-removal.lock")
    result, release = tmp_path / "result.txt", tmp_path / "release.txt"
    child = spawn(native_gate, root, result, release, mode="removal")
    assert wait_result(result) == "data_boundary_invalid"
    child.communicate(timeout=30)
    assert outside.read_text() == "preserve generated outside file"
