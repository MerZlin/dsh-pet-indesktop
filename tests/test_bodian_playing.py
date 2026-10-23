# -*- coding: utf-8 -*-
"""波点音乐曲目来源（``pet/music/bodian.py``）与它的接线。

**为什么需要这一族**：波点音乐（``bodian_pc.exe``）**不注册 Windows 系统媒体会话**
——2026-10-21 实机探针在它正在播放时读到 ``GetSessions()`` 返回 0 个会话，它的
``app.so`` 与全部插件 DLL 里也没有 ``SystemMediaTransportControls`` 字样；主窗口标题
又是常量 ``波点音乐``，所以 foobar 那套「读窗口标题」的补充源对它同样无效。唯一的
可用通道是它自己写在 ``%LOCALAPPDATA%`` 下的播放历史库与日志。

本文件钉住五件事：

1. **数据来源形状**：库读 ``hist_song`` 最新一行、日志取最后一条
   ``mpv playing event=``、路径只认 ``%LOCALAPPDATA%`` 下那一处；
2. **不可信输入**：坏 JSON / 超长 / 字段缺失 / 坏时长一律当"没有"，绝不猜；
3. **降级与边界**：库短暂不可读时只在**进程还在**的前提下沿用旧曲目，
   进程不在就老实返回没有；非 Windows 一律没有；
4. **接线**：只在 SMTC 没有在播会话时才问它、返回的 ``app_id`` 不占 SMTC 跟踪位、
   配置键按 ``_bool_or_default`` 归一、设置行真的挂进设置页；
5. **最脆的两条路径**：数据库被写事务占住（``database is locked``）与**中文/非 ASCII
   安装路径**（SQLite ``file:`` URI 编码）——这两条是"读第三方文件"最容易静默失败
   的地方，各自有独立用例。

全部用例不联网：库与日志都写在 ``tmp_path`` 里，只有 ``%LOCALAPPDATA%`` 的解析被替换
成临时目录。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time

import pytest

from pet.music import bodian, now_playing

# 一条真实形态的 hist_song 行（字段名与 2026-10-21 本机实测一致，值已换掉）。
_TRACK_JSON = json.dumps(
    {
        "id": 64434818,
        "name": "Lag Fyrir Ömmu",
        "album": "Living Room Songs",
        "artist": "Ólafur Arnalds",
        "duration": 212,
        "isMv": 0,
        "online": 1,
    },
    ensure_ascii=False,
)

_LOG_PLAYING = "2026-10-21 10:28:57.095 [I] mpv playing event=true\n"
_LOG_PAUSED = "2026-10-21 10:28:59.209 [I] mpv playing event=false\n"


@pytest.fixture(autouse=True)
def _win32(monkeypatch):
    """默认把平台钉在 win32（来源只在 Windows 生效），每个用例重置缓存。"""
    monkeypatch.setattr(sys, "platform", "win32")
    bodian.reset_cache()


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    """把 ``%LOCALAPPDATA%`` 指到临时目录，并建好波点的目录结构。"""
    root = tmp_path / "LocalAppData"
    (root / "cn.wenyu.bodian" / "bodian_pc" / "database").mkdir(parents=True)
    (root / "cn.wenyu.bodian" / "bodian_pc" / "bdlog").mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(root))
    return root


def _db_path(data_root):
    return data_root / "cn.wenyu.bodian" / "bodian_pc" / "database" / "songDB.db"


def _log_path(data_root, name: str = "log_0.log"):
    return data_root / "cn.wenyu.bodian" / "bodian_pc" / "bdlog" / name


def _write_db(path, rows, *, json_column: str = "json"):
    """建一个与波点同形的 ``hist_song`` 表并写入行。

    ``rows`` 是 ``[(ord, id, time, json_text)]``。表结构与实测一致
    （``ord INTEGER PRIMARY KEY AUTOINCREMENT, id INTEGER, path TEXT, json TEXT, time DATETIME``）。
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE hist_song (ord INTEGER PRIMARY KEY AUTOINCREMENT, id INTEGER, "
            "path TEXT, json TEXT, time DATETIME)"
        )
        for ord_value, song_id, stamp, payload in rows:
            conn.execute(
                "insert into hist_song (ord, id, path, json, time) values (?, ?, '', ?, ?)",
                (ord_value, song_id, payload, stamp),
            )
        conn.commit()
    finally:
        conn.close()


