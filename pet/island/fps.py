# -*- coding: utf-8 -*-
"""灵动岛「游戏帧率」段的接线层（零 Qt）：段状态机 + 进程级唯一的采集器宿主。

分工：**采集与解析在 ``pet/fps_monitor.py`` / ``pet/game_fps.py``**（子进程、
目标解析、CSV、统计），本模块只管岛这一侧的三件事：

1. **段状态机**（:class:`FpsSlot`）：什么时候订阅、什么时候读快照、恢复时拒收旧
   读数、折叠/隐藏时把故障留在卡片上、四态怎么翻成 ``island_layout._InfoSeg``。
   与 ``gpu_monitor`` 的 GPU 段逐条同形，但**不重复实现**：岛侧只剩"按配置/可见性
   调 ``sync`` + 按 1Hz 调 ``poll``"两行接线（见 ``dynamic_island._sync_fps_slot``）。
2. **进程级唯一的采集器宿主**（:class:`FpsHub`）：多宠（多岛）共用**一个**
   ``FpsMonitor`` 与**一场**采集会话——每多开一只宠就多开一场 ETW 会话是错的。
   宿主按岛计数：第一个要看读数的岛起线程、最后一个不要的岛停线程；订阅只在第一个
   岛加入时建立、最后一个岛离开时撤销。
3. **呈现口径**（:func:`hint_parts` 等）：卡片上"在采谁 / Present 来源 / 可选上屏
   帧率"的文案只在这里拼一次；胶囊上仍然只有一个定宽数字（见 :data:`VALUE_SAMPLE`）。

三条硬纪律：

- **不虚 0**：``fps is None`` 一律画 ``--``（``island_segments.UNKNOWN_TEXT``）——
  "没有读数"与"读数就是 0"是两件事，后者照原样显示。
- **不混口径**：主值恒为**应用 Present 帧率**（``game_fps.format_fps`` 是唯一格式化
  出口）；上屏帧率只在卡片上下文里另起一句并注明"另一口径，不与主值混算"；**不登记
  任何告警阈值**——帧率高不是故障，套 GPU/CPU 的线是伪精确（``island_segments.alert_severity``
  对没阈值的段恒返回 ""）。
- **不占位**：这台机器没有这个能力（非 Windows / 内置采集程序不在）时，段不进段列表
  （胶囊连 ``--`` 都不画），只在卡片留一行原因——与 GPU 共用 ``_unavailable_segments``
  同一套机制。

线程纪律：本模块**不建线程也不起子进程**（那是 ``fps_monitor.FpsMonitor`` 的事）；
``sync``/``poll`` 全在 GUI 线程，收口一律走"停线程不 join"的口径（见 :meth:`FpsSlot.pause`），
所以隐藏/折叠灵动岛绝不会被采集链路的收口吊住。
"""
from __future__ import annotations

import itertools
import logging
import math
import threading
from dataclasses import dataclass
from typing import Any, Callable

from pet.island import fps_monitor, game_fps, segments as island_segments
from pet.island.layout import KIND_METRIC, _InfoSeg

logger = logging.getLogger(__name__)

#: 胶囊上的段标签（与 GPU 的 "GPU" 同规：只标段身份，读数在字段盒里）。
LABEL = "FPS"
#: 数值盒样本 = 后端声明的**最坏宽度**（``game_fps.MAX_TEXT``，4 位数字 + 溢出记号）。
#: 0 / 9 / 99 / 9999 / ``9999+`` 共用同一个盒宽，读数只影响盒内字形——这是"数字跳动
#: 不推邻居"的前提。样本从后端声明取而不是在岛侧抄一份：格式上限改了，盒宽必须跟着改
#: （后端把溢出从"夹紧成 9999"改成"写 9999+"时，这里自动跟上）。
VALUE_SAMPLE = str(game_fps.MAX_TEXT)
#: 卡片详情列的单位全称（读数口径写在最前面：不让人把 Present 帧率读成上屏帧率）。
UNIT_FULL = game_fps.UNIT_FULL
#: Present 采集来源（详情列放不下时挂在 tooltip / 无障碍描述上）。
SOURCE_TEXT = game_fps.SOURCE_TEXT
#: 没有任何原因可说时的兜底句。
NO_REASON = "帧率采集当前不可用"

#: 段 id 每一份进程内唯一（``id(self)`` 会被回收复用，重复的 key 会让宿主把"新岛"
#: 当成"老岛还挂着"——退订永远不发生，采集线程就永久留着）。
_KEY_SEQ = itertools.count(1)

