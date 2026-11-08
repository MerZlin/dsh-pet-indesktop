# -*- coding: utf-8 -*-
"""AI 碎碎念（self_talk_ai）的聚焦回归。

产品契约（2026-11 用户令）：
- 开关开 + 性格非空 + 激活模型可用 → 随机自言自语改由 LLM 现场生成（后台线程，
  完成后回 UI 线程落地）；生成中再触发不排队（本轮回固定句，防请求堆积）。
- 任一条件不满足 / 生成失败 / 输出不可用 → 固定句池原样生效，碎碎念绝不消失。
- 请求按碎碎念场景改写：压超时、压 token、关思考、关联网；密钥走
  ``Config.resolve_api_key``（keyring 兼容）。
- 落地时气泡被占用 → 整句丢弃，不覆盖审批等重要气泡。
"""

from __future__ import annotations

import sys
import threading
import time

import pytest
from PySide6.QtWidgets import QApplication

from pet import self_talk_ai


# ---------------------------------------------------------------- 清洗
def test_sanitize_strips_quotes_prefix_newlines_and_caps():
    assert self_talk_ai.sanitize_line("“今天也要加油呀。”") == "今天也要加油呀。"
    assert self_talk_ai.sanitize_line("碎碎念：好困……") == "好困……"
    assert self_talk_ai.sanitize_line("第一句\n\n解释：这是多余的第二行") == "第一句"
    assert len(self_talk_ai.sanitize_line("啊" * 500)) == self_talk_ai.LINE_CHAR_LIMIT
    assert self_talk_ai.sanitize_line("   \n  ") == ""


# ---------------------------------------------------------------- prompt 组装
def test_build_messages_injects_personality_history_and_click_note():
    messages = self_talk_ai.build_messages(
        "傲娇毒舌", ["上一句", "上上句"], trigger="click", now=None
    )
    assert messages[0]["role"] == "system"
    assert "傲娇毒舌" in messages[0]["content"]
    user = messages[1]["content"]
    assert "上一句" in user and "上上句" in user  # 最近说过进了防重复区
    assert "戳了你一下" in user  # 点击触发语境
    assert "【随机主题】" in user and "【当前时间】" in user


def test_build_messages_randomizes_theme():
    themes = {
        self_talk_ai.build_messages("活泼", [], trigger="timer")[1]["content"].split("【随机主题】")[1].split("\n")[0]
        for _ in range(40)
    }
    assert len(themes) >= 3  # 主题池在真随机抽样，40 次只出 1 种即坏


def test_build_messages_empty_personality_uses_default():
    messages = self_talk_ai.build_messages("", [], trigger="timer")
    assert self_talk_ai._DEFAULT_PERSONALITY in messages[0]["content"]


# ---------------------------------------------------------------- 配置门
def test_ai_gates_read_cfg_defensively():
    assert self_talk_ai.ai_enabled({"self_talk_ai_enabled": True}) is True
    assert self_talk_ai.ai_enabled({}) is False
    assert self_talk_ai.ai_enabled(None) is False
    assert self_talk_ai.ai_personality({"self_talk_ai_personality": "  活泼 "}) == "活泼"
    assert self_talk_ai.ai_personality({}) == ""
    assert self_talk_ai.ai_personality(None) == ""


class _FakeConfig:
    """chat_settings()/resolve_api_key() 的最小替身（真实 ChatSettings 解析路径）。"""

    def __init__(self, chat_raw):
        self._chat_raw = chat_raw

    def chat_settings(self):
        from pet.chat.models import ChatSettings

        return ChatSettings.from_dict(self._chat_raw)

    def resolve_api_key(self, provider):
        return "resolved-key"


_CHAT_RAW = {
    "enabled": True,
    "active_provider": "p1",
    "providers": {"p1": {"base_url": "https://api.example.com", "model": "m1", "timeout": 60, "max_tokens": 2048}},
}


def test_provider_config_is_murmur_tuned():
    resolved = self_talk_ai._provider_and_config(_FakeConfig(_CHAT_RAW))
    assert resolved is not None
    provider, pcfg = resolved
    assert pcfg.timeout == self_talk_ai.AI_TIMEOUT_SECONDS  # 60 → 压到碎碎念上限
    assert pcfg.max_tokens == self_talk_ai.AI_MAX_TOKENS
    assert pcfg.thinking == "disabled"
    assert pcfg.web_search is None
    assert pcfg.api_key == "resolved-key"


