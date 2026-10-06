"""Real kernel/process boundary for per-user Core replacement; generated roots only."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def core(tmp_path):
    root = tmp_path / "generated-core"
    root.mkdir()
    (root / "dsh-pet-core-webm.exe").write_bytes(b"generated Core placeholder")
    return root


def test_replacement_waits_for_all_runtime_code_handles(tmp_path):
    from pet.core_code_gate import CoreCodeGate, CoreCodeGateError

    gate = CoreCodeGate(core(tmp_path))
    first = gate.acquire_runtime()
    second = gate.acquire_runtime()
    try:
        with pytest.raises(CoreCodeGateError, match="core_in_use"):
            gate.acquire_replacement()
        first.close()
        with pytest.raises(CoreCodeGateError, match="core_in_use"):
            gate.acquire_replacement()
    finally:
        first.close()
        second.close()
    with gate.acquire_replacement() as replacement:
        assert not replacement.closed
        with pytest.raises(CoreCodeGateError, match="core_replacement_in_progress"):
            gate.acquire_runtime()
    with gate.acquire_runtime() as reopened:
        assert not reopened.closed


def test_real_child_holds_code_until_natural_process_exit(tmp_path):
    from pet.core_code_gate import CoreCodeGate, CoreCodeGateError

    root = core(tmp_path)
    code = """
import json, sys
from pathlib import Path
from pet.core_code_gate import CoreCodeGate
lock = CoreCodeGate(Path(sys.argv[1])).acquire_runtime()
print(json.dumps({'ready': True, 'inheritable': __import__('os').get_inheritable(lock._fd)}), flush=True)
sys.stdin.buffer.read(1)
# Normal child exit intentionally retains the handle; OS releases it.
"""
    child = subprocess.Popen([sys.executable, "-X", "utf8", "-u", "-c", code, str(root)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        ready = json.loads(child.stdout.readline())
        assert ready == {"ready": True, "inheritable": False}
        gate = CoreCodeGate(root)
        with pytest.raises(CoreCodeGateError, match="core_in_use"):
            gate.acquire_replacement()
        child.stdin.write(b"x")
        child.stdin.flush()
        _, errors = child.communicate(timeout=30)
        assert child.returncode == 0, errors.decode(errors="replace")
        with gate.acquire_replacement():
            assert not (root / "data").exists()
    finally:
        if child.poll() is None:
            child.stdin.write(b"x")
            child.stdin.flush()
            child.communicate(timeout=30)


def test_runtime_requires_real_core_and_replacement_supports_empty_target(tmp_path):
    from pet.core_code_gate import CoreCodeGate, CoreCodeGateError

    root = tmp_path / "new-program"
    gate = CoreCodeGate(root)
    with pytest.raises(CoreCodeGateError, match="core_executable_missing"):
        gate.acquire_runtime()
    with gate.acquire_replacement():
        assert root.is_dir()
        (root / "dsh-pet-core-webm.exe").write_bytes(b"generated replacement")
    with gate.acquire_runtime():
        pass


def test_hardlinked_lock_is_not_accepted_as_code_authority(tmp_path):
    from pet.core_code_gate import CoreCodeGate, CoreCodeGateError

    root = core(tmp_path)
    fixture = tmp_path / "outside-generated-lock"
    fixture.write_bytes(b"generated")
    os.link(fixture, root / ".core-files.lock")
    with pytest.raises(CoreCodeGateError, match="code_boundary_invalid"):
        CoreCodeGate(root).acquire_replacement()
    assert fixture.read_bytes() == b"generated"


def test_linked_code_root_is_rejected_without_writing_target(tmp_path):
    from pet.core_code_gate import CoreCodeGate, CoreCodeGateError

    target = core(tmp_path)
    link = tmp_path / "linked-core"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("host denies generated symlink creation")
    with pytest.raises(CoreCodeGateError, match="code_boundary_invalid"):
        CoreCodeGate(link).acquire_replacement()
    assert not (target / ".core-files.lock").exists()
