# -*- coding: utf-8 -*-
"""“吃垃圾文件”拖放模拟功能测试：只记录，绝不真删/移动文件。"""
from __future__ import annotations

import json

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication, QWidget

from pet.file_eater import FileEaterDropHandler, format_bytes
from pet.window_optional_services import WindowFeatureGateMixin


class _FakePet(QWidget):
    def __init__(self, config_dir):
        super().__init__()
        self.cfg_dir = config_dir
        self.acts = ["待机", "吃Token", "吃西瓜"]
        self.anim_requests = []
        self.bubbles = []

    @property
    def cfg(self):
        class _Cfg:
            pass

        obj = _Cfg()
        obj.dir = self.cfg_dir
        return obj

    def request_link_anim(self, name):
        self.anim_requests.append(name)

    def show_bubble(self, text, duration_ms=3200, subtitle=None):
        self.bubbles.append((text, subtitle))


def _qapp():
    return QApplication.instance() or QApplication([])


def _make_pet(tmp_path):
    _qapp()
    return _FakePet(tmp_path)


class _FakePetWindow(QWidget, WindowFeatureGateMixin):
    def __init__(self, config_dir):
        super().__init__()
        self.acts = ["待机", "吃Token"]
        self.cfg = type("Cfg", (), {"dir": config_dir})()


def test_install_file_eater_is_idempotent_and_accepts_drops(tmp_path):
    _qapp()
    pet = _FakePetWindow(tmp_path)
    assert pet._file_eater is None

    first = pet.install_file_eater()
    assert pet.acceptDrops() is True
    assert first is pet._file_eater
    assert pet.install_file_eater() is first


def test_eat_paths_records_stats_plays_eat_animation_and_keeps_files(tmp_path):
    pet = _make_pet(tmp_path)
    first = tmp_path / "junk-a.log"
    first.write_text("0123456789", encoding="utf-8")
    second = tmp_path / "junk-b.bin"
    second.write_text("x" * 2048, encoding="utf-8")
    folder = tmp_path / "junk-dir"
    folder.mkdir()
    inner = folder / "inner.tmp"
    inner.write_text("y" * 512, encoding="utf-8")

    handler = FileEaterDropHandler(pet)
    result = handler.eat_paths([str(first), str(second), str(folder)])

    # 文件只是“被吃”，真实内容不能动。
    assert first.read_text(encoding="utf-8") == "0123456789"
    assert second.stat().st_size == 2048
    assert inner.read_text(encoding="utf-8") == "y" * 512

    # 动画优先选更贴近“吃数字垃圾”的吃Token。
    assert pet.anim_requests == ["吃Token"]
    assert len(pet.bubbles) == 1
    assert "文件没有删除或移动" in pet.bubbles[0][1]

    stats_path = tmp_path / "file_eaten_stats.json"
    assert stats_path.is_file()
    stats = json.loads(stats_path.read_text(encoding="utf-8"))
    assert stats["feed_count"] == 1
    assert stats["file_count"] == 2
    assert stats["folder_count"] == 1
    assert stats["item_count"] == 3
    assert stats["total_bytes"] == 10 + 2048 + 512

    # 再吃一个文件，累计统计继续增加。
    third = tmp_path / "junk-c.tmp"
    third.write_text("z" * 100, encoding="utf-8")
    handler.eat_paths([str(third)])
    stats = json.loads(stats_path.read_text(encoding="utf-8"))
    assert stats["feed_count"] == 2
    assert stats["file_count"] == 3
    assert stats["total_bytes"] == 10 + 2048 + 512 + 100
    assert result["stats"]["total_bytes"] == 10 + 2048 + 512


def test_drag_and_drop_events_are_accepted_and_fed(tmp_path):
    pet = _make_pet(tmp_path)
    target = tmp_path / "drop.txt"
    target.write_text("drop", encoding="utf-8")

    handler = FileEaterDropHandler(pet)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(target))])

    drag = QDragEnterEvent(
        QPoint(0, 0),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    assert handler.eventFilter(pet, drag) is True
    assert drag.isAccepted()

    pet.anim_requests.clear()
    drop = QDropEvent(
        QPointF(5, 5),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    assert handler.eventFilter(pet, drop) is True
    assert drop.isAccepted()
    assert pet.anim_requests == ["吃Token"]
    assert (tmp_path / "file_eaten_stats.json").is_file()


def test_format_bytes_has_human_readable_units():
    assert format_bytes(0) == "0 B"
    assert format_bytes(1023) == "1023 B"
    assert format_bytes(2048) == "2.0 KB"
    assert format_bytes(3 * 1024 * 1024) == "3.0 MB"
    assert format_bytes(4 * 1024 * 1024 * 1024) == "4.0 GB"
