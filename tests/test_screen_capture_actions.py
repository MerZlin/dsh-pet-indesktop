# -*- coding: utf-8 -*-
"""「快速识屏」四条动作链路的编排（假 OCR / 假问答浮窗 / 假采集闭环，offscreen）。

产品面是**采集 → 框选选动作 → 引擎/出口**。动作不在入口定死：覆盖层动作条在松手
后给出动作，随 ``captured`` 的第三个参数回到这里分发。四条链路的差别只在中间那步：

| 动作 | 中间那一步 | 隐私口径 |
|---|---|---|
| 提取文字 | 本地 OCR | 纯本地，绝不联网、不确认 |
| 翻译 | OCR（原文）+ 聊天模型文本互译 | 与问答同一条聊天服务；没配就如实说明，不留假结果 |
| 复制图像 | 系统剪贴板 | 纯本地 |
| 截图问答 | 打开问答浮窗（图进输入栏） | **发端点在浮窗里**：用户亲手点「发送」才出网 |

问答那条的确认环节 2026-10-14 前移了：以前是"框选 → 系统 Yes/No → 直接发图拿答案"，
现在框选只打开浮窗，发不发、发什么，都由用户在浮窗里决定（浮窗即确认面）。所以本
文件不再有"拒绝确认"用例——那条语义搬到了 ``test_screen_capture_ask_dialog.py``
（不点发送就不联网），这里留下的是编排层仍然欠的两条：**打开浮窗一个字节都不发**、
**浮窗打不开就如实报错**（绝不退化成"静默成功"或"直接发出去"）。

另有两条横切纪律，逐条有用例：

- **物理隔离**：采集会话期间暂停主动识屏 watcher，会话结束只恢复到"此前配置
  允许的状态"——此前没在跑就不许被这次截图唤醒（对称，不是无条件 resume）；
- **一次一条**：已经在采集中时第二次触发被拒，不许叠第二层覆盖层。

纪律：offscreen，假宿主同步直调；唯一涉及真线程的用例走事件同步 + 宽预算
（``QSignalSpy.wait`` 在"信号已先到"时会返回 False，那是个会随线程调度翻面的假红），
不 sleep 赌时序。
"""

from __future__ import annotations

import sys
import threading
import time
import types

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from pet.screen_capture import actions as actions_mod  # noqa: E402
from pet.screen_capture.actions import (  # noqa: E402
    ACTION_ASK,
    ACTION_COPY,
    ACTION_OCR,
    ACTION_TRANSLATE,
    ScreenCaptureActions,
    ScreenCapturePresenter,
)
from pet.screen_capture.result_panel import ScreenResult, ScreenResultPanel  # noqa: E402
from pet.screen_capture.session import SelectionRect  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


def _image() -> QImage:
    img = QImage(8, 8, QImage.Format.Format_RGB888)
    img.fill(0x203040)
    return img


def _second_image() -> QImage:
    """第二张互不相同的选区图（跨截图串用的用例必须能靠它分辨"交付的是哪一张"）。

    尺寸与颜色都与 ``_image()`` 不同：断言用的是对象同一性（``is``），这张图只是让
    "两张图不一样"在失败信息里也看得见。
    """
    img = QImage(13, 7, QImage.Format.Format_RGB888)
    img.fill(0x607080)
    return img


#: 假闭环默认框到的那块选区（结果面/问答窗要跟到它下方）。
_SELECTION = SelectionRect(120, 200, 300, 160)

#: 第二块互不相同的选区（同上；跨截图用例要能分辨"用的是哪一次框选的区域"）。
_SELECTION_B = SelectionRect(420, 260, 180, 90)


# ------------------------------------------------------------------ 假件


class _FakeController(QObject):
    """假采集闭环：按脚本把三条出口之一发出来（同步，钉死顺序）。

    ``action`` 就是覆盖层动作条上那一下选择（默认等价于按 Enter 的提取文字）；
    ``selection`` 是框到的那块选区（真控制器给的是 SelectionRect——编排层现在要
    把它记住，好让结果面/问答窗落在选区下方）。真控制器只放行动作闭集
    （见 ``test_screen_capture_controller.py``），这里放开是为了能单独验分发表。
    """

    captured = Signal(object, object, object)
    cancelled = Signal(str)
    failed = Signal(str)

    def __init__(self, *, mode: str = "captured", action: str = ACTION_OCR,
                 reason: str = "esc", selection=None) -> None:
        super().__init__()
        self.mode = mode
        self.action = action
        self.reason = reason
        self.selection = _SELECTION if selection is None else selection
        self.starts = 0
        self.emit_on_start = True

    def start(self) -> bool:
        self.starts += 1
        if self.starts > 1:
            return False
        if self.emit_on_start:
            self.emit()
        return True

    def emit(self) -> None:
        image = _image()
        if self.mode == "captured":
            self.captured.emit(image, self.selection, self.action)
        elif self.mode == "cancelled":
            self.cancelled.emit(self.reason)
        else:
            self.failed.emit("grab-empty")


class _FakeAskDialog:
    """问答浮窗替身：记录"被打开过、收到哪张图、上下文与该跟的选区"。

    （发送与否不归编排层管——浮窗自己负责，见 ``test_screen_capture_ask_dialog.py``。）
    """

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple] = []      # [(图, 上下文文字, below 选区)]
        self.fail = fail

    def start_with_image(self, image, context_text: str = "", below=None) -> None:
        if self.fail:
            raise RuntimeError("浮窗炸了")
        self.calls.append((image, str(context_text), below))

    @property
    def images(self) -> list:
        return [image for image, _text, _below in self.calls]

    @property
    def belows(self) -> list:
        return [below for _image, _text, below in self.calls]


class _FakeWatcher:
    """主动识屏 watcher 的替身：``resume()`` 与真实实现同义（= apply_config）。"""

    def __init__(self, *, running: bool = True, allowed: bool = True) -> None:
        self.running = running
        self.allowed = allowed          # 配置层面的"允许跑"
        self.pauses = 0
        self.resumes = 0
        self.apply_configs = 0

    def is_running(self) -> bool:
        return self.running

    def pause(self) -> None:
        self.pauses += 1
        self.running = False

    def resume(self) -> None:
        self.resumes += 1
        self.apply_config()

    def apply_config(self) -> None:
        self.apply_configs += 1
        self.running = self.allowed


class _FakePanel:
    """结果面板替身：只记"被关过几次"（「继续提问」要先把结果卡收掉）。"""

    def __init__(self) -> None:
        self.closes = 0

    def close_panel(self) -> None:
        self.closes += 1


def _loading_cards(presenter) -> list[ScreenResult]:
    """过程态卡片（"正在跑"的占位，不是结果）。"""
    return [result for result in presenter.results if result.loading]


def _finals(presenter) -> list[ScreenResult]:
    """终态结果（过程态卡片不算——它只是等待期间摆着的那张）。"""
    return [result for result in presenter.results if not result.loading]


def _final(presenter) -> ScreenResult:
    finals = _finals(presenter)
    assert finals, f"没有任何终态结果（presenter 只收到 {len(presenter.results)} 张过程态）"
    return finals[-1]


class _RecordingPresenter:
    """结果落点替身：状态/结果照收，同时记下每一张结果该落在哪个选区下方。

    ``results`` 里**过程态与终态都在**（``loading`` 区分）：presenter 的调用序列
    本身就是契约的一部分（实机反馈"等待白屏像卡死"就是靠过程态解决的）。
    """

    def __init__(self, *, panel=None) -> None:
        self.statuses: list[str] = []
        self.results: list[ScreenResult] = []
        self.belows: list = []            # 每次 show 带下来的 below（选区矩形）
        self.rounds: list = []            # 每次 show 带下来的轮次身份
        self.ended: list = []             # 每次显式收口带的轮次身份
        self._panel = panel

    def status(self, text: str) -> None:
        self.statuses.append(str(text))

    def show(self, result: ScreenResult, below=None, round_key=None):
        self.results.append(result)
        self.belows.append(below)
        self.rounds.append(round_key)
        return None

    def end_loading(self, round_key=None) -> bool:
        self.ended.append(round_key)
        return True

    def panel(self):
        return self._panel


