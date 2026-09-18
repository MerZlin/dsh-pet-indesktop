# -*- coding: utf-8 -*-
"""批5.2a：单进程多窗的进程级共享子系统（agent_link / proactive / 全屏 watcher）。

feature flag ``experimental_single_process_spawn`` 关（现状）时本模块不被实例化
——每窗各自创建子系统，行为与 a2a3fc5 逐位一致（回退保险）。flag 开（多窗）时
``AppShell`` 持有一个共享实例，把呈现/探测事件**扇出**到全部窗，同时只运行一份
监视器/探测线程（省 (N-1) 份重复的后台轮询）。

设计要点（对应 BATCH5_SINGLE_PROCESS_DESIGN §4.7 / §4.8 / §8）：
- **agent_link**：AppShell 持有一个共享 ``AgentLinkManager``，经 ``MultiWindowProxy``
  把呈现事件扇出到所有**可见**窗（保持「全体跳舞」语义）；单个窗口关闭/隐藏/切换
  角色不关停共享监视器（``shutdown/pause/resume`` 为 no-op），真正的收口只在
  进程级 ``stop_all()``。dsh 插件安装授权弹窗随之只弹一次（进程级）。
- **proactive**：一份截屏/探测 → 广播到各窗；限流器保持**全局**语义
  （绑定主窗 config 目录下同名的 ``proactive_screen_state.json``，R8）。
- **全屏 watcher**：一份探测线程（1Hz 全屏 + 20Hz 光标轮询）→ 广播到各窗；
  ``MultiWindowProxy`` 只在「任一窗仍需要」时才继续轮询（G1 守卫保留）。
"""

from __future__ import annotations

import logging
import threading
import time
import weakref

from PySide6.QtCore import QObject, Signal

from . import platform_win
from . import vision as vision_mod
from .agent_link import AgentLinkManager
from .proactive import ProactiveScreenWatcher

log = logging.getLogger("dsh-pet-standalone")

# 存活共享子系统注册表（测试收口用，与 agent_link._LIVE_* 同纪律）。共享
# proactive 的 QTimer / bridge 在多窗代理下 parent 为 None，其 timeout/frame
# 连接从 Qt C++ 侧强引用住 shell 对象图，Python gc 回收不掉，解释器退出 GC
# 才最终化 → 原生访问违规。WeakSet 只弱引用子系统本身。
_LIVE_SHARED_SUBSYSTEMS: "weakref.WeakSet[SharedSubsystems]" = weakref.WeakSet()


