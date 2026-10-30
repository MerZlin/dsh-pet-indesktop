from __future__ import annotations

import random
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, Qt
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .. import paths
from ..media.images import (
    is_animated,
    load_pixmap,
    logical_size,
    sanitize_dpr,
    screen_dpr,
)
from .animated_image import AnimatedImage
from .placement import place_beside_pet

SUPPORTED_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"})

#: 没配置标题时的彩蛋标题（菜单行与气泡共用一份）。
DEFAULT_EASTER_EGG_TITLE = "厉害了我的鲸"

#: 彩蛋图片的等比缩放上限（宽与高都不超过它；按 ``easter_egg_image_scale`` 放大）。
MAX_IMAGE_SIDE = 520

#: 缩放后的边长钳位（太小看不清标题，太大超出小屏）。
POPUP_SIDE_MIN = 280
POPUP_SIDE_MAX = 1200

#: 连续打开时的错位步长与循环数（第 6 张回到原位，避免一直往右下跑）。
CASCADE_STEP = 24
CASCADE_CYCLE = 5

_HINT_TEXT = "点击任意处关闭"

_POPUP_QSS = """
QWidget#easterEggPopup {
    background: rgba(255, 255, 255, 246);
    border: 1px solid rgba(120, 150, 180, 190);
    border-radius: 16px;
}
QLabel#easterEggTitle { color: #243040; font-size: 15px; font-weight: 600; }
QLabel#easterEggHint { color: #6b7c8f; font-size: 12px; }
"""


def clamp_popup_side(image_scale: float = 1.0) -> int:
    """把 ``easter_egg_image_scale / 100`` 换算成弹窗图片边长（280..1200）。"""
    try:
        value = float(image_scale)
    except (TypeError, ValueError):
        value = 1.0
    if value != value:  # NaN
        value = 1.0
    value = max(0.5, min(3.0, value))
    return int(max(POPUP_SIDE_MIN, min(POPUP_SIDE_MAX, round(MAX_IMAGE_SIDE * value))))


def _image_scale(config) -> float:
    """读取 ``easter_egg_image_scale`` 百分比并换算成系数（缺省 1.0）。"""
    try:
        percent = float(config.get("easter_egg_image_scale") or 100)
    except (TypeError, ValueError):
        percent = 100.0
    return percent / 100.0


def load_scaled_pixmap(
    image_path: Path,
    max_side: int = MAX_IMAGE_SIDE,
    dpr: float = 1.0,
) -> QPixmap:
    """按 ``max_side`` 逻辑像素装载图片，并带上屏幕的 ``devicePixelRatio``。

    主分支彩蛋弹窗用原始大图让 painter 缩放到设备分辨率；等价做法是按 DPR
    解码到物理像素，QLabel 1:1 贴图，避免「先缩到逻辑像素再被放大」的发虚。
    """
    pixmap = load_pixmap(image_path, max(1, int(max_side)), dpr)
    if pixmap is None or pixmap.isNull():
        raise ValueError(f"无法读取彩蛋图片: {image_path}")
    return pixmap


