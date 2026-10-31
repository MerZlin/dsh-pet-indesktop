from __future__ import annotations

from petcore.platform import capture_probe


def test_nonblack_ratio_counts_any_rgb_channel() -> None:
    black = bytes([0, 0, 0, 255]) * 4
    assert capture_probe.nonblack_ratio(black, 2, 2) == 0.0

    half = bytes([0, 0, 0, 255]) * 2 + bytes([0, 0, 64, 255]) * 2
    assert capture_probe.nonblack_ratio(half, 2, 2) == 0.5
    assert capture_probe.nonblack_ratio(half, 2, 2, threshold=64) == 0.0
    assert capture_probe.nonblack_ratio(b"", 2, 2) == 0.0
    assert capture_probe.nonblack_ratio(black, 0, 2) == 0.0


def test_probe_is_supported_only_on_windows() -> None:
    report = capture_probe.probe_window(0)
    assert isinstance(report, dict)
    assert "supported" in report


def test_config_normalizes_keycolor_and_color(tmp_path) -> None:
    from petcore.config import Config

    config = Config(
        tmp_path,
        {
            "stream_capture_keycolor": True,
            "stream_capture_mode": False,
            "stream_key_color": "not-a-color",
        },
    )
    assert config.get("stream_capture_mode") is True
    assert config.get("stream_key_color") == "#00FF00"

    config.set("stream_capture_keycolor", False)
    config.set("stream_capture_mode", False)
    config.set("stream_key_color", "#ff00aa")
    assert config.get("stream_capture_mode") is False
    assert config.get("stream_key_color") == "#ff00aa"


def test_apply_stream_window_clears_layered_only_for_keycolor(monkeypatch) -> None:
    from petcore.platform import win as win_mod

    class FakeUser32:
        def __init__(self) -> None:
            self.style = 0x00000080 | 0x08000000 | 0x00080000

        def GetWindowLongW(self, hwnd, index):
            return self.style

        def SetWindowLongW(self, hwnd, index, value):
            self.style = value
            return value

    fake = FakeUser32()
    monkeypatch.setattr(win_mod.os, "name", "nt")
    monkeypatch.setattr(win_mod, "_user32", lambda: fake)
    win_mod.apply_stream_window(1, keycolor=False)
    assert fake.style & 0x00080000, "透明直播模式必须保留 layered"
    fake.style = 0x00000080 | 0x08000000 | 0x00080000
    win_mod.apply_stream_window(1, keycolor=True)
    assert not fake.style & 0x00080000, "色键模式必须清掉 layered"
    assert not fake.style & 0x00000080
    assert not fake.style & 0x08000000
