# -*- coding: utf-8 -*-
"""WebM 元数据跨进程文件缓存的回归测试。

多开场景下，每个实例不应各自重复拉起 ffmpeg 探测同一段动画的
帧数/时长；文件缓存（key 含 mtime+size）应让第二个“进程”直接命中。

批 6-8b 修 3（5.6sol 全审 P2）：缓存写入必须单调累积——后写进程带着
旧内存快照写入时，写前重读磁盘合并，绝不覆盖先写进程刚加入的条目。
"""
from __future__ import annotations

import json
import threading
import types

from PySide6.QtWidgets import QApplication

import pet.webm_clip as webm_clip


def test_meta_file_cache_shared_across_instances(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])

    fake = types.SimpleNamespace(count_frames_and_secs=lambda path: (24, 1.0))
    monkeypatch.setattr(webm_clip, "imageio_ffmpeg", fake)
    monkeypatch.setattr(webm_clip, "_META_FILE_CACHE_PATH", tmp_path / "meta.json")
    monkeypatch.setattr(webm_clip, "_META_FILE_CACHE", None)
    monkeypatch.setattr(webm_clip, "_META_CACHE", {})

    video = tmp_path / "a.webm"
    video.write_bytes(b"fake")

    clip1 = webm_clip.WebMClip(video)
    clip1.warm_meta()
    assert clip1.duration() > 0
    assert (tmp_path / "meta.json").exists()

    # 模拟全新进程：清空内存缓存，只依赖文件缓存
    monkeypatch.setattr(webm_clip, "_META_FILE_CACHE", None)
    monkeypatch.setattr(webm_clip, "_META_CACHE", {})
    calls: list[str] = []
    fake.count_frames_and_secs = lambda path: calls.append(str(path)) or (24, 1.0)

    clip2 = webm_clip.WebMClip(video)
    clip2.warm_meta()
    assert calls == []  # 未再次调用 ffmpeg 探测
    assert clip2.duration() > 0

    app.processEvents()


def test_meta_file_cache_merges_stale_snapshot_entries(tmp_path, monkeypatch):
    """批 6-8b 修 3：后写进程带着旧快照写入时不得覆盖先写进程的新条目——
    写前重读磁盘合并（read-modify-write），缓存单调累积。"""
    app = QApplication.instance() or QApplication([])

    fake = types.SimpleNamespace(count_frames_and_secs=lambda path: (24, 1.0))
    monkeypatch.setattr(webm_clip, "imageio_ffmpeg", fake)
    monkeypatch.setattr(webm_clip, "_META_FILE_CACHE_PATH", tmp_path / "meta.json")
    monkeypatch.setattr(webm_clip, "_META_FILE_CACHE", None)
    monkeypatch.setattr(webm_clip, "_META_CACHE", {})

    video_a = tmp_path / "a.webm"
    video_a.write_bytes(b"fake-a")
    video_b = tmp_path / "b.webm"
    video_b.write_bytes(b"fake-b")

    # 进程 A：写入 a 的条目
    clip_a = webm_clip.WebMClip(video_a)
    clip_a.warm_meta()

    # 进程 B 的旧快照：在 A 写入前就已读盘（模拟旧内存快照，A 的条目不可见）
    monkeypatch.setattr(webm_clip, "_META_FILE_CACHE", {})

    # 进程 B：写入 b 的条目——必须重读磁盘合并，不得用旧快照覆盖
    clip_b = webm_clip.WebMClip(video_b)
    clip_b.warm_meta()

    raw = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert len(raw) == 2, "后写进程不得覆盖先写进程的新条目（缓存必须单调累积）"
    assert any("a.webm" in k for k in raw), "a 的条目必须保留"
    assert any("b.webm" in k for k in raw), "b 的条目必须写入"
    app.processEvents()


def test_meta_file_cache_concurrent_writers_accumulate(tmp_path, monkeypatch):
    """批 6-8b 修 3：并发写入（同进程多线程 + 跨进程锁文件）不丢条目。"""
    app = QApplication.instance() or QApplication([])

    fake = types.SimpleNamespace(count_frames_and_secs=lambda path: (24, 1.0))
    monkeypatch.setattr(webm_clip, "imageio_ffmpeg", fake)
    monkeypatch.setattr(webm_clip, "_META_FILE_CACHE_PATH", tmp_path / "meta.json")
    monkeypatch.setattr(webm_clip, "_META_FILE_CACHE", None)
    monkeypatch.setattr(webm_clip, "_META_CACHE", {})

    def _write(name: str) -> None:
        video = tmp_path / f"{name}.webm"
        video.write_bytes(b"fake")
        clip = webm_clip.WebMClip(video)
        clip.warm_meta()

    ts = [
        threading.Thread(target=_write, args=(n,), daemon=True)
        for n in ("a", "b", "c", "d")
    ]
    for t in ts:
        t.start()
    for t in ts:
        t.join(5.0)

    raw = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert len(raw) == 4, "并发写入不得互相覆盖（4 个条目必须全部保留）"
    app.processEvents()
