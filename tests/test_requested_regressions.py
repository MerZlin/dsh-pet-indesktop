import os
import types

import pytest
from datetime import datetime, timezone


def _slot0_config(tmp_path):
    """生产形态的配置面：slot0 身份经 ``SlotRoutedConfig`` 分流。

    每宠键（气泡文字大小、气泡方案…）的真值在 ``config-slot-0.json``：裸全局
    ``Config.set/save`` 出闸时会过滤掉它们，改不动任何一只宠。
    """
    from pet.config import Config
    from pet.settings_slot_config import route_settings_config

    return route_settings_config(Config(base=tmp_path, instance_id="slot-0"))


def test_modern_message_card_opacity_is_configurable_and_persisted(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.chat.themes import build_modern_custom_overlay_qss
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    config = Config(tmp_path)
    assert config.get("modern_chat_card_opacity") == 84

    dialog = settings_mod.ModernSettingsDialog(config, include_ai=True)
    row = dialog.findChild(
        settings_mod.SettingRow, "settingRow_modern_chat_card_opacity"
    )
    assert row is not None and not row.isHidden()
    dialog.ai_page.chat_ui_style.setCurrentData("classic")
    assert row.isHidden()
    dialog.ai_page.chat_ui_style.setCurrentData("modern")
    assert not row.isHidden()
    dialog.ai_page.message_card_opacity.setValue(65)
    dialog._save()

    config.reload()  # #271：写盘走路由视图另建的主配置，传入对象需重读才可见
    assert config.get("modern_chat_card_opacity") == 65
    assert "rgba(255, 255, 255, 166)" in build_modern_custom_overlay_qss(
        "#3994ff", 65
    )
    dialog.close()
    app.processEvents()


def test_modern_chat_header_uses_current_pet_image(tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication

    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    avatar = QPixmap(24, 24)
    avatar.fill(Qt.GlobalColor.red)

    class Pet:
        def icon_pixmap(self, size):
            assert size == 34
            return avatar

    window = ChatWindow(Config(tmp_path), "shenshen", pet_window=Pet())
    assert window.avatar_label.text() == ""
    assert not window.avatar_label.pixmap().isNull()
    window.close()
    app.processEvents()


def test_bubble_text_scale_row_persists(tmp_path, monkeypatch):
    """「气泡文字大小」设置行：默认 100%，改值保存并持久化，越界被配置清洗钳位。"""
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    config = _slot0_config(tmp_path)
    assert config.get("bubble_text_scale") == 100

    dialog = settings_mod.ModernSettingsDialog(config, include_ai=True)
    try:
        assert dialog.bubble_text_scale_spin.value() == 100
        assert dialog.bubble_text_scale_spin.minimum() == 50
        assert dialog.bubble_text_scale_spin.maximum() == 300
        assert dialog.findChild(
            settings_mod.SettingRow, "settingRow_bubble_text_scale"
        ) is not None, "设置页必须有可发现的「气泡文字大小」行"
        dialog.bubble_text_scale_spin.setValue(180)
        assert dialog._write_config() is True
    finally:
        dialog.close()
        app.processEvents()
    assert _slot0_config(tmp_path).get("bubble_text_scale") == 180


def test_harness_autostart_toggle_persisted(tmp_path, monkeypatch):
    """「随桌宠启动 dsh 服务」开关：默认关，开启后保存并持久化。"""
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    config = Config(tmp_path)
    assert config.get("harness_autostart") is False

    dialog = settings_mod.ModernSettingsDialog(config, include_ai=True)
    assert dialog.harness_autostart_check.isChecked() is False
    dialog.harness_autostart_check.setChecked(True)
    assert dialog._write_config() is True
    assert Config(tmp_path).get("harness_autostart") is True
    dialog.close()
    app.processEvents()


def test_harness_autostart_hint_has_no_markdown_and_names_the_real_stop_gate(tmp_path, monkeypatch):
    """开关说明：不得含字面 Markdown，并如实写全自启门与收口的启动窗口例外。

    实机核对（pet/app.py）：
    - **自启是三条件合取**（``_harness_autostart_wanted``，:2161-2165）：
      ``enable_chat`` + 本开关 + **DSH 联动开着**。联动没开时本开关单独是摆设
      （``test_autostart_gated_off_when_dsh_link_disabled`` 钉住行为），hint 若不写
      这一条，用户看到的就是「开了却什么都不发生」。
    - **收口不止「退出 / 关闭 DSH 联动」两条路**：还有 ``pet/app.py:2136-2138`` 的
      **启动窗口补偿**——刚好在拉起探测期间关掉本项，那一次刚拉起的 web 会被立刻
      收掉（``test_autostart_stops_immediately_if_gate_flips_after_launch``）。稳态
      收口与这一次性补偿必须分开写，否则「关闭本项不会停掉已拉起的服务」在最容易
      被看到的时刻（刚开自启又改主意）就是假话。

    旧文案把 ``**`` 当粗体写进纯文本 hint（原样显示成星号），且缺上面两条事实。
    """
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=True)
    row = dialog.findChild(settings_mod.SettingRow, "settingRow_harness_autostart")
    assert row is not None
    text = row.hint_label.text()
    assert "**" not in text, "hint 是纯文本渲染，字面 Markdown 会原样显示成星号"
    assert "不再自动拉起" in text
    assert "关闭 DSH 联动" in text, "必须说清已在跑的 web 由谁收口"
    assert "只会停止" not in text, "不得声称关闭本开关会停掉已拉起的服务"
    # 自启的必要条件（app.py:2161-2165 的三条件合取）必须写出来
    assert "同时开着 DSH 联动" in text, "必须写明联动不开则不自启，否则开关像个摆设"
    # 启动窗口补偿是收口的第三路，必须与稳态收口分开描述
    assert "例外" in text and "探测期间" in text, (
        "必须写明「拉起探测期间关掉本项会立刻收掉刚拉起的实例」这一例外"
    )
    dialog.close()
    app.processEvents()


def test_readme_harness_autostart_paragraph_matches_the_launch_gates():
    """README 的「随桌宠启动 dsh 服务」段落与代码同口径（自启门 + 收口例外）。

    同一事实的双处守卫：设置页 hint 由上面那条用例看着，README 段落此前无人看管，
    于是同一句话在 README 里停在旧口径（缺联动门、缺启动窗口例外）。代码依据同上
    （``pet/app.py`` :2161-2165 与 :2136-2138）。
    """
    from pathlib import Path

    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    para = next((line for line in readme.splitlines() if "随桌宠启动 dsh 服务" in line), None)
    assert para is not None, "README 里找不到「随桌宠启动 dsh 服务」段落"
    assert "DSH 联动" in para
    assert "不会拉起" in para, "必须写明联动关着时不自启（联动没开时 dsh web 无人消费）"
    assert "例外" in para and "探测期间" in para, (
        "必须写明「拉起探测期间关掉开关会立刻收掉刚拉起的实例」这一例外"
    )


def test_harness_target_desktop_disabled_and_never_saved_on_unsupported_platform(tmp_path, monkeypatch):
    """非 Windows：桌面端目标保留但置灰，且绝不写回 desktop。

    该平台上 ``launch_harness(target="desktop")`` 报 unsupported（web 也不起）：
    设置页若仍允许选中并保存，用户下一次开自启就是「什么都没发生」，且这句谎话
    会一直躺在配置里。存量 desktop（比如从 Windows 带过来的配置）必须回落。
    """
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    monkeypatch.setattr(settings_mod.harness_mod, "DESKTOP_SUPPORTED", False)
    config = Config(tmp_path)
    config.set("harness_launch_target", "desktop")  # 存量配置

    dialog = settings_mod.ModernSettingsDialog(config, include_ai=True)
    select = dialog.harness_launch_target_select
    desktop_idx = select.findData("desktop")
    assert desktop_idx >= 0, "项要保留（暂不可用 ≠ 永久不支持），只是置灰"
    assert select.isItemDisabled(desktop_idx)
    assert select.currentData() == "auto", "存量 desktop 必须回落到本平台合法的目标"

    select.showPopup()
    actions = {action.text(): action for action in select._popup.actions()}
    assert actions["桌面端界面"].isEnabled() is False, "置灰必须落到弹窗项（点不动）"
    assert actions["dsh web 界面"].isEnabled() is True
    select._popup.close()

    row = dialog.findChild(settings_mod.SettingRow, "settingRow_harness_launch_target")
    assert "暂不支持" in row.hint_label.text(), "置灰项必须在说明里给出原因"

    # 程序化硬选 desktop 也存不下去：保存处还有一道门
    select.setCurrentData("desktop")
    assert dialog._write_config() is True
    assert Config(tmp_path).get("harness_launch_target") != "desktop"
    dialog.close()
    app.processEvents()


def test_harness_target_desktop_selectable_on_supported_platform(tmp_path, monkeypatch):
    """对照（Windows）：桌面端项可选可存，说明里不出现「暂不支持」（别把门焊死）。"""
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    monkeypatch.setattr(settings_mod.harness_mod, "DESKTOP_SUPPORTED", True)

    dialog = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=True)
    select = dialog.harness_launch_target_select
    assert not select.isItemDisabled(select.findData("desktop"))
    row = dialog.findChild(settings_mod.SettingRow, "settingRow_harness_launch_target")
    assert "暂不支持" not in row.hint_label.text()

    select.setCurrentData("desktop")
    assert dialog._write_config() is True
    assert Config(tmp_path).get("harness_launch_target") == "desktop"
    dialog.close()
    app.processEvents()


