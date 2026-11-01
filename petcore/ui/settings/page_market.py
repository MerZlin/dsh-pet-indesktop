"""插件市场设置页：浏览索引、安装、更新与回滚。"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ...market import download as market_download
from ...market import install as market_install
from ...market import model
from ...market.source import MarketSource, SourceResult
from ...net.market import HttpFetcher
from ...net.market_transport import local_file_root_for_index
from ..market_jobs import MarketJobRegistry
from .base import SettingsPage, make_button
from ..download_progress import SpeedMeter as _SpeedMeter
from ..download_progress import describe_progress as _describe_progress
from ..download_progress import format_bytes as _format_bytes

FILTER_ALL = ""

#: 进度信号的节流窗口（秒）：10 Hz 足够让进度条看起来连续，又不会把 GUI 线程刷满。
PROGRESS_INTERVAL_S = 0.1

#: 旧测试/第三方探针的兼容符号；真实任务由应用级 MarketJobRegistry 持有。
_ORPHAN_JOBS: set[QThread] = set()


class _CancellableJob(QThread):
    def __init__(self) -> None:
        super().__init__()
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()
        self.requestInterruption()

    def cancelled(self) -> bool:
        return self._cancel_event.is_set() or self.isInterruptionRequested()


class _IndexJob(_CancellableJob):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, source: MarketSource, custom_url: str, force: bool):
        super().__init__()
        self._source = source
        self._custom_url = custom_url
        self._force = force

    def run(self) -> None:
        try:
            result = self._source.load(
                custom_url=self._custom_url,
                force=self._force,
                cancel=self.cancelled,
            )
        except Exception as exc:  # noqa: BLE001 - 线程里只许发信号
            if not self.cancelled():
                self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        if not self.cancelled():
            self.done.emit(result)


class _InstallJob(_CancellableJob):
    done = Signal(object)
    #: 下载进度（已完成字节, 索引声明的大小）；节流后从工作线程排队到 GUI 线程。
    progress = Signal(int, int)

    def __init__(
        self,
        root: Path,
        package,
        version,
        fetcher,
        allow_feature: bool,
        *,
        index_origin: str = "",
        local_file_root: Path | None = None,
        connections: int = 1,
        mirror_policy: str = "auto",
    ):
        super().__init__()
        self._root = root
        self._package = package
        self._version = version
        self._fetcher = fetcher
        self._allow_feature = allow_feature
        self._index_origin = index_origin
        self._local_file_root = local_file_root
        self._connections = connections
        self._mirror_policy = mirror_policy
        self._last_emit = 0.0

    def _report_progress(self, done: int, total: int) -> None:
        now = time.monotonic()
        finished = total > 0 and done >= total
        if not finished and now - self._last_emit < PROGRESS_INTERVAL_S:
            return
        self._last_emit = now
        if not self.cancelled():
            self.progress.emit(int(done), int(total))

    def run(self) -> None:
        try:
            outcome = market_install.install_package(
                self._root,
                self._package,
                self._version,
                fetcher=self._fetcher,
                allow_feature=self._allow_feature,
                index_origin=self._index_origin,
                local_file_root=self._local_file_root,
                cancel=self.cancelled,
                progress=self._report_progress,
                connections=self._connections,
                mirror_policy=self._mirror_policy,
            )
        except Exception as exc:  # noqa: BLE001
            outcome = market_install.InstallOutcome(
                False, f"{type(exc).__name__}: {exc}", mod_id=self._package.id
            )
        if not self.cancelled() or outcome.ok:
            # 取消只丢弃失败/迟到的结果；真的装上了就必须让页面刷新（磁盘是事实）。
            self.done.emit(outcome)


class _RollbackJob(_CancellableJob):
    done = Signal(object)

    def __init__(self, root: Path, mod_id: str):
        super().__init__()
        self._root = root
        self._mod_id = mod_id

    def run(self) -> None:
        if self.cancelled():
            return
        try:
            outcome = market_install.rollback(self._root, self._mod_id)
        except Exception as exc:  # noqa: BLE001
            outcome = market_install.InstallOutcome(
                False, f"{type(exc).__name__}: {exc}", mod_id=self._mod_id
            )
        if not self.cancelled():
            self.done.emit(outcome)


class MarketPage(SettingsPage):
    title = "插件市场"

    def __init__(self, host, parent=None, *, fetcher=None):
        super().__init__(host, parent)
        self._root = Path(str(getattr(host, "root", Path.cwd())))
        self._fetcher = fetcher or HttpFetcher()
        self._source = MarketSource(self._root, self._fetcher)
        self._result: SourceResult | None = None
        self._packages: list[model.MarketPackage] = []
        self._busy = False
        self._loaded_once = False
        self._disposed = False
        self._jobs: list[QThread] = []
        self._job_registry = getattr(host, "market_jobs", None) or MarketJobRegistry()
        self._installed: dict[str, str] = {}
        self._reload_installed()
        self._initial_url = self._config_url()
        self._busy_kind = ""
        self._install_connections = 1
        self._install_resumed = 0
        self._meter = _SpeedMeter()

        self.add_note(
            "插件市场只从索引读取名称、版本、更新说明与 SHA-256；"
            "安装前会校验大小与哈希，失败不会破坏已安装版本。"
            "下载按设置里的连接数并行分段，取消后已下载的部分会保留，下次续传。"
        )

        card = self.section("可用插件")
        controls = QWidget(self)
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(8)
        self.search_field = QLineEdit(controls)
        self.search_field.setObjectName("marketSearch")
        self.search_field.setPlaceholderText("搜索名称、id 或说明")
        self.search_field.setClearButtonEnabled(True)
        self.search_field.textChanged.connect(lambda _text: self._refresh_list())
        self.category_combo = QComboBox(controls)
        self.category_combo.setObjectName("marketCategory")
        self.category_combo.setAccessibleName("插件分类")
        self.category_combo.currentIndexChanged.connect(lambda _index: self._refresh_list())
        self.refresh_button = make_button("刷新", controls)
        self.refresh_button.clicked.connect(lambda: self.refresh())
        controls_layout.addWidget(self.search_field, 1)
        controls_layout.addWidget(self.category_combo)
        controls_layout.addWidget(self.refresh_button)
        card.add_row("筛选", controls, "索引有 5 分钟缓存；离线时继续使用上次结果。", expand=True)

        self.source_label = QLabel("尚未读取索引", self)
        self.source_label.setObjectName("settingHint")
        self.source_label.setWordWrap(True)
        card.add_widget(self.source_label)

        self.package_list = QListWidget(self)
        self.package_list.setObjectName("marketList")
        self.package_list.setAccessibleName("插件市场列表")
        self.package_list.setMinimumHeight(210)
        self.package_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.package_list.currentRowChanged.connect(lambda _row: self._update_detail())
        card.add_widget(self.package_list)

        self.detail_label = QLabel("", self)
        self.detail_label.setObjectName("settingHint")
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card.add_widget(self.detail_label)

        self.install_button = make_button("下载并安装", self)
        self.install_button.clicked.connect(lambda: self.install_selected())
        self.cancel_button = make_button("取消", self)
        self.cancel_button.setObjectName("marketCancel")
        self.cancel_button.setToolTip("取消正在进行的下载；已下载的字节会保留，下次续传")
        self.cancel_button.clicked.connect(lambda: self.cancel_install())
        self.rollback_button = make_button("回滚到上一版本", self)
        self.rollback_button.clicked.connect(lambda: self.rollback_selected())
        card.add_buttons([self.install_button, self.cancel_button, self.rollback_button])

        # 进度条与状态行装在同一个容器里：没有下载时它们只是隐藏，
        # 卡片不会多出一段空白行。
        self.progress_panel = QWidget(self)
        panel_layout = QVBoxLayout(self.progress_panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(6)
        self.progress_bar = QProgressBar(self.progress_panel)
        self.progress_bar.setObjectName("marketProgress")
        self.progress_bar.setAccessibleName("下载进度")
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.hide()
        panel_layout.addWidget(self.progress_bar)
        self.progress_label = QLabel("", self.progress_panel)
        self.progress_label.setObjectName("settingHint")
        self.progress_label.setWordWrap(True)
        self.progress_label.hide()
        panel_layout.addWidget(self.progress_label)

        self.status_label = QLabel("", self.progress_panel)
        self.status_label.setObjectName("settingHint")
        self.status_label.setWordWrap(True)
        panel_layout.addWidget(self.status_label)
        card.add_widget(self.progress_panel)

        source_card = self.section("索引源")
        self.index_field = QLineEdit(self)
        self.index_field.setObjectName("marketIndexUrl")
        self.index_field.setPlaceholderText("留空使用官方索引")
        self.index_field.setText(self._initial_url)
        self.index_field.textChanged.connect(lambda _text: self.changed.emit())
        source_card.add_row(
            "自定义索引地址",
            self.index_field,
            "可填写镜像的 index.json 地址；官方索引仍作为后备。设置随窗口保存。",
            expand=True,
        )
        self.reset_source_button = make_button("恢复官方索引", self)
        self.reset_source_button.clicked.connect(self._reset_source)
        source_card.add_buttons([self.reset_source_button])

        self._set_busy(False)
        self._update_detail()

    # ------------------------------------------------------------ 生命周期
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._loaded_once:
            self._loaded_once = True
            self.refresh()

    def dispose(self) -> None:
        self._disposed = True
        for job in list(self._jobs):
            try:
                cancel = getattr(job, "cancel", None)
                if callable(cancel):
                    cancel()
                for signal_name in ("done", "failed"):
                    signal = getattr(job, signal_name, None)
                    if signal is not None:
                        signal.disconnect()
            except (RuntimeError, TypeError):
                pass
        self._jobs.clear()
        super().dispose()

    def _track(self, job: QThread) -> None:
        self._jobs.append(job)
        self._job_registry.submit(job)

        def _finished() -> None:
            if job in self._jobs:
                self._jobs.remove(job)
            try:
                job.deleteLater()
            except RuntimeError:
                pass
            if not self._jobs and self._busy and not self._disposed:
                # 取消的安装/回滚不会再发 done：收尾必须在这里，否则页面永远 busy。
                message = (
                    "已取消安装；已下载的字节保留在下载目录，下次会接着下。"
                    if self._busy_kind == "install"
                    else "操作已取消。"
                )
                self._hide_progress()
                self._set_busy(False, message)

        job.finished.connect(_finished)

    # ------------------------------------------------------------ 索引
    def _config(self):
        return getattr(self.host, "config", None)

    def _config_url(self) -> str:
        config = self._config()
        if config is None:
            return ""
        try:
            return model.normalize_index_url(str(config.get("market_index_url") or ""))
        except Exception:
            return ""

    def _effective_url(self) -> str:
        return model.normalize_index_url(self.index_field.text())

    def refresh(self, *, blocking: bool = False, force: bool = True) -> None:
        if self._disposed or self._busy:
            return
        self._set_busy(True, "正在读取索引…")
        custom = self._effective_url()
        if blocking:
            try:
                result = self._source.load(custom_url=custom, force=force)
            except Exception as exc:  # noqa: BLE001
                self._set_busy(False, f"读取索引失败：{type(exc).__name__}: {exc}")
                return
            self._apply_result(result)
            return
        job = _IndexJob(self._source, custom, force)
        self._track(job)
        job.done.connect(self._apply_result)
        job.failed.connect(lambda message: self._set_busy(False, f"读取索引失败：{message}"))
        job.start()

    def _apply_result(self, result: SourceResult) -> None:
        if self._disposed:
            return
        self._result = result
        self._packages = list(result.index.packages)
        self._reload_installed()
        self._rebuild_categories()
        self._refresh_list()
        stamp = self._format_time(result.fetched_at)
        if result.stale:
            self.source_label.setText(
                f"索引可能过期：所有索引源都不可用，正在使用本地缓存（{stamp}）。"
            )
        elif result.origin:
            self.source_label.setText(f"索引：{result.origin} · 更新于 {stamp}")
        else:
            self.source_label.setText("索引为空")
        problems = list(result.problems)
        if problems:
            self.source_label.setToolTip("\n".join(problems[:6]))
        else:
            self.source_label.setToolTip("")
        self._set_busy(False, f"共 {len(self._packages)} 个插件")

    @staticmethod
    def _format_time(timestamp: float) -> str:
        if not timestamp:
            return "未知时间"
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp))

    def _rebuild_categories(self) -> None:
        selected = str(self.category_combo.currentData() or FILTER_ALL)
        categories = sorted({item for package in self._packages for item in package.categories})
        self.category_combo.blockSignals(True)
        try:
            self.category_combo.clear()
            self.category_combo.addItem("全部分类", FILTER_ALL)
            for category in categories:
                self.category_combo.addItem(category, category)
            index = self.category_combo.findData(selected)
            self.category_combo.setCurrentIndex(max(0, index))
        finally:
            self.category_combo.blockSignals(False)

    # ------------------------------------------------------------ 列表
    def _visible_packages(self) -> list[model.MarketPackage]:
        needle = self.search_field.text().strip().lower()
        category = str(self.category_combo.currentData() or FILTER_ALL)
        visible: list[model.MarketPackage] = []
        for package in self._packages:
            if category and category not in package.categories:
                continue
            if needle:
                haystack = " ".join(
                    (
                        package.id,
                        package.name,
                        package.name_zh,
                        package.summary,
                        package.author,
                    )
                ).lower()
                if needle not in haystack:
                    continue
            visible.append(package)
        return visible

    def _state_for(self, package: model.MarketPackage) -> model.PackageState:
        return model.package_state(
            package,
            installed_version=self._installed.get(package.id) or None,
            platform=sys.platform,
            core_version=model.CORE_VERSION,
        )

    def _reload_installed(self) -> None:
        self._installed = market_install.scan_installed(self._root)

    def _refresh_list(self) -> None:
        selected = self.selected_package_id()
        self.package_list.blockSignals(True)
        try:
            self.package_list.clear()
            for package in self._visible_packages():
                state = self._state_for(package)
                text = f"{package.display_name} · {package.id} · v{package.version} · {state.label}"
                if state.installed and state.state != model.STATE_INSTALLED:
                    text += f"（已安装 v{state.installed}）"
                item = QListWidgetItem(text, self.package_list)
                item.setData(Qt.ItemDataRole.UserRole, package.id)
                if state.reason:
                    item.setToolTip(state.reason)
            if self.package_list.count() == 0:
                placeholder = QListWidgetItem(
                    "（还没有可显示的插件：点「刷新」读取索引）", self.package_list
                )
                placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
        finally:
            self.package_list.blockSignals(False)

        if selected:
            self.select_package(selected)
        else:
            self.package_list.setCurrentRow(0 if self.package_list.count() else -1)
        self._update_detail()

    def selected_package_id(self) -> str:
        item = self.package_list.currentItem()
        if item is None:
            return ""
        return str(item.data(Qt.ItemDataRole.UserRole) or "")

    def select_package(self, mod_id: str) -> None:
        for row in range(self.package_list.count()):
            item = self.package_list.item(row)
            if str(item.data(Qt.ItemDataRole.UserRole) or "") == mod_id:
                self.package_list.setCurrentRow(row)
                break
        self._update_detail()

    def selected_package(self) -> model.MarketPackage | None:
        mod_id = self.selected_package_id()
        return next((package for package in self._packages if package.id == mod_id), None)

    def _update_detail(self) -> None:
        package = self.selected_package()
        if package is None:
            self.detail_label.setText("选择上面的插件查看版本与更新说明。")
            self.install_button.setEnabled(False)
            self.rollback_button.setEnabled(False)
            return
        state = self._state_for(package)
        version = model.latest_version(package)
        lines = [f"{package.display_name}（{package.id}）"]
        meta = [package.kind, f"最新 v{package.version}"]
        if package.author:
            meta.append(f"作者 {package.author}")
        lines.append(" · ".join(meta))
        if package.summary:
            lines.append(package.summary)
        status_line = f"状态：{state.label}"
        if state.reason:
            status_line += f"（{state.reason}）"
        lines.append(status_line)
        if package.core_requires:
            lines.append(f"Core 要求：{package.core_requires}")
        if version is not None:
            lines.append(f"下载：{version.size / 1024:.1f} KiB · SHA-256 {version.sha256[:16]}…")
            if version.changelog:
                lines.append(f"更新说明：{version.changelog}")
        if package.homepage:
            lines.append(package.homepage)
        self.detail_label.setText("\n".join(lines))

        allow_feature = True
        config = self._config()
        if package.kind == "feature" and config is not None:
            allow_feature = bool(config.get("allow_feature_mods"))
        installable = state.installable and version is not None
        self.install_button.setEnabled(installable and allow_feature and not self._busy)
        self.install_button.setText(
            f"更新到 {package.version}" if state.state == model.STATE_UPDATE else "下载并安装"
        )
        if package.kind == "feature" and not allow_feature:
            self.install_button.setToolTip("先在「扩展管理」里允许功能扩展")
        elif state.reason:
            self.install_button.setToolTip(state.reason)
        else:
            self.install_button.setToolTip("")

        entry = (market_install.load_lock(self._root).get("packages") or {}).get(package.id)
        has_backup = bool(entry) and bool(entry.get("backup"))
        self.rollback_button.setEnabled(has_backup and not self._busy)

    # ------------------------------------------------------------ 安装/回滚
    def _local_file_root(self) -> Path | None:
        origin = str(self._result.origin if self._result is not None else "")
        return local_file_root_for_index(origin)

    def _download_options(self) -> tuple[int, str]:
        """连接数与镜像策略（全局键）；读不到就按单流/自动处理。"""
        config = self._config()
        if config is None:
            return 1, "auto"
        try:
            connections = int(config.get("market_download_connections") or 1)
        except (TypeError, ValueError):
            connections = 1
        try:
            sources = str(config.get("market_download_sources") or "auto").strip().lower()
        except Exception:  # noqa: BLE001
            sources = "auto"
        if sources not in {"auto", "ordered", "race"}:
            sources = "auto"
        return max(1, min(8, connections)), sources

    def install_selected(self, *, blocking: bool = False) -> None:
        package = self.selected_package()
        version = model.latest_version(package) if package is not None else None
        if package is None or version is None:
            self._set_busy(False, "没有可安装的版本")
            return
        config = self._config()
        allow_feature = True
        if config is not None:
            allow_feature = bool(config.get("allow_feature_mods"))
        if package.kind == "feature" and not allow_feature:
            self._set_busy(False, "先在「扩展管理」里允许功能扩展，再安装这个包。")
            return
        connections, mirror_policy = self._download_options()
        self._install_connections = connections
        self._install_resumed = 0
        try:
            self._install_resumed = market_download.resumable_bytes(
                self._root, package, version
            )
        except Exception:  # noqa: BLE001 - 只是显示用，读不到就不显示
            self._install_resumed = 0
        self._reset_progress(version.size)
        self._set_busy(True, f"正在安装 {package.id} {version.version}…", kind="install")
        if blocking:
            outcome = market_install.install_package(
                self._root,
                package,
                version,
                fetcher=self._fetcher,
                allow_feature=allow_feature,
                index_origin=self._result.origin if self._result is not None else "",
                local_file_root=self._local_file_root(),
                connections=connections,
                mirror_policy=mirror_policy,
            )
            self._apply_install(outcome)
            return
        job = _InstallJob(
            self._root,
            package,
            version,
            self._fetcher,
            allow_feature,
            index_origin=self._result.origin if self._result is not None else "",
            local_file_root=self._local_file_root(),
            connections=connections,
            mirror_policy=mirror_policy,
        )
        self._track(job)
        job.done.connect(self._apply_install)
        job.progress.connect(self._on_install_progress)
        job.start()

    # ------------------------------------------------------------ 下载进度
    def _reset_progress(self, total: int) -> None:
        self._meter.reset()
        self.progress_bar.setRange(0, max(1, int(total or 1)))
        self.progress_bar.setValue(0)
        self.progress_label.setText("")
        self.progress_bar.setVisible(bool(total))
        self.progress_label.setVisible(bool(total))

    def _hide_progress(self) -> None:
        self.progress_bar.hide()
        self.progress_label.hide()
        self._meter.reset()

    @staticmethod
    def describe_progress(
        *,
        done: int,
        total: int,
        speed: float,
        eta: float | None,
        connections: int,
        resumed: int = 0,
    ) -> str:
        """进度行文本：已下载/总量 · 速度 · 剩余 · 连接数（可选续传量）。"""
        return _describe_progress(
            done=done,
            total=total,
            speed=speed,
            eta=eta,
            connections=connections,
            resumed=resumed,
        )

    def _on_install_progress(self, done: int, total: int) -> None:
        if self._disposed:
            return
        speed, eta = self._meter.update(done, total)
        self.progress_bar.setRange(0, max(1, int(total or 1)))
        self.progress_bar.setValue(max(0, min(int(done), int(total or 0))))
        self.progress_label.setText(
            self.describe_progress(
                done=done,
                total=total,
                speed=speed,
                eta=eta,
                connections=self._install_connections,
                resumed=self._install_resumed,
            )
        )

    def cancel_install(self) -> None:
        """取消正在跑的安装/回滚任务；已写字节保留供续传。"""
        if not self._busy:
            return
        target = self._busy_kind if self._busy_kind in {"install", "rollback"} else ""
        if not target:
            return
        cancelled = 0
        for job in list(self._jobs):
            cancel = getattr(job, "cancel", None)
            if callable(cancel):
                try:
                    cancel()
                    cancelled += 1
                except RuntimeError:
                    pass
        if cancelled:
            self.status_label.setText("正在取消…已下载的字节保留，下次会续传。")
            self.cancel_button.setEnabled(False)

    def _apply_install(self, outcome: market_install.InstallOutcome) -> None:
        if self._disposed:
            return
        message = outcome.message
        if outcome.ok and not outcome.restart_required:
            reload = getattr(self.host, "reload_mods", None)
            if callable(reload):
                try:
                    reload()
                    message += "（已重新加载）"
                except Exception as exc:  # noqa: BLE001
                    message += f"（重新加载失败：{type(exc).__name__}）"
        if outcome.attempts:
            message += f"；镜像尝试 {len(outcome.attempts)} 次"
        if outcome.ok:
            if outcome.connections > 1:
                message += f"；{outcome.connections} 连接并行下载"
            if outcome.resumed_from > 0:
                message += f"；续传 {_format_bytes(outcome.resumed_from)}"
        self._hide_progress()
        self._set_busy(False, message)
        self._reload_installed()
        self._refresh_list()

    def rollback_selected(self, *, blocking: bool = False) -> None:
        package = self.selected_package()
        if package is None:
            self._set_busy(False, "请先选择一个插件")
            return
        self._hide_progress()
        self._set_busy(True, f"正在回滚 {package.id}…", kind="rollback")
        if blocking:
            outcome = market_install.rollback(self._root, package.id)
            self._apply_install(outcome)
            return
        job = _RollbackJob(self._root, package.id)
        self._track(job)
        job.done.connect(self._apply_install)
        job.start()

    def _set_busy(self, busy: bool, message: str = "", *, kind: str = "") -> None:
        self._busy = bool(busy)
        self._busy_kind = str(kind) if busy else ""
        self.refresh_button.setEnabled(not busy)
        self.search_field.setEnabled(not busy)
        self.package_list.setEnabled(not busy)
        self.cancel_button.setEnabled(bool(busy) and self._busy_kind in {"install", "rollback"})
        if message:
            self.status_label.setText(message)
        if not busy:
            self._update_detail()
        else:
            self.install_button.setEnabled(False)
            self.rollback_button.setEnabled(False)

    # ------------------------------------------------------------ 设置保存
    def _reset_source(self) -> None:
        self.index_field.setText("")
        self.status_label.setText("已恢复官方索引；保存后重新读取。")
        self.changed.emit()

    def _raw_url(self) -> str:
        return self.index_field.text().strip()

    def dirty(self) -> bool:
        return self._raw_url() != self._initial_url

    def confirm_save(self) -> None:
        raw = self._raw_url()
        value = model.normalize_index_url(raw)
        if raw and not value:
            self.status_label.setText(
                "索引地址必须以 http:// 或 https:// 开头；本次未保存。"
            )
            return
        config = self._config()
        if config is not None:
            config.set("market_index_url", value)
        self._initial_url = value
        self.index_field.blockSignals(True)
        self.index_field.setText(value)
        self.index_field.blockSignals(False)

    def discard_changes(self) -> None:
        self.index_field.blockSignals(True)
        self.index_field.setText(self._initial_url)
        self.index_field.blockSignals(False)
        config = self._config()
        if config is not None:
            config.set("market_index_url", self._initial_url)
        self.changed.emit()

    def keywords(self) -> list[str]:
        return [
            "插件市场",
            "市场",
            "商店",
            "安装",
            "更新",
            "回滚",
            "镜像",
            "索引",
            "下载源",
            "下载连接",
            "多连接",
            "分段下载",
            "扩展",
        ]
