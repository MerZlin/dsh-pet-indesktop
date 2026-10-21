# -*- coding: utf-8 -*-
"""灵动岛内容行的**布局层**：段数据 + 落位 + 收缩流水线（零 Qt）。

与 ``dynamic_island.py`` 的分工：

- 这里只有"一段长什么样、摆在哪个 x、宽多少、宁可选谁先牺牲"——纯数据与纯几何，
  度量对象（``QFontMetrics`` 或测试替身）由调用方注入，本模块不引入 Qt 依赖
  （与 ``island_segments`` / ``sys_monitor`` 同一条零 Qt 纪律）。
- ``dynamic_island.py`` 只管"什么时候重算、拿什么值来算、画到 QPainter 上"。

为什么独立成模块：D1 的段管线已让 ``dynamic_island.py`` 净增 471 行，而收缩
流水线又是整套判定逻辑（逐级触发 + 逆序恢复 + +N chip + 伸长锚点）。下沉成
纯函数后，"逐级触发/恢复/尾部优先/主信息永不折"这些规则可以在没有窗口、没有
真实字体、没有屏幕的替身上直接验——它们正是这类改动最容易错的地方。

三块内容：

- :class:`_InfoSeg` / :class:`_SegPlacement` / :func:`place_info_segments`：
  段的落位（段间 19px 槽位、竖线居中、字段盒右对齐、**有单位时数值盒 + 单位盒
  双盒**、chip 居中）；
- :func:`plan_info_row`：收缩流水线（名字省略 → 名字隐藏 → 整段折 +N →
  custom 主信息省略 → 保底）。折叠次序 = **首批候选（声明盒宽 >120px 的第三方段，
  由宿主用 ``island_segments.oversize_segment_ids`` 喂入）在前**，其余段尾部优先；
  中间段被折时按 id 集合生效，不做前缀截断（见 :func:`apply_shrink`）。
- :func:`apply_shrink`：把计划的结构套到**当刻**的段列表上——这是绘制层与测宽层
  取行的唯一方式（计划不持有段对象，读数每秒在变）；
- :func:`anchored_x`：宽度变化时按屏幕三分区决定伸长方向的锚点。
"""
from __future__ import annotations

from dataclasses import dataclass

from . import segments as island_segments
from .. import net_monitor

# ---------------------------------------------------------------- 行内几何常量
#: 统一基线：所有字号层级共用一条（胶囊垂直中心线下方 5px，见设计稿 §2.1）。
#: 逐字号各自垂直居中会在单位跨档（10↔13px 混排）时上下跳。
_BASELINE_DY = 5.0
#: 段间竖线：槽位 19px = 9 + 1 + 9，竖线 1×14 垂直居中。
_SEG_SLOT_PX = 19.0
_DIVIDER_W_PX = 1.0
_DIVIDER_H_PX = 14.0
#: 内容行内边距左右各 19px（= 6 内缩 + 13 内容边距，沿用既有视觉）。
_CONTENT_PAD_PX = 19.0
_ICON_PX = 26.0
_ICON_GAP_PX = 8.0
#: 数值盒与单位盒之间的固定间距（裁决稿 §二-5：k3 的「紧贴数字 1px」落在盒上，
#: 数值盒右对齐 + 单位盒取最宽单位左对齐，两盒之间留 1px）。
_UNIT_GAP_PX = 1.0
#: 角色名上限（静止态）96px（超出右省略）：名字是身份不是数据，不该吃掉指标空间。
#: D2 收缩的第一级把它收紧到 :data:`_NAME_SHRINK_PX`，第二级整段隐藏。
_NAME_MAX_PX = 96
_NAME_SHRINK_PX = 40
_NAME_GAP_PX = 10.0
_STATUS_DOT_PX = 8.0
_STATUS_GAP_PX = 10.0
#: 自然宽下限（沿用既有 120px：极小内容下岛仍是可点可拖的胶囊）。
_MIN_CAPSULE_W = 120
#: 主信息段的省略上限（**只对 custom 生效**：时间/余额/峰谷是定宽主信息，
#: 永不省略——它们是产品契约的锚点，收缩不得动）。
_PRIMARY_ELIDE_PX = 96

