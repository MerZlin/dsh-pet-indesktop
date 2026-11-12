"""Supplement missing native NetEase timelines without changing SMTC selection.

Owned by the existing lyric sampling thread. No injection, process writes,
network requests, UI automation, or account database access. The only metadata
files read are playingList and fmPlay, and only to match the sampled song ID.
"""
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import sys
import time

from .netease_native import Reader, SnapshotUnavailable, native_api

IS_WINDOWS = sys.platform == 'win32'
RETRY_SECONDS = 5.0
MAX_METADATA_BYTES = 8 * 1024 * 1024


def _normal(value):
    return str(value or '').strip().casefold()


def _artists(value):
    return tuple(part.strip().casefold() for part in re.split(r'[/、,，;&]', value) if part.strip())


class Metadata:
    def __init__(self, directory=None):
        self.directory = directory or Path(os.environ.get('LOCALAPPDATA', '')) / 'NetEase/CloudMusic/WebData/file'
        self.identity = None
        self.track = None
        self.stamps = {}

    def matching_track(self, identity, expected):
        if identity != self.identity:
            self.identity, self.track = identity, None
            self.stamps.clear()
        if self.track is None:
            for filename, collection in [('playingList', 'list'), ('fmPlay', 'queue')]:
                path = self.directory / filename
                try:
                    stat = path.stat()
                    stamp = (stat.st_mtime_ns, stat.st_size)
                    if self.stamps.get(filename) == stamp or stat.st_size > MAX_METADATA_BYTES:
                        continue
                    with path.open('rb') as stream:
                        data = stream.read(MAX_METADATA_BYTES + 1)
                    if len(data) > MAX_METADATA_BYTES:
                        continue
                    document = json.loads(data)
                    entries = document.get(collection, []) if isinstance(document, dict) else []
                    if not isinstance(entries, list):
                        continue
                    for entry in entries:
                        if not isinstance(entry, dict) or str(entry.get('id', '')) != identity:
                            continue
                        track = entry.get('track') if filename == 'playingList' else entry
                        if isinstance(track, dict):
                            self.track = track
                            break
                    self.stamps[filename] = stamp
                except (OSError, ValueError):
                    continue  # a concurrent/truncated write must remain retryable
                if self.track is not None:
                    break
        track = self.track
        if track is None or _normal(track.get('name')) != _normal(expected.title):
            return False
        artists = track.get('artists', [])
        if not isinstance(artists, list):
            return False
        actual = tuple(_normal(artist.get('name')) for artist in artists
                       if isinstance(artist, dict) and _normal(artist.get('name')))
        reported = _artists(expected.artist)
        if not actual or not reported or not (set(actual) == set(reported) or reported == actual[:1]):
            return False
        album = track.get('album', {})
        actual_album = _normal(album.get('name')) if isinstance(album, dict) else ''
        return not expected.album or not actual_album or _normal(expected.album) == actual_album


class NetEasePositionSource:
    def __init__(self, metadata=None):
        self.metadata = metadata or Metadata()
        self.reader = None
        self.retry_at = 0.0
        self.status = 'idle'

    def close(self):
        if self.reader is not None:
            self.reader.close()
            self.reader = None

    def enrich(self, playback):
        from .now_playing import Playback

        if (not IS_WINDOWS or not isinstance(playback, Playback)
                or playback.app_id.casefold() != 'cloudmusic.exe' or playback.position is not None):
            self.close()
            self.status = 'not_needed'
            return playback
        if time.monotonic() < self.retry_at:
            return playback
        try:
            native = native_api()
            pid = native.player_pid()
            if pid is None:
                self.close()
                self.status = 'no_unique_player_window'
                return playback
            if self.reader is not None and (self.reader.pid != pid or not native.alive(self.reader.handle)):
                self.close()
            if self.reader is None:
                self.reader = Reader(native, pid)
            sample = self.reader.sample()
            if sample.playing != playback.track.playing:
                self.status = 'state_transition'
                return playback
            if not self.metadata.matching_track(sample.song_id, playback.track):
                self.status = 'metadata_mismatch'
                return playback
            self.status = 'native'
            return replace(playback, position=sample.position, updated_at=sample.sampled_at,
                           track=replace(playback.track, duration=sample.duration))
        except SnapshotUnavailable:
            self.status = 'snapshot_transition'
            return playback
        except (OSError, ValueError, OverflowError):
            self.close()
            self.status = 'native_unavailable'
            self.retry_at = time.monotonic() + RETRY_SECONDS
            return playback
