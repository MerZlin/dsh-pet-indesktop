# -*- coding: utf-8 -*-
"""预缩放帧缓存：最终 QPixmap 的有界 LRU（_plan/WIN_PERF_RESEARCH_SOL.md §3.1 方案 A）。

同一动画同一帧在相同（素材路径+mtime、帧号、朝向、scale、DPR、动画名）下
结果完全确定：把整条 toImage→镜像→预乘→Smooth 缩放→ARGB32→fromImage 链的
最终产物（QPixmap + 缩放后 ARGB32 图）缓存起来，动画循环播放时直接复用，
跳过整条 CPU 转换链。

- 按字节预算有界（默认 256MB）：插入后超限逐出最久未用（LRU）；
- 单帧超过预算时保留该条目（预算可被单帧略微超出），不崩溃；
- 命中/逐出/缺失/插入计数对外暴露，供测试断言与遥测。
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Any

# 默认字节预算：256MB。只缓存最近播放的成品帧，远小于"整段动画全展开"。
FRAME_CACHE_DEFAULT_MAX_BYTES = 256 * 1024 * 1024


class FramePixmapCache:
    """QPixmap + 缩放后 ARGB32 图 的字节预算 LRU 缓存。

    get() 命中会刷新 LRU 序；put() 插入后按字节预算逐出最久未用。
    只应在 GUI 线程使用（条目持有 QPixmap / QImage，有 Qt 线程亲和）。
    """

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max(1, int(max_bytes))
        self._data: OrderedDict[Any, _FrameCacheEntry] = OrderedDict()
        self._bytes = 0
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.inserts = 0

    def get(self, key):
        """取条目并刷新 LRU 序；未命中返回 None 并累计 miss。"""
        entry = self._data.get(key)
        if entry is None:
            self.misses += 1
            return None
        self.hits += 1
        self._data.move_to_end(key)
        return entry

    def put(self, key, pixmap, image, byte_size: int | None = None) -> None:
        """插入/替换条目；byte_size 缺省按 ARGB32 图像字节数（w*h*4）计。"""
        if byte_size is None:
            byte_size = max(1, image.width() * image.height() * 4)
        byte_size = max(1, int(byte_size))
        old = self._data.pop(key, None)
        if old is not None:
            self._bytes -= old.byte_size
        self._data[key] = _FrameCacheEntry(pixmap, image, byte_size)
        self._bytes += byte_size
        self.inserts += 1
        # 超预算逐出最久未用；单帧超预算时保留刚插入条目（至少留 1 条，
        # 预算可被单帧略微超出，但绝不崩溃、绝不自我逐出）
        while self._bytes > self._max_bytes and len(self._data) > 1:
            _, victim = self._data.popitem(last=False)
            self._bytes -= victim.byte_size
            self.evictions += 1

    def clear(self) -> None:
        """清空条目与字节（计数保留，作为生命周期累计值）。"""
        self._data.clear()
        self._bytes = 0

    def __len__(self) -> int:
        return len(self._data)

    def total_bytes(self) -> int:
        return self._bytes

    def max_bytes(self) -> int:
        return self._max_bytes

    def stats(self) -> dict:
        """命中/逐出/缺失/插入计数 + 当前条目/字节/预算，供测试断言与遥测。"""
        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "inserts": self.inserts,
            "entries": len(self._data),
            "bytes": self._bytes,
            "max_bytes": self._max_bytes,
        }


class _FrameCacheEntry:
    __slots__ = ("pixmap", "image", "byte_size")

    def __init__(self, pixmap, image, byte_size: int) -> None:
        self.pixmap = pixmap
        self.image = image
        self.byte_size = byte_size
