# -*- coding: utf-8 -*-
"""灵动岛指标列表 ``island_metrics`` 的配置契约（P1 Slice C）。

覆盖：默认值 / 四处登记（默认值、reload 白名单、整表归一化、set() 归一化名单）、
归一化口径（非 list、非 str、重复、未知 id、无上限）、旧键 ``island_network_enabled``
的**单向迁移**、归一化幂等。

全部走 ``Config`` 这个公开 seam（脏值落盘 → 真实加载路径 reload + 归一化）；只有
``_clean_island_metrics`` 的"返回新列表"契约直接测函数本身（迁移会就地追加，别名
共享会改到调用方的对象）。
"""
from __future__ import annotations

import inspect
import json
import re

import pytest

from pet import config as config_mod
from pet.config import Config
from pet.island.segments import CPU, GPU, MEM, NETWORK


def _dirty_config(tmp_path, raw: dict) -> Config:
    """把键写成脏值落盘，再走真实加载路径（reload + _normalize_pet_settings）。"""
    cfg_dir = tmp_path / config_mod.APP_DIR_NAME
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(
        json.dumps({"version": 4, **raw}, ensure_ascii=False), encoding="utf-8")
    return Config(base=tmp_path)


# ---------------------------------------------------------------- 默认值 / 登记
def test_default_is_an_empty_list(tmp_path):
    """默认空列表 = 只要主信息、不加附加指标（升级用户不应突然多出指标）。"""
    cfg = Config(base=tmp_path)
    assert cfg.data["island_metrics"] == []
    assert cfg.get("island_metrics") == []


def test_key_is_registered_in_reload_whitelist():
    """reload 白名单是 reload() 内的字面量：漏登记会让键在重启后被默认值冲掉。"""
    src = inspect.getsource(config_mod.Config.reload)
    match = re.search(r"for key in \((.*?)\):\n", src, re.S)
    assert match, "从 reload() 源码找不到白名单元组，请检查 config.py 的白名单格式"
    assert "island_metrics" in re.findall(r'"([a-zA-Z0-9_]+)"', match.group(1))


def test_key_is_registered_in_set_normalization_whitelist():
    """set() 的归一化名单：漏登记 → 设置页写回的脏值绕过归一化直接落盘。"""
    src = inspect.getsource(config_mod.Config.set)
    match = re.search(r"if key in \{(.*?)\}:", src, re.S)
    assert match, "从 set() 源码找不到归一化键集合，请检查格式"
    assert "island_metrics" in re.findall(r'"([a-zA-Z0-9_]+)"', match.group(1))


def test_key_survives_reload_round_trip(tmp_path):
    """落盘 → 重启加载：普通白名单键，reload 后不得被默认值冲掉。"""
    cfg = Config(base=tmp_path)
    cfg.set("island_metrics", [CPU, MEM])
    assert cfg.save() is True
    restored = Config(base=tmp_path)
    assert restored.data["island_metrics"] == [CPU, MEM]


# ---------------------------------------------------------------- 归一化
@pytest.mark.parametrize("raw,expected", [
    ("network", []),
    (None, []),
    ({"network": True}, []),
    ([], []),
    # 非 str 项丢弃（True 是 int 但不是 str，字符串化才是 bug）
    ([1, None, True, CPU], [CPU]),
    # 重复去重、保首次序
    ([CPU, CPU, MEM, CPU], [CPU, MEM]),
    ([MEM, CPU, MEM], [MEM, CPU]),
    # 未知 id 丢弃（配置里残留了没实现的段：不抛、不阻止启动）
    # 注意 "gpu" 自 P3 Slice F1 起是**已知 id**（能力占位），不再是未知样本
    (["vram", "temp", CPU, ""], [CPU]),
    # 三员合法全保留，顺序原样（列表序 = 用户优先级）
    ([NETWORK, CPU, MEM], [NETWORK, CPU, MEM]),
    ([MEM, NETWORK], [MEM, NETWORK]),
    # GPU 占位（P3 Slice F1）：已知 id 所以保留——"不可用"是岛与卡片层的事，
    # 不是配置层的脏值（见 tests/test_island_gpu_placeholder.py）
    ([GPU, CPU], [GPU, CPU]),
])
def test_dirty_values_are_normalized(tmp_path, raw, expected):
    cfg = _dirty_config(tmp_path, {"island_metrics": raw})
    assert cfg.data["island_metrics"] == expected


