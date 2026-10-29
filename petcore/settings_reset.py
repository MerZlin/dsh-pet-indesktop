"""设置重置：按**键作用域**把配置恢复出厂默认（Qt-free，纯键级操作）。

petcore 与主分支的存储布局不同，不能照搬「本宠一个文件、全局一个文件」：

- 主宠（slot 0）的全局设置与它自己的偏好都在 ``config.json``；
- 子宠（slot ≥ 1）的偏好另存 ``config-pet-N.json``，全局键仍写 ``config.json``。

所以「重置本宠」与「重置全局」在这里是**两组键**（口径同
:data:`petcore.config.GLOBAL_CONFIG_KEYS` 的路由规则），而不是两个文件：主宠页
两个按钮各自只动自己那一半，谁也不覆盖谁。

永远不重置的两类东西：

- ``active_pets`` / ``positions``：活跃成员表与所有宠的位置（抹掉等于全部回到默认位）；
- 「本宠重置」还保留 ``character`` / ``rx`` / ``ry`` / ``screen_name``：身份与位置
  是这只宠的存在本身，和主分支 ``reset_slot_profile`` 的保留口径一致。

API 密钥只在系统钥匙串、服务规格在 ``api.json``：两者都不在 ``DEFAULTS`` 里，
重置不会碰，也不需要「保留凭据」这类特例。
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from .config import DEFAULTS, GLOBAL_CONFIG_KEYS

logger = logging.getLogger("petcore.settings_reset")

SCOPE_PET = "pet"
SCOPE_GLOBAL = "global"
SCOPES = (SCOPE_GLOBAL, SCOPE_PET)

#: 任何作用域都不动：宠物身份/位置与活跃成员表。
PRESERVED_ALWAYS = frozenset({"active_pets", "positions"})
#: 「本宠重置」额外保留的身份与位置键（保身份、保位置，与主分支一致）。
PET_IDENTITY_KEYS = frozenset({"character", "rx", "ry", "screen_name"})


class ResetError(RuntimeError):
    """重置失败（写盘失败已回滚内存，界面只显示这一条原因）。"""


def normalize_scope(scope: str) -> str:
    value = str(scope or "").strip().lower()
    if value not in SCOPES:
        raise ValueError(f"unknown reset scope: {scope!r}")
    return value


def reset_keys(scope: str) -> tuple[str, ...]:
    """该作用域要恢复默认的键（排序后，便于测试与日志稳定比较）。"""
    scope = normalize_scope(scope)
    if scope == SCOPE_GLOBAL:
        candidates = set(GLOBAL_CONFIG_KEYS) & set(DEFAULTS)
        preserved = PRESERVED_ALWAYS
    else:
        candidates = set(DEFAULTS) - set(GLOBAL_CONFIG_KEYS)
        preserved = PRESERVED_ALWAYS | PET_IDENTITY_KEYS
    return tuple(sorted(candidates - set(preserved)))


def reset_values(scope: str) -> dict[str, Any]:
    """键 → 出厂默认值（深拷贝：``DEFAULTS`` 里的列表/字典是共享单例）。"""
    return {key: copy.deepcopy(DEFAULTS[key]) for key in reset_keys(scope)}


def apply_reset(config, scope: str) -> tuple[str, ...]:
    """把 ``config``（``Config`` 或 ``ScopedConfig``）按作用域恢复默认并落盘。

    返回真正改动过的键；写盘失败时把内存回滚到调用前的快照并抛
    :class:`ResetError`——界面上「重置失败」必须意味着什么都没变。
    """
    scope = normalize_scope(scope)
    values = reset_values(scope)
    snapshot = config.snapshot()
    changed: list[str] = []
    for key, value in values.items():
        try:
            current = config.get(key)
        except Exception:  # noqa: BLE001 - 读不出来的键按「需要重置」处理
            current = object()
        if current == value:
            continue
        config.set(key, copy.deepcopy(value))
        changed.append(key)
    try:
        config.save()
    except OSError as exc:
        try:
            config.restore(snapshot)
        except Exception:  # noqa: BLE001 - 回滚失败只记日志，仍要报出写盘失败
            logger.warning("settings reset rollback failed", exc_info=True)
        raise ResetError(str(exc)) from exc
    logger.info("settings reset scope=%s changed=%d", scope, len(changed))
    return tuple(changed)
