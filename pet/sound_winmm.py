# -*- coding: utf-8 -*-
"""winmm(waveOut) 直放后端：wav 音效绕开 QtMultimedia 的最小播放路径。

动机
----
QtMultimedia 只要被 QSoundEffect 触碰一次，ffmpeg 后端就常驻进程：本机实测
最小进程放一个 wav 后工作集 +10MB，已加载 avcodec-61/avutil-59/avformat-61/
MFCORE，正式程序里常驻约 40MB。点击音效绝大多数是 PCM16 wav，winmm 完全够用。

分层（ctypes 边界可整体替身，便于单测）
--------------------------------------
- ``WinmmApi``：唯一触碰 winmm.dll 的一层（WAVEFORMATEX/WAVEHDR 结构、argtypes、
  句柄与 header 生命周期都在这里）。测试用同协议的替身整体替换；
- ``WinmmSoundPool``：按 (声道, 采样率) 分组的小池子（默认 4 路，与
  ``ClickSoundPool._PLAYER_POOL_SIZE`` 同规模）。快速连点时优先占用空闲句柄、
  不够则增长、全忙则排队——``waveOutWrite`` 本身是队列语义，先入先播，不丢音；
- 模块级纯函数：``read_pcm16`` / ``write_pcm16_wav`` / ``scale_pcm16`` /
  ``pack_volume``，不触碰任何原生状态，任何平台都能测。

非阻塞
------
``waveOutWrite`` 立即返回（驱动侧异步播放），本模块所有公开入口都不等待：
PCM 缓冲与 WAVEHDR 由池持有，播完靠 ``reap()`` 轮询 WHDR_DONE 位回收并
``waveOutUnprepareHeader``，交付给 ``waveOutClose`` 前一定先 reset。GUI 线程
可选地起一个 250ms 的 QTimer 兜底回收（没有 QCoreApplication 时退化为
"每次播放入口顺手回收"，同样不泄漏 header）。

音量
----
- 每次播放的音量（与 ``QSoundEffect.setVolume`` 对齐）用**软件缩放** PCM16：
  ``waveOutSetVolume`` 是设备级的，拿它做每声独立音量会误伤同设备的其它流；
- ``pack_volume`` 把 0.0-1.0 映射成双声道 packed DWORD，配合 ``set_volume``
  （设备级，与 ``click_sound.set_audio_volume`` 对齐）。
"""
from __future__ import annotations

import array
import ctypes
from ctypes import wintypes
import logging
import os
import sys
import time
import wave
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger("pet.sound_winmm")

# —— winmm 常量 ——
WAVE_MAPPER = 0xFFFFFFFF


# ---------------------------------------------------------------------------
# 默认渲染端点探测（issue #315：句柄池跟随默认设备切换）
# ---------------------------------------------------------------------------
# winmm 的 WAVE_MAPPER 只在 waveOutOpen 那一刻解析「当前默认设备」——用户切了
# 默认输出（拔耳机/换音箱），已打开的句柄仍往旧设备播。这里用 MMDevice API
# （纯 ctypes COM，不引第三方）读当前默认端点 ID；读不到一律返回 None
# （调用方按「不知道」处理，绝不影响播放）。

class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


def _make_guid(d1: int, d2: int, d3: int, d4: tuple) -> _GUID:
    return _GUID(d1, d2, d3, (ctypes.c_ubyte * 8)(*d4))


_CLSID_MMDEVENUM = _make_guid(0xBCDE0395, 0xE52F, 0x467C,
                              (0x8E, 0x3D, 0xC4, 0x57, 0x92, 0x91, 0x69, 0x2E))
_IID_IMMDEVENUM = _make_guid(0xA95664D2, 0x9614, 0x4F35,
                             (0xA7, 0x46, 0xDE, 0x8D, 0xB6, 0x36, 0x17, 0xE6))


def _com_call(ppv: int, index: int, restype, argtypes, *args):
    """按 vtbl 序号调 COM 方法（手写最小的 QueryInterface/AddRef/Release 族）。"""
    vtbl = ctypes.cast(ctypes.cast(ppv, ctypes.POINTER(ctypes.c_void_p))[0],
                       ctypes.POINTER(ctypes.c_void_p))
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(vtbl[index])(ppv, *args)


