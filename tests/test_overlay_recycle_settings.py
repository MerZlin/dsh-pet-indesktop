# -*- coding: utf-8 -*-
"""ffmpeg 圈边界回收阈值（``ffmpeg_recycle_minutes``）在新架构的接线回归（G5）。

旧契约（base-2786c15/pet/window.py）：`:2757-2759 _recycle_minutes_from`
（``0`` = 关闭回收，正值 [2,120] 分钟）+ `:2790-2793 _push_recycle`
（``hasattr(movie, 'set_recycle_minutes')`` 才推）。推送时机 = build（`:435`）、
每次动画切换（`:1558`）、回退 idle（`:1661`）、不经 ``_switch`` 的重启（`:1818`）、
refresh（`:3984-3985`，只推当前 clip）。

新架构的等价接缝：``PetSprite.bind_clip``（会上屏的 clip 的唯一取用点，
与 window 的 ``_switch`` 同位置）+ ``OverlayShell._sync_sprite_settings``
（``_apply_window_capabilities`` → start/refresh/spawn 的 config→sprite 同步点，
与 refresh 推送同位置）。``webm_clip.set_recycle_minutes`` 自己夹 [2,120]、
``<=0`` 视为关闭，故这里**不再复制一份解析器**（config 加载期已归一）。

本轮只推参数：不 start/spawn/restart、不加 timer/thread/I-O、不动圈末回收机制。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, Signal
from PySide6.QtWidgets import QApplication

import tests.test_frameseq_clip as fsc
import tests.test_overlay_sprite_settings as oss
import tests.test_sprite_menu_facade as fac
from pet.frameseq_clip import FrameSeqClip
from pet.pet_sprite import PetSprite

app = QApplication.instance() or QApplication([])


class RecordingClip(fac.FakeClip):
    """facade 的 FakeClip + 记录 recycle 推送/启动次数（其余同形）。"""

    finished = Signal()

    def __init__(self, name, frames=24):
        super().__init__(name, frames)
        self.recycle_pushes: list[float] = []
        self.start_calls = 0

    def set_recycle_minutes(self, minutes):
        self.recycle_pushes.append(minutes)

    def start(self):
        self.start_calls += 1
        return super().start()


class RecordingLibrary(fac.RichLibrary):
    """素材池同 RichLibrary，但每个 clip 是可观察的 RecordingClip。"""

    def __init__(self):
        super().__init__()
        self._clips = {n: RecordingClip(n, 24) for n in
                       self.idles + self.turns + self.moves + self.clicks + self.acts}


def _make_shell(tmp_path, values=None, lib=None):
    shell, config = oss._make_shell(tmp_path, values)
    lib = lib if lib is not None else RecordingLibrary()
    shell.lib = lib
    shell.sprite.library = lib
    return shell, config, lib


def test_bind_pushes_default_threshold(tmp_path):
    """没配过该键 → 推 config 默认 10（与旧 `_recycle_minutes_from` 缺省一致）。"""
    shell, _config, lib = _make_shell(tmp_path)
    try:
        shell.sprite.bind_clip("idle1")
        assert lib.movie("idle1").recycle_pushes == [10]
    finally:
        shell._delete_runtime_marker()


def test_bind_pushes_configured_threshold(tmp_path):
    """绑定即推用户值：0（关闭回收）与合法正值逐位透传。"""
    for configured, expected in ((0, 0), (2, 2), (120, 120)):
        shell, _config, lib = _make_shell(
            tmp_path, {"ffmpeg_recycle_minutes": configured})
        try:
            shell.sprite.bind_clip("idle1")
            assert lib.movie("idle1").recycle_pushes == [expected]
        finally:
            shell._delete_runtime_marker()


def test_refresh_pushes_to_existing_clip(tmp_path):
    """热改只推参数：现有 clip 立刻拿到新值，且不重绑、不重启播放。"""
    shell, config, lib = _make_shell(tmp_path, {"ffmpeg_recycle_minutes": 10})
    try:
        shell.sprite.bind_clip("idle1")
        clip = lib.movie("idle1")
        starts, bound = clip.start_calls, shell.sprite._clip
        config.set("ffmpeg_recycle_minutes", 0)
        shell.refresh_settings()
        assert clip.recycle_pushes[-1] == 0
        config.set("ffmpeg_recycle_minutes", 30)
        shell.refresh_settings()
        assert clip.recycle_pushes[-1] == 30
        assert shell.sprite._clip is bound          # 没换绑
        assert clip.start_calls == starts           # 没重启播放
        assert clip.started is True                 # 仍在播
    finally:
        shell._delete_runtime_marker()


def test_refresh_updates_every_sprite_library(tmp_path):
    """多 sprite 各持独立库：刷新时逐只按**各自 slot 档案**更新阈值。

    ``ffmpeg_recycle_minutes`` 是每宠键（规范 §五「唯一归属表」），真值在
    ``config-slot-N.json``：子宠不再跟随主配置（那是"全局混每宠"的旧契约）。
    这里手工加进 overlay 的 sprite 按 slot 1 登记身份，逐只取自己的值。
    """
    from pet import overlay_spawn_state as ovs

    shell, config, lib_main = _make_shell(tmp_path, {"ffmpeg_recycle_minutes": 10})
    lib_sub = RecordingLibrary()
    sub = PetSprite(lib_sub, pos=QPointF(40.0, 40.0), scale=1.0)
    try:
        shell.sprite.bind_clip("idle1")
        shell.overlay.add_sprite(sub)
        shell._spawned.append(sub)
        shell._spawned_slots[sub] = 1
        shell._spawned_libs[sub] = lib_sub
        sub.bind_clip("walk")
        config.set("ffmpeg_recycle_minutes", 7)
        shell.refresh_settings()
        assert lib_main.movie("idle1").recycle_pushes[-1] == 7

        # 子宠只认它自己的档案：主宠改到 7 不影响它
        assert lib_sub.movie("walk").recycle_pushes[-1] == 10
        assert ovs.write_slot_setting(config.dir, 1, "ffmpeg_recycle_minutes", 30,
                                      user_customized=True)
        shell.refresh_settings()
        assert lib_sub.movie("walk").recycle_pushes[-1] == 30
        assert lib_main.movie("idle1").recycle_pushes[-1] == 7, "子宠的改动不动主宠"
    finally:
        shell._delete_runtime_marker()


def test_clip_without_set_recycle_minutes_still_binds(tmp_path):
    """Gif/fake 等缺 ``set_recycle_minutes`` 的 clip 不得因推送而崩（旧同款 hasattr 门）。"""
    shell, _config, lib = _make_shell(tmp_path, lib=fac.RichLibrary())
    try:
        assert shell.sprite.bind_clip("idle1")
        assert hasattr(lib.movie("idle1"), "set_recycle_minutes") is False
        shell.refresh_settings()                    # 刷新路径同样不得崩
    finally:
        shell._delete_runtime_marker()


def test_prewarmed_but_unbound_clip_is_pushed_at_bind(tmp_path):
    """库预热已经建好的 clip：未 bind 时无 reader 可回收故不推，bind 时补上。"""
    shell, _config, lib = _make_shell(tmp_path)
    try:
        clip = lib.movie("idle1")                   # 预热路径先建 clip
        assert clip.recycle_pushes == []
        shell.sprite.bind_clip("idle1")
        assert clip.recycle_pushes == [10]
    finally:
        shell._delete_runtime_marker()


def test_frameseq_clip_accepts_push_without_effect(tmp_path):
    """热集帧序列：bind 时推参数落到 no-op（无 ffmpeg 进程可回收），不新建状态。"""
    frames_dir = tmp_path / "fseq"
    fsc._make_frames(frames_dir)

    class FramesLibrary:
        idles = ["idle1"]

        def __init__(self, clip):
            self._clip = clip

        def movie(self, name, **_kw):
            return self._clip

    clip = FrameSeqClip(frames_dir)
    shell, _config, _lib = _make_shell(tmp_path, lib=FramesLibrary(clip))
    try:
        assert shell.sprite.bind_clip("idle1")
        assert shell.sprite._clip is clip
        assert "_recycle_seconds" not in clip.__dict__   # no-op：不落任何回收状态
        shell.refresh_settings()
        assert "_recycle_seconds" not in clip.__dict__
    finally:
        shell._delete_runtime_marker()
        clip.close()
