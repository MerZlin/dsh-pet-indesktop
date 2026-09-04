# -*- coding: utf-8 -*-
"""字节预算 + LRU 的通用小缓存（ByteBudgetLru）。

供 webm_clip 等「字节预算 + LRU」的进程内小缓存使用。纯 Python 实现，
不依赖 Qt。
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Any


class ByteBudgetLru:
    """通用「字节预算 + LRU」缓存。

    - 字节预算为硬上界：put/``__setitem__`` 后超限逐出最久未用（LRU），
      任何时刻 ``total_bytes() <= max_bytes()``（len>1 守卫防止逐空）；
    - 单条超预算不入缓存（防大条目独占预算造成小条目插入后立即被逐出的
      抖动）；
    - get() 命中刷新 LRU 序；key/value 可为任意可哈希对象与值；
    - 缺省字节按 ``len(key) + 128`` 保守估算（key 需支持 len()，为 str
      键场景设计；其它键请显式传 byte_size）。

    线程：记账不加锁（GIL 下 OrderedDict 读写近似原子）；跨线程使用者
    按需自行加锁。
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
