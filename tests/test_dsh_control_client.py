# -*- coding: utf-8 -*-
"""dsh_control 文件队列客户端集成测试（真实请求/响应文件协议）。

AgentLinkManager 的控制回执文案按 request() 的返回契约映射错误码
（bridge-control-timeout / session-not-found / bridge-control-rejected…），
这里用临时目录 + 假桥接线程锁定该契约，避免两端各自漂移。
"""
from __future__ import annotations

import json
import threading
import time

import pytest

import pet.dsh_control as dsh_control


def _fake_bridge(tmp_path, response):
    """后台线程：等请求文件出现后写响应文件，返回 captured 字典。"""
    captured = {}

    def run():
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            files = sorted(tmp_path.glob("watchdog-request-*.json"))
            if files:
                request = json.loads(files[0].read_text(encoding="utf-8"))
                captured.update(request)
                response_path = tmp_path / f"watchdog-response-{request['id']}.json"
                response_path.write_text(
                    json.dumps({"id": request["id"], **response}), encoding="utf-8")
                return
            time.sleep(0.02)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return captured, thread


@pytest.fixture
def bridge_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(dsh_control, "_bridge_dir", lambda: str(tmp_path))
    return tmp_path


def test_request_writes_context_and_parses_success(bridge_dir):
    captured, thread = _fake_bridge(bridge_dir, {"ok": True, "phase": "replanned", "plan": "下一步"})
    ok, detail = dsh_control.request(
        "replan", "sess-1", goal="修好登录", context="风险理由：W6 同类重复",
        alert_id="exploration-control:sess-1", timeout=5.0)
    thread.join(5.0)
    assert ok is True
    assert json.loads(detail)["phase"] == "replanned"
    assert captured["operation"] == "replan"
    assert captured["sessionId"] == "sess-1"
    assert captured["goal"] == "修好登录"
    assert captured["context"] == "风险理由：W6 同类重复"
    assert captured["timeoutMs"] == 5000
    assert not list(bridge_dir.glob("watchdog-request-*.json")), "请求文件应被清理"
    assert not list(bridge_dir.glob("watchdog-response-*.json")), "响应文件应被清理"


def test_request_returns_bridge_error_code_on_rejection(bridge_dir):
    captured, thread = _fake_bridge(
        bridge_dir, {"ok": False, "phase": "not-found", "error": "session-not-found"})
    ok, detail = dsh_control.request("interrupt", "sess-2", timeout=5.0)
    thread.join(5.0)
    assert ok is False
    assert detail == "session-not-found", "失败原因必须原样回传，供回显区分档位"


def test_request_timeout_cleans_up_request_file(bridge_dir):
    ok, detail = dsh_control.request("interrupt", "sess-3", timeout=1.0)
    assert ok is False
    assert detail == "bridge-control-timeout"
    assert not list(bridge_dir.glob("watchdog-request-*.json")), "超时也必须清理请求文件"


@pytest.mark.parametrize("session_id,expected", [
    ("", "missing-session-id"),
    ("turn:123", "missing-session-id"),
])
def test_request_rejects_bad_session_without_touching_disk(bridge_dir, session_id, expected):
    ok, detail = dsh_control.request("interrupt", session_id, timeout=1.0)
    assert (ok, detail) == (False, expected)
    assert not list(bridge_dir.iterdir()), "非法请求不得写盘"


def test_request_rejects_unknown_operation(bridge_dir):
    ok, detail = dsh_control.request("explode", "sess-4", timeout=1.0)
    assert (ok, detail) == (False, "unsupported-operation")
