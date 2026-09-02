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
    monkeypatch.setattr(webm_clip, "_FIRST_FRAME_BUDGET_BYTES", 250)
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
    assert victims == [a]
    assert a._first_image is None
    assert b._first_image is not None and c._first_image is not None
    assert webm_clip._first_frame_bytes == 200


def test_touch_reorders_lru():
    a, b, c = _FakeClip(100), _FakeClip(100), _FakeClip(100)
    _store(a, 100)
    _store(b, 100)
    webm_clip._ffr_touch(a)  # a 变最近使用
    victims = _store(c, 100)
    assert victims == [b]  # 逐出 b 而非 a
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
