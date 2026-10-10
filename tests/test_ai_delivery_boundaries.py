"""AI DLC source ownership and fail-closed legacy compatibility contracts."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ai_implementation_lives_in_the_feature_not_legacy_core_modules():
    for name in ("models", "providers", "service", "session_store", "widgets", "legacy_widgets", "ai_settings_page"):
        legacy = importlib.import_module("pet.chat." + name)
        owned = importlib.import_module("features.ai_chat.host.chat." + name)
        assert legacy is owned
        assert Path(owned.__file__).resolve().is_relative_to(ROOT / "features/ai_chat/host")
    for name in ("quick_chat", "island_chat", "file_interpret", "settings_file_interpret"):
        assert importlib.import_module("pet." + name) is importlib.import_module("features.ai_chat.host." + name)


def test_ai_factory_contract_is_lazy_without_qt_or_business_imports(tmp_path):
    script = """
import json, sys
from features.ai_chat.host.factory import create_host
from pet.plugins.feature_host import FeatureDefinition
host = create_host()
assert isinstance(host, FeatureDefinition)
assert host.owner == 'official.ai-chat' and host.worker_launch_factory is None
assert host.allow_in_process and callable(host.settings_factory) and callable(host.runtime_factory)
assert not any(name.startswith('PySide6') or name.endswith('.providers') or name.endswith('.service') for name in sys.modules)
print(json.dumps([item.id for item in host.menus]))
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert set(json.loads(result.stdout)) == {"chat", "quick_chat"}


def test_small_core_cannot_reimport_source_ai_via_legacy_shims(tmp_path):
    script = """
from pathlib import Path
from pet import feature_distribution
feature_distribution.BUILTIN_AI = False
from pet.config import Config
cfg = Config(base=Path(__import__('sys').argv[1]))
import importlib, sys
for name in ('pet.chat', 'pet.chat.service', 'pet.quick_chat', 'pet.island_chat', 'pet.file_interpret', 'pet.settings_file_interpret'):
    try:
        importlib.import_module(name)
    except ModuleNotFoundError:
        pass
    else:
        raise AssertionError('source AI fallback: ' + name)
assert not any(name.startswith('features.ai_chat') for name in sys.modules)
from pet import balance
assert cfg.get('character')
"""
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_small_core_settings_without_ai_imports_no_business_code(tmp_path):
    script = """
import sys
from pathlib import Path
from pet import feature_distribution
feature_distribution.BUILTIN_AI = False
feature_distribution.BUILTIN_SCREEN = False
from PySide6.QtWidgets import QApplication
from pet.config import Config
from pet.plugins.feature_host import FeatureHost
from pet.modern_settings_dialog import ModernSettingsDialog
from pet.feature_management import close_official_management
app = QApplication([])
host = FeatureHost()
dialog = ModernSettingsDialog(Config(base=Path(sys.argv[1])), include_ai=False, feature_host=host)
assert dialog.ai_page is None
assert not any(name.startswith('features.ai_chat') for name in sys.modules)
assert not dialog.feature_management_widgets
assert set(dialog.mod_controller.managers) == {'official.ai-chat', 'official.screen-understanding'}
dialog.close()
close_official_management(host)
app.processEvents()
"""
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], cwd=ROOT, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr


def test_small_core_signed_ai_settings_mounts_without_legacy_import(tmp_path):
    script = """
import sys
from pathlib import Path
from pet import feature_distribution
feature_distribution.BUILTIN_AI = False
feature_distribution.BUILTIN_SCREEN = False
from PySide6.QtWidgets import QApplication
from pet.config import Config
from pet.plugins.feature_host import FeatureHost
from features.ai_chat.host.factory import create_host
from pet.feature_management import close_official_management
from pet.modern_settings_dialog import ModernSettingsDialog
app = QApplication([])
host = FeatureHost()
host.provide(create_host(), enabled=False)
dialog = ModernSettingsDialog(Config(base=Path(sys.argv[1])), include_ai=False, feature_host=host)
component = dialog._feature_components['official.ai-chat']
assert dialog.ai_page is component.page
assert dialog.include_ai
assert not any(name == 'pet.chat' or name.startswith('pet.chat.') for name in sys.modules)
component.page.prompt.setPlainText('generated dirty draft')
assert dialog._feature_draft_dirty('official.ai-chat')
component.confirm_save()
host.remove('official.ai-chat')
assert dialog.ai_page is None
assert 'official.ai-chat' not in dialog._feature_components
dialog.close()
close_official_management(host)
app.processEvents()
"""
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], cwd=ROOT, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr
