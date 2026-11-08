"""GIF frames must support the same alpha hit testing as other pet media."""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageDraw
from PySide6.QtCore import QEventLoop, QPoint, QTimer
from PySide6.QtWidgets import QApplication

from petcore.config import Config
from petcore.contracts.content import AnimationSpec, CharacterSpec
from petcore.contracts.enums import AnimatType, GraphType
from petcore.ui.pet_window import PetWindow


def test_real_gif_frame_preserves_opaque_and_transparent_input(tmp_path):
    app = QApplication.instance() or QApplication([])
    frames = []
    for color in ((100, 150, 200, 255), (101, 150, 200, 255)):
        frame = Image.new("RGBA", (640, 360))
        ImageDraw.Draw(frame).rectangle((213, 72, 533, 288), fill=color)
        frames.append(frame)
    path = tmp_path / "pet.gif"
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=1000,
                   loop=0, disposal=2)
    character = CharacterSpec("gif-input", "GIF input", tmp_path, "gif", (
        AnimationSpec("idle", GraphType.IDEL, AnimatType.B_LOOP, path=path, loop=True),
    ), body_box=(213, 72, 533, 288))
    config = Config(tmp_path / "data")
    config.set("frameseq", "off")
    config.set("anim_random_auto", False)
    host = SimpleNamespace(characters=lambda: [character], build_menu=lambda *_: None)
    pet = PetWindow(config, tmp_path / "data", host)
    loop, check, budget = QEventLoop(), QTimer(), QTimer()
    check.timeout.connect(lambda: loop.quit() if pet._image is not None else None)
    budget.setSingleShot(True)
    budget.timeout.connect(loop.quit)
    try:
        pet.start(character)
        check.start(10)
        budget.start(10000)
        if pet._image is None:
            loop.exec()
        assert pet._image is not None, "real GIF decoder did not deliver a frame"
        assert pet.is_transparent_at(QPoint(5, 5))
        assert not pet.is_transparent_at(QPoint(pet.width() // 2, pet.height() // 2))
        # The rendered alpha input image must agree with point hit testing.
        mask = pet.input_image()
        assert mask.pixelColor(5, 5).alpha() == 0
        assert mask.pixelColor(mask.width() // 2, mask.height() // 2).alpha() == 255
    finally:
        check.stop()
        budget.stop()
        pet.shutdown()
        pet.close()
        app.processEvents()
