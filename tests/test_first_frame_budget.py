# -*- coding: utf-8 -*-
"""首帧缓存预算 LRU 的回归测试（内存瘦身批）。

不依赖 Qt：用 duck-type 假 clip（_first_image 只需 width/height，
_first_frame_lock 只需上下文管理器）直接驱动注册表函数。
"""
from __future__ import annotations

import threading

import pytest

from pet import webm_clip


class _FakeImg:
    def __init__(self, w, h):
        self._w, self._h = w, h

    def width(self):
        return self._w

    def height(self):
        return self._h


class _FakeClip:
    """duck-type WebMClip：注册表只碰这三个成员。"""

    def __init__(self, img_bytes: int):
        self._first_frame_lock = threading.Lock()
        # 100 字节 = 5×5×4
        side = 5
        self._first_image = _FakeImg(side, img_bytes // (side * 4)) if img_bytes else None


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    monkeypatch.setattr(webm_clip, "_first_frame_reg", [])
    monkeypatch.setattr(webm_clip, "_first_frame_bytes", 0)
    monkeypatch.setattr(webm_clip, "_first_frame_budget_bytes", 250)
    yield


def _store(clip, nbytes):
    victims = webm_clip._ffr_touch(clip, nbytes)
    webm_clip._ffr_evict(victims)
    return victims


def test_budget_evicts_oldest():
    a, b, c = _FakeClip(100), _FakeClip(100), _FakeClip(100)
    _store(a, 100)
    _store(b, 100)
    victims = _store(c, 100)  # 300 > 250 → 逐出最久未用的 a
    assert [v for v, _t in victims] == [a]
    assert a._first_image is None
    assert b._first_image is not None and c._first_image is not None
    assert webm_clip._first_frame_bytes == 200


def test_touch_reorders_lru():
    a, b, c = _FakeClip(100), _FakeClip(100), _FakeClip(100)
    _store(a, 100)
    _store(b, 100)
    webm_clip._ffr_touch(a)  # a 变最近使用
    victims = _store(c, 100)
    assert [v for v, _t in victims] == [b]  # 逐出 b 而非 a
    assert a._first_image is not None and b._first_image is None


def test_retouch_no_double_accounting():
    a = _FakeClip(100)
    _store(a, 100)
    webm_clip._ffr_touch(a)  # 纯置顶
    webm_clip._ffr_touch(a)
    assert webm_clip._first_frame_bytes == 100
    assert len(webm_clip._first_frame_reg) == 1


def test_unregister_clears_accounting():
    a, b = _FakeClip(100), _FakeClip(100)
    _store(a, 100)
    _store(b, 100)
    webm_clip._ffr_unregister(a)
    assert webm_clip._first_frame_bytes == 100
    assert len(webm_clip._first_frame_reg) == 1


def test_dead_refs_cleaned_lazily():
    import gc

    a = _FakeClip(100)
    _store(a, 100)
    ref_alive = webm_clip._first_frame_reg[0][0]
    assert ref_alive() is a
    del a
    gc.collect()
    b = _FakeClip(100)
    _store(b, 100)  # touch 时顺带清死引用及其账目
    assert webm_clip._first_frame_bytes == 100
    assert len(webm_clip._first_frame_reg) == 1


def test_eviction_of_emptied_clip_clears_its_accounting():
    """cleanup 清过缓存但未摘表的 clip：预算压顶时连账目一起清，不遗留。"""
    a, b, c = _FakeClip(100), _FakeClip(100), _FakeClip(100)
    _store(a, 100)
    _store(b, 100)
    a._first_image = None  # 模拟 cleanup 清了缓存（未走 unregister 的边界）
    _store(c, 100)  # 账面 300 > 250 → a 出列（不视为逐出）
    assert webm_clip._first_frame_bytes == 200
    assert len(webm_clip._first_frame_reg) == 2


def test_stale_eviction_token_skips_reregistered_clip():
    """R3 复审 P1 回归：摘表与清空之间被重新登记的热门 clip，
    迟到的逐出决定必须被 token 拦截（不再误清）。"""
    a, b, c = _FakeClip(100), _FakeClip(100), _FakeClip(100)
    _store(a, 100)
    _store(b, 100)
    victims = webm_clip._ffr_touch(c, 100)  # 300 > 250 → 决定逐出 a
    assert victims and victims[0][0] is a
    # 逐出执行前，a 被重新使用（jumpToFrame 命中路径的 touch）——
    # 这次 touch 自身超预算，顺便会决定逐出 b（真实流程，照常执行）
    pending = webm_clip._ffr_touch(a)
    webm_clip._ffr_evict(pending)
    webm_clip._ffr_evict(victims)  # 针对 a 的迟到逐出：应被 token 拦截
    assert a._first_image is not None
    assert b._first_image is None  # b 被新一轮正常逐出
    assert webm_clip._first_frame_bytes == 200


def test_fresh_eviction_token_still_evicts():
    """token 不误伤正常逐出：未被重新登记的 victim 照常清空。"""
    a, b, c = _FakeClip(100), _FakeClip(100), _FakeClip(100)
    _store(a, 100)
    _store(b, 100)
    victims = webm_clip._ffr_touch(c, 100)
    webm_clip._ffr_evict(victims)
    assert a._first_image is None
    assert webm_clip._first_frame_bytes == 200


def test_pinned_clip_survives_lru_eviction():
    """常驻（高频交互链）clip 不被逐出：LRU 跳过它逐出下一个非驻留项。"""
    a, b, c = _FakeClip(100), _FakeClip(100), _FakeClip(100)
    a._ffr_pinned = True
    _store(a, 100)
    _store(b, 100)
    victims = _store(c, 100)  # 300 > 250 → a 常驻跳过，逐出 b
    assert [v for v, _t in victims] == [b]
    assert a._first_image is not None
    assert b._first_image is None and c._first_image is not None
    assert webm_clip._first_frame_bytes == 200


def test_all_pinned_budget_becomes_soft_cap():
    """剩余全是常驻时预算转软上限：不为压预算逐出常驻项。"""
    a, b, c = _FakeClip(100), _FakeClip(100), _FakeClip(100)
    a._ffr_pinned = True
    b._ffr_pinned = True
    c._ffr_pinned = True
    _store(a, 100)
    _store(b, 100)
    victims = _store(c, 100)  # 300 > 250，但全是常驻 → 一个都不逐
    assert victims == []
    assert a._first_image is not None
    assert b._first_image is not None and c._first_image is not None


def test_set_first_frame_budget_applies():
    """运行期预算设置生效：调小后按新预算逐出，恢复默认后不再逐出。"""
    webm_clip.set_first_frame_budget(150)
    try:
        a, b = _FakeClip(100), _FakeClip(100)
        _store(a, 100)
        victims = _store(b, 100)  # 200 > 150 → 逐出 a
        assert [v for v, _t in victims] == [a]
        assert webm_clip._first_frame_budget_bytes == 150
    finally:
        webm_clip.set_first_frame_budget(250)


def test_config_first_frame_budget_normalized(tmp_path):
    from pet.config import Config

    cfg = Config(tmp_path)
    assert cfg.get("first_frame_cache_max_mb") == 8  # 批10-A3：默认 32→8
    cfg.set("first_frame_cache_max_mb", 999)
    assert cfg.get("first_frame_cache_max_mb") == 64  # 夹到上限
    cfg.set("first_frame_cache_max_mb", "abc")
    assert cfg.get("first_frame_cache_max_mb") == 8  # 非法值回默认


def test_config_first_frame_budget_legacy_32_migrates_to_8(tmp_path):
    """批10-A3：批9 引入仅一天的旧默认 32 视为遗留值，加载时迁移到 8；
    其它显式值（如 16/64）不被动。"""
    import json

    from pet.config import Config

    cfg = Config(tmp_path)
    cfg.save()  # 确保配置文件落盘（构造不一定立即写）
    path = cfg.path
    data = json.loads(path.read_text(encoding="utf-8"))
    data["first_frame_cache_max_mb"] = 32
    path.write_text(json.dumps(data), encoding="utf-8")
    cfg2 = Config(tmp_path)
    assert cfg2.get("first_frame_cache_max_mb") == 8, "旧默认 32 应迁移到 8"

    data["first_frame_cache_max_mb"] = 16
    path.write_text(json.dumps(data), encoding="utf-8")
    cfg3 = Config(tmp_path)
    assert cfg3.get("first_frame_cache_max_mb") == 16, "显式非 32 值不得被迁移"
