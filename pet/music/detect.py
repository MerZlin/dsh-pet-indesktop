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
"""

from __future__ import annotations

import sys

# 峰值电平阈值：0.0=静音，1.0=满幅。取 0.02 过滤极低电平/数字静音。
MUSIC_PEAK_THRESHOLD = 0.02


_meter = None

# 「浏览器媒体会话参与歌词与唱歌」：由宿主每拍按配置推送（见
# ``set_browser_media_enabled``）。默认关闭——浏览器对视频恒报音乐类型，
# 不否决就会对着 B 站视频唱《视频标题》。
_browser_media_enabled = False


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
    """惰性创建并复用一个音频峰值检测 COM 对象。

    每次调用都重新 Activate 会持续产生 COM 接口句柄，长时间运行（如音乐自动
    唱歌每 4 秒检测一次）可能累积并导致崩溃；这里只初始化一次。
    """
    global _meter
    if _meter is not None:
        return _meter
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
    return _meter


def is_music_playing() -> bool:
    """系统是否正在播放**音乐**（Windows；其他平台恒 False）。

    两个条件同时成立才算：默认输出设备有声音，且当前 SMTC 会话没有被否决
    （见 :func:`session_vetoed`）。没有会话、或会话类型是音乐/未上报时，退化成
    只看峰值——即旧行为。
    """
    if sys.platform != 'win32':
        return False
    try:
        meter = _get_meter()
        if meter is None:
            return False
        if meter.GetPeakValue() <= MUSIC_PEAK_THRESHOLD:
            return False
    except Exception:
        # 无 pycaw / 音频设备不可用 / COM 初始化失败时按“未播放”处理，不打扰用户
        return False
    return not session_vetoed()