def test_provider_config_requires_enabled_chat():
    raw = dict(_CHAT_RAW, enabled=False)
    assert self_talk_ai._provider_and_config(_FakeConfig(raw)) is None


def test_provider_config_requires_base_url_and_model():
    raw = {"enabled": True, "active_provider": "p1", "providers": {"p1": {"base_url": "", "model": "m1"}}}
    assert self_talk_ai._provider_and_config(_FakeConfig(raw)) is None
    raw2 = {"enabled": True, "active_provider": "p1", "providers": {"p1": {"base_url": "https://x", "model": ""}}}
    assert self_talk_ai._provider_and_config(_FakeConfig(raw2)) is None


def test_provider_config_absent_in_no_chat_build(monkeypatch):
    """no-chat 变体（pet.chat 被打包剔除）：整条能力缺席，绝不抛。"""
    monkeypatch.setitem(sys.modules, "pet.chat.providers", None)
    monkeypatch.setitem(sys.modules, "pet.chat.models", None)
    assert self_talk_ai._provider_and_config(_FakeConfig(_CHAT_RAW)) is None


# ---------------------------------------------------------------- 接管口
class _Host:
    def __init__(self, cfg=None, blocked=False):
        self.cfg = cfg or {}
        self._blocked = blocked
        self.shown = []
        self.spoken = []
        self._last_self_talk_text = None
        self._self_talk_ai_state = None

    def _bubble_blocked(self):
        return self._blocked

    def _show_self_talk_text(self, text):
        self.shown.append(text)
        return True


class _FakeProvider:
    """stream() 行为可编程的 provider 替身。"""

    def __init__(self, *, text=" AI 现场编的碎碎念 ", error=None, gate=None):
        self.text = text
        self.error = error
        self.gate = gate  # threading.Event：set 前 stream 挂起（模拟在飞）

    def stream(self, messages, config, cancel_event, response_holder=None):
        if self.gate is not None:
            self.gate.wait(timeout=5.0)
        if cancel_event.is_set():
            return
        if self.error is not None:
            raise self.error
        yield self.text


def _qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def configured_host(monkeypatch):
    """开关全开 + provider 替身就位的宿主（替换网络层，绝不真发请求）。"""
    host = _Host(
        cfg={
            "self_talk_ai_enabled": True,
            "self_talk_ai_personality": "傲娇毒舌",
            "self_talk_speak_enabled": False,
        }
    )
    monkeypatch.setattr(
        self_talk_ai, "_provider_and_config", lambda cfg: (_FakeProvider(), object())
    )
    yield host
    self_talk_ai.cancel_all()


def _pump_until(app, condition, timeout_ms=4000):
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        app.processEvents()
    return False


def test_maybe_show_disabled_or_unconfigured_returns_false():
    app = _qapp()
    host = _Host(cfg={})
    assert self_talk_ai.maybe_show_ai_self_talk(host, fallback_text="固定句") is False
    assert host.shown == []  # 原样回落：调用方立即显示固定句

    host2 = _Host(cfg={"self_talk_ai_enabled": True, "self_talk_ai_personality": ""})
    assert self_talk_ai.maybe_show_ai_self_talk(host2, fallback_text="固定句") is False
    assert app is not None


def test_maybe_show_happy_path_displays_generated_line(configured_host):
    app = _qapp()
    host = configured_host

    assert self_talk_ai.maybe_show_ai_self_talk(host, fallback_text="固定句") is True
    assert host.shown == []  # 文本未落地前不动固定句

    assert _pump_until(app, lambda: bool(host.shown))
    assert host.shown == ["AI 现场编的碎碎念"]
    assert host._last_self_talk_text == "AI 现场编的碎碎念"
    assert list(host._self_talk_ai_state.history) == ["AI 现场编的碎碎念"]
    assert _pump_until(app, lambda: host._self_talk_ai_state.worker is None)


def test_maybe_show_generation_failure_falls_back(configured_host, monkeypatch):
    app = _qapp()
    host = configured_host
    monkeypatch.setattr(
        self_talk_ai,
        "_provider_and_config",
        lambda cfg: (_FakeProvider(error=RuntimeError("boom")), object()),
    )

    assert self_talk_ai.maybe_show_ai_self_talk(host, fallback_text="固定句") is True
    assert _pump_until(app, lambda: host.shown == ["固定句"])
    assert host._last_self_talk_text == "固定句"


