# -*- coding: utf-8 -*-
"""功能模块激活层（pet/feature_activation.py）回归。

钉住的语义（SPEC §4）：
- 一次性迁移判定：新装 vs 升级（有任一数据痕迹即升级，继承全模块）；
- 合法空列表是明确选择，绝不回退成全激活；
- 损坏/版本不认识/元素级损坏：保留原数据 + 保守全激活，不覆盖用户数据；
- 未知 ID 留在盘上但不进快照；
- 快照未冻结（测试/工具/独立设置进程）is_active 恒 True（旧行为默认不变）；
- write_enabled 只写盘（下次启动生效），不动本次快照。
"""
from __future__ import annotations

import json

import pytest

from pet import feature_activation as fa
from pet.config import Config
from pet.features import FEATURES, MIGRATION_BASELINE


@pytest.fixture(autouse=True)
def _reset_snapshot():
    fa.reset_snapshot_for_tests()
    yield
    fa.reset_snapshot_for_tests()


def _config(tmp_path) -> Config:
    return Config(tmp_path)


def _disk_payload(config: Config):
    return json.loads(config.path.read_text(encoding="utf-8")).get("feature_activation")


def test_new_install_gets_pure_core_and_persists(tmp_path):
    """期 5：新装默认纯核心（enabled=[]）；升级才继承全集。"""
    cfg = _config(tmp_path)
    frozen = fa.decide_and_freeze(cfg)
    assert frozen == frozenset()  # PURE_CORE_FOR_NEW_INSTALL=True（期 5 落地）
    # 判定只进内存草稿（启动链不许有配置副作用），随下一次自然保存落盘
    assert not cfg.path.is_file()
    assert cfg.data["feature_activation"] == {"version": 1, "enabled": []}
    assert cfg.save()
    assert _disk_payload(cfg) == {"version": 1, "enabled": []}
    # 幂等：二次判定不重判（合法空列表绝不回退成全激活）
    fa.reset_snapshot_for_tests()
    frozen2 = fa.decide_and_freeze(cfg)
    assert frozen2 == frozenset()


def test_upgrade_with_existing_config_gets_baseline(tmp_path):
    cfg = _config(tmp_path)
    cfg.dir.mkdir(parents=True, exist_ok=True)
    cfg.path.write_text("{}", encoding="utf-8")  # 有 config.json 但无激活键 = 老用户
    cfg.reload()
    frozen = fa.decide_and_freeze(cfg)
    assert frozen == frozenset(MIGRATION_BASELINE)


def test_upgrade_detected_by_slot_profile_without_main_config(tmp_path):
    cfg = _config(tmp_path)
    assert not cfg.path.is_file()
    cfg.dir.mkdir(parents=True, exist_ok=True)
    (cfg.dir / "config-slot-1.json").write_text("{}", encoding="utf-8")
    frozen = fa.decide_and_freeze(cfg)
    assert frozen == frozenset(MIGRATION_BASELINE)


def test_decided_subset_is_frozen_verbatim(tmp_path):
    cfg = _config(tmp_path)
    cfg.set("feature_activation", {"version": 1, "enabled": ["weather"]})
    frozen = fa.decide_and_freeze(cfg)
    assert frozen == frozenset({"weather"})
    assert fa.is_active("weather") is True
    assert fa.is_active("todo") is False


def test_legal_empty_list_never_falls_back_to_all(tmp_path):
    cfg = _config(tmp_path)
    cfg.set("feature_activation", {"version": 1, "enabled": []})
    frozen = fa.decide_and_freeze(cfg)
    assert frozen == frozenset()
    assert all(fa.is_active(fid) is False for fid in FEATURES)


def test_unknown_ids_stay_on_disk_but_not_in_snapshot(tmp_path):
    cfg = _config(tmp_path)
    cfg.set("feature_activation", {"version": 1, "enabled": ["weather", "future-dlc"]})
    cfg.save()
    frozen = fa.decide_and_freeze(cfg)
    assert frozen == frozenset({"weather"})
    # 未知 ID 保留在盘上（降级/再升级恢复用）
    assert "future-dlc" in _disk_payload(cfg)["enabled"]


@pytest.mark.parametrize("bad", ["garbage", 42, ["weather"], {"version": 99, "enabled": []},
                                 {"version": 1, "enabled": "weather"}])
def test_corrupt_value_preserves_disk_and_freezes_baseline(tmp_path, bad):
    cfg = _config(tmp_path)
    cfg.set("feature_activation", bad)
    cfg.save()
    frozen = fa.decide_and_freeze(cfg)
    assert frozen == frozenset(MIGRATION_BASELINE)
    assert _disk_payload(cfg) == bad  # 原数据未被覆盖


def test_element_level_corruption_preserves_disk_and_freezes_baseline(tmp_path):
    cfg = _config(tmp_path)
    cfg.set("feature_activation", {"version": 1, "enabled": ["weather", 3]})
    cfg.save()
    frozen = fa.decide_and_freeze(cfg)
    assert frozen == frozenset(MIGRATION_BASELINE)
    assert _disk_payload(cfg)["enabled"] == ["weather", 3]


def test_unfrozen_snapshot_defaults_to_active():
    assert fa.snapshot() is None
    assert all(fa.is_active(fid) is True for fid in FEATURES)