def _default_render_device_id() -> "str | None":
    """当前默认渲染端点 ID；非 Windows/查询失败返回 None（替换点供测试使用）。"""
    if sys.platform != "win32":
        return None
    try:
        ole32 = ctypes.windll.ole32
    except AttributeError:
        return None
    init_hr = -1  # CoInitializeEx 未跑完（异常）时按"未初始化"处理，不配平
    try:
        init_hr = ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED；重复调用安全
        enum = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(ctypes.byref(_CLSID_MMDEVENUM), None, 0x17,
                                    ctypes.byref(_IID_IMMDEVENUM), ctypes.byref(enum))
        if hr != 0 or not enum:
            return None
        try:
            dev = ctypes.c_void_p()
            hr = _com_call(enum, 4, ctypes.c_long,
                           [wintypes.DWORD, wintypes.DWORD,
                            ctypes.POINTER(ctypes.c_void_p)], 0, 0, ctypes.byref(dev))
            if hr != 0 or not dev:
                return None
            try:
                wid = ctypes.c_void_p()
                hr = _com_call(dev, 5, ctypes.c_long,
                               [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(wid))
                if hr != 0 or not wid:
                    return None
                try:
                    return ctypes.wstring_at(wid.value)
                finally:
                    ole32.CoTaskMemFree(wid)
            finally:
                _com_call(dev, 2, ctypes.c_ulong, [])
        finally:
            _com_call(enum, 2, ctypes.c_ulong, [])
    except Exception:            # noqa: BLE001 - 探测失败按"不知道"处理
        return None
    finally:
        # 仅本函数真正初始化成功（S_OK/S_FALSE，会给线程 COM 计数 +1）时才配平；
        # RPC_E_CHANGED_MODE（GUI 线程已是 STA）与其它失败值不增加计数，
        # 此时 CoUninitialize 会错扣别人的计数（播放热路径每 2s 一次）。
        if init_hr in (0, 1):  # S_OK / S_FALSE
            try:
                ole32.CoUninitialize()
            except Exception:        # noqa: BLE001
                pass


#: 默认设备对账的最小间隔（play 热路径不每次做 COM 往返）
_DEVICE_CHECK_INTERVAL_S = 2.0
WAVE_FORMAT_PCM = 0x0001
MMSYSERR_NOERROR = 0
WHDR_DONE = 0x00000001
CALLBACK_NULL = 0x00000000

DEFAULT_POOL_SIZE = 4          # 与 ClickSoundPool._PLAYER_POOL_SIZE 同规模
MAX_FORMATS = 4                # 同时保持的 (声道, 采样率) 组数上限
REAP_INTERVAL_MS = 250         # 兜底回收节拍

#: 解析缓存（``read_pcm16``）：键 = (绝对化路径, mtime_ns, size)
_PARSE_CACHE_LIMIT = 8
#: 单条解析缓存的最大 PCM 体积（超过就不钉在内存里：点击/碰撞音都远小于此）
_PARSE_CACHE_MAX_CLIP_BYTES = 512 * 1024
_parse_cache: dict[tuple[str, int, int], WavClip] = {}

#: 缩放缓存（``WinmmSoundPool.play_clip``）：键 = (clip, 音量)
_SCALED_CACHE_LIMIT = 8
#: 单条缩放缓存的最大 PCM 体积（超过就每次现算，不钉内存）
_SCALED_CACHE_MAX_CLIP_BYTES = 512 * 1024


class WinmmError(RuntimeError):
    """winmm 调用失败（返回码非 MMSYSERR_NOERROR）。"""


# 存活池登记（弱引用）：只服务测试收口（关设备/停 timer），生产路径不读。
_LIVE_POOLS: "weakref.WeakSet[WinmmSoundPool]" = weakref.WeakSet()


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", ctypes.c_ushort),
        ("nChannels", ctypes.c_ushort),
        ("nSamplesPerSec", ctypes.c_uint32),
        ("nAvgBytesPerSec", ctypes.c_uint32),
        ("nBlockAlign", ctypes.c_ushort),
        ("wBitsPerSample", ctypes.c_ushort),
        ("cbSize", ctypes.c_ushort),
    ]


class WAVEHDR(ctypes.Structure):
    _fields_ = [
        ("lpData", ctypes.c_void_p),
        ("dwBufferLength", ctypes.c_uint32),
        ("dwBytesRecorded", ctypes.c_uint32),
        ("dwUser", ctypes.c_size_t),
        ("dwFlags", ctypes.c_uint32),
        ("dwLoops", ctypes.c_uint32),
        ("lpNext", ctypes.c_void_p),
        ("reserved", ctypes.c_size_t),
    ]


