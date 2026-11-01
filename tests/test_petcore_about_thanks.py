"""致谢面板：折叠、粉丝分页/搜索、刷新与签名（宿主是侧栏「致谢」页）。"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from petcore.community import model
from petcore.community.service import RefreshReport, stamp_now
from petcore.config import Config
from petcore.ui.settings import page_about_thanks as thanks_module
from petcore.ui.settings.base import reveal_text
from petcore.ui.settings.page_about import AboutPage
from petcore.ui.settings.page_about_thanks import ThanksPanel


class FakeCommunity:
    def __init__(self, *, names, tmp_path: Path) -> None:
        self.credits_snapshot = model.parse_credits(
            {
                "schema": 1,
                "repo": "MerZlin/dsh-pet-indesktop",
                "fetched_at": "2026-10-27T12:00:00+08:00",
                "author_accounts": [
                    {"platform": "BiliBili", "handle": "@張墨林"},
                    {"platform": "抖音", "handle": "@張墨林"},
                ],
                "special_thanks": [
                    {"kind": "project", "name": "PC2005-cloud/dsh-pet", "note": "素材来源"}
                ],
                "contributors": [
                    {"login": "klxxya", "contributions": 742},
                    {"login": "MerZlin", "contributions": 314},
                ],
                "authors": [
                    {"login": "klxxya", "issues": 2, "prs": 96},
                    {"login": "Xaisx-sys", "issues": 1, "prs": 0},
                ],
            }
        )
        self.fans_snapshot = model.Fans(
            vmid=293026389,
            fetched_at="2026-10-27T12:00:00+08:00",
            names=tuple(names),
            # B 站口径人数可以比去重后的名字多（「账号已注销」这类占位）
            total=len(names) + 2,
        )
        self.cookie = False
        self.meta: dict[str, str] = {}
        self.signature_calls: list[tuple[bytes, str]] = []
        self.name_calls: list[str] = []
        self.path = Path(tmp_path) / "community" / "signature.png"

    def credits(self):
        return self.credits_snapshot

    def fans(self):
        return self.fans_snapshot

    def bilibili_cookie_status(self) -> bool:
        return self.cookie

    def set_bilibili_cookie(self, value: str) -> None:
        self.cookie = bool(str(value).strip())

    def signature_path(self) -> Path:
        return self.path

    def signature_meta(self) -> dict[str, str]:
        return dict(self.meta)

    def save_signature(self, png: bytes, name: str = "") -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(png)
        self.meta = {"name": name, "updated_at": stamp_now()}
        self.signature_calls.append((png, name))
        return self.path

    def save_signature_name(self, name: str) -> None:
        self.meta = {"name": name, "updated_at": stamp_now()}
        self.name_calls.append(name)

    def clear_signature(self) -> bool:
        self.meta = {}
        try:
            self.path.unlink()
        except FileNotFoundError:
            return False
        return True


class FakeHost:
    def __init__(self, root: Path, community=None) -> None:
        self.root = Path(root)
        self.config = Config(self.root)
        self.community = community
        self.load_result = None
        self.mod_dirs = ()
        self.pets = SimpleNamespace(each=lambda: [])
        self.audio_available = True

    def characters(self):
        return []

    def open_data_dir(self):
        return None

    def reload_mods(self):
        return None


class FakeJob(QObject):
    progress = Signal(str)
    done = Signal(object)
    failed = Signal(str)
    instances: list["FakeJob"] = []

    def __init__(self, service, *, force: bool = False) -> None:
        super().__init__()
        self.service = service
        self.force = force
        self.started = False
        self.cancelled = False
        FakeJob.instances.append(self)

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True

    def is_running(self) -> bool:
        return self.started and not self.cancelled


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def names():
    return [f"粉丝{index:03d}" for index in range(200)]


def _panel(tmp_path: Path, names) -> ThanksPanel:
    community = FakeCommunity(names=names, tmp_path=tmp_path)
    panel = ThanksPanel(FakeHost(tmp_path, community))
    panel.resize(820, 700)
    panel.show()
    QTest.qWait(5)
    return panel


def test_sections_exist_and_default_expansion(app, tmp_path: Path, names) -> None:
    panel = _panel(tmp_path, names)
    try:
        assert panel.authors_section.is_expanded() is True
        assert panel.signature_section.is_expanded() is True
        assert panel.contributors_section.is_expanded() is False
        assert panel.special_section.is_expanded() is False
        assert panel.thanks_section.is_expanded() is False
        assert panel.fans_section.is_expanded() is False
        assert "共 200 位粉丝" in panel.fans_count_label.text()
        assert "作者账号" in panel.authors_section.header.text()
    finally:
        panel.dispose()
        panel.deleteLater()


def test_fans_paginate_and_search_without_network(app, tmp_path: Path, names) -> None:
    panel = _panel(tmp_path, names)
    try:
        panel.fans_section.set_expanded(True)
        assert panel.fans_grid.count() == 60
        assert panel.fan_page_label.text().startswith("第 1 / 4 页")

        panel._page_by(1)
        assert panel.fan_page_label.text().startswith("第 2 / 4 页")
        assert panel.fans_grid.count() == 60

        panel.fan_search.setText("粉丝199")
        panel._apply_fan_filter()
        assert panel.fans_grid.count() == 1
        assert panel.fan_page_label.text().startswith("第 1 / 1 页")
        assert "筛选后 1 位" in panel.fans_count_label.text()

        panel.fan_search.setText("")
        panel._apply_fan_filter()
        assert panel.fans_grid.count() == 60
    finally:
        panel.dispose()
        panel.deleteLater()


def test_empty_fans_explain_how_to_load(app, tmp_path: Path) -> None:
    panel = _panel(tmp_path, [])
    try:
        panel.fans_section.set_expanded(True)
        assert "还没有粉丝名单" in panel.fans_empty_label.text()
        assert panel.fans_empty_label.isVisible() is True
        assert panel.fan_page_label.text().startswith("第 1 / 1 页")
    finally:
        panel.dispose()
        panel.deleteLater()


def test_settings_search_expands_the_collapsed_fans_section(app, tmp_path: Path, names) -> None:
    panel = _panel(tmp_path, names)
    try:
        assert panel.fans_section.is_expanded() is False
        assert reveal_text(panel, "粉丝") is True
        assert panel.fans_section.is_expanded() is True
    finally:
        panel.dispose()
        panel.deleteLater()


def test_cookie_save_triggers_a_forced_refresh(app, tmp_path: Path, names, monkeypatch) -> None:
    FakeJob.instances.clear()
    monkeypatch.setattr(thanks_module, "CommunityRefreshJob", FakeJob)
    panel = _panel(tmp_path, names)
    try:
        panel.cookie_edit.setText("SESSDATA=abc")
        panel._save_cookie()

        assert panel.community.cookie is True
        assert panel.cookie_edit.text() == ""
        assert panel.cookie_status.text() == "已配置"
        assert FakeJob.instances and FakeJob.instances[-1].force is True
        assert FakeJob.instances[-1].started is True
    finally:
        panel.dispose()
        panel.deleteLater()


def test_refresh_is_coalesced_and_disposes_cleanly(app, tmp_path: Path, names, monkeypatch) -> None:
    FakeJob.instances.clear()
    monkeypatch.setattr(thanks_module, "CommunityRefreshJob", FakeJob)
    panel = _panel(tmp_path, names)
    try:
        panel.refresh_if_stale()
        assert len(FakeJob.instances) == 1
        assert FakeJob.instances[0].force is False

        panel.refresh_if_stale()
        assert len(FakeJob.instances) == 1, "冷却期内不重复联网"

        report = RefreshReport(credits_ok=True, fans_ok=True, fan_total=200)
        panel._on_job_done(report)
        assert panel.refresh_button.isEnabled() is True
        assert "已更新" in panel.status_label.text()

        panel.dispose()
        assert FakeJob.instances[0].cancelled is True
    finally:
        panel.deleteLater()


def test_refresh_failure_keeps_the_list_and_explains(app, tmp_path: Path, names) -> None:
    panel = _panel(tmp_path, names)
    try:
        report = RefreshReport(
            credits_ok=False,
            fans_ok=False,
            errors={"credits": "rate_limit", "fans": "timeout"},
        )
        panel._on_job_done(report)

        assert "限流" in panel.status_label.text() or "频繁" in panel.status_label.text()
        assert "保留原名单" in panel.status_label.text()
        assert panel.fans_grid.count() == 0, "折叠时不该渲染粉丝格子"
        panel.fans_section.set_expanded(True)
        assert panel.fans_grid.count() == 60
    finally:
        panel.dispose()
        panel.deleteLater()


def test_draw_and_save_signature_overwrites(app, tmp_path: Path, names) -> None:
    panel = _panel(tmp_path, names)
    try:
        panel.signature_panel.name_edit.setText("测试用户")
        pad = panel.signature_panel.pad
        center = pad.rect().center()
        QTest.mousePress(pad, Qt.MouseButton.LeftButton, pos=center)
        QTest.mouseMove(pad, center + QPoint(20, 10))
        QTest.mouseRelease(pad, Qt.MouseButton.LeftButton, pos=center + QPoint(40, 20))
        assert pad.has_content() is True

        panel.signature_panel.save_button.click()
        assert len(panel.community.signature_calls) == 1
        png, name = panel.community.signature_calls[0]
        assert png.startswith(b"\x89PNG")
        assert name == "测试用户"
        assert panel.signature_panel.preview.isVisible() is True

        panel.signature_panel.save_button.click()
        assert len(panel.community.signature_calls) == 2, "新签名要覆盖同一份文件"
        assert panel.community.signature_path().exists()
    finally:
        panel.dispose()
        panel.deleteLater()


def test_name_only_signature_is_saved_as_meta(app, tmp_path: Path, names) -> None:
    panel = _panel(tmp_path, names)
    try:
        panel.signature_panel.name_edit.setText("只用昵称")
        panel.signature_panel.save_button.click()

        assert panel.community.name_calls == ["只用昵称"]
        assert panel.community.signature_calls == []
        assert "已保存昵称" in panel.signature_panel.saved_label.text()
    finally:
        panel.dispose()
        panel.deleteLater()


def test_clear_removes_the_saved_signature(app, tmp_path: Path, names) -> None:
    panel = _panel(tmp_path, names)
    try:
        panel.community.save_signature(b"\x89PNG\r\n\x1a\n" + b"x", "墨林")
        panel.signature_panel._load_saved()
        assert panel.signature_panel.remove_saved_button.isEnabled() is True

        panel.signature_panel._remove_saved()
        assert panel.community.signature_path().exists() is False
        assert panel.signature_panel.remove_saved_button.isEnabled() is False
    finally:
        panel.dispose()
        panel.deleteLater()


def test_about_page_keeps_the_community_report(app, tmp_path: Path) -> None:
    # 致谢面板已是侧栏独立页（``ThanksPage``）；关于页只保留诊断里的 community 段。
    page = AboutPage(FakeHost(tmp_path))
    try:
        assert page.tab_container is not None
        assert page.tab_container.labels() == ("版本与诊断",)
        assert not hasattr(page, "thanks_panel")
        assert "fans" in page._report()["community"] or page._report()["community"] == {"available": False}
        community = page._report()["community"]
        if community.get("available"):
            cached = page.host.community.fans()
            assert community["fans"] == len(cached.names)
            assert community["fans_total"] == cached.total, "诊断要给出 B 站口径人数，不能回退成名字数"
        assert "speech" in page._report()
    finally:
        page.dispose()
        page.deleteLater()
