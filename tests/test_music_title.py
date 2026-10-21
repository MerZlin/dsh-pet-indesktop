# -*- coding: utf-8 -*-
"""标题 → 取词查询词 的纯函数测试（pet/music_title.py）。

覆盖两条真实用户需求：

- **B 站 / 浏览器媒体会话的网页标题**（issue #228 的 B 站侧）：SMTC 给的 title
  就是网页标题（``【4K】周杰伦《晴天》官方MV_哔哩哔哩_bilibili``），直接拿去
  搜歌词命中率极低。
- **foobar2000 窗口标题**（issue #228 本体）：foobar 默认不注册 SMTC，只能从
  窗口标题拿曲目信息，而那是用户可配置的 titleformat 串。

真值表里的输入都是**真实风格**的标题（B 站/YouTube 的实际形态），不是编出来的
形状。误伤防护单独成组：清洗失败必须**整体退回原始输入**，宁可搜不到不可改坏。
"""

from __future__ import annotations

import random
import time

from pet.music import title as music_title


# ------------------------------------------------- 任务一：网页/浏览器标题清洗
#
# 期望值不是"跑出来的"而是按规则推出来的：
# - 书名号内是歌名（优先）；
# - 书名号外的最近一段（书名号前取最后一段、之后取第一段）是歌手；
# - 没有书名号时**不猜顺序**：整条清洗结果同时作歌名与歌手
#   （见 music_title._haystack_artist 的说明）。

WEB_TITLE_CASES = [
    # ---- B 站：书名号 + 站名后缀 ----
    ("【4K】周杰伦《晴天》官方MV_哔哩哔哩_bilibili", ("晴天", "周杰伦")),
    ("周杰伦《晴天》_哔哩哔哩_bilibili", ("晴天", "周杰伦")),
    ("【周杰伦】《晴天》官方MV - 哔哩哔哩", ("晴天", "周杰伦")),
    ("《晴天》- 周杰伦 - 哔哩哔哩", ("晴天", "周杰伦")),
    ("《晴天》 周杰伦 - 哔哩哔哩", ("晴天", "周杰伦")),
    ("【1080P】周杰伦《晴天》【Hi-Res无损】_哔哩哔哩_bilibili", ("晴天", "周杰伦")),
    ("【纯享版】周杰伦《晴天》现场版_哔哩哔哩_bilibili", ("晴天", "周杰伦")),
    ("【歌单】周杰伦《晴天》无损音质_哔哩哔哩_bilibili", ("晴天", "周杰伦")),
    ("【4K修复】周杰伦《晴天》高清MV", ("晴天", "周杰伦")),
    ("《晴天》- Jay Chou - 哔哩哔哩", ("晴天", "Jay Chou")),
    # ---- B 站：没有书名号 → 不猜（整条同时作歌名与歌手 haystack）----
    ("晴天 - 周杰伦_哔哩哔哩_bilibili", ("晴天 - 周杰伦", "晴天 - 周杰伦")),
    ("周杰伦 - 晴天 (Official MV) - 哔哩哔哩", ("周杰伦 - 晴天", "周杰伦 - 晴天")),
    ("周杰伦 - 晴天【官方MV】 - 哔哩哔哩", ("周杰伦 - 晴天", "周杰伦 - 晴天")),
    # ---- 歌名在**第二对**书名号里（前面那对是噪声段）----
    ("《官方MV》周杰伦《晴天》_哔哩哔哩_bilibili", ("晴天", "周杰伦")),
    # ---- B 站：UP 主段 / 分区标签 ----
    (
        "【初音ミク】千本桜「ボカロ」UP主: 某某某 - 哔哩哔哩",
        ("初音ミク千本桜", "初音ミク千本桜ボカロ"),
    ),
    # ---- YouTube / 其它站点 ----
    ("周杰伦 - 晴天 (Official Music Video) - YouTube", ("周杰伦 - 晴天", "周杰伦 - 晴天")),
    ("Jay Chou 周杰倫《晴天》MV - YouTube Music", ("晴天", "Jay Chou 周杰倫")),
]


