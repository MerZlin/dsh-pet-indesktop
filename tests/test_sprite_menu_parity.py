# -*- coding: utf-8 -*-
"""overlay 右键菜单 parity：与真实 PetWindow 的现代菜单树逐项一致。

比对基准是**真实 PetWindow**：构造一个 PetWindow，按 ``app.py::_wire_window``
的原样接线（聊天/余额/看屏/待办/报时/节日/生小肥鱼…）注入回调，再用**同一个
建造器**（``build_modern_menu``）在同一份 Config、同一平台上建菜单；overlay 侧用
``build_sprite_full_menu`` 建。断言 overlay 树**逐项包含**基准树（同一父路径、
兄弟顺序保持），且多出来的条目只允许 overlay 特有项。

基准用真 PetWindow 而不是替身，正是为了防"两边都少同一项"的自证：基准里出现
什么，由注册表 + 用户编排 + 真实窗口面决定；facade 缺任何一面都会在这里红。

旧版扁平布局及模板切换已于 2026-10-08 全量退役，本文件随之收成单树 parity
（见 docs/PR-REPORT-LEGACY-MENU-RETIREMENT-2026-10-08.md）。

覆盖：
1. overlay 树 ≡ build_modern_menu(PetWindow)（注册表 + 用户编排 + 图标分组）；
2. 显式清单：用户点名的缺失条目（AI 对话/黄金回旋/显示本轮消费/音乐子菜单/
   边缘探头/大小/DeepSeek Harness/打开网页版 DeepSeek/主动识屏/Agent 联动/
   网络信息）逐项在树里；
3. 无聊天变体：聊天门条目在两侧一起消失；
4. 音乐服务不可达 → 整组隐藏；
5. 点击路由：每项落到窗口侧同一服务方法（等价性见 sprite_menu_facade 的
   方法 docstring）；
6. close_on_trigger 命令在菜单关闭后必须派发（overlay 此前整类静默失效）。

纪律：offscreen；同步直调，不 sleep 赌时序。
"""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication

import tests.test_context_menu_lifecycle as lc
import tests.test_overlay_window_capabilities as cap
import tests.test_sprite_menu_facade as fac
from pet.catalog import CANVAS_W, SCALE_STEPS
from pet.config import Config
from pet.context_menus import build_modern_menu
from pet.overlay_shell import OverlayShell
from pet.pet_sprite import PetSprite
from pet.sprite_menu_facade import SpriteMenuFacade, build_sprite_full_menu

app = QApplication.instance() or QApplication([])


def _slot0_scale(config) -> float:
    """主宠档案（``config-slot-0.json``）里的 scale：每宠键的唯一真值。"""
    path = config.dir / "config-slot-0.json"
    return json.loads(path.read_text(encoding="utf-8"))["scale"]

# 素材池：两侧同源（分类逻辑不是本测试对象；内容一致才能比较动画分类子树）
POOLS = {
    "idles": ["idle1"],
    "turns": ["turn1"],
    "moves": ["walk"],
    "clicks": ["click1"],
    "acts": ["act1"],
}

# overlay 特有的额外条目（多宠时的「退出这只」；单宠菜单与基准逐项相同）
ALLOWED_EXTRAS = frozenset({"退出这只"})

# 用户点名的缺失条目（窗口侧清单 → overlay 菜单必须逐项存在）
REQUIRED_LABELS = (
    "AI 对话",
    "黄金回旋",
    "显示本轮消费",
    "音乐",
    "边缘探头",
    "大小",
    "DeepSeek Harness",
    "打开网页版 DeepSeek",
    "主动识屏",
    "Agent 联动",
    "网络信息",
    "桌宠设置",
)
MUSIC_LABELS = (
    "让人家歇一会儿嘛（暂停 / 播放）",
    "给主人换一首（切歌）",
    "人家想再听刚才那首（上一首）",
    "人家今天不唱了（退出音乐模式）",
    "歌词对齐",
    "打开网易云音乐给主人放歌",
    "打开QQ音乐给主人放歌",
)
CHAT_GATED_LABELS = ("AI 对话", "看看屏幕", "主动识屏", "DeepSeek 余额")
# Windows 专有项：主动识屏 v1 依赖 win32 前台窗口/截屏能力（vision.py /
# proactive.py 仅 win32 起监视器），菜单注册处 shared.add_proactive_menu 与
# registry proactive_screen 均按 sys.platform == "win32" 门控——posix 上
# 两侧建造器都不产出该项，期望模板须按平台分流（test_menu_layout.py 同先例）。
WIN32_ONLY_LABELS = ("主动识屏",)


