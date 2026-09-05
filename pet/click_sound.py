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

SUPPORTED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac", ".m4a"}


class ClickSoundPool:
    """模块级单例音效池：收编全部 QtMultimedia 音效对象与缓存状态（A14）。

    所有权形态：本模块的全部可变播放状态（播放器/音频输出/音效/解码器/
    播放器池/导入失败标志/类缓存/时长缓存/配对状态）收进这一个对象，
    模块级公开函数只是委托给模块底部单例 ``_pool`` 的薄壳，调用点零改动。

    线程归属（owning thread）：
    - QtMultimedia 对象（QMediaPlayer / QAudioOutput / QSoundEffect /
      QAudioDecoder）只能在 GUI 线程创建与操作。本池的所有播放入口
      （play_* / warm_* / set_audio_volume / play_sound）都必须由 GUI
      线程调用；
    - 唯一例外是惰性 import 探测（qt_available / qt_multimedia_classes），
      只读模块导入结果、不触碰 Qt 对象，任何线程调用均安全；
    - 异步解码回调（QAudioDecoder.bufferReady/finished/error）由 Qt 事件
      循环在 GUI 线程派发，回调只操作本池在 GUI 线程创建的字典，单线程
      约束不破坏。

    生命周期：
    - 进程级单例，应用存活期间不复位——播放器/音效必须持久化，否则
      Python GC 会在播放开始前回收对象；
    - close()/clear() 语义：停止全部音效与播放器对象、清空对象缓存/
      播放器池/解码器注册表、复位播放游标与时长/配对缓存。本池不拥有
      任何后台线程，close 无 join/等待语义；调用方须在 GUI 线程调用。
      生产路径不调用（生命周期=进程），主要供测试隔离与模块热重载场景。
    """

    _PLAYER_POOL_SIZE = 4

    def __init__(self) -> None:
        self._qt_player = None
        self._qt_audio = None
        self._qt_effects: dict[str, Any] = {}
        self._qt_decoders: dict[str, Any] = {}
        self._qt_player_pool: list[tuple[Any, Any]] = []
        self._qt_player_index = 0
        self._qt_import_failed = False
        self._qt_classes: tuple[Any, ...] | None = None
        self._wav_duration_cache: dict[str, float] = {}
        self._click_pair_state: dict[tuple[str, str], dict[str, Any]] = {}

    # ---------------- QtMultimedia 探测与对象创建（GUI 线程） ----------------

    def qt_available(self) -> bool:
        """惰性探测 QtMultimedia；失败只记一次日志。"""
        if self._qt_import_failed:
            return False
        try:
            from PySide6.QtMultimedia import QAudioDecoder, QAudioOutput, QMediaPlayer, QSoundEffect  # noqa: F401

            return True
        except Exception as exc:  # 打包遗漏/精简环境缺失时兜底
            self._qt_import_failed = True
            log.warning("QtMultimedia 不可用，音效将降级或静默失败: %s", exc)
            return False

    def qt_multimedia_classes(self):
        """Load multimedia classes lazily so headless/minimal installs can import this module."""
        if self._qt_classes is not None:
            return self._qt_classes
        if not self.qt_available():
            return None
        try:
            from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat, QAudioOutput, QMediaPlayer, QSoundEffect
            self._qt_classes = (QAudioDecoder, QAudioFormat, QAudioOutput, QMediaPlayer, QSoundEffect)
            return self._qt_classes
        except Exception as exc:
            self._qt_import_failed = True
            log.warning("QtMultimedia 音效类不可用: %s", exc)
            return None

    def ensure_qt_player(self):
        """返回模块级单例播放器；不可用返回 None。

        播放器必须持久化，否则 Python GC 会在播放开始前回收对象。
        """
        if self._qt_player is not None:
            return self._qt_player
        if not self.qt_available():
            return None
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

            self._qt_player = QMediaPlayer()
            self._qt_audio = QAudioOutput()
            self._qt_audio.setVolume(1.0)
            self._qt_player.setAudioOutput(self._qt_audio)
            return self._qt_player
        except Exception:
            log.exception("创建 QMediaPlayer 失败")
            return None

    def wav_duration(self, path: Path) -> float:
        """Read and cache duration from a decoded WAV header."""
        key = str(path.resolve())
        if key in self._wav_duration_cache:
            return self._wav_duration_cache[key]
        try:
            with wave.open(str(path), "rb") as source:
                duration = source.getnframes() / max(1, source.getframerate())
        except (OSError, EOFError, wave.Error):
            duration = 0.0
        self._wav_duration_cache[key] = duration
        return duration

    def effect_for(self, path: Path):
        classes = self.qt_multimedia_classes()
        if classes is None:
            return None
        key = str(path.resolve())
        effect = self._qt_effects.get(key)
        if effect is None:
            try:
                from PySide6.QtCore import QUrl
                effect = classes[4]()
                effect.setSource(QUrl.fromLocalFile(str(path)))
                self._qt_effects[key] = effect
            except Exception:
                log.exception("创建 QSoundEffect 失败: %s", path)
                return None
        return effect

    def play_with_effect(self, path: Path, volume: float) -> bool:
        effect = self.effect_for(path)
        if effect is None:
            return False
        try:
            # 显式 stop 再 play：QSoundEffect 在部分 QtMultimedia/FFmpeg
            # 后端上对同一实例的第二次 play 可能不重启（只响第一次）。
            # 先 stop 可把内部播放状态复位，再 play 保证连点/试听每次都能响。
            stop = getattr(effect, "stop", None)
            if callable(stop):
                stop()
            set_loop_count = getattr(effect, "setLoopCount", None)
            if callable(set_loop_count):
                set_loop_count(1)
            effect.setVolume(volume)
            effect.play()
            return True
        except Exception:
            log.exception("QSoundEffect 播放失败: %s", path)
            return False

    def warm_player_pool(self) -> None:
        """预创建 QMediaPlayer 池，避免首次点击时初始化 QtMultimedia 造成卡顿。"""
        classes = self.qt_multimedia_classes()
        if classes is None:
            return
        try:
            if not self._qt_player_pool:
                if self._qt_player is not None and self._qt_audio is not None:
                    self._qt_player_pool.append((self._qt_player, self._qt_audio))
                for _ in range(self._PLAYER_POOL_SIZE):
                    if len(self._qt_player_pool) >= self._PLAYER_POOL_SIZE:
                        break
                    player, audio = classes[3](), classes[2]()
                    player.setAudioOutput(audio)
                    self._qt_player_pool.append((player, audio))
        except Exception:
            log.exception("预创建 QMediaPlayer 池失败")

    def player_pool_play(self, path: Path, volume: float) -> bool:
        classes = self.qt_multimedia_classes()
        if classes is None:
            return False
        try:
            self.warm_player_pool()
            if not self._qt_player_pool:
                return False
            player, audio = self._qt_player_pool[self._qt_player_index % len(self._qt_player_pool)]
            self._qt_player_index += 1
            audio.setVolume(volume)
            player.stop()
            from PySide6.QtCore import QUrl
            player.setSource(QUrl.fromLocalFile(str(path)))
            player.play()
            return True
        except Exception:
            log.exception("QMediaPlayer 池播放失败: %s", path)
            return False

    def decode_to_wav(self, source: Path, cache: Path, volume: float) -> bool:
        classes = self.qt_multimedia_classes()
        if classes is None:
            return False
        try:
            decoder = classes[0]()
            # 统一输出 16-bit PCM：源是浮点（如 mp3float）时直接写 WAV 会被
            # 当 PCM32 播放成噪音，QSoundEffect 也只认整型 PCM
            try:
                requested = classes[1]()
                requested.setSampleRate(48000)
                requested.setChannelCount(2)
                requested.setSampleFormat(classes[1].SampleFormat.Int16)
                decoder.setAudioFormat(requested)
            except Exception:
                log.warning("设置解码输出格式失败，按源格式解码: %s", source)
            state = {"chunks": [], "format": None}
            def on_buffer_ready():
                while decoder.bufferAvailable():
                    buffer = decoder.read()
                    state["format"] = buffer.format()
                    state["chunks"].append(_audio_buffer_bytes(buffer))
            def on_finished():
                self._qt_decoders.pop(str(source.resolve()), None)
                fmt = state["format"]
                if not fmt or not state["chunks"]:
                    log.warning("音频解码没有产生 PCM: %s", source)
                    return
                try:
                    sample_format = fmt.sampleFormat()
                    # PySide6 返回 SampleFormat 枚举（不能直接 int()），mock/旧版返回 int
                    sample_width = {1: 1, 2: 2, 3: 4, 4: 4}.get(
                        int(getattr(sample_format, "value", sample_format)), 2)
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
            self._qt_decoders[str(source.resolve())] = decoder
            decoder.start()
            return True
        except Exception:
            log.exception("启动音频解码失败: %s", source)
            return False

    def play_with_qt(self, path: Path, volume: float = 1.0) -> bool:
        if path.suffix.lower() == ".wav":
            return self.play_with_effect(path, volume)

        cache = _cache_path(path)
        if cache.is_file() and self.play_with_effect(cache, volume):
            return True

        # The decoder is asynchronous. Keep the first click audible through the
        # pool, while the finished callback warms the low-latency effect cache.
        key = str(path.resolve())
        if key not in self._qt_decoders and self.decode_to_wav(path, cache, volume):
            self.player_pool_play(path, volume)
            return True
        return self.player_pool_play(path, volume)

    def set_audio_volume(self, volume: float) -> float:
        """设置音频输出音量 (0.0..1.0)，返回 clamp 后的实际音量。"""
        try:
            v = float(volume)
        except (TypeError, ValueError):
            v = 1.0
        v = max(0.0, min(1.0, v))
        self.ensure_qt_player()
        if self._qt_audio is not None:
            try:
                self._qt_audio.setVolume(v)
            except Exception:
                log.exception("设置音量失败")
        return v

    def warm_click_sound_effects(
        self,
        pack: dict | None,
        data_dir: Path | None = None,
        limit: int = 8,
    ) -> None:
        """预创建点击音效对象，避免首次点击时初始化 QtMultimedia 造成卡顿。

        启动或切换音效包后调用：WAV/已缓存音频预创建 QSoundEffect 并等待加载完成；
        未缓存的压缩音频启动异步解码并等待缓存生成；同时预创建 QMediaPlayer 池。
        limit 用于限制自定义文件夹随机音效的预热数量，避免一次创建过多对象。
        """
        if not self.qt_available():
            return
        try:
            from PySide6.QtCore import QCoreApplication

            self.warm_player_pool()
            effects: list[Any] = []
            decoding: list[tuple[Path, Path, str]] = []
            candidates = resolve_click_sound_candidates(pack, data_dir)[:limit]
            for path in candidates:
                try:
                    if path.suffix.lower() == ".wav":
                        effect = self.effect_for(path)
                        if effect is not None:
                            effects.append(effect)
                    else:
                        cache = _cache_path(path)
                        if cache.is_file():
                            effect = self.effect_for(cache)
                            if effect is not None:
                                effects.append(effect)
                        else:
                            key = str(path.resolve())
                            if key not in self._qt_decoders:
                                self.decode_to_wav(path, cache, 0.0)
                            decoding.append((path, cache, key))
                except Exception:
                    log.exception("预热点击音效失败: %s", path)

            # 等待压缩音频解码出 WAV 缓存（异步，QCoreApplication 泵事件完成回调）
            deadline = time.monotonic() + 2.0
            while decoding and time.monotonic() < deadline:
                remaining: list[tuple[Path, Path, str]] = []
                for path, cache, key in decoding:
                    if cache.is_file():
                        effect = self.effect_for(cache)
                        if effect is not None:
                            effects.append(effect)
                    elif key in self._qt_decoders:
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

    def play_press_sound(self, pair: tuple[Path, Path], volume: float = 1.0) -> bool:
        """Restart press and cancel any release currently playing."""
        press, release = pair
        release_effect = self.effect_for(release)
        if release_effect is not None:
            try:
                release_effect.stop()
                release_effect.setLoopCount(1)
                release_effect.setVolume(volume)
            except Exception:
                pass
        state = self._click_pair_state.setdefault((str(press), str(release)), {})
        state["generation"] = int(state.get("generation", 0)) + 1
        state["press_started_at"] = time.monotonic()
        state["press_played"] = True
        state["release_scheduled"] = False
        state["release_played"] = False
        return self.play_sound(press, volume=volume)

    def play_release_sound(
        self,
        pair: tuple[Path, Path], volume: float = 1.0, press_started_at: float | None = None,
    ) -> bool:
        """Play release immediately or at the last 100ms of the press sound."""
        press, release = pair
        started = press_started_at
        if started is None:
            started = self._click_pair_state.get((str(press), str(release)), {}).get("press_started_at")
        delay_ms = 0
        if started is not None:
            remaining = self.wav_duration(press) - (time.monotonic() - float(started))
            delay_ms = max(0, int(round((remaining - 0.100) * 1000)))
        state = self._click_pair_state.setdefault((str(press), str(release)), {})
        if state.get("release_scheduled") or state.get("release_played"):
            return False
        state["release_scheduled"] = bool(delay_ms)
        generation = int(state.get("generation", 0))
        def play() -> None:
            if int(state.get("generation", 0)) == generation:
                state["release_scheduled"] = False
                state["release_played"] = True
                self.play_sound(release, volume=volume)
        if delay_ms:
            try:
                from PySide6.QtCore import QTimer
            except Exception:
                return self.play_sound(release, volume=volume)
            QTimer.singleShot(delay_ms, play)
            return True
        play()
        return True

    def play_sound(self, path: Path | str, volume: float = 1.0) -> bool:
        """统一音频播放入口。返回 True 表示已提交播放。"""
        try:
            target = Path(path)
        except (TypeError, ValueError):
            return False
        if not target.is_file():
            return False
        # 取证日志：任何宠物侧发声都必须留痕（排查"莫名音效"用）
        try:
            import traceback
            caller = ""
            for frame in reversed(traceback.extract_stack()[-6:-1]):
                if "click_sound.py" not in frame.filename:
                    caller = f"{Path(frame.filename).name}:{frame.lineno}"
                    break
            log.info("播放音效 path=%s vol=%.2f caller=%s", target, volume, caller)
        except Exception:
            pass

        # WAV and decoded short effects use QSoundEffect; compressed sources use
        # the decoder/cache path and a small player pool while warming up.
        if self.play_with_qt(target, volume):
            return True

        # 非 Windows 回退系统播放器
        if os.name != "nt":
            return _play_with_system_player(target)

        log.warning("QtMultimedia 不可用，音频播放跳过: %s", target)
        return False

    def play_click_sound(self, path: Path | str, volume: float = 1.0) -> bool:
        """兼容旧 API 的薄包装别名。"""
        return self.play_sound(path, volume=volume)

    # ---------------- 生命周期（GUI 线程） ----------------

    def clear(self) -> None:
        """释放全部 Qt 音效对象并复位池状态（GUI 线程调用）。

        停止并丢弃全部 QSoundEffect / QMediaPlayer / QAudioDecoder 引用，
        清空音效缓存、解码器注册表与播放器池，复位播放游标与时长/配对
        缓存。保留 _qt_classes / _qt_import_failed 的惰性探测结果（不清
        除导入失败记忆，维持"失败只记一次日志"语义）。
        """
        for effect in list(self._qt_effects.values()):
            try:
                effect.stop()
            except Exception:
                pass
        for decoder in list(self._qt_decoders.values()):
            try:
                decoder.stop()
            except Exception:
                pass
        for player, _audio in list(self._qt_player_pool):
            try:
                player.stop()
            except Exception:
                pass
        self._qt_effects.clear()
        self._qt_decoders.clear()
        self._qt_player_pool.clear()
        self._qt_player = None
        self._qt_audio = None
        self._qt_player_index = 0
        self._wav_duration_cache.clear()
        self._click_pair_state.clear()

    def close(self) -> None:
        """close 语义 = clear（本池不拥有后台线程，无 join/等待）。

        与 clear() 完全等价，仅为对称的生命周期命名；须在 GUI 线程调用。
        """
        self.clear()