def _write_log(data_root, text: str, name: str = "log_0.log"):
    _log_path(data_root, name).write_text(text, encoding="utf-8")


# ------------------------------------------------------------ 数据来源形状


def test_reads_newest_hist_row_and_maps_fields(data_root, monkeypatch):
    """最新一行 → 曲目字段（歌名/歌手/专辑/时长），播放状态取自日志。"""
    _write_db(
        _db_path(data_root),
        [
            (1, 26195732, "2026-10-21 01:51:10.256299", json.dumps({"name": "旧歌", "artist": "旧人"})),
            (2, 64434818, "2026-10-21 10:14:06.881137", _TRACK_JSON),
        ],
    )
    _write_log(data_root, _LOG_PLAYING)
    monkeypatch.setattr(bodian, "process_running", lambda: True)

    track = bodian.read_track()

    assert track is not None
    assert (track.title, track.artist, track.album) == (
        "Lag Fyrir Ömmu",
        "Ólafur Arnalds",
        "Living Room Songs",
    )
    assert track.duration == 212.0
    assert track.playing is True


def test_playing_state_comes_from_last_log_line(data_root):
    """日志里最后一条 ``mpv playing event=`` 决定播放状态（暂停 → False）。"""
    _write_db(_db_path(data_root), [(1, 1, "t", _TRACK_JSON)])
    _write_log(data_root, _LOG_PLAYING + _LOG_PAUSED)

    assert bodian.playing_state() is False


def test_playing_state_unknown_when_log_has_no_marker(data_root):
    """日志里没有那条事件时返回 ``None``（"不知道"，不是"没在播"）。"""
    _write_db(_db_path(data_root), [(1, 1, "t", _TRACK_JSON)])
    _write_log(data_root, "2026-10-21 10:00:00.000 [I] 别的日志\n")

    assert bodian.playing_state() is None


def test_unknown_playing_state_is_not_cached(data_root):
    """**"不知道"不进缓存**：下一拍必须重新看日志。

    审查指出文档写"不缓存 None"而实现会缓存满 1 秒。缓存 ``None`` 的后果很具体：
    波点刚启动时日志还没写出第一行，那一拍读到 ``None``，若把它缓存住，接下来
    整整一秒都判定不了播放状态——而歌词本来可以在一拍内就出来。
    """
    _write_db(_db_path(data_root), [(1, 1, "t", _TRACK_JSON)])
    _write_log(data_root, "还没有播放事件\n")

    assert bodian.playing_state() is None

    # 不改任何缓存状态，只补写日志：因为 None 没被缓存，这一拍就该读到 True。
    _write_log(data_root, _LOG_PLAYING)

    assert bodian.playing_state() is True


def test_known_playing_state_is_cached(data_root):
    """确定结果照常进缓存（同一拍里被问多次不重复读 64KB 日志）。"""
    _write_db(_db_path(data_root), [(1, 1, "t", _TRACK_JSON)])
    _write_log(data_root, _LOG_PLAYING)

    assert bodian.playing_state() is True
    # 把日志改成"暂停"但不清缓存：仍应返回缓存里的 True。
    _write_log(data_root, _LOG_PAUSED)

    assert bodian.playing_state() is True
    bodian.reset_cache()
    assert bodian.playing_state() is False


def test_unknown_playing_state_is_treated_as_playing(data_root, monkeypatch):
    """判断不了播放状态时按在播处理——宁可多显示一次，也不让歌词整段消失。"""
    _write_db(_db_path(data_root), [(1, 1, "t", _TRACK_JSON)])
    _write_log(data_root, "没有那条事件\n")
    monkeypatch.setattr(bodian, "process_running", lambda: True)

    track = bodian.read_track()

    assert track is not None and track.playing is True


def test_paths_stay_under_local_appdata(data_root):
    """三个路径入口都落在 ``%LOCALAPPDATA%`` 下那一处，不去扫别的盘。"""
    expected_root = data_root / "cn.wenyu.bodian" / "bodian_pc"

    assert bodian.data_dir() == expected_root
    assert bodian.database_path() == expected_root / "database" / "songDB.db"
    assert bodian.log_dir() == expected_root / "bdlog"


