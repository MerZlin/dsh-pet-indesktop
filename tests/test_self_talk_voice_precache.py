# -*- coding: utf-8 -*-
"""点击台词 / 绑定台词「本地语音预缓存」的聚焦回归。

产品契约：
- 命名约定 ``<配置目录>/self_talk_voice/<md5(文本 utf-8)[:16]>.wav`` 必须与外部产出
  脚本（F:\\dsh\\tts\\build_self_talk_voice.py）一致 —— 桌宠按同一算法查找，
  改了就等于把已有缓存全部作废，所以这里把约定钉死；
- 名单 = 全局自言自语 + 各角色**点击动画绑定**台词，按文本去重（绑定台词不在名单
  里时只能回落在线合成，音色会变成另一个人，这正是本功能的由来）；
- 音色按口吻归类，且对同一句话**稳定**（同一句永远同一个音色，不做随机）；
- 后台补齐：本机没跑 TTS 服务是常态 → 安静收工、不刷错误日志；单句失败不影响
  其余句子；
- 开关默认关闭：未开启时一个网络请求都不发（通用用户不该被本机服务探测打扰）。

纪律：全部用例不打网络（``_get_json`` / ``_post_json`` 局部打桩），
线程用例用 Event 同步 + 宽预算，不固定 sleep 猜时序。
"""

from __future__ import annotations

import hashlib
import threading

from pet import self_talk_voice as stv
from pet.config import Config


def _cfg(tmp_path) -> Config:
    cfg = Config(base=tmp_path)
    cfg.set("self_talk_texts", ["好女孩……", "再陪你一会儿。"])
    # 第二条与全局台词重复：名单必须去重，同一句不该合成两遍。
    cfg.set_click_talk_bindings("shenshen", {"click-1": ["绑定的专属台词。", "好女孩……"]})
    return cfg


# --------------------------------------------------------------- 命名约定


def test_cache_name_contract_matches_build_script(tmp_path):
    """查找键必须与产出脚本一致，否则点击时永远命中不了缓存、静默回落到联网合成。"""
    text = "今天也要认真工作呀。"
    path = stv.cache_path(tmp_path, text)

    assert path.parent == tmp_path / "self_talk_voice"
    assert path.name == hashlib.md5(text.encode("utf-8")).hexdigest()[:16] + ".wav"


def test_cache_path_ignores_surrounding_whitespace(tmp_path):
    assert stv.cache_path(tmp_path, "  好女孩……  ") == stv.cache_path(tmp_path, "好女孩……")


# --------------------------------------------------------------- 名单与音色


def test_collect_lines_includes_bindings_and_dedupes(tmp_path):
    lines = stv.collect_lines(_cfg(tmp_path))
    texts = [text for text, _source in lines]

    assert texts.count("好女孩……") == 1, "全局与绑定重复的台词只收一次"
    assert "绑定的专属台词。" in texts
    assert any("点击绑定" in source for text, source in lines if text == "绑定的专属台词。")


def test_missing_lines_skips_already_cached(tmp_path):
    cfg = _cfg(tmp_path)
    cached = stv.cache_path(cfg.dir, "绑定的专属台词。")
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"RIFF0000WAVE")

    pending = [text for text, _voice in stv.missing_lines(cfg)]

    assert "绑定的专属台词。" not in pending
    assert "好女孩……" in pending


def test_missing_lines_treats_empty_cache_file_as_missing(tmp_path):
    """0 字节的残file（合成写入被打断留下）必须当成"没有"。

    否则点击后播的是静音，而且日志里一点线索都没有——主人只会觉得"坏了"。
    """
    cfg = _cfg(tmp_path)
    path = stv.cache_path(cfg.dir, "绑定的专属台词。")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")

    assert "绑定的专属台词。" in [text for text, _voice in stv.missing_lines(cfg)]


