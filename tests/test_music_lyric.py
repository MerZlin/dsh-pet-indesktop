# -*- coding: utf-8 -*-
"""歌词功能的离线单元测试。

全部用例都不联网：网络部分用固定样本注入，只验证解析、匹配与进度跟踪逻辑。
"""
from __future__ import annotations

import http.client
from email.message import Message
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from pet.music import lyric as music_lyric
from pet.music.lyric import Lyrics, LyricLine, parse_lrc
from pet.music.lyric_controller import LyricTracker


@pytest.fixture(scope="module")
def qapp():
    """真窗口用例需要 QApplication（其余用例只用 QObject 替身）。"""
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- parse_lrc


def test_parse_lrc_basic():
    lines = parse_lrc("[00:01.00]第一句\n[00:05.50]第二句")
    assert [(round(x.at, 2), x.text) for x in lines] == [
        (1.0, "第一句"),
        (5.5, "第二句"),
    ]


def test_parse_lrc_skips_tag_only_lines():
    """无时间戳的标签行（[ti:]/[ar:] 等）按 LRC 规范跳过。"""
    text = "[ti:歌名]\n[ar:歌手]\n[al:专辑]\n[by:某人]\n[00:01.00]正文"
    lines = parse_lrc(text)
    assert len(lines) == 1
    assert lines[0].text == "正文"


def test_parse_lrc_keeps_timestamped_metadata_lines():
    """带时间戳的元信息行**不过滤**——这是产品上明确接受的行为。

    QQ音乐歌词开头就是「曲名 - 歌手」和「词：xxx」，预期会显示出来。
    """
    text = "[00:00.00]曲名 - 歌手\n[00:05.55]词：方文山\n[00:22.23]天涯的尽头是风沙"
    lines = parse_lrc(text)
    assert [x.text for x in lines] == ["曲名 - 歌手", "词：方文山", "天涯的尽头是风沙"]


def test_parse_lrc_multiple_stamps_on_one_line():
    """一行多个时间戳应展开成多条。"""
    lines = parse_lrc("[00:01.00][00:03.00]重复的一句")
    assert [(round(x.at, 2), x.text) for x in lines] == [
        (1.0, "重复的一句"),
        (3.0, "重复的一句"),
    ]


def test_parse_lrc_fraction_digits():
    """小数位按实际长度换算：1 位=十分之一秒，2 位=厘秒，3 位=毫秒。"""
    lines = parse_lrc("[00:10.5]a\n[00:20.25]b\n[00:30.125]c")
    assert [round(x.at, 3) for x in lines] == [10.5, 20.25, 30.125]


def test_parse_lrc_applies_offset():
    """[offset:] 以毫秒计，需叠加到所有时间点上。"""
    lines = parse_lrc("[offset:500]\n[00:01.00]正文")
    assert round(lines[0].at, 2) == 1.5
    negative = parse_lrc("[offset:-200]\n[00:01.00]正文")
    assert round(negative[0].at, 2) == 0.8


def test_parse_lrc_offset_never_goes_negative():
    lines = parse_lrc("[offset:-5000]\n[00:01.00]正文")
    assert lines[0].at == 0.0


def test_parse_lrc_keeps_empty_interlude_lines():
    """间奏行（有时间戳、无文本）保留，供上层决定显示策略。"""
    lines = parse_lrc("[00:01.00]a\n[00:05.00]\n[00:09.00]b")
    assert len(lines) == 3
    assert lines[1].text == ""


def test_parse_lrc_sorted_by_time():
    lines = parse_lrc("[00:10.00]晚\n[00:02.00]早")
    assert [x.text for x in lines] == ["早", "晚"]


def test_parse_lrc_empty_input():
    assert parse_lrc("") == []
    assert parse_lrc("\n\n") == []


# ---------------------------------------------------------------- 曲目匹配


def test_name_matches_exact_and_substring():
    assert music_lyric._name_matches("周杰伦", "周杰伦")
    # 接口常返回「周杰伦/Jay Chou」这种拼接形式
    assert music_lyric._name_matches("周杰伦/Jay Chou", "周杰伦")
    assert music_lyric._name_matches("Jay Chou", "jay chou")


def test_name_matches_ignores_whitespace():
    assert music_lyric._name_matches("YOASOBI", "Yo Asobi")


def test_name_matches_rejects_unrelated():
    assert not music_lyric._name_matches("其他歌手", "周杰伦")
    assert not music_lyric._name_matches("", "周杰伦")
    assert not music_lyric._name_matches("周杰伦", "")


# ---------------------------------------------------------------- 取词流程


def test_fetch_lyrics_prefers_higher_priority_source(monkeypatch):
    """三源并发发起，但结果要取优先级最高的那个（QQ音乐 > lrclib）。"""
    qq_lines = [LyricLine(1.0, "来自QQ")]
    lrclib_lines = [LyricLine(1.0, "来自lrclib")]

    monkeypatch.setattr(music_lyric, "_SOURCES", (
        ("qq", lambda t, a, **k: qq_lines),
        ("lrclib", lambda t, a, **k: lrclib_lines),
    ))
    monkeypatch.setattr(music_lyric, "_write_cache", lambda *a, **k: None)
    monkeypatch.setattr(music_lyric, "_read_cache", lambda *a, **k: None)

    assert music_lyric.fetch_lyrics("歌", "手") == qq_lines


def test_fetch_lyrics_falls_back_to_lower_priority(monkeypatch):
    """高优先级源无结果时，采用低优先级源的结果。"""
    lrclib_lines = [LyricLine(2.0, "来自lrclib")]

    monkeypatch.setattr(music_lyric, "_SOURCES", (
        ("qq", lambda t, a, **k: None),
        ("lrclib", lambda t, a, **k: lrclib_lines),
    ))
    monkeypatch.setattr(music_lyric, "_write_cache", lambda *a, **k: None)
    monkeypatch.setattr(music_lyric, "_read_cache", lambda *a, **k: None)

    assert music_lyric.fetch_lyrics("歌", "手") == lrclib_lines


def test_fetch_lyrics_survives_source_exception(monkeypatch):
    """某个源抛异常不能拖垮整体，应继续尝试后续源。"""
    def boom(title, artist, **kwargs):
        raise RuntimeError("接口炸了")

    expected = [LyricLine(1.0, "ok")]
    monkeypatch.setattr(music_lyric, "_SOURCES", (
        ("boom", boom), ("good", lambda t, a, **k: expected),
    ))
    monkeypatch.setattr(music_lyric, "_write_cache", lambda *a, **k: None)
    monkeypatch.setattr(music_lyric, "_read_cache", lambda *a, **k: None)

    assert music_lyric.fetch_lyrics("歌", "手") == expected


def test_fetch_lyrics_returns_none_when_all_fail(monkeypatch):
    monkeypatch.setattr(music_lyric, "_SOURCES", (("none", lambda t, a, **k: None),))
    monkeypatch.setattr(music_lyric, "_read_cache", lambda *a, **k: None)
    assert music_lyric.fetch_lyrics("歌", "手") is None


def test_fetch_lyrics_empty_query_returns_none():
    assert music_lyric.fetch_lyrics("", "") is None


# ---------------------------------------------------------------- 缓存


def test_cache_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(music_lyric, "cache_dir", lambda: tmp_path)
    lines = (LyricLine(1.5, "甲"), LyricLine(3.0, "乙"))
    music_lyric._write_cache("歌", "手", Lyrics(lines=lines))
    got = music_lyric._read_cache("歌", "手")
    assert got is not None
    assert got.lines == lines
    assert got.instrumental is False


def test_cache_stores_instrumental_marker(monkeypatch, tmp_path):
    """纯音乐标记也要落缓存，否则每次播放都要重新联网判定。"""
    monkeypatch.setattr(music_lyric, "cache_dir", lambda: tmp_path)
    music_lyric._write_cache("潮鳴り", "Key Sounds Label", Lyrics(instrumental=True))
    got = music_lyric._read_cache("潮鳴り", "Key Sounds Label")
    assert got is not None
    assert got.instrumental is True
    assert got.lines == ()


def test_cache_key_is_case_and_space_insensitive(monkeypatch, tmp_path):
    """同一首歌因大小写/空格差异不应产生两份缓存。"""
    monkeypatch.setattr(music_lyric, "cache_dir", lambda: tmp_path)
    music_lyric._write_cache("Song", "Artist", Lyrics(lines=(LyricLine(1.0, "x"),)))
    assert music_lyric._read_cache("song", "artist") is not None
    assert music_lyric._read_cache("  SONG  ", " ARTIST ") is not None


def test_cache_version_mismatch_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setattr(music_lyric, "cache_dir", lambda: tmp_path)
    music_lyric._write_cache("歌", "手", Lyrics(lines=(LyricLine(1.0, "x"),)))
    path = next(tmp_path.glob("*.json"))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["v"] = 999
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert music_lyric._read_cache("歌", "手") is None


def test_cache_corrupt_file_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setattr(music_lyric, "cache_dir", lambda: tmp_path)
    music_lyric._write_cache("歌", "手", Lyrics(lines=(LyricLine(1.0, "x"),)))
    next(tmp_path.glob("*.json")).write_text("{ 坏掉的 json", encoding="utf-8")
    assert music_lyric._read_cache("歌", "手") is None


def test_cache_prune_evicts_oldest(monkeypatch, tmp_path):
    """超过上限时按修改时间淘汰最旧的。"""
    monkeypatch.setattr(music_lyric, "cache_dir", lambda: tmp_path)
    import os
    import time as _time

    for i in range(5):
        music_lyric._write_cache(f"歌{i}", "手", Lyrics(lines=(LyricLine(1.0, "x"),)))
        # 把刚写入的文件 mtime 往前拨，制造"越早写的越旧"的顺序
        newest = max(tmp_path.glob("*.json"), key=lambda p: p.stat().st_mtime)
        stamp = _time.time() - (5 - i) * 10
        os.utime(newest, (stamp, stamp))

    music_lyric._prune_cache(limit=2)
    assert len(list(tmp_path.glob("*.json"))) == 2


# ---------------------------------------------------------------- LyricTracker


LINES = [
    LyricLine(0.0, "A"),
    LyricLine(5.0, "B"),
    LyricLine(10.0, "C"),
    LyricLine(15.0, "D"),
]


def test_tracker_local_clock_mode():
    """无真实进度：从切歌时刻起本地累加。"""
    t = LyricTracker()
    t.load(LINES, now=100.0, position=None)
    assert t.text_at(t.advance(100.0)) == "A"
    assert t.advance(102.0) == -1        # 同一句：不重复上报
    assert t.text_at(t.advance(105.0)) == "B"
    assert t.text_at(t.advance(110.0)) == "C"
    assert t.text_at(t.advance(116.0)) == "D"


def test_tracker_reported_position_mode():
    """有真实进度：直接采用，且能跟随跳转/拖拽。"""
    t = LyricTracker()
    t.load(LINES, now=200.0, position=11.0)
    assert t.text_at(t.advance(200.0)) == "C"
    assert t.text_at(t.advance(201.0, reported=16.0)) == "D"
    assert t.text_at(t.advance(202.0, reported=2.0)) == "A"


def test_tracker_pause_freezes_position():
    t = LyricTracker()
    t.load(LINES, now=300.0, position=None)
    t.advance(306.0)
    assert t.position(306.0) == 6.0
    t.set_paused(True, now=306.0)
    # 暂停 14 秒，位置不能前进
    assert t.position(320.0) == 6.0
    t.set_paused(False, now=320.0)
    # 恢复 4 秒后应到 10 秒处
    assert round(t.position(324.0), 3) == 10.0


def test_tracker_line_before_first_line():
    t = LyricTracker()
    t.load(LINES, now=0.0, position=None)
    assert t.line_at(-5.0) == -1


def test_tracker_keeps_last_line_past_end():
    t = LyricTracker()
    t.load(LINES, now=0.0, position=None)
    assert t.line_at(999.0) == len(LINES) - 1


def test_tracker_no_lyrics_returns_minus_one():
    t = LyricTracker()
    assert t.line_at(10.0) == -1
    assert t.has_lyrics is False
    assert t.advance(10.0) == -1


def test_tracker_reset_clears_state():
    t = LyricTracker()
    t.load(LINES, now=0.0, position=None)
    t.advance(6.0)
    t.reset()
    assert t.has_lyrics is False
    assert t.index == -1
    assert t.position(10.0) is None


def test_tracker_position_unknown_when_not_loaded():
    t = LyricTracker()
    assert t.position(5.0) is None


# ---------------------------------------------------------------- 歌词提前量


def test_lead_makes_lyrics_early_not_late():
    """用户明确要求：宁可歌词早，不能接受歌词晚。

    带 1 秒提前量时，位置 4.0s 就该显示 5.0s 那句，而不是等真正唱到。
    """
    from pet.music.lyric_controller import LYRIC_LEAD_SECONDS

    t = LyricTracker()
    t.load(LINES, now=0.0, position=None)
    assert t.lead == LYRIC_LEAD_SECONDS

    # 位置 4.0s：句 B 在 5.0s。有提前量 → 已显示 B；无提前量 → 仍是 A。
    assert t.text_at(t.line_at(4.0)) == "B"
    t.lead = 0.0
    assert t.text_at(t.line_at(4.0)) == "A"


def test_lead_applies_to_reported_position_mode():
    """提前量对有真实进度的播放器同样生效。"""
    t = LyricTracker()
    t.load(LINES, now=0.0, position=9.5)
    # 9.5 + 1.0 提前量 = 10.5 → 落在 C(10.0)
    assert t.text_at(t.line_at(9.5)) == "C"


def test_lead_does_not_show_line_far_in_future():
    """提前量只有 1 秒，不能把很靠后的句子提前拽出来。"""
    t = LyricTracker()
    t.load(LINES, now=0.0, position=None)
    assert t.text_at(t.line_at(0.0)) == "A"


# ---------------------------------------------------------------- 常驻标题 + 歌词


from PySide6.QtCore import QObject, Qt


class _FakeWin(QObject):
    """最小窗口替身：只提供控制器会用到的那几个 seam。

    必须是 QObject——MusicLyricController 继承 QObject，构造时把它当作 parent。
    """

    _alert_current = None
    _sticky_bubble_active = False
    _speech_bubble = None

    def __init__(self):
        super().__init__()
        self.shown: list[str] = []

    def show_bubble(self, text, duration_ms=3200, subtitle=None, **kw):
        # 记录 (标题, 正文)：标题走 subtitle，正文是歌词。与 split_bubble_text 同序。
        self.shown.append((subtitle or "", text))

    def hold_bubble(self, seconds=0.0):
        pass

    def isVisible(self):
        return True


def test_title_shows_immediately_on_track_change():
    """切歌瞬间就出标题，不等取词——填上取词那几秒的空窗。"""
    from pet.music.lyric_controller import MusicLyricController

    win = _FakeWin()
    ctrl = MusicLyricController(win)
    ctrl._current_key = None
    ctrl._primed = True          # 已过首次边界，不是"开启时已在播"那首
    playback = type("P", (), {"position": None, "updated_at": 0.0})()

    ctrl._start_track(("夜曲", "周杰伦"), "夜曲", "周杰伦", playback, 100.0)
    assert win.shown == [("", "我在唱《夜曲》")], win.shown
    assert ctrl._title_line == "我在唱《夜曲》"


