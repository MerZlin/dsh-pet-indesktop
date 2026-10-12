# -*- coding: utf-8 -*-
"""SMTC 会话探针的**线程与生命周期契约**测试。

为什么单独一个文件：门（``music_detect.is_music_playing``）跑在 Qt 主线程，
而查 SMTC 走 ``asyncio.run()``——主线程同步查 WinRT 实测会永久阻塞窗口。这条
约束靠"实现看起来是这样"守不住：把 ``_ensure_session_probe`` 换成同步查、把空闲
退出改成永不退出，原来的行为用例**全部照绿**（本文件就是为堵这个盲区补的）。

四个契约（每条都做过消融验证，见 `docs/PR-REPORT-SMTC-MEDIA-TYPE-2026-10-06.md`）：

1. OS 请求发生在探针线程，**调用线程自己一次 SMTC 都不查**；
2. 缓存新鲜时，门读判定是**纯缓存读**，零 OS 请求；
3. 停止消费后，探针按空闲期限**自行退出**（不留常驻轮询）；
4. 采样异常 / 采样挂起都不能把探针钉死：异常按"没有会话"放行并继续存活，
   挂起由有界超时收口，两条路径最终都能按空闲期限退出。
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from pet import music_detect, now_playing

MUSIC = 1
PLAYING = 4

# 测试用节奏：把模块常量调到毫秒级，避免用真实 2s/8s 期限拖慢套件。
# 探针循环每次都按名字读这些常量，所以 patch 立即生效。
FAST_TTL = 0.02
FAST_IDLE = 0.2
FAST_TIMEOUT = 0.15

# 模块**未被 patch 时**的真实默认值：在 import 期抓一份（见常量契约用例——
# 行为用例靠 monkeypatch 调快期限，所以默认值本身得单独钉）。
DEFAULT_PROBE_CONSTANTS = (
    now_playing.SESSION_PROBE_TTL_SECONDS,
    now_playing.SESSION_PROBE_IDLE_EXIT_SECONDS,
    now_playing.SESSION_PROBE_TIMEOUT_SECONDS,
    now_playing.SESSION_VERDICT_STALE_SECONDS,
)


@pytest.fixture(autouse=True)
def _fast_probe(monkeypatch):
    monkeypatch.setattr(now_playing, "SESSION_PROBE_TTL_SECONDS", FAST_TTL)
    monkeypatch.setattr(now_playing, "SESSION_PROBE_IDLE_EXIT_SECONDS", FAST_IDLE)
    monkeypatch.setattr(now_playing, "SESSION_PROBE_TIMEOUT_SECONDS", FAST_TIMEOUT)
    now_playing.reset_session_probe()
    music_detect.set_browser_media_enabled(False)
    yield
    now_playing.reset_session_probe()
    music_detect.set_browser_media_enabled(False)


class _Sec:
    def __init__(self, value):
        self._value = value

    def total_seconds(self):
        return self._value


class _Info:
    def __init__(self, status, media_type):
        self.playback_status = status
        self.playback_type = media_type


class _Props:
    def __init__(self):
        self.title = "夜曲"
        self.artist = "周杰伦"
        self.album_title = ""
        self.playback_type = MUSIC


class _Session:
    def __init__(self, app_id="cloudmusic.exe", status=PLAYING, media_type=MUSIC):
        self.source_app_user_model_id = app_id
        self._status = status
        self._media_type = media_type

    def get_playback_info(self):
        return _Info(self._status, self._media_type)

    def get_timeline_properties(self):
        return type(
            "T", (), {"position": _Sec(1.0), "end_time": _Sec(200.0), "last_updated_time": None}
        )()

    async def try_get_media_properties_async(self):
        return _Props()


class _Manager:
    def __init__(self, sessions):
        self._sessions = sessions

    def get_sessions(self):
        return list(self._sessions)


def _install(monkeypatch, *, sessions=None, mode="ok", records=None):
    """装一个假 WinRT：记录「哪个线程发出了 OS 请求」/ 按 mode 制造异常或挂起。"""
    sessions = [_Session()] if sessions is None else sessions
    records = [] if records is None else records

    class Cls:
        @staticmethod
        async def request_async():
            records.append(threading.current_thread().name)
            if mode == "raise":
                raise RuntimeError("simulated OS failure")
            if mode == "hang":
                await asyncio.Event().wait()      # 永不完成的请求
            return _Manager(sessions)

    monkeypatch.setattr(now_playing, "_import_winrt", lambda: Cls)
    return records


def _wait_until(predicate, *, timeout=5.0, interval=0.01):
    """宽预算轮询（事件同步，不赌固定 sleep）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _probe_alive() -> bool:
    thread = now_playing._probe_thread
    return thread is not None and thread.is_alive()


def test_probe_constants_are_finite_and_ordered():
    """常量契约：三个期限都必须是有限的、且关系合理。

    为什么单独钉：行为用例靠 monkeypatch 把期限调快（否则套件要等真实的 2s/8s），
    所以"默认值被改成 ``math.inf``"这种退化**不会**被行为用例发现——而它正好等价于
    "探针永不退出"。这里直接对常量本身取断言，堵掉这个盲区。
    """
    import math

    # 用 import 期抓下的真实默认值，而不是当前值——当前值已被上面的快节奏夹具改过。
    ttl, idle, timeout, stale = DEFAULT_PROBE_CONSTANTS

    for name, value in (
        ("TTL", ttl), ("空闲退出", idle), ("采样超时", timeout), ("缓存过期", stale)
    ):
        assert math.isfinite(value), f"{name} 必须是有限值（inf = 永不退出）"
        assert value > 0, f"{name} 必须为正"
    assert ttl < idle, "采样间隔必须小于空闲退出期限，否则探针永远来不及退出"
    assert timeout < idle, "单次采样超时必须小于空闲退出期限，否则挂起会顶掉退出"
    assert stale >= ttl, "缓存有效期不该短于一次采样间隔"


