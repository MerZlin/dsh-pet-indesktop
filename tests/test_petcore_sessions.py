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


def test_schema_v1_migrates_and_keeps_pre_v2_backup(tmp_path) -> None:
    import json

    store = SessionStore(tmp_path, character_id="legacy")
    store.directory.mkdir(parents=True, exist_ok=True)
    path = store.directory / "old.json"
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "session_id": "old",
                "title": "旧会话",
                "messages": [{"role": "user", "content": "旧消息"}],
                "updated_at": 1.0,
                "archived_at": 0.0,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    loaded = store.load("old")
    assert loaded is not None
    assert loaded.channel == "ai"
    assert loaded.messages[0]["id"]
    assert loaded.messages[0]["ts"] == 0.0
    loaded.append("assistant", "新消息")
    store.save(loaded)
    backup = path.with_name("old.json.pre-v2")
    assert backup.is_file()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema"] == 2
    assert raw["channel"] == "ai"


def test_channel_conversations_and_external_id_dedupe(tmp_path) -> None:
    store = SessionStore(tmp_path, character_id="channels")
    session = store.ensure_conversation("qq", "group:123", title="群聊")
    assert session.channel == "qq"
    assert session.conversation_id == "group:123"
    session.append("user", "你好", external_id="m-1", sender="小明")
    session.append("user", "你好", external_id="m-1", sender="小明")
    store.save(session)
    assert len(session.messages) == 1
    assert session.messages[0]["channel"] == "qq"
    assert session.messages[0]["sender"] == "小明"
    assert store.find_by_conversation("qq", "group:123").session_id == session.session_id
    assert store.list_sessions(channel="dsh") == []

    dsh = store.ensure_conversation("dsh", "session-abc", title="DSH")
    dsh.append("assistant", "DSH 回答")
    store.save(dsh)
    assert [item.session_id for item in store.list_sessions(channel="dsh")] == [
        dsh.session_id
    ]
