# -*- coding: utf-8 -*-
"""语音报时：调度服务 + edge-tts 合成 + QtMultimedia 播放（GUI 线程）。

架构（参考 pet/todo_reminder.py 与 pet/click_sound.py）：
- 模块顶层不 import Qt：QObject / QTimer / QtMultimedia 均在方法内惰性导入，
  保证纯逻辑层（voice_chime.py）可在无 Qt 环境测试；
- VoiceChimeService 不继承 QObject，持有无主 QTimer，由 AppShell 持有引用
  保证生命周期（同 TodoReminderService）；
- 报时调度 tick（20s）→ voice_chime.is_chime_minute 判定 + 槽位盖戳幂等；
- 文本双轨：语音用中文口播文本（build_chime_sentence，同时作为合成输入与
  缓存键），气泡用阿拉伯数字文本（build_bubble_sentence），两者解耦；
- 合成在后台线程跑 edge_tts（asyncio），完成后经 QObject 信号（queued）
  桥回 GUI 线程，用 QMediaPlayer + QAudioOutput 播放；
- 音频缓存于 config.dir/voice_chime_cache，按“文本+音色+语速+音调”哈希
  去重，同句不重复合成；
- 预合成降延迟：距下一报时点 ≤ 60s 时提前在后台线程合成该次报时音频并
  写缓存，到点直接播缓存实现近零延迟；预合成未及时完成则回退即时合成。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from .voice_chime import (
    build_bubble_sentence,
    build_chime_sentence,
    cache_key,
    chime_slot,
    edge_pitch_arg,
    edge_rate_arg,
    next_chime_in_seconds,
    normalize_chime_config,
)

logger = logging.getLogger(__name__)

# 缓存清理最小间隔（秒）：报时/试听/设置保存都会触发 apply_config，而缓存目录
# 全量 glob+stat 属纯 IO 开销；节流到 5 分钟一次即可满足 200 文件上限的收敛。
_PRUNE_MIN_INTERVAL_S = 300.0
# 合成回调超时（秒）：worker 线程若异常未回调，_busy 会永久置位导致报时静默，
# 超过该时限在调度 tick 中复位（正常合成 3-8s 完成，45s 余量充足）。
_SYNTH_TIMEOUT_S = 45.0

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

    def __init__(self, text: str, voice: str, rate: str, pitch: str, out_path: Path, on_done) -> None:
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

    _Signals 实例本身即 QObject，随服务存活到进程退出；历史遗留的无主
    QObject（_obj）与其 destroy() 从未被调用，删除以免误导生命周期判断。

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
      voice_chime_voice / voice_chime_rate / voice_chime_pitch / voice_chime_volume /
      voice_chime_show_bubble / voice_chime_show_quote
    """

    TICK_INTERVAL_MS = 20_000
    BUBBLE_DURATION_MS = 8000
    MAX_CACHE_FILES = 200
    # 预合成窗口：距下一报时点 ≤ 60s 时提前在后台合成并写缓存，到点直接播。
    PRECACHE_WINDOW_S = 60

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
        # monotonic 时间戳：_busy_since 用于合成卡死自愈，_last_prune_at 用于
        # 缓存清理节流（两者都只做"多久没做过了"的判断，不参与业务语义）。
        self._busy_since = 0.0
        self._last_prune_at = 0.0
        self._synthesis_role = "play"  # play | precache：区分合成完成回调的用途
        # 预合成状态：目标槽位 / 语音文本 / 气泡文本 / 缓存文件（到点命中即零延迟播放）
        self._precache_slot: str | None = None
        self._precache_text: str | None = None
        self._precache_bubble: str | None = None
        self._precache_path: str | None = None
        # 即时合成中的气泡文本：合成完成回调里与语音文本配对展示（二者解耦）
        self._pending_bubble: str | None = None
        # 停止作废标记：stop()（关闭开关/退出）后，飞行中的合成结果不再回放
        self._stopped = False
        # 外部播报（节日提醒）经 speak() 复用本服务的音频通道：通道忙时进深度 1
        # 待播队列，当前一段播完再播——不打断、不叠音。None 表示无待播。
        self._pending_speech: str | None = None
        # 媒体终止态枚举；_ensure_player 成功时填充，未创建播放器时为空元组。
        self._terminal_statuses: tuple = ()
        # 让位钩子：由 AppShell 注入 Callable[[str], bool]，入参是本分钟的报时槽位，
        # 返回 True 表示该槽位让位给节日提醒（报时本分钟不说）。None = 无人让位。
        # 用注入而不是让报时反查节日服务，是为了保持本模块对外零依赖。
        self.yield_slot = None
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
        # 待播队列同样作废：关掉开关/退出后不该再补播一条排队的语音。
        self._pending_speech = None
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
        config = getattr(self._app, "config", None)
        self._cfg = normalize_chime_config(config if config is not None else {})
        # 配置变更后预合成缓存可能失效（音色/语速/台词开关/时间点变化），丢弃。
        self._precache_slot = None
        self._precache_text = None
        self._precache_bubble = None
        self._precache_path = None
        self._prune_cache()

    # ------------------------------------------------------------ 对外入口
    def say_now(self, text: str = "") -> None:
        """手动报时（右键菜单「立即报时」/ 设置页试听共用）。

        text 留空时按当前时间组装“报时文本 + 台词/歌词”（台词按 8 小时周期分批
        轮换）；气泡用阿拉伯数字文本、语音保持中文数字口播，二者解耦。手动触发
        不占用调度槽位（与自动报时允许同分钟并存，由用户主动发起）。
        """
        self.apply_config()
        now = datetime.now()
        stripped = text.strip()
        if stripped:
            self._fire(stripped, now, stripped)
            return
        self._fire(build_chime_sentence(now, self._cfg), now, build_bubble_sentence(now, self._cfg))

    def speak(self, text: str, *, log_tag: str = "外部播报") -> bool:
        """向本服务（进程内唯一的音频通道）提交一段**纯语音**播报，不含气泡。

        供节日提醒等功能复用：合成、缓存、播放与报时**共用同一套**，因此结构上
        不可能与报时叠音。通道忙（合成中或正在出声）时不打断也不丢弃，进深度 1
        的待播队列，当前一段播完后自动播；队列已有待播时后来的覆盖前面的
        （只留最新一条，避免堆积出已过期的内容）。

        气泡刻意不在这里做：节日提醒有自己的一套气泡（含 show_quote 开关与更长
        的展示时长），若再走报时的 ``_bubble`` 会被 ``voice_chime_show_bubble``
        二次影响，也会出现"两个气泡"。

        返回 False 仅表示文本为空；edge-tts 不可用时只记日志、不出声（调用方的
        气泡照常）。
        """
        stripped = (text or "").strip()
        if not stripped:
            return False
        if self._busy or self._player_busy():
            self._pending_speech = stripped
            logger.info("%s：音频通道忙，已排队待播", log_tag)
            return True
        self._fire(stripped, datetime.now(), None, show_bubble=False, role="speak")
        return True

    # ------------------------------------------------------------ 调度
    def _on_tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        if not self._cfg.get("enabled"):
            return
        # 兜底自愈：合成线程若异常未回调，_busy 会永久置位（报时静默无声）。
        # tick 是 20s 周期的常驻入口，超时即复位，让后续报时/预合成恢复可用。
        self._release_stale_busy()
        slot = chime_slot(now, self._cfg)
        if slot:
            if slot == self._last_slot:
                return
            # 让位检查必须在盖戳之前，且让位同样要消费该槽位——否则本分钟后续
            # tick（20s 一次）会反复询问，并在让位条件变化时补报，产生意外发声。
            # 用 getattr 取钩子：部分构造的服务对象（测试替身走 object.__new__）
            # 没有该属性，不该因此让 tick 抛异常。
            yield_slot = getattr(self, "yield_slot", None)
            if callable(yield_slot) and yield_slot(slot):
                self._last_slot = slot
                logger.info("报时让位给节日提醒：%s", slot)
                return
            self._last_slot = slot
            precache_bubble = self._precache_bubble  # 消费前取用（consume 会清空预合成状态）
            sentence = self._consume_precache(slot)
            bubble = precache_bubble if sentence is not None else None
            self._precache_bubble = None
            if sentence is None:
                sentence = build_chime_sentence(now, self._cfg)
                bubble = build_bubble_sentence(now, self._cfg)
            self._fire(sentence, now, bubble)
            return
        # 未命中：尝试为下一报时点预合成（距下一报时点 ≤ 窗口秒数）。
        self._maybe_precache(now)

    def _maybe_precache(self, now: datetime) -> None:
        """距下一报时点 ≤ 60s 时，提前在后台合成该次报时音频并写缓存。"""
        # 过期清理：预合成目标槽位已过（如配置变更/服务长时间暂停后恢复）。
        if self._precache_slot is not None:
            slot_time = self._precache_slot[:16]  # YYYY-MM-DDTHH:MM
            if now.strftime("%Y-%m-%dT%H:%M") > slot_time:
                self._precache_slot = None
                self._precache_text = None
                self._precache_bubble = None
                self._precache_path = None
        next_secs = next_chime_in_seconds(now, self._cfg)
        if next_secs > self.PRECACHE_WINDOW_S:
            return
        next_at = now + timedelta(seconds=next_secs)
        next_slot = chime_slot(next_at, self._cfg)
        if not next_slot or next_slot == self._last_slot or next_slot == self._precache_slot:
            return
        if self._busy:
            return  # 合成中，下个 tick（20s 内）再试，窗口 60s 足够
        if not _EDGE_TTS_AVAILABLE:
            return
        sentence = build_chime_sentence(next_at, self._cfg)
        bubble = build_bubble_sentence(next_at, self._cfg)
        key = cache_key(
            sentence,
            {
                "voice": self._cfg["voice"],
                "rate": self._cfg["rate"],
                "pitch": self._cfg["pitch"],
            },
        )
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        out_path = self._cache_dir / f"{key}.mp3"
        # 先占位：即便本次合成未在报时点前完成，到点也会回退即时合成。
        self._precache_slot = next_slot
        self._precache_text = sentence
        self._precache_bubble = bubble
        self._precache_path = str(out_path) if out_path.exists() else None
        if out_path.exists():
            logger.info("预合成命中已有缓存：%s", out_path.name)
            return
        self._busy = True
        self._busy_since = time.monotonic()
        self._synthesis_role = "precache"
        if self._bridge is None:
            self._bridge = _AudioBridge()
            self._bridge.signals.synthesized.connect(self._on_synthesized)
        worker = _TTSWorker(
            sentence,
            self._cfg["voice"],
            edge_rate_arg(self._cfg["rate"]),
            edge_pitch_arg(self._cfg["pitch"]),
            out_path,
            self._bridge.on_synthesized,
        )
        logger.info("预合成下一报时音频（%ds 后）：%s", next_secs, out_path.name)
        worker.start()

    def _consume_precache(self, slot: str) -> str | None:
        """到点取用预合成文本；未完成/文件丢失时清空并回退即时合成。

        无论命中与否都清空整组预合成状态（含气泡文本）：气泡文本由调用方
        ``_on_tick`` 在消费前取用，避免残留串到下一次报时。
        """
        if self._precache_slot == slot and self._precache_path:
            path = Path(self._precache_path)
            if path.is_file():
                text = self._precache_text
                self._precache_slot = None
                self._precache_text = None
                self._precache_bubble = None
                self._precache_path = None
                logger.info("报时命中预合成缓存：%s", path.name)
                return text
        self._precache_slot = None
        self._precache_text = None
        self._precache_bubble = None
        self._precache_path = None
        return None

    def _fire(self, sentence: str, now: datetime, bubble_text: str | None = None,
              *, show_bubble: bool = True, role: str = "play") -> None:
        """播报一次。

        语音用口播文本 ``sentence``（同时作为合成输入与缓存键）；气泡用
        ``bubble_text``（阿拉伯数字为主，缺省按 ``now`` 现算），二者解耦。

        ``show_bubble=False`` / ``role="speak"`` 是给外部播报（节日提醒）用的：
        只出声、不出气泡（气泡由调用方自己管），合成完成回调据此走 ``_play_only``。
        """
        if not _EDGE_TTS_AVAILABLE:
            self._notify_missing_tts()
            return
        bubble = bubble_text if bubble_text is not None else build_bubble_sentence(now, self._cfg)
        voice = self._cfg["voice"]
        key = cache_key(
            sentence,
            {
                "voice": voice,
                "rate": self._cfg["rate"],
                "pitch": self._cfg["pitch"],
            },
        )
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        out_path = self._cache_dir / f"{key}.mp3"
        # 缓存命中（含预合成已完成）直接播放，零网络延迟；合成中不阻塞缓存播放。
        if out_path.exists():
            if show_bubble:
                self._play_and_bubble(str(out_path), sentence, bubble)
            else:
                self._play_only(str(out_path))
            return
        if self._busy:
            logger.info("语音报时仍在合成中，跳过本次：%s", sentence[:20])
            return
        self._busy = True
        self._busy_since = time.monotonic()
        self._synthesis_role = role
        self._pending_bubble = bubble if show_bubble else None
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

    # ------------------------------------------------------------ 播放
    def _on_synthesized(self, path: str, text: str, error: str) -> None:
        role = self._synthesis_role
        self._synthesis_role = "play"
        self._busy = False
        if self._stopped:
            # stop()（关闭开关 / 应用退出）之后才回来的结果：不再回放/气泡——
            # 否则「关掉语音报时之后又响一声」，退出路径上还可能触碰正在析构的窗口。
            logger.info("语音报时已停止，丢弃迟到的合成结果：%s", Path(path).name)
            return
        if error:
            if role == "precache":
                # 预合成失败：丢弃占位，到点走即时合成回退。
                self._precache_slot = None
                self._precache_text = None
                self._precache_bubble = None
                self._precache_path = None
                logger.warning("预合成失败，到点将即时合成：%s", error)
                return
            logger.warning("语音报时合成失败：%s", error)
            self._bubble(f"语音合成失败（{error[:40]}…）")
            return
        if role == "precache":
            # 预合成完成：仅记录缓存路径，不播放（到点由 _fire 直接播缓存）。
            self._precache_path = str(path)
            logger.info("预合成完成：%s", Path(path).name)
            return
        if role == "speak":
            # 外部播报（节日提醒）：只出声，气泡由调用方自己展示。
            self._play_only(path)
            return
        self._play_and_bubble(path, text, self._pending_bubble)

    def _play_and_bubble(self, path: str, text: str, bubble_text: str | None = None) -> None:
        """播放音频并展示气泡。

        ``text`` 为语音口播文本（中文数字）；气泡优先用 ``bubble_text``
        （阿拉伯数字文本），未传时回退 ``text`（兼容旧调用与测试）。
        """
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
        self._bubble(bubble_text if bubble_text is not None else text)

    def _play_only(self, path: str) -> bool:
        """只播放音频、不展示气泡（外部播报用，气泡由调用方自己管）。

        返回是否成功起播；失败只记日志——外部的气泡/提示由调用方决定，
        这里再插一条报时风格的气泡会与对方的展示重复。
        """
        if not self._ensure_player():
            logger.warning("播放器不可用，外部播报跳过播放：%s", Path(path).name)
            return False
        try:
            from PySide6.QtCore import QUrl

            self._audio_out.setVolume(self._cfg["volume"] / 100.0)
            self._player.setSource(QUrl.fromLocalFile(path))
            self._player.play()
            return True
        except Exception:
            logger.exception("外部播报播放失败：%s", path)
            return False

    def _player_busy(self) -> bool:
        """音频通道当前是否正在出声（判断能否立即播报）。"""
        if self._player is None:
            return False
        try:
            from PySide6.QtMultimedia import QMediaPlayer

            return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        except Exception:
            return False

    def _on_media_status(self, status) -> None:
        """一条音频播完后，播放待播队列里的那一条（若有）。

        **刻意不在本方法里 import QtMultimedia**：终止态枚举在 ``_ensure_player``
        成功创建播放器时缓存到 ``self._terminal_statuses``。这样本方法没有 Qt
        导入（Linux 上 QtMultimedia 会拖 libpulse 等系统库，缺失即 ImportError），
        也让单元测试无需为了拿枚举值去导入 QtMultimedia——CI 的 ubuntu 作业正是
        因为测试里那次导入而红过。
        """
        if status not in getattr(self, "_terminal_statuses", ()):
            return
        pending = self._pending_speech
        self._pending_speech = None
        if not pending or self._stopped:
            return
        self._fire(pending, datetime.now(), None, show_bubble=False, role="speak")

    def _ensure_player(self) -> bool:
        if self._player is not None:
            return True
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

            self._player = QMediaPlayer()
            self._audio_out = QAudioOutput()
            self._player.setAudioOutput(self._audio_out)
            # 缓存终止态枚举，供 _on_media_status 判空时使用（见该方法的说明）。
            self._terminal_statuses = (
                QMediaPlayer.MediaStatus.EndOfMedia,
                QMediaPlayer.MediaStatus.InvalidMedia,
            )
            # 播完一条后排空待播队列（外部播报排队用）。
            self._player.mediaStatusChanged.connect(self._on_media_status)
            return True
        except Exception:
            logger.exception("创建 QMediaPlayer 失败（检查 QtMultimedia ffmpegmediaplugin）")
            return False

    # ------------------------------------------------------------ 提示
    def _bubble(self, text: str) -> None:
        """桌宠气泡展示报时文字（受 voice_chime_show_bubble 开关控制）。

        设置窗口打开等场景下 window.show_bubble 会被抑制（_bubble_suppressed），
        报时属用户主动关注事件，此时直接经桌宠气泡位（_speech_bubble）展示，
        避免“只听声不见字”。
        """
        if not self._cfg.get("show_bubble", True):
            return
        app = self._app
        win = getattr(app, "win", None)
        if win is not None and win.isVisible():
            suppressed = bool(getattr(win, "_bubble_suppressed", False))
            if not suppressed:
                try:
                    win.show_bubble(text, duration_ms=self.BUBBLE_DURATION_MS)
                    return
                except Exception:
                    logger.exception("气泡展示失败")
            try:
                bubble = getattr(win, "_speech_bubble", None)
                rect = win.visible_content_rect()
                scale = getattr(win, "scale", 1.0)
                if bubble is not None and rect is not None:
                    bubble.show_text(
                        text,
                        rect,
                        self.BUBBLE_DURATION_MS,
                        pet_scale=scale,
                        subtitle="",
                    )
                    return
            except Exception:
                logger.exception("报时气泡降级展示失败")
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

    def _release_stale_busy(self) -> None:
        """合成线程异常未回调时复位 _busy；否则报时/预合成会被永久跳过。"""
        if not self._busy:
            return
        if time.monotonic() - self._busy_since < _SYNTH_TIMEOUT_S:
            return
        logger.warning("语音合成超时未回调，复位合成锁（%.0fs）", _SYNTH_TIMEOUT_S)
        self._busy = False
        self._synthesis_role = "play"

    # ------------------------------------------------------------ 缓存维护
    def _prune_cache(self) -> None:
        """缓存超上限时按修改时间清理最旧文件（节流，避免每次配置变更全扫）。"""
        now = time.monotonic()
        if self._last_prune_at and now - self._last_prune_at < _PRUNE_MIN_INTERVAL_S:
            return
        self._last_prune_at = now
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