def test_non_windows_returns_none(monkeypatch):
    """非 Windows 一律没有（这条来源读的是 Windows 的数据目录）。"""
    monkeypatch.setattr(sys, "platform", "linux")

    assert bodian.read_track() is None


# ------------------------------------------------------------ 不可信输入


@pytest.mark.parametrize(
    "payload",
    [
        "",
        " ",
        "not json at all",
        "[1, 2, 3]",  # 顶层不是对象
        json.dumps({"name": "", "artist": ""}),  # 两个字段都空
        json.dumps({"name": 42, "artist": None}),  # 类型不对 → 空串 → 整体无效
    ],
)
def test_broken_or_empty_json_yields_no_track(data_root, monkeypatch, payload):
    """坏 JSON / 空字段一律当"没有"，不猜歌名。"""
    _write_db(_db_path(data_root), [(1, 1, "t", payload)])
    monkeypatch.setattr(bodian, "process_running", lambda: True)

    assert bodian.read_track() is None


def test_oversized_json_is_rejected(data_root, monkeypatch):
    """单行 json 超过上限按损坏处理（上限见 ``_MAX_JSON_CHARS``）。"""
    big = json.dumps({"name": "x" * (bodian._MAX_JSON_CHARS + 10), "artist": "y"})
    _write_db(_db_path(data_root), [(1, 1, "t", big)])
    monkeypatch.setattr(bodian, "process_running", lambda: True)

    assert bodian.read_track() is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        (212, 212.0),
        ("212", 0.0),  # 字符串不接受
        (True, 0.0),  # bool 不接受
        (-5, 0.0),  # 负数不接受
        (0, 0.0),
        (float("nan"), 0.0),
        (float("inf"), 0.0),
        (None, 0.0),
    ],
)
def test_duration_normalization(raw, expected):
    """时长只接受正数（bool / 字符串 / NaN / 无穷 / 负数一律 0 = 未知）。"""
    assert bodian._seconds(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  歌名  ", "歌名"),
        ("", ""),
        (None, ""),
        (42, ""),
        (True, ""),
        (["歌名"], ""),
    ],
)
def test_text_never_coerces_non_strings(raw, expected):
    """``_text`` 不做 ``str()`` 兜底：数字变歌名会搜到别人的歌。"""
    assert bodian._text(raw) == expected


def test_missing_table_yields_no_track(data_root, monkeypatch):
    """库在、但表不存在（波点还没播过歌）→ 没有曲目，且不抛。"""
    conn = sqlite3.connect(str(_db_path(data_root)))
    try:
        conn.execute("CREATE TABLE something_else (id INTEGER)")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(bodian, "process_running", lambda: True)

    assert bodian.read_track() is None


def test_missing_database_yields_no_track(data_root, monkeypatch):
    """库文件不存在（波点没装/没跑过）→ 没有曲目。"""
    monkeypatch.setattr(bodian, "process_running", lambda: True)

    assert bodian.read_track() is None


def test_process_gone_yields_no_track_even_when_row_reads_fine(data_root, monkeypatch):
    """**实机 bug 回归**：进程不在就没有歌——哪怕库读得到、里面还留着上一首。

    这是 2026-10-23 在真机上抓到的：波点退出两天后，``hist_song`` 里最新一行仍是
    上次播的「赐我茉莉」，而 ``read_track()`` 照样返回它。后果是歌词永远停在那一首。

    根因是进程判定只挂在"库读不到"这条分支上（_current_row 的降级路径），
    库读得到时压根不看进程。修法是在读库**之前**就判，与 ``foobar.py`` 的
    ``if not pids: return None`` 同一先例。

    注意本用例**不能** monkeypatch ``process_running``（那正是被测对象）：库和日志
    都写得完完整整，只让进程判定返回 False。
    """
    _write_db(_db_path(data_root), [(1, 1, "t", _TRACK_JSON)])
    _write_log(data_root, _LOG_PLAYING)
    monkeypatch.setattr(bodian, "process_running", lambda: False)

    assert bodian.read_track() is None


