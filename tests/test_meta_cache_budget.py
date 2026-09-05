# -*- coding: utf-8 -*-
"""P2 内存预算审计：meta 缓存的字节预算 + LRU（批12）回归测试。

审计结论（scripts/audit_warm_cache.py，2026-09）：
- 首帧预热缓存按实例有界（动画数 × 帧字节，实测 97 段 = 85.25MB），
  不需逐出预算（P3 回滚记录同理由：与首帧原子认领协议耦合）；
- meta 缓存（_META_CACHE 进程内 + 磁盘文件缓存）key=(path|mtime|size)
  随素材更新单调新增、永不逐出——无界增长；本批用 frame_cache 的预算
  模式（ByteBudgetLru + 条目上限）封顶。

覆盖：
- ByteBudgetLru 记账/逐出语义（字节硬上界、LRU、替换、超预算跳过）；
- _META_CACHE 接到 ByteBudgetLru 后：预热行为不变、进程内缓存有界、
  逐出后经磁盘缓存命中不重复 ffmpeg 探测；
- 磁盘缓存条数上限（写路径合并后按先写入先逐出裁减）。
"""
from __future__ import annotations

import json
import types

from PySide6.QtWidgets import QApplication

import pet.webm_clip as webm_clip
from pet.frame_cache import ByteBudgetLru


def _qapp() -> QApplication:
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _fresh_lru(max_bytes: int, monkeypatch) -> ByteBudgetLru:
    """把模块内 _META_CACHE 换成受控预算的新实例（预热路径同用）。"""
    lru = ByteBudgetLru(max_bytes)
    monkeypatch.setattr(webm_clip, "_META_CACHE", lru)
    return lru


def _install_meta_env(tmp_path, monkeypatch):
    fake = types.SimpleNamespace(count_frames_and_secs=lambda path: (24, 1.0))
    monkeypatch.setattr(webm_clip, "imageio_ffmpeg", fake)
    monkeypatch.setattr(webm_clip, "_META_FILE_CACHE_PATH", tmp_path / "meta.json")
    monkeypatch.setattr(webm_clip, "_META_FILE_CACHE", None)
    return fake


# ================================================================ ByteBudgetLru 本体
class TestByteBudgetLru:
    def test_evicts_oldest_when_over_budget(self):
        cache = ByteBudgetLru(max_bytes=300)  # 每条默认 len+128 ≈ 129 → 装 2 条
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)
        assert len(cache) == 2
        assert cache.get("a") is None  # 最久未用先被逐出
        assert cache.get("c") == 3
        assert cache.total_bytes() <= cache.max_bytes()

    def test_get_refreshes_lru_order(self):
        cache = ByteBudgetLru(max_bytes=400)  # 3×129=387 可装下
        for k, v in (("a", 1), ("b", 2), ("c", 3)):
            cache.put(k, v)
        assert cache.get("a") == 1  # a 刷新到最近
        cache.put("d", 4)  # 逐出最久未用的 b
        assert cache.get("b") is None
        assert cache.get("a") == 1
        assert cache.get("c") == 3
        assert cache.get("d") == 4

    def test_replace_same_key_reaccounts_bytes(self):
        cache = ByteBudgetLru(max_bytes=1000)
        cache.put("a", 1, byte_size=100)
        assert cache.total_bytes() == 100
        cache.put("a", 2, byte_size=700)  # 替换：扣旧加新，不双计
        assert len(cache) == 1
        assert cache.total_bytes() == 700
        assert cache.get("a") == 2

    def test_oversized_entry_skipped_and_removes_old(self):
        cache = ByteBudgetLru(max_bytes=1000)
        cache.put("a", 1, byte_size=100)
        cache.put("big", 2, byte_size=5000)  # 单条超预算：不入缓存
        assert cache.get("big") is None
        assert len(cache) == 1
        cache.put("a", 3, byte_size=5000)  # 替换为超预算：旧条目一并移除
        assert len(cache) == 0
        assert cache.total_bytes() == 0

    def test_pop_and_clear(self):
        cache = ByteBudgetLru(max_bytes=1000)
        cache.put("a", 1, byte_size=100)
        cache.put("b", 2, byte_size=200)
        assert cache.pop("a", None) == 1
        assert cache.get("a") is None
        assert cache.total_bytes() == 200
        assert cache.pop("missing", "dflt") == "dflt"
        cache.clear()
        assert len(cache) == 0
        assert cache.total_bytes() == 0

    def test_hard_upper_bound_never_exceeded(self):
        cache = ByteBudgetLru(max_bytes=500)
        for i in range(50):
            cache.put(f"k{i}", i, byte_size=100 + (i % 7))
            assert cache.total_bytes() <= cache.max_bytes()
        assert len(cache) <= 5  # 500B / 最小 100B → 至多 5 条


