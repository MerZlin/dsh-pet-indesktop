# -*- coding: utf-8 -*-
"""`tool.ai-browser` v2：站点表、清单、视图贡献与运行时闸门（不启动 Qt、不联网）。"""

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
NO_QT_MODULES = ("sites", "engine", "runtime")
VIEW_READY = ("", "runtime")


@pytest.fixture(scope="module")
def mod():
    factory = feature_mod.import_entrypoint(PACK, "host/__init__.py:create_host", MOD_ID)
    assert callable(factory)
    return types.SimpleNamespace(
        factory=factory,
        sites=importlib.import_module(f"{PACKAGE}.sites"),
        engine=importlib.import_module(f"{PACKAGE}.engine"),
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


class _Chat:
    def __init__(self, result=True, *, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def show_view(self, owner, view_id, *, pet_slot=None):
        self.calls.append((owner, view_id, pet_slot))
        if self.error is not None:
            raise self.error
        return self.result


class _Context:
    def __init__(self, ports):
        self.ports = ports
        self.owner = MOD_ID
        self.core_version = "0.4.0"
        self.api_version = "1"
        self.config = {}
        self.events = None
        self.host = None

    def log(self, *args, **kwargs):
        return None


def _runtime(mod, config=None, chat=None):
    ports = types.SimpleNamespace(
        config=_Config(config),
        presentation=_Presentation(),
        storage=None,
        scheduler=None,
        chat=chat,
    )
    instance = mod.runtime.BrowserRuntime(_Context(ports))
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


# ---- 自定义站点


def test_parse_custom_sites_accepts_pipes_and_bare_urls(mod):
    parsed = mod.sites.parse_custom_sites(
        "\n".join(
            [
                "自建 | http://127.0.0.1:3000",
                "全角｜https://a.example.com",
                "https://b.example.com/chat",
                "坏行 | javascript:alert(1)",
                "ftp | ftp://c.example.com",
                "",
            ]
        )
    )
    assert [item.name for item in parsed] == ["自建", "全角", "b.example.com"]
    assert [item.id for item in parsed] == ["custom1", "custom2", "custom3"]
    assert parsed[0].url == "http://127.0.0.1:3000"
    assert parsed[2].url == "https://b.example.com/chat"


def test_bare_host_gets_https_and_keeps_host_allowlist(mod):
    parsed = mod.sites.parse_custom_sites("我的 | my-ai.cn")
    assert parsed and parsed[0].url == "https://my-ai.cn"
    assert mod.sites.host_allowed("my-ai.cn", parsed[0].hosts)
    assert mod.sites.host_allowed("sub.my-ai.cn", parsed[0].hosts)


def test_all_sites_puts_builtins_first(mod):
    combined = mod.sites.all_sites("x | https://x.example.com")
    assert combined[0].id == "deepseek"
    assert combined[-1].id == "custom1"


# ---- 清单与视图贡献


def test_manifest_is_valid_and_contributions_pair_up(mod):
    manifest, problems = load_manifest(PACK)
    assert problems == []
    assert manifest is not None
    assert manifest.id == MOD_ID
    assert manifest.kind == "feature"
    assert manifest.api_version == "1"
    assert manifest.entrypoint == "host/__init__.py:create_host"
    assert manifest.version == "2.0.0"

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
    definition = features[0].definition
    kinds = {item.kind for item in definition.contributions}
    assert {"menu", "settings_page"} <= kinds
    assert manager.command(MOD_ID, "open") is not None
    # 宿主具备网页内核 → 才贡献 chat_view（宁可没按钮，也不给死入口）
    if mod.engine.missing_reason() in VIEW_READY:
        assert "chat_view" in kinds
        specs = manager.chat_views()
        assert [spec.id for spec in specs] == [f"{MOD_ID}:web"]
        assert specs[0].label == "网页 AI"
        assert specs[0].icon == "globe"
        assert callable(specs[0].factory)
    else:
        assert "chat_view" not in kinds
        assert manager.chat_views() == []


def test_runtime_and_pure_modules_stay_free_of_qt(mod):
    banned = re.compile(
        r"^\s*(?:from|import)\s+(?:PySide6|petcore\.(?:net|api|account|platform))\b",
        re.MULTILINE,
    )
    for name in NO_QT_MODULES:
        text = (PACK / "host" / f"{name}.py").read_text(encoding="utf-8")
        assert banned.search(text) is None, name


def test_view_module_is_never_imported_at_load_time(mod):
    assert f"{PACKAGE}.web_pane" not in sys.modules


# ---- 引擎探针


def test_missing_reason_layers(mod, monkeypatch):
    monkeypatch.setattr(mod.engine, "platform_supported", lambda: False)
    assert mod.engine.missing_reason() == "platform"
    assert mod.engine.missing_hint("platform")

    monkeypatch.setattr(mod.engine, "platform_supported", lambda: True)
    monkeypatch.setattr(mod.engine, "engine_available", lambda: False)
    assert mod.engine.missing_reason() == "engine"

    monkeypatch.setattr(mod.engine, "engine_available", lambda: True)
    monkeypatch.setattr(mod.engine, "runtime_version", lambda: "")
    assert mod.engine.missing_reason() == "runtime"

    monkeypatch.setattr(mod.engine, "runtime_version", lambda: "156.0.0")
    assert mod.engine.missing_reason() == ""


def test_user_data_dir_is_absolute_and_stable(mod, tmp_path):
    target = mod.engine.ensure_user_data(tmp_path)
    assert target == tmp_path / "webview2"
    assert target.is_dir()


# ---- 运行时闸门


def test_open_without_engine_says_why_and_never_touches_chat(mod, monkeypatch):
    chat = _Chat()
    instance, ports = _runtime(mod, chat=chat)
    monkeypatch.setattr(mod.engine, "missing_reason", lambda: "engine")
    instance.open(slot=0)
    text, force = ports.presentation.calls[0]
    assert "QtWebView" in text and force is True
    assert chat.calls == []


def test_open_without_port_says_update(mod, monkeypatch):
    instance, ports = _runtime(mod, chat=None)
    monkeypatch.setattr(mod.engine, "missing_reason", lambda: "")
    instance.open(slot=None)
    text, _force = ports.presentation.calls[0]
    assert "视图接口" in text


def test_open_switches_the_chat_window_to_the_web_view(mod, monkeypatch):
    chat = _Chat(result=True)
    instance, ports = _runtime(mod, chat=chat)
    monkeypatch.setattr(mod.engine, "missing_reason", lambda: "")
    instance.open(slot=2)
    assert chat.calls == [(MOD_ID, "web", 2)]
    assert ports.presentation.calls == []


def test_open_reports_a_refused_view(mod, monkeypatch):
    chat = _Chat(result=False)
    instance, ports = _runtime(mod, chat=chat)
    monkeypatch.setattr(mod.engine, "missing_reason", lambda: "")
    instance.open(slot=None)
    text, force = ports.presentation.calls[0]
    assert "没能打开" in text and force is True


def test_open_swallows_port_errors(mod, monkeypatch):
    chat = _Chat(error=RuntimeError("boom"))
    instance, ports = _runtime(mod, chat=chat)
    monkeypatch.setattr(mod.engine, "missing_reason", lambda: "")
    instance.open(slot=None)
    text, _force = ports.presentation.calls[0]
    assert "没能打开" in text