class _FakeIsland(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []
        self.events: list[str] = []
        self.setVisible(True)

    def set_last_message(self, text: str) -> None:
        self.messages.append(str(text))

    def notify_event(self, kind: str = "generic") -> None:
        self.events.append(str(kind))


class _FakeHost:
    def __init__(self, *, island=None, watcher=None) -> None:
        self._island = island
        self.proactive_watcher = watcher
        self.bubbles: list[str] = []

    def screen_capture_island(self):
        return self._island

    def show_bubble(self, text, duration_ms: int = 3200, **_ignored) -> bool:
        self.bubbles.append(str(text))
        return True


def _make_actions(**kwargs) -> ScreenCaptureActions:
    controller = kwargs.pop("controller", None) or _FakeController()
    presenter = kwargs.pop("presenter", None) or _RecordingPresenter()
    host = kwargs.pop("host", None) or _FakeHost()
    kwargs.setdefault("run_inline", True)
    return ScreenCaptureActions(host, controller=controller, presenter=presenter, **kwargs)


def _start(action: str, **kwargs) -> ScreenCaptureActions:
    """跑一轮：假闭环按动作条的选择 ``action`` 收口（＝旧用例里的 ``run(ACTION_X)``）。"""
    kwargs.setdefault("controller", _FakeController(action=action))
    obj = _make_actions(**kwargs)
    assert obj.run() is True
    return obj


# ------------------------------------------------------------------ 提取文字


def test_ocr_action_feeds_the_selection_to_the_local_engine_and_shows_it(monkeypatch):
    seen: list[object] = []
    presenter = _RecordingPresenter()

    def fake_ocr(image):
        seen.append(image)
        return "识别到的文字"

    _start(ACTION_OCR, ocr=fake_ocr, presenter=presenter)

    assert len(seen) == 1 and isinstance(seen[0], QImage)
    assert len(_finals(presenter)) == 1
    result = _final(presenter)
    assert result.title == "截图提取文字"
    assert result.primary == "识别到的文字"
    assert result.secondary == ""
    assert result.force_panel is True          # 要能复制，必须上面板
    assert "本地" in result.status


def test_ocr_action_reports_an_honest_reason_when_no_engine_is_wired(monkeypatch):
    """引擎缺席不许冒充成功：如实说明 + 不编结果（切片 2 落地前就是这个态）。"""
    monkeypatch.setattr(actions_mod, "resolve_ocr_engine", lambda: None)
    presenter = _RecordingPresenter()
    action = _make_actions(controller=_FakeController(action=ACTION_OCR),
                           ocr=None, presenter=presenter)

    assert action.run() is True
    assert action.busy is False
    assert _loading_cards(presenter) == [], "引擎都不在，没有等待可言，不该先摆过程态"
    assert len(_finals(presenter)) == 1
    result = _final(presenter)
    assert result.primary == ""
    assert "尚未接入" in result.status


def test_ocr_action_shows_a_loading_card_before_the_result():
    """秒级等待不许白屏（实机反馈"中间什么都没有像卡死"）：过程态卡片先摆着。

    过程态 = ``ScreenResult(loading=True)``：**不发 result_ready**（它不是结果，
    只是"正在跑"的占位——最终结果来了原地换内容，面板是复用件）。
    """
    presenter = _RecordingPresenter()
    action = _make_actions(controller=_FakeController(action=ACTION_OCR),
                           ocr=lambda _img: "识别到的文字", presenter=presenter)
    ready: list[ScreenResult] = []
    action.result_ready.connect(ready.append)

    assert action.run() is True

    cards = presenter.results
    assert len(cards) == 2, f"应是「过程态 → 终态」两张，实际 {len(cards)}"
    assert cards[0].loading is True and cards[0].title == "截图提取文字"
    assert cards[0].primary == "" and cards[0].status == "", "过程态不摆文字（文案位给动画）"
    assert cards[1].loading is False and cards[1].primary == "识别到的文字"
    assert ready == [cards[1]], "过程态不许发 result_ready（它不是结果）"
    assert action.busy is False


# ------------------------------------------------------------------ 翻译


def test_translate_action_keeps_the_source_and_says_the_service_is_unavailable():
    """没有配置面就没有翻译器：原文照旧拿得到，状态行如实说"要配 AI 服务"。

    （旧契约里翻译引擎是"后续片"、恒缺席；现在翻译真的接了，缺席的唯一原因是
    宿主没有配置面——文案随之从"将在后续片接入"改成 ``TRANSLATION_UNAVAILABLE_REASON``。
    ``resolve_translator`` 已不是这条链路的入口：装配走 ``_build_translator()``。）
    """
    presenter = _RecordingPresenter()

    _start(ACTION_TRANSLATE, ocr=lambda _img: "Hello world", translator=None,
           presenter=presenter)

    result = _final(presenter)
    assert result.title == "截图翻译"
    assert result.primary == "Hello world"      # 原文照旧拿得到
    assert result.secondary == ""               # 没有翻译器就不摆假译文
    assert result.status == actions_mod.TRANSLATION_UNAVAILABLE_REASON
    assert result.primary_label == "原文"       # 原文先摆出来，不是只剩一句状态
    assert result.language_selector is False, "没有译文就没有可切换的语言行"


def test_translate_action_fills_both_bilingual_slots_with_an_engine():
    calls: list[tuple[object, str]] = []

    def fake_translate(image, source):
        calls.append((image, source))
        return "你好，世界"

    presenter = _RecordingPresenter()
    _start(ACTION_TRANSLATE, ocr=lambda _img: "Hello world", translator=fake_translate,
           presenter=presenter)

    assert calls and calls[0][1] == "Hello world"
    result = _final(presenter)
    assert result.primary == "Hello world"
    assert result.secondary == "你好，世界"
    assert result.language_selector is True, "有译文就该给「译为」语言行"


def test_the_translation_engine_is_the_configured_chat_model(monkeypatch):
    """翻译真的接通了：装机默认翻译器 = ``translate.translate_text``（纯文本聊天调用）。

    打桩打在**模块函数**上（不是网络）：这里验的是编排层把识别出的原文与宿主配置
    一起交给它，再把译文填进第二槽——翻译走哪条端点/用什么 Key 是 ``translate.py``
    自己的事（见 ``test_screen_capture_translate.py``）。
    """
    from pet.screen_capture import translate as translate_mod

    seen: list[tuple] = []
    monkeypatch.setattr(translate_mod, "translate_text",
                        lambda text, config: seen.append((text, config)) or "Hello world")

    host = _FakeHost()
    config = {"context_menu_appearance": {"theme": "light"}}
    host.cfg = config
    presenter = _RecordingPresenter()

    _start(ACTION_TRANSLATE, host=host, ocr=lambda _img: "你好，世界", presenter=presenter)

    assert seen == [("你好，世界", config)]
    result = _final(presenter)
    assert result.primary == "你好，世界"
    assert result.secondary == "Hello world"
    assert "已翻译" in result.status


def test_translate_action_does_not_spin_the_engine_on_an_empty_recognition():
    """没识别到文字就不去翻译：省一次网络往返，状态行如实说明。"""
    calls: list = []
    presenter = _RecordingPresenter()

    _start(ACTION_TRANSLATE, ocr=lambda _img: "   ",
           translator=lambda image, source: calls.append((image, source)) or "不该发生",
           presenter=presenter)

    assert calls == [], "原文是空的还去调翻译＝白花一次网络往返"
    assert len(_loading_cards(presenter)) == 1, "识别阶段的过程态照旧要摆（等待不白屏）"
    result = _final(presenter)
    assert result.primary == "" and result.secondary == ""
    assert result.status == "没有识别到文字，无可翻译"
    assert result.primary_label == ""
    assert result.language_selector is False


def test_translate_action_reports_an_empty_translation_honestly():
    """翻译器在、但没给出译文：说"没有拿到译文"，原文照旧交给用户（不编译文）。"""
    presenter = _RecordingPresenter()

    _start(ACTION_TRANSLATE, ocr=lambda _img: "Hello world",
           translator=lambda _img, _src: "   ", presenter=presenter)

    result = _final(presenter)
    assert result.primary == "Hello world"
    assert result.secondary == ""
    assert result.status == "没有拿到译文"
    assert result.primary_label == ""
    assert result.language_selector is False, "没拿到译文就没有语言行"


def test_translate_action_shows_the_source_before_the_translation():
    """两段式：识别中也摆卡片，原文一到就先上卡片（带原文），再跑翻译。

    三段序列本身就是"等待不白屏"的实现：用户先看到"在识别"，再看到"原文已就位、
    正在翻译"，最后才是双语终态。
    """
    presenter = _RecordingPresenter()

    _start(ACTION_TRANSLATE, ocr=lambda _img: "Hello world",
           translator=lambda _img, _src: "你好，世界", presenter=presenter)

    cards = presenter.results
    assert len(cards) == 3, f"应是「识别中 → 翻译中(带原文) → 终态」三张，实际 {len(cards)}"
    assert cards[0].loading is True and cards[0].primary == ""
    assert cards[1].loading is True, "翻译阶段也要摆过程态"
    assert cards[1].primary == "Hello world" and cards[1].primary_label == "原文"
    assert cards[2].loading is False
    assert (cards[2].primary, cards[2].secondary) == ("Hello world", "你好，世界")


def test_retranslate_reuses_the_cached_source_and_never_reruns_ocr(monkeypatch):
    """语言行切换：用缓存的原文直接重翻（**OCR 不重跑**），面板原地更新成新译文。

    "OCR 不重跑"是这条链路的全部意义：重新识别既慢、又可能给出另一段原文——用户
    只是换了个目标语言，却拿到"另一张图"的翻译。
    """
    from pet.screen_capture import translate as translate_mod

    calls: list[tuple] = []
    monkeypatch.setattr(
        translate_mod, "translate_text",
        lambda text, config, *, target=None: calls.append((text, target)) or f"[{target}] 你好")

    host = _FakeHost()
    host.cfg = {"context_menu_appearance": {"theme": "light"}}
    ocr_calls: list = []
    presenter = _RecordingPresenter()
    action = ScreenCaptureActions(
        host, controller=_FakeController(action=ACTION_TRANSLATE),
        ocr=lambda img: ocr_calls.append(img) or "Hello world",
        presenter=presenter, run_inline=True)

    assert action.run() is True
    assert calls == [("Hello world", None)], "首轮走自动判定（不传 target）"

    action._retranslate("日本語")

    assert len(ocr_calls) == 1, "切语言不许重跑 OCR"
    assert calls == [("Hello world", None), ("Hello world", "日本語")]
    result = _final(presenter)
    assert result.primary == "Hello world"
    assert result.secondary == "[日本語] 你好"
    assert result.language_selector is True
    assert action.busy is False


def test_retranslate_does_nothing_without_a_cached_source():
    """还没有原文可复用（没翻译过）时不许空跑：一张卡片都不摆、一次请求都不发。"""
    presenter = _RecordingPresenter()
    action = _make_actions(controller=_FakeController(action=ACTION_TRANSLATE),
                           presenter=presenter)

    action._retranslate("English")

    assert presenter.results == []


# ------------------------------------------------------------------ 复制图像


def test_copy_action_puts_the_selection_image_on_the_clipboard(qapp):
    """复制图像：框到的那块图直接进系统剪贴板，纯本地、不惊动任何引擎。"""
    ocr_calls: list[object] = []
    presenter = _RecordingPresenter()
    clipboard = QApplication.clipboard()
    clipboard.clear()

    action = _start(ACTION_COPY, ocr=lambda img: ocr_calls.append(img) or "x",
                    presenter=presenter)

    image = clipboard.image()
    assert image.isNull() is False, "剪贴板里没有图"
    assert (image.width(), image.height()) == (8, 8)
    assert image.pixelColor(0, 0).getRgb()[:3] == (0x20, 0x30, 0x40)
    # Electron 系应用（Kimi/微信/浏览器）对纯 DIB 的转换会软化/偏色：
    # 剪贴板必须同时带无损 PNG 供它们优先取用（PixPin 粘出来清晰就靠这条）。
    formats = clipboard.mimeData().formats()
    assert "image/png" in formats, f"剪贴板缺 image/png 格式（只有 {formats}）"
    assert bytes(clipboard.mimeData().data("image/png")).startswith(b"\x89PNG")
    assert ocr_calls == [], "复制是纯本地动作，不许顺手跑引擎"
    assert presenter.results == [], "复制不是「结果」，是一句状态"
    assert any("已复制到剪贴板" in text for text in presenter.statuses)
    assert action.busy is False
    clipboard.clear()


def test_copy_failure_is_reported_honestly(qapp, monkeypatch):
    """剪贴板写不进去（被别的程序占着）＝如实说失败，绝不假装"已复制"。"""
    class _BrokenClipboard:
        def setImage(self, _image):  # noqa: N802 - 对齐 Qt 命名
            raise RuntimeError("剪贴板被占用")

        def setMimeData(self, _mime):  # noqa: N802 - 对齐 Qt 命名
            raise RuntimeError("剪贴板被占用")

    monkeypatch.setattr(QApplication, "clipboard", staticmethod(lambda: _BrokenClipboard()))
    presenter = _RecordingPresenter()
    failures: list[str] = []
    action = _make_actions(controller=_FakeController(action=ACTION_COPY),
                           presenter=presenter)
    action.failed.connect(failures.append)

    assert action.run() is True

    assert failures == ["剪贴板被占用"]
    assert any("没能复制" in text for text in presenter.statuses)
    assert all("已复制" not in text for text in presenter.statuses), "失败不许冒充成功"
    assert action.busy is False


# ------------------------------------------------------------------ 问答


def test_ask_action_opens_the_dialog_and_hands_it_the_selection_image():
    """打开浮窗一个字节都不发：发端点在浮窗里（用户点「发送」那一刻）。"""
    dialog = _FakeAskDialog()
    presenter = _RecordingPresenter()
    action = _start(ACTION_ASK, ask_dialog=dialog, presenter=presenter)

    assert len(dialog.images) == 1 and isinstance(dialog.images[0], QImage)
    assert presenter.results == [], "答案在浮窗里，不是一次性的结果面"
    assert action.busy is False


def test_ask_action_does_not_hold_the_busy_slot_while_the_dialog_is_open():
    """浮窗是独立生命周期的对话面：采集收口就把 busy 交还，还能再框一块。"""
    controller = _FakeController(action=ACTION_ASK)
    dialog = _FakeAskDialog()
    action = _make_actions(controller=controller, ask_dialog=dialog)

    assert action.run() is True
    assert action.busy is False

    controller.starts = 0                   # 上一轮已收口，假件按"允许下一轮"复位
    assert action.run() is True
    assert len(dialog.images) == 2


def test_ask_action_reports_an_honest_reason_when_the_channel_is_unavailable():
    """浮窗建不起来（宿主没有配置面）＝如实上报，不许静默什么也不做。"""
    presenter = _RecordingPresenter()
    action = _make_actions(controller=_FakeController(action=ACTION_ASK),
                           presenter=presenter)
    assert action._get_ask_dialog() is None, "宿主没有配置面时不该硬造一个浮窗"

    assert action.run() is True

    result = _final(presenter)
    assert result.title == "截图问答"
    assert result.primary == ""
    assert "不可用" in result.status
    assert result.force_panel is True
    assert action.busy is False


def test_ask_action_reports_it_when_the_dialog_itself_breaks():
    """浮窗自己炸了（配置坏了/父窗没了）＝把原因给用户看，绝不退化成静默成功。"""
    presenter = _RecordingPresenter()
    action = _start(ACTION_ASK, ask_dialog=_FakeAskDialog(fail=True), presenter=presenter)

    assert len(_finals(presenter)) == 1
    result = _final(presenter)
    assert result.force_panel is True
    assert "浮窗炸了" in result.status
    assert action.busy is False


def test_an_unknown_action_still_lands_on_an_explicit_branch():
    """兜底：漏进来的动作 id 落到问答（打开浮窗＝什么都不发的安全面），不许静默丢弃。

    真控制器在门口就把闭集之外的动作挡掉了
    （``test_screen_capture_controller.py::test_an_unknown_action_aborts_instead_of_flowing_downstream``），
    这条钉的是 `else` 分支本身的存在——它一旦被删掉，漏进来的动作会变成"点了没反应"。
    """
    dialog = _FakeAskDialog()
    action = _start("copy-typo", ask_dialog=dialog)

    assert len(dialog.images) == 1


def test_continue_ask_opens_the_dialog_with_the_image_and_the_recognized_text():
    """结果面板「继续提问…」的落点：图 + 已识别文字 + 本卡选区一起进浮窗（上下文只进首轮）。

    选区由**面板随卡片**交下来（``(image, text, below)`` 三参），不是回调方回头读
    "最近一次采集的选区"——那张卡的图与文字可能来自更早的一次操作（跨截图返修 P1）。
    """
    image = _image()
    dialog = _FakeAskDialog()
    action = _start(ACTION_OCR, ocr=lambda _img: "识别到的文字", ask_dialog=dialog)

    action._continue_ask(image, "识别到的文字", _SELECTION)

    assert dialog.calls == [(image, "识别到的文字", _SELECTION)]


def test_continue_ask_closes_the_result_panel_before_opening_the_dialog():
    """实机反馈：续聊开始后结果卡不该还挂在屏幕上——先收面板、再开浮窗。"""
    panel = _FakePanel()
    dialog = _FakeAskDialog()
    action = _start(ACTION_OCR, ocr=lambda _img: "识别到的文字", ask_dialog=dialog,
                    presenter=_RecordingPresenter(panel=panel))

    action._continue_ask(_image(), "识别到的文字", _SELECTION)

    assert panel.closes == 1
    assert len(dialog.calls) == 1


def test_continue_ask_tolerates_a_host_without_a_panel():
    """面板还不存在（没上面板过/建不出来）时不许炸：关面板那一步跳过，浮窗照开。"""
    dialog = _FakeAskDialog()
    action = _start(ACTION_OCR, ocr=lambda _img: "识别到的文字", ask_dialog=dialog,
                    presenter=_RecordingPresenter())   # panel() 返回 None

    action._continue_ask(_image(), "识别到的文字", _SELECTION)

    assert len(dialog.calls) == 1


def test_ocr_result_carries_the_selection_image_for_the_continue_entry():
    """提取文字的结果必须带着那张图：不带图，面板底部的「继续提问…」就没上下文可续。"""
    presenter = _RecordingPresenter()
    _start(ACTION_OCR, ocr=lambda _img: "识别到的文字", presenter=presenter)

    result = _final(presenter)
    assert isinstance(result.image, QImage)


def test_the_continue_button_on_the_real_panel_reaches_the_ask_dialog():
    """端到端（真实面板，offscreen）：结果面板底部「继续提问…」→ 浮窗。

    串起四层：OCR 结果带图 → 真实面板露出入口 → 点它 → 编排层先收掉结果卡，
    再把（图, 文字, 选区）交给问答浮窗。这条链路上任何一处断掉，用户看到的
    都是"点了没反应"或"结果卡与浮窗同时挂着"。
    """
    dialog = _FakeAskDialog()
    action = ScreenCaptureActions(
        _FakeHost(island=_FakeIsland()),
        controller=_FakeController(action=ACTION_OCR),
        ocr=lambda _img: "识别到的文字", ask_dialog=dialog, run_inline=True)

    assert action.run() is True

    panel = action._presenter.panel()
    assert isinstance(panel, ScreenResultPanel)
    assert panel.continue_button().isVisibleTo(panel) is True, "有图却没有「继续提问」入口"
    panel.continue_button().click()

    assert panel.isVisible() is False, "续聊已经开始，结果卡还挂在屏幕上"
    assert len(dialog.calls) == 1
    image, context_text, below = dialog.calls[0]
    assert isinstance(image, QImage) and context_text == "识别到的文字"
    assert below == _SELECTION
    panel.close_panel()


# ------------------------------------------------------------ 跟到选区下方（round 2）


def test_the_result_panel_follows_the_selection_it_came_from():
    """定位契约：结果面落在框选的那块**下方**（实机反馈：角落要来回跳视线）。

    传下去的是框到的那块选区矩形（逻辑坐标）——不是覆盖层、不是冻结帧。
    """
    presenter = _RecordingPresenter()

    _start(ACTION_OCR, ocr=lambda _img: "识别到的文字", presenter=presenter)

    assert len(presenter.belows) == 2, "过程态与终态都要摆（都在同一次等待里）"
    assert all(below == _SELECTION for below in presenter.belows), (
        "过程态卡片也要落在同一个选区下方——否则等待期间它先跳到角落、出结果再跳回来")


def test_the_ask_dialog_follows_the_selection_it_came_from():
    """问答浮窗同一条定位口径（用户拖过位置后由浮窗自己沿用用户的位置）。"""
    dialog = _FakeAskDialog()

    _start(ACTION_ASK, ask_dialog=dialog)

    assert dialog.belows == [_SELECTION]


def test_default_ask_dialog_is_built_from_the_host_config():
    """端到端：不注入替身时默认造真浮窗（宿主配置面在就造得出来），且是复用件。"""
    from pet.screen_capture.ask_dialog import ScreenAskDialog

    host = _FakeHost()
    host.cfg = {"context_menu_appearance": {"theme": "light"}}
    action = ScreenCaptureActions(host, controller=_FakeController(action=ACTION_ASK),
                                  run_inline=True)

    dialog = action._get_ask_dialog()   # 私有入口：这里要验的正是"默认装配"这一层

    assert isinstance(dialog, ScreenAskDialog)
    assert action._get_ask_dialog() is dialog, "浮窗是复用件，不是每次现造一个"
    dialog.close()


# ------------------------------------------------------------------ 失败/取消


@pytest.mark.parametrize("reason", ["esc", "toolbar"])
def test_cancel_restores_the_watcher_and_never_runs_the_engine(reason):
    """Esc 与动作条「取消」是同一条收口（理由不同，账一样）。"""
    watcher = _FakeWatcher(running=True)
    ocr_calls: list[object] = []
    presenter = _RecordingPresenter()
    action = _make_actions(
        controller=_FakeController(mode="cancelled", reason=reason),
        ocr=lambda img: ocr_calls.append(img) or "x",
        watcher=watcher,
        presenter=presenter,
    )

    assert action.run() is True
    assert ocr_calls == []
    assert presenter.results == []
    assert any("取消" in text for text in presenter.statuses)
    assert watcher.pauses == 1 and watcher.resumes == 1
    assert action.busy is False


def test_capture_failure_is_reported_and_the_watcher_comes_back():
    watcher = _FakeWatcher(running=True)
    presenter = _RecordingPresenter()
    action = _make_actions(
        controller=_FakeController(mode="failed"),
        ocr=lambda _img: "x",
        watcher=watcher,
        presenter=presenter,
    )
    failures: list[str] = []
    action.failed.connect(failures.append)

    assert action.run() is True
    assert failures == ["grab-empty"]
    assert presenter.results == []
    assert watcher.pauses == 1 and watcher.resumes == 1


def test_start_exception_still_restores_the_watcher():
    """启动就抛异常（宿主侧千奇百怪）时，物理隔离同样必须对称收口。"""
    class _RaisingController(_FakeController):
        def start(self) -> bool:
            self.starts += 1
            raise RuntimeError("宿主炸了")

    watcher = _FakeWatcher(running=True)
    presenter = _RecordingPresenter()
    action = _make_actions(controller=_RaisingController(), watcher=watcher,
                           presenter=presenter)

    assert action.run() is False
    assert watcher.pauses == 1 and watcher.resumes == 1
    assert action.busy is False


def test_second_trigger_while_capturing_is_refused():
    controller = _FakeController()
    controller.emit_on_start = False      # 停在"采集中"
    action = _make_actions(controller=controller, ocr=lambda _img: "x")

    assert action.run() is True
    assert action.busy is True
    assert action.run() is False             # 不许叠第二层覆盖层：动作还没选呢
    assert controller.starts == 1

    controller.emit()                        # 收口后可以再来一次
    assert action.busy is False


# ------------------------------------------------------------------ watcher 对称


def test_watcher_pause_restore_is_symmetric_with_the_previous_state():
    """恢复走 ``resume()``（= 按配置重评估）；此前没在跑的绝不被这次截图唤醒。"""
    running = _FakeWatcher(running=True)
    action = _make_actions(controller=_FakeController(), watcher=running,
                           ocr=lambda _img: "x")
    assert action.run() is True
    assert running.pauses == 1
    assert running.resumes == 1
    assert running.apply_configs == 1     # 恢复 = 重评估配置，不是无条件重启
    assert running.running is True

    paused = _FakeWatcher(running=False, allowed=False)
    action = _make_actions(controller=_FakeController(), watcher=paused,
                           ocr=lambda _img: "x")
    assert action.run() is True
    assert paused.pauses == 1
    assert paused.resumes == 0
    assert paused.apply_configs == 0
    assert paused.running is False

    action = _make_actions(controller=_FakeController(), watcher=None,
                           ocr=lambda _img: "x")
    assert action.run() is True     # watcher 缺席也不许炸


def test_watcher_is_resolved_again_for_every_session(monkeypatch):
    """watcher 每次现取（m9）：壳的共享 watcher 会晚建/重建，构造期抓到的那一个
    可能早就不是当值的那一个——一次截图必须暂停**当时**的那个。

    宿主取用路径（``_host_watcher``）返回的是哪个对象由宿主决定，这里换掉它来模拟
    "第一轮之后 watcher 被换掉了"。
    """
    first = _FakeWatcher(running=True)
    second = _FakeWatcher(running=True)
    current = {"watcher": first}
    monkeypatch.setattr(actions_mod, "_host_watcher", lambda _host: current["watcher"])

    controller = _FakeController()          # 假闭环只允许"进入"一次（模拟真实的一次触发）
    action = _make_actions(controller=controller, watcher=None, ocr=lambda _img: "x")
    assert action.run() is True
    assert (first.pauses, first.resumes) == (1, 1)

    current["watcher"] = second             # 第二轮之前换了当值的 watcher
    controller.starts = 0                   # 上一轮已收口，假件按"允许下一轮"复位
    assert action.run() is True

    assert (second.pauses, second.resumes) == (1, 1)
    assert (first.pauses, first.resumes) == (1, 1), "换掉之后不该再动上一个"


def test_watcher_gate_resumes_the_instance_it_actually_paused():
    """恢复必须落在被暂停的那一个实例上：现取到别的对象就恢复别的对象＝
    我们暂停的那个永远醒不过来。"""
    paused_one = _FakeWatcher(running=True)
    swapped_in = _FakeWatcher(running=True)
    current = {"watcher": paused_one}
    gate = actions_mod._WatcherGate(lambda: current["watcher"])

    gate.pause()
    current["watcher"] = swapped_in      # 会话进行中被换掉
    gate.restore()

    assert (paused_one.pauses, paused_one.resumes) == (1, 1)
    assert (swapped_in.pauses, swapped_in.resumes) == (0, 0)


def test_watcher_gate_tolerates_a_broken_provider():
    """取用回调本身出错＝这条面不可用，不是截图失败。"""
    def _boom():
        raise RuntimeError("no watcher today")

    gate = actions_mod._WatcherGate(_boom)
    gate.pause()
    gate.restore()          # 不抛即通过

    assert actions_mod._WatcherGate(None)._current() is None


# ------------------------------------------------------------------ 线程纪律


def test_blocking_stages_run_off_the_gui_thread_and_report_back_on_it():
    """阻塞的那一步（引擎/网络）不许压在 GUI 线程上；结果经信号回到 GUI 线程。"""
    main_thread = threading.current_thread().name
    worker_names: list[str] = []
    done = threading.Event()

    def fake_ocr(image):
        worker_names.append(threading.current_thread().name)
        return "识别到的文字"

    presenter = _RecordingPresenter()
    action = _make_actions(controller=_FakeController(action=ACTION_OCR), ocr=fake_ocr,
                           presenter=presenter, run_inline=False)
    action.result_ready.connect(lambda _result: done.set())

    assert action.run() is True
    # 事件同步 + 宽预算（不用 QtTest 的 wait：它在"信号已先到"时返回 False，
    # 那是个会随线程调度翻面的假红）。CI runner 是新开机的慢机器，预算给足。
    deadline = time.monotonic() + 10.0
    while not done.is_set() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)
    assert done.is_set(), "后台线程的结果没有回到 GUI 线程"
    assert action.busy is False
    assert worker_names and worker_names[0] != main_thread
    assert _final(presenter).primary == "识别到的文字"


