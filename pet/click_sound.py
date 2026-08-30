# -*- coding: utf-8 -*-
"""通用音效播放器与点击音效包支持。

取消 Windows winsound 路径，WAV/MP3/OGG/FLAC/M4A 全部走 QtMultimedia 以支持音量控制；
QtMultimedia 不可用时静默失败并记录 warning，绝不使用系统提示音替代。
非 Windows 平台在 QtMultimedia 缺失时回退到系统播放器。
"""
from __future__ import annotations

import logging
import hashlib
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from collections.abc import Sequence
from pathlib import Path
from typing import Any

log = logging.getLogger("pet.click_sound")

_qt_player = None
_qt_audio = None
_qt_effects: dict[str, Any] = {}
_qt_decoders: dict[str, Any] = {}
_qt_player_pool: list[tuple[Any, Any]] = []
_qt_player_index = 0
_qt_import_failed = False
_qt_classes: tuple[Any, ...] | None = None
_PLAYER_POOL_SIZE = 4

SUPPORTED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac", ".m4a"}


def _qt_available() -> bool:
    """惰性探测 QtMultimedia；失败只记一次日志。"""
    global _qt_import_failed
    if _qt_import_failed:
        return False
    try:
        from PySide6.QtMultimedia import QAudioDecoder, QAudioOutput, QMediaPlayer, QSoundEffect  # noqa: F401

        return True
    except Exception as exc:  # 打包遗漏/精简环境缺失时兜底
        _qt_import_failed = True
        log.warning("QtMultimedia 不可用，音效将降级或静默失败: %s", exc)
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


def _qt_multimedia_classes():
    """Load multimedia classes lazily so headless/minimal installs can import this module."""
    global _qt_classes, _qt_import_failed
    if _qt_classes is not None:
        return _qt_classes
    if not _qt_available():
        return None
    try:
        from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat, QAudioOutput, QMediaPlayer, QSoundEffect
        _qt_classes = (QAudioDecoder, QAudioFormat, QAudioOutput, QMediaPlayer, QSoundEffect)
        return _qt_classes
    except Exception as exc:
        _qt_import_failed = True
        log.warning("QtMultimedia 音效类不可用: %s", exc)
        return None


def _sound_cache_dir() -> Path:
    try:
        from PySide6.QtCore import QStandardPaths
        base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    except Exception:
        base = ""
    root = Path(base) if base else Path(tempfile.gettempdir()) / "dsh-pet"
    result = root / "sounds_cache"
    result.mkdir(parents=True, exist_ok=True)
    return result


def _cache_path(source: Path) -> Path:
    stat = source.stat()
    key = hashlib.sha256(f"{source.resolve()}:{stat.st_mtime_ns}:{stat.st_size}".encode()).hexdigest()[:20]
    return _sound_cache_dir() / f"{source.stem}-{key}.wav"


def _effect_for(path: Path):
    classes = _qt_multimedia_classes()
    if classes is None:
        return None
    key = str(path.resolve())
    effect = _qt_effects.get(key)
    if effect is None:
        try:
            from PySide6.QtCore import QUrl
            effect = classes[4]()
            effect.setSource(QUrl.fromLocalFile(str(path)))
            _qt_effects[key] = effect
        except Exception:
            log.exception("创建 QSoundEffect 失败: %s", path)
            return None
    return effect


def _play_with_effect(path: Path, volume: float) -> bool:
    effect = _effect_for(path)
    if effect is None:
        return False
    try:
        effect.setVolume(volume)
        effect.play()  # QSoundEffect.play() restarts the short sound immediately.
        return True
    except Exception:
        log.exception("QSoundEffect 播放失败: %s", path)
        return False


def _warm_player_pool() -> None:
    """预创建 QMediaPlayer 池，避免首次点击时初始化 QtMultimedia 造成卡顿。"""
    classes = _qt_multimedia_classes()
    if classes is None:
        return
    try:
        if not _qt_player_pool:
            if _qt_player is not None and _qt_audio is not None:
                _qt_player_pool.append((_qt_player, _qt_audio))
            for _ in range(_PLAYER_POOL_SIZE):
                if len(_qt_player_pool) >= _PLAYER_POOL_SIZE:
                    break
                player, audio = classes[3](), classes[2]()
                player.setAudioOutput(audio)
                _qt_player_pool.append((player, audio))
    except Exception:
        log.exception("预创建 QMediaPlayer 池失败")


def _player_pool_play(path: Path, volume: float) -> bool:
    global _qt_player_index
    classes = _qt_multimedia_classes()
    if classes is None:
        return False
    try:
        _warm_player_pool()
        if not _qt_player_pool:
            return False
        player, audio = _qt_player_pool[_qt_player_index % len(_qt_player_pool)]
        _qt_player_index += 1
        audio.setVolume(volume)
        player.stop()
        from PySide6.QtCore import QUrl
        player.setSource(QUrl.fromLocalFile(str(path)))
        player.play()
        return True
    except Exception:
        log.exception("QMediaPlayer 池播放失败: %s", path)
        return False


