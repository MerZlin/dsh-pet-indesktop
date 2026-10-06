"""Native Qt quota charts using local samples only; no model or network calls."""

from __future__ import annotations
import datetime as dt
import math
import time
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QToolTip, QVBoxLayout, QWidget
from .language_ui import language
from .ui_polish import colors, apply_palette, common_qss, titlebar, ui_text

COPY = {
    "title": ("Codex 用量趨勢", "Codex usage trends", "Codex 用量趋势"),
    "week": ("週用量", "Weekly", "周用量"),
    "used": ("已用", "Used", "已用"),
    "remaining": ("剩餘", "Remaining", "剩余"),
    "five": ("首次記錄到消耗起算五小時 · 已用額度百分比", "5 hours from first recorded use · used quota (%)", "首次记录到消耗起算五小时 · 已用额度百分比"),
    "daily": ("最近七天 · 每天觀測消耗（占總週額度 %）", "Last 7 days · daily use (% of total weekly quota)", "最近七天 · 每天观测消耗（占总周额度 %）"),
    "note5": (
        "未來時間留白；額度重置後重新起算。超過三分鐘未讀取時，曲線分段。",
        "Future time stays blank; resets start a new cycle. Reading gaps over 3 minutes break the line.",
        "未来时间留白；额度重置后重新起算。超过三分钟未读取时，曲线分段。",
    ),
    "note7": (
        "百分比以總週額度為基準；— 尚無可計算資料。紀錄完整度可在滑鼠提示查看。",
        "% is relative to total weekly quota; — unavailable. Hover for record completeness.",
        "百分比以总周额度为基准；— 尚无可计算数据。记录完整度可在鼠标提示查看。",
    ),
    "local": (
        "每分鐘記錄於本機 · 從啟用時開始累積 · 帳號共用額度",
        "Recorded locally each minute · history starts when enabled · shared account quota",
        "每分钟记录于本机 · 从启用时开始累积 · 账号共用额度",
    ),
    "empty": ("尚無紀錄，正在開始累積", "No history yet. Recording has started.", "尚无记录，正在开始累积"),
    "one": ("已開始記錄，更多讀取後會形成曲線", "Recording started. More readings will form a curve.", "已开始记录，更多读取后会形成曲线"),
    "waiting5": (
        "尚無消耗紀錄，讀取到用量後顯示五小時座標",
        "Waiting for recorded usage to start the five-hour axis.",
        "尚无消耗记录，读取到用量后显示五小时坐标",
    ),
    "reset": ("重置", "Reset", "重置"),
    "none": ("尚無資料", "No data", "尚无数据"),
    "daily_value": ("占總週額度 {value}%", "{value}% of total weekly quota", "占总周额度 {value}%"),
    "partial": ("紀錄不完整", "Partial record", "记录不完整"),
    "complete": ("紀錄完整", "Complete record", "记录完整"),
    "save": ("紀錄尚未成功儲存，稍後會重試", "History could not be saved. Will retry.", "记录尚未成功保存，稍后会重试"),
    "open": ("點擊查看用量趨勢", "Click to view usage trends", "点击查看用量趋势"),
    "updated": ("最後讀取 {time}", "Last reading {time}", "最后读取 {time}"),
    "unavailable": ("暫時無法讀取額度", "Quota reading unavailable", "暂时无法读取额度"),
    "count": ("{count} 筆讀取", "{count} readings", "{count} 次读取"),
}


def tr(name):
    return COPY[name][{"zh_TW": 0, "en": 1, "zh_CN": 2}.get(language(), 0)]


def row_label(row):
    minutes = row.get("windowDurationMins")
    prefix = str(row.get("label") or "").rsplit(" · ", 1)[0] + " · " if " · " in str(row.get("label") or "") else ""
    return prefix + ("5h" if minutes == 300 else tr("week")) + " " + tr("remaining") + "  ›"


def palette(style):
    c = colors("light" if style == "glass" else style)
    if style == "glass":
        c.update(bg="#eaf3fd", surface="#f5faff", border="#bfd4ed", field="#eff6ff", grid="#d5e3f3", track="#dbe9f8", pill="#ddecff")
    return c


