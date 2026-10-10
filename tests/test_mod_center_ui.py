from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QLineEdit

from pet.mod_management import ModEntry, ModOutcome


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


class Backend(QObject):
    changed = Signal()
    busy_changed = Signal(bool)
    result = Signal(str, object)
    batch_finished = Signal(object)
    busy = False

    def __init__(self):
        super().__init__()
        self.calls = []
        self.rows = [ModEntry("third.chat", "功能扩展", "离线例子", "1.0.0", "<b>纯文本</b>", False, Path("feature")),
                     ModEntry("character.demo", "角色资源", "角色例子", "1.0.0", "说明", False, Path("character")),
                     ModEntry("resource:bundled:shenshen", "角色资源", "深深", "1.0.0", "", True, Path("builtin"), source="bundled")]

    def entries(self): return self.rows
    def perform(self, action, keys): self.calls.append((action, keys))
    def import_source(self, path, expected=None): self.calls.append(("import", path, expected))


def test_list_filters_clear_selection_and_builtin_cannot_be_deleted(app):
    from pet.mod_center_ui import ModCenterWidget
    backend = Backend()
    widget = ModCenterWidget(backend)
    widget.show()
    widget.select_all.setChecked(True)
    assert len(widget.selection.selected) == 3
    widget.filters["角色资源"].click()
    assert not widget.selection.selected
    assert len(widget.rows) == 2
    builtin = widget.rows["角色资源:resource:bundled:shenshen"]
    assert not builtin.delete_action.isEnabled()
    widget.search.setText("离线")
    assert not widget.rows
    widget.close()


def test_enable_is_immediate_and_refresh_keeps_unrelated_draft(app):
    from pet.mod_center_ui import ModCenterWidget
    backend = Backend()
    widget = ModCenterWidget(backend)
    unrelated = QLineEdit("未保存草稿")
    row = widget.rows["功能扩展:third.chat"]
    row.toggle.click()
    assert backend.calls == [("enable", ["功能扩展:third.chat"])]
    backend.result.emit("功能扩展:third.chat", ModOutcome(True, "已启用"))
    backend.changed.emit()
    assert unrelated.text() == "未保存草稿"
    assert widget.rows["功能扩展:third.chat"].message.text() == "已启用"
    widget.close()


@pytest.mark.parametrize("width", [440, 820])
def test_long_plain_description_never_forces_horizontal_scroll(app, width):
    from pet.mod_center_ui import ModCenterWidget
    widget = ModCenterWidget(Backend())
    widget.resize(width, 850)
    widget.show()
    app.processEvents()
    assert widget.minimumSizeHint().width() <= 440
    row = widget.rows["功能扩展:third.chat"]
    assert row.description.text() == "<b>纯文本</b>"
    assert row.description.textFormat().name == "PlainText"
    widget.close()


def test_unchanged_catalog_refresh_preserves_row_widgets_and_selection(app):
    from pet.mod_center_ui import ModCenterWidget
    backend = Backend()
    widget = ModCenterWidget(backend)
    row = widget.rows["功能扩展:third.chat"]
    row.check.setChecked(True)
    backend.changed.emit()
    assert widget.rows["功能扩展:third.chat"] is row
    assert row.check.isChecked()
    widget.close()


def test_single_operation_result_survives_busy_refresh(app):
    from pet.mod_center_ui import ModCenterWidget
    backend = Backend()
    widget = ModCenterWidget(backend)
    backend.busy = True
    backend.busy_changed.emit(True)
    result = ModOutcome(False, "请退出后重试")
    backend.result.emit("功能扩展:third.chat", result)
    backend.changed.emit()
    backend.busy = False
    backend.busy_changed.emit(False)
    backend.changed.emit()
    backend.batch_finished.emit((("功能扩展:third.chat", result),))
    assert widget.summary.text() == result.message
    widget.close()
