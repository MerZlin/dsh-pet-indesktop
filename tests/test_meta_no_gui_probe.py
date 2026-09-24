# -*- coding: utf-8 -*-
"""GUI 线程绝不跑 ffprobe 元数据探测（冷 meta 成簇卡顿修复）回归。

实测定案：冷 meta 的 ffprobe 子进程探测落在 GUI 线程 = 使用中
100-250ms 成簇卡顿（冻结现场采样抓到现行：随机动作首播时 _ensure_meta
经 imageio count_frames_and_secs 拉起 ffprobe）。锁定：
1. 主线程 _ensure_meta 缓存未命中时不探测，踢后台预热并吃默认值；
2. 后台线程 _ensure_meta 正常探测并写缓存（预热链语义不变）；
3. 主线程每 clip 只踢一次后台预热（不形成线程洪峰）。
"""

from __future__ import annotations

import threading
import time

import pytest
from PySide6.QtWidgets import QApplication

import pet.webm_clip as webm_clip_mod
from pet.webm_clip import WebMClip


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


class _ProbeClip(WebMClip):
    """count_frames_and_secs 调用桩（记录调用线程与次数）。"""

    def __init__(self, path, parent=None):
        super().__init__(path, parent)
        self.probe_calls = []

    def _probe(self):
        self.probe_calls.append(threading.get_ident())
        return (241, 10.042)


def test_ensure_meta_never_probes_on_gui_thread(app, monkeypatch):
    """硬不变量：GUI 线程冷 meta 不跑 ffprobe——踢后台、吃默认值。

    时序纪律：探测桩在事件闸门前阻塞，保证「踢出瞬间」的断言是确定性的；
    探测放行后必须落在后台线程（事件同步，不赌线程启动速度）。
    """
    clip = _ProbeClip("dummy.webm")
    calls = clip.probe_calls
    gate = threading.Event()

    def gated_probe(key):
        gate.wait(5.0)
        return clip._probe()

    monkeypatch.setattr(webm_clip_mod.imageio_ffmpeg, "count_frames_and_secs", gated_probe)
    webm_clip_mod._META_CACHE.clear()
    monkeypatch.setattr(webm_clip_mod, "_get_meta_file_cache", lambda: {})  # 双级缓存 miss
    clip._ensure_meta()  # 主线程调用
    assert calls == [], "踢出瞬间不得有任何探测（后台也未放行）"
    assert clip._meta_bg_kicked, "主线程冷调用必须同步踢出后台预热"
    assert clip._duration <= 0, "探测被拦期间保留默认值（reader 后续补充）"
    gate.set()  # 放行后台探测
    deadline = time.monotonic() + 5.0
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(calls) == 1, "后台预热必须接力探测一次"
    assert calls[0] != threading.get_ident(), "探测绝不允许落在 GUI 线程"
    clip.cleanup()
    app.processEvents()


def test_ensure_meta_kicks_background_warm_only_once(app, monkeypatch):
    """主线程重复冷调用只踢一次后台预热（防线程洪峰）。"""
    clip = _ProbeClip("dummy.webm")
    monkeypatch.setattr(webm_clip_mod.imageio_ffmpeg, "count_frames_and_secs", lambda key: clip._probe())
    webm_clip_mod._META_CACHE.clear()
    monkeypatch.setattr(webm_clip_mod, "_get_meta_file_cache", lambda: {})
    clip._ensure_meta()
    clip._ensure_meta()
    assert clip._meta_bg_kicked, "主线程首次调用必须同步踢出后台预热"
    clip._ensure_meta()
    deadline = time.monotonic() + 2.0
    while not clip.probe_calls and time.monotonic() < deadline:
        app.processEvents()
    assert len(clip.probe_calls) == 1, "两次主线程调用合计只踢一次后台预热"
    clip.cleanup()
    app.processEvents()


def test_ensure_meta_probes_normally_on_worker_thread(app, monkeypatch):
    """后台线程：探测/写缓存正常（预热链语义不变）。"""
    clip = _ProbeClip("dummy.webm")
    monkeypatch.setattr(webm_clip_mod.imageio_ffmpeg, "count_frames_and_secs", lambda key: clip._probe())
    webm_clip_mod._META_CACHE.clear()
    monkeypatch.setattr(webm_clip_mod, "_get_meta_file_cache", lambda: {})
    t = threading.Thread(target=clip._ensure_meta, daemon=True)
    t.start()
    t.join(5.0)
    assert len(clip.probe_calls) == 1
    assert clip._frame_count == 241 and clip._duration == 10.042
    clip.cleanup()
    app.processEvents()
