# -*- coding: utf-8 -*-
"""
Smoke test for the GIF-backed media layer + window behavior（真实 GIF 素材，无需外部工具）。

Run: python tests/smoke.py
"""

from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402

from pet import catalog  # noqa: E402
from pet.config import Config  # noqa: E402
from pet.library import MovieLibrary  # noqa: E402
from pet.window import PetWindow  # noqa: E402


def _wait_until(app: QApplication, predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timeout waiting for condition")


def main() -> int:
    app = QApplication([])
    lib = MovieLibrary()  # 真实 GIF：assets/animations

    # 1. 51 段素材全量可加载，帧数/时长有效
    names = lib.names()
    assert len(names) == 51, len(names)
    for name in names:
        assert lib.frames(name) >= 1, (name, lib.frames(name))
        assert lib.duration(name) > 0, (name, lib.duration(name))

    # 2. 透明通道：待机首帧同时含透明与不透明像素
    idle = lib.movie(catalog.IDLE)
    idle.jumpToFrame(0)
    img = idle.currentPixmap().toImage()
    alphas = set()
    for x in range(0, img.width(), 20):
        for y in range(0, img.height(), 10):
            alphas.add(img.pixelColor(x, y).alpha())
    assert len(alphas) >= 2, sorted(alphas)

    # 3. 播放推进
    idle.start()
    _wait_until(app, lambda: idle.currentFrameNumber() >= 1)
    assert idle.currentFrameNumber() >= 1
    idle.stop()

    # 4. 窗口实例化：尺寸/初始动画/透明 mask
    cfg = Config(base=os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp_cfg"))
    win = PetWindow(lib, cfg)
    win.show()
    assert win.anim == catalog.IDLE
    assert win.mask() is not None and not win.mask().isNull()
    assert win.width() == int(round(catalog.CANVAS_W * win.scale))
    assert win.height() == int(round((catalog.CANVAS_H + catalog.PAD) * win.scale))

    # 5. 缩放：底边不动
    bottom = win.geometry().bottom()
    win.change_scale(1.25)
    assert win.geometry().bottom() == bottom
    assert win.width() == int(round(catalog.CANVAS_W * 1.25))
    win.change_scale(1.0)

    # 6. 点击回应：仅待机时可点；播完回待机缓冲
    win._on_click()
    assert win.anim in catalog.CLICKS, win.anim
    win._on_anim_ended(win.anim)
    assert win.anim == catalog.IDLE

    # 7. 转向：东张西望播完翻转朝向
    facing_before = win.facing
    win._switch(catalog.TURN)
    win._on_anim_ended(catalog.TURN)
    assert win.facing != facing_before

    # 8. 移动：空间足够则生成移动计划并推进插值
    win._cancel_move()
    ok = win._try_move()
    assert isinstance(ok, bool)
    if ok:
        assert win._move_plan is not None
        x0 = win.x()
        win._on_move_tick()
        assert win.x() in (x0, win._move_plan["target_x"])  # 前后 2s 内位置不动或已到位
        win._cancel_move()

    # 9. 「不移动」：状态机不再进入移动动画；手动移动仍可走动；开关持久化
    win.set_no_move(True)
    assert win.no_move is True and cfg.get("no_move") is True
    for _ in range(200):
        win._cancel_move()
        win._pick_next()
        assert win.anim not in catalog.MOVES, win.anim
    win._cancel_move()
    win._trigger_move(catalog.MOVES[0])
    assert win.anim in catalog.MOVES, win.anim
    win._cancel_move()
    win.set_no_move(False)
    assert win.no_move is False and cfg.get("no_move") is False

    win.close()
    print("\n=== ALL SMOKE TESTS PASSED ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())