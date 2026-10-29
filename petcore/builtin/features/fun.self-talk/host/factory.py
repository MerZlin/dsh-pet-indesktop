from __future__ import annotations

import logging
import random

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QPlainTextEdit, QSpinBox

from petcore.contracts.contributions import Contribution
from petcore.contracts.feature import FeatureDefinition
from petcore.contracts.speech import SpeechRequest
from petcore.media import talk_images
from petcore.ui.settings.base import SettingsPage, make_checkbox
from petcore.ui.settings.text_lines import format_lines, parse_lines

logger = logging.getLogger("petcore.features.self-talk")

#: 出图概率默认值（与 ``petcore.config.DEFAULTS["self_talk_image_chance"]`` 一致）。
DEFAULT_IMAGE_CHANCE = talk_images.image_chance({})

#: 彩蛋台词：主文案 / 中文译文 / 停留毫秒。逐条冒泡、逐条朗读。
CHAMPION_LINES: tuple[tuple[str, str, int], ...] = (
    ("Maybe not today,", "也许不是今天，", 2200),
    ("maybe not tomorrow,", "也许不是明天，", 2200),
    ("maybe not next month,", "也许不是下个月，", 2200),
    ("but one day,", "但总有一天，", 2200),
    ("you will be a champion.", "你会成为冠军。", 2600),
)
CHAMPION_OWNER = "fun.self-talk"


def _log_speech_failure(tag: str):
    """语音失败不能静默：只记稳定分类，不记台词/URL。"""

    def failed(error) -> None:
        logger.warning(
            "%s speech failed: reason=%s",
            tag,
            getattr(error, "reason", type(error).__name__),
        )

    return failed


