"""Read a bounded local session snapshot without replaying historical alerts."""
from __future__ import annotations

import json
import math
from pathlib import Path
import time

MAX_BYTES = 1024 * 1024
STALE_SECONDS = 1800
STATES = {'thinking', 'working', 'waiting', 'stopped', 'interrupted', 'closed'}


class SnapshotReader:
    def __init__(self, path, *, clock=time.time, codex_home=None):
        self.path = Path(path).expanduser()
        self.clock = clock
        self._signature = None
        self._records = {}
        self._previous = None
        self._seen = None
        self._started_at = clock()
        from .codex_transcript import TranscriptDetails
        self._details = TranscriptDetails(codex_home or self.path.parent.parent)
        self._metadata = {}
        self._session_starts = {}
        self._pending_finals = {}

    def read(self):
        now = self.clock()
        try:
            stat = self.path.stat()
            signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
            if signature != self._signature:
                with self.path.open('rb') as stream:
                    raw = stream.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise ValueError('snapshot too large')
                data = json.loads(raw)
                records = data['sessions']
                if data['version'] != 1 or not isinstance(records, dict) or len(records) > 128:
                    raise ValueError('unsupported snapshot')
                for record in records.values():
                    stamp = record['updated_at']
                    if record['state'] not in STATES or not isinstance(stamp, (int, float)) or not math.isfinite(stamp):
                        raise ValueError('invalid session')
                self._records, self._signature = records, signature
                metadata = data.get('metadata', {})
                self._metadata = metadata if isinstance(metadata, dict) else {}
            records = self._records
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            # Missing, malformed or unreadable is not successful completion.
            self._signature = None
            if self._seen is None:
                self._seen = {}
            return self._changed({'busy': 0, 'waiting': 0, 'unknown': 1,
                                  'thinking': 0, 'tool': '', 'ended': []})
        summary = dict(busy=0, waiting=0, unknown=0, thinking=0, tool='', ended=[])
        seen = {}
        for key, entry in records.items():
            state = entry['state']
            seen[key] = state
            if state in {'thinking', 'working', 'waiting'}:
                if now - entry['updated_at'] > STALE_SECONDS or entry['updated_at'] > now + 60:
                    summary['unknown'] += 1
                elif state == 'waiting':
                    summary['waiting'] += 1
                else:
                    summary['busy'] += 1
                    summary['thinking'] += state == 'thinking'
                    summary['tool'] = str(entry.get('tool') or '')[:64]
            elif self._seen is not None and self._seen.get(key) not in {'stopped', 'interrupted', 'closed'}:
                if key in self._seen or entry['updated_at'] >= self._started_at:
                    summary['ended'].append(state)
                    if state == 'stopped':
                        self._pending_finals[key] = now + 10
        self._pending_finals = {
            key: deadline for key, deadline in self._pending_finals.items()
            if deadline >= now and records.get(key, {}).get('state') == 'stopped'
        }
        summary['detail'] = self._details.read(records, self._metadata, now, self._pending_finals)
        finals = self._details.finals
        if finals:
            summary['final'] = finals[-1]
            for item in finals:
                self._pending_finals.pop(item['key'], None)
        opened = []
        starts = {}
        for key, meta in self._metadata.items():
            if not isinstance(meta, dict):
                continue
            stamp = meta.get('started_at', 0)
            if not isinstance(stamp, (int, float)):
                continue
            starts[key] = stamp
            if (self._started_at <= stamp <= now + 60 and self._session_starts.get(key) != stamp
                    and meta.get('source') in ('startup', 'resume', 'clear')):
                sid = meta.get('id')
                if isinstance(sid, str):
                    opened.append(self._details.session_title(meta, now))
        self._session_starts = starts
        summary['opened'] = opened
        self._seen = seen
        return self._changed(summary)

    def expiry_delay(self):
        """Deadline-driven state expiry; this is not a file polling interval."""
        if self._signature is None:
            return None
        now = self.clock()
        deadlines = list(self._pending_finals.values())
        deadlines.extend(entry['updated_at'] + STALE_SECONDS + .01
                         for entry in self._records.values()
                         if entry['state'] in {'thinking', 'working', 'waiting'}
                         and entry['updated_at'] + STALE_SECONDS + .01 > now)
        return max(.01, min(deadlines) - now) if deadlines else None

    def watch_paths(self):
        paths = {str(self.path), str(self._details.index.path)}
        for cached in self._details.turns.values():
            reader = cached.get('reader')
            if reader is not None:
                paths.add(str(reader.path))
        return sorted(paths)

    def has_backlog(self):
        if self._signature is None:
            return False
        readers = [self._details.index]
        readers.extend(cached['reader'] for cached in self._details.turns.values()
                       if cached.get('reader') is not None)
        return any(reader.more for reader in readers)

    def _changed(self, summary):
        stable = {key: value for key, value in summary.items() if key not in {'ended', 'opened', 'final'}}
        if stable == self._previous and not summary['ended'] and not summary.get('opened') and not summary.get('final'):
            return None
        self._previous = stable
        return summary
