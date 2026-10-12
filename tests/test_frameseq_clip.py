# -*- coding: utf-8 -*-
"""FrameSeqClip + MovieLibrary 帧序列接线 offscreen 单测。

覆盖（帧序列化 B 档）：
- FrameSeqClip 播放语义：start 首帧异步交付、逐帧推进、末帧 finished、
  未到货等待不跳帧、jumpToFrame、空目录 errorOccurred + start False、
  alpha 通道存活；
- MovieLibrary.movie() 的 frameseq 优先接线：有 frameseq 目录 →
  FrameSeqClip；无 → 现 webm 路径（WebMClip）。

纪律（AGENTS.md 时序测试）：播放定时器不依赖真实走时，同步直调
start/_advance/jumpToFrame；异步预取交付用 processEvents 事件泵 +
宽预算等待（不赌固定 sleep）。帧素材用 Qt 现场生成的 webp。
"""
from __future__ import annotations

import gc
import json
import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication

from pet import frameseq_provision
from pet.frameseq_clip import FrameSeqClip
from pet.library import MovieLibrary

app = QApplication.instance() or QApplication([])


def _pump_until(cond, timeout_s=3.0):
    """事件泵：等到 cond 为真（异步预取交付经 queued 信号，需要事件循环）。"""
    t0 = time.monotonic()
    while not cond():
        QApplication.processEvents()
        if time.monotonic() - t0 > timeout_s:
            raise AssertionError("事件泵超时：异步交付未到达")
        time.sleep(0.005)


def _make_frames(dir_path: Path, count: int = 4, size: tuple[int, int] = (32, 24)) -> None:
    """Qt 现场生成 count 帧带 alpha 图案的 webp + meta.json。"""
    dir_path.mkdir(parents=True, exist_ok=True)
    w, h = size
    for i in range(count):
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        # 左上 4x4 不透明块（颜色随帧号变），其余透明——验证 alpha 与帧区分
        for y in range(4):
            for x in range(4):
                img.setPixel(x, y, (0xFF << 24) | ((i * 60) << 16) | 0x3366)
        assert img.save(str(dir_path / f"f_{i + 1:04d}.webp"), "webp", 100)
    (dir_path / "meta.json").write_text(
        json.dumps({"fps": 24.0, "source": "x.webm", "frames": count,
                    # 采纳闸门要求 encoder 与该目录档位一致（这里的夹具是热集 clip）
                    "encoder": frameseq_provision.ENCODER_DESC}),
        encoding="utf-8")


def test_start_delivers_first_frame_async(tmp_path):
    d = tmp_path / "clip"
    _make_frames(d)
    clip = FrameSeqClip(d)
    hits = []
    clip.frameChanged.connect(hits.append)
    try:
        assert clip.start() is True
        _pump_until(lambda: hits == [0])       # 首帧异步交付（~2.5ms，无冷启动）
        assert clip.currentImage() is not None
    finally:
        clip.close()


def test_start_keeps_warm_frame_until_first_frame_arrives(tmp_path):
    """切动画瞬间不清显示槽：start() 前预热的首帧必须活到帧 0 到货。

    旧行为在 _pending 没有 0 号帧时把 _img/_pm 清空，帧 0 要等共享预取
    线程异步交付；这期间 sprite.paint 取不到帧直接 return → sprite 区域
    画透明 = 桌宠闪消失一瞬（回归）。旧架构语义见 window.py:1542-1557：
    stop→jumpToFrame(0) 同步拿首帧→start，显示槽从不清空。
    """
    d = tmp_path / "clip"
    _make_frames(d)
    clip = FrameSeqClip(d)
    hits = []
    clip.frameChanged.connect(hits.append)
    try:
        clip.warm_first_frame()                 # 状态机切动画前的预热（同步首帧）
        warm = clip.currentImage()
        assert warm is not None
        assert clip.start() is True
        # 异步帧 0 到货前：显示槽保持预热帧，不得清空
        assert clip.currentImage() is warm
        assert clip.currentPixmap() is not None
        _pump_until(lambda: hits == [0])        # 到货后自然被新帧覆盖
        assert clip.currentImage() is not None
    finally:
        clip.close()


def test_playthrough_ends_with_finished(tmp_path):
    d = tmp_path / "clip"
    _make_frames(d, count=3)
    clip = FrameSeqClip(d)
    frames, done = [], []
    clip.frameChanged.connect(frames.append)
    clip.finished.connect(lambda: done.append(1))
    try:
        clip.start()
        # 确定性纪律：本用例验证「显式推进序列 + 越末 finished」——计时器（41.6ms
        # 节拍）在负载下会抢跑自推进，把 finished 提前到断言点之前（全量套件里
        # 实测两连红，隔离必绿）。停表后全部推进只来自显式 _advance，时序确定。
        clip._timer.stop()
        _pump_until(lambda: frames == [0])
        clip._advance()                        # 请求帧 1，异步到货
        _pump_until(lambda: frames == [0, 1])
        clip._advance()                        # → 帧 2（末帧）
        _pump_until(lambda: frames == [0, 1, 2])
        assert done == []
        clip._advance()                        # 越末 → finished，停表
        assert done == [1]
        assert frames == [0, 1, 2]
        assert not clip._timer.isActive()
    finally:
        clip.close()


