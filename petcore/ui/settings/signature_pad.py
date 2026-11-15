"""关于页签名区：昵称 + 手写画布 + 唯一签名落盘。

签名是**本机**的个人纪念：固定保存到 ``<data_root>/community/signature.png``，
新签名原子覆盖旧文件；昵称与时间戳在 ``signature.json``。不新增配置键，也不
参与设置窗的保存/取消回滚（点「保存签名」即生效）。
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from ...community import store as community_store
from ...community.service import stamp_now
from .base import make_button

logger = logging.getLogger("petcore.ui.settings.signature")

CANVAS_WIDTH = 800
CANVAS_HEIGHT = 300
INK = "#0a84ff"
MAX_NAME = 40


class SignaturePad(QWidget):
    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("signaturePad")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(150)
        self.setMinimumWidth(280)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.setAccessibleName("手写签名画布")
        self.setAccessibleDescription("按住鼠标左键绘制签名；可撤销或清空后重画")
        self._strokes: list[list[QPointF]] = []
        self._current: list[QPointF] | None = None

    # ------------------------------------------------------------ 交互
    def has_content(self) -> bool:
        return bool(self._strokes or self._current)

    def undo(self) -> None:
        if self._strokes:
            self._strokes.pop()
            self.update()
            self.changed.emit()

    def clear(self) -> None:
        if not self._strokes and self._current is None:
            return
        self._strokes = []
        self._current = None
        self.update()
        self.changed.emit()

    def _normalized(self, event) -> QPointF:
        position = event.position()
        width = max(1, self.width())
        height = max(1, self.height())
        return QPointF(
            min(1.0, max(0.0, position.x() / width)),
            min(1.0, max(0.0, position.y() / height)),
        )

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self._current = [self._normalized(event)]
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if self._current is None:
            super().mouseMoveEvent(event)
            return
        self._current.append(self._normalized(event))
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if self._current is None or event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        self._current.append(self._normalized(event))
        if self._current:
            self._strokes.append(self._current)
        self._current = None
        self.update()
        self.changed.emit()
        event.accept()

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(INK))
        pen.setWidthF(2.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        for stroke in self._strokes:
            self._draw_stroke(painter, stroke, self.width(), self.height())
        if self._current:
            self._draw_stroke(painter, self._current, self.width(), self.height())
        painter.end()

    @staticmethod
    def _draw_stroke(painter: QPainter, stroke: list[QPointF], width: int, height: int) -> None:
        if not stroke:
            return
        if len(stroke) == 1:
            point = stroke[0]
            painter.drawPoint(QPointF(point.x() * width, point.y() * height))
            return
        path = QPainterPath(QPointF(stroke[0].x() * width, stroke[0].y() * height))
        for point in stroke[1:]:
            path.lineTo(QPointF(point.x() * width, point.y() * height))
        painter.drawPath(path)

    def to_png(self) -> bytes:
        image = QImage(CANVAS_WIDTH, CANVAS_HEIGHT, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(INK))
        pen.setWidthF(5.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        for stroke in self._strokes:
            self._draw_stroke(painter, stroke, CANVAS_WIDTH, CANVAS_HEIGHT)
        painter.end()
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        return bytes(buffer.data())


class SignaturePanel(QWidget):
    """签名区：打字昵称、手写、保存/撤销/清空、已保存预览。"""

    def __init__(self, host, parent=None) -> None:
        super().__init__(parent)
        self.host = host
        self.root = Path(str(getattr(host, "root", Path.cwd())))
        self.service = getattr(host, "community", None)
        self._build()
        self._load_saved()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(8)

        self.thanks_label = QLabel("谢谢每一位使用本项目的你。", self)
        self.thanks_label.setObjectName("settingLabel")
        self.thanks_label.setWordWrap(True)
        layout.addWidget(self.thanks_label)

        self.name_edit = QLineEdit(self)
        self.name_edit.setMaxLength(MAX_NAME)
        self.name_edit.setPlaceholderText("输入你的用户名（可只写名字）")
        self.name_edit.setAccessibleName("签名昵称")
        layout.addWidget(self.name_edit)

        self.pad = SignaturePad(self)
        self.pad.changed.connect(self._on_pad_changed)
        layout.addWidget(self.pad)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.undo_button = make_button("撤销", self)
        self.undo_button.clicked.connect(self.pad.undo)
        self.clear_button = make_button("清空", self)
        self.clear_button.clicked.connect(self._clear_pad)
        self.save_button = make_button("保存签名", self, primary=True)
        self.save_button.clicked.connect(self._save)
        buttons.addStretch(1)
        buttons.addWidget(self.undo_button)
        buttons.addWidget(self.clear_button)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)

        self.status_label = QLabel(
            "签名只保存在本机，不上传；全局只有一份，新签名会覆盖旧的。", self
        )
        self.status_label.setObjectName("settingHint")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.preview = QLabel(self)
        self.preview.setObjectName("signaturePreview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.preview.hide()
        layout.addWidget(self.preview)

        self.saved_label = QLabel("", self)
        self.saved_label.setObjectName("settingHint")
        self.saved_label.setWordWrap(True)
        layout.addWidget(self.saved_label)

        self.remove_saved_button = make_button("删除已保存的签名", self)
        self.remove_saved_button.clicked.connect(self._remove_saved)
        self.remove_saved_button.setEnabled(False)
        layout.addWidget(self.remove_saved_button, 0, Qt.AlignmentFlag.AlignLeft)

    # ------------------------------------------------------------ 状态
    def _on_pad_changed(self) -> None:
        if self.pad.has_content():
            self.status_label.setText("按「保存签名」落盘；再画一次会覆盖旧签名。")

    def _load_saved(self) -> None:
        meta = self._meta()
        if meta.get("name"):
            self.name_edit.setText(str(meta["name"]))
        path = self._signature_path()
        if path is not None and path.exists():
            self._show_preview(path)
            stamp = str(meta.get("updated_at") or "")
            self.saved_label.setText(
                f"已保存签名{f'（{stamp}）' if stamp else ''}；删除后不可恢复。"
            )
            self.remove_saved_button.setEnabled(True)
        elif meta.get("name"):
            self.saved_label.setText(f"已保存昵称：{meta['name']}（尚未手写签名）")
            self.remove_saved_button.setEnabled(True)

    def _meta(self) -> dict[str, str]:
        try:
            if self.service is not None:
                return self.service.signature_meta()
            return community_store.load_signature_meta(self.root)
        except Exception:  # noqa: BLE001 - 元数据坏了按未保存
            logger.debug("signature meta read failed", exc_info=True)
            return {}

    def _signature_path(self) -> Path | None:
        try:
            if self.service is not None:
                return self.service.signature_path()
            return community_store.signature_path(self.root)
        except Exception:  # noqa: BLE001
            return None

    def _show_preview(self, path: Path) -> None:
        image = QImage(str(path))
        if image.isNull():
            self.preview.hide()
            return
        pixmap = QPixmap.fromImage(image).scaledToWidth(
            260, Qt.TransformationMode.SmoothTransformation
        )
        self.preview.setPixmap(pixmap)
        self.preview.setFixedSize(pixmap.size())
        self.preview.show()

    def _save(self) -> None:
        name = self.name_edit.text().strip()[:MAX_NAME]
        if not self.pad.has_content() and not name:
            self.status_label.setText("先输入昵称，或用鼠标手写一个签名。")
            return
        try:
            if self.pad.has_content():
                png = self.pad.to_png()
                path = self._save_png(png, name)
                self._show_preview(path)
                self.saved_label.setText("已保存手写签名（新签名会覆盖旧的）。")
                self.remove_saved_button.setEnabled(True)
            else:
                self._save_name(name)
                self.saved_label.setText(f"已保存昵称：{name}（尚未手写签名）")
                self.remove_saved_button.setEnabled(True)
        except Exception as exc:  # noqa: BLE001 - 保存失败要可见，不抛给 Qt
            logger.warning("signature save failed: %s", type(exc).__name__)
            self.status_label.setText("保存失败：请检查数据目录是否可写。")
            return
        self.status_label.setText("已保存。")

    def _save_png(self, png: bytes, name: str) -> Path:
        if self.service is not None:
            return self.service.save_signature(png, name)
        path = community_store.save_signature_png(self.root, png)
        community_store.save_signature_meta(self.root, name, stamp_now())
        return path

    def _save_name(self, name: str) -> None:
        if self.service is not None:
            self.service.save_signature_name(name)
            return
        community_store.save_signature_meta(self.root, name, stamp_now())

    def _clear_pad(self) -> None:
        self.pad.clear()
        self.status_label.setText("画布已清空；已保存的签名不受影响。")

    def _remove_saved(self) -> None:
        answer = QMessageBox.question(
            self,
            "删除签名",
            "确定删除本机签名？删除后不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            if self.service is not None:
                self.service.clear_signature()
            else:
                community_store.clear_signature(self.root)
        except Exception:  # noqa: BLE001
            self.status_label.setText("删除失败：请检查数据目录权限。")
            return
        self.preview.clear()
        self.preview.hide()
        self.saved_label.setText("")
        self.remove_saved_button.setEnabled(False)
        self.status_label.setText("已删除本机签名。")