def test_empty_result_and_recognition_failure_say_different_things():
    """「没认到字」与「识别失败」是两件事，文案不许混：

    - 没认到字 = 引擎正常跑完、图里就是没文字（用户该去换一张图）；
    - 识别失败 = 引擎自己出了问题（缺模型/依赖/推理异常），**不知道**图里有没有
      文字。这时候说"没有识别到文字"就是撒谎，用户会一直换图试。
    """
    empty_presenter = _RecordingPresenter()
    empty_action = _make_actions(controller=_FakeController(action=ACTION_OCR),
                                 ocr=lambda _img: "   ", presenter=empty_presenter)
    empty_failures: list[str] = []
    empty_action.failed.connect(empty_failures.append)

    assert empty_action.run() is True

    empty_status = _final(empty_presenter).status
    assert "没有识别到文字" in empty_status
    assert empty_failures == [], "没认到字不是失败，不许报错"

    def broken(_img):
        raise RuntimeError("缺少本地 OCR 模型文件 PP-OCRv6_det_small.onnx")

    broken_presenter = _RecordingPresenter()
    broken_action = _make_actions(controller=_FakeController(action=ACTION_OCR),
                                  ocr=broken, presenter=broken_presenter)
    broken_failures: list[str] = []
    broken_action.failed.connect(broken_failures.append)

    assert broken_action.run() is True

    broken_status = broken_presenter.statuses[-1]
    assert "识别失败" in broken_status
    assert "没有识别到文字" not in broken_status, "引擎坏了却说「图里没字」＝撒谎"
    assert "PP-OCRv6_det_small.onnx" in broken_status, "失败必须带上人话原因"
    assert broken_failures and "PP-OCRv6_det_small.onnx" in broken_failures[0]
    assert empty_status != broken_status


