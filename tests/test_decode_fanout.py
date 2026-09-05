# -*- coding: utf-8 -*-
"""批5.3：``pet/decode_fanout.py`` 单元测试（无 Qt 窗口，真线程）。

覆盖设计稿（BATCH53_DESIGN_glm53.md §4）：
- start 分派：publish/feed/local（首窗发布、次窗进食、速度不等 local、
  GifClip 无能力 local、源死后再 start 重建源）；
- 回绕合成 'end'；drop-oldest 保序；跨线程生产/消费冒烟；
- handover：发布者 end → 订阅者被扶正（其 on_frame 供环）→ 其余环续帧；
- 看门狗：无帧超时 → 'abort'（手动拨时钟，零 sleep）；
- 摘环/摘 sink 幂等；hub.stop_all 后 ``_sources`` 空（泄漏断言，对齐 G7 精神）；
- 节流/pace 调和：有效 divisor = min(在挂消费者期望值)。
"""
from __future__ import annotations

import threading
import time

import pytest

from pet.decode_fanout import (
    DecodeFanoutHub,
    FanoutFeed,
    _RingBuffer,
    _SourceSink,
    WATCHDOG_BUDGET_MS,
)

FRAME = bytes(640 * 360 * 4)  # 整帧 RGBA（内容不必唯一：帧身份由 src 标记）
PATH = "assets/characters/shenshen/videos/idle/待机呼吸休闲.webm"


class _FakeMovie:
    """与 WebMClip fan-out 接缝兼容的极简替身。"""

    def __init__(self, path: str = PATH, speed: float = 1.0) -> None:
        self.path = path
        self.playback_speed = speed
        self._publish_sink = None
        self._feed_source = None
        self.decode_throttle_divisor = 1
        self.decode_pace_external = False
        self.throttle_calls: list = []

    def set_decode_throttle(self, divisor: int) -> None:
        self.decode_throttle_divisor = max(1, int(divisor))
        self.throttle_calls.append(self.decode_throttle_divisor)

    def set_decode_pace_external(self, value: bool) -> None:
        self.decode_pace_external = bool(value)


class _NoCapabilityMovie(_FakeMovie):
    """无 ``_publish_sink``/``_feed_source`` 能力（GifClip/测试桩）。"""

    def __init__(self, path: str = PATH) -> None:
        super().__init__(path)
        del self._publish_sink
        del self._feed_source


@pytest.fixture
def hub():
    return DecodeFanoutHub(enabled=True)


@pytest.fixture
def disabled_hub():
    return DecodeFanoutHub(enabled=False)


# ---------------------------------------------------------------------------
# start 分派：publish / feed / local
# ---------------------------------------------------------------------------
class TestStartDispatch:
    def test_first_window_publishes(self, hub):
        pub = _FakeMovie(PATH)
        assert hub.shareable_start("idle", pub) == "publish"
        assert pub._publish_sink is not None
        assert pub._feed_source is None
        assert PATH in hub._sources

    def test_second_window_feeds(self, hub):
        pub = _FakeMovie(PATH)
        assert hub.shareable_start("idle", pub) == "publish"
        sub = _FakeMovie(PATH)
        assert hub.shareable_start("idle", sub) == "feed"
        # feed 句柄：进程内 attach 立即就绪（ready=True，result=session）
        feed = sub._feed_source
        assert isinstance(feed, FanoutFeed)
        assert feed.ready is True
        assert feed.result is not None
        # 发布端 sink 仍在（pub 继续喂源）
        assert pub._publish_sink is not None
        assert sub._publish_sink is None

    def test_speed_mismatch_is_local(self, hub):
        pub = _FakeMovie(PATH, speed=1.0)
        assert hub.shareable_start("idle", pub) == "publish"
        sub = _FakeMovie(PATH, speed=2.0)
        assert hub.shareable_start("idle", sub) == "local"
        assert sub._feed_source is None
        assert sub._publish_sink is None

    def test_gifclip_no_capability_is_local(self, hub):
        movie = _NoCapabilityMovie(PATH)
        assert hub.shareable_start("idle", movie) == "local"

    def test_disabled_hub_always_local(self, disabled_hub):
        pub = _FakeMovie(PATH)
        assert disabled_hub.shareable_start("idle", pub) == "local"
        assert pub._publish_sink is None

    def test_source_rebuilds_after_publisher_dead(self, hub):
        pub = _FakeMovie(PATH)
        assert hub.shareable_start("idle", pub) == "publish"
        assert PATH in hub._sources
        # 发布者离开且无订阅者 → 源释放
        hub.shareable_end("idle", pub, natural=True)
        assert PATH not in hub._sources
        assert pub._publish_sink is None
        # 之后再来一个窗 → 重建源
        pub2 = _FakeMovie(PATH)
        assert hub.shareable_start("idle", pub2) == "publish"
        assert pub2._publish_sink is not None
        assert PATH in hub._sources

    def test_publisher_rearm_stills_publish(self, hub):
        pub = _FakeMovie(PATH)
        assert hub.shareable_start("idle", pub) == "publish"
        # 同窗 re-arm（源未释放）→ 仍 'publish'
        assert hub.shareable_start("idle", pub) == "publish"


