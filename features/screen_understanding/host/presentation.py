"""Manual-result presentation and optional text sync; no window or chat imports."""

from __future__ import annotations

import logging
from collections.abc import Callable

from .config import VisionConfigService


def present_manual_result(
    service: VisionConfigService,
    revision: str | None,
    text: str,
    user_text: str,
    is_error: bool,
    show_bubble: Callable[[str, int], None],
    sync_text: Callable[[str, str], None] | None,
) -> None:
    if revision is not None and revision != service.revision():
        return
    if is_error:
        show_bubble(f"看不清啊…{text[:60]}", 5000)
        return
    show_bubble(text, max(4000, min(12000, len(text) * 150)))
    if callable(sync_text):
        try:
            sync_text(user_text, text)
        except Exception:
            logging.warning("识屏文字同步不可用；已保留气泡展示")
