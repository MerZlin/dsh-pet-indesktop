# -*- coding: utf-8 -*-
"""macOS「正在播放」后端（``pet/music/mac_media.py``）的契约测试。

本机（CI / 开发容器）不是 macOS，``/usr/bin/osascript`` 与 MediaRemote 都不存在，
所以只在**操作系统边界**换替身（与 AGENTS.md「mock only OS boundaries」同口径）：

- 常驻 JXA 助手进程换成一个**真实的 Python 子进程**（下面的 ``_FAKE_HELPER``），
  跑的是同一套行协议（一行请求 id → 一行 JSON 应答）。进程边界、管道、超时、
  进程死亡与重启、空闲退出都是真的，只有"应答内容从哪来"是假的；
- ``MRMediaRemoteSendCommand`` 走 ctypes，换成一个可记录调用的替身函数。

纯函数（应答解析、播放位置外推）直接测真值表。
"""
from __future__ import annotations

import json
import sys
import threading
import time

import pytest

from pet.music import mac_media


# ------------------------------------------------------------------ 替身助手进程
#
# 行为由第一个命令行参数决定（tests 用 monkeypatch 改 mac_media._helper_argv）：
#   reply <json>     每收到一行 id 就回 {"id": id, **json}
#   silent           读请求但永不应答（挂住的 MediaRemote 调用）
#   exit             一启动就退出（osascript 起不来 / 脚本语法错）
#   stale <json>     先回一条错误 id 的旧应答，再回正确的
#   count <path>     每启动一次往 path 追加一行（统计重启次数），然后按 reply {} 应答
_FAKE_HELPER = r'''
import json, os, sys
mode = sys.argv[1]
payload = json.loads(sys.argv[2]) if len(sys.argv) > 2 and mode in ("reply", "stale") else {}
if mode == "exit":
    sys.exit(3)
if mode == "count":
    with open(sys.argv[2], "a", encoding="utf-8") as handle:
        handle.write("start\n")
    payload = {"ok": True, "item": None}
out = sys.stdout.buffer
for raw in sys.stdin.buffer:
    rid = raw.decode("utf-8").strip()
    if not rid:
        continue
    if mode == "silent":
        continue
    if mode == "stale":
        out.write((json.dumps({"id": "stale-" + rid, "ok": True, "item": None}) + "\n").encode("utf-8"))
    reply = dict(payload)
    reply["id"] = rid
    out.write((json.dumps(reply, ensure_ascii=False) + "\n").encode("utf-8"))
    out.flush()
'''


@pytest.fixture
def fake_helper(tmp_path, monkeypatch):
    """返回 ``use(mode, *args)``：把助手进程换成替身脚本；用例结束时关掉助手。"""
    if sys.platform == "win32":
        # 助手读取用 ``select`` 等管道可读（``mac_media`` 的读循环），Windows 的 select 只认
        # 套接字——替身助手在 Windows 主机上永远读不到应答。这条读路径只在 macOS 上运行，
        # 进程边界的契约由 Linux / macOS 两端 CI 覆盖。
        pytest.skip("mac 助手的管道读取依赖 POSIX select，Windows 主机上跑不了")
    script = tmp_path / "fake_helper.py"
    script.write_text(_FAKE_HELPER, encoding="utf-8")

    def use(mode: str, *args: str) -> None:
        argv = [sys.executable, "-I", str(script), mode, *args]
        monkeypatch.setattr(mac_media, "_helper_argv", lambda: list(argv))

    # 测试节奏：超时与退避调到毫秒级（行为不变，只是不拖慢套件）。
    monkeypatch.setattr(mac_media, "REQUEST_TIMEOUT_SECONDS", 2.0)
    monkeypatch.setattr(mac_media, "STATE_REUSE_SECONDS", 0.0)
    monkeypatch.setattr(mac_media, "HELPER_RESTART_BACKOFF_SECONDS", 60.0)
    mac_media.shutdown()
    yield use
    mac_media.shutdown()


ITEM = {
    "bundleIdentifier": "com.netease.163music",
    "parentApplicationBundleIdentifier": None,
    "displayName": "网易云音乐",
    "title": "晴天",
    "artist": "周杰伦",
    "album": "叶惠美",
    "duration": 269.0,
    "elapsed": 30.0,
    "timestamp": 1_700_000_000.0,
    "rate": 1.0,
    "mediaType": "MRMediaRemoteMediaTypeMusic",
    "playing": True,
}


# ------------------------------------------------------------------ 纯函数：解析


def test_parse_reply_maps_every_field():
    state = mac_media.parse_reply({"ok": True, "item": ITEM})
    assert state == mac_media.MacNowPlaying(
        bundle_id="com.netease.163music",
        parent_bundle_id="",
        display_name="网易云音乐",
        title="晴天",
        artist="周杰伦",
        album="叶惠美",
        duration=269.0,
        elapsed=30.0,
        timestamp=1_700_000_000.0,
        rate=1.0,
        playing=True,
        media_type="MRMediaRemoteMediaTypeMusic",
    )
    assert state.app_id == "com.netease.163music"


