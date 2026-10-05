"""Loopback Chat Completions adapter for DSH using existing Codex login."""

import argparse
import hmac
import json
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .rpc import catalog, generate, parent_alive
from .usage import UsageReader

MAX_BODY = 24 * 1024 * 1024


def translate(body):
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty array")
    items = []
    for message in messages:
        role = message.get("role")
        if role not in ("system", "developer", "user", "assistant"):
            raise ValueError("Unsupported conversation role")
        content = message.get("content", "")
        if isinstance(content, list):
            parts = []
            for part in content:
                if part.get("type") == "text":
                    parts.append({"type": "input_text", "text": part.get("text", "")})
                elif part.get("type") == "image_url":
                    image = part.get("image_url")
                    url = image.get("url") if isinstance(image, dict) else image
                    parts.append({"type": "input_image", "image_url": url})
                else:
                    raise ValueError("Unsupported content type")
            content = parts
        elif not isinstance(content, str):
            raise ValueError("Unsupported message content")
        items.append({"type": "message", "role": role, "content": content})
    if body.get("tools") or body.get("functions"):
        raise ValueError("This DSH chat adapter supports conversation and image input only")
    return {"model": body.get("model"), "input": items, "reasoning": {"effort": "low"}}


def completion(response):
    text = "\n".join(part["text"] for item in response["output"] if item["type"] == "message" for part in item["content"] if part["type"] == "output_text")
    if not text.strip():
        raise RuntimeError("GPT 未回傳聊天文字，請稍後再試。")
    return {
        "id": response["id"].replace("resp_", "chatcmpl_", 1),
        "object": "chat.completion",
        "created": response["created_at"],
        "model": response["model"],
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": None,
    }


def chunks(result):
    base = {k: result[k] for k in ("id", "created", "model")}
    base["object"] = "chat.completion.chunk"
    yield {**base, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}
    text = result["choices"][0]["message"]["content"]
    for offset in range(0, len(text), 120):
        yield {**base, "choices": [{"index": 0, "delta": {"content": text[offset : offset + 120]}, "finish_reason": None}]}
    yield {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def allowed(self):
        return (
            self.headers.get("Host") == "127.0.0.1:%d" % self.server.config["port"]
            and not self.headers.get("Origin")
            and hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + self.server.config["localToken"])
        )

    def json(self, code, value):
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.allowed():
            return self.json(401, {"error": {"message": "Local bridge authentication required"}})
        if self.path == "/health":
            return self.json(200, {"ok": True, "auth": "codex-chatgpt", "model": self.server.config["model"]})
        if self.path == "/v1/usage":
            try:
                return self.json(200, self.server.usage_reader.read())
            except Exception:
                return self.json(503, {"ok": False, "error": {"message": "目前無法讀取 Codex 用量，請確認 Codex 已登入。"}})
        if self.path == "/v1/models":
            return self.json(200, {"object": "list", "data": [{"id": m["model"], "object": "model", "owned_by": "openai"} for m in self.server.models]})
        self.json(404, {"error": {"message": "Unknown endpoint"}})

    def do_POST(self):
        if not self.allowed():
            return self.json(401, {"error": {"message": "Local bridge authentication required"}})
        if self.path != "/v1/chat/completions":
            return self.json(404, {"error": {"message": "Unknown endpoint"}})
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length <= MAX_BODY:
                return self.json(413, {"error": {"message": "Invalid request size"}})
            body = json.loads(self.rfile.read(length))
            with self.server.model_lock:
                result = completion(generate(translate(body), self.server.config, self.server.models))
            if body.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                for item in chunks(result):
                    self.wfile.write(("data: " + json.dumps(item, ensure_ascii=False) + "\n\n").encode("utf-8"))
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                self.close_connection = True
            else:
                self.json(200, result)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            self.json(400 if isinstance(error, ValueError) else 502, {"error": {"message": str(error), "type": "codex_bridge_error"}})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-pid", type=int)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    models = catalog(config["codexExecutable"])
    server = ThreadingHTTPServer(("127.0.0.1", config["port"]), Handler)
    server.config, server.models, server.model_lock = config, models, threading.Lock()
    server.usage_reader = UsageReader(config)
    # Finish and close an active Codex RPC before exiting the adapter.
    server.daemon_threads = False
    if args.parent_pid:

        def monitor():
            while parent_alive(args.parent_pid):
                time.sleep(2)
            server.shutdown()

        threading.Thread(target=monitor, daemon=True).start()
    server.serve_forever()
    server.server_close()


if __name__ == "__main__":
    main()
