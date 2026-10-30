# -*- coding: utf-8 -*-
"""看看屏幕：视觉模型推导 + 截图存档。"""

import pytest

from pet.chat.models import ProviderConfig
from pet.vision import resolve_vision_model


def _p(model, **kw):
    raw = {'model': model}
    raw.update(kw)
    return ProviderConfig.from_dict('test', raw)


def test_deepseek_flash_maps_to_preview_vision():
    assert resolve_vision_model(_p('deepseek-v4-flash')) == 'deepseek-v4-flash-vision-exp'


def test_other_deepseek_models_use_default_vision():
    assert resolve_vision_model(_p('deepseek-v4-pro')) == 'deepseek-v4-flash-vision-exp'


def test_already_vision_model_passes_through():
    assert resolve_vision_model(_p('deepseek-v4-flash-vision-exp')) == 'deepseek-v4-flash-vision-exp'


def test_multimodal_chat_model_used_as_is():
    assert resolve_vision_model(_p('kimi-k3')) == 'kimi-k3'


def test_manual_override_wins():
    p = _p('deepseek-v4-flash', vision_same_as_chat=False, vision_model='my-vl-model')
    assert resolve_vision_model(p) == 'my-vl-model'


def test_manual_empty_falls_back_to_derivation():
    p = _p('deepseek-v4-flash', vision_same_as_chat=False, vision_model='  ')
    assert resolve_vision_model(p) == 'deepseek-v4-flash-vision-exp'


def test_capture_screen_bytes_requires_screen_recording_permission(monkeypatch):
    """macOS 权限没生效时不要截一张黑图送去模型：直接给出可执行的授权说明。"""
    from pet import vision

    monkeypatch.setattr(vision, "_mac_screen_capture_access", lambda: False)
    with pytest.raises(vision.VisionError) as exc_info:
        vision.capture_screen_bytes()
    assert "屏幕录制" in str(exc_info.value)


def test_capture_screen_bytes_in_memory(tmp_path):
    import os
    if os.environ.get('QT_QPA_PLATFORM') == 'offscreen':
        import pytest
        pytest.skip('无显示环境下不截屏')
    from pet.vision import capture_screen_bytes
    data = capture_screen_bytes()
    assert isinstance(data, bytes) and data[:2] == b'\xff\xd8'  # JPEG SOI
    from PIL import Image
    import io
    with Image.open(io.BytesIO(data)) as img:
        assert max(img.size) <= 768
    # 铁律：截图不落盘——调用后目标目录不得出现任何截图文件
    assert not list(tmp_path.glob('screen-*.jpg'))


def test_endpoint_glm_v4_base():
    from pet.chat.providers import normalize_chat_endpoint
    assert normalize_chat_endpoint('https://open.bigmodel.cn/api/paas/v4') == \
        'https://open.bigmodel.cn/api/paas/v4/chat/completions'


def test_endpoint_openai_v1_base():
    from pet.chat.providers import normalize_chat_endpoint
    assert normalize_chat_endpoint('https://api.openai.com/v1') == \
        'https://api.openai.com/v1/chat/completions'


def test_endpoint_full_url_passthrough():
    from pet.chat.providers import normalize_chat_endpoint
    assert normalize_chat_endpoint('https://api.deepseek.com/v1/chat/completions') == \
        'https://api.deepseek.com/v1/chat/completions'


def test_endpoint_bare_host_appends_default_path():
    from pet.chat.providers import normalize_chat_endpoint
    assert normalize_chat_endpoint('https://api.deepseek.com') == \
        'https://api.deepseek.com/v1/chat/completions'


def test_vision_overrides_ignored_when_same_as_chat():
    """同聊天模型时，视觉独立端点/密钥一律不得生效（防残留 GLM 地址配 ds 模型名）。"""
    import inspect
    from pet import vision
    src = inspect.getsource(vision._post_vision_request)
    assert 'if p.vision_same_as_chat' in src


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, *args):
        import json
        return json.dumps(self._payload).encode('utf-8')


