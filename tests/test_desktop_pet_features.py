from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def test_speech_bubble_never_accepts_focus():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from pet.speech_bubble import PetSpeechBubble

    app = QApplication.instance() or QApplication([])
    bubble = PetSpeechBubble()
    assert bubble.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert bubble.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert bubble.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    bubble.close()
    app.processEvents()


def test_self_talk_bubble_presets_have_distinct_visuals_and_safe_positions():
    from PySide6.QtCore import QRect, QSize

    from pet.speech_bubble import (
        BUBBLE_STYLE_PRESETS,
        bubble_rect_for_anchor,
    )

    assert list(BUBBLE_STYLE_PRESETS) == [
        "classic_top", "paper_left", "glass_right", "soft_blue_top", "breath_bubble",
    ]
    assert len({preset["background"] for preset in BUBBLE_STYLE_PRESETS.values()}) == 5
    assert {preset["placement"] for preset in BUBBLE_STYLE_PRESETS.values()} == {
        "top", "top_left", "top_right",
    }
    available = QRect(0, 0, 1440, 900)
    pet = QRect(620, 420, 220, 260)
    for preset in BUBBLE_STYLE_PRESETS.values():
        bubble = bubble_rect_for_anchor(pet, QSize(240, 92), available, preset["placement"])
        assert available.contains(bubble)
        assert not bubble.intersects(pet)


def test_breath_bubble_uses_organic_water_shape_and_detached_trailing_bubbles():
    import math

    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QApplication

    from pet.speech_bubble import PetSpeechBubble

    app = QApplication.instance() or QApplication([])
    bubble = PetSpeechBubble(style_id="breath_bubble")
    bubble.show_text("今天也要认真工作呀。", QRect(420, 460, 180, 240), 5000)
    app.processEvents()
    assert bubble._preset["shape"] == "breath_bubble"
    assert not bubble._main_bubble_path.isEmpty()
    assert len(bubble._breath_paths) == 2
    assert all(not path.isEmpty() for path in bubble._breath_paths)
    assert bubble._water_fill == "#d9f2fb"
    assert bubble._shadow_alpha == 0
    main = bubble._main_bubble_path.boundingRect()
    assert 1.17 <= main.width() / main.height() <= 1.27
    assert bubble._water_start_ratio <= 0.50
    assert bubble._highlight_width_ratios[0] <= 0.16
    assert bubble._highlight_width_ratios[1] <= 0.05

    def edge_distance(first, second):
        first_points = [first.pointAtPercent(index / 160) for index in range(161)]
        second_points = [second.pointAtPercent(index / 160) for index in range(161)]
        return min(
            math.hypot(left.x() - right.x(), left.y() - right.y())
            for left in first_points for right in second_points
        )

    first_gap = edge_distance(bubble._main_bubble_path, bubble._breath_paths[0])
    second_gap = edge_distance(bubble._breath_paths[0], bubble._breath_paths[1])
    assert 2.0 <= first_gap <= 7.0
    assert 2.0 <= second_gap <= 7.0
    assert abs(first_gap - second_gap) <= 2.0
    assert all(path.elementCount() >= 10 for path in bubble._breath_paths)
    bubble.close()
    app.processEvents()


def test_breath_bubble_size_tracks_the_visible_pet_without_dominating_it():
    from PySide6.QtCore import QRect, QSize
    from PySide6.QtWidgets import QApplication

    from pet.speech_bubble import (
        PetSpeechBubble,
        breath_bubble_size_for_anchor,
        breath_bubble_size_for_scale,
    )

    app = QApplication.instance() or QApplication([])
    small_pet = QRect(420, 460, 180, 240)
    large_pet = QRect(420, 460, 360, 480)

    assert breath_bubble_size_for_anchor(small_pet) == QSize(168, 137)
    assert breath_bubble_size_for_anchor(large_pet) == QSize(216, 176)
    scale_sizes = [breath_bubble_size_for_scale(value) for value in (0.5, 0.72, 0.85, 1.0)]
    assert [size.width() for size in scale_sizes] == sorted({size.width() for size in scale_sizes})

    bubble = PetSpeechBubble(style_id="breath_bubble")
    bubble.show_text("今天也要认真工作呀。", small_pet, 5000)
    app.processEvents()
    assert bubble.size() == QSize(168, 137)
    assert bubble.width() <= small_pet.width()
    # offscreen QPA shifts windows on show(); assert the placement logic
    # directly so the visual gap contract (7px design) is platform-stable.
    bubble._place(small_pet)
    visible_bubble_bottom = bubble.y() + bubble._surface_path.boundingRect().bottom()
    visual_gap = small_pet.top() - visible_bubble_bottom
    assert 5 <= visual_gap <= 9
    visible_surface = bubble._surface_path.translated(bubble.x(), bubble.y())
    pet_path = bubble._surface_path.__class__()
    pet_path.addRect(small_pet)
    assert visible_surface.intersected(pet_path).isEmpty()
    assert 1.17 <= bubble._main_bubble_path.boundingRect().width() / bubble._main_bubble_path.boundingRect().height() <= 1.27
    bubble.close()
    app.processEvents()


def test_breath_bubble_reflows_on_pet_scale_change_and_summarizes_markdown():
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QApplication

    from pet.speech_bubble import PetSpeechBubble

    app = QApplication.instance() or QApplication([])
    pet = QRect(420, 460, 220, 260)
    bubble = PetSpeechBubble(style_id="breath_bubble")
    bubble.show_text(
        "## 小狐狸的星空故事\n森林边有一棵老树，树洞里住着一只小狐狸。"
        "每天傍晚它都会抬头看星星，并把看到的故事记在叶片上。"
        "它把叶片一片片夹进树洞深处的旧书里，等月亮升起来的时候，"
        "就坐在树根上给路过的旅人讲这些星星的故事，讲着讲着自己也睡着了。" * 2,
        pet,
        5000,
        pet_scale=0.5,
    )
    app.processEvents()
    small_width = bubble.width()
    assert "##" not in bubble.label.text()
    assert bubble.label.text().count("\n") <= 5  # 长文本放宽到 6 行
    assert bubble.label.text().endswith("…")  # 超出 6 行仍省略收尾

    bubble.reflow(pet, pet_scale=1.0)
    app.processEvents()
    assert bubble.width() > small_width
    assert bubble.label.pixmap().isNull()
    bubble.close()
    app.processEvents()


def test_breath_bubble_keeps_its_shape_but_adapts_to_text_and_image_content(tmp_path):
    from PIL import Image
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QApplication

    from pet.speech_bubble import PetSpeechBubble

    app = QApplication.instance() or QApplication([])
    pet = QRect(420, 460, 220, 260)
    bubble = PetSpeechBubble(style_id="breath_bubble")
    bubble.show_text("好女孩……", pet, 5000, pet_scale=0.72)
    app.processEvents()
    short_size = bubble.size()

    bubble.show_text(
        "今天也要认真工作，完成任务后一起去看看星星和蓝色的大海吧。",
        pet, 5000, pet_scale=0.72,
    )
    app.processEvents()
    assert bubble.width() > short_size.width()
    assert abs(bubble.width() / bubble.height() - 240 / 195) < 0.03

    image = tmp_path / "square.png"
    Image.new("RGB", (240, 240), "white").save(image)
    assert bubble.show_image(image, pet, 5000, pet_scale=0.72)
    app.processEvents()
    assert bubble.width() > short_size.width()
    assert bubble._breath_image_rect.height() >= 104
    assert abs(bubble.width() / bubble.height() - 240 / 195) < 0.03
    bubble.close()
    app.processEvents()


def test_breath_bubble_image_is_clipped_and_painted_below_water_details(tmp_path):
    from PIL import Image
    from PySide6.QtCore import QPointF, QRect
    from PySide6.QtWidgets import QApplication

    from pet.speech_bubble import PetSpeechBubble

    app = QApplication.instance() or QApplication([])
    image = tmp_path / "square-content.png"
    Image.new("RGB", (300, 300), "red").save(image)
    bubble = PetSpeechBubble(style_id="breath_bubble")
    assert bubble.show_image(
        image, QRect(420, 460, 220, 260), 5000, pet_scale=0.72
    )
    app.processEvents()

    # A child QLabel paints after the parent and used to cover the water,
    # outline and highlight with an un-clipped rectangular image.
    assert bubble.label.pixmap().isNull()
    assert not bubble._breath_image_rect.isEmpty()
    assert not bubble._breath_image_clip_path.isEmpty()
    assert bubble._breath_image_clip_path.contains(
        QPointF(bubble._breath_image_rect.center())
    )
    assert not bubble._breath_image_clip_path.contains(
        QPointF(bubble._breath_image_rect.topLeft())
    )
    bubble.close()
    app.processEvents()


def test_breath_image_position_uses_actual_window_size_not_hidden_label_hint(tmp_path):
    from PIL import Image
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QApplication

    from pet.speech_bubble import PetSpeechBubble, bubble_rect_for_anchor

    app = QApplication.instance() or QApplication([])
    image = tmp_path / "position.png"
    Image.new("RGB", (300, 300), "blue").save(image)
    anchor = QRect(420, 460, 220, 260)
    bubble = PetSpeechBubble(style_id="breath_bubble")
    assert bubble.show_image(image, anchor, 5000, pet_scale=0.72)
    app.processEvents()
    available = app.primaryScreen().availableGeometry()
    expected = bubble_rect_for_anchor(
        anchor, bubble.size(), available, bubble._preset["placement"]
    )
    assert bubble.sizeHint() != bubble.size()
    assert bubble.x() == expected.x()
    bubble.close()
    app.processEvents()


def test_standard_bubble_images_keep_retina_device_pixel_density(tmp_path, monkeypatch):
    from PIL import Image
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QApplication

    from pet.speech_bubble import PetSpeechBubble

    app = QApplication.instance() or QApplication([])
    image = tmp_path / "retina.png"
    Image.new("RGB", (600, 600), "blue").save(image)
    bubble = PetSpeechBubble(style_id="paper_left")
    monkeypatch.setattr(bubble, "devicePixelRatioF", lambda: 2.0)
    assert bubble.show_image(image, QRect(420, 460, 220, 260), 5000)
    app.processEvents()
    bubble.grab()
    assert bubble.label.pixmap().isNull()
    assert bubble._source_pixmap.width() >= bubble.label.width() * 2
    assert bubble._standard_image_rect.toRect() == bubble.label.geometry()
    bubble.close()
    app.processEvents()


def test_all_bubble_presets_use_soft_layered_shadows():
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QApplication

    from pet.speech_bubble import BUBBLE_STYLE_PRESETS, PetSpeechBubble

    app = QApplication.instance() or QApplication([])
    for style_id in BUBBLE_STYLE_PRESETS:
        bubble = PetSpeechBubble(style_id=style_id)
        bubble.show_text("阴影测试", QRect(420, 460, 220, 260), 5000)
        app.processEvents()
        assert len(bubble._shadow_layers) >= 3
        widths = [layer[0] for layer in bubble._shadow_layers]
        alphas = [layer[1] for layer in bubble._shadow_layers]
        assert widths == sorted(widths, reverse=True)
        assert alphas == sorted(alphas)
        assert alphas[0] > 0
        bubble.close()
    app.processEvents()


def test_pet_scale_change_reflows_visible_bubble_after_rebuilding_mask():
    from PySide6.QtCore import QRect

    from pet.window import PetWindow

    events = []

    class Bubble:
        def isVisible(self):
            return True

        def reflow(self, anchor, *, pet_scale):
            events.append(("reflow", anchor, pet_scale))

    class FakePet:
        scale = 0.5
        _h = 100
        _speech_bubble = Bubble()

        def geometry(self):
            return QRect(20, 30, 100, self._h)

        def x(self):
            return 20

        def _apply_scale(self):
            self._h = 200
            events.append("scale")

        def move(self, x, y):
            events.append(("move", x, y))

        def _rebuild_frame(self):
            events.append("rebuild")

        def visible_content_rect(self):
            assert events[-1] == "rebuild"
            return QRect(25, 35, 160, 210)

        def update(self):
            events.append("update")

        def _save_position(self):
            events.append("save")

    pet = FakePet()
    PetWindow.change_scale(pet, 1.0)
    assert events[2] == "rebuild"
    assert events[3] == ("reflow", QRect(25, 35, 160, 210), 1.0)


def test_self_talk_images_and_duration_are_normalized_and_scheduled_after_hide(tmp_path, monkeypatch):
    from PIL import Image

    from pet.config import Config, DEFAULT_SELF_TALK_DURATION_SECONDS
    from pet.speech_bubble import list_self_talk_images
    from pet.window import PetWindow

    image_dir = tmp_path / "talk-images"
    image_dir.mkdir()
    Image.new("RGB", (20, 10), "blue").save(image_dir / "one.png")
    (image_dir / "ignore.txt").write_text("not an image", encoding="utf-8")

    config = Config(tmp_path)
    assert config.get("self_talk_duration_seconds") == DEFAULT_SELF_TALK_DURATION_SECONDS
    config.set("self_talk_duration_seconds", 7.5)
    config.set("self_talk_image_dir", str(image_dir))
    config.save()
    loaded = Config(tmp_path)
    assert loaded.get("self_talk_duration_seconds") == 7.5
    assert loaded.get("self_talk_image_dir") == str(image_dir)
    assert list_self_talk_images(str(image_dir)) == [image_dir / "one.png"]

    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QApplication
    from pet.speech_bubble import PetSpeechBubble

    app = QApplication.instance() or QApplication([])
    bubble = PetSpeechBubble(style_id="breath_bubble")
    assert bubble.show_image(
        image_dir / "one.png", QRect(420, 460, 220, 260),
        5000, pet_scale=0.72,
    )
    app.processEvents()
    assert not bubble._source_pixmap.isNull()
    assert not bubble._breath_image_rect.isEmpty()