def test_pick_voice_is_stable_and_follows_tone():
    assert stv.pick_voice("再陪你一会儿。") == stv.pick_voice("再陪你一会儿。"), "同一句必须稳定"
    assert stv.pick_voice("今天也要认真工作呀。").startswith("happy"), "轻快鼓励 → happy"
    assert stv.pick_voice("晚安，做个好梦。").startswith("sad"), "晚安/温柔 → sad"
    assert stv.pick_voice("这是什么意思？").startswith("neutral"), "平淡陈述 → neutral"
    assert stv.pick_voice("哼，才不理你呢。").startswith("angry"), "较真/生气 → angry"
    assert stv.pick_voice("好女孩……") == "happy-06", "已知台词走固定映射"


def test_pick_voice_always_names_an_existing_family_slot():
    """32 条参照音是 <情绪>-01..08：归类结果必须落在这个命名空间里。"""
    for text in ("随便说点什么", "啦～", "晚安", "哼！", "为什么？"):
        family, _, index = stv.pick_voice(text).partition("-")
        assert family in {"angry", "happy", "neutral", "sad"}
        assert 1 <= int(index) <= 8


# --------------------------------------------------------------- 后台补齐


def _stub_http(monkeypatch, *, healthy=True, fail_texts=()):
    """打桩网络边界：记录请求，返回一段假 wav 字节。"""
    calls = {"health": 0, "synth": []}

    def fake_get(url, timeout):
        calls["health"] += 1
        if not healthy:
            raise OSError("connection refused")
        return {"ok": True}

    def fake_post(url, payload, timeout):
        calls["synth"].append(payload)
        if payload["text"] in fail_texts:
            raise OSError("boom")
        return b"RIFF0000WAVE"

    monkeypatch.setattr(stv, "_get_json", fake_get)
    monkeypatch.setattr(stv, "_post_json", fake_post)
    return calls


def test_run_precache_writes_only_missing_lines(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    cached = stv.cache_path(cfg.dir, "绑定的专属台词。")
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"RIFF0000WAVE")
    calls = _stub_http(monkeypatch)

    stats = stv.run_precache(cfg)

    assert stats["made"] == 2 and stats["skipped"] == 1
    assert cached.read_bytes() == b"RIFF0000WAVE", "已缓存的句子不该被重合成覆盖"
    assert stv.cache_path(cfg.dir, "好女孩……").is_file()
    assert sorted(p["text"] for p in calls["synth"]) == sorted(["好女孩……", "再陪你一会儿。"])
    assert all(p["voice"].count("-") == 1 for p in calls["synth"]), "必须带声线名"


def test_run_precache_is_silent_when_service_is_down(tmp_path, monkeypatch):
    """本机没跑 TTS 服务是常态：不发合成请求、不抛异常、统计里说明原因。"""
    cfg = _cfg(tmp_path)
    calls = _stub_http(monkeypatch, healthy=False)

    stats = stv.run_precache(cfg)

    assert calls["health"] == 1
    assert calls["synth"] == []
    assert stats["made"] == 0 and stats["reason"] == "service-unavailable"
    assert not (cfg.dir / "self_talk_voice").exists() or not list(
        (cfg.dir / "self_talk_voice").glob("*.wav"))