def test_title_persists_after_lyrics_arrive():
    """歌词到位后，标题必须仍在第一行（这就是本次需求的核心）。"""
    from pet.music.lyric_controller import MusicLyricController

    win = _FakeWin()
    ctrl = MusicLyricController(win)
    ctrl._current_key = ("t", "a")
    ctrl._primed = True
    ctrl._title_line = "我在唱《t》"
    ctrl._on_lyrics_ready(("t", "a"), Lyrics(lines=tuple(LINES)))

    assert win.shown, "应至少推送过一次"
    subtitle, text = win.shown[-1]
    # 标题固定走 subtitle（气泡第一行），歌词走正文——这就是需求的核心。
    assert subtitle == "我在唱《t》", subtitle
    assert text.strip() in {line.text for line in LINES}, text


def test_title_kept_when_song_has_no_lyrics():
    """无词歌也要常驻标题，不能把气泡清空或让歌名消失。"""
    from pet.music.lyric_controller import MusicLyricController

    win = _FakeWin()
    ctrl = MusicLyricController(win)
    ctrl._current_key = ("x", "y")
    ctrl._title_line = "我在唱《x》"
    ctrl._on_lyrics_ready(("x", "y"), None)

    assert ("x", "y") in ctrl._no_lyric_until, "失败应进重试冷却（不是永久拉黑）"
    assert win.shown[-1] == ("", "我在唱《x》"), win.shown[-1]
    assert ctrl._title_line == "我在唱《x》"


def test_no_lyric_retry_after_cooldown_not_permanent_blacklist():
    """一次失败只冷却 5 分钟（歌词族终审应修 3）：冷却内不重试，冷却外允许。"""
    import time as _time

    from pet.music.lyric_controller import MusicLyricController

    win = _FakeWin()
    ctrl = MusicLyricController(win)
    ctrl._current_key = ("x", "y")
    ctrl._title_line = "我在唱《x》"
    ctrl._on_lyrics_ready(("x", "y"), None)
    until = ctrl._no_lyric_until[("x", "y")]
    assert _time.monotonic() < until <= _time.monotonic() + 300.5

    # 冷却内：_start_track 早退，不发取词
    started = []
    ctrl._loading.discard(("x", "y"))
    ctrl._lyric_query = lambda *a, **k: started.append(1) or ("t", "a")
    ctrl._start_track(("x", "y"), "x", "y", _playback(None), _time.monotonic())
    assert started == [], "冷却内不许重复取词"

    # 冷却外：允许重试（网络恢复可自愈）
    ctrl._no_lyric_until[("x", "y")] = _time.monotonic() - 1.0
    ctrl._primed = True
    ctrl._start_track(("x", "y"), "x", "y", _playback(None), _time.monotonic())
    assert started == [1], "冷却外必须允许重试"


def test_title_goes_into_body_when_no_lyric():
    """没有歌词时必须让标题走正文。

    气泡在正文为空时直接不渲染，若标题只放 subtitle，取词那几秒会一片空白。
    """
    from pet.music.lyric_controller import split_bubble_text

    # 无歌词：返回 (subtitle="", text=标题) —— 标题走正文，气泡才会渲染
    assert split_bubble_text("我在唱《x》", "") == ("", "我在唱《x》")
    assert split_bubble_text("我在唱《x》") == ("", "我在唱《x》")
    # 有歌词：标题走 subtitle，歌词走正文
    assert split_bubble_text("我在唱《x》", "一句歌词") == ("我在唱《x》", "一句歌词")
    # 都没有则都为空
    assert split_bubble_text("", "") == ("", "")


# ---------------------------------------------------------------- 纯音乐


def test_looks_instrumental_detects_placeholder():
    """平台对纯音乐给的占位文案要能识别出来。"""
    from pet.music.lyric import LyricLine, _looks_instrumental

    # QQ音乐实测的两种占位文案
    assert _looks_instrumental([LyricLine(0.0, "此歌曲为没有填词的纯音乐，请您欣赏")])
    assert _looks_instrumental([LyricLine(1.58, "纯音乐，请欣赏")])
    assert _looks_instrumental([LyricLine(0.0, "暂无歌词")])
    # 英文
    assert _looks_instrumental([LyricLine(0.0, "Instrumental")])


def test_looks_instrumental_ignores_real_lyrics():
    """真歌词不能被误判，哪怕某一行提到「纯音乐」。"""
    from pet.music.lyric import LyricLine, _looks_instrumental

    # 多行真歌词
    assert not _looks_instrumental([
        LyricLine(0.0, "一群嗜血的蚂蚁"),
        LyricLine(5.0, "被腐肉所吸引"),
        LyricLine(10.0, "我面无表情看孤独的风景"),
    ])
    # 行数少但内容是正常歌词
    assert not _looks_instrumental([LyricLine(0.0, "这首纯音乐真好听")])
    # 空列表不算纯音乐
    assert not _looks_instrumental([])


def test_lyrics_dataclass_bool_and_len():
    from pet.music.lyric import Lyrics

    empty = Lyrics()
    assert not empty and len(empty) == 0
    instr = Lyrics(instrumental=True)
    # 纯音乐是**有效结果**（没有行但标记为纯音乐），布尔值必须为 True——
    # 否则 `if cached:` 判假，纯音乐缓存永远读不中，每次切回都重打三个源。
    assert instr, "纯音乐是有效结果，布尔值应为 True"
    assert instr.instrumental
    with_lines = Lyrics(lines=(LyricLine(1.0, "x"),))
    assert with_lines and len(with_lines) == 1


def test_instrumental_hints_fit_persona():
    """提示文案要够多，且符合女仆人设（称主人 / 用人家 / 带「～」）。"""
    from pet.music.lyric_controller import INSTRUMENTAL_HINTS

    assert len(INSTRUMENTAL_HINTS) >= 4, "至少 4 条"
    assert len(set(INSTRUMENTAL_HINTS)) == len(INSTRUMENTAL_HINTS), "不应重复"
    joined = "".join(INSTRUMENTAL_HINTS)
    assert "主人" in joined or "人家" in joined, "应体现女仆口吻"


def test_pick_instrumental_hint_returns_member():
    from pet.music.lyric_controller import (
        INSTRUMENTAL_HINTS,
        pick_instrumental_hint,
    )

    for _ in range(20):
        assert pick_instrumental_hint() in INSTRUMENTAL_HINTS


def test_now_listening_template():
    from pet.music.lyric_controller import NOW_LISTENING_TEMPLATE

    assert NOW_LISTENING_TEMPLATE.format(title="潮鳴り") == "正在听《潮鳴り》"


# ---------------------------------------------------------------- 让路给别的弹窗


class _FakeSubtitle:
    def __init__(self):
        self._text = ""

    def text(self):
        return self._text

    def set(self, value):
        self._text = value


class _FakeBubble:
    """最小气泡替身：只暴露让路检测需要的东西。"""

    def __init__(self):
        self._subtitle_label = _FakeSubtitle()
        self._interactive_active = False
        self._visible = True
        self.moved = []
        self._pos = type("P", (), {"x": lambda s: 0, "y": lambda s: 0})()

    def isVisible(self):
        return self._visible

    def move(self, pos):
        self.moved.append(pos)


class _WinWithBubble(_FakeWin):
    def __init__(self):
        super().__init__()
        self._speech_bubble = _FakeBubble()


def test_yields_when_other_bubble_takes_over():
    """点击台词/被动弹窗占用时，歌词必须让路，不能被每秒重发盖掉。"""
    from pet.music.lyric_controller import LYRIC_YIELD_SECONDS, MusicLyricController

    win = _WinWithBubble()
    ctrl = MusicLyricController(win)
    ctrl._title_line = "我在唱《夜曲》"

    # 我们自己的歌词还在显示：不该让路
    win._speech_bubble._subtitle_label.set("我在唱《夜曲》")
    assert ctrl._yield_active() is False

    # 别的弹窗接管（没有我们的副标题）：开始让路
    win._speech_bubble._subtitle_label.set("")
    assert ctrl._yield_active() is True
    assert ctrl._lyric_yield_until > 0
    assert ctrl._lyric_yield_until >= time.monotonic() + LYRIC_YIELD_SECONDS - 0.5


def test_yield_blocks_show_while_active():
    from pet.music.lyric_controller import MusicLyricController

    win = _WinWithBubble()
    ctrl = MusicLyricController(win)
    ctrl._title_line = "我在唱《夜曲》"
    ctrl._lyric_yield_until = time.monotonic() + 5.0   # 正在让路

    ctrl._show("一句歌词", title="我在唱《夜曲》", force=True)
    assert win.shown == [], "让路期间不应往气泡写任何东西"


def test_yield_expires_and_lyrics_resume():
    """让路到期后歌词恢复显示。"""
    from pet.music.lyric_controller import MusicLyricController

    win = _WinWithBubble()
    ctrl = MusicLyricController(win)
    ctrl._title_line = "我在唱《夜曲》"
    win._speech_bubble._subtitle_label.set("我在唱《夜曲》")
    ctrl._lyric_yield_until = time.monotonic() - 0.1   # 已过期

    ctrl._show("一句歌词", title="我在唱《夜曲》", force=True)
    assert win.shown, "让路到期后应恢复显示"
    assert win.shown[-1] == ("我在唱《夜曲》", "一句歌词")


def test_hidden_bubble_is_not_treated_as_taken():
    """气泡不可见（没有内容）不算被抢占，否则开头会被误判让路。"""
    from pet.music.lyric_controller import MusicLyricController

    win = _WinWithBubble()
    ctrl = MusicLyricController(win)
    ctrl._title_line = "我在唱《夜曲》"
    win._speech_bubble._visible = False
    assert ctrl._bubble_taken_by_other() is False


# ------------------------------------------------- 配置的缓存上限真正生效（P2-c）


class _CfgStub:
    """控制器只读的配置面替身（get 语义与 Config 一致）。"""

    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, key, default=None):
        return self.values.get(key, default)


def _only_source(monkeypatch, lines):
    """把三源压成一个立即返回的假源（绕过网络）。"""
    lyrics = Lyrics(lines=tuple(lines))
    monkeypatch.setattr(music_lyric, "_SOURCES", (("qq", lambda t, a, **k: lyrics),))
    monkeypatch.setattr(music_lyric, "_read_cache", lambda *a, **k: None)


def test_fetch_lyrics_prunes_cache_to_given_limit(monkeypatch, tmp_path):
    """``fetch_lyrics(cache_limit=...)`` 的条目上限必须传给淘汰逻辑（P2-c 消费点）。"""
    monkeypatch.setattr(music_lyric, "cache_dir", lambda: tmp_path)
    _only_source(monkeypatch, (LyricLine(1.0, "x"),))

    for i in range(4):
        music_lyric.fetch_lyrics(f"歌{i}", "手", cache_limit=2)

    assert len(list(tmp_path.glob("*.json"))) == 2


def test_fetch_worker_uses_configured_cache_limit(monkeypatch, tmp_path):
    """取词线程把配置里的 ``music_lyric_cache_limit`` 真正交给淘汰（P2-c）。

    回归：该键此前默认值/白名单/归一化齐备，但 ``_prune_cache`` 恒用模块常量
    ``CACHE_LIMIT``，用户手改的上限完全不起作用（死开关）。
    """
    from pet.music.lyric_controller import MusicLyricController

    monkeypatch.setattr(music_lyric, "cache_dir", lambda: tmp_path)
    _only_source(monkeypatch, (LyricLine(1.0, "x"),))

    win = _FakeWin()
    win.cfg = _CfgStub({"music_lyric_cache_limit": 2})
    ctrl = MusicLyricController(win)

    for i in range(4):
        ctrl._fetch_worker((f"t{i}", "a"), f"t{i}", "a")

    assert len(list(tmp_path.glob("*.json"))) == 2


def test_announce_with_empty_title_still_resets_width_lock():
    """无标题曲目也必须复位锁宽标志（锁宽/锁高重置链路依赖它）。

    回归（实审 P2-3）：_width_locked=False 曾排在空标题提前 return 之后，
    无标题曲目沿用上一首列宽、锁高棘轮整会话不复位。
    """
    from pet.music.lyric_controller import MusicLyricController

    ctrl = MusicLyricController(_FakeWin())
    ctrl._width_locked = True
    ctrl._announce("", "")
    assert ctrl._width_locked is False


# ---------------------------------------------------------------- 采样线程生命周期
#
# 背景（fb38824 引入的缺陷）：_stop_sample_thread 把 _sample_thread 置 None 并
# set 了 _sample_stop，但 _sample_stop **没有复位路径**。于是「关闭歌词 → 再打开」
# 之后，新建的采样线程一进 while 就 break，歌词永久不再更新，直到重启桌宠。
# 这两个用例锁住该行为：线程可复用 + 重开后确实还在采样。


class _FakeWinVisible(QObject):
    """带 cfg 的窗口替身——sync_enabled 会经 apply_lead 读 win.cfg。"""

    _alert_current = None
    _sticky_bubble_active = False
    _speech_bubble = None

    def __init__(self):
        super().__init__()
        self.cfg: dict = {}
        self.shown: list[tuple[str, str]] = []

    def show_bubble(self, text, duration_ms=3200, subtitle=None, **kw):
        self.shown.append((subtitle or "", text))

    def hold_bubble(self, seconds=0.0):
        pass

    def isVisible(self):
        return True


