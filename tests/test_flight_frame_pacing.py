# -*- coding: utf-8 -*-
"""飞行期帧交付节奏（实机"一只鱼上下飞帧数上不去"定案修复）回归测试。

根因：`sprite_physics` 每 tick 把「用户速率 × 飞行倍率」写进 clip
（``PetSprite.set_flight_anim_speed`` → ``clip.set_playback_speed``），两个 clip
实现都用 QTimer 的 ``setInterval`` / ``start(ms)`` 落地新间隔——**对运行中的
QTimer 重设间隔会重开倒计时**（I1 真 QTimer 探针：每 64ms 写一次同值 → 2s 只
交付 31 帧，不写则 47 帧）。飞行期倍率随速度连续变化（重力 1400px/s² → 倍率每
~4 个 16ms tick 跨过 0.05 的门），重设间隔每次都把当拍倒计时截断，帧交付被压到
"写入周期"（≈17fps）：速度越快、写入越密，帧反而越上不去。

两个口径：
1. 端到端：真 ``FrameSeqClip`` + 真 ``PetSprite`` + 真事件循环，按**真抛掷物理**
   的速度剖面写速率，统计实际帧交付数；
2. 定时器口径：运行中同值写入不得重开倒计时；真变速（间隔真变）也只在下次
   timeout 落地（当拍倒计时不被截断，新间隔照样生效）；未运行时写入照旧立即
   生效（``_switch`` 起播前设速率的老语义）。

纪律：真 QTimer + 真事件循环；断言用量级差（≈1.5~2×）而非精确时序，等待一律
走事件循环的 singleShot 退出（不 sleep 赌时序）。
"""
from __future__ import annotations

import json
import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
from pathlib import Path

from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from pet import frameseq_provision
from pet import physics as physics_mod
from pet.frameseq_clip import FrameSeqClip
from pet.pet_sprite import PetSprite
from pet.webm_clip import WebMClip

app = QApplication.instance() or QApplication([])

REAL_WEBM = Path("assets/characters/shenshen/videos/idle/待机呼吸休闲.webm")

WINDOW_S = 1.2
TICK_MS = 16


def _pump(ms: int) -> None:
    """真事件循环跑 ms 毫秒（真 QTimer 照常触发），不用 sleep 赌时序。"""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _make_frames(dir_path: Path, count: int = 120, size: tuple[int, int] = (32, 24)) -> None:
    """Qt 现场生成 count 帧 webp + meta.json（fps=24）。"""
    dir_path.mkdir(parents=True, exist_ok=True)
    w, h = size
    for i in range(count):
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        for y in range(4):
            for x in range(4):
                img.setPixel(x, y, (0xFF << 24) | ((i % 200) << 16) | 0x3366)
        assert img.save(str(dir_path / f"f_{i + 1:04d}.webp"), "webp", 100)
    (dir_path / "meta.json").write_text(
        json.dumps({"fps": 24.0, "source": "x.webm", "frames": count,
                    "encoder": frameseq_provision.ENCODER_DESC}),
        encoding="utf-8")


class _StubLibrary:
    """PetSprite 消费的最小库协议（真 clip，只有 movie/no_mirror 是替身）。"""

    def __init__(self, clip):
        self._clip = clip
        self.no_mirror: set = set()
        self.character_id = ""

    def movie(self, _name):
        return self._clip


# ---------------------------------------------------------------- 1 端到端帧交付
def test_flight_speed_writes_do_not_starve_frame_delivery(tmp_path):
    """飞行期每 tick 写速率：帧交付不得被写入周期压制。

    速度剖面 = **真抛掷物理**（`physics.throw_step`，初速 2500px/s 上抛，重力
    1400px/s²）→ 倍率 1.75~1.44、clip 间隔 24~28ms，而 `set_flight_anim_speed`
    的 0.05 门每 ~4 tick（64ms）放行一次写入。修前：每次写入重开倒计时 → 交付
    被压到写入周期（≈19 帧/1.2s）；修后：交付回到 clip 自己的节奏（≈40 帧）。
    """
    # A prior Qt/OS timing context can shift this small delivery budget by one
    # frame. Run the original real-timer probe in its own process, retaining
    # every assertion below; do not relax the starvation threshold for CI.
    if os.environ.get("DSH_FLIGHT_PACING_WORKER") != "1":
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "pytest", "-q", f"{Path(__file__)}::test_flight_speed_writes_do_not_starve_frame_delivery"],
            env=dict(os.environ, DSH_FLIGHT_PACING_WORKER="1", QT_QPA_PLATFORM="offscreen", PYTHONUTF8="1"),
            capture_output=True, text=True, encoding="utf-8", timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return
    d = tmp_path / "clip"
    _make_frames(d)
    clip = FrameSeqClip(d)
    sprite = PetSprite(_StubLibrary(clip), scale=0.5)
    hits: list[float] = []
    clip.frameChanged.connect(lambda _n: hits.append(time.monotonic()))
    try:
        assert sprite.bind_clip("idle") is True
        _pump(60)  # 起播首帧到货

        # 真抛掷积分（重力 + 四边反弹），与 sprite_physics 同一纯函数
        state = {"px": 0.0, "py": 0.0, "vx": 200.0, "vy": -2500.0}
        writes: list[int] = []
        ideal = [0.0]     # 无截断时的理论交付数（按请求间隔逐 tick 累加）
        fps = 24.0

        def on_tick():
            s = state
            s["px"], s["py"], s["vx"], s["vy"], _bounced = physics_mod.throw_step(
                s["px"], s["py"], s["vx"], s["vy"], TICK_MS / 1000.0,
                0.0, 0.0, 400.0, 400.0)
            speed = math.hypot(s["vx"], s["vy"])
            rate = float(sprite.playback_speed) * physics_mod.flight_anim_speed(speed)
            ideal[0] += TICK_MS / round(1000.0 / fps / rate)
            before = clip.playback_speed
            sprite.set_flight_anim_speed(physics_mod.flight_anim_speed(speed))
            if clip.playback_speed != before:
                writes.append(len(writes))

        tick = QTimer()
        tick.setTimerType(Qt.TimerType.PreciseTimer)
        tick.timeout.connect(on_tick)
        tick.setInterval(TICK_MS)
        tick.start()

        base = len(hits)
        loop = QEventLoop()
        QTimer.singleShot(int(WINDOW_S * 1000), loop.quit)
        loop.exec()
        tick.stop()

        delivered = len(hits) - base
        expected = ideal[0]
        assert len(writes) >= 5, f"本用例前提不成立：写入仅 {len(writes)} 次/1.2s"
        # 交付帧数对账：修前每次写入截断当拍倒计时，实测少交付 7~8 帧；
        # 修后写入不再触碰运行中的倒计时（理论值 −2 内）。
        assert delivered >= expected - 3, (
            f"飞行期帧交付被速率写入吃掉：{delivered} 帧（无截断应 {expected:.0f}，"
            f"写入 {len(writes)} 次）")
    finally:
        sprite.close()
        clip.close()


