# -*- coding: utf-8 -*-
"""P3 broker 机制层单测（无 Qt 依赖，可 offscreen/无显示环境直跑）。

覆盖 _plan/current/P3_BROKER_DESIGN.md §3.2/§3.3/§4 的共享内存机制：
- BrokerShmSession create → publish → attach → read roundtrip；
- 奇偶 seqlock 提交语义（P3A P0-1：原子奇数 in-progress → 帧/动态区 →
  原子偶数 commit；frame_count 帧进度；读端稳定偶数快照校验）；
- K=4 ring 覆盖语义（槽覆盖后旧帧不可读——read_frame 对窗口外 src 返回
  None（P3A R2 P2-1）；feed 慢于发布端时跳最新）；
- P3A R2 P2-1：read_frame(src) 稳定快照 ring 窗口校验（未发布/已覆盖/
  负帧号一律 None；expected_seq 过期 → None）；
- mark_natural_end / mark_aborted 终态（幂等、互斥、flags 位）与
  BrokerFeedSession 的 ('end'/'abort') 收尾事件；
- attach 失败路径统一 close 临时句柄 + 实际尺寸校验（P3A P1-4）；
- unlink 后 attach 失败；消费端 close 不 unlink；
- BrokerBudget 硬上界拒绝；
- multiprocessing spawn 子进程真跨进程 attach 并发读帧压测（Windows 语义，
  P3A P0-1 要求的真实多进程压力而非单次 roundtrip）。

平台门禁（P3A R2 P0-1 / R3）：本文件是**机制**测试，直接调用公开低层入口
``BrokerShmSession.create/attach``；R3 起这两个入口内建平台门禁（非支持平台
直接抛 OSError，见模块 docstring），故整文件只在支持平台（Windows
AMD64/x86_64 TSO）上运行——与 test_decode_broker_control.py 的整文件 skip
一致。门禁本身的逻辑测试（broker_platform_supported 矩阵 / BrokerFacade
enabled 判定 / 低层 create-attach 拒绝，含 Windows ARM 模拟）在
tests/test_decode_broker_platform.py，全平台可跑以证明拒绝。
"""
from __future__ import annotations

import multiprocessing
import os
import threading
import time
import uuid

import pytest

from pet.decode_broker import (
    BPP,
    FLAG_ABORTED,
    FLAG_RUN_ENDED_NATURAL,
    FLAG_SESSION_ACTIVE,
    HEADER_SIZE,
    SLOT_COUNT_DEFAULT,
    BrokerBudget,
    BrokerFeedSession,
    BrokerShmSession,
    SessionHeader,
    broker_platform_supported,
    frame_bytes,
    session_size,
)

# P3A R2 P0-1 / R3：机制测试依赖真实 BrokerShmSession.create/attach（R3 起
# 内建平台门禁），只在支持平台（Windows AMD64/x86_64 TSO）上运行。
pytestmark = pytest.mark.skipif(
    not broker_platform_supported(),
    reason="P3A R2/R3: broker seqlock 机制仅在 Windows x86/x64 TSO 平台验证",
)

W, H = 40, 30  # 小几何提速（帧 4800B）；ring/budget 语义与 640x360 一致


def _shm_name(prefix: str = "dsh-broker-test") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def _payload(w: int, h: int, src: int) -> bytes:
    """按 src 生成可区分的帧字节（长度 = w*h*4）。"""
    n = w * h * 4
    return bytes(((src * 7 + i * 13) % 256) for i in range(n))


# ---------------------------------------------------------------------------
# 头部布局
# ---------------------------------------------------------------------------
def test_header_layout_pack_unpack_roundtrip_and_size():
    header = SessionHeader(
        frame_w=W, frame_h=H, fps_x1000=24000, total_frames=241,
        slot_count=4, seq=12, last_src=9, last_slot=1, epoch=0x123456789,
        pub_pid=4242, flags=FLAG_SESSION_ACTIVE,
    )
    packed = header.pack()
    assert len(packed) == HEADER_SIZE == 128
    restored = SessionHeader.unpack(packed)
    for name in SessionHeader.__slots__:
        assert getattr(restored, name) == getattr(header, name), name
    assert session_size(W, H, BPP, 4) == HEADER_SIZE + 4 * frame_bytes(W, H, BPP)
    assert restored.validate(W, H, BPP, 4) is None
    with pytest.raises(ValueError):
        restored.validate(W, H, BPP, 8)  # slot_count 错配