def test_self_talk_image_scale_config_and_bubble_size(tmp_path):
    """气泡配图尺寸：配置 50~300 钳位 + 气泡按 image_scale 放大（双气泡形态都验证）。"""
    from PIL import Image

    from pet.config import Config

    image_dir = tmp_path / "talk-images"
    image_dir.mkdir()
    Image.new("RGB", (20, 10), "blue").save(image_dir / "one.png")

    config = Config(tmp_path)
    assert config.get("self_talk_image_scale") == 100  # 默认
    config.set("self_talk_image_scale", 250)
    config.save()
    loaded = Config(tmp_path)
    assert loaded.get("self_talk_image_scale") == 250
    # 钳位：越界/非数值
    config.set("self_talk_image_scale", 9999)
    config.save()
    assert Config(tmp_path).get("self_talk_image_scale") == 300
    config.set("self_talk_image_scale", "abc")
    config.save()
    assert Config(tmp_path).get("self_talk_image_scale") == 100

    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QApplication
    from pet.speech_bubble import PetSpeechBubble

    app = QApplication.instance() or QApplication([])
    anchor = QRect(420, 460, 220, 260)
    normal = PetSpeechBubble()
    big = PetSpeechBubble()
    assert normal.show_image(image_dir / "one.png", anchor, 5000, image_scale=1.0)
    assert big.show_image(image_dir / "one.png", anchor, 5000, image_scale=2.5)
    assert big.label.width() > normal.label.width() * 2
    # 呼吸气泡形态同样吃缩放
    breath_normal = PetSpeechBubble(style_id="breath_bubble")
    breath_big = PetSpeechBubble(style_id="breath_bubble")
    assert breath_normal.show_image(image_dir / "one.png", anchor, 5000, pet_scale=0.72, image_scale=1.0)
    assert breath_big.show_image(image_dir / "one.png", anchor, 5000, pet_scale=0.72, image_scale=2.5)
    assert breath_big.width() > breath_normal.width()
    # 超界缩放被弹窗侧钳住（双保险）
    huge = PetSpeechBubble()
    capped = PetSpeechBubble()
    assert huge.show_image(image_dir / "one.png", anchor, 5000, image_scale=99.0)
    assert capped.show_image(image_dir / "one.png", anchor, 5000, image_scale=3.0)
    assert huge._image_scale == 3.0
    assert huge.label.width() == capped.label.width()
    app.processEvents()
    for w in (normal, big, breath_normal, breath_big, huge, capped):
        w.close()
    app.processEvents()


def test_self_talk_scheduling_and_random_talk_dispatch(tmp_path, monkeypatch):
    """自言自语调度间隔与随机图片/文本派发（原 test_self_talk_images... 的后半段）。"""
    import random  # noqa: F401
    from pathlib import Path  # noqa: F401

    from PySide6.QtCore import QRect

    from pet.window import PetWindow

    image_dir = tmp_path / "talk-images"
    image_dir.mkdir()
    # _show_random_self_talk 会惰性剔除不存在的图片文件——必须有真实图片
    from PIL import Image
    Image.new("RGB", (20, 10), "blue").save(image_dir / "one.png")

    starts = []

    class Timer:
        def stop(self):
            pass

        def start(self, milliseconds):
            starts.append(milliseconds)

    class FakePet:
        _self_talk_timer = Timer()
        _self_talk_enabled = True
        _self_talk_texts = ["hello"]
        _self_talk_images = [image_dir / "one.png"]
        _self_talk_image_scale = 1.0
        _self_talk_min_interval = 5.0
        _self_talk_max_interval = 5.0
        _self_talk_duration_seconds = 7.5

    monkeypatch.setattr("pet.window.random.uniform", lambda *_: 5.0)
    PetWindow._schedule_self_talk(FakePet(), after_display=True)
    assert starts == [12500]

    shown = []

    class Bubble:
        def show_image(self, path, anchor, duration, *, pet_scale, image_scale=1.0):
            shown.append((Path(path), anchor, duration, pet_scale, image_scale))
            return True

    runtime_pet = FakePet()
    runtime_pet._speech_bubble = Bubble()
    runtime_pet.scale = 0.72
    runtime_pet.visible_content_rect = lambda: QRect(10, 20, 180, 240)
    monkeypatch.setattr("pet.window.random.choice", lambda choices: choices[-1])
    assert PetWindow._show_random_self_talk(runtime_pet)
    assert shown == [(image_dir / "one.png", QRect(10, 20, 180, 240), 7500, 0.72, 1.0)]


def test_speech_bubble_tail_is_one_surface_and_shadow_has_no_graphics_effect():
    from PySide6.QtCore import QPointF, QRect
    from PySide6.QtWidgets import QApplication

    from pet.speech_bubble import PetSpeechBubble

    app = QApplication.instance() or QApplication([])
    bubble = PetSpeechBubble(style_id="classic_top")
    bubble.show_text("今天也要认真工作呀。", QRect(320, 420, 180, 240), 5000)
    app.processEvents()
    assert bubble.graphicsEffect() is None
    assert not bubble._surface_path.isEmpty()
    base_center = QPointF(
        (bubble._tail_base[0].x() + bubble._tail_base[1].x()) / 2,
        (bubble._tail_base[0].y() + bubble._tail_base[1].y()) / 2,
    )
    inside_tail = QPointF(
        bubble._tail_tip.x() * 0.8 + base_center.x() * 0.2,
        bubble._tail_tip.y() * 0.8 + base_center.y() * 0.2,
    )
    assert bubble._surface_path.contains(inside_tail)
    assert bubble._surface_path.contains(base_center)
    assert bubble._shadow_offset_y <= 2
    bubble.close()
    app.processEvents()


def test_self_talk_bubble_style_is_normalized_and_persisted(tmp_path):
    from pet.config import Config

    config = Config(tmp_path)
    assert config.get("self_talk_bubble_style") == "classic_top"
    config.set("self_talk_bubble_style", "glass_right")
    config.save()
    assert Config(tmp_path).get("self_talk_bubble_style") == "glass_right"
    config.set("self_talk_bubble_style", "unknown")
    assert config.get("self_talk_bubble_style") == "classic_top"


