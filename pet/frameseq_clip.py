# -*- coding: utf-8 -*-
"""FrameSeqClip：无损 WebP 帧序列播放器（帧序列化 B 档，热集专用）。

与 WebMClip/GifClip 同一播放器接口（frameChanged/finished/errorOccurred
+ start/stop/jumpToFrame/currentImage/currentPixmap/frameCount/duration/
set_playback_speed/...），供 MovieLibrary 在存在帧序列素材时替换
WebMClip——热路径（idle/move/turn/click/drag ≈95% 播放时长）从此没有
ffmpeg 子进程、spawn 冷启动（60-166ms）、管道/背压/看门狗/关机残留。

素材形态（转换器 tools/convert_frameseq.py 产出）：
    assets/characters/<id>/frameseq/<folder>/<stem>/
        meta.json      {"fps": 24.0, "frames": 239, "source": "idle/x.webm", ...}
        f_0001.webp ...（libvpx 解码 + bgra 直通的无损帧，视觉 bit-exact）

帧表去物化（M1 内存口径，2026-09-28）：构造阶段不列目录，只读 meta 的
fps/frames；frameCount/duration/currentTimeSeconds 走 meta 的帧数（O(1)，零系统
调用），**播放路径也不物化帧表**——素材是转换器写出的连续 ``f_%04d.webp``，路径
按编号现推、用完即弃。R1 那版"首次真播放 glob 一次并常驻 ~239 个 Path"实测在
106 段 × 3 宠下多出 ≈110MB RSS 且永不释放（``library.release_idle_frames`` 只回收
队列/显示槽，碰不到帧表），而帧数本来就以 meta 为权威。glob 只保留为 meta
缺失/非法（旧产物、转换中断）时的兜底——那才是唯一真"列帧表"的路径。

播放模型与现架构一致（链式一次性播放）：start() 从第 0 帧起按 fps 推进，
末帧后停表并发 finished()，循环由上层状态机重启 clip 承接。

线程模型（实测驱动，2026-09-23 A/B）：逐帧 QImage 加载 ~2.5ms 若放在
GUI 定时器里，3 宠待机循环时 GUI 线程被吃掉 ~18%/核——直接顶撞
"高刷不卡顿"硬指标。因此解码全部在预取 worker 线程：GUI 定时器只消费
已到货的帧（pending 映射），未到货则等待不跳帧（同 WebMClip 语义）；
worker 常驻下一帧预取。内存只驻留当前帧 + 1~2 帧预取（+OS 页缓存），
无 reader 线程级队列。jumpToFrame/warm_first_frame 这类低频同步路径
允许一次 ~2.5ms 的同步加载（调用方期望立即生效）。

显示槽只存 QImage：QPixmap 由 ``currentPixmap()`` 的消费者（托盘/灵动岛
图标、legacy 窗口）惰性构建并按帧缓存。overlay 渲染链（PetSprite →
``library.clip_current_image``）只读 ``currentImage()``，每帧 QPixmap 是纯
浪费；预热跑在后台线程，在那里构建 QPixmap 本身也不合法（Qt 要求 GUI 线程）。

预取看门狗（"画面经常卡住不动"的用户实测根因）：worker 所在共享线程失能
时，``_request`` 的 queued 调用永远不被处理，``_advance`` 会停在 ``_awaiting``
上无限等待。``_advance`` 每 tick 复查等待时长：超 ``PREFETCH_STALL_MS``
重发请求 + WARNING（含目录名/帧号/wanted/pending 大小），连续
``PREFETCH_STALL_LIMIT`` 次仍无帧则同步加载兜底（播放链不断）；线程已死
则重建共享线程并换挂新 worker。到货即清零，恢复后静默。
"""
from __future__ import annotations

import atexit
import json
import logging
import threading
import time
from pathlib import Path

