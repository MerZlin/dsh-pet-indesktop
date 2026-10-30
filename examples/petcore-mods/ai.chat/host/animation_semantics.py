"""聊天文本 → 动作名（纯逻辑，无 Qt / 无网络）。

动作名来自当前角色的 ``PetHandle.animations()``（与行为链的随机池同一批）。
规则只负责「这段话更像什么情绪/场景」，落不到具体动画时返回 ``None``，
绝不编造不存在的素材名。
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    key: str
    keywords: tuple[str, ...]
    names: tuple[str, ...]


#: 顺序即优先级；同一段文本按命中次数打分，分数相同取靠前的规则。
RULES: tuple[Rule, ...] = (
    Rule(
        "happy",
        ("开心", "高兴", "哈哈", "笑", "太好了", "棒", "喜欢", "嘿嘿"),
        (
            "开心",
            "笑",
            "欢呼",
            "高兴",
            "转圈",
            # 随包素材的实际词表：雀跃庆祝 / 宅舞 / 摇摆舞 / 360 度旋转展示。
            # 不写 "跳舞"：那是 music 的专属名，两边都认会让"开心"误播唱歌类素材。
            "雀跃",
            "庆祝",
            "宅舞",
            "摇摆",
            "旋转",
        ),
    ),
    Rule(
        "sad",
        ("难过", "伤心", "抱歉", "对不起", "遗憾", "失望", "哭", "委屈"),
        ("难过", "哭", "垂头", "伤心", "委屈"),
    ),
    Rule(
        "angry",
        (
            "生气",
            "讨厌",
            "气死",
            "烦",
            "别闹",
            # 去掉 "哼"：它会把「悠闲哼歌」当成生气素材（哼歌是开心地在哼）。
            "气急败坏",
        ),
        ("生气", "叉腰", "瞪", "气急败坏"),
    ),
    Rule(
        "sleepy",
        ("困", "睡", "晚安", "累了", "休息", "打哈欠", "熬夜"),
        ("睡觉", "困", "打哈欠", "休息", "躺"),
    ),
    Rule(
        "eat",
        ("吃", "饭", "饿", "好吃", "零食", "喝", "奶茶"),
        ("吃Token", "吃", "嚼", "喝", "干饭"),
    ),
    Rule(
        "think",
        ("思考", "让我想", "考虑", "琢磨", "嗯…", "嗯...", "想想"),
        ("思考", "想", "托腮", "发呆", "沉思"),
    ),
    Rule(
        "code",
        ("代码", "程序", "bug", "调试", "写代码", "开发", "编译"),
        ("写代码", "敲键盘", "打字", "编程"),
    ),
    Rule(
        "greet",
        ("你好", "嗨", "早上好", "晚上好", "在吗", "哈喽", "早安"),
        ("招手", "打招呼", "挥手", "你好", "礼仪", "屈膝"),
    ),
    Rule(
        "thanks",
        ("谢谢", "感谢", "多谢", "辛苦了", "麻烦你"),
        ("鞠躬", "道谢", "害羞", "点头", "礼仪", "屈膝"),
    ),
    Rule(
        "surprise",
        ("哇", "居然", "没想到", "惊讶", "震惊", "真的吗", "吓"),
        ("惊讶", "震惊", "瞪眼", "后退", "吓一跳", "被吓"),
    ),
    Rule(
        "pat",
        ("摸头", "乖", "抱抱", "亲亲", "可爱", "蹭蹭"),
        ("摸头", "害羞", "蹭", "抱抱"),
    ),
    Rule(
        "music",
        ("跳舞", "唱歌", "音乐", "嗨起来", "哼歌", "唱首歌", "唱一曲"),
        ("跳舞", "唱歌", "哼歌", "摇摆"),
    ),
)


def _score(text: str, rule: Rule) -> int:
    return sum(1 for keyword in rule.keywords if keyword in text)


def _match_names(rule: Rule, available: tuple[str, ...]) -> list[str]:
    exact = [name for name in available if name in rule.names]
    if exact:
        return exact
    aliases = [
        name
        for name in available
        if any(alias in name or name in alias for alias in rule.names)
    ]
    if aliases:
        return aliases
    return [
        name
        for name in available
        if any(keyword in name for keyword in rule.keywords)
    ]


def choose_animation(
    reply_text: str,
    user_text: str = "",
    available: tuple[str, ...] = (),
    *,
    rng: random.Random | None = None,
    previous: str = "",
) -> str | None:
    """从可用动作名里挑一个与回复语义相符的；没有合适素材返回 ``None``。"""
    names = tuple(str(name) for name in available if str(name))
    if not names:
        return None
    reply = str(reply_text or "")
    user = str(user_text or "")
    ranked: list[tuple[int, int, Rule]] = []
    for index, rule in enumerate(RULES):
        score = _score(reply, rule) * 2 + _score(user, rule)
        if score > 0:
            ranked.append((score, index, rule))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (-item[0], item[1]))
    for _score_value, _index, rule in ranked:
        candidates = _match_names(rule, names)
        if not candidates:
            continue
        if previous and len(candidates) > 1:
            candidates = [name for name in candidates if name != previous] or candidates
        return rng.choice(candidates) if rng is not None else candidates[0]
    return None


def choose_thinking(
    available: tuple[str, ...] = (),
    *,
    rng: random.Random | None = None,
) -> str | None:
    """生成回复时的「思考」动作；没有思考类素材就不播。"""
    names = tuple(str(name) for name in available if str(name))
    if not names:
        return None
    rule = next(rule for rule in RULES if rule.key == "think")
    candidates = _match_names(rule, names)
    if not candidates:
        return None
    return rng.choice(candidates) if rng is not None else candidates[0]
