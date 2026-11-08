"""灵动岛视觉与动效：五态状态点、声纳环、呼吸浮动、130ms 内容淡化、外壳质感。

数值与主分支 `pet/dynamic_island.py` 同源：
- 五态点 = 实心 / 空心环 / 红底白感叹，优先级 错误 > 待确认 > 工作 > 隐藏 > 完成 > 空闲；
- 声纳环 1600ms、半径 5→13、α = 90·(1−t)³，只在工作态跑；
- 回复后 5s 呼吸浮动 1.5px·sin(2.6t)；
- 结构变化 130ms 交叉淡化，读数换字不闪；
- 三层投影 (1,+1,α28)/(2,+2.5,α16)/(3,+4,α8)、顶部 sheen、软陶 bevel、glass 四停点渐变。
"""

from __future__ import annotations

import dataclasses
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication

from petcore.config import Config
from petcore.island import layout
from petcore.ui import island as island_ui

app = QApplication.instance() or QApplication([])
SCREEN = (0, 0, 1920, 1080)


class _FakeScreen:
    def __init__(self, geometry):
        self._rect = geometry

    def availableGeometry(self):
        from PySide6.QtCore import QRect

        return QRect(*self._rect)

    def geometry(self):
        return self.availableGeometry()


@pytest.fixture()
def make_island(tmp_path):
    created = []

    def factory(values=None, *, character_name="深深"):
        config = Config(
            tmp_path,
            {
                "island_enabled": True,
                "island_metrics": ["cpu", "mem"],
                **(values or {}),
            },
        )
        widget = island_ui.DynamicIsland(config, character_name=character_name)
        widget._screen_at = lambda _point=None: _FakeScreen(SCREEN)  # noqa: SLF001
        widget._screen = lambda: _FakeScreen(SCREEN)  # noqa: SLF001
        created.append(widget)
        return widget

    yield factory
    for widget in created:
        widget.stop()
        widget.sys_monitor.wait_stopped(5)
        widget.net_monitor.wait_stopped(5)


# ---------------------------------------------------------------- 纯函数


def test_dot_state_priority_order() -> None:
    # 错误最高：连完成瞬态也顶掉。
    assert layout.dot_state("error", done=True) == layout.AGENT_ERROR
    assert layout.dot_state("waiting", done=True) == layout.AGENT_WAITING
    assert layout.dot_state("working", done=True) == layout.AGENT_WORKING
    # 隐藏盖过完成瞬态（桌宠藏起来了，刚完成的绿灯没人看）。
    assert layout.dot_state("idle", pet_visible=False, done=True) == layout.AGENT_HIDDEN
    assert layout.dot_state("idle", pet_visible=True, done=True) == layout.AGENT_DONE
    assert layout.dot_state("idle", pet_visible=True, done=False) == layout.AGENT_IDLE
    # 未知状态按空闲处理，不抛异常。
    assert layout.dot_state("nonsense") == layout.AGENT_IDLE


def test_dot_style_shapes_and_roles() -> None:
    assert layout.dot_style(layout.AGENT_ERROR) == (layout.DOT_GLYPH, "error")
    assert layout.dot_style(layout.AGENT_WAITING) == (layout.DOT_HOLLOW, "accent")
    assert layout.dot_style(layout.AGENT_WORKING) == (layout.DOT_SOLID, "accent")
    assert layout.dot_style(layout.AGENT_DONE) == (layout.DOT_SOLID, "done")
    assert layout.dot_style(layout.AGENT_HIDDEN) == (layout.DOT_HOLLOW, "muted")
    assert layout.dot_style(layout.AGENT_IDLE) == (layout.DOT_SOLID, "muted")


def test_sonar_ring_curve() -> None:
    radius, alpha = layout.sonar_ring_params(0.0)
    assert radius == layout.SONAR_R0_PX
    assert alpha == layout.SONAR_ALPHA
    mid_radius, mid_alpha = layout.sonar_ring_params(0.5)
    assert layout.SONAR_R0_PX < mid_radius < layout.SONAR_R1_PX
    # α = 90·(1−t)³：t=0.5 → 11.25 ≈ 11。
    assert mid_alpha == round(layout.SONAR_ALPHA * 0.125)
    end_radius, end_alpha = layout.sonar_ring_params(1.0)
    assert end_radius == layout.SONAR_R1_PX
    assert end_alpha == 0
    # 越界相位被钳住。
    assert layout.sonar_ring_params(-1.0) == (layout.SONAR_R0_PX, layout.SONAR_ALPHA)
    assert layout.sonar_ring_params(2.0)[1] == 0


