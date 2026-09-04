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

from concurrent.futures import ThreadPoolExecutor
import random
import threading
import time
from pathlib import Path
from typing import Callable, Mapping

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QMovie

from . import catalog
from . import perfstats
from .webm_clip import WebMClip


# QMovie 播放速度补偿（%）：GIF 路线使用，校准 QMovie 偏慢问题
PLAYBACK_SPEED = 120


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
        manifest: Mapping[str, str] | None = None,
        prewarm_policy: str = "balanced",
    ) -> None:
        super().__init__(parent)
        self.character_id = character_id or catalog.DEFAULT_CHARACTER
        policy = str(prewarm_policy or "balanced").strip().lower()
        self._prewarm_policy = policy if policy in {"full", "balanced", "minimal"} else "balanced"
        if asset_dir is not None:
            self._asset_dir = Path(asset_dir)
        else:
            self._asset_dir = catalog.resolve_character_video_dir(self.character_id)
        self._manifest = None if manifest is None else dict(manifest)
        self.manifest = catalog.load_character_manifest(self.character_id, self._asset_dir)
        self.folder_map: dict[str, str] = {}
        self.folder_files: dict[str, list[str]] = {}
        self._movies: dict[str, object] = {}
        self._paths: dict[str, Path] = {}
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
        self._interaction_active = threading.Event()  # 观测镜像：set=交互中
        # 预热代次：pause_warm（隐藏/切角色）时自增；在飞的旧代次预热线程
        # 据此放弃，保证旧角色（旧库）的预热不会在交互结束后"复活"。
        # begin_interaction 返回当前代次作为 token，end_interaction(token)
        # 只释放同代次持有：pause_warm 换代的迟到 release 成为 no-op，
        # 不会误释放换代后新交互的持有（可重入配对不被 pause 破坏）。
        self._warm_generation = 0
        # 低优先级批次去重：同一时间最多一个在飞批次（timer 到点/50ms 重试/
        # resume 重排可能并发触发），worker 收尾时在 finally 清除。
        self._low_warm_in_flight = False
        self._warm_state_lock = threading.Lock()  # 保护在飞标志与完成标志
        # 交互中让路重排期：50ms 短间隔重试（交互一结束立即补上，不把 2s
        # 延迟原样再等一遍）；pause_warm 会停掉它，避免遗留 singleShot 在
        # pause 后仍触发起批。
        self._low_warm_retry_timer = QTimer(self)
        self._low_warm_retry_timer.setSingleShot(True)
        self._low_warm_retry_timer.setInterval(50)
        self._low_warm_retry_timer.timeout.connect(self._warm_low_priority_background)
        self.low_warm_batch_finished.connect(self._on_low_warm_batch_finished)
        self.media_type: str = 'webm'
        self.no_mirror: set[str] = self._load_no_mirror()

        self._load_all()

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

        # 高优先级 clip 必须在主线程创建（QObject 线程亲和），再交给后台线程预热；
        # 低优先级由 QTimer 在主线程触发 _warm_low_priority_background 创建。
        high, _ = self._priority_names()
        for name in high:
            clip = self.movie(name)
            # 高频交互链首帧常驻：低优先级随机动作池（数量超首帧预算）
            # 的预热浪涌不得把它们逐出——否则用户点击/拖拽时被迫 GUI
            # 同步解码首帧，产生可感知的百毫秒级切换卡顿（实测定案）。
            clip._ffr_pinned = True

        # 预热线程由应用层在 UI 就绪后统一调度（schedule_high_priority_warm /
        # schedule_low_priority_warm），避免库构造时在测试/非事件循环环境里
        # 凭空拉起 ffmpeg 预热线程。

    def _priority_names(self) -> tuple[list[str], list[str]]:
        """默认优先级：高频交互动画立刻预热，随机动作池延迟预热。

        高优先级来自状态机必然/高频路径：
          idle（启动即播）、turn（10% + 间隔期）、click（点击）、
          drag（拖拽）、move（20% + 手动触发）。
        低优先级 = 随机动作池（42 个，单个命中率低）。
        """
        names = list(self._manifest)
        cats = catalog.build_categories(
            names,
            None,
            self.folder_map,
            self.folder_files,
        )
        # 点击回应优先级最高：首次点击最怕同步 ffmpeg 解码（实测可达 600ms+），
        # 先预热点击动画，避免用户刚启动就点击时卡顿。
        high = list(dict.fromkeys(
            [*(cats['clicks'] or []), *(cats['idles'] or []),
             *(cats['turns'] or []), *(cats['moves'] or [])]
            + ([cats['drag']] if cats.get('drag') else [])
        ))
        low = [n for n in cats.get('acts', []) if n not in high]
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
        with ThreadPoolExecutor(max_workers=workers) as ex:

            def _warm_meta(clip: object) -> None:
                if yield_to_interaction and not self._await_interaction_clear(generation):
                    return
                if cancelled is not None and cancelled():
                    return
                clip.warm_meta()

            list(ex.map(_warm_meta, clips))
            if self._warm_paused or not include_frames:
                return  # 窗口已隐藏 / 该批不需要首帧：首帧留到恢复后或首次播放按需进行

            def _warm_first(clip: object) -> None:
                if yield_to_interaction and not self._await_interaction_clear(generation):
                    return
                if cancelled is not None and cancelled():
                    return
                getattr(clip, 'warm_first_frame', lambda: None)()

            # 预解码各动画首帧（QImage 线程安全），首次播放时零阻塞切换，
            # 避免点击 Q 弹瞬间同步 ffmpeg 解码造成卡顿与旧动画帧残留。
            list(ex.map(_warm_first, clips))

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

    def pause_warm(self) -> None:
        """窗口隐藏时暂停预热：停掉延迟定时器与让路重试，在飞线程尽快收尾。"""
        self._warm_paused = True
        self._low_warm_timer.stop()
        self._low_warm_retry_timer.stop()
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

    def resume_warm(self) -> None:
        """窗口恢复显示时补齐预热：低优先级池未建完或首帧未预热完则重新排期。"""
        self._warm_paused = False
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
                cancelled=lambda: self._warm_paused or generation != self._warm_generation,
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
        """
        try:
            if self._warm_paused:
                return  # 已暂停（隐藏/切角色）：不创建 clip、不启动线程
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
            if self._warm_paused:
                return
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

        加入 0~0.5s 随机错峰，多开同时启动时避免 ffmpeg 进程洪峰。
        """
        if not self._paths:
            return
        threading.Thread(target=self._warm_all_meta_background, daemon=True).start()

    def schedule_low_priority_warm(self) -> None:
        """应用层调用：UI 就绪后延迟补全随机动作池预热（2s 后 1 worker）。"""
        self._low_warm_timer.start()

    def warm_predicted(self, name: str) -> None:
        """批10-A1：后台预解码预测动画的首帧（Phase 1，尽力而为）。

        GLM A-1 / A3：预测预热只复用「交互让路 / 隐藏暂停 / warm_first_frame
        幂等」三重闸门，webm_clip.py 零改动；不预起 reader（Phase 2 挂起）。

        - 交互让路（_await_interaction_clear）：拖拽/点击动画/右键菜单期间等待；
        - 隐藏暂停（_warm_paused / 代次）：pause_warm 换代后作废，不复活；
        - warm_first_frame 幂等：已有缓存直接返回，不重复拉起 ffmpeg。

        预测预热只是消除首播卡顿的缓存；作废/未命中时最坏退化为今天的行为
        （后台短命 ffmpeg 产物进 LRU，被逐出即自然回收）。

        必须在 GUI 线程调用（self.movie(name) 按 QObject thread affinity 在
        主线程创建 clip）；真正耗时的 ffmpeg 解码放到独立 daemon 线程。
        """
        if self._warm_paused:
            return
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

    def movie(self, name: str):
        """按需创建并缓存 clip（懒加载）：启动时只创建实际用到/预热的动画。

        这样多开实例不会在启动瞬间一次性 new 出 91 个播放器对象；
        随机动作池由 _warm_low_priority_background 在启动后 2s 补全。
        """
        if name not in self._movies:
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
