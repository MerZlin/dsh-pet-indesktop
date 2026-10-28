from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from petcore import paths
from petcore.config import Config
from petcore.contracts.host import HostPorts
from petcore.mods import feature as feature_mod

SELF_TALK_PACK = paths.builtin_features_dir() / "fun.self-talk"
CLICK_TALK_PACK = paths.builtin_features_dir() / "fun.click-talk"


class FakeScheduler:
    def __init__(self) -> None:
        self.calls: list[tuple[int, object]] = []
        self.cancelled: list[str] = []

    def call_later(self, delay_ms: int, callback) -> str:
        self.calls.append((delay_ms, callback))
        return f"handle-{len(self.calls)}"

    def call_every(self, interval_ms: int, callback) -> str:
        self.calls.append((interval_ms, callback))
        return f"every-{len(self.calls)}"

    def cancel(self, handle: str) -> None:
        self.cancelled.append(handle)

    def cancel_owner(self, owner: str) -> None:
        del owner


class FakeInteraction:
    def __init__(self) -> None:
        self.callbacks: list = []

    def on_click(self, owner, callback):
        del owner
        self.callbacks.append(callback)

        def _cancel() -> None:
            if callback in self.callbacks:
                self.callbacks.remove(callback)

        return _cancel

    def on_drop(self, owner, callback):
        del owner, callback
        return lambda: None

    def on_tick(self, owner, callback, interval_ms: int = 16):
        del owner, callback, interval_ms
        return lambda: None

    def cancel_owner(self, owner: str) -> None:
        del owner


class SpyPresentation:
    def __init__(
        self,
        *,
        image_ok: bool = True,
        with_image: bool = True,
        say_ok: bool = True,
        visible: bool = True,
    ) -> None:
        self.said: list[tuple[str, int, bool]] = []
        self.images: list[tuple[Path, str, int, float]] = []
        self.sequences: list[list[tuple[str, str, int]]] = []
        self.image_ok = image_ok
        self.say_ok = say_ok
        self.visible_flag = visible
        if not with_image:
            self.show_image = None  # type: ignore[assignment]

    def say(self, text: str, *, duration_ms: int = 4000, force: bool = False, subtitle: str = "") -> bool:
        self.said.append((str(text), int(duration_ms), bool(force)))
        return bool(self.say_ok)

    def say_sequence(self, items, *, on_show=None) -> bool:
        if not self.visible_flag:
            return False
        self.sequences.append([tuple(item) for item in items])
        for item in items:
            if on_show is not None:
                on_show(item[0])
        return True

    def visible(self) -> bool:
        return bool(self.visible_flag)

    def show_image(
        self,
        path,
        caption: str = "",
        *,
        duration_ms: int = 4000,
        scale: float = 1.0,
        force: bool = False,
        owner: str = "",
    ) -> bool:
        self.images.append((Path(path), str(caption), int(duration_ms), float(scale)))
        return self.image_ok

    def play_animation(self, name: str) -> bool:
        del name
        return True

    def play_event(self, group: str, variant=None) -> bool:
        del group, variant
        return True

    def notify(self, title: str, message: str, *, force: bool = False) -> None:
        del title, message, force


class SpySpeech:
    def __init__(self) -> None:
        self.spoken: list[tuple[str, str, int | None]] = []
        self.cancelled_owners: list[str] = []
        self.cancelled_slots: list[int | None] = []

    def speak(self, request, *, owner="", slot=None, guard=None, on_done=None, on_error=None, on_finished=None):
        self.spoken.append((request.text, owner, slot))
        return "speech-1"

    def cancel_owner(self, owner: str) -> None:
        self.cancelled_owners.append(owner)

    def cancel_slot(self, slot) -> None:
        self.cancelled_slots.append(slot)


class FakeHost:
    def __init__(self, config) -> None:
        self.config = config


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _load_feature(pack: Path, owner: str, ports: HostPorts, config):
    manager = feature_mod.FeatureManager(ports, host=FakeHost(config))
    diagnostics = manager.load(
        pack=pack, owner=owner, entrypoint="host/factory.py:create_host"
    )
    assert diagnostics == []
    return manager.features[owner]


def _load_runtime(pack: Path, owner: str, ports: HostPorts, config):
    return _load_feature(pack, owner, ports, config).runtime


def _write_image(path: Path, width: int = 120, height: int = 80) -> Path:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("#30a0ff"))
    assert image.save(str(path))
    return path