def _wait_until(predicate, *, timeout=5.0, interval=0.02):
    """宽预算轮询：CI 慢 runner 是本地数倍慢，禁止固定 sleep 赌时序。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_sample_thread_is_reused_across_disable_enable():
    """关掉歌词再打开，必须复用同一条采样线程，不许再起一条。

    旧线程可能正卡在 WinRT 里，丢掉引用只会让它变成孤儿：之后醒来仍会
    emit，与新线程抢 _sampling 标志（同类教训见 pet/music_detect.py）。
    """
    from pet.music.lyric_controller import MusicLyricController

    win = _FakeWinVisible()
    ctrl = MusicLyricController(win)
    try:
        ctrl.sync_enabled(True)
        first = ctrl._sample_thread
        assert first is not None, "开启后应有采样线程"

        ctrl.sync_enabled(False)
        ctrl.sync_enabled(True)

        assert ctrl._sample_thread is first, (
            "关闭再开启后应复用原采样线程；"
            "新建线程意味着旧线程被丢引用（孤儿），且 _sample_stop 未复位"
        )
    finally:
        ctrl.sync_enabled(False)


class _ParkedSampleThread:
    """「派发已记账、worker 尚未起跑」那一刻的采样线程替身。

    真线程停不进这个窗口（要么还没起跑、要么已进 ``_sample_loop``），而它正是 R02
    的充要条件：``_on_tick`` 已把 ``_sampling`` 记成 True，却没有任何 worker 会去
    消费这一拍。``alive`` 可切；切成 False = 线程在 ``_sample_loop`` 的停止检查处
    break 退出（生产里的结局：退出前不会发任何结果回调）。
    """

    def __init__(self) -> None:
        self.alive = True

    def is_alive(self) -> bool:
        return self.alive


def test_sampling_resumes_after_disable_enable(monkeypatch):
    """关掉歌词再打开，采样必须恢复——不是永久停摆（R02）。

    真实时序：``_on_tick`` 先记 ``_sampling=True`` 再唤醒 worker；worker 若在起跑
    前撞上关闭，就会消费掉唤醒口、在 ``_sample_loop`` 的停止检查处直接 break 退出
    ——**不发任何结果回调**，唯一的常规复位点 ``_on_playback_ready`` 因此永不执行：
    ``_sampling`` 卡在 True，重开后每一拍都在 ``_on_tick`` 第一道闸门返回，唤醒口也
    再没人置位——采样线程永远停在 ``wake.wait()`` 上，歌词再也不更新，直到重启应用。
    """
    from pet.music import now_playing
    from pet.music.lyric_controller import MusicLyricController

    calls = {"n": 0}
    seen: list = []

    def fake_get_now_playing(tracked_app_id=None, **kwargs):
        # **kwargs：采样线程现在还会传 allow_browser（浏览器会话开关）
        calls["n"] += 1
        seen.append(tracked_app_id)
        return None

    monkeypatch.setattr(now_playing, "get_now_playing", fake_get_now_playing)

    win = _FakeWinVisible()
    ctrl = MusicLyricController(win)
    try:
        # 摆出「派发已记账、worker 尚未起跑」：替身顶掉线程且从不跑循环。
        # 此后**不许再手动碰 _sampling**——它卡不卡 True 正是被测行为。
        parked = _ParkedSampleThread()
        ctrl._sample_thread = parked
        ctrl._on_tick()
        assert ctrl._sampling is True, "前提：派发已记账"

        ctrl.sync_enabled(False)
        # worker 此刻才醒：消费掉关闭时置位的唤醒口，看到 stop 就退出（不发回调）。
        ctrl._sample_wake.clear()
        parked.alive = False
        assert ctrl._sampling is False, (
            "关闭必须结清未消费的派发；卡 True 会让重开后的 _on_tick 永远在第一道"
            "闸门返回，唤醒口也再没人置位"
        )

        before = calls["n"]
        ctrl.sync_enabled(True)

        assert _wait_until(lambda: calls["n"] > before), (
            "关闭再开启后采样必须恢复；卡住说明 _sampling 仍卡在 True，"
            "采样线程停在 wake.wait() 上等一个永远不来的唤醒"
        )
        # 采样必须带上"当前跟踪的播放器"，否则会话选取会在两个播放器之间乱跳
        assert seen and seen[0] == "", seen
        assert ctrl._sample_thread is not parked, "替身已死，重开必须重建真线程"
    finally:
        ctrl.sync_enabled(False)


def test_shutdown_leaves_stop_flag_set():
    """shutdown 之后 ``_sample_stop`` 必须保持置位。

    这是「复位 _sample_stop」修复的反向保险：若写成无条件 clear（含关闭分支），
    线程的退出信号就被抹掉了——采样线程退出后会被再次拉起，shutdown 形同虚设。
    直接断言标志位，不依赖 sleep 时序。
    """
    from pet.music.lyric_controller import MusicLyricController

    win = _FakeWinVisible()
    ctrl = MusicLyricController(win)
    ctrl.sync_enabled(True)
    assert not ctrl._sample_stop.is_set(), "运行中不该处于停止态"

    ctrl.shutdown()
    assert ctrl._sample_stop.is_set(), (
        "shutdown 后 _sample_stop 必须仍置位；"
        "被 clear 说明复位逻辑把「关」也一起复掉了"
    )

    # 关闭路径同理：关掉歌词也要留下停止信号。
    ctrl2 = MusicLyricController(_FakeWinVisible())
    ctrl2.sync_enabled(True)
    ctrl2.sync_enabled(False)
    assert ctrl2._sample_stop.is_set(), "sync_enabled(False) 后应保持停止态"


# ------------------------------------------------- 快进 / 中途开始播放的对齐
#
# 背景（2026-09-21 本机实测）：网易云音乐（cloudmusic.exe）通过 SMTC **完全
# 不上报播放进度**——position / end_time 恒为 0.0，播放中、暂停、切歌、快进
# 四种状态下采样结果完全一样。此时控制器只能走 LyricTracker 的本地时钟推算，
# 而本地时钟原理上感知不到快进。因此需要"一次操作即对齐"的手动入口，并修掉
# 由此暴露的两处缺陷：
#   缺陷 1：功能开启时歌已在播 → 连歌名都不显示（整段空白）。
#   缺陷 2：向后拖到第一句歌词之前 → 气泡继续显示旧句。


class _Clock:
    """受控时钟：只替换 monotonic，其余属性透传给真 time 模块。"""

    def __init__(self, now: float = 100.0):
        self.now = now

    def monotonic(self) -> float:
        return self.now

    def __getattr__(self, name):
        return getattr(time, name)


def _patch_clock(monkeypatch, now: float = 100.0) -> _Clock:
    import pet.music.lyric_controller as mod

    clock = _Clock(now)
    monkeypatch.setattr(mod, "time", clock)
    return clock


def _playback(position=None, *, title="t", artist="a", playing=True):
    from pet.music.now_playing import Playback, Track

    return Playback(
        track=Track(title=title, artist=artist, playing=playing),
        position=position,
        updated_at=0.0,
    )


def _aligned_controller(monkeypatch, *, lines=None, position=None, now=100.0):
    """建一个已装载歌词、标题就绪的控制器（取词线程打桩，保持离线）。"""
    import pet.music.lyric_controller as mod

    clock = _patch_clock(monkeypatch, now)
    win = _FakeWin()
    ctrl = mod.MusicLyricController(win)
    monkeypatch.setattr(ctrl, "_fetch_worker", lambda *a, **k: None)
    ctrl._current_key = ("t", "a")
    ctrl._primed = True
    ctrl._title_line = "我在唱《t》"
    ctrl._tracker.lead = 0.0
    ctrl._tracker.load(list(lines if lines is not None else LINES), now=now,
                       position=position)
    return win, ctrl, clock


def test_tracker_reanchor_moves_local_clock():
    """本地时钟模式下重定位：锚点之后的推算必须跟着走。"""
    t = LyricTracker()
    t.lead = 0.0
    t.load(LINES, now=0.0, position=None)
    assert t.position(3.0) == 3.0
    t.reanchor(30.0, now=3.0)
    assert t.position(5.0) == 32.0
    assert t.text_at(t.line_at(t.position(5.0))) == "D"


def test_tracker_reanchor_keeps_frozen_position_while_paused():
    """暂停中对齐：冻结位置也要一起改，否则恢复播放会弹回旧值。"""
    t = LyricTracker()
    t.load(LINES, now=0.0, position=None)
    t.set_paused(True, now=2.0)
    t.reanchor(20.0, now=2.0)
    assert t.position(999.0) == 20.0


def test_tracker_reanchor_does_not_beat_reported_position():
    """报进度的播放器：手动对齐不得覆盖真值。"""
    t = LyricTracker()
    t.load(LINES, now=0.0, position=None)
    t.reanchor(30.0, now=0.0)
    assert t.position(0.0, reported=11.0) == 11.0


def test_tracker_uses_reported_position_flag():
    t = LyricTracker()
    t.load(LINES, now=0.0, position=None)
    assert t.uses_reported_position is False
    t.position(0.0, reported=1.0)
    assert t.uses_reported_position is True
    t.reset()
    assert t.uses_reported_position is False


def test_tracker_line_now_reports_no_line_and_syncs_index():
    """回到第一句之前必须报 -1 **并把 index 同步成 -1**。

    这是缺陷 2 的根：advance() 用 -1 表示"本拍没有变化"，调用方无法区分
    "当前没有行可显示"，于是继续显示上一句；而 _index 不更新又让后续
    每一步都建立在错误状态上。
    """
    t = LyricTracker()
    t.lead = 0.0
    t.load([LyricLine(5.0, "A"), LyricLine(9.0, "B")], now=0.0, position=None)
    assert t.line_now(0.0) == -1
    assert t.index == -1
    assert t.line_now(6.0) == 0
    assert t.index == 0
    # 与 advance() 的区别：同一句也照常返回行号，不做去重
    assert t.line_now(6.5) == 0


def test_tracker_line_now_without_lyrics_is_minus_one():
    t = LyricTracker()
    assert t.line_now(10.0) == -1
    assert t.index == -1


def test_backward_seek_before_first_line_clears_stale_lyric(monkeypatch):
    """缺陷 2 回归：向后拖到第一句之前，气泡必须不再显示旧句。"""
    from pet.music.lyric import LyricLine as _LL

    win, ctrl, clock = _aligned_controller(
        monkeypatch, lines=[_LL(5.0, "A"), _LL(9.0, "B")],
    )
    clock.now = 111.0                      # 本地时钟推到 11s → 第二句
    ctrl._on_playback_ready(_playback(None))
    assert win.shown[-1] == ("我在唱《t》", "B"), win.shown[-1]

    clock.now = 100.0                      # 拖回开头 → 位置 0s，早于第一句
    ctrl._on_playback_ready(_playback(None))
    assert win.shown[-1] == ("", "我在唱《t》"), win.shown[-1]


def test_resync_to_start_realigns_local_clock(monkeypatch):
    """快进之后的救命入口：一键把"现在这句"当作开头。"""
    win, ctrl, clock = _aligned_controller(monkeypatch)
    clock.now = 111.0
    ctrl._on_playback_ready(_playback(None))
    assert ctrl._tracker.text_at(ctrl._tracker.index) == "C"   # LINES: A0 B5 C10 D15

    assert ctrl.resync_to_start() is True
    assert win.shown[-1] == ("我在唱《t》", "A"), win.shown[-1]

    # 对齐后继续按本地时钟推进
    clock.now = 116.0
    ctrl._on_playback_ready(_playback(None))
    assert ctrl._tracker.text_at(ctrl._tracker.index) == "B"


def test_resync_to_line_steps_along_lyrics(monkeypatch):
    """「上一句 / 下一句」按歌词行的时间戳重定位。"""
    win, ctrl, clock = _aligned_controller(monkeypatch)
    clock.now = 111.0
    ctrl._on_playback_ready(_playback(None))
    assert ctrl._tracker.text_at(ctrl._tracker.index) == "C"

    assert ctrl.resync_to_line(1) is True
    assert win.shown[-1] == ("我在唱《t》", "D"), win.shown[-1]

    assert ctrl.resync_to_line(-1) is True
    assert win.shown[-1] == ("我在唱《t》", "C"), win.shown[-1]

    # 越界不动作
    assert ctrl.resync_to_line(99) is False
    assert ctrl.resync_to_line(-99) is False


def test_resync_to_line_lands_exactly_one_line_with_lead(monkeypatch):
    """有提前量时「下一句」也只能走一句。

    查行时会叠加 ``lead``（默认 1 秒，用户可调到 3 秒），所以按行步进必须先把
    提前量减掉——否则行距比提前量短（快歌/说唱）时，按一次会直接跳过一句。
    """
    from pet.music.lyric import LyricLine as _LL

    lines = [_LL(0.0, "A"), _LL(2.0, "B"), _LL(3.0, "C")]
    win, ctrl, clock = _aligned_controller(monkeypatch, lines=lines)
    ctrl._tracker.lead = 1.0                     # 提前量 > 行距
    ctrl._tracker.load(list(lines), now=100.0, position=None)
    clock.now = 100.0
    ctrl._on_playback_ready(_playback(None))
    assert win.shown[-1] == ("我在唱《t》", "A"), win.shown[-1]

    assert ctrl.resync_to_line(1) is True
    assert win.shown[-1] == ("我在唱《t》", "B"), win.shown[-1]

    assert ctrl.resync_to_line(1) is True
    assert win.shown[-1] == ("我在唱《t》", "C"), win.shown[-1]


def test_nudge_seconds_shifts_lyric(monkeypatch):
    """±5 秒微调：以当前位置为基准平移。"""
    win, ctrl, clock = _aligned_controller(monkeypatch)
    clock.now = 111.0
    ctrl._on_playback_ready(_playback(None))

    assert ctrl.nudge(-6.0) is True        # 11s → 5s → 落在 B(5.0)
    assert win.shown[-1] == ("我在唱《t》", "B"), win.shown[-1]

    assert ctrl.nudge(5.0) is True         # 5s → 10s → 落在 C(10.0)
    assert win.shown[-1] == ("我在唱《t》", "C"), win.shown[-1]

    # 不能推到负数
    assert ctrl.nudge(-999.0) is True
    assert ctrl._tracker.position(clock.now) >= 0.0


def test_resync_is_noop_for_reported_position(monkeypatch):
    """报进度的播放器（Chrome / QQ 音乐）：三个入口都必须 no-op。"""
    win, ctrl, clock = _aligned_controller(monkeypatch, position=11.0)
    ctrl._on_playback_ready(_playback(11.0))
    assert ctrl._tracker.uses_reported_position is True

    assert ctrl.resync_to_start() is False
    assert ctrl.resync_to_line(1) is False
    assert ctrl.nudge(5.0) is False
    # 位置仍完全听真值
    assert ctrl._tracker.position(clock.now, reported=2.0) == 2.0


def test_align_available_after_net_ease_lyrics_arrive(monkeypatch):
    """**生产路径**回归：网易云（不上报进度）取词完成后，「歌词对齐」必须可用。

    `_on_lyrics_ready` 在没有真实进度时给的是**本地估算**位置（`now - detected`），
    旧实现按 `position is not None` 就判定"播放器上报了真值"，于是
    `align_available()` 返回 False —— 右键「歌词对齐」整组置灰。而同一个控制器
    刚弹过「快进或从中途开始播放后，用右键菜单「音乐 → 歌词对齐」校正」的提示，
    用户照着点却点不动（2026-09-22 用户反馈「合并后快进进度不行了」）。

    既有用例都用 `_tracker.load(position=None)` 建状态，绕过了这条生产路径，
    所以旧实现是绿的——这个用例刻意走 `_on_playback_ready → _on_lyrics_ready`。
    """
    import pet.music.lyric_controller as mod

    _patch_clock(monkeypatch, 100.0)
    win = _FakeWin()
    ctrl = mod.MusicLyricController(win)
    monkeypatch.setattr(ctrl, "_fetch_worker", lambda *a, **k: None)

    # 网易云式采样：SMTC 无时间轴 → position=None
    ctrl._on_playback_ready(_playback(None, title="讨厌红楼梦", artist="陶喆"))
    # 取词回到主线程：走生产路径（不是直接 load）
    ctrl._on_lyrics_ready(("讨厌红楼梦", "陶喆"), Lyrics(lines=tuple(LINES)))

    assert ctrl._tracker.has_lyrics is True
    assert ctrl.align_available() is True, (
        "网易云不上报进度，位置是估算的，手动对齐正是唯一纠正手段，不该被关掉"
    )
    assert ctrl.resync_to_line(1) is True
    assert ctrl.nudge(5.0) is True


def test_reported_position_still_disables_align(monkeypatch):
    """真上报进度的播放器（QQ 音乐）：取词后仍不许手动对齐，位置听真值。"""
    import pet.music.lyric_controller as mod

    _patch_clock(monkeypatch, 100.0)
    win = _FakeWin()
    ctrl = mod.MusicLyricController(win)
    monkeypatch.setattr(ctrl, "_fetch_worker", lambda *a, **k: None)

    ctrl._on_playback_ready(_playback(11.0, title="夜曲", artist="周杰伦"))
    ctrl._on_lyrics_ready(("夜曲", "周杰伦"), Lyrics(lines=tuple(LINES)))

    assert ctrl.align_available() is False
    assert ctrl.resync_to_line(1) is False
    assert ctrl._tracker.position(100.0, reported=2.0) == 2.0


def test_align_available_gates(monkeypatch):
    """菜单闸门：无词 / 纯音乐 / 报进度 都不可手动对齐。"""
    win, ctrl, clock = _aligned_controller(monkeypatch)     # 本地时钟 + 有词
    assert ctrl.align_available() is True

    ctrl._tracker.reset()                                   # 无词
    assert ctrl.align_available() is False

    win2, ctrl2, _ = _aligned_controller(monkeypatch, position=1.0)   # 报进度
    ctrl2._on_playback_ready(_playback(1.0))
    assert ctrl2.align_available() is False

    win3, ctrl3, _ = _aligned_controller(monkeypatch)       # 纯音乐
    ctrl3._tracker.reset()
    ctrl3._instrumental = True
    assert ctrl3.align_available() is False


def test_start_track_first_song_without_position_still_announces_title(monkeypatch):
    """缺陷 1 回归：功能开启时歌已在播且播放器不报进度，必须至少亮出歌名。

    旧实现在 _announce() 之前就 return，用户看到的是一片空白，于是"识别不到
    我放到一半的歌"。
    """
    import pet.music.lyric_controller as mod

    _patch_clock(monkeypatch, 100.0)
    win = _FakeWin()
    ctrl = mod.MusicLyricController(win)
    monkeypatch.setattr(ctrl, "_fetch_worker", lambda *a, **k: None)
    assert ctrl._primed is False            # 首次边界：正是"开启时已在播"

    ctrl._start_track(("夜曲", "周杰伦"), "夜曲", "周杰伦", _playback(None), 100.0)

    assert win.shown == [("", "我在唱《夜曲》")], win.shown
    assert ctrl._title_line == "我在唱《夜曲》"
    # 锚点就是"检测到这首歌的时刻"——即把此刻当作第 0 秒
    assert ctrl._detected_at == 100.0


def test_start_track_first_song_without_position_fetches_lyrics(monkeypatch):
    """同一情形下不能因为"猜不到进度"就跳过取词——用户要的是看到歌词。"""
    import threading

    import pet.music.lyric_controller as mod

    _patch_clock(monkeypatch, 100.0)
    win = _FakeWin()
    ctrl = mod.MusicLyricController(win)
    started = threading.Event()
    monkeypatch.setattr(ctrl, "_fetch_worker", lambda *a, **k: started.set())

    ctrl._start_track(("夜曲", "周杰伦"), "夜曲", "周杰伦", _playback(None), 100.0)

    assert ("夜曲", "周杰伦") in ctrl._loading, "应已进入取词中"
    assert ctrl._pending_by_key.get(("夜曲", "周杰伦")) is not None
    assert started.wait(5.0), "取词线程应被启动"


def test_estimated_progress_hint_shown_once_per_session(monkeypatch):
    """不报进度的播放器：提示一次"快进后请用菜单对齐"，不反复打扰。"""
    import pet.music.lyric_controller as mod

    class _HintWin(_FakeWin):
        def __init__(self):
            super().__init__()
            self.alerts: list = []

        def show_alert(self, text, **kw):
            # 替身必须建立**真实的占用态**：控制器的 `_bubble_blocked()` 读的就是
            # ``_alert_current``，只记一笔文本会让"提醒正挂着 → 歌词让路"的整条
            # 路径永远走不到（B4 的回归就藏在这个缺口后面）。
            self.alerts.append(text)
            self._alert_current = {"id": kw.get("alert_id", ""), "text": str(text)}

        def resolve_alert(self, alert_id):
            if (self._alert_current or {}).get("id") == alert_id:
                self._alert_current = None

    _patch_clock(monkeypatch, 100.0)
    win = _HintWin()
    ctrl = mod.MusicLyricController(win)
    monkeypatch.setattr(ctrl, "_fetch_worker", lambda *a, **k: None)

    ctrl._start_track(("夜曲", "周杰伦"), "夜曲", "周杰伦", _playback(None), 100.0)
    assert len(win.alerts) == 1, win.alerts
    assert "对齐" in win.alerts[0]

    # 切到下一首（同样不报进度）不再重复提示
    ctrl._start_track(("晴天", "周杰伦"), "晴天", "周杰伦", _playback(None), 200.0)
    assert len(win.alerts) == 1, win.alerts


def test_no_hint_when_player_reports_progress(monkeypatch):
    """报进度的播放器不该看到这条提示。"""
    import pet.music.lyric_controller as mod

    class _HintWin(_FakeWin):
        def __init__(self):
            super().__init__()
            self.alerts: list = []

        def show_alert(self, text, **kw):
            self.alerts.append(text)

    _patch_clock(monkeypatch, 100.0)
    win = _HintWin()
    ctrl = mod.MusicLyricController(win)
    monkeypatch.setattr(ctrl, "_fetch_worker", lambda *a, **k: None)

    ctrl._start_track(("夜曲", "周杰伦"), "夜曲", "周杰伦", _playback(30.0), 100.0)
    assert win.alerts == [], win.alerts


# --------------------------------------------- 引导让位（真窗口 + 真提醒队列）
#
# B4：「首首歌词到位即收起进度引导」曾经**不可达**——`_on_lyrics_ready` 先跑
# `_bubble_blocked()`（`_alert_current` 非空即真）并 return，resolve 排在它后面。
# 引导气泡自身就占着 `_alert_current`，于是这条 resolve 永远走不到，用户第一首歌
# 12 秒内零帧歌词。故本组用例用**真 PetWindow + 真 window_alerts 队列**，而不是
# 只记一笔调用的替身。

def _real_window_controller(qapp, tmp_path, monkeypatch):
    """真 PetWindow（含真提醒队列 + 真气泡）+ 真控制器（取词线程打桩，保持离线）。

    返回 ``(win, ctrl, pushes)``：``pushes`` 记录 ``win.show_bubble`` 的每一次
    真实调用（探针，不是打桩——原方法照跑），用来断言"歌词确实上屏了"。
    刻意**不**替换气泡替身：`resolve_alert` 靠真 `dismiss()` 把气泡隐藏，控制器
    的 `_bubble_taken_by_other` 又据此判定占用位是否还在——换成 isVisible 恒真的
    假气泡就测不到"引导收起后歌词立即上屏"。
    """
    import pet.music.lyric_controller as mod
    from tests.pet_window_fakes import make_pet_window

    _patch_clock(monkeypatch, 100.0)
    win = make_pet_window(tmp_path)
    ctrl = mod.MusicLyricController(win)
    ctrl._tracker.lead = 0.0
    monkeypatch.setattr(ctrl, "_fetch_worker", lambda *a, **k: None)

    pushes: list[dict] = []
    real_show = win.show_bubble

    def _spy(text, duration_ms=3200, subtitle=None, **kwargs):
        pushes.append({"text": str(text), "subtitle": subtitle})
        return real_show(text, duration_ms, subtitle, **kwargs)

    monkeypatch.setattr(win, "show_bubble", _spy)
    return win, ctrl, pushes


def test_first_song_lyrics_resolve_the_progress_estimate_hint(qapp, tmp_path, monkeypatch):
    """首首歌词到位 → 引导被精确收起 + 歌词立即刷新（修复前：引导仍占位、零气泡）。"""
    win, ctrl, pushes = _real_window_controller(qapp, tmp_path, monkeypatch)
    key = ("夜曲", "周杰伦")
    try:
        ctrl._start_track(key, "夜曲", "周杰伦", _playback(None), 100.0)
        assert win._alert_current is not None
        assert win._alert_current["id"] == "music-lyric-estimated", win._alert_current

        pushes.clear()
        ctrl._on_lyrics_ready(key, Lyrics(
            lines=(LyricLine(0.0, "第一句"), LyricLine(5.0, "第二句"))))

        assert win._alert_current is None, "进度引导必须被收起（否则歌词 12s 内上不了屏）"
        assert pushes, "歌词必须立即刷新（修复前 _bubble_blocked 早退，一次都没送）"
        assert pushes[-1]["text"] == "第一句", pushes
        assert pushes[-1]["subtitle"] == "我在唱《夜曲》"
    finally:
        win.close()
        qapp.processEvents()


def test_other_alerts_are_not_resolved_by_lyrics_arriving(qapp, tmp_path, monkeypatch):
    """非回归：收起只认 music-lyric-estimated 这一条 id，队列里的其它提醒不动。"""
    win, ctrl, pushes = _real_window_controller(qapp, tmp_path, monkeypatch)
    key = ("夜曲", "周杰伦")
    try:
        ctrl._start_track(key, "夜曲", "周杰伦", _playback(None), 100.0)
        win.resolve_alert("music-lyric-estimated")   # 先清掉引导，换成别人的提醒
        win.show_alert("审批：需要你点一下", sticky=True, buttons=[("同意", lambda: None)],
                       alert_id="approval-1", priority=0, alert_type="approval")
        current = win._alert_current
        assert current is not None and current["id"] == "approval-1"

        pushes.clear()
        ctrl._on_lyrics_ready(key, Lyrics(
            lines=(LyricLine(0.0, "第一句"), LyricLine(5.0, "第二句"))))

        assert win._alert_current is current, "别人的提醒不得被歌词到位顺手收掉"
        assert pushes == [], "审批占着气球时歌词必须让路"
    finally:
        win.close()
        qapp.processEvents()


def test_resync_ignored_when_no_lyrics(monkeypatch):
    """还没取到词 / 无词歌：对齐入口不动作，也不该抛异常。"""
    win, ctrl, clock = _aligned_controller(monkeypatch)
    ctrl._tracker.reset()
    assert ctrl.resync_to_start() is False
    assert ctrl.resync_to_line(1) is False
    assert ctrl.nudge(5.0) is False


# ------------------------------------------------- 取词失败后的同曲自动重试（B5）

def test_same_track_retries_lyric_fetch_after_cooldown(monkeypatch):
    """冷却到期后同一首歌不切歌也要自动重试（修复前：同曲轮询永不重试）。

    冷却到期的判断原先只在 ``_start_track``（切歌路径）里，而同曲轮询的曲目 key
    不变、压根走不到那条判断——一次网络故障就把这首歌在本次会话里永久拉黑，
    十次同曲采样 ``fetch_attempts`` 恒为 0，网络恢复也不会自愈。
    """
    import pet.music.lyric_controller as mod

    clock = _patch_clock(monkeypatch, 100.0)
    ctrl = mod.MusicLyricController(_FakeWin())
    attempts: list = []
    monkeypatch.setattr(ctrl, "_fetch_worker", lambda *a, **k: attempts.append(a))

    key = ("song-a", "artist")
    sampled = _playback(None, title="Song-A", artist="Artist")
    ctrl._start_track(key, "Song-A", "Artist", sampled, 100.0)
    assert len(attempts) == 1, "切歌路径照常取词"

    ctrl._on_lyrics_ready(key, None)              # 取词失败：记冷却
    cooldown_until = ctrl._no_lyric_until[key]
    assert cooldown_until > clock.now

    clock.now = cooldown_until - 1.0              # 冷却内：同曲采样不重复取
    ctrl._on_playback_ready(sampled)
    assert len(attempts) == 1, f"冷却期内不得重复取词：{attempts}"

    clock.now = cooldown_until + 1.0              # 冷却到期：同曲采样自动重试
    ctrl._on_playback_ready(sampled)
    assert len(attempts) == 2, f"冷却到期后同曲必须自动重试：{attempts}"
    assert key in ctrl._loading, "重试同样走「取词中」去重位"


def test_same_track_does_not_refetch_while_already_loading(monkeypatch):
    """非回归：取词在途时同曲采样不得叠加第二路请求。"""
    import pet.music.lyric_controller as mod

    _patch_clock(monkeypatch, 100.0)
    ctrl = mod.MusicLyricController(_FakeWin())
    attempts: list = []
    monkeypatch.setattr(ctrl, "_fetch_worker", lambda *a, **k: attempts.append(a))

    key = ("song-a", "artist")
    sampled = _playback(None, title="Song-A", artist="Artist")
    ctrl._start_track(key, "Song-A", "Artist", sampled, 100.0)
    ctrl._on_playback_ready(sampled)
    ctrl._on_playback_ready(sampled)

    assert len(attempts) == 1, f"在途取词只允许一路：{attempts}"


# ------------------------------------------------- 网络边界（系统代理必须被绕过）


class _FakeResponse:
    """最小响应替身：只支持 ``with`` + ``read()``。"""

    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _RecordingOpener:
    """假 opener：记录每次请求，返回固定 JSON 或抛指定异常。"""

    def __init__(self, payload=None, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.calls: list[tuple[str, float | None]] = []

    def open(self, request, timeout=None):
        self.calls.append((getattr(request, "full_url", str(request)), timeout))
        if self.error is not None:
            raise self.error
        return _FakeResponse(json.dumps(self.payload).encode("utf-8"))


def _stub_urllib(monkeypatch, opener):
    """把 urllib 的 opener 构造换成记录桩，并假装系统里配着代理。

    ``proxy_bypass`` 一并钉成 False：真实现会去读用户注册表，测试不该依赖它
    （命中绕过名单时"代理路"实际是直连，见 ``_fallback_proxy``）。
    """
    monkeypatch.setattr(
        urllib.request, "getproxies", lambda: {"https": "http://127.0.0.1:12450"}
    )
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: False)
    built: list[tuple] = []

    def build(*handlers):
        built.append(handlers)
        return opener

    monkeypatch.setattr(urllib.request, "build_opener", build)
    return built


def test_lyric_request_bypasses_system_proxy(monkeypatch):
    """歌词请求必须自建直连 opener，不能用会继承系统代理的 urlopen。

    2026-09-22 实机事故：系统代理（VPN 全局模式 127.0.0.1:12450）下三个源
    单次 20~41 秒，全部超过 HTTP_TIMEOUT，于是每首未缓存曲目都以「0 行」收场
    （日志 `歌词取词完成: ... -> 0行, 耗时 9.00s`）。直连实测 0.25~3.5 秒。

    **直连是首选路线，这一点不因 2026-10-16 的有界回退而变**：直连成功时
    只有一次传输尝试，系统代理一次都不碰（见 ``_RouteStub`` 用例）。
    """
    opener = _RecordingOpener({"ok": 1})
    built = _stub_urllib(monkeypatch, opener)

    assert music_lyric._http_get_json("https://c.y.qq.com/soso/x") == {"ok": 1}

    assert built, (
        "歌词请求没有自建 opener —— 仍在用 urllib.request.urlopen，会继承系统代理"
    )
    handlers = [h for h in built[0] if isinstance(h, urllib.request.ProxyHandler)]
    assert handlers, "直连 opener 必须显式传 ProxyHandler({})，否则仍继承系统代理"
    assert handlers[0].proxies == {}, handlers[0].proxies
    # 剩余预算按 deadline 算，`deadline - monotonic()` 在个别平台会差最后几位
    # （CI ubuntu-latest 实测 7.999999999999943）：这里要比的是「用的是 HTTP_TIMEOUT
    # 这个量级」，不是浮点位模式。
    assert opener.calls and opener.calls[0][1] == pytest.approx(
        music_lyric.HTTP_TIMEOUT, abs=1e-3), opener.calls


def test_lyric_request_failure_is_logged_at_warning(monkeypatch, caplog):
    """取不到词的**原因**必须落在 INFO 级日志能看到的级别。

    原先写的是 ``log.debug``，而应用是 ``basicConfig(level=INFO)``，
    线上日志于是只剩「0行, 耗时 9.00s」——看不出是超时、解析失败还是被封。
    """
    opener = _RecordingOpener(error=TimeoutError("timed out"))
    _stub_urllib(monkeypatch, opener)

    url = "https://music.163.com/api/song/lyric?id=1"
    with caplog.at_level(logging.WARNING):
        assert music_lyric._http_get_json(url) is None

    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("music.163.com" in m and "TimeoutError" in m for m in messages), messages


def test_system_proxy_route_policy_is_logged_once(monkeypatch, caplog):
    """配了系统代理时每个进程只记一行路线策略，**不回显代理地址/凭据**。

    日志经常被整段贴进 issue 与报告里，代理串（可能带 ``user:pass@``）属于
    用户环境信息，只说明"走哪条路"就够定位了。
    """
    monkeypatch.setattr(music_lyric, "_route_logged", False)
    opener = _RecordingOpener({"ok": 1})
    _stub_urllib(monkeypatch, opener)

    with caplog.at_level(logging.INFO):
        music_lyric._http_get_json("https://c.y.qq.com/soso/x")
        music_lyric._http_get_json("https://c.y.qq.com/soso/y")

    hits = [r.getMessage() for r in caplog.records if "系统代理" in r.getMessage()]
    assert len(hits) == 1, hits
    assert "直连" in hits[0], hits[0]
    assert "127.0.0.1:12450" not in hits[0], hits[0]


# ------------------------------------------- 有界路线回退（2026-10-16 汽水歌词）
#
# 现场事实（`.scratch/soda-lyrics/REPORT.md` 与部署实例日志）：同 URL、同一分钟，
# QQ 源**直连恒 HTTP 500**，而**走系统代理 200 并真的取回歌词**（36 行）。固定单一
# 路线的歌词链因此对"只有代理能出网"的一半用户永久 0 行——用户看到的就是
# 「歌名在、歌词不在」。修法：直连仍是首选，只在**传输失败或 500/502/504**
# 且本机确有系统代理、且还剩够用预算时，**回退一次**。
#
# 下面这些用例全部离线（opener 桩 + 受控时钟），覆盖用户点名的形状。


class _LyricClock:
    """受控时钟：只替换 ``pet.music.lyric`` 里的 monotonic，其余透传。"""

    def __init__(self, now: float = 1000.0):
        self.now = now

    def monotonic(self) -> float:
        return self.now

    def __getattr__(self, name):
        return getattr(time, name)


def _http_error(code: int, *, retry_after: str | None = None) -> urllib.error.HTTPError:
    # 用 ``email.message.Message`` 而不是 dict：真实 ``urllib`` 交给 ``HTTPError`` 的是
    # ``http.client.HTTPMessage``（Message 子类），``.get()`` 两者都支持；而塞 dict 走的是
    # 宽松兼容路径，``HTTPError`` 上 ``headers`` 是否还等于这个 dict 取决于平台/版本——
    # 实测那档「含逗号的 HTTP 日期」在 macOS CI 上读不到 header，于是换路、用例红。
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(
        "https://c.y.qq.com/x", code, f"HTTP Error {code}", headers, None
    )


class _RouteStub:
    """按**路线**分发的 opener 桩：直连一条、系统代理一条，各自一张响应表。

    ``build_opener`` 收到 ``ProxyHandler({})`` 判为直连，收到非空代理判为回退；
    每次调用都记 ``(路线, URL, 超时)``，因此"试了几次、走的哪条、每次给了多少
    超时"三件事都能被断言。

    ``proxies`` 直接决定"系统里配着什么"（默认 http+https 都配好），用来钉
    "``no`` / 其它协议不算代理"这一类形状；``bypass`` 钉系统代理绕过表——两者
    都是真实现会读的用户环境，测试里一律替身化。
    """

    def __init__(self, monkeypatch, *, direct, proxy_responses=None,
                 proxy: str | None = "http://127.0.0.1:12450",
                 proxies: dict | None = None, bypass: bool = False,
                 clock: _LyricClock | None = None, advance: float = 0.0):
        self.direct = list(direct)
        self.proxy = list(proxy_responses or [])
        self.clock = clock
        self.advance = advance
        self.calls: list[tuple[str, str, float | None]] = []
        if proxies is None:
            proxies = {"http": proxy, "https": proxy} if proxy else {}
        monkeypatch.setattr(urllib.request, "getproxies", lambda: dict(proxies))
        monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: bypass)
        monkeypatch.setattr(urllib.request, "build_opener", self._build)

    def _build(self, *handlers):
        proxies = [h.proxies for h in handlers if isinstance(h, urllib.request.ProxyHandler)]
        route = "代理" if (proxies and proxies[0]) else "直连"
        return _RouteOpener(self, route)

    def routes_of(self, url_part: str) -> list[str]:
        return [route for route, url, _ in self.calls if url_part in url]

    def timeouts_of(self, url_part: str) -> list[float | None]:
        return [timeout for _, url, timeout in self.calls if url_part in url]

    def _dispatch(self, route: str, request, timeout):
        url = getattr(request, "full_url", str(request))
        self.calls.append((route, url, timeout))
        if self.clock is not None:
            self.clock.now += self.advance
        table = self.direct if route == "直连" else self.proxy
        for prefix, result in table:
            if prefix in url:
                if isinstance(result, Exception):
                    raise result
                return _FakeResponse(json.dumps(result).encode("utf-8"))
        raise AssertionError(f"{route} 路线没有录制这个 URL: {url}")


class _RouteOpener:
    def __init__(self, stub: _RouteStub, route: str) -> None:
        self._stub = stub
        self._route = route

    def open(self, request, timeout=None):
        return self._stub._dispatch(self._route, request, timeout)


_URL = "https://c.y.qq.com/soso/x"


def test_direct_success_is_one_attempt_and_never_touches_proxy(monkeypatch):
    """直连成功：恰好一次传输尝试，代理路线一次都不碰。"""
    stub = _RouteStub(monkeypatch, direct=[("c.y.qq.com", {"ok": 1})])

    assert music_lyric._http_get_json(_URL) == {"ok": 1}
    assert [route for route, _, _ in stub.calls] == ["直连"], stub.calls


def test_no_system_proxy_means_no_fallback(monkeypatch):
    """本机没配系统代理：只有直连一条路，失败就是失败（不凭空造第二次请求）。"""
    stub = _RouteStub(
        monkeypatch, proxy=None,
        direct=[("c.y.qq.com", urllib.error.URLError("连接被拒"))],
    )

    assert music_lyric._http_get_json(_URL) is None
    assert [route for route, _, _ in stub.calls] == ["直连"], stub.calls


def test_no_proxy_entry_alone_is_not_a_fallback_route(monkeypatch, caplog):
    """``getproxies()`` 只给了 ``no``（绕过名单）时，不算"有代理"。

    ``no`` 是 ``NO_PROXY`` 的绕过名单、不是代理；把它当第二条路会让"回退"实际
    还是同一发直连，日志也会谎称走了代理。
    """
    stub = _RouteStub(
        monkeypatch, proxies={"no": "c.y.qq.com,localhost"},
        direct=[("c.y.qq.com", _http_error(500))],
        proxy_responses=[("c.y.qq.com", {"ok": "不该被请求"})],
    )

    with caplog.at_level(logging.INFO):
        assert music_lyric._http_get_json(_URL) is None

    assert [route for route, _, _ in stub.calls] == ["直连"], stub.calls
    assert "回退系统代理成功" not in "\n".join(r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("proxies", [
    {"ftp": "http://127.0.0.1:12450"},                    # 只有其它协议的代理
    {"http": "http://127.0.0.1:12450"},                   # https 目标配了 http 代理
    {"all": "http://127.0.0.1:12450"},                    # all_proxy 不是 urllib 的代理条目
    {"no": "localhost", "ftp": "http://127.0.0.1:12450"},
])
def test_proxy_for_other_scheme_does_not_count(monkeypatch, caplog, proxies):
    """URL 实际协议（https）没有代理时不回退：``ProxyHandler`` 本来就分派不到，
    再打一次等于重复直连。"""
    stub = _RouteStub(
        monkeypatch, proxies=proxies,
        direct=[("c.y.qq.com", _http_error(500))],
        proxy_responses=[("c.y.qq.com", {"ok": "不该被请求"})],
    )

    with caplog.at_level(logging.INFO):
        assert music_lyric._http_get_json(_URL) is None

    assert [route for route, _, _ in stub.calls] == ["直连"], stub.calls
    assert "回退系统代理成功" not in "\n".join(r.getMessage() for r in caplog.records)


def test_proxy_bypass_hit_is_not_a_second_route(monkeypatch, caplog):
    """命中系统代理绕过表的 URL：那条"代理路"其实就是直连，不能算回退。

    ``ProxyHandler.proxy_open`` 会在 ``proxy_bypass`` 命中时直接放行成直连，
    所以这里必须**只试一次**，且日志不许出现"回退系统代理成功"——即便替身代理
    本来能返回 200。
    """
    stub = _RouteStub(
        monkeypatch, bypass=True,
        direct=[("c.y.qq.com", _http_error(500))],
        proxy_responses=[("c.y.qq.com", {"ok": "不该被请求"})],
    )

    with caplog.at_level(logging.INFO):
        assert music_lyric._http_get_json(_URL) is None

    assert [route for route, _, _ in stub.calls] == ["直连"], stub.calls
    assert "回退系统代理成功" not in "\n".join(r.getMessage() for r in caplog.records)


def test_fallback_success_logs_proxy_only_when_it_really_used_it(monkeypatch, caplog):
    """成功日志不谎称代理：真走了代理才有那一行；直连成功时一行都没有。"""
    stub = _RouteStub(
        monkeypatch,
        direct=[("c.y.qq.com", _http_error(500))],
        proxy_responses=[("c.y.qq.com", {"ok": "via-proxy"})],
    )
    with caplog.at_level(logging.INFO):
        assert music_lyric._http_get_json(_URL) == {"ok": "via-proxy"}
    hits = [r.getMessage() for r in caplog.records if "回退系统代理成功" in r.getMessage()]
    assert len(hits) == 1, hits
    assert [route for route, _, _ in stub.calls] == ["直连", "代理"], stub.calls

    caplog.clear()
    direct_stub = _RouteStub(monkeypatch, direct=[("c.y.qq.com", {"ok": 1})])
    with caplog.at_level(logging.INFO):
        assert music_lyric._http_get_json(_URL) == {"ok": 1}
    assert "回退系统代理成功" not in "\n".join(r.getMessage() for r in caplog.records)
    assert [route for route, _, _ in direct_stub.calls] == ["直连"], direct_stub.calls


def test_transport_failure_falls_back_to_system_proxy_once(monkeypatch):
    """直连传输失败 + 本机有系统代理 → 回退一次，且回退真的走代理。"""
    stub = _RouteStub(
        monkeypatch,
        direct=[("c.y.qq.com", urllib.error.URLError("隧道不通"))],
        proxy_responses=[("c.y.qq.com", {"ok": "via-proxy"})],
    )

    assert music_lyric._http_get_json(_URL) == {"ok": "via-proxy"}
    assert [route for route, _, _ in stub.calls] == ["直连", "代理"], stub.calls
    assert stub.routes_of("c.y.qq.com") == ["直连", "代理"]


@pytest.mark.parametrize("code", [500, 502, 504])
def test_gateway_statuses_fall_back_to_system_proxy(monkeypatch, code):
    """500/502/504（服务端内部错误 / 上游网关错误 / 网关超时）：直连这条路
    可能坏了，允许换系统代理再试一次。无 Retry-After 时才换。"""
    stub = _RouteStub(
        monkeypatch,
        direct=[("c.y.qq.com", _http_error(code))],
        proxy_responses=[("c.y.qq.com", {"ok": code})],
    )

    assert music_lyric._http_get_json(_URL) == {"ok": code}
    assert [route for route, _, _ in stub.calls] == ["直连", "代理"], stub.calls


@pytest.mark.parametrize("retry_after", [
    "30",                                   # 整数秒
    "Wed, 21 Oct 2026 07:28:00 GMT",        # HTTP 日期
    "not-a-number",                         # 解析不了的值
    "",                                      # 头存在但值为空
])
@pytest.mark.parametrize("code", [500, 502, 504])
def test_retry_after_header_blocks_fallback_even_on_gateway_statuses(
    monkeypatch, caplog, code, retry_after
):
    """5xx 带上 ``Retry-After`` 一律不换路。

    服务端在 5xx 里也常给这个头（整数秒或 HTTP 日期），它的意思就是"过一会儿再
    打"；立刻从另一条路重打同样违背该请求。此前这里只看状态码，日志写着"不换路、
    不重试"，实际却换路打了一遍。
    """
    stub = _RouteStub(
        monkeypatch,
        direct=[("c.y.qq.com", _http_error(code, retry_after=retry_after))],
        proxy_responses=[("c.y.qq.com", {"ok": "不该被请求"})],
    )

    with caplog.at_level(logging.WARNING):
        assert music_lyric._http_get_json(_URL) is None

    assert [route for route, _, _ in stub.calls] == ["直连"], stub.calls
    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("Retry-After" in m for m in messages), messages


def test_gateway_status_without_retry_after_still_falls_back(monkeypatch):
    """对照：同一个 502 只要**没有** Retry-After 头，回退照常发生。"""
    stub = _RouteStub(
        monkeypatch,
        direct=[("c.y.qq.com", _http_error(502))],
        proxy_responses=[("c.y.qq.com", {"ok": "via-proxy"})],
    )

    assert music_lyric._http_get_json(_URL) == {"ok": "via-proxy"}
    assert [route for route, _, _ in stub.calls] == ["直连", "代理"], stub.calls


@pytest.mark.parametrize("code", [401, 403, 404, 429, 503])
def test_rejection_and_throttle_statuses_do_not_switch_route(monkeypatch, code):
    """对方的明确答复（权限 / 不存在 / 限流）不换路：换条路重打只是再挨一次。

    401/403 权限与风控、404 不存在、429/503 限流——这些都是"路由无关"的
    结果，回退只会白打一发，还多违背一次服务端的 Retry-After 请求。
    """
    stub = _RouteStub(
        monkeypatch,
        direct=[("c.y.qq.com", _http_error(code))],
        proxy_responses=[("c.y.qq.com", {"ok": "不该被请求"})],
    )

    assert music_lyric._http_get_json(_URL) is None
    assert [route for route, _, _ in stub.calls] == ["直连"], stub.calls


def test_throttle_retry_after_is_surfaced_in_log(monkeypatch, caplog):
    """限流响应里的 Retry-After 写进日志（本模块对该响应既不换路也不重试）。"""
    stub = _RouteStub(
        monkeypatch,
        direct=[("c.y.qq.com", _http_error(429, retry_after="30"))],
        proxy_responses=[("c.y.qq.com", {"ok": "不该被请求"})],
    )

    with caplog.at_level(logging.WARNING):
        assert music_lyric._http_get_json(_URL) is None

    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert [route for route, _, _ in stub.calls] == ["直连"], stub.calls
    assert any("Retry-After: 30" in m for m in messages), messages


def test_both_routes_failing_are_both_reported(monkeypatch, caplog):
    """两条路都失败：返回 None，日志里两条路的异常类型都可辨（不写异常文本）。"""
    stub = _RouteStub(
        monkeypatch,
        direct=[("c.y.qq.com", urllib.error.URLError("隧道不通"))],
        proxy_responses=[("c.y.qq.com", _http_error(502))],
    )

    with caplog.at_level(logging.WARNING):
        assert music_lyric._http_get_json(_URL) is None

    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert [route for route, _, _ in stub.calls] == ["直连", "代理"], stub.calls
    hit = [m for m in messages if "c.y.qq.com" in m]
    assert hit, messages
    assert any("直连 URLError" in m and "系统代理 HTTPError HTTP 502" in m for m in hit), hit
    assert "隧道不通" not in hit[0], hit[0]


def test_exception_text_never_reaches_the_log(monkeypatch, caplog):
    """异常**文本**不落日志：它可能夹带内部代理地址甚至凭据。

    ``URLError``/``http.client.InvalidURL`` 的消息是拼接出来的，例如
    ``<urlopen error Unable to connect to proxy http://alice:s3cr3t@proxy.internal:8080>``；
    日志经常被整段外贴，所以只留异常类型 + HTTP 状态 + Retry-After。
    """
    secret = "http://alice:s3cr3t@proxy.internal:8080"
    stub = _RouteStub(
        monkeypatch,
        direct=[("c.y.qq.com", urllib.error.URLError(f"<urlopen error tunnel to {secret} refused>"))],
        proxy_responses=[
            ("c.y.qq.com", http.client.InvalidURL(f"no host given in proxy {secret}"))
        ],
    )

    with caplog.at_level(logging.DEBUG):
        assert music_lyric._http_get_json(_URL) is None

    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "proxy.internal" not in joined, joined
    assert "alice" not in joined and "s3cr3t" not in joined, joined
    assert "tunnel to" not in joined, joined
    # 两条路仍可辨
    assert "直连 URLError" in joined and "系统代理 InvalidURL" in joined, joined
    assert [route for route, _, _ in stub.calls] == ["直连", "代理"], stub.calls


def test_fallback_shares_the_deadline_budget_instead_of_doubling(monkeypatch):
    """直连与回退共享同一份预算：回退拿到的是**剩余**部分，不是第二次完整超时。

    否则一次请求会变成 2×HTTP_TIMEOUT，``fetch_lyrics`` 的 9 秒死线会把整趟
    取词判死——"修了路线反而更慢、还是 0 行"。
    """
    clock = _LyricClock(1000.0)
    monkeypatch.setattr(music_lyric, "time", clock)
    stub = _RouteStub(
        monkeypatch, clock=clock, advance=3.0,
        direct=[("c.y.qq.com", urllib.error.URLError("隧道不通"))],
        proxy_responses=[("c.y.qq.com", {"ok": 1})],
    )

    assert music_lyric._http_get_json(_URL, deadline=clock.now + music_lyric.HTTP_TIMEOUT) == {"ok": 1}
    direct_timeout, proxy_timeout = stub.timeouts_of("c.y.qq.com")
    assert direct_timeout == music_lyric.HTTP_TIMEOUT, stub.calls
    # 直连已经花掉 3 秒，回退只能拿剩下的 5 秒（不是又一份 8 秒）
    assert proxy_timeout == pytest.approx(music_lyric.HTTP_TIMEOUT - 3.0), stub.calls
    assert 3.0 + proxy_timeout <= music_lyric.HTTP_TIMEOUT + 1e-6, stub.calls


def test_fallback_is_skipped_when_remaining_budget_is_gone(monkeypatch, caplog):
    """剩余预算不够一次尝试时不要再发请求：注定超时的那发只会白等。"""
    clock = _LyricClock(1000.0)
    monkeypatch.setattr(music_lyric, "time", clock)
    stub = _RouteStub(
        monkeypatch, clock=clock, advance=8.0,
        direct=[("c.y.qq.com", urllib.error.URLError("读超时"))],
        proxy_responses=[("c.y.qq.com", {"ok": "不该被请求"})],
    )

    with caplog.at_level(logging.WARNING):
        result = music_lyric._http_get_json(
            _URL, deadline=clock.now + music_lyric.HTTP_TIMEOUT
        )

    assert result is None
    assert [route for route, _, _ in stub.calls] == ["直连"], stub.calls
    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("预算" in m for m in messages), messages


def test_exhausted_deadline_sends_no_request_at_all(monkeypatch, caplog):
    """截止时刻已过：一次请求都不发，直接按失败收场。"""
    clock = _LyricClock(1000.0)
    monkeypatch.setattr(music_lyric, "time", clock)
    stub = _RouteStub(
        monkeypatch, direct=[("c.y.qq.com", {"ok": 1})],
        proxy_responses=[("c.y.qq.com", {"ok": 1})],
    )

    with caplog.at_level(logging.WARNING):
        assert music_lyric._http_get_json(_URL, deadline=clock.now - 0.5) is None

    assert stub.calls == [], stub.calls


def test_proxy_address_and_credentials_never_reach_the_log(monkeypatch, caplog):
    """两条路都失败时，日志不许出现代理地址与凭据。"""
    proxy = "http://alice:s3cr3t@127.0.0.1:12450"
    stub = _RouteStub(
        monkeypatch, proxy=proxy,
        direct=[("c.y.qq.com", _http_error(500))],
        proxy_responses=[("c.y.qq.com", urllib.error.URLError("代理隧道不通"))],
    )

    with caplog.at_level(logging.DEBUG):
        assert music_lyric._http_get_json(_URL) is None

    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "127.0.0.1:12450" not in joined, joined
    assert "alice" not in joined and "s3cr3t" not in joined, joined
    assert [route for route, _, _ in stub.calls] == ["直连", "代理"], stub.calls


def test_proxy_address_never_reaches_the_route_log(monkeypatch, caplog):
    """回退成功那条 INFO 也不许带上代理地址。"""
    proxy = "http://alice:s3cr3t@127.0.0.1:12450"
    stub = _RouteStub(
        monkeypatch, proxy=proxy,
        direct=[("c.y.qq.com", _http_error(500))],
        proxy_responses=[("c.y.qq.com", {"ok": 1})],
    )

    with caplog.at_level(logging.INFO):
        assert music_lyric._http_get_json(_URL) == {"ok": 1}

    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "系统代理" in joined, joined
    assert "127.0.0.1:12450" not in joined and "s3cr3t" not in joined, joined
    assert [route for route, _, _ in stub.calls] == ["直连", "代理"], stub.calls


# ------------------------------- 汽水曲目：真实元数据 → 歌词（离线集成回归）
#
# 元数据取自 2026-10-16 只读 SMTC 采样与 `.scratch/soda-lyrics/REPORT.md`：
# app_id=汽水音乐、title=「荒(一念间天荒仙灭)」、artist=「莫小檀」，该曲在网易云
# 曲库与 lrclib 里都没有、**只有 QQ 有**，而 QQ 直连恒 500（代理同分钟 200 → 36 行）。
# 因此这一条链路"是死是活"完全取决于两个形状：QQ 搜索命中 songmid、以及
# 直连 500 时敢不敢再走一次代理。
#
# 诚实边界：QQ 响应**结构**按代码读到的字段写（data.song.list[].songmid/singer、
# lyric 明细的 payload["lyric"]），songmid `0009sdl21zLVyO` 与歌手名 `莫小檀` 是
# REPORT.md §4 记录的真实取值；**歌词正文没有落盘**（只有"36 行"的计数证据），
# 故正文用形态等价的替身，断言按替身的行数走，不冒充真实歌词内容。

_SODA_TITLE = "荒(一念间天荒仙灭)"
_SODA_ARTIST = "莫小檀"
_SODA_SONGMID = "0009sdl21zLVyO"
_QQ_SEARCH_SODA = {
    "code": 0,
    "data": {"song": {"list": [
        {"songmid": _SODA_SONGMID, "songname": _SODA_TITLE, "singer": [{"name": _SODA_ARTIST}]},
    ]}},
}
_QQ_LYRIC_SODA = {
    "retcode": 0,
    "lyric": (
        "[ti:荒(一念间天荒仙灭)]\n[ar:莫小檀]\n"
        "[00:00.00]荒(一念间天荒仙灭) - 莫小檀\n"
        "[00:12.34]第一句\n[00:24.00]第二句\n[00:36.00]第三句\n"
    ),
}


class TestSodaTrackOfflineIntegration:
    """汽水在播曲目：真实 title/artist 走完"取词 → 路线回退 → 解析"整条链。"""

    @staticmethod
    def _stub_sources(monkeypatch, *, direct_table, proxy_table):
        """三源全部用录制响应打桩：QQ 有词，lrclib/网易云都判未命中。"""
        return _RouteStub(
            monkeypatch,
            direct=direct_table,
            proxy_responses=proxy_table,
        )

    def test_qq_direct_500_gets_lyrics_after_proxy_fallback(self, monkeypatch, tmp_path):
        """本条即症状回归：直连 500 时，回退系统代理才拿得到这首的歌词。"""
        monkeypatch.setattr(music_lyric, "cache_dir", lambda: tmp_path)
        stub = self._stub_sources(
            monkeypatch,
            direct_table=[
                ("client_search_cp", _http_error(500)),
                ("fcg_query_lyric_new", _http_error(500)),
                ("lrclib.net", _http_error(404)),
                ("music.163.com/api/cloudsearch", {"result": {"songs": []}}),
            ],
            proxy_table=[
                ("client_search_cp", _QQ_SEARCH_SODA),
                ("fcg_query_lyric_new", _QQ_LYRIC_SODA),
                ("lrclib.net", _http_error(404)),
                ("music.163.com/api/cloudsearch", {"result": {"songs": []}}),
            ],
        )

        lyrics = music_lyric.fetch_lyrics(_SODA_TITLE, _SODA_ARTIST)

        assert lyrics is not None and len(lyrics.lines) == 4, lyrics
        assert [line.text for line in lyrics.lines][-3:] == ["第一句", "第二句", "第三句"]
        # 搜索请求带着真实 SMTC 元数据（歌名 + 歌手）出去
        search_url = next(u for r, u, _ in stub.calls if "client_search_cp" in u)
        assert urllib.parse.quote(f"{_SODA_TITLE} {_SODA_ARTIST}") in search_url
        # 命中录制的 songmid → 明细请求带的是它，不是别的歌
        assert any(_SODA_SONGMID in u for _, u, _ in stub.calls), stub.calls
        # 两条路都试过：直连先失败，回退系统代理成事
        assert stub.routes_of("client_search_cp") == ["直连", "代理"], stub.calls
        assert stub.routes_of("fcg_query_lyric_new") == ["直连", "代理"], stub.calls

    def test_same_track_without_proxy_route_gets_nothing(self, monkeypatch, tmp_path):
        """对照：没有系统代理可回退时，这条链路就是 0 行（本次修的就是这个）。"""
        monkeypatch.setattr(music_lyric, "cache_dir", lambda: tmp_path)
        stub = self._stub_sources(
            monkeypatch,
            direct_table=[
                ("client_search_cp", _http_error(500)),
                ("fcg_query_lyric_new", _http_error(500)),
                ("lrclib.net", _http_error(404)),
                ("music.163.com/api/cloudsearch", {"result": {"songs": []}}),
            ],
            proxy_table=[],
        )
        monkeypatch.setattr(urllib.request, "getproxies", lambda: {})

        assert music_lyric.fetch_lyrics(_SODA_TITLE, _SODA_ARTIST) is None
        assert set(stub.routes_of("client_search_cp")) == {"直连"}, stub.calls



# ---------------------------------------------------------------- 三源解析器 fixture（真实响应形态）
# 2026-10-10 调查实锤：三个真实源解析器此前零覆盖（fetch 用例全部拿 lambda 桩换掉
# _SOURCES），上游字段改名（网易云 artists→ar）在测试里不可见——这次事故的本来形态。
# 以下 fixture 按当刻真实响应结构录制，堵住「结构变了测试还绿」这一类死法。

class TestRealSourceParsers:
    """三源解析器的真实响应 fixture（不碰网络：_http_get_json 全替身）。"""

    @staticmethod
    def _patch_http(monkeypatch, responses):
        """按 URL 前缀分发录制响应；未命中前缀返回 None（模拟接口异常）。"""
        def fake_get(url, **kwargs):
            for prefix, payload in responses.items():
                if prefix in url:
                    return payload
            return None
        monkeypatch.setattr(music_lyric, "_http_get_json", fake_get)

    def test_netease_matches_artist_via_ar_field(self, monkeypatch):
        """现行响应：歌手在 ar（无 artists 字段）——本次事故的回归钉。"""
        self._patch_http(monkeypatch, {
            "cloudsearch": {"result": {"songs": [
                {"id": 12345, "name": "还是你的笑容最可爱",
                 "ar": [{"name": "音阙诗听"}, {"name": "赵方婧"}],
                 "al": {"name": "专辑"}},
            ]}},
            "song/lyric": {"lrc": {"lyric": "[00:01.00]第一句\n[00:05.00]第二句"}},
        })
        lyrics = music_lyric._fetch_from_netease("还是你的笑容最可爱", "音阙诗听")
        assert lyrics is not None, "ar 字段必须能匹配歌手（artists 已下线）"
        assert len(lyrics.lines) == 2

    def test_netease_legacy_artists_field_still_works(self, monkeypatch):
        """旧响应形态（artists）：向前兼容不丢。"""
        self._patch_http(monkeypatch, {
            "cloudsearch": {"result": {"songs": [
                {"id": 222, "name": "歌", "artists": [{"name": "歌手甲"}]},
            ]}},
            "song/lyric": {"lrc": {"lyric": "[00:01.00]词"}},
        })
        lyrics = music_lyric._fetch_from_netease("歌", "歌手甲")
        assert lyrics is not None and len(lyrics.lines) == 1

    def test_netease_candidates_without_match_logs_warning(self, monkeypatch, caplog):
        """搜得到但无人匹配必须留 warning——静默失效是上次事故的共犯。"""
        self._patch_http(monkeypatch, {
            "cloudsearch": {"result": {"songs": [
                {"id": 1, "name": "歌", "ar": [{"name": "别人"}]},
            ]}},
        })
        import logging
        with caplog.at_level(logging.WARNING, logger="pet.music.lyric"):
            assert music_lyric._fetch_from_netease("歌", "查无此人") is None
        assert any("无人匹配歌手" in rec.message for rec in caplog.records)

    def test_lrclib_synced_lyrics_parsed(self, monkeypatch):
        self._patch_http(monkeypatch, {
            "lrclib.net/api/get": {
                "syncedLyrics": "[00:01.00]line one\n[00:03.00]line two",
                "plainLyrics": "line one\nline two",
                "instrumental": False,
            },
        })
        lyrics = music_lyric._fetch_from_lrclib("song", "artist")
        assert lyrics is not None and len(lyrics.lines) == 2

    def test_lrclib_plain_only_returns_none(self, monkeypatch):
        """只有 plainLyrics（无时间轴）按设计返回 None——逐行歌词对齐用不了它。"""
        self._patch_http(monkeypatch, {
            "lrclib.net/api/get": {"syncedLyrics": None, "plainLyrics": "a\nb",
                                   "instrumental": False},
        })
        assert music_lyric._fetch_from_lrclib("song", "artist") is None

    def test_lrclib_instrumental_flag(self, monkeypatch):
        self._patch_http(monkeypatch, {
            "lrclib.net/api/get": {"syncedLyrics": None, "instrumental": True},
        })
        lyrics = music_lyric._fetch_from_lrclib("ost", "composer")
        assert lyrics is not None and lyrics.instrumental is True

    def test_qq_parser_with_recorded_shape(self, monkeypatch):
        """QQ search 接口上游 500 是上游的事，解析器本身按录制形态必须有牙。"""
        self._patch_http(monkeypatch, {
            "client_search_cp": {"data": {"song": {"list": [
                {"songmid": "mid123", "songname": "歌",
                 "singer": [{"name": "歌手甲"}]},
            ]}}},
            "fcg_query_lyric_new": {"retcode": 0, "lyric": "[00:01.00]词一"},
        })
        lyrics = music_lyric._fetch_from_qq("歌", "歌手甲")
        assert lyrics is not None and len(lyrics.lines) == 1


class TestPendingPlaybackIsBoundPerRequest:
    """A→B→A 快切时不许拿 B 的采样上下文装载 A 的歌词（歌词族终审阻塞 1）。

    修复前：实例级 ``_pending_playback`` 被 B 覆盖，A 的结果回来读 B 的
    position/reported——A 无进度、B 有进度时 A 被装成 reported=True，之后
    A 每拍 position=None 清不掉它，「歌词对齐」整组置灰一整首。
    """

    def _controller(self, monkeypatch):
        import pet.music.lyric_controller as mlc

        monkeypatch.setattr(mlc.MusicLyricController, "_announce", lambda self, *a, **k: None)
        monkeypatch.setattr(mlc.MusicLyricController, "_show", lambda self, *a, **k: None)
        monkeypatch.setattr(mlc.MusicLyricController, "_bubble_blocked", lambda self: False)
        return mlc, mlc.MusicLyricController(_FakeWin())

    @staticmethod
    def _playback(position, updated_at):
        from types import SimpleNamespace
        return SimpleNamespace(position=position, updated_at=updated_at)

    def test_stale_pending_playback_never_leaks_across_songs(self, monkeypatch):
        mlc, ctl = self._controller(monkeypatch)
        pb_a = self._playback(None, 100.0)        # A 无真实进度（网易云型）
        pb_b = self._playback(42.0, 100.0)        # B 有真实进度（QQ 音乐型）

        # A 开始取词 → 快切 B → 再切回 A（A 在 _loading 早退，不重建上下文）
        monkeypatch.setattr(ctl, "_lyric_query", lambda t, a, p: (t, a))
        # 取词线程只驱动 _loading / pending 记账，本用例不验网络：真上线会往三个
        # 歌词源（以及有系统代理时的回退路线）发真实请求，属于测试不该碰的边界。
        monkeypatch.setattr(ctl, "_fetch_worker", lambda *a, **k: None)
        ctl._start_track("keyA", "歌A", "手A", pb_a, now=100.0)
        ctl._start_track("keyB", "歌B", "手B", pb_b, now=101.0)
        ctl._start_track("keyA", "歌A", "手A", pb_a, now=102.0)   # _loading 命中，早退
        ctl._current_key = "keyA"

        # A 的结果回来：必须读 A 自己的（无进度）上下文——reported=False
        from pet.music.lyric import LyricLine, Lyrics
        lyrics = Lyrics(lines=(LyricLine(1.0, "第一句"), LyricLine(5.0, "第二句")))

        monkeypatch.setattr(ctl, "_enter_instrumental_mode", lambda l: None)
        ctl._on_lyrics_ready("keyA", lyrics)
        loaded = ctl._tracker
        assert getattr(loaded, "_reported_position", None) in (False, None), (
            "A 的歌词被 B 的真实进度污染（reported=True 会置灰对齐一整首）")


# ------------------------------------------------- 关闭歌词后迟到采样的代次守卫
#
# 全产品审查 C4（2026-10-08）：``sync_enabled(False)`` 停表 + 置停止标 + ``_reset()``，
# 但采样线程可能正卡在 WinRT 的 ``get_now_playing()`` 里（OS 边界，实测会长时间不
# 返回）。它返回后照常 ``_playback_ready.emit``，主线程回调只查窗口可见性、不查启用
# 态与代次：``_reset()`` 已把 ``_current_key`` 清空，迟到结果于是被当成"新歌"——重新
# 取词并把歌词顶上屏，用户明明关了歌词，屏幕却冒出歌词、后台还白跑一次网络。


class _GatedSampler:
    """可控 SMTC 采样替身（只替换 OS 边界）：首次调用卡在事件上，之后立即返回。"""

    def __init__(self, playback) -> None:
        import threading

        self.playback = playback
        self.calls = 0
        self.first_started = threading.Event()
        self.release_first = threading.Event()

    def __call__(self, tracked_app_id=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            self.first_started.set()
            self.release_first.wait(timeout=10)
        return self.playback


def _pump_until(predicate, *, timeout=10.0) -> bool:
    """宽预算轮询 + 事件泵：采样线程到主线程的 ``_playback_ready`` 是队列投递。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return predicate()


