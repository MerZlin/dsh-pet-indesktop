"""Core-owned profiles and owner/purpose grants; no DLC business imports.

OS credentials are endpoint-bound, immutable references. Config CAS commits
are the publication point. Journals contain only newly reserved opaque refs;
recovery never deletes legacy keys or restores a stale configuration.
"""

from __future__ import annotations

import copy
import hashlib
import re
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit

from .api_ports import ApiMetadata, ApiRequest, FeatureApiPort
from .credentials import CredentialError, _ScopedVault
from .feature_config import bind_feature_configuration, namespace_revision
from .official_features import is_valid_feature_id

OWNER = "core.api"


def legacy_source_revision(document):
    value = copy.deepcopy(document)
    value.get("plugins", {}).pop(OWNER, None)
    return namespace_revision(value)


_IDENTIFIER = re.compile(r"[a-zA-Z0-9_.-]{1,96}\Z")


def validate_endpoint(value: str) -> str:
    if not isinstance(value, str) or len(value) > 2048 or any(c.isspace() or ord(c) < 32 for c in value) or "\\" in value:
        raise ValueError("api_endpoint_invalid")
    value = value.rstrip("/")
    try:
        u = urlsplit(value)
        _ = u.port  # Parse and range-check the authority before any secret write.
    except ValueError:
        raise ValueError("api_endpoint_invalid") from None
    if u.scheme not in {"https", "http"} or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ValueError("api_endpoint_invalid")
    if u.scheme == "http" and u.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("api_endpoint_requires_tls")
    return value


@dataclass(frozen=True, slots=True)
class ApiService:
    service_id: str
    name: str
    base_url: str
    chat_path: str = "/v1/chat/completions"
    model: str = ""
    vision_model: str = ""
    verify_ssl: bool = True
    timeout: int = 60
    balance_protocol: str = "none"
    credential_ref: str = ""

    def validate(self):
        if any(
            not isinstance(value, str)
            for value in (self.service_id, self.name, self.base_url, self.chat_path, self.model, self.vision_model, self.balance_protocol, self.credential_ref)
        ):
            raise ValueError("api_service_invalid")
        if not _IDENTIFIER.fullmatch(self.service_id) or not self.name or len(self.name) > 128:
            raise ValueError("api_service_invalid")
        endpoint = validate_endpoint(self.base_url)
        if not self.chat_path.startswith("/") or self.chat_path.startswith("//") or any(c in self.chat_path for c in "?#\\\r\n"):
            raise ValueError("api_path_invalid")
        if type(self.verify_ssl) is not bool or type(self.timeout) is not int or not 1 <= self.timeout <= 300:
            raise ValueError("api_service_invalid")
        if self.balance_protocol not in {"none", "deepseek"} or any(len(v) > 256 for v in (self.model, self.vision_model)):
            raise ValueError("api_service_invalid")
        return replace(self, base_url=endpoint)


