# -*- coding: utf-8 -*-
"""
Media library —— 多形象，自动识别 webm / gif。

支持按角色 ID 加载不同形象：
- 默认从内置 assets/characters/<character_id>/videos/ 加载
- 也支持外部扩展目录（exe 同目录/用户数据目录下的 characters/<id>/videos）
- 如果目录里是 *.webm 则用 WebMClip；如果是 *.gif 则用 GifClip

对外保持与窗口层一致的形状：
- movie(name) -> clip object
- movies() -> name -> clip mapping
- frames(name) / duration(name)（秒）

WebMClip 基于 imageio-ffmpeg 解码 640×360 透明 webm（RGBA）。
GifClip 基于 QMovie 播放透明 GIF（兼容旧 GIF 路线）。
"""

from __future__ import annotations

import logging
import queue
import random
import threading
import time
import weakref
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QImage, QMovie

from . import catalog
from . import frameseq_provision
from . import perfstats
from . import webm_clip
from .webm_clip import WebMClip, session_ending

_LIVE_MOVIE_LIBRARIES: weakref.WeakSet = weakref.WeakSet()

#: 兄弟库预热首帧前的有界等待上限（秒）：责任持有者 7 段交互核 ≈0.5s 解完，
#: 给 2s 宽预算；等不到就照旧自己解（正确性不依赖等待成功）。
PEER_FIRST_FRAME_WAIT_S = 2.0

# 供给线程超出有界等待后的落脚点：QThread 带着活线程被销毁是 Qt 的 fatal
# （"QThread: Destroyed while thread is still running" → abort），而库随窗口/进程
# 销毁时就会命中——超时后把线程摘出库、由这里强引用持有，跑完再由
# reap_orphan_provision_workers() 摘除。宁可留一个正在收尾的线程，绝不销毁活线程。
_ORPHAN_PROVISION_WORKERS: set = set()
_ORPHAN_PROVISION_LOCK = threading.Lock()


def orphan_provision_workers() -> tuple[object, ...]:
    """当前被登记为孤儿（超出有界等待仍未退出）的帧序列供给线程快照。"""
    with _ORPHAN_PROVISION_LOCK:
        return tuple(_ORPHAN_PROVISION_WORKERS)


def reap_orphan_provision_workers() -> int:
    """摘掉已退出的孤儿供给线程（返回摘掉数）；还在跑的继续持有。

    ``deleteLater``（库给 worker 接的收尾）先到会让 Python 包装对象失效：
    那同样等于"线程已经走完"，按已退出摘除（不重试、不告警）。
    """
    with _ORPHAN_PROVISION_LOCK:
        workers = list(_ORPHAN_PROVISION_WORKERS)
    done: list[object] = []
    for worker in workers:
        try:
            finished = bool(worker.isFinished())
        except RuntimeError:
            finished = True   # C++ 侧已销毁（deleteLater 已投递）
        except Exception:
            finished = False
        if finished:
            done.append(worker)
    if done:
        with _ORPHAN_PROVISION_LOCK:
            for worker in done:
                _ORPHAN_PROVISION_WORKERS.discard(worker)
    return len(done)


# QMovie 播放速度补偿（%）：GIF 路线使用，校准 QMovie 偏慢问题
PLAYBACK_SPEED = 120

# 素材池低频兜底回收（内存瘦身第一刀）：每 10s 扫一遍"已停播且 reader 已退出"
# 的 clip，把它们残留的解码帧队列与显示槽交还内存。事件驱动的回收已经挂在
# movie()（切换动画 = 残留产生的时刻）上，本定时器只兜住"停播后再也没有新
# clip 被创建"的场景（驻留宽限期满的原地等待、长时间单动画播放等）。
# 单次成本 = 已创建 clip 数（≤素材总数）次属性读；实测 106 段素材下远低于 1ms。
IDLE_FRAME_TRIM_INTERVAL_MS = 10_000


