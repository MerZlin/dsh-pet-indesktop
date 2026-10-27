# -*- coding: utf-8 -*-
"""外部截图后端（vision_capture_command）的行为约束。

公开 seam：`vision.set_capture_command` + 两个截图入口
（`capture_screen_bytes` / `capture_window_rect`）。这里用真实的子进程
（当前解释器 + 一小段写 PNG 的代码）当「外部命令」，验证占位符替换与失败回落，
不 mock subprocess 本身。
"""

from __future__ import annotations

import sys

import pytest
from PIL import Image

from pet import vision

# 一个最小的「外部截图命令」：把收到的 mode/rect 写进 <out>.args，再往 {out} 写张 PNG。
_FAKE = (
    "import sys; from PIL import Image; "
    "open(sys.argv[3] + '.args', 'w', encoding='utf-8').write(sys.argv[1] + '|' + sys.argv[2]); "
    "Image.new('RGB', (64, 32), 'red').save(sys.argv[3])"
)


def _fake_command():
    return [sys.executable, "-c", _FAKE, "{mode}", "{rect}", "{out}"]


@pytest.fixture(autouse=True)
def _reset_capture_command():
    """每条用例后回到内置实现，避免模块级状态串味。"""
    yield
    vision.set_capture_command([], None)


def test_empty_command_means_builtin():
    vision.set_capture_command([])
    assert vision.capture_command() == ()
    vision.set_capture_command(None)
    assert vision.capture_command() == ()


def test_single_command_line_string_is_refused():
    """配置成整条命令行（字符串）必须被忽略——这条路不经过 shell。"""
    vision.set_capture_command("powershell -File C:/x.ps1 -Out {out}")
    assert vision.capture_command() == ()


def test_non_string_entries_are_refused():
    vision.set_capture_command(["python", 42])
    assert vision.capture_command() == ()
    vision.set_capture_command(["python", "  "])
    assert vision.capture_command() == ()


def test_argv_is_kept_and_placeholders_replaced_for_region():
    vision.set_capture_command(_fake_command())
    assert vision.capture_command() == tuple(_fake_command())

    img = vision.capture_window_rect((10, 20, 64, 32))

    assert isinstance(img, Image.Image)
    assert img.size == (64, 32)


def test_region_and_full_modes_reach_the_command(tmp_path):
    """命令收到的 {mode}/{rect} 与两个入口一致：region 传真实矩形，full 传 0,0,0,0。"""
    seen = tmp_path / "seen.txt"
    code = (
        "import sys; from PIL import Image; "
        f"open({str(seen)!r}, 'a', encoding='utf-8').write(sys.argv[1] + '|' + sys.argv[2] + '\\n'); "
        "Image.new('RGB', (64, 32), 'red').save(sys.argv[3])"
    )
    vision.set_capture_command([sys.executable, "-c", code, "{mode}", "{rect}", "{out}"])

    vision.capture_window_rect((10, 20, 64, 32))
    vision.capture_screen_bytes()

    lines = seen.read_text(encoding="utf-8").splitlines()
    assert lines == ["region|10,20,64,32", "full|0,0,0,0"]


def test_failed_command_falls_back_to_builtin(monkeypatch, tmp_path):
    """外部命令失败时仍能截图：回落到内置 ImageGrab，且命令确实被执行过。"""
    marker = tmp_path / "ran.txt"
    code = f"open({str(marker)!r}, 'w').write('ran'); raise SystemExit(3)"
    vision.set_capture_command([sys.executable, "-c", code, "{out}"])

    def fake_grab(*args, **kwargs):
        return Image.new("RGB", (120, 60), "blue")

    monkeypatch.setattr("PIL.ImageGrab.grab", fake_grab)

    shot = vision.capture_screen_bytes()

    assert marker.exists(), "外部命令应当被执行"
    assert shot[:2] == b"\xff\xd8", "回落路径应当产出 JPEG"


def test_timeout_argument_is_validated():
    vision.set_capture_command(_fake_command(), "10")
    assert vision.capture_command() == tuple(_fake_command())      # 命令保留，超时忽略
    vision.set_capture_command(_fake_command(), -1)
    assert vision.capture_command() == tuple(_fake_command())
    vision.set_capture_command([], None)
    assert vision.capture_command() == ()
