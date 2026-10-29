from __future__ import annotations

from PySide6.QtGui import QPixmap


def frame_pixmap(frame):
    """把一帧变成绘制用 ``QPixmap``（不缩放、不镜像、DPR=1）。

    缩放/镜像都留给 ``paintEvent``：同一帧可能被重复绘制（expose、Q 弹），
    在那里按目标矩形绘制比每帧先造一张缩放后的中间 pixmap 更省（实测
    461×259 0.73→0.47 ms、1920×1080 5.99→2.93 ms，见 PR 报告）。
    """
    if frame is None:
        return None
    pixmap = frame if isinstance(frame, QPixmap) else QPixmap.fromImage(frame)
    if pixmap.isNull():
        return None
    return pixmap
