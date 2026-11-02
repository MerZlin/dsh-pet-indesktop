from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from petcore.config import Config

PACK_DIR = (
    Path(__file__).resolve().parents[1]
    / "petcore"
    / "builtin"
    / "features"
    / "fun.self-talk"
)
MOD_DIR = PACK_DIR / "host"
POOL_PATH = PACK_DIR / "daily_lines.json"


def _load(name: str):
    path = MOD_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"self_talk_{name}_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


daily = _load("daily")
daily_pool = _load("daily_pool")
factory = _load("factory")


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


# ------------------------------------------------------------------ 随包台词池


def test_builtin_pool_is_large_unique_and_clean() -> None:
    payload = json.loads(POOL_PATH.read_text(encoding="utf-8"))
    assert payload["schema"] == 1
    assert payload["generated"]
    lines = payload["lines"]
    assert isinstance(lines, list)
    assert len(lines) >= 1000, "内置池至少 1000 句"
    assert len(set(lines)) == len(lines), "内置池不能有重复句"
    for line in lines:
        assert isinstance(line, str)
        assert line
        assert line == line.strip(), "内置池不能有首尾空白"
        assert len(line) <= 80, f"台词过长：{line}"
        assert len(line) >= 6, f"台词过短：{line}"
    # 「避免机械前缀」：开头三个字的组合要足够散。
    assert len({line[:3] for line in lines}) >= 100


def test_pool_loader_reads_the_shipped_file() -> None:
    lines = daily_pool.load_pool()
    assert len(lines) >= 1000
    assert lines == daily_pool.load_pool(PACK_DIR)
    assert daily_pool.load_pool(PACK_DIR.parent / "missing.json") == ()


def test_daily_line_is_stable_per_day_and_changes_across_days() -> None:
    texts = [f"第{i}句" for i in range(12)]
    first = daily.daily_line(texts, dt.date(2026, 10, 31), salt=0)
    assert daily.daily_line(texts, dt.date(2026, 10, 31), salt=0) == first
    values = {
        daily.daily_line(texts, dt.date(2026, 10, 31) + dt.timedelta(days=i), salt=0)
        for i in range(10)
    }
    assert len(values) > 1
    assert daily.daily_line([], dt.date(2026, 10, 31)) == ""


def test_daily_set_is_stable_per_day_and_slot() -> None:
    pool = tuple(f"第{i}句测试台词" for i in range(40))
    day = dt.date(2026, 10, 31)
    first = daily_pool.daily_set(pool, day, count=12, salt=3)
    assert first == daily_pool.daily_set(pool, day, count=12, salt=3)
    assert len(first) == 12
    assert len(set(first)) == 12, "同一天的一组台词不能重复"
    assert daily_pool.daily_set(pool, day + dt.timedelta(days=1), count=12, salt=3) != first
    assert daily_pool.daily_set(pool, day, count=12, salt=4) != first
    # 缩小句数就是同一组的前缀，不会换一批句子。
    assert daily_pool.daily_set(pool, day, count=3, salt=3) == first[:3]
    # 池子比句数少时整池都用；空池返回空。
    assert daily_pool.daily_set(pool[:4], day, count=12, salt=3) == tuple(
        daily_pool.daily_set(pool[:4], day, count=4, salt=3)
    )
    assert daily_pool.daily_set((), day) == ()


def test_daily_count_is_clamped() -> None:
    assert daily_pool.clamp_count(0) == 1
    assert daily_pool.clamp_count(99) == 24
    assert daily_pool.clamp_count("7") == 7
    assert daily_pool.clamp_count(None) == 12
    assert daily_pool.clamp_count("oops") == 12


def test_playback_slot_maps_index_into_cycles() -> None:
    assert daily_pool.playback_slot(0, 3, 5) is None
    assert daily_pool.playback_slot(2, 3, 5) is None
    assert daily_pool.playback_slot(3, 3, 5) == (0, 0)
    assert daily_pool.playback_slot(7, 3, 5) == (0, 4)
    assert daily_pool.playback_slot(8, 3, 5) == (1, 0)
    assert daily_pool.playback_slot(3, 3, 0) is None
    assert daily_pool.playback_slot("oops", 3, 5) is None


