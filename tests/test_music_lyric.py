# -*- coding: utf-8 -*-
"""歌词功能的离线单元测试。

全部用例都不联网：网络部分用固定样本注入，只验证解析、匹配与进度跟踪逻辑。
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request

from pet import music_lyric
from pet.music_lyric import Lyrics, LyricLine, parse_lrc
from pet.music_lyric_controller import LyricTracker


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

    monkeypatch.setattr(
        music_lyric,
        "_SOURCES",
        (
            ("qq", lambda t, a: qq_lines),
            ("lrclib", lambda t, a: lrclib_lines),
        ),
    )
    monkeypatch.setattr(music_lyric, "_write_cache", lambda *a, **k: None)
    monkeypatch.setattr(music_lyric, "_read_cache", lambda *a, **k: None)

    assert music_lyric.fetch_lyrics("歌", "手") == qq_lines


def test_fetch_lyrics_falls_back_to_lower_priority(monkeypatch):
    """高优先级源无结果时，采用低优先级源的结果。"""
    lrclib_lines = [LyricLine(2.0, "来自lrclib")]

    monkeypatch.setattr(
        music_lyric,
        "_SOURCES",
        (
            ("qq", lambda t, a: None),
            ("lrclib", lambda t, a: lrclib_lines),
        ),
    )
    monkeypatch.setattr(music_lyric, "_write_cache", lambda *a, **k: None)
    monkeypatch.setattr(music_lyric, "_read_cache", lambda *a, **k: None)

    assert music_lyric.fetch_lyrics("歌", "手") == lrclib_lines


def test_fetch_lyrics_survives_source_exception(monkeypatch):
    """某个源抛异常不能拖垮整体，应继续尝试后续源。"""

    def boom(title, artist):
        raise RuntimeError("接口炸了")

    expected = [LyricLine(1.0, "ok")]
    monkeypatch.setattr(
        music_lyric,
        "_SOURCES",
        (
            ("boom", boom),
            ("good", lambda t, a: expected),
        ),
    )
    monkeypatch.setattr(music_lyric, "_write_cache", lambda *a, **k: None)
    monkeypatch.setattr(music_lyric, "_read_cache", lambda *a, **k: None)

    assert music_lyric.fetch_lyrics("歌", "手") == expected


def test_fetch_lyrics_returns_none_when_all_fail(monkeypatch):
    monkeypatch.setattr(music_lyric, "_SOURCES", (("none", lambda t, a: None),))
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
    assert t.advance(102.0) == -1  # 同一句：不重复上报
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
    from pet.music_lyric_controller import LYRIC_LEAD_SECONDS

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


from PySide6.QtCore import QObject


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
    from pet.music_lyric_controller import MusicLyricController

    win = _FakeWin()
    ctrl = MusicLyricController(win)
    ctrl._current_key = None
    ctrl._primed = True  # 已过首次边界，不是"开启时已在播"那首
    playback = type("P", (), {"position": None, "updated_at": 0.0})()

    ctrl._start_track(("夜曲", "周杰伦"), "夜曲", "周杰伦", playback, 100.0)
    assert win.shown == [("", "我在唱《夜曲》")], win.shown
    assert ctrl._title_line == "我在唱《夜曲》"


def test_title_persists_after_lyrics_arrive():
    """歌词到位后，标题必须仍在第一行（这就是本次需求的核心）。"""
    from pet.music_lyric_controller import MusicLyricController

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
    from pet.music_lyric_controller import MusicLyricController

    win = _FakeWin()
    ctrl = MusicLyricController(win)
    ctrl._current_key = ("x", "y")
    ctrl._title_line = "我在唱《x》"
    ctrl._on_lyrics_ready(("x", "y"), None)

    assert ("x", "y") in ctrl._no_lyric_keys
    assert win.shown[-1] == ("", "我在唱《x》"), win.shown[-1]
    assert ctrl._title_line == "我在唱《x》"


def test_title_goes_into_body_when_no_lyric():
    """没有歌词时必须让标题走正文。

    气泡在正文为空时直接不渲染，若标题只放 subtitle，取词那几秒会一片空白。
    """
    from pet.music_lyric_controller import split_bubble_text

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
    from pet.music_lyric import LyricLine, _looks_instrumental

    # QQ音乐实测的两种占位文案
    assert _looks_instrumental([LyricLine(0.0, "此歌曲为没有填词的纯音乐，请您欣赏")])
    assert _looks_instrumental([LyricLine(1.58, "纯音乐，请欣赏")])
    assert _looks_instrumental([LyricLine(0.0, "暂无歌词")])
    # 英文
    assert _looks_instrumental([LyricLine(0.0, "Instrumental")])


def test_looks_instrumental_ignores_real_lyrics():
    """真歌词不能被误判，哪怕某一行提到「纯音乐」。"""
    from pet.music_lyric import LyricLine, _looks_instrumental

    # 多行真歌词
    assert not _looks_instrumental(
        [
            LyricLine(0.0, "一群嗜血的蚂蚁"),
            LyricLine(5.0, "被腐肉所吸引"),
            LyricLine(10.0, "我面无表情看孤独的风景"),
        ]
    )
    # 行数少但内容是正常歌词
    assert not _looks_instrumental([LyricLine(0.0, "这首纯音乐真好听")])
    # 空列表不算纯音乐
    assert not _looks_instrumental([])


def test_lyrics_dataclass_bool_and_len():
    from pet.music_lyric import Lyrics

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
    from pet.music_lyric_controller import INSTRUMENTAL_HINTS

    assert len(INSTRUMENTAL_HINTS) >= 4, "至少 4 条"
    assert len(set(INSTRUMENTAL_HINTS)) == len(INSTRUMENTAL_HINTS), "不应重复"
    joined = "".join(INSTRUMENTAL_HINTS)
    assert "主人" in joined or "人家" in joined, "应体现女仆口吻"


def test_pick_instrumental_hint_returns_member():
    from pet.music_lyric_controller import (
        INSTRUMENTAL_HINTS,
        pick_instrumental_hint,
    )

    for _ in range(20):
        assert pick_instrumental_hint() in INSTRUMENTAL_HINTS


def test_now_listening_template():
    from pet.music_lyric_controller import NOW_LISTENING_TEMPLATE

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
    from pet.music_lyric_controller import LYRIC_YIELD_SECONDS, MusicLyricController

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
    from pet.music_lyric_controller import MusicLyricController

    win = _WinWithBubble()
    ctrl = MusicLyricController(win)
    ctrl._title_line = "我在唱《夜曲》"
    ctrl._lyric_yield_until = time.monotonic() + 5.0  # 正在让路

    ctrl._show("一句歌词", title="我在唱《夜曲》", force=True)
    assert win.shown == [], "让路期间不应往气泡写任何东西"


def test_yield_expires_and_lyrics_resume():
    """让路到期后歌词恢复显示。"""
    from pet.music_lyric_controller import MusicLyricController

    win = _WinWithBubble()
    ctrl = MusicLyricController(win)
    ctrl._title_line = "我在唱《夜曲》"
    win._speech_bubble._subtitle_label.set("我在唱《夜曲》")
    ctrl._lyric_yield_until = time.monotonic() - 0.1  # 已过期

    ctrl._show("一句歌词", title="我在唱《夜曲》", force=True)
    assert win.shown, "让路到期后应恢复显示"
    assert win.shown[-1] == ("我在唱《夜曲》", "一句歌词")


def test_hidden_bubble_is_not_treated_as_taken():
    """气泡不可见（没有内容）不算被抢占，否则开头会被误判让路。"""
    from pet.music_lyric_controller import MusicLyricController

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
    monkeypatch.setattr(music_lyric, "_SOURCES", (("qq", lambda t, a: lyrics),))
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
    from pet.music_lyric_controller import MusicLyricController

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
    from pet.music_lyric_controller import MusicLyricController

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
    from pet.music_lyric_controller import MusicLyricController

    win = _FakeWinVisible()
    ctrl = MusicLyricController(win)
    try:
        ctrl.sync_enabled(True)
        first = ctrl._sample_thread
        assert first is not None, "开启后应有采样线程"

        ctrl.sync_enabled(False)
        ctrl.sync_enabled(True)

        assert ctrl._sample_thread is first, "关闭再开启后应复用原采样线程；新建线程意味着旧线程被丢引用（孤儿），且 _sample_stop 未复位"
    finally:
        ctrl.sync_enabled(False)


def test_sampling_resumes_after_disable_enable(monkeypatch):
    """关掉歌词再打开，采样必须恢复——不是永久停摆。

    这是用户可见的后果：一旦发生过一次开关，歌词再也不随播放更新。
    """
    from pet import now_playing
    from pet.music_lyric_controller import MusicLyricController

    calls = {"n": 0}
    seen: list = []

    def fake_get_now_playing(tracked_app_id=None):
        calls["n"] += 1
        seen.append(tracked_app_id)
        return None

    monkeypatch.setattr(now_playing, "get_now_playing", fake_get_now_playing)

    win = _FakeWinVisible()
    ctrl = MusicLyricController(win)
    try:
        ctrl.sync_enabled(True)
        assert _wait_until(lambda: calls["n"] >= 1), "首次开启后应采样"
        # 采样必须带上"当前跟踪的播放器"，否则会话选取会在两个播放器之间乱跳
        assert seen and seen[0] == "", seen

        before = calls["n"]
        ctrl.sync_enabled(False)
        ctrl.sync_enabled(True)
        # 再派发一拍；线程若因 _sample_stop 未复位而退出，这里就永远等不到。
        ctrl._sampling = False
        ctrl._on_tick()

        assert _wait_until(lambda: calls["n"] > before), "关闭再开启后采样必须恢复；卡住说明 _sample_stop 仍是 set 状态，采样线程一进循环就退出了"
    finally:
        ctrl.sync_enabled(False)


def test_shutdown_leaves_stop_flag_set():
    """shutdown 之后 ``_sample_stop`` 必须保持置位。

    这是「复位 _sample_stop」修复的反向保险：若写成无条件 clear（含关闭分支），
    线程的退出信号就被抹掉了——采样线程退出后会被再次拉起，shutdown 形同虚设。
    直接断言标志位，不依赖 sleep 时序。
    """
    from pet.music_lyric_controller import MusicLyricController

    win = _FakeWinVisible()
    ctrl = MusicLyricController(win)
    ctrl.sync_enabled(True)
    assert not ctrl._sample_stop.is_set(), "运行中不该处于停止态"

    ctrl.shutdown()
    assert ctrl._sample_stop.is_set(), "shutdown 后 _sample_stop 必须仍置位；被 clear 说明复位逻辑把「关」也一起复掉了"

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
    import pet.music_lyric_controller as mod

    clock = _Clock(now)
    monkeypatch.setattr(mod, "time", clock)
    return clock


def _playback(position=None, *, title="t", artist="a", playing=True):
    from pet.now_playing import Playback, Track

    return Playback(
        track=Track(title=title, artist=artist, playing=playing),
        position=position,
        updated_at=0.0,
    )


def _aligned_controller(monkeypatch, *, lines=None, position=None, now=100.0):
    """建一个已装载歌词、标题就绪的控制器（取词线程打桩，保持离线）。"""
    import pet.music_lyric_controller as mod

    clock = _patch_clock(monkeypatch, now)
    win = _FakeWin()
    ctrl = mod.MusicLyricController(win)
    monkeypatch.setattr(ctrl, "_fetch_worker", lambda *a, **k: None)
    ctrl._current_key = ("t", "a")
    ctrl._primed = True
    ctrl._title_line = "我在唱《t》"
    ctrl._tracker.lead = 0.0
    ctrl._tracker.load(list(lines if lines is not None else LINES), now=now, position=position)
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
    from pet.music_lyric import LyricLine as _LL

    win, ctrl, clock = _aligned_controller(
        monkeypatch,
        lines=[_LL(5.0, "A"), _LL(9.0, "B")],
    )
    clock.now = 111.0  # 本地时钟推到 11s → 第二句
    ctrl._on_playback_ready(_playback(None))
    assert win.shown[-1] == ("我在唱《t》", "B"), win.shown[-1]

    clock.now = 100.0  # 拖回开头 → 位置 0s，早于第一句
    ctrl._on_playback_ready(_playback(None))
    assert win.shown[-1] == ("", "我在唱《t》"), win.shown[-1]


def test_resync_to_start_realigns_local_clock(monkeypatch):
    """快进之后的救命入口：一键把"现在这句"当作开头。"""
    win, ctrl, clock = _aligned_controller(monkeypatch)
    clock.now = 111.0
    ctrl._on_playback_ready(_playback(None))
    assert ctrl._tracker.text_at(ctrl._tracker.index) == "C"  # LINES: A0 B5 C10 D15

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
    from pet.music_lyric import LyricLine as _LL

    lines = [_LL(0.0, "A"), _LL(2.0, "B"), _LL(3.0, "C")]
    win, ctrl, clock = _aligned_controller(monkeypatch, lines=lines)
    ctrl._tracker.lead = 1.0  # 提前量 > 行距
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

    assert ctrl.nudge(-6.0) is True  # 11s → 5s → 落在 B(5.0)
    assert win.shown[-1] == ("我在唱《t》", "B"), win.shown[-1]

    assert ctrl.nudge(5.0) is True  # 5s → 10s → 落在 C(10.0)
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
    import pet.music_lyric_controller as mod

    _patch_clock(monkeypatch, 100.0)
    win = _FakeWin()
    ctrl = mod.MusicLyricController(win)
    monkeypatch.setattr(ctrl, "_fetch_worker", lambda *a, **k: None)

    # 网易云式采样：SMTC 无时间轴 → position=None
    ctrl._on_playback_ready(_playback(None, title="讨厌红楼梦", artist="陶喆"))
    # 取词回到主线程：走生产路径（不是直接 load）
    ctrl._on_lyrics_ready(("讨厌红楼梦", "陶喆"), Lyrics(lines=tuple(LINES)))

    assert ctrl._tracker.has_lyrics is True
    assert ctrl.align_available() is True, "网易云不上报进度，位置是估算的，手动对齐正是唯一纠正手段，不该被关掉"
    assert ctrl.resync_to_line(1) is True
    assert ctrl.nudge(5.0) is True


def test_reported_position_still_disables_align(monkeypatch):
    """真上报进度的播放器（QQ 音乐）：取词后仍不许手动对齐，位置听真值。"""
    import pet.music_lyric_controller as mod

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
    win, ctrl, clock = _aligned_controller(monkeypatch)  # 本地时钟 + 有词
    assert ctrl.align_available() is True

    ctrl._tracker.reset()  # 无词
    assert ctrl.align_available() is False

    win2, ctrl2, _ = _aligned_controller(monkeypatch, position=1.0)  # 报进度
    ctrl2._on_playback_ready(_playback(1.0))
    assert ctrl2.align_available() is False

    win3, ctrl3, _ = _aligned_controller(monkeypatch)  # 纯音乐
    ctrl3._tracker.reset()
    ctrl3._instrumental = True
    assert ctrl3.align_available() is False


def test_start_track_first_song_without_position_still_announces_title(monkeypatch):
    """缺陷 1 回归：功能开启时歌已在播且播放器不报进度，必须至少亮出歌名。

    旧实现在 _announce() 之前就 return，用户看到的是一片空白，于是"识别不到
    我放到一半的歌"。
    """
    import pet.music_lyric_controller as mod

    _patch_clock(monkeypatch, 100.0)
    win = _FakeWin()
    ctrl = mod.MusicLyricController(win)
    monkeypatch.setattr(ctrl, "_fetch_worker", lambda *a, **k: None)
    assert ctrl._primed is False  # 首次边界：正是"开启时已在播"

    ctrl._start_track(("夜曲", "周杰伦"), "夜曲", "周杰伦", _playback(None), 100.0)

    assert win.shown == [("", "我在唱《夜曲》")], win.shown
    assert ctrl._title_line == "我在唱《夜曲》"
    # 锚点就是"检测到这首歌的时刻"——即把此刻当作第 0 秒
    assert ctrl._detected_at == 100.0


def test_start_track_first_song_without_position_fetches_lyrics(monkeypatch):
    """同一情形下不能因为"猜不到进度"就跳过取词——用户要的是看到歌词。"""
    import threading

    import pet.music_lyric_controller as mod

    _patch_clock(monkeypatch, 100.0)
    win = _FakeWin()
    ctrl = mod.MusicLyricController(win)
    started = threading.Event()
    monkeypatch.setattr(ctrl, "_fetch_worker", lambda *a, **k: started.set())

    ctrl._start_track(("夜曲", "周杰伦"), "夜曲", "周杰伦", _playback(None), 100.0)

    assert ("夜曲", "周杰伦") in ctrl._loading, "应已进入取词中"
    assert ctrl._pending_playback is not None
    assert started.wait(5.0), "取词线程应被启动"


def test_estimated_progress_hint_shown_once_per_session(monkeypatch):
    """不报进度的播放器：提示一次"快进后请用菜单对齐"，不反复打扰。"""
    import pet.music_lyric_controller as mod

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

    ctrl._start_track(("夜曲", "周杰伦"), "夜曲", "周杰伦", _playback(None), 100.0)
    assert len(win.alerts) == 1, win.alerts
    assert "对齐" in win.alerts[0]

    # 切到下一首（同样不报进度）不再重复提示
    ctrl._start_track(("晴天", "周杰伦"), "晴天", "周杰伦", _playback(None), 200.0)
    assert len(win.alerts) == 1, win.alerts


def test_no_hint_when_player_reports_progress(monkeypatch):
    """报进度的播放器不该看到这条提示。"""
    import pet.music_lyric_controller as mod

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


def test_resync_ignored_when_no_lyrics(monkeypatch):
    """还没取到词 / 无词歌：对齐入口不动作，也不该抛异常。"""
    win, ctrl, clock = _aligned_controller(monkeypatch)
    ctrl._tracker.reset()
    assert ctrl.resync_to_start() is False
    assert ctrl.resync_to_line(1) is False
    assert ctrl.nudge(5.0) is False


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
    """把 urllib 的 opener 构造换成记录桩，并假装系统里配着代理。"""
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"https": "http://127.0.0.1:12450"})
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
    """
    opener = _RecordingOpener({"ok": 1})
    built = _stub_urllib(monkeypatch, opener)

    assert music_lyric._http_get_json("https://c.y.qq.com/soso/x") == {"ok": 1}

    assert built, "歌词请求没有自建 opener —— 仍在用 urllib.request.urlopen，会继承系统代理"
    handlers = [h for h in built[0] if isinstance(h, urllib.request.ProxyHandler)]
    assert handlers, "直连 opener 必须显式传 ProxyHandler({})，否则仍继承系统代理"
    assert handlers[0].proxies == {}, handlers[0].proxies
    assert opener.calls and opener.calls[0][1] == music_lyric.HTTP_TIMEOUT


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


def test_bypassing_system_proxy_is_logged_once(monkeypatch, caplog):
    """「绕过了系统代理」只记一行、且带上代理地址，供下次一眼定位。"""
    monkeypatch.setattr(music_lyric, "_proxy_bypass_logged", False)
    opener = _RecordingOpener({"ok": 1})
    _stub_urllib(monkeypatch, opener)

    with caplog.at_level(logging.INFO):
        music_lyric._http_get_json("https://c.y.qq.com/soso/x")
        music_lyric._http_get_json("https://c.y.qq.com/soso/y")

    hits = [r.getMessage() for r in caplog.records if "系统代理" in r.getMessage()]
    assert len(hits) == 1, hits
    assert "127.0.0.1:12450" in hits[0], hits[0]
