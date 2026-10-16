# -*- coding: utf-8 -*-
"""避字移动：把屏幕文字识别成词框，供移动决策挑一个"不压字"的落点。

分层（与 pet/vision.py 同规矩：顶层零 Qt，PIL / Qt / OCR 一律函数内延迟导入）：
- 纯几何与评分：``TextBox`` / ``CoordMap`` / ``text_cost`` / ``plan_avoid``，
  可脱机单测，不碰屏幕也不碰 OCR；
- 采集：``read_words`` 在后台 worker 线程抓屏 + 走**项目自带的本地 OCR**
  （pet/screen_capture 的 RapidOCR，跨平台、断网可用），返回**绝对物理像素**文本块。

坐标口径（唯一约定，改代码前先分清）：
- **逻辑像素**：Qt 虚拟窗口坐标。``sp.vp`` / ``sp.avail`` / 窗口几何都在这套。
- **物理像素**：抓屏位图坐标，原点 = 虚拟桌面左上角（``SM_XVIRTUALSCREEN``）。
  本机 1.5 倍缩放，2560×1600 物理 = 1707×1067 逻辑，两者差 1.5 倍。
- ``CoordMap`` 负责互换，只需窗口当前的逻辑位置与它的 Win32 物理矩形：
  ``logical = anchor + physical / scale``，其中
  ``anchor = vp - WinRect/scale``。**不给缩放做全局换算**——多显示器各自
  缩放不同时，用自己窗口的物理矩形当锚点才不会把别的显示器比例算进来。

隐私：截图只在内存中处理、全程不落盘，也不发送到任何地方（本模块纯本地）。
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

log = logging.getLogger('dsh-pet-standalone')

__all__ = [
    "AvoidPlan", "CoordMap", "TextBox",
    "crop_region", "fingerprint", "grab_screen", "is_cjk", "mask_boxes",
    "ocr_available", "plan_avoid", "read_under_pet", "read_words",
    "read_words_from_image", "snap_to_gait", "text_cost", "virtual_origin",
]

# 判定"压住了一个字"的重叠面积阈值（物理像素²），取下面三者的夹逼：
#   max(TOUCH_MIN_PX2, min(TOUCH_RATIO * 词框面积, TOUCH_MAX_PX2))
# - 下界：OCR 词框边缘通常比字形略松，只擦到一两个像素不算压住，否则一条描边
#   就能让桌宠整屏乱窜；
# - 比例：占词框自身 15% 才算"压住"；
# - 上界：**必须有**——OCR 偶尔会把一整段并成一个大词框，只按比例算的话，
#   桌宠整个身体坐在上面都不够 15%（实测 3000×600 的框要压 27 万像素² 才算），
#   于是"压住了满屏的字"会被判成干净。
TOUCH_MIN_PX2 = 8.0
TOUCH_RATIO = 0.15
TOUCH_MAX_PX2 = 200.0


def _touch_threshold(box_area: float) -> float:
    return max(TOUCH_MIN_PX2, min(TOUCH_RATIO * max(box_area, 1.0), TOUCH_MAX_PX2))

# 扫描步长（逻辑像素）：走路落点按 12px 颗粒采样已足够精细（步态步幅远大于它），
# 又能把一次决策的候选数压在数百量级，GUI 线程查表耗时 < 1ms。
DEFAULT_STEP_PX = 12.0


def is_cjk(ch: str) -> bool:
    """是否汉字（含扩展 A 区与常用兼容区），用于"只避汉字"过滤。"""
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF      # CJK 统一表意
        or 0x3400 <= code <= 0x4DBF   # 扩展 A
        or 0xF900 <= code <= 0xFAFF   # 兼容表意
    )


@dataclass(frozen=True)
class TextBox:
    """一个 OCR 词框，**绝对物理像素**（原点 = 虚拟桌面左上角）。"""

    text: str
    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return max(0.0, self.right - self.left)

    @property
    def height(self) -> float:
        return max(0.0, self.bottom - self.top)

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def has_cjk(self) -> bool:
        return any(is_cjk(ch) for ch in self.text)


@dataclass(frozen=True)
class CoordMap:
    """逻辑像素 <-> 物理像素的仿射换算（只含平移与统一缩放）。

    ``logical = (anchor_x + px / scale, anchor_y + py / scale)``。
    窗口移动后 anchor 不变（逻辑位置与物理矩形同步平移），换屏/换缩放才需要重取。
    """

    anchor_x: float
    anchor_y: float
    scale: float

    @classmethod
    def from_window(cls, logical_x: float, logical_y: float,
                    physical_rect: tuple[int, int, int, int],
                    scale: float) -> "CoordMap":
        """由窗口的逻辑左上角与 Win32 物理矩形（GetWindowRect）构造。

        physical_rect 为 (x, y, w, h)；w/h 只用于自检，换算不需要它们。
        """
        scale = float(scale) or 1.0
        px, py = float(physical_rect[0]), float(physical_rect[1])
        return cls(anchor_x=float(logical_x) - px / scale,
                   anchor_y=float(logical_y) - py / scale,
                   scale=scale)

    def to_physical(self, x: float, y: float) -> tuple[float, float]:
        return ((float(x) - self.anchor_x) * self.scale,
                (float(y) - self.anchor_y) * self.scale)

    def to_logical(self, x: float, y: float) -> tuple[float, float]:
        return (self.anchor_x + float(x) / self.scale,
                self.anchor_y + float(y) / self.scale)

    def rect_to_physical(self, rect: Sequence[float]) -> tuple[float, float, float, float]:
        left, top = self.to_physical(rect[0], rect[1])
        right, bottom = self.to_physical(rect[2], rect[3])
        return (left, top, right, bottom)


@dataclass(frozen=True)
class AvoidPlan:
    """一次避字决策的结果（全部逻辑像素，可直接喂给移动计划）。"""

    cx: float          # 身体框中心 x
    body_top: float    # 身体框顶边 y（虚拟窗口坐标）
    touched: int       # 该落点压住几个字
    area: float        # 该落点压住的文字面积（物理像素²）
    changed: bool      # False = 维持原随机落点（原落点本来就干净）
    # 原来的随机落点压了多少（诊断/日志用：能一眼看出这次改道省掉了什么）
    proposed_touched: int = 0
    proposed_area: float = 0.0

    @property
    def clear(self) -> bool:
        return self.touched == 0


def _rect_of(cx: float, body_top: float, body_w: float, body_h: float
             ) -> tuple[float, float, float, float]:
    half = body_w / 2.0
    return (cx - half, body_top, cx + half, body_top + body_h)


class TextIndex:
    """词框网格索引：候选矩形只跟"自己压得到的格子"里的词框比，而不是全部。

    为什么需要它：决策是一次「数百个候选位置 × 每个位置比一遍全部词框」的扫描。
    实测全屏 563 个词框、条带 137–211 个：朴素实现下一次决策中位 **49ms**（最坏
    80ms），跑在 GUI 线程上会让动画明显一顿。两层优化后降到个位数毫秒
    （实测见 PR 报告「性能分析」）：
    1. 按 128px 网格分桶，单个候选平均只比十来个词框；
    2. ``cjk_only`` 在**建索引时**就把非汉字词框剔掉——放进内层循环的话，
       每个候选都要对每个词框重扫一遍 ``any(is_cjk(ch) ...)``。

    代价：建索引本身是词框数 × 约 2 个格子的一次性开销（微秒级），每次决策重建
    一次即可。去重用整数戳而不是 set，避免每个候选都分配新容器。
    """

    __slots__ = ('_boxes', '_cell', '_cells', '_stamp', '_tick')

    def __init__(self, boxes: Sequence[TextBox], cell: float = 128.0,
                 cjk_only: bool = True) -> None:
        items = [b for b in boxes if b.has_cjk] if cjk_only else list(boxes)
        self._boxes = items
        self._cell = max(16.0, float(cell))
        self._cells: dict[tuple[int, int], list[int]] = {}
        self._stamp = [0] * len(items)
        self._tick = 0
        cell_size = self._cell
        cells = self._cells
        for i, box in enumerate(items):
            # 负数（副屏在主屏左边）下 float//float 仍是向下取整，格子键不会错。
            for gx in range(int(box.left // cell_size), int(box.right // cell_size) + 1):
                for gy in range(int(box.top // cell_size), int(box.bottom // cell_size) + 1):
                    bucket = cells.get((gx, gy))
                    if bucket is None:
                        cells[(gx, gy)] = [i]
                    else:
                        bucket.append(i)

    def touching(self, rect: Sequence[float]) -> list[TextBox]:
        """与 rect 同格子的词框（去重后返回；不保证真的相交，交由 text_cost 判定）。"""
        self._tick += 1
        tick = self._tick
        stamp = self._stamp
        cells = self._cells
        boxes = self._boxes
        cell = self._cell
        out: list[TextBox] = []
        for gx in range(int(rect[0] // cell), int(rect[2] // cell) + 1):
            for gy in range(int(rect[1] // cell), int(rect[3] // cell) + 1):
                bucket = cells.get((gx, gy))
                if not bucket:
                    continue
                for i in bucket:
                    if stamp[i] != tick:
                        stamp[i] = tick
                        out.append(boxes[i])
        return out

    def cost(self, rect: Sequence[float]) -> tuple[int, float]:
        """与 ``text_cost(rect, boxes, cjk_only)`` 同语义，只是先按格子筛一遍。"""
        return text_cost(rect, self.touching(rect), cjk_only=False)


def text_cost(rect: Sequence[float], boxes: Iterable[TextBox],
              cjk_only: bool = True) -> tuple[int, float]:
    """返回 ``(压住的字数, 压住的面积)``；``(0, 0.0)`` = 完全没压到字。

    字数按"重叠是否够显著"计（见 TOUCH_MIN_PX2 / TOUCH_RATIO），面积按原始
    重叠累加——前者是主排序键（压 1 个大字块比压 3 个标点更该动），后者用来
    在字数相同时挑"压得最少"的那个。
    """
    left, top = rect[0], rect[1]
    right, bottom = rect[2], rect[3]
    if right <= left or bottom <= top:
        return 0, 0.0
    touched = 0
    area_sum = 0.0
    for box in boxes:
        if cjk_only and not box.has_cjk:
            continue
        ox = min(right, box.right) - max(left, box.left)
        oy = min(bottom, box.bottom) - max(top, box.top)
        if ox <= 0.0 or oy <= 0.0:
            continue
        overlap = ox * oy
        area_sum += overlap
        if overlap >= _touch_threshold(box.area):
            touched += 1
    return touched, area_sum


def _y_candidates(body_top: float, top_lo: float, top_hi: float,
                  margin: float) -> list[float]:
    """纵向候选：以当前身体框顶边为中心、上下各半个 margin 的 5 个档位。

    纵向只做"附近微调"——桌宠原本就有 ±25% 可用高度的纵向漫游，这里不再
    引入大跨度上下浮动，免得避字变成在屏幕上乱跳。
    """
    if top_hi <= top_lo:
        return [max(top_lo, min(top_hi, body_top))]
    raw = [body_top, body_top - margin, body_top + margin,
           body_top - margin / 2.0, body_top + margin / 2.0]
    out: list[float] = []
    for value in raw:
        clamped = max(top_lo, min(top_hi, value))
        if clamped not in out:
            out.append(clamped)
    return out


def _x_candidates(start: float, lo: float, hi: float, step: float) -> list[float]:
    """横向候选：从 start 向两侧按 step 铺开到 [lo, hi]，并带上两个端点。

    顺序固定（由近到远）——排序用显式距离做键，这里只保证覆盖不重复。
    """
    if hi <= lo:
        return [max(lo, min(hi, start))]
    step = max(1.0, float(step))
    out = [max(lo, min(hi, start))]
    value = out[0] - step
    while value > lo:
        out.append(value)
        value -= step
    if lo not in out:
        out.append(lo)
    value = out[0] + step
    while value < hi:
        out.append(value)
        value += step
    if hi not in out:
        out.append(hi)
    return out


def _dirty_span(box: TextBox, body_w: float, overlap_y: float
                ) -> tuple[float, float] | None:
    """纵向重叠 ``overlap_y`` 已定时，身体框中心落在哪个 x 区间会"压住"该词框。

    这是把"逐候选试算"换成"一次算清"的关键：``text_cost`` 判"压住"的条件是
    重叠面积 ≥ 阈值 ``t``，而 ``面积 = 横向重叠 ox × 纵向重叠 oy``；给定 y 档位
    后 oy 是常数，于是条件等价于 ``ox ≥ t/oy``。而 ox 随身体中心平移是一个梯形
    函数，``ox ≥ m`` 的解集是一段**连续区间**：

        身体左/右边缘扫过词框左/右边缘 → cx ∈ [L - W/2 + m, R + W/2 - m]

    返回 None 表示这个框在这个高度上永远压不住（需要的横向重叠超过身体宽或词框宽，
    即梯形峰值不够高）。区间是**闭**的：重叠刚好等于阈值就算压住。

    注：判定用的是与 ``text_cost`` 同一条"压住"判据，所以快路给出的落点允许与
    词框边界擦过不到一个阈值的宽度（实测约几十 px²，OCR 词框本身又比字形松），
    观感上就是没压住字——换来的是落点离原目标最近。
    """
    if overlap_y <= 0.0:
        return None
    need = _touch_threshold(box.area) / overlap_y
    if need > min(body_w, box.width):
        return None
    half = body_w / 2.0
    lo = box.left - half + need
    hi = box.right + half - need
    if hi <= lo:
        return None
    return (lo, hi)


def _merge_spans(spans: Sequence[tuple[float, float]],
                 cx_lo: float, cx_hi: float) -> list[tuple[float, float]]:
    """把区间裁剪到 [cx_lo, cx_hi] 并合并重叠部分（输入顺序无关）。"""
    out: list[tuple[float, float]] = []
    for lo, hi in sorted(spans):
        lo = max(lo, cx_lo)
        hi = min(hi, cx_hi)
        if hi <= lo:
            continue
        if out and lo <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def _nearest_clear_cx(spans: Sequence[tuple[float, float]], cx_lo: float, cx_hi: float,
                      target: float) -> float | None:
    """在 [cx_lo, cx_hi] 内、所有 spans 之外，取离 target 最近的位置；无解返回 None。

    脏区间是**闭**的（重叠刚好等于阈值就算压住），所以每个空隙都要按开区间取点：
    两端各退一个极小量，绝不能把区间的端点当成可用落点——第一版就是漏了这点，
    在"脏区一直铺到 cx_hi"时返回了 cx_hi 这个压字点（实测被一致性检查抓到）。
    """
    if cx_hi < cx_lo:
        return None
    merged = _merge_spans(spans, cx_lo, cx_hi)
    eps = 1e-6
    best: float | None = None

    def consider(gap_lo: float, gap_hi: float) -> None:
        nonlocal best
        if gap_hi < gap_lo:
            return
        cand = min(max(target, gap_lo), gap_hi)
        if best is None or abs(cand - target) < abs(best - target):
            best = cand

    dirty_hi: float | None = None
    for lo, hi in merged:
        consider(cx_lo if dirty_hi is None else dirty_hi + eps, lo - eps)
        dirty_hi = hi if dirty_hi is None else max(dirty_hi, hi)
    if dirty_hi is None:
        consider(cx_lo, cx_hi)          # 一个脏区间都没有 → 整个范围都可用
    else:
        consider(dirty_hi + eps, cx_hi)
    return best


def _search_clear(index: "TextIndex", *, body_w: float, body_h: float,
                  proposed_cx: float, proposed_top: float,
                  cx_lo: float, cx_hi: float, top_lo: float, top_hi: float,
                  y_margin: float, max_travel: float
                  ) -> tuple[float, float] | None:
    """精确求"完全不压字"的落点：对每个纵向档位求脏区间并集，在空隙里取最近点。

    与逐候选采样等价但**不采样、不近似**：给定 y 档位，脏集是若干连续区间的并集，
    空隙里的任意位置都不压字，所以"最近的不压字位置"就是 target 到最近空隙的
    投影。复杂度 O(n log n)（每个 y 档位一次），与候选数无关。

    **纵向档位优先**：按 ``_y_candidates`` 的顺序（当前高度 → 上/下整档 → 上/下
    半档）逐档看，第一档能在 ``max_travel`` 内就近解决就直接用它——否则会出现
    "为省 13px 横向而先跳 140px 纵向"这种违反直觉的走法（与旧逐点实现同语义）。
    整档都只能靠远处解决时，才在全部档位里挑最近的那个。

    返回 ``(cx, body_top)``；找不到任何不压字的落点（满屏都是字）返回 None。
    """
    fallback: tuple[float, float] | None = None
    slab_left = cx_lo - body_w
    slab_right = cx_hi + body_w
    for cy in _y_candidates(proposed_top, top_lo, top_hi, y_margin):
        spans = []
        for box in index.touching((slab_left, cy, slab_right, cy + body_h)):
            overlap_y = min(cy + body_h, box.bottom) - max(cy, box.top)
            span = _dirty_span(box, body_w, overlap_y)
            if span is not None:
                spans.append(span)
        cx = _nearest_clear_cx(spans, cx_lo, cx_hi, proposed_cx)
        if cx is None:
            continue
        travel = abs(cx - proposed_cx)
        if travel <= max_travel:
            return (cx, cy)
        if fallback is None or travel < abs(fallback[0] - proposed_cx):
            fallback = (cx, cy)
    return fallback


def snap_to_gait(
    *,
    boxes: Sequence[TextBox],
    want_cx: float,
    cur_cx: float,
    stride: float,
    room: float,
    cx_lo: float,
    cx_hi: float,
    body_w: float,
    body_h: float,
    body_top: float,
    cjk_only: bool = True,
    tries: int = 3,
) -> tuple[float, int, float]:
    """把"想要去的落点"吸附到**实际走得到的整圈步态位置**，并复核是否仍然干净。

    为什么需要：窗口位移会被量化到 ``loops × stride``（步态整圈，见 movement.quantize_move），
    所以真正落地的位置是一条离散格子。直接把"精确干净点"当成目标，落地时会再偏最多
    半个步幅——实测 shenshen 的步幅在 scale=0.72 下是 65~173 逻辑像素，半条就是几十
    像素，足够把身体重新压回字上。这里提前吸附，并对吸附后的位置用参考实现复核。

    候选取 ``want`` 最近的 ``tries`` 个整圈倍数（含两侧），按
    ``(压住的字数, 压住的面积, 与想要的落点的距离)`` 取最优；``room`` 限制越界。
    ``stride <= 0``（没有步幅数据）时不做吸附，直接返回 want 的代价。

    返回 ``(cx, touched, area)``。
    """
    want_rect = _rect_of(want_cx, body_top, body_w, body_h)
    if stride <= 0.0:
        touched, area = text_cost(want_rect, boxes, cjk_only)
        return want_cx, touched, area

    delta = want_cx - cur_cx
    if abs(delta) < stride:
        # 一步都走不到：交给窗口按最小一圈走（quantize_move 的下限就是 1 圈）
        sign = 1 if delta >= 0.0 else -1
        base = 1
    else:
        sign = 1 if delta > 0.0 else -1
        base = max(1, round(abs(delta) / stride))

    # 候选必须夹在 room 内，且**至少留一个**：所有候选都被 room 排除时若退回未吸附的
    # want，"复核落点"这件事就静默失效了（退回的位置根本没被检查过）。所以这里把
    # 候选夹进 [1, n_max]，并把 room 允许的最远整圈也放进来（就近都脏时它才是出路）。
    n_max = int(room // stride)
    if n_max < 1:
        # 连一圈都放不下（贴边）：吸附没有意义，交给窗口按 room 夹紧
        touched, area = text_cost(want_rect, boxes, cjk_only)
        return want_cx, touched, area
    base = min(max(base, 1), n_max)
    steps = {min(max(base + step, 1), n_max) for step in range(-tries, tries + 1)}
    steps.add(n_max)

    want_dist = abs(delta)
    best: tuple[tuple[int, float, float], float, int, float] | None = None
    for n in sorted(steps):
        dist = n * stride
        cx = cur_cx + sign * dist
        if cx < cx_lo or cx > cx_hi:
            continue
        touched, area = text_cost(_rect_of(cx, body_top, body_w, body_h), boxes, cjk_only)
        key = (touched, area, abs(dist - want_dist))
        if best is None or key < best[0]:
            best = (key, cx, touched, area)
    if best is None:
        touched, area = text_cost(want_rect, boxes, cjk_only)
        return want_cx, touched, area
    _key, cx, touched, area = best
    return cx, touched, area


def plan_avoid(
    *,
    body_w: float,
    body_h: float,
    proposed_cx: float,
    proposed_top: float,
    cur_cx: float,
    cur_top: float,
    cx_lo: float,
    cx_hi: float,
    top_lo: float,
    top_hi: float,
    boxes: Sequence[TextBox],
    step: float = DEFAULT_STEP_PX,
    max_travel: float = 700.0,
    y_margin: float = 140.0,
    cjk_only: bool = True,
) -> AvoidPlan:
    """给一个"本来想去的落点"，返回避字后的落点（纯函数，全逻辑像素）。

    规则（最小干预，尽量不动原来的随机漫游）：
    1. 原落点本来就不压字 → 原样返回（``changed=False``），不做任何改变；
    2. 原落点压字 → 在 ``max_travel`` 半径内找完全不压字的落点，取最近的；
    3. 半径内找不到全干净的 → 再看整个可达范围有没有"压得更少"的落点，
       有就走过去；4. 都没有（全屏都是字）→ 挑压得最少的那个落点
       （先比压住的字数，再比压住的面积，最后比离原落点的距离）。

    实现分两条路：2/3 走 ``_search_clear`` 的**区间并集精确解**（与候选网格无关，
    实测把 GUI 线程上的决策从 49ms 压到亚毫秒级）；只有在"满屏都是字、一个干净
    落点都没有"时，才退到 4 的逐候选取样（那种场景下本来也没有更聪明的办法）。
    """
    boxes = list(boxes)
    if not boxes:
        return AvoidPlan(cx=proposed_cx, body_top=proposed_top, touched=0,
                         area=0.0, changed=False)

    index = TextIndex(boxes, cjk_only=cjk_only)
    proposed = _rect_of(proposed_cx, proposed_top, body_w, body_h)
    proposed_cost = index.cost(proposed)
    if proposed_cost[0] == 0:
        # 1. 原落点干净：保持随机漫游的自然感，绝不为了"更干净"再挪一次。
        return AvoidPlan(cx=proposed_cx, body_top=proposed_top, touched=0,
                         area=0.0, changed=False)

    # 2/3 快路：区间并集精确求"完全不压字"的最近落点（不采样、不看候选数）。
    clear = _search_clear(index, body_w=body_w, body_h=body_h,
                          proposed_cx=proposed_cx, proposed_top=proposed_top,
                          cx_lo=cx_lo, cx_hi=cx_hi, top_lo=top_lo, top_hi=top_hi,
                          y_margin=y_margin, max_travel=max_travel)
    if clear is not None:
        cx, cy = clear
        touched, area = index.cost(_rect_of(cx, cy, body_w, body_h))
        if touched == 0:
            return AvoidPlan(cx=cx, body_top=cy, touched=0, area=area, changed=True,
                             proposed_touched=proposed_cost[0],
                             proposed_area=proposed_cost[1])
        # 理论上到不了这里（区间口径与 text_cost 同源）；真出现说明两边判据漂移了，
        # 宁可退到慢路给出一个"确实更少压字"的落点，也不返回一个没核对过的坐标。
        log.warning('避字：区间解与逐点判据不一致（touched=%d），回退逐候选取样', touched)

    # 4 慢路：满屏都是字、一个干净落点都没有 → 逐候选取样挑"压得最少"的。
    best_all: tuple[tuple[int, float, float, float], float, float] | None = None
    best_near: tuple[tuple[int, float, float, float], float, float] | None = None
    for cy in _y_candidates(proposed_top, top_lo, top_hi, y_margin):
        for cx in _x_candidates(proposed_cx, cx_lo, cx_hi, step):
            rect = _rect_of(cx, cy, body_w, body_h)
            touched, area = index.cost(rect)
            travel = abs(cx - proposed_cx)
            key = (touched, area, travel, abs(cy - proposed_top))
            candidate = (key, cx, cy)
            if best_all is None or key < best_all[0]:
                best_all = candidate
            if travel <= max_travel and (best_near is None or key < best_near[0]):
                best_near = candidate
        # 纵向档位由近到远，一旦找到干净落点就没必要再扫更远的纵向档位
        if best_near is not None and best_near[0][0] == 0:
            break

    # 2/3：半径内优先；半径内挑不出干净落点时，允许走到半径外的更干净落点。
    chosen = best_near if best_near is not None else best_all
    if (best_near is not None and best_near[0][0] > 0
            and best_all is not None and best_all[0][:2] < best_near[0][:2]):
        chosen = best_all
    assert chosen is not None  # boxes 非空 + 候选至少一个 → 必然有解
    key, cx, cy = chosen
    if (key[0], key[1]) >= proposed_cost:
        # 4. 找不到更好的：保持原落点（它就是"压得最少"的那个）。
        return AvoidPlan(cx=proposed_cx, body_top=proposed_top,
                         touched=proposed_cost[0], area=proposed_cost[1],
                         changed=False, proposed_touched=proposed_cost[0],
                         proposed_area=proposed_cost[1])
    return AvoidPlan(cx=cx, body_top=cy, touched=key[0], area=key[1], changed=True,
                     proposed_touched=proposed_cost[0], proposed_area=proposed_cost[1])


# ------------------------------------------------------------------ OCR 采集

# 屏幕指纹的缩略边（像素）：把整屏缩到 64×64 再比字节，用来判断"屏幕内容有没有
# 变过"。桌宠自己那块要显式抹掉（fingerprint 的 ignore_rect）——它每帧都在动，
# 不抹的话指纹永远在变，静置桌面上也每轮白跑一次 OCR（实测 OCR 才是大头）。
_FINGERPRINT_EDGE = 64
# 抹除桌宠那块时往外多抹几格：缩略图插值有支撑，边界会把桌宠的像素渗到邻格里。
_FINGERPRINT_MASK_PAD = 2

_engine_cache: dict[str, Any] = {}
_ocr_failed = False


def mask_boxes(boxes: Iterable[TextBox],
               rect: Sequence[float]) -> tuple[TextBox, ...]:
    """丢掉与 rect 相交的词框。

    抓屏时桌宠自己的身体就在那块区域上，OCR 到的是它自己的像素（噪声），必须剔除；
    那块区域的真内容由 ``read_under_pet`` 从**底下那个窗口自己**的渲染结果补回来。
    没被 rect 压到的词框一律保留——哪怕是紧贴边缘的（那正是判断"有没有压住字"的依据）。
    """
    left, top, right, bottom = rect[0], rect[1], rect[2], rect[3]
    if right <= left or bottom <= top:
        return tuple(boxes)
    out = []
    for box in boxes:
        if (box.right > left and box.left < right
                and box.bottom > top and box.top < bottom):
            continue
        out.append(box)
    return tuple(out)


# 抓"底下那个窗口"时往外扩一点：桌宠边缘外的字往往才是判断"压住了一整行"的依据。
_UNDER_PET_PAD_PX = 8.0


def read_under_pet(footprint: Sequence[float],
                   need_text: bool = True) -> tuple[TextBox, ...]:
    """抓桌宠**底下那个窗口自己**的内容并 OCR，返回该区域的绝对物理像素词框。

    footprint = (left, top, right, bottom) 绝对物理像素（桌宠身体框）。

    为什么不用"把桌宠从抓屏里排除"（WDA_EXCLUDEFROMCAPTURE）：实机实测那会让窗口在
    物理屏幕上也不再渲染（用户看到的是"桌宠消失了"），详见 platform_win 里的历史注释。
    PrintWindow 取的是窗口自身的渲染结果，与屏幕上谁盖着谁无关，因此不需要给桌宠设
    任何窗口显示属性。失败（窗口不支持下这个 API、视频窗口只回黑图等）一律返回空元组，
    调用方按"这块没数据"处理。
    """
    left = float(footprint[0]) - _UNDER_PET_PAD_PX
    top = float(footprint[1]) - _UNDER_PET_PAD_PX
    right = float(footprint[2]) + _UNDER_PET_PAD_PX
    bottom = float(footprint[3]) + _UNDER_PET_PAD_PX
    if right - left < 24 or bottom - top < 24:
        return ()
    import os as _os

    from . import platform_win  # 延迟导入：本模块顶层保持零 Qt
    hwnd = platform_win.topmost_window_under(
        (int(left), int(top), int(right - left), int(bottom - top)),
        exclude_pid=_os.getpid())
    if not hwnd:
        return ()
    win_rect = platform_win.window_physical_rect(hwnd)
    pixels = platform_win.print_window_pixels(hwnd)
    if win_rect is None or pixels is None:
        return ()
    raw, width, height = pixels
    try:
        from PIL import Image
        img = Image.frombytes('RGBA', (width, height), raw, 'raw', 'BGRA').convert('RGB')
    except Exception:
        log.debug('避字：底下窗口位图转换失败', exc_info=True)
        return ()
    ox, oy = win_rect[0], win_rect[1]
    crop_box = (max(0, int(left) - ox), max(0, int(top) - oy),
                min(width, int(right) - ox), min(height, int(bottom) - oy))
    if crop_box[2] - crop_box[0] < 24 or crop_box[3] - crop_box[1] < 24:
        return ()
    crop = img.crop(crop_box)
    origin = (ox + crop_box[0], oy + crop_box[1])
    return read_words_from_image(crop, origin, need_text=need_text)


def virtual_origin() -> tuple[int, int]:
    """虚拟桌面左上角的物理坐标（抓屏位图的原点）。非 Windows 返回 (0, 0)。"""
    if sys.platform != 'win32':
        return 0, 0
    try:
        import ctypes
        u32 = ctypes.windll.user32
        # SM_XVIRTUALSCREEN = 76, SM_YVIRTUALSCREEN = 77
        return int(u32.GetSystemMetrics(76)), int(u32.GetSystemMetrics(77))
    except Exception:
        return 0, 0


def grab_screen():
    """抓**整个虚拟桌面**（物理像素，PIL Image）；失败返回 None。

    只在后台 worker 线程调用。整屏抓一次的代价与只抓条带几乎相同（位图是从
    同一个屏幕 DC 一次性拷出来的），所以这里抓全屏：既能裁出条带做 OCR，
    又能顺带算出"整屏有没有变"的指纹。
    """
    try:
        from PIL import ImageGrab  # 延迟导入：PIL 不随本模块加载常驻
        return ImageGrab.grab(all_screens=True)
    except Exception:
        log.debug('避字：抓屏失败', exc_info=True)
        return None


def crop_region(img, region: tuple[int, int, int, int]):
    """按**绝对物理坐标**从整屏图里裁出区域；越界/空区域返回 None。"""
    ox, oy = virtual_origin()
    left = int(region[0]) - ox
    top = int(region[1]) - oy
    right = left + int(region[2])
    bottom = top + int(region[3])
    width, height = img.size
    left = max(0, min(width, left))
    top = max(0, min(height, top))
    right = max(left, min(width, right))
    bottom = max(top, min(height, bottom))
    if right - left <= 0 or bottom - top <= 0:
        return None
    return img.crop((left, top, right, bottom))


def fingerprint(img, ignore_rect: Sequence[float] | None = None) -> bytes:
    """整屏指纹：缩略后取字节，仅用于"和上一帧是否一样"。

    ``ignore_rect``（绝对物理像素）里的内容**不参与**指纹：抓屏时桌宠自己就压在
    那块上、每帧都在动，不抹掉的话指纹永远在变，"屏幕没变就别 OCR"这条省流路径
    等于失效。
    """
    try:
        edge = _FINGERPRINT_EDGE
        small = img.resize((edge, edge))
        data = bytearray(small.tobytes())
        if ignore_rect is not None:
            width, height = img.size
            stride = len(data) // (edge * edge)
            if width and height and stride:
                vx, vy = virtual_origin()
                # 缩略图的插值是带支撑的（会取邻域像素），被忽略区域会**渗出**到
                # 相邻格子；不外扩就会看到"抹了还是每帧都在变"。多留 2 格吸收渗漏。
                pad = _FINGERPRINT_MASK_PAD
                x0 = max(0, min(edge, int((ignore_rect[0] - vx) * edge / width) - pad))
                x1 = max(0, min(edge, int((ignore_rect[2] - vx) * edge / width) + 1 + pad))
                y0 = max(0, min(edge, int((ignore_rect[1] - vy) * edge / height) - pad))
                y1 = max(0, min(edge, int((ignore_rect[3] - vy) * edge / height) + 1 + pad))
                blank = b'\x00' * stride
                for ty in range(y0, y1):
                    row = ty * edge
                    for tx in range(x0, x1):
                        off = (row + tx) * stride
                        data[off:off + stride] = blank
        return bytes(data)
    except Exception:
        return b''


def ocr_available() -> bool:
    """本机是否具备可用的本地 OCR（缺依赖 / 缺模型都是 False）。

    识别引擎复用项目自带的 **RapidOCR**（``pet/screen_capture/ocr.py``，PP-OCR 模型
    随包放在 ``resources/rapidocr_models/``，断网可用、跨平台）——不再自建一套
    Windows 专属的 winrt OCR：那是重复建设，而且把功能锁死在 Windows 上。

    **不在这里起子进程试识别**：一次识别要 3~4 秒（见模块头"成本"一节），
    开关是否生效不该付这个代价。这里只做"导入 + 模型文件在位"的静态检查，
    真失败在识别时按"没有文字图"降级并记一条日志。
    """
    try:
        from .screen_capture import ocr_models
        return not ocr_models.model_problem(ocr_models.resolve_model_dir())
    except Exception:
        return False


def _to_qimage(img):
    """PIL 图 → QImage（上游 OCR 的入参口径）。失败返回 None。

    顶层零 Qt 是模块契约，所以 QImage 只在函数内导入。
    """
    try:
        from PySide6.QtGui import QImage
    except Exception:
        return None
    try:
        rgb = img.convert('RGB')
    except Exception:
        return None
    data = rgb.tobytes()
    width, height = rgb.size
    # QImage 不持有 data，必须 copy() 一份再交出去（否则离开本函数就是悬垂指针）。
    return QImage(data, width, height, 3 * width,
                  QImage.Format.Format_RGB888).copy()


def _ocr_engine(use_rec: bool = True):
    """取（并缓存）识别驱动；拿不到返回 None。

    上游引擎是**一次识别起一个子进程、用完就丢**（他们的取舍：常驻内存归零）。
    所以这里的"缓存"只是省掉对象构造，真正的大头（进程启动 + 模型加载 + 推理）每轮
    都要付——正因如此识别改成**按需触发**（见 text_avoid_controller）。

    ``use_rec=False`` 走"只检测"档（``ocr.worker_command(use_rec=False)``）：只要框
    在哪、不要文字内容时用，实测同一张图 2672ms → 1297ms。两种模式各缓存一个对象。
    """
    global _ocr_failed, _engine_cache
    if _ocr_failed:
        return None
    if use_rec not in _engine_cache:
        try:
            from .screen_capture.ocr import OcrEngine, worker_command
            if use_rec:
                engine = OcrEngine()
            else:
                engine = OcrEngine(command=lambda: worker_command(use_rec=False))
            _engine_cache[use_rec] = engine
        except Exception:
            log.debug('避字：OCR 引擎构造失败', exc_info=True)
            _ocr_failed = True
            return None
    return _engine_cache[use_rec]


def read_words_from_image(img, origin: tuple[float, float],
                          need_text: bool = True) -> tuple[TextBox, ...]:
    """对**已经抓好**的图做 OCR，返回绝对物理像素文本块（后台线程入口）。

    ``origin`` = 该图左上角在虚拟桌面物理坐标里的位置；块框是图内相对坐标，
    这里统一加上 origin，调用方拿到的永远是同一套绝对坐标。

    上游 RapidOCR 给的是**行/块级四边形**（不是词级），这里取外接矩形——比词框粗，
    方向上是"宁可多避一点"，符合本功能的口味。失败一律返回空元组并记一次日志
    （调用方按"没有文字图"降级，不打断桌宠）。

    ``need_text=False`` 时走**只检测**档并接受"有框没文字"的块（``text`` 为空）：
    这只在调用方**不需要按文字内容过滤**时才是对的（本功能对应 `cjk_only=False`）。
    反过来如果只避汉字却用了这一档，所有框的文字都是空的，会被当成"哪都没有字"。
    """
    engine = _ocr_engine(use_rec=need_text)
    if engine is None:
        return ()
    qimg = _to_qimage(img)
    if qimg is None:
        return ()
    try:
        # 只检测档的结果全是"有框没文字"，要在宿主侧显式保留它们（默认会丢掉空块）
        blocks = engine.recognize(qimg, keep_empty_text=not need_text)
    except Exception:
        log.info('避字移动：本地 OCR 识别失败，本轮按"没有文字图"处理', exc_info=True)
        return ()
    ox, oy = float(origin[0]), float(origin[1])
    out: list[TextBox] = []
    for block in blocks:
        left, top, width, height = block.bounds
        if width <= 0.0 or height <= 0.0:
            continue
        text = str(block.text)
        if need_text and not text.strip():
            continue          # 需要文字内容时，没有文字的块没有意义（上游的"空块"）
        out.append(TextBox(text=text, left=left + ox, top=top + oy,
                           right=left + width + ox, bottom=top + height + oy))
    return tuple(out)


def read_words(region: tuple[int, int, int, int]) -> tuple[TextBox, ...]:
    """便捷入口：抓取物理矩形并 OCR（探针/测试用）。

    ``region`` = (x, y, w, h)，绝对物理像素。失败一律返回空元组（调用方按
    "没有文字图"降级，不抛异常、不打断桌宠）。
    """
    if region[2] < 32 or region[3] < 32:
        return ()
    img = grab_screen()
    if img is None:
        return ()
    crop = crop_region(img, region)
    if crop is None:
        return ()
    return read_words_from_image(crop, (region[0], region[1]))
