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
        # 发布者存活判定输入（F2 复审 nit-3）：默认存活（运行中、未驻留）
        self._running = True
        self._soft_parked = False

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
            kind, data, got_src, reason = session.poll()
            assert kind == "frame"
            assert got_src == src
        # 源帧号回绕（-stream_loop 从 4 回 0）→ 合成 'end'
        source.sink.on_frame(FRAME, 0)
        assert session.poll() == ("end", None, None, None)

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
            kind, data, src, _ = session.poll()
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

        # 发布者离开（中途打断/非自然切走）→ handover 扶正最老订阅者 sub1
        hub.shareable_end("idle", pub, natural=False)
        assert source.publisher is sub1
        assert sub1._publish_sink is not None
        assert sub1._publish_sink is source.sink
        assert sub1._feed_source is None
        # sub1 的 feed 被 abort（reason='handover'）→ 其 reader 落回本地 ffmpeg
        assert sub2._feed_source is not None  # sub2 仍是订阅者
        # sub1 被扶正后不再出现在订阅者列表
        assert all(r.movie is not sub1 for r in source.subscriptions)
        # 其余订阅者环由新发布者 on_frame 续供
        source.sink.on_frame(FRAME, 10)
        sub2_rec = source.subscriptions[0]
        kind, data, src, reason = sub2_rec.session.poll()
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
        assert session.poll() == ("abort", None, None, "watchdog")

    def test_abort_flag_overrides_watchdog(self, hub):
        pub = _FakeMovie(PATH)
        sub, source, rec = _subscribe(hub, pub)
        rec.abort('handover')  # handover 主动 abort：reason 透传
        assert rec.session.poll() == ("abort", None, None, "handover")

    def test_frames_reset_stall(self, hub):
        pub = _FakeMovie(PATH)
        sub, source, rec = _subscribe(hub, pub)
        session = rec.session
        for src in range(3):
            source.sink.on_frame(FRAME, src)
            assert session.poll()[0] == "frame"
        # 长时间无帧 → 看门狗触发（模拟发布者死亡/僵死）
        session._stall_deadline = time.monotonic() - 0.001
        assert session.poll() == ("abort", None, None, "watchdog")


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

    hub.shareable_end("idle", pub, natural=False)  # 中途打断 → handover 扶正 sub

    assert hub._sources[PATH].publisher is sub
    assert pub.decode_pace_external is False, \
        "旧发布者 pace 标志必须复位（P1-1）"
    assert sub.decode_pace_external is True, "新发布者接管 pace"


# ---------------------------------------------------------------------------
# F1：abort 原因透传（handover / stop_all / watchdog）
# ---------------------------------------------------------------------------
class TestF1AbortReason:
    def test_stop_all_aborts_with_reason(self, hub):
        pub = _FakeMovie(PATH)
        hub.shareable_start("idle", pub)
        sub = _FakeMovie(PATH)
        hub.shareable_start("idle", sub)
        source = hub._sources[PATH]
        sub_rec = source.subscriptions[0]
        hub.stop_all()
        # 进程级收口 → abort(reason='stop_all')
        assert sub_rec.session.poll() == ("abort", None, None, "stop_all")
        assert not hub._sources

    def test_watchdog_aborts_with_reason(self, hub):
        pub = _FakeMovie(PATH)
        sub, source, rec = _subscribe(hub, pub)
        rec.session._stall_deadline = time.monotonic() - 0.001
        assert rec.session.poll() == ("abort", None, None, "watchdog")


