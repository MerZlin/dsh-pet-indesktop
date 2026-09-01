# -*- coding: utf-8 -*-
"""首帧 QImage 字节预算 LRU（_plan/WIN_PERF_RESEARCH_SOL.md §4.2 L2 / §4.4）。

每个动画一条首帧 QImage（WebMClip._first_image）。预算按「角色内动画数 ×
单帧大小」计算：单个角色全部动画各缓存一张首帧正好占满预算，是硬上界。
超限时逐出最久未用的冷门动画首帧（clip.evict_first_frame()），冷动画被逐出
后下次用到再解码（允许代价；§4.4 回收顺序步骤 1/2）。

绝不触碰当前播放帧 / 当前 alpha 图：逐出只清首帧缓存与完成事件，且 LRU
天然保护刚用过的动画（正在播放的动画首帧刚被读取/应用，处于最近使用端）。
"""
from __future__ import annotations

import itertools
import threading
from collections import OrderedDict


def character_first_frame_budget(animation_count, frame_w, frame_h, bpp=4) -> int:
    """角色内首帧预算 = 动画数 × 单帧 RGBA 大小（§4.2 计算方法）。"""
    return (max(1, int(animation_count))
            * max(1, int(frame_w)) * max(1, int(frame_h)) * max(1, int(bpp)))


class FirstFrameCache:
    """clip 首帧 QImage 的字节硬预算 LRU（每库一个，跨线程安全）。

    clip 协议（duck-typed，与 WebMClip 一致）：
    - clip._first_image：当前首帧 QImage 或 None；
    - clip._first_frame_bytes：该 clip 单帧记账字节（w*h*bpp）；
    - clip._first_frame_done：threading.Event，与 _first_image 同生命周期；
    - clip.evict_first_frame()：逐出本 clip 首帧（清图 + 完成事件）。

    note_stored(clip)：clip 写入首帧缓存后调用（含后台预热 worker 线程）；
    note_used(clip)：clip 首帧被读取/应用后调用（GUI 线程）。
    两者都在内部锁下更新 LRU 序并按字节预算逐出最久未用者。
    """

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max(1, int(max_bytes))
        self._order: OrderedDict[int, object] = OrderedDict()
        self._clock = itertools.count()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.inserts = 0

    def note_stored(self, clip) -> None:
        """clip 首帧已写入缓存：登记/刷新 LRU 序并按字节预算逐出。"""
        if getattr(clip, '_first_image', None) is None:
            # 已无首帧（外部直接逐出等）：从治理序移除，避免占位与字节虚计
            with self._lock:
                self._order.pop(id(clip), None)
            return
        with self._lock:
            cid = id(clip)
            if cid not in self._order:
                self._order[cid] = clip
                self.inserts += 1
            self._order.move_to_end(cid)
            self._trim_locked()

    def note_used(self, clip) -> None:
        """clip 首帧被读取/应用：命中刷新 LRU 序；未命中累计 miss（用到再解码）。"""
        with self._lock:
            cid = id(clip)
            if cid in self._order and getattr(clip, '_first_image', None) is not None:
                self.hits += 1
                self._order.move_to_end(cid)
            else:
                self.misses += 1

    def _trim_locked(self) -> None:
        """超预算逐出最久未用者（LRU）。len>1 守卫：最后一条绝不逐空
        （与 FramePixmapCache 同策略，防单条目抖动）。"""
        while len(self._order) > 1 and self._bytes_locked() > self._max_bytes:
            _, victim = self._order.popitem(last=False)
            self.evictions += 1
            try:
                victim.evict_first_frame()
            except Exception:
                pass  # 防御：逐出失败不阻断后续条目（clip 可能已销毁）

    def _bytes_locked(self) -> int:
        return sum(max(1, int(getattr(c, '_first_frame_bytes', 0)))
                   for c in self._order.values())

    def stats(self) -> dict:
        """命中/逐出/插入/缺失计数 + 当前条目/字节/预算，供测试断言与遥测。"""
        with self._lock:
            return {
                "hits": self.hits,
                "misses": self.misses,
                "evictions": self.evictions,
                "inserts": self.inserts,
                "entries": len(self._order),
                "bytes": self._bytes_locked(),
                "max_bytes": self._max_bytes,
            }

    def total_bytes(self) -> int:
        with self._lock:
            return self._bytes_locked()

    def max_bytes(self) -> int:
        return self._max_bytes

    def __len__(self) -> int:
        with self._lock:
            return len(self._order)