def _config(tmp_path: Path, *, chance: int, image_dir: Path | str) -> Config:
    config = Config(tmp_path)
    config.set("self_talk_enabled", True)
    config.set("self_talk_texts", ["今天也要好好休息。"])
    config.set("self_talk_min", 20)
    config.set("self_talk_max", 20)
    config.set("self_talk_duration", 4)
    config.set("self_talk_image_dir", str(image_dir))
    config.set("self_talk_image_scale", 150)
    config.set("self_talk_image_chance", chance)
    # 彩蛋默认按概率触发；单测里关掉，避免随机命中把普通路径断言打穿。
    config.set("self_talk_champion_enabled", False)
    config.set("self_talk_voice_enabled", False)
    return config


def test_self_talk_uses_an_image_when_the_chance_hits(app, tmp_path: Path) -> None:
    folder = tmp_path / "pics"
    folder.mkdir()
    image = _write_image(folder / "a.png")
    config = _config(tmp_path, chance=100, image_dir=folder)
    presentation = SpyPresentation()
    runtime = _load_runtime(
        SELF_TALK_PACK,
        "fun.self-talk",
        HostPorts(presentation=presentation, scheduler=FakeScheduler()),
        config,
    )

    runtime._speak()

    assert presentation.images, "chance=100 且有图时必须走 show_image"
    path, caption, duration_ms, scale = presentation.images[-1]
    assert path == image
    assert caption == "今天也要好好休息。"
    assert duration_ms == 4000
    assert scale == pytest.approx(1.5)
    assert presentation.said == [], "出图成功时不应再叠一条文字气泡"


def test_self_talk_only_speaks_when_chance_is_zero(app, tmp_path: Path) -> None:
    folder = tmp_path / "pics"
    folder.mkdir()
    _write_image(folder / "a.png")
    config = _config(tmp_path, chance=0, image_dir=folder)
    presentation = SpyPresentation()
    runtime = _load_runtime(
        SELF_TALK_PACK,
        "fun.self-talk",
        HostPorts(presentation=presentation, scheduler=FakeScheduler()),
        config,
    )

    runtime._speak()

    assert presentation.images == []
    assert presentation.said == [("今天也要好好休息。", 4000, False)]


def test_empty_directory_falls_back_to_builtin_images(app, tmp_path: Path) -> None:
    folder = tmp_path / "empty"
    folder.mkdir()
    config = _config(tmp_path, chance=100, image_dir=folder)
    presentation = SpyPresentation()
    runtime = _load_runtime(
        SELF_TALK_PACK,
        "fun.self-talk",
        HostPorts(presentation=presentation, scheduler=FakeScheduler()),
        config,
    )

    runtime._speak()

    assert presentation.images, "配置目录无图时应回退内置 assets/big_blue_fat_fish"
    assert presentation.said == []


def test_self_talk_falls_back_to_text_when_no_images_exist(
    app, tmp_path: Path, monkeypatch
) -> None:
    from petcore.media import talk_images

    monkeypatch.setattr(talk_images, "readable_images", lambda directory: [])
    config = _config(tmp_path, chance=100, image_dir=tmp_path)
    presentation = SpyPresentation()
    runtime = _load_runtime(
        SELF_TALK_PACK,
        "fun.self-talk",
        HostPorts(presentation=presentation, scheduler=FakeScheduler()),
        config,
    )

    runtime._speak()

    assert presentation.images == []
    assert presentation.said == [("今天也要好好休息。", 4000, False)]


def test_self_talk_falls_back_when_the_port_has_no_show_image(
    app, tmp_path: Path
) -> None:
    folder = tmp_path / "pics"
    folder.mkdir()
    _write_image(folder / "a.png")
    config = _config(tmp_path, chance=100, image_dir=folder)
    presentation = SpyPresentation(with_image=False)
    runtime = _load_runtime(
        SELF_TALK_PACK,
        "fun.self-talk",
        HostPorts(presentation=presentation, scheduler=FakeScheduler()),
        config,
    )

    runtime._speak()

    assert presentation.said, "端口没有 show_image 时必须回退成 say"


def test_click_talk_forces_the_bubble(app, tmp_path: Path) -> None:
    config = Config(tmp_path)
    config.set("click_talk_enabled", True)
    config.set("click_talk_texts", ["我在呢。"])
    config.set("self_talk_image_chance", 0)
    presentation = SpyPresentation()
    interaction = FakeInteraction()
    runtime = _load_runtime(
        CLICK_TALK_PACK,
        "fun.click-talk",
        HostPorts(
            presentation=presentation,
            interaction=interaction,
            scheduler=FakeScheduler(),
        ),
        config,
    )

    assert interaction.callbacks, "点击台词必须挂在 interaction.on_click 上"
    interaction.callbacks[0](None)

    assert presentation.said == [("我在呢。", 3200, True)], "点击是主动命令，要绕过免打扰"
    runtime.stop()


