# -*- coding: utf-8 -*-
"""B8 复审：会话保存异步化 + debounce 的行为测试。

覆盖：
- 串行 I/O worker：save() 只入队（不阻塞 GUI），worker 串行落盘；
- 同一会话（同一路径）队列合并：只保留最新快照；
- 流式结束后只保存一次（begin 不保存，finish 保存整个交换）；
- 退出/停止/失败/关窗强制 flush，且 flush 必须反映真实落盘结果（失败返回 False）；
- 旧快照覆盖防护：基于旧 rev 的保存按 message_id 合并（双窗口/双进程不互覆）；
- 删除复活防护：显式删除以墓碑拒绝迟到保存；
- 历史上限：单会话消息数、单条消息字符数、单文件大小、列表加载数量；
- 崩溃安全：tmp+fsync+os.replace 原子替换、无 tmp 残留、父目录 fsync；
- worker 异常可观测（failure_count/last_error/failed_count）且不丢内存快照
  （失败快照参与读穿透）；
- 退出时序：关窗/应用退出直接入队当前会话，不依赖 queued stopped 回调；
- 多实例隔离 / 同进程多 store 共享队列（双窗口写同一会话合并不互覆）。

所有线程同步使用 Event / flush()（真实落盘屏障），不用 sleep 猜时序。
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
    _atomic_delete,
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


def test_delete_cancels_pending_save_and_blocks_late_save_resurrection(tmp_path: Path):
    """delete 覆盖未落盘的 save；删除后迟到的保存被拒绝（显式删除不得被旧窗口复活）。"""
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

    # 删除之后，持有旧对象的迟到保存不得复活会话（防复活墓碑）
    session.messages.append(ChatMessage("user", "again"))
    assert store.save(session) is session  # 不抛异常，但提交被拒绝
    assert writer.flush()
    assert not path.exists()
    assert store.load(session.session_id, "cat") is None
    assert writer.conflict_count >= 1
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
    assert not writer.flush()  # 有失败操作未落盘 → flush 必须返回 False（不再是虚假成功）
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
    assert not writer.flush()  # 失败快照未落盘 → flush 返回 False
    assert writer.failed_count == 1
    state["fail"] = False
    assert writer.flush()  # flush 重试 failed 快照，恢复后落盘 → True
    assert writer.failed_count == 0
    assert store.load(session.session_id, "cat").messages[-1].content == "flush 重试"
    writer.close()


def test_close_flushes_pending_ops(tmp_path: Path):
    """close()（退出路径）排空队列：未落盘操作全部写盘，返回真实落盘结果。"""
    writer = _SessionWriter()
    store = _store(tmp_path, writer=writer)
    session = _session(store)
    session.messages.append(ChatMessage("user", "close 前"))
    store.save(session)
    assert writer.close() is True
    path = store._path("cat", session.session_id)
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["messages"][0]["content"] == "close 前"


def test_save_after_close_is_safe_noop(tmp_path: Path):
    """writer 关闭后的 save 不抛异常；关闭后提交自动重开 worker，数据不丢。"""
    writer = _SessionWriter()
    store = _store(tmp_path, writer=writer)
    session = _session(store)
    store.save(session)
    writer.close()
    session.messages.append(ChatMessage("user", "晚到的保存"))
    store.save(session)  # 不应抛异常（关闭后重开 worker 接受并落盘）
    assert writer.flush()
    loaded = store.load(session.session_id, "cat")
    assert loaded is not None and loaded.messages[-1].content == "晚到的保存"
    writer.close()


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


# ---------------------------------------------------------------- flush 契约：真实落盘结果

def test_flush_times_out_and_reports_false(tmp_path: Path):
    """flush 超时（worker 卡死）必须返回 False，而不是虚假成功。"""
    writer = _SessionWriter()
    store = _store(tmp_path, writer=writer)
    session = _session(store)
    session.messages.append(ChatMessage("user", "x"))
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
    assert writer.flush(timeout=0.2) is False
    release.set()
    assert writer.flush()
    assert store.load(session.session_id, "cat").messages[-1].content == "x"
    writer.close()


def test_delete_failure_is_tracked_and_flush_reports(tmp_path: Path):
    """删除失败不得被遗忘：记录在失败表、flush 返回 False、恢复后重试成功。"""
    writer = _SessionWriter()
    store = _store(tmp_path, writer=writer)
    session = _session(store)
    session.messages.append(ChatMessage("user", "hi"))
    path = store._path("cat", session.session_id)
    store.save(session)
    assert writer.flush()
    assert path.exists()

    def failing_delete(target):
        raise OSError("injected delete failure")

    writer.atomic_delete = failing_delete
    store.delete(session)
    assert not writer.flush()  # 删除失败 → flush 必须报告失败
    assert writer.failed_count >= 1
    assert path.exists()  # 文件仍在（删除未生效）

    writer.atomic_delete = _atomic_delete
    assert writer.flush()  # flush 重试删除成功 → True
    assert not path.exists()
    assert store.load(session.session_id, "cat") is None
    writer.close()


def test_load_and_list_see_failed_snapshot(tmp_path: Path):
    """连续写失败后 _failed 快照仍参与读穿透：load/list 不丢会话（不读到旧磁盘版本）。"""
    writer = _SessionWriter()
    store = _store(tmp_path, writer=writer)
    session = _session(store)
    session.messages.append(ChatMessage("user", "重要内容"))

    def failing(path, snapshot):
        raise OSError("disk down")

    writer.atomic_write = failing
    store.save(session)
    assert not writer.flush()
    loaded = store.load(session.session_id, "cat")
    assert loaded is not None and loaded.messages[-1].content == "重要内容"
    assert any(s.session_id == session.session_id for s in store.list("cat"))
    writer.close()


# ---------------------------------------------------------------- 旧快照覆盖 / 删除复活

def test_stale_save_merges_messages_instead_of_overwriting(tmp_path: Path):
    """两个窗口基于同一旧版本并发编辑：后到的保存按 message_id 合并，不静默覆盖。"""
    writer = _SessionWriter()
    store1 = _store(tmp_path, writer=writer)
    store2 = _store(tmp_path, writer=writer)
    session = _session(store1)
    session.messages.append(ChatMessage("user", "from-A"))
    store1.save(session)
    assert writer.flush()

    # B 仍持有旧副本（base rev 0）：追加自己的消息后保存
    stale = _session(store2)
    stale.session_id = session.session_id
    stale.character_id = session.character_id
    stale.messages.append(ChatMessage("user", "from-B"))
    store2.save(stale)
    assert writer.flush()
    assert writer.conflict_count >= 1

    loaded = store1.load(session.session_id, "cat")
    assert [m.content for m in loaded.messages] == ["from-A", "from-B"]
    writer.close()


def test_two_writers_same_path_merge_not_overwrite(tmp_path: Path):
    """两个独立 writer（模拟两个进程）写同一会话：跨进程 CAS 合并，最后写入不静默覆盖。"""
    writer1 = _SessionWriter()
    writer2 = _SessionWriter()
    store1 = _store(tmp_path, writer=writer1)
    store2 = _store(tmp_path, writer=writer2)
    session = _session(store1)
    session.messages.append(ChatMessage("user", "from-process-1"))
    store1.save(session)
    assert writer1.flush()
    path = store1._path("cat", session.session_id)

    # 进程 2 基于旧版本（rev 0）编辑同一会话
    stale = _session(store2)
    stale.session_id = session.session_id
    stale.character_id = session.character_id
    stale.messages.append(ChatMessage("user", "from-process-2"))
    store2.save(stale)
    assert writer2.flush()

    data = json.loads(path.read_text(encoding="utf-8"))
    assert [m["content"] for m in data["messages"]] == ["from-process-1", "from-process-2"]
    writer1.close()
    writer2.close()


def test_close_reports_flush_failure(tmp_path: Path):
    """close() 必须返回真实落盘结果：写失败时返回 False（不再静默忽略）。"""
    writer = _SessionWriter()
    store = _store(tmp_path, writer=writer)
    session = _session(store)
    session.messages.append(ChatMessage("user", "x"))

    def failing(path, snapshot):
        raise OSError("disk down")

    writer.atomic_write = failing
    store.save(session)
    assert writer.close() is False


def test_metadata_over_file_cap_fails_loudly(tmp_path: Path, monkeypatch):
    """非消息字段（system_prompt 等）单独超限：保存必须失败（flush False），不静默产出超限文件。"""
    from pet.chat import session_store as ss

    monkeypatch.setattr(ss, "MAX_SESSION_FILE_BYTES", 256)
    writer = _SessionWriter()
    store = _store(tmp_path, writer=writer)
    session = _session(store)
    session.system_prompt = "超长元数据" * 300  # 元数据本身超过 256 字节
    session.messages.append(ChatMessage("user", "hi"))
    store.save(session)
    assert not writer.flush()
    assert not store._path("cat", session.session_id).exists()
    writer.close()


# ---------------------------------------------------------------- 退出时序

def test_close_event_persists_message_without_queued_stopped_callback(tmp_path: Path):
    """关窗（生成中直接退出）必须直接入队当前会话：不依赖 worker 的 queued stopped 回调。"""
    from PySide6.QtWidgets import QApplication

    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    sid = window.session.session_id
    window.session.messages.append(ChatMessage("user", "生成中的提问"))
    window._active_request_id = "r"
    window.close()  # closeEvent：直接 save + stop + flush
    # 直接读磁盘（不经读穿透），证明已真实落盘
    path = window.store._path("shenshen", sid)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [m["content"] for m in data["messages"]] == ["生成中的提问"]
    app.processEvents()


def test_close_event_during_generation_persists_question(tmp_path: Path):
    """真实 worker 阻塞生成中关窗：提问必须落盘；迟到的 queued stopped 不破坏数据。"""
    import time

    from PySide6.QtWidgets import QApplication

    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    sid = window.session.session_id
    started = threading.Event()
    release = threading.Event()

    class BlockingProvider:
        def stream(self, messages, config, cancel):
            started.set()
            while not cancel.is_set() and not release.is_set():
                time.sleep(0.01)
            if cancel.is_set():
                return
            yield "回答"

    window.service.provider = BlockingProvider()
    window.session.messages.append(ChatMessage("user", "生成中的提问"))
    window.service.send([{"role": "user", "content": "生成中的提问"}], window.settings.active_config)
    assert started.wait(2.0), "worker 未启动"

    window.close()  # closeEvent：直接入队 + flush（不依赖 queued stopped）
    path = window.store._path("shenshen", sid)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert any(m["role"] == "user" and m["content"] == "生成中的提问" for m in data["messages"])

    # 放行 worker 退出并驱动 queued 信号：迟到的 stopped 不得破坏已落盘数据
    release.set()
    deadline = time.time() + 3.0
    while window.service._worker is not None and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert path.exists()


def test_about_to_quit_saves_chat_windows_and_flushes(tmp_path: Path, monkeypatch):
    """应用退出（aboutToQuit）直接保存各聊天窗会话并 flush 共享 writer，不依赖 queued 回调。"""
    from PySide6.QtWidgets import QApplication

    import pet.chat.session_store as ss_mod
    from pet.app import PetApp
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    owner = PetApp(app, Config(tmp_path))
    owner.win = None
    owner.slot_handle = None

    saves: list[str] = []
    flushes: list[int] = []

    class FakeStore:
        def save(self, session):
            saves.append(session.name)

        @property
        def last_error(self):
            return None

    class FakeService:
        def stop(self):
            pass

    class FakeWin:
        def __init__(self, name):
            self.name = name
            self.session = self
            self.store = FakeStore()
            self.service = FakeService()

    owner.legacy_chat_window = FakeWin("legacy")
    owner.modern_chat_window = FakeWin("modern")
    owner.quick_chat = FakeWin("quick")
    monkeypatch.setattr(
        ss_mod, "flush_shared_writer",
        lambda **kw: (flushes.append(1), True)[1],
    )
    owner._on_about_to_quit()
    assert saves == ["legacy", "modern", "quick"]
    assert flushes == [1]


def test_shared_writer_close_flushes_and_reopens_on_submit(tmp_path: Path):
    """共享 writer 的应用退出关闭：先排空落盘；关闭后再提交自动重开（绝不静默丢数据）。"""
    from pet.chat.session_store import close_shared_writer, flush_shared_writer

    store = SessionStore(tmp_path)
    session = _session(store)
    session.messages.append(ChatMessage("user", "退出前"))
    store.save(session)
    assert flush_shared_writer() is True
    path = store._path("cat", session.session_id)
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["messages"][0]["content"] == "退出前"
    assert close_shared_writer() is True
    assert close_shared_writer() is True  # 幂等
    # 关闭后提交：自动重开 worker 接受，不丢数据
    session.messages.append(ChatMessage("user", "关闭后"))
    store.save(session)
    assert flush_shared_writer() is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [m["content"] for m in data["messages"]] == ["退出前", "关闭后"]
    assert close_shared_writer() is True
