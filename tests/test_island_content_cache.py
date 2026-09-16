# -*- coding: utf-8 -*-
"""灵动岛内容层缓存（_content_cache）回归测试。

背景（拖岛扫鱼实测定案）：paintEvent 均值 6.2ms，大头是 CJK 文字
shaping/回退字体解析；内容层只在刷新时变化，拖拽/弹簧期间逐帧重画
是纯浪费。缓存后每帧一次 blit。本文件锁定：缓存命中/失效语义 +
缓存路径下内容确实画出来（不是透明空图）。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from pet.config import Config
from pet.dynamic_island import DynamicIsland


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _island(tmp_path: Path, **overrides) -> DynamicIsland:
    cfg = Config(base=tmp_path)
    data = {
        "enabled": True, "show_icon": True, "show_name": True,
        "show_info": True, "info_mode": "custom", "custom_text": "测试文本",
        "show_status": True, "style": "dark", "x": 400, "y": 300,
    }
    data.update(overrides)
    cfg.set("dynamic_island", data)
    return DynamicIsland(cfg)


def test_content_cache_hit_and_auto_invalidate(tmp_path):
    _qapp()
    island = _island(tmp_path)
    island.show()
    first = island._content_cache()
    assert not first.isNull()
    assert island._content_cache() is first, "同 key 必须命中缓存（同一位图对象）"

    # 内容变化（走生产路径：改配置 + refresh_from_config）→ 自动重建
    data = dict(island._cfg)
    data["custom_text"] = "变了"
    island.config.set("dynamic_island", data)
    island.refresh_from_config()
    second = island._content_cache()
    assert second is not first, "内容变化后必须重建缓存"

    # 可见项变化也必须重建
    data = dict(island._cfg)
    data["show_status"] = False
    island.config.set("dynamic_island", data)
    island.refresh_from_config()
    assert island._content_cache() is not second
    island.deleteLater()


def test_content_cache_renders_nontransparent_content(tmp_path):
    """缓存位图里必须有实际内容（图标圆/文字/状态点的非透明像素）。"""
    _qapp()
    island = _island(tmp_path)
    island.show()
    img = island._content_cache().toImage().convertToFormat(
        QImage.Format.Format_ARGB32)
    opaque = 0
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            if img.pixelColor(x, y).alpha() > 0:
                opaque += 1
    assert opaque > 50, "内容层几乎是空的——缓存路径把内容画丢了"
    island.deleteLater()


def test_paint_uses_cache_without_error(tmp_path):
    """paintEvent 冒烟：正常/形变/展开三态走缓存路径都不炸且有内容。"""
    _qapp()
    island = _island(tmp_path)
    island.show()
    island._squish = 0.85  # 果冻形变中
    grab = island.grab()
    assert not grab.isNull()
    island._squish = 1.0
    island._mode = "expanded"
    island.refresh_from_config()
    grab2 = island.grab()
    assert not grab2.isNull()
    island.deleteLater()


def test_content_cache_key_covers_colors_and_style(tmp_path, monkeypatch):
    """key 必须覆盖所有影响像素的颜色输入；每一步只让一个 key 项在动。

    突变判别：删掉对应 key 项，该步会拿到陈旧位图（同一对象）→ 判红。
    """
    _qapp()
    # 关掉信息槽：风格变化时次文字色项为空，只有主文字色项在动
    island = _island(tmp_path, show_info=False)
    island.show()
    width = island.width()
    dark = island._content_cache()

    data = dict(island._cfg)
    data["style"] = "light"
    island.config.set("dynamic_island", data)
    island.refresh_from_config()
    light = island._content_cache()
    assert island.width() == width, "前提：这些改动不改胶囊宽度（排除宽度项顶包）"
    assert light is not dark, "风格（主文字色）变化必须重建缓存"

    data = dict(island._cfg)
    data["accent"] = "pink"
    island.config.set("dynamic_island", data)
    island.refresh_from_config()
    accent = island._content_cache()
    assert island.width() == width, "前提：主题色不改胶囊宽度"
    assert accent is not light, "主题色变化必须重建缓存"

    island.set_pet_visible(False)  # 状态灯 绿 → 灰
    assert island.width() == width, "前提：可见性不改胶囊宽度"
    assert island._content_cache() is not accent, "状态灯颜色变化必须重建缓存"
    island.deleteLater()

    # 信息色项：自定义峰谷标签同名 + 固定切换时间 → 文案两侧相同，
    # 只有 _info_color 在动（隔离出 info_color.name() 这一项）
    from pet import balance as balance_mod

    island2 = _island(tmp_path, info_mode="balance_tier")
    # 峰谷标签/颜色开关读的是顶层 config（不是 dynamic_island 子字典）
    island2.config.set("balance_tier_labels_mode", "custom")
    island2.config.set("balance_tier_label_peak", "峰")
    island2.config.set("balance_tier_label_idle", "峰")
    island2.show()
    monkeypatch.setattr(balance_mod, "next_pricing_switch",
                        lambda *a, **k: ("idle", None))
    monkeypatch.setattr(balance_mod, "format_switch_time",
                        lambda *a, **k: "12:00")
    monkeypatch.setattr(balance_mod, "deepseek_pricing_tier",
                        lambda *a, **k: "peak")
    peak = island2._content_cache()
    text_peak = island2._info_text()
    monkeypatch.setattr(balance_mod, "deepseek_pricing_tier",
                        lambda *a, **k: "idle")
    assert island2._info_text() == text_peak, "前提：文案不随档位变化"
    assert island2._content_cache() is not peak, "峰谷信息色变化必须重建缓存"
    island2.deleteLater()
