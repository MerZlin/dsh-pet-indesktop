#!/usr/bin/env python3
"""Fail-open Codex hook writer. Standard library only; never emits model context."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import time

if 'diagnostic' not in globals():
    diagnostic = runpy.run_path(str(Path(__file__).with_name('diagnostics.py')))['diagnostic']

MAX_BYTES = 1024 * 1024
MAX_SESSIONS = 128
TERMINAL = {'stopped', 'interrupted', 'closed'}
EVENTS = {'SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'PermissionRequest',
          'Stop', 'Interrupt', 'SessionEnd'}


def recent(records, budget):
    result = {}
    for key, entry in sorted(records.items(), key=lambda item: item[1]['updated_at'], reverse=True)[:MAX_SESSIONS]:
        size = len(json.dumps({key: entry}, ensure_ascii=False).encode('utf-8'))
        if size <= budget:
            result[key] = entry
            budget -= size
    return result


def directory() -> Path:
    override = os.environ.get('DSH_PET_BRIDGE_DIR')
    if override:
        return Path(override).expanduser()
    # Shared by the three local clients; independent of plugin cache/version.
    return Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex') / 'dsh-pet-bridge'


@contextmanager
def locked(path):
    with path.open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        deadline = time.monotonic() + 0.5
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('bridge lock busy')
                time.sleep(0.005)
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def record(payload: dict, root: Path) -> bool:
    event = payload.get('hook_event_name')
    session = str(payload.get('session_id') or '')[:200]
    turn = str(payload.get('turn_id') or '')[:200]
    if event not in EVENTS or not session or (not turn and event not in {'SessionStart', 'SessionEnd'}):
        diagnostic('invalid_event_fields', event=event if isinstance(event, str) else '')
        return False
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with locked(root / 'state.lock'):
        path = root / 'state.json'
        try:
            with path.open('rb') as stream:
                data = json.loads(stream.read(MAX_BYTES + 1))
            if data.get('version') != 1 or not isinstance(data.get('sessions'), dict):
                diagnostic('unsupported_snapshot', event=event)
                return False  # A newer/corrupt schema must not be silently overwritten.
        except FileNotFoundError:
            data = {'version': 1, 'sessions': {}}
        sessions = data['sessions']
        session_key = hashlib.sha256(session.encode()).hexdigest()[:32]
        key = hashlib.sha256((session + '\0' + turn).encode()).hexdigest()[:32]
        now = time.time()
        metadata = data.setdefault('metadata', {})
        meta = metadata.setdefault(session_key, {'id': session})
        for field in ('transcript_path', 'cwd'):
            value = payload.get(field)
            if isinstance(value, str) and value:
                meta[field] = value[:4096]
        meta['updated_at'] = now
        if event == 'SessionStart':
            meta['source'] = str(payload.get('source') or '')[:32]
            meta['started_at'] = now
        elif event == 'SessionEnd':
            for entry in sessions.values():
                if entry['session'] == session_key and entry['state'] not in TERMINAL:
                    entry.update(state='closed', updated_at=now, event=event)
        else:
            entry = sessions.get(key)
            if entry is None:
                # Ignore child/tool events without a root prompt. This also avoids
                # treating a late completion from before installation as a new task.
                if event != 'UserPromptSubmit':
                    return False
                entry = {'session': session_key, 'state': 'thinking', 'calls': {}, 'tool': ''}
                entry.update(turn_id=turn, started_at=now, transcript_offset=0)
                try:
                    transcript = Path(meta.get('transcript_path', ''))
                    if transcript.is_file():
                        entry['transcript_offset'] = transcript.stat().st_size
                except OSError:
                    pass
                sessions[key] = entry
            elif entry['state'] in TERMINAL:
                return False  # A delayed tool hook must not revive a terminal turn.
            elif event == 'UserPromptSubmit':
                return False  # Duplicate delivery must not reset work or waiting.
            call_id = str(payload.get('tool_use_id') or '')
            call = hashlib.sha256(call_id.encode()).hexdigest()[:32] if call_id else ''
            tool = str(payload.get('tool_name') or '').lower()
            tool = {'bash': 'bash', 'apply_patch': 'edit', 'read': 'read',
                    'web_search': 'search'}.get(tool, 'tool')
            if event == 'UserPromptSubmit':
                entry['state'] = 'thinking'
            elif event == 'PreToolUse':
                if call and len(entry['calls']) < 128:
                    entry['calls'][call] = tool
                if entry['state'] != 'waiting':
                    entry.update(state='working', tool=tool)
            elif event == 'PostToolUse':
                entry['calls'].pop(call, None)
                waiting = entry.get('waiting_calls', {})
                if call:
                    waiting.pop(call, None)
                if entry['state'] != 'waiting' or (call and 'waiting_calls' in entry and not waiting):
                    entry.update(state='working' if entry['calls'] else 'thinking', tool='')
            elif event == 'PermissionRequest':
                waiting = entry.setdefault('waiting_calls', {})
                if len(waiting) < 128:
                    waiting[call] = tool
                entry.update(state='waiting', tool=tool)
            else:
                entry.update(state='stopped' if event == 'Stop' else 'interrupted', calls={}, waiting_calls={}, tool='')
            entry.update(event=event, updated_at=now)
        # Bounded snapshot: keep the most recently updated turns, never transcripts.
        data['sessions'] = recent(sessions, (MAX_BYTES - 1024) * 2 // 3)
        data['metadata'] = recent(metadata, (MAX_BYTES - 1024) // 3)
        fd, temporary = tempfile.mkstemp(prefix='.state-', dir=root)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(data, stream, ensure_ascii=False, separators=(',', ':'))
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return True


def main() -> int:
    phase = 'enabled_check'
    event = ''
    try:
        # A client may retain a hook definition after uninstall. The immutable
        # runtime stays available, but disabled integrations do no work.
        if '--bridge-enabled-file' in sys.argv:
            marker = Path(sys.argv[sys.argv.index('--bridge-enabled-file') + 1])
            if not marker.is_file():
                # Drain without retaining content so the host can finish its
                # stdin write even for a payload larger than the pipe buffer.
                while sys.stdin.buffer.read(65536):
                    pass
                print('{}')
                return 0
        phase = 'parse_input'
        raw = sys.stdin.buffer.read(MAX_BYTES + 1)
        if len(raw) <= MAX_BYTES:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                event = payload.get('hook_event_name', '')
                event = event if isinstance(event, str) else ''
                phase = 'write_state'
                changed = record(payload, directory())
                if changed and event in ('SessionStart', 'UserPromptSubmit'):
                    diagnostic('state_updated', event=event)
            else:
                diagnostic('invalid_payload')
        else:
            while sys.stdin.buffer.read(65536):
                pass
            diagnostic('input_too_large')
    except BaseException as exc:
        # Telemetry must never reject a prompt, tool or permission request.
        diagnostic(phase, exc, event=event)
    try:
        print('{}')  # Stop hooks require JSON; no decisions or additionalContext.
        sys.stdout.flush()
    except BaseException:
        pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