# ---------------------------------------------------------------- 假件
class _StubAppShell:
    """AppShell 服务面替身：只记调用（真实路由目标见 facade 方法 docstring）。

    刻意**不**用 ``__getattr__`` 兜底：兜底会让任意属性探测都"可调用"，
    把宿主判定（``install_music_lyric`` 之类）伪装成存在。
    """

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))

    def show_balance(self, parent=None):
        self._record("show_balance", parent)

    def show_weather(self, parent=None):
        self._record("show_weather", parent)

    def check_update(self, parent=None):
        self._record("check_update", parent)

    def open_todo_panel(self):
        self._record("open_todo_panel")

    def open_network_info(self):
        self._record("open_network_info")

    def trigger_voice_chime_now(self, text=""):
        self._record("trigger_voice_chime_now", text)

    def toggle_voice_chime(self):
        self._record("toggle_voice_chime")

    def trigger_festival_now(self):
        self._record("trigger_festival_now")

    def toggle_festival_reminder(self):
        self._record("toggle_festival_reminder")

    def open_settings_process(self, instance=None):
        self._record("open_settings_process", instance)
        return True


class _StubInstance(cap.CapInstance):
    """PetInstance 面替身：聊天/设置/角色库 + 进程级 shell。"""

    enable_chat = True

    def __init__(self, config, *, chat=True) -> None:
        super().__init__(config)
        self.enable_chat = bool(chat)
        self.chat_build_capable = bool(chat)  # 与 _wire_window/PetInstance 的接线同款
        self.shell = _StubAppShell()
        self.calls: list[tuple] = []

    def open_chat(self):
        self.calls.append(("open_chat",))

    def open_chat_settings(self):
        self.calls.append(("open_chat_settings",))

    def sync_look_to_chat(self, user_text, reply):
        self.calls.append(("sync_look_to_chat", user_text, reply))

    def _create_library(self, character_id):
        return fac.RichLibrary()


class _RefLibrary(lc.FakeLibrary):
    """基准 PetWindow 的素材库（真 PetWindow 构造要求 names()）。"""

    def __init__(self) -> None:
        super().__init__()
        self._clips["act1"] = lc.FakeClip()
        self.character_id = ""


class _NoChatInstance(_StubInstance):
    def __init__(self, config) -> None:
        super().__init__(config, chat=False)


# ---------------------------------------------------------------- 装配
def _make_overlay(tmp_path, *, chat=True, values=None):
    config = Config(base=tmp_path)
    for key, value in (values or {}).items():
        config.set(key, value)
    instance = _StubInstance(config, chat=chat)
    screen = cap.FakeScreen((0, 0, 1920, 1080), (0, 0, 1920, 1040))
    shell = OverlayShell(
        app, instance, screen=screen,
        sprite_factory=lambda lib, pos, scale: PetSprite(lib, pos=pos, scale=scale))
    return shell, instance, config


def _reference_window(config, *, chat=True):
    """真实 PetWindow + app.py 同款接线（``_wire_window`` 的逐点对照）。"""
    win = lc.PetWindow(_RefLibrary(), config)
    for key, pool in POOLS.items():
        setattr(win, key, list(pool))
    win.on_open_chat = (lambda: None) if chat else None
    win.chat_build_capable = bool(chat)
    win.on_open_chat_settings = (lambda: None) if chat else None
    win.on_show_balance = (lambda parent=None: None) if chat else None
    win.on_show_weather = lambda parent=None: None
    win.on_look_screen = win.look_at_screen if chat else None
    win.on_open_modern_settings = lambda: None
    win.on_spawn_pet = lambda: None
    win.on_clear_spawned_pets = lambda: None
    win.on_check_update = lambda parent=None: None
    win.on_open_todo_panel = lambda: None
    win.on_network_info = lambda: None
    win.on_voice_chime_now = lambda text="": None
    win.on_toggle_voice_chime = lambda: None
    win.on_festival_now = lambda: None
    win.on_toggle_festival = lambda: None
    win.on_switch_character = lambda character_id: None
    return win


def _reference_menu(win):
    from PySide6.QtWidgets import QMenu

    menu = QMenu()
    build_modern_menu(menu, win)
    return menu


# ---------------------------------------------------------------- 树比对
def _children(container):
    items = []
    for action in container.actions():
        if action.isSeparator():
            continue
        items.append((action.text(), action.menu()))
    return items


def _assert_reference_contained(overlay, reference, path=(), allowed=ALLOWED_EXTRAS):
    """基准树逐项包含在 overlay 树里（同父路径 + 兄弟顺序保持），额外项受限。"""
    ref_items = _children(reference)
    ov_items = _children(overlay)
    ov_texts = [text for text, _ in ov_items]
    cursor = 0
    for text, ref_sub in ref_items:
        found = None
        for index in range(cursor, len(ov_texts)):
            if ov_texts[index] == text:
                found = index
                break
        assert found is not None, (
            f"overlay 菜单缺项 {path + (text,)}；同级实际 = {ov_texts[cursor:]}")
        if ref_sub is not None:
            ov_sub = ov_items[found][1]
            assert ov_sub is not None, f"{path + (text,)} 在 overlay 侧不是子菜单"
            _assert_reference_contained(
                ov_sub, ref_sub, path + (text,), allowed)
        cursor = found + 1
    ref_texts = {text for text, _ in ref_items}
    extras = [text for text in ov_texts if text not in ref_texts]
    assert set(extras) <= set(allowed), f"{path} 出现未预期条目：{extras}"


def _find(container, label: str):
    """同级查找（子菜单不算）。"""
    for action in container.actions():
        if action.text() == label:
            return action
    raise AssertionError(
        f"菜单缺项 {label}；实际 = {[a.text() for a in container.actions()]}")


