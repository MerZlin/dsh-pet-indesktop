# -*- coding: utf-8 -*-
"""P3 broker：WebMClip feed 模式端到端单测（合成发布端直接喂 RGBA 帧）。

覆盖 _plan/current/P3_BROKER_DESIGN.md §3.4/§4 的 webm_clip 钩子契约：
- ``_feed_source``（消费端）：reader 线程 feed-pending → 有界等待 grant →
  合成发布端逐帧 poll 入队；帧序正确（无丢帧无乱序）；natural end → 结束
  标记 → finished 恰一次；
- deny / 授权超时 / 中途断流(abort) → 同一 reader 线程回退本地 ffmpeg
  （帧 0 起播，_reader_proc 真实拉起，无 errorOccurred）；
- ``_publish_sink``（发布端）：coordinator 本地解码每帧回调恰一次
  （on_frame(frame, src_idx) 每解码帧一次、按序、无重复），natural end
  触发 finished。

批5.3：decode_broker 退役后，WebMClip 的 feed 协议由进程内 DecodeFanoutHub
（FanoutFeed/FanoutFeedSession）继承。本文件改用本地 ``_StubFeed``（与 hub 的
FanoutFeed 同形：ready/result/expire/budget_ms 鸭子类型）驱动同一 ``_reader_feed``
协议，逐位锁定 WebMClip 的消费契约（帧序、end、abort→本地回退、发布 sink）。
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from pet.webm_clip import WebMClip

SAMPLE_WEBM = Path("assets/characters/shenshen/videos/idle/待机呼吸休闲.webm")
FRAME_BYTES = 640 * 360 * 4  # 921600
# 可区分长度的单帧 RGBA 字节（内容无需逐帧唯一：帧身份由 src 标记）
_FRAME = bytes(range(256)) * (FRAME_BYTES // 256)


class _StubFeed:
    """一次订阅尝试句柄（DecodeFanoutHub.FanoutFeed 的鸭子类型替身）。

    GUI 侧 ``complete(result)`` 填 grant/deny；reader 线程经 ``ready``/
    ``result``/``expire`` 读取。保留 ``budget_ms`` 以验证 ``_reader_feed`` 的
    grant 有界等待路径（进程内 hub 立即就绪，不实际等待）。
    """

    def __init__(self, req_id: str, asset: str) -> None:
        self.req_id = req_id
        self.asset = asset
        self.budget_ms = 600
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._result = None
        self._expired = False

    def complete(self, result) -> None:
        with self._lock:
            if self._expired:
                return
            self._result = result
            self._event.set()

    def expire(self) -> None:
        with self._lock:
            if self._expired:
                return
            self._expired = True
            self._event.set()

    @property
    def ready(self) -> bool:
        return self._event.is_set()

    @property
    def expired(self) -> bool:
        return self._expired

    @property
    def result(self):
        with self._lock:
            return self._result


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _consume_until(clip: WebMClip, predicate, timeout: float = 10.0) -> bool:
    """手动驱动主线程消费（等价于 QTimer 的 _poll 节奏，确定性更强）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        clip._poll()
        if predicate():
            return True
        time.sleep(0.002)
    return False


