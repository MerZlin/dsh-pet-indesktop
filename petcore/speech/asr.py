"""语音识别服务：系统兜底（System.Speech）或本地 OpenAI 兼容服务。

``AsrService`` 只做「读配置 → 选后端 → 识别」，可注入两个识别器，测试因此
完全不碰麦克风、PowerShell 与网络。音频只在内存里传递；系统兜底需要文件路径时
才写一个临时 WAV，识别完立即删除。
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any, Callable

from ..net import asr as http_asr
from ..platform import asr_windows

logger = logging.getLogger("petcore.speech.asr")

ENGINE_SYSTEM = "sapi"
ENGINE_HTTP = "http"
ENGINES = (ENGINE_SYSTEM, ENGINE_HTTP)


class AsrService:
    def __init__(
        self,
        config: Any = None,
        *,
        http_recognizer: Callable[..., str] | None = None,
        system_recognizer: Callable[..., str] | None = None,
    ) -> None:
        self.config = config
        self._http = http_recognizer or http_asr.transcribe
        self._system = system_recognizer or asr_windows.recognize_wav

    def _cfg(self, key: str, default: Any) -> Any:
        getter = getattr(self.config, "get", None)
        if not callable(getter):
            return default
        try:
            value = getter(key, default)
        except Exception:  # noqa: BLE001
            return default
        return default if value is None else value

    def engine(self) -> str:
        value = str(self._cfg("asr_engine", ENGINE_SYSTEM) or "").strip().lower()
        return value if value in ENGINES else ENGINE_SYSTEM

    def available_engines(self) -> tuple[str, ...]:
        found: list[str] = []
        if asr_windows.available():
            found.append(ENGINE_SYSTEM)
        found.append(ENGINE_HTTP)
        return tuple(found)

    def language(self) -> str:
        return str(self._cfg("asr_language", "zh") or "zh").strip()[:16] or "zh"

    def _timeout(self) -> float:
        try:
            value = float(self._cfg("asr_timeout", 30.0) or 30.0)
        except (TypeError, ValueError):
            value = 30.0
        return max(5.0, min(120.0, value))

    def recognize(self, wav: bytes, *, language: str | None = None) -> str:
        payload = bytes(wav or b"")
        if not payload:
            raise http_asr.AsrError("empty")
        lang = str(language or self.language())
        if self.engine() == ENGINE_HTTP:
            endpoint = str(self._cfg("asr_endpoint", "") or "").strip()
            model = str(self._cfg("asr_model", "") or "").strip()
            if not endpoint:
                raise http_asr.AsrError("unconfigured")
            return self._http(
                endpoint,
                payload,
                model=model,
                language=lang,
                timeout=self._timeout(),
            )
        return self._recognize_system(payload, lang)

    def _recognize_system(self, wav: bytes, language: str) -> str:
        with tempfile.NamedTemporaryFile(
            prefix="dshpet-mic-", suffix=".wav", delete=False
        ) as handle:
            handle.write(wav)
            path = Path(handle.name)
        try:
            try:
                return self._system(path, timeout=self._timeout())
            except asr_windows.SystemAsrError as exc:
                raise http_asr.AsrError(exc.reason, exc.detail) from None
            except Exception as exc:  # noqa: BLE001 - 不把异常文本抛给 UI
                raise http_asr.AsrError("unavailable", type(exc).__name__) from None
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