#: 采集器快照状态 → 段状态。``""`` 与词表外的值一律按"采集中"兜底：``FpsMonitor`` 在
#: "已开始、已订阅、还没有第一拍"时给的正是空状态（见 ``fps_monitor._build_fallback``），
#: 按 unknown/error 呈现都会说谎，按"采集中"才是那一拍的真话。
_STATE_MAP = {
    game_fps.STATE_OK: island_segments.STATE_OK,
    game_fps.STATE_SAMPLING: island_segments.STATE_SAMPLING,
    game_fps.STATE_UNKNOWN: island_segments.STATE_UNKNOWN,
    game_fps.STATE_ERROR: island_segments.STATE_ERROR,
    game_fps.STATE_UNSUPPORTED: island_segments.STATE_UNSUPPORTED,
}
#: 非 ok 态在卡片详情列上的默认原因（快照自己带原因时优先用快照的）。
_STATE_REASONS = {
    island_segments.STATE_SAMPLING: "采集中",
    island_segments.STATE_UNKNOWN: "当前没有可采集的目标",
    island_segments.STATE_ERROR: "采样失败",
    island_segments.STATE_UNSUPPORTED: NO_REASON,
}


@dataclass(frozen=True)
class PollResult:
    """一拍读数的结果：内容要不要重画、**占位**有没有变（段增删要重算几何）。"""

    changed: bool = False
    occupying_changed: bool = False


_NO_RESULT = PollResult()


def _segment_state(sample: Any) -> str:
    """快照状态 → 段状态（词表外的值按"采集中"兜底，见 :data:`_STATE_MAP`）。"""
    state = str(getattr(sample, "state", "") or "")
    return _STATE_MAP.get(state, island_segments.STATE_SAMPLING)


def _has_reading(sample: Any) -> bool:
    """这份快照带不带**真读数**（屏障只对带读数的那一份生效，见 :meth:`FpsSlot._resume`）。"""
    return sample is not None and getattr(sample, "fps", None) is not None


def detail_of(sample: Any) -> dict:
    """快照的卡片素材（不是 dict 时给空表：快照是外部面，脏值不该掀翻绘制路径）。"""
    detail = getattr(sample, "detail", None) if sample is not None else None
    return detail if isinstance(detail, dict) else {}


def unit_full(sample: Any) -> str:
    """卡片里这一段的口径全称（读数器自己的声明优先，拿不到才用兜底串）。"""
    return str(detail_of(sample).get("unit_full") or "") or UNIT_FULL


def target_label(sample: Any) -> str:
    """卡片里的"在采谁"：进程名 + pid（读数的归属必须是具体进程，不写"某个游戏"）。"""
    if sample is None:
        return ""
    label = str(detail_of(sample).get("detail") or "").strip()
    if label:
        return label
    pid = getattr(sample, "target_pid", None)
    name = str(getattr(sample, "target_name", "") or "").strip()
    if pid:
        return f"{name} (pid {pid})" if name else f"pid {pid}"
    return "没有可采集的目标"


def mode_label(sample: Any) -> str:
    """采集目标的选定方式（"跟随前台程序" / "指定程序 game.exe"）——用户配的那一项。"""
    detail = detail_of(sample)
    mode = str(detail.get("mode") or "").strip()
    if not mode:
        return ""
    if mode == "process":
        name = str(detail.get("process") or "").strip()
        return f"指定程序 {name}" if name else "指定程序（未填程序名）"
    return "跟随前台程序"


def displayed_label(sample: Any) -> str:
    """上屏帧率（可选）：只在有这一路样本时给，并**注明是另一个口径**。

    两者不可混算、不可互相替代（上屏帧率是显示器真正刷出来的那一档，应用 Present
    帧率可能更高——比如无垂直同步的窗口化游戏）。它只出现在卡片上下文里，胶囊上
    永远只有主值。
    """
    value = getattr(sample, "displayed_fps", None) if sample is not None else None
    if value is None:
        return ""
    return f"上屏帧率 {game_fps.format_fps(value)}（另一口径，不与主值混算）"


