# -*- coding: utf-8 -*-
"""每日一签的服务层：把纯逻辑接到桌宠气泡与语音通道上。

**触发方式全是用户主动，没有定时器**——这是它和节日提醒/语音报时最本质的区别：
一天一支签，"什么时候看"由林小姐说了算，我们只负责在她点的时候给出来。
唯一主动的一次是**每天第一次点桌宠时的一行提示**（可关），因为"每日"的仪式感
来自"你知道它在等你"，而不是来自弹窗。

三件事：

* `draw_now()`　—— 点「抽支签」：先冒/先念「抽签中...」，约 1 秒后出签并念全篇；
* `hint_on_interaction()`　—— 当天还没看时，冒一行「今日签未看」；
* 状态（今天抽到几号 / 本轮抽过哪些）落在 `config['divination_state']`；
  **不记签册**（2026-10-16 她选 B：历史只写不读，那就别写）。

**当天锁定**：同一天再点永远出同一支（逻辑层就是确定性的，服务层只负责别把
状态写乱）；跨天换新。**一轮不重复**由 `drawn` 累积实现，走满 48 支后开新一轮。
"""
from __future__ import annotations

import datetime as _dt
import logging
import time
from typing import Any

from .. import divination

logger = logging.getLogger(__name__)

#: 气泡停留时长：**按字数算**（固定 12 秒对一百来字偏短，她还没看完就没了）。
#: 4 秒打底 + 每字 90ms，上限 30 秒。
BUBBLE_BASE_MS = 4000
BUBBLE_PER_CHAR_MS = 90
BUBBLE_MAX_MS = 30000

#: 「抽签中...」**念完之后**再静多久才念签文/解签。
#: 她 2026-10-16：「抽签中要停久一点」→「抽签中和解签要分一个比较长的间隔」。
#: 注意是从**声音停下**起算，不是从点击起算——音色/语速一变，固定延时会把间隔吃掉。
READING_GAP_MS = 2800

#: 预热失败后的重试间隔与次数上限。
#: 为什么需要：启动时两段预合成只能提交一段（另一段撞"通道忙"被拒），若不重试，
#: 签文那段会一直缺着，点下去仍要等一次冷合成（实测 1.2–7.9 秒不等）。
PRECACHE_RETRY_MS = 3000
PRECACHE_MAX_TRIES = 20

#: 跨天检查间隔：**开着机过夜**时，0 点后签换新，没人预热的话早上第一次点就是冷的。
ROLL_CHECK_MS = 10 * 60 * 1000

#: 等「抽签中...」念完的轮询步长与上限（超时就直接进下一拍，绝不把抽签卡住）
WAITING_POLL_MS = 150
WAITING_MAX_WAIT_MS = 6000

#: 当天没看时的那行提示（很短，不打扰）
HINT_TEXT = "今日的签还没看 · 右键点「抽支签」"

_STATE_KEY = "divination_state"


def _bubble_ms(text: str) -> int:
    """气泡停留时长：按字数算（4 秒打底 + 每字 90ms，上限 30 秒）。"""
    return min(BUBBLE_MAX_MS, BUBBLE_BASE_MS + BUBBLE_PER_CHAR_MS * len(str(text or "")))


