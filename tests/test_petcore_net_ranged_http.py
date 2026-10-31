"""分段下载的 HTTP 接线：Range 预检、206 校验、200 回退、429、取消与镜像竞速。

全部走 loopback ``ThreadingHTTPServer``，不外网；时序用 Event/宽预算，不用 sleep 猜。
"""

from __future__ import annotations

import hashlib
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from petcore.contracts.http import TransferSpec
from petcore.market.errors import TransferCancelled
from petcore.net import errors, market
from petcore.net import market_transport

MIB = 1024 * 1024
PAYLOAD = bytes((index * 101 + 7) % 251 for index in range(5 * MIB))
TOTAL = len(PAYLOAD)


class _State:
    def __init__(self) -> None:
        self.payload = PAYLOAD
        self.lock = threading.Lock()
        self.requests: list[tuple[str, str]] = []
        self.cancelled = threading.Event()
        self.throttle_bytes_per_second = 0
        self.delays: dict[str, float] = {}
        self.flips = 0
        self.flip_after = 3

    def record(self, path: str, range_header: str) -> None:
        with self.lock:
            self.requests.append((path, range_header))

    def ranges(self, path: str) -> list[str]:
        with self.lock:
            return [value for name, value in self.requests if name == path]

    def count(self, path: str) -> int:
        with self.lock:
            return sum(1 for name, _value in self.requests if name == path)