class GifClip(QObject):
    """QMovie 包装：与 WebMClip 接口兼容的 GIF 播放器。"""

    frameChanged = Signal(int)
    finished = Signal()
    errorOccurred = Signal(str)

    def __init__(self, path: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.path = path
        self._movie = QMovie(str(path))
        self._movie.setCacheMode(QMovie.CacheMode.CacheNone)
        self._movie.setSpeed(PLAYBACK_SPEED)
        self._movie.frameChanged.connect(self._on_frame_changed)
        self._movie.finished.connect(self.finished)
        self._movie.error.connect(lambda err: self.errorOccurred.emit(str(err)))
        self._frame_count = 0
        self.playback_speed = 1.0
        self._movie.jumpToFrame(0)
        self._frame_count = max(0, self._movie.frameCount())

    def frameCount(self) -> int:
        if self._frame_count <= 0:
            self._frame_count = max(0, self._movie.frameCount())
        return max(1, self._frame_count)

    def duration(self) -> float:
        return self.frameCount() * catalog.FRAME_MS / 1000.0 / self.playback_speed

    def currentFrameNumber(self) -> int:
        return self._movie.currentFrameNumber()

    def currentTimeSeconds(self) -> float:
        n = self._movie.currentFrameNumber()
        frames = self.frameCount()
        if frames <= 0:
            return 0.0
        return n * (self.duration() / frames)

    def currentPixmap(self):
        return self._movie.currentPixmap()

    def set_playback_speed(self, speed: float) -> None:
        self.playback_speed = max(0.1, float(speed))
        self._movie.setSpeed(int(round(PLAYBACK_SPEED * self.playback_speed)))

    def start(self) -> None:
        self._movie.start()

    def stop(self) -> None:
        self._movie.stop()

    def jumpToFrame(self, frame_index: int) -> bool:
        if frame_index < 0:
            frame_index = 0
        total = self._movie.frameCount()
        if total > 0 and frame_index >= total:
            frame_index = total - 1
        return self._movie.jumpToFrame(frame_index)

    def warm_meta(self) -> None:
        # GIF 由 QMovie 直接管理元数据，无需额外预热
        return

    def _on_frame_changed(self, n: int) -> None:
        fc = self._movie.frameCount()
        if fc > 0:
            self._frame_count = fc
        self.frameChanged.emit(n)


# ------------------------------------------------------------ 同角色媒体共享
# overlay 单进程多 sprite 下，每只与主宠同角色的子宠此前各建一份完整
# MovieLibrary：各解析一遍 manifest/素材路径、各读一遍 no_mirror 与
# move_strides.json、各为**每段素材**重算一次源哈希（帧序列世代身份）、各 new
# 出 106 个 clip 对象、各把同一批素材预热一遍。其中"素材怎么读、有哪些段、
# 帧序列世代是谁、分类怎么切"这部分**在同角色下逐位相同**，重复算三遍纯属浪费。
#
# 共享边界严格画在**只读媒体视图**上（SharedCharacterMedia）：clip 对象、
# 播放位置、定时器、首帧私有缓存**一律不共享**——同角色两只宠同时播同名 clip
# 时各自拿的是各自的 clip 实例，播放态天然隔离。
_SHARED_MEDIA_LOCK = threading.Lock()
_SHARED_MEDIA: "weakref.WeakValueDictionary[tuple[str, str], SharedCharacterMedia]" = (
    weakref.WeakValueDictionary())


def shared_media_key(character_id: str, asset_dir) -> tuple[str, str]:
    """共享键：角色 id + 素材目录的绝对路径（同键 = 素材逐位相同）。"""
    try:
        resolved = str(Path(asset_dir).resolve())
    except OSError:
        resolved = str(asset_dir)
    return (str(character_id), resolved)


def lookup_shared_media(character_id: str, asset_dir):
    """取同角色已有的只读媒体视图；没有活库持有时返回 None。"""
    with _SHARED_MEDIA_LOCK:
        return _SHARED_MEDIA.get(shared_media_key(character_id, asset_dir))


class SharedCharacterMedia:
    """一个角色的**只读**媒体视图：同角色多库共用一份（异角色/异目录不共享）。

    生命期由在用库强引用持有（注册表是弱值表）：最后一个库被回收即整份失效，
    之后新建的库按需重建——切角色重建、退出重进都不留常驻残影。

    只读契约：这些表在库创建后无人写入（`rescan_frameseq` 是唯一例外，见其
    "就地更新共享映射"说明）。**播放态绝不在此**。
    """

    __slots__ = (
        'character_id', 'asset_dir', 'manifest', 'name_paths', 'folder_map',
        'folder_files', 'media_type', 'no_mirror', 'move_strides', 'move_curves',
        'paths', 'frameseq_dirs', 'priority', '_warm_owner', '__weakref__',
    )

    def __init__(self, *, character_id, asset_dir, manifest, name_paths,
                 folder_map, folder_files, media_type, no_mirror, move_strides,
                 move_curves, paths, frameseq_dirs, priority):
        self.character_id = character_id
        self.asset_dir = asset_dir
        self.manifest = manifest
        self.name_paths = name_paths
        self.folder_map = folder_map
        self.folder_files = folder_files
        self.media_type = media_type
        self.no_mirror = no_mirror
        self.move_strides = move_strides
        self.move_curves = move_curves
        self.paths = paths
        self.frameseq_dirs = frameseq_dirs
        self.priority = priority
        self._warm_owner = None

    # -------------------------------------------------------------- 预热责任
    def claim_warm_owner(self, lib) -> bool:
        """认领本份素材的预热责任：首个活库得 True，兄弟库得 False。

        低优先级随机动作池（99 段 clip 对象 + 逐段预热）只需跑一次：素材相同、
        结果相同，兄弟库重复跑只是白建一整套 clip 对象再白预热一遍。责任持有者
        退出时在 MovieLibrary.shutdown 里交给仍活着的兄弟库（_hand_off_shared_warm），
        所以"主宠退出、子宠被提升"这类换手不会把预热责任丢掉。
        """
        with _SHARED_MEDIA_LOCK:
            owner = self._warm_owner() if self._warm_owner is not None else None
            if owner is None or owner is lib:
                self._warm_owner = weakref.ref(lib)
                return True
            return False

    def is_warm_owner(self, lib) -> bool:
        with _SHARED_MEDIA_LOCK:
            owner = self._warm_owner() if self._warm_owner is not None else None
            return owner is lib

    def release_warm_owner(self, lib) -> None:
        with _SHARED_MEDIA_LOCK:
            owner = self._warm_owner() if self._warm_owner is not None else None
            if owner is lib:
                self._warm_owner = None


def publish_shared_media(shared: SharedCharacterMedia) -> None:
    """把一份只读媒体视图登记进进程级注册表（供同角色兄弟库采用）。"""
    with _SHARED_MEDIA_LOCK:
        _SHARED_MEDIA[shared_media_key(shared.character_id, shared.asset_dir)] = shared


class MovieLibrary(QObject):
    """素材库：加载指定形象的 webm 或 gif 动画。"""

    # 后台低优先级预热批次收尾通知（worker 线程 emit → GUI 线程槽）：
    # 批次被 pause_warm 作废（未完成）后，据此在恢复显示时重新排期，
    # 避免"完成标志被旧批次覆盖后无人再排期"。
    low_warm_batch_finished = Signal()

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        character_id: str | None = None,
        asset_dir: Path | str | None = None,
        prewarm_policy: str = "balanced",
        prewarm_enabled: bool = True,
    ) -> None:
        super().__init__(parent)
        _LIVE_MOVIE_LIBRARIES.add(self)
        self.character_id = character_id or catalog.DEFAULT_CHARACTER
        policy = str(prewarm_policy or "balanced").strip().lower()
        self._prewarm_policy = policy if policy in {"full", "balanced", "minimal"} else "balanced"
        if asset_dir is not None:
            self._asset_dir = Path(asset_dir)
        else:
            self._asset_dir = catalog.resolve_character_video_dir(self.character_id)
        self._manifest = None
        self.manifest = catalog.load_character_manifest(self.character_id, self._asset_dir)
        self.folder_map: dict[str, str] = {}
        self.folder_files: dict[str, list[str]] = {}
        self._movies: dict[str, object] = {}
        self._paths: dict[str, Path] = {}
        # 帧序列世代映射（rescan_frameseq 填充；同角色共享同一份 dict 对象，
        # 见该方法"就地更新共享映射"说明）
        self._frameseq_dirs: dict[str, Path] = {}
        # 随机动作池延迟预热：启动后 2s 再以 1 个 worker 慢慢补，避免多开时
        # ffmpeg 进程洪峰；只在高优先级（idle/turn/click/drag/move）就绪后触发。
        self._low_warm_timer = QTimer(self)
        self._low_warm_timer.setSingleShot(True)
        self._low_warm_timer.setInterval(2000)
        self._low_warm_timer.timeout.connect(self._warm_low_priority_background)
        # 隐藏即暂停：桌宠不可见时预热没有任何可见收益，停掉定时器并
        # 让在飞的预热线程尽快退出（低功耗铁律）。
        self._warm_paused = False
        self._low_first_frames_done = False  # 低优先级池首帧预热是否完整跑完
        # 低优先级预热交互让路：用户交互（拖拽/点击动画/右键菜单）期间暂停
        # 随机动作池预热，交互结束后继续；可重入（begin/end 配对计数，
        # 拖拽中再点击等叠加持有）。高优先级预热不走此闸门。
        # Condition 与 _interaction_lock 共用同一把锁：等待线程真正阻塞
        # （交互放行时 notify 唤醒），而不是对已 set 的 Event 高频 wait 空转。
        self._interaction_holders = 0
        self._interaction_lock = threading.Lock()
        self._interaction_cond = threading.Condition(self._interaction_lock)
        # 测试 seam：仅供测试注入，产品侧无调用
        self._interaction_active = threading.Event()  # 观测镜像：set=交互中
        # 预热代次：pause_warm（隐藏/切角色）时自增；在飞的旧代次预热线程
        # 据此放弃，保证旧角色（旧库）的预热不会在交互结束后"复活"。
        # begin_interaction 返回当前代次作为 token，end_interaction(token)
        # 只释放同代次持有：pause_warm 换代的迟到 release 成为 no-op，
        # 不会误释放换代后新交互的持有（可重入配对不被 pause 破坏）。
        self._warm_generation = 0
        # Phase 2：动画预热总开关（默认开）。关闭时不启动高/低优先级预热，
        # 也不在窗口恢复显示时自动 resume 预热；首次播放/交互按需同步解码。
        self._prewarm_enabled = bool(prewarm_enabled)
        # 低优先级批次去重：同一时间最多一个在飞批次（timer 到点/50ms 重试/
        # resume 重排可能并发触发），worker 收尾时在 finally 清除。
        self._low_warm_in_flight = False
        self._warm_state_lock = threading.Lock()  # 保护在飞标志与完成标志
        self._shutdown = False
        # 首跑帧序列供给（B 档）：排期幂等标志 + library 拥有的供给线程句柄
        self._frameseq_provision_requested = False
        self._frameseq_worker = None
        # 锁被占时的有界重试计数（见 _retry_frameseq_provision_after_lock）：三宠三库
        # 抢同一把素材根锁，抢不到的那个若就此收手就再也不会供给。
        self._frameseq_lock_retries = 0
        # 交互中让路重排期：50ms 短间隔重试（交互一结束立即补上，不把 2s
        # 延迟原样再等一遍）；pause_warm 会停掉它，避免遗留 singleShot 在
        # pause 后仍触发起批。
        self._low_warm_retry_timer = QTimer(self)
        self._low_warm_retry_timer.setSingleShot(True)
        self._low_warm_retry_timer.setInterval(50)
        self._low_warm_retry_timer.timeout.connect(self._warm_low_priority_background)
        # 非播放中 clip 的残留帧回收（内存瘦身第一刀）：低频兜底定时器，随
        # 低优先级预热排期开、随隐藏/关闭停（见 pause_warm / shutdown）。
        self._idle_trim_timer = QTimer(self)
        self._idle_trim_timer.setInterval(IDLE_FRAME_TRIM_INTERVAL_MS)
        self._idle_trim_timer.timeout.connect(self._on_idle_trim)
        self.low_warm_batch_finished.connect(self._on_low_warm_batch_finished)
        # 同角色媒体共享（见模块顶部"同角色媒体共享"）：先在进程级注册表里找
        # 有没有同角色活库留下的只读视图——有就直接采用（不读盘、不重算源哈希、
        # 不重建表），没有才自己读一份并发布出去。
        self._shared: SharedCharacterMedia | None = None
        # 是否为本份素材的**兄弟库**（预热责任不在本库）：由 schedule_*_warm /
        # resume_warm 在认领预热责任时刷新，预热期据此跳过重复工作。
        self._warm_peer = False
        shared = lookup_shared_media(self.character_id, self._asset_dir)
        if shared is not None:
            self._adopt_shared_media(shared)
        else:
            self.media_type: str = 'webm'
            self.no_mirror: set[str] = self._load_no_mirror()
            # move_strides.json 一次读取、一次遍历 → (步幅, 曲线) 两份结果：
            # 此前两个加载器各读一遍文件、各遍历一遍 dict（重复 IO，且两套口径
            # 有分叉风险）。加载器方法保留为公开接口（单测按口径直调）。
            self.move_strides, self.move_curves = self._load_move_sidecar()

            self._load_all()
            self._publish_shared_media()
            self._register_high_priority()

    # ------------------------------------------------------------ 同角色媒体共享
    def _adopt_shared_media(self, shared: SharedCharacterMedia) -> None:
        """采用同角色已有库的只读媒体视图（兄弟库路径）。

        不读 manifest/text_clips/move_strides、不解析素材路径、不重算帧序列世代
        （每段一次源 sha256）——这些表逐位沿用首库算好的那份。clip 对象仍由本库
        自己按需创建（播放态隔离）。
        """
        self._shared = shared
        self.manifest = shared.manifest
        self._manifest = shared.name_paths
        self.folder_map = shared.folder_map
        self.folder_files = shared.folder_files
        self.media_type = shared.media_type
        self.no_mirror = shared.no_mirror
        self.move_strides = shared.move_strides
        self.move_curves = shared.move_curves
        self._paths = shared.paths
        self._frameseq_dirs = shared.frameseq_dirs
        self._register_high_priority()

    def _publish_shared_media(self) -> None:
        """把本库刚读出来的只读媒体视图登记进进程级注册表（首个库路径）。"""
        self._shared = SharedCharacterMedia(
            character_id=self.character_id,
            asset_dir=self._asset_dir,
            manifest=self.manifest,
            name_paths=self._manifest,
            folder_map=self.folder_map,
            folder_files=self.folder_files,
            media_type=self.media_type,
            no_mirror=self.no_mirror,
            move_strides=self.move_strides,
            move_curves=self.move_curves,
            paths=self._paths,
            frameseq_dirs=self._frameseq_dirs,
            priority=self._build_priority_names(),
        )
        publish_shared_media(self._shared)

    def _load_no_mirror(self) -> set[str]:
        '''加载 text_clips.json：内含文字的动画在朝向翻转时不镜像（防文字反显）。'''
        import json
        path = self._asset_dir / 'text_clips.json'
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return set()
        names = data.get('no_mirror', [])
        return {str(n) for n in names} if isinstance(names, list) else set()

    def _load_move_sidecar(self) -> tuple[dict[str, float], dict[str, list[float]]]:
        '''加载 move_strides.json：一次读取、一次遍历 → (步幅, 曲线)。

        缺文件/解析失败 → ({}, {})，绝不抛异常；「_comment」等备注字段与其余
        项静默忽略。两份结果共用同一份源数据，保证口径一致（此前两套读取器
        各读一遍文件、各遍历一遍 dict）。
        '''
        data = self._read_move_strides_json()
        strides: dict[str, float] = {}
        curves: dict[str, list[float]] = {}
        for k, v in data.items():
            name = str(k)
            stride = self._move_stride_of(v)
            if stride is not None:
                strides[name] = stride
            curve = self._move_curve_of(v)
            if curve is not None:
                curves[name] = curve
        return strides, curves

    @staticmethod
    def _move_stride_of(v) -> float | None:
        '''单项步幅解析：数值项或 {'stride': 数值} 对象项，其余（含 bool）返回 None。'''
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, dict) and isinstance(v.get('stride'), (int, float)) \
                and not isinstance(v.get('stride'), bool):
            return float(v['stride'])
        return None

    @staticmethod
    def _move_curve_of(v) -> list[float] | None:
        '''单项曲线解析：校验不过（非列表/太短/越界/回退/首尾不符）返回 None。

        curve[i] = 播到源帧 i 时圈内累计进度（0..1，单调不减，首 0 尾 1）。
        动画静帧段曲线走平 → 窗口停住；动帧段匀速 → 动帧才动、静帧不动。
        '''
        if not isinstance(v, dict):
            return None
        curve = v.get('curve')
        if not isinstance(curve, list) or len(curve) < 2:
            return None
        if any(isinstance(c, bool) or not isinstance(c, (int, float)) for c in curve):
            return None
        vals = [float(c) for c in curve]
        if vals[0] != 0.0 or vals[-1] != 1.0:
            return None
        if any(c < 0.0 or c > 1.0 for c in vals):
            return None
        if any(b < a for a, b in zip(vals, vals[1:])):
            return None
        return vals

    def _load_move_strides(self) -> dict[str, float]:
        '''加载 move_strides.json：移动动画每圈（scale=1.0）地面位移像素数。

        缺文件/解析失败 → 空 dict（窗口回退 catalog.MOVE_STRIDE_DEFAULT_PX），
        绝不抛异常。只收数值项与 {'stride': 数值} 对象项："_comment" 等备注
        字段与其余项静默忽略。
        '''
        return self._load_move_sidecar()[0]

    def _load_move_curves(self) -> dict[str, list[float]]:
        '''加载 move_strides.json 对象项里的 curve：圈内逐帧位移曲线。

        校验不过（非列表/太短/越界/回退/首尾不符）静默跳过，绝不抛异常。
        '''
        return self._load_move_sidecar()[1]

    def _read_move_strides_json(self) -> dict:
        import json
        path = self._asset_dir / 'move_strides.json'
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _load_all(self) -> None:
        if self._manifest is None:
            # 自动扫描该形象目录下的 webm 或 gif，支持不同角色有不同动作集
            if not self._asset_dir.is_dir():
                raise FileNotFoundError(
                    f"角色素材目录不存在: {self._asset_dir}（character_id={self.character_id}）"
                )
            webm_files = sorted(self._asset_dir.rglob('*.webm'))
            gif_files = sorted(self._asset_dir.rglob('*.gif'))
            files = webm_files + gif_files
            if not files:
                raise FileNotFoundError(
                    f"角色素材目录中没有 webm/gif 文件: {self._asset_dir}"
                )
            if webm_files and gif_files:
                self.media_type = 'mixed'
            elif webm_files:
                self.media_type = 'webm'
            else:
                self.media_type = 'gif'
            self._manifest = {}
            self.folder_map = {}
            self.folder_files = {}
            for f in files:
                rel = f.relative_to(self._asset_dir)
                name = f.stem
                self._manifest[name] = rel.as_posix()
                folder = rel.parts[0].lower() if len(rel.parts) > 1 else ''
                self.folder_map[name] = folder
                self.folder_files.setdefault(folder, []).append(name)

        missing: list[str] = []
        resolved: dict[str, Path] = {}
        for name, fname in self._manifest.items():
            path = self._asset_dir / fname
            if not path.exists():
                missing.append(f"{name}: {path}")
                continue
            resolved[name] = path

        if missing:
            raise FileNotFoundError("缺少素材文件: " + ", ".join(missing))

        self._paths = resolved
        # 帧序列化 B 档（热集）映射：见 rescan_frameseq()
        self.rescan_frameseq()

        # 高优先级 clip 必须在主线程创建（QObject 线程亲和），再交给后台线程预热；
        # 低优先级由 QTimer 在主线程触发 _warm_low_priority_background 创建。
        # 创建动作在 _register_high_priority（本方法之后单独调用，见 __init__）：
        # 同角色兄弟库采用共享视图时同样要走那一步，但不必重读素材。

        # 预热线程由应用层在 UI 就绪后统一调度（schedule_high_priority_warm /
        # schedule_low_priority_warm），避免库构造时在测试/非事件循环环境里
        # 凭空拉起 ffmpeg 预热线程。

    def _register_high_priority(self) -> None:
        """建高优先级（瞬时交互核）clip 并 pinned 其首帧。

        高优先级 clip 必须在主线程创建（QObject 线程亲和）。pinned = 首帧
        常驻：低优先级随机动作池（数量超首帧预算）的预热浪涌不得把它们逐出，
        否则用户点击/拖拽时被迫 GUI 同步解码首帧，产生可感知的百毫秒级切换
        卡顿（实测定案）。同素材在**跨库首帧共享表**里也 pin 一条（见
        webm_clip.pin_shared_first_frame）：兄弟库晚几秒才 spawn 时，交互核首帧
        不该因为期间播了别的动画就被逐掉，否则它又要重新 spawn 一个 ffmpeg。
        """
        high, _ = self._priority_names()
        for name in high:
            clip = self.movie(name)
            clip._ffr_pinned = True
            path = self._paths.get(name)
            if path is not None:
                webm_clip.pin_shared_first_frame(path)

    def _priority_names(self) -> tuple[list[str], list[str]]:
        """默认优先级：瞬时交互核立刻预热并常驻，其余动画按需/预测预热。

        高优先级（pinned 首帧）= 用户手指的瞬时事件，零预测提前量：
          click（点击）、drag（拖拽）、turn（拖拽变向/掷骰转向）。
        低优先级 = idle / move / 随机动作池：idle-return 与 move 由批10-A1
        预测式预热覆盖（播放点前 ~350ms 后台预解码），且 idle 常播在 LRU 里
        永远热，不需要 pinned 常驻（批10-A3 瘦身，首帧预算随之 32→8MB）。

        同角色共享：分类表随只读媒体视图共享，只算一次（3 库 × 每次预热批次
        都要调一次 build_categories，此前每次都重算）。返回浅拷贝——调用方
        （含测试）可以随便改，不会污染共享的那份。
        """
        shared = getattr(self, '_shared', None)
        if shared is not None:
            high, low = shared.priority
            return list(high), list(low)
        return self._build_priority_names()

    def _build_priority_names(self) -> tuple[list[str], list[str]]:
        """按本库自己的 manifest/目录分类算出优先级（只在本库为首个库时跑）。"""
        names = list(self._manifest)
        cats = catalog.build_categories(
            names,
            # 与运行分类（window.py 建 cats）同一 manifest 口径：外部角色包
            # 以 manifest 声明分类时，预热若按无 manifest 分叉，点击/转向动画
            # 进不了 pinned 高优，首次交互同步 ffmpeg 解码卡顿。
            self.manifest,
            self.folder_map,
            self.folder_files,
        )
        # 点击回应优先级最高：首次点击最怕同步 ffmpeg 解码（实测可达 600ms+），
        # 先预热点击动画，避免用户刚启动就点击时卡顿。
        high = list(dict.fromkeys(
            [*(cats['clicks'] or []), *(cats['turns'] or [])]
            + ([cats['drag']] if cats.get('drag') else [])
        ))
        # 低优先级也必须去重（与 high 同构）：build_categories 在无 idle 兜底时
        # 会把随机动作池里的一个 clip 同时归入 idles 与 acts（Safety fallback），
        # 若不去重则同一素材在单批里被预热两次（重复拉起 ffmpeg）。dict.fromkeys
        # 保序去重，绝不改变池构成。批10-A3 缩池后该路径暴露为 CI 负载 flake。
        low = list(dict.fromkeys(
            n for n in (*(cats['idles'] or []), *(cats['moves'] or []),
                        *(cats['acts'] or [])) if n not in high
        ))
        return high, low

    def _warm_objects(
        self,
        clips: list,
        workers: int,
        *,
        yield_to_interaction: bool = False,
        generation: int | None = None,
        cancelled: Callable[[], bool] | None = None,
        include_frames: bool = True,
    ) -> None:
        """预热已创建的 clip 对象：元数据 +（可选）首帧 QImage（线程安全）。

        include_frames 控制是否预解码首帧：首帧只是消除首次播放卡顿的缓存，
        每段 QImage 约占 640×360×4 ≈ 0.9MB；随机动作池有 40+ 段，全部预解码
        会白白吃掉数十 MB 常驻内存。由 prewarm_policy 决定取舍：
        - full     所有段落都预解码首帧（最流畅，内存最高）
        - balanced 只预解码常用交互动画首帧，随机动作池只取元数据（默认）
        - minimal  一律不预解码首帧，按需同步解码（最省内存，首次播放可能微卡）

        yield_to_interaction=True 时（低优先级随机动作池），每段耗时的
        ffmpeg 预热前检查交互让路闸门：交互进行中阻塞等待，交互结束后继续；
        被 pause_warm（隐藏/切角色）作废则放弃本批（代次检查），不复活。

        cancelled：可选的轻量中途作废检查（高优先级预热用，非阻塞）。每个
        clip 预热前调用，返回 True（已暂停/换代）则跳过该 clip——隐藏/切角色
        发生在预热中途时，旧库不再继续为后续 clip 拉起 ffmpeg（P2 对齐低优
        路径的门控；低优路径走 yield_to_interaction 的阻塞闸门，不受影响）。

        generation：批次认领时（GUI 线程）捕获的代次，随批次传入 worker；避免
        "认领后、worker 真正开始预热前"的快速 pause/resume 让旧批次读到新代次
        而误以为自己是当前批次继续预热（代次捕获窗口闭合，worker 不再自读）。
        """
        if not clips:
            return
        if generation is None:
            generation = self._warm_generation
        n = len(clips)
        nworkers = max(1, min(workers, n))

        def _run_phase(warm: Callable[[object], None]) -> None:
            """用自管守护线程跑一个预热阶段（并发达 `nworkers`、逐 clip 让路/代次）。

            不用 ``ThreadPoolExecutor`` 上下文管理器 + 连续两次 ``ex.map``：在
            极重负载下 executor 的 worker 拿到 None 哨兵后会把 ``_shutdown`` 提前
            置 True 并退出（实测 `BEFORE _warm_first shutdown=True`），导致首帧
            阶段被整个吞掉、批次被误标完成。这里用显式守护线程 + 锁保护下标推进，
            语义与 ``ex.map`` 一致（并发 ≤ workers、每个 clip 先让路/代次检查）。
            """
            state = {'idx': 0, 'failed': False}
            state_lock = threading.Lock()

            def _work() -> None:
                while True:
                    with state_lock:
                        if state['idx'] >= n:
                            return
                        i = state['idx']
                        state['idx'] += 1
                    clip = clips[i]
                    if yield_to_interaction and not self._await_interaction_clear(generation):
                        continue
                    if cancelled is not None and cancelled():
                        continue
                    try:
                        warm(clip)
                    except Exception:
                        pass  # 单个素材预热失败不拖垮整批（与顶层 try/except 一致）

            threads = [
                threading.Thread(target=_work, daemon=True) for _ in range(nworkers)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        _run_phase(lambda c: c.warm_meta())
        if self._warm_paused or not include_frames:
            return  # 窗口已隐藏 / 该批不需要首帧：首帧留到恢复后或首次播放按需进行

        # 预解码各动画首帧（QImage 线程安全），首次播放时零阻塞切换，
        # 避免点击 Q 弹瞬间同步 ffmpeg 解码造成卡顿与旧动画帧残留。
        #
        # 同角色兄弟库跳过"重复代价可忽略"的那类 clip（clip 自己声明
        # FIRST_FRAME_WARM_TRIVIAL，目前只有 FrameSeqClip）：帧 0 冷解码 ~1.2ms
        # 且 start() 本就异步交付首帧，兄弟库再解一遍同一帧是纯重复。
        #
        # 声明白
        # FIRST_FRAME_WARM_ADOPTABLE 的 clip（WebMClip）不跳过：它的首帧冷路径要
        # spawn 一个 ffmpeg（60~166ms），点击时没有首帧会露出旧帧窗口。兄弟库改为
        # **先等责任持有者解出**（有界），等到了就从跨库共享表取用（0 spawn）。
        def _warm_frame(clip) -> None:
            if self._warm_peer:
                if getattr(clip, 'FIRST_FRAME_WARM_TRIVIAL', False):
                    return
                if getattr(clip, 'FIRST_FRAME_WARM_ADOPTABLE', False):
                    self._await_peer_first_frame(clip)
            warm = getattr(clip, 'warm_first_frame', None)
            if callable(warm):
                warm()

        _run_phase(_warm_frame)

    def _await_peer_first_frame(self, clip) -> None:
        """兄弟库预热前：有界等责任持有者解出**同一素材**的首帧。

        三只宠几乎同时启动（连续 spawn / 活跃清单复活）时，三份库的高优先级预热
        是并发跑的：谁都还没解出，跨库共享表就是空的——不发这一等，各自 spawn 一个
        ffmpeg 解同一段素材，并发窗口里的重复照旧（实测三库并发预热 12 次 spawn，
        本该 4 次）。责任持有者 7 段交互核 3 个并发 worker ≈0.5s 解完，这里给 2s
        宽预算轮询共享表。

        等待**成功与否不影响正确性**：等到了 clip 侧预热直接从共享表取用（0 spawn）；
        等不到（责任持有者被隐藏/挂起/已退出）就照旧自己解一遍。
        """
        path = getattr(clip, 'path', None)
        if path is None:
            return
        deadline = time.monotonic() + PEER_FIRST_FRAME_WAIT_S
        while time.monotonic() < deadline:
            if webm_clip.shared_first_frame_ready(path):
                return
            if self._warm_paused or self._shutdown:
                return  # 已隐藏/切角色/收尾：不再等，照旧自己解
            time.sleep(0.02)

    def _await_interaction_clear(self, generation: int) -> bool:
        """低优先级预热让路：交互进行中阻塞等待，交互结束返回 True 继续。

        用 Condition 阻塞等待（事件已 set 时 Event.wait 会立即返回，不能
        用作"等待放行"原语——那样会形成忙循环空转 CPU）；交互结束或被
        pause_warm 复位时 notify 唤醒。被 pause_warm（隐藏/切角色）作废
        （暂停或代次不匹配）返回 False，调用方跳过该 clip —— 旧角色/旧库
        的预热不会在交互结束后复活。
        """
        with self._interaction_lock:
            while self._interaction_holders > 0:
                if self._warm_paused or generation != self._warm_generation:
                    return False
                # 真正阻塞：wait 释放锁并睡眠，交互结束 notify 后立即返回；
                # 50ms 超时只是防丢失唤醒的兜底轮询，不是忙循环。
                self._interaction_cond.wait(timeout=0.05)
            return not self._warm_paused and generation == self._warm_generation

    def set_prewarm_enabled(self, enabled: bool, *, visible: bool | None = None) -> None:
        """运行时开关动画预热（Phase 2）。

        关闭：立即取消在飞/未开始的预热；窗口后续显示也不会自动 resume。
        开启：可见时立即补跑；隐藏时保持暂停，等 showEvent 的 resume_warm 再启动。
        """
        enabled = bool(enabled)
        if self._prewarm_enabled == enabled:
            return
        self._prewarm_enabled = enabled
        if not enabled:
            self.pause_warm()
        elif visible is not False:
            self._warm_paused = False
            self.schedule_high_priority_warm()
            self.schedule_low_priority_warm()

    def warm_allowed(self) -> bool:
        """库级预热闸门（只读判定）：设置页总开关开 **且** 未被隐藏/挂起暂停。

        本库自有的预热路径（``_warm_objects`` / ``warm_predicted`` / 两条
        ``schedule_*``）各自读这两个标志；行为层的两条**直提**路径（预测预热与
        起飞落地预热）不经过它们，需要同一个判据才能守住"关闭后停止后台动画
        预热"的承诺。只读两个 bool，无锁、无副作用（跨线程读安全）。
        """
        return bool(self._prewarm_enabled) and not self._warm_paused

    def pause_warm(self) -> None:
        """窗口隐藏时暂停预热：停掉延迟定时器与让路重试，在飞线程尽快收尾。"""
        self._warm_paused = True
        self._low_warm_timer.stop()
        self._low_warm_retry_timer.stop()
        self._idle_trim_timer.stop()  # 隐藏即停：池级回收也没有可见收益
        self._warm_generation += 1
        # 取消在飞的首帧预热（B7 审查 P1-2）：其拉起的 ffmpeg 进程随 clip 侧
        # 取消（换代 + 主动 terminate）回收，隐藏/切角色后不再有不受控的
        # 后台解码进程存活；恢复显示后新预热仍可正常进行（非终态）。
        for clip in list(self._movies.values()):
            cancel = getattr(clip, 'cancel_first_frame_warm', None)
            if callable(cancel):
                try:
                    cancel()
                except Exception:
                    pass
        # 交互让路闸门同步复位：持有计数清零并放行在飞的低优先级预热线程，
        # 它们醒来后经代次检查发现本批已作废而放弃（旧角色预热不复活）。
        # 计数与 event 在同一临界区更新，并 notify 唤醒等待线程。
        with self._interaction_lock:
            self._interaction_holders = 0
            self._interaction_active.clear()
            self._interaction_cond.notify_all()

    def cancel_frameseq_provision(self, *, timeout_ms: int = 2000) -> bool:
        """取消在飞的首跑供给线程（会话结束/退出收口）：置取消谓词 + terminate
        在飞 ffmpeg（reader 立即返回），有界等待；返回线程是否已退出。幂等。

        这个界成立靠两件事：在飞 ffmpeg 被 terminate（``communicate`` 立即返回），
        退役清扫也逐条目复查同一取消谓词
        （``frameseq_provision._remove_generation_bounded``）——否则一个上千帧的
        世代目录就足以让等待超时。

        超时**不是记一行日志就完事**：此刻线程还活着，而它的 QThread 是本库的子
        对象——库被销毁（窗口关闭、进程退出）时 Qt 会把活线程一起析构，那是 abort
        而不是异常。所以超时路径把线程摘出库（``setParent(None)``，见
        ``_orphan_frameseq_worker``）交给模块级登记处强引用持有，跑完再由
        ``reap_orphan_provision_workers()`` 摘除。等待本身抛异常（缺陷 22）时
        同样如此：异常不是"线程已收口"的证据，线程的状态和超时窗口里一模一样。
        """
        worker, self._frameseq_worker = self._frameseq_worker, None
        if worker is None:
            return True
        try:
            worker.cancel()
        except Exception:
            logging.getLogger(__name__).debug('帧序列供给线程取消失败', exc_info=True)
        try:
            if worker.wait(int(timeout_ms)):
                return True
        except Exception:
            # 缺陷 22：等待失败（QThread 半销毁等）**不是**线程已收口的证据——
            # 此时线程仍 setParent(self) 挂在库上，库被销毁时会连同活线程一起
            # 析构（Qt 对此的处理是 abort）。与超时分支同一落脚点：摘出库 +
            # 交孤儿登记处持有。
            logging.getLogger(__name__).debug('帧序列供给线程等待失败', exc_info=True)
            self._orphan_frameseq_worker(worker)
            return False
        self._orphan_frameseq_worker(worker)
        logging.getLogger(__name__).warning(
            '帧序列供给线程 %dms 内未退出（取消未被及时响应）：已摘出库、'
            '交孤儿登记处持有到跑完，不再随库销毁', int(timeout_ms))
        return False

    @staticmethod
    def _orphan_frameseq_worker(worker: object) -> None:
        """把超出有界等待的供给线程登记为孤儿（保住它不被随库销毁）。

        ``setParent(None)`` 是保护的实质：QThread 还挂在库上时，库一被销毁就会连同
        运行中的线程一起析构（Qt 对此的处理是 abort）。摘掉父子关系 + 模块级强引用
        持有 → 线程自己跑完退出，对象由 ``reap_orphan_provision_workers`` 摘除。
        """
        try:
            worker.setParent(None)
        except Exception:
            logging.getLogger(__name__).debug('供给线程脱离库失败', exc_info=True)
        with _ORPHAN_PROVISION_LOCK:
            _ORPHAN_PROVISION_WORKERS.add(worker)
        reap_orphan_provision_workers()   # 顺手清掉此前已跑完的（廉价、幂等）

    def shutdown(self) -> None:
        """关闭素材库并收口所有已创建的 clip（WebM reader / 帧序列预取 worker）。"""
        if self._shutdown:
            return
        self._shutdown = True
        self.pause_warm()
        # 同角色媒体共享：本库若是预热责任持有者，退出前把责任交给仍活着的兄弟库
        #（见 _hand_off_shared_warm）——否则"主宠退出、子宠被提升"之后整份素材
        # 没有人再预热低优先级池。
        self._hand_off_shared_warm()
        # 首跑供给线程：取消 + 有界等待（口径与超时兜底见
        # cancel_frameseq_provision——关机/切角色窗口里绝不留下不受控的重编码进程）。
        self.cancel_frameseq_provision()
        for clip in tuple(self._movies.values()):
            try:
                # close() 优先（FrameSeqClip：stop + worker 退役留引用）——它的
                # 预取 worker 亲和于共享预取线程，绝不能在 GUI 线程被 GC 析构
                # （跨线程销毁的 AV 前科见 frameseq_clip :84-92）；漏掉这一步
                # 旧 clip 的 worker 就失去受控退役路径。
                close = getattr(clip, 'close', None)
                if callable(close):
                    close()
                    continue
                cleanup = getattr(clip, 'cleanup', None)
                if callable(cleanup):
                    cleanup()
                else:
                    stop = getattr(clip, 'stop', None)
                    if callable(stop):
                        stop()
            except Exception:
                logging.getLogger(__name__).debug(
                    '素材库关闭时收口 clip 失败', exc_info=True,
                )

    def _hand_off_shared_warm(self) -> None:
        """退出时把同角色媒体的低优先级预热责任交给仍活着的兄弟库。

        预热责任只是"这批 99 段 clip 谁来建、谁来预热"的一枚令牌：持有者退出
        （主宠退出 → 子宠被提升为主）时若直接丢令牌，同角色素材就再没人预热了。
        这里显式交接给第一个活着的兄弟库（它下一拍照既有 2s 延迟补跑）。
        """
        shared = getattr(self, '_shared', None)
        if shared is None or not shared.is_warm_owner(self):
            return
        shared.release_warm_owner(self)
        for peer in list(_LIVE_MOVIE_LIBRARIES):
            if peer is self or getattr(peer, '_shared', None) is not shared:
                continue
            if getattr(peer, '_shutdown', False):
                continue
            peer.schedule_low_priority_warm()
            break

    @classmethod
    def _shutdown_live_for_tests(cls) -> None:
        """收口未由窗口持有的素材库，避免 reader 跨测试存活。"""
        # 测试 seam：仅供测试注入，产品侧无调用
        for library in tuple(_LIVE_MOVIE_LIBRARIES):
            try:
                library.shutdown()
            except Exception:
                logging.getLogger(__name__).debug(
                    '测试收口 MovieLibrary 失败', exc_info=True,
                )

    def resume_warm(self) -> None:
        """窗口恢复显示时补齐预热：低优先级池未建完或首帧未预热完则重新排期。

        同角色兄弟库（预热责任在别库）不补跑，且每次恢复都**重新认领一次**
        责任：责任持有者退出后（_hand_off_shared_warm 交出的令牌）本库随下一次
        隐藏/恢复即接手，不必额外轮询。
        """
        if not self._prewarm_enabled:
            return  # Phase 2：动画预热关闭时，隐藏/恢复都不再自动拉起预热
        self._warm_paused = False
        # 池级残留回收随隐藏/恢复成对（与 schedule_low_priority_warm 同款判断）：
        # pause_warm 停掉它，恢复显示时这里必须重启——它的唯一启动点在建库/角色
        # 交接的那次排期，不在这里补上就是"首次隐藏后终身停摆"，已停播 clip 的
        # 残留解码帧再无人回收。兄弟库照样重启：回收管的是本库自己的内存，
        # 与预热责任在谁手里无关（故放在 _warm_peer 早退之前）。
        if not self._idle_trim_timer.isActive():
            self._idle_trim_timer.start()
        self._warm_peer = not self.claim_shared_warm()
        if self._warm_peer:
            return
        try:
            _, low = self._priority_names()
            with self._warm_state_lock:
                incomplete = (
                    any(name not in self._movies for name in low)
                    or not self._low_first_frames_done
                )
            if incomplete and not self._low_warm_timer.isActive():
                self._low_warm_timer.start()
        except Exception:
            pass

    def claim_shared_warm(self) -> bool:
        """认领同角色媒体的预热责任：首个活库得 True，兄弟库得 False。

        没有共享视图（单库/异角色）时恒为 True——单库行为与共享前逐位一致。
        """
        shared = self._shared
        if shared is None:
            return True
        return shared.claim_warm_owner(self)

    def begin_interaction(self) -> int:
        """用户交互开始：低优先级预热让路（可重入，拖拽中再点击等叠加持有）。

        返回当前交互代次 token；释放时须原样传回 end_interaction(token)。
        pause_warm 换代清零后，旧代次 token 的迟到 end 成为 no-op，不会
        误释放换代后新交互的持有（可重入配对不被 pause 破坏）。
        """
        with self._interaction_lock:
            self._interaction_holders += 1
            self._interaction_active.set()
            return self._warm_generation

    def end_interaction(self, token: int | None = None) -> None:
        """用户交互结束：全部持有者释放后恢复低优先级预热。

        token 来自 begin_interaction()；与当前代次不匹配（pause_warm 换代
        后的迟到 release）时忽略。token 为 None 时退化为旧式全局递减
        （兼容无 token 调用方），仅在不涉及换代时使用。
        """
        with self._interaction_lock:
            if token is not None and token != self._warm_generation:
                return
            if self._interaction_holders > 0:
                self._interaction_holders -= 1
            if self._interaction_holders == 0:
                self._interaction_active.clear()
                self._interaction_cond.notify_all()

    def _warm_clips(
        self,
        names: list[str],
        workers: int,
        *,
        generation: int | None = None,
        cancelled: Callable[[], bool] | None = None,
        include_frames: bool = True,
    ) -> None:
        """预热指定动画（调用方需保证 clip 已在主线程创建）。"""
        if not names:
            return
        self._warm_objects(
            [self.movie(name) for name in names], workers,
            generation=generation, cancelled=cancelled,
            include_frames=include_frames,
        )

    def _warm_all_meta_background(self) -> None:
        """高优先级后台预热（独立 daemon 线程，由 schedule_high_priority_warm 调度）。

        门控与低优先级批次对齐（P2）：代次在批次认领这一刻捕获；随机错峰
        sleep 前检查 _warm_paused、sleep 后校验 _warm_paused 与代次；预热
        中途每个 clip 前再查一次（cancelled）。隐藏/切角色/关闭发生在 sleep
        或 metadata 解码期间时，旧库不再拉起 ffmpeg 继续预热（低功耗铁律 +
        旧角色预热不复活）。
        """
        try:
            if self._warm_paused:
                return  # 已暂停（隐藏/切角色）：不进入 sleep、不启动 ffmpeg
            generation = self._warm_generation  # 认领即捕获：worker 不再自读
            # 高优先级（尤其点击回应）尽快开始；保留极短随机错峰降低多开
            # 同时拉起 ffmpeg 的峰值，但不再让首次点击等 0.5s 预热。
            time.sleep(random.uniform(0, 0.05))
            if self._warm_paused or generation != self._warm_generation:
                return  # sleep 期间隐藏/切角色（换代）：本批作废，不拉起 ffmpeg
            # 并发控制在 3：每个 webm 首帧预热都会拉起一个 ffmpeg 子进程，
            # 并发过高会形成进程洪峰，提高杀毒软件拦截/误报概率。
            high, _ = self._priority_names()
            self._warm_clips(
                high, workers=min(3, len(high)),
                generation=generation,
                cancelled=lambda: (self._warm_paused or session_ending()
                                   or generation != self._warm_generation),
                include_frames=(self._prewarm_policy != "minimal"),
            )
        except Exception:
            # 预热失败不致命，后续按需读取时会再尝试
            pass

    def _warm_low_priority_background(self) -> None:
        """启动后延迟补全随机动作池：1 个 worker，避免多开启动 CPU 峰值。

        注意：QTimer 回调运行在主线程，clip 对象必须在主线程创建；
        真正耗时的 ffmpeg 预热放到独立 daemon 线程，避免阻塞事件循环。

        交互让路：拖拽/点击动画/右键菜单期间不创建 clip（主线程开销）、
        不启动预热线程，改为 50ms 短间隔重排期，交互一结束立即补上
        （不把 2s 延迟原样再等一遍）；已暂停（隐藏/切角色）则直接放弃。

        批次去重：同一时间最多一个在飞批次（_low_warm_in_flight），
        timer 到点 / 50ms 重试 / resume 重排的并发触发只保留最早一批；
        已完整预热过则直接跳过（遗留 timer 触发不重复起批）。

        同角色兄弟库：预热责任在同角色首个活库（_warm_peer），本批不起——
        素材相同、结果相同，重复跑只是白建一整套 clip 对象再白预热一遍。
        """
        try:
            if self._warm_paused:
                return  # 已暂停（隐藏/切角色）：不创建 clip、不启动线程
            if self._warm_peer:
                return  # 兄弟库：责任在预热责任持有者，本库不重复起批
            _, low = self._priority_names()
            if not low:
                return
            with self._warm_state_lock:
                if self._low_first_frames_done:
                    return  # 已完整跑完：遗留 timer/重试触发不再重复起批
                if self._low_warm_in_flight:
                    return  # 已有批次在飞：并发触发去重
            with self._interaction_lock:
                interaction_active = self._interaction_holders > 0
            if interaction_active:
                # 交互中：50ms 短间隔重排期；pause_warm 会停掉此 timer，
                # 交互结束（end_interaction）后下一次触发立即起批。
                self._low_warm_retry_timer.start()
                return
            clips = [self.movie(name) for name in low]  # 主线程创建 QObject
            self._low_warm_retry_timer.stop()
            with self._warm_state_lock:
                self._low_warm_in_flight = True
                # 代次必须在 GUI 线程认领批次这一刻捕获并随批次传入 worker：
                # worker 不再自己读 _warm_generation，否则"认领后、线程真正
                # 开始预热前"的快速 pause/resume 会让旧批次把新代次误认成
                # 自己的代次而复活（N2 回归）。
                generation = self._warm_generation

            def run() -> None:
                try:
                    self._warm_objects(
                        clips, 1,
                        yield_to_interaction=True,
                        generation=generation,
                        include_frames=(self._prewarm_policy == "full"),
                    )
                finally:
                    # 记录首帧预热是否完整跑完：中途 pause/换代会跳过首帧阶段，
                    # resume_warm 据此决定是否重新排期（避免"clip 已建但首帧永缺"）。
                    # 完成标志与在飞标志在同一锁内更新：批次唯一（去重）且
                    # 旧批次收尾不可能覆盖新批次的结果。
                    with self._warm_state_lock:
                        self._low_first_frames_done = (
                            not self._warm_paused
                            and generation == self._warm_generation
                        )
                        self._low_warm_in_flight = False
                    # 通知 GUI 线程：批次被 pause 作废（未完成）时由槽重新排期
                    self.low_warm_batch_finished.emit()

            try:
                threading.Thread(target=run, daemon=True).start()
            except Exception:
                # 认领后线程启动失败（如系统资源耗尽）：回滚在飞标志，
                # 否则后续排期被永久去重、低优先级预热彻底停摆（N3 回归）。
                with self._warm_state_lock:
                    self._low_warm_in_flight = False
        except Exception:
            # 预热失败不致命，后续按需读取时会再尝试
            pass

    def _on_low_warm_batch_finished(self) -> None:
        """后台批次收尾（GUI 线程槽）：批次被 pause 作废后恢复排期。

        pause→resume 的快速往返可能让"恢复后的新排期"与"正在收尾的旧批次"
        交错：旧批次收尾时若发现未完成且未暂停，重新启动 2s 定时器补跑，
        避免完成标志被旧批次置 False 后无人再排期（去重保证不会双批并发）。
        """
        try:
            if self._warm_paused or not self._prewarm_enabled:
                return
            if self._warm_peer:
                return  # 兄弟库：本批不由本库跑，没有"批次未完成"要补
            _, low = self._priority_names()
            with self._warm_state_lock:
                incomplete = (
                    any(name not in self._movies for name in low)
                    or not self._low_first_frames_done
                )
            if incomplete and not self._low_warm_timer.isActive():
                self._low_warm_timer.start()
        except Exception:
            pass

    def schedule_high_priority_warm(self) -> None:
        """应用层调用：UI 就绪后后台预热高优先级动画。

        加入 0~0.05s 随机错峰，多开同时启动时避免 ffmpeg 进程洪峰。
        Phase 2：动画预热关闭时不启动。

        同角色兄弟库照样跑本批（只有几段交互核，且首帧由跨库共享表供给，
        等于不花 ffmpeg），但先认领一次预热责任：认领失败即标记兄弟库，
        预热期据此跳过"重复代价可忽略"的帧 0 预热（见 _warm_objects）。
        """
        if not self._paths or not self._prewarm_enabled:
            return
        self._warm_peer = not self.claim_shared_warm()
        threading.Thread(target=self._warm_all_meta_background, daemon=True).start()

    def schedule_low_priority_warm(self) -> None:
        """应用层调用：UI 就绪后延迟补全随机动作池预热（2s 后 1 worker）。

        同角色兄弟库不排这一批（认领失败）：随机动作池 99 段 clip 对象 + 逐段
        预热由预热责任持有者跑一次就够（素材相同、结果相同），兄弟库重复跑只是
        白建一整套 clip 对象再白预热一遍。池级残留回收照旧开——它管的是内存。
        """
        if not self._prewarm_enabled:
            return
        self._warm_peer = not self.claim_shared_warm()
        if not self._warm_peer:
            self._low_warm_timer.start()
        # 预热排期即代表进程进入"会反复切换动画"的常态：启动池级残留回收。
        if not self._idle_trim_timer.isActive():
            self._idle_trim_timer.start()

    # ------------------------------------------------------------------ 池级回收
    def _on_idle_trim(self) -> None:
        """定时器槽：低频兜底回收（异常绝不逃逸到 Qt 事件循环）。"""
        try:
            self.release_idle_frames()
        except Exception:
            logging.getLogger(__name__).debug('素材池残留帧回收失败', exc_info=True)

    @staticmethod
    def _drain_clip_queue(clip) -> int:
        """排空一个 clip 的帧队列，返回释放的字节数（坏对象按 0 计）。"""
        handle = getattr(clip, '_queue', None)
        if handle is None:
            return 0
        freed = 0
        while True:
            try:
                item = handle.get_nowait()
            except queue.Empty:
                return freed
            except Exception:
                return freed  # 半销毁对象：能从队列里拿多少算多少
            if item is None:
                continue  # 圈末结束标记本身不占像素
            try:
                freed += len(item[0])
            except Exception:
                continue

    def release_idle_frames(self) -> int:
        """回收"已停播且 reader 已退出"的 clip 残留解码帧，返回释放字节数。

        实测依据（.scratch/mem-probe/base-overlay-trace，tracemalloc 口径 B）：
        稳态 50.1MB Python 堆集中在 ``pet/webm_clip.py:2545``（``next(it)`` 解出的
        RGBA 帧）共 57 块 —— 66 段素材被切走后各攥着一条 8 帧队列（8×0.879MB）。

        不变量与功能等价（为什么这一刀不换功能）：
        - 队列只被该 clip 自己的 QTimer(_poll) 消费；clip 不在播 = 定时器已停，
          这些帧物理上不可能再被任何路径读到；
        - webm_clip.start() 每次都重建 ``_queue``（maxsize=8），下一次播放拿到的
          是全新队列，不依赖旧队列里的任何一帧；
        - 圈末软停驻留（_soft_parked，等 re-arm 续圈）时 reader 仍存活，被
          "reader 已退出"判据排除；宽限期满 reader 自行退出后，re-arm 已不可能
          成功（_rearm_loop_reader 判 is_alive），start() 必走全新队列路径；
        - 显示槽只在非软停驻留的 clip 上清空（与 webm_clip.clear_display_frame
          的既有契约一致："软停驻留（park）绝不清"）；桌宠真正显示的是
          PetSprite 自己那份 pixmap，清空已停播 clip 的显示槽无可见变化。
        """
        freed = 0
        for clip in tuple(self._movies.values()):
            try:
                if getattr(clip, '_running', False):
                    continue  # 在播：队列是活数据
                thread = getattr(clip, '_thread', None)
                if thread is not None and thread.is_alive():
                    continue  # reader 未退出（含圈末驻留）：队列仍会被续写
                freed += self._drain_clip_queue(clip)
                if not getattr(clip, '_soft_parked', False):
                    clear = getattr(clip, 'clear_display_frame', None)
                    if callable(clear):
                        clear()
            except Exception:
                logging.getLogger(__name__).debug(
                    '回收 %s 的残留帧失败', getattr(clip, 'path', '?'), exc_info=True,
                )
                continue
        return freed

    def stop_all_clips(self) -> None:
        """停止全部已建 clip 的 reader（会话结束/关机专用，issue #111）。

        与 ``shutdown()`` 的区别：只调 ``stop()``（置停止信号 + terminate ffmpeg，
        有界不阻塞 GUI 线程），**不**离线销毁 clip/清缓存/登记孤儿——关机时进程
        随即退出，那些收尾既无收益又会拉长清理窗口。

        逐 clip 兜异常：半销毁（C++ 侧已删）或 stop() 抛错的 clip 不得阻断其余
        clip 的收口，本路径必须尽力而为。

        首跑供给线程一并取消（``cancel_frameseq_provision``）：本方法是**两条拓扑
        唯一的会话结束入口**（legacy 由 ``AppShell._on_session_end`` 逐窗调、overlay
        由 ``OverlayShell._on_session_end`` 逐库调），只停 clip 不取消它，关机窗口里
        那个线程照样能派生转换 ffmpeg——issue #111 要挡的正是这种派生。取消 = 置谓词
        + terminate 在飞进程 + 有界等待：正常毫秒级返回（取消被响应时线程当场退出），
        最坏受 2s 上界约束；真卡住的那个由 ``cancel_frameseq_provision`` 的孤儿兜底
        接管，不再随库销毁（见那里的说明）。
        """
        self.cancel_frameseq_provision()
        for clip in tuple(self._movies.values()):
            try:
                stop = getattr(clip, 'stop', None)
                if callable(stop):
                    stop()
            except Exception:
                logging.getLogger(__name__).debug(
                    '会话结束时停止 clip 失败', exc_info=True,
                )

    def warm_predicted(self, name: str) -> None:
        """批10-A1：后台预解码预测动画的首帧（Phase 1，尽力而为）。

        GLM A-1 / A3：预测预热只复用「交互让路 / 隐藏暂停 / warm_first_frame
        幂等」三重闸门，webm_clip.py 零改动；不预起 reader（Phase 2 挂起）。

        - 交互让路（_await_interaction_clear）：拖拽/点击动画/右键菜单期间等待；
        - 隐藏暂停（_warm_paused / 代次）：pause_warm 换代后作废，不复活；
        - 总开关（_prewarm_enabled）：省电模式关闭预热时不启动；
        - warm_first_frame 幂等：已有缓存直接返回，不重复拉起 ffmpeg。

        预测预热只是消除首播卡顿的缓存；作废/未命中时最坏退化为今天的行为
        （后台短命 ffmpeg 产物进 LRU，被逐出即自然回收）。

        必须在 GUI 线程调用（self.movie(name) 按 QObject thread affinity 在
        主线程创建 clip）；真正耗时的 ffmpeg 解码放到独立 daemon 线程。
        """
        if self._warm_paused or not self._prewarm_enabled:
            return
        if session_ending():
            return  # 会话结束（关机/注销）：绝不起预热线程拉 ffmpeg（issue #111）
        clip = self.movie(name)
        generation = self._warm_generation

        def _run() -> None:
            try:
                if not self._await_interaction_clear(generation):
                    return
                if self._warm_paused or generation != self._warm_generation:
                    return
                warm = getattr(clip, 'warm_first_frame', None)
                if not callable(warm):
                    return
                t0 = perfstats.clock() if perfstats.ENABLED else 0.0
                warm()
                if perfstats.ENABLED:
                    perfstats.time('prewarm.ff_ms', perfstats.clock() - t0)
            except Exception:
                pass  # 预热失败不致命，后续播放按需同步解码

        try:
            threading.Thread(target=_run, daemon=True).start()
        except Exception:
            pass

    def rescan_frameseq(self) -> None:
        """重建帧序列映射（热集 + random/events）：只采纳**当前源身份**的世代目录。

        帧序列化 B 档：assets/characters/<id>/frameseq/<folder>[...]/<stem>.g<戳12>/
        （戳 = 源 webm 的 sha256 前 12 位，meta.json 同戳；见
        ``frameseq_provision`` 模块契约）——目录名即身份，``events/balance/`` 这类
        深层目录的世代目录同样在映射内（扫描递归到任意深度）。采纳前按当前源重算
        sha256 比对（``current_generation``，与生产者共用同一谓词）：源换版 ⇒
        旧世代立即不采纳，本库回退 webm 播放（不冻结、不播错素材），后台供给
        随后转出新世代。档位也在同一谓词里核对：世代必须是**当前档**产物
        （``ENCODER_DESC`` = Q70 + 白底反解），档位不符 = 不采纳（回退 webm 播放）
        ——改档（W1/W2）后旧的「热集无损 / 冷集 Q80」世代全部走这条路径。

        不采纳的形态：旧版无戳 ``<stem>/`` 与 meta 缺戳的产物（**没有身份凭证
        的缓存绝不当当前源用**——真实部署包里的 153MB 帧集正是这个形态）、
        ``<stem>.g<戳>.tmp/`` 半成品、meta 帧数与磁盘不符（半拷贝/丢帧）。
        不采纳 = 现 webm 路径逐行不变；素材包不带 frameseq 时本映射为空、
        零行为变化。

        全程只读目录名 / meta / 帧数，**不读任何帧内容**（155MB 帧集不哈希）：
        库创建路径上的开销 = 每段"已有世代目录"的 clip 一次源哈希（热集 11 段
        5.45MB 实测 ≈12ms 页缓存热；含 random/events 的满配 ≈53MB），没有世代
        目录的 clip 一个字节都不读（先按目录名筛）。身份**每次由内容重算、无 stat
        记忆**，见
        ``frameseq_provision.source_sha256``；没有世代目录的 clip 零开销）。
        已采纳的世代目录登记
        进进程级在用集合，供给线程清扫退役世代时据此跳过（活 clip 可能正在读）。
        集合单调只增：源换版后旧世代只是不再采纳（本进程回退 WebM），它仍留在
        在用集合里——已创建的 FrameSeqClip 还在读那份目录，删除闸门放行不了它。

        首跑自动供给（maybe_provision_frameseq）转换结束后在 GUI 线程调用本
        方法重建映射：此后**新请求**的 clip 走 FrameSeqClip，已创建的 clip
        不动（进程内已有播放器不换实现）。供给轮确实转了新世代或清扫了退役世代时
        才调用（见 ``_on_frameseq_provision_finished``）：本方法要重算源哈希，无事
        可做的收尾里重扫纯属白做。

        就地更新共享映射（同角色多库共用一个 dict 对象）：本方法**原地清空再填充**
        ``self._frameseq_dirs``，绝不重新绑定——兄弟库持有的就是同一个 dict，首库
        供给完成后的重扫因此对所有同角色库同时生效，兄弟库不必各自再哈希一遍源
        （每段一次 sha256，满配 ≈53MB/库）。
        """
        dirs = self._frameseq_dirs
        dirs.clear()
        frameseq_root = self._asset_dir.parent / 'frameseq'
        generations = frameseq_provision.scan_generations(frameseq_root)
        if not generations:
            return
        for name in self._manifest:
            source = self._paths.get(name)
            if source is None:
                continue
            # 廉价钱筛：该 clip 连世代目录都没有 → 不哈希源（零开销）
            if frameseq_provision.clip_base_dir(
                    source, self._asset_dir, frameseq_root) not in generations:
                continue
            generation = frameseq_provision.current_generation(
                source, self._asset_dir, frameseq_root)
            if generation is not None:
                dirs[name] = generation
        if dirs:
            frameseq_provision.protect_generations(dirs.values())

    # ------------------------------------------------------------ 首跑帧序列供给
    def maybe_provision_frameseq(self) -> None:
        """库创建后调用：延迟 5s 后台低优先级供给帧序列（热集 + random/events，幂等入口）。

        PET_FRAMESEQ=0（dev 逃生门）/ 会话结束（issue #111）时静默 no-op；
        「范围内都有可用世代 / 拿不到实例锁 / 无 ffmpeg exe」在供给 worker 内静默
        no-op。只排一个 QTimer.singleShot，绝不阻塞库创建。

        一轮 = 一次启动，代价有上限（``frameseq_provision.provision_once``）：升级
        形态（旧版无戳帧集在场，播放不降级）**每个启动周期合计**只迁一个 clip——配额
        记在进程内共享的台账上（键 = 素材根），三宠三库共用同一份，不是每库一份；
        起手上限到点也不再起新的 clip（已起手的跑完，不硬杀 ffmpeg），余下留到下次
        启动；失败的 clip 按指数退避；磁盘余量不足的 clip 不起手。所以升级后帧序列
        不是一次补齐，未迁到的 clip 继续走现 WebM 路径（行为不变，只是帧序列来得
        晚一点）。供给范围与档位见 ``frameseq_provision``（全部 clip 统一 Q70 +
        白底反解；单 clip 转一段 = 无损抓帧 + 逐帧反解重编码，比旧的一趟 ffmpeg
        慢，因此起手上限与预算口径更吃紧）。
        """
        if self._frameseq_provision_requested:
            return
        if frameseq_provision.provision_disabled() or session_ending():
            return
        self._frameseq_provision_requested = True
        QTimer.singleShot(frameseq_provision.PROVISION_DELAY_MS,
                          self._start_frameseq_provision)

    def _start_frameseq_provision(self) -> None:
        """延迟回调（GUI 线程）：只起 worker，**"有没有活干"的判定全留给 worker 线程**。

        「待转」（``plan_clips``：给供给范围内每个 clip 算一次源哈希——热集 5.45MB
        + random 45MB + events 3MB）、「待清扫」（``pending_retirements``）、
        「回朝旧账」（``stale_current_markers``）这三项以前在**本回调（GUI 线程）**
        里先判一次：满配素材下这是启动后一次可感的卡顿，而同一个结论 worker 还要再
        算一遍。现在 GUI 线程只做两件廉价事（是否已收尾、是否已有在飞 worker），其余
        交给 worker：无事可做时 worker 立刻结束并在 ``report.idle`` 留痕，收尾也不做
        rescan（见 ``_on_frameseq_provision_finished``）。删除目录这类 I/O 依旧只在
        worker 线程发生，GUI 线程不碰。
        """
        try:
            if self._shutdown or self._frameseq_worker is not None:
                return
            root = self._asset_dir.parent / 'frameseq'
            worker = frameseq_provision.FrameseqProvisionWorker(
                self._asset_dir, root, parent=self)
        except Exception:
            logging.getLogger(__name__).debug('帧序列供给排期失败', exc_info=True)
            return
        worker.finished_work.connect(self._on_frameseq_provision_finished)
        worker.finished.connect(worker.deleteLater)
        self._frameseq_worker = worker
        try:
            worker.start()
        except Exception:
            self._frameseq_worker = None
            logging.getLogger(__name__).debug('帧序列供给线程启动失败', exc_info=True)

    def _on_frameseq_provision_finished(self) -> None:
        """供给收尾（GUI 线程槽）：**确有变化时**重建映射，此后新 clip 走 FrameSeqClip。

        库已收尾（``shutdown()``）时丢弃这个迟到的信号：此刻 rescan 会重算源哈希、
        重建映射、把世代目录登记进进程级「在用世代」集合——库都没了，这些登记既
        无收益又会让已退役目录再也清扫不掉（"在用"与"活 clip 在读"从此无法区分）。
        worker 的取消发生在 ``shutdown()``，而 queued ``finished_work`` 必须等 GUI
        事件循环才投递，所以"库已收尾、槽才到"是常态而非异常。

        rescan 只在 ``report.converted`` / ``report.retired`` 非零时做：它要按当前源
        给每个已有世代目录的 clip 重算哈希（满配 ≈53MB）。无事可做的轮次
        （``report.idle``）或只清退役标记的轮次，映射与重扫前逐条相同——重扫只是白烧
        一次启动后的 I/O。
        """
        if self._shutdown:
            self._frameseq_worker = None
            return
        worker, self._frameseq_worker = self._frameseq_worker, None
        report = getattr(worker, 'report', None)
        if report is not None and (report.converted
                                   or getattr(report, 'retired', 0)):
            self.rescan_frameseq()
        if report is not None:
            logging.getLogger(__name__).info(
                '帧序列首跑供给收尾：新转 %s / 跳过 %s / 失败 %s / 清扫退役 %s / '
                '预算搁置 %s / 退避搁置 %s / 磁盘搁置 %s / 迁移配额已用 %s%s，'
                '帧序列映射 %d 段',
                report.converted, report.skipped, report.failed,
                getattr(report, 'retired', 0),
                getattr(report, 'deferred_budget', 0),
                getattr(report, 'deferred_backoff', 0),
                getattr(report, 'deferred_disk', 0),
                getattr(report, 'migrations', 0),
                '（本轮无事可做）' if getattr(report, 'idle', False) else '',
                len(self._frameseq_dirs),
            )
        if report is not None and getattr(report, 'locked', False):
            self._retry_frameseq_provision_after_lock()
        else:
            self._frameseq_lock_retries = 0

    def _retry_frameseq_provision_after_lock(self) -> None:
        """锁被占：本库这一轮一个 clip 都没动，别就此永久放弃（有界重试）。

        QLockFile 是一把建在素材根上的锁：三宠三库（或另一实例）几乎同时排期时，只有
        先拿到锁的那个跑得成，其余 ``report.locked = True`` 立即返回；而本库的首跑入口
        ``maybe_provision_frameseq`` 每个进程只排一次 —— 没有这个重试，没抢到锁的那些
        角色在这次启动里就再也不会供给（审计口径："锁失败永久放弃别的角色"）。

        重试有界：``PROVISION_LOCK_RETRY_LIMIT`` 次、退让从
        ``PROVISION_LOCK_RETRY_DELAY_MS`` 起逐次翻倍封顶 —— 只重排一个 QTimer（不等待
        锁、不引常驻线程、不阻塞 GUI），每次尝试在 worker 里 tryLock 失败即返回。超出
        上限的角色留到下次启动重新排期（仍不是永久放弃）。
        """
        if self._frameseq_lock_retries >= frameseq_provision.PROVISION_LOCK_RETRY_LIMIT:
            return
        self._frameseq_lock_retries += 1
        QTimer.singleShot(
            frameseq_provision.lock_retry_delay_ms(self._frameseq_lock_retries),
            self, self._start_frameseq_provision)

    def movie(self, name: str):
        """按需创建并缓存 clip（懒加载）：启动时只创建实际用到/预热的动画。

        这样多开实例不会在启动瞬间一次性 new 出 91 个播放器对象；
        随机动作池由 _warm_low_priority_background 在启动后 2s 补全。

        新建 clip = 动画切换点：顺手回收兄弟 clip 的残留解码帧
        （release_idle_frames，内存瘦身第一刀的事件驱动触发点）。回收只动
        "不在播且 reader 已退出"的 clip，正在播的对象与本 clip 都不受影响。
        """
        if name not in self._movies:
            try:
                self.release_idle_frames()
            except Exception:
                logging.getLogger(__name__).debug('切换动画时回收残留帧失败', exc_info=True)
            frameseq_dir = self._frameseq_dirs.get(name)
            if frameseq_dir is not None:
                # 热集帧序列：无 ffmpeg 进程/spawn 冷启动/看门狗（B 档）
                from .frameseq_clip import FrameSeqClip
                self._movies[name] = FrameSeqClip(frameseq_dir, parent=self)
                return self._movies[name]
            path = self._paths[name]
            if path.suffix.lower() == '.gif':
                self._movies[name] = GifClip(path, parent=self)
            else:
                self._movies[name] = WebMClip(path, parent=self)
        return self._movies[name]

    def clip_path(self, name: str) -> Path | None:
        """只取素材路径、不创建 clip——供工作线程解码缩略图用。

        movie() 会构造带 QTimer 的 WebMClip（GUI 线程亲和对象），
        在 QThreadPool worker 里调用会违反 Qt 线程规则；缩略图只需要文件路径。
        """
        return self._paths.get(name)

    def frames(self, name: str) -> int:
        return self.movie(name).frameCount()

    def duration(self, name: str) -> float:
        return self.movie(name).duration()

    def names(self) -> list[str]:
        return list(self._paths)

    def movies(self) -> dict[str, object]:
        """当前已创建（已加载）的 clip 映射，供窗口层连接信号。"""
        return dict(self._movies)


def clip_current_image(clip):
    """取 clip 当前显示帧为 QImage（零拷贝优先，只在 GUI 线程调用）。

    WebMClip 已持有 _current_image，优先走 currentImage() 直取——省掉一次
    QPixmap.fromImage→toImage 的全画布往返（GUI 减负 Step1b）。clip 无该
    能力（GifClip）或当前无帧时，回退 currentPixmap().toImage()，与旧链
    逐位一致。返回 None 表示当前没有可显示帧（首帧未就绪/素材损坏），
    调用方按原有空判语义跳过本帧。
    """
    getter = getattr(clip, 'currentImage', None)
    if callable(getter):
        img = getter()
        if isinstance(img, QImage) and not img.isNull():
            return img
    pm = clip.currentPixmap()
    if pm is None or pm.isNull():
        return None
    return pm.toImage()
