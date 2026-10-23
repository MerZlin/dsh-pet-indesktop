# -*- coding: utf-8 -*-
"""音频峰值探针的**线程与生命周期契约**测试。

为什么单独一个文件：唱歌动画门（``music_detect.is_music_playing``）跑在 Qt
主线程（音乐唱歌定时器槽），而读系统峰值是一次 pycaw COM 调用——实测设备
繁忙时单次能阻塞到 250ms，把那一帧拖长（issue #339：tick p99 从 31ms 跳到
250ms，窗口隐藏时同样复现）。这条约束靠"实现看起来是这样"守不住：把
``peak_value`` 换回同步调 ``GetPeakValue``，原来的行为用例**全部照绿**
（本文件就是为堵这个盲区补的，与 ``test_now_playing_session_probe.py`` 同构）。

四个契约（每条都做过消融验证，见 `docs/PR-REPORT-PEAK-PROBE-2026-10-23.md`）：

1. COM 调用发生在探针线程，**调用线程自己一次 COM 都不调**；
2. 缓存新鲜时，门读峰值是**纯缓存读**，零 COM 调用；
3. 停止消费后，探针按空闲期限**自行退出**（不留常驻轮询）；
4. 采样异常 / meter 不可用不能把探针钉死：按"没有声音"处理并继续存活，
   最终仍能按空闲期限退出。

全部用例不碰真实音频设备：只替换 ``_get_meter`` 这个 OS 边界，产品代码
（缓存、线程、判定）全走真路径。
"""
from __future__ import annotations

import math
import threading
import time

import pytest

from pet.music import detect as music_detect

# 测试用节奏：把模块常量调到毫秒级，避免用真实 1s/8s 期限拖慢套件。
# 探针循环每次都按名字读这些常量，所以 patch 立即生效。
FAST_INTERVAL = 0.02
FAST_IDLE = 0.2
FAST_STALE = 0.06

# 模块**未被 patch 时**的真实默认值：在 import 期抓一份（见常量契约用例——
# 行为用例靠 monkeypatch 调快期限，所以默认值本身得单独钉）。
DEFAULT_PROBE_CONSTANTS = (
    music_detect.PEAK_PROBE_INTERVAL_SECONDS,
    music_detect.PEAK_PROBE_IDLE_EXIT_SECONDS,
    music_detect.PEAK_VERDICT_STALE_SECONDS,
    music_detect.PEAK_METER_RETRY_SECONDS,
)


@pytest.fixture(autouse=True)
def _fast_probe(monkeypatch):
    """Windows 分支 + 毫秒级期限 + 每个用例前后清掉探针（防跨用例串味）。"""
    monkeypatch.setattr(music_detect.sys, "platform", "win32")
    monkeypatch.setattr(music_detect, "PEAK_PROBE_INTERVAL_SECONDS", FAST_INTERVAL)
    monkeypatch.setattr(music_detect, "PEAK_PROBE_IDLE_EXIT_SECONDS", FAST_IDLE)
    monkeypatch.setattr(music_detect, "PEAK_VERDICT_STALE_SECONDS", FAST_STALE)
    music_detect.reset_peak_probe()
    yield
    music_detect.reset_peak_probe()


def _install(monkeypatch, *, meter_value=0.5, records=None):
    """装一个假 meter，记录「哪个线程读了峰值」。

    ``meter_value=None`` 模拟"meter 建不出来"（无 pycaw / 无音频设备）。
    """
    records = [] if records is None else records

    class _Meter:
        def GetPeakValue(self) -> float:
            return meter_value

    def _get_meter():
        records.append(threading.current_thread().name)
        return None if meter_value is None else _Meter()

    monkeypatch.setattr(music_detect, "_get_meter", _get_meter)
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
    thread = music_detect._peak_thread
    return thread is not None and thread.is_alive()


def test_probe_constants_are_finite_and_ordered():
    """常量契约：期限都必须是有限值，且关系合理。

    为什么单独钉：行为用例靠 monkeypatch 把期限调快（否则套件要等真实的
    1s/8s），所以"默认值被改成 ``math.inf``"这种退化**不会**被行为用例发现——
    而它正好等价于"探针永不退出"。这里直接对常量本身取断言，堵掉这个盲区。
    """
    interval, idle, stale, retry = DEFAULT_PROBE_CONSTANTS

    for name, value in (
        ("采样间隔", interval), ("空闲退出", idle), ("缓存过期", stale), ("meter 重试", retry)
    ):
        assert math.isfinite(value), f"{name} 必须是有限值（inf = 永不退出）"
        assert value > 0, f"{name} 必须为正"
    assert interval < idle, "采样间隔必须小于空闲退出期限，否则探针永远来不及退出"
    assert stale >= interval, "缓存有效期不该短于一次采样间隔"


