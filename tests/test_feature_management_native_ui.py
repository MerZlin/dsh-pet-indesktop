"""Real Windows widgets at 175% DPI; not offscreen or desktop screenshots."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows High DPI gate")
@pytest.mark.parametrize("scale", [1.0, 1.75])
@pytest.mark.parametrize("width", [720, 1100])
@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("language", ["zh", "en"])
def test_native_management_high_dpi_keyboard_layout_and_own_widget_capture(tmp_path, width, theme, language, scale):
    environment = dict(os.environ, QT_SCALE_FACTOR=str(scale))
    environment.pop("QT_QPA_PLATFORM", None)
    payload = {
        "data": str(tmp_path / "owned-data"),
        "screenshot": str(tmp_path / "owned-dialog.png"),
        "width": width,
        "theme": theme,
        "language": language,
        "scale": scale,
    }
    result = subprocess.run(
        [sys.executable, "-m", "tests._feature_ui_child"],
        cwd=Path(__file__).resolve().parents[1],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=environment,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value["passed"] and value["management_only"] and value["device_pixel_ratio"] >= scale - 0.05
    (tmp_path / "native-ui-evidence.json").write_text(json.dumps(value, indent=2), encoding="utf-8")
