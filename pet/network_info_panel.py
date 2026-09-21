# -*- coding: utf-8 -*-
"""网络信息弹窗：公网 IP / 属地是否在中国 / 能否连外网。

从右键菜单「网络信息」打开，**非模态单例**（与 ``todo_panel`` 同模式）。

## 为什么这个面板要异步

两段数据的时间差很大（本机实测）：

| 数据 | 耗时 | 做法 |
|---|---|---|
| 公网 IP + 属地 | **400~500 ms** | 后台线程拿 |
| 外网连通性 | **3.5~6 秒** | 后台线程拿，且**必须**——直连失败要等 DNS 超时 |

所以面板**先显示占位**，数据回来后经 Qt 信号填进去。绝不在 GUI 线程里等——
那是 ``fb38824`` 修过的老毛病（SMTC 阻塞主线程导致窗口未响应）。

## 隐私

查询会把本机公网 IP 暴露给第三方服务，所以**只在用户主动打开面板时才查**，
并在界面底部明确告知。
"""
from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import network_status as ns

log = logging.getLogger(__name__)


def _stylesheet(widget: QWidget) -> str:
    """沿用待办面板的配色口径（明暗跟随系统调色板）。"""
    dark = widget.palette().color(QPalette.ColorRole.Window).lightness() < 128
    if dark:
        window, card, border = "#202024", "#2a2a30", "#3a3a42"
        text, hint = "#e0e0e6", "#9a9aa3"
    else:
        window, card, border = "#fcfcfd", "#ffffff", "#e2e4e8"
        text, hint = "#252525", "#777777"
    accent = "#0a84ff"
    return f"""
    QDialog, QWidget#netRowsHost {{
        background: {window}; color: {text}; font-size: 13px;
    }}
    QLabel#pageTitle {{
        font-size: 22px; font-weight: 600; color: {text}; background: transparent;
    }}
    QLabel#netHint, QLabel#netPrivacy {{
        color: {hint}; font-size: 12px; background: transparent;
    }}
    QFrame#netCard {{
        background: {card}; border: 1px solid {border}; border-radius: 12px;
    }}
    QLabel#netKey {{ color: {hint}; font-size: 12px; background: transparent; }}
    QLabel#netValue {{ color: {text}; font-size: 13px; background: transparent; }}
    QLabel#netVerdict {{ font-size: 14px; font-weight: 600; background: transparent; }}
    QPushButton {{
        color: {text}; background: transparent;
        border: 1px solid {accent}; border-radius: 8px;
        padding: 5px 14px; font-size: 12px;
    }}
    QPushButton:hover {{ background: {accent}; color: #ffffff; }}
    QPushButton:disabled {{ color: {hint}; border-color: {border}; }}
    """


class _ProbeWorker(QObject):
    """后台探测：结果经 Qt 信号回主线程（跨线程不直接碰控件）。"""

    location_ready = Signal(object)      # ns.LocationInfo
    connectivity_ready = Signal(object)  # ns.ConnectivityResult

    def fetch_location(self) -> None:
        try:
            self.location_ready.emit(ns.fetch_ip_location())
        except Exception:
            log.debug("属地查询异常", exc_info=True)
            self.location_ready.emit(ns.LocationInfo(ok=False, error="查询异常"))

    def fetch_connectivity(self) -> None:
        try:
            self.connectivity_ready.emit(ns.check_connectivity())
        except Exception:
            log.debug("连通性检测异常", exc_info=True)
            self.connectivity_ready.emit(ns.ConnectivityResult())


