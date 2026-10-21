# -*- coding: utf-8 -*-
"""点击台词 / 绑定台词「本地语音预缓存」的聚焦回归。

产品契约：
- 注册表生成的音频按 ``text + provider.flavor(实际合成输入)`` 命名（与 ``voice_chime``
  的报时缓存同一个 ``cache_key``）：换音色/语速/后端就换键，旧句子不会被当成已缓存；
  preflight 把音色换掉之后，键按**替换后**的音色算；
- 命名约定 ``<配置目录>/self_talk_voice/<md5(文本 utf-8)[:16]>.<扩展名>`` 是**经典
  文本缓存**（外部产出脚本 ``F:\\dsh\\tts\\build_self_talk_voice.py`` 与自建服务覆盖
  模式）：没有 flavor、仍按纯文本命中，升级不作废它；但查找顺序是「当前配置的产物 →
  经典文本缓存」，它不凌驾于当前配置生成的产物之上；
- 合成走「设置 → 语音报时」同一条链路（``pet/tts`` 注册表 + 同一音色），所以「试听
  有声音」等价于「台词语音能生成」；自建 CosyVoice 只剩 ``DSH_PET_TTS_URL`` 这条可选
  覆盖，且它不在线时必须落回注册表而不是静默收工（issue #305）；
- 名单 = 每只宠**自己 slot 档案**里的自言自语（``self_talk_texts`` 是每宠键）＋该宠
  各角色**点击动画绑定**台词，跨宠按文本去重（读错作用域 → 在别人的名单上生成、
  点击命中不到 → 回退在线合成，音色变成另一个人）；
- 落盘原子：残片/空文件既不上账也不被查找侧当成命中；
- 开关默认关闭：未开启时一个合成请求都不发；一轮合成期间关掉开关，句间就停手；
- 单飞不等于丢弃：合成期间再次保存台词，当前批结束后按最新名单补跑一轮。

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

from pet.voice import self_talk as stv
from pet import tts
from pet.config import Config
from pet.voice_chime import cache_key

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
    """假合成后端：不联网，写一段结构自洽的 wav（可按文本失败或只写残片）。

    ``voice`` 参与 ``flavor``：与真实 provider 同一口径（flavor 只放影响音频内容的
    参数），因此换音色就换缓存键。
    """

    id = "faketts"
    label = "假合成后端（测试）"
    fields = (
        tts.TtsField(name="voice", key="fake_voice", label="音色", default="v1"),
    )
    is_fallback = False

    def __init__(self, *, fail=(), truncate=(), unavailable: str = "",
                 dead=(), resolved: str = "v-default") -> None:
        self.fail = set(fail)
        self.truncate = set(truncate)
        self.unavailable = unavailable
        self.dead = set(dead)
        self.resolved = resolved
        self.calls: list[str] = []

    def plan(self, values):
        return {"ext": "wav"}

    def flavor(self, values):
        return f"fake|{values.get('voice') or ''}"

    def availability(self, values):
        return self.unavailable

    def preflight(self, values):
        """配置里的音色"已下架"时换默认音色（与 edge 的 preflight 同语义）。"""
        if values.get("voice") in self.dead:
            return {**values, "voice": self.resolved}, "音色已下架，改用默认音色"
        return values, ""

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
    # 预缓存开关默认关着（产品默认），这里默认打开：本文件的 `run_precache` 用例
    # 验的都是"开关开着时这一批怎么跑"，关着那一侧由 start_precache / AppShell 的
    # 用例单独守。
    cfg.set("self_talk_voice_precache_enabled", True)
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


# ------------------------------------------- 键 = 实际合成输入的指纹（缺陷 A / B）


def test_changing_the_voice_marks_the_old_line_as_uncached(tmp_path, fake_tts):
    """① 换音色后旧句子不能再被当成"已缓存"，点击侧也不能命中另一把嗓子。

    缺陷 A：键里只有文本时，试听已经换了新声音、点击台词仍是旧声音，而且保存设置 /
    重启 / 重开预缓存都不会重合成（"有文件＝已缓存"永远成立）。键带上 provider
    flavor 之后，换音色就等于换键。
    """
    provider = fake_tts()
    cfg = _cfg(tmp_path, backend=provider.id, fake_voice="v1")
    stv.run_precache(cfg)
    first = stv.find_audio(cfg.dir, "好女孩……", cfg)
    assert first is not None, "首次预缓存必须落出可播音频"

    cfg.set("fake_voice", "v2")  # 用户换了音色
    assert stv.find_audio(cfg.dir, "好女孩……", cfg) is None, (
        "换音色后旧音频不算命中——否则就是「试听换了声音、点击还是旧声音」")

    provider.calls.clear()
    stats = stv.run_precache(cfg)
    assert "好女孩……" in provider.calls, "新音色必须重新合成"
    assert stats["made"] == 3
    second = stv.find_audio(cfg.dir, "好女孩……", cfg)
    assert second is not None and second != first, "新音色的产物落在新键上"


def test_classic_text_wav_still_hits_but_never_outranks_the_current_flavor(tmp_path, fake_tts):
    """② 兼容不丢：外部脚本的经典文本 wav 仍命中，但当前配置的产物优先于它。

    两种来源的身份是分开的：经典缓存没有 flavor（纯文本命中），注册表产物按 flavor
    命名。查找顺序「当前配置 → 经典」，所以经典缓存既不会作废，也不会挡住新音色
    （合成侧的"还缺什么"只认当前配置的产物，不拿经典缓存当已缓存）。
    """
    provider = fake_tts()
    cfg = _cfg(tmp_path, backend=provider.id)
    classic = stv.cache_path(cfg.dir, "好女孩……")
    classic.parent.mkdir(parents=True, exist_ok=True)
    classic.write_bytes(_wav())

    assert stv.cached_audio(cfg.dir, "好女孩……") == classic, "外部脚本的老缓存必须仍命中"
    assert stv.find_audio(cfg.dir, "好女孩……", cfg) == classic, "当前配置还没产物时回退经典缓存"

    provider.calls.clear()
    stats = stv.run_precache(cfg)

    assert "好女孩……" in provider.calls, "经典缓存不能压掉按当前音色的合成"
    assert stats["made"] == 3
    fresh = stv.find_audio(cfg.dir, "好女孩……", cfg)
    assert fresh is not None and fresh != classic, "当前配置的产物优先于经典缓存"


def test_cached_line_still_plays_after_the_backend_became_unavailable(tmp_path, fake_tts):
    """后端此后不可用（依赖被卸 / Key 被清）时，已合出来的台词照样能播。

    "断网可用"是这条产品承诺的核心：查找只按当前配置算键（preflight 同源），不判
    运行期可用性——否则 edge-tts 一坏，几十个已经下好的音频文件全部变成"没声音"。
    """
    provider = fake_tts()
    cfg = _cfg(tmp_path, backend=provider.id)
    stv.run_precache(cfg)
    produced = stv.find_audio(cfg.dir, "好女孩……", cfg)
    assert produced is not None

    provider.unavailable = "fake_missing"

    assert stv.find_audio(cfg.dir, "好女孩……", cfg) == produced


def test_precache_names_the_file_after_the_resolved_voice(tmp_path, fake_tts):
    """③ 缺陷 B 的台词侧：preflight 回退后，键必须按**实际合成**的音色算。

    按配置原音色命名会让产物与文件身份不符：用户显式改成那把回退音色时要再合一遍，
    将来原音色恢复又可能继续命中回退的声音。
    """
    provider = fake_tts(dead={"dead-voice"})
    cfg = _cfg(tmp_path, backend=provider.id, fake_voice="dead-voice")

    stv.run_precache(cfg)

    resolved_key = cache_key("好女孩……", provider_id=provider.id,
                             values={"voice": provider.resolved})
    configured_key = cache_key("好女孩……", provider_id=provider.id,
                               values={"voice": "dead-voice"})
    assert resolved_key != configured_key, "两种音色必须是两个键"
    assert (_cache_dir(cfg.dir) / f"{resolved_key}.wav").is_file(), "产物按实际合成的音色命名"
    assert not (_cache_dir(cfg.dir) / f"{configured_key}.wav").exists(), (
        "按配置原音色命名会让回退产物与文件身份不符")

    provider.calls.clear()
    cfg.set("fake_voice", provider.resolved)  # 用户显式改成同一把嗓子
    assert stv.find_audio(cfg.dir, "好女孩……", cfg) is not None
    assert provider.calls == [], "同一把嗓子不该因为换了写法再合一遍"


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
    target = stv.find_audio(cfg.dir, "好女孩……", cfg)
    assert target is not None and target.is_file()
    assert sorted(provider.calls) == ["再陪你一会儿。", "好女孩……", "绑定的专属台词。"]


def test_run_precache_skips_lines_that_already_have_audio(tmp_path, fake_tts):
    provider = fake_tts()
    cfg = _cfg(tmp_path, backend=provider.id)
    stv.run_precache(cfg)  # 先按当前音色补齐，拿到"当前配置的产物"
    cached = stv.find_audio(cfg.dir, "绑定的专属台词。", cfg)
    provider.calls.clear()

    stats = stv.run_precache(cfg)

    assert stats["made"] == 0 and stats["skipped"] == 3
    assert cached is not None and cached.read_bytes() == _wav(), "已缓存的句子不该被重合成覆盖"
    assert provider.calls == []


def test_run_precache_keeps_going_after_one_failure(tmp_path, fake_tts):
    provider = fake_tts(fail={"好女孩……"})
    cfg = _cfg(tmp_path, backend=provider.id)

    stats = stv.run_precache(cfg)

    assert stats["made"] == 2 and stats["failed"] == 1
    assert stv.find_audio(cfg.dir, "再陪你一会儿。", cfg) is not None
    assert stv.find_audio(cfg.dir, "好女孩……", cfg) is None


def test_run_precache_never_publishes_a_truncated_clip(tmp_path, fake_tts):
    """⑤ 合成只写出残片时既不上账，也不留残file（它会永远被当成命中）。"""
    provider = fake_tts(truncate={"好女孩……"})
    cfg = _cfg(tmp_path, backend=provider.id)

    stats = stv.run_precache(cfg)

    assert stats["failed"] == 1 and stats["made"] == 2
    assert stv.find_audio(cfg.dir, "好女孩……", cfg) is None
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
    monkeypatch.setattr(stv, "_pending_run", None)

    def slow_run(*_a, **_k):
        entered.set()
        release.wait(5.0)

    monkeypatch.setattr(stv, "run_precache", slow_run)

    assert stv.start_precache(cfg) is True
    assert entered.wait(5.0)
    assert stv.start_precache(cfg) is False, "上一轮还在跑时不该再起一轮"
    release.set()


def _wait_until(predicate, budget: float = 10.0) -> bool:
    """轮询等待状态收敛（宽预算、事件式判据，不固定 sleep 猜时序）。"""
    import time

    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_lines_added_during_a_run_are_picked_up_after_the_batch(tmp_path, fake_tts, monkeypatch):
    """④ 缺陷 C 前半：合成中追加台词并保存，当前批结束后必须补跑一轮。

    旧实现单飞时直接丢弃新请求，而名单只在本轮开始时取一次：新台词要等到下次保存
    设置或重启才有缓存（用户看到的是"刚加的台词点了没声音/走了联网合成"）。
    """
    provider = fake_tts()
    cfg = _cfg(tmp_path, backend=provider.id)
    cfg.set("self_talk_voice_precache_enabled", True)
    monkeypatch.setattr(stv, "_active", None)
    monkeypatch.setattr(stv, "_pending_run", None)

    started = threading.Event()
    release = threading.Event()
    synth = provider.synth

    def slow_synth(text, plan, out_path):
        started.set()
        assert release.wait(10.0), "第一批的合成没被放行"
        synth(text, plan, out_path)

    provider.synth = slow_synth

    try:
        assert stv.start_precache(cfg, configs=[cfg]) is True
        assert started.wait(10.0), "第一批合成没开始"

        cfg.set("self_talk_texts", ["好女孩……", "再陪你一会儿。", "合成期间新加的一句。"])
        assert stv.start_precache(cfg, configs=[cfg]) is False, "单飞保留：不并发起第二轮"

        release.set()
        assert _wait_until(
            lambda: stv.find_audio(cfg.dir, "合成期间新加的一句。", cfg) is not None), (
            "当前批结束后必须按最新名单补跑，新台词不能永久错过")
    finally:
        release.set()
        _wait_until(lambda: not (stv._active and stv._active.is_alive()))


def test_switching_the_setting_off_stops_starting_new_syntheses(tmp_path, fake_tts):
    """⑤ 缺陷 C 后半：合成期间关掉开关，句间就停手，不再发后续请求。

    不要求取消已发出的那一次（那是 provider 自己的超时），只要求"不再开新的"：
    关掉开关之后一条都不该再发出去。
    """
    provider = fake_tts()
    cfg = _cfg(tmp_path, backend=provider.id)
    cfg.set("self_talk_voice_precache_enabled", True)
    synth = provider.synth

    def synth_and_switch_off(text, plan, out_path):
        synth(text, plan, out_path)
        cfg.set("self_talk_voice_precache_enabled", False)  # 用户在合成期间关掉

    provider.synth = synth_and_switch_off

    stats = stv.run_precache(cfg, configs=[cfg])

    assert provider.calls == ["好女孩……"], "句间检查：关掉开关后不许再开新的合成"
    assert stats["made"] == 1
    assert stats["reason"] == "switched-off"


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
    cfg.set("self_talk_voice_precache_enabled", False)
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
    cfg.set("self_talk_voice_precache_enabled", False)
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

    produced = stv.find_audio(cfg.dir, "好女孩……", cfg)
    assert played == [str(produced)]
    assert produced != stv.cache_path(cfg.dir, "好女孩……"), "注册表产物按 flavor 命名"
    assert shell.self_talk_voice_file("好女孩……") == produced
