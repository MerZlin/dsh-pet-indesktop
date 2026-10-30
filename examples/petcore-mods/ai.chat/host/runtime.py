"""AI 对话扩展的运行时：只做编排，不碰 Qt、不碰网络实现。

依赖面固定为 HostPorts：

- ``ports.chat``       主对话窗口与会话
- ``ports.screen``     整屏截图 / 框选截图
- ``ports.screen_ask`` 截图问答窗口（窗口归 core）
- ``ports.api``        流式对话（传输在 core）
- ``ports.config``     只读配置
- ``ports.scheduler``  把 worker 回调排队回 GUI 线程
- ``ports.presentation`` 气泡
"""

from __future__ import annotations

import base64
import logging
import random
import re
import threading
import time
from pathlib import Path

from petcore.contracts.api import WebSearchOptions
from petcore.contracts.speech import SpeechRequest

from .animation_semantics import choose_animation, choose_thinking
from .constants import LOOK_SCREEN_COOLDOWN_S, MAX_HISTORY, OWNER, QUICK_HISTORY
from .memory import MemoryStore, extract_memory, format_context
from .persona_presets import load_presets, resolve_prompt
from .proactive import PRE_CUE_MS, PRE_CUE_TEXT, ProactiveWatcher
from .prompts import (
    ask_messages,
    chat_messages,
    look_screen_messages,
    ocr_messages,
    translate_messages,
)

logger = logging.getLogger(__name__)

#: 与 core 的稳定分类同名（mod 不许 import core 的网络层，只能照抄字符串）。
#: 没有 ``reason`` 的异常一律归到这里——**不是** ``network``：把 ``KeyError`` 说成
#: 「网络不可达」会让用户去重启路由，而且日志里什么都不留。
REASON_INTERNAL = "internal"
#: 与 ``petcore/net/errors.py`` 的 ``REASON_TEXT`` 同口径：屏幕上不许出现英文 reason。
_REASON_TEXT = {
    "auth": "密钥无效或没有权限",
    "rate_limit": "请求过于频繁，稍后再试",
    "network": "网络不可达",
    "timeout": "连接超时",
    "tls": "证书校验失败",
    "protocol": "服务端返回了无法识别的响应",
    REASON_INTERNAL: "内部错误",
}
NO_SERVICE_HINT = "还没有可用的 API 服务，请先在「设置 → API 与账户」里填写"


class AiChatFailure(RuntimeError):
    """带稳定 ``reason`` 的失败：core 侧的调用方（截图问答/动作回调）只收到一条异常。"""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = str(reason or REASON_INTERNAL)
        self.detail = str(detail or "")[:200]
        text = _REASON_TEXT.get(self.reason, self.reason)
        # core 的异常消息本身就是同一句中文（``NetworkError(NETWORK)`` →
        # "网络不可达"）：再拼一遍就成了「网络不可达：网络不可达」。
        if self.detail and self.detail != text:
            super().__init__(f"{text}：{self.detail}")
        else:
            super().__init__(text)


