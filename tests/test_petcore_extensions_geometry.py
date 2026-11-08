"""扩展管理页的两个轴：宽度（导航 + 内容）与高度（浏览器填充）都随字号/窗口弹性。

用户口径：**当前的显示范围就是下限**——字号 13 时导航 190、右侧最小高 420、
页面内容 860 宽都与改动前一致，只许往上长；窗口拉大时内容跟窗口，不再停在 860，
也不再在下方留一大片空白。
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QApplication

from petcore.contracts.contributions import SettingsPageSpec
from petcore.ui.settings import page_extensions as ext_mod
from petcore.ui.settings.base import SettingsPage
from petcore.ui.settings.dialog import SettingsDialog
from petcore.ui.settings.page_extensions import ExtensionsPage
from tests.test_petcore_settings import FakeHost


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- 纯函数


def test_font_scale_never_shrinks_below_the_default_font() -> None:
    assert ext_mod.font_scale(13) == 1.0
    assert ext_mod.font_scale(10) == 1.0  # 下限：不缩
    assert ext_mod.font_scale(18) == pytest.approx(18 / 13)


def test_nav_bounds_keep_the_current_range_as_the_lower_bound() -> None:
    assert ext_mod.nav_bounds(13) == (190, 260)
    assert ext_mod.nav_bounds(10) == (190, 260)
    low, high = ext_mod.nav_bounds(18)
    assert low == ext_mod.NAV_MIN_WIDTH  # 下限不随字号变
    assert high == 360 and high > ext_mod.NAV_MAX_WIDTH


def test_nav_width_is_content_driven_between_the_scaled_bounds() -> None:
    # 内容很窄 → 不低于下限（保持现状宽度）
    assert ext_mod.nav_width(87, 13) == 190
    assert ext_mod.nav_width(87, 18) == 190
    # 内容超过上限 → 封在（放大后的）上限
    assert ext_mod.nav_width(300, 13) == 260
    assert ext_mod.nav_width(380, 18) == 360
    # 大字号下上限更大：13 字号会被裁的同一段文字，18 字号下放得下
    assert ext_mod.nav_width(320, 13) == 260
    assert ext_mod.nav_width(320, 18) == 320 + ext_mod.NAV_TEXT_CHROME_PX


# ---------------------------------------------------------------- 窗口行为


class _LongLabelPage(SettingsPage):
    title = "长名字的加装页"

    def __init__(self, host):
        super().__init__(host)


LONG_LABEL = "一个名字特别特别长的加装扩展页面标题"


def _dialog(
    app,
    tmp_path: Path,
    *,
    font_size: int | None = None,
    size=(900, 620),
    long_label: bool = False,
):
    host = FakeHost(Path(tmp_path))
    if font_size is not None:
        host.config.set("ui_font_size", int(font_size))
    if long_label:
        host.settings_pages = lambda: [
            SettingsPageSpec(
                "addon.long",
                "Long",
                _LongLabelPage,
                order=10,
                label=LONG_LABEL,
                group="addon",
            )
        ]
    dialog = SettingsDialog(host)
    dialog.resize(*size)
    dialog.show()
    _pump(app)
    dialog.select_page("mods")
    _pump(app)
    return dialog


def _pump(app, rounds: int = 6) -> None:
    for _ in range(rounds):
        app.processEvents()


def _extensions(dialog: SettingsDialog) -> ExtensionsPage:
    for index in range(dialog.stack.count()):
        page = dialog.stack.widget(index)
        if getattr(page, "title", "") == "扩展管理":
            return page
    raise AssertionError("missing extensions page")


def test_browser_fills_the_window_height_instead_of_a_fixed_420(app, tmp_path: Path) -> None:
    dialog = _dialog(app, tmp_path / "tall", size=(900, 1000))
    try:
        ext = _extensions(dialog)
        viewport = ext.scroll.viewport()
        content = ext.scroll.widget()
        browser = ext.nav.parentWidget()
        assert ext.stack.height() > ext_mod.STACK_MIN_HEIGHT + 40
        # 浏览器下缘贴住视口（只剩页面自身的 20px 下边距），不再留 464px 空白
        gap = viewport.height() - (browser.geometry().bottom() + 1)
        assert gap <= 32, gap
        assert content.height() >= viewport.height()
    finally:
        dialog.close()


def test_page_content_follows_the_window_width_past_860(app, tmp_path: Path) -> None:
    dialog = _dialog(app, tmp_path / "wide", size=(1400, 800))
    try:
        ext = _extensions(dialog)
        assert ext.scroll.widget().width() > 900
        assert ext.stack.width() > 600
    finally:
        dialog.close()


def test_other_pages_keep_their_reading_width(app, tmp_path: Path) -> None:
    """只有扩展管理页解除 860 上限，别的页仍是阅读宽度（不顺手改全局观感）。"""
    dialog = _dialog(app, tmp_path / "reading", size=(1400, 800))
    try:
        general = next(e.page for e in dialog._pages if e.key == "general")  # noqa: SLF001
        dialog._select_row([e.key for e in dialog._pages].index("general"))  # noqa: SLF001
        _pump(app)
        assert general.content.width() <= 860
    finally:
        dialog.close()


def test_larger_font_grows_the_nav_and_its_text(app, tmp_path: Path) -> None:
    """字号放大：导航文字跟着变成 18px；长标签在上限内完整显示（不再省略号）。"""
    dialog = _dialog(
        app, tmp_path / "bigfont", font_size=18, size=(1100, 700), long_label=True
    )
    try:
        ext = _extensions(dialog)
        assert ext.nav.font().pixelSize() == 18
        metrics = QFontMetrics(ext.nav.font())
        widest = max(
            (metrics.horizontalAdvance(ext.nav.item(row).text()) for row in range(ext.nav.count())),
            default=0,
        )
        assert ext.nav.width() > ext_mod.NAV_MIN_WIDTH
        assert ext.nav.width() >= widest + ext_mod.NAV_TEXT_CHROME_PX
        assert ext.nav.width() <= ext_mod.nav_bounds(18)[1]
    finally:
        dialog.close()


def test_large_font_does_not_add_an_outer_scrollbar_at_the_default_window(app, tmp_path: Path) -> None:
    """字号 18 时右侧最小高仍是 420：不让整页多出一层外层滚动（实测 582 会）。"""
    dialog = _dialog(app, tmp_path / "no-scroll", font_size=18, size=(900, 620))
    try:
        ext = _extensions(dialog)
        assert ext.stack.minimumHeight() == ext_mod.STACK_MIN_HEIGHT
        assert ext.scroll.verticalScrollBar().maximum() == 0
    finally:
        dialog.close()


def test_minimum_window_is_no_worse_than_before(app, tmp_path: Path) -> None:
    """720×500：导航仍是 190、右侧最小高仍是 420、Mods 页横向滚动不超过现状的 110px。"""
    dialog = _dialog(app, tmp_path / "narrow", size=(720, 500))
    try:
        ext = _extensions(dialog)
        mods = dict(ext._pages)["mods"]  # noqa: SLF001
        _pump(app)
        assert ext.nav.width() == ext_mod.NAV_MIN_WIDTH
        assert ext.stack.minimumHeight() >= ext_mod.STACK_MIN_HEIGHT
        assert mods.scroll.horizontalScrollBar().maximum() <= 110
    finally:
        dialog.close()


def test_minimum_window_mod_controls_fit_without_horizontal_scroll(app, tmp_path: Path) -> None:
    dialog = _dialog(app, tmp_path / "compact-controls", size=(720, 500))
    try:
        mods = dict(_extensions(dialog)._pages)["mods"]
        assert mods.scroll.horizontalScrollBar().maximum() == 0
        viewport = mods.scroll.viewport()
        for control in (mods.feature_check, mods.filter_combo, mods.import_zip_button,
                        mods.import_dir_button, mods.reload_button):
            left = control.mapTo(viewport, control.rect().topLeft()).x()
            assert left >= 0
            assert left + control.width() <= viewport.width()
    finally:
        dialog.close()


def test_font_change_reflows_the_open_dialog(app, tmp_path: Path) -> None:
    host = FakeHost(Path(tmp_path / "live"))
    host.settings_pages = lambda: [
        SettingsPageSpec(
            "addon.long", "Long", _LongLabelPage, order=10, label=LONG_LABEL, group="addon"
        )
    ]
    dialog = SettingsDialog(host)
    dialog.resize(1100, 700)
    dialog.show()
    _pump(app)
    dialog.select_page("mods")
    _pump(app)
    ext = _extensions(dialog)
    before = ext.nav.width()
    try:
        host.config.set("ui_font_size", 18)
        dialog.refresh_style()
        _pump(app)
        assert ext.nav.font().pixelSize() == 18
        assert ext.nav.width() >= before
        metrics = QFontMetrics(ext.nav.font())
        widest = max(
            (metrics.horizontalAdvance(ext.nav.item(row).text()) for row in range(ext.nav.count())),
            default=0,
        )
        assert ext.nav.width() >= widest + ext_mod.NAV_TEXT_CHROME_PX
    finally:
        dialog.close()


def test_settings_sidebar_row_height_follows_the_font_size(app, tmp_path: Path) -> None:
    dialog = _dialog(app, tmp_path / "rows13", font_size=13)
    try:
        assert all(
            dialog.nav.item(row).sizeHint().height() == 34
            for row in range(dialog.nav.count())
        )
    finally:
        dialog.close()

    big = _dialog(app, tmp_path / "rows18", font_size=18)
    try:
        assert all(
            big.nav.item(row).sizeHint().height() == 39
            for row in range(big.nav.count())
        )
        # 行高与 QSS 同源：字号 18 的侧栏文字不会被 34px 的行切掉
        assert "min-height: 39px" in big.styleSheet()
    finally:
        big.close()


def test_extensions_nav_is_accessible_by_keyboard(app, tmp_path: Path) -> None:
    """导航仍是可聚焦的多选列表（几何变化不能把键盘可达性弄丢）。"""
    dialog = _dialog(app, tmp_path / "keys")
    try:
        ext = _extensions(dialog)
        assert ext.nav.focusPolicy() != Qt.FocusPolicy.NoFocus
        assert ext.nav.selectionMode() == ext.nav.SelectionMode.SingleSelection
    finally:
        dialog.close()
