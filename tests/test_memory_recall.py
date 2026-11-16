# -*- coding: utf-8 -*-
"""全局记忆 v2a：分层、检索打分、已见去重、首轮闸与 topic 归档。

测试口径与 ``.scratch/memory-v2/SPEC-v2a.md`` 一致：
- 老记忆文件（无 layer/keywords）读取即 fact，且**不迁移**（盘上原样）；
- 打分对交集、idf、覆盖率、艾宾浩斯衰减、importance 各自单调；
- top-3 的前 2 条不排除已见，第 3 条起跳过已见；
- 首轮只注 user 快照，第二轮起才命中 fact/lesson/topic；
- topic 连续 14 天未命中自动归档、可恢复；
- 手动钉四前缀分别落 user/lesson/topic/fact。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from pet.chat.memory import (
    LAYERS,
    ChatMemory,
    injection_text,
    layer_of,
    memory_path,
    memory_payload,
    parse_remember_command,
)
from pet.chat.memory_recall import (
    HALF_LIFE_DAYS,
    MAX_KEYWORDS,
    RECALL_TOP_K,
    RELATIVE_MIN_RATIO,
    TOPIC_ARCHIVE_DAYS,
    _SEEN,
    _entry_key,
    _now,
    archive_stale_topics,
    augment_for_turn,
    extract_keywords,
    is_archived,
    rank_entries,
    recall,
    render_user_snapshot,
)
from pet.chat.models import ChatMessage, ChatSettings
from pet.chat.prompt import PromptBuilder

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def _entry(
    text: str,
    *,
    layer: str = "fact",
    keywords: list[str] | None = None,
    source: str = "manual",
    updated_at: datetime = NOW,
    entry_id: str | None = None,
    **extra,
) -> dict:
    item = {
        "id": entry_id or f"id-{text}",
        "text": text,
        "source": source,
        "layer": layer,
        "created_at": updated_at.isoformat(),
        "updated_at": updated_at.isoformat(),
        "hits": 1,
    }
    if keywords is not None:
        item["keywords"] = list(keywords)
    item.update(extra)
    return item


def _scores(entries: list[dict], query: str, now: datetime = NOW) -> dict[str, float]:
    return {entry["text"]: score for score, entry in rank_entries(entries, query, now=now)}


# ---------------------------------------------------------------- 分层兼容
def test_old_memory_file_reads_as_fact_without_migration(tmp_path):
    path = tmp_path / "memory.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": [
                    {
                        "id": "old-1",
                        "text": "周三有课",
                        "source": "manual",
                        "created_at": NOW.isoformat(),
                        "updated_at": NOW.isoformat(),
                        "hits": 1,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    before = path.read_text(encoding="utf-8")
    entries = ChatMemory(path).load()
    assert entries[0]["layer"] == "fact"
    assert layer_of(entries[0]) == "fact"
    assert entries[0]["keywords"]  # 老条目现场 bigram 兜底
    assert path.read_text(encoding="utf-8") == before  # 无迁移写盘


def test_unknown_layer_falls_back_to_fact():
    assert layer_of({"text": "x", "layer": "bogus"}) == "fact"
    assert layer_of({"text": "x"}) == "fact"
    assert LAYERS == ("user", "fact", "lesson", "topic")


def test_load_grouped_is_four_groups_and_newest_first(tmp_path):
    ticks = iter(
        [
            NOW,
            NOW + timedelta(minutes=1),
            NOW + timedelta(minutes=2),
            NOW + timedelta(minutes=3),
            NOW + timedelta(minutes=4),
        ]
    )
    mem = ChatMemory(tmp_path / "memory.json", clock=lambda: next(ticks))
    mem.add("旧事实", layer="fact")
    mem.add("新事实", layer="fact")
    mem.add("偏好", layer="user")
    mem.add("教训", layer="lesson")
    mem.add("话题", layer="topic")
    groups = mem.load_grouped()
    assert set(groups) == set(LAYERS)
    assert [e["text"] for e in groups["fact"]] == ["新事实", "旧事实"]
    assert [e["text"] for e in groups["user"]] == ["偏好"]
    assert [e["text"] for e in groups["lesson"]] == ["教训"]
    assert [e["text"] for e in groups["topic"]] == ["话题"]


# ---------------------------------------------------------------- 关键词 / 打分
def test_extract_keywords_bigram_and_words_and_cap():
    keywords = extract_keywords("我不吃辣，喜欢 Python 和 pytest")
    assert "不吃" in keywords
    assert "python" in keywords
    assert "pytest" in keywords
    # v2b A：上限放宽到整条记忆（48），不再只留前 6 个 bigram。
    assert len(extract_keywords("很长" * 100)) <= MAX_KEYWORDS
    assert MAX_KEYWORDS == 48


def test_long_entry_keyword_cap_recalls_exam_topic():
    """盲审反例 1：条目关键词不能只留前 6 个 bigram（考试被截掉）。"""
    entries = [_entry("我在准备十月底的考试"), _entry("今天天气不错")]
    result = recall(entries, "考试还有两周，有点紧张", now=NOW)
    assert [e["text"] for e in result.entries][:1] == ["我在准备十月底的考试"]
    assert "今天天气不错" not in [e["text"] for e in result.entries]


def test_version_number_produces_bigrams_and_recalls():
    """盲审反例 2：4.4.0 这类数字 token 不再按长度丢弃，且产出 4.4/4.0 bigram。"""
    keywords = extract_keywords("版本 4.4.0 修了几个崩溃")
    assert "4.4" in keywords
    assert "4.0" in keywords
    entries = [_entry("版本 4.4.0 修了几个崩溃"), _entry("今天天气不错")]
    result = recall(entries, "4.4.0 还有问题吗", now=NOW)
    assert [e["text"] for e in result.entries][:1] == ["版本 4.4.0 修了几个崩溃"]
    assert "今天天气不错" not in [e["text"] for e in result.entries]


def test_score_grows_with_keyword_intersection():
    entries = [
        _entry("多词", keywords=["考试", "复习"]),
        _entry("单词", keywords=["考试"]),
    ]
    assert _scores(entries, "考试 复习")["多词"] > _scores(entries, "考试 复习")["单词"]


def test_score_grows_with_idf():
    entries = [
        _entry("rare", keywords=["alpha"]),
        _entry("common-1", keywords=["beta"]),
        _entry("common-2", keywords=["beta"]),
        _entry("common-3", keywords=["beta"]),
    ]
    scores = _scores(entries, "alpha beta")
    assert scores["rare"] > scores["common-1"]


def test_coverage_favors_entry_with_fewer_unmatched_keywords():
    """v2c M2：长度归一（BM25）取代 coverage——短条目不再因分母小占便宜。"""
    entries = [
        _entry("focused", keywords=["alpha"]),
        _entry("diffuse", keywords=["alpha", "beta"]),
    ]
    # beta 在另一条里也出现，idf 相同口径下 focused 的长度归一更低
    scores = _scores(entries, "alpha")
    assert scores["focused"] > scores["diffuse"]


def test_ebbinghaus_decay_halves_every_fourteen_days():
    old = _entry("旧", keywords=["alpha"], updated_at=NOW - timedelta(days=HALF_LIFE_DAYS))
    new = _entry("新", keywords=["alpha"], updated_at=NOW)
    scores = _scores([old, new], "alpha")
    assert scores["新"] > scores["旧"]
    assert scores["旧"] == pytest.approx(scores["新"] * 0.5, rel=1e-6)


def test_importance_manual_and_lesson_are_weighted():
    auto = _entry("自动", keywords=["alpha"], source="auto")
    manual = _entry("手动", keywords=["alpha"], source="manual")
    lesson = _entry("教训", layer="lesson", keywords=["alpha"], source="auto")
    # 小语料里 alpha 的 idf 会变低，但相对门槛只看三者相对最高分；
    # 加无关条目让 idf 回到可判定区间，再比较 importance。
    noise = [
        _entry(f"噪声-{index}", keywords=[f"noise{index}"])
        for index in range(20)
    ]
    scores = _scores([auto, manual, lesson, *noise], "alpha")
    assert scores["手动"] > scores["自动"]
    assert scores["教训"] > scores["自动"]


def test_global_idf_keeps_relative_order_when_unrelated_entries_added():
    """G：idf 用全部未归档条目统计，加无关条目后既有条目相对排序不变。"""
    focused = _entry("focused", keywords=["alpha"])
    diffuse = _entry("diffuse", keywords=["alpha", "beta"])
    baseline = recall([focused, diffuse], "alpha", now=NOW)
    assert [e["text"] for e in baseline.entries] == ["focused", "diffuse"]
    noise = [
        _entry(f"无关-{index}", keywords=[f"zz{index}"])
        for index in range(39)
    ]
    grown = recall([focused, diffuse, *noise], "alpha", now=NOW)
    assert [e["text"] for e in grown.entries] == ["focused", "diffuse"]


def test_now_dead_branch_merged():
    """H：naive 与 aware 都按本地时区归一，语义不变。"""
    naive = datetime(2026, 10, 21, 12, 0)
    aware = naive.astimezone()
    assert _now(naive) == aware
    assert _now(aware) == aware


# ---------------------------------------------------------------- v2c M2：BM25
#: 盲审反例的长条目关键词（48 个，前 6 个覆盖 cap=6 的对比）。
_LONG_ENTRY_KEYWORDS = [
    "鉴权", "token", "登录", "权限", "刷新", "过期", "失败", "重试",
    "接口", "服务", "证书", "会话", "缓存", "网关", "报错", "堆栈",
    "复现", "修复", "验证", "回滚", "配置", "环境", "测试", "限流",
    "熔断", "降级", "监控", "告警", "链路", "超时", "并发", "数据库",
    "索引", "迁移", "备份", "恢复", "部署", "容器", "镜像", "启动",
    "停止", "健康", "检查", "依赖", "升级", "兼容", "文档", "回放",
]
assert len(_LONG_ENTRY_KEYWORDS) == MAX_KEYWORDS == 48


@pytest.mark.parametrize("cap", [6, MAX_KEYWORDS])
def test_bm25_length_normalization_keeps_long_entry_first(cap):
    """M2 盲审反例：长条目 + 三条两字短条目，查询同时命中。

    旧 coverage 下长条目 5/48 对短条目 1.0；BM25 长度归一后长条目必须
    排第 1（不只是进 top-3），且 cap=6 / 48 两遍都不能反转。
    """
    long_entry = _entry(
        "关于那个鉴权 bug 的完整记录",
        keywords=_LONG_ENTRY_KEYWORDS[:cap],
    )
    shorts = [_entry(text, keywords=[text]) for text in ("发版", "日志", "灰度")]
    entries = [long_entry, *shorts]
    query = "鉴权 token 登录 发版 日志 灰度"
    ranked = rank_entries(entries, query, now=NOW)
    assert ranked[0][1]["text"] == long_entry["text"], (
        f"cap={cap} 时长条目被短条目挤出第 1：{[e['text'] for _s, e in ranked]}")
    assert all(ranked[0][0] > score for score, _entry_ in ranked[1:])
    result = recall(entries, query, now=NOW)
    assert result.entries[0]["text"] == long_entry["text"]


# ---------------------------------------------------------------- v2c M4：误注入
def test_single_ascii_and_version_parts_are_not_keywords():
    """M4：单字符 ASCII 不入关键词；4.4.0 只留整串 + 含点分段。"""
    keywords = extract_keywords("版本 4.4.0 a b 他说第 4 点")
    assert "4.4.0" in keywords
    assert "4.4" in keywords
    assert "4.0" in keywords
    assert "4" not in keywords
    assert "0" not in keywords
    assert "a" not in keywords
    assert "b" not in keywords


def test_version_entry_not_hit_by_bare_number_but_hit_by_full_version():
    """M4：4.4.0 的条目不得被「第 4 点」命中，但必须被完整版本号命中。"""
    version = _entry("版本 4.4.0 修了几个崩溃")
    assert recall([version], "他说第 4 点还要再确认一下", now=NOW).entries == ()
    result = recall([version], "4.4.0 还有问题吗", now=NOW)
    assert [e["text"] for e in result.entries] == ["版本 4.4.0 修了几个崩溃"]


def test_single_char_is_gated_but_shared_common_word_is_relative():
    """v2f N2：单字仍挡；共享常见词不再被绝对 idf 门槛整体挡掉。"""
    assert rank_entries(
        [_entry("单字", keywords=["点"])], "点", now=NOW) == []
    common = [_entry(f"常见-{index}", keywords=["发版"]) for index in range(30)]
    # 30 条同主题：旧 MIN_SCORE 会因 idf 被 df/N 压到 0.15 以下而返回 []；
    # 相对门槛下它们分数相同，应全部可检索（top-3 由 recall 截取）。
    assert len(rank_entries(common, "发版", now=NOW)) == 30
    assert len(recall(common, "发版", now=NOW).entries) == RECALL_TOP_K
    assert RELATIVE_MIN_RATIO == 0.25


def test_zero_score_entry_is_never_injected():
    """v2f N2：衰减下溢成 0 分的条目（score <= 0）永不进入注入段。"""
    ancient = _entry(
        "论文", keywords=["论文"], updated_at=NOW - timedelta(days=20000))
    assert rank_entries([ancient], "论文", now=NOW) == []
    assert recall([ancient], "论文", now=NOW).entries == ()


def test_relative_threshold_prunes_entries_below_quarter_of_best():
    """v2f N2：相对门槛只保留 best_score * RELATIVE_MIN_RATIO 以上的条目。"""
    strong = _entry("strong", keywords=["alpha", "beta", "gamma"])
    weak = _entry("weak", keywords=["alpha"])
    ranked = rank_entries(
        [strong, weak],
        "alpha beta gamma",
        now=NOW,
        idf={"alpha": 0.01, "beta": 1.0, "gamma": 1.0},
        avgdl=2.0,
    )
    assert [entry["text"] for _score, entry in ranked] == ["strong", "weak"]
    result = recall(
        [strong, weak],
        "alpha beta gamma",
        now=NOW,
    )
    # recall 内用 idf 按全库重算（alpha 在 2 条里都出现，比例仍远低于 0.25），
    # 因此弱条目被剔除，只剩强条目。
    assert [entry["text"] for entry in result.entries] == ["strong"]


def test_shared_topic_word_keeps_the_whole_topic_recallable():
    """v2f N2 盲审探针：6 条全是论文主题、只共享「论文」时两种提问都能命中。"""
    entries = [
        _entry("论文的数据集已经整理完"),
        _entry("论文的图表还没画"),
        _entry("论文的实验跑完了"),
        _entry("论文的摘要写了三版"),
        _entry("论文的参考文献要补"),
        _entry("论文的导师意见还没回"),
    ]
    relevant = {entry["text"] for entry in entries}
    for query in ("论文进度怎么样了", "论文还要改吗"):
        result = recall(entries, query, now=NOW)
        assert len(result.entries) == RECALL_TOP_K, query
        assert {entry["text"] for entry in result.entries} <= relevant, query
        assert result.text


_CHITCHAT_MEMORIES = [
    "我不吃辣，口味清淡",
    "我通常十一点前就睡了",
    "我在准备十月底的考试",
    "生日是三月十七号",
    "我养了只猫，叫团子",
    "我住在杭州，周末常去西湖",
    "我在做后端开发，主要写 Python",
    "我讨厌别人迟到",
    "我喝咖啡不加糖",
    "我每周三有日语课",
    "版本 4.4.0 修了几个崩溃",
    "上次把配置文件删了，别再用 rm -rf",
    "显示器是 27 寸 4K，刷新率 144 Hz",
    "Python 版本是 3.11",
    "Qt 版本是 6.5",
    "我在控制体重，晚饭不吃主食",
    "手机尾号是 8848",
    "我周末常打羽毛球",
    "我对花生过敏",
    "我计划十二月去日本旅行",
]

_CHITCHAT_LINES = [
    "今天天气怎么样",
    "晚上吃什么好呢",
    "他说第 4 点还要再确认一下",
    "你能讲个笑话吗",
    "我有点累了",
    "刚才那个电影好看吗",
    "帮我查一下明天几点日出",
    "最近有什么新闻",
    "你觉得这个颜色怎么样",
    "我找不到钥匙了",
    "我 3 点还有个会",
    "外面下雨了吗",
    "这首歌叫什么名字",
    "我明天要早起",
    "这个要等 5 分钟",
    "这个游戏好玩吗",
    "我刚刚看到一只鸟",
    "你能推荐一本书吗",
    "第 1 步怎么做",
    "我买了新鞋子",
    "先做 2 个版本试试",
    "帮我写一句祝福语",
    "今天工作好忙",
    "6 点下班",
    "我想换个发型",
    "这个功能怎么用",
    "我的电脑有点卡",
    "你最喜欢什么颜色",
    "明天会更好吗",
    "随便聊聊吧",
]


def test_chitchat_false_injection_rate_stays_at_baseline():
    """v2c M4 基线：20 条真实风格记忆 × 30 句无关闲聊，注入轮数 ≤ 3/30。

    v2f N2 删掉绝对 idf 门槛后，本机同规模语料实测仍为 1/30（只有「先做 2 个
    版本试试」与「版本 4.4.0」共享真实词「版本」）；相对门槛不改变命中与否，
    只裁剪同轮弱条目。
    """
    memories = [_entry(text) for text in _CHITCHAT_MEMORIES]
    injected = [
        line for line in _CHITCHAT_LINES
        if recall(memories, line, now=NOW, turn_index=2).entries
    ]
    assert len(injected) <= 3, f"误注入 {len(injected)}/30：{injected}"


# ---------------------------------------------------------------- v2c L3：超长查询
def test_extract_keywords_long_cjk_query_is_bounded_and_fast():
    """L3：100k CJK 查询达到 limit 立即停，关键词数 ≤ limit 且耗时 <3ms。"""
    text = "测" * 100_000
    timings: list[float] = []
    keywords: list[str] = []
    for _ in range(5):
        started = time.perf_counter()
        keywords = extract_keywords(text, limit=64)
        timings.append(time.perf_counter() - started)
    elapsed = min(timings)
    assert elapsed < 0.003, f"100k CJK 查询耗时 {elapsed * 1000:.3f}ms"
    assert len(keywords) <= 64


# ---------------------------------------------------------------- 命中 / 已见
def test_first_turn_has_no_recall_and_second_turn_does():
    entries = [
        _entry("我不吃辣", layer="user"),
        _entry("周三有课", keywords=["周三", "有课"]),
    ]
    assert "我不吃辣" in render_user_snapshot(entries, now=NOW)
    assert "周三有课" not in render_user_snapshot(entries, now=NOW)
    assert recall(entries, "周三有课吗", now=NOW, turn_index=1).entries == ()
    second = recall(entries, "周三有课吗", now=NOW, turn_index=2)
    assert [e["text"] for e in second.entries] == ["周三有课"]


def test_recall_top_three_and_first_two_ignore_seen():
    entries = [
        _entry(f"e{index}", keywords=[f"kw{index}"],
               updated_at=NOW - timedelta(days=index))
        for index in range(4)
    ]
    query = "kw0 kw1 kw2 kw3"
    first = recall(entries, query, now=NOW)
    assert [e["text"] for e in first.entries] == ["e0", "e1", "e2"]
    assert len(first.entries) == RECALL_TOP_K
    second = recall(entries, query, now=NOW, seen=first.seen)
    # 前 2 条豁免已见；第 3 条（e2）跳过，顺延到 e3
    assert [e["text"] for e in second.entries] == ["e0", "e1", "e3"]
    assert "e2" not in {e["text"] for e in second.entries}


def test_missing_id_entries_get_distinct_seen_keys():
    """v2f L3：老条目缺 id 时用正文哈希去重，第 3 个名额不会被空串永久关闭。"""
    entries = []
    for index in range(4):
        entry = _entry(
            f"e{index}",
            keywords=[f"kw{index}"],
            updated_at=NOW - timedelta(days=index),
        )
        entry.pop("id")
        entries.append(entry)
    assert len({_entry_key(entry) for entry in entries}) == 4
    query = "kw0 kw1 kw2 kw3"
    first = recall(entries, query, now=NOW)
    assert [entry["text"] for entry in first.entries] == ["e0", "e1", "e2"]
    assert "" not in first.seen
    assert len(first.seen) == 3
    second = recall(entries, query, now=NOW, seen=first.seen)
    assert [entry["text"] for entry in second.entries] == ["e0", "e1", "e3"]


def test_first_turn_clears_all_seen_buckets_for_session():
    """v2f L2：无历史的轮次清掉该 session_key 前缀下的所有已见桶。"""
    session = "memory-recall-test-session"
    other = "memory-recall-test-other"
    saved = dict(_SEEN)
    try:
        _SEEN[(session, "m1")] = frozenset({"a"})
        _SEEN[(session, "m2")] = frozenset({"b"})
        _SEEN[(other, "m1")] = frozenset({"c"})
        assert augment_for_turn([], session, [], "你好") == ""
        assert [key for key in _SEEN if key[0] == session] == []
        assert (other, "m1") in _SEEN
    finally:
        _SEEN.clear()
        _SEEN.update(saved)


def test_recall_empty_hit_does_not_render_placeholder():
    entries = [_entry("周三有课", keywords=["周三"])]
    result = recall(entries, "完全无关的句子", now=NOW)
    assert result.entries == ()
    assert result.text == ""


def test_archived_topic_excluded_from_recall():
    stale = _entry(
        "旧话题",
        layer="topic",
        keywords=["bug"],
        updated_at=NOW - timedelta(days=TOPIC_ARCHIVE_DAYS + 1),
        last_hit_at=(NOW - timedelta(days=TOPIC_ARCHIVE_DAYS + 1)).isoformat(),
    )
    fresh = _entry("新话题", layer="topic", keywords=["bug"], updated_at=NOW)
    assert is_archived(stale, NOW) is True
    assert is_archived(fresh, NOW) is False
    result = recall([stale, fresh], "bug", now=NOW)
    assert [e["text"] for e in result.entries] == ["新话题"]
    assert [e["text"] for e in recall([stale], "bug", now=NOW).entries] == []


def test_archive_stale_topics_only_touches_topics():
    stale_topic = _entry(
        "话题",
        layer="topic",
        updated_at=NOW - timedelta(days=TOPIC_ARCHIVE_DAYS + 1),
    )
    old_fact = _entry(
        "事实",
        layer="fact",
        updated_at=NOW - timedelta(days=TOPIC_ARCHIVE_DAYS + 1),
    )
    assert archive_stale_topics([stale_topic, old_fact], NOW) is True
    assert stale_topic["archived"] is True
    assert "archived" not in old_fact


def test_topic_auto_archive_persists_via_product_path(tmp_path):
    """F：产品路径落盘（build_messages → augment_for_turn → note_recalled 巡检）。

    不是直接调 ``note_recalled([])`` 的假绿断言：这里从真实 prompt 构建进入，
    由 daemon 写盘线程把 ``archived=True`` 落到 memory.json，再读盘核对。
    """
    path = tmp_path / "memory.json"
    first = ChatMemory(path, clock=lambda: NOW)
    entry, _ = first.add("那个 Bug", layer="topic")
    later = NOW + timedelta(days=TOPIC_ARCHIVE_DAYS + 1)
    later_mem = ChatMemory(path, clock=lambda: later)
    payload = memory_payload(later_mem, True)

    builder = PromptBuilder()
    builder.build_messages(
        _settings(), "cat", _history(), "完全无关的句子",
        now=later, memory_payload=payload,
    )
    assert later_mem.flush_hits(5.0) is True

    raw = json.loads(path.read_text(encoding="utf-8"))
    persisted = next(e for e in raw["entries"] if e["id"] == entry["id"])
    assert persisted["archived"] is True
    assert is_archived(persisted, later) is True

    assert later_mem.set_archived(entry["id"], False) is True
    restored = later_mem.load()[0]
    assert restored["archived"] is False
    assert is_archived(restored, later) is False


def test_note_recalled_empty_ids_archive_via_internal_api(tmp_path):
    """F 补充：覆盖内部异步批量 API——空命中同样触发归档巡检并最终落盘。"""
    path = tmp_path / "memory.json"
    first = ChatMemory(path, clock=lambda: NOW)
    entry, _ = first.add("那个 Bug", layer="topic")
    later = NOW + timedelta(days=TOPIC_ARCHIVE_DAYS + 1)
    later_mem = ChatMemory(path, clock=lambda: later)
    later_mem.note_recalled([])
    assert later_mem.flush_hits(5.0) is True
    raw = json.loads(path.read_text(encoding="utf-8"))
    persisted = next(e for e in raw["entries"] if e["id"] == entry["id"])
    assert persisted["archived"] is True


def test_note_hits_reports_all_injected_layers(tmp_path):
    """发版交叉审 M1：fact/lesson 命中也计入 hits（淘汰与注入排序都依据 hits）。

    旧实现只上报 topic，fact/lesson 的 hits 永远停在 1。走真实产品路径
    （build_messages → augment_for_turn → daemon 落盘）核对。"""
    import dataclasses

    path = tmp_path / "memory.json"
    mem = ChatMemory(path, clock=lambda: NOW)
    fact, _ = mem.add("用户不吃香菜", layer="fact")
    lesson, _ = mem.add("推荐香菜前先问口味", layer="lesson")
    payload = memory_payload(mem, True)
    captured: list[list[str]] = []
    original = payload.note_hits

    def _spy(ids: list[str]) -> None:
        captured.append(list(ids))
        original(ids)

    payload = dataclasses.replace(payload, note_hits=_spy)

    builder = PromptBuilder()
    builder.build_messages(
        _settings(), "cat", _history(), "香菜过敏怎么办",
        now=NOW, memory_payload=payload,
    )
    assert captured and set(captured[-1]) == {fact["id"], lesson["id"]}
    assert mem.flush_hits(5.0) is True
    persisted = {
        e["id"]: e
        for e in json.loads(path.read_text(encoding="utf-8"))["entries"]
    }
    assert persisted[fact["id"]]["hits"] == 2
    assert persisted[lesson["id"]]["hits"] == 2
    # last_hit_at 仍只刷新 topic（分层纪律不变）
    assert "last_hit_at" not in persisted[fact["id"]]


# ---------------------------------------------------------------- 手动钉四前缀
@pytest.mark.parametrize(
    ("raw", "body", "layer"),
    [
        ("记住偏好：我不吃辣", "我不吃辣", "user"),
        ("记住：周三有课", "周三有课", "fact"),
        ("记住教训：别再把配置删了", "别再把配置删了", "lesson"),
        ("记住话题：4.4.0 发版", "4.4.0 发版", "topic"),
        ("记住偏好:我不吃辣", "我不吃辣", "user"),
    ],
)
def test_parse_remember_command_four_prefixes(raw, body, layer):
    command = parse_remember_command(raw)
    assert command == body
    assert command.layer == layer


@pytest.mark.parametrize("raw", ["请记住偏好：x", "记住我", "你好", ""])
def test_parse_remember_command_non_command_stays_none(raw):
    assert parse_remember_command(raw) is None


def test_add_uses_prefix_layer_and_extracts_keywords(tmp_path):
    mem = ChatMemory(tmp_path / "memory.json")
    command = parse_remember_command("记住教训：别再把配置删了")
    entry, created = mem.add(command)
    assert created is True
    assert entry["layer"] == "lesson"
    assert entry["keywords"]
    assert mem.load()[0]["layer"] == "lesson"


def test_add_accepts_full_remember_command_text(tmp_path):
    mem = ChatMemory(tmp_path / "memory.json")
    entry, _ = mem.add("记住偏好：别叫我全名")
    assert entry["text"] == "别叫我全名"
    assert entry["layer"] == "user"


def test_manual_pin_remember_topic_unarchives(tmp_path):
    """D：手动钉「记住话题：」是允许解除归档的入口（自动来源不允许）。"""
    path = tmp_path / "memory.json"
    # 时钟必须是真实「现在」而不是固定的 NOW：load() 的读副本按墙上时钟判
    # 「topic 超 14 天未命中即归档」，用固定 NOW 写条目，NOW 过去 14 天后
    # （2026-11-04 起）刚解除归档的条目会在读回时又被标成 archived。
    mem = ChatMemory(path, clock=lambda: datetime.now(timezone.utc))
    entry, _ = mem.add("记住话题：那个 Bug")
    assert mem.set_archived(entry["id"], True) is True
    assert mem.load()[0]["archived"] is True
    again, created = mem.add("记住话题：那个 Bug")
    assert created is False
    assert "archived" not in again
    assert mem.load()[0].get("archived") is not True


# ---------------------------------------------------------------- 首轮/次轮注入集成
def _settings():
    return ChatSettings()


def _history():
    return [
        ChatMessage("user", "第一轮"),
        ChatMessage("assistant", "回复"),
    ]


def test_injection_text_is_user_snapshot_only(tmp_path):
    path = memory_path(tmp_path, "", "shenshen")
    mem = ChatMemory(path)
    mem.add("我不吃辣", layer="user")
    mem.add("周三有课", layer="fact")
    section = injection_text(True, tmp_path, "", "shenshen")
    assert "我不吃辣" in section
    assert "周三有课" not in section


def test_prompt_first_turn_skips_recall_then_second_turn_hits(tmp_path):
    path = memory_path(tmp_path, "", "shenshen")
    mem = ChatMemory(path)
    mem.add("我不吃辣", layer="user")
    mem.add("周三有课", layer="fact")
    builder = PromptBuilder()
    now = datetime(2026, 10, 21, 12, 0).astimezone()
    payload = memory_payload(mem, True)

    first = builder.build_messages(
        _settings(), "cat", [], "周三有课吗", now=now, memory_payload=payload
    )
    assert "我不吃辣" in first[0]["content"]
    assert "周三有课" not in first[0]["content"]

    second = builder.build_messages(
        _settings(), "cat", _history(), "周三有课吗",
        now=now, memory_payload=payload,
    )
    assert "周三有课" in second[0]["content"]


# ---------------------------------------------------------------- v2b B：显式 payload
def _payload_for(tmp_path, character_id: str, text: str):
    path = memory_path(tmp_path, "", character_id)
    mem = ChatMemory(path)
    mem.add(text, layer="fact")
    return memory_payload(mem, True)


def test_payloads_do_not_cross_sessions(tmp_path):
    """B：A/B 两个 payload 交替构建，各自只拿到自己的条目（不再串会话）。"""
    builder = PromptBuilder()
    now = datetime(2026, 10, 21, 12, 0).astimezone()
    payload_a = _payload_for(tmp_path, "alpha", "周三有课")
    payload_b = _payload_for(tmp_path, "beta", "我不吃辣")

    first = builder.build_messages(
        _settings(), "cat", _history(), "周三有课吗", now=now, memory_payload=payload_a)
    second = builder.build_messages(
        _settings(), "cat", _history(), "我不吃辣吗", now=now, memory_payload=payload_b)
    third = builder.build_messages(
        _settings(), "cat", _history(), "周三有课吗", now=now, memory_payload=payload_a)

    assert "周三有课" in first[0]["content"]
    assert "我不吃辣" not in first[0]["content"]
    assert "我不吃辣" in second[0]["content"]
    assert "周三有课" not in second[0]["content"]
    assert "周三有课" in third[0]["content"]


def test_payload_exception_does_not_pollute_next_build(tmp_path, monkeypatch):
    """B：一次构建中途抛异常，不应留下任何可供下一次取走的暂存状态。"""
    import pet.chat.prompt as prompt_mod

    builder = PromptBuilder()
    now = datetime(2026, 10, 21, 12, 0).astimezone()
    payload_a = _payload_for(tmp_path, "alpha", "周三有课")
    payload_b = _payload_for(tmp_path, "beta", "我不吃辣")
    real_augment = prompt_mod.augment_for_turn
    calls = {"count": 0}

    def flaky(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("构建中途失败")
        return real_augment(*args, **kwargs)

    monkeypatch.setattr(prompt_mod, "augment_for_turn", flaky)
    with pytest.raises(RuntimeError):
        builder.build_messages(
            _settings(), "cat", _history(), "周三有课吗", now=now, memory_payload=payload_a)
    recovered = builder.build_messages(
        _settings(), "cat", _history(), "我不吃辣吗", now=now, memory_payload=payload_b)
    assert "我不吃辣" in recovered[0]["content"]
    assert "周三有课" not in recovered[0]["content"]


def test_payload_wins_over_memory_text_without_duplicate_injection(tmp_path):
    """B：memory_text 与 memory_payload 同时给时以 payload 为准，不重复注入。"""
    builder = PromptBuilder()
    now = datetime(2026, 10, 21, 12, 0).astimezone()
    payload = _payload_for(tmp_path, "alpha", "周三有课")
    messages = builder.build_messages(
        _settings(), "cat", _history(), "周三有课吗", now=now,
        memory_text="【关于用户的记忆】\n- 不该出现", memory_payload=payload)
    system = messages[0]["content"]
    assert "不该出现" not in system
    assert system.count("周三有课") == 1


def test_build_messages_without_payload_is_byte_identical():
    """B 硬约束：未传 memory_payload 时输出与改动前逐字节一致（固定输入）。"""
    now = datetime(2026, 10, 21, 12, 0).astimezone()
    history = [
        ChatMessage("user", "上一条", created_at=(now - timedelta(minutes=5)).isoformat(),
                    message_id="m1"),
        ChatMessage("assistant", "回复", created_at=(now - timedelta(minutes=4, seconds=30)).isoformat(),
                    message_id="m2"),
    ]
    messages = PromptBuilder().build_messages(
        _settings(), "cat", history, "你好", now=now)
    assert messages == [
        {
            "role": "system",
            "content": (
                "你是一只可爱的桌面宠物，请用自然、友善的中文和用户交流。\n\n"
                "【时间信息】当前时间：2026-10-21 周三 12:00。对话记录中用户消息开头的"
                "「[日期 时间]」是系统自动附加的发送时间，用来帮助你感知时间的流逝"
                "（例如隔了多久没聊、现在是白天还是深夜、日期是否变了），回复时自然地"
                "体现即可；不要在回复中复述或模仿这种时间标记。"
            ),
        },
        {"role": "user", "content": "[2026-10-21 周三 11:55] 上一条"},
        {"role": "assistant", "content": "回复"},
        {"role": "user", "content": "你好"},
    ]


# ---------------------------------------------------------------- v2b C：异步命中写盘
def test_topic_hit_build_messages_fast_and_persists_async(tmp_path):
    """C：GUI 线程构建 <5ms；命中 last_hit_at 最终由 daemon 线程落盘。"""
    path = tmp_path / "memory.json"
    mem = ChatMemory(path)
    topic, _ = mem.add("那个 Bug", layer="topic")
    payload = memory_payload(mem, True)
    builder = PromptBuilder()
    now = datetime(2026, 10, 21, 12, 0).astimezone()

    started = time.perf_counter()
    messages = builder.build_messages(
        _settings(), "cat", _history(), "那个 Bug 怎么样了",
        now=now, memory_payload=payload)
    elapsed = time.perf_counter() - started
    assert "那个 Bug" in messages[0]["content"]
    assert elapsed < 0.005, f"GUI 线程构建耗时 {elapsed * 1000:.2f}ms，超过 5ms"

    assert mem.flush_hits(5.0) is True
    raw = json.loads(path.read_text(encoding="utf-8"))
    persisted = next(e for e in raw["entries"] if e["id"] == topic["id"])
    assert datetime.fromisoformat(persisted["last_hit_at"]) >= datetime.fromisoformat(
        persisted["created_at"])


def test_note_recalled_cache_visible_before_disk_write(tmp_path):
    """C：命中先更新进程内缓存（load 立即可见），随后由 daemon 线程落盘。"""
    path = tmp_path / "memory.json"
    mem = ChatMemory(path, clock=lambda: NOW)
    topic, _ = mem.add("那个 Bug", layer="topic")
    later = NOW + timedelta(days=3)
    mem.note_recalled([topic["id"]], now=later)
    assert mem.load()[0]["last_hit_at"] == later.isoformat()
    assert mem.flush_hits(5.0) is True
    raw = json.loads(path.read_text(encoding="utf-8"))
    persisted = next(e for e in raw["entries"] if e["id"] == topic["id"])
    assert persisted["last_hit_at"] == later.isoformat()