class UsageChart(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.mode = "five"
        self.data = None
        self.colors = palette("dark")
        self.setMinimumHeight(230)
        self.setMouseTracking(True)
        self.setAccessibleName(tr("title"))

    def plot(self):
        return QRectF(50, 30, max(10, self.width() - 68), max(10, self.height() - 72))

    def maximum(self):
        if self.mode == "five" or not self.data:
            return 100
        return max(4, math.ceil(max((item["value"] or 0 for item in self.data), default=0) / 4) * 4)

    def xy(self, point):
        rect = self.plot()
        x = rect.left() + rect.width() * (point["t"] - self.data["start"]) / (self.data["end"] - self.data["start"])
        return QPointF(x, rect.bottom() - rect.height() * point["used"] / 100)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c, rect = self.colors, self.plot()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c["surface"]))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 14, 14)
        p.setFont(QFont("Microsoft JhengHei UI", 9))
        maximum = self.maximum()
        for index in range(5):
            value = maximum * index / 4
            y = rect.bottom() - rect.height() * index / 4
            p.setPen(QPen(QColor(c["grid"]), 1, Qt.PenStyle.DotLine))
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            p.setPen(QColor(c["muted"]))
            p.drawText(QRectF(2, y - 9, 40, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "%g%%" % value)
        if not self.data:
            p.setPen(QColor(c["muted"]))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, tr("empty"))
            return
        if self.mode == "five":
            if self.data.get("waiting"):
                p.setPen(QColor(c["muted"]))
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, tr("waiting5"))
                p.end()
                return
            now_x = rect.left() + rect.width() * (time.time() - self.data["start"]) / (self.data["end"] - self.data["start"])
            if rect.left() < now_x < rect.right():
                future = QRectF(now_x, rect.top(), rect.right() - now_x, rect.height())
                shade = QColor(c["bg"])
                shade.setAlpha(70)
                p.fillRect(future, shade)
                p.setPen(QPen(QColor(c["grid"]), 1, Qt.PenStyle.DashLine))
                p.drawLine(QPointF(now_x, rect.top()), QPointF(now_x, rect.bottom()))
                if future.width() > 100:
                    p.setPen(QColor(c["muted"]))
                    p.drawText(future, Qt.AlignmentFlag.AlignCenter, ui_text("upcoming"))
            for index in range(6):
                stamp = self.data["start"] + (self.data["end"] - self.data["start"]) * index / 5
                x = rect.left() + rect.width() * index / 5
                p.setPen(QColor(c["muted"]))
                p.drawText(QRectF(x - 25, rect.bottom() + 12, 50, 20), Qt.AlignmentFlag.AlignCenter, time.strftime("%H:%M", time.localtime(stamp)))
            for segment in self.data["segments"]:
                path = QPainterPath(self.xy(segment[0]))
                for point in segment[1:]:
                    path.lineTo(self.xy(point))
                if len(segment) > 1:
                    fill = QPainterPath(path)
                    fill.lineTo(QPointF(self.xy(segment[-1]).x(), rect.bottom()))
                    fill.lineTo(QPointF(self.xy(segment[0]).x(), rect.bottom()))
                    fill.closeSubpath()
                    gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
                    top, bottom = QColor(c["blue"]), QColor(c["blue"])
                    top.setAlpha(66)
                    bottom.setAlpha(3)
                    gradient.setColorAt(0, top)
                    gradient.setColorAt(1, bottom)
                    p.fillPath(fill, gradient)
                    p.setPen(QPen(QColor(c["blue"]), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawPath(path)
                p.setPen(QPen(QColor(c["surface"]), 2))
                p.setBrush(QColor(c["blue"]))
                p.drawEllipse(self.xy(segment[-1]), 4, 4)
            for stamp in self.data["resets"][-4:]:
                x = rect.left() + rect.width() * (stamp - self.data["start"]) / 18000
                p.setPen(QPen(QColor(c["muted"]), 1, Qt.PenStyle.DashLine))
                p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
                p.drawText(QRectF(max(rect.left(), min(x - 24, rect.right() - 48)), 8, 48, 18), Qt.AlignmentFlag.AlignCenter, tr("reset"))
            if self.data["count"] <= 1:
                p.setPen(QColor(c["muted"]))
                p.drawText(rect.adjusted(15, 0, -20, 0), Qt.AlignmentFlag.AlignCenter, tr("one") if self.data["count"] else tr("empty"))
        else:
            step = rect.width() / 7
            bar_width = min(42, step * 0.56)
            for index, item in enumerate(self.data):
                x = rect.left() + step * (index + 0.5)
                value = item["value"]
                if value is None:
                    p.setPen(QPen(QColor(c["grid"]), 1.2, Qt.PenStyle.DashLine))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRoundedRect(QRectF(x - bar_width / 2, rect.bottom() - 22, bar_width, 22), 5, 5)
                    top = rect.bottom() - 22
                    label = "—"
                else:
                    height = max(3, rect.height() * value / maximum)
                    top = rect.bottom() - height
                    p.setPen(Qt.PenStyle.NoPen)
                    color = QColor(c["blue"])
                    color.setAlpha(150 if item["partial"] else 235)
                    p.setBrush(color)
                    p.drawRoundedRect(QRectF(x - bar_width / 2, top, bar_width, height), 5, 5)
                    label = "%g%%" % value
                p.setPen(QColor(c["text"] if value is not None else c["muted"]))
                p.drawText(QRectF(x - step / 2, top - 23, step, 20), Qt.AlignmentFlag.AlignCenter, label)
                p.setPen(QColor(c["muted"]))
                date = dt.date.fromisoformat(item["date"])
                p.drawText(QRectF(x - step / 2, rect.bottom() + 12, step, 20), Qt.AlignmentFlag.AlignCenter, "%02d/%02d" % (date.month, date.day))
        p.end()

    def mouseMoveEvent(self, event):
        if not self.data or not self.plot().contains(event.position()):
            QToolTip.hideText()
            return
        if self.mode == "five":
            points = [point for segment in self.data["segments"] for point in segment]
            if not points:
                return
            nearest = min(points, key=lambda point: abs(self.xy(point).x() - event.position().x()))
            if abs(self.xy(nearest).x() - event.position().x()) > 14:
                QToolTip.hideText()
                return
            message = "%s\n%s %g%% · %s %g%%" % (
                time.strftime("%m/%d %H:%M", time.localtime(nearest["t"])),
                tr("used"),
                nearest["used"],
                tr("remaining"),
                100 - nearest["used"],
            )
        else:
            index = min(6, max(0, int((event.position().x() - self.plot().left()) / self.plot().width() * 7)))
            item = self.data[index]
            message = (
                item["date"]
                + "\n"
                + (
                    tr("none")
                    if item["value"] is None
                    else tr("daily_value").format(value="%g" % item["value"]) + " · " + (tr("partial") if item["partial"] else tr("complete"))
                )
            )
        QToolTip.showText(event.globalPosition().toPoint(), message, self)

    def leaveEvent(self, event):
        QToolTip.hideText()
        super().leaveEvent(event)


class UsageTrendWindow(QDialog):
    def __init__(self, controller):
        super().__init__(controller.pet, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.controller = controller
        self.mode = "five"
        self.limit_id = "codex"
        self.setObjectName("codexUsageTrendWindow")
        self.setMinimumSize(620, 560)
        self.resize(740, 600)
        self.setFont(QFont("Microsoft JhengHei UI", 10))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 20)
        layout.setSpacing(10)
        header = QHBoxLayout()
        self.heading = QLabel(self)
        self.heading.setFont(QFont("Microsoft JhengHei UI", 17, QFont.Weight.DemiBold))
        header.addWidget(self.heading)
        header.addStretch()
        self.five_button = QPushButton("5h", self)
        self.week_button = QPushButton(self)
        for button in (self.five_button, self.week_button):
            button.setCheckable(True)
            button.setMinimumHeight(36)
            button.setMinimumWidth(82)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            header.addWidget(button)
        self.five_button.clicked.connect(lambda: self.select("five"))
        self.week_button.clicked.connect(lambda: self.select("daily"))
        layout.addLayout(header)
        self.summary = QFrame(self)
        self.summary.setObjectName("usageSummary")
        summary_layout = QHBoxLayout(self.summary)
        summary_layout.setContentsMargins(20, 12, 20, 12)
        left = QVBoxLayout()
        left.setSpacing(0)
        self.metric_caption = QLabel(self.summary)
        self.metric_caption.setObjectName("usageCaption")
        self.remaining_metric = QLabel(self.summary)
        self.remaining_metric.setObjectName("usageRemaining")
        self.remaining_metric.setFont(QFont("Segoe UI", 28, QFont.Weight.DemiBold))
        left.addWidget(self.metric_caption)
        left.addWidget(self.remaining_metric)
        summary_layout.addLayout(left)
        summary_layout.addStretch(1)
        right = QVBoxLayout()
        right.setSpacing(5)
        self.used_metric = QLabel(self.summary)
        self.used_metric.setObjectName("usageUsed")
        self.used_metric.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.period_label = QLabel(self.summary)
        self.period_label.setObjectName("usagePeriod")
        self.period_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        right.addWidget(self.used_metric)
        right.addWidget(self.period_label)
        summary_layout.addLayout(right)
        layout.addWidget(self.summary)
        self.description = QLabel(self)
        layout.addWidget(self.description)
        self.chart = UsageChart(self)
        layout.addWidget(self.chart, 1)
        self.note = QLabel(self)
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.timer = QTimer(self)
        self.timer.setInterval(60000)
        self.timer.timeout.connect(self.refresh)
        self.refresh()

    def row(self):
        field = "primary" if self.mode == "five" else "secondary"
        minutes = 300 if self.mode == "five" else 10080
        rows = (self.controller.snapshot or {}).get("windows", [])
        return next(
            (row for row in rows if row.get("limitId", "codex") == self.limit_id and row.get("windowDurationMins") == minutes),
            {"limitId": self.limit_id, "window": field, "windowDurationMins": minutes},
        )

    def open_row(self, row):
        self.limit_id = row.get("limitId") or "codex"
        self.mode = "daily" if row.get("windowDurationMins") == 10080 else "five"
        self.refresh()
        self.show()
        self.raise_()
        self.activateWindow()

    def select(self, mode):
        self.mode = mode
        self.refresh()

    def refresh(self):
        from .independent_ui import quota_style

        style = quota_style(self.controller.pet.cfg)
        c = palette(style)
        self.setProperty("dshQuotaTheme", style)
        apply_palette(self, c)
        titlebar(self, style == "dark")
        self.setStyleSheet(
            "QDialog#codexUsageTrendWindow { background: %s; } QLabel { color: %s; background: transparent; } "
            "QFrame#usageSummary { background: %s; border: 1px solid %s; border-radius: 16px; } "
            "QLabel#usageCaption { color: %s; font-size: 12px; } QLabel#usageRemaining { color: %s; } "
            "QLabel#usageUsed { font-size: 16px; font-weight: 600; } QLabel#usagePeriod { color: %s; font-size: 12px; } "
            "QPushButton { background: %s; color: %s; border: 1px solid %s; border-radius: 11px; padding: 0 16px; } "
            "QPushButton:checked { background: %s; color: %s; border-color: %s; font-weight: 600; } "
            "QPushButton:hover { border-color: %s; } QPushButton:pressed { background: %s; }"
            % (
                c["bg"],
                c["text"],
                c["surface"],
                c["border"],
                c["muted"],
                c["blue"],
                c["muted"],
                c["field"],
                c["muted"],
                c["border"],
                c["pill"],
                c["blue"],
                c["blue"],
                c["blue"],
                c["hover"],
            )
            + common_qss(c)
        )
        if style == "glass":
            self.setStyleSheet(
                self.styleSheet()
                + "QDialog#codexUsageTrendWindow { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #f4f9ff,stop:0.5 #e4eefb,stop:1 #f4f9ff); } QFrame#usageSummary { background: rgba(255,255,255,205); }"
            )
        self.setWindowTitle(tr("title"))
        self.heading.setText(tr("title"))
        self.week_button.setText(tr("week"))
        self.five_button.setChecked(self.mode == "five")
        self.week_button.setChecked(self.mode == "daily")
        row = self.row()
        remaining = row.get("remainingPercent")
        label = "5h" if self.mode == "five" else tr("week")
        self.metric_caption.setText(label + " · " + ui_text("remaining"))
        self.remaining_metric.setText("%g%%" % remaining if isinstance(remaining, (int, float)) else "—")
        self.remaining_metric.setAccessibleName(self.metric_caption.text() + " " + self.remaining_metric.text())
        self.used_metric.setText("%s %s" % (tr("used"), "%g%%" % (100 - remaining) if isinstance(remaining, (int, float)) else "—"))
        self.description.setText(tr("five" if self.mode == "five" else "daily"))
        self.chart.mode = self.mode
        self.chart.colors = c
        self.chart.data = self.controller.history.five_hour(row) if self.mode == "five" else self.controller.history.daily(row)
        self.chart.setAccessibleName(tr("five" if self.mode == "five" else "daily"))
        self.chart.update()
        if self.mode == "five" and self.chart.data.get("start") is not None:
            period = "%s — %s" % (
                time.strftime("%H:%M", time.localtime(self.chart.data["start"])),
                time.strftime("%H:%M", time.localtime(self.chart.data["end"])),
            )
        elif self.mode == "daily":
            period = self.chart.data[0]["date"][5:].replace("-", "/") + " — " + self.chart.data[-1]["date"][5:].replace("-", "/")
        else:
            period = "—"
        self.period_label.setText(period)
        self.note.setText(tr("note5" if self.mode == "five" else "note7"))
        stamp = (self.controller.snapshot or {}).get("updatedAt")
        status = tr("updated").format(time=time.strftime("%m/%d %H:%M", time.localtime(stamp))) if stamp else tr("empty")
        if getattr(self.controller, "_read_failed", False):
            status += " · " + tr("unavailable")
        if not self.controller.history.saved:
            status += " · " + tr("save")
        self.status.setText(status)
        self.status.setToolTip(tr("local"))
        for widget in (self.description, self.note, self.status):
            widget.setStyleSheet("color: %s; font-size: 12px;" % c["muted"])

    def showEvent(self, event):
        self.refresh()
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)
