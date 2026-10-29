"""P5 回归：碰撞圆链、气泡锚点、Enter 保存、多行台词、免打扰与扩展反馈。"""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QImage, QPainter, QRegion
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QPlainTextEdit,
    QStyle,
    QStyleOptionMenuItem,
    QWidget,
)

from petcore.engine.collision import (
    HIT_FEEDBACK_MIN_SPEED,
    MAX_SEPARATION,
    OVERLAP_RATIO,
    SLOP,
    CollisionBody,
    circles_from_rect,
    collide,
    deepest_overlap,
    rects_may_touch,
    separation_distance,
    stable_tie_break,
)
from petcore.ui.bubble import BUBBLE_GAP, SpeechBubble
from petcore.ui.menu_model import MenuItem, MenuModel
from petcore.ui.menu_style import (
    CHECK_LAYER_ATTR,
    MenuCheckLayer,
    menu_stylesheet,
)
from petcore.ui.settings.text_lines import format_lines, parse_lines


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- 碰撞
def test_circles_from_rect_uses_three_inscribed_circles() -> None:
    circles = circles_from_rect((0, 0, 300, 100))
    assert len(circles) == 3
    assert all(radius == 50 for _x, _y, radius in circles)
    assert [round(x) for x, _y, _r in circles] == [50, 150, 250]


def test_circles_from_rect_stacks_vertically_when_tall() -> None:
    circles = circles_from_rect((0, 0, 100, 300))
    assert [round(y) for _x, y, _r in circles] == [50, 150, 250]


def test_deepest_overlap_reports_depth_and_normal() -> None:
    a = circles_from_rect((0, 0, 100, 100))
    b = circles_from_rect((40, 0, 100, 100))
    depth, nx, ny = deepest_overlap(a, b)
    assert depth == pytest.approx(60.0)
    assert nx == pytest.approx(1.0)
    assert ny == pytest.approx(0.0, abs=1e-9)


def test_deepest_overlap_is_none_when_far_apart() -> None:
    a = circles_from_rect((0, 0, 100, 100))
    b = circles_from_rect((300, 0, 100, 100))
    assert deepest_overlap(a, b) is None


def test_rects_may_touch_is_a_necessary_condition_only() -> None:
    """粗筛只排除「矩形分离」的配对：相交 / 相切 / 重叠都必须放行给圆链判定。"""
    assert rects_may_touch((0, 0, 100, 100), (40, 0, 100, 100)) is True
    assert rects_may_touch((0, 0, 100, 100), (0, 0, 100, 100)) is True
    assert rects_may_touch((0, 0, 100, 100), (100.5, 0, 100, 100)) is True
    assert rects_may_touch((0, 0, 100, 100), (0, 100.5, 100, 100)) is True
    assert rects_may_touch((0, 0, 100, 100), (300, 0, 100, 100)) is False
    assert rects_may_touch((0, 0, 100, 100), (0, 300, 100, 100)) is False
    # 退化矩形（宽/高为 0）没有内切圆 ⇒ 不可能接触。
    assert rects_may_touch((0, 0, 0, 100), (0, 0, 100, 100)) is False


def test_collide_rejects_far_bodies_before_circle_math() -> None:
    """99 只时 n² 对里绝大多数离得很远：粗筛直接返回 None，结果与圆链判定一致。"""
    a = CollisionBody(x=0, y=0, width=100, height=100, slot=1)
    b = CollisionBody(x=900, y=900, width=100, height=100, slot=2)
    assert collide(a, b) is None


def test_collide_still_reports_overlapping_bodies() -> None:
    a = CollisionBody(x=0, y=0, width=100, height=100, slot=1)
    b = CollisionBody(x=20, y=0, width=100, height=100, slot=2)
    result = collide(a, b)
    assert result is not None and result.depth > 0 and result.nx > 0


