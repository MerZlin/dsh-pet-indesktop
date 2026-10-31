"""市场下载编排：分段分支直写稳定 `.part`、镜像竞速、旧替身路径不变。"""

from __future__ import annotations

import hashlib
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from petcore.contracts.http import TransferReport, TransferSpec
from petcore.market import download as market_download
from petcore.market import model
from petcore.net.market import HttpFetcher

MIB = 1024 * 1024
PAYLOAD = bytes((index * 53 + 17) % 251 for index in range(5 * MIB))


def _package(urls: tuple[str, ...], payload: bytes = PAYLOAD) -> tuple[model.MarketPackage, model.MarketVersion]:
    version = model.MarketVersion(
        version="1.0.0",
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        urls=urls,
    )
    package = model.MarketPackage(
        id="fun.seg",
        kind="content",
        name="fun.seg",
        version=version.version,
        versions=(version,),
    )
    return package, version


class _State:
    def __init__(self, payload: bytes = PAYLOAD) -> None:
        self.payload = payload
        self.lock = threading.Lock()
        self.requests: list[tuple[str, str]] = []
        self.delays: dict[str, float] = {}

    def record(self, path: str, range_header: str) -> None:
        with self.lock:
            self.requests.append((path, range_header))

    def ranges(self, path: str) -> list[str]:
        with self.lock:
            return [value for name, value in self.requests if name == path]

    def count(self, path: str) -> int:
        with self.lock:
            return sum(1 for name, _value in self.requests if name == path)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *_args):  # noqa: D401 - 测试静音
        return None

    def _send(self, status: int, body: bytes, headers=None) -> None:
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            try:
                self.wfile.write(body)
            except OSError:
                self.close_connection = True

    def do_GET(self):  # noqa: N802
        state: _State = self.server.state  # type: ignore[attr-defined]
        path = self.path.split("?", 1)[0]
        range_header = self.headers.get("Range", "")
        state.record(path, range_header)
        delay = state.delays.get(path, 0.0)
        if delay:
            time.sleep(delay)
        payload = state.payload
        if path == "/broken.bin":
            self._send(500, b"boom")
            return
        if not range_header.startswith("bytes="):
            self._send(200, payload)
            return
        first, _, last = range_header[len("bytes=") :].partition("-")
        try:
            start = int(first)
            stop = int(last) if last else len(payload) - 1
        except ValueError:
            self._send(400, b"bad range")
            return
        if start >= len(payload):
            self._send(416, b"", {"Content-Range": f"bytes */{len(payload)}"})
            return
        stop = min(stop, len(payload) - 1)
        body = payload[start : stop + 1]
        self._send(206, body, {"Content-Range": f"bytes {start}-{stop}/{len(payload)}"})


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
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class _RecordingFetchFetcher:
    """只实现 ``fetch``：用来观察 download 层交给传输层的 spec 与目录。"""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.specs: list[TransferSpec] = []
        self.dest_dirs: list[Path] = []

    def fetch(self, spec, dest_dir, *, limits=None, progress=None, cancel=None):
        self.specs.append(spec)
        self.dest_dirs.append(Path(dest_dir))
        name = spec.artifact_name or "artifact.zip"
        part = Path(dest_dir) / f"{name}.part"
        part.write_bytes(self.payload)
        target = Path(dest_dir) / name
        part.replace(target)
        if progress is not None:
            progress(len(self.payload), len(self.payload))
        return TransferReport(
            url=spec.urls[0], bytes_read=len(self.payload), path=str(target)
        )


class _RankingFetcher:
    """实现 ``rank_sources`` + ``download``：验证竞速只改顺序、不改回退语义。"""

    def __init__(self, files: dict[str, Path], order: tuple[str, ...], *, failures=()):
        self.files = dict(files)
        self.order = tuple(order)
        self.failures = set(failures)
        self.rank_calls: list[tuple[str, ...]] = []
        self.calls: list[str] = []

    def rank_sources(self, urls, **kwargs):
        self.rank_calls.append(tuple(urls))
        return self.order

    def download(self, url, part_path, *, expected_size=None, timeout=15.0, progress=None, cancel=None):
        self.calls.append(url)
        if url in self.failures:
            raise OSError("mirror down")
        shutil.copyfile(self.files[url], part_path)
        return part_path.stat().st_size


def test_segmented_branch_writes_the_stable_part_directly(tmp_path):
    fetcher = _RecordingFetchFetcher(PAYLOAD)
    package, version = _package(("https://mirror.invalid/fun.seg-1.0.0.zip",))

    result = market_download.download_artifact(
        fetcher, tmp_path, package, version, connections=4
    )

    spec = fetcher.specs[0]
    assert spec.download_connections == 4
    assert spec.artifact_name == "fun.seg-1.0.0.zip"
    assert fetcher.dest_dirs[0] == market_download.download_dir(tmp_path)
    assert result.path == market_download.download_dir(tmp_path) / "fun.seg-1.0.0.zip"
    assert result.path.read_bytes() == PAYLOAD
    assert not list((tmp_path / "market").rglob(".fetch-*")), "分段分支不做整段复制"
    assert result.connections == 4


