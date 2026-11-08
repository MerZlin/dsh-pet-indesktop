# -*- coding: utf-8 -*-
"""macOS 的系统「正在播放」读取与媒体控制（MediaRemote）。零 Qt。

Windows 那一侧靠 SMTC（``now_playing`` 的 WinRT 路径）；macOS 上对应的系统设施是
**MediaRemote**——控制中心「正在播放」、键盘媒体键背后的那一层。网易云音乐 /
QQ音乐 / 酷狗 / 汽水音乐 / Apple Music / Spotify / 浏览器等只要接了系统的
``MPNowPlayingInfoCenter``（控制中心里能看到它在播），这里都能读到，不需要逐个播放器
适配——与 SMTC 的"一份会话列表覆盖所有播放器"同一个思路。

## 为什么读要经过 ``/usr/bin/osascript``

MediaRemote 是私有框架。macOS 15.4 起，``mediaremoted`` 只把正在播放信息回给
**bundle id 以 ``com.apple.`` 开头**的进程：普通应用（含本桌宠的 Python 进程、
PyInstaller 打出来的 .app）调 ``MRMediaRemoteGetNowPlayingInfo`` 只会拿到空结果，
不报错。系统自带、Apple 签名的 ``/usr/bin/osascript`` 仍在放行之列，所以读这一侧
是在 osascript 里跑一段 JXA（JavaScript for Automation），经 ObjC 桥读
``MRNowPlayingRequest``——开源社区（Pock 的 now-playing widget、SKaplan 的 gist、
mediaremote-adapter 走的 perl 变体）在 15.4 之后普遍改用的就是这一条路。

**不发 Apple Events**：脚本只经 ObjC 桥读本机框架，不 ``tell application`` 任何
播放器，因此不会触发「自动化」授权弹窗，也不需要 Info.plist 里的
``NSAppleEventsUsageDescription``。

## 为什么助手进程常驻

每拍新起一个 osascript 要付进程启动 + 框架加载的代价（几十到上百毫秒），而歌词采样
是 1 秒一拍。所以起**一条常驻助手**，按行收请求、按行回 JSON（Pock 同款做法）：

- 请求是一行请求 id，应答带回同一个 id——超时后才迟到的旧应答按 id 丢弃，不错配；
- 单次请求有超时（:data:`REQUEST_TIMEOUT_SECONDS`），超时就杀掉助手、下次重起；
- 连续失败 :data:`HELPER_MAX_FAILURES` 次进入退避（不每拍重起一个注定失败的进程）；
  助手明确报告"MediaRemote 不可用"时直接长退避——那是系统能力问题，重试无用；
- 没人再问超过 :data:`HELPER_IDLE_EXIT_SECONDS` 就自行收掉（关掉歌词/唱歌后不留
  常驻子进程）；本进程退出时管道关闭，助手读到 EOF 也会自己退出，不会成为孤儿。

## 控制（播放 / 暂停 / 切歌）

``MRMediaRemoteSendCommand`` 在 15.4 之后**仍对普通进程有效**（被收紧的只有读），
所以命令走 ctypes 直接在本进程调用（几微秒，不经助手）。命令发给系统当前的
「正在播放」应用——与媒体键同一个目标，也与 Windows 端"操作当前会话"同口径。

本模块只读系统信息、发媒体命令，不发网络请求，不碰音频流。所有入口都不抛异常。
"""

from __future__ import annotations

import json
import logging
import math
import os
import select
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

OSASCRIPT = "/usr/bin/osascript"
MEDIAREMOTE_LIBRARY = "/System/Library/PrivateFrameworks/MediaRemote.framework/MediaRemote"

# MRMediaRemoteCommand（与 mediaremote-adapter / nowplaying-cli 用的同一张表）。
COMMAND_PLAY = 0
COMMAND_PAUSE = 1
COMMAND_TOGGLE_PLAY_PAUSE = 2
COMMAND_NEXT_TRACK = 4
COMMAND_PREVIOUS_TRACK = 5

