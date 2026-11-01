"""聊天窗口的公开契约。

mod 只能通过 ``HostPorts.chat`` 驱动 core 的聊天窗口：窗口、Markdown 渲染、会话
落盘和取消都在 core。``owner`` 是 mod id，窗口按 ``(owner, pet_slot)`` 隔离，
互不串流。

运行时代码不得 import PySide6；需要回 GUI 线程用 ``HostPorts.scheduler``。
只有设置页（Core 托管的受信 UI）可以用 QtWidgets，但同样不得自建聊天窗口或
直接操作气泡。
"""

from __future__ import annotations

from typing import Callable, Protocol


class ChatPort(Protocol):
    def open(self, owner: str, *, pet_slot: int | None = None, title: str = "") -> bool: ...
    def append(self, owner: str, role: str, text: str, *, pet_slot: int | None = None) -> None: ...
    def token(self, owner: str, delta: str, *, pet_slot: int | None = None) -> None: ...
    def reasoning(self, owner: str, delta: str, *, pet_slot: int | None = None) -> None:
        """深度思考的思维链增量；只进助手气泡的折叠面板，不进正文。"""
        ...

    def status(self, owner: str, text: str, *, pet_slot: int | None = None) -> None:
        """联网检索等进度行（「🔍 正在搜索：…」）；只进助手气泡的活动面板。"""
        ...

    def done(self, owner: str, text: str = "", *, pet_slot: int | None = None) -> None: ...
    def error(
        self, owner: str, reason: str, message: str = "", *, pet_slot: int | None = None
    ) -> None: ...
    def close(self, owner: str, *, pet_slot: int | None = None) -> None: ...
    def cancel(self, owner: str, *, pet_slot: int | None = None) -> None: ...
    def is_open(self, owner: str, *, pet_slot: int | None = None) -> bool: ...
    def show_view(
        self, owner: str, view_id: str, *, pet_slot: int | None = None
    ) -> bool:
        """打开/复用对话窗并切到该扩展的 ``chat_view`` 页。

        没有该视图（扩展没装、没声明、或工厂缺失）时返回 ``False``，不新建窗口。
        """
        ...
    def history(self, owner: str, *, pet_slot: int | None = None) -> list[dict]: ...
    def record(
        self, owner: str, role: str, text: str, *, pet_slot: int | None = None
    ) -> None:
        """只写会话（和已存在的窗口），不新建/不唤出窗口。

        「看看屏幕」这类后台结果要进聊天记录，但不得打断用户当前操作。
        """
        ...

    def take_attachments(
        self, owner: str, *, pet_slot: int | None = None
    ) -> list[dict]:
        """取走并清空最近一次提交携带的附件描述（文本/图片 data URL）。"""
        ...

    def on_submit(
        self, owner: str, callback: Callable[[str, int], None]
    ) -> Callable[[], None]: ...
    def on_stop(self, owner: str, callback: Callable[[int], None]) -> Callable[[], None]: ...
    def register_channel(
        self,
        channel: str,
        on_submit: Callable[[str, int, str], None],
        on_stop: Callable[[int, str], None] | None = None,
    ) -> Callable[[], None]:
        """注册外部通道（dsh/qq）的提交与停止处理；``ai`` 仍用 ``on_submit``。"""
        ...

    def open_channel(
        self,
        channel: str,
        conversation_id: str,
        title: str = "",
        *,
        owner: str,
        pet_slot: int | None = None,
    ) -> str:
        """打开/新建外部通道会话，返回本地 session id。"""
        ...

    def ingest(
        self, record: dict, *, owner: str, pet_slot: int | None = None
    ) -> str:
        """幂等写入一条外部消息（按 external_id 去重）；不主动创建窗口。"""
        ...

    def messages(
        self, session_id: str, *, owner: str, pet_slot: int | None = None
    ) -> list[dict]: ...


class ChatCancelled(RuntimeError):
    """调用方主动停止；界面按中性状态显示。"""


class QuickChatPort(Protocol):
    """桌宠旁的「快速对话」气泡（窗口在 Core，模型调用在 mod）。

    与 :class:`ChatPort` 共用同一份会话：提交前 Core 先落 user，完成后落
    assistant；因此 `去聊天窗` 能看到同一段对话。``token/reasoning/status`` 与
    `ChatPort` 同义，只是驱动紧凑气泡而不是完整窗口。
    """

    def open(self, owner: str, *, pet_slot: int | None = None) -> bool: ...
    def close(self, owner: str, *, pet_slot: int | None = None) -> None: ...
    def is_open(self, owner: str, *, pet_slot: int | None = None) -> bool: ...
    def token(self, owner: str, delta: str, *, pet_slot: int | None = None) -> None: ...
    def reasoning(self, owner: str, delta: str, *, pet_slot: int | None = None) -> None: ...
    def status(self, owner: str, text: str, *, pet_slot: int | None = None) -> None: ...
    def done(self, owner: str, text: str = "", *, pet_slot: int | None = None) -> None: ...
    def error(
        self, owner: str, reason: str, message: str = "", *, pet_slot: int | None = None
    ) -> None: ...
    def history(self, owner: str, *, pet_slot: int | None = None) -> list[dict]: ...
    def on_submit(
        self, owner: str, callback: Callable[[str, int], None]
    ) -> Callable[[], None]: ...
    def open_full_chat(self, owner: str, *, pet_slot: int | None = None) -> None: ...
