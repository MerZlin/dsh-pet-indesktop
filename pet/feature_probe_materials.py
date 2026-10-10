"""Parent-owned, bounded probe material recovery; never adopt directory scans.

The private kernel maintenance lease spans copying and native launch, not a
transaction/state lock. Only explicit write-before-create attempts are collected.
A compact journal retains at most four unresolved attempts and eight receipts.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from . import feature_package_files as files
from . import feature_state_io as io
from .feature_probe_windows import ProbeLaunchError, cleanup_owned_probe
from .plugins.package_trust import VerificationLimits, VerifiedFeatureDescriptor


@dataclass(frozen=True)
class ProbeMaterialCleanup:
    status: str
    attempt_id: str | None = None
    reason: str | None = None
    bytes_reclaimed: int = 0


@dataclass(frozen=True)
class ProbeMaterialAttempt:
    attempt_id: str
    root: Path


class ProbeMaterialStore:
    MAX_PENDING = 4
    MAX_RECENT = 8
    LIMITS = VerificationLimits()

    def __init__(self, root: Path, *, limits: VerificationLimits | None = None):
        base = limits or self.LIMITS
        # Two host/Worker snapshots, each containing a verified candidate and
        # the trusted helper. Aggregate cleanup is not a one-package inventory;
        # verification limits for candidate execution remain unchanged.
        self.limits = replace(
            base,
            max_files=4 * base.max_files + 64,
            max_entries=4 * base.max_entries + 64,
            max_total_bytes=4 * base.max_total_bytes + 64 * 1024,
            max_file_bytes=max(base.max_file_bytes, 4 * 1024 * 1024),
            max_depth=base.max_depth + 3,
            max_path_chars=base.max_path_chars + 32,
        )
        self.root = root.absolute()
        self.path = self.root / "materials.json"
        self._owner_thread: int | None = None

    @contextmanager
    def lock(self):
        with io.open_kernel_lock(self.root / "materials.lock"):
            self._owner_thread = threading.get_ident()
            try:
                yield
            finally:
                self._owner_thread = None

    def _require_lock(self):
        if self._owner_thread != threading.get_ident():
            raise io.StateError("probe_materials_lock_required")

    def _read(self):
        self._require_lock()
        io.safe_path(self.path)
        if not self.path.exists():
            return dict(schema=1, root=str(self.root), attempts={}, recent=[])
        try:
            doc = json.loads(io.read_bytes(self.path, 64 * 1024))
            if (
                set(doc) != {"schema", "root", "attempts", "recent"}
                or type(doc["schema"]) is not int
                or doc["schema"] != 1
                or doc["root"] != str(self.root)
                or not isinstance(doc["attempts"], dict)
                or len(doc["attempts"]) > self.MAX_PENDING
                or not isinstance(doc["recent"], list)
                or len(doc["recent"]) > self.MAX_RECENT
            ):
                raise ValueError()
            for name, row in doc["attempts"].items():
                if (
                    not re.fullmatch(r"probe-[0-9a-f]{32}", name)
                    or not isinstance(row, dict)
                    or set(row)
                    != {"root", "feature_id", "source_root", "parent_operation_id", "manifest_digest", "bundle_digest", "phase", "segments", "reason"}
                    or row["root"] != str(self.root / name)
                    or row["phase"] not in {"active", "deleting"}
                    or not isinstance(row["source_root"], str)
                    or len(row["source_root"]) > 4096
                    or not Path(row["source_root"]).is_absolute()
                    or not isinstance(row["segments"], dict)
                    or not set(row["segments"]) <= {"host", "worker"}
                    or any(value not in {"copying", "launching", "returned"} for value in row["segments"].values())
                    or "worker" in row["segments"]
                    and "host" not in row["segments"]
                    or row["parent_operation_id"] is not None
                    and not re.fullmatch(r"tx-[0-9a-f]{32}", row["parent_operation_id"])
                    or row["reason"] is not None
                    and (not isinstance(row["reason"], str) or len(row["reason"]) > 128)
                    or not all(isinstance(row[key], str) and re.fullmatch(r"[0-9a-f]{64}", row[key]) for key in ("manifest_digest", "bundle_digest"))
                ):
                    raise ValueError()
                from .official_features import is_valid_feature_id

                if not is_valid_feature_id(row["feature_id"]):
                    raise ValueError("invalid feature id")
            return doc
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise io.StateError("probe_materials_evidence") from exc

    def _write(self, doc):
        self._require_lock()
        raw = (json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n").encode()
        if len(raw) > 64 * 1024:
            raise io.StateError("probe_materials_limit")
        io.atomic_write(self.path, raw)

    def begin(self, descriptor: VerifiedFeatureDescriptor, bundle_digest: str) -> ProbeMaterialAttempt:
        import hashlib

        doc = self._read()
        if len(doc["attempts"]) >= self.MAX_PENDING:
            raise io.StateError("probe_materials_limit")
        name = "probe-" + uuid.uuid4().hex
        root = self.root / name
        io.safe_path(root)
        if root.exists():
            raise io.StateError("probe_materials_collision")
        # This is diagnostic linkage, not transaction authorization. Normal
        # transaction staging is exactly staging/<tx-id>/package; arbitrary
        # source paths never become state authority or cleanup targets.
        source = descriptor.root.absolute()
        parent = source.parent.name if source.name == "package" and source.parent.parent.name == "staging" else None
        parent = parent if parent is not None and re.fullmatch(r"tx-[0-9a-f]{32}", parent) else None
        doc["attempts"][name] = dict(
            root=str(root),
            feature_id=descriptor.id,
            source_root=str(source),
            parent_operation_id=parent,
            manifest_digest=hashlib.sha256(descriptor.raw_manifest).hexdigest(),
            bundle_digest=bundle_digest,
            phase="active",
            segments={},
            reason=None,
        )
        self._write(doc)  # Durable intent BEFORE mkdir, copying or any process.
        return ProbeMaterialAttempt(name, root)

    def mark(self, attempt: ProbeMaterialAttempt, kind: str, phase: str):
        doc = self._read()
        row = doc["attempts"][attempt.attempt_id]
        if attempt.root != Path(row["root"]) or kind not in {"host", "worker"} or phase not in {"copying", "launching", "returned"}:
            raise io.StateError("probe_materials_evidence")
        row["segments"][kind] = phase
        self._write(doc)

    def _remove(self, name, row):
        root = self.root / name
        io.safe_path(root)
        if not root.exists():
            if row["phase"] != "deleting" and row["segments"]:
                raise io.StateError("probe_materials_missing")
            return 0
        entries = files.inventory(root, self.limits)  # Validate ALL nodes before deleting anything.
        top = {path.name for path in root.iterdir()}
        if not top <= set(row["segments"]):
            raise io.StateError("probe_materials_unknown_node")
        for kind, phase in row["segments"].items():
            segment = root / kind
            if not segment.exists():
                if row["phase"] == "deleting" or phase == "copying":
                    continue
                raise io.StateError("probe_materials_missing")
            allowed = {"helper", "candidate"} if phase == "copying" else {"helper", "candidate", "scratch", "probe-ownership.json", "probe-ownership.tmp"}
            if not {path.name for path in segment.iterdir()} <= allowed:
                raise io.StateError("probe_materials_unknown_node")
            if row["phase"] != "deleting" and phase != "copying":
                outcome = cleanup_owned_probe(segment, row["bundle_digest"])
                if outcome == "awaiting_release":
                    raise io.StateError("probe_materials_in_use")
        # After all recorded processes/profiles are released, commit the delete
        # intent. Interrupted deletion must not need already-deleted helper
        # bytes to be reverified; unknown/link nodes are still rejected above.
        row["phase"] = "deleting"
        doc = self._read()
        doc["attempts"][name] = row
        self._write(doc)
        total = sum(info.st_size for _, path, info in entries if path.is_file())
        files.remove_owned_tree(root, self.root, self.limits)
        return total

    def recover_locked(self) -> tuple[ProbeMaterialCleanup, ...]:
        doc = self._read()
        results = []
        for name in tuple(doc["attempts"]):
            row = doc["attempts"][name]
            try:
                count = self._remove(name, row)
                result = ProbeMaterialCleanup("completed", name, bytes_reclaimed=count)
            except (io.StateError, ProbeLaunchError, OSError) as exc:
                reason = exc.code if isinstance(exc, io.StateError) else exc.reason if isinstance(exc, ProbeLaunchError) else "probe_materials_io_error"
                result = ProbeMaterialCleanup("awaiting_release" if reason == "probe_materials_in_use" else "recovery_required", name, reason)
            doc = self._read()
            if result.status == "completed":
                del doc["attempts"][name]
                doc["recent"] = [*doc["recent"], asdict(result)][-self.MAX_RECENT :]
            else:
                doc["attempts"][name]["reason"] = result.reason
            self._write(doc)
            results.append(result)
        return tuple(results) or (ProbeMaterialCleanup("idempotent"),)

    def collect_garbage(self) -> tuple[ProbeMaterialCleanup, ...]:
        try:
            with self.lock():
                return self.recover_locked()
        except (io.StateError, ProbeLaunchError, OSError) as exc:
            reason = exc.code if isinstance(exc, io.StateError) else exc.reason if isinstance(exc, ProbeLaunchError) else "probe_materials_io_error"
            return (ProbeMaterialCleanup("awaiting_release" if reason == "lock_busy" else "recovery_required", reason=reason),)
