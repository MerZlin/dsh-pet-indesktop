# -*- coding: utf-8 -*-
"""批5.3：同角色共享解码链（进程内帧扇出）。

设计（BATCH53_DESIGN_glm53.md §2.3 / §3 / 附录）：
- ``DecodeFanoutHub`` 是**进程级**一个编排器（AppShell 持有；对窗口实现
  BrokerFacade 同形接口，调用点/参数名零改，仅换实现——改名留给 5.4/5.5）。
- 窗口侧 ``shareable_start``/``shareable_end`` 调用点、clip 侧
  ``_publish_sink``（发布镜像，每帧回调 ``on_frame(data, src_idx)``）与
  ``_feed_source``（消费：先有界 grant → 取帧入队 → 'end'/'abort' → 回退
  本地帧 0）钩子全部原样复用；**webm_clip.py 核心零改动**。
- **谁持有 clip**：各窗仍各自持有 MovieLibrary 与 clip（呈现对象不动）。hub
  持有「asset path → 源」映射；**谁起 reader**：同素材**首发窗**的 reader 兼任
  发布者（经 ``_publish_sink`` 每帧镜像）；后续同速窗的 reader 走
  ``_feed_source`` 进食，**不拉 ffmpeg**。
- **发布源**（``_Source``）持有一个 ``_SourceSink``，其 ``on_frame`` 把解码
  帧扇出到各订阅者环形缓冲（``_RingBuffer``, cap=4, drop-oldest）。帧对象
  （不可变 bytes）只存引用，零拷贝；环自带锁（跨线程安全），hub 的 GUI 线程
  只做挂/摘订阅者，不碰帧。
- **订阅者**（``_Subscription``)的 feed 会话（``_FanoutFeedSession.poll()``）
  返回 ``('frame'|'end'|'abort'|'none', data, src)``，消费协议与 ``_reader_feed``
  完全兼容。源帧号回绕（-stream_loop 从 N-1 回 0）即合成一次 ``('end')``；
  消费侧看门狗（覆盖叠加链：park 宽限 1s + re-arm ack 0.5s + fresh 拉起 ~0.25s，
  取 1900ms）无帧且无 end → ``('abort')``
  → 同一 reader 线程回退本地 ffmpeg（R2 承重）。
- **handover**：发布者离开且有订阅者时，摘旧 sink，把最老订阅者扶正为**新**
  发布者（``_publish_sink=source.sink`` + abort 其 feed 会话 → 其 reader 在
  ``_reader_feed`` 返回 False 后落回本地 ffmpeg 帧 0 起播，其 ``on_frame`` 开始
  喂源 → 其余订阅者环续到回绕帧为止，随后按圈末语义正常切走）。
- **节流/速度调和**（§2.4）：有效解码 divisor = min(在挂消费者期望值)；hub
  把有效值经 ``movie.set_decode_throttle`` 推给源 clip；源窗自身视觉降帧走
  ``_on_frame`` 既有跳帧分支（``decode_pace_external`` 标志置位后源窗
  ``_sync_movie_throttle`` 不再直接推、改经 ``_report_desired_throttle`` 上报
  hub）。播放速度**相等才共享**；不等 → 本地解码。

生命周期哲学（对齐批5.2a 共享子系统）：单窗 ``pause/shutdown`` = no-op，
进程级 ``stop_all`` 才是真收口（aboutToQuit）。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 参数表（设计稿附录：集中一处，落地后按实测校准）
# ---------------------------------------------------------------------------
# 环形缓冲容量（drop-oldest，保低延迟；对齐 queue(8) 语义减半）
RING_CAPACITY = 4
# 看门狗无帧预算（覆盖合法无帧叠加链，杜绝驻留误判）
# 复审 P2-4（批5.3）：合法无帧窗口会叠加——park 宽限 1.0s + re-arm ack 超时
# 0.5s + fresh ffmpeg 拉起 ~250ms（病态但合法，GUI 拥塞/连点风暴）可超 1.1s。
# 预算放宽到 1.9s 覆盖叠加链，避免误判 abort → 订阅者全部本地回退。
WATCHDOG_BUDGET_MS = 1900
# 速度相等判定 ε（float 配置往返精度）
SPEED_EPSILON = 1e-6
# feed budget_ms（兼容 ``_reader_feed`` 既有等待路径；进程内 attach 立即
# 就绪，不实际等待）。
FEED_BUDGET_MS = 600


# ---------------------------------------------------------------------------
# 环形缓冲（订阅者帧队列，cap=RING_CAPACITY, drop-oldest）
# ---------------------------------------------------------------------------
class _RingBuffer:
    """跨线程安全的有限环形缓冲（drop-oldest：保低延迟，对齐「队列满丢帧、
    源帧号照常推进」契约）。生产端 = 源窗 reader 线程；消费端 = 订阅者 reader
    线程。只存帧 bytes 引用，零拷贝。"""

    def __init__(self, capacity: int = RING_CAPACITY) -> None:
        self._capacity = max(1, int(capacity))
        self._dq: deque = deque(maxlen=self._capacity)
        self._lock = threading.Lock()

    def push(self, data, src_idx) -> None:
        with self._lock:
            self._dq.append((data, src_idx))  # 满时 deque 自动 drop 最旧

    def pop(self):
        """取最早一帧；空返回 None。"""
        with self._lock:
            if not self._dq:
                return None
            return self._dq.popleft()

    def clear(self) -> None:
        with self._lock:
            self._dq.clear()


# ---------------------------------------------------------------------------
# 发布侧 sink（``movie._publish_sink`` 的实现；_mirror_frame_to_sink 调用）
# ---------------------------------------------------------------------------
class _SourceSink:
    """发布源 sink：reader 线程每解码一帧经 ``on_frame(data, src_idx)`` 扇出到
    各订阅者环形缓冲。sink 挂/摘订阅者由 hub（GUI 线程）完成，帧扇出在 reader
    线程完成——两者只经 ``_lock`` 相交（attach/detach 与 on_frame 并发安全）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: list = []
        self._open = True

    def on_frame(self, data, src_idx) -> None:
        with self._lock:
            if not self._open:
                return
            subs = list(self._subscribers)
        for sub in subs:
            sub.ring.push(data, src_idx)

    def attach(self, sub) -> None:
        with self._lock:
            if self._open and sub not in self._subscribers:
                self._subscribers.append(sub)

    def detach(self, sub) -> None:
        with self._lock:
            try:
                self._subscribers.remove(sub)
            except ValueError:
                pass

    def close(self) -> None:
        with self._lock:
            self._open = False
            self._subscribers = []


