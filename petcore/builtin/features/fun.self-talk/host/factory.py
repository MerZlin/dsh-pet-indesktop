from __future__ import annotations

import datetime as _dt
import logging
import random
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from petcore import paths
from petcore.config import DEFAULTS
from petcore.contracts.contributions import Contribution
from petcore.contracts.feature import FeatureDefinition
from petcore.contracts.speech import SpeechRequest
from petcore.media import talk_images
from petcore.ui.settings.base import SettingsPage, make_button, make_checkbox
from petcore.ui.settings.folds import CollapsibleSection
from petcore.ui.settings.text_lines import format_lines, parse_lines

try:  # 正常包加载（tests/安装后的 mod loader）
    from . import daily, daily_pool
except ImportError:  # 单文件加载器（内置特性按文件加载）没有 package 上下文
    import importlib.util
    import sys

    def _load_sibling(module_name: str):
        spec = importlib.util.spec_from_file_location(
            f"{__name__}.{module_name}", Path(__file__).with_name(f"{module_name}.py")
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    daily = _load_sibling("daily")
    daily_pool = _load_sibling("daily_pool")

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

#: 「试听连说」最多连说几句：试听不该把整天的台词播一遍。
PREVIEW_SEQUENCE_LIMIT = 5


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
            # 每天第一次先用每日句（确定性、每宠不同），再走彩蛋/普通随机。
            if self._speak_daily(config, presentation, slot):
                return
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

    def _daily_path(self) -> Path | None:
        storage = getattr(self.context.ports, "storage", None)
        data_dir = getattr(storage, "data_dir", None)
        if not callable(data_dir):
            return None
        try:
            return Path(data_dir("fun.self-talk")) / "daily.json"
        except Exception:  # noqa: BLE001 - 存储不可用就跳过每日句
            return None

    def _daily_pool(self, config) -> tuple[str, ...]:
        """自定义台词优先；留空用随包内置池。"""
        texts = tuple(
            str(item).strip()
            for item in (config.get("self_talk_daily_texts") or [])
            if str(item).strip()
        )
        return texts or daily_pool.load_pool()

    @staticmethod
    def _daily_count(config) -> int:
        return daily_pool.clamp_count(config.get("self_talk_daily_count"))

    def _speak_daily(
        self,
        config,
        presentation,
        slot: int | None,
        *,
        day: _dt.date | None = None,
    ) -> bool:
        """说当天的这一句；返回 True 表示这次自语由每日台词占据。

        当天的一组台词由「日期 + slot」确定，一次自语说一句，游标写进
        ``daily.json``；一组说完（或自定义台词为空）就回落到普通自言自语。
        只有气泡真的显示出来才推进游标——文案没冒出来就不算说过。
        """
        if not bool(config.get("self_talk_daily_enabled", True)):
            return False
        path = self._daily_path()
        if path is None:
            return False
        current = day or _dt.date.today()
        slot_value = int(slot or 0)
        lines = daily_pool.daily_set(
            self._daily_pool(config),
            current,
            count=self._daily_count(config),
            salt=slot_value,
        )
        if not lines:
            return False
        state = daily.load_state(path)
        index = daily.daily_index(state, slot_value, current)
        if index >= len(lines):
            return False
        line = lines[index]
        sequence = (
            daily.split_sequence(line)
            if bool(config.get("self_talk_daily_sequence"))
            else (line,)
        )
        if not sequence:
            return False
        duration_ms = int(float(config.get("self_talk_duration") or 5) * 1000)
        if len(sequence) > 1:
            runner = getattr(presentation, "say_sequence", None)
            if callable(runner):
                shown = (
                    runner(
                        [
                            (part, "", max(1800, duration_ms))
                            for part in sequence
                        ],
                        on_show=lambda text: self._speak_text(config, slot, text),
                    )
                    is not False
                )
            else:
                shown = (
                    presentation.say(
                        " ".join(sequence),
                        duration_ms=duration_ms * len(sequence),
                    )
                    is not False
                )
        else:
            shown = (
                presentation.say(sequence[0], duration_ms=duration_ms) is not False
            )
        if shown:
            daily.mark_said(state, slot_value, current, index + 1)
            try:
                daily.save_state(path, state)
            except OSError:
                logger.debug("self-talk daily state save failed", exc_info=True)
            if len(sequence) == 1:
                self._speak_text(config, slot, sequence[0])
        logger.info(
            "self-talk daily slot=%s index=%d/%d parts=%d shown=%s",
            slot_value,
            index,
            len(lines),
            len(sequence),
            shown,
        )
        return shown

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
        card.add_row(
            "候选台词",
            self.texts,
            "每行一条；这里是初始预制句，可直接改/删/加，留空则不显示。",
            expand=True,
            search_keys=("恢复内置台词", "预制句"),
        )

        daily_card = self.section("每日一句")
        self.daily_enabled = make_checkbox(self)
        self.daily_enabled.setChecked(
            bool(self.config().get("self_talk_daily_enabled", True))
        )
        self.daily_enabled.toggled.connect(self._on_daily_enabled_toggled)
        daily_card.add_row(
            "启用每日一句",
            self.daily_enabled,
            "每天按日期给每只宠挑一组台词，一次自言自语说一句；说完再回到普通台词。",
            search_keys=("每日一句", "今日台词", "每日随机"),
        )
        self.daily_count = QSpinBox(self)
        self.daily_count.setRange(daily_pool.MIN_COUNT, daily_pool.MAX_COUNT)
        self.daily_count.setSuffix(" 句")
        self.daily_count.setValue(
            daily_pool.clamp_count(self.config().get("self_talk_daily_count"))
        )
        self.daily_count.valueChanged.connect(self._on_daily_count_changed)
        daily_card.add_row(
            "每天说几句",
            self.daily_count,
            f"{daily_pool.MIN_COUNT}–{daily_pool.MAX_COUNT} 句；"
            "同一天同一只宠拿到的这一组固定不变，换天才换。",
            search_keys=("句数", "每天几句", "每日句数"),
        )
        preview = QWidget(self)
        preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(6)
        self.daily_preview_title = QLabel("", preview)
        self.daily_preview_title.setObjectName("settingHint")
        self.daily_preview_title.setWordWrap(True)
        preview_layout.addWidget(self.daily_preview_title)
        self.daily_preview_list = QListWidget(preview)
        self.daily_preview_list.setObjectName("creditList")
        self.daily_preview_list.setAccessibleName("今日台词预览")
        self.daily_preview_list.setMinimumHeight(132)
        self.daily_preview_list.setMaximumHeight(240)
        self.daily_preview_list.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self.daily_preview_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.daily_preview_list.setUniformItemSizes(True)
        preview_layout.addWidget(self.daily_preview_list)
        daily_card.add_row(
            "今日台词",
            preview,
            "",
            expand=True,
            search_keys=("每日一句", "今日台词", "今天的一句", "预览"),
        )
        daily_buttons = QWidget(self)
        daily_buttons_layout = QHBoxLayout(daily_buttons)
        daily_buttons_layout.setContentsMargins(0, 0, 0, 0)
        daily_buttons_layout.setSpacing(8)
        self.tomorrow_button = make_button("明天再看", daily_buttons)
        self.tomorrow_button.clicked.connect(self._toggle_preview_day)
        self.preview_button = make_button("试听连说", daily_buttons)
        self.preview_button.clicked.connect(self._preview_daily_sequence)
        self.restore_daily_button = make_button("恢复内置台词", daily_buttons)
        self.restore_daily_button.clicked.connect(self._restore_default_texts)
        daily_buttons_layout.addWidget(self.tomorrow_button)
        daily_buttons_layout.addWidget(self.preview_button)
        daily_buttons_layout.addWidget(self.restore_daily_button)
        daily_buttons_layout.addStretch(1)
        daily_card.add_row(
            "快捷操作",
            daily_buttons,
            "「明天再看」切到明天的这一组（只看不改）；「试听连说」连说预览的前几句；"
            "「恢复内置台词」同时恢复候选台词与每日池。",
            expand=True,
            search_keys=(
                "明天再看",
                "试听连说",
                "恢复默认",
                "恢复内置台词",
                "试听",
                "连说",
            ),
        )
        self.daily_sequence = make_checkbox(self)
        self.daily_sequence.setChecked(
            bool(self.config().get("self_talk_daily_sequence"))
        )
        self.daily_sequence.toggled.connect(self._on_changed)
        daily_card.add_row(
            "每日一句连说",
            self.daily_sequence,
            "把一句台词按「|」、换行或句号拆成多条气泡，复用多气泡连说通道。",
            search_keys=("连说", "多气泡"),
        )

        # 自定义台词默认折叠：绝大多数人用内置池，展开才占版面。
        self.custom_section = CollapsibleSection(
            "自定义台词（留空使用内置池）", expanded=False, parent=self
        )
        self.daily_texts = QPlainTextEdit(self.custom_section)
        self.daily_texts.setPlaceholderText("每行一条自定义台词；留空 = 随包内置池")
        self.daily_texts.setMinimumHeight(110)
        self.daily_texts.setPlainText(
            format_lines(self.config().get("self_talk_daily_texts") or [])
        )
        self._daily_timer = QTimer(self)
        self._daily_timer.setSingleShot(True)
        self._daily_timer.setInterval(250)
        self._daily_timer.timeout.connect(self._on_changed)
        self.daily_texts.textChanged.connect(lambda: self._daily_timer.start())
        self.daily_texts.textChanged.connect(self._refresh_daily_preview)
        self.custom_section.card().add_row(
            "自定义台词",
            self.daily_texts,
            "每行一条（也可用「|」分隔）；留空时使用随包的内置池，"
            "填了就用你自己的句子替换整个池子。",
            expand=True,
            search_keys=("自定义台词", "覆盖内置池", "每日台词池"),
        )
        self._append_section_widget(self.custom_section)
        self._preview_offset = 0
        self._refresh_daily_preview()

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
            "daily_enabled": bool(config.get("self_talk_daily_enabled", True)),
            "daily_texts": list(config.get("self_talk_daily_texts") or []),
            "daily_count": daily_pool.clamp_count(
                config.get("self_talk_daily_count")
            ),
            "daily_sequence": bool(config.get("self_talk_daily_sequence")),
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
            "daily_enabled": self.daily_enabled.isChecked(),
            "daily_texts": parse_lines(self.daily_texts.toPlainText()),
            "daily_count": int(self.daily_count.value()),
            "daily_sequence": self.daily_sequence.isChecked(),
            "voice": self.voice_enabled.isChecked(),
            "champion": self.champion_enabled.isChecked(),
            "champion_chance": int(self.champion_chance.value()),
        }

    def _on_champion_toggled(self, checked: bool) -> None:
        self.champion_chance.setEnabled(bool(checked))
        self._on_changed()

    def _on_daily_enabled_toggled(self, _checked: bool) -> None:
        self._refresh_daily_preview()
        self._on_changed()

    def _on_daily_count_changed(self, _value: int) -> None:
        self._refresh_daily_preview()
        self._on_changed()

    # ------------------------------------------------------------ 每日一句预览

    def _slot(self) -> int:
        """预览用哪只宠的 slot：设置窗的目标宠，否则当作主宠 0。"""
        try:
            return int(getattr(self.host, "window", None).slot or 0)
        except (AttributeError, TypeError, ValueError):
            return 0

    def _daily_state_path(self) -> Path | None:
        try:
            root = Path(self.config().root)
        except Exception:  # noqa: BLE001 - 预览没有数据根时按「还没说过」显示
            return None
        return paths.feature_data_dir(root, CHAMPION_OWNER) / "daily.json"

    def _daily_pool_lines(self) -> tuple[str, ...]:
        texts = parse_lines(self.daily_texts.toPlainText())
        return tuple(texts) if texts else daily_pool.load_pool()

    def _preview_date(self) -> _dt.date:
        return _dt.date.today() + _dt.timedelta(days=int(self._preview_offset))

    def _preview_lines(self) -> tuple[str, ...]:
        return daily_pool.daily_set(
            self._daily_pool_lines(),
            self._preview_date(),
            count=int(self.daily_count.value()),
            salt=self._slot(),
        )

    def _spoken_today(self) -> int:
        path = self._daily_state_path()
        if path is None:
            return 0
        state = daily.load_state(path)
        return daily.daily_index(state, self._slot(), _dt.date.today())

    def showEvent(self, event):  # noqa: N802 - Qt 事件名
        super().showEvent(event)
        self._refresh_daily_preview()

    def _refresh_daily_preview(self, *_args) -> None:
        """纯渲染：只读配置与状态文件，不改配置、不碰运行时的排期/语音。"""
        day = self._preview_date()
        lines = self._preview_lines()
        offset = int(getattr(self, "_preview_offset", 0) or 0)
        if not lines:
            self.daily_preview_title.setText(
                "没有可用的台词：内置池缺失，自定义台词也是空的。"
            )
        else:
            spoken = self._spoken_today() if offset == 0 else 0
            head = f"{'明天' if offset else '今天'} {day.month} 月 {day.day} 日 · 共 {len(lines)} 句"
            if offset:
                head += " · 明天的预览（明天再看这一组）"
            elif spoken >= len(lines):
                head += " · 都说过了，接下来是普通自言自语"
            elif spoken > 0:
                head += f" · 下一句第 {spoken + 1} 句"
            self.daily_preview_title.setText(head)
        self.daily_preview_list.clear()
        for index, line in enumerate(lines):
            self.daily_preview_list.addItem(f"{index + 1}. {line}")

    def _toggle_preview_day(self) -> None:
        self._preview_offset = 0 if self._preview_offset else 1
        self.tomorrow_button.setText(
            "回到今天" if self._preview_offset else "明天再看"
        )
        self._refresh_daily_preview()

    def _restore_default_texts(self) -> None:
        self.texts.setPlainText(format_lines(DEFAULTS["self_talk_texts"]))
        self.daily_texts.setPlainText(format_lines(DEFAULTS["self_talk_daily_texts"]))
        self.daily_count.setValue(
            daily_pool.clamp_count(DEFAULTS["self_talk_daily_count"])
        )
        self._refresh_daily_preview()
        self._on_changed()

    def _presentation_port(self):
        """设置页借用宿主的展示端口做试听（``CoreHost.presentation_port()``）。"""
        getter = getattr(self.host, "presentation_port", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:  # noqa: BLE001 - 试听失败不该影响设置页
            logger.debug("self-talk preview presentation unavailable", exc_info=True)
            return None

    def _preview_daily_sequence(self) -> None:
        """试听：把预览里的前几句走真正的多气泡通道，只显示、不写配置。"""
        presentation = self._presentation_port()
        runner = getattr(presentation, "say_sequence", None)
        if not callable(runner):
            return
        lines = self._preview_lines()
        if not lines:
            return
        parts: list[str] = []
        for line in lines:
            if len(parts) >= PREVIEW_SEQUENCE_LIMIT:
                break
            parts.extend(
                daily.split_sequence(line)
                if self.daily_sequence.isChecked()
                else (line,)
            )
        parts = parts[:PREVIEW_SEQUENCE_LIMIT]
        if not parts:
            return
        duration_ms = max(
            1800, int(float(self.config().get("self_talk_duration") or 5) * 1000)
        )
        try:
            runner([(part, "", duration_ms) for part in parts], force=True)
        except Exception:  # noqa: BLE001 - 试听失败不许冒到设置页
            logger.debug("self-talk preview sequence failed", exc_info=True)

    def _on_changed(self, *_args) -> None:
        values = self._current()
        config = self.config()
        config.set("self_talk_enabled", values["enabled"])
        config.set("self_talk_min", values["min"])
        config.set("self_talk_max", max(values["min"], values["max"]))
        config.set("self_talk_duration", values["duration"])
        config.set("self_talk_texts", list(values["texts"]))
        config.set("self_talk_daily_enabled", values["daily_enabled"])
        config.set("self_talk_daily_texts", list(values["daily_texts"]))
        config.set("self_talk_daily_count", values["daily_count"])
        config.set("self_talk_daily_sequence", values["daily_sequence"])
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
        config.set("self_talk_daily_enabled", self._initial["daily_enabled"])
        config.set("self_talk_daily_texts", list(self._initial["daily_texts"]))
        config.set("self_talk_daily_count", self._initial["daily_count"])
        config.set("self_talk_daily_sequence", self._initial["daily_sequence"])
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
            (self.daily_enabled, "daily_enabled"),
            (self.daily_sequence, "daily_sequence"),
        ):
            check.blockSignals(True)
            check.setChecked(self._initial[key])
            check.blockSignals(False)
        self.champion_chance.blockSignals(True)
        self.champion_chance.setValue(self._initial["champion_chance"])
        self.champion_chance.setEnabled(self._initial["champion"])
        self.champion_chance.blockSignals(False)
        self.daily_count.blockSignals(True)
        self.daily_count.setValue(self._initial["daily_count"])
        self.daily_count.blockSignals(False)
        self.texts.blockSignals(True)
        self.texts.setPlainText(format_lines(self._initial["texts"]))
        self.texts.blockSignals(False)
        self.daily_texts.blockSignals(True)
        self.daily_texts.setPlainText(format_lines(self._initial["daily_texts"]))
        self.daily_texts.blockSignals(False)
        self._refresh_daily_preview()
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
            "每日一句",
            "今日台词",
            "每日随机",
            "每日句数",
            "句数",
            "内置池",
            "自定义台词",
            "明天再看",
            "连说",
            "恢复默认",
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