def _range_start(value: str) -> tuple[int, int] | None:
    if not value.startswith("bytes="):
        return None
    spec = value[len("bytes=") :]
    if spec.endswith("-"):
        return None
    first, _, last = spec.partition("-")
    try:
        return int(first), int(last)
    except ValueError:
        return None


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *_args):  # noqa: D401 - 测试静音
        return None

    def _head(self, status: int, body_length: int, headers=None) -> None:
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(body_length))
        self.end_headers()

    def _write(self, body: bytes) -> None:
        state: _State = self.server.state  # type: ignore[attr-defined]
        rate = state.throttle_bytes_per_second
        if rate <= 0:
            try:
                self.wfile.write(body)
            except OSError:
                self.close_connection = True
            return
        block = max(1024, rate // 20)
        for offset in range(0, len(body), block):
            try:
                self.wfile.write(body[offset : offset + block])
                self.wfile.flush()
            except OSError:
                self.close_connection = True
                return
            time.sleep(block / rate)

    def do_GET(self):  # noqa: N802
        state: _State = self.server.state  # type: ignore[attr-defined]
        path = self.path.split("?", 1)[0]
        range_header = self.headers.get("Range", "")
        state.record(path, range_header)
        delay = state.delays.get(path, 0.0)
        if delay:
            time.sleep(delay)
        payload = state.payload

        if path in ("/artifact.bin", "/throttled.bin", "/mirror-fast.bin", "/mirror-slow.bin"):
            parsed = _range_start(range_header)
            if parsed is None:
                self._head(200, len(payload), {"Accept-Ranges": "bytes"})
                self._write(payload)
                return
            start, last = parsed
            if start >= len(payload):
                self._head(416, 0, {"Content-Range": f"bytes */{len(payload)}"})
                return
            last = min(last, len(payload) - 1)
            body = payload[start : last + 1]
            self._head(
                206,
                len(body),
                {"Content-Range": f"bytes {start}-{last}/{len(payload)}"},
            )
            self._write(body)
            return
        if path == "/norange.bin":
            self._head(200, len(payload))
            self._write(payload)
            return
        if path == "/badrange.bin":
            parsed = _range_start(range_header) or (0, 0)
            start, last = parsed
            if range_header == "bytes=0-0":
                self._head(
                    206,
                    1,
                    {"Content-Range": f"bytes 0-0/{len(payload)}"},
                )
                self._write(payload[:1])
                return
            body = payload[start:]
            self._head(
                206,
                len(body),
                {"Content-Range": f"bytes {start}-{len(payload) - 2}/{len(payload)}"},
            )
            self._write(body)
            return
        if path == "/encoded.bin":
            parsed = _range_start(range_header) or (0, 0)
            start, last = parsed
            last = min(last, len(payload) - 1)
            body = payload[start : last + 1]
            self._head(
                206,
                len(body),
                {
                    "Content-Range": f"bytes {start}-{last}/{len(payload)}",
                    "Content-Encoding": "gzip",
                },
            )
            self._write(body)
            return
        if path == "/flip.bin":
            with state.lock:
                state.flips += 1
                served = state.flips
            if served > state.flip_after:
                self._head(200, len(payload))
                self._write(payload)
                return
            parsed = _range_start(range_header) or (0, 0)
            start, last = parsed
            last = min(last, len(payload) - 1)
            body = payload[start : last + 1]
            self._head(
                206,
                len(body),
                {"Content-Range": f"bytes {start}-{last}/{len(payload)}"},
            )
            self._write(body)
            return
        if path == "/ratelimit.bin":
            self._head(429, 0, {"Retry-After": "1"})
            return
        self._head(404, 0)
        return


@pytest.fixture()
def loopback():
    state = _State()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    server.state = state  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        state.cancelled.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _spec(url: str, **kwargs) -> TransferSpec:
    return TransferSpec(urls=(url,), allowed_schemes=("http",), expected_size=TOTAL, **kwargs)


def _no_segment_files(directory: Path) -> bool:
    return not list(directory.glob("*.seg*"))


def test_segmented_transfer_fetches_ranges_and_assembles(loopback, tmp_path):
    state, base = loopback
    url = f"{base}/artifact.bin"

    report = market_transport.fetch_artifact(
        _spec(url, download_connections=4), tmp_path
    )

    body = Path(report.path).read_bytes()
    assert body == PAYLOAD
    assert report.bytes_read == TOTAL
    ranges = state.ranges("/artifact.bin")
    assert ranges[0] == "bytes=0-0", "先做 Range 预检"
    segments = [value for value in ranges if value != "bytes=0-0"]
    assert len(segments) >= 4, ranges
    assert len(set(segments)) == len(segments)
    assert _no_segment_files(tmp_path)
    assert not (tmp_path / "artifact.bin.part").exists()


def test_segmented_progress_uses_index_size_and_ends_full(loopback, tmp_path):
    _state, base = loopback
    url = f"{base}/artifact.bin"
    seen: list[tuple[int, int]] = []

    market_transport.fetch_artifact(
        _spec(url, download_connections=3),
        tmp_path,
        progress=lambda done, total: seen.append((done, total)),
    )

    assert seen[-1] == (TOTAL, TOTAL)
    assert all(total == TOTAL for _done, total in seen)
    assert all(previous[0] <= current[0] for previous, current in zip(seen, seen[1:]))


def test_segmented_transfer_sends_accept_encoding_identity(loopback, tmp_path):
    state, base = loopback
    headers: list[dict[str, str]] = []
    handler = _Handler

    class _Recorder(type):
        pass

    original = handler.do_GET

    def do_GET(self):  # noqa: N802
        headers.append({key.lower(): value for key, value in self.headers.items()})
        return original(self)

    handler.do_GET = do_GET
    try:
        market_transport.fetch_artifact(
            _spec(f"{base}/artifact.bin", download_connections=2), tmp_path
        )
    finally:
        handler.do_GET = original

    assert state.ranges("/artifact.bin")
    assert all(item.get("accept-encoding") == "identity" for item in headers)


def test_server_ignoring_range_falls_back_to_single_stream(loopback, tmp_path):
    state, base = loopback
    url = f"{base}/norange.bin"
    seen: list[tuple[int, int]] = []

    report = market_transport.fetch_artifact(
        _spec(url, download_connections=4),
        tmp_path,
        progress=lambda done, total: seen.append((done, total)),
    )

    assert Path(report.path).read_bytes() == PAYLOAD
    ranges = state.ranges("/norange.bin")
    assert ranges[0] == "bytes=0-0", "预检一次"
    assert ranges[1] == "", "200 之后只跑一次完整单流"
    assert len(ranges) == 2
    assert seen[-1] == (TOTAL, TOTAL)
    assert _no_segment_files(tmp_path)


def test_malformed_content_range_on_a_segment_is_a_protocol_error(loopback, tmp_path):
    _state, base = loopback
    url = f"{base}/badrange.bin"

    with pytest.raises(errors.NetworkError) as caught:
        market_transport.fetch_artifact(_spec(url, download_connections=4), tmp_path)

    assert caught.value.reason == errors.PROTOCOL
    assert not (tmp_path / "badrange.bin.part").exists()


def test_a_200_midway_never_writes_into_segments_and_keeps_progress_monotonic(
    loopback, tmp_path
):
    """分段跑到一半服务端改口 200：整段作废、回退单流一次，进度不许倒退。"""
    state, base = loopback
    state.flip_after = 3
    url = f"{base}/flip.bin"
    seen: list[tuple[int, int]] = []

    report = market_transport.fetch_artifact(
        _spec(url, download_connections=4),
        tmp_path,
        progress=lambda done, total: seen.append((done, total)),
    )

    assert Path(report.path).read_bytes() == PAYLOAD
    assert seen[-1] == (TOTAL, TOTAL)
    assert all(total == TOTAL for _done, total in seen)
    assert all(
        previous[0] <= current[0] for previous, current in zip(seen, seen[1:])
    ), seen
    assert not (tmp_path / "flip.bin.part").exists()
    assert not list(tmp_path.glob("flip.bin.part.seg*"))
    assert not (tmp_path / "flip.bin.part.segments.json").exists()


def test_content_encoding_other_than_identity_is_rejected(loopback, tmp_path):
    _state, base = loopback
    url = f"{base}/encoded.bin"

    with pytest.raises(errors.NetworkError) as caught:
        market_transport.fetch_artifact(_spec(url, download_connections=4), tmp_path)

    assert caught.value.reason == errors.PROTOCOL
    assert not (tmp_path / "encoded.bin.part").exists()


def test_rate_limited_segmented_run_surfaces_retry_after(loopback, tmp_path):
    state, base = loopback
    url = f"{base}/ratelimit.bin"

    with pytest.raises(errors.RateLimitError) as caught:
        market_transport.fetch_artifact(_spec(url, download_connections=4), tmp_path)

    assert caught.value.status == 429
    assert caught.value.retry_after == 1.0
    assert state.count("/ratelimit.bin") == 1, "限流不做换路重试"


def test_expected_size_mismatch_with_the_server_is_a_protocol_error(loopback, tmp_path):
    _state, base = loopback
    url = f"{base}/artifact.bin"

    with pytest.raises(errors.NetworkError) as caught:
        market_transport.fetch_artifact(
            TransferSpec(
                urls=(url,),
                allowed_schemes=("http",),
                expected_size=TOTAL + 1,
                download_connections=4,
            ),
            tmp_path,
        )

    assert caught.value.reason == errors.PROTOCOL


def test_cancel_during_segmented_transfer_is_prompt_and_resumable(loopback, tmp_path):
    state, base = loopback
    state.throttle_bytes_per_second = 4 * MIB
    url = f"{base}/throttled.bin"
    cancelled = threading.Event()
    started = time.monotonic()

    def progress(done: int, _total: int) -> None:
        if done >= 512 * 1024:
            cancelled.set()

    with pytest.raises(TransferCancelled):
        market_transport.fetch_artifact(
            _spec(url, download_connections=4),
            tmp_path,
            progress=progress,
            cancel=cancelled.is_set,
        )

    assert time.monotonic() - started < 20.0
    part = tmp_path / "throttled.bin.part"
    assert not part.exists()
    state_file = tmp_path / "throttled.bin.part.segments.json"
    assert state_file.is_file(), "分段状态必须留给续传"
    assert list(tmp_path.glob("throttled.bin.part.seg*"))

    resumed = market_transport.fetch_artifact(
        TransferSpec(
            urls=(url,),
            allowed_schemes=("http",),
            expected_size=TOTAL,
            download_connections=4,
        ),
        tmp_path,
    )
    assert Path(resumed.path).read_bytes() == PAYLOAD
    assert resumed.resumed_from > 0
    assert _no_segment_files(tmp_path)


def test_single_connection_never_preflights(loopback, tmp_path):
    state, base = loopback
    url = f"{base}/artifact.bin"

    report = market_transport.fetch_artifact(
        _spec(url, download_connections=1), tmp_path
    )

    assert Path(report.path).read_bytes() == PAYLOAD
    assert state.ranges("/artifact.bin") == [""]


def test_monotonic_progress_never_goes_backwards():
    """分段写了几段再回退单流：done 不许倒退（进度条会往回跳）。"""
    from petcore.net.market_transport import _monotonic_progress

    seen: list[tuple[int, int]] = []
    report = _monotonic_progress(lambda done, total: seen.append((done, total)))
    assert report is not None
    report(100, 1000)
    report(0, 1000)
    report(50, 1000)
    report(1000, 1000)

    assert [done for done, _total in seen] == [100, 100, 100, 1000]
    assert all(total == 1000 for _done, total in seen)


def test_probe_source_measures_range_support_and_ttfb(loopback):
    state, base = loopback
    state.delays["/mirror-slow.bin"] = 0.15
    state.delays["/mirror-fast.bin"] = 0.0

    fast = market.probe_source(f"{base}/mirror-fast.bin", policy=_policy())
    slow = market.probe_source(f"{base}/mirror-slow.bin", policy=_policy())
    plain = market.probe_source(f"{base}/norange.bin", policy=_policy())

    assert fast.ok and fast.range_supported and fast.total == TOTAL
    assert slow.ok and slow.range_supported
    assert plain.ok and plain.range_supported is False
    assert fast.ttfb < slow.ttfb
    assert state.ranges("/mirror-fast.bin") == ["bytes=0-0"]


def _policy() -> market.TransportPolicy:
    return market.TransportPolicy(max_redirects=3, allowed_schemes=("http",))


def test_rank_sources_prefers_fastest_range_capable_source(loopback):
    state, base = loopback
    state.delays["/mirror-slow.bin"] = 0.1
    probes = market.probe_sources(
        (f"{base}/mirror-slow.bin", f"{base}/mirror-fast.bin"),
        policy=_policy(),
    )
    ranked = market.rank_sources(probes, prefer_range=True)

    assert len(ranked) == 2
    assert ranked[0].endswith("/mirror-fast.bin")
    assert ranked[1].endswith("/mirror-slow.bin")


def test_probe_failures_keep_the_original_order(tmp_path):
    probes = (
        market.SourceProbe(url="https://a.invalid/x", ok=False, ttfb=0.0),
        market.SourceProbe(url="https://b.invalid/x", ok=False, ttfb=0.0),
    )

    assert market.rank_sources(probes, prefer_range=True) == (
        "https://a.invalid/x",
        "https://b.invalid/x",
    )


def test_fetch_artifact_without_known_size_still_segments(loopback, tmp_path):
    _state, base = loopback
    url = f"{base}/artifact.bin"

    report = market_transport.fetch_artifact(
        TransferSpec(urls=(url,), allowed_schemes=("http",), download_connections=4),
        tmp_path,
    )

    assert Path(report.path).read_bytes() == PAYLOAD
    assert report.bytes_read == TOTAL


def test_small_artifact_stays_single_stream(loopback, tmp_path):
    state, base = loopback
    state.payload = b"tiny"
    url = f"{base}/artifact.bin"

    report = market_transport.fetch_artifact(
        TransferSpec(
            urls=(url,), allowed_schemes=("http",), expected_size=4, download_connections=4
        ),
        tmp_path,
    )

    assert Path(report.path).read_bytes() == b"tiny"
    assert state.ranges("/artifact.bin") == [""]


def test_legacy_fetch_artifact_still_reports_sha_of_the_same_bytes(loopback, tmp_path):
    _state, base = loopback
    url = f"{base}/artifact.bin"

    report = market_transport.fetch_artifact(_spec(url), tmp_path)

    assert hashlib.sha256(Path(report.path).read_bytes()).hexdigest() == hashlib.sha256(
        PAYLOAD
    ).hexdigest()
