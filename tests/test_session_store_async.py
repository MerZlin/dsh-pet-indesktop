# -*- coding: utf-8 -*-
"""B8 会话异步写盘回归测试。

同步点一律用 flush()/close()（确定性），不用 sleep 猜时序。
每个用例结束 close_all_writers() 清注册表，避免测试间共享 worker。
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from pet.chat import session_store as ss
from pet.chat.models import ChatMessage


@pytest.fixture()
def store(tmp_path):
    yield ss.SessionStore(tmp_path), tmp_path
    ss.close_all_writers()


def _make_session(store, sid="s1", text="你好"):
    session = store.create("shenshen", "p1", "sys")
    session.session_id = sid
    session.messages.append(ChatMessage("user", text))
    return session


def test_save_flush_load_roundtrip(store):
    st, tmp = store
    session = _make_session(st)
    assert st.save(session) is True
    assert st.flush() is True
    path = tmp / "sessions" / "shenshen" / "s1.json"
    assert path.is_file()
    loaded = st.load("s1", "shenshen")
    assert loaded is not None and loaded.session_id == "s1"


def test_read_through_pending_without_flush(store):
    """保存后不 flush 也能 load 到（read-your-writes，对齐旧同步语义）。"""
    st, _ = store
    session = _make_session(st)
    assert st.save(session) is True
    loaded = st.load("s1", "shenshen")
    assert loaded is not None and loaded.session_id == "s1"


def test_coalescing_keeps_latest_snapshot(store):
    """同会话连续保存只落最新快照（流式 delta 合并）。"""
    st, tmp = store
    session = _make_session(st)
    st.save(session)
    session.messages.append(ChatMessage("assistant", "旧"))
    st.save(session)
    session.messages.append(ChatMessage("assistant", "新"))
    st.save(session)
    assert st.flush() is True
    raw = json.loads((tmp / "sessions" / "shenshen" / "s1.json").read_text("utf-8"))
    texts = [m.get("content") for m in raw.get("messages", [])]
    assert "新" in texts


def test_delete_then_save_and_save_then_delete(store):
    st, tmp = store
    path = tmp / "sessions" / "shenshen" / "s1.json"
    session = _make_session(st)
    st.save(session)
    assert st.flush() is True
    assert path.is_file()
    assert st.delete(session) is True
    assert st.flush() is True
    assert not path.exists()
    assert st.load("s1", "shenshen") is None
    # 删除后同 key 再保存：最终状态是存在
    st.save(session)
    assert st.flush() is True
    assert path.is_file()


def test_pending_delete_hides_from_load_and_list(store):
    st, _ = store
    session = _make_session(st)
    st.save(session)
    assert st.flush() is True
    st.delete(session)
    # 未 flush：读穿 pending，删除已生效
    assert st.load("s1", "shenshen") is None
    assert all(x.session_id != "s1" for x in st.list("shenshen"))


def test_pending_save_shows_in_list(store):
    st, _ = store
    session = _make_session(st)
    st.save(session)
    ids = [x.session_id for x in st.list("shenshen")]
    assert "s1" in ids


def test_flush_reports_write_failure_once(store):
    """写失败必须让 flush 返回 False，且失败被上报一次后清零。"""
    st, tmp = store
    blocker = tmp / "sessions" / "shenshen" / "s1.json"
    blocker.mkdir(parents=True)  # 目标路径是目录 → os.replace 必失败
    session = _make_session(st)
    assert st.save(session) is True
    assert st.flush() is False   # 诚实：没写上就是 False
    assert st.flush() is True    # 失败已上报过一次，队列已空
    blocker.rmdir()


def test_close_sticky_and_rejects_late_save(store):
    st, tmp = store
    blocker = tmp / "sessions" / "shenshen" / "s1.json"
    blocker.mkdir(parents=True)
    session = _make_session(st)
    st.save(session)
    assert ss.close_all_writers() is False  # 第一次 close 吃到失败
    blocker.rmdir()
    # close 结果粘滞：同一批 writer 再 close 不会反转成 True
    # （close_all 已清注册表，这里直接验单 writer 的粘滞）
    st2 = ss.SessionStore(tmp)
    session2 = _make_session(st2, sid="s2")
    assert st2.save(session2) is True
    w = ss._registry.get_writer(st2.root)
    assert w.close() is True
    assert w.close() is True          # 幂等
    assert st2.save(session2) is False  # 关闭后拒绝可观测


def test_close_all_then_fresh_writer_works(store):
    st, tmp = store
    session = _make_session(st)
    st.save(session)
    assert ss.close_all_writers() is True
    st2 = ss.SessionStore(tmp)
    session2 = _make_session(st2, sid="s2")
    assert st2.save(session2) is True
    assert st2.flush() is True
    assert (tmp / "sessions" / "shenshen" / "s2.json").is_file()


def test_corrupt_file_still_quarantined(store):
    """既有的损坏文件隔离行为不回归。"""
    st, tmp = store
    bad = tmp / "sessions" / "shenshen"
    bad.mkdir(parents=True)
    (bad / "bad.json").write_text("{oops", encoding="utf-8")
    assert st.load("bad", "shenshen") is None
    assert list(bad.glob("*.corrupt-*.json"))


def test_close_drains_accepted_ops_before_shutdown(store):
    """close 屏障：先关入口再排空——已接受的提交必须全部落盘。"""
    st, tmp = store
    session = _make_session(st)
    assert st.save(session) is True
    assert ss.close_all_writers() is True
    assert (tmp / "sessions" / "shenshen" / "s1.json").is_file()


def test_close_total_blocking_time_bounded_by_timeout(tmp_path, monkeypatch):
    """P2：close(timeout) 的实际上界必须等于 timeout——flush 耗尽 deadline 后
    join 不得再额外阻塞（旧实现固定 join(5.0)，close(0.3) 实际约 5.3s；
    close(10) 约 15s，close_all 多 root 逐个关闭还会继续累加）。"""
    st = ss.SessionStore(tmp_path)
    session = _make_session(st, sid="s-block")
    assert st.save(session) is True
    w = ss._registry.get_writer(st.root)
    assert w is not None

    entered = threading.Event()
    release = threading.Event()
    orig_write = ss._atomic_write

    def stuck_write(path, payload):
        entered.set()
        assert release.wait(timeout=10.0), "测试释放信号未到达"
        return orig_write(path, payload)

    monkeypatch.setattr(ss, "_atomic_write", stuck_write)

    start = time.monotonic()
    ok = w.close(timeout=0.3)
    elapsed = time.monotonic() - start
    assert ok is False, "worker 未在 deadline 内落盘，close 必须诚实返回 False"
    assert elapsed < 1.5, f"close(0.3) 实际阻塞 {elapsed:.2f}s（join 必须计入同一 deadline）"

    # 放行卡住的写盘，让 worker 正常收尾（避免泄漏/teardown 竞态）
    release.set()
    w._thread.join(timeout=5.0)
    assert not w._thread.is_alive()
    # 粘滞语义不变：已关闭的 writer 再 close 返回同一结果、不再阻塞
    start = time.monotonic()
    assert w.close(timeout=0.3) is False
    assert time.monotonic() - start < 0.5


def test_permanent_shutdown_rejects_new_writers(store):
    """permanent 关闭后不再创建新 writer，save 被拒绝且可观测。"""
    st, _ = store
    session = _make_session(st)
    st.save(session)
    assert ss.close_all_writers(permanent=True) is True
    try:
        st2 = ss.SessionStore(_)
        assert st2.save(_make_session(st2, sid="s9")) is False
        assert ss._registry.get_writer(st2.root) is None  # 没有偷偷重建 writer
    finally:
        ss.reset_writers_for_tests()  # 复位全局屏障，不影响后续测试


def test_concurrent_submit_during_close(store):
    """close 进行中迟到的提交被拒绝而不是进入双 writer。"""
    st, _ = store
    session = _make_session(st)
    st.save(session)
    w = ss._registry.get_writer(st.root)
    assert w is not None
    with w._cond:
        w._closing = True  # 模拟关闭屏障已落下
    assert st.save(session) is False


def test_close_all_rejects_new_writers_during_close_window(store):
    """全审 P3-5：close_all 逐个关闭旧 writer 的窗口期内，save 不得新建
    同 root writer（双 writer 竞态）——「关闭中」标志下被拒绝且可观测；
    close 完成后标志复位，后续提交自然重建新 writer。"""
    st, tmp = store
    session = _make_session(st)
    assert st.save(session) is True
    assert ss._registry.get_writer(st.root) is not None
    # 模拟 close_all 已清表、正在逐个 close 旧 writer 的中间窗口态：
    # 锁内 _closing 已置位 + 注册表已清空（等价 close_all 的临界状态）。
    with ss._registry._lock:
        ss._registry._closing = True
        ss._registry._writers.clear()
    try:
        assert st.save(session) is False                       # 拒绝可观测
        assert ss._registry.get_writer(st.root) is None        # 没有偷偷重建
        assert st.flush() is True                              # 无 writer 时 flush 无事可等
    finally:
        with ss._registry._lock:
            ss._registry._closing = False
    # 标志复位（close 完成）后：同 root 可正常重建新 writer
    assert st.save(session) is True
    assert ss._registry.get_writer(st.root) is not None
    assert ss.close_all_writers() is True
    assert (tmp / "sessions" / "shenshen" / "s1.json").is_file()


def test_concurrent_save_during_close_all_is_rejected(store, monkeypatch):
    """全审 P3-5 真实线程竞争：close_all 进行中（旧 writer 未关完）的并发
    save 被拒绝，注册表不重建 writer；close 完成后可正常重建。"""
    st, tmp = store
    session = _make_session(st)
    assert st.save(session) is True
    w = ss._registry.get_writer(st.root)
    assert w is not None

    entered = threading.Event()
    release = threading.Event()
    orig_close = ss._AsyncWriter.close

    def slow_close(self, timeout=10.0):
        entered.set()
        assert release.wait(timeout=5.0), "测试释放信号未到达"
        return orig_close(self, timeout=timeout)

    monkeypatch.setattr(ss._AsyncWriter, "close", slow_close)

    results = {}

    def closer():
        results["close"] = ss.close_all_writers()

    t = threading.Thread(target=closer, name="test-close-all")
    t.start()
    assert entered.wait(timeout=5.0), "close_all 未进入旧 writer 关闭循环"
    # 此刻 close_all 已清表并置 _closing，正在等旧 writer 关完：
    # 并发 save 必须被拒绝，且不得新建同 root writer。
    assert st.save(session) is False
    assert ss._registry.get_writer(st.root) is None
    release.set()
    t.join(timeout=5.0)
    assert not t.is_alive()
    assert results["close"] is True
    # 关闭完成后（标志复位）：重建新 writer 正常工作
    assert st.save(session) is True
    assert st.flush() is True
    assert (tmp / "sessions" / "shenshen" / "s1.json").is_file()


def test_overlapping_close_all_keeps_barrier_until_last_closer_finishes(store, monkeypatch):
    """B9 复审 P2：两个 close_all 重叠时，先进入的 close_all 仍在锁外逐个
    关闭旧 writer，后进入的 close_all 不得提前复位「关闭中」屏障——否则
    writer_for 会在旧 writer 未关完时新建同 root writer（双 writer 竞态，
    正是全审 P3-5 要封的窗口）。屏障必须保持到最后一个关闭者完成。"""
    st, tmp = store
    session = _make_session(st)
    assert st.save(session) is True
    w = ss._registry.get_writer(st.root)
    assert w is not None

    entered = threading.Event()
    release = threading.Event()
    orig_close = ss._AsyncWriter.close

    def slow_close(self, timeout=10.0):
        entered.set()
        assert release.wait(timeout=5.0), "测试释放信号未到达"
        return orig_close(self, timeout=timeout)

    monkeypatch.setattr(ss._AsyncWriter, "close", slow_close)

    results = {}

    def closer(tag):
        results[tag] = ss.close_all_writers()

    t1 = threading.Thread(target=closer, args=("c1",), name="test-close-all-1")
    t1.start()
    assert entered.wait(timeout=5.0), "第一个 close_all 未进入关闭循环"
    # 第二个 close_all 重叠进入：注册表已被第一个清空，它很快返回；
    # 但绝不能复位「关闭中」屏障（第一个仍在锁外关闭旧 writer）。
    t2 = threading.Thread(target=closer, args=("c2",), name="test-close-all-2")
    t2.start()
    t2.join(timeout=5.0)
    assert not t2.is_alive()
    # 第一个 close_all 仍未完成：屏障必须依然生效
    assert st.save(session) is False
    assert ss._registry.get_writer(st.root) is None
    assert ss._registry._closing is True
    release.set()
    t1.join(timeout=5.0)
    assert not t1.is_alive()
    assert results == {"c1": True, "c2": True}
    # 全部关闭完成后屏障复位：同 root 可正常重建新 writer
    assert st.save(session) is True
    assert st.flush() is True
    assert (tmp / "sessions" / "shenshen" / "s1.json").is_file()


def test_permanent_close_concurrent_with_reset_keeps_barrier(store, monkeypatch):
    """B9 R2 P1：`reset_for_tests()` 与 permanent close 并发交错时，永久关闭
    屏障（应用退出路径）权威——测试重置不得把 `_shutdown` 清掉。

    竞态窗口：permanent close 线程先把 `_shutdown` 置 True 并在锁外逐个
    关闭旧 writer；若此时 reset 的 finally 无条件 `_shutdown = False`，
    即使永久关闭已经完成，writer_for 也会重新允许创建 writer。"""
    st, tmp = store
    session = _make_session(st)
    assert st.save(session) is True
    w = ss._registry.get_writer(st.root)
    assert w is not None

    entered = threading.Event()
    release = threading.Event()
    orig_close = ss._AsyncWriter.close

    def slow_close(self, timeout=10.0):
        entered.set()
        assert release.wait(timeout=5.0), "测试释放信号未到达"
        return orig_close(self, timeout=timeout)

    monkeypatch.setattr(ss._AsyncWriter, "close", slow_close)

    results = {}

    def closer():
        results["close"] = ss.close_all_writers(permanent=True)

    t = threading.Thread(target=closer, name="test-perm-close")
    t.start()
    assert entered.wait(timeout=5.0), "permanent close 未进入旧 writer 关闭循环"
    # permanent close 已置位 _shutdown 并在锁外关闭旧 writer：
    # 并发 reset 不得清掉退出屏障（即使 permanent close 随后完成）。
    ss.reset_writers_for_tests()
    release.set()
    t.join(timeout=5.0)
    assert not t.is_alive()
    assert results["close"] is True
    # 权威规则：与 permanent close 交错的 reset 保留屏障
    assert ss._registry._shutdown is True
    assert ss._registry.get_writer(st.root) is None
    assert st.save(session) is False
    # 顺序复位（测试隔离契约，无并发永久关闭在飞）：显式再 reset 可恢复
    ss.reset_writers_for_tests()
    assert ss._registry._shutdown is False
    assert st.save(session) is True
    assert st.flush() is True
    assert (tmp / "sessions" / "shenshen" / "s1.json").is_file()


def test_permanent_close_started_during_reset_keeps_barrier(store, monkeypatch):
    """B9 R2 P1 变体：reset 进行中（正关闭旧 writer）才发起 permanent close——
    reset 结束时的清理不得清掉随后置位的 `_shutdown`（代次已前进）。"""
    st, tmp = store
    session = _make_session(st)
    assert st.save(session) is True
    w = ss._registry.get_writer(st.root)
    assert w is not None

    entered = threading.Event()
    release = threading.Event()
    orig_close = ss._AsyncWriter.close

    def slow_close(self, timeout=10.0):
        entered.set()
        assert release.wait(timeout=5.0), "测试释放信号未到达"
        return orig_close(self, timeout=timeout)

    monkeypatch.setattr(ss._AsyncWriter, "close", slow_close)

    def resetter():
        ss.reset_writers_for_tests()

    t = threading.Thread(target=resetter, name="test-reset")
    t.start()
    assert entered.wait(timeout=5.0), "reset 未进入旧 writer 关闭循环"
    # reset 在飞（正关闭旧 writer）：此刻发起 permanent close
    assert ss.close_all_writers(permanent=True) is True
    release.set()
    t.join(timeout=5.0)
    assert not t.is_alive()
    # reset 期间发起了 permanent close：屏障必须保留
    assert ss._registry._shutdown is True
    assert ss._registry.get_writer(st.root) is None
    assert st.save(session) is False
    # 顺序复位（测试隔离契约）：显式再 reset 可恢复
    ss.reset_writers_for_tests()
    assert ss._registry._shutdown is False
    assert st.save(session) is True
    assert st.flush() is True
    assert (tmp / "sessions" / "shenshen" / "s1.json").is_file()