def test_harness_target_offers_service_item_and_auto_says_headless(tmp_path, monkeypatch):
    """自启目标：新增「仅 dsh 服务」项；auto 措辞改成「仅服务，不开界面」。

    语义收紧（2026-10，产品口径）：自启的本意只是把 dsh 服务拉起来——不开浏览器、
    不开桌面端界面；要界面必须自己选 web / 桌面端。旧措辞「自动（桌面端优先）」
    描述的是被撤掉的旧语义（装上桌面端就拐 desktop），留着就是骗用户。
    """
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    monkeypatch.setattr(settings_mod.harness_mod, "DESKTOP_SUPPORTED", True)

    dialog = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=True)
    select = dialog.harness_launch_target_select

    assert select.itemText(0) == "自动（仅服务，不开界面）", "auto 项不得再写「桌面端优先」"
    assert select.currentData() == "auto", "默认仍是 auto"
    service_idx = select.findData("service")
    assert service_idx == 1, "「仅 dsh 服务」排在 auto 之后"
    assert select.itemText(service_idx) == "仅 dsh 服务（不开界面）"
    assert not select.isItemDisabled(service_idx)
    # web / desktop 两项不动
    assert select.itemText(select.findData("web")) == "dsh web 界面"
    assert select.itemText(select.findData("desktop")) == "桌面端界面"

    row = dialog.findChild(settings_mod.SettingRow, "settingRow_harness_launch_target")
    hint = row.hint_label.text()
    # 2026-10-07 第一刀：定稿把重复表述「（不开浏览器、不开桌面端界面）」收敛成
    # 「不打开界面」，语义断言不变（auto = 只起服务、不进界面）。
    assert "自动" in hint and "不打开界面" in hint, "说明里必须写清 auto = 只起服务不进界面"

    select.setCurrentData("service")
    assert dialog._write_config() is True
    assert Config(tmp_path).get("harness_launch_target") == "service", "service 必须能落盘"
    dialog.close()
    app.processEvents()


def test_harness_target_hint_helper_keeps_both_platform_copies_verbatim():
    """自启目标说明的逐字文案住在 ``settings_pet_controls.harness_target_hint``。

    搬迁原因（2026-10-10）：``modern_settings_dialog`` 撞了行数预算，两句纯文案
    选择挪到 ``settings_pet_controls``（``stream_capture_hint`` 同一先例）。两支
    共用首句与末句，差别只在桌面端能不能自动拉起——本用例逐字钉住，搬迁不许
    顺手改字（改字会同时骗过「暂不支持」的存在性断言）。
    """
    from pet.settings_pet_controls import (
        HARNESS_TARGET_HINT_DESKTOP,
        HARNESS_TARGET_HINT_UNSUPPORTED,
        harness_target_hint,
    )

    assert harness_target_hint(True) == HARNESS_TARGET_HINT_DESKTOP
    assert harness_target_hint(False) == HARNESS_TARGET_HINT_UNSUPPORTED
    for hint in (HARNESS_TARGET_HINT_DESKTOP, HARNESS_TARGET_HINT_UNSUPPORTED):
        assert hint.startswith(
            "「自动」和「仅 dsh 服务」都会只拉起 dsh 后台服务（不打开界面）；"
        ), "auto / service = 只起服务不进界面，这句两平台一字不差"
        assert "右键菜单的手动启动不受此设置影响。" in hint
        # 规范 §五：全局键必须写明作用域（本轮已修的「影响所有桌宠」不许回退）。
        assert "此设置影响所有桌宠。" in hint
    assert "也可以显式选择打开 web 界面或桌面端。" in HARNESS_TARGET_HINT_DESKTOP
    assert "暂不支持" not in HARNESS_TARGET_HINT_DESKTOP, "支持平台上不许出现「暂不支持」"
    assert "本平台暂不支持自动拉起桌面端（本版本仅 Windows）" in HARNESS_TARGET_HINT_UNSUPPORTED
    assert "想在自启时进界面只能显式选「dsh web 界面」。" in HARNESS_TARGET_HINT_UNSUPPORTED
    assert "也可以显式选择打开 web 界面或桌面端。" not in HARNESS_TARGET_HINT_UNSUPPORTED


@pytest.mark.parametrize("desktop_available", [True, False])
def test_harness_target_row_hint_is_the_helper_output(tmp_path, monkeypatch, desktop_available):
    """调用路径：真实设置页那一行的 hint 必须等于 helper 对应分支的逐字文案。

    只测 helper 会漏掉「接线断了」（搬迁后 dialog 忘了调用它）；只测行会漏掉
    另一分支。此处两个平台能力都钉一遍，取行文本时剥掉可能被追加的「默认：」小字。
    """
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config
    from pet.settings_pet_controls import harness_target_hint

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    monkeypatch.setattr(settings_mod.harness_mod, "DESKTOP_SUPPORTED", desktop_available)

    dialog = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=True)
    try:
        row = dialog.findChild(settings_mod.SettingRow, "settingRow_harness_launch_target")
        assert row is not None, "自启目标行必须在"
        rendered = row.hint_label.text().split("默认：", 1)[0].rstrip("\n")
        # 作用域那句已从说明末尾拆成行内独立标注（见 SettingRow.set_scope_note）；
        # 两者拼起来仍必须逐字等于 helper 的输出——文案来源没有第二份。
        assert rendered + row.scope_note.text() == harness_target_hint(desktop_available)
        assert row.scope_note.text() == "此设置影响所有桌宠。"
    finally:
        dialog.close()
        app.processEvents()


def test_harness_target_select_width_fits_its_longest_item(tmp_path, monkeypatch):
    """下拉宽度必须装得下最长项文案——自绘文本区没有省略号，窄了直接裁字。

    回归（本轮）：控件宽度写死 190px，文本区只有 ``190-34=156px``，而新增的
    「仅 dsh 服务（不开界面）」在默认字体下要 182px → 用户看到的是被截断的半句话。
    """
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)

    dialog = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=True)
    select = dialog.harness_launch_target_select

    assert select.width() >= select.suggestedWidth(), (
        f"控件宽 {select.width()}px 装不下最长项（需 {select.suggestedWidth()}px）"
    )
    dialog.close()
    app.processEvents()


