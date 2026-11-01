"""素材包下载：分段主路、单流回退、摘要校验、续传、取消与缓存命中。

全部走 loopback ``ThreadingHTTPServer``，不外网；时序用 Event/宽预算，不用 sleep 猜。
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from petcore.contracts.archive import BudgetExceeded
from petcore.market.errors import TransferCancelled
from petcore.net import packs
from petcore.net.errors import NETWORK, PROTOCOL, NetworkError
from petcore.net.stream_download import download_stream

MIB = 1024 * 1024
PAYLOAD = bytes((index * 37 + 11) % 251 for index in range(5 * MIB))
TOTAL = len(PAYLOAD)
SHA = hashlib.sha256(PAYLOAD).hexdigest()
CHUNK = 256 * 1024
EMPTY_SHA = "0" * 64


class _State:
    def __init__(self) -> None:
        self.payload = PAYLOAD
        self.lock = threading.Lock()
        self.requests: list[tuple[str, str]] = []
        #: 逐路径的写出限速（字节/秒）；0 = 全速。
        self.throttle: dict[str, int] = {}
        #: 任意路径的固定内容（分片测试用）。
        self.files: dict[str, bytes] = {}
        self.index_body = json.dumps(
            {
                "schema": 1,
                "packs": [
                    {
                        "id": "blue-fish",
                        "name": "蓝胖鱼",
                        "version": "1.0.0",
                        "filename": "blue-fish-1.0.0.zip",
                        "size": TOTAL,
                        "sha256": SHA,
                        "character": "big_blue_fat_fish",
                        "urls": [
                            "https://github.com/dsh-pet/packs/releases/download/v1/blue-fish-1.0.0.zip"
                        ],
                    }
                ],
            }
        ).encode("utf-8")

    def record(self, path: str, range_header: str) -> None:
        with self.lock:
            self.requests.append((path, range_header))

    def ranges(self, path: str) -> list[str]:
        with self.lock:
            return [value for name, value in self.requests if name == path]


def _range_start(value: str) -> tuple[int, int | None] | None:
    """``bytes=N-M`` / ``bytes=N-`` → ``(start, last|None)``。"""
    if not value.startswith("bytes="):
        return None
    spec = value[len("bytes=") :].strip()
    first, separator, last = spec.partition("-")
    if not separator:
        return None
    try:
        start = int(first)
    except ValueError:
        return None
    if last.strip() == "":
        return start, None
    try:
        return start, int(last)
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
        rate = state.throttle.get(self.path.split("?", 1)[0], 0)
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

    def do_GET(self):  # noqa: N802 - http.server API
        state: _State = self.server.state  # type: ignore[attr-defined]
        path = self.path.split("?", 1)[0]
        range_header = self.headers.get("Range", "")
        state.record(path, range_header)
        payload = state.payload

        if path == "/ranges.bin":
            parsed = _range_start(range_header)
            if parsed is None:
                self._head(200, len(payload), {"Accept-Ranges": "bytes"})
                self._write(payload)
                return
            start, last = parsed
            if start >= len(payload):
                self._head(416, 0, {"Content-Range": f"bytes */{len(payload)}"})
                return
            stop = len(payload) - 1 if last is None else min(last, len(payload) - 1)
            body = payload[start : stop + 1]
            self._head(
                206,
                len(body),
                {"Content-Range": f"bytes {start}-{stop}/{len(payload)}"},
            )
            self._write(body)
            return
        if path in ("/plain.bin", "/slow.bin"):
            self._head(200, len(payload))
            self._write(payload)
            return
        if path == "/index.json":
            body = state.index_body
            self._head(200, len(body), {"Content-Type": "application/json"})
            self._write(body)
            return
        if path == "/broken.json":
            body = b"{oops"
            self._head(200, len(body), {"Content-Type": "application/json"})
            self._write(body)
            return
        if path in state.files:
            self._serve_range(state.files[path], range_header)
            return
        self._head(404, 0)
        return

    def _serve_range(self, payload: bytes, range_header: str) -> None:
        parsed = _range_start(range_header)
        if parsed is None:
            self._head(200, len(payload), {"Accept-Ranges": "bytes"})
            self._write(payload)
            return
        start, last = parsed
        if start >= len(payload):
            self._head(416, 0, {"Content-Range": f"bytes */{len(payload)}"})
            return
        stop = len(payload) - 1 if last is None else min(last, len(payload) - 1)
        body = payload[start : stop + 1]
        self._head(
            206,
            len(body),
            {"Content-Range": f"bytes {start}-{stop}/{len(payload)}"},
        )
        self._write(body)


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


def _entry(url: str, **overrides) -> packs.PackEntry:
    data = {
        "id": "blue-fish",
        "name": "蓝胖鱼",
        "version": "1.0.0",
        "filename": "blue-fish-1.0.0.zip",
        "size": TOTAL,
        "sha256": SHA,
        "character": "big_blue_fat_fish",
        "urls": (url,),
        "links": {},
        "notes": "",
    }
    data.update(overrides)
    return packs.PackEntry(**data)


def _no_part_files(directory: Path) -> bool:
    return not list(directory.glob("*.part*"))


def test_range_source_uses_segmented_download(loopback, tmp_path):
    state, base = loopback
    seen: list[tuple[int, int]] = []

    result = packs.download_pack(
        _entry(f"{base}/ranges.bin"),
        tmp_path,
        connections=4,
        progress=lambda done, total: seen.append((done, total)),
    )

    assert result.path == tmp_path / "blue-fish-1.0.0.zip"
    assert result.path.read_bytes() == PAYLOAD
    assert result.size == TOTAL
    assert result.sha256 == SHA
    assert result.from_cache is False
    assert result.connections >= 2
    assert result.resumed_from == 0
    assert result.url_host == "127.0.0.1"

    ranges = state.ranges("/ranges.bin")
    assert ranges[0] == "bytes=0-0", "先做 Range 预检"
    segments = [value for value in ranges if value and value != "bytes=0-0"]
    assert len(segments) >= 2, ranges
    assert _no_part_files(tmp_path)
    assert seen[-1] == (TOTAL, TOTAL)
    assert all(total == TOTAL for _done, total in seen)
    assert all(
        previous[0] <= current[0] for previous, current in zip(seen, seen[1:])
    ), seen


def test_server_ignoring_range_falls_back_to_single_stream(loopback, tmp_path):
    state, base = loopback
    seen: list[tuple[int, int]] = []

    result = packs.download_pack(
        _entry(f"{base}/plain.bin"),
        tmp_path,
        connections=4,
        progress=lambda done, total: seen.append((done, total)),
    )

    assert result.path.read_bytes() == PAYLOAD
    assert result.sha256 == SHA
    assert result.connections == 1
    assert state.ranges("/plain.bin") == ["bytes=0-0", ""], "预检后只跑一次完整单流"
    assert _no_part_files(tmp_path)
    assert seen[-1] == (TOTAL, TOTAL)
    assert all(total == TOTAL for _done, total in seen)
    assert all(
        previous[0] <= current[0] for previous, current in zip(seen, seen[1:])
    ), seen


def test_digest_mismatch_removes_part_and_final(loopback, tmp_path):
    _state, base = loopback
    final = tmp_path / "blue-fish-1.0.0.zip"

    with pytest.raises(NetworkError) as caught:
        packs.download_pack(
            _entry(f"{base}/ranges.bin", sha256=EMPTY_SHA), tmp_path, connections=4
        )

    assert caught.value.reason == NETWORK
    assert "127.0.0.1" in str(caught.value)
    assert "/ranges.bin" not in str(caught.value)
    assert not final.exists()
    assert not Path(f"{final}.part").exists()
    assert _no_part_files(tmp_path)


def test_stream_digest_mismatch_removes_part_and_final(tmp_path, loopback):
    _state, base = loopback
    final = tmp_path / "pack.zip"
    final.write_bytes(b"stale")

    with pytest.raises(NetworkError) as caught:
        download_stream(
            f"{base}/plain.bin",
            final,
            expected_size=TOTAL,
            expected_sha256=EMPTY_SHA,
            timeout=20.0,
        )

    assert caught.value.reason == PROTOCOL
    assert not final.exists()
    assert not Path(f"{final}.part").exists()


def test_stream_resumes_from_an_existing_part(loopback, tmp_path):
    state, base = loopback
    final = tmp_path / "pack.zip"
    part = Path(f"{final}.part")
    part.write_bytes(PAYLOAD[:CHUNK])

    result = download_stream(
        f"{base}/ranges.bin",
        final,
        expected_size=TOTAL,
        expected_sha256=SHA,
        timeout=20.0,
    )

    assert result.path.read_bytes() == PAYLOAD
    assert result.size == TOTAL
    assert result.sha256 == SHA
    assert state.ranges("/ranges.bin") == [f"bytes={CHUNK}-"]
    assert not part.exists()


def test_cancel_mid_stream_keeps_the_part_file(loopback, tmp_path):
    state, base = loopback
    state.throttle["/slow.bin"] = 4 * MIB
    final = tmp_path / "pack.zip"
    cancelled = threading.Event()

    def progress(done: int, _total: int) -> None:
        if done >= CHUNK:
            cancelled.set()

    started = time.monotonic()
    with pytest.raises(TransferCancelled):
        download_stream(
            f"{base}/slow.bin",
            final,
            expected_size=TOTAL,
            expected_sha256=SHA,
            timeout=20.0,
            progress=progress,
            cancel=cancelled.is_set,
        )

    assert time.monotonic() - started < 20.0
    part = Path(f"{final}.part")
    assert part.is_file(), "取消必须保留 .part 以便续传"
    assert part.stat().st_size > 0
    assert not final.exists()


def test_cancel_before_start_keeps_the_existing_part_file(tmp_path):
    final = tmp_path / "pack.zip"
    part = Path(f"{final}.part")
    part.write_bytes(b"partial-bytes")

    with pytest.raises(TransferCancelled):
        download_stream("http://127.0.0.1:9/x.zip", final, cancel=lambda: True)

    assert part.read_bytes() == b"partial-bytes"
    assert not final.exists()


def test_cancelled_pack_download_sends_no_request(loopback, tmp_path):
    state, base = loopback

    with pytest.raises(TransferCancelled):
        packs.download_pack(_entry(f"{base}/ranges.bin"), tmp_path, cancel=lambda: True)

    assert state.requests == []


def test_existing_verified_file_is_a_cache_hit(loopback, tmp_path):
    state, base = loopback
    final = tmp_path / "blue-fish-1.0.0.zip"
    final.write_bytes(PAYLOAD)

    result = packs.download_pack(_entry(f"{base}/ranges.bin"), tmp_path)

    assert result.from_cache is True
    assert result.path == final
    assert result.size == TOTAL
    assert result.sha256 == SHA
    assert result.connections == 0
    assert result.url_host == ""
    assert state.requests == [], "命中缓存不许发请求"


def test_stale_file_is_replaced(loopback, tmp_path):
    _state, base = loopback
    final = tmp_path / "blue-fish-1.0.0.zip"
    final.write_bytes(b"stale")

    result = packs.download_pack(_entry(f"{base}/ranges.bin"), tmp_path, connections=2)

    assert result.from_cache is False
    assert final.read_bytes() == PAYLOAD


def test_failed_mirror_falls_through_to_the_next_url(loopback, tmp_path):
    _state, base = loopback
    entry = _entry(
        f"{base}/missing.zip",
        urls=(f"{base}/missing.zip", f"{base}/ranges.bin"),
    )

    result = packs.download_pack(entry, tmp_path, connections=2)

    assert result.path.read_bytes() == PAYLOAD
    assert result.url_host == "127.0.0.1"


def test_all_urls_failing_names_hosts_only(loopback, tmp_path):
    _state, base = loopback

    with pytest.raises(NetworkError) as caught:
        packs.download_pack(
            _entry(f"{base}/missing.zip"), tmp_path, connections=2
        )

    message = str(caught.value)
    assert caught.value.reason == NETWORK
    assert "127.0.0.1" in message
    assert "HttpStatusError" in message
    assert "/missing.zip" not in message
    assert "http://" not in message


def test_pack_without_urls_is_a_protocol_error(tmp_path):
    with pytest.raises(NetworkError) as caught:
        packs.download_pack(_entry("https://github.com/x.zip", urls=()), tmp_path)

    assert caught.value.reason == PROTOCOL


def test_budget_overrun_is_not_retried_on_the_next_mirror(loopback, tmp_path):
    state, base = loopback
    entry = _entry(
        f"{base}/ranges.bin", urls=(f"{base}/ranges.bin", f"{base}/plain.bin")
    )

    with pytest.raises(BudgetExceeded):
        packs.download_pack(entry, tmp_path, connections=2, max_bytes=TOTAL // 2)

    assert len(state.requests) <= 1, "预算越界不是镜像问题，不许换下一个地址"
    assert _no_part_files(tmp_path)


def test_stream_budget_overrun_removes_the_part_file(loopback, tmp_path):
    _state, base = loopback
    final = tmp_path / "pack.zip"

    with pytest.raises(BudgetExceeded):
        download_stream(f"{base}/plain.bin", final, timeout=20.0, max_bytes=CHUNK)

    assert not final.exists()
    assert not Path(f"{final}.part").exists()


def test_fetch_index_reads_and_parses_a_remote_payload(loopback):
    state, base = loopback

    index = packs.fetch_index(f"{base}/index.json", timeout=10.0)

    assert index.schema == 1
    assert [entry.id for entry in index.packs] == ["blue-fish"]
    assert index.packs[0].sha256 == SHA
    assert index.packs[0].urls[0].startswith("https://github.com/")
    assert state.ranges("/index.json") == [""]


def test_fetch_index_reports_a_broken_payload(loopback):
    _state, base = loopback

    with pytest.raises(NetworkError) as caught:
        packs.fetch_index(f"{base}/broken.json", timeout=10.0)

    assert caught.value.reason == PROTOCOL
    assert "/broken.json" not in str(caught.value)


# ---------------------------------------------------------------- 直链解析
def test_release_parts_parses_a_github_release_url():
    assert packs.release_parts(
        "https://github.com/MerZlin/Animations/releases/download/packs-v1/shenshen-1.zip"
    ) == ("MerZlin", "Animations", "packs-v1", "shenshen-1.zip")


@pytest.mark.parametrize(
    "url",
    [
        "https://raw.githubusercontent.com/a/b/main/packs-index.json",
        "https://github.com/MerZlin/Animations",
        "http://github.com/a/b/releases/download/t/f.zip",
        "https://example.com/a/b/releases/download/t/f.zip",
    ],
)
def test_release_parts_rejects_other_urls(url):
    assert packs.release_parts(url) is None


def test_resolve_keeps_non_release_urls_untouched():
    url = "https://raw.githubusercontent.com/a/b/main/x.zip"
    assert packs.resolve_github_asset(url) == url


def test_resolve_falls_back_when_the_api_fails(monkeypatch):
    """api.github.com 不可用时必须回落原链接，不许把下载直接判死。"""
    from petcore.net import http as http_mod

    release = "https://github.com/MerZlin/Animations/releases/download/packs-v1/shenshen-1.zip"

    def boom(*_args, **_kwargs):
        raise NetworkError("unreachable", reason=NETWORK)

    monkeypatch.setattr(http_mod, "fetch_url", boom)
    assert packs.resolve_github_asset(release) == release


def test_resolve_returns_the_cdn_location(monkeypatch):
    """正常路径：API 给出签名直链，最终地址必须 https 且在主机白名单内。"""
    from petcore.net import http as http_mod

    release = "https://github.com/MerZlin/Animations/releases/download/packs-v1/shenshen-1.zip"
    payload = json.dumps({"assets": [{"id": 42, "name": "shenshen-1.zip"}]}).encode()

    monkeypatch.setattr(
        http_mod,
        "fetch_url",
        lambda *a, **k: (200, (), payload),
    )

    class _Response:
        def geturl(self):
            return "https://release-assets.githubusercontent.com/x/y?sig=1"

        def close(self):
            return None

    monkeypatch.setattr(http_mod, "urlopen", lambda request, **_k: _Response())
    assert packs.resolve_github_asset(release) == (
        "https://release-assets.githubusercontent.com/x/y?sig=1"
    )


def test_resolve_rejects_a_location_outside_the_whitelist(monkeypatch):
    from petcore.net import http as http_mod

    release = "https://github.com/MerZlin/Animations/releases/download/packs-v1/shenshen-1.zip"
    payload = json.dumps({"assets": [{"id": 42, "name": "shenshen-1.zip"}]}).encode()
    monkeypatch.setattr(http_mod, "fetch_url", lambda *a, **k: (200, (), payload))

    class _Response:
        def geturl(self):
            return "https://evil.example.com/x.zip"

        def close(self):
            return None

    monkeypatch.setattr(http_mod, "urlopen", lambda request, **_k: _Response())
    assert packs.resolve_github_asset(release) == release


def test_download_pack_uses_the_resolved_url(loopback, tmp_path, monkeypatch):
    """解析结果就是下载地址：github.com 原链接一次都不该被真正请求。"""
    state, base = loopback
    original = "https://github.com/MerZlin/Animations/releases/download/packs-v1/shenshen-1.zip"
    monkeypatch.setattr(packs, "resolve_github_asset", lambda url, **_k: f"{base}/ranges.bin")

    result = packs.download_pack(_entry(original), tmp_path, connections=4)

    assert result.path.read_bytes() == PAYLOAD
    assert result.url_host == "github.com"
    assert state.ranges("/ranges.bin")


# ---------------------------------------------------------------- 分片（Gitee 等有单文件上限的源）
SPLIT = TOTAL // 2
PART_A = PAYLOAD[:SPLIT]
PART_B = PAYLOAD[SPLIT:]
SHA_A = hashlib.sha256(PART_A).hexdigest()
SHA_B = hashlib.sha256(PART_B).hexdigest()


def _parts_entry(base: str, **overrides) -> packs.PackEntry:
    data = {
        "id": "blue-fish",
        "name": "蓝胖鱼",
        "version": "1.0.0",
        "filename": "blue-fish-1.0.0.zip",
        "size": TOTAL,
        "sha256": SHA,
        "character": "big_blue_fat_fish",
        "urls": (f"{base}/ranges.bin",),
        "links": {},
        "notes": "",
        "parts": (
            packs.PackPart("bf.part1", len(PART_A), SHA_A, (f"{base}/bf.part1",)),
            packs.PackPart("bf.part2", len(PART_B), SHA_B, (f"{base}/bf.part2",)),
        ),
    }
    data.update(overrides)
    return packs.PackEntry(**data)


def _serve_parts(state) -> None:
    state.files["/bf.part1"] = PART_A
    state.files["/bf.part2"] = PART_B


def test_download_pack_assembles_parts(loopback, tmp_path):
    state, base = loopback
    _serve_parts(state)
    seen: list[tuple[int, int]] = []

    result = packs.download_pack(
        _parts_entry(base, urls=(), prefer_parts=True),
        tmp_path,
        connections=4,
        progress=lambda done, total: seen.append((done, total)),
    )

    assert result.path.read_bytes() == PAYLOAD
    assert result.size == TOTAL
    assert result.sha256 == SHA
    assert sorted(p.name for p in tmp_path.iterdir()) == ["blue-fish-1.0.0.zip"], "分片在拼装后删除"
    assert seen[-1] == (TOTAL, TOTAL)
    assert all(total == TOTAL for _done, total in seen)
    assert all(a[0] <= b[0] for a, b in zip(seen, seen[1:])), "进度必须单调"


def test_parts_skip_already_verified_part(loopback, tmp_path):
    state, base = loopback
    _serve_parts(state)
    (tmp_path / "bf.part1").write_bytes(PART_A)

    packs.download_pack(_parts_entry(base, urls=(), prefer_parts=True), tmp_path, connections=2)

    assert state.ranges("/bf.part1") == [], "已校验的分片不该再发请求"
    assert state.ranges("/bf.part2"), "缺的分片要下载"


def test_parts_digest_mismatch_is_rejected(loopback, tmp_path):
    state, base = loopback
    state.files["/bf.part1"] = PART_A
    state.files["/bf.part2"] = PART_B[:-10] + b"0" * 10

    with pytest.raises(NetworkError):
        packs.download_pack(_parts_entry(base, urls=(), prefer_parts=True), tmp_path)

    assert not (tmp_path / "blue-fish-1.0.0.zip").exists()


def test_prefer_parts_uses_the_domestic_source_first(loopback, tmp_path):
    state, base = loopback
    _serve_parts(state)

    result = packs.download_pack(
        _parts_entry(base, urls=(f"{base}/does-not-exist.bin",), prefer_parts=True),
        tmp_path,
        connections=2,
    )

    assert result.path.read_bytes() == PAYLOAD
    assert state.ranges("/does-not-exist.bin") == []


def test_single_source_wins_when_prefer_parts_is_false(loopback, tmp_path):
    state, base = loopback
    _serve_parts(state)

    result = packs.download_pack(_parts_entry(base), tmp_path, connections=2)

    assert result.path.read_bytes() == PAYLOAD
    assert state.ranges("/bf.part1") == [], "默认先走单文件源，分片不该被碰"


def test_parts_fall_back_to_single_when_parts_fail(loopback, tmp_path):
    state, base = loopback
    state.files["/bf.part1"] = b"broken"
    state.files["/bf.part2"] = PART_B

    result = packs.download_pack(_parts_entry(base, prefer_parts=True), tmp_path, connections=2)

    assert result.path.read_bytes() == PAYLOAD
    assert state.ranges("/ranges.bin")


def test_policy_mirror_prefers_parts_even_without_the_index_flag(loopback, tmp_path):
    state, base = loopback
    _serve_parts(state)

    result = packs.download_pack(
        _parts_entry(base, urls=(f"{base}/ranges.bin",)),  # prefer_parts 为假
        tmp_path,
        connections=2,
        policy="mirror",
    )

    assert result.path.read_bytes() == PAYLOAD
    assert state.ranges("/bf.part1"), "policy=mirror 必须先走分片镜像"
    assert state.ranges("/ranges.bin") == [], "镜像成功就不该再碰 GitHub 源"


def test_policy_github_never_touches_parts(loopback, tmp_path):
    state, base = loopback
    _serve_parts(state)

    result = packs.download_pack(_parts_entry(base), tmp_path, connections=2, policy="github")

    assert result.path.read_bytes() == PAYLOAD
    assert state.ranges("/bf.part1") == [] and state.ranges("/bf.part2") == []


def test_unknown_policy_falls_back_to_auto(loopback, tmp_path):
    state, base = loopback
    _serve_parts(state)

    packs.download_pack(_parts_entry(base), tmp_path, connections=2, policy="whatever")

    assert state.ranges("/ranges.bin"), "未知策略按 auto（单文件优先）处理"