class MultiWindowProxy:
    """单进程多窗的「窗集合替身」：把单窗接口扇出到全部窗。

    生命周期：作为共享 ``AgentLinkManager`` / ``ProactiveScreenWatcher`` 的
    ``win`` 实参。只读 ``shell._instances``（含各窗 ``win``），不持有窗对象
    引用；任一窗退出/重建后自动忽略它（逐窗探活读取）。

    呈现类方法只扇出到**可见**窗（隐藏窗不跳舞/不弹泡，与多进程各窗独立显隐
    等价）；状态类属性做成聚合值（任一可见 / 任一生效），供 Reducer 与
    proactive 的 G1 守卫读取。
    """

    def __init__(self, shell) -> None:
        self._shell = shell
        self.cfg = shell.config

    def _windows(self) -> list:
        return [inst.win for inst in self._shell.instances if inst.win is not None]

    def _visible_windows(self) -> list:
        return [w for w in self._windows() if getattr(w, "isVisible", lambda: True)()]

    # ---- 呈现扇出（只发给可见窗）----
    def show_bubble(self, text: str, duration_ms: int = 4500) -> None:
        # 联动气泡只发首个可见窗（与 show_alert 同策）：多窗同弹一条过程汇报
        # 既吵又重复，单窗展示即可；隐藏窗不弹的语义保持不变。
        for w in self._visible_windows():
            if hasattr(w, "show_bubble"):
                w.show_bubble(text, duration_ms=duration_ms)
                return

    # ---- 提醒扇出（交互式提醒：审批/问题/控制级 Watchdog）----
    def show_alert(self, text: str, *, subtitle: str = "", duration_ms: int = 0,
                   buttons: list | None = None, sticky: bool = True,
                   alert_id: str = "", priority: int = 3,
                   alert_type: str = "watchdog", metadata: dict | None = None) -> None:
        """把交互式提醒入队到**首个可见窗**（多窗只弹一处，避免每只桌宠重复轰炸）。

        ``agent_link`` 会先 ``hasattr(win, "show_alert")`` 决定是否走队列提醒；本方法
        确保共享 manager 的 ``win`` 是 proxy 时仍走交互式提醒（审批/控制按钮不丢失）。
        首个可见窗缺失 ``show_alert`` 时回退 ``show_bubble``（绝不因签名差异崩溃）。
        收起仍由 ``resolve_alert`` 全窗扇出兜底。
        """
        for w in self._visible_windows():
            method = getattr(w, "show_alert", None)
            if callable(method):
                method(text, subtitle=subtitle, duration_ms=duration_ms,
                       buttons=buttons, sticky=sticky, alert_id=alert_id,
                       priority=priority, alert_type=alert_type, metadata=metadata)
                return
        # 可见窗都不支持 show_alert：退化到首个可见窗的 show_bubble
        for w in self._visible_windows():
            if hasattr(w, "show_bubble"):
                try:
                    w.show_bubble(text, sticky=sticky, buttons=buttons,
                                  duration_ms=duration_ms if not sticky else 0)
                except TypeError:
                    w.show_bubble(text, duration_ms=max(duration_ms, 4500))
                return

    def resolve_alert(self, alert_id: str) -> None:
        """按 alert_id 在所有窗收起该提醒（含隐藏窗）：任意窗点按钮即全局收起。

        ``agent_link`` 的按钮回调/审批回写都经 ``self.win.resolve_alert`` 关闭气泡；
        扇出到全部窗，保证一个窗上点按钮/忽略后，其它窗上的同款气泡一并收起。
        """
        if not alert_id:
            return
        for w in self._windows():
            method = getattr(w, "resolve_alert", None)
            if callable(method):
                method(alert_id)
            elif hasattr(w, "hide_bubble"):
                w.hide_bubble()

    def clear_alerts(self) -> None:
        """清空所有窗的提醒队列并关闭当前提醒（DSH 离线/重启收口）。"""
        for w in self._windows():
            method = getattr(w, "clear_alerts", None)
            if callable(method):
                method()

    def hide_bubble(self) -> None:
        """主动收起所有窗当前气泡（审批结束/离线兜底）。"""
        for w in self._windows():
            method = getattr(w, "hide_bubble", None)
            if callable(method):
                method()

    @property
    def _bubble_suppressed(self) -> bool:
        # 设置窗抑制的聚合视图：任一窗打开设置窗即视为全局抑制。共享 manager 的
        # N2-a 节流门禁 ``getattr(self.win, "_bubble_suppressed", False)`` 据此读到
        # True，避免单窗设置期间共享管理器继续弹提醒/白占节流槽。
        return any(getattr(w, "_bubble_suppressed", False) for w in self._windows())

    @property
    def _sticky_bubble_active(self) -> bool:
        # 任一窗仍有常驻粘滞气泡（审批/控制提醒）时记为 True，供
        # ``dismiss_all_interactions`` 的提前返回判定使用（避免漏清理）。
        return any(getattr(w, "_sticky_bubble_active", False) for w in self._windows())

    @property
    def _alert_current(self):
        # 任一见窗当前正在展示提醒时返回该提醒，否则 None（_show_link_bubble 据此
        # 让联动气泡让路，避免在提醒/审批展示期间被普通联动气泡覆盖）。
        for w in self._windows():
            cur = getattr(w, "_alert_current", None)
            if cur is not None:
                return cur
        return None

    @property
    def _alert_queue(self) -> bool:
        # 聚合视图：任一窗提醒队列非空即视为激活（_show_link_bubble 据此让路）。
        return any(getattr(w, "_alert_queue", None) for w in self._windows())

    def request_link_anim(self, name: str) -> None:
        for w in self._visible_windows():
            if hasattr(w, "request_link_anim"):
                w.request_link_anim(name)

    def request_link_idle(self) -> None:
        for w in self._visible_windows():
            if hasattr(w, "request_link_idle"):
                w.request_link_idle()

    def mark_activity(self) -> None:
        for w in self._windows():
            if hasattr(w, "mark_activity"):
                w.mark_activity()

    def clear_pending_link_anim(self) -> None:
        for w in self._windows():
            if hasattr(w, "clear_pending_link_anim"):
                w.clear_pending_link_anim()

    def set_link_next_provider(self, provider) -> None:
        for w in self._windows():
            if hasattr(w, "set_link_next_provider"):
                w.set_link_next_provider(provider)

    def hold_bubble(self, seconds: float) -> None:
        for w in self._windows():
            if hasattr(w, "hold_bubble"):
                w.hold_bubble(seconds)

    def on_look_synced(self, user_text: str, reply: str) -> None:
        # 主动识屏答复同步进**各窗**的 AI 会话（各窗独立会话目录）
        for w in self._windows():
            cb = getattr(w, "on_look_synced", None)
            if callable(cb):
                try:
                    cb(user_text, reply)
                except Exception:
                    log.exception("主动识屏回复同步进会话失败")

    @property
    def cats(self) -> dict:
        # 联动动作池按「首个含动作包的窗」归类（角色不同动作名不同，缺失名 no-op）
        for w in self._windows():
            c = getattr(w, "cats", None)
            if c:
                return c
        return {}

    # ---- 聚合状态（Reducer / proactive G1 守卫）----
    def isVisible(self) -> bool:
        return any(getattr(w, "isVisible", lambda: True)() for w in self._windows())

    @property
    def _bubble_busy_until(self) -> float:
        vals = [getattr(w, "_bubble_busy_until", 0.0) for w in self._windows()]
        return max(vals) if vals else 0.0

    @property
    def _dragging(self) -> bool:
        return any(getattr(w, "_dragging", False) for w in self._windows())

    @property
    def _physics_mode(self) -> bool:
        return any(getattr(w, "_physics_mode", None) is not None for w in self._windows())

    @property
    def _click_effect_phase(self) -> int:
        vals = [getattr(w, "_click_effect_phase", 0) or 0 for w in self._windows()]
        return max(vals) if vals else 0

    @property
    def mouse_through(self) -> bool:
        return any(getattr(w, "mouse_through", False) for w in self._windows())

    @property
    def agent_link_manager(self):
        # G2.5 联动去重：读共享 agent_link 管理器（无共享时为 None）
        shared = getattr(self._shell, "_shared", None)
        return getattr(shared, "agent_link", None) if shared is not None else None