def test_click_sound_path_is_linked_to_enable_toggle_and_persisted(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    config = Config(tmp_path)
    dialog = settings_mod.ModernSettingsDialog(config, include_ai=True)
    row = dialog.findChild(
        settings_mod.SettingRow, "settingRow_click_sound_pack"
    )
    assert row is not None
    # 默认开启 → 音效包行可见
    assert dialog.click_sound_check.isChecked()
    assert not row.isHidden()
    dialog.click_sound_check.setChecked(False)
    assert row.isHidden()
    dialog.click_sound_check.setChecked(True)
    assert not row.isHidden()
    dialog.click_sound_picker.set_pack({"kind": "file", "id": "custom", "path": "/tmp/my-click.wav"})
    dialog._save()

    # #271：空身份会话按 slot0 分流，音效包是每宠键——真值在 config-slot-0.json，
    # 传入的空身份对象读的是主配置，从档案读回。
    from pet import pet_profiles

    assert pet_profiles.read_slot_profile(config.dir, 0).value("click_sound_pack") == {
        "kind": "file", "id": "custom", "path": "/tmp/my-click.wav"}
    dialog.close()
    app.processEvents()


def test_click_sound_path_row_hidden_initially_when_toggle_disabled(tmp_path, monkeypatch):
    """点击音效未启用时，音效包行初始就应隐藏（此前初始同步在 UI 构建前，
    findChild 找不到行导致初始状态错误显示）。"""
    import pet.modern_settings_dialog as settings_mod
    from PySide6.QtWidgets import QApplication

    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    # 本用例只验证音效包行初始隐藏，不验证音频预热；Windows headless CI 上
    # QSoundEffect 异步加载的 processEvents 可能 access violation，因此跳过预热。
    monkeypatch.setattr(
        settings_mod,
        "warm_click_sound_effects",
        lambda *args, **kwargs: None,
    )
    config = Config(tmp_path)
    # #271：空身份会话按 slot0 分流，每宠键的真值在 config-slot-0.json——
    # 内存预置对话框读不到，必须落盘（先建主配置/落种档案，再写该槽偏好）。
    config.save()
    from pet import pet_profiles

    assert pet_profiles.write_profile_settings(
        config.dir, 0, {"click_sound_enabled": False}).ok
    dialog = settings_mod.ModernSettingsDialog(config, include_ai=True)
    row = dialog.findChild(
        settings_mod.SettingRow, "settingRow_click_sound_pack"
    )
    assert row is not None
    assert not dialog.click_sound_check.isChecked()
    assert row.isHidden()
    dialog.close()
    app.processEvents()


def test_click_sound_path_row_sits_directly_below_toggle(tmp_path, monkeypatch):
    """音效包行必须紧贴点击音效行下方（此前 click_balance 插入 index 1 把
    音效包行挤到第三位）；2026-09-17 定稿口径下这两行仍在「互动 · 点击反馈」。"""
    import pet.modern_settings_dialog as settings_mod
    from PySide6.QtWidgets import QApplication, QLabel

    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    config = Config(tmp_path)
    dialog = settings_mod.ModernSettingsDialog(config, include_ai=True)
    section = next(
        s for s in dialog.findChildren(settings_mod.SettingsSection)
        if s.findChild(QLabel, "sectionTitle") is not None
        and s.findChild(QLabel, "sectionTitle").text() == "点击反馈"
    )
    card = section.findChild(settings_mod.SettingsCard)
    assert card is not None
    names = [row.objectName() for row in card.rows]
    assert names[0] == "settingRow_click_sound"
    assert names[1] == "settingRow_click_sound_pack"
    dialog.close()
    app.processEvents()


def test_settings_dialog_position_avoids_pet_window(tmp_path, monkeypatch):
    """设置窗口激活时不应遮挡桌宠：打开时移动到不与桌宠相交的位置。"""
    import pet.modern_settings_dialog as settings_mod
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QApplication, QWidget

    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    pet = QWidget()
    pet.setGeometry(QRect(100, 100, 200, 200))
    pet.show()
    app.processEvents()
    config = Config(tmp_path)
    dialog = settings_mod.ModernSettingsDialog(config, pet, include_ai=True)
    # 真实最小尺寸 720x500 在 offscreen 800x600 屏幕上无处可避，放宽以测试避让逻辑
    dialog.setMinimumSize(360, 260)
    dialog.resize(420, 320)
    dialog.show()
    app.processEvents()
    try:
        assert not dialog.geometry().intersects(pet.geometry())
    finally:
        dialog.close()
        pet.close()
        app.processEvents()


def test_menu_font_select_lists_system_fonts(tmp_path, monkeypatch):
    """UI 字体设置项应枚举系统可用字体，而不是硬编码少数几个。"""
    import pet.modern_settings_dialog as settings_mod
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication

    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    config = Config(tmp_path)
    dialog = settings_mod.ModernSettingsDialog(config, include_ai=True)
    select = dialog.menu_font_select
    # 字体枚举延迟到事件循环空闲时填充（避免阻塞窗口打开）。
    # 测试直接同步触发填充，不等待 QTimer：QTest.qWait 的嵌套事件循环
    # 会触发 GC 析构残留测试对象，在 macOS/Windows CI 上均可致进程
    # abort（setParent_helper / QPA 平台层），同步调用则完全绕开。
    dialog._populate_menu_fonts()
    available = {select.itemData(i) for i in range(select.count())}
    system_families = set(QFontDatabase.families())
    assert "system" in available
    custom = available - {"system"}
    # 所有可选字体必须来自系统字体表
    assert custom <= system_families
    # 必须真正列出系统字体（而非只剩硬编码几项）；无系统字体的平台
    # （如 Windows offscreen CI）跳过数量断言
    if system_families:
        assert len(custom) >= 4
    dialog.close()
    app.processEvents()


def test_settings_first_paint_does_not_enumerate_system_fonts(tmp_path, monkeypatch):
    """系统字体枚举可能在 Windows 阻塞数秒，只能在用户展开字体选择器时执行。"""
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    calls = []
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    monkeypatch.setattr(
        settings_mod,
        "_system_font_families",
        lambda: calls.append("enumerated") or ("Regression Test Font",),
    )

    dialog = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=False)
    dialog.show()
    app.processEvents()
    assert calls == [], "首次显示设置窗口时不应枚举全部系统字体"

    dialog.menu_font_select.showPopup()
    assert calls == ["enumerated"]
    dialog.menu_font_select._popup.close()
    dialog.close()
    app.processEvents()


