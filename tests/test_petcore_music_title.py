# -*- coding: utf-8 -*-
"""标题 → 取词查询词 的纯函数测试（media.music 的 ``host/title_clean.py``）。

这是主分支 ``tests/test_music_title.py`` 的移植：同一张真值表、同一套断言，守的是
mod 里那份**自持副本**（``pet/`` 是待删的兼容层，mod 不许 import 它）。两边口径
必须一致——任何一边的红灯都意味着"洗标题"与"搜歌词"之间又多了一种对不上的方言。

覆盖两条真实用户需求：

- **B 站 / 浏览器媒体会话的网页标题**：SMTC 给的 title 就是网页标题
  （``【4K】周杰伦《晴天》官方MV_哔哩哔哩_bilibili``），直接拿去搜歌词命中率极低。
- **自绘窗口标题的播放器**：AIMP / Winamp / foobar2000 默认不上报结构化曲目，只能
  从窗口标题拿曲目信息，而那是用户可配置的 titleformat 串。

真值表里的输入都是**真实风格**的标题（B 站/YouTube 的实际形态），不是编出来的
形状。误伤防护单独成组：清洗失败必须**整体退回原始输入**，宁可搜不到不可改坏。

全部离线、不依赖 Windows API：被测模块只 import ``re`` 与 ``functools``。
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import random
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

MOD_DIR = Path(__file__).resolve().parents[1] / "examples" / "petcore-mods" / "media.music"
HOST_PKG = "petcore_music_title_host"


def _host_package():
    if HOST_PKG in sys.modules:
        return sys.modules[HOST_PKG]
    spec = importlib.util.spec_from_file_location(
        HOST_PKG,
        MOD_DIR / "host" / "__init__.py",
        submodule_search_locations=[str(MOD_DIR / "host")],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[HOST_PKG] = module
    spec.loader.exec_module(module)
    return module


_host_package()
title_clean = importlib.import_module(f"{HOST_PKG}.title_clean")


# ------------------------------------------------- 任务一：网页/浏览器标题清洗
#
# 期望值不是"跑出来的"而是按规则推出来的：
# - 书名号内是歌名（优先）；
# - 书名号外的最近一段（书名号前取最后一段、之后取第一段）是歌手；
# - 没有书名号时**不猜顺序**：整条清洗结果同时作歌名与歌手
#   （见 title_clean._haystack_artist 的说明）。

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
        got = title_clean.clean_web_title(raw, "")
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
    assert title_clean.clean_web_title("晴天 - 周杰伦", "周杰伦") == ("晴天", "周杰伦")
    assert title_clean.clean_web_title("周杰伦 - 晴天", "周杰伦") == ("晴天", "周杰伦")
    assert title_clean.clean_web_title(
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
    assert title_clean.clean_web_title(raw, "") == (raw, "")


def test_falls_back_when_cleaned_song_name_is_too_short():
    """书名号内只剩一个字 → 视为洗坏了 → 退回原文（含原歌手字段）。"""
    raw = "【4K】《晴》_哔哩哔哩_bilibili"
    assert title_clean.clean_web_title(raw, "周杰伦") == (raw, "周杰伦")


def test_empty_input_stays_empty():
    assert title_clean.clean_web_title("", "") == ("", "")
    assert title_clean.clean_web_title("   ", "周杰伦") == ("   ", "周杰伦")


def test_already_clean_title_is_returned_unchanged():
    """本来就干净的标题不得被"顺手"改动——单段且无被剥内容时歌手留空。

    这里刻意不把整条标题塞进歌手位：标题只有一个词时它不含歌手信息，硬塞进去
    会让下游的歌手校验把歌名当成歌手名（标题就叫《周杰伦》时会给任意周杰伦的歌
    贴上歌词，那才是真正的"改坏"）。
    """
    assert title_clean.clean_web_title("晴天", "") == ("晴天", "")
    assert title_clean.clean_web_title("晴天", "周杰伦") == ("晴天", "周杰伦")


def test_cleaning_never_invents_characters():
    """清洗只做删片段：输出必须是输入的**子序列**（不补字、不改字）。"""
    for raw, _expected in WEB_TITLE_CASES:
        title, artist = title_clean.clean_web_title(raw, "")
        assert _is_subsequence(title, raw), f"{title!r} 不是 {raw!r} 的子序列"
        assert _is_subsequence(artist, raw), f"{artist!r} 不是 {raw!r} 的子序列"


def _is_subsequence(part: str, whole: str) -> bool:
    it = iter(whole)
    return all(ch in it for ch in part)


# ------------------------------------------- 任务二：窗口标题（titleformat 解析）
#
# 播放器的窗口标题是用户可配置的 titleformat 串（默认值随版本/主题变化，社区常见
# 写法有 ``%artist% - %title%``、``[%artist%][ - %title%]`` 等）。所以解析器有两条
# 路：用户填了格式串就照它切；没填就**不猜顺序**。


def test_parse_auto_does_not_guess_the_order():
    """未给格式串：``A - B`` 两种顺序都不可确认 → 整条同时作歌名与歌手。"""
    assert title_clean.parse_foobar_title("周杰伦 - 晴天") == ("周杰伦 - 晴天", "周杰伦 - 晴天")
    assert title_clean.parse_foobar_title("晴天 - 周杰伦") == ("晴天 - 周杰伦", "晴天 - 周杰伦")


def test_parse_auto_single_field():
    """只有一段时歌名就是它，歌手留空（不硬塞 haystack）。"""
    assert title_clean.parse_foobar_title("晴天") == ("晴天", "")


def test_parse_with_explicit_format():
    """给了格式串就按它切；两种常见写法都要能对上。"""
    assert title_clean.parse_foobar_title(
        "周杰伦 - 晴天", "%artist% - %title%"
    ) == ("晴天", "周杰伦")
    assert title_clean.parse_foobar_title(
        "晴天 - 周杰伦", "%title% - %artist%"
    ) == ("晴天", "周杰伦")
    # 实际窗口标题里的多空格/无空格都要能吃下
    assert title_clean.parse_foobar_title(
        "晴天-周杰伦", "%title% - %artist%"
    ) == ("晴天", "周杰伦")


def test_parse_with_conditional_brackets():
    """titleformat 的方括号是"字段非空才输出"的条件段，解析必须容得下。

    ``[%artist% - ]%title%`` 是社区里最常见的一种写法：歌手为空时窗口标题只剩
    歌名，两个分支都要能解析。
    """
    assert title_clean.parse_foobar_title(
        "周杰伦 - 晴天", "[%artist% - ]%title%"
    ) == ("晴天", "周杰伦")
    assert title_clean.parse_foobar_title("晴天", "[%artist% - ]%title%") == ("晴天", "")


def test_parse_format_falls_back_to_auto_when_it_does_not_match():
    """格式串对不上（用户改了播放器显示设置）→ 退回不猜，而不是放弃整条。"""
    assert title_clean.parse_foobar_title(
        "晴天 - 周杰伦", "%album% / %title%"
    ) == ("晴天 - 周杰伦", "晴天 - 周杰伦")


def test_parse_rejects_idle_and_empty_titles():
    """没在播歌时窗口标题只剩程序名/占位符，必须判为"无曲目"。"""
    for idle in ("foobar2000", "foobar2000 v2.24.1", "FooBar2000", "  ", "- "):
        assert title_clean.parse_foobar_title(idle) is None, idle


def test_parse_rejects_format_without_title_field():
    """格式串里没有 %title% 就等于拿不到歌名——退回不猜，而不是返回半条。"""
    assert title_clean.parse_foobar_title(
        "周杰伦 - 晴天", "%artist% - %album%"
    ) == ("周杰伦 - 晴天", "周杰伦 - 晴天")


def test_parse_strips_whitespace():
    assert title_clean.parse_foobar_title("  晴天  ") == ("晴天", "")


def test_space_only_separator_requires_real_whitespace():
    """格式串里只有一个空格当分隔符时，窗口标题里必须有真空白。

    这是从"每个占位符都翻译成 ``.+?``"换成逐段扫描后顺手修掉的老毛病：旧实现
    把空白放宽成 ``\\s*``，于是 ``%title% %artist%`` 对 ``晴天 周杰伦`` 会在位置 0
    匹配到**空**的空白段，切出 ``(晴, 天 周杰伦)`` 这种碎片。
    """
    assert title_clean.parse_foobar_title(
        "晴天 周杰伦", "%title% %artist%"
    ) == ("晴天", "周杰伦")


def test_format_with_trailing_blank_literal_still_parses():
    """格式串首尾的空白字面量不强制要求：读到的窗口标题已经去过首尾空白。

    ``%artist% - %title% ``（末尾多打一个空格）在旧实现里靠"空白放宽成 ``\\s*``"
    侥幸能用；改成"字段之间的空白必须真吃到"之后就得单独兜住首尾这一对，
    否则用户只是多打一个空格，整条格式串就静默退回不猜了。
    """
    assert title_clean.parse_foobar_title(
        "周杰伦 - 晴天", "%artist% - %title% "
    ) == ("晴天", "周杰伦")
    assert title_clean.parse_foobar_title(
        "周杰伦 - 晴天", " %title%"
    ) == ("周杰伦 - 晴天", "")


def test_format_with_optional_length_field():
    """``%artist% - %title% [%length%]``：带/不带时长两种渲染都要能解析。

    旧实现对这个格式串**恒返回 None**（整条链路当成"没有曲目"），而它是社区里
    常见的窗口标题写法——属于这次重写顺手修掉的行为。
    """
    assert title_clean.parse_foobar_title(
        "周杰伦 - 晴天 [04:29]", "%artist% - %title% [%length%]"
    ) == ("晴天", "周杰伦")
    # 条件段不出现时渲染结果末尾会留一个空格，读回来已被去掉
    assert title_clean.parse_foobar_title(
        "周杰伦 - 晴天", "%artist% - %title% [%length%]"
    ) == ("晴天", "周杰伦")


# ------------------------------------- 病态格式串必须快速失败（ReDoS 回归，M7）
#
# 背景（主分支 2026-10 扩大终审）：旧实现把每个占位符翻译成 `.+?`、条件段翻译成
# 可选组，于是"一段相邻的不受限字段 + 一个在标题里找不到的字面量"构成了组合爆炸：
# 匹配失败前要把 n 个字符切给 k 个字段的所有分法试一遍。主分支本机实测（修复前）：
#
#     "%title%" + "%album%"*8 + "X"（64 字符，**未超配置的 120 字上限**）
#        标题 "a"*30 -> 0.26s / "a"*40 -> 4.13s / "a"*50 -> **34.03s**
#
# 危害不是"慢"：CPython 的正则回溯持 GIL，而这条路径跑在歌词采样线程上
# （常驻采样线程），被钉住就是歌词与切歌检测整条停摆。
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
            title_clean.parse_foobar_title(title, fmt)
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
    assert title_clean.parse_foobar_title(title, fmt) == (title, "")


def test_over_complex_conditional_format_falls_back_to_auto():
    """条件段太多时**不展开**（2^n 会炸），直接退回不猜。

    10 个条件段 = 1024 条候选；上限是 16 条（4 个条件段），超过就判定"这不是
    我们能可靠解析的格式串"。结果与不猜口径一致：整条当歌名、单段歌手留空。
    """
    fmt = "[%artist%]" * 10 + "%title%"
    title = "a" * 35 + "周杰伦"
    started = time.perf_counter()
    got = title_clean.parse_foobar_title(title, fmt)
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
        title_clean.parse_foobar_title(title, fmt)
    elapsed = time.perf_counter() - started
    assert elapsed < _FUZZ_BUDGET_SECONDS, f"800 组随机输入用了 {elapsed:.2f}s"


def test_titleformat_multiword_title_not_truncated():
    """%artist% - %title% [%length%] 配多词英文歌名：歌名必须完整（歌词族终审应修 2）。

    修复前最左字面量匹配把 "Hotel California 06:30" 切成 title="Hotel"、
    length="California 06:30"——截错的名字直接进搜索与缓存。
    """
    assert title_clean.parse_foobar_title(
        "Eagles - Hotel California 06:30", "%artist% - %title% [%length%]"
    ) == ("Hotel California", "Eagles")
    # 单字面量格式（%title% - %artist%）同样取最右分隔：多词歌名不切错
    assert title_clean.parse_foobar_title(
        "Hotel California - Eagles", "%title% - %artist%"
    ) == ("Hotel California", "Eagles")
    # 中文回归：原有行为不变
    assert title_clean.parse_foobar_title("周杰伦 - 晴天", "%artist% - %title%") == ("晴天", "周杰伦")


# ------------------------------- mod 侧新增：按播放器 exe 剥掉窗口标题尾部标识
#
# 主分支只有 foobar2000 一个窗口标题源，所以没有这一层；mod 还要照顾 AIMP /
# Winamp——它们的默认窗口标题把程序名直接拼在末尾（``Artist - Title - AIMP``）。
# 规则只有一条：认识这个 exe 才剥，不认识就不剥；剥完解析不出就返回 None（不猜）。


def test_window_title_strips_aimp_suffix():
    """AIMP 的默认标题尾巴 `` - AIMP`` 必须剥掉，不能进歌名/歌手。

    不带格式串时也必须是**剥过之后**的那条整串同时作歌名与歌手——程序名一旦留着，
    下游就会拿 "AIMP" 当歌手去搜。
    """
    assert title_clean.parse_window_title(
        "周杰伦 - 晴天 - AIMP", "aimp.exe", "%artist% - %title%"
    ) == ("晴天", "周杰伦")
    assert title_clean.parse_window_title("周杰伦 - 晴天 - AIMP", "aimp.exe") == (
        "周杰伦 - 晴天",
        "周杰伦 - 晴天",
    )
    # 窗口标题里的大小写由播放器决定，比较不区分大小写
    assert title_clean.parse_window_title(
        "周杰伦 - 晴天 - Aimp", "aimp.exe", "%artist% - %title%"
    ) == ("晴天", "周杰伦")


def test_window_title_strips_winamp_suffix():
    """Winamp 的默认标题尾巴 `` - Winamp`` 同样剥掉。"""
    assert title_clean.parse_window_title(
        "周杰伦 - 晴天 - Winamp", "winamp.exe", "%artist% - %title%"
    ) == ("晴天", "周杰伦")
    assert title_clean.parse_window_title("周杰伦 - 晴天 - Winamp", "winamp.exe") == (
        "周杰伦 - 晴天",
        "周杰伦 - 晴天",
    )
    # 表外的 exe 不剥（不猜）：整条原样进入解析
    assert title_clean.parse_window_title("周杰伦 - 晴天 - Winamp", "someplayer.exe") == (
        "周杰伦 - 晴天 - Winamp",
        "周杰伦 - 晴天 - Winamp",
    )


def test_window_title_honours_configured_format():
    """用户配了 titleformat 就按它切；foobar2000 不做任何尾部剥离。"""
    assert title_clean.parse_window_title(
        "周杰伦 - 晴天", "foobar2000.exe", "%artist% - %title%"
    ) == ("晴天", "周杰伦")
    assert title_clean.parse_window_title(
        "晴天 - 周杰伦", "foobar2000.exe", "%title% - %artist%"
    ) == ("晴天", "周杰伦")
    # 条件段（歌手为空时只剩歌名）也要能解析
    assert title_clean.parse_window_title(
        "晴天", "foobar2000.exe", "[%artist% - ]%title%"
    ) == ("晴天", "")
    # 空格式串 = 自动：不猜顺序
    assert title_clean.parse_window_title(
        "周杰伦 - 晴天", "foobar2000.exe", ""
    ) == ("周杰伦 - 晴天", "周杰伦 - 晴天")


def test_window_title_returns_none_when_nothing_to_parse():
    """解析不出可用歌名一律 ``None``——包括"剥离后为空"，不猜。"""
    # 没在播歌：窗口标题只剩程序名
    assert title_clean.parse_window_title("AIMP", "aimp.exe") is None
    assert title_clean.parse_window_title("Winamp", "winamp.exe") is None
    assert title_clean.parse_window_title("foobar2000", "foobar2000.exe") is None
    assert title_clean.parse_window_title("foobar2000 v2.24.1", "foobar2000.exe") is None
    # 剥掉尾巴后什么都不剩 / 只剩分隔符
    assert title_clean.parse_window_title(" - AIMP", "aimp.exe") is None
    assert title_clean.parse_window_title("", "aimp.exe") is None
    assert title_clean.parse_window_title("   ", "winamp.exe") is None
    # 歌名不足两字（清洗后不可用）也不猜
    assert title_clean.parse_window_title("《晴》 - AIMP", "aimp.exe") is None


def test_window_title_strips_netease_cloud_music_suffix():
    """网易云默认标题 ``歌名 - 歌手 - 网易云音乐``：尾部标识剥掉，两段各归各位。

    网易云的窗口标题布局是固定的（歌名在前、歌手在后），不是通用猜测；所以这条
    只对 ``cloudmusic.exe`` 生效——foobar2000 / 浏览器仍然"不猜顺序"。
    """
    assert title_clean.parse_window_title(
        "晴天 - 周杰伦 - 网易云音乐", "cloudmusic.exe"
    ) == ("晴天", "周杰伦")
    # 窗口标题里的程序名写法由播放器决定，比较不区分大小写
    assert title_clean.parse_window_title(
        "晴天 - 周杰伦 - 网易云音乐", "CloudMusic.exe"
    ) == ("晴天", "周杰伦")


def test_window_title_rejects_idle_player_names():
    """标题只剩播放器名 = 没在播歌，不许把它当成歌名。

    只对**已登记的播放器 exe** 生效：浏览器/未知播放器的标题照旧整条进入解析
    （否则会把"标题就叫网易云音乐"的网页误判为空闲）。
    """
    for title, exe in (
        ("网易云音乐", "cloudmusic.exe"),
        ("网易云音乐", "CloudMusic.exe"),
        ("网易云 音乐", "cloudmusic.exe"),
        ("QQ音乐", "qqmusic.exe"),
        ("QQ 音乐", "QQMusic.exe"),
        ("酷狗音乐", "kugou.exe"),
        ("汽水音乐", "sodamusic.exe"),
    ):
        assert title_clean.parse_window_title(title, exe) is None, (title, exe)
    assert title_clean.parse_window_title("网易云音乐", "chrome.exe") == ("网易云音乐", "")


# ---------------------------------------------------------------- 与主分支的等价性
#
# `title_clean.py` 是 `pet/music_title.py` 的移植件。两份实现同时留在仓库里，
# 靠"一次性差分脚本"守不住——今天开始由这条用例常驻把关：任何一边改了清洗口径
# 而另一边没跟，这里立刻红。

_EQUIVALENCE_TITLES = (
    ("【4K】周杰伦《晴天》官方MV_哔哩哔哩_bilibili", ""),
    ("周杰伦 - 晴天", ""),
    ("晴天 - 周杰伦", ""),
    ("【Hi-Res无损】晴天-周杰伦-高清MV", ""),
    ("[Official MV] 晴天 / 周杰伦", ""),
    ("「官方」晴天_哔哩哔哩_bilibili", ""),
    ("UP主: 某某 晴天", ""),
    ("Taylor Swift - Blank Space (Official Video)", "Taylor Swift"),
    ("Blank Space", "Taylor Swift"),
    ("《晴天》", ""),
    ("【某某UP】周杰伦《晴天》【某某UP】", ""),
    ("纯音乐 - 钢琴曲", ""),
    ("A - B - C - D", ""),
    ("", ""),
    ("x", ""),
)

_EQUIVALENCE_WINDOWS = (
    ("周杰伦 - 晴天", ""),
    ("周杰伦 - 晴天", "%artist% - %title%"),
    ("晴天 - 周杰伦", "%title% - %artist%"),
    ("Eagles - Hotel California 06:30", "%artist% - %title% [%length%]"),
    ("[周杰伦] 晴天", "[%artist% - ]%title%"),
    ("foobar2000 v2.1", ""),
    ("周杰伦 - 晴天 [03:59]", "%artist% - %title% - %album%"),
)


def test_port_stays_equivalent_to_the_main_branch_implementation():
    """与 `pet/music_title.py` 逐条等价（清洗 + titleformat 解析各 15/7 组真值）。"""
    from pet import music_title as legacy

    for title, artist in _EQUIVALENCE_TITLES:
        assert title_clean.clean_web_title(title, artist) == legacy.clean_web_title(
            title, artist
        ), (title, artist)
    for window, fmt in _EQUIVALENCE_WINDOWS:
        assert title_clean.parse_foobar_title(window, fmt) == legacy.parse_foobar_title(
            window, fmt
        ), (window, fmt)
