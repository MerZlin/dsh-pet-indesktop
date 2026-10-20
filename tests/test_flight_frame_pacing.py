# -*- coding: utf-8 -*-
"""飞行期帧交付节奏（实机"一只鱼上下飞帧数上不去"定案修复）回归测试。

根因：`sprite_physics` 每 tick 把「用户速率 × 飞行倍率」写进 clip
（``PetSprite.set_flight_anim_speed`` → ``clip.set_playback_speed``），两个 clip
实现都用 QTimer 的 ``setInterval`` / ``start(ms)`` 落地新间隔——**对运行中的
QTimer 重设间隔会重开倒计时**（I1 真 QTimer 探针：每 64ms 写一次同值 → 2s 只
交付 31 帧，不写则 47 帧）。飞行期倍率随速度连续变化（重力 1400px/s² → 倍率每
~4 个 16ms tick 跨过 0.05 的门），重设间隔每次都把当拍倒计时截断。代价的字面
量级是"每次写入最多推后一个整间隔、平均半帧"：实测（本机 2.4s 窗口、26 次写入）
旧语义交付 67 帧 vs 修复后 80 帧；只有写入周期明显长于帧间隔时才会一路退化到
"写入周期"（≈17fps——上面的 I1 探针就是那种配置）。

两个口径：
1. 端到端：真 ``FrameSeqClip`` + 真 ``PetSprite`` + 真事件循环，按**真抛掷物理**
   的速度剖面写速率，统计实际帧交付数——判据是**同事件循环里的配对量**（产品
   clip vs 旧写入语义对照 clip，见 ``_OldWriteSemanticsClip``）＋ 写入处的确定
   性倒计时见证，不用解析积分当阈值（理由见用例 1 的 docstring）；
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

WINDOW_S = 2.4            # 端到端窗口（s）：写入周期 ≈64ms → 窗口内 ≈26 次真写入
TICK_MS = 16

#: 前提：窗口内的真写入次数（实测 26；剖面由 0.05 门 + 64ms 写入周期决定）
MIN_WRITES = 16
#: 对照臂（旧写入语义）必须被写入推后倒计时的次数（实测 11~25）；低于它就说明
#: 见证或对照臂没生效，判据本身不可信——那时必须红，不能静默放行
MIN_BASELINE_RESETS = 5
#: 用户可见帧交付余量（帧/窗口）：实测产品 clip 多交付 10~14 帧，结构性预期
#: ≈26 次写入 × 半帧 ≈ 13 帧（每次写入最多推后一个整间隔、平均半个）
MIN_FRAME_GAIN = 6
#: 倒计时见证：写入前倒计时还剩 ≥ 该值才算"当拍倒计时成立"（写入瞬间正好到点时
#: Qt 本来就要重开倒计时，不能算作重置）
WITNESS_MIN_PHASE_MS = 5
#: remainingTime() 是 1ms 分辨率；容差内的读数跳动不算被推后（倒计时只减不增）
WITNESS_SLACK_MS = 2


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

    def movie(self, _name, **_kw):
        return self._clip


class _OldWriteSemanticsClip(FrameSeqClip):
    """旧写入语义对照臂：716ad4a 的 ``set_playback_speed`` 原文（两行）。

    取 ``git show 716ad4a:pet/frameseq_clip.py``——修复前就是"运行中每次写入都
    ``start(interval)``"（= 重开当拍倒计时）。本机 Qt 6.11.2 真 QTimer 探针：
    40ms 间隔跑到剩 19ms 时同值 ``start(40)``/``setInterval(40)`` 都把读数打回
    40；每 16ms 同值写一次 → 800ms 一帧都不交付。作为**同事件循环里的基线对照**
    用：两臂同为真 clip、真 PetSprite、同一素材、同一速度剖面、同一 tick，只有
    写入语义不同，因此环境噪声（Qt 取整/启动瞬时/机器负载）在两臂上同向，配对
    差值才是原缺陷的净代价。
    """

    def set_playback_speed(self, speed: float) -> None:
        self.playback_speed = max(0.1, float(speed))
        if self._running:
            self._timer.start(self._interval_ms())


class _FrameCounter:
    """frameChanged 计数（窗口起点前的一次性计数用 base 差值扣掉）。"""

    def __init__(self) -> None:
        self.n = 0

    def __call__(self, _frame: int) -> None:
        self.n += 1


class _WriteWitness:
    """飞行期真写入的倒计时见证：写入次数 + "写入把运行中倒计时推后"的次数。

    ``_apply_interval`` 的契约是"运行中绝不重设 QTimer"——倒计时读数只能单调
    减少。写入前后各读一次 ``remainingTime()``：读数变大（超出 1ms 分辨率容差）
    就是被重开。判定只在写入前倒计时还剩 ≥ ``WITNESS_MIN_PHASE_MS`` 时生效：
    只剩 0~1ms 时写入瞬间正好到点是正常现象（那一刻 Qt 本来就要重开倒计时），
    那种情况不参与判定，因此全部是"确定性的状态观察"，与机器负载无关。
    """

    def __init__(self) -> None:
        self.writes = 0
        self.resets = 0

    def apply(self, sprite: PetSprite, clip: FrameSeqClip, factor: float) -> None:
        before_speed = clip.playback_speed
        before_left = clip._timer.remainingTime()
        sprite.set_flight_anim_speed(factor)
        after_left = clip._timer.remainingTime()
        if clip.playback_speed == before_speed:
            return                      # 0.05 门挡下的调用：没有真写入
        self.writes += 1
        if before_left >= WITNESS_MIN_PHASE_MS \
                and after_left > before_left + WITNESS_SLACK_MS:
            self.resets += 1


# ---------------------------------------------------------------- 1 端到端帧交付
def test_flight_speed_writes_do_not_starve_frame_delivery(tmp_path):
    """飞行期每 tick 写速率：不得重开帧表倒计时，也不得压慢用户可见帧交付。

    速度剖面 = **真抛掷物理**（`physics.throw_step`，初速 2500px/s 上抛，重力
    1400px/s²）→ 倍率 1.75→1.44、clip 间隔 24~29ms，而 `set_flight_anim_speed`
    的 0.05 门每 ~4 tick（64ms）放行一次写入（2.4s 窗口实测 26 次）。

    判据都建立在同事件循环里的**双真-clip 对照**上（对照臂 = 旧写入语义的真
    clip）：

    1. **确定性倒计时见证**：26 次真写入里，产品 clip 把运行中倒计时推后的次数
       必须为 0；对照臂实测 11~25 次（即见证确实看得见重置，不是空判据）；
    2. **用户可见帧交付**：产品 clip 在窗口内交付的帧数必须比对照臂多
       `MIN_FRAME_GAIN` 帧（实测多 10~14 帧）。

    改前的口径是 `delivered >= ideal − 3`（1.2s 窗口、解析积分 43.8~44.8 帧）：
    解析积分假设"每次写入立即生效"，而 Qt 计时器取整、起播瞬时与机器负载合起来
    就能吃掉 0~5 帧，实测 delivered 40~45 抖动、判据落在噪声里（本机 16 次跑：
    9 红）。这里不再给绝对数字定阈值：噪声在两臂上同向，配对差值（实测 10~14
    帧 vs 0 帧）才是原缺陷的净代价，同时保留确定性见证作为与负载无关的判别器。
    """
    d = tmp_path / "clip"
    _make_frames(d, count=200)          # 窗口内最多消耗 ≈105 帧（42fps 上限），留足余量
    subject = FrameSeqClip(d)
    baseline = _OldWriteSemanticsClip(d)     # 同一素材目录（只读）：只有写入语义不同
    subject_sprite = PetSprite(_StubLibrary(subject), scale=0.5)
    baseline_sprite = PetSprite(_StubLibrary(baseline), scale=0.5)
    subject_frames = _FrameCounter()
    baseline_frames = _FrameCounter()
    subject.frameChanged.connect(subject_frames)
    baseline.frameChanged.connect(baseline_frames)
    subject_witness = _WriteWitness()
    baseline_witness = _WriteWitness()
    try:
        assert subject_sprite.bind_clip("idle") is True
        assert baseline_sprite.bind_clip("idle") is True
        _pump(60)  # 起播首帧到货

        # 真抛掷积分（重力 + 四边反弹），与 sprite_physics 同一纯函数
        state = {"px": 0.0, "py": 0.0, "vx": 200.0, "vy": -2500.0}
        ideal = [0.0]     # 诊断用解析积分（每次写入立即生效的假想交付数）
        fps = 24.0

        def on_tick():
            s = state
            s["px"], s["py"], s["vx"], s["vy"], _bounced = physics_mod.throw_step(
                s["px"], s["py"], s["vx"], s["vy"], TICK_MS / 1000.0,
                0.0, 0.0, 400.0, 400.0)
            speed = math.hypot(s["vx"], s["vy"])
            factor = physics_mod.flight_anim_speed(speed)
            rate = float(subject_sprite.playback_speed) * factor
            ideal[0] += TICK_MS / round(1000.0 / fps / rate)
            subject_witness.apply(subject_sprite, subject, factor)
            baseline_witness.apply(baseline_sprite, baseline, factor)

        tick = QTimer()
        tick.setTimerType(Qt.TimerType.PreciseTimer)
        tick.timeout.connect(on_tick)
        tick.setInterval(TICK_MS)
        tick.start()

        base_subject = subject_frames.n
        base_baseline = baseline_frames.n
        loop = QEventLoop()
        QTimer.singleShot(int(WINDOW_S * 1000), loop.quit)
        loop.exec()
        tick.stop()

        delivered = subject_frames.n - base_subject
        delivered_baseline = baseline_frames.n - base_baseline
        assert subject_witness.writes >= MIN_WRITES, (
            f"本用例前提不成立：窗口内真写入仅 {subject_witness.writes} 次"
            f"（{WINDOW_S}s 剖面预期 ≈26 次）")
        assert baseline_witness.resets >= MIN_BASELINE_RESETS, (
            f"对照臂（旧写入语义）没被写入推后倒计时：{baseline_witness.resets}/"
            f"{baseline_witness.writes} 次——见证或对照臂失效，本用例的判据不可信")
        assert subject_witness.resets == 0, (
            f"飞行期写入重开了帧表倒计时 {subject_witness.resets} 次"
            f"（真写入 {subject_witness.writes} 次）：交付被压到写入周期")
        assert delivered >= delivered_baseline + MIN_FRAME_GAIN, (
            f"同循环对照下帧交付被吃掉：产品 clip {delivered} 帧 vs 旧写入语义对照 "
            f"{delivered_baseline} 帧（差 {delivered - delivered_baseline} < "
            f"{MIN_FRAME_GAIN}）；解析积分 {ideal[0]:.1f} 帧、真写入 "
            f"{subject_witness.writes} 次")
    finally:
        subject_sprite.close()
        subject.close()
        baseline_sprite.close()
        baseline.close()


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
