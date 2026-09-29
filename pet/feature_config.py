"""Core-side namespace and journal binding. No screen or chat business imports."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config_transaction import atomic_document, file_transaction, read_document
from .feature_ports import FeatureConfigurationPort, FeaturePreferencesPort


def namespace_revision(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class _BoundConfiguration:
    # Bound callbacks deliberately do not expose Config or a general-purpose path.
    read_namespace: Callable[[], dict]
    revision: Callable[[], str]
    migration_source: Callable[[], dict]
    commit_namespace: Callable[..., None]
    operation: Callable[[], AbstractContextManager[None]]
    read_journal: Callable[[], dict | None]
    write_journal: Callable[[dict], None]


def bind_feature_configuration(
    cfg, owner: str, *, journal_path: Path | None, migration_source: Callable[[dict], dict] | None = None
) -> FeatureConfigurationPort:
    """Host-only factory. Feature code receives the returned, already-bound port."""
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)+", owner):
        raise ValueError("invalid_feature_owner")
    path = Path(cfg.path) if hasattr(cfg, "path") else None
    if journal_path is not None and (path is None or journal_path.parent.resolve() != path.parent.resolve() or journal_path.resolve() == path.resolve()):
        raise ValueError("invalid_feature_journal")

    def document() -> dict:
        return read_document(path) if path is not None and path.exists() else copy.deepcopy(cfg.data)

    def select(doc: dict) -> dict:
        plugins = doc.get("plugins", {})
        if not isinstance(plugins, dict) or not isinstance(plugins.get(owner, {}), dict):
            raise ValueError("configuration_invalid")
        return copy.deepcopy(plugins.get(owner, {}))

    def source(doc: dict) -> dict:
        return copy.deepcopy(migration_source(copy.deepcopy(doc))) if migration_source else {}

    def commit(value: dict, *, expected_revision: str, source_guard: Callable[[dict], None] | None = None) -> None:
        if path is None:
            raise ValueError("configuration_unbound")
        value = copy.deepcopy(value)
        with file_transaction(path.with_suffix(path.suffix + ".write.lock")):
            latest = document()
            if namespace_revision(select(latest)) != expected_revision:
                raise ValueError("configuration_changed")
            if source_guard:
                source_guard(source(latest))
            latest.setdefault("plugins", {})[owner] = value
            atomic_document(path, latest)
            cfg.data.setdefault("plugins", {})[owner] = copy.deepcopy(value)

    def journal() -> Path:
        if journal_path is None:
            raise ValueError("configuration_unbound")
        return journal_path

    def read_journal() -> dict | None:
        target = journal()
        return read_document(target) if target.exists() else None

    return _BoundConfiguration(
        lambda: select(document()),
        lambda: namespace_revision(select(document())),
        lambda: source(document()),
        commit,
        lambda: file_transaction(journal().with_suffix(".lock")),
        read_journal,
        lambda value: atomic_document(journal(), copy.deepcopy(value)),
    )


@dataclass(frozen=True, slots=True)
class _BoundPreferences:
    read: Callable[[], dict]
    stage: Callable[[dict], None]
    flush: Callable[[], bool]


def bind_feature_preferences(cfg, *, key: str, fields: frozenset[str]) -> FeaturePreferencesPort:
    """Core grants a fixed compatibility field set; feature cannot select a key."""

    def read() -> dict:
        value = cfg.get(key, {})
        return copy.deepcopy(value) if isinstance(value, dict) else {}

    def stage(values: dict) -> None:
        if not isinstance(values, dict) or values.keys() - fields:
            raise PermissionError("preference_not_granted")
        value = read()
        value.update(copy.deepcopy(values))
        cfg.set(key, value)

    return _BoundPreferences(read, stage, lambda: bool(cfg.save()))