def test_com_call_happens_on_probe_thread_not_the_caller(monkeypatch):
    """契约 1：门被问时，COM 调用发生在探针线程，调用线程一次都不调。

    消融可判别：把 ``peak_value`` 换回"当场同步读 GetPeakValue"，调用线程名
    就会出现在记录里，本用例立即红——这正是主线程阻塞窗口那条红线的守门人。
    首拍没有采样结果，门必须按"没有声音"处理（宁可晚一拍唱，也不在主线程读）。
    """
    records = _install(monkeypatch)
    monkeypatch.setattr(music_detect, "session_vetoed", lambda: False)
    caller = threading.current_thread().name

    assert music_detect.is_music_playing() is False, "首拍无数据 → 按没有声音处理"
    assert _wait_until(lambda: records), "探针应在一个宽预算内完成首次采样"
    assert caller not in records, f"调用线程自己读了峰值：{records}"
    assert set(records) == {"audio-peak-probe"}, records


def test_fresh_cache_read_never_touches_the_com(monkeypatch):
    """契约 2：缓存新鲜时门读峰值是纯缓存读，零 COM 调用。"""
    records = _install(monkeypatch)
    monkeypatch.setattr(music_detect, "session_vetoed", lambda: False)
    music_detect.refresh_peak_once()
    records.clear()

    for _ in range(20):
        assert music_detect.is_music_playing() is True

    assert records == [], "缓存新鲜时不许再读 COM"


def test_probe_exits_after_idle_deadline(monkeypatch):
    """契约 3：停止消费后探针按空闲期限自行退出（不留常驻轮询）。

    消融可判别：把 ``PEAK_PROBE_IDLE_EXIT_SECONDS`` 改成 ``math.inf``（或删掉
    退出分支），线程会一直活着，本用例红。
    """
    _install(monkeypatch)

    for _ in range(5):
        music_detect.peak_value()
        time.sleep(FAST_INTERVAL)
    assert _probe_alive(), "消费期间探针应在跑"

    assert _wait_until(lambda: not _probe_alive(), timeout=5.0), "空闲后探针应自行退出"

    with music_detect._peak_lock:
        assert music_detect._peak_value is None, "退出时缓存要清空（下次重新采）"


def test_probe_survives_sampling_errors_and_still_exits(monkeypatch):
    """契约 4：采样异常不能让探针死掉，也不能挡住空闲退出。

    meter 建不出来时按"没有声音"处理（不写缓存），线程继续服务；停止消费后
    照常退出。消融可判别：让异常打死线程，本用例红。
    """
    records = _install(monkeypatch, meter_value=None)

    for _ in range(5):
        music_detect.peak_value()
        time.sleep(FAST_INTERVAL)

    assert _wait_until(lambda: len(records) >= 2), "异常后探针必须继续采（不是一次就死）"
    assert _probe_alive(), "采样异常不许把探针线程打死"
    assert music_detect.is_music_playing() is False, "没有峰值 → 不唱"

    assert _wait_until(lambda: not _probe_alive(), timeout=5.0), "空闲后仍应退出"


def test_probe_restarts_after_exit(monkeypatch):
    """退出不是"永久停摆"：再有人问就重新拉起（功能反复开关的路径）。"""
    _install(monkeypatch)

    music_detect.peak_value()
    assert _wait_until(lambda: not _probe_alive(), timeout=5.0)

    music_detect.peak_value()
    assert _wait_until(_probe_alive), "再次被问时必须重新拉起探针"


def test_stale_cache_is_treated_as_no_sound(monkeypatch):
    """缓存过期后门按"没有声音"处理——探针停摆时唱歌应自行退出，而不是拿旧值唱。

    这里把 ``_ensure_peak_probe`` 换成 no-op 模拟"探针停摆、没人补数"：否则
    过期时门会拉起探针，而探针启动即采一拍，用例自己就会和它抢时序。
    """
    _install(monkeypatch)
    monkeypatch.setattr(music_detect, "session_vetoed", lambda: False)
    monkeypatch.setattr(music_detect, "_ensure_peak_probe", lambda: None)
    music_detect.refresh_peak_once()
    with music_detect._peak_lock:
        music_detect._peak_at = time.monotonic() - FAST_STALE * 2

    assert music_detect.peak_value() is None
    assert music_detect.is_music_playing() is False


def test_silent_system_is_not_music(monkeypatch):
    """峰值在阈值以下（数字静音）不算音乐。"""
    _install(monkeypatch, meter_value=0.0)
    monkeypatch.setattr(music_detect, "session_vetoed", lambda: False)
    music_detect.refresh_peak_once()

    assert music_detect.is_music_playing() is False


def test_session_veto_still_beats_peak(monkeypatch):
    """否决叠加：峰值再高，当前会话被否决（视频/浏览器）也不唱。"""
    _install(monkeypatch)
    monkeypatch.setattr(music_detect, "session_vetoed", lambda: True)
    music_detect.refresh_peak_once()

    assert music_detect.is_music_playing() is False


def test_non_windows_short_circuits_without_starting_probe(monkeypatch):
    """非 Windows：恒 false，且不拉起探针线程（不留无意义的常驻线程）。"""
    monkeypatch.setattr(music_detect.sys, "platform", "linux")

    assert music_detect.peak_value() is None
    assert music_detect.is_music_playing() is False
    assert music_detect._peak_thread is None
