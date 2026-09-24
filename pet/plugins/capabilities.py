"""插件 capability 集合。"""

from __future__ import annotations


class CapabilityDenied(PermissionError):
    """插件没有声明所请求的 Core capability。"""


class CapabilitySet:
    def __init__(self, capabilities=()) -> None:
        self._values = frozenset(str(item) for item in capabilities)

    def __contains__(self, capability: str) -> bool:
        return str(capability) in self._values

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def allows(self, capability: str) -> bool:
        return str(capability) in self._values

    def require(self, capability: str) -> None:
        if not self.allows(capability):
            raise CapabilityDenied(f"capability denied: {capability}")

    def as_frozenset(self) -> frozenset[str]:
        return self._values

    def __repr__(self) -> str:
        return f"CapabilitySet({sorted(self._values)!r})"