# ================================================================ _META_CACHE 集成
class TestMetaCacheBudgetIntegration:
    def _make_videos(self, tmp_path, n: int):
        videos = []
        for i in range(n):
            p = tmp_path / f"clip-{i}.webm"
            p.write_bytes(b"fake")
            videos.append(p)
        return videos

    def test_warm_meta_bounded_in_process_cache(self, tmp_path, monkeypatch):
        _qapp()
        fake = _install_meta_env(tmp_path, monkeypatch)
        calls = []
        fake.count_frames_and_secs = lambda path: calls.append(str(path)) or (24, 1.0)
        lru = _fresh_lru(max_bytes=400, monkeypatch=monkeypatch)  # 每条约 130B → 装 3 条

        videos = self._make_videos(tmp_path, 10)
        for video in videos:
            webm_clip.WebMClip(video).warm_meta()

        assert len(calls) == 10  # 首次全部真探测
        assert len(lru) <= 3
        assert lru.total_bytes() <= lru.max_bytes()  # 进程内缓存字节有界

    def test_evicted_entry_served_from_file_cache_without_reprobe(
        self, tmp_path, monkeypatch
    ):
        """进程内被 LRU 逐出后，磁盘缓存仍在 → 不重复拉起 ffmpeg 探测。"""
        _qapp()
        fake = _install_meta_env(tmp_path, monkeypatch)
        calls = []
        fake.count_frames_and_secs = lambda path: calls.append(str(path)) or (24, 1.0)
        lru = _fresh_lru(max_bytes=400, monkeypatch=monkeypatch)

        videos = self._make_videos(tmp_path, 10)
        for video in videos:
            webm_clip.WebMClip(video).warm_meta()
        first_calls = len(calls)
        assert len(lru) < 10  # 小预算下进程内缓存确实发生了逐出
        # 逐出后同文件的新实例：应命中磁盘缓存（探测数不再增长）
        for video in videos:
            webm_clip.WebMClip(video).warm_meta()
        assert len(calls) == first_calls

    def test_meta_cache_plain_dict_monkeypatch_still_works(self, tmp_path, monkeypatch):
        """既有测试把 _META_CACHE 换回 {}：mapping 语义下预热路径不受影响。"""
        _qapp()
        fake = _install_meta_env(tmp_path, monkeypatch)
        monkeypatch.setattr(webm_clip, "_META_CACHE", {})
        video = self._make_videos(tmp_path, 1)[0]
        clip = webm_clip.WebMClip(video)
        clip.warm_meta()
        assert clip.duration() > 0
        assert len(webm_clip._META_CACHE) == 1


# ================================================================ 磁盘缓存条数上限
class TestMetaFileCacheCap:
    def test_file_cache_pruned_to_cap_on_write(self, tmp_path, monkeypatch):
        _qapp()
        fake = _install_meta_env(tmp_path, monkeypatch)
        monkeypatch.setattr(webm_clip, "_META_FILE_CACHE_MAX_ENTRIES", 3)

        for i in range(6):
            video = tmp_path / f"clip-{i}.webm"
            video.write_bytes(b"fake")
            webm_clip.WebMClip(video).warm_meta()

        raw = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
        assert len(raw) == 3  # 写路径合并后裁到上限
        # 最旧写入的先被逐出；最新 3 个仍在
        keys = list(raw)
        assert any("clip-3" in k for k in keys)
        assert any("clip-5" in k for k in keys)
        assert not any("clip-0" in k for k in keys)
        assert not any("clip-1" in k for k in keys)

    def test_cap_does_not_evict_under_limit(self, tmp_path, monkeypatch):
        """正常量（< 上限）保持单调累积语义，与批 6-8b 修 3 契约一致。"""
        _qapp()
        fake = _install_meta_env(tmp_path, monkeypatch)
        monkeypatch.setattr(webm_clip, "_META_FILE_CACHE_MAX_ENTRIES", 100)

        for i in range(4):
            video = tmp_path / f"clip-{i}.webm"
            video.write_bytes(b"fake")
            webm_clip.WebMClip(video).warm_meta()

        raw = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
        assert len(raw) == 4  # 未到上限：全部保留（单调累积不变）
