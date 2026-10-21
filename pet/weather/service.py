# -*- coding: utf-8 -*-
"""天气服务：后台取数 + 冒泡落地（GUI 线程侧）。

**分层**（与 ``balance`` / ``festival_service`` 一致）：

- 纯逻辑与网络全在 ``pet/weather.py``（无 Qt，可独立单测）；
- 本模块只做三件事：**线程边界**、**忙门**、**把结果冒泡**。

**为什么必须后台取数**：中国天气网一次点击要连发两个请求（城市检索 + 实况），
慢代理/弱网下单请求就能等到 ``HTTP_TIMEOUT``；放在 GUI 线程会让桌宠整个卡住
（音乐播放器路径自动检测踩过同一个坑，菜单都会冻住）。取数线程是 daemon 且
带名字（``pet-weather``），崩了就崩了，回归日志里能一眼认出来。

**结果怎么回主线程**：``_WeatherBridge`` 是 GUI 线程构造的 QObject，信号跨线程
emit 自动走 queued 连接，槽里再碰 Qt 对象——这是仓库既有的唯一跨线程口径
（见 AGENTS.md「Keep Qt objects on their owning thread」与 ``_BalanceBridge``）。

**气泡通道**：走宿主既有的 ``show_bubble``（PetWindow 或是 overlay 壳），因此
自动落在提醒队列、飞行期抑制、设置页抑制与免打扰门控之内，不另开一条通道。

**失败口径**：两源都失败、或干脆没配城市、或功能被关掉，一律**如实冒泡**——
绝不静默（静默降级会让"功能坏了"变成用户唯一能看到的现象）。
"""

from __future__ import annotations

import logging
import threading
import time

from PySide6.QtCore import QObject, Signal

import shiboken6

from pet.weather import (
    NO_CITY_TEXT,
    TOTAL_BUDGET_SECONDS,
    WeatherClient,
    WeatherError,
    city_code_for,
    current_city,
)

log = logging.getLogger(__name__)

DISABLED_TEXT = "天气功能已在设置中关闭（设置 → 互动 → 天气）"


def _run_in_thread(work) -> None:
    """默认执行器：daemon 线程 + 可辨识线程名（回归日志里认得出来）。"""
    threading.Thread(target=work, daemon=True, name="pet-weather").start()


class _WeatherBridge(QObject):
    """后台线程 → GUI 线程的结果桥（queued 信号，见模块头）。

    ``done`` 的槽固定是服务自己的结算入口：**无论宿主还在不在**，请求生命周期
    都在这里收尾（M6）。
    """

    done = Signal(bool, str)

    def __init__(self, service, host) -> None:
        super().__init__()
        self._service = service
        self.host = host
        self.done.connect(self._settle)

    def _settle(self, ok: bool, text: str) -> None:
        self._service._settle(self, ok, str(text))


class WeatherService:
    """进程级天气服务（AppShell 持有；全部对外入口都在 GUI 线程调用）。"""

    #: 成功气泡的停留时长（比普通反馈长一点：用户点开就是为了看完这几个数字）
    BUBBLE_DURATION_MS = 8000
    #: 失败气泡的停留时长（要读得完两个源各自的原因）
    FAILURE_DURATION_MS = 12000

    def __init__(self, app, *, client=None, run_async=None) -> None:
        self._app = app
        self._client = client
        self._run_async = run_async or _run_in_thread
        self._busy = False
        self._bridge = None

    # ------------------------------------------------------------ 依赖
    def client(self) -> WeatherClient:
        """取数客户端（懒建，进程内复用；测试注入假客户端）。"""
        if self._client is None:
            self._client = WeatherClient()
        return self._client

    def _config(self):
        return getattr(self._app, "config", None)

    # ------------------------------------------------------------ 对外入口
    def show_weather(self, parent=None) -> None:
        """「查看天气」入口：后台取当前城市天气 → 桌宠气泡。

        ``parent`` = 气泡宿主（旧版单窗拓扑是 PetWindow，overlay 拓扑是壳），
        与 ``AppShell.show_balance`` 同口径：宿主缺 ``show_bubble`` 就回退主窗。
        """
        config = self._config()
        if config is not None and not bool(config.get("weather_enabled", True)):
            self._safe_bubble(parent, DISABLED_TEXT)
            return
        city = current_city(config) if config is not None else ""
        if not city:
            self._safe_bubble(parent, NO_CITY_TEXT)
            return
        if self._busy:
            return
        host = self._bubble_host(parent)
        if host is None:
            return
        code = city_code_for(config, city)
        self._busy = True
        bridge = _WeatherBridge(self, host)
        self._bridge = bridge
        try:
            self._run_async(lambda: self._worker(bridge, city, code))
        except Exception:  # noqa: BLE001 - 起线程失败也必须结算并如实冒泡
            log.exception("天气取数线程启动失败")
            bridge.done.emit(False, "天气获取失败：取数线程没起来")

    def _worker(self, bridge, city: str, code: str = "") -> None:
        """后台线程体：取数（含预算）→ 结果文本交回 GUI 线程。"""
        deadline = time.monotonic() + TOTAL_BUDGET_SECONDS
        try:
            reading = self.client().fetch(city, code=code, deadline=deadline)
        except WeatherError as exc:
            bridge.done.emit(False, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - 任何意外都变成一条如实的失败文案
            log.exception("天气取数异常")
            bridge.done.emit(False, f"天气获取失败（{city}）：{type(exc).__name__}: {exc}")
            return
        bridge.done.emit(True, reading.bubble_text())

    # ------------------------------------------------------------ 落地
    def _settle(self, bridge, ok: bool, text: str) -> None:
        """请求收尾（GUI 线程）：**任何**结束路径都先结算，再决定要不要冒泡。

        M6 的教训：此前只有"宿主还活着"的路径会解忙门，宿主在飞行中被销毁
        （切角色/退出/换拓扑）时忙门永锁，之后换新宿主再点也没反应。现在
        结算与呈现解耦——成功 / 失败 / 宿主已死 / 线程异常都在这里释放忙门并
        断开旧 bridge。
        """
        if self._bridge is bridge:
            self._bridge = None
            self._busy = False
        host = bridge.host
        if host is None or not shiboken6.isValid(host):
            log.info("天气结果到达时宿主已不存在，仅结算忙门：%s", text)
            return
        self._bubble(host, text, self.BUBBLE_DURATION_MS if ok else self.FAILURE_DURATION_MS,
                     ok=ok)

    def _bubble(self, host, text: str, duration_ms: int, *, ok: bool) -> None:
        if not text:
            return
        if ok:
            log.info("天气气泡：%s", text)
        else:
            log.warning("天气气泡（失败）：%s", text)
        try:
            host.show_bubble(text, duration_ms=duration_ms)
        except Exception:  # noqa: BLE001 - 气泡失败不影响服务状态
            log.exception("天气气泡展示失败")

    def _bubble_host(self, parent):
        host = parent
        if host is None or not callable(getattr(host, "show_bubble", None)):
            host = getattr(self._app, "win", None)
        if host is None or not callable(getattr(host, "show_bubble", None)):
            return None
        return host

    def _safe_bubble(self, parent, text: str) -> None:
        """不经过后台线程的直接冒泡（无城市/功能关闭：无需联网，也不动忙门）。

        刻意**不**走 ``_deliver``：那会解掉忙标志，而"打开设置把天气关掉"不该
        把正在飞的那次取数的忙门一起放掉（否则能叠出第二次取数）。
        """
        host = self._bubble_host(parent)
        if host is not None:
            self._bubble(host, text, self.FAILURE_DURATION_MS, ok=False)
