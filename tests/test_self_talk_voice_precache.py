# -*- coding: utf-8 -*-
"""点击台词 / 绑定台词「本地语音预缓存」的聚焦回归。

产品契约：
- 命名约定 ``<配置目录>/self_talk_voice/<md5(文本 utf-8)[:16]>.<扩展名>`` 必须与外部
  产出脚本（F:\\dsh\\tts\\build_self_talk_voice.py）一致 —— 桌宠按同一算法查找，
  改了就等于把已有缓存全部作废，所以这里把约定钉死（默认 wav = 脚本的产出扩展名）；
- 合成走「设置 → 语音报时」同一条链路（``pet/tts`` 注册表 + 同一音色），所以「试听
  有声音」等价于「台词语音能生成」；自建 CosyVoice 只剩 ``DSH_PET_TTS_URL`` 这条可选
  覆盖，且它不在线时必须落回注册表而不是静默收工（issue #305）；
- 名单 = 每只宠**自己 slot 档案**里的自言自语（``self_talk_texts`` 是每宠键）＋该宠
  各角色**点击动画绑定**台词，跨宠按文本去重（读错作用域 → 在别人的名单上生成、
  点击命中不到 → 回退在线合成，音色变成另一个人）；
- 落盘原子：残片/空文件既不上账也不被查找侧当成命中；
- 开关默认关闭：未开启时一个合成请求都不发。

纪律：全部用例不打网络（``_get_json`` / ``_post_json`` 局部打桩，合成后端用假
provider），线程用例用 Event 同步 + 宽预算，不固定 sleep 猜时序。
"""

from __future__ import annotations

import hashlib
import logging
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from pet import self_talk_voice as stv
from pet import tts
from pet.config import Config

# --------------------------------------------------------------- 替身与夹具


def _wav(payload: bytes = b"\x00\x00" * 16) -> bytes:
    """结构自洽的最小 wav：RIFF 头声明的长度与实际字节数一致（查找侧会核对）。"""
    fmt = (
        b"fmt " + (16).to_bytes(4, "little") + b"\x01\x00\x01\x00"
        + (16000).to_bytes(4, "little") + (32000).to_bytes(4, "little")
        + b"\x02\x00\x10\x00"
    )
    data = b"data" + len(payload).to_bytes(4, "little") + payload
    body = b"WAVE" + fmt + data
    return b"RIFF" + (len(body) - 4).to_bytes(4, "little") + body


class _FakeProvider(tts.TtsProvider):
    """假合成后端：不联网，写一段结构自洽的 wav（可按文本失败或只写残片）。"""

    id = "faketts"
    label = "假合成后端（测试）"
    fields = ()
    is_fallback = False

    def __init__(self, *, fail=(), truncate=(), unavailable: str = "") -> None:
        self.fail = set(fail)
        self.truncate = set(truncate)
        self.unavailable = unavailable
        self.calls: list[str] = []

    def plan(self, values):
        return {"ext": "wav"}

    def flavor(self, values):
        return "fake"

    def availability(self, values):
        return self.unavailable

    def synth(self, text, plan, out_path):
        self.calls.append(text)
        if text in self.fail:
            raise RuntimeError("boom")
        blob = _wav()
        Path(out_path).write_bytes(blob[:20] if text in self.truncate else blob)


@pytest.fixture
def fake_tts():
    """按需注册假后端；用例结束一律注销，避免污染注册表。"""
    created: list[_FakeProvider] = []

    def make(**kwargs) -> _FakeProvider:
        provider = _FakeProvider(**kwargs)
        tts.register(provider)
        created.append(provider)
        return provider

    try:
        yield make
    finally:
        for provider in created:
            tts.unregister(provider.id)


def _cfg(tmp_path, *, backend: str = "", **extra) -> Config:
    cfg = Config(base=tmp_path)
    cfg.set("self_talk_texts", ["好女孩……", "再陪你一会儿。"])
    # 第二条与全局台词重复：名单必须去重，同一句不该合成两遍。
    cfg.set_click_talk_bindings("shenshen", {"click-1": ["绑定的专属台词。", "好女孩……"]})
    if backend:
        cfg.set("voice_chime_tts_backend", backend)
        cfg.set("voice_chime_tts_fallback", False)
    for key, value in extra.items():
        cfg.set(key, value)
    return cfg


def _cache_dir(config_dir) -> Path:
    return Path(config_dir) / stv.CACHE_DIR_NAME


