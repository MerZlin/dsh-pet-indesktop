"""Validation-only deterministic image/network boundary, not a release module.

Only generated images and an authenticated local HTTP fixture are permitted.
Production bootstrap, package factory, lifecycle, leases, Qt and Worker remain real.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import TypedDict


class ValidationHTTPStats(TypedDict):
    requests: int
    errors: list[str]


@contextmanager
def synthetic_http_service():
    """A bounded loopback fixture; never log image bytes or request credentials."""
    import base64
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    stats: ValidationHTTPStats = {"requests": 0, "errors": []}
    key = "synthetic-test-secret"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                if self.path != "/v1/chat/completions" or self.headers.get("Authorization") != "Bearer " + key:
                    raise ValueError("invalid fixture request route/authorization")
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 1024 * 1024:
                    raise ValueError("fixture request size limit")
                body = json.loads(self.rfile.read(size))
                images = [
                    part["image_url"]["url"]
                    for message in body["messages"]
                    for part in message.get("content", [])
                    if isinstance(part, dict) and part.get("type") == "image_url"
                ]
                if len(images) != 1 or not images[0].startswith("data:image/jpeg;base64,"):
                    raise ValueError("expected one synthetic JPEG")
                if not base64.b64decode(images[0].split(",", 1)[1], validate=True).startswith(b"\xff\xd8"):
                    raise ValueError("invalid synthetic JPEG")
                stats["requests"] += 1
                reply = json.dumps({"choices": [{"message": {"content": "synthetic analysis"}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)
            except Exception as error:
                stats["errors"].append(type(error).__name__)
                self.send_error(400, "fixture request rejected")

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 5
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield {"base_url": f"http://127.0.0.1:{server.server_port}", "model": "validation", "api_key": key, "timeout": 10}, stats
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("local fixture did not stop")
