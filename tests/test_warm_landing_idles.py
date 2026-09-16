# -*- coding: utf-8 -*-
"""_warm_landing_idles 回归测试：起飞预热绝不在 GUI 线程同步解码。

实机教训（肥鱼互撞超级卡）：该方法曾在 GUI 线程同步执行
clip.warm_first_frame() —— 碰撞风暴下每次撞飞进 throw 都同步拉起
ffmpeg（~100ms/只），多鱼互撞时 GUI 看门狗连续抓 200ms+ 卡顿。
修复后预热在 daemon 线程执行；这些测试锁定该语义（改回同步即判红）。
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

from pet.window import PetWindow


class _FakeClip:
    def __init__(self, name, sink, block_event=None, raises=False):
        self.name = name
        self._sink = sink
        self._block_event = block_event
        self._raises = raises

    def warm_first_frame(self):
        if self._block_event is not None:
            self._block_event.wait(timeout=2.0)
        self._sink.append((self.name, threading.get_ident()))
        if self._raises:
            raise RuntimeError("boom")


def _stub_win(clips):
    return SimpleNamespace(
        lib=SimpleNamespace(movie=lambda name: clips[name]),
        idles=list(clips.keys()),
    )


def test_warm_landing_idles_runs_off_calling_thread():
    sink: list = []
    clips = {n: _FakeClip(n, sink) for n in ("idle_a", "idle_b")}
    main_ident = threading.get_ident()

    PetWindow._warm_landing_idles(_stub_win(clips))

    import time
    deadline = time.monotonic() + 2.0
    while len(sink) < 2 and time.monotonic() < deadline:
        time.sleep(0.005)

    assert len(sink) == 2, "所有 idle 首帧都应被预热"
    assert all(ident != main_ident for _name, ident in sink), \
        "warm_first_frame 绝不允许在 GUI（调用）线程同步执行"


def test_warm_landing_idles_returns_immediately_when_decode_blocks():
    block = threading.Event()  # 永不 set：模拟 ffmpeg 解码卡住
    sink: list = []
    clips = {n: _FakeClip(n, sink, block_event=block) for n in ("idle_a",)}

    import time
    t0 = time.monotonic()
    PetWindow._warm_landing_idles(_stub_win(clips))
    elapsed = time.monotonic() - t0

    assert elapsed < 0.2, f"起飞预热阻塞了调用线程 {elapsed * 1000:.0f}ms"
    assert sink == [], "阻塞中的预热不应已完成（在后台线程挂着）"


def test_warm_landing_idles_tolerates_clip_errors():
    sink: list = []
    clips = {
        "bad": _FakeClip("bad", sink, raises=True),
        "good": _FakeClip("good", sink),
    }
    # 不应抛出席卷 GUI 线程；good 仍应被预热
    PetWindow._warm_landing_idles(_stub_win(clips))

    import time
    deadline = time.monotonic() + 2.0
    while not any(n == "good" for n, _ in sink) and time.monotonic() < deadline:
        time.sleep(0.005)
    assert any(n == "good" for n, _ in sink)


def test_warm_landing_idles_no_idles_noop():
    win = SimpleNamespace(lib=SimpleNamespace(
        movie=lambda name: (_ for _ in ()).throw(AssertionError("不应解析 clip"))),
        idles=[])
    PetWindow._warm_landing_idles(win)  # 不抛异常即通过


# ------------------------------------------------------------ 飞行期 pin 保护
# 实测定案：多鱼同进程 8MB 预算下，起飞暖好的落地首帧会在飞行窗口内被预热
# 浪涌逐出（风暴期仍有 105~399ms 落地冷解码卡顿）。pin 只活起飞→落地窗口。
# 两类 pin 必须分标志：library 常驻（clicks/turns/drag 的 _ffr_pinned）永不
# 来摘，而 idle 池可与它在同一 clip 上重叠（catalog 允许同一文件多分类）。

def _clip_with_pin(name, sink=None):
    clip = _FakeClip(name, sink if sink is not None else [])
    clip._ffr_pinned = False
    clip._ffr_landing_pinned = False
    return clip


def test_takeoff_pins_landing_idles_and_landing_unpins():
    clips = {n: _clip_with_pin(n) for n in ("idle_a", "idle_b")}
    win = _stub_win(clips)

    PetWindow._warm_landing_idles(win)
    assert all(c._ffr_landing_pinned for c in clips.values()), \
        "起飞必须把落地 idle 首帧打进飞行期 pin（防飞行途中被逐出）"
    assert not any(c._ffr_pinned for c in clips.values()), \
        "飞行期 pin 不得复用 library 常驻标志 _ffr_pinned"

    PetWindow._unpin_landing_idles(win)
    assert not any(c._ffr_landing_pinned for c in clips.values()), \
        "落地/打断后必须摘掉飞行期 pin（常驻内存零增长）"


def test_landing_unpin_keeps_library_resident_pin():
    """落地摘 pin 只摘飞行期那一类：与 idle 池重叠的 library 常驻保护不动。

    突变判别：把 _unpin_landing_idles 改回无脑置 _ffr_pinned = False，
    resident 会丢失常驻保护 → 断言红（正是本轮修复前的缺陷）。
    """
    resident = _clip_with_pin("both")
    resident._ffr_pinned = True  # library 高频交互核常驻（clicks/turns/drag）
    win = _stub_win({"both": resident})

    PetWindow._warm_landing_idles(win)
    PetWindow._unpin_landing_idles(win)

    assert resident._ffr_pinned is True, \
        "library 常驻保护绝不能被落地摘 pin 顺手摘掉（点击/拖拽首帧会重新冷解码）"
    assert resident._ffr_landing_pinned is False, "飞行期 pin 仍须摘除"


def test_unpin_landing_idles_tolerates_broken_lib():
    # 库已销毁/解析失败：摘 pin 绝不抛出席卷调用方
    win = SimpleNamespace(
        lib=SimpleNamespace(movie=lambda name: (_ for _ in ()).throw(RuntimeError())),
        idles=["x"])
    PetWindow._unpin_landing_idles(win)
    win2 = SimpleNamespace(lib=None, idles=["x"])
    PetWindow._unpin_landing_idles(win2)


def test_ffr_evict_respects_late_pin():
    """_ffr_evict 复查 pin：选型后才打上的 clip 不得清空（两类标志都查）。

    窗口期：_ffr_touch 选型（跳过 pinned）与 _ffr_evict 清空分两处，
    常驻/飞行期 pin 可能落在两者之间——不复查的话"绝不逐出"语义形同虚设。
    """
    import threading
    from pet import webm_clip

    class _Victim:
        def __init__(self, **flags):
            self._first_frame_lock = threading.Lock()
            self._ffr_evict_token = 7
            self._first_image = object()
            for key, value in flags.items():
                setattr(self, key, value)

    for flag in ("_ffr_pinned", "_ffr_landing_pinned"):
        pinned_victim = _Victim(**{flag: True})
        plain_victim = _Victim()
        webm_clip._ffr_evict([(pinned_victim, 7), (plain_victim, 7)])
        assert pinned_victim._first_image is not None, \
            f"被 {flag} 保护的首帧绝不可逐出"
        assert plain_victim._first_image is None, "未 pin 的照常逐出（防回归放水）"


def test_landing_pin_skips_lru_selection(monkeypatch):
    """飞行期 pin 与常驻 pin 同权参与逐出选型：LRU 从头跳过它，逐出下一个。

    突变判别：_ffr_touch 的选型失去落地标志 → 被 pin 的 a 被选中/摘表，
    victims 变成 [a] 且账面错位 → 断言红。
    """
    from pet import webm_clip

    class _Img:
        def width(self):
            return 5

        def height(self):
            return 5

    class _Clip:
        def __init__(self):
            self._first_frame_lock = threading.Lock()
            self._first_image = _Img()
            self._ffr_evict_token = None

    monkeypatch.setattr(webm_clip, "_first_frame_reg", [])
    monkeypatch.setattr(webm_clip, "_first_frame_bytes", 0)
    monkeypatch.setattr(webm_clip, "_first_frame_budget_bytes", 250)

    a, b, c = _Clip(), _Clip(), _Clip()
    a._ffr_landing_pinned = True
    webm_clip._ffr_touch(a, 100)
    webm_clip._ffr_touch(b, 100)
    victims = webm_clip._ffr_touch(c, 100)  # 300 > 250 → a 被跳过，逐出 b
    assert [v for v, _t in victims] == [b]
    assert a._first_image is not None, "被飞行期 pin 的首帧不得进逐出选型"
    assert webm_clip._first_frame_bytes == 200
