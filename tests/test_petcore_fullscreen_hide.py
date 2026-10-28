"""全屏自动隐藏（petcore）：纯几何、状态机、配置键与设置行。

探测器在 `petcore/platform/fullscreen.py`（零 Qt），状态机在
`petcore/ui/fullscreen_hide.py`。这里全部用注入的假探测，不碰真 Win32；
只有最后一个用例走一次真实探测，锁「不抛异常 + 非 Windows 如实报不支持」。
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from petcore.platform import fullscreen as fs
from petcore.ui.fullscreen_hide import FullscreenHideController

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class Geo:
    """最小 QRect 替身：方法访问（QRect 走的就是这条路）。"""

    def __init__(self, left: int, top: int, right: int, bottom: int) -> None:
        self._edges = (left, top, right, bottom)

    def left(self) -> int:
        return self._edges[0]

    def top(self) -> int:
        return self._edges[1]

    def right(self) -> int:
        return self._edges[2]

    def bottom(self) -> int:
        return self._edges[3]


PRIMARY = (0, 0, 1920, 1080)
SECONDARY = (1920, 0, 3840, 1080)
TWO_SCREENS = [PRIMARY, SECONDARY]


# ------------------------------------------------------------------ 纯几何


def test_geometry_full_screen_without_caption_hits() -> None:
    assert fs.fullscreen_geometry_hit(0, 0, 1920, 1080, Geo(*PRIMARY), False) is True


def test_geometry_caption_without_topmost_misses() -> None:
    # 带标题栏的普通/最大化窗口（含自动隐藏任务栏）：不置顶一律不算全屏。
    assert fs.fullscreen_geometry_hit(0, 0, 1920, 1080, Geo(*PRIMARY), True) is False
    # Unity/UE 系游戏全屏保留 WS_CAPTION 但几乎必带 WS_EX_TOPMOST，置顶位兜住。
    assert fs.fullscreen_geometry_hit(0, 0, 1920, 1080, Geo(*PRIMARY), True, True) is True


def test_geometry_accepts_edges_that_are_a_pixel_or_two_small() -> None:
    """边框/圆角在不同 API 上差 1–2px：容差内算命中。"""
    assert fs.fullscreen_geometry_hit(1, 1, 1919, 1079, Geo(*PRIMARY), False) is True
    assert fs.fullscreen_geometry_hit(0, 0, 1918, 1080, Geo(*PRIMARY), False) is True
    # 超出容差（3px）就不是「铺满整屏」了。
    assert fs.fullscreen_geometry_hit(0, 0, 1917, 1080, Geo(*PRIMARY), False) is False


def test_geometry_taskbar_inset_misses() -> None:
    """任务栏（约 40px）让窗口比屏幕矮：任何容差都不能把最大化算成全屏。"""
    assert (
        fs.fullscreen_geometry_hit(0, 0, 1920, 1040, Geo(*PRIMARY), False) is False
    )


def test_multi_monitor_judges_against_the_window_own_screen() -> None:
    # 副屏整屏（无标题栏）= 命中。
    assert fs.window_covers_monitor(SECONDARY, TWO_SCREENS, False) is True
    # 副屏整屏 + 标题栏且不置顶 = 不命中（不能拿主屏的几何去凑）。
    assert fs.window_covers_monitor(SECONDARY, TWO_SCREENS, True) is False
    # 只铺满主屏，对副屏来说不是全屏。
    assert fs.window_covers_monitor(PRIMARY, [SECONDARY], False) is False
    # 跨屏窗口按相交面积最大的那块屏判断：主体在副屏 → 判副屏。
    assert fs.monitor_rect_for((1800, 0, 2600, 1080), TWO_SCREENS) == (
        float(SECONDARY[0]),
        float(SECONDARY[1]),
        float(SECONDARY[2]),
        float(SECONDARY[3]),
    )
    # 与所有屏都不相交（离屏窗口）不判命中。
    assert fs.window_covers_monitor((5000, 5000, 6000, 6000), TWO_SCREENS, False) is False


def test_platform_probe_module_is_qt_and_network_free() -> None:
    """探测层零 Qt、零联网库（架构红线的局部守卫）。"""
    path = ROOT / "petcore" / "platform" / "fullscreen.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            roots.add((node.module or "").split(".")[0])
    blocked = {"PySide6", "PyQt5", "PyQt6", "shiboken6"} | {
        "urllib",
        "http",
        "socket",
        "requests",
        "aiohttp",
        "httpx",
    }
    assert not (roots & blocked), sorted(roots & blocked)


def test_real_probe_reports_unsupported_without_raising() -> None:
    result = fs.probe_fullscreen()
    assert isinstance(result, tuple) and len(result) == 2
    hit, detail = result
    assert isinstance(hit, bool)
    assert isinstance(detail, str) and detail
    if sys.platform != "win32":
        assert hit is False
        assert "Windows" in detail
    assert fs.probe_fullscreen_hit() is hit or isinstance(fs.probe_fullscreen_hit(), bool)


# ------------------------------------------------------------------ 状态机


class FakePet:
    def __init__(self, slot: int, visible: bool = True) -> None:
        self.slot = slot
        self.visible = bool(visible)
        self.calls: list[bool] = []

    def isVisible(self) -> bool:  # noqa: N802 - 与 PetWindow 同名
        return self.visible

    def set_visible(self, visible: bool) -> None:
        self.calls.append(bool(visible))
        self.visible = bool(visible)


class FakeProbe:
    def __init__(self, hit: bool = False) -> None:
        self.hit = hit
        self.calls = 0
        self.error: Exception | None = None

    def __call__(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return (self.hit, "fake probe")


def _controller(pets, probe, *, supported: bool = True):
    return FullscreenHideController(
        lambda: list(pets), probe=probe, supported=supported, interval_ms=60_000
    )


def test_hit_hides_every_visible_pet_and_leave_restores_only_those(app) -> None:
    a, b, hidden = FakePet(0), FakePet(1), FakePet(2, visible=False)
    pets = [a, b, hidden]
    probe = FakeProbe(False)
    ctrl = _controller(pets, probe)
    try:
        ctrl.set_enabled(True)
        assert probe.calls == 1, "开启后立刻探一拍（启动时已在全屏不该先露一下）"

        probe.hit = True
        ctrl.poll()
        assert [a.visible, b.visible, hidden.visible] == [False, False, False]
        assert a.calls == [False] and b.calls == [False]
        assert hidden.calls == [], "进入全屏前就隐藏的宠不归我们管"
        assert sorted(ctrl.hidden_slots()) == [("slot", 0), ("slot", 1)]

        probe.hit = False
        ctrl.poll()
        assert [a.visible, b.visible, hidden.visible] == [True, True, False]
        assert ctrl.hidden_slots() == []
    finally:
        ctrl.stop()


def test_pet_hidden_by_user_stays_hidden_after_leaving_fullscreen(app) -> None:
    shown, user_hidden = FakePet(0), FakePet(1, visible=False)
    probe = FakeProbe(True)
    ctrl = _controller([shown, user_hidden], probe)
    try:
        ctrl.set_enabled(True)  # 首拍即全屏命中
        assert shown.visible is False
        probe.hit = False
        ctrl.poll()
        assert shown.visible is True
        assert user_hidden.visible is False
        assert user_hidden.calls == []
    finally:
        ctrl.stop()


def test_user_show_during_fullscreen_is_not_hidden_again(app) -> None:
    a = FakePet(0)
    probe = FakeProbe(True)
    ctrl = _controller([a], probe)
    try:
        ctrl.set_enabled(True)
        assert a.visible is False
        a.set_visible(True)  # 用户在全屏期间手动显示
        ctrl.poll()
        ctrl.poll()
        assert a.visible is True
        assert a.calls == [False, True]
        assert ctrl.hidden_slots() == []
        probe.hit = False
        ctrl.poll()
        assert a.visible is True, "离开全屏不该动用户刚显示的宠"
        assert a.calls == [False, True]
    finally:
        ctrl.stop()


def test_pet_shown_by_user_during_fullscreen_after_being_hidden_stays_visible(app) -> None:
    """全屏前就隐藏、全屏期间被用户显示的宠：不许抢回去，也不许随离开再动它。"""
    shown, other = FakePet(0), FakePet(1, visible=False)
    probe = FakeProbe(True)
    ctrl = _controller([shown, other], probe)
    try:
        ctrl.set_enabled(True)
        assert shown.visible is False and other.visible is False
        other.set_visible(True)  # 用户在全屏期间显示另一只
        ctrl.poll()
        assert other.visible is True
        assert ctrl.hidden_slots() == [("slot", 0)]
        probe.hit = False
        ctrl.poll()
        assert shown.visible is True and other.visible is True
    finally:
        ctrl.stop()


def test_user_show_then_hide_during_fullscreen_is_not_reopened(app) -> None:
    a = FakePet(0)
    probe = FakeProbe(True)
    ctrl = _controller([a], probe)
    try:
        ctrl.set_enabled(True)
        a.set_visible(True)  # 用户显示
        ctrl.poll()  # 让位：记成 override
        a.set_visible(False)  # 用户又隐藏
        probe.hit = False
        ctrl.poll()
        assert a.visible is False, "用户自己隐藏的宠不许在全屏结束时被打开"
    finally:
        ctrl.stop()


def test_repeated_hits_are_idempotent(app) -> None:
    a = FakePet(0)
    probe = FakeProbe(True)
    ctrl = _controller([a], probe)
    try:
        ctrl.set_enabled(True)
        for _ in range(4):
            ctrl.poll()
        assert a.calls == [False], "重复命中不许反复 set_visible"
    finally:
        ctrl.stop()


def test_pet_spawned_during_fullscreen_is_hidden_and_restored(app) -> None:
    a, b = FakePet(0), FakePet(1)
    probe = FakeProbe(True)
    pets = [a]
    ctrl = _controller(pets, probe)
    try:
        ctrl.set_enabled(True)
        assert a.visible is False
        pets.append(b)  # 全屏期间新生成的宠
        ctrl.poll()
        assert b.visible is False
        assert len(ctrl.hidden_slots()) == 2
        probe.hit = False
        ctrl.poll()
        assert a.visible is True and b.visible is True
    finally:
        ctrl.stop()


def test_pet_closed_during_fullscreen_is_forgotten(app) -> None:
    a, b = FakePet(0), FakePet(1)
    probe = FakeProbe(True)
    pets = [a, b]
    ctrl = _controller(pets, probe)
    try:
        ctrl.set_enabled(True)
        pets.remove(b)  # 全屏期间被关掉
        ctrl.poll()
        assert ctrl.hidden_slots() == [("slot", 0)]
        probe.hit = False
        ctrl.poll()
        assert a.visible is True
    finally:
        ctrl.stop()


def test_disabling_while_hidden_restores_immediately(app) -> None:
    a = FakePet(0)
    probe = FakeProbe(True)
    ctrl = _controller([a], probe)
    try:
        ctrl.set_enabled(True)
        assert a.visible is False
        ctrl.set_enabled(False)
        assert a.visible is True, "关掉开关不许滞留隐藏态"
        assert ctrl.timer_active() is False
    finally:
        ctrl.stop()


def test_disabled_or_unsupported_never_polls(app) -> None:
    a = FakePet(0)
    probe = FakeProbe(True)
    ctrl = _controller([a], probe, supported=True)
    try:
        ctrl.poll()
        assert probe.calls == 0, "未开启时手动 poll 也不许探测"
        assert ctrl.timer_active() is False
        ctrl.set_enabled(True)
        assert ctrl.timer_active() is True
        probe.hit = False
        ctrl.set_enabled(False)
        calls = probe.calls
        ctrl.poll()
        assert probe.calls == calls, "关掉开关后不再轮询"
        assert ctrl.timer_active() is False
    finally:
        ctrl.stop()

    unsupported_probe = FakeProbe(True)
    pet = FakePet(0)
    unsupported = _controller([pet], unsupported_probe, supported=False)
    try:
        unsupported.set_enabled(True)
        unsupported.poll()
        assert unsupported_probe.calls == 0, "非 Windows 平台不轮询"
        assert unsupported.timer_active() is False
        assert pet.visible is True
    finally:
        unsupported.stop()


def test_poll_interval_is_one_second(app) -> None:
    """1Hz 是硬要求：更快的轮询白烧 CPU，更慢的全屏退出后恢复迟钝。"""
    ctrl = FullscreenHideController(
        lambda: [FakePet(0)], probe=FakeProbe(False), supported=True
    )
    try:
        assert ctrl._timer.interval() == 1000  # noqa: SLF001 - 只验默认节拍
    finally:
        ctrl.stop()


def test_probe_failure_keeps_state(app) -> None:
    a = FakePet(0)
    probe = FakeProbe(True)
    ctrl = _controller([a], probe)
    try:
        ctrl.set_enabled(True)
        assert a.visible is False
        probe.error = RuntimeError("boom")
        ctrl.poll()
        assert a.visible is False, "探测异常不翻转状态，也不抛给宿主"
    finally:
        ctrl.stop()


# ------------------------------------------------------------------ 配置键


def test_config_key_defaults_true_and_normalizes(tmp_path: Path) -> None:
    from petcore.config import DEFAULTS, GLOBAL_CONFIG_KEYS, Config

    assert DEFAULTS["auto_hide_fullscreen"] is True
    assert "auto_hide_fullscreen" in GLOBAL_CONFIG_KEYS, "进程级开关必须是全局键"
    config = Config.load(tmp_path)
    assert config.get("auto_hide_fullscreen") is True
    config.set("auto_hide_fullscreen", False)
    config.save()
    assert Config.load(tmp_path).get("auto_hide_fullscreen") is False
    # 脏值按默认 True 收敛（布尔归一，不让 "yes"/0 之类混进配置）。
    config.set("auto_hide_fullscreen", "yes")
    assert config.get("auto_hide_fullscreen") is True


# ------------------------------------------------------------------ 宿主接线


def test_core_host_wires_one_shared_controller(app, tmp_path: Path, monkeypatch) -> None:
    """接线：CoreHost 只建一个控制器，配置门关时零定时器，开时起表并探一拍。"""
    from petcore.config import Config
    from petcore.ui.app import CoreHost
    from petcore.ui.fullscreen_hide import FullscreenHideController
    from petcore.ui.pet_manager import PetManager

    # 只跳过真实角色素材扫描：CoreHost / PetManager 都是真的（同 click_listeners）。
    monkeypatch.setattr("petcore.content.collect.load_all", lambda **_kw: [])
    config = Config(tmp_path)
    config.set("auto_hide_fullscreen", False)
    host = CoreHost(app, config, tmp_path, ())
    manager = PetManager(host, tmp_path, config)
    host.pets = manager
    character = host.character_for("fallback")
    host.window = manager.create_primary(character)
    host.attach_window(host.window)
    try:
        host.start_fullscreen_hide()
        controller = host.fullscreen_hide
        assert isinstance(controller, FullscreenHideController)
        assert controller.timer_active() is False, "开关关闭时不许留常驻定时器"

        host.start_fullscreen_hide()  # 幂等：重载扩展会再次调用
        assert host.fullscreen_hide is controller, "多宠共用一个控制器"

        probe = FakeProbe(False)
        controller._probe = probe  # noqa: SLF001 - 不让测试读真实前台
        host.set_auto_hide_fullscreen(True)
        assert config.get("auto_hide_fullscreen") is True
        assert controller.timer_active() is True
        assert probe.calls == 1

        host.set_auto_hide_fullscreen(False)
        assert controller.timer_active() is False
        calls = probe.calls
        controller.poll()
        assert probe.calls == calls
    finally:
        host.stop_fullscreen_hide()
        if host.features is not None:
            host.features.stop()
        manager.shutdown()


# ------------------------------------------------------------------ 设置行


def test_general_page_has_fullscreen_row_and_writes_config(app, tmp_path: Path) -> None:
    from petcore.ui.settings.base import page_search_texts
    from petcore.ui.settings.pages_core import GeneralPage
    from tests.test_petcore_settings import _host

    host = _host(tmp_path)
    applied: list[int] = []
    host.apply_fullscreen_hide = lambda *a, **k: applied.append(1)  # type: ignore[attr-defined]
    page = GeneralPage(host)
    try:
        box = page.auto_hide_fullscreen_check
        assert box.isChecked() is True
        if sys.platform != "win32":
            assert box.isEnabled() is False
            pytest.skip("非 Windows 不提供该开关")
        assert box.isEnabled() is True
        texts = page_search_texts(page)
        assert any("全屏" in text for text in texts)
        assert any("全屏游戏或视频期间自动隐藏桌宠" in text for text in texts)
        assert "全屏" in page.keywords()

        assert page.dirty() is False
        box.setChecked(False)
        assert host.config.get("auto_hide_fullscreen") is False
        assert applied, "勾选必须实时作用到宿主"
        assert page.dirty() is True

        page.discard_changes()
        assert host.config.get("auto_hide_fullscreen") is True
        assert box.isChecked() is True
        assert page.dirty() is False

        box.setChecked(False)
        page.confirm_save()
        assert host.config.get("auto_hide_fullscreen") is False
    finally:
        page.deleteLater()