class CoreApiConfiguration:
    def __init__(self, cfg, *, backend=None):
        self.path = Path(cfg.path)
        self.port = bind_feature_configuration(
            cfg,
            OWNER,
            journal_path=self.path.with_suffix(self.path.suffix + ".api-journal.json"),
            migration_source=lambda doc: {"source_revision": legacy_source_revision(doc)},
        )
        layout = getattr(cfg, "runtime_layout", None)
        identity = getattr(layout, "data_root_id", "") or hashlib.sha256(str(self.path.resolve()).encode()).hexdigest()
        self.vault = _ScopedVault(
            "core.api/" + identity, str(getattr(cfg, "instance_id", "") or "primary"), frozenset({"api.resolve"}), backend=backend, prefix="dsh-pet/core/api/"
        )

    def revision(self):
        return self.port.revision()

    def document(self):
        raw = self.port.read_namespace()
        if raw and (raw.get("schema_version") != 1 or not isinstance(raw.get("services"), dict) or not isinstance(raw.get("bindings"), dict)):
            raise ValueError("api_configuration_invalid")
        raw = raw or {"schema_version": 1, "services": {}, "bindings": {}}
        for owner, uses in raw["bindings"].items():
            if not is_valid_feature_id(owner) or not isinstance(uses, dict):
                raise ValueError("api_configuration_invalid")
            for purpose, binding in uses.items():
                if not isinstance(purpose, str) or not _IDENTIFIER.fullmatch(purpose) or not isinstance(binding, dict):
                    raise ValueError("api_configuration_invalid")
                service_id, model, endpoint = (binding.get(key) for key in ("service_id", "model", "authorized_endpoint"))
                if (
                    not isinstance(service_id, str)
                    or not _IDENTIFIER.fullmatch(service_id)
                    or not isinstance(model, str)
                    or len(model) > 256
                    or not isinstance(endpoint, str)
                ):
                    raise ValueError("api_configuration_invalid")
                grant_id = binding.get("grant_id", "")
                if not isinstance(grant_id, str) or (grant_id and not re.fullmatch(r"[0-9a-f]{32}", grant_id)):
                    raise ValueError("api_configuration_invalid")
        if not isinstance(raw.get("migration_receipts", {}), dict):
            raise ValueError("api_configuration_invalid")
        return raw

    def services(self):
        result = {}
        for pid, value in self.document()["services"].items():
            try:
                service = ApiService(**value).validate()
                if pid != service.service_id:
                    raise ValueError
                result[pid] = service
            except (TypeError, ValueError):
                raise ValueError("api_configuration_invalid") from None
        return result

    def _recover(self):
        journal = self.port.read_journal()
        if not journal or journal.get("phase") == "completed":
            return
        live = {s.credential_ref for s in self.services().values()}
        pending = []
        for ref in journal.get("created_refs", []):
            if ref not in live:
                try:
                    self.vault.delete(ref, authorized=True)
                except CredentialError:
                    pending.append(ref)
        self.port.write_journal({**journal, "created_refs": pending, "phase": "cleanup_pending" if pending else "recovered"})
        if pending:
            raise CredentialError("api_recovery_pending")

    def recover(self):
        with self.port.operation():
            self._recover()

    def save_service(self, service, *, secret="", grants=None, expected_revision, source_guard=None, migration_receipt=None):
        service = service.validate()
        if not isinstance(secret, str) or len(secret) > 16384:
            raise ValueError("api_secret_invalid")
        with self.port.operation():
            self._recover()
            if self.revision() != expected_revision:
                raise ValueError("configuration_changed")
            if source_guard:
                source_guard(self.port.migration_source())
            before = self.document()
            previous = self.services().get(service.service_id)
            # A draft is not authority to reuse an arbitrary ref or change its endpoint.
            ref = previous.credential_ref if previous and previous.base_url == service.base_url else ""
            service = replace(service, credential_ref=ref)
            target = copy.deepcopy(before)
            if previous and previous.base_url != service.base_url:
                for uses in target["bindings"].values():
                    for purpose, binding in list(uses.items()):
                        if binding.get("service_id") == service.service_id:
                            del uses[purpose]
            if grants is not None:
                # Explicit UI list replaces this service's grants only, never another service.
                for uses in target["bindings"].values():
                    for purpose, binding in list(uses.items()):
                        if binding.get("service_id") == service.service_id:
                            del uses[purpose]
                for owner, purpose, model in grants:
                    if not is_valid_feature_id(owner) or not _IDENTIFIER.fullmatch(purpose) or not isinstance(model, str) or len(model) > 256:
                        raise ValueError("api_grant_invalid")
                    uses = target["bindings"].setdefault(owner, {})
                    if purpose in uses and uses[purpose].get("service_id") != service.service_id:
                        raise ValueError("api_binding_conflict")
                    old = before["bindings"].get(owner, {}).get(purpose, {})
                    retained = old.get("service_id") == service.service_id and old.get("authorized_endpoint") == service.base_url
                    grant_id = old.get("grant_id", "") if retained else ""
                    uses[purpose] = {
                        "service_id": service.service_id,
                        "model": model,
                        "authorized_endpoint": service.base_url,
                        "grant_id": grant_id or uuid.uuid4().hex,
                    }
            created = self.vault.reserve() if secret.strip() else ""
            journal = {"phase": "prepared", "created_refs": [created] if created else [], "expected_revision": expected_revision}
            self.port.write_journal(journal)
            try:
                if created:
                    self.vault.save(service.service_id, service.base_url, secret, ref=created)
                    service = replace(service, credential_ref=created)
                target["services"][service.service_id] = asdict(service)
                if migration_receipt:
                    target.setdefault("migration_receipts", {})[migration_receipt[0]] = migration_receipt[1]
                self.port.commit_namespace(target, expected_revision=expected_revision, source_guard=source_guard)
            except Exception:
                try:
                    self._recover()
                except CredentialError:
                    pass  # Retain cleanup_pending, surface the original safe failure.
                raise
            try:
                self.port.write_journal({**journal, "phase": "completed"})
            except OSError:
                pass  # Config commit is publication; replay recognizes its live refs.
        self._publish()
        return service

    def _publish(self):
        from .api_notifications import publish_api

        publish_api(self.path)

    def revoke(self, owner, purpose, *, expected_revision):
        with self.port.operation():
            target = self.document()
            target["bindings"].get(owner, {}).pop(purpose, None)
            self.port.commit_namespace(target, expected_revision=expected_revision)
        self._publish()

    def delete_service(self, service_id, *, expected_revision):
        with self.port.operation():
            target = self.document()
            target["services"].pop(service_id, None)
            for uses in target["bindings"].values():
                for purpose, binding in list(uses.items()):
                    if binding.get("service_id") == service_id:
                        del uses[purpose]
            # Explicit deletion revokes usage; it is not permission to delete an old key.
            self.port.commit_namespace(target, expected_revision=expected_revision)
        self._publish()

    def bind(self, owner, *, authorized, open_settings=None):
        if not is_valid_feature_id(owner):
            raise ValueError("api_owner_invalid")

        def selection(purpose):
            if not authorized():
                raise PermissionError("execution_not_authorized")
            document = self.document()
            from .simple_api import simple_selection

            simple = simple_selection(document, purpose)
            if simple is not None:
                return simple
            binding = document["bindings"].get(owner, {}).get(purpose)
            if not isinstance(binding, dict):
                raise PermissionError("api_use_not_authorized")
            raw = document["services"].get(binding.get("service_id"))
            if raw is None:
                raise PermissionError("api_use_not_authorized")
            try:
                service = ApiService(**raw).validate()
            except (TypeError, ValueError, AttributeError):
                raise ValueError("api_configuration_invalid") from None
            if binding.get("authorized_endpoint") != service.base_url:
                raise PermissionError("api_use_not_authorized")
            return service, binding

        def authority_version(service, binding):
            return namespace_revision({"service_id": service.service_id, "endpoint": service.base_url, "grant_id": binding.get("grant_id", "legacy")})

        def snapshot_version(purpose, service, binding):
            return namespace_revision(
                {
                    "service_id": service.service_id,
                    "endpoint": service.base_url,
                    "path": service.chat_path,
                    "model": binding.get("model", "") or service.model,
                    "fallback_model": binding.get("fallback_model", ""),
                    "credential_source": binding.get("credential_source", ""),
                    "tls": service.verify_ssl,
                    "timeout": service.timeout,
                    "ref": service.credential_ref,
                    "authorization": authority_version(service, binding),
                    "balance": service.balance_protocol if purpose == "balance.query" else "",
                }
            )

        def version(purpose):
            try:
                service, binding = selection(purpose)
                return snapshot_version(purpose, service, binding)
            except (PermissionError, ValueError, OSError):
                return "unavailable"

        def make_metadata(purpose, service, binding):
            return ApiMetadata(
                service.service_id,
                service.name,
                service.base_url,
                service.chat_path,
                binding.get("model", "") if binding.get("simple_visual") else (binding.get("model", "") or service.model),
                service.verify_ssl,
                service.timeout,
                service.balance_protocol,
                snapshot_version(purpose, service, binding),
                authority_version(service, binding),
                binding.get("credential_source", ""),
                binding.get("fallback_model", ""),
            )

        def metadata(purpose):
            service, binding = selection(purpose)
            return make_metadata(purpose, service, binding)

        def resolve(purpose):
            service, binding = selection(purpose)
            meta = make_metadata(purpose, service, binding)
            secret = self.vault.acquire(service.credential_ref, service.service_id, service.base_url, "api.resolve")
            if meta.version != version(purpose) or not authorized():
                raise PermissionError("api_configuration_changed")
            from .api_notifications import observe_api_request

            observe_api_request(self.path, owner, purpose, meta.version)
            return ApiRequest(meta, secret)

        def subscribe(callback):
            from .api_notifications import subscribe_api
            from .simple_api import PURPOSES

            return subscribe_api(
                self.path, lambda: PURPOSES if self.document().get("simple") else self.document()["bindings"].get(owner, {}), version, callback, owner=owner
            )

        return FeatureApiPort(metadata, version, resolve, subscribe, open_settings or (lambda: None))