def test_parse_reply_prefers_parent_bundle_as_app_id():
    """Safari 的网页媒体由 WebKit 子进程上报：会话归属看父应用，不看子进程。"""
    item = dict(ITEM, bundleIdentifier="com.apple.WebKit.GPU",
                parentApplicationBundleIdentifier="com.apple.Safari")
    state = mac_media.parse_reply({"ok": True, "item": item})
    assert state.app_id == "com.apple.Safari"


@pytest.mark.parametrize("reply", [
    {"ok": True, "item": None},          # 没有任何应用在「正在播放」
    {"ok": False, "error": "x"},         # 助手报告能力不可用
    {"ok": True, "item": "not-a-dict"},  # 形状不对
    None,
    [],
])
def test_parse_reply_returns_none_without_an_item(reply):
    assert mac_media.parse_reply(reply) is None


def test_parse_reply_drops_malformed_numbers_and_strings():
    """助手是外部进程：数值字段只认有限实数，字符串字段只认 str，其余按缺失。"""
    item = dict(ITEM, duration="269", elapsed=float("nan"), timestamp=True,
                rate=None, title=123, playing="yes")
    state = mac_media.parse_reply({"ok": True, "item": item})
    assert state.duration is None and state.elapsed is None
    assert state.timestamp is None and state.rate is None
    assert state.title == "" and state.playing is False


# ------------------------------------------------------------------ 纯函数：位置


def _state(**overrides):
    return mac_media.parse_reply({"ok": True, "item": dict(ITEM, **overrides)})


def test_position_extrapolates_from_timestamp_while_playing():
    """MediaRemote 的 elapsed 是 timestamp 那一刻的位置：在播时按速率外推到现在。"""
    state = _state(elapsed=30.0, timestamp=1000.0, rate=1.0)
    assert mac_media.position_at(state, now_epoch=1012.5) == pytest.approx(42.5)


def test_position_is_frozen_while_paused():
    state = _state(playing=False, elapsed=30.0, timestamp=1000.0)
    assert mac_media.position_at(state, now_epoch=1500.0) == pytest.approx(30.0)


def test_position_clamps_to_duration():
    state = _state(elapsed=260.0, timestamp=1000.0, duration=269.0)
    assert mac_media.position_at(state, now_epoch=1020.0) == pytest.approx(269.0)


@pytest.mark.parametrize("overrides", [
    {"duration": None},                    # 不上报时间轴（网易云在 Windows 上就是这样）
    {"duration": 0.0},
    {"elapsed": None},
    {"timestamp": None},                   # 在播却不知道 elapsed 是哪一刻的：不猜
])
def test_position_is_unknown_without_a_usable_timeline(overrides):
    state = _state(**overrides)
    assert mac_media.position_at(state, now_epoch=1_700_000_010.0) is None


def test_position_rejects_clock_anomalies():
    """timestamp 在未来、或比整首歌还旧很多：时钟/字段异常，交给本地计时。"""
    future = _state(elapsed=30.0, timestamp=2000.0)
    assert mac_media.position_at(future, now_epoch=1000.0) is None
    stale = _state(elapsed=30.0, timestamp=1000.0, duration=200.0)
    assert mac_media.position_at(stale, now_epoch=1000.0 + 200.0 + 3600.0) is None


def test_position_uses_reported_rate_and_defaults_to_one():
    double = _state(elapsed=10.0, timestamp=1000.0, rate=2.0)
    assert mac_media.position_at(double, now_epoch=1005.0) == pytest.approx(20.0)
    zero = _state(elapsed=10.0, timestamp=1000.0, rate=0.0)  # 在播但没报速率
    assert mac_media.position_at(zero, now_epoch=1005.0) == pytest.approx(15.0)


# ------------------------------------------------------------------ 助手进程协议


def test_non_darwin_never_spawns_the_helper(monkeypatch, fake_helper):
    fake_helper("reply", json.dumps({"ok": True, "item": ITEM}))
    monkeypatch.setattr(mac_media.sys, "platform", "linux")
    assert mac_media.read_now_playing() is None
    assert mac_media.helper_pid() is None


def test_reads_now_playing_through_a_persistent_helper(monkeypatch, fake_helper):
    """一次启动、多次请求：助手常驻（每拍新起 osascript 的代价是每次几十毫秒）。"""
    monkeypatch.setattr(mac_media.sys, "platform", "darwin")
    fake_helper("reply", json.dumps({"ok": True, "item": ITEM}, ensure_ascii=False))

    first = mac_media.read_now_playing()
    pid = mac_media.helper_pid()
    second = mac_media.read_now_playing()

    assert first is not None and first.title == "晴天" and first.artist == "周杰伦"
    assert second == first
    assert pid is not None and mac_media.helper_pid() == pid, "第二次请求不该重启助手"