# ---------------------------------------------------------------------------
# 回绕合成 'end'；drop-oldest 保序；跨线程生产/消费冒烟
# ---------------------------------------------------------------------------
def _subscribe(hub, pub, path=PATH):
    hub.shareable_start("idle", pub)
    sub = _FakeMovie(path)
    hub.shareable_start("idle", sub)
    source = hub._sources[path]
    rec = source.subscriptions[0]
    return sub, source, rec


class TestRingAndFeed:
    def test_wraparound_synthesizes_end(self, hub):
        pub = _FakeMovie(PATH)
        sub, source, rec = _subscribe(hub, pub)
        session = rec.session
        # 0..4 依次 push->poll（每次独一帧，无 drop）
        for src in range(5):
            source.sink.on_frame(FRAME, src)
            kind, data, got_src = session.poll()
            assert kind == "frame"
            assert got_src == src
        # 源帧号回绕（-stream_loop 从 4 回 0）→ 合成 'end'
        source.sink.on_frame(FRAME, 0)
        assert session.poll() == ("end", None, None)

    def test_drop_oldest_preserves_order(self):
        ring = _RingBuffer(4)
        for src in range(6):
            ring.push(FRAME, src)  # 满时 drop 最旧
        popped = [ring.pop() for _ in range(4)]
        assert [src for _, src in popped] == [2, 3, 4, 5]
        assert ring.pop() is None  # 4 槽读完即空

    def test_cross_thread_fanout(self, hub):
        pub = _FakeMovie(PATH)
        sub, source, rec = _subscribe(hub, pub)
        session = rec.session
        produced = {"n": 0, "done": False}

        def producer():
            for src in range(20):
                source.sink.on_frame(FRAME, src)
                produced["n"] += 1
                time.sleep(0.001)
            produced["done"] = True

        t = threading.Thread(target=producer)
        t.start()
        got: list = []
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            kind, data, src = session.poll()
            if kind == "frame":
                got.append(src)
            elif kind in ("abort", "end"):
                break  # 生产端持续供帧，正常不触发
            time.sleep(0.001)
            if kind == "none" and produced["done"]:
                break  # 生产完成且消费端追平
        t.join(timeout=5.0)
        # 冒烟：帧到达、单调不减（drop-oldest 下允许跳帧，绝不乱序/重复后续）
        assert got, "跨线程消费未收到任何帧"
        assert all(got[i] <= got[i + 1] for i in range(len(got) - 1)), f"乱序: {got}"
        assert max(got) >= 19


# ---------------------------------------------------------------------------
# handover
# ---------------------------------------------------------------------------
class TestHandover:
    def test_publisher_leave_promotes_oldest_subscriber(self, hub):
        pub = _FakeMovie(PATH)
        hub.shareable_start("idle", pub)
        sub1 = _FakeMovie(PATH)
        hub.shareable_start("idle", sub1)
        sub2 = _FakeMovie(PATH)
        hub.shareable_start("idle", sub2)
        source = hub._sources[PATH]
        assert len(source.subscriptions) == 2

        # 发布者离开（自然播完切走）→ handover 扶正最老订阅者 sub1
        hub.shareable_end("idle", pub, natural=True)
        assert source.publisher is sub1
        assert sub1._publish_sink is not None
        assert sub1._publish_sink is source.sink
        assert sub1._feed_source is None
        # sub1 的 feed 被 abort → 其 reader 落回本地 ffmpeg（_reader_feed 返回 False）
        sub1_rec = hub._sources[PATH].subscriptions[0] if hub._sources.get(PATH) else None
        assert sub2._feed_source is not None  # sub2 仍是订阅者
        # sub1 被扶正后不再出现在订阅者列表
        assert all(r.movie is not sub1 for r in source.subscriptions)
        # 其余订阅者环由新发布者 on_frame 续供
        source.sink.on_frame(FRAME, 10)
        sub2_rec = source.subscriptions[0]
        kind, data, src = sub2_rec.session.poll()
        assert kind == "frame" and src == 10

    def test_publisher_leave_no_subscriber_releases_source(self, hub):
        pub = _FakeMovie(PATH)
        hub.shareable_start("idle", pub)
        hub.shareable_end("idle", pub, natural=True)
        assert not hub._sources
        assert pub._publish_sink is None


