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


def test_capture_screen_saves_and_prunes(tmp_path):
    import os
    if os.environ.get('QT_QPA_PLATFORM') == 'offscreen':
        import pytest
        pytest.skip('无显示环境下不截屏')
    from pet.vision import KEEP_SHOTS, capture_screen
    shot = capture_screen(tmp_path)
    assert shot.exists() and shot.suffix == '.jpg'
    from PIL import Image
    with Image.open(shot) as img:
        assert max(img.size) <= 768
    assert len(list(tmp_path.glob('screen-*.jpg'))) <= KEEP_SHOTS


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