# ------------------------------------------------------------ 用完就丢（归还）


def _recording_release():
    """假释放入口：记录"被调了几次"，并支持验证"确实发生在收口之后"。"""
    calls: list[float] = []

    def release() -> bool:
        calls.append(time.monotonic())
        return True

    release.calls = calls          # type: ignore[attr-defined]
    return release


def test_ocr_action_hands_the_local_engine_back_when_the_session_ends():
    """交付后必须归还引擎：这是「用完就丢」在编排层的唯一落点。"""
    release = _recording_release()
    presenter = _RecordingPresenter()
    _start(ACTION_OCR, ocr=lambda _img: "识别到的文字", release=release,
           presenter=presenter)

    assert len(release.calls) == 1, "一次会话收口恰好归还一次"
    assert _finals(presenter), "归还发生在收口里，但结果照样要交付"


def test_copy_action_also_hands_the_local_engine_back():
    """复制出口同样是会话结束：归还点落在唯一的收口里，哪条链路都不例外。"""
    release = _recording_release()
    _start(ACTION_COPY, release=release)

    assert len(release.calls) == 1


def test_cancelled_and_failed_sessions_also_hand_the_engine_back():
    """取消/失败同样是会话结束：任何出口都不许把引擎留在原地。

    这两条出口上引擎通常还没被加载（no-op），但"调用点存在"必须被钉住——
    漏掉它就会表现为"某条失败路径之后引擎常驻到下次重启"。
    """
    for mode in ("cancelled", "failed"):
        release = _recording_release()
        controller = _FakeController(mode=mode)
        action = _make_actions(controller=controller, release=release,
                              presenter=_RecordingPresenter())

        assert action.run() is True
        assert action.busy is False
        assert len(release.calls) == 1, f"{mode} 出口没有归还引擎"


def test_release_failure_never_breaks_a_delivered_result():
    """归还失败只是日志：图已经拿到了，不该反过来变成用户可见的错误。"""
    def boom() -> bool:
        raise RuntimeError("释放炸了")

    presenter = _RecordingPresenter()
    _start(ACTION_OCR, ocr=lambda _img: "识别到的文字", release=boom,
           presenter=presenter)

    assert _final(presenter).primary == "识别到的文字"


def test_release_does_not_block_the_gui_thread_or_the_result():
    """真线程口径：归还跑在后台（实测 unload 约 48 ms，压在浮层弹出那一刻）。"""
    main_thread = threading.current_thread().name
    worker_names: list[str] = []
    done = threading.Event()

    def release() -> bool:
        worker_names.append(threading.current_thread().name)
        done.set()
        return True

    presenter = _RecordingPresenter()
    action = _make_actions(controller=_FakeController(action=ACTION_OCR),
                           ocr=lambda _img: "识别到的文字", release=release,
                           presenter=presenter, run_inline=False)
    action.result_ready.connect(lambda _result: None)

    assert action.run() is True

    deadline = time.monotonic() + 10.0
    while not done.is_set() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)
    assert done.is_set(), "归还没有在预算内发生"
    assert worker_names[0] != main_thread, "归还跑在了 GUI 线程上"
    assert _final(presenter).primary == "识别到的文字"


def test_release_seam_matches_the_engine_module_contract():
    """跨切片契约：``ocr.py`` 必须真的有编排层要调的那个释放入口。"""
    engine = pytest.importorskip("pet.screen_capture.ocr")
    assert callable(getattr(engine, actions_mod.OCR_RELEASE_ENTRY, None))
    assert callable(actions_mod.resolve_ocr_release())


def test_missing_release_entry_is_tolerated(monkeypatch):
    """旧版引擎没有释放入口时：不还而已，识别链路照常交付。"""
    monkeypatch.setattr(actions_mod, "resolve_ocr_release", lambda: None)
    presenter = _RecordingPresenter()
    _start(ACTION_OCR, ocr=lambda _img: "识别到的文字", presenter=presenter)

    assert _final(presenter).primary == "识别到的文字"


# ------------------------------------------------------------------ seam 契约


def test_ocr_seam_matches_the_engine_module_contract():
    """同包 OCR 引擎（切片 2）的模块级入口必须与 ``actions`` 的 seam 常量对得上。

    这是**跨切片契约**的机器化：入口改名/挪位在这里红，而不是等用户点了菜单
    才发现"本地识别没接线"。引擎模块不在时跳过（本片可以独立交付）。
    """
    engine = pytest.importorskip("pet.screen_capture.ocr")
    assert callable(getattr(engine, actions_mod.OCR_ENTRY, None))
    assert actions_mod.ocr_engine_available() is True


def test_actions_tuple_is_the_dispatch_closed_set():
    """分发表的闭集：四个动作都在，且就是覆盖层动作条那一组 id（同一批对象）。"""
    from pet.screen_capture import overlay as overlay_mod

    assert actions_mod.ACTIONS == (ACTION_OCR, ACTION_TRANSLATE, ACTION_ASK, ACTION_COPY)
    assert actions_mod.ACTION_COPY is overlay_mod.ACTION_COPY
    assert actions_mod.ACTION_OCR is overlay_mod.ACTION_OCR


# ------------------------------------------------------------------ 展示落点


def test_presenter_drives_the_real_dynamic_island(tmp_path):
    """真实灵动岛（offscreen）：状态与短结果确实进了岛的「最近消息」面。

    岛没有公开的消息读取入口——``_last_message`` 就是卡片那一行渲染的那一份，
    所以这里读它（不是断言私有调用顺序，而是断言用户看得见的那段文案）。
    """
    from pet.config import Config
    from pet.island.dynamic_island import DynamicIsland

    cfg = Config(tmp_path)
    data = dict(cfg.get("dynamic_island", {}) or {})
    data.update({"style": "dark", "show_info": True})
    cfg.set("dynamic_island", data)
    island = DynamicIsland(cfg)
    island.show()   # 离屏：只让"可见"判据成立，屏幕上不会出现任何东西
    presenter = ScreenCapturePresenter(island=island, panel_factory=ScreenResultPanel)

    presenter.status("正在本地识别文字…")
    assert island._last_message == "正在本地识别文字…"

    panel = presenter.show(ScreenResult(title="截图提取文字", primary="字" * 80))
    assert isinstance(panel, ScreenResultPanel)
    assert island._last_message.endswith("…")

    panel.close_panel()
    island.close()
    island.deleteLater()


def test_presenter_routes_status_and_short_results_to_the_island():
    island = _FakeIsland()
    presenter = ScreenCapturePresenter(island=island, bubble=None,
                                       panel_factory=ScreenResultPanel)

    presenter.status("正在识别…")
    assert island.messages[-1] == "正在识别…"
    assert island.events[-1] == "generic"

    panel = presenter.show(ScreenResult(title="截图提取文字", primary="确定"))
    assert panel is None                        # 短结果不上面板
    assert island.messages[-1] == "确定"
    assert island.events[-1] == "reply"


def test_presenter_sends_long_results_to_the_panel_beside_the_island():
    island = _FakeIsland()
    presenter = ScreenCapturePresenter(island=island, panel_factory=ScreenResultPanel)

    panel = presenter.show(ScreenResult(title="截图提取文字", primary="字" * 120))
    assert isinstance(panel, ScreenResultPanel)
    assert panel.isVisible() is True
    assert panel.primary_text() == "字" * 120
    # 岛的短文案是限长摘要，不是全文
    assert island.messages[-1].endswith("…")
    assert len(island.messages[-1]) <= actions_mod.SHORT_RESULT_LIMIT + 1
    panel.close_panel()


def test_presenter_boundary_at_the_island_line_limit():
    """边界值：正好等于上限的短结果不上岛外，超一个字就必须开面板。"""
    island = _FakeIsland()
    presenter = ScreenCapturePresenter(island=island, panel_factory=ScreenResultPanel)

    at_limit = "字" * actions_mod.SHORT_RESULT_LIMIT
    assert presenter.show(ScreenResult(title="截图提取文字", primary=at_limit)) is None
    assert island.messages[-1] == at_limit

    over_limit = "字" * (actions_mod.SHORT_RESULT_LIMIT + 1)
    panel = presenter.show(ScreenResult(title="截图提取文字", primary=over_limit))
    assert isinstance(panel, ScreenResultPanel)
    assert panel.primary_text() == over_limit
    panel.close_panel()


def test_presenter_falls_back_to_the_pet_bubble_without_an_island():
    host = _FakeHost(island=None)
    presenter = ScreenCapturePresenter(island=None, bubble=host.show_bubble,
                                       panel_factory=ScreenResultPanel)

    presenter.status("正在识别…")
    assert host.bubbles[-1] == "正在识别…"

    panel = presenter.show(ScreenResult(title="截图问答", primary="答" * 100))
    assert isinstance(panel, ScreenResultPanel)
    assert host.bubbles[-1].startswith("答")
    panel.close_panel()


