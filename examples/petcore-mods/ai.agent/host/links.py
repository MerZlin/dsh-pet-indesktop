"""联动状态机：六态 → 联动动作 / 过程气泡 / 完成通知 / 音效。

无 Qt、无文件 IO：runtime 把解析后的事件喂进 ``handle()``，呈现经由注入的
``bridge`` 完成。时钟与延后调用都可注入，因此整条状态机可以脱离事件循环测试。

``bridge`` 需要提供：

* ``prefs() -> LinkPrefs``：每次判定现取，设置改完立即生效；
* ``visible() -> bool``：隐藏态不切动画；
* ``animations() -> tuple[str, ...]``：当前角色的动作名；
* ``animation_state() -> AnimationState | None``（可选）：正在播的动作名 / 循环标志 /
  接管态；旧 Core 没有这个方法，返回 ``None`` 表示「未知」；
* ``play_animation(name)`` / ``reset_idle()`` / ``say(text, important=False)``；
* ``play_sound(kind)``：``kind`` 为 ``start`` / ``done`` / ``error``。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from .protocol import BUSY_STATES, AgentEvent, activity_text

#: 联动动作池：主动作严格交替，每第 3 次插播一个「摸鱼」动作（同主分支）。
MAIN_ANIMATIONS = ("写代码", "吃Token")
BREAK_ANIMATIONS = ("轻快记录", "漂浮踏步")
MAIN_KEYWORDS = ("代码", "工作", "写", "打字", "敲")
BREAK_KEYWORDS = ("记录", "踏步", "伸懒腰")

#: 六态各自的动作池。精确名不存在时按关键词回退，再回退到主动作池。
THINKING_ANIMATIONS = ("思考", "想", "托腮", "发呆")
THINKING_KEYWORDS = ("思考", "想", "托腮", "发呆", "沉思")
ATTENTION_ANIMATIONS = ("招手", "惊讶", "举手", "歪头")
ATTENTION_KEYWORDS = ("招手", "惊讶", "举手", "歪头", "注意")
SUCCESS_ANIMATIONS = ("开心", "欢呼", "笑", "庆祝")
SUCCESS_KEYWORDS = ("开心", "欢呼", "笑", "庆祝")
ERROR_ANIMATIONS = ("难过", "垂头", "哭", "沮丧")
ERROR_KEYWORDS = ("难过", "垂头", "哭", "沮丧")

#: busy → 非 busy 的稳定确认窗口：过滤 working→idle→working 抖动。
DONE_CONFIRM_MS = 800
#: 同一 Agent 完成通知的最小间隔（最后一道保险）。
DONE_COOLDOWN_S = 5.0
#: 过程气泡节流：同 Agent / 全局 / 同一文案。
ACTIVITY_MIN_INTERVAL_S = 10.0
ACTIVITY_GLOBAL_MIN_S = 8.0
ACTIVITY_SAME_LABEL_S = 60.0
#: 音效冷却（默认值，可被配置覆盖）。
SOUND_COOLDOWN_S = 2.0
#: 联动动作的最小旋转间隔。一次联动动作是 10 秒级的单次片段，而 DSH 一轮思考里
#: 每 1.5 s 就可能写一条 busy 事件：按 ``ai_agent_min_interval_seconds``（默认 2 s）
#: 重发 ``play_animation`` 会让 WebM 终止+重开、FrameSeq 回到第 0 帧，整轮思考都在
#: 烧 CPU。即使上一次动作已经播完、名字变了，也不许比这个间隔更频繁地换动作。
AGENT_ANIMATION_MIN_SECONDS = 8.0

ERROR_TEXT = "好像遇到报错了…"
ATTENTION_TEXT = "这边需要你看一眼～"
DONE_TEXT = "干完活啦～"
DONE_ERROR_TEXT = "这轮好像没能跑完…"
START_TEXT = "开始干活啦～"


@dataclass(frozen=True, slots=True)
class LinkPrefs:
    """一次判定读到的配置快照。"""

    animation: bool = True
    activity: bool = True
    done: bool = True
    sound: bool = False
    min_interval: float = 2.0
    sound_cooldown: float = SOUND_COOLDOWN_S


class LinkEngine:
    def __init__(
        self,
        bridge,
        *,
        schedule: Callable[[int, Callable[[], None]], object],
        cancel: Callable[[object], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._bridge = bridge
        self._schedule = schedule
        self._cancel = cancel
        self._clock = clock
        # (agent, writer) → 写者各自的原始状态。多实例 DSH 各写一份，
        # 一个实例回待机不能把另一个实例的忙判成待机。
        self._raw: dict[tuple[str, str], str] = {}
        # agent → (上次生效的折叠状态, 时刻)，只用于动画/普通气泡节流。
        self._applied: dict[str, tuple[str, float]] = {}
        self._done_handle: dict[str, object] = {}
        self._done_cooldown: dict[str, float] = {}
        self._last_activity: dict[str, tuple[str, float]] = {}
        self._activity_global_last = 0.0
        self._sound_at: dict[tuple[str, str], float] = {}
        self._link_seq = 0
        # 上一次真正请求的联动动作（名字 + 时刻）：用来判断「我请求的那段还在播吗」
        # 与旋转地板。跨 Agent 共用一份——动画状态是整个桌宠的，不分 Agent。
        self._last_anim_name = ""
        self._last_anim_at: float | None = None

    # ------------------------------------------------------------ 查询

    def states(self) -> dict[str, str]:
        """各 Agent 的折叠状态（设置页诊断用）。"""
        agents = {agent for agent, _writer in self._raw}
        return {agent: self._fold(agent) for agent in sorted(agents)}

    def any_busy(self) -> bool:
        return any(state in BUSY_STATES for state in self.states().values())

    def reset(self) -> None:
        for handle in self._done_handle.values():
            self._cancel(handle)
        self._done_handle.clear()
        self._raw.clear()
        self._applied.clear()
        self._link_seq = 0
        self._last_anim_name = ""
        self._last_anim_at = None

    def close(self) -> None:
        self.reset()

    # ------------------------------------------------------------ 入口

    def handle(self, event: AgentEvent, *, writer: str = "default") -> None:
        if not event.state:
            return
        agent = event.agent or "agent"
        key = (agent, writer or "default")
        prev_raw = self._raw.get(key, "")
        folded_before = self._fold(agent)
        self._raw[key] = event.state
        folded_after = self._fold(agent)
        now = self._clock()

        self._maybe_activity(agent, event, now)

        if event.state in BUSY_STATES:
            if prev_raw not in BUSY_STATES:
                self._sound(agent, "start", now)
            self._on_busy(agent, folded_before, now, event.state)
        elif event.state == "error":
            if prev_raw in BUSY_STATES:
                self._schedule_done(agent, cause="error")
            else:
                self._important(ERROR_TEXT)
                self._sound(agent, "error", now)
            self._play_state_animation("error", now)
        elif event.state == "attention":
            if prev_raw not in BUSY_STATES:
                self._important(ATTENTION_TEXT)
            self._play_state_animation("attention", now)
        if folded_before in BUSY_STATES and folded_after not in BUSY_STATES:
            self._schedule_done(agent, cause=self._failure_cause(agent))
        if folded_before not in BUSY_STATES and folded_after in BUSY_STATES:
            self._cancel_done(agent)

    # ------------------------------------------------------------ 内部

    def _fold(self, agent: str) -> str:
        states = [state for (name, _writer), state in self._raw.items() if name == agent]
        if not states:
            return ""
        if any(state in BUSY_STATES for state in states):
            return "working"
        for candidate in ("error", "attention", "sleeping", "idle"):
            if candidate in states:
                return candidate
        return ""

    def _failure_cause(self, agent: str) -> str:
        states = [state for (name, _writer), state in self._raw.items() if name == agent]
        return "error" if "error" in states else "done"

    def _prefs(self) -> LinkPrefs:
        prefs = self._bridge.prefs()
        return prefs if isinstance(prefs, LinkPrefs) else LinkPrefs()

    def _throttled(self, agent: str, now: float) -> bool:
        prefs = self._prefs()
        last = self._applied.get(agent)
        return bool(last is not None and (now - last[1]) < prefs.min_interval)

    def _on_busy(
        self, agent: str, folded_before: str, now: float, state: str = "working"
    ) -> None:
        prefs = self._prefs()
        if folded_before in BUSY_STATES and self._throttled(agent, now):
            return
        self._applied[agent] = ("working", now)
        if not prefs.animation or not self._bridge.visible():
            return
        self._play_state_animation(state, now)

    def _play_state_animation(self, state: str, now: float) -> None:
        """按六态播一次动作；隐藏/关闭/还在播/旋转地板内一律不发。"""
        prefs = self._prefs()
        if not prefs.animation or not self._bridge.visible():
            return
        if not self._should_request(now):
            return
        name = self._next_animation(state)
        if not name:
            return
        self._last_anim_name = name
        self._last_anim_at = now
        self._bridge.play_animation(name)

    def _animation_state(self):
        """问 Core 当前动画状态；旧桥没有这个方法就返回 ``None``（= 未知）。"""
        query = getattr(self._bridge, "animation_state", None)
        if not callable(query):
            return None
        try:
            return query()
        except Exception:  # noqa: BLE001 - 查询失败按未知处理，不该打断状态机
            return None

    def _should_request(self, now: float) -> bool:
        """这一拍要不要真的请求联动动作（不改旋转序列）。

        状态可读时两条闸门，缺一条就是「整轮思考每 2 秒重开一次素材」：

        1. 我上一次请求的那段**还在播**（名字相同、当前没被探头/击飞/拖拽接管）
           → 让它播完，不重发；
        2. 旋转地板 :data:`AGENT_ANIMATION_MIN_SECONDS`：即使名字已经变了，也
           不许比这更频繁地换动作。

        旧 Core 查不到状态（``None``）时退回旧的节流口径：每
        ``min_interval`` 一拍请求一次，不额外加闸（不认识的状态不猜）。
        """
        state = self._animation_state()
        if state is None:
            return True
        last_at = self._last_anim_at
        if last_at is not None and (now - last_at) < AGENT_ANIMATION_MIN_SECONDS:
            return False
        if self._last_anim_name and not bool(getattr(state, "locked", False)):
            if str(getattr(state, "name", "") or "") == self._last_anim_name:
                return False
        return True

    def _next_animation(self, state: str = "working") -> str:
        acts = tuple(self._bridge.animations() or ())
        if not acts:
            return ""
        state_pools = {
            "thinking": (THINKING_ANIMATIONS, THINKING_KEYWORDS),
            "attention": (ATTENTION_ANIMATIONS, ATTENTION_KEYWORDS),
            "success": (SUCCESS_ANIMATIONS, SUCCESS_KEYWORDS),
            "error": (ERROR_ANIMATIONS, ERROR_KEYWORDS),
        }
        exact, keywords = state_pools.get(state, ((), ()))
        if exact:
            names = [name for name in exact if name in acts]
            if not names:
                names = [name for name in acts if any(key in name for key in keywords)]
            if names:
                return names[0]
        main = [name for name in MAIN_ANIMATIONS if name in acts]
        if not main:
            main = [name for name in acts if any(key in name for key in MAIN_KEYWORDS)]
        brk = [name for name in BREAK_ANIMATIONS if name in acts]
        if not brk:
            brk = [name for name in acts if any(key in name for key in BREAK_KEYWORDS)]
        if not main and not brk:
            main = list(acts)
        self._link_seq += 1
        if brk and self._link_seq % 3 == 0:
            return brk[(self._link_seq // 3 - 1) % len(brk)]
        pool = main or brk
        return pool[(self._link_seq - 1) % len(pool)]

    def _maybe_activity(self, agent: str, event: AgentEvent, now: float) -> None:
        if event.state not in BUSY_STATES:
            return
        text = activity_text(event)
        if not text:
            return
        prefs = self._prefs()
        if not prefs.activity:
            return
        last = self._last_activity.get(agent)
        if last is not None:
            last_text, last_at = last
            if now - last_at < ACTIVITY_MIN_INTERVAL_S:
                return
            if last_text == text and now - last_at < ACTIVITY_SAME_LABEL_S:
                return
        if now - self._activity_global_last < ACTIVITY_GLOBAL_MIN_S:
            return
        self._last_activity[agent] = (text, now)
        self._activity_global_last = now
        self._bridge.say(text)

    def _important(self, text: str) -> None:
        self._bridge.say(text, important=True)

    def _sound(self, agent: str, kind: str, now: float) -> None:
        prefs = self._prefs()
        if not prefs.sound:
            return
        key = (agent, kind)
        last = self._sound_at.get(key)
        if last is not None and now - last < max(0.0, prefs.sound_cooldown):
            return
        self._sound_at[key] = now
        self._bridge.play_sound(kind)

    def _schedule_done(self, agent: str, *, cause: str) -> None:
        self._cancel_done(agent)
        prefs = self._prefs()
        if not prefs.done:
            return
        now = self._clock()
        if now - self._done_cooldown.get(agent, 0.0) < DONE_COOLDOWN_S:
            return
        self._done_handle[agent] = self._schedule(
            DONE_CONFIRM_MS, lambda agent=agent, cause=cause: self._finish(agent, cause)
        )

    def _cancel_done(self, agent: str) -> None:
        handle = self._done_handle.pop(agent, None)
        if handle is not None:
            self._cancel(handle)

    def _finish(self, agent: str, cause: str) -> None:
        self._done_handle.pop(agent, None)
        if self._fold(agent) in BUSY_STATES:
            # 确认窗口里又忙起来了：这轮没结束，不报完成。
            return
        now = self._clock()
        self._done_cooldown[agent] = now
        prefs = self._prefs()
        if not prefs.done:
            return
        if cause == "error":
            self._bridge.say(DONE_ERROR_TEXT, important=True)
            self._sound(agent, "error", now)
            return
        self._play_state_animation("success", now)
        self._bridge.say(DONE_TEXT, important=True)
        self._sound(agent, "done", now)
        if self._bridge.visible():
            self._bridge.reset_idle()
