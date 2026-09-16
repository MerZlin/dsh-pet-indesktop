# -*- coding: utf-8 -*-
"""语音报时：调度服务 + edge-tts 合成 + QtMultimedia 播放（GUI 线程）。

架构（参考 pet/todo_reminder.py 与 pet/click_sound.py）：
- 模块顶层不 import Qt：QObject / QTimer / QtMultimedia 均在方法内惰性导入，
  保证纯逻辑层（voice_chime.py）可在无 Qt 环境测试；
- VoiceChimeService 不继承 QObject，持有无主 QTimer，由 AppShell 持有引用
  保证生命周期（同 TodoReminderService）；
- 报时调度 tick（20s）→ voice_chime.is_chime_minute 判定 + 槽位盖戳幂等；
- 合成在后台线程跑 edge_tts（asyncio），完成后经 QObject 信号（queued）
  桥回 GUI 线程，用 QMediaPlayer + QAudioOutput 播放；
- 音频缓存于 config.dir/voice_chime_cache，按“文本+音色+语速+音调”哈希
  去重，同句不重复合成。
"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime
from pathlib import Path

from .voice_chime import (
    build_chime_sentence,
    cache_key,
    chime_slot,
    edge_pitch_arg,
    edge_rate_arg,
    normalize_chime_config,
)

logger = logging.getLogger(__name__)

# 尝试导入 edge_tts：缺失时降级为“仅气泡提示”（用户可后续安装）。
try:  # pragma: no cover - 环境探测
    import edge_tts  # noqa: F401

    _EDGE_TTS_AVAILABLE = True
except Exception:  # pragma: no cover
    _EDGE_TTS_AVAILABLE = False


class _TTSWorker(threading.Thread):
    """后台线程：edge-tts 合成 mp3 后经回调返回。

    线程不直接碰 Qt 对象；完成后把结果交给 owner 提供的回调（owner 在
    GUI 线程，回调触发 queued 信号桥接）。
    """

    def __init__(self, text: str, voice: str, rate: str, pitch: str,
                 out_path: Path, on_done) -> None:
        super().__init__(daemon=True)
        self._text = text
        self._voice = voice
        self._rate = rate
        self._pitch = pitch
        self._out_path = out_path
        self._on_done = on_done

    def run(self) -> None:  # noqa: D102
        try:
            asyncio.run(self._synthesize())
        except Exception as exc:  # noqa: BLE001
            logger.exception("edge-tts 合成失败")
            self._on_done(str(self._out_path), self._text, f"{type(exc).__name__}: {exc}")
            return
        self._on_done(str(self._out_path), self._text, "")

    async def _synthesize(self) -> None:
        communicate = edge_tts.Communicate(
            self._text,
            self._voice,
            rate=self._rate,
            pitch=self._pitch,
        )
        await communicate.save(str(self._out_path))


class _AudioBridge:
    """QObject 信号桥：后台线程合成完成 → queued 信号回 GUI 线程。

    信号对象在 GUI 线程创建（_fire 由 GUI 线程调用），PySide6 依其线程亲和
    把跨线程 emit 排到 GUI 线程执行——已在实机测过：后台 emit、槽在 GUI
    线程运行（不要把这里改成普通 Python 回调绕开信号）。
    """

    def __init__(self) -> None:
        from PySide6.QtCore import QObject, Signal

        class _Signals(QObject):
            synthesized = Signal(str, str, str)  # path, text, error

        self.signals = _Signals()

    def on_synthesized(self, path: str, text: str, error: str) -> None:
        # 从后台线程调用：Qt 自动 queued 到 GUI 线程
        self.signals.synthesized.emit(path, text, error)


class VoiceChimeService:
    """语音报时服务（AppShell 持有，GUI 线程）。

    配置键（config.py 平铺顶层键）：
      voice_chime_enabled / voice_chime_schedule / voice_chime_custom_times /
      voice_chime_voice / voice_chime_rate / voice_chime_pitch / voice_chime_volume
    """

    TICK_INTERVAL_MS = 20_000
    BUBBLE_DURATION_MS = 8000
    MAX_CACHE_FILES = 200

    def __init__(self, app) -> None:
        from PySide6.QtCore import QTimer

        self._app = app
        config = getattr(app, "config", None)
        self._cache_dir = Path(getattr(config, "dir", Path("."))) / "voice_chime_cache"
        # 契约形状（enabled/schedule/custom_times/voice/rate/pitch/volume）：
        # 与 _on_tick/_fire 读取的键一致，勿用带 voice_chime_ 前缀的镜像默认值。
        self._cfg: dict = normalize_chime_config(None)
        self._last_slot: str | None = None
        self._busy = False
        # 停止作废标记：stop()（关闭开关/退出）后，飞行中的合成结果不再回放
        self._stopped = False
        self._bridge: _AudioBridge | None = None
        self._player = None
        self._audio_out = None
        self._timer = QTimer()
        self._timer.setInterval(self.TICK_INTERVAL_MS)
        self._timer.timeout.connect(self._on_tick)

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        self.apply_config()
        self._on_tick()
        self._timer.start()

    def stop(self) -> None:
        """停止调度；飞行中的合成结果一并作废（见 _on_synthesized）。"""
        self._stopped = True
        self._timer.stop()

    def is_running(self) -> bool:
        """调度 tick 是否在跑（AppShell 的懒启停门控据此决定重启还是只刷配置）。"""
        return bool(self._timer.isActive())

    def apply_config(self) -> None:
        """重读配置（设置保存、右键开关后调用）。

        统一走纯逻辑层 normalize_chime_config：清洗/钳制规则只有一处实现，
        且 config.json 被手改成非法值时回落默认值而不是抛异常（本方法在
        start()（开机）与「立即报时」路径上执行，抛异常等于语音报时在启动期
        直接失败）。
        """
        self._cfg = normalize_chime_config(getattr(self._app, "config", None))
        self._prune_cache()

    # ------------------------------------------------------------ 对外入口
    def say_now(self, text: str = "") -> None:
        """手动报时（右键菜单「立即报时」/ 设置页试听共用）。

        text 留空时按当前时间组装“报时文本 + 随机台词”；手动触发不占用
        调度槽位（与自动报时允许同分钟并存，由用户主动发起）。
        """
        self.apply_config()
        now = datetime.now()
        sentence = text.strip() or build_chime_sentence(now, self._cfg)
        self._fire(sentence, now)

    # ------------------------------------------------------------ 调度
    def _on_tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        if not self._cfg.get("enabled"):
            return
        slot = chime_slot(now, self._cfg)
        if not slot or slot == self._last_slot:
            return
        self._last_slot = slot
        self._fire(build_chime_sentence(now, self._cfg), now)

    def _fire(self, sentence: str, now: datetime) -> None:
        if not _EDGE_TTS_AVAILABLE:
            self._notify_missing_tts()
            return
        if self._busy:
            logger.info("语音报时仍在合成中，跳过本次：%s", sentence[:20])
            return
        voice = self._cfg["voice"]
        key = cache_key(sentence, {
            "voice": voice,
            "rate": self._cfg["rate"],
            "pitch": self._cfg["pitch"],
        })
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        out_path = self._cache_dir / f"{key}.mp3"
        if not out_path.exists():
            self._busy = True
            # 本次合成有效：清掉上一次 stop() 留下的作废标记，
            # 否则新起的合成结果会被当成"迟到结果"丢弃。
            self._stopped = False
            if self._bridge is None:
                self._bridge = _AudioBridge()
                self._bridge.signals.synthesized.connect(self._on_synthesized)
            worker = _TTSWorker(
                sentence,
                voice,
                edge_rate_arg(self._cfg["rate"]),
                edge_pitch_arg(self._cfg["pitch"]),
                out_path,
                self._bridge.on_synthesized,
            )
            worker.start()
        else:
            self._play_and_bubble(str(out_path), sentence)

    # ------------------------------------------------------------ 播放
    def _on_synthesized(self, path: str, text: str, error: str) -> None:
        self._busy = False
        if self._stopped:
            # stop()（关闭开关 / 应用退出）之后才回来的结果：不再回放/气泡——
            # 否则「关掉语音报时之后又响一声」，退出路径上还可能触碰正在析构的窗口。
            logger.info("语音报时已停止，丢弃迟到的合成结果：%s", Path(path).name)
            return
        if error:
            logger.warning("语音报时合成失败：%s", error)
            self._bubble(f"语音合成失败（{error[:40]}…）")
            return
        self._play_and_bubble(path, text)

    def _play_and_bubble(self, path: str, text: str) -> None:
        if not self._ensure_player():
            self._bubble(f"播放器不可用，音频已缓存：{Path(path).name}")
            return
        try:
            from PySide6.QtCore import QUrl

            self._audio_out.setVolume(self._cfg["volume"] / 100.0)
            self._player.setSource(QUrl.fromLocalFile(path))
            self._player.play()
        except Exception:
            logger.exception("语音报时播放失败：%s", path)
            self._bubble("语音播放失败（设备/解码器异常）")
            return
        self._bubble(text)

    def _ensure_player(self) -> bool:
        if self._player is not None:
            return True
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

            self._player = QMediaPlayer()
            self._audio_out = QAudioOutput()
            self._player.setAudioOutput(self._audio_out)
            return True
        except Exception:
            logger.exception("创建 QMediaPlayer 失败（检查 QtMultimedia ffmpegmediaplugin）")
            return False

    # ------------------------------------------------------------ 提示
    def _bubble(self, text: str) -> None:
        app = self._app
        win = getattr(app, "win", None)
        if win is not None and win.isVisible():
            try:
                win.show_bubble(text, duration_ms=self.BUBBLE_DURATION_MS)
                return
            except Exception:
                logger.exception("气泡展示失败")
        notify = getattr(app, "system_notify", None)
        if callable(notify):
            try:
                notify("语音报时", text)
            except Exception:
                logger.exception("桌面通知失败")

    def _notify_missing_tts(self) -> None:
        msg = "语音报时需要 edge-tts 库：请运行 pip install edge-tts 后重试"
        logger.warning(msg)
        self._bubble(msg)

    # ------------------------------------------------------------ 缓存维护
    def _prune_cache(self) -> None:
        """缓存目录超过上限时按修改时间清理最旧文件（仅限本服务缓存目录）。"""
        try:
            if not self._cache_dir.is_dir():
                return
            files = sorted(
                (p for p in self._cache_dir.glob("*.mp3")),
                key=lambda p: p.stat().st_mtime,
            )
            for old in files[: max(0, len(files) - self.MAX_CACHE_FILES)]:
                try:
                    old.unlink(missing_ok=True)
                except OSError:
                    logger.debug("清理语音缓存失败：%s", old)
        except OSError:
            logger.debug("语音缓存目录不可访问：%s", self._cache_dir)