# 模块级单例：全部音效状态与生命周期的唯一所有权对象。
_pool = ClickSoundPool()


# ---------------------------------------------------------------------------
# 模块级函数：公开 API 为薄壳（调用点零改动）；私有 helper 亦保留为薄壳，
# 以便测试沿用「替换模块级名称」的桩点（如 _qt_multimedia_classes）。
# ---------------------------------------------------------------------------

def _qt_available() -> bool:
    """惰性探测 QtMultimedia；失败只记一次日志。"""
    return _pool.qt_available()


def _ensure_qt_player():
    """返回模块级单例播放器；不可用返回 None。"""
    return _pool.ensure_qt_player()


def _qt_multimedia_classes():
    """Load multimedia classes lazily so headless/minimal installs can import this module."""
    return _pool.qt_multimedia_classes()


def _wav_duration(path: Path) -> float:
    """Read and cache duration from a decoded WAV header."""
    return _pool.wav_duration(path)


def _effect_for(path: Path):
    return _pool.effect_for(path)


def _play_with_effect(path: Path, volume: float) -> bool:
    return _pool.play_with_effect(path, volume)


def _warm_player_pool() -> None:
    """预创建 QMediaPlayer 池，避免首次点击时初始化 QtMultimedia 造成卡顿。"""
    _pool.warm_player_pool()


