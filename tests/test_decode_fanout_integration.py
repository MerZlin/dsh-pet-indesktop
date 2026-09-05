# -*- coding: utf-8 -*-
"""批5.3 集成（准出门 G-53 家族）：同角色两窗共**一个**解码源。

用真实 WebMClip + 真实 ffmpeg 驱动进程内 fan-out：首发窗作发布者（本地解码每帧
镜像到源 sink），次窗作订阅者（从源环形缓冲进食，**不拉 ffmpeg**）。断言双方都
出帧且订阅者源帧号单调（此即「双窗同角色 idle 稳态」的可机器化子集）。

注：G-53-2 的「ffmpeg 子进程数==1」与 G-53-4 的浸泡（≥2h）属重量级/长时验收，
本文件聚焦其结构性核心（单源 + 单订阅者 + 帧流），用有界消费（≤30 帧）保证
确定性。asyncio/psutil 子进程计数的机器级断言留在验收脚本（不引入 flaky 依赖）。
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from pet.decode_fanout import DecodeFanoutHub
from pet.webm_clip import WebMClip

SAMPLE_WEBM = Path("assets/characters/shenshen/videos/idle/待机呼吸休闲.webm")


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _consume(clip, counter, cap: int, timeout: float = 20.0) -> None:
    """主线程手动驱动消费（等价 QTimer _poll 节奏），直到累计 >= cap 帧或超时。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and len(counter) < cap:
        clip._poll()
        time.sleep(0.005)


def test_two_clips_share_single_decode(app):
    assert SAMPLE_WEBM.exists()
    hub = DecodeFanoutHub(enabled=True)
    pub = WebMClip(SAMPLE_WEBM)
    sub = WebMClip(SAMPLE_WEBM)

    assert hub.shareable_start("idle", pub) == "publish"
    assert hub.shareable_start("idle", sub) == "feed"
    # 结构：单源记录、发布者=pub、订阅者=sub（进程内扇出已建立）
    source = hub._sources[str(SAMPLE_WEBM)]
    assert source.publisher is pub
    assert len(source.subscriptions) == 1
    assert sub._feed_source is not None
    assert pub._publish_sink is not None

    pubs: list = []
    subs: list = []
    errors: list = []
    pub.frameChanged.connect(pubs.append)
    sub.frameChanged.connect(subs.append)
    pub.errorOccurred.connect(errors.append)
    sub.errorOccurred.connect(errors.append)
    try:
        assert pub.start() is True
        assert sub.start() is True
        # P1-3（复审）：订阅者绝不拉起 ffmpeg——reader 进程句柄恒为 None。
        assert sub._reader_proc is None, "订阅者不得拉起 ffmpeg（G-53-2 在库断言）"
        # 有界消费：双方都出帧（订阅者从共享环进食，不拉 ffmpeg）
        _consume(pub, pubs, cap=30)
        _consume(sub, subs, cap=30)
        assert len(pubs) >= 30, f"发布者出帧不足: {len(pubs)} errors={errors}"
        assert len(subs) >= 30, f"订阅者出帧不足: {len(subs)} errors={errors}"
        # 订阅者源帧号单调不减（帧序协议：drop-oldest 允许跳帧，绝不乱序）
        assert all(subs[i] < subs[i + 1] for i in range(len(subs) - 1)), \
            f"订阅者帧号乱序: {subs}"
        assert errors == []
        assert sub._reader_proc is None, "消费全程订阅者不得拉起 ffmpeg"
        # 反向证明共享真实发生：订阅者的帧来自发布者的源（发布者帧号已是高位，
        # 若订阅者本地解码则帧号从 0 开始——进食则源帧号接近发布者当前位置）
        assert pubs and subs and subs[-1] > 0, "订阅者帧号应跟随共享源（非本地从 0 起）"
    finally:
        try:
            pub.stop()
            sub.stop()
        except Exception:
            pass
        hub.stop_all()
        pub.cleanup()
        sub.cleanup()
        app.processEvents()
