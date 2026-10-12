"""Bounded, read-only enrichment of Codex hooks from local rollout files.

Rollouts are an internal format: unknown records never affect lifecycle. Only
public assistant commentary and final replies are retained in memory.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import stat

READ_BYTES = 64 * 1024
MAX_LINE = 256 * 1024


def short_text(value, limit=240):
    if not isinstance(value, str):
        return ''
    # QLabel may auto-detect rich text; display transcript markup literally.
    text = ' '.join(value.split()).replace('<', '‹').replace('>', '›')
    return text if len(text) <= limit else text[:limit - 1] + '…'


def activity_label(entry):
    if entry['state'] == 'thinking':
        return '思考中'
    if entry['state'] == 'waiting':
        return '等待处理'
    calls = entry.get('calls', {})
    tools = [value for value in calls.values() if isinstance(value, str)] if isinstance(calls, dict) else []
    tools.append(entry.get('tool'))
    for tool, label in (('bash', '运行命令'), ('edit', '修改文件'), ('read', '读取文件'), ('search', '搜索资料')):
        if tool in tools:
            return label
    return '调用工具'


class JsonlReader:
    def __init__(self, path, offset=0, *, backfill=False):
        self.path = Path(path)
        self.offset = max(0, offset)
        self.identity = None
        self.pending = b''
        self.discard = False
        self.backfill = backfill
        self.more = False

    def read(self, budget=READ_BYTES):
        self.more = False
        try:
            info = self.path.stat()
            if not stat.S_ISREG(info.st_mode):
                return []
            identity = (info.st_dev, info.st_ino)
            if (self.identity is not None and identity != self.identity) or info.st_size < self.offset:
                # Replacement/truncation is not an invitation to replay history.
                self.offset = max(0, info.st_size - MAX_LINE) if self.backfill else info.st_size
                self.pending = b''
                self.discard = self.backfill and self.offset > 0
            self.identity = identity
            if info.st_size == self.offset:
                return []
            with self.path.open('rb') as stream:
                stream.seek(self.offset)
                raw = stream.read(budget)
                self.offset += len(raw)
                self.more = bool(raw) and self.offset < info.st_size
        except (OSError, ValueError):
            return []
        lines = (self.pending + raw).split(b'\n')
        self.pending = lines.pop()
        records = []
        for line in lines:
            if self.discard:
                self.discard = False
                continue
            if len(line) > MAX_LINE:
                continue
            try:
                record = json.loads(line)
                if isinstance(record, dict):
                    records.append(record)
            except (ValueError, RecursionError):
                pass
        if len(self.pending) > MAX_LINE:
            self.pending = b''
            self.discard = True
        return records


class TranscriptDetails:
    def __init__(self, home):
        self.home = Path(home)
        index = Path(home) / 'session_index.jsonl'
        # Titles are optional; bounded startup backfill, then append-only reads.
        try:
            offset = max(0, index.stat().st_size - MAX_LINE)
        except OSError:
            offset = 0
        self.index = JsonlReader(index, offset, backfill=True)
        self.index.discard = bool(offset)
        self.titles = {}
        self.turns = {}
        self._located = {}

    def _locate(self, sid, now):
        """Optional fallback for delayed/null hook paths and archived rollouts."""
        previous = self._located.get(sid)
        if previous and now - previous[0] < 15:
            return previous[1]
        result = ('', '')
        try:
            databases = sorted(self.home.glob('state_*.sqlite'), key=lambda path: path.stat().st_mtime, reverse=True)
            for database in databases[:2]:
                with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.02)) as db:
                    row = db.execute('SELECT rollout_path, title FROM threads WHERE id = ?', (sid,)).fetchone()
                    if row:
                        result = (row[0] if isinstance(row[0], str) else '', short_text(row[1], 80))
                        break
        except (OSError, sqlite3.Error):
            pass
        self._located[sid] = (now, result)
        while len(self._located) > 128:
            self._located.pop(next(iter(self._located)))
        return result

    def session_title(self, meta, now):
        sid, cwd = meta.get('id'), meta.get('cwd')
        title = ''
        if isinstance(sid, str) and sid:
            title = self.titles.get(sid) or self._locate(sid, now)[1]
        project = short_text(Path(cwd).name, 80) if isinstance(cwd, str) else ''
        return title or project or 'Codex 会话'

    def read(self, records, metadata, now, terminal_keys=()):
        self.finals = []
        for item in self.index.read():
            sid, title = item.get('id'), short_text(item.get('thread_name'), 80)
            if isinstance(sid, str) and title:
                self.titles.pop(sid, None)
                self.titles[sid] = title
        while len(self.titles) > 1024:
            self.titles.pop(next(iter(self.titles)))
        active = sorted(((key, entry) for key, entry in records.items()
                         if (entry['state'] in {'thinking', 'working', 'waiting'} or key in terminal_keys)
                         and -60 <= now - entry['updated_at'] <= 1800),
                        key=lambda pair: (pair[1]['state'] == 'waiting', pair[1]['updated_at']), reverse=True)[:16]
        keys = {key for key, _ in active}
        self.turns = {key: value for key, value in self.turns.items() if key in keys}
        details = []
        for key, entry in active:
            session = entry.get('session')
            if not isinstance(session, str):
                continue
            meta = metadata.get(session, {})
            if not isinstance(meta, dict):
                continue
            sid = meta.get('id', '')
            if not isinstance(sid, str) or not sid:
                continue
            path = meta.get('transcript_path')
            located_title = ''
            try:
                available = isinstance(path, str) and Path(path).is_file()
            except (OSError, ValueError):
                available = False
            if sid and (not available or sid not in self.titles):
                located_path, located_title = self._locate(sid, now)
                path = path if available else located_path or path
            cwd = meta.get('cwd')
            project = short_text(Path(cwd).name, 80) if isinstance(cwd, str) else ''
            title = self.titles.get(sid, '') or located_title or project or 'Codex 会话'
            cached = self.turns.setdefault(key, dict(reader=None, text='', kind='', final='', at=0, context=''))
            if isinstance(path, str) and path and cached['reader'] is None:
                offset = entry.get('transcript_offset', 0)
                offset = offset if isinstance(offset, int) else 0
                try:
                    start = max(offset, Path(path).stat().st_size - MAX_LINE)
                except (OSError, ValueError):
                    start = offset
                cached['reader'] = JsonlReader(path, start)
                cached['reader'].discard = start > offset
            elif isinstance(path, str) and path and str(cached['reader'].path) != path:
                # SQLite can point at the same rollout after archival. Preserve
                # the cursor; replacement identity is checked by the reader.
                cached['reader'].path = Path(path)
            if cached['reader'] is not None:
                for record in cached['reader'].read(READ_BYTES // max(1, len(active))):
                    self._consume(record, entry, cached, now)
            if key in terminal_keys:
                if cached['final']:
                    self.finals.append(dict(key=key, title=title, text=cached['final']))
                continue
            details.append(dict(key=key, title=title, text=cached['text'], kind=cached['kind'],
                                state=entry['state'], activity=activity_label(entry),
                                at=max(cached['at'], entry['updated_at'])))
        if not details:
            return {}
        chosen = max(details, key=lambda item: (item['state'] == 'waiting', item['at']))
        return {key: value for key, value in chosen.items() if key != 'at'}

    @staticmethod
    def _consume(record, entry, cached, now):
        payload = record.get('payload')
        if not isinstance(payload, dict):
            return
        kind = record.get('type')
        if kind == 'turn_context' or (kind == 'event_msg' and payload.get('type') == 'task_started'):
            cached['context'] = payload.get('turn_id') or ''
            return
        if cached['context'] and cached['context'] != entry.get('turn_id'):
            return
        timestamp = record.get('timestamp')
        if isinstance(timestamp, str):
            try:
                started = entry.get('started_at', 0)
                if not isinstance(started, (int, float)):
                    return
                if datetime.fromisoformat(timestamp.replace('Z', '+00:00')).timestamp() < started:
                    return
            except (ValueError, OverflowError):
                return
        if kind == 'event_msg' and payload.get('type') == 'item_completed':
            payload = payload.get('item')
            if not isinstance(payload, dict):
                return
        item_type = payload.get('type')
        if not isinstance(item_type, str):
            return
        text = ''
        if item_type in {'message', 'AgentMessage'}:
            if payload.get('phase') not in {'commentary', 'final', 'final_answer'} or payload.get('role', 'assistant') != 'assistant':
                return
            content = payload.get('content', [])
            if isinstance(content, list):
                text = ' '.join(part.get('text', '') for part in content
                                if isinstance(part, dict) and isinstance(part.get('text'), str))
            elif isinstance(content, str):
                text = content
        # Hooks describe current activity; persisted tool calls/results may be
        # delayed and must not replace commentary or announce completed work.
        # Never extract encrypted/private reasoning, tool output or user text.
        text = short_text(text)
        if text and payload.get('phase') in {'final', 'final_answer'}:
            cached['final'] = text
            return
        if text and text != cached['text']:
            cached.update(text=text, kind='commentary', at=now)
