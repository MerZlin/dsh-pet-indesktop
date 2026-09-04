# -*- coding: utf-8 -*-
"""
WebM-backed clip library（webm 主路线）。

使用 imageio-ffmpeg 自带的静态 ffmpeg 解码 640×360 透明 webm：
- read_frames(..., pix_fmt='rgba', bits_per_pixel=32, input_params=['-c:v','libvpx-vp9'])
  可正确保留 VP9 alpha，输出 RGBA 原始帧。
- imageio_ffmpeg 内部在 Windows 上使用 STARTUPINFO 隐藏控制台窗口，
  避免旧 ffmpeg 子进程方案导致的“窗口反复出现/消失”。

线程模型（B7 生命周期受控）：
- 后台 reader 线程只负责把 RGBA 字节放入有界队列；每帧附带素材源时间线
  帧号（0-based），队列满丢弃时源帧号照常推进（被丢弃的帧仍占用时间线
  槽位）——主线程拿到的显示帧索引在丢帧后依然锚定素材原始时间线；
- 主线程 QTimer 按视频 fps 从队列取帧，构造 QImage/QPixmap 并发出
  frameChanged(源时间线帧号)；
- 帧号契约（P1 复审）：frameChanged / currentFrameNumber 携带 0-based
  素材源时间线帧号（显示帧索引，= elapsed video time × fps）；播放计数
  _frame_index 是 1-based 主线程已消费帧数。降帧相位与末帧判断必须用
  显示帧索引，绝不能使用消费计数（队列满丢帧后两者不再相等）；
- 解码节流（批11，闲置降帧联动）：set_decode_throttle(ratio) 把消费端
  QTimer interval ×ratio，同时 reader 入队由「超时丢帧」切为「有界阻塞
  重试」——队列写满后 ffmpeg 的 stdout 管道写满、解码进程阻塞在 write()，
  解码速率随消费端联动下降到 ≈原始 fps/ratio。非闲置（ratio=1）路径
  与历史行为逐位一致（超时丢帧 + 全速解码）；
- 所有 Qt GUI 操作只发生在主线程。
- 同一 clip 最多 1 个 active reader + 有上限的退役 reader（_MAX_RETIRED_READERS）：
  stop() 主动 terminate 底层 ffmpeg 进程（_PopenCapture 捕获句柄），退役池超上限时
  强制回收最旧的；start() 前清空退役池，池内仍有存活 reader 时拒绝启动（防无上限累积）。
- cleanup() 对仍存活的退役 reader 保留追踪（绝不静默丢弃），等待后续 sweep 回收。
- Popen 并发串行化（批 6-8b，Windows access violation 主凶修复）：clip 名下任一
  Popen 的「操作」（poll/terminate/wait/kill/关管道）任意时刻只允许一个线程执行。
  播放 reader 的 Popen 由 reader 线程独占生命周期（创建/读/close/terminate/wait/
  kill），GUI stop() 只置 stop_evt + 经 _unblock_proc 做最小 TerminateProcess 解除
  阻塞读（所有权仍在 reader，GUI 绝不 wait/kill/关管道）；首帧解码进程同样由解码
  线程独占 close/terminate，GUI cancel_first_frame_warm 的完整 terminate 与之经
  _ff_proc_lock 互斥。阻塞读（generator 内 stdout.read）不持锁——只与最小
  terminate 并发（进程句柄 vs 管道句柄，不同原生对象），是解除阻塞读的既定安全
  配对。详见 _proc_lock/_ff_proc_lock 的注释。
- try-acquire 超时跳过的最终保障链（批 6-8b 收尾；R3 闭合 R2 复审 P1）：
  _unblock_proc / cancel_first_frame_warm 的锁超时跳过依赖 owner
  （reader/decode 线程）在 finally 杀进程——这是条件保证；闭合它的兜底是
  ——reader 的（thread, proc）随 stop() 进入退役池，孤儿注册表 sweep 的
  _reap_retired 在线程退出（finally 完成）后经 _confirm_retired_proc 补杀
  仍存活者；首帧进程在取消超时跳过时登记进 _unconfirmed_procs，sweep 的
  _sweep_unconfirmed_procs 在 owner 释放 _ff_proc_lock 后确认/补杀。
  两条兜底链对「poll 异常 / terminate+kill 后仍存活」都不静默丢句柄：
  确认失败保留追踪并累计有界重试，达到上限（_CONFIRM_KILL_MAX /
  _UNCONFIRMED_KILL_MAX）告警并标注 abandoned（保留追踪不再重试）——
  绝不漏杀、不无限静默重试、也不静默丢句柄。sweep 的补杀（poll/terminate）
  在注册表锁外执行（锁内只取快照，写回再进锁），单个 clip 的串行补杀不阻塞
  其他 clip 的 register/unregister。
"""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import weakref
import time
import types
import json
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication

from . import catalog
from . import perfstats
from .frame_cache import ByteBudgetLru

logger = logging.getLogger(__name__)

# 进程内元数据缓存：避免反复切换角色时重复调用 count_frames_and_secs
# （P2 审计 2026-09：key=(path|mtime|size) 随素材原地更新单调新增且永不
# 逐出——无界；改字节预算 + LRU，复用 frame_cache 的预算模式。默认
# 512KB ≈ 数千条（多角色 × 文件版本）量级，超出逐出最久未用：旧版本
# 失效条目最先走，仍在用的版本常驻；逐出后经磁盘缓存命中可零 ffmpeg 重探）。
_META_CACHE_MAX_BYTES = 512 * 1024
_META_CACHE = ByteBudgetLru(_META_CACHE_MAX_BYTES)

# 跨进程共享的元数据缓存文件：多开实例共享同一份，避免每个实例都拉起
# ffmpeg 探测 91 段动画。缓存以（文件 mtime + size）为失效依据。
# 条目数硬上限（P2 审计）：该文件跨进程/跨运行单调累积且从不清理——
# 每轮素材更新新增整套 key 后永久留存，长期无界；写路径在合并后按
# 「先写入先逐出」裁到上限（无访问时间戳，近似 LRU，仅防病态长尾），
# 20000 条 ≈ 3MB 文件，正常使用（多角色 × 版本数）远够不到。
_META_FILE_CACHE_PATH = Path(tempfile.gettempdir()) / "dsh-pet-media-meta-cache.json"
_META_FILE_CACHE_MAX_ENTRIES = 20000
_META_FILE_CACHE: dict | None = None
_META_CACHE_LOCK = threading.Lock()

# 前台同步解码等待后台预热完成的有限时长（毫秒）：超过此时长前台放弃等待、
# 直接自行解码，保证 GUI 线程不被后台首帧预热长时间卡住（N4）。
_FIRST_FRAME_SYNC_WAIT_MS = 120

# ------------------------------------------------------------ reader 生命周期（B7）
# 同一 clip 允许的退役 reader 上限：1 个 active reader + 1 个 retiring reader。
_MAX_RETIRED_READERS = 1
# 强制回收最旧退役 reader 时的有界 join 时长（秒）；真实 reader 的 ffmpeg 已被
# terminate，join 通常在毫秒级返回，此值只作为病态场景（卡死）的上限。
_RECLAIM_JOIN_TIMEOUT = 0.5
# 定时 sweep 对仍存活退役 reader 的 join 时长（秒）。
_SWEEP_JOIN_TIMEOUT = 0.2
# terminate 后等待进程退出的有界时长（秒）；超时则 kill 强杀（P2）。
# 在 GUI 线程调用时（stop/_reap_retired）该值同时是 GUI 阻塞上限，
# 正常进程 terminate 后毫秒级退出，此值只作为病态场景的兜底。
_PROC_TERMINATE_TIMEOUT = 0.5
# 结束标记（None）放入队列的总时限（秒，Fix C）：正常路径队列很快腾出
# 槽位、立即送达；仅病态（队列持续满且无人消费、stop_evt 缺失的历史僵尸）
# 时有界放弃并告警，绝不让 reader 永久空转。放弃后 finally 仍保证
# _terminate_proc 与 gen.close() 执行。
_END_MARKER_PUT_TIMEOUT = 5.0
# GUI 侧「最小解除阻塞 terminate」（_unblock_proc）获取 _proc_lock /
# _ff_proc_lock 的有限等待（秒，批 6-8b）：超过此时长说明 reader/解码线程
# 正在 finally 收尾（持锁），它自己会 terminate 进程——GUI 跳过是安全的
# （绝不漏杀），同时也绝不让 stop()/cancel_first_frame_warm() 因锁等待
# 明显变长（用户可感知的响应延迟红线）。
_PROC_LOCK_ACQUIRE_TIMEOUT = 0.2
# 首帧进程「取消后未确认退出」的有界补杀重试上限（批 6-8b 收尾；R3 语义）：
# cancel_first_frame_warm 的 try-acquire 超时跳过的进程登记进
# _unconfirmed_procs，孤儿注册表 sweep 周期补杀；owner（解码线程）持续
# 持锁不释放（g.close() 病态卡死）或补杀后进程仍存活（poll 异常 / kill
# 失败）时，达到此上限记录告警并**标注 abandoned**（条目保留在追踪中、
# 后续 sweep 不再重试）——不无限静默重试，也绝不静默丢句柄（与
# _LEAK_ATTEMPTS 的「不无限静默重试」同一原则）。
_UNCONFIRMED_KILL_MAX = 6
# 退役 reader 兜底确认（_confirm_retired_proc）失败后的有界重试上限
# （批 6-8b R3）：线程退出后 poll 异常 / terminate+kill 后仍存活时保留
# _Reader 记录并累计重试；达到此上限记录告警并标注 abandoned（保留追踪
# 不再重试）——绝不静默丢弃句柄（与 _UNCONFIRMED_KILL_MAX 同一原则）。
_CONFIRM_KILL_MAX = 6

# ------------------------------------------------------------ ffmpeg exe 探测串行化（批 6-8b）
# imageio 的 get_ffmpeg_exe() 在缓存未命中时用 subprocess.check_call 跑
# `ffmpeg -version` 探测（**无限等待**）。并发 reader 同时冷启动会各自拉起
# 探测进程：进程拉起风暴显著拖慢探测，极端负载下探测可超过 0.5s——快速
# start/stop 时被 stop 的 reader 会滞留在探测里迟迟不退（test_webm_clip_
# lifecycle::test_rapid_start_stop 的既有 flake 根因，基线 2/30 失败）。
# 用模块级锁串行化首次探测：一次命中缓存后所有后续调用零进程开销。reader
# 在拉起解码进程前探测、且探测后复查 stop_evt——被 stop 的 reader 绝不
# 拉起解码进程（省掉「拉起→_register 发现 stale→自终止」的浪费与延迟）。
_FFMPEG_EXE_LOCK = threading.Lock()


def _ensure_ffmpeg_exe() -> None:
    """串行化首次 ffmpeg exe 探测并预热缓存（幂等，任意线程可调用）。

    imageio_ffmpeg 不可用 / 探测失败时为无操作（read_frames 内部会再报错）。
    锁内探测保证并发调用方不重复拉起探测进程；探测完成后 lru_cache 命中，
    后续 get_ffmpeg_exe() 零子进程开销。
    """
    if imageio_ffmpeg is None:
        return
    with _FFMPEG_EXE_LOCK:
        try:
            imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            pass

