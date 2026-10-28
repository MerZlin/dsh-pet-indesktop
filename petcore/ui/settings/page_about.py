from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit

from ... import __version__
from ...update import UpdateResult, describe_state
from ..external_links import REPO_URL
from .base import (
    SettingsPage,
    build_embedded_page,
    embedded_page_specs,
    make_button,
    value_field,
)
from .page_about_thanks import ThanksPanel

#: 内嵌进「关于与诊断」的 Core 特性页，按这个顺序排（order 只决定注册序）。
ABOUT_HOST_PAGES = ("tool.links", "tool.network-info", "tool.memory-trim")


class AboutPage(SettingsPage):
    title = "关于与诊断"

    def __init__(self, host, parent=None):
        super().__init__(host, parent)
        self._root = Path(str(getattr(host, "root", Path.cwd())))
        self._log_path = self._root / "logs" / "petcore.log"
        self._disposed = False

        embedded = self._embedded_specs(host)
        with self.tab("about", "版本与诊断"):
            self._build_about_section()
        with self.tab("thanks", "致谢"):
            self._build_thanks_section()
        for spec, page in embedded:
            self.embed_page(page, str(spec.id), str(spec.title))

        self._refresh()

    def _build_thanks_section(self) -> None:
        self.thanks_panel = ThanksPanel(self.host, self)
        self._append_section_widget(self.thanks_panel)

    @staticmethod
    def _embedded_specs(host) -> list[tuple[object, SettingsPage]]:
        specs = {spec.owner: spec for spec in embedded_page_specs(host, "about")}
        ordered = [specs.pop(owner) for owner in ABOUT_HOST_PAGES if owner in specs]
        ordered.extend(sorted(specs.values(), key=lambda spec: int(spec.order or 0)))
        pages: list[tuple[object, SettingsPage]] = []
        for spec in ordered:
            page = build_embedded_page(spec, host)
            if page is not None:
                pages.append((spec, page))
        return pages

    def _build_about_section(self) -> None:
        info_card = self.section("版本信息")
        info_card.add_row("petcore", QLabel(f"v{__version__}", self))
        info_card.add_row("数据目录", value_field(str(self._root), self))
        info_card.add_row("日志文件", value_field(str(self._log_path), self))
        info_card.add_row("项目主页", value_field(REPO_URL, self))
        self.characters_label = QLabel("", self)
        info_card.add_row("角色数", self.characters_label)
        self.mods_label = QLabel("", self)
        info_card.add_row("扩展数", self.mods_label)
        self.pets_label = QLabel("", self)
        info_card.add_row("桌宠数", self.pets_label)

        update_card = self.section("Core 更新")
        self.update_status_label = QLabel("尚未检查", self)
        self.update_status_label.setWordWrap(True)
        update_card.add_row("状态", self.update_status_label)
        check_update_button = make_button("检查更新", self)
        check_update_button.clicked.connect(self._check_update)
        self.download_update_button = make_button("下载更新", self)
        self.download_update_button.setEnabled(False)
        self.download_update_button.clicked.connect(self._download_update)
        self.install_update_button = make_button("安装更新", self)
        self.install_update_button.setEnabled(False)
        self.install_update_button.clicked.connect(self._install_update)
        self.cancel_update_button = make_button("取消下载", self)
        self.cancel_update_button.setEnabled(False)
        self.cancel_update_button.clicked.connect(self._cancel_update)
        update_card.add_buttons(
            [
                check_update_button,
                self.download_update_button,
                self.install_update_button,
                self.cancel_update_button,
            ]
        )

        open_button = make_button("打开数据目录", self)
        open_button.clicked.connect(self._open_data_dir)
        reload_button = make_button("重新加载扩展", self)
        reload_button.clicked.connect(self._reload)
        copy_button = make_button("复制诊断信息", self)
        copy_button.clicked.connect(self._copy_report)
        repo_button = make_button("打开项目主页", self)
        repo_button.clicked.connect(self._open_repo)
        info_card.add_buttons([open_button, reload_button, copy_button, repo_button])

        snapshot_card = self.section("诊断快照")
        self.snapshot_view = QPlainTextEdit(self)
        self.snapshot_view.setReadOnly(True)
        self.snapshot_view.setAccessibleName("诊断快照")
        self.snapshot_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.snapshot_view.setMinimumHeight(220)
        snapshot_card.add_widget(self.snapshot_view)
        self.set_update_status(getattr(self.host, "update_status", lambda: None)())

    def _mods(self) -> tuple:
        result = getattr(self.host, "load_result", None)
        return tuple(getattr(result, "mods", ()) or ())

    def _report(self) -> dict:
        mods = []
        for mod in self._mods():
            manifest = getattr(mod, "manifest", None)
            mods.append(
                {
                    "id": str(getattr(mod, "id", "") or getattr(manifest, "id", "")),
                    "name": str(getattr(manifest, "name", "") or ""),
                    "version": str(getattr(manifest, "version", "") or ""),
                    "enabled": bool(getattr(mod, "enabled", False)),
                }
            )
        characters = [
            {
                "id": str(getattr(spec, "id", "")),
                "name": str(getattr(spec, "name", "")),
                "owner": str(getattr(spec, "owner", "")),
            }
            for spec in self.host.characters()
        ]
        pets = []
        manager = getattr(self.host, "pets", None)
        if manager is not None:
            pets = [
                {
                    "slot": window.slot,
                    "primary": window.is_primary,
                    "character": window.character_id,
                    "scale": float(window.state.scale),
                }
                for window in manager.each()
            ]
        return {
            "version": __version__,
            "data_root": str(self._root),
            "log_path": str(self._log_path),
            "config": self.host.config.snapshot(),
            "mod_dirs": [str(Path(item)) for item in (getattr(self.host, "mod_dirs", ()) or ())],
            "mods": mods,
            "characters": characters,
            "pets": pets,
            "speech": self._speech_report(),
            "community": self._community_report(),
        }

    def _speech_report(self) -> dict:
        service = getattr(self.host, "speech", None)
        if service is None:
            return {"available": False}
        try:
            providers = [
                {
                    "id": info.id,
                    "available": bool(info.available),
                    "reason": str(info.reason or ""),
                }
                for info in service.providers()
            ]
            return {
                "available": True,
                "backend": service.backend(),
                "fallback": service.fallback(),
                "providers": providers,
                "cache_dir": str(service.cache_dir()),
                "cached_files": len(service.cache.entries()),
            }
        except Exception as exc:  # noqa: BLE001 - 诊断不能把设置页打挂
            return {"available": False, "error": type(exc).__name__}

    def _community_report(self) -> dict:
        service = getattr(self.host, "community", None)
        if service is None:
            return {"available": False}
        try:
            fans = service.fans()
            credits = service.credits()
            return {
                "available": True,
                "fans": len(fans.names),
                "fans_total": int(fans.total),
                "fans_fetched_at": fans.fetched_at,
                "credits_fetched_at": credits.fetched_at,
                "contributors": len(credits.contributors),
                "authors": len(credits.authors),
                "bilibili_cookie": bool(service.bilibili_cookie_status()),
            }
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "error": type(exc).__name__}

    def _refresh(self) -> None:
        report = self._report()
        self.characters_label.setText(str(len(report["characters"])))
        self.mods_label.setText(str(len(report["mods"])))
        self.pets_label.setText(str(len(report["pets"])))
        self.snapshot_view.setPlainText(json.dumps(report, ensure_ascii=False, indent=2))
        self.set_update_status(getattr(self.host, "update_status", lambda: None)())

    def set_update_status(self, result: UpdateResult | None) -> None:
        if not hasattr(self, "update_status_label") or result is None:
            return
        message = str(result.message or describe_state(result.state))
        if result.version:
            message = f"{message}（{result.version}）"
        if result.path:
            message = f"{message}\n文件：{result.path}"
        self.update_status_label.setText(message)
        self.download_update_button.setEnabled(result.state == "available")
        # 源码运行/非 Windows 不能原地更新：按钮直接不给点亮，别让用户点了才知道。
        self.install_update_button.setEnabled(
            result.state == "downloaded" and result.path is not None and self._can_auto_install()
        )
        self.cancel_update_button.setEnabled(result.state in {"downloading", "checking"})

    def _can_auto_install(self) -> bool:
        checker = getattr(self.host, "_install_available", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:  # noqa: BLE001 - 探针失败按不可安装处理
                return False
        return False

    def _check_update(self) -> None:
        callback = getattr(self.host, "check_for_updates", None)
        if callable(callback):
            callback()

    def _download_update(self) -> None:
        callback = getattr(self.host, "download_update", None)
        if callable(callback):
            callback()

    def _install_update(self) -> None:
        callback = getattr(self.host, "install_update", None)
        if callable(callback):
            callback()

    def _cancel_update(self) -> None:
        callback = getattr(self.host, "cancel_update", None)
        if callable(callback):
            callback()

    def _copy_report(self) -> None:
        QApplication.clipboard().setText(self.snapshot_view.toPlainText())

    def _open_repo(self) -> None:
        QDesktopServices.openUrl(QUrl(REPO_URL))

    def _open_data_dir(self) -> None:
        opener = getattr(self.host, "open_data_dir", None)
        if callable(opener):
            opener()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh()
        panel = getattr(self, "thanks_panel", None)
        if panel is not None:
            panel.refresh_if_stale()

    def _reload(self) -> None:
        reload_mods = getattr(self.host, "reload_mods", None)
        if callable(reload_mods):
            reload_mods()
        self._refresh()
        panel = getattr(self, "thanks_panel", None)
        if panel is not None:
            panel.reload()

    def dirty(self) -> bool:
        return self._embedded_dirty()

    def confirm_save(self) -> None:
        self._embedded_confirm_save()

    def discard_changes(self) -> None:
        self._embedded_discard()

    def dispose(self) -> None:
        self._disposed = True
        panel = getattr(self, "thanks_panel", None)
        if panel is not None:
            panel.dispose()
        super().dispose()

    def keywords(self) -> list[str]:
        return [
            "关于",
            "诊断",
            "版本",
            "数据目录",
            "日志",
            "配置",
            "json",
            "链接",
            "网络信息",
            "回收内存",
            "about",
            "version",
            "diagnostics",
            "log",
            "config",
            "links",
            "network",
            "memory",
            "更新",
            "检查更新",
            "下载更新",
            "安装更新",
            "取消下载",
            "update",
            "致谢",
            "贡献者",
            "特别鸣谢",
            "粉丝",
            "签名",
            "作者",
            "鸣谢",
            "contributors",
            "thanks",
            "fans",
            "signature",
            *self._embedded_keywords(),
        ]