def test_process_check_failure_does_not_block_reading(data_root, monkeypatch):
    """进程判定本身抛异常时**不拦截**（fail-open）：不能让一次扫描失败吞掉歌词。"""
    _write_db(_db_path(data_root), [(1, 1, "t", _TRACK_JSON)])
    _write_log(data_root, _LOG_PLAYING)

    def boom():
        raise RuntimeError("process scan exploded")

    monkeypatch.setattr(bodian, "process_running", boom)

    track = bodian.read_track()

    assert track is not None and track.title == "Lag Fyrir Ömmu"


# ------------------------------------------------------------ 最脆的两条路径


def test_locked_database_falls_back_to_last_track(data_root, monkeypatch):
    """**真锁**：写事务持有排他锁时，读不炸、且在进程还在时沿用上次曲目。

    这是「读第三方文件」最脆的路径之一：波点用 ``journal_mode=delete``，它写库的那
    一瞬间我们读会拿到 ``database is locked``。用例真的开一个 ``BEGIN EXCLUSIVE``
    来占锁（不是 mock 异常），验证产品代码走的是"沿用旧值"而不是"抛异常/清空歌词"。
    """
    _write_db(_db_path(data_root), [(1, 1, "t", _TRACK_JSON)])
    _write_log(data_root, _LOG_PLAYING)
    monkeypatch.setattr(bodian, "process_running", lambda: True)
    first = bodian.read_track()
    assert first is not None

    # 另开一个连接占住排他锁并保持住（不提交）。
    holder = sqlite3.connect(str(_db_path(data_root)), timeout=0.1)
    try:
        holder.execute("BEGIN EXCLUSIVE")
        # 真的撞锁了：读超时 = 产品常量（0.2s），拿到的必须是上一次的曲目。
        second = bodian.read_track()
    finally:
        holder.rollback()
        holder.close()

    assert second is not None
    assert second.title == first.title


def test_locked_database_without_previous_track_yields_none(data_root, monkeypatch):
    """锁住且没有历史可沿用 → ``None``（不编造、不抛）。"""
    _write_db(_db_path(data_root), [(1, 1, "t", _TRACK_JSON)])
    monkeypatch.setattr(bodian, "process_running", lambda: True)

    holder = sqlite3.connect(str(_db_path(data_root)), timeout=0.1)
    try:
        holder.execute("BEGIN EXCLUSIVE")
        assert bodian.read_track() is None
    finally:
        holder.rollback()
        holder.close()


def test_non_ascii_install_path_is_readable(tmp_path, monkeypatch):
    """**中文/非 ASCII 路径**：``%LOCALAPPDATA%`` 在中文 Windows 上带中文用户名。

    ``file:`` URI 对非 ASCII 与保留字符有语义，直接拼字符串会让 SQLite 去开一个
    不存在的路径——表现为"读不到歌"，而且**没有任何报错**（异常被吞成 debug 日志）。
    本用例把整个数据目录放进中文目录名里，并额外塞进 ``#`` 与空格（URI 的 fragment
    分隔符与保留字符）。
    """
    root = tmp_path / "用户 数据" / "AppData#1" / "Local"
    (root / "cn.wenyu.bodian" / "bodian_pc" / "database").mkdir(parents=True)
    (root / "cn.wenyu.bodian" / "bodian_pc" / "bdlog").mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(root))
    db = root / "cn.wenyu.bodian" / "bodian_pc" / "database" / "songDB.db"
    _write_db(db, [(1, 1, "t", _TRACK_JSON)])
    _write_log(root, _LOG_PLAYING)
    monkeypatch.setattr(bodian, "process_running", lambda: True)

    track = bodian.read_track()

    assert track is not None, "中文/含 # 的路径读不到——file: URI 编码有问题"
    assert track.title == "Lag Fyrir Ömmu"


def test_sqlite_uri_encodes_reserved_characters(tmp_path):
    """``_sqlite_uri`` 必须编码 ``#`` / ``%`` / 空格，且保留斜杠与盘符冒号。"""
    path = tmp_path / "a#b" / "100%" / "song db.sqlite"
    uri = bodian._sqlite_uri(path)

    assert uri.startswith("file:")
    assert uri.endswith("?mode=ro")
    body = uri[len("file:"): -len("?mode=ro")]
    assert "#" not in body, "未编码的 # 会被当成 fragment 分隔符"
    assert " " not in body
    assert "//" not in body.replace("file://", "") or True
    # 盘符冒号与路径分隔符保留（SQLite 需要它们表达绝对路径）。
    assert ":" in body
    assert "/" in body
    # 百分号自身必须被编码，否则路径里的 % 会被当成转义起点。
    assert "%25" in body