# ---------------------------------------------------------------------------
# create → publish → attach → read roundtrip + seq 提交字段
# ---------------------------------------------------------------------------
def test_create_publish_attach_read_roundtrip():
    name = _shm_name()
    publisher = BrokerShmSession.create(name, W, H, fps=24.0, total_frames=241)
    try:
        assert not publisher.closed
        assert publisher.shm_size == session_size(W, H, BPP, SLOT_COUNT_DEFAULT)
        # K=4 ring：恰写 4 帧（每槽一帧），逐槽可读回
        payloads = {src: _payload(W, H, src) for src in range(4)}
        for src in range(4):
            publisher.publish_frame(payloads[src], src)

        reader = BrokerShmSession.attach(name, W, H)
        try:
            header = reader.read_header()
            # 奇偶 seqlock：seq = 提交序号（偶数 = 已提交；每帧 +2）；
            # frame_count = 已提交帧数（帧进度计数，终态标记不推进）。
            assert header.frame_count == 4
            assert header.seq == 8  # 4 帧 × 2（in-progress 奇数 + commit 偶数）
            assert header.last_src == 3
            assert header.last_slot == 3
            assert header.flags & FLAG_SESSION_ACTIVE
            assert header.pub_pid == os.getpid()
            assert header.epoch != 0
            assert (header.frame_w, header.frame_h, header.bpp) == (W, H, BPP)
            assert header.slot_count == SLOT_COUNT_DEFAULT
            for src in range(4):
                assert reader.read_frame(src) == payloads[src]
        finally:
            reader.close()
        # 消费端 close 只是关句柄，不 unlink → 再次 attach 仍可
        again = BrokerShmSession.attach(name, W, H)
        assert again.read_header().seq == 8
        again.close()
    finally:
        publisher.unlink()


def test_publish_pads_short_and_truncates_long_payload():
    name = _shm_name()
    publisher = BrokerShmSession.create(name, W, H, fps=24.0, total_frames=241)
    try:
        expect = frame_bytes(W, H, BPP)
        publisher.publish_frame(b"", 0)  # 过短 → 补零
        reader = BrokerShmSession.attach(name, W, H)
        try:
            data = reader.read_frame(0)
            assert data is not None and len(data) == expect
            publisher.publish_frame(b"\xab" * (expect * 2), 1)  # 过长 → 截断
            assert reader.read_frame(1) == b"\xab" * expect
        finally:
            reader.close()
    finally:
        publisher.unlink()


# ---------------------------------------------------------------------------
# seq 提交标记复查：并发写读不撕裂、帧内容一致
# ---------------------------------------------------------------------------
def test_seq_commit_marker_review_under_concurrent_writer():
    name = _shm_name()
    publisher = BrokerShmSession.create(name, W, H, fps=24.0, total_frames=500)
    total = 400
    payloads = {src: _payload(W, H, src) for src in range(total)}
    reader = BrokerShmSession.attach(name, W, H)
    feed = BrokerFeedSession(reader, {"seq": 0, "last_src": -1})
    errors = []

    def writer() -> None:
        try:
            for src in range(total):
                publisher.publish_frame(payloads[src], src)
            publisher.mark_natural_end()
        except Exception as exc:  # pragma: no cover - 仅失败取证
            errors.append(repr(exc))

    thread = threading.Thread(target=writer, name="broker-test-writer")
    thread.start()
    seen_srcs = []
    deadline = time.monotonic() + 15.0
    try:
        while time.monotonic() < deadline:
            kind, data, src = feed.poll()
            if kind == "frame":
                assert data == payloads[src], "读到撕裂/错位帧"
                if seen_srcs:
                    assert src > seen_srcs[-1], "feed 帧号必须单调（跳最新可跳，不可回退）"
                seen_srcs.append(src)
            elif kind == "end":
                break
            elif kind == "abort":
                pytest.fail("并发写读中出现异常 abort")
            else:
                time.sleep(0.001)
        else:
            pytest.fail("15s 内未等到 run_ended_natural")
        assert errors == []
        assert seen_srcs, "并发写读应至少收到一帧"
        assert seen_srcs[-1] < total
        # 终态后：frame_count = 发布总数（帧进度）；seq = 提交序号
        # （400 帧 ×2 + mark_natural_end ×2 = 802，偶数 = 已提交）
        terminal = reader.read_header()
        assert terminal.frame_count == total
        assert terminal.seq == 2 * total + 2
        assert terminal.seq % 2 == 0
        assert terminal.flags & FLAG_RUN_ENDED_NATURAL
    finally:
        thread.join(timeout=10)
        feed.close()
        publisher.unlink()


