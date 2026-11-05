"""QQ OneBot v11 适配：HTTP 事件入站 + HTTP API 出站。

不 import 任何网络库：入站 socket 由 Core 的 WebhookPort 持有，出站只经
HostPorts.http。运行时零 Qt；所有 UI 调用经 scheduler 回 GUI 线程。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import threading
import time

from petcore.contracts.http import HttpRequest

logger = logging.getLogger("petcore.im.qq")

OWNER = "im.qq"
_CQ_RE = re.compile(r"\[CQ:[^\]]+\]")
_MAX_SEEN = 500
_MAX_REPLY_THREADS = 4


def _now() -> float:
    return time.time()


class QqRuntime:
    def __init__(self, context) -> None:
        self.context = context
        self.ports = getattr(context, "ports", None)
        self._server = None
        self._unregister = None
        self._closed = False
        self._seen: dict[str, float] = {}
        self._replying: set[str] = set()
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- 生命周期

    def start(self) -> None:
        self._closed = False
        if not self._get("im_qq_enabled", False):
            return
        webhook = getattr(self.ports, "webhook", None)
        credentials = getattr(self.ports, "credentials", None)
        if webhook is None or credentials is None:
            self._notify("QQ 互通不可用：缺少 webhook/凭据端口")
            return
        webhook_token = str(credentials.get(OWNER, "webhook_token") or "")
        if not webhook_token:
            webhook_token = secrets.token_hex(16)
            try:
                credentials.set(OWNER, "webhook_token", webhook_token)
                logger.info("qq webhook token generated")
            except Exception:
                logger.warning("qq webhook token save failed")
            if not self._credential_persisted(credentials, "webhook_token"):
                logger.warning(
                    "qq webhook token generated but not persisted; it will change after restart"
                )
        try:
            self._server = webhook.start(
                OWNER,
                port=int(self._get("im_qq_webhook_port", 8790) or 8790),
                path=str(self._get("im_qq_webhook_path", "/onebot") or "/onebot"),
                token=webhook_token,
                handler=self._on_webhook,
            )
        except Exception as exc:
            self._notify(f"QQ 互通启动失败：{type(exc).__name__}")
            return
        chat = getattr(self.ports, "chat", None)
        register = getattr(chat, "register_channel", None)
        if callable(register):
            try:
                self._unregister = register("qq", self._on_submit)
            except Exception:
                logger.debug("qq channel register failed", exc_info=True)
        api_base = self._api_base()
        if not api_base:
            self._notify("QQ 互通已监听；尚未填写 OneBot HTTP API 地址")
        elif not self._access_token():
            self._notify("QQ 互通已监听；尚未填写 OneBot access token（如网关未启用鉴权可留空）")

    def stop(self) -> None:
        self._closed = True
        if self._unregister is not None:
            try:
                self._unregister()
            except Exception:
                pass
            self._unregister = None
        server = self._server
        self._server = None
        if server is not None:
            try:
                server.stop()
            except Exception:
                pass
        with self._lock:
            self._replying.clear()

    # ---------------------------------------------------------------- 配置

    def _get(self, key: str, default=None):
        port = getattr(self.ports, "config", None)
        getter = getattr(port, "get", None)
        if not callable(getter):
            return default
        try:
            value = getter(key, default)
        except Exception:
            return default
        return default if value is None else value

    def _list(self, key: str) -> set[str]:
        value = self._get(key, [])
        if isinstance(value, str):
            items = re.split(r"[,，\s]+", value)
        elif isinstance(value, (list, tuple, set)):
            items = [str(item) for item in value]
        else:
            items = []
        return {item.strip() for item in items if item.strip()}

    def _api_base(self) -> str:
        return str(self._get("im_qq_api_base", "") or "").strip().rstrip("/")

    def _access_token(self) -> str:
        credentials = getattr(self.ports, "credentials", None)
        if credentials is None:
            return ""
        try:
            return str(credentials.get(OWNER, "access_token") or "")
        except Exception:
            return ""

    @staticmethod
    def _credential_persisted(credentials, key: str) -> bool:
        """回读校验；没有 persisted() 的替身按已持久化处理。"""
        checker = getattr(credentials, "persisted", None)
        if not callable(checker):
            return True
        try:
            return bool(checker(OWNER, key))
        except Exception:  # noqa: BLE001 - 回读失败等于没持久化
            return False

    # ---------------------------------------------------------------- 入站

    def _on_webhook(self, request: dict) -> dict:
        if self._closed:
            return {"ok": False, "error": "closed"}
        body = request.get("body") if isinstance(request, dict) else None
        if not isinstance(body, dict):
            return {"ok": False, "error": "bad_body"}
        post_type = str(body.get("post_type") or "")
        if post_type not in {"message", "message_sent"}:
            return {"ok": True, "ignored": True}
        scheduler = getattr(self.ports, "scheduler", None)
        if scheduler is not None:
            scheduler.call_later(0, lambda event=body: self._handle_event(event))
        else:
            self._handle_event(body)
        return {"ok": True}

    def _handle_event(self, event: dict) -> None:
        if self._closed:
            return
        if str(event.get("post_type") or "") == "message_sent":
            return
        self_id = str(event.get("self_id") or "")
        user_id = str(event.get("user_id") or "")
        if self_id and user_id and self_id == user_id:
            return
        message_type = str(event.get("message_type") or "")
        group_id = str(event.get("group_id") or "")
        if message_type not in {"private", "group"}:
            return
        if message_type == "group":
            allowed = self._list("im_qq_allowed_groups")
            if not allowed or group_id not in allowed:
                return
            conversation = f"group:{group_id}"
        else:
            allowed = self._list("im_qq_allowed_users")
            if not allowed or user_id not in allowed:
                return
            conversation = f"private:{user_id}"
        text = self._message_text(event)
        if not text:
            return
        message_id = str(event.get("message_id") or "")
        key = message_id or hashlib.sha1(
            f"{conversation}|{user_id}|{text}|{event.get('time')}".encode("utf-8")
        ).hexdigest()[:20]
        with self._lock:
            if key in self._seen:
                return
            self._seen[key] = _now()
            if len(self._seen) > _MAX_SEEN:
                for old in sorted(self._seen, key=self._seen.get)[:100]:
                    self._seen.pop(old, None)
        sender = event.get("sender")
        sender_name = ""
        if isinstance(sender, dict):
            sender_name = str(sender.get("card") or sender.get("nickname") or "")
        sender_name = sender_name or user_id or "QQ"
        chat = getattr(self.ports, "chat", None)
        ingest = getattr(chat, "ingest", None)
        if callable(ingest):
            try:
                ingest(
                    {
                        "channel": "qq",
                        "conversation_id": conversation,
                        "role": "user",
                        "text": text,
                        "sender": sender_name,
                        "sender_id": user_id,
                        "external_id": key,
                        "ts": float(event.get("time") or _now()),
                    },
                    owner=OWNER,
                )
            except Exception:
                logger.debug("qq ingest failed", exc_info=True)
        if bool(self._get("im_qq_notify_bubble", True)):
            self._notify(f"【QQ】{sender_name}：{text[:120]}", force=True)
        mode = str(self._get("im_qq_reply_mode", "notify") or "notify")
        if mode == "ai":
            self._reply_ai(conversation, text)

    def _message_text(self, event: dict) -> str:
        segments = event.get("message")
        parts: list[str] = []
        if isinstance(segments, list):
            for segment in segments:
                if not isinstance(segment, dict):
                    continue
                kind = str(segment.get("type") or "")
                data = segment.get("data") if isinstance(segment.get("data"), dict) else {}
                if kind == "text":
                    parts.append(str(data.get("text") or ""))
                elif kind == "at":
                    parts.append(f"@{data.get('qq') or ''}")
                elif kind == "image":
                    parts.append("[图片]")
                elif kind == "face":
                    parts.append("[表情]")
                elif kind == "record":
                    parts.append("[语音]")
                elif kind == "reply":
                    parts.append("[引用]")
        text = "".join(parts).strip() or str(event.get("raw_message") or "")
        text = _CQ_RE.sub("", text).strip()
        return text[:4000]

    # ---------------------------------------------------------------- 出站

    def _on_submit(self, text: str, slot: int, conversation: str) -> None:
        text = str(text or "").strip()
        if not text:
            return
        threading.Thread(
            target=self._send_qq,
            args=(conversation, text),
            name="petcore-qq-send",
            daemon=True,
        ).start()

    def _send_qq(self, conversation: str, text: str) -> bool:
        base = self._api_base()
        if not base:
            self._notify("QQ 发送失败：还没有填写 OneBot HTTP API 地址")
            return False
        kind, _, target = str(conversation or "").partition(":")
        if kind == "group" and target.isdigit():
            action = "send_group_msg"
            payload = {"group_id": int(target), "message": text}
        elif kind == "private" and target.isdigit():
            action = "send_private_msg"
            payload = {"user_id": int(target), "message": text}
        else:
            self._notify("QQ 发送失败：会话标识无效")
            return False
        token = self._access_token()
        headers = [("Content-Type", "application/json")]
        if token:
            headers.append(("Authorization", f"Bearer {token}"))
        request = HttpRequest(
            url=f"{base}/{action}",
            method="POST",
            headers=tuple(headers),
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=15.0,
            max_bytes=512 * 1024,
        )
        http = getattr(self.ports, "http", None)
        if http is None or not callable(getattr(http, "request", None)):
            self._notify("QQ 发送失败：HTTP 端口不可用")
            return False
        try:
            response = http.request(request)
        except Exception as exc:
            self._notify(f"QQ 发送失败：{type(exc).__name__}")
            return False
        if int(getattr(response, "status", 0) or 0) >= 400:
            self._notify(f"QQ 发送失败：HTTP {getattr(response, 'status', 0)}")
            return False
        try:
            doc = json.loads(response.body.decode("utf-8", errors="replace"))
        except ValueError:
            doc = {}
        if isinstance(doc, dict) and int(doc.get("retcode") or 0) != 0:
            self._notify(f"QQ 发送失败：retcode {doc.get('retcode')}")
            return False
        return True

    # ---------------------------------------------------------------- AI 自动回复

    def _reply_ai(self, conversation: str, text: str) -> None:
        with self._lock:
            if conversation in self._replying:
                return
            if len(self._replying) >= _MAX_REPLY_THREADS:
                return
            self._replying.add(conversation)

        def run() -> None:
            try:
                reply = self._ask_ai(text)
            finally:
                with self._lock:
                    self._replying.discard(conversation)
            if not reply:
                return
            if self._send_qq(conversation, reply):
                chat = getattr(self.ports, "chat", None)
                ingest = getattr(chat, "ingest", None)
                scheduler = getattr(self.ports, "scheduler", None)
                record = {
                    "channel": "qq",
                    "conversation_id": conversation,
                    "role": "assistant",
                    "text": reply,
                    "external_id": f"ai:{conversation}:{time.time_ns()}",
                }
                if callable(ingest):
                    if scheduler is not None:
                        scheduler.call_later(0, lambda r=record: ingest(r, owner=OWNER))
                    else:
                        ingest(record, owner=OWNER)

        threading.Thread(target=run, name="petcore-qq-reply", daemon=True).start()

    def _ask_ai(self, text: str) -> str:
        api = getattr(self.ports, "api", None)
        stream = getattr(api, "stream_chat", None)
        if not callable(stream):
            self._notify("QQ 自动回复不可用：API 端口缺失")
            return ""
        service_id = str(self._get("ai_service", "") or "")
        if not service_id:
            self._notify("QQ 自动回复不可用：还没有选择 AI 服务")
            return ""
        system = str(
            self._get("ai_system_prompt", "")
            or "你是桌面宠物，正在通过 QQ 和主人聊天；回答简短、自然。"
        )
        memory = ""
        memory_port = getattr(self.ports, "memory", None)
        render = getattr(memory_port, "render_prompt", None)
        if callable(render):
            try:
                memory = str(render("qq", text) or "").strip()
            except Exception:
                memory = ""
        if memory:
            system = f"{system}\n\n{memory}"
        chunks: list[str] = []
        try:
            result = stream(
                service_id,
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": text},
                ],
                model=str(self._get("ai_model", "") or ""),
                temperature=float(self._get("ai_temperature", 0.7) or 0.7),
                on_delta=lambda delta: chunks.append(str(delta or "")),
            )
        except Exception as exc:
            self._notify(f"QQ 自动回复失败：{type(exc).__name__}")
            return ""
        final = "".join(chunks).strip()
        if not final:
            final = str(getattr(result, "text", "") or "").strip()
        if getattr(result, "incomplete", False):
            logger.info("qq ai reply incomplete: %s", getattr(result, "end_reason", ""))
        return final[:1500]

    # ---------------------------------------------------------------- 通知

    def _notify(self, text: str, *, force: bool = False) -> None:
        presentation = getattr(self.ports, "presentation", None)
        say = getattr(presentation, "say", None)
        if not callable(say):
            return
        scheduler = getattr(self.ports, "scheduler", None)
        if scheduler is not None:
            scheduler.call_later(0, lambda: say(text, force=force))
        else:
            try:
                say(text, force=force)
            except Exception:
                pass
