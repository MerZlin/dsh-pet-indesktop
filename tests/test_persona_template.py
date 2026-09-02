# -*- coding: utf-8 -*-
import json
from pet.persona_phrases import phrase_keys
from pet.persona_template import build_persona_template, template_json


def test_template_is_complete_and_safe():
    data = build_persona_template({"dialogue_mode": "custom", "dialogue_phrases": {"start": "你好，{name}"}, "api_key": "secret", "path": "C:/secret"})
    assert data["template"] == "persona-phrases/v1"
    assert set(data["phrases"]) == set(phrase_keys())
    assert data["phrases"]["start"] == ["你好，{name}"]
    assert data["phrases"]["thinking"] == []
    assert data["variables"]["command"]
    text = template_json({"dialogue_phrases": {"start": "中文\n\""}})
    assert json.loads(text)["phrases"]["start"] == ["中文\n\""]
    assert "secret" not in text and "C:/secret" not in text


def test_template_limits_and_ignores_bad_values():
    data = build_persona_template({"dialogue_phrases": {"start": [" a ", 3] * 10, "thinking": None}})
    assert data["phrases"]["start"] == ["a"] * 8
    assert data["phrases"]["thinking"] == []