# --------------------------------------------------------------- 命名约定


def test_cache_name_contract_matches_build_script(tmp_path):
    """查找键必须与产出脚本一致，否则点击时永远命中不了缓存、静默回落到联网合成。"""
    text = "今天也要认真工作呀。"
    path = stv.cache_path(tmp_path, text)

    assert path.parent == tmp_path / "self_talk_voice"
    assert path.name == hashlib.md5(text.encode("utf-8")).hexdigest()[:16] + ".wav"


def test_cache_path_ignores_surrounding_whitespace(tmp_path):
    assert stv.cache_path(tmp_path, "  好女孩……  ") == stv.cache_path(tmp_path, "好女孩……")


def test_cache_path_extension_follows_the_backend(tmp_path):
    """扩展名由产出它的后端决定（edge/doubao 给 mp3），键本体不变。"""
    assert stv.cache_path(tmp_path, "好女孩……", "mp3").suffix == ".mp3"
    assert stv.cache_path(tmp_path, "好女孩……", "mp3").stem == (
        stv.cache_path(tmp_path, "好女孩……").stem)


# --------------------------------------------------------------- 命中判定


def test_cached_audio_finds_the_wav_left_by_the_build_script(tmp_path):
    """外部脚本产出的 wav 仍要命中（否则升级后老缓存等于作废）。"""
    path = stv.cache_path(tmp_path, "绑定的专属台词。")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_wav())

    assert stv.cached_audio(tmp_path, "绑定的专属台词。") == path


def test_cached_audio_finds_mp3_produced_by_an_online_backend(tmp_path):
    """非 wav 产物（edge/doubao）只判非空——没有可校验的长度字段。"""
    path = stv.cache_path(tmp_path, "好女孩……", "mp3")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00")

    assert stv.cached_audio(tmp_path, "好女孩……") == path


def test_cached_audio_ignores_empty_file(tmp_path):
    """0 字节的残file（合成写入被打断留下）必须当成"没有"。

    否则点击后播的是静音，而且日志里一点线索都没有——主人只会觉得"坏了"。
    """
    path = stv.cache_path(tmp_path, "绑定的专属台词。")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")

    assert stv.cached_audio(tmp_path, "绑定的专属台词。") is None


def test_cached_audio_ignores_truncated_wav(tmp_path):
    """半截 wav 不是缓存：头部声明的长度大于实际字节数，播出来是半句/爆音。"""
    path = stv.cache_path(tmp_path, "好女孩……")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_wav()[:24])

    assert stv.cached_audio(tmp_path, "好女孩……") is None


def test_cached_audio_ignores_in_flight_temp_files(tmp_path):
    """在飞的临时文件（同目录、以 ``.`` 开头）绝不能被当成命中。"""
    _cache_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    (_cache_dir(tmp_path) / f"{stv.TEMP_PREFIX}deadbeef.wav").write_bytes(_wav())

    assert stv.cached_audio(tmp_path, "好女孩……") is None


# --------------------------------------------------------------- 名单与音色


def test_collect_lines_includes_bindings_and_dedupes(tmp_path):
    lines = stv.collect_lines(_cfg(tmp_path))
    texts = [text for text, _source in lines]

    assert texts.count("好女孩……") == 1, "全局与绑定重复的台词只收一次"
    assert "绑定的专属台词。" in texts
    assert any("点击绑定" in source for text, source in lines if text == "绑定的专属台词。")


def test_collect_all_lines_reads_each_pet_own_slot_scope(tmp_path):
    """每只宠的名单必须来自**它自己**的 slot 档案（``self_talk_texts`` 是每宠键）。"""
    from pet.settings_slot_config import route_settings_config

    main = Config(base=tmp_path)
    main.set("self_talk_texts", ["主配置层那份（不该被读）。"])
    slot3 = route_settings_config(Config(base=tmp_path, instance_id="slot-3"))
    slot3.set("self_talk_texts", ["三号宠自己的台词。"])
    assert slot3.save() is True

    texts = [text for text, _source in stv.collect_all_lines([slot3])]

    assert "三号宠自己的台词。" in texts
    assert "主配置层那份（不该被读）。" not in texts


class _TextView:
    """最小配置视图替身：只回答 ``self_talk_texts``（合并语义的用例用它）。"""

    def __init__(self, texts) -> None:
        self._texts = list(texts)

    def get(self, key, default=None):
        return list(self._texts) if key == "self_talk_texts" else default