# ---------------------------------------------------------------------------
# K=4 ring 覆盖语义
# ---------------------------------------------------------------------------
def test_ring_slot_overwrite_old_frame_unreadable():
    name = _shm_name()
    publisher = BrokerShmSession.create(name, W, H, fps=24.0, total_frames=241)
    try:
        payloads = {src: _payload(W, H, src) for src in range(5)}
        for src in range(4):
            publisher.publish_frame(payloads[src], src)
        reader = BrokerShmSession.attach(name, W, H)
        try:
            # 帧 0 与帧 4 同槽（0 % 4）：写入帧 4 覆盖帧 0
            publisher.publish_frame(payloads[4], 4)
            assert reader.read_header().last_src == 4
            assert reader.read_header().last_slot == 0
            assert reader.read_frame(4) == payloads[4]
            # P3A R2 P2-1：槽 0 现在承载帧 4 的字节，帧 0 已被 ring 覆盖——
            # read_frame(0) 必须返回 None（协议层丢帧），绝不把槽 0 的帧 4
            # 内容当作帧 0 交给调用方。
            assert reader.read_frame(0) is None
            # 未覆盖槽位仍可读（src ∈ 窗口 [1, 4]）
            for src in (1, 2, 3):
                assert reader.read_frame(src) == payloads[src]
        finally:
            reader.close()
    finally:
        publisher.unlink()


def test_feed_skips_overwritten_frames_and_follows_latest():
    """feed 慢于发布端：落后帧被 ring 覆盖 → 消费端跳最新（与本地丢帧同语义）。"""
    name = _shm_name()
    publisher = BrokerShmSession.create(name, W, H, fps=24.0, total_frames=241)
    try:
        payloads = {src: _payload(W, H, src) for src in range(13)}
        # 消费端加入前发布端已快速连发 10 帧（含 3 轮同槽覆盖：0/4/8、1/5/9 …）
        for src in range(10):
            publisher.publish_frame(payloads[src], src)
        reader = BrokerShmSession.attach(name, W, H)
        feed = BrokerFeedSession(reader, {"seq": 0, "last_src": -1})
        try:
            kind, data, src = feed.poll()
            assert kind == "frame"
            # join at current position：中间帧被覆盖/跳过，直接取到最新帧
            assert src == 9 and data == payloads[9]
            # 之后逐帧发布，feed 单调跟进（无回退）
            for expected in (10, 11, 12):
                publisher.publish_frame(payloads[expected], expected)
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    kind, data, got = feed.poll()
                    if kind == "frame":
                        assert got == expected and data == payloads[expected]
                        break
                    assert kind == "none"
                    time.sleep(0.001)
                else:
                    pytest.fail(f"帧 {expected} 未在 5s 内送达")
        finally:
            feed.close()
            reader.close()
    finally:
        publisher.unlink()


