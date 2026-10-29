"""ai.agent 运行时：轮询事件文件 → 状态机 → 呈现。

Qt-free：文件 IO 在一条后台线程里做，解析结果经 ``scheduler.call_later(0, …)``
回到 GUI 线程，所有呈现调用只发生在 GUI 线程。配置每轮现读，改完即生效。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

from .links import LinkEngine, LinkPrefs
from .protocol import parse_line
from .tailer import ByteTailer, GlobTailer, agent_key_for

#: 轮询间隔（协议 §1 的 1.5s 节拍）。
POLL_SECONDS = 1.5
#: 诊断面板保留的最近事件条数。
MAX_RECENT = 20
#: 内置音效文件名（``assets/sounds/agent``）。
SOUND_FILES = {"start": "start.wav", "done": "done.wav", "error": "error.wav"}


@dataclass(slots=True)
class _Source:
    key: str
    reader: object
    per_file_key: bool = False

    def read(self) -> list[tuple[Path, bytes]]:
        if isinstance(self.reader, GlobTailer):
            return self.reader.read_lines()
        return [(self.reader.path, line) for line in self.reader.read_lines()]

    def paths(self) -> list[Path]:
        if isinstance(self.reader, GlobTailer):
            return self.reader.paths()
        return [self.reader.path]


class _Bridge:
    def __init__(self, runtime: "AgentLinkRuntime") -> None:
        self._runtime = runtime

    def prefs(self) -> LinkPrefs:
        return self._runtime.prefs()

    def visible(self) -> bool:
        return self._runtime.visible()

    def animations(self) -> tuple[str, ...]:
        return self._runtime.animations()

    def animation_state(self):
        return self._runtime.animation_state()

    def play_animation(self, name: str) -> None:
        self._runtime.play_animation(name)

    def reset_idle(self) -> None:
        self._runtime.reset_idle()

    def say(self, text: str, important: bool = False) -> None:
        self._runtime.say(text, important=important)

    def play_sound(self, kind: str) -> None:
        self._runtime.play_sound(kind)


class AgentLinkRuntime:
    def __init__(self, context) -> None:
        self.context = context
        self.ports = context.ports
        self.commands = {"agent_toggle": self.toggle}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._generation = 0
        self._wanted = False
        self._closed = False
        self._restart_handle = None
        self._lock = threading.Lock()
        self._sources: list[_Source] = []
        self._signature: object = None
        self._recent: list[dict] = []
        #: 本 runtime 是否真的请求过 loop=True 的动画；没有就绝不许 release_loop()，
        #: 否则会误清唱歌/报时等其它持有者的循环。
        self._requested_loop = False
        self.engine = LinkEngine(
            _Bridge(self),
            schedule=self._schedule,
            cancel=self._cancel,
            clock=time.monotonic,
        )

    # ------------------------------------------------------------ 生命周期

    def start(self) -> None:
        # Lifecycle calls and retries belong to the GUI thread. Never replace a
        # cancelled worker until its last filesystem read has actually returned.
        if self._closed:
            return
        self._wanted = True
        if self._thread is not None and self._thread.is_alive():
            if self._stop.is_set() and self._restart_handle is None:
                scheduler = getattr(self.ports, "scheduler", None)
                schedule = getattr(scheduler, "call_later", None)
                if callable(schedule):
                    self._restart_handle = schedule(100, self._retry_start)
            return
        self._generation += 1
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, args=(self._stop,), name="ai-agent-link", daemon=True
        )
        self._thread.start()

    def _retry_start(self) -> None:
        self._restart_handle = None
        if self._wanted and not self._closed:
            self.start()

    def stop(self) -> None:
        self._wanted = False
        self._generation += 1
        self._stop.set()
        self._cancel(self._restart_handle)
        self._restart_handle = None
        self.engine.reset()

    def close(self) -> None:
        self._closed = True
        self.stop()

    # ------------------------------------------------------------ 命令

    def toggle(self, slot: int | None = None) -> None:
        """菜单「Agent 联动」：切换总开关并立即落盘。"""
        port = getattr(self.ports, "config", None)
        setter = getattr(port, "set", None)
        if not callable(setter):
            self._say_now("这个安装不允许扩展改设置，请在「设置 → 扩展管理」里调整。")
            return
        value = not bool(self._config_value("ai_agent_enabled", False))
        try:
            setter("ai_agent_enabled", value)
        except Exception:  # noqa: BLE001 - 端口失败只提示
            self._say_now("保存设置失败，详情见日志")
            return
        if not value:
            self._generation += 1
            self.engine.reset()
            self._say_now("Agent 联动已关闭")
            return
        # Do not discover files (possibly network paths) on the GUI thread.
        self._say_now("Agent 联动已开启；后台正在检查事件文件，可在设置中查看来源。")

    # ------------------------------------------------------------ 轮询

    def _loop(self, cancel: threading.Event) -> None:
        while not cancel.is_set():
            try:
                self._drain_once()
            except Exception:  # noqa: BLE001 - 包含首次读取，失败不许打死线程
                self._log("agent link poll failed", exc_info=True)
            if cancel.wait(POLL_SECONDS):
                break

    def _drain_once(self) -> None:
        if self._closed or self._stop.is_set():
            return
        generation = self._generation
        sources = self._current_sources()
        if not self._enabled():
            # 关闭时也推进偏移：重新开启不该回放关闭期间的旧事件。
            for source in sources:
                source.read()
            return
        batch: list[tuple[object, str]] = []
        for source in sources:
            for path, line in source.read():
                event = parse_line(line)
                if event is None:
                    continue
                if not event.agent:
                    key = (
                        agent_key_for(path, default=source.key)
                        if source.per_file_key
                        else source.key
                    )
                    event = replace(event, agent=key)
                batch.append((event, event.writer or "default"))
        if batch:
            self._schedule(0, lambda batch=batch: self._apply(batch), generation=generation)

    def _apply(self, batch) -> None:
        if self._closed or self._stop.is_set() or not self._enabled():
            return
        for event, writer in batch:
            self._log(
                f"agent={event.agent} writer={writer} state={event.state} "
                f"event={event.event} tool={event.tool}",
                level="debug",
            )
            try:
                self.engine.handle(event, writer=writer)
            except Exception:  # noqa: BLE001 - 单条事件失败不影响其余
                self._log("agent link handle failed", exc_info=True)
            self._remember(event)

    def _remember(self, event) -> None:
        self._recent.append(
            {
                "at": round(time.time(), 3),
                "agent": event.agent,
                "state": event.state,
                "tool": event.tool,
                "event": event.event,
            }
        )
        if len(self._recent) > MAX_RECENT:
            del self._recent[:-MAX_RECENT]

    def recent(self) -> list[dict]:
        return list(self._recent)

    # ------------------------------------------------------------ 来源

    def _current_sources(self) -> list[_Source]:
        signature = self._config_signature()
        with self._lock:
            if signature != self._signature:
                self._sources = self._build_sources()
                self._signature = signature
            return self._sources

    def _config_signature(self) -> object:
        return (
            str(self._data_root()),
            bool(self._config_value("ai_agent_dsh_enabled", True)),
            bool(self._config_value("ai_agent_events_enabled", True)),
            tuple(
                (item.get("key"), item.get("path"))
                for item in self._custom_agents()
            ),
        )

    def _build_sources(self) -> list[_Source]:
        sources: list[_Source] = []
        root = self._data_root()
        if bool(self._config_value("ai_agent_dsh_enabled", True)):
            sources.append(
                _Source(
                    key="dsh",
                    reader=GlobTailer(root.parent / "dsh-pet-bridge", "dsh*.jsonl"),
                    per_file_key=True,
                )
            )
        if bool(self._config_value("ai_agent_events_enabled", True)):
            sources.append(
                _Source(
                    key="",
                    reader=GlobTailer(root / "agent-events", "*.jsonl"),
                    per_file_key=True,
                )
            )
        for item in self._custom_agents():
            path = Path(str(item.get("path") or "")).expanduser()
            if not path.name:
                continue
            reader = (
                GlobTailer(path, "*.jsonl")
                if path.is_dir()
                else ByteTailer(path, prime_to_end=True)
            )
            sources.append(_Source(key=str(item.get("key") or ""), reader=reader))
        return sources

    def _custom_agents(self) -> list[dict]:
        raw = self._config_value("ai_agent_custom_agents", [])
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, dict) and item.get("path")]

    def _data_root(self) -> Path:
        storage = getattr(self.ports, "storage", None)
        root = getattr(storage, "data_root", None)
        if callable(root):
            try:
                return Path(root())
            except Exception:  # noqa: BLE001 - 端口异常退回默认
                pass
        data_dir = getattr(storage, "data_dir", None)
        if callable(data_dir):
            try:
                return Path(data_dir(self.context.owner)).parent.parent
            except Exception:
                pass
        return Path.home()

    # ------------------------------------------------------------ 呈现

    def prefs(self) -> LinkPrefs:
        return LinkPrefs(
            animation=bool(self._config_value("ai_agent_animation_enabled", True)),
            activity=bool(self._config_value("ai_agent_activity_bubbles", True)),
            done=bool(self._config_value("ai_agent_done_notice", True)),
            sound=bool(self._config_value("ai_agent_sound_enabled", False)),
            min_interval=float(self._config_value("ai_agent_min_interval_seconds", 2.0)),
            sound_cooldown=float(
                self._config_value("ai_agent_sound_cooldown_seconds", 2.0)
            ),
        )

    def visible(self) -> bool:
        presentation = getattr(self.ports, "presentation", None)
        check = getattr(presentation, "visible", None)
        if not callable(check):
            return True
        try:
            return bool(check())
        except Exception:  # noqa: BLE001
            return True

    def animations(self) -> tuple[str, ...]:
        registry = getattr(self.ports, "pets", None)
        primary = getattr(registry, "primary", None)
        handle = primary() if callable(primary) else None
        lister = getattr(handle, "animations", None)
        if not callable(lister):
            return ()
        try:
            return tuple(lister())
        except Exception:  # noqa: BLE001
            return ()

    def play_animation(self, name: str, *, loop: bool = False) -> None:
        presentation = getattr(self.ports, "presentation", None)
        player = getattr(presentation, "play_animation", None)
        if callable(player):
            self._requested_loop = bool(loop)
            try:
                player(name, loop=loop)
            except TypeError:
                # 旧 Core 的 play_animation 只收 name；本 mod 默认 loop=False。
                player(name)

    def animation_state(self):
        """当前动画状态；端口/窗口查不到（含旧 Core 没有这个方法）返回 ``None``。"""
        presentation = getattr(self.ports, "presentation", None)
        query = getattr(presentation, "animation_state", None)
        if not callable(query):
            return None
        try:
            return query()
        except Exception:  # noqa: BLE001 - 读不到就当未知，别据此重试
            return None

    def reset_idle(self) -> None:
        """解除**自己**持有的循环动画；一次性动作播完会自然回行为链。

        本 mod 默认只请求一次性动作（``loop=False``），所以没有 ``_requested_loop``
        时**绝不**发 ``release_loop()``——``animation_state().loop`` 为真只说明
        *某个*持有者还在循环（唱歌/报时），盲发会把别人的循环一起清掉。
        """
        if not self._requested_loop:
            return
        state = self.animation_state()
        if state is None or not bool(getattr(state, "loop", False)):
            return
        self._requested_loop = False
        presentation = getattr(self.ports, "presentation", None)
        release = getattr(presentation, "release_loop", None)
        if callable(release):
            release()

    def say(self, text: str, *, important: bool = False) -> None:
        presentation = getattr(self.ports, "presentation", None)
        speaker = getattr(presentation, "say", None)
        if not callable(speaker):
            return
        speaker(text, duration_ms=5000, force=bool(important))

    def play_sound(self, kind: str) -> None:
        path = self._sound_path(kind)
        if path is None:
            return
        audio = getattr(self.ports, "audio", None)
        player = getattr(audio, "play", None)
        if not callable(player):
            return
        volume = float(self._config_value("ai_agent_sound_volume", 0.65))
        try:
            player(path, volume)
        except Exception:  # noqa: BLE001
            self._log("agent link sound failed", exc_info=True)

    def _sound_path(self, kind: str) -> Path | None:
        configured = str(self._config_value(f"ai_agent_sound_{kind}_path", "") or "").strip()
        if configured and not configured.startswith("builtin:"):
            return Path(configured).expanduser()
        name = SOUND_FILES.get(kind)
        if not name:
            return None
        resources = getattr(self.ports, "resources", None)
        bundle = getattr(resources, "bundle_path", None)
        if not callable(bundle):
            return None
        try:
            path = Path(bundle("assets", "sounds", "agent", name))
        except Exception:  # noqa: BLE001
            return None
        return path if path.is_file() else None

    # ------------------------------------------------------------ 杂项

    def _say_now(self, text: str) -> None:
        self.say(text, important=True)

    def _enabled(self) -> bool:
        return bool(self._config_value("ai_agent_enabled", False))

    def _config_value(self, key: str, default=None):
        port = getattr(self.ports, "config", None)
        getter = getattr(port, "get", None)
        if not callable(getter):
            return default
        try:
            return getter(key, default)
        except Exception:  # noqa: BLE001
            return default

    def _schedule(self, delay_ms: int, callback, *, generation=None) -> object:
        epoch = self._generation if generation is None else generation

        def guarded():
            if self._closed or self._stop.is_set() or epoch != self._generation:
                return
            if not self._enabled():
                self.engine.reset()
                return
            callback()

        scheduler = getattr(self.ports, "scheduler", None)
        schedule = getattr(scheduler, "call_later", None)
        if not callable(schedule):
            return None
        try:
            return schedule(int(delay_ms), guarded)
        except Exception:  # noqa: BLE001
            return None

    def _cancel(self, handle) -> None:
        if handle is None:
            return
        scheduler = getattr(self.ports, "scheduler", None)
        cancel = getattr(scheduler, "cancel", None)
        if not callable(cancel):
            return
        try:
            cancel(handle)
        except Exception:  # noqa: BLE001
            pass

    def _log(self, message: str, *, exc_info: bool = False, level: str = "info") -> None:
        logger = getattr(self.context, "log", None)
        if callable(logger):
            try:
                logger(message, level="error" if exc_info else level)
                return
            except Exception:  # noqa: BLE001
                pass
        if exc_info:
            import logging

            logging.getLogger("petcore.mods.ai.agent").exception(message)