def test_collect_all_lines_dedupes_across_pets():
    # 真实每宠视图的读取由上面那条用例守，这里只验合并语义
    texts = [text for text, _source in stv.collect_all_lines(
        [_TextView(["同一句。", "只属于 A。"]), _TextView(["同一句。", "只属于 B。"])])]

    assert texts.count("同一句。") == 1
    assert "只属于 A。" in texts and "只属于 B。" in texts


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


# --------------------------------------------------------------- 后台补齐（注册表链路）


def test_run_precache_synthesizes_with_the_configured_backend(tmp_path, fake_tts):
    """① 与「试听」同一个后端：配置的后端能合成，预缓存就必须落出可播的音频。"""
    provider = fake_tts()
    cfg = _cfg(tmp_path, backend=provider.id)

    stats = stv.run_precache(cfg)

    assert stats["made"] == 3 and stats["failed"] == 0 and stats["skipped"] == 0
    target = stv.cache_path(cfg.dir, "好女孩……")
    assert target.is_file()
    assert stv.cached_audio(cfg.dir, "好女孩……") == target
    assert sorted(provider.calls) == ["再陪你一会儿。", "好女孩……", "绑定的专属台词。"]


def test_run_precache_skips_lines_that_already_have_audio(tmp_path, fake_tts):
    provider = fake_tts()
    cfg = _cfg(tmp_path, backend=provider.id)
    cached = stv.cache_path(cfg.dir, "绑定的专属台词。")
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(_wav())

    stats = stv.run_precache(cfg)

    assert stats["made"] == 2 and stats["skipped"] == 1
    assert cached.read_bytes() == _wav(), "已缓存的句子不该被重合成覆盖"
    assert "绑定的专属台词。" not in provider.calls


def test_run_precache_keeps_going_after_one_failure(tmp_path, fake_tts):
    provider = fake_tts(fail={"好女孩……"})
    cfg = _cfg(tmp_path, backend=provider.id)

    stats = stv.run_precache(cfg)

    assert stats["made"] == 2 and stats["failed"] == 1
    assert stv.cached_audio(cfg.dir, "再陪你一会儿。") is not None
    assert stv.cached_audio(cfg.dir, "好女孩……") is None


def test_run_precache_never_publishes_a_truncated_clip(tmp_path, fake_tts):
    """⑤ 合成只写出残片时既不上账，也不留残file（它会永远被当成命中）。"""
    provider = fake_tts(truncate={"好女孩……"})
    cfg = _cfg(tmp_path, backend=provider.id)

    stats = stv.run_precache(cfg)

    assert stats["failed"] == 1 and stats["made"] == 2
    assert stv.cached_audio(cfg.dir, "好女孩……") is None
    leftovers = [p.name for p in _cache_dir(cfg.dir).iterdir() if p.name.startswith(stv.TEMP_PREFIX)]
    assert leftovers == [], f"临时文件没清干净：{leftovers}"


def test_run_precache_reports_missing_backend_instead_of_silence(tmp_path, fake_tts, caplog):
    """一个后端都用不了时必须说话：这就是 issue #305 的"没生成也没提示"。"""
    provider = fake_tts(unavailable="fake_missing")
    cfg = _cfg(tmp_path, backend=provider.id)

    with caplog.at_level(logging.WARNING):
        stats = stv.run_precache(cfg)

    assert stats["reason"] == "no-backend"
    assert stats["made"] == 0
    assert "没有可用的合成后端" in caplog.text


# --------------------------------------------------------------- 自建服务覆盖


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
        return _wav()

    monkeypatch.setattr(stv, "_get_json", fake_get)
    monkeypatch.setattr(stv, "_post_json", fake_post)
    return calls


def test_local_service_override_still_writes_the_classic_wav(tmp_path, monkeypatch, fake_tts):
    """``DSH_PET_TTS_URL`` 指向的自建服务仍可用，且产出落在老路径上（不浪费老缓存）。"""
    provider = fake_tts()
    cfg = _cfg(tmp_path, backend=provider.id)
    monkeypatch.setenv(stv.SERVER_ENV, "http://127.0.0.1:9880")
    calls = _stub_http(monkeypatch)

    stats = stv.run_precache(cfg)

    assert stats["made"] == 3 and stats["failed"] == 0
    assert calls["synth"], "覆盖模式下必须真的调自建服务"
    assert all(payload["voice"].count("-") == 1 for payload in calls["synth"]), "必须带声线名"
    assert provider.calls == [], "覆盖模式优先：不该同时动注册表后端"
    assert stv.cached_audio(cfg.dir, "好女孩……") == stv.cache_path(cfg.dir, "好女孩……")


