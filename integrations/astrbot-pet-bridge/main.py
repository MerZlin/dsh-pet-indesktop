# -*- coding: utf-8 -*-
"""AstrBot → dsh-pet Agent 联动桥接插件。

把 AstrBot 的「收到需要 LLM 处理的消息 → 调工具 → 回复发出」这一过程折叠成
桌宠统一协议的六态（thinking / working / error / idle），追加写到一个本地
JSONL 文件，由桌宠的 ``AstrBotMonitor`` 只读 tail。

数据边界（docs/AGENT_LINK_PROTOCOL.md §6）：每行只有 ``ts`` / ``agent`` /
``inst`` / ``state``，working 时附短工具名 ``tool``。**不写**消息正文、提示词、
会话/用户/群号（unified_msg_origin）、工具参数与结果。不发任何网络请求；写失败
静默放弃，绝不影响 AstrBot 本体。

事件文件位置（桌宠安装时按同一规则计算，两侧必须一致）::

    <AstrBot data>/plugin_data/astrbot_plugin_dsh_pet_bridge/astrbot.jsonl

``<AstrBot data>`` 取本插件所在 ``data/plugins/<本插件>/`` 的上两级，因此
Docker 部署只要把 data 目录挂载到宿主机，桌宠就能在宿主机上读到同一个文件。
环境变量 ``DSH_PET_ASTRBOT_EVENTS`` 可整体覆盖该路径。
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

PLUGIN_NAME = "astrbot_plugin_dsh_pet_bridge"
EVENTS_FILE_NAME = "astrbot.jsonl"
AGENT_KEY = "astrbot"
#: 事件文件轮转阈值（协议 §2.5：约 1MB 轮转，只留一代）
ROTATE_BYTES = 1024 * 1024
#: 回合静默上限：一个回合超过这么久没有任何钩子活动（思考 / 工具调用）就按
#: 「已结束」回收。AstrBot 4.29.4 实测有一条路径**没有任何收尾钩子**：流式回复
#: （WebChat 等）下 LLM 请求失败——``on_agent_done`` 不触发，RespondStage 走流式
#: 分支提前 return，``after_message_sent`` 也不触发。没有这道兜底，桌宠会一直忙。
TURN_IDLE_TTL_S = 180.0
#: 同时在途回合上限（群聊洪泛时防止内存无界增长；超出淘汰最旧的）。
MAX_TURNS = 256
_VALID_STATES = {"thinking", "working", "attention", "error", "idle", "sleeping"}


def default_events_path(plugin_file: str | os.PathLike | None = None) -> Path:
    """事件文件路径：环境变量覆盖，否则 ``<data>/plugin_data/<插件名>/astrbot.jsonl``。"""
    override = os.environ.get("DSH_PET_ASTRBOT_EVENTS", "").strip()
    if override:
        return Path(override).expanduser()
    # 不 resolve()：data/plugins 可能是软链，桌宠按「安装目录」计算的是未解析路径
    here = Path(os.path.abspath(str(plugin_file or __file__)))
    data_dir = here.parent.parent.parent
    return data_dir / "plugin_data" / PLUGIN_NAME / EVENTS_FILE_NAME


class PetBridge:
    """纯逻辑：在途回合折叠 + 去重追加写。与 AstrBot API 无关，便于单测。"""

    def __init__(self, events_path: Path, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.events_path = Path(events_path)
        self._clock = clock
        self._lock = threading.RLock()
        self._inst = uuid.uuid4().hex
        # turn key → {"seen": 最近一次钩子活动时刻, "tools": 在途工具数}
        self._turns: dict[Any, dict[str, Any]] = {}
        self._last_emitted: tuple[str, str] | None = None

    # ------------------------------------------------------------ 写入
    def _append(self, state: str, tool: str = "") -> None:
        state = state if state in _VALID_STATES else "idle"
        tool = str(tool or "").strip().lower()[:48] if state == "working" else ""
        fingerprint = (state, tool)
        with self._lock:
            # error 是一次性提醒，不参与去重；其余连续相同状态不重复落盘（协议 §2.5）
            if state != "error" and fingerprint == self._last_emitted:
                return
            record: dict[str, Any] = {
                "ts": time.time(), "agent": AGENT_KEY, "inst": self._inst, "state": state,
            }
            if tool:
                record["tool"] = tool
            payload = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            try:
                path = self.events_path
                path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    if path.stat().st_size >= ROTATE_BYTES:
                        os.replace(path, path.with_name(path.name + ".1"))
                except FileNotFoundError:
                    pass
                fd = os.open(str(path), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
                try:
                    os.write(fd, payload)
                finally:
                    os.close(fd)
            except Exception:
                return
            self._last_emitted = fingerprint

    def _aggregate(self) -> str:
        if any(t["tools"] > 0 for t in self._turns.values()):
            return "working"
        return "thinking" if self._turns else "idle"

    def _emit_aggregate(self, tool: str = "") -> None:
        state = self._aggregate()
        self._append(state, tool if state == "working" else "")

    def _sweep_locked(self) -> bool:
        now = self._clock()
        stale = [key for key, t in self._turns.items() if now - t["seen"] >= TURN_IDLE_TTL_S]
        for key in stale:
            self._turns.pop(key, None)
        return bool(stale)

    def _ensure_turn(self, key: Any) -> dict[str, Any]:
        turn = self._turns.get(key)
        if turn is None:
            while len(self._turns) >= MAX_TURNS:
                self._turns.pop(next(iter(self._turns)))
            turn = {"seen": 0.0, "tools": 0}
            self._turns[key] = turn
        turn["seen"] = self._clock()
        return turn

    # ------------------------------------------------------------ 生命周期
    def reset(self) -> None:
        """插件加载/卸载：清空在途回合并写一条 idle（收口上次异常退出留下的忙碌态）。"""
        with self._lock:
            self._turns.clear()
            # 本实例刚加载时 _last_emitted 为 None，必写一条 idle；卸载时已是 idle 则去重
            self._append("idle")

    def sweep(self) -> None:
        with self._lock:
            if self._sweep_locked():
                self._emit_aggregate()

    def begin_turn(self, key: Any) -> None:
        with self._lock:
            self._sweep_locked()
            self._ensure_turn(key)
            self._emit_aggregate()

    def tool_start(self, key: Any, tool_name: str) -> None:
        with self._lock:
            self._sweep_locked()
            self._ensure_turn(key)["tools"] += 1
            self._emit_aggregate(tool_name)

    def tool_end(self, key: Any, *, failed: bool) -> None:
        with self._lock:
            turn = self._turns.get(key)
            if turn is not None:
                turn["tools"] = max(0, turn["tools"] - 1)
                turn["seen"] = self._clock()
            if failed:
                self._append("error")
            self._emit_aggregate()

    def llm_response(self, key: Any, *, failed: bool) -> None:
        """最终 LLM 响应（AstrBot 的 ``on_agent_done``）= 回合结束。

        流式与非流式都会走到这里；``after_message_sent`` 在流式分支不触发，
        不能当收尾信号（见 ``TURN_IDLE_TTL_S``）。
        """
        with self._lock:
            known = self._turns.pop(key, None) is not None
            if failed:
                self._append("error")
            elif not known:
                return
            self._sweep_locked()
            self._emit_aggregate()

    def message_sent(self, key: Any) -> None:
        """兜底收尾：没有 ``on_llm_response`` 的路径（第三方 Agent 执行器、非流式
        LLM 报错后把错误文案发给用户）。纯指令回复没有 begin_turn，不产生任何事件。"""
        with self._lock:
            if self._turns.pop(key, None) is None:
                return
            self._sweep_locked()
            self._emit_aggregate()

    def in_flight(self) -> int:
        with self._lock:
            return len(self._turns)


def _tool_name(tool: Any) -> str:
    return str(getattr(tool, "name", "") or "")


def _tool_failed(result: Any) -> bool:
    return bool(getattr(result, "isError", False) or getattr(result, "is_error", False))


def _response_failed(resp: Any) -> bool:
    return str(getattr(resp, "role", "") or "").lower() == "err"


# ---------------------------------------------------------------- AstrBot 插件
try:  # AstrBot 运行时才有；单测/离线导入时只用上面的纯逻辑
    from astrbot.api.event import AstrMessageEvent, filter
    from astrbot.api.star import Context, Star
except Exception:  # pragma: no cover - 非 AstrBot 环境
    AstrMessageEvent = Any  # type: ignore[assignment,misc]
    filter = None  # type: ignore[assignment]
    Context = Any  # type: ignore[assignment,misc]
    Star = None  # type: ignore[assignment]

try:
    from astrbot.api.star import register as _register
except Exception:  # pragma: no cover - 新版仅靠 metadata.yaml
    _register = None


def _hook(name: str):
    """取 ``filter.<name>()`` 装饰器；旧版 AstrBot 没有该钩子时原样返回函数（不注册）。"""
    factory = getattr(filter, name, None) if filter is not None else None
    if factory is None:
        return lambda fn: fn
    return factory()


if Star is not None:

    class DshPetBridge(Star):
        """把 AstrBot 的处理状态同步给 dsh-pet 桌宠（只写本地文件）。"""

        def __init__(self, context: Context) -> None:
            super().__init__(context)
            self._bridge = PetBridge(default_events_path(__file__))
            self._sweep_handle = None
            self._bridge.reset()

        def _schedule_sweep(self, delay: float) -> None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            if self._sweep_handle is not None:
                self._sweep_handle.cancel()
            self._sweep_handle = loop.call_later(delay, self._bridge.sweep)

        @_hook("on_llm_request")
        async def dsh_pet_on_llm_request(self, event: AstrMessageEvent, req: Any = None):
            self._bridge.begin_turn(id(event))
            self._schedule_sweep(TURN_IDLE_TTL_S + 1.0)

        @_hook("on_using_llm_tool")
        async def dsh_pet_on_using_llm_tool(self, event: AstrMessageEvent, tool: Any = None,
                                            tool_args: Any = None):
            self._bridge.tool_start(id(event), _tool_name(tool))
            self._schedule_sweep(TURN_IDLE_TTL_S + 1.0)

        @_hook("on_llm_tool_respond")
        async def dsh_pet_on_llm_tool_respond(self, event: AstrMessageEvent, tool: Any = None,
                                              tool_args: Any = None, tool_result: Any = None):
            self._bridge.tool_end(id(event), failed=_tool_failed(tool_result))
            self._schedule_sweep(TURN_IDLE_TTL_S + 1.0)

        @_hook("on_llm_response")
        async def dsh_pet_on_llm_response(self, event: AstrMessageEvent, resp: Any = None):
            self._bridge.llm_response(id(event), failed=_response_failed(resp))

        @_hook("after_message_sent")
        async def dsh_pet_after_message_sent(self, event: AstrMessageEvent):
            self._bridge.message_sent(id(event))

        async def terminate(self):
            if self._sweep_handle is not None:
                self._sweep_handle.cancel()
                self._sweep_handle = None
            self._bridge.reset()

    if _register is not None:
        try:
            DshPetBridge = _register(  # type: ignore[misc]
                PLUGIN_NAME, "dsh-pet-indesktop",
                "把 AstrBot 的处理状态同步给 dsh-pet 桌宠（只写本地文件）", "v1.0.0",
                "https://github.com/imal1/dsh-pet-indesktop",
            )(DshPetBridge)
        except Exception:  # pragma: no cover - 装饰器签名变化时退回 metadata.yaml
            pass
