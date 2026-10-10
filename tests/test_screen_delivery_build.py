"""Independent validation builds cannot accidentally ship the screen source."""

from pathlib import Path

import pytest

from scripts.build_screen_delivery import (
    assemble_package,
    core_excludes,
    core_module_inventory,
    host_dependencies,
    prepare_core,
    read_pe_subsystem,
    require_gui_pe_subsystem,
    sanitized_build_environment,
    verify_worker_inputs,
)

ROOT = Path(__file__).resolve().parents[1]


def _minimal_pe(subsystem: int) -> bytes:
    data = bytearray(0x200)
    data[:2] = b"MZ"
    data[0x3C:0x40] = (0x80).to_bytes(4, "little")
    data[0x80:0x84] = b"PE\x00\x00"
    coff_start = 0x84
    data[coff_start + 16 : coff_start + 18] = (0xE0).to_bytes(2, "little")
    optional_start = coff_start + 20
    data[optional_start : optional_start + 2] = (0x20B).to_bytes(2, "little")
    data[optional_start + 68 : optional_start + 70] = subsystem.to_bytes(2, "little")
    return bytes(data)


def test_core_pe_subsystem_gate_accepts_gui_and_rejects_console(tmp_path):
    gui = tmp_path / "gui.exe"
    console = tmp_path / "console.exe"
    gui.write_bytes(_minimal_pe(2))
    console.write_bytes(_minimal_pe(3))

    assert read_pe_subsystem(gui) == 2
    require_gui_pe_subsystem(gui)
    assert read_pe_subsystem(console) == 3
    with pytest.raises(ValueError, match="subsystem must be GUI"):
        require_gui_pe_subsystem(console)


def test_core_snapshot_has_no_screen_and_preserves_repository(tmp_path):
    before = (ROOT / "pet/feature_distribution.py").read_bytes()
    output = tmp_path / "core"
    prepare_core(ROOT, output, chat=False, public_key="ab" * 32)
    assert not (output / "source/features").exists()
    assert not (output / "source/pet/chat").exists()
    assert not (output / "source/pet/vision.py").exists()
    assert not (output / "source/pet/proactive.py").exists()
    assert not (output / "source/pet/screen_understanding").exists()
    assert not (output / "source/pet/workers/proactive_screen_worker.py").exists()
    assert (output / "source/pet/credentials.py").exists()
    distribution = (output / "source/pet/feature_distribution.py").read_text()
    assert "BUILTIN_AI = False" in distribution
    assert "BUILTIN_SCREEN = False" in distribution
    assert (ROOT / "pet/feature_distribution.py").read_bytes() == before
    assert "VALIDATION_ONLY = True" in (output / "source/validation_config.py").read_text()
    with pytest.raises(FileExistsError):
        prepare_core(ROOT, output, chat=False, public_key="ab" * 32)


def test_no_chat_retains_credential_runtime():
    excludes = core_excludes(chat=False)
    assert "pet.chat" in excludes
    assert "keyring" not in excludes
    assert "cryptography" not in excludes
    assert "pet.vision" in excludes
    assert "features" in excludes


def test_actual_inventory_not_a_source_string_gate():
    with pytest.raises(ValueError, match="screen"):
        core_module_inventory(["pet.app", "features.screen_understanding.host.factory"], chat=True)
    with pytest.raises(ValueError, match="chat"):
        core_module_inventory(["pet.app", "pet.chat.service"], chat=False)
    with pytest.raises(ValueError, match="missing"):
        core_module_inventory(["pet.app"], chat=True)


def test_host_dependencies_do_not_collect_host_or_worker():
    modules = host_dependencies(ROOT)
    assert "pet.plugins.feature_host" in modules
    assert "pet.feature_ports" in modules
    assert not any(name.startswith(("features.", "pet.vision", "pet.chat")) for name in modules)


def test_build_path_filters_unknown_external_icu_without_personal_paths(tmp_path):
    foreign = tmp_path / "tool"
    foreign.mkdir()
    (foreign / "icuuc.dll").write_bytes(b"incompatible")
    ordinary = tmp_path / "ordinary"
    ordinary.mkdir()
    environment, removed = sanitized_build_environment(
        {"PATH": str(foreign) + ";" + str(ordinary), "HTTPS_PROXY": "proxy", "PYTHONPATH": "source"}, path_separator=";", trusted_directories=()
    )
    assert environment["PATH"] == str(ordinary)
    assert environment["HTTPS_PROXY"] == "proxy"
    assert "PYTHONPATH" not in environment
    assert removed == [str(foreign)]