class _Voice:
    """一个在途 buffer：header 与底层内存必须同生共死（不能只留 header）。"""

    __slots__ = ("header", "buffer")

    def __init__(self, header: WAVEHDR, buffer: ctypes.Array) -> None:
        self.header = header
        self.buffer = buffer


# ---------------------------------------------------------------------------
# 纯函数：wav 解析 / 归一化 / 音量
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WavClip:
    """一段已经归一化为 PCM16 的音频数据（非 PCM16 源见 ``converted``）。"""

    data: bytes
    channels: int
    sample_rate: int
    converted: bool = False     # True = 源不是 PCM16，data 由本模块转换而来


def clamp_volume(volume: Any) -> float:
    """把任意输入收敛到 0.0..1.0（非法输入按 1.0，与 set_audio_volume 一致）。"""
    try:
        value = float(volume)
    except (TypeError, ValueError):
        return 1.0
    if value != value:  # NaN
        return 1.0
    return max(0.0, min(1.0, value))


def pack_volume(volume: Any) -> int:
    """0.0-1.0 → 双声道 packed DWORD（waveOutSetVolume 的入参形态）。"""
    word = int(round(clamp_volume(volume) * 0xFFFF))
    return (word << 16) | word


def scale_pcm16(pcm: bytes, volume: Any) -> bytes:
    """按 16.16 定点增益缩放 PCM16（音量 1.0 原样返回，避免无谓舍入）。"""
    value = clamp_volume(volume)
    if value >= 1.0:
        return bytes(pcm)
    usable = len(pcm) - (len(pcm) % 2)
    if usable <= 0:
        return b""
    if value <= 0.0:
        return b"\x00" * usable
    samples = array.array("h")
    samples.frombytes(pcm[:usable])
    if sys.byteorder == "big":
        samples.byteswap()
    gain = int(round(value * 0x10000))
    for index in range(len(samples)):
        samples[index] = (samples[index] * gain) >> 16
    if sys.byteorder == "big":
        samples.byteswap()
    return samples.tobytes()


def _samples_from_bytes(raw: bytes) -> bytes:
    """array('h') 字节序归一化（Windows 是小端，但纯函数要保持平台无关）。"""
    samples = array.array("h")
    samples.frombytes(raw)
    if sys.byteorder == "big":
        samples.byteswap()
    return samples.tobytes()


