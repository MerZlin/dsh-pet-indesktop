from __future__ import annotations

import importlib.util
import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def fcitx_artifact(tmp_path):
    plugin = tmp_path / "fcitx.so"
    plugin.write_bytes(b"built-plugin")
    provenance = {"schema": 1, "qt_version": "6.11.3", "qt_source_tag": "v6.11.3",
                  "qt_source_commit": "a" * 40, "fcitx_source_tag": "5.1.14",
                  "fcitx_source_commit": "b" * 40,
                  "plugin_sha256": hashlib.sha256(plugin.read_bytes()).hexdigest()}
    # Qt's QPluginMetaData::Header has no patch field (observed Qt 6.11.3).
    metadata = {"version": 396032, "MetaData": {"Keys": ["fcitx", "fcitx5"]}}
    return plugin, provenance, metadata


def test_fcitx_accepts_real_patchless_metadata_with_exact_build_provenance(fcitx_artifact):
    from petcore.platform.bundle import fcitx_problems
    plugin, provenance, metadata = fcitx_artifact
    assert fcitx_problems(plugin, metadata, "6.11.3", provenance) == []


@pytest.mark.parametrize("change", ["qt", "abi", "keys", "digest", "commit", "tag", "missing"])
def test_fcitx_rejects_wrong_or_unverifiable_build(fcitx_artifact, change):
    from petcore.platform.bundle import fcitx_problems
    plugin, provenance, metadata = fcitx_artifact
    if change == "qt":
        provenance["qt_version"] = "6.11.2"
    elif change == "abi":
        metadata["version"] = 0x060A00
    elif change == "keys":
        metadata["MetaData"]["Keys"] = ["ibus"]
    elif change == "digest":
        plugin.write_bytes(b"different-plugin")
    elif change == "commit":
        provenance["qt_source_commit"] = "unknown"
    elif change == "tag":
        provenance["qt_source_tag"] = "v6.11.2"
    else:
        provenance = {}
    assert fcitx_problems(plugin, metadata, "6.11.3", provenance)


def builder():
    spec = importlib.util.spec_from_file_location("posix_builder", ROOT / "scripts/build_petcore_posix.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_posix_manifest_carries_runtime_assets_without_windows_binaries():
    from petcore.platform.bundle import DATA_DIRS, DATA_FILES, resource_problems
    assert "integrations" in DATA_DIRS
    assert "petcore/builtin" in DATA_DIRS
    assert "resources/rapidocr_models" in DATA_DIRS
    assert "resources/presentmon" not in DATA_DIRS
    assert {"LICENSE", "THIRD_PARTY_NOTICES.md"} <= set(DATA_FILES)
    assert resource_problems(ROOT) == []


def test_bundle_gate_names_missing_bridge_and_license(tmp_path):
    from petcore.platform.bundle import resource_problems
    problems = resource_problems(tmp_path)
    assert any("integrations/dsh-pet-bridge/index.js" in p for p in problems)
    assert any("LICENSE" in p for p in problems)


def test_build_host_gate_refuses_incompatible_runtime():
    build = builder()
    assert build.host_problems("linux", "x86_64", "6.11.3", "22.04") == []
    assert build.host_problems("darwin", "arm64", "6.12.0", "14.4") == []
    assert build.host_problems("linux", "x86_64", "6.12.0", "24.04")
    assert build.host_problems("darwin", "x86_64", "6.12.0", "15.0")
    assert build.host_problems("darwin", "arm64", "6.12.0", "14.3")


def test_glibc_gate_checks_versions_numerically_and_architecture():
    build = builder()
    header = bytearray(64)
    header[:6] = b"\x7fELF\x02\x01"
    header[18:20] = (62).to_bytes(2, "little")
    assert build.elf_problems(bytes(header), "GLIBC_2.9 GLIBC_2.34 GLIBC_2.35") == []
    assert build.elf_problems(bytes(header), "GLIBC_2.36")
    header[18:20] = (183).to_bytes(2, "little")
    assert build.elf_problems(bytes(header), "GLIBC_2.17")


def test_build_requires_fresh_output_without_deleting_existing_files(tmp_path):
    build = builder()
    output = tmp_path / "candidate"
    build.prepare_output(output)
    marker = output / "keep"
    marker.write_text("user build", encoding="utf-8")
    with pytest.raises(ValueError, match="empty|fresh"):
        build.prepare_output(output)
    assert marker.read_text() == "user build"


def test_source_snapshot_supports_exported_tree_without_git(tmp_path):
    build = builder()
    source = tmp_path / "源码 有空格"
    source.mkdir()
    (source / "main.py").write_text("print('pet')\n", encoding="utf-8")
    snapshot = build.source_snapshot(source, ("main.py",))
    assert snapshot["sha"] is None
    assert snapshot["dirty"] is None
    assert snapshot["files"][0]["path"] == "main.py"
    assert len(snapshot["source_sha256"]) == 64
    assert build.source_snapshot(source, ("main.py",)) == snapshot


def test_source_snapshot_identifies_changed_content_and_missing_inputs(tmp_path):
    build = builder()
    code = tmp_path / "main.py"
    code.write_text("first", encoding="utf-8")
    before = build.source_snapshot(tmp_path, ("main.py",))
    code.write_text("second", encoding="utf-8")
    after = build.source_snapshot(tmp_path, ("main.py",))
    assert before["source_sha256"] != after["source_sha256"]
    assert before["files"][0]["sha256"] != after["files"][0]["sha256"]
    with pytest.raises(FileNotFoundError):
        build.source_snapshot(tmp_path, ("missing.py",))


def test_mac_bundle_keeps_user_writes_outside_signed_resources(monkeypatch, tmp_path):
    from petcore import paths
    bundle = tmp_path / "Pet 中文.app"
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    monkeypatch.setattr(paths.sys, "executable", str(bundle / "Contents/MacOS/dsh-pet-core"))
    assert paths.install_root() == tmp_path
    assert not paths.default_data_root(portable=True).is_relative_to(bundle)
    assert not paths.default_data_root().is_relative_to(bundle)
    assert paths.install_mods_dir() == bundle / "Contents/Resources/mods"