def test_web_title_truth_table():
    """20 条真实风格标题的清洗真值表（逐条断言，失败即指出哪一条）。"""
    wrong = []
    for raw, expected in WEB_TITLE_CASES:
        got = music_title.clean_web_title(raw, "")
        if got != expected:
            wrong.append(f"{raw!r} -> {got!r}（期望 {expected!r}）")
    assert not wrong, "清洗结果不符：\n" + "\n".join(wrong)


def test_web_title_truth_table_covers_enough_real_shapes():
    """真值表本身要够宽：至少 15 条，且书名号/无书名号两种形态都在。"""
    assert len(WEB_TITLE_CASES) >= 15
    assert any("《" in raw for raw, _ in WEB_TITLE_CASES)
    assert any("《" not in raw for raw, _ in WEB_TITLE_CASES)


def test_web_title_keeps_supplied_artist_and_uses_it_to_pick_the_song_side():
    """SMTC 给了歌手字段时以它为准，并用它把"歌名 - 歌手"的两段切开。

    浏览器会话通常给空 artist，但网页音乐平台（或用户装了媒体扩展）可能给值；
    有值时顺序就不再是未知——把等于歌手名的那一段摘出去，剩下的就是歌名。
    """
    assert music_title.clean_web_title("晴天 - 周杰伦", "周杰伦") == ("晴天", "周杰伦")
    assert music_title.clean_web_title("周杰伦 - 晴天", "周杰伦") == ("晴天", "周杰伦")
    assert music_title.clean_web_title(
        "周杰伦《晴天》官方MV_哔哩哔哩_bilibili", "周杰伦"
    ) == ("晴天", "周杰伦")


# ------------------------------------------------------- 误伤防护（不可改坏）
#
# 清洗只允许"删片段"，不允许改写或补字；结果为空/过短时**整体退回原始输入**。
# 这是仓库明文反对白名单式误伤的同一条纪律：宁可这条搜不到，也不能把用户的
# 歌名改成一个搜得到但不对的东西。


def test_falls_back_when_cleaning_empties_the_title():
    """整条都是站名后缀 → 清洗后为空 → 退回原文（不得返回空串）。"""
    raw = "_哔哩哔哩_bilibili"
    assert music_title.clean_web_title(raw, "") == (raw, "")


def test_falls_back_when_cleaned_song_name_is_too_short():
    """书名号内只剩一个字 → 视为洗坏了 → 退回原文（含原歌手字段）。"""
    raw = "【4K】《晴》_哔哩哔哩_bilibili"
    assert music_title.clean_web_title(raw, "周杰伦") == (raw, "周杰伦")


def test_empty_input_stays_empty():
    assert music_title.clean_web_title("", "") == ("", "")
    assert music_title.clean_web_title("   ", "周杰伦") == ("   ", "周杰伦")


def test_already_clean_title_is_returned_unchanged():
    """本来就干净的标题不得被"顺手"改动——单段且无被剥内容时歌手留空。

    这里刻意不把整条标题塞进歌手位：标题只有一个词时它不含歌手信息，硬塞进去
    会让 ``_name_matches`` 把歌名当成歌手名（标题就叫《周杰伦》时会给任意
    周杰伦的歌贴上歌词，那才是真正的"改坏"）。
    """
    assert music_title.clean_web_title("晴天", "") == ("晴天", "")
    assert music_title.clean_web_title("晴天", "周杰伦") == ("晴天", "周杰伦")


def test_cleaning_never_invents_characters():
    """清洗只做删片段：输出必须是输入的**子序列**（不补字、不改字）。"""
    for raw, _expected in WEB_TITLE_CASES:
        title, artist = music_title.clean_web_title(raw, "")
        assert _is_subsequence(title, raw), f"{title!r} 不是 {raw!r} 的子序列"
        assert _is_subsequence(artist, raw), f"{artist!r} 不是 {raw!r} 的子序列"


