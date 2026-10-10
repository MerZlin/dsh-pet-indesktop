"""Windows metadata replacement must tolerate readers, not ignore failures."""
from __future__ import annotations

import os
import sys
import time

import pytest

from pet import feature_state_io as io


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file sharing semantics")
def test_atomic_write_retries_a_real_transient_windows_reader(tmp_path, monkeypatch):
    path = tmp_path / "transaction.json"
    path.write_bytes(b"old")
    reader = path.open("rb")
    replace = os.replace
    failures = []

    def observed_replace(source, target):
        try:
            return replace(source, target)
        except PermissionError as exc:
            # The actual OS denies replacing an open Python reader. Release
            # only after observing that denial, without guessing a sleep time.
            failures.append(exc.winerror)
            assert path.read_bytes() == b"old"
            reader.close()
            raise

    monkeypatch.setattr(io.os, "replace", observed_replace)
    try:
        io.atomic_write(path, b"new")
    finally:
        reader.close()
    assert failures and set(failures) <= {5, 32}
    assert path.read_bytes() == b"new"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["transaction.json"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file sharing semantics")
def test_atomic_write_does_not_hide_persistent_windows_denial(tmp_path):
    path = tmp_path / "transaction.json"
    path.write_bytes(b"old")
    start = time.monotonic()
    with path.open("rb"), pytest.raises(PermissionError):
        io.atomic_write(path, b"new")
    assert time.monotonic() - start < 5  # bounded failure, wide CI budget
    assert path.read_bytes() == b"old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["transaction.json"]


def test_atomic_write_does_not_retry_other_os_errors(tmp_path, monkeypatch):
    path = tmp_path / "transaction.json"
    path.write_bytes(b"old")
    attempts = []

    def unavailable(source, target):
        attempts.append(target)
        raise OSError("simulated unavailable filesystem")

    monkeypatch.setattr(io.os, "replace", unavailable)
    with pytest.raises(OSError, match="unavailable"):
        io.atomic_write(path, b"new")
    assert len(attempts) == 1
    assert path.read_bytes() == b"old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["transaction.json"]