def test_breath_offset_is_integer_and_stops_after_five_seconds() -> None:
    assert layout.breath_offset(-1.0) == 0
    assert layout.breath_offset(layout.BREATHE_SECONDS) == 0
    assert layout.breath_offset(layout.BREATHE_SECONDS + 10) == 0
    offsets = {layout.breath_offset(step / 100.0) for step in range(0, 500)}
    assert offsets <= {-2, -1, 0, 1, 2}
    assert max(offsets) >= 1 and min(offsets) <= -1


def test_content_fade_alpha_is_linear() -> None:
    assert layout.content_fade_alpha(0.0) == 1.0
    assert layout.content_fade_alpha(0.5) == pytest.approx(0.5)
    assert layout.content_fade_alpha(1.0) == 0.0
    assert layout.content_fade_alpha(3.0) == 0.0


def test_depth_constants_match_the_main_branch() -> None:
    assert layout.CAPSULE_SHADOW_LAYERS == ((1.0, 1.0, 28), (2.0, 2.5, 16), (3.0, 4.0, 8))
    assert layout.SHEEN_ALPHA == {"dark": 14, "light": 0, "glass": 34}
    assert layout.sheen_alpha("light") == 0
    assert layout.bevel_colors("dark") == ((255, 255, 255, 26), (0, 0, 0, 46))
    assert layout.GLASS_STOPS[0][1][3] == 214 and layout.GLASS_STOPS[-1][1][3] == 196
    assert layout.UNIT_ALPHA == 0.7


# ---------------------------------------------------------------- 状态输入


def test_done_is_transient_and_arms_the_timer(make_island) -> None:
    island = make_island()
    island.show()
    app.processEvents()
    try:
        assert island._dot_state() == layout.AGENT_IDLE
        island.set_agent_state("done")
        assert island._dot_state() == layout.AGENT_DONE
        assert island._dot_done_timer.isActive()
        assert island._breathe_until > time.monotonic()
        # 更高优先级的态顶掉完成瞬态。
        island.set_agent_state("error")
        assert island._dot_state() == layout.AGENT_ERROR
        assert not island._dot_done_timer.isActive()
        # 错误态带 TTL：一次请求失败不该把红感叹永久钉住。
        assert island._error_timer.isActive()
        island._on_error_timeout()  # noqa: SLF001
        assert island._dot_state() == layout.AGENT_IDLE
    finally:
        island.hide()


def test_sources_are_merged_instead_of_last_write_wins(make_island) -> None:
    """截图收尾发 idle 不许把仍在流式回答的对话（working）一起关掉。"""
    island = make_island()
    island.set_agent_state("working", source="chat")
    assert island._dot_state() == layout.AGENT_WORKING
    island.set_agent_state("working", source="screen")
    island.set_agent_state("idle", source="screen")
    assert island._dot_state() == layout.AGENT_WORKING
    island.set_agent_state("idle", source="chat")
    assert island._dot_state() == layout.AGENT_IDLE
    # 错误压过工作态；错误到期后回到仍在工作的状态。
    island.set_agent_state("working", source="chat")
    island.set_agent_state("error", source="chat")
    assert island._dot_state() == layout.AGENT_ERROR
    island._on_error_timeout()  # noqa: SLF001
    assert island._dot_state() == layout.AGENT_IDLE


def test_balance_result_never_moves_the_status_dot(make_island) -> None:
    """余额失败在卡片里已经是一行红字，不许把状态点永久钉成红感叹。"""
    island = make_island()
    island.set_agent_state("working", source="chat")
    island.show_balance(error="没有配置余额服务")
    assert island._dot_state() == layout.AGENT_WORKING
    island.show_balance(result="余额 ¥1.00")
    assert island._dot_state() == layout.AGENT_WORKING