class EasterEggPopup(QWidget):
    """无边框彩蛋弹窗：点任意处或按 Esc 关闭。"""

    def __init__(
        self,
        image_path: Path,
        title: str = "",
        *,
        hint: str = _HINT_TEXT,
        offset: int = 0,
        image_scale: float = 1.0,
        dpr: float | None = None,
        on_top: bool = True,
    ):
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            if on_top
            else Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        )
        super().__init__(None, flags)
        self.setObjectName("easterEggPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet(_POPUP_QSS)

        self.image_path = Path(image_path)
        self._offset = max(0, int(offset))
        self._offset_applied = False
        self.image_scale = max(0.5, min(3.0, float(image_scale)))
        ratio = sanitize_dpr(dpr if dpr is not None else screen_dpr())
        self.device_pixel_ratio = ratio
        max_side = clamp_popup_side(self.image_scale)
        self.animated_image: AnimatedImage | None = None
        pixmap = None
        if is_animated(self.image_path):
            # 动图：逻辑尺寸只读图片头，帧由 AnimatedImage 按 DPR 出。
            self.animated_image = AnimatedImage(
                self.image_path, max_side, self, dpr=ratio
            )
            width, height = self.animated_image.logical_size()
        else:
            pixmap = load_scaled_pixmap(self.image_path, max_side, ratio)
            width, height = logical_size(pixmap)

        self.setWindowTitle(str(title or ""))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(8)

        self.image_label = QLabel(self)
        self.image_label.setObjectName("easterEggImage")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setFixedSize(width, height)
        self.image_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        if pixmap is not None:
            self.image_label.setPixmap(pixmap)
        else:
            # 动图占位：同一布局位置，静态图的 label 让位（隐藏的项不占版面）。
            self.image_label.hide()
        layout.addWidget(self.image_label, 0, Qt.AlignmentFlag.AlignHCenter)
        if self.animated_image is not None:
            layout.addWidget(self.animated_image, 0, Qt.AlignmentFlag.AlignHCenter)

        title_text = str(title or "").strip()
        self.title_label = QLabel(title_text, self)
        self.title_label.setObjectName("easterEggTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.title_label.setFixedHeight(24)
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        if title_text:
            layout.addWidget(self.title_label)
        else:
            self.title_label.hide()

        self.hint_label = QLabel(str(hint or _HINT_TEXT).strip() or _HINT_TEXT, self)
        self.hint_label.setObjectName("easterEggHint")
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setFixedHeight(18)
        self.hint_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.hint_label)

        extra = 24 if title_text else 0
        extra += 8 if title_text else 0
        self.setFixedSize(width + 24, height + 18 + 8 + 10 + 12 + extra)

    def show(self) -> None:
        if not self._offset_applied:
            self._offset_applied = True
            if self._offset:
                self.move(self.pos() + QPoint(self._offset, self._offset))
        super().show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        self.close()
        event.accept()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        # 弹窗是 WA_DeleteOnClose：销毁前停播并断开 QMovie，别让动图继续解码。
        if self.animated_image is not None:
            self.animated_image.close()
        super().closeEvent(event)


class EasterEggController(QObject):
    """按配置打开彩蛋图片；同一次运行里连续打开按 24px 阶梯错位。"""

    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self._popups: list[EasterEggPopup] = []
        self._open_count = 0
        self._rng = random.Random()

    def _config(self):
        return getattr(self._window, "config", None)

    def asset_root(self) -> Path:
        return paths.legacy_asset_root()

    def image_dir(self) -> Path:
        config = self._config()
        raw = ""
        if config is not None:
            try:
                raw = str(config.get("easter_egg_image_dir") or "").strip()
            except Exception:
                raw = ""
        if raw:
            return Path(raw).expanduser()
        return self.asset_root() / "big_blue_fat_fish"

    def images(self) -> list[Path]:
        directory = self.image_dir()
        if not directory.is_dir():
            return []
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            return []
        readable: list[Path] = []
        for path in entries:
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
                continue
            if QImageReader(str(path)).canRead():
                readable.append(path)
        return readable

    def selected_image(
        self, images: list[Path] | None = None, rng: random.Random | None = None
    ) -> Path | None:
        candidates = list(images if images is not None else self.images())
        if not candidates:
            return None
        config = self._config()
        raw = "" if config is None else str(config.get("easter_egg_avatar") or "").strip()
        if raw:
            avatar = Path(raw).expanduser()
            if avatar.is_file() and avatar.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES:
                try:
                    if QImageReader(str(avatar)).canRead():
                        return avatar
                except OSError:
                    pass
        picker = rng if rng is not None else self._rng
        return picker.choice(candidates)

    def live_popups(self) -> list[EasterEggPopup]:
        self._prune()
        return list(self._popups)

    def _prune(self) -> None:
        alive: list[EasterEggPopup] = []
        for popup in self._popups:
            try:
                popup.objectName()
            except RuntimeError:
                continue
            alive.append(popup)
        self._popups = alive

    def _say(self, text: str) -> None:
        say = getattr(self._window, "say", None)
        if not callable(say):
            return
        try:
            say(text, force=True)
        except TypeError:
            try:
                say(text)
            except Exception:
                pass
        except Exception:
            pass

    def show(self) -> bool:
        config = self._config()
        enabled = (
            True if config is None else bool(config.get("easter_egg_enabled", True))
        )
        if not enabled:
            self._say("彩蛋已关闭（设置 → 互动与物理）")
            return False
        images = self.images()
        if not images:
            self._say("彩蛋图片目录里没有可读取的图片")
            return False
        image_path = self.selected_image(images)
        if image_path is None:
            self._say("彩蛋图片无法读取")
            return False
        title = ""
        hint = _HINT_TEXT
        image_scale = 1.0
        if config is not None:
            title = str(config.get("easter_egg_title") or "")
            hint = str(config.get("easter_egg_hint") or _HINT_TEXT)
            image_scale = _image_scale(config)
        try:
            popup = EasterEggPopup(
                image_path,
                title,
                hint=hint,
                offset=(self._open_count % CASCADE_CYCLE) * CASCADE_STEP,
                image_scale=image_scale,
                dpr=screen_dpr(getattr(self, "_window", None)),
                on_top=bool(getattr(self._window, "always_on_top", True)),
            )
        except (OSError, ValueError):
            self._say("彩蛋图片无法读取")
            return False
        self._open_count += 1
        self._prune()
        self._popups.append(popup)
        anchor = self._window if isinstance(self._window, QWidget) else None
        place_beside_pet(popup, anchor)
        popup.show()
        return True

    def close_all(self) -> None:
        for popup in self.live_popups():
            try:
                popup.close()
            except RuntimeError:
                pass
        self._popups.clear()
