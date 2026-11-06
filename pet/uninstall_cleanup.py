# -*- coding: utf-8 -*-
"""卸载清理：删除自启项、移除 Claude hooks、卸载 DSH / AstrBot 桥接插件。

供 `--uninstall-cleanup` 参数使用——安装包（Inno Setup）卸载时调用。两个冻结
入口（`packaging/pet_entry.py` / `pet_entry_no_chat.py`）必须在 import `pet.app`
之前分流到本模块的 :func:`main`，否则参数会当成普通启动、清理根本不会执行。

该路径不启动 QApplication/事件循环，只做必要的清理，便于「无 Qt 依赖路径」
（仍可 import Qt 模块，但不建窗口）下执行。

多实例占用判断由 agent_link 模块统一提供（语义并集：当前变体目录 + <base>
下全部变体任一认为在用即保留），本模块只负责消费。
"""

from __future__ import annotations

import logging
import os
import time

from .agent_link import AstrBotMonitor, ClaudeCodeMonitor, DshMonitor, other_instances_use_agent

# 结果留痕文件名（写在配置目录里）：冻结入口是 windowed exe，没有控制台，
# 只靠 stdout 的话卸载清理的结果在用户机器上等于消失。
RESULT_LOG_NAME = "uninstall-cleanup.log"


def run_uninstall_cleanup(config=None) -> dict:
    """执行卸载清理各步骤，返回结果字典（供测试与日志）。

    步骤：
    1. 删除当前变体开机自启项（autostart.disable）；
    2. 若无其他实例使用 Claude 联动，移除 Claude hooks（ClaudeCodeMonitor.uninstall_hooks）；
    3. 若无其他实例使用 DSH 联动，卸载 DSH 桥接插件（DshMonitor.uninstall_bridge）；
    4. 若无其他实例使用 AstrBot 联动，卸载本配置记住的 AstrBot data 目录里的桥接插件
       （AstrBotMonitor.uninstall_bridge；没记住目录 = 从未安装过，视为完成）。
    """
    from . import autostart

    if config is None:
        from .config import Config
        config = Config()

    results = {"autostart": bool(autostart.disable())}

    # 其他实例仍在使用对应联动则保留（hooks/桥接插件是全局状态）
    astrbot_root = (config.get("agent_link", {}) or {}).get("astrbot_root")
    for agent_key, result_key, uninstaller in (
        ("claude", "claude_hooks", ClaudeCodeMonitor.uninstall_hooks),
        ("dsh", "dsh_bridge", DshMonitor.uninstall_bridge),
        ("astrbot", "astrbot_bridge", lambda: AstrBotMonitor.uninstall_bridge(astrbot_root)),
    ):
        if other_instances_use_agent(config, agent_key):
            results[result_key] = "skipped"
        else:
            results[result_key] = bool(uninstaller())

    return results


def _describe_step(value) -> str:
    if value is True:
        return "ok"
    if value == "skipped":
        return "skipped（其他实例仍在使用，保留）"
    return "failed"


def _format_results(results: dict, error: str) -> list[str]:
    lines = ["dsh-pet 卸载清理结果："]
    if error:
        lines.append(f"  清理执行失败：{error}")
    for step, value in results.items():
        lines.append(f"  {step} = {_describe_step(value)}")
    failed = [step for step, value in results.items() if value is False]
    if error:
        lines.append("  结论：清理异常，见上面错误")
    elif failed:
        lines.append("  结论：有步骤失败（" + "、".join(failed) + "）")
    else:
        lines.append("  结论：全部完成")
    return lines


def _write_result_record(config, lines: list[str], *, ok: bool) -> bool:
    """把本次结果追加进配置目录里的留痕文件；失败只返回 False，不打断清理。"""
    try:
        config.dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(config.dir / RESULT_LOG_NAME, "a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] pid={os.getpid()} exit={0 if ok else 1}\n")
            for line in lines:
                handle.write(f"{line}\n")
    except Exception:
        return False
    return True


def main(config=None) -> int:
    """``--uninstall-cleanup`` 入口：执行清理、输出可诊断结果、返回退出码。

    退出码语义与 ``pet/__main__.py`` 的既有分流一致：任一步骤明确失败（False）
    或清理本身异常即 1，成功与 skipped 为 0——安装器据此判断是否需要提示用户。
    """
    logger = logging.getLogger(__name__)
    if config is None:
        try:
            from .config import Config

            config = Config()
        except Exception as exc:  # 读不到配置也要给出结果与退出码，不能抛 traceback
            lines = _format_results({}, f"配置不可读：{type(exc).__name__}: {exc}")
            for line in lines:
                print(line, flush=True)
            logger.error("卸载清理无法读取配置：%s", exc)
            return 1

    try:
        results = run_uninstall_cleanup(config)
        error = ""
    except Exception as exc:  # 清理崩了也要有结果与退出码，不能只留 traceback
        results = {}
        error = f"{type(exc).__name__}: {exc}"

    failed = [step for step, value in results.items() if value is False]
    lines = _format_results(results, error)
    for line in lines:
        print(line, flush=True)
    if error:
        logger.error("卸载清理异常：%s", error)
    elif failed:
        logger.warning("卸载清理有步骤失败：%s", "、".join(failed))
    else:
        logger.info("卸载清理完成：%s", results)

    _write_result_record(config, lines, ok=not error and not failed)
    return 1 if (error or failed) else 0