def test_local_service_failure_falls_back_to_the_registry(tmp_path, fake_tts, monkeypatch, caplog):
    """② 自建服务不在线时**不许**安静收工：落回配置的合成后端（用户有声音就行）。"""
    provider = fake_tts()
    cfg = _cfg(tmp_path, backend=provider.id)
    monkeypatch.setenv(stv.SERVER_ENV, "http://127.0.0.1:9880")
    calls = _stub_http(monkeypatch, healthy=False)

    with caplog.at_level(logging.WARNING):
        stats = stv.run_precache(cfg)

    assert calls["synth"] == [], "服务不可用就不该继续往它发合成请求"
    assert stats["made"] == 3, "必须落回注册表后端，而不是一个文件都不生成"
    assert provider.calls, "落回的证据：假后端被调用"
    assert "不可用" in caplog.text, "降级必须留下提示"


def test_local_service_partial_failure_is_reported(tmp_path, monkeypatch, fake_tts):
    provider = fake_tts()
    cfg = _cfg(tmp_path, backend=provider.id)
    monkeypatch.setenv(stv.SERVER_ENV, "http://127.0.0.1:9880")
    _stub_http(monkeypatch, fail_texts={"好女孩……"})

    stats = stv.run_precache(cfg)

    assert stats["made"] == 2 and stats["failed"] == 1
    assert provider.calls == [], "覆盖模式优先：单句失败也不该改走注册表"
    assert stv.cached_audio(cfg.dir, "再陪你一会儿。") is not None


# --------------------------------------------------------------- 后台线程入口


def test_start_precache_respects_disabled_setting(tmp_path, monkeypatch):
    """开关默认关闭：未开启时连线程都不该起（通用用户不该被合成请求打扰）。"""
    cfg = Config(base=tmp_path)
    started = threading.Event()
    monkeypatch.setattr(stv, "_active", None)
    monkeypatch.setattr(stv, "run_precache", lambda *a, **k: started.set())

    assert stv.start_precache(cfg) is False
    assert not started.wait(0.5), "关着就不该跑"

    cfg.set("self_talk_voice_precache_enabled", True)
    assert stv.start_precache(cfg, configs=[cfg]) is True
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


def test_click_self_talk_precache_hint_no_longer_promises_a_local_cosyvoice():
    """设置页文案必须与实现一致：合成走的是「语音报时」的后端，不是"必须先装 CosyVoice"。"""
    src = (Path(__file__).resolve().parents[1] / "pet" / "settings_interaction.py").read_text(
        encoding="utf-8")

    assert "CosyVoice" not in src, "文案还写着需要本机 CosyVoice 服务，与实际实现不符"
    assert "与语音报时相同的语音后端" in src


# --------------------------------------------------------------- 与 AppShell 的接线


def _appshell(cfg):
    from PySide6.QtWidgets import QApplication

    from pet.app import AppShell

    QApplication.instance() or QApplication([])
    return AppShell(QApplication.instance(), cfg, enable_chat=False)


def test_appshell_precache_passes_every_pet_view_to_the_entry(tmp_path, monkeypatch):
    """④ 每只活跃宠的配置视图都要交给入口：名单是每宠键，主配置那份只是一只宠的。"""
    cfg = _cfg(tmp_path)
    cfg.set("self_talk_voice_precache_enabled", True)
    shell = _appshell(cfg)
    other = SimpleNamespace(config=Config(base=tmp_path, instance_id="slot-7"))
    shell._instances = [shell.instance, other]
    seen: list = []
    monkeypatch.setattr(stv, "start_precache",
                        lambda config, **kwargs: seen.append((config, kwargs)) or True)

    assert shell.precache_self_talk_voice("测试") is True

    assert seen and seen[0][0] is cfg
    assert seen[0][1]["configs"] == [shell.instance.config, other.config]


def test_precache_stays_off_until_the_switch_is_on(tmp_path, monkeypatch):
    """开关关着时连每宠名单都不读（每宠档案读取不该为关着的功能付费）。"""
    cfg = _cfg(tmp_path)
    shell = _appshell(cfg)
    seen: list = []
    monkeypatch.setattr(stv, "start_precache",
                        lambda config, **kwargs: seen.append(config) or True)

    assert shell.precache_self_talk_voice("测试") is False
    assert seen == []