# ---------------------------------------------------------------------------
# P3A R2 P2-1：read_frame(src) 协议层 ring 窗口校验
# （src 必须落在稳定快照的 [last_src-K+1, last_src] 窗口；窗口外 = 未发布
#  或已被同槽更新的帧覆盖 → None，绝不把槽内容当错误帧号交给调用方）
# ---------------------------------------------------------------------------
def test_read_frame_before_any_publish_returns_none():
    name = _shm_name()
    publisher = BrokerShmSession.create(name, W, H, fps=24.0, total_frames=241)
    try:
        reader = BrokerShmSession.attach(name, W, H)
        try:
            # 尚未发布任何帧：frame_count == 0 → 任何 src（含 0）都不可读
            assert reader.read_header().frame_count == 0
            assert reader.read_frame(0) is None
            assert reader.read_frame(3) is None
        finally:
            reader.close()
    finally:
        publisher.unlink()


def test_read_frame_window_rejects_unpublished_and_overwritten_src():
    """窗口 = [last_src-K+1, last_src]：未发布(src>last_src) 与已覆盖
    (src 出窗口下界) 一律返回 None，绝不返回同槽新帧内容。"""
    name = _shm_name()
    publisher = BrokerShmSession.create(name, W, H, fps=24.0, total_frames=241)
    try:
        payloads = {src: _payload(W, H, src) for src in range(6)}
        for src in range(4):
            publisher.publish_frame(payloads[src], src)
        reader = BrokerShmSession.attach(name, W, H)
        try:
            # K=4 恰写 4 帧：窗口 [0, 3]，全部可读
            for src in range(4):
                assert reader.read_frame(src) == payloads[src]
            # 负帧号 / 未发布（> last_src=3）→ None
            assert reader.read_frame(-1) is None
            assert reader.read_frame(4) is None
            assert reader.read_frame(99) is None
            # 第 5 帧发布（覆盖槽 0）→ 帧 0 出窗口：read_frame(0) → None
            publisher.publish_frame(payloads[4], 4)
            assert reader.read_header().last_src == 4
            assert reader.read_frame(0) is None   # 已覆盖：绝不返回帧 4
            assert reader.read_frame(4) == payloads[4]
            for src in (1, 2, 3):
                assert reader.read_frame(src) == payloads[src]
            # 第 6 帧发布（覆盖槽 1）→ 窗口 [2, 5]：帧 1 也出窗口
            publisher.publish_frame(payloads[5], 5)
            assert reader.read_frame(1) is None
            for src in (2, 3, 4, 5):
                assert reader.read_frame(src) == payloads[src]
        finally:
            reader.close()
    finally:
        publisher.unlink()


def test_read_frame_rejects_stale_expected_seq():
    """expected_seq 语义：read_frame 用自己的稳定快照做窗口判定，且要求
    当前 seq == 调用方快照 seq——发布端在快照后提交新帧 → None（重取）。"""
    name = _shm_name()
    publisher = BrokerShmSession.create(name, W, H, fps=24.0, total_frames=241)
    try:
        publisher.publish_frame(_payload(W, H, 0), 0)
        reader = BrokerShmSession.attach(name, W, H)
        try:
            stale = reader.read_header()  # seq=2（帧 0 已提交）
            publisher.publish_frame(_payload(W, H, 1), 1)  # seq → 4
            # 旧快照的 seq：当前已推进 → None（调用方须重取最新快照）
            assert reader.read_frame(1, expected_seq=int(stale.seq)) is None
            # 最新快照 seq → 读到帧 1
            fresh = reader.read_header()
            assert reader.read_frame(1, expected_seq=int(fresh.seq)) == _payload(W, H, 1)
        finally:
            reader.close()
    finally:
        publisher.unlink()


