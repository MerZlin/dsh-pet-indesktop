"""User-confirmed legacy API import. Preview never opens the credential store.

Schema compatibility lives in Core's migration adapter, not in the generic API
port. Old namespaces and keys remain untouched; each selected source is a
separate transaction (never silently merge two credentials).
"""

from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit

from .api_config import ApiService, CoreApiConfiguration, legacy_source_revision
from .config_transaction import read_document
from .credentials import CoreBalanceVault, CredentialError, CredentialVaultPort, secure_backend


@dataclass(frozen=True)
class MigrationPreview:
    source_id: str
    title: str
    service: ApiService
    source_revision: str
    purposes: tuple


class ApiMigration:
    def __init__(self, cfg, *, backend=None):
        self.cfg, self.backend = cfg, backend
        self.api = CoreApiConfiguration(cfg, backend=backend)

    def _sources(self, document):
        plugins = document.get("plugins", {})
        ai = plugins.get("official.ai-chat", {}).get("chat", {}) or document.get("chat", {})
        result = {}
        for pid, p in ai.get("providers", {}).items():
            if not isinstance(p, dict):
                continue
            result["ai:" + pid + ":text"] = (
                p,
                "official.ai-chat",
                pid,
                "chat.send",
                (("official.ai-chat", "chat.send", ""), ("official.ai-chat", "files.interpret", "")),
            )
            vision = (
                p
                if p.get("vision_same_as_chat", True)
                else {**p, "base_url": p.get("vision_base_url", ""), "model": p.get("vision_model", ""), "api_key_ref": p.get("vision_api_key_ref", "")}
            )
            result["ai:" + pid + ":vision"] = (
                vision,
                "official.ai-chat",
                pid,
                "chat.send",
                (
                    ("official.screen-understanding", "manual_look", vision.get("model", "")),
                    ("official.screen-understanding", "analyze_frame", vision.get("model", "")),
                ),
            )
        screen = plugins.get("official.screen-understanding", {}).get("settings", {})
        for mode, pid in screen.get("bindings", {}).items():
            p = screen.get("profiles", {}).get(pid, {})
            if mode not in {"manual", "automatic"} or not p:
                continue
            purpose = "manual_look" if mode == "manual" else "analyze_frame"
            result["screen:" + mode + ":" + pid] = (
                {**p, "api_key_ref": p.get("credential_ref", "")},
                "official.screen-understanding",
                pid,
                purpose,
                (("official.screen-understanding", purpose, p.get("model", "")),),
            )
        balance = plugins.get("core.balance", {})
        if balance:
            p = {"base_url": balance.get("endpoint", ""), "api_key_ref": balance.get("credential_ref", ""), "balance_protocol": "deepseek"}
            result["balance"] = (p, "core.balance", "balance", "balance.query", (("core.balance", "balance.query", ""),))
        return result

    def preview(self):
        document = read_document(Path(self.cfg.path))
        revision = legacy_source_revision(document)
        previews = []
        for sid, (p, owner, pid, purpose, grants) in self._sources(document).items():
            try:
                service = ApiService(
                    "imported",
                    str(p.get("name") or sid),
                    str(p.get("base_url", "")),
                    str(p.get("chat_path") or "/v1/chat/completions"),
                    str(p.get("model", "")),
                    str(p.get("vision_model", "")),
                    bool(p.get("verify_ssl", True)),
                    int(p.get("timeout", 60)),
                    "deepseek" if p.get("balance_protocol") == "deepseek" or urlsplit(p.get("base_url", "")).hostname == "api.deepseek.com" else "none",
                ).validate()
            except (TypeError, ValueError):
                continue
            previews.append(MigrationPreview(sid, sid, service, revision, grants))
        return previews

    def confirm(self, preview, service_id, *, grants, expected_revision):
        if not isinstance(preview, MigrationPreview):
            raise ValueError("migration_selection_invalid")
        receipt = self.api.document().get("migration_receipts", {}).get(preview.source_id)
        if receipt == {"source_revision": preview.source_revision, "service_id": service_id}:
            return self.api.services()[service_id]
        latest = read_document(Path(self.cfg.path))
        if legacy_source_revision(latest) != preview.source_revision:
            raise ValueError("migration_source_changed")
        candidate = next((p for p in self.preview() if p.source_id == preview.source_id), None)
        if candidate != preview or service_id in self.api.services():
            raise ValueError("migration_selection_invalid")
        if self.api.revision() != expected_revision:
            raise ValueError("configuration_changed")
        p, owner, pid, operation, _ = self._sources(latest)[preview.source_id]
        ref = p.get("api_key_ref", "")
        if not ref:
            raise CredentialError("legacy_key_reentry_required")
        instance = str(getattr(self.cfg, "instance_id", "") or "primary")
        layout = getattr(self.cfg, "runtime_layout", None)
        if owner == "core.balance":
            if layout is None:
                raise CredentialError("scope_invalid")
            vault = CoreBalanceVault(layout.data_root_id, instance, backend=self.backend)
        else:
            scope = layout.credential_namespace(owner, instance) if layout else str(Path(self.cfg.path).resolve())
            vault = CredentialVaultPort(owner, scope, backend=self.backend)
        if ref.startswith(vault.scope + "/"):
            secret = vault.acquire(ref, pid, p["base_url"], operation)
        else:
            # Only the explicitly chosen legacy ref, never enumeration or fallback.
            try:
                secret = (self.backend or secure_backend()).get_password("dsh-pet-standalone", ref) or ""
            except Exception:
                raise CredentialError("legacy_credential_unavailable") from None
        if not secret:
            raise CredentialError("credential_missing")

        def guard(source):
            if source.get("source_revision") != preview.source_revision:
                raise ValueError("migration_source_changed")

        return self.api.save_service(
            replace(preview.service, service_id=service_id),
            secret=secret,
            grants=grants,
            expected_revision=expected_revision,
            source_guard=guard,
            migration_receipt=(preview.source_id, {"source_revision": preview.source_revision, "service_id": service_id}),
        )
