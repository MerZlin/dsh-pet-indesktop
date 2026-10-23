# -*- coding: utf-8 -*-
"""波点的切歌 / 暂停通道（``pet/music/bodian_control.py``）与它的接线。

**为什么需要这一族**：波点不注册 SMTC，所以 ``now_playing.skip_track`` 对它恒为
``False``。唯一可达的控制通道是**系统媒体键**（实测 2026-10-23：波点不在前台时
也响应）。但媒体键是全局的——发错对象等于把用户正在听的另一个播放器换掉，所以
"什么情况下允许发"必须有测试钉住。

本文件钉三件事：

1. **结构体布局**：``sizeof(INPUT)`` 必须是 40（x64）。声明少了成员 → ``SendInput``
   返回 0 且 ``ERROR_INVALID_PARAMETER``，表现成"播放器不响应媒体键"这种假象。
2. **发送语义**：一次投递 down+up 两个事件；非 2 即失败，**不重试**（重试会连累
   别的播放器）。
3. **接线**：只有"最近一次采样来自波点"时才发媒体键；来源是网易云或没有来源时
   一个键都不许发。
"""
from __future__ import annotations

import sys

import pytest

from pet.music import bodian_control


# ------------------------------------------------------------ 结构体布局


def test_input_struct_is_40_bytes_on_x64():
    """``INPUT`` 必须是 40 字节（x64）——少声明成员会让 SendInput 静默失败。"""
    if sys.maxsize <= 2**32:
        pytest.skip("只在 64 位下校验布局")
    assert bodian_control.input_struct_size() == 40, (
        "INPUT 结构体尺寸不对：SendInput 会返回 0 且报 ERROR_INVALID_PARAMETER，"
        "看起来像播放器不响应媒体键"
    )


# ------------------------------------------------------------ 发送语义


def test_media_key_codes_are_the_documented_vk_values():
    """三个动作映射到正确的虚拟键码（写错就是给波点发了个别的键）。"""
    assert bodian_control._ACTIONS["next"] == 0xB0
    assert bodian_control._ACTIONS["previous"] == 0xB1
    assert bodian_control._ACTIONS["play_pause"] == 0xB3


def test_non_windows_never_sends(monkeypatch):
    """非 Windows 直接返回 False（不做任何平台特判）。"""
    monkeypatch.setattr(sys, "platform", "linux")
    assert bodian_control.send_media_key(0xB0) is False
    assert bodian_control.target_available() is False
    assert bodian_control.skip("next") is False
    assert bodian_control.toggle_pause() is False


def test_skip_requires_the_bodian_process(monkeypatch):
    """波点没在跑 → 一个键都不发（否则媒体键会落到别的播放器上）。"""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(bodian_control.bodian_playing, "process_running", lambda: False)
    sent: list[int] = []
    monkeypatch.setattr(bodian_control, "send_media_key", lambda vk: sent.append(vk) or True)

    assert bodian_control.skip("next") is False
    assert bodian_control.toggle_pause() is False
    assert sent == []


def test_skip_sends_next_and_previous(monkeypatch):
    """进程在跑时，next/previous 分别发对应的键。"""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(bodian_control.bodian_playing, "process_running", lambda: True)
    sent: list[int] = []
    monkeypatch.setattr(bodian_control, "send_media_key", lambda vk: sent.append(vk) or True)

    assert bodian_control.skip("next") is True
    assert bodian_control.skip("previous") is True
    assert bodian_control.toggle_pause() is True

    assert sent == [0xB0, 0xB1, 0xB3]


def test_unknown_direction_falls_back_to_next(monkeypatch):
    """``direction`` 传了不认识的值时按 next 处理（不静默什么都不做）。"""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(bodian_control.bodian_playing, "process_running", lambda: True)
    sent: list[int] = []
    monkeypatch.setattr(bodian_control, "send_media_key", lambda vk: sent.append(vk) or True)

    assert bodian_control.skip("sideways") is True
    assert sent == [0xB0]


def test_send_media_key_treats_partial_injection_as_failure(monkeypatch):
    """``SendInput`` 只注入了 1 个事件 → 必须返回 False 且**不重试**。"""
    calls: list[int] = []

    class _FakeUser32:
        def SendInput(self, count, events, size):
            calls.append(count)
            return 1  # 只成功一半

    monkeypatch.setattr(bodian_control, "_user32", lambda: _FakeUser32())

    assert bodian_control.send_media_key(0xB0) is False
    assert calls == [2], "不得重试：媒体键是全局动作，重试会连累别的播放器"