def _gated_controller(monkeypatch, *, title="夜曲", artist="周杰伦"):
    """真实控制器 + 真实采样线程，只把 SMTC 采样换成卡在事件上的替身。"""
    from pet.music import now_playing
    from pet.music.lyric_controller import MusicLyricController

    sampler = _GatedSampler(_playback(None, title=title, artist=artist))
    monkeypatch.setattr(now_playing, "get_now_playing", sampler)
    win = _FakeWinVisible()
    ctrl = MusicLyricController(win)
    fetched: list = []
    monkeypatch.setattr(ctrl, "_fetch_worker", lambda *a, **k: fetched.append(a))
    return win, ctrl, sampler, fetched


def test_late_sample_after_disable_is_dropped(qapp, monkeypatch):
    """关闭歌词后迟到的采样：不取词、不上屏（代次守卫）。"""
    win, ctrl, sampler, fetched = _gated_controller(monkeypatch)
    try:
        ctrl.sync_enabled(True)
        assert sampler.first_started.wait(timeout=10), "前提：采样已进入 SMTC 调用"
        ctrl.sync_enabled(False)      # 用户关掉歌词：停表 + 置停止标 + _reset()
        sampler.release_first.set()   # 放行卡住的采样 → 迟到结果回到主线程

        assert _pump_until(lambda: ctrl._sampling is False), "迟到采样未回到主线程"
        assert fetched == [], "关闭后迟到的采样不得重新派发取词"
        assert win.shown == [], "关闭后迟到的采样不得把歌名/歌词顶上屏"
    finally:
        ctrl.shutdown()


