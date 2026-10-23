# -*- coding: utf-8 -*-
"""后台音乐/音频播放检测（Windows）。

通过 pycaw 读取默认音频输出设备的瞬时峰值电平：系统正在输出声音（音乐、
视频、游戏等）峰值就会高于静音阈值。峰值只能说明"有声音在出"，所以还叠加
一道**否决**：当前 SMTC 会话被明确判定为视频（``playback_type=VIDEO``），或
（设置里关掉"浏览器媒体会话参与歌词"时）来自已知浏览器，就不触发唱歌动画。

为什么要按来源否决而不是只看类型：浏览器把网页里的**视频**也上报成
``MUSIC(1)``（2026-10 实测 Edge / Chromium 播放 B 站视频，类型恒为 1，且
artist/album/subtitle 全空，SMTC 里没有任何字段能把它与网页音乐分开）。

没有 SMTC 会话时维持旧行为（只看峰值）：游戏这类不上报会话的声音照常触发。
判定结果由 ``now_playing`` 的常驻探针在非主线程产出，本模块只读缓存——主线程
同步查 SMTC 实测会永久阻塞窗口（见 ``music_lyric_controller`` 采样线程说明）。

**峰值本身也走同一条后台探针**（2026-10，issue #339）：``GetPeakValue()`` 是
一次 pycaw COM 调用，正常情况下只有几十微秒，但音频设备繁忙 / 驱动重入时实测
能阻塞到 **250ms** 量级。它原先每秒一次跑在 Qt 主线程（音乐唱歌定时器槽）上，
把那一帧拖长，用户侧表现为"桌宠每隔一会儿顿一下"（tick p99 从 31ms 跳到 250ms，
窗口隐藏时同样复现，所以与渲染无关）。现在 COM 只发生在常驻探针线程，主线程
每拍只读缓存（一次加锁读，微秒级）；代价是最多晚一拍（1s）才看到峰值变化。
"""

from __future__ import annotations

import sys
import threading
import time

# 峰值电平阈值：0.0=静音，1.0=满幅。取 0.02 过滤极低电平/数字静音。
MUSIC_PEAK_THRESHOLD = 0.02

# ---------------------------------------------------------------- 峰值探针
#
# 采样间隔：与唱歌轮询（``window.py`` / ``overlay_shell`` 的 1s 定时器）同拍，
# 兼顾时效与开销——再快也只是多付 COM 开销，主线程读的仍是缓存。
PEAK_PROBE_INTERVAL_SECONDS = 1.0
# 缓存超过该年龄即视为过期（按"没有声音"处理）：宁可晚一拍才唱，也不拿几拍
# 之前的旧峰值误唱。探针线程停摆时，唱歌会在该期限后自行退出（fail-safe 方向）。
PEAK_VERDICT_STALE_SECONDS = 3.0
# 无人询问超过该时长，探针线程自行退出并清缓存：功能关掉后不留常驻轮询。
PEAK_PROBE_IDLE_EXIT_SECONDS = 8.0
# meter 创建失败后的重试间隔：失败时别每拍都 Activate——每次 Activate 都是新的
# COM 接口句柄，长时间累积会崩（见 :func:`_get_meter` 的既有说明）。探针线程
# 不阻塞主线程，这里的重试只是省句柄，不必急于恢复。
PEAK_METER_RETRY_SECONDS = 10.0


_meter = None
_meter_failed_at = 0.0

# 「浏览器媒体会话参与歌词与唱歌」：由宿主每拍按配置推送（见
# ``set_browser_media_enabled``）。默认关闭——浏览器对视频恒报音乐类型，
# 不否决就会对着 B 站视频唱《视频标题》。
_browser_media_enabled = False

# 探针线程与它的缓存。缓存只存**峰值**（不是最终判定）：判定还取决于
# ``session_vetoed()``，它在读取时才参与，所以开关改动下一拍就生效。
_peak_lock = threading.Lock()
_peak_thread: threading.Thread | None = None
_peak_wake: threading.Event | None = None
_peak_stop: threading.Event | None = None
_peak_value: float | None = None  # None = 还没有采样结果
_peak_at = 0.0
_peak_last_asked = 0.0


