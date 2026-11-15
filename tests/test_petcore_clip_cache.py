# -*- coding: utf-8 -*-
"""ClipLibrary 的缓存上界：长时间运行不许把整库动画一直留住。

每个 clip 都带播放器/预取 worker 与至少一帧内存；没有上界时 90+ 段动画会在
一场会话里越攒越多（旧版线程/句柄缓慢上涨的同构风险面）。
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from petcore.contracts.content import AnimationSpec
from petcore.engine.enums import AnimatType, GraphType
from petcore.media import library as library_mod
from petcore.media.library import ClipLibrary


class _FakeClip:
    def __init__(self, name: str) -> None:
        self.name = name
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _spec(index: int) -> AnimationSpec:
    return AnimationSpec(
        name=f"anim-{index}",
        graph=GraphType.IDEL,
        animat=AnimatType.SINGLE,
        path=Path(f"anim-{index}.webm"),
    )


def test_clip_cache_evicts_the_least_recently_used(monkeypatch) -> None:
    created: list[_FakeClip] = []

    def _create(path, name):
        clip = _FakeClip(name)
        created.append(clip)
        return clip

    monkeypatch.setattr(library_mod, "create_clip", _create)
    library = ClipLibrary("off")
    library.MAX_CLIPS = 2
    first = library.clip_for(_spec(0))
    second = library.clip_for(_spec(1))
    assert first is not None and second is not None
    library.clip_for(_spec(0))  # 触碰：0 变成最近使用
    third = library.clip_for(_spec(2))
    assert third is not None
    assert len(created) == 3
    assert created[1].closed is True, "最久未用的 clip 必须被关掉"
    assert created[0].closed is False and created[2].closed is False
    assert library.clip_for(_spec(0)) is created[0], "未淘汰的 clip 仍然复用"


def test_cached_clip_is_reused_without_recreating(monkeypatch) -> None:
    calls: list[str] = []

    def _create(path, name):
        calls.append(name)
        return _FakeClip(name)

    monkeypatch.setattr(library_mod, "create_clip", _create)
    library = ClipLibrary("off")
    spec = _spec(7)
    one = library.clip_for(spec)
    two = library.clip_for(spec)
    assert one is two
    assert calls == ["anim-7"]