def test_sample_callback_still_processed_when_enabled(qapp, monkeypatch):
    """非回归：开启状态下采样回调照常推进（代次守卫不得误杀正常一拍）。"""
    win, ctrl, sampler, fetched = _gated_controller(monkeypatch)
    try:
        ctrl.sync_enabled(True)
        assert sampler.first_started.wait(timeout=10)
        sampler.release_first.set()

        assert _pump_until(lambda: fetched), "开启状态下采样结果必须照常处理"
        assert win.shown, "开启状态下歌名必须照常亮出"
    finally:
        ctrl.shutdown()


def test_resample_after_disable_enable_is_processed(qapp, monkeypatch):
    """关闭再开启：新一代采样照常处理（代次不串，不把新结果当陈旧的丢）。"""
    win, ctrl, sampler, fetched = _gated_controller(monkeypatch)
    try:
        ctrl.sync_enabled(True)
        assert sampler.first_started.wait(timeout=10)
        ctrl.sync_enabled(False)
        sampler.release_first.set()
        assert _pump_until(lambda: ctrl._sampling is False)
        assert fetched == []

        ctrl.sync_enabled(True)       # 重新开启：新采样（第 2 次调用）立即返回
        assert _pump_until(lambda: fetched), "关闭再开启后新采样不得被误丢"
    finally:
        ctrl.shutdown()


