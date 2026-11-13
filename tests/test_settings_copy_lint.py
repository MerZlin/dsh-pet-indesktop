# -*- coding: utf-8 -*-
"""设置页文案「黑话」lint（设计稿 §四 C3 的机制化落地）。

判据（设计稿 §四）：标题/hint 里出现 ①全大写缩写（≥2 字母）②IP 地址 ③黑名单词表，
该行必须满足以下之一，否则 fail：

1. 它落在 advanced 折叠组或可折叠框内——同一任务里的低频参数，术语在这层是允许的；
2. 它在上面的域级/行级白名单里——每条都写明「为什么对用户是合法说法」。

为什么要有这条 lint：文案审查是人对人、会漏会疲劳；lint 是机制——新增设置的人写文案时
就被 CI 拦下，「foobar 黑话」这类问题在源头不再发生（GATES 准入记录里
``disclosure_level: primary|advanced`` 的自动化执行）。

**白名单不是垃圾桶**：加词必须改测试、留评审痕迹，且下面有一条防腐烂用例盯着
「白名单里的每个条目都还真的存在且真的命中」。
"""
from __future__ import annotations

import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import pet.modern_settings_dialog as settings_mod
from pet.config import Config
from pet.modern_settings_dialog import SettingRow, SettingsSection
from pet.settings_widgets import CollapsibleGroup

app = QApplication.instance() or QApplication([])

#: 全大写缩写（≥2 字母）。左界只排 ASCII 字母：``macOS`` 里的 ``OS`` 是产品名的一部分，
#: 不是缩写（mac 专属的 ``dock_icon`` 行文案就是 ``在 macOS Dock 中…``，旧写法把整行判成黑话）。
#: **不许改用 ``\b``**：``\b`` 按 ``\w`` 判词界，而 Python 的 ``\w`` 含 CJK——``CPU使用率``
#: 这种紧贴中文的缩写会被判成没有词界而漏检，等于把这条 lint 的判据削掉一半。
_ABBREV = re.compile(r"(?<![A-Za-z])[A-Z]{2,}")
#: IPv4 字面量（探测目标这类实现细节不许出现在主视线里）。
_IPV4 = re.compile(r"\d+\.\d+\.\d+\.\d+")
#: 黑名单词表：协议名 / 内部模块名 / 物理引擎术语 / 与 lint 同一族的旧黑话。
_BLACKLIST = ("SMTC", "UDP", "DNS", "甩出死区", "零初速", "网卡字节计数器", "foo_mediacontrol")

#: 域级豁免：本域目标用户必然懂这些词（设计稿 §四 C3 第二出口）。
_DOMAIN_WHITELIST: dict[str, frozenset[str]] = {
    # AI 域的用户就是在配 API——「API Key / API 地址」是他要处理的对象本身。
    "AI 与对话": frozenset({"API", "AI", "JSON", "SSL"}),
    # 语音域的用户在这里就要去服务商拿 Key：带上 Key 的后端（豆包 / MiMo）若不写
    # 「API Key」，用户根本不知道该填什么、去哪儿找（2026-10-14 随 provider 注册表
    # 进来：凭据行按后端声明生成）。这是他要处理的对象，不是实现黑话。
    "语音": frozenset({"API"}),
}

#: 行级豁免：行 key（去掉 ``settingRow_`` 前缀）→ 允许出现在该行文案里的 token。
_ROW_WHITELIST: dict[str, frozenset[str]] = {
    # 文件识别域：解释「交给谁」的产品级说法，不是内部代号。
    "file_interpret_enabled": frozenset({"AI"}),
    # 全局快捷键：行标题必须**与右键菜单项「AI 对话」逐字一致**——用户在菜单上看到
    # 的就是这个名字，换成「对话」反而对不上号（这是外部契约，不是内部代号）。
    "hotkey_open_chat": frozenset({"AI"}),
    # 注（2026-10-14）：`voice_chime_custom_times` / `festival_reminder_times` /
    # `voice_chime_voice` / `festival_custom_quotes_west` 四条豁免已随文案重写而失效
    # ——新文案不再出现 HH/MM、API、KJV 这些 token，所以按本文件「白名单不是垃圾桶」
    # 的口径把它们**删掉**（防腐烂用例会盯着失效条目）。
    # 免费视觉模型名：用户要按这个名字去服务商侧配置，删掉就无法照做。
    "proactive_free": frozenset({"GLM"}),
    # 自定义台词模板是 JSON：用户要复制/粘贴的就是它。
    "dialogue_mode": frozenset({"JSON"}),
    "dialogue_template_actions": frozenset({"AI", "JSON"}),
    # 流量/内存单位。
    "island_network": frozenset({"KB"}),
    "dynamic_island_icon_value": frozenset({"MB"}),
    # 岛指标段名：CPU/GPU 是用户日常词汇（段就叫这个名），不是实现黑话；
    # 内存行的 hint 如实说「与 CPU 共用同一条采样线程」。
    "island_cpu": frozenset({"CPU"}),
    "island_mem": frozenset({"CPU"}),
    "island_gpu": frozenset({"GPU"}),
    # 游戏帧率段：FPS 就是这一段在用户侧的名字（同 CPU / GPU 的口径），也是用户搜索
    # 这一行时打的词；hint 里的「Present FPS」正是在点明读数口径，不能改写掉。
    "island_fps": frozenset({"FPS"}),
    # 「AI 回复到达」是对话功能在用户侧的通用说法。
    "dynamic_island_hidden_chat": frozenset({"AI"}),
    "dynamic_island_event_effects": frozenset({"AI"}),
    # 菜单字体设置：「UI 字体」＝界面字体。
    "menu_font": frozenset({"UI"}),
    "menu_font_size": frozenset({"UI"}),
    # 菜单实际显示「公网 IP / IP 属地」，只说菜单里能看到的内容；KB 是取一次的量级。
    "network_info_menu": frozenset({"IP", "KB"}),
    # 推荐用户去装的第三方插件名（定稿明文保留，要能照着搜）。
    "foobar_lyrics": frozenset({"foo_mediacontrol"}),
    # 键盘按键名：用户要按的就是它。
    "shift_drag": frozenset({"SHIFT"}),
    # 直播软件名（用户自己的工具）。
    "stream_capture": frozenset({"OBS"}),
}