def _consume_one(clip: WebMClip, timeout: float = 8.0) -> bool:
    """等队列出现一帧后 _poll 消费恰一项（合成 feed 逐帧放行的对偶）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if clip._queue.qsize() > 0:
            clip._poll()
            return True
        time.sleep(0.002)
    return False


class _PacedFeedSession:
    """合成发布端 feed session（BrokerFeedSession 鸭子类型）。

    逐帧放行：消费端每消费一帧 release_next() 才出下一帧 → 队列永不溢出、
    帧序严格 0..N-1（可断言无丢帧）。stage_end/stage_abort 触发终态一次。
    """

    def __init__(self, payload: bytes, frame_count: int = 16) -> None:
        self._payload = payload
        self._frame_count = int(frame_count)
        self._delivered = 0
        self._permit = threading.Event()
        self._permit.set()  # 首帧立即可出
        self._terminal = None
        self._lock = threading.Lock()
        self.close_count = 0

    def release_next(self) -> None:
        self._permit.set()

    def stage_end(self) -> None:
        with self._lock:
            self._terminal = "end"
        self._permit.set()

    def stage_abort(self) -> None:
        with self._lock:
            self._terminal = "abort"
        self._permit.set()

    def poll(self):
        with self._lock:
            if self._terminal is not None:
                terminal = self._terminal
                self._terminal = None  # 终态只报一次
                return (terminal, None, None)
            if self._delivered < self._frame_count and self._permit.is_set():
                self._permit.clear()
                src = self._delivered
                self._delivered += 1
                return ("frame", self._payload, src)
        return ("none", None, None)

    def close(self) -> None:
        with self._lock:
            self.close_count += 1


class _CountingSink:
    """发布 sink 钩子（记录每帧回调的 src）。"""

    def __init__(self) -> None:
        self.srcs: list = []

    def on_frame(self, data: bytes, src: int) -> None:
        assert len(data) == FRAME_BYTES  # 回调必须是整帧 RGBA
        self.srcs.append(int(src))


# ---------------------------------------------------------------------------
# feed 模式：帧序 + natural end → finished
# ---------------------------------------------------------------------------
def test_feed_frame_order_and_natural_end_fires_finished(app):
    assert SAMPLE_WEBM.exists()
    clip = WebMClip(SAMPLE_WEBM)
    clip._duration = 10.0  # 跳过 meta 探测（feed 模式不触 ffmpeg）
    frame_count = 12
    session = _PacedFeedSession(_FRAME, frame_count=frame_count)
    feed = _StubFeed("feed-order", str(SAMPLE_WEBM))
    feed.complete(session)  # grant 已落定（feed-pending 直接放行）
    clip._feed_source = feed
    srcs: list = []
    finished: list = []
    errors: list = []
    clip.frameChanged.connect(srcs.append)
    clip.finished.connect(lambda: finished.append(True))
    clip.errorOccurred.connect(errors.append)
    try:
        assert clip.start() is True
        for k in range(frame_count):
            assert _consume_one(clip, timeout=8.0), f"feed 帧 {k} 未送达"
            assert srcs == list(range(k + 1)), f"帧序错位: srcs={srcs}"
            session.release_next()
        session.stage_end()
        assert _consume_until(clip, lambda: len(finished) == 1, timeout=8.0), \
            "natural end 未触发 finished"
        assert srcs == list(range(frame_count))  # 帧序正确、每帧恰一次
        assert finished == [True]
        assert errors == []
        assert not clip._running
        assert session.close_count == 1  # feed session 收尾 close 恰一次
    finally:
        clip.cleanup()
        app.processEvents()


# ---------------------------------------------------------------------------
# 授权失败（deny / 超时）与中途断流 → 回退本地 ffmpeg（帧 0 起播）
# ---------------------------------------------------------------------------
def _wait_local_ffmpeg(clip: WebMClip, timeout: float = 10.0) -> bool:
    """等本地 ffmpeg 解码进程拉起并存活（feed 模式本身绝不拉起 ffmpeg）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        proc = clip._reader_proc
        if proc is not None and proc.poll() is None:
            return True
        time.sleep(0.005)
    return False