class NetworkInfoDialog(QDialog):
    """网络信息面板。打开时自动查询；「重新检测」可手动再来一次。"""

    def __init__(self, app=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app = app
        self._busy = False
        self.setWindowTitle("网络信息")
        self.setObjectName("networkInfoDialog")
        self.setModal(False)
        self.resize(420, 330)
        self.setMinimumWidth(380)
        self.setStyleSheet(_stylesheet(self))

        self._worker = _ProbeWorker()
        self._worker.location_ready.connect(self._on_location)
        self._worker.connectivity_ready.connect(self._on_connectivity)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 14)
        root.setSpacing(10)

        title = QLabel("网络信息")
        title.setObjectName("pageTitle")
        root.addWidget(title)

        self._hint = QLabel("正在查询…")
        self._hint.setObjectName("netHint")
        root.addWidget(self._hint)

        # ---- 卡片一：公网 IP 与属地 ----
        self._ip_card, ip_grid = self._make_card("公网 IP")
        self._ip_value = self._add_row(ip_grid, 0, "公网 IP", "查询中…")
        self._region_value = self._add_row(ip_grid, 1, "IP 属地", "查询中…")
        self._country_verdict = QLabel("属地判断  查询中…")
        self._country_verdict.setObjectName("netVerdict")
        ip_grid.addWidget(self._country_verdict, 2, 0, 1, 2)
        root.addWidget(self._ip_card)

        # ---- 卡片二：外网连通性 ----
        self._net_card, self._net_grid = self._make_card("外网连通性")
        self._reach_verdict = QLabel("检测中…")
        self._reach_verdict.setObjectName("netVerdict")
        self._net_grid.addWidget(self._reach_verdict, 0, 0, 1, 2)
        self._target_labels: dict[str, QLabel] = {}
        root.addWidget(self._net_card)

        root.addStretch(1)

        footer = QHBoxLayout()
        privacy = QLabel("ⓘ 会联网查询，IP 将被发送至第三方服务")
        privacy.setObjectName("netPrivacy")
        footer.addWidget(privacy)
        footer.addStretch(1)
        self._retry = QPushButton("重新检测")
        self._retry.clicked.connect(self.refresh_now)
        footer.addWidget(self._retry)
        root.addLayout(footer)

    # ------------------------------------------------------------ 构建辅助

    def _make_card(self, title: str) -> tuple[QFrame, QGridLayout]:
        card = QFrame(self)
        card.setObjectName("netCard")
        outer = QVBoxLayout(card)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(6)
        head = QLabel(title)
        head.setObjectName("netKey")
        outer.addWidget(head)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        outer.addLayout(grid)
        return card, grid

    def _add_row(self, grid: QGridLayout, row: int, key: str, value: str) -> QLabel:
        key_label = QLabel(key)
        key_label.setObjectName("netKey")
        value_label = QLabel(value)
        value_label.setObjectName("netValue")
        value_label.setWordWrap(True)
        value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        grid.addWidget(key_label, row, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(value_label, row, 1)
        return value_label

    # ------------------------------------------------------------ 查询

    def refresh_now(self) -> None:
        """立刻重新查询（也用于首次打开）。"""
        if self._busy:
            return
        self._busy = True
        self._retry.setEnabled(False)
        self._hint.setText("正在查询…")
        self._ip_value.setText("查询中…")
        self._region_value.setText("查询中…")
        self._country_verdict.setText("属地判断  查询中…")
        self._reach_verdict.setText("检测中…")
        for label in self._target_labels.values():
            label.setText("")
        threading.Thread(target=self._worker.fetch_location,
                         name="net-info-location", daemon=True).start()
        threading.Thread(target=self._worker.fetch_connectivity,
                         name="net-info-connectivity", daemon=True).start()

    def _on_location(self, info: ns.LocationInfo) -> None:
        if info.ok:
            self._ip_value.setText(info.ip or "未知")
            self._region_value.setText(info.region_text or "未知")
            in_china = info.in_china
            if in_china is True:
                self._country_verdict.setText("属地判断  ✅ 在中国")
                self._country_verdict.setStyleSheet("color: #2e9e5b;")
            elif in_china is False:
                self._country_verdict.setText("属地判断  ⚠️ 不在中国")
                self._country_verdict.setStyleSheet("color: #d98a2b;")
            else:
                self._country_verdict.setText("属地判断  无法判定")
                self._country_verdict.setStyleSheet("")
        else:
            self._ip_value.setText("查询失败")
            self._region_value.setText("查询失败")
            self._country_verdict.setText("属地判断  查询失败")
            self._country_verdict.setStyleSheet("")
        self._maybe_idle()

    def _on_connectivity(self, result: ns.ConnectivityResult) -> None:
        if result.reachable:
            self._reach_verdict.setText("外网连通性  ✅ 可访问")
            self._reach_verdict.setStyleSheet("color: #2e9e5b;")
        else:
            self._reach_verdict.setText("外网连通性  ❌ 不可访问")
            self._reach_verdict.setStyleSheet("color: #d9534f;")
        # 逐目标明细：首次填充时建行，之后只改文本
        for index, (name, ok, elapsed_ms) in enumerate(result.targets):
            label = self._target_labels.get(name)
            if label is None:
                label = QLabel("")
                label.setObjectName("netValue")
                self._target_labels[name] = label
                # 明细从第 1 行起（第 0 行是结论）
                self._net_grid.addWidget(label, index + 1, 0, 1, 2)
            mark = "✅" if ok else "❌"
            label.setText(f"    {name}  {mark}  {elapsed_ms:.0f} ms")
        self._maybe_idle()

    def _maybe_idle(self) -> None:
        """两路都回来了才恢复按钮——只回来一路时按钮仍禁用，避免误以为全好了。"""
        # 用「结论标签是否还有占位文案」判断该路是否已回。
        loc_done = "查询中" not in self._country_verdict.text()
        net_done = "检测中" not in self._reach_verdict.text()
        if loc_done and net_done:
            self._busy = False
            self._retry.setEnabled(True)
            self._hint.setText("检测完成")
