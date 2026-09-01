# -*- coding: utf-8 -*-
"""首帧 QImage 字节预算 LRU（_plan/WIN_PERF_RESEARCH_SOL.md §4.2 L2 / §4.4）。

每个动画一条首帧 QImage（WebMClip._first_image）。预算按「角色内动画数 ×
单帧大小」计算：单个角色全部动画各缓存一张首帧正好占满预算，是硬上界。
超限时逐出最久未用的冷门动画首帧（clip.evict_first_frame()），冷动画被逐出
后下次用到再解码（允许代价；§4.4 回收顺序步骤 1/2）。

P3 复审（875a246 一审）修复的治理协议：
- pin/unpin 保护：正在播放（start() 中）或正在等待应用首帧（同步解码等待
  窗口）的 clip 首帧绝不可被逐出——不是靠 LRU touch（播放期间不 touch），
  而是显式 pin；全部条目被 pin 时预算暂为软约束，播放结束后恢复硬约束。
- 超预算单条目不入缓存：单帧记账字节超过预算的条目在登记时即拒收并逐出
  图像（与 FramePixmapCache 同语义），预算恒为硬上界 bytes <= max_bytes。
- clear()：库级关闭路径（MovieLibrary.close）清空全部条目、逐出全部首帧
  图像并释放全部 pin——角色切换后旧库不再持有任何 clip/QImage。

逐出绝不触碰当前播放帧 / 当前 alpha 图：只清首帧缓存图像，且 _first_frame_done
（「解码已完成」的粘性事件）由 WebMClip 持有、治理逐出不撤销（解码完成与
缓存持有两个状态分离）。
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
    - clip._first_frame_done：threading.Event，解码已完成（粘性，逐出不清）；
    - clip.evict_first_frame()：逐出本 clip 首帧图像（清 _first_image）。

    note_stored(clip)：clip 写入首帧缓存后调用（含后台预热 worker 线程）；
    note_used(clip)：clip 首帧被读取/应用后调用（GUI 线程）。
    两者都在内部锁下更新 LRU 序并按字节预算逐出最久未用者。

    pin(clip)/unpin(clip)：治理保护。pin 的条目绝不参与逐出（正在播放 /
    正在等待应用首帧），unpin 后恢复可逐出。pin 计数可重入（start + 同步
    认领可叠加）。
    """

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max(1, int(max_bytes))
        self._order: OrderedDict[int, object] = OrderedDict()
        # pin 计数（cid -> 次数）：正在播放 / 正在等待应用首帧的 clip 不可逐出。
        self._pins: dict[int, int] = {}
        self._clock = itertools.count()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.inserts = 0

    def pin(self, clip) -> None:
        """治理保护：pin 的 clip 首帧绝不可被逐出（可重入计数）。"""
        with self._lock:
            cid = id(clip)
            self._pins[cid] = self._pins.get(cid, 0) + 1

    def unpin(self, clip) -> None:
        """释放一次治理保护；计数归零后恢复可逐出。"""
        with self._lock:
            cid = id(clip)
            n = self._pins.get(cid, 0)
            if n <= 1:
                self._pins.pop(cid, None)
            else:
                self._pins[cid] = n - 1

    def note_stored(self, clip) -> None:
        """clip 首帧已写入缓存：登记/刷新 LRU 序并按字节预算逐出。

        单帧记账字节超过预算的条目不入缓存（与 FramePixmapCache 同语义）：
        立即逐出该 clip 首帧图像并从治理序移除——预算恒为硬上界，不存在
        「单条超预算永久超限」的漏洞（P3 复审 P1）。
        """
        if getattr(clip, '_first_image', None) is None:
            # 已无首帧（外部直接逐出等）：从治理序移除，避免占位与字节虚计
            with self._lock:
                self._order.pop(id(clip), None)
            return
        frame_bytes = max(1, int(getattr(clip, '_first_frame_bytes', 0)))
        if frame_bytes > self._max_bytes:
            # 超预算单条目不缓存：逐出图像（当前帧仍由调用方持有显示），
            # 下次用到再解码（允许代价）——与 FramePixmapCache 拒绝大条目一致。
            with self._lock:
                self._order.pop(id(clip), None)
            try:
                clip.evict_first_frame()
            except Exception:
                pass
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
        """超预算逐出最久未用的非 pin 条目（LRU）。

        条目在登记时已保证单条 <= 预算（超预算在 note_stored 拒收），因此
        循环必然在逐出到 bytes <= max_bytes 时终止；全部条目均被 pin 时
        无可逐出者、立即停止（播放期间预算为软约束，播放结束恢复硬约束）。
        """
        while self._bytes_locked() > self._max_bytes:
            victim_cid = None
            for cid in self._order:
                if cid not in self._pins:
                    victim_cid = cid
                    break
            if victim_cid is None:
                break  # 全部被 pin：正在播放/等待应用，不可逐出
            victim = self._order.pop(victim_cid)
            self.evictions += 1
            try:
                victim.evict_first_frame()
            except Exception:
                pass  # 防御：逐出失败不阻断后续条目（clip 可能已销毁）

    def _bytes_locked(self) -> int:
        return sum(max(1, int(getattr(c, '_first_frame_bytes', 0)))
                   for c in self._order.values())

    def clear(self) -> None:
        """库级关闭（MovieLibrary.close）：清空全部条目并逐出全部首帧图像、
        释放全部 pin——旧库不再持有任何 clip 首帧 QImage（P3 复审 P1）。
        计数保留，作为生命周期累计值。"""
        with self._lock:
            for clip in self._order.values():
                try:
                    clip.evict_first_frame()
                except Exception:
                    pass
            self._order.clear()
            self._pins.clear()

    def stats(self) -> dict:
        """命中/逐出/插入/缺失计数 + 当前条目/字节/预算/pin，供测试断言与遥测。"""
        with self._lock:
            return {
                "hits": self.hits,
                "misses": self.misses,
                "evictions": self.evictions,
                "inserts": self.inserts,
                "entries": len(self._order),
                "bytes": self._bytes_locked(),
                "max_bytes": self._max_bytes,
                "pinned": len(self._pins),
            }

    def total_bytes(self) -> int:
        with self._lock:
            return self._bytes_locked()

    def max_bytes(self) -> int:
        return self._max_bytes

    def __len__(self) -> int:
        with self._lock:
            return len(self._order)