# ---------------------------------------------------------------------------
# 订阅者 feed 会话（``_reader_feed`` 消费协议：poll/close）
# ---------------------------------------------------------------------------
class _FanoutFeedSession:
    """一个订阅者的 feed 运行期对象（reader 线程 poll）。绑定该订阅者的环形
    缓冲；``poll()`` 返回 ``('frame'|'end'|'abort'|'none', data, src)``。

    - 源帧号回绕（src < last_src）→ 合成一次 ``('end')``（圈界自然结束）；
    - 看门狗：无帧且无 end 超过 ``WATCHDOG_BUDGET_MS`` → ``('abort')``（R2）；
    - handover 主动 ``abort()`` → 下一次 poll 立即 ``('abort')``。
    """

    def __init__(self, ring: _RingBuffer) -> None:
        self._ring = ring
        self._last_src = -1
        self._stall_deadline = time.monotonic() + WATCHDOG_BUDGET_MS / 1000.0
        self._aborted = False
        self._lock = threading.Lock()

    def abort(self) -> None:
        with self._lock:
            self._aborted = True

    def reset_stall(self) -> None:
        self._stall_deadline = time.monotonic() + WATCHDOG_BUDGET_MS / 1000.0

    def poll(self):
        with self._lock:
            if self._aborted:
                return ("abort", None, None)
            if time.monotonic() > self._stall_deadline:
                logger.warning(
                    'fanout feed 看门狗超时（%dms 无帧无 end），回退本地解码',
                    WATCHDOG_BUDGET_MS)
                return ("abort", None, None)
        item = self._ring.pop()
        if item is None:
            return ("none", None, None)
        data, src = item
        src = int(src)
        with self._lock:
            if src < self._last_src:
                # 源帧号回绕（-stream_loop 从 N-1 回 0，或 handover 后从中圈跳
                # 0）：合成一次圈界自然结束 → 订阅者链照常推进（与单窗一圈结束
                # 逐位同语义）。
                self._last_src = src
                self.reset_stall()
                return ("end", None, None)
            self._last_src = src
            self.reset_stall()
        return ("frame", data, src)

    def close(self) -> None:
        with self._lock:
            self._ring.clear()