def test_stop_releases_every_new_timer(make_island) -> None:
    island = make_island()
    island.show()
    app.processEvents()
    try:
        island.set_agent_state("working", source="chat")
        island.set_agent_state("error", source="chat")
        island.set_agent_state("done")
        island._start_content_fade(island._content_layer())  # noqa: SLF001
        island.stop()
        for timer in (
            island._sonar_timer,  # noqa: SLF001
            island._dot_done_timer,  # noqa: SLF001
            island._error_timer,  # noqa: SLF001
            island._fade_timer,  # noqa: SLF001
        ):
            assert not timer.isActive()
        assert island._fade_from is None  # noqa: SLF001
        assert island._agent_sources == {}  # noqa: SLF001
    finally:
        island.hide()


def test_pet_visibility_drives_the_hidden_state(make_island) -> None:
    island = make_island()
    island.set_pet_visible(False)
    assert island._dot_state() == layout.AGENT_HIDDEN
    assert island.dot_style() == (layout.DOT_HOLLOW, "muted")
    island.set_pet_visible(True)
    assert island._dot_state() == layout.AGENT_IDLE


def test_sonar_timer_follows_the_working_state(make_island) -> None:
    island = make_island()
    island.show()
    app.processEvents()
    try:
        island.set_agent_state("working")
        assert island._sonar_timer.isActive()
        assert island.dot_style() == (layout.DOT_SOLID, "accent")
        island.set_agent_state("idle")
        assert not island._sonar_timer.isActive()
    finally:
        island.hide()


def test_sonar_is_suppressed_when_the_status_dot_is_off(make_island) -> None:
    island = make_island({"island_show_status": False})
    island.show()
    app.processEvents()
    try:
        island.set_agent_state("working")
        assert not island._sonar_timer.isActive()
    finally:
        island.hide()


def test_sonar_is_suppressed_while_the_card_is_expanded(make_island) -> None:
    island = make_island()
    island.show()
    app.processEvents()
    try:
        island.set_agent_state("working", source="chat")
        assert island._sonar_timer.isActive()
        island.expand_card()
        app.processEvents()
        assert not island._sonar_timer.isActive()
        island.collapse_card()
        app.processEvents()
        assert island._sonar_timer.isActive()
    finally:
        island.hide()


def test_sonar_is_suppressed_while_docked(make_island) -> None:
    island = make_island()
    island.show()
    app.processEvents()
    try:
        island.set_agent_state("working", source="chat")
        assert island._sonar_timer.isActive()
        island._docked = True  # noqa: SLF001
        island._dock_edge = "top"  # noqa: SLF001
        island._sync_sonar()  # noqa: SLF001
        assert not island._sonar_timer.isActive()
    finally:
        island._docked = False  # noqa: SLF001
        island._dock_edge = "none"  # noqa: SLF001
        island.hide()


def test_error_state_is_red_glyph(make_island) -> None:
    island = make_island()
    island.set_agent_state("error")
    assert island.dot_style() == (layout.DOT_GLYPH, "error")
    assert island._dot_color().name() == layout.DOT_ERROR_COLOR


# ---------------------------------------------------------------- 状态点绘制


def _dot_image(island, size: int = 16) -> QColor:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    island._paint_status_dot(painter, QRectF(4, 4, 8, 8))  # noqa: SLF001
    painter.end()
    return pixmap.toImage()


def test_hollow_dot_keeps_a_transparent_centre(make_island) -> None:
    island = make_island()
    island.set_agent_state("waiting")
    image = _dot_image(island)
    assert image.pixelColor(8, 8).alpha() <= 8
    # 环上（左缘）必须有像素。
    assert image.pixelColor(4, 8).alpha() > 40


def test_solid_dot_fills_the_centre(make_island) -> None:
    island = make_island()
    island.set_agent_state("working")
    image = _dot_image(island)
    assert image.pixelColor(8, 8).alpha() > 80


def test_error_dot_draws_a_white_glyph_on_red(make_island) -> None:
    island = make_island()
    island.set_agent_state("error")
    image = _dot_image(island)
    # 采样必须落在**竖杠上**（y = 4 + 8×0.18 … 4 + 8×0.46 ≈ 5.4…7.7）。
    # 采 (8,9) 会落在红底上，而 #f2777a 的 HSL 亮度本来就是 181 —— 那样
    # 「实心红点根本不画感叹」也能过。
    bar = image.pixelColor(8, 6)
    assert bar.alpha() > 200
    assert bar.lightness() > 220
    # 圆点在 4 + 8×0.82 ≈ 10.6 附近（RoundCap 描边，直径 1.36）：区域内必须有白墨。
    dot_pixels = [
        image.pixelColor(x, y).lightness() for x in range(6, 11) for y in range(10, 12)
    ]
    assert max(dot_pixels) > 220


