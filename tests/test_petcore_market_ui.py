from __future__ import annotations

import json
import os
import shutil
import threading
import time
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from petcore.market import model, verify
from petcore.market.source import IndexFetch, IndexFetchError
from petcore.ui.settings.page_extensions import ExtensionsPage
from petcore.ui.settings.page_market import MarketPage
from tests.test_petcore_settings import FakeHost


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _make_zip(path: Path, mod_id: str, version: str, payload: str, *, kind="content") -> Path:
    manifest = {
        "schema": 1,
        "id": mod_id,
        "kind": kind,
        "name": mod_id,
        "version": version,
        "api_version": "1",
        "core_requires": ">=0.1.0,<1.0.0",
    }
    if kind == "feature":
        manifest["entrypoint"] = "host/factory.py:create"
        manifest["execution"] = "host-only"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mod.json", json.dumps(manifest, ensure_ascii=False))
        archive.writestr("payload.txt", payload)
        if kind == "feature":
            archive.writestr("host/factory.py", "def create(host):\n    return None\n")
    return path


def _document(path: Path, url: str, *, mod_id="fun.test", kind="content", version="1.0.0") -> dict:
    return {
        "schema_version": 1,
        "generated_at": "2026-10-22T00:00:00Z",
        "packages": [
            {
                "id": mod_id,
                "kind": kind,
                "name": mod_id,
                "name_zh": "测试插件",
                "summary_zh": "用于测试的市场插件",
                "latest": version,
                "api_version": "1",
                "core_requires": ">=0.1.0,<1.0.0",
                "versions": [
                    {
                        "version": version,
                        "size": path.stat().st_size,
                        "sha256": verify.sha256_file(path),
                        "urls": [url],
                        "changelog": f"{version} 的更新说明",
                    }
                ],
            }
        ],
    }


class FakeFetcher:
    def __init__(self, document: dict, files: dict[str, Path], *, fail: bool = False):
        self.body = json.dumps(document).encode("utf-8")
        self.files = dict(files)
        self.fail = fail
        self.index_calls = 0

    def fetch_index(self, url, *, etag="", timeout=8.0):
        self.index_calls += 1
        if self.fail:
            raise IndexFetchError("network", url)
        return IndexFetch(body=self.body, etag='"v1"')

    def download(self, url, part_path, *, expected_size=None, timeout=15.0, progress=None, cancel=None):
        if self.fail:
            raise OSError("mirror down")
        shutil.copyfile(self.files[url], part_path)
        return part_path.stat().st_size


def _page(tmp_path, fetcher):
    host = FakeHost(tmp_path)
    return host, MarketPage(host, fetcher=fetcher)


def test_market_page_lists_and_installs_a_content_package(app, tmp_path):
    artifact = _make_zip(tmp_path / "v1.zip", "fun.test", "1.0.0", "one")
    url = "https://mirror.invalid/fun.test-1.0.0.zip"
    fetcher = FakeFetcher(_document(artifact, url), {url: artifact})
    host, page = _page(tmp_path, fetcher)

    page.refresh(blocking=True)

    assert page.package_list.count() == 1
    assert "未安装" in page.package_list.item(0).text()
    assert page.install_button.isEnabled()

    page.install_selected(blocking=True)

    assert (tmp_path / "mods" / "fun.test" / "payload.txt").read_text(encoding="utf-8") == "one"
    assert host.reloaded is True
    assert "已安装" in page.status_label.text()
    assert "已安装" in page.package_list.item(0).text()


def test_market_page_updates_then_rolls_back(app, tmp_path):
    files = tmp_path / "files"
    files.mkdir()
    v1 = _make_zip(files / "fun.test-1.0.0.zip", "fun.test", "1.0.0", "one")
    url1 = "https://mirror.invalid/fun.test-1.0.0.zip"
    fetcher = FakeFetcher(_document(v1, url1), {url1: v1})
    host, page = _page(tmp_path, fetcher)
    page.refresh(blocking=True)
    page.install_selected(blocking=True)
    assert page.selected_package() is not None

    v2 = _make_zip(files / "fun.test-2.0.0.zip", "fun.test", "2.0.0", "two")
    url2 = "https://mirror.invalid/fun.test-2.0.0.zip"
    fetcher.body = json.dumps(_document(v2, url2, version="2.0.0")).encode("utf-8")
    fetcher.files[url2] = v2
    page.refresh(blocking=True)

    assert "可更新" in page.package_list.item(0).text()
    page.install_selected(blocking=True)
    assert (tmp_path / "mods" / "fun.test" / "payload.txt").read_text(encoding="utf-8") == "two"

    page.rollback_selected(blocking=True)
    assert (tmp_path / "mods" / "fun.test" / "payload.txt").read_text(encoding="utf-8") == "one"
    assert "回滚" in page.status_label.text()


