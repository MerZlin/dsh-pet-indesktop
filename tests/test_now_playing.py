# -*- coding: utf-8 -*-
"""回归：SMTC 采样不得阻塞调用方线程（事故 2026-09-17）。

背景：歌词控制器每 1000ms 在 GUI 线程 tick 一次 ``now_playing.get_now_playing()``。
旧实现直接 ``asyncio.run(_read_async())``；而 PyWinRT 的 awaitable 在某些系统/
播放器状态下**永不完成**，且该 await 连 ``asyncio.wait_for`` 都取消不掉（事件循环
钉在 proactor ``_poll``，定时器得不到执行）→ GUI 线程永久阻塞 → 窗口冻死，被
Windows 判定 ``Application Hang`` 关掉。

设计约束（本文件锁死的契约）：采样在工作线程里做，取值/操作在调用线程只做**有界
等待**。功能可以降级（歌词停止更新），进程必须活着。
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time

import pytest

from pet import now_playing

# 预算给得很宽（CI 慢 runner 是本地数倍慢），只要求「有界」而非「快」。
BUDGET = 3.0


@pytest.fixture(autouse=True)
def _isolate_sampler():
    """每个用例前后都摘掉采样线程，避免模块级状态串场。"""
    now_playing._stop_sampler()
    yield
    now_playing._stop_sampler()
    now_playing._sample_empty_since = None


@pytest.fixture(autouse=True)
def _no_real_window_probe(monkeypatch):
    """窗口标题兜底默认按「没在放歌」处理（打到最内层探针，便于用例自替换）。"""
    monkeypatch.setattr(
        now_playing.media_window, "read_window_media", lambda: None
    )


@pytest.fixture(autouse=True)
def _clear_window_sticky():
    """兜底粘滞是模块级状态：每个用例前后清干净。"""
    now_playing._window_sticky = None
    yield
    now_playing._window_sticky = None


# ------------------------------------------------- 空闲降档（多宠物省开销）

def test_empty_sessions_slow_the_sampler_down():
    """一个会话都没有 → 连续空转超过宽限期后降档到慢节拍。"""
    now_playing._sample_empty_since = None
    assert now_playing._next_sample_interval(None, 100.0) == now_playing._SAMPLE_INTERVAL
    assert now_playing._next_sample_interval(None, 101.0) == now_playing._SAMPLE_INTERVAL
    slow = now_playing._next_sample_interval(
        None, 100.0 + now_playing._SAMPLE_EMPTY_GRACE
    )
    assert slow == now_playing._SAMPLE_IDLE_INTERVAL
    assert slow > now_playing._SAMPLE_INTERVAL


def test_playing_session_restores_fast_interval():
    """一有会话就立刻回到快节拍（不允许慢档拖住切歌/起播的发现）。"""
    now_playing._sample_empty_since = None
    now_playing._next_sample_interval(None, 100.0)
    now_playing._next_sample_interval(None, 100.0 + now_playing._SAMPLE_EMPTY_GRACE)
    assert now_playing._next_sample_interval(_playback(), 200.0) == now_playing._SAMPLE_INTERVAL
    # 会话消失后重新计时：先快节拍观察，再降档
    assert now_playing._next_sample_interval(None, 201.0) == now_playing._SAMPLE_INTERVAL


def test_sampler_loop_uses_adaptive_interval(monkeypatch):
    """采样循环必须真的用 _next_sample_interval 的返回值（不是写死常量）。"""
    intervals: list[float] = []

    def _fake_interval(value, now):
        intervals.append(now)
        return 0.01

    monkeypatch.setattr(now_playing, "_next_sample_interval", _fake_interval)
    monkeypatch.setattr(now_playing, "_IDLE_STOP", 5.0)
    monkeypatch.setattr(now_playing, "_read_blocking", lambda: _playback())

    now_playing.get_now_playing()
    assert _wait_for_sample(_playback()) is not None or intervals, "采样线程没跑起来"
    deadline = time.monotonic() + 2.0
    while not intervals and time.monotonic() < deadline:
        time.sleep(0.02)
    assert intervals, "采样循环没有调用自适应间隔计算"


# --------------------------------------- 播放器进程预判（承接上游 #134/#135）

def test_player_process_running_matches_by_process_name(monkeypatch):
    """进程在跑 -> True；不在 -> False（按 exe 名精确匹配）。"""
    import sys as _sys

    class _Proc:
        def __init__(self, name):
            self.info = {"name": name}

    fake = type("_FakePsutil", (), {
        "process_iter": staticmethod(lambda attrs=None: [_Proc("explorer.exe"), _Proc("cloudmusic.exe")])
    })
    monkeypatch.setitem(_sys.modules, "psutil", fake)

    assert now_playing.player_process_running("cloudmusic.exe") is True
    assert now_playing.player_process_running("CLOUDMUSIC.EXE") is True, "大小写不敏感"
    assert now_playing.player_process_running("qqmusic.exe") is False
    assert now_playing.player_process_running("") is False


def test_player_process_running_fails_open(monkeypatch):
    """psutil 缺失或扫描失败一律放行（宁可多试一次也不误伤播放器）。"""
    import sys as _sys

    monkeypatch.setitem(_sys.modules, "psutil", None)
    assert now_playing.player_process_running("cloudmusic.exe") is True

    class _Boom:
        @staticmethod
        def process_iter(attrs=None):
            raise OSError("access denied")

    monkeypatch.setitem(_sys.modules, "psutil", _Boom)
    assert now_playing.player_process_running("cloudmusic.exe") is True


def test_play_session_async_skips_winrt_when_process_absent(monkeypatch):
    """进程不在就直接返回 False：不发起那次可能永久阻塞的 SMTC 请求。"""
    import asyncio

    monkeypatch.setattr(now_playing, "player_process_running", lambda _exe: False)
    requested: list[int] = []

    class _Manager:
        @staticmethod
        async def request_async():
            requested.append(1)
            return None

    monkeypatch.setattr(now_playing, "_import_winrt", lambda: _Manager)

    assert asyncio.run(now_playing._play_session_async("cloudmusic.exe")) is False
    assert requested == [], "播放器进程不在时不该去碰 SMTC（它是会卡死的那一步）"


def _playback(title: str = "曲名", artist: str = "歌手") -> now_playing.Playback:
    return now_playing.Playback(
        track=now_playing.Track(title=title, artist=artist, playing=True),
        position=1.0,
        updated_at=time.monotonic(),
    )


def _wait_for_sample(want, *, timeout: float = 5.0):
    """轮询取值直到拿到目标样本（宽预算 + 事件驱动，不用固定 sleep 赌时序）。"""
    deadline = time.monotonic() + timeout
    value = None
    while time.monotonic() < deadline:
        value = now_playing.get_now_playing()
        if value is want:
            return value
        time.sleep(0.02)
    return value


# ------------------------------------------------------- 采样绝不阻塞调用方


def test_get_now_playing_never_blocks_when_sample_stalls(monkeypatch):
    """底层请求永不返回时，取值必须立刻返回——这是本次事故的回归点。"""
    entered = threading.Event()
    gate = threading.Event()

    def _stuck():
        entered.set()
        gate.wait(5.0)  # 模拟「请求永不完成」
        return None

    monkeypatch.setattr(now_playing, "_read_blocking", _stuck)
    try:
        t0 = time.monotonic()
        assert now_playing.get_now_playing() is None
        first = time.monotonic() - t0
        assert entered.wait(BUDGET), "采样线程未启动"
        # 卡住的采样在飞：后续取值同样不得等待
        t1 = time.monotonic()
        assert now_playing.get_now_playing() is None
        second = time.monotonic() - t1
    finally:
        gate.set()
    assert first < BUDGET, f"首次取值被阻塞 {first:.2f}s（GUI 线程会冻死）"
    assert second < BUDGET, f"采样卡住后取值被阻塞 {second:.2f}s"


def test_get_now_playing_publishes_background_sample(monkeypatch):
    """正常路径不变：后台采到的样本必须能被取值读到。"""
    sample = _playback()
    monkeypatch.setattr(now_playing, "_read_blocking", lambda: sample)
    assert _wait_for_sample(sample) is sample


def test_stalled_sampler_is_abandoned_and_recovers(monkeypatch):
    """采样线程卡死后必须被弃用重开，且陈旧结果不得发布。"""
    monkeypatch.setattr(now_playing, "_SAMPLE_STALL_LIMIT", 0.2)
    monkeypatch.setattr(now_playing, "_RESTART_COOLDOWN", 0.0)
    # 本用例只考「摘牌重开」，不考 SMTC 退避：关掉退避让新线程立刻重试 SMTC。
    monkeypatch.setattr(now_playing, "_SMTC_BACKOFF_S", 0.0)
    state = {"stuck": True}
    entered = threading.Event()
    sample = _playback()

    def _read():
        if state["stuck"]:
            entered.set()
            threading.Event().wait(5.0)
            return None
        return sample

    monkeypatch.setattr(now_playing, "_read_blocking", _read)
    assert now_playing.get_now_playing() is None
    assert entered.wait(BUDGET), "采样线程未启动"

    # 第一个采样线程永久卡住 → 监管方弃用它并起新线程，新线程给出样本
    state["stuck"] = False
    assert _wait_for_sample(sample) is sample, "卡死的采样线程未被弃用重启"


def test_sampler_idles_out_without_requests(monkeypatch):
    """调用方不再取值（歌词关/窗口隐藏）时，采样线程必须自行退出。"""
    monkeypatch.setattr(now_playing, "_IDLE_STOP", 0.2)
    monkeypatch.setattr(now_playing, "_SAMPLE_INTERVAL", 0.02)
    monkeypatch.setattr(now_playing, "_read_blocking", lambda: _playback())
    now_playing.get_now_playing()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        thread = now_playing._sample_thread
        if thread is None or not thread.is_alive():
            return
        time.sleep(0.02)
    pytest.fail("采样线程在无人取值后仍在轮询")


# --------------------------------------------------- 用户操作同样必须有界


def test_run_bounded_returns_default_when_work_never_finishes(monkeypatch):
    """有界执行器：工作永不结束时按预算返回默认值（跨平台覆盖机制本身）。"""
    monkeypatch.setattr(now_playing, "_ACTION_TIMEOUT", 0.2)
    gate = threading.Event()
    try:
        t0 = time.monotonic()
        assert now_playing._run_bounded(lambda: gate.wait(5.0), "default") == "default"
        elapsed = time.monotonic() - t0
    finally:
        gate.set()
    assert elapsed < BUDGET, f"有界执行器超预算 {elapsed:.2f}s"


def test_run_bounded_returns_value_when_work_finishes():
    """正常路径不变：工作正常结束时必须拿到真实返回值。"""
    assert now_playing._run_bounded(lambda: "ok", "default") == "ok"


@pytest.mark.skipif(sys.platform != "win32", reason="SMTC 只在 Windows 存在")
@pytest.mark.parametrize(
    "invoke",
    [
        pytest.param(lambda: now_playing.toggle_play_pause(), id="toggle_play_pause"),
        pytest.param(lambda: now_playing.skip_track("next"), id="skip_track"),
        pytest.param(
            lambda: now_playing.play_session_for("cloudmusic.exe"),
            id="play_session_for",
        ),
    ],
)
def test_player_actions_are_bounded_when_winrt_stalls(monkeypatch, invoke):
    """右键菜单里的播放器操作也不能被卡死的 SMTC 拖住 GUI。"""
    monkeypatch.setattr(now_playing, "_ACTION_TIMEOUT", 0.2)

    async def _never(*_args, **_kwargs):
        await asyncio.Event().wait()

    for name in (
        "_play_session_async",
        "_skip_async",
        "_play_pause_async",
    ):
        monkeypatch.setattr(now_playing, name, _never)

    t0 = time.monotonic()
    assert invoke() is False
    elapsed = time.monotonic() - t0
    assert elapsed < BUDGET, f"播放器操作被阻塞 {elapsed:.2f}s"


# ------------------------------------------- 窗口标题兜底（SMTC 卡死时仍能知道在放什么）


def _fallback_playback(title: str = "夜曲", artist: str = "周杰伦"):
    return now_playing.Playback(
        track=now_playing.Track(title=title, artist=artist, playing=True),
        position=None,  # 窗口兜底拿不到进度
        updated_at=time.monotonic(),
    )


def test_smtc_sample_wins_when_healthy(monkeypatch):
    """SMTC 健康时优先它（有播放进度），兜底不得顶掉。"""
    smtc = _playback("SMTC曲", "SMTC歌手")
    monkeypatch.setattr(now_playing, "_read_blocking", lambda: smtc)
    monkeypatch.setattr(
        now_playing, "_read_window_media", lambda: _fallback_playback("窗口曲", "窗口歌手")
    )
    assert _wait_for_sample(smtc) is smtc
    assert now_playing.get_now_playing() is smtc


def test_window_fallback_used_when_smtc_returns_nothing(monkeypatch):
    """SMTC 正常但查不到会话（未接入/无会话）→ 用窗口标题兜底。"""
    monkeypatch.setattr(now_playing, "_read_blocking", lambda: None)
    fallback = _fallback_playback()
    monkeypatch.setattr(now_playing, "_read_window_media", lambda: fallback)
    assert _wait_for_sample(fallback) is fallback


def test_window_fallback_takes_over_after_smtc_wedges(monkeypatch):
    """SMTC 卡死被摘牌后进入退避：改为只用窗口兜底，且退避期内不再触 SMTC。"""
    monkeypatch.setattr(now_playing, "_SAMPLE_STALL_LIMIT", 0.2)
    monkeypatch.setattr(now_playing, "_RESTART_COOLDOWN", 0.0)
    monkeypatch.setattr(now_playing, "_SAMPLE_INTERVAL", 0.05)
    entered = threading.Event()
    calls = {"smtc": 0}

    def _stuck():
        calls["smtc"] += 1
        entered.set()
        threading.Event().wait(5.0)  # 模拟 request_async 永不返回
        return None

    monkeypatch.setattr(now_playing, "_read_blocking", _stuck)
    fallback = _fallback_playback()
    monkeypatch.setattr(now_playing, "_read_window_media", lambda: fallback)

    assert now_playing.get_now_playing() is None
    assert entered.wait(BUDGET), "采样线程未启动"

    # 卡死被监管方摘牌 → 之后只走兜底
    assert _wait_for_sample(fallback) is fallback
    smtc_calls = calls["smtc"]
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        assert now_playing.get_now_playing() is fallback
        time.sleep(0.05)
    assert calls["smtc"] == smtc_calls, "退避窗口内不得再次调用 SMTC（会被再次卡死）"


def test_window_fallback_sticks_through_transient_probe_failure(monkeypatch):
    """兜底探针偶发失败（播放器切窗口/改标题）时沿用上一次结果，不返回 None。

    回归（实机 2026-09-17）：单拍 None 会让控制器复位整条链路 → 重新取词、歌词时间轴
    从 0 秒重来，用户看到的是「歌词显示一半就只剩歌名，然后从头再唱」。
    """
    calls = {"n": 0}

    def _probe():
        calls["n"] += 1
        if calls["n"] == 1:
            return ("孤独患者", "陈奕迅", True)
        return None

    monkeypatch.setattr(now_playing.media_window, "read_window_media", _probe)

    first = now_playing._read_window_media()
    assert first is not None and first.track.title == "孤独患者"
    # 探针开始失败：粘滞期内必须继续给出同一首（否则控制器会复位）
    assert now_playing._read_window_media().track.title == "孤独患者"
    assert now_playing._read_window_media().track.title == "孤独患者"

    # 超过粘滞时长才允许返回 None（播放器真的关了）。用负值：Windows 上
    # time.monotonic() 分辨率约 15.6ms，紧跟其后的调用可能算出恰好 0.0。
    monkeypatch.setattr(now_playing, "_WINDOW_STICKY_S", -1.0)
    assert now_playing._read_window_media() is None


def test_smtc_is_rate_limited(monkeypatch):
    """SMTC 不能被采样节拍（0.3s）带着高频调用——实机怀疑这就是把系统 SMTC 打挂的原因。"""
    clock = {"t": 1000.0}
    monkeypatch.setattr(now_playing, "_now", lambda: clock["t"])
    monkeypatch.setattr(now_playing, "_SMTC_MIN_INTERVAL_S", 1.0)
    calls = {"n": 0}

    def _smtc():
        calls["n"] += 1
        return None  # 查不到 → 走窗口兜底（这里不关心结果）

    monkeypatch.setattr(now_playing, "_read_blocking", _smtc)

    for _ in range(10):  # 10 拍 × 0.1s = 1.0s
        now_playing._sample_once()
        clock["t"] += 0.1
    assert calls["n"] == 1, "1 秒内只允许调一次 SMTC，实际 %d 次" % calls["n"]

    clock["t"] += 1.0
    now_playing._sample_once()
    assert calls["n"] == 2


def test_smtc_retried_after_backoff_expires(monkeypatch):
    """退避到期后应重新尝试 SMTC，以便它恢复时自动切回（有进度的来源）。"""
    monkeypatch.setattr(now_playing, "_SAMPLE_STALL_LIMIT", 0.2)
    monkeypatch.setattr(now_playing, "_RESTART_COOLDOWN", 0.0)
    monkeypatch.setattr(now_playing, "_SAMPLE_INTERVAL", 0.05)
    monkeypatch.setattr(now_playing, "_SMTC_BACKOFF_S", 0.3)
    gate = threading.Event()

    def _stuck_then_ok():
        if not gate.is_set():
            gate.set()
            threading.Event().wait(5.0)
            return None
        return _playback("SMTC曲", "SMTC歌手")

    monkeypatch.setattr(now_playing, "_read_blocking", _stuck_then_ok)
    monkeypatch.setattr(
        now_playing, "_read_window_media", lambda: _fallback_playback("窗口曲", "窗口歌手")
    )

    assert now_playing.get_now_playing() is None
    # 先落到兜底
    assert _wait_for_sample(_fallback_playback("窗口曲", "窗口歌手"), timeout=5.0) is not None
    # 退避到期后重试 SMTC：拿到带进度的样本
    want = now_playing.Playback(
        track=now_playing.Track(title="SMTC曲", artist="SMTC歌手", playing=True),
        position=1.0,
        updated_at=time.monotonic(),
    )
    deadline = time.monotonic() + 6.0
    got = None
    while time.monotonic() < deadline:
        got = now_playing.get_now_playing()
        if got is not None and got.track.title == "SMTC曲":
            break
        time.sleep(0.05)
    assert got is not None and got.track.title == "SMTC曲", "退避到期后未重试 SMTC"
    del want
