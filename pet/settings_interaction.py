# -*- coding: utf-8 -*-
"""设置页「互动」域：页内任务标签（点击与音效 / 自言自语）+ 本域全部设置行。

**为什么单独一个模块**：``modern_settings_dialog.py`` 是"上帝对话框"（行数预算
反复触顶），本域的行定义与页装配搬到这里，收益是双向的——既让互动域长得像
「菜单」域那样可分页（主人反馈「单页面太多东西了」），又把 60+ 行从该文件搬出。

**为什么用页内标签而不是再拆一个侧栏域**：侧栏 9 个域是外部契约
（``SETTINGS_DOMAIN_NAV``，多处测试与截图脚本按索引/名称依赖）；同一个能力域内的
多个**同级任务**适合页内标签（先例：菜单域的 菜单编排 / 快捷启动 / 外观）。本域两个
标签就是两个同级任务：**点击与音效**（输入 + 点击反馈）、**自言自语**（周期气泡的
台词与配图）。

**接线契约**（与 ``settings_file_interpret`` 同口径）：

- 行在本模块构建、由 ``build_interaction_domain`` 挂进「互动」域；
- 行**不在** ``_rebuild_domain_navigation`` 开头的 ``all_rows`` 快照里，因此不需要
  ``claim``，也不会被判成「待分类（开发期）」；
- ``objectName`` 仍是 ``settingRow_<配置键>``，所以显隐联动（``_update_self_talk_controls``
  等按 objectName ``findChild``）与设置搜索索引照旧工作；
- 保存链一字不用改：``_write_config`` 全部基于控件属性。
"""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from .settings_widgets import SettingRow, SettingsSection, SettingsTabContainer

TAB_KEYS = ("click", "self_talk")
TAB_LABELS = ("点击与音效", "自言自语")


def _page(dialog, title: str, sections: list[tuple[str, list[SettingRow]]]) -> QWidget:
    """标签页内容 = 若干 ``SettingsSection`` 竖排（与域页里的组渲染一致）。"""
    content = QWidget(dialog)
    layout = QVBoxLayout(content)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(18)
    for section_title, rows in sections:
        layout.addWidget(SettingsSection(section_title, rows, content))
    layout.addStretch(1)
    return content


def build_click_rows(dialog) -> list[SettingRow]:
    """「点击反馈」组的行（顺序与既有渲染顺序一致，多处用例按顺序/组名断言）。"""
    rows = [
        SettingRow("click_sound", "点击音效", "点击桌宠时播放轻量反馈音效。", dialog.click_sound_check),
        SettingRow("click_sound_pack", "音效音源", "选择预设音效包、自定义音频文件或文件夹随机播放。", dialog.click_sound_picker, stacked=True),
        SettingRow("click_sound_volume", "音效音量", "调整点击音效播放音量。", dialog.click_sound_volume_spin),
        SettingRow("click_sound_preview", "试听音效", "测试当前选择的点击音效。", dialog.click_sound_preview_btn),
    ]
    if getattr(dialog, "click_balance_check", None) is not None:
        # 余额能力可用时才出现（与控件创建口径一致），位置保持在「试听音效」之后。
        rows.append(
            SettingRow(
                "click_balance",
                "点击显示余额",
                "点击桌宠时查询并用气泡展示模型服务余额。",
                dialog.click_balance_check,
            )
        )
    rows.extend(
        [
            SettingRow(
                "click_self_talk", "点击触发自言自语", "点击时随机显示一条自言自语内容；打开后可用下方两项把同一句读出来。", dialog.click_self_talk_check
            ),
            SettingRow(
                "click_self_talk_speak",
                "点击台词朗读",
                "点击触发自言自语时，用语音报时的音色把这句话读出来（与报时共用音频通道，不会叠音）。",
                dialog.click_self_talk_speak_check,
            ),
            SettingRow(
                "click_self_talk_precache",
                "台词自动预缓存",
                "保存设置后，在后台把点击台词与点击动画绑定台词合成为本机语音文件，"
                "点击时秒出、断网也能说（需要本机 CosyVoice 语音服务在运行；"
                "没装本地语音服务时保持关闭即可，新台词仍走在线合成）。",
                dialog.self_talk_voice_precache_check,
            ),
            SettingRow("click_talk_bindings", "点击动画台词绑定", "为每个点击动画设置专属自言自语台词。", dialog.click_talk_bindings_btn),
            SettingRow(
                "golden_spin_click",
                "点击触发黄金回旋",
                "开启后点击桌宠触发原地逆时针 360° 旋转；下方子开关可选择跳过点击动画直接回旋。",
                dialog.golden_spin_click_check,
            ),
            SettingRow(
                "golden_spin_direct",
                "点击回旋跳过动画",
                "开启后点击直接开始黄金回旋，不播放 Q 弹与点击素材；连续点击会累计旋转圈数并逐圈加速。",
                dialog.golden_spin_direct_check,
                stacked=True,
            ),
        ]
    )
    return rows