# ---------------------------- 展示归属的唯一权威在落点（跨截图返修 P2 二轮）


def test_a_stale_round_cannot_overwrite_the_island_or_the_bubble_with_a_short_result():
    """短结果不上面板，照样覆写岛/气泡——归属必须先于**任何**通知判。

    旧实现的两条毛病：① 判据设在面板里，短结果根本到不了那里（没人挡）；② 判在
    ``_notify`` **之后**，旧终态的岛/气泡文案照旧刷掉新一轮的。这条只用短结果（面板
    一次都不该被造出来）：岛与气泡两条回落链路各钉一遍。
    """
    made: list = []

    def factory(parent, **kwargs):
        made.append(1)
        return ScreenResultPanel(parent, **kwargs)

    island = _FakeIsland()
    bubbles: list[str] = []
    presenter = ScreenCapturePresenter(island=island, bubble=bubbles.append,
                                       panel_factory=factory)

    presenter.show(ScreenResult(title="截图提取文字", primary="A 的字"), round_key=1)
    assert island.messages[-1] == "A 的字"
    presenter.show(ScreenResult(title="截图提取文字", primary="B 的字"), round_key=2)
    assert island.messages[-1] == "B 的字"
    spoke = (len(island.messages), len(island.events))
    assert made == [], "短结果不该造结果面板"

    presenter.show(ScreenResult(title="截图提取文字", primary="A 的旧终态"), round_key=1)
    assert (len(island.messages), len(island.events)) == spoke, "旧轮次又发了一条通知"
    assert island.messages[-1] == "B 的字", "旧轮次的短结果覆写了岛上的文案"
    assert bubbles == [], "岛可达时不该走气泡"

    # 岛不可达时的回落链路不是归属判据的盲区
    host = _FakeHost(island=None)
    fallback = ScreenCapturePresenter(island=None, bubble=host.show_bubble,
                                      panel_factory=factory)
    fallback.show(ScreenResult(title="截图提取文字", primary="B 的字"), round_key=2)
    fallback.show(ScreenResult(title="截图提取文字", primary="A 的旧终态"), round_key=1)
    assert host.bubbles == ["B 的字"], f"气泡被旧轮次覆写了：{host.bubbles}"


def test_a_stale_round_arriving_while_the_newer_one_is_running_changes_nothing():
    """旧终态**后**到、接棒的还在跑：岛/气泡/正文/译文/动画/「继续提问」一处都不许动。

    两张卡片的**图与文字都不同**（A 的图配 A 的文字、B 的图配 B 的文字），所以"屏上这张
    卡是谁的"可以由「继续提问」实收的那份 ``(图, 正文)`` 直接判定——旧实现里 A 的旧终态
    会在 B 的过程态卡片上摆出 A 的图文，并把 B 还在跑的动画收掉。
    """
    island = _FakeIsland()
    bubbles: list[str] = []
    handed: list[tuple] = []
    presenter = ScreenCapturePresenter(
        island=island, bubble=bubbles.append, panel_factory=ScreenResultPanel,
        continue_ask=lambda image, text, below: handed.append((image, text, below)))

    image_a, image_b = _image(), _second_image()
    panel = presenter.show(ScreenResult(title="截图提取文字", primary="A" * 60,
                                        image=image_a), round_key=1)
    assert panel is not None and panel.primary_text() == "A" * 60
    assert island.messages[-1] == "A" * actions_mod.SHORT_RESULT_LIMIT + "…"

    # B 接棒：过程态卡片在屏上跑（同一次操作的第二张卡片，同身份）。过程态不往岛推
    # （它没有内容可推），所以岛上还停着 A 那一行——正是"旧终态不许再发一条通知"的可判面
    assert presenter.show(ScreenResult(title="截图翻译", primary="B" * 60,
                                       force_panel=True, loading=True,
                                       image=image_b), round_key=2) is panel
    animation = panel.findChild(QWidget, "screen-result-loading")
    assert animation.isVisible() is True and animation._timer.isActive() is True
    assert len(island.messages) == 1, "过程态卡片不该往岛上推（它没有内容可推）"
    assert island.messages[-1] == "A" * actions_mod.SHORT_RESULT_LIMIT + "…"

    island_before = list(island.messages)
    events_before = len(island.events)
    stale = ScreenResult(title="截图提取文字", primary="A" * 60, secondary="A 译文",
                         status="已翻译 · 3 字", image=image_a, language_selector=True)

    assert presenter.show(stale, below=_SELECTION, round_key=1) is None, "旧终态没有被判掉"
    assert island.messages == island_before, "旧终态又往岛上推了一条通知"
    assert len(island.events) == events_before, "旧终态又戳了一次岛的动效"
    assert bubbles == [], "旧终态走了气泡"
    assert panel.primary_text() == "B" * 60, "旧终态覆写了接棒卡片的正文"
    assert panel.secondary_text() == "" and panel.has_secondary() is False, "旧终态摆出了译文"
    assert panel.status_text() == "" and panel.title_text() == "截图翻译", "旧终态改了标题栏"
    assert animation.isVisible() is True, "旧终态收掉了接棒卡片的加载动画"
    assert animation._timer.isActive() is True, "旧终态停了接棒卡片还在跑的表"
    assert panel.continue_button().isVisible() is False, "旧终态把 B 的加载态改成了终态"

    # B 自己的终态：岛上是 B 的摘要，正文/「继续提问」交出来的也都是 B 那份（不是 A 的）
    assert presenter.show(ScreenResult(title="截图翻译", primary="B" * 60, image=image_b,
                                       force_panel=True), below=_SELECTION_B,
                          round_key=2) is panel
    assert panel.primary_text() == "B" * 60
    assert island.messages[-1] == "B" * actions_mod.SHORT_RESULT_LIMIT + "…"
    assert panel.continue_button().isVisible() is True
    panel.continue_button().click()
    assert handed == [(image_b, "B" * 60, _SELECTION_B)], f"接棒的卡片交错了上下文：{handed}"
    panel.close_panel()


def test_a_stale_round_arriving_after_the_newer_one_finished_changes_nothing(tmp_path):
    """旧终态**更晚**到、接棒的已经收口：真灵动岛上的那一行与面板上的那张卡都不许变。

    岛用**真件**（``DynamicIsland``，离屏 show 只为让"可见"判据成立）：断言读的是它真在
    渲染的那份 ``_last_message``，不是替身记的调用序列。
    """
    from pet.config import Config
    from pet.island.dynamic_island import DynamicIsland

    cfg = Config(tmp_path)
    data = dict(cfg.get("dynamic_island", {}) or {})
    data.update({"style": "dark", "show_info": True})
    cfg.set("dynamic_island", data)
    island = DynamicIsland(cfg)
    island.show()

    bubbles: list[str] = []
    presenter = ScreenCapturePresenter(island=island, bubble=bubbles.append,
                                       panel_factory=ScreenResultPanel)
    image_a, image_b = _image(), _second_image()

    presenter.show(ScreenResult(title="截图提取文字", primary="A" * 60, image=image_a),
                   round_key=1)
    panel = presenter.show(ScreenResult(title="截图提取文字", primary="B" * 60,
                                        image=image_b), round_key=2)
    assert island._last_message == "B" * actions_mod.SHORT_RESULT_LIMIT + "…"
    assert panel.primary_text() == "B" * 60
    landed = island._last_message

    assert presenter.show(ScreenResult(title="截图提取文字", primary="A 的旧终态",
                                       secondary="A 译文", status="已翻译 · 3 字",
                                       image=image_a), round_key=1) is None
    assert island._last_message == landed, "旧终态刷掉了岛上已经收口的那一行"
    assert bubbles == []
    assert panel.primary_text() == "B" * 60, "旧终态覆写了已经收口的卡片"
    assert panel.secondary_text() == "" and panel.has_secondary() is False
    assert panel.findChild(QWidget, "screen-result-loading").isVisible() is False

    panel.close_panel()
    island.close()
    island.deleteLater()


def test_the_ownership_state_stays_bounded_across_100k_operations():
    """10 万次操作之后归属状态仍然有界：只有一个"已放行的最高编号"，不记任何历史。

    旧实现按身份登记（``_round_epoch`` 每个身份一条 int，实测 ≈85 B/操作、10 万条
    ≈10.6 MiB，见报告 §四）。这里用 10 万个**互不相同**的轮次身份把旧写法的增长面跑满，
    断言落点里没有任何随轮次增长的容器；并且钉住**没有淘汰**——第 1 轮的身份在第 10 万
    轮之后仍然被判成旧轮次（LRU 一淘汰，被忘掉的旧身份就重新"看起来最新"，旧回调复活
    来覆写屏上的新卡片）。
    """
    island = _FakeIsland()
    presenter = ScreenCapturePresenter(island=island, bubble=None, panel_factory=None)
    rounds = 100_000

    for key in range(1, rounds + 1):
        presenter.show(ScreenResult(title="截图提取文字", primary="短"), round_key=key)
    assert island.messages[-1] == "短"

    growing = sorted(name for name, value in vars(presenter).items()
                     if isinstance(value, (dict, list, set, tuple)))
    assert bool(growing) is False, f"归属面留下了随轮次增长的状态：{growing}"
    assert presenter._accepted_round == rounds
    assert sys.getsizeof(presenter._accepted_round) == sys.getsizeof(rounds), \
        "归属状态随轮次数变大了（应当是定长 int）"
    assert sys.getsizeof(rounds) <= 32, "定长 int 的内存上界"

    spoke = (len(island.messages), len(island.events))
    assert presenter.show(ScreenResult(title="截图提取文字", primary="旧轮次"),
                          round_key=1) is None
    assert (len(island.messages), len(island.events)) == spoke, "首轮的旧终态复活了"
    assert island.messages[-1] == "短"

    presenter.show(ScreenResult(title="截图提取文字", primary="下一轮"), round_key=rounds + 1)
    assert island.messages[-1] == "下一轮", "水位挡错了方向（新一轮被当成旧轮次）"

    presenter.show(ScreenResult(title="截图提取文字", primary="匿名"))   # round_key=None
    assert island.messages[-1] == "匿名", "匿名（None）身份不参与归属，应当照常展示"


# ------------------------------------------------- 结果浮层主题（交叉审查 m2）


def test_presenter_hands_an_explicit_theme_to_the_panel_factory():
    """``style`` 必须真的传下去：不传等于浅色宿主上永远弹深色浮层。"""
    seen: dict = {}

    def factory(parent, **kwargs):
        seen.update(kwargs)
        return None      # 造不出来也不影响这条断言：本用例只看"传了什么"

    presenter = ScreenCapturePresenter(island=None, panel_factory=factory, style="light")
    presenter.show(ScreenResult(title="截图提取文字", primary="字" * 80, force_panel=True))

    assert seen == {"style": "light"}


def test_presenter_leaves_the_theme_to_the_panel_when_the_host_has_none():
    """没主题（跟随系统/读不到配置）＝不传 style，由面板按宿主调色板现判。"""
    seen: list = []

    def factory(parent, **kwargs):
        seen.append(kwargs)
        return None

    presenter = ScreenCapturePresenter(island=None, panel_factory=factory, style=None)
    presenter.show(ScreenResult(title="截图提取文字", primary="字" * 80, force_panel=True))

    assert seen == [{}]


def test_host_theme_comes_from_the_same_key_the_menus_use():
    """真值只有一份：主配置的 ``context_menu_appearance.theme``（菜单渲染用的那个键）。

    ``system``/缺失一律 ``None``——那表示"跟着宿主调色板现判"，不由这里猜。
    """
    host = _FakeHost()
    host.cfg = {"context_menu_appearance": {"theme": "light"}}
    assert actions_mod._host_theme(host) == "light"

    host.cfg = {"context_menu_appearance": {"theme": "dark"}}
    assert actions_mod._host_theme(host) == "dark"

    host.cfg = {"context_menu_appearance": {"theme": "system"}}
    assert actions_mod._host_theme(host) is None

    host.cfg = {"context_menu_appearance": "not a mapping"}
    assert actions_mod._host_theme(host) is None

    assert actions_mod._host_theme(object()) is None