def test_os_request_happens_on_probe_thread_not_the_caller(monkeypatch):
    """契约 1：门被问时，OS 请求发生在探针线程，调用线程一次 SMTC 都不查。

    消融可判别：把 `_ensure_session_probe` 换成"当场同步采一次"，调用线程名就会
    出现在记录里，本用例立即红——这正是主线程阻塞窗口那条红线的守门人。
    """
    records = _install(monkeypatch)
    caller = threading.current_thread().name

    assert now_playing.song_session_accepted(False) is None, "首拍无数据 → 放行"
    assert _wait_until(lambda: records), "探针应在一个宽预算内完成首次采样"

    assert caller not in records, f"调用线程自己查了 SMTC：{records}"
    # CI can deschedule the caller across several valid 20ms samples.
    # This contract concerns thread ownership, not the number of samples.
    assert set(records) == {"smtc-session-probe"}, records


def test_fresh_cache_read_never_touches_the_os(monkeypatch):
    """契约 2：缓存新鲜时门读判定是纯缓存读，零 OS 请求。"""
    records = _install(monkeypatch)
    now_playing.refresh_session_probe_once()
    records.clear()

    for _ in range(20):
        assert now_playing.song_session_accepted(False) is True

    assert records == [], "缓存新鲜时不许再发 OS 请求"


def test_probe_exits_after_idle_deadline(monkeypatch):
    """契约 3：停止消费后探针按空闲期限自行退出（不留常驻轮询）。

    消融可判别：把 `SESSION_PROBE_IDLE_EXIT_SECONDS` 改成 ``math.inf``（或删掉
    退出分支），线程会一直活着，本用例红。
    """
    _install(monkeypatch)

    for _ in range(5):
        now_playing.song_session_accepted(False)
        time.sleep(FAST_TTL)
    assert _probe_alive(), "消费期间探针应在跑"

    assert _wait_until(lambda: not _probe_alive(), timeout=5.0), "空闲后探针应自行退出"

    with now_playing._probe_lock:
        assert now_playing._session_facts is None, "退出时缓存要清空（下次重新采）"


def test_probe_survives_sampling_errors_and_still_exits(monkeypatch):
    """契约 4a：采样异常不能让探针死掉，也不能挡住空闲退出。

    异常按"没有会话"处理（放行），线程继续服务；停止消费后照常退出。
    """
    records = _install(monkeypatch, mode="raise")

    for _ in range(5):
        now_playing.song_session_accepted(False)
        time.sleep(FAST_TTL)

    assert _wait_until(lambda: len(records) >= 2), "异常后探针必须继续采（不是一次就死）"
    assert _probe_alive(), "采样异常不许把探针线程打死"
    assert now_playing.song_session_accepted(False) is True, "无会话事实 → 放行"

    assert _wait_until(lambda: not _probe_alive(), timeout=5.0), "空闲后仍应退出"


def test_hung_os_request_is_bounded_and_probe_can_exit(monkeypatch):
    """契约 4b：挂起的 OS 请求由有界超时收口，探针不会被困在采样里。

    修前实测：请求永不返回时线程钉死在 `asyncio.run`，停止消费后也不退出
    （只能等进程结束）。修后：超时 → 按"没有会话"写缓存 → 回到循环 → 空闲退出。
    """
    _install(monkeypatch, mode="hang")

    start = time.monotonic()
    while time.monotonic() - start < 0.5:      # 持续消费，确保探针进过采样
        now_playing.song_session_accepted(False)
        time.sleep(FAST_TTL)
    assert _probe_alive(), "挂起期间线程仍在（但必须有界收口）"

    # 单次采样必须在超时量级内返回。放工作线程里等：没有有界超时时它会永远不返回，
    # 这里在预算内断言"跑完了"，用例快速失败而不是把整个测试进程挂住。
    done: list = []
    worker = threading.Thread(
        target=lambda: done.append(now_playing.refresh_session_probe_once()),
        daemon=True,
    )
    t0 = time.monotonic()
    worker.start()
    worker.join(timeout=FAST_TIMEOUT * 20)
    elapsed = time.monotonic() - t0
    assert not worker.is_alive(), f"单次采样未被有界超时收口（已等 {elapsed:.2f}s）"
    assert elapsed < FAST_TIMEOUT * 10, f"收口太慢：{elapsed:.3f}s"

    assert _wait_until(lambda: not _probe_alive(), timeout=5.0), "空闲后仍应退出"


def test_probe_restarts_after_exit(monkeypatch):
    """退出不是"永久停摆"：再有人问就重新拉起（功能反复开关的路径）。"""
    _install(monkeypatch)

    now_playing.song_session_accepted(False)
    assert _wait_until(lambda: not _probe_alive(), timeout=5.0)

    now_playing.song_session_accepted(False)
    assert _wait_until(_probe_alive), "再次被问时必须重新拉起探针"


def test_gate_uses_probe_facts_and_takes_effect_without_resampling(monkeypatch):
    """端到端：真实探针线程写下的会话事实，直接决定门的否决（开关翻转即时生效）。"""
    _install(monkeypatch, sessions=[_Session("MSEdge", PLAYING, MUSIC)])

    for _ in range(5):
        now_playing.song_session_accepted(False)
        time.sleep(FAST_TTL)

    assert _wait_until(
        lambda: now_playing.song_session_accepted(False) is False
    ), "浏览器会话 + 开关关 → 探针应给出否决"
    assert now_playing.song_session_accepted(True) is True, "开关打开 → 同一份事实判为放行"