# ---------------------------------------------------------------------------
# 终态：mark_natural_end / mark_aborted
# ---------------------------------------------------------------------------
def test_mark_natural_end_terminal_state_and_feed_end():
    name = _shm_name()
    publisher = BrokerShmSession.create(name, W, H, fps=24.0, total_frames=241)
    try:
        publisher.publish_frame(_payload(W, H, 0), 0)
        assert publisher.mark_natural_end() is True  # 首次置位
        assert publisher.mark_natural_end() is False  # 幂等
        assert publisher.mark_aborted() is False  # 自然结束优先，不可改中止
        header = publisher.read_header()
        assert header.flags & FLAG_RUN_ENDED_NATURAL
        assert header.flags & FLAG_SESSION_ACTIVE  # 自然结束不清 active（宽限读尾帧）
        assert not header.flags & FLAG_ABORTED

        reader = BrokerShmSession.attach(name, W, H)
        feed = BrokerFeedSession(reader, {"seq": header.seq, "last_src": 0})
        try:
            kind, _, _ = feed.poll()
            assert kind == "end"
        finally:
            feed.close()
            reader.close()
    finally:
        publisher.unlink()


def test_mark_aborted_terminal_state_and_feed_abort():
    name = _shm_name()
    publisher = BrokerShmSession.create(name, W, H, fps=24.0, total_frames=241)
    try:
        publisher.publish_frame(_payload(W, H, 0), 0)
        assert publisher.mark_aborted() is True
        assert publisher.mark_aborted() is False  # 幂等
        assert publisher.mark_natural_end() is False  # 已中止不可再自然结束
        header = publisher.read_header()
        assert header.flags & FLAG_ABORTED
        assert not header.flags & FLAG_SESSION_ACTIVE  # 中止清 active
        assert not header.flags & FLAG_RUN_ENDED_NATURAL

        reader = BrokerShmSession.attach(name, W, H)
        feed = BrokerFeedSession(reader, {"seq": header.seq, "last_src": 0})
        try:
            kind, _, _ = feed.poll()
            assert kind == "abort"
        finally:
            feed.close()
            reader.close()
    finally:
        publisher.unlink()


def test_feed_stall_budget_reports_abort():
    """发布端无新帧且未标记 → 停滞看门狗超时回退（abort）。"""
    from pet.decode_broker import STALL_BUDGET_MS

    name = _shm_name()
    publisher = BrokerShmSession.create(name, W, H, fps=24.0, total_frames=241)
    try:
        publisher.publish_frame(_payload(W, H, 0), 0)
        reader = BrokerShmSession.attach(name, W, H)
        # grant 后发布端再没出帧：构造停滞场景
        feed = BrokerFeedSession(reader, {"seq": 1, "last_src": 0})
        try:
            kind, _, _ = feed.poll()
            assert kind == "none"  # 刚过 grant：预算内先返回 none
            deadline = time.monotonic() + (STALL_BUDGET_MS / 1000.0) + 2.0
            while time.monotonic() < deadline:
                kind, _, _ = feed.poll()
                if kind == "abort":
                    break
                time.sleep(0.01)
            else:
                pytest.fail("停滞看门狗未在预算+2s 内报 abort")
        finally:
            feed.close()
            reader.close()
    finally:
        publisher.unlink()


# ---------------------------------------------------------------------------
# unlink / close 语义与几何错配
# ---------------------------------------------------------------------------
def test_unlink_then_attach_fails():
    name = _shm_name()
    publisher = BrokerShmSession.create(name, W, H, fps=24.0, total_frames=241)
    publisher.unlink()
    assert publisher.closed
    with pytest.raises((FileNotFoundError, OSError)):
        BrokerShmSession.attach(name, W, H)


def test_consumer_close_does_not_unlink():
    name = _shm_name()
    publisher = BrokerShmSession.create(name, W, H, fps=24.0, total_frames=241)
    try:
        reader = BrokerShmSession.attach(name, W, H)
        reader.close()
        assert reader.closed
        # close 只关本地句柄：名字仍可重新 attach
        again = BrokerShmSession.attach(name, W, H)
        again.close()
    finally:
        publisher.unlink()


def test_attach_geometry_or_version_mismatch_rejected():
    name = _shm_name()
    publisher = BrokerShmSession.create(name, W, H, fps=24.0, total_frames=241)
    try:
        with pytest.raises(ValueError):
            BrokerShmSession.attach(name, W + 1, H)
        with pytest.raises(ValueError):
            BrokerShmSession.attach(name, W, H, expected_slot_count=2)
    finally:
        publisher.unlink()