def test_stable_tie_break_is_deterministic_and_unit_length() -> None:
    first = stable_tie_break(0, 1)
    assert first == stable_tie_break(1, 0)
    assert first == stable_tie_break(0, 1)
    assert len(first) == 2
    assert first[0] ** 2 + first[1] ** 2 == pytest.approx(1.0)


def test_separation_follows_the_sixty_percent_rule() -> None:
    depth = 20.0
    expected = max(1.0, min(MAX_SEPARATION, (depth - SLOP) * OVERLAP_RATIO))
    assert separation_distance(depth) == pytest.approx(expected)


def test_separation_ignores_sub_slop_overlap_and_caps_at_twelve() -> None:
    assert separation_distance(SLOP) == 0.0
    assert separation_distance(200.0) == pytest.approx(MAX_SEPARATION)


def test_force_full_separation_clears_the_whole_overlap() -> None:
    assert separation_distance(30.0, force_full=True) == pytest.approx(30.0)
    assert separation_distance(400.0, force_full=True) == pytest.approx(MAX_SEPARATION * 4)


def test_collide_separates_and_reports_impact() -> None:
    a = CollisionBody(0, 0, 100, 100, vx=400.0, vy=0.0, slot=0)
    b = CollisionBody(40, 0, 100, 100, vx=-400.0, vy=0.0, slot=1)
    result = collide(a, b)
    assert result is not None
    assert result.separation > 0
    assert result.ax < result.bx
    assert result.impact_speed > HIT_FEEDBACK_MIN_SPEED


# ---------------------------------------------------------------- 文本行
def test_parse_lines_splits_rows_and_legacy_pipes() -> None:
    text = "第一条\n 第二条 \n\n第三条|第四条\n｜第五条"
    assert parse_lines(text) == ["第一条", "第二条", "第三条", "第四条", "第五条"]


def test_format_lines_round_trips() -> None:
    items = ["a", " b ", ""]
    assert parse_lines(format_lines(items)) == ["a", "b"]


