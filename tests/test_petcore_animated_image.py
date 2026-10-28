"""`AnimatedImage`：动图按逻辑尺寸排版、按屏幕 DPR 出帧（QMovie 帧 DPR 恒为 1）。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from petcore.ui.animated_image import AnimatedImage

# 8x4 双帧 GIF89a（首帧红、次帧蓝，100ms/帧），字节内联以避免外部素材。
_ANIMATED_GIF = (
    b'GIF89a\x08\x00\x04\x00\x81\x00\x00\xff\x00\x00\x00\x00\x00\x00'
    b'\x00\x00\x00\x00\x00!\xff\x0bNETSCAPE2.0\x03'
    b'\x01\x00\x00\x00!\xf9\x04\x00\n\x00\x00\x00,\x00\x00\x00\x00\x08\x00\x04'
    b'\x00\x00\x08\x0c\x00\x01\x08\x1cH\xb0\xa0\xc1\x83\x04\x03\x02\x00!\xf9\x04'
    b'\x01\n\x00\x01\x00,\x00\x00\x00\x00\x08\x00\x04\x00\x81\x00\x00\xff\x00\x00'
    b'\x00\x00\x00\x00\x00\x00\x00\x08\x0c\x00\x01\x08\x1cH\xb0\xa0\xc1\x83\x04\x03'
    b'\x02\x00;'
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _write_gif(path: Path, payload: bytes = _ANIMATED_GIF) -> Path:
    path.write_bytes(payload)
    return path


def _wider_gif() -> bytes:
    """同一份动图，逻辑屏幕改成 16x8（帧仍是 8x4，落在左上角）。"""
    payload = bytearray(_ANIMATED_GIF)
    payload[6:10] = b"\x10\x00\x08\x00"
    return bytes(payload)


def test_sizes_logically_and_decodes_at_the_screen_ratio(app, tmp_path: Path) -> None:
    gif = _write_gif(tmp_path / "anim.gif")
    widget = AnimatedImage(gif, 320, dpr=2.0)
    try:
        assert widget.movie_path == str(gif)
        assert widget.logical_size() == (8, 4)
        assert widget.device_pixel_ratio() == pytest.approx(2.0)
        assert widget.size().toTuple() == (8, 4)  # 逻辑尺寸，不含 DPR
        assert widget.sizeHint().toTuple() == (8, 4)
        assert widget.minimumSizeHint().toTuple() == (8, 4)
        # 帧按「逻辑 × dpr」解码，绘制前才写回 DPR，避免二次放大发虚。
        assert widget.movie() is not None
        assert widget.movie().scaledSize().toTuple() == (16, 8)
    finally:
        widget.close()


def test_max_side_caps_the_logical_size(app, tmp_path: Path) -> None:
    gif = _write_gif(tmp_path / "anim.gif")
    widget = AnimatedImage(gif, 6, dpr=1.0)
    try:
        assert widget.logical_size() == (6, 3)
        assert widget.size().toTuple() == (6, 3)
    finally:
        widget.close()


def test_explicit_ratio_survives_the_show_event(app, tmp_path: Path) -> None:
    """宿主算好的 DPR 不能被「落到本屏」的自动同步改回去。"""
    gif = _write_gif(tmp_path / "anim.gif")
    widget = AnimatedImage(gif, 320, dpr=2.0)
    try:
        widget.show()
        assert widget.device_pixel_ratio() == pytest.approx(2.0)
        assert widget.movie().scaledSize().toTuple() == (16, 8)
        assert widget.size().toTuple() == (8, 4)
    finally:
        widget.close()


def test_start_stop_and_frame_advance(app, tmp_path: Path) -> None:
    gif = _write_gif(tmp_path / "anim.gif")
    widget = AnimatedImage(gif, 320)
    frames: list[int] = []
    try:
        assert widget.is_running is False
        widget.movie().frameChanged.connect(frames.append)
        widget.start()
        assert widget.is_running is True
        QTest.qWait(300)
        assert frames, "动图要在事件循环里推进帧"
        assert widget.movie().currentFrameNumber() >= 1

        widget.stop()
        assert widget.is_running is False
    finally:
        widget.close()


def test_hide_stops_and_show_restarts(app, tmp_path: Path) -> None:
    gif = _write_gif(tmp_path / "anim.gif")
    widget = AnimatedImage(gif, 320)
    try:
        widget.show()
        assert widget.is_running is True
        widget.hide()
        assert widget.is_running is False
        widget.show()
        assert widget.is_running is True
    finally:
        widget.close()


def test_set_path_reuses_the_movie_for_the_same_path(app, tmp_path: Path) -> None:
    gif = _write_gif(tmp_path / "anim.gif")
    bigger = _write_gif(tmp_path / "bigger.gif", _wider_gif())
    widget = AnimatedImage(gif, 320)
    try:
        movie = widget.movie()
        widget.set_path(gif)
        assert widget.movie() is movie, "同一路径重复设置不重建 movie"

        widget.set_path(bigger)
        assert widget.movie_path == str(bigger)
        assert widget.movie() is not movie
        assert widget.logical_size() == (16, 8)
        assert widget.size().toTuple() == (16, 8)
    finally:
        widget.close()


def test_set_max_side_rescales_without_changing_the_path(app, tmp_path: Path) -> None:
    gif = _write_gif(tmp_path / "anim.gif")
    widget = AnimatedImage(gif, 320)
    try:
        widget.set_max_side(6)
        assert widget.logical_size() == (6, 3)
        assert widget.movie_path == str(gif)
        assert widget.movie().scaledSize().toTuple() == (6, 3)
    finally:
        widget.close()


def test_close_releases_the_movie(app, tmp_path: Path) -> None:
    gif = _write_gif(tmp_path / "anim.gif")
    widget = AnimatedImage(gif, 320)
    widget.show()
    assert widget.is_running is True

    widget.close()

    assert widget.is_running is False
    assert widget.movie() is None


def test_missing_file_degrades_to_a_placeholder(app, tmp_path: Path) -> None:
    widget = AnimatedImage(tmp_path / "missing.gif", 320)
    try:
        assert widget.logical_size() == (1, 1)
        assert widget.is_running is False
    finally:
        widget.close()