def test_playback_pool_merges_and_dedupes() -> None:
    assert daily_pool.merge_pool(["甲", "乙"], ["乙", "丙"]) == ("甲", "乙", "丙")
    assert daily_pool.merge_pool([], ["甲", "  ", "甲"]) == ("甲",)
    assert daily_pool.merge_pool([], []) == ()


def test_playback_order_is_stable_per_cycle_and_avoids_seam() -> None:
    daily_lines = ["甲", "乙", "丙"]
    extras = ["丁", "戊"]
    day = dt.date(2026, 11, 3).toordinal()
    first = daily_pool.playback_order(
        daily_lines, extras, day, cycle=0, salt=0, leading="丙"
    )
    assert first == daily_pool.playback_order(
        daily_lines, extras, day, cycle=0, salt=0, leading="丙"
    )
    assert len(first) == 5
    assert set(first) == {"甲", "乙", "丙", "丁", "戊"}
    second = daily_pool.playback_order(
        daily_lines, extras, day, cycle=1, salt=0, leading="丙"
    )
    assert set(second) == set(first)
    assert second != first
    assert second[0] != first[-1], "轮次接缝不能连续重复同一句"
    assert daily_pool.playback_order(
        daily_lines, extras, day, cycle=1, salt=1, leading="丙"
    ) != second


def test_playback_line_walks_the_pool_then_cycles() -> None:
    daily_lines = ["甲", "乙"]
    extras = ["丙", "丁"]
    day = dt.date(2026, 11, 3).toordinal()
    first = [
        daily_pool.playback_line(daily_lines, extras, day, index, salt=0)
        for index in range(2, 6)
    ]
    assert first == list(
        daily_pool.playback_order(
            daily_lines, extras, day, cycle=0, salt=0, leading="乙"
        )
    )
    second = [
        daily_pool.playback_line(daily_lines, extras, day, index, salt=0)
        for index in range(6, 10)
    ]
    assert second == list(
        daily_pool.playback_order(
            daily_lines, extras, day, cycle=1, salt=0, leading="乙"
        )
    )
    assert daily_pool.playback_line(daily_lines, extras, day, 0, salt=0) == ""


def test_daily_sets_change_day_to_day_with_the_shipped_pool() -> None:
    pool = daily_pool.load_pool()
    first = dt.date(2026, 10, 31)
    sets = {
        daily_pool.daily_set(pool, first + dt.timedelta(days=offset), count=12, salt=0)
        for offset in range(7)
    }
    assert len(sets) == 7


# ---------------------------------------------------------------------- 状态


def test_split_sequence_handles_separators_and_cap() -> None:
    assert daily.split_sequence("一|二。三！四\n五？六") == ("一", "二", "三", "四", "五")
    assert daily.split_sequence("") == ()


def test_daily_state_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "daily.json"
    state = daily.load_state(path)
    assert state == {}
    daily.mark_said(state, 2, dt.date(2026, 10, 31), 4)
    daily.save_state(path, state)
    loaded = daily.load_state(path)
    assert loaded["2"] == {"date": "2026-10-31", "index": 4}
    assert daily.daily_index(loaded, 2, dt.date(2026, 10, 31)) == 4
    assert daily.daily_index(loaded, 2, dt.date(2026, 11, 1)) == 0
    assert daily.said_today(loaded, 2, dt.date(2026, 10, 31))
    assert not daily.said_today(loaded, 2, dt.date(2026, 11, 1))
    path.write_text("{bad json", encoding="utf-8")
    assert daily.load_state(path) == {}


