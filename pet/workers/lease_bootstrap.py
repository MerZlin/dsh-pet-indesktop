"""Minimal child-side Worker lease takeover before runtime import.

This module intentionally imports no Qt, UI, Core host, or ``pet.plugins``
implementation.  The parent passes a one-time token and a data-root path in
controlled environment fields; the OS-backed coordinator is the authority.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..feature_version_lease import FeatureVersionLeaseCoordinator, LeaseError, retain_process_lease

TOKEN_ENV = "DSH_PET_FEATURE_LEASE_HANDOFF_TOKEN"
ROOT_ENV = "DSH_PET_FEATURE_LEASE_ROOT"
OWNER_ENV = "DSH_PET_FEATURE_LEASE_OWNER"
CLAIMED_ENV = "DSH_PET_FEATURE_LEASE_CLAIMED"


def claim_worker_lease_from_environment() -> bool:
    token = os.environ.pop(TOKEN_ENV, None)
    if token is None:
        return False
    root = os.environ.pop(ROOT_ENV, None)
    if not isinstance(root, str) or not root:
        raise LeaseError("handoff_unavailable")
    from ..feature_install_state import FEATURE_ID, FeatureInstallStateStore

    # The parent supplies the verified owner, not a child-selected package path.
    # Absent owner retains the old screen Worker handoff contract.
    owner = os.environ.pop(OWNER_ENV, FEATURE_ID)
    store = FeatureInstallStateStore(Path(root), feature_id=owner)
    lease = FeatureVersionLeaseCoordinator(store).claim_worker(token)
    retain_process_lease(lease)
    os.environ[CLAIMED_ENV] = "1"
    return True


def worker_lease_claimed() -> bool:
    return os.environ.get(CLAIMED_ENV) == "1"
