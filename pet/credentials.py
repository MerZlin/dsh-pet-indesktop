"""Scoped host credential port. Never used inside a worker process."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Protocol


class SecureBackend(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...
    def set_password(self, service: str, username: str, password: str) -> None: ...
    def delete_password(self, service: str, username: str) -> None: ...


class CredentialError(RuntimeError):
    """Message is a fixed safe reason, never a backend exception or credential."""


def secure_backend() -> SecureBackend:
    """Accept OS credential stores only, never an optional plaintext/file fallback."""
    try:
        import keyring

        backend = keyring.get_keyring()
        allowed = {"keyring.backends.Windows", "keyring.backends.macOS", "keyring.backends.SecretService", "keyring.backends.kwallet"}
        candidates = backend.backends if type(backend).__module__ == "keyring.backends.chainer" else [backend]
        for candidate in candidates:
            if type(candidate).__module__ in allowed and candidate.priority > 0:
                return candidate
    except Exception:
        pass
    raise CredentialError("backend_unavailable")


class CredentialVaultPort:
    OPERATIONS = frozenset({"manual_look", "analyze_frame"})

    def __init__(self, feature: str, instance: str, *, backend: SecureBackend | None = None):
        self.scope = hashlib.sha256((feature + "\0" + instance).encode()).hexdigest()
        self.service = "dsh-pet/features/" + self.scope
        self._backend = backend

    def _store(self) -> SecureBackend:
        if self._backend is not None:
            return self._backend
        return secure_backend()

    def _check_ref(self, ref: str) -> None:
        if not ref.startswith(self.scope + "/") or len(ref) != len(self.scope) + 33:
            raise CredentialError("scope_denied")

    def reserve(self) -> str:
        """Create a non-secret reference for write-ahead recovery before touching keyring."""
        return self.scope + "/" + uuid.uuid4().hex

    def save(self, profile: str, endpoint: str, secret: str, *, ref: str | None = None) -> str:
        if not secret.strip():
            raise CredentialError("credential_missing")
        ref = ref or self.reserve()
        self._check_ref(ref)
        envelope = json.dumps({"profile": profile, "endpoint": endpoint, "secret": secret})
        backend = self._store()
        try:
            backend.set_password(self.service, ref, envelope)
            if backend.get_password(self.service, ref) != envelope:
                raise CredentialError("credential_write_failed")
        except Exception:
            try:
                backend.delete_password(self.service, ref)
            except Exception:
                pass
            raise CredentialError("credential_write_failed") from None
        return ref

    def acquire(self, ref: str, profile: str, endpoint: str, operation: str) -> str:
        if not ref:
            raise CredentialError("credential_missing")
        self._check_ref(ref)
        if operation not in self.OPERATIONS:
            raise CredentialError("operation_denied")
        backend = self._store()
        try:
            raw = backend.get_password(self.service, ref)
        except Exception:
            raise CredentialError("credential_read_failed") from None
        if not raw:
            raise CredentialError("credential_missing")
        try:
            data = json.loads(raw)
            if data["profile"] != profile or data["endpoint"] != endpoint:
                raise CredentialError("scope_denied")
            secret = data["secret"]
            if not isinstance(secret, str) or not secret:
                raise ValueError
            return secret
        except CredentialError:
            raise
        except Exception:
            raise CredentialError("credential_read_failed") from None

    def status(self, ref: str, profile: str, endpoint: str, operation: str) -> str:
        try:
            self.acquire(ref, profile, endpoint, operation)
            return "available"
        except CredentialError as exc:
            return str(exc)

    def delete(self, ref: str, *, authorized: bool = False) -> None:
        if not authorized:
            raise CredentialError("delete_not_authorized")
        self._check_ref(ref)
        backend = self._store()
        try:
            if backend.get_password(self.service, ref) is not None:
                backend.delete_password(self.service, ref)
        except Exception:
            raise CredentialError("credential_delete_failed") from None
