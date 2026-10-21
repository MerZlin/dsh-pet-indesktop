# -*- coding: utf-8 -*-
"""每日一签服务层用例（纯逻辑 + 桩件；不开 Qt）。

钉住的是**状态与节奏**：当天锁定、跨天换新、一轮不重复的累积、先冒「抽签中...」
再揭签、提示一天只冒一次、语音默认不念、脏状态不崩。

延时用 `service._after` 打桩成"立即执行"——用例里不引入事件循环（真机上它有
QTimer 走 1.1 秒的停顿，那属于观感，不属于逻辑）。
"""
from __future__ import annotations

import datetime as dt

from pet import divination
from pet.divination.service import (
    HINT_TEXT,
    PRECACHE_MAX_TRIES,
    READING_GAP_MS,
    WAITING_POLL_MS,
    DivinationService,
)


class _Cfg:
    def __init__(self, data: dict | None = None) -> None:
        self.data = dict(data or {})
        self.saves = 0

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value

    def save(self) -> None:
        self.saves += 1


class _Win:
    def __init__(self) -> None:
        self.bubbles: list[str] = []
        self._bubble_suppressed = False
        self.cfg = _Cfg()

    def isVisible(self) -> bool:  # noqa: N802 - 对齐 Qt 命名
        return True

    def show_bubble(self, text, duration_ms=0) -> None:  # noqa: N802
        self.bubbles.append(text)


class _Channel:
    def __init__(self) -> None:
        self.spoken: list[tuple[str, str]] = []

    def speak(self, text: str, *, log_tag: str = "") -> None:
        self.spoken.append((text, log_tag))


class _App:
    def __init__(self, **cfg) -> None:
        self.config = _Cfg(cfg)
        self.win = _Win()
        self.channel = _Channel()

    def ensure_audio_channel(self):
        return self.channel


def _day(text: str) -> dt.date:
    return dt.date.fromisoformat(text)


def _service(**cfg) -> tuple[DivinationService, _App]:
    app = _App(divination_enabled=True, **cfg)
    service = DivinationService(app)
    service._after = lambda _ms, fn: fn()      # 打桩：延时立即执行
    service.apply_config()
    return service, app


# ------------------------------------------------------------- 出签
def test_draw_shows_waiting_then_the_whole_slip() -> None:
    """先「抽签中...」，再全篇（签头＋四句＋解签）——这是她要的仪式感。"""
    service, app = _service()
    result = service.draw_now(_day("2026-10-16"))
    assert app.win.bubbles[0] == divination.WAITING_TEXT
    expected = divination.card_lines(result["quote"])
    assert app.win.bubbles[1].splitlines() == expected
    assert len(app.win.bubbles[1].splitlines()) == 6


def test_bubble_and_speech_carry_the_same_text() -> None:
    """**气泡与播报的文字必须一致**（她 2026-10-16：「文字不一样，这一点不舒服」）。

    签题、四句签诗、解签——三样两边都得有。气泡只显示两行简版时这条会红。
    """
    service, app = _service(divination_speak=True)
    result = service.draw_now(_day("2026-10-16"))
    bubble = app.win.bubbles[-1]
    speech = app.channel.spoken[-1][0]
    quote = result["quote"]
    for piece in (quote.title, *quote.verses, quote.reading):
        assert piece in bubble, f"气泡缺：{piece}"
        assert piece in speech, f"播报缺：{piece}"


def test_reveal_scheduled_after_the_reading_gap() -> None:
    """等「抽签中...」念完，再静 READING_GAP_MS 才揭签（不是从点击起算的固定延时）。"""
    service, app = _service()
    calls: list[int] = []
    service._after = lambda ms, fn: calls.append(ms)
    service.draw_now(_day("2026-10-16"))
    assert calls == [READING_GAP_MS]
    assert app.win.bubbles == [divination.WAITING_TEXT]      # 这一刻还没揭签


def test_reveal_waits_while_the_channel_is_busy() -> None:
    """通道还占着（「抽签中...」在响）就先轮询，不揭签。"""
    service, app = _service()
    polls: list[int] = []

    class _Busy:
        channel_idle = False

    app.channel = _Busy()
    service._after = lambda ms, fn: (polls.append(ms), None)[1]
    service.draw_now(_day("2026-10-16"))
    assert polls == [WAITING_POLL_MS]           # 只轮询，不揭签
    assert app.win.bubbles == [divination.WAITING_TEXT]


def test_draw_is_disabled_when_switch_off() -> None:
    app = _App(divination_enabled=False)
    service = DivinationService(app)
    service.apply_config()
    assert service.draw_now(_day("2026-10-16")) is None
    assert app.win.bubbles == []


