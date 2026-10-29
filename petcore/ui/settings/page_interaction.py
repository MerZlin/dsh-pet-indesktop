from __future__ import annotations

import random

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QWidget,
)

from ... import paths
from ...media import talk_images
from ...media.click_sound import choose_click_sound, resolve_click_sound_candidates
from ...media.sound import SoundPlayer
from ...engine.head_pat import (
    HEAD_PAT_ALL_MODE,
    HEAD_PAT_CUSTOM_MODE,
    HEAD_PAT_DEFAULT_MODE,
    eligible_head_pat_specs,
    head_pat_specs,
)
from .base import (
    SettingsPage,
    build_embedded_page,
    embedded_page_specs,
    make_button,
    make_checkbox,
    value_field,
)

#: 彩蛋展示方式的配置键：`bubble` 跟随桌宠，`window` 独立浮窗。
EASTER_DISPLAY_KEY = "easter_egg_display"
EASTER_DISPLAY_DEFAULT = "bubble"


def _percent(scale: float) -> int:
    return int(round(float(scale) * 100))


class InteractionPage(SettingsPage):
    title = "互动与物理"

    _CHECK_KEYS = (
        "click_squash_enabled",
        "click_sound",
        "click_show_balance",
        "balance_tier_color_enabled",
        "drag_physics_enabled",
        "drop_gravity_enabled",
        "slingshot_enabled",
        "collision_enabled",
        "collision_sound",
        "golden_spin_on_click",
        "golden_spin_direct",
        "throw_egg_enabled",
        "file_feeding_enabled",
        "no_move",
        "lock_position",
        "shift_drag",
    )

    def __init__(self, host, parent=None):
        super().__init__(host, parent)
        self._sound: SoundPlayer | None = None
        self._initial = self._snapshot()
        embedded = {spec.owner: spec for spec in embedded_page_specs(host, "interaction")}

        with self.tab("click", "点击与音效"):
            click_card = self.section("点击反馈")
            self._checks = {}
            for key in self._CHECK_KEYS:
                box = make_checkbox(self)
                box.setChecked(bool(self.config().get(key)))
                box.toggled.connect(self._on_changed)
                self._checks[key] = box
            click_card.add_row(
                "点击 Q 弹",
                self._checks["click_squash_enabled"],
                "点击/碰撞/撞岛时先压扁再恢复；关闭后不再触发 Q 弹。",
            )
            self.squash_bounces = QSpinBox(self)
            self.squash_bounces.setRange(1, 5)
            self.squash_bounces.setSuffix(" 次")
            self.squash_bounces.setValue(
                int(self.config().get("click_squash_bounces") or 1)
            )
            self.squash_bounces.valueChanged.connect(self._on_changed)
            click_card.add_row(
                "弹跳次数",
                self.squash_bounces,
                "一次触发里连续压/鼓的次数；1 次就是原来的单次 Q 弹。",
            )
            self.squash_strength = QSpinBox(self)
            self.squash_strength.setRange(30, 200)
            self.squash_strength.setSingleStep(10)
            self.squash_strength.setSuffix("%")
            self.squash_strength.setValue(
                int(self.config().get("click_squash_strength") or 100)
            )
            self.squash_strength.valueChanged.connect(self._on_changed)
            click_card.add_row(
                "弹性强度",
                self.squash_strength,
                "压扁/鼓出的幅度；100% 为默认观感。",
            )
            self.squash_ms = QSpinBox(self)
            self.squash_ms.setRange(80, 1000)
            self.squash_ms.setSingleStep(20)
            self.squash_ms.setSuffix(" ms")
            self.squash_ms.setValue(
                int(float(self.config().get("click_squash_ms") or 220))
            )
            self.squash_ms.valueChanged.connect(self._on_changed)
            click_card.add_row(
                "持续时间",
                self.squash_ms,
                "整段 Q 弹的时长；越长越软，越短越脆。",
            )
            self._checks["click_squash_enabled"].toggled.connect(
                self._sync_squash_rows
            )
            self._sync_squash_rows()
            click_card.add_row(
                "点击音效",
                self._checks["click_sound"],
                "点击桌宠时播放轻量反馈音效。",
            )
            self.sound_pack_combo = QComboBox(self)
            for key, label in (
                ("builtin:default", "内置 · 默认"),
                ("builtin:duck", "内置 · 小黄鸭"),
                ("file", "自定义文件"),
                ("folder", "自定义文件夹（随机）"),
            ):
                self.sound_pack_combo.addItem(label, key)
            self._sound_pack = self._read_sound_pack()
            click_card.add_row(
                "音效音源",
                self.sound_pack_combo,
                "选择内置音效包、单个自定义音频文件，或从文件夹随机播放。",
            )

            sound_path_row = QWidget(self)
            sound_path_layout = QHBoxLayout(sound_path_row)
            sound_path_layout.setContentsMargins(0, 0, 0, 0)
            sound_path_layout.setSpacing(8)
            self.sound_path_edit = value_field("", sound_path_row)
            self.sound_path_edit.setMinimumWidth(220)
            self._sync_sound_pack_widgets()
            self.sound_pack_combo.currentIndexChanged.connect(self._on_sound_pack_changed)
            sound_path_layout.addWidget(self.sound_path_edit, 1)
            choose_file = make_button("选择文件…", sound_path_row)
            choose_file.clicked.connect(self._choose_sound_file)
            sound_path_layout.addWidget(choose_file)
            choose_folder = make_button("选择文件夹…", sound_path_row)
            choose_folder.clicked.connect(self._choose_sound_folder)
            sound_path_layout.addWidget(choose_folder)
            preview_sound = make_button("试听", sound_path_row)
            preview_sound.clicked.connect(self._preview_sound)
            sound_path_layout.addWidget(preview_sound)
            click_card.add_row(
                "音效文件",
                sound_path_row,
                "自定义音效支持 wav / mp3 / ogg / flac / m4a；文件夹会随机抽取一个。",
                expand=True,
            )
            click_card.add_row(
                "点击触发黄金回旋",
                self._checks["golden_spin_on_click"],
                "点击回应动画结束后接一段原地 360° 逆时针旋转。",
            )
            self._spin_direct_row = click_card.add_row(
                "点击时跳过点击动画",
                self._checks["golden_spin_direct"],
                "点击直接回旋，不播放 Q 弹与点击素材；连续点击会累计圈数并加速。",
                sub=True,
            )
            self._sync_spin_direct_row()
            self._checks["golden_spin_on_click"].toggled.connect(self._sync_spin_direct_row)

            balance_card = self.section("余额提示")
            balance_card.add_row(
                "点击查看余额",
                self._checks["click_show_balance"],
                "左键点击桌宠时异步查询余额并用气泡展示；按当前宠物保存，不阻塞点击动画。"
                "开启后余额气泡先冒，点击台词等它收起后再接上。",
            )
            balance_card.add_row(
                "峰谷提示颜色",
                self._checks["balance_tier_color_enabled"],
                "余额气泡下方显示 DeepSeek 高峰/空闲与下次切换时间；开启后高峰红、低谷绿。",
            )
            self.balance_tier_mode = QComboBox(self)
            for key, label in (
                ("default", "高峰 / 空闲"),
                ("liangwen", "梁文峰 / 梁文谷"),
                ("custom", "自定义"),
            ):
                self.balance_tier_mode.addItem(label, key)
            mode_index = self.balance_tier_mode.findData(
                str(self.config().get("balance_tier_labels_mode") or "default")
            )
            self.balance_tier_mode.setCurrentIndex(max(0, mode_index))
            self.balance_tier_mode.currentIndexChanged.connect(self._on_balance_tier_changed)
            balance_card.add_row(
                "峰谷文案",
                self.balance_tier_mode,
                "「梁文峰 / 梁文谷」是主分支的彩蛋叫法；自定义留空回退默认文案。",
            )
            self.balance_tier_peak = value_field("高峰", self)
            self.balance_tier_peak.setText(str(self.config().get("balance_tier_label_peak") or ""))
            self.balance_tier_peak.setMaximumWidth(160)
            self.balance_tier_peak.editingFinished.connect(self._on_changed)
            self._tier_peak_row = balance_card.add_row(
                "高峰自定义文本",
                self.balance_tier_peak,
                "仅「自定义」模式生效；留空回退「高峰」。",
                sub=True,
            )
            self.balance_tier_idle = value_field("空闲", self)
            self.balance_tier_idle.setText(str(self.config().get("balance_tier_label_idle") or ""))
            self.balance_tier_idle.setMaximumWidth(160)
            self.balance_tier_idle.editingFinished.connect(self._on_changed)
            self._tier_idle_row = balance_card.add_row(
                "空闲自定义文本",
                self.balance_tier_idle,
                "仅「自定义」模式生效；留空回退「空闲」。",
                sub=True,
            )
            self._sync_tier_custom_rows()

        self._embed_owner(embedded, "fun.click-talk", "click_talk", "点击台词")
        self._embed_owner(embedded, "fun.self-talk", "self_talk", "自言自语")

        with self.tab("drag", "拖拽与投掷"):
            drag_card = self.section("拖拽与弹弓")
            drag_card.add_row(
                "拖动物理",
                self._checks["drag_physics_enabled"],
                "开启后拖拽带弹簧跟随，松手按轨迹甩出；关闭时保持跟手拖动。",
            )
            drag_card.add_row(
                "静止放手下落",
                self._checks["drop_gravity_enabled"],
                "低速松手也受重力落地；关闭时低速松手直接停住。",
            )
            drag_card.add_row("弹弓", self._checks["slingshot_enabled"], "拖拽中按右键进入瞄准，松左键发射。")
            self.throw_strength = QComboBox(self)
            for key, label in (
                ("gentle", "轻"),
                ("standard", "标准"),
                ("strong", "强"),
                ("crazy", "疯狂"),
            ):
                self.throw_strength.addItem(label, key)
            index = self.throw_strength.findData(str(self.config().get("throw_strength") or "standard"))
            self.throw_strength.setCurrentIndex(max(0, index))
            self.throw_strength.currentIndexChanged.connect(self._on_changed)
            drag_card.add_row("甩出力度", self.throw_strength, "影响甩出与弹弓的速度上限。")
            drag_card.add_row(
                "锁定位置",
                self._checks["lock_position"],
                "锁定后无法拖动，但点击、右键和滚轮仍然有效。",
            )
            drag_card.add_row(
                "仅 Shift 拖动",
                self._checks["shift_drag"],
                "只有按住 Shift 再左键拖拽才能移动桌宠，避免误拖。",
            )
            drag_card.add_row(
                "不自动移动",
                self._checks["no_move"],
                "桌宠不再自主漫游，仍可拖拽与点击。",
            )

        with self.tab("collision", "碰撞与投喂"):
            collision_card = self.section("碰撞与投喂")
            collision_card.add_row(
                "桌宠碰撞",
                self._checks["collision_enabled"],
                "多只桌宠互相碰撞时弹开并触发 Q 弹。",
            )
            collision_card.add_row("碰撞音效", self._checks["collision_sound"], "碰撞时播放反馈音效。")
            collision_card.add_row(
                "探头击飞旋转",
                self._checks["throw_egg_enabled"],
                "边缘探头状态下被甩飞时，整帧旋转让头部跟随速度方向。",
            )
            collision_card.add_row(
                "拖文件投喂",
                self._checks["file_feeding_enabled"],
                "把文件拖到桌宠身上会播放吃动画并统计；文件不会被删除或移动。",
            )

        with self.tab("animation", "动画"):
            animation_card = self.section("动画")
            speed_row = QWidget(self)
            speed_layout = QHBoxLayout(speed_row)
            speed_layout.setContentsMargins(0, 0, 0, 0)
            speed_layout.setSpacing(12)
            self.playback_speed = QDoubleSpinBox(speed_row)
            self.playback_speed.setRange(0.5, 2.0)
            self.playback_speed.setSingleStep(0.1)
            self.playback_speed.setDecimals(1)
            self.playback_speed.setSuffix("x")
            self.playback_speed.setValue(float(self.config().get("playback_speed") or 1.0))
            self.playback_speed.valueChanged.connect(self._on_changed)
            speed_layout.addWidget(self.playback_speed)
            speed_layout.addStretch(1)
            animation_card.add_row("播放速率", speed_row, "0.5x–2.0x，立即作用于当前动画。")
            self.animation_gap = QSpinBox(self)
            self.animation_gap.setRange(0, 3600)
            self.animation_gap.setSuffix(" 秒")
            self.animation_gap.setValue(int(float(self.config().get("animation_gap_seconds") or 0.0)))
            self.animation_gap.valueChanged.connect(self._on_changed)
            animation_card.add_row(
                "动作等待间隔",
                self.animation_gap,
                "非待机动作之间的休息时间；0 表示连续播放。",
            )

            head_pat_card = self.section("摸头互动")
            self.head_pat_enabled = make_checkbox(self)
            self.head_pat_enabled.setChecked(
                bool(self.config().get("head_pat_enabled", True))
            )
            self.head_pat_enabled.toggled.connect(self._on_head_pat_enabled_changed)
            head_pat_card.add_row(
                "启用摸头互动",
                self.head_pat_enabled,
                "鼠标在角色头部区域连续往返晃动时播放一次动画。",
            )

            self.head_pat_mode = QComboBox(self)
            for key, label in (
                (HEAD_PAT_DEFAULT_MODE, "默认点击动画"),
                (HEAD_PAT_CUSTOM_MODE, "自定义动画"),
                (HEAD_PAT_ALL_MODE, "全部单次动画"),
            ):
                self.head_pat_mode.addItem(label, key)
            mode = str(
                self.config().get("head_pat_animation_mode") or HEAD_PAT_DEFAULT_MODE
            )
            index = self.head_pat_mode.findData(mode)
            self.head_pat_mode.setCurrentIndex(max(0, index))
            self.head_pat_mode.currentIndexChanged.connect(self._on_head_pat_mode_changed)
            head_pat_card.add_row(
                "动画范围",
                self.head_pat_mode,
                "只会选择当前角色存在的合格单次动画；拖拽、投掷和循环动画会排除。",
            )

            self.head_pat_info = QLabel(self)
            self.head_pat_info.setWordWrap(True)
            head_pat_card.add_row(
                "当前角色",
                self.head_pat_info,
                "自定义动画按当前宠物 slot 保存，切换角色后会重新过滤。",
            )

            self.head_pat_names = QListWidget(self)
            self.head_pat_names.setSelectionMode(
                QListWidget.SelectionMode.NoSelection
            )
            self.head_pat_names.setMinimumHeight(100)
            self.head_pat_names.itemChanged.connect(self._on_head_pat_names_changed)
            head_pat_card.add_row(
                "自定义清单",
                self.head_pat_names,
                "自定义模式下勾选希望播放的动画。",
                expand=True,
            )
            head_pat_buttons = [
                make_button("全选", self),
                make_button("恢复点击动画", self),
                make_button("清空", self),
            ]
            head_pat_buttons[0].clicked.connect(self._select_all_head_pat)
            head_pat_buttons[1].clicked.connect(self._restore_click_head_pat)
            head_pat_buttons[2].clicked.connect(self._clear_head_pat)
            head_pat_card.add_buttons(head_pat_buttons)
            self._refresh_head_pat_options()

            weight_card = self.section("自主行为概率")
            self._weights: dict[str, QSpinBox] = {}
            weight_rows = (
                ("anim_weight_idle", "待机", "四项按比例归一化，总和不必等于 100；0 表示从不。"),
                ("anim_weight_turn", "转向", ""),
                ("anim_weight_act", "随机动作", ""),
                ("anim_weight_move", "漫游", ""),
            )
            for key, label, hint in weight_rows:
                spin = QSpinBox(self)
                spin.setRange(0, 100)
                spin.setSuffix("%")
                spin.setValue(int(float(self.config().get(key) or 0)))
                spin.valueChanged.connect(self._on_changed)
                weight_card.add_row(label, spin, hint)
                self._weights[key] = spin

        with self.tab("easter", "彩蛋与配图"):
            egg_card = self.section("彩蛋")
            self.easter_enabled = make_checkbox(self)
            self.easter_enabled.setChecked(bool(self.config().get("easter_egg_enabled")))
            self.easter_enabled.toggled.connect(self._on_changed)
            egg_card.add_row(
                "启用彩蛋",
                self.easter_enabled,
                "关闭后菜单「彩蛋图片」只提示开关位置，不弹图片。",
            )

            self.easter_display = QComboBox(self)
            for key, label in (
                ("bubble", "气泡内显示（随桌宠移动）"),
                ("window", "独立窗口"),
            ):
                self.easter_display.addItem(label, key)
            self.easter_display.setMinimumWidth(240)
            self.easter_display.setCurrentIndex(
                max(0, self.easter_display.findData(self._easter_display_value()))
            )
            self.easter_display.currentIndexChanged.connect(self._on_changed)
            egg_card.add_row(
                "显示方式",
                self.easter_display,
                "气泡内显示会跟着桌宠移动；独立窗口可以拖到屏幕上的任意位置。",
            )

            self.easter_image_scale = QSpinBox(self)
            self.easter_image_scale.setRange(50, 300)
            self.easter_image_scale.setSingleStep(10)
            self.easter_image_scale.setSuffix("%")
            self.easter_image_scale.setValue(
                int(self.config().get("easter_egg_image_scale") or 100)
            )
            self.easter_image_scale.valueChanged.connect(self._on_changed)
            egg_card.add_row(
                "图片大小",
                self.easter_image_scale,
                "只影响彩蛋大图/气泡内彩蛋图的显示上限，不改变原图文件。",
            )

            self.easter_title = QLineEdit(self)
            self.easter_title.setMaxLength(40)
            self.easter_title.setText(str(self.config().get("easter_egg_title") or ""))
            self.easter_title.setMinimumWidth(220)
            self.easter_title.textChanged.connect(self._on_changed)
            egg_card.add_row("标题", self.easter_title, "弹窗里显示的标题，留空则不显示。")

            self.easter_hint = QLineEdit(self)
            self.easter_hint.setMaxLength(80)
            self.easter_hint.setText(str(self.config().get("easter_egg_hint") or ""))
            self.easter_hint.setMinimumWidth(220)
            self.easter_hint.textChanged.connect(self._on_changed)
            egg_card.add_row(
                "操作提示",
                self.easter_hint,
                "独立彩蛋窗口底部的提示文案；留空使用默认提示。",
            )

            avatar_row = QWidget(self)
            avatar_layout = QHBoxLayout(avatar_row)
            avatar_layout.setContentsMargins(0, 0, 0, 0)
            avatar_layout.setSpacing(8)
            self.easter_avatar_edit = value_field("", avatar_row)
            self.easter_avatar_edit.setMinimumWidth(220)
            self.easter_avatar_edit.setText(
                str(self.config().get("easter_egg_avatar") or "")
            )
            self.easter_avatar_edit.textChanged.connect(self._on_changed)
            avatar_layout.addWidget(self.easter_avatar_edit, 1)
            choose_avatar = make_button("选择图片…", avatar_row)
            choose_avatar.clicked.connect(self._choose_easter_avatar)
            avatar_layout.addWidget(choose_avatar)
            clear_avatar = make_button("清空（随机）", avatar_row)
            clear_avatar.clicked.connect(self._clear_easter_avatar)
            avatar_layout.addWidget(clear_avatar)
            egg_card.add_row(
                "指定头像",
                avatar_row,
                "无效或留空时回退到图片目录中的首张可读图片。",
                expand=True,
            )

            egg_dir_row = QWidget(self)
            egg_dir_layout = QHBoxLayout(egg_dir_row)
            egg_dir_layout.setContentsMargins(0, 0, 0, 0)
            egg_dir_layout.setSpacing(8)
            self.easter_dir_edit = value_field("", egg_dir_row)
            self.easter_dir_edit.setMinimumWidth(220)
            egg_dir_layout.addWidget(self.easter_dir_edit, 1)
            choose_dir = make_button("选择目录…", egg_dir_row)
            choose_dir.clicked.connect(self._choose_easter_dir)
            egg_dir_layout.addWidget(choose_dir)
            clear_dir = make_button("清空（用内置）", egg_dir_row)
            clear_dir.clicked.connect(self._clear_easter_dir)
            egg_dir_layout.addWidget(clear_dir)
            self.easter_preview = make_button("预览", egg_dir_row)
            self.easter_preview.clicked.connect(self._preview_easter)
            self.easter_preview.setEnabled(
                callable(getattr(getattr(self.host, "window", None), "show_easter_egg", None))
            )
            egg_dir_layout.addWidget(self.easter_preview)
            egg_card.add_row(
                "图片目录",
                egg_dir_row,
                "留空使用内置 assets/big_blue_fat_fish；目录里的 jpg/png/webp/gif 随机抽一张。",
                expand=True,
            )
            self._sync_easter_dir()

            image_card = self.section("自言自语配图")
            self.self_talk_dir_edit = value_field("", image_card)
            self.self_talk_dir_edit.setMinimumWidth(220)
            talk_dir_row = QWidget(self)
            talk_dir_layout = QHBoxLayout(talk_dir_row)
            talk_dir_layout.setContentsMargins(0, 0, 0, 0)
            talk_dir_layout.setSpacing(8)
            talk_dir_layout.addWidget(self.self_talk_dir_edit, 1)
            talk_choose = make_button("选择目录…", talk_dir_row)
            talk_choose.clicked.connect(self._choose_self_talk_dir)
            talk_dir_layout.addWidget(talk_choose)
            talk_clear = make_button("清空（用内置）", talk_dir_row)
            talk_clear.clicked.connect(self._clear_self_talk_dir)
            talk_dir_layout.addWidget(talk_clear)
            image_card.add_row(
                "图片目录",
                talk_dir_row,
                "留空使用内置 assets/big_blue_fat_fish；目录里的 jpg/png/webp/gif 随机抽一张。",
                expand=True,
            )

            self.self_talk_image_scale = QSpinBox(self)
            self.self_talk_image_scale.setRange(50, 300)
            self.self_talk_image_scale.setSingleStep(10)
            self.self_talk_image_scale.setSuffix("%")
            self.self_talk_image_scale.setValue(
                int(self.config().get("self_talk_image_scale") or 100)
            )
            self.self_talk_image_scale.valueChanged.connect(self._on_changed)
            image_card.add_row(
                "图片大小",
                self.self_talk_image_scale,
                "只影响自言语气泡配图的显示上限，100% 为默认。",
            )

            self.self_talk_image_chance = QSpinBox(self)
            self.self_talk_image_chance.setRange(0, 100)
            self.self_talk_image_chance.setSuffix("%")
            self.self_talk_image_chance.setValue(
                int(self.config().get("self_talk_image_chance") or 0)
            )
            self.self_talk_image_chance.valueChanged.connect(self._on_changed)
            image_card.add_row(
                "出图概率",
                self.self_talk_image_chance,
                "每次自言自语按此概率配图；0% = 只说话不出图。",
            )
            self.self_talk_image_info = QLabel("", self)
            self.self_talk_image_info.setObjectName("settingHint")
            self.self_talk_image_info.setWordWrap(True)
            image_card.add_row("当前目录", self.self_talk_image_info, "实际解析到的目录与可读图片数量。")
            preview_talk = make_button("预览配图", self)
            preview_talk.clicked.connect(self._preview_self_talk_image)
            image_card.add_buttons([preview_talk])
            self._sync_self_talk_dir()

        self._embed_owner(embedded, "tool.quiet-mode", "quiet", "免打扰")
        # 差异写入的基准：控件建好后才拿得到当前值。
        self._last_values = self._current()

    def _embed_owner(self, embedded: dict, owner: str, key: str, label: str) -> None:
        spec = embedded.pop(owner, None)
        if spec is None:
            return
        page = build_embedded_page(spec, self.host)
        if page is None:
            return
        self.embed_page(page, key, label)

    def config(self):
        return self.host.config

    # ---------------------------------------------------------------- 点击音效包
    def _read_sound_pack(self) -> dict[str, str]:
        pack = self.config().get("click_sound_pack")
        if not isinstance(pack, dict):
            pack = {}
        return {
            "kind": str(pack.get("kind") or "builtin"),
            "id": str(pack.get("id") or "default"),
            "path": str(pack.get("path") or ""),
        }

    def _sound_pack_key(self) -> str:
        if self._sound_pack.get("kind") == "builtin":
            return "builtin:" + (self._sound_pack.get("id") or "default")
        return str(self._sound_pack.get("kind") or "builtin")

    def _sound_path_text(self) -> str:
        pack = self._sound_pack
        if pack.get("kind") == "builtin":
            if pack.get("id") == "duck":
                return "内置小黄鸭音效包（assets/sounds/duck）"
            return "内置默认音效（assets/sounds/click.wav）"
        return str(pack.get("path") or "（未选择）")

    def _sync_sound_pack_widgets(self) -> None:
        index = self.sound_pack_combo.findData(self._sound_pack_key())
        self.sound_pack_combo.blockSignals(True)
        self.sound_pack_combo.setCurrentIndex(max(0, index))
        self.sound_pack_combo.blockSignals(False)
        text = self._sound_path_text()
        self.sound_path_edit.setText(text)
        self.sound_path_edit.setToolTip(text)

    def _on_sound_pack_changed(self, _index: int = 0) -> None:
        key = str(self.sound_pack_combo.currentData() or "builtin:default")
        if key.startswith("builtin:"):
            self._sound_pack["kind"] = "builtin"
            self._sound_pack["id"] = key.split(":", 1)[1] or "default"
        else:
            self._sound_pack["kind"] = key if key in {"file", "folder"} else "builtin"
        self._sync_sound_pack_widgets()
        self._on_changed()

    def _choose_sound_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "选择点击音效",
            str(self._sound_pack.get("path") or ""),
            "音频文件 (*.wav *.mp3 *.ogg *.flac *.m4a)",
        )
        if not path:
            return
        self._sound_pack["kind"] = "file"
        self._sound_pack["path"] = str(path)
        self._sync_sound_pack_widgets()
        self._on_changed()

    def _choose_sound_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "选择点击音效文件夹", str(self._sound_pack.get("path") or "")
        )
        if not folder:
            return
        self._sound_pack["kind"] = "folder"
        self._sound_pack["path"] = str(folder)
        self._sync_sound_pack_widgets()
        self._on_changed()

    def _preview_sound(self) -> None:
        candidates = resolve_click_sound_candidates(self._sound_pack, paths.legacy_asset_root())
        chosen = choose_click_sound(candidates)
        if chosen is None:
            self._notify("没有可播放的音效：请检查音源设置。")
            return
        if self._sound is None:
            self._sound = SoundPlayer(float(self.config().get("volume") or 0.8))
        self._sound.play(chosen)

    def dispose(self) -> None:
        if self._sound is not None:
            try:
                self._sound.stop_all()
            except Exception:
                pass
            self._sound = None
        super().dispose()

    # ---------------------------------------------------------------- 彩蛋
    def _easter_display_value(self) -> str:
        value = str(self.config().get(EASTER_DISPLAY_KEY) or "")
        return value if value in {"bubble", "window"} else EASTER_DISPLAY_DEFAULT

    def _easter_dir_text(self) -> str:
        config = self.config()
        return str(config.get("easter_egg_image_dir") or "")

    def _sync_easter_dir(self) -> None:
        text = self._easter_dir_text()
        self.easter_dir_edit.setText(text)
        self.easter_dir_edit.setPlaceholderText("留空使用内置图片目录")
        self.easter_dir_edit.setToolTip(text or "留空使用内置图片目录")

    def _choose_easter_avatar(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "选择彩蛋头像",
            str(self.config().get("easter_egg_image_dir") or ""),
            "图片 (*.png *.jpg *.jpeg *.webp *.gif)",
        )
        if path:
            self.easter_avatar_edit.setText(path)

    def _clear_easter_avatar(self) -> None:
        self.easter_avatar_edit.clear()

    def _choose_easter_dir(self) -> None:
        config = self.config()
        folder = QFileDialog.getExistingDirectory(
            self, "选择彩蛋图片目录", str(config.get("easter_egg_image_dir") or "")
        )
        if not folder:
            return
        self.easter_dir_edit.setText(folder)
        self.easter_dir_edit.setToolTip(folder)
        self._on_changed()

    def _clear_easter_dir(self) -> None:
        self.easter_dir_edit.setText("")
        self.easter_dir_edit.setToolTip("")
        self._on_changed()

    def _sync_self_talk_dir(self) -> None:
        text = str(self.config().get("self_talk_image_dir") or "").strip()
        self.self_talk_dir_edit.setText(text)
        self.self_talk_dir_edit.setPlaceholderText("留空使用内置图片目录")
        self.self_talk_dir_edit.setToolTip(text or "留空使用内置图片目录")
        self._update_self_talk_info()

    def _update_self_talk_info(self) -> None:
        label = getattr(self, "self_talk_image_info", None)
        if label is None:
            return
        directory, images = talk_images.configured_images(self.config())
        fallback = "" if directory == talk_images.builtin_image_dir() else "（已回退内置）"
        label.setText(f"{directory}{fallback} · {len(images)} 张")

    def _preview_self_talk_image(self) -> None:
        directory, images = talk_images.configured_images(self.config())
        if not images:
            self._notify(f"目录里没有可读图片：{directory}")
            return
        window = getattr(self.host, "window", None)
        show = getattr(window, "show_image", None)
        if not callable(show):
            self._notify("当前窗口不支持图片气泡。")
            return
        scale = talk_images.image_scale(self.config()) / 100.0
        path = random.choice(images)
        shown = show(path, "自言自语配图预览", 4000, image_scale=scale)
        if not shown:
            self._notify(f"图片气泡没能显示：{path.name}")

    def _choose_self_talk_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "选择自言自语图片目录",
            str(self.config().get("self_talk_image_dir") or ""),
        )
        if not folder:
            return
        self.self_talk_dir_edit.setText(folder)
        self.self_talk_dir_edit.setToolTip(folder)
        self._on_changed()
        self._update_self_talk_info()

    def _clear_self_talk_dir(self) -> None:
        self.self_talk_dir_edit.setText("")
        self.self_talk_dir_edit.setToolTip("")
        self._on_changed()
        self._update_self_talk_info()

    def _preview_easter(self) -> None:
        window = getattr(self.host, "window", None)
        preview = getattr(window, "show_easter_egg", None)
        if callable(preview):
            preview()

    def _notify(self, text: str) -> None:
        say = getattr(getattr(self.host, "window", None), "say", None)
        if not callable(say):
            return
        try:
            say(text, force=True)
        except TypeError:
            try:
                say(text)
            except Exception:
                pass
        except Exception:
            pass

    def _sync_spin_direct_row(self, *_args) -> None:
        enabled = self._checks["golden_spin_on_click"].isChecked()
        self._spin_direct_row.setVisible(enabled)

    def _sync_squash_rows(self, *_args) -> None:
        enabled = self._checks["click_squash_enabled"].isChecked()
        for widget in (self.squash_bounces, self.squash_strength, self.squash_ms):
            widget.setEnabled(enabled)

    def _on_balance_tier_changed(self, *_args) -> None:
        self._sync_tier_custom_rows()
        self._on_changed()

    def _sync_tier_custom_rows(self, *_args) -> None:
        """自定义文本两行只在「自定义」模式出现（其余模式写进去也不生效）。"""
        custom = str(self.balance_tier_mode.currentData() or "default") == "custom"
        self._tier_peak_row.setVisible(custom)
        self._tier_idle_row.setVisible(custom)

    # ------------------------------------------------------------ 摸头互动
    def _head_pat_window(self):
        return getattr(self.host, "window", None)

    def _head_pat_catalog(self):
        return getattr(self._head_pat_window(), "catalog", None)

    def _head_pat_available(self):
        catalog = self._head_pat_catalog()
        return eligible_head_pat_specs(getattr(catalog, "animations", ()))

    def _refresh_head_pat_options(self) -> None:
        available = self._head_pat_available()
        configured = {
            str(name).strip()
            for name in (self.config().get("head_pat_animation_names") or ())
            if str(name).strip()
        }
        self.head_pat_names.blockSignals(True)
        self.head_pat_names.clear()
        for spec in available:
            item = QListWidgetItem(spec.name, self.head_pat_names)
            item.setData(Qt.ItemDataRole.UserRole, spec.name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if spec.name in configured
                else Qt.CheckState.Unchecked
            )
        self.head_pat_names.blockSignals(False)
        window = self._head_pat_window()
        character = getattr(window, "character", None)
        character_name = getattr(character, "name", None) or getattr(
            window, "character_id", None
        ) or "未加载角色"
        self.head_pat_info.setText(f"{character_name} · {len(available)} 个合格动画")
        self._sync_head_pat_list_enabled()

    def _sync_head_pat_list_enabled(self) -> None:
        enabled = (
            self.head_pat_enabled.isChecked()
            and self.head_pat_mode.currentData() == HEAD_PAT_CUSTOM_MODE
        )
        self.head_pat_names.setEnabled(enabled)

    def _head_pat_checked_names(self) -> list[str]:
        names: list[str] = []
        for index in range(self.head_pat_names.count()):
            item = self.head_pat_names.item(index)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            name = str(item.data(Qt.ItemDataRole.UserRole) or item.text()).strip()
            if name and name not in names:
                names.append(name)
        return names

    def _on_head_pat_enabled_changed(self, *_args) -> None:
        self._sync_head_pat_list_enabled()
        self._on_changed()

    def _on_head_pat_mode_changed(self, *_args) -> None:
        self._sync_head_pat_list_enabled()
        self._on_changed()

    def _on_head_pat_names_changed(self, *_args) -> None:
        self._on_changed()

    def _set_all_head_pat_checks(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.head_pat_names.blockSignals(True)
        for index in range(self.head_pat_names.count()):
            self.head_pat_names.item(index).setCheckState(state)
        self.head_pat_names.blockSignals(False)
        self._on_changed()

    def _select_all_head_pat(self) -> None:
        self._set_all_head_pat_checks(True)

    def _restore_click_head_pat(self) -> None:
        catalog = self._head_pat_catalog()
        names = {
            spec.name
            for spec in head_pat_specs(catalog, mode=HEAD_PAT_DEFAULT_MODE)
        }
        self.head_pat_names.blockSignals(True)
        for index in range(self.head_pat_names.count()):
            item = self.head_pat_names.item(index)
            item.setCheckState(
                Qt.CheckState.Checked
                if item.text() in names
                else Qt.CheckState.Unchecked
            )
        self.head_pat_names.blockSignals(False)
        self._on_changed()

    def _clear_head_pat(self) -> None:
        self._set_all_head_pat_checks(False)

    def _snapshot(self) -> dict:
        config = self.config()
        values = {key: bool(config.get(key)) for key in self._CHECK_KEYS}
        values["throw_strength"] = str(config.get("throw_strength") or "standard")
        values["playback_speed"] = float(config.get("playback_speed") or 1.0)
        values["click_squash_bounces"] = int(config.get("click_squash_bounces") or 1)
        values["click_squash_strength"] = int(config.get("click_squash_strength") or 100)
        values["click_squash_ms"] = int(float(config.get("click_squash_ms") or 220))
        values["animation_gap_seconds"] = int(float(config.get("animation_gap_seconds") or 0.0))
        values["head_pat_enabled"] = bool(config.get("head_pat_enabled", True))
        values["head_pat_animation_mode"] = str(
            config.get("head_pat_animation_mode") or HEAD_PAT_DEFAULT_MODE
        )
        values["head_pat_animation_names"] = list(
            config.get("head_pat_animation_names") or ()
        )
        for key in (
            "anim_weight_idle",
            "anim_weight_turn",
            "anim_weight_act",
            "anim_weight_move",
        ):
            values[key] = int(float(config.get(key) or 0))
        values["click_sound_pack"] = self._read_sound_pack()
        values["balance_tier_labels_mode"] = str(
            config.get("balance_tier_labels_mode") or "default"
        )
        values["balance_tier_label_peak"] = str(
            config.get("balance_tier_label_peak") or ""
        )
        values["balance_tier_label_idle"] = str(
            config.get("balance_tier_label_idle") or ""
        )
        values["easter_egg_enabled"] = bool(config.get("easter_egg_enabled"))
        values["easter_egg_display"] = self._easter_display_value()
        values["easter_egg_title"] = str(config.get("easter_egg_title") or "")
        values["easter_egg_hint"] = str(config.get("easter_egg_hint") or "")
        values["easter_egg_avatar"] = str(config.get("easter_egg_avatar") or "")
        values["easter_egg_image_dir"] = str(config.get("easter_egg_image_dir") or "")
        values["easter_egg_image_scale"] = int(
            config.get("easter_egg_image_scale") or 100
        )
        values["self_talk_image_dir"] = str(config.get("self_talk_image_dir") or "")
        values["self_talk_image_scale"] = int(config.get("self_talk_image_scale") or 100)
        values["self_talk_image_chance"] = int(
            config.get("self_talk_image_chance") or 0
        )
        return values

    def _current(self) -> dict:
        values = {key: bool(box.isChecked()) for key, box in self._checks.items()}
        values["throw_strength"] = str(self.throw_strength.currentData() or "standard")
        values["playback_speed"] = float(self.playback_speed.value())
        values["click_squash_bounces"] = int(self.squash_bounces.value())
        values["click_squash_strength"] = int(self.squash_strength.value())
        values["click_squash_ms"] = int(self.squash_ms.value())
        values["animation_gap_seconds"] = int(self.animation_gap.value())
        values["head_pat_enabled"] = bool(self.head_pat_enabled.isChecked())
        values["head_pat_animation_mode"] = str(
            self.head_pat_mode.currentData() or HEAD_PAT_DEFAULT_MODE
        )
        values["head_pat_animation_names"] = self._head_pat_checked_names()
        for key, spin in self._weights.items():
            values[key] = int(spin.value())
        values["click_sound_pack"] = dict(self._sound_pack)
        values["balance_tier_labels_mode"] = str(
            self.balance_tier_mode.currentData() or "default"
        )
        values["balance_tier_label_peak"] = self.balance_tier_peak.text().strip()
        values["balance_tier_label_idle"] = self.balance_tier_idle.text().strip()
        values["easter_egg_enabled"] = bool(self.easter_enabled.isChecked())
        values["easter_egg_display"] = str(
            self.easter_display.currentData() or EASTER_DISPLAY_DEFAULT
        )
        values["easter_egg_title"] = self.easter_title.text().strip()
        values["easter_egg_hint"] = self.easter_hint.text().strip()
        values["easter_egg_avatar"] = self.easter_avatar_edit.text().strip()
        values["easter_egg_image_dir"] = self.easter_dir_edit.text().strip()
        values["easter_egg_image_scale"] = int(self.easter_image_scale.value())
        values["self_talk_image_dir"] = self.self_talk_dir_edit.text().strip()
        values["self_talk_image_scale"] = int(self.self_talk_image_scale.value())
        values["self_talk_image_chance"] = int(self.self_talk_image_chance.value())
        return values

    def _on_changed(self, *_args) -> None:
        """只写**用户真的改过**的键。

        旧实现一次回写 35 个键（用打开设置时定格的控件值），会把右键菜单里刚改的
        勾选项按旧值翻回去并落盘（审计 AUD-27）。以「上次写出去的值」为基准做差异
        写入，未触碰的键一律不动。
        """
        config = self.config()
        values = self._current()
        for key, value in values.items():
            if value == self._last_values.get(key):
                continue
            config.set(EASTER_DISPLAY_KEY if key == "easter_egg_display" else key, value)
            self._last_values[key] = value
        self.changed.emit()

    def dirty(self) -> bool:
        return self._current() != self._initial or self._embedded_dirty()

    def confirm_save(self) -> None:
        self._on_changed()
        self._initial = self._snapshot()
        self._last_values = self._current()
        self._embedded_confirm_save()

    def discard_changes(self) -> None:
        config = self.config()
        for key in self._CHECK_KEYS:
            config.set(key, self._initial[key])
        self._last_values = dict(self._initial)
        config.set("throw_strength", self._initial["throw_strength"])
        config.set("playback_speed", self._initial["playback_speed"])
        config.set("animation_gap_seconds", self._initial["animation_gap_seconds"])
        config.set("head_pat_enabled", self._initial["head_pat_enabled"])
        config.set("head_pat_animation_mode", self._initial["head_pat_animation_mode"])
        config.set("head_pat_animation_names", list(self._initial["head_pat_animation_names"]))
        for key in self._weights:
            config.set(key, self._initial[key])
        config.set("click_sound_pack", dict(self._initial["click_sound_pack"]))
        config.set(
            "balance_tier_labels_mode", self._initial["balance_tier_labels_mode"]
        )
        config.set("balance_tier_label_peak", self._initial["balance_tier_label_peak"])
        config.set("balance_tier_label_idle", self._initial["balance_tier_label_idle"])
        config.set("easter_egg_enabled", self._initial["easter_egg_enabled"])
        config.set(EASTER_DISPLAY_KEY, self._initial["easter_egg_display"])
        config.set("easter_egg_title", self._initial["easter_egg_title"])
        config.set("easter_egg_hint", self._initial["easter_egg_hint"])
        config.set("easter_egg_avatar", self._initial["easter_egg_avatar"])
        config.set("easter_egg_image_dir", self._initial["easter_egg_image_dir"])
        config.set("easter_egg_image_scale", self._initial["easter_egg_image_scale"])
        config.set("self_talk_image_dir", self._initial["self_talk_image_dir"])
        config.set("self_talk_image_scale", self._initial["self_talk_image_scale"])
        config.set("self_talk_image_chance", self._initial["self_talk_image_chance"])
        for key, box in self._checks.items():
            box.blockSignals(True)
            box.setChecked(bool(self._initial[key]))
            box.blockSignals(False)
        self.throw_strength.blockSignals(True)
        self.throw_strength.setCurrentIndex(max(0, self.throw_strength.findData(self._initial["throw_strength"])))
        self.throw_strength.blockSignals(False)
        self.playback_speed.blockSignals(True)
        self.playback_speed.setValue(self._initial["playback_speed"])
        self.playback_speed.blockSignals(False)
        for widget, key in (
            (self.squash_bounces, "click_squash_bounces"),
            (self.squash_strength, "click_squash_strength"),
            (self.squash_ms, "click_squash_ms"),
        ):
            widget.blockSignals(True)
            widget.setValue(int(self._initial[key]))
            widget.blockSignals(False)
        self._sync_squash_rows()
        self.animation_gap.blockSignals(True)
        self.animation_gap.setValue(self._initial["animation_gap_seconds"])
        self.animation_gap.blockSignals(False)
        self.head_pat_enabled.blockSignals(True)
        self.head_pat_enabled.setChecked(bool(self._initial["head_pat_enabled"]))
        self.head_pat_enabled.blockSignals(False)
        self.head_pat_mode.blockSignals(True)
        self.head_pat_mode.setCurrentIndex(
            max(0, self.head_pat_mode.findData(self._initial["head_pat_animation_mode"]))
        )
        self.head_pat_mode.blockSignals(False)
        self._refresh_head_pat_options()
        for key, spin in self._weights.items():
            spin.blockSignals(True)
            spin.setValue(int(self._initial[key]))
            spin.blockSignals(False)
        self._sound_pack = dict(self._initial["click_sound_pack"])
        self._sync_sound_pack_widgets()
        self.balance_tier_mode.blockSignals(True)
        self.balance_tier_mode.setCurrentIndex(
            max(
                0,
                self.balance_tier_mode.findData(
                    self._initial["balance_tier_labels_mode"]
                ),
            )
        )
        self.balance_tier_mode.blockSignals(False)
        for widget, key in (
            (self.balance_tier_peak, "balance_tier_label_peak"),
            (self.balance_tier_idle, "balance_tier_label_idle"),
        ):
            widget.blockSignals(True)
            widget.setText(str(self._initial[key]))
            widget.blockSignals(False)
        self._sync_tier_custom_rows()
        self.easter_enabled.blockSignals(True)
        self.easter_enabled.setChecked(bool(self._initial["easter_egg_enabled"]))
        self.easter_enabled.blockSignals(False)
        self.easter_title.blockSignals(True)
        self.easter_title.setText(str(self._initial["easter_egg_title"]))
        self.easter_title.blockSignals(False)
        self.easter_hint.blockSignals(True)
        self.easter_hint.setText(str(self._initial["easter_egg_hint"]))
        self.easter_hint.blockSignals(False)
        self.easter_avatar_edit.blockSignals(True)
        self.easter_avatar_edit.setText(str(self._initial["easter_egg_avatar"]))
        self.easter_avatar_edit.blockSignals(False)
        self.easter_display.blockSignals(True)
        self.easter_display.setCurrentIndex(
            max(0, self.easter_display.findData(str(self._initial["easter_egg_display"])))
        )
        self.easter_display.blockSignals(False)
        self.easter_dir_edit.setText(str(self._initial["easter_egg_image_dir"]))
        self.easter_dir_edit.setToolTip(str(self._initial["easter_egg_image_dir"]))
        self.easter_image_scale.blockSignals(True)
        self.easter_image_scale.setValue(int(self._initial["easter_egg_image_scale"]))
        self.easter_image_scale.blockSignals(False)
        self.self_talk_image_scale.blockSignals(True)
        self.self_talk_image_scale.setValue(int(self._initial["self_talk_image_scale"]))
        self.self_talk_image_scale.blockSignals(False)
        self.self_talk_image_chance.blockSignals(True)
        self.self_talk_image_chance.setValue(int(self._initial["self_talk_image_chance"]))
        self.self_talk_image_chance.blockSignals(False)
        self.self_talk_dir_edit.setText(str(self._initial["self_talk_image_dir"]))
        self.self_talk_dir_edit.setToolTip(str(self._initial["self_talk_image_dir"]))
        self._sync_spin_direct_row()
        self._embedded_discard()
        self.changed.emit()

    def keywords(self) -> list[str]:
        return [
            "互动",
            "物理",
            "点击",
            "Q弹",
            "弹跳次数",
            "弹性强度",
            "持续时间",
            "音效",
            "点击音效",
            "音效音源",
            "小黄鸭",
            "duck",
            "click sound",
            "sound pack",
            "余额",
            "点击查看余额",
            "点击显示余额",
            "峰谷",
            "高峰",
            "空闲",
            "梁文峰",
            "梁文谷",
            "balance",
            "tier",
            "点击台词",
            "自言自语",
            "免打扰",
            "彩蛋",
            "彩蛋图片",
            "显示方式",
            "图片大小",
            "指定头像",
            "操作提示",
            "独立窗口",
            "easter egg",
            "拖拽",
            "拖动物理",
            "投掷",
            "弹弓",
            "抛掷",
            "碰撞",
            "投喂",
            "黄金回旋",
            "探头",
            "动画",
            "摸头",
            "被摸头",
            "头部",
            "Touch_Head",
            "head pat",
            "播放速率",
            "等待间隔",
            "概率",
            "待机",
            "转向",
            "随机动作",
            "漫游",
            "不自动移动",
            "interaction",
            "physics",
            "click",
            "drag",
            "slingshot",
            "collision",
            "feeding",
            "golden spin",
            "animation",
            "probability",
            "idle",
            "turn",
            "act",
            "move",
            *self._embedded_keywords(),
        ]