# ------------------------------------------------------------ 孤儿 sweep 生命周期管理器（B7 审查 P2）
# 退役 reader 的回收由「独立生命周期管理器」持有：注册表记录所有
# 退役池非空的 clip，lazy QTimer 周期回收。状态与回收逻辑收编进
# `_OrphanClipRegistry`（N2b），模块级 `_register_orphan` /
# `_unregister_orphan` / `_reap_orphaned_clips` 只是委托到模块底部单例
# `_ORPHAN_REGISTRY` 的薄壳，clip 侧调用点与时序零改动。
#
# 为什么必须模块级单例持有：若 sweep timer 是 clip 的成员 QTimer，会形成
# 引用环（clip → timer → 连接 → clip），循环 GC 可能在 reader 线程仍在
# 收尾（finally/gen.close/进程 teardown）时回收整个 clip——clip 的属性
# （锁/队列/进程句柄）在 reader 线程使用中被释放，Windows 上原生崩溃。
# 模块级注册表强引用持有 clip，直到其退役池清空，杜绝该竞态；同时满足
# 「cleanup 后不残留调度」（cleanup 后 clip 自身不再安排任何 timer，
# 由管理器统一回收）与「cleanup 不丢追踪」（存活 reader 的记录被持有
# 到线程退出）。
class _OrphanClipRegistry:
    """跨实例孤儿回收管理器：退役 reader 的注册表 + lazy sweep timer（N2b）。

    线程归属：
    - 注册表（_clips）由 _lock 保护：register/unregister/holders 任意
      线程可调用（clip 的 cleanup/stop 在主线程，reader 线程不触碰）；
    - sweep QTimer 只能在 GUI 线程创建（须已有 QApplication）与启动；
      reap 由 Qt 事件循环在 GUI 线程触发。R3（R2 复审「锁内串行补杀的
      累计阻塞」闭合）：reap 不再全程持 _lock——锁内只取快照（list 强引用
      持有 clip，锁外窗口内不被 GC），锁外对每个 clip 做有界 join 与补杀
      （_reap_retired 的 join 时长受模块级 _SWEEP_JOIN_TIMEOUT 兜底、
      _sweep_unconfirmed_procs 的锁等待受 _PROC_LOCK_ACQUIRE_TIMEOUT
      兜底，正常毫秒级），状态写回（移出注册表 / 泄漏计数 / _ensure_timer）
      时再进锁；_reaping 标志防止锁外窗口期重复 reap 交错。此期间
      register/unregister 只会在「取快照 / 写回」两个短临界区短暂阻塞，
      不会被单个 clip 的串行补杀长时间阻塞。

    生命周期语义（与既有模块级实现逐条一致，只搬形态不改时序/加锁）：
    - register：强引用持有 clip（防 GC 与 reader 收尾竞态），并启动 timer；
    - reap：快照后对注册的 clip 做有界回收与首帧补杀，退役池清空且无
      未确认首帧进程者移出注册表；仍存活者累计回收次数，达到
      _LEAK_ATTEMPTS 阈值记录泄漏告警（不无限静默重试）并继续持有追踪；
    - unregister：移除追踪（退役池已清空时调用）。
    """

    _SWEEP_DELAY_MS = 500  # 惰性 sweep timer 的间隔（原 _ORPHAN_SWEEP_DELAY_MS）
    _LEAK_ATTEMPTS = 6  # 病态 reader 永不退出时的泄漏告警阈值（原 _ORPHAN_LEAK_ATTEMPTS）

    class _ArmInvoker(QObject):
        """跨线程编排信号载体（终审 P1-4）：worker 线程的 register 经
        arm_requested 信号排队到 GUI 线程执行 timer 创建/启动。实例在注册表
        实例化（模块导入 = 主线程 = 本应用的 GUI 线程）时创建，affinity 即
        主线程；在任意线程 emit 都安全（Qt 排队投递）。

        槽必须挂在 QObject（本类自身）上：排队投递到 QObject receiver 是
        Qt 久经验证的跨线程路径（与 PetWindow.fullscreen_changed 同款）；
        投递到非 QObject 的 plain callable 在跨线程场景不稳定（终审修复
        初版曾因此原生崩溃）。"""

        arm_requested = Signal()

        def __init__(self, registry: "_OrphanClipRegistry") -> None:
            super().__init__()
            self._registry = registry
            # AutoConnection：emit 线程 ≠ invoker 所在线程（主线程）时自动
            # 走 QueuedConnection，槽在 GUI 线程执行。
            self.arm_requested.connect(self._on_arm_requested)

        @Slot()
        def _on_arm_requested(self) -> None:
            self._registry._arm_sweep_timer_on_gui()

    def __init__(self) -> None:
        self._clips: "set[WebMClip]" = set()
        self._lock = threading.Lock()
        self._timer: "QTimer | None" = None
        # reap 防重入（R3）：reap 的锁外窗口期（补杀 poll/terminate）不持
        # _lock，第二个并发 reap 进入时直接跳过，避免与首个 reap 交错。
        self._reaping = False
        self._arm_invoker = self._ArmInvoker(self)

    def register(self, clip: "WebMClip") -> None:
        """把退役池非空的 clip 挂到注册表（强引用持有，防 GC 竞态）。

        终审 P1-4：timer 的创建/启动必须在 GUI 线程——本方法可被任意线程
        调用（如首帧进程收尾经 _track_unconfirmed_proc 在解码线程触发），
        若在无线程事件循环的 worker 线程里首次创建并 start QTimer，sweep
        永不触发（Qt 定时器只在其所属线程的事件循环里工作），退役 reader /
        未确认首帧进程将无人回收。非 GUI 线程经 _ArmInvoker 信号排队到
        GUI 线程执行（多次编排幂等）。
        """
        with self._lock:
            self._clips.add(clip)
        self._arm_sweep_timer()

    def _arm_sweep_timer(self) -> None:
        """确保 sweep timer 存在并启动（只在 GUI 线程执行创建/启动）。"""
        app = QApplication.instance()
        if app is None:
            return
        if QThread.currentThread() is app.thread():
            self._arm_sweep_timer_on_gui()
        else:
            self._arm_invoker.arm_requested.emit()

    def _arm_sweep_timer_on_gui(self) -> None:
        """创建（若未建）并启动 sweep timer——只在 GUI 线程执行。"""
        timer = self._ensure_timer()
        if timer is not None:
            timer.start()

    def unregister(self, clip: "WebMClip") -> None:
        with self._lock:
            self._clips.discard(clip)

    def holders(self) -> "set[WebMClip]":
        """当前被持有追踪的 clip 快照（加锁拷贝；诊断/测试用）。"""
        with self._lock:
            return set(self._clips)

    def _ensure_timer(self) -> "QTimer | None":
        """惰性创建 sweep timer（须在 GUI 线程；无 QApplication 时返回 None）。"""
        if self._timer is None:
            app = QApplication.instance()
            if app is None:
                return None
            self._timer = QTimer()
            self._timer.setSingleShot(True)
            self._timer.setInterval(self._SWEEP_DELAY_MS)
            self._timer.timeout.connect(self.reap)
        return self._timer

    def reap(self) -> None:
        """对注册的 clip 做有界回收；退役池清空者移出注册表。

        R3（R2 复审「sweep 锁内串行补杀的累计阻塞」闭合）：补杀的
        poll/terminate（clip._reap_retired 的兜底确认与
        clip._sweep_unconfirmed_procs 的补杀）**移出注册表锁**——锁内只取
        快照（快照 list 强引用持有 clip，锁外窗口内不被 GC），锁外对每个
        clip 做有界 join / 补杀确认（时长分别受 `_SWEEP_JOIN_TIMEOUT` /
        `_PROC_LOCK_ACQUIRE_TIMEOUT` 兜底，正常毫秒级），状态写回（移出
        注册表 / 泄漏计数 / `_ensure_timer()`）时再进锁。单个 clip 的串行
        补杀不再阻塞其他 clip 的 register/unregister；`_reaping` 标志防止
        锁外窗口期重复 reap 交错。病态 reader 多次回收仍不退出时记录泄漏
        告警（而不是无限静默重试）。
        """
        with self._lock:
            if self._reaping:
                return  # 已有 reap 在锁外窗口期运行：本次跳过（下次 timer 再来）
            self._reaping = True
            holders = list(self._clips)
        try:
            for clip in holders:
                try:
                    clip._reap_retired(join_timeout=_SWEEP_JOIN_TIMEOUT)
                except Exception:
                    pass  # clip 已销毁等：交由 GC 兜底
                try:
                    clip._sweep_unconfirmed_procs()
                except Exception:
                    pass
            with self._lock:
                for clip in holders:
                    if not clip._retired and not clip._has_unconfirmed_procs():
                        self._clips.discard(clip)
                if self._clips:
                    for clip in self._clips:
                        clip._orphan_reap_count = getattr(clip, '_orphan_reap_count', 0) + 1
                        if clip._orphan_reap_count >= self._LEAK_ATTEMPTS:
                            logger.warning(
                                'webm 退役 reader 多次回收仍存活（疑似泄漏，进程已 terminate）: %s',
                                clip.path,
                            )
                    timer = self._ensure_timer()
                    if timer is not None:
                        timer.start()
        finally:
            with self._lock:
                self._reaping = False


# 模块级单例：跨实例孤儿回收管理器（强引用持有 clip，防 GC 竞态）。
_ORPHAN_REGISTRY = _OrphanClipRegistry()


def _register_orphan(clip: "WebMClip") -> None:
    """把退役池非空的 clip 挂到模块级注册表（强引用持有，防 GC 竞态）。"""
    _ORPHAN_REGISTRY.register(clip)


def _unregister_orphan(clip: "WebMClip") -> None:
    _ORPHAN_REGISTRY.unregister(clip)


# ---- 首帧缓存总预算（内存瘦身批，2026-09-03）----
# 现状问题：每段动画的首帧缓存 ~0.88MB（640×360 RGBA QImage），按内容
# 规模自然累积（97 段 ≈ 85MB/实例），无总量上限。
# 预算 LRU：总量超预算时逐出「最久未用」的首帧——冷门动画下次冷播重新
# 解码（走既有 120ms 有界等待/逃生口路径），热门（待机/点击/最近播放）
# 常驻。锁序：clip._first_frame_lock → 注册表锁（单向）；逐出在注册表锁外
# 逐个取 victim 自己的锁，杜绝跨对象持锁嵌套（对照 P2-10 教训）。
_FIRST_FRAME_BUDGET_BYTES = 32 * 1024 * 1024  # 32MB ≈ 36 段热动画常驻
_first_frame_reg_lock = threading.Lock()
_first_frame_reg: list = []  # [(weakref(clip), bytes)]，尾部 = 最近使用
_first_frame_bytes = 0
_ffr_evict_seq = 0


def _clip_first_frame_bytes(clip) -> int:
    img = clip._first_image
    return 0 if img is None else img.width() * img.height() * 4


def _ffr_touch(clip, added_bytes: int = 0) -> list:
    """登记/置顶 clip 并记账；返回待逐出 (clip, token) 列表（调用方锁外处理）。

    token 机制（R3 复审 P1）：摘表与清空分两阶段，期间的重新登记会把
    clip._ffr_evict_token 复位为 None，使迟到的逐出在校验时跳过——
    热门 clip 不会被「过期的逐出决定」误清。
    """
    global _first_frame_bytes, _ffr_evict_seq
    victims = []
    with _first_frame_reg_lock:
        live = []
        for ref, nbytes in _first_frame_reg:
            other = ref()
            if other is None:
                _first_frame_bytes -= nbytes  # 死引用连带账目一并清
            elif other is clip:
                _first_frame_bytes -= nbytes  # 旧条目账目移除，下方按现状重计
            else:
                live.append((ref, nbytes))
        current = added_bytes or _clip_first_frame_bytes(clip)
        live.append((weakref.ref(clip), current))
        _first_frame_reg[:] = live
        _first_frame_bytes += current
        clip._ffr_evict_token = None  # 新登记/置顶 = 取消悬挂中的逐出
        while _first_frame_bytes > _FIRST_FRAME_BUDGET_BYTES and len(_first_frame_reg) > 1:
            # 从 LRU 头部找首个可逐出项：死引用/已清缓存顺手清账摘出；
            # _ffr_pinned（高频交互链：click/idle/turn/move/drag，由
            # MovieLibrary 标记）跳过不逐——否则低优先级随机动作池的预热
            # 浪涌会把交互首帧挤出去，用户点击/拖拽时被迫 GUI 同步解码
            # （实测：42 段低优先级 ≈38MB > 32MB 预算，交互首帧被逐出后
            # 看门狗抓到 117~160ms 切换卡顿）。
            victim = None
            removed_dead = False
            for i, (ref, nbytes) in enumerate(_first_frame_reg):
                c = ref()
                if c is None or c._first_image is None:
                    _first_frame_bytes -= nbytes  # 死引用/已清缓存：清账摘出，不算逐出
                    _first_frame_reg.pop(i)
                    removed_dead = True
                    break
                if getattr(c, '_ffr_pinned', False):
                    continue
                victim = (c, i)
                break
            if removed_dead:
                continue
            if victim is None:
                break  # 剩余全是常驻：预算对常驻条目转软上限
            c, i = victim
            _ffr_evict_seq += 1
            c._ffr_evict_token = _ffr_evict_seq
            victims.append((c, _ffr_evict_seq))
            _first_frame_bytes -= _first_frame_reg[i][1]
            _first_frame_reg.pop(i)
    return victims