def overflow_label(sample: Any) -> str:
    """读数超过呈现盒宽时的**明说**：段上写 ``9999+``（"至少这么多"），精确值在这里给。

    夹紧成一个看着精确的 ``9999`` 才是说谎（那可能是个 12000 的读数）；后端因此把
    溢出表达成"数字 + 记号"，而精确值照旧留在快照里——这一句就是把它读回来。
    """
    value = getattr(sample, "fps", None) if sample is not None else None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(number) or number <= game_fps.MAX_FPS:
        return ""
    return (f"实际帧率 {number:.0f}：段上只写 {game_fps.MAX_TEXT}"
            "（至少这么多），精确值不夹紧")


def hint_parts(sample: Any, *, reason: str = "", updated: str = "",
               ok: bool = False) -> tuple[str, ...]:
    """帧率行的完整上下文（tooltip / 无障碍描述读它）——**只在这里拼一次**。

    内容全部来自这一段的快照与后端声明：口径全称、目标进程、目标选定方式、可选上屏
    帧率、溢出说明、后端 stderr 原文、这一拍的原因、Present 采集来源、采样时刻。
    段名与值不重复进来（行上已经可见）。空项由宿主 ``_card_hint_text`` 自动省略。
    """
    if sample is None:
        return (UNIT_FULL, str(reason or "").strip() or NO_REASON, SOURCE_TEXT)
    return (
        unit_full(sample),
        target_label(sample),
        mode_label(sample),
        displayed_label(sample),
        overflow_label(sample),
        str(detail_of(sample).get("backend_stderr") or "").strip(),
        str(reason or "").strip(),
        str(detail_of(sample).get("source") or "") or SOURCE_TEXT,
        f"采样于 {updated}" if ok and updated else "",
    )


def _new_monitor():
    """建真实采集器（**测试替换点**：替身不起线程、不碰子进程）。

    会话工厂就是 :func:`fps_monitor.default_session`：采集**只有普通权限这一条路**
    （产品自己不提权，也没有第二个采集后端可选）。
    """
    return fps_monitor.FpsMonitor(session_factory=fps_monitor.default_session)


class FpsHub:
    """进程级唯一的采集器宿主（多宠 = 一个实例、一场采集会话）。

    宿主只做计数与实例管理，不解释读数：订阅按岛计（第一个岛加入才 ``subscribe``，
    最后一个岛离开才 ``unsubscribe``），线程闸门也按岛计（第一个要读数的岛 ``start``、
    最后一个不要的岛 ``stop``）。这样"两只宠都开着帧率段"只会有**一场** ETW 会话，
    而"一只隐藏了"也不会把另一只正在看的读数掐掉。

    实例跨"没人订阅"的窗口保留（不 close）：重新订阅时复用同一个对象，少一次
    "旧的还在收口、新的已经开跑"的重叠；真正的收口在 :meth:`close`
    （``reset_shared_hub`` / 测试用）。
    """

    def __init__(self, *, monitor_factory: Callable[[], Any] | None = None,
                 closed: bool = False):
        self._factory = monitor_factory or _new_monitor
        self._lock = threading.Lock()
        self._monitor: Any = None
        self._keys: set[str] = set()
        self._wanting: set[str] = set()
        self._target: tuple[str, str] | None = None
        self._created = 0
        #: 已收口：退出路径 ``close`` 之后**不再**接受登记。岛侧迟到的 sync（配置
        #: 变更 / 重新显示）不许在退出途中重开一场采集——那是"退出后残留"的另一条路。
        self._closed = bool(closed)

    @property
    def monitor(self):
        """共享采集器（一个岛都还没加入、或已收口时为 ``None``）。"""
        return self._monitor

    @property
    def closed(self) -> bool:
        """宿主是否已收口（收口后登记一律被拒，见 :meth:`attach`）。"""
        return self._closed

    @property
    def keys(self) -> frozenset[str]:
        """当前挂着的岛（订阅计数：空集 = 进程里没有任何岛在采帧率）。"""
        with self._lock:
            return frozenset(self._keys)

    def attach(self, key: str, *, mode: str, process: str):
        """登记一个岛（第一个岛才真的建采集器）并写入目标配置。

        **重复登记不重复下发**：岛侧每次配置/可见性同步都会调本方法，而"已登记 + 目标
        没变"是常态——那时不再往采集器上打一次 ``configure``/``subscribe``（两者各自
        会做一次"内置采集程序在不在"的文件检查）。真正变化的两件事各走一条：新岛才
        ``subscribe``，目标变了才 ``configure``（采集器据此换代重解析目标）。

        已收口（退出路径）时返回 ``None`` 且**什么都不建**：迟到的同步不许诈尸。
        """
        key = str(key)
        target = (str(mode or ""), str(process or ""))
        with self._lock:
            if self._closed:
                return None
            if self._monitor is None:
                self._monitor = self._factory()
                self._created += 1
                self._keys.clear()
                self._wanting.clear()
                self._target = None
            fresh = key not in self._keys
            self._keys.add(key)
            monitor = self._monitor
            target_changed = self._target != target
            self._target = target
        if fresh:
            monitor.configure(*target)
            monitor.subscribe(key)
        elif target_changed:
            monitor.configure(*target)
        return monitor

    def detach(self, key: str) -> None:
        """注销一个岛：订阅按岛退，最后一个岛走了采集线程自己停（**不 join**）。"""
        key = str(key)
        with self._lock:
            self._keys.discard(key)
            self._wanting.discard(key)
            monitor = self._monitor
        if monitor is None:
            return
        monitor.unsubscribe(key)

    def want(self, key: str, wanted: bool) -> bool:
        """某个岛要不要"现在就有读数"；返回采集线程是否**刚启动**（= 新序列）。"""
        key = str(key)
        with self._lock:
            monitor = self._monitor
            if monitor is None:
                return False
            before = bool(self._wanting)
            if wanted:
                self._wanting.add(key)
            else:
                self._wanting.discard(key)
            after = bool(self._wanting)
        if after and not before:
            return bool(monitor.start())
        if before and not after:
            monitor.stop()
        return False

    def close(self, *, terminate_session: bool = True) -> None:
        """收口整场采集（退出钩子 / 测试）：停线程 + 丢实例（幂等、有界 join）。

        ``terminate_session`` 原样透传给采集器：正常退出走官方终止命令（唯一能保证
        释放 ETW 会话的方式），关机/注销那条路径置假（不得再派生进程，见
        ``fps_monitor.FpsSession.stop``）。收口后宿主**不再接受登记**（见 :meth:`attach`）。
        """
        with self._lock:
            self._closed = True
            monitor, self._monitor = self._monitor, None
            self._keys.clear()
            self._wanting.clear()
        if monitor is None:
            return
        try:
            monitor.stop(terminate_session=terminate_session)
        except Exception:  # pragma: no cover - 替身/异常实例
            logger.debug("帧率采集：宿主停采集异常", exc_info=True)
        try:
            monitor.close(terminate_session=terminate_session)
        except Exception:  # pragma: no cover
            logger.debug("帧率采集：宿主收口异常", exc_info=True)


