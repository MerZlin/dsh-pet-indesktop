from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

MOD_DIR = Path(__file__).resolve().parents[1] / "petcore" / "builtin" / "features" / "fun.self-talk" / "host"


def _load(name: str):
    path = MOD_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"self_talk_{name}_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


daily = _load("daily")
factory = _load("factory")


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


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


def test_split_sequence_handles_separators_and_cap() -> None:
    assert daily.split_sequence("一|二。三！四\n五？六") == ("一", "二", "三", "四", "五")
    assert daily.split_sequence("") == ()


def test_daily_state_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "daily.json"
    state = daily.load_state(path)
    assert state == {}
    daily.mark_said(state, 2, dt.date(2026, 10, 31))
    daily.save_state(path, state)
    loaded = daily.load_state(path)
    assert daily.said_today(loaded, 2, dt.date(2026, 10, 31))
    assert not daily.said_today(loaded, 2, dt.date(2026, 11, 1))
    path.write_text("{bad json", encoding="utf-8")
    assert daily.load_state(path) == {}


class _Config:
    def __init__(self, **values) -> None:
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


class _Presentation:
    def __init__(self) -> None:
        self.said: list[str] = []
        self.sequences: list[list[tuple[str, str, int]]] = []

    def say(self, text, **kwargs):
        self.said.append(str(text))
        return True

    def say_sequence(self, items, **kwargs):
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


def test_daily_line_consumed_once_per_day_per_slot(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    config = _Config(
        self_talk_daily_enabled=True,
        self_talk_daily_texts=["每日甲", "每日乙"],
        self_talk_daily_sequence=False,
        self_talk_duration=5,
    )
    presentation = _Presentation()
    assert runtime._speak_daily(config, presentation, 0) is True
    assert len(presentation.said) == 1
    assert runtime._speak_daily(config, presentation, 0) is False
    assert len(presentation.said) == 1
    # 另一只宠有自己的每日句。
    assert runtime._speak_daily(config, presentation, 1) is True
    assert len(presentation.said) == 2


def test_daily_sequence_uses_multi_bubble_channel(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    config = _Config(
        self_talk_daily_enabled=True,
        self_talk_daily_texts=["第一段|第二段|第三段"],
        self_talk_daily_sequence=True,
        self_talk_duration=3,
    )
    presentation = _Presentation()
    assert runtime._speak_daily(config, presentation, 0) is True
    assert len(presentation.sequences) == 1
    assert [item[0] for item in presentation.sequences[0]] == [
        "第一段",
        "第二段",
        "第三段",
    ]


def test_settings_page_daily_rows_write_and_discard(tmp_path: Path) -> None:
    from petcore.config import Config

    _app()
    host = SimpleNamespace(config=Config(tmp_path))
    page = factory.SelfTalkPage(host)
    try:
        assert page.daily_enabled.isChecked() is True
        assert "今天：" in page.daily_preview.text()
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