# 单次请求的超时。正常一次读取是毫秒级（一次本机 XPC），首个请求还要付助手启动 +
# 框架加载；3 秒给足余量，又不至于让采样线程长时间钉在一次挂住的调用上。
REQUEST_TIMEOUT_SECONDS = 3.0
# 复用窗口：歌词采样（1s 一拍）与唱歌探针（2s 一拍）同时在问时，窗口内的第二个
# 调用方直接拿上一份结果，不再发请求。
STATE_REUSE_SECONDS = 0.5
# 没人再问多久后收掉助手。
HELPER_IDLE_EXIT_SECONDS = 30.0
# 连续失败多少次后进入退避、退避多久。
HELPER_MAX_FAILURES = 3
HELPER_RESTART_BACKOFF_SECONDS = 30.0
# 助手明确报告 MediaRemote 不可用时的退避（系统能力问题，短重试没有意义）。
HELPER_UNSUPPORTED_BACKOFF_SECONDS = 600.0
# 单行应答的长度上界（正常一行几百字节；超长按坏数据处理，不无限缓冲）。
MAX_REPLY_BYTES = 64 * 1024


# 助手脚本：读一行请求 id → 读一次 MRNowPlayingRequest → 写一行 JSON（带回 id）。
# stdin 关闭（本进程退出 / 主动收掉）时 availableData 为空，脚本随之退出。
#
# 读到的键（kMRMediaRemoteNowPlayingInfo*）：elapsed 是 timestamp 那一刻的位置，
# 在播时由调用方按 rate 外推（见 position_at）；mediaType 原样带回（字符串），由
# now_playing 归一成 MUSIC / VIDEO / UNKNOWN。
JXA_HELPER_SCRIPT = r"""
ObjC.import("Foundation");
function run(argv) {
  const input = $.NSFileHandle.fileHandleWithStandardInput;
  const output = $.NSFileHandle.fileHandleWithStandardOutput;
  const exists = function (v) {
    return v !== undefined && v !== null && !(typeof v.isNil === "function" && v.isNil());
  };
  const str = function (v) {
    if (!exists(v)) { return null; }
    const u = ObjC.unwrap(v);
    return (typeof u === "string" && u.length > 0) ? u : null;
  };
  const num = function (v) {
    if (!exists(v)) { return null; }
    let u = ObjC.unwrap(v);
    if (typeof u !== "number") {
      try { u = Number(v.doubleValue); } catch (e) { return null; }
    }
    return isFinite(u) ? u : null;
  };
  const epoch = function (v) {
    if (!exists(v)) { return null; }
    try {
      const t = v.timeIntervalSince1970;
      if (typeof t === "number" && isFinite(t)) { return t; }
    } catch (e) {}
    try {
      const u = ObjC.unwrap(v);
      if (u instanceof Date) { return u.getTime() / 1000; }
    } catch (e) {}
    return null;
  };
  const bundle = $.NSBundle.bundleWithPath("/System/Library/PrivateFrameworks/MediaRemote.framework/");
  const request = (exists(bundle) && bundle.load) ? $.NSClassFromString("MRNowPlayingRequest") : undefined;
  const read = function () {
    if (!exists(request)) { return { ok: false, error: "MRNowPlayingRequest unavailable" }; }
    const item = request.localNowPlayingItem;
    if (!exists(item)) { return { ok: true, item: null }; }
    const info = item.nowPlayingInfo;
    if (!exists(info)) { return { ok: true, item: null }; }
    const path = request.localNowPlayingPlayerPath;
    const client = exists(path) ? path.client : undefined;
    const has = exists(client);
    const key = function (name) { return info.objectForKey(name); };
    return { ok: true, item: {
      bundleIdentifier: has ? str(client.bundleIdentifier) : null,
      parentApplicationBundleIdentifier: has ? str(client.parentApplicationBundleIdentifier) : null,
      displayName: has ? str(client.displayName) : null,
      title: str(key("kMRMediaRemoteNowPlayingInfoTitle")),
      artist: str(key("kMRMediaRemoteNowPlayingInfoArtist")),
      album: str(key("kMRMediaRemoteNowPlayingInfoAlbum")),
      duration: num(key("kMRMediaRemoteNowPlayingInfoDuration")),
      elapsed: num(key("kMRMediaRemoteNowPlayingInfoElapsedTime")),
      timestamp: epoch(key("kMRMediaRemoteNowPlayingInfoTimestamp")),
      rate: num(key("kMRMediaRemoteNowPlayingInfoPlaybackRate")),
      mediaType: str(key("kMRMediaRemoteNowPlayingInfoMediaType")),
      playing: request.localIsPlaying ? true : false
    } };
  };
  let pending = "";
  while (true) {
    const data = input.availableData;
    if (Number(data.length) === 0) { return; }
    pending += ObjC.unwrap($.NSString.alloc.initWithDataEncoding(data, $.NSUTF8StringEncoding)) || "";
    const lines = pending.split("\n");
    pending = lines.pop();
    for (let i = 0; i < lines.length; i++) {
      const id = lines[i].trim();
      if (!id) { continue; }
      let reply;
      try { reply = read(); } catch (e) { reply = { ok: false, error: String(e) }; }
      reply.id = id;
      output.writeData($(JSON.stringify(reply) + "\n").dataUsingEncoding($.NSUTF8StringEncoding));
    }
  }
}
"""


