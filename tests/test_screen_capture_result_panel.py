# -*- coding: utf-8 -*-
"""「快速识屏」结果浮层：可复制、Esc 可关、可拖可缩、贴边、不抢焦点（offscreen）。

面板是四条截图链路共同的落点，它的契约有五条，逐条都有对应用例：

1. **可复制**：OCR 文本 / 问答答复靠鼠标点按钮进剪贴板（主按钮是 accent 蓝）；
   文本视图本身是 ``ClickFocus``，选中一段后 **Ctrl+C 也能复制**——面板不再带
   ``WindowDoesNotAcceptFocus``（那会把"点击后激活"一起禁掉，于是键盘事件永远进不来，
   用户只能右键菜单复制，2026-10-19 实机反馈）；
2. **Esc 可关**：同上，面板拿不到键盘焦点，Esc 只能走应用级过滤器；而过滤器
   **绝不吞事件**（返回 False），所以"关面板"永远不挡主人的其它 Esc 语义；
3. **贴边**：锚在岛旁，越界夹回当刻可用几何——不许跑到屏幕外；
4. **不抢焦点**：显示本身不得激活窗口（``WA_ShowWithoutActivating``），
   否则用户正在打的字会被截断；
5. **可拖动 / 可缩放**（2026-10-14 实机反馈后加的）：标题栏拖动 + 右下角
   ``QSizeGrip``；用户调过的尺寸/位置是他要的，复用同一件面板时不许被排版没收。

纪律：offscreen，同步直调，不 sleep 赌时序。
"""

from __future__ import annotations

import re
import types

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor,
    QGuiApplication,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPalette,
)
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QLabel,
    QSizeGrip,
    QWidget,
)

from pet.screen_capture.result_panel import (  # noqa: E402
    SHORT_RESULT_LIMIT,
    SYSTEM_STYLE,
    ScreenResult,
    ScreenResultPanel,
    resolve_style,
)


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


def _escape_event() -> QKeyEvent:
    return QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)


def _drag(widget, start, end) -> None:
    """按住 ``start`` 拖到 ``end``（局部坐标；全局坐标 = 窗口位置 + 局部坐标）。

    真实现读的是 ``event.globalPosition()``，所以这里必须把两套坐标都给对：
    只给局部坐标的话"按下点"与"窗口位置"的差值算错，一次拖拽会跳着走。
    """
    origin = widget.pos()

    def _pos(local):
        return QPointF(origin.x() + local[0], origin.y() + local[1])

    press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(*start), _pos(start),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)
    move = QMouseEvent(QEvent.Type.MouseMove, QPointF(*end), _pos(end),
                       Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)
    release = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(*end), _pos(end),
                          Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                          Qt.KeyboardModifier.NoModifier)
    widget.mousePressEvent(press)
    widget.mouseMoveEvent(move)
    widget.mouseReleaseEvent(release)


def _style_rule(stylesheet: str, selector: str) -> str:
    """从 QSS 里抠出某个选择器的规则体（按选择器断言，不比对整份样式表）。"""
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", stylesheet)
    assert match is not None, f"样式表里没有 {selector} 的规则"
    return match.group(1)


def test_result_summary_is_bounded_to_the_island_line():
    """岛的短文案单行、限长——岛是"一眼"面，不承载长文本。"""
    short = ScreenResult(title="截图提取文字", primary="确定")
    assert short.summary == "确定"

    long_text = "行" * 200
    summary = ScreenResult(title="截图提取文字", primary=long_text).summary
    assert len(summary) <= SHORT_RESULT_LIMIT + 1
    assert summary.endswith("…")

    multiline = ScreenResult(title="截图问答", primary="第一行\n第二行")
    assert "\n" not in multiline.summary


def test_panel_shows_copy_and_close_widgets_for_primary_text():
    panel = ScreenResultPanel()
    panel.show_result(ScreenResult(title="截图提取文字", primary="识别到的文字",
                                  status="本地识别 · 5 字"))

    assert panel.title_text() == "截图提取文字"
    assert panel.primary_text() == "识别到的文字"
    assert panel.status_text() == "本地识别 · 5 字"
    assert panel.has_secondary() is False
    assert panel.copy_button().text() == "复制"
    # 关面板的鼠标入口（面板是防抢焦点窗，收不到键盘）：断言它真的建出来了
    assert panel.close_button().objectName() == "screen-result-close"
    panel.close_panel()


def test_copy_button_puts_the_primary_text_on_the_clipboard():
    panel = ScreenResultPanel()
    panel.show_result(ScreenResult(title="截图提取文字", primary="要复制的文字"))

    assert panel.copy_primary() is True
    assert QApplication.clipboard().text() == "要复制的文字"
    # 空文本不冒充成功（否则用户以为复制到了，粘出来却是上一次的内容）
    panel.show_result(ScreenResult(title="截图提取文字", primary="   "))
    assert panel.copy_primary() is False
    panel.close_panel()


def test_copy_is_the_accent_button(qapp):
    """复制是面板的主按钮：accent 蓝（与图里/问答浮窗同一支 #1677e8）。

    只比对"有背景色"是不够的——读 QSS 里**那条规则**，才不会因为别处
    也出现过同一个色值而自证通过。
    """
    panel = ScreenResultPanel()
    rule = _style_rule(panel.styleSheet(), "QPushButton#screen-result-copy")

    assert "#1677e8" in rule
    assert panel.copy_button().objectName() == "screen-result-copy"
    panel.close_panel()


def test_secondary_slot_appears_only_for_bilingual_results():
    """双语对照位：无译文时不占地方，有译文时两个槽各自可复制。"""
    panel = ScreenResultPanel()
    panel.show_result(ScreenResult(title="截图翻译", primary="Hello world",
                                   secondary="你好，世界"))

    assert panel.has_secondary() is True
    assert panel.secondary_text() == "你好，世界"
    assert panel.copy_button().text() == "复制原文"
    assert panel.copy_secondary_button().text() == "复制译文"
    assert panel.copy_secondary() is True
    assert QApplication.clipboard().text() == "你好，世界"

    panel.show_result(ScreenResult(title="截图翻译", primary="Hello world"))
    assert panel.has_secondary() is False
    panel.close_panel()


def test_panel_never_steals_focus_and_esc_closes_it_without_swallowing():
    panel = ScreenResultPanel()
    assert panel.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating) is True
    # 不抢焦点靠 WA_ShowWithoutActivating（显示不激活）；不再带
    # WindowDoesNotAcceptFocus——那个标志连"点击后激活"也禁掉，Ctrl+C 就没有落点。
    assert Qt.WindowType.WindowDoesNotAcceptFocus not in panel.windowFlags()

    panel.show_result(ScreenResult(title="截图提取文字", primary="内容"))
    panel.show()
    app = QApplication.instance()
    assert app is not None

    # Esc 走应用级过滤器（面板是防抢焦点窗，收不到键盘焦点）：关掉面板，
    # 但**不吞**事件——别的窗口要用 Esc（弹弓取消 / 关设置页）照旧收得到。
    # 这里按真实投递路径验：事件发给另一个窗口，过滤器顺手关面板，事件继续
    # 走到目标自己手里（probe 记到就算没被吞）。
    received: list[int] = []

    class _Probe(QWidget):
        def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
            received.append(event.key())
            super().keyPressEvent(event)

    probe = _Probe()
    probe.show()
    QApplication.sendEvent(probe, _escape_event())
    assert received == [Qt.Key.Key_Escape]
    assert panel.isVisible() is False
    probe.deleteLater()

    # 非 Esc 的按键一律直通
    other = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier)
    assert panel.eventFilter(panel, other) is False
    panel.close_panel()


