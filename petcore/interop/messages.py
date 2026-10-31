"""消息账本：单调 seq、连续已读水位、未读快照折叠。

DSH/QQ 的来源消息在入会话库前先经过这里，保证：
- 每个会话有本地单调 ``seq``（不是平台 messageId）；
- ``read_through`` 只能推进到真的展示给模型/用户看过的位置；
- 一次注入太多未读时折叠成 ``folded``，让模型知道「更早还有 N 条，不必逐条回」。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MessageRecord:
    seq: int
    text: str
    role: str = "user"
    channel: str = "pet"
    conversation_id: str = ""
    sender: str = ""
    external_id: str = ""
    ts: float = 0.0
    meta: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "seq": int(self.seq),
            "text": str(self.text),
            "role": str(self.role),
            "channel": str(self.channel),
            "conversation_id": str(self.conversation_id),
            "sender": str(self.sender),
            "external_id": str(self.external_id),
            "ts": float(self.ts),
            "meta": dict(self.meta),
        }


class Watermark:
    """单会话的已读水位；写入方只 append，读取方只推进 read_through。"""

    def __init__(self, read_through: int = 0) -> None:
        self.read_through = max(0, int(read_through))
        self._next = self.read_through + 1
        self._records: list[MessageRecord] = []

    @property
    def next_seq(self) -> int:
        return self._next

    def append(
        self,
        text: str,
        *,
        role: str = "user",
        channel: str = "pet",
        conversation_id: str = "",
        sender: str = "",
        external_id: str = "",
        ts: float = 0.0,
        meta: dict | None = None,
    ) -> MessageRecord:
        record = MessageRecord(
            seq=self._next,
            text=str(text or ""),
            role=str(role or "user"),
            channel=str(channel or "pet"),
            conversation_id=str(conversation_id or ""),
            sender=str(sender or ""),
            external_id=str(external_id or ""),
            ts=float(ts or 0.0),
            meta=dict(meta or {}),
        )
        self._next += 1
        self._records.append(record)
        return record

    def unread(self) -> list[MessageRecord]:
        return [item for item in self._records if item.seq > self.read_through]

    def acknowledge(self, through_seq: int | None = None) -> int:
        if through_seq is None:
            target = self._next - 1
        else:
            target = max(0, int(through_seq))
        # 只能推进到当前已知最大 seq，不能越过未来。
        target = min(target, self._next - 1)
        self.read_through = max(self.read_through, target)
        return self.read_through

    def snapshot(
        self, *, limit: int = 12, recent_limit: int = 4
    ) -> dict:
        unread = self.unread()
        if not unread:
            return {
                "messages": [],
                "unread_count": 0,
                "included_unread_count": 0,
                "read_through_seq": self.read_through,
                "partial": False,
            }
        included = unread[-max(1, int(limit)) :]
        folded = unread[: len(unread) - len(included)]
        recent = [
            item
            for item in self._records
            if item.seq <= self.read_through
        ][-max(0, int(recent_limit)) :]
        snapshot = {
            "messages": [item.as_dict() for item in included],
            "recent": [item.as_dict() for item in recent],
            "unread_count": len(unread),
            "included_unread_count": len(included),
            "read_through_seq": self.read_through,
            "partial": bool(folded),
        }
        if folded:
            snapshot["folded"] = {
                "count": len(folded),
                "from_seq": folded[0].seq,
                "to_seq": folded[-1].seq,
            }
        return snapshot