def test_overlay_topology_reads_each_sprite_view(tmp_path):
    """overlay 拓扑下子宠是 sprite、不在 ``_instances``：名单必须走壳的 sprite 视图。

    只认 ``_instances`` 的话，overlay 下等于只有 slot0 一只参与，子宠的台词依旧
    不会被预缓存（点击时回退在线合成）。
    """
    cfg = _cfg(tmp_path)
    cfg.set("self_talk_voice_precache_enabled", True)
    shell = _appshell(cfg)
    views = [_TextView(["主 sprite 的台词。"]), _TextView(["子 sprite 的台词。"])]
    shell._overlay_shell = SimpleNamespace(
        _all_sprites=lambda: ["main", "child"],
        _sprite_config=lambda sprite: views[0] if sprite == "main" else views[1],
    )

    assert shell._self_talk_voice_configs() == views


def test_startup_hook_lives_on_appshell_itself(tmp_path, monkeypatch):
    """启动钩子必须挂在 AppShell 自身上，不能写 ``self.shell.…``。

    真实踩过：``AppShell.start()`` 里写成 ``lambda: self.shell.precache…``，
    而 ``self.shell`` 只存在于 PetInstance 上，于是每次启动都在 Qt 槽里抛
    AttributeError——pythonw 没有控制台，异常只进 stderr，表现就是"功能静默
    不生效"，日志里一条线索都没有。这条用例锁死"钩子自足"。
    """
    cfg = _cfg(tmp_path)
    cfg.set("self_talk_voice_precache_enabled", True)
    shell = _appshell(cfg)
    seen: list = []
    monkeypatch.setattr(stv, "start_precache",
                        lambda config, **kwargs: seen.append(config) or True)

    shell._precache_self_talk_voice_on_start()  # 不许抛异常，且要真的走到入口

    assert seen and seen[0] is cfg


def test_external_settings_save_starts_the_precache(tmp_path, monkeypatch):
    """③ 独立设置进程保存（watcher 路径）必须触发：这是用户最常走的那条。

    进程内设置对话框的 finished 那条链（``_modern_settings_finished``）在
    ``settings_process_isolation`` 默认开启时根本不会执行，缺了这一行就是
    "打开开关并保存、本次运行一个文件都不生成"（issue #305）。
    """
    import pet.app as app_mod

    cfg = _cfg(tmp_path)
    shell = _appshell(cfg)
    shell._sync_dynamic_island = lambda: None
    shell._apply_balance_timer = lambda: None
    shell._sync_todo_service = lambda: None
    shell._sync_chime_service = lambda: None
    shell._sync_festival_service = lambda: None
    shell._sync_divination_service = lambda: None
    shell._stop_harness_if_still_unwanted = lambda: None
    monkeypatch.setattr(app_mod, "_mac_set_dock_icon_visible", lambda *a, **k: None)
    seen: list = []
    monkeypatch.setattr(stv, "start_precache",
                        lambda config, **kwargs: seen.append((config, kwargs)) or True)

    shell._apply_external_config_change()
    assert seen == [], "开关关着时外部变更不该起预缓存"

    cfg.set("self_talk_voice_precache_enabled", True)  # 独立设置进程刚落盘的值
    shell._apply_external_config_change()

    assert len(seen) == 1, "外部存盘打开开关后本次运行必须补一次预缓存"
    assert seen[0][0] is cfg
    assert seen[0][1]["configs"], "名单要按每只宠自己的配置视图取"


def test_producer_output_is_what_the_click_path_plays(tmp_path, fake_tts):
    """⑥ 产出侧写出的文件必须就是点击侧命中的那个（对不上=静默回退在线合成）。"""
    provider = fake_tts()
    cfg = _cfg(tmp_path, backend=provider.id, self_talk_speak_enabled=True)
    stv.run_precache(cfg)

    shell = _appshell(cfg)
    played: list[str] = []
    channel = SimpleNamespace(
        play_file=lambda path, log_tag="": played.append(path) or True,
        speak=lambda text, **kwargs: (_ for _ in ()).throw(
            AssertionError("不该回退在线合成")),
        is_running=lambda: True,
    )
    shell.ensure_audio_channel = lambda: channel

    assert shell.speak_self_talk("好女孩……") is True

    assert played == [str(stv.cache_path(cfg.dir, "好女孩……"))]
    assert shell.self_talk_voice_file("好女孩……") == stv.cached_audio(cfg.dir, "好女孩……")