# ---------------------------------------------------------------- 2 定时器口径
def test_webm_set_playback_speed_same_interval_keeps_countdown():
    """同值写入（间隔不变）不得重设 QTimer：倒计时读数不回到整间隔。"""
    clip = WebMClip(REAL_WEBM)
    try:
        clip.set_playback_speed(0.25)          # 慢速 → 大间隔，读数分辨力足够
        interval = clip._timer_interval()
        assert interval >= 100
        clip._timer.start()                    # 直接跑帧表（不起 reader）
        _pump(60)
        before = clip._timer.remainingTime()
        assert 0 < before <= interval
        clip.set_playback_speed(0.25)          # 同值重写：间隔未变
        after = clip._timer.remainingTime()
        clip._timer.stop()
        assert after <= before + 20, (
            f"同值重写重开了倒计时：{before}ms → {after}ms（整间隔 {interval}ms）")
    finally:
        clip.stop()


def test_webm_set_playback_speed_change_defers_and_then_applies():
    """真变速：间隔真变时不得截断当拍倒计时，但下次 timeout 必须落地新间隔。"""
    clip = WebMClip(REAL_WEBM)
    try:
        clip.set_playback_speed(0.25)
        slow = clip._timer_interval()
        clip._timer.start()
        _pump(60)
        before = clip._timer.remainingTime()
        clip.set_playback_speed(1.0)           # 真变速：间隔变小
        fast = clip._timer_interval()
        assert fast < slow
        mid = clip._timer.remainingTime()
        assert mid <= before + 20, f"变速截断了倒计时：{before}ms → {mid}ms"
        _pump(slow + 60)                       # 跨过至少一次 timeout
        assert clip._timer.interval() == fast, "新间隔未在下一次 timeout 落地"
    finally:
        clip.stop()


def test_webm_set_playback_speed_before_start_applies_immediately():
    """未运行的帧表：写入照旧立即落地（_switch 起播前设速率的老语义）。"""
    clip = WebMClip(REAL_WEBM)
    try:
        clip.set_playback_speed(2.0)
        assert clip._timer.interval() == clip._timer_interval()
        clip.set_playback_speed(0.5)
        assert clip._timer.interval() == clip._timer_interval()
    finally:
        clip.stop()


def test_frameseq_set_playback_speed_same_interval_keeps_countdown(tmp_path):
    """FrameSeqClip 同款：同值写入不得重开帧表倒计时，真变速同样只顺延落地。"""
    d = tmp_path / "clip"
    _make_frames(d, count=8)
    clip = FrameSeqClip(d)
    try:
        clip.set_playback_speed(0.25)
        slow = clip._interval_ms()
        assert slow >= 100
        assert clip.start() is True            # 起播：帧表按 slow 运行
        _pump(60)
        before = clip._timer.remainingTime()
        assert 0 < before <= slow

        clip.set_playback_speed(0.25)          # 同值重写：间隔未变
        same = clip._timer.remainingTime()
        assert same <= before + 20, (
            f"同值重写重开了倒计时：{before}ms → {same}ms（整间隔 {slow}ms）")

        clip.set_playback_speed(1.0)           # 真变速：间隔真变，仍不得截断当拍
        fast = clip._interval_ms()
        assert fast < slow
        mid = clip._timer.remainingTime()
        assert mid <= before + 20, f"变速截断了倒计时：{before}ms → {mid}ms"
        _pump(slow + 60)
        assert clip._timer.interval() == fast, "新间隔未在下一次 timeout 落地"
    finally:
        clip.close()