def _find_anywhere(container, label: str):
    """递归查找（现代树的条目多藏在分组子菜单里，如「桌宠控制」）。"""
    for action in container.actions():
        if action.text() == label:
            return action
        submenu = action.menu()
        if submenu is not None:
            found = _find_anywhere(submenu, label)
            if found is not None:
                return found
    return None


def _require(container, label: str):
    action = _find_anywhere(container, label)
    assert action is not None, (
        f"菜单缺项 {label}；实际 = {_all_labels(container)}")
    return action


def _flatten(container, path=()):
    entries = []
    for action in container.actions():
        if action.isSeparator():
            continue
        entries.append(path + (action.text(),))
        if action.menu() is not None:
            entries.extend(_flatten(action.menu(), path + (action.text(),)))
    return entries


def _all_labels(container):
    return {path[-1] for path in _flatten(container)}


# ---------------------------------------------------------------- 1. 树 parity
def test_menu_tree_matches_reference_petwindow(tmp_path):
    shell, _instance, config = _make_overlay(tmp_path)
    win = _reference_window(config)
    try:
        reference = _reference_menu(win)
        overlay = build_sprite_full_menu(shell)
        _assert_reference_contained(overlay, reference)
        # 现代布局确实生效：分组子菜单 + 样式标记
        assert "桌宠控制" in _all_labels(overlay)
        assert overlay.property("menuStyle") == "modern"
    finally:
        win.close()
        shell._delete_runtime_marker()


def test_menu_is_grouped_under_submenus(tmp_path):
    shell, _instance, _config = _make_overlay(tmp_path)
    try:
        menu = build_sprite_full_menu(shell)
        root = [a.text() for a in menu.actions() if not a.isSeparator()]
        assert "桌宠控制" in root and "工具与帮助" in root
        assert "回到右下角" not in root          # 收进分组子菜单
    finally:
        shell._delete_runtime_marker()


# ---------------------------------------------------------------- 2. 显式清单
def test_required_entries_all_present(tmp_path):
    # 歌词四件套按 `music_lyric_enabled` 门控（注册表 available 过滤，默认关）：
    # 要断言完整音乐子菜单就得把功能打开，与 test_menu_layout 同口径。
    shell, _instance, _config = _make_overlay(
        tmp_path, values={"music_lyric_enabled": True})
    try:
        menu = build_sprite_full_menu(shell)
        labels = _all_labels(menu)
        expected = REQUIRED_LABELS
        if sys.platform != "win32":
            expected = tuple(
                label for label in REQUIRED_LABELS if label not in WIN32_ONLY_LABELS)
            for label in WIN32_ONLY_LABELS:
                assert label not in labels, f"posix 不应出现 Windows 专有项：{label}"
        for label in expected:
            assert label in labels, f"菜单缺项：{label}"
        # overlay 的「隐藏桌宠」随注册表的 hide_pet 落在「桌宠控制」里（不再是
        # 顶层的补位条目），与窗口侧同一棵树
        controls = _find(menu, "桌宠控制").menu()
        assert _find(controls, "隐藏桌宠") is not None
        music = _find(menu, "音乐").menu()
        assert music is not None
        music_labels = [a.text() for a in music.actions() if not a.isSeparator()]
        for label in MUSIC_LABELS:
            assert label in music_labels, f"音乐子菜单缺项：{label}"
    finally:
        shell._delete_runtime_marker()


def test_ai_settings_stays_merged_into_pet_settings_entry(tmp_path):
    """「AI 设置」不回到右键菜单：它已并入「桌宠设置 → AI 与对话」。

    这是文档化的产品决定（docs/SETTINGS-REDESIGN-Q6-Q7-DOMAIN-LAYOUT-DECISION.md
    402/428 行：「合并到『桌宠设置 → AI 与对话』」「移除重复快捷入口」），旧版菜单
    退役时必须保住它——给 modern 树再加一条「AI 设置」= 把当年有意移除的重复入口
    装回来。设置页内的 AI 能力面由「AI 与对话」域承担（见 test_menu_layout /
    test_settings_ia_first_cut）。
    """
    from pet.context_menus.registry import MENU_ACTIONS

    assert "chat_settings" not in MENU_ACTIONS.ids, (
        "「AI 设置」已并入桌宠设置，不得作为独立菜单项复活")
    shell, _instance, _config = _make_overlay(tmp_path)
    try:
        labels = _all_labels(build_sprite_full_menu(shell))
        assert "桌宠设置" in labels
        assert "AI 设置" not in labels
    finally:
        shell._delete_runtime_marker()