def test_panel_docks_beside_the_anchor_and_stays_inside_the_screen():
    screen = QGuiApplication.primaryScreen()
    assert screen is not None
    available = screen.availableGeometry()

    anchor = QWidget()
    anchor.setGeometry(available.right() - 40, available.bottom() - 20, 40, 20)
    panel = ScreenResultPanel()
    panel.show_result(ScreenResult(title="截图问答", primary="答" * 400))
    panel.show_near(anchor)

    rect = panel.frameGeometry()
    assert rect.left() >= available.left()
    assert rect.top() >= available.top()
    assert rect.right() <= available.right()
    assert rect.bottom() <= available.bottom()
    assert panel.width() <= available.width()
    assert panel.height() <= available.height()

    # 锚缺席时也不许把面板丢到屏幕外（回落到右下角）
    panel.show_near(None)
    rect = panel.frameGeometry()
    assert available.contains(rect)
    panel.close_panel()
    anchor.deleteLater()


def test_panel_flips_above_an_anchor_that_sits_on_the_bottom_edge():
    """屏幕边缘翻转：锚（灵动岛）贴底时，面板往**上**弹而不是压出屏外。"""
    screen = QGuiApplication.primaryScreen()
    assert screen is not None
    available = screen.availableGeometry()

    anchor = QWidget()
    anchor.setGeometry(available.right() - 40, available.bottom() - 20, 40, 20)
    panel = ScreenResultPanel()
    panel.show_result(ScreenResult(title="截图问答", primary="答" * 400))
    panel.show_near(anchor)

    rect = panel.frameGeometry()
    assert rect.bottom() <= anchor.geometry().top(), "贴底的锚点没有把面板翻到上方"
    assert rect.bottom() <= available.bottom()
    panel.close_panel()
    anchor.deleteLater()