class AiChatRuntime:
    def __init__(self, context):
        self.context = context
        self.commands = {
            "open": self.open_chat,
            "look_screen": self.look_screen,
            "screen_capture": self.screen_capture,
            "proactive_toggle": self.toggle_proactive,
        }
        self._cancels: dict[int, threading.Event] = {}
        self._quick_cancels: dict[int, threading.Event] = {}
        self._threads: dict[int, threading.Thread] = {}
        self._vision_cancels: dict[threading.Event, threading.Event] = {}
        self._unsubs: list = []
        self._look_busy: set[int] = set()
        self._last_look: dict[int, float] = {}
        self._proactive = ProactiveWatcher(self)
        self._chat_anim_rng = random.Random()
        self._last_chat_anim_at = 0.0
        self._last_chat_anim_name = ""

    # ---------------------------------------------------------------- 配置
    def _config_value(self, key: str, default=None):
        port = getattr(self.context.ports, "config", None)
        if port is None:
            return default
        try:
            return port.get(key, default)
        except Exception:
            return default

    def _api(self):
        return getattr(self.context.ports, "api", None)

    def _chat(self):
        return getattr(self.context.ports, "chat", None)

    def _screen(self):
        return getattr(self.context.ports, "screen", None)

    def _screen_ask(self):
        return getattr(self.context.ports, "screen_ask", None)

    def _quick(self):
        return getattr(self.context.ports, "quick_chat", None)

    def _service_id(self) -> str:
        api = self._api()
        if api is None:
            return ""
        try:
            services = list(api.services())
        except Exception:
            services = []
        if not services:
            return ""
        configured = str(self._config_value("ai_service", "") or "")
        if configured and any(item.service_id == configured for item in services):
            return configured
        return services[0].service_id

    # ------------------------------------------------------------ 失败分类
    def _failure_reason(self, exc: BaseException, service_id: str = "") -> str:
        """异常 → 稳定 reason，并**在这里**记一条失败日志。

        只认异常自己带的 ``reason``；没有就归 ``internal``。以前这里写的是
        ``or "network"``，于是 ``KeyError``（服务 ID 对不上）、``ValueError``、
        ``ssl``/``json`` 解析失败全被显示成「网络不可达」，且日志里一个字都没有。
        只记类型与分类，不记异常文本（可能带 endpoint/密钥片段）；``cause`` 是
        core 包装时留下的底层异常类型（``URLError``/``SSLError``/``ECONNREFUSED``
        这一层），下一次真出问题就靠它定位。
        """
        reason = str(getattr(exc, "reason", "") or "") or REASON_INTERNAL
        self._log_failure(exc, reason, service_id)
        return reason

    @staticmethod
    def _log_failure(exc: BaseException, reason: str, service_id: str) -> None:
        """一条失败只记一行：``_require_service`` 已记过的异常不再重复记。"""
        if getattr(exc, "_ai_failure_logged", False):
            return
        try:
            exc._ai_failure_logged = True
        except Exception:  # noqa: BLE001 - 少数异常不允许写属性，退化成照常记
            pass
        cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
        logger.warning(
            "ai call failed: type=%s reason=%s service=%s cause=%s",
            type(exc).__name__,
            reason,
            service_id or "-",
            type(cause).__name__ if cause is not None else "-",
        )

    def _failure_detail(self, exc: BaseException, service_id: str, reason: str) -> str:
        detail = str(exc)[:200]
        if reason not in ("rate_limit", "auth"):
            return detail
        try:
            metadata = self._api().metadata(service_id)
            base_url = str(getattr(metadata, "base_url", "") or "")
        except Exception:  # noqa: BLE001
            return detail
        if "opencode.ai" in base_url:
            return (
                detail
                + "（该免费模型仅限 OpenCode 客户端；请切换 DeepSeek 或本地模型）"
            )[:300]
        return detail

    def _require_service(self, service_id: str) -> None:
        """发请求前先把「没有服务」说清楚（内部错误），别让它变成一次链路失败。"""
        if service_id:
            return
        failure = AiChatFailure(REASON_INTERNAL, NO_SERVICE_HINT)
        self._log_failure(failure, REASON_INTERNAL, service_id)
        raise failure

    def _character_name(self) -> str:
        host = getattr(self.context, "host", None)
        character_id = str(self._config_value("character", "") or "")
        getter = getattr(host, "character_for", None)
        if callable(getter) and character_id:
            try:
                spec = getter(character_id)
                return str(getattr(spec, "name", "") or character_id)
            except Exception:
                pass
        return character_id or "桌宠"

    def _prompt(self) -> str:
        return resolve_prompt(
            self._config_value("ai_persona", ""),
            self._config_value("ai_system_prompt", ""),
            self._character_name(),
            load_presets(),
        )

    def _vision_enabled(self) -> bool:
        return bool(self._config_value("ai_vision_enabled", True))

    def _temperature(self):
        try:
            return float(self._config_value("ai_temperature", 0.7) or 0.7)
        except (TypeError, ValueError):
            return 0.7

    def _auto_open_after_look(self) -> bool:
        return bool(self._config_value("ai_look_screen_auto_open", False))

    def _thinking(self) -> str:
        mode = str(self._config_value("ai_thinking", "auto") or "auto")
        return mode if mode in {"auto", "enabled", "disabled"} else "auto"

    def _reasoning_effort(self) -> str:
        level = str(self._config_value("ai_reasoning_effort", "high") or "high")
        return level if level in {"low", "high", "max"} else "high"

    def _web_search(self):
        """联网开关关闭时返回 None：core 连一次额外请求都不会发。"""
        if not bool(self._config_value("ai_web_search", False)):
            return None
        engine = str(self._config_value("ai_web_engine", "baidu") or "baidu")
        if engine not in {"baidu", "duckduckgo", "bocha", "tavily"}:
            engine = "baidu"
        try:
            limit = int(self._config_value("ai_web_results", 5) or 5)
        except (TypeError, ValueError):
            limit = 5
        return WebSearchOptions(
            engine=engine,
            max_results=max(1, min(10, limit)),
            fetch_pages=bool(self._config_value("ai_web_fetch", True)),
        )

    # ---------------------------------------------------------------- 线程/宠物
    def _later(self, callback, delay_ms: int = 0) -> None:
        """把回调排到 GUI 线程；scheduler 缺失时退化为直接调用（测试宿主）。"""
        scheduler = getattr(self.context.ports, "scheduler", None)
        if scheduler is None:
            callback()
            return
        try:
            scheduler.call_later(max(0, int(delay_ms)), callback)
        except Exception:
            logging.getLogger(__name__).warning("GUI callback discarded: scheduler unavailable")

    def _active_slot(self) -> int:
        pets = getattr(self.context.ports, "pets", None)
        if pets is None:
            return 0
        try:
            handle = pets.active() or pets.primary()
        except Exception:
            handle = None
        try:
            return int(getattr(handle, "slot", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def _slot(self, slot: int | None) -> int:
        if slot is None:
            return self._active_slot()
        try:
            return max(0, int(slot))
        except (TypeError, ValueError):
            return self._active_slot()

    # ------------------------------------------------------------ 对话动作
    def _presentation(self):
        return getattr(self.context.ports, "presentation", None)

    def _animation_names(self) -> tuple[str, ...]:
        pets = getattr(self.context.ports, "pets", None)
        if pets is None:
            return ()
        try:
            handle = pets.active() or pets.primary()
        except Exception:
            handle = None
        lister = getattr(handle, "animations", None)
        if not callable(lister):
            return ()
        try:
            return tuple(lister())
        except Exception:
            return ()

    def _animation_min_seconds(self) -> float:
        try:
            value = float(self._config_value("ai_animation_min_seconds", 2.0) or 2.0)
        except (TypeError, ValueError):
            value = 2.0
        return max(0.5, min(30.0, value))

    def _play_chat_animation(
        self,
        reply_text: str,
        user_text: str = "",
        *,
        thinking: bool = False,
        force: bool = False,
    ) -> None:
        """回复生成时/完成后播一个语义动作；隐藏、循环、接管态一律不抢。"""
        if not bool(self._config_value("ai_animation_enabled", True)):
            return
        if thinking and not bool(self._config_value("ai_animation_thinking", True)):
            return
        presentation = self._presentation()
        if presentation is None:
            return
        visible = getattr(presentation, "visible", None)
        if callable(visible):
            try:
                if not visible():
                    return
            except Exception:
                return
        state = None
        query = getattr(presentation, "animation_state", None)
        if callable(query):
            try:
                state = query()
            except Exception:
                state = None
        if state is not None and (
            bool(getattr(state, "loop", False))
            or bool(getattr(state, "locked", False))
        ):
            return
        now = time.monotonic()
        if (
            not force
            and not thinking
            and now - self._last_chat_anim_at < self._animation_min_seconds()
        ):
            return
        names = self._animation_names()
        name = (
            choose_thinking(names, rng=self._chat_anim_rng)
            if thinking
            else choose_animation(
                reply_text,
                user_text,
                names,
                rng=self._chat_anim_rng,
                previous=self._last_chat_anim_name,
            )
        )
        if not name:
            return
        player = getattr(presentation, "play_animation", None)
        if not callable(player):
            return
        try:
            played = player(name)
        except Exception:
            return
        if played is False:
            return
        self._last_chat_anim_at = now
        self._last_chat_anim_name = name

    @staticmethod
    def _last_user_text(messages) -> str:
        for item in reversed(list(messages or ())):
            if isinstance(item, dict) and item.get("role") == "user":
                content = item.get("content")
                if isinstance(content, str):
                    return content[:2000]
        return ""

    @staticmethod
    def _speech_text(text: str) -> str:
        value = str(text or "")
        value = re.sub(r"```.*?```", " ", value, flags=re.S)
        value = re.sub(r"`([^`]*)`", r"\1", value)
        value = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", value)
        value = re.sub(r"[*_#>~]+", "", value)
        return " ".join(value.split())[:500]

    def _speak_reply(self, text: str) -> None:
        if not bool(self._config_value("ai_voice_reply", False)):
            return
        speech = getattr(self.context.ports, "speech", None)
        speaker = getattr(speech, "speak", None)
        if not callable(speaker):
            return
        clean = self._speech_text(text)
        if not clean:
            return

        def on_error(error) -> None:
            reason = str(getattr(error, "reason", "") or "speech")
            logger.info("ai voice reply failed: reason=%s", reason)

        try:
            speaker(SpeechRequest(text=clean), on_error=on_error)
        except Exception:  # noqa: BLE001 - 朗读失败不影响文字回答
            logger.info("ai voice reply failed: reason=internal")

    # ---------------------------------------------------------------- 长期记忆
    def _memory_store(self):
        ports = getattr(self.context, "ports", None)
        storage = getattr(ports, "storage", None)
        data_dir = getattr(storage, "data_dir", None)
        if not callable(data_dir):
            return None
        try:
            root = Path(data_dir("ai.chat")) / "memory"
        except Exception:  # noqa: BLE001 - 存储不可用就退化成无记忆
            return None
        try:
            max_entries = int(self._config_value("ai_memory_max_entries", 500) or 500)
        except (TypeError, ValueError):
            max_entries = 500
        return MemoryStore(
            root,
            str(self._config_value("character", "") or "default"),
            max_entries=max_entries,
        )

    def _memory_context(self, text: str) -> str:
        if not bool(self._config_value("ai_memory_enabled", True)):
            return ""
        store = self._memory_store()
        if store is None:
            return ""
        try:
            limit = int(self._config_value("ai_memory_inject", 3) or 3)
        except (TypeError, ValueError):
            limit = 3
        try:
            entries = store.search_all(text, limit=max(1, min(8, limit)))
        except Exception:  # noqa: BLE001 - 坏文件不该让对话失败
            return ""
        return format_context(entries) if entries else ""

    def _remember_text(self, text: str) -> None:
        if not bool(self._config_value("ai_memory_enabled", True)):
            return
        content = extract_memory(text)
        if not content:
            return
        store = self._memory_store()
        if store is None:
            return
        scope = "global" if str(text).strip().startswith(("全局", "永久")) else "character"
        try:
            store.add(content, scope=scope)
        except OSError:
            logger.warning("memory write failed")

    # ---------------------------------------------------------------- 入口
    def open_chat(self, slot: int | None = None) -> None:
        chat = self._chat()
        target = self._slot(slot)
        if chat is None:
            self._say("聊天端口不可用，无法打开 AI 对话")
            return
        if not self._service_id():
            self._say("还没有可用的 API 服务，请先在「设置 → API 与账户」里填写")
            api = self._api()
            opener = getattr(api, "open_settings", None) if api is not None else None
            if callable(opener):
                try:
                    opener()
                except Exception:
                    pass
            return
        chat.open(OWNER, pet_slot=target, title=f"AI 对话 · {self._character_name()}")

    def look_screen(self, slot: int | None = None) -> None:
        """截屏 → 视觉问答 → 气泡答复，并静默同步进该宠的对话记录。"""
        target = self._slot(slot)
        screen = self._screen()
        if screen is None:
            self._say("这个安装没有开放截屏能力")
            return
        if target in self._look_busy:
            self._say("上一张还没看完呢…", duration_ms=3200)
            return
        now = time.monotonic()
        if now - self._last_look.get(target, 0.0) < LOOK_SCREEN_COOLDOWN_S:
            self._say("喘口气嘛，刚看过啦…", duration_ms=3200)
            return
        if not self._service_id():
            self._say("还没有可用的 API 服务，请先在「设置 → API 与账户」里填写")
            return
        if not self._vision_enabled():
            self._say("设置里关掉了视觉模型，先开启再让我看屏幕")
            return
        self._last_look[target] = now
        self._look_busy.add(target)
        self._say("让我看看…", duration_ms=6000)
        self._later(lambda: self._look_capture(target))

    def _look_capture(self, slot: int) -> None:
        screen = self._screen()
        try:
            capture = screen.capture(pet_slot=slot)
        except Exception as exc:  # noqa: BLE001 - 稳定 reason 已在端口里分类
            self._look_failed(slot, getattr(exc, "reason", "") or type(exc).__name__)
            return
        app_info = str(getattr(capture, "app_info", "") or "")
        if not app_info:
            getter = getattr(screen, "foreground_app", None)
            if callable(getter):
                try:
                    app_info = str(getter() or "")
                except Exception:
                    app_info = ""
        def messages():
            # 图像缩放+base64 都是几十毫秒级，放在 _start_vision 的线程里做，
            # 不占 GUI 线程（「看看屏幕」的抓图本身已经在 GUI 线程）。
            image = base64.b64encode(self._vision_bytes(capture.png)).decode("ascii")
            return look_screen_messages(self._prompt(), image, app_info)

        self._start_vision(
            slot, messages, on_done=lambda text: self._look_done(slot, text, app_info)
        )

    def screen_capture(self, slot: int | None = None) -> None:
        """框选区域 → 截图提问窗口；模型调用由 ``_on_ask`` 接管。"""
        target = self._slot(slot)
        screen = self._screen()
        ask = self._screen_ask()
        if screen is None or ask is None:
            self._say("这个安装没有开放截图提问能力")
            return
        try:
            picked = screen.capture_region(
                pet_slot=target,
                ai_available=bool(self._service_id() and self._vision_enabled()),
            )
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "reason", "") == "cancelled":
                return
            self._say(f"截屏失败：{getattr(exc, 'reason', '') or type(exc).__name__}")
            return
        capture = getattr(picked, "capture", picked)
        action = str(getattr(picked, "action", "ask") or "ask")
        if action == "copy":
            self._say("已复制图像", duration_ms=3000)
            return
        if action in ("ocr", "translate"):
            ask.open_action(
                OWNER,
                action,
                capture,
                pet_slot=target,
                text=str(getattr(picked, "text", "") or ""),
            )
            return
        if not self._vision_enabled():
            self._say("设置里关掉了视觉模型，先开启再截图提问")
            return
        if not self._service_id():
            self._say("还没有可用的 API 服务，请先在「设置 → API 与账户」里填写")
            return
        ask.open(OWNER, capture, pet_slot=target, title="截图问答")

    # ---------------------------------------------------------------- 对话
    def _on_submit(self, text: str, slot: int) -> None:
        text = str(text or "").strip()
        if not text:
            return
        chat = self._chat()
        if chat is None:
            return
        if not self._service_id():
            chat.error(OWNER, "no_service", "还没有可用的 API 服务", pet_slot=slot)
            self._say("还没有可用的 API 服务，请先在「设置 → API 与账户」里填写")
            return
        attachments: list[dict] = []
        taker = getattr(chat, "take_attachments", None)
        if callable(taker):
            try:
                attachments = list(taker(OWNER, pet_slot=slot))
            except Exception:
                attachments = []
        try:
            history = chat.history(OWNER, pet_slot=slot) if chat is not None else []
        except Exception:
            history = []
        images = [
            item.get("image_data_url")
            for item in attachments
            if item.get("image_data_url")
        ]
        self._remember_text(text)
        messages = chat_messages(self._prompt(), history, text, images, limit=MAX_HISTORY)
        memory = self._memory_context(text)
        if memory:
            messages.insert(1, {"role": "system", "content": memory})
        self._start(slot, messages)

    def _on_quick_submit(self, text: str, slot: int) -> None:
        """快速对话气泡提交：同一会话、更短历史、同一人格。"""
        quick = self._quick()
        if quick is None:
            return
        text = str(text or "").strip()
        if not text:
            return
        if not self._service_id():
            quick.error(
                OWNER, "no_service", "还没有可用的 API 服务", pet_slot=slot
            )
            return
        try:
            history = quick.history(OWNER, pet_slot=slot)
        except Exception:
            history = []
        self._remember_text(text)
        messages = chat_messages(
            self._prompt(), history, text, [], limit=QUICK_HISTORY
        )
        memory = self._memory_context(text)
        if memory:
            messages.insert(1, {"role": "system", "content": memory})
        self._start_quick(slot, messages)

    def _on_ask(self, question: str, image_png: bytes, history: list[dict], slot: int) -> str:
        """截图提问窗口的回调（在 core 的后台线程里执行，同步返回整段答复）。"""
        api = self._api()
        service_id = self._service_id()
        try:
            if api is None:
                raise AiChatFailure(REASON_INTERNAL, NO_SERVICE_HINT)
            self._require_service(service_id)
            image_url = (
                "data:image/png;base64,"
                + base64.b64encode(self._vision_bytes(image_png)).decode("ascii")
            )
            messages = ask_messages(
                self._prompt(), history, question, image_url, limit=MAX_HISTORY
            )
            result = api.stream_chat(
                service_id,
                messages,
                model=str(self._config_value("ai_model", "") or ""),
                temperature=self._temperature(),
                thinking=self._thinking(),
                reasoning_effort=self._reasoning_effort(),
            )
        except AiChatFailure:
            raise
        except Exception as exc:  # noqa: BLE001 - 分类后交给 core 显示
            reason = self._failure_reason(exc, service_id)
            raise AiChatFailure(reason, str(exc)[:200]) from exc
        if getattr(result, "cancelled", False):
            return ""
        return str(getattr(result, "text", "") or "").strip()

    def _on_action(self, action: str, source_text: str, png: bytes, slot: int) -> str:
        """提取文字 / 翻译的 AI 回退（core 后台线程调用，同步返回整段文本）。"""
        action = str(action or "")
        if action not in ("ocr", "translate"):
            return ""
        api = self._api()
        service_id = self._service_id()
        try:
            if api is None:
                raise AiChatFailure(REASON_INTERNAL, NO_SERVICE_HINT)
            self._require_service(service_id)
            image_url = (
                "data:image/png;base64,"
                + base64.b64encode(self._vision_bytes(png)).decode("ascii")
            )
            if action == "ocr":
                messages = ocr_messages(self._prompt(), image_url)
            else:
                messages = translate_messages(self._prompt(), source_text, image_url)
            result = api.stream_chat(
                service_id,
                messages,
                model=str(self._config_value("ai_model", "") or ""),
                temperature=self._temperature(),
                thinking=self._thinking(),
                reasoning_effort=self._reasoning_effort(),
            )
        except AiChatFailure:
            raise
        except Exception as exc:  # noqa: BLE001 - 分类后交给 core 显示
            reason = self._failure_reason(exc, service_id)
            raise AiChatFailure(reason, str(exc)[:200]) from exc
        if getattr(result, "cancelled", False):
            return ""
        return str(getattr(result, "text", "") or "").strip()

    def _on_stop(self, slot: int) -> None:
        cancel = self._cancels.get(int(slot))
        if cancel is not None:
            cancel.set()

    def _request_callback(self, requests, slot, cancel, callback, *, final=False) -> None:
        def deliver():
            if requests.get(slot) is not cancel:
                return
            try:
                if final or not cancel.is_set():
                    callback()
            finally:
                if final and requests.get(slot) is cancel:
                    requests.pop(slot, None)

        self._later(deliver)

    def _start_quick(self, slot: int, messages) -> None:
        quick = self._quick()
        api = self._api()
        if quick is None or api is None:
            return
        previous = self._quick_cancels.get(slot)
        if previous is not None:
            previous.set()
        cancel = threading.Event()
        self._quick_cancels[slot] = cancel
        service_id = self._service_id()
        model = str(self._config_value("ai_model", "") or "")
        temperature = self._temperature()
        user_text = self._last_user_text(messages)

        def run() -> None:
            partial: list[str] = []
            first_token = [False]

            def delta(value: str) -> None:
                partial.append(value)
                if not first_token[0]:
                    first_token[0] = True
                    self._later(
                        lambda: self._play_chat_animation(
                            "", user_text, thinking=True
                        )
                    )
                self._request_callback(self._quick_cancels, slot, cancel,
                    lambda: quick.token(OWNER, value, pet_slot=slot))

            def reasoning(value: str) -> None:
                self._request_callback(self._quick_cancels, slot, cancel,
                    lambda: quick.reasoning(OWNER, value, pet_slot=slot))

            def status(line: str) -> None:
                self._request_callback(self._quick_cancels, slot, cancel,
                    lambda: quick.status(OWNER, line, pet_slot=slot))

            try:
                self._require_service(service_id)
                result = api.stream_chat(
                    service_id,
                    messages,
                    model=model,
                    temperature=temperature,
                    on_delta=delta,
                    on_reasoning=reasoning,
                    on_status=status,
                    thinking=self._thinking(),
                    reasoning_effort=self._reasoning_effort(),
                    web_search=self._web_search(),
                    cancel=cancel,
                )
            except Exception as exc:  # noqa: BLE001
                reason = self._failure_reason(exc, service_id)
                detail = self._failure_detail(exc, service_id, reason)

                def failed():
                    if cancel.is_set():
                        quick.done(OWNER, "".join(partial), pet_slot=slot)
                    elif partial:
                        # 已经吐出的正文比错误卡重要：收尾成半条回答，别把它抹掉。
                        final = (
                            "".join(partial)
                            + f"\n\n（{detail}：这次回答没有正常结束，已保留前面的内容。）"
                        )
                        quick.done(OWNER, final, pet_slot=slot)
                    else:
                        quick.error(OWNER, reason, detail, pet_slot=slot)

                self._request_callback(self._quick_cancels, slot, cancel, failed, final=True)
            else:
                final = "".join(partial) if cancel.is_set() or getattr(result, "cancelled", False) else str(getattr(result, "text", "") or "".join(partial))

                def finished():
                    quick.done(OWNER, final, pet_slot=slot)
                    if not cancel.is_set():
                        self._play_chat_animation(final, user_text, force=True)
                        self._speak_reply(final)

                self._request_callback(self._quick_cancels, slot, cancel,
                    finished, final=True)

        threading.Thread(target=run, name=f"ai-quick-{slot}", daemon=True).start()

    def _start(self, slot: int, messages) -> None:
        chat = self._chat()
        api = self._api()
        if chat is None or api is None:
            return
        previous = self._cancels.get(slot)
        if previous is not None:
            previous.set()
        cancel = threading.Event()
        self._cancels[slot] = cancel
        service_id = self._service_id()
        model = str(self._config_value("ai_model", "") or "")
        temperature = self._temperature()
        user_text = self._last_user_text(messages)

        def run() -> None:
            partial: list[str] = []
            first_token = [False]

            def delta(value: str) -> None:
                partial.append(value)
                if not first_token[0]:
                    first_token[0] = True
                    self._later(
                        lambda: self._play_chat_animation(
                            "", user_text, thinking=True
                        )
                    )
                self._request_callback(self._cancels, slot, cancel,
                    lambda: chat.token(OWNER, value, pet_slot=slot))

            def reasoning(value: str) -> None:
                self._request_callback(self._cancels, slot, cancel,
                    lambda: chat.reasoning(OWNER, value, pet_slot=slot))

            def status(line: str) -> None:
                self._request_callback(self._cancels, slot, cancel,
                    lambda: chat.status(OWNER, line, pet_slot=slot))

            try:
                self._require_service(service_id)
                result = api.stream_chat(
                    service_id,
                    messages,
                    model=model,
                    temperature=temperature,
                    on_delta=delta,
                    on_reasoning=reasoning,
                    on_status=status,
                    thinking=self._thinking(),
                    reasoning_effort=self._reasoning_effort(),
                    web_search=self._web_search(),
                    cancel=cancel,
                )
            except Exception as exc:  # noqa: BLE001
                reason = self._failure_reason(exc, service_id)
                detail = self._failure_detail(exc, service_id, reason)

                def failed():
                    if cancel.is_set():
                        chat.done(OWNER, "".join(partial), pet_slot=slot)
                    elif partial:
                        # 已经吐出的正文比错误卡重要：收尾成半条回答，别把它抹掉。
                        final = (
                            "".join(partial)
                            + f"\n\n（{detail}：这次回答没有正常结束，已保留前面的内容。）"
                        )
                        chat.done(OWNER, final, pet_slot=slot)
                    else:
                        chat.error(OWNER, reason, detail, pet_slot=slot)
                        self._say(f"AI 请求失败：{detail}")

                self._request_callback(self._cancels, slot, cancel, failed, final=True)
            else:
                final = "".join(partial) if cancel.is_set() or getattr(result, "cancelled", False) else str(getattr(result, "text", "") or "".join(partial))
                if getattr(result, "incomplete", False) and final:
                    # 半条回答必须让用户看得见（AUD-15）：否则下次翻会话会以为这就是全部。
                    final += "\n\n（这次回答没有正常结束，内容可能不完整。请重试或重新提问。）"

                def finished():
                    chat.done(OWNER, final, pet_slot=slot)
                    if not cancel.is_set():
                        self._play_chat_animation(final, user_text, force=True)
                        self._speak_reply(final)

                self._request_callback(self._cancels, slot, cancel,
                    finished, final=True)

        thread = threading.Thread(target=run, name=f"ai-chat-{slot}", daemon=True)
        self._threads[slot] = thread
        thread.start()

    def _start_vision(self, slot: int, messages, *, on_done, on_fail=None) -> None:
        fail = on_fail or (lambda reason: self._look_failed(slot, reason))
        api = self._api()
        if api is None:
            self._later(lambda: fail("no_api"))
            return
        service_id = self._service_id()
        model = str(self._config_value("ai_model", "") or "")
        temperature = self._temperature()

        cancel = threading.Event()
        self._vision_cancels[cancel] = cancel

        def publish(callback):
            self._request_callback(self._vision_cancels, cancel, cancel, callback, final=True)

        def run() -> None:
            try:
                self._require_service(service_id)
                payload = messages() if callable(messages) else messages
                result = api.stream_chat(
                    service_id,
                    payload,
                    model=model,
                    temperature=temperature,
                    thinking=self._thinking(),
                    reasoning_effort=self._reasoning_effort(),
                    cancel=cancel,
                )
            except Exception as exc:  # noqa: BLE001
                reason = self._failure_reason(exc, service_id)
                publish(lambda: fail(reason))
                return
            text = str(getattr(result, "text", "") or "").strip()
            if not text:
                publish(lambda: fail("empty_reply"))
                return
            if getattr(result, "incomplete", False):
                text += "\n\n（这次回答没有正常结束，内容可能不完整。）"
            publish(lambda: on_done(text))

        threading.Thread(target=run, name=f"ai-look-{slot}", daemon=True).start()

    def _vision_bytes(self, png: bytes) -> bytes:
        """模型输入按物理像素封顶（Core 缩放），本地 OCR/复制仍用原图。"""
        screen = self._screen()
        shrink = getattr(screen, "shrink_for_vision", None)
        if not callable(shrink):
            return bytes(png or b"")
        try:
            return bytes(shrink(bytes(png or b"")))
        except Exception:
            return bytes(png or b"")

    # ---------------------------------------------------------------- 主动识屏
    def toggle_proactive(self, slot: int | None = None) -> None:
        """菜单「主动识屏」：切换开关并立即落盘，气泡回报当前状态。"""
        port = getattr(self.context.ports, "config", None)
        setter = getattr(port, "set", None)
        if not callable(setter):
            self._say("这个安装不允许扩展改设置，请在「设置 → 扩展管理」里调整。")
            return
        value = not bool(self._config_value("ai_proactive_enabled", False))
        try:
            setter("ai_proactive_enabled", value)
        except Exception:
            self._say("保存设置失败，详情见日志")
            return
        if not value:
            self._say("主动识屏已关闭")
            return
        rules = self._config_value("ai_proactive_whitelist", []) or []
        if not [rule for rule in rules if str(rule).strip()]:
            self._say("主动识屏已开启；白名单还是空的，先到设置里添加要看的软件。")
        else:
            self._say(f"主动识屏已开启（白名单 {len(rules)} 条）")

    def proactive_cue(self) -> None:
        self._say(PRE_CUE_TEXT, duration_ms=PRE_CUE_MS)

    def proactive_ask(self, capture, app_info: str, *, on_done, on_fail) -> None:
        """主动识屏的模型调用：图片只在这一刻拼进消息，不落盘。"""
        def messages():
            image = base64.b64encode(self._vision_bytes(capture.png)).decode("ascii")
            return look_screen_messages(self._prompt(), image, app_info)

        self._start_vision(
            self._active_slot(),
            messages,
            on_done=on_done,
            on_fail=on_fail,
        )

    def proactive_answer(self, text: str, *, app_info: str, sync_chat: bool) -> None:
        duration = max(6000, min(20000, 4000 + len(text) * 150))
        self._say(text, duration_ms=duration)
        if sync_chat:
            self._record_look(self._active_slot(), app_info, text)

    def _record_look(self, slot: int, app_info: str, text: str) -> None:
        """静默写进该宠的会话；不触发开窗（``ChatPort.record`` 的契约）。"""
        chat = self._chat()
        record = getattr(chat, "record", None) if chat is not None else None
        if not callable(record):
            return
        try:
            record(
                OWNER,
                "user",
                f"（看看屏幕）{app_info}" if app_info else "（看看屏幕）",
                pet_slot=slot,
            )
            record(OWNER, "assistant", text, pet_slot=slot)
        except Exception:
            pass

    # ---------------------------------------------------------------- 看看屏幕收尾
    def _look_done(self, slot: int, text: str, app_info: str) -> None:
        self._look_busy.discard(slot)
        self._say(text, duration_ms=max(4000, min(12000, len(text) * 150)))
        self._record_look(slot, app_info, text)
        if self._auto_open_after_look():
            self.open_chat(slot)

    def _look_failed(self, slot: int, reason: str) -> None:
        self._look_busy.discard(slot)
        message = "看不清啊…"
        if reason and reason != "empty_reply":
            text = str(reason)[:60]
            message += _REASON_TEXT.get(text, text)
        self._say(message, duration_ms=5000)

    # ---------------------------------------------------------------- 生命周期
    def start(self) -> None:
        chat = self._chat()
        ask = self._screen_ask()
        self._unsubs = []
        if chat is not None:
            self._unsubs.append(chat.on_submit(OWNER, self._on_submit))
            self._unsubs.append(chat.on_stop(OWNER, self._on_stop))
        quick = self._quick()
        if quick is not None:
            self._unsubs.append(quick.on_submit(OWNER, self._on_quick_submit))
        if ask is not None:
            self._unsubs.append(ask.on_ask(OWNER, self._on_ask))
            register = getattr(ask, "on_action", None)
            if callable(register):
                self._unsubs.append(
                    register(
                        OWNER,
                        self._on_action,
                        priority=100,
                        actions=("ocr", "translate"),
                    )
                )
        self._proactive.start()

    def stop(self) -> None:
        self._proactive.stop()
        for cancel in self._vision_cancels:
            cancel.set()
        self._vision_cancels.clear()
        for cancel in list(self._cancels.values()):
            cancel.set()
        self._cancels.clear()
        for cancel in list(self._quick_cancels.values()):
            cancel.set()
        self._quick_cancels.clear()
        for unsubscribe in self._unsubs:
            try:
                unsubscribe()
            except Exception:
                pass
        self._unsubs = []

    def _say(self, text: str, *, duration_ms: int = 6000) -> None:
        presentation = getattr(self.context.ports, "presentation", None)
        if presentation is None:
            return

        def speak() -> None:
            try:
                presentation.say(text, duration_ms=duration_ms)
            except Exception:
                pass

        self._later(speak)