def test_same_day_is_locked_to_one_quote() -> None:
    service, app = _service()
    first = service.draw_now(_day("2026-10-16"))
    second = service.draw_now(_day("2026-10-16"))
    assert first["quote"].id == second["quote"].id
    assert first["newly"] is True and second["newly"] is False
    state = app.config.data["divination_state"]
    assert state["quote_id"] == first["quote"].id
    assert "history" not in state          # 签册已删（她选 B）
    assert len(app.win.bubbles) == 4      # 两次点击各冒一次起手＋一次结果


def test_next_day_draws_again() -> None:
    service, app = _service()
    service.draw_now(_day("2026-10-16"))
    service.draw_now(_day("2026-10-17"))
    state = app.config.data["divination_state"]
    assert state["date"] == "2026-10-17"
    assert len(state["drawn"]) == 2


def test_drawn_pool_accumulates_across_days() -> None:
    service, app = _service()
    day = _day("2026-01-01")
    for i in range(10):
        service.draw_now(day + dt.timedelta(days=i))
    drawn = app.config.data["divination_state"]["drawn"]
    assert len(drawn) == 10 and len(set(drawn)) == 10


def test_drawn_pool_resets_after_a_full_round() -> None:
    service, app = _service()
    day = _day("2026-01-01")
    for i in range(len(divination.QUOTES)):
        service.draw_now(day + dt.timedelta(days=i))
    state = app.config.data["divination_state"]
    assert state["drawn"] == []               # 走满一轮 → 清空重开


def test_state_is_todays_quote_even_if_config_edited() -> None:
    """手改配置把 quote_id 写成不存在的号 → 当天重抽，不抛异常。"""
    service, app = _service()
    app.config.data["divination_state"] = {"date": "2026-10-16", "quote_id": 9999}
    assert service.draw_now(_day("2026-10-16"))["quote"].id in {q.id for q in divination.QUOTES}


def test_garbage_state_does_not_crash() -> None:
    service, app = _service()
    for junk in ("nonsense", 42, {"date": "not-a-date", "drawn": ["x"], "history": {"bad": "y"}}):
        app.config.data["divination_state"] = junk
        service.apply_config()
        assert service.draw_now(_day("2026-10-16")) is not None


def test_state_stays_small_over_a_long_run() -> None:
    """状态里只有 日期 / 今天那支 / 本轮已抽 —— 跑一年也不会越写越大。"""
    service, app = _service()
    day = _day("2026-01-01")
    for i in range(400):
        service.draw_now(day + dt.timedelta(days=i))
    state = app.config.data["divination_state"]
    assert set(state) == {"date", "quote_id", "drawn"}
    assert len(state["drawn"]) <= len(divination.QUOTES)


# ------------------------------------------------------------- 每日提示
def test_hint_fires_only_before_todays_draw() -> None:
    service, app = _service()
    assert service.hint_on_interaction(_day("2026-10-16")) is True
    assert app.win.bubbles[-1] == HINT_TEXT
    service.draw_now(_day("2026-10-16"))
    assert service.hint_on_interaction(_day("2026-10-16")) is False


def test_hint_fires_at_most_once_a_day() -> None:
    """她一天会点很多次桌宠——提示只能冒一次，否则就是唠叨。"""
    service, app = _service()
    assert service.hint_on_interaction(_day("2026-10-16")) is True
    for _ in range(5):
        assert service.hint_on_interaction(_day("2026-10-16")) is False
    assert len(app.win.bubbles) == 1
    assert service.hint_on_interaction(_day("2026-10-17")) is True


def test_hint_can_be_turned_off() -> None:
    service, app = _service(divination_hint=False)
    assert service.hint_on_interaction(_day("2026-10-16")) is False
    assert app.win.bubbles == []


def test_hint_off_when_feature_disabled() -> None:
    app = _App(divination_enabled=False, divination_hint=True)
    service = DivinationService(app)
    service.apply_config()
    assert service.hint_on_interaction(_day("2026-10-16")) is False


# ------------------------------------------------------------- 语音
def test_speak_is_off_by_default() -> None:
    service, app = _service()
    service.draw_now(_day("2026-10-16"))
    assert app.channel.spoken == []


def test_speak_is_waiting_then_full_slip() -> None:
    """两段：先「抽签中...」出声（命中预合成 → 零延迟），再念全篇。"""
    service, app = _service(divination_speak=True)
    result = service.draw_now(_day("2026-10-16"))
    assert [text for text, _ in app.channel.spoken] == [
        divination.WAITING_TEXT, divination.speech_text(result["quote"])]
    assert all(tag == "每日一签" for _, tag in app.channel.spoken)