class SharedAgentLinkManager(AgentLinkManager):
    """进程级共享的 ``AgentLinkManager``：呈现扇出 + 生命周期按「任窗仍存活」处理。

    与每窗独立 manager 的差别：
    - ``shutdown()`` / ``pause()`` / ``resume()`` 为 no-op——单个桌宠窗口关闭、
      隐藏、切换角色都不该停掉其它窗共享的监视器（否则一窗退、全局断）；
    - 真正的收口只在进程级 ``stop_all()``（由 AppShell 在 aboutToQuit 调用）。
    """

    def __init__(self, proxy: MultiWindowProxy, config, **kw) -> None:
        super().__init__(proxy, config, **kw)
        self._stopped = False

    @property
    def presentation(self):
        """Compatibility facade for callers of the pre-split presentation API."""
        return self

    def show_link_bubble(self, text: str, *, important: bool = False, duration_ms: int = 4500) -> None:
        """Expose presentation's historical bubble entry point."""
        self._show_link_bubble(text, important=important, duration_ms=duration_ms)

    def pause(self) -> None:
        # 单窗隐藏不停共享监视器；呈现扇出已按「可见窗」过滤
        pass

    def resume(self) -> None:
        pass

    def shutdown(self) -> None:
        # 单窗关闭/切换角色不动共享监视器；全部退出由 stop_all() 收口。
        # 但基类的「过继给 QApplication」GC 安全收口必须继承：多窗代理 parent
        # 为 None 时 C++ 对象为 Python 持有，wrapper 经信号连接/闭包成环只能等
        # 循环 GC，跨线程析构带 QTimer/信号连接的 QObject 会腐化 Qt 事件队列
        # （interpreter 退出 access violation，见 AgentLinkManager 注释）。
        # 这里只接管生命周期、不关停监视器——嘲笑/直连测试退出时（conftest
        # _shutdown_live_for_tests 每测收口路径）同样要走该收口。
        AgentLinkManager._adopt_to_app_for_gc(self)

    def stop_all(self) -> None:
        """进程级收口：幂等，只真正关停监视器一次。"""
        if self._stopped:
            return
        self._stopped = True
        try:
            super().shutdown()
        except Exception:
            log.exception("关闭共享 Agent 管理器失败")