#: 主信息段的测宽样本（按模式取最坏形态）。time 定宽是"时间不推邻居"的前提；
#: balance 的样本取 ``balance.format_balance`` 的真实单行形态（带 ¥ 与 5 位整数），
#: 比它更长的串（带充值/赠送明细）按现文案加宽而不静默吃字（见 place_info_segments）。
_PRIMARY_SAMPLES = {
    "time": "88:88",
    "balance": "余额 ¥88888.88",
    "balance_tier": "低谷 → 88:88",
}
#: 网络段盒宽样本：用真格式化器生成（手写样本会和 ``format_network_slot`` 的定宽
#: 契约漂移）。取「千兆满速 + 近秒级延迟」这一**现实包络**（5 列全宽数字 + 3 位
#: 毫秒）——包络内读数在盒内右对齐伸缩、不推邻居；包络外（G 级速率 / 四位毫秒）
#: 由字段盒右省略护栏承接（R1 评审修复：盒宽恒为样本，采样值永不进宽度键）。
#: 不取 G 级病态最坏形态：那会让典型读数（K 级 + 两位毫秒）在盒内留出大块空白。
_NETWORK_BOX_SAMPLE = net_monitor.format_network_slot(
    1023 * 1024 ** 2, 1023 * 1024 ** 2, 999.0)
#: 设备槽段的标签（数值盒样本与单位盒内容见 ``DynamicIsland._device_segment``：
#: ``sys_monitor.MAX_TEXT`` 经 ``island_segments.split_value_unit`` 拆成值/单位两半，
#: 与格式化器同源）。
_DEVICE_LABELS = {island_segments.CPU: "CPU", island_segments.MEM: "内存"}
#: 设备槽段 id（订阅 key 同 id；cpu/mem 共用一条采样线程）。
_SYS_METRIC_IDS = (island_segments.CPU, island_segments.MEM)
#: GPU 段的胶囊标签。GPU **不是设备槽成员**（它的读数来自 PDH 读数器而不是 psutil
#: 快照，订阅也走 ``subscribe_gpu`` 那本账），所以它有独立常量而不是进
#: :data:`_DEVICE_LABELS`：那张表同时是"设备槽的段 id 集合"（胶囊分发用它）。
_GPU_LABEL = "GPU"

# ---------------------------------------------------------------- 段数据
#: 折叠 chip 的段类型（"+N"）。它不是指标段：没有 label、不参与折叠、不画竖线、
#: 盒宽自带内边距（见 :data:`_CHIP_PAD_PX`），文字在盒内居中而不是右对齐。
KIND_PRIMARY = "primary"
KIND_METRIC = "metric"
KIND_CHIP = "chip"
_CHIP_ID = "chip"
#: chip 规格（K3 §3.3）：高 18px、圆角 9px（半高）、文字左右各 6px、最小宽 26px。
_CHIP_H_PX = 18.0
_CHIP_RADIUS_PX = 9.0
_CHIP_PAD_PX = 6.0
_CHIP_MIN_W_PX = 26
#: chip 告警点（裁决稿 §三）：被折段含告警时在 chip 左侧点一颗 4px 色点。
#: **画在 chip 盒已有的内边距里**（文字左右各 6px，共 12px 松弛 ≥ 内侧 3px +
#: 点 4px + 与文字间距 3px = 10px），所以它一个像素都不改盒宽——色变与宽变
#: 彻底解耦（宽度白名单键里根本没有 severity）。
_CHIP_ALERT_DOT_PX = 4.0
_CHIP_ALERT_INSET_PX = 3.0
_CHIP_ALERT_GAP_PX = 3.0
#: 色点占据的文字起点（点 + 间距）：chip 文字从它开始居中，不压到点。
_CHIP_ALERT_FOOTPRINT_PX = _CHIP_ALERT_INSET_PX + _CHIP_ALERT_DOT_PX + _CHIP_ALERT_GAP_PX


