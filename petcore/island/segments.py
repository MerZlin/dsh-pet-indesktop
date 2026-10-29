"""灵动岛「段」契约层：状态词表 + 盒宽自测量 + 字段盒 + 告警判定（零 Qt）。

移植自主分支 ``pet/island_segments.py`` 的宿主侧契约，第三方段通过快照和可撤销
释放回调注册，口径见 ``docs/PETCORE-SPEC.md`` §29。三条不变量：

- **盒宽自测量**：段声明最坏宽度样本 ``max_text``，盒宽 = 度量结果，按
  ``(id, 字体指纹)`` 缓存；读数**永不进宽度**，否则岛宽会跟着数字跳。
- **运行期截断护栏**：实测文案超过盒宽时右省略并记 warning——一段失控不得推歪整岛。
- **四态兜底**：``ok`` 才有真读数；``sampling``/``unknown``/``error`` 一律同宽
  ``--``；``unsupported`` 不占位（调用方跳过）。状态切换因此不改岛宽。

本模块零 Qt：度量对象按鸭子类型接收（``QFontMetrics`` 或测试替身）。
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Callable
from typing import Any

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
#: 系统盘剩余空间（**默认关**的选择性段）：读数是「还剩多少」，不是占用率。
#: 口径见 ``petcore/platform/diskmon.py``——5 秒一拍、读不到报 error、绝不写 0。
#: 它**不进告警表**：现表的语义是「越高越危险」，剩余空间方向相反，硬套会反告警
#: （还剩 5G 时显示绿色、还剩 400G 时飘红）。
DISK = "disk"
#: 内置段 id 全集——它们是**保留字**：第三方段注册同名 id 会与内置段的折叠/卡片
#: 索引撞车，故注册时直接拒注（见 :func:`register_segment`）。
BUILTIN_SEGMENT_IDS = frozenset({NETWORK, CPU, MEM, GPU, FPS, DISK})

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
#: GPU/FPS 探针段的定宽占位素材：盒样本与单位都取自真实读数那一套，因此
#: 「有快照 ↔ 停采占位 ↔ 还没有第一份读数」三种形态的行宽逐像素相同。
#: 宽度必须与采样状态无关：否则「推快照 → 变宽 → 折进 +N → 停采 → 变窄 → 复原」
#: 会变成 1Hz 的折叠振荡（与 AUD-25 同族）。``unsupported`` 仍**不占位**。
PROBE_BOX_TEXT = {GPU: "100", FPS: "9999"}
PROBE_UNITS = {GPU: PERCENT_UNIT, FPS: "fps"}
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
    """label 归一化 + 长度护栏。"""
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
        self._releases: dict[str, Callable[[], object]] = {}
        for segment in segments or ():
            self.register(segment)

    def register(
        self,
        segment: SegmentSnapshot,
        *,
        release: Callable[[], object] | None = None,
    ) -> SegmentSnapshot:
        if not isinstance(segment, SegmentSnapshot):
            raise TypeError(f"段注册表只接受 SegmentSnapshot，收到 {type(segment).__name__}")
        if segment.id in self._by_id:
            raise ValueError(f"段 id 重复注册：{segment.id!r}（同一 id 只允许一段）")
        self._segments.append(segment)
        self._by_id[segment.id] = segment
        if release is not None:
            if not callable(release):
                raise TypeError("段释放回调必须可调用")
            self._releases[segment.id] = release
        return segment

    def get(self, segment_id) -> SegmentSnapshot | None:
        """按 id 取段；未注册返回 None（不抛）。"""
        return self._by_id.get(segment_id)

    def unregister(self, segment_id) -> bool:
        """撤销一个第三方段；未知 id 安全返回 False。"""
        segment = self._by_id.pop(str(segment_id), None)
        if segment is None:
            return False
        self._segments = [item for item in self._segments if item.id != segment.id]
        release = self._releases.pop(segment.id, None)
        if release is not None:
            try:
                release()
            except Exception:  # noqa: BLE001 - 扩展清理不能炸掉灵动岛
                logger.debug("segment release failed: %s", segment.id, exc_info=True)
        return True

    def clear(self) -> None:
        """清空注册段，并调用每段可选的释放回调。"""
        releases = list(self._releases.items())
        self._segments.clear()
        self._by_id.clear()
        self._releases.clear()
        for segment_id, release in releases:
            try:
                release()
            except Exception:  # noqa: BLE001 - 扩展清理不能炸掉灵动岛
                logger.debug("segment release failed: %s", segment_id, exc_info=True)

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

