# -*- coding: utf-8 -*-
"""预缩放帧缓存：最终 QPixmap 的有界 LRU（_plan/WIN_PERF_RESEARCH_SOL.md §3.1 方案 A）。

同一动画同一帧在相同（素材路径+mtime+大小、帧号、朝向、scale、DPR、动画名）下
结果完全确定：把整条 toImage→镜像→预乘→Smooth 缩放→ARGB32→fromImage 链的
最终产物（QPixmap + 缩放后 ARGB32 图）缓存起来，动画循环播放时直接复用，
跳过整条 CPU 转换链。

- 字节预算为硬上界（默认 256MB，按 QPixmap+QImage 双份 ARGB32 记账）：
  插入后超限逐出最久未用（LRU），任何时刻 total_bytes() <= max_bytes()；
- 单帧超过预算的条目不入缓存（当前帧仍由调用方直接持有显示），避免大条目
  独占预算导致小条目插入后立即被逐出的缓存抖动；
- 命中/逐出/缺失/插入计数对外暴露，供测试断言与遥测。

P2（批12）：`ByteBudgetLru` 是本文件预算/逐出模式的通用（非 Qt）复用件，
供 webm_clip 等「字节预算 + LRU」的进程内小缓存使用；`FramePixmapCache`
保持 Qt 专门实现不变。
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Any

# 默认字节预算：64MB（按 QPixmap+QImage 双份记账）。只缓存最近播放的成品帧，
# 远小于"整段动画全展开"；单帧超过预算不入缓存，预算为硬上界。
# 实测记录（2026-09-01）：256MB 预算下内存涨约 200MB 但待机 CPU 没降——
# 收紧到短动画（点击/拖拽等高频交互路径）装得下的量级，长动画不缓存。
FRAME_CACHE_DEFAULT_MAX_BYTES = 64 * 1024 * 1024


class ByteBudgetLru:
    """通用「字节预算 + LRU」缓存（frame_cache 预算模式的非 Qt 复用件，P2）。

    `FramePixmapCache` 是它面向 QPixmap+QImage 双份记账的专门化；本类只做
    与它同构的通用记账，不持有任何 Qt 对象：

    - 字节预算为硬上界：put/``__setitem__`` 后超限逐出最久未用（LRU），
      任何时刻 ``total_bytes() <= max_bytes()``（len>1 守卫防止逐空）；
    - 单条超预算不入缓存（与 FramePixmapCache 同语义：防大条目独占预算
      造成小条目插入后立即被逐出的抖动）；
    - get() 命中刷新 LRU 序；key/value 可为任意可哈希对象与值；
    - 缺省字节按 ``len(key) + 128`` 保守估算（key 需支持 len()，为 str
      键场景设计；其它键请显式传 byte_size）。

    线程：记账不加锁（与 FramePixmapCache 同约定——GIL 下 OrderedDict
    读写近似原子）；跨线程使用者按需自行加锁。
    """

    _OVERHEAD_ESTIMATE = 128

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max(1, int(max_bytes))
        # key -> (value, byte_size)：逐条记账，替换/逐出时按各自字节扣减
        self._data: OrderedDict[Any, tuple[Any, int]] = OrderedDict()
        self._bytes = 0

    def get(self, key):
        """取条目并刷新 LRU 序；未命中返回 None。"""
        entry = self._data.get(key)
        if entry is None:
            return None
        self._data.move_to_end(key)
        return entry[0]

    def put(self, key, value, byte_size: int | None = None) -> None:
        """插入/替换条目（等价 ``cache[key] = value``，可显式给字节数）。

        缺省按 ``len(key) + 128`` 保守估算。单条超预算不入缓存（防御性
        移除同 key 旧条目，绝不留下按旧记账的陈旧条目）；超限后逐出
        最久未用，预算为硬上界。
        """
        if byte_size is None:
            byte_size = len(key) + self._OVERHEAD_ESTIMATE
        byte_size = max(1, int(byte_size))
        old = self._data.pop(key, None)
        if old is not None:
            self._bytes -= old[1]
        if byte_size > self._max_bytes:
            return  # 超预算条目不缓存（同 key 旧条目已在上面移除）
        self._data[key] = (value, byte_size)
        self._bytes += byte_size
        while self._bytes > self._max_bytes and len(self._data) > 1:
            _, victim = self._data.popitem(last=False)
            self._bytes -= victim[1]

    def __setitem__(self, key, value) -> None:
        self.put(key, value)

    def pop(self, key, default=None):
        """移除条目并返回其值；不存在返回 default。"""
        entry = self._data.pop(key, None)
        if entry is None:
            return default
        self._bytes -= entry[1]
        return entry[0]

    def clear(self) -> None:
        self._data.clear()
        self._bytes = 0

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key) -> bool:
        return key in self._data

    def __iter__(self):
        """迭代 key（dict 兼容；审计/调试用）。"""
        return iter(self._data)

    def keys(self):
        return self._data.keys()

    def total_bytes(self) -> int:
        return self._bytes

    def max_bytes(self) -> int:
        return self._max_bytes


class FramePixmapCache:
    """QPixmap + 缩放后 ARGB32 图 的字节硬预算 LRU 缓存。

    get() 命中会刷新 LRU 序；put() 插入后按字节预算逐出最久未用。
    字节按双份（QPixmap + QImage）ARGB32 估算：默认 w*h*4*2，取保守上界
    （QPixmap 与源 QImage 可能共享底层存储，实际占用通常更低）。
    单帧超过预算的条目不入缓存（当前帧仍由调用方直接持有显示），因此
    预算严格不被突破，也不存在"大条目独占预算"导致的抖动。
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
        """插入/替换条目。

        byte_size 缺省按「QPixmap + QImage 双份 ARGB32」估算（w*h*4*2）：
        每条目同时持有缩放后 QImage 与由其创建的 QPixmap，两者在 raster/
        Windows 平台是独立像素存储，预算按双份记账（保守上界）。

        单帧超过预算的条目不入缓存：当前帧仍由调用方直接持有显示，但不再
        占用预算——避免大条目独占预算导致小条目"插入后立即被逐出"的抖动，
        也保证字节预算为硬上界（total_bytes() <= max_bytes()）。
        超预算且同 key 已有旧条目时，旧条目一并移除，绝不留下陈旧像素。
        """
        if byte_size is None:
            byte_size = max(1, image.width() * image.height() * 4 * 2)
        byte_size = max(1, int(byte_size))
        if byte_size > self._max_bytes:
            # 超预算条目不缓存（防御性移除同 key 旧条目，避免返回陈旧像素）
            old = self._data.pop(key, None)
            if old is not None:
                self._bytes -= old.byte_size
            return
        old = self._data.pop(key, None)
        if old is not None:
            self._bytes -= old.byte_size
        self._data[key] = _FrameCacheEntry(pixmap, image, byte_size)
        self._bytes += byte_size
        self.inserts += 1
        # 超预算逐出最久未用（LRU）。条目都 <= 预算，循环终止时必有
        # total_bytes() <= max_bytes()（len>1 守卫防止逐空最后一条）。
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