def test_market_page_shows_stale_banner_from_cache(app, tmp_path):
    artifact = _make_zip(tmp_path / "v1.zip", "fun.test", "1.0.0", "one")
    url = "https://mirror.invalid/fun.test-1.0.0.zip"
    good = FakeFetcher(_document(artifact, url), {url: artifact})
    host = FakeHost(tmp_path)
    first = MarketPage(host, fetcher=good)
    first.refresh(blocking=True)
    assert "可能过期" not in first.source_label.text()

    failing = FakeFetcher(_document(artifact, url), {url: artifact}, fail=True)
    second = MarketPage(host, fetcher=failing)
    second.refresh(blocking=True)

    assert "可能过期" in second.source_label.text()
    assert second.package_list.count() == 1


def test_market_page_saves_and_discards_the_index_url(app, tmp_path):
    artifact = _make_zip(tmp_path / "v1.zip", "fun.test", "1.0.0", "one")
    url = "https://mirror.invalid/fun.test-1.0.0.zip"
    host, page = _page(tmp_path, FakeFetcher(_document(artifact, url), {url: artifact}))

    page.index_field.setText("https://mirror.invalid/index.json")

    assert page.dirty() is True
    page.confirm_save()
    assert host.config.get("market_index_url") == "https://mirror.invalid/index.json"
    assert page.dirty() is False

    page.index_field.setText("https://other.invalid/index.json")
    page.discard_changes()
    assert page.index_field.text() == "https://mirror.invalid/index.json"
    assert host.config.get("market_index_url") == "https://mirror.invalid/index.json"


def test_market_page_blocks_feature_install_until_allowed(app, tmp_path):
    artifact = _make_zip(tmp_path / "feature.zip", "fun.feature", "1.0.0", "code", kind="feature")
    url = "https://mirror.invalid/fun.feature-1.0.0.zip"
    fetcher = FakeFetcher(
        _document(artifact, url, mod_id="fun.feature", kind="feature"), {url: artifact}
    )
    host, page = _page(tmp_path, fetcher)
    page.refresh(blocking=True)

    assert page.install_button.isEnabled() is False

    page.install_selected(blocking=True)
    assert "允许功能扩展" in page.status_label.text()
    assert not (tmp_path / "mods" / "fun.feature").exists()

    host.config.set("allow_feature_mods", True)
    page.select_package("fun.feature")
    assert page.install_button.isEnabled() is True
    page.install_selected(blocking=True)
    assert (tmp_path / "mods" / "fun.feature" / "mod.json").is_file()


def test_extensions_nav_lists_the_market_page(app, tmp_path):
    host = FakeHost(tmp_path)
    page = ExtensionsPage(host)

    labels = [page.nav.item(row).text() for row in range(page.nav.count())]
    assert "扩展管理" in labels
    assert "插件市场" in labels
    assert labels.index("插件市场") == labels.index("扩展管理") + 1


def test_dispose_during_reload_keeps_the_thread_alive_and_drops_the_result(tmp_path, app):
    """设置页销毁时不销毁仍在跑的 QThread，迟到的索引结果也不能再写回页面。"""
    import threading
    import time

    from petcore.market.source import IndexFetch
    from petcore.ui.settings import page_market

    started = threading.Event()
    release = threading.Event()

    class SlowFetcher:
        def fetch_index(self, url, *, etag="", timeout=8.0):
            started.set()
            assert release.wait(5.0), "test did not release the fetcher"
            return IndexFetch(
                body=b'{"schema_version": 1, "packages": []}', etag="slow-1"
            )

        def download(self, *_args, **_kwargs):
            raise AssertionError("dispose test must not download artifacts")

    host = FakeHost(tmp_path)
    page = MarketPage(host, fetcher=SlowFetcher())
    page.refresh()

    assert started.wait(5.0)
    page.dispose()
    release.set()

    deadline = time.monotonic() + 10.0
    while page_market._ORPHAN_JOBS and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert not page_market._ORPHAN_JOBS
    assert page._disposed is True
    assert page._result is None
    assert page._packages == []


