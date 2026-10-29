"""把一次识别采样渲染成给人看的多行诊断文本（纯函数、零 I/O、零 Qt）。

设置页的「识别诊断」与 ``scripts/music_probe.py`` 共用这一份格式：用户报「不唱歌 /
歌词不对」时，把这段文本贴过来就能一眼看出是哪一层出的问题——三个来源各自看到了
什么、判定为什么这么下、被哪条规则挡下。

**不写任何隐私外的内容**：只有 app_id / 可执行名 / 曲名 / 状态与数值；没有 URL、
没有代理地址、没有进程路径、没有歌词正文。曲名与窗口标题里若带本地路径
（放本地文件时很常见），只留文件名——这段文本是给用户复制出去贴工单的。
"""

from __future__ import annotations

import re

from . import now_playing, recognizer

_MEDIA_TYPE_NAMES = {
    now_playing.MEDIA_TYPE_UNKNOWN: "UNKNOWN(0)",
    now_playing.MEDIA_TYPE_MUSIC: "MUSIC(1)",
    now_playing.MEDIA_TYPE_VIDEO: "VIDEO(2)",
    now_playing.MEDIA_TYPE_IMAGE: "IMAGE(3)",
}

#: 形如 ``C:\Users\me\Music\x.flac`` 的盘符路径：放本地文件时窗口标题会带上它。
_DRIVE_PATH_RE = re.compile(r"[A-Za-z]:[\\/]")
#: 曲名/窗口标题的最大展示长度（再长就不是排障信息了）。
_TITLE_LIMIT = 80


def _safe_title(text) -> str:
    """窗口标题 → 可安全贴进工单的展示文本。

    播放器放本地文件时窗口标题常含完整路径（`C:\\Users\\<用户名>\\Music\\x.flac`），
    而这段诊断文本的用途正是**让用户复制给别人看**。所以带盘符的路径只留文件名，
    其余按 :data:`_TITLE_LIMIT` 截断。
    """
    value = str(text or "")
    if _DRIVE_PATH_RE.search(value):
        value = value.replace("\\", "/").rsplit("/", 1)[-1]
    if len(value) > _TITLE_LIMIT:
        value = value[: _TITLE_LIMIT - 1] + "…"
    return value



def _media_type_name(value) -> str:
    try:
        return _MEDIA_TYPE_NAMES.get(int(value), f"UNKNOWN({value})")
    except (TypeError, ValueError):
        return "?"


def _smtc_lines(sample) -> list[str]:
    sessions = tuple(getattr(sample, "sessions", ()) or ())
    degraded = bool(getattr(sample, "smtc_degraded", False))
    if degraded and not sessions:
        # 系统媒体服务无响应（读取超时）：这一行就是排障的第一现场，别写成"没有会话"。
        return ["SMTC：读取超时（已降级：用播放器窗口标题 + 音频会话识别）"]
    if not sessions:
        return ["SMTC：没有会话"]
    lines = [f"SMTC：{len(sessions)} 个会话"]
    for session in sessions:
        state = "PLAYING" if bool(getattr(session, "playing", False)) else "非播放"
        marks = []
        if bool(getattr(session, "browser", False)):
            marks.append("浏览器")
        if bool(getattr(session, "vetoed", False)):
            marks.append("系统应用否决")
        suffix = f"  [{'、'.join(marks)}]" if marks else ""
        lines.append(
            f"  {getattr(session, 'app_id', '') or '未知应用'}  "
            f"{_media_type_name(getattr(session, 'media_type', 0))}  {state}{suffix}"
        )
    return lines


def _audio_lines(sample) -> list[str]:
    rows = tuple(getattr(sample, "audio", ()) or ())
    if not bool(getattr(sample, "audio_available", True)):
        return ["音频会话：本平台不可用（仅 Windows 能拿到 WASAPI 会话）"]
    active = sum(1 for row in rows if str(getattr(row, "state", "")) == "active")
    if not rows:
        return ["音频会话：0 个"]
    lines = [f"音频会话：{len(rows)} 个（Active {active}）"]
    for row in rows:
        lines.append(
            f"  {str(getattr(row, 'exe', '') or '未知进程')}  pid={getattr(row, 'pid', 0)}  "
            f"{str(getattr(row, 'state', '') or '?')}  "
            f"mute={'yes' if bool(getattr(row, 'muted', False)) else 'no'}  "
            f"peak={float(getattr(row, 'peak', 0.0) or 0.0):.2f}"
        )
    return lines


def _window_lines(sample) -> list[str]:
    rows = tuple(getattr(sample, "windows", ()) or ())
    if not bool(getattr(sample, "window_available", True)):
        return ["播放器窗口标题：本平台不可用"]
    if not rows:
        return ["播放器窗口标题：0 个已登记播放器的窗口"]
    lines = [f"播放器窗口标题：{len(rows)} 个"]
    for row in rows:
        lines.append(
            f"  {str(getattr(row, 'exe', '') or '?')}  pid={getattr(row, 'pid', 0)}  "
            f"「{_safe_title(getattr(row, 'title', ''))}」"
        )
    return lines


def _decision_lines(decision, lyric_state: str) -> list[str]:
    status = str(getattr(decision, "status", ""))
    label = {
        recognizer.STATUS_PLAYING: "在播",
        recognizer.STATUS_PAUSED: "暂停",
        recognizer.STATUS_ABSENT: "不在播",
        recognizer.STATUS_ERROR: "读取失败（保留上一次状态）",
    }.get(status, status or "?")
    source = {
        recognizer.SOURCE_SMTC: "系统媒体会话",
        recognizer.SOURCE_WINDOW: "播放器窗口标题",
        recognizer.SOURCE_NONE: "无",
    }.get(str(getattr(decision, "source", "")), str(getattr(decision, "source", "")))
    track = getattr(decision, "track", None)
    parts = [f"判定：{label} · 来源={source}"]
    if getattr(decision, "exe", ""):
        parts.append(f"进程={decision.exe}")
    if track is not None:
        parts.append(
            f"曲目=《{_safe_title(track.title) or '未知'}》"
            f"—{_safe_title(track.artist) or '未知歌手'}"
        )
    elif status == recognizer.STATUS_PLAYING:
        parts.append("曲目=认不出来（只唱歌，不取词）")
    confidence = str(getattr(decision, "confidence", "") or "")
    if confidence:
        parts.append(
            f"置信度={'有音频证据' if confidence == recognizer.CONFIDENCE_CONFIRMED else '按系统状态推断'}"
        )
    lines = [" · ".join(parts)]
    if lyric_state:
        lines.append(f"歌词：{lyric_state}")
    return lines


def render(
    sample,
    decision,
    *,
    lyric_state: str = "",
) -> str:
    """一次判定 → 诊断文本（设置页文本框与命令行探针共用）。"""
    lines: list[str] = []
    lines.extend(_smtc_lines(sample))
    lines.extend(_audio_lines(sample))
    lines.extend(_window_lines(sample))
    lines.append("")
    lines.extend(_decision_lines(decision, lyric_state))
    reasons = tuple(getattr(decision, "reasons", ()) or ())
    if reasons:
        lines.append("理由：")
        lines.extend(f"  · {reason}" for reason in reasons)
    evidence = tuple(getattr(decision, "evidence", ()) or ())
    if evidence:
        lines.append("证据：")
        lines.extend(f"  · {item}" for item in evidence)
    return "\n".join(lines)