class _PageHost:
    def __init__(self, root: Path) -> None:
        self.config = Config(root)
        self.features = None


def test_image_settings_live_on_the_interaction_page(app, tmp_path: Path) -> None:
    host = _PageHost(tmp_path)
    feature = _load_feature(
        SELF_TALK_PACK,
        "fun.self-talk",
        HostPorts(presentation=SpyPresentation(), scheduler=FakeScheduler()),
        host.config,
    )
    page = feature.definition.settings_factory(host)

    # 配图三件套已合并到「互动与物理 → 彩蛋与配图」，自言自语页不再重复。
    assert not hasattr(page, "image_dir_edit")
    assert not hasattr(page, "image_scale_spin")
    assert not hasattr(page, "image_chance_spin")

    from petcore.ui.settings.page_interaction import InteractionPage

    interaction = InteractionPage(host)
    assert interaction.self_talk_image_chance.value() == 30
    interaction.self_talk_image_scale.setValue(150)
    interaction.self_talk_image_scale.valueChanged.emit(150)
    assert host.config.get("self_talk_image_scale") == 150
    interaction.discard_changes()
    assert host.config.get("self_talk_image_scale") == 100


def test_chance_100_always_picks_an_image(app, tmp_path: Path) -> None:
    folder = tmp_path / "pics"
    folder.mkdir()
    _write_image(folder / "a.png")
    config = _config(tmp_path, chance=100, image_dir=folder)
    presentation = SpyPresentation()
    runtime = _load_runtime(
        SELF_TALK_PACK,
        "fun.self-talk",
        HostPorts(presentation=presentation, scheduler=FakeScheduler()),
        config,
    )
    scheduler = runtime.context.ports.scheduler

    runtime._speak()

    assert presentation.images, "chance=100 必须出图"
    assert presentation.said == []
    assert len(scheduler.calls) >= 2, "出图后必须继续排下一次自言自语"


def test_click_talk_can_show_images(app, tmp_path: Path) -> None:
    folder = tmp_path / "pics"
    folder.mkdir()
    _write_image(folder / "a.png")
    config = Config(tmp_path)
    config.set("click_talk_enabled", True)
    config.set("click_talk_texts", ["我在呢。"])
    config.set("self_talk_image_dir", str(folder))
    config.set("self_talk_image_chance", 100)
    config.set("self_talk_image_scale", 120)
    presentation = SpyPresentation()
    interaction = FakeInteraction()
    runtime = _load_runtime(
        CLICK_TALK_PACK,
        "fun.click-talk",
        HostPorts(
            presentation=presentation,
            interaction=interaction,
            scheduler=FakeScheduler(),
        ),
        config,
    )

    interaction.callbacks[0](None)

    assert presentation.images, "点击台词在 100% 出图时必须显示图片"
    assert presentation.said == []
    path, caption, duration_ms, scale = presentation.images[-1]
    assert caption == "我在呢。"
    assert duration_ms == 3200
    assert scale == pytest.approx(1.2)
    runtime.stop()


# ---------------------------------------------------------------- champion 彩蛋 / 语音

CHAMPION_EN = [
    "Maybe not today,",
    "maybe not tomorrow,",
    "maybe not next month,",
    "but one day,",
    "you will be a champion.",
]
CHAMPION_ZH = [
    "也许不是今天，",
    "也许不是明天，",
    "也许不是下个月，",
    "但总有一天，",
    "你会成为冠军。",
]


def _champion_config(tmp_path: Path, chance: int = 100) -> Config:
    config = _config(tmp_path, chance=0, image_dir=tmp_path)
    config.set("self_talk_champion_enabled", True)
    config.set("self_talk_champion_chance", chance)
    return config


def test_champion_egg_plays_five_bubbles_and_speaks_every_line(app, tmp_path: Path) -> None:
    config = _champion_config(tmp_path)
    config.set("self_talk_voice_enabled", True)
    presentation = SpyPresentation()
    speech = SpySpeech()
    runtime = _load_runtime(
        SELF_TALK_PACK,
        "fun.self-talk",
        HostPorts(presentation=presentation, scheduler=FakeScheduler(), speech=speech),
        config,
    )

    runtime._speak()

    assert len(presentation.sequences) == 1
    lines = presentation.sequences[0]
    assert [line[0] for line in lines] == CHAMPION_EN
    assert [line[1] for line in lines] == CHAMPION_ZH
    assert [text for text, _owner, _slot in speech.spoken] == CHAMPION_EN
    assert {owner for _text, owner, _slot in speech.spoken} == {"fun.self-talk"}
    assert presentation.said == [], "彩蛋命中时不应再叠普通台词"


