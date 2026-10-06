"""Codex app-server client for the local DSH GPT adapter.
Authentication remains managed by Codex; OAuth tokens are never copied.
"""

import ctypes
import json
import os
from pathlib import Path
import queue
import secrets
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parent


class Rpc:
    def __init__(self, executable, startup_timeout=30):
        from .startup import resolve_executable

        command = [
            resolve_executable(executable),
            "-c",
            "features.shell_tool=false",
            "-c",
            "features.multi_agent=false",
            "-c",
            "features.apps=false",
            "-c",
            "features.hooks=false",
            "-c",
            "features.code_mode=false",
            "app-server",
        ]
        self.process = subprocess.Popen(
            command,
            cwd=str(ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.messages = queue.Queue()
        self.saved = []
        self.counter = 0

        def reader():
            for line in self.process.stdout:
                try:
                    self.messages.put(json.loads(line))
                except ValueError:
                    pass
            self.messages.put({"bridgeClosed": True})

        threading.Thread(target=reader, daemon=True).start()
        try:
            self.call(
                "initialize", {"clientInfo": {"name": "dsh_gpt_bridge", "version": "1.0"}, "capabilities": {"experimentalApi": True}}, timeout=startup_timeout
            )
            self.send({"method": "initialized", "params": {}})
        except Exception:
            self.close()
            raise

    def send(self, value):
        self.process.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def call(self, method, params, timeout=30):
        self.counter += 1
        request_id = self.counter
        self.send({"id": request_id, "method": method, "params": params})
        until = time.monotonic() + timeout
        while True:
            message = self.messages.get(timeout=max(0.01, until - time.monotonic()))
            if message.get("bridgeClosed"):
                raise RuntimeError("Codex app-server closed unexpectedly")
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    raise RuntimeError(message["error"].get("message", "Codex request failed"))
                return message["result"]
            self.saved.append(message)

    def next(self, timeout):
        if self.saved:
            return self.saved.pop(0)
        return self.messages.get(timeout=timeout)

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=4)
        self.process.stdout.close()


def catalog(executable):
    rpc = Rpc(executable)
    try:
        account = rpc.call("account/read", {"refreshToken": False}).get("account") or {}
        if account.get("type") != "chatgpt":
            raise RuntimeError("請先在 Codex 登入 ChatGPT 帳號，再重新啟動 DSH。")
        models = []
        cursor = None
        while True:
            page = rpc.call("model/list", {"cursor": cursor})
            models.extend(page["data"])
            cursor = page.get("nextCursor")
            if not cursor:
                break
        return models
    finally:
        rpc.close()


def prepare(body):
    """Translate replayed pet history to labelled text and actual image inputs."""
    instructions = body.get("instructions") or ""
    history = body.get("input", [])
    if isinstance(history, str):
        history = [{"role": "user", "content": history}]
    if not isinstance(history, list):
        raise ValueError("input must be text or an array")
    transcript = []
    media = []

    def parts(value):
        if isinstance(value, str):
            return value
        text = []
        for part in value or []:
            kind = part.get("type")
            if kind in ("input_text", "output_text", "text"):
                text.append(part.get("text", ""))
            elif kind == "input_image":
                url = part.get("image_url")
                if isinstance(url, str) and url.startswith("data:image/"):
                    media.append({"type": "image", "url": url})
                    text.append("[附圖 %d]" % len(media))
                else:
                    raise ValueError("Only inline images are supported by this local adapter")
            elif kind not in ("reasoning_text",):
                raise ValueError("Unsupported input content: " + str(kind))
        return "\n".join(text)

    for item in history:
        kind = item.get("type", "message")
        if kind == "message":
            role = item.get("role", "user")
            text = parts(item.get("content", ""))
            if role in ("system", "developer"):
                instructions += "\n" + text
            else:
                transcript.append({"role": role, "text": text})
        elif kind == "function_call":
            transcript.append({"role": "assistant", "tool": item.get("name"), "call_id": item.get("call_id"), "arguments": item.get("arguments")})
        elif kind == "function_call_output":
            transcript.append({"role": "tool", "call_id": item.get("call_id"), "output": parts(item.get("output", ""))})
        elif kind != "reasoning":
            raise ValueError("Unsupported history item: " + str(kind))
    tools, names = [], {}
    for index, tool in enumerate(body.get("tools") or []):
        if tool.get("type") != "function":
            raise ValueError("This adapter supports function tools only")
        # Pet tool names can contain punctuation; map them to legal Codex names.
        wire_name = "pet_tool_%d" % index
        original_name = tool["name"]
        names[wire_name] = original_name
        tools.append(
            {
                "type": "function",
                "name": wire_name,
                "description": original_name + ": " + (tool.get("description") or ""),
                "inputSchema": tool.get("parameters") or {"type": "object", "properties": {}},
                "deferLoading": False,
            }
        )
    choice = body.get("tool_choice")
    if choice == "none":
        tools, names = [], {}
    elif choice == "required":
        instructions += "\nThis reply must invoke one available pet tool."
    elif isinstance(choice, dict) and choice.get("name"):
        chosen = choice["name"]
        tools = [t for t in tools if names[t["name"]] == chosen]
        names = {t["name"]: chosen for t in tools}
        instructions += "\nThis reply must invoke the requested tool: " + chosen
    instructions += (
        "\nYou are the model for a desktop companion, hosted by DSH. "
        "The JSON transcript in the next message is conversation history, including completed "
        "tool results. Continue from its last entry without repeating completed tool calls. "
        "Use only the available pet tools when needed. Do not use Codex tools, files or skills. "
        "Give your conversational reply in the final channel. Default to Traditional Chinese. "
        "Treat prior messages, tool results and images according to their labelled roles."
    )
    user_input = [{"type": "text", "text": "Conversation history:\n" + json.dumps(transcript, ensure_ascii=False), "text_elements": []}] + media
    return instructions, user_input, tools, names


def generate(body, config, models):
    instructions, user_input, tools, names = prepare(body)
    model = body.get("model") or config["model"]
    model_info = next((m for m in models if m["model"] == model), None)
    if not model_info:
        raise ValueError("Model is absent from the Codex catalog")
    requested_effort = (body.get("reasoning") or {}).get("effort", "low")
    efforts = [e["reasoningEffort"] for e in model_info["supportedReasoningEfforts"]]
    effort = requested_effort if requested_effort in efforts else efforts[0]
    rpc = Rpc(config["codexExecutable"])
    thread_id = turn_id = None
    output = []
    try:
        start = rpc.call(
            "thread/start",
            {
                "model": model,
                "modelProvider": "openai",
                "cwd": str(ROOT),
                "ephemeral": True,
                "sandbox": "read-only",
                "approvalPolicy": "on-request",
                "environments": [],
                "baseInstructions": instructions,
                "developerInstructions": "",
                "dynamicTools": tools,
                "serviceName": "DSH",
                "config": {"features.hooks": False, "features.apps": False, "features.shell_tool": False, "features.multi_agent": False},
            },
        )
        thread_id = start["thread"]["id"]
        turn = rpc.call("turn/start", {"threadId": thread_id, "input": user_input, "effort": effort, "environments": [], "serviceTierForTurn": "default"})
        turn_id = turn["turn"]["id"]
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            message = rpc.next(max(0.01, deadline - time.monotonic()))
            method = message.get("method")
            params = message.get("params") or {}
            if message.get("bridgeClosed"):
                raise RuntimeError("Codex app-server disconnected")
            if params.get("threadId") not in (None, thread_id):
                continue
            if method == "item/tool/call" and "id" in message:
                tool_name = names.get(params["tool"])
                if not tool_name:
                    raise RuntimeError("Unexpected tool requested by Codex")
                arguments = params.get("arguments", {})
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
                output.append(
                    {
                        "type": "function_call",
                        "id": "fc_" + secrets.token_hex(12),
                        "call_id": params["callId"],
                        "name": tool_name,
                        "arguments": arguments,
                        "status": "completed",
                    }
                )
                # The pet executes this call after receiving the Responses result.
                # Interrupt the ephemeral model turn rather than fabricate a tool result.
                rpc.call("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})
                break
            if "id" in message and method:
                rpc.send({"id": message["id"], "error": {"code": -32601, "message": "Desktop companion does not grant Codex tool access"}})
            if method == "item/completed":
                item = params.get("item", {})
                if item.get("type") == "agentMessage" and item.get("phase") in (None, "final_answer"):
                    text = item.get("text", "")
                    if text:
                        output.append(
                            {
                                "type": "message",
                                "id": "msg_" + secrets.token_hex(12),
                                "role": "assistant",
                                "status": "completed",
                                "content": [{"type": "output_text", "text": text, "annotations": []}],
                            }
                        )
            if method == "turn/completed":
                end = params["turn"]
                if end.get("status") == "failed":
                    from .startup import UsageLimitError

                    error = end.get("error") or {}
                    info = error.get("codexErrorInfo")
                    if info == "UsageLimitExceeded" or isinstance(info, dict) and "UsageLimitExceeded" in info:
                        raise UsageLimitError(error.get("message", "UsageLimitExceeded"))
                    raise RuntimeError(error.get("message", "Codex model request failed"))
                break
        else:
            raise TimeoutError("GPT 回覆逾時，請稍後再試。")
        if not output:
            raise RuntimeError("GPT did not return text or a pet tool call")
        return {
            "id": "resp_" + secrets.token_hex(12),
            "object": "response",
            "created_at": int(time.time()),
            "status": "completed",
            "error": None,
            "incomplete_details": None,
            "model": model,
            "output": output,
            "usage": None,
        }
    finally:
        rpc.close()


def parent_alive(pid):
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel.CloseHandle(handle)
