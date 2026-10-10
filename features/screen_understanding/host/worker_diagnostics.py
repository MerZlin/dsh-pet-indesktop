"""Bounded startup diagnostics; stderr is not a safe log or display payload."""

from __future__ import annotations

import re
from collections.abc import Mapping


def safe_diagnostic(detail):
    raw = detail if isinstance(detail, Mapping) else {}
    result = {k: raw[k] for k in ("exit_code", "generation", "restart_count", "pending") if type(raw.get(k)) is int}
    for key in ("reason", "code"):
        if raw.get(key) in {
            "worker_runtime_boundary",
            "worker_runtime_unavailable",
            "worker_package_invalid",
            "worker_authorization_unavailable",
            "worker_launch_failed",
            "handshake timeout",
            "heartbeat timeout",
            "worker exited",
            "external launch validation failed",
            "request_timeout",
            "worker_stopped",
            "worker_crashed",
            "restart_scheduled",
        }:
            result[key] = raw[key]
    match = re.search(r"No module named ['\"]([a-zA-Z_][a-zA-Z0-9_.]{0,127})['\"]", str(raw.get("line", ""))[:2000])
    if match:
        result["missing_module"] = match.group(1)
    if isinstance(raw.get("missing_module"), str) and re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_.]{0,127}", raw["missing_module"]):
        result["missing_module"] = raw["missing_module"]
    return result


def failure_hint(detail):
    code = detail.get("code")
    if code == "worker_runtime_boundary":
        return "识屏运行目录不符合当前安装布局；请更新 Core 和识屏功能包后重试。"
    if code == "worker_runtime_unavailable":
        return "识屏运行目录不可用；请检查项目 data 目录是否存在及访问权限后重试。"
    if code == "worker_authorization_unavailable":
        return "识屏功能包当前不可执行；请检查是否已启用或正在更新，稍后重试。"
    if code == "worker_launch_failed":
        return "识屏 Worker 未能启动；请检查程序文件及安全软件拦截后重试。"
    if detail.get("missing_module"):
        return "识屏 Worker 缺少模块 " + detail["missing_module"] + "；请安装新版识屏功能包后重试。"
    if detail.get("reason") == "heartbeat timeout":
        return "识屏 Worker 响应超时；请稍后重试，仍失败时检查系统负载与安全软件拦截。"
    if detail.get("reason") == "handshake timeout":
        return "识屏 Worker 握手超时；请检查安全软件拦截，稍后重试。"
    if code == "worker_package_invalid" or detail.get("reason") == "external launch validation failed":
        return "识屏 Worker 安装身份校验失败；请重新导入完整功能包。"
    return "识屏 Worker 启动或运行失败；请重试，仍失败时重新安装新版功能包。"
