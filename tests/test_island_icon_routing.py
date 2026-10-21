# -*- coding: utf-8 -*-
"""子项1 回归：灵动岛"鱼本体头像"的 overlay 拓扑路由（offscreen）。

根因（实机截图）：overlay 拓扑下 ``instances[].win`` 恒为 None（桌宠在
OverlayShell 里），``AppShell._island_icon_pixmap`` 只遍历 PetWindow →
provider 恒返回 None → 岛只能画蓝点默认图标。

修法与契约：
- overlay 分支改从 ``OverlayShell.icon_pixmap``（主 sprite 当前帧 → idle 首帧
  → 裁剪/缩放）取图；无帧仍返回 ``None``，岛侧按 provider 契约稍后重试
  （``dynamic_island._icon_pixmap`` 明确不缓存 None）；
- legacy 窗口分支逐行不变（有 OverlayShell 在场也不读它）。

纪律：offscreen 真 QApplication；假屏/假 sprite/假库沿用 test_overlay_shell
的装配（同一份契约，不复制实现）；不 sleep、不赌时序。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from pet.app import AppShell
from tests.test_overlay_shell import _make_shell

app = QApplication.instance() or QApplication([])


def _pixmap(size: tuple[int, int] = (96, 64), color=Qt.GlobalColor.red) -> QPixmap:
    pm = QPixmap(*size)
    pm.fill(color)
    return pm


class _FakeWin:
    """legacy PetWindow 图标面替身（只读 ``icon_pixmap(size)``）。"""

    def __init__(self, pm: QPixmap) -> None:
        self._pm = pm
        self.calls: list[int] = []

    def icon_pixmap(self, size: int = 64) -> QPixmap:
        self.calls.append(size)
        return self._pm


class _Inst:
    """PetInstance 桩（只读 ``win``）。"""

    def __init__(self, win) -> None:
        self.win = win


def _app_shell_stub(instances, overlay_shell) -> AppShell:
    """最小 AppShell 桩：__new__ 绕过完整初始化，只接岛图标链路。"""
    shell = AppShell.__new__(AppShell)
    shell._instances = instances
    shell._overlay_shell = overlay_shell
    return shell


# ---------------------------------------------------------------- OverlayShell 图标面
def test_overlay_shell_icon_pixmap_from_sprite_current_frame():
    """主 sprite 当前帧 → 裁透明留白 + 等比缩放到请求尺寸。"""
    shell, _, _ = _make_shell()
    shell.sprite._pixmap = _pixmap()
    pm = shell.icon_pixmap(48)
    assert pm is not None and not pm.isNull()
    assert max(pm.width(), pm.height()) == 48


def test_overlay_shell_icon_pixmap_none_without_frame():
    """帧未就绪返回 None（不是空 QPixmap）——岛侧据此重试。"""
    shell, _, _ = _make_shell()  # FakeLibrary.names() == [] → 无 idle 首帧
    assert getattr(shell.sprite, "_pixmap", None) is None
    assert shell.icon_pixmap(64) is None


def test_overlay_shell_icon_pixmap_falls_back_to_idle_first_frame():
    """无当前帧时回退 idle 首帧（``_tray_icon`` 原有取图链原样保留）。"""
    from tests.test_overlay_shell import FakeInstance, FakeLibrary

    class _StubClip:
        def __init__(self, pm):
            self._pm = pm

        def currentPixmap(self):
            return self._pm

    class _IdleLibrary(FakeLibrary):
        def __init__(self, pm):
            super().__init__()
            self.manifest = {"idle": "x"}
            self._pm = pm

        def names(self):
            return ["x"]

        def movie(self, name, **_kw):
            return _StubClip(self._pm)

    instance = FakeInstance()
    instance._create_library = lambda character_id: _IdleLibrary(_pixmap((80, 40)))
    shell, _, _ = _make_shell(instance=instance)

    pm = shell.icon_pixmap(64)

    assert pm is not None and not pm.isNull()
    assert max(pm.width(), pm.height()) == 64


def test_tray_icon_reuses_same_source_and_falls_back():
    """托盘图标提炼后仍取同一取图链，取不到才回退系统标准图标。"""
    shell, _, _ = _make_shell()
    fallback = shell._tray_icon()
    assert not fallback.isNull()  # 无帧：系统标准图标（旧行为）
    shell.sprite._pixmap = _pixmap()
    assert not shell._tray_icon().isNull()  # 有帧：鱼本体头像


# ---------------------------------------------------------------- AppShell provider 路由
def test_island_provider_routes_to_overlay_shell():
    """overlay 拓扑（instances[].win 全 None）→ 从壳的主 sprite 取头像。"""
    shell, _, _ = _make_shell()
    shell.sprite._pixmap = _pixmap()
    app_shell = _app_shell_stub([_Inst(win=None)], shell)

    pm = app_shell._island_icon_pixmap()

    assert pm is not None and not pm.isNull()


def test_island_provider_none_while_frames_not_ready():
    """壳里也没有帧 → None（岛侧 provider 契约：不缓存失败，稍后再问）。"""
    shell, _, _ = _make_shell()
    app_shell = _app_shell_stub([_Inst(win=None)], shell)

    assert app_shell._island_icon_pixmap() is None


def test_island_provider_legacy_window_branch_unchanged():
    """legacy 分支逐行不变：仍只读 PetWindow，且在有壳在场时优先级不变。"""
    win = _FakeWin(_pixmap((10, 10)))
    overlay_shell = _make_shell()[0]
    overlay_shell.sprite._pixmap = _pixmap()  # 有也不该被读
    app_shell = _app_shell_stub([_Inst(win=win)], overlay_shell)

    assert app_shell._island_icon_pixmap() is win._pm
    assert win.calls == [64]


def test_island_consumes_overlay_provider_end_to_end(tmp_path):
    """岛侧真消费：接上路由后的 provider，``_icon_pixmap()`` 拿得到头像。"""
    from tests.test_dynamic_island_revamp import _island

    shell, _, _ = _make_shell()
    shell.sprite._pixmap = _pixmap()
    app_shell = _app_shell_stub([_Inst(win=None)], shell)
    island = _island(tmp_path, icon="auto")
    try:
        island.set_icon_provider(app_shell._island_icon_pixmap)
        pm = island._icon_pixmap()
        assert pm is not None and not pm.isNull()
    finally:
        island.hide()
        island.deleteLater()