@dataclass(frozen=True)
class _InfoSeg:
    """一个待渲染段（主信息段 / 指标段 / 折叠 chip）——**数据不是绘制串**。

    ``text`` 是"四态兜底之后"的显示文案（非 ``ok`` 一律同宽 ``--``）；``box_text``
    是测宽样本（指标段 = 声明的最坏形态，主信息段 = 模式样本）。段列表是数据，
    收缩流水线只过滤/替换这个列表（折尾段、加 chip、省名字），不改绘制。

    ``unit`` 是**单位盒**里画的串（cpu/mem 的 ``%``，见 ``island_segments`` 的
    「值/单位分离」）：有单位时这一段的排版是"数值盒 + 1px + 单位盒"两个盒子，
    ``box_text`` 因此只声明**数值**的样本宽（单位宽另算，见 :func:`place_info_segments`）。
    单位是模板面（进宽度白名单键）而读数不是——段自己声明单位，拆出来的值只影响
    盒内字形，不影响盒宽。

    ``elide_px`` 是"超宽时允许右省略到多少 px"：0 = 永不省略（时间/余额/峰谷这类
    定宽主信息、以及所有指标段——指标段本来就靠字段盒截断护栏兜），>0 = 收缩
    保底级可省略到该上限（custom 主信息）。

    ``severity`` 是告警级（``""|warn|crit``，见 ``island_segments.alert_severity``）：
    它是**呈现面**——只决定数值（与单位盒）的颜色，以及 chip 自己那一颗色点。
    与 ``unit`` 同一纪律：进内容缓存键（色变 → 位图重建）、**不进宽度白名单键**
    （色不变宽）。指标段的值由 ``_device_segment`` 从岛上的"上一拍告警级"
    （``DynamicIsland._update_alert_severity``）透传，chip 段由被折段的最高级透传
    （见 :func:`chip_segment`）。
    """

    kind: str        # "primary" / "metric" / "chip"
    seg_id: str      # "primary" / "network" / "cpu" / "mem" / 配置里的未知 id / "chip"
    label: str       # 段标签（L4 10px 层；主信息段与 chip 为空）
    text: str        # 当前显示文案（运行期值；有单位时只含数值那半）
    box_text: str    # 测宽样本（指标段 = max_text；主信息段 = 模式最坏形态）
    state: str       # island_segments 的状态词表
    elide_px: int = 0  # 超宽可右省略到的上限（0 = 永不省略）
    unit: str = ""     # 单位盒的串（"" = 该段没有单位盒）
    severity: str = ""  # 告警级（""|warn|crit）：只染数值，不改宽


@dataclass(frozen=True)
class _SegPlacement:
    """一段在内容行里的落位（纯几何：绘制层只做装配，不做度量）。

    有单位的段占**两个盒子**：``box_x/box_w`` 是数值盒（右对齐），
    ``unit_x/unit_w`` 是单位盒（左对齐、紧贴数值盒 1px）。``unit_w`` 取"各段最宽
    单位"的实测宽，所以同一行里所有单位盒左右缘对齐；无单位的段 ``unit_x=None``。

    ``alert_dot_x`` 只有带告警的 chip 段有值（被折段含 warn/crit）：它是色点
    左缘的绝对 x。色点占的是 chip 盒**内含**的内边距，所以它不影响 ``box_x`` /
    ``box_w`` / :attr:`right`——宽度白名单因此对 severity 完全免疫。
    """

    seg: _InfoSeg
    text: str            # 盒内实际绘制的文案（超宽时已右省略到盒宽）
    text_w: int          # 上述文案的实测宽（盒内对齐用）
    label_x: float
    label_w: int
    box_x: float
    box_w: int
    divider_x: float | None   # 段前竖线的 x；首段与 chip 前为 None
    unit_x: float | None = None   # 单位盒左缘；None = 该段没有单位盒
    unit_w: int = 0               # 单位盒宽（各段最宽单位实测；无单位时 0）
    alert_dot_x: float | None = None  # chip 告警点左缘；None = 这一行没有告警点

    @property
    def right(self) -> float:
        """段右缘：无单位 = 数值盒右缘；有单位 = 单位盒右缘。"""
        if self.unit_x is None:
            return self.box_x + self.box_w
        return self.unit_x + self.unit_w

    @property
    def text_x(self) -> float:
        """文案落笔的 x：字段盒内右对齐；chip 是容器，文字居中。

        chip 带告警点时，居中的**可用区**从色点右缘之后起算（``text_x`` 因此
        右移，文字不压点）；chip 盒本身的宽与缘不受影响。
        """
        if self.seg.kind == KIND_CHIP:
            left = self.box_x
            if self.alert_dot_x is not None:
                left += _CHIP_ALERT_FOOTPRINT_PX
            return left + (self.box_x + self.box_w - left - self.text_w) / 2.0
        return self.box_x + self.box_w - self.text_w

    @property
    def unit_text(self) -> str:
        """单位盒里**实际绘制**的串：非 ``ok`` 态不画（盒宽仍占，见 :attr:`right`）。

        四态兜底的呈现口径是"只有 ``--``"（见 ``island_segments.render_compact``），
        给未知读数配一个孤零零的 ``%`` 是伪造结构；但盒宽是模板面——不占宽就会在
        采样态↔ok 态之间跳宽。
        """
        if self.unit_x is None or self.seg.state != island_segments.STATE_OK:
            return ""
        return self.seg.unit


