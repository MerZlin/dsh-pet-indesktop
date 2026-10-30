"""Windows 系统语音识别兜底（``System.Speech``，经 PowerShell 子进程）。

为什么不用 SAPI COM：``SpInprocRecognizer`` 从文件流识别需要事件循环，实测拿不到
``Recognition`` 事件；``System.Speech.SpeechRecognitionEngine`` 的
``SetInputToWaveFile`` + ``Recognize()`` 是同步的，实测可用（中文语言包即可）。

代价：每次识别起一个短命 PowerShell 进程（本机实测一段 3 秒中文约 1–2 秒）。
只在用户选了「系统识别」时走这条；高质量路径是本地 OpenAI 兼容 ASR 服务。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

SCRIPT = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Speech
$engine = $null
try {
    $engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine
    $engine.SetInputToWaveFile($args[0])
    $engine.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
    $result = $engine.Recognize()
    if ($null -ne $result) { Write-Output $result.Text }
} finally {
    if ($null -ne $engine) { $engine.Dispose() }
}
"""


class SystemAsrError(RuntimeError):
    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = str(reason or "unavailable")
        self.detail = str(detail or "")[:200]
        super().__init__(self.reason)


def powershell_path() -> str:
    if os.name != "nt":
        return ""
    return shutil.which("pwsh") or shutil.which("powershell") or ""


def available() -> bool:
    return bool(powershell_path())


def recognize_wav(
    wav_path: Path | str,
    *,
    timeout: float = 30.0,
    runner=None,
) -> str:
    """同步识别一个 WAV；``runner`` 是测试接缝（默认 subprocess.run）。"""
    executable = powershell_path()
    if not executable:
        raise SystemAsrError("unsupported")
    source = Path(wav_path)
    if not source.is_file():
        raise SystemAsrError("empty", "wav missing")
    run = runner or subprocess.run
    with tempfile.TemporaryDirectory(prefix="dshpet-asr-") as folder:
        script = Path(folder) / "recognize.ps1"
        script.write_text(SCRIPT, encoding="utf-8")
        try:
            completed = run(
                [
                    executable,
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                    str(source),
                ],
                capture_output=True,
                timeout=max(3.0, float(timeout)),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            raise SystemAsrError("timeout") from None
        except OSError as exc:
            raise SystemAsrError("unavailable", type(exc).__name__) from None
    if int(getattr(completed, "returncode", 1) or 0) != 0:
        raise SystemAsrError("unavailable", f"ps={int(completed.returncode)}")
    text = bytes(getattr(completed, "stdout", b"") or b"").decode(
        "utf-8", errors="replace"
    )
    text = text.strip()
    if not text:
        raise SystemAsrError("empty")
    return text