class SelfTalkRuntime:
    def __init__(self, context):
        self.context = context
        self._handles: dict[int, str] = {}
        self._rng = random.Random()
        self._stopped = False
        self.commands = {}

    def start(self) -> None:
        self._stopped = False
        self._schedule_all()

    def on_pet_added(self, window) -> None:
        self._schedule(window)

    def on_pet_removed(self, window) -> None:
        slot = int(getattr(window, "slot", 0))
        handle = self._handles.pop(slot, None)
        if handle is not None:
            self.context.ports.scheduler.cancel(handle)
        speech = getattr(self.context.ports, "speech", None)
        if speech is not None:
            try:
                speech.cancel_slot(slot)
            except Exception:  # noqa: BLE001 - 端口异常不影响移除
                logger.debug("self-talk speech cancel_slot failed", exc_info=True)

    def _pets(self):
        registry = getattr(self.context.ports, "pets", None)
        getter = getattr(registry, "pets", None)
        if callable(getter):
            return list(getter())
        return []

    def _schedule_all(self) -> None:
        pets = self._pets()
        if not pets:
            self._schedule(None)
            return
        for pet in pets:
            slot = int(getattr(pet, "slot", 0))
            if slot not in self._handles:
                self._schedule(pet)

    def _schedule(self, pet) -> None:
        config = getattr(pet, "config", None) or getattr(self.context.host, "config", None)
        if config is None or not bool(config.get("self_talk_enabled")):
            return
        low = float(config.get("self_talk_min") or 20)
        high = max(low, float(config.get("self_talk_max") or 60))
        delay_ms = int(self._rng.uniform(low, high) * 1000)
        slot = int(getattr(pet, "slot", 0))
        self._handles[slot] = self.context.ports.scheduler.call_later(
            delay_ms, lambda slot=slot: self._speak(slot)
        )

    def _speak(self, slot: int | None = None) -> None:
        pet = None
        if slot is not None:
            pet = next(
                (item for item in self._pets() if int(getattr(item, "slot", -1)) == slot),
                None,
            )
            self._handles.pop(slot, None)
        config = getattr(pet, "config", None) or getattr(self.context.host, "config", None)
        if config is None or not bool(config.get("self_talk_enabled")):
            return
        try:
            presentation = self.context.ports.presentation
            if presentation is None:
                return
            binder = getattr(presentation, "for_window", None)
            if callable(binder) and pet is not None:
                presentation = binder(getattr(pet, "window", None))
            # 先掷彩蛋：命中时整段 champion 台词走多气泡 + 逐句朗读，不再选普通台词。
            if self._champion_roll(config):
                self._play_champion(config, presentation, slot)
                return
            texts = [
                str(item)
                for item in (config.get("self_talk_texts") or [])
                if str(item).strip()
            ]
            if not texts:
                return
            directory, images = talk_images.configured_images(config)
            chance = talk_images.image_chance(config, DEFAULT_IMAGE_CHANCE)
            choice = talk_images.pick_choice(self._rng, texts, images, chance)
            if choice is None:
                return
            kind, value = choice
            duration_ms = int(float(config.get("self_talk_duration") or 5) * 1000)
            shown = False
            text = ""
            if kind == "image":
                caption = self._rng.choice(texts)
                text = caption
                shown = self._show_image(
                    presentation,
                    value,
                    caption,
                    duration_ms,
                    talk_images.image_scale(config),
                )
            if not shown:
                text = value if kind == "text" else self._rng.choice(texts)
                shown = presentation.say(text, duration_ms=duration_ms) is not False
            if shown:
                # 气泡没显示出来（例如歌词持有展示会话）就不要朗读：
                # 听得见却看不见的那句话会让人以为桌宠出错了。
                self._speak_text(config, slot, text)
            logger.info(
                "self-talk kind=%s dir=%s images=%d shown=%s",
                kind,
                directory,
                len(images),
                shown,
            )
        except Exception:
            logger.exception("self-talk failed")
        finally:
            self._schedule(pet)

    def _champion_roll(self, config) -> bool:
        if not bool(config.get("self_talk_champion_enabled")):
            return False
        try:
            chance = int(config.get("self_talk_champion_chance") or 0)
        except (TypeError, ValueError):
            chance = 0
        chance = max(0, min(100, chance))
        return chance > 0 and self._rng.randint(1, 100) <= chance

    def _play_champion(self, config, presentation, slot: int | None) -> None:
        """彩蛋：5 条气泡依次出现（英文主文案 + 中文副标题），并逐句朗读。"""
        lines = [(text, subtitle, duration) for text, subtitle, duration in CHAMPION_LINES]
        runner = getattr(presentation, "say_sequence", None)
        if callable(runner):
            # 朗读挂在「这一条真的显示出来」的钩子上：隐藏态不会整段朗读，
            # 也不会在开头就把 5 个合成任务全塞进语音队列。
            runner(
                lines,
                on_show=lambda text: self._speak_text(config, slot, text),
            )
            return
        # 旧端口/假件没有队列：合成一条，保证至少能看到完整台词。
        text = " ".join(item[0] for item in lines)
        shown = presentation.say(
            text,
            duration_ms=sum(item[2] for item in lines),
            subtitle=" ".join(item[1] for item in lines),
        )
        if shown is not False or self._presentation_visible(presentation):
            self._speak_text(config, slot, text)

    @staticmethod
    def _presentation_visible(presentation) -> bool:
        getter = getattr(presentation, "visible", None)
        if not callable(getter):
            return True
        try:
            return bool(getter())
        except Exception:  # noqa: BLE001 - 探测失败按可见处理
            return True

    def _speak_text(self, config, slot: int | None, text: str) -> None:
        text = str(text or "").strip()
        if not text or not bool(config.get("self_talk_voice_enabled")):
            return
        speech = getattr(self.context.ports, "speech", None)
        if speech is None or self._stopped:
            return

        def valid() -> bool:
            return (
                not self._stopped
                and bool(config.get("self_talk_enabled"))
                and bool(config.get("self_talk_voice_enabled"))
            )

        try:
            speech.speak(
                SpeechRequest(text=text),
                owner=CHAMPION_OWNER,
                slot=slot,
                guard=valid,
                on_error=_log_speech_failure("self-talk"),
            )
        except Exception:  # noqa: BLE001 - 语音失败不能影响气泡与下次排期
            logger.debug("self-talk speech failed", exc_info=True)

    def _show_image(
        self,
        presentation,
        path,
        caption: str,
        duration_ms: int,
        scale: int,
    ) -> bool:
        show_image = getattr(presentation, "show_image", None)
        if not callable(show_image):
            return False
        try:
            return bool(
                show_image(
                    path,
                    caption,
                    duration_ms=duration_ms,
                    scale=scale / 100.0,
                )
            )
        except Exception:
            logger.exception("self-talk image failed: %s", path)
            return False

    def stop(self) -> None:
        self._stopped = True
        for handle in self._handles.values():
            self.context.ports.scheduler.cancel(handle)
        self._handles.clear()
        speech = getattr(self.context.ports, "speech", None)
        if speech is not None:
            try:
                speech.cancel_owner(CHAMPION_OWNER)
            except Exception:  # noqa: BLE001 - 端口异常不影响停止
                logger.debug("self-talk speech cancel failed", exc_info=True)

    def close(self) -> None:
        self.stop()


