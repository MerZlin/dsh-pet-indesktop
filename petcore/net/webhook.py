"""本地回环 webhook：只给 OneBot/本地桥接之类的外部进程 POST 事件。

安全口径：
- 只绑 ``127.0.0.1``，不监听局域网；
- token 必填，空 token 直接拒绝启动；常量时间比较；
- 只接受 JSON，body 上限 1 MiB，超限 413；
- 日志只记 owner/方法/状态，不记 body、token 或查询串。
"""

from __future__ import annotations

import hmac
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlsplit

logger = logging.getLogger("petcore.webhook")

MAX_BODY = 1024 * 1024


class WebhookServer:
    def __init__(
        self,
        *,
        owner: str,
        port: int,
        path: str,
        token: str,
        handler: Callable[[dict], dict],
    ) -> None:
        self.owner = str(owner or "mod")
        self.port = max(0, int(port))
        self.path = str(path or "/").strip() or "/"
        self.token = str(token or "")
        self.handler = handler
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        if not self.token:
            raise ValueError("webhook token must not be empty")

    @property
    def url(self) -> str:
        port = self._server.server_address[1] if self._server is not None else self.port
        return f"http://127.0.0.1:{port}{self.path}"

    def start(self) -> "WebhookServer":
        if self._server is not None:
            return self
        owner = self

        class _Handler(BaseHTTPRequestHandler):
            def _send(self, code: int, payload: dict) -> None:
                raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except OSError:
                    pass

            def _authorized(self) -> bool:
                header = str(self.headers.get("X-Webhook-Token") or "")
                if not header:
                    auth = str(self.headers.get("Authorization") or "").strip()
                    if auth.lower().startswith("bearer "):
                        header = auth[7:].strip()
                    elif auth:
                        # NapCat 的 HTTP 客户端发裸 token（没有 Bearer 前缀）。
                        header = auth
                if not header:
                    query = parse_qs(urlsplit(self.path).query)
                    header = str((query.get("token") or [""])[0]) or str(
                        (query.get("access_token") or [""])[0]
                    )
                return bool(header) and hmac.compare_digest(header, owner.token)

            def _read(self) -> dict:
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = 0
                if length > MAX_BODY:
                    raise ValueError("too_large")
                raw = self.rfile.read(max(0, length)) if length else b""
                if not raw:
                    return {}
                try:
                    value = json.loads(raw.decode("utf-8", errors="replace"))
                except ValueError:
                    raise ValueError("bad_json") from None
                return value if isinstance(value, dict) else {}

            def do_POST(self):  # noqa: N802 - http.server API
                split = urlsplit(self.path)
                if split.path.rstrip("/") != owner.path.rstrip("/"):
                    self._send(404, {"ok": False, "error": "not_found"})
                    return
                if not self._authorized():
                    self._send(401, {"ok": False, "error": "bad_token"})
                    return
                try:
                    body = self._read()
                except ValueError as exc:
                    self._send(413 if str(exc) == "too_large" else 400, {"ok": False})
                    return
                request = {
                    "method": "POST",
                    "path": split.path,
                    "headers": {str(k).lower(): str(v) for k, v in self.headers.items()},
                    "body": body,
                    "query": {key: values[-1] for key, values in parse_qs(split.query).items()},
                }
                try:
                    response = owner.handler(request)
                except Exception:
                    logger.warning("webhook handler failed: owner=%s", owner.owner, exc_info=True)
                    response = {"ok": False, "error": "handler_failed"}
                self._send(200, response if isinstance(response, dict) else {"ok": True})

            def do_GET(self):  # noqa: N802 - http.server API
                split = urlsplit(self.path)
                if split.path.rstrip("/") != owner.path.rstrip("/"):
                    self._send(404, {"ok": False, "error": "not_found"})
                    return
                if not self._authorized():
                    self._send(401, {"ok": False, "error": "bad_token"})
                    return
                self._send(200, {"ok": True, "owner": owner.owner})

            def log_message(self, *args):
                return

        class _Server(ThreadingHTTPServer):
            # Windows 上 allow_reuse_address=1 会让两个实例绑同一端口并随机分流。
            allow_reuse_address = False

        server = _Server(("127.0.0.1", self.port), _Handler)
        server.daemon_threads = True
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever,
            name=f"petcore-webhook-{self.owner}",
            daemon=True,
        )
        self._thread.start()
        logger.info("webhook listening: owner=%s port=%s", self.owner, server.server_address[1])
        return self

    def stop(self) -> None:
        server = self._server
        self._server = None
        self._thread = None
        if server is None:
            return
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass
