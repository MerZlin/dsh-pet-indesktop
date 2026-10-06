"""Explicit, closed reference transfer; preflight never reads OS secrets.

Only metadata from a user-selected Config and the fixed two official namespaces
is recognized. Secrets move directly between OS vaults after the import's durable
acceptance. No enumeration, plaintext snapshot, source deletion or code loading.
Legacy plaintext/unknown credential schemas remain a safe, visible refusal.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from . import feature_state_io as io
from .credentials import CoreBalanceVault, CredentialError, CredentialVaultPort, SecureBackend, secure_backend
from .http_compat import normalize_chat_endpoint
from .official_features import AI_OWNER, SCREEN_OWNER
from .runtime_layout import PRODUCT_ID, RuntimeLayout

_REFS = frozenset({"api_key_ref", "vision_api_key_ref", "credential_ref"})
_ID = re.compile(r"[a-zA-Z0-9_-]{1,100}\Z")
_SCOPE = re.compile(r"[a-f0-9]{64}\Z")
_REF = re.compile(r"[a-f0-9]{64}/[a-f0-9]{32}\Z")


@dataclass(frozen=True)
class CredentialImportMapping:
    source_file: str
    source_pointer: tuple[str, ...]
    target_pointer: tuple[str, ...]
    owner: str
    instance: str
    profile: str
    endpoint: str
    source_kind: str
    source_scope: str
    source_ref: str
    target_ref: str


def _lookup(document, pointer):
    current = document
    for part in pointer:
        if not isinstance(current, dict) or part not in current:
            raise io.StateError("credential_mapping_changed")
        current = current[part]
    return current


def _put(document, pointer, value):
    _lookup(document, pointer)  # never creates an arbitrary path
    parent = _lookup(document, pointer[:-1])
    parent[pointer[-1]] = value


def _endpoint(value):
    if not isinstance(value, str) or not value or len(value) > 2048 or any(ord(c) < 33 for c in value):
        raise io.StateError("credential_endpoint_invalid")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise io.StateError("credential_endpoint_invalid")
    parsed.port
    return value


def _references(document, pointer=(), depth=0):
    if depth > 64:
        raise io.StateError("import_document_invalid")
    found = {}
    if isinstance(document, dict):
        for key, value in document.items():
            current = (*pointer, key)
            if key in _REFS and value not in (None, ""):
                if not isinstance(value, str):
                    raise io.StateError("credential_migration_required")
                found[current] = value
            found.update(_references(value, current, depth + 1))
    elif isinstance(document, list):
        for i, value in enumerate(document):
            found.update(_references(value, (*pointer, str(i)), depth + 1))
    return found


def _source_root_id(source: Path) -> str | None:
    path = source / "data-root.json"
    io.safe_path(path)
    if not path.exists():
        return None

    def unique(pairs):
        if len(dict(pairs)) != len(pairs):
            raise io.StateError("source_identity_invalid")
        return dict(pairs)

    value = json.loads(io.read_bytes(path, 4096), object_pairs_hook=unique)
    if (
        not isinstance(value, dict)
        or set(value) != {"format_version", "product_id", "data_root_id"}
        or type(value["format_version"]) is not int
        or value["format_version"] != 1
        or value["product_id"] != PRODUCT_ID
        or not isinstance(value["data_root_id"], str)
        or not re.fullmatch(r"[a-f0-9]{32}", value["data_root_id"])
    ):
        raise io.StateError("source_identity_invalid")
    return value["data_root_id"]


def _instance(name, document):
    match = re.fullmatch(r"config(?:-([a-zA-Z0-9_-]{1,100}))?\.json", name)
    if not match:
        raise io.StateError("credential_source_invalid")
    suffix = match[1] or ""
    declared = document.get("instance_id", suffix)
    if declared != suffix:
        raise io.StateError("credential_instance_mismatch")
    return suffix or "primary"


class RuntimeCredentialImporter:
    def __init__(self, layout: RuntimeLayout, *, backend: SecureBackend | None = None):
        self.layout = layout
        self.backend = backend

    def _source_vault(self, source: Path, name: str, owner: str, instance: str):
        root_id = _source_root_id(source)
        scope = str((source / name).resolve())
        if root_id is not None and owner != "core.balance":
            old = RuntimeLayout(self.layout.executable, source, "installed", root_id)
            scope = old.credential_namespace(owner, instance)
        if owner == "core.balance":
            if root_id is None:
                raise io.StateError("source_identity_invalid")
            return CoreBalanceVault(root_id, instance, backend=self.backend)
        return CredentialVaultPort(owner, scope, backend=self.backend)

    def _target_vault(self, owner, instance):
        if owner == "core.balance":
            return CoreBalanceVault(self.layout.data_root_id, instance, backend=self.backend)
        return CredentialVaultPort(owner, self.layout.credential_namespace(owner, instance), backend=self.backend)

    def source_identity(self, source: Path) -> str:
        return _source_root_id(source) or "legacy"

    @staticmethod
    def _operation(owner):
        return {AI_OWNER: "chat.send", SCREEN_OWNER: "manual_look", "core.balance": "balance.query"}[owner]

    def verify_source(self, source: Path, rows, files):
        for row in rows:
            doc = json.loads(files[row.source_file])
            if _instance(row.source_file, doc) != row.instance or _lookup(doc, row.source_pointer) != row.source_ref:
                raise io.StateError("credential_mapping_changed")
            old = self._source_vault(source, row.source_file, row.owner, row.instance)
            if row.source_kind == "scoped" and old.scope != row.source_scope:
                raise io.StateError("credential_source_scope_invalid")

    def preview(self, source: Path, files: dict[str, bytes]):
        result = dict(files)
        mappings = []
        for name, data in files.items():
            if not re.fullmatch(r"config(?:-[a-zA-Z0-9_-]{1,100})?\.json", name):
                if _references(json.loads(data)):
                    raise io.StateError("credential_migration_required")
                continue
            doc = json.loads(data)
            if not isinstance(doc, dict):
                raise io.StateError("import_document_invalid")
            instance = _instance(name, doc)
            target = copy.deepcopy(doc)
            references = _references(doc)
            allowed = set()
            pointer: tuple[str, ...]
            plugins = doc.get("plugins", {})
            if not isinstance(plugins, dict):
                raise io.StateError("import_document_invalid")

            def add(pointer, target_pointer, owner, profile, endpoint):
                ref = _lookup(doc, pointer)
                if not isinstance(ref, str) or not ref:
                    raise io.StateError("credential_mapping_changed")
                endpoint = _endpoint(endpoint)
                if not isinstance(profile, str) or not _ID.fullmatch(profile):
                    raise io.StateError("credential_profile_invalid")
                old = self._source_vault(source, name, owner, instance)
                kind = "scoped" if _REF.fullmatch(ref) and ref.startswith(old.scope + "/") else "legacy"
                if kind == "legacy" and (owner != AI_OWNER or ref not in {"provider/" + profile, "provider/" + profile + "/vision"}):
                    raise io.StateError("credential_source_scope_invalid")
                new = self._target_vault(owner, instance)
                row = CredentialImportMapping(
                    name, pointer, target_pointer, owner, instance, profile, endpoint, kind, old.scope if kind == "scoped" else "", ref, new.reserve()
                )
                _put(target, target_pointer, row.target_ref)
                mappings.append(row)
                allowed.add(pointer)

            own_chat = plugins.get(AI_OWNER, {}).get("chat") if isinstance(plugins.get(AI_OWNER, {}), dict) else None
            if not isinstance(plugins.get(AI_OWNER, {}), dict):
                raise io.StateError("import_document_invalid")
            raw_chat = doc.get("chat", {})
            if not isinstance(raw_chat, dict):
                raise io.StateError("import_document_invalid")
            chat = own_chat if own_chat is not None else raw_chat
            if not isinstance(chat, dict):
                raise io.StateError("import_document_invalid")
            providers = chat.get("providers", {})
            if not isinstance(providers, dict):
                raise io.StateError("import_document_invalid")
            if own_chat is None and providers:
                target.setdefault("plugins", {}).setdefault(AI_OWNER, {})["chat"] = copy.deepcopy(chat)
            for pid, provider in providers.items():
                if not isinstance(provider, dict) or not isinstance(pid, str) or not _ID.fullmatch(pid):
                    raise io.StateError("credential_profile_invalid")
                base = ("plugins", AI_OWNER, "chat", "providers", pid) if own_chat is not None else ("chat", "providers", pid)
                new_base = ("plugins", AI_OWNER, "chat", "providers", pid)
                for field, endpoint in (
                    ("api_key_ref", provider.get("base_url", "https://api.deepseek.com")),
                    ("vision_api_key_ref", provider.get("vision_base_url") or provider.get("base_url", "https://api.deepseek.com")),
                ):
                    if provider.get(field):
                        add((*base, field), (*new_base, field), AI_OWNER, pid, endpoint)
            # Opaque raw compatibility references are retained, not executed or
            # dereferenced by Core. They may only name this exact legacy provider.
            raw_providers = raw_chat.get("providers", {})
            if not isinstance(raw_providers, dict):
                raise io.StateError("import_document_invalid")
            for pid, provider in raw_providers.items():
                if not isinstance(provider, dict) or not isinstance(pid, str) or not _ID.fullmatch(pid):
                    raise io.StateError("credential_profile_invalid")
                for field, suffix in (("api_key_ref", ""), ("vision_api_key_ref", "/vision")):
                    pointer = ("chat", "providers", pid, field)
                    if pointer in references and pointer not in allowed:
                        if references[pointer] != "provider/" + pid + suffix:
                            raise io.StateError("credential_source_scope_invalid")
                        allowed.add(pointer)
            screen = plugins.get(SCREEN_OWNER, {})
            if not isinstance(screen, dict):
                raise io.StateError("import_document_invalid")
            settings = screen.get("settings", {})
            if not isinstance(settings, dict):
                raise io.StateError("import_document_invalid")
            profiles = settings.get("profiles", {})
            if not isinstance(profiles, dict):
                raise io.StateError("import_document_invalid")
            for pid, profile in profiles.items():
                if not isinstance(profile, dict) or profile.get("profile_id", pid) != pid:
                    raise io.StateError("credential_profile_invalid")
                pointer = ("plugins", SCREEN_OWNER, "settings", "profiles", pid, "credential_ref")
                if pointer in references:
                    add(pointer, pointer, SCREEN_OWNER, pid, normalize_chat_endpoint(profile.get("base_url"), profile.get("chat_path", "/v1/chat/completions")))
            balance = plugins.get("core.balance", {})
            if not isinstance(balance, dict):
                raise io.StateError("import_document_invalid")
            pointer = ("plugins", "core.balance", "credential_ref")
            if pointer in references:
                add(pointer, pointer, "core.balance", "balance", balance.get("endpoint"))
            if set(references) - allowed:
                raise io.StateError("credential_migration_required")
            result[name] = json.dumps(target, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        return result, tuple(mappings)

    def validate(self, rows, files):
        seen = set()
        for row in rows:
            if (
                not isinstance(row, CredentialImportMapping)
                or row.source_file not in files
                or "/" in row.source_file
                or not isinstance(row.source_pointer, tuple)
                or not 1 <= len(row.source_pointer) <= 8
                or not isinstance(row.target_pointer, tuple)
                or not 1 <= len(row.target_pointer) <= 8
                or any(not isinstance(part, str) for part in (*row.source_pointer, *row.target_pointer))
                or row.owner not in {AI_OWNER, SCREEN_OWNER, "core.balance"}
                or not isinstance(row.instance, str)
                or not _ID.fullmatch(row.instance)
                or not isinstance(row.profile, str)
                or not _ID.fullmatch(row.profile)
                or row.source_kind not in {"legacy", "scoped"}
                or not isinstance(row.target_ref, str)
                or not _REF.fullmatch(row.target_ref)
            ):
                raise io.StateError("credential_mapping_invalid")
            _endpoint(row.endpoint)
            if not row.target_ref.startswith(self._target_vault(row.owner, row.instance).scope + "/"):
                raise io.StateError("credential_target_scope_invalid")
            if row.source_kind == "legacy":
                if row.owner != AI_OWNER or row.source_scope or row.source_ref not in {"provider/" + row.profile, "provider/" + row.profile + "/vision"}:
                    raise io.StateError("credential_source_scope_invalid")
            elif (
                not isinstance(row.source_scope, str)
                or not _SCOPE.fullmatch(row.source_scope)
                or not _REF.fullmatch(row.source_ref)
                or not row.source_ref.startswith(row.source_scope + "/")
            ):
                raise io.StateError("credential_source_scope_invalid")
            expected = (
                ("plugins", AI_OWNER, "chat", "providers", row.profile)
                if row.owner == AI_OWNER
                else ("plugins", "core.balance")
                if row.owner == "core.balance"
                else ("plugins", SCREEN_OWNER, "settings", "profiles", row.profile)
            )
            if row.owner == "core.balance" and row.profile != "balance":
                raise io.StateError("credential_mapping_invalid")
            fields = {"api_key_ref", "vision_api_key_ref"} if row.owner == AI_OWNER else {"credential_ref"}
            if row.target_pointer[:-1] != expected or row.target_pointer[-1] not in fields:
                raise io.StateError("credential_mapping_invalid")
            if row.source_pointer != row.target_pointer and not (
                row.owner == AI_OWNER and row.source_pointer == ("chat", "providers", row.profile, row.target_pointer[-1])
            ):
                raise io.StateError("credential_mapping_invalid")
            key = row.source_file, row.target_pointer
            if key in seen:
                raise io.StateError("credential_mapping_collision")
            seen.add(key)

    def transfer(self, source: Path, row: CredentialImportMapping, document):
        # Caller proves immutable source bytes under the accepted root fence.
        if _instance(row.source_file, document) != row.instance or _lookup(document, row.source_pointer) != row.source_ref:
            raise io.StateError("credential_mapping_changed")
        backend = self.backend if self.backend is not None else secure_backend()
        old = self._source_vault(source, row.source_file, row.owner, row.instance)
        old._backend = backend
        new = self._target_vault(row.owner, row.instance)
        new._backend = backend
        try:
            if row.source_kind == "legacy":
                secret = backend.get_password("dsh-pet-standalone", row.source_ref)
            else:
                if old.scope != row.source_scope:
                    raise io.StateError("credential_source_scope_invalid")
                secret = old.acquire(row.source_ref, row.profile, row.endpoint, self._operation(row.owner))
            if not isinstance(secret, str) or not secret or len(secret) > 65536:
                raise io.StateError("credential_source_unavailable")
            current = backend.get_password(new.service, row.target_ref)
            if current is not None:
                if new.acquire(row.target_ref, row.profile, row.endpoint, self._operation(row.owner)) != secret:
                    raise io.StateError("credential_target_changed")
            else:
                new.save(row.profile, row.endpoint, secret, ref=row.target_ref)
        except CredentialError as exc:
            raise io.StateError(str(exc)) from None
        except io.StateError:
            raise
        except Exception:
            raise io.StateError("credential_transfer_failed") from None
