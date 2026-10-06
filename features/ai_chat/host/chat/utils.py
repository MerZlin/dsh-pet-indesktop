"""共享聊天纯函数：会话标题等字符串/数据变换。

两套 UI 各自维护过一份几乎相同的 _short_title；本模块统一实现，通过
`localize_time` 参数保留两边的历史行为差异（详见函数 docstring）。
"""

from __future__ import annotations

from datetime import datetime


def _safe_emit(obj, signal_name: str, *args) -> None:
    """从 worker 线程向 Qt 对象发射信号的安全闸。

    连接测试 worker（最长 10s）在飞时，对话框可能已被关闭销毁
    （WA_DeleteOnClose）：对已删 C++ 对象访问信号属性或 emit 都是
    RuntimeError（异常崩在 worker 线程里）。isValid 判断覆盖「已删」，
    try/except 覆盖「判断后、emit 前被删」的竞态。信号名以字符串传入——
    已删对象上连 ``obj.sig`` 的属性访问都会炸，必须在 try 块内 getattr。
    """
    try:
        import shiboken6

        if shiboken6.isValid(obj):
            getattr(obj, signal_name).emit(*args)
    except RuntimeError:
        pass


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
