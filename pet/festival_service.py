# -*- coding: utf-8 -*-
"""节日提醒服务：调度 + 气泡落地（服务层）。

分层约定与 voice_chime_service.py 一致：
  - **模块顶层不 import Qt**：QTimer 在 ``__init__`` 内惰性导入，使本模块
    可被无 GUI 环境导入（也便于纯逻辑测试引用常量）。
  - 服务不继承 QObject，由 AppShell 持有引用保证生命周期。
  - Phase 2 插件路径可以注入 SchedulerPort、PresentationPort 和音频端口；
    旧 AppShell 直接构造路径继续使用本地 QTimer/窗口兼容逻辑。

为什么 tick 是 30s 而不是 60s：提醒时间点是精确到分钟的，60s 间隔在边界
抖动下可能整分钟跳过；30s 保证任意分钟至少被采样两次，且开销可忽略。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Callable

from .festival import (
    NO_FESTIVAL_TEXT,
    build_festival_text,
    normalize_festival_config,
    reminder_slot,
    startup_slot,
)

logger = logging.getLogger(__name__)


class FestivalReminderService:
    """节日提醒服务（GUI 线程）。"""

    TICK_INTERVAL_MS = 30_000
    BUBBLE_DURATION_MS = 12_000

    def __init__(
        self,
        app,
        *,
        scheduler: Any | None = None,
        presentation: Any | None = None,
        audio: Callable[..., Any] | None = None,
        config_source: Any | None = None,
    ) -> None:
        self._app = app
        self._scheduler = scheduler
        self._presentation = presentation
        self._audio = audio
        self._config_source = config_source
        # 契约形状由纯逻辑层定义（enabled/cn/solar_terms/west/mode/count/times/...）
        self._cfg: dict = normalize_festival_config(None)
        # 当天已触发的槽位；跨天自动清空（槽位本身含日期，这里只是防集合无界增长）
        self._slot_day: date | None = None
        self._fired: set[str] = set()
        self._timer = None
        self._timer_handle = None
        if scheduler is None:
            from PySide6.QtCore import QTimer

            self._timer = QTimer()
            self._timer.setInterval(self.TICK_INTERVAL_MS)
            self._timer.timeout.connect(self._on_tick)

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        self.apply_config()
        self._catch_up()
        if self._scheduler is not None:
            if self._timer_handle is None or not self._timer_handle.is_active():
                self._timer_handle = self._scheduler.call_repeating(self._on_timer_tick, self.TICK_INTERVAL_MS)
        else:
            self._timer.start()

    def stop(self) -> None:
        if self._scheduler is not None:
            if self._timer_handle is not None:
                self._timer_handle.cancel()
                self._timer_handle = None
        elif self._timer is not None:
            self._timer.stop()

    def is_running(self) -> bool:
        """调度 tick 是否在跑（AppShell 的懒启停门控据此决定重启还是只刷配置）。"""
        if self._scheduler is not None:
            return bool(self._timer_handle is not None and self._timer_handle.is_active())
        return bool(self._timer is not None and self._timer.isActive())

    def _on_timer_tick(self) -> None:
        self._on_tick()

    def _config_values(self) -> Any:
        config = self._config_source
        if config is None:
            config = getattr(self._app, "config", None)
        if hasattr(config, "as_legacy_config"):
            return config.as_legacy_config()
        return config if config is not None else {}

    def apply_config(self) -> None:
        """重读配置并使用纯逻辑层统一清洗。"""
        self._cfg = normalize_festival_config(self._config_values())

    # ------------------------------------------------------------ 对外入口
    def remind_now(self) -> None:
        """手动提醒「今日节日」，无视总开关。"""
        self.apply_config()
        now = datetime.now()
        text = build_festival_text(now.date(), self._cfg, 0) or NO_FESTIVAL_TEXT
        self._bubble(text)
        self._speak(text)

    def should_speak_at(self, chime_slot: str) -> bool:
        """本分钟是否该让报时让位。"""
        cfg = normalize_festival_config(self._config_values())
        if not (cfg.get("enabled") and cfg.get("speak")):
            return False
        try:
            when = datetime.strptime(str(chime_slot)[:16], "%Y-%m-%dT%H:%M")
        except (TypeError, ValueError):
            return False
        return bool(reminder_slot(when, cfg))

    # ------------------------------------------------------------ 调度
    def _catch_up(self, now: datetime | None = None) -> None:
        """启动补提醒：当天有节日且已过首个提醒点时，立即补报一次。

        桌宠不保证常驻，用户可能中午才开机；没有这一步，当天的提醒点
        全部错过后就再也收不到，功能体感等于失效。

        ``now`` 可注入（同 ``_on_tick``）：调用方传 ``None`` 即取当前时刻，
        便于用例覆盖“恰好在提醒分钟内启动”这条只有真机重启才会踩到的路径。
        """
        now = now or datetime.now()
        slot = startup_slot(now, self._cfg)
        if not slot or slot in self._fired:
            return
        self._roll_day(now.date())
        self._fired.add(slot)
        text = build_festival_text(now.date(), self._cfg, 0)
        if text:
            self._bubble(text)
            self._speak(text)
            # 启动时刻恰好落在当天的某个提醒分钟内：把本分钟的正式槽位一并
            # 盖戳。否则紧接着的 _on_tick 会命中同一分钟再播一次（两次气泡 +
            # 两段 TTS），与 reminder_slot 承诺的“同一提醒时间只播报一次”矛盾。
            # 只压这一分钟——晚些时候的提醒点仍走各自的正式槽位照常播报。
            due = reminder_slot(now, self._cfg)
            if due:
                self._fired.add(due)

    def _on_tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        if not self._cfg.get("enabled"):
            return
        self._roll_day(now.date())
        slot = reminder_slot(now, self._cfg)
        if not slot or slot in self._fired:
            return
        self._fired.add(slot)
        # 当天第几次提醒：作为文案索引，使同一天多次提醒轮到不同句子。
        index = len(self._fired) - 1
        text = build_festival_text(now.date(), self._cfg, index)
        if text:
            self._bubble(text)
            self._speak(text)

    def _roll_day(self, today: date) -> None:
        if self._slot_day != today:
            self._slot_day = today
            self._fired = set()

    # ------------------------------------------------------------ 语音
    def _speak(self, text: str) -> None:
        """经语音报时服务的音频通道播报节日提醒。

        刻意**不自建播放器**：报时服务是进程内唯一的音频通道，共用它才能在结构上
        保证不叠音（详情见 voice_chime_service.speak 的说明）。通道不存在或播放
        失败都只降级为“有气泡没声音”，不影响提醒本身。
        """
        if not self._cfg.get("speak") or not text:
            return
        if self._audio is not None:
            try:
                self._audio(text, log_tag="节日提醒")
            except Exception:
                logger.exception("节日提醒语音播报失败")
            return
        getter = getattr(self._app, "ensure_audio_channel", None)
        if not callable(getter):
            logger.warning("音频通道不可用，节日提醒仅出气泡")
            return
        try:
            channel = getter()
        except Exception:
            logger.exception("获取音频通道失败，节日提醒仅出气泡")
            return
        if channel is None:
            logger.warning("音频通道为空，节日提醒仅出气泡")
            return
        try:
            channel.speak(text, log_tag="节日提醒")
        except Exception:
            logger.exception("节日提醒语音播报失败")

    # ------------------------------------------------------------ 提示
    def _bubble(self, text: str) -> None:
        """桌宠气泡展示节日提醒。

        与语音报时同策略：设置窗口打开等场景下 ``win.show_bubble`` 会被抑制，
        而节日提醒是用户主动关注的事件（一天只有一两次），此时退到桌宠气泡位
        直接展示，避免“弹一下就没了”；两者都不可用时再退到系统通知。
        插件路径优先通过 PresentationPort，旧路径保留兼容降级。
        """
        if not text:
            return
        if self._presentation is not None:
            try:
                if self._presentation.show_bubble(text, duration_ms=self.BUBBLE_DURATION_MS):
                    return
            except Exception:
                logger.exception("节日提醒气泡展示失败")
            try:
                if self._presentation.notify("节日提醒", text, duration_ms=self.BUBBLE_DURATION_MS):
                    return
            except Exception:
                logger.exception("节日提醒桌面通知失败")
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
                    logger.exception("节日提醒气泡展示失败")
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
                logger.exception("节日提醒气泡降级展示失败")
        notify = getattr(app, "system_notify", None)
        if callable(notify):
            try:
                notify("节日提醒", text)
            except Exception:
                logger.exception("节日提醒桌面通知失败")
