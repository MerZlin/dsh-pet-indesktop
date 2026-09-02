"""共享聊天纯函数：会话标题等字符串/数据变换。

两套 UI 各自维护过一份几乎相同的 _short_title；本模块统一实现，通过
`localize_time` 参数保留两边的历史行为差异（详见函数 docstring）。
"""

from __future__ import annotations

from datetime import datetime


def _short_title(session, *, localize_time: bool = True) -> str:
    """返回会话的显示标题。

    规则（与两套 UI 历史实现一致）：
    1. 自定义标题优先（去空白后为空则忽略）；
    2. 否则取第一条非空 user 消息内容，压缩空白后截断到 24 字符（超长加 …）；
    3. 否则回退为「新会话 · HH:MM」，created_at 解析失败则仅「新会话」。

    `localize_time` 控制回退标题中时间戳的时区处理：
    - True（默认，Modern 历史行为；批10 产品修复后 Legacy 调用点同样走默认
      True）：timezone-aware 的 created_at 先转换到本地时区再格式化
      （UTC 存储 → 本地钟点，见 test_requested_regressions.py 固定用例）；
    - False（批10 前 Legacy 的历史行为，现无生产调用方，仅作 API 兼容保留）：
      按存储的钟点原样格式化，不做本地转换。
    naive 时间戳两者结果一致（astimezone 对 naive 视为本地时间、钟点不变）。
    """
    if str(getattr(session, "custom_title", "")).strip():
        return str(session.custom_title).strip()
    for message in session.messages:
        if message.role == "user" and message.content.strip():
            text = " ".join(message.content.split())
            return text[:24] + ("…" if len(text) > 24 else "")
    try:
        created = datetime.fromisoformat(session.created_at)
        if localize_time:
            created = created.astimezone()
        return "新会话 · " + created.strftime("%H:%M")
    except (TypeError, ValueError):
        return "新会话"


def resync_session_from_disk(store, session, character_id):
    """多前端共享会话目录的陈旧快照防护（审查 DS-M7）。

    现代/经典聊天窗与 QuickChat 共享同一会话目录且各持内存快照：
    本窗口打开期间另一前端写入的消息，会被本窗口下次 save 整体覆盖
    （静默丢消息）。发送消息前调用本函数对齐：磁盘版本更新则返回磁盘
    版本，否则原样返回。失败一律原样返回（宁可保持旧行为也不阻断发送）。
    """
    if session is None or not getattr(session, "session_id", ""):
        return session
    try:
        disk = store.load(session.session_id, character_id)
    except Exception:
        return session
    if disk is None:
        return session
    if str(disk.updated_at) > str(session.updated_at):
        return disk
    return session
