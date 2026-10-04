"""Process-local startup capability; never serialized or accepted from UI/IPC."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

_SEAL = object()


@dataclass(frozen=True)
class StartupLoadPermit:
    operation_id: str
    revision: int
    version: str
    manifest_digest: str
    feature_root: Path
    role: str
    process_id: int
    nonce: str
    _seal: object = field(repr=False, compare=False)

    @property
    def version_owner(self) -> str:
        return "official.screen-understanding"

    @classmethod
    def _issue(cls, operation_id, state, root, role):
        if role not in ("core", "settings") or state.active is None:
            raise ValueError("startup_permit_invalid")
        return cls(operation_id, state.revision, state.active, state.versions[state.active], root.resolve(), role, os.getpid(), secrets.token_hex(32), _SEAL)

    def matches(self, root, state) -> bool:
        return (
            self._seal is _SEAL
            and self.process_id == os.getpid()
            and self.feature_root == root.resolve()
            and self.operation_id == state.pending_transaction
            and self.revision == state.revision
            and self.version == state.active
            and self.manifest_digest == state.versions.get(self.version)
        )
