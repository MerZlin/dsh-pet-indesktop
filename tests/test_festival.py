# -*- coding: utf-8 -*-
"""节日提醒：纯逻辑层测试（零 Qt 依赖，可离线快跑）。

覆盖：节日表冻结快照、农历/节气/浮动节日换算、今日命中与分类开关、
配置清洗、提醒时间计算、文案池与挑选、槽位幂等、启动补提醒。

日历基准日全部是**外部已知事实**（春节/复活节/母亲节等公开历法日期），
不是从实现里回抄的——否则测试只能证明"代码没变"，不能证明"代码是对的"。
"""

from __future__ import annotations

import datetime as dt

import pytest

from pet import festival as F
from pet import festival_calendar as C
from pet import festival_data as D
from pet.festival_quotes_cn import QUOTES_CN
from pet.festival_quotes_west import QUOTES_WEST
from pet.festival_quotes_west_game import QUOTES_WEST_GAME
from pet.festival_quotes_west_movie import QUOTES_WEST_MOVIE
from pet.festival_quotes_west_song import QUOTES_WEST_SONG

# 西方节日的四段内置库（公有领域 -> 电影 -> 游戏 -> 歌曲），顺序即拼装顺序。
WEST_LIBRARIES = (QUOTES_WEST, QUOTES_WEST_MOVIE, QUOTES_WEST_GAME, QUOTES_WEST_SONG)

# 一个**已核实**当天无任何节日/节气的公历日（2026-01-02 小寒尚未到、
# 农历仍在腊月且非腊八/除夕）。原先误用 2026-03-03，那天其实是元宵节。
PLAIN_DAY = dt.date(2026, 1, 2)