# ---------------------------------------------------------------- 气泡锚点
def test_bubble_sits_just_above_the_anchor(app) -> None:
    bubble = SpeechBubble()
    anchor = QRect(300, 300, 108, 135)
    bubble.show_text("你好", anchor)
    try:
        assert bubble.y() + bubble.height() == anchor.top() - BUBBLE_GAP
        assert abs(bubble.x() + bubble.width() // 2 - anchor.center().x()) <= 2
    finally:
        bubble.hide()


def test_bubble_avoids_the_pet_when_the_top_is_blocked(app) -> None:
    bubble = SpeechBubble()
    anchor = QRect(300, 0, 108, 60)
    bubble.show_text("贴顶", anchor)
    try:
        placed = QRect(bubble.pos(), bubble.size())
        assert not placed.intersects(anchor)
        assert bubble.y() >= 0
    finally:
        bubble.hide()


def test_bubble_stays_on_screen_near_the_right_edge(app) -> None:
    screen = app.primaryScreen()
    area = screen.availableGeometry()
    bubble = SpeechBubble()
    anchor = QRect(area.right() - 60, area.center().y(), 60, 120)
    bubble.show_text("贴右边", anchor)
    try:
        assert bubble.x() >= area.left()
        assert bubble.x() + bubble.width() <= area.right() + 1
    finally:
        bubble.hide()


# ---------------------------------------------------------------- 设置 Enter
def _host_with_settings(tmp_path: Path):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import test_petcore_settings as fixtures

    return fixtures.FakeHost(tmp_path)


def test_enter_accepts_the_settings_dialog(app, tmp_path: Path) -> None:
    from petcore.ui.settings.dialog import SettingsDialog

    dialog = SettingsDialog(_host_with_settings(tmp_path))
    dialog.show()
    app.processEvents()
    dialog.nav.setFocus()
    QTest.keyClick(dialog, Qt.Key.Key_Return)
    app.processEvents()
    assert dialog.result() == QDialog.DialogCode.Accepted
    dialog.close()


def test_enter_inserts_a_newline_inside_a_multiline_editor(app, tmp_path: Path) -> None:
    from petcore.ui.settings.dialog import SettingsDialog

    dialog = SettingsDialog(_host_with_settings(tmp_path))
    dialog.show()
    app.processEvents()
    editor = QPlainTextEdit(dialog)
    editor.resize(200, 100)
    editor.show()
    editor.setFocus()
    app.processEvents()
    editor.setPlainText("第一行")
    editor.moveCursor(editor.textCursor().MoveOperation.End)
    QTest.keyClick(editor, Qt.Key.Key_Return)
    app.processEvents()
    assert "\n" in editor.toPlainText()
    assert dialog.isVisible()
    dialog.close()


# ---------------------------------------------------------------- 菜单对勾
def test_check_mark_is_drawn_on_the_row_end_layer(app) -> None:
    """勾选标记由行尾层负责（QSS indicator 只剩 1px，不给图片）。"""
    model = MenuModel()
    model.register(MenuItem("a", "开着", checked=lambda: True))
    menu = model.build()
    layer = getattr(menu, CHECK_LAYER_ATTR)
    assert isinstance(layer, MenuCheckLayer)
    assert "QMenu::indicator:checked" not in menu_stylesheet(dark=True)
    assert "QMenu::indicator:checked" not in menu_stylesheet(dark=False)


def test_menu_actions_expose_checked_state(app) -> None:
    model = MenuModel()
    model.register(MenuItem("a", "开着", checked=lambda: True))
    model.register(MenuItem("b", "关着", checked=lambda: False))
    menu = model.build()
    actions = {action.text(): action for action in menu.actions()}
    assert actions["开着"].isCheckable() and actions["开着"].isChecked()
    assert actions["关着"].isCheckable() and not actions["关着"].isChecked()


def test_checked_menu_row_paints_a_mark(app) -> None:
    """勾选行在**行尾**画出强调色标记，未勾选行不画。

    勾选标记不再走 QSS 的 `QMenu::indicator`（那会把图标列占掉），而是由
    `MenuCheckLayer` 画在行右缘——所以深浅主题同色，也不再依赖临时图片文件。
    """
    model = MenuModel()
    model.register(MenuItem("a", "第一项", checked=lambda: True))
    model.register(MenuItem("b", "第二项", checked=lambda: False))
    menu = model.build()
    menu.show()
    app.processEvents()
    layer = getattr(menu, CHECK_LAYER_ATTR)
    layer.sync()
    image = QImage(layer.size(), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    layer.render(image, QPoint(0, 0), QRegion(), QWidget.RenderFlag.DrawChildren)

    def row_of(label: str):
        return menu.actionGeometry(
            next(action for action in menu.actions() if action.text() == label)
        )

    def ink(top: int, bottom: int) -> int:
        return sum(
            1
            for y in range(top, max(top, bottom))
            for x in range(max(0, image.width() - 32), image.width())
            if image.pixelColor(x, y).alpha() > 120
        )

    first = row_of("第一项")
    second = row_of("第二项")
    try:
        assert ink(first.top(), first.bottom()) > 0
        assert ink(second.top(), second.bottom()) == 0
    finally:
        menu.close()


# ---------------------------------------------------------------- 静默与反馈
class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool]] = []

    def say(self, text: str, *, duration_ms: int = 4000, force: bool = False) -> None:
        self.calls.append(("say", text, force))

    def notify(self, title: str, message: str, *, force: bool = False) -> None:
        self.calls.append(("notify", f"{title}：{message}", force))

    def play_animation(self, name: str) -> bool:
        return False


class _QuietHost:
    def __init__(self, root: Path, quiet=None) -> None:
        from petcore.config import Config
        from petcore.ui.ports import QuietMode

        self.root = root
        self.config = Config(root)
        self.quiet = quiet if quiet is not None else QuietMode()
        self.recorder = _Recorder()

    def presentation_port(self):
        return self.recorder


