# -*- coding: utf-8 -*-
"""Qt 插件搜索路径消毒（issue #323）。

装了 Qt 开发环境的机器上，``QT_PLUGIN_PATH`` / ``QT_QPA_PLATFORM_PLUGIN_PATH``
会排在 PySide6 自带插件目录之前：Qt 先命中外部旧版本插件 DLL，却从 PySide6
目录解析同名运行时依赖 → 跨 minor ABI 不匹配 → 加载器抛 0xc0000139
（faulthandler 打一条"fatal exception"噪声）+ 白付一次失败加载；platforms
插件被命中时应用甚至起不来。对本应用而言外部插件路径是纯污染——我们只应使用
PySide6 自带、与自身 ABI 匹配的那一套。

时序是硬约束：Qt 在**首次加载插件**时解析这些变量，因此本模块必须在任何
PySide6 导入之前调用（``pet/__main__`` 顶部、打包入口 ``packaging/pet_entry.py``）。
本模块自身零依赖（不 import PySide6），幂等。
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

#: 会被 Qt 优先于自带插件目录采纳的环境变量（iss323 根因）。
_PLUGIN_PATH_VARS = ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH")


def sanitize_plugin_path() -> tuple[str, ...]:
    """删除外部 Qt 插件路径环境变量；返回被清掉的变量名（用于断言与日志）。

    幂等：已清理时返回空 tuple。日志只记变量名——路径可能含机器/用户信息，
    不落盘（与仓库代理日志同一条纪律）。
    """
    removed = tuple(name for name in _PLUGIN_PATH_VARS if name in os.environ)
    for name in removed:
        os.environ.pop(name, None)
    if removed:
        log.debug("已清除外部 Qt 插件路径变量（防 ABI 污染）: %s", ",".join(removed))
    return removed