def test_the_gap_between_waiting_and_reading_is_long() -> None:
    """她 2026-10-16：「抽签中和解签要分一个比较长的间隔」——钉住下限。"""
    assert READING_GAP_MS >= 2500


def test_waiting_line_is_spoken_exactly_once() -> None:
    """「抽签中...」只能念一遍：起手是独立一句，签文那句**不许**再带它。"""
    service, app = _service(divination_speak=True)
    result = service.draw_now(_day("2026-10-16"))
    spoken = [text for text, _ in app.channel.spoken]
    assert spoken[0] == divination.WAITING_TEXT
    assert not spoken[1].startswith(divination.WAITING_TEXT)
    assert sum(text.count("抽签中") for text in spoken) == 1
    assert spoken[1] == divination.speech_text(result["quote"])


def test_speak_precaches_the_clips_on_config() -> None:
    """开关开着时，配置一变就把「抽签中...」与今天的签文提前合进缓存（不播放）。"""
    app = _App(divination_enabled=True, divination_speak=True)
    app.channel.precached = []

    def _fake_precache(text, log_tag=""):
        app.channel.precached.append(text)
        return True          # 语义：这次之后它已可用 / 已在合成中

    app.channel.precache = _fake_precache
    service = DivinationService(app)
    service._after = lambda _ms, fn: fn()
    service.apply_config()
    assert divination.WAITING_TEXT in app.channel.precached
    assert any(text.startswith(divination.WAITING_TEXT) for text in app.channel.precached)


def test_precache_retries_until_the_clips_are_ready() -> None:
    """通道忙拒绝时必须重试——启动时两段只能成一段，不补签文那段当天一直缺。"""
    app = _App(divination_enabled=True, divination_speak=True)
    calls: list[str] = []

    def _refuse_twice(text, log_tag=""):
        calls.append(text)
        return len(calls) > 2          # 前两次拒绝（模拟"通道忙"），之后接受

    app.channel.precache = _refuse_twice
    service = DivinationService(app)
    service._after = lambda _ms, fn: fn()
    service.apply_config()
    assert service._precached_day == dt.date.today().isoformat(), "重试链跑完后应当打标"


def test_precache_gives_up_after_the_cap() -> None:
    """一直拒绝也不能无限重试（上限后放弃，点下去最多等一次冷合成）。"""
    app = _App(divination_enabled=True, divination_speak=True)
    calls: list[str] = []
    app.channel.precache = lambda text, log_tag="": (calls.append(text), False)[1]
    service = DivinationService(app)
    service._after = lambda _ms, fn: fn()
    service.apply_config()
    assert service._precached_day is None
    assert len(calls) <= 2 * (PRECACHE_MAX_TRIES + 2), len(calls)


def test_bubble_duration_scales_with_text() -> None:
    """⑤ 气泡时长按字数算：六行签文不能被固定 12 秒截短。"""
    from pet.divination.service import BUBBLE_MAX_MS, _bubble_ms

    short = _bubble_ms("一行")
    long = _bubble_ms("字" * 400)
    assert short < long
    assert long == BUBBLE_MAX_MS           # 有上限，别停着不走
    assert _bubble_ms("") >= 4000
    # 六行签文（约 100 字）要明显长于固定 12 秒
    assert _bubble_ms("字" * 100) > 12000


def test_precache_retry_chain_is_not_duplicated() -> None:
    """④ 只许一条重试链（曾经有两条并行，日志每 3 秒两条）。"""
    app = _App(divination_enabled=True, divination_speak=True)
    app.channel.precache = lambda text, log_tag="": False      # 一直拒绝
    service = DivinationService(app)
    scheduled: list[int] = []
    service._after = lambda ms, fn: scheduled.append(ms)
    service.apply_config()             # 先读配置（_cfg 非空才会走到重试）
    service._precache_today()          # 第二次应当被"挂起中"挡住，不再挂链
    service._precache_today()
    assert scheduled == [3000], scheduled


def test_precache_skipped_when_speech_off() -> None:
    app = _App(divination_enabled=True, divination_speak=False)
    app.channel.precached = []
    app.channel.precache = lambda text, log_tag="": app.channel.precached.append(text)
    service = DivinationService(app)
    service._after = lambda _ms, fn: fn()
    service.apply_config()
    assert app.channel.precached == []


def test_speak_survives_missing_channel() -> None:
    service, app = _service(divination_speak=True)
    app.ensure_audio_channel = lambda: None
    assert service.draw_now(_day("2026-10-16")) is not None