class _IndexedGatedSampler:
    """按调用序号分别卡放的 SMTC 采样替身（只替换 OS 边界）。

    第 N 次调用先置 ``started[N]`` 再等 ``release[N]``，于是测试能精确摆出
    「旧那一拍还卡在 WinRT 里、关闭重开后新的一拍已经派发」那一刻；超出
    ``playbacks`` 长度的调用复用最后一格（已放行则立即返回）。
    """

    def __init__(self, playbacks) -> None:
        import threading

        self.playbacks = list(playbacks)
        self.calls = 0
        self.started = [threading.Event() for _ in self.playbacks]
        self.release = [threading.Event() for _ in self.playbacks]

    def __call__(self, tracked_app_id=None, **kwargs):
        index = min(self.calls, len(self.playbacks) - 1)
        self.calls += 1
        self.started[index].set()
        self.release[index].wait(timeout=30)
        return self.playbacks[index]


class _PlaybackReadySpy(QObject):
    """记录 ``_playback_ready`` 到达主线程几次：**队列投递的顺序证据**。

    产品回调在 ``MusicLyricController.__init__`` 里先连，本替身后连；同一次发射
    按连接顺序各投一个事件，事件队列 FIFO，因此「spy 见到第 N 次」意味着产品
    回调已经处理过第 N 次——这正是断言「迟到结果什么也没做」所需的时序证据。
    """

    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    def on_ready(self, *_args) -> None:
        self.count += 1