def test_settings_save_preserves_custom_font_before_selector_is_opened(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    config = Config(tmp_path)
    appearance = dict(config.get("context_menu_appearance"))
    appearance["ui_font"] = "Regression Custom Font"
    config.set("context_menu_appearance", appearance)
    # #271：空身份会话按 slot0 分流，全局键读路由视图另建的主配置（初值只取盘）
    # ——预置必须落盘。
    assert config.save() is True

    dialog = settings_mod.ModernSettingsDialog(config, include_ai=False)
    assert dialog._menu_fonts_populated is False
    assert dialog.menu_font_select.currentData() == "Regression Custom Font"
    dialog._save()
    config.reload()  # 写盘走另一份主配置对象，传入对象需重读才可见
    assert config.get("context_menu_appearance")["ui_font"] == "Regression Custom Font"
    app.processEvents()


def test_modern_select_reuses_one_popup_without_accumulating_children(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMenu

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=False)
    select = dialog.menu_theme_select

    popup_ids = []
    for _ in range(3):
        select.showPopup()
        app.processEvents()
        popup_ids.append(id(select._popup))
        select._popup.close()
        app.processEvents()
        assert len(select.findChildren(QMenu)) == 1

    assert len(set(popup_ids)) == 1

    dialog.close()
    app.processEvents()


def test_dock_icon_row_platform_gated(tmp_path, monkeypatch):
    """「显示 Dock 图标」是 macOS 专属选项，其他平台不应显示。"""
    import sys

    import pet.modern_settings_dialog as settings_mod
    from PySide6.QtWidgets import QApplication

    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    monkeypatch.setattr(sys, "platform", "win32")
    config = Config(tmp_path)
    dialog = settings_mod.ModernSettingsDialog(config, include_ai=True)
    assert dialog.findChild(settings_mod.SettingRow, "settingRow_dock_icon") is None
    dialog.close()
    monkeypatch.setattr(sys, "platform", "darwin")
    dialog2 = settings_mod.ModernSettingsDialog(config, include_ai=True)
    assert dialog2.findChild(settings_mod.SettingRow, "settingRow_dock_icon") is not None
    dialog2.close()
    app.processEvents()


@pytest.mark.skipif(
    os.name == "nt",
    reason="Windows 无 time.tzset()，无法在测试进程内切换时区（CI runner 为 UTC）",
)
def test_new_session_title_converts_utc_creation_time_to_local(monkeypatch):
    import os
    import time

    from pet.chat.models import ChatSession
    from pet.chat.widgets import _short_title

    previous_tz = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    if hasattr(time, "tzset"):
        time.tzset()
    try:
        created = datetime(2026, 8, 27, 1, 5, tzinfo=timezone.utc).isoformat()
        session = ChatSession("id", "shenshen", "provider", "", created_at=created)
        assert _short_title(session) == "新会话 · 09:05"
    finally:
        if previous_tz is None:
            monkeypatch.delenv("TZ", raising=False)
        else:
            monkeypatch.setenv("TZ", previous_tz)
        if hasattr(time, "tzset"):
            time.tzset()


def test_chat_window_left_edge_drag_resizes(tmp_path):
    """无边框聊天窗口应支持按住边缘拖拽缩放（此前无任何边缘 resize 处理）。"""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    win = ChatWindow(Config(tmp_path), "shenshen")
    win.resize(960, 700)
    before_w = win.width()

    def mouse(kind, x, y, button):
        # 旧 5 参构造不写 globalPosition（遗留默认值），必须显式传全局坐标
        return QMouseEvent(
            kind, QPointF(x, y), QPointF(x, y), button, button,
            Qt.KeyboardModifier.NoModifier,
        )

    before_x = win.x()
    win.mousePressEvent(mouse(
        QEvent.Type.MouseButtonPress, 2, 350, Qt.MouseButton.LeftButton
    ))
    win.mouseMoveEvent(mouse(
        QEvent.Type.MouseMove, 140, 350, Qt.MouseButton.LeftButton
    ))
    # 左边缘跟随鼠标右移：窗口变窄（右边缘锚定），x 坐标右移
    assert win.x() > before_x, "按住左边缘右拖时左边缘应跟随鼠标右移"
    assert win.width() < before_w, "按住左边缘右拖应使窗口变窄（右边缘锚定）"
    win.mouseReleaseEvent(mouse(
        QEvent.Type.MouseButtonRelease, 140, 350, Qt.MouseButton.LeftButton
    ))
    win.close()
    app.processEvents()


def test_chat_window_edge_resize_clamps_position_with_size(tmp_path):
    """边缘拖拽触达最小尺寸时，位置应随尺寸回退（锚定对侧边缘），
    窗口不能被推出屏幕（此前 setGeometry 仅 clamp 尺寸不回退位置）。"""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    win = ChatWindow(Config(tmp_path), "shenshen")
    win.resize(960, 700)
    min_w, min_h = win.minimumWidth(), win.minimumHeight()
    start_right = win.x() + win.width()
    start_bottom = win.y() + win.height()

    def mouse(kind, x, y, button):
        return QMouseEvent(
            kind, QPointF(x, y), QPointF(x, y), button, button,
            Qt.KeyboardModifier.NoModifier,
        )

    # 左边缘右拖 1000px（远超最小宽度）→ 右边缘锚定，x = 原右边缘 - 最小宽度
    win.mousePressEvent(mouse(
        QEvent.Type.MouseButtonPress, 2, 350, Qt.MouseButton.LeftButton
    ))
    win.mouseMoveEvent(mouse(
        QEvent.Type.MouseMove, 1002, 350, Qt.MouseButton.LeftButton
    ))
    assert win.width() == min_w
    # offscreen 平台对窗口几何有 1px 微调，右边缘不得超出原位置（此前会偏出 600px）
    assert abs((win.x() + win.width()) - start_right) <= 1, (
        f"拖到最小宽度时右边缘应锚定在 {start_right}，实际 {win.x() + win.width()}"
    )
    win.mouseReleaseEvent(mouse(
        QEvent.Type.MouseButtonRelease, 1002, 350, Qt.MouseButton.LeftButton
    ))

    # 顶部上拖 2000px → 高度触达最大尺寸上限，底边缘锚定（此前窗口整体移出屏幕顶部）
    max_h = win.maximumHeight()
    win.resize(960, 700)
    win.mousePressEvent(mouse(
        QEvent.Type.MouseButtonPress, 480, 2, Qt.MouseButton.LeftButton
    ))
    win.mouseMoveEvent(mouse(
        QEvent.Type.MouseMove, 480, -1998, Qt.MouseButton.LeftButton
    ))
    assert win.height() == max_h
    assert abs((win.y() + win.height()) - start_bottom) <= 1, (
        f"拖到最大高度时底边缘应锚定在 {start_bottom}，实际 {win.y() + win.height()}"
    )
    win.mouseReleaseEvent(mouse(
        QEvent.Type.MouseButtonRelease, 480, -1998, Qt.MouseButton.LeftButton
    ))
    win.close()
    app.processEvents()


def test_chat_window_edge_hover_shows_resize_cursor(tmp_path):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QHoverEvent
    from PySide6.QtWidgets import QApplication

    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    win = ChatWindow(Config(tmp_path), "shenshen")
    win.resize(960, 700)

    def hover(x: float, y: float) -> None:
        win.event(QHoverEvent(QEvent.Type.HoverMove, QPointF(x, y), QPointF(x, y)))

    # 悬停在窗口右边缘时应显示水平缩放光标
    hover(win.width() - 2, 350)
    assert win.cursor().shape() == Qt.CursorShape.SizeHorCursor, (
        "悬停在窗口右边缘时应显示水平缩放光标"
    )
    # 移入窗口内部应立即恢复箭头（回归：从窗口外进入后光标卡在缩放双箭头）
    hover(win.width() // 2, 350)
    assert win.cursor().shape() == Qt.CursorShape.ArrowCursor, (
        "离开边缘进入窗口内部应恢复箭头光标"
    )
    # 离开窗口恢复箭头
    win.event(QHoverEvent(QEvent.Type.HoverLeave, QPointF(1, 1), QPointF(1, 1)))
    assert win.cursor().shape() == Qt.CursorShape.ArrowCursor
    win.close()
    app.processEvents()


def test_ojingjing_entry_hover_survives_widget_children(monkeypatch):
    """彩蛋项 hover 不应依赖 enter 事件：菜单弹出时鼠标已在项上（无 enter）
    应合成高亮；鼠标移入子 widget 触发的 leave 不应丢高亮。"""
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication, QMenu

    import pet.context_menus.fun_entry as fun_entry
    from pet.context_menus.fun_entry import OjingjingMenuEntry

    app = QApplication.instance() or QApplication([])
    menu = QMenu()
    entry = OjingjingMenuEntry(menu, {"title": "厉害了我的鲸", "hint": "请点击"})
    # offscreen QPA 的 QCursor.setPos 不生效（光标位置由平台管理）。
    # 模拟光标悬在项内时用项自身的全局坐标（mapToGlobal），而不是绝对屏幕点：
    # 菜单实际弹出位置受任务栏/DPI/多屏约束可能不在 (0, 0)，绝对点一旦落在
    # 项外，showEvent 合成 _hovered=False 反而是正确行为，测试就变成环境耦合。
    # 几何相对点保证任何 QPA/窗口管理器下合成高亮都能命中项内，验证的是
    # “不依赖 enter 事件”本身，而非菜单弹出的绝对位置。
    monkeypatch.setattr(
        fun_entry.QCursor,
        "pos",
        staticmethod(lambda: entry.mapToGlobal(QPoint(10, 10))),
    )
    # 用 popup(0, 0) 而不是 show()：Windows QPA 上 QMenu.show() 跟随真实系统
    # 光标落点（Qt C++ 内部调用不受 Python 层 QCursor.pos mock 影响）。
    menu.popup(QPoint(0, 0))
    app.processEvents()
    try:
        # 1. 菜单弹出时鼠标已悬在项上：showEvent 按光标位置合成高亮
        assert entry._hovered, "鼠标已位于项上时应合成初始高亮（无需 enter 事件）"
        # 2. 鼠标移入子 widget 触发的 leave：光标仍在项内，高亮保持
        entry.leaveEvent(QEvent(QEvent.Type.Leave))
        assert entry._hovered, "光标仍在项内时 leave 不应清除高亮"
        # 3. 跨窗口移动丢失 enter 时，mouseMove 兜底恢复高亮
        entry._hovered = False
        entry.mouseMoveEvent(QMouseEvent(
            QEvent.Type.MouseMove, QPointF(20, 20), QPointF(20, 20),
            Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        ))
        assert entry._hovered, "鼠标移动应恢复高亮"
    finally:
        entry.close()
        menu.close()
        app.processEvents()


def test_windows_ojingjing_children_do_not_intercept_hover():
    """Windows 按子窗口做命中测试；首项内容必须把鼠标事件透传给背景控件。"""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMenu, QWidget

    from pet.context_menus.fun_entry import OjingjingMenuEntry

    app = QApplication.instance() or QApplication([])
    menu = QMenu()
    entry = OjingjingMenuEntry(menu)
    children = [
        entry.findChild(QWidget, "ojingjingAvatar"),
        entry.findChild(QWidget, "ojingjingTitle"),
        entry.findChild(QWidget, "ojingjingClickAccessory"),
    ]
    assert all(child is not None for child in children)
    assert all(
        child.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        for child in children
    ), "头像、标题和提示不应截获 Windows hover 事件"
    entry.close()
    menu.close()
    app.processEvents()


def test_macos_hide_pet_respects_dock_icon_hidden_preference(tmp_path, monkeypatch):
    """macOS 关闭「显示 Dock 图标」后，隐藏桌宠不得再临时唤回 Dock 图标。

    issue #74：托盘（菜单栏）菜单已承担恢复入口（显示/隐藏、鼠标穿透、
    桌宠设置），因此隐藏桌宠应尊重用户关闭 Dock 图标的偏好，不再把
    activation policy 临时改回 Regular，也不写回配置。
    """
    import sys

    from unittest import mock

    from PySide6.QtWidgets import QWidget

    import pet.app
    from pet.config import Config
    from pet.window import PetWindow

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(pet.app, "_mac_set_dock_icon_visible", mock.Mock())

    for pref in (False, True):
        with mock.patch.object(QWidget, "hide") as mock_hide:
            win = PetWindow.__new__(PetWindow)
            win.cfg = Config(tmp_path)
            win.cfg.set("show_dock_icon", pref)
            PetWindow.hide(win, notify=False)
            mock_hide.assert_called_once()
            assert win.cfg.get("show_dock_icon") is pref
            pet.app._mac_set_dock_icon_visible.assert_not_called()


def test_windows_settings_has_no_orphan_macos_dock_toggle(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.sys, "platform", "win32")
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    config = Config(tmp_path)
    config.set("show_dock_icon", False)
    dialog = settings_mod.ModernSettingsDialog(config, include_ai=False)

    assert dialog.dock_icon_check is None
    assert not any(
        isinstance(child, settings_mod.ToggleSwitch)
        for child in dialog.children()
    ), "所有开关都必须被设置行接管，不能游离在窗口左上角"
    assert dialog.findChild(
        settings_mod.SettingRow, "settingRow_auto_hide_fullscreen"
    ) is not None
    assert dialog.findChild(
        settings_mod.SettingRow, "settingRow_stream_capture"
    ) is not None

    dialog._save()
    assert config.get("show_dock_icon") is False
    app.processEvents()


# Linux 回归：Windows 专属开关不得在非 Windows 平台创建后游离到设置窗左上角。
def test_linux_settings_has_no_orphan_windows_cursor_passthrough_toggle(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.sys, "platform", "linux")
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=False)

    assert dialog.cursor_hidden_passthrough_check is None
    assert not any(
        isinstance(child, settings_mod.ToggleSwitch)
        for child in dialog.children()
    ), "所有开关都必须被设置行接管，不能游离在窗口左上角"

    dialog.close()
    app.processEvents()


def test_hide_pet_notifies_and_dock_click_restores(tmp_path, monkeypatch):
    """用户主动隐藏：弹托盘提示 + macOS 点击 Dock 图标恢复桌宠。"""
    import sys

    from unittest import mock

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QWidget

    import pet.window as window_mod
    from pet.config import Config

    monkeypatch.setattr(sys, "platform", "darwin")
    app = QApplication.instance() or QApplication([])
    win = window_mod.PetWindow.__new__(window_mod.PetWindow)
    win.cfg = Config(tmp_path)
    notified = []
    win.on_hidden = lambda: notified.append(True)

    with mock.patch.object(QWidget, "hide"), mock.patch.object(
        window_mod.PetWindow, "show"
    ) as mock_show, mock.patch.object(app, "applicationStateChanged") as m_sig:
        window_mod.PetWindow.hide(win)
        assert notified, "用户主动隐藏应触发托盘提示回调"
        # 隐藏后 arm Dock 点击恢复监听
        m_sig.connect.assert_called_once()
        # 点击 Dock 图标 → 应用激活 → 自动恢复桌宠
        window_mod.PetWindow._restore_on_dock_reactivate(
            win, Qt.ApplicationState.ApplicationActive
        )
        mock_show.assert_called_once()
        # 一次性监听：再次激活不再恢复
        mock_show.reset_mock()
        window_mod.PetWindow._restore_on_dock_reactivate(
            win, Qt.ApplicationState.ApplicationActive
        )
        mock_show.assert_not_called()


def test_hide_pet_internal_replacement_skips_notify(tmp_path, monkeypatch):
    """角色切换等内部替换隐藏：不弹提示、不 arm Dock 恢复监听。"""
    import sys

    from unittest import mock

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QWidget

    import pet.window as window_mod
    from pet.config import Config

    monkeypatch.setattr(sys, "platform", "darwin")
    app = QApplication.instance() or QApplication([])
    win = window_mod.PetWindow.__new__(window_mod.PetWindow)
    win.cfg = Config(tmp_path)
    win.on_hidden = lambda: pytest.fail("内部替换不应弹提示")

    with mock.patch.object(QWidget, "hide"), mock.patch.object(
        window_mod.PetWindow, "show"
    ) as mock_show, mock.patch.object(app, "applicationStateChanged") as m_sig:
        window_mod.PetWindow.hide(win, notify=False)
        m_sig.connect.assert_not_called()
        window_mod.PetWindow._restore_on_dock_reactivate(
            win, Qt.ApplicationState.ApplicationActive
        )
        mock_show.assert_not_called()


def test_animation_icon_applier_updates_action_and_cleans_worker():
    """图标解码完成回调须经 GUI 线程槽更新 QAction，并清理 worker 记录。

    回归背景：ready 信号此前直连普通闭包，setIcon/update 在 QThreadPool
    工作线程执行（Qt 未定义行为）；现经 _AnimationIconApplier（挂在
    submenu 下、随菜单生命周期）队列投递回 GUI 线程。
    """
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication, QMenu

    from pet.context_menus.shared import _AnimationIconApplier

    app = QApplication.instance() or QApplication([])
    menu = QMenu()
    submenu = QMenu(menu)
    submenu._animation_icon_workers = []
    action = submenu.addAction("测试动画")
    pump_calls = []
    worker = object()

    # 无图（解码失败/空帧）：不 setIcon，但必须移除 worker 并继续泵任务
    applier = _AnimationIconApplier(
        submenu, action, worker, lambda: pump_calls.append(1), parent=submenu
    )
    applier.on_ready(None)
    assert submenu._animation_icon_workers == []
    assert pump_calls == [1]

    # 有效图：菜单不可见（offscreen）时更新图标
    image = QImage(16, 16, QImage.Format.Format_ARGB32)
    image.fill(0xFF3366FF)
    submenu._animation_icon_workers.append(worker)
    applier2 = _AnimationIconApplier(
        submenu, action, worker, lambda: pump_calls.append(2), parent=submenu
    )
    applier2.on_ready(image)
    assert submenu._animation_icon_workers == []
    assert pump_calls == [1, 2]
    assert not action.icon().isNull()

    # 菜单已销毁：槽必须静默返回，不访问已删 C++ 对象
    menu.deleteLater()
    app.processEvents()


def test_build_scripts_bundle_menu_templates_and_chat_styles():
    """三平台打包脚本必须包含菜单模板与聊天样式资源（防漏打包回归）。

    回归背景：Linux workflow 曾漏掉 pet/menu_templates（右键菜单在冻结版
    打不开）与 legacy/modern_styles.qss（聊天窗无样式）。

    构建定义统一在本地脚本（scripts/build_*.sh / build_onedir.ps1），
    CI workflow 只调用脚本不再内联打包命令，故资源断言指向脚本而非 workflow。
    """
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    linux = (repo / "scripts" / "build_linux.sh").read_text(encoding="utf-8")
    macos = (repo / "scripts" / "build_macos.sh").read_text(encoding="utf-8")
    windows = (repo / "scripts" / "build_onedir.ps1").read_text(encoding="utf-8")

    for name, text in (("build_linux.sh", linux), ("build_macos.sh", macos), ("build_onedir.ps1", windows)):
        assert "menu_templates" in text, f"{name} 必须打包 pet/menu_templates"
        assert "legacy_styles.qss" in text, f"{name} 必须打包 legacy_styles.qss"
        assert "modern_styles.qss" in text, f"{name} 必须打包 modern_styles.qss"
    # 兜底：默认菜单布局 JSON 必须能按资源方式读出来（右键菜单的唯一布局来源；
    # 旧版菜单退役后 pet/menu_templates 下只剩它）
    from pet.menu_layout import load_default_menu_layout
    assert load_default_menu_layout()["layout_id"] == "modern-default-v1"


def test_store_fun_asset_keeps_bundled_paths_relative(tmp_path):
    """内置 assets 内的路径必须持久化为相对值（portable），外部文件保留绝对路径。

    回归背景：设置对话框把默认相对路径固化成安装目录绝对路径，目录移动/
    自更新后彩蛋弹窗失效。
    """
    from pet.fun_image_popup import bundled_assets_root, store_fun_asset

    default = bundled_assets_root() / "big_blue_fat_fish" / "ojingjing.jpg"
    # 绝对路径指向内置 assets → 归一化为相对值
    stored = store_fun_asset(str(default), default)
    assert stored == "assets/big_blue_fat_fish/ojingjing.jpg"
    # 相对值原样保留
    assert store_fun_asset("assets/big_blue_fat_fish", default) == "assets/big_blue_fat_fish"
    # 空值回退默认
    assert store_fun_asset("", default) == str(default)
    # 外部文件 → 绝对路径保留
    external = tmp_path / "custom" / "egg.jpg"
    external.parent.mkdir(parents=True)
    external.write_bytes(b"x")
    assert store_fun_asset(str(external), default) == str(external)


def test_popup_image_paths_survives_missing_directory(tmp_path):
    """彩蛋图片目录不存在时回退默认彩蛋池（而非抛异常或空列表）。"""
    from pet.fun_image_popup import popup_image_paths

    paths = popup_image_paths(tmp_path / "does-not-exist")
    assert paths, "缺失目录应回退默认彩蛋图片池"
    assert all(path.is_file() for path in paths)


def test_config_normalizes_polluted_absolute_easter_egg_paths(tmp_path):
    """旧配置里已固化的内置资产绝对路径在加载时归一化回相对值。"""
    from pet.fun_image_popup import bundled_assets_root
    from pet.config import Config

    bundled = bundled_assets_root()
    (tmp_path / "config.json").write_text(
        '{"version": 4, "menu_easter_egg": {"enabled": true, "avatar": "'
        + str(bundled / "big_blue_fat_fish" / "ojingjing.jpg").replace("\\", "/")
        + '", "image_dir": "'
        + str(bundled / "big_blue_fat_fish").replace("\\", "/")
        + '"}}',
        encoding="utf-8",
    )
    cfg = Config(tmp_path)
    egg = cfg.get("menu_easter_egg")
    assert egg["avatar"] == "assets/big_blue_fat_fish/ojingjing.jpg"
    assert egg["image_dir"] == "assets/big_blue_fat_fish"


def test_easter_egg_activate_defers_until_menu_closes():
    """彩蛋点击在菜单可见时排入 deferred callbacks，菜单关闭后才开窗。

    回归背景：macOS 原生 NSMenu 跟踪循环中直接开窗被 AppKit 抑制（首点无效）。
    """
    import time

    from PySide6.QtWidgets import QApplication, QMenu

    import pet.context_menus.fun_entry as fun_entry_mod
    from pet.context_menus.fun_entry import OjingjingMenuEntry

    app = QApplication.instance() or QApplication([])
    menu = QMenu()
    entry = OjingjingMenuEntry(menu, {"title": "厉害了我的鲸"})
    opened = []
    monkeypatch = __import__("pytest").MonkeyPatch()
    monkeypatch.setattr(fun_entry_mod, "open_ojingjing_window", lambda config: opened.append(config))
    try:
        # 菜单不可见：走 singleShot 延迟
        entry._activate()
        deadline = time.time() + 1
        while not opened and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert opened, "不可见菜单点击应立即排队开窗"
        opened.clear()

        # 菜单可见：排入 deferred callbacks 并关闭菜单
        menu.show()
        app.processEvents()
        entry._activate()
        callbacks = list(getattr(menu, "_deferred_callbacks", ()))
        assert callbacks, "可见菜单点击必须排入 deferred callbacks"
        assert not menu.isVisible(), "点击后菜单应被关闭"
        callbacks[0]()
        assert opened, "回调应打开彩蛋窗口"
    finally:
        monkeypatch.undo()
        menu.close()
        app.processEvents()


def test_modern_settings_save_writes_autostart_wanted(tmp_path, monkeypatch):
    """新版设置保存必须记录 autostart_wanted（启动自检提醒依赖它）。"""
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    # 不碰真实系统自启：CI runner 上写入失败会触发 QMessageBox.warning，
    # offscreen 下无人交互（实测 access violation / 挂起）。
    monkeypatch.setattr(settings_mod.autostart_mod, "set_enabled", lambda enabled: True)
    config = Config(tmp_path)
    assert config.get("autostart_wanted") is False
    dialog = settings_mod.ModernSettingsDialog(config, include_ai=True)
    dialog.autostart_check.setChecked(True)
    dialog._save()
    config.reload()  # #271：写盘走路由视图另建的主配置，传入对象需重读才可见
    assert config.get("autostart_wanted") is True
    dialog.close()
    app.processEvents()


def test_menu_autostart_persists_wanted_flag(tmp_path, monkeypatch):
    """右键菜单的「开机自启」必须记录 autostart_wanted（启动自检提醒依赖它）。

    注册表（registry 的 autostart 项）、设置页与托盘都写这个键；共享建造器被
    直调时漏传 pet 就会只写系统登录项、不写配置——``pet/app.py`` 的自检
    （``autostart_wanted`` 为真但系统项被安全软件清掉 → 提醒用户重新启用）
    因此永远看不到用户勾过。

    真 QMenu + 真 Config + 真 PetWindow 建菜单，只把系统登录项边界（autostart
    模块）打桩；断言配置落盘，而不只是内存里的 Config 对象。
    """
    import json

    from PySide6.QtWidgets import QApplication, QMenu

    import pet.context_menus.shared as shared_mod
    import tests.test_context_menu_lifecycle as lc
    from pet.config import Config
    from pet.context_menu import populate_context_menu

    app = QApplication.instance() or QApplication([])
    applied: list[bool] = []
    monkeypatch.setattr(shared_mod.autostart_mod, "is_enabled", lambda: False)
    monkeypatch.setattr(shared_mod.autostart_mod, "set_enabled",
                        lambda enabled: applied.append(bool(enabled)) or True)
    config = Config(base=tmp_path)
    config.save()
    assert config.get("autostart_wanted") is False
    win = lc.PetWindow(lc.FakeLibrary(), config)
    menu = QMenu()
    try:
        populate_context_menu(menu, win)
        action = next(a for action in menu.actions()
                      if action.menu() is not None
                      for a in action.menu().actions() if a.text() == "开机自启")
        action.setChecked(True)
        assert applied == [True], "系统登录项照常写入"
        assert config.get("autostart_wanted") is True
        persisted = json.loads((config.dir / "config.json").read_text(encoding="utf-8"))
        assert persisted["autostart_wanted"] is True, "必须落盘（自检读的是盘上的值）"
    finally:
        menu.close()
        win.close()
        app.processEvents()


def test_modern_provisional_config_falls_back_to_keyring(tmp_path, monkeypatch):
    """测试连接未填 Key 时必须回退系统钥匙串，否则默认场景误报 401。"""
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)

    class FakeStore:
        def get(self, _ref):
            return "keyring-secret"

    dialog = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=True)
    monkeypatch.setattr(dialog.ai_page, "_secret_store_type", FakeStore)
    provisional = dialog.ai_page.provisional_config()
    assert provisional.api_key == "keyring-secret"
    dialog.close()
    app.processEvents()


