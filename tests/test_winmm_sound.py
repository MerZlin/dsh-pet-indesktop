# -*- coding: utf-8 -*-
"""winmm(waveOut) 直放路径测试。

覆盖四条契约：
1. winmm 可用时播放 wav 全程不加载 QtMultimedia / ffmpeg（子进程取证，
   因为同进程里其它用例早已 import 过 QtMultimedia）；
2. winmm 池的并发语义：快速连播优先占用空闲句柄、不足时增长、全忙时排队，
   一个音都不丢、不抛异常；
3. 音量映射边界（0 / 0.5 / 1.0）与 set_audio_volume 的语义对齐；
4. winmm 不可用 / 打开设备失败 / 写入失败时逐级回退到既有 Qt 路径；
   预热在 winmm 可用时只预热 winmm 池，绝不创建 QSoundEffect/QMediaPlayer。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import textwrap
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from pet import click_sound, sound_winmm

WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="winmm 仅 Windows")
ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 装置：ctypes 边界的整体替身
# ---------------------------------------------------------------------------

class FakeWinmmApi:
    """``sound_winmm.WinmmApi`` 的最小替身（一个方法不多、一个不少）。

    产品代码只通过这一层触碰 winmm.dll，因此替身能完整观测池语义：
    开了几个设备、往哪个句柄写了多少字节、音量 packed 值是多少。
    """

    def __init__(self, *, fail_open: bool = False, fail_write: bool = False) -> None:
        self.opened: list[tuple[int, int, int]] = []
        self.writes: list[tuple[int, bytes]] = []
        self.volumes: list[tuple[int, int]] = []
        self.closed: list[int] = []
        self.reaped: list[int] = []
        self.resets: list[int] = []
        # 复刻 WAVEHDR 的两种状态：在队列里（WHDR_INQUEUE）与已播完待回收（WHDR_DONE）。
        # reap 只放行"已播完"的，pending_count 两者都算占用——否则测试会误以为
        # 写完即播完，掩盖真实设备上"上一声还在播"的并发语义。
        self._inflight: dict[int, int] = {}
        self._done: dict[int, int] = {}
        self._next_handle = 1
        self.open_calls = 0
        self.fail_open = fail_open
        self.fail_write = fail_write

    def device_count(self) -> int:
        return 1

    def open(self, channels: int, sample_rate: int, bits: int = 16) -> int:
        self.open_calls += 1
        if self.fail_open:
            raise sound_winmm.WinmmError("waveOutOpen failed")
        handle = self._next_handle
        self._next_handle += 1
        self.opened.append((handle, channels, sample_rate))
        self._inflight[handle] = 0
        self._done[handle] = 0
        return handle

    def close(self, handle: int) -> None:
        self.closed.append(handle)
        self._inflight.pop(handle, None)
        self._done.pop(handle, None)

    def write(self, handle: int, pcm: bytes) -> None:
        if self.fail_write:
            raise sound_winmm.WinmmError("waveOutWrite failed")
        self.writes.append((handle, pcm))
        self._inflight[handle] = self._inflight.get(handle, 0) + 1

    def pending_count(self, handle: int) -> int:
        return self._inflight.get(handle, 0) + self._done.get(handle, 0)

    def reap(self, handle: int) -> int:
        self.reaped.append(handle)
        return self._done.pop(handle, 0)

    def reset(self, handle: int) -> None:
        self.resets.append(handle)
        self._done[handle] = self._done.get(handle, 0) + self._inflight.pop(handle, 0)

    def set_volume(self, handle: int, packed: int) -> None:
        self.volumes.append((handle, packed))

    # 测试侧用于模拟"这一路播放完成（WHDR_DONE 置位）"
    def finish(self, handle: int) -> None:
        self._done[handle] = self._done.get(handle, 0) + self._inflight.pop(handle, 0)


def _pcm16_wav(path: Path, *, channels: int = 1, rate: int = 22050, samples: int = 3528) -> Path:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\x00\x10" * samples)
    return path


def _pcm8_wav(path: Path, *, channels: int = 1, rate: int = 22050, samples: int = 100) -> Path:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(1)
        out.setframerate(rate)
        out.writeframes(bytes([128, 200, 60, 255] * samples))
    return path


class FakeQtAudio:
    def __init__(self) -> None:
        self.volume = 1.0

    def setVolume(self, value: float) -> None:
        self.volume = value


class FakeQtPlayer:
    def __init__(self) -> None:
        self.played = False

    def setAudioOutput(self, audio) -> None:
        pass

    def stop(self) -> None:
        pass

    def setSource(self, source) -> None:
        pass

    def play(self) -> None:
        self.played = True


class FakeQtEffect:
    instances: list["FakeQtEffect"] = []

    def __init__(self) -> None:
        self.play_count = 0
        self.volumes: list[float] = []
        FakeQtEffect.instances.append(self)

    def setSource(self, source) -> None:
        pass

    def setVolume(self, volume: float) -> None:
        self.volumes.append(volume)

    def setLoopCount(self, count: int) -> None:
        pass

    def stop(self) -> None:
        pass

    def play(self) -> None:
        self.play_count += 1


def _fake_qt_classes():
    return (None, FakeQtAudio, FakeQtAudio, FakeQtPlayer, FakeQtEffect)


def _install_winmm(monkeypatch, pool: click_sound.ClickSoundPool, api: FakeWinmmApi) -> sound_winmm.WinmmSoundPool:
    winmm = sound_winmm.WinmmSoundPool(api=api)
    monkeypatch.setattr(click_sound, "_new_winmm_pool", lambda: winmm)
    return winmm


def _forbid_qt(monkeypatch, pool: click_sound.ClickSoundPool) -> list[str]:
    """任何 QtMultimedia 触点被走到都记一笔（用于断言"全程没碰 Qt"）。"""
    touched: list[str] = []

    def forbid(name):
        def _boom(*_args, **_kwargs):
            touched.append(name)
            raise AssertionError(f"winmm 路径不应触碰 {name}")
        return _boom

    monkeypatch.setattr(pool, "qt_available", forbid("qt_available"))
    monkeypatch.setattr(pool, "qt_multimedia_classes", forbid("qt_multimedia_classes"))
    monkeypatch.setattr(pool, "effect_for", forbid("effect_for"))
    return touched


# ---------------------------------------------------------------------------
# 1. 不加载 QtMultimedia / ffmpeg（子进程取证）
# ---------------------------------------------------------------------------

_QT_FREE_SCRIPT = textwrap.dedent(
    '''
    import ctypes
    import sys
    from pathlib import Path

    sys.path.insert(0, r"{root}")
    from pet import click_sound, sound_winmm


    class FakeApi:
        def __init__(self):
            self.writes = []

        def device_count(self):
            return 1

        def open(self, channels, sample_rate, bits=16):
            return 1

        def close(self, handle):
            pass

        def write(self, handle, pcm):
            self.writes.append((handle, pcm))

        def pending_count(self, handle):
            return 0

        def reap(self, handle):
            return 0

        def reset(self, handle):
            pass

        def set_volume(self, handle, packed):
            pass


    api = FakeApi()
    click_sound._new_winmm_pool = lambda: sound_winmm.WinmmSoundPool(api=api)
    pool = click_sound.ClickSoundPool()
    assert pool.play_sound(Path(r"{wav}"), 0.7) is True, "winmm 未接管 wav 播放"
    assert len(api.writes) == 1, f"winmm 写入次数异常: {{len(api.writes)}}"

    assert "PySide6.QtMultimedia" not in sys.modules, "winmm 路径触发了 QtMultimedia import"
    k32 = ctypes.WinDLL("kernel32")
    k32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    k32.GetModuleHandleW.restype = ctypes.c_void_p
    for module in ("avcodec-61.dll", "avutil-59.dll", "avformat-61.dll", "mfcore.dll"):
        assert not k32.GetModuleHandleW(module), f"QtMultimedia/ffmpeg 被拖入: {{module}}"
    print("QT-FREE-OK")
    '''
)


@WINDOWS_ONLY
def test_winmm_wav_play_never_loads_qtmultimedia(tmp_path):
    wav = _pcm16_wav(tmp_path / "click.wav")
    script = _QT_FREE_SCRIPT.format(root=str(ROOT), wav=str(wav))
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, cwd=str(ROOT), env=env, timeout=120,
    )
    assert proc.returncode == 0, f"子进程取证失败:\n{proc.stdout}\n{proc.stderr}"
    assert "QT-FREE-OK" in proc.stdout


# ---------------------------------------------------------------------------
# 2. 池并发语义
# ---------------------------------------------------------------------------

def test_winmm_pool_grows_for_rapid_clicks_then_queues(tmp_path, monkeypatch):
    api = FakeWinmmApi()
    pool = click_sound.ClickSoundPool()
    winmm = _install_winmm(monkeypatch, pool, api)
    wav = _pcm16_wav(tmp_path / "click.wav")

    # 字面意义上的"快速连点"：上一声还在播（pending>0）就再点。
    for _ in range(4):
        assert pool.play_sound(wav, 0.7) is True

    assert [h for h, _c, _r in api.opened] == [1, 2, 3, 4], "快速连点没有各自占用独立句柄"
    assert [h for h, _pcm in api.writes] == [1, 2, 3, 4]
    assert all(len(pcm) == 3528 * 2 for _h, pcm in api.writes), "写入的不是原始 PCM16 帧"

    # 池满（4 路都在播）后继续连点：排队而不是丢音/重建。
    for _ in range(3):
        assert pool.play_sound(wav, 0.7) is True
    assert len(api.opened) == 4, f"池不应无界增长: {api.opened}"
    assert [h for h, _pcm in api.writes][4:] == [1, 2, 3]
    assert winmm.pending() == 7


def test_winmm_pool_reuses_finished_device_instead_of_growing(tmp_path, monkeypatch):
    api = FakeWinmmApi()
    pool = click_sound.ClickSoundPool()
    winmm = _install_winmm(monkeypatch, pool, api)
    wav = _pcm16_wav(tmp_path / "click.wav")

    for _ in range(4):
        pool.play_sound(wav, 0.7)
    assert len(api.opened) == 4

    for handle in (1, 2, 3, 4):
        api.finish(handle)
    assert pool.play_sound(wav, 0.7) is True
    assert len(api.opened) == 4, "有空闲句柄时不应再开新设备"
    assert api.writes[-1][0] == 1, "应优先复用已播完的句柄"
    assert api.reaped, "播放入口应先回收已完成 buffer（不泄漏 header）"
    assert winmm.pending() == 1


def test_winmm_pool_default_size_matches_qt_pool(tmp_path, monkeypatch):
    api = FakeWinmmApi()
    pool = sound_winmm.WinmmSoundPool(api=api)
    wav = _pcm16_wav(tmp_path / "click.wav")
    assert pool.play(wav, 0.7) is True
    for _ in range(6):
        pool.play(wav, 0.7)
    assert len(api.opened) == click_sound.ClickSoundPool._PLAYER_POOL_SIZE
    pool.clear()
    assert sorted(api.closed) == [1, 2, 3, 4]


def test_winmm_pool_clear_releases_headers_and_handles(tmp_path, monkeypatch):
    api = FakeWinmmApi()
    pool = sound_winmm.WinmmSoundPool(api=api)
    wav = _pcm16_wav(tmp_path / "click.wav")
    pool.play(wav, 0.7)
    pool.clear()
    assert api.closed == [1], "clear 必须关闭设备（设备内部先 reset 再回收 header）"
    assert pool.pending() == 0
    assert pool._formats == {}


def test_winmm_eviction_never_drops_the_freshly_opened_pool():
    """格式组超限时，刚建好、马上要写入的那个池绝不能被淘汰。

    回归：``play_clip`` 先登记新池再 ``_evict_formats``；其余格式组全有在途
    buffer 时新池是唯一"没有在途 buffer"的候选，被关掉后 ``_pick_device`` 又
    在它上面 ``api.open`` 重开一路——那个句柄脱离了 ``_formats`` 登记，
    reap/pending/clear 都看不到它，句柄 + WAVEHDR + PCM 缓冲活到进程结束。
    """
    api = FakeWinmmApi()
    pool = sound_winmm.WinmmSoundPool(api=api, size=1)

    def clip(channels: int, rate: int) -> sound_winmm.WavClip:
        return sound_winmm.WavClip(b"\x00\x10" * 8, channels, rate)

    # 前 4 种格式各写一次且都不收尾：4 个池全都 pending > 0。
    for channels, rate in ((1, 22050), (2, 22050), (1, 44100), (2, 44100)):
        assert pool.play_clip(clip(channels, rate)) is True

    # 第 5 种格式：正是"其余格式组全有在途 buffer"的触发点。
    assert pool.play_clip(clip(2, 48000)) is True

    fresh = (2, 48000)
    assert fresh in pool._formats, "刚建的池被淘汰了（随后会在失联池上重开句柄）"
    assert pool._formats[fresh].handles, "留在登记里的池不能是空的"

    opened = {h for h, _channels, _rate in api.opened}
    tracked = {h for fmt in pool._formats.values() for h in fmt.handles}
    assert not (tracked & set(api.closed)), "同一个句柄不能既在登记里又被关掉"
    assert tracked | set(api.closed) == opened, (
        f"存在既不在登记里也没被关闭的失联句柄：{opened - tracked - set(api.closed)}"
    )
    pool.clear()
    assert sorted(api.closed) == sorted(opened), "clear 之后每个开出来的句柄都必须被关掉"


# ---------------------------------------------------------------------------
# 3b. ctypes 边界本体（用假 DLL 验证调用顺序，不需要真声卡）
# ---------------------------------------------------------------------------

def _fake_winmm_dll():
    """waveOut* 的假 DLL：只记录调用顺序/参数，用于验证 Api 层契约。

    用 ``SimpleNamespace`` + 普通函数（不是绑定方法）：``WinmmApi._configure``
    会给这些属性赋 ``argtypes``/``restype``，绑定方法不允许。
    """
    dll = SimpleNamespace(calls=[], headers=[], payloads=[])

    def waveOutGetNumDevs():
        return 1

    def waveOutOpen(phandle, device_id, pwfx, callback, instance, flags):
        dll.calls.append("open")
        phandle._obj.value = 7
        return 0

    def waveOutPrepareHeader(handle, pheader, size):
        dll.calls.append("prepare")
        dll.headers.append(pheader._obj)
        return 0

    def waveOutWrite(handle, pheader, size):
        dll.calls.append("write")
        header = pheader._obj
        dll.payloads.append(ctypes.string_at(header.lpData, header.dwBufferLength))
        return 0

    def waveOutUnprepareHeader(handle, pheader, size):
        dll.calls.append("unprepare")
        return 0

    def waveOutReset(handle):
        dll.calls.append("reset")
        for header in dll.headers:  # 驱动在 reset 后把在途 buffer 标成 WHDR_DONE
            header.dwFlags |= sound_winmm.WHDR_DONE
        return 0

    def waveOutClose(handle):
        dll.calls.append("close")
        return 0

    def waveOutSetVolume(handle, packed):
        dll.calls.append(("volume", int(packed)))
        return 0

    dll.waveOutGetNumDevs = waveOutGetNumDevs
    dll.waveOutOpen = waveOutOpen
    dll.waveOutPrepareHeader = waveOutPrepareHeader
    dll.waveOutWrite = waveOutWrite
    dll.waveOutUnprepareHeader = waveOutUnprepareHeader
    dll.waveOutReset = waveOutReset
    dll.waveOutClose = waveOutClose
    dll.waveOutSetVolume = waveOutSetVolume
    return dll


def test_winmm_api_writes_pcm_and_reaps_done_headers():
    dll = _fake_winmm_dll()
    api = sound_winmm.WinmmApi(dll)
    handle = api.open(1, 22050)
    api.write(handle, b"\x01\x02\x03\x04")

    assert dll.calls[:3] == ["open", "prepare", "write"]
    assert dll.payloads == [b"\x01\x02\x03\x04"]
    assert api.pending_count(handle) == 1
    assert api.reap(handle) == 0, "没播完的 buffer 不许回收"

    dll.waveOutReset(handle)                # 模拟驱动置 WHDR_DONE
    assert api.reap(handle) == 1
    assert "unprepare" in dll.calls
    assert api.pending_count(handle) == 0


def test_winmm_api_close_resets_before_unprepare_and_close():
    dll = _fake_winmm_dll()
    api = sound_winmm.WinmmApi(dll)
    handle = api.open(1, 22050)
    api.write(handle, b"\x00\x00" * 4)
    api.set_volume(handle, 0x80008000)
    api.close(handle)

    assert dll.calls.index("reset") < dll.calls.index("close"), "关闭前必须 reset 清队列"
    assert "unprepare" in dll.calls, "在途 header 必须 unprepare，不能泄漏"
    assert ("volume", 0x80008000) in dll.calls
    assert api.pending_count(handle) == 0


# ---------------------------------------------------------------------------
# 3. 音量映射（0 / 0.5 / 1.0）与 set_audio_volume 对齐
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("volume", "packed"),
    [(0.0, 0x00000000), (0.5, 0x80008000), (1.0, 0xFFFFFFFF)],
)
def test_winmm_volume_packed_mapping(volume, packed):
    assert sound_winmm.pack_volume(volume) == packed


def test_winmm_volume_clamped_to_unit_range():
    assert sound_winmm.pack_volume(-3.0) == 0x00000000
    assert sound_winmm.pack_volume(2.5) == 0xFFFFFFFF
    assert sound_winmm.pack_volume("bad") == 0xFFFFFFFF
    assert sound_winmm.pack_volume(None) == 0xFFFFFFFF


def test_winmm_set_volume_applies_to_open_and_later_devices(tmp_path, monkeypatch):
    api = FakeWinmmApi()
    pool = click_sound.ClickSoundPool()
    _install_winmm(monkeypatch, pool, api)
    wav = _pcm16_wav(tmp_path / "click.wav")

    assert pool.set_audio_volume(0.5) == 0.5
    assert pool._qt_player is None, "winmm 可用时 set_audio_volume 不得创建 QMediaPlayer"
    assert api.volumes == [], "此时还没有打开任何设备"

    assert pool.play_sound(wav, 0.5) is True
    assert api.volumes == [(1, 0x80008000)], "新开的设备必须继承已设置的全局音量"

    assert pool.set_audio_volume(1.0) == 1.0
    assert api.volumes[-1] == (1, 0xFFFFFFFF)
    assert pool.set_audio_volume(9.0) == 1.0, "越界音量必须 clamp 到 1.0"


def test_winmm_per_play_volume_scales_pcm(tmp_path, monkeypatch):
    api = FakeWinmmApi()
    pool = click_sound.ClickSoundPool()
    _install_winmm(monkeypatch, pool, api)
    wav = _pcm16_wav(tmp_path / "click.wav")
    with wave.open(str(wav), "rb") as source:
        raw = source.readframes(source.getnframes())

    assert pool.play_sound(wav, 0.0) is True
    assert api.writes[0][1] == b"\x00" * len(raw), "音量 0.0 必须写出等长静音帧"

    assert pool.play_sound(wav, 1.0) is True
    assert api.writes[1][1] == raw, "音量 1.0 不得改动 PCM"


def test_scale_pcm16_halves_and_clips():
    frames = (1000).to_bytes(2, "little", signed=True) + (-1000).to_bytes(2, "little", signed=True)
    scaled = sound_winmm.scale_pcm16(frames, 0.5)
    assert int.from_bytes(scaled[:2], "little", signed=True) == 500
    assert int.from_bytes(scaled[2:], "little", signed=True) == -500
    assert sound_winmm.scale_pcm16(frames, 1.0) == frames
    assert sound_winmm.scale_pcm16(frames, 0.0) == b"\x00\x00\x00\x00"


# ---------------------------------------------------------------------------
# 4. 回退：不可用 / 打开失败 / 写入失败
# ---------------------------------------------------------------------------

def _qt_fallback_fixture(monkeypatch, pool, tmp_path):
    """把池的 Qt 侧换成可观测替身（wav 走 QSoundEffect）。"""
    monkeypatch.setattr(click_sound, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(pool, "_qt_effects", {})
    monkeypatch.setattr(pool, "_qt_player_pool", [])
    monkeypatch.setattr(pool, "_qt_player_index", 0)
    monkeypatch.setattr(pool, "_qt_player", None)
    monkeypatch.setattr(pool, "_qt_audio", None)
    monkeypatch.setattr(pool, "qt_available", lambda: True)
    monkeypatch.setattr(pool, "qt_multimedia_classes", _fake_qt_classes)
    FakeQtEffect.instances.clear()
    return FakeQtEffect


def test_winmm_unavailable_falls_back_to_qt_wav(tmp_path, monkeypatch):
    pool = click_sound.ClickSoundPool()
    monkeypatch.setattr(click_sound, "_new_winmm_pool", lambda: None)
    effect_cls = _qt_fallback_fixture(monkeypatch, pool, tmp_path)
    effect_cls.instances.clear()
    wav = _pcm16_wav(tmp_path / "click.wav")

    assert pool.play_sound(wav, 0.5) is True
    assert effect_cls.instances and effect_cls.instances[-1].play_count == 1
    assert pool.winmm_pool() is None


def test_winmm_open_failure_falls_back_to_qt_wav(tmp_path, monkeypatch):
    api = FakeWinmmApi(fail_open=True)
    pool = click_sound.ClickSoundPool()
    _install_winmm(monkeypatch, pool, api)
    effect_cls = _qt_fallback_fixture(monkeypatch, pool, tmp_path)
    effect_cls.instances.clear()
    wav = _pcm16_wav(tmp_path / "click.wav")

    assert pool.play_sound(wav, 0.5) is True
    assert effect_cls.instances and effect_cls.instances[-1].play_count == 1
    assert api.writes == []


def test_winmm_write_failure_falls_back_to_qt_wav(tmp_path, monkeypatch):
    api = FakeWinmmApi(fail_write=True)
    pool = click_sound.ClickSoundPool()
    _install_winmm(monkeypatch, pool, api)
    effect_cls = _qt_fallback_fixture(monkeypatch, pool, tmp_path)
    effect_cls.instances.clear()
    wav = _pcm16_wav(tmp_path / "click.wav")

    assert pool.play_sound(wav, 0.5) is True
    assert effect_cls.instances and effect_cls.instances[-1].play_count == 1


def test_winmm_missing_file_is_not_played(tmp_path, monkeypatch):
    api = FakeWinmmApi()
    pool = click_sound.ClickSoundPool()
    _install_winmm(monkeypatch, pool, api)
    assert pool.play_sound(tmp_path / "nope.wav", 0.5) is False
    assert api.writes == []


# ---------------------------------------------------------------------------
# 5. 压缩音频的转码缓存产物走 winmm
# ---------------------------------------------------------------------------

def test_decoded_cache_wav_plays_through_winmm_without_qt(tmp_path, monkeypatch):
    api = FakeWinmmApi()
    pool = click_sound.ClickSoundPool()
    _install_winmm(monkeypatch, pool, api)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(click_sound, "_sound_cache_dir", lambda: cache_dir)
    source = tmp_path / "Ya1.mp3"
    source.write_bytes(b"not-a-real-mp3")
    cache = click_sound._cache_path(source)
    _pcm16_wav(cache, channels=2, rate=48000, samples=4800)

    touched = _forbid_qt(monkeypatch, pool)
    assert pool.play_sound(source, 0.7) is True
    assert touched == []
    assert [h for h, _c, _r in api.opened] == [1]
    assert api.opened[0][1:] == (2, 48000), "转码产物（48k 立体声）必须按自身格式开设备"
    assert len(api.writes) == 1


def test_uncached_compressed_audio_keeps_qt_path(tmp_path, monkeypatch):
    """缓存未命中时仍走既有 Qt 解码/播放器池路径（行为与改动前一致）。"""
    api = FakeWinmmApi()
    pool = click_sound.ClickSoundPool()
    _install_winmm(monkeypatch, pool, api)
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(click_sound, "_sound_cache_dir", lambda: cache_dir)
    from tests.test_click_sound import FakeQtDecoder

    monkeypatch.setattr(
        pool, "qt_multimedia_classes",
        lambda: (FakeQtDecoder, FakeQtAudio, FakeQtAudio, FakeQtPlayer, FakeQtEffect))
    monkeypatch.setattr(pool, "_qt_effects", {})
    monkeypatch.setattr(pool, "_qt_player_pool", [])
    monkeypatch.setattr(pool, "_qt_player_index", 0)
    monkeypatch.setattr(click_sound, "os", SimpleNamespace(name="nt"))
    source = tmp_path / "Ya1.mp3"
    source.write_bytes(b"not-a-real-mp3")

    assert pool.play_sound(source, 0.7) is True
    assert api.writes == [], "无缓存时 winmm 不应拿到任何数据"
    assert pool._qt_player_pool and pool._qt_player_pool[0][0].played is True


def test_press_sound_with_winmm_does_not_create_qsound_effect(tmp_path, monkeypatch):
    """小黄鸭 press/release：winmm 可用时连 release 的 QSoundEffect 都不许建。

    play_press_sound 原来无条件 effect_for(release)（会 import QtMultimedia），
    这会抵消 winmm 直放的全部收益。
    """
    api = FakeWinmmApi()
    pool = click_sound.ClickSoundPool()
    _install_winmm(monkeypatch, pool, api)
    touched = _forbid_qt(monkeypatch, pool)
    press = _pcm16_wav(tmp_path / "Ya1.wav", samples=2000)
    release = _pcm16_wav(tmp_path / "Ya2.wav", samples=2000)

    assert pool.play_press_sound((press, release), 0.6) is True
    assert touched == []
    assert len(api.writes) == 1, "press 音必须真的交给 winmm"
    assert pool._click_pair_state[(str(press), str(release))]["press_played"] is True


# ---------------------------------------------------------------------------
# 6. 非 PCM16 wav：先转标准 wav 缓存再走 winmm
# ---------------------------------------------------------------------------

def test_non_pcm16_wav_converted_to_cache_then_played_by_winmm(tmp_path, monkeypatch):
    api = FakeWinmmApi()
    pool = click_sound.ClickSoundPool()
    _install_winmm(monkeypatch, pool, api)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(click_sound, "_sound_cache_dir", lambda: cache_dir)
    wav = _pcm8_wav(tmp_path / "legacy.wav")
    touched = _forbid_qt(monkeypatch, pool)

    assert pool.play_sound(wav, 1.0) is True
    assert touched == []
    cache = click_sound._cache_path(wav)
    assert cache.is_file(), "非 PCM16 wav 必须先落成标准 wav 缓存"
    with wave.open(str(cache), "rb") as converted:
        assert converted.getsampwidth() == 2
        assert converted.getnchannels() == 1
        assert converted.getframerate() == 22050
    assert len(api.writes) == 1
    assert len(api.writes[0][1]) == 400 * 2, "8bit → 16bit 后帧数不变、每帧两字节"


def test_read_pcm16_rejects_unknown_format(tmp_path):
    bogus = tmp_path / "bogus.wav"
    bogus.write_bytes(b"RIFFnotreallyawave")
    assert sound_winmm.read_pcm16(bogus) is None


def test_unreadable_wav_uses_existing_transcode_cache(tmp_path, monkeypatch):
    """浮点/压缩 wav（`wave` 打不开）若已有转码缓存，也要走 winmm 而不是回退 Qt。"""
    api = FakeWinmmApi()
    pool = click_sound.ClickSoundPool()
    _install_winmm(monkeypatch, pool, api)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(click_sound, "_sound_cache_dir", lambda: cache_dir)
    wav = tmp_path / "float.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 64)
    assert sound_winmm.read_pcm16(wav) is None, "该素材必须落在'标准库读不了'这一类"
    _pcm16_wav(click_sound._cache_path(wav), channels=2, rate=48000, samples=100)
    touched = _forbid_qt(monkeypatch, pool)

    assert pool.play_sound(wav, 0.7) is True
    assert touched == []
    assert len(api.writes) == 1


# ---------------------------------------------------------------------------
# 7. 预热：winmm 可用时只预热 winmm 池
# ---------------------------------------------------------------------------

def test_warm_click_sound_effects_prefers_winmm_pool(tmp_path, monkeypatch):
    api = FakeWinmmApi()
    pool = click_sound.ClickSoundPool()
    _install_winmm(monkeypatch, pool, api)
    monkeypatch.setattr(pool, "_qt_effects", {})
    monkeypatch.setattr(pool, "_qt_player_pool", [])
    touched = _forbid_qt(monkeypatch, pool)

    wav = _pcm16_wav(tmp_path / "click.wav")
    pack = {"kind": "file", "id": "custom", "path": str(wav)}
    pool.warm_click_sound_effects(pack, data_dir=tmp_path)

    assert touched == [], "预热不得触碰任何 QtMultimedia 入口"
    assert pool._qt_effects == {} and pool._qt_player_pool == []
    assert [h for h, _c, _r in api.opened] == [1, 2, 3, 4], "预热必须把句柄一次开满（waveOutOpen 实测 ~70ms）"
    assert api.writes == [], "预热只开设备，不发声"

    # 预热过的格式，连点不再付开设备的钱：4 连点仍是这 4 个句柄
    for _ in range(4):
        assert pool.play_sound(wav, 0.7) is True
    assert [h for h, _c, _r in api.opened] == [1, 2, 3, 4]
    assert [h for h, _pcm in api.writes] == [1, 2, 3, 4]


def test_warm_does_not_open_device_for_missing_assets(tmp_path, monkeypatch):
    api = FakeWinmmApi()
    pool = click_sound.ClickSoundPool()
    _install_winmm(monkeypatch, pool, api)
    pool.warm_click_sound_effects({"kind": "file", "id": "custom", "path": str(tmp_path / "none.wav")}, data_dir=tmp_path)
    assert api.opened == []


def test_winmm_pool_gives_up_growing_after_open_failure(tmp_path, monkeypatch):
    """开设备失败（驱动句柄耗尽）后不许每次点击都重试——waveOutOpen ~70ms。"""
    api = FakeWinmmApi()
    pool = sound_winmm.WinmmSoundPool(api=api, size=4)
    wav = _pcm16_wav(tmp_path / "click.wav")
    assert pool.play(wav, 0.7) is True
    api.fail_open = True
    for _ in range(3):
        assert pool.play(wav, 0.7) is True    # 全忙 → 试试增长 → 失败 → 排队
    assert len(api.opened) == 1
    attempts = api.open_calls
    assert pool.play(wav, 0.7) is True
    assert api.open_calls == attempts, "失败后不得反复重试开设备"


def test_winmm_reaper_timer_only_created_on_gui_thread(tmp_path):
    """Agent 音效可能从 worker 线程调 play_sound：那里不许建 QTimer。"""
    import threading

    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    api = FakeWinmmApi()
    pool = sound_winmm.WinmmSoundPool(api=api)
    wav = _pcm16_wav(tmp_path / "click.wav")
    results: list[bool] = []

    worker = threading.Thread(target=lambda: results.append(pool.play(wav, 0.7)))
    worker.start()
    worker.join(10)

    assert results == [True]
    assert pool._timer is None, "非 GUI 线程不得创建 QTimer（会永不触发并刷告警）"
    pool.clear()


# ---------------------------------------------------------------------------
# 8. 实机冒烟（有声卡的真机，默认跳过）
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("DSH_WINMM_SMOKE") != "1",
    reason="实机发声冒烟：需 Windows 真声卡且显式设 DSH_WINMM_SMOKE=1",
)
def test_real_device_smoke_click_wav_and_decoded_duck_mp3(capsys, monkeypatch):
    """真机发声：click.wav 与 duck（mp3→转码 wav）各放一次，打印耗时。

    为什么 gated：这条用例会真的让声卡出声，测试套件默认静音（conftest 把
    winmm 默认为不可用），只有主人显式 ``DSH_WINMM_SMOKE=1`` 时才跑。
    """
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])
    monkeypatch.setattr(sound_winmm, "default_api", sound_winmm.WinmmApi.load)
    pool = sound_winmm.WinmmSoundPool()
    assert pool.available(), "真机冒烟要求 winmm 可用"

    click = ROOT / "assets" / "sounds" / "click.wav"
    t0 = time.perf_counter()
    pool.warm([click])
    t_warm = time.perf_counter() - t0

    timings = []
    for _ in range(4):                      # 快速连点：4 路句柄各占一条流
        t0 = time.perf_counter()
        timings.append((pool.play(click, 0.7), (time.perf_counter() - t0) * 1000))
    assert all(ok for ok, _ms in timings), f"click.wav 未播成: {timings}"
    assert len({handle for pool_ in pool._formats.values() for handle in pool_.handles}) >= 4

    duck = ROOT / "assets" / "sounds" / "duck" / "Ya1.mp3"
    cache = click_sound._cache_path(duck)
    if not cache.is_file():
        qt_pool = click_sound.ClickSoundPool()
        qt_pool.decode_to_wav(duck, cache, 0.0)
        deadline = time.monotonic() + 10.0
        while not cache.is_file() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        qt_pool.clear()
    assert cache.is_file(), f"duck mp3 未转码出缓存: {cache}"

    pool.warm([cache])                      # 转码产物（48k 立体声）另开一组句柄
    t0 = time.perf_counter()
    ok_duck = pool.play(cache, 0.7)
    t_duck = (time.perf_counter() - t0) * 1000

    deadline = time.monotonic() + 5.0       # QTimer 兜底回收需要事件循环
    while pool.pending() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    with capsys.disabled():
        print(f"\n[SMOKE] 预热 4 句柄 {t_warm * 1000:.0f}ms")
        for index, (ok, ms) in enumerate(timings, 1):
            print(f"[SMOKE] click.wav #{index} ok={ok} 提交 {ms:.2f}ms")
        print(f"[SMOKE] duck 转码缓存 ok={ok_duck} 提交 {t_duck:.2f}ms pending={pool.pending()}")
    assert ok_duck
    assert pool.pending() == 0, "播放结束后 header 必须全部回收，不能泄漏"
    pool.clear()