def test_all_four_builtin_ids_are_kept_in_order(tmp_path):
    """段数**不设上限**（产品决策）：四个内置 id 一个都不许被裁掉。

    合法 id 目前只有四员（含 P3 的 gpu 占位），构造不出"超额"样本——所以这条断言
    的是"全保留且保序"，超限截断的护栏有意不存在（布局层收缩流水线负责兜宽度）。
    """
    cfg = _dirty_config(tmp_path, {"island_metrics": [MEM, GPU, CPU, NETWORK]})
    assert cfg.data["island_metrics"] == [MEM, GPU, CPU, NETWORK]
    assert len(cfg.data["island_metrics"]) == 4


def test_set_path_normalizes_too(tmp_path):
    """set() 路径（设置页写回）同样归一：脏值不得落盘。"""
    cfg = Config(base=tmp_path)
    cfg.set("island_metrics", ["vram", CPU, CPU, 7])
    assert cfg.data["island_metrics"] == [CPU]
    cfg.set("island_metrics", "cpu")
    assert cfg.data["island_metrics"] == []


def test_clean_returns_a_fresh_list():
    """``_clean_island_metrics`` 必须返回新列表：迁移会就地 append，别名共享会改到调用方对象。"""
    raw = [NETWORK]
    cleaned = config_mod._clean_island_metrics(raw)
    assert cleaned == [NETWORK]
    assert cleaned is not raw


def test_normalization_is_idempotent(tmp_path):
    """连续两次归一结果相同（保存任何设置都会跑整表归一化）。"""
    cfg = _dirty_config(tmp_path, {
        "island_network_enabled": True,
        "island_metrics": ["vram", MEM, MEM, 7, NETWORK],
    })
    first = list(cfg.data["island_metrics"])
    for _ in range(3):
        cfg._normalize_pet_settings()
        assert cfg.data["island_metrics"] == first
    assert first == [MEM, NETWORK]


# ---------------------------------------------------------------- 旧键单向迁移
def test_legacy_true_key_migrates_into_the_list(tmp_path):
    """旧键 True（老用户开着网络槽）→ 列表补 network，旧键置回 False。"""
    cfg = _dirty_config(tmp_path, {"island_network_enabled": True})
    assert cfg.data["island_metrics"] == [NETWORK]
    assert cfg.data["island_network_enabled"] is False, "迁移后旧键不再是第二写入来源"


@pytest.mark.parametrize("legacy", ["true", "on", True])
def test_legacy_string_booleans_also_migrate(tmp_path, legacy):
    """字符串布尔（手改配置/旧版导出）同规：判真后迁移，不得被丢下。"""
    cfg = _dirty_config(tmp_path / f"legacy-{legacy}", {"island_network_enabled": legacy})
    assert cfg.data["island_metrics"] == [NETWORK]
    assert cfg.data["island_network_enabled"] is False


def test_legacy_true_does_not_duplicate_an_existing_network_member(tmp_path):
    """旧键 True 且列表已含 network：原样保留，不重复追加。"""
    cfg = _dirty_config(tmp_path, {
        "island_network_enabled": True, "island_metrics": [NETWORK, MEM],
    })
    assert cfg.data["island_metrics"] == [NETWORK, MEM]
    assert cfg.data["island_network_enabled"] is False


def test_legacy_true_appends_network_after_existing_members(tmp_path):
    """追加在尾部：列表序是用户优先级，迁移不得插队。"""
    cfg = _dirty_config(tmp_path, {
        "island_network_enabled": True, "island_metrics": [MEM, CPU],
    })
    assert cfg.data["island_metrics"] == [MEM, CPU, NETWORK]


def test_legacy_false_leaves_the_list_untouched(tmp_path):
    """旧键 False（新用户 / 已迁移用户）→ 列表原样，零影响。"""
    cfg = _dirty_config(tmp_path, {
        "island_network_enabled": False, "island_metrics": [CPU],
    })
    assert cfg.data["island_metrics"] == [CPU]
    assert cfg.data["island_network_enabled"] is False
    cfg_default = Config(base=tmp_path / "fresh")
    assert cfg_default.data["island_metrics"] == []


def test_set_legacy_true_migrates_immediately(tmp_path):
    """set() 写旧键同样触发迁移（设置页之外的写入路径不得漏）。"""
    cfg = Config(base=tmp_path)
    cfg.set("island_network_enabled", True)
    assert cfg.data["island_metrics"] == [NETWORK]
    assert cfg.data["island_network_enabled"] is False
    # 迁移后再写旧键 False 不回滚列表（列表是唯一真值）
    cfg.set("island_network_enabled", False)
    assert cfg.data["island_metrics"] == [NETWORK]