def test_music_menu_lyric_align_availability_matches_the_registry(tmp_path):
    """音乐子菜单装有「歌词对齐」，且可用性语义与新版注册表逐点一致。

    引导文案（首首无进度歌曲的一次性提示、设置页「显示歌词」行）都写着「用右键
    菜单『音乐 → 歌词对齐』校正」；注册表里有 ``music_lyric_align``，共享建造器
    必须同判据，否则用户按提示去找的那一项点了没反应。
    """
    from PySide6.QtWidgets import QMenu

    from pet.context_menus.registry import MENU_ACTIONS
    from pet.context_menus.shared import MUSIC_ALIGN_DISABLED_REASON, add_music_lyric_align
    from pet.music.lyric import LyricLine
    from pet.music.lyric_controller import MusicLyricController

    config = Config(base=tmp_path)
    config.set("music_lyric_enabled", True)
    win = _reference_window(config)
    try:
        def _align_action():
            menu = QMenu()
            build_modern_menu(menu, win)
            music = next(a.menu() for a in menu.actions() if a.text() == "音乐")
            return next(a for a in music.actions() if a.text() == "歌词对齐")

        action = _align_action()
        entries = [a.text() for a in action.menu().actions() if not a.isSeparator()]
        assert entries == [
            "回到开头（现在这句算开头）", "上一句", "下一句", "后退 5 秒", "前进 5 秒",
        ], entries
        # 没有控制器：置灰 + 写明原因（点了没反应是反模式），与注册表判定一致
        assert action.isEnabled() is ("music_lyric_align" in MENU_ACTIONS.enabled_ids(win))
        assert action.isEnabled() is False
        assert action.toolTip() == MUSIC_ALIGN_DISABLED_REASON

        # 位置靠本地时钟估算（网易云这类）→ 菜单可用；与注册表同判据
        ctrl = MusicLyricController(win)
        ctrl._title_line = "我在唱《t》"
        ctrl._tracker.load([LyricLine(0.0, "第一句")], now=time.monotonic(),
                           position=None)
        win._music_lyric = ctrl
        assert ctrl.align_available() is True

        action = _align_action()
        assert action.isEnabled() is True
        assert action.isEnabled() is ("music_lyric_align" in MENU_ACTIONS.enabled_ids(win))
        assert add_music_lyric_align is not None   # 共享建造器仍是唯一实现
    finally:
        win.close()


def test_capabilities_exposed_in_the_overlay_menu(tmp_path):
    shell, _instance, _config = _make_overlay(tmp_path)
    try:
        menu = build_sprite_full_menu(shell)
        labels = _all_labels(menu)
        for label in ("AI 对话", "播放动画", "切换角色", "播放速率", "大小", "音乐",
                      "拖动物理", "边缘探头", "黄金回旋", "显示本轮消费", "桌宠设置"):
            assert label in labels, f"modern 树缺项：{label}"
        controls = _find(menu, "桌宠控制").menu()
        assert _find(controls, "生小肥鱼") is not None
        tools = _find(menu, "工具与帮助").menu()
        for label in ("DeepSeek Harness", "打开网页版 DeepSeek", "网络信息",
                      "GitHub 项目页"):
            assert _find(tools, label) is not None
    finally:
        shell._delete_runtime_marker()


def test_music_submenu_offers_every_player_a_launch_entry(tmp_path):
    """overlay 侧音乐子菜单给每个播放器一条「打开…给主人放歌」入口。

    酷狗 / 汽水是后补的（此前只有网易云 / QQ音乐）：入口少了就是"用户按名字找不到
    那个能放歌的项"。文案按 ``music_players`` 注册表逐键核对，不手抄标签（避免与
    注册表漂移，也让四个键的映射抄错时立刻红）；窗口侧同一棵树由
    ``test_menu_tree_matches_reference_petwindow`` 的逐项包含 + 兄弟顺序保证同源。
    """
    from pet.music import players as music_players

    shell, _instance, _config = _make_overlay(
        tmp_path, values={"music_lyric_enabled": True})
    try:
        music = _find(build_sprite_full_menu(shell), "音乐").menu()
        assert music is not None
        labels = [a.text() for a in music.actions() if not a.isSeparator()]
    finally:
        shell._delete_runtime_marker()

    for key in ("netease", "qqmusic", "kugou", "soda"):
        expected = f"打开{music_players.player_label(key)}给主人放歌"
        assert expected in labels, f"音乐子菜单缺 {key} 的启动入口：{labels}"
    launch = [text for text in labels if text.startswith("打开")]
    assert len(launch) == len(set(launch)) == 4, f"启动入口重复或数量不对：{launch}"


def test_divination_entry_appears_when_app_shell_provides_the_callback(tmp_path):
    """回调存在 → overlay 菜单必须出现「抽支签」。

    真 app 里回调来自 ``AppShell.trigger_divination_draw``（app.py），门面
    ``SpriteMenuFacade`` **必须显式代理** ``on_divination_draw``：注册表的可用性
    判定是 ``callable(getattr(facade, "on_divination_draw", None))``，漏代理就
    **静默少一项**——2026-10-15 林小姐截图发现的就是这一条（模板里 visible: true
    也照样不显示）。

    这里不能靠"和 PetWindow 对齐"那条拦：脚手架两侧都没有 app 回调，
    包含关系照样成立，红不了。所以要显式把回调装上再断言。
    """
    shell, instance, _config = _make_overlay(tmp_path)
    try:
        instance.shell.trigger_divination_draw = lambda: None
        labels = _all_labels(build_sprite_full_menu(shell))
        assert "抽支签" in labels, "回调可用时「抽支签」必须出现在 overlay 菜单里"
        # 反向：把回调摘掉，这一项必须消失（可用性判定真的在起作用，不是硬编码显示）
        del instance.shell.trigger_divination_draw
        assert "抽支签" not in _all_labels(build_sprite_full_menu(shell))
    finally:
        shell._delete_runtime_marker()


