from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from petcore.market import install, model, verify


def _manifest(mod_id: str, kind: str, version: str, **extra) -> dict:
    document = {
        "schema": 1,
        "id": mod_id,
        "kind": kind,
        "name": mod_id,
        "version": version,
        "api_version": "1",
        "core_requires": ">=0.1.0,<1.0.0",
    }
    document.update(extra)
    return document


def _make_zip(root: Path, name: str, manifest: dict, payload: str) -> Path:
    path = root / name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mod.json", json.dumps(manifest, ensure_ascii=False))
        archive.writestr("payload.txt", payload)
        if manifest.get("kind") == "feature":
            archive.writestr("host/factory.py", "def create(host):\n    return None\n")
    return path


def _document(package_id: str, version: str, artifact: Path, *, kind="content", extra=None):
    digest = verify.sha256_file(artifact)
    entry = {
        "id": package_id,
        "kind": kind,
        "name": package_id,
        "latest": version,
        "api_version": "1",
        "core_requires": ">=0.1.0,<1.0.0",
        "execution": "host-only",
        "versions": [
            {
                "version": version,
                "size": artifact.stat().st_size,
                "sha256": digest,
                "urls": [f"https://mirror.invalid/{package_id}-{version}.zip"],
            }
        ],
    }
    if extra:
        entry.update(extra)
    return {
        "schema_version": 1,
        "generated_at": "2026-10-22T00:00:00Z",
        "packages": [entry],
    }


def _package(document):
    index, problems = model.parse_index(document)
    assert index is not None, problems
    package = index.packages[0]
    version = model.latest_version(package)
    assert version is not None
    return package, version


class FakeFetcher:
    def __init__(self, files: dict[str, Path], *, failures: set[str] | None = None):
        self.files = dict(files)
        self.failures = set(failures or ())
        self.calls: list[str] = []

    def download(self, url, part_path, *, expected_size=None, timeout=15.0, progress=None, cancel=None):
        self.calls.append(url)
        if url in self.failures:
            raise OSError("mirror down")
        shutil.copyfile(self.files[url], part_path)
        return part_path.stat().st_size


def test_install_content_package_applies_and_writes_lock(tmp_path):
    artifact = _make_zip(tmp_path, "v1.zip", _manifest("fun.test", "content", "1.0.0"), "one")
    package, version = _package(_document("fun.test", "1.0.0", artifact))
    fetcher = FakeFetcher({version.urls[0]: artifact})
    applied: list[str] = []

    outcome = install.install_package(
        tmp_path, package, version, fetcher=fetcher, apply=lambda: applied.append("reload")
    )

    assert outcome.ok, outcome.message
    assert outcome.applied is True
    assert outcome.restart_required is False
    assert applied == ["reload"]
    installed = tmp_path / "mods" / "fun.test"
    assert (installed / "mod.json").is_file()
    assert (installed / "payload.txt").read_text(encoding="utf-8") == "one"
    lock = install.load_lock(tmp_path)
    assert lock["packages"]["fun.test"]["version"] == "1.0.0"
    assert lock["packages"]["fun.test"]["sha256"] == version.sha256


def test_update_backs_up_and_rollback_restores_the_previous_version(tmp_path):
    v1 = _make_zip(tmp_path, "v1.zip", _manifest("fun.test", "content", "1.0.0"), "one")
    v2 = _make_zip(tmp_path, "v2.zip", _manifest("fun.test", "content", "2.0.0"), "two")
    package1, version1 = _package(_document("fun.test", "1.0.0", v1))
    install.install_package(
        tmp_path, package1, version1, fetcher=FakeFetcher({version1.urls[0]: v1})
    )

    package2, version2 = _package(_document("fun.test", "2.0.0", v2))
    outcome = install.install_package(
        tmp_path, package2, version2, fetcher=FakeFetcher({version2.urls[0]: v2})
    )

    assert outcome.ok, outcome.message
    assert outcome.previous_version == "1.0.0"
    assert (tmp_path / "mods" / "fun.test" / "payload.txt").read_text(encoding="utf-8") == "two"
    backup = tmp_path / "market" / "backup" / "fun.test" / "1.0.0"
    assert (backup / "payload.txt").read_text(encoding="utf-8") == "one"

    rolled = install.rollback(tmp_path, "fun.test")

    assert rolled.ok, rolled.message
    assert rolled.version == "1.0.0"
    assert (tmp_path / "mods" / "fun.test" / "payload.txt").read_text(encoding="utf-8") == "one"
    lock = install.load_lock(tmp_path)
    assert lock["packages"]["fun.test"]["version"] == "1.0.0"
    assert lock["packages"]["fun.test"]["backup"].endswith("2.0.0")


