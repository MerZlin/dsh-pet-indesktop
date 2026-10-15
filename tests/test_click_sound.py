# -*- coding: utf-8 -*-
"""点击音效播放、缓存与包解析测试。"""
from __future__ import annotations

import random
import sys
import time
import types
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QTimer

from pet import click_sound
from pet import window as window_mod


class FakeQtAudio:
    def __init__(self) -> None:
        self.volume = 1.0

    def setVolume(self, v: float) -> None:
        self.volume = v


class FakeQtPlayer:
    def __init__(self) -> None:
        self.stopped = False
        self.source = None
        self.played = False
        self.audio_output = None

    def stop(self) -> None:
        self.stopped = True

    def setSource(self, qurl) -> None:
        self.source = qurl

    def play(self) -> None:
        self.played = True

    def setAudioOutput(self, audio) -> None:
        self.audio_output = audio


class FakeSignal:
    def connect(self, callback):
        self.callback = callback


class FakeQtEffect:
    instances = []

    def __init__(self):
        self.source = None
        self.volumes = []
        self.play_count = 0
        self.stop_count = 0
        self.loop_counts = []
        self.__class__.instances.append(self)

    def setSource(self, source):
        self.source = source

    def setVolume(self, volume):
        self.volumes.append(volume)

    def stop(self):
        self.stop_count += 1

    def setLoopCount(self, count):
        self.loop_counts.append(count)

    def play(self):
        self.play_count += 1


class FakeQtDecoder:
    def __init__(self):
        self.bufferReady = FakeSignal()
        self.finished = FakeSignal()
        self.error = FakeSignal()

    def setSource(self, source):
        self.source = source

    def start(self):
        self.error.callback("decode failed")


def _fake_classes():
    return (FakeQtDecoder, FakeQtAudio, FakeQtAudio, FakeQtPlayer, FakeQtEffect)


