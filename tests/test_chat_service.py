from __future__ import annotations

import time
import threading
from PySide6.QtWidgets import QApplication
from pet.chat.models import ProviderConfig
from pet.chat.service import ChatService


def _get_qapp():
    return QApplication.instance() or QApplication([])


class BlockingFakeProvider:
    def __init__(self, started_event: threading.Event | None = None, block_event: threading.Event | None = None):
        self.started_event = started_event or threading.Event()
        self.block_event = block_event or threading.Event()

    def stream(self, messages, config, cancel):
        self.started_event.set()
        while not cancel.is_set() and not self.block_event.is_set():
            time.sleep(0.01)
        if cancel.is_set():
            return
        yield "response"


class FastFakeProvider:
    def stream(self, messages, config, cancel):
        yield "hello world"


def test_concurrent_send_keeps_both_workers_in_set_and_cancels_old():
    app = _get_qapp()
    p1_started = threading.Event()
    p1_block = threading.Event()
    provider1 = BlockingFakeProvider(started_event=p1_started, block_event=p1_block)

    p2_started = threading.Event()
    p2_block = threading.Event()
    provider2 = BlockingFakeProvider(started_event=p2_started, block_event=p2_block)

    service = ChatService(provider=provider1)
    cfg = ProviderConfig(provider_id="test", name="test", base_url="http://invalid", model="test")

    rid1 = service.send([], cfg)
    assert p1_started.wait(timeout=2.0), "Worker 1 did not start"
    w1 = service._worker
    c1 = service._cancel
    assert w1 in service._workers
    assert not c1.is_set()

    # 替换 provider 并发第二个请求
    service.provider = provider2
    rid2 = service.send([], cfg)
    assert p2_started.wait(timeout=2.0), "Worker 2 did not start"
    w2 = service._worker
    c2 = service._cancel

    # a) 连续 send 两次后两个 worker 都在集合中、旧的被取消标记已置位
    assert len(service._workers) == 2
    assert w1 in service._workers
    assert w2 in service._workers
    assert c1.is_set()
    assert not c2.is_set()

    # 清理退出：shutdown 会置 cancel 并 wait(1500)，worker 收到 cancel 退出后 emit finished
    ok = service.shutdown()
    assert ok is True
    # 由于 finished 槽函数挂在 Qt.QueuedConnection 上，需 processEvents 驱动槽函数执行 discard
    app.processEvents()
    assert len(service._workers) == 0


def test_worker_finished_removes_from_workers_set():
    app = _get_qapp()
    service = ChatService(provider=FastFakeProvider())
    cfg = ProviderConfig(provider_id="test", name="test", base_url="http://invalid", model="test")

    finished_events = []
    service.finished.connect(lambda rid, text: finished_events.append(text))

    rid = service.send([], cfg)
    worker = service._worker
    assert worker in service._workers

    # 等待完成
    deadline = time.time() + 2.0
    while len(finished_events) == 0 and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()

    # b) worker 结束后自动从集合移除
    assert len(finished_events) == 1
    assert worker not in service._workers
    assert len(service._workers) == 0


def test_shutdown_cancels_and_clears_workers_set():
    app = _get_qapp()
    p1_started = threading.Event()
    provider = BlockingFakeProvider(started_event=p1_started)

    service = ChatService(provider=provider)
    cfg = ProviderConfig(provider_id="test", name="test", base_url="http://invalid", model="test")

    service.send([], cfg)
    assert p1_started.wait(timeout=2.0)
    assert len(service._workers) == 1

    # c) shutdown 退出，正常退出返回 True，集合靠 finished 信号或结束后清空
    ok = service.shutdown()
    assert ok is True
    # 等 finished 队列投递完成
    app.processEvents()
    assert len(service._workers) == 0


def test_shutdown_uninterruptible_worker_returns_false_and_retains_reference():
    """a) fake worker 阻塞 5 秒不可中断 → shutdown() 返回 False 且 worker 仍在 _workers 集合中（引用不丢）"""
    app = _get_qapp()

    class UninterruptibleWorker:
        def __init__(self, started_event):
            self.started_event = started_event
            self.cancel = None

        def stream(self, messages, config, cancel):
            self.started_event.set()
            # 模拟阻塞 5 秒且不响应 cancel
            time.sleep(2.0)
            yield "done"

    started = threading.Event()
    provider = UninterruptibleWorker(started)
    service = ChatService(provider=provider)
    cfg = ProviderConfig(provider_id="test", name="test", base_url="http://invalid", model="test")

    service.send([], cfg)
    assert started.wait(timeout=2.0)
    worker = service._worker
    assert worker in service._workers

    # 临时把 worker.wait(1500) 设为短等待 50ms 加速测试
    orig_wait = worker.wait
    worker.wait = lambda msecs=1500: orig_wait(50)

    ok = service.shutdown()
    assert ok is False
    assert worker in service._workers, "未退出的 worker 必须保留在 _workers 集合中，防止引用丢失导致崩溃"

    # 清理并等待 worker 自然结束避免后台挂起
    worker.wait = orig_wait
    worker.wait(2500)
    app.processEvents()
    assert worker not in service._workers


def test_provider_cancel_closes_blocking_response():
    """b) fake provider 的 response 阻塞 read → cancel 后 close 被调用、read 返回"""
    from pet.chat.providers import OpenAICompatibleProvider

    class BlockingFakeResponse:
        def __init__(self):
            self.closed = False
            self.read_called = threading.Event()
            self.unblock_event = threading.Event()

        def read(self, size=4096):
            self.read_called.set()
            # 阻塞直到 close() 被调用或 unblock
            while not self.closed and not self.unblock_event.is_set():
                time.sleep(0.01)
            return b""

        def close(self):
            self.closed = True
            self.unblock_event.set()

    provider = OpenAICompatibleProvider()
    fake_resp = BlockingFakeResponse()
    provider._current_response = fake_resp

    cancel_event = threading.Event()
    finished = threading.Event()

    def _stream():
        # 模拟 provider 循环
        try:
            while not cancel_event.is_set():
                chunk = fake_resp.read(4096)
                if not chunk:
                    break
        finally:
            if provider._current_response is fake_resp:
                provider._current_response = None
            fake_resp.close()
            finished.set()

    t = threading.Thread(target=_stream)
    t.start()

    assert fake_resp.read_called.wait(timeout=2.0)
    # 触发取消
    cancel_event.set()
    provider.cancel()

    assert finished.wait(timeout=2.0), "cancel 路径未能在超时前中断阻塞的 read"
    t.join(timeout=2.0)
    assert fake_resp.closed is True