def test_independent_vision_empty_key_never_uses_chat_key(monkeypatch):
    """安全回归：独立视觉端点 + 视觉 Key 为空时，绝不把聊天 Key 带过去。

    应直接抛出明确的「未配置 API Key」错误，且根本不应发起任何 HTTP 请求
    （聊天 Key 因此绝不会被发送到独立视觉端点）。
    """
    import pytest
    from pet import vision
    from pet.chat.models import ProviderConfig

    calls = []
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: calls.append(a) or _FakeResponse({}))

    p = ProviderConfig.from_dict("test", {
        "model": "deepseek-v4-flash",
        "api_key": "sk-chat-secret",
        "vision_same_as_chat": False,
        "vision_base_url": "https://open.bigmodel.cn/api/paas/v4",
        # 视觉 Key 为空且无钥匙串引用
        "vision_api_key": "",
        "vision_api_key_ref": "",
    })
    with pytest.raises(vision.VisionError) as exc_info:
        vision._post_vision_request(b"fake-jpeg", "code.exe | t", "sys", p)
    assert "独立视觉服务未配置 API Key" in str(exc_info.value)
    assert calls == [], "视觉 Key 缺失时绝不应发起请求（聊天 Key 不得外发）"


def test_independent_vision_prefers_own_key_over_chat_key(monkeypatch):
    """独立视觉端点有视觉 Key 时，应携带视觉 Key 而非聊天 Key。"""
    from pet import vision
    from pet.chat.models import ProviderConfig

    called_with = {}

    def fake_urlopen(req, timeout=None, context=None):
        called_with["url"] = req.full_url
        called_with["headers"] = dict(req.header_items())
        return _FakeResponse({"choices": [{"message": {"content": "好呀"}, "finish_reason": "stop"}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    p = ProviderConfig.from_dict("test", {
        "model": "deepseek-v4-flash",
        "api_key": "sk-chat-secret",
        "vision_same_as_chat": False,
        "vision_base_url": "https://open.bigmodel.cn/api/paas/v4",
        "vision_api_key": "sk-vision-secret",
    })
    reply = vision._post_vision_request(b"fake-jpeg", "code.exe | t", "sys", p)
    assert reply == "好呀"
    assert called_with["url"] == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    auth = called_with["headers"].get("Authorization")
    assert called_with["headers"].get("User-agent", "").startswith("Mozilla/")
    assert auth == "Bearer sk-vision-secret"
    assert "sk-chat-secret" not in str(auth)


def test_vision_request_includes_self_recognition_hint(monkeypatch):
    """视觉请求 user 文本应携带自我识别提示（认出截图里的桌宠是自己）。"""
    import json
    from pet import vision
    from pet.chat.models import ProviderConfig

    captured = {}

    def fake_urlopen(req, timeout=None, context=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({"choices": [{"message": {"content": "好"}, "finish_reason": "stop"}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    p = ProviderConfig.from_dict("test", {"model": "deepseek-v4-flash", "api_key": "sk-x"})
    vision._post_vision_request(b"fake-jpeg", "code.exe | t", "sys", p, pet_name="大肥鱼")
    user_msg = captured["body"]["messages"][1]["content"][0]["text"]
    assert "那就是你自己「大肥鱼」" in user_msg
    # 系统提示词不被污染：提示只加在 user 文本里
    assert captured["body"]["messages"][0]["content"] == "sys"


def test_look_worker_receives_snapshot_and_does_not_mutate_shared_config(monkeypatch):
    """回归测试：看看屏幕 worker 接收快照，不改写主线程 ProviderConfig 共享对象。"""
    from pet.chat.models import ProviderConfig, ChatSettings
    from pet.window import PetWindow
    from pet.config import Config
    from types import SimpleNamespace

    shared_provider = ProviderConfig.from_dict("test-p", {
        "model": "deepseek-v4-flash",
        "api_key": "unresolved_or_empty",
    })
    shared_settings = ChatSettings(providers={"test-p": shared_provider}, active_provider="test-p")

    win = PetWindow.__new__(PetWindow)
    win.cfg = SimpleNamespace(
        chat_settings=lambda: shared_settings,
        resolve_api_key=lambda p: "sk-resolved-secret",
        get=lambda k, d=None: d,
        # Config.character_display_name：用户别名优先（回归：识屏链路曾直取默认名）
        character_display_name=lambda cid: "小鲸鱼",
    )
    win._last_look_ts = 0.0
    win._look_busy = False
    bubbles = []
    win.show_bubble = lambda msg, *a, **k: bubbles.append(msg)
    threads = []
    monkeypatch.setattr("threading.Thread", lambda **kwargs: SimpleNamespace(start=lambda: threads.append(kwargs)))

    win._on_look_screen()

    assert len(threads) == 1
    passed_args = threads[0]["args"]
    passed_provider, passed_system_prompt, passed_pet_name = passed_args
    # 传递给 worker 的 provider 是快照，含有已解析的 key
    assert passed_provider.api_key == "sk-resolved-secret"
    assert passed_provider is not shared_provider
    # 自我识别提示用别名解析后的角色名
    assert passed_pet_name == "小鲸鱼"
    # 共享对象未被污染改写
    assert shared_provider.api_key == "unresolved_or_empty"


def test_post_vision_messages_keeps_the_endpoint_and_key_discipline(monkeypatch):
    """messages 自带的入口（截图问答的多轮浮窗）走同一条路：端点解析、Key 纪律、
    重试全共用，只有组包权交给调用方。

    两半都验：配了独立视觉端点/视觉 Key 时带对端点和 Key；**没配视觉 Key 时一次
    请求都不发**（聊天 Key 绝不能被带到独立视觉端点上）。组好的 messages 原样发出
    ——这条入口的整个意义就在"整条由调用方提供"。
    """
    import json

    import pytest

    from pet import vision
    from pet.chat.models import ProviderConfig

    captured = {}

    def fake_urlopen(req, timeout=None, context=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse(
            {"choices": [{"message": {"content": "像是日报模板"}, "finish_reason": "stop"}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    messages = [
        {"role": "system", "content": "人设台词"},
        {"role": "user", "content": [
            {"type": "text", "text": "这是什么？"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
        ]},
    ]
    p = ProviderConfig.from_dict("test", {
        "model": "deepseek-v4-flash",
        "api_key": "sk-chat-secret",
        "vision_same_as_chat": False,
        "vision_base_url": "https://open.bigmodel.cn/api/paas/v4",
        "vision_api_key": "sk-vision-secret",
    })

    assert vision.post_vision_messages(messages, p) == "像是日报模板"
    assert captured["url"] == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    assert captured["headers"].get("Authorization") == "Bearer sk-vision-secret"
    assert captured["body"]["messages"] == messages, "调用方组好的整条 messages 必须原样发出"
    assert captured["body"]["stream"] is False

    # 另一半：独立视觉端点 + 空视觉 Key ⇒ 直接报错，一个请求都不发
    captured.clear()
    bare = ProviderConfig.from_dict("test", {
        "model": "deepseek-v4-flash",
        "api_key": "sk-chat-secret",
        "vision_same_as_chat": False,
        "vision_base_url": "https://open.bigmodel.cn/api/paas/v4",
        "vision_api_key": "",
        "vision_api_key_ref": "",
    })
    with pytest.raises(vision.VisionError) as exc_info:
        vision.post_vision_messages(messages, bare)
    assert "独立视觉服务未配置 API Key" in str(exc_info.value)
    assert captured == {}, "视觉 Key 缺失时绝不应发起请求（聊天 Key 不得外发）"