def test_stale_sample_after_disable_enable_does_not_clear_busy_gate(qapp, monkeypatch):
    """关闭+重开后，关闭前那一拍的迟到结果必须被丢弃，且不得清掉忙门（F5）。

    旧实现把「本拍代次」记在**可变单槽** ``_dispatch_generation`` 里：``_reset()``
    只递增 ``_generation``、不回写这个槽，于是关闭后立刻重开时，``sync_enabled(True)``
    同步调用的 ``_on_tick`` 把该槽覆盖成新代次。关闭前派发、此刻才回来的旧采样
    读到的是**新**代次 → 被当成本代结果接受，并顺手清掉忙门：重开后派发的那一拍
    还在 WinRT 里飞着，忙门却已经打开，"同一时刻只允许一拍在途"就此破裂（两拍
    并发回来会乱序推进状态）。
    """
    from pet.music import now_playing
    from pet.music.lyric_controller import MusicLyricController, NOW_SINGING_TEMPLATE

    stale = _playback(None, title="旧歌", artist="旧手")   # 关闭前那一拍的结果
    fresh = _playback(None, title="新歌", artist="新手")   # 重开后那一拍的结果
    fresh_title = NOW_SINGING_TEMPLATE.format(title="新歌")
    sampler = _IndexedGatedSampler([stale, fresh])
    monkeypatch.setattr(now_playing, "get_now_playing", sampler)

    win = _FakeWinVisible()
    ctrl = MusicLyricController(win)
    fetched: list = []
    monkeypatch.setattr(ctrl, "_fetch_worker", lambda *a, **k: fetched.append(a))
    spy = _PlaybackReadySpy()
    ctrl._playback_ready.connect(spy.on_ready, Qt.ConnectionType.QueuedConnection)
    try:
        ctrl.sync_enabled(True)                 # 派发第 1 拍（旧代）→ 卡在 SMTC 里
        assert sampler.started[0].wait(timeout=10), "前提：第 1 拍已进入 SMTC 调用"

        ctrl.sync_enabled(False)                # 关闭：_reset() 递增代次
        ctrl.sync_enabled(True)                 # 立刻重开：同步 _on_tick 派发第 2 拍
        assert ctrl._sampling is True, "前提：第 2 拍已派发（worker 还卡在第 1 拍里）"

        sampler.release[0].set()                # 旧那一拍迟到返回
        assert _pump_until(lambda: spy.count >= 1), "迟到采样未回到主线程"
        assert ctrl._sampling is True, (
            "上一代的迟到结果清掉了忙门：此刻第 2 拍还在 SMTC 里飞，"
            "忙门却已打开，一拍在途的约束失效"
        )
        assert win.shown == [], "上一代的迟到结果不得把旧歌名顶上屏"
        assert fetched == [], "上一代的迟到结果不得重新派发取词"

        sampler.release[1].set()                # 第 2 拍（本代）照常处理
        assert _pump_until(lambda: fresh_title in [t for _, t in win.shown]), (
            "重开后的本代采样被令牌守卫误杀"
        )
        assert {t for _, t in win.shown} == {fresh_title}, (
            "只有本代那一拍能上屏；出现别的标题说明上一代的结果也被认了"
        )
        assert ctrl._current_key == ("新歌", "新手")
        assert all(a[0] == ("新歌", "新手") for a in fetched), fetched
    finally:
        sampler.release[0].set()
        sampler.release[1].set()
        ctrl.shutdown()


def test_music_mode_reentry_after_inflight_sample_keeps_sampling(qapp, monkeypatch):
    """退出音乐模式再进入：在途那一拍不得把忙门留下永久闭着（F5 配套）。

    「退出音乐模式」（``set_music_mode_enabled(False)``）只停表 + ``_reset()``，
    **不停采样线程**；那一拍的结果回来时在途令牌已被作废 → 令牌守卫直接丢弃。
    丢弃路径不清忙门（忙门属于"在途派发"，这一拍已被作废），所以作废方必须自己
    结清：否则忙门卡在 True，而这是唯一会来的结果（表已停），用户再进音乐模式时
    ``_on_tick`` 每拍都在第一道闸门返回、唤醒口也再没人置位——歌词直到重启都不
    再更新。
    """
    from pet.music import now_playing
    from pet.music.lyric_controller import MusicLyricController

    sampler = _IndexedGatedSampler([_playback(None, title="歌", artist="手")])
    monkeypatch.setattr(now_playing, "get_now_playing", sampler)

    win = _FakeWinVisible()
    ctrl = MusicLyricController(win)
    monkeypatch.setattr(ctrl, "_fetch_worker", lambda *a, **k: None)
    spy = _PlaybackReadySpy()
    ctrl._playback_ready.connect(spy.on_ready, Qt.ConnectionType.QueuedConnection)
    try:
        ctrl.sync_enabled(True)
        assert sampler.started[0].wait(timeout=10), "前提：采样已进入 SMTC 调用"

        ctrl.set_music_mode_enabled(False)
        assert ctrl._sampling is False, "退出音乐模式必须结清在途那一拍的忙门"

        sampler.release[0].set()                # 那一拍迟到返回
        assert _pump_until(lambda: spy.count >= 1), "迟到采样未回到主线程"
        assert ctrl._sampling is False, "被作废的那一拍不得再把忙门拨回在途"
        assert win.shown == [], "退出音乐模式后迟到的采样不得把歌名顶上屏"

        ctrl.set_music_mode_enabled(True)       # 再进音乐模式
        assert _pump_until(lambda: sampler.calls >= 2), (
            "再进音乐模式后采样必须恢复；卡住说明忙门被留下一拍而永远闭着，"
            "_on_tick 每拍都在第一道闸门返回"
        )
    finally:
        sampler.release[0].set()
        ctrl.shutdown()



# ------------------------------------------ 取词在飞期间的暂停（B05）


def test_pause_during_lyric_fetch_is_not_counted_as_playback(monkeypatch):
    """取词在飞期间按的暂停不算播放时间（B05）。

    网易云这类播放器不上报进度，位置只能按"检测到切歌的时刻"往下估算；而取词那
    几秒里 tracker 里还没有歌词，``set_paused`` 冻结不了任何东西（位置基准要等
    歌词到位才由 ``load()`` 建立），暂停边沿也就没人记。结果取词回来按
    ``now - detected`` 估算时把暂停时长算成播放进度，歌词当场跳到未来。
    """
    import pet.music.lyric_controller as mod

    clock = _patch_clock(monkeypatch, 100.0)
    win = _FakeWin()
    ctrl = mod.MusicLyricController(win)
    monkeypatch.setattr(ctrl, "_fetch_worker", lambda *a, **k: None)   # 取词停在"在飞"
    key = ("t", "a")

    ctrl._on_playback_ready(_playback(None))          # 检测到歌 → 取词在飞
    assert ctrl._current_key == key and key in ctrl._loading
    assert ctrl._tracker.has_lyrics is False

    clock.now = 101.0
    ctrl._on_playback_ready(_playback(None, playing=False))   # 暂停
    clock.now = 104.0
    ctrl._on_playback_ready(_playback(None, playing=False))   # 还在暂停（同一段，不重复记）
    ctrl._on_playback_ready(_playback(None))                  # 恢复播放

    clock.now = 105.0
    ctrl._on_lyrics_ready(key, Lyrics(lines=tuple(LINES)))

    # 真实播放时间 = 105 - 100 - 3 = 2s（LINES: A0 B5 C10 D15 → 还没唱到第一句）
    assert ctrl._tracker.position(105.0) == pytest.approx(2.0)
    assert ctrl._tracker.has_lyrics is True
    assert ctrl.align_available() is True, "估算位置仍然要能手动对齐"
    # 暂停区间的结算：段内重复采样不重复记，恢复那一拍封口。
    assert ctrl._paused_since is None and ctrl._paused_total == pytest.approx(3.0)