def _box_sample(seg: _InfoSeg, value_fm) -> str:
    """段盒的测宽样本：指标段恒为声明样本，主信息段取样本与现文案的较宽者。

    指标段盒宽**与当前读数无关**，这就是"数字跳动不推邻居"的全部机制；主信息段
    允许被现文案撑宽——``HH:mm`` 这类等宽形态由样本兜住（值变化不改宽），而余额
    明细那种超长串宁可让岛变宽也不静默吃字（右省略由收缩保底级负责 custom）。
    """
    if seg.kind != KIND_PRIMARY:
        return seg.box_text
    if int(value_fm.horizontalAdvance(seg.text)) <= int(value_fm.horizontalAdvance(seg.box_text)):
        return seg.box_text
    return seg.text


def elide_to_width(text: str, limit_px: int, font_metrics) -> str:
    """右省略到 ``limit_px``：二分找"能塞下省略号的最长前缀"。

    二分而非逐字符回退——名字/自定义文本可以是任意长度，逐字符会退化成 O(n²)
    次度量。塞不下连省略号时返回空串（宁可不显示，也不越界）。

    为什么不用 ``QFontMetrics.elidedText``：本模块零 Qt（度量对象按鸭子类型收），
    而且名字省略在三个地方各用一次（静止态 96px / 收缩态 40px / custom 主信息
    96px），三处必须由**同一份实现**给出同一个串，否则宽度白名单的键与实际排版
    会各自漂移。
    """
    limit = int(limit_px)
    if limit <= 0 or not text:
        return ""
    if int(font_metrics.horizontalAdvance(text)) <= limit:
        return text
    if int(font_metrics.horizontalAdvance(island_segments.ELLIPSIS)) > limit:
        return ""  # 连省略号都塞不下：宁可不显示，也不越界
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if int(font_metrics.horizontalAdvance(
                text[:mid] + island_segments.ELLIPSIS)) <= limit:
            low = mid
        else:
            high = mid - 1
    return text[:low] + island_segments.ELLIPSIS


def _unit_box_width(segments, label_fm) -> int:
    """一行里**单位盒的统一宽** = 各段单位实测宽的最大者（0 = 这一行没有单位）。

    取统一宽而不是逐段实测：一行的单位盒左缘本来就贴在各自数值盒右缘（数值宽
    可变），统一宽至少让右缘对齐——"各段最宽单位"是 ops5.5 双盒的原始口径。单位
    与标签同走 10px 层（见 ``dynamic_island._FONT_LAYERS``），所以度量对象复用
    ``label_fm`` 即可。
    """
    widths = [int(label_fm.horizontalAdvance(seg.unit))
              for seg in segments if seg.unit]
    return max(widths) if widths else 0