@dataclass(frozen=True)
class MacNowPlaying:
    """一次读取结果：系统当前的「正在播放」应用与曲目。

    数值字段读不到时为 ``None``（不是 0）：网易云这类不上报时间轴的播放器，
    调用方要能分清"没有进度"与"进度是 0"。
    """

    bundle_id: str
    parent_bundle_id: str
    display_name: str
    title: str
    artist: str
    album: str
    duration: float | None
    elapsed: float | None
    timestamp: float | None  # elapsed 被采样的时刻（Unix 秒）
    rate: float | None
    playing: bool
    media_type: str

    @property
    def app_id(self) -> str:
        """会话归属的应用。Safari 的网页媒体由 WebKit 子进程上报，归属看父应用。"""
        return self.parent_bundle_id or self.bundle_id


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _number(value) -> float | None:
    # bool 是 int 的子类：True 不是一个时长。
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def parse_reply(reply) -> MacNowPlaying | None:
    """助手的一条应答 → :class:`MacNowPlaying`；没有在播条目 / 能力不可用 / 形状不对 → None。

    助手是外部进程：每个字段都按不可信输入收（字符串只认 str、数值只认有限实数）。
    """
    if not isinstance(reply, dict) or reply.get("ok") is not True:
        return None
    item = reply.get("item")
    if not isinstance(item, dict):
        return None
    return MacNowPlaying(
        bundle_id=_text(item.get("bundleIdentifier")),
        parent_bundle_id=_text(item.get("parentApplicationBundleIdentifier")),
        display_name=_text(item.get("displayName")),
        title=_text(item.get("title")),
        artist=_text(item.get("artist")),
        album=_text(item.get("album")),
        duration=_number(item.get("duration")),
        elapsed=_number(item.get("elapsed")),
        timestamp=_number(item.get("timestamp")),
        rate=_number(item.get("rate")),
        playing=item.get("playing") is True,
        media_type=_text(item.get("mediaType")),
    )


# 在播时间轴的有效性门槛（与 now_playing._MIN_VALID_DURATION 同值）。
_MIN_VALID_DURATION = 0.01
# timestamp 比"现在"超前多少秒以内算时钟抖动（再多就是字段异常）。
_FUTURE_TOLERANCE_SECONDS = 2.0
# timestamp 比"整首歌的时长"还旧多少秒算陈旧（上一首残留的时间戳）。
_STALE_TOLERANCE_SECONDS = 60.0


def position_at(state: MacNowPlaying, now_epoch: float) -> float | None:
    """把采样时刻的 elapsed 外推到 ``now_epoch``；时间轴不可用时返回 ``None``。

    与 Windows 的 SMTC 外推（``now_playing._extrapolate``）区别在于基准：
    MPNowPlayingInfoCenter 的约定是播放器只在**播放状态变化/跳转**时更新 elapsed，
    系统按 rate 自行推算，所以 delta 可能是几分钟，不能套 SMTC 那个"< 5 秒"的门。

    ``None`` 的几种情况（调用方回退本地计时，与网易云在 Windows 上同一口径）：
    没有时长 / 没有 elapsed / 在播却没有 timestamp / timestamp 在未来或比整首歌还旧。
    """
    duration = state.duration
    if duration is None or duration <= _MIN_VALID_DURATION or state.elapsed is None:
        return None
    position = state.elapsed
    if state.playing:
        if state.timestamp is None:
            return None
        delta = now_epoch - state.timestamp
        if delta < -_FUTURE_TOLERANCE_SECONDS or delta > duration + _STALE_TOLERANCE_SECONDS:
            return None
        rate = state.rate if state.rate is not None and state.rate > 0 else 1.0
        position += max(0.0, delta) * rate
    return min(max(0.0, position), duration)


# ------------------------------------------------------------------ 常驻助手进程


def _helper_argv() -> list[str]:
    """助手的启动命令（测试换成跑同一协议的替身子进程）。"""
    return [OSASCRIPT, "-l", "JavaScript", "-e", JXA_HELPER_SCRIPT]