def test_single_connection_keeps_the_legacy_staging_branch(tmp_path):
    fetcher = _RecordingFetchFetcher(PAYLOAD)
    package, version = _package(("https://mirror.invalid/fun.seg-1.0.0.zip",))

    market_download.download_artifact(fetcher, tmp_path, package, version, connections=1)

    spec = fetcher.specs[0]
    assert spec.download_connections == 1
    assert spec.artifact_name == ""
    assert fetcher.dest_dirs[0].name.startswith(".fetch-")


def test_segmented_http_download_leaves_only_the_artifact(loopback, tmp_path):
    state, base = loopback
    url = f"{base}/fun.seg-1.0.0.zip"
    package, version = _package((url,))

    result = market_download.download_artifact(
        HttpFetcher(), tmp_path, package, version, connections=4
    )

    assert result.path.read_bytes() == PAYLOAD
    assert result.url == url
    downloads = market_download.download_dir(tmp_path)
    asserts = sorted(path.name for path in downloads.iterdir())
    assert asserts == ["fun.seg-1.0.0.zip"], asserts
    ranges = state.ranges("/fun.seg-1.0.0.zip")
    assert ranges[0] == "bytes=0-0"
    assert len([value for value in ranges if value != "bytes=0-0"]) >= 4


def test_segmented_http_download_resumes_after_cancel(loopback, tmp_path):
    state, base = loopback
    url = f"{base}/fun.seg-1.0.0.zip"
    package, version = _package((url,))
    cancelled = threading.Event()

    def progress(done: int, _total: int) -> None:
        if done >= 512 * 1024:
            cancelled.set()

    from petcore.market.errors import TransferCancelled

    with pytest.raises(TransferCancelled):
        market_download.download_artifact(
            HttpFetcher(),
            tmp_path,
            package,
            version,
            connections=4,
            progress=progress,
            cancel=cancelled.is_set,
        )

    downloads = market_download.download_dir(tmp_path)
    assert (downloads / "fun.seg-1.0.0.zip.part.segments.json").is_file()
    assert list(downloads.glob("fun.seg-1.0.0.zip.part.seg*"))

    result = market_download.download_artifact(
        HttpFetcher(), tmp_path, package, version, connections=4
    )

    assert result.path.read_bytes() == PAYLOAD
    assert result.resumed_from > 0
    assert sorted(path.name for path in downloads.iterdir()) == ["fun.seg-1.0.0.zip"]


def test_download_only_fetcher_keeps_ordered_fallback(tmp_path):
    artifact = tmp_path / "artifact.zip"
    artifact.write_bytes(PAYLOAD)
    fail_url = "https://mirror.invalid/first.zip"
    ok_url = "https://mirror.invalid/second.zip"
    package, version = _package((fail_url, ok_url))

    fetcher = _RankingFetcher({ok_url: artifact}, (ok_url, fail_url), failures={fail_url})
    result = market_download.download_artifact(
        fetcher, tmp_path, package, version, mirror_policy="ordered"
    )

    assert result.url == ok_url
    assert fetcher.calls == [fail_url, ok_url]
    assert fetcher.rank_calls == [], "ordered 不预检"
    assert len(result.attempts) == 1


def test_race_policy_ranks_the_fastest_mirror_first(tmp_path):
    artifact = tmp_path / "artifact.zip"
    artifact.write_bytes(PAYLOAD)
    slow = "https://slow.invalid/a.zip"
    fast = "https://fast.invalid/a.zip"
    package, version = _package((slow, fast))

    fetcher = _RankingFetcher({slow: artifact, fast: artifact}, (fast, slow))
    result = market_download.download_artifact(
        fetcher, tmp_path, package, version, mirror_policy="race"
    )

    assert result.url == fast
    assert fetcher.rank_calls == [(slow, fast)]
    assert fetcher.calls == [fast], "赢的源失败前不该先打另一个"


def test_race_falls_back_when_the_fastest_mirror_fails(tmp_path):
    artifact = tmp_path / "artifact.zip"
    artifact.write_bytes(PAYLOAD)
    slow = "https://slow.invalid/a.zip"
    fast = "https://fast.invalid/a.zip"
    package, version = _package((slow, fast))

    fetcher = _RankingFetcher({slow: artifact, fast: artifact}, (fast, slow), failures={fast})
    result = market_download.download_artifact(
        fetcher, tmp_path, package, version, mirror_policy="race"
    )

    assert result.url == slow
    assert fetcher.calls == [fast, slow]
    assert len(result.attempts) == 1