def place_info_segments(segments, *, start_x, primary_fm, value_fm, label_fm):
    """把段序列摆成一行（纯函数）：段间 19px 槽位、竖线居中、盒内对齐。

    为什么不用 ``island_segments.measure_segment_width``：那个函数只收**一个**
    度量对象（Slice A 的契约假设"标签与数值同字体"），而排版是两档字号——标签
    10px、数值 13px、主信息 14px。用 10px 度量数值会把盒子量小（13px 的字塞不
    进去，每帧吃一次右省略 warning），用 13px 度量标签又让段宽虚胖。所以标签宽走
    ``label_fm`` + :data:`island_segments.LABEL_GAP_PX`，数值盒宽走
    :func:`island_segments.format_field_box`（同一套 ``max_text`` 语义，且自带
    "超宽右省略 + warning"的硬护栏）。

    有单位的段（cpu/mem 的 ``%``）是**双盒**：数值盒（``box_x/box_w``，右对齐）
    + :data:`_UNIT_GAP_PX` + 单位盒（``unit_x/unit_w``，左对齐、统一取各段最宽
    单位实测）。拆盒的全部意义是"``9% → 100%`` 时数值盒右缘不动"——盒宽由样本
    定死，读数只在数值盒里左扩。

    ``start_x`` 是内容行里第一个段的起点（头像/角色名之后），返回值里的 x 全是
    绝对坐标——绘制层只按它落笔。chip 不画竖线（它是段序列的收尾标记，不是新数据
    段），盒宽自带左右内边距，文字居中；带告警（``seg.severity``）时色点画在 chip
    盒内的左内边距里，文字在色点右侧居中——两者都不改盒宽。
    """
    segments = tuple(segments)
    unit_w = _unit_box_width(segments, label_fm)
    placements: list[_SegPlacement] = []
    x = float(start_x)
    for index, seg in enumerate(segments):
        metrics = primary_fm if seg.kind == KIND_PRIMARY else value_fm
        alert_dot_x = None
        if seg.kind == KIND_CHIP:
            # chip 的文字走标签字号层（10px），盒宽自带左右内边距
            metrics = label_fm
            display = seg.text
            box_w = max(_CHIP_MIN_W_PX,
                        int(metrics.horizontalAdvance(display)) + 2 * int(_CHIP_PAD_PX))
        else:
            display, box_w = island_segments.format_field_box(
                seg.text, _box_sample(seg, metrics), metrics)
        label_w = int(label_fm.horizontalAdvance(seg.label)) if seg.label else 0
        gap = island_segments.LABEL_GAP_PX if seg.label else 0
        divider_x = None
        if index > 0 and seg.kind != KIND_CHIP:
            divider_x = x + (_SEG_SLOT_PX - _DIVIDER_W_PX) / 2.0
            x += _SEG_SLOT_PX
        box_x = x + label_w + gap
        if seg.kind == KIND_CHIP and seg.severity in island_segments.ALERT_LEVELS:
            alert_dot_x = box_x + _CHIP_ALERT_INSET_PX
        unit_x = box_x + box_w + _UNIT_GAP_PX if seg.unit else None
        placements.append(_SegPlacement(
            seg=seg, text=display, text_w=int(metrics.horizontalAdvance(display)),
            label_x=x, label_w=label_w, box_x=box_x, box_w=box_w,
            divider_x=divider_x,
            unit_x=unit_x, unit_w=unit_w if unit_x is not None else 0,
            alert_dot_x=alert_dot_x,
        ))
        x = placements[-1].right
    return placements


# ---------------------------------------------------------------- 收缩流水线
#: 收缩级（越大牺牲越多；纯计算，不落盘、不存状态——换个预算就重算）。
#: 逐级顺序 = 设计稿 §3.2：名字省略 → 名字隐藏 → 尾部整段折 +N → custom
#: 主信息省略 → 保底。同一份段列表 + 同一份预算永远得到同一级。
LEVEL_NONE = 0
LEVEL_NAME_ELIDE = 1
LEVEL_NAME_HIDE = 2
LEVEL_FOLD_BASE = 3   # 3 + k = 从尾部折掉 k 个指标段