from PySide6.QtCore import (
    QCoreApplication,
    QMetaObject,
    QObject,
    Qt,
    Q_ARG,
    QThread,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QImage, QPixmap

logger = logging.getLogger(__name__)

DEFAULT_FPS = 24.0

#: 预取看门狗阈值（ms）：播放位置停在 ``_awaiting`` 上超过它即判定预取失能
#: （worker/共享线程事件循环停摆），重发请求并记 WARNING。
PREFETCH_STALL_MS = 500.0
#: 连续超时次数达到它 → 降级同步加载兜底（保证播放链不断，不跳帧不冻结）。
PREFETCH_STALL_LIMIT = 3


def _now() -> float:
    """看门狗时钟（模块属性可替换：测试注入假钟，不 sleep 赌时序）。"""
    return time.monotonic()


# 预取线程进程级共享（每 clip 一个 QThread 的方案被实机否决：clip 销毁时
# 运行中的 QThread 触发 access violation——tests/test_move_sync.py 实崩）。
_shared_thread: QThread | None = None


def _shutdown_shared_prefetch() -> None:
    """进程/应用退出收口：停掉共享预取线程。

    不收口则解释器退出时 QApplication 先于运行中的 QThread 销毁，
    Windows 上直接 0xC0000409 fail-fast（pytest 进程尾崩实测）。幂等：
    收口后可由 _shared_prefetch_thread() 重建（测试反复建 QApplication）。
    """
    global _shared_thread
    thread, _shared_thread = _shared_thread, None
    if thread is not None and thread.isRunning():
        thread.quit()
        thread.wait(2000)


def _shared_prefetch_thread() -> QThread:
    """懒建进程级预取线程（clip 只挂 worker，不拥有线程）。"""
    global _shared_thread
    if _shared_thread is not None and not _shared_thread.isRunning():
        _shared_thread = None  # 已被退出收口：按懒建语义重建
    if _shared_thread is None:
        _shared_thread = QThread()
        _shared_thread.setObjectName("frameseq-prefetch-shared")
        _shared_thread.start()
        app = QCoreApplication.instance()
        if app is not None:
            # 生产路径：exec() 退出时 aboutToQuit 收口
            app.aboutToQuit.connect(
                _shutdown_shared_prefetch, Qt.ConnectionType.UniqueConnection)
        # 兜底：无 exec() 的上下文（pytest/脚本——QApplication 析构不发
        # aboutToQuit），atexit 在模块拆除前收口（幂等，注册一次即安）
        atexit.register(_shutdown_shared_prefetch)
    return _shared_thread


class _FramePaths:
    """帧路径序列：按编号现推，不物化 Path 列表（M1 内存口径）。

    素材命名是转换器写出的连续 ``f_%04d.webp``、帧数以 meta.json 的 ``frames`` 为
    权威，因此路径可以按编号现推：``seq[i]`` = ``dir/f_{i+1:04d}.webp``（一个临时
    Path，用完即弃，不常驻）。改前这里是首次播放 glob 出来的 ~241 个 Path 的常驻
    列表（106 段 × 3 宠实测 ≈110MB RSS，且 ``library.release_idle_frames`` 碰不到）。

    meta 缺失/非法（旧产物、转换中断）才 glob 一次兜底——那才是唯一真"列帧表"的
    路径，此时 ``listed`` 非 None。``len()``/``seq[i]`` 的语义与旧列表逐位相同
    （预取 worker 与播放层只用这两个），实例身份恒定（worker 持有的同一引用自动
    可见）；锁只护"glob 一次"（预热跑在后台线程，GUI 线程可能同时在播放）。
    """

    def __init__(self, frames_dir: Path, meta_count: int | None) -> None:
        self._dir = frames_dir
        self._meta_count = meta_count
        self._lock = threading.Lock()
        #: glob 兜底产物（仅 meta 缺失/非法时为非 None）；常态 None = 零物化
        self.listed: list[Path] | None = None

    def __len__(self) -> int:
        if self._meta_count is not None:
            return self._meta_count
        return len(self._resolve())

    def __getitem__(self, index: int) -> Path:
        if self._meta_count is None:
            return self._resolve()[index]
        if index < 0:                      # 与列表同语义（调用方理论上只传非负）
            index += self._meta_count
        if not 0 <= index < self._meta_count:
            raise IndexError(index)
        return self._dir / f"f_{index + 1:04d}.webp"

    def _resolve(self) -> list[Path]:
        """glob 兜底：只列一次（meta 缺失/非法时才会走到）。"""
        if self.listed is None:
            with self._lock:
                if self.listed is None:
                    self.listed = sorted(self._dir.glob("f_*.webp"))
        return self.listed


#: 进程级 worker 强钉表（见 ``_PrefetchWorker.__init__`` 注释）：clip 被丢弃而
#: 未 close 的路径上，在途 queued 交付不得命中死包装。
_LIVE_WORKERS: list = []

#: 进程级 clip 强钉表（见 ``FrameSeqClip.__init__`` 注释）：在途 ``loaded``
#: 交付命中析构中的 clip = UAF。生产里 clip 本就被库缓存常驻，钉表零增量。
_LIVE_CLIPS: list = []


class _PrefetchWorker(QObject):
    """后台预取：按路径加载帧（~2.5ms/帧）移出 GUI 线程。

    常驻独立 QThread；交付经 queued 信号传 QImage（隐式共享 + 文件加载
    即独立缓冲，clip 侧无需再拷贝，无跨线程别名）。
    """

    loaded = Signal(int, QImage)

    def __init__(self, frames: _FramePaths, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._frames = frames
        # 强钉入进程级存活表：worker 的 prefetch 是 Python 槽，排队交付执行时
        # 需要 Python 包装对象活着。clip 被丢弃而未 close 时包装随 GC 析构，
        # 在途交付命中死包装 = 共享线程上 0x8 段错误（mac CI 原生栈实锤：
        # QThread::exec → sendPostedEvents → qtPythonMetacall）。 worker 是
        # 无父小对象，进程级钉住有界（每 clip 一个）。
        _LIVE_WORKERS.append(self)

    @Slot(int)
    def prefetch(self, idx: int) -> None:
        if 0 <= idx < len(self._frames):
            self.loaded.emit(idx, QImage(str(self._frames[idx])))


class FrameSeqClip(QObject):
    """与 WebMClip 接口兼容的帧序列 clip（链式一次性播放）。"""

    frameChanged = Signal(int)
    finished = Signal()
    errorOccurred = Signal(str)
    #: 内部：后台预热解出的帧 0 → 显示槽提交（跨线程队列投递，见
    #: ``warm_first_frame``）。用信号而不是 QMetaObject.invokeMethod：信号
    #: 的参数类型在类创建时就绑定了，不受模块级 ``QImage`` 被测试替身替换
    #: 的影响（``Q_ARG(QImage, ...)`` 会在运行期读那个名字）。
    _warm_frame_ready = Signal(QImage)

    #: 帧 0 预热的**重复代价可忽略**：冷解码 ~1.2ms，且 ``start()`` 本就异步
    #: 交付首帧（未到货时继续显示旧帧，不阻塞 GUI）——同角色兄弟库因此不重复
    #: 预热这一帧（见 ``MovieLibrary._warm_objects`` 的 _warm_peer 分支）。
    #: WebMClip 故意没有这个标记：它的首帧冷路径要 spawn 一个 ffmpeg（60~166ms），
    #: 兄弟库必须覆盖，那笔重复由 ``webm_clip`` 的跨库首帧共享表消掉。
    FIRST_FRAME_WARM_TRIVIAL = True

    def __init__(self, frames_dir: Path | str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # 强钉入进程级存活表（与 ``_LIVE_WORKERS`` 同族防线）：worker 经 queued
        # 信号交付 ``loaded`` 到本 clip 的 ``_on_loaded``（Python 槽）；clip 被
        # 丢弃而未 close 时，GUI 线程析构撞上共享线程的在途交付 = UAF 段错误。
        # 生产里 clip 本就由库缓存常驻，钉表只兜"丢弃未收口"路径，量有界。
        _LIVE_CLIPS.append(self)
        self._dir = Path(frames_dir)
        self._fps = DEFAULT_FPS
        #: meta.json 里的整数帧数（>0 才有效；缺失/非法 = None，计数走列目录兜底）
        self._meta_count: int | None = None
        try:
            meta = json.loads((self._dir / "meta.json").read_text(encoding="utf-8"))
            self._fps = float(meta.get("fps") or DEFAULT_FPS)
            # 与 frameseq_provision.is_complete 同一口径：帧数必须是正整数
            # （转换器的 _write_meta 就是这么写的），浮点/字符串一律当无效。
            meta_count = meta.get("frames")
            if type(meta_count) is int and meta_count > 0:
                self._meta_count = meta_count
        except (OSError, ValueError):
            pass  # 缺 meta 按默认 fps（与包内素材 24fps 一致）
        if self._fps <= 0:
            self._fps = DEFAULT_FPS
        #: 帧表（M1 去物化）：按编号现推路径、帧数以 meta 为权威——构造阶段绝不
        #: glob，播放路径也不物化 Path 列表（改前每 clip 常驻 ~239 个 Path，见
        #: ``_FramePaths``）。实例身份恒定，worker 持有的同一引用自动可见。
        self._frames = _FramePaths(self._dir, self._meta_count)
        self._cur = 0
        self._img: QImage | None = None
        #: 显示槽里那张 QImage 对应的帧号（-1 = 无图）。与 _cur 的区别只在
        #: warm_first_frame（后台预热装载帧 0 但不动播放位置）——start() 靠它
        #: 判断"显示槽已经就是第 0 帧"，避免又让 worker 解一遍（O1）。
        self._img_frame = -1
        self._pm: QPixmap | None = None
        self.playback_speed = 1.0
        self._running = False
        # 播放节拍暂停（O3）：隐藏/挂起期为 True——定时器停摆（不推进、不预取
        # 下一帧），播放位置/在途帧/显示图原地保留，恢复时从暂停处续播。
        self._paused = False
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._advance)
        # 待落地的帧表间隔（见 _apply_interval）：运行中真变速排到下一次
        # _advance 落地，避免重设 QTimer 截断当拍倒计时
        self._pending_interval: int | None = None
        # 异步预取（GUI 零解码）：pending = 已到货未上屏，wanted = 在途请求，
        # awaiting = 播放位置在等的帧号（到货即上屏）；线程进程级共享，
        # clip 只挂 worker（退役经 close()→断开信号+留引用，不跨线程销毁）
        self._pending: dict[int, QImage] = {}
        self._wanted = -1
        self._awaiting = -1
        # 预取看门狗状态：awaiting 起点（monotonic；None = 不在等）+ 连续超时次数
        self._awaiting_since: float | None = None
        self._stall_count = 0
        # 已退役 worker（共享线程死亡后换新 worker，旧对象的线程亲和性留在死
        # 线程上——不能 deleteLater（没人处理），只能保留引用不跨线程销毁）
        self._retired_workers: list[_PrefetchWorker] = []
        self._worker = _PrefetchWorker(self._frames)
        #: close() 幂等标志（worker 退役只许一次：重复断信号/重复入列无意义）
        self._closed = False
        # 预热帧的提交走 clip 自身线程（QueuedConnection 队列投递）：后台预热
        # 线程只解码，不在 GUI 侧状态（_img/_img_frame/_pending）上并发写。
        self._warm_frame_ready.connect(
            self._commit_warm_first_frame, Qt.ConnectionType.QueuedConnection)
        self._prefetch_thread = _shared_prefetch_thread()
        self._worker.moveToThread(self._prefetch_thread)
        self._worker.loaded.connect(self._on_loaded,
                                    Qt.ConnectionType.QueuedConnection)

    # ---------------------------------------------------------------- 元信息
    def frameCount(self) -> int:
        return max(1, self._frame_count())

    def duration(self) -> float:
        return self._frame_count() / self._fps / self.playback_speed

    def currentFrameNumber(self) -> int:
        return self._cur

    def currentTimeSeconds(self) -> float:
        frames = self._frame_count()
        if frames <= 0:
            return 0.0
        return self._cur * (self.duration() / frames)

    # ---------------------------------------------------------------- 当前帧
    def currentImage(self) -> QImage | None:
        return self._img

    def currentPixmap(self) -> QPixmap | None:
        """当前帧的 QPixmap（惰性构建、按帧缓存；无帧返回 ``None``）。

        接口语义与改前逐位一致：返回值仍是**当前显示帧**那一张、非空；只是
        构建时机从"每帧上屏时"挪到"消费者第一次要"（托盘/灵动岛图标、
        legacy 窗口）。同一帧重复请求复用同一对象，新帧到货即失效。
        """
        if self._img is None:
            return None
        if self._pm is None:
            self._pm = QPixmap.fromImage(self._img)
        return self._pm

    def clear_display_frame(self) -> None:
        """清显示槽（``MovieLibrary.release_idle_frames`` 回收残留帧的入口）。

        pinned clip 也清（2026-09-27 真机 A/B 撤回 O1 的 pinned 保留）：保留
        每 pinned clip 1 帧在三宠下实测多占私有内存 ~17.5MB（7 段 × 3 库 ×
        0.88MB，`.scratch/windows-parity-20260926-a/opt-ab-20260927-b/`），换来的
        只是一次 ~1.2ms 的帧 0 解码——与 WebMClip 不同，帧序列冷解码本来就快，
        不值得常驻。惰性 pixmap 同样丢掉（下个消费者按需重建）。
        """
        self._pm = None
        self._img = None
        self._img_frame = -1
        self._pending.clear()

    # ---------------------------------------------------------------- 播放控制
    def start(self) -> bool:
        """从第 0 帧起播（链式模型：播完发 finished，循环由上层重启）。

        首帧异步交付（~2.5ms 后到，frameChanged 通知——与 WebMClip 的
        冷路径语义一致，只是从 60-166ms 缩到 ~2.5ms）。

        帧 0 已到手时不重复解码（O1）：预热（``warm_first_frame``）解出的帧 0
        直接寄存 ``_pending[0]``、显示槽已是帧 0（``jumpToFrame(0)`` 起播、
        上一圈停在帧 0）则原地复用——两种情况都省掉 worker 的第二次帧 0 解码
        与一次线程往返（实测单帧解码 ~1.2ms，见证据目录 BENCH-frame-path.txt
        「帧 0 解码」列：改前 2 次/改后 1 次，每次起播都付）。
        """
        if not self._frames:               # 帧表按 meta 现推；0 帧 = 空素材目录
            self.errorOccurred.emit(f"frameseq: 空素材目录 {self._dir}")
            return False
        self._timer.stop()
        self._cur = 0
        self._clear_awaiting()
        img = self._pending.pop(0, None)
        if img is not None:
            self._apply(img, 0)
            self.frameChanged.emit(0)
        elif self._img is not None and self._img_frame == 0:
            # 显示槽已是帧 0（预热装载/jumpToFrame(0) 起播/上一圈停在帧 0）：
            # 直接起播并补发帧 0 通知，零解码
            self.frameChanged.emit(0)
            # 预取链深度与旧路径保持一致（旧路径帧 0 一到货就 `_request(1)`）：
            # 不预取帧 1 会让起播后第一个 tick 停在等待上（不跳帧但白等一拍）
            self._request(1)
        else:
            # 帧 0 尚未到货：**不清显示槽**——保留预热帧/上一圈末帧直到新帧
            # 交付（与 WebMClip edecd57 的语义对齐）。清空会让 sprite.paint
            # 在帧 0 到货前取不到帧直接 return，sprite 区域画透明 = 桌宠闪
            # 消失一瞬（每次切动画都触发）。新帧到货自然覆盖。
            self._set_awaiting(0)
            self._request(0)
        self._running = True
        if not self._paused:
            self._set_interval_now(self._interval_ms())
            self._timer.start()
        return True

    def stop(self) -> None:
        self._running = False
        self._timer.stop()
        # 在途到货帧只对"这一圈播放"有意义，停播后留着就是每 clip 1 帧的
        # 常驻浪费（pinned 例外：预热帧 0 寄存在 _pending 里供 start() 复用）。
        # ``_wanted`` 是"请求在途"标记，照旧保留——真到货时会按 _awaiting
        # 匹配上屏，误清反而会让 _request 去重拦掉本该发出的请求。
        if not getattr(self, "_ffr_pinned", False):
            self._pending.clear()

    def pause(self) -> None:
        """暂停播放节拍（O3：隐藏/挂起期零帧推进、零预取）。

        只停自身的 QTimer：播放位置 ``_cur``、在途/已到货帧 ``_pending``、
        等待态 ``_awaiting`` 与显示图 ``_img`` 全部原地保留（``stop()`` 会清
        ``_pending`` 并置 ``_running=False``，语义不同，绝不能拿来当暂停用）。
        恢复走 ``resume()``，从暂停处续播——绝不 ``jumpToFrame(0)``。
        """
        if self._paused:
            return
        self._paused = True
        self._timer.stop()

    def resume(self) -> None:
        """恢复播放节拍（从暂停处续播）。已停播（``_running=False``）时只清标记。"""
        if not self._paused:
            return
        self._paused = False
        if self._running:
            self._set_interval_now(self._interval_ms())
            self._timer.start()

    def close(self) -> None:
        """停止播放并退役预取 worker（MovieLibrary.shutdown/收尾调用）。幂等。

        worker 退役 = 断开交付信号 + 留引用（``_retired_workers``），**绝不
        deleteLater**：共享线程被看门狗重建/退出收口杀掉后，挂在死线程队列里
        的 DeferredDelete 没有事件循环处理，QThread 销毁/重建时的处置竞态在
        全套件上下文实测就是 access violation（2026-10-03 邻域连跑：deleteLater
        版 3/17 崩、退役版 0/6；同文件 :84-92 的 AV 前科与 ``_revive_prefetch_worker``
        的退役先例同因——跨线程销毁才是真风险）。进程退出时 worker 随共享线程
        一起由 Qt 收口，退役累积量 = 每 clip 一个小 QObject，有界。
        """
        if self._closed:
            return
        self._closed = True
        self.stop()
        old = self._worker
        try:
            old.loaded.disconnect(self._on_loaded)
        except (RuntimeError, TypeError):
            pass  # 未连接过/对象已毁：忽略（与 _revive_prefetch_worker 同口径）
        self._retired_workers.append(old)

    def jumpToFrame(self, frame_index: int) -> bool:
        if not self._frames:
            return False
        frame_index = max(0, min(int(frame_index), len(self._frames) - 1))
        # 低频同步路径（拖拽/复位）：一次 ~2.5ms 同步加载换立即生效
        img = QImage(str(self._frames[frame_index]))
        if not img.isNull():
            self._cur = frame_index
            self._clear_awaiting()
            self._apply(img, frame_index)
        self.frameChanged.emit(frame_index)
        return True

    def set_playback_speed(self, speed: float) -> None:
        """写入播放速率（用户速率 × 飞行倍率）。

        运行中的帧表按 ``_apply_interval`` 落地新间隔：同值不碰 QTimer、真变速
        排到下一次 ``_advance`` 落地——对运行中的 QTimer ``start(ms)`` 会重开
        倒计时，飞行期每 ~4 tick 的写入会把帧交付一路推迟（实机"上下飞帧数上
        不去"，见 tests/test_flight_frame_pacing.py）。
        """
        self.playback_speed = max(0.1, float(speed))
        if self._running and not self._paused:
            self._apply_interval(self._interval_ms())

    def _apply_interval(self, interval: int) -> None:
        """落地帧表间隔：同值不重设；运行中真变速排到下一次 timeout 落地。

        对运行中的 QTimer 重设间隔（``setInterval`` / ``start(ms)`` 同语义）会
        重开倒计时，抹掉当拍已走掉的时间（下一帧最多被推迟一个整间隔）。同值
        直接不碰；真变了且帧表在跑 → 记 ``_pending_interval``，由 ``_advance``
        入口落地（那一刻倒计时本来就要重开，零额外代价）。帧表未运行时立即
        落地（起播前设速率的老语义不变）。
        """
        interval = max(1, int(interval))
        if interval == self._timer.interval():
            self._pending_interval = None
            return
        if self._timer.isActive():
            self._pending_interval = interval
            return
        self._pending_interval = None
        self._timer.setInterval(interval)

    def _flush_pending_interval(self) -> None:
        """把排队的间隔落地（由帧表 timeout 入口调用：倒计时刚重开，零代价）。"""
        pending = self._pending_interval
        if pending is None:
            return
        self._pending_interval = None
        if pending != self._timer.interval():
            self._timer.setInterval(pending)

    def _set_interval_now(self, interval: int) -> None:
        """立即落地间隔（帧表未运行 / 即将 start 的路径：没有倒计时要保）。"""
        self._pending_interval = None
        self._timer.setInterval(max(1, int(interval)))

    # ---------------------------------------------------------------- 解码层兼容（本实现无 webm 解码层语义，全为良性 no-op）
    def warm_meta(self) -> None:
        return

    def warm_first_frame(self) -> None:
        """预热帧 0（幂等：显示槽已有帧即返回）。

        解出的帧 0 同时寄存 ``_pending[0]``：``start()`` 起播时直接弹出来上屏
        （``frameChanged(0)`` 同步发出），不再让 prefetch worker 又解一遍
        （每段动画每次起播省一次帧解码 + 一次线程往返）。

        本方法跑在**后台预热线程**（library._warm_objects 的 worker），因此
        只做解码（QImage 加载，IO），提交回显示槽经 ``_warm_frame_ready`` 队列
        投递到 clip 所属线程（``_commit_warm_first_frame``）：显示槽是播放状态
        （``_img``/``_img_frame``/``_pending``），GUI 起播/跳帧/池级回收都在
        并发写它，后台直写会把新提交的帧倒写成帧 0（图与帧号不一致），或把
        ``release_idle_frames`` 刚清空的槽重新填回。同线程调用（GUI 侧预热）
        保持原有的同步语义。
        """
        if self._img is not None or not self._frames:
            return
        img = QImage(str(self._frames[0]))
        if img.isNull():
            return
        try:
            if self.thread() is QThread.currentThread():
                self._commit_warm_first_frame(img)
                return
            self._warm_frame_ready.emit(img)
        except RuntimeError:
            pass  # clip 半销毁（C++ 侧已删）：安静降级，等下一次起播同步解码

    def _commit_warm_first_frame(self, img: QImage) -> None:
        """把后台预热解出的帧 0 提交进显示槽（只在 clip 所属线程执行）。

        复核与解码之间隔着一次跨线程排队，所以这里必须重新判空：期间前台
        已经提交过新帧（起播/跳帧）就不动它——预热帧只值一次 ~1.2ms 解码，
        绝不能倒写显示槽。
        """
        if self._img is not None or self._running:
            return
        self._apply(img, 0)
        self._pending[0] = img

    def cancel_first_frame_warm(self) -> None:
        return

    @property
    def decode_throttle_divisor(self) -> int:
        """与 WebMClip 同形的只读属性——window/decode_fanout 按属性读
        （普通方法会在该路径 TypeError，4.4b 评审实测 484~546 次/45s）。"""
        return 1

    @property
    def decode_pace_external(self) -> bool:
        return False

    def set_decode_pace_external(self, _value: bool) -> None:
        return

    def set_decode_throttle(self, _divisor: int) -> None:
        return

    def set_recycle_minutes(self, _minutes: int) -> None:
        return

    # ---------------------------------------------------------------- 内部
    def _frame_count(self) -> int:
        """帧数快查：meta 的 ``frames`` 为权威（O(1)，零 glob）。

        帧表已按 meta 现推 → ``len`` 就是 meta 帧数；meta 缺失/非法（旧产物、转换
        中断）才由 ``_FramePaths`` 列一次目录兜底——那是唯一会 glob 的元信息路径。
        """
        return len(self._frames)

    def _interval_ms(self) -> int:
        return max(1, round(1000.0 / self._fps / self.playback_speed))

    def _apply(self, img: QImage, frame: int) -> None:
        """上屏一帧：只存 QImage + 帧号；pixmap 交给 ``currentPixmap()`` 惰性建。

        ``frame`` 是这张图对应的源帧号（调用点都持有），``start()`` 靠它判断
        显示槽是否已经是第 0 帧（帧号不能用 ``_cur`` 代替：预热装载不动播放
        位置，``start()`` 又会先把自己置 0）。
        """
        self._img = img
        self._img_frame = frame
        self._pm = None  # 新帧到货 → 惰性 pixmap 缓存失效

    def _request(self, idx: int) -> None:
        """请求 worker 预取一帧（幂等去重：已到货/在途不重发）。

        帧表按编号现推（``_FramePaths``），worker 侧同样零 glob。
        """
        if idx >= len(self._frames) or idx in self._pending or idx == self._wanted:
            return
        self._wanted = idx
        QMetaObject.invokeMethod(self._worker, "prefetch",
                                 Qt.ConnectionType.QueuedConnection,
                                 Q_ARG(int, idx))

    @Slot(int, QImage)
    def _on_loaded(self, idx: int, img: QImage) -> None:
        self._wanted = -1
        if img.isNull():
            return  # 坏帧：保持现状，不崩播放链
        if self._running and idx == self._awaiting:
            self._cur = idx
            self._clear_awaiting()  # 到货 = 恢复：看门狗状态清零，此后不再告警
            self._apply(img, idx)
            self.frameChanged.emit(idx)
            self._request(idx + 1)  # 链式预取下一帧
        else:
            self._pending[idx] = img

    def _advance(self) -> None:
        self._flush_pending_interval()  # 排队的变速在本次 timeout 落地（零代价）
        nxt = self._cur + 1
        if nxt >= len(self._frames):
            # 链式一次性播放到尾：停表并发 finished（上层据此接下一个动画）
            self._running = False
            self._timer.stop()
            self.finished.emit()
            return
        img = self._pending.pop(nxt, None)
        if img is not None:
            self._cur = nxt
            self._apply(img, nxt)
            self.frameChanged.emit(nxt)
            self._request(nxt + 1)
        else:
            # 未到货：等待不跳帧（同 WebMClip 空转语义），并向 worker 催取
            self._set_awaiting(nxt)
            self._request(nxt)
            self._check_prefetch_watchdog()

    # ---------------------------------------------------------------- 预取看门狗
    def _set_awaiting(self, idx: int) -> None:
        """登记"播放位置在等 idx"；帧号变化 = 新一轮等待（超时计数清零）。

        同一帧的重复调用不改起点——否则每次 ``_advance`` 都会把计时推后，
        看门狗永远不会到点（挂死检测失效）。
        """
        if self._awaiting != idx or self._awaiting_since is None:
            self._stall_count = 0
            self._awaiting_since = _now()
        self._awaiting = idx

    def _clear_awaiting(self) -> None:
        """到货/跳帧收口：清等待态与超时计数（恢复后保持静默）。"""
        self._awaiting = -1
        self._awaiting_since = None
        self._stall_count = 0

    def _check_prefetch_watchdog(self) -> None:
        """预取看门狗：awaiting 挂死超时 → 重发请求；连续超时 → 同步加载兜底。

        worker/共享线程失能（退出收口后的孤儿 worker、线程事件循环停摆）时，
        ``_request`` 的 queued 调用永远不被处理，``_advance`` 就一直等不到帧——
        用户可见表现即"画面卡住不动"。这里按墙钟兜底：到点重发 + WARNING（含
        clip 目录名/帧号/wanted/pending 大小），连续 ``PREFETCH_STALL_LIMIT``
        次仍无帧就同步读一帧顶上，保证播放链不断。
        """
        since = self._awaiting_since
        if since is None or self._awaiting < 0:
            return
        now = _now()
        waited_ms = (now - since) * 1000.0
        if waited_ms < PREFETCH_STALL_MS:
            return
        # 到点即重置计时：下一次判定在又一个阈值之后（不刷屏），超时计数累加
        self._awaiting_since = now
        self._stall_count += 1
        idx = self._awaiting
        thread_alive = self._prefetch_thread_alive()
        if self._stall_count < PREFETCH_STALL_LIMIT:
            logger.warning(
                "frameseq 预取超时 %.0fms：clip=%s frame=%d wanted=%d pending=%d "
                "thread_alive=%s（重发预取请求）",
                waited_ms, self._dir.name, idx, self._wanted, len(self._pending),
                thread_alive)
            if not thread_alive:
                self._revive_prefetch_worker()
            self._wanted = -1  # 清在途标记，让 _request 的重发不被去重拦下
            self._request(idx)
            return
        logger.warning(
            "frameseq 预取连续 %d 次超时（本次等待 %.0fms）：clip=%s frame=%d "
            "wanted=%d pending=%d thread_alive=%s，降级同步加载",
            self._stall_count, waited_ms, self._dir.name, idx, self._wanted,
            len(self._pending), thread_alive)
        self._stall_count = 0
        img = QImage(str(self._frames[idx]))   # 同步兜底直读帧路径（帧表按编号现推）
        if img.isNull():
            return  # 坏帧：保持现状，下个 tick 重新走看门狗
        if not self._running:
            return
        self._cur = idx
        self._clear_awaiting()
        self._apply(img, idx)
        self.frameChanged.emit(idx)
        self._request(idx + 1)  # 同步兜底后仍续链式预取（worker 恢复即接回异步）

    def _prefetch_thread_alive(self) -> bool:
        """共享预取线程复查：线程对象失效/已停 = 死（异常一律按死处理）。

        ``_prefetch_thread`` 是本 clip 对共享线程的强引用：``_shared_thread``
        重建时旧线程若被 GC，``worker.thread()`` 会变悬垂指针，故不直接回查。
        """
        thread = self._prefetch_thread
        if thread is None:
            return False
        try:
            return bool(thread.isRunning())
        except RuntimeError:
            return False

    def _revive_prefetch_worker(self) -> None:
        """共享线程已死 → 重建线程并换新 worker（``_shared_prefetch_thread`` 有懒建语义）。

        ``moveToThread`` 只能由对象所属线程调用（GUI 线程调会被 Qt 拒绝），而旧
        worker 的亲和性绑在已停线程上，因此这里换一个新 worker；旧 worker 只留
        引用不删——跨线程销毁才是真风险（见 ``_retired_workers`` 注释）。
        """
        old = self._worker
        try:
            old.loaded.disconnect(self._on_loaded)
        except (RuntimeError, TypeError):
            pass
        self._retired_workers.append(old)
        # 不设上限裁剪（2026-10-03）：裁掉引用 = 让 Python GC 在某个随机线程
        # 析构一个亲和于共享线程的 QObject——跨线程销毁正是这条路要防的 AV。
        # worker 是无父小 QObject，退役累积量有界（每 clip 一个 + 复活几次）。
        self._worker = _PrefetchWorker(self._frames)
        self._prefetch_thread = _shared_prefetch_thread()
        self._worker.moveToThread(self._prefetch_thread)
        self._worker.loaded.connect(self._on_loaded,
                                    Qt.ConnectionType.QueuedConnection)
        logger.warning("frameseq 共享预取线程失能：已重建并换挂新 worker（clip=%s）",
                       self._dir.name)
