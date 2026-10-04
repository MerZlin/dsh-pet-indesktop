"""Own fixture event-loop peer; no desktop, credentials or feature execution."""

import json
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from pet.feature_install_state import FeatureInstallStateStore
from pet.feature_lifecycle import FeatureLifecycleEndpoint
from pet.feature_lifecycle_ipc import FeatureLifecycleServer
from pet.feature_version_lease import process_owner_identity
from pet.plugins.feature_host import FeatureDefinition, FeatureHost

app = QApplication([])
store = FeatureInstallStateStore(Path(sys.argv[1]).parents[1])
# Store constructor consumes the shared data root, not the feature root.
host = FeatureHost()
host.provide(FeatureDefinition("official.screen-understanding", (), lambda: None))
endpoint = FeatureLifecycleEndpoint(store, host)
server = FeatureLifecycleServer(endpoint)


class Quit(QObject):
    requested = Signal()


quit_signal = Quit()
quit_signal.requested.connect(app.quit)


def control():
    sys.stdin.readline()
    quit_signal.requested.emit()


threading.Thread(target=control, daemon=True).start()
print(json.dumps({"identity": process_owner_identity()}), flush=True)
app.exec()
server.close()
endpoint.close()
