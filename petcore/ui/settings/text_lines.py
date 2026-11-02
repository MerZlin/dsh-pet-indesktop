"""多行台词/列表类设置的统一解析：一行一条，兼容旧的 `a | b` 单行写法。"""

from __future__ import annotations

LINE_SEPARATOR = "|"
FULL_WIDTH_SEPARATOR = "｜"


def parse_lines(text: str) -> list[str]:
    """按行切分，顺带拆开行内的 `|` / `｜`，丢掉空白项。"""
    items: list[str] = []
    normalized = str(text or "").replace(FULL_WIDTH_SEPARATOR, LINE_SEPARATOR)
    for raw in normalized.splitlines():
        for part in raw.split(LINE_SEPARATOR):
            value = part.strip()
            if value:
                items.append(value)
    return items


def format_lines(items) -> str:
    return "\n".join(str(item).strip() for item in (items or ()) if str(item).strip())


def merge_lines(configured, defaults) -> list[str]:
    """用户内容在前、内置初始句在后：去空、去重，保留用户自己的顺序。

    设置页用它把「初始台词」显示进编辑框；写回配置仍由页面的保存/取消语义
    决定，不在打开页面时静默覆盖。
    """
    values: list[str] = []
    seen: set[str] = set()
    for item in list(configured or ()) + list(defaults or ()):
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        values.append(text)
    return values
