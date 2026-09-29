"""Owner lifecycle exercises real menus, settings widgets and event loops."""

import sys

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QPlainTextEdit

from pet.config import Config
from pet.modern_settings_dialog import ModernSettingsDialog
from pet.official_features import SCREEN_OWNER, default_feature_host
from pet.plugins.feature_host import FeatureHost
from pet.settings_widgets import SettingRow


@pytest.fixture
def context(tmp_path):
    app = QApplication.instance() or QApplication([])
    cfg = Config(tmp_path)
    host = default_feature_host()
    yield app, cfg, host


def test_absent_feature_has_no_settings_factory_or_entries(context):
    app, cfg, _ = context
    host = FeatureHost()
    dlg = ModernSettingsDialog(cfg, include_ai=False, feature_host=host)
    try:
        assert dlg.screen_settings_page is None
        assert dlg.findChild(SettingRow, "settingRow_screen_model") is None
        assert not {"look_screen", "proactive_screen"} & dlg.menu_available_actions
        before = cfg.get("proactive_screen").copy()
        dlg._save()
        assert cfg.get("proactive_screen") == before
    finally:
        dlg.close()
        dlg.deleteLater()
        QCoreApplication.sendPostedEvents(dlg, QEvent.Type.DeferredDelete)


def test_disable_retains_settings_draft_and_revoke_cleans_search(context, monkeypatch):
    app, cfg, host = context
    dlg = ModernSettingsDialog(cfg, include_ai=False, feature_host=host)
    page = dlg.screen_settings_page
    page.model.setText("unsaved-model")
    raw = dlg.menu_layout_editor.value()
    host.disable(SCREEN_OWNER)
    assert dlg.screen_settings_page is page
    assert page.model.text() == "unsaved-model"
    assert dlg.menu_layout_editor.value() == raw
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    assert not host.remove(SCREEN_OWNER)
    assert dlg.screen_settings_page is page
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    assert host.remove(SCREEN_OWNER)
    assert dlg.screen_settings_page is None
    assert not [r for r in dlg._search_rows if r.objectName().startswith(("settingRow_screen_", "settingRow_pro_"))]
    assert dlg.findChild(SettingRow, "settingRow_screen_model") is None
    dlg._save()
    dlg.close()
    dlg.deleteLater()
    QCoreApplication.sendPostedEvents(dlg, QEvent.Type.DeferredDelete)


