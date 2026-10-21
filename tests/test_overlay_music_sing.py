# -*- coding: utf-8 -*-
"""音乐自动唱歌（``music_sing_enabled``）overlay 接线回归（DS 审查 M5f）。

旧架构落点：``window.py:485-489/745-746`` 建 1s 轮询 + 显隐对称启停；
``window_alerts.py:495-550`` 的 host 形实现（检测/唱歌态/静音宽限期）。
overlay 拓扑下 PetWindow 不构造，该键此前零消费（死开关）。

接法对齐 self_talk：壳提供 host 形转发（``_check_music_sing`` /
``_switch`` / ``_is_one_shot_playing``）+ 定时器 + 配置热改/显隐对称。

纪律：offscreen；monkeypatch 音乐检测函数，同步直调 handler，不 sleep。
"""
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import tests.test_sprite_menu_facade as fac
from tests.test_overlay_dead_switches import _make_shell

app = QApplication.instance() or QApplication([])


class _MusicSingHarness:
    """把 shell 与假音乐检测拼起来（不依赖真实音频 COM/素材）。"""

    def __init__(self, shell, playing=True):
        self.shell = shell
        self.playing = playing

    def check(self):
        from pet.music import detect as music_detect
        original = music_detect.is_music_playing
        try:
            music_detect.is_music_playing = lambda: self.playing
            self.shell._check_music_sing()
        finally:
            music_detect.is_music_playing = original


def test_music_sing_polls_when_enabled(tmp_path):
    """开关开：1s 轮询启动；隐藏停表、恢复显示按开关重启。"""
    shell, _lib = _make_shell(tmp_path, {"music_sing_enabled": True})
    try:
        shell.overlay.show()
        app.processEvents()
        assert shell._music_sing_timer.interval() == 1000
        assert shell._music_sing_timer.isActive()
        shell.set_pet_visible(False)
        assert not shell._music_sing_timer.isActive()
        shell.set_pet_visible(True)
        assert shell._music_sing_timer.isActive()
    finally:
        shell.overlay.close()
        shell._delete_runtime_marker()


def test_music_sing_disabled_no_timer(tmp_path):
    """开关默认关：定时器不跑（不可见零消耗纪律）。"""
    shell, _lib = _make_shell(tmp_path)
    try:
        shell.overlay.show()
        app.processEvents()
        assert not shell._music_sing_timer.isActive()
    finally:
        shell.overlay.close()
        shell._delete_runtime_marker()


def test_music_sing_switches_to_sing_anim_and_back(tmp_path):
    """检测到音乐 → 播唱歌动画；音乐停 + 宽限期过 → 退出唱歌态。"""
    shell, lib = _make_shell(tmp_path, {"music_sing_enabled": True})
    # 唱歌素材名与旧架构同源常量（window_alerts.check_music_sing 读它）
    from pet.window import SING_ANIM
    lib._clips[SING_ANIM] = fac.FakeClip(SING_ANIM, 24)
    try:
        shell.overlay.show()
        app.processEvents()
        harness = _MusicSingHarness(shell, playing=True)
        harness.check()
        assert shell._music_sing_active is True
        assert shell.behavior.anim_of(shell.sprite) == SING_ANIM

        # 音乐停：宽限期内仍保持唱歌（前奏/间奏不退出）
        harness.playing = False
        shell._music_sing_silent_since = time.monotonic()
        harness.check()
        assert shell._music_sing_active is True
        # 宽限期（>=1s）已过：退出唱歌态
        shell._music_sing_silent_since = time.monotonic() - 999.0
        harness.check()
        assert shell._music_sing_active is False
    finally:
        shell.overlay.close()
        shell._delete_runtime_marker()


