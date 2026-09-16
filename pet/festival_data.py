# -*- coding: utf-8 -*-
"""节日提醒：节日定义表（纯数据，零 Qt / 零 GUI）。

收录策略（敏感议题规避，属**白名单**制）
----------------------------------------
只收录**民俗 / 自然 / 文化**类日期，且必须同时满足：有广泛民间认知、
氛围适合桌宠温馨提醒、不含政治或宗教争议。

- 收录：公历民俗节日（元旦/劳动节/儿童节/国庆节）、农历传统节日
  （春节/元宵/龙抬头/端午/七夕/中元/中秋/重阳/腊八/除夕）、全部 24 节气、
  西方民俗节日（情人节/愚人节/复活节/母亲节/父亲节/万圣节/平安夜/圣诞节）。
- **故意不收录**：任何政治性纪念日、宗教教义性节日、历史事件纪念日。
  国庆节属国家法定节日、民间普遍庆祝，按维护者决定收录。
- **故意不收录「小年」**：北方腊月廿三、南方腊月廿四，存在地域分歧，
  收录任一日都会让另一半用户觉得不对，故整体不收。
- **故意不收录「感恩节」**：按维护者决定整体移除——它在各文案库中的素材
  质量均偏弱（游戏库尤其找不到氛围契合的名句），且与中文用户日常关联度低，
  与其留着一条勉强的提醒，不如不做。
- 中元节按维护者决定收录；其氛围为追思，文案库按「肃穆追思」而非
  「欢庆」选材（见 festival_quotes_cn.py 的选材说明）。

防回归：`tests/test_festival.py` 对本表的 id 集合做**冻结快照**断言。
新增/删除节日必须同步改快照，使每一次收录决策都成为显式、可评审的改动，
而不是悄悄混入。
"""

from __future__ import annotations

from dataclasses import dataclass

from .festival_calendar import SOLAR_TERMS

# —— 类别 ——（同时是配置开关名与显示分组名）
CATEGORY_CN = "cn"
CATEGORY_SOLAR_TERM = "solar_term"
CATEGORY_WEST = "west"

CATEGORY_LABELS: dict[str, str] = {
    CATEGORY_CN: "中国节日",
    CATEGORY_SOLAR_TERM: "24 节气",
    CATEGORY_WEST: "西方节日",
}

# 展示与匹配的顺序：先中国节日，再节气，最后西方节日。
CATEGORY_ORDER: tuple[str, ...] = (CATEGORY_CN, CATEGORY_SOLAR_TERM, CATEGORY_WEST)

# —— 日期规则种类 ——
KIND_SOLAR = "solar"              # 公历固定月日
KIND_LUNAR = "lunar"              # 农历固定月日（非闰月）
KIND_LUNAR_LAST = "lunar_last"    # 腊月最后一天（除夕）
KIND_SOLAR_TERM = "solar_term"    # 24 节气当日
KIND_EASTER = "easter"            # 复活节（西方教会算法）
KIND_NTH_WEEKDAY = "nth_weekday"  # 某月第 n 个星期 w


@dataclass(frozen=True)
class Festival:
    """一个节日/节气的定义。

    ``categories`` 是元组而非单值：清明节既是 24 节气之一、又是中国传统
    节日，两个开关任一开启都应命中（见 ``festival.is_festival_enabled``）。
    """

    id: str
    name: str
    categories: tuple[str, ...]
    kind: str
    month: int = 0          # KIND_SOLAR / KIND_NTH_WEEKDAY
    day: int = 0            # KIND_SOLAR
    lunar_month: int = 0    # KIND_LUNAR
    lunar_day: int = 0      # KIND_LUNAR
    term: str = ""          # KIND_SOLAR_TERM
    weekday: int = 0        # KIND_NTH_WEEKDAY：周一=0 … 周日=6
    nth: int = 0            # KIND_NTH_WEEKDAY


_SUN = 6