def test_run_precache_keeps_going_after_one_failure(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _stub_http(monkeypatch, fail_texts={"好女孩……"})

    stats = stv.run_precache(cfg)

    assert stats["made"] == 2 and stats["failed"] == 1
    assert stv.cache_path(cfg.dir, "再陪你一会儿。").is_file()


def test_start_precache_respects_disabled_setting(tmp_path, monkeypatch):
    """开关默认关闭：未开启时连线程都不该起（通用用户不该被探测打扰）。"""
    cfg = Config(base=tmp_path)
    started = threading.Event()
    monkeypatch.setattr(stv, "_active", None)
    monkeypatch.setattr(stv, "run_precache", lambda *a, **k: started.set())

    assert stv.start_precache(cfg) is False
    assert not started.wait(0.5), "关着就不该跑"

    cfg.set("self_talk_voice_precache_enabled", True)
    assert stv.start_precache(cfg) is True
    assert started.wait(5.0), "开启后应在后台补齐"


def test_start_precache_is_single_flight(tmp_path, monkeypatch):
    """重复触发（保存设置 + 启动）不该并发跑两轮合成。"""
    cfg = Config(base=tmp_path)
    cfg.set("self_talk_voice_precache_enabled", True)
    release = threading.Event()
    entered = threading.Event()
    monkeypatch.setattr(stv, "_active", None)

    def slow_run(*_a, **_k):
        entered.set()
        release.wait(5.0)

    monkeypatch.setattr(stv, "run_precache", slow_run)

    assert stv.start_precache(cfg) is True
    assert entered.wait(5.0)
    assert stv.start_precache(cfg) is False, "上一轮还在跑时不该再起一轮"
    release.set()


# --------------------------------------------------------------- 设置


def test_precache_setting_defaults_off_and_persists(tmp_path):
    cfg = Config(base=tmp_path)
    assert cfg.get("self_talk_voice_precache_enabled") is False

    cfg.set("self_talk_voice_precache_enabled", True)
    cfg.save()  # set() 只做归一化，落盘要走显式 save()（与既有同类设置一致）

    assert Config(base=tmp_path).get("self_talk_voice_precache_enabled") is True


def test_settings_page_toggle_round_trip(tmp_path):
    """设置页开关能落盘回读，且挂在「点击行为」那组里（门禁：持久化往返 + 归属）。"""
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod

    app = QApplication.instance() or QApplication([])
    cfg = Config(base=tmp_path)
    dialog = settings_mod.ModernSettingsDialog(cfg, include_ai=False)
    try:
        row = dialog.findChild(settings_mod.SettingRow, "settingRow_click_self_talk_precache")
        assert row is not None, "设置页必须有这一行"
        assert dialog.self_talk_voice_precache_check.isChecked() is False, "默认关闭"

        dialog.self_talk_voice_precache_check.setChecked(True)
        assert dialog._write_config() is True
    finally:
        dialog.deleteLater()

    assert Config(base=tmp_path).get("self_talk_voice_precache_enabled") is True


def test_appshell_precache_passes_config_to_entry(tmp_path, monkeypatch):
    """AppShell 只是薄封装：把配置交给预缓存入口（开关判定在入口内部）。"""
    from PySide6.QtWidgets import QApplication

    from pet.app import AppShell

    QApplication.instance() or QApplication([])
    cfg = Config(base=tmp_path)
    shell = AppShell(QApplication.instance(), cfg, enable_chat=False)
    seen: list = []
    monkeypatch.setattr(stv, "start_precache",
                        lambda config, **kwargs: seen.append(config) or True)

    assert shell.precache_self_talk_voice("测试") is True
    assert seen and seen[0] is cfg


def test_startup_hook_lives_on_appshell_itself(tmp_path, monkeypatch):
    """启动钩子必须挂在 AppShell 自身上，不能写 ``self.shell.…``。

    真实踩过：``AppShell.start()`` 里写成 ``lambda: self.shell.precache…``，
    而 ``self.shell`` 只存在于 PetInstance 上，于是每次启动都在 Qt 槽里抛
    AttributeError——pythonw 没有控制台，异常只进 stderr，表现就是"功能静默
    不生效"，日志里一条线索都没有。这条用例锁死"钩子自足"。
    """
    from PySide6.QtWidgets import QApplication

    from pet.app import AppShell

    QApplication.instance() or QApplication([])
    cfg = Config(base=tmp_path)
    shell = AppShell(QApplication.instance(), cfg, enable_chat=False)
    seen: list = []
    monkeypatch.setattr(stv, "start_precache",
                        lambda config, **kwargs: seen.append(config) or True)

    shell._precache_self_talk_voice_on_start()  # 不许抛异常，且要真的走到入口

    assert seen and seen[0] is cfg