def test_champion_chance_zero_never_triggers(app, tmp_path: Path) -> None:
    config = _champion_config(tmp_path, chance=0)
    presentation = SpyPresentation()
    runtime = _load_runtime(
        SELF_TALK_PACK,
        "fun.self-talk",
        HostPorts(presentation=presentation, scheduler=FakeScheduler()),
        config,
    )

    runtime._speak()

    assert presentation.sequences == []
    assert presentation.said == [("今天也要好好休息。", 4000, False)]


def test_self_talk_speaks_only_when_voice_is_enabled(app, tmp_path: Path) -> None:
    config = _config(tmp_path, chance=0, image_dir=tmp_path)
    config.set("self_talk_voice_enabled", True)
    presentation = SpyPresentation()
    speech = SpySpeech()
    runtime = _load_runtime(
        SELF_TALK_PACK,
        "fun.self-talk",
        HostPorts(presentation=presentation, scheduler=FakeScheduler(), speech=speech),
        config,
    )

    runtime._speak()
    assert speech.spoken == [("今天也要好好休息。", "fun.self-talk", None)]

    runtime.stop()
    assert "fun.self-talk" in speech.cancelled_owners


def test_self_talk_stays_silent_when_voice_is_off(app, tmp_path: Path) -> None:
    config = _config(tmp_path, chance=0, image_dir=tmp_path)
    presentation = SpyPresentation()
    speech = SpySpeech()
    runtime = _load_runtime(
        SELF_TALK_PACK,
        "fun.self-talk",
        HostPorts(presentation=presentation, scheduler=FakeScheduler(), speech=speech),
        config,
    )

    runtime._speak()

    assert presentation.said == [("今天也要好好休息。", 4000, False)]
    assert speech.spoken == []


def test_hidden_pet_does_not_speak_normal_self_talk(app, tmp_path: Path) -> None:
    config = _config(tmp_path, chance=0, image_dir=tmp_path)
    config.set("self_talk_voice_enabled", True)
    presentation = SpyPresentation(say_ok=False, visible=False)
    speech = SpySpeech()
    runtime = _load_runtime(
        SELF_TALK_PACK,
        "fun.self-talk",
        HostPorts(presentation=presentation, scheduler=FakeScheduler(), speech=speech),
        config,
    )

    runtime._speak()

    assert presentation.said, "调用仍会尝试冒泡"
    assert speech.spoken == [], "隐藏的桌宠不该出声"


def test_hidden_pet_does_not_speak_champion_egg(app, tmp_path: Path) -> None:
    config = _champion_config(tmp_path)
    config.set("self_talk_voice_enabled", True)
    presentation = SpyPresentation(visible=False)
    speech = SpySpeech()
    runtime = _load_runtime(
        SELF_TALK_PACK,
        "fun.self-talk",
        HostPorts(presentation=presentation, scheduler=FakeScheduler(), speech=speech),
        config,
    )

    runtime._speak()

    assert presentation.sequences == []
    assert speech.spoken == []


def test_click_talk_speaks_when_voice_is_enabled(app, tmp_path: Path) -> None:
    config = Config(tmp_path)
    config.set("click_talk_enabled", True)
    config.set("click_talk_texts", ["我在呢。"])
    config.set("click_talk_voice_enabled", True)
    config.set("self_talk_image_chance", 0)
    presentation = SpyPresentation()
    interaction = FakeInteraction()
    speech = SpySpeech()
    runtime = _load_runtime(
        CLICK_TALK_PACK,
        "fun.click-talk",
        HostPorts(
            presentation=presentation,
            interaction=interaction,
            scheduler=FakeScheduler(),
            speech=speech,
        ),
        config,
    )

    interaction.callbacks[0](None)

    assert speech.spoken == [("我在呢。", "fun.click-talk", 0)]
    runtime.stop()
    assert "fun.click-talk" in speech.cancelled_owners


def test_click_talk_voice_off_keeps_silence(app, tmp_path: Path) -> None:
    config = Config(tmp_path)
    config.set("click_talk_enabled", True)
    config.set("click_talk_texts", ["我在呢。"])
    config.set("click_talk_voice_enabled", False)
    config.set("self_talk_image_chance", 0)
    presentation = SpyPresentation()
    interaction = FakeInteraction()
    speech = SpySpeech()
    runtime = _load_runtime(
        CLICK_TALK_PACK,
        "fun.click-talk",
        HostPorts(
            presentation=presentation,
            interaction=interaction,
            scheduler=FakeScheduler(),
            speech=speech,
        ),
        config,
    )

    interaction.callbacks[0](None)

    assert presentation.said == [("我在呢。", 3200, True)]
    assert speech.spoken == []
    runtime.stop()