def _make_file(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(b"not-a-real-audio-file")
    return path


def test_wav_restarts_qsound_effect_on_each_click(monkeypatch, tmp_path):
    monkeypatch.setattr(click_sound, "os", SimpleNamespace(name="nt"))
    effect = FakeQtEffect()
    monkeypatch.setattr(click_sound._pool, "_qt_effects", {})
    monkeypatch.setattr(click_sound._pool, "qt_multimedia_classes", _fake_classes)

    path_wav = _make_file(tmp_path, "click.wav")
    assert click_sound.play_sound(path_wav, volume=0.5) is True
    assert click_sound.play_sound(path_wav, volume=0.5) is True
    assert len(FakeQtEffect.instances) >= 1
    assert FakeQtEffect.instances[-1].play_count == 2
    assert FakeQtEffect.instances[-1].volumes == [0.5, 0.5]
    # 回归：同一 QSoundEffect 实例每次播放前先 stop，防止部分 FFmpeg
    # 后端第二次 play 不重启导致“后续点击/试听无声”。
    assert FakeQtEffect.instances[-1].stop_count >= 2
    assert FakeQtEffect.instances[-1].loop_counts == [1, 1]


def test_mp3_decode_failure_falls_back_to_player_pool(monkeypatch, tmp_path):
    monkeypatch.setattr(click_sound, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(click_sound._pool, "_qt_effects", {})
    monkeypatch.setattr(click_sound._pool, "_qt_decoders", {})
    monkeypatch.setattr(click_sound._pool, "_qt_player_pool", [])
    monkeypatch.setattr(click_sound._pool, "_qt_player_index", 0)
    monkeypatch.setattr(click_sound._pool, "qt_multimedia_classes", _fake_classes)
    monkeypatch.setattr(click_sound, "_sound_cache_dir", lambda: tmp_path / "cache")

    path = _make_file(tmp_path, "click.mp3")
    assert click_sound.play_click_sound(path) is True
    assert len(click_sound._pool._qt_player_pool) == 4
    assert click_sound._pool._qt_player_pool[0][0].played is True


def test_nonwav_qt_unavailable_on_windows_skips_silently(monkeypatch, tmp_path):
    monkeypatch.setattr(click_sound, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(click_sound._pool, "qt_multimedia_classes", lambda: None)

    path = _make_file(tmp_path, "click.mp3")
    assert click_sound.play_click_sound(path) is False


def test_mp3_second_click_uses_decoded_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(click_sound, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(click_sound._pool, "_qt_effects", {})
    monkeypatch.setattr(click_sound._pool, "_qt_decoders", {})
    monkeypatch.setattr(click_sound._pool, "_qt_player_pool", [])
    monkeypatch.setattr(click_sound._pool, "_qt_player_index", 0)
    monkeypatch.setattr(click_sound._pool, "qt_multimedia_classes", _fake_classes)
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(click_sound, "_sound_cache_dir", lambda: cache_dir)
    path = _make_file(tmp_path, "click.mp3")

    class Decoder(FakeQtDecoder):
        def start(self):
            class Format:
                def sampleFormat(self): return 2
                def channelCount(self): return 1
                def sampleRate(self): return 8000
            class Buffer:
                def format(self): return Format()
                def data(self): return b"pcm"
            self.bufferAvailable = lambda: bool(getattr(self, "pending", True))
            self.read = lambda: (setattr(self, "pending", False) or Buffer())
            self.bufferReady.callback()
            self.finished.callback()

    monkeypatch.setattr(click_sound._pool, "qt_multimedia_classes", lambda: (Decoder, FakeQtAudio, FakeQtAudio, FakeQtPlayer, FakeQtEffect))
    assert click_sound.play_sound(path) is True
    assert list(cache_dir.glob("*.wav"))
    assert click_sound.play_sound(path) is True
    assert FakeQtEffect.instances[-1].play_count == 1


def test_warm_player_pool_precreates_qt_players(monkeypatch):
    monkeypatch.setattr(click_sound._pool, "qt_multimedia_classes", _fake_classes)
    monkeypatch.setattr(click_sound._pool, "_qt_player_pool", [])
    monkeypatch.setattr(click_sound._pool, "_qt_player", None)
    monkeypatch.setattr(click_sound._pool, "_qt_audio", None)

    click_sound._warm_player_pool()

    assert len(click_sound._pool._qt_player_pool) == 4


def test_warm_click_sound_effects_precreates_wav_effect_and_pool(monkeypatch, tmp_path):
    monkeypatch.setattr(click_sound._pool, "qt_available", lambda: True)
    monkeypatch.setattr(click_sound._pool, "qt_multimedia_classes", _fake_classes)
    monkeypatch.setattr(click_sound._pool, "_qt_effects", {})
    monkeypatch.setattr(click_sound._pool, "_qt_player_pool", [])
    monkeypatch.setattr(click_sound._pool, "_qt_player", None)
    monkeypatch.setattr(click_sound._pool, "_qt_audio", None)

    wav = _make_file(tmp_path, "click.wav")
    pack = {"kind": "file", "id": "custom", "path": str(wav)}
    click_sound.warm_click_sound_effects(pack, data_dir=tmp_path)

    assert str(wav.resolve()) in click_sound._pool._qt_effects
    assert len(click_sound._pool._qt_player_pool) == 4


def test_resolve_click_sound_candidates_and_choose(tmp_path):
    # 1. file mode
    f = _make_file(tmp_path, "test.mp3")
    pack_file = {"kind": "file", "id": "custom", "path": str(f)}
    candidates = click_sound.resolve_click_sound_candidates(pack_file)
    assert candidates == [f]
    assert click_sound.choose_sound(candidates) == f

    # 2. folder mode
    folder = tmp_path / "sounds_folder"
    folder.mkdir()
    f1 = _make_file(folder, "1.wav")
    f2 = _make_file(folder, "2.mp3")
    _make_file(folder, "ignored.txt")
    pack_folder = {"kind": "folder", "id": "custom", "path": str(folder)}
    candidates_folder = click_sound.resolve_click_sound_candidates(pack_folder)
    assert candidates_folder == [f1, f2]
    # deterministic choose via seeded rng
    rng = random.Random(42)
    chosen = click_sound.choose_sound(candidates_folder, rng=rng)
    assert chosen in {f1, f2}

    # 3. empty list
    assert click_sound.choose_sound([]) is None

    # 4. builtin duck pack
    pack_duck = {"kind": "builtin", "id": "duck", "path": ""}
    duck_candidates = click_sound.resolve_click_sound_candidates(pack_duck)
    assert len(duck_candidates) >= 2
    assert any(c.name == "Ya1.mp3" for c in duck_candidates)
    assert any(c.name == "Ya2.mp3" for c in duck_candidates)


def test_window_play_click_sound_uses_pack(monkeypatch, tmp_path):
    custom = _make_file(tmp_path, "custom.wav")
    cfg_dir = tmp_path / "data"
    cfg_dir.mkdir()

    class Cfg:
        dir = cfg_dir

        def get(self, key, default=None):
            if key == "click_sound_pack":
                return {"kind": "file", "id": "custom", "path": str(custom)}
            if key == "click_sound_volume":
                return 0.8
            return default

    class FakePet:
        click_sound_enabled = True
        cfg = Cfg()

    sent = []

    def capture(path, volume=1.0):
        sent.append((path, volume))
        return True

    monkeypatch.setattr(window_mod, "play_sound", capture)
    window_mod.PetWindow._play_click_sound(FakePet())
    assert sent == [(custom, 0.8)]


def test_duck_candidates_cached_until_reset(monkeypatch, tmp_path):
    duck = tmp_path / "assets" / "sounds" / "duck"
    duck.mkdir(parents=True)
    _make_file(duck, "Ya1.mp3")
    _make_file(duck, "Ya2.mp3")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    pack = {"kind": "builtin", "id": "duck"}

    first = click_sound.resolve_click_sound_candidates(pack)
    assert len(first) == 2

    # 缓存生效：进程运行期目录内容变化不再触发重扫（打包资源本就静态）
    _make_file(duck, "Ya3.mp3")
    assert click_sound.resolve_click_sound_candidates(pack) == first

    # 返回的是副本：调用方改动不污染缓存
    first.append(duck / "bogus.wav")
    assert len(click_sound.resolve_click_sound_candidates(pack)) == 2

    # 测试复位后重新扫描
    click_sound._reset_caches_for_tests()
    assert len(click_sound.resolve_click_sound_candidates(pack)) == 3


def test_resolve_click_sound_pair_duck_and_non_duck(monkeypatch, tmp_path):
    press = _make_file(tmp_path, "Ya1.mp3")
    release = _make_file(tmp_path, "Ya2.mp3")
    monkeypatch.setattr(click_sound, "resolve_click_sound_candidates", lambda pack, data_dir=None: [press, release])
    monkeypatch.setattr(click_sound, "_cache_path", lambda path: path.with_suffix(".wav"))
    assert click_sound.resolve_click_sound_pair({"kind": "builtin", "id": "duck"}) == (press, release)
    press.with_suffix(".wav").write_bytes(b"")
    release.with_suffix(".wav").write_bytes(b"")
    assert click_sound.resolve_click_sound_pair({"kind": "builtin", "id": "duck"}) == (
        press.with_suffix(".wav"), release.with_suffix(".wav"))
    assert click_sound.resolve_click_sound_pair({"kind": "file", "id": "custom"}) is None


def test_press_sound_stops_release_and_restarts_press(monkeypatch, tmp_path):
    pair = (_make_file(tmp_path, "press.wav"), _make_file(tmp_path, "release.wav"))
    release_effect = SimpleNamespace(
        stopped=False, stop=lambda: setattr(release_effect, "stopped", True),
        setLoopCount=lambda count: None, setVolume=lambda volume: None,
    )
    played = []
    monkeypatch.setattr(click_sound._pool, "effect_for", lambda path: release_effect)
    monkeypatch.setattr(click_sound._pool, "play_sound", lambda path, volume=1.0: played.append((path, volume)) or True)
    assert click_sound.play_press_sound(pair, 0.6) is True
    assert release_effect.stopped is True
    assert played == [(pair[0], 0.6)]


def test_release_sound_schedules_press_tail(monkeypatch, tmp_path):
    press = tmp_path / "press.wav"
    release = tmp_path / "release.wav"
    for path in (press, release):
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(1000)
            out.writeframes(b"\0\0" * 1000)
    calls = []
    monkeypatch.setattr(click_sound._pool, "play_sound", lambda path, volume=1.0: calls.append((path, volume)) or True)
    monkeypatch.setattr(click_sound.time, "monotonic", lambda: 0.2)
    scheduled = []
    monkeypatch.setattr(QTimer, "singleShot", lambda delay, callback: scheduled.append((delay, callback)))
    click_sound.play_release_sound((press, release), 0.5, press_started_at=0.0)
    assert scheduled and scheduled[0][0] == 700
    scheduled[0][1]()
    assert calls == [(release, 0.5)]


def test_second_pool_instance_state_is_isolated_from_singleton(tmp_path):
    """批4：第二个 ClickSoundPool 实例与模块单例 _pool 的状态互相隔离。

    类方法一律走 self.<method>()，实例的可变状态（音效/解码器/播放器池/
    时长缓存/配对状态）必须各自独立。不造 Qt 对象，仅轻量状态断言。
    """
    other = click_sound.ClickSoundPool()
    singleton = click_sound._pool

    # 1) 可变状态容器不是同一对象
    for attr in ("_qt_effects", "_qt_decoders", "_qt_player_pool",
                 "_wav_duration_cache", "_click_pair_state"):
        assert getattr(other, attr) is not getattr(singleton, attr), attr

    # 2) 直接写互不串
    other._wav_duration_cache["a.wav"] = 1.5
    assert "a.wav" not in singleton._wav_duration_cache
    other._qt_effects["k"] = object()
    assert "k" not in singleton._qt_effects
    index_before = singleton._qt_player_index
    other._qt_player_index = 7
    assert singleton._qt_player_index == index_before

    # 3) 经实例方法写入只落在第二个实例：批4 前 play_with_effect 会经实例
    #    方法 effect_for 把音效写进单例 _pool._qt_effects（实例间串写）。
    wav = tmp_path / "tick.wav"
    with wave.open(str(wav), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(1000)
        out.writeframes(b"\0\0" * 100)

    assert other.wav_duration(wav) == 0.1
    assert str(wav.resolve()) in other._wav_duration_cache
    assert str(wav.resolve()) not in singleton._wav_duration_cache

    class _FakeEffect:
        def __init__(self):
            self.source = None

        def setSource(self, source):
            self.source = source

        def setVolume(self, volume):
            pass

        def play(self):
            pass

    other.qt_multimedia_classes = lambda: (None, None, None, None, _FakeEffect)
    assert other.play_with_effect(wav, 0.5) is True
    assert str(wav.resolve()) in other._qt_effects
    assert str(wav.resolve()) not in singleton._qt_effects


class _StubQSoundEffect:
    """QSoundEffect 的最小替身：只提供产品代码判定所需的 Status 成员。

    Ubuntu CI runner 没有 libpulse，``import PySide6.QtMultimedia`` 会直接
    ImportError，因此测试不能依赖真实 QtMultimedia 可用性（否则同一用例在
    Windows 绿、Linux 红）。测试通过 ``_stub_qt_multimedia`` 把它注入
    ``sys.modules``，让 click_sound 的惰性 import 与本替身同源。
    """

    class Status:
        Null = "Null"
        Loading = "Loading"
        Ready = "Ready"
        Error = "Error"


class _StickyErrorEffect:
    """复刻 QSoundEffect 的错误态粘滞：status()==Error 时 play() 是静默空操作。

    实机取证见 .scratch/issue116-probe/probe_sound_status.py：对 status 停在
    Error 的 QSoundEffect 反复 play()，status 恒为 Error、isPlaying() 恒为
    False，既不抛异常也不打日志——调用方只能靠 status 识别，且只有重建对象
    才能恢复（真实表现就是"日志照打但没声"）。

    注意 PySide6 该枚举只有 Null/Loading/Ready/Error 四个成员，"正在播放"
    是 ``isPlaying`` 属性而非状态值，所以可播状态记为 Ready。
    """

    def __init__(self, path, status_value=None):
        self.status_value = _StubQSoundEffect.Status.Error if status_value is None else status_value
        self._path = path
        self.play_calls = 0
        self.source = None

    def status(self):
        return self.status_value

    def setSource(self, source):
        self.source = source

    def setLoopCount(self, count):
        pass

    def setVolume(self, volume):
        pass

    def stop(self):
        pass

    def play(self):
        self.play_calls += 1
        if self.status_value == _StubQSoundEffect.Status.Error:
            return  # 与真实 Qt 一致：静默无效
        self.status_value = _StubQSoundEffect.Status.Ready


def _stub_qt_multimedia(monkeypatch):
    """把 PySide6.QtMultimedia 换成只含 QSoundEffect 的桩模块。

    click_sound 的错误判定走 ``from PySide6.QtMultimedia import QSoundEffect``，
    桩化后该 import 在任何平台都成立，用例不再受本机音频库缺失影响。
    """
    module = types.ModuleType("PySide6.QtMultimedia")
    module.QSoundEffect = _StubQSoundEffect
    monkeypatch.setitem(sys.modules, "PySide6.QtMultimedia", module)
    return _StubQSoundEffect


def _click_wav(tmp_path, name="Ya1.wav"):
    wav = tmp_path / name
    with wave.open(str(wav), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(1000)
        out.writeframes(b"\0\0" * 100)
    return wav


def _install_effect_factory(pool, *, fresh_error: bool = False):
    """把池的 Qt 多媒体接缝换成可观测替身，并保持真实缓存语义。

    只替换 Qt 类（操作系统/多媒体边界）：``effect_for`` 仍是产品代码，缓存
    与重建逻辑照旧走产品路径，因此测试验证的是真实的"复用 vs 重建"契约。
    """
    created = []

    def factory():
        # 重建出来的新对象：默认可用（模拟真实设备恢复后的情形）；
        # fresh_error=True 表示音频设备整体不可用，新对象依旧停在 Error。
        effect = _StickyErrorEffect(
            None, None if fresh_error else _StubQSoundEffect.Status.Ready)
        created.append(effect)
        return effect

    pool.qt_multimedia_classes = lambda: (None, None, None, None, factory)
    return created


def test_play_with_effect_recovers_from_sticky_qt_error(tmp_path, monkeypatch):
    """回归（issue #116）：音效对象粘在 Error 后，下一次点击必须重新出声。

    现象：小黄鸭音效（Ya1/Ya2 缓存 WAV）首次点击正常，放置一段时间后
    （音频端点被切换/休眠/独占）再点就没声音，日志却照打——因为池复用
    status==Error 的旧对象，而 Qt 对 Error 对象的 play() 是永久空操作。
    """
    _stub_qt_multimedia(monkeypatch)
    wav = _click_wav(tmp_path)
    pool = click_sound.ClickSoundPool()
    created = _install_effect_factory(pool)

    # 第一次点击：音效正常，对象进入缓存
    assert pool.play_with_effect(wav, 0.7) is True
    assert len(created) == 1 and created[0].play_calls == 1

    # 期间音频端点被切走 → 缓存里的对象落到粘滞错误态
    cached = created[0]
    cached.status_value = _StubQSoundEffect.Status.Error
    key = str(wav.resolve())
    assert pool._qt_effects.get(key) is cached

    # 第二次点击：必须丢弃坏对象、重建后真正播出去
    assert pool.play_with_effect(wav, 0.7) is True
    assert len(created) == 2, "错误态旧对象未被丢弃重建，点击音效会永久静音"
    rebuilt = created[1]
    assert rebuilt.play_calls == 1, "重建后的对象必须真的被播放"
    assert rebuilt.status_value == _StubQSoundEffect.Status.Ready, "重建后的对象必须回到可播状态"
    assert pool._qt_effects.get(key) is rebuilt, "缓存必须换成本次重建的可用对象"


def test_play_with_effect_returns_false_when_error_persists(tmp_path, monkeypatch):
    """重建后仍无法播放（音频设备整体不可用）时如实返回 False，不再谎报成功。"""
    _stub_qt_multimedia(monkeypatch)
    wav = _click_wav(tmp_path)
    pool = click_sound.ClickSoundPool()
    created = _install_effect_factory(pool, fresh_error=True)  # 新对象也停在 Error
    key = str(wav.resolve())

    assert pool.play_with_effect(wav, 0.7) is False
    assert len(created) == 2, "应重建一次后放弃，且不得无界重建"
    assert key not in pool._qt_effects, "不可用的对象不得留在缓存里反复重试"


def test_click_sound_immediate_toggle_in_dialog_affects_pet_window(tmp_path, monkeypatch):
    """回归测试：设置对话框中即时关闭点击音效，桌宠窗口点击立即不播放。"""
    from pet.config import Config
    from pet.window import PetWindow
    from pet.modern_settings_dialog import ModernSettingsDialog
    from PySide6.QtWidgets import QApplication
    from types import SimpleNamespace

    app = QApplication.instance() or QApplication([])
    # #271：主宠（空身份）的设置会话按 slot0 分流，点击音效是每宠键、真值在
    # config-slot-0.json。运行期那份配置面因此也是路由视图（PetWindow 读的就是它），
    # 否则设置窗写档案、窗口读主配置，即时生效变成空话。初值同样要落盘再开窗：
    # 路由视图的读源只取盘。
    from pet.settings_slot_config import route_settings_config

    config = route_settings_config(Config(base=tmp_path, instance_id="slot-0"))
    config.set("click_sound_enabled", True)
    assert config.save() is True

    win = PetWindow.__new__(PetWindow)
    win.cfg = config

    # 初始状态下属性读取 cfg 为 True
    assert win.click_sound_enabled is True

    # 模拟设置对话框即时关闭
    monkeypatch.setattr("pet.modern_settings_dialog.autostart_mod.is_enabled", lambda: False)
    dialog = ModernSettingsDialog(config, include_ai=False)
    assert dialog.click_sound_check.isChecked() is True

    played = []
    monkeypatch.setattr("pet.window.play_sound", lambda *a, **k: played.append(a))
    monkeypatch.setattr("pet.window.play_press_sound", lambda *a, **k: played.append(a))

    # 关闭点击音效
    dialog.click_sound_check.setChecked(False)

    # 验证即时写回 config 且 PetWindow 读到 False
    assert config.get("click_sound_enabled") is False
    assert win.click_sound_enabled is False

    # 触发播放点击音效
    win._play_click_sound()
    assert played == []

    dialog.close()
    app.processEvents()




def test_cache_path_stable_across_mtime_change(tmp_path):
    """转码缓存键用内容哈希：mtime 变（重新部署素材）不应对缓存失配。

    回归：键曾是 path:mtime:size——每次重新部署（文件复制刷新 mtime）后
    首次点击必重转码，并因此拉起 QtMultimedia ffmpeg 后端常驻 +40~74MB。
    """
    from pet import click_sound

    src = tmp_path / "ding.mp3"
    src.write_bytes(b"fake-mp3-content")
    first = click_sound._cache_path(src)
    # 同一内容、mtime 变了（模拟重新部署）
    import os
    st = src.stat()
    os.utime(src, ns=(st.st_atime_ns + 1_000_000_000, st.st_mtime_ns + 1_000_000_000))
    assert click_sound._cache_path(src) == first
    # 内容变了才换键
    src.write_bytes(b"different-content")
    assert click_sound._cache_path(src) != first


def test_cache_path_memoizes_content_hash(tmp_path, monkeypatch):
    """内容哈希经 (path,mtime,size) memo 只算一次（点击热路径不反复整读）。"""
    from pet import click_sound

    src = tmp_path / "ding.mp3"
    src.write_bytes(b"fake-mp3-content")
    click_sound._DIGEST_MEMO.clear()
    calls = []
    real_read_bytes = type(src).read_bytes

    def counting_read_bytes(self):
        calls.append(1)
        return real_read_bytes(self)

    monkeypatch.setattr(type(src), "read_bytes", counting_read_bytes)
    assert click_sound._cache_path(src) == click_sound._cache_path(src)
    assert len(calls) == 1
    click_sound._DIGEST_MEMO.clear()


def test_cache_path_falls_back_to_stat_key_when_unreadable(tmp_path, monkeypatch):
    """内容读不到（ACL/独占）时退回 stat 键，绝不让缓存键计算炸掉播放。"""
    from pet import click_sound

    src = tmp_path / "locked.mp3"
    src.write_bytes(b"x")
    click_sound._DIGEST_MEMO.clear()

    def boom(self):
        raise OSError("locked")

    monkeypatch.setattr(type(src), "read_bytes", boom)
    path = click_sound._cache_path(src)  # 不抛错
    assert path.suffix == ".wav"
    click_sound._DIGEST_MEMO.clear()


# ---------------------------------------------------------------------------
# 碰撞音效每次发声的路径解析成本（C1）
#
# 实机取证（.scratch/windows-parity-20260926-a/fix-20260928-C1/bench-before.json）：
# `_sound_cache_dir()` 0.25ms（QStandardPaths + mkdir 每次发声都跑一次）、
# `Path(__file__).resolve()` 0.22ms（音源根目录每次发声都重新 resolve）。
# 两项都在 tick 里的碰撞事件链上（每秒最多 12.5 次）。
# ---------------------------------------------------------------------------

def test_sound_cache_dir_resolved_once_per_process(monkeypatch):
    """缓存目录只向 QStandardPaths 解析一次；测试复位后可重新解析。"""
    from PySide6.QtCore import QStandardPaths

    from pet import click_sound

    click_sound._reset_caches_for_tests()
    calls = []
    real = QStandardPaths.writableLocation

    def counting(location):
        calls.append(location)
        return real(location)

    monkeypatch.setattr(QStandardPaths, "writableLocation", staticmethod(counting))
    first = click_sound._sound_cache_dir()
    assert click_sound._sound_cache_dir() == first
    assert len(calls) == 1, "同一进程内缓存目录稳定，不得每次发声都重新解析并 mkdir"

    click_sound._reset_caches_for_tests()
    assert click_sound._sound_cache_dir() == first
    assert len(calls) == 2, "测试复位后允许重新解析"


def test_cache_path_does_not_resolve_source_path(monkeypatch, tmp_path):
    """缓存键不得每次发声都做 resolve()（Windows 上每源两次 GetFinalPathName）。"""
    from pathlib import Path as _Path

    from pet import click_sound

    src = tmp_path / "ding.mp3"
    src.write_bytes(b"fake-mp3-content")
    click_sound._DIGEST_MEMO.clear()
    counts = []
    real_resolve = _Path.resolve

    def counting(self, *args, **kwargs):
        counts.append(str(self))
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(_Path, "resolve", counting)
    first = click_sound._cache_path(src)
    assert first == click_sound._cache_path(src)
    assert counts == [], "缓存键只需要 stat + abspath，不得走 resolve 系统调用"
    click_sound._DIGEST_MEMO.clear()


def test_sounds_root_resolved_once_per_meipass(monkeypatch):
    """音源根目录不得每次发声都重新 resolve（Windows 上是一次 GetFinalPathName）。"""
    from pathlib import Path as _Path

    from pet import click_sound

    click_sound._reset_caches_for_tests()
    counts = []
    real_resolve = _Path.resolve

    def counting(self, *args, **kwargs):
        counts.append(str(self))
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(_Path, "resolve", counting)
    pack = {"kind": "builtin", "id": "duck"}
    click_sound.resolve_click_sound_candidates(pack)
    before = len(counts)
    assert before >= 1, "首次解析仍需 resolve 一次"
    click_sound.resolve_click_sound_candidates(pack)
    assert len(counts) == before, "第二次解析不得再 resolve 音源根目录"


# ===========================================================================
# #199 非 Windows 回退播放器的子进程回收
# ===========================================================================

@pytest.fixture
def system_player_registry():
    """隔离回退播放器的句柄登记表，并在用例结束时清干净（模块级状态不串味）。"""
    click_sound._SYSTEM_PLAYERS[:] = []
    yield click_sound._SYSTEM_PLAYERS
    for proc in list(click_sound._SYSTEM_PLAYERS):
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=30)
    click_sound._SYSTEM_PLAYERS[:] = []


def _install_python_as_player(monkeypatch) -> None:
    """把回退播放器指到 Python 自身：起的是**真子进程**，且立刻退出。

    `_play_with_system_player` 只负责 "which 找得到就从命令行拉起"，用解释器
    顶替 afplay/paplay 后命令是 `python <非源码文件>`——秒退且不依赖平台播放器
    是否安装，回收语义（poll 是否被调用）才是本组用例的观测对象。
    """
    monkeypatch.setattr(
        click_sound, "shutil", SimpleNamespace(which=lambda name: sys.executable))


def test_system_player_child_is_reaped_after_exit(monkeypatch, tmp_path, system_player_registry):
    """#199：拉起的播放器句柄必须登记并在退出后被 poll 回收（POSIX 僵尸）。

    旧实现丢弃 Popen 返回值且全文件无 poll/wait：每次点击音都留一个僵尸，
    桌宠长开就是"调几次留几个"。
    """
    target = _make_file(tmp_path, "click.wav")
    _install_python_as_player(monkeypatch)

    assert click_sound._play_with_system_player(target) is True, "起播成功仍须返回 True"

    tracked = list(system_player_registry)
    assert len(tracked) == 1, "拉起的播放器句柄必须登记（不登记就没有任何回收点）"
    proc = tracked[0]
    assert proc.pid > 0, "登记的必须是真子进程句柄"

    assert proc.wait(timeout=30) is not None, "顶替播放器应自然退出"
    click_sound._reap_system_players()
    assert system_player_registry == [], "已退出的句柄必须被收割（否则僵尸累积）"


def test_system_player_registry_does_not_grow_with_play_count(
    monkeypatch, tmp_path, system_player_registry,
):
    """#199：登记表只留活着的子进程——播放前先收割，不随播放次数增长。

    断言用 `<= 1`：登记表**另外**还有一条不依赖下次播放的自回收路径（本文件的
    `test_system_player_registry_self_reaps_without_another_play`），已退出的条目
    可能在那条路径上先被拿走，两条路径都不允许表里堆死句柄。
    """
    target = _make_file(tmp_path, "click.wav")
    _install_python_as_player(monkeypatch)

    for _ in range(3):
        assert click_sound._play_with_system_player(target) is True
        for proc in list(system_player_registry):
            proc.wait(timeout=30)  # 每次都等它退

    assert len(system_player_registry) <= 1, \
        f"3 次播放后登记表不得堆积已退出的句柄，实得 {len(system_player_registry)}"

    assert click_sound._play_with_system_player(target) is True
    assert len(system_player_registry) <= 1, \
        f"再播放一次仍不得堆积（收割 + 登记各一次），实得 {len(system_player_registry)}"


def test_system_player_registry_self_reaps_without_another_play(
    monkeypatch, tmp_path, system_player_registry,
):
    """#199：最后一批播放器退出后必须被回收——回收点不得挂在"下次播放"上。

    旧实现只在 `_play_with_system_player` 开头收割，而点击音是"不点就不播"的
    高频动作：最后一批退出之后没有任何后续调用，句柄/僵尸就一直留在表里
    （POSIX 僵尸、Windows 句柄滞留）。

    观测纪律：本用例**不碰** `Popen.wait()/poll()`——在测试里先自己 wait 一遍
    就等于测试亲手把它回收了，缺陷会被掩盖。子进程退出只通过它自己写出的哨兵
    文件观察，登记表是否清空由被测代码负责。
    """
    monkeypatch.setattr(click_sound, "_REAP_INTERVAL_S", 0.05)
    sentinel = tmp_path / "child-exited"
    script = tmp_path / "player.py"
    script.write_text(
        "import pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text('x', encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        click_sound, "shutil", SimpleNamespace(which=lambda name: sys.executable))
    real_popen = click_sound.subprocess.Popen

    def popen(command, **kwargs):
        # 顶替播放器：起的是**真子进程**，秒退并留下哨兵文件
        return real_popen([sys.executable, str(script), str(sentinel)], **kwargs)

    monkeypatch.setattr(click_sound.subprocess, "Popen", popen)

    assert click_sound._play_with_system_player(_make_file(tmp_path, "click.wav")) is True
    assert len(system_player_registry) == 1, "拉起的播放器句柄必须登记"

    deadline = time.monotonic() + 30
    while not sentinel.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert sentinel.exists(), "顶替播放器未自然退出"

    deadline = time.monotonic() + 30  # 宽预算：CI 慢机不赌时序
    while system_player_registry and time.monotonic() < deadline:
        time.sleep(0.02)
    assert system_player_registry == [], \
        "已退出的最后一批播放器必须自行被回收（旧实现只在下一次播放前收割）"


def test_system_player_missing_player_registers_nothing(monkeypatch, tmp_path, system_player_registry):
    """找不到播放器时不得登记空条目（只有真拉起的进程才进登记表）。"""
    target = _make_file(tmp_path, "click.wav")
    monkeypatch.setattr(click_sound, "shutil", SimpleNamespace(which=lambda name: None))

    assert click_sound._play_with_system_player(target) is False
    assert system_player_registry == []


def test_reaper_start_failure_does_not_break_playback(
    monkeypatch, tmp_path, system_player_registry, caplog,
):
    """收割线程起不来（RuntimeError）既不得拖累播放，也不得让登记项永久滞留。

    旧实现把 `Thread.start()` 的 RuntimeError 原样从 `_ensure_reaper` 抛出去，而
    `_play_with_system_player` 唯一的 except 是 OSError——异常继续穿到 `play_sound`
    （那里没有 try）再穿进 GUI 调用栈：一次点击音换一次抛错；同时那条已经 Popen
    起来的句柄失去唯一的周期回收点（登记表里没人再碰它）。

    取舍（最小可靠方案）：起不了线程只记 warning、不向调用方抛错，回收退到既有
    路径——`_play_with_system_player` 入口的同步收割（该路径本来就在，用于"不点
    就不播"的最后一批之前）。播放功能不该被回收机制拖累。
    """
    monkeypatch.setattr(click_sound, "_REAPER", None)
    _install_python_as_player(monkeypatch)

    reap_calls: list[int] = []
    real_reap = click_sound._reap_system_players

    def counting_reap() -> None:
        reap_calls.append(1)
        real_reap()

    monkeypatch.setattr(click_sound, "_reap_system_players", counting_reap)

    class _RefusingThread:
        """顶替 threading.Thread：start() 必抛 RuntimeError（线程名额耗尽）。"""

        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            raise RuntimeError("can't start new thread")

        def is_alive(self):
            return False

    monkeypatch.setattr(
        click_sound, "threading", SimpleNamespace(Thread=_RefusingThread))

    target = _make_file(tmp_path, "click.wav")

    caplog.set_level("WARNING")
    assert click_sound._play_with_system_player(target) is True, \
        "起不了收割线程也必须照常播放（回收机制不得拖累播放）"

    assert len(system_player_registry) == 1, "拉起的句柄仍要登记"
    assert click_sound._REAPER is None, \
        "没起来的线程不得留在 _REAPER 里（状态必须与事实一致，下次登记才能重试）"
    assert "收割" in caplog.text, f"启动失败必须留下可发现的日志：{caplog.text!r}"

    first = system_player_registry[0]
    assert first.wait(timeout=30) is not None, "顶替播放器应自然退出"

    assert click_sound._play_with_system_player(target) is True
    assert len(reap_calls) >= 2, \
        f"每次播放入口的同步收割必须仍然活着（否则登记项永久滞留）：{len(reap_calls)}"
    assert first not in system_player_registry, \
        "已退出的句柄必须被下次播放前的同步收割拿走（永久滞留 = 句柄泄漏）"
    assert len(system_player_registry) <= 1, \
        f"收割 + 登记各一次，表里最多剩新拉起的那一个：{len(system_player_registry)}"
