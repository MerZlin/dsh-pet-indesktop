"""Trusted, Qt-free native canary helper. Only accesses parent-supplied fixtures.

No desktop screenshot, credential enumeration, or model request. This is test
material; production candidate execution remains disabled until the full gate.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import winreg
from pathlib import Path


def network_access(family, endpoint):
    try:
        import socket
    except (ImportError, OSError) as exc:
        return False, {"stage": "initialize", "error": type(exc).__name__}
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(2)
            sock.connect(tuple(endpoint))
        return True, {"stage": "connect", "error": None}
    except OSError as exc:
        return False, {"stage": "connect", "error": getattr(exc, "winerror", None) or exc.errno}


def evaluate(policy):
    result = {}
    for label, value in policy["read_paths"].items():
        try:
            with open(value, "rb") as handle:
                handle.read(1)  # Never emit fixture contents.
            result[label] = True
        except OSError:
            result[label] = False
    try:
        with open(policy["helper_readonly"], "ab"):
            pass
        result["helper_writable"] = True
    except OSError:
        result["helper_writable"] = False
    try:
        with open(policy["readonly"], "ab"):
            pass  # Opening for mutation must fail; do not mutate trusted helper.
        result["readonly_writable"] = True
    except OSError:
        result["readonly_writable"] = False
    try:
        p = Path(policy["scratch"]) / "canary-owned.txt"
        p.write_bytes(b"owned-canary")
        result["scratch_rw"] = p.read_bytes() == b"owned-canary"
    except OSError:
        result["scratch_rw"] = False
    network_details = {}
    for label, family, endpoint in policy["network"]:
        result[label], network_details[label] = network_access(family, endpoint)
    print(json.dumps({"canary_network": network_details}, sort_keys=True), file=sys.stderr, flush=True)
    import _dsh_probe_native as native

    result["inherited_file_access"] = native.inherited_file_access(policy["inherited_handle"])
    result["credential_read"] = native.credential_read(policy["credential"])
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, policy["registry"], 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, "canary")
        result["registry_read"] = True
    except OSError:
        result["registry_read"] = False
    result["dpapi_read"] = native.dpapi_read(base64.b64decode(policy["dpapi"]))
    result["process_vm_handle"] = native.process_read(policy["owned_process_pid"])
    result["desktop_access"] = native.desktop_access(policy["desktop_name"])
    try:
        completed = subprocess.run([sys.executable, "--child"], capture_output=True, timeout=5)
        result["child_process"] = completed.returncode == 0
    except OSError:
        result["child_process"] = False
    return result


def main():
    if "--child" in sys.argv:
        return 0
    if "--crash" in sys.argv:
        import os

        os._exit(27)
    if "--ready" in sys.argv:
        import os
        import threading

        print(json.dumps({"owned_probe_pid": os.getpid()}), flush=True)
        threading.Event().wait(120)
        return 0
    if "--timeout" in sys.argv or "--input-block" in sys.argv:
        import threading

        threading.Event().wait(120)
        return 0
    if "--memory" in sys.argv:
        try:
            value = bytearray(512 * 1024 * 1024)
        except MemoryError:
            print(json.dumps({"memory_limit_enforced": True}), flush=True)
            return 0
        print(json.dumps({"memory_limit_enforced": False, "allocated_bytes": len(value)}), flush=True)
        return 0
    if "--line-overflow" in sys.argv:
        sys.stdout.buffer.write(b"x" * (64 * 1024 + 1))
        sys.stdout.flush()
        return 0
    if "--overflow" in sys.argv:
        sys.stdout.buffer.write((b"x" * 9999 + b"\n") * 30)
        sys.stdout.flush()
        return 0
    request = json.loads(sys.stdin.buffer.readline(64 * 1024 + 1))
    print(json.dumps(evaluate(request), sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
