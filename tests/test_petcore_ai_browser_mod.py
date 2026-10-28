# -*- coding: utf-8 -*-
"""`tool.ai-browser` 扩展：纯逻辑、清单、加载与降级路径（不启动 Qt、不联网）。"""

from __future__ import annotations

import importlib
import re
import sys
import types
from pathlib import Path

import pytest

from petcore.contracts.host import HostPorts
from petcore.mods import feature as feature_mod
from petcore.mods.manifest import load_manifest

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "examples" / "petcore-mods" / "tool.ai-browser"
MOD_ID = "tool.ai-browser"
PACKAGE = "petcore_feature_tool_ai_browser"
NO_QT_MODULES = ("sites", "engine", "external", "runtime")


@pytest.fixture(scope="module")
def mod():
    factory = feature_mod.import_entrypoint(PACK, "host/__init__.py:create_host", MOD_ID)
    assert callable(factory)
    return types.SimpleNamespace(
        sites=importlib.import_module(f"{PACKAGE}.sites"),
        engine=importlib.import_module(f"{PACKAGE}.engine"),
        external=importlib.import_module(f"{PACKAGE}.external"),
        runtime=importlib.import_module(f"{PACKAGE}.runtime"),
    )


class _Config:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def get(self, key, default=None):
        return self.data.get(key, default)


class _Presentation:
    def __init__(self):
        self.calls = []

    def say(self, text, *, duration_ms=4000, force=False, subtitle=""):
        self.calls.append((text, force))
        return True


def _runtime(mod, config=None):
    ports = types.SimpleNamespace(
        config=_Config(config),
        presentation=_Presentation(),
        storage=None,
        scheduler=None,
    )
    instance = mod.runtime.BrowserRuntime(types.SimpleNamespace(ports=ports))
    return instance, ports


# ---- 站点表与白名单


def test_site_table_covers_the_named_domestic_models(mod):
    ids = {site.id for site in mod.sites.SITES}
    assert {"deepseek", "doubao", "qianwen", "kimi"} <= ids
    for site in mod.sites.SITES:
        assert mod.sites.is_allowed(site.url), site.id
        assert mod.sites.site_for_url(site.url) is site


def test_resolve_target_accepts_keywords_and_allowlisted_urls(mod):
    cases = {
        "kimi": "kimi",
        "豆包": "doubao",
        "通义千问": "qianwen",
        "DeepSeek": "deepseek",
    }
    for text, site_id in cases.items():
        site, url = mod.sites.resolve_target(text)
        assert site is not None and site.id == site_id, text
        assert url == site.url

    site, url = mod.sites.resolve_target("chat.deepseek.com")
    assert site is not None and site.id == "deepseek"
    assert url == "https://chat.deepseek.com"


def test_resolve_target_refuses_everything_outside_the_allowlist(mod):
    for text in (
        "example.com",
        "https://bing.com",
        "javascript:alert(1)",
        "file:///C:/Windows/System32",
        "bing 搜索",
        "",
    ):
        assert mod.sites.resolve_target(text) == (None, ""), text


def test_host_allowed_uses_dot_boundaries(mod):
    assert mod.sites.host_allowed("chat.deepseek.com")
    assert mod.sites.host_allowed("DEEPSEEK.COM.")
    assert not mod.sites.host_allowed("evil-deepseek.com")
    assert not mod.sites.host_allowed("deepseek.com.evil.cn")
    assert not mod.sites.host_allowed("")
    assert mod.sites.host_allowed("sub.my-site.cn", ("my-site.cn",))
    assert not mod.sites.host_allowed("sub.my-site.cn")


def test_normalize_extra_accepts_messy_user_input(mod):
    assert mod.sites.normalize_extra(
        " https://A.Example.com:8443/x, *.foo.cn；bar.com\nA.Example.com "
    ) == ("a.example.com", "foo.cn", "bar.com")
    assert mod.sites.normalize_extra("") == ()
    assert mod.sites.normalize_extra(("x.cn", "x.cn", "")) == ("x.cn",)


def test_is_allowed_rejects_non_http_schemes(mod):
    assert mod.sites.is_allowed("https://www.kimi.com/chat")
    assert not mod.sites.is_allowed("javascript:void(0)")
    assert not mod.sites.is_allowed("ftp://www.kimi.com")


# ---- 清单与加载


def test_manifest_is_valid_and_contributions_pair_up(mod):
    manifest, problems = load_manifest(PACK)
    assert problems == []
    assert manifest is not None
    assert manifest.id == MOD_ID
    assert manifest.kind == "feature"
    assert manifest.api_version == "1"
    assert manifest.entrypoint == "host/__init__.py:create_host"

    manager = feature_mod.FeatureManager(HostPorts())
    diagnostics = manager.load(
        pack=PACK,
        owner=manifest.id,
        entrypoint=manifest.entrypoint,
        config={},
    )
    assert diagnostics == []
    features = list(manager.features.values())
    assert len(features) == 1
    kinds = {item.kind for item in features[0].definition.contributions}
    assert {"menu", "settings_page"} <= kinds
    assert manager.command(MOD_ID, "open") is not None


def test_runtime_and_pure_modules_stay_free_of_qt_and_core_private_packages(mod):
    banned = re.compile(
        r"^\s*(?:from|import)\s+(?:PySide6|petcore\.(?:net|api|account|platform))\b",
        re.MULTILINE,
    )
    for name in NO_QT_MODULES:
        text = (PACK / "host" / f"{name}.py").read_text(encoding="utf-8")
        assert banned.search(text) is None, name


# ---- 运行时闸门与降级


def test_disabled_switch_only_says_so_and_never_opens_a_window(mod):
    instance, ports = _runtime(mod, {"ai_browser_enabled": False})
    instance.open(slot=0)
    assert ports.presentation.calls
    assert "关闭" in ports.presentation.calls[0][0]
    assert f"{PACKAGE}.browser_window" not in sys.modules


def test_missing_engine_falls_back_to_edge_and_says_why(mod, monkeypatch):
    instance, ports = _runtime(
        mod,
        {
            "ai_browser_enabled": True,
            "ai_browser_default_site": "kimi",
        },
    )
    monkeypatch.setattr(mod.engine, "engine_available", lambda: False)
    opened = {}

    def fake_open(url):
        opened["url"] = url
        return "edge"

    monkeypatch.setattr(mod.external, "open_external", fake_open)
    instance.open(slot=None)
    assert opened["url"] == "https://www.kimi.com"
    text = ports.presentation.calls[0][0]
    assert "Edge" in text and "Kimi" in text


def test_missing_engine_and_missing_edge_reports_failure(mod, monkeypatch):
    instance, ports = _runtime(mod, {"ai_browser_enabled": True})
    monkeypatch.setattr(mod.engine, "engine_available", lambda: False)
    monkeypatch.setattr(mod.external, "open_external", lambda url: "")
    instance.open(slot=None)
    text, force = ports.presentation.calls[0]
    assert "打不开浏览器" in text
    assert force is True