def _spawn_helper():
    """起一条助手进程；起不来返回 None（osascript 缺失等）。"""
    try:
        return subprocess.Popen(
            _helper_argv(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            close_fds=True,
        )
    except (OSError, ValueError):
        log.debug("起不了 MediaRemote 助手进程", exc_info=True)
        return None


class _Helper:
    """一条常驻助手 + 它的应答缓冲。只由持 :data:`_lock` 的调用方使用。"""

    def __init__(self, process) -> None:
        self.process = process
        self.buffer = b""
        self.sequence = 0

    @property
    def pid(self) -> int | None:
        return getattr(self.process, "pid", None)

    def alive(self) -> bool:
        try:
            return self.process.poll() is None
        except Exception:
            return False

    def close(self) -> None:
        process = self.process
        try:
            if process.stdin is not None:
                process.stdin.close()
        except Exception:
            pass
        try:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=1.0)
        except Exception:
            pass
        try:
            if process.stdout is not None:
                process.stdout.close()
        except Exception:
            pass

    def _pop_line(self) -> bytes | None:
        index = self.buffer.find(b"\n")
        if index < 0:
            return None
        line, self.buffer = self.buffer[:index], self.buffer[index + 1:]
        return line

    def request(self, timeout: float):
        """发一次请求，返回对应 id 的应答 dict；超时 / 进程死亡 / 坏数据返回 None。

        返回 ``None`` 时调用方应关掉这条助手（它要么挂住了，要么已经没了）。
        """
        self.sequence += 1
        request_id = str(self.sequence)
        try:
            os.write(self.process.stdin.fileno(), (request_id + "\n").encode("ascii"))
        except (OSError, ValueError):
            return None
        deadline = time.monotonic() + timeout
        fd = self.process.stdout.fileno()
        while True:
            line = self._pop_line()
            while line is not None:
                try:
                    reply = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    reply = None
                if isinstance(reply, dict) and reply.get("id") == request_id:
                    return reply
                line = self._pop_line()  # 旧 id 的迟到应答：丢弃，继续等本次的
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                ready, _, _ = select.select([fd], [], [], remaining)
            except (OSError, ValueError):
                return None
            if not ready:
                continue
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                return None
            if not chunk:
                return None  # EOF：助手退出了
            self.buffer += chunk
            if len(self.buffer) > MAX_REPLY_BYTES:
                return None


_lock = threading.Lock()
_helper: _Helper | None = None
_failures = 0
_retry_after = 0.0
_last_used = 0.0
_last_state: MacNowPlaying | None = None
_last_state_at = 0.0
_reaper: threading.Thread | None = None
_reaper_stop: threading.Event | None = None


def _close_helper_locked() -> None:
    global _helper
    helper, _helper = _helper, None
    if helper is not None:
        helper.close()


def _note_failure_locked(backoff: float | None = None) -> None:
    global _failures, _retry_after
    _close_helper_locked()
    _failures += 1
    if backoff is not None:
        _retry_after = time.monotonic() + backoff
    elif _failures >= HELPER_MAX_FAILURES:
        _retry_after = time.monotonic() + HELPER_RESTART_BACKOFF_SECONDS


def _reaper_loop(stop: threading.Event) -> None:
    """空闲收尾：没人再问超过期限就关掉助手，然后自己退出。

    "决定退出"与"摘掉自己的登记"必须在**同一段锁**里完成：分成两段时，中间那一刻
    若有请求起了新助手，``_ensure_reaper_locked`` 会看到本线程还活着而不另起，
    随后本线程摘掉登记退出——新助手就再也没有人收（直到进程退出）。
    """
    global _reaper, _reaper_stop
    while True:
        stopped = stop.wait(max(0.05, min(HELPER_IDLE_EXIT_SECONDS / 4.0, 5.0)))
        with _lock:
            if _reaper is not threading.current_thread():
                return  # 已被 shutdown 摘掉登记
            if _helper is not None and time.monotonic() - _last_used > HELPER_IDLE_EXIT_SECONDS:
                _close_helper_locked()
            if stopped or _helper is None:
                _reaper = None
                _reaper_stop = None
                return


def _ensure_reaper_locked() -> None:
    global _reaper, _reaper_stop
    if _reaper is not None and _reaper.is_alive():
        return
    stop = threading.Event()
    thread = threading.Thread(target=_reaper_loop, args=(stop,),
                              name="mac-media-helper-reaper", daemon=True)
    _reaper, _reaper_stop = thread, stop
    thread.start()


