# -*- coding: utf-8 -*-
"""帧表节拍契约：帧序列必须用 ``PreciseTimer``。

Windows 的计时器颗粒是 15.625ms，Qt 的 ``CoarseTimer`` 会把间隔量化到那个边界：
24fps 的 42ms 变成 47ms（1x 只有 21.3fps）、2x 的 21ms 变成 31ms（2x 只有 32fps，
观感是"开了 2 倍速仍偏慢"）。实测见
``docs/PR-REPORT-ANIMATION-PACING-MUSIC-RECOVERY-2026-10-29.md``。
``PreciseTimer`` 的均值与目标一致（1x 23.9fps / 2x 47.8fps）；旧版主分支的满速档也
一直用它，这里只钉"两种定时器里选哪一种"这条确定性契约。真实走时由 PR 报告的实机
测量记录，不做成 CI 里的时序断言（AGENTS 的 CI 成本纪律）。
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from petcore.media.clip import create_frame_clip

app = QApplication.instance() or QApplication([])


def _make_frames(dir_path: Path, count: int = 4) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        image = QImage(8, 8, QImage.Format.Format_ARGB32)
        image.fill(0x80FF0000 | index)
        image.save(str(dir_path / f"f_{index + 1:04d}.webp"), "WEBP")
    (dir_path / "meta.json").write_text(
        json.dumps({"fps": 24.0, "frames": count}), encoding="utf-8"
    )


def test_frame_clip_uses_the_precise_timer(tmp_path: Path) -> None:
    _make_frames(tmp_path)
    clip = create_frame_clip(tmp_path, "pace")
    assert clip is not None
    timer = clip._impl._timer
    assert timer.timerType() == Qt.TimerType.PreciseTimer, (
        "帧表必须用 PreciseTimer：Coarse 会被 Windows 的 15.6ms 颗粒量化，"
        "2x 实测只有 32fps（Precise 47.8fps）"
    )


def test_frame_interval_follows_the_playback_speed(tmp_path: Path) -> None:
    """间隔 = 1000/fps/speed：24fps 的 1x=42ms、2x=21ms（不许被量化成 31ms）。"""
    _make_frames(tmp_path)
    clip = create_frame_clip(tmp_path, "pace")
    assert clip is not None
    impl = clip._impl
    assert impl._interval_ms() == 42
    clip.set_playback_speed(2.0)
    assert impl._interval_ms() == 21