def test_harness_launcher_gets_widget_dialog_parent(tmp_path, monkeypatch):
    """harness 四个入口的模态父必须是 QWidget（facade 不能当 QMessageBox parent）。

    同时锁住宿主形气泡面：``launch_harness_gui`` 用 ``parent.show_bubble`` 反馈。
    """
    from PySide6.QtWidgets import QWidget

    import pet.context_menus.shared as shared_mod

    shell, _instance, _config = _make_overlay(tmp_path)
    captured: list[tuple] = []
    bubbles: list[tuple] = []
    try:
        monkeypatch.setattr(
            shared_mod, "launch_harness_gui",
            lambda parent=None, action="start", target="auto": captured.append(
                (parent, action, target)))
        monkeypatch.setattr(
            shell, "show_bubble",
            lambda text, duration_ms=3200, **kw: bubbles.append((text, duration_ms)))
        facade = SpriteMenuFacade(shell)
        facade.show_bubble("宿主形气泡", 1234)
        assert bubbles == [("宿主形气泡", 1234)]

        menu = build_sprite_full_menu(shell)
        harness = _require(menu, "DeepSeek Harness").menu()
        for action in harness.actions():
            action.trigger()            # 菜单未弹出 → 回调立即执行
        assert [(action, target) for _, action, target in captured] == [
            ("start", "web"), ("start", "desktop"), ("restart", "auto"), ("stop", "auto"),
        ]
        assert all(isinstance(parent, QWidget) for parent, _, _ in captured)
        assert all(parent is shell.overlay for parent, _, _ in captured)
    finally:
        shell._delete_runtime_marker()


# ---------------------------------------------------------------- 3. 聊天门
def test_chat_gated_entries_follow_chat_availability(tmp_path):
    shell, _instance, config = _make_overlay(tmp_path, chat=False)
    win = _reference_window(config, chat=False)
    try:
        reference = _reference_menu(win)
        overlay = build_sprite_full_menu(shell)
        _assert_reference_contained(overlay, reference)
        labels = _all_labels(overlay)
        assert "AI 对话" not in labels
        assert "DeepSeek Harness" not in labels and "主动识屏" not in labels
        # 与基准逐点一致：基准也不含这些
        assert set(CHAT_GATED_LABELS).isdisjoint(_all_labels(reference))
    finally:
        win.close()
        shell._delete_runtime_marker()


# ------------------------------------------------- 网络信息入口（issue #172）
def test_network_info_entry_greyed_out_when_switched_off(tmp_path):
    """入口开关关掉 = 保留位置 + 置灰 + 说明（Runtime Disabled）。"""
    shell, _instance, _config = _make_overlay(
        tmp_path, values={"network_info_menu_enabled": False})
    try:
        action = _find_anywhere(build_sprite_full_menu(shell), "网络信息")
        assert action is not None, "入口应保留位置（不是消失）"
        assert action.isEnabled() is False
        assert action.toolTip(), "置灰必须有说明"
    finally:
        shell._delete_runtime_marker()


def test_network_info_entry_enabled_by_default(tmp_path):
    shell, _instance, _config = _make_overlay(tmp_path)
    try:
        action = _find_anywhere(build_sprite_full_menu(shell), "网络信息")
        assert action is not None and action.isEnabled() is True
    finally:
        shell._delete_runtime_marker()


# ---------------------------------------------------------------- 4. 音乐服务门
def test_music_group_hidden_when_service_unavailable(tmp_path, monkeypatch):
    shell, _instance, _config = _make_overlay(tmp_path)
    try:
        assert "音乐" in _all_labels(build_sprite_full_menu(shell))
        monkeypatch.setattr(SpriteMenuFacade, "_music_host", lambda self: None)
        assert "音乐" not in _all_labels(build_sprite_full_menu(shell))
    finally:
        shell._delete_runtime_marker()


def test_music_service_host_is_the_overlay_shell(tmp_path):
    shell, _instance, _config = _make_overlay(tmp_path)
    try:
        facade = SpriteMenuFacade(shell)
        assert facade.music_service_available is True
        controller = facade.install_music_lyric()
        assert controller is shell._music_lyric
        assert shell.install_music_lyric() is controller      # 幂等
        assert facade._music_lyric is controller
    finally:
        shell.shutdown_music_lyric()
        shell._delete_runtime_marker()