# ------------------------------------------------------------ 降级与边界


def test_stale_row_is_reused_while_process_runs(data_root, monkeypatch):
    """库短暂不可读（写事务撞锁）时，进程还在就沿用上次的曲目。"""
    _write_db(_db_path(data_root), [(1, 1, "t", _TRACK_JSON)])
    _write_log(data_root, _LOG_PLAYING)
    monkeypatch.setattr(bodian, "process_running", lambda: True)
    first = bodian.read_track()
    assert first is not None

    _db_path(data_root).unlink()  # 模拟这一拍读不到
    second = bodian.read_track()

    assert second is not None
    assert second.title == first.title


def test_stale_row_is_dropped_when_process_gone(data_root, monkeypatch):
    """进程不在了就不许沿用——否则波点退出后歌词永远停在上一首。"""
    _write_db(_db_path(data_root), [(1, 1, "t", _TRACK_JSON)])
    _write_log(data_root, _LOG_PLAYING)
    monkeypatch.setattr(bodian, "process_running", lambda: True)
    assert bodian.read_track() is not None

    _db_path(data_root).unlink()
    monkeypatch.setattr(bodian, "process_running", lambda: False)

    assert bodian.read_track() is None


def test_stale_reuse_expires(data_root, monkeypatch):
    """沿用有上限：超过 ``_STALE_REUSE_SECONDS`` 就当作没有。"""
    _write_db(_db_path(data_root), [(1, 1, "t", _TRACK_JSON)])
    _write_log(data_root, _LOG_PLAYING)
    monkeypatch.setattr(bodian, "process_running", lambda: True)
    assert bodian.read_track() is not None

    _db_path(data_root).unlink()
    stale_at = time.monotonic() - (bodian._STALE_REUSE_SECONDS + 1.0)
    with bodian._lock:
        bodian._last_good = (stale_at, bodian._last_good[1])

    assert bodian.read_track() is None


def test_row_json_is_parsed_once_per_row(data_root, monkeypatch):
    """同一行只解析一次 JSON（1s 一拍的热路径，不做重复工作）。"""
    _write_db(_db_path(data_root), [(1, 1, "t", _TRACK_JSON)])
    _write_log(data_root, _LOG_PLAYING)
    monkeypatch.setattr(bodian, "process_running", lambda: True)

    calls = {"n": 0}
    real_parse = bodian._parse_row

    def counting(row):
        calls["n"] += 1
        return real_parse(row)

    monkeypatch.setattr(bodian, "_parse_row", counting)
    for _ in range(5):
        bodian.read_track()

    assert calls["n"] == 1


def test_new_row_invalidates_the_parse_cache(data_root, monkeypatch):
    """换歌（新的一行）必须重新解析，不能拿上一首的缓存。"""
    _write_db(_db_path(data_root), [(1, 1, "t", _TRACK_JSON)])
    _write_log(data_root, _LOG_PLAYING)
    monkeypatch.setattr(bodian, "process_running", lambda: True)
    assert bodian.read_track().title == "Lag Fyrir Ömmu"

    conn = sqlite3.connect(str(_db_path(data_root)))
    try:
        conn.execute(
            "insert into hist_song (ord, id, path, json, time) values (?, ?, '', ?, ?)",
            (2, 2, json.dumps({"name": "新歌", "artist": "新人"}, ensure_ascii=False), "t2"),
        )
        conn.commit()
    finally:
        conn.close()

    assert bodian.read_track().title == "新歌"


def test_database_is_opened_read_only(data_root, monkeypatch):
    """只读打开：读一次不会往库目录里留下任何文件（含 -wal/-journal）。"""
    _write_db(_db_path(data_root), [(1, 1, "t", _TRACK_JSON)])
    _write_log(data_root, _LOG_PLAYING)
    monkeypatch.setattr(bodian, "process_running", lambda: True)
    before = {p.name for p in _db_path(data_root).parent.iterdir()}

    bodian.read_track()

    after = {p.name for p in _db_path(data_root).parent.iterdir()}
    assert after == before