def test_sha_mismatch_keeps_the_old_version_and_removes_the_part_file(tmp_path):
    v1 = _make_zip(tmp_path, "v1.zip", _manifest("fun.test", "content", "1.0.0"), "one")
    v2 = _make_zip(tmp_path, "v2.zip", _manifest("fun.test", "content", "2.0.0"), "two")
    package1, version1 = _package(_document("fun.test", "1.0.0", v1))
    install.install_package(
        tmp_path, package1, version1, fetcher=FakeFetcher({version1.urls[0]: v1})
    )

    document = _document("fun.test", "2.0.0", v2)
    document["packages"][0]["versions"][0]["sha256"] = "d" * 64
    package2, version2 = _package(document)
    outcome = install.install_package(
        tmp_path, package2, version2, fetcher=FakeFetcher({version2.urls[0]: v2})
    )

    assert not outcome.ok
    assert "SHA-256" in outcome.message
    assert (tmp_path / "mods" / "fun.test" / "payload.txt").read_text(encoding="utf-8") == "one"
    downloads = tmp_path / "market" / "downloads"
    assert not list(downloads.glob("*.part"))


def test_manifest_version_mismatch_is_rejected_before_replacing(tmp_path):
    v1 = _make_zip(tmp_path, "v1.zip", _manifest("fun.test", "content", "1.0.0"), "one")
    wrong = _make_zip(tmp_path, "wrong.zip", _manifest("fun.test", "content", "9.9.9"), "nine")
    package1, version1 = _package(_document("fun.test", "1.0.0", v1))
    install.install_package(
        tmp_path, package1, version1, fetcher=FakeFetcher({version1.urls[0]: v1})
    )

    package2, version2 = _package(_document("fun.test", "2.0.0", wrong))
    outcome = install.install_package(
        tmp_path, package2, version2, fetcher=FakeFetcher({version2.urls[0]: wrong})
    )

    assert not outcome.ok
    assert "version" in outcome.message
    assert (tmp_path / "mods" / "fun.test" / "payload.txt").read_text(encoding="utf-8") == "one"


def test_blocked_package_is_refused(tmp_path):
    artifact = _make_zip(tmp_path, "v1.zip", _manifest("fun.test", "content", "1.0.0"), "one")
    package, version = _package(
        _document("fun.test", "1.0.0", artifact, extra={"blocked": True, "blocked_reason": "恶意行为"})
    )

    outcome = install.install_package(
        tmp_path, package, version, fetcher=FakeFetcher({version.urls[0]: artifact})
    )

    assert not outcome.ok
    assert outcome.message == "恶意行为"
    assert not (tmp_path / "mods" / "fun.test").exists()


def test_feature_packages_need_the_global_permission(tmp_path):
    artifact = _make_zip(
        tmp_path,
        "feature.zip",
        _manifest("fun.feature", "feature", "1.0.0", execution="host-only", entrypoint="host/factory.py:create"),
        "code",
    )
    package, version = _package(_document("fun.feature", "1.0.0", artifact, kind="feature"))
    fetcher = FakeFetcher({version.urls[0]: artifact})

    blocked = install.install_package(
        tmp_path, package, version, fetcher=fetcher, allow_feature=False
    )
    assert not blocked.ok
    assert "允许功能扩展" in blocked.message
    assert not (tmp_path / "mods" / "fun.feature").exists()

    allowed = install.install_package(
        tmp_path, package, version, fetcher=fetcher, allow_feature=True
    )
    assert allowed.ok, allowed.message


