from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from petcore.account.service import BalanceResult
from petcore.island import segments as isl
from petcore.island.segments import SegmentSnapshot
from petcore.ui.island import DynamicIsland


class _Config:
    def __init__(self):
        self.data = {
            "island_enabled": True,
            "island_metrics": ["cpu", "gpu", "fps"],
            "island_show_messages": True,
            "island_show_name": True,
            "island_show_status": True,
            "island_info_mode": "full",
            "island_custom_text": "",
            "island_style": "dark",
            "island_opacity": 1.0,
            "island_accent": "blue",
            "island_x": None,
            "island_y": None,
        }
        self.saved = 0

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        self.saved += 1


def _app():
    return QApplication.instance() or QApplication([])


def test_island_card_contains_balance_messages_extensions_and_unavailable_probes():
    _app()
    island = DynamicIsland(_Config(), character_name="测试宠物")
    island.show_balance(BalanceResult("svc", "3.00", "", ""))
    island.add_message("最近一条消息")
    island.register_segment(SegmentSnapshot("third-party", "扩展", "在线", "在线"))
    rows = island._card_rows()
    labels = [row[0] for row in rows]
    assert "余额" in labels
    assert "最近消息" in labels
    assert "扩展" in labels
    # 主分支口径：不支持/未接入的段不占位，原因写在卡片一行里
    assert any(row[2] == "该指标不可用：当前环境未接入可用 GPU/FPS 探针" for row in rows)
    assert not any(seg.seg_id in (isl.GPU, isl.FPS) for seg in island._row)
    island.clear_segments()
    island.deleteLater()


def test_island_signals_expose_balance_and_settings_actions():
    _app()
    island = DynamicIsland(_Config())
    balance = []
    settings = []
    island.balance_requested.connect(lambda: balance.append(True))
    island.settings_requested.connect(lambda: settings.append(True))
    island.card._refresh_button.click()
    island.card._settings_button.click()
    assert balance == [True]
    assert settings == [True]
    island.deleteLater()


def test_island_card_toggle_button_defaults_on_and_follows_pet_visibility():
    """卡片「隐藏/显示桌宠」：默认加、文案跟随显隐、点击只发信号（宿主接线）。"""
    _app()
    island = DynamicIsland(_Config())
    fired = []
    island.toggle_pet_requested.connect(lambda: fired.append(True))
    button = island.card._toggle_button
    assert not button.isHidden()
    assert button.text() == "隐藏桌宠"
    island.set_pet_visible(False)
    assert button.text() == "显示桌宠"
    button.click()
    assert fired == [True]
    island.deleteLater()


def test_island_card_toggle_button_respects_the_setting():
    """设置关掉后按钮真的收起（不是只写配置不生效的活开关）。"""
    _app()
    config = _Config()
    config.data["island_show_toggle_button"] = False
    island = DynamicIsland(config)
    island._rebuild_card()
    assert island.card._toggle_button.isHidden()
    island.deleteLater()


def test_island_accepts_optional_gpu_probe_snapshot_without_starting_a_sampler():
    _app()
    island = DynamicIsland(_Config())
    island.set_probe_snapshot(
        SegmentSnapshot(
            isl.GPU,
            label="GPU",
            text="73",
            max_text="100",
            detail={"unit": "%", "source": "probe"},
        )
    )
    gpu = next(segment for segment in island._row if segment.seg_id == isl.GPU)
    assert gpu.text == "73"
    assert gpu.box_text == "100"
    assert gpu.unit == "%"
    island.clear_probe_snapshot(isl.GPU)
    assert not any(segment.seg_id == isl.GPU for segment in island._row)
    rows = {row[0]: row for row in island._card_rows()}
    assert "未接入" in rows["GPU"][2]
    island.deleteLater()


def test_island_unsupported_probe_takes_no_segment_but_says_why_in_the_card():
    from petcore.island import segments as isl_segments

    _app()
    island = DynamicIsland(_Config())
    island.set_probe_snapshot(
        isl_segments.SegmentSnapshot(
            isl.FPS,
            label="FPS",
            text="--",
            max_text="9999",
            state=isl.STATE_UNSUPPORTED,
            detail={"error_reason": "PresentMon 未随包提供"},
        )
    )
    assert not any(segment.seg_id == isl.FPS for segment in island._row)
    rows = {row[0]: row for row in island._card_rows()}
    assert "PresentMon 未随包提供" in rows["帧率"][2]
    island.deleteLater()


def test_island_avoidance_does_not_overwrite_the_user_placement_and_returns_home():
    from PySide6.QtCore import QPoint, QRect

    from petcore.ui import island as island_ui

    _app()
    config = _Config()
    island = DynamicIsland(config)
    island.show()
    island.move(QPoint(400, 300))
    home = QPoint(island.pos())
    obstacle = QRect(home.x() - 10, home.y() - 10, island.width() + 40, island.height() + 40)

    saved_before = config.saved
    assert island.avoid_overlaps([obstacle]) is True
    assert island.pos() != home
    assert config.saved == saved_before, "自动避让不许写 island_x/island_y"
    assert config.data["island_x"] is None

    for _ in range(island_ui.AVOID_RESTORE_TICKS - 1):
        island.avoid_overlaps([])
    assert island.pos() != home
    island.avoid_overlaps([])
    assert island.pos() == home
    island.hide()
    island.deleteLater()
