"""致谢（侧栏「致谢」页的宿主面板）：作者、贡献者、特别鸣谢、issue/PR 协作者、粉丝与签名。

数据来自 ``host.community``（无 service 时只用随包快照）；刷新在后台守护线程，
只写缓存文件，失败保留旧名单。粉丝区只渲染当前页 60 条，搜索/翻页不联网。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...community import model
from ...community import store as community_store
from .base import make_button
from .credits_job import CommunityRefreshJob
from .folds import CollapsibleSection
from .signature_pad import SignaturePanel

logger = logging.getLogger("petcore.ui.settings.thanks")

FAN_PAGE_SIZE = 60
FAN_COLUMNS = 4
AUTO_REFRESH_COOLDOWN_S = 600.0
SEARCH_DEBOUNCE_MS = 200

REASON_TEXT = {
    "auth": "B 站登录凭据未配置或已失效",
    "timeout": "请求超时（名单可能没读完）",
    "network": "网络不可达",
    "rate_limit": "请求过于频繁，稍后再试",
    "protocol": "服务端返回了无法识别的响应",
    "unavailable": "本机文件不可写",
    "config": "未配置",
}


class ThanksPanel(QWidget):
    def __init__(self, host, parent=None) -> None:
        super().__init__(parent)
        self.host = host
        self.root = Path(str(getattr(host, "root", Path.cwd())))
        self.community = getattr(host, "community", None)
        self._disposed = False
        self._job: CommunityRefreshJob | None = None
        self._last_auto_refresh = 0.0
        self._credits: model.Credits | None = None
        self._fans: model.Fans | None = None
        self._fans_all: list[str] = []
        self._fans_filtered: list[str] = []
        self._fan_page = 1
        self._build()
        self.reload()

    # ------------------------------------------------------------ 构造
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        top = QWidget(self)
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)
        self.status_label = QLabel("", top)
        self.status_label.setObjectName("settingHint")
        self.status_label.setWordWrap(True)
        top_layout.addWidget(self.status_label, 1)
        self.refresh_button = make_button("刷新名单", top)
        self.refresh_button.clicked.connect(self.request_refresh)
        top_layout.addWidget(self.refresh_button)
        layout.addWidget(top)

        self.authors_section = CollapsibleSection("作者账号", expanded=True, parent=self)
        layout.addWidget(self.authors_section)

        self.contributors_section = CollapsibleSection(
            "贡献者 Contributor", expanded=False, parent=self
        )
        self.contributors_count = QLabel("", self.contributors_section.card())
        self.contributors_count.setObjectName("settingHint")
        self.contributors_section.card().add_widget(self.contributors_count)
        self.contributors_host = QWidget(self.contributors_section.card())
        self.contributors_layout = QVBoxLayout(self.contributors_host)
        self.contributors_layout.setContentsMargins(0, 0, 0, 0)
        self.contributors_layout.setSpacing(6)
        self.contributors_section.card().add_widget(self.contributors_host)
        layout.addWidget(self.contributors_section)

        self.special_section = CollapsibleSection("特别鸣谢", expanded=False, parent=self)
        layout.addWidget(self.special_section)
        self.special_host = QWidget(self.special_section.card())
        self.special_layout = QVBoxLayout(self.special_host)
        self.special_layout.setContentsMargins(0, 0, 0, 0)
        self.special_layout.setSpacing(6)
        self.special_section.card().add_widget(self.special_host)

        self.thanks_section = CollapsibleSection(
            "致谢（Issue / PR 协作者）", expanded=False, parent=self
        )
        self.thanks_count = QLabel("", self.thanks_section.card())
        self.thanks_count.setObjectName("settingHint")
        self.thanks_section.card().add_widget(self.thanks_count)
        self.thanks_list = QListWidget(self.thanks_section.card())
        self.thanks_list.setObjectName("creditList")
        self.thanks_list.setMinimumHeight(220)
        self.thanks_list.setAccessibleName("Issue 与 PR 协作者")
        self.thanks_list.itemDoubleClicked.connect(self._open_list_login)
        self.thanks_section.card().add_widget(self.thanks_list)
        self.thanks_section.toggled.connect(self._fill_thanks_list)
        layout.addWidget(self.thanks_section)

        self.fans_section = CollapsibleSection(
            "感谢（B 站粉丝）", expanded=False, parent=self
        )
        self._build_fans()
        layout.addWidget(self.fans_section)

        self.signature_section = CollapsibleSection("签名与致谢", expanded=True, parent=self)
        self.signature_panel = SignaturePanel(self.host, self.signature_section.card())
        self.signature_section.card().add_widget(self.signature_panel)
        layout.addWidget(self.signature_section)

        layout.addStretch(1)
        self._build_author_rows()
        self._build_special_rows()

    def _build_author_rows(self) -> None:
        card = self.authors_section.card()
        credits = self._load_credits()
        for account in credits.author_accounts:
            row = self._text_row(
                account.platform,
                account.handle,
                copy_text=account.handle,
                url=account.url,
            )
            card.add_widget(row)

    def _build_special_rows(self) -> None:
        credits = self._load_credits()
        for item in credits.special_thanks:
            row = self._text_row(item.name, item.note, url=item.url)
            self.special_layout.addWidget(row)

    def _build_fans(self) -> None:
        card = self.fans_section.card()
        self.fans_count_label = QLabel("", card)
        self.fans_count_label.setObjectName("settingLabel")
        self.fans_count_label.setWordWrap(True)
        card.add_widget(self.fans_count_label)

        self.fans_stamp_label = QLabel("", card)
        self.fans_stamp_label.setObjectName("settingHint")
        self.fans_stamp_label.setWordWrap(True)
        card.add_widget(self.fans_stamp_label)

        search_row = QWidget(card)
        search_layout = QHBoxLayout(search_row)
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.setSpacing(8)
        self.fan_search = QLineEdit(search_row)
        self.fan_search.setPlaceholderText("搜索粉丝名称（输入你自己的用户名）")
        self.fan_search.setClearButtonEnabled(True)
        self.fan_search.setAccessibleName("搜索粉丝名称")
        self.fan_search.textChanged.connect(self._on_search_changed)
        search_layout.addWidget(self.fan_search, 1)
        self.fan_search_timer = QTimer(self)
        self.fan_search_timer.setSingleShot(True)
        self.fan_search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self.fan_search_timer.timeout.connect(self._apply_fan_filter)
        card.add_widget(search_row)

        self.fans_grid_host = QWidget(card)
        self.fans_grid = QGridLayout(self.fans_grid_host)
        self.fans_grid.setContentsMargins(0, 4, 0, 4)
        self.fans_grid.setHorizontalSpacing(12)
        self.fans_grid.setVerticalSpacing(4)
        card.add_widget(self.fans_grid_host)

        self.fans_empty_label = QLabel("没有匹配的粉丝", card)
        self.fans_empty_label.setObjectName("settingHint")
        self.fans_empty_label.hide()
        card.add_widget(self.fans_empty_label)

        pager = QWidget(card)
        pager_layout = QHBoxLayout(pager)
        pager_layout.setContentsMargins(0, 0, 0, 0)
        pager_layout.setSpacing(8)
        self.fan_prev = make_button("上一页", pager)
        self.fan_prev.clicked.connect(lambda: self._page_by(-1))
        self.fan_next = make_button("下一页", pager)
        self.fan_next.clicked.connect(lambda: self._page_by(1))
        self.fan_page_label = QLabel("", pager)
        self.fan_page_label.setObjectName("settingHint")
        pager_layout.addWidget(self.fan_prev)
        pager_layout.addWidget(self.fan_next)
        pager_layout.addWidget(self.fan_page_label, 1)
        self.fan_refresh = make_button("刷新名单", pager)
        self.fan_refresh.clicked.connect(self.request_refresh)
        pager_layout.addWidget(self.fan_refresh)
        card.add_widget(pager)

        cookie_row = QWidget(card)
        cookie_layout = QHBoxLayout(cookie_row)
        cookie_layout.setContentsMargins(0, 0, 0, 0)
        cookie_layout.setSpacing(8)
        self.cookie_edit = QLineEdit(cookie_row)
        self.cookie_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.cookie_edit.setPlaceholderText("粘贴 SESSDATA（只存系统钥匙串）")
        self.cookie_edit.setAccessibleName("B 站登录凭据 SESSDATA")
        self.cookie_status = QLabel("", cookie_row)
        self.cookie_status.setObjectName("settingHint")
        save_cookie = make_button("保存凭据", cookie_row)
        save_cookie.clicked.connect(self._save_cookie)
        clear_cookie = make_button("清除", cookie_row)
        clear_cookie.clicked.connect(self._clear_cookie)
        cookie_layout.addWidget(self.cookie_edit, 1)
        cookie_layout.addWidget(self.cookie_status)
        cookie_layout.addWidget(save_cookie)
        cookie_layout.addWidget(clear_cookie)
        card.add_row(
            "B 站登录凭据",
            cookie_row,
            "完整名单需要登录态；凭据只进系统钥匙串，界面不显示已保存的值。",
            expand=True,
        )

        self.fans_section.toggled.connect(self._on_fans_toggled)

    # ------------------------------------------------------------ 数据装载
    def _service(self):
        return self.community

    def _load_credits(self) -> model.Credits:
        if self.community is not None:
            try:
                return self.community.credits()
            except Exception:  # noqa: BLE001 - 服务异常退回随包
                logger.debug("community credits failed", exc_info=True)
        return community_store.load_bundled_credits()

    def _load_fans(self) -> model.Fans:
        if self.community is not None:
            try:
                return self.community.fans()
            except Exception:  # noqa: BLE001
                logger.debug("community fans failed", exc_info=True)
        return community_store.load_bundled_fans()

    def reload(self) -> None:
        if self._disposed:
            return
        self._credits = self._load_credits()
        self._fans = self._load_fans()
        self._fill_contributors()
        self._fill_thanks_list(self.thanks_section.is_expanded())
        self._fans_all = list(self._fans.names)
        self._fans_filtered = model.filter_names(self._fans_all, self.fan_search.text())
        self._fan_page = model.clamp_page(self._fan_page, self._fans_filtered, FAN_PAGE_SIZE)
        self.fans_count_label.setText(self._fans_count_text())
        stamp = str(self._fans.fetched_at or "")
        if stamp:
            self.fans_stamp_label.setText(f"名单更新于 {stamp[:16].replace('T', ' ')}")
        elif not self._fans_all:
            self.fans_stamp_label.setText(
                "还没有粉丝名单：配置 B 站凭据后点「刷新名单」，或使用随包快照。"
            )
        else:
            self.fans_stamp_label.setText("名单来自随包快照")
        self._refresh_cookie_status()
        if self.fans_section.is_expanded():
            self._render_fans_page()
        self.set_status(self.summary())

    def summary(self) -> str:
        credits = self._credits or model.parse_credits({})
        fans = self._fans or model.parse_fans({})
        stamp = str(fans.fetched_at or "")
        suffix = f" · 名单 {stamp[:10]}" if stamp else ""
        fans_text = f"粉丝 {len(fans.names)} 位"
        if int(fans.total) > len(fans.names):
            fans_text += f"（B 站 {int(fans.total)} 位，含同名占位）"
        return (
            f"贡献者 {len(credits.contributors)} 位 · "
            f"issue/PR 协作者 {len(credits.authors)} 位 · "
            f"{fans_text}{suffix}"
        )

    def _fill_contributors(self) -> None:
        credits = self._credits or model.parse_credits({})
        self.contributors_count.setText(
            f"按提交数排序，共 {len(credits.contributors)} 位；点击「打开」查看 GitHub 主页。"
        )
        self._clear_layout(self.contributors_layout)
        for item in credits.contributors:
            hint = f"提交 {item.contributions} 次"
            if item.login.casefold() == "merzlin":
                hint = f"维护者 · {hint}"
            row = self._text_row(
                item.login,
                hint,
                url=f"https://github.com/{item.login}",
                copy_text=item.login,
            )
            self.contributors_layout.addWidget(row)

    def _fill_thanks_list(self, expanded: bool | None = None) -> None:
        if self._disposed:
            return
        credits = self._credits or model.parse_credits({})
        self.thanks_count.setText(
            f"共 {len(credits.authors)} 位提出过 issue 或 PR 的协作者；双击打开 GitHub 主页。"
        )
        if expanded is False:
            # 折叠时只更新计数，不建 124 个列表项；展开时再填。
            return
        self.thanks_list.clear()
        for item in credits.authors:
            label = f"{item.login} · issue {item.issues} / PR {item.prs}"
            row = QListWidgetItem(label, self.thanks_list)
            row.setData(Qt.ItemDataRole.UserRole, item.login)
            row.setToolTip(f"https://github.com/{item.login}")

    def _open_list_login(self, item: QListWidgetItem) -> None:
        login = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if login:
            self._open_url(f"https://github.com/{login}")

    # ------------------------------------------------------------ 行
    def _text_row(
        self,
        title: str,
        hint: str,
        *,
        copy_text: str = "",
        url: str = "",
    ) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(10)
        text = QVBoxLayout()
        text.setSpacing(2)
        title_label = QLabel(title, row)
        title_label.setObjectName("settingLabel")
        title_label.setWordWrap(True)
        text.addWidget(title_label)
        if hint:
            hint_label = QLabel(hint, row)
            hint_label.setObjectName("settingHint")
            hint_label.setWordWrap(True)
            text.addWidget(hint_label)
        layout.addLayout(text, 1)
        if copy_text:
            copy = make_button("复制", row)
            copy.setAccessibleName(f"复制 {copy_text}")
            copy.clicked.connect(lambda _checked=False, value=copy_text: self._copy(value))
            layout.addWidget(copy)
        if url:
            open_button = make_button("打开", row)
            open_button.setAccessibleName(f"打开 {url}")
            open_button.clicked.connect(lambda _checked=False, target=url: self._open_url(target))
            layout.addWidget(open_button)
        return row

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    # ------------------------------------------------------------ 粉丝
    def _on_fans_toggled(self, expanded: bool) -> None:
        if expanded:
            self._render_fans_page()

    def _on_search_changed(self, _text: str) -> None:
        self.fan_search_timer.start()

    def _fans_count_text(self) -> str:
        """收录数 + （筛选后）+ B 站口径总数（两者只差同名占位时给出说明）。"""
        total = int(self._fans.total) if self._fans is not None else 0
        text = f"共 {len(self._fans_all)} 位粉丝"
        if len(self._fans_filtered) != len(self._fans_all):
            text += f"（筛选后 {len(self._fans_filtered)} 位）"
        if total > len(self._fans_all):
            text += f" · B 站显示 {total} 位（含同名占位）"
        return text

    def _apply_fan_filter(self) -> None:
        self._fans_filtered = model.filter_names(self._fans_all, self.fan_search.text())
        self._fan_page = 1
        self.fans_count_label.setText(self._fans_count_text())
        if self.fans_section.is_expanded():
            self._render_fans_page()

    def _page_by(self, delta: int) -> None:
        self._fan_page = model.clamp_page(
            self._fan_page + int(delta), self._fans_filtered, FAN_PAGE_SIZE
        )
        self._render_fans_page()

    def _render_fans_page(self) -> None:
        if self._disposed:
            return
        self._clear_layout(self.fans_grid)
        total = len(self._fans_filtered)
        page_count = model.page_count(self._fans_filtered, FAN_PAGE_SIZE)
        self._fan_page = model.clamp_page(self._fan_page, self._fans_filtered, FAN_PAGE_SIZE)
        names = model.page_slice(self._fans_filtered, self._fan_page, FAN_PAGE_SIZE)
        for index, name in enumerate(names):
            label = QLabel(name, self.fans_grid_host)
            label.setObjectName("fanName")
            label.setToolTip(name)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.fans_grid.addWidget(label, index // FAN_COLUMNS, index % FAN_COLUMNS)
        self.fans_empty_label.setText(
            "没有匹配的粉丝"
            if self._fans_all
            else "还没有粉丝名单：配置 B 站凭据后点「刷新名单」。"
        )
        self.fans_empty_label.setVisible(total == 0)
        self.fans_grid_host.setVisible(total > 0)
        self.fan_page_label.setText(f"第 {self._fan_page} / {page_count} 页 · 共 {total} 位")
        self.fan_prev.setEnabled(self._fan_page > 1)
        self.fan_next.setEnabled(self._fan_page < page_count)

    def _refresh_cookie_status(self) -> None:
        if self.community is None:
            self.cookie_status.setText("未接入")
            return
        try:
            configured = bool(self.community.bilibili_cookie_status())
        except Exception:  # noqa: BLE001
            configured = False
        self.cookie_status.setText("已配置" if configured else "未配置")

    def _save_cookie(self) -> None:
        if self.community is None:
            self._set_status("凭据存储未接入")
            return
        value = self.cookie_edit.text().strip()
        if not value:
            self._set_status("请先粘贴 SESSDATA")
            return
        try:
            self.community.set_bilibili_cookie(value)
        except ValueError as exc:
            self._set_status(str(exc))
            return
        except Exception:  # noqa: BLE001 - 钥匙串不可用要可见
            self._set_status("凭据保存失败（系统钥匙串不可用）")
            return
        self.cookie_edit.clear()
        self._refresh_cookie_status()
        self._set_status("凭据已保存，正在刷新名单…")
        self.request_refresh()

    def _clear_cookie(self) -> None:
        if self.community is None:
            return
        try:
            self.community.set_bilibili_cookie("")
        except Exception:  # noqa: BLE001
            self._set_status("凭据清除失败")
            return
        self.cookie_edit.clear()
        self._refresh_cookie_status()
        self._set_status("凭据已清除，继续使用现有名单。")

    # ------------------------------------------------------------ 刷新
    def refresh_if_stale(self) -> None:
        if self._disposed or self.community is None:
            return
        if self._job is not None and self._job.is_running():
            return
        now = time.monotonic()
        if now - self._last_auto_refresh < AUTO_REFRESH_COOLDOWN_S:
            return
        self._last_auto_refresh = now
        self._start_job(force=False)

    def request_refresh(self) -> None:
        if self._disposed:
            return
        if self.community is None:
            self._set_status("刷新未接入：仅显示随包快照")
            return
        if self._job is not None and self._job.is_running():
            return
        self._start_job(force=True)

    def _start_job(self, *, force: bool) -> None:
        self._job = CommunityRefreshJob(self.community, force=force)
        self._job.progress.connect(self._on_progress)
        self._job.done.connect(self._on_job_done)
        self._job.failed.connect(self._on_job_failed)
        self.refresh_button.setEnabled(False)
        self.fan_refresh.setEnabled(False)
        self._set_status("正在更新致谢数据…")
        self._job.start()

    def _finish_job(self) -> None:
        self.refresh_button.setEnabled(True)
        self.fan_refresh.setEnabled(True)

    def _on_progress(self, message: str) -> None:
        if not self._disposed:
            self._set_status(str(message))

    def _on_job_done(self, report) -> None:
        if self._disposed:
            return
        self._finish_job()
        self.reload()
        errors = dict(getattr(report, "errors", {}) or {})
        notes = []
        for key in ("credits", "fans", "fan_count"):
            reason = errors.get(key)
            if not reason:
                continue
            text = REASON_TEXT.get(str(reason), str(reason))
            if key == "fan_count":
                notes.append(f"粉丝总数查询失败：{text}")
            elif key == "fans":
                notes.append(f"粉丝名单未更新：{text}（保留原名单）")
            else:
                notes.append(f"贡献者未更新：{text}（保留原数据）")
        if notes:
            self._set_status("；".join(notes))
        else:
            self._set_status(f"已更新：{self.summary()}")

    def _on_job_failed(self, message: str) -> None:
        if self._disposed:
            return
        self._finish_job()
        self._set_status(f"刷新失败：{message}")

    def _set_status(self, text: str) -> None:
        self.status_label.setText(str(text or ""))

    def set_status(self, text: str) -> None:
        self._set_status(text)

    # ------------------------------------------------------------ 动作
    def _copy(self, text: str) -> None:
        QApplication.clipboard().setText(str(text))
        self._set_status(f"已复制 {text}")

    def _open_url(self, url: str) -> None:
        QDesktopServices.openUrl(QUrl(str(url)))

    # ------------------------------------------------------------ 释放
    def dispose(self) -> None:
        self._disposed = True
        self.fan_search_timer.stop()
        if self._job is not None:
            self._job.cancel()
            self._job = None