def _request_locked():
    """在锁内完成一次请求（必要时起助手）。返回应答 dict 或 None。"""
    global _helper, _failures, _last_used
    _last_used = time.monotonic()
    if _helper is not None and not _helper.alive():
        _note_failure_locked()
    if _helper is None:
        if time.monotonic() < _retry_after:
            return None
        process = _spawn_helper()
        if process is None:
            _note_failure_locked()
            return None
        _helper = _Helper(process)
        _ensure_reaper_locked()
    reply = _helper.request(REQUEST_TIMEOUT_SECONDS)
    _last_used = time.monotonic()
    if reply is None:
        _note_failure_locked()
        return None
    if reply.get("ok") is not True:
        # 助手活着、但明确说读不了（老系统没有 MRNowPlayingRequest，或被系统封掉）：
        # 能力问题，不是偶发故障——收掉助手并长退避，不每拍重起一个注定失败的进程。
        log.info("macOS MediaRemote 不可用，正在播放读取暂停 %.0f 秒",
                 HELPER_UNSUPPORTED_BACKOFF_SECONDS)
        _note_failure_locked(HELPER_UNSUPPORTED_BACKOFF_SECONDS)
        return None
    _failures = 0
    return reply


def read_now_playing(max_age: float | None = None) -> MacNowPlaying | None:
    """读系统当前的「正在播放」；没有在播条目、非 macOS、任何故障都返回 ``None``。

    ``max_age``：可接受的已有结果的最大年龄（秒），默认 :data:`STATE_REUSE_SECONDS`。
    需要"此刻"的读数（发命令前确认目标）时传 ``0``。

    **会阻塞调用线程**（首个请求要起助手，最坏到超时）：只许在后台线程调用——与
    Windows 端"主线程不许查 SMTC"同一条纪律。
    """
    global _last_state, _last_state_at
    if sys.platform != "darwin":
        return None
    reuse = STATE_REUSE_SECONDS if max_age is None else max(0.0, float(max_age))
    try:
        with _lock:
            now = time.monotonic()
            if reuse > 0 and _last_state_at and now - _last_state_at <= reuse:
                return _last_state
            reply = _request_locked()
            state = parse_reply(reply) if reply is not None else None
            if reply is not None:
                _last_state, _last_state_at = state, time.monotonic()
            return state
    except Exception:
        log.debug("读取 macOS 正在播放失败", exc_info=True)
        return None


def helper_pid() -> int | None:
    """当前助手进程的 pid（没有在跑的助手时为 None）。诊断与测试用。"""
    with _lock:
        if _helper is None or not _helper.alive():
            return None
        return _helper.pid


def shutdown() -> None:
    """收掉助手与收尾线程，清掉缓存与退避状态（测试与诊断用；产品路径靠空闲退出）。"""
    global _failures, _retry_after, _last_state, _last_state_at, _last_used
    with _lock:
        _close_helper_locked()
        stop, reaper = _reaper_stop, _reaper
        _failures = 0
        _retry_after = 0.0
        _last_state = None
        _last_state_at = 0.0
        _last_used = 0.0
    if stop is not None:
        stop.set()
    if reaper is not None and reaper is not threading.current_thread():
        reaper.join(timeout=2.0)


# ------------------------------------------------------------------ 媒体命令


_command_lock = threading.Lock()
_command_fn = None
_command_unavailable = False


def _command_function():
    """惰性加载 ``MRMediaRemoteSendCommand``（只加载一次；加载失败也只试一次）。"""
    global _command_fn, _command_unavailable
    with _command_lock:
        if _command_fn is not None or _command_unavailable:
            return _command_fn
        try:
            import ctypes

            library = ctypes.CDLL(MEDIAREMOTE_LIBRARY)
            function = library.MRMediaRemoteSendCommand
            function.argtypes = [ctypes.c_int, ctypes.c_void_p]
            function.restype = ctypes.c_bool
            _command_fn = function
        except Exception:
            log.debug("加载 MRMediaRemoteSendCommand 失败", exc_info=True)
            _command_unavailable = True
        return _command_fn


def send_command(command: int) -> bool:
    """给系统当前的「正在播放」应用发一条媒体命令；返回系统是否受理。

    非 macOS、框架加载失败、调用异常一律返回 False（不抛）。调用本身是一次本机
    XPC 投递，不阻塞；但与其它音乐控制入口一样只在后台线程调用。
    """
    if sys.platform != "darwin":
        return False
    function = _command_function()
    if function is None:
        return False
    try:
        return bool(function(int(command), None))
    except Exception:
        log.debug("发送 MediaRemote 命令失败：%s", command, exc_info=True)
        return False
