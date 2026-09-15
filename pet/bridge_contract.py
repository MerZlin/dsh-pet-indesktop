# -*- coding: utf-8 -*-
"""The versioned contract shared by the bundled DSH bridge and Pet monitor.

The bridge protocol version is deliberately independent from the bridge
package's semver.  A package can gain implementation fixes without making the
JSONL consumer incompatible, while changing the record envelope requires a
protocol bump.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

BRIDGE_PROTOCOL_VERSION = 1
BRIDGE_VERSION = "0.3.0"

# 桥目录的显式覆盖入口：与插件（integrations/dsh-pet-bridge/impl/*/index.js 的
# BRIDGE_DIR_ENV）同名同语义。设置时**取代**平台默认路径；两边必须看到同一个值，
# 否则插件写一个目录、桌宠读另一个目录。
BRIDGE_DIR_ENV = "DSH_PET_BRIDGE_DIR"
BRIDGE_DIR_NAME = "dsh-pet-bridge"


def resolve_bridge_dir(config_dir: str | os.PathLike[str] | None = None) -> Path:
    """桥目录：显式覆盖 > config_dir 的父目录 > 平台默认。

    为什么需要"显式覆盖"这一层：桥目录是插件与桌宠的**跨侧约定**，平台默认路径
    在三平台各不相同（win32=%APPDATA%、darwin=~/Library/Application Support、
    其他=~/.config），于是"把数据根指到别处"这件事（CI/多实例/便携部署/测试隔离）
    在 POSIX 上只能靠改 HOME 之类的环境技巧——那是在拿平台细节绕过产品行为。
    给它一个正式入口后，两边读同一个变量、行为与平台无关。

    生产环境里 `config_dir` 的父目录就是平台默认数据根，因此不给 config_dir 时
    用平台分支、给了就用其父目录，两者在真实运行时等价（保留 config_dir 派生是
    为了兼容便携/自定义配置目录的部署，以及测试里把配置目录指向临时目录的用法）。
    """
    override = os.environ.get(BRIDGE_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    if config_dir is not None:
        return Path(config_dir).parent / BRIDGE_DIR_NAME
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home()) / BRIDGE_DIR_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / BRIDGE_DIR_NAME
    return Path.home() / ".config" / BRIDGE_DIR_NAME

# Keep this list in lock-step with integrations/dsh-pet-bridge/index.js.  The
# bridge's writeRecord helper defaults event to AgentStatus, so that baseline is
# part of the contract even though most records override it.
BRIDGE_EVENT_INVENTORY: frozenset[str] = frozenset({
    "AgentStatus",
    "agent/request-error",
    "agent_reasoning",
    "agent_reasoning_raw_content",
    "approval/asked",
    "approval/decided",
    "approval/request",
    "approval/resolved",
    "assistant/message",
    "bridge/control-received",
    "bridge/control-result",
    "bridge/diagnostic",
    "bridge/hello",
    "command/done",
    "command/run",
    "context_compacted",
    "cordis/request-run",
    "cordis/request-run-resolved",
    "exec_command_begin",
    "exec_command_end",
    "execution/failed",
    "interaction/resolved",
    "llm/retry",
    "llm_error",
    "mcp_tool_call_begin",
    "mcp_tool_call_end",
    "model_access",
    "question/requested",
    "question/resolved",
    "step/end",
    "step/start",
    "task_complete",
    "task_started",
    "thread_rolled_back",
    "tool-workflow/run-end",
    "tool-workflow/run-start",
    "tool/call",
    "tool/result",
    "turn/end",
    "turn/start",
    "user/message",
    "user_action",
    "watchdog/control-result",
    "web_search_begin",
    "web_search_end",
})

# Names used by consumers and contract tests.  Keeping aliases avoids making
# callers know whether they are checking the producer or Pet side of the
# contract.
PET_ACCEPTED_EVENT_INVENTORY = BRIDGE_EVENT_INVENTORY
ACCEPTED_DSH_EVENTS = BRIDGE_EVENT_INVENTORY
BRIDGE_CAPABILITIES: frozenset[str] = frozenset({
    "agent-status",
    "session-events",
    "tool-events",
    "interaction-relay",
    "watchdog-control",
    "model-access-diagnostics",
})

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_INVENTORY_KEYS = (
    "emittedEventInventory",
    "emittedEvents",
    "eventInventory",
    "bridgeEventInventory",
)


@dataclass(frozen=True)
class BridgeRecordValidation:
    """Result of checking one DSH bridge record."""

    valid: bool
    reason: str = ""
    received_protocol: Any = None
    received_version: Any = None
    received_inventory: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.valid


def _inventory(record: Mapping[str, Any]) -> Any:
    for key in _INVENTORY_KEYS:
        if key in record:
            return record[key]
    return None


def _normalise_inventory(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return None
    if not all(isinstance(item, str) and item for item in value):
        return None
    if len(set(value)) != len(value):
        return None
    return tuple(sorted(set(value)))


def validate_bridge_record(record: Mapping[str, Any]) -> BridgeRecordValidation:
    """Validate a record before it reaches any Pet consumer.

    ``bridge/hello`` must include the producer's inventory.  For normal
    records, an inventory is optional for compatibility with producers that
    only put it in hello; when present it is checked as well.  This lets the
    monitor validate each bridge instance without relying on having observed
    its hello line first.
    """

    if not isinstance(record, Mapping):
        return BridgeRecordValidation(False, "record is not an object")
    protocol = record.get("bridgeProtocolVersion")
    version = record.get("bridgeVersion")
    if isinstance(protocol, bool) or not isinstance(protocol, int):
        return BridgeRecordValidation(False, "missing bridge protocol version", protocol, version)
    if protocol != BRIDGE_PROTOCOL_VERSION:
        return BridgeRecordValidation(
            False, "unsupported bridge protocol version", protocol, version,
        )
    if not isinstance(version, str) or not _SEMVER_RE.fullmatch(version.strip()):
        return BridgeRecordValidation(
            False, "missing or invalid bridge package version", protocol, version,
        )

    event = record.get("event")
    if not isinstance(event, str) or event not in BRIDGE_EVENT_INVENTORY:
        return BridgeRecordValidation(
            False, "event is outside the bridge inventory", protocol, version,
        )

    raw_inventory = _inventory(record)
    normalised_inventory = (
        _normalise_inventory(raw_inventory) if raw_inventory is not None else None
    )
    if event == "bridge/hello" and normalised_inventory is None:
        return BridgeRecordValidation(
            False, "bridge hello has no event inventory", protocol, version,
        )
    if event == "bridge/hello":
        capabilities = record.get("capabilities")
        if not isinstance(capabilities, (list, tuple, set, frozenset)):
            return BridgeRecordValidation(
                False, "bridge hello has no capabilities", protocol, version,
                normalised_inventory or (),
            )
        if not all(isinstance(item, str) and item for item in capabilities):
            return BridgeRecordValidation(
                False, "bridge hello capabilities are malformed", protocol, version,
                normalised_inventory or (),
            )
        if set(capabilities) != BRIDGE_CAPABILITIES or len(capabilities) != len(
            BRIDGE_CAPABILITIES
        ):
            return BridgeRecordValidation(
                False, "bridge capabilities do not match Pet", protocol, version,
                normalised_inventory or (),
            )
    if raw_inventory is not None and normalised_inventory is None:
        return BridgeRecordValidation(
            False, "bridge event inventory is malformed", protocol, version,
        )
    if normalised_inventory is not None and set(normalised_inventory) != BRIDGE_EVENT_INVENTORY:
        return BridgeRecordValidation(
            False,
            "bridge event inventory does not match Pet",
            protocol,
            version,
            normalised_inventory,
        )
    return BridgeRecordValidation(True, received_protocol=protocol, received_version=version,
                                 received_inventory=normalised_inventory or ())


def is_valid_bridge_record(record: Mapping[str, Any]) -> bool:
    """Boolean convenience seam for tests and lightweight consumers."""

    return bool(validate_bridge_record(record))