def test_default_presenter_builds_a_light_panel_for_a_light_host():
    """端到端：浅色宿主 → 默认编排件造出来的浮层真的是浅色那份。"""
    host = _FakeHost()
    host.cfg = {"context_menu_appearance": {"theme": "light"}}
    action = ScreenCaptureActions(host, controller=_FakeController(), run_inline=True)

    panel = action._presenter.panel()   # 私有入口：这里要验的正是"默认装配"这一层

    assert isinstance(panel, ScreenResultPanel)
    assert panel.style == "light"
    panel.close_panel()


# ------------------------------------------------------------------ 盲审 M1：重翻不占采集会话


def _pump_until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)
    return predicate()


def test_retranslate_is_blocked_during_a_busy_capture():
    """采集会话在跑时语言行重翻是 no-op：busy 位不动、引擎不还、面板不多卡。"""
    ocr_gate = threading.Event()
    released: list[bool] = []

    def slow_ocr(_img):
        ocr_gate.wait(10)
        return "识别到的文字"

    presenter = _RecordingPresenter()
    action = _make_actions(controller=_FakeController(action=ACTION_OCR),
                           ocr=slow_ocr, presenter=presenter, run_inline=False,
                           translator=lambda _img, src: "译文",
                           release=lambda: released.append(True))
    assert action.run() is True
    assert action.busy is True
    action._last_ocr_source = "上一轮原文"
    before = len(presenter.results)

    action._retranslate("日本語")   # busy 守卫：直接 no-op

    assert action.busy is True
    assert released == []
    assert len(presenter.results) == before, "忙时重翻不该往面板上摆任何东西"
    ocr_gate.set()
    assert _pump_until(lambda: not action.busy)
    assert _final(presenter).primary == "识别到的文字"


def test_an_inflight_retranslate_survives_a_new_capture_and_neither_loses_its_result():
    """重翻在跑时用户又发起新采集：两个槽位互不覆盖，两边结果都该落地（M1 时序一）。"""
    retranslate_gate = threading.Event()
    ocr_gate = threading.Event()
    released: list[bool] = []

    def slow_translate(_img, src):
        retranslate_gate.wait(10)
        return f"{src}→译文"

    def slow_ocr(_img):
        ocr_gate.wait(10)
        return "识别到的文字"

    import types

    # 重翻要有配置面（_config 守卫）；注入的 translator 替身不走真网络
    host = types.SimpleNamespace(cfg={}, show_bubble=None, proactive_watcher=None)
    presenter = _RecordingPresenter()
    action = _make_actions(host=host, controller=_FakeController(action=ACTION_OCR),
                           ocr=slow_ocr, presenter=presenter, run_inline=False,
                           translator=slow_translate,
                           release=lambda: released.append(True))
    action._last_ocr_source = "原文"
    action._retranslate("日本語")          # 空闲时发起：占重翻槽
    assert action._pending_retranslate is not None

    assert action.run() is True            # 新采集：占主槽，互不覆盖
    assert action.busy is True

    retranslate_gate.set()
    assert _pump_until(lambda: action._pending_retranslate is None)
    assert action.busy is True, "重翻收口把采集的 busy 清了"
    assert released == [], "重翻收口把在跑的 OCR 引擎还了"
    assert any("译文" in (r.secondary or "") for r in presenter.results), "重翻结果丢了"

    ocr_gate.set()
    assert _pump_until(lambda: not action.busy)
    assert _final(presenter).primary == "识别到的文字", "采集结果被重翻吞了"


# -------------------------------------- 加载轮次的生命周期（失败 / 取消 / 两条链路）


class _OnDemandController(QObject):
    """按需发出口的假闭环：``start()`` 永远受理，三条出口由用例亲手发。

    （``_FakeController`` 是按脚本自动发的、``start()`` 第二次就拒——那是"一次一条"
    的用途。轮次生命周期的用例要在同一条编排件上跑**多次独立操作**，并要求精确
    控制出口落在哪一刻。）
    """

    captured = Signal(object, object, object)
    cancelled = Signal(str)
    failed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.starts = 0

    def start(self) -> bool:
        self.starts += 1
        return True


def _retranslate_host():
    """重翻链路要有配置面（``_retranslate`` 的 ``_config`` 守卫）；注入替身不走真网络。"""
    return types.SimpleNamespace(cfg={}, show_bubble=None, proactive_watcher=None)


#: 本用例文件里造过的轮次装置（真面板 + 在跑的加载动画），供收尾夹具统一关掉。
_LIVE_RIGS: list = []


@pytest.fixture(autouse=True)
def _close_round_rigs():
    """每个用例结束后把真面板收干净——**断言失败提前退出时也收**，并清掉剪贴板。

    两条都是"别让不入账的 Qt 对象活到解释器退出"：实测那种活法以原生段错误收场
    （退出码 139，一次 `1 passed` 被记成崩溃，红跑的失败信息也读不到）。

    - 面板：断言失败时用例提前退出，面板还开着、加载动画还在滴答；
    - 剪贴板：复制图像那条链路会把一份 QMimeData（内含 QImage）放进系统剪贴板，
      它比本进程活得更久，退出时才销毁就晚了——这是**既有**故障面
      （`test_a_capture_cleanup_never_ends_the_retranslate_round_in_flight` 单跑
      必现 139，本轮只是把它记下来并清干净），不是本轮改动引入的。
    """
    yield
    while _LIVE_RIGS:
        rig = _LIVE_RIGS.pop()
        try:
            rig.close()
        except Exception:      # 面板可能本来就没建出来（用例在更早的地方就退了）
            pass
    clipboard = QApplication.clipboard()
    clear = getattr(clipboard, "clear", None)
    if callable(clear):        # 测试可以把剪贴板换成一个坏掉的替身，这里不许炸
        clear()


class _RoundRig:
    """真链路轮次装置：真 presenter + 真结果面板 + 抽签替身 + 按需假闭环。

    轮次口径（谁抽、抽几次、谁收口、谁的脸）只能在**真面板**上观察——替身 presenter
    没有加载控件。抽签按序发**四张互不相同**的牌，所以"这张脸是不是刚才那一张"可以
    逐次判定，不靠计数也能验。
    """

    def __init__(self, monkeypatch, *, ocr, translator=None, run_inline=False, host=None):
        from PySide6.QtGui import QColor, QPixmap

        from pet.screen_capture import brand_icons
        from pet.screen_capture import result_panel as rp

        deck = [brand_icons.load_jingwa(), brand_icons.load_ds_whale()]
        for color in ("#ff0000", "#00ff00"):
            card = QPixmap(6, 6)
            card.fill(QColor(color))
            deck.append(card)
        self.deck = deck
        self.rolls = 0
        monkeypatch.setattr(rp, "pick_loading_icon", self._pick)

        self.controller = _OnDemandController()
        # 「继续提问」要验到"面板把**这张卡自己**的图与选区交出去"：presenter 的回调在
        # actions 建好之后回填（构造期还没有 actions 实例），替身浮窗记录实收参数。
        self.ask = _FakeAskDialog()
        self._ask_handoff: dict = {}
        self.presenter = ScreenCapturePresenter(
            style="light",
            continue_ask=lambda image, text, below: self._ask_handoff["fn"](image, text, below),
        )
        # 编排层交给展示层的**每一次请求**（图 / 选区 / 轮次身份）：跨截图串用要能在
        # "摆上去的是哪张图"这一层看见，不只看终态文案（被展示层挡下的请求也留档）。
        self.shown: list[tuple] = []
        real_show = ScreenCapturePresenter.show

        def spying_show(presenter_self, result, below=None, round_key=None):
            self.shown.append((result.image, below, round_key))
            return real_show(presenter_self, result, below=below, round_key=round_key)

        monkeypatch.setattr(ScreenCapturePresenter, "show", spying_show)
        self.actions = ScreenCaptureActions(
            _FakeHost() if host is None else host, controller=self.controller,
            presenter=self.presenter, ocr=ocr, translator=translator,
            release=lambda: None, run_inline=run_inline, ask_dialog=self.ask,
        )
        self._ask_handoff["fn"] = self.actions._continue_ask
        self.image = _image()
        self.selection = _SELECTION
        _LIVE_RIGS.append(self)

    def _pick(self):
        icon = self.deck[self.rolls % len(self.deck)]
        self.rolls += 1
        return icon

    @property
    def panel(self) -> ScreenResultPanel:
        panel = self.presenter.panel()
        assert panel is not None, "过程态应该已经把结果面板开出来了"
        return panel

    @property
    def animation(self):
        return self.panel.findChild(QWidget, "screen-result-loading")

    @property
    def face(self):
        """当前卡片上的那一只（本轮抽中的图标）。"""
        return self.animation._current_icon()

    def run_and(self, action, *, image=None, selection=None) -> None:
        """跑一次采集：默认用装置自带的那张图/那块选区（跨截图用例显式给第二张）。"""
        assert self.actions.run() is True
        self.controller.captured.emit(
            self.image if image is None else image,
            self.selection if selection is None else selection,
            action)

    def cancel(self, reason: str = "esc") -> None:
        self.controller.cancelled.emit(reason)

    def close(self) -> None:
        self.panel.close_panel()


def test_a_failed_stage_ends_the_loading_round_so_the_next_operation_rolls_again(monkeypatch):
    """识别失败没有终态结果可摆，加载卡会一直转：失败出口必须**显式收口本轮**。

    不收口的后果不只是"卡上多转一会儿"：下一独立操作会继承上一轮的脸（旧实现里
    第二轮那张卡片一次都不抽），用户看到的是抽签失灵。真实链路：``run()`` → 动作条
    选提取文字 → 假 OCR 先抛错、第二次才给结果。
    """
    gate = threading.Event()
    calls = {"n": 0}

    def flaky_ocr(_image):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("引擎炸了")
        gate.wait(10)
        return "识别到的文字"

    rig = _RoundRig(monkeypatch, ocr=flaky_ocr, run_inline=False)
    rig.run_and(ACTION_OCR)
    assert _pump_until(lambda: not rig.actions.busy)   # 失败出口（没有终态结果）

    animation = rig.animation
    assert rig.rolls == 1, "第一轮没抽签"
    assert animation._timer.isActive() is False, "失败之后加载卡还在转"
    assert animation.isVisible() is False, "失败之后加载卡还挂在面板上"

    # 下一次独立操作＝新的一轮：它自己抽一张（这里闸住后台步，好在卡片在屏上看脸）
    rig.run_and(ACTION_OCR)
    assert rig.rolls == 2, "失败之后的下一轮没有重新抽签"
    assert rig.face is rig.deck[1], "第二轮还是上一轮那一只"
    assert animation._timer.isActive() is True

    gate.set()
    assert _pump_until(lambda: not rig.actions.busy)
    assert rig.panel.primary_text() == "识别到的文字"
    rig.close()