class _PresentationHost:
    def __init__(self, quiet_active: bool) -> None:
        from petcore.ui.ports import QuietMode

        self.quiet = QuietMode()
        if quiet_active:
            self.quiet.set_quiet(60)
        self.window = QWidget()
        self.window.say = lambda text, duration=4000: host_said.append(text)


host_said: list[str] = []


def test_quiet_suppresses_proactive_bubbles_but_not_forced_ones(app) -> None:
    from petcore.ui.app import _PresentationAdapter

    host_said.clear()
    host = _PresentationHost(quiet_active=True)
    host.window.show()  # Delivery now truthfully refuses a hidden target.
    adapter = _PresentationAdapter(host)
    adapter.say("自动冒泡")
    assert host_said == []
    adapter.say("命令反馈", force=True)
    assert host_said == ["命令反馈"]
    adapter.notify("恢复", "好了", force=True)
    assert host_said[-1] == "恢复：好了"


def _builtin_manager(recorder: _Recorder):
    from petcore.contracts.host import HostPorts
    from petcore.mods import feature as feature_mod
    from petcore.ui.ports import QuietMode

    quiet = QuietMode()
    ports = HostPorts(presentation=recorder, quiet=quiet)
    manager = feature_mod.FeatureManager(ports)
    diagnostics = manager.load_builtin_features()
    return manager, quiet, diagnostics


def test_builtin_commands_report_even_while_quiet(app) -> None:
    recorder = _Recorder()
    manager, quiet, diagnostics = _builtin_manager(recorder)
    assert diagnostics == []

    trim = manager.command("tool.memory-trim", "trim")
    assert trim is not None
    trim()
    assert recorder.calls, "回收内存必须在气泡里报告结果"
    assert recorder.calls[-1][2] is True

    quiet.set_quiet(600)
    assert quiet.quiet_active() is True
    show = manager.command("tool.network-info", "show")
    assert show is not None
    show()
    assert recorder.calls[-1][2] is True

    quiet_cmd = manager.command("tool.quiet-mode", "quiet_30")
    assert quiet_cmd is not None
    before = len(recorder.calls)
    quiet_cmd()
    assert len(recorder.calls) == before + 1
    assert recorder.calls[-1][2] is True


def test_quiet_settings_page_has_text_state_and_actions(app, tmp_path: Path) -> None:
    from PySide6.QtWidgets import QPushButton

    recorder = _Recorder()
    manager, quiet, diagnostics = _builtin_manager(recorder)
    assert diagnostics == []
    specs = {spec.id: spec for spec in manager.settings_pages()}
    page = specs["quiet"].factory(_QuietHost(tmp_path, quiet))
    try:
        assert page.state_label.text() == "未开启"
        assert page.restore_button.isEnabled() is False
        labels = [button.text() for button in page.findChildren(QPushButton)]
        for expected in ("15 分钟", "30 分钟", "1 小时", "2 小时", "恢复提醒"):
            assert expected in labels
        button = next(b for b in page.findChildren(QPushButton) if b.text() == "15 分钟")
        button.click()
        assert quiet.quiet_active() is True
        assert "剩余" in page.state_label.text()
        assert page.restore_button.isEnabled() is True
        page.restore_button.click()
        assert quiet.quiet_active() is False
    finally:
        page.dispose()


def test_text_pages_expose_multiline_editors(app) -> None:
    recorder = _Recorder()
    manager, _quiet, diagnostics = _builtin_manager(recorder)
    assert diagnostics == []
    specs = {spec.id: spec for spec in manager.settings_pages()}
    host = _QuietHost(Path("."))
    for key in ("self_talk", "click_talk"):
        page = specs[key].factory(host)
        try:
            editors = page.findChildren(QPlainTextEdit)
            assert editors, f"{key} 的候选台词必须是大框"
            assert editors[0].minimumHeight() >= 100
        finally:
            page.dispose()


