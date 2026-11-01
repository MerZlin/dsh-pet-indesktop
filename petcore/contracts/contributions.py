from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CONTRIBUTION_KINDS = frozenset(
    {
        "menu",
        "command",
        "settings_page",
        # 对话窗里的一页（`view_factories` 提供 QWidget）。契约见
        # docs/PETCORE-MOD-FORMAT.md §5.2：只给槽位，不给窗口。
        "chat_view",
        "hotkey",
        "card",
        "phrases",
        "sound",
        "theme",
        "seasonal",
        "install_hook",
    }
)


@dataclass(frozen=True, slots=True)
class Contribution:
    kind: str
    id: str
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in CONTRIBUTION_KINDS:
            raise ValueError(f"unknown contribution kind: {self.kind}")
        if not self.id:
            raise ValueError("contribution id must not be empty")


@dataclass(frozen=True, slots=True)
class SettingsPageSpec:
    id: str
    title: str
    factory: Any
    icon: str = ""
    order: int = 500
    owner: str = ""
    label: str = ""
    group: str = "base"
    #: 非空 = 该页由同名 Core 设置页内嵌（"interaction" / "menu" / "about"），
    #: 不再进扩展管理的导航。
    host_page: str = ""

    def __post_init__(self) -> None:
        if not self.id or not self.title:
            raise ValueError("settings page requires id and title")


@dataclass(frozen=True, slots=True)
class ChatViewSpec:
    """对话窗里的一页。

    ``id`` 是 ``"<owner>:<contribution id>"``，在窗口内唯一；``factory`` 返回一个
    QWidget，由 core 放进 ``view_stack``。core 只负责槽位与生命周期，窗口对象不
    外泄——契约见 ``docs/PETCORE-MOD-FORMAT.md`` §5.2。
    """

    id: str
    owner: str
    view_id: str
    label: str
    factory: Any
    icon: str = ""
    order: int = 500

    def __post_init__(self) -> None:
        if not self.id or not self.label:
            raise ValueError("chat view requires id and label")
        if not callable(self.factory):
            raise ValueError("chat view requires a callable factory")