def set_browser_media_enabled(on: bool) -> None:
    """推送「浏览器媒体会话是否参与歌词/唱歌」开关（宿主在轮询里按配置更新）。

    刻意做成"每拍推送"而不是一次性初始化：开关在设置页可以随时改，读配置的
    地方只有宿主（``window_alerts.check_music_sing`` 有 cfg），多存一份本地
    副本就多一处会过期的状态。
    """
    global _browser_media_enabled
    _browser_media_enabled = bool(on)


def browser_media_enabled() -> bool:
    """当前推送的浏览器开关值（默认 False）。"""
    return _browser_media_enabled


def session_vetoed() -> bool:
    """当前 SMTC 会话是否被唱歌链路**明确否决**（视频 / 开关关时的浏览器）。

    只读 ``now_playing`` 的后台探针缓存，**绝不在这里查 SMTC**：本函数在 Qt
    主线程（唱歌定时器槽）被调用。没有判定数据时返回 False（放行）——门故障
    不许误伤，宁可多唱一次。
    """
    try:
        from . import now_playing

        return now_playing.song_session_accepted(_browser_media_enabled) is False
    except Exception:
        return False


def _get_meter():
    """惰性创建并复用一个音频峰值检测 COM 对象（**只在探针线程调用**）。

    每次调用都重新 Activate 会持续产生 COM 接口句柄，长时间运行可能累积并
    导致崩溃；这里只初始化一次，失败后按 ``PEAK_METER_RETRY_SECONDS`` 节流
    重试（设备热插拔后仍能自己恢复）。

    对象只在创建它的探针线程里使用：COM 接口不跨线程共享（pycaw 的
    ``GetSpeakers()`` 依赖调用线程已初始化 COM apartment，见
    :func:`_peak_loop`）。
    """
    global _meter, _meter_failed_at
    if _meter is not None:
        return _meter
    now = time.monotonic()
    if _meter_failed_at and (now - _meter_failed_at) < PEAK_METER_RETRY_SECONDS:
        return None
    try:
        import comtypes
        from ctypes import POINTER, cast

        from pycaw.pycaw import AudioUtilities, IAudioMeterInformation

        device = AudioUtilities.GetSpeakers()._dev
        interface = device.Activate(
            IAudioMeterInformation._iid_, comtypes.CLSCTX_ALL, None
        )
        _meter = cast(interface, POINTER(IAudioMeterInformation))
    except Exception:
        _meter = None
        _meter_failed_at = now
    return _meter


def peak_value() -> float | None:
    """最近一次探针采到的系统峰值（**主线程安全**，只读缓存）。

    ``None`` 表示还没有采样结果、或缓存已过期——调用方按"没有声音"处理。
    首次调用会拉起后台探针线程，因此第一拍拿到的通常是 ``None``。
    """
    global _peak_last_asked
    if sys.platform != 'win32':
        return None
    now = time.monotonic()
    with _peak_lock:
        _peak_last_asked = now
        value = _peak_value
        fresh = (
            value is not None
            and (now - _peak_at) <= PEAK_VERDICT_STALE_SECONDS
        )
    if fresh:
        return value
    _ensure_peak_probe()
    return None


def refresh_peak_once() -> float | None:
    """立即采一次峰值并刷新缓存；返回本次读数（失败为 ``None``）。

    探针线程每拍调用它；测试可同步调用以确定性地填充缓存（不必等线程）。
    异常（含无 pycaw / 设备不可用）按"没有声音"处理：**不写缓存**，让旧值
    自然过期——meter 坏掉时宁可少唱一次，也不上报一个假的峰值。

    刻意不在这里处理 COM 初始化：apartment 归线程，见 :func:`_peak_loop`。
    """
    global _peak_value, _peak_at
    if sys.platform != 'win32':
        return None
    try:
        meter = _get_meter()
        value = float(meter.GetPeakValue()) if meter is not None else None
    except Exception:
        value = None
    if value is not None:
        with _peak_lock:
            _peak_value = value
            _peak_at = time.monotonic()
    return value


