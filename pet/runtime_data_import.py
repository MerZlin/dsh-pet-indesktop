"""Explicit, bounded ordinary-data import. Never discovers executable packages.

A pending intent fences normal Core access; an exclusive OS root lock proves
that current Core/settings processes have naturally exited. Secrets/reference
migration uses an explicitly authorized vault adapter; only reference metadata
is staged, never secret values. No Qt objects or source code are executed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import stat
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from . import feature_state_io as io
from .feature_package_files import canonical_name, remove_owned_tree
from .plugins.package_trust import VerificationLimits
from .runtime_credential_import import CredentialImportMapping, RuntimeCredentialImporter
from .runtime_layout import RuntimeLayout
from .runtime_resource_import import MAX_TOTAL_BYTES as RESOURCE_TOTAL_BYTES
from .runtime_resource_import import file_limit, resource_name, validate_resources

_ORDINARY_TOTAL_BYTES = 64 * 1024**2
_LIMITS = VerificationLimits(max_file_bytes=128 * 1024**2, max_total_bytes=_ORDINARY_TOTAL_BYTES + RESOURCE_TOTAL_BYTES, max_entries=4096)
_CONFIG = re.compile(r"config(?:-[a-zA-Z0-9_-]{1,100})?\.json\Z")
_SESSIONS = re.compile(r"sessions(?:-[a-zA-Z0-9_-]{1,100})?\Z")
_DATA = frozenset({"proactive_screen_memory.json", "proactive_screen_state.json", "proactive_screen_dryrun_state.json"})
_AI_DATA = "feature-data/official.ai-chat/"
_SECRETS = frozenset(
    {
        "api_key",
        "api_key_ref",
        "vision_api_key",
        "vision_api_key_ref",
        "credential_ref",
        "access_token",
        "refresh_token",
        "authorization_token",
        "password",
        "secret",
    }
)


def _ordinary_name(name: str) -> bool:
    if name.startswith(_AI_DATA):
        name = name[len(_AI_DATA) :]
        parts = name.split("/")
        return len(parts) >= 2 and bool(_SESSIONS.fullmatch(parts[0])) and parts[-1].endswith(".json")
    parts = name.split("/")
    return (len(parts) == 1 and bool(_CONFIG.fullmatch(name) or name in _DATA)) or (
        len(parts) >= 2 and bool(_SESSIONS.fullmatch(parts[0])) and parts[-1].endswith(".json")
    )


def _destination(name: str) -> str:
    # Legacy sessions lived alongside Core Config; the actual split AI host
    # reads only its owner-bound user-data root. This mapping is explicit in
    # the immutable confirmation, not a silent merge or a Core-side AI loader.
    return _AI_DATA + name if _SESSIONS.fullmatch(name.split("/")[0]) else name


def _mappings(files) -> tuple[tuple[str, str], ...]:
    result = tuple((name, _destination(name)) for name in files)
    if len({target.casefold() for _, target in result}) != len(result):
        raise io.StateError("import_mapping_collision")
    return result


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _encode(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _read(path: Path, limit: int = _LIMITS.max_file_bytes) -> bytes:
    io.safe_path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise io.StateError("import_size_or_type_limit")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    after = path.stat()
    if len(data) > limit or (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size) != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size):
        raise io.StateError("source_changed")
    io.safe_path(path)
    return data


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise io.StateError("import_document_invalid")
        value[key] = item
    return value


def _document(path: Path):
    return json.loads(_read(path), object_pairs_hook=_pairs)


def _no_secrets(value, depth=0, *, allow_refs=False):
    if depth > 64:
        raise io.StateError("import_document_invalid")
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _SECRETS and item not in (None, "", [], {}) and not (allow_refs and key in {"api_key_ref", "vision_api_key_ref", "credential_ref"}):
                raise io.StateError("credential_migration_required")
            _no_secrets(item, depth + 1, allow_refs=allow_refs)
    elif isinstance(value, list):
        for item in value:
            _no_secrets(item, depth + 1, allow_refs=allow_refs)


def _inventory(root: Path, *, allow_refs=False, check_resources=True) -> dict[str, bytes]:
    io.safe_path(root)
    if not root.is_dir():
        raise io.StateError("import_source_invalid")
    result = {}
    names = set()
    total = 0
    ordinary_total = 0
    resource_total = 0
    entries = 0

    def add(path):
        nonlocal total, entries, ordinary_total, resource_total
        entries += 1
        if entries > _LIMITS.max_entries:
            raise io.StateError("import_entry_limit")
        name = canonical_name(path.relative_to(root).as_posix(), _LIMITS)
        folded = name.casefold()
        if folded in names:
            raise io.StateError("case_collision")
        names.add(folded)
        io.safe_path(path)
        info = path.stat()
        resource = name.startswith("content/characters/")
        if resource and not resource_name(name, directory=stat.S_ISDIR(info.st_mode)):
            raise io.StateError("import_resource_layout_invalid")
        if stat.S_ISDIR(info.st_mode):
            for child in path.iterdir():
                add(child)
        elif stat.S_ISREG(info.st_mode) and (path.suffix == ".json" or resource):
            data = _read(path, file_limit(name))
            if path.suffix == ".json":
                doc = json.loads(data, object_pairs_hook=_pairs)
                _no_secrets(doc, allow_refs=allow_refs and not resource)
            total += len(data)
            if resource:
                resource_total += len(data)
            else:
                ordinary_total += len(data)
            if total > _LIMITS.max_total_bytes or ordinary_total > _ORDINARY_TOTAL_BYTES or resource_total > RESOURCE_TOTAL_BYTES:
                raise io.StateError("import_total_limit")
            result[name] = data
        else:
            raise io.StateError("unsupported_ordinary_data")

    count = 0
    for path in root.iterdir():
        count += 1
        if count > _LIMITS.max_entries:
            raise io.StateError("import_entry_limit")
        if _CONFIG.fullmatch(path.name) or _SESSIONS.fullmatch(path.name) or path.name in _DATA:
            add(path)
        elif path.name == "content":
            io.safe_path(path)
            if not path.is_dir():
                raise io.StateError("import_resource_layout_invalid")
            characters = path / "characters"
            io.safe_path(characters)
            if characters.exists():
                if not characters.is_dir():
                    raise io.StateError("import_resource_layout_invalid")
                for character in characters.iterdir():
                    add(character)
        elif path.name == "feature-data":
            owner_root = path / "official.ai-chat"
            io.safe_path(owner_root)
            if owner_root.exists():
                if not owner_root.is_dir():
                    raise io.StateError("unsupported_ordinary_data")
                for child in owner_root.iterdir():
                    if _SESSIONS.fullmatch(child.name):
                        add(child)
    if check_resources:
        validate_resources(root)
    return dict(sorted(result.items()))


def _hashes(files):
    return tuple((name, _digest(data)) for name, data in files.items())


@dataclass(frozen=True)
class DataImportPlan:
    operation_id: str
    source: str
    target: str
    data_root_id: str
    files: tuple[str, ...]
    source_digests: tuple[tuple[str, str], ...]
    before_digests: tuple[tuple[str, str], ...]
    estimated_bytes: int
    file_mappings: tuple[tuple[str, str], ...] = ()
    snapshot_digests: tuple[tuple[str, str], ...] = ()
    credential_mappings: tuple[CredentialImportMapping, ...] = ()
    source_root_identity: str | None = None

    @property
    def destinations(self) -> tuple[tuple[str, str], ...]:
        return self.file_mappings or tuple((name, name) for name in self.files)

    @property
    def output_digests(self) -> tuple[tuple[str, str], ...]:
        digests = dict(self.snapshot_digests or self.source_digests)
        return tuple((target, digests[source]) for source, target in self.destinations)

    @property
    def confirmation_digest(self) -> str:
        document = asdict(self)
        if not self.file_mappings:
            # Preserve the hash of an already-authored ordinary-only intent.
            document.pop("file_mappings")
        for field in ("snapshot_digests", "credential_mappings"):
            if not getattr(self, field):
                document.pop(field)
        if self.source_root_identity is None:
            document.pop("source_root_identity")
        return _digest(_encode(document))

    @property
    def confirmation_token(self) -> str:
        return _digest(("data-import-v1:" + self.confirmation_digest).encode())


@dataclass(frozen=True)
class DataImportResult:
    status: str
    plan: DataImportPlan | None = None
    reason: str | None = None


class RuntimeDataImporter:
    def __init__(self, layout: RuntimeLayout, *, credentials: RuntimeCredentialImporter | None = None):
        if credentials is not None and credentials.layout != layout:
            raise ValueError("credential_target_layout_mismatch")
        self.layout = layout
        self.credentials = credentials
        self.root = layout.data_root / "data-import"
        self.pending = self.root / "pending.json"
        self.management_lock = self.root / "management.lock"

    def _management(self):
        # Serializes acceptance/recovery/owned cleanup. Normal Core processes
        # pin ordinary data access, not this lock: closing an unaccepted preview
        # never needs to wait for them or leak a staged snapshot.
        try:
            return io.open_kernel_lock(self.management_lock)
        except io.StateError as exc:
            if exc.code == "lock_busy":
                raise io.StateError("import_management_busy") from None
            raise

    def _operation(self, operation_id):
        if not isinstance(operation_id, str) or not re.fullmatch(r"import-[a-f0-9]{32}", operation_id):
            raise io.StateError("import_identity_invalid")
        path = self.root / operation_id
        io.safe_path(path)
        return path

    def _checkpoint(self, step):
        """Fault-injection boundary; production performs no extra work."""

    def _save(self, directory, journal):
        io.atomic_write(directory / "journal.json", _encode(journal))

    def _load(self, operation_id):
        directory = self._operation(operation_id)
        journal = _document(directory / "journal.json")
        if set(journal) != {"format_version", "plan", "confirmation_digest", "phase"} or journal["format_version"] != 1:
            raise io.StateError("import_journal_invalid")
        value = dict(journal["plan"])
        value["files"] = tuple(value["files"])
        value.setdefault("file_mappings", ())
        value.setdefault("snapshot_digests", ())
        value.setdefault("credential_mappings", ())
        value.setdefault("source_root_identity", None)
        rows = []
        for row in value["credential_mappings"]:
            row = dict(row)
            for field in ("source_pointer", "target_pointer"):
                row[field] = tuple(row[field])
            rows.append(CredentialImportMapping(**row))
        value["credential_mappings"] = tuple(rows)
        for field in ("source_digests", "before_digests", "file_mappings", "snapshot_digests"):
            value[field] = tuple(tuple(pair) for pair in value[field])
        plan = DataImportPlan(**value)
        if (
            plan.operation_id != operation_id
            or plan.target != str(self.layout.data_root)
            or plan.data_root_id != self.layout.data_root_id
            or not hmac.compare_digest(journal["confirmation_digest"], plan.confirmation_digest)
            or journal["phase"] not in {"preflighted", "writing", "completed"}
        ):
            raise io.StateError("import_journal_invalid")
        if (
            not plan.files
            or len(plan.files) > _LIMITS.max_entries
            or type(plan.estimated_bytes) is not int
            or not 0 <= plan.estimated_bytes <= _LIMITS.max_total_bytes
        ):
            raise io.StateError("import_journal_invalid")
        for name in plan.files:
            canonical_name(name, _LIMITS)
            if not (_ordinary_name(name) or resource_name(name)):
                raise io.StateError("import_journal_invalid")
        if plan.file_mappings and plan.file_mappings != _mappings(plan.files):
            raise io.StateError("import_journal_invalid")
        targets = {target for _, target in plan.destinations}
        for pairs, allowed in ((plan.source_digests, set(plan.files)), (plan.before_digests, targets)):
            if len(dict(pairs)) != len(pairs) or any(
                name not in allowed or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest) for name, digest in pairs
            ):
                raise io.StateError("import_journal_invalid")
        if tuple(name for name, digest in plan.source_digests) != plan.files or len(set(plan.files)) != len(plan.files):
            raise io.StateError("import_journal_invalid")
        if plan.snapshot_digests:
            if tuple(name for name, _ in plan.snapshot_digests) != plan.files or any(
                not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest) for _, digest in plan.snapshot_digests
            ):
                raise io.StateError("import_journal_invalid")
        if plan.source_root_identity is not None and (
            not isinstance(plan.source_root_identity, str) or not re.fullmatch(r"(?:legacy|[a-f0-9]{32})", plan.source_root_identity)
        ):
            raise io.StateError("import_journal_invalid")
        if plan.credential_mappings:
            if self.credentials is None or not plan.snapshot_digests:
                raise io.StateError("credential_import_adapter_required")
            self.credentials.validate(plan.credential_mappings, plan.files)
        return directory, journal, plan

    def preflight(self, source: Path | str) -> DataImportResult:
        directory = None
        created = False
        try:
            source = Path(source).absolute()
            target = self.layout.data_root
            io.safe_path(source)
            if source == target or source.is_relative_to(target) or target.is_relative_to(source):
                raise io.StateError("import_root_overlap")
            io.safe_path(self.pending)
            if self.pending.exists():
                raise io.StateError("data_import_recovery_required")
            source_files = _inventory(source, allow_refs=self.credentials is not None)
            existing = _inventory(target, allow_refs=True)
            if not source_files:
                raise io.StateError("import_source_empty")
            ordinary = {name: data for name, data in source_files.items() if not resource_name(name)}
            transformed, credential_mappings = self.credentials.preview(source, ordinary) if self.credentials is not None else (ordinary, ())
            files = {name: transformed[name] if name in transformed else data for name, data in source_files.items()}
            for name, data in files.items():
                if not resource_name(name):
                    _no_secrets(json.loads(data), allow_refs=self.credentials is not None)
            if self.credentials is not None:
                self.credentials.validate(credential_mappings, files)
            mappings = _mappings(files)
            if set(existing) - {target for _, target in mappings}:
                raise io.StateError("target_has_unmapped_data")
            plan = DataImportPlan(
                "import-" + uuid.uuid4().hex,
                str(source),
                str(target),
                self.layout.data_root_id,
                tuple(files),
                _hashes(source_files),
                _hashes(existing),
                sum(map(len, files.values())),
                mappings,
                _hashes(files) if self.credentials is not None else (),
                credential_mappings,
                self.credentials.source_identity(source) if self.credentials is not None else None,
            )
            directory = self._operation(plan.operation_id)
            directory.mkdir(parents=True, exist_ok=False)
            created = True
            snapshot = directory / "snapshot"
            snapshot.mkdir()
            for name, data in files.items():
                io.atomic_write(snapshot / name, data)
            self._save(directory, {"format_version": 1, "plan": asdict(plan), "confirmation_digest": plan.confirmation_digest, "phase": "preflighted"})
            return DataImportResult("awaiting_confirmation", plan)
        except (io.StateError, OSError, ValueError, TypeError, RecursionError) as exc:
            if created and directory is not None:
                try:
                    remove_owned_tree(directory, self.root, _LIMITS)
                except (io.StateError, OSError):
                    # Keep bounded owned evidence; never widen cleanup.
                    pass
            return DataImportResult("rejected", reason=exc.code if isinstance(exc, io.StateError) else "import_preflight_failed")

    def apply(self, plan: DataImportPlan, *, confirmation_token: str | None) -> DataImportResult:
        accepted = False
        try:
            if (
                not isinstance(plan, DataImportPlan)
                or not isinstance(confirmation_token, str)
                or not hmac.compare_digest(confirmation_token, plan.confirmation_token)
            ):
                raise io.StateError("import_confirmation_required")
            with self._management():
                directory, journal, saved = self._load(plan.operation_id)
                if saved != plan:
                    raise io.StateError("import_plan_changed")
                if journal["phase"] == "completed":
                    return DataImportResult("idempotent", plan)
                with io.open_kernel_lock(self.layout.data_root / "data-access.lock"):
                    io.safe_path(self.pending)
                    if self.pending.exists():
                        raise io.StateError("data_import_recovery_required")
                    source_files = _inventory(Path(plan.source), allow_refs=bool(plan.snapshot_digests))
                    if _hashes(source_files) != plan.source_digests:
                        raise io.StateError("source_changed")
                    if self.credentials is not None and plan.source_root_identity is not None:
                        if self.credentials.source_identity(Path(plan.source)) != plan.source_root_identity:
                            raise io.StateError("source_identity_changed")
                        self.credentials.verify_source(Path(plan.source), plan.credential_mappings, source_files)
                    if _hashes(_inventory(self.layout.data_root, allow_refs=True)) != plan.before_digests:
                        raise io.StateError("target_changed")
                    self._check_snapshot(directory, plan)
                    # This single atomic intent is the acceptance point. Never write
                    # ordinary data before it. The preflight journal is immutable.
                    io.atomic_write(
                        self.pending, _encode({"format_version": 1, "operation_id": plan.operation_id, "confirmation_digest": plan.confirmation_digest})
                    )
                    accepted = True
                    self._checkpoint("accepted")
                    return self._resume(directory, journal, plan)
        except (io.StateError, OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            reason = exc.code if isinstance(exc, io.StateError) else "import_write_failed"
            if reason in {"lock_busy", "import_management_busy"} and not accepted:
                return DataImportResult("awaiting_release", plan, "data_root_in_use" if reason == "lock_busy" else reason)
            return DataImportResult("recovery_required" if accepted else "rejected", plan, reason)

    def _check_snapshot(self, directory, plan):
        for name, digest in plan.snapshot_digests or plan.source_digests:
            if _digest(_read(directory / "snapshot" / name, file_limit(name))) != digest:
                raise io.StateError("import_snapshot_changed")
        _inventory(directory / "snapshot", allow_refs=bool(plan.snapshot_digests))

    def _resume(self, directory, journal, plan):
        self._check_snapshot(directory, plan)
        was_completed = journal["phase"] == "completed"
        before = dict(plan.before_digests)
        current = _inventory(self.layout.data_root, allow_refs=True, check_resources=False)
        if set(current) - {name for name, _ in plan.output_digests}:
            raise io.StateError("target_changed")
        # Verify ALL before making further changes: conflicts never cause a
        # partially accepted retry to overwrite an unrelated user's new edit.
        for name, digest in plan.output_digests:
            actual = _digest(current[name]) if name in current else None
            if actual not in (before.get(name), digest):
                raise io.StateError("target_changed")
        journal["phase"] = "writing"
        self._save(directory, journal)
        if plan.credential_mappings and not was_completed:
            if self.credentials is None:
                raise io.StateError("credential_import_adapter_required")
            source_files = _inventory(Path(plan.source), allow_refs=True)
            if _hashes(source_files) != plan.source_digests:
                raise io.StateError("source_changed")
            if self.credentials.source_identity(Path(plan.source)) != plan.source_root_identity:
                raise io.StateError("source_identity_changed")
            self.credentials.verify_source(Path(plan.source), plan.credential_mappings, source_files)
            for row in plan.credential_mappings:
                snapshot = _document(directory / "snapshot" / row.source_file)
                from .runtime_credential_import import _lookup

                if _lookup(snapshot, row.target_pointer) != row.target_ref:
                    raise io.StateError("credential_mapping_changed")
                self._checkpoint("before_credential")
                self.credentials.transfer(Path(plan.source), row, json.loads(source_files[row.source_file]))
                self._checkpoint("after_credential")
        digests = dict(plan.snapshot_digests or plan.source_digests)
        for source_name, name in plan.destinations:
            digest = digests[source_name]
            path = self.layout.data_root / name
            if name in current and _digest(current[name]) == digest:
                continue
            self._checkpoint("before_file")
            io.safe_path(path)
            actual = _digest(_read(path, file_limit(name))) if path.exists() else None
            expected = _digest(current[name]) if name in current else None
            if actual != expected:
                raise io.StateError("target_changed")
            payload = _read(directory / "snapshot" / source_name, file_limit(source_name))
            if _digest(payload) != digest:
                raise io.StateError("import_snapshot_changed")
            if name in current:
                backup = directory / "before" / name
                io.safe_path(backup)
                if not backup.exists():
                    io.atomic_write(backup, current[name])
                elif _digest(_read(backup, file_limit(name))) != before[name]:
                    raise io.StateError("import_backup_conflict")
            io.atomic_write(path, payload)
            self._checkpoint("after_file")
        # Pending remains fenced until ALL resource manifests and pointers agree.
        _inventory(self.layout.data_root, allow_refs=True)
        journal["phase"] = "completed"
        self._save(directory, journal)
        self._checkpoint("completed")
        io.safe_path(self.pending)
        self.pending.unlink()
        return DataImportResult("completed", plan)

    def cancel_preflight(self, plan: DataImportPlan) -> DataImportResult:
        try:
            if not isinstance(plan, DataImportPlan):
                raise io.StateError("import_plan_invalid")
            with self._management():
                directory, journal, saved = self._load(plan.operation_id)
                if saved != plan or journal["phase"] != "preflighted":
                    raise io.StateError("accepted_import_cannot_be_cancelled")
                io.safe_path(self.pending)
                if self.pending.exists() and _document(self.pending).get("operation_id") == plan.operation_id:
                    raise io.StateError("accepted_import_cannot_be_cancelled")
                remove_owned_tree(directory, self.root, _LIMITS)
                return DataImportResult("completed", plan, "preflight_cancelled")
        except (io.StateError, OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            reason = exc.code if isinstance(exc, io.StateError) else "import_cancel_failed"
            return DataImportResult("awaiting_release" if reason in {"lock_busy", "import_management_busy"} else "rejected", plan, reason)

    def recover_pending(self) -> DataImportResult:
        try:
            with self._management(), io.open_kernel_lock(self.layout.data_root / "data-access.lock"):
                io.safe_path(self.pending)
                if not self.pending.exists():
                    return DataImportResult("idempotent", reason="no_pending_import")
                intent = _document(self.pending)
                if set(intent) != {"format_version", "operation_id", "confirmation_digest"} or intent["format_version"] != 1:
                    raise io.StateError("import_intent_invalid")
                directory, journal, plan = self._load(intent["operation_id"])
                if intent["confirmation_digest"] != plan.confirmation_digest:
                    raise io.StateError("import_intent_conflict")
                return self._resume(directory, journal, plan)
        except (io.StateError, OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            reason = exc.code if isinstance(exc, io.StateError) else "import_recovery_failed"
            if reason in {"lock_busy", "import_management_busy"}:
                return DataImportResult("awaiting_release", reason="data_root_in_use" if reason == "lock_busy" else reason)
            return DataImportResult("recovery_required", reason=reason)