def test_two_real_pets_settle_without_interpenetration(app, tmp_path: Path) -> None:
    from petcore.config import Config
    from petcore.content.legacy import load_character
    from petcore.ui.pet_manager import PetManager

    character = load_character("shenshen", frameseq_mode="off")
    assert character is not None
    config = Config(tmp_path, {"character": "shenshen", "scale": 0.5, "no_move": True})
    host = _QuietHost(tmp_path, quiet=None)
    host.characters = lambda: [character]  # type: ignore[attr-defined]
    host.character_for = lambda _id: character  # type: ignore[attr-defined]
    host.pets = None  # type: ignore[attr-defined]
    host.build_menu = lambda parent=None: None  # type: ignore[attr-defined]
    host.open_settings = lambda *a, **k: None  # type: ignore[attr-defined]
    host.reload_mods = lambda: None  # type: ignore[attr-defined]
    host.open_data_dir = lambda: None  # type: ignore[attr-defined]
    host.quit_app = lambda: None  # type: ignore[attr-defined]
    manager = PetManager(host, tmp_path, config)
    host.pets = manager
    try:
        first = manager.create_primary(character)
        second = manager.spawn()
        assert second is not None
        second.move(first.x() + 6, first.y())
        for _ in range(40):
            manager._tick_collision()
        overlap = deepest_overlap(
            circles_from_rect(first.body_rect()), circles_from_rect(second.body_rect())
        )
        assert overlap is None
    finally:
        manager.shutdown()


def test_every_builtin_settings_page_builds(app, tmp_path: Path) -> None:
    recorder = _Recorder()
    manager, _quiet, diagnostics = _builtin_manager(recorder)
    assert diagnostics == []
    host = _QuietHost(tmp_path)
    host.characters = lambda: []  # type: ignore[attr-defined]
    built = []
    for spec in manager.settings_pages():
        page = spec.factory(host)
        try:
            assert page.title == spec.title
            built.append(spec.id)
        finally:
            page.dispose()
    for expected in (
        "self_talk",
        "click_talk",
        "quick_launch",
        "quiet",
        "links",
        "memory",
        "network",
    ):
        assert expected in built


# ---------------------------------------------------------------- 碰撞瞬移 / 撞斜
class _ImpulseWindow:
    def __init__(self, x: int = 0, y: int = 0) -> None:
        self._x = x
        self._y = y
        self._layout = None
        self.probe_active = False
        self.config = type("C", (), {"get": staticmethod(lambda _key, default=None: default)})()
        self.effects = self
        self.moves: list[tuple[int, int]] = []

    def width(self) -> int:
        return 320

    def height(self) -> int:
        return 180

    def body_local_rect(self):
        return (0, 0, 320, 180)

    def body_rect(self):
        return (self._x, self._y, 320, 180)

    def update_throw_egg(self, *_args):
        return None

    def x(self) -> int:
        return self._x

    def y(self) -> int:
        return self._y

    def move(self, x, y) -> None:
        self._x, self._y = int(x), int(y)
        self.moves.append((self._x, self._y))


def test_collision_impulse_reanchors_to_the_current_position(app) -> None:
    from petcore.ui.motion import MotionController

    window = _ImpulseWindow(0, 0)
    motion = MotionController(window, clock=lambda: 100.0)
    window.move(400, 400)  # 构造之后再移动窗口 = 旧的 _x/_y 变成过期值
    assert motion._x == 0.0 and motion._y == 0.0
    motion.set_velocity(-300.0, 0.0)
    assert motion.mode == "thrown"
    assert (motion._x, motion._y) == (400.0, 400.0)
    motion.shutdown()


