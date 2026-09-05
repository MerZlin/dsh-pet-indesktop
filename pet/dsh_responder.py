# -*- coding: utf-8 -*-
"""DSH /api/respond 回写模块（审批/问题的决策交还 DSH）。

桌宠气泡内点选「同意/拒绝/A/B/C」后，以与 web UI 相同的 client-response 机制
POST 给 DSH 的 /api/respond（无鉴权，本机回环）。只在后台线程调用，绝不阻塞
Qt 主线程；失败返回 (False, reason) 由上层提示"请到 DSH 界面处理"。
"""
from __future__ import annotations

import json
import logging
import urllib.request

log = logging.getLogger("dsh-pet-standalone")

RESPOND_PATH = "/api/respond"
TIMEOUT_S = 4.0


def respond(message: dict, ports: list[int], *, timeout_s: float | None = None) -> tuple[bool, str]:
    """POST 一条 client-response 到在线 DSH 端口，返回 (ok, detail)。

    ``ports`` 为候选端口（3080 / 38080 / DSH_PORT …），逐个尝试；任一返回 HTTP
    200 JSON 且业务字段 ``accepted: true`` 才视为 DSH 已接受。
    全部失败返回 (False, reason)。

    绝不在 Qt 主线程调用：本函数会同步网络等待最多 len(ports) * TIMEOUT_S 秒。
    """
    if not isinstance(message, dict):
        return False, "bad-message"
    timeout = TIMEOUT_S if timeout_s is None else max(0.01, float(timeout_s))
    body = json.dumps(message, ensure_ascii=False).encode("utf-8")
    last_err = "no-port"
    for port in ports:
        try:
            url = f"http://127.0.0.1:{int(port)}{RESPOND_PATH}"
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read(1024).decode("utf-8", "replace")
            try:
                parsed = json.loads(raw)
            except Exception:  # noqa: BLE001
                parsed = {}
            status = getattr(resp, "status", 200)
            if status == 200 and isinstance(parsed, dict) and parsed.get("accepted") is True:
                return True, str(parsed)
            if isinstance(parsed, dict):
                last_err = str(parsed.get("reason") or parsed.get("error") or f"http-{status}")
            else:
                last_err = f"http-{status}"
        except Exception as exc:  # noqa: BLE001 —— 连接失败/超时都算候选端口不可达
            last_err = str(exc)
    return False, last_err