# —— 中国节日（14 项；清明节见下方节气表，带 cn 类别）——
_CN_FESTIVALS: tuple[Festival, ...] = (
    Festival("yuandan", "元旦", (CATEGORY_CN,), KIND_SOLAR, month=1, day=1),
    Festival("chunjie", "春节", (CATEGORY_CN,), KIND_LUNAR, lunar_month=1, lunar_day=1),
    Festival("yuanxiao", "元宵节", (CATEGORY_CN,), KIND_LUNAR, lunar_month=1, lunar_day=15),
    Festival("longtaitou", "龙抬头", (CATEGORY_CN,), KIND_LUNAR, lunar_month=2, lunar_day=2),
    Festival("laodongjie", "劳动节", (CATEGORY_CN,), KIND_SOLAR, month=5, day=1),
    Festival("duanwu", "端午节", (CATEGORY_CN,), KIND_LUNAR, lunar_month=5, lunar_day=5),
    Festival("ertongjie", "儿童节", (CATEGORY_CN,), KIND_SOLAR, month=6, day=1),
    Festival("qixi", "七夕节", (CATEGORY_CN,), KIND_LUNAR, lunar_month=7, lunar_day=7),
    Festival("zhongyuan", "中元节", (CATEGORY_CN,), KIND_LUNAR, lunar_month=7, lunar_day=15),
    Festival("zhongqiu", "中秋节", (CATEGORY_CN,), KIND_LUNAR, lunar_month=8, lunar_day=15),
    Festival("chongyang", "重阳节", (CATEGORY_CN,), KIND_LUNAR, lunar_month=9, lunar_day=9),
    Festival("guoqingjie", "国庆节", (CATEGORY_CN,), KIND_SOLAR, month=10, day=1),
    Festival("labajie", "腊八节", (CATEGORY_CN,), KIND_LUNAR, lunar_month=12, lunar_day=8),
    Festival("chuxi", "除夕", (CATEGORY_CN,), KIND_LUNAR_LAST),
)

# —— 西方节日（9 项）——
_WEST_FESTIVALS: tuple[Festival, ...] = (
    Festival("valentine", "情人节", (CATEGORY_WEST,), KIND_SOLAR, month=2, day=14),
    Festival("april_fools", "愚人节", (CATEGORY_WEST,), KIND_SOLAR, month=4, day=1),
    Festival("easter", "复活节", (CATEGORY_WEST,), KIND_EASTER),
    Festival("mothers_day", "母亲节", (CATEGORY_WEST,), KIND_NTH_WEEKDAY, month=5, weekday=_SUN, nth=2),
    Festival("fathers_day", "父亲节", (CATEGORY_WEST,), KIND_NTH_WEEKDAY, month=6, weekday=_SUN, nth=3),
    Festival("halloween", "万圣节", (CATEGORY_WEST,), KIND_SOLAR, month=10, day=31),
    Festival("christmas_eve", "平安夜", (CATEGORY_WEST,), KIND_SOLAR, month=12, day=24),
    Festival("christmas", "圣诞节", (CATEGORY_WEST,), KIND_SOLAR, month=12, day=25),
)


def _solar_term_festivals() -> tuple[Festival, ...]:
    """由 ``SOLAR_TERMS`` 生成 24 个节气定义。

    清明是唯一特例：它同时是传统节日「清明节」，故 id/显示名用节日口径，
    并**附带 cn 类别**，使「中国节日」与「24 节气」两个开关任一开启都命中。
    """
    entries: list[Festival] = []
    for index, term in enumerate(SOLAR_TERMS):
        if term == "清明":
            entries.append(
                Festival(
                    "qingming",
                    "清明节",
                    (CATEGORY_SOLAR_TERM, CATEGORY_CN),
                    KIND_SOLAR_TERM,
                    term=term,
                )
            )
        else:
            entries.append(
                Festival(
                    f"term_{index:02d}",
                    term,
                    (CATEGORY_SOLAR_TERM,),
                    KIND_SOLAR_TERM,
                    term=term,
                )
            )
    return tuple(entries)


_SOLAR_TERM_FESTIVALS: tuple[Festival, ...] = _solar_term_festivals()

#: 全量节日表（中国节日 + 24 节气 + 西方节日）。
FESTIVALS: tuple[Festival, ...] = _CN_FESTIVALS + _SOLAR_TERM_FESTIVALS + _WEST_FESTIVALS

#: id -> Festival 索引，供文案库与槽位按 id 反查。
FESTIVALS_BY_ID: dict[str, Festival] = {f.id: f for f in FESTIVALS}

assert len(FESTIVALS_BY_ID) == len(FESTIVALS), "节日 id 必须唯一"
