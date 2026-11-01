"""「常规 → 数据」里的素材包下载行与存储位置行。

锁住五件事：两个素材包行都在「数据」卡片里、每行有「下载 / 打开 GitHub / 夸克网盘」
三枚按钮、下载按钮走应用内下载（不打开浏览器、进度区可见）、外链仍指向已验证地址、
这些行都能被设置搜索命中且不制造草稿差异。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QLabel, QPushButton

from petcore.ui import external_links
from petcore.ui.settings.base import SettingsCard, page_search_texts
from tests.test_petcore_settings import _host

PACK_ROWS = ("完整角色素材包", "高清完整角色素材包")


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    instance = QApplication.instance()
    if instance is None:
        instance = QApplication([])
    return instance


def _general_page(app, tmp_path: Path):
    from petcore.ui.settings.pages_core import GeneralPage

    return GeneralPage(_host(tmp_path))


def _data_card(page) -> SettingsCard:
    title = next(
        label
        for label in page.findChildren(QLabel)
        if label.objectName() == "sectionTitle" and label.text().strip() == "数据"
    )
    card = title.parentWidget().findChild(SettingsCard)
    assert card is not None
    return card


def _row_buttons(page, title: str) -> list[QPushButton]:
    """按行标题找到该行容器里的按钮（``add_row`` 的行 = 标题 → 文字块 → 行）。"""
    label = next(
        label
        for label in page.findChildren(QLabel)
        if label.objectName() == "settingLabel" and label.text().strip() == title
    )
    row = label.parentWidget().parentWidget()
    return list(row.findChildren(QPushButton))


def _button(buttons: list[QPushButton], text: str) -> QPushButton:
    matches = [button for button in buttons if button.text().strip() == text]
    assert len(matches) == 1, [button.text() for button in buttons]
    return matches[0]


def test_both_pack_rows_sit_in_the_data_card(app, tmp_path: Path) -> None:
    page = _general_page(app, tmp_path)
    try:
        titles = [
            label.text().strip()
            for label in _data_card(page).findChildren(QLabel)
            if label.objectName() == "settingLabel"
        ]
        assert "角色资源包" in titles
        assert "数据目录" in titles
        assert "缓存目录" in titles
        for title in PACK_ROWS:
            assert title in titles, titles
        # 素材包行必须排在「角色资源包」之后，并且高清包紧跟在完整包之后。
        assert titles.index("角色资源包") < titles.index(PACK_ROWS[0])
        assert titles.index(PACK_ROWS[0]) < titles.index(PACK_ROWS[1])
    finally:
        page.deleteLater()


def test_each_pack_row_has_download_and_both_links(app, tmp_path: Path) -> None:
    page = _general_page(app, tmp_path)
    try:
        for title in PACK_ROWS:
            buttons = _row_buttons(page, title)
            texts = [button.text().strip() for button in buttons]
            assert "GitHub 下载" in texts, (title, texts)
            assert "打开 GitHub" in texts, (title, texts)
            assert "夸克网盘" in texts, (title, texts)
            # 导入按钮在下载完成前是隐藏的；不许靠 setFixedWidth 钉死宽度。
            import_button = _button(buttons, "导入这个素材包")
            assert import_button.isHidden() is True
            assert min(button.minimumWidth() for button in buttons) < 60
    finally:
        page.deleteLater()


def test_pack_row_texts_are_searchable(app, tmp_path: Path) -> None:
    page = _general_page(app, tmp_path)
    try:
        haystack = " ".join(page_search_texts(page))
        for needle in ("素材包", "夸克网盘", "GitHub 下载", "高清", "缓存目录", "清空缓存"):
            assert needle in haystack, needle
        # 提示文案要说清「下载后怎么装」，不能只剩几枚裸按钮。
        assert "导入角色目录" in haystack
        assert "导入这个素材包" in haystack
    finally:
        page.deleteLater()


def test_download_button_stays_in_app(app, tmp_path: Path, monkeypatch) -> None:
    """点「GitHub 下载」不打开浏览器，而是显示进度区（任务本身被替身拦下）。"""
    from petcore.ui.settings import pack_download

    opened: list[str] = []
    monkeypatch.setattr(
        QDesktopServices,
        "openUrl",
        lambda url: opened.append(url.toString()) or True,
    )
    started: list[object] = []

    def _fake_start(self) -> None:
        started.append(self)

    monkeypatch.setattr(pack_download.PackJob, "start", _fake_start)
    page = _general_page(app, tmp_path)
    try:
        row = page.pack_rows["full"]
        _button(_row_buttons(page, PACK_ROWS[0]), "GitHub 下载").click()
        assert opened == []
        assert started, "下载任务没有启动"
        assert row.panel.isHidden() is False
        assert row.download_button.isEnabled() is False
        row.cancel_download()
        assert row.download_button.isEnabled() is True
    finally:
        page.deleteLater()


def test_pack_links_open_the_verified_urls(app, tmp_path: Path, monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        QDesktopServices,
        "openUrl",
        lambda url: opened.append(url.toString()) or True,
    )
    page = _general_page(app, tmp_path)
    try:
        for title in PACK_ROWS:
            buttons = _row_buttons(page, title)
            _button(buttons, "打开 GitHub").click()
            _button(buttons, "夸克网盘").click()
        assert opened == [
            external_links.CHARACTER_PACKS_URL,
            external_links.QUARK_URL,
            external_links.CHARACTER_PACKS_URL,
            external_links.QUARK_URL,
        ]
    finally:
        page.deleteLater()


def test_pack_links_leave_no_draft_behind(app, tmp_path: Path, monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        QDesktopServices,
        "openUrl",
        lambda url: opened.append(url.toString()) or True,
    )
    page = _general_page(app, tmp_path)
    try:
        assert page.dirty() is False
        for title in PACK_ROWS:
            for button in _row_buttons(page, title):
                if button.text().strip() in ("GitHub 下载", "导入这个素材包"):
                    continue
                button.click()
        # 外链只开浏览器：不写配置、不进草稿，所以「保存」按钮不该因此亮起。
        assert len(opened) == 4
        assert page.dirty() is False
    finally:
        page.deleteLater()


def test_index_url_points_at_the_animations_repo() -> None:
    assert external_links.PACK_INDEX_URL.startswith("https://")
    assert "MerZlin/Animations" in external_links.PACK_INDEX_URL
    assert external_links.PACK_INDEX_URL.endswith("packs-index.json")
    # 主源是 Release 资产（走 api 直链，永远最新），raw 只做兜底。
    assert "/releases/download/" in external_links.PACK_INDEX_URL
    assert external_links.PACK_INDEX_URL.endswith("packs-index.json")
    assert external_links.PACK_INDEX_FALLBACKS
    assert all(url.startswith("https://") for url in external_links.PACK_INDEX_FALLBACKS)
    assert any("jsdelivr" in url for url in external_links.PACK_INDEX_FALLBACKS)
    assert any("raw.githubusercontent.com" in url for url in external_links.PACK_INDEX_FALLBACKS)


def _fake_download(path: Path):
    from types import SimpleNamespace

    return SimpleNamespace(path=path, from_cache=True, connections=1)


def test_import_uses_the_index_character_id_not_the_zip_name(app, tmp_path: Path) -> None:
    """`shenshen-1.zip` 不能装成 `character.shenshen-1`：角色 id 取索引里的 character。"""
    from types import SimpleNamespace

    from petcore.ui.settings.pack_download import PackJob

    page = _general_page(app, tmp_path)
    try:
        row = page.pack_rows["full"]
        archive = tmp_path / "shenshen-1.zip"
        archive.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        job = PackJob(
            target=row._target, dest_dir=tmp_path, cache_file=tmp_path / "index.json"
        )
        job.entry = SimpleNamespace(character="shenshen")
        row._job = job
        row._on_done(_fake_download(archive))
        assert row.character_id() == "shenshen"
        assert row.import_button.isHidden() is False
    finally:
        page.dispose()
        page.deleteLater()


def test_import_falls_back_to_the_pack_id_without_an_index_entry(app, tmp_path: Path) -> None:
    page = _general_page(app, tmp_path)
    try:
        row = page.pack_rows["full"]
        archive = tmp_path / "shenshen-1.zip"
        archive.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        row._job = None
        row._on_done(_fake_download(archive))
        assert row.character_id() == "shenshen"
    finally:
        page.dispose()
        page.deleteLater()


def test_index_loader_falls_back_to_the_second_source(tmp_path, monkeypatch):
    """主索引源挂了要走兜底，且成功的那份要落缓存。"""
    import json

    from petcore.ui.settings import pack_download

    calls: list[str] = []

    def fake_fetch(url, timeout=15.0):
        calls.append(url)
        if "releases/download" in url:
            raise ConnectionError("blocked")
        return json.dumps(
            {
                "schema": 1,
                "packs": [
                    {
                        "id": "blue-fish",
                        "name": "蓝胖鱼",
                        "version": "1",
                        "filename": "blue-fish.zip",
                        "size": 10,
                        "sha256": "a" * 64,
                        "character": "blue_fish",
                        "urls": ["https://github.com/x/y/releases/download/v1/blue-fish.zip"],
                    }
                ],
            }
        ).encode()

    monkeypatch.setattr(pack_download, "fetch_index_payload", fake_fetch)
    cache = tmp_path / "index.json"
    index = pack_download.fetch_first_index(
        (
            "https://github.com/a/b/releases/download/v1/i.json",
            "https://raw.githubusercontent.com/a/b/i.json",
        ),
        cache,
    )
    assert [p.id for p in index.packs] == ["blue-fish"]
    # 主源连续失败（含重试）后落到第二条；顺序必须是主源在前。
    assert calls[0] == calls[1] and "releases/download" in calls[0], calls
    assert calls[-1].startswith("https://raw.githubusercontent.com"), calls
    assert cache.is_file(), "成功的索引要落盘，下次不用再联网"


def test_index_loader_reports_every_source(tmp_path, monkeypatch):
    import pytest as _pytest

    from petcore.ui.settings import pack_download

    def boom(url, timeout=15.0):
        raise ConnectionError("nope")

    monkeypatch.setattr(pack_download, "fetch_index_payload", boom)
    with _pytest.raises(ValueError) as caught:
        pack_download.fetch_first_index(
            (
                "https://github.com/a/b/releases/download/v1/i.json",
                "https://raw.githubusercontent.com/a/b/i.json",
            ),
            tmp_path / "index.json",
        )
    message = str(caught.value)
    assert "github.com" in message and "raw.githubusercontent.com" in message


_GOOD_INDEX = {
    "schema": 1,
    "packs": [
        {
            "id": "blue-fish",
            "name": "蓝胖鱼",
            "version": "1",
            "filename": "blue-fish.zip",
            "size": 10,
            "sha256": "a" * 64,
            "character": "blue_fish",
            "urls": ["https://github.com/x/y/releases/download/v1/blue-fish.zip"],
        }
    ],
}

_STALE_INDEX = {
    "schema": 1,
    "packs": [
        {
            **_GOOD_INDEX["packs"][0],
            "parts": [{"filename": "p.zip.001", "size": 10, "sha256": "b" * 64, "urls": []}],
        }
    ],
}


def test_stale_payload_from_one_source_falls_through(tmp_path, monkeypatch):
    """旧索引（分片 urls 为空）只该跳过那个源，不许终止链路、更不许写进缓存。"""
    import json

    from petcore.ui.settings import pack_download

    seen: list[str] = []

    def fake_fetch(url, timeout=15.0):
        seen.append(url)
        if "github.com" in url:  # 主源返回旧索引（现场就是这样）
            return json.dumps(_STALE_INDEX).encode()
        return json.dumps(_GOOD_INDEX).encode()

    monkeypatch.setattr(pack_download, "fetch_index_payload", fake_fetch)
    cache = tmp_path / "index.json"
    index = pack_download.fetch_first_index(
        ("https://github.com/a/b/releases/download/v1/i.json",
         "https://fastly.jsdelivr.net/gh/a/b@main/i.json"),
        cache,
    )
    assert [p.id for p in index.packs] == ["blue-fish"]
    # 缓存里必须是那份能用的索引，不是旧内容。
    assert '"parts"' not in cache.read_text(encoding="utf-8"), "坏内容不许落缓存"
    # 主源自带重试（同一地址两次），然后才轮到兜底源。
    assert seen[0] == seen[1] and "fastly.jsdelivr.net" in seen[-1], seen


def test_stale_only_caches_report_every_source(tmp_path, monkeypatch):
    import json

    import pytest as _pytest

    from petcore.ui.settings import pack_download

    monkeypatch.setattr(
        pack_download, "fetch_index_payload", lambda url, timeout=15.0: json.dumps(_STALE_INDEX).encode()
    )
    cache = tmp_path / "index.json"
    with _pytest.raises(ValueError) as caught:
        pack_download.fetch_first_index(("https://fastly.jsdelivr.net/gh/a/b@main/i.json",), cache)
    assert "fastly.jsdelivr.net" in str(caught.value)
    assert not cache.exists(), "全失败也不许留下缓存"


def test_broken_cache_is_deleted(tmp_path):
    from petcore.ui.settings import pack_download

    cache = tmp_path / "index.json"
    cache.write_text("{not json", encoding="utf-8")
    assert pack_download.load_cached_index(cache) is None
    assert not cache.exists(), "解析不了就删掉，别把用户卡到 TTL 结束"


def test_valid_cache_is_reused(tmp_path, monkeypatch):
    import json

    from petcore.ui.settings import pack_download

    cache = tmp_path / "index.json"
    cache.write_text(json.dumps(_GOOD_INDEX), encoding="utf-8")

    def boom(*_a, **_k):
        raise AssertionError("有效缓存不该再联网")

    monkeypatch.setattr(pack_download, "fetch_index_payload", boom)
    index = pack_download.fetch_first_index(
        ("https://example.invalid/i.json",), cache, use_cache=True
    )
    assert [p.id for p in index.packs] == ["blue-fish"]


def test_legacy_cache_with_empty_part_urls_is_dropped(tmp_path):
    """现场就是这个：旧索引写进了缓存，之后每次都报「分片 1 urls 为空」。"""
    import json

    from petcore.ui.settings import pack_download

    cache = tmp_path / "index.json"
    cache.write_text(json.dumps(_STALE_INDEX), encoding="utf-8")
    assert pack_download.load_cached_index(cache) is None
    assert not cache.exists()


def test_mirror_policy_row_writes_and_rolls_back(app, tmp_path: Path) -> None:
    page = _general_page(app, tmp_path)
    try:
        combo = page.mirror_combo
        assert [combo.itemData(i) for i in range(combo.count())] == ["auto", "mirror", "github"]
        assert combo.currentData() == "auto"
        combo.setCurrentIndex(combo.findData("mirror"))
        assert page.config().get("pack_mirror_policy") == "mirror", "切换即时生效"
        page.discard_changes()
        assert page.config().get("pack_mirror_policy") == "auto", "取消要回滚"
        assert combo.currentData() == "auto"
    finally:
        page.dispose()
        page.deleteLater()


def test_mirror_policy_row_is_searchable(app, tmp_path: Path) -> None:
    page = _general_page(app, tmp_path)
    try:
        haystack = " ".join(page_search_texts(page))
        for needle in ("下载镜像", "jsDelivr", "镜像"):
            assert needle in haystack, needle
        assert "下载镜像" in " ".join(page.keywords())
    finally:
        page.dispose()
        page.deleteLater()