def test_legacy_date_only_state_reads_as_nothing_consumed(tmp_path: Path) -> None:
    path = tmp_path / "daily.json"
    day = dt.date(2026, 10, 31)
    path.write_text(
        json.dumps({"0": {"date": day.isoformat()}}, ensure_ascii=False),
        encoding="utf-8",
    )
    state = daily.load_state(path)
    assert daily.daily_index(state, 0, day) == 0
    assert daily.said_today(state, 0, day)
    # 下一次写入自动升级成新结构。
    daily.mark_said(state, 0, day, 1)
    assert state["0"] == {"date": day.isoformat(), "index": 1}


# ------------------------------------------------------------------- 运行时


class _Config:
    def __init__(self, **values) -> None:
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


class _Presentation:
    def __init__(self, ok: bool = True) -> None:
        self.said: list[str] = []
        self.sequences: list[list[tuple[str, str, int]]] = []
        self.ok = ok

    def say(self, text, **kwargs):
        self.said.append(str(text))
        return self.ok

    def say_sequence(self, items, **kwargs):
        if not self.ok:
            return False
        self.sequences.append(list(items))
        return True


def _runtime(tmp_path: Path):
    runtime = object.__new__(factory.SelfTalkRuntime)
    runtime.context = SimpleNamespace(
        ports=SimpleNamespace(
            storage=SimpleNamespace(data_dir=lambda owner: tmp_path / owner)
        )
    )
    runtime._speak_text = lambda *args, **kwargs: None
    return runtime


def _daily_config(**overrides) -> _Config:
    values = dict(
        self_talk_daily_enabled=True,
        self_talk_daily_texts=["每日甲", "每日乙", "每日丙"],
        self_talk_daily_count=2,
        self_talk_daily_sequence=False,
        self_talk_duration=5,
    )
    values.update(overrides)
    return _Config(**values)