def test_write_enabled_roundtrip_and_rejects_unknown(tmp_path):
    cfg = _config(tmp_path)
    assert fa.write_enabled(cfg, ["todo", "weather", "todo"]) is True
    assert _disk_payload(cfg) == {"version": 1, "enabled": ["todo", "weather"]}
    # 写盘不动本次快照（重启才生效）
    assert fa.snapshot() is None
    assert fa.write_enabled(cfg, ["not-a-feature"]) is False
    assert _disk_payload(cfg)["enabled"] == ["todo", "weather"]


def test_write_enabled_allows_legal_empty_list(tmp_path):
    cfg = _config(tmp_path)
    assert fa.write_enabled(cfg, []) is True
    assert _disk_payload(cfg) == {"version": 1, "enabled": []}


def test_pending_enabled_previews_disk_or_baseline(tmp_path):
    cfg = _config(tmp_path)
    assert fa.pending_enabled(cfg) == list(MIGRATION_BASELINE)  # 未判定 → 基线预览
    cfg.set("feature_activation", {"version": 1, "enabled": ["todo"]})
    assert fa.pending_enabled(cfg) == ["todo"]
    cfg.set("feature_activation", {"version": 1, "enabled": ["todo", 3]})
    assert fa.pending_enabled(cfg) == list(MIGRATION_BASELINE)  # 损坏 → 基线预览


def test_reload_picks_up_disk_value_and_null_means_undecided(tmp_path):
    cfg = _config(tmp_path)
    cfg.set("feature_activation", {"version": 1, "enabled": ["weather"]})
    cfg.save()
    cfg2 = _config(tmp_path)
    assert cfg2.data["feature_activation"] == {"version": 1, "enabled": ["weather"]}
    # 显式 null（NULL 接受键）= 撤成未判定
    raw = json.loads(cfg2.path.read_text(encoding="utf-8"))
    raw["feature_activation"] = None
    cfg2.path.write_text(json.dumps(raw), encoding="utf-8")
    cfg2.reload()
    assert cfg2.data["feature_activation"] is None


def test_initial_data_has_undecided_default(tmp_path):
    assert _config(tmp_path).data["feature_activation"] is None


def test_settings_process_prefers_main_process_snapshot_env(tmp_path, monkeypatch):
    """独立设置进程：盘上未判定时吃主进程透传的快照环境变量（盲审存疑 B）——
    空串 = 纯核心（与未设置可区分）；有值按其冻结；未设置才走自己的判定。"""
    cfg = _config(tmp_path)
    cfg.dir.mkdir(parents=True, exist_ok=True)
    # ① 主进程判了纯核心（空串）：设置进程同样纯核心
    monkeypatch.setenv(fa.ENV_FEATURE_SNAPSHOT, "")
    fa.freeze_for_settings_process(cfg)
    assert fa.snapshot() == frozenset()
    # ② 主进程快照子集：按子集冻结
    fa.reset_snapshot_for_tests()
    monkeypatch.setenv(fa.ENV_FEATURE_SNAPSHOT, "weather,todo")
    fa.freeze_for_settings_process(cfg)
    assert fa.snapshot() == frozenset({"weather", "todo"})
    # ③ 无透传 + 全新空目录：自己的判定 = 新装纯核心
    fa.reset_snapshot_for_tests()
    monkeypatch.delenv(fa.ENV_FEATURE_SNAPSHOT, raising=False)
    fa.freeze_for_settings_process(cfg)
    assert fa.snapshot() == frozenset()
    # ④ 无透传 + 老用户痕迹（slot 档案）：自己的判定 = 迁移基线
    fa.reset_snapshot_for_tests()
    (cfg.dir / "config-slot-1.json").write_text("{}", encoding="utf-8")
    fa.freeze_for_settings_process(cfg)
    assert fa.snapshot() == frozenset(MIGRATION_BASELINE)


def test_snapshot_env_value_roundtrip():
    """主进程侧透传串：未冻结 None；冻结后序列化可被设置进程解析。"""
    assert fa.snapshot_env_value() is None
    fa.freeze_snapshot_for_tests([])
    assert fa.snapshot_env_value() == ""
    fa.freeze_snapshot_for_tests(["weather", "todo"])
    assert fa.snapshot_env_value() == "todo,weather"


def test_pending_preview_follows_effective_decision_not_baseline(tmp_path):
    """功能页预览与有效判定同源：新装（冻结纯核心）预览必须是空——否则
    功能页显示全模块勾选，一次保存就把全激活写进盘（复审补漏）。"""
    cfg = _config(tmp_path)
    cfg.dir.mkdir(parents=True, exist_ok=True)
    # 新装：主进程判定并冻结纯核心 → 预览纯核心（修复前：预览=迁移基线全开）
    fa.decide_and_freeze(cfg)
    assert fa.pending_enabled(cfg) == []
    # 升级：冻结迁移基线 → 预览全模块
    fa.reset_snapshot_for_tests()
    cfg2 = _config(tmp_path / "upg")
    cfg2.dir.mkdir(parents=True, exist_ok=True)
    cfg2.path.write_text("{}", encoding="utf-8")
    cfg2.reload()
    fa.decide_and_freeze(cfg2)
    assert fa.pending_enabled(cfg2) == list(MIGRATION_BASELINE)
    # 未冻结（工具/测试上下文）：沿用迁移基线预览（旧行为不变）
    fa.reset_snapshot_for_tests()
    assert fa.pending_enabled(_config(tmp_path / "raw")) == list(MIGRATION_BASELINE)
