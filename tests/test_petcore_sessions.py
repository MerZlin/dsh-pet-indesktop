from __future__ import annotations

from petcore.chat.session_store import SessionStore


def test_session_store_isolated_by_slot_and_character(tmp_path) -> None:
    main = SessionStore(tmp_path, slot=0, character_id="main")
    child = SessionStore(tmp_path, slot=1, character_id="main")
    main_session = main.new_session()
    main_session.append("user", "主宠问题")
    main.save(main_session)

    assert main.load(main_session.session_id).messages[-1]["content"] == "主宠问题"
    assert child.list_sessions() == []
    assert "sessions" in str(main.directory)
    assert "sessions-slot-1" in str(child.directory)


def test_session_store_limits_messages_and_writes_atomically(tmp_path) -> None:
    store = SessionStore(tmp_path, slot=2, character_id="safe")
    session = store.new_session()
    for index in range(250):
        session.append("user", f"message-{index}")
    store.save(session)

    loaded = store.load(session.session_id)
    assert loaded is not None
    assert len(loaded.messages) == 200
    assert loaded.messages[0]["content"] == "message-50"
    assert not list(store.directory.glob("*.tmp"))


def test_session_store_rejects_path_escape(tmp_path) -> None:
    store = SessionStore(tmp_path)
    try:
        store.load("../outside")
    except ValueError as exc:
        assert "session id" in str(exc)
    else:
        raise AssertionError("path escape must be rejected")


def test_session_archive_round_trip_and_old_files(tmp_path) -> None:
    import json

    store = SessionStore(tmp_path, character_id="arch")
    first = store.new_session()
    first.append("user", "旧会话")
    store.save(first)

    archived = store.archive(first.session_id, True)
    assert archived is not None and archived.archived_at > 0
    assert store.load(first.session_id).archived_at > 0
    assert store.list_sessions() == []
    assert [item.session_id for item in store.list_sessions(include_archived=True)] == [
        first.session_id
    ]

    # 旧文件没有 archived_at：读出来就是未归档，schema 仍按 1 接受。
    path = store.directory / f"{first.session_id}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.pop("archived_at")
    path.write_text(json.dumps(raw), encoding="utf-8")
    loaded = store.load(first.session_id)
    assert loaded is not None and loaded.archived_at == 0.0
    assert store.list_sessions()

    store.archive(first.session_id, False)
    assert store.load(first.session_id).archived_at == 0.0