def test_advance_waits_without_frame_skip(tmp_path):
    """未到货等待语义：_advance 在帧未就绪时不跳帧、不上屏旧帧号。"""
    d = tmp_path / "clip"
    _make_frames(d, count=3)
    clip = FrameSeqClip(d)
    frames = []
    clip.frameChanged.connect(frames.append)
    try:
        clip.start()
        # 确定性纪律（同 test_playthrough_ends_with_finished）：停表后推进只来自
        # 显式 _advance 与到货回调。不停表时计时器会在「帧 1 上屏、awaiting 复位」
        # 的断言窗口内再抢跑一拍，把 awaiting 登记成 2（macOS CI 实测一红）。
        clip._timer.stop()
        _pump_until(lambda: frames == [0])
        clip._awaiting = -1
        clip._pending.clear()                  # 制造"未预取"状态
        clip._advance()                        # 未到货：只登记 awaiting，不上屏
        assert frames == [0]
        assert clip._awaiting == 1
        _pump_until(lambda: frames == [0, 1])  # 到货即上屏并续播
        assert clip._awaiting == -1
    finally:
        clip.close()


def test_jump_to_frame(tmp_path):
    d = tmp_path / "clip"
    _make_frames(d, count=4)
    clip = FrameSeqClip(d)
    try:
        clip.start()
        assert clip.jumpToFrame(3) is True
        assert clip.currentFrameNumber() == 3
        assert clip.currentImage() is not None  # 低频同步路径立即生效
        assert clip.jumpToFrame(99) is True     # 钳到末帧
        assert clip.currentFrameNumber() == 3
    finally:
        clip.close()


def test_alpha_channel_survives(tmp_path):
    d = tmp_path / "clip"
    _make_frames(d, count=2)
    clip = FrameSeqClip(d)
    hits = []
    clip.frameChanged.connect(hits.append)
    try:
        clip.start()
        _pump_until(lambda: hits == [0])
        img = clip.currentImage()
        assert img is not None
        assert ((img.pixel(1, 1) >> 24) & 0xFF) == 0xFF    # 不透明块
        assert ((img.pixel(20, 20) >> 24) & 0xFF) == 0     # 透明区
    finally:
        clip.close()


