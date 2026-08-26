# -*- coding: utf-8 -*-
"""WebM 元数据跨进程文件缓存的回归测试。

多开场景下，每个实例不应各自重复拉起 ffmpeg 探测同一段动画的
帧数/时长；文件缓存（key 含 mtime+size）应让第二个“进程”直接命中。
"""
from __future__ import annotations

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
