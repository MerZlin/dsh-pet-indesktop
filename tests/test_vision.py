# -*- coding: utf-8 -*-
"""看看屏幕：视觉模型推导 + 截图存档。"""

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
    assert auth == "Bearer sk-vision-secret"
    assert "sk-chat-secret" not in str(auth)
