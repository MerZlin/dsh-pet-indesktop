# -*- coding: utf-8 -*-
"""自言自语「配图概率」：图片与文本**不再等权随机**。

背景（真实数据）：某配置 24 张配图 + 5 句文本，等权随机的出图概率 = 24/29 ≈ 82.8%，
点击桌宠几乎总是弹图、文本基本轮不到。现在先掷一次骰子决定"这次出图还是出文本"，
再在对应池里等权选一条。
"""

from pet import window_alerts as alerts

TEXTS = ["好女孩……", "再陪你一会儿。"]
IMAGES = ["a.jpg", "b.png"]


def test_chance_zero_always_picks_text():
    for _ in range(40):
        kind, value = alerts.pick_self_talk_choice(TEXTS, IMAGES, 0)
        assert kind == "text" and value in TEXTS


def test_chance_hundred_always_picks_image():
    for _ in range(40):
        kind, value = alerts.pick_self_talk_choice(TEXTS, IMAGES, 100)
        assert kind == "image" and value in IMAGES


def test_chance_is_roughly_respected():
    """统计口径：1000 次抽样里出图比例应落在 25%±8%（宽预算，避免抖动）。"""
    picks = [alerts.pick_self_talk_choice(TEXTS, IMAGES, 25)[0] for _ in range(1000)]
    ratio = picks.count("image") / len(picks)
    assert 0.17 <= ratio <= 0.33, f"实测出图比例 {ratio:.3f}"


def test_single_sided_pools_never_return_nothing():
    """只有图没文本时永远出图、只有文本没图时永远出文本——不掷骰子。

    否则会出现"这次既不显示文本也不显示图片"，表现就是点了没反应。
    """
    assert alerts.pick_self_talk_choice([], IMAGES, 0)[0] == "image"
    assert alerts.pick_self_talk_choice(TEXTS, [], 100)[0] == "text"


def test_blank_texts_and_empty_pools_return_none():
    assert alerts.pick_self_talk_choice([], [], 50) is None
    assert alerts.pick_self_talk_choice(["", "   "], [], 50) is None


def test_out_of_range_chance_is_clamped():
    assert alerts.pick_self_talk_choice(TEXTS, IMAGES, -5)[0] == "text"
    assert alerts.pick_self_talk_choice(TEXTS, IMAGES, 250)[0] == "image"


def test_image_chance_reads_config_with_safe_fallback():
    class _Cfg:
        def __init__(self, value=None):
            self._value = value

        def get(self, key, default=None):
            return default if self._value is None else self._value

    class _Host:
        def __init__(self, cfg):
            self.cfg = cfg

        def get(self, *_args, **_kwargs):  # 防呆：误用 host.get 也能被这条用例抓住
            raise AssertionError("应通过 host.cfg.get 读取")

    assert alerts.self_talk_image_chance(_Host(_Cfg())) == alerts.DEFAULT_IMAGE_CHANCE
    assert alerts.self_talk_image_chance(_Host(_Cfg(0))) == 0
    assert alerts.self_talk_image_chance(_Host(_Cfg(77))) == 77
    assert alerts.self_talk_image_chance(object()) == alerts.DEFAULT_IMAGE_CHANCE


def test_config_default_clamps_and_persists(tmp_path):
    """默认 30%、越界钳位到 0~100、能落盘回读。"""
    from pet.config import Config

    cfg = Config(base=tmp_path)
    assert cfg.get("self_talk_image_chance") == 30

    cfg.set("self_talk_image_chance", 0)
    cfg.save()
    assert Config(base=tmp_path).get("self_talk_image_chance") == 0

    cfg.set("self_talk_image_chance", 999)
    cfg.save()
    assert Config(base=tmp_path).get("self_talk_image_chance") == 100


def test_settings_page_row_round_trip(tmp_path):
    """设置页「配图概率」行存在、初值跟配置、改完能落盘。"""
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    QApplication.instance() or QApplication([])
    cfg = Config(base=tmp_path)
    dialog = settings_mod.ModernSettingsDialog(cfg, include_ai=False)
    try:
        row = dialog.findChild(settings_mod.SettingRow, "settingRow_self_talk_image_chance")
        assert row is not None, "设置页必须有这一行"
        assert dialog.self_talk_image_chance_spin.value() == 30
        assert dialog.self_talk_image_chance_spin.minimum() == 0
        assert dialog.self_talk_image_chance_spin.maximum() == 100

        dialog.self_talk_image_chance_spin.setValue(0)
        assert dialog._write_config() is True
    finally:
        dialog.deleteLater()

    assert Config(base=tmp_path).get("self_talk_image_chance") == 0