class FanoutFeed:
    """一次订阅尝试的句柄：对 ``_reader_feed`` 是 BrokerFeed 的鸭子类型。

    进程内 attach 同步完成，``ready`` 恒 True、``result`` 即 feed 会话——
    shm 的 ≤600ms grant 等待路径自然不触发。reader 放弃等待（回退本地）时
    ``expire()`` 闭锁并关闭会话，杜绝无主句柄。``result``/``ready``/``expire``
    经 ``_lock`` 串行化（跨线程安全）。
    """

    def __init__(self, session: _FanoutFeedSession,
                 budget_ms: int = FEED_BUDGET_MS) -> None:
        self._session = session
        self.budget_ms = int(budget_ms)
        self._lock = threading.Lock()
        self._expired = False

    @property
    def ready(self) -> bool:
        return True

    @property
    def result(self) -> _FanoutFeedSession:
        return self._session

    def expire(self) -> None:
        with self._lock:
            if self._expired:
                return
            self._expired = True
        try:
            self._session.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 订阅者（ring + feed session + feed 句柄 的组合记录）
# ---------------------------------------------------------------------------
class _Subscription:
    def __init__(self, source: "_Source", movie, name: str) -> None:
        self.source = source
        self.movie = movie
        self.name = name
        self.ring = _RingBuffer()
        self.session = _FanoutFeedSession(self.ring)
        self.feed = FanoutFeed(self.session)
        # 本窗期望解码 divisor（窗口经 _report_desired_throttle 上报；1=全速）
        self.desired = int(getattr(movie, 'decode_throttle_divisor', 1) or 1)

    def abort(self) -> None:
        """让本订阅者 feed 会话下一次 poll 立即返回 'abort'（handover/收口用）。"""
        self.session.abort()

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass
        self.ring.clear()


# ---------------------------------------------------------------------------
# 发布源（asset path -> 源记录）
# ---------------------------------------------------------------------------
class _Source:
    def __init__(self, asset: str, name: str, publisher_movie) -> None:
        self.asset = asset
        self.name = name
        self.publisher = publisher_movie
        self.sink = _SourceSink()
        self.subscriptions: list[_Subscription] = []  # FIFO（最老在前 → handover 选它）
        # 发布窗期望解码 divisor（一旦 hub 接管 pace，源窗不再直接推 divisor，
        # 改经 _report_desired_throttle 上报）。默认读取源窗当前推送值。
        self.publisher_desired = int(
            getattr(publisher_movie, 'decode_throttle_divisor', 1) or 1)
        self._pace_external = False


