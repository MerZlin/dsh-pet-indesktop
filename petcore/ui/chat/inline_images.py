"""聊天消息里的本地图片：从 Markdown 抽出来，做成内联贴纸控件。

只认**本地存在的文件**与 ``data:`` URL；``http(s)`` 留给
:func:`markdown_view.markdown_to_html` 显示占位，UI 线程不联网。
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ...media.images import (
    is_animated,
    load_pixmap,
    logical_size,
    scale_pixmap,
    screen_dpr,
)
from ..animated_image import AnimatedImage

#: 主聊天窗一条消息里最多渲染几张图（防止模型一次贴十张把时间线撑爆）。
MAX_INLINE_IMAGES = 3
#: 主聊天窗贴纸的逻辑边长上限；快速对话更窄。
INLINE_IMAGE_MAX_SIDE = 240
QUICK_IMAGE_MAX_SIDE = 160
#: data URL 超过这个长度不解码（模型不太可能生成合法的大图，防内存峰值）。
MAX_DATA_URL_CHARS = 2_000_000

_IMAGE = re.compile(r"!\[([^\]]*)\]\(\s*(?:<([^>]*)>|([^\s)]+))\s*\)")
#: 围栏里只有一张本地图片：模型常把图片语法包进代码块，这里只解包这一种。
_FENCE_IMAGE = re.compile(
    r"(?m)^[ \t]*(```|~~~)[ \t]*[A-Za-z0-9_-]*[ \t]*\n"
    r"[ \t]*(!\[[^\]]*\]\(\s*(?:<[^>]*>|[^\n]*?)\s*\))[ \t]*\n"
    r"[ \t]*\1[ \t]*$"
)
#: 任意围栏代码块：里面的图片语法是**代码示例**，不能被抽成贴纸。
_FENCE_BLOCK = re.compile(
    r"(?ms)^[ \t]*(```|~~~)[^\n]*\n.*?^[ \t]*\1[ \t]*$"
)


@dataclass(frozen=True)
class InlineImage:
    """一条可渲染的图片引用；``path`` 与 ``data_url`` 二选一。"""

    alt: str
    path: Path | None = None
    data_url: str = ""


def _local_path(source: str) -> Path | None:
    text = str(source or "").strip()
    if not text or text.lower().startswith(("http://", "https://")):
        return None
    if text.startswith("data:"):
        return None
    if text.lower().startswith("file://"):
        # QUrl 处理 Windows 盘符与 percent-encoding；UI 层不引 urllib（架构守卫）。
        local = QUrl(text).toLocalFile()
        if not local:
            return None
        text = local
    try:
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        return path if path.is_file() else None
    except (OSError, ValueError):
        return None


def _parse_token(token: str) -> InlineImage | None:
    match = _IMAGE.fullmatch(str(token or "").strip())
    if match is None:
        return None
    alt = str(match.group(1) or "").strip()
    source = str(match.group(2) or match.group(3) or "").strip()
    if not source:
        return None
    if source.startswith("data:image/"):
        if len(source) > MAX_DATA_URL_CHARS:
            return None
        return InlineImage(alt=alt, data_url=source)
    path = _local_path(source)
    if path is None:
        return None
    return InlineImage(alt=alt, path=path)


def extract_inline_images(text: str) -> tuple[str, list[InlineImage]]:
    """把本地/data 图片从 Markdown 里抽走，返回 ``(清理后正文, 图片)``。

    远程图片、普通代码块、找不到文件的语法示例都原样保留，交给
    :func:`markdown_view.markdown_to_html` 按普通 Markdown 处理。
    """
    source = str(text or "")

    def _unwrap(match: re.Match[str]) -> str:
        token = match.group(2)
        return token if _parse_token(token) is not None else match.group(0)

    source = _FENCE_IMAGE.sub(_unwrap, source)
    # 先把剩余围栏代码块占位，避免把「代码示例里的图片语法」抽成贴纸；
    # 图片-only 围栏已经在上一步解包，不会被这里吞掉。
    blocks: list[str] = []

    def _stash(match: re.Match[str]) -> str:
        blocks.append(match.group(0))
        return f"\x00FENCE{len(blocks) - 1}\x00"

    protected = _FENCE_BLOCK.sub(_stash, source)
    refs: list[InlineImage] = []
    spans: list[tuple[int, int]] = []
    for match in _IMAGE.finditer(protected):
        token = match.group(0)
        ref = _parse_token(token)
        if ref is None:
            continue
        refs.append(ref)
        spans.append(match.span())
    if not spans:
        return source, []
    pieces: list[str] = []
    cursor = 0
    for start, end in spans:
        pieces.append(protected[cursor:start])
        cursor = end
    pieces.append(protected[cursor:])
    cleaned = "".join(pieces)
    cleaned = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", cleaned).strip()
    for index, block in enumerate(blocks):
        cleaned = cleaned.replace(f"\x00FENCE{index}\x00", block)
    return cleaned, refs


def _data_url_pixmap(source: str, max_side: int, dpr: float) -> QPixmap | None:
    header, _sep, payload = str(source or "").partition(",")
    if ";base64" not in header.lower() or not payload:
        return None
    try:
        raw = base64.b64decode(payload, validate=False)
    except (ValueError, TypeError):
        return None
    image = QImage.fromData(raw)
    if image.isNull():
        return None
    return scale_pixmap(QPixmap.fromImage(image), max_side, dpr)


def create_sticker(
    ref: InlineImage,
    parent: QWidget,
    max_side: int,
    *,
    animate: bool = False,
) -> QWidget | None:
    """按 ``ref`` 造一张贴纸；动图只有 ``animate`` 为真才起播。"""
    if ref.path is not None:
        if animate and is_animated(ref.path):
            widget = AnimatedImage(ref.path, max_side, parent)
            if widget.logical_size()[0] > 1:
                return widget
            widget.close()
            widget.deleteLater()
        pixmap = load_pixmap(ref.path, max_side, screen_dpr(parent))
        if pixmap is None or pixmap.isNull():
            return None
    else:
        pixmap = _data_url_pixmap(ref.data_url, max_side, screen_dpr(parent))
        if pixmap is None or pixmap.isNull():
            return None
    label = QLabel(parent)
    label.setObjectName("bubble-inline-image")
    label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    label.setPixmap(pixmap)
    label.setFixedSize(*logical_size(pixmap))
    return label


def clear_stickers(layout: QVBoxLayout) -> None:
    """清空贴纸布局：先停 QMovie，再交给 Qt 销毁。"""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is None:
            continue
        close = getattr(widget, "close", None)
        if callable(close):
            try:
                close()
            except RuntimeError:
                pass
        widget.deleteLater()