# ---------------------------------------------------------------- 内容位图缓存


def test_content_bitmap_is_reused_until_the_pixels_change(make_island, monkeypatch) -> None:
    island = make_island()
    island._update_size()  # noqa: SLF001
    calls = {"count": 0}
    original = island._render_content_pixmap  # noqa: SLF001

    def counting():
        calls["count"] += 1
        return original()

    monkeypatch.setattr(island, "_render_content_pixmap", counting)
    first = island._content_layer()  # noqa: SLF001
    second = island._content_layer()  # noqa: SLF001
    assert first is not None and second is first
    assert calls["count"] == 1

    # 读数换字 → 像素变了，必须重建。
    island.settings = dataclasses.replace(
        island.settings, info_mode="custom", custom_text="换一段字"
    )
    island._rebuild_row()  # noqa: SLF001
    island._update_size()  # noqa: SLF001
    third = island._content_layer()  # noqa: SLF001
    assert third is not first
    assert calls["count"] == 2


def test_value_change_does_not_start_a_fade(make_island) -> None:
    island = make_island()
    island.settings = dataclasses.replace(
        island.settings, info_mode="custom", custom_text="甲"
    )
    island._rebuild_row()  # noqa: SLF001
    island._update_size()  # noqa: SLF001
    island._content_layer()  # noqa: SLF001
    assert island._fade_from is None  # noqa: SLF001

    island.settings = dataclasses.replace(island.settings, custom_text="乙")
    island._rebuild_row()  # noqa: SLF001
    island._update_size()  # noqa: SLF001
    island._content_layer()  # noqa: SLF001
    # 只有读数变、结构没变：不播淡化（否则时间每秒都在闪）。
    assert island._fade_from is None  # noqa: SLF001


def test_structure_change_starts_and_finishes_the_fade(make_island) -> None:
    """宽度不变、结构变了（指标段换序）才播淡化；宽度变化不许播（会重影）。"""
    island = make_island()
    island._update_size()  # noqa: SLF001
    first = island._content_layer()  # noqa: SLF001
    assert first is not None

    island.settings = dataclasses.replace(island.settings, metrics=("mem", "cpu"))
    island._rebuild_row()  # noqa: SLF001
    island._update_size()  # noqa: SLF001
    second = island._content_layer()  # noqa: SLF001
    assert second is not None and second.size() == first.size()
    assert island._fade_from is not None  # noqa: SLF001
    assert island._fade_t < 1.0  # noqa: SLF001

    # 推进到超过 130ms：回到终点帧并收表。
    island._fade_last = time.monotonic() - 1.0  # noqa: SLF001
    island._on_fade_tick()  # noqa: SLF001
    assert island._fade_t == 1.0  # noqa: SLF001
    assert island._fade_from is None  # noqa: SLF001
    assert not island._fade_timer.isActive()  # noqa: SLF001


def test_width_change_does_not_start_a_fade(make_island) -> None:
    """几何动画每帧都改宽度：含进结构键会每帧重起淡化 + 掩码尺寸不符 → 重影。"""
    island = make_island()
    island._update_size()  # noqa: SLF001
    island._content_layer()  # noqa: SLF001
    island.settings = dataclasses.replace(island.settings, show_name=False)
    island._update_size()  # noqa: SLF001
    island._content_layer()  # noqa: SLF001
    assert island._fade_from is None  # noqa: SLF001
    assert not island._fade_timer.isActive()  # noqa: SLF001


def test_cache_key_tracks_the_status_dot(make_island, monkeypatch) -> None:
    """点画在位图里：形状/颜色变了必须重建，否则状态点会冻结在旧态。"""
    island = make_island()
    island._update_size()  # noqa: SLF001
    calls = {"count": 0}
    original = island._render_content_pixmap  # noqa: SLF001

    def counting():
        calls["count"] += 1
        return original()

    monkeypatch.setattr(island, "_render_content_pixmap", counting)
    island._content_layer()  # noqa: SLF001
    assert calls["count"] == 1
    island.set_agent_state("working", source="chat")
    island._content_layer()  # noqa: SLF001
    assert calls["count"] == 2
    island.set_agent_state("idle", source="chat")
    island._content_layer()  # noqa: SLF001
    assert calls["count"] == 3


