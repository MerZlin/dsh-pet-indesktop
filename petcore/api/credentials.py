"""把系统钥匙串包成 ``CredentialsPort``。

``keyring`` 惰性导入：装不上、没有可用后端、写入抛异常都**不抛给调用方**，
改为退回进程内内存，并由 ``available()`` 告诉界面「重启即丢」。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("petcore.api.credentials")

_PLACEHOLDER_BACKENDS = frozenset(
    {
        "keyring.backends.fail.Keyring",
        "keyring.backends.null.Keyring",
    }
)


def _is_placeholder(backend: Any) -> bool:
    backend_type = type(backend)
    return f"{backend_type.__module__}.{backend_type.__name__}" in _PLACEHOLDER_BACKENDS


class KeyringCredentials:
    def __init__(self, service: str = "dsh-pet-core", backend: Any = None):
        self.service = str(service)
        self._backend = backend
        self._resolved = backend is not None
        self._usable: bool | None = True if backend is not None else None
        self._memory: dict[str, str] = {}

    def available(self) -> bool:
        if self._usable is None:
            self._usable = self._probe()
        return bool(self._usable)

    def backend_name(self) -> str:
        """当前解析到的后端类名；不可用时返回空串。只读，不写任何条目。"""
        backend = self._resolve()
        if backend is None:
            return ""
        try:
            resolved = backend.get_keyring() if hasattr(backend, "get_keyring") else backend
        except Exception:  # noqa: BLE001 - 探不到就是「没有后端」
            return ""
        return f"{type(resolved).__module__}.{type(resolved).__name__}"

    def get(self, owner: str, key: str) -> str | None:
        entry = self._entry(owner, key)
        cached = self._memory.get(entry)
        if cached is not None:
            return cached
        if not self.available():
            return None
        backend = self._resolve()
        try:
            value = backend.get_password(self.service, entry)
        except Exception as exc:  # noqa: BLE001 - 后端坏了就退内存
            self._degrade(exc)
            return None
        if value is None:
            return None
        text = str(value)
        self._memory[entry] = text
        return text

    def set(self, owner: str, key: str, value: str) -> None:
        entry = self._entry(owner, key)
        text = "" if value is None else str(value)
        if not text:
            self.delete(owner, key)
            return
        self._memory[entry] = text
        if not self.available():
            return
        backend = self._resolve()
        try:
            backend.set_password(self.service, entry, text)
        except Exception as exc:  # noqa: BLE001 - 写不进钥匙串就留在内存
            self._degrade(exc)

    def delete(self, owner: str, key: str) -> None:
        entry = self._entry(owner, key)
        self._memory.pop(entry, None)
        if not self.available():
            return
        backend = self._resolve()
        try:
            backend.delete_password(self.service, entry)
        except Exception:  # noqa: BLE001 - 条目本就不存在/后端不支持删除
            pass

    def persisted(self, owner: str, key: str) -> bool:
        """回读比对：钥匙串里确实有这个条目（且是我们写的那一份）。"""
        entry = self._entry(owner, key)
        if not self.available():
            return False
        backend = self._resolve()
        try:
            actual = backend.get_password(self.service, entry)
        except Exception:  # noqa: BLE001
            return False
        if actual is None or not str(actual):
            return False
        expected = self._memory.get(entry)
        return expected is None or str(actual) == expected

    def _entry(self, owner: str, key: str) -> str:
        return f"{owner}:{key}"

    def _resolve(self) -> Any:
        if not self._resolved:
            self._resolved = True
            try:
                import keyring
            except Exception:  # noqa: BLE001 - 没装 keyring 就是「不可用」
                keyring = None
            self._backend = keyring
        return self._backend

    def _probe(self) -> bool:
        backend = self._resolve()
        if backend is None:
            return False
        try:
            resolved = backend.get_keyring() if hasattr(backend, "get_keyring") else backend
        except Exception:  # noqa: BLE001
            return False
        return not _is_placeholder(resolved)

    def _degrade(self, exc: BaseException) -> None:
        self._usable = False
        logger.warning(
            "凭据存储不可用（%s）：密钥只保留在本进程内存，重启后需要重新填写",
            type(exc).__name__,
        )