def _answer_save_failure(monkeypatch, seen: list, *, discard: bool = False) -> None:
    """替掉保存失败框的 ``exec``，但框本体走**产品真实构造**（文案/按钮都是真的）。

    只有"用户点了哪个按钮"这一处可注入：``settings_close_flow.confirm_discard_unsaved``
    （conftest 里默认桩成"继续编辑"，这里换成取真框并读回文案的替身）。

    ``seen`` 里也收了一份标题，但**断言不许只看标题**：macOS 的 QMessageBox 走系统原生
    对话框，``windowTitle()`` 在那边恒为空（仓库既有结论与同族修法见
    ``tests/test_harness_lifecycle.py::test_confirm_dialog_text_names_pid_and_port``）。
    承载"用户看得见什么"的是 ``text`` / ``info``——标题只在 Windows/Linux 上读得到。
    """
    from pet import settings_close_flow

    def answer(parent, failure):
        box, _keep, discard_button = settings_close_flow.build_save_failure_box(parent, failure)
        try:
            seen.append({"failure": failure, "title": box.windowTitle(),
                         "text": box.text(), "info": box.informativeText(),
                         "discard": discard_button})
        finally:
            box.deleteLater()
        return discard

    monkeypatch.setattr(settings_close_flow, "confirm_discard_unsaved", answer)