_HUB: FpsHub | None = None
_HUB_LOCK = threading.Lock()
#: 退出闸门：``reset_shared_hub`` 置位后，本进程**不再新建**采集宿主——迟到的岛侧同步
#: （显示回来 / 设置热改）不许在退出途中重开一场采集。生产路径只置位；测试用
#: :func:`_clear_exit_latch_for_tests` 显式复位（同 ``webm_clip._reset_session_ending_for_tests``
#: 的先例）。
_EXIT_LATCH = False


def shared_hub() -> FpsHub:
    """进程级共享的采集器宿主（惰性建；多宠共用一个实例）。

    惰性是为了"默认关闭零开销"：帧率段没开时**连宿主里的采集器都不会建**
    （见 :meth:`FpsHub.attach`），更不会起线程/子进程。已经过退出收口的进程里，
    这里给的是一个**已收口**的宿主：它拒收一切登记，"采集器诈尸"从这一层就堵死。
    """
    global _HUB
    with _HUB_LOCK:
        if _HUB is None:
            _HUB = FpsHub(closed=_EXIT_LATCH)
        return _HUB


def reset_shared_hub(*, terminate_session: bool = True) -> None:
    """丢掉共享宿主并**有界收口**（退出钩子/测试用）：采集线程与子进程一起停。

    ``terminate_session=False``（关机/注销路径）不透传官方终止命令，见
    :meth:`FpsHub.close`。收口同时给本进程置上退出闸门（见 :data:`_EXIT_LATCH`）。
    """
    global _HUB, _EXIT_LATCH
    with _HUB_LOCK:
        hub, _HUB = _HUB, None
        _EXIT_LATCH = True
    if hub is not None:
        hub.close(terminate_session=terminate_session)


def _clear_exit_latch_for_tests() -> None:
    """测试专用：清掉退出闸门，让共享宿主能重新建（生产路径只置位、绝不清）。"""
    global _EXIT_LATCH
    with _HUB_LOCK:
        _EXIT_LATCH = False


