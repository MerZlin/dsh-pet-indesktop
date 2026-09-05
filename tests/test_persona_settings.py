# -*- coding: utf-8 -*-
from pet.config import Config


def test_dialogue_modes_and_custom_phrases_persist(tmp_path):
    cfg = Config(base=tmp_path)
    assert cfg.get("dialogue_mode") == "legacy"
    cfg.set("dialogue_mode", "whale_maid")
    cfg.set("dialogue_phrases", {"start": "你好", "thinking": ["想想"]})
    cfg.save()
    loaded = Config(base=tmp_path)
    assert loaded.get("dialogue_mode") == "whale_maid"
    assert loaded.get("dialogue_phrases")["start"] == "你好"
    assert loaded.get("dialogue_phrases")["thinking"] == ["想想"]


def test_bad_mode_and_phrase_types_are_repaired(tmp_path):
    cfg = Config(base=tmp_path)
    cfg.set("dialogue_mode", "bad")
    cfg.set("dialogue_phrases", {"start": 42, "thinking": [], "unknown": ["ok"]})
    cfg._normalize_pet_settings()
    assert cfg.get("dialogue_mode") == "legacy"
    assert "start" not in cfg.get("dialogue_phrases")
    assert cfg.get("dialogue_phrases")["unknown"] == ["ok"]
