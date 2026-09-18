# -*- coding: utf-8 -*-
"""后台音乐/音频播放检测（Windows）。

通过 pycaw 读取默认音频输出设备的瞬时峰值电平：只要系统正在输出声音
（音乐、视频、游戏等），峰值就会高于静音阈值。桌宠据此自动播放唱歌动画；
阈值设置得较低，避免只有极微弱提示音时频繁触发。
"""

from __future__ import annotations

import sys

# 峰值电平阈值：0.0=静音，1.0=满幅。取 0.02 过滤极低电平/数字静音。
MUSIC_PEAK_THRESHOLD = 0.02


_meter = None


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
    """返回系统当前是否正在输出音频（Windows；其他平台恒 False）。"""
    if sys.platform != 'win32':
        return False
    try:
        meter = _get_meter()
        if meter is None:
            return False
        return meter.GetPeakValue() > MUSIC_PEAK_THRESHOLD
    except Exception:
        # 无 pycaw / 音频设备不可用 / COM 初始化失败时按“未播放”处理，不打扰用户
        return False
