"""语音合成端口的公开契约。

背景：P4 前 mod 自己做 TTS（`voice.chime` 直接用 `QTextToSpeech`），既绕过了
「网络只在 core」的红线，也没法共享缓存与失败兜底。P4-b 起**合成与传输归 core**，
mod 只声明要说什么、用哪个后端，以及自己什么时候说。

分工：

- :class:`SpeechPort`：provider 列表、预检、合成（阻塞，给预缓存用）、
  播放（异步，走 core 唯一音频通道）、停止、密钥读写。
- mod：日程、台词库、免打扰判断、动画与气泡（`voice.chime` 就是这么做的）。

失败一律收敛成 :class:`SpeechError.reason`（`unavailable/network/timeout/auth/
rate_limit/protocol/cache`），不把供应商异常文本直接抛给界面。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

#: 失败分类（与 net/errors 同口径，另加 `unavailable`/`cache`）。
SPEECH_REASONS = (
    "unavailable",
    "network",
    "timeout",
    "auth",
    "rate_limit",
    "protocol",
    "cache",
)


@dataclass(frozen=True, slots=True)
class SpeechRequest:
    """一次合成请求。

    ``provider`` 留空表示用 core 配置里的后端链首项；``cache_key`` 留空时由
    core 按 ``provider/flavor`` 生成（换音色必须换缓存键）。
    ``rate``/``volume`` 留空表示沿用 core 的 ``speech_rate``/``speech_volume``；
    显式传值（如 ``voice.chime`` 的页面）优先。
    """

    text: str
    provider: str = ""
    voice: str = ""
    rate: float | None = None
    pitch: float = 0.0
    volume: float | None = None
    cache_key: str = ""


@dataclass(frozen=True, slots=True)
class SpeechResult:
    """一次合成产物；``volume`` 是**实际采用**的音量（请求留空时取配置值）。

    播放层必须用这个值，不能回读 ``SpeechRequest.volume``：请求里的 ``None``
    表示「沿用配置」，把它直接交给 ``float()`` 会失败，整句语音静默丢失。
    """

    path: Path
    provider: str
    voice: str
    cached: bool = False
    text: str = ""
    volume: float = 1.0


@dataclass(frozen=True, slots=True)
class SpeechProviderInfo:
    """provider 的展示信息；``fields`` 是设置页要渲染的配置字段名。"""

    id: str
    label: str
    online: bool = False
    fields: tuple[str, ...] = ()
    available: bool = True
    reason: str = ""


class SpeechError(RuntimeError):
    reason = "unavailable"

    def __init__(self, reason: str = "", message: str = "") -> None:
        if reason:
            self.reason = str(reason)
        super().__init__(message or self.reason)


class SpeechPort(Protocol):
    def providers(self) -> tuple[SpeechProviderInfo, ...]: ...

    def preflight(self, provider: str = "") -> str:
        """无网也能跑：把已失效的音色换成默认音色并返回最终 provider id。"""
        ...

    def synthesize(self, request: SpeechRequest) -> SpeechResult:
        """阻塞合成（缓存命中直接返回）；调用方负责放到后台线程。"""
        ...

    def speak(
        self,
        request: SpeechRequest,
        *,
        owner: str = "",
        slot: int | None = None,
        guard: Callable[[], bool] | None = None,
        on_finished: Callable[[SpeechResult], None] | None = None,
        on_done: Callable[[SpeechResult], None] | None = None,
        on_error: Callable[[SpeechError], None] | None = None,
    ) -> str:
        """on_done retains synthesis/play-start semantics; on_finished means real audio end.

        Cancellation suppresses both callbacks. Queued jobs share one bounded channel.
        """
        ...

    def stop(self, token: str = "") -> None: ...

    def playing(self) -> bool: ...

    def cancel_owner(self, owner: str) -> None: ...
    def cancel_slot(self, slot: int) -> None: ...
    def register_yield(self, owner: str, callback: Callable) -> Callable[[], None]: ...
    def should_yield(self, moment, *, requester: str = "") -> bool: ...

    def cache_dir(self) -> Path: ...

    def set_secret(self, provider: str, field: str, value: str) -> None: ...

    def secret_status(self, provider: str) -> dict[str, bool]: ...