def test_modern_settings_close_autosaves(tmp_path, monkeypatch):
    """直接关闭（X）新版设置也必须落盘，不能只靠「保存并退出」。

    回归背景：用户改完字体颜色/气泡方案后直接关窗，修改全部丢失。
    """
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    config = _slot0_config(tmp_path)
    dialog = settings_mod.ModernSettingsDialog(config, include_ai=True)
    dialog.bubble_style_select.setCurrentData("breath_bubble")
    dialog.light_background_picker.edit.setText("#123456")
    dialog.close()  # 不点「保存并退出」
    app.processEvents()
    assert config.get("self_talk_bubble_style") == "breath_bubble"
    assert config.get("context_menu_appearance", {}).get("light_background") == "#123456"
    # 重载磁盘验证（气泡方案是每宠键 → 走 slot0 档案；菜单外观是全局键 → 主配置）
    reloaded = _slot0_config(tmp_path)
    assert reloaded.get("self_talk_bubble_style") == "breath_bubble"


def test_modern_settings_escape_keeps_the_dialog_open_when_save_fails(tmp_path, monkeypatch):
    """保存失败时 Esc 与 X 同契约：默认（继续编辑）不关窗，草稿留在控件里。

    回归背景：``closeEvent``（X）一直有 ``if not self._write_config():
    event.ignore()``，而 ``reject()``（Esc / 关闭按钮）忽略返回值照常
    ``super().reject()``——磁盘写不进时按 Esc 会**静默丢草稿**：窗口关了、改动
    从没落盘，用户以为保存成功了。

    2026-10-10 实机 §4 之后失败提示换了实现：不再是一条 ``QMessageBox.warning``，而是
    ``pet/settings_close_flow`` 的「继续编辑（默认）/ 放弃未保存的改动并关闭」框——
    这条用例仍钉同一件事（失败不关窗、草稿还在、用户看得见），多钉一层：默认答案必须是
    "继续编辑"（误触不丢草稿）。
    """
    import shiboken6
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    seen: list = []
    _answer_save_failure(monkeypatch, seen)
    config = Config(tmp_path)
    config.path.mkdir(parents=True, exist_ok=True)   # 目标为目录 → os.replace 必失败
    dialog = settings_mod.ModernSettingsDialog(config, include_ai=False)
    dialog.bubble_style_select.setCurrentData("breath_bubble")
    finished: list = []
    dialog.finished.connect(lambda code: finished.append(int(code)))
    dialog.show()
    app.processEvents()
    try:
        QTest.keyClick(dialog, Qt.Key.Key_Escape)    # 真实 Esc 键事件
        app.processEvents()

        # 设置窗带 WA_DeleteOnClose：真被关掉时 C++ 对象当场销毁，isValid 立即可判
        assert finished == [], "保存失败时 Esc 不得关窗（草稿会丢）"
        assert shiboken6.isValid(dialog) and dialog.isVisible(), "窗口必须还在"
        assert dialog.bubble_style_select.currentData() == "breath_bubble", "草稿必须还在"
        assert seen and "配置未能写入磁盘" in seen[-1]["text"], (
            "必须给出可见的失败提示（macOS 取不到窗口标题，见本文件 _answer_save_failure 的注释）")
        assert seen[-1]["discard"].text() == "放弃未保存的改动并关闭", "出口必须可达且写明"
    finally:
        config.path.rmdir()                          # 恢复可写，收尾关窗
        if shiboken6.isValid(dialog):
            dialog.close()
        app.processEvents()


