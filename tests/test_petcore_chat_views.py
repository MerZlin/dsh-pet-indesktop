"""`chat_view` 插件缝：标题栏按钮、懒建、生命周期、故障隔离、深色扇出。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from petcore.config import Config
from petcore.contracts.contributions import ChatViewSpec, Contribution
from petcore.contracts.feature import FeatureDefinition
from petcore.mods.feature import FeatureManager, LoadedFeature
from petcore.ui.chat import ChatController
from petcore.ui.chat.window import ChatWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class FakeView(QWidget):
    """最小扩展视图替身：记录 core 调了哪些生命周期方法。"""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[object] = []

    def activate(self) -> None:
        self.calls.append("activate")

    def deactivate(self) -> None:
        self.calls.append("deactivate")

    def set_dark(self, dark: bool) -> None:
        self.calls.append(("set_dark", bool(dark)))

    def shutdown(self) -> None:
        self.calls.append("shutdown")


def _spec(view_id: str = "tool.demo:web", label: str = "网页 AI", factory=None, order: int = 100):
    return ChatViewSpec(
        id=view_id,
        owner=view_id.split(":", 1)[0],
        view_id=view_id.split(":", 1)[1],
        label=label,
        factory=factory or (lambda: FakeView()),
        icon="globe",
        order=order,
    )


def _window(**kwargs) -> ChatWindow:
    kwargs.setdefault("dsh", None)
    window = ChatWindow(**kwargs)
    window.show()
    return window


def _close(window: ChatWindow) -> None:
    window.close()
    window.deleteLater()


# ---------------------------------------------------------------- 窗口侧
def test_view_button_is_added_to_the_title_bar(app):
    window = _window(views=[_spec()])
    try:
        buttons = window.findChildren(type(window.view_button), "chat-view-button")
        assert len(buttons) == 1
        assert buttons[0].text() == "网页 AI"
        layout = window.title_bar.layout()
        # 顺序：DSH → 扩展视图 → 侧栏 → 深色 → 置顶 → 最小化 → 最大化 → 关闭
        assert layout.indexOf(window.view_button) < layout.indexOf(buttons[0])
        assert layout.indexOf(buttons[0]) < layout.indexOf(window.sidebar_toggle_button)
        assert layout.indexOf(window.sidebar_toggle_button) < layout.indexOf(window.dark_button)
    finally:
        _close(window)


def test_no_contributed_view_means_no_button(app):
    window = _window()
    try:
        assert window.findChildren(QWidget, "chat-view-button") == []
        assert window.view_ids() == []
        assert window.current_view() == "ai"
    finally:
        _close(window)


def test_switching_to_a_view_is_lazy_and_activates(app):
    built: list[FakeView] = []

    def factory() -> FakeView:
        view = FakeView()
        built.append(view)
        return view

    window = _window(views=[_spec(factory=factory)])
    try:
        assert built == []  # 没切过去就不建
        assert window.show_view("tool.demo:web") is True
        assert window.current_view() == "tool.demo:web"
        assert len(built) == 1
        assert built[0].calls == [("set_dark", False), "activate"]
        # 再切一次不重建
        window.set_view("ai")
        window.set_view("tool.demo:web")
        assert len(built) == 1
        assert built[0].calls[-1] == "activate"
    finally:
        _close(window)


def test_clicking_the_active_view_button_returns_to_ai(app):
    built: list[FakeView] = []

    def factory() -> FakeView:
        view = FakeView()
        built.append(view)
        return view

    window = _window(views=[_spec(factory=factory)])
    try:
        button = window._view_buttons["tool.demo:web"]
        button.click()
        assert window.current_view() == "tool.demo:web"
        assert len(built) == 1
        # 已经停在该视图：再点一次要回到 AI 对话页，而不是死点
        button.click()
        assert window.current_view() == "ai"
        assert window.view_stack.currentWidget() is window.ai_view
        assert button.isChecked() is False
        assert built[0].calls.count("deactivate") == 1
        assert len(built) == 1  # 页面对象没被重建
        button.click()
        assert window.current_view() == "tool.demo:web"
        assert len(built) == 1
        assert window._view_widgets["tool.demo:web"] is built[0]
    finally:
        _close(window)


def test_clicking_an_inactive_view_button_switches_to_it(app):
    window = _window(views=[_spec()])
    try:
        assert window.current_view() == "ai"
        window._view_buttons["tool.demo:web"].click()
        assert window.current_view() == "tool.demo:web"
        assert window.view_stack.currentWidget() is window._view_pages["tool.demo:web"]
    finally:
        _close(window)


def test_unknown_view_is_refused(app):
    window = _window(views=[_spec()])
    try:
        assert window.show_view("tool.demo:nope") is False
        assert window.current_view() == "ai"
    finally:
        _close(window)


def test_factory_failure_is_isolated(app):
    def boom():
        raise RuntimeError("mod 崩了")

    window = _window(views=[_spec(factory=boom)])
    try:
        assert window.show_view("tool.demo:web") is True
        assert window.current_view() == "tool.demo:web"
        labels = window.findChildren(QWidget, "chat-view-error")
        assert labels, "工厂失败应显示错误占位"
        # 对话窗其余功能仍然可用
        window.set_view("ai")
        assert window.current_view() == "ai"
        assert window.composer.isVisible()
    finally:
        _close(window)


def test_non_widget_factory_result_falls_back(app):
    window = _window(views=[_spec(factory=lambda: object())])
    try:
        assert window.show_view("tool.demo:web") is True
        assert window.findChildren(QWidget, "chat-view-error")
    finally:
        _close(window)


def test_switching_away_deactivates_and_close_shuts_down(app):
    views: list[FakeView] = []

    def factory() -> FakeView:
        view = FakeView()
        views.append(view)
        return view

    window = _window(views=[_spec(factory=factory)])
    try:
        window.show_view("tool.demo:web")
        window.set_view("ai")
        assert "deactivate" in views[0].calls
        window.show_view("tool.demo:web")
        window.close()
        assert "shutdown" in views[0].calls
    finally:
        window.deleteLater()


def test_view_changed_only_fires_for_ai_and_dsh(app):
    seen: list[str] = []
    window = _window(views=[_spec()])
    window.view_changed.connect(seen.append)
    try:
        window.show_view("tool.demo:web")
        assert seen == []
        window._on_view_toggled(True)
        assert seen == ["dsh"]
        window._on_view_toggled(False)
        assert seen == ["dsh", "ai"]
    finally:
        _close(window)


def test_third_view_goes_into_overflow_menu(app):
    specs = [
        _spec("a.one:v", "视图一", order=1),
        _spec("b.two:v", "视图二", order=2),
        _spec("c.three:v", "视图三", order=3),
    ]
    window = _window(views=specs)
    try:
        inline = window.findChildren(QWidget, "chat-view-button")
        assert len(inline) == 2
        assert window.view_more_button is not None
        actions = [action.text() for action in window.view_more_menu.actions()]
        assert actions == ["视图三"]
        window.set_view("c.three:v")
        assert window.current_view() == "c.three:v"
    finally:
        _close(window)


def test_clicking_the_active_view_in_the_overflow_menu_returns_to_ai(app):
    specs = [
        _spec("a.one:v", "视图一", order=1),
        _spec("b.two:v", "视图二", order=2),
        _spec("c.three:v", "视图三", order=3),
    ]
    window = _window(views=specs)
    try:
        action = window.view_more_menu.actions()[0]
        action.trigger()
        assert window.current_view() == "c.three:v"
        action.trigger()
        assert window.current_view() == "ai"
    finally:
        _close(window)


# ---------------------------------------------------------------- 深色
def test_dark_toggle_restyles_and_fans_out(app):
    built: list[FakeView] = []

    def factory() -> FakeView:
        view = FakeView()
        built.append(view)
        return view

    window = _window(views=[_spec(factory=factory)])
    try:
        window.show_view("tool.demo:web")
        assert window.is_dark() is False
        window.dark_button.setChecked(True)
        assert window.is_dark() is True
        sheet = window.styleSheet()
        assert "QFrame#phone-shell { background: #16181d" in sheet
        assert ("set_dark", True) in built[0].calls
        window.dark_button.setChecked(False)
        assert window.is_dark() is False
        assert "QFrame#phone-shell { background: #16181d" not in window.styleSheet()
    finally:
        _close(window)


def test_dark_button_state_matches_constructor(app):
    window = _window(dark=True)
    try:
        assert window.is_dark() is True
        assert window.dark_button.isChecked() is True
    finally:
        _close(window)


# ---------------------------------------------------------------- 收集器
def _manager_with(definition: FeatureDefinition, path: Path) -> FeatureManager:
    manager = FeatureManager(ports=None)
    manager.features[definition.owner] = LoadedFeature(
        owner=definition.owner, definition=definition, path=path
    )
    return manager


def test_feature_manager_collects_and_sorts_views(tmp_path: Path):
    factory = lambda: FakeView()  # noqa: E731
    definition = FeatureDefinition(
        owner="tool.demo",
        contributions=(
            Contribution(kind="chat_view", id="late", data={"label": "后", "order": 900}),
            Contribution(kind="chat_view", id="early", data={"label": "前", "order": 10}),
            Contribution(kind="chat_view", id="broken", data={"label": "", "order": 5}),
            Contribution(kind="chat_view", id="nofactory", data={"label": "缺工厂"}),
        ),
        view_factories={"late": factory, "early": factory},
    )
    specs = _manager_with(definition, tmp_path).chat_views()
    assert [spec.id for spec in specs] == ["tool.demo:early", "tool.demo:late"]
    assert [spec.label for spec in specs] == ["前", "后"]
    assert all(spec.icon == "" for spec in specs)


def test_chat_view_contribution_kind_is_declared():
    Contribution(kind="chat_view", id="v", data={"label": "视图"})
    with pytest.raises(ValueError):
        Contribution(kind="chat_view_typo", id="v")


# ---------------------------------------------------------------- ChatPort
class _FakeHost:
    def __init__(self, root: Path, definition: FeatureDefinition) -> None:
        self.config = Config(root)
        self.pets = None
        self._manager = _manager_with(definition, root)

    @property
    def features(self):
        return self._manager


@pytest.fixture()
def controller(app, tmp_path: Path):
    definition = FeatureDefinition(
        owner="tool.demo",
        contributions=(
            Contribution(kind="chat_view", id="web", data={"label": "网页 AI"}),
        ),
        view_factories={"web": lambda: FakeView()},
    )
    host = _FakeHost(tmp_path, definition)
    control = ChatController(host, tmp_path)
    yield control
    for state in control.states().values():
        if state.window is not None:
            state.window.close()
            state.window.deleteLater()


def test_show_view_opens_the_window_on_that_page(controller):
    assert controller.show_view("tool.demo", "web") is True
    window = controller.window_for("tool.demo")
    assert window is not None and window.isVisible()
    assert window.current_view() == "tool.demo:web"


def test_show_view_unknown_id_does_not_open_a_window(controller):
    assert controller.show_view("tool.demo", "missing") is False
    assert controller.window_for("tool.demo") is None


def test_show_view_reuses_the_window_the_user_is_using(controller):
    """网页页是对话窗的一页：菜单「AI 网页」不该另开一扇窗。"""
    assert controller.open("ai.chat") is True
    chat_window = controller.window_for("ai.chat")
    assert controller.show_view("tool.demo", "web") is True
    assert controller.window_for("tool.demo") is None
    assert controller.window_for("ai.chat") is chat_window
    assert chat_window.current_view() == "tool.demo:web"


def test_dark_choice_is_persisted_by_the_controller(controller, tmp_path: Path):
    assert controller.open("ai.chat") is True
    window = controller.window_for("ai.chat")
    assert window is not None
    window.dark_button.setChecked(True)
    saved = Config.load(tmp_path).get("chat_dark_mode")
    assert saved is True
    assert controller.window_for("ai.chat").is_dark() is True