def test_a_cancelled_session_ends_the_loading_round_so_the_next_operation_rolls_again(
        monkeypatch):
    """取消（Esc）也是"这一轮到此结束"：卡片不许继续转，下一独立操作要重新抽签。

    取消到达时链路里确实有一张过程态卡片在跑（识别在后台线程里），所以这条用真线程
    + 事件闸门钉住那一刻：不 sleep 赌时序，也不靠"取消时恰好没有卡片"这种巧合。
    """
    gate = threading.Event()

    def slow_ocr(_image):
        gate.wait(10)
        return "识别到的文字"

    rig = _RoundRig(monkeypatch, ocr=slow_ocr, run_inline=False)
    rig.run_and(ACTION_OCR)
    animation = rig.animation
    assert rig.rolls == 1 and animation._timer.isActive() is True

    rig.cancel()
    assert rig.actions.busy is False
    assert animation._timer.isActive() is False, "取消之后加载卡还在转"
    assert animation.isVisible() is False, "取消之后加载卡还挂着"

    # 放掉被闸住的后台步：它的结果属于已经取消的那一轮，不许把卡片拉回来
    gate.set()
    assert _pump_until(
        lambda: not any(t.name == "pet-screen-capture" for t in threading.enumerate()))
    assert rig.rolls == 1, "取消之后迟到的结果把一轮拉了回来"
    assert animation._timer.isActive() is False

    rig.run_and(ACTION_OCR)             # 下一次独立操作＝新的一轮
    assert rig.rolls == 2, "取消之后的下一轮没有重新抽签"
    assert rig.face is rig.deck[1]
    assert _pump_until(lambda: not rig.actions.busy)
    assert rig.panel.primary_text() == "识别到的文字"
    rig.close()


def test_a_retranslate_terminal_never_changes_the_capture_operations_face(monkeypatch):
    """两条链路共用一块面板，但**各自一轮**：重翻的终态不许给采集那一轮换脸、也不许覆写它。

    真实时序（每一步都由用例亲手放行，不 sleep 赌）：翻译结果摆着 → 用户切语言
    （重翻在后台跑，卡片在屏上）→ 用户又发起一次翻译采集（卡片上是它自己抽的那只）
    → 重翻结果回来（它是**已经被顶下去**的那一轮：面板上第二张的卡片一个字都不许变）
    → 采集的第二步卡片回来时**必须还是同一张脸**，最后本轮的终态正常落地。

    旧实现：重翻的终态先把面板内容换掉，再把轮次位一并清掉——采集这一步就重抽了一次，
    同一次操作中途换脸（用户读到的不是抽签而是"换了件事"）。
    """
    retranslate_gate = threading.Event()
    ocr_gate = threading.Event()
    stage_gate = threading.Event()
    ocr_calls = {"n": 0}
    translate_calls = {"n": 0}

    def counting_ocr(_image):
        ocr_calls["n"] += 1
        if ocr_calls["n"] >= 2:          # 第二次采集的识别：闸住，好让重翻先回来
            ocr_gate.wait(10)
        return "识别到的文字"

    def counting_translator(_image, source):
        translate_calls["n"] += 1
        if translate_calls["n"] == 2:    # 第二次＝重翻：闸住，好让它迟到
            retranslate_gate.wait(10)
        elif translate_calls["n"] == 3:  # 第三次＝采集的第二段：闸住，好在卡片在屏上看脸
            stage_gate.wait(10)
        return f"{source}→译文"

    rig = _RoundRig(monkeypatch, ocr=counting_ocr, translator=counting_translator,
                    run_inline=False, host=_retranslate_host())
    # 第一轮：一次翻译（"正在识别" + "正在翻译"两张卡片同一轮）→ 终态
    rig.run_and(ACTION_TRANSLATE)
    assert _pump_until(lambda: not rig.actions.busy)
    assert rig.rolls == 1, "一次翻译的两张过程态卡片抽了不止一次"
    assert rig.panel.secondary_text() == "识别到的文字→译文"

    # 用户切语言：重翻在后台跑，面板上摆着重翻那张卡片
    rig.actions._retranslate("日本語")
    animation = rig.animation
    assert rig.rolls == 2, "重翻这张卡片没抽签"
    retranslate_face = rig.face
    assert animation._timer.isActive() is True

    # 用户又发起一次采集（翻译）：它自己抽一张，不继承重翻那只
    rig.run_and(ACTION_TRANSLATE)
    assert rig.rolls == 3, "新一次的采集没有重新抽签（继承了上一轮的脸）"
    capture_face = rig.face
    assert capture_face is not retranslate_face

    # 重翻的终态回来了：它属于已经被顶下去的那一轮——第二张的卡片一个字都不许变，
    # 连它还在跑的加载动画一起留着（展示归属：共用面板只认最新到过的那一轮）
    retranslate_gate.set()
    assert _pump_until(lambda: rig.actions._pending_retranslate is None)
    assert rig.panel.secondary_text() == "", "旧重翻的终态把第二张的卡片正文覆写了"
    assert rig.panel.primary_text() == "", "旧重翻的原文摆到了第二张的卡片上"
    assert animation._timer.isActive() is True, "旧终态把第二张还在跑的表停了"

    # 采集的第二步卡片回来：还是同一张脸（旧实现这里重抽了一次）
    ocr_gate.set()
    assert _pump_until(lambda: translate_calls["n"] == 3)
    assert rig.rolls == 3, "重翻的终态让采集这一轮重抽了"
    assert rig.face is capture_face, "同一次采集中途换了脸"

    stage_gate.set()
    assert _pump_until(lambda: not rig.actions.busy)
    assert rig.panel.secondary_text() == "识别到的文字→译文", "本轮自己的终态没落地"
    rig.close()


def test_a_capture_cleanup_never_ends_the_retranslate_round_in_flight(monkeypatch):
    """采集链路的收口（``_finish``）只收**自己那一轮**：重翻还在跑的那张卡片不许被它收掉。

    这条守的是修复的边界——"失败/取消就收干净"若写成无条件收口，重翻那一轮会被连
    动画一起收掉（本用例在那种写法下必红）。复制动作没有过程态卡片，正好让"收口时
    面板上摆的是别人那张卡"这一格局被单独钉住。
    """
    held = threading.Event()
    calls = {"n": 0}

    def translator(_image, source):
        calls["n"] += 1
        if calls["n"] >= 2:              # 第二次＝重翻：闸住
            held.wait(10)
        return f"{source}→译文"

    rig = _RoundRig(monkeypatch, ocr=lambda _image: "识别到的文字", translator=translator,
                    run_inline=False, host=_retranslate_host())
    rig.run_and(ACTION_TRANSLATE)
    assert _pump_until(lambda: not rig.actions.busy)
    assert rig.rolls == 1

    rig.actions._retranslate("日本語")   # 重翻在跑：面板上摆着它那张卡片
    animation = rig.animation
    retranslate_face = rig.face
    assert rig.rolls == 2 and animation._timer.isActive() is True

    rig.run_and(ACTION_COPY)            # 采集链路收口（这一次没有它自己的卡片）
    assert animation._timer.isActive() is True, "采集链路的收口把重翻那一轮收掉了"
    assert rig.face is retranslate_face, "采集链路的收口换了重翻的脸"
    assert rig.rolls == 2, "采集链路的收口让重翻那一轮重抽了"

    held.set()                          # 重翻自己收自己的口
    assert _pump_until(lambda: rig.actions._pending_retranslate is None)
    assert animation._timer.isActive() is False
    assert rig.panel.secondary_text() == "识别到的文字→译文"
    rig.close()


# ------------------------------------------------------------------ 跨截图上下文

# 返修 P1（两条链路的上下文串用）：一次操作的**图 / 选区 / 原文 / 轮次身份**必须在
# 发起那一刻捕获，收口只读这份快照；共用面板同一时刻只认最新到过的那一轮。四条用例
# 各钉一半：快照（旧重翻配别人的图）、展示归属（旧终态覆写/停表）、两种返回序、重翻失败。
# 断言一律用**两张互不相同**的图与两段互不相同的原文，并且用同一性（``is``）比对，
# 不用"同图对比"这种分不出来的弱断言。


def test_a_retranslate_keeps_the_image_and_selection_of_the_text_it_translates(monkeypatch):
    """重翻的图与选区取**这段原文那次采集**的快照，不取"最近一次"的状态（P1 之一）。

    真实时序（每一步都由用例亲手放行）：截 A 翻译 → 用户切语言（重翻在后台，闸住）→
    用户又截 B 走「截图问答」（这次采集只开浮窗、不上面板，所以面板上摆着的仍是重翻
    那张卡）→ 重翻结果回来。旧实现里重翻终态在收口那一刻现读 ``self._last_image`` /
    ``self._last_selection``（此刻已经是 B 的了）：面板上出现「A 的原文」配「B 的图」，
    点「继续提问」把 A 的文字交给 B 的图——图与文字不是同一次操作的产物。
    """
    gate = threading.Event()
    sources: list[str] = []

    def translator(_image, source):
        sources.append(source)
        if len(sources) >= 2:            # 第二次＝重翻：闸住，好让第二次采集先落地
            gate.wait(10)
        return f"{source}→译文"

    image_a, image_b = _image(), _second_image()
    rig = _RoundRig(monkeypatch, ocr=lambda image: "A 原文" if image is image_a else "B 原文",
                    translator=translator, run_inline=False, host=_retranslate_host())
    rig.run_and(ACTION_TRANSLATE, image=image_a, selection=_SELECTION)
    assert _pump_until(lambda: not rig.actions.busy)
    assert rig.panel.secondary_text() == "A 原文→译文"

    rig.actions._retranslate("日本語")       # 重翻发起：图/选区在**这一刻**定下来
    assert rig.actions._pending_retranslate is not None

    rig.run_and(ACTION_ASK, image=image_b, selection=_SELECTION_B)   # 第二次采集：开浮窗，不上面板
    assert _pump_until(lambda: not rig.actions.busy)
    assert rig.actions._pending_retranslate is not None, "这次采集把重翻槽占了"
    # 前提：这次采集确实带着 B 的图与选区走完了（不然"串用"根本测不出来）
    assert rig.ask.calls[0][0] is image_b and rig.ask.calls[0][2] is _SELECTION_B
    opened = len(rig.ask.calls)

    gate.set()
    assert _pump_until(lambda: rig.actions._pending_retranslate is None)
    assert rig.panel.secondary_text() == "A 原文→译文"

    # 摆上去的那张卡带的是哪张图、哪块选区（编排层交给展示层的原件）
    shown_image, shown_below, _round = rig.shown[-1]
    assert shown_image is image_a, "重翻那张卡摆上的是别人的图（跨截图串用）"
    assert shown_below is _SELECTION, "重翻那张卡用的是另一次框选的选区"

    assert rig.panel.continue_button().isVisible() is True
    rig.panel.continue_button().click()
    assert len(rig.ask.calls) == opened + 1, f"「继续提问」没有把参数交出去：{rig.ask.calls}"
    got_image, got_text, got_below = rig.ask.calls[-1]
    assert got_image is image_a, "重翻那张卡把别人的图交给了问答浮窗（跨截图串用）"
    assert got_text == "A 原文", f"重翻那张卡的正文不是这段原文：{got_text!r}"
    assert got_below is _SELECTION, "重翻那张卡用的是另一次框选的选区"
    rig.close()