def test_worker_feature_requires_a_restart_and_skips_reload(tmp_path):
    artifact = _make_zip(
        tmp_path,
        "worker.zip",
        _manifest("fun.worker", "feature", "1.0.0", execution="worker", entrypoint="host/factory.py:create"),
        "code",
    )
    package, version = _package(_document("fun.worker", "1.0.0", artifact, kind="feature"))
    applied: list[str] = []

    outcome = install.install_package(
        tmp_path,
        package,
        version,
        fetcher=FakeFetcher({version.urls[0]: artifact}),
        allow_feature=True,
        apply=lambda: applied.append("reload"),
    )

    assert outcome.ok, outcome.message
    assert outcome.restart_required is True
    assert applied == []
    assert "重启" in outcome.message


def test_first_url_failure_falls_back_to_the_next_mirror(tmp_path):
    artifact = _make_zip(tmp_path, "v1.zip", _manifest("fun.test", "content", "1.0.0"), "one")
    document = _document("fun.test", "1.0.0", artifact)
    document["packages"][0]["versions"][0]["urls"].append(
        "https://backup.invalid/fun.test-1.0.0.zip"
    )
    package, version = _package(document)
    fetcher = FakeFetcher(
        {version.urls[1]: artifact}, failures={version.urls[0]}
    )

    outcome = install.install_package(tmp_path, package, version, fetcher=fetcher)

    assert outcome.ok, outcome.message
    assert fetcher.calls == list(version.urls)
    assert outcome.attempts


def test_same_version_is_not_reinstalled(tmp_path):
    artifact = _make_zip(tmp_path, "v1.zip", _manifest("fun.test", "content", "1.0.0"), "one")
    package, version = _package(_document("fun.test", "1.0.0", artifact))
    install.install_package(
        tmp_path, package, version, fetcher=FakeFetcher({version.urls[0]: artifact})
    )

    again = install.install_package(
        tmp_path, package, version, fetcher=FakeFetcher({version.urls[0]: artifact})
    )

    assert not again.ok
    assert "已安装" in again.message


def test_incompatible_core_is_refused(tmp_path):
    artifact = _make_zip(tmp_path, "v1.zip", _manifest("fun.test", "content", "1.0.0"), "one")
    document = _document(
        "fun.test", "1.0.0", artifact, extra={"core_requires": ">=9.0.0"}
    )
    package, version = _package(document)

    outcome = install.install_package(
        tmp_path, package, version, fetcher=FakeFetcher({version.urls[0]: artifact})
    )

    assert not outcome.ok
    assert "Core" in outcome.message


def test_rollback_without_market_record_is_refused(tmp_path):
    outcome = install.rollback(tmp_path, "fun.test")

    assert not outcome.ok
    assert "没有市场安装记录" in outcome.message


# --- T04：预算、交叉校验与锁失败回滚 ---------------------------------------


class FetchAwareFetcher:
    """模拟 HttpFetcher 的 `fetch` 入口，记录它拿到的 TransferSpec。"""

    def __init__(self, artifact: Path):
        self.artifact = artifact
        self.specs: list = []
        self.limits: list = []

    def fetch(self, spec, dest_dir, *, limits=None, progress=None, cancel=None):
        from petcore.contracts.http import TransferReport

        self.specs.append(spec)
        self.limits.append(limits)
        path = Path(dest_dir) / "artifact.zip"
        path.write_bytes(self.artifact.read_bytes())
        return TransferReport(
            url=spec.urls[0], bytes_read=path.stat().st_size, path=str(path)
        )


