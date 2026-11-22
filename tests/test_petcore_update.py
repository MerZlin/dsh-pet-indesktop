from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from petcore.update import UpdateAsset, UpdateManifest, UpdateResult, UpdateService, parse_manifest


class _Response:
    def __init__(self, body: bytes = b"", *, status: int = 200, headers=None, url: str | None = None):
        self._body = body
        self.status = status
        self.headers = headers or {}
        self._url = url
        self.closed = False

    def read(self, _limit=-1):
        value, self._body = self._body, b""
        return value

    def geturl(self):
        return self._url or "https://api.github.com/releases/latest"

    def close(self):
        self.closed = True


def _manifest(data: bytes, *, version: str = "9.0.0") -> tuple[dict, UpdateAsset]:
    digest = hashlib.sha256(data).hexdigest()
    asset = UpdateAsset("petcore-setup.exe", "https://github.com/example/petcore-setup.exe", len(data), digest)
    return {
        "version": version,
        "release_url": "https://github.com/example/releases/9.0.0",
        "assets": [asset.__dict__ if hasattr(asset, "__dict__") else {
            "name": asset.name, "url": asset.url, "size": asset.size, "sha256": asset.sha256,
            "platform": asset.platform, "architecture": asset.architecture, "variant": asset.variant,
        }],
    }, asset


def test_parse_manifest_rejects_untrusted_urls_and_keeps_valid_assets() -> None:
    raw, asset = _manifest(b"installer")
    raw["assets"].append({"name": "bad.exe", "url": "http://example.com/bad.exe", "size": 1, "sha256": "0" * 64})
    parsed = parse_manifest(raw)
    assert parsed.version == "9.0.0"
    assert parsed.assets == (asset,)
    with pytest.raises(ValueError):
        parse_manifest({"version": "1.0.0", "release_url": "file:///tmp/release", "assets": []})


def test_update_check_uses_etag_cache_and_304(tmp_path: Path) -> None:
    raw, _asset = _manifest(b"installer")
    calls = []

    def first(request):
        calls.append(dict(request.header_items()))
        return _Response(json.dumps(raw).encode(), headers={"ETag": '"abc"'})

    service = UpdateService(tmp_path, current_version="1.0.0", fetcher=first)
    assert service.check(force=True).state == "available"
    assert service.check().state == "available"

    def second(request):
        calls.append(dict(request.header_items()))
        return _Response(status=304, headers={"ETag": '"abc"'})

    service.fetcher = second
    assert service.check(force=True).state == "available"
    assert any(key.lower() == "if-none-match" and value == '"abc"' for key, value in calls[-1].items())


def test_download_resumes_206_and_verifies_digest(tmp_path: Path) -> None:
    body = b"0123456789"
    raw, asset = _manifest(body)
    service = UpdateService(tmp_path, fetcher=lambda request: _Response(json.dumps(raw).encode()))
    service._manifest = parse_manifest(raw)
    service.update_dir.mkdir(parents=True)
    part = service.update_dir / (asset.name + ".part")
    part.write_bytes(body[:4])
    (service.update_dir / (asset.name + ".part.json")).write_text(json.dumps({
        "url": asset.url, "size": asset.size, "sha256": asset.sha256, "name": asset.name,
    }), encoding="utf-8")

    def fetch(request):
        assert request.get_header("Range") == "bytes=4-"
        return _Response(body[4:], status=206, headers={"Content-Range": "bytes 4-9/10"}, url=asset.url)

    service.fetcher = fetch
    result = service.download(asset)
    assert result.read_bytes() == body
    assert service.status().state == "downloaded"


def test_download_cancel_preserves_partial_state(tmp_path: Path) -> None:
    body = b"0123456789"
    _raw, asset = _manifest(body)

    class _Chunked(_Response):
        def __init__(self):
            super().__init__(body, url=asset.url)
            self.calls = 0

        def read(self, _limit=-1):
            self.calls += 1
            if self.calls == 1:
                return body[:4]
            return body[4:]

    service = UpdateService(tmp_path, fetcher=lambda request: _Chunked())
    calls = 0

    def cancel():
        nonlocal calls
        calls += 1
        return calls > 1

    with pytest.raises(InterruptedError):
        service.download(asset, cancel=cancel)
    assert (tmp_path / "updates" / (asset.name + ".part")).exists()
    assert (tmp_path / "updates" / (asset.name + ".part.json")).exists()


def test_download_transport_failure_preserves_resume_state(tmp_path: Path) -> None:
    body = b"0123456789"
    _raw, asset = _manifest(body)
    service = UpdateService(tmp_path, fetcher=lambda request: (_ for _ in ()).throw(OSError("offline")))
    service.update_dir.mkdir(parents=True)
    part = service.update_dir / (asset.name + ".part")
    part.write_bytes(body[:4])

    with pytest.raises(ConnectionError):
        service.download(asset)

    assert part.read_bytes() == body[:4]
    assert (tmp_path / "updates" / (asset.name + ".part.json")).exists()
    assert service.status().state == "download_failed"


