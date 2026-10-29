"""点击 Q 弹的纯几何。"""

from __future__ import annotations

import math

SQUASH_DURATION_MS = 220
SQUASH_SX = 0.10
SQUASH_SY = 0.15


def squash_geometry(
    window_width: int,
    window_height: int,
    frame_width: int,
    frame_height: int,
    progress: float,
    *,
    strength: float = 1.0,
    bounces: int = 1,
) -> tuple[int, int, int, int]:
    """返回 Q 弹帧在窗口内的绘制矩形（底部居中，先压扁再横向鼓出）。

    ``bounces`` 是这次触发内的压扁次数：1 = 现在的单次 Q 弹；>1 时用
    ``abs(sin(pi * bounces * p))`` 连续压/鼓多次，并按进度轻微衰减，避免
    第一次就把振幅吃满。``strength`` 是弹性系数（1.0 = 现有幅度）。
    矩形始终从底边向上长，不会越过窗口顶部。
    """
    progress = max(0.0, min(1.0, float(progress)))
    bounces = max(1, min(5, int(bounces)))
    strength = max(0.3, min(2.0, float(strength)))
    pulse = abs(math.sin(math.pi * bounces * progress)) * strength
    if bounces > 1:
        pulse *= max(0.55, 1.0 - 0.25 * (bounces - 1) * progress)
    width = max(1, int(round(frame_width * (1.0 + SQUASH_SX * pulse))))
    height = max(1, int(round(frame_height * (1.0 - SQUASH_SY * pulse))))
    x = int(round((window_width - width) / 2))
    y = window_height - height
    return x, y, width, height