def test_publish_after_close_or_unlink_is_noop():
    name = _shm_name()
    publisher = BrokerShmSession.create(name, W, H, fps=24.0, total_frames=241)
    publisher.close()
    publisher.publish_frame(_payload(W, H, 0), 0)  # 不抛
    assert publisher.mark_natural_end() is False
    assert publisher.closed
    publisher.unlink()  # 幂等
    assert publisher.closed
    with pytest.raises(ValueError):
        publisher.read_header()  # 句柄已关：读头明确报错


# ---------------------------------------------------------------------------
# BrokerBudget 硬上界
# ---------------------------------------------------------------------------
def test_broker_budget_reserve_reject_and_release():
    budget = BrokerBudget(max_bytes=1000)
    assert budget.reserve(600) is True
    assert budget.total_bytes() == 600
    assert budget.reserve(600) is False  # 600+600 > 1000 → 拒绝
    assert budget.total_bytes() == 600
    budget.release(600)
    assert budget.total_bytes() == 0
    assert budget.reserve(1000) is True  # 释放后可再入
    budget.release(500)
    assert budget.total_bytes() == 500
    budget.release(1000)  # 过量 release 不跌穿 0
    assert budget.total_bytes() == 0
    assert budget.max_bytes() == 1000


# ---------------------------------------------------------------------------
# 终态提交不重复出帧（P3A P0-1：frame_count 帧进度语义）
# ---------------------------------------------------------------------------
def test_terminal_commit_does_not_duplicate_last_frame():
    """自然结束 = 终态提交（seq +2 但 frame_count 不动）→ 消费端收 'end'，
    绝不把终态提交误判为新帧重复投递末帧。"""
    name = _shm_name()
    publisher = BrokerShmSession.create(name, W, H, fps=24.0, total_frames=241)
    try:
        publisher.publish_frame(_payload(W, H, 0), 0)
        reader = BrokerShmSession.attach(name, W, H)
        # 已消费到帧 0（last_src=0 → frame_count=1）
        feed = BrokerFeedSession(reader, {"seq": 2, "last_src": 0})
        try:
            publisher.publish_frame(_payload(W, H, 1), 1)
            kind, data, src = feed.poll()
            assert (kind, src) == ("frame", 1), f"首帧未达: {kind}"
            assert data == _payload(W, H, 1)
            assert publisher.mark_natural_end() is True
            kind, _, _ = feed.poll()
            assert kind == "end", f"终态提交被误判: {kind}"
            # 再 poll 仍稳定收 'end'（无重复帧）
            kind, _, _ = feed.poll()
            assert kind == "end"
        finally:
            feed.close()
            reader.close()
    finally:
        publisher.unlink()


# ---------------------------------------------------------------------------
# 跨进程真并发压力（P3A P0-1：真实多进程读帧，非单次 roundtrip）
# ---------------------------------------------------------------------------
def _spawn_stress_reader(name: str, w: int, h: int, out_q):
    """spawn 子进程：attach 就绪后回报，随后在父进程持续发布的同时并发
    poll 读帧，逐帧校验「内容 == src 专属字节」与「src 单调不后退」——
    撕裂/错位帧必现形。"""
    from pet.decode_broker import BrokerFeedSession, BrokerShmSession
    try:
        reader = BrokerShmSession.attach(name, w, h)
        feed = BrokerFeedSession(reader, {"frame_count": 0, "last_src": -1})
        out_q.put(("ready", []))
        seen = []
        deadline = time.monotonic() + 45.0
        state = "ok"
        while time.monotonic() < deadline:
            kind, data, src = feed.poll()
            if kind == "frame":
                if data != _payload(w, h, src):
                    state = f"content-mismatch src={src}"
                    break
                if seen and src <= seen[-1]:
                    state = f"non-monotonic {seen[-1]} -> {src}"
                    break
                seen.append(src)
            elif kind == "end":
                break
            elif kind == "abort":
                state = "unexpected-abort"
                break
            else:
                time.sleep(0.0005)
        else:
            state = "timeout-no-end"
        feed.close()
        reader.close()
        out_q.put((state, seen))
    except Exception as exc:  # pragma: no cover - 失败取证
        out_q.put(("error", repr(exc)))