def _is_subsequence(part: str, whole: str) -> bool:
    it = iter(whole)
    return all(ch in it for ch in part)


# ------------------------------------------------- 任务二：foobar2000 窗口标题
#
# foobar2000 的窗口标题是用户可配置的 titleformat 串（默认值随版本/主题变化，
# 社区常见写法有 ``%artist% - %title%``、``[%artist%][ - %title%]`` 等）。
# 所以解析器有两条路：用户填了格式串就照它切；没填就**不猜顺序**。


def test_parse_auto_does_not_guess_the_order():
    """未给格式串：``A - B`` 两种顺序都不可确认 → 整条同时作歌名与歌手。"""
    assert music_title.parse_foobar_title("周杰伦 - 晴天") == ("周杰伦 - 晴天", "周杰伦 - 晴天")
    assert music_title.parse_foobar_title("晴天 - 周杰伦") == ("晴天 - 周杰伦", "晴天 - 周杰伦")


def test_parse_auto_single_field():
    """只有一段时歌名就是它，歌手留空（不硬塞 haystack）。"""
    assert music_title.parse_foobar_title("晴天") == ("晴天", "")


def test_parse_with_explicit_format():
    """给了格式串就按它切；两种常见写法都要能对上。"""
    assert music_title.parse_foobar_title(
        "周杰伦 - 晴天", "%artist% - %title%"
    ) == ("晴天", "周杰伦")
    assert music_title.parse_foobar_title(
        "晴天 - 周杰伦", "%title% - %artist%"
    ) == ("晴天", "周杰伦")
    # 实际窗口标题里的多空格/无空格都要能吃下
    assert music_title.parse_foobar_title(
        "晴天-周杰伦", "%title% - %artist%"
    ) == ("晴天", "周杰伦")


def test_parse_with_conditional_brackets():
    """titleformat 的方括号是"字段非空才输出"的条件段，解析必须容得下。

    ``[%artist% - ]%title%`` 是社区里最常见的一种写法：歌手为空时窗口标题只剩
    歌名，两个分支都要能解析。
    """
    assert music_title.parse_foobar_title(
        "周杰伦 - 晴天", "[%artist% - ]%title%"
    ) == ("晴天", "周杰伦")
    assert music_title.parse_foobar_title("晴天", "[%artist% - ]%title%") == ("晴天", "")


def test_parse_format_falls_back_to_auto_when_it_does_not_match():
    """格式串对不上（用户改了 foobar 显示设置）→ 退回不猜，而不是放弃整条。"""
    assert music_title.parse_foobar_title(
        "晴天 - 周杰伦", "%album% / %title%"
    ) == ("晴天 - 周杰伦", "晴天 - 周杰伦")


def test_parse_rejects_idle_and_empty_titles():
    """没在播歌时 foobar 窗口标题只剩程序名/占位符，必须判为"无曲目"。"""
    for idle in ("foobar2000", "foobar2000 v2.24.1", "FooBar2000", "  ", "- "):
        assert music_title.parse_foobar_title(idle) is None, idle


def test_parse_rejects_format_without_title_field():
    """格式串里没有 %title% 就等于拿不到歌名——退回不猜，而不是返回半条。"""
    assert music_title.parse_foobar_title(
        "周杰伦 - 晴天", "%artist% - %album%"
    ) == ("周杰伦 - 晴天", "周杰伦 - 晴天")


def test_parse_strips_whitespace():
    assert music_title.parse_foobar_title("  晴天  ") == ("晴天", "")


def test_space_only_separator_requires_real_whitespace():
    """格式串里只有一个空格当分隔符时，窗口标题里必须有真空白。

    这是从"每个占位符都翻译成 ``.+?``"换成逐段扫描后顺手修掉的老毛病：旧实现
    把空白放宽成 ``\\s*``，于是 ``%title% %artist%`` 对 ``晴天 周杰伦`` 会在位置 0
    匹配到**空**的空白段，切出 ``(晴, 天 周杰伦)`` 这种碎片。
    """
    assert music_title.parse_foobar_title(
        "晴天 周杰伦", "%title% %artist%"
    ) == ("晴天", "周杰伦")


