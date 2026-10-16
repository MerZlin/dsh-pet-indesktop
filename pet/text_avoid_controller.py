# -*- coding: utf-8 -*-
"""避字移动控制器：后台刷文字图，移动决策时纯查表。

分工：
- ``pet/text_avoid.py``：纯逻辑（评分/落点）与 OCR/抓屏原语，零 Qt；
- 本文件：Qt 侧装配——后台刷新线程、文字图缓存、接进移动计划。

为什么必须后台刷新：一次条带 OCR 实测 137–190ms（全屏 911ms），放在 GUI 线程
会让动画卡一下。移动决策本身是纯查表（几百个候选 × 数百个词框），< 1ms。

为什么必须"看见自己背后"：桌宠压在字上时，抓屏拿到的只有桌宠自己。
**不要用 WDA_EXCLUDEFROMCAPTURE**（曾经这么做过，实机翻车）：置位后窗口在物理屏幕上
也不再渲染，用户看到的是"桌宠消失了"。现方案是 PrintWindow 直接抓"桌宠底下那个窗口
自己的渲染结果"，不碰任何窗口显示属性。详见 platform_win 里的历史注释。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import NamedTuple

from PySide6.QtCore import QObject, QTimer, Signal

from . import platform_win, text_avoid
from .text_avoid import CoordMap, plan_avoid

log = logging.getLogger('dsh-pet-standalone')

# 文字图保鲜期的**下限**（秒）：真实保鲜期 = max(本值, 6 × 最短刷新间隔)。
# 为什么下限要这么大：刷新改成**按需**之后，文字图的年龄不再由"刷新周期"决定，而是
# 由"两次决策的间隔"决定——桌宠可能安静几分钟才动一次，那时手里的图就有几分钟旧。
# 按几十秒判过期会让功能**永远不生效**（每次决策都发现图过期 → 什么都不做）。
# 折中：容许到 6 分钟；再配一个 2 分钟的低频兜底刷新（IDLE_REFRESH_SECONDS），
# 让"图太旧"这件事基本不会发生。
STALE_FLOOR_SECONDS = 360.0
# 空闲兜底刷新（秒）：按需刷新只管"有需求时"，但桌宠可能安静好几分钟——那样每次
# 决策都会遇到"图太旧"（于是永远不改道）。所以再给一个低频兜底：图再旧也要有个上限。
# 代价可忽略：一次识别约 3 秒 CPU，2 分钟一轮 ≈ 2.5% 单核。
IDLE_REFRESH_SECONDS = 120.0
# 两次识别的**最短间隔**（秒）——注意这是"限流"而不是"轮询周期"：
# 识别是**按需触发**的（要决定落点、或要判断"此刻压着字吗"时才刷），本值只负责
# 压住请求频率。实测单次识别的成本：起进程 + 载入模型 ≈ 1.7s，**推理本身 ≈ 3s**
# （上游 RapidOCR 只用 2 个 CPU 线程、关自旋，换来"不把整机 CPU 打满"）。
# 按需之后，桌宠一次移动（20~60 秒）最多刷一次，所以这个值小一点也不费 CPU；
# 反过来若做成定时轮询，10 秒一轮就是 ~30% 单核常驻。
REFRESH_DEFAULT_SECONDS = 10.0
# 抓屏条带的高度（物理像素）：盖住身体框上下各一个纵向搜索余量。
# 上限是实测取舍——条带越高 OCR 越慢（winrt 时代实测：全屏 911ms vs 240px 条带 137ms）。
BAND_MAX_PX = 900.0
BAND_MIN_PX = 200.0
# GUI 侧时钟节拍（毫秒）：只做"该不该发起一次刷新"的判定，本身不抓屏。
TICK_MS = 500
# 「立即反应」的最短间隔（秒）：发现被压住就打断动画走开是显眼行为，连续触发会
# 变成抽搐；给一个冷却，让每次走开都有完整的一段动画时间。
REACT_COOLDOWN_SECONDS = 6.0
# 连续白走时的退避档数（冷却按 2^n 翻倍，6s → 96s 封顶）。
# 为什么需要：实机日志里出现过"逃逸 → 日志说落点干净 → 6 秒后又压着字再逃逸"的
# 成对记录，用户看到的就是"屏幕没动它也一直在乱跑"。走得没用就该自己安静下来。
REACT_BACKOFF_STEPS = 4


class _SearchBox(NamedTuple):
    """一次决策的**搜索框**，同时就是 OCR 条带（绝对物理像素）。

    为什么必须是同一个矩形：规划器只允许在 OCR 过的地方挑落点。此前条带按整屏宽铺、
    纵向只盖"身体上下各一个 y_margin"，而规划器的纵向搜索范围是整屏——于是
    "往上一大截"的候选落在**没 OCR 过的区域**里，代价读成 0（看起来干净），
    桌宠会往那儿走。合成一个来源之后规则也变得可陈述：**看不到的地方不许选**。

    ``band`` 是实际送进 OCR 的矩形（只会 ⊇ 搜索框，绝不小于）；``cx_lo/cx_hi``
    是身体中心允许范围；``top_lo/top_hi`` 是身体顶允许范围。
    """

    band: tuple[int, int, int, int]
    footprint: tuple[float, float, float, float]
    cx_lo: float
    cx_hi: float
    top_lo: float
    top_hi: float
    body_w: float
    body_h: float
    cur_cx: float
    cur_top: float


class _PlanContext(NamedTuple):
    """一次规划的完整上下文（几何快照 + 判据 + 结果），供改道与逃逸判定共用。"""

    plan: object
    cmap: object
    sbr: object
    cfg: dict
    boxes: tuple
    scale: float
    body_w: float
    body_h: float
    cur_cx_px: float
    cur_top_px: float
    cx_lo_px: float
    cx_hi_px: float


def _cfg(host) -> dict:
    raw = host.cfg.get('text_avoid', {})
    return raw if isinstance(raw, dict) else {}


def enabled_for(host) -> bool:
    """功能是否对此刻的这个窗口生效（开关 + 平台 + OCR 可用）。"""
    return bool(_cfg(host).get('enabled', False))


class TextAvoidController(QObject):
    """一个桌宠窗口一份文字图缓存（多开时各窗各自刷新自己的条带）。"""

    words_ready = Signal(object)   # tuple[TextBox, ...]，worker 线程 → GUI 线程
    # 「立即反应」请求：控制器判定"此刻正压着字"，请窗口打断当前动画走开。
    # 控制器不直接碰窗口的移动接口——用信号让窗口自己决定要不要动（可拒绝）。
    escape_requested = Signal()

    def __init__(self, host) -> None:
        super().__init__(host)
        self._host = host
        self._boxes: tuple[text_avoid.TextBox, ...] = ()
        self._boxes_at = 0.0
        self._inflight = False
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._band: tuple[int, int, int, int] | None = None
        self._last_fingerprint: bytes | None = None
        self._thread: threading.Thread | None = None
        self._last_request = 0.0
        self._last_react = 0.0
        # 连续"白走"次数（逃逸之后文字图刷新过、仍然压着字）→ 指数退避。
        self._react_failures = 0
        # 最近一次逃逸的时刻；文字图刷新到它之后才能判断那趟有没有用。
        self._escaped_at: float | None = None
        self._escaped_count = 0                # 逃逸前压住的字数（判"这趟有没有用"）
        # 文字图**代次**：每拿到一张新图 +1。"同一张图只逃逸一次"按它判——比时间戳
        # 准确（时间戳说不清"图到底换没换"，还会被测试里的 inf 之类取值干扰）。
        self._map_generation = 0
        self._escaped_generation = -1
        self._ocr_ok: bool | None = None
        self.words_ready.connect(self._on_words_ready)
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._on_tick)

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        """开始跟随配置：定时驱动后台刷新（可重复调用）。

        可重复调用（设置页反复开关同一只桌宠时不重建线程）；``stop()`` 之后
        再 ``start()`` 要能重新起线程——所以这里判的是线程是否还活着，而不是
        是否创建过。
        """
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, daemon=True, name="pet-text-avoid")
            self._thread.start()
        if not self._timer.isActive():
            self._timer.start()
            # 启用时先刷一次：按需模式下没人会"顺手"刷新，而第一次决策值得有图可用
            self._request_refresh()

    def stop(self) -> None:
        self._timer.stop()
        self._stop.set()
        self._wake.set()

    # ------------------------------------------------------------ GUI 侧节拍
    def _on_tick(self) -> None:
        want = enabled_for(self._host)
        if not want:
            return
        if self._ocr_ok is None:
            self._ocr_ok = text_avoid.ocr_available()
            if not self._ocr_ok:
                log.info('避字移动：本机没有可用的本地 OCR（缺 rapidocr/onnxruntime '
                         '或模型文件），功能静默降级')
                return
        if not self._ocr_ok:
            return
        # **不再按最短间隔定时轮询**：识别按需发起（决策前 / 判定前），这里只做
        # "要不要走开"的判定——它自己没有图时也会顺手发起一次刷新（见 _maybe_react）。
        self._maybe_react()
        # 低频兜底：图太旧就补一次（避免长时间空闲后"每次决策都发现图过期" → 永不改道）
        if time.monotonic() - self._boxes_at > IDLE_REFRESH_SECONDS:
            self._request_refresh()

    def _request_refresh(self) -> bool:
        """发起一次后台刷新（按需入口）；返回是否真的发起了。

        限流两道：正在刷（``_inflight``）不重复发起；距上次发起不足
        ``refresh_seconds`` 也不发起——后者是**最短间隔**，不是轮询周期。
        非阻塞：抓屏 + 识别都在后台线程，调用方拿手里现有的图继续决策。
        """
        if self._inflight:
            return False
        try:
            interval = max(1.0, float(_cfg(self._host).get('refresh_seconds',
                                                           REFRESH_DEFAULT_SECONDS)
                                      or REFRESH_DEFAULT_SECONDS))
        except (TypeError, ValueError):
            interval = REFRESH_DEFAULT_SECONDS
        now = time.monotonic()
        if now - self._last_request < interval:
            return False
        request = self._current_band()
        if request is None:
            return False
        self._last_request = now
        self._inflight = True
        with self._lock:
            self._band = request
        self._wake.set()
        return True

    def _maybe_react(self) -> None:
        """「立即反应」：发现自己此刻正压着字 → 请求窗口打断动画走开。

        只在开关打开、有新鲜文字图、当前没有正在走的移动计划时判定；成功后进冷却，
        避免连续触发变成抽搐。判定用**当前位置**，与"下一次自选移动才改道"是两回事。

        **逃逸前先确认"真的有个干净地方可去"**：找不到就别走。否则走一趟还是压着字，
        冷却一到再来一趟，用户看到的就是"屏幕没动它也一直在乱跑"（实测日志里出现过
        恰好每隔 6 秒一对的逃逸）。走不通时按次数**指数退避**，让节奏自己静下来。
        """
        if not bool(_cfg(self._host).get('react_now', False)):
            return
        if getattr(self._host, '_move_plan', None) is not None:
            return
        now = time.monotonic()
        # 开了「被压住就立刻走开」= 要持续盯着"此刻压不压字"，所以这一档**按最短间隔
        # 持续刷新**（限流在 _request_refresh 里）；没开这一档时不走到这里，也就不会
        # 有空转的刷新。
        self._request_refresh()
        cost = self.current_cost()
        if cost is None:
            return
        if cost[0] == 0:
            # 干净了：退避清零（下次真压住字时能立刻反应）
            self._react_failures = 0
            self._escaped_at = None
            return
        if self._escaped_at is not None:
            if self._map_generation == self._escaped_generation:
                # **同一张文字图已经为它逃过一趟了**：再逃就是拿同一份数据做同一个决定，
                # 结果只会是 A→B→A→B 来回跑（实机日志里每 6 秒一对的逃逸就是这个）。
                # 等拿到新图再判——那时才知道上趟到底有没有用。
                #
                # 但**不能因此把她永远钉死**：万一刷新一直在失败（抓屏/识别报错），
                # 代次永远不会推进。所以图超过两倍最短间隔（且至少 30 秒）没更新时放行，
                # 宁可偶尔多跑一趟，也不要让她卡在字上一动不动。
                if time.monotonic() - self._boxes_at < self._react_stuck_seconds():
                    return
            # 换过图了：这一趟有没有用？没有（压住的字没减少）就记一次失败，冷却翻倍。
            self._escaped_at = None
            if cost[0] >= self._escaped_count:
                self._react_failures = min(self._react_failures + 1, REACT_BACKOFF_STEPS)
        if now - self._last_react < self._react_cooldown():
            return
        if not self.escape_is_worthwhile():
            # 附近没有**核实过干净**的落点：走开也只是换个地方压着字 → 原地不动。
            # 同样记一次失败：接下来每次判定间隔翻倍，直到它自己走到干净地方为止。
            self._react_failures = min(self._react_failures + 1, REACT_BACKOFF_STEPS)
            self._last_react = now
            log.info('避字移动：当前位置压 %d 字，但 %.0fpx 内没有干净落点，原地不动'
                     '（已白走 %d 次，下次判定间隔 %.0fs）',
                     cost[0], _cfg(self._host).get('max_travel', 700.0),
                     self._react_failures, self._react_cooldown())
            return
        self._last_react = now
        self._escaped_at = now
        self._escaped_count = cost[0]          # 记下逃逸前的"压几个字"，用来判这趟有没有用
        self._escaped_generation = self._map_generation
        log.info('避字移动：当前位置压 %d 字/%.0fpx²，立刻走开', cost[0], cost[1])
        self.escape_requested.emit()

    def _react_stuck_seconds(self) -> float:
        """"同一张图不许重复逃逸"这条规则的**兜底放行时间**（秒）。

        正常情况下新图几秒内就到（按需刷新），这条用不到；它防的是"刷新一直失败"
        导致代次永不推进时，逃逸被无限期压住。取两倍最短间隔、且不少于 30 秒。
        """
        try:
            refresh = float(_cfg(self._host).get('refresh_seconds',
                                                 REFRESH_DEFAULT_SECONDS)
                            or REFRESH_DEFAULT_SECONDS)
        except (TypeError, ValueError):
            refresh = REFRESH_DEFAULT_SECONDS
        return max(30.0, 2.0 * max(1.0, refresh))

    def _react_cooldown(self) -> float:
        """连续白走时指数退避（6s → 12s → 24s → 48s → 96s，封顶）。"""
        return REACT_COOLDOWN_SECONDS * (2 ** min(self._react_failures, REACT_BACKOFF_STEPS))

    def escape_is_worthwhile(self) -> bool:
        """此刻是否**真的**有个"干净地方"可去（供「立刻走开」决策用）。

        判据与改道走同一条规划路径：``plan.touched == 0`` 表示在 ``max_travel`` 范围内
        找到了干净落点；``>0`` 表示那个范围内没有干净点（规划已退化成"整屏挑压得最少的"）。
        逃逸只认前者——否则就是白跑一趟，还会连锁触发成"乱跑"。

        注意：这里判的是**精确干净点**，实际落点还要被吸附到整圈步态格子上
        （我在 4.2% 的情形里实测过"格子上没有干净点"）。那点残差交给白走退避兜底，
        不在这里重复一遍吸附（逃逸判定拿不到本次移动动画的步幅）。
        """
        try:
            sp = self._host._move_space()
            if sp is None:
                return False
            ctx = self._plan_at(sp, sp.cx)
        except Exception:
            return False
        return ctx is not None and ctx.plan.touched == 0

    def _host_physical_rect(self):
        """宿主的物理矩形（``CoordMap`` 只用它的原点）。

        优先问宿主自己：sprite 世界里的"宿主"不是窗口（桌宠是画在合成窗里的 sprite），
        与 ``_virtual_pos()`` 配对的物理原点只有它自己知道。拿不到再回落到按 HWND
        查 Win32 矩形（legacy 的 ``PetWindow`` 就是这条路）。

        为什么必须配对：``CoordMap.anchor = 逻辑位置 − 物理原点/缩放``。把 A 的逻辑
        位置配上 B 的物理矩形，anchor 会整体错开一个 A 到 B 的距离——实测后果是
        身体框与 OCR 条带一起偏了一个 sprite 的位置（条带跑到 x=-820）。
        """
        provider = getattr(self._host, '_coord_physical_rect', None)
        if callable(provider):
            try:
                rect = provider()
            except Exception:
                rect = None
            if rect is not None:
                return rect
        return platform_win.window_physical_rect(int(self._host.winId()))

    def current_cost(self) -> tuple[int, float] | None:
        """当前身体位置压了几个字（没有可用文字图或几何时返回 None）。

        **纯查询，无副作用**：需要图的那两条路（决策前的 `_plan_at`、判定前的
        `_maybe_react`）各自负责发起刷新，本方法不在这里偷偷触发识别。
        """
        boxes = self.text_boxes()
        if not boxes:
            return None
        geom = self._body_geometry()
        if geom is None:
            return None
        cmap, sbr, vp, scale = geom
        left, top = cmap.to_physical(vp.x() + sbr.x(), vp.y() + sbr.y())
        rect = (left, top, left + sbr.width() * scale, top + sbr.height() * scale)
        return text_avoid.text_cost(rect, boxes,
                                    bool(_cfg(self._host).get('cjk_only', True)))

    def _body_geometry(self):
        """取（CoordMap, 身体框局部矩形, 虚拟位置, 缩放）；任一取不到返回 None。"""
        host = self._host
        try:
            vp = host._virtual_pos()
            sbr = host._stable_body_local_rect()
            scale = float(host.devicePixelRatioF() or 1.0)
            rect = self._host_physical_rect()
        except Exception:
            return None
        if rect is None or sbr.isEmpty():
            return None
        return (CoordMap.from_window(vp.x(), vp.y(), rect, scale), sbr, vp, scale)

    def _geometry_box(self) -> "_SearchBox | None":
        """算出这次的搜索框 / OCR 条带 / 身体框；几何取不到返回 None。

        单一来源：``_current_band``（喂给 OCR）与 ``_plan_at``（喂给规划器）都从这里取，
        两处**不可能**再漂移。范围由 ``max_travel``（横向最远多走）与 ``y_margin``
        （纵向搜索余量）决定——所以调小它们会同时缩小搜索范围**和**识别成本。
        """
        host = self._host
        geom = self._body_geometry()
        if geom is None:
            return None
        cmap, sbr, vp, scale = geom
        try:
            avail = host._screen_available().availableGeometry()
        except Exception:
            return None
        cfg = _cfg(host)
        max_travel = max(0.0, float(cfg.get('max_travel', 700.0) or 0.0)) * scale
        y_margin = max(0.0, float(cfg.get('y_margin', 140.0) or 0.0)) * scale
        body_w = sbr.width() * scale
        body_h = sbr.height() * scale
        body_left, body_top = cmap.to_physical(vp.x() + sbr.x(), vp.y() + sbr.y())
        footprint = (body_left, body_top, body_left + body_w, body_top + body_h)

        a_left, a_top = cmap.to_physical(avail.left(), avail.top())
        a_right, a_bottom = cmap.to_physical(avail.right(), avail.bottom())
        edge = catalog_margin(host) * scale
        cur_cx = body_left + body_w / 2.0
        # 横向：当前位置 ± max_travel（还要装得下身体），再夹到屏内可用区
        left = max(a_left, cur_cx - max_travel - body_w / 2.0)
        right = min(a_right, cur_cx + max_travel + body_w / 2.0)
        # 纵向：身体 ± y_margin，且身体不许越过上下安全边距
        top = max(a_top + edge, body_top - y_margin)
        bottom = min(a_bottom - edge, body_top + body_h + y_margin)
        if bottom - top < body_h:            # 屏太矮/边距太大：退化成"只能待在这儿"
            top = bottom = body_top
        if right - left < body_w:
            left, right = a_left, a_right
        # 条带高度上限（OCR 成本随面积走）：超了就一起收，保持"条带=搜索框"
        if bottom - top > BAND_MAX_PX:
            bottom = top + BAND_MAX_PX
        # 条带下限（避免极小图上的退化）：只把**条带**放大，搜索框不动——
        # 多认一点是安全方向（条带 ⊇ 搜索框）。
        band_left, band_top = left, top
        band_right, band_bottom = right, bottom
        if band_right - band_left < BAND_MIN_PX:
            cx_mid = (band_left + band_right) / 2.0
            band_left = cx_mid - BAND_MIN_PX / 2.0
            band_right = cx_mid + BAND_MIN_PX / 2.0
        if band_bottom - band_top < BAND_MIN_PX:
            cy_mid = (band_top + band_bottom) / 2.0
            band_top = cy_mid - BAND_MIN_PX / 2.0
            band_bottom = cy_mid + BAND_MIN_PX / 2.0
        band = (int(round(band_left)), int(round(band_top)),
                int(round(band_right - band_left)), int(round(band_bottom - band_top)))
        return _SearchBox(band=band, footprint=footprint,
                          cx_lo=left + body_w / 2.0, cx_hi=right - body_w / 2.0,
                          top_lo=top, top_hi=bottom - body_h,
                          body_w=body_w, body_h=body_h,
                          cur_cx=cur_cx, cur_top=body_top)

    def _current_band(self):
        """当前该 OCR 的物理条带 + 桌宠身体框（绝对物理像素）。

        返回 ``(band, footprint)``；几何取不到返回 None。footprint 是"抓屏里属于
        桌宠自己"的那块：它既要在文字图里被剔掉（那是桌宠的像素，不是屏幕内容），
        又要用来告诉 PrintWindow 该去抓底下哪个窗口。
        """
        box = self._geometry_box()
        if box is None:
            return None
        return (box.band, box.footprint)

    # ------------------------------------------------------------ worker 侧
    def _loop(self) -> None:
        while not self._stop.is_set():
            if not self._wake.wait(0.5):
                continue
            self._wake.clear()
            if self._stop.is_set():
                return
            with self._lock:
                request = self._band
            if request is None:
                self._inflight = False
                continue
            try:
                self._refresh_once(*request)
            except Exception:
                log.debug('避字：后台刷新异常', exc_info=True)
                self._inflight = False

    def _refresh_once(self, band: tuple[int, int, int, int],
                      footprint: tuple[float, float, float, float]) -> None:
        img = text_avoid.grab_screen()
        if img is None:
            self._inflight = False
            return
        # 指纹把桌宠自己那块抹掉再算：它每帧都在动，不抹的话"屏幕没变"永远不成立。
        fingerprint = text_avoid.fingerprint(img, ignore_rect=footprint)
        if fingerprint == self._last_fingerprint:
            # 屏幕内容一个字都没变 → 省掉这一次 OCR，但要发一次"空更新"把保鲜期
            # 续上，否则静置久了文字图会过期。
            self.words_ready.emit(self._boxes)
            return
        crop = text_avoid.crop_region(img, band)
        if crop is None:
            self._inflight = False
            return
        # 要不要"文字内容"只取决于是否只避汉字：不需要时让工作端**只做检测**
        # （实测同一张图 2672ms → 1297ms）。两者必须配对——只检测的结果里没有文字，
        # 若还按"只避汉字"过滤，整张图会被判成"哪都没有字"。
        need_text = bool(_cfg(self._host).get('cjk_only', True))
        words = text_avoid.read_words_from_image(crop, (band[0], band[1]),
                                                 need_text=need_text)
        # 桌宠自己那块是它的像素（OCR 出来是噪声），剔掉，再从"底下那个窗口自己"
        # 的渲染结果里把真内容补回来——两者合起来才是完整的屏幕文字图。
        words = text_avoid.mask_boxes(words, footprint)
        under = text_avoid.read_under_pet(footprint, need_text=need_text)
        self._last_fingerprint = fingerprint
        self.words_ready.emit(words + under)

    def _on_words_ready(self, words) -> None:
        self._boxes = tuple(words)
        self._boxes_at = time.monotonic()
        self._map_generation += 1            # 新图 = 新一代（"一张图只逃逸一次"按它判）
        self._inflight = False

    def _stale_seconds(self) -> float:
        """文字图保鲜期：**跟着刷新间隔走**（下限 60 秒，取刷新间隔的 3 倍）。

        为什么不能写死：识别的成本决定了刷新间隔是几十秒量级（见模块头），保鲜期若
        短于它，每一轮刚刷出来的图当场就是"过期"的，功能等于没开。
        """
        try:
            refresh = float(_cfg(self._host).get('refresh_seconds',
                                                 REFRESH_DEFAULT_SECONDS))
        except Exception:
            refresh = REFRESH_DEFAULT_SECONDS
        return max(STALE_FLOOR_SECONDS, 3.0 * max(1.0, refresh))

    # ------------------------------------------------------------ 决策入口
    def text_boxes(self) -> tuple[text_avoid.TextBox, ...]:
        """当前可用的文字图（过期/空则返回空元组）。"""
        if not self._boxes or time.monotonic() - self._boxes_at > self._stale_seconds():
            return ()
        return self._boxes

    def adjust_move(self, sp, dir_sign: int, distance: float, room: float,
                    stride: float = 0.0):
        """移动决策入口：返回 ``(dir_sign, distance, room, target_cy, run_boost)``。

        功能关闭、OCR 不可用、文字图为空或已过期、几何取不到时，一律**原样返回
        入参 + target_cy=None + run_boost=1.0**（窗口侧继续用它原来的随机纵向漫游
        与用户播放速率）——本功能绝不改变"没数据时"的任何既有行为。

        ``stride`` 是本次移动动画的步幅（逻辑像素）：位移最终会被量化到整圈步态，
        所以落点要提前吸附到那条格子上，否则会走偏（见 text_avoid.snap_to_gait）。
        """
        passthrough = (dir_sign, distance, room, None, 1.0)
        ctx = self._plan_at(sp, sp.cx + dir_sign * distance)
        if ctx is None:
            return passthrough
        plan, cmap, cfg, boxes = ctx.plan, ctx.cmap, ctx.cfg, ctx.boxes
        # 纵向一律以"核对过的那一条"为准：勾选的目标 y 与实际决策时的 y 必须同一个，
        # 否则随机漫游会把身体挪到没核对过的高度上。
        target_cy = cmap.to_logical(0.0, plan.body_top)[1] - ctx.sbr.y()
        if not plan.changed:
            return (dir_sign, distance, room, target_cy, 1.0)

        # 吸附到实际走得到的整圈步态位置并复核：窗口会把位移量化到 loops×stride，
        # 直接返回"精确干净点"会再偏最多半个步幅（实测几十像素），可能又压回字上。
        snap_dir = 1 if plan.cx > ctx.cur_cx_px else -1
        room_px = ((ctx.cur_cx_px - ctx.cx_lo_px) if snap_dir < 0
                   else (ctx.cx_hi_px - ctx.cur_cx_px))
        snapped, touched, area = text_avoid.snap_to_gait(
            boxes=boxes, want_cx=plan.cx, cur_cx=ctx.cur_cx_px,
            stride=max(0.0, float(stride) * ctx.scale), room=room_px,
            cx_lo=ctx.cx_lo_px, cx_hi=ctx.cx_hi_px,
            body_w=ctx.body_w, body_h=ctx.body_h, body_top=plan.body_top,
            cjk_only=bool(cfg.get('cjk_only', True)),
        )
        if (touched, area) > (plan.proposed_touched, plan.proposed_area):
            # 吸附后的落点**比原来那个随机落点更差**（整圈格子上没有干净点时会发生，
            # 实测日志里出现过"原落点压 3 字 → 改走 1382px → 落地压 5 字"）。
            # 那就干脆不改道：白跑一趟还越改越差，用户看到的就是"乱跑"。
            log.info('避字移动：吸附后反而更差（原落点压 %d 字/%.0fpx²，可落点压 %d 字/'
                     '%.0fpx²）→ 不改道', plan.proposed_touched, plan.proposed_area,
                     touched, area)
            return (dir_sign, distance, room, target_cy, 1.0)
        new_cx = cmap.to_logical(snapped, 0.0)[0]
        new_distance = abs(new_cx - sp.cx)
        if new_distance < 1.0:
            return (dir_sign, distance, room, target_cy, 1.0)
        new_dir = 1 if new_cx > sp.cx else -1
        new_room = (sp.cx - sp.left) if new_dir < 0 else (sp.right - sp.cx)
        # 只有真的为避字改道的那一次移动才用"跑动速度"：普通漫游不该被加速。
        run_boost = float(cfg.get('run_speed', 1.0) or 1.0)
        run_boost = min(3.0, max(1.0, run_boost))
        log.info('避字移动：原落点压 %d 字/%.0fpx² → 改走 %.0fpx（改后压 %d 字/%.0fpx²，'
                 '跑动 %.2gx）',
                 plan.proposed_touched, plan.proposed_area, new_distance,
                 touched, area, run_boost)
        return (new_dir, new_distance, new_room, target_cy, run_boost)

    def _plan_at(self, sp, proposed_cx: float):
        """按给定落点（逻辑 x）跑一次规划；几何/文字图不可用时返回 None。

        `adjust_move` 与 `escape_is_worthwhile` 共用这一条路径，保证"能不能去"
        与"实际怎么去"用的是同一套判据与同一个几何快照。
        """
        host = self._host
        if not enabled_for(host):
            return None
        boxes = self.text_boxes()
        # **按需刷新的主触发点**：每次要挑落点都发起一次（限流由 _request_refresh 的
        # "最短间隔"负责，所以不会刷屏）。这里必须**每次都发**而不是"没图才发"——
        # 后者会让图只要还没过期就永远不刷新，判断一直基于同一张旧快照（实机后果：
        # 同一处位置连续 50 秒报出完全相同的"压 3 字/5957px²"，桌宠对着幻影文字
        # 每 6 秒逃逸一次、来回乱跑）。
        self._request_refresh()
        if not boxes:
            return None
        cfg = _cfg(host)
        try:
            vp = host._virtual_pos()
            sbr = host._stable_body_local_rect()
            scale = float(host.devicePixelRatioF() or 1.0)
            rect = self._host_physical_rect()
        except Exception:
            return None
        if rect is None or sbr.isEmpty():
            return None
        cmap = CoordMap.from_window(vp.x(), vp.y(), rect, scale)

        # 搜索框与 OCR 条带同源（_geometry_box）：看不到的地方不许选。
        box = self._geometry_box()
        if box is None:
            return None
        body_w, body_h = box.body_w, box.body_h
        cur_cx_px = cmap.to_physical(sp.cx, 0.0)[0]
        cur_top_px = cmap.to_physical(0.0, vp.y() + sbr.y())[1]
        cx_lo_px, cx_hi_px = box.cx_lo, box.cx_hi
        top_lo_px, top_hi_px = box.top_lo, box.top_hi

        plan = plan_avoid(
            body_w=body_w, body_h=body_h,
            proposed_cx=cmap.to_physical(proposed_cx, 0.0)[0], proposed_top=cur_top_px,
            cur_cx=cur_cx_px, cur_top=cur_top_px,
            cx_lo=cx_lo_px, cx_hi=cx_hi_px,
            top_lo=top_lo_px, top_hi=top_hi_px,
            boxes=boxes,
            step=float(cfg.get('step', text_avoid.DEFAULT_STEP_PX) or 0) * scale,
            max_travel=float(cfg.get('max_travel', 700.0) or 0) * scale,
            y_margin=float(cfg.get('y_margin', 140.0) or 0) * scale,
            cjk_only=bool(cfg.get('cjk_only', True)),
        )
        return _PlanContext(plan=plan, cmap=cmap, sbr=sbr, cfg=cfg, boxes=boxes,
                            scale=scale, body_w=body_w, body_h=body_h,
                            cur_cx_px=cur_cx_px, cur_top_px=cur_top_px,
                            cx_lo_px=cx_lo_px, cx_hi_px=cx_hi_px)


def catalog_margin(host) -> float:
    """与 window.py 建移动计划时用的纵向安全边距保持同源。"""
    from . import catalog
    return float(catalog.MOVE_MARGIN)
