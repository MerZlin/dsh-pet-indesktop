"""角色重命名的输入框与反馈文案。

右键「切换角色 → 重命名当前角色…」和设置页「角色 → 重命名…」共用这一份：两处
必须问同一个问题、按同一个上限截断、冒同一句话，否则用户会以为「菜单能起 30 字、
设置页只能 24 字」。
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import QInputDialog, QWidget

from ..config import ALIAS_MAX_CHARS

__all__ = [
    "ALIAS_MAX_CHARS",
    "AliasPrompt",
    "alias_bubble_text",
    "alias_prompt_text",
    "prompt_alias",
]

#: 输入框标题（两处入口一致）。
_TITLE = "重命名角色"
#: 「给 {名字} 起个名字（留空恢复默认）：」——名字用**显示名**（目录名，不是 id），
#: 用户看到的是「Shenshen」而不是内部 id。
_PROMPT = "给 {name} 起个名字（留空恢复默认，最多 {limit} 个字）："


@dataclass(frozen=True)
class AliasPrompt:
    """一次重命名输入的结果；``accepted`` 为假时其余字段无意义。"""

    accepted: bool
    #: 已 strip + 截断的名字；空串 = 恢复默认。
    name: str = ""
    #: 输入超过上限、被截断了（要在气泡里说清，不能悄悄吞掉半句话）。
    truncated: bool = False


def alias_prompt_text(default_name: str) -> str:
    return _PROMPT.format(
        name=str(default_name or "").strip() or "这个角色", limit=ALIAS_MAX_CHARS
    )


def prompt_alias(
    parent: QWidget | None,
    *,
    default_name: str,
    current: str = "",
) -> AliasPrompt:
    """弹出重命名输入框。取消（含 Esc/关窗）返回 ``accepted=False``，不改任何东西。"""
    text, ok = QInputDialog.getText(
        parent,
        _TITLE,
        alias_prompt_text(default_name),
        text=str(current or ""),
    )
    if not ok:
        return AliasPrompt(False)
    # 静态 getText 设不了输入上限，只能在取回时兜：换行拍平成空格（配置层会丢弃
    # 控制字符，那样名字会静默消失），再按字符截断。
    cleaned = str(text or "").replace("\r", " ").replace("\n", " ").strip()
    truncated = len(cleaned) > ALIAS_MAX_CHARS
    return AliasPrompt(True, cleaned[:ALIAS_MAX_CHARS], truncated)


def alias_bubble_text(result: AliasPrompt, default_name: str) -> str:
    """改完之后的气泡文案；空别名回落到目录显示名。"""
    shown = result.name or str(default_name or "").strip()
    if result.truncated:
        return f"角色名：{shown}（最多 {ALIAS_MAX_CHARS} 个字）"
    return f"角色名：{shown}"
