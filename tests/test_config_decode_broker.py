# -*- coding: utf-8 -*-
"""P3 broker 配置键测试：decode_broker_enabled 灰度开关（默认关，不进设置 UI）。

护栏语义（P3_BROKER_DESIGN.md §3.8/§4）：
- 默认值 = False（灰度默认关：不开启时零行为差异）；
- reload() 白名单可读写（磁盘重载生效）；
- _normalize_pet_settings 做 bool 归一（字符串 "1"/"true"/"0" 等均归 bool）；
- 不进入 config_domains / 设置对话框（本批无 UI）——键只存在于顶层 defaults。
"""

from __future__ import annotations

from pet.config import Config

KEY = "decode_broker_enabled"


def test_default_is_false(tmp_path):
    """灰度默认关：无配置文件时键存在且为 False。"""
    cfg = Config(base=tmp_path)
    assert cfg.get(KEY) is False


def test_true_written_by_config_file_is_loaded(tmp_path):
    """磁盘写入 true 后 reload() 生效（白名单路径）。"""
    cfg = Config(base=tmp_path)
    cfg.set(KEY, True)
    cfg.save()
    reloaded = Config(base=tmp_path)
    assert reloaded.get(KEY) is True


def test_false_written_by_config_file_is_loaded(tmp_path):
    """显式 false 同样可经白名单加载（不留 True 残留）。"""
    cfg = Config(base=tmp_path)
    cfg.set(KEY, False)
    cfg.save()
    reloaded = Config(base=tmp_path)
    assert reloaded.get(KEY) is False


def test_normalize_bool_strings(tmp_path):
    """归一化：bool/字符串输入都归 bool（_normalize_pet_settings）。

    语义对齐 config._bool_or_default（与 collision_enabled 同款）：bool 直通；
    字符串 true/false/1/0/yes/no/on/off 归一；非 bool/非 str（如数字）
    回退默认 False（与既有归一器行为一致，防脏数据）。
    """
    cfg = Config(base=tmp_path)
    for raw, expected in (("1", True), ("true", True), ("on", True),
                          ("0", False), ("false", False), ("off", False),
                          ("yes", True), ("no", False),
                          (True, True), (False, False),
                          (1, False), (0, False)):
        cfg.data[KEY] = raw
        cfg._normalize_pet_settings()
        assert cfg.get(KEY) is expected, f"raw={raw!r} -> {cfg.get(KEY)!r}"
