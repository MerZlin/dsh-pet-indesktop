from __future__ import annotations

import json
import time
from types import SimpleNamespace


def _runtime_module():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "examples" / "petcore-mods" / "im.qq" / "host" / "runtime.py"
    spec = importlib.util.spec_from_file_location("im_qq_runtime_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _Config:
    def __init__(self, **values):
        self.values = dict(values)

    def get(self, key, default=None):
        return self.values.get(key, default)


class _Credentials:
    def __init__(self):
        self.data = {}

    def get(self, owner, key):
        return self.data.get((owner, key))

    def set(self, owner, key, value):
        self.data[(owner, key)] = value

    def delete(self, owner, key):
        self.data.pop((owner, key), None)


class _Webhook:
    def __init__(self):
        self.handler = None
        self.started = []

    def start(self, owner, *, port, path, token, handler):
        self.started.append((owner, port, path, token))
        self.handler = handler
        return SimpleNamespace(stop=lambda: None)


class _Chat:
    def __init__(self):
        self.ingested = []
        self.handlers = {}

    def ingest(self, record, *, owner):
        self.ingested.append((owner, dict(record)))
        return "s1"

    def register_channel(self, channel, on_submit):
        self.handlers[channel] = on_submit
        return lambda: self.handlers.pop(channel, None)


class _Presentation:
    def __init__(self):
        self.said = []

    def say(self, text, *, force=False):
        self.said.append((text, force))


class _Scheduler:
    def call_later(self, delay_ms, callback):
        callback()
        return "h"


class _Http:
    def __init__(self):
        self.requests = []

    def request(self, request):
        self.requests.append(request)
        return SimpleNamespace(status=200, body=b'{"retcode":0}')


class _Api:
    def stream_chat(self, service_id, messages, *, on_delta=None, **kwargs):
        if on_delta:
            on_delta("你好呀")
        return SimpleNamespace(text="你好呀", incomplete=False)


def _runtime(**config):
    module = _runtime_module()
    values = {
        "im_qq_enabled": True,
        "im_qq_api_base": "http://127.0.0.1:3000",
        "im_qq_webhook_port": 8790,
        "im_qq_webhook_path": "/onebot",
        "im_qq_reply_mode": "notify",
        "im_qq_notify_bubble": True,
        "im_qq_allowed_groups": ["123"],
        "im_qq_allowed_users": ["42"],
        "ai_service": "svc",
        "ai_model": "m",
        "ai_temperature": 0.7,
        "ai_system_prompt": "",
    }
    values.update(config)
    ports = SimpleNamespace(
        config=_Config(**values),
        credentials=_Credentials(),
        webhook=_Webhook(),
        chat=_Chat(),
        presentation=_Presentation(),
        scheduler=_Scheduler(),
        http=_Http(),
        api=_Api(),
        memory=None,
    )
    runtime = module.QqRuntime(SimpleNamespace(ports=ports))
    runtime.start()
    return runtime, ports


def test_inbound_group_message_ingests_and_notifies() -> None:
    runtime, ports = _runtime()
    try:
        event = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 123,
            "user_id": 42,
            "self_id": 999,
            "message_id": 77,
            "time": time.time(),
            "sender": {"card": "小明"},
            "message": [
                {"type": "text", "data": {"text": "晚上吃"}},
                {"type": "text", "data": {"text": "什么"}},
            ],
        }
        assert ports.webhook.handler({"body": event}) == {"ok": True}
        assert ports.chat.ingested
        owner, record = ports.chat.ingested[0]
        assert owner == "im.qq"
        assert record["channel"] == "qq"
        assert record["conversation_id"] == "group:123"
        assert record["text"] == "晚上吃什么"
        assert record["external_id"] == "77"
        assert ports.presentation.said
        # 同一 message_id 再投一次不重复入库
        ports.webhook.handler({"body": event})
        assert len(ports.chat.ingested) == 1
    finally:
        runtime.stop()


def test_outbound_submit_sends_onebot_action() -> None:
    runtime, ports = _runtime()
    try:
        handler = ports.chat.handlers["qq"]
        handler("在吗", 0, "private:42")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not ports.http.requests:
            time.sleep(0.01)
        assert ports.http.requests
        request = ports.http.requests[0]
        assert request.url.endswith("/send_private_msg")
        payload = json.loads(request.body)
        assert payload == {"user_id": 42, "message": "在吗"}
    finally:
        runtime.stop()


def test_ai_reply_sends_and_records_assistant() -> None:
    runtime, ports = _runtime(im_qq_reply_mode="ai")
    try:
        event = {
            "post_type": "message",
            "message_type": "private",
            "user_id": 42,
            "self_id": 999,
            "message_id": 88,
            "time": time.time(),
            "sender": {"nickname": "主人"},
            "message": [{"type": "text", "data": {"text": "你好"}}],
        }
        ports.webhook.handler({"body": event})
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and len(ports.chat.ingested) < 2:
            time.sleep(0.01)
        assert any(record["role"] == "assistant" for _owner, record in ports.chat.ingested)
        assert ports.http.requests
    finally:
        runtime.stop()


def test_empty_whitelist_ignores_everything() -> None:
    runtime, ports = _runtime(im_qq_allowed_groups=[], im_qq_allowed_users=[])
    try:
        event = {
            "post_type": "message",
            "message_type": "private",
            "user_id": 42,
            "self_id": 999,
            "message_id": 99,
            "time": time.time(),
            "message": [{"type": "text", "data": {"text": "你好"}}],
        }
        assert ports.webhook.handler({"body": event}) == {"ok": True}
        assert ports.chat.ingested == []
        assert not any("你好" in text for text, _force in ports.presentation.said)
    finally:
        runtime.stop()
