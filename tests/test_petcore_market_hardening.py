from __future__ import annotations

import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from petcore.contracts.archive import (
    ArchiveLimits,
    ArchiveOrigin,
    MARKET_ARCHIVE_LIMITS,
    effective_archive_limits,
)
from petcore.contracts.http import TransferSpec
from petcore.market import install, model
from petcore.market.download import download_artifact
from petcore.market.errors import TransferCancelled
from petcore.net import errors
from petcore.net.market import HttpFetcher
from petcore.net.market_transport import fetch_artifact


class _RangeState:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.lock = threading.Lock()
        self.ranges: list[str] = []


class _RangeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *_args):  # noqa: D401 - 测试静音
        return None

    def do_GET(self):  # noqa: N802
        state: _RangeState = self.server.state  # type: ignore[attr-defined]
        range_header = self.headers.get("Range", "")
        with state.lock:
            state.ranges.append(range_header)
        start = 0
        status = 200
        if range_header.startswith("bytes="):
            start = int(range_header.removeprefix("bytes=").split("-", 1)[0])
            status = 206
        body = state.payload[start:]
        self.send_response(status)
        if status == 206:
            self.send_header(
                "Content-Range",
                f"bytes {start}-{len(state.payload) - 1}/{len(state.payload)}",
            )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            self.close_connection = True


@pytest.fixture()
def range_server():
    payload = bytes(range(256)) * 4096  # 1 MiB，保证取消发生在一个读块之后
    state = _RangeState(payload)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
    server.daemon_threads = True
    server.state = state  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/artifact.bin", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _market_package(url: str, payload: bytes) -> tuple[model.MarketPackage, model.MarketVersion]:
    version = model.MarketVersion(
        version="1.0.0",
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        urls=(url,),
    )
    package = model.MarketPackage(
        id="fun.resume",
        kind="content",
        name="fun.resume",
        version=version.version,
        versions=(version,),
    )
    return package, version


def test_market_download_artifact_keeps_stable_resume_state(range_server, tmp_path):
    url, state = range_server
    payload = state.payload
    package, version = _market_package(url, payload)
    cancelled = threading.Event()

    def progress(done: int, _total: int) -> None:
        if done >= 256 * 1024:
            cancelled.set()

    with pytest.raises(TransferCancelled):
        download_artifact(
            HttpFetcher(),
            tmp_path,
            package,
            version,
            progress=progress,
            cancel=cancelled.is_set,
        )

    downloads = tmp_path / "market" / "downloads"
    part = downloads / "fun.resume-1.0.0.zip.part"
    state_file = downloads / "fun.resume-1.0.0.download.json"
    assert part.is_file()
    assert 0 < part.stat().st_size < version.size
    assert json.loads(state_file.read_text(encoding="utf-8")) == {
        "package_id": package.id,
        "version": version.version,
        "size": version.size,
        "sha256": version.sha256,
    }

    result = download_artifact(HttpFetcher(), tmp_path, package, version)

    assert result.path == downloads / "fun.resume-1.0.0.zip"
    assert result.path.read_bytes() == payload
    assert not part.exists()
    assert not state_file.exists()
    assert state.ranges[0] == ""
    assert state.ranges[1].startswith("bytes=")


def test_effective_archive_limits_only_market_feature_packages():
    assert effective_archive_limits(ArchiveOrigin.MARKET, "feature") == MARKET_ARCHIVE_LIMITS
    assert effective_archive_limits("market", "content") is None
    assert effective_archive_limits("local", "feature") is None
    strict = ArchiveLimits(1024, 2048, 3)
    assert effective_archive_limits("market", "feature", strict) == strict
    loose = ArchiveLimits(128 * 1024 * 1024, 512 * 1024 * 1024, 20_000)
    assert effective_archive_limits("market", "feature", loose) == MARKET_ARCHIVE_LIMITS


