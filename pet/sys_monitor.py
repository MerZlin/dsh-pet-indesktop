# -*- coding: utf-8 -*-
"""灵动岛「设备槽」（CPU / 内存）与 GPU 段的**共享**采样核心（零 Qt，依赖全部可注入）。

一条后台守护线程喂三个段：CPU 与内存的读数来自同一份 psutil 快照，GPU 的读数来自
``pet/gpu_monitor.GpuReader``（Windows PDH）。拆成多条线程只是多几份 syscall 与竞态，
所以 GPU 也走这一条线程——**不另起常驻 GPU 线程**。

- **两套订阅，各自记账**。设备槽用 :meth:`SysMonitor.subscribe`，GPU 用
  :meth:`SysMonitor.subscribe_gpu`；谁有订阅采谁，互不牵连（CPU-only 不会去建 PDH，
  GPU-only 不会去读 psutil）。
- **GPU 读数器按 worker 代创建**：只有存在 GPU 订阅的那一拍才调工厂，且**只在本线程**
  建 / 读 / 关。``GpuReader`` 的 PDH query 绑定创建线程，跨线程关句柄比不关更危险，
  所以调用方线程（GUI）一次都不碰它：``stop()`` / ``close()`` 也不替它收口，join 超时
  只是放弃等待，句柄由那个线程自己收。
- **订阅归零即停采**：没有 GPU 订阅的那一拍里由 worker 收口读数器（没人看就别占着
  句柄）；重新订阅是**新读数器**，第一拍按 ``sampling`` 重新建差分基线（短停同样重建，
  不拿一段旧区间的窗口平均冒充"此刻"）。
- **退订拒收在途结果**：订阅集合换代，退订前开始的那一拍回来时按旧代次丢弃。

其余取舍：

- **绝不在 GUI 线程采样**。先例见 ``pet/net_monitor.py``：psutil 单次调用本机
  实测 **23ms**（63 张网卡的表遍历）；CPU + 内存两次调用同理在几十毫秒量级，
  塞进 16ms 的动画帧预算就是把界面卡成幻灯片。GUI 侧只读一次快照。
- **1Hz 上限，不可配**。CPU/内存占用是秒级趋势量，更快的刷新只烧 CPU；且
  ``psutil.cpu_percent(interval=None)`` 的差分区间越短噪声越大（一个采样间隔
  内的抖动会被放大成满量程跳变）。所以 :func:`clamp_interval` 把下界钉在
  1.0s，``set_interval`` 只能放慢。
- **无订阅不采样**。段被禁用 / 岛不可见时订阅数归零，线程只空转计时，
  不产生任何 syscall——用户关掉指标段就该真的零开销。
- **诚实降级**。psutil 缺失或平台无此计数器时抛 :class:`PsutilUnavailable`
  （不是返回 0），采样线程把它收成 ``state="error"`` 的快照并带原因，
  段显示 ``--``、卡片写明为什么——``--`` 不伪造 0。
- **差分首样本为 None**。``cpu_percent(interval=None)`` 首次调用没有参照区间
  （恒 0），:class:`CpuMeter` 吃掉这一轮并报 None，绝不把 0 当"CPU 空闲"报出去。

线程纪律：``stop()`` 默认只置停止事件；``close()`` 做**有界** join（在途一次
采样 ~23ms，1s 预算足够；读数卡死时宁可放弃 join 也不吊死调用方）。**GPU 读数器不算
在"可放弃"的东西里**：它的句柄由采样线程自己收口，调用方永远不替它关。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from pet import gpu_monitor
from pet.island import segments as island_segments
from pet.island.segments import STATE_ERROR, STATE_OK

logger = logging.getLogger(__name__)

#: 采样间隔区间：下界 = 1Hz 硬上限（CPU/内存是秒级趋势量，不可配更快）。
MIN_INTERVAL_SECONDS = 1.0
MAX_INTERVAL_SECONDS = 60.0
DEFAULT_INTERVAL_SECONDS = 1.0
#: 最坏宽度样本：3 位数字 + 百分号（段的盒宽由它声明，见 ``island_segments``）。
MAX_TEXT = "888%"
#: 无读数时的占位文案（与 ``island_segments.UNKNOWN_TEXT`` 同一口径）。
UNKNOWN_TEXT = "--"

#: 采样线程名（收口断言与线程枚举用）。
THREAD_NAME = "pet-sys-monitor"


class PsutilUnavailable(RuntimeError):
    """psutil 不可用（未安装 / 导入失败）——读数不可用，不是 0。"""


def clamp_interval(value: Any) -> float:
    """``SysMonitor`` 的间隔口径：夹在 [1.0, 60.0] 秒，坏值回默认。

    1.0s 是**硬上限**（1Hz）：传更快只会被夹回来，这是不可配的规格约束。
    """
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_INTERVAL_SECONDS
    if number != number:  # NaN
        return DEFAULT_INTERVAL_SECONDS
    return max(MIN_INTERVAL_SECONDS, min(MAX_INTERVAL_SECONDS, number))


def _psutil():
    """惰性取 psutil 模块；不可用即抛（调用方据此报"读数不可用"，不吞成 0）。"""
    try:
        import psutil
    except Exception as exc:  # pragma: no cover - 环境缺 psutil 时走这里
        raise PsutilUnavailable(f"psutil 不可用：{exc}") from exc
    return psutil


def read_cpu_percent() -> float:
    """系统级 CPU 占用百分比（``interval=None`` 差分口径）。

    **只许在采样线程里调用**（单次调用毫秒级，见模块 docstring）。首调没有
    参照区间，所以调用方必须经 :class:`CpuMeter` 走——直接用它会把首调的 0
    当成"CPU 空闲"。
    """
    return float(_psutil().cpu_percent(interval=None))


def read_mem_percent() -> float:
    """物理内存占用百分比。同上，只许在采样线程里调用。"""
    return float(_psutil().virtual_memory().percent)


def format_cpu(percent: Any) -> str:
    """CPU 占用 → ``"24%"``；无读数 / 非数字 → ``"--"``。"""
    return _format_percent(percent)


def format_mem(percent: Any) -> str:
    """内存占用 → ``"61%"``；无读数 / 非数字 → ``"--"``。"""
    return _format_percent(percent)


def _format_percent(percent: Any) -> str:
    """百分比取整成字段盒文案。取整是刻意的：小数会让盒内数字每秒抖动，
    而行内宽度由字段盒兜住（超宽由 :func:`island_segments.format_field_box` 截断）。"""
    if percent is None:
        return UNKNOWN_TEXT
    try:
        number = float(percent)
    except (TypeError, ValueError):
        return UNKNOWN_TEXT
    if number != number:  # NaN
        return UNKNOWN_TEXT
    return f"{number:.0f}%"


class CpuMeter:
    """``psutil.cpu_percent(interval=None)`` 的差分语义封装。

    首次成功读数只建立基线（psutil 自己那一轮没有参照区间，值恒 0），报 ``None``
    而不是伪造 0。**基线在"读数成功"之后才算建立**：首轮抛错时 psutil 并未消费
    它的基线，下一轮仍要再吃一次——否则会把 psutil 的第二轮前那个 0 当真实占用。
    """

    def __init__(self, reader=read_cpu_percent):
        self._reader = reader
        self._primed = False

    @property
    def primed(self) -> bool:
        return self._primed

    def reset(self) -> None:
        """重新建立基线（线程换代后用：新代的第一轮仍是 None）。"""
        self._primed = False

    def sample(self) -> float | None:
        value = self._reader()
        if self._primed:
            return value
        self._primed = True
        return None


# ---------------------------------------------------------------- GPU 接线
def default_gpu_reader_factory() -> gpu_monitor.GpuReader:
    """默认 GPU 读数器工厂：**每次调用新建一个** :class:`gpu_monitor.GpuReader`。

    工厂是"能不能采 GPU"的注入缝（单测替身、平台裁剪都从这里进）。**调用了工厂才算
    开始采 GPU**：构造 ``GpuReader`` 本身零系统调用（``pdh.dll`` 在第一次 ``read`` 才
    装载），所以"没有订阅就没有 PDH"这条纪律落在"根本不调工厂"上，而不是落在
    "先建一个空读数器放着"上。
    """
    return gpu_monitor.GpuReader()


def _gpu_error_sample(reason: str, now: float) -> gpu_monitor.GpuSample:
    """接线层的退化样本（工厂失败 / 读数器抛异常 / 返回了不是样本的东西）。

    **借用** ``gpu_monitor`` 的样本类型而不是另造一个：呈现层只认 ``GpuSample``，
    "为什么没有读数"就写在它自己的 ``state``/``error_reason``/``detail`` 里。``detail``
    的键与 ``gpu_monitor`` 的退化样本逐键一致（``tests/test_sys_monitor_gpu.py`` 拿真
    读数器的一条真退化样本对拍键集，漂移会当场红）。
    """
    return gpu_monitor.GpuSample(
        percent=None, sampled_at=now, state=STATE_ERROR, error_reason=reason,
        detail={"unit_full": gpu_monitor.UNIT_FULL, "source": gpu_monitor.SOURCE_TEXT,
                "detail": "", "error_reason": reason},
    )


class _GpuSession:
    """一代 worker 与它的 GPU 读数器：**建 / 读 / 关都在同一个线程**里。

    ``GpuReader`` 的 PDH query 绑定创建线程（跨线程关句柄比不关更危险），所以这个
    会话只在采样线程里使用，也只在采样线程里 :meth:`close`——调用方线程（GUI）
    一次都不碰它，``SysMonitor.close()`` 也不替它收口。
    """

    __slots__ = ("epoch", "reader", "release_error")

    def __init__(self, epoch: int, reader):
        #: 建这个读数器时的订阅代次（订阅集合一变，本会话就作废）。
        self.epoch = epoch
        self.reader = reader
        #: 上一次收口失败的原因（成功/没失败过是 ``""``）：有界诊断，见 :meth:`close`。
        self.release_error = ""

    def close(self) -> bool:
        """收口读数器；返回**句柄是不是真的放掉了**。

        读数器的 ``close()`` 可能返回 ``False``（原生 ``PdhCloseQuery`` 失败 = 句柄
        状态未知，见 ``gpu_monitor.GpuReader.close``），那时候：

        - **引用不清**（``self.reader`` 留着）：句柄没被确认收掉，"忘掉它"等于把
          可能还有效的句柄丢进没人管的地方；留下它，owner 线程的下一次收口还能再试；
        - **owner 明确再试一次**：本方法只可能被 owner（采样线程）调用，所以这一跳
          是"owner 的第二次尝试"，**至多一次**——不是内部重试循环，失败就停；
        - **仍然失败**：把原因记进 :attr:`release_error`（有界：截到段契约的 detail
          上限）并如实返回 ``False``。**不**等待、**不**重试第三次、**不**声称成功：
          句柄状态未知这件事只有日志和这个字符串能说，谁都不许把它翻译成"已释放"。

        读数器没有 ``close``（替身/异常对象）时如实返回 ``False``：收不掉就是收不掉。
        """
        reader = self.reader
        if reader is None:
            return True
        closer = getattr(reader, "close", None)
        if not callable(closer):
            self.release_error = f"{type(reader).__name__} 没有 close()，无法收口"
            logger.warning("设备槽：GPU 读数器 %s", self.release_error)
            return False
        for attempt in (1, 2):
            try:
                if closer():
                    self.reader = None
                    self.release_error = ""
                    return True
            except Exception:
                logger.debug("设备槽：GPU 读数器收口异常", exc_info=True)
            if attempt == 1:
                # owner 的明确第二次尝试（恰好一次）；失败与否都走上面的同一路径。
                reason = str(getattr(reader, "release_error", "") or "")
                logger.warning(
                    "设备槽：GPU 读数器第一次收口未成功（%s），owner 线程再试一次",
                    reason or "原因未提供")
        self.release_error = _bounded_release_error(reader)
        logger.warning(
            "设备槽：GPU 读数器收口仍然失败（%s）——句柄状态未知，"
            "本代不再重试、也不替它强关（收口由 owner 线程负责）", self.release_error)
        return False


def _bounded_release_error(reader) -> str:
    """读数器给出的释放失败原因 → 有界诊断串（读不到就如实说读不到，不编）。"""
    reason = str(getattr(reader, "release_error", "") or "").strip()
    if not reason:
        reason = f"{type(reader).__name__}.close() 返回 False 且未给出原因"
    return reason[:island_segments.MAX_DETAIL_CHARS]


# ---------------------------------------------------------------- 快照
@dataclass(frozen=True)
class SysSample:
    """一个采样周期的结果快照（GUI 线程只读它，不做任何 syscall）。

    ``state`` 用段契约的词表（``ok``/``error``）：``error`` 时两个读数字段都是
    None，``error_reason`` 带原因（卡片直接拿去显示）。``sampled_at`` 是采样
    时刻（``time.monotonic``）。
    """

    cpu_percent: float | None
    mem_percent: float | None
    sampled_at: float
    state: str = STATE_OK
    error_reason: str = ""


# ---------------------------------------------------------------- 采样线程
class SysMonitor:
    """后台守护线程：每 ``interval`` 秒读一次 CPU + 内存，只对订阅者说话。

    生命周期：调用方（灵动岛）在岛可见时 :meth:`start`、隐藏时 :meth:`stop`，
    段自己按显示与否 :meth:`subscribe` / :meth:`unsubscribe`——**订阅数为 0 时
    线程只空转，不产生任何 syscall**（关掉指标段就该真的零开销）。

    **代（generation）隔离**：与 ``pet/net_monitor.py`` 同一套纪律。``stop()``
    之后立刻 ``start()`` 时上一轮线程可能还卡在读数里，所以每轮自带 generation
    token 与独立的 :class:`CpuMeter`：旧代晚到的结果被丢弃，不会用更晚的时间戳
    盖掉新代快照；新一代第一轮也重新建基线（宁可少报一轮，不报错值）。

    **GPU 也走这一条线程**（不另起常驻 GPU 线程）：读数器按代创建，只有存在 GPU 订阅
    的那一拍才调 ``gpu_reader_factory``，且只在本线程建 / 读 / 关。
    """

    def __init__(
        self,
        interval: float = DEFAULT_INTERVAL_SECONDS,
        *,
        cpu_reader=read_cpu_percent,
        mem_reader=read_mem_percent,
        clock=time.monotonic,
        on_sample=None,
        gpu_reader_factory=default_gpu_reader_factory,
        on_gpu_sample=None,
    ):
        self._interval = clamp_interval(interval)
        self._clock = clock
        self._cpu_reader = cpu_reader
        self._mem_reader = mem_reader
        self._on_sample = on_sample
        self._gpu_reader_factory = gpu_reader_factory
        self._on_gpu_sample = on_gpu_sample
        self._lock = threading.Lock()
        self._snapshot: SysSample | None = None
        self._subscribers: set[str] = set()
        self._gpu_sample: gpu_monitor.GpuSample | None = None
        self._gpu_subscribers: set[str] = set()
        #: GPU 订阅集合的**代次**：集合一变就 +1。在途那一拍拿的是旧代次，回来时按它
        #: 拒收——退订后不该再看到它的新读数，重新订阅也必须从新序列的第一拍重建基线。
        self._gpu_epoch = 0
        self._generation, self._meter = self._new_generation()
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None

    # ------------------------------------------------------------ 状态
    @property
    def interval(self) -> float:
        return self._interval

    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    @property
    def gpu_subscriber_count(self) -> int:
        """GPU 段的订阅数（与 :attr:`subscriber_count` **各自记账**，互不影响）。"""
        with self._lock:
            return len(self._gpu_subscribers)

    def snapshot(self) -> SysSample | None:
        """最近一次采样（还没跑完第一轮时返回 None）。线程安全。"""
        with self._lock:
            return self._snapshot

    def gpu_snapshot(self) -> gpu_monitor.GpuSample | None:
        """最近一次 GPU 采样（没采过时返回 None）。线程安全。

        返回的就是读数器给的那个 :class:`gpu_monitor.GpuSample`——``state`` /
        ``error_reason`` / ``detail`` / ``sampled_at``（monotonic）原样保留，呈现层要的
        "为什么没有读数"就在样本里，宿主不再自己攒第二份状态。**读快照不是采样的许可**：
        它不建读数器、不产生任何系统调用。
        """
        with self._lock:
            return self._gpu_sample

    def set_interval(self, interval: float) -> float:
        """运行中改采样间隔（即时生效，不重建线程）；返回值是夹过界的实际值。"""
        self._interval = clamp_interval(interval)
        return self._interval

    # ------------------------------------------------------------ 订阅
    def subscribe(self, key: str) -> int:
        """按稳定 key 订阅（段 id 就是天然的 key）；返回订阅后的计数。

        同 key 重复订阅不重复计数——否则段重建（值刷新导致的快照重建）会把
        计数越堆越高，退订一次也归不了零。"""
        with self._lock:
            self._subscribers.add(str(key))
            return len(self._subscribers)

    def unsubscribe(self, key: str) -> int:
        """退订；未订阅的 key 是 no-op（不抛、不把计数压成负数）。"""
        with self._lock:
            self._subscribers.discard(str(key))
            return len(self._subscribers)

    def subscribe_gpu(self, key: str) -> int:
        """按稳定 key 订阅 GPU 段；返回订阅后的计数。

        与 :meth:`subscribe` 同一口径：同 key 重复订阅不重复计数（宿主每次值刷新都会
        重放订阅，不幂等就会越堆越高、退订一次归不了零）。**集合真的变了才换代次**：
        重复订阅不换代，也就不会把在途那一拍判成旧代，更不会把读数器换掉。
        """
        with self._lock:
            before = len(self._gpu_subscribers)
            self._gpu_subscribers.add(str(key))
            if len(self._gpu_subscribers) != before:
                self._gpu_epoch += 1
            return len(self._gpu_subscribers)

    def unsubscribe_gpu(self, key: str) -> int:
        """退订 GPU 段；未订阅的 key 是 no-op。

        集合一变就换代次：在途那一拍回来时按旧代次被拒收（用户停掉的段不该在之后
        收到它的新读数），且"订阅归零"会让采样线程在收尾时把读数器收口。
        """
        with self._lock:
            before = len(self._gpu_subscribers)
            self._gpu_subscribers.discard(str(key))
            if len(self._gpu_subscribers) != before:
                self._gpu_epoch += 1
            return len(self._gpu_subscribers)

    # ------------------------------------------------------------ 采样
    def _new_generation(self) -> tuple[object, CpuMeter]:
        """开一代新采样：token（身份）+ 独立 meter（基线不跨代）。"""
        return object(), CpuMeter(reader=self._cpu_reader)

    def _retire_generation(self) -> None:
        """换代：在途的旧代结果从此不再被接受（stop 时也走这里）。"""
        self._generation, self._meter = self._new_generation()

    def sample_once(self, now: float | None = None) -> SysSample:
        """同步采样一轮 CPU/内存（当前代；线程循环与测试共用同一实现）。

        显式调用不查订阅数——调用方既然主动问，就给它一个样本。**只管设备槽**：
        GPU 读数器绑定"创建它的线程"，而本方法可能被 GUI 线程调用——在这里顺手采 GPU
        就等于在 GUI 线程里建 PDH query（线程归属 + 进程内第一次 read 的 0.54s provider
        装载都会落在界面线程上）。GPU 只在采样
        线程里按订阅采，快照走 :meth:`gpu_snapshot`。
        """
        return self._sample(self._generation, self._meter, now)

    def _sample(self, generation, meter: CpuMeter, now: float | None = None) -> SysSample:
        moment = self._clock() if now is None else float(now)
        try:
            cpu = meter.sample()
            sample = SysSample(
                cpu_percent=None if cpu is None else float(cpu),
                mem_percent=float(self._mem_reader()),
                sampled_at=moment,
                state=STATE_OK,
            )
        except Exception as exc:
            # 读数不可用 / 调用失败：收成 error 快照（而不是抛穿线程或报 0）。
            # 半份读数（CPU 有、内存没）不留：状态是 error，两个值都按"没有"算。
            logger.debug("设备槽：采样失败", exc_info=True)
            sample = SysSample(
                cpu_percent=None,
                mem_percent=None,
                sampled_at=moment,
                state=STATE_ERROR,
                error_reason=f"{type(exc).__name__}: {exc}",
            )
        self._publish(generation, sample)
        return sample

    def _publish(self, generation, sample: SysSample) -> bool:
        """发布一次采样；**只认当前代**（旧代晚到的结果直接丢弃）。"""
        with self._lock:
            if self._generation is not generation:
                return False
            self._snapshot = sample
        if callable(self._on_sample):
            try:
                self._on_sample(sample)
            except Exception:  # pragma: no cover - 回调是宿主注入面，异常不外溢
                logger.debug("设备槽：采样回调异常", exc_info=True)
        return True

    # ------------------------------------------------------------ GPU 采样
    def _gpu_tick(self, generation, session: _GpuSession | None) -> _GpuSession | None:
        """一拍 GPU：按订阅决定**建 / 采 / 关**，返回本代（可能新建）的会话。

        - **无订阅即停采**：订阅归零的那一拍，由 worker（也是唯一的 owner）把读数器
          收口——"没人看就别采"包含"别占着 PDH 句柄"。句柄留着不 collect 也能省掉
          重建，但"留着"意味着"随时准备采"，与本节口径不符；重建本身是常数级小成本
          （实测：``pdh.dll`` / provider 装载约 0.54s 只在**进程内第一次** read 上付，
          此后新 query 的第一拍 read 约 1ms），真正的代价是恢复后第一拍报 ``sampling``
          ——读者多等一拍 ``--``，而不是等半秒。
        - **重新订阅 = 新读数器 = 重新建基线**：旧读数器的基线属于上一段订阅，短停之后
          拿它的区间算出来的窗口平均不是"此刻"（长停同理，那一拍同样只建基线）。
        - **每拍最多关一次 + 建一次**：先关作废的、再建新的，不在同一拍里反复开关；
          读数器一旦建好就跨拍复用（重复订阅同一个 key 不会换代，也就不会重建）。
        - **收口失败如实收场**：:meth:`_GpuSession.close` 返回 ``False`` 表示句柄状态
          未知；这里记一条带原因的 warning 并把会话**退出**（不再重试、不无限等），
          下一拍该建就建——"这一代收口没成"是日志里的事实，不是可以咽下去的细节，
          也不是"没订阅却还在采"的许可（订阅账本才是建不建的唯一判据）。
        """
        with self._lock:
            epoch, subscribed = self._gpu_epoch, bool(self._gpu_subscribers)
        if session is not None and (not subscribed or session.epoch != epoch):
            if not session.close():
                logger.warning(
                    "设备槽：GPU 读数器本代收口未成功（%s）——句柄状态未知，"
                    "本代到此为止（不重试、不替它强关）", session.release_error)
            session = None
        if not subscribed:
            return None
        try:
            if session is None:
                session = _GpuSession(epoch, self._open_gpu_reader())
        except Exception as exc:
            # 工厂失败 = "这一拍没有读数器可用"这一类**可诊断**的失败面：出一个带原因的
            # error 样本（静默跳过在界面上就是"永远 -- 且说不出为什么"），也不崩线程。
            # 读数器没建成，本代没有会话要收口，下一拍再试一次（1Hz，不是热循环）。
            logger.debug("设备槽：GPU 读数器创建失败", exc_info=True)
            try:
                self._publish_gpu(generation, epoch, _gpu_error_sample(
                    f"读数器创建失败 {type(exc).__name__}: {exc}", self._clock()))
            except Exception:  # pragma: no cover - 注入的时钟炸了才会到这里
                logger.exception("设备槽：GPU 创建失败样本未发布")
            return None
        try:
            self._publish_gpu(generation, epoch, self._read_gpu(session))
        except Exception:  # pragma: no cover - 兜底：注入的时钟炸了之类的意外
            # 无论如何都要把本代刚建的会话交回调用方（它的 finally 收口）：
            # 句柄丢在没人管的角落里，比这一拍没有读数更坏。
            logger.exception("设备槽：GPU 采样异常（线程继续）")
        return session

    def _open_gpu_reader(self):
        """调工厂建一个读数器；失败即抛（由 :meth:`_gpu_tick` 收成 error 样本）。

        只做形状校验：拿不到 ``read()`` 的东西（None、工厂返回错对象）在这里就挡下，
        不让它在采集路径上以 AttributeError 的形式出现。
        """
        reader = self._gpu_reader_factory()
        if not callable(getattr(reader, "read", None)):
            raise TypeError(f"读数器工厂返回的 {type(reader).__name__} 没有 read()")
        return reader

    def _read_gpu(self, session: _GpuSession) -> gpu_monitor.GpuSample:
        """读一拍：**只读那一拍**，任何失败都收成可诊断的 error 样本（不抛穿线程）。"""
        now = self._clock()
        try:
            sample = session.reader.read()
        except Exception as exc:
            logger.debug("设备槽：GPU 读数失败", exc_info=True)
            return _gpu_error_sample(f"读数器抛异常 {type(exc).__name__}: {exc}", now)
        if not isinstance(sample, gpu_monitor.GpuSample):
            return _gpu_error_sample(
                f"读数器返回了 {type(sample).__name__}，不是 GpuSample", now)
        return sample

    def _publish_gpu(self, generation, epoch: int, sample: gpu_monitor.GpuSample) -> bool:
        """发布一拍 GPU 读数；**只认当前代 + 当前订阅代次**（两道门都要过）。

        线程换代（stop→start）后旧代晚到的样本不许盖新代快照；订阅集合换过代次
        （退订 / 重新订阅前后的那一拍）的样本同样不许发布——退订即拒收在途结果，
        重新订阅从新序列的第一拍重新开始。回调在锁外调用（回调里再读快照不自锁死）。
        """
        with self._lock:
            if self._generation is not generation or self._gpu_epoch != epoch:
                return False
            if not self._gpu_subscribers:
                return False
            self._gpu_sample = sample
        if callable(self._on_gpu_sample):
            try:
                self._on_gpu_sample(sample)
            except Exception:  # pragma: no cover - 回调是宿主注入面，异常不外溢
                logger.debug("设备槽：GPU 采样回调异常", exc_info=True)
        return True

    def _tick(self, generation, meter: CpuMeter, session: _GpuSession | None,
              ) -> _GpuSession | None:
        """一拍：设备槽与 GPU 各按**自己的**订阅独立采（谁有订阅采谁，互不牵连）。"""
        if self.subscriber_count > 0:  # 无订阅不采样：关掉的段零 syscall
            self._sample(generation, meter)
        return self._gpu_tick(generation, session)

    # ------------------------------------------------------------ 线程
    def start(self) -> bool:
        if self.running:
            return False
        # 每次 start 一份独立的停止事件 + 一代独立的 generation/meter：紧接着
        # stop→start（隐藏后马上显示）时，上一轮线程只认自己的事件（不会被新一轮
        # clear() 复活成第二条常驻线程），晚到的结果也会被 _publish 按代拒收。
        event = threading.Event()
        self._stop_event = event
        self._retire_generation()
        generation, meter = self._generation, self._meter
        self._thread = threading.Thread(
            target=self._run, args=(event, generation, meter),
            daemon=True, name=THREAD_NAME,
        )
        self._thread.start()
        return True

    def stop(self, *, wait: bool = False, timeout: float = 2.0) -> None:
        """置停止事件并换代；``wait=True`` 时**有界** join（测试 / 退出路径用）。"""
        event, self._stop_event = self._stop_event, None
        if event is not None:
            event.set()
        thread, self._thread = self._thread, None
        self._retire_generation()
        if wait and thread is not None:
            thread.join(timeout)

    def close(self) -> None:
        """停止线程并收口（有界 join：在途一次采样毫秒级，1s 预算足够）。

        读数卡死时宁可放弃 join（线程是 daemon，进程退出不受阻）也不吊死调用方。
        **不从调用方关 GPU 读数器**：它的句柄属于采样线程（``GpuReader`` 的 query 绑定
        创建线程，跨线程关比不关更危险），join 超时也只是放弃等待——句柄由那个线程在
        自己的 ``finally`` 里收口，哪怕它晚几秒。
        """
        self.stop(wait=True, timeout=1.0)

    def _run(self, stop: threading.Event, generation, meter: CpuMeter) -> None:
        # 本代自己的 GPU 会话：读数器只在本线程建 / 读 / 关。调用方线程（GUI）一次都不碰，
        # 包括 close() 的 join 超时路径——收口只在这里发生。
        session: _GpuSession | None = None
        try:
            while not stop.is_set() and self._stop_event is stop:
                started = self._clock()
                try:
                    session = self._tick(generation, meter, session)
                except Exception:  # pragma: no cover - _sample/_gpu_tick 已兜异常
                    logger.exception("设备槽：采样异常（线程继续）")
                sleep = max(0.05, self._interval - (self._clock() - started))
                if stop.wait(sleep):
                    break
        finally:
            if session is not None and not session.close():
                # 收口失败：句柄状态未知。**不**声称已释放、**不**在这里第二次尝试
                # （:meth:`_GpuSession.close` 已经给过 owner 一次明确重试），
                # 线程就此退出——它是 daemon，进程退出不受阻，也没有任何"等它关掉"
                # 的循环。留下的是一句能复现原因的 warning，不是一句假成功。
                logger.warning(
                    "设备槽：GPU 读数器收口未成功（%s）——采样线程退出，"
                    "句柄未确认释放（不声称已释放，也不无限等待）", session.release_error)