class SharedProactiveWatcher(ProactiveScreenWatcher):
    """进程级共享的 ``ProactiveScreenWatcher``：一份截屏/探测 → 广播到各窗。

    ``MultiWindowProxy`` 作为其 ``win``：G1 守卫按「任一可见/未交互」聚合，
    呈现经 ``show_bubble`` 扇出到全部可见窗。``pause`` / ``resume`` 为 no-op——
    单窗隐藏/显示不动共享定时器（G1 守卫逐 tick 判定可见性），与「限流器保持
    全局语义」一致。
    """

    def __init__(self, proxy: MultiWindowProxy, config) -> None:
        super().__init__(proxy, config)

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass

    def stop_all(self) -> None:
        """进程级收口：停掉共享定时器并作废在飞任务。"""
        self._timer.stop()
        self._generation += 1


class SharedFullscreenWatcher(QObject):
    """进程级共享的全屏/光标 watcher：一份探测线程 → 广播到各窗。

    Windows 下启动单个后台线程，以 1Hz 探测前台窗口全屏、20Hz 轮询系统光标
    可见性；通过 Qt 信号（queued→GUI 线程）扇出到全部注册窗。任一窗的
    ``_on_fullscreen_changed`` / ``_on_cursor_visibility_changed`` 照常处理。
    仅当「任一窗需要」时才真正探测（``_any_wants`` 自省），否则线程空转。
    """

    fullscreen_changed = Signal(bool)
    cursor_visibility_changed = Signal(str)

    def __init__(self, shell) -> None:
        super().__init__()
        self._shell = shell
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._fs_last = False
        self._fs_polls = 0
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="pet-shared-fs-watch")
        self._thread.start()
        log.info("共享全屏监视线程已启动")

    def stop(self) -> None:
        self._stop.set()

    def _windows(self) -> list:
        return [inst.win for inst in self._shell.instances if inst.win is not None]

    def _any_wants(self) -> bool:
        # 任一窗需要全屏自动隐藏或光标穿透（_watch_required 已含 Windows 平台判定）
        return any(
            getattr(w, "_watch_required", lambda: False)()
            for w in self._windows())

    def _any_fullscreen_wants(self) -> bool:
        return any(
            getattr(w, "auto_hide_fullscreen", False) for w in self._windows())

    def _any_cursor_wants(self) -> bool:
        return any(
            getattr(w, "_cursor_hidden_passthrough_enabled", lambda: False)()
            for w in self._windows())

    def _probe_fullscreen(self) -> bool:
        try:
            hit, detail = platform_win._fg_fullscreen_probe()
            if hit != self._fs_last:
                self._fs_last = hit
                log.info("共享全屏检测变化 hit=%s (%s)", hit, detail)
                self.fullscreen_changed.emit(hit)
            elif self._fs_polls % 15 == 0:
                log.info("共享全屏检测心跳 hit=%s %s", hit, detail)
            self._fs_polls += 1
            return hit
        except Exception:
            log.exception("共享全屏检测异常")
            return False

    def _poll_cursor(self) -> None:
        try:
            visibility = vision_mod.get_cursor_visibility()
            self.cursor_visibility_changed.emit(visibility)
        except (RuntimeError, AttributeError) as exc:
            log.debug("共享光标状态检测瞬时异常 (%s)", exc)
        except Exception:
            try:
                self.cursor_visibility_changed.emit('UNKNOWN')
            except (RuntimeError, AttributeError) as exc:
                log.debug("共享光标状态降级发射瞬时异常 (%s)", exc)

    def _loop(self) -> None:
        next_fullscreen = time.monotonic() + 1.0
        while not self._stop.wait(0.05):
            if not self._any_wants():
                # 无人需要则低频空转（1s 一醒），避免 20Hz 空转
                self._stop.wait(1.0)
                continue
            # 光标可见性：20Hz（每循环一次）
            if self._any_cursor_wants():
                self._poll_cursor()
            # 全屏探测：1Hz 节拍
            now = time.monotonic()
            if now < next_fullscreen:
                continue
            next_fullscreen = now + 1.0
            if self._any_fullscreen_wants():
                self._probe_fullscreen()