# --- 分段下载的进度条 -------------------------------------------------------

#: 两次进度采样之间要跨过节流窗口（page_market.PROGRESS_INTERVAL_S）。
PROGRESS_WAIT_S = 0.15


def _pump_until(app, predicate, *, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


class _ProgressFetcher:
    """只实现 download：安装线程把 progress 回调转成页面上的进度。"""

    def __init__(self, document: dict, files: dict[str, Path]) -> None:
        self.body = json.dumps(document).encode("utf-8")
        self.files = dict(files)
        self.started = threading.Event()
        self.release = threading.Event()

    def fetch_index(self, url, *, etag="", timeout=8.0):
        return IndexFetch(body=self.body, etag='"v1"')

    def download(self, url, part_path, *, expected_size=None, timeout=15.0, progress=None, cancel=None):
        self.started.set()
        size = expected_size or self.files[url].stat().st_size
        if progress is not None:
            progress(size // 2, size)
        # 第二次采样必须跨过节流窗口，速度/ETA 才有意义。
        time.sleep(PROGRESS_WAIT_S)
        if progress is not None:
            progress(3 * size // 4, size)
        assert self.release.wait(10.0), "test did not release the download"
        shutil.copyfile(self.files[url], part_path)
        if progress is not None:
            progress(size, size)
        return part_path.stat().st_size


def test_market_page_tracks_install_progress(app, tmp_path):
    artifact = _make_zip(tmp_path / "v1.zip", "fun.test", "1.0.0", "one")
    url = "https://mirror.invalid/fun.test-1.0.0.zip"
    document = _document(artifact, url)
    fetcher = _ProgressFetcher(document, {url: artifact})
    host = FakeHost(tmp_path)
    page = MarketPage(host, fetcher=fetcher)
    page.refresh(blocking=True)
    size = document["packages"][0]["versions"][0]["size"]

    page.install_selected()
    assert fetcher.started.wait(5.0), "安装线程没有开始下载"
    assert _pump_until(app, lambda: page.progress_bar.value() == 3 * size // 4), (
        page.progress_bar.value(),
        size,
    )

    assert not page.progress_bar.isHidden(), "下载中进度条必须可见"
    assert page.progress_bar.maximum() == size
    text = page.progress_label.text()
    assert "MiB" in text or "KiB" in text or " B " in text
    assert "连接" in text
    assert "剩余" in text, text

    fetcher.release.set()
    assert _pump_until(app, lambda: "已安装" in page.status_label.text())
    assert page.progress_bar.isHidden(), "安装结束后进度条收起"
    assert page.progress_bar.value() == page.progress_bar.maximum()


def test_market_page_cancel_button_stops_the_install(app, tmp_path):
    artifact = _make_zip(tmp_path / "v1.zip", "fun.test", "1.0.0", "one")
    url = "https://mirror.invalid/fun.test-1.0.0.zip"
    fetcher = _ProgressFetcher(_document(artifact, url), {url: artifact})
    host = FakeHost(tmp_path)
    page = MarketPage(host, fetcher=fetcher)
    page.refresh(blocking=True)

    page.install_selected()
    assert fetcher.started.wait(5.0)
    assert page.cancel_button.isEnabled()

    page.cancel_button.click()
    fetcher.release.set()

    assert _pump_until(app, lambda: not page._busy)
    assert not page.cancel_button.isEnabled()
    assert "取消" in page.status_label.text()
    assert page.progress_bar.isHidden()
    assert not (tmp_path / "mods" / "fun.test").exists()


def test_market_page_progress_text_reports_speed_eta_resume_and_connections(app):
    text = MarketPage.describe_progress(
        done=2 * 1024 * 1024,
        total=8 * 1024 * 1024,
        speed=1024 * 1024,
        eta=6.0,
        connections=4,
        resumed=1024 * 1024,
    )

    assert "2.0 MiB / 8.0 MiB" in text
    assert "1.0 MiB/s" in text
    assert "剩余 6 秒" in text
    assert "4 连接" in text
    assert "续传 1.0 MiB" in text


def test_market_page_progress_text_without_speed(app):
    text = MarketPage.describe_progress(
        done=0, total=1024, speed=0.0, eta=None, connections=4, resumed=0
    )

    assert "0 B / 1.0 KiB" in text
    assert "4 连接" in text
    assert "续传" not in text