def test_market_download_uses_hardened_fetch_with_limits_for_feature(tmp_path):
    artifact = _make_zip(
        tmp_path,
        "v1.zip",
        _manifest(
            "fun.test",
            "feature",
            "1.0.0",
            execution="host-only",
            entrypoint="host/factory.py:create",
        ),
        "one",
    )
    package, version = _package(_document("fun.test", "1.0.0", artifact, kind="feature"))
    fetcher = FetchAwareFetcher(artifact)

    outcome = install.install_package(
        tmp_path, package, version, fetcher=fetcher, allow_feature=True
    )

    assert outcome.ok, outcome.message
    assert len(fetcher.specs) == 1
    spec = fetcher.specs[0]
    assert spec.expected_size == version.size
    assert "objects.githubusercontent.com" in spec.allowed_hosts
    assert fetcher.limits[0] is not None
    assert (tmp_path / "mods" / "fun.test" / "mod.json").is_file()


def test_archive_budget_failure_leaves_no_package_behind(tmp_path):
    from petcore.contracts.archive import ArchiveLimits

    artifact = _make_zip(tmp_path, "v1.zip", _manifest("fun.test", "content", "1.0.0"), "x" * 200)
    package, version = _package(_document("fun.test", "1.0.0", artifact))

    outcome = install.install_package(
        tmp_path,
        package,
        version,
        fetcher=FakeFetcher({version.urls[0]: artifact}),
        limits=ArchiveLimits(4 * 1024 * 1024, 64, 100),
    )

    assert not outcome.ok
    assert "expanded" in outcome.message
    assert not (tmp_path / "mods" / "fun.test").exists()
    assert not list((tmp_path / "market" / "staging").glob("*")) if (tmp_path / "market" / "staging").is_dir() else True


def test_capability_not_granted_by_index_is_rejected(tmp_path):
    artifact = _make_zip(
        tmp_path,
        "v1.zip",
        _manifest(
            "fun.test", "content", "1.0.0", capabilities=["settings.write"]
        ),
        "one",
    )
    package, version = _package(_document("fun.test", "1.0.0", artifact))

    outcome = install.install_package(
        tmp_path, package, version, fetcher=FakeFetcher({version.urls[0]: artifact})
    )

    assert not outcome.ok
    assert "未授权的能力" in outcome.message
    assert not (tmp_path / "mods" / "fun.test").exists()


def test_granted_capability_in_index_is_accepted(tmp_path):
    artifact = _make_zip(
        tmp_path,
        "v1.zip",
        _manifest("fun.test", "content", "1.0.0", capabilities=["settings.read"]),
        "one",
    )
    package, version = _package(
        _document(
            "fun.test", "1.0.0", artifact, extra={"capabilities": ["settings.read"]}
        )
    )

    outcome = install.install_package(
        tmp_path, package, version, fetcher=FakeFetcher({version.urls[0]: artifact})
    )

    assert outcome.ok, outcome.message


def test_api_version_mismatch_between_pack_and_index_is_rejected(tmp_path):
    artifact = _make_zip(tmp_path, "v1.zip", _manifest("fun.test", "content", "1.0.0"), "one")
    package, version = _package(
        _document("fun.test", "1.0.0", artifact, extra={"api_version": "2"})
    )

    outcome = install.install_package(
        tmp_path, package, version, fetcher=FakeFetcher({version.urls[0]: artifact})
    )

    assert not outcome.ok
    assert "API" in outcome.message
    assert not (tmp_path / "mods" / "fun.test").exists()


def test_verify_manifest_rejects_api_version_drift_directly(tmp_path):
    import dataclasses

    import pytest

    artifact = _make_zip(tmp_path, "v1.zip", _manifest("fun.test", "content", "1.0.0"), "one")
    package, version = _package(_document("fun.test", "1.0.0", artifact))
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    with zipfile.ZipFile(artifact) as archive:
        archive.extractall(pack_dir)
    manifest = verify.read_pack_manifest(pack_dir)
    drifted = dataclasses.replace(package, api_version="2")

    with pytest.raises(Exception) as caught:
        verify.verify_manifest(manifest, drifted, version)

    assert "api_version" in str(caught.value)