def test_no_now_playing_item_is_none(monkeypatch, fake_helper):
    monkeypatch.setattr(mac_media.sys, "platform", "darwin")
    fake_helper("reply", json.dumps({"ok": True, "item": None}))
    assert mac_media.read_now_playing() is None
    assert mac_media.helper_pid() is not None, "「没有在播」是正常应答，助手应保持常驻"


def test_stale_replies_with_other_ids_are_skipped(monkeypatch, fake_helper):
    """应答按请求 id 配对：超时后才迟到的旧应答不能被当成本次结果。"""
    monkeypatch.setattr(mac_media.sys, "platform", "darwin")
    fake_helper("stale", json.dumps({"ok": True, "item": ITEM}, ensure_ascii=False))
    state = mac_media.read_now_playing()
    assert state is not None and state.title == "晴天"


def test_hung_helper_times_out_and_is_killed(monkeypatch, fake_helper):
    """助手不应答：请求在超时内返回 None，并杀掉那个挂住的进程（下次重起）。"""
    monkeypatch.setattr(mac_media.sys, "platform", "darwin")
    monkeypatch.setattr(mac_media, "REQUEST_TIMEOUT_SECONDS", 0.3)
    fake_helper("silent")

    started = time.monotonic()
    assert mac_media.read_now_playing() is None
    elapsed = time.monotonic() - started

    assert elapsed < 5.0, f"超时没有生效：{elapsed:.2f}s"
    assert mac_media.helper_pid() is None, "挂住的助手应被杀掉"


def test_dead_helper_backs_off_instead_of_respawning_every_tick(monkeypatch, fake_helper):
    """助手一起来就死（脚本被系统拒绝等）：连续失败后进入退避，不每拍重起进程。"""
    monkeypatch.setattr(mac_media.sys, "platform", "darwin")
    spawned: list[int] = []
    real_spawn = mac_media._spawn_helper

    def counting_spawn():
        spawned.append(1)
        return real_spawn()

    monkeypatch.setattr(mac_media, "_spawn_helper", counting_spawn)
    fake_helper("exit")

    for _ in range(mac_media.HELPER_MAX_FAILURES + 5):
        assert mac_media.read_now_playing() is None

    assert len(spawned) == mac_media.HELPER_MAX_FAILURES, (
        f"退避没生效：起了 {len(spawned)} 次助手"
    )


def test_unsupported_reply_stops_the_helper_and_backs_off(monkeypatch, fake_helper):
    """助手明确报告 MediaRemote 不可用（老系统 / 被系统封掉）：不再常驻、不再重试。"""
    monkeypatch.setattr(mac_media.sys, "platform", "darwin")
    spawned: list[int] = []
    real_spawn = mac_media._spawn_helper
    monkeypatch.setattr(mac_media, "_spawn_helper",
                        lambda: (spawned.append(1), real_spawn())[1])
    fake_helper("reply", json.dumps({"ok": False, "error": "MRNowPlayingRequest unavailable"}))

    assert mac_media.read_now_playing() is None
    assert mac_media.read_now_playing() is None

    assert spawned == [1], "能力不可用后不该再起助手"
    assert mac_media.helper_pid() is None


def test_idle_helper_exits_on_its_own(monkeypatch, fake_helper):
    """没人再问：助手按空闲期限自行退出（关掉歌词/唱歌后不留常驻子进程）。"""
    monkeypatch.setattr(mac_media.sys, "platform", "darwin")
    monkeypatch.setattr(mac_media, "HELPER_IDLE_EXIT_SECONDS", 0.2)
    fake_helper("reply", json.dumps({"ok": True, "item": None}))

    mac_media.read_now_playing()
    assert mac_media.helper_pid() is not None

    deadline = time.monotonic() + 10.0
    while mac_media.helper_pid() is not None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert mac_media.helper_pid() is None, "空闲后助手没有退出"


def test_recent_state_is_reused_within_the_reuse_window(monkeypatch, fake_helper, tmp_path):
    """歌词采样（1s）与唱歌探针（2s）同时在问：复用窗口内只发一次请求。"""
    monkeypatch.setattr(mac_media.sys, "platform", "darwin")
    monkeypatch.setattr(mac_media, "STATE_REUSE_SECONDS", 60.0)
    requests: list[int] = []
    real_request = mac_media._Helper.request

    def counting(self, timeout):
        requests.append(1)
        return real_request(self, timeout)

    monkeypatch.setattr(mac_media._Helper, "request", counting)
    fake_helper("reply", json.dumps({"ok": True, "item": ITEM}, ensure_ascii=False))

    assert mac_media.read_now_playing() is not None
    assert mac_media.read_now_playing() is not None
    assert mac_media.read_now_playing(max_age=0.0) is not None

    assert len(requests) == 2, f"复用窗口没生效：发了 {len(requests)} 次请求"