def _peak_loop(wake: threading.Event, stop: threading.Event) -> None:
    """探针线程主体：先采一拍 → 等一拍 → 看还有人问吗 → 再采。

    启动即采一次，避免"功能刚开还要白等一个周期"；随后按
    ``PEAK_PROBE_INTERVAL_SECONDS`` 节奏采样。空闲（没人读缓存）超过
    ``PEAK_PROBE_IDLE_EXIT_SECONDS`` 就退出——功能关掉后不留常驻轮询。

    COM apartment 在这个线程里初始化（``CoInitialize``），``_get_meter()``
    创建的对象也只在本线程使用；线程结束时 ``CoUninitialize``。非 Windows
    （测试/CI）没有 comtypes，跳过初始化，循环照常跑——``_get_meter()``
    自己会失败并按"没有声音"返回，生命周期契约仍可被测到。
    """
    global _peak_thread, _peak_wake, _peak_stop, _peak_value, _peak_at
    comtypes = None
    try:
        try:
            import comtypes as _comtypes

            comtypes = _comtypes
            comtypes.CoInitialize()
        except Exception:
            comtypes = None
        while not stop.is_set():
            with _peak_lock:
                idle_seconds = time.monotonic() - _peak_last_asked
            if idle_seconds > PEAK_PROBE_IDLE_EXIT_SECONDS:
                break
            refresh_peak_once()
            if wake.wait(PEAK_PROBE_INTERVAL_SECONDS):
                wake.clear()
    finally:
        if comtypes is not None:
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass
        with _peak_lock:
            # 只清自己的引用/缓存：别把后来重启的线程的现场抹掉。
            if _peak_thread is threading.current_thread():
                _peak_thread = None
                _peak_wake = None
                _peak_stop = None
                _peak_value = None
                _peak_at = 0.0


def _ensure_peak_probe() -> None:
    """确保有一条在跑的峰值探针线程（幂等；非 Windows 不拉起）。

    缓存的只是峰值，不含窗口/配置状态，所以这里不需要任何参数——开关改动
    不需要重启线程。
    """
    global _peak_thread, _peak_wake, _peak_stop
    if sys.platform != 'win32':
        return
    with _peak_lock:
        if _peak_thread is not None and _peak_thread.is_alive():
            return
        wake = threading.Event()
        stop = threading.Event()
        thread = threading.Thread(
            target=_peak_loop,
            args=(wake, stop),
            name="audio-peak-probe",
            daemon=True,
        )
        _peak_wake = wake
        _peak_stop = stop
        _peak_thread = thread
    thread.start()


def reset_peak_probe() -> None:
    """停掉峰值探针并清空缓存（测试用；产品路径不需要）。"""
    global _peak_thread, _peak_wake, _peak_stop, _peak_value, _peak_at
    with _peak_lock:
        wake, stop, thread = _peak_wake, _peak_stop, _peak_thread
    if stop is not None:
        stop.set()
    if wake is not None:
        wake.set()
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=1.0)
    with _peak_lock:
        _peak_thread = None
        _peak_wake = None
        _peak_stop = None
        _peak_value = None
        _peak_at = 0.0


def is_music_playing() -> bool:
    """系统是否正在播放**音乐**（Windows；其他平台恒 False）。

    两个条件同时成立才算：默认输出设备有声音，且当前 SMTC 会话没有被否决
    （见 :func:`session_vetoed`）。没有会话、或会话类型是音乐/未上报时，退化成
    只看峰值——即旧行为。

    **本函数在 Qt 主线程（唱歌定时器槽）调用，一次 COM 都不做**：峰值读
    :func:`peak_value` 的缓存，会话读 ``now_playing`` 的缓存。
    """
    if sys.platform != 'win32':
        return False
    try:
        value = peak_value()
    except Exception:
        return False
    if value is None or value <= MUSIC_PEAK_THRESHOLD:
        return False
    return not session_vetoed()
