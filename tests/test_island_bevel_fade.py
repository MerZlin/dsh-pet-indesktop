# -*- coding: utf-8 -*-
"""灵动岛**软陶笔**与**结构变化交叉淡化**（P2 Slice E3）契约测试。

施工单 §E3 / 裁决稿 §四（k3 高光笔）+ §一-6（淡化与停表纪律），色值表见 k3 设计稿
§7.1-§7.4。两条各自独立的能力：

1. **软陶笔**：顶部 1px 内高光 + 底部 1px 内暗边，画在胶囊外形**内缘**（既有
   keyline 里侧再 1px），随 squish 形变，**不进内容缓存**（paintEvent 里两笔固定
   成本，内容位图命中不失效）。像素断言取真渲染位图，反解上层笔的 alpha
   （SourceOver premultiplied）：暗色档要量到白 α≈10% / 黑 α≈18%，亮底/玻璃按
   各自档位取值。已知底色两处取法不同——顶部两行底下是底板本身（投影是纯黑，
   只加 alpha、不动预乘颜色），底部那一行的底层取同栈的上一行（底板压在投影上，
   RGB 不变）。
2. **结构变化淡化**：只在**结构变化**时淡化，120-150ms，单侧口径（主代理裁决
   2026-10-07）：**旧位图按 (1−t) 渐出、新位图始终直绘就位**——未变化的内容
   全程零稀释零闪烁，新增/重排的内容即时到位，旧结构残影渐隐。
   **采样值换字零动画**（硬规则）。结构判据 = 宽度白名单键去掉主信息**现文案**
   （时间/余额在跳，照字面拿整键当判据会让时间跳分、余额刷新各播一次淡化）。
   淡化由专表驱动，到点**停表销毁**（停表纪律），拖拽中不播，隐藏即休眠。

禁区（与施工单一致）：几何/信号/停靠/单击路由一律不碰；停靠细条是另一种图元
语言，不画软陶笔也不淡化。
"""
from __future__ import annotations

import math
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QCoreApplication, QTimer, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

import pet.island.dynamic_island as island_mod
from pet.config import Config
from pet.island.dynamic_island import DynamicIsland

#: 墨阈值：滤掉抗锯齿外溢与阴影的弱覆盖（0-255 的 alpha）。
_INK = 40
#: "亮行"判据（R 通道比底板高多少才算一笔高光/描边）。
#: 取 16：软陶笔（白 α26 ≈ +24）与外描边（白 α52 ≈ +50）都在它之上，而顶部
#: 光泽 sheen（暗风白 α14 ≈ +12）在它之下——不然扫亮带的用例会把光泽带读成笔。
_BRIGHT_DELTA = 16


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _island(tmp_path, metrics=(), **overrides) -> DynamicIsland:
    """短文案 + 关闭图标/名字：默认宽恒为 ``_MIN_CAPSULE_W``，中列上下几行零内容。

    软陶笔的取像口径要求"被量的那一行只有底板 + 一支笔"——默认配置下中列（x=60）
    在行 6..10 与行 35..37 上没有任何内容图元（正文墨在 y=13..25）。余额用例宽
    187px，不受这条约束（它只断言"帧 == 位图"）。
    ``metrics`` 是 ``island_metrics`` 列表（未知 id 会降级成 ``--`` 段，无需采样器）。
    """
    cfg = Config(base=tmp_path)
    data = {
        "enabled": True, "show_icon": False, "show_name": False,
        "show_info": True, "info_mode": "custom", "custom_text": "测试",
        "show_status": False, "style": "dark", "x": 400, "y": 300,
        "opacity": 1.0,
    }
    data.update(overrides)
    cfg.set("dynamic_island", data)
    cfg.set("island_metrics", list(metrics))
    return DynamicIsland(cfg)


def _update_config(island: DynamicIsland, **changes) -> None:
    data = dict(island._cfg)
    data.update(changes)
    island.config.set("dynamic_island", data)
    island.refresh_from_config()