def cfg(**overrides) -> dict:
    """构造一份启用的配置，便于逐项覆盖。"""
    base = {
        "enabled": True,
        "cn": True,
        "solar_terms": True,
        "west": True,
        "mode": F.MODE_TIMES,
        "count": 2,
        "times": frozenset({"09:00"}),
        "show_quote": True,
        "custom_quotes_cn": (),
        "custom_quotes_west": (),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------- 节日表
def test_festival_ids_are_frozen():
    """节日 id 集合冻结：新增/删除节日必须显式改本快照，属于需要评审的决策。

    本表是「敏感议题规避白名单」的落地形态——不靠列举违禁词，而是要求
    任何收录变更都必须走一次显式改动，无法悄悄混入。
    """
    assert {f.id for f in D.FESTIVALS} == {
        # 中国节日
        "yuandan", "chunjie", "yuanxiao", "longtaitou", "laodongjie", "duanwu",
        "ertongjie", "qixi", "zhongyuan", "zhongqiu", "chongyang", "guoqingjie",
        "labajie", "chuxi",
        # 24 节气（term_06 由清明节占用）
        "term_00", "term_01", "term_02", "term_03", "term_04", "term_05",
        "qingming",
        "term_07", "term_08", "term_09", "term_10", "term_11", "term_12",
        "term_13", "term_14", "term_15", "term_16", "term_17", "term_18",
        "term_19", "term_20", "term_21", "term_22", "term_23",
        # 西方节日
        "valentine", "april_fools", "easter", "mothers_day", "fathers_day",
        "halloween", "christmas_eve", "christmas",
    }
    assert len(D.FESTIVALS) == 46


def test_solar_terms_cover_all_24():
    assert len(C.SOLAR_TERMS) == 24
    assert set(C.SOLAR_TERMS) == {
        "小寒", "大寒", "立春", "雨水", "惊蛰", "春分", "清明", "谷雨",
        "立夏", "小满", "芒种", "夏至", "小暑", "大暑", "立秋", "处暑",
        "白露", "秋分", "寒露", "霜降", "立冬", "小雪", "大雪", "冬至",
    }


def test_qingming_belongs_to_both_category_switches():
    """清明节既是节气又是传统节日：任一开关开启都应命中。"""
    qingming = D.FESTIVALS_BY_ID["qingming"]
    assert set(qingming.categories) == {D.CATEGORY_CN, D.CATEGORY_SOLAR_TERM}
    assert F.is_festival_enabled(qingming, cfg(cn=True, solar_terms=False))
    assert F.is_festival_enabled(qingming, cfg(cn=False, solar_terms=True))
    assert not F.is_festival_enabled(qingming, cfg(cn=False, solar_terms=False))


# ---------------------------------------------------------------- 历法换算
@pytest.mark.parametrize(
    ("year", "expected"),
    [
        (2024, "2024-02-10"),
        (2025, "2025-01-29"),
        (2026, "2026-02-17"),
        (2027, "2027-02-06"),
        (2028, "2028-01-26"),
    ],
)
def test_spring_festival_dates(year, expected):
    assert C.spring_festival(year).isoformat() == expected


@pytest.mark.parametrize(
    ("year", "expected"),
    [(2026, "2026-02-16"), (2027, "2027-02-05")],
)
def test_new_year_eve_is_day_before_spring_festival(year, expected):
    """除夕必须是春节前一天；用「腊月三十」直构会在小月年份抛错。"""
    assert C.new_year_eve(year).isoformat() == expected


@pytest.mark.parametrize(
    ("year", "expected"),
    [
        (2024, "2024-03-31"),
        (2025, "2025-04-20"),
        (2026, "2026-04-05"),
        (2027, "2027-03-28"),
    ],
)
def test_easter_dates(year, expected):
    assert C.easter(year).isoformat() == expected


def test_nth_weekday_dates_2026():
    assert C.nth_weekday(2026, 5, 6, 2).isoformat() == "2026-05-10"   # 母亲节
    assert C.nth_weekday(2026, 6, 6, 3).isoformat() == "2026-06-21"   # 父亲节
    assert C.nth_weekday(2026, 11, 3, 4).isoformat() == "2026-11-26"  # 通用工具：11 月第 4 个周四


def test_solar_terms_of_year_2026():
    terms = C.solar_terms_of_year(2026)
    assert len(terms) == 24
    assert terms["立春"].isoformat() == "2026-02-04"
    assert terms["春分"].isoformat() == "2026-03-20"
    assert terms["清明"].isoformat() == "2026-04-05"
    assert terms["冬至"].isoformat() == "2026-12-22"


def test_leap_month_is_flagged_and_excluded_from_festivals():
    """闰月必须被识别，且闰月里的同号日不得被当成农历节日。

    2025 年有闰六月；闰六月初一（2025-07-25）绝不能被判成"六月初一"类
    节日。六月初一本身不在表内，故这里用一个一定存在的对照：闰月标记本身。
    """
    leap = C.solar_to_lunar(dt.date(2025, 7, 25))
    assert leap.is_leap and leap.month == 6 and leap.day == 1
    # 闰月里的日期不产生任何农历节日命中
    assert F.festivals_on(dt.date(2025, 7, 25), cfg()) == ()


# ---------------------------------------------------------------- 今日命中
def test_festivals_on_known_days():
    assert [f.name for f in F.festivals_on(dt.date(2026, 2, 17), cfg())] == ["春节"]
    assert [f.name for f in F.festivals_on(dt.date(2026, 2, 16), cfg())] == ["除夕"]
    assert [f.name for f in F.festivals_on(dt.date(2026, 2, 4), cfg())] == ["立春"]
    assert [f.name for f in F.festivals_on(dt.date(2026, 10, 1), cfg())] == ["国庆节"]
    assert [f.name for f in F.festivals_on(dt.date(2026, 5, 10), cfg())] == ["母亲节"]
    assert [f.name for f in F.festivals_on(dt.date(2026, 12, 25), cfg())] == ["圣诞节"]


def test_festivals_on_orders_cn_before_west():
    """2026-04-05 同时是清明节与复活节：中国节日/节气排在西方节日之前。"""
    names = [f.name for f in F.festivals_on(dt.date(2026, 4, 5), cfg())]
    assert names == ["清明节", "复活节"]


def test_festivals_on_respects_category_switches():
    """三个分类开关各自独立生效；清明节因同属两类，需两个开关都关才消失。"""
    # 纯中国节日
    assert F.festivals_on(dt.date(2026, 2, 17), cfg(cn=False)) == ()
    # 纯节气
    assert F.festivals_on(dt.date(2026, 2, 4), cfg(solar_terms=False)) == ()
    # 纯西方节日
    assert F.festivals_on(dt.date(2026, 12, 25), cfg(west=False)) == ()

    day = dt.date(2026, 4, 5)
    assert [f.name for f in F.festivals_on(day, cfg(cn=False))] == ["清明节", "复活节"]
    assert [f.name for f in F.festivals_on(day, cfg(solar_terms=False))] == ["清明节", "复活节"]
    assert [f.name for f in F.festivals_on(day, cfg(cn=False, solar_terms=False))] == ["复活节"]


def test_festivals_on_plain_day_is_empty():
    assert F.festivals_on(PLAIN_DAY, cfg()) == ()


# ---------------------------------------------------------------- 配置清洗
def test_normalize_defaults_when_master_switch_absent():
    """总开关默认关闭：配置里没有该键时不得变成开启。"""
    assert F.normalize_festival_config({})["enabled"] is False


def test_normalize_accepts_string_booleans():
    normalized = F.normalize_festival_config(
        {"festival_reminder_enabled": "开", "festival_reminder_west": "0"}
    )
    assert normalized["enabled"] is True
    assert normalized["west"] is False


def test_normalize_clamps_count_and_falls_back_on_garbage():
    assert F.normalize_festival_config({"festival_reminder_count": 99})["count"] == F.MAX_COUNT
    assert F.normalize_festival_config({"festival_reminder_count": -5})["count"] == F.MIN_COUNT
    assert F.normalize_festival_config({"festival_reminder_count": "abc"})["count"] == F.DEFAULT_COUNT


def test_normalize_mode_falls_back_on_unknown_value():
    assert F.normalize_festival_config({"festival_reminder_mode": "nope"})["mode"] == F.DEFAULT_MODE
    assert F.normalize_festival_config({"festival_reminder_mode": "custom"})["mode"] == "custom"


def test_normalize_never_raises_on_dirty_config():
    """config.json 被手改坏时不得抛异常——本方法跑在启动路径上。"""
    dirty = {
        "festival_reminder_enabled": object(),
        "festival_reminder_count": [1, 2],
        "festival_reminder_times": 12345,
        "festival_custom_quotes_cn": {"a": 1},
    }
    normalized = F.normalize_festival_config(dirty)
    assert normalized["enabled"] is False
    assert normalized["count"] == F.DEFAULT_COUNT


def test_custom_times_parsing_normalizes_and_drops_garbage():
    normalized = F.normalize_festival_config(
        {"festival_reminder_times": "9:00, 25:00，12:30; 坏值 09:00"}
    )
    assert normalized["times"] == frozenset({"09:00", "12:30"})


# ---------------------------------------------------------------- 提醒时间
def test_times_mode_spreads_count_over_window():
    assert F.reminder_times(cfg(count=1)) == ("09:00",)
    assert F.reminder_times(cfg(count=2)) == ("09:00", "21:00")
    assert F.reminder_times(cfg(count=3)) == ("09:00", "15:00", "21:00")
    assert F.reminder_times(cfg(count=4)) == ("09:00", "13:00", "17:00", "21:00")


def test_custom_mode_uses_user_times_sorted():
    got = F.reminder_times(cfg(mode="custom", times=frozenset({"20:00", "07:30"})))
    assert got == ("07:30", "20:00")


def test_custom_mode_with_empty_times_falls_back():
    """选了自定义却没填：回落默认时间，而不是静默永不提醒。"""
    assert F.reminder_times(cfg(mode="custom", times=frozenset())) == (F.DEFAULT_TIMES,)


# ---------------------------------------------------------------- 文案
def test_quote_pool_uses_cn_for_chinese_and_west_for_western():
    cn_pool = F.quote_pool(D.FESTIVALS_BY_ID["chunjie"], cfg())
    west_pool = F.quote_pool(D.FESTIVALS_BY_ID["christmas"], cfg())
    assert cn_pool == QUOTES_CN["chunjie"]
    # 西文库是「公有领域 -> 电影 -> 游戏 -> 歌曲」四段按序拼接；后三段都是
    # 有意不覆盖全部节日，故用 .get 取值（缺键即不追加）。
    expected = (
        tuple(QUOTES_WEST["christmas"])
        + tuple(QUOTES_WEST_MOVIE.get("christmas", ()))
        + tuple(QUOTES_WEST_GAME.get("christmas", ()))
        + tuple(QUOTES_WEST_SONG.get("christmas", ()))
    )
    assert west_pool == expected


def test_pop_culture_libraries_are_present_and_non_empty():
    """受版权库是「可剥离」设计：若被误删，本用例必须立刻红。

    festival.py 用 try/except 导入这三个模块（删文件即降级为空表），
    因此"静默变空"是可能的——这条断言就是防那种静默退化。
    """
    assert QUOTES_WEST_MOVIE, "电影台词库缺失或为空（被误删？）"
    assert QUOTES_WEST_GAME, "游戏台词库缺失或为空（被误删？）"
    assert QUOTES_WEST_SONG, "歌曲歌词库缺失或为空（被误删？）"
    assert sum(len(v) for v in QUOTES_WEST_MOVIE.values()) >= 30
    assert sum(len(v) for v in QUOTES_WEST_GAME.values()) >= 20
    assert sum(len(v) for v in QUOTES_WEST_SONG.values()) >= 8


def test_pop_culture_libraries_key_sets():
    """电影库覆盖全部西方节日；游戏库与歌曲库是**有意**的子集。

    游戏里"广为流传 + 氛围确实契合某节日"的交集远小于电影；歌曲里只有圣诞
    这一类与节日强绑定且名句密度够高。宁可缺项也不硬塞弱相关句子（缺的节日
    仍由公有领域库与电影库覆盖）。这条断言把该设计固定下来：子集是刻意的，
    不是遗漏。
    """
    expected = {f.id for f in D.FESTIVALS if set(f.categories) == {D.CATEGORY_WEST}}
    assert set(QUOTES_WEST_MOVIE) == expected
    assert set(QUOTES_WEST_GAME) <= expected
    assert set(QUOTES_WEST_SONG) <= expected
    # 至少要覆盖多数节日，避免哪天被误删成只剩一两个键还"测试通过"
    assert len(QUOTES_WEST_GAME) >= 6
    assert len(QUOTES_WEST_SONG) >= 2
    # 维护者明确要求：圣诞与平安夜必须有歌曲歌词
    assert {"christmas", "christmas_eve"} <= set(QUOTES_WEST_SONG)
    # 每个已覆盖的键都必须有实质内容
    for library in (QUOTES_WEST_GAME, QUOTES_WEST_SONG):
        for key, quotes in library.items():
            assert len(quotes) >= 1, f"{key} 为空"


def test_custom_quotes_are_appended_not_replacing():
    """自定义文案是追加：不应把内置库挤掉。"""
    pool = F.quote_pool(D.FESTIVALS_BY_ID["chunjie"], cfg(custom_quotes_cn=("我的句子",)))
    assert pool[: len(QUOTES_CN["chunjie"])] == QUOTES_CN["chunjie"]
    assert pool[-1] == "我的句子"


def test_custom_quotes_do_not_leak_across_languages():
    pool_cn = F.quote_pool(D.FESTIVALS_BY_ID["chunjie"], cfg(custom_quotes_west=("English only",)))
    pool_west = F.quote_pool(D.FESTIVALS_BY_ID["christmas"], cfg(custom_quotes_cn=("仅中文",)))
    assert "English only" not in pool_cn
    assert "仅中文" not in pool_west


def test_pick_quote_is_deterministic_and_varies_by_index():
    day = dt.date(2026, 2, 17)
    festival = D.FESTIVALS_BY_ID["chunjie"]
    first = F.pick_quote(festival, day, 0, cfg())
    assert first == F.pick_quote(festival, day, 0, cfg())
    pool = F.quote_pool(festival, cfg())
    assert len({F.pick_quote(festival, day, i, cfg()) for i in range(len(pool))}) == len(pool)


def test_build_festival_text_names_the_day_and_appends_quote():
    text = F.build_festival_text(dt.date(2026, 2, 17), cfg(), 0)
    assert text.startswith("今天是春节。")
    assert text[len("今天是春节。"):] in QUOTES_CN["chunjie"]


def test_build_festival_text_without_quote_switch():
    assert F.build_festival_text(dt.date(2026, 2, 17), cfg(show_quote=False)) == "今天是春节。"


def test_build_festival_text_lists_multiple_festivals():
    text = F.build_festival_text(dt.date(2026, 4, 5), cfg(), 0)
    assert text.startswith("今天是清明节、复活节。")


def test_build_festival_text_empty_when_nothing_matches():
    assert F.build_festival_text(PLAIN_DAY, cfg()) == ""
    # 分类全关时同样不提醒
    assert F.build_festival_text(dt.date(2026, 2, 17), cfg(cn=False, solar_terms=False, west=False)) == ""


def test_festival_label_for_menu():
    assert F.festival_label(dt.date(2026, 2, 17), cfg()) == "春节"
    assert F.festival_label(PLAIN_DAY, cfg()) == ""


def test_no_festival_text_is_available_for_manual_entry():
    assert F.NO_FESTIVAL_TEXT


# ---------------------------------------------------------------- 槽位幂等
def test_reminder_slot_only_at_configured_time_and_on_festival_day():
    at_nine = dt.datetime(2026, 2, 17, 9, 0, 30)
    assert F.reminder_slot(at_nine, cfg(count=2)) == "2026-02-17T09:00"
    # 非提醒时间
    assert F.reminder_slot(dt.datetime(2026, 2, 17, 10, 0), cfg(count=2)) == ""
    # 提醒时间但当天无节日
    assert F.reminder_slot(dt.datetime(2026, 1, 2, 9, 0), cfg(count=2)) == ""


def test_reminder_slot_is_stable_within_the_same_minute():
    """同一分钟内的多次 tick 必须得到同一槽位，才能靠盖戳去重。"""
    a = F.reminder_slot(dt.datetime(2026, 2, 17, 9, 0, 1), cfg(count=2))
    b = F.reminder_slot(dt.datetime(2026, 2, 17, 9, 0, 59), cfg(count=2))
    assert a == b == "2026-02-17T09:00"


def test_reminder_slot_does_not_use_master_switch():
    """槽位只回答"此刻该不该提醒"，总开关由服务层把关（手动触发要无视开关）。"""
    assert F.reminder_slot(dt.datetime(2026, 2, 17, 9, 0), cfg(enabled=False)) != ""


def test_startup_slot_only_after_first_reminder_time():
    assert F.startup_slot(dt.datetime(2026, 2, 17, 8, 0), cfg(count=2)) == ""
    assert F.startup_slot(dt.datetime(2026, 2, 17, 12, 0), cfg(count=2)) == "2026-02-17#startup"
    # 当天无节日则不补
    assert F.startup_slot(dt.datetime(2026, 1, 2, 12, 0), cfg(count=2)) == ""


def test_startup_slot_differs_from_scheduled_slot():
    """补提醒不能压掉当天晚些时候的正常提醒。"""
    day = dt.date(2026, 2, 17)
    assert F.startup_slot(dt.datetime(2026, 2, 17, 12, 0), cfg(count=2)) != (
        f"{day.isoformat()}T21:00"
    )


# ---------------------------------------------------------------- 文案库完整性
def test_every_festival_has_a_quote_pool():
    """每个节日都必须有非空文案池，否则提醒会变成光秃秃一行字。

    直接断言真实取词入口的返回值（而不是逐个查内置表），这样新增/剥离
    任一文案库都会被覆盖到。
    """
    missing = [f.id for f in D.FESTIVALS if not F.quote_pool(f, cfg())]
    assert missing == []


def test_quote_libraries_have_no_orphan_keys():
    needed_cn = {
        f.id for f in D.FESTIVALS
        if set(f.categories) & {D.CATEGORY_CN, D.CATEGORY_SOLAR_TERM}
    }
    needed_west = {f.id for f in D.FESTIVALS if set(f.categories) == {D.CATEGORY_WEST}}
    assert set(QUOTES_CN) == needed_cn
    assert set(QUOTES_WEST) == needed_west


@pytest.mark.parametrize("library", [QUOTES_CN, *WEST_LIBRARIES])
def test_quote_entries_are_clean(library):
    for key, quotes in library.items():
        assert quotes, f"{key} 文案池为空"
        assert len(set(quotes)) == len(quotes), f"{key} 存在重复文案"
        for quote in quotes:
            assert isinstance(quote, str)
            assert quote.strip() == quote and quote, f"{key} 文案含首尾空白"
            assert "\n" not in quote, f"{key} 文案含换行"


def _actual_sources(path, marker: str) -> dict[str, int]:
    """从受版权库的**行内出处注释**统计「作品名 -> 条数」。

    出处写成行内注释正是为了让这份统计可被机器核对（而不是靠人誊抄到声明里）。
    """
    import collections
    import re as _re

    src = path.read_text(encoding="utf-8")
    src = src[src.index(marker):]
    names = (
        _re.sub(r"\s*\(\d{4}\)$", "", m).strip()
        for m in _re.findall(r"#\s*(.+?)\s*\(\d{4}\)", src)
    )
    return dict(collections.Counter(names))


def _declared_sources(notices: str, section: str) -> dict[str, int]:
    import re as _re

    part = notices[notices.index(section):]
    nxt = part.find("###", 10)
    if nxt != -1:
        part = part[:nxt]
    return {
        _re.sub(r"\s*\(\d{4}\)$", "", m.group(1)).strip(): int(m.group(2))
        for m in _re.finditer(r"^\|\s*(.+?)\s*\|\s*(\d+)\s*\|$", part, _re.M)
    }


@pytest.mark.parametrize(
    ("path", "marker", "section"),
    [
        ("festival_quotes_west_movie.py", "QUOTES_WEST_MOVIE", "### 5.1"),
        ("festival_quotes_west_game.py", "QUOTES_WEST_GAME", "### 5.2"),
    ],
)
def test_third_party_notices_match_quote_libraries(path, marker, section):
    """THIRD_PARTY_NOTICES.md 的逐作品条数必须与受版权库内容逐项一致。

    声明文件里的数字错一处就是法律文件失实，而手工誊抄必然漂移——本功能开发
    过程中就真的写错过两处（Forrest Gump 2/3、Home Alone 3/2），且因一增一减
    总数恰好不变，只看总数根本发现不了。故用机器核对逐项对齐。
    """
    from pathlib import Path as _Path

    repo_root = _Path(__file__).resolve().parents[1]
    notices = (repo_root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    actual = _actual_sources(repo_root / "pet" / path, marker)
    declared = _declared_sources(notices, section)
    assert declared == actual, (
        "第三方声明与文案库不一致："
        f"\n  声明 vs 实际（不一致项）: "
        f"{ {k: (declared.get(k), actual.get(k)) for k in set(declared) | set(actual) if declared.get(k) != actual.get(k)} }"
    )


def test_third_party_notices_list_every_song():
    """歌曲库每一首都必须在第三方声明里被列出。

    歌曲声明的表结构（歌名/词曲作者/年份/条数）比电影/游戏多两列，不适用上面
    的逐项条数比对，故单独校验"每首都登记了"——漏登记一首就是版权声明缺项。
    """
    from pathlib import Path as _Path
    import re as _re

    notices = (_Path(__file__).resolve().parents[1] / "THIRD_PARTY_NOTICES.md").read_text(
        encoding="utf-8"
    )
    db = _Path(__file__).resolve().parents[1] / "pet" / "festival_quotes_west_song.py"
    # 只取「引号字符串后面紧跟的注释」——否则文件头 # -*- coding -*- 与
    # "# 平安夜（12/24）：…" 这类分组注释也会被当成歌曲名。
    titles = {
        _re.sub(r"\s*\(\d{4}\).*$", "", m).strip()
        for m in _re.findall(r'^\s*"[^"]*",\s*#\s*(.+?)$', db.read_text(encoding="utf-8"), _re.M)
    }
    titles = {t for t in titles if t}
    assert titles, "歌曲库出处注释解析失败"
    missing = [t for t in sorted(titles) if t not in notices]
    assert missing == [], f"第三方声明漏登记以下歌曲：{missing}"


# ---------------------------------------------------------------- 语音播报与让位
class _Win:
    """假桌宠窗口：只记录气泡。"""

    def __init__(self) -> None:
        self.bubbles: list[str] = []

    def show_bubble(self, text: str, duration_ms: int | None = None) -> None:
        self.bubbles.append(text)

    def isVisible(self) -> bool:  # noqa: N802 - 对齐 Qt API
        return True


class _Channel:
    """假音频通道（即语音报时服务）：只记录被要求播报的文本。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.spoken: list[str] = []
        self.fail = fail

    def speak(self, text: str, log_tag: str = "") -> bool:
        if self.fail:
            raise RuntimeError("通道故障")
        self.spoken.append(text)
        return True


class _App:
    def __init__(self, config, channel) -> None:
        self.config = config
        self.win = _Win()
        self._channel = channel

    def ensure_audio_channel(self):
        return self._channel


def _service(config, channel=None):
    from PySide6.QtWidgets import QApplication

    from pet.festival_service import FestivalReminderService

    QApplication.instance() or QApplication([])
    app = _App(config, channel if channel is not None else _Channel())
    return FestivalReminderService(app), app


def _cfg_with(tmp_path, **overrides):
    from pet.config import Config

    cfg = Config(base=tmp_path)
    cfg.set("festival_reminder_enabled", True)
    for key, value in overrides.items():
        cfg.set(key, value)
    return cfg


def test_should_speak_at_true_only_when_speak_on_and_festival_due(tmp_path):
    service, _app = _service(_cfg_with(tmp_path, festival_reminder_speak=True))
    service.apply_config()

    # 报时槽位带调度后缀，判定须只看前 16 位
    assert service.should_speak_at("2026-02-17T09:00#hourly") is True
    # 非提醒时间
    assert service.should_speak_at("2026-02-17T10:00#hourly") is False
    # 提醒时间但当天无节日
    assert service.should_speak_at("2026-01-02T09:00#hourly") is False
    # 脏输入不得抛异常
    assert service.should_speak_at("垃圾数据") is False


def test_should_speak_at_false_when_speak_disabled(tmp_path):
    """节日语音没开时不能让报时让位——否则报时会被静默吞掉。"""
    service, _app = _service(_cfg_with(tmp_path, festival_reminder_speak=False))
    service.apply_config()

    assert service.should_speak_at("2026-02-17T09:00#hourly") is False


def test_service_speaks_festival_text_when_enabled(tmp_path):
    channel = _Channel()
    service, app = _service(_cfg_with(tmp_path, festival_reminder_speak=True), channel)
    service.apply_config()

    service._on_tick(dt.datetime(2026, 2, 17, 9, 0, 5))

    assert len(channel.spoken) == 1
    assert channel.spoken[0].startswith("今天是春节。")
    # 气泡与语音内容一致（都走同一次组装的文案）
    assert app.win.bubbles == channel.spoken


def test_service_stays_silent_when_speak_disabled(tmp_path):
    channel = _Channel()
    service, app = _service(_cfg_with(tmp_path, festival_reminder_speak=False), channel)
    service.apply_config()

    service._on_tick(dt.datetime(2026, 2, 17, 9, 0, 5))

    assert channel.spoken == []
    assert len(app.win.bubbles) == 1, "不出声也必须有气泡"


def test_startup_catch_up_suppresses_same_minute_scheduled_slot(tmp_path):
    """P1：节日当天恰在提醒分钟内启动时，同一分钟不得播报两次。

    回归背景：``_catch_up`` 只用独立槽位 ``{day}#startup`` 盖戳，而 ``_on_tick``
    认的是 ``{day}T{HH:MM}``，两者互不压制；09:00:05 启动会先补报一次，
    紧接着同一分钟的 tick 再播一次（两次气泡 + 两段 TTS），与 ``reminder_slot``
    承诺的"同一天同一提醒时间只播报一次"矛盾。
    """
    channel = _Channel()
    service, app = _service(_cfg_with(tmp_path, festival_reminder_speak=True), channel)
    service.apply_config()

    service._catch_up(dt.datetime(2026, 2, 17, 9, 0, 5))
    assert len(app.win.bubbles) == 1, "启动补提醒应播报一次"
    assert len(channel.spoken) == 1

    # 同一分钟内的 tick（30s 间隔的那一拍）不得再播
    service._on_tick(dt.datetime(2026, 2, 17, 9, 0, 35))
    assert len(app.win.bubbles) == 1, "同一分钟不得重复播报"
    assert len(channel.spoken) == 1, "同一分钟不得重复出声"

    # 当天晚些时候的正式提醒点照常播报：补提醒只压当前这一分钟
    service._on_tick(dt.datetime(2026, 2, 17, 21, 0, 30))
    assert len(app.win.bubbles) == 2
    assert len(channel.spoken) == 2


def test_roll_day_resets_fired_slots_across_days(tmp_path):
    """跨天复位：新一天的同一提醒分钟必须能再播，且次数索引从 0 重新起算。

    回归背景：``_roll_day`` 是"提醒次数 → 文案索引"与"当天去重"的共同前提，
    此前零覆盖；它一旦失效，第二天的提醒会被前一天的槽位永久压掉。
    """
    config = _cfg_with(tmp_path, festival_reminder_speak=True)
    channel = _Channel()
    service, app = _service(config, channel)
    service.apply_config()

    # 2026-02-16 除夕 09:00
    service._on_tick(dt.datetime(2026, 2, 16, 9, 0, 5))
    assert len(app.win.bubbles) == 1
    assert app.win.bubbles[0].startswith("今天是除夕。")
    # 同一天同一槽位再 tick 不重复
    service._on_tick(dt.datetime(2026, 2, 16, 9, 0, 40))
    assert len(app.win.bubbles) == 1

    # 跨天（2026-02-17 春节）：同是 09:00 的槽位，必须重新可播
    service._on_tick(dt.datetime(2026, 2, 17, 9, 0, 5))
    assert len(app.win.bubbles) == 2
    assert app.win.bubbles[1].startswith("今天是春节。")
    # 跨天后"当天第几次提醒"从 0 重算（文案与当天的第一次提醒一致）
    expected = F.build_festival_text(
        dt.date(2026, 2, 17), F.normalize_festival_config(config), 0
    )
    assert app.win.bubbles[1] == expected


def test_remind_now_speaks_and_bubbles(tmp_path):
    channel = _Channel()
    service, app = _service(_cfg_with(tmp_path, festival_reminder_speak=True), channel)
    service.apply_config()

    service.remind_now()

    assert app.win.bubbles and channel.spoken == app.win.bubbles


def test_speak_failure_degrades_to_bubble_only(tmp_path):
    """音频通道故障不得影响提醒本身——有气泡就算成功。"""
    service, app = _service(_cfg_with(tmp_path, festival_reminder_speak=True), _Channel(fail=True))
    service.apply_config()

    service._on_tick(dt.datetime(2026, 2, 17, 9, 0, 5))

    assert len(app.win.bubbles) == 1


def test_missing_audio_channel_degrades_to_bubble_only(tmp_path):
    service, app = _service(_cfg_with(tmp_path, festival_reminder_speak=True), None)
    app._channel = None
    service.apply_config()

    service._on_tick(dt.datetime(2026, 2, 17, 9, 0, 5))

    assert len(app.win.bubbles) == 1


# ---------------------------------------------------------------- 设置页「立即试听」
def _page(tmp_path):
    from PySide6.QtWidgets import QApplication

    from pet.config import Config
    from pet.festival_settings import FestivalSettingsPage

    QApplication.instance() or QApplication([])
    return FestivalSettingsPage(Config(base=tmp_path))


def test_settings_page_has_preview_row_with_button(tmp_path):
    from pet.modern_settings_dialog import SettingRow

    page = _page(tmp_path)
    rows = {r.objectName() for r in page.findChildren(SettingRow)}
    assert "settingRow_festival_preview" in rows
    assert page.preview_btn.text() == "立即试听"


def test_preview_click_emits_signal_and_writes_config(tmp_path):
    page = _page(tmp_path)
    seen: list[int] = []
    page.preview_requested.connect(lambda: seen.append(1))
    page.speak_check.setChecked(True)

    page.preview_btn.click()

    assert seen == [1], "试听应发出信号（供宿主/测试观察）"
    assert page.config.get("festival_reminder_speak") is True, "试听前必须先落盘当前控件值"


def test_preview_click_is_safe_without_any_host(tmp_path):
    """没有宿主回调（独立构造/宿主未接线）时点击不得抛异常。"""
    page = _page(tmp_path)
    assert page._resolve_preview_callback() is None
    page.preview_btn.click()  # 不得抛


def test_preview_click_reaches_host_callback_end_to_end(tmp_path):
    """按钮 -> 向上解析宿主回调 -> 真实服务 remind_now -> 气泡，整条链路打通。"""
    from PySide6.QtWidgets import QWidget

    page = _page(tmp_path)
    channel = _Channel()
    # 必须改**控件**而不是 config：点击会先 apply_to_config()，用控件值覆盖 config
    # （这正是"先落盘再触发"的语义，直接改 config 会被冲掉）。
    page.enabled_check.setChecked(True)
    page.speak_check.setChecked(True)
    service, app = _service(page.config, channel)
    service.apply_config()

    host = QWidget()
    host.on_festival_now = service.remind_now  # 模拟 AppShell 给窗口赋的入口
    page.setParent(host)

    page.preview_btn.click()

    assert app.win.bubbles, "试听必须产生气泡"
    assert channel.spoken == app.win.bubbles, "开启语音播报时试听应同时出声"


def test_preview_resolution_survives_an_extra_parent_layer(tmp_path):
    """向上解析必须容忍多一层包装（页面被 reparent 到中间容器时不失效）。"""
    from PySide6.QtWidgets import QWidget

    page = _page(tmp_path)
    called: list[int] = []
    host = QWidget()
    host.on_festival_now = lambda: called.append(1)
    middle = QWidget(host)      # 中间多一层
    page.setParent(middle)

    page.preview_btn.click()

    assert called == [1]


def test_appshell_trigger_festival_now_reaches_service(tmp_path):
    """试听链路的末端：窗口属性 on_festival_now 指向 AppShell.trigger_festival_now。

    该入口必须**无视总开关**（试听语义）并懒创建服务，与语音报时的
    trigger_voice_chime_now 同约定。
    """
    from PySide6.QtWidgets import QApplication

    from pet.app import AppShell
    from pet.config import Config

    QApplication.instance() or QApplication([])
    cfg = Config(base=tmp_path)
    cfg.set("festival_reminder_enabled", False)   # 总开关关闭
    cfg.set("festival_reminder_speak", False)
    shell = AppShell(QApplication.instance(), cfg, enable_chat=False)
    assert shell.festival_service is None

    shell.trigger_festival_now()                  # 不得抛

    assert shell.festival_service is not None, "试听应懒创建服务（无视总开关）"
    shell._on_about_to_quit()


def test_preview_rows_are_collected_into_domain_sections(tmp_path):
    """试听行必须被域收集机制收进某个 SettingsSection（否则打包版里按钮会不见）。

    历史事故：设置页里**没包在 SettingRow 内**的控件（含页面根布局里的按钮）不会
    被 `_rebuild_domain_navigation` 收集，页面被移出 pages 后按钮直接消失——当时
    的表现是"打包版看不到立即试听"。这里用**真实对话框**断言试听行确实落进了域卡片，
    同时覆盖语音报时与节日提醒两个按钮（后者是本次新增）。
    """
    from PySide6.QtWidgets import QApplication

    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog
    from pet.settings_widgets import SettingsSection

    QApplication.instance() or QApplication([])
    dialog = ModernSettingsDialog(Config(base=tmp_path), include_ai=False)
    try:
        sections = dialog.findChildren(SettingsSection)
        for target in ("settingRow_festival_preview", "settingRow_voice_chime_preview"):
            holders = [
                s for s in sections if any(r.objectName() == target for r in s.rows)
            ]
            assert holders, f"{target} 未被任何域卡片收集（历史事故：打包版不可见）"
    finally:
        dialog.close()