def test_music_sing_replays_clip_while_music_continues(tmp_path):
    """唱歌 clip 播完而音乐仍在放 → 无缝续播（window.py:2595-2601 语义）。

    收口判据归状态机（``BehaviorController.sing_continue_provider``），不再是
    extras 链观测到的「ACTS → IDLE 下降沿」：后者只在掷骰恰好落到待机时才成立，
    旧测试用 ``ScriptedRng(rolls=[0.05])`` 强行掷中待机桶，属自证式通过——掷骰
    0.85（移动桶，占比 20%）时唱歌照样被打断。

    判据来源是本壳构造期自己接的（``_init_music_sing`` 一行）——测试**不再**
    手工注入 provider，否则「壳到底接没接」永远测不到。
    """
    shell, lib = _make_shell(tmp_path, {"music_sing_enabled": True})
    from pet.window import SING_ANIM
    lib._clips[SING_ANIM] = fac.FakeClip(SING_ANIM, 24)
    try:
        from pet.sprite_behavior import STATE_ACTS
        from tests.test_sprite_behavior import ScriptedRng

        shell.overlay.show()
        app.processEvents()
        sprite = shell.sprite
        shell._music_sing_active = True
        shell.switch_clip(SING_ANIM)
        assert shell.behavior.state_of(sprite) == STATE_ACTS

        assert callable(shell.behavior.sing_continue_provider), \
            "壳构造期必须把唱歌续播判据接到状态机（_init_music_sing 一行）"
        # 唱歌 clip 到点：掷骰 0.85 命中的是移动桶，续唱不得依赖掷骰结果
        shell.behavior.rng = ScriptedRng(rolls=[0.85])
        shell.behavior.tick([sprite], 24 * 42 / 1000.0 + 0.1)
        assert shell.behavior.state_of(sprite) == STATE_ACTS
        assert shell.behavior.anim_of(sprite) == SING_ANIM

        # 纯音乐标志 / 音乐停止 → 不再续播
        shell.set_instrumental_playing(True)
        assert shell._music_sing_active is False
        shell.behavior.tick([sprite], 24 * 42 / 1000.0 + 0.1)
        assert shell.behavior.anim_of(sprite) != SING_ANIM
    finally:
        shell.overlay.close()
        shell._delete_runtime_marker()


def _sing_scene():
    """BehaviorController 直连现场：唱歌素材当一次性动画播（无壳/无 extras 链）。"""
    from pet.sprite_behavior import BehaviorController
    from tests.test_sprite_behavior import (
        BOUNDS,
        ScriptedRng,
        _make_library,
        _make_sprite,
    )
    from pet.window import SING_ANIM

    lib = _make_library(idles=["idle1", SING_ANIM])
    sprite = _make_sprite(lib, facing="right")
    c = BehaviorController(BOUNDS, rng=ScriptedRng(rolls=[0.85], ints=(100, 0)))
    c.predict_enabled = False
    assert c.play_once(sprite, SING_ANIM) is True
    return c, sprite, lib, SING_ANIM


def _sing_expire(c, sprite, lib, sing):
    from pet.sprite_behavior import STATE_ACTS
    assert c.state_of(sprite) == STATE_ACTS
    c.tick([sprite], lib.duration(sing) + 0.1)


def test_behavior_continues_singing_when_roll_lands_outside_idle():
    """掷骰命中移动桶（0.85）也必须续唱：判据在状态机，不看掷骰结果。"""
    from pet.sprite_behavior import STATE_ACTS

    c, sprite, lib, sing = _sing_scene()
    c.sing_continue_provider = lambda: True
    _sing_expire(c, sprite, lib, sing)

    assert c.state_of(sprite) == STATE_ACTS
    assert c.anim_of(sprite) == sing


def test_behavior_does_not_continue_singing_without_provider_or_when_false():
    """默认（壳未注入）与 provider 返假：都走原掷骰链——默认行为零变化。"""
    from pet.sprite_behavior import STATE_ACTS

    for provider in (None, lambda: False):
        c, sprite, lib, sing = _sing_scene()
        c.sing_continue_provider = provider
        _sing_expire(c, sprite, lib, sing)
        assert (c.state_of(sprite), c.anim_of(sprite)) != (STATE_ACTS, sing), \
            "未注入/返假时不得续唱（唱歌随掷骰结果自然打断）"


def test_behavior_does_not_continue_singing_for_other_anim():
    """续唱只认唱歌素材本身：同一 provider 对普通动作绝不放行。"""
    from pet.sprite_behavior import STATE_ACTS

    c, sprite, lib, sing = _sing_scene()
    c.sing_continue_provider = lambda: True
    assert c.play_once(sprite, "idle1") is True
    _sing_expire(c, sprite, lib, sing)
    assert c.anim_of(sprite) != sing


def test_music_sing_config_hot_toggle(tmp_path):
    """设置页热改：开→启轮询，关→停表并退出唱歌态。"""
    shell, _lib = _make_shell(tmp_path)
    try:
        shell.overlay.show()
        app.processEvents()
        assert not shell._music_sing_timer.isActive()
        shell._config.set("music_sing_enabled", True)
        shell.refresh_settings()
        assert shell._music_sing_timer.isActive()
        shell._config.set("music_sing_enabled", False)
        shell.refresh_settings()
        assert not shell._music_sing_timer.isActive()
        assert shell._music_sing_active is False
    finally:
        shell.overlay.close()
        shell._delete_runtime_marker()