def _render(island: DynamicIsland) -> QImage:
    """真渲染一帧（走完整 paintEvent）：透明底 + 显式 render（与既有用例同口径）。"""
    image = QImage(island.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    island.render(image)
    return image


def _content(island: DynamicIsland) -> QImage:
    return island._content_cache().toImage().convertToFormat(
        QImage.Format.Format_ARGB32)


def _pump(predicate, timeout: float = 5.0) -> bool:
    app = _qapp()
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    return predicate()


def _edge_rows(island: DynamicIsland) -> tuple[int, int, int, int]:
    """(顶部外描边行, 顶部内壁笔行, 底部内壁笔行, 底部外描边行)。

    取真几何（``_squished_capsule_rect``），不写死行号：笔的几何必须与胶囊外形
    同源，本函数就是"同源"这件事的断言入口。
    """
    rect, _radius = island._squished_capsule_rect()
    top = int(round(rect.top()))
    bottom = int(round(rect.bottom()))
    return top, top + 1, bottom - 2, bottom - 1


def _premul(image: QImage, x: int, y: int) -> int:
    """像素的**预乘** R 分量（``ARGB32_Premultiplied`` 的原始存储值）。

    取原始预乘值而不是 ``pixelColor().red()``：后者是反预乘后的整数，反解笔的
    alpha 时那一次取整会被 ``1/(1-W)`` 放大（暗底底板 α235、R28 → 反预乘的
    ±0.5 直接变成 ±0.02 的 alpha 误差，实测把黑 α46 量成 α51）。
    """
    return (image.pixel(x, y) >> 16) & 0xFF


def _premul_ref(color: QColor) -> int:
    """主题色在渲染器里实际存下的预乘 R（含 8bit 取整：28·235/255 → 26）。

    顶部那两行的底层就是底板本身（三层投影是纯黑，只加 alpha、不动预乘 R），
    所以底板色的预乘值就是底色参考；底部那几行走 :func:`_bottom_base_premul`
    （同栈实测外推）。
    """
    return round(color.red() * color.alpha() / 255.0)


def _bottom_base_premul(frame: QImage, x: int, row: int) -> int:
    """暗边那一行 ``row`` 的底色预乘 R：上一行实测值 + 逐行斜率外推一格。

    底部那一行的底层是"底板压在阴影上"，而阴影是纯黑（只加 alpha、不动预乘颜色
    ——探针实测渐变+阴影与纯渐变的预乘 R 逐行相同），所以底色 = 底板的预乘值。
    但笔占住了 ``row`` 这一整行，只能从上面两行（无笔）外推：玻璃底是竖直渐变，
    逐行差 ~3 —— 不外推的话反解要偏 0.02。
    """
    previous = _premul(frame, x, row - 1)
    return previous + (previous - _premul(frame, x, row - 2))


def _implied_alpha(measured_premul: int, base_premul: int, src: int) -> float:
    """反解压在 ``base`` 之上那层 ``src`` 灰阶笔的 alpha（SourceOver，预乘域）。

    ``out = src·W + base·(1-W)``（三者都取预乘 R/SourceOver 域）；``src`` 是笔色
    的 R 通道（白 255 / 黑 0 / 玻璃暗边 ``#23323C`` 的 35）。8bit 预乘存储下
    ``base`` 越接近饱和、反解越粗（暗底底板预乘 R=26 → 每 LSB ≈ 0.04），
    所以容差按各档的实际精度给。
    """
    return (measured_premul - base_premul) / (src - base_premul)


def _gradient_color_at(gradient, pos: float) -> QColor:
    """竖直渐变在 ``pos``（0..1）处的颜色——玻璃底板的底色参考。

    PySide6 没暴露 ``QGradient::colorAt``，自己按 stops 线性插值（渐变起止点就是
    (0,0)-(0,height)，停靠位置与像素行线性对应）。
    """
    stops = gradient.stops()
    pos = max(0.0, min(1.0, pos))
    for index in range(1, len(stops)):
        left_pos, left_color = stops[index - 1]
        right_pos, right_color = stops[index]
        if pos <= right_pos or index == len(stops) - 1:
            span = right_pos - left_pos
            ratio = 0.0 if span <= 0 else (pos - left_pos) / span
            ratio = max(0.0, min(1.0, ratio))
            return QColor(
                round(left_color.red() + (right_color.red() - left_color.red()) * ratio),
                round(left_color.green() + (right_color.green() - left_color.green()) * ratio),
                round(left_color.blue() + (right_color.blue() - left_color.blue()) * ratio),
                round(left_color.alpha() + (right_color.alpha() - left_color.alpha()) * ratio),
            )
    return QColor(stops[0][1])


def _shell_distance(island: DynamicIsland, x: float, y: float) -> float:
    """像素到胶囊外形那一圈（外描边 + 内壁笔所在位置）的距离。

    软陶笔按施工单画在**内容位图之上**，所以"帧 vs 内容位图"的逐像素断言必须把
    壳体那几行/几列剔出去（±3px 带外），否则量到的是笔盖在内容上的正确像素。
    """
    rect, radius = island._squished_capsule_rect()
    cx0, cy0 = rect.left() + radius, rect.top() + radius
    cx1, cy1 = rect.right() - radius, rect.bottom() - radius
    dx = max(cx0 - x, 0.0, x - cx1)
    dy = max(cy0 - y, 0.0, y - cy1)
    return abs(math.hypot(dx, dy) - radius)


# ---------------------------------------------------------------- 软陶笔：像素
def test_top_highlight_and_bottom_shadow_pixels_on_dark(tmp_path):
    """暗色档：内缘上 1px 是白 α≈10% 的高光，下 1px 是黑 α≈18% 的暗边。

    顶部底色取 ``_style_palette()[0]``（行 6/7 的底层是底板本身——三层投影是纯黑，
    只加 alpha、不动预乘 R）；底部底色取同栈上一行并按逐行斜率外推一格（见
    :func:`_bottom_base_premul`：投影纯黑只加 alpha，笔又占住了这一整行，只能外推）。
    外描边（keyline）必须原样保留：笔在它**里侧**再 1px（k3 §7.4）。
    """
    _qapp()
    island = _island(tmp_path)
    island.show()
    try:
        top, highlight_row, shadow_row, bottom = _edge_rows(island)
        base = island._style_palette()[0]
        frame = _render(island)
        cx = island.width() // 2
        base_top = _premul_ref(base)

        w_highlight = _implied_alpha(_premul(frame, cx, highlight_row), base_top, 255)
        w_keyline = _implied_alpha(_premul(frame, cx, top), base_top, 255)
        w_shadow = _implied_alpha(
            _premul(frame, cx, shadow_row),
            _bottom_base_premul(frame, cx, shadow_row), 0)

        assert abs(w_highlight - 26 / 255) <= 0.025, (
            f"顶部内高光必须 ≈ 白 α10%（量到 {w_highlight:.3f}）")
        assert abs(w_shadow - 46 / 255) <= 0.03, (
            f"底部内暗边必须 ≈ 黑 α18%（量到 {w_shadow:.3f}）")
        assert abs(w_keyline - 52 / 255) <= 0.025, (
            f"既有 keyline（白 α20%）不许被笔改掉（量到 {w_keyline:.3f}）")

        # 各 1px：外侧那一行的相邻行必须回到纯底板 / 回到外描边
        assert frame.pixelColor(cx, top + 2).red() == base.red(), "高光只有 1px"
        assert frame.pixelColor(cx, bottom).red() > frame.pixelColor(cx, shadow_row).red(), (
            "暗边只有 1px（再往外是外描边，不是继续变暗）")
    finally:
        island.hide()
        island.deleteLater()


def test_bevel_colors_follow_theme(tmp_path):
    """三风格各取自己的档位（k3 §7.1-§7.3），不新增主题键。

    亮底：白高光在纯白上"几乎不可见"（RGB 饱和），但预乘 R 从 242 跳到 ~254，
    仍量得到 α≈90%；底部是黑 α8% 的承托边。玻璃：白 α35% 内高光 +
    ``#23323C`` α10% 带蓝相暗边。亮底白笔的取样精度天生粗（预乘 R 只剩 13 档
    → 每 LSB ≈ 0.08），容差按此放宽。
    """
    _qapp()
    app = _qapp()

    light = _island(tmp_path, style="light")
    light.show()
    try:
        app.processEvents()
        _top, highlight_row, shadow_row, _bottom = _edge_rows(light)
        frame = _render(light)
        cx = light.width() // 2
        w_high = _implied_alpha(
            _premul(frame, cx, highlight_row),
            _premul_ref(light._style_palette()[0]), 255)
        w_shadow = _implied_alpha(
            _premul(frame, cx, shadow_row),
            _bottom_base_premul(frame, cx, shadow_row), 0)
        assert w_high >= 0.78, f"亮底顶部高光是白 α90%（量到 {w_high:.3f}）"
        assert abs(w_shadow - 20 / 255) <= 0.02, (
            f"亮底底部暗边是黑 α8%（量到 {w_shadow:.3f}）")
    finally:
        light.hide()
        light.deleteLater()

    glass = _island(tmp_path, style="glass")
    glass.show()
    try:
        app.processEvents()
        _top, highlight_row, shadow_row, _bottom = _edge_rows(glass)
        frame = _render(glass)
        cx = glass.width() // 2
        gradient = glass._style_palette()[0]
        height = float(glass.height())
        w_high = _implied_alpha(
            _premul(frame, cx, highlight_row),
            _premul_ref(_gradient_color_at(gradient, highlight_row / height)), 255)
        w_shadow = _implied_alpha(
            _premul(frame, cx, shadow_row),
            _bottom_base_premul(frame, cx, shadow_row), 35)
        assert abs(w_high - 90 / 255) <= 0.05, (
            f"glass 内高光减半档 = 白 α35%（量到 {w_high:.3f}）")
        assert abs(w_shadow - 26 / 255) <= 0.04, (
            f"glass 暗边 = #23323C α10%（量到 {w_shadow:.3f}）")
    finally:
        glass.hide()
        glass.deleteLater()


def test_bevel_is_outside_content_cache(tmp_path):
    """两笔不进内容缓存：位图里没有任何笔的像素，且连画多帧缓存不失效。"""
    _qapp()
    island = _island(tmp_path)
    island.show()
    try:
        content = _content(island)
        cx = island.width() // 2
        top, highlight_row, _shadow, _bottom = _edge_rows(island)
        base = island._style_palette()[0]
        for row in range(max(0, top - 2), top + 4):
            assert content.pixelColor(cx, row).alpha() == 0, (
                f"内容位图第 {row} 行不该有软陶笔（笔属于外壳，不进缓存）")
        frame = _render(island)
        assert frame.pixelColor(cx, highlight_row).red() - base.red() >= _BRIGHT_DELTA, (
            "外壳层必须有笔：内缘那一行要比底板亮")

        cached = island._content_cache()
        for _ in range(3):
            _render(island)
        assert island._content_cache() is cached, (
            "每帧画两笔不得让内容缓存失效（成本固定，不进 key）")
    finally:
        island.hide()
        island.deleteLater()


def test_bevel_follows_squish_deformation(tmp_path):
    """随 squish 形变：形变后整条高光带跟着胶囊边缘走（不是钉在固定行上）。"""
    _qapp()
    island = _island(tmp_path)
    island.show()
    try:
        cx = island.width() // 2
        base = island._style_palette()[0]

        def bright_rows(frame: QImage) -> list[int]:
            return [y for y in range(4, 16)
                    if frame.pixelColor(cx, y).red() - base.red() >= _BRIGHT_DELTA]

        flat = bright_rows(_render(island))
        assert len(flat) >= 2, f"顶部应量到外描边 + 内高光两行（量到 {flat}）"
        assert min(flat) == _edge_rows(island)[0], (
            f"静态亮带应起于外描边行（量到 {flat}）")

        island._squish = 0.85  # 果冻形变中（竖压横鼓，绕胶囊中心）
        rect, _radius = island._squished_capsule_rect()
        squished = bright_rows(_render(island))
        assert len(squished) >= 2, f"形变中两行都要在（量到 {squished}）"
        assert min(squished) > max(flat), (
            f"形变后亮带必须整体下移（静态 {flat} / 形变 {squished}）")
        centre = sum(squished) / len(squished)
        assert abs(centre - (rect.top() + 1.0)) <= 2.0, (
            f"亮带中心 {centre:.1f} 应贴住形变后的内缘 {rect.top() + 1.0:.1f}")
    finally:
        island.hide()
        island.deleteLater()


def test_docked_strip_has_no_bevel(tmp_path):
    """停靠细条不画软陶笔：细条是另一种图元语言，保持现状（同岛自由态作对照）。"""
    _qapp()
    island = _island(tmp_path, dock_edge="bottom")
    island.show()
    try:
        _qapp().processEvents()
        assert island._mode == "docked" and not island._hover_peek
        frame = _render(island)
        assert frame.height() == 16, "前提：停靠细条"
        fill = frame.pixelColor(35, 8)
        assert fill.alpha() > 0, "前提：细条底板画出来了"
        for row in (0, 1):
            assert frame.pixelColor(35, row) == fill, (
                f"细条第 {row} 行必须与底板同色（不画高光）")

        # 对照：同一个岛回到自由胶囊态，顶部内缘就要有笔（否则上面的断言是空话）
        island._mode = "normal"
        island._hover_peek = False
        island._apply_fixed_size()
        _qapp().processEvents()
        normal = _render(island)
        _top, highlight_row, _shadow, _bottom = _edge_rows(island)
        assert (normal.pixelColor(island.width() // 2, highlight_row).red()
                - island._style_palette()[0].red()) >= _BRIGHT_DELTA, (
            "对照：自由胶囊态必须有软陶笔")
    finally:
        island.hide()
        island.deleteLater()


# ------------------------------------------------- 顶部光泽 / 玻璃 / 字号 / 细节批
def test_top_sheen_covers_only_the_upper_band_and_never_enters_content(tmp_path):
    """暗风顶部光泽：从 rect.top()+3 起、比软陶笔弱、到中线前归零，且不进内容缓存。

    起点避开上面两行（外描边 + 软陶笔）——那两行是既有像素契约的采样行。
    """
    _qapp()
    island = _island(tmp_path)
    island.show()
    try:
        top, highlight_row, _shadow, _bottom = _edge_rows(island)
        rect, _radius = island._squished_capsule_rect()
        base = island._style_palette()[0]
        frame = _render(island)
        cx = island.width() // 2

        assert frame.pixelColor(cx, top + 2).red() == base.red(), (
            "光泽不得爬上软陶笔所在的行")
        sheen = frame.pixelColor(cx, top + 3).red() - base.red()
        bevel = frame.pixelColor(cx, highlight_row).red() - base.red()
        assert 0 < sheen < bevel, (
            f"光泽必须弱于软陶笔、亮于底板（量到 {sheen} vs 笔 {bevel}）")
        mid = int(round(rect.center().y()))
        assert frame.pixelColor(cx, mid).red() == base.red(), "光泽不该铺到胶囊中线"

        content = _content(island)
        assert content.pixelColor(cx, top + 3).alpha() == 0, (
            "光泽是外壳图元，不得进内容缓存")
        cached = island._content_cache()
        for _ in range(3):
            _render(island)
        assert island._content_cache() is cached, (
            "每帧一笔光泽不得让内容缓存失效（同 _paint_bevel 口径）")
    finally:
        island.hide()
        island.deleteLater()


def test_top_sheen_skips_light_style(tmp_path, monkeypatch):
    """亮风不画光泽：白底上的白光泽等于没有，只会把顶部两道白笔糊宽。

    取证口径是等价性：亮风帧必须与"把整个 sheen 表清零后重画"的帧逐像素相同
    （白底上光泽本就不可见，只有等价性才证得动"真的没画"）。
    """
    _qapp()
    assert island_mod._SHEEN_ALPHA["light"] == 0
    island = _island(tmp_path, style="light")
    island.show()
    try:
        frame = _render(island)
        monkeypatch.setattr(island_mod, "_SHEEN_ALPHA",
                            {"dark": 0, "light": 0, "glass": 0})
        without = _render(island)
        assert frame.size() == without.size()
        diff = [(x, y) for y in range(frame.height()) for x in range(frame.width())
                if frame.pixelColor(x, y).rgba() != without.pixelColor(x, y).rgba()]
        assert not diff, f"亮风帧与「关掉光泽」帧必须一模一样（差异 {diff[:4]}）"
    finally:
        island.hide()
        island.deleteLater()


def test_glass_gradient_has_four_stops_and_a_refraction_fold(tmp_path):
    """玻璃底四停点：0/1 是上下表面，0.48→0.52 掉一档 = 玻璃折射线。"""
    _qapp()
    island = _island(tmp_path, style="glass")
    try:
        stops = island._style_palette()[0].stops()
        assert [round(pos, 2) for pos, _color in stops] == [0.0, 0.48, 0.52, 1.0]
        alphas = [color.alpha() for _pos, color in stops]
        assert alphas == [214, 168, 146, 196], f"四停点 alpha 走样：{alphas}"
        assert alphas[1] > alphas[2], "0.48→0.52 必须掉一档（折不出来的玻璃没有折射）"
    finally:
        island.deleteLater()


def test_value_layer_is_demibold_and_keeps_the_unit_alpha(tmp_path):
    """读数层加粗到 DemiBold：字号仍是 13（盒宽按实测走），单位降色系数不动。"""
    from PySide6.QtGui import QFont
    _qapp()
    island = _island(tmp_path)
    try:
        font = island._font_for("value")
        assert font.pixelSize() == 13
        assert font.weight() == QFont.Weight.DemiBold
        assert island_mod._FONT_LAYERS["label"] == (10, QFont.Weight.Normal), (
            "label 层字重不动（单位盒的弱化靠 10px + α70 双管）")
        assert island_mod._UNIT_ALPHA == 0.7
    finally:
        island.deleteLater()


def test_icon_ring_follows_style_and_style_is_in_the_content_key(tmp_path):
    """头像底圈细光圈按 style 分档，且 style 显式进内容键（跨风格不得命中旧位图）。"""
    _qapp()
    # 只留图标：主/次文字色项全空，键里只有 style 这一项能识别风格
    island = _island(tmp_path, show_icon=True, show_info=False,
                     icon="img:missing.png")
    island.show()
    try:
        dark = island._content_cache()
        assert island._icon_ring_color().name() == "#ffffff"
        assert island._icon_ring_color().alpha() == 46
        _update_config(island, style="light")
        light = island._content_cache()
        assert light is not dark, "跨风格必须重建内容位图（头像光圈随 style 变色）"
        assert island._icon_ring_color().name() == "#000000"
        assert island._icon_ring_color().alpha() == 20
    finally:
        island.hide()
        island.deleteLater()


def test_status_dot_highlight_is_bead_only_for_solid_and_glyph(tmp_path):
    """状态点珠感高光：实心点有上半高光，空心环不画（环内必须仍是空的）。"""
    _qapp()
    neutral = QColor("#a0aabe")  # dark 主题的中性点色（见 _status_dot_style）
    island = _island(tmp_path, show_status=True, show_info=False)
    island.show()
    try:
        def dot_image():
            image = _content(island)
            xs, ys = [], []
            for y in range(image.height()):
                for x in range(image.width()):
                    if image.pixelColor(x, y).alpha() >= 40:
                        xs.append(x)
                        ys.append(y)
            assert xs, "状态点没画出来"
            return image, (min(xs), min(ys))

        island.set_dsh_state("idle")
        image, (x0, y0) = dot_image()
        # 采样点 = 珠感高光的椭圆中心（点内局部 (4, 2.5)）：实心点上这里被提亮，
        # 空心环上这里必须是空的（环内是洞，高光会把洞填回实心）
        assert image.pixelColor(x0 + 4, y0 + 2).red() > neutral.red(), (
            "实心点的上缘要被珠感高光提亮（量到 "
            f"{image.pixelColor(x0 + 4, y0 + 2).red()} ≤ 中性色 {neutral.red()}）")

        island.set_pet_visible(False)  # 空心环
        image, (x0, y0) = dot_image()
        assert image.pixelColor(x0 + 4, y0 + 2).alpha() == 0, (
            "空心环不画珠感（高光中心点必须仍是洞，否则形状通道就废了）")
        assert image.pixelColor(x0 + 3, y0 + 3).alpha() == 0, "环内整片必须是空的"
    finally:
        island.hide()
        island.deleteLater()


# ---------------------------------------------------------------- 结构变化淡化
def _ink_probe(old: QImage, new: QImage, *, old_has: bool, new_has: bool,
               hit: int = 200, miss: int = 0) -> list[tuple[int, int]]:
    """筛出"旧位图有/无墨 × 新位图有/无墨"的像素（有墨 α≥hit，无墨 α≤miss）。

    三个用途：共用区（两版都有墨）、新增区（只有新版有墨）、残影区（只有旧版有墨）。
    共用区要断言"逐像素等于新位图"，所以那里把 hit 提到 255（不透明的新墨才盖得住
    底下那层渐出的旧墨）。
    """
    probes: list[tuple[int, int]] = []
    for y in range(new.height()):
        for x in range(new.width()):
            old_alpha = old.pixelColor(x, y).alpha() if x < old.width() else 0
            new_alpha = new.pixelColor(x, y).alpha()
            old_ok = old_alpha >= hit if old_has else old_alpha <= miss
            new_ok = new_alpha >= hit if new_has else new_alpha <= miss
            if old_ok and new_ok:
                probes.append((x, y))
    return probes


def _ink_mean(frame: QImage, probes) -> float:
    total = 0.0
    for x, y in probes:
        color = frame.pixelColor(x, y)
        total += color.red() + color.green() + color.blue()
    return total / len(probes)


def _mid_fade_frame(island: DynamicIsland) -> QImage:
    """把事件循环泵到混合系数落在 (0,1) 中段，返回当刻帧。

    用例都把 ``_CONTENT_FADE_MS`` 拉长，所以这里赌的不是"睡多久"，而是"系数进了
    中段"——CI 慢机也只是多泵几轮。
    """
    assert _pump(lambda: island._content_fade_timer is not None
                 and 0.15 < island._content_fade_t < 0.85), (
        f"淡化没走到中段：t={island._content_fade_t}")
    return _render(island)


def test_structure_change_starts_fade_with_new_content_in_place(tmp_path, monkeypatch):
    """结构变化触发淡化；新增内容**即时就位**（不参与系数），共用区全程零稀释。

    ``show_icon=True``：**取样锚**。共用区用的是 ``hit=255`` 的逐像素比对，所以
    必须有一个"不透明墨够多、且与字体无关"的区域可量——文字的核心墨随机器字体/
    抗锯齿走（mac CI 实测只剩 10 个 alpha=255 的像素，门槛形同虚设），而头像是内容
    层里**纯几何**画出来的实心圆底（强调色 drawEllipse），换字体一个像素不变。
    """
    _qapp()
    monkeypatch.setattr(island_mod, "_CONTENT_FADE_MS", 900)  # 拉长：不赌时序
    island = _island(tmp_path, show_icon=True)
    island.show()
    try:
        _qapp().processEvents()
        fill = _render(island)
        old_content = _content(island)
        assert island._content_fade_timer is None, "首帧只登记结构签名，不播"

        _update_config(island, show_status=True)  # 结构变化：多一个状态点
        started = _render(island)
        assert island._content_fade_timer is not None, "结构变化必须起淡化"
        assert island._content_fade_from is not None

        mid = _mid_fade_frame(island)
        coefficient = island._content_fade_t
        assert 0.0 < coefficient < 1.0
        new_content = _content(island)
        fresh = _ink_probe(old_content, new_content, old_has=False, new_has=True)
        shared = _ink_probe(old_content, new_content, old_has=True, new_has=True, hit=255)
        assert len(fresh) >= 20, f"新增墨太少，取样没意义（{len(fresh)}）"
        assert len(shared) >= 20, f"共用墨太少，取样没意义（{len(shared)}）"

        island._stop_content_fade()  # 跳到终点（与到点/隐藏同一条收尾路径）
        end = _render(island)
        assert mid.size() == end.size() == fill.size(), "前提：这次结构变化不改宽"

        # 新增内容即时就位：t=0 与中段都是满强度（新位图不按系数渐入）
        assert abs(_ink_mean(started, fresh) - _ink_mean(end, fresh)) <= 0.5, (
            "t=0 帧的新增内容就该是满强度")
        assert abs(_ink_mean(mid, fresh) - _ink_mean(end, fresh)) <= 0.5, (
            f"中段帧的新增内容被稀释了（t={coefficient:.3f}）")
        assert _ink_mean(fill, fresh) < _ink_mean(end, fresh), "前提：那一格原来是空白"
        # 共用区零稀释：中段帧逐像素等于终点帧
        for x, y in shared:
            assert mid.pixelColor(x, y).rgba() == end.pixelColor(x, y).rgba(), (
                f"({x},{y}) 未变化的内容在淡化中途被稀释了")
    finally:
        island.hide()
        island.deleteLater()


def test_old_structure_fades_out_while_shared_stays_intact(tmp_path, monkeypatch):
    """单侧淡化（主代理裁决）：旧结构独有区按 (1−t) 渐出；共用区全程零稀释。

    ``show_icon=True`` = 共用区的取样锚（见上一条用例的说明）。
    """
    _qapp()
    monkeypatch.setattr(island_mod, "_CONTENT_FADE_MS", 3000)
    island = _island(tmp_path, show_status=True, show_icon=True)
    island.show()
    try:
        _qapp().processEvents()
        before = _render(island)  # 旧结构（带状态点）的画面
        old_content = _content(island)

        _update_config(island, show_status=False)  # 结构变化：状态点消失（宽不变）
        started = _render(island)
        assert island._content_fade_timer is not None, "结构变化必须起淡化"
        mid = _mid_fade_frame(island)
        coefficient = island._content_fade_t
        new_content = _content(island)
        assert mid.size() == before.size(), "前提：这次结构变化不改宽"

        island._stop_content_fade()
        end = _render(island)

        vanished = _ink_probe(old_content, new_content, old_has=True, new_has=False)
        shared = _ink_probe(old_content, new_content, old_has=True, new_has=True, hit=255)
        assert len(vanished) >= 20, f"残影区太少，取样没意义（{len(vanished)}）"
        assert len(shared) >= 20, f"共用墨太少，取样没意义（{len(shared)}）"

        # (1-t) 轨迹的起点：t=0 时旧结构还在，且是满强度
        assert abs(_ink_mean(started, vanished) - _ink_mean(before, vanished)) <= 0.5, (
            "t=0 帧必须还是旧画面（残影起点）")
        # 残影按 (1-t) 渐出：中段可见但不满
        faded = ((_ink_mean(mid, vanished) - _ink_mean(end, vanished))
                 / (_ink_mean(before, vanished) - _ink_mean(end, vanished)))
        assert 0.05 < faded < 0.95, f"残影在中段应可见但不满（实测 {faded:.3f}）"
        assert abs(faded - (1.0 - coefficient)) <= 0.25, (
            f"残影强度应 ≈ 1-t = {1.0 - coefficient:.3f}（实测 {faded:.3f}）")
        # 共用区零稀释
        for x, y in shared:
            assert mid.pixelColor(x, y).rgba() == end.pixelColor(x, y).rgba(), (
                f"({x},{y}) 未变化的内容在淡化中途被稀释了")
    finally:
        island.hide()
        island.deleteLater()


def test_sample_value_change_never_fades(tmp_path):
    """采样值换字零动画（硬规则）：余额换字不播淡化，帧就是新位图。"""
    _qapp()
    # 图标提供不依赖字体的实心取样点，余额文字仍参与逐像素比对。
    island = _island(tmp_path, info_mode="balance", show_icon=True)
    island.show()
    try:
        _qapp().processEvents()
        _render(island)
        island.set_balance_info("峰谷", "余额 ¥88888.88")
        first = _content(island)
        _render(island)
        assert island._content_fade_timer is None

        island.set_balance_info("峰谷", "余额 ¥1.00")  # 换字（盒宽不变）
        second = _content(island)
        assert second is not first, "前提：读数真的换了（位图重建）"
        frame = _render(island)
        assert island._content_fade_timer is None, "采样值换字不得起淡化"
        assert island._content_fade_t == 1.0
        assert island._content_fade_from is None

        checked = _assert_frame_is_content(frame, second, island)
        assert checked >= 60, f"取样点太少（{checked}）"
    finally:
        island.hide()
        island.deleteLater()


def _assert_frame_is_content(frame: QImage, content: QImage, island: DynamicIsland) -> int:
    """终点帧与新位图逐像素一致（全不透明的内容像素上，壳体带除外）；返回取样点数。

    壳体带（外描边 + 内壁笔）按施工单画在内容之上，那里的像素本来就该被笔改色。
    """
    checked = 0
    for y in range(frame.height()):
        for x in range(frame.width()):
            pixel = content.pixelColor(x, y)
            if pixel.alpha() != 255:
                continue
            if _shell_distance(island, x, y) <= 3.0:
                continue
            checked += 1
            assert frame.pixelColor(x, y).rgb() == pixel.rgb(), (
                f"({x},{y}) 帧应与新位图逐像素一致（有混合残留）")
    return checked


def test_fade_end_destroys_timer_and_final_frame_is_new_content(tmp_path, monkeypatch):
    """停表纪律：到点定时器销毁；终点帧与新位图逐像素一致（没有残影）。

    ``show_icon=True`` = 逐像素比对（``_assert_frame_is_content`` 的 ``checked``）
    的取样锚：纯几何的实心头像圆底换字体不变，字形核心墨不是（mac CI 实测只剩 42
    个可取样点，门槛形同虚设）。对照岛必须用同一份素材，否则比的是两张不同的图。
    """
    _qapp()
    monkeypatch.setattr(island_mod, "_CONTENT_FADE_MS", 60)
    island = _island(tmp_path, show_icon=True)
    island.show()
    try:
        _qapp().processEvents()
        _render(island)
        _update_config(island, show_status=True)
        _render(island)
        timer = island._content_fade_timer
        assert timer is not None

        assert _pump(lambda: island._content_fade_timer is None), "淡化没到终点"
        assert timer.isActive() is False
        assert island._content_fade_t == 1.0
        assert island._content_fade_from is None
        _process_deferred_delete()
        import shiboken6
        assert not shiboken6.isValid(timer), "淡化结束必须销毁定时器（deleteLater）"

        frame = _render(island)
        checked = _assert_frame_is_content(frame, _content(island), island)
        assert checked >= 60, f"取样点太少（{checked}）"

        # 强断言：终点帧与"从未淡化过的同状态岛"逐像素一致——旧位图零残留
        fresh = _island(tmp_path, show_status=True, show_icon=True)
        fresh.show()
        try:
            _qapp().processEvents()
            reference = _render(fresh)
            assert reference.size() == frame.size()
            residue = [(x, y) for y in range(frame.height()) for x in range(frame.width())
                       if frame.pixelColor(x, y).rgb() != reference.pixelColor(x, y).rgb()]
            assert not residue, f"终点帧有旧位图残留：{residue[:8]}"
        finally:
            fresh.hide()
            fresh.deleteLater()
    finally:
        island.hide()
        island.deleteLater()


def _process_deferred_delete() -> None:
    _qapp().processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_no_fade_while_dragging(tmp_path, monkeypatch):
    """拖拽中不播：拖拽期间的结构变化被静默吸收，松手也不补播。"""
    _qapp()
    monkeypatch.setattr(island_mod, "_CONTENT_FADE_MS", 900)
    island = _island(tmp_path)
    island.show()
    try:
        _qapp().processEvents()
        _render(island)
        island._dragging = True
        _update_config(island, show_status=True)
        _render(island)
        assert island._content_fade_timer is None, "拖拽中不播淡化"
        assert island._content_fade_from is None

        island._dragging = False
        _render(island)
        assert island._content_fade_timer is None, "变化已在拖拽期间吸收，不补播"
        assert island._content_fade_t == 1.0
    finally:
        island.hide()
        island.deleteLater()


def test_release_after_drag_plays_fade_for_real_relayout(tmp_path, monkeypatch):
    """拖拽静默 ≠ 永久失声：松手后收缩级真的变了（冻结解除）→ 照常播一次。"""
    _qapp()
    monkeypatch.setattr(island_mod, "_CONTENT_FADE_MS", 900)
    monkeypatch.setattr(island_mod.DynamicIsland, "_shrink_budget", lambda self: 0)
    island = _island(tmp_path, show_status=True, metrics=("foo", "bar"))
    island.show()
    try:
        _qapp().processEvents()
        _render(island)
        assert island._row_plan().level == 0, "前提：预算 0 = 不收缩"

        island._dragging = True
        monkeypatch.setattr(island_mod.DynamicIsland, "_shrink_budget", lambda self: 60)
        _render(island)
        assert island._content_fade_timer is None, "拖拽中不播"
        assert island._row_plan().level == 0, "前提：拖拽期间计划冻结"

        island._dragging = False
        island._refresh()  # 松手路径：几何与行计划重算
        _render(island)
        assert island._row_plan().level > 0, "前提：松手后真的重排了（折尾段）"
        assert island._content_fade_timer is not None, (
            "松手后的真实结构变化照常播——拖拽静默不是永久失声")
    finally:
        island.hide()
        island.deleteLater()


def test_hide_stops_and_destroys_fade_timer(tmp_path, monkeypatch):
    """隐藏即休眠：隐藏当刻停表销毁，并跳到终点帧。"""
    _qapp()
    monkeypatch.setattr(island_mod, "_CONTENT_FADE_MS", 3000)
    island = _island(tmp_path)
    island.show()
    try:
        _qapp().processEvents()
        _render(island)
        _update_config(island, show_status=True)
        _render(island)
        timer = island._content_fade_timer
        assert timer is not None

        island.hide()
        assert island._content_fade_timer is None
        assert island._content_fade_from is None
        assert island._content_fade_t == 1.0
        _process_deferred_delete()
        import shiboken6
        assert not shiboken6.isValid(timer)
    finally:
        island.hide()
        island.deleteLater()


def test_repeated_fades_do_not_leak_timers(tmp_path):
    """反复淡化不留常驻定时器：20 轮之后 Qt 对象树上的 QTimer 数量不变。"""
    _qapp()
    island = _island(tmp_path)
    island.show()
    try:
        _qapp().processEvents()
        _render(island)
        baseline = len(island.findChildren(QTimer))
        for index in range(20):
            _update_config(island, show_status=bool(index % 2))
            _render(island)
            island._stop_content_fade()
        _process_deferred_delete()
        assert len(island.findChildren(QTimer)) == baseline, (
            "淡化定时器必须真的从对象树上消失（不是只停表）")
    finally:
        island.hide()
        island.deleteLater()


def test_fade_duration_within_spec():
    """时长落在施工单给的 120-150ms 窗口里。"""
    assert 120 <= island_mod._CONTENT_FADE_MS <= 150