class DivinationService:
    """每日一签（无定时器；由菜单动作与桌宠交互驱动）。"""

    def __init__(self, app: Any) -> None:
        self._app = app
        self._cfg: dict = {}
        # 停用标志 / 代次（见 shutdown 与 _after）：``_disabled`` 挡**入口**，
        # ``_generation`` 挡**投递**——延后回调在投递时记下动身时的代次，回来时
        # 对不上就整条丢弃。
        self._disabled = False
        self._generation = 0

    # ------------------------------------------------------------ 生命周期
    def is_running(self) -> bool:
        """永远"在跑"：本服务没有后台线程，配置一变即可用。"""
        return True

    def apply_config(self) -> None:
        self._cfg = self._read_cfg()
        # 读一次配置即视为"可用"（停用后再启用走的就是这里）
        self._disabled = False
        # 音色/语速/开关可能刚被改过 → 缓存键变了，允许重预合成
        self._precached_day = None
        self.ensure_roll_timer()
        self._precache_today()

    def _read_cfg(self) -> dict:
        """统一走纯逻辑层的清洗（非法值回落默认，启动路径上不许抛异常）。"""
        return divination.normalize_divination_config(getattr(self._app, "config", None))

    # ------------------------------------------------------------ 状态
    def _state(self) -> dict:
        raw = self._app.config.get(_STATE_KEY, {})
        return divination.clean_divination_state(raw)

    def _save_state(self, **changes: Any) -> None:
        state = dict(self._state())
        state.update(changes)
        try:
            self._app.config.set(_STATE_KEY, state)
            self._app.config.save()
        except Exception:
            logger.exception("抽签状态保存失败（本次仍会显示签）")

    def today_quote_id(self, today: _dt.date | None = None) -> int:
        """今天已经抽到的签号；今天还没抽则返回 0。"""
        day = (today or _dt.date.today()).isoformat()
        state = self._state()
        if state.get("date") != day:
            return 0
        try:
            return int(state.get("quote_id") or 0)
        except (TypeError, ValueError):
            return 0

    def has_drawn_today(self, today: _dt.date | None = None) -> bool:
        return self.today_quote_id(today) > 0

    # ------------------------------------------------------------ 抽签
    def draw_now(self, today: _dt.date | None = None) -> dict | None:
        """点「抽支签」：先「抽签中...」，约 1 秒后揭签（同一天重复点只再显示一次，不改签号）。

        返回 ``{"quote":..., "newly": bool}``；功能关闭或服务已停用时返回 None。
        """
        if self._disabled:
            return None
        self._cfg = self._read_cfg()
        if not self._cfg["enabled"]:
            return None
        day = today or _dt.date.today()
        quote, newly = self._resolve_today(day)
        # 起手：气泡先冒「抽签中...」，语音也把它作为整段播报的开头（见 speech_text）。
        # 不单独合成一段"抽签中"——通道只留最新一条待播，两段会互相顶掉。
        self._precache_today(day)
        self._bubble(divination.WAITING_TEXT)
        self._speak(divination.WAITING_TEXT)      # 命中预合成 → 立刻出声
        detail = " / ".join(divination.card_lines(quote))
        self._schedule_reveal(lambda: self._reveal(quote, detail, newly))
        return {"quote": quote, "newly": newly}

    def _schedule_reveal(self, fn) -> None:
        """等「抽签中...」**念完**，再静 ``READING_GAP_MS``，然后才念签文/解签。

        判据是通道真的空了（``channel_idle``），不是从点击起算固定毫秒——音色或语速
        一变，固定延时会把"间隔"吃掉。拿不到空闲判据（测试桩 / 通道不存在）或等超时
        （``WAITING_MAX_WAIT_MS``）都直接进下一拍：宁可间隔短，也不能把抽签卡住。
        """
        state = {"polls": 0}
        deadline = time.monotonic() + WAITING_MAX_WAIT_MS / 1000.0

        def wait() -> None:
            channel = self._channel()
            idle = bool(getattr(channel, "channel_idle", True))
            state["polls"] += 1
            if idle or state["polls"] > 60 or time.monotonic() >= deadline:
                self._after(READING_GAP_MS, fn)
            else:
                self._after(WAITING_POLL_MS, wait)

        wait()

    def _reveal(self, quote: divination.DivinationQuote, detail: str, newly: bool) -> None:
        """揭签：气泡换成签头＋解签，语音念同一段文字（气泡与播报必须一致：她 2026-10-16「语音播报和气泡显示的文字不一样，这一点不舒服」；语音念了四句签诗，气泡就得看见那四句。唯一形式差异是档位——气泡 〔下〕 念成「今日下签」）。"""
        self._bubble("\n".join(divination.card_lines(quote)))
        self._speak(divination.speech_text(quote))
        logger.info("[抽签] %s ｜ %s", detail, "新抽" if newly else "当天重现")

    def _after(self, delay_ms: int, fn) -> None:
        """延时执行；**没有 Qt 事件循环时就地执行**（用例里不引入事件循环）。

        投递口统一套一层代次 guard：闭包带回**动身时**的代次，回来时服务已被停用
        （``shutdown`` 把代次 +1）或已被换掉（停用后再开是另一个实例）就整条丢弃。
        没有这层 guard，关掉抽签之后那条揭签 / 预热重试闭包照旧到点执行——冒气泡、
        经共享音频通道出声、还把当天那段文本重新提交合成。
        """
        generation = self._generation

        def deliver() -> None:
            if generation != self._generation:
                logger.debug("抽签已停用（代次 %s→%s），丢弃迟到的延时回调",
                             generation, self._generation)
                return
            fn()

        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            deliver()
            return
        if QApplication.instance() is None:
            deliver()
            return
        from PySide6.QtCore import QTimer

        QTimer.singleShot(int(delay_ms), deliver)

    def _resolve_today(self, day: _dt.date) -> tuple[divination.DivinationQuote, bool]:
        """拿到当天的签（必要时落状态）。**当天锁定**：有记录就用记录。"""
        state = self._state()
        today = day.isoformat()
        if state.get("date") == today:
            kept = divination.quote_by_id(state.get("quote_id") or 0)
            if kept is not None:
                return kept, False
        drawn = list(state.get("drawn") or [])
        quote = divination.draw_for_day(day, drawn)
        drawn = [*drawn, quote.id]
        if len(set(drawn)) >= len(divination.QUOTES):
            drawn = []          # 一轮走满 48 支 → 开新一轮
        # 只留"今天抽到几号"与"本轮抽过哪些"——**不记签册**：她 2026-10-16 看过
        # "签册只写不读"这条，选了 B（删掉），所以别再往 config 里堆历史。
        # 旧配置里遗留的 `history` 不需要手工清：这里整块覆盖 `divination_state`，
        # 下一次抽签它自己就没了。
        self._save_state(date=today, quote_id=quote.id, drawn=drawn)
        return quote, True

    # ------------------------------------------------------------ 每日提示
    def hint_on_interaction(self, today: _dt.date | None = None) -> bool:
        """当天还没看签时冒一行提示；返回是否真的提示了。

        **一天只提示一次**（state 里记 ``hint_date``）：她一天会点桌宠很多次，
        每点一次冒一次就不叫提醒、叫唠叨了。
        """
        if self._disabled:
            return False
        self._cfg = self._read_cfg()
        if not self._cfg["enabled"] or not self._cfg["hint"]:
            return False
        day = (today or _dt.date.today()).isoformat()
        if self.has_drawn_today(today):
            return False
        if self._state().get("hint_date") == day:
            return False
        self._bubble(HINT_TEXT)
        self._save_state(hint_date=day)
        self._precache_today(today or _dt.date.today())
        return True

    # ------------------------------------------------------------ 输出
    def _bubble(self, text: str) -> None:
        """桌宠气泡。门禁与降级口径照抄节日提醒（免打扰判定必须在可见性之前）。"""
        if not text:
            return
        from .. import window_alerts
        app = self._app
        win = getattr(app, "win", None)
        if win is not None and window_alerts.quiet_blocked(win):
            window_alerts.defer_quiet(text)
            return
        if win is not None and win.isVisible():
            if window_alerts.sprite_in_flight(win):
                return
            if not window_alerts.bubble_blocked(win):
                try:
                    win.show_bubble(text, duration_ms=_bubble_ms(text))
                    return
                except Exception:
                    logger.exception("抽签气泡展示失败")
            try:
                bubble = getattr(win, "_speech_bubble", None)
                rect = win.visible_content_rect()
                scale = getattr(win, "scale", 1.0)
                if bubble is not None and rect is not None:
                    bubble.show_text(text, rect, _bubble_ms(text), pet_scale=scale, subtitle="")
                    return
            except Exception:
                logger.exception("抽签气泡降级展示失败")
        notify = getattr(app, "system_notify", None)
        if callable(notify):
            try:
                notify("每日一签", text)
            except Exception:
                logger.exception("抽签桌面通知失败")

    def _channel(self):
        """进程内唯一的音频通道（报时服务）。取不到就返回 None，只降级不出错。"""
        getter = getattr(self._app, "ensure_audio_channel", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:
            logger.exception("获取音频通道失败，抽签仅出气泡")
            return None

    def ensure_roll_timer(self) -> None:
        """装一个"跨天看一眼"的定时器（每 10 分钟）。

        **只在真有 Qt 事件循环时装**：用例里没有事件循环，而 `_after` 在无事件循环时
        就地执行——那时装定时器等于把 `_precache_today` 变成死循环。
        """
        if getattr(self, "_roll_timer", None) is not None:
            return
        try:
            from PySide6.QtCore import QTimer
            from PySide6.QtWidgets import QApplication
        except Exception:
            return
        if QApplication.instance() is None:
            return
        timer = QTimer()
        timer.setInterval(ROLL_CHECK_MS)
        timer.timeout.connect(self._precache_today)
        timer.start()
        self._roll_timer = timer

    def shutdown(self) -> None:
        """停用抽签：停掉跨天定时器，并让所有已投递的延后回调作废。

        **为什么不止停定时器**：揭签、通道轮询、预热重试都是 ``QTimer.singleShot``
        投递出去的闭包，停定时器管不着它们——关掉抽签之后它们照旧到点执行，冒出
        气泡、经共享音频通道出声、还把当天那段文本重新提交合成。代次 +1 之后，
        投递口（``_after``）把它们整条丢弃；``_disabled`` 挡住停用期间新到的入口
        调用（含跨天定时器那一拍已经排进事件队列的 timeout）。

        **只作废本服务自己的东西**：那条音频通道是报时 / 节日语音 / 点击朗读共用
        的，这里一个字节都不碰（停用它等于替别的功能关声音）。
        """
        self._disabled = True
        self._generation += 1
        # 在飞的那条预热重试链同样已被代次作废，标记要一起复位：留着 True 会让
        # 复活后的 `_precache_today` 一直以为"已有一条链在跑"而不再挂新的，当天那段
        # 签文就再也没人补合成（停用是终态、复活走 `apply_config`）。
        self._precache_pending = False
        timer = getattr(self, "_roll_timer", None)
        self._roll_timer = None
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                logger.exception("停止抽签跨天定时器失败")

    def _quote_for_day(self, day: _dt.date):
        """今天**会**抽到哪一支（不落状态，供预合成用；与 `_resolve_today` 同算法）。"""
        state = self._state()
        if state.get("date") == day.isoformat():
            kept = divination.quote_by_id(state.get("quote_id") or 0)
            if kept is not None:
                return kept
        return divination.draw_for_day(day, state.get("drawn") or [])

    def _precache_today(self, today: _dt.date | None = None) -> None:
        """把「抽签中...」和今天那段签文提前合成进缓存（**不播放**）。

        这是「语音合成慢」的正解：合成发生在点击**之前**，点下去就是缓存命中，
        零网络延迟。开关关着 / 通道不支持预合成 / 免打扰期间都直接跳过。
        """
        # 入口门：停用后不再提交任何合成（跨天定时器的 timeout 可能已经排在队列里，
        # 迟到的重试闭包也不再走这条路——它在投递口就被代次挡掉了，这里是第二道）。
        if self._disabled:
            return
        if not self._cfg.get("speak"):
            logger.info("[抽签] 语音关着，跳过预合成")
            return
        day = today or _dt.date.today()
        if getattr(self, "_precached_day", None) == day.isoformat():
            return
        channel = self._channel()
        precache = getattr(channel, "precache", None)
        if not callable(precache):
            logger.info("[抽签] 音频通道不支持预合成（channel=%s），跳过", type(channel).__name__)
            return
        try:
            first = bool(precache(divination.WAITING_TEXT, log_tag="每日一签"))
            second = bool(precache(divination.speech_text(self._quote_for_day(day)),
                                   log_tag="每日一签"))
        except Exception:
            logger.exception("抽签语音预合成失败")
            return
        # **两段都办成才算今天预热过**；办不成就别打标，留给下一次调用重试——
        # 通道忙时第二段会被跳掉，打早了当天就再也不会补（真事故）。
        # 下一次调用通常在点击那一刻，离揭签还有 2.8 秒，够它合完。
        if first and second:
            self._precached_day = day.isoformat()
            self._precache_tries = 0
            self._precache_pending = False
            logger.info("[抽签] 语音已预热（起手 + 今天的签文）")
            return
        if getattr(self, "_precache_pending", False):
            return          # 已经有一条重试链在跑，别再挂第二条（日志里曾每 3 秒两条）
        tries = getattr(self, "_precache_tries", 0) + 1
        self._precache_tries = tries
        if tries > PRECACHE_MAX_TRIES:
            logger.info("[抽签] 语音预热放弃（已试 %s 次），点下去可能等一次冷合成", tries)
            return
        logger.info("[抽签] 语音预热未完成（起手=%s 签文=%s），%s 秒后重试（第 %s 次）",
                    first, second, PRECACHE_RETRY_MS // 1000, tries)
        # **必须重试**：启动时两段只能成一段（另一段撞"通道忙"），不补的话签文那段
        # 当天一直缺着，点下去仍要等一次冷合成——那正是她说的"还是太慢"。
        self._precache_pending = True

        def _retry() -> None:
            self._precache_pending = False
            self._precache_today(day)

        self._after(PRECACHE_RETRY_MS, _retry)

    def _speak(self, text: str) -> None:
        """经报时服务的音频通道播报（刻意不自建播放器：结构上保证不叠音）。

        命中预合成缓存时是零延迟起播——这就是"点下去马上出声"的来源。
        """
        if not self._cfg.get("speak") or not text:
            return
        channel = self._channel()
        if channel is None:
            return
        try:
            channel.speak(text, log_tag="每日一签")
        except Exception:
            logger.exception("抽签语音播报失败")