def test_static_manifest_dictionary_and_cdn_fallback_are_supported(tmp_path: Path) -> None:
    body = b"installer"
    digest = hashlib.sha256(body).hexdigest()
    raw = {
        "version": "v2.0.0",
        "html_url": "https://github.com/MerZlin/dsh-pet-indesktop/releases/tag/v2.0.0",
        "assets": {
            "petcore-setup.exe": {
                "urls": [
                    "https://github.com/MerZlin/dsh-pet-indesktop/releases/download/v2.0.0/petcore-setup.exe",
                    "https://cdn.jsdelivr.net/gh/MerZlin/dsh-pet-indesktop@main/petcore-setup.exe",
                ],
                "size": len(body),
                "sha256": digest,
            }
        },
    }
    parsed = parse_manifest(raw)
    assert parsed.version == "2.0.0"
    assert parsed.assets[0].candidate_urls()[1].startswith("https://cdn.jsdelivr.net/")

    calls = []
    payload = json.dumps(raw).encode()

    def fetch(request):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise OSError("primary unavailable")
        return _Response(payload, url=calls[-1])

    service = UpdateService(
        tmp_path,
        current_version="1.0.0",
        fetcher=fetch,
        fallback_manifest_urls=("https://cdn.jsdelivr.net/gh/MerZlin/dsh-pet-indesktop@main/update.json",),
    )
    result = service.check(force=True)
    assert result.ok is True
    assert result.state == "available"
    assert calls == [service.manifest_url, service.fallback_manifest_urls[0]]


def test_download_tries_trusted_asset_mirror_after_transport_failure(tmp_path: Path) -> None:
    body = b"mirror-installer"
    digest = hashlib.sha256(body).hexdigest()
    primary = "https://github.com/example/petcore-setup.exe"
    mirror = "https://cdn.jsdelivr.net/gh/example/petcore-setup.exe"
    asset = UpdateAsset("petcore-setup.exe", primary, len(body), digest, urls=(primary, mirror))
    calls: list[str] = []

    def fetch(request):
        calls.append(request.full_url)
        if request.full_url == primary:
            raise OSError("primary unavailable")
        return _Response(body, url=mirror)

    service = UpdateService(tmp_path, fetcher=fetch)
    result = service.download(asset)
    assert result.read_bytes() == body
    assert calls == [primary, mirror]
    assert service.status().state == "downloaded"


def _write_verified_meta(package: Path, digest: str) -> None:
    import json

    meta = package.with_suffix(package.suffix + ".verify.json")
    meta.write_text(
        json.dumps({"name": package.name, "size": package.stat().st_size, "sha256": digest}),
        encoding="utf-8",
    )


def test_update_install_is_source_safe_and_frozen_windows_only(tmp_path: Path) -> None:
    package = tmp_path / "updates" / "petcore-setup.exe"
    package.parent.mkdir()
    package.write_bytes(b"candidate")

    source_service = UpdateService(tmp_path / "source", platform="windows", frozen=False)
    source_result = source_service.install(package)
    assert source_result.state == "unsupported_runtime"

    launched: list[Path] = []
    frozen_service = UpdateService(
        tmp_path / "frozen",
        platform="windows",
        frozen=True,
        launcher=launched.append,
    )
    frozen_package = tmp_path / "frozen" / "updates" / package.name
    frozen_package.parent.mkdir(parents=True)
    frozen_package.write_bytes(b"candidate")
    _write_verified_meta(frozen_package, hashlib.sha256(b"candidate").hexdigest())
    # 侧车不能自证：安装门禁还要求「与更新清单登记条目一致」。
    frozen_service._manifest = UpdateManifest(  # noqa: SLF001 - 单元测试直接给清单
        version="9.0.0",
        release_url="https://github.com/example/releases/9.0.0",
        notes="",
        assets=(
            UpdateAsset(
                frozen_package.name,
                f"https://github.com/example/{frozen_package.name}",
                frozen_package.stat().st_size,
                hashlib.sha256(b"candidate").hexdigest(),
            ),
        ),
    )
    result = frozen_service.install(frozen_package)
    assert result.ok is True
    assert result.state == "installer_started"
    assert launched == [frozen_package]


def test_update_install_rejects_a_package_swapped_after_verification(tmp_path: Path) -> None:
    launched: list[Path] = []
    service = UpdateService(
        tmp_path,
        platform="windows",
        frozen=True,
        launcher=launched.append,
    )
    package = tmp_path / "updates" / "petcore-setup.exe"
    package.parent.mkdir()
    package.write_bytes(b"candidate")
    _write_verified_meta(package, hashlib.sha256(b"candidate").hexdigest())

    package.write_bytes(b"tampered")
    result = service.install(package)
    assert result.ok is False
    assert result.state == "invalid_package"
    assert launched == []


