# -*- coding: utf-8 -*-
"""每日一签的**时序**用例：真 Qt 事件循环 + 真 ``QTimer.singleShot`` 投递。

``test_divination_service.py`` 刻意不开 Qt（把 ``_after`` 打桩成立即执行）；本文件
补的是另一半——揭签 / 通道轮询 / 预热重试都是**投递出去的闭包**，它们在该服务被
停用之后还会不会执行，只有在真实事件循环里才判得出来。

纪律（AGENTS.md「CI cost discipline」）：不用固定 sleep 猜时序——每个"什么都没
发生"的断言都配一个**更晚投递、更晚到点**的控制定时器当凭据（它响了才证明事件
循环确实走过了旧回调的到点时刻），预算取宽。
"""

from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from pet import divination
from pet.divination.service import DivinationService
from tests.test_divination_service import _App, _Channel, _day

#: 把揭签间隔与预热重试间隔缩到几十毫秒：被测语义是投递口的停用 / 代次 guard，
#: 不是延时的数值。缩短后走的仍是真实的 ``QTimer.singleShot`` 与真实事件循环。
_SHORT_MS = 25
_BUDGET_S = 20.0


class _SharedChannel(_Channel):
    """共用音频通道替身：额外记下"有没有人停过它"（停用抽签不许碰它）。"""

    def __init__(self) -> None:
        super().__init__()
        self.stopped: list[bool] = []

    def stop(self) -> None:  # pragma: no cover - 只有"有人误停通道"时才会被调用
        self.stopped.append(True)


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _wait_until(predicate, *, budget: float = _BUDGET_S) -> bool:
    """轮询直到成立（期间照常跑事件循环）；超时返回最后的判定值。"""
    deadline = time.monotonic() + budget
    while True:
        _qapp().processEvents()
        result = predicate()
        if result or time.monotonic() >= deadline:
            return bool(result)
        time.sleep(0.005)


def _advance_past(ms: int) -> bool:
    """把事件循环推进到 ``ms`` 之后；返回控制定时器是否真的响过。"""
    fired: list[bool] = []
    QTimer.singleShot(int(ms) + 5, lambda: fired.append(True))
    return _wait_until(lambda: bool(fired))


@pytest.fixture
def short_delays(monkeypatch):
    """缩短两个延时（数值不是被测语义）。"""
    import pet.divination.service as dsvc

    monkeypatch.setattr(dsvc, "READING_GAP_MS", _SHORT_MS)
    monkeypatch.setattr(dsvc, "PRECACHE_RETRY_MS", _SHORT_MS)
    return _SHORT_MS


def _timer_service(*, speak: bool = True) -> tuple[DivinationService, _App]:
    """真事件循环 + 真 ``_after``（不打桩）的服务。"""
    _qapp()
    app = _App(divination_enabled=True, divination_speak=speak)
    app.channel = _SharedChannel()
    service = DivinationService(app)
    service.apply_config()
    return service, app


def test_shutdown_drops_the_scheduled_reveal(short_delays):
    """停用抽签后，已经投递出去的揭签闭包不许再冒气泡 / 出声（也不许停共用通道）。"""
    service, app = _timer_service()
    service.draw_now(_day("2026-10-16"))
    assert app.win.bubbles == [divination.WAITING_TEXT], "前提：这一刻只冒了起手"
    spoken = list(app.channel.spoken)

    service.shutdown()
    assert _advance_past(_SHORT_MS), "前提：事件循环确实走过了揭签到点的时刻"

    assert app.win.bubbles == [divination.WAITING_TEXT], "停用后旧闭包不许再揭签"
    assert app.channel.spoken == spoken, "停用后旧闭包不许再出声"
    assert app.channel.stopped == [], "停用抽签不许停那条共用音频通道"


def test_shutdown_drops_the_pending_precache_retry(short_delays):
    """停用抽签后，还没到点的那条预热重试闭包不许再提交一次合成。"""
    service, app = _timer_service()
    calls: list[str] = []

    def _refuse(text, log_tag=""):
        calls.append(text)
        return False          # 契约语义：这次没办成 → 服务层挂一条重试链

    app.channel.precache = _refuse
    service._precache_today()
    assert service._precache_pending is True, "前提：一条重试链已经投递出去"
    tried = len(calls)
    assert tried, "前提：这一轮确实问过通道"

    service.shutdown()
    assert _advance_past(_SHORT_MS), "前提：事件循环确实走过了重试到点的时刻"

    assert len(calls) == tried, "停用后旧重试闭包不许再提交合成"
    assert service._precache_pending is False, "作废的链不许留下'已有一条在跑'的标记"


def test_disable_then_re_enable_is_a_new_generation(short_delays):
    """停用 → 再启用（产品路径 = 重建实例）：旧实例的在途回调全无副作用，新实例照常工作。

    停用期间**入口**也要关门（不出签 / 不冒提示），与投递口的代次 guard 一起构成
    "停用后不再产生任何副作用"。
    """
    service, app = _timer_service()
    service.draw_now(_day("2026-10-16"))            # 旧实例投递出去的揭签
    spoken = len(app.channel.spoken)

    service.shutdown()
    assert service.draw_now(_day("2026-10-21")) is None, "停用后不再出签"
    # 这一天没抽过、也没提示过 → 返回 False 只可能来自停用门
    assert service.hint_on_interaction(_day("2026-10-21")) is False, "停用后不再冒提示"

    fresh = DivinationService(app)                  # 再启用：_sync_divination_service 重建的就是它
    fresh.apply_config()
    assert _advance_past(_SHORT_MS), "前提：事件循环确实走过了旧闭包的到点时刻"

    assert app.win.bubbles == [divination.WAITING_TEXT], "旧实例的闭包不许再执行"
    assert len(app.channel.spoken) == spoken, "停用后不许再出声"
    assert app.channel.stopped == [], "停用抽签不许停那条共用音频通道"

    result = fresh.draw_now(_day("2026-10-17"))
    assert result is not None
    assert _wait_until(lambda: len(app.win.bubbles) == 3), "再启用后抽签必须照常揭签"
    assert app.win.bubbles[-1].splitlines() == divination.card_lines(result["quote"])
    fresh.shutdown()
