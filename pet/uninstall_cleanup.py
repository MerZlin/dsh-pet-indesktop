# -*- coding: utf-8 -*-
"""卸载清理：删除自启项、移除 Claude hooks、卸载 DSH 桥接插件。

供 `--uninstall-cleanup` 参数使用——安装包（Inno Setup）卸载时调用。
该路径不启动 QApplication/事件循环，只做必要的清理，便于「无 Qt 依赖路径」
（仍可 import Qt 模块，但不建窗口）下执行。
"""

from __future__ import annotations

import json


def other_instances_use_agent(config, agent_key: str) -> bool:
    """<base> 下其他变体 / 多开实例是否仍开启该 Agent 联动。

    DSH 桥接插件目录与变体无关（<base>/dsh-pet-bridge），卸载前必须确认
    没有任何变体或实例还在用它，否则保留插件。
    """
    base = config.dir.parent
    try:
        candidates = list(base.glob("dsh-pet-standalone*/config*.json"))
    except OSError:
        return False
    for f in candidates:
        if config.path and f == config.path:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and bool((data.get("agent_link") or {}).get(agent_key, False)):
            return True
    return False


def run_uninstall_cleanup(config=None) -> dict:
    """执行卸载清理各步骤，返回结果字典（供测试与日志）。

    步骤：
    1. 删除当前变体开机自启项（autostart.disable）；
    2. 移除 Claude hooks（ClaudeCodeMonitor.uninstall_hooks）；
    3. 若无其他实例使用 DSH 联动，卸载 DSH 桥接插件（DshMonitor.uninstall_bridge）。
    """
    from . import autostart

    if config is None:
        from .config import Config
        config = Config()

    results = {"autostart": bool(autostart.disable())}

    from .agent_link import ClaudeCodeMonitor, DshMonitor

    results["claude_hooks"] = bool(ClaudeCodeMonitor.uninstall_hooks())

    if other_instances_use_agent(config, "dsh"):
        results["dsh_bridge"] = "skipped"
    else:
        results["dsh_bridge"] = bool(DshMonitor.uninstall_bridge())

    return results