# ------------------------------------------------- 桌宠设置入口（唯一一条）
def test_settings_entry_present_once(tmp_path):
    """overlay 侧「桌宠设置」恰有一条，且点它按被点 sprite 打开设置。"""
    shell, instance, config = _make_overlay(tmp_path)
    try:
        menu = build_sprite_full_menu(shell)
        entries = [action for action in menu.actions() if action.text() == "桌宠设置"]
        assert len(entries) == 1, (
            f"「桌宠设置」入口应恰有一条，实际 {len(entries)} 条")
        assert entries[0].isEnabled()
        entries[0].trigger()
        opened = [call for call in instance.shell.calls
                  if call[0] == "open_settings_process"]
        assert len(opened) == 1, f"入口未落到设置进程（实际 {opened}）"
        identity = opened[0][1][0]
        # 主宠（slot0）的档案身份 = "slot-0"（身份空间含 0）：设置进程显式带
        # ``--instance slot-0``，打开的是这只宠自己的档案，不是笼统的"主身份"。
        assert identity.config.instance_id == "slot-0", \
            "设置入口必须带上被点 sprite 的配置身份"
        menu.close()
    finally:
        shell._delete_runtime_marker()


# ---------------------------------------------------------------- 5. 点击路由
def test_menu_routes_service_semantics(tmp_path, monkeypatch):
    """逐项点击路由：每项落到窗口侧同一服务/同一配置键。"""
    shell, instance, config = _make_overlay(tmp_path)
    manager_calls: list[tuple] = []
    watcher_calls: list[str] = []
    spin_calls: list[str] = []
    reopened: list = []
    try:
        shell.agent_link_manager = type("_M", (), {
            "set_enabled": lambda self, key, on: (
                manager_calls.append((key, on)), True)[1]})()
        shell.proactive_watcher = type("_W", (), {
            "apply_config": lambda self: watcher_calls.append("apply")})()
        monkeypatch.setattr(shell, "trigger_golden_spin",
                            lambda sprite=None: spin_calls.append(sprite))
        monkeypatch.setattr(shell.overlay, "reopen_context_menu",
                            lambda menu: reopened.append(menu))

        menu = build_sprite_full_menu(shell)
        # AI 对话 → PetInstance（无聊天变体整条不显示）；「AI 设置」不在右键菜单里
        # （已并入桌宠设置，见 test_ai_settings_stays_merged_into_pet_settings_entry）
        _find(menu, "AI 对话").trigger()
        assert ("open_chat",) in instance.calls
        assert _find_anywhere(menu, "AI 设置") is None
        # 黄金回旋 → 壳的 sprite 版旋转（作用对象 = 被点 sprite；主宠菜单 = 主 sprite）
        _require(menu, "黄金回旋").trigger()
        assert spin_calls == [shell.sprite]
        # 边缘探头 → config 键（探头世界每 tick 热读）；勾选动作 = toggled
        probe = _require(menu, "边缘探头")
        probe.setChecked(True)
        assert config.get("edge_probe_enabled") is True
        # 显示本轮消费 → agent_cost_enabled（agent_link 结算时读）
        cost = _require(menu, "显示本轮消费")
        cost.setChecked(True)
        assert config.get("agent_cost_enabled") is True
        # 大小四档 → sprite scale setter + 主配置（主宠菜单：作用对象 = 主 sprite）
        size = _require(menu, "大小").menu()
        target = next(a for a in size.actions() if a.text().endswith("px"))
        target.trigger()
        px = int(target.text()[:-2])
        expected_scale = next(s for s in SCALE_STEPS
                              if int(round(CANVAS_W * s)) == px)
        assert abs(shell.sprite.scale - expected_scale) < 1e-9
        # "大小"是**每宠键**：真值落这只宠自己的档案（主宠 = config-slot-0.json），
        # 不再回写全局那份（规范 §五「一处真值」）。
        assert _slot0_scale(config) == shell.sprite.scale
        # 主动识屏 → proactive_screen 配置 + 共享监视器 apply_config
        # （Windows 专有：posix 菜单按设计不含该项，路由无从点击）
        if sys.platform == "win32":
            proactive_menu = _require(menu, "主动识屏").menu()
            _find(proactive_menu, "开启主动识屏").setChecked(True)
            assert config.get("proactive_screen", {}).get("enabled") is True
            assert watcher_calls, "主动识屏开关必须让共享监视器重读配置"
        # Agent 联动 → 共享 manager.set_enabled
        link_menu = _find(menu, "Agent 联动").menu()
        _find(link_menu, "Claude Code").setChecked(True)
        assert manager_calls == [("claude", True)]
        # 网络信息 → AppShell.open_network_info（进程级弹窗，与作用对象无关）
        _require(menu, "网络信息").trigger()
        assert "open_network_info" in [call[0] for call in instance.shell.calls]
    finally:
        shell._delete_runtime_marker()


def test_menu_agent_link_rolls_back_when_manager_refuses(tmp_path):
    """set_enabled 返回 False（拒绝授权）→ 菜单勾选态回滚。"""
    shell, _instance, _config = _make_overlay(tmp_path)

    class _Refusing:
        def set_enabled(self, key, on):
            return False

    try:
        shell.agent_link_manager = _Refusing()
        facade = SpriteMenuFacade(shell)
        action = QAction("Claude Code")
        action.setCheckable(True)
        action.setChecked(True)
        facade.toggle_agent_link("claude", True, action)
        assert action.isChecked() is False
    finally:
        shell._delete_runtime_marker()