def _player_pool_play(path: Path, volume: float) -> bool:
    return _pool.player_pool_play(path, volume)


def _decode_to_wav(source: Path, cache: Path, volume: float) -> bool:
    return _pool.decode_to_wav(source, cache, volume)


def _play_with_qt(path: Path, volume: float = 1.0) -> bool:
    return _pool.play_with_qt(path, volume)


def set_audio_volume(volume: float) -> float:
    """设置音频输出音量 (0.0..1.0)，返回 clamp 后的实际音量。"""
    return _pool.set_audio_volume(volume)


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
    _pool.warm_click_sound_effects(pack, data_dir, limit)


def play_press_sound(pair: tuple[Path, Path], volume: float = 1.0) -> bool:
    """Restart press and cancel any release currently playing."""
    return _pool.play_press_sound(pair, volume)


def play_release_sound(
    pair: tuple[Path, Path], volume: float = 1.0, press_started_at: float | None = None,
) -> bool:
    """Play release immediately or at the last 100ms of the press sound."""
    return _pool.play_release_sound(pair, volume, press_started_at)


def play_sound(path: Path | str, volume: float = 1.0) -> bool:
    """统一音频播放入口。返回 True 表示已提交播放。"""
    return _pool.play_sound(path, volume)


def play_click_sound(path: Path | str, volume: float = 1.0) -> bool:
    """兼容旧 API 的薄包装别名。"""
    return _pool.play_click_sound(path, volume)