class SelfTalkPage(SettingsPage):
    title = "自言自语"

    def __init__(self, host, parent=None):
        super().__init__(host, parent)
        self._initial = self._snapshot()
        card = self.section("节奏")
        self.enabled = make_checkbox(self)
        self.enabled.setChecked(bool(self.config().get("self_talk_enabled")))
        self.enabled.toggled.connect(self._on_changed)
        card.add_row("启用自言自语气泡", self.enabled, "桌宠偶尔显示一条随机短句。")
        self.min_spin = QSpinBox(self)
        self.min_spin.setRange(1, 3600)
        self.min_spin.setSuffix(" 秒")
        self.min_spin.setValue(int(float(self.config().get("self_talk_min") or 20)))
        self.min_spin.valueChanged.connect(self._on_changed)
        card.add_row("最短间隔", self.min_spin, "上一条气泡消失后的最短空闲时间。")
        self.max_spin = QSpinBox(self)
        self.max_spin.setRange(1, 3600)
        self.max_spin.setSuffix(" 秒")
        self.max_spin.setValue(int(float(self.config().get("self_talk_max") or 60)))
        self.max_spin.valueChanged.connect(self._on_changed)
        card.add_row("最长间隔", self.max_spin, "上一条气泡消失后的最长空闲时间。")
        self.duration_spin = QSpinBox(self)
        self.duration_spin.setRange(1, 120)
        self.duration_spin.setSuffix(" 秒")
        self.duration_spin.setValue(int(float(self.config().get("self_talk_duration") or 5)))
        self.duration_spin.valueChanged.connect(self._on_changed)
        card.add_row("显示时间", self.duration_spin, "每条气泡保持显示的时间。")
        self.texts = QPlainTextEdit(self)
        self.texts.setPlaceholderText("每行一条台词")
        self.texts.setMinimumHeight(120)
        self.texts.setPlainText(format_lines(self.config().get("self_talk_texts") or []))
        self._text_timer = QTimer(self)
        self._text_timer.setSingleShot(True)
        self._text_timer.setInterval(250)
        self._text_timer.timeout.connect(self._on_changed)
        self.texts.textChanged.connect(lambda: self._text_timer.start())
        card.add_row("候选台词", self.texts, "每行一条；留空则不显示。", expand=True)

        extra = self.section("语音与彩蛋")
        self.voice_enabled = make_checkbox(self)
        self.voice_enabled.setChecked(bool(self.config().get("self_talk_voice_enabled")))
        self.voice_enabled.toggled.connect(self._on_changed)
        extra.add_row(
            "朗读自言自语",
            self.voice_enabled,
            "用「语音」页配置的后端与音色朗读；关闭后只冒气泡。",
        )
        self.champion_enabled = make_checkbox(self)
        self.champion_enabled.setChecked(
            bool(self.config().get("self_talk_champion_enabled"))
        )
        self.champion_enabled.toggled.connect(self._on_champion_toggled)
        extra.add_row(
            "champion 彩蛋",
            self.champion_enabled,
            "偶尔连续说 5 句英文台词（带中文译文）："
            "Maybe not today … you will be a champion.",
        )
        self.champion_chance = QSpinBox(self)
        self.champion_chance.setRange(0, 100)
        self.champion_chance.setSuffix(" %")
        self.champion_chance.setValue(
            int(self.config().get("self_talk_champion_chance") or 0)
        )
        self.champion_chance.setEnabled(self.champion_enabled.isChecked())
        self.champion_chance.valueChanged.connect(self._on_changed)
        extra.add_row(
            "彩蛋概率",
            self.champion_chance,
            "每次自言自语掷一次；0% 表示永不触发。",
        )

        self.add_note("配图目录、图片大小与出图概率在「互动与物理 → 彩蛋与配图」统一调整。")

    def config(self):
        return self.host.config

    def _snapshot(self) -> dict:
        config = self.config()
        return {
            "enabled": bool(config.get("self_talk_enabled")),
            "min": int(float(config.get("self_talk_min") or 20)),
            "max": int(float(config.get("self_talk_max") or 60)),
            "duration": int(float(config.get("self_talk_duration") or 5)),
            "texts": list(config.get("self_talk_texts") or []),
            "voice": bool(config.get("self_talk_voice_enabled")),
            "champion": bool(config.get("self_talk_champion_enabled")),
            "champion_chance": int(config.get("self_talk_champion_chance") or 0),
        }

    def _current(self) -> dict:
        return {
            "enabled": self.enabled.isChecked(),
            "min": int(self.min_spin.value()),
            "max": int(self.max_spin.value()),
            "duration": int(self.duration_spin.value()),
            "texts": parse_lines(self.texts.toPlainText()),
            "voice": self.voice_enabled.isChecked(),
            "champion": self.champion_enabled.isChecked(),
            "champion_chance": int(self.champion_chance.value()),
        }

    def _on_champion_toggled(self, checked: bool) -> None:
        self.champion_chance.setEnabled(bool(checked))
        self._on_changed()

    def _on_changed(self, *_args) -> None:
        values = self._current()
        config = self.config()
        config.set("self_talk_enabled", values["enabled"])
        config.set("self_talk_min", values["min"])
        config.set("self_talk_max", max(values["min"], values["max"]))
        config.set("self_talk_duration", values["duration"])
        config.set("self_talk_texts", list(values["texts"]))
        config.set("self_talk_voice_enabled", values["voice"])
        config.set("self_talk_champion_enabled", values["champion"])
        config.set("self_talk_champion_chance", values["champion_chance"])
        runtime = getattr(self.host, "features", None)
        if runtime is not None:
            feature = runtime.features.get("fun.self-talk")
            if feature is not None and feature.runtime is not None:
                feature.runtime.stop()
                feature.runtime.start()
        self.changed.emit()

    def dirty(self) -> bool:
        return self._current() != self._initial

    def confirm_save(self) -> None:
        self._on_changed()
        self._initial = self._snapshot()

    def discard_changes(self) -> None:
        config = self.config()
        config.set("self_talk_enabled", self._initial["enabled"])
        config.set("self_talk_min", self._initial["min"])
        config.set("self_talk_max", self._initial["max"])
        config.set("self_talk_duration", self._initial["duration"])
        config.set("self_talk_texts", list(self._initial["texts"]))
        config.set("self_talk_voice_enabled", self._initial["voice"])
        config.set("self_talk_champion_enabled", self._initial["champion"])
        config.set("self_talk_champion_chance", self._initial["champion_chance"])
        self.enabled.blockSignals(True)
        self.enabled.setChecked(self._initial["enabled"])
        self.enabled.blockSignals(False)
        for spin, key in (
            (self.min_spin, "min"),
            (self.max_spin, "max"),
            (self.duration_spin, "duration"),
        ):
            spin.blockSignals(True)
            spin.setValue(self._initial[key])
            spin.blockSignals(False)
        for check, key in (
            (self.voice_enabled, "voice"),
            (self.champion_enabled, "champion"),
        ):
            check.blockSignals(True)
            check.setChecked(self._initial[key])
            check.blockSignals(False)
        self.champion_chance.blockSignals(True)
        self.champion_chance.setValue(self._initial["champion_chance"])
        self.champion_chance.setEnabled(self._initial["champion"])
        self.champion_chance.blockSignals(False)
        self.texts.blockSignals(True)
        self.texts.setPlainText(format_lines(self._initial["texts"]))
        self.texts.blockSignals(False)
        self.changed.emit()

    def keywords(self) -> list[str]:
        return [
            "自言自语",
            "气泡",
            "台词",
            "间隔",
            "配图",
            "图片",
            "语音",
            "朗读",
            "声音",
            "彩蛋",
            "champion",
            "冠军",
            "self talk",
            "bubble",
        ]


def create_host(_context):
    return FeatureDefinition(
        owner="fun.self-talk",
        contributions=(
            Contribution(
                kind="settings_page",
                id="self_talk",
                data={"host_page": "interaction", "title": "自言自语", "order": 620, "icon": "chat"},
            ),
        ),
        runtime_factory=SelfTalkRuntime,
        settings_factory=SelfTalkPage,
    )
