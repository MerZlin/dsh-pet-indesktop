"""Windows 麦克风录音（WinMM ``waveIn``；零 Qt、零新依赖）。

分层与 ``pet/sound_winmm.py`` 一致：``WinmmInputApi`` 是唯一触碰 ``winmm.dll``
的一层（结构体、句柄、header 生命周期），测试用同协议替身整体替换；
``MicRecorder`` 只做状态机与 WAV 封装；``wav_bytes`` 是纯函数。

录音用**一个预分配的大 buffer + CALLBACK_NULL**：开始后驱动直接写入，停止时
``waveInReset`` 收口，读 ``WAVEHDR.dwBytesRecorded``。不注册回调，避免驱动线程
回调 Python 的线程/生命周期问题。最长录音由调用方（ASR 通道）计时并 stop。
"""

from __future__ import annotations

import ctypes
import io
import os
import time
import wave
from dataclasses import dataclass

WAVE_MAPPER = 0xFFFFFFFF
WAVE_FORMAT_PCM = 0x0001
CALLBACK_NULL = 0x00000000
MMSYSERR_NOERROR = 0

SAMPLE_RATE = 16000
CHANNELS = 1
BITS = 16
MAX_SECONDS = 60
MAX_BYTES = 16 * 1024 * 1024


class MicError(RuntimeError):
    """``reason`` 是稳定分类：unsupported / no_device / busy / open / record。"""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = str(reason or "record")
        self.detail = str(detail or "")[:200]
        super().__init__(self.reason)


@dataclass(frozen=True)
class MicDevice:
    index: int
    name: str


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


class WAVEINCAPS(ctypes.Structure):
    _fields_ = [
        ("wMid", ctypes.c_ushort),
        ("wPid", ctypes.c_ushort),
        ("vDriverVersion", ctypes.c_uint32),
        ("szPname", ctypes.c_wchar * 32),
        ("dwFormats", ctypes.c_uint32),
        ("wChannels", ctypes.c_ushort),
        ("wReserved1", ctypes.c_ushort),
    ]


class WinmmInputApi:
    """``winmm`` 录音函数的最小包装；非 Windows 直接 ``unsupported``。"""

    def __init__(self) -> None:
        if os.name != "nt":
            raise MicError("unsupported")
        try:
            self.winmm = ctypes.WinDLL("winmm", use_last_error=True)
        except OSError as exc:  # pragma: no cover - Windows 才有
            raise MicError("unsupported", type(exc).__name__) from exc
        self._bind()

    def _bind(self) -> None:
        self.winmm.waveInGetNumDevs.argtypes = []
        self.winmm.waveInGetNumDevs.restype = ctypes.c_uint
        self.winmm.waveInGetDevCapsW.argtypes = [
            ctypes.c_uint,
            ctypes.POINTER(WAVEINCAPS),
            ctypes.c_uint,
        ]
        self.winmm.waveInGetDevCapsW.restype = ctypes.c_uint
        self.winmm.waveInOpen.argtypes = [
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_uint,
            ctypes.POINTER(WAVEFORMATEX),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint,
        ]
        self.winmm.waveInOpen.restype = ctypes.c_uint
        self.winmm.waveInPrepareHeader.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(WAVEHDR),
            ctypes.c_uint,
        ]
        self.winmm.waveInPrepareHeader.restype = ctypes.c_uint
        self.winmm.waveInAddBuffer.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(WAVEHDR),
            ctypes.c_uint,
        ]
        self.winmm.waveInAddBuffer.restype = ctypes.c_uint
        self.winmm.waveInStart.argtypes = [ctypes.c_void_p]
        self.winmm.waveInStart.restype = ctypes.c_uint
        self.winmm.waveInStop.argtypes = [ctypes.c_void_p]
        self.winmm.waveInStop.restype = ctypes.c_uint
        self.winmm.waveInReset.argtypes = [ctypes.c_void_p]
        self.winmm.waveInReset.restype = ctypes.c_uint
        self.winmm.waveInUnprepareHeader.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(WAVEHDR),
            ctypes.c_uint,
        ]
        self.winmm.waveInUnprepareHeader.restype = ctypes.c_uint
        self.winmm.waveInClose.argtypes = [ctypes.c_void_p]
        self.winmm.waveInClose.restype = ctypes.c_uint

    def num_devices(self) -> int:
        return int(self.winmm.waveInGetNumDevs())

    def device_name(self, index: int) -> str:
        caps = WAVEINCAPS()
        code = self.winmm.waveInGetDevCapsW(
            int(index), ctypes.byref(caps), ctypes.sizeof(caps)
        )
        if code != MMSYSERR_NOERROR:
            return f"麦克风 {int(index) + 1}"
        return str(caps.szPname or "").strip() or f"麦克风 {int(index) + 1}"

    def open(
        self, device: int, sample_rate: int, channels: int, max_bytes: int
    ) -> tuple[ctypes.c_void_p, WAVEHDR, ctypes.Array]:
        fmt = WAVEFORMATEX(
            wFormatTag=WAVE_FORMAT_PCM,
            nChannels=channels,
            nSamplesPerSec=sample_rate,
            nAvgBytesPerSec=sample_rate * channels * 2,
            nBlockAlign=channels * 2,
            wBitsPerSample=16,
            cbSize=0,
        )
        handle = ctypes.c_void_p()
        code = self.winmm.waveInOpen(
            ctypes.byref(handle),
            ctypes.c_uint(device if device >= 0 else WAVE_MAPPER),
            ctypes.byref(fmt),
            None,
            None,
            CALLBACK_NULL,
        )
        if code != MMSYSERR_NOERROR or not handle:
            raise MicError("open", f"waveInOpen={int(code)}")
        buffer = ctypes.create_string_buffer(max_bytes)
        header = WAVEHDR(
            lpData=ctypes.cast(buffer, ctypes.c_void_p),
            dwBufferLength=max_bytes,
        )
        code = int(
            self.winmm.waveInPrepareHeader(
                handle, ctypes.byref(header), ctypes.sizeof(header)
            )
        )
        if code != MMSYSERR_NOERROR:
            self.close(handle)
            raise MicError("open", f"waveInPrepareHeader={code}")
        code = int(
            self.winmm.waveInAddBuffer(
                handle, ctypes.byref(header), ctypes.sizeof(header)
            )
        )
        if code != MMSYSERR_NOERROR:
            self.unprepare(handle, header)
            self.close(handle)
            raise MicError("open", f"waveInAddBuffer={code}")
        return handle, header, buffer

    def start(self, handle: ctypes.c_void_p) -> None:
        code = int(self.winmm.waveInStart(handle))
        if code != MMSYSERR_NOERROR:
            raise MicError("record", f"waveInStart={code}")

    def stop(self, handle: ctypes.c_void_p) -> None:
        self.winmm.waveInStop(handle)

    def reset(self, handle: ctypes.c_void_p) -> None:
        self.winmm.waveInReset(handle)

    def recorded_bytes(self, header: WAVEHDR) -> int:
        return int(header.dwBytesRecorded)

    def read_buffer(self, buffer: ctypes.Array, size: int) -> bytes:
        usable = max(0, min(int(size), int(header_size(buffer))))
        return bytes(buffer.raw[:usable])

    def unprepare(self, handle: ctypes.c_void_p, header: WAVEHDR) -> None:
        self.winmm.waveInUnprepareHeader(
            handle, ctypes.byref(header), ctypes.sizeof(header)
        )

    def close(self, handle: ctypes.c_void_p) -> None:
        if handle:
            self.winmm.waveInClose(handle)