class FpsSlot:
    """一个岛窗口的帧率段：订阅/停采、快照闸门、四态与段对象（零 Qt）。

    岛侧的唯一驱动面是 :meth:`sync`（配置/可见性一变就调，幂等）+ :meth:`poll`
    （1Hz 读一次共享快照）+ :meth:`pause` / :meth:`release`（隐藏 / 销毁收口）。
    段对象（:meth:`segment`）与卡片素材（:func:`hint_parts`）也从这里出去，所以
    "四态怎么翻、什么时候拒收旧值"只有这一份实现。
    """

    def __init__(
        self,
        *,
        key: str | None = None,
        hub: Any = None,
        platform_supported: Callable[[], bool] | None = None,
    ):
        # 未显式给 key 时用进程内唯一序号：``id(self)`` 会被回收复用，重复的 key 会让
        # 宿主把新岛当成老岛还挂着（退订永不发生 = 采集线程永久留着）。
        self.key = str(key) if key else f"island-fps-{next(_KEY_SEQ)}"
        self._hub = hub
        self._hub_used: FpsHub | None = None
        self._platform_supported = platform_supported or game_fps.platform_support
        self._monitor: Any = None
        self._sample: Any = None
        #: 折叠/隐藏前最后一拍不是 ok 时的 (段状态, 原因)：卡片是唯一还看得见它的地方。
        self._hold: tuple[str, str] | None = None
        #: 恢复屏障：恢复那一刻手里那份**带读数**的快照对象（拒收一次，见 _resume）。
        self._barrier: Any = None
        self._wanted = False
        self._mode = "foreground"
        self._process = ""

    # ------------------------------------------------------------ 状态
    @property
    def sample(self):
        """最近一拍的读数（``None`` = 这一刻没有读数：没采过 / 被折 / 刚恢复）。"""
        return self._sample

    @property
    def monitor(self):
        """共享采集器（没在采时为 ``None``；取证与测试用）。"""
        return self._monitor

    @property
    def attached(self) -> bool:
        """这个岛当前是否挂在共享采集器上（订阅影子）。"""
        return self._monitor is not None

    @property
    def wanted(self) -> bool:
        """这个岛当前是否在要求"现在就有读数"（线程闸门）。"""
        return self._wanted

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def process(self) -> str:
        return self._process

    @property
    def sampled_at(self):
        """这一拍的采样时刻（卡片"更新时间"用；``None`` = 没有读数）。"""
        sample = self._sample
        return None if sample is None else getattr(sample, "sampled_at", None)

    # ------------------------------------------------------------ 生命周期
    def sync(self, *, enabled: bool, visible: bool, mode: str = "foreground",
             process: str = "") -> None:
        """按"段在不在 + 岛可不可见"驱动共享采集器（**幂等**，岛侧唯一入口）。

        - ``enabled``（段在列表里、没被折、不是细条、信息槽开着）为假：**退订收口**
          （看不见的段不该继续烧 ETW），并在收口前把最后一份非 ok 的原因留在卡片上。
        - ``visible``（岛窗口真的显示着）为假：停线程、**保留订阅**（显示回来按同一
          路径恢复，不用重开一场采集会话）。
        - 目标配置（跟随方式 / 指定程序名）跟着登记一起下发（见 :meth:`FpsHub.attach`：
          同一个目标不会重复下发），设置页改了就即时换代，正在采的目标会被换掉
          （旧代读数由采集器的代次闸门丢弃）。

        本方法**不做几何**：它会被 ``_row_plan`` 的路径调到（折叠同步），在那里重算
        几何就是递归。段增删的几何重算由 :meth:`poll` 的结果在宿主那一侧触发。
        """
        self._mode = str(mode or "")
        self._process = str(process or "")
        if not enabled or not self._platform_supported():
            self._detach()
            return
        monitor = self._attach()
        if monitor is None:
            # 宿主已收口（退出路径的 reset_shared_hub）：**不再重开一场采集**。
            # 退出途中迟到的可见性/配置同步（显示回来、设置热改）不许把采集器诈尸。
            self._detach()
            return
        if visible and not self._wanted:
            self._resume(monitor)
        elif not visible and self._wanted:
            self.pause()

    def pause(self) -> None:
        """岛隐藏：停采集线程、丢旧值，**保留订阅**（幂等）。

        与 :meth:`release` 分开是刻意的：隐藏只是"暂时看不见"，段还在列表里——订阅
        跟着断的话，显示回来就是一次全新的采集会话（PresentMon 起停的代价白付两遍）。
        收口**不 join**（``FpsMonitor.stop`` 默认不阻塞）：在 GUI 线程上等采集进程
        有界退出，会把"隐藏灵动岛"这件事卡住，而换代的 token 已经保证在途结果不会
        再被发布（见 ``fps_monitor`` 的线程纪律）。
        """
        self._keep_hold()
        self._sample = None
        self._barrier = None
        if not self._wanted:
            return
        self._wanted = False
        if self._hub_used is not None:
            self._hub_used.want(self.key, False)

    def release(self) -> None:
        """窗口销毁：停线程 + 退订 + 丢实例引用（幂等）。

        退订按岛计：还有别的岛在看读数时，这一个岛的销毁不会把采集链路带走。
        """
        self._detach()

    # ------------------------------------------------------------ 内部：挂/收
    def _attach(self):
        """向共享宿主登记（宿主负责"进程里只有一个采集器"）。"""
        if self._hub_used is None:
            self._hub_used = self._hub or shared_hub()
        self._monitor = self._hub_used.attach(
            self.key, mode=self._mode, process=self._process)
        return self._monitor

    def _resume(self, monitor) -> None:
        """恢复：清旧值 → 让宿主起线程 → 布恢复屏障。

        屏障只对**带读数**的快照生效（:func:`_has_reading`）：隐藏/折叠期间静默过期
        的那一份读数不许在恢复后冒充实时值——这是用户最容易看到的假象。而没有读数的
        快照（fallback / "刚起来还没第一拍"）不设屏障：挡住它只会让段永远停在 ``--``
        （"采集程序不在"这一类能力原因正是靠那份快照报出来的）。
        """
        self._sample = None
        self._hold = None
        self._barrier = None
        self._wanted = True
        if self._hub_used is not None:
            self._hub_used.want(self.key, True)
        current = monitor.snapshot()
        if _has_reading(current):
            self._barrier = current

    def _detach(self) -> None:
        """收口：停线程 + 退订 + 丢样本（最后一份非 ok 的读数留在保持表里）。"""
        self._keep_hold()
        self._sample = None
        self._barrier = None
        self._wanted = False
        hub, self._hub_used = self._hub_used, None
        self._monitor = None
        if hub is not None:
            hub.detach(self.key)

    def _keep_hold(self) -> None:
        """把"最后一份读数不是 ok"的原因留在卡片上（折叠/隐藏不埋没故障）。

        与 GPU 的 ``_gpu_error_hold`` 同一机制：被折进 +N 的段不再订阅，卡片是唯一
        还看得见它的地方——那一拍为什么没读数（权限不足 / 目标退出了）必须还能读到，
        否则"折一下就没了"就是静默吞故障。
        """
        sample = self._sample
        if sample is None:
            return
        state = _segment_state(sample)
        if state == island_segments.STATE_OK:
            self._hold = None
            return
        self._hold = (state, str(getattr(sample, "error_reason", "") or "").strip())

    # ------------------------------------------------------------ 读数
    def poll(self) -> PollResult:
        """读一次共享快照（宿主按 1Hz 调）：要不要重画、占位有没有变。

        拒收两类：① **屏障对象**（恢复前那份带读数的旧快照）；② 与上一拍**同一个
        对象**——采集器每发布一次给一个新对象，"还是那一个"精确等于"上一代/没变"，
        所以这一条同时兜住了"停在旧快照不复位"的情形。

        **刻意不按 ``generation`` 判决**：降级快照（能力不足 / 还没第一拍）的
        generation 恒为 0（它不属于任何一场采集会话），对代次做单调判决会把
        "这台机器没有这个能力"整个挡掉。旧值进不来靠的是另外三条：``stop()`` 清快照
        （旧读数对象根本不再从采集器出去）、恢复屏障、同对象即无变化。
        """
        monitor = self._monitor
        if monitor is None:
            return _NO_RESULT
        sample = monitor.snapshot()
        if sample is None or sample is self._barrier or sample is self._sample:
            return _NO_RESULT
        self._barrier = None
        occupying_before = bool(self.unavailable_reason())
        self._sample = sample
        return PollResult(
            changed=True,
            occupying_changed=occupying_before != bool(self.unavailable_reason()),
        )

    def unavailable_reason(self) -> str:
        """这台机器**没有这个能力**的原因（``""`` = 有能力）。

        两个来源，与 GPU 的动态判定同形：① 平台/架构闸门（非 Windows，或 Windows 上
        架构与随包的 x64 后端不匹配——数据源是 ETW 上的 Present 事件，ARM64 未验证）；
        ② 采集器回报的 ``unsupported``（内置采集程序不在这一类）。两种都让段**不占位**、
        只在卡片留一行原因。``sampling`` / ``unknown`` / ``error`` 一律不在这里：把
        "当前没有读数"说成"没有这个能力"才是谎话。
        """
        if not self._platform_supported():
            return self._platform_blocked_reason()
        sample = self._sample
        if sample is not None and _segment_state(sample) == island_segments.STATE_UNSUPPORTED:
            return str(getattr(sample, "error_reason", "") or "").strip() or NO_REASON
        return ""

    def _platform_blocked_reason(self) -> str:
        """平台/架构闸门的人话原因（非 Windows / ARM64 未验证 / 架构不匹配）。

        ``platform_supported`` 可注入（替身测试替换它），真实架构判定替换不了，所以
        注入版本说"没有能力"、真实原因却取不到时回落到非 Windows 那一句——替身用例的
        行为与本批之前逐字一致，不因新增架构判据而变。
        """
        return game_fps.platform_problem() or game_fps.PLATFORM_UNSUPPORTED_REASON

    def reason(self) -> str:
        """当前为什么没有读数（卡片详情列用）：快照自带原因优先，其次折叠前的保持。

        权限不足时给的就是采集链路自己那句原文（"access denied / Performance Log
        Users"这类）：不虚 0、不猜一个更好的说法——有权限的人照常出读数，没权限的人
        拿到的是能拿去搜的那句话。
        """
        return self._base_reason()

    def _base_reason(self) -> str:
        sample = self._sample
        if sample is not None:
            text = str(getattr(sample, "error_reason", "") or "").strip()
            state = _segment_state(sample)
            if text:
                return text
            if state != island_segments.STATE_OK:
                return _STATE_REASONS.get(state, "")
            return ""
        if self._hold is not None:
            return self._hold[1] or _STATE_REASONS.get(self._hold[0], "")
        return ""

    def hint_parts(self, *, reason: str | None = None, updated: str = "",
                   ok: bool = False) -> tuple[str, ...]:
        """卡片行的完整上下文（口径 / 目标 / Present 来源 / 可选上屏帧率 / 失败原文）。

        ``reason`` 省略时用这一拍自己的原因（快照自带原因优先，其次折叠前的保持）——
        调用方想说别的（如整行 note）就显式传。
        """
        return hint_parts(self._sample, reason=self.reason() if reason is None else reason,
                          updated=updated, ok=ok)

    def segment(self) -> _InfoSeg:
        """帧率段（四态兜底 + 声明盒宽）。

        唯一格式化出口是 ``game_fps.format_fps``（与卡片读同一个串，两处不会漂移）；
        ``box_text`` 恒为后端声明的最坏样本（4 位数字 + 溢出记号，见 :data:`VALUE_SAMPLE`），
        所以 0 → 60 → 144 → 9999 → ``9999+`` 一个像素都不动。
        ``severity`` 刻意不填：帧率没有普适阈值（:data:`island_segments.ALERT_THRESHOLDS`
        里没有它），套 GPU/CPU 的线是伪精确——不登记阈值就恒无色，这是判定链自己的结论。
        """
        state, value = self._reading()
        text = (game_fps.format_fps(value) if state == island_segments.STATE_OK
                else island_segments.UNKNOWN_TEXT)
        return _InfoSeg(kind=KIND_METRIC, seg_id=island_segments.FPS, label=LABEL,
                        text=text, box_text=VALUE_SAMPLE, state=state)

    def _reading(self) -> tuple[str, float | None]:
        """``(段状态, 主值)``；ok 态却没有读数一律降为"采集中"——不虚 0 的最后一道闸。"""
        sample = self._sample
        if sample is None:
            if self._hold is not None:
                return self._hold[0], None
            return island_segments.STATE_SAMPLING, None
        state = _segment_state(sample)
        value = getattr(sample, "fps", None)
        if state == island_segments.STATE_OK and value is None:
            state = island_segments.STATE_SAMPLING
        return state, (value if state == island_segments.STATE_OK else None)
