"""ai.agent：协议解析 / 增量读取 / 联动状态机 / 运行时接线 / 设置页。

全部离线：文件在 ``tmp_path`` 里造，时钟与延后调用都是替身，没有固定 sleep。
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from petcore.config import Config
from petcore.contracts.host import AnimationState, HostPorts

MOD_DIR = Path(__file__).resolve().parents[1] / "examples" / "petcore-mods" / "ai.agent"
HOST_PKG = "petcore_agent_test_host"
SOUNDS = Path(__file__).resolve().parents[1] / "assets" / "sounds" / "agent"


def _host_package():
    if HOST_PKG in sys.modules:
        return sys.modules[HOST_PKG]
    spec = importlib.util.spec_from_file_location(
        HOST_PKG,
        MOD_DIR / "host" / "__init__.py",
        submodule_search_locations=[str(MOD_DIR / "host")],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[HOST_PKG] = module
    spec.loader.exec_module(module)
    return module


_host_package()
protocol = importlib.import_module(f"{HOST_PKG}.protocol")
tailer_mod = importlib.import_module(f"{HOST_PKG}.tailer")
links_mod = importlib.import_module(f"{HOST_PKG}.links")
runtime_mod = importlib.import_module(f"{HOST_PKG}.runtime")
settings_mod = importlib.import_module(f"{HOST_PKG}.settings_page")


# ---------------------------------------------------------------- 替身


class FakeClock:
    def __init__(self, start: float = 100.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeScheduler:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.tasks: dict[str, tuple[float, object]] = {}
        self._seq = 0

    def schedule(self, delay_ms: int, callback) -> str:
        self._seq += 1
        handle = f"t{self._seq}"
        self.tasks[handle] = (self.clock() + delay_ms / 1000.0, callback)
        return handle

    def cancel(self, handle) -> None:
        self.tasks.pop(handle, None)

    def pending(self) -> int:
        return len(self.tasks)

    def run_due(self) -> None:
        due = [
            (handle, entry)
            for handle, entry in list(self.tasks.items())
            if entry[0] <= self.clock()
        ]
        for handle, entry in sorted(due, key=lambda item: item[1][0]):
            self.tasks.pop(handle, None)
            entry[1]()


class FakeBridge:
    def __init__(self, prefs=None, *, visible=True, animations=None, state=None) -> None:
        self._prefs = prefs or links_mod.LinkPrefs()
        self._visible = visible
        self._animations = tuple(animations or ("写代码", "吃Token", "轻快记录", "漂浮踏步"))
        self.state = state
        self.state_queries = 0
        self.animations_played: list[str] = []
        self.bubbles: list[tuple[str, bool]] = []
        self.sounds: list[str] = []
        self.idle_resets = 0

    def prefs(self):
        return self._prefs

    def visible(self) -> bool:
        return self._visible

    def animations(self):
        return self._animations

    def animation_state(self):
        self.state_queries += 1
        return self.state

    def play_animation(self, name: str) -> None:
        self.animations_played.append(name)

    def reset_idle(self) -> None:
        self.idle_resets += 1

    def say(self, text: str, important: bool = False) -> None:
        self.bubbles.append((text, important))

    def play_sound(self, kind: str) -> None:
        self.sounds.append(kind)


class LegacyBridge(FakeBridge):
    """旧 Core 的桥：没有 ``animation_state()``（调用方 ``getattr`` 得到非可调用）。

    状态机必须按「未知」退回旧节流口径，不许因此不切动作或抛异常。
    """

    animation_state = None


def _engine(prefs=None, *, visible=True, animations=None, state=None, bridge_cls=FakeBridge):
    clock = FakeClock()
    scheduler = FakeScheduler(clock)
    bridge = bridge_cls(prefs, visible=visible, animations=animations, state=state)
    engine = links_mod.LinkEngine(
        bridge, schedule=scheduler.schedule, cancel=scheduler.cancel, clock=clock
    )
    return engine, bridge, scheduler, clock


def _event(state: str, *, agent: str = "dsh", tool: str = "", event: str = ""):
    return protocol.AgentEvent(agent=agent, state=state, tool=tool, event=event)


# ---------------------------------------------------------------- 协议


@pytest.mark.parametrize("state", protocol.SIX_STATES)
def test_parse_line_accepts_every_six_state(state: str) -> None:
    parsed = protocol.parse_line(json.dumps({"state": state, "agent": "Claude"}))
    assert parsed is not None
    assert parsed.state == state
    assert parsed.agent == "claude"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"event": "UserPromptSubmit"}, "thinking"),
        ({"event": "PreToolUse", "tool": "Bash"}, "working"),
        ({"event": "PostToolUseFailure"}, "error"),
        ({"event": "Stop"}, "attention"),
        ({"event": "turn/start"}, "thinking"),
        ({"event": "tool/call", "tool": "Read"}, "working"),
        ({"event": "execution/failed"}, "error"),
        ({"event": "question/requested"}, "attention"),
        ({"event": "turn/end"}, "attention"),
        ({"event": "turn/end", "reasonKind": "aborted"}, "idle"),
        ({"event": "model_access"}, ""),
        ({"event": "unknown/thing"}, ""),
    ],
)
def test_event_names_map_to_six_states(payload: dict, expected: str) -> None:
    parsed = protocol.parse_line(json.dumps(payload))
    if not expected:
        assert parsed is None
    else:
        assert parsed is not None and parsed.state == expected


def test_parse_line_strips_bom_and_keeps_writer() -> None:
    raw = "\ufeff" + json.dumps({"state": "working", "inst": "boot-1"})
    parsed = protocol.parse_line(raw)
    assert parsed is not None
    assert parsed.writer == "boot-1"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "{broken",
        "[1, 2, 3]",
        json.dumps({"nothing": 1}),
        json.dumps({"state": "working", "pad": "x" * 5000}),
    ],
)
def test_parse_line_rejects_junk(raw: str) -> None:
    assert protocol.parse_line(raw) is None


def test_tool_labels_are_user_facing_and_truncated() -> None:
    assert protocol.tool_label("BASH") == "正在跑命令"
    assert protocol.tool_label("mystery") == protocol.UNKNOWN_TOOL_LABEL
    assert protocol.tool_label("") == ""
    long_label = "正在" + "x" * 200
    protocol.TOOL_LABELS["odd"] = long_label
    try:
        text = protocol.activity_text(_event("working", tool="odd"))
        assert text.endswith("…")
        assert len(text) == protocol.ACTIVITY_TEXT_LIMIT
    finally:
        protocol.TOOL_LABELS.pop("odd", None)


def test_activity_text_only_for_tool_events() -> None:
    assert protocol.activity_text(_event("working", tool="Read")) == "正在读文件"
    assert protocol.activity_text(_event("working")) == ""


# ---------------------------------------------------------------- tailer


def test_tailer_primes_to_end_and_reads_appends(tmp_path: Path) -> None:
    path = tmp_path / "dsh.jsonl"
    path.write_bytes(b'{"state": "idle"}\n')
    tailer = tailer_mod.ByteTailer(path)
    assert tailer.read_lines() == []
    with path.open("ab") as handle:
        handle.write(b'{"state": "working"}\n')
    assert tailer.read_lines() == [b'{"state": "working"}']


def test_tailer_reads_new_file_from_start(tmp_path: Path) -> None:
    path = tmp_path / "late.jsonl"
    tailer = tailer_mod.ByteTailer(path)
    assert tailer.read_lines() == []
    path.write_bytes(b'{"state": "working"}\n')
    assert tailer.read_lines() == [b'{"state": "working"}']


def test_tailer_buffers_half_lines(tmp_path: Path) -> None:
    path = tmp_path / "half.jsonl"
    tailer = tailer_mod.ByteTailer(path)
    assert tailer.read_lines() == []
    path.write_bytes(b'{"state": "work')
    assert tailer.read_lines() == []
    with path.open("ab") as handle:
        handle.write(b'ing"}\n')
    assert tailer.read_lines() == [b'{"state": "working"}']


def test_tailer_replays_one_generation_after_truncation(tmp_path: Path) -> None:
    path = tmp_path / "rot.jsonl"
    path.write_bytes(b"a\nb\n")
    tailer = tailer_mod.ByteTailer(path)
    assert tailer.read_lines() == []
    path.write_bytes(b"c\n")
    assert tailer.read_lines() == [b"c"]


def test_tailer_bounds_each_read(tmp_path: Path) -> None:
    path = tmp_path / "big.jsonl"
    tailer = tailer_mod.ByteTailer(path, max_read=12)
    path.write_bytes(b"111111111\n222222222\n")
    first = tailer.read_lines()
    assert first == [b"111111111"]
    second = tailer.read_lines()
    assert b"222222222" in second


def test_tailer_drops_oversized_partial_line(tmp_path: Path) -> None:
    path = tmp_path / "huge.jsonl"
    path.write_bytes(b"x" * (tailer_mod.MAX_BUFFER_BYTES + 10))
    tailer = tailer_mod.ByteTailer(path, max_read=tailer_mod.MAX_BUFFER_BYTES + 10)
    assert tailer.read_lines() == []
    with path.open("ab") as handle:
        handle.write(b'\n{"state": "idle"}\n')
    assert tailer.read_lines() == [b'{"state": "idle"}']


def test_glob_tailer_reads_all_files_and_drops_missing(tmp_path: Path) -> None:
    first = tmp_path / "dsh-1.jsonl"
    second = tmp_path / "dsh-2.jsonl"
    first.write_bytes(b'{"state": "idle"}\n')
    second.write_bytes(b'{"state": "idle"}\n')
    glob = tailer_mod.GlobTailer(tmp_path, "dsh*.jsonl")
    assert glob.read_lines() == []
    with first.open("ab") as handle:
        handle.write(b'{"state": "working"}\n')
    with second.open("ab") as handle:
        handle.write(b'{"state": "thinking"}\n')
    lines = glob.read_lines()
    assert {line for _path, line in lines} == {
        b'{"state": "working"}',
        b'{"state": "thinking"}',
    }
    first.unlink()
    assert glob.read_lines() == []
    assert len(glob._tailers) == 1


def test_glob_tailer_caps_file_count(tmp_path: Path) -> None:
    for index in range(5):
        path = tmp_path / f"dsh-{index}.jsonl"
        path.write_bytes(b"")
        os.utime(path, (1000 + index, 1000 + index))
    glob = tailer_mod.GlobTailer(tmp_path, "dsh*.jsonl", limit=3)
    assert len(glob.paths()) == 3


def test_agent_key_for_reads_bridge_names(tmp_path: Path) -> None:
    assert tailer_mod.agent_key_for(tmp_path / "dsh-1234.jsonl") == "dsh"
    assert tailer_mod.agent_key_for(tmp_path / "dsh.jsonl") == "dsh"
    assert tailer_mod.agent_key_for(tmp_path / "Claude.jsonl") == "claude"
    assert tailer_mod.agent_key_for(tmp_path / ".jsonl", default="x") == "x"


# ------------------------------------------------------------ 状态机


def test_busy_plays_link_animation_or_break_rotation() -> None:
    engine, bridge, _scheduler, clock = _engine()
    for state in ("working", "thinking", "working", "thinking", "working"):
        engine.handle(_event(state))
        clock.advance(3.0)
    assert bridge.animations_played == ["写代码", "吃Token", "轻快记录", "吃Token", "写代码"]


def test_animation_pool_falls_back_to_keywords() -> None:
    engine, bridge, _scheduler, _clock = _engine(
        animations=("写点代码吧", "摸鱼记录时间", "站立")
    )
    engine.handle(_event("working"))
    engine.handle(_event("attention"))
    engine.handle(_event("working"))
    assert bridge.animations_played[0] == "写点代码吧"


def test_animation_pool_falls_back_to_any_act() -> None:
    engine, bridge, _scheduler, _clock = _engine(animations=("伸个懒腰",))
    engine.handle(_event("working"))
    assert bridge.animations_played == ["伸个懒腰"]


# ------------------------------------- 「还在播就别重发」与旋转地板


def test_busy_keeps_a_still_playing_animation() -> None:
    """状态说我请求的那段还在播 → 后续每一拍 busy 都不许重发。

    真机上 DSH 一轮思考每 1.5–2 秒写一条 busy 事件；重发 ``play_animation``
    会让 WebM 终止+重开 / FrameSeq 回到第 0 帧，整轮都在烧 CPU。
    """
    engine, bridge, _scheduler, clock = _engine(state=AnimationState(name="写代码"))
    engine.handle(_event("working"))
    assert bridge.animations_played == ["写代码"]
    for _ in range(6):  # 12 秒的忙碌尾巴，跨过 8 秒旋转地板
        clock.advance(2.0)
        bridge.state = AnimationState(name="写代码")
        engine.handle(_event("working"))
    assert bridge.animations_played == ["写代码"]
    assert bridge.state_queries > 1  # 每一拍都真的问了 Core，不是碰巧没请求


def test_busy_requests_again_after_the_clip_finished() -> None:
    engine, bridge, _scheduler, clock = _engine(state=AnimationState(name="写代码"))
    engine.handle(_event("working"))
    clock.advance(9.0)
    bridge.state = AnimationState(name="待机")  # 片段播完，行为链换了素材
    engine.handle(_event("working"))
    assert bridge.animations_played == ["写代码", "吃Token"]


def test_busy_holds_the_rotation_floor_even_when_the_name_changed() -> None:
    engine, bridge, _scheduler, clock = _engine(state=AnimationState(name="写代码"))
    engine.handle(_event("working"))
    clock.advance(3.0)
    bridge.state = AnimationState(name="待机")
    engine.handle(_event("working"))
    assert bridge.animations_played == ["写代码"]
    clock.advance(6.0)  # 离上次请求 9 秒，过地板
    engine.handle(_event("working"))
    assert bridge.animations_played == ["写代码", "吃Token"]


def test_busy_requests_again_when_the_pet_is_locked() -> None:
    """接管态（探头/击飞/拖拽）名字未必变，但这段已经不是我们的循环了。"""
    engine, bridge, _scheduler, clock = _engine(
        state=AnimationState(name="写代码", locked=True)
    )
    engine.handle(_event("working"))
    clock.advance(9.0)
    bridge.state = AnimationState(name="写代码", locked=True)
    engine.handle(_event("working"))
    assert bridge.animations_played == ["写代码", "吃Token"]


def test_locked_animation_still_respects_the_rotation_floor() -> None:
    engine, bridge, _scheduler, clock = _engine(
        state=AnimationState(name="写代码", locked=True)
    )
    engine.handle(_event("working"))
    clock.advance(2.0)
    engine.handle(_event("working"))
    assert bridge.animations_played == ["写代码"]


def test_missing_animation_state_keeps_the_old_throttle() -> None:
    """旧 Core 没有 ``animation_state()``：退回每 ``min_interval`` 一拍。"""
    engine, bridge, _scheduler, clock = _engine(bridge_cls=LegacyBridge)
    engine.handle(_event("working"))
    clock.advance(2.5)
    engine.handle(_event("working"))
    assert bridge.animations_played == ["写代码", "吃Token"]


def test_animation_state_errors_count_as_unknown() -> None:
    engine, bridge, _scheduler, _clock = _engine()

    def boom():
        raise RuntimeError("port gone")

    bridge.animation_state = boom
    engine.handle(_event("working"))
    assert bridge.animations_played == ["写代码"]


def test_no_animation_when_hidden_or_disabled() -> None:
    engine, bridge, _scheduler, _clock = _engine(visible=False)
    engine.handle(_event("working"))
    assert bridge.animations_played == []
    engine, bridge, _scheduler, _clock = _engine(links_mod.LinkPrefs(animation=False))
    engine.handle(_event("working"))
    assert bridge.animations_played == []


def test_activity_bubble_throttles_per_agent() -> None:
    engine, bridge, _scheduler, clock = _engine()
    engine.handle(_event("working", tool="Bash"))
    assert bridge.bubbles == [("正在跑命令", False)]
    clock.advance(3.0)
    engine.handle(_event("working", tool="Read"))
    assert len(bridge.bubbles) == 1
    clock.advance(8.0)
    engine.handle(_event("working", tool="Read"))
    assert bridge.bubbles[-1] == ("正在读文件", False)


def test_activity_bubble_same_label_waits_a_minute() -> None:
    engine, bridge, _scheduler, clock = _engine()
    engine.handle(_event("working", tool="Bash"))
    clock.advance(11.0)
    engine.handle(_event("working", tool="Bash"))
    assert len(bridge.bubbles) == 1
    clock.advance(50.0)
    engine.handle(_event("working", tool="Bash"))
    assert len(bridge.bubbles) == 2


def test_activity_bubble_respects_global_interval() -> None:
    engine, bridge, _scheduler, clock = _engine()
    engine.handle(_event("working", tool="Bash", agent="dsh"))
    clock.advance(2.0)
    engine.handle(_event("working", tool="Read", agent="claude"))
    assert len(bridge.bubbles) == 1
    clock.advance(7.0)
    engine.handle(_event("working", tool="Read", agent="claude"))
    assert len(bridge.bubbles) == 2


def test_activity_bubble_can_be_disabled() -> None:
    engine, bridge, _scheduler, _clock = _engine(links_mod.LinkPrefs(activity=False))
    engine.handle(_event("working", tool="Bash"))
    assert bridge.bubbles == []


def test_error_bubble_only_outside_busy() -> None:
    engine, bridge, _scheduler, clock = _engine(links_mod.LinkPrefs(sound=True))
    engine.handle(_event("error"))
    assert bridge.bubbles == [(links_mod.ERROR_TEXT, True)]
    assert bridge.sounds == ["error"]
    clock.advance(10)
    engine.handle(_event("working"))
    engine.handle(_event("error"))
    assert bridge.bubbles.count((links_mod.ERROR_TEXT, True)) == 1


def test_attention_bubble_only_outside_busy() -> None:
    engine, bridge, _scheduler, _clock = _engine()
    engine.handle(_event("attention"))
    assert bridge.bubbles == [(links_mod.ATTENTION_TEXT, True)]


def test_busy_then_attention_reports_completion() -> None:
    engine, bridge, scheduler, clock = _engine()
    engine.handle(_event("working"))
    engine.handle(_event("attention"))
    clock.advance(0.8)
    scheduler.run_due()
    assert bridge.bubbles[-1] == (links_mod.DONE_TEXT, True)
    assert bridge.idle_resets == 1


def test_done_notice_waits_for_the_stable_window() -> None:
    engine, bridge, scheduler, clock = _engine()
    engine.handle(_event("working"))
    engine.handle(_event("idle"))
    clock.advance(0.4)
    scheduler.run_due()
    assert all(text != links_mod.DONE_TEXT for text, _ in bridge.bubbles)
    clock.advance(0.5)
    scheduler.run_due()
    assert bridge.bubbles[-1] == (links_mod.DONE_TEXT, True)


def test_jitter_into_busy_cancels_completion() -> None:
    engine, bridge, scheduler, clock = _engine()
    engine.handle(_event("working"))
    engine.handle(_event("idle"))
    clock.advance(0.3)
    engine.handle(_event("working"))
    clock.advance(1.0)
    scheduler.run_due()
    assert all(text != links_mod.DONE_TEXT for text, _ in bridge.bubbles)


def test_done_cooldown_suppresses_back_to_back_notices() -> None:
    engine, bridge, scheduler, clock = _engine()
    engine.handle(_event("working"))
    engine.handle(_event("idle"))
    clock.advance(0.9)
    scheduler.run_due()
    assert bridge.bubbles.count((links_mod.DONE_TEXT, True)) == 1
    clock.advance(1.0)
    engine.handle(_event("working"))
    engine.handle(_event("idle"))
    clock.advance(1.0)
    scheduler.run_due()
    assert bridge.bubbles.count((links_mod.DONE_TEXT, True)) == 1


def test_failure_turn_does_not_report_success() -> None:
    engine, bridge, scheduler, clock = _engine(links_mod.LinkPrefs(sound=True))
    engine.handle(_event("working"))
    engine.handle(_event("error"))
    clock.advance(1.0)
    scheduler.run_due()
    assert bridge.bubbles[-1] == (links_mod.DONE_ERROR_TEXT, True)
    assert all(text != links_mod.DONE_TEXT for text, _ in bridge.bubbles)
    assert "error" in bridge.sounds


def test_done_notice_can_be_disabled() -> None:
    engine, bridge, scheduler, clock = _engine(links_mod.LinkPrefs(done=False))
    engine.handle(_event("working"))
    engine.handle(_event("idle"))
    clock.advance(1.0)
    scheduler.run_due()
    assert all(text != links_mod.DONE_TEXT for text, _ in bridge.bubbles)


def test_multi_writer_idle_does_not_fake_completion() -> None:
    engine, bridge, scheduler, clock = _engine()
    engine.handle(_event("working"), writer="a")
    engine.handle(_event("working"), writer="b")
    engine.handle(_event("idle"), writer="a")
    clock.advance(1.0)
    scheduler.run_due()
    assert all(text != links_mod.DONE_TEXT for text, _ in bridge.bubbles)
    engine.handle(_event("idle"), writer="b")
    clock.advance(1.0)
    scheduler.run_due()
    assert bridge.bubbles[-1] == (links_mod.DONE_TEXT, True)


def test_sound_only_when_enabled_with_cooldown() -> None:
    engine, bridge, _scheduler, clock = _engine(links_mod.LinkPrefs(sound=False))
    engine.handle(_event("working"))
    assert bridge.sounds == []
    engine, bridge, _scheduler, clock = _engine(
        links_mod.LinkPrefs(sound=True, sound_cooldown=5.0)
    )
    engine.handle(_event("working"))
    clock.advance(1.0)
    engine.handle(_event("idle"))
    engine.handle(_event("working"))
    assert bridge.sounds == ["start"]


def test_states_expose_folded_values() -> None:
    engine, _bridge, _scheduler, _clock = _engine()
    engine.handle(_event("working"), writer="a")
    engine.handle(_event("working"), writer="b")
    assert engine.states() == {"dsh": "working"}
    assert engine.any_busy() is True
    engine.handle(_event("idle"), writer="a")
    engine.handle(_event("idle"), writer="b")
    assert engine.states() == {"dsh": "idle"}
    assert engine.any_busy() is False


# ------------------------------------------------------------ 运行时


class FakeConfigPort:
    def __init__(self, values=None) -> None:
        self.values = dict(values or {})
        self.written: list[tuple[str, object]] = []

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value) -> None:
        self.values[key] = value
        self.written.append((key, value))


class FakeSchedulerPort:
    def __init__(self) -> None:
        self.pending: list = []

    def call_later(self, delay_ms, callback):
        self.pending.append(callback)
        return f"h{len(self.pending)}"

    def call_every(self, interval_ms, callback):
        return "every"

    def cancel(self, handle) -> None:
        return None

    def cancel_owner(self, owner) -> None:
        return None

    def run(self) -> None:
        pending, self.pending = self.pending, []
        for callback in pending:
            callback()


class FakePresentation:
    def __init__(self) -> None:
        self.said: list[tuple[str, bool]] = []
        self.animations: list[str] = []
        self.visible_flag = True
        self.current_name = ""
        self.loop_active = False
        self.released_loops = 0

    def say(self, text, *, duration_ms=4000, force=False, subtitle="") -> None:
        self.said.append((text, force))

    def show_image(self, *args, **kwargs) -> bool:
        return True

    def animation_state(self):
        return AnimationState(
            name=self.current_name, loop=bool(self.loop_active), locked=False
        )

    def play_animation(self, name, *, loop=False) -> bool:
        self.animations.append(name)
        self.current_name = name
        self.loop_active = bool(loop)
        return True

    def release_loop(self) -> bool:
        self.released_loops += 1
        was = self.loop_active
        self.loop_active = False
        return was

    def play_event(self, group, variant=None) -> bool:
        return True

    def notify(self, title, message, *, force=False) -> None:
        self.said.append((f"{title}：{message}", force))

    def visible(self) -> bool:
        return self.visible_flag


class FakeAudio:
    def __init__(self) -> None:
        self.played: list[Path] = []

    def play(self, path, volume=None) -> bool:
        self.played.append(Path(path))
        return True

    def set_volume(self, volume) -> None:
        return None


class FakeStorage:
    def __init__(self, root: Path) -> None:
        self.root = root

    def data_dir(self, owner: str) -> Path:
        path = self.root / "feature-data" / owner
        path.mkdir(parents=True, exist_ok=True)
        return path

    def config_dir(self, owner: str) -> Path:
        return self.data_dir(owner)

    def data_root(self) -> Path:
        return self.root


class FakeResources:
    def bundle_path(self, *parts: str) -> Path:
        return MOD_DIR.parents[2].joinpath(*parts)


class FakePet:
    def animations(self):
        return ("写代码", "吃Token")


class FakePets:
    def primary(self):
        return FakePet()


def _data_root(tmp_path: Path) -> Path:
    root = tmp_path / "dsh-pet-core"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _events_file(tmp_path: Path, name: str = "demo.jsonl") -> Path:
    return _data_root(tmp_path) / "agent-events" / name


def _runtime(tmp_path: Path, values=None):
    config = FakeConfigPort(
        {
            "ai_agent_enabled": True,
            "ai_agent_sound_enabled": True,
            "ai_agent_custom_agents": [],
            **(values or {}),
        }
    )
    presentation = FakePresentation()
    audio = FakeAudio()
    scheduler = FakeSchedulerPort()
    ports = HostPorts(
        presentation=presentation,
        scheduler=scheduler,
        audio=audio,
        storage=FakeStorage(_data_root(tmp_path)),
        resources=FakeResources(),
        config=config,
        pets=FakePets(),
    )
    context = SimpleNamespace(
        owner="ai.agent", ports=ports, log=lambda *a, **k: None
    )
    runtime = runtime_mod.AgentLinkRuntime(context)
    return runtime, config, presentation, audio, scheduler


def _append(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def test_runtime_drains_unified_events(tmp_path: Path) -> None:
    runtime, _config, presentation, _audio, scheduler = _runtime(tmp_path)
    events = _events_file(tmp_path, "claude.jsonl")
    runtime._drain_once()  # 建源，并跳到各自末尾
    _append(events, {"event": "PreToolUse", "tool": "Bash", "agent": "claude"})
    runtime._drain_once()
    scheduler.run()
    assert presentation.said[-1][0] == "正在跑命令"
    assert presentation.animations == ["写代码"]


def test_runtime_forwards_animation_state(tmp_path: Path) -> None:
    runtime, _config, presentation, _audio, _scheduler = _runtime(tmp_path)
    presentation.current_name = "写代码"
    state = runtime.animation_state()
    assert state == AnimationState(name="写代码", loop=False, locked=False)
    assert runtime.engine._bridge.animation_state() == state


def test_runtime_animation_state_is_none_without_the_port_method(tmp_path: Path) -> None:
    runtime, *_rest = _runtime(tmp_path)
    runtime.ports.presentation = SimpleNamespace(visible=lambda: True)
    assert runtime.animation_state() is None
    assert runtime.engine._bridge.animation_state() is None  # 不抛


def test_runtime_animation_state_errors_are_none(tmp_path: Path) -> None:
    runtime, *_rest = _runtime(tmp_path)

    def boom():
        raise RuntimeError("window gone")

    runtime.ports.presentation.animation_state = boom
    assert runtime.animation_state() is None


def test_done_path_does_not_release_a_one_shot_animation(tmp_path: Path) -> None:
    runtime, _config, presentation, _audio, scheduler = _runtime(tmp_path)
    runtime._drain_once()
    events = _events_file(tmp_path)
    _append(events, {"event": "PreToolUse", "tool": "Bash", "agent": "demo"})
    runtime._drain_once()
    scheduler.run()
    assert presentation.animations == ["写代码"]
    assert presentation.loop_active is False  # 本 mod 只请求一次性动作
    _append(events, {"event": "Stop", "agent": "demo"})
    runtime._drain_once()
    scheduler.run()  # 应用事件期间才排上完成确认
    scheduler.run()
    assert presentation.said[-1] == (links_mod.DONE_TEXT, True)
    assert presentation.released_loops == 0


def test_reset_idle_releases_a_loop_only_when_we_requested_one(tmp_path: Path) -> None:
    runtime, _config, presentation, _audio, _scheduler = _runtime(tmp_path)
    # 别人（唱歌/报时）的循环在跑：本 mod 没请求过 loop，绝不许 release。
    presentation.loop_active = True
    runtime.reset_idle()
    assert presentation.released_loops == 0

    runtime.play_animation("写代码", loop=True)
    assert presentation.loop_active is True
    runtime.reset_idle()
    assert presentation.released_loops == 1
    runtime.reset_idle()
    assert presentation.released_loops == 1


def test_runtime_reads_dsh_bridge_records(tmp_path: Path) -> None:
    runtime, _config, _presentation, _audio, scheduler = _runtime(tmp_path)
    runtime._drain_once()
    bridge = tmp_path / "dsh-pet-bridge" / "dsh-4242.jsonl"
    _append(bridge, {"event": "tool/call", "tool": "Read", "inst": "boot"})
    runtime._drain_once()
    scheduler.run()
    assert runtime.engine.states() == {"dsh": "working"}


def test_runtime_advances_offsets_while_disabled(tmp_path: Path) -> None:
    runtime, config, presentation, _audio, scheduler = _runtime(
        tmp_path, {"ai_agent_enabled": False}
    )
    events = _events_file(tmp_path)
    runtime._drain_once()
    _append(events, {"state": "working"})
    runtime._drain_once()
    scheduler.run()
    assert presentation.animations == []
    config.set("ai_agent_enabled", True)
    runtime._drain_once()
    scheduler.run()
    assert presentation.animations == []


def test_runtime_rebuilds_sources_when_config_changes(tmp_path: Path) -> None:
    runtime, config, _presentation, _audio, _scheduler = _runtime(tmp_path)
    runtime._drain_once()
    assert len(runtime._current_sources()) == 2
    config.set("ai_agent_events_enabled", False)
    assert len(runtime._current_sources()) == 1


def test_runtime_custom_agent_file(tmp_path: Path) -> None:
    custom = tmp_path / "custom" / "my.jsonl"
    runtime, _config, _presentation, _audio, scheduler = _runtime(
        tmp_path,
        {"ai_agent_custom_agents": [{"key": "mine", "name": "Mine", "path": str(custom)}]},
    )
    runtime._drain_once()
    _append(custom, {"state": "working"})
    runtime._drain_once()
    scheduler.run()
    assert runtime.engine.states() == {"mine": "working"}


def test_runtime_plays_builtin_sound(tmp_path: Path) -> None:
    runtime, _config, _presentation, audio, scheduler = _runtime(tmp_path)
    events = _events_file(tmp_path)
    runtime._drain_once()
    _append(events, {"state": "working"})
    runtime._drain_once()
    scheduler.run()
    assert audio.played and audio.played[-1].name == "start.wav"


def test_runtime_toggle_writes_config_and_reports(tmp_path: Path) -> None:
    runtime, config, presentation, _audio, _scheduler = _runtime(
        tmp_path, {"ai_agent_enabled": False}
    )
    runtime.toggle()
    assert config.values["ai_agent_enabled"] is True
    assert "已开启" in presentation.said[-1][0]
    runtime.toggle()
    assert config.values["ai_agent_enabled"] is False
    assert presentation.said[-1] == ("Agent 联动已关闭", True)


def test_runtime_toggle_without_config_port(tmp_path: Path) -> None:
    runtime, _config, presentation, _audio, _scheduler = _runtime(tmp_path)
    runtime.ports.config = None
    runtime.toggle()
    assert "不允许扩展改设置" in presentation.said[-1][0]


def test_runtime_recent_events_are_bounded(tmp_path: Path) -> None:
    runtime, _config, _presentation, _audio, scheduler = _runtime(tmp_path)
    events = _events_file(tmp_path)
    runtime._drain_once()
    for index in range(30):
        _append(events, {"state": "working" if index % 2 else "idle"})
    runtime._drain_once()
    scheduler.run()
    assert len(runtime.recent()) == runtime_mod.MAX_RECENT


# ------------------------------------------------------------ 设置页


from PySide6.QtWidgets import QApplication, QPlainTextEdit  # noqa: E402


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


class FakeSettingsHost:
    def __init__(self, root: Path, values=None) -> None:
        self.root = root
        self.config = FakeConfigPort(values)

    def presentation_port(self):
        return FakePresentation()


def _page(app, tmp_path: Path, values=None):
    host = FakeSettingsHost(tmp_path, values)
    page = settings_mod.AgentLinkPage(host)
    return page, host


def test_custom_rows_round_trip() -> None:
    text = "claude | Claude Code | C:/x/claude.jsonl\n# 注释\nbad row\nmine|Mine|D:/m.jsonl"
    rows = settings_mod.parse_custom_rows(text)
    assert [row["key"] for row in rows] == ["claude", "mine"]
    assert settings_mod.format_custom_rows(rows).splitlines()[0].startswith("claude")


def test_page_builds_with_grouped_sections(app, tmp_path: Path) -> None:
    from PySide6.QtWidgets import QLabel

    page, _host = _page(app, tmp_path, {"ai_agent_enabled": True})
    assert page.title == "Agent 联动"
    assert page.enabled.isChecked() is True
    assert isinstance(page.diagnostics, QPlainTextEdit)
    headings = [
        (label.objectName(), label.text())
        for label in page.findChildren(QLabel)
        if label.objectName() in {"groupTitle", "subsectionTitle"}
    ]
    assert headings[0] == ("groupTitle", "Agent 联动")
    assert [text for name, text in headings if name == "subsectionTitle"] == [
        "总开关",
        "事件来源",
        "动画与气泡",
        "音效",
        "诊断",
    ]


def test_page_writes_live_and_discards(app, tmp_path: Path) -> None:
    page, host = _page(app, tmp_path, {"ai_agent_enabled": False})
    page.enabled.setChecked(True)
    assert host.config.values["ai_agent_enabled"] is True
    assert page.dirty() is True
    page.discard_changes()
    assert host.config.values["ai_agent_enabled"] is False
    assert page.enabled.isChecked() is False


def test_page_diagnostics_lists_sources(app, tmp_path: Path) -> None:
    page, _host = _page(app, tmp_path)
    page.refresh_diagnostics()
    text = page.diagnostics.toPlainText()
    assert "agent-events" in text
    assert "dsh-pet-bridge" in text


# ---------------------------------------------------------------- 配置


def test_core_config_normalizes_agent_keys(tmp_path: Path) -> None:
    config = Config(
        tmp_path,
        {
            "ai_agent_enabled": "yes",
            "ai_agent_min_interval_seconds": 99,
            "ai_agent_sound_volume": -1,
            "ai_agent_sound_cooldown_seconds": "x",
            "ai_agent_custom_agents": [
                {"key": "Claude", "path": "~/.claude.jsonl"},
                {"key": "claude", "path": "dup"},
                {"key": "-bad", "path": "x"},
                {"key": "ok", "path": ""},
                {"key": "dsh", "path": "reserved"},
                "junk",
            ],
            "ai_agent_sound_start_path": "",
        },
    ).data
    assert config["ai_agent_enabled"] is False
    assert config["ai_agent_min_interval_seconds"] == 10.0
    assert config["ai_agent_sound_volume"] == 0.0
    assert config["ai_agent_sound_cooldown_seconds"] == 2.0
    assert [item["key"] for item in config["ai_agent_custom_agents"]] == ["claude"]
    assert config["ai_agent_sound_start_path"] == "builtin:agent-start"


def test_core_config_accepts_eight_custom_agents_at_most(tmp_path: Path) -> None:
    rows = [{"key": f"a{index}", "path": f"/tmp/{index}.jsonl"} for index in range(12)]
    config = Config(tmp_path, {"ai_agent_custom_agents": rows}).data
    assert len(config["ai_agent_custom_agents"]) == 8


# ------------------------------------------------------------ 端口接缝


def test_pet_handle_animations_use_the_act_graph() -> None:
    """`PetHandle.animations()` 必须取「动作」池（`GraphType.DEFAULT`）。

    真机冒烟时这条曾经错写成 `GraphType.ACT`（枚举里没有）：异常被端口吞掉，
    mod 拿到空池 → 联动永远不切动作，而单元测试用替身宠物看不到这个问题。
    """
    from petcore.contracts.content import AnimationSpec
    from petcore.engine.catalog import GraphCatalog
    from petcore.engine.enums import AnimatType, GraphType
    from petcore.ui.ports import PetHandleImpl

    catalog = GraphCatalog(
        [
            AnimationSpec(name="写代码", graph=GraphType.DEFAULT, animat=AnimatType.SINGLE),
            AnimationSpec(name="待机", graph=GraphType.IDEL, animat=AnimatType.B_LOOP),
        ]
    )
    window = SimpleNamespace(
        slot=0,
        is_primary=True,
        config=None,
        catalog=catalog,
    )
    handle = PetHandleImpl(SimpleNamespace(), window)
    assert handle.animations() == ("写代码",)


def test_pet_handle_animations_tolerate_missing_catalog() -> None:
    from petcore.ui.ports import PetHandleImpl

    window = SimpleNamespace(slot=0, is_primary=True, config=None)
    handle = PetHandleImpl(SimpleNamespace(), window)
    assert handle.animations() == ()


@pytest.mark.parametrize("action", ["stop", "close", "disable"])
def test_runtime_drops_queued_events_after_deactivation(tmp_path, action):
    runtime, config, presentation, audio, scheduler = _runtime(tmp_path)
    runtime._drain_once()
    _append(_events_file(tmp_path), {"event": "PreToolUse", "tool": "Bash", "agent": "demo"})
    runtime._drain_once()
    if action == "disable":
        config.set("ai_agent_enabled", False)
    else:
        getattr(runtime, action)()
    scheduler.run()
    assert presentation.said == []
    assert presentation.animations == []
    assert audio.played == []
    assert runtime.recent() == []


def test_runtime_stop_cancels_done_confirmation_even_when_scheduler_cancel_races(tmp_path):
    runtime, _config, presentation, audio, scheduler = _runtime(tmp_path)
    runtime._drain_once()
    events = _events_file(tmp_path)
    _append(events, {"event": "PreToolUse", "tool": "Bash", "agent": "demo"})
    runtime._drain_once()
    scheduler.run()
    _append(events, {"event": "Stop", "agent": "demo"})
    runtime._drain_once()
    scheduler.run()
    assert scheduler.pending  # done callback has been enqueued
    runtime.stop()
    presentation.said.clear()
    audio.played.clear()
    scheduler.run()  # this scheduler deliberately cannot retract dispatched tasks
    assert presentation.said == []
    assert audio.played == []


def test_runtime_toggle_does_not_read_event_files_on_gui_thread(tmp_path, monkeypatch):
    runtime, _config, presentation, _audio, _scheduler = _runtime(
        tmp_path, {"ai_agent_enabled": False})

    def forbidden(_self):
        raise AssertionError("event-file discovery must stay in the worker")

    monkeypatch.setattr(runtime_mod.GlobTailer, "paths", forbidden)
    runtime.toggle()
    assert presentation.said


def test_runtime_blocked_reader_stop_and_restart_are_nonblocking_and_single_worker(tmp_path, monkeypatch):
    import threading
    import time

    entered, release = threading.Event(), threading.Event()
    runtime, _config, _presentation, _audio, scheduler = _runtime(tmp_path, {
        "ai_agent_dsh_enabled": False, "ai_agent_events_enabled": False,
        "ai_agent_custom_agents": [{"key": "test", "path": str(tmp_path / "events.jsonl")}],
    })
    original = runtime_mod.ByteTailer.read_lines

    def blocked(reader):
        entered.set()
        assert release.wait(15), "test must release the OS reader"
        return original(reader)

    monkeypatch.setattr(runtime_mod.ByteTailer, "read_lines", blocked)
    threads = []
    try:
        runtime.start()
        threads.append(runtime._thread)
        assert entered.wait(10)
        began = time.monotonic()
        runtime.stop()
        elapsed = time.monotonic() - began
        runtime.start()
        threads.append(runtime._thread)
        assert runtime._thread is threads[0], "live cancelled reader must not be replaced"
        assert elapsed < 1.0, "GUI stop must not join a blocked reader"
        release.set()
        threads[0].join(10)
        assert not threads[0].is_alive()
        scheduler.run()
        assert runtime._thread is not threads[0]
        threads.append(runtime._thread)
    finally:
        runtime.close()
        release.set()
        for thread in threads:
            thread.join(10)
            assert not thread.is_alive()


def test_closed_runtime_cannot_restart(tmp_path):
    runtime, *_rest = _runtime(tmp_path)
    runtime.close()
    try:
        runtime.start()
        assert runtime._thread is None or not runtime._thread.is_alive()
    finally:
        runtime.stop()