def _ffr_unregister(clip) -> None:
    """clip 终结时摘出注册表并清账（cleanup 调用）。"""
    global _first_frame_bytes
    with _first_frame_reg_lock:
        live = []
        for ref, nbytes in _first_frame_reg:
            other = ref()
            if other is None or other is clip:
                _first_frame_bytes -= nbytes
            else:
                live.append((ref, nbytes))
        _first_frame_reg[:] = live
    clip._ffr_evict_token = None


def _ffr_evict(victims) -> None:
    """锁外逐出：逐个取 victim 自己的首帧锁，校验 token 未过期才清空。"""
    for victim, token in victims:
        try:
            with victim._first_frame_lock:
                if victim._ffr_evict_token != token:
                    continue  # 摘表后被重新登记/置顶：本次逐出决定已过期
                victim._first_image = None
                victim._ffr_evict_token = None
        except Exception:
            pass  # clip 可能正在 cleanup，逐出失败无碍（其清理路径自会释放）


def _reap_orphaned_clips() -> None:
    """模块级回收（薄壳）：委托给 _ORPHAN_REGISTRY.reap()。"""
    _ORPHAN_REGISTRY.reap()


class _Reader:
    """一个 reader 线程 + 其持有的底层 ffmpeg 进程句柄。

    kill_attempts / abandoned（批 6-8b R3）：线程退出后的兜底确认
    （_confirm_retired_proc）失败时保留记录并累计重试次数；达到
    _CONFIRM_KILL_MAX 上限标注 abandoned（保留追踪、sweep 不再重试），
    绝不静默丢弃句柄。
    """

    __slots__ = ("thread", "proc", "kill_attempts", "abandoned")

    def __init__(self, thread: threading.Thread, proc: subprocess.Popen | None) -> None:
        self.thread = thread
        self.proc = proc
        self.kill_attempts = 0
        self.abandoned = False

    def join(self, timeout: float | None = None) -> None:
        self.thread.join(timeout)


class _PopenCapture:
    """跨线程透明的 subprocess.Popen 包装，用于捕获 reader 线程拉起的 ffmpeg 进程句柄。

    - 安装一次（幂等、加锁防并发）：把 imageio_ffmpeg._io 模块内的 `subprocess`
      引用替换为一个代理模块（其余属性原样透传、仅 Popen 换成 _wrapped），
      只影响 imageio_ffmpeg 的 Popen 调用；进程内全局 subprocess.Popen 保持原样
      ——不能全局替换，否则会破坏 `class X(subprocess.Popen)` 这类继承用法
      （asyncio.windows_utils 就是这样，会导致 unittest.mock 导入失败）。
    - 仅「进入 capture 上下文」的线程（reader 线程）会把新建进程记入自己的
      _procs 列表并可即时回调（on_process）；其余线程完全无感。
    - 即时回调让 stop() 在 reader 尚处于 ffmpeg 头部解析（可能卡住）时也能
      拿到进程句柄并主动 terminate，而不是等 reader 自己退。
    """

    _install_lock = threading.Lock()
    _installed = False
    _real_popen = subprocess.Popen
    _local = threading.local()

    def __init__(self, on_process=None) -> None:
        self._on_process = on_process
        self._procs: list[subprocess.Popen] = []
        self._prev: _PopenCapture | None = None

    def __enter__(self) -> "_PopenCapture":
        self._install()
        self._prev = getattr(self._local, "capture", None)
        self._local.capture = self
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._prev is None:
            try:
                del self._local.capture
            except AttributeError:
                pass
        else:
            self._local.capture = self._prev
        return False

    @property
    def process(self) -> subprocess.Popen | None:
        """最近拉起的进程（解码进程；ffmpeg exe 探测进程在其之前）。"""
        return self._procs[-1] if self._procs else None

    @classmethod
    def _install(cls) -> None:
        if cls._installed:
            return
        with cls._install_lock:
            if cls._installed:
                return
            cls._real_popen = subprocess.Popen
            io_mod = sys.modules.get("imageio_ffmpeg._io")
            if io_mod is not None and getattr(io_mod, "subprocess", None) is subprocess:
                proxy = types.ModuleType("subprocess")
                proxy.__dict__.update(subprocess.__dict__)
                proxy.Popen = cls._wrapped  # type: ignore[assignment]
                io_mod.subprocess = proxy
            cls._installed = True

    @classmethod
    def _wrapped(cls, *args, **kwargs):
        proc = cls._real_popen(*args, **kwargs)
        state = getattr(cls._local, "capture", None)
        if state is not None:
            state._procs.append(proc)
            if state._on_process is not None:
                try:
                    state._on_process(proc, args[0] if args else None)
                except Exception:
                    pass
        return proc