def test_cross_process_concurrent_publish_read_stress():
    """真双进程压力：子进程 attach 就绪后，父进程以 ~1ms 节奏发布 300 帧并
    自然结束；子进程并发 poll 校验每帧内容与单调性（奇偶 seqlock 在跨进程
    memcpy 下无撕裂/错位）。ready 握手保证发布与读取真正并发。"""
    ctx = multiprocessing.get_context("spawn")
    name = _shm_name("dsh-broker-stress")
    w, h = 40, 30
    total = 300
    publisher = BrokerShmSession.create(name, w, h, fps=24.0, total_frames=241)
    out_q = ctx.Queue()
    process = ctx.Process(target=_spawn_stress_reader, args=(name, w, h, out_q))
    errors = []
    result = None
    try:
        process.start()
        first = out_q.get(timeout=30)
        assert first[0] == "ready", f"子进程未就绪: {first}"
        for src in range(total):
            publisher.publish_frame(_payload(w, h, src), src)
            time.sleep(0.001)  # ~1ms 节奏：给子进程并发读窗口（非单次 roundtrip）
        publisher.mark_natural_end()
        result = out_q.get(timeout=60)
    except Exception as exc:  # pragma: no cover - 失败取证
        errors.append(repr(exc))
    finally:
        process.join(timeout=15)
        publisher.unlink()
    assert not errors, f"压测驱动异常: {errors}"
    assert result is not None, "子进程未返回结果"
    state, seen = result
    assert state == "ok", f"子进程压测失败: {state}"
    assert len(seen) >= total // 2, f"并发读帧过少: {len(seen)}/{total}"
    assert seen[-1] == total - 1, f"末帧未送达: {seen[-1]} != {total - 1}"
    assert seen == sorted(seen), "src 回退"


# ---------------------------------------------------------------------------
# attach 失败路径统一关闭临时句柄（P3A P1-4）
# ---------------------------------------------------------------------------
def _spy_shm_handles(monkeypatch):
    """包装 multiprocessing.shared_memory.SharedMemory：记录 open/close。"""
    import multiprocessing.shared_memory as mpsm
    real_cls = mpsm.SharedMemory
    opened: list = []

    class _Spy(real_cls):  # type: ignore[misc]
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._spy_closed = False
            opened.append(self)

        def close(self):  # noqa: D401 - 记录 close 调用
            self._spy_closed = True
            return super().close()

    monkeypatch.setattr(mpsm, "SharedMemory", _Spy)
    return opened


def test_attach_geometry_mismatch_closes_temporary_handle(monkeypatch):
    name = _shm_name()
    publisher = BrokerShmSession.create(name, W, H, fps=24.0, total_frames=241)
    try:
        opened = _spy_shm_handles(monkeypatch)
        with pytest.raises(ValueError):
            BrokerShmSession.attach(name, W + 1, H)
        assert len(opened) == 1 and opened[0]._spy_closed, \
            "attach 校验失败必须 close 临时句柄（P3A P1-4）"
    finally:
        publisher.unlink()


def test_attach_rejects_undersized_mapping_and_closes(monkeypatch):
    """mapping 实际大小容不下 header 声称的几何/槽数（畸形对象）→ 拒绝并关闭。"""
    import multiprocessing.shared_memory as shared_memory
    name = _shm_name("dsh-undersized")
    # 只够 1 槽的 mapping，却声称 4 槽（header 看似合法）
    tiny = HEADER_SIZE + frame_bytes(W, H, BPP)
    shm = shared_memory.SharedMemory(create=True, name=name, size=tiny)
    try:
        shm.buf[0:HEADER_SIZE] = SessionHeader(
            frame_w=W, frame_h=H, slot_count=SLOT_COUNT_DEFAULT,
            total_frames=241, flags=FLAG_SESSION_ACTIVE,
        ).pack()
        opened = _spy_shm_handles(monkeypatch)
        with pytest.raises(ValueError):
            BrokerShmSession.attach(name, W, H)
        assert len(opened) == 1 and opened[0]._spy_closed, \
            "尺寸不足 attach 必须拒绝并 close 临时句柄"
    finally:
        shm.close()
        shm.unlink()