@dataclass(frozen=True)
class ShrinkPlan:
    """一行内容的收缩结果：**结构**（可见段集 + 折叠段 + 名字态）与它的自然宽。

    刻意不持有段对象：段的 ``text`` 是运行期值（时间/读数每秒在变），计划里的
    快照一转眼就过期。计划只说"这一行画哪些段"（:attr:`visible`），文案由
    :func:`apply_shrink` 从**当刻**的段列表取——几何与像素因此不可能各自漂移。
    """

    level: int                       # 收缩级（见 LEVEL_*；同级 = 同一种牺牲）
    name: str                        # 实际显示的名字（"" = 隐藏）
    name_w: int                      # 名字占位宽（隐藏时 0）
    visible: tuple[str, ...]         # 实际排版的段 id（收尾的 chip 是 "chip"）
    folded: tuple[str, ...]          # 被折进 +N 的段 id，**折叠次序（先牺牲的在前）**
    segments_x: float                # 首段的绝对起点（绘制层照它落笔）
    width: int                       # 该级下的自然宽（未截断）
    elide_primary: bool = False      # 主信息是否按 elide_px 省略（custom 才会开）
    chip_alert: str = ""             # 被折段的最高告警级（""|warn|crit → chip 色点）

    @property
    def folded_count(self) -> int:
        return len(self.folded)


def apply_shrink(plan: ShrinkPlan, segments, *, primary_fm) -> tuple[_InfoSeg, ...]:
    """把行计划的**结构**套到当刻的段列表上（绘制/测宽两处的唯一取行方式）。

    ``segments`` 是调用方刚刚组合出来的段列表（文案是当前值），计划只负责"折掉
    哪几段、主信息省不省"——所以读数每一秒在变也不影响这一行画哪些段。

    **折叠按 id 而不是按"前缀截断"**（F2 起）：超宽第三方段是首批折叠候选，
    它可能落在列表**中间**——按前缀切片会折错段。这里只认计划给出的
    :attr:`ShrinkPlan.folded` id 集合，所以中间段被折时：

    - 可见段顺序照旧（去掉被折的那几个，chip 仍排在行尾作为"还有 N 段"的标记）；
    - ``+N`` 的 N = **实际折掉的段数**（不是计划里的数——计划被冻结期间配置可能
      刚被改小，报一个折不了的数就是骗人）；
    - chip 的告警级取**当刻**被折段的最高级（不是 ``plan.chip_alert``）：计划按
      ``_plan_inputs`` 缓存，而那个键刻意不含采样值与 severity（宽度白名单的纪律），
      于是"读数变、四舍五入后的文本没变、severity 变了"这一拍会命中旧计划——照抄
      计划里的告警级等于让 chip 色点停在上一拍的账上。

    计划里的 id 在当前列表里凑不够折叠数（拖拽冻结计划 + 配置刚被改小）时**按尾部
    补足**：宁可多折尾部，也不许把段数算成负数（负索引会从尾部吃段，那才是真的把
    主信息折了）。
    """
    segments = tuple(segments)
    if not segments:
        return ()
    primary, metrics = segments[0], segments[1:]
    target = min(plan.folded_count, len(metrics))
    dropped: set[str] = set()
    if target:
        present = {seg.seg_id for seg in metrics}
        for segment_id in plan.folded:      # 计划里的折叠顺序（先牺牲的在前）
            if len(dropped) >= target:
                break
            if segment_id in present:
                dropped.add(segment_id)
        for seg in reversed(metrics):       # 凑不够：按尾部补足（不折主信息）
            if len(dropped) >= target:
                break
            dropped.add(seg.seg_id)
    row: list[_InfoSeg] = [
        _elide_primary_segment(primary, primary_fm) if plan.elide_primary else primary]
    row.extend(seg for seg in metrics if seg.seg_id not in dropped)
    if dropped:
        alert = island_segments.highest_severity(
            _folded_alert(seg) for seg in metrics if seg.seg_id in dropped)
        row.append(chip_segment(len(dropped), alert))
    return tuple(row)