def test_menu_exit_this_kept_for_multi_pet(tmp_path):
    shell, _instance, _config = _make_overlay(tmp_path)
    try:
        shell.spawn_pet()
        menu = build_sprite_full_menu(shell, shell._spawned[0])
        assert _find(menu, "退出这只") is not None
        labels = [a.text() for a in menu.actions()]
        assert labels.index("退出这只") < labels.index("退出")
    finally:
        shell.clear_spawned_pets()
        shell._delete_runtime_marker()


# ---------------------------------------------------------------- 6. 动画分组与收起
# 长随机动作清单样本：前缀家族结构同实机 shenshen 随机动作池（吃* 17 项、
# 余额-* / 工作状态-* / 原地* 各 6 项、放* 4 项、碎碎念-* 3 项 + 长尾）。
LONG_ACTS = (
    [f"吃{index}号" for index in range(17)]
    + [f"余额-档位{index}" for index in range(6)]
    + [f"工作状态-状态{index}" for index in range(6)]
    + [f"原地{tag}动作" for tag in ("小憩", "敲击", "跳跃", "蹲下", "重力", "旋转")]
    + [f"放{index}灯" for index in range(4)]
    + [f"碎碎念-场景{index}" for index in range(3)]
    + [f"{head}长尾动作" for head in "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉"]
)


def _animation_families(container, *, label="随机动作", collect_actions=None):
    submenu = _require(container, label).menu()
    submenu.aboutToShow.emit()
    families = {}
    for action in submenu.actions():
        child = action.menu()
        if child is None:
            continue
        child.aboutToShow.emit()
        families[action.text()] = [a.text() for a in child.actions() if not a.isSeparator()]
        if collect_actions is not None:
            collect_actions.extend(
                a for a in child.actions() if not a.isSeparator())
    return families


def test_long_animation_menu_groups_and_closes(tmp_path):
    """长随机动作清单：overlay facade 与真实 PetWindow 两条路径逐项一致。

    同时锁住 overlay 侧的两件能力：分组层级照建（facade 的素材面接得上），
    以及族内叶子带 ``closeOnTrigger``（点完动作整条菜单链收起，overlay 的
    ``_dispatch_deferred_menu_callbacks`` 负责派发挂起命令）。
    """
    shell, _instance, config = _make_overlay(tmp_path)
    win = _reference_window(config)
    try:
        win.acts = list(LONG_ACTS)
        shell.sprite.library.acts = list(LONG_ACTS)
        shell.behavior._cats_cache.clear()

        reference = _reference_menu(win)
        overlay = build_sprite_full_menu(shell)
        _assert_reference_contained(overlay, reference)

        leaves: list = []
        overlay_families = _animation_families(overlay, collect_actions=leaves)
        assert overlay_families == _animation_families(reference)
        assert set(overlay_families) == {
            "吃", "余额", "工作状态", "原地", "放", "碎碎念", "更多",
        }
        assert sum(len(members) for members in overlay_families.values()) == len(LONG_ACTS)
        assert leaves
        for action in leaves:
            assert action.property("closeOnTrigger") is True, action.text()
    finally:
        win.close()
        shell._delete_runtime_marker()


# ---------------------------------------------------------------- 7. 菜单关闭后派发
def test_close_on_trigger_commands_dispatch_after_menu_exec(tmp_path):
    """close_on_trigger 命令必须派发（此前 overlay 整类静默失效）。"""
    shell, _instance, _config = _make_overlay(tmp_path)
    calls: list[str] = []

    class _Menu:
        _deferred_callbacks = [lambda: calls.append("hidden")]

        def exec(self, *_args):
            return None

    try:
        shell.overlay._full_menu_builder = lambda target: _Menu()
        shell.overlay._dispatch_deferred_menu_callbacks(_Menu())
        assert calls == []                       # 0ms 定时器：菜单关闭后派发
        app.processEvents()
        assert calls == ["hidden"]
    finally:
        shell._delete_runtime_marker()


def test_open_full_menu_at_rebuilds_menu_at_position(tmp_path, monkeypatch):
    """原位重开：同一位置、同一被点 sprite 重新建菜单。"""
    shell, _instance, _config = _make_overlay(tmp_path)
    from PySide6.QtCore import QPoint

    seen: list = []
    shell.sprite.bind_clip("idle1")
    shell.sprite._rebuild_pixmap()
    anchor = shell.overlay.mapToGlobal(shell.sprite.rect().center())

    class _Menu:
        def exec(self, pos):
            seen.append(("exec", QPoint(pos)))

        def close(self):
            seen.append(("close", None))

        def pos(self):
            return QPoint(anchor)

    try:
        target_sprite = shell.sprite
        shell.overlay._full_menu_builder = (
            lambda target: seen.append(("build", target)) or _Menu())
        shell.overlay.reopen_context_menu(_Menu())
        assert ("close", None) in seen
        # 重开走 10ms 定时器：轮询状态 + 宽预算（不赌固定 sleep）
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            app.processEvents()
            if any(item[0] == "build" for item in seen):
                break
            time.sleep(0.005)
        builds = [item for item in seen if item[0] == "build"]
        assert builds and builds[0][1] is target_sprite
        assert any(item[0] == "exec" for item in seen)
    finally:
        shell._delete_runtime_marker()