def test_empty_dir_fails_clean(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    clip = FrameSeqClip(d)
    errs = []
    clip.errorOccurred.connect(errs.append)
    try:
        assert clip.start() is False
        assert len(errs) == 1
        assert clip.frameCount() == 1           # 空目录安全回退
    finally:
        clip.close()


def test_decode_layer_compat_noops(tmp_path):
    d = tmp_path / "clip"
    _make_frames(d)
    clip = FrameSeqClip(d)
    try:
        clip.warm_meta()
        clip.warm_first_frame()
        assert clip.currentImage() is not None  # warm 装载第 0 帧
        clip.cancel_first_frame_warm()
        assert clip.decode_throttle_divisor == 1  # property（与 WebMClip 同形）
        assert clip.decode_pace_external is False
        clip.set_decode_pace_external(True)
        clip.set_decode_throttle(2)
        clip.set_recycle_minutes(5)
        clip.clear_display_frame()
        assert clip.currentImage() is None
    finally:
        clip.close()


# ---------------------------------------------------------------- MovieLibrary 接线
def _make_pack(tmp_path: Path) -> Path:
    """临时角色包：videos/idle/x.webm + frameseq/idle/x.g<戳12>/（当前源世代）。

    目录名必须按源身份命名（``frameseq_provision`` 契约）：无戳旧版 ``<stem>/``
    目录不再被采纳，库接线用例随之改成真实世代命名。
    """
    videos = tmp_path / "videos"
    (videos / "idle").mkdir(parents=True)
    webm = videos / "idle" / "x.webm"
    webm.write_bytes(b"placeholder")
    frames_dir = frameseq_provision.clip_out_dir(
        webm, videos, tmp_path / "frameseq")
    _make_frames(frames_dir)
    meta = json.loads((frames_dir / "meta.json").read_text(encoding="utf-8"))
    meta["source_sha256"] = frameseq_provision.source_sha256(webm)
    (frames_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return videos


def test_library_prefers_frameseq_clip(tmp_path):
    videos = _make_pack(tmp_path)
    lib = MovieLibrary(character_id="shenshen", asset_dir=videos,
                       prewarm_enabled=False)
    try:
        assert "x" in lib._frameseq_dirs
        assert lib._frameseq_dirs["x"] == frameseq_provision.clip_out_dir(
            videos / "idle" / "x.webm", videos, tmp_path / "frameseq")
        clip = lib.movie("x")
        assert isinstance(clip, FrameSeqClip)
        assert clip.frameCount() == 4
    finally:
        for m in lib.movies().values():
            close = getattr(m, "close", None)
            if callable(close):
                close()


def test_library_ignores_unstamped_legacy_frames_dir(tmp_path):
    """无戳旧版产物（``<stem>/``，meta 无 source_sha256）不被采纳 → 走 webm。"""
    videos = tmp_path / "videos"
    (videos / "idle").mkdir(parents=True)
    (videos / "idle" / "x.webm").write_bytes(b"placeholder")
    legacy = tmp_path / "frameseq" / "idle" / "x"
    _make_frames(legacy)
    meta = json.loads((legacy / "meta.json").read_text(encoding="utf-8"))
    meta.pop("source_sha256", None)
    (legacy / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    lib = MovieLibrary(character_id="shenshen", asset_dir=videos,
                       prewarm_enabled=False)
    try:
        assert lib._frameseq_dirs == {}
        from pet.webm_clip import WebMClip
        assert isinstance(lib.movie("x"), WebMClip)   # 回退，不冻结
        assert legacy.is_dir()                        # 旧产物不删也不补戳
    finally:
        for m in lib.movies().values():
            cleanup = getattr(m, "cleanup", None)
            if callable(cleanup):
                cleanup()


def test_library_without_frameseq_keeps_webm_path(tmp_path):
    videos = tmp_path / "videos"
    (videos / "idle").mkdir(parents=True)
    (videos / "idle" / "x.webm").write_bytes(b"placeholder")
    lib = MovieLibrary(character_id="shenshen", asset_dir=videos,
                       prewarm_enabled=False)
    assert lib._frameseq_dirs == {}
    clip = lib.movie("x")
    from pet.webm_clip import WebMClip
    assert isinstance(clip, WebMClip)       # 现路径逐行不变


def test_shared_prefetch_thread_shutdown_and_recreate(tmp_path):
    """退出收口：aboutToQuit 停共享预取线程（防解释器退出 0xC0000409），
    且收口后可懒重建（测试/会话内 QApplication 反复生灭）。"""
    from pet import frameseq_clip

    _make_frames(tmp_path, count=2)
    clip = FrameSeqClip(tmp_path)
    thread = frameseq_clip._shared_thread
    assert thread is not None and thread.isRunning()
    frameseq_clip._shutdown_shared_prefetch()
    assert frameseq_clip._shared_thread is None
    assert not thread.isRunning()
    # 重建：新 clip 照常拿到运行中的线程并能播
    clip2 = FrameSeqClip(tmp_path)
    thread2 = frameseq_clip._shared_thread
    assert thread2 is not None and thread2.isRunning()
    assert clip2.start()
    _pump_until(lambda: clip2.currentImage() is not None)
    clip2.stop()
    clip2.close()
    clip.close()


def test_decode_layer_attrs_are_properties():
    """decode_throttle_divisor/decode_pace_external 必须与 WebMClip 同形
    （property 只读）——window.py:2646/decode_fanout.py:329 按属性读，
    普通方法会在 FrameSeqClip 路径 TypeError（4.4b 评审实测）。"""
    assert isinstance(FrameSeqClip.__dict__["decode_throttle_divisor"], property)
    assert isinstance(FrameSeqClip.__dict__["decode_pace_external"], property)


# ------------------------------------------------------- O1：帧路径去浪费（2026-09-27）
class _PixmapProbe:
    """``frameseq_clip.QPixmap`` 的计数替身：记录 fromImage 的调用线程，转发真构建。

    测的是产品代码里那一次 QPixmap 构建请求（模块名绑定即 seam），不是产品对象替身。
    """

    calls: list[int] = []

    @staticmethod
    def fromImage(image):
        _PixmapProbe.calls.append(threading.get_ident())
        return QPixmap.fromImage(image)


class _DecodeProbe:
    """``frameseq_clip.QImage`` 的计数替身：记录"解一帧"（实测 ~1.2ms/帧）的次数。

    计数只认 ``scope``（本用例 tmp_path）下的帧文件：钉表后历史用例遗留的
    在播 clip 仍在后台推进解码，不收窄口径会把外来解码算进本用例（与
    test_frame_path_waste 的 H2 教训同款）。
    """

    def __init__(self):
        self.calls: list[str] = []
        self.scope: str = ""

    def __call__(self, path):
        import os as _os
        p = _os.path.normcase(str(path))
        if self.scope and not p.startswith(self.scope):
            return QImage(path)      # 范围外不计数
        self.calls.append(str(path))
        return QImage(path)


@pytest.fixture
def pixmap_probe(monkeypatch):
    from pet import frameseq_clip
    _PixmapProbe.calls = []
    monkeypatch.setattr(frameseq_clip, "QPixmap", _PixmapProbe)
    return _PixmapProbe


@pytest.fixture
def decode_probe(monkeypatch, tmp_path):
    from pet import frameseq_clip
    import os as _os
    probe = _DecodeProbe()
    probe.scope = _os.path.normcase(str(tmp_path))
    monkeypatch.setattr(frameseq_clip, "QImage", probe)
    return probe


def test_apply_builds_no_pixmap_and_current_pixmap_builds_lazily(
        tmp_path, pixmap_probe):
    """O1：上屏只存 QImage——QPixmap 由 currentPixmap() 消费者按帧惰性构建/缓存。

    overlay 渲染链（PetSprite → library.clip_current_image）只读 currentImage()，
    每帧 fromImage 是纯浪费；托盘/岛图标那类消费者仍必须拿到非空、同帧的 pixmap。
    """
    d = tmp_path / "clip"
    _make_frames(d, count=3)
    clip = FrameSeqClip(d)
    hits = []
    clip.frameChanged.connect(hits.append)
    try:
        clip.start()
        _pump_until(lambda: hits == [0])
        clip._advance()
        _pump_until(lambda: hits == [0, 1])
        assert pixmap_probe.calls == [], "上屏/交付不得构建 QPixmap"

        img = clip.currentImage()
        pm1 = clip.currentPixmap()
        assert pm1 is not None and not pm1.isNull()
        assert len(pixmap_probe.calls) == 1
        # 语义与旧实现一致：返回值仍是当前显示帧那一张（同图、同像素）
        assert (pm1.width(), pm1.height()) == (img.width(), img.height())
        assert pm1.toImage().pixel(1, 1) == img.pixel(1, 1)
        assert clip.currentPixmap() is pm1, "同帧重复请求：复用缓存"
        assert len(pixmap_probe.calls) == 1

        clip._advance()                            # 新帧到达 → 缓存失效
        _pump_until(lambda: hits == [0, 1, 2])
        pm2 = clip.currentPixmap()
        assert pm2 is not None and pm2 is not pm1
        assert len(pixmap_probe.calls) == 2

        clip.clear_display_frame()                 # 清显示槽：pixmap 一并丢
        assert clip.currentPixmap() is None
        assert len(pixmap_probe.calls) == 2, "空显示槽不得构建 pixmap"
    finally:
        clip.close()


def test_warm_first_frame_never_builds_pixmap_off_gui_thread(tmp_path, pixmap_probe):
    """O1：warm_first_frame 跑在后台预热线程（library._run_phase / 落地预热 worker）。

    改前它在**非 GUI 线程**里 QPixmap.fromImage——Qt 的 QPixmap 只能在 GUI 线程
    构造。改后预热只解 QImage，pixmap 留给 GUI 线程按需构建。

    提交（显示槽写入）经 queued 信号投递到 clip 所属线程，因此等到事件队列
    排空才算"预热产物到货"——期间后台线程同样不得构建 QPixmap。
    """
    d = tmp_path / "clip"
    _make_frames(d, count=3)
    clip = FrameSeqClip(d)
    try:
        t = threading.Thread(target=clip.warm_first_frame, daemon=True)
        t.start()
        t.join(5.0)
        assert not t.is_alive()
        assert pixmap_probe.calls == [], "预热线程构建 QPixmap = 跨线程用 QPixmap"
        _pump_until(lambda: clip.currentImage() is not None)  # 提交队列排空
        assert clip.currentImage() is not None      # 预热产物仍在（QImage 跨线程安全）
        pm = clip.currentPixmap()
        assert pm is not None and pixmap_probe.calls == [threading.get_ident()]
    finally:
        clip.close()


def test_warm_then_start_reuses_frame_zero_without_second_decode(
        tmp_path, decode_probe):
    """O1：预热已解出帧 0 → 起播直接上屏，prefetch worker 不再解第二遍。

    旧行为：warm 解帧 0 上屏 → start() 见 _pending 无 0 → 又向 worker 请求帧 0
    （一次 ~1.2ms 帧解码 + 一次线程往返延迟）；预热等于半白做。
    """
    d = tmp_path / "clip"
    _make_frames(d, count=4)
    f0 = str(sorted(d.glob("f_*.webp"))[0])
    clip = FrameSeqClip(d)
    hits = []
    clip.frameChanged.connect(hits.append)
    try:
        clip.warm_first_frame()
        assert decode_probe.calls == [f0]
        warm = clip.currentImage()
        assert warm is not None

        assert clip.start() is True
        assert hits == [0], "预热帧在 start() 即上屏（帧 0 通知同步发出）"
        assert clip.currentImage() is warm, "复用同一份预热 QImage"
        QApplication.processEvents()                # 事件泵一轮：预取线程若有交付也在此到
        assert decode_probe.calls.count(f0) == 1, "起播不得再解一遍帧 0"
        assert clip._awaiting == -1                 # 没进"等帧 0"
    finally:
        clip.close()


def test_start_reuses_displayed_frame_zero(tmp_path, decode_probe):
    """O1：显示槽已是第 0 帧（上一圈停在帧 0）→ start() 不再请求帧 0。"""
    d = tmp_path / "clip"
    _make_frames(d, count=4)
    f0 = str(sorted(d.glob("f_*.webp"))[0])
    clip = FrameSeqClip(d)
    hits = []
    clip.frameChanged.connect(hits.append)
    try:
        clip.start()
        _pump_until(lambda: hits == [0])
        assert clip.currentFrameNumber() == 0
        assert decode_probe.calls.count(f0) == 1
        clip.stop()
        assert clip.start() is True
        assert hits == [0, 0]
        assert clip._awaiting == -1                 # 未请求帧 0（无需等）
        _pump_until(lambda: 1 in clip._pending)     # 预取链路仍按旧深度续（帧 1 在途）
        assert decode_probe.calls.count(f0) == 1, "帧 0 不得二次解码"
    finally:
        clip.close()


def test_clear_display_frame_releases_pinned_first_frame(tmp_path, decode_probe):
    """pinned clip 的显示槽同样被 release_idle 回收（真机 A/B 撤回 O1 保留）。

    保留每 pinned clip 1 帧在三宠下实测多占 ~17.5MB 私有内存，只省一次 ~1.2ms
    帧 0 解码。回收后再起播：帧 0 恰好解一次、正常上屏。
    """
    d = tmp_path / "clip"
    _make_frames(d, count=4)
    clip = FrameSeqClip(d)
    hits = []
    clip.frameChanged.connect(hits.append)
    try:
        clip._ffr_pinned = True
        clip.warm_first_frame()
        assert clip.currentImage() is not None

        clip.clear_display_frame()
        assert clip.currentImage() is None, "pinned 也不常驻显示帧"
        assert clip._pm is None

        f0 = str(d / "f_0001.webp")
        before = decode_probe.calls.count(f0)
        assert clip.start() is True
        _pump_until(lambda: hits == [0])
        assert clip.currentImage() is not None
        assert decode_probe.calls.count(f0) == before + 1, "帧 0 恰好重解一次"
    finally:
        clip.close()


def test_stop_and_clear_drop_inflight_pending(tmp_path):
    """O1：stop()/clear_display_frame() 丢掉在途到货帧（非 pinned clip）。

    在途结果只对"这一圈播放"有意义；停播后留着就是每 clip 1 帧的常驻浪费。
    pinned clip 例外（上一用例的预热帧 0 就寄存在 _pending 里）。
    """
    d = tmp_path / "clip"
    _make_frames(d, count=4)
    clip = FrameSeqClip(d)
    pinned = FrameSeqClip(d)
    try:
        in_flight = QImage(str(d / "f_0004.webp"))
        clip._pending[3] = in_flight
        clip.stop()
        assert clip._pending == {}

        pinned._ffr_pinned = True
        pinned._pending[3] = in_flight
        pinned.stop()
        assert 3 in pinned._pending
    finally:
        clip.close()
        pinned.close()


def test_retained_frame_is_not_reused_as_frame_zero(tmp_path, decode_probe):
    """O1 反向守门：显示槽里留的不是帧 0 时，start() 不得把它当成帧 0 复用。

    停播后显示槽留的是"当前那张"（可能是上一圈的末帧），_cur 也不一定是 0。
    复用判据必须是**这张图自己的帧号**，否则 start() 会把末帧当帧 0 上屏
    （画面与播放位置不一致）。
    """
    d = tmp_path / "clip"
    _make_frames(d, count=4)
    clip = FrameSeqClip(d)
    hits = []
    clip.frameChanged.connect(hits.append)
    try:
        clip._ffr_pinned = True
        clip.start()
        _pump_until(lambda: hits == [0])
        clip.jumpToFrame(3)                         # 显示槽 = 帧 3（_cur = 3）
        last = clip.currentImage()
        clip.stop()                                 # 停在帧 3（显示槽保留当前图）
        assert clip.currentImage() is last
        assert clip._img_frame == 3

        decodes = len(decode_probe.calls)
        hits.clear()                                # 重启后的交付序列从零记起
        assert clip.start() is True
        assert clip.currentImage() is last, "帧 3 留作兜底，直到真帧 0 到货"
        _pump_until(lambda: clip.currentImage() is not last)
        # 时序口径：断言「重启后第一拍交付的是帧 0」而不是「此刻停在帧 0」——
        # 慢 runner 上帧表可能在断言前又推进了一拍（macOS CI 实测 assert 1==0
        # 抖动红），交付序列的首元素才是本用例要守的语义。
        assert hits and hits[0] == 0, "重启后首拍交付必须是帧 0"
        # 帧 0 解码计数同理只数「快照之后的帧 0」：第一圈链式预取的帧 1 可能
        # 在慢 runner 上于快照之后才落账（解码在 worker 线程、计数以调用线程
        # 为准），把总帧数差钉成 1 会把那次迟到误算进来（macOS CI 实测抖动）。
        frame0_after = [c for c in decode_probe.calls[decodes:]
                        if c.endswith("f_0001.webp")]
        assert len(frame0_after) == 1, "重启后帧 0 恰好解一次"
    finally:
        clip.close()


def test_single_frame_clip_starts_and_finishes(tmp_path, decode_probe):
    """边界：单帧素材——起播复用、推进即 finished，不越界请求。"""
    d = tmp_path / "clip"
    _make_frames(d, count=1)
    clip = FrameSeqClip(d)
    done = []
    clip.finished.connect(lambda: done.append(1))
    try:
        clip.warm_first_frame()
        assert clip.start() is True
        assert clip.currentImage() is not None
        assert clip.currentPixmap() is not None
        clip._advance()                             # 越末帧 → finished
        assert done == [1]
        assert len(decode_probe.calls) == 1         # 只有预热那一次解码
    finally:
        clip.close()


# ------------------------------------------------- 帧表懒列（R1：构造零 glob）
@pytest.fixture
def glob_probe(monkeypatch):
    """``pathlib.Path.glob`` 计数替身：转发真列目录，记录每次 (目录, 模式)。

    测的是"构造/元信息是否去列目录"这个产品行为（模块层 seam），产品对象全真。
    """
    import pathlib
    calls: list[tuple[str, str]] = []
    real_glob = pathlib.Path.glob

    def counting_glob(self, pattern, *args, **kwargs):
        calls.append((str(self), pattern))
        return real_glob(self, pattern, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "glob", counting_glob)
    return calls


def _frame_globs(calls):
    return [c for c in calls if c[1] == "f_*.webp"]


def test_construct_lists_no_frames_and_meta_covers_metadata(tmp_path, glob_probe):
    """构造 N 个 clip 零 glob；frameCount/duration 走 meta，也不 glob。

    改前：每个 clip 构造即 ``sorted(dir.glob("f_*.webp"))``——每帧一个 Path 对象
    （实测每 clip ~239 个），低优先级预热为每宠整库随机 clip 建对象，多出
    90-110MB 堆。
    """
    d = tmp_path / "clip"
    _make_frames(d, count=5)
    clips = [FrameSeqClip(d) for _ in range(5)]
    try:
        assert _frame_globs(glob_probe) == [], "构造阶段不得列帧目录"
        for clip in clips:
            assert clip.frameCount() == 5
            assert clip.duration() == pytest.approx(5 / 24.0)
        assert _frame_globs(glob_probe) == [], "frameCount/duration 不得列帧目录"
    finally:
        for clip in clips:
            clip.close()


def test_start_never_lists_frames_when_meta_has_the_count(tmp_path, glob_probe):
    """M1：meta 带帧数时，整条播放路径（起播/跳帧/预热/推进）零 glob。

    改前（R1 懒列版）：起播在锁内 glob 一次并常驻 Path 列表。改后帧表按编号
    现推，glob 只留给 meta 缺失/非法的兜底（见
    ``test_frame_count_falls_back_to_listing_without_meta_frames``）。
    """
    d = tmp_path / "clip"
    _make_frames(d, count=3)
    clip = FrameSeqClip(d)
    hits = []
    clip.frameChanged.connect(hits.append)
    try:
        assert _frame_globs(glob_probe) == [], "起播前就列帧表 = 白建 Path 对象"
        assert clip.frameCount() == 3               # meta 已带帧数
        assert clip.start() is True
        _pump_until(lambda: hits == [0])            # 帧 0 异步交付（真预取线程）
        assert clip.currentImage() is not None
        assert _frame_globs(glob_probe) == [], "起播不得列帧目录（meta 是权威）"

        assert clip.jumpToFrame(2) is True
        assert clip.warm_first_frame() is None
        clip._advance()                             # 末帧后 finished
        assert _frame_globs(glob_probe) == [], "播放路径不得列帧目录"
        assert clip.frameCount() == 3
    finally:
        clip.close()


def test_frame_count_falls_back_to_listing_without_meta_frames(
        tmp_path, glob_probe):
    """meta 缺 ``frames``（或非法）→ 帧数查询兜底列目录。"""
    d = tmp_path / "clip"
    _make_frames(d, count=4)
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    del meta["frames"]
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    clip = FrameSeqClip(d)
    try:
        assert clip.frameCount() == 4
        assert len(_frame_globs(glob_probe)) == 1
        assert clip.duration() == pytest.approx(4 / 24.0)   # 已列 → 零额外 glob
        assert len(_frame_globs(glob_probe)) == 1
    finally:
        clip.close()


def test_invalid_meta_frames_falls_back_and_empty_dir_still_fails(
        tmp_path, glob_probe):
    """非法 ``frames``（0/字符串）按无效处理；空目录仍 start False + 一次告警。"""
    d = tmp_path / "clip"
    _make_frames(d, count=2)
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    meta["frames"] = 0
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    clip = FrameSeqClip(d)
    try:
        assert clip.frameCount() == 2                # 0 无效 → 兜底列目录
        assert len(_frame_globs(glob_probe)) == 1
    finally:
        clip.close()

    # 字符串帧数同样是无效值（口径对齐 frameseq_provision.is_complete）
    meta["frames"] = "2"
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    clip3 = FrameSeqClip(d)
    globs_before = len(_frame_globs(glob_probe))
    try:
        assert clip3.frameCount() == 2
        assert len(_frame_globs(glob_probe)) == globs_before + 1
    finally:
        clip3.close()

    empty = tmp_path / "empty"
    empty.mkdir()
    clip2 = FrameSeqClip(empty)
    errs = []
    clip2.errorOccurred.connect(errs.append)
    try:
        assert clip2.start() is False
        assert len(errs) == 1
        assert clip2.frameCount() == 1
    finally:
        clip2.close()


# --------------------------------------------- M1：帧表去物化（2026-09-28）
def _live_path_count() -> int:
    """进程内活着的 ``pathlib.Path`` 对象数（"帧表是否常驻 Path"的直接证据）。"""
    gc.collect()
    return sum(1 for obj in gc.get_objects() if isinstance(obj, Path))


def test_play_path_never_lists_frames_when_meta_is_authoritative(
        tmp_path, glob_probe):
    """M1：meta 权威 → 路径按编号现推，播放路径零 glob、零物化 Path 列表。

    改前：首次预热/起播 glob 一次并把 ~240 个 Path 常驻 ``_frames`` 且永不释放
    （实测 106 段 × 3 宠 ≈110MB RSS，``library.release_idle_frames`` 也不回收
    它）。素材命名是转换器写出的连续 ``f_%04d.webp``、帧数以 meta 的 ``frames``
    为权威，因此路径可以现推：``_frames[i]`` = ``dir/f_{i+1:04d}.webp``。
    """
    d = tmp_path / "clip"
    _make_frames(d, count=240)
    before = _live_path_count()
    clip = FrameSeqClip(d)
    try:
        assert clip.frameCount() == 240                  # 元信息走 meta，零 glob
        clip.warm_first_frame()                          # 生产预热入口
        assert clip.start() is True
        _pump_until(lambda: clip.currentImage() is not None)
        assert clip.jumpToFrame(239) is True
        clip._advance()                                  # 末帧后 finished

        assert _frame_globs(glob_probe) == [], "meta 权威：播放路径不得列帧目录"
        assert not isinstance(clip._frames, list), "帧表不得物化 Path 列表"
        assert len(clip._frames) == 240                  # 逻辑帧数仍以 meta 为准
        assert clip._frames[0] == d / "f_0001.webp"      # 路径按编号现推
        assert clip._frames[239] == d / "f_0240.webp"
        assert _live_path_count() - before < 20, "帧表不得常驻 Path 对象"
    finally:
        clip.close()


# ---------------------------------------- 收口：worker 回收（O2，2026-10-03）
def test_library_shutdown_closes_frameseq_clip_and_retires_worker(tmp_path):
    """库收口必须走 clip.close()：FrameSeqClip 的 worker 受控退役（不跨线程销毁）。

    改前 library.shutdown 的收口循环只试 cleanup（仅 WebMClip 有）否则 stop()，
    FrameSeqClip 永远走 stop 分支、close() 零调用 → worker 失去受控退役路径，
    只能靠 Python GC 在某个随机线程析构一个亲和于共享预取线程的 QObject
    （该文件 :84-92 的同类 AV 前科）。2026-10-03 起 close() 的退役口径是
    「断信号 + 留引用」而非 deleteLater：挂在死线程队列里的 DeferredDelete
    在共享线程被杀/重建时是实测的 access violation 源（邻域连跑 3/17 vs 0/6）。
    """
    import shiboken6

    videos = _make_pack(tmp_path)
    lib = MovieLibrary(character_id="shenshen", asset_dir=videos,
                       prewarm_enabled=False)
    try:
        clip = lib.movie("x")
        assert isinstance(clip, FrameSeqClip)
        worker = clip._worker
        assert shiboken6.isValid(worker) and clip._prefetch_thread.isRunning()

        lib.shutdown()

        assert clip._closed is True, "库收口必须调 close()"
        assert clip._worker is worker and clip._retired_workers[-1] is worker,             "worker 必须退役留引用（不跨线程销毁）"
        assert shiboken6.isValid(worker), "退役 ≠ 销毁：不许 deleteLater 上共享线程"
        assert clip._timer.isActive() is False
    finally:
        lib.shutdown()


def test_frameseq_close_is_idempotent(tmp_path):
    """close() 幂等：重复调用不重复退役（_retired_workers 只入列一次）。"""
    import shiboken6

    d = tmp_path / "clip"
    _make_frames(d, count=3)
    clip = FrameSeqClip(d)
    worker = clip._worker
    clip.close()
    assert clip._closed is True
    assert clip._retired_workers == [worker]
    assert shiboken6.isValid(worker), "退役保留引用，worker 不得被销毁"

    clip.close()  # 二次调用：幂等 no-op
    clip.close()
    assert clip._retired_workers == [worker]


# ------------------------------ 后台预热提交（O3：显示槽只由 GUI 线程写）
class _GatedDecodeProbe:
    """``frameseq_clip.QImage`` 计数替身：非 GUI 线程的加载在闸门上阻塞。

    复现"后台已检查过显示槽为空、正在解码"的那段窗口：闸门放行前前台可以
    任意提交新帧。测的是产品代码那一次解码请求（模块名绑定即 seam）。
    """

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.gui_thread = threading.get_ident()
        self.calls: list[tuple[int, str]] = []

    def __call__(self, path):
        self.calls.append((threading.get_ident(), str(path)))
        if threading.get_ident() != self.gui_thread:
            self.entered.set()
            self.release.wait(10.0)
        return QImage(path)


def test_background_warm_never_overwrites_newer_displayed_frame(
        tmp_path, monkeypatch):
    """后台预热不得倒写显示槽：提交走 GUI 线程、槽内复核空槽。

    改前 warm_first_frame 在后台线程里"检查 _img is None → 解 QImage（IO）→
    _apply(img, 0)"三步无原子性：期间 GUI 起播/跳帧提交的新帧会被倒写成帧 0
    （图与帧号不一致），release_idle_frames 刚清空的槽也会被重新填回。
    """
    from pet import frameseq_clip

    d = tmp_path / "clip"
    _make_frames(d, count=4)
    probe = _GatedDecodeProbe()
    monkeypatch.setattr(frameseq_clip, "QImage", probe)
    clip = FrameSeqClip(d)
    try:
        worker = threading.Thread(target=clip.warm_first_frame, daemon=True)
        worker.start()
        assert probe.entered.wait(5.0), "后台预热未进入解码"
        assert clip.currentImage() is None, "后台此刻还没提交（显示槽为空）"

        # 前台（GUI 线程）在后台解码期间提交帧 3
        assert clip.jumpToFrame(3) is True
        newer = clip.currentImage()
        assert clip._img_frame == 3

        probe.release.set()
        worker.join(5.0)
        assert not worker.is_alive()
        # 后台的提交是 queued 到 GUI 线程的：排空事件队列后必须被复核拦下
        for _ in range(3):
            QApplication.processEvents()

        assert clip._img_frame == 3, "后台预热把显示槽倒写成帧 0 了"
        assert clip.currentImage() is newer, "显示槽必须是前台提交的那一帧"
        assert 0 not in clip._pending, "被拦下的预热帧不得寄存回 _pending"
    finally:
        clip.close()


def test_background_warm_commits_pending_frame_zero_for_start(
        tmp_path, decode_probe):
    """正常路子（生产预热线程）：提交经 GUI 线程照旧落地，起播零解码。

    与 test_warm_then_start_reuses_frame_zero_without_second_decode 同一口径，
    只是预热跑在后台线程（生产路径）——预热结果寄存 _pending[0]、显示槽是帧 0，
    start() 直接弹出来上屏、不再让 worker 解第二遍。
    """
    d = tmp_path / "clip"
    _make_frames(d, count=4)
    f0 = str(d / "f_0001.webp")
    clip = FrameSeqClip(d)
    hits = []
    clip.frameChanged.connect(hits.append)
    try:
        worker = threading.Thread(target=clip.warm_first_frame, daemon=True)
        worker.start()
        worker.join(5.0)
        assert not worker.is_alive()

        _pump_until(lambda: clip._img_frame == 0)     # 提交在 GUI 线程落地
        warm = clip.currentImage()
        assert warm is not None and clip._pending[0] is warm
        assert decode_probe.calls.count(f0) == 1

        assert clip.start() is True
        assert hits == [0], "预热帧在 start() 即上屏（帧 0 通知同步发出）"
        assert clip.currentImage() is warm
        assert clip._awaiting == -1                   # 没进"等帧 0"
        QApplication.processEvents()
        assert decode_probe.calls.count(f0) == 1, "起播不得再解一遍帧 0"
    finally:
        clip.close()


def test_background_warm_on_half_destroyed_clip_degrades_quietly(tmp_path):
    """半销毁（C++ 侧已删）clip 的后台预热：安静降级，绝不把异常抛给预热线程。

    新提交路径要读线程归属、要投递信号，两者在已删对象上都会 RuntimeError；
    预热是尽力而为的后台动作（library._run_phase 逐 clip 吞异常），这里按既有
    口径安静返回，等下一次起播同步解码。
    """
    import shiboken6

    d = tmp_path / "clip"
    _make_frames(d, count=2)
    clip = FrameSeqClip(d)
    shiboken6.delete(clip)                        # 同步销毁 C++ 侧，包装器留下
    assert not shiboken6.isValid(clip)
    errors: list = []

    def _run() -> None:
        try:
            clip.warm_first_frame()
        except BaseException as exc:              # noqa: BLE001 - 断言"什么都没抛"
            errors.append(exc)

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(5.0)
    assert not worker.is_alive()
    assert errors == [], f"半销毁 clip 的预热不得抛异常：{errors}"


def test_dropped_without_close_stays_pinned_for_inflight_delivery(tmp_path):
    """丢弃未 close 的 clip：clip 与 worker 必须被进程级强钉（在途交付安全落地）。

    崩溃家族根因（mac CI 原生栈实锤）：clip 被 GC 析构时，共享预取线程的在途
    queued 交付（``_on_loaded`` / ``prefetch``）命中死对象 = UAF 段错误
    （QThread::exec → sendPostedEvents → qtPythonMetacall，KERN_INVALID_ADDRESS
    at 0x8）。钉表让"丢弃未收口"路径的对象存活到进程退出，交付安全落地。
    """
    import pet.frameseq_clip as fs

    d = tmp_path / "clip"
    _make_frames(d, count=4)
    before_c, before_w = len(fs._LIVE_CLIPS), len(fs._LIVE_WORKERS)
    clip = FrameSeqClip(d)
    clip.start()
    clip._request(1)                      # 制造在途交付
    worker = clip._worker
    del clip                              # 丢弃，不 close
    gc.collect()
    assert len(fs._LIVE_CLIPS) == before_c + 1, "clip 必须入钉表"
    assert len(fs._LIVE_WORKERS) == before_w + 1, "worker 必须入钉表"
    assert worker in fs._LIVE_WORKERS
    # 泵事件让在途交付落地：钉住的对象接管 = 不崩（修复前这里是 UAF 窗口）
    for _ in range(40):
        QApplication.processEvents()
        time.sleep(0.005)