def test_package_signs_exact_inventory_with_no_private_key_or_python_worker(tmp_path):
    import sys

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from pet import __version__
    from pet.plugins.feature_packages import FeaturePackageVerifier

    worker = tmp_path / "worker"
    worker.mkdir()
    (worker / "proactive-screen-worker.exe").write_bytes(b"fixture-executable")
    key = Ed25519PrivateKey.generate()
    package = assemble_package(ROOT, tmp_path / "package", worker, key)
    assert not (package / "worker/vision.py").exists()
    assert (package / "host/factory.py").exists()
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    verifier = FeaturePackageVerifier(
        core_version=__version__,
        api_version="1",
        platform=sys.platform,
        allowed_capabilities={"screen.capture"},
        trust_anchors={"validation-only": key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)},
    )
    descriptor = verifier.verify(package)
    assert descriptor.id == "official.screen-understanding"
    assert descriptor.version == "1.0.3"
    import json

    assert json.loads((package / "manifest.json").read_bytes())["core_requires"] == ">=4.2.4,<6.0.0"
    assert not any("private" in p.name for p in package.rglob("*"))


def test_worker_evidence_required_before_reusing_artifact(tmp_path):
    with pytest.raises((ValueError, FileNotFoundError)):
        verify_worker_inputs(ROOT, tmp_path)