def _load_meta_file_cache() -> dict:
    try:
        raw = json.loads(_META_FILE_CACHE_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _prune_meta_file_cache(cache: dict) -> None:
    """磁盘 meta 缓存条数上限（P2 审计）：跨进程/跨运行单调累积且从不
    清理——无界；在写路径（读盘合并后）与加载路径按「先写入先逐出」裁到
    _META_FILE_CACHE_MAX_ENTRIES（无访问时间戳，近似 LRU 的卫生上限，
    预算为硬上界）。正常使用远够不到；逐出条目由需要它的实例以一次
    ffmpeg 探测自愈，无正确性影响。"""
    while len(cache) > _META_FILE_CACHE_MAX_ENTRIES:
        cache.pop(next(iter(cache)))


def _get_meta_file_cache() -> dict:
    global _META_FILE_CACHE
    if _META_FILE_CACHE is None:
        _META_FILE_CACHE = _load_meta_file_cache()
        _prune_meta_file_cache(_META_FILE_CACHE)  # 历史超限文件：内存即有界
    return _META_FILE_CACHE


def _meta_cache_file_lock():
    """跨进程互斥锁文件（批 6-8b 修 3）：让「读盘→合并→原子替换」成为
    read-modify-write 临界区，保证多开实例的缓存单调累积——后写进程不会用
    旧进程内快照覆盖先写进程刚加入的条目（5.6sol 全审 P2）。

    返回持锁文件对象（调用方在 finally 中 close 即释放锁）；平台不支持
    （非 Windows/POSIX）或锁获取超时/失败时返回 None——调用方退化为纯
    「写前重读合并」（仍优于旧实现的进程内快照覆盖，仅余极小竞态窗口，
    且每个条目仍以（mtime+size）key 幂等）。锁文件与缓存文件分离：
    缓存文件用 tmp+replace 原子替换，锁文件固定不变（不随替换消失）。
    """
    lock_path = _META_FILE_CACHE_PATH.with_suffix(
        _META_FILE_CACHE_PATH.suffix + ".lock"
    )
    try:
        f = open(lock_path, "a+b")
    except OSError:
        return None
    try:
        # 保证至少 1 字节可锁（msvcrt.locking 从当前位置起锁；空文件位置 0
        # 也可锁，写 1 字节更稳）
        f.seek(0, 2)
        if f.tell() == 0:
            f.write(b"\x00")
            f.flush()
        deadline = time.monotonic() + 1.0  # 有界等待，绝不长期阻塞
        while True:
            try:
                f.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return f
            except OSError:
                if time.monotonic() >= deadline:
                    f.close()
                    return None
                time.sleep(0.005)
    except Exception:
        try:
            f.close()
        except Exception:
            pass
        return None


def _save_meta_file_cache_entry(key: str, frames: int, duration: float) -> None:
    global _META_FILE_CACHE
    try:
        with _META_CACHE_LOCK:
            lock = _meta_cache_file_lock()
            try:
                # 写前重读磁盘合并（read-modify-write，批 6-8b 修 3）：其他
                # 进程可能刚写入了新条目，绝不用进程内旧快照覆盖它们——
                # 缓存单调累积，多开预热不重复探测。跨进程临界区由
                # _meta_cache_file_lock 提供（有界等待，失败退化为重读合并）。
                cache = _load_meta_file_cache()
                cache[key] = {
                    "frames": frames,
                    "duration": duration,
                }
                # 条数上限（P2 审计）：合并后裁到硬上限，防长期跨运行无界增长
                _prune_meta_file_cache(cache)
                # 进程内快照同步为合并结果（原子换引用；后续读取即命中）
                _META_FILE_CACHE = cache
                # tmp 名带 PID：共享临时目录下防符号链接预占攻击与多实例互抢
                tmp = _META_FILE_CACHE_PATH.with_suffix(f".{os.getpid()}.tmp")
                tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
                tmp.replace(_META_FILE_CACHE_PATH)
            finally:
                if lock is not None:
                    try:
                        lock.close()
                    except OSError:
                        pass
    except OSError:
        pass

try:
    import imageio_ffmpeg
except Exception as exc:  # pragma: no cover - 依赖缺失时无法使用 webm 路线
    imageio_ffmpeg = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


class WebMClip(QObject):
    """与窗口层期望的媒体播放器接口兼容。"""

    available = imageio_ffmpeg is not None

    frameChanged = Signal(int)
    finished = Signal()
    errorOccurred = Signal(str)

    def __init__(self, path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.path = path
        self._w = catalog.CANVAS_W
        self._h = catalog.CANVAS_H
        self._bpp = 4  # RGBA

        # 元数据（惰性填充；由 MovieLibrary 并行 warm 或首次使用时读取）
        self._frame_count = 0
        self._duration = 0.0
        self._fps = 24.0
        self.playback_speed = 1.0

        # 播放状态
        self._queue: queue.Queue = queue.Queue(maxsize=8)
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        # 当前 active reader 持有的 ffmpeg 进程句柄（_reader_lock 保护，reader 线程
        # 注册、GUI 线程 stop 时读取/清空并 terminate）。
        self._reader_proc: subprocess.Popen | None = None
        self._reader_lock = threading.Lock()
        # Popen 操作串行化锁（批 6-8b）：播放 reader 的 ffmpeg Popen 的「操作」
        # （poll/terminate/wait/kill/关管道）任意时刻只允许一个线程执行。Windows
        # 上两线程并发操作同一 Popen（如 GUI stop 的 terminate/wait 与 reader
        # finally 的 _terminate_proc/gen.close() 关管道）是原生竞态崩溃
        # （access violation）的根因。与 _reader_lock（状态锁，瞬时持有）分离：
        # terminate/gen.close 等最长可达 ~1s 的操作不阻塞状态链（_register
        # 登记、start() 残留摘取等）。阻塞读（generator 内 stdout.read）不持锁
        # ——它只与 GUI 的「最小 terminate」并发（进程句柄 vs 管道句柄，不同
        # 原生对象），是解除阻塞读的既定安全配对（B7 注释「正在阻塞读管道的
        # reader 会因进程终止而立即解除阻塞退出」）。
        self._proc_lock = threading.Lock()
        # 首帧解码进程的 Popen 操作锁（批 6-8b）：与 _proc_lock 同语义，独立
        # 成锁避免首帧解码收尾（g.close() 最长 ~1.5s 病态）阻塞播放 reader 的
        # stop 解除阻塞——两类 Popen 互不相交，锁也互不相交。
        self._ff_proc_lock = threading.Lock()
        # reader 已拉起并登记 ffmpeg 进程（或确定无进程）的信号；每轮 start() 重建。
        self._reader_ready = threading.Event()
        # 退役 reader 池（有硬上限）：thread + 其 ffmpeg 进程句柄的记录列表。
        self._retired: list[_Reader] = []
        # 解码节流比率（闲置降帧联动，批11）：1 = 不节流（非闲置路径零变化）；
        # >1 时消费端 QTimer interval ×ratio、reader 按目标呈现节奏阻塞
        # （q.put 有界重试不丢帧）——管道写满让 ffmpeg 阻塞在 write()，
        # 解码速率随消费端联动下降 ≈ 原始帧率/ratio。比率由窗口层经
        # set_decode_throttle 推送（可配置接口，默认跟随闲置降帧除数）。
        # 主线程写、reader 线程读（int 赋值在 CPython 下原子，GIL 保证，
        # 与 _generation 的跨线程读取同一模式）。必须在 _timer 初始化前
        # 赋值（_timer_interval 会读它）。
        self._decode_throttle_divisor = 1
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(self._timer_interval())
        self._timer.timeout.connect(self._poll)
        # P3 broker（共享解码，默认 None = 今天的行为逐位不变）：
        # - _publish_sink：coordinator 播放时置（facade 在 start() 前设置）——
        #   reader 线程每解码一帧回调 sink.on_frame(data, src_idx)，只做镜像；
        # - _feed_source：消费端置（facade 在 start() 前设置）——reader 线程
        #   先有界等待 grant（feed-pending，≤600ms），grant 后从共享内存取帧
        #   入队；deny/超时/断流 → 同一 reader 线程内回退本地 ffmpeg（帧 0）。
        self._publish_sink = None
        self._feed_source = None
        # cleanup 后 clip 终结：不再启动新 reader；退役池回收由模块级
        # 生命周期管理器持有（见模块头注释，P2）。
        self._cleaned = False

        self._current_image: QImage | None = None
        self._current_pixmap: QPixmap | None = None
        self._first_image: QImage | None = None
        self._ffr_evict_token = None  # 首帧预算 LRU 的逐出代次（见模块级注册表注释）
        # 首帧解码原子认领（N4）：warm_first_frame（后台）与 _decode_first_frame_sync
        # （前台）同一时间只有一个执行者。_first_frame_done 在 _first_image 写入后
        # set，供前台有界等待复用后台解码结果（零重复解码）。
        self._first_frame_lock = threading.Lock()
        self._first_frame_done = threading.Event()
        # 首帧解码生命周期（P1-2）：与播放 reader 同一回收体系。
        # _first_frame_gen 是首帧解码代次：cancel_first_frame_warm/cleanup 时自增，
        # 使在飞解码的结果作废（不写入缓存）；_first_frame_procs 登记在飞首帧
        # 解码拉起的 ffmpeg 进程句柄（_reader_lock 保护），取消/cleanup 时主动
        # terminate，隐藏/切角色后不再有不受控的后台 ffmpeg 存活。
        self._first_frame_gen = 0
        self._first_frame_procs: set = set()
        # 取消时 try-acquire 超时跳过、尚未确认退出的首帧进程（批 6-8b 收尾；
        # R3 条目格式 [proc, attempts, abandoned]）：_reader_lock 保护。
        # cancel_first_frame_warm 的超时跳过依赖解码线程 finally 的 g.close()
        # 杀进程——该保证是条件性的（g.close 异常被吞等病态路径会漏），登记后
        # 由孤儿注册表 sweep（_sweep_unconfirmed_procs）在 owner 释放
        # _ff_proc_lock 后确认/补杀；确认失败保留条目并累计有界重试，达到上限
        # 告警并标注 abandoned（保留追踪不再重试）——闭合「取消绝不留存活
        # ffmpeg」的最终保障，绝不静默丢句柄。
        self._unconfirmed_procs: list = []
        self._frame_index = 0
        # 显示帧索引 = 素材源时间线上的 0-based 帧号（reader 打标，丢帧后
        # 仍一致）；与 1-based 播放计数 _frame_index 分离（P1 复审）。
        self._current_frame_index = 0
        self._ended_fired = False
        self._running = False
        self._generation = 0

        # 销毁清理（Fix A1）：绝不连接「自身 bound-method」槽——PySide6 在
        # C++ 删除对象时【从不】调用它（调查实证：lambda/外部对象方法才会被
        # 调用），那会让 cleanup() 缺席、reader 变僵尸。改连无 receiver 的
        # callable；lambda 捕获 self 会形成引用环（clip→连接→lambda→clip），
        # 由 cleanup() 内显式断开（_destroyed_conn）打破，C++ 删除时 Qt 也会
        # 清理连接兜底。
        self._destroyed_conn = self.destroyed.connect(
            lambda *_: WebMClip._destroyed_cleanup(self)
        )

    @staticmethod
    def _destroyed_cleanup(clip: "WebMClip") -> None:
        """C++ 对象销毁时经 destroyed 信号回调（无 receiver callable）。"""
        clip.cleanup()

    def __del__(self) -> None:
        try:
            self.cleanup()
        except RuntimeError:
            pass  # 解释器退出期 Qt C++ 对象可能已先销毁，此时无需清理

    def cleanup(self) -> None:
        """销毁/清理：terminate active reader 的 ffmpeg，退役并回收。

        对仍存活的退役 reader 保留追踪（绝不静默丢弃）：clip 及其 _Reader
        记录交由模块级生命周期管理器持有（_ORPHAN_REGISTRY），持续回收直到
        线程退出——绝不随 clip GC 丢弃追踪，也不与 reader 线程的收尾竞态
        （Windows 原生崩溃的根因，见模块头注释）。cleanup 后 clip 终结
        （_cleaned），自身不再安排任何 sweep/timer。
        """
        # 断开 destroyed 的 lambda 连接（Fix A1）：lambda 捕获 self 形成
        # 引用环，显式断开让 Python GC 可回收；C++ 已删场景直接跳过——
        # 否则 libpyside 在 Python except 之前先打 RuntimeWarning 噪音
        # （审查 DS-L3）。
        conn = getattr(self, '_destroyed_conn', None)
        if conn is not None:
            import shiboken6
            if shiboken6.isValid(self):
                try:
                    self.destroyed.disconnect(conn)
                except RuntimeError:
                    pass
            self._destroyed_conn = None
        self._cleaned = True
        self.cancel_first_frame_warm()
        # 释放首帧缓存（内存瘦身批）：clip 终结后缓存无意义，同时摘出预算
        # 注册表清账
        try:
            with self._first_frame_lock:
                self._first_image = None
        except Exception:
            pass
        _ffr_unregister(self)
        try:
            self.stop()
        except RuntimeError:
            pass  # QTimer 等 Qt 子对象已随 C++ 侧销毁
        self._reap_retired(join_timeout=_RECLAIM_JOIN_TIMEOUT)
        if self._retired or self._has_unconfirmed_procs():
            # 仍存活 reader 或未确认退出的首帧进程：保持模块级持有，由管理器
            # 继续回收/补杀（批 6-8b 收尾：_unconfirmed_procs 非空也必须留
            # 在注册表，否则 sweep 不会再来补杀）。
            _register_orphan(self)
        else:
            _unregister_orphan(self)

    def _sweep_retired(self) -> None:
        """兼容别名：由模块级生命周期管理器驱动（_reap_orphaned_clips）。"""
        self._reap_retired(join_timeout=_SWEEP_JOIN_TIMEOUT)

    def _reap_retired(self, join_timeout: float) -> None:
        """回收退役池：丢弃已确认退出的记录；对仍存活者有界 join；仍不退出
        或兜底确认失败则保留在池中（绝不静默丢弃追踪），由模块级管理器
        持续重试。

        兜底确认（批 6-8b 收尾；R3 闭合 R2 复审 P1）：只在「reader 线程
        已退出」后触碰其 proc——线程退出意味着 finally 已完整执行
        （_terminate_proc + gen.close() 是杀进程的主保证），此刻不存在与
        reader 收尾的并发 Popen 操作，可安全做「reader finally 之外的兜底
        确认」：句柄仍存活（_terminate_proc / gen.close 的异常被吞的病态
        路径）则补杀。确认失败（poll 异常 / terminate+kill 后仍存活）时
        **保留记录**并累计重试次数，达到 _CONFIRM_KILL_MAX 上限告警并标注
        abandoned（保留追踪不再重试）——绝不静默丢弃句柄。这闭合了
        try-acquire 超时跳过（_unblock_proc）依赖 owner 进 finally 杀进程的
        条件保证——即使 owner 因异常未能杀成，sweep 也会确认并补杀。
        """
        survivors = []
        for r in self._retired:
            if not r.thread.is_alive():
                if not self._confirm_or_keep(r):
                    survivors.append(r)
                    continue
                try:
                    r.join(timeout=0)
                except Exception:
                    pass
                continue
            r.thread.join(timeout=join_timeout)
            if r.thread.is_alive():
                survivors.append(r)
            elif not self._confirm_or_keep(r):
                survivors.append(r)
        self._retired = survivors

    def _confirm_or_keep(self, r: "_Reader") -> bool:
        """确认退役 reader 进程已退出；确认成功返回 True（调用方可移出追踪）。

        确认失败（_confirm_retired_proc 返回 False：poll 异常 / terminate+kill
        后仍存活）时**保留记录**并累计重试次数（有界重试）：达到
        _CONFIRM_KILL_MAX 记录告警并标注 abandoned——记录保留在追踪中但
        sweep 不再重试，绝不静默丢弃句柄（R3 闭合 R2 复审 P1）。
        已标注 abandoned 的记录不再尝试确认（直接返回 False 保留追踪）。
        """
        if r.abandoned:
            return False
        if self._confirm_retired_proc(r):
            return True
        if r.kill_attempts >= _CONFIRM_KILL_MAX:
            r.abandoned = True
            logger.warning(
                '退役 reader 进程补杀确认多次失败，标注放弃（保留追踪不再重试）: pid=%s path=%s',
                getattr(r.proc, 'pid', '?'),
                self.path,
            )
        return False

    @staticmethod
    def _confirm_retired_proc(r: "_Reader") -> bool:
        """退役 reader 线程已退出后的兜底确认/补杀（批 6-8b 收尾；R3 闭合）。

        前置条件：调用方已确认 r.thread 不再存活（finally 已完整执行，不会
        再有并发 Popen 操作）。正常路径进程已被 reader finally 终止
        （poll()!=None），此处为无操作；仅病态路径（_terminate_proc 或
        gen.close() 的异常被吞、进程仍存活）触发补杀——与「绝不让超时跳过
        的 _unblock_proc 留下未确认存活的 ffmpeg」的最终保障对应。

        R3（R2 复审 P1 闭合）：返回明确的退出确认结果——
        - True：进程已确认退出（或无需处理，proc 为 None），调用方可以安全
          移出追踪；
        - False：未能确认退出（poll 异常 / terminate+kill 后仍存活），调用方
          必须保留记录继续受控追踪（累计 r.kill_attempts，达到上限由调用方
          告警并标注 abandoned）——绝不静默丢弃句柄。
        """
        proc = r.proc
        if proc is None:
            return True
        try:
            if proc.poll() is not None:
                return True
        except Exception:
            r.kill_attempts += 1
            return False  # poll 异常：无法确认退出 → 保留追踪重试
        if not WebMClip._terminate_proc(proc):
            r.kill_attempts += 1
            return False  # 杀后仍存活 / 终止异常：保留追踪重试
        return True

    def _enforce_retired_cap(self) -> None:
        """退役池超过上限时回收已确认退出者（零等待 join；存活者保留追踪，
        由模块级管理器持续重试）。绝不做有界 join——stop() 在 GUI 线程，
        任何 join 等待都会变成用户可感知的卡顿（连点回归教训）。

        弹出记录前做兜底确认（_confirm_retired_proc / _confirm_or_keep）：
        确认成功才弹出；确认失败（poll 异常 / reader finally 异常被吞后
        进程仍存活）保留追踪（有界重试，达上限告警标注放弃）——绝不静默
        丢失句柄。"""
        while len(self._retired) > _MAX_RETIRED_READERS:
            oldest = self._retired[0]
            oldest.thread.join(timeout=0)
            if oldest.thread.is_alive():
                return  # 仍存活：保留追踪，池短暂超限（进程已终止，线程随管道 EOF 退出）
            if not self._confirm_or_keep(oldest):
                return  # 未确认/已放弃：保留追踪（宁可池短暂超限，不可丢句柄）
            self._retired.pop(0)

    @staticmethod
    def _terminate_proc(proc: subprocess.Popen | None,
                        timeout: float = _PROC_TERMINATE_TIMEOUT) -> bool:
        """主动终止 ffmpeg 子进程并确认退出（P2；R3 返回确认结果）。

        顺序：terminate() → 有界 wait() → 超时 kill() → 再次 wait()。
        返回 True=已确认退出（或 None / 已退出进程，无操作）；False=未能
        确认退出（kill 后仍存活 / poll/terminate/wait 异常）——调用方必须
        保留句柄供后续有界重试（绝不静默假设 terminate 后进程已退出，也
        不静默丢句柄）。

        已知局限：只保证单个 Popen 句柄（父进程）的退出，不覆盖其派生的
        进程树。当前 ffmpeg 为单进程解码器、不派生子进程；若未来引入会
        派生工作子进程的解码器，需在此升级为进程树终止（Windows job
        object / 进程组 kill），本实现不负责该场景。
        """
        if proc is None:
            return True
        try:
            if proc.poll() is not None:
                return True
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
                return True
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=timeout)
                    return True
                except subprocess.TimeoutExpired:
                    logger.warning(
                        'ffmpeg 进程 terminate+kill 后仍存活（保留句柄供 sweep 重试）: pid=%s',
                        getattr(proc, 'pid', '?'),
                    )
                    return False
        except Exception:
            return False

    def _unblock_proc(self, proc: subprocess.Popen | None) -> None:
        """GUI 侧对播放 reader Popen 的唯一操作：最小解除阻塞 terminate（批 6-8b）。

        只发 TerminateProcess（Windows 上同步杀进程），不做 wait/kill/关管道
        ——进程退出的确认与管道清理由 reader 线程 finally 的 _terminate_proc +
        gen.close() 完成（同一 Popen 的完整生命周期只由 owner 线程操作，杜绝
        跨线程并发操作同一 Popen 的原生竞态）。正在阻塞读 stdout/解析头部的
        reader 会因进程终止而立即解除阻塞退出（B7 契约不变）。

        幂等：进程已退出/returncode 已知时 terminate() 直接返回（CPython
        Windows 实现），重复调用无副作用。获取 _proc_lock 有界等待
        （_PROC_LOCK_ACQUIRE_TIMEOUT）：超时说明 reader 线程正在 finally
        收尾（持锁，它自己会 _terminate_proc + gen.close() 杀进程）——此处
        跳过。**跳过不是无条件安全，但最终保障链是闭合的**：stop() 在调用
        本方法后把（thread, proc）记录进退役池，孤儿注册表 sweep 的
        _reap_retired 在 reader 线程退出（finally 完成）后做兜底确认
        （_confirm_retired_proc：句柄仍存活则补杀）。因此即使 owner 的
        _terminate_proc / gen.close() 因异常被吞而未杀成，sweep 也会补杀
        并确认退出——try-acquire 超时绝不会留下无人追踪的存活 ffmpeg，
        也绝不让 stop() 因锁等待明显变长。
        """
        if proc is None:
            return
        if not self._proc_lock.acquire(timeout=_PROC_LOCK_ACQUIRE_TIMEOUT):
            # reader 正在 finally 收尾：它会自行 terminate。跳过不新增登记——
            # 本 proc 已随 stop() 进入退役池 _Reader 记录，sweep 的
            # _reap_retired 会在线程退出后兜底确认/补杀（见方法 docstring）。
            return
        try:
            try:
                if proc.poll() is None:
                    proc.terminate()
            except Exception:
                pass
        finally:
            self._proc_lock.release()

    # ------------------------------------------------------------ metadata
    def _ensure_meta(self) -> None:
        if self._duration > 0 or imageio_ffmpeg is None:
            return
        key = str(self.path)
        try:
            st = Path(key).stat()
            cache_key = f"{key}|{st.st_mtime_ns}|{st.st_size}"
        except OSError:
            cache_key = key
        cached = _META_CACHE.get(cache_key)
        if cached is not None:
            self._frame_count, self._duration = cached
            if self._frame_count > 0 and self._duration > 0:
                self._fps = self._frame_count / self._duration
            return
        entry = _get_meta_file_cache().get(cache_key)
        if isinstance(entry, dict) and entry.get('frames') and entry.get('duration'):
            self._frame_count = int(entry['frames'])
            self._duration = float(entry['duration'])
            if self._frame_count > 0 and self._duration > 0:
                self._fps = self._frame_count / self._duration
            _META_CACHE[cache_key] = (self._frame_count, self._duration)
            return
        try:
            frames, secs = imageio_ffmpeg.count_frames_and_secs(key)
            if frames and frames > 0:
                self._frame_count = int(frames)
            if secs and secs > 0:
                self._duration = float(secs)
            if self._frame_count > 0 and self._duration > 0:
                self._fps = self._frame_count / self._duration
            _META_CACHE[cache_key] = (self._frame_count, self._duration)
            _save_meta_file_cache_entry(cache_key, self._frame_count, self._duration)
        except Exception as exc:
            logger.warning('webm 元数据读取失败 %s: %s', self.path, exc)
            # 保留默认值，后续 reader 会尝试从 read_frames 的 meta 补充

    def warm_meta(self) -> None:
        """预取元数据（可被线程池并行调用）。"""
        self._ensure_meta()

    def _timer_interval(self) -> int:
        base = (
            max(1, int(round(1000 / (self._fps * self.playback_speed))))
            if self._fps > 0
            else max(1, int(round(catalog.FRAME_MS / self.playback_speed)))
        )
        # 解码节流（批11）：interval ×ratio —— 消费端降速为
        # fps/ratio，配合 reader 的阻塞入队（背压）让 ffmpeg 解码速率
        # 联动下降到同一节奏。ratio=1（默认/非闲置）时与旧行为逐位一致。
        return max(1, base * self._decode_throttle_divisor)

    def frameCount(self) -> int:
        if self._frame_count <= 0:
            self._ensure_meta()
        return max(1, self._frame_count)

    def duration(self) -> float:
        if self._duration <= 0:
            self._ensure_meta()
        return self._duration / self.playback_speed if self._duration > 0 else 0.0

    def currentFrameNumber(self) -> int:
        # 显示帧索引 = 素材源时间线 0-based 帧号（P1 复审）：降帧相位与
        # 末帧判断、预缩放缓存的帧号 key 都以此为准，与消费计数无关。
        return self._current_frame_index

    def currentTimeSeconds(self) -> float:
        if self._fps <= 0:
            return 0.0
        return self._current_frame_index / (self._fps * self.playback_speed)

    def currentPixmap(self):
        return self._current_pixmap

    # ------------------------------------------------------------ lifecycle
    def set_playback_speed(self, speed: float) -> None:
        self.playback_speed = max(0.1, float(speed))
        # _switch() 在 movie.start() 之前设置速率，不能只在 QTimer 已启动时更新。
        # 否则每个新 WebM 动画都会继续使用默认的 1x interval。
        self._timer.setInterval(self._timer_interval())

    @property
    def decode_throttle_divisor(self) -> int:
        """当前解码节流比率（1 = 不节流）。窗口层读取以协调发布语义。"""
        return self._decode_throttle_divisor

    def set_decode_throttle(self, divisor: int) -> None:
        """设置解码节流比率（闲置降帧联动，批11；主线程调用）。

        预留接口：比率可配，默认由窗口层按闲置降帧除数（IDLE_LOW_FPS_
        DIVISOR = 2）推送，不硬编码。>1 时：
        - 消费端：QTimer interval ×divisor（呈现节奏 = 原始 fps/divisor）；
        - 解码端：reader 入队改阻塞（背压），ffmpeg 解码速率随消费端联动
          下降（队列写满 → ffmpeg 阻塞在 write()）。
        ratio=1 恢复全速——非闲置路径调用此方法为幂等 no-op，行为零变化。
        幂等：比率未变时不做任何事（窗口层每帧同步调用，成本仅一次 int 比较）。
        """
        divisor = max(1, int(divisor))
        if divisor == self._decode_throttle_divisor:
            return
        self._decode_throttle_divisor = divisor
        self._timer.setInterval(self._timer_interval())

    def start(self) -> bool:
        """启动播放；返回 True 表示已启动（或已在播），False 表示本次启动被拒绝。

        拒绝原因：imageio_ffmpeg 不可用、clip 已 cleanup（终结），或退役
        reader 池未清空（存在存活 reader，B7 硬上限）。

        调用方（窗口层）必须把 False 当作「动画没有在播」并执行明确降级
        （回退上一动画/待机、安排重试），绝不能把动画状态切换与播放启动
        当成同一件事（B7 审查 P1-1）。
        """
        if self._running:
            return True
        if self._cleaned:
            logger.warning('clip 已 cleanup，拒绝启动 reader: %s', self.path)
            return False
        if imageio_ffmpeg is None:
            self.errorOccurred.emit(str(_IMPORT_ERROR or 'imageio_ffmpeg 不可用'))
            return False

        # 上一轮 natural end 残留的 active 线程先退役（其 ffmpeg 已自行退出）
        with self._reader_lock:
            stale_thread = self._thread
            stale_proc = self._reader_proc
            self._thread = None
            self._reader_proc = None
        if stale_thread is not None:
            self._retired.append(_Reader(thread=stale_thread, proc=stale_proc))
        # 退役池回收只做零等待（丢弃已退出者）；存活者已在 stop() 时 terminate，
        # 其线程由管道 EOF 驱动退出、模块级管理器持续追踪。
        # 绝不在这里 join 等待：join 会阻塞 GUI 线程，连点/快速切换动画时
        # 每次都卡最多 0.5s（实测回归）。病态卡死的极端累积由日志可观测。
        self._reap_retired(join_timeout=0)
        if len(self._retired) > _MAX_RETIRED_READERS * 4:
            logger.warning(
                'webm reader 退役池异常累积（%d 个存活）：%s',
                len(self._retired), self.path,
            )

        # 在 GUI 线程读取真实 fps 后再启动 QTimer，保证新动画的实际帧率
        # 与播放速率计算一致；reader 线程只负责解码和入队。
        self._ensure_meta()
        self._timer.setInterval(self._timer_interval())
        stop_evt = threading.Event()
        ready_evt = threading.Event()
        self._stop_evt = stop_evt
        self._reader_ready = ready_evt
        self._queue = queue.Queue(maxsize=8)
        self._frame_index = 0
        self._current_frame_index = 0  # 新一轮播放从头计时（P1 复审）
        self._ended_fired = False
        self._running = True
        self._generation += 1
        gen_id = self._generation

        thread = threading.Thread(
            target=self._reader,
            args=(stop_evt, gen_id, ready_evt),
            daemon=True,
            name=f'webm-reader-{gen_id}',
        )
        with self._reader_lock:
            self._thread = thread
        thread.start()
        self._timer.start()
        return True

    def stop(self) -> None:
        self._running = False
        # 停止信号先于任何 Qt 交互送达（Fix B）：C++ 半销毁（QTimer 已随 clip
        # 销毁）场景下，reader 也必须收到停止信号、ffmpeg 必被 terminate、
        # 线程必被退役登记——_timer.stop() 移到最末并吞 RuntimeError，保证
        # 前置步骤永不因 Qt 缺失中止（否则又是同款僵尸）。
        stop_evt = self._stop_evt
        if stop_evt is not None:
            stop_evt.set()
        # 主动 terminate 底层 ffmpeg：不能只是 set 事件等 reader 自己退（B7）。
        # 正在阻塞读管道/解析头部的 reader 会因进程终止而立即解除阻塞退出。
        # 批 6-8b：这里只做「最小解除阻塞 terminate」（_unblock_proc）——所有权
        # 在 reader 线程（唯一执行 wait/kill/关管道的线程），GUI 侧绝不再并发
        # 操作同一 Popen（Windows 原生竞态崩溃根因）；进程退出的确认由 reader
        # finally 完成，stop() 本身零等待返回。
        thread = None
        proc = None
        with self._reader_lock:
            thread = self._thread
            proc = self._reader_proc
            self._thread = None
            self._reader_proc = None
        if proc is not None:
            self._unblock_proc(proc)
        if thread is not None:
            self._retired.append(_Reader(thread=thread, proc=proc))
            self._enforce_retired_cap()
            # 退役池回收交给模块级生命周期管理器（P2）：持有 clip 直到退役
            # reader 全部退出，既避免 GC 与 reader 收尾竞态，也保证 cleanup
            # 后不残留本 clip 的 timer 调度。
            if not self._cleaned and self._retired:
                _register_orphan(self)
        try:
            self._timer.stop()
        except RuntimeError:
            pass  # C++ QTimer 已随 clip 销毁（半销毁场景）：停止信号与进程终止已先行完成

    def jumpToFrame(self, frame_index: int) -> bool:
        # 本项目只需要回到首帧；完整 seek 通过重启 reader + 丢弃帧实现。
        if frame_index <= 0:
            self.stop()
            self._frame_index = 0
            self._current_frame_index = 0  # 回到首帧 = 源时间线 0（P1 复审）
            if self._first_image is not None:
                # 首帧已缓存（后台 warm_first_frame 或上次同步解码）：
                # 主线程直接转 QPixmap，零阻塞、无旧帧残留窗口。
                self._current_image = self._first_image
                self._current_pixmap = QPixmap.fromImage(self._first_image)
                _ffr_evict(_ffr_touch(self))  # LRU 置顶 + 执行被逐出项（不丢弃）
            else:
                self._current_image = None
                self._current_pixmap = None
                self._decode_first_frame_sync()
            return True
        return False

    def _decode_first_qimage(self, gen: int | None = None):
        """解码首帧为 QImage（线程安全：不触碰 QPixmap/QTimer）。

        返回 None 表示失败、依赖缺失，或解码期间已被取消（换代）；调用方
        负责经 _store_first_frame（持锁提交）写入 _first_image 缓存。

        P1-2 生命周期：解码拉起的 ffmpeg 进程经 _PopenCapture 登记到
        _first_frame_procs（_reader_lock 保护），cancel_first_frame_warm/
        cleanup 可主动 terminate；gen 是本次解码认领的首帧代次，解码期间
        代次被换代则结果作废返回 None。未显式传入 gen 时以解码开始时的
        代次为准（所有真实调用路径均显式传入，此为防御默认）。
        """
        if imageio_ffmpeg is None:
            return None
        # 批 6-8b：探测串行化预热——与播放 reader 同源，避免首帧解码路径
        # 并发跑 ffmpeg -version 探测（read_frames 内部本就会探测，此处仅
        # 提前到统一入口并串行化）。
        _ensure_ffmpeg_exe()
        if gen is None:
            gen = self._first_frame_gen
        proc = None
        g = None
        try:
            def _register(p: subprocess.Popen, argv) -> None:
                """解码进程 Popen 一拉起即登记（可被取消/cleanup 主动 terminate）。

                登记在 _reader_lock 内复查代次与 cleanup 状态（B7 复审 R2）：
                取消（cancel_first_frame_warm 换代/清集合）与登记之间存在
                竞态窗口——「Popen 已创建、登记尚未完成」时取消会读到空集合；
                迟到的登记必须自检 stale，已取消的进程绝不漏进集合，而是
                立即自终止，保证取消后不再有不受控的 ffmpeg 存活。
                """
                nonlocal proc
                if not (isinstance(argv, list) and "-i" in argv):
                    return  # ffmpeg exe 探测等非解码进程：忽略
                proc = p
                with self._reader_lock:
                    stale = self._cleaned or gen != self._first_frame_gen
                    if not stale:
                        self._first_frame_procs.add(p)
                if stale:
                    self._terminate_proc(p)

            if perfstats.ENABLED:
                _ff_t0 = perfstats.clock()
            with _PopenCapture(on_process=_register):
                g = imageio_ffmpeg.read_frames(
                    str(self.path),
                    pix_fmt='rgba',
                    bits_per_pixel=self._bpp * 8,
                    input_params=['-c:v', 'libvpx-vp9'],
                )
                meta = next(g)  # ffmpeg 进程在此拉起；capture 即时登记句柄
                frame = next(g)
            if perfstats.ENABLED:
                # 首帧解码核心段（ffmpeg 拉起 + 两帧交付）：同步路径的
                # 点击卡顿与后台预热的耗时都在这里（P0 观测）。
                perfstats.time('webm.first_frame', perfstats.clock() - _ff_t0)
            if gen is not None and gen != self._first_frame_gen:
                return None  # 解码期间被取消/换代：结果作废
            if meta.get('fps'):
                self._fps = float(meta['fps'])
            if meta.get('duration'):
                self._duration = float(meta['duration'])
            if self._frame_count <= 0 and self._fps > 0 and self._duration > 0:
                self._frame_count = int(round(self._fps * self._duration))
            expect = self._w * self._h * self._bpp
            if len(frame) == expect:
                img = QImage(frame, self._w, self._h, self._w * self._bpp,
                             QImage.Format.Format_RGBA8888)
                if not img.isNull():
                    return img.copy()
            return None
        except Exception as exc:
            logger.warning('webm 首帧解码失败 %s: %s', self.path, exc)
            return None
        finally:
            if proc is not None:
                with self._reader_lock:
                    self._first_frame_procs.discard(proc)
            if g is not None:
                try:
                    # 批 6-8b：g.close()（内部 poll/关管道/等待/kill）与 GUI
                    # cancel_first_frame_warm 的 _terminate_proc 以 _ff_proc_lock
                    # 互斥——同一 Popen 的操作任意时刻只允许一个线程执行。
                    with self._ff_proc_lock:
                        g.close()
                except Exception:
                    pass

    def _store_first_frame(self, img) -> list:
        """把解码结果写入 _first_image 缓存（调用方须已持有 _first_frame_lock）。

        幂等：缓存已存在则跳过；写入后 set _first_frame_done。
        返回待逐出列表——调用方必须在释放本 clip 锁后再 _ffr_evict
        （R3 复审：锁内逐出会取 victim 的锁，构成跨对象持锁嵌套）。
        """
        if img is None:
            return []
        if self._first_image is None:
            self._first_image = img
            self._first_frame_done.set()
            # 预算 LRU 登记；逐出返回给调用方、在释放本 clip 锁后执行
            # （R3 复审：持锁期间逐出会取 victim 的锁，跨对象嵌套可死锁）
            return _ffr_touch(self, img.width() * img.height() * 4)
        return []

    def _decode_first_qimage_and_cache(self, gen: int | None = None) -> list:
        """解码首帧并写入 _first_image 缓存（调用方须已持有 _first_frame_lock）。

        幂等：缓存已存在时直接返回，避免重复拉起 ffmpeg。
        gen：warm 传入的首帧代次；解码期间被换代（cancel_first_frame_warm/
        cleanup）则丢弃结果，不污染缓存（P1-2）。
        """
        if self._first_image is not None:
            return []
        img = self._decode_first_qimage(gen=gen)
        if gen is not None and gen != self._first_frame_gen:
            return []  # 已被取消/换代：结果作废，不提交
        return self._store_first_frame(img)

    def _apply_first_frame(self) -> None:
        """把已缓存的 _first_image 应用到当前播放帧（仅主线程调用）。"""
        if self._first_image is None:
            return
        self._current_image = self._first_image
        self._current_pixmap = QPixmap.fromImage(self._first_image)

    def _apply_first_frame_image(self, img) -> None:
        """把解码得到的首帧图像直接应用到当前播放帧（仅主线程调用）。

        不写入 _first_image 缓存：用于逃生口拿不到锁、无法经锁提交缓存的
        场景（P1-3 / B7 复审 R2——绝不做无锁 check-then-store，缓存提交
        只可能由持锁方完成）。
        """
        if img is None:
            return
        self._current_image = img
        self._current_pixmap = QPixmap.fromImage(img)

    def _decode_first_frame_sync(self) -> None:
        """同步解码首帧（主线程），保证 jumpToFrame(0)/currentPixmap 在 start() 前有画面。

        与后台 warm_first_frame 原子互斥：同一时间只有一个首帧解码执行者。
        认领失败说明后台预热正在解码：最多等待 _FIRST_FRAME_SYNC_WAIT_MS
        （后台完成则直接用其缓存，零重复解码）；超时则放弃等待直接自行解码，
        前台播放绝不被后台预热长时间卡住（代价是极端情况下短暂双解码）。

        逃生口（超时自行解码）允许与后台双解码，但缓存提交单胜者化
        （P1-3 / B7 复审 R2）：始终经锁提交，拿不到锁就放弃写缓存、只把
        图像直接应用到当前画面——绝不无锁 check-then-store（两个逃生提交者
        并发时缓存胜者取决于调度顺序，且后台完成后的幂等检查会让先写入者
        永久决定缓存）。

        P1-2 / B7 复审 R2：同步路径在进入时捕获首帧代次并贯穿解码与提交，
        在飞解码被取消（换代）后结果作废，不污染缓存——与后台 warm 同等
        的代次取消语义。
        """
        gen = self._first_frame_gen
        victims = []
        if self._first_frame_lock.acquire(blocking=False):
            try:
                if perfstats.ENABLED:
                    perfstats.note('webm.ff_gui_decode')  # GUI 线程亲自解码首帧（~166ms 冻结，P0 定案测量）
                victims = self._decode_first_qimage_and_cache(gen=gen)
            finally:
                self._first_frame_lock.release()
        else:
            if perfstats.ENABLED:
                perfstats.note('webm.ff_gui_wait')  # GUI 等后台预热（有界等待，P0 定案测量）
            if not self._first_frame_done.wait(timeout=_FIRST_FRAME_SYNC_WAIT_MS / 1000.0):
                img = self._decode_first_qimage(gen=gen)
                victims = self._commit_first_frame_escape(img, gen=gen)
        _ffr_evict(victims)  # 逐出延迟到本 clip 锁释放后（防跨对象持锁嵌套）
        self._apply_first_frame()

    def _commit_first_frame_escape(self, img, gen: int | None = None) -> list:
        """逃生口（未持锁）的首帧缓存提交（P1-3 / B7 复审 R2）。

        只允许两种结果：
        1. 拿到锁：经 _store_first_frame 幂等提交（与后台写真正互斥）；
        2. 拿不到锁（后台仍持锁卡住）：放弃写缓存，把本帧直接应用到当前
           画面（主线程已拿到可显示首帧）——缓存提交只可能由持锁方完成，
           明确单胜者，绝不在锁外触碰 _first_image。

        绝不在逃生路径上阻塞等待锁：后台真卡死时持锁不释放，等待会把
        「前台绝不被后台预热长时间卡住」的承诺重新变成 GUI 冻结。
        gen：本次解码认领的首帧代次；解码期间被取消（换代）则结果作废。
        """
        if img is None:
            return []
        if gen is not None and gen != self._first_frame_gen:
            return []  # 解码期间被取消/换代：结果作废，不提交（P1-2）
        if self._first_frame_lock.acquire(blocking=False):
            try:
                return self._store_first_frame(img)
            finally:
                self._first_frame_lock.release()
        self._apply_first_frame_image(img)
        return []

    def warm_first_frame(self) -> None:
        """后台线程预解码首帧缓存（仅 QImage，线程安全）。

        首次播放某动画时 jumpToFrame(0) 需要首帧：有缓存则主线程零阻塞，
        避免点击瞬间同步 ffmpeg 解码造成卡顿，以及 Q 弹期间残留旧动画帧。

        原子认领（N4）：同一时间只有一个首帧解码执行者。认领失败（前台
        同步解码或并发预热正在进行）直接放弃——预热是尽力而为，不排队等待。

        P1-2：预热解码纳入生命周期回收——解码拉起的 ffmpeg 登记到
        _first_frame_procs，cancel_first_frame_warm/cleanup 可主动 terminate；
        解码代次在认领时捕获，取消后结果作废不写入缓存。cleanup 后不再预热。
        """
        if self._first_image is not None or imageio_ffmpeg is None or self._cleaned:
            return
        if not self._first_frame_lock.acquire(blocking=False):
            return
        victims = []
        try:
            gen = self._first_frame_gen
            victims = self._decode_first_qimage_and_cache(gen=gen)
        finally:
            self._first_frame_lock.release()
        _ffr_evict(victims)  # 锁外逐出（同上）

    def cancel_first_frame_warm(self) -> None:
        """取消在飞的首帧预热（P1-2）：换代使在飞解码结果作废，并主动
        terminate 其 ffmpeg 进程。

        由 cleanup()（clip 销毁）与 MovieLibrary.pause_warm()（隐藏/切角色）
        调用。之后新的 warm_first_frame 仍可重新预热（代次自增，非终态）。

        换代与集合读取/清空在 _reader_lock 内原子完成（B7 复审 R2）：登记
        回调也在该锁内复查代次——「Popen 已创建、登记尚未完成」窗口内取消
        时，迟到的登记会看到换代并自终止，已取消进程绝不漏进集合。
        """
        with self._reader_lock:
            self._first_frame_gen += 1
            procs = list(self._first_frame_procs)
            self._first_frame_procs.clear()
        for p in procs:
            # 批 6-8b：完整 terminate（terminate→wait→kill→wait，测试锁定的取消
            # 语义：进程必须确认退出）在 _ff_proc_lock 内执行——与解码线程
            # finally 的 g.close()（同锁）互斥，杜绝 GUI 与解码线程并发操作
            # 同一 Popen。有界等待：超时说明解码线程正在收尾，其 g.close() 内部
            # 会 kill 存活进程（imageio finally：poll 判活 → 关管道 → kill），
            # 且结果已因换代作废。超时跳过不是无条件安全（g.close 异常被吞的
            # 病态路径会漏）——登记到 _unconfirmed_procs，由孤儿注册表 sweep
            # 在 owner 释放锁后确认/补杀（_sweep_unconfirmed_procs）。
            if self._ff_proc_lock.acquire(timeout=_PROC_LOCK_ACQUIRE_TIMEOUT):
                try:
                    self._terminate_proc(p)
                finally:
                    self._ff_proc_lock.release()
            else:
                self._track_unconfirmed_proc(p)

    def _track_unconfirmed_proc(self, proc: subprocess.Popen) -> None:
        """把 try-acquire 超时跳过、未确认退出的首帧进程登记进重试机制
        （批 6-8b 收尾；R3 条目格式 [proc, attempts, abandoned]）：挂到
        _unconfirmed_procs 并确保 clip 进入孤儿注册表，sweep 会在 owner
        释放 _ff_proc_lock 后确认/补杀。确认失败保留条目并累计重试，达到
        上限告警标注 abandoned（保留追踪不再重试）——绝不静默丢弃句柄。"""
        with self._reader_lock:
            self._unconfirmed_procs.append([proc, 0, False])
        _register_orphan(self)

    def _has_unconfirmed_procs(self) -> bool:
        with self._reader_lock:
            return bool(self._unconfirmed_procs)

    def _sweep_unconfirmed_procs(self) -> None:
        """对未确认退出的首帧进程做有界补杀确认（批 6-8b 收尾；孤儿注册表
        sweep 调用，GUI 线程）。

        owner（解码线程）持有 _ff_proc_lock 说明其 finally 的 g.close() 正在
        执行（内部会杀存活进程）——此时有界尝试获取锁失败即留待下次 sweep
        （绝不阻塞 GUI）；owner 已释放（g.close 完成 / 线程已退出）时获取
        成功并确认：进程已死则为无操作，仍存活则补杀。

        R3（R2 复审 P1 闭合）：确认失败（poll 异常 / terminate+kill 后仍
        存活 / 锁竞争超时）**保留条目**并累计 attempts，绝不一次即丢；达到
        _UNCONFIRMED_KILL_MAX 记录告警并标注 abandoned（条目保留在追踪中、
        后续 sweep 不再重试）——不无限静默重试，也不静默丢句柄。
        """
        with self._reader_lock:
            if not self._unconfirmed_procs:
                return
            pending = list(self._unconfirmed_procs)
            self._unconfirmed_procs.clear()
        still: list = []
        for entry in pending:
            proc, attempts, abandoned = entry
            if abandoned:
                still.append(entry)  # 已标注放弃：保留追踪，不再重试
                continue
            if not self._ff_proc_lock.acquire(timeout=_PROC_LOCK_ACQUIRE_TIMEOUT):
                self._bump_unconfirmed(proc, attempts, still)
                continue
            try:
                confirmed = WebMClip._terminate_proc(proc)
            finally:
                self._ff_proc_lock.release()
            if not confirmed:
                self._bump_unconfirmed(proc, attempts, still)
        if still:
            with self._reader_lock:
                self._unconfirmed_procs.extend(still)

    def _bump_unconfirmed(self, proc: subprocess.Popen, attempts: int,
                          still: list) -> None:
        """未确认退出的一次重试记账（R3）：递增 attempts；达到上限告警并
        标注 abandoned（条目保留在追踪中、不再重试），否则保留待下次 sweep
        重试——绝不静默丢弃句柄。"""
        attempts += 1
        if attempts >= _UNCONFIRMED_KILL_MAX:
            logger.warning(
                '首帧进程取消后未确认退出，标注放弃（保留追踪不再重试）: pid=%s',
                getattr(proc, 'pid', '?'),
            )
            still.append([proc, attempts, True])
        else:
            still.append([proc, attempts, False])

    # ------------------------------------------------------------ reader
    def _reader(self, stop_evt: threading.Event, generation: int,
                ready_evt: threading.Event | None = None) -> None:
        """reader 线程入口：feed 模式（P3 broker）与本地解码的分派。

        - ``_feed_source`` 为 None（默认/灰度关）：逐位走 ``_reader_local``，
          与历史行为零差异；
        - ``_feed_source`` 已置（client 消费端，facade 在 start() 前设置）：
          先有界等待 grant（feed-pending，≤SUBSCRIBE_BUDGET_MS，stop 感知），
          成功后从共享内存取帧入队（沿用本地同款有界 put/丢帧契约）；
          grant 失败/被拒/超时/中途断流/中止 → **同一 reader 线程内**回退
          本地 ffmpeg 解码（帧 0 起播，重入 _reader_local 的拉起序列——
          capture/登记/兜底全复用，绝不复刻一个绕过追踪的新拉起，P1-1）。
        """
        # 批 6-8b：线程启动前已被 stop/换代的 reader 零成本退出——绝不拉起
        # 任何 ffmpeg 进程（省掉「拉起→_register 发现 stale→自终止」的浪费
        # 与延迟，也杜绝 stop 无法解除的探测/解码等待）。
        if stop_evt.is_set() or self._generation != generation:
            return
        feed = self._feed_source
        if feed is not None:
            done = self._reader_feed(feed, stop_evt, generation, ready_evt)
            if done:
                return  # feed 已完整处理本轮（自然结束/停止）
            # 断流/被拒/超时 → 回退本地：清空 feed 残留帧后落本地路径
            if stop_evt.is_set() or self._generation != generation:
                return
            self._drain_queue_for_local()
            logger.info('broker feed 回退本地解码（帧 0 起播）: %s', self.path)
        self._reader_local(stop_evt, generation, ready_evt)

    def _reader_local(self, stop_evt: threading.Event, generation: int,
                      ready_evt: threading.Event | None = None) -> None:
        # 批 6-8b：线程启动前已被 stop/换代的 reader 零成本退出——绝不拉起
        # 任何 ffmpeg 进程（省掉「拉起→_register 发现 stale→自终止」的浪费
        # 与延迟，也杜绝 stop 无法解除的探测/解码等待）。
        if stop_evt.is_set() or self._generation != generation:
            return
        # ffmpeg exe 探测串行化预热（见模块级 _ensure_ffmpeg_exe）：并发
        # reader 不再各自跑 ffmpeg -version 探测（无限等待的 check_call）。
        _ensure_ffmpeg_exe()
        if stop_evt.is_set() or self._generation != generation:
            return  # 探测期间被 stop：不拉起解码进程，直接退出
        gen = None
        proc = None
        try:
            q = self._queue

            def _register(p: subprocess.Popen, argv) -> None:
                """解码进程 Popen 一拉起即回调（可能发生在头部解析阻塞期间）：
                登记进程句柄，让 stop() 能立即 terminate；若本轮已被 stop/换代，
                则立即自终止。

                只认解码进程（argv 含 '-i <path>'）：imageio 的 ffmpeg exe 探测
                （ffmpeg -version）也在 reader 线程上拉起，不能登记/终止，否则
                探测会被误杀导致 get_ffmpeg_exe 失败。
                """
                nonlocal proc
                if not (isinstance(argv, list) and "-i" in argv):
                    return  # ffmpeg exe 探测等非解码进程：忽略
                proc = p
                # stale 判定与登记在 _reader_lock 内原子完成（R3 收尾，与首帧
                # 路径同构）：stop() 先 set stop_evt、再取 _reader_lock。若 stale
                # 在锁外先算、锁内再复查，会留下「外层 False → 锁内 True → 既
                # 不登记也不自终止」的窗口——进程存活且无任何追踪，stop 拿不到
                # handle 无法解除其阻塞读（reader 卡死 + LogCatcher 残留的全量
                # 偶发 flake 根因）。锁内单次判定保证：要么完成登记（stop 可见
                # handle 并解除阻塞读），要么判定 stale 并立即自终止，绝不漏。
                with self._reader_lock:
                    stale = stop_evt.is_set() or self._generation != generation
                    if not stale:
                        self._reader_proc = p
                if stale:
                    self._terminate_proc(p)

            with _PopenCapture(on_process=_register) as capture:
                gen = imageio_ffmpeg.read_frames(
                    str(self.path),
                    pix_fmt='rgba',
                    bits_per_pixel=self._bpp * 8,
                    input_params=['-c:v', 'libvpx-vp9'],
                )
                meta = next(gen)  # ffmpeg 进程在此拉起；capture 即时登记句柄
                if proc is None:
                    proc = capture.process
            if proc is not None:
                # 兜底登记：capture 即时回调已覆盖，此处仅防异常路径遗漏。
                # stale 时不能只跳过登记——进程逃出追踪链（5.6sol 三审），
                # 与 _register 回调同构：判定 stale 则立即自终止（幂等，
                # 已死进程 terminate 是 no-op）。
                with self._reader_lock:
                    stale = stop_evt.is_set() or self._generation != generation
                    if not stale:
                        self._reader_proc = proc
                if stale:
                    self._terminate_proc(proc)
            if ready_evt is not None:
                ready_evt.set()
            if stop_evt.is_set() or self._generation != generation:
                return
            # 用实际流信息修正元数据
            if meta.get('fps'):
                self._fps = float(meta['fps'])
            if meta.get('duration'):
                self._duration = float(meta['duration'])
            if self._frame_count <= 0 and self._fps > 0 and self._duration > 0:
                self._frame_count = int(round(self._fps * self._duration))

            self._stamp_source_indices(
                gen,
                q,
                lambda: stop_evt.is_set() or self._generation != generation,
                throttled=lambda: self._decode_throttle_divisor > 1,
                # P3 broker：发布镜像（coordinator 播放时置 _publish_sink）。
                # reader 只做每帧回调；自然播完/中止的会话收尾由 facade 经
                # movie finished/stop 在 GUI 侧驱动（P1-2），reader 不写标记。
                on_frame=(self._publish_sink.on_frame
                          if self._publish_sink is not None else None),
            )
            # 正常播完时放入结束标记。主线程可能正忙（队列满、帧被丢弃），
            # 必须循环重试直到放入或收到停止信号；否则“最后一帧被丢弃且
            # 结束标记也丢失”会让上层永远等不到播完，动画链卡死在最后一帧。
            # 重试有总时限（Fix C）：病态场景（队列持续满）下有界放弃，绝不
            # 永久空转；_terminate_proc / gen.close() 由 finally 保证。
            self._put_end_marker(q, stop_evt, generation)
        except Exception as exc:
            if self._generation != generation or stop_evt.is_set():
                return
            logger.exception('webm 解码失败: %s', self.path)
            self.errorOccurred.emit(str(exc))
            # 异常中断也要放入结束标记，避免动画链卡在最后一帧（同样有界）。
            self._put_end_marker(q, stop_evt, generation)
        finally:
            with self._reader_lock:
                if proc is not None and self._reader_proc is proc:
                    self._reader_proc = None
            if proc is not None or gen is not None:
                # 批 6-8b：收尾操作（_terminate_proc 的 poll/terminate/wait/kill +
                # gen.close() 的 poll/关管道）在 _proc_lock 内串行化——与 GUI
                # stop() 的 _unblock_proc 互斥，同一 Popen 任意时刻只有一个线程
                # 操作（Windows 原生竞态崩溃根因）。
                #
                # 代码路径保证（针对「gen.close() 1.5s 轮询被短路」的条件性）：
                # - 路径 A（正常，proc 已捕获）：先 _terminate_proc 杀进程，再
                #   gen.close()。实测 imageio-ffmpeg 0.6.0 的 close() 首先
                #   `process.poll()` 判活——进程已死（returncode 已知）时整个
                #   「存活进程清理块」（关管道 + 1.5s 轮询 + kill，_io.py
                #   finally 的 `if process.poll() is None:` 分支）被跳过，
                #   锁持有上界 = _terminate_proc 时间（正常毫秒级，无 1.5s）。
                # - 路径 B（兜底，proc 句柄捕获失败 = capture 未看到进程）：
                #   只剩 gen.close() 执行终止——imageio finally 内 poll 判活 →
                #   关管道 → 1.5s 轮询 → kill，锁持有时间病态可达 ~1.5s。
                #   这是捕获失败这一罕见异常路径的代价上界（可接受：进程
                #   仍必被终止，只是时间更长）。
                with self._proc_lock:
                    if proc is not None:
                        # 兜底 terminate：正常情况下 stop() 已解除阻塞/终止；
                        # 自然播完/解码失败时进程已自行退出（poll()!=None），
                        # 此处为无操作。
                        self._terminate_proc(proc)
                    if gen is not None:
                        try:
                            gen.close()
                        except Exception:
                            pass

    # ------------------------------------------------------------ P3 broker
    def _reader_feed(self, feed, stop_evt: threading.Event, generation: int,
                     ready_evt: threading.Event | None = None) -> bool:
        """feed 分支（reader 线程内）。返回 True = 本轮已由 feed 完整处理
        （grant 成功并流完自然结束 / 或已被 stop 打断）；False = 需要回退
        本地解码（grant 失败/被拒/超时/断流/中止）。

        只在该 WebMClip 以消费端（client）身份、facade 在 start() 前设置了
        ``_feed_source`` 时进入。feed 等待/读取期间不持有任何锁；有界 put
        沿用本地同款丢帧契约（队列满丢帧、源帧号照常推进）。
        """
        # 1) feed-pending：grant 有界等待（reader 线程内，≤SUBSCRIBE_BUDGET_MS）
        from . import decode_broker as broker_mod
        budget_ms = getattr(feed, 'budget_ms', None) or broker_mod.SUBSCRIBE_BUDGET_MS
        deadline = time.monotonic() + max(1, int(budget_ms)) / 1000.0
        while not (stop_evt.is_set() or self._generation != generation):
            if feed.ready:
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.005)
        if stop_evt.is_set() or self._generation != generation:
            # 等待期被 stop/换代：闭锁本 feed——若 grant 恰在此时落定，session
            # 由 expire 立即 close（绝不遗留无主句柄）；迟到的 grant 同样被
            # close 而非 complete（P3A P1-2：attach 句柄必须有主）。
            feed.expire()
            return True  # 由调用方退出，无残留
        feed_session = feed.result if feed.ready else None
        if feed_session is None:
            # deny / 超时 / 通道不可用：授权失败 → 本地解码（帧 0 起播）。
            # expire 闭锁本 feed：晚到的 grant 命中已闭锁句柄 → 立即 close，
            # 不再 complete（reader 已不再等待该 Event）。
            feed.expire()
            logger.info('broker feed 授权失败（deny/超时），回退本地解码: %s', self.path)
            return False
        if ready_evt is not None:
            ready_evt.set()  # feed 已就绪：等价于本地 ffmpeg 拉起完成的信号
        q = self._queue
        try:
            while not (stop_evt.is_set() or self._generation != generation):
                kind, data, src = feed_session.poll()
                if kind == 'frame':
                    try:
                        q.put((data, src), timeout=0.2)
                    except queue.Full:
                        if perfstats.ENABLED:
                            perfstats.note('webm.queue_drop')
                        pass  # 队列满丢帧：源帧号照常推进（本地同款契约）
                elif kind == 'end':
                    # 发布端自然播完（run_ended_natural）：结束标记 → finished
                    self._put_end_marker(q, stop_evt, generation)
                    return True
                elif kind == 'abort':
                    logger.warning('broker feed 断流/中止，回退本地解码: %s', self.path)
                    return False
                else:  # 'none'：暂无新帧，微让步避免忙等
                    time.sleep(0.002)
            return True  # 被 stop/换代：调用方直接退出
        finally:
            try:
                feed_session.close()
            except Exception:
                pass

    def _drain_queue_for_local(self) -> None:
        """回退本地解码前清空队列残留的 feed 帧（P1-1：feed → 本地 帧 0
        起播，不得与旧 feed 帧混序显示）。只清不阻塞；GUI 线程可能正在消费，
        二者并发安全（queue.Queue 线程安全）。"""
        q = self._queue
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                return

    def _poll(self) -> None:
        """主线程按视频帧率逐帧取帧，不跳帧、不积压追帧。

        注意：不能一次清空队列只处理最新帧，否则会把中间帧丢弃，
        导致动画视觉上“快进”。这里每次只取最早的一帧。
        """
        try:
            item = self._queue.get_nowait()
        except queue.Empty:
            if perfstats.ENABLED:
                # 消费端空转（解码还没跟上/未开始）：P0 观测。
                perfstats.note('webm.poll_empty')
            return

        if item is None:
            # 正常播完；若在处理最后一帧时已经由窗口层启动了下一个动画，
            # self._queue 已被替换，不会走到这里。
            if not self._ended_fired:
                self._ended_fired = True
                self._running = False
                self._timer.stop()
                self.finished.emit()
            return

        self._process_frame(item)

    def _put_end_marker(self, q, stop_evt, generation) -> None:
        """有界重试把结束标记（None）放入队列（Fix C）。

        正常路径队列很快腾出槽位、首次即送达（保住「最后一帧/结束标记必须
        送达」契约）；仅病态（队列持续满且无人消费、stop_evt 缺失的历史
        僵尸）在 _END_MARKER_PUT_TIMEOUT 内有界放弃并告警，绝不永久空转。
        调用方（_reader）的 finally 仍保证 _terminate_proc 与 gen.close()。
        """
        deadline = time.monotonic() + _END_MARKER_PUT_TIMEOUT
        while not stop_evt.is_set() and self._generation == generation:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning(
                    'webm 结束标记放入超时放弃（队列持续满）: %s', self.path,
                )
                return
            try:
                q.put(None, timeout=min(0.5, remaining))
                break
            except queue.Full:
                continue

    @staticmethod
    def _stamp_source_indices(frames, q, is_stopped, timeout: float = 0.2,
                              throttled=None, on_frame=None) -> None:
        """reader 线程把解码帧逐帧打上素材源时间线帧号后入队。

        队列项 = (RGBA 字节, 源时间线 0-based 帧号)。返回帧号即
        elapsed video time × fps 对应的素材原始帧号；队列满（UI 消费
        不过来）时丢弃该帧，但源帧号照常推进——被丢弃的帧仍占用时间线
        槽位，保证主线程拿到的显示帧索引在丢帧后依然锚定素材时间线
        （消费计数在丢帧后不再等于源帧号，绝不能用作降帧相位/末帧判断）。

        throttled（批11）：可调用对象，每次入队前求值；返回 True 表示当前
        解码节流生效（闲置降帧激活）。节流路径 reader **绝不超时丢帧**，
        而是按目标呈现节奏阻塞：q.put 有界重试同一帧直到成功或收到停止
        信号——队列写满即 reader 停步、ffmpeg 的 stdout 管道写满、解码进程
        阻塞在 write()，解码速率随消费端联动下降到目标节奏（≈原始帧率/
        ratio）。停止检查夹在每次重试之间（有界，_reader 的 finally 仍保证
        杀进程与 gen.close()，绝不让 reader 永久空转）。节流时源帧号只在
        入队成功后推进——被阻塞重试的帧绝不丢失、绝不虚占时间线槽位。
        throttled=None（默认）＝永不节流：与历史行为逐位一致（超时丢帧）。

        on_frame（P3 broker）：可选回调 on_frame(frame_bytes, src_idx)，
        每解码一帧调用一次（在节流/丢帧决策之前，即"解码节奏"镜像——
        发布端 coordinator 的共享 session 按此节奏发布帧，见 WebMClip
        ``_publish_sink`` 钩子）。None（默认）＝零行为差异。

        抽取为独立方法便于单元测试：不依赖 ffmpeg/Qt，直接验证
        「丢帧后帧号连续性与停止语义」（P1 复审）。
        """
        src_idx = 0
        it = iter(frames)
        while True:
            if perfstats.ENABLED:
                _dec_t0 = perfstats.clock()
            try:
                frame = next(it)
            except StopIteration:
                break
            if is_stopped():
                break
            if on_frame is not None:
                on_frame(frame, src_idx)
            if perfstats.ENABLED:
                # 帧间隔 = ffmpeg 解码 + 管道交付一帧的耗时（reader 侧，
                # P0 观测：不把下方入队阻塞计入解码耗时）。
                perfstats.time('webm.decode', perfstats.clock() - _dec_t0)
            if throttled is not None and throttled():
                # 节流路径：阻塞入队（背压），不丢帧、不虚推进源帧号。
                if perfstats.ENABLED:
                    _put_t0 = perfstats.clock()
                while not is_stopped():
                    try:
                        q.put((frame, src_idx), timeout=timeout)
                        src_idx += 1
                        break
                    except queue.Full:
                        continue  # 队列仍满：同一帧继续阻塞重试
                if perfstats.ENABLED:
                    perfstats.time('webm.queue_wait', perfstats.clock() - _put_t0)
                continue
            if perfstats.ENABLED:
                _put_t0 = perfstats.clock()
            try:
                q.put((frame, src_idx), timeout=timeout)
            except queue.Full:
                if perfstats.ENABLED:
                    perfstats.note('webm.queue_drop')
                pass  # 丢弃该帧；源帧号照常推进（时间线槽位不因丢帧回退）
            if perfstats.ENABLED:
                perfstats.time('webm.queue_wait', perfstats.clock() - _put_t0)
            src_idx += 1

    def _process_frame(self, item) -> None:
        data, src_idx = item
        expect = self._w * self._h * self._bpp
        if len(data) != expect:
            logger.warning('webm 帧长度异常: got=%d expect=%d', len(data), expect)
            return
        if perfstats.ENABLED:
            _cons_t0 = perfstats.clock()
        img = QImage(data, self._w, self._h, self._w * self._bpp,
                     QImage.Format.Format_RGBA8888)
        if img.isNull():
            return
        self._current_image = img.copy()
        self._current_pixmap = QPixmap.fromImage(self._current_image)
        # 显示帧索引 = 素材源时间线 0-based 帧号（reader 打标，丢帧后仍
        # 一致）；播放计数 _frame_index = 已消费帧数（1-based）。二者分离：
        # 降帧相位与末帧判断一律使用显示帧索引，绝不使用消费计数
        # （P1 复审——否则 reader 队列满丢帧后相位错位、末帧提前）。
        self._current_frame_index = src_idx
        self._frame_index += 1
        if perfstats.ENABLED:
            # 主线程消费转换（RGBA→QImage→QPixmap）耗时（P0 观测）。
            perfstats.time('webm.consume', perfstats.clock() - _cons_t0)
        self.frameChanged.emit(self._current_frame_index)