def test_attach_epoch_mismatch_closes_temporary_handle(monkeypatch):
    name = _shm_name()
    publisher = BrokerShmSession.create(name, W, H, fps=24.0, total_frames=241,
                                        epoch="0123456789abcdef")
    try:
        opened = _spy_shm_handles(monkeypatch)
        with pytest.raises(ValueError):
            BrokerShmSession.attach(name, W, H, epoch="ffffffffffffffff")
        assert len(opened) == 1 and opened[0]._spy_closed, \
            "epoch 错配 attach 必须 close 临时句柄"
    finally:
        publisher.unlink()


# ---------------------------------------------------------------------------
# 真跨进程压测的补充：attach 成功路径不误关（close 语义保持）
# ---------------------------------------------------------------------------
def test_attach_success_keeps_handle_open():
    name = _shm_name()
    publisher = BrokerShmSession.create(name, W, H, fps=24.0, total_frames=241)
    try:
        reader = BrokerShmSession.attach(name, W, H)
        assert not reader.closed
        reader.read_header()  # 可读
        reader.close()
        assert reader.closed
    finally:
        publisher.unlink()



def _spawn_child_attach(name: str, w: int, h: int, srcs: list, out_q) -> None:
    """在 spawn 子进程内 attach 并读帧；结果经队列回传（纯 Python，无 Qt）。"""
    from pet.decode_broker import BrokerShmSession
    try:
        session = BrokerShmSession.attach(name, w, h)
        header = session.read_header()
        info = {
            "seq": int(header.seq),
            "frame_count": int(header.frame_count),
            "last_src": int(header.last_src),
            "last_slot": int(header.last_slot),
            "pub_pid": int(header.pub_pid),
            "epoch": int(header.epoch),
            "frames": {},
        }
        for src in srcs:
            data = session.read_frame(src)
            info["frames"][int(src)] = None if data is None else data.hex()
        session.close()
        out_q.put(("ok", info))
    except Exception as exc:  # pragma: no cover - 失败取证
        out_q.put(("error", repr(exc)))


def test_cross_process_spawn_child_attaches_and_reads_frames():
    """spawn 子进程（独立解释器）attach 父进程创建的共享内存并逐槽读帧。

    K=4 ring：父进程只写 4 帧（每槽一帧）避免子进程读取前被覆盖。
    """
    ctx = multiprocessing.get_context("spawn")
    name = _shm_name("dsh-broker-spawn")
    w, h = 32, 24
    publisher = BrokerShmSession.create(name, w, h, fps=24.0, total_frames=241)
    payloads = {src: _payload(w, h, src) for src in range(4)}
    for src in range(4):
        publisher.publish_frame(payloads[src], src)
    out_q = ctx.Queue()
    process = ctx.Process(target=_spawn_child_attach,
                          args=(name, w, h, list(range(4)), out_q))
    try:
        process.start()
        result = out_q.get(timeout=30)
    finally:
        process.join(timeout=10)
        publisher.unlink()
    assert result[0] == "ok", f"子进程失败: {result}"
    info = result[1]
    assert info["frame_count"] == 4
    assert info["seq"] == 8  # 奇偶提交序号（偶数 = 已提交）
    assert info["last_src"] == 3
    assert info["pub_pid"] == os.getpid()  # 跨进程读到的发布者 pid = 父进程
    assert info["epoch"] != 0
    for src in range(4):
        assert info["frames"][src] == payloads[src].hex(), f"帧 {src} 内容错"
