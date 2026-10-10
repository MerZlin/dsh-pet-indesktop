"""One local catalog over the existing feature and resource services."""
from __future__ import annotations

import json
import logging
import threading
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from .feature_package_transactions import OperationResult
from .feature_state_io import StateError, safe_path
from .mod_catalog import publish_catalog_change, refresh_feature_catalog
from .mod_management import ModEntry, ModOutcome, ResourceMods

_LOG = logging.getLogger(__name__)


class ModCenterController(QObject):
    changed = Signal()
    busy_changed = Signal(bool)
    result = Signal(str, object)
    batch_finished = Signal(object)
    _finished = Signal(object)

    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        self.config, self.host, self.managers = dialog.config, dialog.feature_host, dialog.feature_managers
        from .mod_resource_ipc import ResourceClient
        self.resource_client = ResourceClient(self.config.dir, str(self.config.instance_id or ""))
        self.resources = ResourceMods(self.config.dir, prepare_removal=self.resource_client.prepare_removal)
        self.busy = False
        self._queue = deque()
        self._results = []
        self._current = None
        self._connected = set()
        self._closed = False
        self._finished.connect(self._resource_finished, Qt.ConnectionType.QueuedConnection)
        self.connect_managers()

    def connect_managers(self):
        for owner, manager in self.managers.items():
            if owner not in self._connected:
                self._connected.add(owner)
                manager.result_ready.connect(lambda result, owner=owner: self._feature_finished(owner, result))
                manager.state_changed.connect(lambda *_: self.changed.emit())

    def entries(self):
        rows = []
        for owner, manager in tuple(self.managers.items()):
            if manager.service is None:
                continue
            try:
                info = manager.service.inspect()
                if info.active is None:
                    continue
                path = manager.service.store.root / "versions" / info.active
                safe_path(path / "manifest.json")
                raw = (path / "manifest.json").read_bytes()
                metadata = json.loads(raw) if len(raw) <= 2 * 1024 * 1024 else {}
                rows.append(ModEntry(owner, "功能扩展", str(metadata.get("name") or owner), info.active,
                                     str(metadata.get("description") or ""), info.enabled, path,
                                     pending="等待自然退出后完成操作" if info.pending_transaction else ""))
            except (OSError, ValueError, StateError):
                _LOG.warning("MOD metadata unavailable: %s", owner)
        try:
            rows.extend(self.resources.entries())
        except (OSError, ValueError, StateError, RuntimeError):
            _LOG.exception("Resource catalog unavailable")
        return sorted(rows, key=lambda row: (row.kind, row.name.casefold(), row.id))

    def _begin(self, jobs):
        if self.busy or self._closed:
            return False
        self.busy = True
        self.busy_changed.emit(True)
        self._results = []
        self._queue.extend(jobs)
        self._next()
        return True

    def perform(self, action, keys):
        if action not in {"enable", "disable", "delete", "rollback", "use"}:
            return False
        visible = {entry.key: entry for entry in self.entries()}
        return self._begin([(action, visible[key], None) for key in dict.fromkeys(keys) if key in visible])

    def import_source(self, path, expected=None):
        from .local_package_intents import _read_bounded, _read_zip_manifest, route_local_package
        try:
            path = Path(path).resolve(strict=True)
            raw = _read_bounded(path / "manifest.json", 2 * 1024 * 1024) if path.is_dir() else _read_zip_manifest(path)
            manifest = json.loads(raw)
            if manifest.get("kind") == "content":
                if expected is not None and not expected.startswith("角色资源:"):
                    raise ValueError("更新文件类型不一致")
                return self._begin([("import", expected, path)])
            route = route_local_package(path)
            if expected is not None and expected != "功能扩展:" + route.feature_id:
                raise ValueError("更新文件不是同一个包")
            from .local_package_intents import attach_local_package_manager
            manager = attach_local_package_manager(self.config, self.host, route, role="settings", management_only=True)
            self.managers[route.feature_id] = manager
            self.dialog._mount_local_feature_manager(route, manager)
            self.connect_managers()
            return self._begin([("import_feature", route, path)])
        except (OSError, ValueError, StateError, RuntimeError) as exc:
            self.result.emit(expected or "", ModOutcome(False, "导入失败：请选择完整的 MOD ZIP 或包目录。" + str(exc)[:160]))
            return False

    def _background(self, operation):
        def run():
            try:
                outcome = operation()
            except (OSError, ValueError, RuntimeError) as exc:
                _LOG.exception("Resource operation failed")
                outcome = ModOutcome(False, "操作未完成：" + str(exc)[:160])
            if not self._closed:
                self._finished.emit(outcome)
        threading.Thread(target=run, name="mod-resource-operation", daemon=False).start()

    def _next(self):
        if not self._queue:
            self._current = None
            self.busy = False
            self.busy_changed.emit(False)
            self.changed.emit()
            self.batch_finished.emit(tuple(self._results))
            return
        self._current = self._queue.popleft()
        self._load_retries = 0
        self._apply_retries = 0
        action, entry, source = self._current
        if action == "import":
            expected = entry.split(":", 1)[1] if entry else None
            def install():
                previous = {row.id: row.version for row in self.resources.entries(include_unmanaged=False)}
                installed = self.resources.install(source, expected_id=expected)
                if previous.get(installed.id) == installed.version:
                    return ModOutcome(True, "已存在，不重复导入")
                return ModOutcome(True, f"已导入 {installed.name}；" + ("保持启用" if installed.enabled else "点击启用即可使用"))
            self._background(install)
            return
        if action == "import_feature":
            manager = self.managers[entry.feature_id]
            if not self.dialog._prepare_feature_revocation(entry.feature_id):
                self._finish(ModOutcome(False, "已取消，编辑保留"))
            elif not manager.submit_local_source(source, auto_apply=True, enabled=False):
                self._finish(ModOutcome(False, "导入未开始；该包可能正在操作中"))
            return
        if not entry.managed and action != "use":
            self._finish(ModOutcome(False, "此项来自内置或手动目录，不由管理中心修改"))
            return
        if entry.kind == "角色资源":
            actions = {"enable": lambda: self.resources.set_enabled(entry.id, True),
                       "disable": lambda: self.resources.set_enabled(entry.id, False),
                       "delete": lambda: self.resources.remove(entry.id),
                       "rollback": lambda: self.resources.rollback(entry.id),
                       "use": lambda: self.resource_client.use(entry)}
            self._background(actions[action])
            return
        if action in {"disable", "delete", "rollback"} and not self.dialog._prepare_feature_revocation(entry.id):
            self._finish(ModOutcome(False, "已取消，编辑保留"))
            return
        manager = self.managers[entry.id]
        if action in {"enable", "disable"}:
            info = manager.service.inspect()
            accepted = manager.submit("enable", action == "enable", expected_revision=info.revision)
        else:
            manager._auto_apply_requested = True
            accepted = manager.submit("uninstall" if action == "delete" else "rollback")
        if not accepted:
            self._finish(ModOutcome(False, "该包正在操作，请稍后重试"))

    def _feature_finished(self, owner, result):
        if self._current is None or not isinstance(result, OperationResult):
            return
        action, entry, _ = self._current
        expected = entry.feature_id if action == "import_feature" else getattr(entry, "id", None)
        if owner != expected or (result.phase == "awaiting_confirmation" and not result.reason):
            return
        if result.status in ("completed", "idempotent", "awaiting_startup_confirmation"):
            refresh_feature_catalog(self.config, self.host, self.managers, role="settings")
            self.dialog._mod_settings.sync()
            loaded = self.managers[owner].last_result
            from .feature_package_transactions import LOCK_BUSY_REASONS
            if loaded.reason in LOCK_BUSY_REASONS:
                delays = (100, 250, 500, 1000, 2000)
                attempt = getattr(self, "_load_retries", 0)
                if attempt < len(delays):
                    self._load_retries = attempt + 1
                    current = self._current
                    QTimer.singleShot(delays[attempt], lambda: self._feature_finished(owner, result)
                                      if not self._closed and self._current is current else None)
                    return
            if action not in {"delete", "disable"} and (self.host.state(owner) == "fault" or loaded.status not in {"completed", "idempotent"}):
                self._finish(ModOutcome(False, "安装状态已保存，但加载未完成：" + str(loaded.reason or loaded.status)))
                return
            if result.status == "idempotent" and result.reason == "same_version_same_digest":
                outcome = ModOutcome(True, "已存在，不重复导入")
            else:
                outcome = ModOutcome(True, {"import_feature": ("已导入；保持启用" if self.host.enabled(owner) else "已导入；点击启用即可使用"), "enable": "已启用", "disable": "已停用",
                                            "delete": "已删除安装副本，个人数据保留", "rollback": "已恢复上一版"}.get(action, "已完成"))
        elif result.status == "awaiting_release":
            # Stopping a Worker may remove an owner between preparation and
            # acceptance. Revalidate the same explicit confirmation; never
            # pretend a pre-acceptance wait is already a durable transaction.
            from .feature_package_transactions import LOCK_BUSY_REASONS
            delays = (100, 250, 500, 1000, 2000)
            attempt = getattr(self, "_apply_retries", 0)
            if result.plan is not None and result.reason in {*LOCK_BUSY_REASONS, "lifecycle_owners_changed"} and attempt < len(delays):
                self._apply_retries = attempt + 1
                current = self._current
                def retry():
                    if self._closed or self._current is not current:
                        return
                    if not self.managers[owner].submit("apply", result.plan, confirmation_token=result.plan.confirmation_token):
                        self._feature_finished(owner, result)
                QTimer.singleShot(delays[attempt], retry)
                return
            try:
                accepted = bool(result.operation_id) and self.managers[owner].service.inspect().pending_transaction == result.operation_id
            except (OSError, ValueError, StateError):
                accepted = False
            outcome = (ModOutcome(False, "等待相关程序自然退出后完成操作", True) if accepted else
                       ModOutcome(False, "操作尚未接受，请自然退出相关程序后重试"))
        else:
            outcome = ModOutcome(False, "操作未完成：" + str(result.reason or result.status))
        self._finish(outcome)

    def _resource_finished(self, outcome):
        try:
            publish_catalog_change(self.config.dir)
        except (OSError, StateError):
            _LOG.exception("MOD notification failed")
        self._finish(outcome)

    def _finish(self, outcome):
        if self._current is None:
            return
        action, entry, _ = self._current
        key = "功能扩展:" + entry.feature_id if action == "import_feature" else (entry.key if isinstance(entry, ModEntry) else entry or "")
        self._results.append((key, outcome))
        self.result.emit(key, outcome)
        self._current = None
        self.changed.emit()
        QTimer.singleShot(0, self._next)

    def close(self):
        self._closed = True
        self._queue.clear()