class SharedSubsystems:
    """批5.2a：单个进程的一整套共享子系统（agent_link / proactive / 全屏 watcher）。

    ``AppShell`` 在 flag 开时实例化它一次；各窗经 ``PetWindow`` 构造参数引用
    同一份 ``agent_link`` / ``proactive``，并订阅 ``fs`` 的广播信号。flag 关
    （现状）时本类不被实例化——每窗各自建子系统（逐位一致）。
    """

    def __init__(self, shell) -> None:
        self.proxy = MultiWindowProxy(shell)
        self.agent_link = SharedAgentLinkManager(self.proxy, shell.config)
        self.proactive = SharedProactiveWatcher(self.proxy, shell.config)
        self.fs = SharedFullscreenWatcher(shell)
        self.fs.fullscreen_changed.connect(shell._on_shared_fullscreen)
        self.fs.cursor_visibility_changed.connect(shell._on_shared_cursor)
        _LIVE_SHARED_SUBSYSTEMS.add(self)

    def start(self) -> None:
        self.fs.start()

    def stop_all(self) -> None:
        """进程级收口：停共享定时器/探测线程 + 释放 Qt 生命周期引用。

        生产路径由 ``AppShell._on_about_to_quit`` 在「全部退出」时调用；测试
        （conftest ``_close_qt_top_level_widgets`` → ``_shutdown_live_for_tests``）
        亦经本方法收口。除停表外还必须把 parent 为 None 的 Qt 对象过继给
        QApplication：否则 Qt C++ 侧连接强引用住整个对象图，Python gc 回收
        不掉，解释器退出 GC 才最终化 → 原生访问违规（见 _LIVE_SHARED_SUBSYSTEMS
        注释）。
        """
        self.proactive.stop_all()
        self.agent_link.stop_all()
        self.fs.stop()
        _release_qt_lifetimes(self)

    @classmethod
    def _shutdown_live_for_tests(cls) -> None:
        """收口未由测试显式 stop 的共享子系统（对齐 agent_link 同族防线）。"""
        for subs in tuple(_LIVE_SHARED_SUBSYSTEMS):
            try:
                subs.stop_all()
            except Exception:
                log.debug("测试收口共享子系统失败", exc_info=True)


def _release_qt_lifetimes(subs: "SharedSubsystems") -> None:
    """把共享子系统里 parent 为 None 的 Qt 对象过继给 QApplication。

    仅接管 Python/C++ 生命周期，不改变业务状态（与
    ``AgentLinkManager._adopt_to_app_for_gc`` 同治）。多窗代理下这些对象的
    parent 是伪装成窗口的 ``MultiWindowProxy``（无 ``winId``），构造时退化为
    None；其 ``timeout`` / ``frame_ready`` 连接从 Qt C++ 侧强引用住 shell
    对象图，Python gc 回收不掉。过继给与进程同寿的 QApplication 后，wrapper
    在任何线程被回收都只是空壳析构，不会跨线程删除带 QTimer/信号连接的
    QObject（interpreter 退出 access violation 根因）。
    """
    try:
        from PySide6.QtCore import QCoreApplication
        app = QCoreApplication.instance()
        if app is None:
            return
        for obj in (subs.proactive._bridge, subs.proactive._timer, subs.fs):
            try:
                if obj.parent() is None and obj.thread() is app.thread():
                    obj.setParent(app)
            except RuntimeError:
                pass
    except Exception:
        log.debug("共享子系统 Qt 生命周期过继失败", exc_info=True)
