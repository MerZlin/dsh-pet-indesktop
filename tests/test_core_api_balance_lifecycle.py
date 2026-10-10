"""Shared balance snapshots and queued late-result fences; fake OS/network only."""

import threading
from types import SimpleNamespace

from PySide6.QtCore import QEventLoop
from PySide6.QtWidgets import QApplication, QWidget

from pet.api_config import ApiService, CoreApiConfiguration
from pet.balance_config import resolve_balance_request
from pet.config import Config
from tests.screen_fakes import MemoryVault


def provision(tmp_path, monkeypatch):
    from pet import feature_distribution

    monkeypatch.setattr(feature_distribution, "BUILTIN_AI", False)
    backend = MemoryVault()
    monkeypatch.setattr("pet.credentials.secure_backend", lambda: backend)
    cfg = Config(base=tmp_path)
    cfg.save()
    api = CoreApiConfiguration(cfg, backend=backend)
    api.save_service(
        ApiService("shared", "余额", "https://api.deepseek.com", balance_protocol="deepseek"),
        secret="GENERATED",
        grants=[("core.balance", "balance.query", "")],
        expected_revision=api.revision(),
    )
    return cfg, api


def test_balance_inflight_revocation_drops_payload_and_cache(tmp_path, monkeypatch):
    from pet.app import AppShell

    cfg, api = provision(tmp_path, monkeypatch)
    request = resolve_balance_request(cfg)
    entered, release = threading.Event(), threading.Event()

    def fetch(*args, **kwargs):
        entered.set()
        assert release.wait(20)
        return {"total": "1"}

    monkeypatch.setattr("pet.app.balance_mod.fetch_balance", fetch)
    saved, delivered = [], []
    owner = SimpleNamespace(_balance_cache=None, _balance_busy=True, _write_balance_file_cache=lambda *args: saved.append(args))
    bridge = SimpleNamespace(done=SimpleNamespace(emit=lambda *args: delivered.append(args)))
    thread = threading.Thread(
        target=AppShell._balance_worker,
        args=(owner, bridge, request.base_url, request.api_key, request.verify_ssl, "identity"),
        kwargs={"authorized": request.authorization_check},
    )
    try:
        thread.start()
        assert entered.wait(20)
        api.revoke("core.balance", "balance.query", expected_revision=api.revision())
    finally:
        release.set()
        thread.join(20)
    assert not thread.is_alive()
    assert saved == delivered == []
    assert owner._balance_cache is None and not owner._balance_busy


def test_balance_already_queued_result_is_discarded_after_revoke(tmp_path, monkeypatch):
    from pet.app import _BalanceBridge

    cfg, api = provision(tmp_path, monkeypatch)
    request = resolve_balance_request(cfg)
    app = QApplication.instance() or QApplication([])
    win = QWidget()
    shown = []
    monkeypatch.setattr("pet.app._show_balance_payload", lambda *args: shown.append(args))
    bridge = _BalanceBridge(win, authorized=request.authorization_check)
    producer = threading.Thread(target=lambda: bridge.done.emit(True, {"text": "late", "info": {}}))
    try:
        producer.start()
        producer.join(20)
        api.revoke("core.balance", "balance.query", expected_revision=api.revision())
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        assert shown == []
    finally:
        win.close()
        bridge.deleteLater()
        app.processEvents()
