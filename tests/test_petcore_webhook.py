from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from petcore.net.webhook import WebhookServer


def _post(url: str, body: dict, token: str, *, path: str = "/onebot"):
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Token": token,
        },
        method="POST",
    )
    return urllib.request.urlopen(request, timeout=5)


def _post_with(url: str, body: dict, headers: dict[str, str] | None = None):
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    return urllib.request.urlopen(request, timeout=5)


def test_webhook_accepts_token_and_returns_handler_json() -> None:
    seen = []
    server = WebhookServer(
        owner="test",
        port=0,
        path="/onebot",
        token="secret",
        handler=lambda request: seen.append(request) or {"ok": True, "echo": request["body"]["x"]},
    ).start()
    try:
        with _post(server.url, {"x": 7}, "secret") as response:
            assert response.status == 200
            assert json.loads(response.read())["echo"] == 7
        assert seen and seen[0]["body"] == {"x": 7}
    finally:
        server.stop()


def test_webhook_rejects_bad_token_and_unknown_path() -> None:
    server = WebhookServer(
        owner="test",
        port=0,
        path="/onebot",
        token="secret",
        handler=lambda request: {"ok": True},
    ).start()
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(server.url, {"x": 1}, "wrong")
        assert exc.value.code == 401
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(server.url.replace("/onebot", "/other"), {"x": 1}, "secret")
        assert exc.value.code == 404
    finally:
        server.stop()


def test_webhook_requires_non_empty_token() -> None:
    with pytest.raises(ValueError):
        WebhookServer(owner="test", port=0, path="/", token="", handler=lambda r: {})


def test_webhook_accepts_raw_and_bearer_authorization() -> None:
    seen = []
    server = WebhookServer(
        owner="test",
        port=0,
        path="/onebot",
        token="secret",
        handler=lambda request: seen.append(request) or {"ok": True},
    ).start()
    try:
        for value in ("secret", "Bearer secret"):
            with _post_with(server.url, {"x": 1}, {"Authorization": value}) as response:
                assert response.status == 200
        assert len(seen) == 2
    finally:
        server.stop()


def test_webhook_accepts_query_tokens_and_a_trailing_slash() -> None:
    seen = []
    server = WebhookServer(
        owner="test",
        port=0,
        path="/onebot",
        token="secret",
        handler=lambda request: seen.append(request) or {"ok": True},
    ).start()
    try:
        for url in (
            server.url + "?token=secret",
            server.url + "?access_token=secret",
            server.url + "/?token=secret",
        ):
            with _post_with(url, {"x": 2}) as response:
                assert response.status == 200
        assert len(seen) == 3
        assert all(item["body"] == {"x": 2} for item in seen)
    finally:
        server.stop()


def test_webhook_rejects_a_wrong_raw_authorization() -> None:
    server = WebhookServer(
        owner="test",
        port=0,
        path="/onebot",
        token="secret",
        handler=lambda request: {"ok": True},
    ).start()
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_with(server.url, {"x": 1}, {"Authorization": "wrong"})
        assert exc.value.code == 401
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post_with(server.url + "?access_token=wrong", {"x": 1})
        assert exc.value.code == 401
    finally:
        server.stop()
