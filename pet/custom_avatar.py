"""Native fish window icon and managed still/GIF island avatars."""

from __future__ import annotations
import hashlib, shutil
from pathlib import Path
from PySide6.QtCore import QObject, QEvent, QSize, Qt
from PySide6.QtGui import QIcon, QImageReader, QMovie
from PySide6.QtWidgets import QFileDialog, QPushButton, QMessageBox

SENTINEL = "__import_avatar__"
COPY = {
    "import": ("匯入圖片／GIF…", "Import image / GIF…", "导入图片／GIF…"),
    "custom": ("自訂頭像：", "Custom avatar: ", "自定义头像："),
    "label": ("自訂靈動島頭像", "Custom island avatar", "自定义灵动岛头像"),
    "hint": (
        "匯入圖片或 GIF，圓形顯示；預覽後保存，GIF 保留動畫。",
        "Import an image or GIF for the round avatar. Preview, then save; GIFs animate.",
        "导入图片或 GIF，圆形显示；预览后保存，GIF 保留动画。",
    ),
    "invalid": ("無法讀取這個圖片或 GIF。", "This image or GIF could not be read.", "无法读取此图片或 GIF。"),
}


def text(key):
    from .language_ui import language

    return COPY[key][{"zh_TW": 0, "en": 1, "zh_CN": 2}.get(language(), 0)]


def icon_file():
    return Path(__file__).resolve().parents[1] / "assets/icon.ico"


def add_controls(dialog):
    if hasattr(dialog, "avatar_import_button"):
        return
    path = icon_file()
    if path.exists():
        dialog.setWindowIcon(QIcon(str(path)))
    selector = dialog.island_icon_select
    existing = str(dialog.config.get("dynamic_island", {}).get("icon", "auto"))
    if existing.startswith("img:"):
        selector.addItem(text("custom") + Path(existing[4:]).name, existing)
        selector.setCurrentData(existing)
    selector.addItem(text("import"), SENTINEL)
    dialog._avatar_last = selector.currentData()
    dialog._avatar_preview_files = []
    button = QPushButton(text("import"), dialog)
    button.setObjectName("dshImportIslandAvatar")
    dialog.avatar_import_button = button
    from .settings_widgets import SettingRow
    from .ytmusic import append_row

    row = dialog.findChild(SettingRow, "settingRow_dynamic_island_icon_value")
    if row is not None:
        append_row(dialog, row.parentWidget(), SettingRow("dynamic_island_custom_avatar", text("label"), text("hint"), button))

    def browse():
        selected, _ = QFileDialog.getOpenFileName(dialog, text("label"), "", "Images / GIF (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;All files (*)")
        if selected:
            choose_file(dialog, selected)
        else:
            selector.setCurrentData(dialog._avatar_last)

    def changed(_index):
        if selector.currentData() == SENTINEL:
            browse()
        else:
            dialog._avatar_last = selector.currentData()

    button.clicked.connect(lambda _checked=False: browse())
    selector.currentIndexChanged.connect(changed)
    dialog.ui_language_select.currentIndexChanged.connect(lambda _index: translate_controls(dialog))
    translate_controls(dialog)


def translate_controls(dialog):
    button = getattr(dialog, "avatar_import_button", None)
    if button is None:
        return
    button.setText(text("import"))
    selector = dialog.island_icon_select
    for i, (label, data) in enumerate(selector._items):
        if data == SENTINEL:
            selector._items[i] = (text("import"), data)
        elif str(data).startswith("img:"):
            selector._items[i] = (text("custom") + Path(str(data)[4:]).name, data)
    selector.setText(selector.currentText())
    selector.update()
    from .settings_widgets import SettingRow

    row = dialog.findChild(SettingRow, "settingRow_dynamic_island_custom_avatar")
    if row is not None:
        row.label.setText(text("label"))
        row.hint_label.setText(text("hint"))
        for w in (row.label, row.hint_label):
            w.setProperty("_dsh_source_text", None)
            w.setProperty("_dsh_rendered_text", None)


def choose_file(dialog, path):
    source = Path(path)
    reader = QImageReader(str(source))
    if not reader.canRead() or reader.size().isEmpty():
        QMessageBox.warning(dialog, text("label"), text("invalid"))
        dialog.island_icon_select.setCurrentData(dialog._avatar_last)
        return False
    fmt = bytes(reader.format()).decode("ascii", "ignore").lower()
    suffix = ".gif" if fmt == "gif" else "." + fmt
    owner = dialog._dsh_preview_writer.owner
    directory = Path(dialog.config.path).parent / "preview-assets" / owner
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (hashlib.sha256(source.read_bytes()).hexdigest()[:24] + suffix)
    shutil.copyfile(source, target)
    dialog._avatar_preview_files.append(target)
    value = "img:" + str(target)
    selector = dialog.island_icon_select
    if not any(data == value for _, data in selector._items):
        selector.addItem(text("custom") + source.name, value)
    selector.setCurrentData(value)
    dialog._avatar_last = value
    dialog._dsh_preview_writer.changed()
    return True


def prepare_save(dialog):
    value = str(dialog.island_icon_select.currentData())
    if not value.startswith("img:"):
        return
    source = Path(value[4:])
    if source not in getattr(dialog, "_avatar_preview_files", []):
        return
    directory = Path(dialog.config.path).parent / "island-avatars"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / source.name
    if not target.exists():
        shutil.copyfile(source, target)
    persistent = "img:" + str(target)
    dialog.island_icon_select.addItem(text("custom") + source.name, persistent)
    dialog.island_icon_select.setCurrentData(persistent)


def cleanup(dialog):
    for path in getattr(dialog, "_avatar_preview_files", []):
        try:
            path.unlink()
        except OSError:
            pass
    dialog._avatar_preview_files = []


class AnimatedAvatar(QObject):
    def __init__(self, island, path):
        super().__init__(island)
        self.island = island
        self.path = path
        self.movie = QMovie(path, parent=self)
        reader = QImageReader(path)
        size = reader.size()
        if not size.isEmpty():
            self.movie.setScaledSize(size.scaled(QSize(96, 96), Qt.AspectRatioMode.KeepAspectRatio))
        self.movie.frameChanged.connect(self.frame)
        self.movie.finished.connect(self.movie.start)
        island.installEventFilter(self)
        self.movie.start()

    def frame(self, _number):
        island = self.island
        island._icon_pixmap_cache = None
        island._icon_pixmap_token += 1
        island._content_pixmap = None
        island._content_cache_key = None
        island.update()
        if not island.isVisible():
            self.movie.setPaused(True)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Show:
            self.movie.setPaused(False)
        elif event.type() == QEvent.Type.Hide:
            self.movie.setPaused(True)
        return False

    def stop(self):
        self.movie.stop()
        self.island.removeEventFilter(self)
        self.deleteLater()


def pixmap(island, original):
    spec = island._icon_spec()
    path = spec[4:] if spec.startswith("img:") else ""
    current = getattr(island, "_dsh_animated_avatar", None)
    if current is not None and current.path != path:
        current.stop()
        island._dsh_animated_avatar = None
        current = None
    if path and Path(path).suffix.lower() == ".gif":
        if current is None:
            current = AnimatedAvatar(island, path)
            island._dsh_animated_avatar = current
        if current.movie.isValid():
            frame = current.movie.currentPixmap()
            if not frame.isNull():
                return frame
    return original(island)
