# -*- coding: utf-8 -*-
"""洛雪音乐（LX Music）曲目来源：读它的本地 OpenAPI。

**为什么需要它**：LX Music 在 Windows 系统媒体控制（SMTC）里能看见歌名，但
媒体类型报 Unknown、且经常不报时间轴（``end_time`` / ``position`` 恒为 0）。
时间轴一缺，``now_playing`` 只能把 ``position`` 记成 ``None``，歌词于是走
"按检测到切歌的时刻估算"那条路——快进、拖进度、中途开始播放都会错位，暂停
久一点还会越漂越远。

**LX 自己给得更准**：v2.7.0 起桌面版带开放 API（设置 → 其他 → 开放 API 服务），
默认监听 ``127.0.0.1:23330``，``GET /status`` 直接返回 ``progress``（秒）和
``duration``，与播放器内部状态同源。所以本模块只做一件事：把它的
``/status`` 翻译成 ``Playback``，让歌词链路拿到真实进度。

**它在链路里的地位是优先源**（见 ``now_playing._read_async``）：只要这个接口
答了且正在播放，就不必再问 SMTC——同一首歌，LX 的进度比 SMTC 的更细更准。

**失败必须无声**：端口没开（用户没启用开放 API / 洛雪没启动）是常态，不是
错误。连接被拒后缓存几秒不再尝试，避免每秒一拍都去撞一次超时。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

#: 作为 ``Playback.app_id`` 的取值。刻意与 SMTC 的会话 id 区分开：调用方
#: 据此知道"这条来自本机 HTTP 接口"，也能和 foobar 的窗口标题源一样被识别。
LX_APP_ID = "lx-music-desktop.exe"

#: 开放 API 默认端口（LX 文档：/desktop/open-api）。
DEFAULT_PORT = 23330

#: 本地回环请求超时。洛雪就在本机，正常响应 <10ms；超过这个值说明它没在
#: 监听或卡住了，等下去只会拖慢采样线程。
REQUEST_TIMEOUT = 0.6

#: 连不上之后多久再试一次（秒）。用户可能随时在洛雪里打开开放 API 开关，
#: 所以不永久放弃；但也不必每秒重连——5 秒的滞后换来采样线程稳定。
RETRY_AFTER = 5.0

#: 读洛雪配置（拿真实端口 / 开关）的缓存时长。它是个几 KB 的 JSON，但没
#: 必要每秒读一次。
CONFIG_TTL = 30.0

#: 只取需要的字段，响应因此只有一两百字节。
_FILTER = "status,name,singer,albumName,progress,duration"

_config_cache: tuple[float, bool, int] | None = None
_blocked_until = 0.0


def is_lx_source(app_id: str | None) -> bool:
    """这条采样是否来自洛雪的开放 API（调用方可用它区分来源、决定是否覆盖）。"""
    return str(app_id or "").strip().lower() == LX_APP_ID


def _config_path() -> str:
    """洛雪桌面版的设置文件路径（Electron userData 下的 LxDatas/config_v2.json）。"""
    appdata = os.environ.get("APPDATA") or ""
    if not appdata:
        return ""
    return os.path.join(appdata, "lx-music-desktop", "LxDatas", "config_v2.json")


def _read_lx_config() -> tuple[bool, int]:
    """读洛雪的「开放 API」开关与端口；读不到时按"开着 + 默认端口"猜。

    读不到不代表没开——用户可能把它装在别的用户下、或版本换了存储位置。
    猜错的代价只是一次失败的 HTTP 请求（有退避），比"因为读不到配置就整个
    功能不工作"要好。
    """
    global _config_cache
    now = time.monotonic()
    cached = _config_cache
    if cached is not None and now - cached[0] < CONFIG_TTL:
        return cached[1], cached[2]

    enabled, port = True, DEFAULT_PORT
    path = _config_path()
    if path:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                setting = json.load(fh).get("setting") or {}
            raw_enabled = setting.get("openAPI.enable")
            if isinstance(raw_enabled, bool):
                enabled = raw_enabled
            raw_port = setting.get("openAPI.port")
            if isinstance(raw_port, (int, str)):
                parsed = int(str(raw_port).strip())
                if 0 < parsed < 65536:
                    port = parsed
        except (OSError, ValueError, TypeError, AttributeError):
            pass  # 配置读不到/格式变了：用默认值猜，错了有退避兜着

    _config_cache = (now, enabled, port)
    return enabled, port


def _fetch_status(port: int) -> dict | None:
    """GET ``/status``；任何异常都返回 ``None``（歌词是锦上添花，不能抛）。"""
    url = "http://127.0.0.1:%d/status?%s" % (
        port,
        urllib.parse.urlencode({"filter": _FILTER}),
    )
    try:
        with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT) as resp:
            if getattr(resp, "status", 200) != 200:
                return None
            payload = resp.read(64 * 1024)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    try:
        data = json.loads(payload.decode("utf-8", "replace"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def read_playback() -> "Playback | None":  # noqa: F821 - 类型名延迟到函数内导入
    """采样洛雪当前播放；没在播 / 接口不可用 / 非 Windows 时返回 ``None``。

    返回对象的 ``position`` 一定是真实进度（LX 的 ``progress``），因此调用方
    不需要再走"按检测时刻估算"那条分支。
    """
    global _blocked_until
    if sys.platform != "win32":
        return None
    now = time.monotonic()
    if now < _blocked_until:
        return None

    enabled, port = _read_lx_config()
    if not enabled:
        # 用户在洛雪里关掉了开放 API：尊重它，不试探端口。
        return None

    data = _fetch_status(port)
    if data is None:
        _blocked_until = now + RETRY_AFTER
        return None

    status = str(data.get("status", "") or "").strip().lower()
    if status != "playing":
        # 暂停 / 停止 / 出错：交回 SMTC（那里能正确表达"暂停但还在"）。
        return None

    title = str(data.get("name", "") or "").strip()
    artist = str(data.get("singer", "") or "").strip()
    if not title and not artist:
        return None

    # 延迟导入：本模块被 now_playing 反向依赖，模块级互相 import 会成环。
    from .now_playing import Playback, Track

    try:
        position = float(data.get("progress"))
    except (TypeError, ValueError):
        position = None
    try:
        duration = float(data.get("duration"))
    except (TypeError, ValueError):
        duration = 0.0
    if position is not None and position < 0:
        position = 0.0

    return Playback(
        track=Track(
            title=title,
            artist=artist,
            album=str(data.get("albumName", "") or "").strip(),
            duration=duration if duration > 0 else 0.0,
            playing=True,
        ),
        position=position,
        updated_at=time.monotonic(),
        app_id=LX_APP_ID,
    )
