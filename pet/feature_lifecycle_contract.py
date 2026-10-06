"""Qt-free, bounded lifecycle intent. Never an installed-state authority."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field

from . import feature_state_io as io
from .feature_state_io import StateError
from .official_features import OFFICIAL_FEATURES, SCREEN_FEATURE_ID


@dataclass(frozen=True)
class LifecyclePrepareRequest:
    operation_id: str
    revision: int
    operation: str
    versions: tuple[str, ...]
    live_owners: tuple[tuple[int, str], ...] = ()
    nonce: str = field(default_factory=lambda: uuid.uuid4().hex)
    feature_id: str = SCREEN_FEATURE_ID

    def document(self):
        return asdict(self)

    @classmethod
    def parse(cls, document):
        if isinstance(document, dict) and "feature_id" not in document:
            document = {**document, "feature_id": SCREEN_FEATURE_ID}
        if not isinstance(document, dict) or set(document) != set(cls.__dataclass_fields__):
            raise StateError("lifecycle_request_invalid")
        if (
            not isinstance(document["feature_id"], str)
            or document["feature_id"] not in OFFICIAL_FEATURES
            or not isinstance(document["operation_id"], str)
            or not re.fullmatch(r"(?:tx|lc)-[a-f0-9]{32}", document["operation_id"])
            or type(document["revision"]) is not int
            or document["revision"] < 0
            or document["operation"] not in ("install", "upgrade", "rollback", "uninstall", "disable")
            or not isinstance(document["nonce"], str)
            or not re.fullmatch(r"[a-f0-9]{32}", document["nonce"])
            or not isinstance(document["versions"], (list, tuple))
            or len(document["versions"]) > 256
            or any(not isinstance(v, str) or not re.fullmatch(r"[0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9}", v) for v in document["versions"])
            or not isinstance(document["live_owners"], (list, tuple))
            or len(document["live_owners"]) > 4096
        ):
            raise StateError("lifecycle_request_invalid")
        owners = []
        for owner in document["live_owners"]:
            if (
                not isinstance(owner, (list, tuple))
                or len(owner) != 2
                or type(owner[0]) is not int
                or owner[0] <= 0
                or not isinstance(owner[1], str)
                or not re.fullmatch(r"[a-f0-9]{32}", owner[1])
            ):
                raise StateError("lifecycle_owner_invalid")
            owners.append(tuple(owner))
        return cls(
            document["operation_id"],
            document["revision"],
            document["operation"],
            tuple(document["versions"]),
            tuple(owners),
            document["nonce"],
            document["feature_id"],
        )


def request_path(store, request):
    LifecyclePrepareRequest.parse(request.document())
    if request.feature_id != store.feature_id:
        raise StateError("feature_identity_conflict")
    path = store.root / "locks" / "lifecycle-requests" / (request.operation_id + ".json")
    io.safe_path(path)
    return path


def authorize_request(store, request):
    request_path(store, request)
    state = store.read().state
    if state is None or state.revision != request.revision or not set(request.versions) <= set(state.versions):
        raise StateError("revision_conflict")
    io.atomic_write(request_path(store, request), json.dumps(request.document(), sort_keys=True, separators=(",", ":")).encode())


def verify_request(store, request):
    document = json.loads(io.read_bytes(request_path(store, request), 65536))
    if LifecyclePrepareRequest.parse(document) != request:
        raise StateError("lifecycle_intent_mismatch")
    state = store.read().state
    if state is None or state.revision != request.revision or not set(request.versions) <= set(state.versions):
        raise StateError("revision_conflict")
