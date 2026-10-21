# -*- coding: utf-8 -*-
"""灵动岛「段」的契约层：注册表 + 宽度自测量 + 字段盒 + 四态兜底（零 Qt）。

段是胶囊上的一个信息单元（``CPU 24%``、``↓ 1.2M``），本模块只管**契约**，
不画一个像素：注册接口给第三方段用，其余是布局层的硬护栏。

五条设计取舍：

- **盒宽自测量，不写死 px**。段声明最坏宽度样本（``max_text``，如 ``"888%"``），
  ``horizontalAdvance(label) + 4(有 label 时) + horizontalAdvance(max_text)``
  就是它的声明盒宽，按 ``(id, font_key)`` 缓存。字体/DPI 一变指纹就变，
  自动重测——写死 px 在 HiDPI 与用户改字号时必然错位。
- **运行期截断护栏**：实测 ``text`` 超过声明盒宽时右省略到盒宽并记一条 warning。
  契约第一原则是「任何一段失控不得推歪整岛」——截断 + 日志让违规可见，
  比静默信任格式化器安全（第三方段是常态，不是异常）。
- **四态兜底呈现**：``ok`` 才有真读数；``sampling``/``unknown``/``error`` 一律
  同宽 ``--``（区别只在卡片与颜色，纯逻辑层同文），``unsupported`` 直接不占位。
  ``--`` 与 max_text 同盒宽语义，所以状态切换不改岛宽——不跳。
- **告警级判定**：CPU/内存的占用率有普适阈值（warn/crit 两级 + 3pt 滞回，见
  :func:`alert_severity`），网络段的延迟/速率没有，所以内置阈值表只收前者；
  第三方段可以自带阈值（见 :class:`SegmentDeclaration`），注册时并进同一张表。
  判断只吃"读数 + 上一拍"，是纯函数——滞回状态归调用方持有。
- **第三方段公开注册面**（P3 Slice F1）：第三方只交数据与声明
  （:func:`register_segment`），**拿不到 QPainter**——本模块零 Qt，排版是宿主的
  事。声明盒宽 >120px **不拒绝注册**，只降为「+N 首批折叠候选」
  （见 :func:`oversize_segment_ids`）；格式化函数抛异常/返回巨型串一律在两处
  就地兜住（error 态 + 度量前按字符上限截断），所以失控段推不歪整岛。

本模块零 Qt 依赖（度量对象按鸭子类型接收）：跑在后台线程/单测替身上都能用，
真实 QFontMetrics 只在测宽时由调用方传进来。
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: 内置段 id（稳定标识：折叠、+N chip、卡片行都按它索引）。
NETWORK = "network"
CPU = "cpu"
MEM = "mem"
#: GPU 段：读数来自 ``pet/gpu_monitor.py``（Windows PDH 的**最忙引擎**占用），由
#: ``pet/sys_monitor.py`` 的共享采样线程按订阅喂。它如今与其它内置段同规——胶囊上
#: 一个定宽读数、展开卡片一行详情；平台不支持时按 ``unsupported`` 不占位（卡片仍
#: 写原因）。把它登记成已知 id 而不是"未实现的残留 id"，是为了让「这台机器没有这个
#: 能力」与「配置里写了个不存在的 id」在 UI 上分得开（前者进卡片原因行，后者是
#: ``--`` 降级段）。
GPU = "gpu"
#: 游戏帧率段（``island_metrics`` 的 fps 成员，**默认关**）：读数是**游戏的 Present
#: 帧率**——游戏每秒真正呈现到屏幕上的帧数。三个口径不许混算，也不许互相替代：
#: ①它不是桌宠动画的帧率（素材 fps / 渲染节拍）；②它不是显卡占用推算出来的；
#: ③它只对"当前采集目标"成立（目标由 ``game_fps_target_mode`` 决定：跟随前台程序
#: 或指定程序名），目标切走后的旧读数一律丢弃。采集链路的平台边界是 Windows
#: （系统级性能事件接口，首次使用可能需要授权）：其他平台上这一段按
#: ``unsupported`` 不占位，卡片里写原因——"这台机器没有这个能力"与"配置里写了
#: 个不存在的 id"必须分得开（同 GPU 的登记口径）。
FPS = "fps"
#: 内置段 id 全集——它们是**保留字**：第三方段注册同名 id 会与内置段的折叠/卡片
#: 索引撞车，故注册时直接拒注（见 :func:`register_segment`）。
BUILTIN_SEGMENT_IDS = frozenset({NETWORK, CPU, MEM, GPU, FPS})

#: 段状态词表（呈现见 :func:`render_compact`）。
STATE_OK = "ok"
STATE_SAMPLING = "sampling"
STATE_UNKNOWN = "unknown"
STATE_ERROR = "error"
STATE_UNSUPPORTED = "unsupported"
STATES = frozenset({STATE_OK, STATE_SAMPLING, STATE_UNKNOWN, STATE_ERROR, STATE_UNSUPPORTED})

# ---------------------------------------------------------------- 告警色体系（P2 Slice E2）
#: 告警级词表（段的 ``severity`` 取值）：``""`` = 正常，两级告警 ``warn`` / ``crit``。
#: 它是**呈现面**的契约（绘制层的数值色 + chip 告警点），与 ``state`` 正交：
#: ``state`` 说"这个读数可不可信"，``severity`` 说"这个可信读数危不危险"。
ALERT_WARN = "warn"
ALERT_CRIT = "crit"
#: 两级告警的词表（同 ``STATES`` 的用法）：查"这个串是不是告警级"。
ALERT_LEVELS = frozenset({ALERT_WARN, ALERT_CRIT})
#: 逐指标的两级阈值 ``(warn, crit)``（百分比）。**内置段只收有普适阈值的指标**：
#: CPU/内存的占用率是跨机器可比的量纲；网络段的延迟与速率差几个数量级
#: （家宽 vs 机房），给死线就是伪精确，所以它刻意不在这张表里。
#: 第三方段按自己声明的阈值在注册时并进本表（见 ``register_segment``：注销时撤回），
#: 所以判定链（:func:`alert_severity`）对内置段与第三方段完全是同一条。
ALERT_THRESHOLDS = {
    CPU: (80.0, 92.0),
    MEM: (85.0, 95.0),
}
#: 滞回带宽（pt）：升过线才上色、降过"线 − 3"才褪色。79/80 反复横跳不再闪色。
ALERT_HYSTERESIS = 3.0
#: 告警级的序（取最高级用）：越危险越大。
_ALERT_LEVELS = {"": 0, ALERT_WARN: 1, ALERT_CRIT: 2}
_ALERT_BY_LEVEL = ("", ALERT_WARN, ALERT_CRIT)

#: label 长度上限（11px 标签的排版预算）。
LABEL_MAX_CHARS = 6
#: 标签与字段盒之间的固定间距（px）。绘制层按它排版，宽度公式也含它——
#: 所以 :func:`render_compact` 的 compact 串里**不插空格**（插了就比盒宽多 1 列）。
LABEL_GAP_PX = 4
#: 四种兜底态共用的占位文案（与 max_text 同盒宽语义，不占位异常宽、不伪造 0）。
UNKNOWN_TEXT = "--"
#: 百分比段的单位（cpu / 内存）。单位是**段契约的呈现元素**而不是格式化器的一部分：
#: 数值进数值盒、单位进单位盒（双盒排版见 ``island_layout.place_info_segments``）。
#: 格式化器仍输出合并串（``sys_monitor.format_cpu/format_mem``）——卡片与其他消费方
#: 用同一个串，兼容面不因拆盒而破。
PERCENT_UNIT = "%"
#: 右省略符。用单字符 …（``...`` 会白占两列，盒宽紧张时更早截断）。
ELLIPSIS = "…"
#: 字体指纹的探针串：度量对象不暴露字体（QFontMetrics 没有 ``font()``），
#: 用它量出的宽度 + 一组行度量足以把字号/家族/DPI 变化区分开。
_FONT_PROBE_TEXT = "0123456789"


def _single_line(value: Any, field_name: str) -> str:
    """字段归一化为单行字符串；含换行直接拒注（排版禁令的机器可检部分）。"""
    text = "" if value is None else str(value)
    if "\n" in text or "\r" in text:
        raise ValueError(f"段契约：{field_name} 必须单行（禁止 \\n / \\r）：{text!r}")
    return text


def _clean_label(label: Any, owner: str) -> str:
    """label 归一化 + 长度护栏（快照与第三方声明共用同一份实现，免得两处漂移）。"""
    text = _single_line(label, "label")
    if len(text) > LABEL_MAX_CHARS:
        logger.warning(
            "段 %s 的 label %r 超过 %d 字符，已截断（超长标签会挤掉字段盒）",
            owner, text, LABEL_MAX_CHARS,
        )
        return text[:LABEL_MAX_CHARS]
    return text


@dataclass(frozen=True)
class SegmentSnapshot:
    """一段在某一时刻的完整状态（值 + 呈现态 + 卡片行素材）。

    ``id`` 稳定标识（折叠/卡片按它索引）；``label`` ≤6 字符可空（纯 glyph 段，
    如网络段的 ↓↑）；``text`` 当前紧凑文案（单行）；``max_text`` 最坏宽度样本
    （声明盒宽用）；``state`` 见 :data:`STATES`；``detail`` 是卡片 B 区的行素材
    （``unit_full``/``source``/``updated_at``/``error_reason``），本层原样透传。
    """

    id: str
    label: str = ""
    text: str = ""
    max_text: str = ""
    state: str = STATE_OK
    detail: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        segment_id = _single_line(self.id, "id")
        if not segment_id:
            raise ValueError("段契约：id 不能为空（它是折叠与卡片的稳定标识）")
        object.__setattr__(self, "id", segment_id)
        object.__setattr__(self, "label", _clean_label(self.label, segment_id))
        object.__setattr__(self, "text", _single_line(self.text, "text"))
        object.__setattr__(self, "max_text", _single_line(self.max_text, "max_text"))


class SegmentRegistry:
    """有序段注册表：**列表序 = 用户优先级 = 尾部先被折叠**（收缩 S2/S3 从尾部取）。

    - 重复 id 拒注（同一 id 只有一段，否则折叠/卡片索引会指到两个对象）；
    - 未知 id 查询返回 ``None``（不抛）——配置里残留了没实现的段时，
      调用方按"未注册"处理即可，不该让整条岛炸掉。
    """

    def __init__(self, segments=None):
        self._segments: list[SegmentSnapshot] = []
        self._by_id: dict[str, SegmentSnapshot] = {}
        for segment in segments or ():
            self.register(segment)

    def register(self, segment: SegmentSnapshot) -> SegmentSnapshot:
        if not isinstance(segment, SegmentSnapshot):
            raise TypeError(f"段注册表只接受 SegmentSnapshot，收到 {type(segment).__name__}")
        if segment.id in self._by_id:
            raise ValueError(f"段 id 重复注册：{segment.id!r}（同一 id 只允许一段）")
        self._segments.append(segment)
        self._by_id[segment.id] = segment
        return segment

    def get(self, segment_id) -> SegmentSnapshot | None:
        """按 id 取段；未注册返回 None（不抛）。"""
        return self._by_id.get(segment_id)

    def snapshots(self) -> list[SegmentSnapshot]:
        """当前全部段（注册序的副本：调用方排序不会改到注册表）。"""
        return list(self._segments)

    def __len__(self) -> int:
        return len(self._segments)


# ---------------------------------------------------------------- 宽度自测量
#: 声明盒宽缓存：``(段 id, 字体指纹) -> 宽度``。段数量有界（内置 3 段 +
#: 未来第三方段），字体切换会自然产生新键，无需 LRU；:func:`clear_width_cache`
#: 给测试与"手动失效"用。
_WIDTH_CACHE: dict[tuple[str, Any], int] = {}


def font_metrics_key(font_metrics) -> tuple:
    """度量对象 → 字体指纹（缓存键的一部分）。

    QFontMetrics 不暴露 QFont 对象（无 ``font()``），所以指纹 = 一组行度量 +
    探针串宽度。任一项变化（字号、家族、DPI）都换键 → 盒宽自动重测。
    """
    values = []
    for name in ("height", "lineSpacing", "ascent", "descent", "leading", "averageCharWidth"):
        getter = getattr(font_metrics, name, None)
        try:
            values.append(int(getter()) if callable(getter) else None)
        except Exception:  # pragma: no cover - 度量对象的极端实现
            values.append(None)
    advance = getattr(font_metrics, "horizontalAdvance", None)
    try:
        probe = int(advance(_FONT_PROBE_TEXT)) if callable(advance) else None
    except Exception:  # pragma: no cover
        probe = None
    return tuple(values) + (probe,)


def measure_segment_width(seg: SegmentSnapshot, font_metrics, *, font_key=None) -> int:
    """段的**声明盒宽**（px）：``label 宽 + 4(有 label 时) + max_text 宽``。

    调用方（布局层）在段注册 / 字体变化时问一次即可，结果按 ``(id, font_key)``
    缓存；``font_key`` 显式传入可以跳过指纹计算（已知字体的调用方省一次探测）。

    注意：``max_text`` 是**注册期声明**（最坏样本），缓存按规格只认 ``(id,
    font_key)``——某段运行期改动了自己的最坏样本时，调用方要自己
    :func:`clear_width_cache`，缓存不会替你发现。
    """
    key = (seg.id, font_metrics_key(font_metrics) if font_key is None else font_key)
    cached = _WIDTH_CACHE.get(key)
    if cached is not None:
        return cached
    gap = LABEL_GAP_PX if seg.label else 0
    width = (
        int(font_metrics.horizontalAdvance(seg.label))
        + gap
        + int(font_metrics.horizontalAdvance(seg.max_text))
    )
    _WIDTH_CACHE[key] = width
    return width


def clear_width_cache() -> None:
    """清空盒宽缓存（测试用；字体热切换想强制重测时也可手动调用）。"""
    _WIDTH_CACHE.clear()


# ---------------------------------------------------------------- 字段盒
def format_field_box(text: Any, max_text: Any, font_metrics) -> tuple[str, int]:
    """把当前文案塞进**定宽字段盒**：``(显示文案, 盒宽)``。

    盒宽由 ``max_text``（最坏样本）实测而来，盒内右对齐是布局层的事。实测文案
    不超过盒宽就原样返回；超了就右省略到盒宽并记 warning——这是"任何一段失控
    不得推歪整岛"的硬护栏，宁可截断可见地坏，也不让段挤歪邻居。
    """
    raw = "" if text is None else str(text)
    box_width = int(font_metrics.horizontalAdvance("" if max_text is None else str(max_text)))
    measured = int(font_metrics.horizontalAdvance(raw))
    if measured <= box_width:
        return raw, box_width
    display = _elide_right(raw, box_width, font_metrics)
    logger.warning(
        "字段盒截断：%r 实测 %dpx 超过盒宽 %dpx（最坏样本 %r），已右省略为 %r",
        raw, measured, box_width, max_text, display,
    )
    return display, box_width


def _elide_right(text: str, box_width: int, font_metrics) -> str:
    """右省略到盒宽：二分找"能塞下省略号的最长前缀"。二分而非逐字符回退——
    恶意段可以塞进几万字符，逐字符会退化成 O(n²) 次度量。"""
    if box_width <= 0:
        return ""
    if int(font_metrics.horizontalAdvance(ELLIPSIS)) > box_width:
        return ""  # 连省略号都塞不下：宁可不显示，也不越界
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if int(font_metrics.horizontalAdvance(text[:mid] + ELLIPSIS)) <= box_width:
            low = mid
        else:
            high = mid - 1
    return text[:low] + ELLIPSIS


# ---------------------------------------------------------------- 四态兜底呈现
def split_value_unit(merged: str, unit: str) -> tuple[str, str]:
    """把格式化器的合并串拆成 ``(数值, 单位)``——段契约的「值/单位分离」输出。

    合并串照旧由格式化器提供（卡片与其他消费方用它，兼容面不破），这个函数只给
    胶囊的双盒排版用：数值进右对齐的数值盒、单位进左对齐的单位盒（见
    ``island_layout.place_info_segments``）。

    ``unit`` 为空、整串就是单位本身、或不以该单位结尾（兜底串 ``--``）时把整串
    当数值、单位为空——四态兜底的 ``--`` 因此不会拆出一个孤零零的单位。注意单位
    **盒宽**由段自行声明（``_InfoSeg.unit``）：拆分结果只用来剥掉数值盒里的单位，
    单位盒宽不跟着读数走，否则采样态与 ok 态会差一个单位盒宽（状态切换跳宽）。
    """
    if not unit or merged == unit or not merged.endswith(unit):
        return merged, ""
    return merged[: -len(unit)], unit


def render_compact(seg: SegmentSnapshot) -> str | None:
    """胶囊上的紧凑文案；``unsupported`` 段返回 ``None``（调用方跳过、不占位）。

    - ``ok`` → ``label + text``（label 空则是纯 text）。分隔符是绘制层的 4px
      间距（见 :data:`LABEL_GAP_PX`），串里不插空格。
    - ``sampling`` / ``unknown`` / ``error`` → ``label + "--"``：同宽兜底，
      差别（更淡 / 红字 / 原因）由绘制层与卡片层承担。
    - 未识别的 state 一律按 ``unknown`` 兜底：不抛，也不把原始 ``text``
      当读数显示出去。
    """
    if seg.state == STATE_UNSUPPORTED:
        return None
    body = seg.text if seg.state == STATE_OK else UNKNOWN_TEXT
    return f"{seg.label}{body}"


# ---------------------------------------------------------------- 告警级判定
def alert_thresholds(metric_id: Any) -> tuple[float, float] | None:
    """该指标的 ``(warn, crit)`` 阈值；没声明过阈值的段返回 ``None``（永不告警）。"""
    return ALERT_THRESHOLDS.get(metric_id)


def seed_thresholds(segment_id: Any, thresholds: Any) -> tuple[float, float] | None:
    """把**声明源**（组件清单等宿主侧声明）里的阈值并进告警表，返回归一化后的值。

    为什么需要它：判定链（:func:`alert_severity`）只看这张表，而清单里的组件
    还没被 ``register_segment`` 注册过（注册是运行期、要看 provider 能不能导入），
    没有这一步，"声明了阈值却不告警"就成了必然。**注册面的条目优先**：同 id 已经
    注册过的阈值不被清单覆盖（注册就是这份进程里更强的声明）。

    非法阈值按"这条声明不合法"处理：记一条 warning 并返回 ``None``、**不动表**
    （宁可这一段不告警，也不让脏值把既有条目改坏）。
    """
    if segment_id in _SEGMENTS:
        return None
    if thresholds is None:
        return None
    try:
        normalized = _clean_thresholds(str(segment_id), thresholds)
    except (ValueError, TypeError) as exc:
        logger.warning("段 %s 的声明阈值非法，未并入告警表：%s", segment_id, exc)
        return None
    ALERT_THRESHOLDS[segment_id] = normalized
    return normalized


def drop_thresholds(segment_id: Any) -> bool:
    """撤掉**声明源**并进告警表的阈值（组件卸载 = 撤声明、撤阈值，不留第二真值）。

    内置段与已注册段的条目不动：前者是产品自己的账，后者归 ``unregister_segment``
    管（那条路径会一并撤）。返回是否真的撤掉了。
    """
    if segment_id in BUILTIN_SEGMENT_IDS or segment_id in _SEGMENTS:
        return False
    return ALERT_THRESHOLDS.pop(segment_id, None) is not None


def alert_severity(metric_id: Any, value: Any, previous: str = "") -> str:
    """一个读数对应的告警级（``""`` / ``warn`` / ``crit``）——**纯函数 + 上一拍**。

    - **升过线才上色**：``≥ warn`` 线上 warn、``≥ crit`` 线上 crit；
    - **降过"线 − 3"才褪色**：已在某一级的段要跌到 ``阈值 − ALERT_HYSTERESIS``
      以下才降一档。边界抖动（79/80/81 反复横跳）因此不会闪色——滞回要靠
      ``previous``，它是**上一拍的返回值**，由绘制层存在岛实例上（不进段对象：
      段是每拍现算的数据，不是累积状态）；
    - **无读数不褪色**：``value`` 为 None / NaN / 无穷 / 不可转数字时原样返回
      ``previous``——"还没读数"不等于"读数降到安全值"（与四态兜底的 ``--``
      不伪造 0 同口径）。折叠态会停掉被折段的采样，这条让告警不因停采样而凭空
      消失（chip 告警点的前提）；
    - **没阈值的指标恒无色**：网络段的延迟/速率没有普适阈值，``previous`` 也不还。
    """
    limits = alert_thresholds(metric_id)
    if limits is None:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return previous
    if not math.isfinite(number):
        return previous
    warn_line, crit_line = limits
    level = _ALERT_LEVELS.get(previous, 0)
    if number >= crit_line:
        return ALERT_CRIT
    if level >= _ALERT_LEVELS[ALERT_CRIT] and number >= crit_line - ALERT_HYSTERESIS:
        return ALERT_CRIT
    if number >= warn_line:
        return ALERT_WARN
    if level >= _ALERT_LEVELS[ALERT_WARN] and number >= warn_line - ALERT_HYSTERESIS:
        return ALERT_WARN
    return ""


def highest_severity(severities) -> str:
    """取一组告警级里的**最高级**（``chip_alert`` 用它：折叠只报最危险的那一段）。

    未识别的串按无色处理（不抛）：级别是绘制层的词表，脏值不该让整条岛炸掉。
    """
    top = 0
    for severity in severities:
        level = _ALERT_LEVELS.get(severity, 0)
        if level > top:
            top = level
    return _ALERT_BY_LEVEL[top]


# ---------------------------------------------------------------- 第三方段公开注册接口（P3 Slice F1）
#: 第三方段的声明盒宽上限（px，裁决稿 §二-7 的折中裁决）：**不拒绝注册**，只把它
#: 降为「+N 首批折叠候选」——不禁止表达，让长段自付成本。
MAX_SEGMENT_WIDTH_PX = 120
#: 第三方段 id 的长度上限（id 是折叠/卡片/日志的索引键，太长的 id 读不出是什么）。
MAX_SEGMENT_ID_CHARS = 32
#: 值文案的字符上限：**在度量之前**先砍。失控的格式化函数可以返回几万字符，
#: 让它进一次 ``QFontMetrics.horizontalAdvance`` 就是自己给自己排一个 O(n) 长任务；
#: 这是"任何一段失控不得推歪整岛"的第一道护栏，第二道是布局层的字段盒右省略
#: （:func:`format_field_box`），第三道是声明盒宽本身与读数无关。
MAX_VALUE_CHARS = 32
#: 原因/说明文案的字符上限：异常文案同样是第三方可控输入，不许把卡片撑爆。
MAX_DETAIL_CHARS = 120

#: 第三方段注册表（模块级）：**注册序 = 用户优先级 = 尾部先被折叠**（与内置段同）。
#: 键是段 id，值是声明；重复 id 在 :func:`register_segment` 处拒注。
_SEGMENTS: dict[str, "SegmentDeclaration"] = {}
#: 已经就"超宽"告过警的 ``(id, 字体指纹)``：宽度判决按字体缓存，日志同理——
#: 判决每次布局都会问，日志不该跟着刷屏。
_OVERSIZE_WARNED: set[tuple] = set()


def _clean_segment_id(value: Any) -> str:
    """第三方段 id 校验：str、非空、单行、无空白、不超长、非内置保留字。"""
    if not isinstance(value, str):
        raise TypeError(f"段 id 必须是 str，收到 {type(value).__name__}")
    segment_id = _single_line(value, "id")
    if not segment_id:
        raise ValueError("段 id 不能为空（它是折叠、+N chip 与卡片行的索引键）")
    if any(char.isspace() for char in segment_id):
        raise ValueError(
            f"段 id 不能含空白：{segment_id!r}（id 是索引键，含空白只会招来复制粘贴故障）")
    if len(segment_id) > MAX_SEGMENT_ID_CHARS:
        raise ValueError(
            f"段 id 超过 {MAX_SEGMENT_ID_CHARS} 字符：{segment_id[:MAX_SEGMENT_ID_CHARS]}…")
    if segment_id in BUILTIN_SEGMENT_IDS:
        raise ValueError(f"段 id {segment_id!r} 是内置段保留字（第三方段不得占用）")
    return segment_id


def _clean_thresholds(segment_id: str, thresholds: Any) -> tuple[float, float]:
    """阈值声明校验：``(warn, crit)`` 两个有限数，且 ``warn < crit``。"""
    try:
        warn, crit = (float(item) for item in thresholds)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"段 {segment_id!r} 的 thresholds 必须是 (warn, crit) 两个数：{thresholds!r}"
        ) from exc
    if not (math.isfinite(warn) and math.isfinite(crit)) or warn >= crit:
        raise ValueError(f"段 {segment_id!r} 的阈值必须是 warn < crit 的有限数：{thresholds!r}")
    return (warn, crit)


def _clean_value_text(value: Any, segment_id: str) -> str:
    """格式化结果 → 单行、限长的文案（护栏落在**度量之前**）。

    - 返回 ``None`` = 漏写 return，不是"读数就是 None"：抛给调用方降级成 error 态，
      不许把 None 当成一个合法读数（``--`` 是缺失读数的文案，不是格式化器的输出）；
    - 换行压平成空格（段契约禁换行）而不是拒注：这里是**运行期**，第三方段的
      一次脏输出不该让整条岛停止绘制；
    - 超长按字符上限截断 + warning：见 :data:`MAX_VALUE_CHARS`。
    """
    if value is None:
        raise ValueError("格式化函数返回 None（漏写 return？）")
    text = str(value).replace("\r", " ").replace("\n", " ")
    if len(text) > MAX_VALUE_CHARS:
        logger.warning(
            "段 %s 的读数 %d 字符超过 %d 字符上限，已截断（防度量长任务）",
            segment_id, len(text), MAX_VALUE_CHARS,
        )
        text = text[:MAX_VALUE_CHARS]
    return text


def _clean_note(text: Any) -> str:
    """原因文案 → 单行 + 限长（第三方可控输入，不许撑爆卡片）。"""
    flat = str(text).replace("\r", " ").replace("\n", " ").strip()
    return flat[:MAX_DETAIL_CHARS]


@dataclass(frozen=True)
class SegmentDeclaration:
    """第三方段的**全部输入面**（P3 Slice F1 的公开契约）。

    第三方只交数据与声明：id / label、值格式化函数、单位、阈值、detail 文案——
    **拿不到 QPainter**（本模块零 Qt）。排版、四态兜底、告警色、折叠都是宿主的
    既有链路，段这边只有数据。

    字段的纪律：

    - ``id`` 必须唯一、非空、单行、无空白、≤ :data:`MAX_SEGMENT_ID_CHARS`，且不是
      内置保留字（见 :data:`BUILTIN_SEGMENT_IDS`）；
    - ``max_text`` **必填**：它是声明盒宽的最坏样本。没有它就没有盒宽契约，岛宽会
      跟着读数跳——那是本项目的红线，所以缺它直接拒注；
    - ``format_value`` 把裸值转成文案。**异常与巨型串都在这里被兜住**
      （error 态 + 度量前截断），所以一个失控的格式化函数只会弄坏自己那一段；
    - ``thresholds`` 可选，声明后并进 :data:`ALERT_THRESHOLDS`，第三方段与
      CPU/内存走**同一条**告警判定（含 3pt 滞回）；
    - ``read`` 可选：无参取值函数。声明了就能用 :meth:`sample` 一步拿到快照；
      没声明也能注册，只是 :meth:`sample` 会如实报 error（"没有数据源"不是"读数是 0"）。
    """

    id: str = ""
    format_value: Callable[[Any], str] | None = None
    max_text: str = ""
    label: str = ""
    unit: str = ""
    unit_full: str = ""
    source: str = ""
    detail: str = ""
    thresholds: tuple[float, float] | None = None
    read: Callable[[], Any] | None = None

    def __post_init__(self) -> None:
        segment_id = _clean_segment_id(self.id)
        object.__setattr__(self, "id", segment_id)
        if not callable(self.format_value):
            if self.format_value is None:
                raise ValueError(
                    f"段 {segment_id!r} 缺少 format_value（值 → 文案的可调用对象）")
            raise TypeError(f"段 {segment_id!r} 的 format_value 必须是可调用对象（值 → 文案）")
        if not str(self.max_text or "").strip():
            raise ValueError(
                f"段 {segment_id!r} 缺少 max_text（最坏宽度样本）：没有它就没有盒宽契约，"
                "岛宽会跟着读数跳")
        object.__setattr__(self, "max_text", _single_line(self.max_text, "max_text"))
        object.__setattr__(self, "label", _clean_label(self.label, segment_id))
        for name in ("unit", "unit_full", "source", "detail"):
            object.__setattr__(self, name, _single_line(getattr(self, name), name))
        if self.thresholds is not None:
            object.__setattr__(self, "thresholds", _clean_thresholds(segment_id, self.thresholds))
        if self.read is not None and not callable(self.read):
            raise TypeError(f"段 {segment_id!r} 的 read 必须是可调用对象（无参取值）")

    def probe_snapshot(self) -> SegmentSnapshot:
        """测宽专用探针：盒宽只吃**声明面**（id/label/max_text），读数一个都不进。"""
        return SegmentSnapshot(id=self.id, label=self.label, max_text=self.max_text)

    def width_px(self, font_metrics, *, font_key=None) -> int:
        """声明盒宽（实测 px）：复用内置段的 :func:`measure_segment_width` 与同一份缓存。

        含**单位盒**（R1 评审修复）：E4 起胶囊是「值盒 + 1px + 单位盒」双盒，
        只量 label/max_text 会让长单位（如 ``Mbps``）绕过 120px 超宽判决——
        声明了 unit 的段必须把 1px 间隙与单位实测宽一并计入。
        """
        width = measure_segment_width(self.probe_snapshot(), font_metrics, font_key=font_key)
        if self.unit:
            width += 1 + int(font_metrics.horizontalAdvance(self.unit))
        return width

    def is_oversize(self, font_metrics, *, font_key=None) -> bool:
        """>120px 即超宽（降级为折叠候选，但**不**拒绝注册）。"""
        return self.width_px(font_metrics, font_key=font_key) > MAX_SEGMENT_WIDTH_PX

    def snapshot(self, value=None, *, state: str = STATE_OK,
                 error_reason: str = "") -> SegmentSnapshot:
        """把一个裸值格式化成一个段快照（**护栏在这里**，见类 docstring）。

        ``state != ok`` 时走兜底路径：不调格式化器（读数不该被拿去解释成文案），
        ``text`` 留空——:func:`render_compact` 会把它显示成同宽的 ``--``。
        """
        if state != STATE_OK:
            return self._degraded(state, error_reason)
        try:
            text = _clean_value_text(self.format_value(value), self.id)
        except Exception as exc:  # 第三方格式化器：任何异常都不许掀翻整岛
            reason = f"{type(exc).__name__}: {exc}"
            logger.warning("段 %s 的格式化函数抛异常，已降为 error 态：%s", self.id, reason)
            return self._degraded(STATE_ERROR, reason)
        return SegmentSnapshot(id=self.id, label=self.label, text=text,
                               max_text=self.max_text, state=STATE_OK,
                               detail=self._detail(error_reason=""))

    def sample(self) -> SegmentSnapshot:
        """取值 + 格式化一步到位（声明了 ``read`` 的段走这个）。

        ``read`` 抛异常 → error 态 + warning；没声明 ``read`` → error 态（静默：
        宿主也有可能自己采值后用 :meth:`snapshot`，缺 read 不是错误配置）。
        """
        if self.read is None:
            return self._degraded(STATE_ERROR, "段未声明数据源（read）")
        try:
            value = self.read()
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            logger.warning("段 %s 的取值函数抛异常，已降为 error 态：%s", self.id, reason)
            return self._degraded(STATE_ERROR, reason)
        return self.snapshot(value)

    def _degraded(self, state: str, error_reason: str) -> SegmentSnapshot:
        """兜底快照：不伪造读数（``text`` 空 → 呈现层画同宽 ``--``），原因落进 detail。"""
        return SegmentSnapshot(id=self.id, label=self.label, text="",
                               max_text=self.max_text, state=state,
                               detail=self._detail(error_reason))

    def _detail(self, error_reason: str) -> dict:
        """卡片 B 区的行素材（宿主的卡片层按同一组键取用，本层原样透传）。"""
        detail = {"unit_full": self.unit_full, "source": self.source, "detail": self.detail}
        if error_reason:
            detail["error_reason"] = _clean_note(error_reason)
        return detail


def register_segment(declaration: SegmentDeclaration = None, /, **fields) -> SegmentDeclaration:
    """注册一个第三方段（**公开 API**，宿主的唯一接入面）。

    两种调用形式（等价）::

        register_segment(id="wifi", label="WiFi", format_value=fn, max_text="888")
        register_segment(SegmentDeclaration(...))

    失败面（都是"早死早超生"的明确报错，不静默吞）：

    - 字段缺失/非法（空 id、含空白、超长、保留的内置 id、缺 ``max_text``、
      阈值不是 ``warn < crit``）→ ``ValueError``；
    - 类型不对（id 非 str、``format_value`` 不可调用、多给了字段关键字）→ ``TypeError``；
    - 重复 id → ``ValueError``（同一 id 只允许一段：折叠/卡片索引会指到两个对象）。

    **唯二不影响注册的违规**：声明盒宽 > :data:`MAX_SEGMENT_WIDTH_PX`（降为折叠
    候选 + :func:`oversize_segment_ids` 的 warning），以及段自己的数据源坏掉
    （运行期降级成 error 态）。
    """
    if declaration is None:
        declaration = SegmentDeclaration(**fields)
    elif fields:
        raise TypeError("register_segment 只接受一个声明，不能再带字段关键字参数")
    if not isinstance(declaration, SegmentDeclaration):
        raise TypeError(
            f"register_segment 只接受 SegmentDeclaration 或字段关键字，"
            f"收到 {type(declaration).__name__}")
    if declaration.id in _SEGMENTS:
        raise ValueError(f"段 id 重复注册：{declaration.id!r}（同一 id 只允许一段）")
    _SEGMENTS[declaration.id] = declaration
    if declaration.thresholds is not None:
        ALERT_THRESHOLDS[declaration.id] = declaration.thresholds
    return declaration


def unregister_segment(segment_id: Any) -> bool:
    """注销一个第三方段（返回是否真的注销了）：声明与它并进阈值表的阈值一起撤。

    内置段撤不动（:data:`BUILTIN_SEGMENT_IDS` 本来也不允许注册）——阈值表里的
    内置条目不该被一次第三方注销带走。

    **同时清空盒宽缓存**：缓存按 ``(id, 字体指纹)`` 记，注销后同名 id 带着新
    ``max_text`` 重新注册时必须重测，否则新声明会拿到旧样本的盒宽。注销是插件
    卸载这条冷路径，整表清缓存比"按 id 精细失效"更简单且不可能漏。

    **超宽告警去重记录也按 id 撤**：那本账按 ``(id, 字体指纹)`` 去重，注销后同名
    id 用**新**样本重新注册时必须能重新告警一次——不撤的话新声明会继承旧声明的
    "已经告过了"，超宽这件事就再也不会在日志里出现（卸载重装后静默超标）。
    """
    if _SEGMENTS.pop(segment_id, None) is None:
        return False
    if segment_id not in BUILTIN_SEGMENT_IDS:
        ALERT_THRESHOLDS.pop(segment_id, None)
    for key in [item for item in _OVERSIZE_WARNED if item[0] == segment_id]:
        _OVERSIZE_WARNED.discard(key)
    clear_width_cache()
    return True


def get_segment(segment_id: Any) -> SegmentDeclaration | None:
    """按 id 取声明；未注册返回 ``None``（不抛——与 ``SegmentRegistry.get`` 同口径）。"""
    return _SEGMENTS.get(segment_id)


def segment_declarations() -> tuple[SegmentDeclaration, ...]:
    """当前全部第三方段声明（**注册序 = 用户优先级 = 尾部先被折叠**）。"""
    return tuple(_SEGMENTS.values())


def clear_segments() -> None:
    """清空第三方段注册表（测试与卸载用）。

    一并撤掉三样东西：并进 :data:`ALERT_THRESHOLDS` 的阈值、超宽告警去重记录、
    盒宽缓存（缓存是纯函数结果的副本，清掉只会让下次重测，不会改变任何答案）。
    """
    for segment_id in list(_SEGMENTS):
        unregister_segment(segment_id)
    _OVERSIZE_WARNED.clear()
    clear_width_cache()


def judge_oversize(declaration, font_metrics, *, font_key=None) -> bool:
    """**公开的超宽判决**（含"同一字体只告警一次"的去重）：声明源与注册面共用。

    ``declaration`` 是任何带 ``id`` 与 ``is_oversize`` 的声明——``SegmentDeclaration``
    或宿主按清单装配的探针声明（``island_extension_host.ComponentDeclaration``）。
    把判决与告警都收在这里，是为了让"哪些段先被折"的 px 口径**只有一份**：
    ``oversize_segment_ids``（注册面）与宿主的首批折叠候选（清单面）都走这条。
    """
    key = font_metrics_key(font_metrics) if font_key is None else font_key
    if not declaration.is_oversize(font_metrics, font_key=key):
        return False
    if (declaration.id, key) not in _OVERSIZE_WARNED:
        _OVERSIZE_WARNED.add((declaration.id, key))
        logger.warning(
            "第三方段 %s 的声明盒宽 %dpx 超过 %dpx 上限：记入 +N 首批折叠候选名单"
            "（注册保留，但它会先被折进 chip；超长段自付成本）",
            declaration.id, declaration.width_px(font_metrics, font_key=key),
            MAX_SEGMENT_WIDTH_PX,
        )
    return True


def oversize_segment_ids(font_metrics, *, font_key=None) -> tuple[str, ...]:
    """声明盒宽超过 :data:`MAX_SEGMENT_WIDTH_PX` 的第三方段 id（注册序）。

    这是 120px 规则的落地点：超宽段**照样注册**，但进这张"首批折叠候选"名单
    （长段自付成本），而不是把注册掐死在门口。**宿主已消费这张名单**（F2 起）：
    宿主按同一份判决（:func:`judge_oversize`，注册面与清单面共用一个实现）把超宽段
    喂进 ``pet/island_layout.plan_info_row`` 的 ``fold_first_ids``——超宽段因此在收缩
    流水线里**先于**尾部段被折，哪怕它不在列表尾部；折叠计数、``+N`` chip 与卡片标注
    也都按"实际折掉的那几段"算（见 ``apply_shrink``）。

    判决在这里做而不是注册处，是因为 px 必须**实测**：第三方注册时进程里通常还
    没有字体（本项目的盒宽从来不写死 px）。同一个声明按 ``(id, 字体指纹)`` 只告警
    一次——判决每次布局都会问，日志不该跟着刷屏（注销会撤掉该 id 的去重记录，
    重装后能重新告警一次）。
    """
    key = font_metrics_key(font_metrics) if font_key is None else font_key
    return tuple(declaration.id for declaration in _SEGMENTS.values()
                 if judge_oversize(declaration, font_metrics, font_key=key))