def test_format_with_trailing_blank_literal_still_parses():
    """格式串首尾的空白字面量不强制要求：读到的窗口标题已经去过首尾空白。

    ``%artist% - %title% ``（末尾多打一个空格）在旧实现里靠"空白放宽成 ``\\s*``"
    侥幸能用；改成"字段之间的空白必须真吃到"之后就得单独兜住首尾这一对，
    否则用户只是多打一个空格，整条格式串就静默退回不猜了。
    """
    assert music_title.parse_foobar_title(
        "周杰伦 - 晴天", "%artist% - %title% "
    ) == ("晴天", "周杰伦")
    assert music_title.parse_foobar_title(
        "周杰伦 - 晴天", " %title%"
    ) == ("周杰伦 - 晴天", "")


def test_format_with_optional_length_field():
    """``%artist% - %title% [%length%]``：带/不带时长两种渲染都要能解析。

    旧实现对这个格式串**恒返回 None**（整条链路当成"没有曲目"），而它是社区里
    常见的窗口标题写法——属于这次重写顺手修掉的行为。
    """
    assert music_title.parse_foobar_title(
        "周杰伦 - 晴天 [04:29]", "%artist% - %title% [%length%]"
    ) == ("晴天", "周杰伦")
    # 条件段不出现时渲染结果末尾会留一个空格，读回来已被去掉
    assert music_title.parse_foobar_title(
        "周杰伦 - 晴天", "%artist% - %title% [%length%]"
    ) == ("晴天", "周杰伦")


# ------------------------------------- 病态格式串必须快速失败（ReDoS 回归，M7）
#
# 背景（2026-10 扩大终审）：旧实现把每个占位符翻译成 `.+?`、条件段翻译成可选组，
# 于是"一段相邻的不受限字段 + 一个在标题里找不到的字面量"构成了组合爆炸：
# 匹配失败前要把 n 个字符切给 k 个字段的所有分法试一遍。本机实测（修复前）：
#
#     "%title%" + "%album%"*8 + "X"（64 字符，**未超配置的 120 字上限**）
#        标题 "a"*30 -> 0.26s / "a"*40 -> 4.13s / "a"*50 -> **34.03s**
#
# 危害不是"慢"：CPython 的正则回溯持 GIL，而这条路径跑在歌词采样线程上
# （music_lyric_controller 的常驻采样线程），被钉住就是歌词与切歌检测整条停摆。
# 同一个进程里其它 Python 线程也一起挨饿。
#
# 断言口径：**同一病态输入重复 3 次的总耗时上限**。上限取得很宽（修复后实测
# 约 1ms，即便 CI 慢 100 倍也远低于它）——它只用来钉住"指数级/组合爆炸"，
# 不是基准测试。
_PATHOLOGICAL_MATCH_REPEATS = 3
_PATHOLOGICAL_MATCH_BUDGET_SECONDS = 0.5
# 有界模糊的总预算（800 组，修复后实测约 40ms；CI 慢 20 倍也过得去）。
_FUZZ_BUDGET_SECONDS = 1.0
_PATHOLOGICAL_FORMATS = (
    # 终审给的原始形态（64 字符）
    ("%title%" + "%album%" * 8 + "X", "a" * 35),
    # 换成另一个我们关心的字段，形态相同
    ("%title%" + "%artist%" * 8 + "X", "a" * 35),
    # 字段更多（106 字符，仍在 120 上限内）：修复前 7.6s
    ("%title%" + "%album%" * 14 + "X", "a" * 30),
    # 关心字段夹在中间（85 字符）：修复前 2.3s
    ("%album%" * 3 + "%title%" + "%album%" * 8 + "X", "a" * 30),
)


