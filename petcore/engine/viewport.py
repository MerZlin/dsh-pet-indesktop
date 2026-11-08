"""Logical canvas -> work-area-contained native viewport (no Qt, no DPI scaling)."""
from __future__ import annotations

from dataclasses import dataclass

from .geometry import Rect


@dataclass(frozen=True)
class Viewport:
    native: Rect
    offset: tuple[int, int]


def viewport_for(canvas: Rect, workarea: Rect) -> Viewport:
    x, y, width, height = canvas
    left, top, aw, ah = workarea
    width, height = max(1, min(width, aw)), max(1, min(height, ah))
    nx = max(left, min(left + aw - width, x))
    ny = max(top, min(top + ah - height, y))
    return Viewport((nx, ny, width, height), (x - nx, y - ny))