# ---------------------------------------------------------------------------
# 看门狗
# ---------------------------------------------------------------------------
class TestWatchdog:
    def test_no_frame_timeout_aborts(self, hub):
        pub = _FakeMovie(PATH)
        sub, source, rec = _subscribe(hub, pub)
        session = rec.session
        # 手动拨时钟到过去（零 sleep）：下次 poll 触发看门狗 'abort'
        session._stall_deadline = time.monotonic() - 0.001
        assert session.poll() == ("abort", None, None)

    def test_abort_flag_overrides_watchdog(self, hub):
        pub = _FakeMovie(PATH)
        sub, source, rec = _subscribe(hub, pub)
        rec.abort()
        assert rec.session.poll() == ("abort", None, None)

    def test_frames_reset_stall(self, hub):
        pub = _FakeMovie(PATH)
        sub, source, rec = _subscribe(hub, pub)
        session = rec.session
        for src in range(3):
            source.sink.on_frame(FRAME, src)
            assert session.poll()[0] == "frame"
        # 长时间无帧 → 看门狗触发（模拟发布者死亡/僵死）
        session._stall_deadline = time.monotonic() - 0.001
        assert session.poll() == ("abort", None, None)


# ---------------------------------------------------------------------------
# 摘环 / 摘 sink 幂等；stop_all 清空
# ---------------------------------------------------------------------------
class TestLifecycle:
    def test_detach_is_idempotent(self, hub):
        pub = _FakeMovie(PATH)
        hub.shareable_start("idle", pub)
        sub = _FakeMovie(PATH)
        hub.shareable_start("idle", sub)
        hub.shareable_end("idle", sub, natural=False)  # 订阅者离开
        assert PATH not in hub._sources  # 最后订阅者离开 → 源释放
        assert sub._feed_source is None
        # 重复收尾：no-op，不抛
        hub.shareable_end("idle", sub, natural=False)
        hub.shareable_end("idle", pub, natural=False)
        assert not hub._sources

    def test_stop_all_clears_sources(self, hub):
        pub = _FakeMovie(PATH)
        hub.shareable_start("idle", pub)
        sub = _FakeMovie(PATH)
        hub.shareable_start("idle", sub)
        hub.stop_all()
        assert not hub._sources

    def test_shutdown_is_noop(self, hub):
        pub = _FakeMovie(PATH)
        hub.shareable_start("idle", pub)
        sub = _FakeMovie(PATH)
        hub.shareable_start("idle", sub)
        hub.shutdown()  # 单窗关闭：hub 不动（进程级共享）
        assert PATH in hub._sources
        assert sub._feed_source is not None


# ---------------------------------------------------------------------------
# 节流 / pace 调和
# ---------------------------------------------------------------------------
class TestPace:
    def test_effective_divisor_is_min(self, hub):
        pub = _FakeMovie(PATH)
        assert hub.shareable_start("idle", pub) == "publish"
        sub = _FakeMovie(PATH)
        assert hub.shareable_start("idle", sub) == "feed"
        source = hub._sources[PATH]
        # 首订阅者接管 pace：source window 变外部 pace，有效 = min(1,1) = 1
        assert source.publisher.decode_pace_external is True
        assert pub.decode_throttle_divisor == 1
        # 发布窗期望 2（闲置降帧）时有效仍受订阅者 1 压制 → 1
        hub._report_desired_throttle(pub, 2)
        assert pub.decode_throttle_divisor == 1
        # 订阅者也期望 2 → 有效 = min(2,2) = 2
        hub._report_desired_throttle(sub, 2)
        assert pub.decode_throttle_divisor == 2
        # 订阅者变活跃（期望 1）→ 任一窗活跃 → 有效回落 1
        hub._report_desired_throttle(sub, 1)
        assert pub.decode_throttle_divisor == 1

    def test_no_subscriber_does_not_manage_pace(self, hub):
        pub = _FakeMovie(PATH)
        hub.shareable_start("idle", pub)
        source = hub._sources[PATH]
        assert source.publisher.decode_pace_external is False


def test_handover_resets_old_publisher_pace_external(hub):
    """P1-1 回归：handover 后旧发布者的 decode_pace_external 必须复位——
    否则它日后以订阅者身份再进场时 divisor 永久卡在旧值（半速不自愈）。"""
    pub = _FakeMovie(PATH)
    hub.shareable_start("idle", pub)
    sub = _FakeMovie(PATH)
    hub.shareable_start("idle", sub)
    assert pub.decode_pace_external is True, "发布者 pace 应被 hub 接管"

    hub.shareable_end("idle", pub, natural=True)  # handover 扶正 sub

    assert hub._sources[PATH].publisher is sub
    assert pub.decode_pace_external is False, \
        "旧发布者 pace 标志必须复位（P1-1）"
    assert sub.decode_pace_external is True, "新发布者接管 pace"