def chip_segment(folded_count: int, alert: str = "") -> _InfoSeg:
    """``+N`` chip 段（N = 被折段数）：段序列的收尾标记。

    它不是数据段——没有 label、不参与再次折叠、不画竖线、文字居中，只声明
    "还有 N 段在卡片里"。建模成段是为了让落位/测宽走**同一条**排版代码路径
    （见 :func:`place_info_segments`），绘制层不必为它开一套几何。

    ``alert`` 是被折段的**最高告警级**（``""|warn|crit``，见 :func:`plan_info_row`）：
    它透传进段的 ``severity``，绘制层据此在 chip 左侧点色点、落位层据此给文字
    让位——折叠不埋没告警。色点画在盒内已有内边距里，**不占额外宽**。
    """
    text = f"+{int(folded_count)}"
    return _InfoSeg(kind=KIND_CHIP, seg_id=_CHIP_ID, label="", text=text,
                    box_text=text, state=island_segments.STATE_OK, severity=alert)


def _elide_primary_segment(seg: _InfoSeg, font_metrics) -> _InfoSeg:
    """主信息的保底级省略：``elide_px>0`` 才动，且省略后盒宽跟着收。"""
    if seg.elide_px <= 0:
        return seg
    text = elide_to_width(seg.text, seg.elide_px, font_metrics)
    if text == seg.text:
        return seg
    return _InfoSeg(kind=seg.kind, seg_id=seg.seg_id, label=seg.label, text=text,
                    box_text=text, state=seg.state, elide_px=seg.elide_px,
                    unit=seg.unit, severity=seg.severity)


def _folded_alert(seg: _InfoSeg) -> str:
    """一个被折段贡献给 chip 的告警级。

    ``state=error`` 的段一律按 ``crit`` 算：读数拿不到（``--`` + 原因）本身就是
    要人打开卡片看的故障，折叠不能把它吞掉——这是 D2 ``chip_alert`` 的原始语义，
    E2 只是把"有没有告警"细化成"最高是哪一级"。
    """
    if seg.state == island_segments.STATE_ERROR:
        return island_segments.ALERT_CRIT
    return seg.severity