def test_pause_still_open_when_lyrics_arrive_freezes_clock(monkeypatch):
    """取词回来时仍在暂停：以暂停态装载、**不立即显示**，恢复后从正确位置接上。

    暂停中的歌词位置必须冻结在"暂停那一刻"：``load()`` 会清掉暂停态，不显式冻结
    的话恢复播放后基准仍从 load 的时刻起算，暂停的这几秒又会被算成播放时间。
    """
    import pet.music.lyric_controller as mod

    clock = _patch_clock(monkeypatch, 100.0)
    win = _FakeWin()
    ctrl = mod.MusicLyricController(win)
    monkeypatch.setattr(ctrl, "_fetch_worker", lambda *a, **k: None)
    key = ("t", "a")

    ctrl._on_playback_ready(_playback(None))
    clock.now = 101.0
    ctrl._on_playback_ready(_playback(None, playing=False))
    shown_before = list(win.shown)

    clock.now = 105.0
    ctrl._on_lyrics_ready(key, Lyrics(lines=tuple(LINES)))

    assert win.shown == shown_before, "仍暂停时不显示歌词（恢复播放那一拍再显示）"
    assert ctrl._tracker.position(105.0) == pytest.approx(1.0), "105-100-4=1s"
    # 暂停期间位置不动
    assert ctrl._tracker.position(108.0) == pytest.approx(1.0)

    clock.now = 110.0
    ctrl._on_playback_ready(_playback(None))          # 恢复播放
    assert ctrl._tracker.position(110.0) == pytest.approx(1.0), "从冻结处顺延，不跳"
    assert win.shown[-1] == ("我在唱《t》", "A"), win.shown[-1]

    clock.now = 111.0
    ctrl._on_playback_ready(_playback(None))
    assert ctrl._tracker.position(111.0) == pytest.approx(2.0), "恢复后按真实播放时间走"


def test_pause_bookkeeping_resets_on_track_change(monkeypatch):
    """切歌重置暂停记账：上一首的暂停时长绝不能算进下一首的进度。"""
    import pet.music.lyric_controller as mod

    clock = _patch_clock(monkeypatch, 100.0)
    win = _FakeWin()
    ctrl = mod.MusicLyricController(win)
    monkeypatch.setattr(ctrl, "_fetch_worker", lambda *a, **k: None)

    ctrl._on_playback_ready(_playback(None))
    clock.now = 101.0
    ctrl._on_playback_ready(_playback(None, playing=False))
    assert ctrl._paused_since == pytest.approx(101.0)

    clock.now = 102.0
    ctrl._on_playback_ready(_playback(None, title="other", artist="x"))

    assert ctrl._paused_since is None
    assert ctrl._paused_total == pytest.approx(0.0)


def test_pause_during_fetch_freezes_clock_even_when_bubble_is_taken(monkeypatch):
    """取词期间暂停 + 完成时气泡被占：冻结时钟不许被"不显示"那条早退跳过。

    ``load()`` 会清掉暂停态，所以"仍暂停就以暂停态装载"这一步不可省；它一旦排在
    ``_bubble_blocked()`` 早退之后，完成时气泡被别的提醒占着就直接 return——tracker
    按播放态从 load 那一刻起算，取词期间的暂停被算成播放时间，恢复播放后歌词停在
    错的那一句。冻结时钟与"是否显示"是两件事，必须解耦。
    """
    import pet.music.lyric_controller as mod

    clock = _patch_clock(monkeypatch, 100.0)
    win = _FakeWin()
    ctrl = mod.MusicLyricController(win)
    monkeypatch.setattr(ctrl, "_fetch_worker", lambda *a, **k: None)
    key = ("t", "a")

    ctrl._on_playback_ready(_playback(None))                 # 检测到歌 → 取词在飞
    clock.now = 101.0
    ctrl._on_playback_ready(_playback(None, playing=False))  # 取词在飞期间暂停
    win._alert_current = {"id": "agent-approval"}            # 完成那一刻气泡被占
    shown_before = list(win.shown)

    clock.now = 105.0
    ctrl._on_lyrics_ready(key, Lyrics(lines=tuple(LINES)))

    assert win.shown == shown_before, "气泡被占时仍然不显示歌词"
    assert ctrl._tracker.position(105.0) == pytest.approx(1.0), "105-100-4=1s"
    assert ctrl._tracker.position(108.0) == pytest.approx(1.0), "暂停态：位置不许前进"

    win._alert_current = None
    clock.now = 110.0
    ctrl._on_playback_ready(_playback(None))                 # 恢复播放

    # 暂停段 101→110 全程 9s（含"歌词到位时仍暂停"的那段），恢复这一拍封口
    assert ctrl._paused_since is None and ctrl._paused_total == pytest.approx(9.0)
    assert ctrl._tracker.position(110.0) == pytest.approx(1.0), "从冻结处顺延，不跳"
    assert win.shown[-1] == ("我在唱《t》", "A"), win.shown[-1]


def test_pause_during_fetch_is_deducted_from_reported_progress(monkeypatch):
    """有真实进度的播放器同样要扣取词期间的暂停（窗口里并非全是播放时间）。

    ``reported + (now - updated_at)`` 把整段取词耗时都当成播放时间：取词期间按的
    暂停会被算进位置，歌词当场跳到未来。暂停时长要扣，但**只能扣窗口里那部分**——
    保守取 min(暂停, 窗口)，宁可少扣也绝不多扣（多扣会把位置压到采样值以下，
    歌词相对播放器倒退）。
    """
    import pet.music.lyric_controller as mod
    from pet.music.now_playing import Playback, Track

    clock = _patch_clock(monkeypatch, 100.0)
    win = _FakeWin()
    ctrl = mod.MusicLyricController(win)
    monkeypatch.setattr(ctrl, "_fetch_worker", lambda *a, **k: None)
    key = ("t", "a")

    def sample(position, *, playing=True):
        return Playback(track=Track(title="t", artist="a", playing=playing),
                        position=position, updated_at=clock.now)

    ctrl._on_playback_ready(sample(30.0))                    # 取词在飞：真值 30s @100s
    clock.now = 101.0
    ctrl._on_playback_ready(sample(30.0, playing=False))     # 暂停
    clock.now = 104.0
    ctrl._on_playback_ready(sample(30.0))                    # 恢复（暂停段 = 3s）

    clock.now = 105.0
    ctrl._on_lyrics_ready(key, Lyrics(lines=(
        LyricLine(0.0, "A"), LyricLine(30.0, "B"), LyricLine(34.0, "C"))))

    # 30 + (105-100) - 3 = 32s；不扣暂停是 35s（越过 34s 那句 → 歌词超前一整句）
    assert ctrl._tracker.position(105.0) == pytest.approx(32.0)
    assert ctrl._tracker.uses_reported_position is True, "真值口径不变，仍不提供手动对齐"
    assert win.shown[-1] == ("我在唱《t》", "B"), win.shown[-1]


def test_pause_before_a_retry_dispatch_is_not_deducted_from_reported_progress(monkeypatch):
    """同曲失败重试：只扣**本次派发之后**新增的暂停（astra F3）。

    `_paused_seconds` 是**整首歌**累计（只在切歌时复位），而同曲失败重试会换一份新的
    采样上下文——位置窗口 `[updated_at, now]` 从这次派发起算，早于它的暂停段（首试与
    冷却期）根本不在窗口里。旧实现按窗口长度封顶（`min(暂停, 窗口)`）只保证"位置不低于
    采样值"，保证不了"扣的暂停属于窗口"：30s 冷却期暂停撞上 5s 窗口，会把 105 算成 100
    （歌词退回一整句）。修法是派发那一拍快照累计暂停，完成时只扣增量。
    """
    import pet.music.lyric_controller as mod
    from pet.music.now_playing import Playback, Track

    clock = _patch_clock(monkeypatch, 100.0)
    win = _FakeWin()
    ctrl = mod.MusicLyricController(win)
    monkeypatch.setattr(ctrl, "_fetch_worker", lambda *a, **k: None)
    key = ("t", "a")

    def sample(position):
        return Playback(track=Track(title="t", artist="a", playing=True),
                        position=position, updated_at=clock.now)

    def paused_at(moment):
        clock.now = moment
        ctrl._on_playback_ready(Playback(
            track=Track(title="t", artist="a", playing=False),
            position=100.0, updated_at=clock.now))

    ctrl._on_playback_ready(sample(100.0))        # t=100 首试派发（真值 100s）
    paused_at(101.0)
    clock.now = 131.0
    ctrl._on_playback_ready(sample(100.0))        # t=131 恢复 → 冷却期暂停累计 30s

    clock.now = 135.0
    ctrl._on_lyrics_ready(key, None)              # 首试失败：记 5 分钟冷却
    assert ctrl._no_lyric_until[key] > clock.now

    clock.now = 436.0
    ctrl._on_playback_ready(sample(100.0))        # 冷却到期：同曲重试 = 新派发
    assert key in ctrl._loading, "前提：重试真的派发了"

    clock.now = 441.0
    ctrl._on_lyrics_ready(key, Lyrics(lines=(
        LyricLine(0.0, "A"), LyricLine(100.0, "B"), LyricLine(104.0, "C"))))

    # 窗口只有 5s（436→441，其间零暂停）：100 + 5 - 0 = 105。
    # 按整首累计扣（旧的封顶写法）会算成 100 + 5 - min(30, 5) = 100。
    assert ctrl._tracker.position(441.0) == pytest.approx(105.0)
    assert win.shown[-1] == ("我在唱《t》", "C"), win.shown[-1]   # 错的 100 → B（退回一句）


def test_cooldown_pause_is_still_deducted_without_reported_progress(monkeypatch):
    """无进度分支的窗口起点是**切歌时刻**，所以重试前的暂停照样要扣（刻意的不同）。

    两个分支只共用"同一份暂停记账 + 只扣锚点之后的暂停"这一条口径，锚点不同：
    有进度 → 本次派发那次采样；无进度 → 检测到切歌的时刻（`_detected_at`，那里的
    暂停记账本就归零）。把"派发基线"照搬到无进度分支会**漏扣**冷却期暂停，位置偏大。
    """
    import pet.music.lyric_controller as mod

    clock = _patch_clock(monkeypatch, 100.0)
    win = _FakeWin()
    ctrl = mod.MusicLyricController(win)
    monkeypatch.setattr(ctrl, "_fetch_worker", lambda *a, **k: None)
    key = ("t", "a")

    ctrl._on_playback_ready(_playback(None))            # t=100 检测到歌（无进度源）
    clock.now = 101.0
    ctrl._on_playback_ready(_playback(None, playing=False))
    clock.now = 131.0
    ctrl._on_playback_ready(_playback(None))            # 恢复：暂停累计 30s

    clock.now = 135.0
    ctrl._on_lyrics_ready(key, None)                    # 首试失败 → 冷却
    clock.now = 436.0
    ctrl._on_playback_ready(_playback(None))            # 冷却到期：重试派发

    clock.now = 441.0
    ctrl._on_lyrics_ready(key, Lyrics(lines=tuple(LINES)))

    # 441 - 100 - 30 = 311（冷却期的 300s 算作播放——无进度源只能这么估，本修复没改）
    assert ctrl._tracker.position(441.0) == pytest.approx(311.0)


def test_name_matches_multi_artist_separator_variants():
    """多歌手分隔符归一：酷狗报「、」歌词源写「/」，两侧都是多歌手时逐段比对。"""
    # 实机样本（酷狗 20.1 SMTC → 网易云歌词源）
    assert music_lyric._name_matches("FLOW/GRANRODEO", "FLOW、GRANRODEO")
    assert music_lyric._name_matches("FLOW、GRANRODEO", "FLOW/GRANRODEO")
    assert music_lyric._name_matches("周杰伦/蔡依林", "周杰伦、蔡依林")
    assert music_lyric._name_matches("FLOW feat. GRANRODEO", "FLOW、GRANRODEO")
    assert music_lyric._name_matches("FLOW, GRANRODEO", "FLOW、GRANRODEO")


def test_name_matches_multi_artist_does_not_overreach():
    """单侧无分隔符保持原语义；两侧多歌手但谁都不沾边不误判。"""
    assert not music_lyric._name_matches("周杰伦", "林俊杰、蔡依林")
    assert not music_lyric._name_matches("A", "B、C")
    assert not music_lyric._name_matches("FLOW/GRANRODEO", "MUSE、Aimer")


def test_name_matches_feat_split_never_shreds_single_names():
    """feat/ft 必须带词边界（盲审 P1）：swift/craft/daft punk 这类含 ft/feat
    字母序列的单名不得被拆碎成"多歌手形态"，碎片子串不得乱命中。"""
    for single in ("Taylor Swift", "Craft", "Daft Punk", "Soft Cell", "Leftfield",
                   "Featherstone"):
        # 单名 vs 多歌手：单侧无分隔符，不许借碎片命中
        assert not music_lyric._name_matches(single, "林俊杰、蔡依林"), single
    # 碎片假阳性实案（ops5.5 审查实测）：'cra' 不得借 'raf' 碎片命中
    assert not music_lyric._name_matches("Craft/Lift", "Soft、Raft")
    # 逐段相等：任一段相等即命中（"BTS/Halsey" 与 "BTS、Coldplay" 共有 'bts' 段）
    assert music_lyric._name_matches("BTS/Halsey", "BTS、Coldplay")
    assert not music_lyric._name_matches("BTS/Halsey", "BT、Coldplay")  # 'bt' 不是 'bts'
    # feat 的正常形态仍认（词边界 + 可选句点）
    assert music_lyric._name_matches("FLOW feat. GRANRODEO", "FLOW、GRANRODEO")
    assert music_lyric._name_matches("FLOW ft GRANRODEO", "FLOW/GRANRODEO")


def test_name_matches_feat_split_has_both_word_boundaries():
    """feat/ft 双侧词边界（astra 盲审）：Featherstone→herstone、FTIsland→island
    这类词首拆碎不得发生，拆出的碎片不得参与命中。"""
    assert not music_lyric._name_matches("Featherstone/Alpha", "Herstone、Beta")
    assert not music_lyric._name_matches("FTIsland/Alpha", "Island、Beta")
    # 左侧边界（消融：去掉 feat/ft 前的 \b 时这三条必须转红）：没有左边界时
    # "ft" 在单词内部就被当分隔符切，"Daft Punk"→"Da"+"Punk"、
    # "Swift Lefty"→"Swi"+"Lefty"、"Soft Cell"→"So"+"Cell"——切出的碎片跟对侧
    # 真多歌手串里的段相等，单名就被误认成多歌手命中。
    assert not music_lyric._name_matches("Daft Punk", "Da、Punk")
    assert not music_lyric._name_matches("Swift Lefty", "Swi、X")
    assert not music_lyric._name_matches("Soft Cell", "So、胖胖")
    # 正例仍认：独立 feat 标记（含无空格/带句点形态）
    assert music_lyric._name_matches("FLOW feat. GRANRODEO", "FLOW、GRANRODEO")
    assert music_lyric._name_matches("FLOW feat.GRANRODEO", "FLOW、GRANRODEO")
    assert music_lyric._name_matches("FLOW ft GRANRODEO", "FLOW/GRANRODEO")
    assert music_lyric._name_matches("FLOW ft. GRANRODEO", "FLOW/GRANRODEO")