def test_file_artifact_requires_explicit_local_root_and_stays_in_root(tmp_path):
    root = tmp_path / "mirror"
    root.mkdir()
    artifact = root / "artifact.zip"
    artifact.write_bytes(b"artifact")
    outside = tmp_path / "outside.zip"
    outside.write_bytes(b"outside")

    denied = TransferSpec(urls=(artifact.as_uri(),), allowed_schemes=("https",))
    with pytest.raises(errors.NetworkError) as caught:
        fetch_artifact(denied, tmp_path / "denied")
    assert caught.value.reason == errors.PROTOCOL

    allowed = TransferSpec(
        urls=(artifact.as_uri(),),
        allowed_schemes=("file",),
        allow_local_file=True,
        local_root=str(root),
        expected_size=artifact.stat().st_size,
    )
    report = fetch_artifact(allowed, tmp_path / "allowed")
    assert Path(report.path).read_bytes() == b"artifact"

    traversal = TransferSpec(
        urls=(outside.as_uri(),),
        allowed_schemes=("file",),
        allow_local_file=True,
        local_root=str(root),
    )
    with pytest.raises(errors.NetworkError) as caught:
        fetch_artifact(traversal, tmp_path / "traversal")
    assert caught.value.reason == errors.PROTOCOL


def test_network_index_cannot_authorize_file_artifact(tmp_path):
    root = tmp_path / "mirror"
    root.mkdir()
    artifact = root / "artifact.zip"
    artifact.write_bytes(b"not-a-package")
    spec = TransferSpec(
        urls=(artifact.as_uri(),),
        allowed_schemes=("https", "http"),
        allow_local_file=False,
        local_root=str(root),
    )
    with pytest.raises(errors.NetworkError) as caught:
        fetch_artifact(spec, tmp_path / "network")
    assert caught.value.reason == errors.PROTOCOL


def test_market_job_registry_holds_thread_until_bounded_shutdown():
    app = QApplication.instance() or QApplication([])
    started = threading.Event()

    class Job(QThread):
        def __init__(self):
            super().__init__()
            self.cancelled = threading.Event()

        def cancel(self):
            self.cancelled.set()

        def run(self):
            started.set()
            self.cancelled.wait(5)

    from petcore.ui.market_jobs import MarketJobRegistry

    registry = MarketJobRegistry()
    job = Job()
    registry.submit(job)
    job.start()
    assert started.wait(2)
    report = registry.shutdown_all(2000)
    assert report.total == 1
    assert report.cancelled == 1
    assert report.timed_out == 0
    assert registry.active_jobs() == ()
    app.processEvents()


def test_explicit_local_index_can_install_same_root_artifact(tmp_path):
    import zipfile

    mirror = tmp_path / "mirror"
    mirror.mkdir()
    artifact = mirror / "fun.local-1.0.0.zip"
    manifest = {
        "schema": 1,
        "id": "fun.local",
        "kind": "content",
        "name": "fun.local",
        "version": "1.0.0",
        "api_version": "1",
        "core_requires": ">=0.1.0,<1.0.0",
    }
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("mod.json", json.dumps(manifest))
        archive.writestr("payload.txt", "local")
    document = {
        "schema_version": 1,
        "packages": [
            {
                "id": "fun.local",
                "kind": "content",
                "name": "fun.local",
                "latest": "1.0.0",
                "api_version": "1",
                "core_requires": ">=0.1.0,<1.0.0",
                "versions": [
                    {
                        "version": "1.0.0",
                        "size": artifact.stat().st_size,
                        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                        "urls": [artifact.as_uri()],
                    }
                ],
            }
        ],
    }
    index, problems = model.parse_index(document)
    assert index is not None, problems
    package = index.packages[0]
    version = package.versions[0]

    outcome = install.install_package(
        tmp_path,
        package,
        version,
        fetcher=HttpFetcher(),
        index_origin=(mirror / "extensions-index.json").as_uri(),
        local_file_root=mirror,
    )

    assert outcome.ok, outcome.message
    assert (tmp_path / "mods" / "fun.local" / "payload.txt").read_text() == "local"


def test_rollback_failure_reports_state_and_paths(tmp_path):
    install.save_lock(
        tmp_path,
        {
            "schema": 1,
            "packages": {
                "fun.missing": {
                    "version": "2.0.0",
                    "backup": "market/backup/fun.missing/1.0.0",
                }
            },
        },
    )

    outcome = install.rollback(tmp_path, "fun.missing")

    assert isinstance(outcome, install.RollbackResult)
    assert not outcome.ok
    assert outcome.state == "backup-missing"
    assert "已回滚" not in outcome.message
    assert Path(outcome.backup_path) == tmp_path / "market" / "backup" / "fun.missing" / "1.0.0"