def test_icon_composition_ignores_low_alpha_noise_and_fills_canvas():
    from PIL import Image, ImageDraw

    script = Path("scripts/make_icon.py")
    spec = importlib.util.spec_from_file_location("make_icon_for_test", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    source = Image.new("RGBA", (640, 360), (0, 0, 0, 0))
    draw = ImageDraw.Draw(source)
    draw.rectangle((290, 120, 349, 239), fill=(40, 100, 180, 255))
    draw.point((1, 1), fill=(255, 255, 255, 1))

    icon = module.prepare_icon_image(source, side=256)
    bbox = icon.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
    assert icon.size == (256, 256)
    assert bbox is not None
    assert max(bbox[2] - bbox[0], bbox[3] - bbox[1]) >= 248


def test_new_pet_command_relaunches_current_frozen_executable(monkeypatch):
    from pet import instance_launcher

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/Applications/dsh-pet.app/Contents/MacOS/dsh-pet")
    assert instance_launcher.new_pet_command() == [sys.executable]


def test_launch_new_pet_uses_detached_process(monkeypatch):
    from pet import instance_launcher

    captured = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(instance_launcher, "new_pet_command", lambda: ["pet-program"])
    monkeypatch.setattr(instance_launcher.subprocess, "Popen", fake_popen)
    instance_launcher.launch_new_pet()

    assert captured["command"] == ["pet-program"]
    assert captured["kwargs"]["env"]["DSH_PET_SPAWN_OFFSET_INDEX"] == "1"
    if sys.platform == "win32":
        assert captured["kwargs"]["creationflags"]
    else:
        assert captured["kwargs"]["start_new_session"] is True


def test_pet_app_assigns_distinct_offsets_to_spawned_pets(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    import pet.app as app_mod
    from pet.app import AppShell
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    offsets = []
    monkeypatch.setattr(app_mod, "launch_new_pet", lambda index: offsets.append(index))
    owner = AppShell(app, Config(tmp_path))
    owner.spawn_pet()
    owner.spawn_pet()
    assert offsets == [1, 2]


def test_pet_click_restores_fun_windows_even_without_click_animation():
    from pet.window import PetWindow

    restored = []

    class FakePet:
        _just_dragged = False
        clicks = []
        on_restore_fun_windows = lambda self: restored.append(True)

    PetWindow._on_click(FakePet())
    assert restored == [True]


def test_modern_pet_context_menu_has_spawn_action_with_avatar_icon(monkeypatch):
    from PySide6.QtWidgets import QApplication, QMenu, QWidget

    import pet.window as window_mod

    class FakeConfig:
        values = {"character": "shenshen", "on_top": True}

        def get(self, key, default=None):
            return self.values.get(key, default)

    class FakePet:
        on_open_chat = None
        on_open_chat_settings = None
        on_open_settings = None
        on_spawn_pet = None
        idles = []
        turns = []
        moves = []
        clicks = []
        acts = []
        playback_speed = 1.0
        drag_physics = False
        no_move = False
        scale = 1.0
        cfg = FakeConfig()

        def __init__(self):
            self.spawn_count = 0
            self.on_spawn_pet = self.spawn

        def spawn(self):
            self.spawn_count += 1

        def icon_pixmap(self, size=64):
            from PySide6.QtGui import QPixmap

            pixmap = QPixmap(size, size)
            pixmap.fill()
            return pixmap

        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(window_mod.autostart_mod, "is_enabled", lambda: False)
    monkeypatch.setattr(window_mod.catalog, "list_available_characters", lambda: ["shenshen"])
    pet = FakePet()
    menu = QMenu()
    window_mod._populate_context_menu(menu, pet)

    direct_actions = [action for action in menu.actions() if not action.isSeparator()]
    assert direct_actions
    controls = next(action.menu() for action in direct_actions if action.text() == "桌宠控制")
    spawn_action = next(action for action in controls.actions() if action.text() == "生小肥鱼")
    assert not spawn_action.icon().isNull()
    spawn_action.trigger()
    assert pet.spawn_count == 1
    menu.close()
    app.processEvents()


def test_context_menu_defaults_to_modern_and_keeps_migration_metadata(tmp_path):
    from pet.config import Config
    from pet.context_menu import load_menu_template

    config = Config(tmp_path)
    assert config.get("context_menu_template") == "modern"
    legacy = load_menu_template("legacy")
    modern = load_menu_template("modern")
    assert legacy["switch_to"] == "modern"
    assert modern["switch_to"] == "legacy"
    assert legacy["switch_label"] == "切换到新版菜单"
    assert modern["switch_label"] == "切换回旧版菜单"
    assert [group["id"] for group in modern["groups"]] == [
        "interaction",
        "playback",
        "functions",
        "tools",
        "settings",
        "template",
        "exit",
    ]


def test_context_menu_runtime_only_dispatches_modern_layout():
    legacy_source = Path("pet/context_menus/legacy.py").read_text(encoding="utf-8")
    modern_source = Path("pet/context_menus/modern.py").read_text(encoding="utf-8")
    dispatcher_source = Path("pet/context_menu.py").read_text(encoding="utf-8")

    assert "build_legacy_menu" in legacy_source
    assert "build_modern_menu" not in legacy_source
    assert "build_modern_menu" in modern_source
    assert "build_legacy_menu" not in modern_source
    # 分发器按 context_menu_template 配置选择两套模板（不再硬编码 modern）
    assert "build_legacy_menu" in dispatcher_source
    assert "build_modern_menu" in dispatcher_source
    assert "context_menu_template" in dispatcher_source


def test_pet_avatar_menu_icon_fills_native_slot_and_stays_centered(monkeypatch):
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication, QMenu, QStyle

    from pet.context_menus.icons import pet_avatar_menu_icon

    class FakePet:
        @staticmethod
        def icon_pixmap(size=64):
            # Reproduce a Retina source: physical dimensions are twice the
            # logical dimensions. The old painter path rendered this at 1/2 size.
            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.GlobalColor.transparent)
            from PySide6.QtGui import QPainter

            painter = QPainter(pixmap)
            painter.fillRect(QRect(size // 4, 0, size // 2, size), Qt.GlobalColor.blue)
            painter.end()
            pixmap.setDevicePixelRatio(2.0)
            return pixmap

    app = QApplication.instance() or QApplication([])
    menu = QMenu()
    native_size = menu.style().pixelMetric(QStyle.PixelMetric.PM_SmallIconSize, None, menu)
    icon = pet_avatar_menu_icon(menu, FakePet())
    pixmap = icon.pixmap(native_size, native_size)
    # Retina menu (dpr=2) yields physical 32px backing for the same 16 logical slot.
    dpr = pixmap.devicePixelRatio() or 1.0
    assert pixmap.width() / dpr == native_size
    assert pixmap.height() / dpr == native_size
    assert icon.availableSizes()[0].width() / dpr == native_size
    # Inspect actual non-transparent pixels, not merely the QImage canvas.
    points = [
        (x, y)
        for y in range(pixmap.height())
        for x in range(pixmap.width())
        if pixmap.toImage().pixelColor(x, y).alpha() > 0
    ]
    left, right = min(x for x, _ in points), max(x for x, _ in points)
    top, bottom = min(y for _, y in points), max(y for _, y in points)
    assert (bottom - top + 1) / dpr >= native_size * 0.8
    assert abs(((left + right) / 2.0) / dpr - ((native_size - 1) / 2.0)) <= 1.0
    menu.close()
    app.processEvents()


def test_modern_context_menu_has_compact_semantic_groups(monkeypatch):
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication, QMenu

    import pet.window as window_mod
    import pet.context_menus.registry as registry_mod
    import pet.context_menus.shared as shared_menu_mod

    class FakeConfig:
        def __init__(self):
            self.values = {
                "character": "shenshen",
                "on_top": True,
                "context_menu_template": "modern",
            }

        def get(self, key, default=None):
            return self.values.get(key, default)

        def set(self, key, value):
            self.values[key] = value

        def save(self):
            return None

    class FakePet:
        on_open_chat = lambda self: None
        on_look_screen = lambda self: None
        on_show_balance = lambda self, parent=None: None
        on_check_update = lambda self, parent=None: None
        on_open_chat_settings = lambda self: None
        on_open_legacy_settings = lambda self: None
        on_open_modern_settings = lambda self: None
        on_spawn_pet = lambda self: None
        idles = ["待机"]
        turns = ["转向"]
        moves = ["移动"]
        clicks = ["点击"]
        acts = ["动作"]
        playback_speed = 1.0
        drag_physics = False
        no_move = False
        scale = 1.0

        def __init__(self):
            self.cfg = FakeConfig()

        def icon_pixmap(self, size=64):
            pixmap = QPixmap(size, size)
            pixmap.fill()
            return pixmap

        def set_context_menu_template(self, template_id):
            self.cfg.set("context_menu_template", template_id)

        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(window_mod.autostart_mod, "is_enabled", lambda: False)
    monkeypatch.setattr(window_mod.catalog, "list_available_characters", lambda: ["shenshen"])
    opened_urls = []
    monkeypatch.setattr(
        registry_mod.QDesktopServices,
        "openUrl",
        lambda url: opened_urls.append(url.toString()) or True,
    )
    pet = FakePet()
    menu = QMenu()
    window_mod._populate_context_menu(menu, pet)
    labels = [action.text() for action in menu.actions() if not action.isSeparator()]
    expected_labels = [
        "厉害了我的鲸",
        "AI 对话",
        "看看屏幕",
        "播放动画",
        "切换角色",
        "播放速率",
        "大小",
        "桌宠控制",
        "快捷启动",
        "工具与帮助",
        "Agent 联动",
    ]
    if sys.platform == "win32":
        expected_labels.append("主动识屏")  # 仅 Windows + 有聊天能力时显示
    expected_labels.extend([
        "桌宠设置",
        "退出",
    ])
    assert labels == expected_labels
    animation_action = next(action for action in menu.actions() if action.text() == "播放动画")
    assert animation_action.menu() is not None
    assert [action.text() for action in animation_action.menu().actions()] == [
        "待机",
        "转向",
        "移动",
        "点击回应",
        "随机动作",
    ]
    assert next(action for action in menu.actions() if action.text() == "播放速率").menu() is not None
    assert next(action for action in menu.actions() if action.text() == "大小").menu() is not None
    tools = next(action.menu() for action in menu.actions() if action.text() == "工具与帮助")
    next(action for action in tools.actions() if action.text() == "打开网页版 DeepSeek").trigger()
    assert opened_urls == [shared_menu_mod.DEEPSEEK_WEB_URL]
    # 模板模式已迁入设置；默认菜单不再用低频迁移命令占据根菜单。
    assert all("旧版菜单" not in action.text() for action in menu.actions())
    menu.close()
    app.processEvents()


def test_pure_pet_context_menu_keeps_web_but_hides_harness():
    """纯桌宠（无 Chat/DSH 联动）右键菜单去掉 DeepSeek Harness，保留网页版。"""
    from PySide6.QtWidgets import QApplication, QMenu

    from pet.context_menu import populate_context_menu

    class FakeConfig:
        def get(self, key, default=None):
            return {
                "context_menu_template": "modern",
                "character": "shenshen",
                "context_menu_appearance": {"theme": "light"},
            }.get(key, default)

    class FakePet:
        cfg = FakeConfig()
        on_open_chat = None
        on_open_chat_settings = None
        on_open_legacy_settings = None
        on_open_modern_settings = None
        on_spawn_pet = None
        idles = turns = moves = clicks = acts = []
        playback_speed = scale = 1.0
        drag_physics = no_move = False

        def __getattr__(self, name):
            return None

    app = QApplication.instance() or QApplication([])
    menu = QMenu()
    populate_context_menu(menu, FakePet())
    def labels_in(menu):
        labels = []
        for action in menu.actions():
            if action.isSeparator():
                continue
            labels.append(action.text())
            if action.menu() is not None:
                labels.extend(labels_in(action.menu()))
        return labels

    labels = labels_in(menu)
    assert "启动 DeepSeek Harness" not in labels
    assert "打开网页版 DeepSeek" in labels
    menu.close()
    app.processEvents()


def test_context_menu_dispatches_style_by_template(monkeypatch):
    from PySide6.QtWidgets import QApplication, QMenu, QStyle, QWidget

    from pet.context_menu import populate_context_menu

    class Config:
        def __init__(self, template):
            self.template = template

        def get(self, key, default=None):
            return {
                "context_menu_template": self.template,
                "character": "shenshen",
                # 固定浅色主题，避免断言随系统深色模式翻转
                "context_menu_appearance": {"theme": "light"},
            }.get(key, default)

    class Pet:
        on_open_chat = on_open_chat_settings = None
        on_open_legacy_settings = on_open_modern_settings = None
        on_spawn_pet = None
        idles = turns = moves = clicks = acts = []
        playback_speed = scale = 1.0
        drag_physics = no_move = False

        def __init__(self, template):
            self.cfg = Config(template)

        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    app = QApplication.instance() or QApplication([])
    legacy_menu = QMenu()
    modern_menu = QMenu()
    populate_context_menu(legacy_menu, Pet("legacy"))
    populate_context_menu(modern_menu, Pet("modern"))
    # legacy 模板走 legacy 样式（无 modern 外观），modern 模板走 modern 样式
    assert legacy_menu.objectName() != "modernContextMenu"
    assert legacy_menu.styleSheet() == ""
    assert modern_menu.objectName() == "modernContextMenu"
    # The modern menu follows the compact macOS project-menu reference: a
    # white hairline surface, small system text, outline icons and subtle rules
    # between semantic groups.
    assert "background-color: rgba(255, 255, 255, 240)" in modern_menu.styleSheet()
    assert "border: none" in modern_menu.styleSheet()
    hairline = modern_menu.findChild(QWidget, "modernHairlineBorder")
    assert hairline is not None
    assert hairline.property("physicalPixelWidth") == 1
    assert "border-radius: 12px" in modern_menu.styleSheet()
    assert "font-size: 13px" in modern_menu.styleSheet()
    assert "icon-size: 18px" in modern_menu.styleSheet()
    assert "background-color: #eeeeee" in modern_menu.styleSheet()
    assert "min-height: 18px" in modern_menu.styleSheet()
    assert "padding: 3px 29px 3px 13px" in modern_menu.styleSheet()
    assert "margin-right: 10px" in modern_menu.styleSheet()
    assert "QMenu::separator" in modern_menu.styleSheet()
    separator_rule = modern_menu.styleSheet().split("QMenu::separator", 1)[1]
    assert "height: 1px" in separator_rule
    assert "background: #e5e5e5" in separator_rule
    assert Path("pet/context_menus/menu_styles/common.py").is_file()
    assert Path("pet/context_menus/menu_styles/legacy.py").is_file()
    assert Path("pet/context_menus/menu_styles/modern.py").is_file()
    for menu in (legacy_menu, modern_menu):
        assert menu.style().styleHint(QStyle.StyleHint.SH_Menu_SubMenuPopupDelay, None, menu) == 60
        assert menu.style().styleHint(QStyle.StyleHint.SH_Menu_SubMenuSloppyCloseTimeout, None, menu) == 120
        assert menu.style().styleHint(QStyle.StyleHint.SH_Menu_SubMenuSloppySelectOtherActions, None, menu) == 1
        assert menu.style().styleHint(QStyle.StyleHint.SH_Menu_SubMenuUniDirection, None, menu) == 0
        menu.close()
    app.processEvents()


def test_context_menu_invalid_template_falls_back_to_modern_uniformly():
    """非法/缺失模板 id：load_menu_template 与 populate_context_menu 统一回退 modern。

    历史分歧：load_menu_template 曾把非法值回退 legacy、populate_context_menu 回退
    modern——同一非法配置在两个入口结果不同。现在统一走 normalize_template_id，
    非法值一律落到现行默认模板 modern。
    """
    from PySide6.QtWidgets import QApplication, QMenu
    from pet.context_menu import load_menu_template, normalize_template_id, populate_context_menu

    # normalize 是唯一的回退决策点
    assert normalize_template_id("modern") == "modern"
    assert normalize_template_id("legacy") == "legacy"
    assert normalize_template_id("MODERN") == "modern"
    assert normalize_template_id(None) == "modern"
    assert normalize_template_id("") == "modern"
    assert normalize_template_id("bogus") == "modern"
    # 两个入口对同一非法值给出同一结果：modern 模板
    assert load_menu_template("bogus")["id"] == "modern"
    assert load_menu_template(None)["id"] == "modern"
    assert load_menu_template("")["id"] == "modern"

    class Config:
        def get(self, key, default=None):
            return "bogus" if key == "context_menu_template" else default

    class Pet:
        on_open_chat = on_open_chat_settings = None
        on_open_legacy_settings = on_open_modern_settings = None
        on_spawn_pet = None
        idles = turns = moves = clicks = acts = []
        playback_speed = scale = 1.0
        drag_physics = no_move = False

        def __init__(self):
            self.cfg = Config()

        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    app = QApplication.instance() or QApplication([])
    menu = QMenu()
    populate_context_menu(menu, Pet())
    # 非法配置下分发器也走 modern（现行默认模板）
    assert menu.objectName() == "modernContextMenu"
    menu.close()
    app.processEvents()

    # PetWindow.set_context_menu_template 是第三个入口（历史旁路：非法值曾回退 legacy，
    # 与 normalize 分歧）。现在同样走 normalize_template_id，持久化值与 normalize 结果一致。
    from pet.window import PetWindow

    class RecorderConfig:
        def __init__(self):
            self.values = {}

        def set(self, key, value):
            self.values[key] = value

        def save(self):
            return None

    class PetWindowStub:
        def __init__(self):
            self.cfg = RecorderConfig()

    def persisted_template(raw):
        stub = PetWindowStub()
        PetWindow.set_context_menu_template(stub, raw)
        return stub.cfg.values["context_menu_template"]

    for raw in (None, "bad", "LEGACY", "legacy", "modern"):
        assert persisted_template(raw) == normalize_template_id(raw)
    # 非法值 None/bad 落到现行默认模板 modern；合法值 legacy/modern 原样保留
    # （'LEGACY' 是 'legacy' 的大小写变体，normalize 折叠为合法值 'legacy'）
    assert persisted_template(None) == "modern"
    assert persisted_template("bad") == "modern"
    assert persisted_template("legacy") == "legacy"
    assert persisted_template("modern") == "modern"


def test_modern_menu_icons_are_crisp_outline_glyphs(monkeypatch):
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication, QMenu

    from pet.context_menus.icons import vector_menu_icon
    from pet.context_menus.menu_styles.modern import apply_modern_menu_style

    app = QApplication.instance() or QApplication([])
    menu = QMenu()
    # 固定浅色主题：深色系统下菜单图标为浅灰（深色菜单设计），
    # 该断言验证的是浅色菜单的深色轮廓图标
    apply_modern_menu_style(menu, {"theme": "light"})
    names = ("chat", "play", "character", "speed", "spawn", "settings", "web", "exit")
    cache_keys = []
    for name in names:
        icon = vector_menu_icon(menu, name)
        pixmap = icon.pixmap(18, 18)
        image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        cache_keys.append(icon.cacheKey())
        opaque_pixels = 0
        dark_pixels = 0
        for y in range(image.height()):
            for x in range(image.width()):
                color = QColor(image.pixelColor(x, y))
                if color.alpha() > 24:
                    opaque_pixels += 1
                    if max(color.red(), color.green(), color.blue()) < 150:
                        dark_pixels += 1
        assert opaque_pixels >= 12
        assert dark_pixels >= 8
    assert len(set(cache_keys)) == len(names)
    menu.close()
    app.processEvents()


def test_modern_checked_action_is_painted_in_reserved_right_slot():
    from PySide6.QtWidgets import QApplication, QMenu, QWidget

    from pet.context_menus.menu_styles.modern import (
        apply_modern_menu_style,
        install_modern_check_indicators,
    )

    app = QApplication.instance() or QApplication([])
    menu = QMenu()
    apply_modern_menu_style(menu)
    action = menu.addAction("窗口置顶")
    action.setCheckable(True)
    action.setChecked(True)
    install_modern_check_indicators(menu)
    assert menu.property("paintChecksOnRight") is True
    assert menu.findChild(QWidget, "modernEnabledIndicator") is None
    assert menu.findChild(QWidget, "modernCheckLayer") is not None
    assert "padding:" in menu.styleSheet()
    action.setChecked(False)
    app.processEvents()
    assert action.isChecked() is False
    action.setChecked(True)
    app.processEvents()
    assert action.isChecked() is True
    menu.close()
    app.processEvents()


def test_modern_menu_starts_with_ojingjing_entry_and_uses_pet_avatar(monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication, QMenu, QWidget

    from pet.context_menu import populate_context_menu

    class Config(dict):
        def get(self, key, default=None):
            return super().get(key, default)

    class Pet:
        cfg = Config(context_menu_template="modern", character="shenshen", on_top=False)
        on_open_chat = on_open_modern_settings = None
        idles = turns = moves = clicks = acts = []
        playback_speed = scale = 1.0
        drag_physics = no_move = False
        on_spawn_pet = lambda self: None

        def icon_pixmap(self, size=64):
            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.GlobalColor.blue)
            return pixmap

        def __getattr__(self, _name):
            return lambda *_args, **_kwargs: None

    app = QApplication.instance() or QApplication([])
    menu = QMenu()
    pet = Pet()
    populate_context_menu(menu, pet)
    first_action = menu.actions()[0]
    assert first_action.text() == "厉害了我的鲸"
    entry = menu.findChild(QWidget, "ojingjingMenuEntry")
    assert entry is not None
    assert entry.height() == 39
    assert entry.findChild(QWidget, "ojingjingAvatar") is not None
    assert entry.findChild(QWidget, "ojingjingClickAccessory") is not None
    controls = next(action.menu() for action in menu.actions() if action.text() == "桌宠控制")
    spawn = next(action for action in controls.actions() if action.text() == "生小肥鱼")
    pixmap = spawn.icon().pixmap(18, 18)
    center = pixmap.toImage().pixelColor(pixmap.width() // 2, pixmap.height() // 2)
    assert center.blue() > center.red() + 80
    menu.close()
    app.processEvents()


def test_ojingjing_windows_are_frameless_stackable_and_closeable():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QPushButton

    from pet.fun_image_popup import OjingjingWindowManager

    app = QApplication.instance() or QApplication([])
    manager = OjingjingWindowManager()
    first = manager.open_window(show=False)
    second = manager.open_window(show=False)
    app.processEvents()
    assert len(manager.windows) == 2
    assert first.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert first.property("cascadeOffset") != second.property("cascadeOffset")
    labels = {button.text() for button in second.findChildren(QPushButton)}
    assert labels == {"关闭", "全部关闭"}
    manager.close_all()
    app.processEvents()
    assert manager.windows == []


def test_ojingjing_uses_popup_directory_random_images_and_drag_helpers(monkeypatch):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication

    import pet.fun_image_popup as popup_mod

    app = QApplication.instance() or QApplication([])
    paths = popup_mod.popup_image_paths()
    assert len(paths) >= 2
    assert popup_mod.oijingjing_image_path() == Path("assets/big_blue_fat_fish/ojingjing.jpg").resolve()
    picks = iter((paths[0], paths[1]))
    monkeypatch.setattr(popup_mod.random, "choice", lambda _paths: next(picks))
    manager = popup_mod.OjingjingWindowManager()
    first = manager.open_window(show=False)
    second = manager.open_window(show=False)
    assert first.property("sourceImage") != second.property("sourceImage")
    first.move(30, 40)
    first.begin_drag_at(QPoint(100, 100))
    first.drag_to(QPoint(125, 135))
    assert first.pos() == QPoint(55, 75)
    first.end_drag()
    manager.close_all()
    app.processEvents()


def test_ojingjing_invalid_empty_or_unreadable_directory_falls_back(tmp_path):
    import pet.fun_image_popup as popup_mod

    fallback = popup_mod.oijingjing_image_path().parent
    missing = tmp_path / "missing"
    empty = tmp_path / "empty"
    empty.mkdir()
    unreadable = tmp_path / "unreadable"
    unreadable.mkdir()
    (unreadable / "broken.jpg").write_bytes(b"not-an-image")

    for configured in (missing, empty, unreadable):
        paths = popup_mod.popup_image_paths(configured)
        assert paths
        assert all(path.parent == fallback for path in paths)

    assert popup_mod.resolve_fun_asset(missing, fallback) == fallback


def test_ojingjing_menu_entry_defers_window_until_menu_exec_returns(monkeypatch):
    from PySide6.QtWidgets import QApplication, QMenu

    import pet.context_menus.fun_entry as fun_entry
    from pet.context_menus.shared import take_deferred_menu_callbacks

    app = QApplication.instance() or QApplication([])
    calls = []
    monkeypatch.setattr(fun_entry, "open_ojingjing_window", lambda config: calls.append(config))
    menu = QMenu()
    entry = fun_entry.OjingjingMenuEntry(menu, {"title": "彩蛋"})
    menu.show()
    app.processEvents()

    entry._activate()
    assert calls == []
    callbacks = take_deferred_menu_callbacks(menu)
    assert len(callbacks) == 1
    callbacks[0]()
    assert calls == [{"title": "彩蛋"}]
    entry.close()
    menu.close()
    app.processEvents()


def test_ojingjing_menu_entry_reports_unexpected_open_error(monkeypatch):
    from PySide6.QtWidgets import QApplication, QMenu

    import pet.context_menus.fun_entry as fun_entry

    app = QApplication.instance() or QApplication([])
    warnings = []

    def fail(_config):
        raise RuntimeError("图片解码失败")

    monkeypatch.setattr(fun_entry, "open_ojingjing_window", fail)
    monkeypatch.setattr(
        fun_entry.QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )
    menu = QMenu()
    entry = fun_entry.OjingjingMenuEntry(menu)
    entry._activate()
    assert warnings == [("彩蛋图片不可用", "图片解码失败")]
    entry.close()
    app.processEvents()


def test_popup_manager_restores_all_existing_windows_before_new_window():
    from pet.fun_image_popup import OjingjingWindowManager

    class FakeWindow:
        def __init__(self):
            self.calls = []

        def show(self):
            self.calls.append("show")

        def raise_(self):
            self.calls.append("raise")

        def activateWindow(self):
            self.calls.append("activate")

    manager = OjingjingWindowManager()
    first = FakeWindow()
    second = FakeWindow()
    manager.windows = [first, second]
    manager.restore_all()
    assert first.calls == ["show", "raise"]
    assert second.calls == ["show", "raise", "activate"]


def test_modern_settings_panel_uses_sidebar_and_includes_ai_settings(tmp_path, monkeypatch):
    from PySide6.QtCore import QSize, Qt
    from PySide6.QtWidgets import QApplication, QListWidget, QStackedWidget

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    app = QApplication.instance() or QApplication([])
    config = Config(tmp_path)
    autostart_values = []
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    monkeypatch.setattr(settings_mod.autostart_mod, "set_enabled", autostart_values.append)
    dialog = ModernSettingsDialog(config, include_ai=True)
    assert dialog.size() == QSize(800, 560)
    assert dialog.minimumSize() == QSize(720, 500)
    assert dialog.font().pixelSize() == 13
    assert dialog.findChild(settings_mod.QFrame, "sidebarPane").width() == 200
    assert isinstance(dialog.sidebar, QListWidget)
    assert isinstance(dialog.pages, QStackedWidget)
    expected_pages = ["常规", "桌宠", "互动", "菜单", "桌面组件", "AI 与对话", "自动化与联动"]
    assert [dialog.sidebar.item(i).text() for i in range(dialog.sidebar.count())] == expected_pages
    assert dialog.pages.count() == len(expected_pages)
    assert dialog.search_edit.placeholderText() == "搜索设置…"
    assert all(not dialog.sidebar.item(i).icon().isNull() for i in range(dialog.sidebar.count()))
    assert all(dialog.sidebar.item(i).sizeHint().height() >= 34 for i in range(dialog.sidebar.count()))
    assert "QListWidget#settingsSidebar::item:hover" in dialog.styleSheet()
    assert "border-right: 1px solid #e3e5e8" in dialog.styleSheet()
    assert "background: #f7f7f8" in dialog.styleSheet()
    section_titles = [label.text() for label in dialog.findChildren(settings_mod.QLabel, "sectionTitle")]
    assert {
        "应用启动", "窗口与系统", "动画与移动", "点击反馈", "自言自语",
        "显示", "菜单外观", "对话窗口", "已配置应用", "内容与布局",
        "模型与连接", "视觉能力",
    }.issubset(set(section_titles))
    advanced_titles = [
        button.text()
        for button in dialog.findChildren(
            settings_mod.SettingsDisclosureHeader,
            "advancedSectionToggle",
        )
    ]
    assert {"生成参数（高级）", "碰撞参数（高级）", "高级配色"}.issubset(
        set(advanced_titles)
    )
    assert {"浅色主题", "深色主题", "彩蛋入口"}.issubset(set(section_titles))
    scale_row = dialog.findChild(settings_mod.QWidget, "settingRow_scale")
    assert scale_row is not None
    assert scale_row.findChild(settings_mod.QLabel, "settingLabel").text() == "桌宠大小"
    assert scale_row.findChild(settings_mod.QLabel, "settingHint").text()
    stylesheet = dialog.styleSheet()
    assert "QLineEdit:focus" in stylesheet
    assert "QSpinBox::up-button" in stylesheet
    assert "QScrollBar::handle:vertical" in stylesheet
    assert "font-size: 13px" in stylesheet
    assert "font-size: 22px" in stylesheet
    assert "min-height: 20px" in stylesheet
    dialog.scale_combo.setCurrentIndex(dialog.scale_combo.findData(0.85))
    dialog.on_top_check.setChecked(False)
    dialog.no_move_check.setChecked(True)
    dialog.drag_physics_check.setChecked(True)
    dialog.autostart_check.setChecked(True)
    assert isinstance(dialog.scale_combo, settings_mod.ModernSelect)
    assert isinstance(dialog.speed_select, settings_mod.ModernSelect)
    assert isinstance(dialog.bubble_style_select, settings_mod.ModernSelect)
    assert [dialog.bubble_style_select.itemText(index) for index in range(dialog.bubble_style_select.count())] == [
        "经典暖黄 · 正上方",
        "纸感卡片 · 左上方",
        "深色玻璃 · 右上方",
        "柔蓝对话 · 正上方",
        "吐气水泡 · 左上方",
    ]
    assert "SettingsPopup" in dialog.scale_combo.popupStyleSheet()
    assert dialog.gap_spin.maximumWidth() <= 100
    assert dialog.self_talk_duration_spin.value() == 3.2
    assert dialog.self_talk_image_dir_picker.directory is True
    texts_row = dialog.findChild(settings_mod.SettingRow, "settingRow_self_talk_texts")
    assert texts_row.property("stackedControl") is True
    assert dialog.texts_edit.maximumHeight() <= 180

    def page_index(row):
        return next(index for index in range(dialog.pages.count()) if dialog.pages.widget(index).isAncestorOf(row))

    assert page_index(dialog.findChild(settings_mod.SettingRow, "settingRow_autostart")) == 0
    assert page_index(dialog.findChild(settings_mod.SettingRow, "settingRow_playback_speed")) == 1
    assert page_index(dialog.findChild(settings_mod.SettingRow, "settingRow_self_talk_texts")) == 2
    assert page_index(dialog.findChild(settings_mod.SettingRow, "settingRow_scale")) == 1
    assert page_index(dialog.findChild(settings_mod.SettingRow, "settingRow_chat_ui_style")) == 5
    assert page_index(dialog.findChild(settings_mod.SettingRow, "settingRow_api_url")) == 5
    if settings_mod.sys.platform != "win32":
        assert dialog.auto_hide_fullscreen_check is None
        assert dialog.stream_capture_check is None
    dialog.show()
    dialog.pages.widget(2).findChild(settings_mod.QScrollArea, "settingsScroll").ensureWidgetVisible(texts_row)
    app.processEvents()
    label = texts_row.findChild(settings_mod.QLabel, "settingLabel")
    hint = texts_row.findChild(settings_mod.QLabel, "settingHint")
    assert hint.y() - (label.y() + label.height()) <= 4
    prompt_row = dialog.findChild(settings_mod.SettingRow, "settingRow_system_prompt")
    prompt_label = prompt_row.findChild(settings_mod.QLabel, "settingLabel")
    prompt_hint = prompt_row.findChild(settings_mod.QLabel, "settingHint")
    assert prompt_hint.y() - (prompt_label.y() + prompt_label.height()) <= 4
    editor = dialog.quick_launch_editor
    assert editor.list.dragDropMode() == settings_mod.QAbstractItemView.DragDropMode.InternalMove
    assert editor.list.iconSize().width() >= 22
    assert editor.list.item(0).flags() & Qt.ItemFlag.ItemIsUserCheckable
    assert editor.list.item(0).checkState() == Qt.CheckState.Unchecked
    assert dialog.findChild(settings_mod.QDialogButtonBox, "settingsButtons") is None
    assert dialog.save_exit_button.parent() is dialog.findChild(settings_mod.QFrame, "sidebarPane")
    dialog.speed_select.setCurrentData(1.5)
    dialog.bubble_style_select.setCurrentData("paper_left")
    dialog.self_talk_duration_spin.setValue(8.5)
    dialog.self_talk_image_dir_picker.setText(str(tmp_path.resolve()))
    dialog.ai_page.url.setText("https://example.test/v1")
    dialog.ai_page.model.setText("deepseek-test")
    dialog._save()
    assert config.get("scale") == 0.85
    assert config.get("on_top") is False
    assert config.get("no_move") is True
    assert config.get("drag_physics") is True
    assert config.get("playback_speed") == 1.5
    assert config.get("self_talk_bubble_style") == "paper_left"
    assert config.get("self_talk_duration_seconds") == 8.5
    assert config.get("self_talk_image_dir") == str(tmp_path.resolve())
    assert config.chat_settings().active_config.base_url == "https://example.test/v1"
    assert config.chat_settings().active_config.model == "deepseek-test"
    assert autostart_values == [True]
    app.processEvents()


def test_modern_settings_progressively_reveals_dependent_controls(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=True)

    vision_model = dialog.findChild(settings_mod.SettingRow, "settingRow_vision_model")
    custom_background = dialog.findChild(settings_mod.SettingRow, "settingRow_chat_background_file")
    self_talk_rows = [
        dialog.findChild(settings_mod.SettingRow, f"settingRow_{key}")
        for key in (
            "self_talk_duration", "self_talk_min", "self_talk_max",
            "self_talk_texts", "self_talk_images", "self_talk_image_scale", "click_self_talk",
            "click_talk_bindings",
        )
    ]
    opacity = dialog.findChild(settings_mod.SettingRow, "settingRow_menu_opacity")

    assert vision_model.isHidden() is dialog.ai_page.vision_same.isChecked()
    dialog.ai_page.vision_same.setChecked(False)
    assert not vision_model.isHidden()
    assert custom_background.isHidden()
    dialog.ai_page.background_select.setCurrentData("custom")
    assert not custom_background.isHidden()
    dialog.self_talk_check.setChecked(False)
    assert all(row is not None and row.isHidden() for row in self_talk_rows)
    dialog.self_talk_check.setChecked(True)
    assert all(row is not None and not row.isHidden() for row in self_talk_rows)
    dialog.menu_translucent_check.setChecked(False)
    assert opacity.isHidden()
    dialog.menu_translucent_check.setChecked(True)
    assert not opacity.isHidden()
    dialog.close()
    app.processEvents()


def test_modern_settings_toggle_dependencies_hide_complete_setting_groups(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=True)

    def row(key):
        result = dialog.findChild(settings_mod.SettingRow, f"settingRow_{key}")
        assert result is not None
        return result

    island_children = [
        row(key) for key in (
            "dynamic_island_icon", "dynamic_island_name", "dynamic_island_info",
            "dynamic_island_status", "dynamic_island_info_mode",
            "dynamic_island_style", "dynamic_island_icon_value",
            "dynamic_island_custom_text",
        )
    ]
    dialog.island_enabled_check.setChecked(False)
    assert all(child.isHidden() for child in island_children)
    dialog.island_enabled_check.setChecked(True)
    assert not row("dynamic_island_icon_value").isHidden()
    assert row("dynamic_island_custom_text").isHidden()
    dialog.island_icon_check.setChecked(False)
    assert row("dynamic_island_icon_value").isHidden()
    dialog.island_info_check.setChecked(False)
    assert row("dynamic_island_info_mode").isHidden()
    dialog.island_info_mode_select.setCurrentData("custom")
    assert row("dynamic_island_custom_text").isHidden()
    dialog.island_info_check.setChecked(True)
    assert not row("dynamic_island_custom_text").isHidden()

    egg_children = [row(key) for key in ("egg_title", "egg_hint", "egg_avatar", "egg_image_dir")]
    dialog.egg_enabled_check.setChecked(False)
    assert all(child.isHidden() for child in egg_children)
    dialog.egg_enabled_check.setChecked(True)
    assert all(not child.isHidden() for child in egg_children)

    collision_children = [
        row(key) for key in (
            "collision_sound_enabled", "collision_restitution", "collision_friction",
            "collision_mass_scale", "collision_impulse_cap", "collision_sound_volume",
        )
    ]
    collision_advanced_section = row("collision_restitution").parentWidget().parentWidget()
    dialog.collision_enabled_check.setChecked(False)
    assert all(child.isHidden() for child in collision_children)
    assert collision_advanced_section.isHidden()
    dialog.collision_sound_check.setChecked(False)
    dialog.collision_enabled_check.setChecked(True)
    assert not row("collision_sound_enabled").isHidden()
    assert row("collision_sound_volume").isHidden()
    assert not collision_advanced_section.isHidden()

    dialog.close()
    app.processEvents()


def test_windows_proactive_master_and_idle_toggles_hide_dependent_rows(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.sys, "platform", "win32")
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=True)

    def row(key):
        result = dialog.findChild(settings_mod.SettingRow, f"settingRow_{key}")
        assert result is not None
        return result

    child_keys = (
        "proactive_dry_run", "proactive_preset", "proactive_dwell",
        "proactive_cooldown", "proactive_min_interval", "proactive_daily_cap",
        "proactive_require_idle", "proactive_idle_seconds", "proactive_through",
        "proactive_pre_cue", "proactive_free", "proactive_whitelist",
        "proactive_whitelist_add", "proactive_memory_clear",
    )
    dialog.pro_enabled_check.setChecked(False)
    assert all(row(key).isHidden() for key in child_keys)
    dialog.pro_idle_check.setChecked(False)
    dialog.pro_enabled_check.setChecked(True)
    assert row("proactive_idle_seconds").isHidden()
    assert all(not row(key).isHidden() for key in child_keys if key != "proactive_idle_seconds")
    dialog.pro_idle_check.setChecked(True)
    assert not row("proactive_idle_seconds").isHidden()

    dialog.close()
    app.processEvents()


def test_chat_appearance_options_follow_selected_window_and_persist_independently(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    config = Config(tmp_path)
    dialog = settings_mod.ModernSettingsDialog(config, include_ai=True)

    assert [dialog.ai_page.chat_ui_style.itemText(index) for index in range(2)] == [
        "肥鱼版 DeepSeek", "肥鱼牌小手机",
    ]
    assert dialog.ai_page.chat_ui_style.width() >= 180
    window_row = dialog.findChild(settings_mod.SettingRow, "settingRow_chat_ui_style")
    assert "宽屏现代体验" in window_row.hint_label.text()
    assert "紧凑经典体验" in window_row.hint_label.text()
    # 内置主题两种风格都可选（肥鱼版 DeepSeek 与肥鱼牌小手机一致）
    modern_options = [
        dialog.ai_page.background_select.itemText(index)
        for index in range(dialog.ai_page.background_select.count())
    ]
    assert modern_options[0] == "纯色背景"
    assert modern_options[-1] == "自定义图片"
    assert len(modern_options) > 2
    opacity_row = dialog.findChild(settings_mod.SettingRow, "settingRow_chat_background_opacity")
    fill_row = dialog.findChild(settings_mod.SettingRow, "settingRow_chat_background_fill")
    assert opacity_row.isHidden()
    assert fill_row.isHidden()

    dialog.ai_page.chat_ui_style.setCurrentData("classic")
    classic_options = [
        dialog.ai_page.background_select.itemText(index)
        for index in range(dialog.ai_page.background_select.count())
    ]
    assert classic_options[0] == "纯色背景"
    assert classic_options[-1] == "自定义图片"
    assert len(classic_options) > 2
    dialog.ai_page.background_select.setCurrentData("builtin:whale")
    assert not opacity_row.isHidden()
    assert not fill_row.isHidden()
    dialog.ai_page.background_opacity.setValue(68)
    dialog.ai_page.background_fill.setCurrentData("contain")

    dialog.ai_page.chat_ui_style.setCurrentData("modern")
    dialog.ai_page.background_select.setCurrentData("custom")
    modern_background = str((tmp_path / "modern.png").resolve())
    dialog.ai_page.background_picker.setText(modern_background)
    dialog.ai_page.background_opacity.setValue(92)
    dialog.ai_page.background_fill.setCurrentData("stretch")
    dialog._save()

    assert config.get("chat_background") == "builtin:whale"
    assert config.get("modern_chat_background") == modern_background
    assert config.get("chat_background_opacity") == 68
    assert config.get("chat_background_fill") == "contain"
    assert config.get("modern_chat_background_opacity") == 92
    assert config.get("modern_chat_background_fill") == "stretch"
    dialog.close()
    app.processEvents()


def test_quick_launch_editor_drag_order_and_checked_removal_drive_saved_menu_order(monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod

    app = QApplication.instance() or QApplication([])
    editor = settings_mod.QuickLaunchEditor([
        {"name": "A", "path": "/Applications/A.app", "kind": "application"},
        {"name": "B", "path": "/Applications/B.app", "kind": "application"},
    ])
    moved = editor.list.takeItem(1)
    editor.list.insertItem(0, moved)
    assert [item["name"] for item in editor.apps()] == ["B", "A"]
    editor.list.item(1).setCheckState(Qt.CheckState.Checked)
    editor._remove_checked()
    assert [item["name"] for item in editor.apps()] == ["B"]
    editor.close()
    app.processEvents()


def test_quick_launch_editor_uses_content_sized_rows_and_grouped_add_menu():
    from PySide6.QtWidgets import QApplication, QSizePolicy

    import pet.modern_settings_dialog as settings_mod

    app = QApplication.instance() or QApplication([])
    editor = settings_mod.QuickLaunchEditor([
        {"name": "默认浏览器", "path": "", "kind": "default_browser"},
        {"name": "Finder", "path": "/System/Library/CoreServices/Finder.app", "kind": "application"},
    ])
    editor.resize(760, 300)
    editor.show()
    app.processEvents()

    assert editor.count_label.text() == "2 个快捷项"
    assert editor.add_button.text() == "添加"
    assert [action.text() for action in editor.add_button.popupMenu().actions()] == [
        "选择应用…", "添加默认浏览器",
    ]
    assert editor.list.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Fixed
    assert editor.list.height() <= 124
    first_row = editor.list.itemWidget(editor.list.item(0))
    second_row = editor.list.itemWidget(editor.list.item(1))
    assert first_row.name_label.text() == "默认浏览器"
    assert "系统默认" in first_row.detail_label.text()
    assert second_row.name_label.text() == "Finder"
    assert "Finder.app" in second_row.detail_label.text()
    assert editor.empty_label.isHidden()

    editor.close()
    app.processEvents()


def test_quick_launch_editor_collapses_to_a_compact_empty_state_after_last_removal():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod

    app = QApplication.instance() or QApplication([])
    editor = settings_mod.QuickLaunchEditor([
        {"name": "默认浏览器", "path": "", "kind": "default_browser"},
    ])
    editor.show()
    app.processEvents()

    editor.list.item(0).setCheckState(Qt.CheckState.Checked)
    editor._remove_checked()
    app.processEvents()

    assert editor.count_label.text() == "0 个快捷项"
    assert editor.list.isHidden()
    assert not editor.empty_label.isHidden()
    assert editor.empty_label.height() <= 72

    editor.close()
    app.processEvents()


def test_quick_launch_checkbox_can_be_selected_with_a_pointer_click():
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod

    app = QApplication.instance() or QApplication([])
    editor = settings_mod.QuickLaunchEditor([
        {"name": "默认浏览器", "path": "", "kind": "default_browser"},
    ])
    editor.resize(520, 140)
    editor.show()
    app.processEvents()

    item = editor.list.item(0)
    row = editor.list.visualItemRect(item)
    QTest.mouseClick(
        editor.list.viewport(),
        Qt.MouseButton.LeftButton,
        pos=QPoint(row.left() + 14, row.center().y()),
    )
    app.processEvents()

    assert item.checkState() == Qt.CheckState.Checked
    editor.close()
    app.processEvents()


def test_macos_tool_window_stays_visible_when_application_deactivates(monkeypatch):
    from PySide6.QtCore import Qt

    import pet.window as window_mod

    attributes = []

    class FakeWindow:
        def setAttribute(self, attribute, enabled=True):
            attributes.append((attribute, enabled))

    monkeypatch.setattr(window_mod.sys, "platform", "darwin")
    window_mod._keep_macos_tool_window_visible(FakeWindow())
    assert attributes == [(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)]
    source = Path("pet/window.py").read_text(encoding="utf-8")
    set_on_top_source = source.split("def set_on_top", 1)[1].split("def showEvent", 1)[0]
    assert "WA_MacAlwaysShowToolWindow, on" not in set_on_top_source


def test_config_defaults_and_normalizes_menu_appearance_and_quick_launch(tmp_path):
    from pet.config import Config

    config = Config(tmp_path)
    appearance = config.get("context_menu_appearance")
    assert {key: appearance[key] for key in ("theme", "density", "corner_radius")} == {
        "theme": "system", "density": "standard", "corner_radius": 12,
    }
    assert config.get("quick_launch_apps") == [
        {"name": "默认浏览器", "path": "", "kind": "default_browser"}
    ]
    config.set("context_menu_appearance", {"theme": "invalid", "density": "spacious", "corner_radius": 99})
    config.set("quick_launch_apps", [{"name": "  Finder  ", "path": "/System/Library/CoreServices/Finder.app"}, {}])
    appearance = config.get("context_menu_appearance")
    assert {key: appearance[key] for key in ("theme", "density", "corner_radius")} == {
        "theme": "system", "density": "spacious", "corner_radius": 18,
    }
    assert config.get("quick_launch_apps") == [
        {"name": "Finder", "path": "/System/Library/CoreServices/Finder.app", "kind": "application"}
    ]


def test_modern_menu_adds_quick_launch_submenu_and_uses_saved_appearance(monkeypatch):
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication, QMenu

    import pet.context_menus.quick_launch as quick_launch_mod
    from pet.context_menu import populate_context_menu

    class Config:
        values = {
            "context_menu_template": "modern",
            "character": "shenshen",
            "on_top": True,
            "context_menu_appearance": {"theme": "dark", "density": "spacious", "corner_radius": 16},
            "quick_launch_apps": [
                {"name": "默认浏览器", "path": "", "kind": "default_browser"},
                {"name": "Finder", "path": "/System/Library/CoreServices/Finder.app", "kind": "application"},
            ],
        }

        def get(self, key, default=None):
            return self.values.get(key, default)

    class Pet:
        cfg = Config()
        on_open_chat = on_open_modern_settings = on_spawn_pet = None
        idles = turns = moves = clicks = acts = []
        playback_speed = scale = 1.0
        drag_physics = no_move = False

        def icon_pixmap(self, size=64):
            pixmap = QPixmap(size, size)
            pixmap.fill()
            return pixmap

        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    launched = []
    monkeypatch.setattr(quick_launch_mod, "launch_quick_app", lambda app: launched.append(app) or True)
    app = QApplication.instance() or QApplication([])
    menu = QMenu()
    populate_context_menu(menu, Pet())
    shortcut = next(action for action in menu.actions() if action.text() == "快捷启动")
    assert shortcut.menu() is not None
    assert [action.text() for action in shortcut.menu().actions()] == ["默认浏览器", "Finder"]
    assert all(not action.icon().isNull() for action in shortcut.menu().actions())
    shortcut.menu().actions()[1].trigger()
    assert launched == [Config.values["quick_launch_apps"][1]]
    assert menu.property("modernTheme") == "dark"
    assert menu.property("modernDensity") == "spacious"
    assert "background-color: rgba(37, 37, 37, 240)" in menu.styleSheet()
    assert "border-radius: 16px" in menu.styleSheet()
    menu.close()
    app.processEvents()


def test_modern_settings_search_locates_rows_and_return_does_not_close(tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=True)
    dialog.show()
    dialog.search_edit.setFocus()
    dialog.search_edit.setText("API 地址")
    app.processEvents()
    assert dialog.sidebar.currentRow() == 5  # 稳定的“AI 与对话”能力域
    api_row = dialog.findChild(settings_mod.SettingRow, "settingRow_api_url")
    assert api_row.property("searchMatch") is True
    QTest.keyClick(dialog.search_edit, Qt.Key.Key_Return)
    app.processEvents()
    assert dialog.isVisible()
    assert dialog.result() == 0
    dialog.close()
    app.processEvents()


def test_legacy_config_value_dispatches_legacy_layout(monkeypatch):
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication, QMenu

    import pet.window as window_mod

    class Config:
        def get(self, key, default=None):
            return {"context_menu_template": "legacy", "character": "shenshen"}.get(key, default)

    class Pet:
        cfg = Config()
        on_open_chat = on_open_chat_settings = on_open_legacy_settings = None
        on_open_modern_settings = None
        on_spawn_pet = lambda self: None
        idles = turns = moves = clicks = acts = []
        playback_speed = scale = 1.0
        drag_physics = no_move = False

        @staticmethod
        def icon_pixmap(size=64):
            pixmap = QPixmap(size, size)
            pixmap.fill()
            return pixmap

        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(window_mod.autostart_mod, "is_enabled", lambda: False)
    monkeypatch.setattr(window_mod.catalog, "list_available_characters", lambda: ["shenshen"])
    menu = QMenu()
    window_mod._populate_context_menu(menu, Pet())
    labels = [action.text() for action in menu.actions() if not action.isSeparator()]
    # legacy 布局：无图标、无现代专属入口（看看屏幕/更新与帮助/生小肥鱼层级不同）
    # Pet 无 on_open_chat，属于纯桌宠版：不显示 DeepSeek Harness，保留网页版
    assert labels.index("生小肥鱼") == labels.index("开机自启") + 1
    assert "启动 DeepSeek Harness" not in labels
    assert "打开网页版 DeepSeek" in labels
    assert menu.styleSheet() == ""
    icon_actions = [action.text() for action in menu.actions() if not action.icon().isNull()]
    assert icon_actions == []
    assert "看看屏幕" not in labels
    assert "更新与帮助" not in labels
    assert "切换角色" in labels
    assert "窗口置顶" in labels
    menu.close()
    app.processEvents()


def test_legacy_config_value_uses_legacy_settings_callback(monkeypatch):
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication, QMenu

    from pet.context_menu import populate_context_menu

    class Config:
        def get(self, key, default=None):
            return {"context_menu_template": "legacy", "character": "shenshen"}.get(key, default)

    class Pet:
        cfg = Config()
        on_open_chat = None
        on_open_chat_settings = None
        on_open_legacy_settings = None
        on_open_modern_settings = None
        on_spawn_pet = None
        idles = turns = moves = clicks = acts = []
        playback_speed = scale = 1.0
        drag_physics = no_move = False

        def __init__(self):
            self.opened = []
            self.on_open_legacy_settings = lambda: self.opened.append("legacy")
            self.on_open_modern_settings = lambda: self.opened.append("modern")

        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    app = QApplication.instance() or QApplication([])
    pet = Pet()
    menu = QMenu()
    populate_context_menu(menu, pet)
    # legacy 模板的「桌宠设置」走旧版设置回调（不再是现代设置）
    next(action for action in menu.actions() if action.text() == "桌宠设置").trigger()
    assert pet.opened == ["legacy"]
    menu.close()
    app.processEvents()


def test_runtime_pet_icon_crops_transparent_canvas_before_scaling():
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtWidgets import QApplication

    from pet.window import PetWindow

    class FakePet:
        idle = None
        lib = None

    app = QApplication.instance() or QApplication([])
    source = QPixmap(100, 100)
    source.fill(Qt.GlobalColor.transparent)
    painter = QPainter(source)
    painter.fillRect(QRect(45, 40, 10, 20), Qt.GlobalColor.blue)
    painter.end()
    fake = FakePet()
    fake._frame_pixmap = source

    icon = PetWindow.icon_pixmap(fake, 32)
    assert max(icon.width(), icon.height()) >= 30
    app.processEvents()


def test_windows_build_regenerates_the_icon_before_pyinstaller():
    build_script = Path("scripts/build_onedir.ps1").read_text(encoding="utf-8")
    make_icon = build_script.index("scripts\\make_icon.py")
    pyinstaller = build_script.index("python -m PyInstaller")
    assert make_icon < pyinstaller
    assert "assets\\big_blue_fat_fish;assets\\big_blue_fat_fish" in build_script
    assert "pet\\menu_templates;pet\\menu_templates" in build_script
    mac_build_script = Path("scripts/build_macos.sh").read_text(encoding="utf-8")
    assert 'assets/big_blue_fat_fish:assets/big_blue_fat_fish' in mac_build_script
    assert "pet/menu_templates:pet/menu_templates" in mac_build_script


def test_modern_animation_leaf_icons_are_loaded_only_when_category_opens(monkeypatch):
    import time

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication, QMenu

    from pet.context_menu import populate_context_menu

    class Config(dict):
        def get(self, key, default=None):
            return super().get(key, default)

    class Pet:
        cfg = Config(context_menu_template="modern", character="shenshen", on_top=False)
        on_open_chat = on_open_modern_settings = on_spawn_pet = None
        idles = ["idle-a"]
        turns = ["turn-a"]
        moves = ["move-a"]
        clicks = ["click-a"]
        acts = ["act-a"]
        playback_speed = scale = 1.0
        drag_physics = no_move = False

        def __init__(self):
            self.icon_requests = 0

        def animation_icon_image(self, name):
            self.icon_requests += 1
            image = QImage(64, 64, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.blue if name.endswith("a") else Qt.GlobalColor.red)
            return image

        def __getattr__(self, _name):
            return lambda *_args, **_kwargs: None

    app = QApplication.instance() or QApplication([])
    menu = QMenu()
    pet = Pet()
    populate_context_menu(menu, pet)
    # Opening the root menu must not synchronously decode every animation.
    assert pet.icon_requests == 0
    animations = next(submenu for submenu in menu._owned_submenus if submenu.title() == "播放动画")
    idle = next(submenu for submenu in animations._owned_submenus if submenu.title() == "待机")
    assert idle.actions() == [], "根菜单构建不应同步填充动画分类动作"
    idle.aboutToShow.emit()
    placeholder_key = idle.actions()[0].icon().cacheKey()
    assert not idle.actions()[0].icon().isNull()
    deadline = time.monotonic() + 1.0
    while (
        (pet.icon_requests < 1 or idle.actions()[0].icon().cacheKey() == placeholder_key)
        and time.monotonic() < deadline
    ):
        app.processEvents()
        time.sleep(0.01)
    assert pet.icon_requests == 1
    assert not idle.actions()[0].icon().isNull()
    assert idle.actions()[0].icon().cacheKey() != placeholder_key
    menu.close()
    app.processEvents()


def test_animation_leaf_icon_crops_transparent_frame_before_filling_slot():
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QBitmap, QPainter, QPixmap, QRegion
    from PySide6.QtWidgets import QApplication, QMenu

    from pet.context_menus.icons import fitted_pet_pixmap_icon
    from pet.context_menus.menu_styles.modern import apply_modern_menu_style

    app = QApplication.instance() or QApplication([])
    menu = QMenu()
    apply_modern_menu_style(menu)
    source = QPixmap(640, 360)
    source.fill(Qt.GlobalColor.transparent)
    painter = QPainter(source)
    painter.fillRect(QRect(295, 120, 50, 120), Qt.GlobalColor.blue)
    painter.end()

    rendered = fitted_pet_pixmap_icon(menu, source).pixmap(18, 18)
    bounds = QRegion(QBitmap.fromImage(rendered.toImage().createAlphaMask())).boundingRect()
    assert max(bounds.width(), bounds.height()) >= 16
    app.processEvents()


def test_reopened_animation_menu_reuses_cached_images_immediately():
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication, QMenu

    from pet.context_menus.shared import build_animation_categories
    from pet.context_menus.menu_styles.modern import apply_modern_menu_style

    class Pet:
        idles = ["idle-a"]
        turns = moves = clicks = acts = []

        def animation_icon_cached_image(self, name):
            assert name == "idle-a"
            image = QImage(64, 64, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.blue)
            return image

        def animation_icon_image(self, _name):
            raise AssertionError("cached thumbnail must not be decoded again")

        def __getattr__(self, _name):
            return lambda *_args, **_kwargs: None

    app = QApplication.instance() or QApplication([])
    root = QMenu()
    apply_modern_menu_style(root)
    build_animation_categories(root, Pet(), icons=False, leaf_role_icons=True)
    idle = root._owned_submenus[0]
    assert idle.actions() == [], "根菜单构建不应同步填充动画分类动作"
    idle.aboutToShow.emit()
    assert idle.actions(), "首次展开分类子菜单时应填充动作"
    assert not idle.actions()[0].icon().isNull()
    app.processEvents()


def test_representative_animation_image_is_cached_on_pet(monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage

    import pet.window as window_mod
    from pet.window import PetWindow

    calls = []

    def decode(path):
        calls.append(path)
        image = QImage(64, 64, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.blue)
        return image

    class Clip:
        path = "/tmp/cached-animation.webm"

    class Library:
        def movie(self, _name):
            return Clip()

        def clip_path(self, _name):
            return Clip.path

    class Pet:
        lib = Library()

    monkeypatch.setattr(window_mod, "decode_representative_frame", decode)
    pet = Pet()
    first = PetWindow.animation_icon_image(pet, "idle-a")
    second = PetWindow.animation_icon_image(pet, "idle-a")
    assert not first.isNull() and not second.isNull()
    assert calls == ["/tmp/cached-animation.webm"]


def test_representative_animation_decode_is_shared_across_pets(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from PySide6.QtGui import QImage

    import pet.animation_thumbnail as thumbnail

    path = tmp_path / "shared.webm"
    path.write_bytes(b"fixture")
    thumbnail._image_cache.clear()
    thumbnail._inflight.clear()
    calls = []

    def decode(_path):
        calls.append(1)
        return QImage(8, 8, QImage.Format.Format_ARGB32)

    monkeypatch.setattr(thumbnail, "_decode_representative_frame", decode)
    with ThreadPoolExecutor(max_workers=4) as pool:
        images = list(pool.map(thumbnail.decode_representative_frame, [path] * 4))

    assert len(calls) == 1
    assert all(not image.isNull() for image in images)
    assert thumbnail.decode_representative_frame(tmp_path / "missing.webm").isNull()


def test_long_submenus_enable_qt_scrolling():
    from PySide6.QtWidgets import QApplication, QMenu, QStyle

    from pet.context_menus.menu_styles.common import install_responsive_menu_style

    app = QApplication.instance() or QApplication([])
    menu = QMenu()
    for index in range(100):
        menu.addAction(f"动作 {index}")
    install_responsive_menu_style(menu)
    assert menu.style().styleHint(QStyle.StyleHint.SH_Menu_Scrollable, None, menu) == 1
    app.processEvents()


def test_visible_animation_submenu_does_not_relayout_when_thumbnail_finishes():
    import time

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication, QMenu

    from pet.context_menus.shared import build_animation_categories
    from pet.context_menus.menu_styles.modern import apply_modern_menu_style

    class Pet:
        idles = ["idle-a"]
        turns = moves = clicks = acts = []

        def __init__(self):
            self.cached = {}

        def animation_icon_cached_image(self, name):
            return self.cached.get(name, QImage())

        def animation_icon_image(self, name):
            image = QImage(64, 64, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.blue)
            self.cached[name] = image
            return image

        def __getattr__(self, _name):
            return lambda *_args, **_kwargs: None

    app = QApplication.instance() or QApplication([])
    root = QMenu()
    apply_modern_menu_style(root)
    pet = Pet()
    build_animation_categories(root, pet, icons=False, leaf_role_icons=True)
    submenu = root._owned_submenus[0]
    assert submenu.actions() == [], "根菜单构建不应同步填充动画分类动作"
    submenu.aboutToShow.emit()
    placeholder_key = submenu.actions()[0].icon().cacheKey()
    # popup() 在无交互桌面的测试环境中可能不会进入可见状态，
    # 改用 show() 稳定模拟“菜单已展开”，从而覆盖可见时不重排图标的保护逻辑。
    submenu.show()
    deadline = time.monotonic() + 1.0
    while not pet.cached and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert pet.cached
    assert submenu.actions()[0].icon().cacheKey() == placeholder_key
    submenu.hide()
    submenu.aboutToShow.emit()
    assert submenu.actions()[0].icon().cacheKey() != placeholder_key


def test_animation_hover_only_dispatches_bounded_background_work():
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication, QMenu

    from pet.context_menus.shared import build_animation_categories
    from pet.context_menus.menu_styles.modern import apply_modern_menu_style

    class Pet:
        idles = turns = moves = clicks = []
        acts = [f"act-{index}" for index in range(80)]

        def animation_icon_cached_image(self, _name):
            return QImage()

        def animation_icon_image(self, _name):
            return QImage()

        def __getattr__(self, _name):
            return lambda *_args, **_kwargs: None

    app = QApplication.instance() or QApplication([])
    root = QMenu()
    apply_modern_menu_style(root)
    build_animation_categories(root, Pet(), icons=False, leaf_role_icons=True)
    submenu = root._owned_submenus[0]
    submenu.aboutToShow.emit()
    assert len(submenu._animation_icon_workers) <= 2
    app.processEvents()


def test_on_top_pet_reasserts_native_level_after_context_menu_hides():
    from PySide6.QtWidgets import QApplication

    from pet.window import PetWindow

    class Config(dict):
        def get(self, key, default=None):
            return super().get(key, default)

    class Pet:
        cfg = Config(on_top=True)

        def __init__(self):
            self.levels = []

        def _schedule_macos_window_level(self, enabled):
            self.levels.append(enabled)

        def setAttribute(self, *_args):
            pass

    QApplication.instance() or QApplication([])
    pet = Pet()
    PetWindow._restore_on_top_after_context_menu(pet)
    assert pet.levels == [True]


def test_disabled_on_top_does_not_raise_after_context_menu_hides():
    from PySide6.QtWidgets import QApplication

    from pet.window import PetWindow

    class Pet:
        cfg = {"on_top": False}

        def __init__(self):
            self.levels = []

        def _schedule_macos_window_level(self, enabled):
            self.levels.append(enabled)

    QApplication.instance() or QApplication([])
    pet = Pet()
    PetWindow._restore_on_top_after_context_menu(pet)
    assert pet.levels == []


def test_animation_icon_pixmap_reads_named_clip_without_switching_active_animation():
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtWidgets import QApplication

    from pet.window import PetWindow

    app = QApplication.instance() or QApplication([])
    source = QPixmap(100, 80)
    source.fill(Qt.GlobalColor.transparent)
    painter = QPainter(source)
    painter.fillRect(QRect(35, 10, 30, 60), Qt.GlobalColor.blue)
    painter.end()

    class Clip:
        def __init__(self): self.jumps = []
        def frameCount(self): return 50
        def currentPixmap(self): return source
        def jumpToFrame(self, frame): self.jumps.append(frame); return True

    clip = Clip()

    class Library:
        def movie(self, name):
            assert name == "动画-A"
            return clip

        def clip_path(self, name):
            assert name == "动画-A"
            return "/tmp/动画-A.webm"

    class FakePet:
        lib = Library()
        anim = "当前动画"

    icon = PetWindow.animation_icon_pixmap(FakePet(), "动画-A", 32)
    assert clip.jumps == [30]
    assert not icon.isNull()
    assert max(icon.width(), icon.height()) >= 30
    assert FakePet.anim == "当前动画"


def test_representative_animation_frame_uses_the_later_middle():
    from pet.animation_thumbnail import representative_frame_index

    assert representative_frame_index(1) == 0
    assert representative_frame_index(50) == 30
    assert representative_frame_index(100) == 61


def test_template_reopen_reuses_original_visible_menu_position(monkeypatch):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication, QMenu

    import pet.window as window_mod
    from pet.window import PetWindow

    class FakePet:
        _context_menu_anchor = QPoint(320, 240)

        def __init__(self):
            self.shown_at = None

        def _show_context_menu(self, point):
            self.shown_at = QPoint(point)

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(window_mod.QTimer, "singleShot", lambda *args: args[-1]())
    menu = QMenu()
    menu.move(410, 260)
    fake = FakePet()
    PetWindow.reopen_context_menu(fake, menu)
    assert fake.shown_at == QPoint(410, 260)
    menu.close()
    app.processEvents()


def test_menu_easter_egg_and_modern_theme_fields_are_configurable(tmp_path):
    from pet.config import Config

    config = Config(tmp_path)
    appearance = config.get("context_menu_appearance")
    assert appearance["ui_font_size"] == 13
    assert appearance["translucent"] is True
    assert appearance["light_background"] == "#ffffff"
    assert appearance["dark_background"] == "#252525"
    egg = config.get("menu_easter_egg")
    assert egg["enabled"] is True
    assert egg["title"] == "厉害了我的鲸"
    assert egg["avatar"] == "assets/big_blue_fat_fish/ojingjing.jpg"
    assert egg["image_dir"] == "assets/big_blue_fat_fish"
    config.set("menu_easter_egg", {"title": "秘密入口", "hint": "打开", "enabled": False})
    config.set("context_menu_appearance", {
        "theme": "dark", "ui_font": "PingFang SC", "ui_font_size": 16,
        "translucent": False, "opacity": 0.7,
        "light_background": "bad", "dark_background": "#111213",
    })
    assert config.get("menu_easter_egg")["title"] == "秘密入口"
    assert config.get("menu_easter_egg")["enabled"] is False
    assert config.get("context_menu_appearance")["ui_font_size"] == 16
    assert config.get("context_menu_appearance")["light_background"] == "#ffffff"
    assert config.get("context_menu_appearance")["dark_background"] == "#111213"


def test_easter_egg_config_preserves_custom_paths(tmp_path):
    from pet.config import Config

    config = Config(tmp_path)
    config.set("menu_easter_egg", {
        "avatar": "/custom/avatar.png",
        "image_dir": "/custom/images",
    })
    custom = config.get("menu_easter_egg")
    assert custom["avatar"] == "/custom/avatar.png"
    assert custom["image_dir"] == "/custom/images"


def test_easter_egg_path_and_color_controls_use_native_pickers(tmp_path, monkeypatch):
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    image = (tmp_path / "avatar.webp").resolve()
    image.write_bytes(b"image")
    image_dir = tmp_path.resolve()
    monkeypatch.setattr(settings_mod.QFileDialog, "getOpenFileName", lambda *args, **kwargs: (str(image), ""))
    monkeypatch.setattr(settings_mod.QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(image_dir))
    monkeypatch.setattr(settings_mod.QColorDialog, "getColor", lambda *args, **kwargs: QColor("#123456"))
    dialog = settings_mod.ModernSettingsDialog(Config(tmp_path / "cfg"), include_ai=False)
    dialog.egg_avatar_picker.choose()
    dialog.egg_image_dir_picker.choose()
    dialog.light_background_picker.choose()
    assert dialog.egg_avatar_picker.text() == str(image)
    assert dialog.egg_image_dir_picker.text() == str(image_dir)
    assert dialog.light_background_picker.text() == "#123456"
    assert "*.webp" in dialog.egg_avatar_picker.name_filter
    dialog.close()
    app.processEvents()


def test_easter_egg_menu_text_elides_when_content_is_too_long():
    from PySide6.QtWidgets import QApplication, QMenu

    from pet.context_menus.fun_entry import OjingjingMenuEntry

    app = QApplication.instance() or QApplication([])
    menu = QMenu()
    entry = OjingjingMenuEntry(menu, {"title": "非常长的彩蛋标题" * 10, "hint": "非常长的提示" * 8})
    entry.resize(224, 39)
    app.processEvents()
    assert entry.title_label.displayText().endswith("…")
    assert entry.click_accessory.displayText().endswith("…")
    entry.close()
    menu.close()
    app.processEvents()


def test_easter_egg_first_row_font_tracks_modern_menu_ui_size():
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QMenu

    from pet.context_menus.fun_entry import OjingjingMenuEntry

    app = QApplication.instance() or QApplication([])
    menu = QMenu()
    font = QFont(menu.font())
    font.setPixelSize(17)
    menu.setFont(font)
    entry = OjingjingMenuEntry(menu, {})
    assert entry.title_label.font().pixelSize() == 17
    assert entry.click_accessory.font().pixelSize() == 15
    entry.close()
    menu.close()
    app.processEvents()


def test_easter_egg_text_remains_readable_in_the_dark_menu_theme():
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication, QMenu

    from pet.context_menus.fun_entry import OjingjingMenuEntry
    from pet.context_menus.menu_styles.modern import apply_modern_menu_style

    app = QApplication.instance() or QApplication([])
    menu = QMenu()
    apply_modern_menu_style(menu, {"theme": "dark"})
    entry = OjingjingMenuEntry(menu, {"title": "彩蛋入口", "hint": "请点击"})
    entry.show()
    app.processEvents()

    title_color = entry.title_label.palette().color(QPalette.ColorRole.WindowText)
    accessory_image = entry.click_accessory.grab().toImage()
    accessory_lightness = max(
        accessory_image.pixelColor(x, y).lightness()
        for x in range(accessory_image.width())
        for y in range(accessory_image.height())
    )
    assert title_color.lightness() >= 180
    assert accessory_lightness >= 160

    entry.close()
    menu.close()
    app.processEvents()


def test_easter_egg_hover_surface_stays_dark_in_the_dark_menu_theme():
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QEnterEvent
    from PySide6.QtWidgets import QApplication, QMenu

    from pet.context_menus.fun_entry import OjingjingMenuEntry
    from pet.context_menus.menu_styles.modern import apply_modern_menu_style

    app = QApplication.instance() or QApplication([])
    menu = QMenu()
    apply_modern_menu_style(menu, {"theme": "dark", "dark_hover": "#3a3a3a"})
    entry = OjingjingMenuEntry(menu, {"title": "彩蛋入口", "hint": "请点击"})
    entry.show()
    QApplication.sendEvent(
        entry,
        QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1)),
    )
    app.processEvents()

    image = entry.grab().toImage()
    hover_surface = image.pixelColor(4, entry.height() // 2)
    assert hover_surface.lightness() < 100

    entry.close()
    menu.close()
    app.processEvents()


def test_template_switch_requests_immediate_menu_reopen():
    from PySide6.QtWidgets import QApplication, QMenu

    from pet.context_menus.shared import add_template_switch

    class Pet:
        def __init__(self):
            self.template = None
            self.reopened = None

        def set_context_menu_template(self, template):
            self.template = template

        def reopen_context_menu(self, menu):
            self.reopened = menu

    app = QApplication.instance() or QApplication([])
    menu = QMenu()
    pet = Pet()
    action = add_template_switch(menu, pet, "切换到新版菜单", "modern")
    action.trigger()
    assert pet.template == "modern"
    assert pet.reopened is menu
    menu.close()
    app.processEvents()


def test_color_picker_uses_stable_painted_swatch_instead_of_border_hack():
    from PySide6.QtWidgets import QApplication

    from pet.modern_settings_dialog import ColorPicker, ColorSwatchButton

    app = QApplication.instance() or QApplication([])
    picker = ColorPicker("#171717")
    assert isinstance(picker.button, ColorSwatchButton)
    assert picker.button.color().name() == "#171717"
    assert "border-left" not in picker.button.styleSheet()
    picker.edit.setText("#eeeeee")
    assert picker.button.color().name() == "#eeeeee"
    picker.close()
    app.processEvents()


def test_dock_icon_visibility_defaults_on_and_is_saved_by_modern_settings(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.sys, "platform", "darwin")
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    config = Config(tmp_path)
    assert config.get("show_dock_icon") is True
    dialog = settings_mod.ModernSettingsDialog(config, include_ai=False)
    dialog.dock_icon_check.setChecked(False)
    dialog._save()
    assert config.get("show_dock_icon") is False
    app.processEvents()


def test_product_copy_has_no_external_brand_reference():
    forbidden = ("co" + "dex").lower()
    roots = [Path("pet"), Path("tests"), Path("docs"), Path("README.md")]
    hits = []
    for root in roots:
        paths = [root] if root.is_file() else list(root.rglob("*"))
        for path in paths:
            if not path.is_file() or path.suffix.lower() not in {".py", ".qss", ".md", ".json"}:
                continue
            # Competitive research records source names by design; they are
            # evidence, not user-facing product copy.
            if path.name.endswith("-RESEARCH.md"):
                continue
            if forbidden in path.read_text(encoding="utf-8", errors="ignore").lower():
                hits.append(str(path))
    assert hits == []


def test_return_corner_cancels_all_position_writers_before_move():
    from PySide6.QtCore import QRect

    from pet.window import PetWindow

    calls = []

    class Screen:
        @staticmethod
        def availableGeometry():
            return QRect(0, 0, 1000, 700)

        @staticmethod
        def name():
            return "test"

        @staticmethod
        def devicePixelRatio():
            return 1.0

    class FakePet:
        _w = 120
        _h = 180
        _dragging = False
        _press_global = None
        _grab_offset = None

        def _cancel_move(self): calls.append("move")
        def _stop_physics(self): calls.append("physics")
        def _screen_available(self): return Screen()
        def move(self, x, y): calls.append((x, y))
        def _save_position(self): calls.append("save")

    PetWindow._go_default_corner(FakePet())
    assert calls[:2] == ["move", "physics"]
    assert calls[-1] == "save"
    assert (855, 519) in calls


def test_macos_on_top_reapplies_native_level_without_stale_window_id():
    source = Path("pet/window.py").read_text(encoding="utf-8")
    method = source.split("def set_on_top", 1)[1].split("def showEvent", 1)[0]
    assert "_schedule_macos_window_level" in method
    assert "int(self.winId())" not in method
    helper = source.split("def _schedule_macos_window_level", 1)[1].split("def set_on_top", 1)[0]
    assert "self.winId()" in helper
    assert "40" in helper


def test_context_menu_leaf_actions_stay_open_until_focus_is_lost():
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication, QMenu

    from pet.context_menus.menu_styles.common import install_stay_open_interaction

    app = QApplication.instance() or QApplication([])
    menu = QMenu()
    action = menu.addAction("拖动物理")
    action.setCheckable(True)
    install_stay_open_interaction(menu)
    menu.popup(QPoint(30, 30))
    app.processEvents()
    rect = menu.actionGeometry(action)
    pos = rect.center()
    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease, pos, menu.mapToGlobal(pos),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(menu, event)
    app.processEvents()
    assert action.isChecked() is True
    assert menu.isVisible() is True
    menu.close()
    app.processEvents()


def test_window_opening_menu_action_uses_native_close_path():
    from PySide6.QtWidgets import QApplication, QMenu

    from pet.context_menus.shared import add_action

    app = QApplication.instance() or QApplication([])
    menu = QMenu()
    action = add_action(menu, "桌宠设置", None, lambda: None, close_on_trigger=True)
    assert action.property("closeOnTrigger") is True
    menu.close()
    app.processEvents()


def test_macos_dock_icon_policy_tracks_the_saved_visibility_setting():
    source = Path("pet/app.py").read_text(encoding="utf-8")
    helper = source.split("def _mac_set_dock_icon_visible", 1)[1].split("def main", 1)[0]
    assert "NSApplicationActivationPolicyRegular = 0" in helper
    assert "Accessory = 1" in helper
    assert "setActivationPolicy:" in helper
    main = source.split("def main", 1)[1]
    assert '_mac_set_dock_icon_visible(bool(config.get("show_dock_icon", True)))' in main


def test_pet_app_binds_about_to_quit_once_to_current_window(tmp_path, monkeypatch):
    """aboutToQuit 只绑定一次，且触发时保存「当前」有效窗口位置（非已销毁的旧窗口）。"""
    from PySide6.QtWidgets import QApplication

    import pet.app as app_mod
    from pet.app import AppShell
    from pet.config import Config

    QApplication.instance() or QApplication([])

    class FakeApp:
        def __init__(self):
            self.connections = []

        @property
        def aboutToQuit(self):
            # 模拟信号对象：self.app.aboutToQuit.connect(slot) 追加到 connections
            return self

        def connect(self, slot):
            self.connections.append(slot)

    class FakeWin:
        def __init__(self):
            self.saved = 0

        def save_position(self):
            self.saved += 1

    monkeypatch.setattr(app_mod.QTimer, "singleShot", lambda *a, **k: None)
    owner = AppShell(FakeApp(), Config(tmp_path))
    owner.instance._create_ui = lambda cid: None
    owner.instance._apply_spawn_offset = lambda: None
    owner._apply_balance_timer = lambda: None

    old = FakeWin()
    owner.instance.win = old
    owner.start()
    owner.start()  # 重复 start 不得叠加 connect
    assert len(owner.app.connections) == 1
    assert owner.app.connections[0] == owner._on_about_to_quit

    current = FakeWin()
    owner.instance.win = current
    owner.app.connections[0]()  # 触发 aboutToQuit
    assert current.saved == 1
    assert old.saved == 0  # 旧窗口不再被保存


def test_external_character_dirs_uses_variant_then_legacy_fallback(tmp_path, monkeypatch):
    """外部角色目录应优先变体目录，并保留旧 dsh-pet-standalone 目录兜底。"""
    import sys

    import pet.catalog as catalog_mod
    from pet import config as config_mod

    monkeypatch.setattr(config_mod, "APP_DIR_NAME", "dsh-pet-standalone-webm-chat")
    # 数据目录按平台走 APPDATA / Library/Application Support / XDG_CONFIG_HOME
    if sys.platform == "win32":
        root = tmp_path
        monkeypatch.setenv("APPDATA", str(tmp_path))
    elif sys.platform == "darwin":
        root = tmp_path / "Library" / "Application Support"
        monkeypatch.setenv("HOME", str(tmp_path))
    else:
        root = tmp_path
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    dirs = catalog_mod.external_character_dirs()
    variant = root / "dsh-pet-standalone-webm-chat" / "characters"
    legacy = root / "dsh-pet-standalone" / "characters"
    assert variant in dirs
    assert legacy in dirs
    assert dirs.index(variant) < dirs.index(legacy)  # 新目录优先


def test_external_character_dirs_dedupes_base_dir(tmp_path, monkeypatch):
    """非变体（APP_DIR_NAME == 默认）时旧目录不被重复追加。"""
    import sys

    import pet.catalog as catalog_mod
    from pet import config as config_mod

    monkeypatch.setattr(config_mod, "APP_DIR_NAME", "dsh-pet-standalone")
    if sys.platform == "win32":
        root = tmp_path
        monkeypatch.setenv("APPDATA", str(tmp_path))
    elif sys.platform == "darwin":
        root = tmp_path / "Library" / "Application Support"
        monkeypatch.setenv("HOME", str(tmp_path))
    else:
        root = tmp_path
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    dirs = catalog_mod.external_character_dirs()
    base = root / "dsh-pet-standalone" / "characters"
    assert dirs.count(base) == 1
def test_self_talk_deleted_external_dir_falls_back_to_text_not_bundled(tmp_path):
    """回归：用户显式配置的外部图片目录被删后，不再回退内置彩蛋池
    （用户删目录的意图就是不要再看图）。"""
    from pet.window import _resolve_self_talk_image_dir

    gone = tmp_path / "deleted_dir"
    assert _resolve_self_talk_image_dir(str(gone)) == ""

    existing = tmp_path / "pics"
    existing.mkdir()
    assert _resolve_self_talk_image_dir(str(existing)) == str(existing)

    # 相对路径（内置 assets）仍走既有回退解析
    assert _resolve_self_talk_image_dir("") == ""
# --- Custom image-directory gallery regression (2026-09-03) ---


def test_image_directory_picker_opens_right_drawer_with_three_column_masonry(tmp_path):
    from PIL import Image
    from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout

    from pet.modern_settings_dialog import ImagePreviewDrawer, ResourcePathPicker

    short = tmp_path / "01-one.png"
    long = tmp_path / ("02-" + "very-long-image-name-" * 8 + ".jpg")
    Image.new("RGB", (40, 80), "red").save(short)
    Image.new("RGB", (100, 40), "blue").save(long)
    Image.new("RGB", (80, 80), "green").save(tmp_path / "03-square.png")
    Image.new("RGB", (120, 50), "yellow").save(tmp_path / "04-fourth.png")
    (tmp_path / "ignore.txt").write_text("ignore", encoding="utf-8")
    app = QApplication.instance() or QApplication([])
    host = QDialog()
    host.resize(1000, 680)
    layout = QVBoxLayout(host)
    picker = ResourcePathPicker(str(tmp_path), directory=True, image_preview=True, parent=host)
    layout.addWidget(picker)
    host.show()
    app.processEvents()

    assert picker.preview_button.text() == "预览"
    assert host.findChild(ImagePreviewDrawer, "imagePreviewDrawer") is None
    picker.preview_button.click()
    app.processEvents()
    drawer = host.findChild(ImagePreviewDrawer, "imagePreviewDrawer")
    assert drawer is not None and not drawer.isHidden()
    assert drawer.geometry().right() == host.rect().right()
    assert drawer.flow.layout.column_count == 3
    assert drawer.count_label.text() == "4 张图片"
    assert len(drawer.flow.cards) == 4
    assert drawer.flow.cards[1].toolTip() == long.name
    assert drawer.flow.cards[3].y() < drawer.flow.cards[0].geometry().bottom()
    assert drawer.flow.cards[3].x() == drawer.flow.cards[1].x()
    host.close()
    app.processEvents()


def test_cleanup_old_pet_logs(tmp_path):
    """审查 GLM-M2 回归：启动时清理超龄 pet-*.log，不动新日志与其他文件。"""
    import os
    import time

    from pet.app import _cleanup_old_pet_logs

    old = tmp_path / "pet-111.log"
    old.write_text("x", encoding="utf-8")
    old_ts = time.time() - 8 * 86400
    os.utime(old, (old_ts, old_ts))
    old_backup = tmp_path / "pet-111.log.1"
    old_backup.write_text("x", encoding="utf-8")
    os.utime(old_backup, (old_ts, old_ts))
    fresh = tmp_path / "pet-222.log"
    fresh.write_text("y", encoding="utf-8")
    other = tmp_path / "config.json"
    other.write_text("z", encoding="utf-8")
    os.utime(other, (time.time() - 30 * 86400,) * 2)  # 老但非 pet 日志

    assert _cleanup_old_pet_logs(tmp_path) == 2
    assert not old.exists() and not old_backup.exists()
    assert fresh.exists() and other.exists()


def test_check_update_failure_reports_and_reentry_guard(tmp_path, monkeypatch):
    """审查 P1-01/GLM-L3 回归：更新检查线程异常收口回 GUI + 重入防护。

    （修复批复审 P2：payload 不再带重复前缀；重入必须在请求进行中验证，
    且完成后必须能再次发起。）
    """
    import threading
    import time

    from PySide6.QtWidgets import QApplication

    from pet import app as app_mod
    from pet.app import AppShell
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    owner = AppShell(app, Config(tmp_path))

    gate = threading.Event()

    def boom():
        gate.wait(5)
        raise RuntimeError("boom")

    monkeypatch.setattr(app_mod.updater, "latest_release", boom)
    owner.check_update(parent=None)
    first_bridge = owner._update_bridge
    results = []
    first_bridge.done.connect(lambda ok, payload: results.append((bool(ok), str(payload))))

    # 请求进行中再次调用 → 闸门拦截，不换 bridge、不起新线程
    owner.check_update(parent=None)
    assert owner._update_bridge is first_bridge
    assert owner._update_checking is True

    gate.set()
    deadline = time.monotonic() + 5
    while not results and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert results == [(False, "boom")]  # 前缀由 UI 层统一加，payload 不带

    # 完成后闸门放行，可再次发起
    deadline = time.monotonic() + 5
    while owner._update_checking and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert owner._update_checking is False
    gate.clear()
    owner.check_update(parent=None)
    assert owner._update_bridge is not first_bridge
    # 收尾：放行第二个线程，避免泄漏到后续测试
    gate.set()
    deadline = time.monotonic() + 5
    while owner._update_checking and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


def test_modern_settings_reject_saves_config(tmp_path):
    """审查 DS-M9 回归：Esc（reject）路径与 X 一样落盘。"""
    from PySide6.QtWidgets import QApplication

    from pet import modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    cfg = Config(tmp_path)
    dlg = settings_mod.ModernSettingsDialog(cfg, include_ai=False)
    dlg.self_talk_image_scale_spin.setValue(180)
    dlg.reject()
    app.processEvents()
    assert Config(tmp_path).get("self_talk_image_scale") == 180


def test_settings_image_directories_use_preview_but_audio_folders_do_not(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from pet import modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=True)
    assert dialog.self_talk_image_dir_picker.preview_button is not None
    assert dialog.egg_image_dir_picker.preview_button is not None
    assert dialog.click_sound_picker.folder_picker.preview_button is None
    dialog.reject()
    app.processEvents()