def _drain_until_local_frame_zero(clip: WebMClip, timeout: float = 8.0) -> bool:
    """直接扫描队列直到出现本地解码帧（src==0）。

    回退路径先 _drain_queue_for_local 清空 feed 残留，本地 reader 从空队列
    起播 → 帧 0 必先入队；逐项弹出（丢弃等价）直到命中 src==0。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            item = clip._queue.get_nowait()
        except Exception:
            time.sleep(0.002)
            continue
        if item is None:
            continue
        _, src = item
        if int(src) == 0:
            return True
    return False


def test_feed_grant_denied_falls_back_to_local_ffmpeg(app):
    assert SAMPLE_WEBM.exists()
    clip = WebMClip(SAMPLE_WEBM)
    feed = _StubFeed("deny-1", str(SAMPLE_WEBM))
    feed.complete(None)  # decode_deny：授权失败
    clip._feed_source = feed
    errors: list = []
    clip.errorOccurred.connect(errors.append)
    try:
        assert clip.start() is True
        assert _wait_local_ffmpeg(clip), "deny 后本地 ffmpeg 未拉起"
        assert _drain_until_local_frame_zero(clip), "本地解码未从帧 0 起播"
        assert errors == []
    finally:
        clip.cleanup()
        app.processEvents()


def test_feed_grant_timeout_falls_back_to_local_ffmpeg(app):
    assert SAMPLE_WEBM.exists()
    clip = WebMClip(SAMPLE_WEBM)
    feed = _StubFeed("timeout-1", str(SAMPLE_WEBM))
    feed.budget_ms = 120  # 缩短订阅预算：feed-pending 有界等待后超时
    clip._feed_source = feed  # 永不 complete → 超时回退
    errors: list = []
    clip.errorOccurred.connect(errors.append)
    try:
        assert clip.start() is True
        assert _wait_local_ffmpeg(clip), "订阅超时后本地 ffmpeg 未拉起"
        assert _drain_until_local_frame_zero(clip), "本地解码未从帧 0 起播"
        assert errors == []
        # P3A P1-2：reader 放弃等待时闭锁 feed——此后迟到的 grant 会被
        # complete 路径立即 close，绝不遗留无主 attach 句柄。
        assert feed.expired, "reader 超时后 feed 未 expire 闭锁"
        assert feed.result is None
    finally:
        clip.cleanup()
        app.processEvents()


def test_feed_midstream_abort_falls_back_to_local_ffmpeg(app):
    assert SAMPLE_WEBM.exists()
    clip = WebMClip(SAMPLE_WEBM)
    clip._duration = 10.0
    session = _PacedFeedSession(_FRAME, frame_count=100)
    feed = _StubFeed("abort-1", str(SAMPLE_WEBM))
    feed.complete(session)  # grant：先流 feed 帧
    clip._feed_source = feed
    srcs: list = []
    errors: list = []
    clip.frameChanged.connect(srcs.append)
    clip.errorOccurred.connect(errors.append)
    try:
        assert clip.start() is True
        for k in range(3):
            assert _consume_one(clip, timeout=8.0), f"feed 帧 {k} 未送达"
            assert srcs == list(range(k + 1)), f"feed 帧序错位: srcs={srcs}"
            session.release_next()
        assert srcs == [0, 1, 2]
        # 发布端中途停止（aborted）→ 断流 → 同一 reader 线程回退本地 ffmpeg
        session.stage_abort()
        assert _wait_local_ffmpeg(clip), "abort 断流后本地 ffmpeg 未拉起"
        assert _drain_until_local_frame_zero(clip), "本地解码未从帧 0 起播"
        assert errors == []
        assert session.close_count == 1  # 断流收尾 close 恰一次
    finally:
        clip.cleanup()
        app.processEvents()


# ---------------------------------------------------------------------------
# F1：abort 原因透传 → handover 打 INFO（不再 WARNING），watchdog/stop_all 留 WARNING
# ---------------------------------------------------------------------------
class _AbortReasonSession:
    """3 元组之上透传 abort reason 的 feed session（F1 专测，直接驱动 _reader_feed）。"""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def poll(self):
        return ("abort", None, None, self._reason)

    def close(self) -> None:
        pass


def _run_reader_feed_abort(reason: str):
    """直接调用 ``clip._reader_feed`` 触发一次 abort 回退判定（零 ffmpeg）。
    返回 (clip, feed, stop_evt, gen)；feed 与 clip 由调用方清理。"""
    assert SAMPLE_WEBM.exists()
    clip = WebMClip(SAMPLE_WEBM)
    clip._duration = 10.0  # 跳过 meta 探测：feed 模式不触 ffmpeg
    session = _AbortReasonSession(reason)
    feed = _StubFeed(f"abort-{reason}", str(SAMPLE_WEBM))
    feed.complete(session)
    stop_evt = threading.Event()
    gen = clip._generation
    return clip, feed, stop_evt, gen


def test_feed_handover_abort_logs_info_not_warning(app, caplog):
    """F1：handover abort → 打 INFO（设计内行为），不再是 WARNING。"""
    clip, feed, stop_evt, gen = _run_reader_feed_abort("handover")
    try:
        with caplog.at_level(logging.INFO, logger="pet.webm_clip"):
            done = clip._reader_feed(feed, stop_evt, gen)
        assert done is False  # 仍回退本地解码（_reader_feed 契约）
        info = [r for r in caplog.records
                if r.levelno == logging.INFO and "handover" in r.getMessage()]
        assert info, "handover abort 应打 INFO"
        warn = [r for r in caplog.records
                if r.levelno == logging.WARNING and "断流" in r.getMessage()]
        assert not warn, "handover abort 不得再打断流 WARNING"
    finally:
        clip.cleanup()
        app.processEvents()


def test_feed_watchdog_abort_still_warns(app, caplog):
    """F1：watchdog/stop_all abort → 保留 WARNING（真断流仍是故障信号）。"""
    clip, feed, stop_evt, gen = _run_reader_feed_abort("watchdog")
    try:
        with caplog.at_level(logging.WARNING, logger="pet.webm_clip"):
            done = clip._reader_feed(feed, stop_evt, gen)
        assert done is False
        warn = [r for r in caplog.records
                if r.levelno == logging.WARNING and "断流" in r.getMessage()]
        assert warn, "watchdog/stop_all abort 应保留 WARNING"
    finally:
        clip.cleanup()
        app.processEvents()


# ---------------------------------------------------------------------------
# 发布 sink：coordinator 本地解码每帧回调恰一次
# ---------------------------------------------------------------------------
def test_publish_sink_on_frame_called_exactly_once_per_frame(app):
    assert SAMPLE_WEBM.exists()
    clip = WebMClip(SAMPLE_WEBM)
    sink = _CountingSink()
    clip._publish_sink = sink  # coordinator 角色：movie reader 逐帧镜像
    finished: list = []
    errors: list = []
    clip.finished.connect(lambda: finished.append(True))
    clip.errorOccurred.connect(errors.append)
    try:
        assert clip.start() is True
        # 全速解码到自然播完：手动消费直至 finished
        assert _consume_until(clip, lambda: len(finished) == 1, timeout=60.0), \
            f"本地解码未自然结束; sink={len(sink.srcs)} errors={errors}"
        assert finished == [True]
        assert errors == []
        n = len(sink.srcs)
        assert n >= 200  # 全素材 241 帧；至少接近完整
        assert sink.srcs == list(range(n))  # 每帧恰一次、按解码序
        assert n == clip.frameCount()  # 与实际素材帧数一致（仓库 = 241）
    finally:
        clip.cleanup()
        app.processEvents()