def plan_info_row(
    segments, *, lead_w, trail_w, name_text, available_w,
    primary_fm, value_fm, label_fm, name_fm, fold_first_ids=(),
) -> ShrinkPlan:
    """收缩流水线（纯函数）：给「段列表 + 行首行尾固定件 + 可用宽」返回行计划。

    ``available_w`` 是收缩触发线（屏可用宽 − 32px；≤0 表示拿不到屏信息 → 不收缩）。
    ``lead_w`` 是名字之前的固定件宽（左内边距 + 头像 + 间距），``trail_w`` 是段之后
    的固定件宽（状态点 + 间距）——两者由调用方按可见项算好，本函数不查任何配置。

    ``fold_first_ids`` 是**首批折叠候选**（宿主用
    ``island_segments.oversize_segment_ids`` 的判决喂进来）：声明盒宽 >120px 的
    第三方段先被牺牲，**哪怕它不在列表尾部**——长段自付代价，而"尾部先折"仍然是
    其余段之间的次序。折叠计数、``+N`` chip 与实际可见段集都按这个次序算，所以
    中间段被折不会折错段（见 :func:`apply_shrink`）。内置段默认不在候选里
    （它们的盒宽是产品自己定的模板，不受第三方 120px 规则约束）。

    逐级候选**按牺牲顺序排列**，取第一个 fits 的；一个都不 fits 就取最后一个
    （保底：头像 + 主信息 + 状态点永不折，宽度如实溢出，由绘制的截断护栏兜）。
    纯计算没有状态：放宽预算立刻整行恢复，不会留下"名字忽隐忽现"的迟滞。
    """
    segments = tuple(segments)
    primary = segments[0] if segments else None
    metrics = segments[1:] if segments else ()

    name_full = elide_to_width(name_text, _NAME_MAX_PX, name_fm) if name_text else ""
    name_shrunk = (elide_to_width(name_text, _NAME_SHRINK_PX, name_fm)
                   if name_text else "")

    # 牺牲次序：首批候选（注册序）在前，其余段按列表序的严格倒序（尾部优先）。
    first = set(fold_first_ids)
    fold_order: list[_InfoSeg] = [seg for seg in metrics if seg.seg_id in first]
    fold_order.extend(seg for seg in reversed(metrics) if seg.seg_id not in first)

    def build(name: str, folded_segments, elide_primary: bool, level: int) -> ShrinkPlan:
        sacrificed = tuple(folded_segments)        # 已是牺牲次序（先牺牲的在前）
        fold_set = {seg.seg_id for seg in sacrificed}
        row: list[_InfoSeg] = []
        if primary is not None:
            row.append(_elide_primary_segment(primary, primary_fm)
                       if elide_primary else primary)
        row.extend(seg for seg in metrics if seg.seg_id not in fold_set)
        folded = tuple(seg.seg_id for seg in sacrificed)
        chip_alert = island_segments.highest_severity(
            _folded_alert(seg) for seg in sacrificed)
        if sacrificed:
            row.append(chip_segment(len(sacrificed), chip_alert))
        name_w = int(name_fm.horizontalAdvance(name)) if name else 0
        x = float(lead_w) + (name_w + _NAME_GAP_PX if name else 0.0)
        placements = place_info_segments(row, start_x=x, primary_fm=primary_fm,
                                         value_fm=value_fm, label_fm=label_fm)
        end = placements[-1].right if placements else x
        return ShrinkPlan(
            level=level, name=name, name_w=name_w,
            visible=tuple(seg.seg_id for seg in row),
            segments_x=x, folded=folded,
            width=max(_MIN_CAPSULE_W, int(round(end + trail_w))),
            elide_primary=elide_primary,
            chip_alert=chip_alert,
        )

    candidates: list[ShrinkPlan] = []
    if name_full:
        candidates.append(build(name_full, (), False, LEVEL_NONE))
        if name_shrunk != name_full:
            candidates.append(build(name_shrunk, (), False, LEVEL_NAME_ELIDE))
        candidates.append(build("", (), False, LEVEL_NAME_HIDE))
    else:
        candidates.append(build("", (), False, LEVEL_NONE))
    for count in range(1, len(metrics) + 1):
        candidates.append(build("", fold_order[:count], False, LEVEL_FOLD_BASE + count))
    if primary is not None and primary.elide_px > 0:
        candidates.append(build("", fold_order, True, LEVEL_FOLD_BASE + len(metrics) + 1))

    if available_w <= 0:
        return candidates[0]        # 拿不到屏信息：不收缩（clamp 兜底）
    for plan in candidates:
        if plan.width <= available_w:
            return plan
    return candidates[-1]


def anchored_x(*, x: int, width: int, new_width: int,
               screen_left: int, screen_width: int) -> int:
    """伸长方向的三分区锚点（纯函数）：宽度变化时返回窗口新的左缘 x。

    岛中心在屏**左 1/3** → 左缘固定（向右长）；**右 1/3** → 右缘固定（向左长）；
    **中间 1/3** → 中心固定（两侧长）。为什么需要：贴着屏边的胶囊若一律向左缘长，
    会先长出屏再被 ``_clamp_rect`` 顶回来——用户看到的是"长出去又弹回"的抖。
    停靠状态不走这里（细条的几何由 ``strip_rect_for`` 贴边决定）。

    屏信息无效（``screen_width<=0``）时原样返回，调用方的 clamp 兜底。
    """
    if screen_width <= 0:
        return int(x)
    center = float(x) + float(width) / 2.0
    offset = center - float(screen_left)
    third = float(screen_width) / 3.0
    if offset < third:
        return int(x)
    if offset >= 2.0 * third:
        return int(round(float(x) + float(width) - float(new_width)))
    return int(round(center - float(new_width) / 2.0))
