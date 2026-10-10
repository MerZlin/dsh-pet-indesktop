"""Phase5A Setup input validation and closed maintenance evidence."""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from pet.official_features import AI_FEATURE_ID, SCREEN_FEATURE_ID
from scripts.build_core_webm_setup import build_setup
from scripts.validate_phase5a_setup import SetupPackageValidationError, validate_official_package_inputs

ROOT = Path(__file__).resolve().parents[1]


def _manifest(owner: str, *, factory: str | None = None, format_version: int | None = None) -> dict:
    if owner == AI_FEATURE_ID:
        payload = {
            "id": owner,
            "version": "1.0.1",
            "api_version": "1",
            "core_requires": ">=4.2.1,<6.0.0",
            "platforms": [sys.platform],
            "capabilities": [
                "network.http",
                "settings.contribute",
                "menu.contribute",
                "chat.contribute",
                "files.user-selected.read",
            ],
            "factory": factory or "ai-chat/v1",
            "worker": None,
            "files": {},
        }
        if format_version is None or format_version == 2:
            payload.update({"format_version": 2, "execution_kind": "host-only", "key_id": "local-user"})
        return payload
    payload = {
        "id": owner,
        "version": "1.0.0",
        "api_version": "1",
        "core_requires": ">=4.2.1,<6.0.0",
        "platforms": [sys.platform],
        "capabilities": ["screen.capture"],
        "factory": factory or "screen-understanding/v1",
        "worker": {"path": "worker/proactive-screen-worker.exe" if sys.platform == "win32" else "worker/proactive-screen-worker", "args": []},
        "files": {},
    }
    if format_version is not None:
        payload.update({"format_version": format_version, "execution_kind": "host-worker", "key_id": "local-user"})
    return payload


def _write_manifest_zip(package_dir: Path, owner: str, *, factory: str | None = None, format_version: int | None = None) -> Path:
    package_dir.mkdir(parents=True, exist_ok=True)
    archive = package_dir / f"{owner}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("manifest.json", json.dumps(_manifest(owner, factory=factory, format_version=format_version), sort_keys=True).encode())
    return archive


def _write_official_inputs(package_dir: Path) -> None:
    _write_manifest_zip(package_dir, AI_FEATURE_ID)
    _write_manifest_zip(package_dir, SCREEN_FEATURE_ID)


def test_setup_validator_accepts_the_two_official_manifest_formats(tmp_path):
    package_dir = tmp_path / "official-packages"
    _write_official_inputs(package_dir)

    routes = validate_official_package_inputs(package_dir.resolve())

    assert routes[AI_FEATURE_ID].format_version == 2
    assert routes[AI_FEATURE_ID].execution_kind == "host-only"
    assert routes[SCREEN_FEATURE_ID].format_version == 1
    assert routes[SCREEN_FEATURE_ID].execution_kind == "host-worker"


def test_setup_validator_rejects_official_manifest_identity_mismatch(tmp_path):
    package_dir = tmp_path / "official-packages"
    _write_official_inputs(package_dir)
    _write_manifest_zip(package_dir, AI_FEATURE_ID, factory="third-party/v1")

    with pytest.raises(SetupPackageValidationError, match="factory"):
        validate_official_package_inputs(package_dir.resolve())


def test_setup_builder_validates_inputs_before_invoking_iscc(tmp_path, monkeypatch):
    import scripts.build_core_webm_setup as builder

    package_dir = tmp_path / "official-packages"
    _write_official_inputs(package_dir)
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    compiler = tmp_path / "ISCC.exe"
    compiler.write_bytes(b"inert compiler placeholder")
    calls = []
    monkeypatch.setattr(
        builder.subprocess,
        "run",
        lambda command, cwd, check: calls.append((command, cwd, check)) or SimpleNamespace(returncode=7),
    )

    result = build_setup(
        core_dir=core_dir,
        package_dir=package_dir,
        core_output_dir=tmp_path / "setup-output",
        core_version="5.0.0",
        iscc=compiler,
    )

    assert result == 7
    assert len(calls) == 1
    command, cwd, check = calls[0]
    assert f"/DPackageDir={package_dir.resolve()}" in command
    assert cwd == builder.ROOT
    assert check is False


def test_setup_builder_rejects_bad_inputs_before_iscc(tmp_path, monkeypatch):
    import scripts.build_core_webm_setup as builder

    package_dir = tmp_path / "official-packages"
    _write_official_inputs(package_dir)
    _write_manifest_zip(package_dir, SCREEN_FEATURE_ID, factory="third-party/v1")
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    compiler = tmp_path / "ISCC.exe"
    compiler.write_bytes(b"inert compiler placeholder")
    monkeypatch.setattr(builder.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ISCC must not run")))

    with pytest.raises(SetupPackageValidationError, match="factory"):
        build_setup(
            core_dir=core_dir,
            package_dir=package_dir,
            core_output_dir=tmp_path / "setup-output",
            core_version="5.0.0",
            iscc=compiler,
        )


def test_setup_embeds_same_whale_icon_as_core():
    script = (ROOT / "packaging/core_webm.iss").read_text(encoding="utf-8")
    assert r"SetupIconFile=..\assets\icon.ico" in script
    assert r"UninstallDisplayIcon={app}\dsh-pet-core-webm.exe" in script