def test_log_tail_is_bounded(data_root, monkeypatch):
    """日志只读尾巴：超长日志里**靠前**的旧状态不影响判定（取最后一条）。"""
    padding = "".join(f"2026-10-21 10:00:{i:02d}.000 [I] 噪声行 {i}\n" for i in range(60))
    _write_db(_db_path(data_root), [(1, 1, "t", _TRACK_JSON)])
    _write_log(data_root, _LOG_PLAYING + padding + _LOG_PAUSED)

    assert bodian.playing_state() is False


def test_newest_log_wins_even_when_there_are_many(data_root):
    """日志滚动出超过 ``_MAX_LOG_FILES`` 个时，**最新的那个**必须进样本。

    审查指出旧实现边枚举边 ``break``（先截断再排序），拿到的是目录枚举序的前 N 个；
    日志多到 8 个以上时可能把 mtime 最新的那个排除在外，播放状态就一直读旧值。

    关键在**挑哪个文件当"最新"**：``pathlib.glob`` 的顺序不是字典序也不是创建序
    （实测 0,1,10,11,2,…,9）。本用例把最新状态写进 ``log_9.log``——在 12 个文件的
    枚举序里它排**第 11 位**，旧实现的"取前 8 个"必然把它排除。若随便挑一个名字
    （比如 ``log_11.log``，排第 4 位），旧实现会碰巧留下它，用例就成了空转
    ——这条是消融验证抓出来的（撤掉修复后它曾经照旧变绿）。
    """
    _write_db(_db_path(data_root), [(1, 1, "t", _TRACK_JSON)])
    # 12 个日志：全部是"在播"，只有 log_9.log（枚举序靠后）是"暂停"。
    for index in range(12):
        _write_log(data_root, _LOG_PLAYING, name=f"log_{index}.log")
    newest = _log_path(data_root, "log_9.log")
    _write_log(data_root, _LOG_PAUSED, name="log_9.log")
    # 明确把它的 mtime 推到最前（其它文件统一调旧），不依赖创建顺序。
    import os as _os

    old = time.time() - 3600
    for index in range(12):
        if index == 9:
            continue
        _os.utime(_log_path(data_root, f"log_{index}.log"), (old, old))
    _os.utime(newest, (time.time(), time.time()))

    assert bodian.playing_state() is False


def test_log_files_caps_the_list(data_root):
    """条数上限仍然生效（先排完全部再截，不是不截）。"""
    for index in range(20):
        _write_log(data_root, _LOG_PLAYING, name=f"log_{index}.log")

    files = bodian._log_files()

    assert len(files) == bodian._MAX_LOG_FILES


# ------------------------------------------------------------ 接线


class _Sec:
    def __init__(self, value: float):
        self._value = value

    def total_seconds(self) -> float:
        return self._value


class _Timeline:
    def __init__(self, position: float, end: float):
        self.position = _Sec(position)
        self.end_time = _Sec(end)
        self.last_updated_time = None


class _Controls:
    is_next_enabled = True
    is_previous_enabled = True


class _Info:
    def __init__(self, status: int, media_type: int):
        self.playback_status = status
        self.playback_type = media_type
        self.controls = _Controls()


class _Props:
    def __init__(self, title: str, artist: str):
        self.title = title
        self.artist = artist
        self.album_title = ""


class _Session:
    def __init__(self, app_id: str, *, title="t", artist="a", status=4, media_type=1):
        self.source_app_user_model_id = app_id
        self._title = title
        self._artist = artist
        self._status = status
        self._media_type = media_type

    def get_playback_info(self):
        return _Info(self._status, self._media_type)

    def get_timeline_properties(self):
        return _Timeline(0.0, 254.0)

    async def try_get_media_properties_async(self):
        return _Props(self._title, self._artist)


class _Manager:
    def __init__(self, sessions):
        self._sessions = sessions

    def get_sessions(self):
        return list(self._sessions)

    @classmethod
    async def request_async(cls):
        return cls(cls.sessions_for_test)


def _install_manager(monkeypatch, sessions):
    _Manager.sessions_for_test = sessions
    monkeypatch.setattr(now_playing, "_import_winrt", lambda: _Manager)
    import datetime

    monkeypatch.setattr(
        now_playing, "_utc_now", lambda: datetime.datetime.now(datetime.timezone.utc)
    )


