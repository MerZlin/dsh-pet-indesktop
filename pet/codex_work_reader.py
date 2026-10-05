"""Read local Codex metadata/events without generation or modifying Codex files."""

from __future__ import annotations
import hashlib, json, os, sqlite3, time
from pathlib import Path

NOTICES = frozenset(("completed", "attention", "failed"))
ACTIVE = frozenset(("thinking", "coding"))


class CodexWorkReader:
    def __init__(self, codex_root=None, hook_root=None):
        self.root = Path(codex_root or os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        self.hook_root = Path(hook_root) if hook_root else None
        self.cursors = {}

    @staticmethod
    def _connect(path):
        conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=0.15)
        conn.execute("PRAGMA query_only=ON")
        return conn

    def _questions(self, thread, path, turn):
        p = Path(path)
        key = (thread, turn)
        state = self.cursors.get(key)
        try:
            size = p.stat().st_size
            if state is None or state["pos"] > size:
                pos = max(0, size - 1024 * 1024)
                state = {"pos": pos, "fragment": b"", "pending": None}
                self.cursors[key] = state
                if pos:
                    with p.open("rb") as stream:
                        stream.seek(pos)
                        stream.readline()
                        state["pos"] = stream.tell()
            with p.open("rb") as stream:
                stream.seek(state["pos"])
                data = stream.read(1024 * 1024)
                state["pos"] = stream.tell()
            data = state["fragment"] + data
            lines = data.split(b"\n")
            state["fragment"] = lines.pop()
            for line in lines:
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                payload = event.get("payload") or {}
                kind = payload.get("type")
                if event.get("type") == "event_msg" and kind in ("task_started", "task_complete", "turn_aborted"):
                    state["pending"] = None
                if event.get("type") != "response_item":
                    continue
                if kind == "message" and payload.get("role") == "user":
                    state["pending"] = None
                if kind in ("function_call", "custom_tool_call"):
                    name = str(payload.get("name", "")).lower()
                    # Use actual tool names; never inspect prompts, tool arguments or output.
                    if "request_user_input" in name or name.endswith("request_permissions"):
                        state["pending"] = {"id": str(payload.get("call_id") or payload.get("id") or event.get("timestamp")), "async": "async" in name}
                pending = state["pending"]
                if pending and not pending["async"] and kind in ("function_call_output", "custom_tool_call_output") and payload.get("call_id") == pending["id"]:
                    state["pending"] = None
            return state["pending"]
        except (OSError, ValueError):
            return None

    def _hook_attention(self, thread, started, completed):
        if self.hook_root is None:
            return None
        path = self.hook_root / (hashlib.sha256(thread.encode()).hexdigest() + ".json")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            stamp = float(value["ts"])
            if value.get("state") == "attention" and stamp >= started and not (completed and completed >= stamp):
                return "permission:" + str(stamp)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def read(self, now=None):
        now = time.time() if now is None else now
        records = []
        try:
            with self._connect(self.root / "state_5.sqlite") as metadata, self._connect(self.root / "thread_history_1.sqlite") as history:
                rows = metadata.execute(
                    "SELECT id,COALESCE(NULLIF(name,''),title),rollout_path,updated_at,source FROM threads "
                    "WHERE archived=0 AND COALESCE(source,'') NOT LIKE '%subagent%' "
                    "AND (agent_path IS NULL OR agent_path='' OR agent_path='/root') ORDER BY updated_at DESC"
                ).fetchall()
                for thread, title, path, updated, source in rows:
                    turns = history.execute(
                        "SELECT turn_id,status,started_at,completed_at FROM thread_turns WHERE thread_id=? ORDER BY started_at DESC LIMIT 1", (thread,)
                    ).fetchone()
                    if not turns:
                        continue
                    turn, status, started, completed = turns
                    started = started or updated
                    # A long running turn must not disappear behind newer idle threads.
                    # The recency cutoff applies only to terminal notifications.
                    if status != "inProgress" and now - max(started, completed or 0) > 12 * 3600:
                        continue
                    stamp = completed or started
                    state = (
                        "completed" if status == "completed" else "failed" if status == "failed" else "interrupted" if status == "interrupted" else "thinking"
                    )
                    item = history.execute(
                        "SELECT item_type,created_at_ms,item_json FROM thread_items WHERE thread_id=? AND turn_id=? ORDER BY created_at_ms DESC,rollout_ordinal DESC LIMIT 1",
                        (thread, turn),
                    ).fetchone()
                    if status == "inProgress" and item:
                        kind, created, raw = item
                        stamp = (created or started * 1000) / 1000
                        if kind in ("commandExecution", "fileChange", "mcpToolCall", "webSearch", "imageView", "subAgentActivity", "collabAgentToolCall"):
                            state = "coding"
                        elif kind == "agentMessage":
                            # Structured questions carry only the fact that input is requested.
                            try:
                                value = json.loads(raw)
                                if value.get("questions"):
                                    state = "attention"
                            except ValueError:
                                pass
                    attention = None
                    if status == "inProgress":
                        pending = self._questions(thread, path, turn)
                        attention = (pending or {}).get("id") or self._hook_attention(thread, started, completed)
                        if attention:
                            state = "attention"
                    signature = turn + ":" + state + ((":" + attention) if attention else "")
                    records.append(
                        {"threadId": thread, "turnId": turn, "title": str(title).strip()[:240], "state": state, "signature": signature, "timestamp": stamp}
                    )
        except (sqlite3.Error, OSError, ValueError):
            return {"ok": False, "updatedAt": now, "rows": []}
        current_turns = {(r["threadId"], r["turnId"]) for r in records}
        self.cursors = {key: value for key, value in self.cursors.items() if key in current_turns}
        records.sort(key=lambda r: ({"attention": 0, "coding": 1, "thinking": 1, "failed": 2, "completed": 3}.get(r["state"], 4), -r["timestamp"]))
        return {"ok": True, "updatedAt": now, "rows": records}


class NoticeLedger:
    """Remember exactly which completion/input transitions have been viewed."""

    def __init__(self, path):
        self.path = Path(path)
        self.seen = {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.seen = {str(k): str(v) for k, v in data.get("seen", {}).items()}
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def visible(self, rows):
        return [r for r in rows if r["state"] in ACTIVE or (r["state"] in NOTICES and self.seen.get(r["threadId"]) != r["signature"])]

    def mark(self, rows):
        changed = False
        for r in rows:
            if r["state"] in NOTICES and self.seen.get(r["threadId"]) != r["signature"]:
                self.seen[r["threadId"]] = r["signature"]
                changed = True
        if changed:
            self.seen = dict(list(self.seen.items())[-200:])
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temp = self.path.with_suffix(".tmp")
                temp.write_text(json.dumps({"version": 1, "seen": self.seen}, ensure_ascii=True), encoding="utf-8")
                os.replace(temp, self.path)
            except OSError:
                pass
        return changed