def test_update_install_without_verified_meta_is_refused(tmp_path: Path) -> None:
    """重启后靠侧车记录恢复可安装状态；没有侧车就不能装。"""
    launched: list[Path] = []
    service = UpdateService(tmp_path, platform="windows", frozen=True, launcher=launched.append)
    package = tmp_path / "updates" / "petcore-setup.exe"
    package.parent.mkdir()
    package.write_bytes(b"candidate")
    result = service.install(package)
    assert result.ok is False
    assert result.state == "invalid_package"
    assert launched == []


def test_update_download_writes_verified_meta_for_later_install(tmp_path: Path) -> None:
    body = b"payload"
    digest = hashlib.sha256(body).hexdigest()
    asset = UpdateAsset(
        name="petcore-setup.exe",
        url="https://api.github.com/repos/o/r/releases/assets/1",
        size=len(body),
        sha256=digest,
    )

    def fetch(_request):
        return _Response(body)

    service = UpdateService(tmp_path, platform="windows", frozen=True, fetcher=fetch)
    path = service.download(asset)
    meta = path.with_suffix(path.suffix + ".verify.json")
    assert meta.is_file()
    assert service._package_is_verified(path) is True

def test_update_check_stops_when_cancel_was_requested(tmp_path: Path) -> None:
    requests: list[object] = []

    def fetch(request):
        requests.append(request)
        raise AssertionError("取消后不应再发请求")

    service = UpdateService(tmp_path, fetcher=fetch)
    service.cancel()
    result = service.check(force=True)
    assert result.state == "cancelled"
    assert requests == []
    service.reset_cancel()


def test_download_does_not_clear_a_cancel_that_arrived_before_it_started(tmp_path: Path) -> None:
    """排队期间按下的取消不能被 worker 开头的 clear 吞掉。"""
    body = b"payload"
    asset = UpdateAsset(
        name="petcore-setup.exe",
        url="https://api.github.com/repos/o/r/releases/assets/1",
        size=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
    )

    def fetch(_request):
        raise AssertionError("取消后不应再发请求")

    service = UpdateService(tmp_path, fetcher=fetch)
    service.cancel()
    with pytest.raises(InterruptedError):
        service.download(asset)


def test_update_download_supports_proxy_mirror_and_falls_back_on_bad_digest(
    tmp_path: Path,
) -> None:
    body = b"real-installer-payload"
    digest = hashlib.sha256(body).hexdigest()
    release_url = "https://github.com/MerZlin/dsh-pet-indesktop/releases/download/v4.3.4/petcore-setup.exe"
    asset = UpdateAsset("petcore-setup.exe", release_url, len(body), digest)
    calls: list[str] = []

    def fetch(request):
        calls.append(request.full_url)
        if request.full_url.startswith("https://gh-proxy.com/"):
            return _Response(b"bad!" + body[4:], url=request.full_url)
        return _Response(body, url=request.full_url)

    service = UpdateService(tmp_path, fetcher=fetch)
    path = service.download(asset, mirror_policy="gh-proxy")
    assert path.read_bytes() == body
    assert calls == [f"https://gh-proxy.com/{release_url}", release_url]


def test_update_download_blocks_untrusted_proxy_redirect_and_check_never_uses_proxy(
    tmp_path: Path,
) -> None:
    body = b"custom-installer"
    digest = hashlib.sha256(body).hexdigest()
    release_url = "https://github.com/MerZlin/dsh-pet-indesktop/releases/download/v4.3.4/petcore-setup.exe"
    asset = UpdateAsset("petcore-setup.exe", release_url, len(body), digest)
    calls: list[str] = []

    def fetch(request):
        calls.append(request.full_url)
        if request.full_url.startswith("https://custom.mirror/"):
            # 模拟跳转到不在白名单的第三方域，必须被拒绝并回退原地址。
            return _Response(body, url="https://evil.example.net/payload.exe")
        return _Response(body, url=request.full_url)

    service = UpdateService(tmp_path, fetcher=fetch)
    path = service.download(
        asset, mirror_policy="custom", custom_mirror_prefix="https://custom.mirror"
    )
    assert path.read_bytes() == body
    assert calls == [f"https://custom.mirror/{release_url}", release_url]

    check_urls: list[str] = []
    manifest_payload = json.dumps(
        {
            "version": "9.0.0",
            "release_url": "https://github.com/MerZlin/dsh-pet-indesktop/releases/tag/v9.0.0",
            "assets": [],
        }
    ).encode()

    def check_fetch(request):
        check_urls.append(request.full_url)
        return _Response(manifest_payload, url=request.full_url)

    check_service = UpdateService(tmp_path / "chk", current_version="1.0.0", fetcher=check_fetch)
    assert check_service.check(force=True).state == "available"
    assert all("gh-proxy" not in u and "custom.mirror" not in u for u in check_urls)