def test_auto_policy_only_probes_when_there_is_another_source(tmp_path):
    artifact = tmp_path / "artifact.zip"
    artifact.write_bytes(PAYLOAD)
    url = "https://mirror.invalid/only.zip"
    package, version = _package((url,))

    fetcher = _RankingFetcher({url: artifact}, (url,))
    market_download.download_artifact(fetcher, tmp_path, package, version)

    assert fetcher.rank_calls == []


def test_race_keeps_only_the_index_urls_when_a_probe_returns_unknown(tmp_path):
    artifact = tmp_path / "artifact.zip"
    artifact.write_bytes(PAYLOAD)
    first = "https://a.invalid/pack.zip"
    second = "https://b.invalid/pack.zip"
    package, version = _package((first, second))

    class _StrangeRanker(_RankingFetcher):
        def rank_sources(self, urls, **kwargs):
            return ("https://evil.invalid/pack.zip", second, first)

    fetcher = _StrangeRanker({first: artifact, second: artifact}, ())
    result = market_download.download_artifact(
        fetcher, tmp_path, package, version, mirror_policy="race"
    )

    assert result.url == second
    assert all("evil" not in url for url in fetcher.calls)


def test_http_fetcher_rank_sources_orders_by_ttfb(loopback):
    state, base = loopback
    state.delays["/slow.bin"] = 0.12
    fetcher = HttpFetcher()

    ranked = fetcher.rank_sources(
        (f"{base}/slow.bin", f"{base}/fast.bin"),
        timeout=5.0,
        allowed_schemes=("http",),
    )

    assert ranked == (f"{base}/fast.bin", f"{base}/slow.bin")

def _zip_bytes(mod_id: str, version: str, payload: str) -> bytes:
    import io
    import json
    import zipfile

    buffer = io.BytesIO()
    manifest = {
        "schema": 1,
        "id": mod_id,
        "kind": "content",
        "name": mod_id,
        "version": version,
        "api_version": "1",
        "core_requires": ">=0.1.0,<1.0.0",
    }
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("mod.json", json.dumps(manifest))
        archive.writestr("payload.bin", payload)
    return buffer.getvalue()


def test_install_package_wires_connections_into_the_transfer(tmp_path):
    from petcore.market import install as market_install

    blob = _zip_bytes("fun.seg", "1.0.0", "x" * (5 * MIB))
    package, version = _package(("https://mirror.invalid/fun.seg-1.0.0.zip",), blob)
    fetcher = _RecordingFetchFetcher(blob)

    outcome = market_install.install_package(
        tmp_path,
        package,
        version,
        fetcher=fetcher,
        connections=4,
        mirror_policy="ordered",
    )

    assert outcome.ok, outcome.message
    assert fetcher.specs[0].download_connections == 4
    assert fetcher.specs[0].artifact_name == "fun.seg-1.0.0.zip"
    assert outcome.connections == 4
    assert outcome.resumed_from == 0
    assert (tmp_path / "mods" / "fun.seg" / "mod.json").is_file()


def test_resumable_bytes_reads_part_then_segment_state(tmp_path):
    from petcore.net import ranged

    package, version = _package(("https://mirror.invalid/fun.seg-1.0.0.zip",))
    downloads = market_download.download_dir(tmp_path)
    downloads.mkdir(parents=True)
    part = downloads / "fun.seg-1.0.0.zip.part"

    assert market_download.resumable_bytes(tmp_path, package, version) == 0

    ranged.write_state(
        part,
        size=version.size,
        sha256=version.sha256,
        segment_bytes=2 * MIB,
        connections=4,
        segments={
            0: ranged.SegmentState(bytes=1024, url="https://mirror.invalid/a.zip"),
            1: ranged.SegmentState(bytes=2048, url="https://mirror.invalid/a.zip"),
        },
    )
    assert market_download.resumable_bytes(tmp_path, package, version) == 3072

    part.write_bytes(b"y" * 4096)
    assert market_download.resumable_bytes(tmp_path, package, version) == 4096

    ranged.clear_state(part)
    part.unlink()
    assert market_download.resumable_bytes(tmp_path, package, version) == 0

def test_sha_mismatch_removes_the_segmented_artifact(loopback, tmp_path):
    from petcore.market.errors import DownloadError

    _state, base = loopback
    url = f"{base}/fun.seg-1.0.0.zip"
    package, version = _package((url,))
    bad = model.MarketVersion(
        version=version.version,
        size=version.size,
        sha256="d" * 64,
        urls=version.urls,
    )
    package = model.MarketPackage(
        id=package.id,
        kind=package.kind,
        name=package.name,
        version=package.version,
        versions=(bad,),
    )

    with pytest.raises(DownloadError):
        market_download.download_artifact(
            HttpFetcher(), tmp_path, package, bad, connections=4
        )

    downloads = market_download.download_dir(tmp_path)
    assert sorted(path.name for path in downloads.iterdir()) == []