# ---------------------------------------------------------------------------
# F2：自然圈末解散（draining）vs 中途打断 handover
# ---------------------------------------------------------------------------
class TestF2NaturalDisband:
    def test_natural_end_disband_no_abort_no_spawn(self, hub):
        """圈末自然解散：发布者 natural=True 离开且仍有订阅者 → 不做 handover，
        订阅者零 abort（零本地回退 spawn），随自身 is_last 自行 unregister，
        最后一个离开时 _release_source。"""
        pub = _FakeMovie(PATH)
        assert hub.shareable_start("idle", pub) == "publish"
        sub = _FakeMovie(PATH)
        assert hub.shareable_start("idle", sub) == "feed"
        source = hub._sources[PATH]
        sub_rec = source.subscriptions[0]

        # 圈末自然解散：标记 draining，不 handover
        hub.shareable_end("idle", pub, natural=True)
        assert source.draining is True
        # 发布者仍为 pub（未被扶正）；订阅者 feed 会话未 abort（零 abort）
        assert source.publisher is pub
        assert sub_rec.session._aborted is False
        # nit-2 加固：draining 不吞环尾帧——发布者回绕前最后一帧（src=240）
        # 仍可被订阅者 poll 到（「吃到尾帧再自行收尾」是 F2 的核心契约）
        source.sink.on_frame(FRAME, 240)
        kind, data, src, reason = sub_rec.session.poll()
        assert kind == "frame" and src == 240 and reason is None
        # 订阅者 poll 不得出 'abort'（无本地回退 spawn 的 feed 侧前置）
        assert sub_rec.session.poll()[0] != "abort"
        # 订阅者随自身 is_last 自行 unregister → 最后一个 _release_source
        hub.shareable_end("idle", sub, natural=True)
        assert not hub._sources
        assert sub._feed_source is None

    def test_draining_new_window_builds_fresh_source(self, hub):
        """draining 期新窗进场：不订阅已死源 → 释放旧源、以本窗建新源（publish）。"""
        pub = _FakeMovie(PATH)
        assert hub.shareable_start("idle", pub) == "publish"
        sub = _FakeMovie(PATH)
        assert hub.shareable_start("idle", sub) == "feed"
        source = hub._sources[PATH]
        sub_rec = source.subscriptions[0]
        hub.shareable_end("idle", pub, natural=True)
        assert source.draining is True

        # draining 期新窗进场：不订阅死源 → 释放旧源、建新源（本窗为发布者）
        new2 = _FakeMovie(PATH)
        assert hub.shareable_start("idle", new2) == "publish"
        new_source = hub._sources[PATH]
        assert new_source is not source
        assert new_source.publisher is new2
        assert new2._publish_sink is not None
        assert new2._feed_source is None
        # 旧源已收口：sink 关闭、订阅者列表清空（其 feed 被闭）
        assert source.sink._open is False
        assert source.subscriptions == []
        # 复审 P1-1：被释放的存量订阅者必须先收到 'disband' abort（立即回退），
        # 而不是在空环上白等看门狗（≤1.9s 冻结 + 整段重播）
        assert sub_rec.session._aborted is True
        assert sub_rec.session.poll() == ("abort", None, None, "disband")

    def test_interrupt_handover_preserved(self, hub):
        """中途打断（natural=False）：原 handover 语义不变，且被扶正者 feed
        以 reason='handover' 被 abort（F1 透传）。"""
        pub = _FakeMovie(PATH)
        assert hub.shareable_start("idle", pub) == "publish"
        sub1 = _FakeMovie(PATH)
        assert hub.shareable_start("idle", sub1) == "feed"
        source = hub._sources[PATH]
        sub1_rec = source.subscriptions[0]

        hub.shareable_end("idle", pub, natural=False)
        # handover 扶正 sub1（原语义逐位保留）
        assert source.publisher is sub1
        assert sub1._publish_sink is source.sink
        assert sub1._feed_source is None
        # 被扶正者 feed 以 reason='handover' 被 abort
        assert sub1_rec.session.poll() == ("abort", None, None, "handover")


class TestPublisherAlive:
    """F2 复审 nit-3：_publisher_alive 两分支的单元覆盖（TOCTOU 回归哨兵）。"""

    def test_dead_publisher_rebuilds_source(self, hub):
        """发布者死（非 draining）：新窗 shareable_start → 释放旧源、建新源。"""
        pub = _FakeMovie(PATH)
        assert hub.shareable_start("idle", pub) == "publish"
        sub = _FakeMovie(PATH)
        assert hub.shareable_start("idle", sub) == "feed"
        source = hub._sources[PATH]
        sub_rec = source.subscriptions[0]
        # 发布者死：未运行且未软停驻留（如窗口崩溃/异常退出未经 shareable_end）
        pub._running = False
        pub._soft_parked = False

        new2 = _FakeMovie(PATH)
        assert hub.shareable_start("idle", new2) == "publish"
        assert hub._sources[PATH] is not source
        assert hub._sources[PATH].publisher is new2
        # 存量订阅者立即收到 abort（不白等看门狗）
        assert sub_rec.session.poll() == ("abort", None, None, "disband")

    def test_soft_parked_publisher_counts_alive(self, hub):
        """软停驻留（将被 re-arm 续圈）的发布者必须算存活：新窗正常 'feed' 订阅，
        不得误判死而释放源。"""
        pub = _FakeMovie(PATH)
        assert hub.shareable_start("idle", pub) == "publish"
        pub._running = False
        pub._soft_parked = True  # 圈边界驻留中，等 re-arm

        sub = _FakeMovie(PATH)
        assert hub.shareable_start("idle", sub) == "feed"
        assert hub._sources[PATH].publisher is pub
        assert sub._feed_source is not None