# ---------------------------------------------------------------------------
# DecodeFanoutHub（进程级控制器；对窗口实现 BrokerFacade 同形接口）
# ---------------------------------------------------------------------------
class DecodeFanoutHub:
    """进程级同角色共享解码链编排器（AppShell 持有）。

    ``enabled`` 门控：由 ``experimental_shared_decode``（默认开）且
    ``experimental_single_process_spawn``（多窗）双门快照决定。门关 = 每窗
    独立解码（批5.2 形态），``shareable_start`` 恒返回 ``'local'``。

    线程模型：``shareable_start``/``shareable_end``/``_report_desired_throttle``
    全在 GUI 线程（单进程单 GUI 线程）；``_SourceSink.on_frame`` 在源窗 reader
    线程；``_FanoutFeedSession.poll`` 在订阅者 reader 线程。hub 不碰帧，只挂/摘
    订阅者与推 pace。``bind``/``unbind`` 为 no-op（无 shm/QLocal 会话可绑，
    保留签名平稳窗口调用点）。
    """

    def __init__(self, *, enabled: bool = True) -> None:
        self._enabled = bool(enabled)
        self._sources: dict[str, _Source] = {}

    # ---- 开关 / 角色 -------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._enabled

    # ---- 绑定 / 解绑（hub 无会话可绑；保留签名平稳窗口调用点）---------------
    def bind(self, ipc_session) -> None:
        pass

    def unbind(self) -> None:
        pass

    # ---- 窗口层入口（window._switch 在 shareable movie start/end 时调用）-----
    def shareable_start(self, name, movie, path=None, fps=None,
                        total_frames=None) -> str:
        """shareable（idle 类）movie 即将 start() 前调用。

        判定序（全 GUI 线程）：
        1. movie 无 ``_feed_source``/``_publish_sink`` 能力（GifClip/测试桩）
           → ``'local'``；
        2. 无存活源（首发或源已亡）→ 建源：``movie._publish_sink = source.sink``
           → ``'publish'``（本窗照常本地起 reader，无额外动作）；
        3. |publisher_speed − movie.playback_speed| > eps → ``'local'``；
        4. movie 即源发布者（同窗 re-arm/回退重播）→ ``'publish'``；
        5. 其余 → 挂订阅：``movie._feed_source = FanoutFeed(session)``
           （ready=True, result=session 立即就绪）→ ``'feed'``。
        """
        if not self._enabled:
            return 'local'
        if not (hasattr(movie, '_publish_sink') and hasattr(movie, '_feed_source')):
            return 'local'  # GifClip/测试桩：无 fan-out 能力
        asset = self._asset_of(movie, path)
        if asset is None:
            return 'local'
        source = self._sources.get(asset)
        if source is None:
            source = _Source(asset, name, movie)
            self._sources[asset] = source
            self._set_publish_sink(movie, source.sink)
            self._set_feed_source(movie, None)
            self._set_pace_external(movie, False)
            return 'publish'
        if abs(float(getattr(movie, 'playback_speed', 1.0))
               - float(getattr(source.publisher, 'playback_speed', 1.0))) > SPEED_EPSILON:
            return 'local'  # 速度不等不共享
        if movie is source.publisher:
            self._set_publish_sink(movie, source.sink)
            self._set_feed_source(movie, None)
            return 'publish'
        sub = _Subscription(source, movie, name)
        source.subscriptions.append(sub)
        source.sink.attach(sub)
        self._set_feed_source(movie, sub.feed)
        self._set_publish_sink(movie, None)
        self._recompute_pace(source)
        return 'feed'

    def shareable_end(self, name, movie, natural: bool = True) -> None:
        """shareable movie 播完/停播后调用，幂等。"""
        if not self._enabled:
            return
        asset = self._asset_of(movie, None)
        source = self._sources.get(asset) if asset else None
        if source is None:
            self._cleanup_movie_hooks(movie)
            return
        if movie is source.publisher:
            self._publisher_leave(asset, source, movie)
        else:
            self._subscriber_leave(asset, source, movie)

    # ---- 发布侧 ------------------------------------------------------------
    def _publisher_leave(self, asset: str, source: _Source, movie) -> None:
        if not source.subscriptions:
            self._release_source(asset, source, movie)
            return
        # handover：选最老订阅者扶正为新发布者
        sub = source.subscriptions.pop(0)
        source.sink.detach(sub)
        sub.abort()  # 让 S 的 feed 会话返回 'abort' → 回退本地 ffmpeg 帧 0 起播
        source.publisher = sub.movie
        sub.movie._publish_sink = source.sink
        sub.movie._feed_source = None
        self._set_pace_external(sub.movie, True)
        # 新发布者（曾被订阅窗扇出）的期望 divisor 由其窗直接推（尚未被外部
        # pace 接管前），此处刷新以避免 handover 后第一拍用旧发布者的期望值。
        source.publisher_desired = int(
            getattr(sub.movie, 'decode_throttle_divisor', 1) or 1)
        self._recompute_pace(source)
        # 复审 P1-1：handover 后旧发布者的外部 pace 标志必须复位——否则它日后
        # 以订阅者身份再进场时 divisor 永久卡在旧值（交互中画面半速不自愈）。
        self._set_pace_external(movie, False)
        self._cleanup_movie_hooks(movie)

    def _subscriber_leave(self, asset: str, source: _Source, movie) -> None:
        sub = self._find_subscription(source, movie)
        if sub is None:
            self._cleanup_movie_hooks(movie)
            return
        source.subscriptions.remove(sub)
        source.sink.detach(sub)
        sub.close()
        self._set_feed_source(movie, None)
        if not source.subscriptions:
            # 最后订阅者离开：摘发布者 sink，源独播（发布窗继续自己 reader）
            self._release_source(asset, source, source.publisher)
        else:
            self._recompute_pace(source)

    # ---- 节流 / pace 调和 --------------------------------------------------
    def _recompute_pace(self, source: _Source) -> None:
        """有效解码 divisor = min(在挂消费者期望值)（任一窗活跃 → 1）。推给
        源 clip；源窗视觉降帧由其 ``_on_frame`` 既有跳帧分支保住（窗口层零新
        逻辑）。只在有订阅者时接管 pace（无订阅者 = 源窗独播，原窗自管）。"""
        if not source.subscriptions:
            return
        if not source._pace_external:
            # 首次接管：把发布窗当前期望刷新（其窗口仍直接管理 divisor 时读取）
            source.publisher_desired = int(
                getattr(source.publisher, 'decode_throttle_divisor', 1) or 1)
            self._set_pace_external(source.publisher, True)
            source._pace_external = True
        effective = source.publisher_desired
        for sub in source.subscriptions:
            if sub.desired < effective:
                effective = sub.desired
        effective = max(1, int(effective))
        setter = getattr(source.publisher, 'set_decode_throttle', None)
        if callable(setter):
            try:
                setter(effective)
            except Exception:
                logger.exception('fanout pace 推送给源 clip 失败: %s', source.asset)

    def _report_desired_throttle(self, movie, divisor: int) -> None:
        """窗口上报本窗期望解码 divisor（源窗被 pace_external 接管后也走这里
        而非直接推 movie）。hub 据此重算源 pace。"""
        if not self._enabled:
            return
        divisor = max(1, int(divisor))
        for source in self._sources.values():
            if source.publisher is movie:
                source.publisher_desired = divisor
                self._recompute_pace(source)
                return
            for sub in source.subscriptions:
                if sub.movie is movie:
                    sub.desired = divisor
                    self._recompute_pace(source)
                    return

    # ---- 收口 ---------------------------------------------------------------
    def shutdown(self) -> None:
        """单窗关闭/切角色 close：hub 为进程级，单窗退出不关它（no-op）。
        各窗的源/订阅早已由 ``shareable_end``（window closeEvent/_switch）逐素材
        收口，此处什么都不做。真收口只在进程级 ``stop_all()``。"""
        pass

    def stop_all(self) -> None:
        """进程级收口（aboutToQuit）：摘全部 sink/关闭全部 feed/清空源表。幂等。"""
        self._close_quiescent()

    # ---- helpers ------------------------------------------------------------
    @staticmethod
    def _asset_of(movie, path) -> str | None:
        p = path
        if p is None:
            p = getattr(movie, 'path', None)
            if p is None:
                return None
        return os.fspath(p)

    @staticmethod
    def _set_publish_sink(movie, sink) -> None:
        try:
            movie._publish_sink = sink
        except Exception:
            pass

    @staticmethod
    def _set_feed_source(movie, feed) -> None:
        try:
            movie._feed_source = feed
        except Exception:
            pass

    @staticmethod
    def _set_pace_external(movie, value: bool) -> None:
        setter = getattr(movie, 'set_decode_pace_external', None)
        if callable(setter):
            try:
                setter(bool(value))
            except Exception:
                pass
        elif hasattr(movie, 'decode_pace_external'):
            try:
                movie.decode_pace_external = bool(value)
            except Exception:
                pass

    @staticmethod
    def _cleanup_movie_hooks(movie) -> None:
        DecodeFanoutHub._set_publish_sink(movie, None)
        DecodeFanoutHub._set_feed_source(movie, None)

    @staticmethod
    def _find_subscription(source: _Source, movie):
        for sub in source.subscriptions:
            if sub.movie is movie:
                return sub
        return None

    def _release_source(self, asset: str, source: _Source, publisher_movie) -> None:
        if self._sources.get(asset) is source:
            del self._sources[asset]
        self._cleanup_movie_hooks(publisher_movie)
        self._set_pace_external(publisher_movie, False)
        source.sink.close()
        for sub in source.subscriptions:
            sub.close()
        source.subscriptions = []

    def _close_quiescent(self) -> None:
        for asset in list(self._sources):
            source = self._sources[asset]
            try:
                self._cleanup_movie_hooks(source.publisher)
                self._set_pace_external(source.publisher, False)
                source.sink.close()
                for sub in source.subscriptions:
                    sub.abort()
                    sub.close()
                source.subscriptions = []
            except Exception:
                logger.exception('fanout 收口源失败: %s', asset)
        self._sources.clear()