# ---------------------------------------------------------------------------
# 无状态的纯 helper 与解析 API（不触碰池状态，保持模块级）。
# ---------------------------------------------------------------------------

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


def _audio_buffer_bytes(buffer) -> bytes:
    data = buffer.data()
    try:
        return bytes(data)
    except (TypeError, ValueError):
        return bytes(data.constData())


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


def resolve_click_sound_pair(pack: dict | None, data_dir: Path | None = None) -> tuple[Path, Path] | None:
    """Resolve the duck press/release pair, preferring decoded WAV caches."""
    pack = pack if isinstance(pack, dict) else {}
    if str(pack.get("kind") or "builtin").strip().lower() != "builtin":
        return None
    if str(pack.get("id") or "default").strip() != "duck":
        return None
    candidates = {path.stem.lower(): path for path in resolve_click_sound_candidates(pack, data_dir)}
    press, release = candidates.get("ya1"), candidates.get("ya2")
    if press is None or release is None:
        return None
    def cached(path: Path) -> Path:
        if path.suffix.lower() == ".wav":
            return path
        cache = _cache_path(path)
        if cache.is_file():
            return cache
        _decode_to_wav(path, cache, 0.0)
        return cache if cache.is_file() else path
    return cached(press), cached(release)


def choose_sound(candidates: Sequence[Path], rng: random.Random | None = None) -> Path | None:
    """从候选列表中挑选一个音频文件（支持传入 RNG 便于单测）。"""
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    picker = rng if rng is not None else random
    return picker.choice(candidates)
