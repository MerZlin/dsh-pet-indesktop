# -*- coding: utf-8 -*-
"""低开销性能观测（P0）：轻量计数器 / 累计耗时，默认零开销。

启用方式（任选）：
- 进程启动前设环境变量 ``PET_PERF_STATS``（任意非空值即启用）；
- 运行中 ``perfstats.enable()`` / ``perfstats.disable()`` 动态开关
  （测试 / 调试用，下一帧起生效）。

输出：``perfstats.dump()`` 打一份快照 —— 环境变量
``PET_PERF_STATS_FILE`` 指定了路径则写 JSON 文件，否则走 logging
（logger name ``pet.perfstats``，INFO 级）。启用时注册 atexit 钩子，
进程正常退出自动 dump 一次，无需业务代码显式调用。

产品打点指标清单（快照键 ``{name: {count, total}}``；计时键 total 单位 =
秒，avg = total/count×1000ms 在 dump 日志中直接给出）。来源：webm_clip.py
（webm.*）与 window.py（rebuild.* / frame_cache.* / paint.draw）：

- ``webm.first_frame``：首帧解码核心段 = ffmpeg 拉起 + 两帧交付耗时；
  同步路径的点击卡顿与后台预热的耗时主体都在这。
- ``webm.decode``：reader 线程每帧「解码 + 管道交付」间隔（不含入队阻塞）。
- ``webm.queue_wait``：reader 入队阻塞耗时；节流路径 = 背压阻塞重试，
  非节流路径含超时丢帧尝试。
- ``webm.queue_drop``：非节流路径队列满丢帧计数（节流路径绝不丢帧）。
- ``webm.poll_empty``：消费端空转计数（取帧队列空：解码未跟上/未开始）。
- ``webm.consume``：主线程消费转换 RGBA→QImage→QPixmap 耗时。
- ``rebuild.calls``：_rebuild_frame 实际进入次数（movie 非空即计）。
- ``rebuild.skip``：快路径跳过计数（同 movie 同帧同 key，整条链未执行）。
- ``rebuild.total``：帧重建整条路径耗时（skip/命中/未命中成功路径都计）。
- ``frame_cache.hit`` / ``frame_cache.miss``：预缩放缓存命中/未命中计数，
  命中率 = hit/(hit+miss)，可从快照直接算出。
- ``rebuild.scale``：未命中路径 CPU 转换链耗时（toImage→镜像→预乘→
  Smooth 缩放→ARGB32→fromImage）。
- ``rebuild.mask``：_sync_mask 掩码生成耗时（canvas 绘制 + createAlphaMask
  + QRegion）。
- ``paint.draw``：paintEvent 全段耗时（含 slingshot/squash 附加绘制）。

零开销论证（默认关闭）：
- 产品代码里的每个打点形如 ``if perfstats.ENABLED: ...``：关闭时每帧
  只多一次模块级 bool 属性读取 + 一次条件跳转（LOAD_GLOBAL /
  LOAD_ATTR / POP_JUMP_IF_FALSE），不创建任何对象、不调用计时 /
  记账函数、不改变控制流 —— 满足「不打点时不得引入每帧分配」；
- 只有启用后才进入 ``clock()`` 计时与 dict 记账（观测状态下的开销也
  只是每帧几次 ``time.perf_counter`` 调用与定长对象更新，远低于被测的
  毫秒级解码 / 画面重建成本）。

线程：记账不加锁 —— CPython GIL 下 dict 读改写近似原子；reader /
解码线程与 GUI 线程并发打点可能丢最后一次累加，观测用途可接受。
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import time as _time

logger = logging.getLogger("pet.perfstats")

# 模块级开关：打点处直接读 perfstats.ENABLED（关闭时 = 一次属性读取 +
# 一次跳转，零分配零调用）。enable()/disable() 原子替换该 bool。
ENABLED = False

_OUTPUT_FILE: str | None = None
_atexit_registered = False


class _Stat:
    """单指标累计：count = 次数，total = 累计值（计时打点为秒）。"""

    __slots__ = ("count", "total")

    def __init__(self) -> None:
        self.count = 0
        self.total = 0.0


_stats: dict[str, _Stat] = {}


def enable() -> None:
    """开启打点（幂等）。"""
    global ENABLED
    ENABLED = True
    _maybe_register_atexit()


def disable() -> None:
    """关闭打点（幂等）：之后所有打点只付一次布尔检查。"""
    global ENABLED
    ENABLED = False


def set_output_file(path: str | None) -> None:
    """指定 dump() 的落盘路径；None = 走 logging。"""
    global _OUTPUT_FILE
    _OUTPUT_FILE = path


def reset() -> None:
    """清空已累计的计数与耗时（保留开关状态）。"""
    _stats.clear()


def clock() -> float:
    """观测用时钟（秒）；只应在 ENABLED 时调用。"""
    return _time.perf_counter()


def note(name: str, amount: int = 1) -> None:
    """累加计数器（默认 +1）。关闭时为 no-op（打点处应先用 ENABLED 守卫，
    此处再兜底一次，保证直接调用也安全）。"""
    if not ENABLED:
        return
    stat = _stats.get(name)
    if stat is None:
        stat = _stats[name] = _Stat()
    stat.count += amount


def time(name: str, seconds: float) -> None:
    """累加一次耗时（秒）：count+1、total+=seconds。关闭时为 no-op（同上）。"""
    if not ENABLED:
        return
    stat = _stats.get(name)
    if stat is None:
        stat = _stats[name] = _Stat()
    stat.count += 1
    stat.total += seconds


def snapshot() -> dict:
    """当前全部指标快照：{名字: {count, total}}，total 为调用方传入单位
    （计时打点为秒）。"""
    return {
        name: {"count": stat.count, "total": stat.total}
        for name, stat in _stats.items()
    }


def dump() -> dict:
    """打一份快照：落盘（PET_PERF_STATS_FILE / set_output_file）或日志。

    返回快照本身，调用方/测试可直接断言。文件格式：{name: {count, total}}。
    """
    snap = snapshot()
    if _OUTPUT_FILE:
        try:
            with open(_OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump(snap, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            logger.warning("perfstats dump 落盘失败 %s: %s", _OUTPUT_FILE, exc)
            _log_snapshot(snap)
    else:
        _log_snapshot(snap)
    return snap


def _log_snapshot(snap: dict) -> None:
    for name, stat in sorted(snap.items()):
        count = stat["count"]
        total = stat["total"]
        if count > 0 and total:
            logger.info(
                "perfstats %s: count=%d total=%.6fs avg=%.3fms",
                name, count, total, total / count * 1000.0,
            )
        else:
            logger.info("perfstats %s: count=%d", name, count)


def _maybe_register_atexit() -> None:
    global _atexit_registered
    if not _atexit_registered:
        _atexit_registered = True
        atexit.register(dump)


def _configure_from_env() -> None:
    """进程启动时按环境变量初始化（模块导入时调用一次）。

    显式导出的 enable()/disable()/set_output_file() 供运行期切换，
    测试环境不设 PET_PERF_STATS，保持默认关闭的零开销基线。
    """
    global _OUTPUT_FILE
    if os.environ.get("PET_PERF_STATS"):
        _OUTPUT_FILE = os.environ.get("PET_PERF_STATS_FILE")
        enable()


_configure_from_env()