def _to_pcm16(frames: bytes, width: int) -> bytes | None:
    """把整型 PCM（8/24/32 位，小端）收敛成 16 位；其它宽度交给 Qt 路线。"""
    if width == 1:
        # 8bit PCM 是无符号，先归零再左移 8 位到 int16 量级
        return _samples_from_bytes(array.array("h", ((byte - 128) << 8 for byte in frames)).tobytes())
    if width == 3:
        usable = len(frames) - (len(frames) % 3)
        narrowed = bytearray(usable // 3 * 2)
        narrowed[0::2] = frames[1:usable:3]   # 取高位两字节（小端）
        narrowed[1::2] = frames[2:usable:3]
        return _samples_from_bytes(bytes(narrowed))
    if width == 4:
        usable = len(frames) - (len(frames) % 4)
        narrowed = bytearray(usable // 2)
        narrowed[0::2] = frames[2:usable:4]
        narrowed[1::2] = frames[3:usable:4]
        return _samples_from_bytes(bytes(narrowed))
    return None


def read_pcm16(path: str | Path) -> WavClip | None:
    """读 wav 并归一化为 PCM16；不可读/压缩/浮点等读不了的一律返回 None。

    返回 ``WavClip.converted=True`` 表示需要先落成标准 wav 缓存（调用方决定）。

    解析结果按 (绝对化路径, mtime_ns, size) 缓存：密集碰撞（实机 5 次/秒）每次都
    重读重解析同一个音源，实测 45.7KB 的碰撞音读盘+解析 0.11~0.22ms/次，是
    GUI 线程的纯税（``.scratch/windows-parity-20260926-a/fix-20260928-C1/
    bench-before.json``）。读不出来的结果不进缓存：转码/写入中途的失败必须下次
    还能重试。
    """
    key = _parse_cache_key(path)
    if key is not None:
        cached = _parse_cache.get(key)
        if cached is not None:
            return cached
    clip = _parse_wav(path)
    if key is not None and clip is not None and len(clip.data) <= _PARSE_CACHE_MAX_CLIP_BYTES:
        if len(_parse_cache) >= _PARSE_CACHE_LIMIT:
            _parse_cache.clear()
        _parse_cache[key] = clip
    return clip


def _parse_wav(path: str | Path) -> WavClip | None:
    """真正读盘并归一化（``read_pcm16`` 的未缓存路径）。"""
    try:
        with wave.open(str(path), "rb") as source:
            channels = int(source.getnchannels())
            width = int(source.getsampwidth())
            rate = int(source.getframerate())
            comp_type = source.getcomptype()
            frames = source.readframes(source.getnframes())
    except (OSError, EOFError, wave.Error, ValueError):
        return None
    if comp_type != "NONE":
        return None
    # 1/2 声道才能直接喂 WAVE_FORMAT_PCM；多声道需要 EXTENSIBLE，交给 Qt 兜底
    if channels < 1 or channels > 2 or rate <= 0:
        return None
    if width == 2:
        return WavClip(frames, channels, rate)
    data = _to_pcm16(frames, width)
    if data is None:
        return None
    return WavClip(data, channels, rate, converted=True)


def _parse_cache_key(path: str | Path) -> tuple[str, int, int] | None:
    """解析缓存键；文件 stat 不到（不存在/非法路径）时返回 None = 不进缓存。"""
    try:
        stat = os.stat(path)
        return (os.path.abspath(os.fspath(path)), int(stat.st_mtime_ns), int(stat.st_size))
    except (OSError, TypeError, ValueError):
        return None


def write_pcm16_wav(clip: WavClip, dest: str | Path) -> bool:
    """把已归一化的 PCM16 写成标准 wav（转码缓存产物）。"""
    try:
        target = Path(dest)
        target.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(target), "wb") as out:
            out.setnchannels(clip.channels)
            out.setsampwidth(2)
            out.setframerate(clip.sample_rate)
            out.writeframes(clip.data)
        return True
    except (OSError, wave.Error, ValueError):
        log.warning("写入标准 wav 缓存失败: %s", dest, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# ctypes 边界：唯一的 winmm.dll 收敛点
# ---------------------------------------------------------------------------

class WinmmApi:
    """winmm.dll 的薄封装：句柄与 WAVEHDR 的生命周期都在这一层。

    测试替身只需实现同名方法（open/close/write/pending_count/
    reap/reset/set_volume），整条产品路径即可在无音频设备的环境下验证。
    """

    def __init__(self, dll: Any) -> None:
        self._dll = dll
        self._voices: dict[int, list[_Voice]] = {}
        self._configure()

    @classmethod
    def load(cls) -> "WinmmApi | None":
        """加载 winmm；非 Windows / 加载失败返回 None（调用方回退 Qt 路径）。"""
        if os.name != "nt":
            return None
        try:
            dll = ctypes.WinDLL("winmm")
        except (OSError, AttributeError):
            return None
        try:
            return cls(dll)
        except Exception:
            log.warning("winmm 接口初始化失败，wav 回退 Qt 路径", exc_info=True)
            return None

    def _configure(self) -> None:
        dll = self._dll
        dll.waveOutGetNumDevs.restype = ctypes.c_uint
        dll.waveOutOpen.argtypes = [
            ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint, ctypes.POINTER(WAVEFORMATEX),
            ctypes.c_size_t, ctypes.c_size_t, ctypes.c_uint,
        ]
        dll.waveOutOpen.restype = ctypes.c_uint
        for name in ("waveOutPrepareHeader", "waveOutWrite", "waveOutUnprepareHeader"):
            func = getattr(dll, name)
            func.argtypes = [ctypes.c_void_p, ctypes.POINTER(WAVEHDR), ctypes.c_uint]
            func.restype = ctypes.c_uint
        dll.waveOutReset.argtypes = [ctypes.c_void_p]
        dll.waveOutReset.restype = ctypes.c_uint
        dll.waveOutClose.argtypes = [ctypes.c_void_p]
        dll.waveOutClose.restype = ctypes.c_uint
        dll.waveOutSetVolume.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        dll.waveOutSetVolume.restype = ctypes.c_uint

    @staticmethod
    def _check(code: Any, what: str) -> None:
        value = int(code)
        if value != MMSYSERR_NOERROR:
            raise WinmmError(f"{what} 失败: MMSYSERR={value}")

    @staticmethod
    def _handle_ref(handle: int) -> ctypes.c_void_p:
        return ctypes.c_void_p(handle)

    def open(self, channels: int, sample_rate: int, bits: int = 16) -> int:
        fmt = WAVEFORMATEX()
        fmt.wFormatTag = WAVE_FORMAT_PCM
        fmt.nChannels = int(channels)
        fmt.nSamplesPerSec = int(sample_rate)
        fmt.wBitsPerSample = int(bits)
        fmt.nBlockAlign = fmt.nChannels * fmt.wBitsPerSample // 8
        fmt.nAvgBytesPerSec = fmt.nSamplesPerSec * fmt.nBlockAlign
        fmt.cbSize = 0
        handle = ctypes.c_void_p()
        self._check(
            self._dll.waveOutOpen(
                ctypes.byref(handle), WAVE_MAPPER, ctypes.byref(fmt),
                0, 0, CALLBACK_NULL),
            "waveOutOpen",
        )
        if not handle.value:
            raise WinmmError("waveOutOpen 返回空句柄")
        self._voices[int(handle.value)] = []
        return int(handle.value)

    def close(self, handle: int) -> None:
        ref = self._handle_ref(handle)
        # 队列里还有 buffer 时 waveOutClose 会直接失败：先 reset 让它们全部完成
        self._dll.waveOutReset(ref)
        self.reap(handle)
        for voice in self._voices.pop(handle, []):
            self._dll.waveOutUnprepareHeader(ref, ctypes.byref(voice.header), ctypes.sizeof(WAVEHDR))
        self._dll.waveOutClose(ref)

    def write(self, handle: int, pcm: bytes) -> None:
        payload = bytes(pcm)
        if not payload:
            raise WinmmError("拒绝写入空 buffer")
        # create_string_buffer(init, size) 精确分配 size 字节并拷贝内容；该对象
        # 由 _Voice 持有，播放期间地址必须保持有效（ctypes 不会替我们钉住内存）。
        buffer = ctypes.create_string_buffer(payload, len(payload))
        header = WAVEHDR()
        header.lpData = ctypes.addressof(buffer)
        header.dwBufferLength = len(payload)
        ref = self._handle_ref(handle)
        self._check(
            self._dll.waveOutPrepareHeader(ref, ctypes.byref(header), ctypes.sizeof(WAVEHDR)),
            "waveOutPrepareHeader",
        )
        try:
            self._check(
                self._dll.waveOutWrite(ref, ctypes.byref(header), ctypes.sizeof(WAVEHDR)),
                "waveOutWrite",
            )
        except WinmmError:
            self._dll.waveOutUnprepareHeader(ref, ctypes.byref(header), ctypes.sizeof(WAVEHDR))
            raise
        self._voices.setdefault(handle, []).append(_Voice(header, buffer))

    def pending_count(self, handle: int) -> int:
        return len(self._voices.get(handle, ()))

    def reap(self, handle: int) -> int:
        """回收本句柄所有 WHDR_DONE 的 buffer（不泄漏 header）。"""
        voices = self._voices.get(handle)
        if not voices:
            return 0
        ref = self._handle_ref(handle)
        done = [voice for voice in voices if voice.header.dwFlags & WHDR_DONE]
        for voice in done:
            self._dll.waveOutUnprepareHeader(ref, ctypes.byref(voice.header), ctypes.sizeof(WAVEHDR))
            voices.remove(voice)
        return len(done)

    def reset(self, handle: int) -> None:
        self._dll.waveOutReset(self._handle_ref(handle))

    def set_volume(self, handle: int, packed: int) -> None:
        self._check(self._dll.waveOutSetVolume(self._handle_ref(handle), int(packed)), "waveOutSetVolume")


# ---------------------------------------------------------------------------
# 池
# ---------------------------------------------------------------------------

class _FormatPool:
    """同 (声道, 采样率) 的一组 waveOut 句柄。"""

    __slots__ = ("key", "handles", "index", "stamp", "exhausted")

    def __init__(self, key: tuple[int, int], handles: list[int]) -> None:
        self.key = key
        self.handles = list(handles)
        self.index = 0
        self.stamp = time.monotonic()
        # 开设备失败过就不再重试：waveOutOpen 实测 ~70ms，反复试会把 GUI 线程
        # 拖成幻灯片；已开出来的句柄照常排队播放（不丢音）。
        self.exhausted = False


class WinmmSoundPool:
    """waveOut 小池子：非阻塞提交、轮询回收、设备级音量。

    线程归属：GUI 线程（与 ClickSoundPool 一致）。所有入口都不阻塞、不抛异常，
    失败一律返回 False/0 让调用方回退 Qt 路径。
    """

    def __init__(
        self,
        api: "WinmmApi | None" = None,
        size: int = DEFAULT_POOL_SIZE,
        max_formats: int = MAX_FORMATS,
    ) -> None:
        self._api = api
        self._api_probed = api is not None
        self._size = max(1, int(size))
        self._max_formats = max(1, int(max_formats))
        self._formats: dict[tuple[int, int], _FormatPool] = {}
        self._volume = 1.0
        self._timer: Any = None
        self._scaled_cache: dict[Any, bytes] = {}
        #: 默认设备跟随（issue #315）：已开句柄绑死在「打开那一刻的默认设备」上，
        #: 用户切了默认输出后旧句柄继续往旧设备播。每次播放前限频对一次默认
        #: 端点 ID，变了就整池关掉、句柄按需在**新**默认设备上重建。
        self._device_id: "str | None" = None
        self._device_checked_at: float = 0.0
        _LIVE_POOLS.add(self)

    def _follow_default_device(self) -> None:
        """默认设备变了就整池重建（限频；查询失败绝不动——宁可留旧设备也不误清）。"""
        now = time.monotonic()
        if now - self._device_checked_at < _DEVICE_CHECK_INTERVAL_S:
            return
        self._device_checked_at = now
        current = _default_render_device_id()
        if current is None:
            return
        if self._device_id is None:
            self._device_id = current      # 首次记录
            return
        if current != self._device_id:
            log.info("默认音频设备已切换，winmm 句柄池按新设备重建")
            self._device_id = current
            self.clear()

    # ---------------- 可用性 ----------------

    def _ensure_api(self) -> "WinmmApi | None":
        if self._api is not None:
            return self._api
        if self._api_probed:
            return None
        self._api_probed = True
        self._api = default_api()
        if self._api is None:
            log.info("winmm 不可用，wav 播放回退 Qt 路径")
        return self._api

    def available(self) -> bool:
        return self._ensure_api() is not None

    # ---------------- 播放 ----------------

    def play(self, path: str | Path, volume: float = 1.0) -> bool:
        """提交一次播放。True = 已交给声卡（异步），False = 本次没播成。"""
        self._follow_default_device()
        clip = read_pcm16(path)
        if clip is None:
            return False
        return self.play_clip(clip, volume)

    def play_clip(self, clip: WavClip, volume: float = 1.0) -> bool:
        """提交一段已解析的 PCM16（调用方已解析过时避免重复读盘）。"""
        api = self._ensure_api()
        if api is None:
            return False
        if not clip.data:
            return False
        self.reap()
        key = (clip.channels, clip.sample_rate)
        pool = self._formats.get(key)
        if pool is None:
            pool = self._open_format(api, key)
            if pool is None:
                return False
            self._formats[key] = pool
            # 保护刚建的池：它是"马上要写入"的那个，被淘汰掉的话 _pick_device
            # 只会在已脱离登记的池上重开句柄（见 _evict_formats 的说明）。
            self._evict_formats(api, protect=pool)
        handle = self._pick_device(api, pool)
        if handle is None:
            return False
        try:
            api.write(handle, self._scaled(clip, volume))
        except Exception:
            log.warning("winmm 播放失败，回退 Qt 路径（channels=%s rate=%s）", clip.channels, clip.sample_rate, exc_info=True)
            return False
        self._start_reaper()
        return True

    def _scaled(self, clip: WavClip, volume: float) -> bytes:
        """按音量缩放 PCM16，结果按 (clip, 音量) 缓存。

        ``scale_pcm16`` 在 0<音量<1 时是逐样本的 Python 循环：45.7KB 的 48k 立体声
        碰撞音实测 3.19ms/次（``bench-before.json``），而密集碰撞（实机 5 次/秒）
        反复播的就是同一段 PCM、同一音量。缓存只是省掉重复计算，写进设备的字节与
        现算逐位相同；音量变化（含 0/1 的短路档）各自成键，不会串味。
        """
        value = clamp_volume(volume)
        if value <= 0.0 or value >= 1.0:
            return scale_pcm16(clip.data, value)      # 两个短路档本就极廉价，不进缓存
        if len(clip.data) > _SCALED_CACHE_MAX_CLIP_BYTES:
            return scale_pcm16(clip.data, value)      # 大素材不钉进内存
        key = (clip, value)
        try:
            hash(key)
        except TypeError:
            return scale_pcm16(clip.data, value)      # 不可哈希的替身 clip：不缓存
        cached = self._scaled_cache.get(key)
        if cached is None:
            cached = scale_pcm16(clip.data, value)
            if len(self._scaled_cache) >= _SCALED_CACHE_LIMIT:
                self._scaled_cache.clear()
            self._scaled_cache[key] = cached
        return cached

    def warm(self, paths: Iterable[str | Path]) -> int:
        """预热：为给定 wav 的格式各开满一个小池子（只开设备，不发声）。

        ``waveOutOpen`` 实测约 70ms（首次更贵），而 ``waveOutWrite`` 只有
        ~0.2ms——预热必须把句柄**一次开满**，否则快速连点会在 GUI 线程上
        一次一次地付开设备的钱。
        """
        api = self._ensure_api()
        if api is None:
            return 0
        opened = 0
        for path in paths:
            clip = read_pcm16(path)
            if clip is None or clip.converted:
                continue
            key = (clip.channels, clip.sample_rate)
            if key in self._formats:
                continue
            pool = self._open_format(api, key, fill=self._size)
            if pool is None:
                continue
            self._formats[key] = pool
            opened += len(pool.handles)
        self._evict_formats(api)
        return opened

    def _open_format(self, api: "WinmmApi", key: tuple[int, int], fill: int = 1) -> "_FormatPool | None":
        channels, sample_rate = key
        handles: list[int] = []
        for _ in range(max(1, int(fill))):
            try:
                handles.append(api.open(channels, sample_rate, 16))
            except Exception:
                log.warning("waveOutOpen 失败（channels=%s rate=%s），该格式回退 Qt 路径", channels, sample_rate)
                break
        if not handles:
            return None
        pool = _FormatPool(key, handles)
        pool.exhausted = len(handles) < max(1, int(fill))
        self._apply_volume(api, handles)
        return pool

    def _pick_device(self, api: "WinmmApi", pool: _FormatPool) -> "int | None":
        handles = pool.handles
        count = len(handles)
        # 优先占用当前没在播的句柄：快速连点各占一路，后者不盖前者
        for offset in range(count):
            index = (pool.index + offset) % count
            if api.pending_count(handles[index]) == 0:
                pool.index = pool.index + offset + 1
                pool.stamp = time.monotonic()
                return handles[index]
        # 全在播：池没满就再开一路（单个 ~70ms，属一次性代价；失败后不再重试）
        if count < self._size and not pool.exhausted:
            try:
                handle = api.open(pool.key[0], pool.key[1], 16)
            except Exception:
                handle = None
                pool.exhausted = True
            if handle is not None:
                handles.append(handle)
                self._apply_volume(api, (handle,))
                pool.index = len(handles)
                pool.stamp = time.monotonic()
                return handle
        # 池满：轮询排队（waveOutWrite 是队列语义，先入先播，一个不丢）
        index = pool.index % count
        pool.index = pool.index + 1
        pool.stamp = time.monotonic()
        return handles[index]

    # ---------------- 回收 ----------------

    def reap(self) -> int:
        """轮询回收所有句柄里已播完的 buffer，返回回收数量。"""
        api = self._api
        if api is None:
            return 0
        total = 0
        for pool in list(self._formats.values()):
            for handle in list(pool.handles):
                try:
                    total += api.reap(handle)
                except Exception:
                    log.debug("winmm 回收失败 handle=%s", handle, exc_info=True)
        if total and self._timer is not None and self.pending() == 0:
            self._stop_reaper()
        return total

    def pending(self) -> int:
        """在途（未播完）buffer 总数。"""
        api = self._api
        if api is None:
            return 0
        total = 0
        for pool in list(self._formats.values()):
            for handle in list(pool.handles):
                try:
                    total += api.pending_count(handle)
                except Exception:
                    pass
        return total

    def _start_reaper(self) -> None:
        """起兜底回收定时器（只在有 QCoreApplication 时；否则靠下次播放顺手回收）。"""
        if self._timer is not None:
            return
        try:
            from PySide6.QtCore import QCoreApplication, QThread, QTimer
        except Exception:
            return
        app = QCoreApplication.instance()
        if app is None:
            return
        # QTimer 只能在 GUI 线程建：Agent 音效存在从 worker 线程调 play_sound 的
        # 路径，非 GUI 线程建 timer 会得到"Timers can only be used with threads
        # started with QThread"告警且永不触发——那种情况下退化为下次播放顺手回收。
        if QThread.currentThread() != app.thread():
            return
        timer = QTimer()
        timer.setInterval(REAP_INTERVAL_MS)
        timer.timeout.connect(self._on_reap_tick)
        timer.start()
        self._timer = timer

    def _on_reap_tick(self) -> None:
        self.reap()
        if self.pending() == 0:
            self._stop_reaper()

    def _stop_reaper(self) -> None:
        timer = self._timer
        self._timer = None
        if timer is None:
            return
        try:
            timer.stop()
        except Exception:
            pass

    # ---------------- 音量 ----------------

    def set_volume(self, volume: float) -> float:
        """设备级音量（对齐 set_audio_volume 语义），返回 clamp 后的值。"""
        value = clamp_volume(volume)
        self._volume = value
        api = self._api
        if api is not None:
            packed = pack_volume(value)
            for handle in self._all_handles():
                try:
                    api.set_volume(handle, packed)
                except Exception:
                    log.debug("设置 winmm 音量失败 handle=%s", handle, exc_info=True)
        return value

    def _apply_volume(self, api: "WinmmApi", handles: Iterable[int]) -> None:
        if self._volume >= 1.0:
            return
        packed = pack_volume(self._volume)
        for handle in handles:
            try:
                api.set_volume(handle, packed)
            except Exception:
                log.debug("设置 winmm 音量失败 handle=%s", handle, exc_info=True)

    def _all_handles(self) -> list[int]:
        return [handle for pool in self._formats.values() for handle in pool.handles]

    def _evict_formats(self, api: "WinmmApi", protect: "_FormatPool | None" = None) -> None:
        """格式组数超限时，按最久未用淘汰没有在途 buffer 的组。

        ``protect`` 是调用方刚建好、马上要用的池。其余格式组全有在途 buffer 时
        它是候选集合里唯一一项，淘汰它会让随后的 ``_pick_device`` 在这个已经
        脱离登记的池上重开句柄——那个句柄不再被 reap/pending/clear 看到，连同
        ``_Voice``（WAVEHDR + PCM 缓冲）一起活到进程结束。
        """
        while len(self._formats) > self._max_formats:
            candidates = [
                (pool.stamp, key, pool) for key, pool in self._formats.items()
                if pool is not protect
                and not any(api.pending_count(handle) for handle in pool.handles)
            ]
            if not candidates:
                return
            _stamp, key, pool = min(candidates, key=lambda item: item[0])
            self._formats.pop(key, None)
            self._close_pool(api, pool)

    # ---------------- 生命周期 ----------------

    def _close_pool(self, api: "WinmmApi", pool: _FormatPool) -> None:
        for handle in pool.handles:
            try:
                api.close(handle)
            except Exception:
                log.debug("waveOutClose 失败 handle=%s", handle, exc_info=True)
        pool.handles.clear()

    def clear(self) -> None:
        """释放全部设备与在途 buffer（GUI 线程调用；生产路径只在退出/测试隔离用）。"""
        self._stop_reaper()
        self._scaled_cache.clear()
        api = self._api
        if api is not None:
            for pool in self._formats.values():
                self._close_pool(api, pool)
        self._formats.clear()

    def close(self) -> None:
        """close 语义 = clear（本池不拥有后台线程，仅可选一个 GUI 定时器）。"""
        self.clear()


# ---------------------------------------------------------------------------
# 模块级默认后端探测（只探一次；替换点供测试/诊断使用）
# ---------------------------------------------------------------------------

_default_api: "WinmmApi | None" = None
_default_api_probed = False


def default_api() -> "WinmmApi | None":
    """返回进程级共享的 winmm 后端；非 Windows / 加载失败 / 被测试替换时为 None。"""
    global _default_api, _default_api_probed
    if _default_api_probed:
        return _default_api
    _default_api_probed = True
    _default_api = WinmmApi.load()
    if _default_api is None:
        log.info("winmm.dll 不可用（非 Windows 或加载失败）")
    return _default_api


def reset_default_api_for_tests() -> None:
    """复位"只探一次"的缓存（测试隔离用）。"""
    global _default_api, _default_api_probed
    _default_api = None
    _default_api_probed = False


def _clear_live_pools_for_tests() -> None:
    """收口所有仍存活的池，并清空中立解析缓存（conftest 每测后调用，不改变产品语义）。

    解析缓存是模块级中立缓存（不含设备状态），跨用例留着会让"同一路径换内容"
    的用例读到上一测的解析结果；mtime/size 键本就防这种串味，这里是显式隔离。
    """
    _parse_cache.clear()
    for pool in list(_LIVE_POOLS):
        try:
            pool.clear()
        except Exception:
            pass