def test_close_on_trigger_action_defers_then_dispatches(tmp_path, monkeypatch):
    """真实弹出菜单里点 close_on_trigger 条目：命令被挂起 → 关闭后派发。

    这条锁的是 overlay 此前整类静默失效的缺陷（defer_menu_callback 挂了却没人
    派发）；直接走 QMenu.popup() + action.trigger()，不 mock 挂起机制。
    挂起判据是**动作自己所在的那张菜单**可见（``connect_action``），所以这里用
    根层的「AI 对话」（子菜单里的条目在父菜单可见时不会挂起——子菜单没显示过）。
    """
    from PySide6.QtCore import QPoint

    shell, instance, _config = _make_overlay(tmp_path)
    try:
        menu = build_sprite_full_menu(shell)
        action = _find(menu, "AI 对话")
        assert action.property("closeOnTrigger") is True
        menu.popup(QPoint(20, 20))
        app.processEvents()
        assert menu.isVisible(), "offscreen 下 popup 必须真的可见（否则测不到挂起）"
        action.trigger()
        assert instance.calls == [], "菜单可见时命令必须挂起（deferred），不能同步执行"
        shell.overlay._dispatch_deferred_menu_callbacks(menu)
        app.processEvents()
        assert instance.calls == [("open_chat",)]
        menu.close()
    finally:
        shell._delete_runtime_marker()


# ---------------------------------------------------------------- 8. 菜单入口的服务等价物
def test_golden_spin_rotates_once_and_restores(tmp_path):
    """「黄金回旋」→ sprite 整帧旋转一圈（golden_spin 常量 + 缓动）。"""
    shell, _instance, _config = _make_overlay(tmp_path)
    try:
        sprite = shell.sprite
        shell.trigger_golden_spin()
        assert shell._golden_spin_timer is not None
        assert shell._golden_spin_timer.isActive()
        assert sprite.throw_rotation == 0.0
        # 中途：缓动后应落在 (0, -360) 之间（逆时针）
        shell._golden_spin_started = time.monotonic() - 0.2
        shell._advance_golden_spin()
        assert -360.0 < sprite.throw_rotation < 0.0
        # 超过单圈时长：停表 + 回正
        shell._golden_spin_started = time.monotonic() - 1.0
        shell._advance_golden_spin()
        assert not shell._golden_spin_timer.isActive()
        assert sprite.throw_rotation == 0.0
    finally:
        shell._delete_runtime_marker()


def test_golden_spin_skips_while_throw_egg_active(tmp_path):
    """探头/彩蛋在跑时不叠加旋转（PetWindow「探头激活时不叠加」同纪律）。"""
    shell, _instance, _config = _make_overlay(tmp_path)

    class _BusyEgg:
        active = True

    try:
        # 探头/彩蛋逐屏一份：本用例只有一块屏，替身挂到那块屏的宿主上
        shell._main_host.throw_egg = _BusyEgg()
        shell.trigger_golden_spin()
        assert shell._golden_spin_timer is None or not shell._golden_spin_timer.isActive()
    finally:
        shell._delete_runtime_marker()


def test_look_screen_throttles_and_syncs_reply(tmp_path, monkeypatch):
    """「看看屏幕」：忙碌/冷却提示 + worker 结果回 GUI（sync_look_to_chat）。"""
    shell, instance, _config = _make_overlay(tmp_path)
    started: list = []

    class _Thread:
        def __init__(self, *args, **kwargs):
            started.append(kwargs.get("target"))

        def start(self):
            pass

    try:
        monkeypatch.setattr("pet.overlay_shell.threading.Thread", _Thread)
        assert shell.on_look_synced is not None
        shell.show_bubble  # 确保气泡面存在（首次冒泡惰性建气泡）
        shell.look_at_screen()
        assert shell._look_busy is True
        assert started, "worker 线程必须被启动"
        # 忙碌期再点：提示而不重复起线程
        shell.look_at_screen()
        assert len(started) == 1
        # worker 回报：忙碌复位 + 同步进聊天（on_look_synced 路由）
        shell._on_look_done("我看到了浏览器", "[看看屏幕] 前台窗口：chrome", False)
        assert shell._look_busy is False
        assert ("sync_look_to_chat", "[看看屏幕] 前台窗口：chrome",
                "我看到了浏览器") in instance.calls
        # 冷却期：不重复起线程
        shell.look_at_screen()
        assert len(started) == 1
    finally:
        shell._delete_runtime_marker()


def test_music_lyric_host_degrades_without_config(tmp_path):
    """歌词宿主默认不装（配置关）→ 不起轮询线程；开启后可幂等装上。"""
    shell, _instance, config = _make_overlay(tmp_path)
    try:
        shell.sync_music_lyric()
        assert shell._music_lyric is None
        config.set("music_lyric_enabled", True)
        shell.sync_music_lyric()
        assert shell._music_lyric is not None
        shell.shutdown_music_lyric()
    finally:
        shell._delete_runtime_marker()