def test_pathological_format_strings_fail_fast():
    """病态格式串必须在毫秒级失败，而不是组合爆炸式回溯。"""
    for fmt, title in _PATHOLOGICAL_FORMATS:
        started = time.perf_counter()
        for _ in range(_PATHOLOGICAL_MATCH_REPEATS):
            music_title.parse_foobar_title(title, fmt)
        elapsed = time.perf_counter() - started
        assert elapsed < _PATHOLOGICAL_MATCH_BUDGET_SECONDS, (
            f"格式串 {fmt!r}（{len(fmt)} 字符）匹配 {len(title)} 字标题，"
            f"{_PATHOLOGICAL_MATCH_REPEATS} 次用了 {elapsed:.2f}s"
            f"（上限 {_PATHOLOGICAL_MATCH_BUDGET_SECONDS}s）——回溯又回来了"
        )


def test_pathological_format_still_returns_a_usable_answer():
    """快速失败不等于放弃：切不出来就退回"不猜"，标签照常显示。"""
    fmt = "%title%" + "%album%" * 8 + "X"
    title = "a" * 35
    # 没有书名号、也没有分隔符 → 单段 → 歌名即整条、歌手留空（见 _haystack_artist）
    assert music_title.parse_foobar_title(title, fmt) == (title, "")


def test_over_complex_conditional_format_falls_back_to_auto():
    """条件段太多时**不展开**（2^n 会炸），直接退回不猜。

    10 个条件段 = 1024 条候选；上限是 16 条（4 个条件段），超过就判定"这不是
    我们能可靠解析的格式串"。结果与不猜口径一致：整条当歌名、单段歌手留空。
    """
    fmt = "[%artist%]" * 10 + "%title%"
    title = "a" * 35 + "周杰伦"
    started = time.perf_counter()
    got = music_title.parse_foobar_title(title, fmt)
    elapsed = time.perf_counter() - started
    assert got == (title, "")
    assert elapsed < _PATHOLOGICAL_MATCH_BUDGET_SECONDS, f"{elapsed:.2f}s"


def test_random_format_fuzz_stays_bounded():
    """有界模糊：随机格式串 × 随机标题，任何组合都不许慢下来。

    单点回归只能钉住已知形态；这一条用一个**固定种子**的随机语料把"格式串空间
    里没有别的爆炸角落"也钉住（固定种子 = 可复现，不是掷骰子）。语料里刻意塞
    了方括号、引号、`%`、空白与中英混排。
    """
    rng = random.Random(20261007)
    pieces = (
        "%title%", "%artist%", "%album%", "%tracknumber%", "%length%",
        " - ", " / ", " ", "  ", "[", "]", "'", ".", "%artist% - ",
        "%title% ", " (", ")", "%title%", "%artist%",
    )
    alphabet = "ab -/[]()%AB周杰伦"
    started = time.perf_counter()
    for _ in range(800):
        fmt = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 9)))[:120]
        title = "".join(
            rng.choice(alphabet) for _ in range(rng.randint(1, 90))
        )
        music_title.parse_foobar_title(title, fmt)
    elapsed = time.perf_counter() - started
    assert elapsed < _FUZZ_BUDGET_SECONDS, f"800 组随机输入用了 {elapsed:.2f}s"


def test_titleformat_multiword_title_not_truncated():
    """%artist% - %title% [%length%] 配多词英文歌名：歌名必须完整（歌词族终审应修 2）。

    修复前最左字面量匹配把 "Hotel California 06:30" 切成 title="Hotel"、
    length="California 06:30"——截错的名字直接进搜索与缓存。
    """
    from pet.music.title import parse_foobar_title

    assert parse_foobar_title(
        "Eagles - Hotel California 06:30", "%artist% - %title% [%length%]"
    ) == ("Hotel California", "Eagles")
    # 单字面量格式（%title% - %artist%）同样取最右分隔：多词歌名不切错
    assert parse_foobar_title(
        "Hotel California - Eagles", "%title% - %artist%"
    ) == ("Hotel California", "Eagles")
    # 中文回归：原有行为不变
    assert parse_foobar_title("周杰伦 - 晴天", "%artist% - %title%") == ("晴天", "周杰伦")