def test_collision_does_not_teleport_a_real_window(app, tmp_path: Path) -> None:
    from petcore.config import Config
    from petcore.content.legacy import load_character
    from petcore.ui.pet_window import PetWindow

    character = load_character("shenshen", frameseq_mode="off")
    assert character is not None
    host = _QuietHost(tmp_path)
    host.characters = lambda: [character]  # type: ignore[attr-defined]
    host.character_for = lambda _id: character  # type: ignore[attr-defined]
    host.build_menu = lambda parent=None: None  # type: ignore[attr-defined]
    window = PetWindow(Config(tmp_path, {"character": "shenshen", "scale": 0.5}), tmp_path, host)
    window.start(character)
    try:
        window.move(400, 400)
        window._cancel_move_plan()
        app.processEvents()
        window.motion.set_velocity(-300.0, 0.0)
        assert window.motion._x == pytest.approx(400.0)
        for _ in range(6):
            app.processEvents()
        assert abs(window.x() - 400) < 260
    finally:
        window.shutdown()


def test_hit_probing_pet_exits_probe_and_stays_upright(app, tmp_path: Path) -> None:
    from petcore.config import Config
    from petcore.contracts.content import CharacterSpec
    from petcore.ui.pet_manager import PetManager

    character = CharacterSpec(id="fallback", name="Fallback", root=Path("."), media_kind="none", animations=())
    config = Config(tmp_path, {"character": "fallback", "scale": 0.5, "no_move": True})
    host = _QuietHost(tmp_path)
    host.characters = lambda: [character]  # type: ignore[attr-defined]
    host.character_for = lambda _id: character  # type: ignore[attr-defined]
    host.build_menu = lambda parent=None: None  # type: ignore[attr-defined]
    host.pets = None  # type: ignore[attr-defined]
    manager = PetManager(host, tmp_path, config)
    host.pets = manager
    try:
        primary = manager.create_primary(character)
        second = manager.spawn()
        assert second is not None
        second.move(500, 500)
        primary.set_edge_probe_enabled(True)
        primary.move(0, 300)
        app.processEvents()
        primary._edge_probe.on_release(True)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
            if primary.probe_angle() != 0.0:
                break
        assert primary.probe_active is True
        assert primary.probe_angle() != 0.0

        second.move(primary.x() + 20, primary.y())
        # 主分支口径：只有真撞击（相对接近速度 ≥ HIT_FEEDBACK_MIN_SPEED）才退出探头；
        # 这里给迎面宠物一个真实的撞击速度。
        second.motion._vx = -600.0
        app.processEvents()
        manager._tick_collision()
        app.processEvents()
        assert primary.probe_active is False
        assert primary.probe_angle() == pytest.approx(0.0)
        assert primary.probe_exposure() == pytest.approx(1.0)
        bx, _by, _bw, _bh = primary.body_rect()
        assert bx >= 0
    finally:
        manager.shutdown()


def test_collision_ticks_never_jump_a_pet_far(app, tmp_path: Path) -> None:
    from petcore.config import Config
    from petcore.content.legacy import load_character
    from petcore.ui.pet_manager import PetManager

    character = load_character("shenshen", frameseq_mode="off")
    assert character is not None
    host = _QuietHost(tmp_path)
    host.characters = lambda: [character]  # type: ignore[attr-defined]
    host.character_for = lambda _id: character  # type: ignore[attr-defined]
    host.build_menu = lambda parent=None: None  # type: ignore[attr-defined]
    host.pets = None  # type: ignore[attr-defined]
    manager = PetManager(host, tmp_path, Config(tmp_path, {"character": "shenshen", "scale": 0.5, "no_move": True}))
    host.pets = manager
    try:
        first = manager.create_primary(character)
        second = manager.spawn()
        assert second is not None
        first.move(300, 300)
        second.move(340, 300)
        first.motion.set_velocity(400.0, 0.0)
        previous = {0: first.pos(), 1: second.pos()}
        for _ in range(30):
            manager._tick_collision()
            app.processEvents()
            for window in (first, second):
                before = previous[window.slot]
                after = window.pos()
                assert abs(after.x() - before.x()) < 120
                assert abs(after.y() - before.y()) < 120
                previous[window.slot] = after
    finally:
        manager.shutdown()
