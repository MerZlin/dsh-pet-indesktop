# -*- coding: utf-8 -*-
"""OverlayShell.eventFilter 快路径回归（offscreen）：应用级 Esc 兜底契约 + 零白跑。

背景（实机 py-spy，120s 窗口、3 sprite 持续运动）：该过滤器由
``self.app.installEventFilter(self)``（overlay_shell.py:2501）注册在
QApplication 上，每个事件（110Hz 重绘 + 定时器 + 输入，每秒数千个）都要过
一遍 Python，占 GUI 线程 9.4%——而其中只有「弹弓瞄准中按 Esc」才需要动手。
本轮把它改成快路径：非 KeyPress 第一行直接 ``return False``，不再走
``super().eventFilter`` 的 C++ 往返；``QObject.eventFilter`` 基类实现恒返回
False（Qt 文档：The default implementation always returns false），故消费
语义逐位不变。

本文件锁两件事：
1. **契约不变**：真 PetSprite + 真鼠标事件经 overlay 进瞄准后，Esc 经
   eventFilter 取消会话并消费事件；非瞄准 / 非 Esc / 非按键一律不消费；
2. **快路径确实生效**：非 KeyPress（以及不消费的 KeyPress）不得触达
   ``QObject.eventFilter`` 基类实现——monkeypatch 计数，改前红、改后绿。

纪律：offscreen 真 QApplication，同步直调事件处理器，不 sleep 赌时序
（AGENTS.md 时序测试）；假屏/假库沿用 test_overlay_shell 的装配（同一份
契约，不复制实现）。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt, Signal
from PySide6.QtGui import QImage, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication

from pet.overlay_shell import OverlayShell
from pet.pet_sprite import INTERACTION_NORMAL, PetSprite
from tests.test_overlay_shell import FakeInstance, FakeLibrary, FakeScreen

app = QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- 可按素材：真 PetSprite
class _FakeClip(QObject):
    """最小 clip：一帧不透明图（命中测试要真 alpha，故不能用 FakeSprite）。"""

    frameChanged = Signal(int)
    finished = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.frame = 0
        self.image = QImage(640, 360, QImage.Format.Format_ARGB32)
        self.image.fill(0xFF112233)

    def currentFrameNumber(self):
        return self.frame

    def currentImage(self):
        return self.image

    def frameCount(self):
        return 1

    def start(self):
        return True

    def stop(self):
        return True


class _ClipLibrary(FakeLibrary):
    """在 FakeLibrary 之上补 ``movie``/``no_mirror``（sprite 绑 clip 的两处依赖）。"""

    def __init__(self) -> None:
        super().__init__()
        self.no_mirror: set[str] = set()
        self._clip = _FakeClip()

    def movie(self, name, **_kw):
        return self._clip


class _ClipInstance(FakeInstance):
    """用可按素材建库的实例（其余契约同 FakeInstance）。"""

    def _create_library(self, character_id):
        self.created_character_ids.append(character_id)
        return _ClipLibrary()


def _make_shell() -> OverlayShell:
    """真 sprite 的 OverlayShell（sprite_factory 走产品默认构造）。"""
    return OverlayShell(
        app, _ClipInstance(),
        screen=FakeScreen((0, 0, 1920, 1080), (0, 0, 1920, 1040)),
        sprite_factory=lambda lib, pos, scale: PetSprite(lib, pos=pos, scale=scale))


def _mouse(etype, pos, button=Qt.MouseButton.LeftButton, buttons=None):
    btns = button if buttons is None else buttons
    point = QPointF(float(pos[0]), float(pos[1]))
    return QMouseEvent(etype, point, point, button, btns,
                       Qt.KeyboardModifier.NoModifier)


def _right_press(pos):
    return _mouse(QEvent.Type.MouseButtonPress, pos, Qt.MouseButton.RightButton,
                  Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)


def _key(key: Qt.Key) -> QKeyEvent:
    return QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)


def _enter_aim(shell: OverlayShell):
    """真鼠标链路进瞄准：按下 → 拖动 → 右键；返回 (光标点, 锚点位置)。"""
    overlay = shell.overlay
    sprite = shell.sprite
    sprite.bind_clip("idle")
    sprite._rebuild_pixmap()
    center = sprite.rect().center()
    overlay.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress,
                                   (center.x(), center.y())))
    assert overlay._mouse_grab is sprite
    moved = QPoint(center.x() + 24, center.y())
    overlay.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, (moved.x(), moved.y()),
                                  Qt.MouseButton.NoButton,
                                  Qt.MouseButton.LeftButton))
    overlay.mousePressEvent(_right_press((moved.x(), moved.y())))
    assert overlay.slingshot.aiming, "真鼠标链路进瞄准失败"
    return moved, QPointF(sprite.pos)


# ---------------------------------------------------------------- 契约：Esc 取消
def test_escape_during_aim_cancels_and_consumes_event():
    """瞄准中 Esc 经应用级过滤器取消会话（消费事件），回锚点 + 收 grab。"""
    shell = _make_shell()
    overlay = shell.overlay
    sprite = shell.sprite
    moved, anchored = _enter_aim(shell)

    # 先拉一段，制造非零 progress（取消路径里的回弹反馈分支）
    overlay.mouseMoveEvent(_mouse(QEvent.Type.MouseMove,
                                  (moved.x() - 60, moved.y() - 40),
                                  Qt.MouseButton.NoButton,
                                  Qt.MouseButton.LeftButton))
    assert overlay.slingshot.pull != QPointF(0, 0)

    assert shell.eventFilter(None, _key(Qt.Key.Key_Escape)) is True

    assert overlay.slingshot.aiming is False
    assert overlay.slingshot.sprite is None
    assert sprite.pos == anchored                    # 回锚点（resume_drag=False）
    assert sprite.interaction_state == INTERACTION_NORMAL
    assert overlay._mouse_grab is None               # 会话结束：grab 收尾
    assert overlay._press_global is None


def test_escape_without_aim_is_not_consumed():
    shell = _make_shell()
    assert shell.overlay.slingshot.aiming is False
    assert shell.eventFilter(None, _key(Qt.Key.Key_Escape)) is False


def test_non_escape_key_is_not_consumed_and_keeps_aim():
    shell = _make_shell()
    overlay = shell.overlay
    _enter_aim(shell)
    assert shell.eventFilter(None, _key(Qt.Key.Key_A)) is False
    assert overlay.slingshot.aiming is True


def test_escape_tolerates_overlay_without_slingshot():
    """无 overlay（构造期/已拆壳）时 Esc 不炸、不消费。"""
    shell = _make_shell()
    # 无 overlay 的形态 = 没有宿主（构造期/已拆壳）：``shell.overlay`` 由宿主派生
    shell._hosts = []
    assert shell.eventFilter(None, _key(Qt.Key.Key_Escape)) is False


# ---------------------------------------------------------------- 非 KeyPress：零副作用
@pytest.mark.parametrize("etype", [QEvent.Type.Paint, QEvent.Type.Timer,
                                   QEvent.Type.MouseMove, QEvent.Type.ApplicationActivate])
def test_non_keypress_during_aim_has_no_side_effect(etype):
    """瞄准中的非按键事件：不消费、不取消会话、sprite 不动。"""
    shell = _make_shell()
    overlay = shell.overlay
    sprite = shell.sprite
    _enter_aim(shell)
    pos_before = QPointF(sprite.pos)

    assert shell.eventFilter(None, QEvent(etype)) is False

    assert overlay.slingshot.aiming is True
    assert sprite.pos == pos_before
    assert sprite.interaction_state != INTERACTION_NORMAL


# ---------------------------------------------------------------- 快路径：不触达基类
@pytest.mark.parametrize("make_event", [
    lambda: QEvent(QEvent.Type.Paint),
    lambda: QEvent(QEvent.Type.Timer),
    lambda: _key(Qt.Key.Key_A),
    lambda: _key(Qt.Key.Key_Escape),   # 未瞄准：不消费 → 必须走快路径
])
def test_unconsumed_event_never_reaches_base_implementation(monkeypatch, make_event):
    """不消费的事件必须在 Python 侧就地返回 False，不构造 super() 的 C++ 往返。

    基类 ``QObject.eventFilter`` 恒返回 False，故替换成 return False 行为等价；
    这里把基类换成「记录 + 返回 True」的探针：一旦被触达，返回值会变成 True、
    计数非零——改前红、改后绿。
    """
    shell = _make_shell()
    base_calls: list = []
    monkeypatch.setattr(
        QObject, "eventFilter",
        lambda self, watched, event: (base_calls.append(event), True)[1])

    assert shell.eventFilter(None, make_event()) is False
    assert base_calls == [], "非消费路径触达了 QObject.eventFilter 基类实现"


def test_consumed_escape_also_skips_base_implementation(monkeypatch):
    """消费路径（瞄准中 Esc）同样不触达基类：返回 True 且计数为空。"""
    shell = _make_shell()
    _enter_aim(shell)
    base_calls: list = []
    monkeypatch.setattr(
        QObject, "eventFilter",
        lambda self, watched, event: (base_calls.append(event), True)[1])

    assert shell.eventFilter(None, _key(Qt.Key.Key_Escape)) is True
    assert base_calls == []
