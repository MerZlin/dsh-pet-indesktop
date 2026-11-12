"""Read-only native-clock/Qt-heartbeat probe; never controls the music player.

python scripts/bench_netease_position.py --samples 40 --interval .1 --output result.json
Add --smtc to also check metadata matching against one real system-media sample.
"""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import statistics
import struct
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psutil
from PySide6.QtCore import QCoreApplication, QTimer, Qt

from pet.music.netease_native import Reader, SnapshotUnavailable, native_api
from pet.music.netease_position import Metadata, NetEasePositionSource


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=int, default=40)
    parser.add_argument('--interval', type=float, default=.1)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--smtc', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.samples <= 10000 or not 0 <= args.interval <= 10:
        parser.error('invalid sample count or interval')
    result = {'completed': False, 'planned': args.samples, 'observed': [], 'unavailable': [], 'errors': []}
    process = psutil.Process()

    def resources():
        memory = process.memory_info()
        return {'handles': process.num_handles(), 'rss': memory.rss, 'private': memory.private}

    result['before'] = resources()
    finished = threading.Event()
    heartbeats = []
    last = time.perf_counter()

    def beat():
        nonlocal last
        now = time.perf_counter()
        heartbeats.append((now - last) * 1000)
        last = now
        if finished.is_set():
            app.quit()

    def run():
        reader = None
        try:
            start = time.perf_counter()
            api = native_api()
            pid = api.player_pid()
            result['player_pid'] = pid
            if pid is None:
                result['errors'].append('no_unique_player_window')
                return
            reader = Reader(api, pid)
            result['connect_ms'] = (time.perf_counter() - start) * 1000
            result['connected'] = resources()
            for _ in range(args.samples):
                start = time.perf_counter()
                try:
                    sample = reader.sample()
                except SnapshotUnavailable as exc:
                    result['unavailable'].append({
                        'reason': str(exc), 'sampled_at': time.monotonic(),
                        'position': struct.unpack('<d', api.read(reader.handle, reader.base + reader.layout.position_rva, 8))[0],
                        'duration': struct.unpack('<d', api.read(reader.handle, reader.base + reader.layout.player_rva + 0xa8, 8))[0],
                        'state': struct.unpack('<i', api.read(reader.handle, reader.base + reader.layout.player_rva + 0x60, 4))[0],
                    })
                    finished.wait(args.interval)
                    continue
                result['observed'].append({
                    'position': sample.position, 'duration': sample.duration, 'playing': sample.playing,
                    'elapsed_ms': (time.perf_counter() - start) * 1000,
                    'has_song_id': bool(sample.song_id),
                    'sampled_at': sample.sampled_at,
                    'song_key': hashlib.sha256(sample.song_id.encode()).hexdigest()[:12],
                    'instance_key': hashlib.sha256(sample.token.encode()).hexdigest()[:12],
                })
                finished.wait(args.interval)
            result['sampled'] = resources()
            if args.smtc:
                from pet.music.now_playing import get_now_playing
                playback = get_now_playing(allow_browser=False)
                result['smtc_has_track'] = playback is not None
                if playback is not None:
                    result['same_player'] = playback.app_id.casefold() == 'cloudmusic.exe'
                    result['metadata_matches'] = Metadata().matching_track(sample.song_id, playback.track)
                    enhancement = NetEasePositionSource()
                    try:
                        timings = []
                        for _ in range(args.samples):
                            start = time.perf_counter()
                            enriched = enhancement.enrich(replace(playback, position=None))
                            timings.append((time.perf_counter() - start) * 1000)
                        result['enrichment_status'] = enhancement.status
                        result['enriched_position'] = enriched.position
                        result['enrich_connect_ms'] = timings[0]
                        result['enrich_warm_mean_ms'] = statistics.mean(timings[1:]) if len(timings) > 1 else None
                        result['enrich_warm_max_ms'] = max(timings[1:], default=0)
                    finally:
                        enhancement.close()
            result['completed'] = len(result['observed']) + len(result['unavailable']) == args.samples
        except Exception as exc:
            result['errors'].append(type(exc).__name__ + ':' + str(exc))
        finally:
            if reader is not None:
                reader.close()
            result['closed'] = resources()
            finished.set()

    app = QCoreApplication([])
    timer = QTimer()
    timer.setTimerType(Qt.TimerType.PreciseTimer)
    timer.setInterval(10)
    timer.timeout.connect(beat)
    timer.start()
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    QTimer.singleShot(60000, app.quit)
    app.exec()
    result['heartbeat_count'] = len(heartbeats)
    result['heartbeat_max_ms'] = max(heartbeats, default=0)
    times = [item['elapsed_ms'] for item in result['observed']]
    result['sample_mean_ms'] = statistics.mean(times) if times else None
    result['sample_max_ms'] = max(times, default=0)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'observed'}, indent=2))
    return 0 if result['completed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