def header_size(buffer: ctypes.Array) -> int:
    return len(buffer.raw)


def wav_bytes(
    pcm: bytes,
    *,
    sample_rate: int = SAMPLE_RATE,
    channels: int = CHANNELS,
) -> bytes:
    """把 PCM16 裸流封装成标准 WAV（纯函数，任何平台可测）。"""
    payload = bytes(pcm or b"")
    payload = payload[: len(payload) - (len(payload) % max(1, channels * 2))]
    sink = io.BytesIO()
    with wave.open(sink, "wb") as stream:
        stream.setnchannels(int(channels))
        stream.setsampwidth(2)
        stream.setframerate(int(sample_rate))
        stream.writeframes(payload)
    return sink.getvalue()


class MicRecorder:
    """一次只录一段：``start(device)`` → ``stop()`` 返回 WAV / ``cancel()``。"""

    def __init__(self, api=None, *, sample_rate: int = SAMPLE_RATE) -> None:
        self._api = api
        self.sample_rate = max(8000, min(48000, int(sample_rate)))
        self._handle = None
        self._header = None
        self._buffer = None
        self._started_at = 0.0

    def _system_api(self):
        if self._api is None:
            self._api = WinmmInputApi()
        return self._api

    def available(self) -> bool:
        try:
            return self._system_api().num_devices() > 0
        except MicError:
            return False
        except Exception:
            return False

    def devices(self) -> tuple[MicDevice, ...]:
        try:
            api = self._system_api()
            return tuple(
                MicDevice(index=index, name=api.device_name(index))
                for index in range(api.num_devices())
            )
        except Exception:
            return ()

    @property
    def recording(self) -> bool:
        return self._handle is not None

    def elapsed(self) -> float:
        return max(0.0, time.monotonic() - self._started_at) if self.recording else 0.0

    def start(self, device: int = 0, *, max_seconds: int = 30) -> None:
        if self.recording:
            raise MicError("busy")
        api = self._system_api()
        seconds = max(1, min(MAX_SECONDS, int(max_seconds)))
        max_bytes = min(MAX_BYTES, self.sample_rate * CHANNELS * 2 * seconds)
        handle, header, buffer = api.open(0 if int(device) < 0 else int(device),
                                          self.sample_rate, CHANNELS, max_bytes)
        try:
            api.start(handle)
        except Exception:
            api.unprepare(handle, header)
            api.close(handle)
            raise
        self._handle, self._header, self._buffer = handle, header, buffer
        self._started_at = time.monotonic()

    def stop(self) -> bytes:
        if not self.recording:
            raise MicError("not_recording")
        api = self._system_api()
        handle, header, buffer = self._handle, self._header, self._buffer
        self._handle = self._header = self._buffer = None
        try:
            api.stop(handle)
            api.reset(handle)
            size = api.recorded_bytes(header)
            pcm = api.read_buffer(buffer, size)
        finally:
            api.unprepare(handle, header)
            api.close(handle)
        return wav_bytes(pcm, sample_rate=self.sample_rate, channels=CHANNELS)

    def cancel(self) -> None:
        if not self.recording:
            return
        api = self._system_api()
        handle, header, _buffer = self._handle, self._header, self._buffer
        self._handle = self._header = self._buffer = None
        try:
            api.stop(handle)
            api.reset(handle)
        finally:
            api.unprepare(handle, header)
            api.close(handle)
