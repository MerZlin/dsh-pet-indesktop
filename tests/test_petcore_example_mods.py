from __future__ import annotations

from pathlib import Path

from petcore.contracts.host import HostPorts
from petcore.mods import feature as feature_mod
from petcore.mods import install as installer
from petcore.mods.loader import load_mods
from petcore.mods.manifest import load_manifest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "petcore-mods"
IDS = (
    "fun.divination",
    "fun.festival",
    "reminder.todo",
    "voice.chime",
    "media.music",
    "tool.ocr",
    "tool.weather",
    "tool.file-reader",
    "tool.ai-browser",
)


def test_example_mods_validate_and_load() -> None:
    assert EXAMPLES.is_dir()
    for mod_id in IDS:
        pack = EXAMPLES / mod_id
        manifest, problems = load_manifest(pack)
        assert problems == [], (mod_id, problems)
        assert manifest is not None
        assert manifest.kind == "feature"
        assert manifest.entrypoint
        entry = (pack / manifest.entrypoint.split(":", 1)[0]).resolve()
        assert entry.exists(), (mod_id, entry)

    manager = feature_mod.FeatureManager(HostPorts())
    for mod_id in IDS:
        pack = EXAMPLES / mod_id
        manifest, _ = load_manifest(pack)
        diagnostics = manager.load(
            pack=pack,
            owner=manifest.id,
            entrypoint=manifest.entrypoint,
            config={},
        )
        assert diagnostics == [], (mod_id, diagnostics)

    owners_with_menu = {feature.owner for feature, _ in manager.menu_contributions()}
    owners_with_settings = {
        feature.owner
        for feature in manager.features.values()
        for contribution in feature.definition.contributions
        if contribution.kind == "settings_page"
    }
    assert owners_with_menu <= owners_with_settings


def test_all_example_mods_install_into_fresh_root(tmp_path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    for mod_id in IDS:
        ok, message = installer.install(EXAMPLES / mod_id, root)
        assert ok, (mod_id, message)

    result = load_mods(root, (), allow_feature_mods=True)
    enabled = {mod.manifest.id for mod in result.mods if mod.enabled}
    assert set(IDS) <= enabled
    assert list(result.diagnostics) == []


def test_installer_trust_marker_loads_feature_mods_without_the_global_switch(tmp_path, monkeypatch) -> None:
    from petcore.mods import trust

    root = tmp_path / "data"
    pack = root / "mods" / "fun.divination"
    pack.parent.mkdir(parents=True)
    import shutil

    shutil.copytree(EXAMPLES / "fun.divination", pack)
    # 授信 = 位置 + 标记：这里把用户 mod 根当作「安装器投放目录」来模拟冻结包。
    monkeypatch.setattr(trust, "install_trust_root", lambda: pack.parent)
    assert trust.is_trusted(pack) is False
    blocked = load_mods(root, (), allow_feature_mods=False)
    assert all(not mod.enabled for mod in blocked.mods if mod.manifest.id == "fun.divination")

    assert trust.mark_trusted(pack) is True
    allowed = load_mods(root, (), allow_feature_mods=False)
    enabled = {mod.manifest.id for mod in allowed.mods if mod.enabled}
    assert "fun.divination" in enabled