def build_self_talk_rows(dialog) -> list[SettingRow]:
    """「自言自语」组的行（周期气泡的台词 / 节奏 / 配图）。"""
    return [
        SettingRow(
            "self_talk_bubble_style",
            "气泡方案",
            "选择气泡视觉与相对桌宠的位置；贴近屏幕边缘时自动换位。",
            dialog.bubble_style_select,
        ),
        SettingRow("self_talk", "气泡自言自语", "让桌宠偶尔显示一条随机思考气泡。", dialog.self_talk_check),
        SettingRow("self_talk_duration", "显示时间", "每条文字或图片气泡保持显示的时间。", dialog.self_talk_duration_spin),
        SettingRow("self_talk_min", "最短间隔", "上一条气泡消失后，到下一条出现前的最短空闲时间。", dialog.min_spin),
        SettingRow("self_talk_max", "最长间隔", "上一条气泡消失后，到下一条出现前的最长空闲时间。", dialog.max_spin),
        SettingRow("self_talk_texts", "候选内容", "每行一条；留空时恢复内置文本。", dialog.texts_edit, stacked=True),
        SettingRow(
            "self_talk_images",
            "图片目录",
            "从目录中的常见图片格式随机选择；默认使用内置彩蛋图片池，留空时只显示文本。",
            dialog.self_talk_image_dir_picker,
            stacked=True,
        ),
        SettingRow("self_talk_image_scale", "配图大小", "气泡里配图的显示尺寸（100% 为默认）。", dialog.self_talk_image_scale_spin),
        SettingRow(
            "self_talk_image_chance",
            "配图概率",
            "点击/定时自言自语时显示配图的概率，其余显示文本；0% 表示只出文本。图片目录里往往有几十张图，这一项决定文本还能不能轮到（默认 30%）。",
            dialog.self_talk_image_chance_spin,
        ),
    ]


def build_interaction_domain(dialog) -> QWidget:
    """「互动」域整页：一个页内任务标签容器（两个标签各含自己的组）。

    组名 ``输入`` / ``点击反馈`` / ``自言自语`` 保持不变——它们是对外契约，
    既有用例（test_menu_layout / test_desktop_pet_features / test_requested_regressions）
    按组名硬编码断言。
    """
    tabs = SettingsTabContainer(dialog)
    tabs.addTab(
        TAB_KEYS[0],
        TAB_LABELS[0],
        _page(
            dialog,
            TAB_LABELS[0],
            [
                (
                    "输入",
                    [
                        SettingRow(
                            "mouse_through",
                            "鼠标穿透",
                            "开启后桌宠不接收鼠标事件，点击穿透到下层窗口。",
                            dialog.mouse_through_check,
                        )
                    ],
                ),
                ("点击反馈", build_click_rows(dialog)),
            ],
        ),
    )
    tabs.addTab(
        TAB_KEYS[1],
        TAB_LABELS[1],
        _page(dialog, TAB_LABELS[1], [("自言自语", build_self_talk_rows(dialog))]),
    )
    return tabs