def test_modern_settings_escape_closes_when_save_succeeds(tmp_path, monkeypatch):
    """非回归：保存正常时 Esc 照旧关窗，并落盘。"""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialog

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    config = Config(tmp_path)
    dialog = settings_mod.ModernSettingsDialog(config, include_ai=False)
    dialog.bubble_style_select.setCurrentData("breath_bubble")
    finished: list = []
    dialog.finished.connect(lambda code: finished.append(int(code)))
    dialog.show()
    app.processEvents()

    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    app.processEvents()

    assert finished == [int(QDialog.DialogCode.Rejected)], "保存成功时 Esc 必须关窗"
    # #271：气泡样式是每宠键——真值在 config-slot-0.json，从档案读回。
    from pet import pet_profiles

    assert pet_profiles.read_slot_profile(config.dir, 0).value(
        "self_talk_bubble_style") == "breath_bubble", "关窗前必须落盘"


def test_settings_stylesheet_has_dark_overrides(monkeypatch):
    """深色系统下新版设置必须追加深色覆盖段（白底白字不可读问题）。"""
    from pet.modern_settings_dialog import _settings_stylesheet

    monkeypatch.setattr("pet.settings_theme_qss._system_dark", lambda: True)
    qss = _settings_stylesheet()
    assert "background: #202024" in qss
    assert "color: #e4e4e9" in qss
    monkeypatch.setattr("pet.settings_theme_qss._system_dark", lambda: False)
    qss_light = _settings_stylesheet()
    assert "background: #202024" not in qss_light
    # 浅色也必须显式给按钮补文字色（防深色 palette 白字）
    assert "QPushButton { color: #202020; }" in qss_light


def test_settings_window_uses_the_explicit_dark_appearance_on_a_light_system(
    tmp_path, monkeypatch,
):
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod, "_system_dark", lambda: False)
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    config = Config(tmp_path)
    config.set("context_menu_appearance", {"theme": "dark"})
    # #271：空身份会话按 slot0 分流，全局键读路由视图另建的主配置（初值只取盘）
    # ——预置必须落盘。
    assert config.save() is True

    dialog = settings_mod.ModernSettingsDialog(config, include_ai=False)
    assert "QDialog { background: #202024" in dialog.styleSheet()

    dialog.close()
    app.processEvents()


def test_settings_window_rethemes_immediately_with_the_appearance_selector(
    tmp_path, monkeypatch,
):
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("pet.settings_theme_qss._system_dark", lambda: False)
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=False)
    assert "QDialog { background: #202024" not in dialog.styleSheet()

    dialog.menu_theme_select.setCurrentData("dark")
    app.processEvents()
    assert "QDialog { background: #202024" in dialog.styleSheet()

    dialog.menu_theme_select.setCurrentData("light")
    app.processEvents()
    assert "QDialog { background: #202024" not in dialog.styleSheet()

    dialog.close()
    app.processEvents()


def test_modern_settings_finished_refreshes_even_on_rejected(tmp_path, monkeypatch):
    """新版设置直接关闭（Rejected）也必须把改动应用到桌宠。

    回归背景：只有 Accepted（保存并退出）才刷新；X 关闭时 closeEvent 已
    自动保存，但桌宠 scale/拖动物理等不生效。
    """
    from PySide6.QtWidgets import QApplication

    import pet.app as app_mod
    from pet.app import AppShell, PetInstance
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    owner = PetInstance.__new__(PetInstance)
    owner.shell = AppShell.__new__(AppShell)
    owner.modern_settings_dialog = object()
    refreshed = []

    class FakeWin:
        def refresh_pet_settings(self):
            refreshed.append(1)

        def set_bubble_suppressed(self, _suppressed):
            pass

    owner.win = FakeWin()
    owner.config = Config(tmp_path)
    # 真实壳恒有 config（应用链末尾无条件走一次台词预缓存，入口自己按开关收敛）
    owner.shell.config = owner.config
    owner.shell._apply_balance_timer = lambda: None
    owner.shell._sync_dynamic_island = lambda: None
    # Phase 1 门控后 todo 服务走 _sync_todo_service（懒启停）；测试只测桌宠刷新
    owner.shell._sync_todo_service = lambda: None
    owner.shell._sync_chime_service = lambda: None  # 语音报时同样懒启停，本用例只测桌宠刷新
    owner.shell._sync_festival_service = lambda: None  # 节日提醒同样懒启停（其内部会连带同步报时通道）
    owner.shell._sync_divination_service = lambda: None  # 每日一签同属懒启停族（无定时器，仍按开关建/释放）
    owner._refresh_chat_windows = lambda: None
    owner._sync_animation_prewarm = lambda: None
    monkeypatch.setattr(app_mod, "_mac_set_dock_icon_visible", lambda *a, **k: None)
    PetInstance._modern_settings_finished(owner, 0)  # QDialog.Rejected（X 关闭）
    assert refreshed == [1], "Rejected 关闭也必须刷新桌宠"