def test_lock_failure_rolls_back_a_fresh_install(tmp_path, monkeypatch):
    artifact = _make_zip(tmp_path, "v1.zip", _manifest("fun.test", "content", "1.0.0"), "one")
    package, version = _package(_document("fun.test", "1.0.0", artifact))

    def boom(root, payload):
        raise OSError("disk full")

    monkeypatch.setattr(install, "save_lock", boom)

    outcome = install.install_package(
        tmp_path, package, version, fetcher=FakeFetcher({version.urls[0]: artifact})
    )

    assert not outcome.ok
    assert "已回滚" in outcome.message
    assert not (tmp_path / "mods" / "fun.test").exists()


def test_lock_failure_restores_the_previous_version(tmp_path, monkeypatch):
    first = _make_zip(tmp_path, "v1.zip", _manifest("fun.test", "content", "1.0.0"), "one")
    package1, version1 = _package(_document("fun.test", "1.0.0", first))
    assert install.install_package(
        tmp_path, package1, version1, fetcher=FakeFetcher({version1.urls[0]: first})
    ).ok

    second_dir = Path(tmp_path) / "v2"
    second_dir.mkdir(exist_ok=True)
    second = _make_zip(
        second_dir, "v2.zip", _manifest("fun.test", "content", "2.0.0"), "two"
    )
    package2, version2 = _package(_document("fun.test", "2.0.0", second))

    def boom(root, payload):
        raise OSError("disk full")

    monkeypatch.setattr(install, "save_lock", boom)

    outcome = install.install_package(
        tmp_path, package2, version2, fetcher=FakeFetcher({version2.urls[0]: second})
    )

    assert not outcome.ok
    assert "已回滚" in outcome.message
    assert install.installed_version(tmp_path, "fun.test") == "1.0.0"


def test_market_install_supports_github_mirror_with_allowlist_and_fallback(tmp_path):
    artifact = _make_zip(tmp_path, "v1.zip", _manifest("fun.test", "content", "1.0.0"), "one")
    gh_url = "https://github.com/MerZlin/dsh-pet-extensions/releases/download/v1/fun.test-1.0.0.zip"
    doc = _document("fun.test", "1.0.0", artifact)
    doc["packages"][0]["versions"][0]["urls"] = [gh_url]
    package, version = _package(doc)

    # 1. 成功经公益镜像下载，且 allowed_hosts 含代理主机 + GitHub CDN 主机
    fetcher_ok = FetchAwareFetcher(artifact)
    outcome = install.install_package(
        tmp_path / "ok",
        package,
        version,
        fetcher=fetcher_ok,
        github_mirror="gh-proxy",
    )
    assert outcome.ok, outcome.message
    assert [spec.urls[0] for spec in fetcher_ok.specs] == [f"https://gh-proxy.com/{gh_url}"]
    assert "gh-proxy.com" in fetcher_ok.specs[0].allowed_hosts
    assert "objects.githubusercontent.com" in fetcher_ok.specs[0].allowed_hosts

    # 2. 自定义前缀失败时自动回退到原始 GitHub 地址
    class _FailFirstFetcher(FetchAwareFetcher):
        def fetch(self, spec, dest_dir, *, limits=None, progress=None, cancel=None):
            if spec.urls[0].startswith("https://my.mirror/"):
                self.specs.append(spec)
                self.limits.append(limits)
                raise OSError("custom proxy down")
            return super().fetch(spec, dest_dir, limits=limits, progress=progress, cancel=cancel)

    fetcher_fb = _FailFirstFetcher(artifact)
    outcome2 = install.install_package(
        tmp_path / "fb",
        package,
        version,
        fetcher=fetcher_fb,
        github_mirror="custom",
        custom_mirror_prefix="https://my.mirror",
    )
    assert outcome2.ok, outcome2.message
    assert [spec.urls[0] for spec in fetcher_fb.specs] == [
        f"https://my.mirror/{gh_url}",
        gh_url,
    ]
    assert "my.mirror" in fetcher_fb.specs[0].allowed_hosts