def _audio_buffer_bytes(buffer) -> bytes:
    data = buffer.data()
    try:
        return bytes(data)
    except (TypeError, ValueError):
        return bytes(data.constData())


def _decode_to_wav(source: Path, cache: Path, volume: float) -> bool:
    classes = _qt_multimedia_classes()
    if classes is None:
        return False
    try:
        decoder = classes[0]()
        state = {"chunks": [], "format": None}
        def on_buffer_ready():
            while decoder.bufferAvailable():
                buffer = decoder.read()
                state["format"] = buffer.format()
                state["chunks"].append(_audio_buffer_bytes(buffer))
        def on_finished():
            _qt_decoders.pop(str(source.resolve()), None)
            fmt = state["format"]
            if not fmt or not state["chunks"]:
                log.warning("音频解码没有产生 PCM: %s", source)
                return
            try:
                sample_format = fmt.sampleFormat()
                sample_width = {1: 1, 2: 2, 3: 4, 4: 4}.get(int(sample_format), 2)
                cache.parent.mkdir(parents=True, exist_ok=True)
                with wave.open(str(cache), "wb") as out:
                    out.setnchannels(fmt.channelCount())
                    out.setsampwidth(sample_width)
                    out.setframerate(fmt.sampleRate())
                    out.writeframes(b"".join(state["chunks"]))
            except Exception:
                log.exception("写入音效缓存失败: %s", cache)
        decoder.bufferReady.connect(on_buffer_ready)
        decoder.finished.connect(on_finished)
        error_signal = getattr(decoder, "error", None)
        if error_signal is not None and hasattr(error_signal, "connect"):
            error_signal.connect(lambda *_: log.warning("音频解码失败: %s", source))
        from PySide6.QtCore import QUrl
        decoder.setSource(QUrl.fromLocalFile(str(source)))
        _qt_decoders[str(source.resolve())] = decoder
        decoder.start()
        return True
    except Exception:
        log.exception("启动音频解码失败: %s", source)
        return False


def set_audio_volume(volume: float) -> float:
    """设置音频输出音量 (0.0..1.0)，返回 clamp 后的实际音量。"""
    try:
        v = float(volume)
    except (TypeError, ValueError):
        v = 1.0
    v = max(0.0, min(1.0, v))
    _ensure_qt_player()
    if _qt_audio is not None:
        try:
            _qt_audio.setVolume(v)
        except Exception:
            log.exception("设置音量失败")
    return v


def _play_with_qt(path: Path, volume: float = 1.0) -> bool:
    if path.suffix.lower() == ".wav":
        return _play_with_effect(path, volume)

    cache = _cache_path(path)
    if cache.is_file() and _play_with_effect(cache, volume):
        return True

    # The decoder is asynchronous. Keep the first click audible through the
    # pool, while the finished callback warms the low-latency effect cache.
    key = str(path.resolve())
    if key not in _qt_decoders and _decode_to_wav(path, cache, volume):
        _player_pool_play(path, volume)
        return True
    return _player_pool_play(path, volume)


def _effect_is_ready(effect: Any) -> bool:
    """判断 QSoundEffect 是否已完成异步加载；无 status 的测试替身视为就绪。"""
    status = getattr(effect, "status", None)
    if not callable(status):
        return True
    try:
        from PySide6.QtMultimedia import QSoundEffect

        return status() == QSoundEffect.Status.Ready
    except Exception:
        return True