def _dialog(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    return settings_mod.ModernSettingsDialog(Config(base=tmp_path), include_ai=True)


def _tokens(text: str) -> frozenset[str]:
    """命中判据：缩写 ∪ IP 字面量 ∪ 黑名单词。"""
    found = set(_ABBREV.findall(text)) | set(_IPV4.findall(text))
    found |= {word for word in _BLACKLIST if word in text}
    return frozenset(found)


def _in_disclosure(row: SettingRow) -> bool:
    """行是否落在高级折叠组 / 可折叠框内（C3 的第一出口）。"""
    parent = row.parentWidget()
    while parent is not None:
        if isinstance(parent, CollapsibleGroup):
            return True
        if isinstance(parent, SettingsSection) and parent.advanced:
            return True
        parent = parent.parentWidget()
    return False


def _domain_of(dialog, row: SettingRow) -> str:
    for index in range(dialog.pages.count()):
        if dialog.pages.widget(index).isAncestorOf(row):
            return dialog.sidebar.item(index).text()
    return ""


def test_no_jargon_outside_advanced_groups(tmp_path, monkeypatch):
    """主视线（非高级组、非折叠框）里不许出现黑话；白名单例外逐条登记在上面。"""
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        rows = dialog.findChildren(SettingRow)
        assert len(rows) > 150, "设置行没扫到（域装配可能变了），本用例会假绿"
        offenders = []
        for row in rows:
            key = row.objectName()[len("settingRow_"):]
            hits = _tokens(f"{row.label.text()}\n{row.hint_label.text()}")
            if not hits:
                continue
            allowed = _ROW_WHITELIST.get(key, frozenset())
            allowed |= _DOMAIN_WHITELIST.get(_domain_of(dialog, row), frozenset())
            if hits - allowed and not _in_disclosure(row):
                offenders.append(f"{key}: {sorted(hits - allowed)} | {row.label.text()}")
        assert not offenders, (
            "主视线里的设置行出现黑话（要么改文案，要么挪进高级折叠，"
            "要么在 _ROW_WHITELIST 里写明豁免理由）：\n" + "\n".join(offenders)
        )
    finally:
        dialog.close()


def test_abbrev_criteria_lets_product_names_through_but_keeps_chinese_adjacency():
    """缩写判据的两条边界：产品名 ``macOS`` 不算黑话，紧贴中文的缩写仍必须命中。

    回归背景：``[A-Z]{2,}`` 会把 ``macOS`` 里的 ``OS`` 抓成缩写。mac 上唯一的 ``dock_icon``
    行（``sys.platform == "darwin"`` 才建）文案写着「在 macOS Dock 中显示桌宠应用」，于是
    这行被判黑话——而 Windows/Linux 根本不建这一行，本地与那两个平台全绿，只有 mac CI 红。
    修法是**只加左界** ``(?<![A-Za-z])``：``macOS`` 里的 ``OS`` 前面是 ASCII 字母 ``c``，
    不算缩写；``CPU使用率检测`` 里的 ``CPU`` 前面没有字母，照旧命中。

    为什么不能用 ``\\b``：Python 的 ``\\w`` 含 CJK，``CPU`` 后面紧贴中文时 ``\\b`` 判不出
    词界，``CPU使用率检测`` 会被整条漏掉——那是把判据削掉，不是修误报。
    """
    # 产品名不算缩写（mac 那行的真实文案）。
    assert _tokens("在 macOS Dock 中显示桌宠应用；关闭后仍可通过桌宠和托盘操作。") == frozenset()
    # 紧贴中文的缩写照旧命中（左右两侧都试）。
    assert "CPU" in _tokens("CPU使用率检测")
    assert "CPU" in _tokens("检测使用率CPU")
    assert "GPU" in _tokens("与该显卡GPU共享同一块显存")
    # 行首 / 空格后 / 标点后的缩写一个都不许漏。
    assert _ABBREV.findall("SMTC 与 UDP、DNS 上报") == ["SMTC", "UDP", "DNS"]
    assert _ABBREV.findall("(IP)") == ["IP"]


def test_mac_music_hints_are_jargon_free_on_every_host(monkeypatch):
    """mac 口径的「显示歌词」「音乐自动唱歌」说明，在任何主机上都要过同一套判据。

    回归背景：这两行的说明按 ``sys.platform`` 分口径（``pet/music/settings.py``），上面那条
    整窗 lint 只看**本机平台**渲染出来的文案——mac 口径写了「QQ音乐」，``QQ`` 被缩写判据
    命中，可 Windows / Linux 上根本渲染不到这段，本地与那两个平台全绿，只有 mac 红
    （与上面 ``macOS`` 那次是同一类）。这里强制 darwin 直接取文案，让三端都能拦下。

    为什么不走 ``_ROW_WHITELIST``：``music_lyric`` 行在三个平台上都存在，而 ``QQ`` 只出现在
    mac 口径里；登记进白名单的话，防腐烂用例会在 Windows / Linux 上判它失效。
    """
    import sys

    from pet.music import settings as settings_music

    monkeypatch.setattr(sys, "platform", "darwin")
    for name, text in (
        ("显示歌词", settings_music.lyric_row_hint()),
        ("音乐自动唱歌", settings_music.sing_row_hint()),
    ):
        assert _tokens(text) == frozenset(), f"mac 口径的「{name}」说明出现黑话：{sorted(_tokens(text))}"


def test_whitelist_has_no_stale_entries(tmp_path, monkeypatch):
    """白名单不许腐烂：登记的行仍存在、登记的 token 仍真的命中。"""
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        rows = {row.objectName()[len("settingRow_"):]: row for row in dialog.findChildren(SettingRow)}
        stale = []
        for key, allowed in _ROW_WHITELIST.items():
            row = rows.get(key)
            if row is None:
                # 平台闸门（如 foobar / 直播捕获只在 Windows 建行）不算腐烂。
                continue
            hits = _tokens(f"{row.label.text()}\n{row.hint_label.text()}")
            unused = sorted(token for token in allowed if token not in hits)
            if unused:
                stale.append(f"{key}: {unused}")
        assert not stale, (
            "白名单条目已失效（文案改过或行删了，请同步收紧白名单）：\n" + "\n".join(stale)
        )
    finally:
        dialog.close()


#: 「逐宠」行文案不许出现的范围词：这些键一只一份（``PER_PET_KEYS``），
#: 承诺"所有桌宠"就是把每宠设置说成全局。
#:
#: 为什么要有这条机制：F06（2026-10-10 终审）抓到「播放速率」行写着
#: 「控制**所有**桌宠动画的播放速度」，而 ``playback_speed`` 从 B7b 起就是每宠键
#: （右键菜单的速率也只改被点的那一只）——修复报告当时称"已改"，交付字节里却没改。
#: 手工核一遍文案会漏、会疲劳；键集与文案的这层对应关系必须由机器盯着。
GLOBAL_SCOPE_WORDS = ("所有桌宠", "全部桌宠", "所有宠物")


def test_per_pet_rows_never_promise_a_global_scope(tmp_path, monkeypatch):
    """逐宠键的行文案不许说"所有桌宠"（范围真值 = ``PER_PET_KEYS``）。"""
    from pet import pet_profiles

    dialog = _dialog(tmp_path, monkeypatch)
    try:
        offenders = []
        scanned = 0
        for row in dialog.findChildren(SettingRow):
            key = row.objectName()[len("settingRow_"):]
            if key not in pet_profiles.PER_PET_KEYS:
                continue
            scanned += 1
            claimed = [word for word in GLOBAL_SCOPE_WORDS
                       if word in f"{row.label.text()}\n{row.hint_label.text()}"]
            if claimed:
                offenders.append(f"{key}: {claimed} | {row.hint_label.text()}")
        assert scanned >= 5, "逐宠行没扫到（键集或行名变了），本用例会假绿"
        assert not offenders, (
            "逐宠键的行文案承诺了全局范围（改成「这只桌宠」，或把键真的做成全局）：\n"
            + "\n".join(offenders)
        )
    finally:
        dialog.close()
