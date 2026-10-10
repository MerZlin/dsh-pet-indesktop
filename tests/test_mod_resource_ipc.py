import threading
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication


def test_release_matches_actual_source_even_when_character_ids_equal(tmp_path):
    from pet.mod_management import ModEntry
    from pet.mod_resource_ipc import release_resource_instances
    path = tmp_path / "content/characters/shenshen/versions/1.0.0"
    switched = []
    def instance(source, name):
        return SimpleNamespace(win=SimpleNamespace(lib=SimpleNamespace(_asset_dir=source)),
                               switch_character=lambda character_id, **kw: switched.append((name, character_id, kw)) or True)
    shell = SimpleNamespace(instances=[instance(path / "characters/shenshen/videos", "override"),
                                      instance(tmp_path / "builtin/videos", "builtin")])
    entry = ModEntry("replace.shenshen", "角色资源", "替换", "1.0.0", "", True, path, character_id="shenshen")
    assert release_resource_instances(shell, entry, fallback=("shenshen", tmp_path / "builtin/videos"))
    assert len(switched) == 1 and switched[0][0] == "override"
    assert switched[0][2]["force"] is True


def test_resource_bridge_real_qt_roundtrip_and_stale_lock(tmp_path):
    from pet.mod_resource_ipc import ResourceClient, ResourceServer
    app = QApplication.instance() or QApplication([])
    seen = []
    server = ResourceServer(tmp_path, lambda request: seen.append(request) or {"ok": True, "message": "ready"})
    result = []
    done = threading.Event()
    def run():
        result.append(ResourceClient(tmp_path, "slot-2").request_all({"command": "release", "id": "sample.role"}))
        done.set()
    worker = threading.Thread(target=run)
    worker.start()
    loop = QEventLoop()
    tick = QTimer()
    tick.setInterval(10)
    tick.timeout.connect(lambda: loop.quit() if done.is_set() else None)
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    timeout.start(15000)
    tick.start()
    loop.exec()
    tick.stop()
    timeout.stop()
    worker.join(1)
    assert done.is_set() and result[0][0]["ok"], (result, seen)
    assert seen == [{"command": "release", "id": "sample.role"}]
    server.close()
    assert ResourceClient(tmp_path).request_all({"command": "release", "id": "sample.role"}) == []
    app.processEvents()

def test_resource_bridge_cross_process_and_natural_exit(tmp_path):
    import os
    import subprocess
    import sys

    from pet.mod_resource_ipc import ResourceServer
    app = QApplication.instance() or QApplication([])
    server = ResourceServer(tmp_path, lambda request: {"ok": True, "message": request["id"]})
    child = subprocess.Popen([sys.executable, "-c", "from pathlib import Path; from PySide6.QtCore import QCoreApplication; from pet.mod_resource_ipc import ResourceClient; import sys,json; app=QCoreApplication([]); print(json.dumps(ResourceClient(Path(sys.argv[1])).request_all({'command':'release','id':'sample.role'})))", str(tmp_path)], cwd=Path(__file__).resolve().parents[1], env=dict(os.environ, QT_QPA_PLATFORM="offscreen"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    loop = QEventLoop()
    tick = QTimer()
    tick.setInterval(20)
    tick.timeout.connect(lambda: loop.quit() if child.poll() is not None else None)
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    timeout.start(30000)
    tick.start()
    try:
        loop.exec()
        out, err = child.communicate(timeout=5)
        assert child.returncode == 0 and 'sample.role' in out and 'true' in out, (out, err)
    finally:
        tick.stop()
        timeout.stop()
        server.close()
        app.processEvents()
