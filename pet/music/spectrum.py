# -*- coding: utf-8 -*-
"""系统输出音频频谱（Windows WASAPI 回环采集，只用 ctypes + numpy）。

为什么是「系统回环」而不是抓网易云进程：

- 网易云不上报可读的音频流，SMTC 也只给元信息（歌名/歌手），没有任何频谱数据；
- Windows 的 WASAPI 提供 ``AUDCLNT_STREAMFLAGS_LOOPBACK``，能直接录到**正在播放的
  输出设备**上的混音（即用户此刻听到的声音），48 kHz / 双声道 / float32；
- 只依赖标准库 ``ctypes`` 与已随包分发的 ``numpy``，不需要 soundcard / PyAudio
  这类带 C 扩展的新依赖（冻结包只能替换 PYZ 里的纯 Python 模块，装不了新二进制）。

数据流向：采集线程 → 去重声道 → 滚动窗口 → 汉宁窗 rfft → 对数分频（22 段）
→ 分贝归一 → 快起慢落平滑 → 主线程按心跳读 ``levels``。

失败一律降级：任何一步抛错就把 ``failed`` 置位、清空电平，调用方回落到程序化波形，
不会拖垮悬浮条。
"""

from __future__ import annotations

import ctypes
import logging
import math
import sys
import threading
import time
import uuid
from ctypes import wintypes

_BARS = 22
_FRAME = 4096           # 48 kHz 下约 85 ms，频率分辨率 ≈ 11.7 Hz（低频段才够分格）
_LOW_HZ = 60.0
_HIGH_HZ = 8000.0
_DB_FLOOR = -72.0       # 该分贝以下视作静音
_DB_SPAN = 57.0         # 到 -15 dB 满格
_TILT_DB = 0.3          # 每段递增的斜率补偿：音乐高频天然比低频弱
_GAMMA = 0.9
_DECAY = 0.82           # 落下去的平滑系数（越大越慢）
_POLL_S = 0.02
_SMOOTH_LOW = 0.18      # 衰减时新值的权重

CLSID_MMDEVICE_ENUMERATOR = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
IID_IMMDEVICE_ENUMERATOR = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
IID_IAUDIO_CLIENT = "{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}"
IID_IAUDIO_CAPTURE_CLIENT = "{C8ADBD64-E71E-48a0-A4DE-185C395CD317}"

_CLSCTX_ALL = 23
_AUDCLNT_SHAREMODE_SHARED = 0
_AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
_AUDCLNT_BUFFERFLAGS_SILENT = 0x2
_WAVE_FORMAT_IEEE_FLOAT = 3
_WAVE_FORMAT_EXTENSIBLE = 0xFFFE

_logger = logging.getLogger(__name__)


def _log_error(where: str, exc: BaseException) -> None:
    _logger.error("%s: %r", where, exc, exc_info=True)


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


def _guid(value: str) -> _GUID:
    parsed = uuid.UUID(value)
    out = _GUID()
    out.Data1 = parsed.time_low
    out.Data2 = parsed.time_mid
    out.Data3 = parsed.time_hi_version
    for index, byte in enumerate(parsed.bytes[8:]):
        out.Data4[index] = byte
    return out


_call_cache: dict[tuple, object] = {}


def _call(ptr, index: int, restype, *argtypes):
    """按 COM 虚表下标取出方法并调用（第一个参数固定是 this 指针）。

    每次调用都 ``WINFUNCTYPE(...)`` 重建函数对象很贵（实测占采集线程 CPU 的大头），
    这里按「接口地址 + 下标 + 签名」缓存；同一对象的虚表项本来就不变。
    """
    key = (ptr.value, index, restype, argtypes)
    fn = _call_cache.get(key)
    if fn is None:
        table = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        fn = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(table[index])
        _call_cache[key] = fn
    return fn


def _release(ptr) -> None:
    if not ptr:
        return
    try:
        _call(ptr, 2, ctypes.c_ulong)(ptr)
    except Exception:
        pass


def _band_edges(bars: int) -> list[float]:
    step = math.log10(_HIGH_HZ / _LOW_HZ) / bars
    return [_LOW_HZ * (10 ** (step * i)) for i in range(bars + 1)]


def _band_centers(bars: int):
    """每段几何中心频率（用 numpy，供无 bin 的低频段插值取电平）。"""
    import numpy as np

    edges = np.asarray(_band_edges(bars))
    return np.sqrt(edges[:-1] * edges[1:])


