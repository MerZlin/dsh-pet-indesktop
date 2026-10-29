"""AI 对话扩展：manifest、prompt 组装、流式回传、截图提问、无服务提示。"""

from __future__ import annotations

import importlib.util
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from petcore.config import Config
from petcore.contracts.screen import RegionPick, ScreenCapture
from petcore.mods.feature import import_entrypoint
from petcore.mods.manifest import load_manifest

MOD_DIR = Path(__file__).resolve().parents[1] / "examples" / "petcore-mods" / "ai.chat"


def _create_host(context):
    factory = import_entrypoint(MOD_DIR, "host/__init__.py:create_host", "ai.chat")
    return factory(context)


def _load_module(name: str):
    path = MOD_DIR / "host" / name
    spec = importlib.util.spec_from_file_location(f"ai_chat_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeChat:
    OWNER = "ai.chat"

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.done_event = threading.Event()
        self.history_messages: list[dict] = []
        self.submit_callback = None
        self.stop_callback = None
        self.records: list[tuple[str, str, int]] = []
        self.attachments: list[dict] = []

    def open(self, owner, *, pet_slot=None, title=""):
        self.calls.append(("open", pet_slot, title))
        return True

    def append(self, owner, role, text, *, pet_slot=None):
        self.calls.append(("append", role, text))

    def record(self, owner, role, text, *, pet_slot=None):
        self.records.append((role, text, pet_slot))

    def take_attachments(self, owner, *, pet_slot=None):
        pending, self.attachments = list(self.attachments), []
        return pending

    def token(self, owner, delta, *, pet_slot=None):
        self.calls.append(("token", delta))

    def reasoning(self, owner, delta, *, pet_slot=None):
        self.calls.append(("reasoning", delta))

    def status(self, owner, text, *, pet_slot=None):
        self.calls.append(("status", text))

    def done(self, owner, text="", *, pet_slot=None):
        self.calls.append(("done", text))
        self.done_event.set()

    def error(self, owner, reason, message="", *, pet_slot=None):
        self.calls.append(("error", reason, message))
        self.done_event.set()

    def history(self, owner, *, pet_slot=None):
        return list(self.history_messages)

    def on_submit(self, owner, callback):
        self.submit_callback = callback
        return lambda: None

    def on_stop(self, owner, callback):
        self.stop_callback = callback
        return lambda: None


class FakeApi:
    def __init__(self, service: bool = True) -> None:
        self.service = service
        self.requests: list[dict] = []
        self.search_keys: dict[str, str] = {}

    def services(self):
        if not self.service:
            return []
        return [
            SimpleNamespace(
                service_id="svc", name="服务", model="chat-model", vision_model="vision-model"
            )
        ]

    def stream_chat(
        self,
        service_id,
        messages,
        *,
        model="",
        temperature=None,
        timeout=60.0,
        on_delta=None,
        on_reasoning=None,
        on_status=None,
        thinking="auto",
        reasoning_effort="",
        web_search=None,
        cancel=None,
    ):
        self.requests.append(
            {
                "service_id": service_id,
                "messages": messages,
                "model": model,
                "temperature": temperature,
                "thinking": thinking,
                "reasoning_effort": reasoning_effort,
                "web_search": web_search,
            }
        )
        if on_status is not None:
            on_status("正在判断是否需要联网…")
        if on_reasoning is not None:
            on_reasoning("想一想")
        if on_delta is not None:
            on_delta("答")
            on_delta("案")
        return SimpleNamespace(text="答案", chunks=2, cancelled=False)

    def open_settings(self):
        self.requests.append({"open_settings": True})

    def web_search_key_status(self):
        return {engine: True for engine in self.search_keys}

    def set_web_search_key(self, engine, value):
        self.search_keys[str(engine)] = str(value)

    def credentials_available(self):
        return True


class FakeScreen:
    def __init__(self) -> None:
        self.calls = 0
        self.region_calls = 0
        self.region_action = "ask"
        self.ai_available = True

    def capture(self, *, pet_slot=None):
        self.calls += 1
        return ScreenCapture(
            png=b"png-bytes",
            width=10,
            height=10,
            screen_name="fake",
            app_info="code.exe | main.py",
        )

    def capture_region(self, *, pet_slot=None, ai_available=True):
        self.region_calls += 1
        self.ai_available = bool(ai_available)
        return RegionPick(
            ScreenCapture(png=b"region-png", width=8, height=6, screen_name="fake"),
            self.region_action,
        )

    def foreground_app(self):
        return "code.exe | main.py"


class FakeScreenAsk:
    def __init__(self) -> None:
        self.opened: list[tuple] = []
        self.open_actions: list[tuple] = []
        self.callback = None
        self.action_callback = None

    def open(self, owner, capture, *, pet_slot=None, title=""):
        self.opened.append((owner, capture, pet_slot, title))
        return True

    def open_action(self, owner, action, capture, *, text="", pet_slot=None):
        self.open_actions.append((owner, action, capture, text, pet_slot))
        return True

    def on_action(self, owner, callback, *, priority=100, actions=()):
        self.action_callback = callback
        return lambda: None

    def on_ask(self, owner, callback):
        self.callback = callback
        return lambda: None

    def history(self, owner, *, pet_slot=None):
        return []

    def is_open(self, owner, *, pet_slot=None):
        return bool(self.opened)

    def close(self, owner, *, pet_slot=None):
        return None


class FakeQuickChat:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.submit_callback = None
        self.history_messages: list[dict] = []
        self.done_event = threading.Event()

    def on_submit(self, owner, callback):
        self.submit_callback = callback
        return lambda: None

    def history(self, owner, *, pet_slot=None):
        return list(self.history_messages)

    def open(self, owner, *, pet_slot=None):
        self.calls.append(("open", pet_slot))
        return True

    def close(self, owner, *, pet_slot=None):
        return None

    def is_open(self, owner, *, pet_slot=None):
        return True

    def token(self, owner, delta, *, pet_slot=None):
        self.calls.append(("token", delta))

    def reasoning(self, owner, delta, *, pet_slot=None):
        self.calls.append(("reasoning", delta))

    def status(self, owner, text, *, pet_slot=None):
        self.calls.append(("status", text))

    def done(self, owner, text="", *, pet_slot=None):
        self.calls.append(("done", text))
        self.done_event.set()

    def error(self, owner, reason, message="", *, pet_slot=None):
        self.calls.append(("error", reason, message))
        self.done_event.set()

    def open_full_chat(self, owner, *, pet_slot=None):
        self.calls.append(("open_full_chat", pet_slot))


class FakePets:
    def active(self):
        return SimpleNamespace(slot=2)

    def primary(self):
        return SimpleNamespace(slot=0)


class FakePresentation:
    def __init__(self) -> None:
        self.said: list[str] = []

    def say(self, text, **_kwargs):
        self.said.append(str(text))


class FakeConfig:
    """HostPorts.config 的测试实现（只读）。"""

    def __init__(self, config: Config) -> None:
        self._config = config

    def get(self, key, default=None):
        value = self._config.get(key, default)
        return default if value is None else value

    def set(self, key, value):
        self._config.set(str(key), value)


class FakeScheduler:
    """立即执行版 scheduler；生产实现会排队到 GUI 线程。"""

    def __init__(self) -> None:
        self.calls = 0

    def call_later(self, delay_ms, callback):
        self.calls += 1
        callback()
        return f"timer-{self.calls}"


def _context(tmp_path: Path, *, service: bool = True):
    host = SimpleNamespace(
        config=Config(tmp_path),
        character_for=lambda character_id: SimpleNamespace(name="鲸鱼娘"),
    )
    host.config.set("character", "shen")
    ports = SimpleNamespace(
        api=FakeApi(service=service),
        chat=FakeChat(),
        quick_chat=FakeQuickChat(),
        screen=FakeScreen(),
        screen_ask=FakeScreenAsk(),
        pets=FakePets(),
        presentation=FakePresentation(),
        config=FakeConfig(host.config),
        scheduler=FakeScheduler(),
    )
    return SimpleNamespace(host=host, ports=ports)


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def _wait_until(app, predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(predicate())


@pytest.fixture()
def runtime(tmp_path: Path):
    context = _context(tmp_path)
    definition = _create_host(context)
    instance = definition.runtime_factory(context)
    instance.start()
    yield instance
    instance.stop()


def test_manifest_is_a_host_only_feature_with_capabilities() -> None:
    manifest, problems = load_manifest(MOD_DIR)
    assert problems == []
    assert manifest is not None
    assert manifest.id == "ai.chat"
    assert manifest.kind == "feature"
    assert manifest.execution == "host-only"
    assert "screenshot.capture" in manifest.capabilities
    assert "network.request" in manifest.capabilities


def test_definition_exposes_three_commands_and_a_settings_page(tmp_path: Path) -> None:
    definition = _create_host(_context(tmp_path))
    assert definition.owner == "ai.chat"
    assert [item.kind for item in definition.contributions] == [
        "settings_page",
        "menu",
        "menu",
        "menu",
        "menu",
    ]
    # L108：AI 对话/看看屏幕/截图提问 也是这个扩展注册出来的菜单项
    assert [item.id for item in definition.contributions if item.kind == "menu"] == [
        "open",
        "look_screen",
        "screen_capture",
        "proactive",
    ]
    instance = definition.runtime_factory(_context(tmp_path))
    assert set(instance.commands) == {
        "open",
        "look_screen",
        "screen_capture",
        "proactive_toggle",
    }


def test_submit_sends_system_prompt_and_history(runtime) -> None:
    chat = runtime.context.ports.chat
    chat.history_messages = [
        {"role": "user", "content": "上一句"},
        {"role": "assistant", "content": "上一答"},
        {"role": "user", "content": "现在这句"},
    ]
    runtime._on_submit("现在这句", 2)
    assert chat.done_event.wait(5), "流式回调没有结束"
    request = runtime.context.ports.api.requests[0]
    assert request["service_id"] == "svc"
    messages = request["messages"]
    assert messages[0]["role"] == "system"
    assert "鲸鱼娘" in messages[0]["content"]
    assert [item["content"] for item in messages[1:]] == ["上一句", "上一答", "现在这句"]
    assert ("token", "答") in chat.calls and ("token", "案") in chat.calls
    assert ("done", "答案") in chat.calls


def test_open_creates_a_chat_window_for_the_active_pet(runtime) -> None:
    runtime.open_chat()
    chat = runtime.context.ports.chat
    assert chat.calls[0][0] == "open"
    assert chat.calls[0][1] == 2


def test_screen_capture_opens_the_region_ask_window(runtime) -> None:
    runtime.screen_capture(2)
    ask = runtime.context.ports.screen_ask
    screen = runtime.context.ports.screen
    assert screen.region_calls == 1
    assert ask.opened and ask.opened[0][1].png == b"region-png"
    assert ask.opened[0][2] == 2
    # 旧流程会自动把整屏图塞进主对话；新流程必须等用户在 ask 窗口里发问。
    assert runtime.context.ports.chat.calls == []


def test_ask_callback_sends_image_and_history(runtime) -> None:
    reply = runtime._on_ask(
        "这是什么？",
        b"region-png",
        [{"role": "user", "content": "第一问"}, {"role": "assistant", "content": "第一答"}],
        2,
    )
    assert reply == "答案"
    request = runtime.context.ports.api.requests[-1]
    messages = request["messages"]
    assert messages[0]["role"] == "system"
    assert messages[1]["content"][0]["text"] == "第一问"
    assert messages[1]["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    assert messages[2] == {"role": "assistant", "content": "第一答"}
    assert messages[3]["content"] == "这是什么？"


def test_look_screen_replies_in_bubble_and_records_without_opening(
    qt_app, runtime
) -> None:
    runtime.look_screen(2)
    chat = runtime.context.ports.chat
    assert _wait_until(qt_app, lambda: len(chat.records) == 2)
    said = runtime.context.ports.presentation.said
    assert said[0] == "让我看看…"
    assert said[-1] == "答案"
    assert chat.records[0][0] == "user"
    assert "看看屏幕" in chat.records[0][1]
    assert "code.exe" in chat.records[0][1]
    assert chat.records[1] == ("assistant", "答案", 2)
    assert not [call for call in chat.calls if call[0] == "open"]


def test_look_screen_auto_open_toggle_opens_the_chat(qt_app, tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.host.config.set("ai_look_screen_auto_open", True)
    instance = _create_host(context).runtime_factory(context)
    instance.start()
    instance.look_screen(2)
    chat = context.ports.chat
    assert _wait_until(qt_app, lambda: bool([c for c in chat.calls if c[0] == "open"]))
    assert [c for c in chat.calls if c[0] == "open"][0][1] == 2
    instance.stop()


def test_submit_includes_attachment_images(runtime) -> None:
    chat = runtime.context.ports.chat
    chat.attachments = [
        {
            "path": "a.png",
            "name": "a.png",
            "mime": "image/png",
            "text": None,
            "image_data_url": "data:image/png;base64,AAAA",
        }
    ]
    runtime._on_submit("看看这张图", 2)
    assert chat.done_event.wait(5)
    request = runtime.context.ports.api.requests[0]
    content = request["messages"][-1]["content"]
    assert isinstance(content, list)
    assert content[0]["text"] == "看看这张图"
    assert content[1]["image_url"]["url"] == "data:image/png;base64,AAAA"


def test_submit_does_not_duplicate_the_current_user_turn(runtime) -> None:
    chat = runtime.context.ports.chat
    # controller 在回调前已把本轮问题追加进会话；mod 不能再发一遍同样的 user 消息。
    chat.history_messages = [
        {"role": "user", "content": "上一句"},
        {"role": "assistant", "content": "上一答"},
        {"role": "user", "content": "现在这句"},
    ]
    runtime._on_submit("现在这句", 2)
    assert chat.done_event.wait(5)
    messages = runtime.context.ports.api.requests[0]["messages"]
    user_turns = [item["content"] for item in messages if item["role"] == "user"]
    assert user_turns == ["上一句", "现在这句"]


def test_missing_service_reports_instead_of_silently_failing(tmp_path: Path) -> None:
    context = _context(tmp_path, service=False)
    instance = _create_host(context).runtime_factory(context)
    instance._on_submit("你好", 0)
    chat = context.ports.chat
    assert chat.done_event.wait(5), "无服务时必须给出明确错误"
    assert any(call[0] == "error" and call[1] == "no_service" for call in chat.calls)
    assert context.ports.presentation.said


def test_stop_sets_the_cancel_event(runtime) -> None:
    cancel = threading.Event()
    runtime._cancels[2] = cancel
    runtime._on_stop(2)
    assert cancel.is_set()


def test_ai_keys_survive_config_reload(tmp_path: Path) -> None:
    config = Config(tmp_path)
    config.set("ai_vision_enabled", False)
    config.set("ai_temperature", 1.3)
    config.set("ai_look_screen_auto_open", True)
    config.save()
    reloaded = Config.load(tmp_path)
    assert reloaded.get("ai_vision_enabled") is False
    assert reloaded.get("ai_temperature") == 1.3
    assert reloaded.get("ai_look_screen_auto_open") is True


def test_manifest_uses_a_package_entrypoint() -> None:
    manifest, problems = load_manifest(MOD_DIR)
    assert problems == []
    assert manifest is not None
    assert manifest.entrypoint == "host/__init__.py:create_host"


def test_runtime_module_is_qt_and_network_free() -> None:
    runtime_text = (MOD_DIR / "host" / "runtime.py").read_text(encoding="utf-8")
    for needle in ("PySide6", "petcore.net", "petcore.api", "host.config"):
        assert needle not in runtime_text, f"runtime.py 不应出现 {needle}"
    settings_text = (MOD_DIR / "host" / "settings_page.py").read_text(encoding="utf-8")
    assert "PySide6" in settings_text, "设置页是受信 UI，允许使用 Qt"


def test_prompt_builders_keep_history_shapes() -> None:
    prompts = _load_module("prompts.py")
    chat = prompts.chat_messages(
        "sys",
        [
            {"role": "user", "content": "上一句"},
            {"role": "assistant", "content": "上一答"},
            {"role": "user", "content": "现在这句"},
        ],
        "现在这句",
        ["data:image/png;base64,AAAA"],
    )
    assert chat[0] == {"role": "system", "content": "sys"}
    assert chat[1] == {"role": "user", "content": "上一句"}
    assert chat[2] == {"role": "assistant", "content": "上一答"}
    assert chat[3]["content"][0] == {"type": "text", "text": "现在这句"}
    assert chat[3]["content"][1]["image_url"]["url"].endswith("AAAA")

    ask = prompts.ask_messages(
        "sys", [{"role": "user", "content": "第一问"}], "第二问", "data:image/png;base64,BBBB"
    )
    assert ask[1]["content"][0]["text"] == "第一问"
    assert ask[1]["content"][1]["image_url"]["url"].endswith("BBBB")
    assert ask[2] == {"role": "user", "content": "第二问"}


def test_settings_page_exposes_chat_appearance_and_previews(qt_app, tmp_path: Path) -> None:
    context = _context(tmp_path)
    definition = _create_host(context)

    class AppearanceChat:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def apply_appearance(self, **kwargs):
            self.calls.append(dict(kwargs))

    host = SimpleNamespace(
        config=Config(tmp_path),
        features=SimpleNamespace(ports=SimpleNamespace(api=FakeApi())),
        chat=AppearanceChat(),
    )
    page = definition.settings_factory(host)
    try:
        values = [
            page.background_combo.itemData(index)
            for index in range(page.background_combo.count())
        ]
        assert "builtin:whale" in values
        assert "__custom__" in values

        index = page.background_combo.findData("builtin:whale")
        page.background_combo.setCurrentIndex(index)
        assert host.config.get("chat_background") == "builtin:whale"

        page.accent_edit.setText("#FF8800")
        page._on_accent_finished()
        assert host.config.get("chat_accent") == "#ff8800"

        page.follow_check.setChecked(True)
        page.on_top_check.setChecked(True)
        assert host.config.get("chat_follow_pet") is True
        assert host.config.get("chat_always_on_top") is True
        assert host.chat.calls, "设置改动必须实时预览到对话窗"
        assert host.chat.calls[-1]["background"] == "builtin:whale"
        assert host.chat.calls[-1]["accent"] == "#ff8800"
        assert host.chat.calls[-1]["follow"] is True
        assert host.chat.calls[-1]["always_on_top"] is True

        page.accent_edit.setText("不是颜色")
        page._on_accent_finished()
        assert host.config.get("chat_accent") == "#ff8800"
    finally:
        page.close()


def test_thinking_and_web_search_config_reach_the_port(runtime) -> None:
    config = runtime.context.host.config
    config.set("ai_thinking", "enabled")
    config.set("ai_reasoning_effort", "max")
    config.set("ai_web_search", True)
    config.set("ai_web_engine", "bocha")
    config.set("ai_web_results", 3)
    config.set("ai_web_fetch", False)

    runtime._on_submit("联网查一下", 2)
    chat = runtime.context.ports.chat
    assert chat.done_event.wait(5)

    request = runtime.context.ports.api.requests[0]
    assert request["thinking"] == "enabled"
    assert request["reasoning_effort"] == "max"
    options = request["web_search"]
    assert options is not None
    assert (options.engine, options.max_results, options.fetch_pages) == (
        "bocha",
        3,
        False,
    )


def test_reasoning_and_status_reach_the_chat_port(runtime) -> None:
    runtime.context.host.config.set("ai_thinking", "enabled")
    runtime._on_submit("想一想", 2)
    chat = runtime.context.ports.chat
    assert chat.done_event.wait(5)

    assert ("reasoning", "想一想") in chat.calls
    assert ("status", "正在判断是否需要联网…") in chat.calls


def test_settings_page_saves_search_key_to_keyring_port(qt_app, tmp_path: Path) -> None:
    context = _context(tmp_path)
    definition = _create_host(context)
    api = FakeApi()
    host = SimpleNamespace(
        config=Config(tmp_path),
        features=SimpleNamespace(ports=SimpleNamespace(api=api)),
    )
    page = definition.settings_factory(host)
    try:
        page.web_check.setChecked(True)
        page.engine_combo.setCurrentIndex(page.engine_combo.findData("bocha"))
        assert page._web_key_row.isVisibleTo(page) is True

        page.web_key_edit.setText("sk-search")
        page._save_web_key()

        assert api.search_keys == {"bocha": "sk-search"}
        assert page.web_key_edit.text() == ""
        assert page.web_key_state.text() == "已保存"
        assert host.config.get("ai_web_search") is True
        assert host.config.get("ai_web_engine") == "bocha"
    finally:
        page.close()


def test_settings_page_maps_thinking_and_web_toggles(qt_app, tmp_path: Path) -> None:
    context = _context(tmp_path)
    definition = _create_host(context)
    host = SimpleNamespace(
        config=Config(tmp_path),
        features=SimpleNamespace(ports=SimpleNamespace(api=FakeApi())),
    )
    page = definition.settings_factory(host)
    try:
        assert page._effort_row.isVisibleTo(page) is False

        page.thinking_combo.setCurrentIndex(page.thinking_combo.findData("enabled"))
        page.effort_combo.setCurrentIndex(page.effort_combo.findData("max"))
        page.web_check.setChecked(True)
        page.engine_combo.setCurrentIndex(page.engine_combo.findData("tavily"))
        page.web_results_spin.setValue(8)
        page.web_fetch_check.setChecked(False)

        assert host.config.get("ai_thinking") == "enabled"
        assert host.config.get("ai_reasoning_effort") == "max"
        assert host.config.get("ai_web_search") is True
        assert host.config.get("ai_web_engine") == "tavily"
        assert host.config.get("ai_web_results") == 8
        assert host.config.get("ai_web_fetch") is False
        assert page._effort_row.isVisibleTo(page) is True

        page.thinking_combo.setCurrentIndex(page.thinking_combo.findData("disabled"))
        assert page._effort_row.isVisibleTo(page) is False

        page.web_check.setChecked(False)
        assert page._engine_row.isVisibleTo(page) is False
        assert page._web_key_row.isVisibleTo(page) is False
    finally:
        page.close()


def test_settings_page_marks_in_memory_search_keys(qt_app, tmp_path: Path) -> None:
    context = _context(tmp_path)
    definition = _create_host(context)
    api = FakeApi()
    api.credentials_available = lambda: False
    host = SimpleNamespace(
        config=Config(tmp_path),
        features=SimpleNamespace(ports=SimpleNamespace(api=api)),
    )
    page = definition.settings_factory(host)
    try:
        page.web_check.setChecked(True)
        page.engine_combo.setCurrentIndex(page.engine_combo.findData("tavily"))
        page.web_key_edit.setText("sk-memory")
        page._save_web_key()
        assert "重启即丢" in page.web_key_state.text()
    finally:
        page.close()


def test_copy_action_only_reports_copied(runtime) -> None:
    runtime.context.ports.screen.region_action = "copy"
    runtime.screen_capture(2)
    presentation = runtime.context.ports.presentation
    ask = runtime.context.ports.screen_ask
    assert presentation.said[-1] == "已复制图像"
    assert ask.opened == [] and ask.open_actions == []


def test_ocr_action_opens_the_action_window(runtime) -> None:
    runtime.context.ports.screen.region_action = "ocr"
    runtime.screen_capture(2)
    ask = runtime.context.ports.screen_ask
    screen = runtime.context.ports.screen
    assert screen.ai_available is True
    assert ask.open_actions and ask.open_actions[0][1] == "ocr"
    assert ask.open_actions[0][2].png == b"region-png"
    assert ask.opened == []


def test_action_callback_builds_the_ocr_prompt(runtime) -> None:
    reply = runtime._on_action("ocr", "", b"png-bytes", 2)
    assert reply == "答案"
    messages = runtime.context.ports.api.requests[0]["messages"]
    assert messages[0]["role"] == "system"
    assert "完整提取" in messages[1]["content"][0]["text"]


def test_translate_uses_the_local_source_text(runtime) -> None:
    reply = runtime._on_action("translate", "第一行\n第二行", b"png-bytes", 2)
    assert reply == "答案"
    messages = runtime.context.ports.api.requests[0]["messages"]
    assert isinstance(messages[1]["content"], str)
    assert "第一行" in messages[1]["content"]
    assert "英文" in messages[1]["content"]  # 中日韩占多数 → 英文（同主分支口径）


def test_translate_without_local_text_uses_the_vision_prompt(runtime) -> None:
    runtime._on_action("translate", "", b"png-bytes", 2)
    messages = runtime.context.ports.api.requests[0]["messages"]
    assert isinstance(messages[1]["content"], list)
    assert "翻译" in messages[1]["content"][0]["text"]


def test_unknown_action_is_ignored(runtime) -> None:
    assert runtime._on_action("copy", "", b"png", 2) == ""
    assert runtime.context.ports.api.requests == []


def test_quick_submit_streams_to_the_quick_bubble(runtime) -> None:
    quick = runtime.context.ports.quick_chat
    quick.history_messages = [{"role": "user", "content": "在吗"}]
    runtime._on_quick_submit("在吗", 2)
    assert quick.done_event.wait(5)
    assert ("token", "答") in quick.calls and ("token", "案") in quick.calls
    assert quick.calls[-1] == ("done", "答案")


def test_quick_submit_without_service_reports_an_error(tmp_path: Path) -> None:
    context = _context(tmp_path, service=False)
    runtime = _create_host(context).runtime_factory(context)
    quick = context.ports.quick_chat
    runtime._on_quick_submit("在吗", 2)
    assert ("error", "no_service", "还没有可用的 API 服务") in quick.calls


def test_vision_off_still_allows_copy_and_local_ocr(runtime) -> None:
    runtime.context.host.config.set("ai_vision_enabled", False)
    screen = runtime.context.ports.screen
    ask = runtime.context.ports.screen_ask
    presentation = runtime.context.ports.presentation

    screen.region_action = "copy"
    runtime.screen_capture(2)
    assert presentation.said[-1] == "已复制图像"

    screen.region_action = "ocr"
    runtime.screen_capture(2)
    assert ask.open_actions[-1][1] == "ocr"

    screen.region_action = "ask"
    runtime.screen_capture(2)
    assert "视觉模型" in presentation.said[-1]


# ------------------------------------------------------------------ 主动识屏


def test_runtime_proactive_ask_sends_the_window_frame(runtime) -> None:
    done = threading.Event()
    captured: list[str] = []

    def on_done(text: str) -> None:
        captured.append(text)
        done.set()

    runtime.proactive_ask(
        ScreenCapture(
            png=b"window-png",
            width=64,
            height=48,
            app_info="code.exe | main.py",
            hwnd=7,
            dh_hash=0x1234,
        ),
        "code.exe | main.py",
        on_done=on_done,
        on_fail=lambda reason: done.set(),
    )
    assert done.wait(5), "视觉请求没有结束"
    assert captured == ["答案"]
    request = runtime.context.ports.api.requests[-1]
    payload = request["messages"]
    assert payload[0]["role"] == "system"
    assert "鲸鱼娘" in payload[0]["content"]
    assert "main.py" in str(payload[-1]["content"])


def test_runtime_proactive_answer_records_without_opening_the_window(runtime) -> None:
    chat = runtime.context.ports.chat
    presentation = runtime.context.ports.presentation
    runtime.proactive_answer("看到一份报告", app_info="code.exe | main.py", sync_chat=True)
    assert presentation.said == ["看到一份报告"]
    assert [entry[0] for entry in chat.records] == ["user", "assistant"]
    assert "main.py" in chat.records[0][1]
    assert not [call for call in chat.calls if call[0] == "open"], "主动识屏不许唤出对话窗"


def test_runtime_proactive_answer_can_skip_the_session(runtime) -> None:
    chat = runtime.context.ports.chat
    runtime.proactive_answer("只看气泡", app_info="", sync_chat=False)
    assert chat.records == []


def test_runtime_toggle_proactive_writes_the_flag(runtime) -> None:
    config = runtime.context.host.config
    assert config.get("ai_proactive_enabled") is False
    runtime.toggle_proactive()
    assert config.get("ai_proactive_enabled") is True
    assert any("白名单" in text for text in runtime.context.ports.presentation.said)
    runtime.toggle_proactive()
    assert config.get("ai_proactive_enabled") is False
    assert runtime.context.ports.presentation.said[-1] == "主动识屏已关闭"


def test_runtime_toggle_reports_count_when_whitelist_is_set(runtime) -> None:
    config = runtime.context.host.config
    config.set("ai_proactive_whitelist", ["chrome.exe", "title:*报告*"])
    runtime.toggle_proactive()
    assert runtime.context.ports.presentation.said[-1] == "主动识屏已开启（白名单 2 条）"


def test_settings_page_exposes_the_proactive_section(qt_app, tmp_path: Path) -> None:
    context = _context(tmp_path)
    definition = _create_host(context)
    host = SimpleNamespace(
        config=Config(tmp_path),
        features=SimpleNamespace(ports=SimpleNamespace(api=FakeApi(), screen=FakeScreen())),
        chat=SimpleNamespace(),
    )
    page = definition.settings_factory(host)
    try:
        page.proactive_check.setChecked(True)
        assert host.config.get("ai_proactive_enabled") is True

        page.whitelist_edit.setPlainText("chrome.exe\n  title:*报告*  \n\n")
        assert host.config.get("ai_proactive_whitelist") == ["chrome.exe", "title:*报告*"]

        page.proactive_dwell_spin.setValue(90)
        assert host.config.get("ai_proactive_dwell_seconds") == 90
        page.proactive_threshold_spin.setValue(12)
        assert host.config.get("ai_proactive_change_threshold") == 12
        page.proactive_cap_spin.setValue(8)
        assert host.config.get("ai_proactive_daily_cap") == 8
        page.proactive_cooldown_spin.setValue(600)
        assert host.config.get("ai_proactive_cooldown_seconds") == 600
        page.proactive_interval_spin.setValue(120)
        assert host.config.get("ai_proactive_min_interval_seconds") == 120

        assert not page._proactive_idle_row.isEnabled()
        page.proactive_idle_check.setChecked(True)
        assert host.config.get("ai_proactive_require_idle") is True
        assert page._proactive_idle_row.isEnabled()

        page.proactive_dry_check.setChecked(True)
        page.proactive_cue_check.setChecked(False)
        page.proactive_sync_check.setChecked(False)
        assert host.config.get("ai_proactive_dry_run") is True
        assert host.config.get("ai_proactive_pre_cue") is False
        assert host.config.get("ai_proactive_sync_chat") is False

        page._append_rule("chrome.exe")
        page._append_rule("title:*报告*")
        page._append_rule("code.exe")
        lines = [line for line in page.whitelist_edit.toPlainText().splitlines() if line]
        assert lines.count("chrome.exe") == 1
        assert "code.exe" in lines
    finally:
        page.deleteLater()


def test_menu_contribution_checkmark_reads_the_config(tmp_path: Path) -> None:
    context = _context(tmp_path)
    definition = _create_host(context)
    menu = [item for item in definition.contributions if item.kind == "menu"]
    assert [item.id for item in menu] == ["open", "look_screen", "screen_capture", "proactive"]
    contribution = next(item for item in menu if item.id == "proactive")
    assert contribution.data["command"] == "proactive_toggle"
    assert contribution.data["label"] == "主动识屏"
    checked = contribution.data["checked"]
    assert checked() is False
    context.host.config.set("ai_proactive_enabled", True)
    assert checked() is True


# ------------------------------------------------------------------ DPI 图像封顶


def test_vision_payload_is_capped_by_the_screen_port(runtime) -> None:
    """送模型的图先经 Core 按物理像素封顶；本地 OCR 仍拿原图。"""
    import base64 as _b64

    screen = runtime.context.ports.screen
    calls: list[tuple[int, int]] = []

    def shrink(png, *, max_edge=1024):
        calls.append((len(png), int(max_edge)))
        return b"vision-png"

    screen.shrink_for_vision = shrink
    runtime._on_ask("这是什么", b"raw-png-bytes", [], 0)
    assert calls and calls[0][0] == len(b"raw-png-bytes")
    request = runtime.context.ports.api.requests[-1]
    encoded = str(request["messages"][-1]["content"])
    assert _b64.b64encode(b"vision-png").decode("ascii") in encoded
    assert _b64.b64encode(b"raw-png-bytes").decode("ascii") not in encoded
    del screen.shrink_for_vision


def test_vision_bytes_falls_back_when_the_port_cannot_shrink(runtime) -> None:
    assert runtime._vision_bytes(b"raw") == b"raw"


@pytest.mark.parametrize("quick", [False, True])
def test_superseded_request_cannot_finish_or_uncancel_new_request(tmp_path, qt_app, quick):
    from PySide6.QtCore import QEventLoop, QTimer
    from petcore.ui.ports import QtScheduler

    context = _context(tmp_path)
    context.ports.scheduler = QtScheduler()
    instance = _create_host(context).runtime_factory(context)
    entered = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]
    workers = []

    def stream(*args, **kwargs):
        index = len(workers)
        workers.append(threading.current_thread())
        entered[index].set()
        assert release[index].wait(10)
        kwargs["on_delta"](f"token-{index}")
        return SimpleNamespace(text=f"answer-{index}", cancelled=False)

    context.ports.api.stream_chat = stream
    start = instance._start_quick if quick else instance._start
    cancels = instance._quick_cancels if quick else instance._cancels
    port = context.ports.quick_chat if quick else context.ports.chat

    def flush():
        loop = QEventLoop()
        delivered = []
        context.ports.scheduler.call_later(0, lambda: (delivered.append(True), loop.quit()))
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(10000)
        loop.exec()
        timer.stop()
        assert delivered

    try:
        start(0, [])
        assert entered[0].wait(10)
        start(0, [])
        assert entered[1].wait(10)
        current = cancels[0]
        release[0].set()
        workers[0].join(10)
        flush()
        assert cancels.get(0) is current
        assert not any("answer-0" in str(call) or "token-0" in str(call) for call in port.calls)
        instance.stop()
        assert current.is_set()
        release[1].set()
        workers[1].join(10)
        flush()
        assert not any("answer-1" in str(call) or "token-1" in str(call) for call in port.calls)
    finally:
        instance.stop()
        for event in release:
            event.set()
        for worker in workers:
            worker.join(10)
        flush()


def test_stopped_vision_request_is_cancelled_without_late_publish(tmp_path, qt_app):
    from PySide6.QtCore import QEventLoop, QTimer
    from petcore.ui.ports import QtScheduler

    context = _context(tmp_path)
    context.ports.scheduler = QtScheduler()
    instance = _create_host(context).runtime_factory(context)
    entered, release = threading.Event(), threading.Event()
    observed, workers, delivered = [], [], []

    def stream(*args, **kwargs):
        observed.append(kwargs.get("cancel"))
        workers.append(threading.current_thread())
        entered.set()
        assert release.wait(10)
        return SimpleNamespace(text="late")

    context.ports.api.stream_chat = stream
    try:
        instance._start_vision(0, [], on_done=delivered.append)
        assert entered.wait(10)
        instance.stop()
        assert observed[0] is not None and observed[0].is_set()
    finally:
        instance.stop()
        release.set()
        for worker in workers:
            worker.join(10)
        loop = QEventLoop()
        marker = []
        context.ports.scheduler.call_later(0, lambda: (marker.append(True), loop.quit()))
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(10000)
        loop.exec()
        timer.stop()
        assert marker
    assert delivered == []


def test_failed_scheduler_never_executes_ui_callback_on_worker(tmp_path):
    context = _context(tmp_path)
    instance = _create_host(context).runtime_factory(context)
    called = []

    def fail(*args):
        raise RuntimeError("scheduler closed")

    context.ports.scheduler.call_later = fail
    worker = threading.Thread(target=lambda: instance._later(lambda: called.append(True)))
    worker.start()
    worker.join(10)
    assert not worker.is_alive()
    assert called == []


# ------------------------------------------------------------------ 半条回答


@pytest.mark.parametrize("quick", [False, True])
def test_partial_answer_survives_a_transport_failure(tmp_path, quick) -> None:
    """已经有正文时断流：交给 done 收尾（带「已保留前面的内容」），不再弹错误卡。"""
    from petcore.net import errors as net_errors

    context = _context(tmp_path)
    instance = _create_host(context).runtime_factory(context)

    def stream(*_args, **kwargs):
        kwargs["on_delta"]("半条回答")
        raise net_errors.NetworkError(reason=net_errors.NETWORK)

    context.ports.api.stream_chat = stream
    start = instance._start_quick if quick else instance._start
    port = context.ports.quick_chat if quick else context.ports.chat
    try:
        start(0, [])
        assert port.done_event.wait(5), "失败回调没有结束"
    finally:
        instance.stop()

    assert not [call for call in port.calls if call[0] == "error"]
    done = [call for call in port.calls if call[0] == "done"]
    assert done, "半条回答必须交给 done 收尾"
    assert "半条回答" in done[-1][1]
    assert "已保留前面的内容" in done[-1][1]
    assert context.ports.presentation.said == []


@pytest.mark.parametrize("quick", [False, True])
def test_transport_failure_without_content_still_reports_an_error(tmp_path, quick) -> None:
    from petcore.net import errors as net_errors

    context = _context(tmp_path)
    instance = _create_host(context).runtime_factory(context)

    def stream(*_args, **_kwargs):
        raise net_errors.NetworkError(reason=net_errors.NETWORK)

    context.ports.api.stream_chat = stream
    start = instance._start_quick if quick else instance._start
    port = context.ports.quick_chat if quick else context.ports.chat
    try:
        start(0, [])
        assert port.done_event.wait(5), "失败回调没有结束"
    finally:
        instance.stop()

    errors_seen = [call for call in port.calls if call[0] == "error"]
    assert errors_seen and errors_seen[0][1] == net_errors.NETWORK
    # 屏幕上只能是中文：英文 reason 是给日志/分类用的，不是给人看的。
    assert errors_seen[0][2] == "网络不可达"
    assert not [call for call in port.calls if call[0] == "done"]


def test_ai_chat_failure_text_never_repeats_the_reason(tmp_path: Path) -> None:
    context = _context(tmp_path)
    instance = _create_host(context).runtime_factory(context)
    try:
        module = sys.modules[type(instance).__module__]
        failure_cls = module.AiChatFailure
        assert str(failure_cls("network", "网络不可达")) == "网络不可达"
        assert str(failure_cls("tls")) == "证书校验失败"
        assert str(failure_cls("network", "HTTP 502")) == "网络不可达：HTTP 502"
        # 「看看屏幕」的气泡文案也走同一张表（以前会显示英文 network）。
        assert module._REASON_TEXT["network"] == "网络不可达"
        assert module._REASON_TEXT["protocol"] == "服务端返回了无法识别的响应"
    finally:
        instance.stop()


# ---------------------------------------------------------------- 联网检索降级


def test_web_search_failure_never_reaches_the_mod_error_path(tmp_path: Path, monkeypatch) -> None:
    """联网检索挂了也要走 done（带「未联网核实」说明），不能弹错误卡。

    这是用户看到的那条链：mod → core 传输层 → 本机服务端。检索轮先回一个
    ``web_search`` 工具调用、搜索本身抛错，正式回答必须照发；状态行走
    ``ChatPort.status``，**不是** ``ChatPort.error``。
    """
    import json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from petcore.api.chat import stream_chat as core_stream_chat
    from petcore.contracts.api import ApiMetadata, ApiRequest
    from petcore.net import errors as net_errors
    from petcore.net import http as net_http
    from petcore.net import websearch

    planner_calls = {"count": 0}

    class _Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):  # noqa: A003 - 静音
            return

        def do_POST(self):  # noqa: N802 - HTTP 方法名
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if body.get("tools"):
                planner_calls["count"] += 1
                if planner_calls["count"] == 1:
                    payload = {
                        "choices": [
                            {
                                "message": {
                                    "content": "",
                                    "tool_calls": [
                                        {
                                            "id": "call_1",
                                            "type": "function",
                                            "function": {
                                                "name": "web_search",
                                                "arguments": json.dumps({"query": "新闻"}),
                                            },
                                        }
                                    ],
                                }
                            }
                        ]
                    }
                else:
                    payload = {"choices": [{"message": {"content": "无需搜索"}}]}
                data = json.dumps(payload, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            data = (
                b'data: {"choices":[{"delta":{"content":"\xe7\xba\xaf API \xe5\x9b\x9e\xe7\xad\x94"}}]}\n\n'
                b"data: [DONE]\n\n"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    class _Server(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            return

    httpd = _Server(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def boom(*_args, **_kwargs):
        raise websearch.SearchError("连接被重置", reason=net_errors.NETWORK)

    monkeypatch.setattr(websearch, "search", boom)

    def direct_urlopen(request, *, timeout, context=None, opener=None, routes=None, **kwargs):
        return net_http._opener(False, context).open(request, timeout=timeout, **kwargs)  # noqa: SLF001

    # 本机服务端：绕过系统代理，用例只观察 mod 的错误路径有没有被走。
    monkeypatch.setattr("petcore.api.chat.urlopen", direct_urlopen)

    context = _context(tmp_path)
    context.host.config.set("ai_web_search", True)
    request = ApiRequest(
        ApiMetadata(
            service_id="svc",
            name="服务",
            base_url=f"http://127.0.0.1:{httpd.server_address[1]}",
            model="chat-model",
        ),
        "secret-key",
    )

    def stream(
        service_id,
        messages,
        *,
        web_search=None,
        cancel=None,
        on_delta=None,
        on_reasoning=None,
        on_status=None,
        **_kwargs,
    ):
        return core_stream_chat(
            request,
            messages,
            on_delta=on_delta,
            on_reasoning=on_reasoning,
            on_status=on_status,
            web_search=web_search,
            cancel=cancel,
            timeout=5.0,
        )

    context.ports.api.stream_chat = stream
    instance = _create_host(context).runtime_factory(context)
    chat = context.ports.chat
    try:
        instance._start(0, [{"role": "user", "content": "今天有什么新闻"}])
        assert chat.done_event.wait(10), "失败回调没有结束"
    finally:
        instance.stop()
        httpd.shutdown()
        httpd.server_close()

    assert not [call for call in chat.calls if call[0] == "error"], chat.calls
    assert any(
        call[0] == "status" and "联网检索暂时不可用" in call[1] for call in chat.calls
    ), chat.calls
    done = [call for call in chat.calls if call[0] == "done"]
    assert done and "纯 API 回答" in done[-1][1]