def test_send_media_key_success_returns_true(monkeypatch):
    """两个事件都注入成功才返回 True。"""
    class _FakeUser32:
        def SendInput(self, count, events, size):
            return count

    monkeypatch.setattr(bodian_control, "_user32", lambda: _FakeUser32())

    assert bodian_control.send_media_key(0xB0) is True


def test_send_media_key_swallows_exceptions(monkeypatch):
    """底层炸了也不许抛到菜单线程。"""
    class _Boom:
        def SendInput(self, *a):
            raise OSError("boom")

    monkeypatch.setattr(bodian_control, "_user32", lambda: _Boom())

    assert bodian_control.send_media_key(0xB0) is False


# ------------------------------------------------------------ 控制器接线


def _controller(monkeypatch, cfg=None):
    from pet.music.lyric_controller import MusicLyricController

    import tests.test_lyric_sources as tls

    win = tls._SongWindow(cfg)
    ctrl = MusicLyricController(win)
    monkeypatch.setattr(ctrl, "_fetch_worker", lambda *a, **kw: None)
    return ctrl


def test_controller_uses_smtc_when_it_works(monkeypatch):
    """SMTC 成功时**不许**再发媒体键（否则等于切两次）。"""
    from pet.music import now_playing

    ctrl = _controller(monkeypatch)
    ctrl._source_app_id = "bodian_pc.exe"
    monkeypatch.setattr(now_playing, "skip_track", lambda direction="next": True)
    sends: list[str] = []
    monkeypatch.setattr(bodian_control, "skip", lambda d="next": sends.append(d) or True)

    assert ctrl.skip_track("next") is True
    assert sends == []


def test_controller_falls_back_to_media_key_for_bodian(monkeypatch):
    """SMTC 失败 + 当前来源是波点 → 发媒体键。"""
    from pet.music import now_playing

    ctrl = _controller(monkeypatch)
    ctrl._source_app_id = "bodian_pc.exe"
    monkeypatch.setattr(now_playing, "skip_track", lambda direction="next": False)
    sends: list[str] = []
    monkeypatch.setattr(bodian_control, "skip", lambda d="next": sends.append(d) or True)

    assert ctrl.skip_track("next") is True
    assert sends == ["next"]


def test_controller_does_not_send_media_key_for_other_players(monkeypatch):
    """当前来源是网易云时，SMTC 失败也**不许**发媒体键（会切到错误的对象）。"""
    from pet.music import now_playing

    ctrl = _controller(monkeypatch)
    ctrl._source_app_id = "cloudmusic.exe"
    monkeypatch.setattr(now_playing, "skip_track", lambda direction="next": False)
    sends: list[str] = []
    monkeypatch.setattr(bodian_control, "skip", lambda d="next": sends.append(d) or True)

    assert ctrl.skip_track("next") is False
    assert sends == []


def test_controller_does_not_send_media_key_without_a_source(monkeypatch):
    """还没采到任何来源时，不许发媒体键。"""
    from pet.music import now_playing

    ctrl = _controller(monkeypatch)
    ctrl._source_app_id = ""
    monkeypatch.setattr(now_playing, "skip_track", lambda direction="next": False)
    sends: list[str] = []
    monkeypatch.setattr(bodian_control, "skip", lambda d="next": sends.append(d) or True)

    assert ctrl.skip_track("next") is False
    assert sends == []


def test_controller_pause_falls_back_to_media_key_for_bodian(monkeypatch):
    """暂停同样：SMTC 失败 + 来源是波点 → 发媒体键。"""
    from pet.music import now_playing

    ctrl = _controller(monkeypatch)
    ctrl._source_app_id = "bodian_pc.exe"
    monkeypatch.setattr(now_playing, "toggle_play_pause", lambda: False)
    sends: list[str] = []
    monkeypatch.setattr(
        bodian_control, "toggle_pause", lambda: sends.append("pause") or True
    )

    assert ctrl.toggle_play_pause() is True
    assert sends == ["pause"]


def test_source_app_id_tracks_supplementary_sources(monkeypatch):
    """补充源也要记进 ``_source_app_id``——否则切歌永远不知道该发给谁。"""
    import tests.test_lyric_sources as tls

    ctrl = _controller(monkeypatch)
    ctrl._on_playback_ready(tls._playback("bodian_pc.exe"))

    assert ctrl._source_app_id == "bodian_pc.exe"
    assert ctrl._tracked_app_id == "", "补充源仍然不许占用 SMTC 跟踪位"