def test_concurrent_callers_are_serialized(monkeypatch, fake_helper):
    """两个后台线程同时问：请求串行化，各自拿到完整应答（不串行、不错配）。"""
    monkeypatch.setattr(mac_media.sys, "platform", "darwin")
    fake_helper("reply", json.dumps({"ok": True, "item": ITEM}, ensure_ascii=False))
    results: list = []
    barrier = threading.Barrier(4)

    def worker():
        barrier.wait(10.0)
        for _ in range(5):
            results.append(mac_media.read_now_playing(max_age=0.0))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30.0)

    assert len(results) == 20
    assert all(state is not None and state.title == "晴天" for state in results)


# ------------------------------------------------------------------ 媒体命令（ctypes）


class _FakeCommandFn:
    def __init__(self, result=True):
        self.calls: list = []
        self.result = result
        self.argtypes = None
        self.restype = None

    def __call__(self, command, options):
        self.calls.append((command, options))
        return self.result


def test_send_command_calls_mediaremote(monkeypatch):
    monkeypatch.setattr(mac_media.sys, "platform", "darwin")
    fn = _FakeCommandFn()
    monkeypatch.setattr(mac_media, "_command_function", lambda: fn)

    assert mac_media.send_command(mac_media.COMMAND_NEXT_TRACK) is True
    assert fn.calls == [(mac_media.COMMAND_NEXT_TRACK, None)]


def test_send_command_is_false_off_darwin_or_when_unavailable(monkeypatch):
    fn = _FakeCommandFn()
    monkeypatch.setattr(mac_media, "_command_function", lambda: fn)
    monkeypatch.setattr(mac_media.sys, "platform", "linux")
    assert mac_media.send_command(mac_media.COMMAND_PLAY) is False
    assert fn.calls == []

    monkeypatch.setattr(mac_media.sys, "platform", "darwin")
    monkeypatch.setattr(mac_media, "_command_function", lambda: None)
    assert mac_media.send_command(mac_media.COMMAND_PLAY) is False


def test_send_command_swallows_ffi_errors(monkeypatch):
    monkeypatch.setattr(mac_media.sys, "platform", "darwin")

    def boom(command, options):
        raise OSError("dlsym failed")

    monkeypatch.setattr(mac_media, "_command_function", lambda: boom)
    assert mac_media.send_command(mac_media.COMMAND_TOGGLE_PLAY_PAUSE) is False


def test_command_ids_match_mediaremote_enum():
    """MRMediaRemoteCommand 的取值（mediaremote-adapter / nowplaying-cli 同表）。"""
    assert (mac_media.COMMAND_PLAY, mac_media.COMMAND_PAUSE,
            mac_media.COMMAND_TOGGLE_PLAY_PAUSE, mac_media.COMMAND_NEXT_TRACK,
            mac_media.COMMAND_PREVIOUS_TRACK) == (0, 1, 2, 4, 5)


# ------------------------------------------------------------------ 助手脚本本体


def test_helper_argv_runs_jxa_through_apple_signed_osascript():
    """读必须经 /usr/bin/osascript：macOS 15.4 起 MediaRemote 只回应 com.apple.* 进程。"""
    argv = mac_media._helper_argv()
    assert argv[:3] == ["/usr/bin/osascript", "-l", "JavaScript"]
    assert argv[3] == "-e" and argv[4] == mac_media.JXA_HELPER_SCRIPT


def test_helper_script_reads_the_fields_we_parse():
    """脚本与解析器是两端的同一份契约：解析器认的每个键，脚本都得产出。"""
    script = mac_media.JXA_HELPER_SCRIPT
    for key in ("bundleIdentifier", "parentApplicationBundleIdentifier", "displayName",
                "title", "artist", "album", "duration", "elapsed", "timestamp",
                "rate", "mediaType", "playing"):
        assert f"{key}:" in script, f"助手脚本没有产出 {key}"
    for mr_key in ("kMRMediaRemoteNowPlayingInfoTitle", "kMRMediaRemoteNowPlayingInfoArtist",
                   "kMRMediaRemoteNowPlayingInfoAlbum", "kMRMediaRemoteNowPlayingInfoDuration",
                   "kMRMediaRemoteNowPlayingInfoElapsedTime",
                   "kMRMediaRemoteNowPlayingInfoTimestamp",
                   "kMRMediaRemoteNowPlayingInfoPlaybackRate"):
        assert mr_key in script
    # 只走 ObjC 桥读 MediaRemote，不给别的应用发 Apple Events（那要「自动化」授权弹窗）。
    assert "Application(" not in script
