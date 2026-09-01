# -*- coding: utf-8 -*-
"""B8 会话异步写盘回归测试。

同步点一律用 flush()/close()（确定性），不用 sleep 猜时序。
每个用例结束 close_all_writers() 清注册表，避免测试间共享 worker。
"""
from __future__ import annotations

import json

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
    w = ss._writers[st2.root]
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