# ---------------------------------------------------------------- 外壳质感（离屏像素）


def _shell_image(island, *, sheen: bool = True, style: str | None = None):
    if style is not None:
        island.settings = dataclasses.replace(island.settings, style=style)
    pixmap = QPixmap(200, 44)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    palette = island._palette()  # noqa: SLF001
    rect = QRectF(6, 6, 188, 32)
    path = QPainterPath()
    path.addRoundedRect(rect, 16.0, 16.0)
    island._paint_shell(painter, palette, rect, 16.0, path)  # noqa: SLF001
    if sheen:
        island._paint_sheen(painter, rect, 16.0)  # noqa: SLF001
    painter.end()
    return pixmap.toImage()


def test_three_layer_shadow_reaches_below_the_capsule(make_island) -> None:
    island = make_island({"island_style": "dark"})
    image = _shell_image(island, sheen=False)
    # 胶囊底边 = 6+32 = 38；最底层下伸 4px，39..41 必须还有半透明黑。
    alphas = [image.pixelColor(100, y).alpha() for y in (39, 40, 41)]
    assert all(alpha > 0 for alpha in alphas), alphas
    # 越往下越淡（三层叠加的单向衰减）。
    assert alphas[0] >= alphas[-1]


def test_sheen_makes_the_top_of_the_capsule_brighter(make_island) -> None:
    island = make_island({"island_style": "dark"})
    with_sheen = _shell_image(island, sheen=True)
    without = _shell_image(island, sheen=False)
    top_with = with_sheen.pixelColor(100, 11).lightness()
    top_without = without.pixelColor(100, 11).lightness()
    assert top_with > top_without
    # 中部（sheen 覆盖区之外）两版一致。
    assert with_sheen.pixelColor(100, 30) == without.pixelColor(100, 30)


def test_light_style_skips_the_sheen(make_island) -> None:
    island = make_island({"island_style": "light"})
    with_sheen = _shell_image(island, sheen=True, style="light")
    without = _shell_image(island, sheen=False, style="light")
    assert with_sheen.pixelColor(100, 11) == without.pixelColor(100, 11)


def test_glass_style_uses_a_vertical_gradient(make_island) -> None:
    island = make_island({"island_style": "glass"})
    image = _shell_image(island, sheen=False, style="glass")
    top = image.pixelColor(100, 8).alpha()
    bottom = image.pixelColor(100, 35).alpha()
    # 四停点：顶 214 / 底 196（乘不透明度 ≈1.0）。
    assert top > bottom


def test_bevel_draws_both_halves(make_island) -> None:
    island = make_island({"island_style": "dark"})
    pixmap = QPixmap(200, 44)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    rect = QRectF(6, 6, 188, 32)
    island._paint_bevel(painter, rect, 16.0)  # noqa: SLF001
    painter.end()
    image = pixmap.toImage()
    top_half = sum(
        image.pixelColor(x, y).alpha()
        for y in range(7, 14)
        for x in range(20, 180)
    )
    bottom_half = sum(
        image.pixelColor(x, y).alpha()
        for y in range(30, 37)
        for x in range(20, 180)
    )
    assert top_half > 0 and bottom_half > 0


def test_capsule_has_no_header_dot_and_content_starts_at_the_padding(make_island) -> None:
    """头标（左侧强调色圆点）已退役：内容直接从 CONTENT_PAD 起排，不留空槽。"""
    island = make_island(
        {"island_show_name": False, "island_show_status": False, "island_metrics": [],
         "island_info_mode": "custom", "island_custom_text": ""}
    )
    island._update_size()  # noqa: SLF001
    layer = island._content_layer()  # noqa: SLF001
    assert layer is not None
    image = layer.toImage()
    centre_x = int(island_ui.CAPSULE_INSET + island_ui.CONTENT_PAD + 10)
    centre_y = int(island_ui.CAPSULE_HEIGHT / 2)
    # 旧头标圆心位置必须干净：没有一个强调色实心圆压在内容行左端。
    assert image.pixelColor(centre_x, centre_y).alpha() == 0
    assert island._lead_trail("")[0] == island_ui.CONTENT_PAD  # noqa: SLF001