def test_maybe_show_unusable_output_falls_back(configured_host, monkeypatch):
    app = _qapp()
    host = configured_host
    monkeypatch.setattr(
        self_talk_ai, "_provider_and_config", lambda cfg: (_FakeProvider(text="  \n "), object())
    )

    assert self_talk_ai.maybe_show_ai_self_talk(host, fallback_text="固定句") is True
    assert _pump_until(app, lambda: host.shown == ["固定句"])


def test_maybe_show_inflight_request_not_queued(configured_host, monkeypatch):
    app = _qapp()
    host = configured_host
    gate = threading.Event()
    monkeypatch.setattr(
        self_talk_ai,
        "_provider_and_config",
        lambda cfg: (_FakeProvider(text="慢句", gate=gate), object()),
    )

    assert self_talk_ai.maybe_show_ai_self_talk(host, fallback_text="固定句") is True
    assert self_talk_ai.maybe_show_ai_self_talk(host, fallback_text="固定句") is False
    gate.set()
    assert _pump_until(app, lambda: host._self_talk_ai_state.worker is None)


def test_maybe_show_drops_line_when_bubble_busy(configured_host):
    app = _qapp()
    host = configured_host

    assert self_talk_ai.maybe_show_ai_self_talk(host, fallback_text="固定句") is True
    host._blocked = True  # 生成落地前气泡被审批等占住
    assert _pump_until(app, lambda: host._self_talk_ai_state.worker is None)
    app.processEvents()
    assert host.shown == []  # 重要气泡优先：AI 句整句丢弃，不覆盖


def test_click_speak_flag_flows_to_display(configured_host):
    app = _qapp()
    host = configured_host
    host.cfg["self_talk_speak_enabled"] = True
    host.on_self_talk_speak = lambda text: host.spoken.append(text)

    host._self_talk_ai_speak_next = True
    assert self_talk_ai.maybe_show_ai_self_talk(
        host, fallback_text="固定句", speak_after=True
    ) is True
    assert _pump_until(app, lambda: bool(host.spoken))
    assert host.spoken == ["AI 现场编的碎碎念"]  # 说的 == 看的


# ---------------------------------------------------------------- 根 Config 解析
class _OverlaySpriteHost:
    """overlay 子宠宿主形态：``cfg`` 是每宠视图（无 chat_settings），真 Config
    在 ``_shell._config``——2026-11-07 实机回归的病灶形态。"""

    def __init__(self, root_config, per_pet_cfg):
        self.cfg = per_pet_cfg
        self._shell = type("Shell", (), {"_config": root_config})()
        self.shown = []
        self._last_self_talk_text = None

    def _bubble_blocked(self):
        return False

    def _show_self_talk_text(self, text):
        self.shown.append(text)
        return True


class _RootConfig:
    """带 chat_settings 的真 Config 替身（方法存在性就是解析判据）。"""

    def chat_settings(self):
        return None


def test_overlay_host_resolves_root_config(monkeypatch):
    """每宠视图读开关/性格，chat 设置必须从真 Config 解析——两者不能混同一个对象。"""
    app = _qapp()
    root = _RootConfig()
    host = _OverlaySpriteHost(
        root,
        {"self_talk_ai_enabled": True, "self_talk_ai_personality": "傲娇毒舌"},
    )
    captured = {}
    monkeypatch.setattr(
        self_talk_ai,
        "_provider_and_config",
        lambda cfg: (captured.__setitem__("cfg", cfg), (_FakeProvider(), object()))[1],
    )

    assert self_talk_ai.maybe_show_ai_self_talk(host, fallback_text="固定句") is True
    assert captured["cfg"] is root  # chat 设置走真 Config，不是每宠视图
    assert _pump_until(app, lambda: bool(host.shown))
    assert host.shown == ["AI 现场编的碎碎念"]


def test_resolve_root_config_prefers_chat_settings_capable():
    # PetWindow 形态：cfg 本身就是真 Config
    real = _RootConfig()
    assert self_talk_ai._resolve_root_config(type("W", (), {"cfg": real})()) is real
    # 子宠宿主形态：cfg 无 chat_settings，真 Config 在 _shell._config
    host = _OverlaySpriteHost(real, {})
    assert self_talk_ai._resolve_root_config(host) is real