def test_ai_page_warns_when_keyring_unavailable(tmp_path, monkeypatch):
    """keyring 不可用时保存必须提示，且 key 仅保留内存（不落盘明文）。"""
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)

    class FakeStore:
        def get(self, _ref):
            return ""

        def set(self, _ref, _value):
            return False

    warnings = []
    monkeypatch.setattr(settings_mod.QMessageBox, "warning", lambda *a, **k: warnings.append(a))
    dialog = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=True)
    monkeypatch.setattr(dialog.ai_page, "_secret_store_type", FakeStore)
    dialog.ai_page.key.setText("sk-new")
    dialog.ai_page.save()
    assert len(warnings) == 1
    assert "系统安全存储" in str(warnings[0][2])
    assert dialog.ai_page.settings.active_config.api_key == "sk-new"
    dialog.close()
    app.processEvents()


def test_modern_settings_save_warns_on_failure(tmp_path, monkeypatch):
    """保存失败（此处置目标为目录迫使 os.replace 失败）时提示用户 + 配置路径。

    提示形态自 2026-10-10 起是 ``pet/settings_close_flow`` 的失败框（自带「放弃未保存的
    改动并关闭」出口）；这一条钉的是"失败必须看得见，且看得到路径"。
    """
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    seen: list = []
    _answer_save_failure(monkeypatch, seen)
    config = Config(tmp_path)
    config.path.mkdir(parents=True, exist_ok=True)  # 目标为目录，os.replace 必失败
    dialog = settings_mod.ModernSettingsDialog(config, include_ai=False)
    dialog._save()
    assert seen, "保存失败必须给用户可见提示"
    assert "配置未能写入磁盘" in seen[-1]["text"]
    assert str(config.path) in seen[-1]["info"]
    dialog.close()
    app.processEvents()


def test_modern_settings_close_applies_autostart(tmp_path, monkeypatch):
    """直接关闭（X）新版设置也必须应用开机自启开关（与「保存并退出」一致）。"""
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    applied = []
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    monkeypatch.setattr(
        settings_mod.autostart_mod,
        "set_enabled",
        lambda enabled: applied.append(enabled) or True,
    )
    config = Config(tmp_path)
    dialog = settings_mod.ModernSettingsDialog(config, include_ai=True)
    dialog.autostart_check.setChecked(True)
    dialog.close()  # X 关闭，不点「保存并退出」
    app.processEvents()
    assert applied == [True]
    config.reload()  # #271：写盘走路由视图另建的主配置，传入对象需重读才可见
    assert config.get("autostart_wanted") is True
    dialog.close()
    app.processEvents()


def test_modern_autostart_write_failure_warns(tmp_path, monkeypatch):
    """开机自启写入失败时必须弹窗提示（此前无任何反馈，用户不知自启没生效）。"""
    from PySide6.QtWidgets import QApplication

    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    monkeypatch.setattr(settings_mod.autostart_mod, "set_enabled", lambda enabled: False)
    warnings = []
    monkeypatch.setattr(
        settings_mod.QMessageBox, "warning", lambda *a, **k: warnings.append(a)
    )
    config = Config(tmp_path)
    dialog = settings_mod.ModernSettingsDialog(config, include_ai=True)
    # 与初始状态不同（初始 = is_enabled() = False）→ 触发写入
    dialog.autostart_check.setChecked(True)
    dialog._apply_autostart()
    assert len(warnings) == 1
    assert "开机自启设置失败" in str(warnings[0][1])
    assert "失败" in str(warnings[0][2])
    dialog.close()
    app.processEvents()
# --- macOS Dock recovery menu regression (2026-09-03) ---


def test_macos_dock_menu_keeps_settings_reachable_when_pet_is_mouse_through(monkeypatch):
    from unittest import mock

    from PySide6.QtWidgets import QApplication

    import pet.app as app_mod

    app = QApplication.instance() or QApplication([])
    controller = app_mod.AppShell.__new__(app_mod.AppShell)
    controller.app = app
    controller.enable_chat = True
    controller.instance = app_mod.PetInstance.__new__(app_mod.PetInstance)
    controller.instance.win = mock.Mock()
    controller.instance.open_modern_settings = mock.Mock()
    controller.instance.open_chat = mock.Mock()
    monkeypatch.setattr(app_mod.sys, "platform", "darwin")

    menu = controller._install_macos_dock_menu()

    assert menu is controller.dock_menu
    assert menu.property("dockMenuInstalled") is callable(getattr(menu, "setAsDockMenu", None))
    labels = [action.text() for action in menu.actions() if not action.isSeparator()]
    assert labels[:3] == ["显示桌宠", "桌宠设置", "AI 对话"]
    next(action for action in menu.actions() if action.text() == "桌宠设置").trigger()
    controller.instance.open_modern_settings.assert_called_once_with()
    menu.close()
    app.processEvents()


# --- 评审回归：碰撞分离累计写回 / 隐藏宠退出碰撞世界（2026-10-03）---


def _make_pet_sprite(pos, scale=0.5):
    """真 PetSprite（offscreen）：假 clip 无 ffmpeg 依赖，几何 = 640x360 画布 × scale。"""
    from PySide6.QtCore import QPointF

    from pet.pet_sprite import PetSprite
    from tests.test_sprite_collision_id import FakeLibrary

    return PetSprite(FakeLibrary(), pos=QPointF(*pos), scale=scale)


def _world_members(world, sprites):
    return [world._member_from_sprite(s) for s in sprites]


def test_real_pets_separate_by_solver_cumulative_in_one_tick():
    """真 PetSprite 静止重叠：一轮 tick 的终位 = 求解器累计分离量（D9 回归）。

    旧口径按 ImpulseResult 的**末轮** dx 逐 pair 写回：本场景 40px 重叠四轮迭代
    累计每只 18.51px，实际只写回 1.86px——90% 的分离量留到下一 tick，与连续
    重叠记账 / 去抖互相打架。
    """
    from PySide6.QtWidgets import QApplication

    from pet import collision
    from pet.sprite_collision import SpriteCollisionWorld

    app = QApplication.instance() or QApplication([])
    a = _make_pet_sprite((600, 300))
    b = _make_pet_sprite((880, 300))          # 320 宽 → 重叠 40px
    id_a, id_b = a.collision_id, b.collision_id
    world = SpriteCollisionWorld()
    members = _world_members(world, (a, b))
    impulses, combined, _history = collision.solve_multi_body_collision(
        members, tick=1)

    assert abs(combined[id_a][2]) > 5 * abs(impulses[0].dx_a), \
        "前提：累计分离量远大于末轮增量（旧写回口径）"

    world.tick([a, b], 0.016)

    assert a.pos.x() == pytest.approx(600.0 + combined[id_a][2], abs=1e-6)
    assert b.pos.x() == pytest.approx(880.0 + combined[id_b][2], abs=1e-6)
    # 一轮之后只剩求解器自己的迭代残差，而不是 90% 的重叠留待下一 tick
    residual = collision.check_collision_members(
        *_world_members(world, (a, b)))[3]
    assert residual <= 0.1 * 40.0
    app.processEvents()


def test_real_hidden_pet_is_not_an_invisible_obstacle():
    """逐只隐藏的真 PetSprite 退出碰撞世界：不推可见宠、不触发碰撞反馈（D10）。"""
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QApplication

    from pet.sprite_collision import (
        INTERACTION_NORMAL,
        INTERACTION_THROWN,
        SpriteCollisionWorld,
    )

    app = QApplication.instance() or QApplication([])
    world = SpriteCollisionWorld()
    victim = _make_pet_sprite((600, 300))
    ghost = _make_pet_sprite((880, 300))
    ghost.set_velocity(QPointF(-800, 0))      # 高速撞向可见宠
    ghost.set_visible(False)

    assert world.tick([victim, ghost], 0.016) == []
    assert victim.pos.x() == 600.0 and victim.pos.y() == 300.0
    assert victim.velocity.x() == 0.0
    assert victim.interaction_state == INTERACTION_NORMAL

    ghost.set_visible(True)                   # 恢复可见：碰撞干净回归
    results = world.tick([victim, ghost], 0.016)
    assert [r.pair for r in results] == [
        "|".join(sorted((victim.collision_id, ghost.collision_id)))]
    assert victim.velocity.x() < 0.0          # 被撞飞
    assert victim.interaction_state == INTERACTION_THROWN
    app.processEvents()