def test_daily_lines_advance_one_per_tick_then_yield_to_playback(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    config = _daily_config()
    presentation = _Presentation()
    day = dt.date(2026, 10, 31)
    expected = daily_pool.daily_set(
        ["每日甲", "每日乙", "每日丙"], day, count=2, salt=0
    )

    assert runtime._speak_daily(config, presentation, 0, day=day) is True
    assert runtime._speak_daily(config, presentation, 0, day=day) is True
    assert presentation.said == list(expected)
    # 当天的 2 句说完，_speak_daily 返回 False；_speak 接下来走播放池，
    # 不再静默回落到纯随机候选。
    assert runtime._speak_daily(config, presentation, 0, day=day) is False
    assert len(presentation.said) == 2
    state = daily.load_state(tmp_path / "fun.self-talk" / "daily.json")
    assert state["0"] == {"date": day.isoformat(), "index": 2}


def test_daily_index_only_advances_after_a_visible_bubble(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    config = _daily_config()
    day = dt.date(2026, 10, 31)
    path = tmp_path / "fun.self-talk" / "daily.json"

    assert runtime._speak_daily(config, _Presentation(ok=False), 0, day=day) is False
    assert not path.exists()
    assert daily.daily_index(daily.load_state(path), 0, day) == 0

    presentation = _Presentation()
    assert runtime._speak_daily(config, presentation, 0, day=day) is True
    assert daily.daily_index(daily.load_state(path), 0, day) == 1


def test_each_slot_has_its_own_progress_and_set(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    config = _daily_config(self_talk_daily_count=3)
    presentation = _Presentation()
    day = dt.date(2026, 10, 31)

    assert runtime._speak_daily(config, presentation, 0, day=day) is True
    assert runtime._speak_daily(config, presentation, 1, day=day) is True
    state = daily.load_state(tmp_path / "fun.self-talk" / "daily.json")
    assert state["0"]["index"] == 1
    assert state["1"]["index"] == 1
    assert presentation.said[0] == daily_pool.daily_set(
        ["每日甲", "每日乙", "每日丙"], day, count=3, salt=0
    )[0]
    assert presentation.said[1] == daily_pool.daily_set(
        ["每日甲", "每日乙", "每日丙"], day, count=3, salt=1
    )[0]


def test_playback_line_starts_after_the_daily_set(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    config = _daily_config(
        self_talk_daily_count=2,
        self_talk_texts=["候选甲", "候选乙"],
    )
    presentation = _Presentation()
    day = dt.date(2026, 10, 31)

    assert runtime._playback_line(config, 0, day=day) is None
    assert runtime._speak_daily(config, presentation, 0, day=day) is True
    assert runtime._speak_daily(config, presentation, 0, day=day) is True

    first = runtime._playback_line(config, 0, day=day)
    assert first is not None
    text, index = first
    assert index == 2
    daily_lines = daily_pool.daily_set(
        ["每日甲", "每日乙", "每日丙"], day, count=2, salt=0
    )
    order = daily_pool.playback_order(
        daily_lines,
        ["候选甲", "候选乙"],
        day.toordinal(),
        cycle=0,
        salt=0,
        leading=daily_lines[-1],
    )
    assert text == order[0]

    runtime._mark_daily(0, index + 1, day)
    assert runtime._playback_line(config, 0, day=day) == (order[1], 3)


def test_playback_line_is_off_when_daily_is_disabled(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    config = _daily_config(
        self_talk_daily_enabled=False,
        self_talk_daily_count=1,
        self_talk_texts=["候选甲"],
    )
    day = dt.date(2026, 10, 31)

    assert runtime._playback_line(config, 0, day=day) is None


def test_next_day_restarts_the_sets(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    config = _daily_config(self_talk_daily_count=2)
    presentation = _Presentation()
    first = dt.date(2026, 10, 31)
    for _ in range(2):
        assert runtime._speak_daily(config, presentation, 0, day=first) is True
    assert runtime._speak_daily(config, presentation, 0, day=first) is False
    # 换天：游标归零，重新开始这一组。
    assert runtime._speak_daily(config, presentation, 0, day=first + dt.timedelta(days=1)) is True
    state = daily.load_state(tmp_path / "fun.self-talk" / "daily.json")
    assert state["0"] == {
        "date": (first + dt.timedelta(days=1)).isoformat(),
        "index": 1,
    }


def test_daily_uses_the_builtin_pool_when_custom_texts_are_empty(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    config = _daily_config(self_talk_daily_texts=[], self_talk_daily_count=4)
    presentation = _Presentation()
    day = dt.date(2026, 10, 31)

    assert runtime._speak_daily(config, presentation, 0, day=day) is True
    expected = daily_pool.daily_set(
        daily_pool.load_pool(), day, count=4, salt=0
    )[0]
    assert presentation.said == [expected]


def test_daily_disabled_skips_the_feature(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    config = _daily_config(self_talk_daily_enabled=False)
    assert runtime._speak_daily(config, _Presentation(), 0) is False


def test_daily_sequence_uses_multi_bubble_channel(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    config = _daily_config(
        self_talk_daily_texts=["第一段|第二段|第三段"],
        self_talk_daily_count=1,
        self_talk_daily_sequence=True,
    )
    presentation = _Presentation()
    assert runtime._speak_daily(config, presentation, 0) is True
    assert len(presentation.sequences) == 1
    assert [item[0] for item in presentation.sequences[0]] == [
        "第一段",
        "第二段",
        "第三段",
    ]
    assert presentation.said == []


# ------------------------------------------------------------------ 设置页


def _host(tmp_path: Path, slot: int = 0, presentation=None):
    values = dict(
        config=Config(tmp_path),
        window=SimpleNamespace(slot=slot),
    )
    if presentation is not None:
        values["presentation_port"] = lambda: presentation
    return SimpleNamespace(**values)


def _list_items(page) -> list[str]:
    view = page.daily_preview_list
    return [view.item(row).text() for row in range(view.count())]


def test_settings_page_previews_the_numbered_set(tmp_path: Path) -> None:
    _app()
    page = factory.SelfTalkPage(_host(tmp_path))
    try:
        assert page.daily_count.value() == 12
        expected = daily_pool.daily_set(
            daily_pool.load_pool(), dt.date.today(), count=12, salt=0
        )
        items = _list_items(page)
        assert items == [f"{index + 1}. {line}" for index, line in enumerate(expected)]
        assert "今天" in page.daily_preview_title.text()
        assert "12 句" in page.daily_preview_title.text()
    finally:
        page.close()


def test_settings_page_preview_follows_the_target_slot(tmp_path: Path) -> None:
    _app()
    main = factory.SelfTalkPage(_host(tmp_path, slot=0))
    child = factory.SelfTalkPage(_host(tmp_path, slot=2))
    try:
        assert _list_items(main) != _list_items(child)
        expected = daily_pool.daily_set(
            daily_pool.load_pool(), dt.date.today(), count=12, salt=2
        )
        assert _list_items(child)[0] == f"1. {expected[0]}"
    finally:
        main.close()
        child.close()


def test_settings_count_spin_rerenders_and_persists(tmp_path: Path) -> None:
    _app()
    host = _host(tmp_path)
    page = factory.SelfTalkPage(host)
    try:
        page.daily_count.setValue(5)
        assert len(_list_items(page)) == 5
        assert host.config.get("self_talk_daily_count") == 5
        expected = daily_pool.daily_set(
            daily_pool.load_pool(), dt.date.today(), count=5, salt=0
        )
        assert _list_items(page) == [f"{i + 1}. {line}" for i, line in enumerate(expected)]
    finally:
        page.close()


def test_tomorrow_button_rerenders_without_touching_the_runtime(
    tmp_path: Path,
) -> None:
    _app()
    host = _host(tmp_path)
    runtime_calls: list[str] = []
    feature = SimpleNamespace(
        runtime=SimpleNamespace(
            stop=lambda: runtime_calls.append("stop"),
            start=lambda: runtime_calls.append("start"),
        )
    )
    host.features = SimpleNamespace(features={"fun.self-talk": feature})
    page = factory.SelfTalkPage(host)
    try:
        page.tomorrow_button.click()
        tomorrow = dt.date.today() + dt.timedelta(days=1)
        expected = daily_pool.daily_set(
            daily_pool.load_pool(), tomorrow, count=12, salt=0
        )
        assert _list_items(page)[0] == f"1. {expected[0]}"
        assert "明天" in page.daily_preview_title.text()
        assert page.tomorrow_button.text() == "回到今天"
        assert runtime_calls == [], "预览只是渲染，不许重启运行时"
        page.tomorrow_button.click()
        assert "今天" in page.daily_preview_title.text()
        assert page.tomorrow_button.text() == "明天再看"
        assert runtime_calls == []
    finally:
        page.close()


def test_custom_override_section_is_collapsed_and_drives_the_preview(
    tmp_path: Path,
) -> None:
    _app()
    host = _host(tmp_path)
    page = factory.SelfTalkPage(host)
    try:
        assert page.custom_section.is_expanded() is False
        page.custom_section.set_expanded(True)
        assert page.custom_section.is_expanded() is True
        page.daily_texts.setPlainText("甲\n乙")
        page.daily_count.setValue(3)
        assert host.config.get("self_talk_daily_texts") == ["甲", "乙"]
        expected = daily_pool.daily_set(
            ["甲", "乙"], dt.date.today(), count=3, salt=0
        )
        assert _list_items(page) == [
            f"{index + 1}. {line}" for index, line in enumerate(expected)
        ]
        # 清空即回到内置池。
        page.daily_texts.setPlainText("")
        assert len(_list_items(page)) == 3
    finally:
        page.close()


def test_restore_button_clears_the_custom_override(tmp_path: Path) -> None:
    _app()
    host = _host(tmp_path)
    page = factory.SelfTalkPage(host)
    try:
        page.daily_texts.setPlainText("甲")
        page.daily_count.setValue(4)
        page.restore_daily_button.click()
        assert host.config.get("self_talk_daily_texts") == []
        assert host.config.get("self_talk_daily_count") == 12
        assert page.daily_texts.toPlainText() == ""
        expected = daily_pool.daily_set(
            daily_pool.load_pool(), dt.date.today(), count=12, salt=0
        )
        assert _list_items(page)[0] == f"1. {expected[0]}"
    finally:
        page.close()


def test_settings_discard_restores_count_and_texts(tmp_path: Path) -> None:
    _app()
    host = _host(tmp_path)
    page = factory.SelfTalkPage(host)
    try:
        page.daily_count.setValue(3)
        page.daily_enabled.setChecked(False)
        page.daily_texts.setPlainText("甲")
        page._on_changed()
        page.discard_changes()
        assert host.config.get("self_talk_daily_count") == 12
        assert host.config.get("self_talk_daily_enabled") is True
        assert host.config.get("self_talk_daily_texts") == []
        assert page.daily_count.value() == 12
        assert page.daily_enabled.isChecked() is True
    finally:
        page.close()


def test_test_button_uses_the_presentation_port(tmp_path: Path) -> None:
    _app()

    class _Preview:
        def __init__(self) -> None:
            self.calls: list[tuple[list, bool]] = []

        def say_sequence(self, items, *, force=False, on_show=None):
            self.calls.append(([tuple(item) for item in items], bool(force)))
            return True

    preview = _Preview()
    page = factory.SelfTalkPage(_host(tmp_path, presentation=preview))
    try:
        assert page.preview_button.text() == "试听连说"
        page.preview_button.click()
        assert preview.calls, "试听连说必须走真实的连说通道"
        items, force = preview.calls[0]
        assert force is True
        expected = daily_pool.daily_set(
            daily_pool.load_pool(), dt.date.today(), count=12, salt=0
        )
        assert [text for text, _sub, _duration in items] == list(expected[:5])
    finally:
        page.close()


def test_settings_daily_rows_are_searchable(tmp_path: Path) -> None:
    from petcore.ui.settings.base import page_search_texts, reveal_text

    _app()
    page = factory.SelfTalkPage(_host(tmp_path))
    try:
        texts = page_search_texts(page)
        for needle in ("每日一句", "今日台词", "连说", "自定义台词", "句数"):
            assert any(needle in text for text in texts), needle
        assert reveal_text(page, "自定义台词") is True
        _app().processEvents()
        assert page.custom_section.is_expanded() is True
    finally:
        page.close()


def test_preview_shows_progress_from_the_state_file(tmp_path: Path) -> None:
    from petcore import paths

    _app()
    host = _host(tmp_path, slot=1)
    state_path = paths.feature_data_dir(tmp_path, "fun.self-talk") / "daily.json"
    daily.save_state(state_path, daily.mark_said({}, 1, dt.date.today(), 3))
    page = factory.SelfTalkPage(host)
    try:
        assert "下一句第 4 句" in page.daily_preview_title.text()
    finally:
        page.close()

    daily.save_state(state_path, daily.mark_said({}, 1, dt.date.today(), 24))
    page = factory.SelfTalkPage(host)
    try:
        assert "都说过了" in page.daily_preview_title.text()
        # 明天是全新的一组：预览不再标记进度。
        page.tomorrow_button.click()
        assert "下一句" not in page.daily_preview_title.text()
    finally:
        page.close()


def test_settings_page_daily_rows_write_and_discard(tmp_path: Path) -> None:
    from petcore.config import Config

    _app()
    host = SimpleNamespace(config=Config(tmp_path))
    page = factory.SelfTalkPage(host)
    try:
        assert page.daily_enabled.isChecked() is True
        assert "今天" in page.daily_preview_title.text()
        page.daily_enabled.setChecked(False)
        assert host.config.get("self_talk_daily_enabled") is False
        page.daily_texts.setPlainText("甲\n乙")
        page.daily_sequence.setChecked(True)
        page._on_changed()
        assert host.config.get("self_talk_daily_texts") == ["甲", "乙"]
        assert host.config.get("self_talk_daily_sequence") is True
        page.discard_changes()
        assert host.config.get("self_talk_daily_enabled") is True
        assert host.config.get("self_talk_daily_sequence") is False
    finally:
        page.close()