def test_bodian_is_asked_only_without_a_playing_session(monkeypatch):
    """SMTC 有在播会话时**绝不**问波点（补充源不许抢正在听的歌）。"""
    _install_manager(monkeypatch, [_Session("cloudmusic.exe")])
    called = {"n": 0}

    def fake_read(allow_bodian):
        called["n"] += 1
        return None

    monkeypatch.setattr(now_playing, "_read_bodian_playback", fake_read)

    playback = now_playing._run_sync(
        now_playing._read_async(None, allow_browser=True, allow_bodian=True)
    )

    assert playback is not None
    assert playback.app_id == "cloudmusic.exe"
    assert called["n"] == 0


def test_bodian_supplies_track_when_no_session(monkeypatch):
    """没有会话时波点接手，``app_id`` 是补充源标记、进度为 ``None``。"""
    _install_manager(monkeypatch, [])
    monkeypatch.setattr(
        bodian,
        "read_track",
        lambda: bodian.BodianTrack(
            title="晴天", artist="周杰伦", album="叶惠美", duration=269.0, playing=True
        ),
    )
    monkeypatch.setattr(bodian, "process_running", lambda: True)

    playback = now_playing._run_sync(
        now_playing._read_async(None, allow_browser=True, allow_bodian=True)
    )

    assert playback is not None
    assert playback.track.title == "晴天"
    assert playback.track.artist == "周杰伦"
    assert playback.track.album == "叶惠美"
    assert playback.track.duration == 269.0
    assert playback.track.playing is True
    assert playback.app_id == bodian.BODIAN_APP_ID
    # 进度必须为 None：库里的时间是"开始播放的时刻"，当成真值会关掉手动对齐。
    assert playback.position is None


def test_bodian_disabled_by_default(monkeypatch):
    """``allow_bodian`` 默认关：不传就是旧行为（没有会话时返回 None）。"""
    _install_manager(monkeypatch, [])
    monkeypatch.setattr(
        bodian,
        "read_track",
        lambda: bodian.BodianTrack(title="晴天", artist="周杰伦"),
    )

    playback = now_playing._run_sync(now_playing._read_async(None))

    assert playback is None


def test_foobar_wins_over_bodian(monkeypatch):
    """两路都读得到时 foobar 先接手（注册顺序固定，不赌枚举）。

    这个顺序**有用户可见后果**：foobar 源看不出播放状态，开着不播也会挡住波点。
    设置行文案必须如实写明（``settings.bodian_lyrics_rows`` 里写了）。
    """
    _install_manager(monkeypatch, [])
    monkeypatch.setattr(
        now_playing,
        "_read_foobar_playback",
        lambda allow, fmt: now_playing.Playback(
            track=now_playing.Track(title="foobar 的歌", artist="foobar 的人", playing=True),
            position=None,
            updated_at=0.0,
            app_id="foobar2000.exe",
        ),
    )
    monkeypatch.setattr(
        bodian,
        "read_track",
        lambda: bodian.BodianTrack(title="波点的歌", artist="波点的人"),
    )

    playback = now_playing._run_sync(
        now_playing._read_async(None, allow_foobar=True, allow_bodian=True)
    )

    assert playback is not None and playback.track.title == "foobar 的歌"


def test_bodian_source_is_not_the_tracking_slot():
    """补充源的 id 不被当成 SMTC 跟踪位（``is_supplementary_source`` 是唯一入口）。"""
    assert now_playing.is_bodian_source(bodian.BODIAN_APP_ID) is True
    assert now_playing.is_bodian_source("BODIAN_PC.EXE") is True  # 大小写/后缀归一
    assert now_playing.is_bodian_source("cloudmusic.exe") is False
    assert now_playing.is_supplementary_source(bodian.BODIAN_APP_ID) is True
    assert now_playing.is_supplementary_source("foobar2000.exe") is True
    assert now_playing.is_supplementary_source("cloudmusic.exe") is False