def _band_db(spec, freqs, centers, band_masks) -> list[float]:
    """每段的分贝值。

    低频段的带宽可能比 FFT 分辨率还窄（例如 60~66 Hz 在 4096 点 / 48 kHz 下
    一个 bin 都落不进去），这种段直接返回 ``-140`` 就会变成一根永不跳动的死条。
    所以段内没有 bin 时退回「离段中心最近的那个 bin」——它承载的是真实音频能量，
    相邻两段偶尔共用同一个 bin，视觉上就是低频两根条同步起伏，不会僵死。
    """
    import numpy as np

    curve = 20.0 * np.log10(np.maximum(spec, 1e-7))
    out = []
    for center, mask in zip(centers, band_masks):
        if mask.any():
            mag = float(np.sqrt(np.mean(np.square(spec[mask]))))
            out.append(20.0 * math.log10(max(mag, 1e-7)))
        else:
            out.append(float(curve[int(np.argmin(np.abs(freqs - center)))]))
    return out


class SystemSpectrum:
    """在后台线程里录系统输出并持续产出 ``bars`` 个 0~1 的电平。"""

    def __init__(self, bars: int = _BARS) -> None:
        self._bars = bars
        self._levels = [0.0] * bars
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._failed = False

    @property
    def failed(self) -> bool:
        return self._failed

    @property
    def levels(self) -> list[float]:
        with self._lock:
            return list(self._levels)

    def start(self) -> None:
        if self._thread is not None or self._failed or sys.platform != "win32":
            return
        self._stop.clear()
        thread = threading.Thread(target=self._run, name="music-spectrum", daemon=True)
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None
        with self._lock:
            self._levels = [0.0] * self._bars

    def _publish(self, levels: list[float]) -> None:
        with self._lock:
            self._levels = levels

    def _run(self) -> None:
        try:
            self._capture()
        except Exception as exc:
            _log_error("spectrum_capture", exc)
            self._failed = True
        finally:
            self._publish([0.0] * self._bars)

    def _capture(self) -> None:
        import numpy as np

        ole32 = ctypes.oledll.ole32
        ole32.CoInitialize(None)
        enum_ptr = ctypes.c_void_p()
        device_ptr = ctypes.c_void_p()
        client_ptr = ctypes.c_void_p()
        capture_ptr = ctypes.c_void_p()
        try:
            ole32.CoCreateInstance(
                ctypes.byref(_guid(CLSID_MMDEVICE_ENUMERATOR)),
                None,
                _CLSCTX_ALL,
                ctypes.byref(_guid(IID_IMMDEVICE_ENUMERATOR)),
                ctypes.byref(enum_ptr),
            )
            # IMMDeviceEnumerator::GetDefaultAudioEndpoint(eRender=0, eConsole=0)
            _call(enum_ptr, 4, ctypes.HRESULT, ctypes.c_int, ctypes.c_int,
                  ctypes.POINTER(ctypes.c_void_p))(
                enum_ptr, 0, 0, ctypes.byref(device_ptr)
            )
            # IMMDevice::Activate(IID_IAudioClient, CLSCTX_ALL, None, &client)
            _call(device_ptr, 3, ctypes.HRESULT, ctypes.POINTER(_GUID), wintypes.DWORD,
                  ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
                device_ptr, ctypes.byref(_guid(IID_IAUDIO_CLIENT)), _CLSCTX_ALL, None,
                ctypes.byref(client_ptr)
            )
            fmt_ptr = ctypes.POINTER(_WAVEFORMATEX)()
            # IAudioClient::GetMixFormat(&fmt)
            _call(client_ptr, 8, ctypes.HRESULT, ctypes.POINTER(ctypes.POINTER(_WAVEFORMATEX)))(
                client_ptr, ctypes.byref(fmt_ptr)
            )
            fmt = fmt_ptr.contents
            # IAudioClient::Initialize(SHARED, LOOPBACK, 500 ms, 0, fmt, None)
            _call(client_ptr, 3, ctypes.HRESULT, ctypes.c_int, wintypes.DWORD,
                  ctypes.c_longlong, ctypes.c_longlong, ctypes.POINTER(_WAVEFORMATEX),
                  ctypes.c_void_p)(
                client_ptr, _AUDCLNT_SHAREMODE_SHARED, _AUDCLNT_STREAMFLAGS_LOOPBACK,
                5_000_000, 0, fmt_ptr, None
            )
            # IAudioClient::GetService(IID_IAudioCaptureClient, &capture)
            _call(client_ptr, 14, ctypes.HRESULT, ctypes.POINTER(_GUID),
                  ctypes.POINTER(ctypes.c_void_p))(
                client_ptr, ctypes.byref(_guid(IID_IAUDIO_CAPTURE_CLIENT)),
                ctypes.byref(capture_ptr)
            )
            _call(client_ptr, 10, ctypes.HRESULT)(client_ptr)

            channels = max(1, int(fmt.nChannels))
            rate = float(fmt.nSamplesPerSec or 48000)
            is_float = fmt.wFormatTag in (_WAVE_FORMAT_IEEE_FLOAT, _WAVE_FORMAT_EXTENSIBLE)
            dtype = np.float32 if is_float else np.int16
            block_align = int(fmt.nBlockAlign)
            edges = _band_edges(self._bars)
            centers = _band_centers(self._bars)
            window = np.hanning(_FRAME)
            freqs = np.fft.rfftfreq(_FRAME, 1.0 / rate)
            bands = [(freqs >= edges[i]) & (freqs < edges[i + 1]) for i in range(self._bars)]
            smooth = [0.0] * self._bars
            samples = np.zeros(0, dtype=np.float32)
            last_emit = 0.0

            while not self._stop.is_set():
                size = wintypes.UINT()
                _call(capture_ptr, 5, ctypes.HRESULT, ctypes.POINTER(wintypes.UINT))(
                    capture_ptr, ctypes.byref(size)
                )
                if not size.value:
                    time.sleep(_POLL_S)
                    continue
                while size.value:
                    data = ctypes.POINTER(ctypes.c_byte)()
                    frames = wintypes.UINT()
                    flags = wintypes.DWORD()
                    dpos = ctypes.c_ulonglong()
                    qpos = ctypes.c_ulonglong()
                    _call(capture_ptr, 3, ctypes.HRESULT, ctypes.POINTER(ctypes.POINTER(ctypes.c_byte)),
                          ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.DWORD),
                          ctypes.POINTER(ctypes.c_ulonglong), ctypes.POINTER(ctypes.c_ulonglong))(
                        capture_ptr, ctypes.byref(data), ctypes.byref(frames), ctypes.byref(flags),
                        ctypes.byref(dpos), ctypes.byref(qpos)
                    )
                    if frames.value:
                        raw = np.frombuffer(
                            ctypes.string_at(data, frames.value * block_align), dtype=dtype
                        )
                        mono = raw.reshape(-1, channels).mean(axis=1).astype(np.float32)
                        if not is_float:
                            mono /= 32768.0
                        if flags.value & _AUDCLNT_BUFFERFLAGS_SILENT:
                            mono[:] = 0.0
                        samples = np.concatenate([samples, mono])[-_FRAME * 2:]
                    _call(capture_ptr, 4, ctypes.HRESULT, wintypes.UINT)(capture_ptr, frames.value)
                    _call(capture_ptr, 5, ctypes.HRESULT, ctypes.POINTER(wintypes.UINT))(
                        capture_ptr, ctypes.byref(size)
                    )
                if samples.size < _FRAME:
                    continue
                now = time.time()
                if now - last_emit < 0.03:
                    continue
                last_emit = now
                spec = np.abs(np.fft.rfft(samples[-_FRAME:] * window)) / _FRAME
                db_values = _band_db(spec, freqs, centers, bands)
                for i, db in enumerate(db_values):
                    raw = (db + _TILT_DB * i - _DB_FLOOR) / _DB_SPAN
                    level = max(0.0, min(1.0, raw)) ** _GAMMA
                    previous = smooth[i]
                    smooth[i] = level if level > previous else previous * _DECAY + level * _SMOOTH_LOW
                self._publish(list(smooth))
        finally:
            try:
                _call(client_ptr, 11, ctypes.HRESULT)(client_ptr)   # IAudioClient::Stop
            except Exception:
                pass
            _release(capture_ptr)
            _release(client_ptr)
            _release(device_ptr)
            _release(enum_ptr)
            ole32.CoUninitialize()
