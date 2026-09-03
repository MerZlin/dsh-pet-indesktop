# -*- coding: utf-8 -*-
"""卸载清理：删除自启项、移除 Claude hooks、卸载 DSH 桥接插件。

供 `--uninstall-cleanup` 参数使用——安装包（Inno Setup）卸载时调用。
该路径不启动 QApplication/事件循环，只做必要的清理，便于「无 Qt 依赖路径」
（仍可 import Qt 模块，但不建窗口）下执行。

多实例占用判断由 agent_link 模块统一提供（语义并集：当前变体目录 + <base>
下全部变体任一认为在用即保留），本模块只负责消费。
"""

from __future__ import annotations

from .agent_link import ClaudeCodeMonitor, DshMonitor, other_instances_use_agent


def run_uninstall_cleanup(config=None) -> dict:
    """执行卸载清理各步骤，返回结果字典（供测试与日志）。

    步骤：
    1. 删除当前变体开机自启项（autostart.disable）；
    2. 若无其他实例使用 Claude 联动，移除 Claude hooks（ClaudeCodeMonitor.uninstall_hooks）；
    3. 若无其他实例使用 DSH 联动，卸载 DSH 桥接插件（DshMonitor.uninstall_bridge）。
    """
    from . import autostart

    if config is None:
        from .config import Config
        config = Config()

    results = {"autostart": bool(autostart.disable())}

    # 其他实例仍在使用对应联动则保留（hooks/桥接插件是全局状态）
    for agent_key, result_key, uninstaller in (
        ("claude", "claude_hooks", ClaudeCodeMonitor.uninstall_hooks),
        ("dsh", "dsh_bridge", DshMonitor.uninstall_bridge),
    ):
        if other_instances_use_agent(config, agent_key):
            results[result_key] = "skipped"
        else:
            results[result_key] = bool(uninstaller())

    return results
