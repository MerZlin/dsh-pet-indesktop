"""Public author worker facade: real child and Qt event loop, no network."""
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QEventLoop
from PySide6.QtWidgets import QApplication

from pet.config import Config
from pet.feature_host_bindings import bind_local_context
from pet.workers.launch import WorkerLaunch


def pump(app, predicate):
    deadline = time.monotonic() + 30
    while not predicate() and time.monotonic() < deadline:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
    assert predicate()


def test_v1_worker_requires_verified_launch_and_current_execution_authority(tmp_path):
    from pet.mod_api.v1 import WorkerClient
    app = QApplication.instance() or QApplication([])
    client = WorkerClient(bind_local_context(Config(base=tmp_path), 'demo.echo'))
    assert not client.start()
    client.close()
    app.processEvents()


def test_v1_echo_request_cancel_late_result_and_natural_exit(tmp_path):
    from pet.mod_api.v1 import WorkerClient
    app = QApplication.instance() or QApplication([])
    context = bind_local_context(Config(base=tmp_path), 'demo.echo')
    released = []
    script = "from pet.mod_api.worker_v1 import serve; serve('demo.echo', lambda op, args, cancel: {'echo': args.get('text', '')})"
    context = replace(context, worker_launch_factory=lambda: WorkerLaunch(sys.executable, ('-c', script), str(Path.cwd()), dict(os.environ), lambda: released.append(True)), execution_authorized=lambda: True)
    client = WorkerClient(context)
    responses = []
    client.response.connect(lambda rid, payload: responses.append((rid, payload)))
    assert client.start()
    pump(app, lambda: client.is_ready)
    canceled = client.request('echo', {'text': 'discard'})
    client.cancel(canceled)
    accepted = client.request('echo', {'text': 'hello'})
    pump(app, lambda: bool(responses))
    assert responses == [(accepted, {'echo': 'hello', 'operation': 'echo'})]
    client.stop()
    pump(app, lambda: client.supervisor.process is None)
    assert released == [True]
    client.close()


def test_public_definitions_and_sample_factories_are_headless_probe_safe(tmp_path):
    import subprocess
    script = r"""
import importlib.abc, importlib.util, sys
from pathlib import Path
class NoQt(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('PySide6', 'shiboken6')):
            raise ImportError('headless probe has no GUI')
sys.meta_path.insert(0, NoQt())
from pet.mod_api.v1 import FeatureDefinition
for index, example in enumerate(('hello-local', 'echo-worker')):
    path = Path('examples/mods') / example / 'host'
    spec = importlib.util.spec_from_file_location('sample' + str(index), path / '__init__.py', submodule_search_locations=[str(path)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    factory = importlib.import_module(spec.name + '.factory')
    definition = factory.create_host()
    assert isinstance(definition, FeatureDefinition)
    assert definition.mount_contract == 'mod/v1'
assert not any(name.startswith('PySide6') for name in sys.modules)
"""
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_public_worker_probe_is_headless_and_never_enters_business_or_lease(tmp_path):
    import subprocess

    from pet.workers.protocol import build_message, encode_message
    script = """
import sys, types
sys.modules['_dsh_probe_native'] = types.SimpleNamespace(sandbox_enforced=lambda: True)
sys.argv = ['echo', '--feature-package-probe']
from pet.mod_api.worker_v1 import serve
code = serve('demo.echo-worker', lambda *_: (_ for _ in ()).throw(AssertionError('business invoked')))
assert 'pet.feature_version_lease' not in sys.modules
assert not any(name.startswith('PySide6') for name in sys.modules)
raise SystemExit(code)
"""
    result = subprocess.run([sys.executable, '-c', script], input=encode_message(build_message('demo.echo-worker', 'shutdown')), capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    from pet.feature_package_probe import validate_worker_transcript
    assert validate_worker_transcript(result.stdout, graceful_returncode=0, worker_id='demo.echo-worker')
    assert b'"probe":true' in result.stdout


def test_worker_probe_rejects_launch_without_native_isolation():
    import subprocess
    result = subprocess.run([sys.executable, '-c', "import sys; sys.argv=['echo','--feature-package-probe']; from pet.mod_api.worker_v1 import serve; raise SystemExit(serve('demo.echo-worker', lambda *_: {}))"], input=b'', capture_output=True, timeout=20)
    assert result.returncode == 77
    assert result.stdout == b''


def test_nonofficial_worker_claims_its_own_installed_lease(tmp_path):
    """Real child bootstrap must not search the official screen owner's ledger."""
    import hashlib
    from types import SimpleNamespace

    from pet.feature_install_state import FeatureInstallStateStore, StateChange
    from pet.feature_version_lease import FeatureVersionLeaseCoordinator, FeatureVersionSelection
    from pet.workers.supervisor import WorkerSupervisor
    from tests.test_feature_worker_handoff import _launch_with_reservation
    app = QApplication.instance() or QApplication([])
    owner = 'demo.nonofficial-worker'
    raw = b'nonofficial worker installed identity'
    digest = hashlib.sha256(raw).hexdigest()
    store = FeatureInstallStateStore(tmp_path, feature_id=owner)
    store.commit(StateChange({'1.0.0': digest}, active='1.0.0', enabled=True), expected_revision=0, operation_id='install')
    root = store.root / 'versions/1.0.0'
    root.mkdir(parents=True)
    descriptor = SimpleNamespace(id=owner, version='1.0.0', trust_status='trusted_official', raw_manifest=raw, root=root, execution_kind='host-worker')
    coordinator = FeatureVersionLeaseCoordinator(store)
    selection = FeatureVersionSelection(owner, '1.0.0', 1, digest, descriptor)
    reservation = coordinator.reserve_worker(selection)
    script = tmp_path / 'worker.py'
    script.write_text("from pet.mod_api.worker_v1 import serve\nraise SystemExit(serve('demo.nonofficial-worker', lambda op, args, cancel: {'echo': args['text']}))", encoding='utf8')
    launch = _launch_with_reservation(tmp_path, script, reservation)
    launch.environment = {**launch.environment, 'DSH_PET_FEATURE_LEASE_OWNER': owner}
    supervisor = WorkerSupervisor(owner, launch_factory=lambda: launch, max_restarts=0)
    try:
        assert supervisor.start()
        pump(app, lambda: supervisor.state in {supervisor.READY, supervisor.FAULT})
        assert supervisor.state == supervisor.READY
        assert launch.handoff_confirmed
        assert coordinator.inspect_occupancy('1.0.0', 1).status == 'occupied'
    finally:
        supervisor.stop()
        pump(app, lambda: supervisor.process is None)
    assert coordinator.inspect_occupancy('1.0.0', 1).status == 'free'


def test_echo_sample_surfaces_start_failure_and_can_be_closed(tmp_path):
    import importlib.util
    path = Path('examples/mods/echo-worker/host/factory.py')
    spec = importlib.util.spec_from_file_location('test_echo_author_factory', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = QApplication.instance() or QApplication([])
    runtime = module.Runtime(bind_local_context(Config(base=tmp_path), module.OWNER))
    try:
        runtime.echo()
        app.processEvents()
        assert len(runtime.dialogs) == 1
        assert '无法启动' in runtime.dialogs[0].text()
        assert 'worker_not_authorized' in runtime.dialogs[0].text()
    finally:
        runtime.close()
        app.processEvents()
