# -*- coding: utf-8 -*-
"""点击音效播放器。

WAV 在 Windows 上继续使用 winsound（轻量、低延迟）；非 WAV（MP3/OGG/
FLAC/M4A 等）改用 QtMultimedia（QMediaPlayer + QAudioOutput，自带 FFmpeg
后端）播放。QtMultimedia 不可用时回退到系统播放器（macOS afplay / Linux
paplay / aplay），但绝不在 Windows 上用 winsound 播放非 WAV——winsound
只支持 WAV，传入 MP3 会播放系统默认提示音。
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("pet.click_sound")

_qt_player = None
_qt_audio = None
_qt_import_failed = False


def _qt_available() -> bool:
    """惰性探测 QtMultimedia；失败只记一次日志。"""
    global _qt_import_failed
    if _qt_import_failed:
        return False
    try:
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer  # noqa: F401

        return True
    except Exception as exc:  # 打包遗漏/精简环境缺失时兜底
        _qt_import_failed = True
        log.warning("QtMultimedia 不可用，非 WAV 点击音效将降级: %s", exc)
        return False


def _ensure_qt_player():
    """返回模块级单例播放器；不可用返回 None。

    播放器必须持久化，否则 Python GC 会在播放开始前回收对象。
    """
    global _qt_player, _qt_audio
    if _qt_player is not None:
        return _qt_player
    if not _qt_available():
        return None
    try:
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

        _qt_player = QMediaPlayer()
        _qt_audio = QAudioOutput()
        _qt_audio.setVolume(1.0)
        _qt_player.setAudioOutput(_qt_audio)
        return _qt_player
    except Exception:
        log.exception("创建 QMediaPlayer 失败")
        return None


def _play_with_qt(path: Path) -> bool:
    player = _ensure_qt_player()
    if player is None:
        return False
    try:
        from PySide6.QtCore import QUrl

        player.stop()
        player.setSource(QUrl.fromLocalFile(str(path)))
        player.play()
        return True
    except Exception:
        log.exception("QtMultimedia 播放失败: %s", path)
        return False


def _play_wav_windows(path: Path) -> bool:
    """Windows WAV：winsound 轻量播放。"""
    try:
        import winsound

        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        return True
    except Exception:
        log.exception("winsound 播放失败: %s", path)
        return False


def _play_with_system_player(path: Path) -> bool:
    """非 Windows 回退：afplay / paplay / aplay。"""
    player = shutil.which("afplay") or shutil.which("paplay") or shutil.which("aplay")
    if not player:
        return False
    command = [player, str(path)]
    if Path(player).name == "aplay":
        command.insert(1, "-q")
    try:
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        log.exception("系统播放器失败: %s", player)
        return False


def play_click_sound(path: Path | str) -> bool:
    """播放点击音效。返回 True 表示已交给某个播放通道。"""
    try:
        path = Path(path)
    except TypeError:
        return False
    if not path.is_file():
        return False

    if os.name == "nt" and path.suffix.lower() == ".wav":
        return _play_wav_windows(path)

    # MP3/OGG/FLAC/M4A 等统一优先 QtMultimedia（跨平台，自带 FFmpeg）
    if _play_with_qt(path):
        return True

    # 非 Windows 回退到系统播放器；Windows 非 WAV 宁可静默失败也不走
    # winsound，避免 PlaySound 用系统提示音掩盖真实的格式错误。
    if os.name != "nt":
        return _play_with_system_player(path)
    log.warning("非 WAV 音效无可用播放器，已跳过: %s", path)
    return False