def test_a_retranslate_returning_after_a_second_screenshot_cannot_overwrite_it(monkeypatch):
    """第二张截图已经占住面板：A 的旧重翻终态不许覆写正文、不许停它的表（P1 之二）。

    返回序的这一半＝旧终态**后**到。真实时序：截 A 翻译 → 用户切语言（重翻闸住）→
    用户又截 B 翻译（B 的过程态卡片占住面板，A 的图/选区/原文在这一刻全被换掉）→
    A 的重翻回来。旧实现把 A 的原文/译文写在 B 的卡片上、把 B 还在转的加载动画收掉，
    用户看到的是一张静止的旧结果；随后 B 自己的结果又把面板抢回来——屏上那一段时间
    的图文根本不是同一次操作的产物。
    """
    retranslate_gate = threading.Event()
    ocr_gate = threading.Event()
    translate_gate = threading.Event()
    ocr_images: list = []
    translate_sources: list[str] = []

    def ocr(image):
        ocr_images.append(image)
        if len(ocr_images) >= 2:         # 第二张的识别：闸住，好让旧重翻先回来
            ocr_gate.wait(10)
        return "A 原文" if image is image_a else "B 原文"

    def translator(_image, source):
        translate_sources.append(source)
        if len(translate_sources) == 2:      # 第二次＝A 的重翻：闸住
            retranslate_gate.wait(10)
        elif len(translate_sources) == 3:    # 第三次＝B 的翻译：闸住，好在卡片在屏上看
            translate_gate.wait(10)
        return f"{source}→译文"

    image_a, image_b = _image(), _second_image()
    rig = _RoundRig(monkeypatch, ocr=ocr, translator=translator, run_inline=False,
                    host=_retranslate_host())
    rig.run_and(ACTION_TRANSLATE, image=image_a, selection=_SELECTION)
    assert _pump_until(lambda: not rig.actions.busy)
    assert rig.panel.secondary_text() == "A 原文→译文"

    rig.actions._retranslate("日本語")       # A 的重翻：闸住
    assert rig.actions._pending_retranslate is not None

    rig.run_and(ACTION_TRANSLATE, image=image_b, selection=_SELECTION_B)   # 第二张占住面板
    animation = rig.animation
    second_face = rig.face
    assert rig.rolls == 3, "第二张的过程态卡片没有抽签"
    assert rig.panel.primary_text() == "" and rig.panel.secondary_text() == ""

    retranslate_gate.set()                   # A 的旧终态回来
    assert _pump_until(lambda: rig.actions._pending_retranslate is None)
    # 旧终态确实来过展示层（不是"根本没发生"），且带着**自己那张图**（快照生效）——
    # 是展示层按轮次归属把它挡在第二张的卡片之外
    assert rig.shown[-1][0] is image_a, "旧终态的请求里带着别人的图"
    assert rig.panel.primary_text() == "", "旧重翻把第二张的卡片正文覆写了"
    assert rig.panel.secondary_text() == "" and rig.panel.has_secondary() is False
    assert rig.panel.status_text() == "", "旧重翻把第二张的状态行占了"
    assert animation._timer.isActive() is True, "旧终态把第二张还在跑的表停了"
    assert animation.isVisible() is True, "旧终态把第二张的加载卡收掉了"
    assert rig.face is second_face and rig.rolls == 3, "旧终态给第二张换了脸"

    ocr_gate.set()                           # 放第二张走完
    assert _pump_until(lambda: rig.panel.primary_text() == "B 原文")
    translate_gate.set()
    assert _pump_until(lambda: not rig.actions.busy)
    assert rig.panel.primary_text() == "B 原文", "第二张的原文被旧重翻的余波顶掉了"
    assert rig.panel.secondary_text() == "B 原文→译文"

    assert rig.panel.continue_button().isVisible() is True
    rig.panel.continue_button().click()
    assert len(rig.ask.calls) == 1, f"「继续提问」没有把参数交出去：{rig.ask.calls}"
    got_image, got_text, got_below = rig.ask.calls[0]
    assert got_image is image_b, "第二张的卡片交出去的是另一张图"
    assert got_text == "B 原文", f"第二张的卡片交出去的是别人的正文：{got_text!r}"
    assert got_below is _SELECTION_B, "第二张的卡片用了另一次框选的选区"
    rig.close()


def test_a_retranslate_landing_first_is_shown_with_its_own_context_then_superseded(monkeypatch):
    """返回序的另一半：重翻**先**回来——它照常落地，且带着自己那张图与选区。

    守卫认的是"有没有更新的轮次**到过**面板"，不是"后面还会不会有新操作"：重翻先回来时
    第二张还没开始，它必须显示（把它丢掉才是把用户刚点的动作吃掉）；随后第二张接棒，
    面板上换成第二张的图与文字，两张卡各自的「继续提问」都交自己那份上下文。
    """
    gate = threading.Event()
    sources: list[str] = []

    def translator(_image, source):
        sources.append(source)
        if len(sources) >= 2:            # 第二次＝重翻：闸住
            gate.wait(10)
        return f"{source}→译文"

    image_a, image_b = _image(), _second_image()
    rig = _RoundRig(monkeypatch, ocr=lambda image: "A 原文" if image is image_a else "B 原文",
                    translator=translator, run_inline=False, host=_retranslate_host())
    rig.run_and(ACTION_TRANSLATE, image=image_a, selection=_SELECTION)
    assert _pump_until(lambda: not rig.actions.busy)

    rig.actions._retranslate("日本語")
    gate.set()
    assert _pump_until(lambda: rig.actions._pending_retranslate is None)
    assert rig.panel.secondary_text() == "A 原文→译文", "先回来的重翻没落地"

    rig.panel.continue_button().click()
    got_image, got_text, got_below = rig.ask.calls[-1]
    assert got_image is image_a and got_text == "A 原文" and got_below is _SELECTION

    rig.run_and(ACTION_TRANSLATE, image=image_b, selection=_SELECTION_B)   # 第二张接棒
    assert _pump_until(lambda: not rig.actions.busy)
    assert rig.panel.secondary_text() == "B 原文→译文", "第二张的结果没落地"
    assert rig.panel.continue_button().isVisible() is True

    rig.panel.continue_button().click()
    assert len(rig.ask.calls) == 2, f"两次「继续提问」的实收参数是 {rig.ask.calls}"
    got_image, got_text, got_below = rig.ask.calls[1]
    assert got_image is image_b and got_text == "B 原文" and got_below is _SELECTION_B
    rig.close()


def test_a_failed_retranslate_is_reported_with_its_own_context_when_it_is_the_newest(monkeypatch):
    """重翻失败也是终态：没有更新的轮次时照常摆出来，且带的是**这段原文那张图**。

    （守卫不是"一律丢弃旧东西"：失败卡片只在"它还是最新那一轮"时摆——否则用户点了语言
    却没有下文。这里同时钉住失败卡片自己的图/选区也是发起时捕获的那一份。）
    """
    calls: list[str] = []

    def translator(_image, source):
        calls.append(source)
        if len(calls) >= 2:              # 第二次＝重翻：直接炸
            raise RuntimeError("重翻炸了")
        return f"{source}→译文"

    image_a = _image()
    rig = _RoundRig(monkeypatch, ocr=lambda _image: "A 原文", translator=translator,
                    run_inline=False, host=_retranslate_host())
    rig.run_and(ACTION_TRANSLATE, image=image_a, selection=_SELECTION)
    assert _pump_until(lambda: not rig.actions.busy)
    assert rig.panel.secondary_text() == "A 原文→译文"

    rig.actions._retranslate("日本語")
    assert _pump_until(lambda: rig.actions._pending_retranslate is None)
    assert "翻译失败" in rig.panel.status_text(), "重翻失败没有告诉用户"
    assert rig.panel.primary_text() == "A 原文"

    assert rig.panel.continue_button().isVisible() is True
    rig.panel.continue_button().click()
    got_image, got_text, got_below = rig.ask.calls[-1]
    assert got_image is image_a and got_text == "A 原文" and got_below is _SELECTION
    rig.close()


def test_a_failed_retranslate_cannot_overwrite_a_newer_screenshot(monkeypatch):
    """重翻失败的终态同样受展示归属约束：它也不许覆写/收掉第二张的卡片。

    （失败出口容易漏——它走的是"直接摆一张说明卡"的旁路，不带采集链路那套收口；
    旁路同样要带**自己的**轮次身份，否则第二张那轮会被一张旧失败卡顶掉。）
    """
    retranslate_gate = threading.Event()
    ocr_gate = threading.Event()
    ocr_images: list = []
    translate_calls: list[str] = []

    def ocr(image):
        ocr_images.append(image)
        if len(ocr_images) >= 2:         # 第二张的识别：闸住，好让失败卡先回来
            ocr_gate.wait(10)
        return "A 原文" if image is image_a else "B 原文"

    def translator(_image, source):
        translate_calls.append(source)
        if len(translate_calls) == 2:    # 第二次＝A 的重翻：闸住后炸（第三张的翻译照常）
            retranslate_gate.wait(10)
            raise RuntimeError("重翻炸了")
        return f"{source}→译文"

    image_a, image_b = _image(), _second_image()
    rig = _RoundRig(monkeypatch, ocr=ocr, translator=translator, run_inline=False,
                    host=_retranslate_host())
    rig.run_and(ACTION_TRANSLATE, image=image_a, selection=_SELECTION)
    assert _pump_until(lambda: not rig.actions.busy)

    rig.actions._retranslate("日本語")
    assert rig.actions._pending_retranslate is not None

    rig.run_and(ACTION_TRANSLATE, image=image_b, selection=_SELECTION_B)
    animation = rig.animation
    second_face = rig.face
    assert rig.rolls == 3

    retranslate_gate.set()                   # 旧重翻以失败告终
    assert _pump_until(lambda: rig.actions._pending_retranslate is None)
    assert rig.shown[-1][0] is image_a, "旧失败卡的请求里带着别人的图"
    assert "翻译失败" not in rig.panel.status_text(), "旧失败卡把第二张的状态行占了"
    assert rig.panel.primary_text() == "", "旧失败卡把第二张的正文覆写了"
    assert animation._timer.isActive() is True, "旧失败卡把第二张还在跑的表停了"
    assert rig.face is second_face and rig.rolls == 3

    ocr_gate.set()
    assert _pump_until(lambda: not rig.actions.busy)
    assert rig.panel.primary_text() == "B 原文"
    assert rig.panel.secondary_text() == "B 原文→译文"
    rig.close()


def test_cancelling_the_second_screenshot_ends_its_round_and_the_stale_card_stays_off(monkeypatch):
    """取消归属不退：取消收的是**自己那一轮**的表，旧重翻的终态不许把卡片拉回来。

    （取消/失败这类"没有终态内容"的出口靠 ``end_loading(自己那一轮)`` 收口；这一条把
    取消放进跨截图的格局里：第二张被取消之后，A 的旧重翻回来也不许让面板重新长出卡片。）
    """
    retranslate_gate = threading.Event()
    ocr_gate = threading.Event()
    translate_calls: list[str] = []

    def ocr(image):
        if image is image_b:
            ocr_gate.wait(10)            # 第二张的识别：闸住，让取消落在卡片在屏那一刻
        return "A 原文" if image is image_a else "B 原文"

    def translator(_image, source):
        translate_calls.append(source)
        if len(translate_calls) >= 2:    # 第二次＝A 的重翻：闸住
            retranslate_gate.wait(10)
        return f"{source}→译文"

    image_a, image_b = _image(), _second_image()
    rig = _RoundRig(monkeypatch, ocr=ocr, translator=translator, run_inline=False,
                    host=_retranslate_host())
    rig.run_and(ACTION_TRANSLATE, image=image_a, selection=_SELECTION)
    assert _pump_until(lambda: not rig.actions.busy)

    rig.actions._retranslate("日本語")       # A 的重翻：闸住
    assert rig.actions._pending_retranslate is not None

    rig.run_and(ACTION_TRANSLATE, image=image_b, selection=_SELECTION_B)   # 第二张在跑
    animation = rig.animation
    assert animation._timer.isActive() is True

    rig.cancel()                             # 用户取消第二张
    assert rig.actions.busy is False
    assert animation._timer.isActive() is False, "取消没有收掉自己那一轮的表"
    assert animation.isVisible() is False, "取消没有收掉自己那一轮的卡片"

    retranslate_gate.set()                   # A 的旧终态回来：不许把卡片拉回来
    assert _pump_until(lambda: rig.actions._pending_retranslate is None)
    assert animation._timer.isActive() is False, "旧终态把已经取消的那一轮拉回来了"
    assert animation.isVisible() is False, "旧终态把取消掉的卡片摆回来了"
    assert rig.panel.secondary_text() == "", "旧终态在取消之后往面板上写了正文"

    ocr_gate.set()                           # 放掉被闸住的后台步（结果属于已取消的那一轮）
    assert _pump_until(
        lambda: not any(t.name == "pet-screen-capture" for t in threading.enumerate()))
    assert rig.panel.secondary_text() == "", "取消之后迟到的结果往面板上写了正文"
    assert rig.rolls == 3, "取消之后迟到的结果又抽了一轮"
    rig.close()
