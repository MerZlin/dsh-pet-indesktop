"""Independent per-instance vision settings. No chat imports on the runtime path."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Callable

from pet.credentials import CredentialError, CredentialVaultPort
from pet.feature_ports import FeatureConfigurationPort

from ..common.models import MODES, VisionProfile, VisionRequestConfig, VisionSettings, infer_vision_model


def digest(data: object) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class Resolution:
    ready: bool
    reason: str
    request: VisionRequestConfig | None = None
    revision: str = ""


class VisionConfigService:
    def __init__(self, config: FeatureConfigurationPort, *, vault: CredentialVaultPort, legacy_secret_reader: Callable[[str], str] | None = None, api=None):
        self.config = config
        self.vault = vault
        self._legacy_secret_reader = legacy_secret_reader
        self.api = api

    def read_legacy_secret(self, ref: str) -> str:
        if not ref:
            return ""
        if self._legacy_secret_reader is None:
            raise CredentialError("legacy_credential_unavailable")
        return self._legacy_secret_reader(ref)

    def revision(self, mode="automatic") -> str:
        try:
            if self.api is not None:
                purpose = "manual_look" if mode == "manual" else "analyze_frame"
                business = self.business(mode)
                return digest({"api": self.api.effective_version(purpose), "business": business})
            return self.config.revision()
        except (OSError, ValueError, TypeError):
            # A stable invalid revision cancels previous work without crashing GUI timers.
            return "configuration_invalid"

    def business(self, mode):
        document = self.config.read_namespace()
        if mode in document.get("business", {}):
            return document["business"][mode]
        settings = self.settings()
        p = settings.profiles.get(settings.bindings.get(mode, ""))
        return (
            {"prompt": p.system_prompt, "temperature": p.temperature, "max_tokens": p.max_tokens}
            if p
            else {"prompt": "请描述屏幕中可见的内容。", "temperature": 0.7, "max_tokens": 2048}
        )

    def save_business(self, mode, prompt, temperature, max_tokens, *, expected_revision):
        if self.api is None or mode not in MODES:
            raise ValueError("invalid_mode")
        # Reuse the request model's existing sampling/budget validation, without
        # persisting a second endpoint, model or credential reference.
        VisionProfile("business", "https://api.invalid", "business", system_prompt=prompt, temperature=temperature, max_tokens=max_tokens)
        with self.config.operation():
            document = self.config.read_namespace()
            document.setdefault("business", {})[mode] = {"prompt": prompt, "temperature": temperature, "max_tokens": max_tokens}
            self.config.commit_namespace(document, expected_revision=expected_revision)

    def settings(self) -> VisionSettings:
        return VisionSettings.from_dict(self.config.read_namespace().get("settings", {}))

    def resolve(self, mode: str) -> Resolution:
        if mode not in MODES:
            return Resolution(False, "invalid_mode")
        try:
            if self.api is not None:
                purpose = "manual_look" if mode == "manual" else "analyze_frame"
                before = self.revision(mode)
                request = self.api.resolve(purpose)
                meta = request.metadata
                business = self.business(mode)
                profile = VisionProfile(
                    "core-api",
                    meta.base_url,
                    meta.model or infer_vision_model(getattr(meta, "fallback_model", "")),
                    chat_path=meta.chat_path,
                    verify_ssl=meta.verify_ssl,
                    timeout=meta.timeout,
                    system_prompt=business["prompt"],
                    temperature=business["temperature"],
                    max_tokens=business["max_tokens"],
                )
                if self.revision(mode) != before:
                    return Resolution(False, "configuration_changed")
                return Resolution(
                    True,
                    "ready",
                    replace(VisionRequestConfig.from_profile(profile, request.api_key), credential_source=getattr(meta, "credential_source", "")),
                    before,
                )
            document = self.config.read_namespace()
            revision = digest(document)
            settings = VisionSettings.from_dict(document.get("settings", {}))
            profile = settings.profiles.get(settings.bindings.get(mode, ""))
            if profile is None:
                return Resolution(False, "not_configured")
            secret = self.vault.acquire(profile.credential_ref, profile.profile_id, profile.endpoint, "manual_look" if mode == "manual" else "analyze_frame")
            if self.revision() != revision:
                return Resolution(False, "configuration_changed")
            return Resolution(True, "ready", VisionRequestConfig.from_profile(profile, secret), revision)
        except (CredentialError, PermissionError) as exc:
            return Resolution(False, str(exc))
        except (ValueError, TypeError, OSError, AttributeError):
            return Resolution(False, "configuration_invalid")

    def recover(self) -> None:
        """Never restore old user edits. Only remove this transaction's unused secrets."""
        with self.config.operation():
            self._recover()

    def _recover(self) -> None:
        journal = self.config.read_journal()
        if journal is None:
            return
        referenced = {p.credential_ref for p in self.settings().profiles.values()}
        remaining = []
        for ref in journal.get("created_refs", []) + journal.get("retired_refs", []):
            if ref in referenced:
                continue
            try:
                self.vault.delete(ref, authorized=True)
            except CredentialError:
                remaining.append(ref)
        journal["created_refs"] = remaining
        journal["retired_refs"] = []
        journal["phase"] = "cleanup_pending" if remaining else "recovered"
        self.config.write_journal(journal)
        if remaining:
            raise CredentialError("recovery_pending")

    def apply(self, settings: VisionSettings, secrets: dict[str, str], *, expected_revision: str, source_guard: Callable[[dict], None] | None = None) -> None:
        # Operation lock serializes recover/migrate/editor; keyring never holds the config write lock.
        with self.config.operation():
            self._recover()
            if self.config.revision() != expected_revision:
                raise ValueError("configuration_changed")
            if source_guard:
                source_guard(self.config.migration_source())
            references = {pid: self.vault.reserve() for pid, secret in secrets.items() if secret}
            # Validated snapshot only; no raw legacy config and no plaintext credentials.
            backup = self.settings().to_dict()
            journal = {
                "phase": "prepared",
                "before": backup,
                "created_refs": list(references.values()),
                "retired_refs": [p.credential_ref for p in self.settings().profiles.values() if p.credential_ref],
            }
            self.config.write_journal(journal)
            profiles = dict(settings.profiles)
            try:
                for pid, ref in references.items():
                    profile = profiles[pid]
                    self.vault.save(pid, profile.endpoint, secrets[pid], ref=ref)
                    profiles[pid] = replace(profile, credential_ref=ref)
                target = replace(settings, profiles=profiles)
                self.config.commit_namespace(
                    {"schema_version": 1, "settings": target.to_dict()},
                    expected_revision=expected_revision,
                    source_guard=source_guard,
                )
                journal["phase"] = "committed"
                self.config.write_journal(journal)
                self._recover()
            except Exception:
                # Read-after-write handles a crash/failure after commit without deleting live keys.
                self._recover()
                raise

    def save_profile(self, profile: VisionProfile, *, modes: list[str], expected_revision: str, secret: str | None = None) -> None:
        if not modes or set(modes) - MODES:
            raise ValueError("invalid_mode")
        settings = self.settings()
        profiles, bindings = dict(settings.profiles), dict(settings.bindings)
        old = profiles.get(profile.profile_id)
        if secret is None and profile.credential_ref:
            self.vault.acquire(profile.credential_ref, profile.profile_id, profile.endpoint, "manual_look")
        if old and secret is None and not profile.credential_ref and old.endpoint == profile.endpoint:
            profile = replace(profile, credential_ref=old.credential_ref)
        profiles[profile.profile_id] = profile
        bindings.update({mode: profile.profile_id for mode in modes})
        self.apply(
            VisionSettings(profiles, bindings, "configured"), {profile.profile_id: secret} if secret is not None else {}, expected_revision=expected_revision
        )