# ---------------------------------------------------------------- 动作联动
def test_build_messages_action_overrides_theme():
    messages = self_talk_ai.build_messages("傲娇", [], trigger="timer", action_name="下五子棋")
    user = messages[1]["content"]
    assert "【正在做的动作】下五子棋" in user
    assert messages[0]["content"].count("必须围绕这个动作") == 1  # 系统规则里动作优先


def test_build_messages_without_action_keeps_theme_only():
    user = self_talk_ai.build_messages("傲娇", [], trigger="timer")[1]["content"]
    assert "【正在做的动作】" not in user


class _ActionHost(_Host):
    """带动作播放通道的宿主；记录 request_link_anim 的调用。"""

    def __init__(self, *, cfg=None, blocked=False, clips=("下五子棋", "写代码", "吃Token")):
        super().__init__(cfg=cfg, blocked=blocked)
        self.clips = list(clips)
        self.played = []

    def request_link_anim(self, name):
        self.played.append(str(name))


def _make_action_host(monkeypatch, **kwargs):
    """直接构造带播放通道的宿主（不换 class——那不会重跑 __init__）。"""
    host = _ActionHost(
        cfg={
            "self_talk_ai_enabled": True,
            "self_talk_ai_personality": "傲娇毒舌",
            "self_talk_speak_enabled": False,
        },
        **kwargs,
    )
    monkeypatch.setattr(
        self_talk_ai, "_provider_and_config", lambda c: (_FakeProvider(), object())
    )
    return host


def test_periodic_murmur_picks_and_plays_action(monkeypatch):
    app = _qapp()
    host = _make_action_host(monkeypatch)
    monkeypatch.setattr(
        self_talk_ai, "pick_random_action_clip", lambda h: "下五子棋"
    )

    assert self_talk_ai.maybe_show_ai_self_talk(host, fallback_text="固定句") is True
    assert host.played == ["下五子棋"]  # 动作先播，不等生成
    assert _pump_until(app, lambda: bool(host.shown))


def test_click_action_name_flows_into_request(monkeypatch):
    app = _qapp()
    host = _make_action_host(monkeypatch)
    seen = {}
    real_build = self_talk_ai.build_messages

    def spy_build(personality, history, *, trigger="timer", action_name="", now=None):
        seen["action"] = action_name
        seen["trigger"] = trigger
        return real_build(personality, history, trigger=trigger, action_name=action_name, now=now)

    monkeypatch.setattr(self_talk_ai, "build_messages", spy_build)

    host._self_talk_ai_speak_next = True
    host._self_talk_ai_action_name = "点击回应-挠痒咯咯笑"
    assert self_talk_ai.maybe_show_ai_self_talk(
        host, fallback_text="固定句", speak_after=True,
        action_name=host._self_talk_ai_action_name,
    ) is True
    assert seen["action"] == "点击回应-挠痒咯咯笑" and seen["trigger"] == "click"
    assert host.played == []  # 点击动画本来就在播，不重复播
    assert _pump_until(app, lambda: bool(host.shown))


def test_sub_sprite_host_gets_no_action(monkeypatch):
    """子 sprite 宿主（request_link_anim 是 no-op）不配动作、不播动画。"""
    app = _qapp()
    host = _make_action_host(monkeypatch)
    # 伪装成子 sprite：有 _shell/_sprite 且 sprite 不是主宠
    host._shell = type("S", (), {"sprite": object()})()
    host._sprite = object()
    seen = {}
    real_build = self_talk_ai.build_messages

    def spy_build(personality, history, *, trigger="timer", action_name="", now=None):
        seen["action"] = action_name
        return real_build(personality, history, trigger=trigger, action_name=action_name, now=now)

    monkeypatch.setattr(self_talk_ai, "build_messages", spy_build)

    assert self_talk_ai.maybe_show_ai_self_talk(host, fallback_text="固定句") is True
    assert seen["action"] == "" and host.played == []
    assert _pump_until(app, lambda: bool(host.shown))


def test_can_play_action_host_forms():
    # 无通道 → False
    assert self_talk_ai._can_play_action(_Host()) is False
    # 有通道、无子宠语境 → True
    host = _ActionHost()
    assert self_talk_ai._can_play_action(host) is True
    # 子 sprite → False（主宠才播）
    host._shell = type("S", (), {"sprite": "main"})()
    host._sprite = "sub"
    assert self_talk_ai._can_play_action(host) is False
    host._sprite = "main"
    assert self_talk_ai._can_play_action(host) is True