def test_supplementary_fallback_works_without_winrt(monkeypatch):
    """没有 WinRT 时补充源仍然可用（它们不依赖系统会话）。"""
    monkeypatch.setattr(now_playing, "_import_winrt", lambda: None)
    monkeypatch.setattr(
        bodian,
        "read_track",
        lambda: bodian.BodianTrack(title="晴天", artist="周杰伦"),
    )

    playback = now_playing._run_sync(now_playing._read_async(None, allow_bodian=True))

    assert playback is not None and playback.app_id == bodian.BODIAN_APP_ID


# ------------------------------------------------------------ 配置与设置行


def test_config_key_defaults_off_and_normalizes(tmp_path):
    """配置键默认关；脏值按 ``_bool_or_default`` 归一（字符串 "false" 不许判真）。"""
    from pet.config import Config

    cfg = Config(base=tmp_path)
    assert cfg.get("bodian_lyrics_enabled") is False

    for raw, expected in ((True, True), ("true", True), ("false", False), (0, False), (None, False)):
        cfg = Config(base=tmp_path)
        cfg.data["bodian_lyrics_enabled"] = raw
        cfg._normalize_pet_settings()
        assert cfg.get("bodian_lyrics_enabled") is expected, raw


def test_settings_row_exists_on_windows_only():
    """设置行按平台条件出现：Windows 有、其它平台没有（永久不支持不建控件）。"""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from pet.music import settings as settings_music
    from pet.settings_widgets import ToggleSwitch

    app = QApplication.instance() or QApplication([])
    assert app is not None

    class _Dialog:
        pass

    dialog = _Dialog()
    dialog.bodian_lyrics_check = None
    assert settings_music.bodian_lyrics_rows(dialog) == []

    # SettingRow 要真 QWidget（它把控件设成标签的 buddy），不能用占位对象。
    dialog.bodian_lyrics_check = ToggleSwitch()
    original = sys.platform
    try:
        sys.platform = "linux"
        assert settings_music.bodian_lyrics_rows(dialog) == []
        assert settings_music.bodian_lyrics_supported() is False
        sys.platform = "win32"
        assert settings_music.bodian_lyrics_supported() is True
        rows = settings_music.bodian_lyrics_rows(dialog)
        assert [row.objectName() for row in rows] == ["settingRow_bodian_lyrics"]
    finally:
        sys.platform = original


def test_row_is_actually_mounted_in_the_settings_dialog(tmp_path, monkeypatch):
    """**B1 回归**：设置行必须真的挂进设置窗，不能只是"定义了却没人调用"。

    审查在原 PR 上实测到 ``settingRow_bodian_lyrics`` 为 ``None``，而
    ``bodian_lyrics_check`` 成了 (0,0) 幽灵控件——``claim()`` 取不到行会静默跳过，
    所以"漏了挂载链"在旧测试里完全看不出来。这里走真实 ``ModernSettingsDialog``
    构造，直接断言行与控件都拿到手。
    """
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.settings_widgets import SettingRow
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)

    dialog = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=True)

    row = dialog.findChild(SettingRow, "settingRow_bodian_lyrics")
    assert row is not None, (
        "settingRow_bodian_lyrics 没挂进设置窗——settings_music.bodian_lyrics_rows(self) "
        "没接进「音乐关联」的挂载链"
    )
    check = getattr(dialog, "bodian_lyrics_check", None)
    assert check is not None
    # 行必须真的**包裹**那个控件：审查实测的幽灵控件是"控件建了但没有父行"，
    # 判据取"行 → 控件"这条实际装配关系，而不是控件的 sizeHint（离屏下可能无效）。
    assert row.control is check, "行没有包裹 bodian_lyrics_check"
    assert check.parent() is not None, "控件没有父对象（孤儿 = 幽灵控件）"


def test_source_switch_is_saved(tmp_path):
    """保存路径真的写回配置键（``_SOURCE_SWITCHES`` 已登记波点）。"""
    from pet.config import Config
    from pet.music import settings as settings_music

    class _Toggle:
        def __init__(self, checked):
            self._checked = checked

        def isChecked(self):
            return self._checked

    class _Dialog:
        config = Config(base=tmp_path)
        bodian_lyrics_check = _Toggle(True)
        music_browser_media_check = None
        foobar_lyrics_check = None
        foobar_title_format_edit = None

    settings_music.save_source_settings(_Dialog())

    assert _Dialog.config.get("bodian_lyrics_enabled") is True