def warm_click_sound_effects(
    pack: dict | None,
    data_dir: Path | None = None,
    limit: int = 8,
) -> None:
    """预创建点击音效对象，避免首次点击时初始化 QtMultimedia 造成卡顿。

    启动或切换音效包后调用：WAV/已缓存音频预创建 QSoundEffect 并等待加载完成；
    未缓存的压缩音频启动异步解码并等待缓存生成；同时预创建 QMediaPlayer 池。
    limit 用于限制自定义文件夹随机音效的预热数量，避免一次创建过多对象。
    """
    if not _qt_available():
        return
    try:
        from PySide6.QtCore import QCoreApplication

        _warm_player_pool()
        effects: list[Any] = []
        decoding: list[tuple[Path, Path, str]] = []
        candidates = resolve_click_sound_candidates(pack, data_dir)[:limit]
        for path in candidates:
            try:
                if path.suffix.lower() == ".wav":
                    effect = _effect_for(path)
                    if effect is not None:
                        effects.append(effect)
                else:
                    cache = _cache_path(path)
                    if cache.is_file():
                        effect = _effect_for(cache)
                        if effect is not None:
                            effects.append(effect)
                    else:
                        key = str(path.resolve())
                        if key not in _qt_decoders:
                            _decode_to_wav(path, cache, 0.0)
                        decoding.append((path, cache, key))
            except Exception:
                log.exception("预热点击音效失败: %s", path)

        # 等待压缩音频解码出 WAV 缓存（异步，QCoreApplication 泵事件完成回调）
        deadline = time.monotonic() + 2.0
        while decoding and time.monotonic() < deadline:
            remaining: list[tuple[Path, Path, str]] = []
            for path, cache, key in decoding:
                if cache.is_file():
                    effect = _effect_for(cache)
                    if effect is not None:
                        effects.append(effect)
                elif key in _qt_decoders:
                    remaining.append((path, cache, key))
                # key 不在 _qt_decoders 且没有缓存 = 解码失败/超时，跳过
            decoding = remaining
            if decoding:
                QCoreApplication.processEvents()
                time.sleep(0.005)

        # 等待 QSoundEffect 完成异步加载；未等待就播放会在事件循环里触发
        # 一次性加载/初始化，造成首次点击 Q 弹卡顿（实测可达数百 ms）。
        deadline = time.monotonic() + 2.0
        while effects and time.monotonic() < deadline:
            effects = [e for e in effects if not _effect_is_ready(e)]
            if effects:
                QCoreApplication.processEvents()
                time.sleep(0.005)
    except Exception:
        log.exception("点击音效预热失败")


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


def resolve_builtin_sound(sound_id: str) -> Path | None:
    """统一解析内置音频路径（支持源码目录与 PyInstaller sys._MEIPASS）。"""
    s_id = str(sound_id or "").strip()
    if s_id.startswith("builtin:"):
        s_id = s_id[len("builtin:"):]

    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    sounds_dir = root / "assets" / "sounds"

    # Agent 音效别名或直接文件名
    agent_map = {
        "agent-start": sounds_dir / "agent" / "start.wav",
        "agent-done": sounds_dir / "agent" / "done.wav",
        "agent-error": sounds_dir / "agent" / "error.wav",
    }
    if s_id in agent_map:
        target = agent_map[s_id]
        return target if target.is_file() else None

    # 点击音效内置包
    if s_id == "default":
        target = sounds_dir / "click.wav"
        return target if target.is_file() else None

    return None


def resolve_click_sound_candidates(pack: dict | None, data_dir: Path | None = None) -> list[Path]:
    """根据点击音效包配置解析候选音频文件列表。"""
    pack = pack if isinstance(pack, dict) else {}
    kind = str(pack.get("kind") or "builtin").strip().lower()
    pack_id = str(pack.get("id") or "default").strip()
    path_str = str(pack.get("path") or "").strip()

    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    sounds_dir = root / "assets" / "sounds"

    if kind == "builtin":
        if pack_id == "duck":
            duck_dir = sounds_dir / "duck"
            if duck_dir.is_dir():
                candidates = [
                    p for p in duck_dir.iterdir()
                    if p.is_file() and p.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS
                ]
                return sorted(candidates)
            return []
        # default builtin
        candidates = []
        if data_dir is not None:
            user_click = Path(data_dir) / "sounds" / "click.wav"
            if user_click.is_file():
                candidates.append(user_click)
        built_click = sounds_dir / "click.wav"
        if built_click.is_file():
            candidates.append(built_click)
        return candidates

    if kind == "file":
        if not path_str:
            return []
        p = Path(path_str).expanduser()
        if p.is_file() and p.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS:
            return [p]
        return []

    if kind == "folder":
        if not path_str:
            return []
        p = Path(path_str).expanduser()
        if p.is_dir():
            candidates = [
                f for f in p.iterdir()
                if f.is_file() and f.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS
            ]
            candidates.sort()
            return candidates[:128]
        return []

    return []


def choose_sound(candidates: Sequence[Path], rng: random.Random | None = None) -> Path | None:
    """从候选列表中挑选一个音频文件（支持传入 RNG 便于单测）。"""
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    picker = rng if rng is not None else random
    return picker.choice(candidates)


def play_sound(path: Path | str, volume: float = 1.0) -> bool:
    """统一音频播放入口。返回 True 表示已提交播放。"""
    try:
        target = Path(path)
    except (TypeError, ValueError):
        return False
    if not target.is_file():
        return False

    # WAV and decoded short effects use QSoundEffect; compressed sources use
    # the decoder/cache path and a small player pool while warming up.
    if _play_with_qt(target, volume):
        return True

    # 非 Windows 回退系统播放器
    if os.name != "nt":
        return _play_with_system_player(target)

    log.warning("QtMultimedia 不可用，音频播放跳过: %s", target)
    return False


def play_click_sound(path: Path | str, volume: float = 1.0) -> bool:
    """兼容旧 API 的薄包装别名。"""
    return play_sound(path, volume=volume)