def test_fault_retains_only_nonsecret_draft_and_removes_legacy_link(context):
    app, cfg, host = context
    dlg = ModernSettingsDialog(cfg, include_ai=True, feature_host=host)
    page = dlg.screen_settings_page
    page.model.setText("retain-this-model")
    page.key_edit.setText("TEST-NEVER-PERSIST")
    host.fault(SCREEN_OWNER, "factory_failed")
    assert "retain-this-model" in str(dlg._feature_drafts)
    assert "TEST-NEVER-PERSIST" not in str(dlg._feature_drafts)
    assert page.key_edit.text() == ""
    draft_view = dlg.findChild(QPlainTextEdit, "screenContributionDraft")
    assert draft_view is not None and draft_view.isReadOnly()
    assert "retain-this-model" in draft_view.toPlainText()
    assert "TEST-NEVER-PERSIST" not in draft_view.toPlainText()
    assert dlg.findChild(SettingRow, "settingRow_vision_migration") is None
    assert not host.registry.list(owner=SCREEN_OWNER)
    dlg.close()
    dlg.deleteLater()
    QCoreApplication.sendPostedEvents(dlg, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("template", ["modern", "legacy"])
def test_open_menu_lease_cannot_execute_after_disable_or_reregister(context, template):
    from pet.context_menus.shared import add_proactive_menu
    from pet.feature_bindings import build_screen_menu

    app, cfg, host = context
    calls = []

    class Target:
        feature_host = host

        def on_look_screen(self):
            calls.append("look")

        def toggle_proactive_enabled(self, on):
            calls.append(on)

        def set_proactive_option(self, *args):
            pass

        proactive_watcher = None
        _look_busy = False

    target = Target()
    target.cfg = cfg
    menu = QMenu()
    if template == "modern":
        action = build_screen_menu(menu, target, "look_screen")
    else:
        if sys.platform != "win32":
            pytest.skip("legacy automatic entry is Windows-only")
        sub = add_proactive_menu(menu, target)
        action = sub.actions()[0]
    action.trigger()
    assert calls
    calls.clear()
    host.disable(SCREEN_OWNER)
    host.enable(SCREEN_OWNER)
    action.trigger()
    assert not calls
    menu.deleteLater()
    QCoreApplication.sendPostedEvents(menu, QEvent.Type.DeferredDelete)


@pytest.fixture
def windows(context, tmp_path):
    from pet.library import MovieLibrary
    from pet.window import PetWindow

    app, cfg, host = context
    cfg.data.update({"agent_link": {}, "proactive_screen": {"enabled": False}, "click_sound_enabled": False, "music_lyric_enabled": False})
    cfg.data["chat"]["enabled"] = False
    created = []

    def make():
        videos = tmp_path / f"videos-{len(created)}"
        videos.mkdir()
        from PIL import Image

        Image.new("RGBA", (32, 32), "red").save(
            videos / "idle.gif", save_all=True, append_images=[Image.new("RGBA", (32, 32), (i, 0, 255, 255)) for i in range(100)], duration=100, loop=0
        )
        win = PetWindow(MovieLibrary(asset_dir=videos, prewarm_enabled=False), cfg, feature_host=host)
        created.append(win)
        return win

    yield make, host
    from shiboken6 import isValid

    for win in created:
        if isValid(win):
            win.close()
            win.deleteLater()
            QCoreApplication.sendPostedEvents(win, QEvent.Type.DeferredDelete)


def test_window_direct_entry_and_late_result_are_revoked(windows):
    make, host = windows
    win = make()
    bubbles = []
    win.show_bubble = lambda *a, **k: bubbles.append(a)
    callback = win._screen_result_callback()
    host.disable(SCREEN_OWNER)
    win.look_at_screen()
    before = win.cfg.get("proactive_screen").copy()
    win.toggle_proactive_enabled(True)
    win.set_proactive_option("dry_run", True)
    assert win.cfg.get("proactive_screen") == before
    assert not bubbles
    assert win.proactive_watcher is None
    host.enable(SCREEN_OWNER)
    callback("old result", "[看看屏幕]", False)
    assert not bubbles
    win.look_at_screen()
    assert bubbles and "配置" in bubbles[-1][0]


def test_window_scope_detaches_without_revoking_other_window(windows):
    from pet.feature_bindings import bind_screen_window

    make, host = windows
    first, second = make(), make()
    _, scope1 = bind_screen_window(first)
    _, scope2 = bind_screen_window(second)
    assert host.menu(SCREEN_OWNER, scope1, "look_screen")
    first.close()
    assert host.menu(SCREEN_OWNER, scope1, "look_screen") is None
    assert host.menu(SCREEN_OWNER, scope2, "look_screen").active


def test_whole_feature_disable_stops_and_guards_real_watcher(context):
    from types import SimpleNamespace

    from pet.feature_bindings import bind_screen_window
    from pet.proactive import ProactiveScreenWatcher

    app, cfg, host = context
    target = SimpleNamespace(feature_host=host, cfg=cfg, _look_busy=False)
    watcher = ProactiveScreenWatcher(target, cfg)
    target.proactive_watcher = watcher
    bind_screen_window(target)
    watcher._timer.start()
    generation = watcher._generation
    host.disable(SCREEN_OWNER)
    assert not watcher.is_running()
    assert watcher._generation > generation
    assert not watcher.request_manual_look(None, "", "", lambda *a: pytest.fail("disabled callback"))
    watcher.resume()
    assert not watcher.is_running()


def test_strategy_toggle_draft_and_apply_baseline(context):
    app, cfg, host = context
    dlg = ModernSettingsDialog(cfg, include_ai=False, feature_host=host)
    try:
        component = dlg._screen_component
        if component.strategy is None:
            return  # Automatic screen capability is Windows-only.
        assert not component.dirty()
        toggle = dlg.pro_dryrun_check
        toggle.setChecked(not toggle.isChecked())
        assert component.dirty()
        assert dlg._write_config()
        assert not component.dirty()
    finally:
        dlg.close()
        dlg.deleteLater()
        QCoreApplication.sendPostedEvents(dlg, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("exit_method", ["accept", "reject", "close"])
def test_settings_exit_releases_owner_leases_and_timers(context, exit_method):
    from PySide6.QtCore import QTimer

    app, cfg, host = context
    dlg = ModernSettingsDialog(cfg, include_ai=False, feature_host=host)
    component = dlg._screen_component
    timers = component.findChildren(QTimer)
    scope = dlg._feature_scope
    assert host.registry.list(owner=SCREEN_OWNER, scope=scope)
    getattr(dlg, exit_method)()
    assert not host.registry.list(owner=SCREEN_OWNER, scope=scope)
    assert component._disposed
    assert not any(timer.isActive() for timer in timers)
    assert not host._listeners
    assert not host._prepare.get(SCREEN_OWNER)
    dlg.deleteLater()
    QCoreApplication.sendPostedEvents(dlg, QEvent.Type.DeferredDelete)


def test_window_destroy_without_close_revokes_its_actions(windows):
    make, host = windows
    win = make()
    scope = f"window:{id(win)}"
    assert host.registry.list(owner=SCREEN_OWNER, scope=scope)
    win.deleteLater()
    QCoreApplication.sendPostedEvents(win, QEvent.Type.DeferredDelete)
    assert not host.registry.list(owner=SCREEN_OWNER, scope=scope)


@pytest.mark.parametrize("provided", [False, True])
def test_standalone_contributions_use_real_settings_entry_without_runtime(tmp_path, provided):
    import os
    import subprocess
    from pathlib import Path

    script = r"""
import importlib.abc
import sys
from pathlib import Path

provided = sys.argv[2] == "True"
blocked = {"pet.app", "pet.vision", "pet.proactive", "pet.workers.supervisor"}
if not provided:
    blocked.update({"pet.screen_understanding.settings", "pet.screen_understanding.strategy_settings",
                    "pet.screen_understanding.contribution_settings"})
class NoRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname in blocked:
            raise AssertionError("settings loaded runtime or absent UI: " + fullname)
sys.meta_path.insert(0, NoRuntime())
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from pet.config import Config
from pet.plugins.feature_host import FeatureHost
from pet.official_features import default_feature_host, SCREEN_OWNER
import pet.modern_settings_dialog as mod
import pet.__main__ as entry
import pet.credentials as credentials
from tests.screen_fakes import MemoryVault
backend = MemoryVault()
credentials.secure_backend = lambda: backend
host = default_feature_host() if provided else FeatureHost()
cfg = Config(Path(sys.argv[1]))
cfg.data["chat"]["enabled"] = False
cfg.save()
BaseDialog = mod.ModernSettingsDialog
class ProbeDialog(BaseDialog):
    def __init__(self, config, parent=None, **kw):
        kw["include_ai"] = False
        super().__init__(config, parent, feature_host=host, **kw)
    def show(self):
        super().show()
        QTimer.singleShot(0, self.verify)
    def verify(self):
        try:
            assert self.standalone
            assert (self.screen_settings_page is not None) == provided
            self._search_settings("screen_model")
            if provided:
                self.screen_settings_page.model.setText("unsaved-independent-model")
                assert host.registry.list(owner=SCREEN_OWNER)
            self._save()
            assert not backend.items  # outer Apply cannot save credential drafts
            self.accept()
            assert not host.registry.list(owner=SCREEN_OWNER)
            assert not blocked.intersection(sys.modules)
            print("STANDALONE_CONTRIBUTIONS_OK", flush=True)
            QApplication.instance().exit(0)
        except BaseException:
            import traceback
            traceback.print_exc()
            QApplication.instance().exit(1)
mod.ModernSettingsDialog = ProbeDialog
raise SystemExit(entry._run_settings(cfg))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), str(provided)],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "STANDALONE_CONTRIBUTIONS_OK" in result.stdout


def test_shared_runtime_lease_survives_one_window_but_ends_on_process_stop(context):
    from types import SimpleNamespace

    from pet.feature_bindings import bind_screen_window
    from pet.multi_window_shared import SharedProactiveWatcher

    app, cfg, host = context
    proxy = SimpleNamespace(feature_host=host)
    watcher = SharedProactiveWatcher(proxy, cfg)
    first = SimpleNamespace(feature_host=host)
    second = SimpleNamespace(feature_host=host)
    _, first_scope = bind_screen_window(first)
    _, second_scope = bind_screen_window(second)
    adapter = watcher._worker_adapter
    try:
        host.detach(SCREEN_OWNER, first_scope)
        assert len(host._executions) == 1
        assert watcher._worker_adapter is adapter
        assert host.menu(SCREEN_OWNER, second_scope, "look_screen").active
        host.disable(SCREEN_OWNER)
        assert not watcher.is_running()
        watcher.stop_all()
        watcher.stop_all()
        assert not host._executions
    finally:
        watcher.dispose()
        QCoreApplication.sendPostedEvents(watcher._bridge, QEvent.Type.DeferredDelete)
        host.detach(SCREEN_OWNER, second_scope)


def test_remove_save_failure_retains_page_and_other_settings(context, monkeypatch):
    app, cfg, host = context
    dlg = ModernSettingsDialog(cfg, include_ai=False, feature_host=host)
    component = dlg._screen_component
    component.vision.profile_id.setText("manual")
    component.vision.url.setText("https://vision.example.test")
    component.vision.model.setText("unsaved-model")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Save)
    # The storage failure is the boundary; keep the real confirmation and dirty logic.
    attempts = []

    def storage_failure(*args, **kwargs):
        attempts.append(args)
        raise OSError("storage unavailable")

    monkeypatch.setattr(component.vision.service, "save_profile", storage_failure)
    assert not host.remove(SCREEN_OWNER)
    assert len(attempts) == 1
    assert host.enabled(SCREEN_OWNER)
    assert dlg._screen_component is component
    assert component.vision.model.text() == "unsaved-model"
    dlg.close()
    dlg.deleteLater()
    QCoreApplication.sendPostedEvents(dlg, QEvent.Type.DeferredDelete)


def test_fault_does_not_save_and_cancels_delayed_queries(context, monkeypatch):
    from PySide6.QtCore import QTimer

    app, cfg, host = context
    dlg = ModernSettingsDialog(cfg, include_ai=False, feature_host=host)
    component = dlg._screen_component
    component.vision.model.setText("nonsecret draft")
    component.vision.key_edit.setText("DO-NOT-SAVE")
    timer = QTimer(component)
    timer.timeout.connect(lambda: pytest.fail("disposed task ran"))
    timer.start(1000)
    monkeypatch.setattr(component, "confirm_save", lambda: pytest.fail("fault component must not save"))
    host.fault(SCREEN_OWNER, "invalid_component")
    assert not timer.isActive()
    assert not component.dirty()
    assert "DO-NOT-SAVE" not in str(cfg.data)
    assert not any(row.objectName().startswith("settingRow_screen_") for row in dlg._search_rows)
    assert not any(row.objectName().startswith("settingRow_pro_") for row in dlg._search_rows)
    dlg.close()
    dlg.deleteLater()
    QCoreApplication.sendPostedEvents(dlg, QEvent.Type.DeferredDelete)