def test_the_panel_lands_below_the_selection_it_came_from(qapp):
    """定位契约（round 2 实机反馈）：给选区就落在选区正下方，不再甩到屏幕角落。

    落点是"这块图的下文"，用户的视线不用在选区和屏幕角落之间来回跳。
    """
    from pet.screen_capture.session import SelectionRect

    rect = SelectionRect(200, 200, 300, 100)
    panel = ScreenResultPanel()
    panel.show_result(ScreenResult(title="截图提取文字", primary="识别到的文字"), below=rect)

    geo = panel.geometry()
    assert geo.top() >= rect.y + rect.height, "面板压在选区上了"
    assert abs(geo.center().x() - (rect.x + rect.width // 2)) <= 2, "没有对齐选区中线"
    panel.close_panel()


def test_the_selection_beats_the_anchor_and_the_screen_corner(qapp):
    """选区优先级最高：同一次展示既给锚（岛）又给选区时，跟选区。

    （锚/角落是"没有选区上下文"时的退路——两套口径混用会让结果卡在岛边、问答窗
    在选区边，用户看到的是同一张图的两个落点。）
    """
    from pet.screen_capture.session import SelectionRect

    screen = QGuiApplication.primaryScreen()
    assert screen is not None
    available = screen.availableGeometry()
    anchor = QWidget()
    anchor.setGeometry(available.right() - 40, available.bottom() - 20, 40, 20)
    rect = SelectionRect(120, 120, 260, 80)

    panel = ScreenResultPanel()
    panel.show_result(ScreenResult(title="截图提取文字", primary="识别到的文字"),
                      anchor, below=rect)

    geo = panel.geometry()
    assert abs(geo.center().x() - (rect.x + rect.width // 2)) <= 2
    assert geo.top() >= rect.y + rect.height
    panel.close_panel()
    anchor.deleteLater()


def test_empty_result_renders_without_crashing():
    """空输入（引擎没认到文字 / 引擎缺席）：面板照旧能开，复制如实返回 False。"""
    panel = ScreenResultPanel()
    panel.show_result(ScreenResult(title="截图提取文字", status="本地文字识别引擎尚未接入"))
    assert panel.primary_text() == ""
    assert panel.secondary_text() == ""
    assert panel.has_secondary() is False
    assert panel.copy_primary() is False
    assert panel.status_text() == "本地文字识别引擎尚未接入"
    panel.close_panel()


def test_panel_height_tracks_the_content():
    """定高按内容实测，**复用同一件**照样跟随：涨、封顶、落都钉住。

    面板是复用件（presenter 只造一件，每张结果原地换内容），所以这条必须在
    **同一个实例**上量——"每次换张新的量一量"会把复用路径整个漏掉。

    2026-10-14 起面板有最小高度（``QSizeGrip`` 要抓得住一个像样的窗口），所以
    "空结果比一行结果矮"这条老断言不再成立：地板以下的一切都停在地板上。

    （无裁切由同一份度量保证，视觉证据是离屏截图 probe_panel_ocr.png：
    四行中文完整可见、没有滚动条。）

    历史：这条曾经真红过——首张结果是矮结果时，实例被钉死在 140 px，后面的长文本
    只能靠滚动条读（``show_near`` 的 ``_applying_size`` 挡不住首次 show 的补投递
    resize，排版尺寸被当成了"用户拉的尺寸"）。现在那一环已修好，这条用例连
    "再换回短结果要收回去"一起钉住，防止再退回去。
    """
    panel = ScreenResultPanel()
    floor = panel.minimumHeight()

    panel.show_result(ScreenResult(title="截图提取文字", status="本地文字识别引擎尚未接入"))
    empty_h = panel.height()
    panel.show_result(ScreenResult(title="截图提取文字", primary="一行文字"))
    one_line_h = panel.height()
    panel.show_result(ScreenResult(title="截图提取文字", primary="行\n" * 8))
    many_lines_h = panel.height()
    panel.show_result(ScreenResult(title="截图问答", primary="答" * 4000))
    capped_h = panel.height()
    panel.show_result(ScreenResult(title="截图提取文字", primary="一行文字"))
    shrunk_h = panel.height()

    assert empty_h == one_line_h == floor, "地板以内的短内容都停在最小高度"
    assert one_line_h < many_lines_h <= capped_h
    assert capped_h <= 240 + 120            # 上限附近，不无限拉高
    assert shrunk_h == one_line_h, "复用同一件时尺寸没跟着内容收回来"
    panel.close_panel()


def test_the_continue_button_needs_an_image_and_hands_it_over_with_the_text(qapp):
    """「继续提问…」是把结果卡接回问答浮窗的入口（图 + 已识别文字 + **这张卡自己的**选区）。

    三条都要钉：没有图时**不出现**（没有上下文的"继续问"是空转）；有图时点了要把
    （图, 正文, 本卡选区）原样交出去——面板自己不打开浮窗，那是编排层的事；选区必须
    是**这张卡片自己那份**（跨截图返修：A 的卡片摆着时用户又截了 B，交出去的仍旧要是
    A 的图与选区），所以它随卡片记录，不许回调方回头去问"最近一次采集的选区"。
    """
    seen: list[tuple] = []
    panel = ScreenResultPanel()
    image = QImage(12, 9, QImage.Format.Format_RGB32)
    image.fill(QColor(1, 2, 3))
    below = types.SimpleNamespace(x=10, y=20, width=30, height=40)

    panel.show_result(ScreenResult(title="截图提取文字", primary="识别到的文字"))
    assert panel.continue_button().isVisibleTo(panel) is False

    panel.show_result(ScreenResult(title="截图提取文字", primary="识别到的文字", image=image),
                      below=below,
                      on_continue_ask=lambda img, text, sel: seen.append((img, text, sel)))
    assert panel.continue_button().isVisibleTo(panel) is True
    panel.continue_button().click()

    assert seen == [(image, "识别到的文字", below)]
    panel.close_panel()


# ------------------------------------------------- 渐进式结果卡片（2026-10-14 round 3）


def _loading(panel):
    """面板上的加载动画控件（objectName 固定；画的**内容**随美术迭代）。

    曾经是三点跳动，现在是项目吉祥物的小鱼摆尾——所以用例只钉"这个控件在、
    计时器在跑、它真的画了东西"，不钉具体形状。
    """
    area = panel.findChild(QWidget, "screen-result-loading")
    assert area is not None, "面板上没有加载动画控件"
    return area


def test_a_loading_result_shows_the_animation_and_hides_the_actions(qapp):
    """过程态卡片：正文区摆加载动画，复制/语言行/状态文字一律收起。

    实机反馈"等待白屏像卡死"：等待期间要给"在跑"的信号，而不是一句文案。
    """
    panel = ScreenResultPanel()
    panel.show_result(ScreenResult(title="截图翻译", status="正在翻译…", force_panel=True,
                                   loading=True, language_selector=True),
                      on_language_change=lambda _target: None)

    animation = _loading(panel)
    assert animation.isVisibleTo(panel) is True
    assert animation._timer.isActive() is True, "过程态的动画必须真的在跑"
    assert panel.copy_button().isVisibleTo(panel) is False
    assert panel.language_row().isVisibleTo(panel) is False
    status = panel.findChild(QLabel, "screen-result-status")
    assert status.isVisibleTo(panel) is False, "文案位让给动画（有字也不摆）"
    assert panel.continue_button().isVisibleTo(panel) is False
    panel.close_panel()


def test_a_loading_result_actually_paints_something(qapp):
    """不只是摆了个空控件：动画真的画在面板上（否则"等待白屏"照旧）。

    只断"控件可见 + 计时器在跑"会漏掉"画了个空白"这种失败——那时动画是个空框，
    用户看到的仍是白屏，正是本轮要解决的问题。（实测踩到过：美术换了一版动画，
    ``paintEvent`` 里用了一个没 import 的符号，控件在、计时器在跑、一个像素没画。）
    """
    panel = ScreenResultPanel()
    panel.show_result(ScreenResult(title="截图提取文字", force_panel=True, loading=True))
    panel.show()

    canvas = _render(panel, background=QColor(255, 255, 255))
    area = _loading(panel).geometry()
    pixels = [canvas.pixelColor(x, y).getRgb()
              for x in range(area.x(), area.x() + area.width())
              for y in range(area.y(), area.y() + area.height())]

    assert any(pixel != (255, 255, 255, 255) for pixel in pixels), "动画区是一片空白"
    accent = QColor("#1677e8").getRgb()
    assert any(pixel[:3] == accent[:3] for pixel in pixels), "没画出产品的 accent 色"
    panel.close_panel()


def test_a_loading_result_keeps_the_primary_text_it_already_has(qapp):
    """翻译第二段：原文已就位就留着（只是复制按钮收起），动画摆在它下面。"""
    panel = ScreenResultPanel()
    panel.show_result(ScreenResult(title="截图翻译", primary="Hello world",
                                   primary_label="原文", force_panel=True, loading=True))

    assert panel.primary_text() == "Hello world"
    assert panel.primary_text() != ""
    assert _loading(panel).isVisibleTo(panel) is True
    panel.close_panel()


def test_the_terminal_result_stops_and_hides_the_animation(qapp):
    """终态到了：动画停下来并收起（留着滴答就是白耗电）。"""
    panel = ScreenResultPanel()
    panel.show_result(ScreenResult(title="截图提取文字", force_panel=True, loading=True))
    animation = _loading(panel)
    assert animation._timer.isActive() is True

    panel.show_result(ScreenResult(title="截图提取文字", primary="识别到的文字",
                                   force_panel=True))

    assert animation.isVisibleTo(panel) is False
    assert animation._timer.isActive() is False, "出结果还在后台滴答"
    assert panel.copy_button().isVisibleTo(panel) is True
    panel.close_panel()


def test_hiding_the_panel_stops_the_animation(qapp):
    """面板收起（Esc / 继续提问 / 手动关）时定时器必须停：不留后台空转。"""
    panel = ScreenResultPanel()
    panel.show_result(ScreenResult(title="截图提取文字", force_panel=True, loading=True))
    animation = _loading(panel)
    assert animation._timer.isActive() is True

    panel.close_panel()

    assert animation._timer.isActive() is False


def test_hiding_the_card_keeps_its_round_so_a_covered_card_is_still_the_same_round(
        qapp, monkeypatch):
    """卡片被收起（面板关掉 / 被别的链路的终态顶下去）只停表，**轮次身份留着**。

    共用面板的覆盖行为（重翻的终态摆上来会把采集那张过程态卡顶下去）不该被读成
    "采集换了件事"：同一轮再来卡片时不许重抽（中途换脸＝用户以为换了件事）；
    真正结束这一轮的是本轮的终态结果或显式收口（见 ``end_loading``）。
    """
    from pet.screen_capture import brand_icons, result_panel as rp

    jingwa, whale = brand_icons.load_jingwa(), brand_icons.load_ds_whale()
    order = [jingwa, whale]
    rolls: list[int] = []
    monkeypatch.setattr(rp, "pick_loading_icon",
                        lambda: rolls.append(len(rolls)) or order[len(rolls) - 1])

    panel = ScreenResultPanel()
    animation = _loading(panel)
    panel.show_result(ScreenResult(title="截图翻译", force_panel=True, loading=True),
                      round_key=7)
    assert len(rolls) == 1 and animation._current_icon() is jingwa
    assert animation._timer.isActive() is True

    panel.close_panel()          # 卡片离开屏幕（用户 Esc / 被别的终态顶下去）
    assert animation._timer.isActive() is False, "收起之后还在后台滴答"

    panel.show_result(ScreenResult(title="截图翻译", primary="原文",
                                   force_panel=True, loading=True), round_key=7)
    assert len(rolls) == 1, "同一轮中途被收起过就重抽了"
    assert animation._current_icon() is jingwa, "同一轮中途换了脸"
    assert animation._timer.isActive() is True

    panel.show_result(ScreenResult(title="截图翻译", primary="原文", force_panel=True),
                      round_key=7)     # 终态：这一轮到此收口
    panel.show_result(ScreenResult(title="截图提取文字", force_panel=True, loading=True),
                      round_key=8)
    assert len(rolls) == 2, "终态之后的下一轮没有重抽"
    assert animation._current_icon() is whale
    panel.close_panel()


# ------------------------------------------------------------ 语言行（round 3）


def test_the_language_row_needs_both_the_flag_and_a_callback(qapp):
    """「译为」语言行：只有翻译结果（``language_selector``）**且**编排层给了回调才出现。"""
    panel = ScreenResultPanel()
    translated = ScreenResult(title="截图翻译", primary="Hello world",
                              secondary="你好，世界", language_selector=True,
                              force_panel=True)

    panel.show_result(translated)
    assert panel.language_row().isVisibleTo(panel) is False, "没有回调就没有可切换的目标"

    panel.show_result(ScreenResult(title="截图提取文字", primary="识别到的文字",
                                   force_panel=True),
                      on_language_change=lambda _target: None)
    assert panel.language_row().isVisibleTo(panel) is False, "不是翻译结果就不出语言行"

    panel.show_result(translated, on_language_change=lambda _target: None)
    assert panel.language_row().isVisibleTo(panel) is True
    combo = panel.language_combo()
    assert isinstance(combo, QComboBox)
    assert [combo.itemData(index) for index in range(combo.count())] == [
        "auto", "中文", "English", "日本語"]
    assert combo.currentData() == "auto", "缺省是自动检测"
    panel.close_panel()


def test_switching_the_language_reports_the_key(qapp):
    """切换语言行 = 换翻译目标：回调拿到的是 combo 的 ``currentData``（key）。"""
    seen: list[str] = []
    panel = ScreenResultPanel()
    panel.show_result(ScreenResult(title="截图翻译", primary="Hello world",
                                   secondary="你好，世界", language_selector=True,
                                   force_panel=True),
                      on_language_change=seen.append)

    combo = panel.language_combo()
    combo.setCurrentIndex(2)          # English

    assert seen == ["English"]
    assert combo.currentData() == "English"
    panel.close_panel()


def test_the_language_row_hides_while_loading(qapp):
    """重翻时（过程态）语言行收起：正在跑的时候不给用户再切一次的机会。"""
    seen: list[str] = []
    panel = ScreenResultPanel()
    panel.show_result(ScreenResult(title="截图翻译", primary="Hello world",
                                   language_selector=True, force_panel=True, loading=True),
                      on_language_change=seen.append)

    assert panel.language_row().isVisibleTo(panel) is False
    assert _loading(panel).isVisibleTo(panel) is True
    panel.close_panel()


def test_close_panel_detaches_the_escape_filter_and_can_be_reused():
    """面板是复用件：关掉之后过滤器要摘干净，再展示一次照旧好使。"""
    panel = ScreenResultPanel()
    panel.show_result(ScreenResult(title="截图提取文字", primary="第一次"))
    panel.show()
    panel.close_panel()
    assert panel.isVisible() is False

    panel.show_result(ScreenResult(title="截图提取文字", primary="第二次"))
    panel.show()
    assert panel.isVisible() is True
    assert panel.primary_text() == "第二次"
    assert panel.eventFilter(panel, _escape_event()) is False
    assert panel.isVisible() is False
    panel.close_panel()


# ------------------------------------------------------- 拖动 / 缩放（2026-10-14）


def test_panel_has_a_translucent_background_and_a_size_grip():
    """真圆角靠"透明窗口 + paintEvent 手画的圆角卡片"；缩放入口是右下角的 ``QSizeGrip``。"""
    panel = ScreenResultPanel()

    assert panel.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) is True
    grip = panel.findChild(QSizeGrip)
    assert grip is not None, "面板右下角没有可抓的缩放手柄"
    assert grip.isVisibleTo(panel) is True
    panel.close_panel()


def test_the_title_bar_drags_the_panel(qapp):
    """标题栏按下即可拖动整块面板（标题/状态标签对鼠标透明，事件落到面板本体）。"""
    panel = ScreenResultPanel()
    panel.show_result(ScreenResult(title="截图提取文字", primary="内容"))
    panel.show()
    before = panel.pos()

    _drag(panel, (12, 10), (60, 40))

    assert panel.pos() - before == QPoint(48, 30)
    panel.close_panel()


def test_the_panel_has_no_maximum_width_cap(qapp):
    """``_MAX_WIDTH`` 上限取消：用户把面板拉宽不该被悄悄夹回去。"""
    panel = ScreenResultPanel()
    panel.show_result(ScreenResult(title="截图提取文字", primary="内容"))
    panel.show()

    panel.resize(700, 200)

    assert panel.maximumWidth() > 700, "宽度又被写死上限了"
    assert panel.width() == 700
    panel.close_panel()


def test_a_size_the_user_pulled_survives_the_next_result(qapp):
    """grip 拉过的尺寸在下一张结果上沿用——面板是复用件，不该没收用户的调整。

    ``QSizeGrip`` 拖完的结果就是一次 ``resize()``，所以这里直接 ``resize`` 模拟。
    反过来那一半由 ``test_panel_height_tracks_the_content`` 钉住：**排版自己算出来的
    尺寸不许被记成"用户拉的"**（记错了实例就被钉死在首张的尺寸上）。
    """
    panel = ScreenResultPanel()
    panel.show_result(ScreenResult(title="截图提取文字", primary="第一张"))
    panel.show()
    panel.resize(420, 260)

    panel.show_result(ScreenResult(title="截图问答", primary="第二张更长的内容" * 20))

    assert (panel.width(), panel.height()) == (420, 260)
    panel.close_panel()


# ------------------------------------------------- 主题（2026-10-13 交叉审查 m2）


def _palette(window: QColor):
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, window)
    return palette


def test_style_follows_the_host_palette_unless_it_is_pinned(qapp):
    """``_STYLES["light"]`` 以前是死代码（style 缺省写死 dark，没人传得进来）。

    现在缺省/``system`` 按宿主调色板判——判据与菜单层同一条（窗口底色亮度）。
    """
    dark_host = _palette(QColor(24, 26, 32))
    light_host = _palette(QColor(245, 246, 248))

    assert resolve_style(None, dark_host) == "dark"
    assert resolve_style(SYSTEM_STYLE, light_host) == "light"
    assert resolve_style("", light_host) == "light"
    # 明确指定的一律照用（宿主是暗色也让用户/宿主选浅色）
    assert resolve_style("light", dark_host) == "light"
    assert resolve_style("dark", light_host) == "dark"
    # 不认识的值不许抛：面板样式不该成为一次截图失败的理由
    assert resolve_style("neon", light_host) == "light"


def test_light_panel_is_reachable_and_renders_the_light_palette(qapp):
    """真圆角 + 主题：卡片是**画出来**的，所以按渲染像素验，不按样式表验。

    （``WA_TranslucentBackground`` 一开，QSS 的 ``background`` 不再落像素——
    卡片底色/描边改由 ``paintEvent`` 手画。写样式表字符串的那版断言曾自证通过，
    实际屏幕上是什么颜色它一句话也没说。）
    """
    light = ScreenResultPanel(style="light")
    dark = ScreenResultPanel(style="dark")

    assert light.style == "light" and dark.style == "dark"
    assert light.styleSheet() != dark.styleSheet()

    light.show_result(ScreenResult(title="截图提取文字", primary="浅色宿主上的结果"))
    assert light.primary_text() == "浅色宿主上的结果"

    assert _card_color(light) == QColor("#ffffff").getRgb()
    assert _card_color(dark) == QColor("#1c1e26").getRgb()
    light.close_panel()
    dark.close_panel()


def _render(widget, background: QColor | None = None) -> QImage:
    canvas = QImage(widget.size(), QImage.Format.Format_ARGB32)
    canvas.fill(background if background is not None else QColor(0, 0, 0, 0))
    painter = QPainter(canvas)
    widget.render(painter, QPoint(0, 0))
    painter.end()
    return canvas


def _card_color(panel) -> tuple:
    return _render(panel).pixelColor(panel.width() // 2, panel.height() // 2).getRgb()


def test_the_card_is_a_rounded_translucent_corner(qapp):
    """角上必须是透的：圆角靠"窗口透明 + 手画圆角矩形"成立。

    不透明背景会把圆角吃成直角白盒（实机"白盒"观感之源），所以这条按像素验：
    角落留得住底下画布的底色、中间是卡片色。
    """
    panel = ScreenResultPanel(style="dark")
    panel.show_result(ScreenResult(title="截图提取文字", primary="内容"))

    canvas = _render(panel, background=QColor(255, 0, 255))

    assert canvas.pixelColor(0, 0).getRgb() == (255, 0, 255, 255), "角上被涂实了，圆角没成形"
    assert canvas.pixelColor(panel.width() // 2, panel.height() // 2).getRgb() != (255, 0, 255, 255)
    panel.close_panel()


# ------------------------------------------- 加载动画的品牌图标（每次加载抽一次）


def _is_ink(rgb: tuple, background: tuple = (255, 255, 255), tolerance: int = 24) -> bool:
    """像素算不算"画了东西"：给抗锯齿的花边留一点容差，别把极淡的 fringe 算进去。"""
    return sum(abs(a - b) for a, b in zip(rgb[:3], background)) > tolerance


def _ink_bbox(canvas: QImage, exclude: tuple | None = None,
              background: tuple = (255, 255, 255)) -> tuple[int, int, int, int]:
    """画布上的墨迹包围盒 ``(x, y, w, h)``（背景是一整片纯色时量画面尺寸）。

    ``exclude`` 是 ``(x, y, w, h)`` 的排除区：加载动画的泡泡浮在图标上方，
    量图标本身时要把那一片让出去。
    """
    points = []
    for x in range(canvas.width()):
        for y in range(canvas.height()):
            if exclude is not None:
                ex, ey, ew, eh = exclude
                if ex <= x < ex + ew and ey <= y < ey + eh:
                    continue
            if _is_ink(canvas.pixelColor(x, y).getRgb(), background):
                points.append((x, y))
    assert points, "动画一个像素都没画"
    xs = [x for x, _y in points]
    ys = [y for _x, y in points]
    return min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1


def _white_host(width: int = 240, height: int = 64) -> QWidget:
    """白底宿主：动画挂上去再渲染——顶层窗会被系统填底色，量的就不是它画的像素。"""
    host = QWidget()
    host.resize(width, height)
    palette = host.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(255, 255, 255))
    host.setPalette(palette)
    host.setAutoFillBackground(True)
    return host


def _alpha_bbox(pixmap, threshold: int = 8) -> tuple[int, int, int, int] | None:
    """素材自己画了东西的范围（物理像素）——非方形素材的不透明边界常有一圈内缩。"""
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    xs, ys = [], []
    for x in range(image.width()):
        for y in range(image.height()):
            if ((image.pixel(x, y) >> 24) & 0xFF) > threshold:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1


def test_a_loading_round_rolls_once_and_only_a_new_round_rolls_again(qapp, monkeypatch):
    """轮次口径：换身份才抽一次，同身份的 start（过程更新）不抽，收口后才重抽。

    整次加载保持所选的那一只——旧实现按相位在两只之间轮换，所以这里把相位推到
    老的换场点（3.1 ≈ 1.2s）再问一次，轮播一回来就红。
    """
    from pet.screen_capture import brand_icons, result_panel as rp

    jingwa, whale = brand_icons.load_jingwa(), brand_icons.load_ds_whale()
    order = [jingwa, whale]
    rolls: list[int] = []
    monkeypatch.setattr(rp, "pick_loading_icon",
                        lambda: rolls.append(len(rolls)) or order[len(rolls) - 1])

    fish = rp._LoadingFish()
    fish.resize(240, 64)
    assert fish._current_icon().isNull() is True, "没开始加载就不该有图标"

    fish.start()
    assert len(rolls) == 1, "开始一轮加载要抽一次图标"
    assert fish._current_icon() is jingwa

    fish.start()               # 同一轮里的过程更新（如第二张过程态卡片）
    assert len(rolls) == 1, "同轮的过程更新重抽了图标"
    assert fish._current_icon() is jingwa, "同一轮中途换了脸"

    for _ in range(40):
        fish._tick()
    assert len(rolls) == 1, "tick 重抽了图标"
    fish._phase = 3.2          # 旧实现的换场点（3.1 ≈ 1.2s）
    assert fish._current_icon() is jingwa, "相位把图标轮换回来了"

    fish.stop(round_key=None)   # 轮次收口（终态结果 / 显式 end_loading 都走它）
    fish.start(round_key=None)  # 下一轮：重新抽
    assert len(rolls) == 2, "新一轮没有重抽"
    assert fish._current_icon() is whale
    fish.stop(round_key=None)


def test_a_rounds_identity_decides_who_rolls_and_who_may_end_it(qapp, monkeypatch):
    """轮次身份（``round_key``）的两条口径：换身份＝换了件事；收口只收自己的轮。

    两条都是真实故障面的产物：

    - **上一轮没来得及收口**（失败/取消只有一个"失败理由"、没有终态结果可摆到面板上）：
      新的独立操作必须重新抽，不许继承上一轮的脸；
    - **别人的轮收不得**：重翻链路的收口不许把采集链路还在跑的那一轮连图一起收掉
      （两条链路共用一块面板，各自的轮次身份互不覆盖）。
    """
    from pet.screen_capture import brand_icons, result_panel as rp

    jingwa, whale = brand_icons.load_jingwa(), brand_icons.load_ds_whale()
    order = [jingwa, whale]
    rolls: list[int] = []
    monkeypatch.setattr(rp, "pick_loading_icon",
                        lambda: rolls.append(len(rolls)) or order[len(rolls) - 1])

    panel = ScreenResultPanel()
    animation = _loading(panel)

    panel.show_result(ScreenResult(title="截图提取文字", force_panel=True, loading=True),
                      round_key=1)
    assert len(rolls) == 1 and animation._current_icon() is jingwa

    # 上一轮没收口（旧实现的失败/取消故障面）：新身份照样重新抽，不继承上一轮的脸
    panel.show_result(ScreenResult(title="截图翻译", force_panel=True, loading=True),
                      round_key=2)
    assert len(rolls) == 2, "换了轮次身份却没有重抽"
    assert animation._current_icon() is whale

    # 收口只收自己的轮：别人的身份收不掉它（连可见性一起不动）
    assert panel.end_loading(1) is False, "别人的轮被收掉了"
    assert animation._timer.isActive() is True, "别人的收口把在跑的动画停了"
    assert animation.isVisible() is True
    assert animation._current_icon() is whale

    assert panel.end_loading(2) is True
    assert animation._timer.isActive() is False
    assert animation.isVisible() is False
    panel.close_panel()


def _round_presenter():
    """真落点（展示归属的**唯一**权威）＋真面板——面板不再自判归属，所以这两条走落点。

    （跨截图返修 P2 二轮：判据从面板搬到 ``ScreenCapturePresenter``。它必须先判再通知、
    也得连不上面板的短结果一起判，所以"谁能改这块屏"的用例必须打在这一层；面板侧只剩
    "照着摆"。）
    """
    from pet.screen_capture.actions import ScreenCapturePresenter

    return ScreenCapturePresenter(panel_factory=ScreenResultPanel)


def test_a_superseded_rounds_result_cannot_overwrite_the_card_that_took_over(qapp, monkeypatch):
    """展示归属：共用面板同一时刻只认**最新到过的那一轮**——旧终态不许覆写正文、不许停表。

    真实故障（跨截图返修 P1）：两条链路共用一块面板。A 的翻译结果摆着 → 用户切语言触发
    重翻 → 重翻还在路上时用户又截了第二张图，第二张的过程态卡片已经占住面板 → A 的旧
    终态回来。旧实现先写正文（把第二张的卡片写成 A 的原文/译文、摆出 A 的译文槽与状态行）
    **再**判轮次归属，连第二张还在转的加载动画一起收掉：用户看到的是一张静止的旧结果，
    第二张"正在跑"没有任何痕迹。
    """
    from pet.screen_capture import brand_icons, result_panel as rp

    jingwa, whale = brand_icons.load_jingwa(), brand_icons.load_ds_whale()
    order = [jingwa, whale]
    rolls: list[int] = []
    monkeypatch.setattr(rp, "pick_loading_icon",
                        lambda: rolls.append(len(rolls)) or order[len(rolls) - 1])

    presenter = _round_presenter()
    panel = presenter.panel()
    animation = _loading(panel)
    assert presenter.show(
        ScreenResult(title="截图翻译", primary="A 原文", primary_label="原文",
                     force_panel=True, loading=True), round_key=1) is panel
    assert presenter.show(
        ScreenResult(title="截图提取文字", force_panel=True, loading=True),
        round_key=2) is panel
    assert animation._current_icon() is whale and animation._timer.isActive() is True

    stale = ScreenResult(title="截图翻译", primary="A 原文", secondary="A 译文",
                         primary_label="原文", status="已翻译 · 3 字",
                         force_panel=True, language_selector=True)
    assert presenter.show(stale, round_key=1) is None, "旧终态没有被判掉"

    assert panel.title_text() == "截图提取文字", "旧终态把第二张的卡片改了标题"
    assert panel.primary_text() == "", "旧终态把正文覆写成了自己那一轮的文字"
    assert panel.secondary_text() == "" and panel.has_secondary() is False, "旧终态摆出了译文"
    assert panel.status_text() == "", "旧终态把状态行占了"
    assert animation.isVisible() is True, "旧终态把第二张的加载卡收掉了"
    assert animation._timer.isActive() is True, "旧终态把第二张还在跑的表停了"
    assert animation._current_icon() is whale, "旧终态给第二张换了脸"
    assert len(rolls) == 2, "旧终态让面板重抽了图标"

    # 本轮自己的终态照常落地——守卫挡的是"已经被顶下去的那一轮"，不是"一轮只许摆一次"
    assert presenter.show(
        ScreenResult(title="截图提取文字", primary="B 文字", status="本地识别 · 3 字",
                     force_panel=True), round_key=2) is panel
    assert panel.primary_text() == "B 文字"
    assert animation._timer.isActive() is False
    panel.close_panel()


def test_the_display_guard_only_drops_rounds_a_newer_one_already_took_over(qapp):
    """守卫的边界：只挡"已经被更新的轮次顶下去"的卡片，别的照旧展示。

    五种"别的情形"逐条钉：① 身份第一次出现就是终态（引擎缺席的出口就是这样，没有过程态
    卡片）照常展示；② 同一轮的重复更新照常展示（翻译链路先"正在识别"再"正在翻译"）；
    ③ 用户把卡片关掉之后，旧轮次不许把它拉回来，而**新的一轮**照常弹出；④ 排不了序的
    身份（非整数，非预期输入）放行且不记；⑤ 缺省（``None``）＝匿名，不参与归属、照旧
    展示——落点不假设调用方一定按编号发号。
    """
    presenter = _round_presenter()
    panel = presenter.panel()

    assert presenter.show(ScreenResult(title="截图提取文字", primary="第一次",
                                       force_panel=True), round_key=5) is panel
    assert presenter.show(ScreenResult(title="截图提取文字", primary="第二次",
                                       force_panel=True), round_key=5) is panel
    assert panel.primary_text() == "第二次", "同一轮的更新被丢了"

    assert presenter.show(ScreenResult(title="截图翻译", force_panel=True, loading=True),
                          round_key=6) is panel
    panel.close_panel()
    assert presenter.show(ScreenResult(title="截图提取文字", primary="旧轮次",
                                       force_panel=True), round_key=5) is None
    assert panel.isVisible() is False, "旧轮次的终态把用户关掉的卡片拉回来了"
    assert presenter.show(ScreenResult(title="截图提取文字", primary="新轮次",
                                       force_panel=True), round_key=7) is panel
    assert panel.isVisible() is True and panel.primary_text() == "新轮次"

    assert presenter.show(ScreenResult(title="截图", primary="排不了序",
                                       force_panel=True), round_key=["a"]) is panel
    assert panel.primary_text() == "排不了序"

    assert presenter.show(ScreenResult(title="截图", primary="匿名",
                                       force_panel=True)) is panel
    assert panel.primary_text() == "匿名"
    panel.close_panel()


def test_consecutive_loading_cards_on_the_same_panel_are_one_round(qapp, monkeypatch):
    """翻译链路先"正在识别"再"正在翻译"：两张过程态卡片是同一轮，中途不许换脸。

    （这条纠正了上一版把"连续 show_result 即算新轮"当契约的写法——真实链路
    ``actions._run_translate`` 一次操作连发两张过程态卡片，那是过程更新。）
    """
    from pet.screen_capture import brand_icons, result_panel as rp

    jingwa, whale = brand_icons.load_jingwa(), brand_icons.load_ds_whale()
    order = [jingwa, whale]
    rolls: list[int] = []
    monkeypatch.setattr(rp, "pick_loading_icon",
                        lambda: rolls.append(len(rolls)) or order[len(rolls) - 1])

    panel = ScreenResultPanel()
    animation = _loading(panel)
    panel.show_result(ScreenResult(title="截图翻译", force_panel=True, loading=True))
    assert animation._current_icon() is jingwa and len(rolls) == 1
    assert animation._timer.isActive() is True

    panel.show_result(ScreenResult(title="截图翻译", primary="Hello world",
                                   primary_label="原文", force_panel=True, loading=True))
    assert len(rolls) == 1, "同一轮的第二张过程态卡片重抽了图标"
    assert animation._current_icon() is jingwa, "同一轮中途换了脸"

    panel.show_result(ScreenResult(title="截图翻译", primary="Hello world", force_panel=True))
    assert len(rolls) == 1, "终态不该再抽图标"

    panel.show_result(ScreenResult(title="截图问答", force_panel=True, loading=True))
    assert len(rolls) == 2, "终态之后的下一轮没有重抽"
    assert animation._current_icon() is whale
    panel.close_panel()


def test_one_translate_operation_keeps_one_icon_across_its_two_loading_cards(qapp, monkeypatch):
    """真实链路回归：一次 ``_run_translate`` 只许抽一次图标。

    路径是 ``ScreenCaptureActions._run_translate → ScreenCapturePresenter →
    ScreenResultPanel``（真面板、真加载动画），只把 OCR 与网络翻译引擎换成假件
    （外加 release 假件，免去碰真 OCR 引擎的释放）。
    """
    from PySide6.QtCore import QObject, Signal
    from pet.screen_capture import result_panel as rp
    from pet.screen_capture.actions import (ACTION_TRANSLATE, ScreenCaptureActions,
                                            ScreenCapturePresenter)
    from pet.screen_capture.brand_icons import load_ds_whale, load_jingwa
    from pet.screen_capture.session import SelectionRect

    class _FakeController(QObject):
        """采集闭环替身：只提供编排层要连的三条信号（不在测试里跑真采集）。"""

        captured = Signal(object, object, object)
        cancelled = Signal(str)
        failed = Signal(str)

        def start(self, **_kwargs) -> bool:
            return True

    class _FakeHost:
        """宿主替身：没有岛、没有气泡、没有 watcher——过程态只落结果面板。"""

        cfg = {"context_menu_appearance": {"theme": "light"}}

    jingwa, whale = load_jingwa(), load_ds_whale()
    order = [jingwa, whale]
    rolls: list[int] = []
    monkeypatch.setattr(rp, "pick_loading_icon",
                        lambda: rolls.append(len(rolls)) or order[len(rolls) - 1])

    shown: list[bool] = []
    faces: list = []                 # 每张卡片摆完之后面板上那张脸（收口后为空图）
    real_show_result = rp.ScreenResultPanel.show_result

    def spying_show_result(self, result, *args, **kwargs):
        shown.append(bool(result.loading))
        try:
            return real_show_result(self, result, *args, **kwargs)
        finally:
            faces.append(self._loading._current_icon())

    monkeypatch.setattr(rp.ScreenResultPanel, "show_result", spying_show_result)

    controller = _FakeController()
    presenter = ScreenCapturePresenter(style="light")
    actions = ScreenCaptureActions(
        _FakeHost(), controller=controller, presenter=presenter,
        ocr=lambda _image: "Hello world",
        translator=lambda _image, _source: "你好，世界",
        release=lambda: None, run_inline=True,
    )
    image = QImage(12, 9, QImage.Format.Format_RGB32)
    image.fill(QColor(3, 4, 5))
    selection = SelectionRect(200, 200, 300, 100)

    assert actions.run() is True
    controller.captured.emit(image, selection, ACTION_TRANSLATE)

    panel = presenter.panel()
    assert panel is not None, "过程态应该开出结果面板"
    animation = _loading(panel)
    # 前提：这一次操作真的连发了两张过程态卡片（识别完再翻译），紧接着一张终态
    assert shown == [True, True, False], f"这次操作的过程态序列不是预期：{shown}"
    assert len(rolls) == 1, "一次操作里抽了多次图标（中途换脸）"
    # 两张卡片**在屏上时**是同一张脸（终态把这一轮收口，脸随之清掉）
    assert faces[0] is jingwa and faces[1] is jingwa, f"同一轮中途换了脸：{faces}"
    assert faces[2].isNull() is True, "轮次收口后还留着上一轮的脸"
    assert animation._timer.isActive() is False, "终态之后动画还在滴答"

    for _ in range(20):
        animation._tick()
    assert len(rolls) == 1, "tick 重抽了图标"

    # 下一次操作（上一轮已被终态停掉）＝ 新的一轮：重新抽
    assert actions.run() is True
    controller.captured.emit(image, selection, ACTION_TRANSLATE)

    assert shown == [True, True, False, True, True, False]
    assert len(rolls) == 2, "下一次操作没有重新抽"
    assert faces[3] is whale and faces[4] is whale, f"新一轮两张卡片的脸不是同一只：{faces}"
    panel.close_panel()


def test_the_loading_roll_is_jingwa_only_below_one_in_ten(qapp, monkeypatch):
    """抽取口径：``random() < 0.1`` 出鲸蛙，0.1 及以上一律 DS logo（边界逐点钉）。"""
    from pet.screen_capture import brand_icons

    jingwa, whale = brand_icons.load_jingwa(), brand_icons.load_ds_whale()
    assert jingwa.isNull() is False and whale.isNull() is False, "两只素材都应随包在场"

    for roll, expected in ((0.0, jingwa), (0.09999, jingwa), (0.1, whale), (0.999, whale)):
        monkeypatch.setattr(brand_icons.random, "random", lambda roll=roll: roll)
        assert brand_icons.pick_loading_icon() is expected, f"roll={roll}"


def test_the_loading_roll_falls_back_to_the_ds_logo_without_the_jingwa_asset(qapp, monkeypatch):
    """鲸蛙素材缺席：不该掷出空帧，直接给 DS logo；两只都缺席才回空图。"""
    from PySide6.QtGui import QPixmap
    from pet.screen_capture import brand_icons

    whale = brand_icons.load_ds_whale()
    monkeypatch.setattr(brand_icons, "load_jingwa", lambda: QPixmap())
    monkeypatch.setattr(brand_icons.random, "random", lambda: 0.0)   # 本该抽中鲸蛙
    assert brand_icons.pick_loading_icon() is whale

    monkeypatch.setattr(brand_icons, "load_ds_whale", lambda: QPixmap())
    assert brand_icons.pick_loading_icon().isNull() is True


def test_the_loading_animation_falls_back_to_dots_without_assets(qapp, monkeypatch):
    """两只素材全缺席：回退三点跳动（动画自己绝不许成为故障点）。"""
    from PySide6.QtGui import QPixmap
    from pet.screen_capture import result_panel as rp

    monkeypatch.setattr(rp, "pick_loading_icon", lambda: QPixmap())
    fish = rp._LoadingFish()
    fish.resize(240, 64)
    fish.start()

    canvas = _render(fish, background=QColor(255, 255, 255))
    accent = QColor("#1677e8").getRgb()
    assert any(canvas.pixelColor(x, y).getRgb()[:3] == accent[:3]
               for x in range(canvas.width())
               for y in range(canvas.height())), "回退态一片空白"
    fish.stop(round_key=None)


def test_the_loading_icon_keeps_its_box_size_at_any_dpr(qapp, monkeypatch):
    """HiDPI：素材的 DPR 不改变画面尺寸（方形素材照样画满 44 px 的槽）。

    按物理像素直接落图会让 2x 素材缩成半个槽——所以这里两种 DPR 各画一次，
    比画面上的实际宽度（动画挂在白底宿主里，量的才是它自己画的像素）。
    """
    from PySide6.QtGui import QPixmap
    from pet.screen_capture import result_panel as rp

    widths = []
    for dpr in (1.0, 2.0):
        src = QPixmap(int(64 * dpr), int(64 * dpr))
        src.fill(QColor("#1677e8"))
        src.setDevicePixelRatio(dpr)
        monkeypatch.setattr(rp, "pick_loading_icon", lambda src=src: src)

        host = _white_host()
        fish = rp._LoadingFish(host)
        fish.setGeometry(0, 0, 240, 64)
        fish.start()
        widths.append(_ink_bbox(_render(host))[2])
        fish.stop(round_key=None)
        host.deleteLater()

    assert widths[0] == widths[1], f"DPR 改变了画面尺寸：{widths}"
    assert 40 <= widths[0] <= 48, f"图标没有画满 44 px 的槽：{widths}"


def test_the_non_square_icon_is_fitted_without_distortion(qapp, monkeypatch):
    """真实渲染：847×682 的鲸蛙等比塞进方槽并居中——不许被强制拉成正方。

    期望值由素材自己那圈不透明范围映射进槽里算出来（不写死像素数）；旧实现按
    44×44 硬拉伸，高度会顶到 ~42（宽高比从 1.25 掉到 1.05）。
    """
    from pet.screen_capture import brand_icons, result_panel as rp

    jingwa = brand_icons.load_jingwa()
    assert jingwa.isNull() is False
    source = jingwa.deviceIndependentSize()
    assert source.width() != source.height(), "夹具不是非方形素材"
    content = _alpha_bbox(jingwa)
    assert content is not None, "素材里没有不透明像素"
    bx, by, bw, bh = content

    box = rp._ICON_BOX
    ratio = box / max(source.width(), source.height())
    expected_w, expected_h = bw * ratio, bh * ratio
    # 槽心与 paintEvent 的 (cx, cy) 同一条：宽的一半 / 高的一半 + 10
    expected_cx = 240 / 2 + (bx + bw / 2 - source.width() / 2) * ratio
    expected_cy = 64 / 2 + 10 + (by + bh / 2 - source.height() / 2) * ratio

    monkeypatch.setattr(rp, "pick_loading_icon", lambda: jingwa)
    host = _white_host()
    fish = rp._LoadingFish(host)
    fish.setGeometry(0, 0, 240, 64)
    fish.start()
    canvas = _render(host)
    fish.stop(round_key=None)

    # 泡泡浮在图标上方（x 106..122 / y 0..22 那一片），量图标本身要让开
    x, y, width, height = _ink_bbox(canvas, exclude=(103, 0, 21, 24))
    drawn_aspect = width / height
    content_aspect = bw / bh

    assert abs(width - expected_w) <= 2, f"宽度没等比塞满方槽：{width} vs {expected_w:.2f}"
    assert abs(height - expected_h) <= 2, f"高度不是等比（被拉伸了？）：{height} vs {expected_h:.2f}"
    assert abs(drawn_aspect - content_aspect) < abs(drawn_aspect - 1.0), \
        f"画面被拉成了正方：{drawn_aspect:.3f}（素材宽高比 {content_aspect:.3f}）"
    assert abs((x + width / 2) - expected_cx) <= 2, "图标没有横向居中"
    assert abs((y + height / 2) - expected_cy) <= 2, "图标没有纵向居中"
    host.deleteLater()


def test_the_whole_icon_is_drawn_not_just_one_corner(qapp, monkeypatch):
    """整张图都要画出来：2x 素材的源矩形必须按**物理像素**取全图。

    ``drawPixmap(target, pixmap, sourceRect)`` 的 sourceRect 是物理像素，若按设备
    无关尺寸给（2x 素材的一半），画面上只剩左上那 1/4。纯色素材验不出这条（取错
    也是同一个颜色铺满框），所以用四象限四种颜色，按画面上四个象限中心的颜色断言。
    """
    from PySide6.QtGui import QPainter, QPixmap
    from pet.screen_capture import result_panel as rp

    quadrants = {"tl": "#ff0000", "tr": "#00c000", "bl": "#0040ff", "br": "#ffd000"}

    for dpr in (2.0, 1.0):
        side = int(88 * dpr)                      # 逻辑 88×88（比方槽大一倍）
        image = QImage(side, side, QImage.Format.Format_RGB32)
        half = side // 2
        painter = QPainter(image)
        painter.fillRect(0, 0, half, half, QColor(quadrants["tl"]))
        painter.fillRect(half, 0, half, half, QColor(quadrants["tr"]))
        painter.fillRect(0, half, half, half, QColor(quadrants["bl"]))
        painter.fillRect(half, half, half, half, QColor(quadrants["br"]))
        painter.end()
        source = QPixmap.fromImage(image)
        source.setDevicePixelRatio(dpr)
        assert source.deviceIndependentSize().width() == 88

        monkeypatch.setattr(rp, "pick_loading_icon", lambda source=source: source)
        host = _white_host()
        fish = rp._LoadingFish(host)
        fish.setGeometry(0, 0, 240, 64)
        fish.start()
        canvas = _render(host)
        fish.stop(round_key=None)

        # 等比后是 44×44 的正方槽（槽心 120, 42）：四个象限的中心各取一点
        quarter = rp._ICON_BOX / 4
        samples = {
            "tl": (int(120 - quarter), int(42 - quarter)),
            "tr": (int(120 + quarter), int(42 - quarter)),
            "bl": (int(120 - quarter), int(42 + quarter)),
            "br": (int(120 + quarter), int(42 + quarter)),
        }
        drawn = {name: canvas.pixelColor(*point).getRgb()[:3] for name, point in samples.items()}

        for name, (x, y) in samples.items():
            expected = QColor(quadrants[name]).getRgb()[:3]
            actual = drawn[name]
            assert all(abs(a - b) <= 8 for a, b in zip(actual, expected)), \
                f"dpr={dpr} 时 {name} 象限颜色是 {actual}，期望 {expected}（整图 {drawn}）"
        assert len(set(drawn.values())) == 4, f"四个象限被画成了同一种颜色：{drawn}"
        host.deleteLater()


# ------------------------------------------- 素材加载（候选序与缓存）


def test_a_broken_first_candidate_falls_through_to_the_next_one(qapp, monkeypatch, tmp_path):
    """首候选"文件在、解码失败"不许就地收工——要继续试下一个候选。

    冻结包（``_MEIPASS``）里混进半截下载 / 错格式的图标时，旧实现一看文件存在
    就 break：源码树里那只好的永远试不到，图标从此缺席。这里把坏图放在首候选，
    好图留在源码树候选上。
    """
    from PySide6.QtGui import QPixmap
    from pet.screen_capture import brand_icons

    good = brand_icons.load_ds_whale()
    assert good.isNull() is False, "源码树里的 DS logo 素材应当在场"

    frozen = tmp_path / "frozen"
    (frozen / "assets" / "chat").mkdir(parents=True)
    broken = frozen / "assets" / "chat" / "ds_whale.png"
    broken.write_bytes(b"\x89PNG\r\n\x1a\n truncated payload")
    assert broken.is_file() is True
    assert QPixmap(str(broken)).isNull() is True, "夹具不是一张解不开的图"

    monkeypatch.setattr(brand_icons, "_CACHE", {})
    monkeypatch.setattr(brand_icons.sys, "_MEIPASS", str(frozen), raising=False)

    loaded = brand_icons.load_ds_whale()

    assert loaded.isNull() is False, "首候选解码失败后没有回退到下一个候选"
    assert loaded.size() == good.size(), "回退拿到的不是源码树那只素材"
    assert brand_icons.load_ds_whale() is loaded, "回退结果没有进缓存"


def test_all_candidates_broken_gives_the_null_pixmap_and_only_tries_once(qapp, monkeypatch,
                                                                        tmp_path):
    """所有候选都解不开：如实给空图（调用方回退三点跳动），且只试一次。"""
    from pet.screen_capture import brand_icons

    frozen = tmp_path / "frozen" / "assets" / "chat"
    frozen.mkdir(parents=True)
    (frozen / "jingwa.png").write_bytes(b"\x89PNG\r\n\x1a\n truncated payload")
    source_tree = tmp_path / "pkg" / "assets" / "chat"
    source_tree.mkdir(parents=True)
    (source_tree / "jingwa.png").write_bytes(b"still not a png")
    # 源码树候选 = Path(__file__).parents[2]/assets/chat：挪走 __file__ 就是挪走候选
    fake_module = tmp_path / "pkg" / "pet" / "screen_capture" / "brand_icons.py"
    fake_module.parent.mkdir(parents=True)
    fake_module.write_text("", encoding="utf-8")

    decoded: list[str] = []
    real_pixmap = brand_icons.QPixmap

    def counting(path=None):
        if path is not None:
            decoded.append(str(path))
        return real_pixmap() if path is None else real_pixmap(path)

    monkeypatch.setattr(brand_icons, "_CACHE", {})
    monkeypatch.setattr(brand_icons, "__file__", str(fake_module))
    monkeypatch.setattr(brand_icons.sys, "_MEIPASS", str(tmp_path / "frozen"), raising=False)
    monkeypatch.setattr(brand_icons, "QPixmap", counting)

    assert brand_icons.load_jingwa().isNull() is True, "坏素材该给空图而不是抛异常"
    assert len(decoded) == 2, f"两个候选各试一次：{decoded}"

    assert brand_icons.load_jingwa().isNull() is True
    assert len(decoded) == 2, f"空图没有进缓存，又去解码了：{decoded}"


def test_the_decoded_pixmap_is_cached_and_never_decoded_twice(qapp, monkeypatch):
    """缓存：第二次调用直接用缓存（不再碰磁盘、不再解码一次）。"""
    from pet.screen_capture import brand_icons

    monkeypatch.setattr(brand_icons, "_CACHE", {})
    decoded: list[str] = []
    real_pixmap = brand_icons.QPixmap

    def counting(path=None):
        if path is not None:
            decoded.append(str(path))
        return real_pixmap() if path is None else real_pixmap(path)

    monkeypatch.setattr(brand_icons, "QPixmap", counting)

    first = brand_icons.load_jingwa()
    second = brand_icons.load_jingwa()

    assert first.isNull() is False
    assert second is first, "第二次调用没有走缓存"
    assert len(decoded) == 1, f"同一只素材被解码了 {len(decoded)} 次：{decoded}"