def test_real_worker_reuse_requires_native_probe_evidence(tmp_path):
    import hashlib
    import json

    from scripts.build_screen_worker import prepare_build

    output = tmp_path / "real-worker"
    prepare_build(ROOT, output, synthetic=False)
    bundle = output / "dist/proactive-screen-worker"
    bundle.mkdir(parents=True)
    executable = b"frozen-worker"
    native = b"native-isolation-leaf"
    (bundle / "proactive-screen-worker.exe").write_bytes(executable)
    (bundle / "_internal").mkdir()
    (bundle / "_internal/_dsh_probe_native.pyd").write_bytes(native)
    files = {
        "proactive-screen-worker.exe": {"size": len(executable), "sha256": hashlib.sha256(executable).hexdigest()},
        "_internal/_dsh_probe_native.pyd": {"size": len(native), "sha256": hashlib.sha256(native).hexdigest()},
    }
    (output / "evidence/artifact.json").write_text(json.dumps({"files": files}), encoding="utf-8")

    with pytest.raises(ValueError, match="headless probe evidence"):
        verify_worker_inputs(ROOT, output, synthetic=False)

    (output / "evidence/headless-input.json").write_text(
        json.dumps(
            {
                "bootloader_sha256": "a" * 64,
                "native_leaf_sha256": hashlib.sha256(native).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    assert verify_worker_inputs(ROOT, output, synthetic=False) == bundle


def test_production_worker_evidence_can_be_reused_after_source_verification(tmp_path):
    import hashlib
    import json

    from scripts.build_feature_release import prepare_worker

    owned = tmp_path / "owned"
    owned.mkdir()
    output = owned / "worker"
    prepare_worker(ROOT, output, owned_root=owned)
    bundle = output / "dist/proactive-screen-worker"
    bundle.mkdir(parents=True)
    executable = b"production-worker-fixture"
    native = b"production-native-isolation-leaf"
    (bundle / "proactive-screen-worker.exe").write_bytes(executable)
    (bundle / "_internal").mkdir()
    (bundle / "_internal/_dsh_probe_native.pyd").write_bytes(native)
    files = {
        "proactive-screen-worker.exe": {"size": len(executable), "sha256": hashlib.sha256(executable).hexdigest()},
        "_internal/_dsh_probe_native.pyd": {"size": len(native), "sha256": hashlib.sha256(native).hexdigest()},
    }
    (output / "evidence/artifact.json").write_text(json.dumps({"files": files}), encoding="utf-8")
    (output / "evidence/headless-input.json").write_text(json.dumps({"native_leaf_sha256": hashlib.sha256(native).hexdigest()}), encoding="utf-8")

    assert verify_worker_inputs(ROOT, output, synthetic=False) == bundle


def test_core_inventory_checks_native_extensions_outside_pyz(tmp_path):
    from scripts.build_screen_delivery import CORE_REQUIRED, native_module_inventory

    internal = tmp_path / "_internal"
    (internal / "PySide6").mkdir(parents=True)
    (internal / "PySide6/QtWidgets.pyd").write_bytes(b"native-fixture")
    native = native_module_inventory(internal)
    pure = CORE_REQUIRED - {"PySide6.QtWidgets"}
    assert native == ["PySide6.QtWidgets"]
    assert core_module_inventory(pure, chat=False, native_modules=native) == sorted(pure)
    with pytest.raises(ValueError, match="missing"):
        core_module_inventory(pure, chat=False, native_modules=())
    with pytest.raises(ValueError, match="screen"):
        core_module_inventory(pure, chat=False, native_modules=native + ["pet.vision"])


@pytest.mark.parametrize("shared", [False, True])
def test_no_chat_core_shutdown_cleans_up_without_optional_module(tmp_path, shared):
    """Real Qt shutdown must not depend on the omitted chat package."""
    import os
    import subprocess
    import sys

    code = r"""
import importlib.abc
import os
import sys
from pathlib import Path
base = Path(sys.argv[1])
for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
    directory = base / name.lower()
    directory.mkdir(parents=True)
    os.environ[name] = str(directory)
class NoChat(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "pet.chat" or fullname.startswith("pet.chat."):
            raise ModuleNotFoundError("chat absent from validation Core", name=fullname)
sys.meta_path.insert(0, NoChat())
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from pet.app import AppShell
from pet.config import Config
from pet.plugins.feature_host import FeatureHost
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
config = Config(base=base / "config", instance_id="isolated-no-chat")
config.set("experimental_single_process_spawn", sys.argv[2] == "True")
shell = AppShell(app, config, enable_chat=False, feature_host=FeatureHost())
errors = []
def record_error(kind, error, tb):
    errors.append(str(error))
    sys.__excepthook__(kind, error, tb)
sys.excepthook = record_error
# Exercise the real aboutToQuit callback without starting optional services or media.
app.aboutToQuit.connect(shell._on_about_to_quit)
shell._install_config_watcher()
reload_timer = shell._config_reload_timer
assert reload_timer is not None
reload_timer.start(60000)
QTimer.singleShot(0, app.quit)
assert app.exec() == 0
assert not errors, errors
assert not reload_timer.isActive()
assert shell._config_watcher is None
assert not any(n == "pet.chat" or n.startswith("pet.chat.") for n in sys.modules)
print("NO_CHAT_SHUTDOWN_OK")
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path), str(shared)],
        cwd=ROOT,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "NO_CHAT_SHUTDOWN_OK" in result.stdout


def test_validation_handles_synchronous_launch_rejection_in_event_loop(tmp_path):
    """A rejected launch must exit the real Qt loop, not hang the artifact gate."""
    import os
    import subprocess
    import sys

    program = r"""
import runpy
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from pet.workers.supervisor import WorkerSupervisor
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
namespace = runpy.run_path('packaging/phase4a_validation_entry.py')
errors = []
def rejected():
    raise ValueError('expected validation-only rejection')
def failed(reason):
    errors.append(reason)
    QTimer.singleShot(0, app.quit)
supervisor = WorkerSupervisor('proactive-screen', launch_factory=rejected, max_restarts=0)
supervisor.failed.connect(failed)
QTimer.singleShot(0, lambda: namespace['start_worker'](supervisor, failed))
QTimer.singleShot(10000, lambda: failed('timeout'))
app.exec()
assert errors and 'timeout' not in errors, errors
assert supervisor.process is None
assert 'worker_start_rejected' in errors
print('VALIDATION_LAUNCH_REJECTION_OK')
"""
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=ROOT,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "VALIDATION_LAUNCH_REJECTION_OK" in result.stdout


def test_validation_observes_accepted_wire_heartbeat_and_drains_worker(tmp_path):
    """The host consumes heartbeats; a smoke must not wait on its event signal."""
    import os
    import subprocess
    import sys

    program = r"""
import runpy
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from pet.workers.supervisor import WorkerSupervisor
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
namespace = runpy.run_path('packaging/phase4a_validation_entry.py')
heartbeats, errors = [], []
def heartbeat():
    heartbeats.append(True)
    supervisor.stop()
    QTimer.singleShot(0, app.quit)
def failed(reason):
    errors.append(reason)
    QTimer.singleShot(0, app.quit)
supervisor = namespace['probe_supervisor'](heartbeat, max_restarts=0)
supervisor.failed.connect(failed)
QTimer.singleShot(0, lambda: namespace['start_worker'](supervisor, failed))
QTimer.singleShot(15000, lambda: failed('timeout'))
app.exec()
assert WorkerSupervisor.finish_app_shutdown()
assert not errors, errors
assert heartbeats and supervisor.process is None
print('VALIDATION_HEARTBEAT_AND_DRAIN_OK')
"""
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=ROOT,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=25,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "VALIDATION_HEARTBEAT_AND_DRAIN_OK" in result.stdout
    assert "already deleted" not in result.stderr


def test_synthetic_probe_requires_signed_marker_and_loopback_fixture():
    import base64
    import io
    import json
    import runpy
    from types import SimpleNamespace
    from urllib.request import Request, urlopen

    from PIL import Image

    helpers = runpy.run_path(str(ROOT / "packaging/phase4a_validation_entry.py"))
    authorize = helpers["require_synthetic_package"]
    with pytest.raises(ValueError, match="synthetic"):
        authorize(SimpleNamespace(files={}, trust_status="trusted_official"))
    with pytest.raises(ValueError, match="synthetic"):
        authorize(SimpleNamespace(files={"resources/VALIDATION-SYNTHETIC.txt": None}, trust_status="unsigned"))
    authorize(SimpleNamespace(files={"resources/VALIDATION-SYNTHETIC.txt": None}, trust_status="trusted_official"))
    image = io.BytesIO()
    Image.new("RGB", (100, 80), "navy").save(image, "JPEG")
    payload = {
        "messages": [{"content": [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(image.getvalue()).decode()}}]}]
    }
    with helpers["synthetic_http_service"]() as (provider, stats):
        assert provider["base_url"].startswith("http://127.0.0.1:")
        response = urlopen(
            Request(
                provider["base_url"] + "/v1/chat/completions",
                data=json.dumps(payload).encode(),
                headers={"Authorization": "Bearer " + provider["api_key"], "Content-Type": "application/json"},
            ),
            timeout=5,
        )
        assert json.load(response)["choices"][0]["message"]["content"] == "synthetic analysis"
        assert stats == {"requests": 1, "errors": []}


def test_fresh_synthetic_worker_lease_first_entry_matches_closed_inputs(tmp_path):
    import json

    from scripts.build_screen_worker import prepare_build

    output = tmp_path / "fresh-worker"
    prepare_build(ROOT, output, synthetic=True)
    bundle = output / "dist/proactive-screen-worker"
    bundle.mkdir(parents=True)
    data = b"generated-nonexecuted-artifact"
    (bundle / "proactive-screen-worker.exe").write_bytes(data)
    (output / "evidence/artifact.json").write_text(
        json.dumps({"files": {"proactive-screen-worker.exe": {"size": len(data), "sha256": __import__("hashlib").sha256(data).hexdigest()}}}), encoding="utf-8"
    )
    assert verify_worker_inputs(ROOT, output, synthetic=True) == bundle


def test_manual_core_build_embeds_existing_whale_icon(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from scripts import build_screen_delivery as build

    out = tmp_path / "build"
    monkeypatch.setattr(build, "DATA_ROOTS", ())
    monkeypatch.setattr(build.subprocess, "run", lambda *args, **kw: SimpleNamespace(returncode=0))
    monkeypatch.setattr(build, "verify_core_bundle", lambda *args, **kw: out / "generated.exe")
    build.build_core(ROOT, out, chat=False)
    icon = out / "source/assets/icon.ico"
    assert icon.read_bytes() == (ROOT / "assets/icon.ico").read_bytes()
    assert f"icon={str(icon)!r}" in (out / "validation.spec").read_text()
