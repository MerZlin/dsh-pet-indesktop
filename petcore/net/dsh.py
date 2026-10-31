"""DSH 本地通道客户端（HTTP + SSE，只连 127.0.0.1）。

通道插件 ``dsh-pet-channel`` 监听本机端口，并把 ``{origin, port, token}`` 写到
``%APPDATA%/dsh-pet-bridge/pet-channel.json``。本模块只读这个文件、只发本机请求；
token 只进请求头，不写日志、不进异常文本。
"""

from __future__ import annotations

import json
import os
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from . import http as http_mod


class DshError(RuntimeError):
    """稳定 ``reason``：not_running / auth / network / timeout / protocol / bad_request。"""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = str(reason or "network")
        self.detail = str(detail or "")[:200]
        super().__init__(self.reason)


@dataclass(frozen=True)
class DshInfo:
    origin: str
    token: str
    port: int = 0
    version: str = ""
    pid: int = 0


def _state_file() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Roaming"
    return root / "dsh-pet-bridge" / "pet-channel.json"


def _reason_for(exc: BaseException) -> str:
    if isinstance(exc, DshError):
        return exc.reason
    if isinstance(exc, urllib.error.HTTPError):
        code = int(getattr(exc, "code", 0) or 0)
        if code in (401, 403):
            return "auth"
        if code == 404:
            return "not_running"
        return "protocol"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "timeout"
    return "network"


class DshClient:
    def __init__(self, *, timeout: float = 4.0, state_file: Path | None = None) -> None:
        self.timeout = max(1.0, min(30.0, float(timeout)))
        self._state_file = Path(state_file) if state_file is not None else _state_file()
        self._stream_lock = threading.Lock()
        self._active_streams: set = set()

    def _register_stream(self, response) -> None:
        with self._stream_lock:
            self._active_streams.add(response)

    def _unregister_stream(self, response) -> None:
        with self._stream_lock:
            self._active_streams.discard(response)

    def close_active(self) -> None:
        """关闭所有在飞的 SSE 响应，让 ``events()`` 的读循环立即退出。

        ``stop`` 事件只在读到下一个行边界时才被检查；通道 keepalive 是 15s，
        不主动 close 时关闭窗口/退出视图会留下最长 15–30s 的线程。关闭响应
        只影响当前客户端，不影响其它连接。
        """
        with self._stream_lock:
            streams = list(self._active_streams)
            self._active_streams.clear()
        for response in streams:
            try:
                response.close()
            except Exception:  # noqa: BLE001 - 已被对端关闭
                pass

    def info(self) -> DshInfo | None:
        try:
            doc = json.loads(self._state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(doc, dict):
            return None
        token = str(doc.get("token") or "").strip()
        origin = str(doc.get("origin") or "").strip().rstrip("/")
        if not token:
            return None
        if not origin:
            host = str(doc.get("host") or "127.0.0.1").strip()
            try:
                port = int(doc.get("port") or 0)
            except (TypeError, ValueError):
                port = 0
            if port <= 0:
                return None
            origin = f"http://{host}:{port}"
        try:
            port = int(doc.get("port") or 0)
        except (TypeError, ValueError):
            port = 0
        try:
            parts = urllib.parse.urlsplit(origin)
        except ValueError:
            return None
        host = (parts.hostname or "").lower()
        if parts.scheme != "http" or host not in {"127.0.0.1", "localhost", "::1"}:
            return None
        url_port = parts.port or 0
        if port and url_port and port != url_port:
            return None
        if not port:
            port = url_port
        if port <= 0:
            return None
        return DshInfo(
            origin=origin,
            token=token,
            port=port,
            version=str(doc.get("version") or ""),
            pid=int(doc.get("pid") or 0) if str(doc.get("pid") or "").isdigit() else 0,
        )

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict | None = None,
        token: str = "",
        timeout: float | None = None,
        stream: bool = False,
    ):
        info = self.info()
        if info is None:
            raise DshError("not_running")
        url = info.origin + path
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token or path not in ("/pet/ping", "/"):
            headers["x-pet-token"] = info.token
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            return http_mod.urlopen(
                request,
                timeout=float(timeout or self.timeout),
                routes=(False,),
                pollute=False,
            )
        except urllib.error.HTTPError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DshError(_reason_for(exc)) from None

    def _json(self, path: str, *, method: str = "GET", payload: dict | None = None):
        try:
            response = self._request(path, method=method, payload=payload)
        except urllib.error.HTTPError as exc:
            raise DshError(_reason_for(exc)) from None
        try:
            raw = response.read(1024 * 1024)
        finally:
            try:
                response.close()
            except Exception:
                pass
        try:
            doc = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            raise DshError("protocol") from None
        if not isinstance(doc, dict):
            raise DshError("protocol")
        return doc

    def ping(self) -> dict:
        try:
            response = self._request("/pet/ping", timeout=min(self.timeout, 2.0))
        except urllib.error.HTTPError as exc:
            raise DshError(_reason_for(exc)) from None
        try:
            raw = response.read(64 * 1024)
        finally:
            try:
                response.close()
            except Exception:
                pass
        try:
            doc = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            raise DshError("protocol") from None
        return doc if isinstance(doc, dict) else {}

    def prompt(self, text: str) -> dict:
        value = str(text or "").strip()
        if not value:
            raise DshError("bad_request")
        return self._json("/pet/prompt", method="POST", payload={"text": value[:4000]})

    def answer(self, request_id: str, allow: bool) -> dict:
        return self._json(
            "/pet/answer",
            method="POST",
            payload={"id": str(request_id), "allow": bool(allow)},
        )

    def cancel(self) -> dict:
        return self._json("/pet/cancel", method="POST", payload={})

    def history(self, limit: int = 20) -> list[dict]:
        doc = self._json(f"/pet/history?limit={max(1, min(50, int(limit)))}")
        items = doc.get("items")
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []

    def events(self, stop, *, idle_sleep: float = 1.0) -> Iterator[dict]:
        """SSE 帧迭代器：断线自动重连；``stop`` 是 ``threading.Event``。

        连接状态以 ``{"kind": "connection", "ok": bool, "reason": str}`` 帧暴露，
        视图据此把「已连接」改成「已断开」；重连按指数退避，读超时至少 2 倍
        keepalive，避免机器忙时周期性误重连。
        """
        delay = max(1.0, float(idle_sleep))
        while not stop.is_set():
            try:
                response = self._request(
                    "/pet/events", timeout=max(30.0, self.timeout * 2.0)
                )
            except Exception as exc:  # noqa: BLE001 - 通道未起/鉴权失败
                yield {
                    "kind": "connection",
                    "ok": False,
                    "reason": _reason_for(exc),
                }
                if stop.wait(delay):
                    return
                delay = min(30.0, delay * 2.0)
                continue
            delay = max(1.0, float(idle_sleep))
            self._register_stream(response)
            yield {"kind": "connection", "ok": True, "reason": ""}
            try:
                for raw in response:
                    if stop.is_set():
                        return
                    line = bytes(raw).decode("utf-8", errors="replace").strip()
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data:"):
                        payload = line[5:].strip()
                        try:
                            frame = json.loads(payload)
                        except ValueError:
                            continue
                        if isinstance(frame, dict):
                            yield frame
            except (TimeoutError, socket.timeout):
                pass
            except Exception:  # noqa: BLE001 - 断线后重连
                pass
            finally:
                self._unregister_stream(response)
                try:
                    response.close()
                except Exception:
                    pass
            if stop.is_set():
                return
            if stop.wait(max(0.5, float(idle_sleep))):
                return
