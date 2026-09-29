"""VALIDATION ONLY: fixed image/foreground boundary and loopback HTTP.

Never part of a normal Worker. The real Worker execution, JPEG encoding, IPC,
quota handshake and HTTP parsing remain unchanged. No user screen is accessed.
"""

from __future__ import annotations

import io
from urllib.parse import urlsplit

from PIL import Image

from features.screen_understanding.worker import vision
from features.screen_understanding.worker.runtime import run_proactive_screen_worker


def install_synthetic_boundary():
    def foreground():
        return {"hwnd": 42, "pid": 7, "process": "fixture.exe", "title": "synthetic", "rect": [0, 0, 100, 80]}

    def capture_rect(rect):
        return Image.new("RGB", (100, 80), "navy")

    def capture_screen():
        output = io.BytesIO()
        capture_rect(None).save(output, "JPEG")
        return output.getvalue()

    original_request = vision._post_vision_request

    def loopback_request(jpeg, app_info, prompt, provider, **kwargs):
        url = urlsplit(provider.base_url)
        if url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port or url.username or url.password:
            raise ValueError("synthetic validation only accepts loopback HTTP")
        return original_request(jpeg, app_info, prompt, provider, **kwargs)

    vision.foreground_window_info = foreground
    vision.capture_window_rect = capture_rect
    vision.capture_screen_bytes = capture_screen
    vision._post_vision_request = loopback_request


if __name__ == "__main__":
    install_synthetic_boundary()
    raise SystemExit(run_proactive_screen_worker())
