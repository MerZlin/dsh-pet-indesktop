# -*- coding: utf-8 -*-
"""B8：会话保存异步化 + debounce 的行为测试。

覆盖：
- 串行 I/O worker：save() 只入队（不阻塞 GUI），worker 串行落盘；
- 同一会话（同一路径）队列合并：只保留最新快照；
- 流式结束后只保存一次（begin 不保存，finish 保存整个交换）；
- 退出/停止/失败/关窗强制 flush；
- 历史上限：单会话消息数、单条消息字符数、单文件大小、列表加载数量；
- 崩溃安全：tmp+fsync+os.replace 原子替换、无 tmp 残留；
- worker 异常可观测（failure_count/last_error/日志）且不丢内存快照；
- 多实例隔离 / 同进程多 store 共享队列（双窗口写同一会话不互相覆盖）。

所有线程同步使用 Event / flush()（排空屏障），不用 sleep 猜时序。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from pet.chat.models import ChatMessage
from pet.chat.session_store import (
    MAX_MESSAGE_CHARS,
    MAX_MESSAGES_PER_SESSION,
    MAX_SESSION_FILE_BYTES,
    MAX_SESSION_LIST,
    SessionStore,
    _SessionWriter,
)


def _store(tmp_path: Path, writer: _SessionWriter | None = None, instance_id: str = ""):
    return SessionStore(tmp_path, instance_id=instance_id, writer=writer)


def _session(store: SessionStore, character: str = "cat"):
    return store.create(character, "provider", "system")


# ---------------------------------------------------------------- worker 队列合并

def test_save_returns_immediately_while_worker_stalled(tmp_path: Path):
    """save() 不得等待 worker：worker 被阻塞时 save() 立即返回，随后 flush 落盘。"""
    writer = _SessionWriter()
    store = _store(tmp_path, writer=writer)
    session = _session(store)
    session.messages.append(ChatMessage("user", "async"))

    started = threading.Event()
    release = threading.Event()
    real = writer.atomic_write

    def stalled(path, snapshot):
        started.set()
        assert release.wait(5)
        return real(path, snapshot)

    writer.atomic_write = stalled

    t0 = time.monotonic()
    store.save(session)
    assert time.monotonic() - t0 < 2.0, "save() 不应等待 worker 落盘"
    assert started.wait(2), "worker 已开始写（save 已入队）"
    assert not (store._path("cat", session.session_id)).exists()  # 尚未落盘

    release.set()
    assert writer.flush()
    assert store.load(session.session_id, "cat").messages[-1].content == "async"
    writer.close()


def test_rapid_queued_saves_merge_into_single_write_per_session(tmp_path: Path):
    """同一会话连续 save 多次：队列只保留最新快照，最终一次写盘。"""
    writer = _SessionWriter()
    store = _store(tmp_path, writer=writer)
    session = _session(store)
    session.messages.append(ChatMessage("user", "one"))

    started = threading.Event()
    release = threading.Event()
    real = writer.atomic_write
    calls: list[Path] = []

    def stalled(path, snapshot):
        calls.append(path)
        started.set()
        assert release.wait(5)
        return real(path, snapshot)

    writer.atomic_write = stalled

    store.save(session)  # v1：worker 拿起并阻塞在写
    assert started.wait(2), "worker 未开始写 v1"
    session.messages.append(ChatMessage("assistant", "two"))
    store.save(session)  # v2 入队
    session.messages.append(ChatMessage("user", "three"))
    store.save(session)  # v3 入队，覆盖 v2（合并）

    release.set()
    assert writer.flush()
    assert len(calls) == 2, f"应只写 v1 + 合并后的最终快照，实际 {len(calls)} 次写"

    loaded = store.load(session.session_id, "cat")
    assert [m.content for m in loaded.messages] == ["one", "two", "three"]
    writer.close()


def test_load_reads_queued_snapshot_while_write_in_flight(tmp_path: Path):
    """写仍在进行时 load 读到最新快照（读穿透），不阻塞、不读到旧磁盘内容。"""
    writer = _SessionWriter()
    store = _store(tmp_path, writer=writer)
    session = _session(store)
    session.messages.append(ChatMessage("user", "first"))

    started = threading.Event()
    release = threading.Event()
    real = writer.atomic_write

    def stalled(path, snapshot):
        started.set()
        assert release.wait(5)
        return real(path, snapshot)

    writer.atomic_write = stalled

    store.save(session)  # v1 被 worker 拿起、写被阻塞
    assert started.wait(2)
    session.messages.append(ChatMessage("assistant", "second"))
    store.save(session)  # v2 在 v1 在飞期间入队

    loaded = store.load(session.session_id, "cat")  # 必须不阻塞且读到 v2
    assert [m.content for m in loaded.messages] == ["first", "second"]

    release.set()
    assert writer.flush()
    final = store.load(session.session_id, "cat")
    assert final.messages[-1].content == "second"
    writer.close()


def test_delete_cancels_pending_save_and_remove_wins_then_save_wins(tmp_path: Path):
    """delete 覆盖未落盘的 save；删除后再 save 则 save 胜出（按路径最新操作）。"""
    writer = _SessionWriter()
    store = _store(tmp_path, writer=writer)
    session = _session(store)
    session.messages.append(ChatMessage("user", "hi"))
    path = store._path("cat", session.session_id)

    store.save(session)
    store.delete(session)
    # 保存仍在队列时即视为已删除（读穿透）
    assert store.load(session.session_id, "cat") is None
    assert writer.flush()
    assert not path.exists()

    # 删除之后再保存：最新操作是 save → 文件恢复
    session.messages.append(ChatMessage("user", "again"))
    store.save(session)
    assert writer.flush()
    assert path.exists()
    assert store.load(session.session_id, "cat").messages[-1].content == "again"
    writer.close()


# ---------------------------------------------------------------- 流式结束只保存一次

def test_stream_finish_persists_exchange_once(tmp_path: Path):
    """流式结束（_complete_finished）保存整个交换一次；begin 阶段不落盘。"""
    from PySide6.QtWidgets import QApplication

    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    sid = window.session.session_id

    # begin：只改内存 + 不保存 → flush 后磁盘仍是空会话
    window.session.messages.append(ChatMessage("user", "提问"))
    assert window.store.flush()
    before = window.store.load(sid, "shenshen")
    assert before is not None and len(before.messages) == 0

    # finish：一次 save 持久化整个交换
    window._complete_finished("回答")
    assert window.store.flush()
    after = window.store.load(sid, "shenshen")
    assert [m.content for m in after.messages] == ["提问", "回答"]
    window.close()
    app.processEvents()


def test_stop_and_error_force_flush_of_pending_state(tmp_path: Path):
    """停止 / 失败时强制 flush：内存中的提问立即落盘。"""
    from PySide6.QtWidgets import QApplication

    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")

    window.session.messages.append(ChatMessage("user", "被停止的提问"))
    window._active_request_id = "r"
    window._stopped("r")
    assert window.store.flush()
    stopped = window.store.load(window.session.session_id, "shenshen")
    assert [m.content for m in stopped.messages] == ["被停止的提问"]

    window._active_request_id = "r2"
    window.session.messages.append(ChatMessage("user", "失败的提问"))
    window._error("r2", "连接失败")
    assert window.store.flush()
    failed = window.store.load(window.session.session_id, "shenshen")
    assert [m.content for m in failed.messages] == ["被停止的提问", "失败的提问"]
    window.close()
    app.processEvents()


def test_close_event_flushes_pending_saves(tmp_path: Path):
    """关闭窗口时强制 flush：排队中的保存立即落盘。"""
    from PySide6.QtWidgets import QApplication

    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    sid = window.session.session_id
    window.session.messages.append(ChatMessage("user", "关闭前的内容"))
    window.store.save(window.session)  # 模拟排队中的保存
    window.close()  # closeEvent → flush
    reloaded = window.store.load(sid, "shenshen")
    assert reloaded is not None and reloaded.messages[-1].content == "关闭前的内容"
    app.processEvents()


def test_session_and_character_switch_persist_old_session(tmp_path: Path):
    """新建/切换会话、切换角色时，旧会话的已输入内容必须先落盘。"""
    from PySide6.QtWidgets import QApplication

    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")

    a_id = window.session.session_id
    window.session.messages.append(ChatMessage("user", "给角色A"))
    window.new_session()  # 应保存旧会话
    assert window.store.flush()
    old = window.store.load(a_id, "shenshen")
    assert old is not None and old.messages[-1].content == "给角色A"

    b_id = window.session.session_id
    window.session.messages.append(ChatMessage("user", "角色A新会话"))
    window.switch_character("another")
    assert window.store.flush()
    kept = window.store.load(b_id, "shenshen")
    assert kept is not None and kept.messages[-1].content == "角色A新会话"

    c_id = window.session.session_id
    window.session.messages.append(ChatMessage("user", "给角色B"))
    window.store.save(window.session)
    assert window.store.flush()
    b_saved = window.store.load(c_id, "another")
    assert b_saved is not None and b_saved.messages[-1].content == "给角色B"
    window.close()
    app.processEvents()


# ---------------------------------------------------------------- 历史上限

def test_message_count_cap_trims_oldest_on_save(tmp_path: Path, monkeypatch):
    from pet.chat import session_store as ss

    monkeypatch.setattr(ss, "MAX_MESSAGES_PER_SESSION", 5)
    writer = _SessionWriter()
    store = _store(tmp_path, writer=writer)
    session = _session(store)
    for i in range(8):
        session.messages.append(ChatMessage("user" if i % 2 == 0 else "assistant", f"msg-{i}"))
    store.save(session)
    assert writer.flush()
    loaded = store.load(session.session_id, "cat")
    assert len(loaded.messages) == 5
    assert loaded.messages[0].content == "msg-3"  # 最旧 3 条被裁剪
    assert session.messages[0].content == "msg-3"  # 内存与磁盘保持一致
    writer.close()


def test_message_char_cap_truncates_long_content(tmp_path: Path, monkeypatch):
    from pet.chat import session_store as ss

    monkeypatch.setattr(ss, "MAX_MESSAGE_CHARS", 10)
    writer = _SessionWriter()
    store = _store(tmp_path, writer=writer)
    session = _session(store)
    session.messages.append(ChatMessage("user", "x" * 100))
    store.save(session)
    assert writer.flush()
    loaded = store.load(session.session_id, "cat")
    assert loaded.messages[0].content == "x" * 10
    assert session.messages[0].content == "x" * 10
    writer.close()


def test_file_size_cap_trims_on_save_and_refuses_oversized_load(tmp_path: Path, monkeypatch):
    from pet.chat import session_store as ss

    monkeypatch.setattr(ss, "MAX_SESSION_FILE_BYTES", 512)
    writer = _SessionWriter()
    store = _store(tmp_path, writer=writer)
    session = _session(store)
    for i in range(60):
        session.messages.append(ChatMessage("user", f"很长很长很长很长很长很长很长很长很长很长很长很长很长-{i}" * 2))
    store.save(session)
    assert writer.flush()
    path = store._path("cat", session.session_id)
    assert path.stat().st_size <= 512
    assert store.load(session.session_id, "cat") is not None

    # 外部写入的超限文件：load 拒绝解析但保留原文件（不误判为损坏）
    huge = store._path("cat", "huge")
    huge.write_text("x" * 1024, encoding="utf-8")
    assert store.load("huge", "cat") is None
    assert huge.exists()
    assert list(huge.parent.glob("huge.corrupt-*.json")) == []
    writer.close()


def test_list_load_cap_keeps_recent_and_pinned(tmp_path: Path, monkeypatch):
    from pet.chat import session_store as ss

    monkeypatch.setattr(ss, "MAX_SESSION_LIST", 3)
    writer = _SessionWriter()
    store = _store(tmp_path, writer=writer)
    sessions = []
    for i in range(5):
        session = _session(store)
        session.messages.append(ChatMessage("user", f"session-{i}"))
        store.save(session)
        sessions.append(session)
    assert writer.flush()

    listed = store.list("cat")
    assert len(listed) == 3

    # 置顶的旧会话不被最新 3 条挤出
    sessions[0].pinned = True
    store.save(sessions[0])
    assert writer.flush()
    listed = store.list("cat")
    assert len(listed) == 3
    assert any(s.session_id == sessions[0].session_id for s in listed)
    writer.close()


def test_cap_defaults_anchor_existing_magnitudes():
    """上限数值锚定现有代码量级（prompt 上下文 40 条 / 24000 字符，附件单条 200_000 字符）。"""
    assert MAX_MESSAGES_PER_SESSION >= 40
    assert MAX_MESSAGE_CHARS >= 200_000
    assert MAX_SESSION_FILE_BYTES >= 1 * 1024 * 1024
    assert MAX_SESSION_LIST >= 50


# ---------------------------------------------------------------- 崩溃安全 / 原子替换

def test_atomic_write_leaves_no_temp_and_is_valid_json(tmp_path: Path):
    writer = _SessionWriter()
    store = _store(tmp_path, writer=writer)
    session = _session(store)
    session.messages.append(ChatMessage("user", "原子写"))
    store.save(session)
    assert writer.flush()
    path = store._path("cat", session.session_id)
    assert path.exists()
    assert list(path.parent.glob("*.tmp")) == []
    assert list(path.parent.glob(".*.tmp")) == []
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["messages"][0]["content"] == "原子写"
    writer.close()


def test_worker_failure_is_observed_and_snapshot_retained(tmp_path: Path):
    """写失败：日志可观测（failure_count/last_error），内存保留最新快照，不产生半成品文件。"""
    writer = _SessionWriter()
    store = _store(tmp_path, writer=writer)
    session = _session(store)
    session.messages.append(ChatMessage("user", "重要内容"))

    def failing(path, snapshot):
        raise OSError("injected disk failure")

    writer.atomic_write = failing
    store.save(session)
    assert writer.flush()  # 队列排空（失败不阻塞 flush）
    assert writer.failure_count >= 1
    assert writer.last_error is not None
    assert writer.failed_count == 1
    assert not store._path("cat", session.session_id).exists()

    # 恢复后再次保存：自动重试成功，快照落盘
    import pet.chat.session_store as ss

    writer.atomic_write = ss._atomic_write
    store.save(session)
    assert writer.flush()
    assert writer.failed_count == 0
    assert store.load(session.session_id, "cat").messages[-1].content == "重要内容"
    writer.close()


def test_worker_failure_retries_once_then_recovers(tmp_path: Path):
    """瞬时失败自动重试一次并成功：最终数据完整，失败可观测。"""
    writer = _SessionWriter()
    store = _store(tmp_path, writer=writer)
    session = _session(store)
    session.messages.append(ChatMessage("user", "重试后落盘"))
    real = writer.atomic_write
    calls = []

    def flaky(path, snapshot):
        calls.append(path)
        if len(calls) == 1:
            raise OSError("transient failure")
        return real(path, snapshot)

    writer.atomic_write = flaky
    store.save(session)
    assert writer.flush()
    assert len(calls) == 2, f"首写失败 + 自动重试成功，实际 {len(calls)} 次写"
    assert writer.failure_count == 1
    assert writer.failed_count == 0
    assert store.load(session.session_id, "cat").messages[-1].content == "重试后落盘"
    writer.close()


def test_flush_retries_failed_snapshots(tmp_path: Path):
    """失败快照保留在内存；下次 flush() 重试，恢复后落盘。"""
    writer = _SessionWriter()
    store = _store(tmp_path, writer=writer)
    session = _session(store)
    session.messages.append(ChatMessage("user", "flush 重试"))
    real = writer.atomic_write
    state = {"fail": True}

    def conditional(path, snapshot):
        if state["fail"]:
            raise OSError("disk down")
        return real(path, snapshot)

    writer.atomic_write = conditional
    store.save(session)
    assert writer.flush()
    assert writer.failed_count == 1
    state["fail"] = False
    assert writer.flush()  # flush 重试 failed 快照
    assert writer.failed_count == 0
    assert store.load(session.session_id, "cat").messages[-1].content == "flush 重试"
    writer.close()


def test_close_flushes_pending_ops(tmp_path: Path):
    """close()（退出路径）排空队列：未落盘操作全部写盘。"""
    writer = _SessionWriter()
    store = _store(tmp_path, writer=writer)
    session = _session(store)
    session.messages.append(ChatMessage("user", "close 前"))
    store.save(session)
    writer.close()
    path = store._path("cat", session.session_id)
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["messages"][0]["content"] == "close 前"


def test_save_after_close_is_safe_noop(tmp_path: Path):
    """writer 关闭后的 save 不抛异常（shutdown 竞态安全）。"""
    writer = _SessionWriter()
    store = _store(tmp_path, writer=writer)
    session = _session(store)
    store.save(session)
    writer.close()
    session.messages.append(ChatMessage("user", "晚到的保存"))
    store.save(session)  # 不应抛异常


# ---------------------------------------------------------------- 多实例 / 共享队列

def test_multi_instance_roots_are_isolated(tmp_path: Path):
    writer = _SessionWriter()
    store_a = _store(tmp_path, writer=writer, instance_id="a")
    store_b = _store(tmp_path, writer=writer, instance_id="b")
    s1 = _session(store_a)
    s1.messages.append(ChatMessage("user", "instance-a"))
    store_a.save(s1)
    s2 = _session(store_b)
    s2.messages.append(ChatMessage("user", "instance-b"))
    store_b.save(s2)
    assert writer.flush()
    assert (tmp_path / "sessions-a" / "cat" / f"{s1.session_id}.json").exists()
    assert (tmp_path / "sessions-b" / "cat" / f"{s2.session_id}.json").exists()
    assert store_a.load(s1.session_id, "cat").messages[-1].content == "instance-a"
    assert store_b.load(s2.session_id, "cat").messages[-1].content == "instance-b"
    writer.close()


def test_two_stores_sharing_writer_serialize_same_session(tmp_path: Path):
    """同进程双 store（同根目录）写同一会话：共享串行队列，读穿透看到最新，不互相覆盖。"""
    writer = _SessionWriter()
    store1 = _store(tmp_path, writer=writer)
    store2 = _store(tmp_path, writer=writer)
    session = _session(store1)
    session.messages.append(ChatMessage("user", "from-store1"))
    store1.save(session)

    seen = store2.load(session.session_id, "cat")
    assert seen is not None and seen.messages[-1].content == "from-store1"
    seen.messages.append(ChatMessage("assistant", "from-store2"))
    store2.save(seen)

    assert writer.flush()
    final = store1.load(session.session_id, "cat")
    assert [m.content for m in final.messages] == ["from-store1", "from-store2"]
    writer.close()


def test_load_without_character_finds_pending_snapshot(tmp_path: Path):
    """不带 character_id 的 load 也能看到尚未落盘的快照（异步期间不丢会话）。"""
    writer = _SessionWriter()
    store = _store(tmp_path, writer=writer)
    session = _session(store)
    session.messages.append(ChatMessage("user", "pending"))
    started = threading.Event()
    release = threading.Event()
    real = writer.atomic_write

    def stalled(path, snapshot):
        started.set()
        assert release.wait(5)
        return real(path, snapshot)

    writer.atomic_write = stalled
    store.save(session)
    assert started.wait(2)
    loaded = store.load(session.session_id)  # 无 character_id：glob + 待写快照
    assert loaded is not None and loaded.messages[-1].content == "pending"
    release.set()
    assert writer.flush()
    writer.close()
