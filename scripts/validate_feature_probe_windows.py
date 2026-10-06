"""Real Windows canary gate. Creates and cleans only uniquely owned fixtures.

Normal-process execution below is an explicit positive test control, NEVER a
fallback for package execution. No actual package is executed by this script.
"""

from __future__ import annotations

import argparse
import base64
import ctypes as C
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from ctypes import wintypes as W
from pathlib import Path

from pet.feature_probe_windows import ProbeLimits, TrustedProbeBundle, WindowsProbeLauncher, _Win32, cleanup_owned_probe

CANARY_CHECKS = frozenset(
    {
        "ordinary",
        "state",
        "leases",
        "all_apps",
        "user_directory",
        "helper_read",
        "candidate_read",
        "scratch_rw",
        "helper_writable",
        "readonly_writable",
        "ipv4",
        "ipv6",
        "credential_read",
        "registry_read",
        "dpapi_read",
        "process_vm_handle",
        "desktop_access",
        "child_process",
        "inherited_file_access",
    }
)


def check_canary_results(positive, negative):
    assert set(positive) == set(negative) == CANARY_CHECKS, "incomplete canary matrix"
    assert all(type(value) is bool and value for value in positive.values()), "positive control failed"
    expected = {name: name in {"helper_read", "candidate_read", "scratch_rw"} for name in CANARY_CHECKS}
    assert all(type(value) is bool for value in negative.values()), "nonboolean evidence"
    assert negative == expected, {"expected": expected, "actual": negative}


def check_network_results(positive, negative):
    assert set(positive) == set(negative) == {"ipv4", "ipv6"}, "incomplete native network matrix"
    for name in ("ipv4", "ipv6"):
        good, denied = positive[name], negative[name]
        assert good == {"api": "windows.winsock2", "stage": "connect", "error": 0, "connect_attempted": True}, "network positive control failed"
        assert denied.get("api") == "windows.winsock2", "network boundary not reached"
        # WSAStartup's actual native system-call denial is a distinct result:
        # do NOT label it a connect() / firewall denial. Loader/import failures
        # or an unavailable service cannot satisfy this gate.
        if denied.get("stage") == "initialize":
            assert denied.get("error") in {10013, 10107} and denied.get("connect_attempted") is False, "network boundary not reached"
        else:
            assert denied.get("stage") in {"create", "connect"} and denied.get("error") == 10013, "network boundary not reached"
            assert denied.get("connect_attempted") is (denied["stage"] == "connect"), "network stage evidence conflict"


def _network_details(stderr):
    values = [json.loads(line)["canary_network"] for line in stderr.splitlines() if b'"canary_network"' in line]
    assert len(values) == 1, "missing or conflicting native network evidence"
    return values[0]


class Blob(C.Structure):
    _fields_ = [("size", W.DWORD), ("data", C.c_void_p)]


class Credential(C.Structure):
    _fields_ = [
        ("flags", W.DWORD),
        ("type", W.DWORD),
        ("target", W.LPWSTR),
        ("comment", W.LPWSTR),
        ("last_written", W.FILETIME),
        ("blob_size", W.DWORD),
        ("blob", C.c_void_p),
        ("persist", W.DWORD),
        ("attribute_count", W.DWORD),
        ("attributes", C.c_void_p),
        ("alias", W.LPWSTR),
        ("username", W.LPWSTR),
    ]


def _owned(root: Path, bundle: Path, digest: str, name: str):
    stage = root / name
    stage.mkdir()
    copy = stage / "helper"
    shutil.copytree(bundle, copy)
    return stage, WindowsProbeLauncher(TrustedProbeBundle(copy, digest))


def validate(bundle: Path, root: Path):
    import winreg

    bundle = bundle.absolute()
    root = root.absolute()
    root.mkdir(parents=True, exist_ok=False)
    manifest = hashlib.sha256((bundle / "bundle.json").read_bytes()).hexdigest()
    TrustedProbeBundle(bundle, manifest).verify()
    identity = uuid.uuid4().hex
    secret = os.urandom(32)
    credential_target = "DSHPet.Phase4B.Canary." + identity
    registry_path = "Software\\DSHPetPhase4BCanary\\" + identity
    user_file = Path.home() / ("dshpet-phase4b-canary-" + identity + ".bin")
    outside = root / "outside"
    outside.mkdir()
    for name in ("ordinary", "state", "leases", "all_apps"):
        (outside / name).write_bytes(b"owned-nonsecret-canary")
    api = _Win32()
    api.set_acl(outside / "all_apps", f"D:P(A;;FA;;;SY)(A;;FA;;;{api.user_sid()})(A;;FR;;;S-1-15-2-1)")
    adv, crypt, kernel = C.WinDLL("advapi32", use_last_error=True), C.WinDLL("crypt32", use_last_error=True), C.WinDLL("kernel32", use_last_error=True)
    adv.CredWriteW.argtypes = [C.POINTER(Credential), W.DWORD]
    adv.CredWriteW.restype = W.BOOL
    adv.CredDeleteW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD]
    adv.CredDeleteW.restype = W.BOOL
    crypt.CryptProtectData.argtypes = [C.POINTER(Blob), W.LPCWSTR, C.c_void_p, C.c_void_p, C.c_void_p, W.DWORD, C.POINTER(Blob)]
    crypt.CryptProtectData.restype = W.BOOL
    kernel.LocalFree.argtypes = [C.c_void_p]
    secret_buffer = C.create_string_buffer(secret)
    credential = Credential()
    credential.type, credential.target = 1, credential_target
    credential.blob_size, credential.blob, credential.persist = len(secret), C.cast(secret_buffer, C.c_void_p), 1
    created_credential = created_registry = False
    user = C.WinDLL("user32", use_last_error=True)
    user.CreateDesktopW.argtypes = [W.LPCWSTR, W.LPCWSTR, C.c_void_p, W.DWORD, W.DWORD, C.c_void_p]
    user.CreateDesktopW.restype = W.HANDLE
    user.CloseDesktop.argtypes = [W.HANDLE]
    desktop_name = "dshpet-canary-" + identity
    desktop = user.CreateDesktopW(desktop_name, None, None, 0, 0x10000000, None)
    api.checked(desktop, "canary_desktop_create")
    listeners = []
    inherited_handle = None
    evidence = {}
    try:
        with user_file.open("xb") as handle:
            handle.write(b"owned-user-directory-canary")
        api.checked(adv.CredWriteW(C.byref(credential), 0), "canary_credential_create")
        created_credential = True
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, registry_path) as key:
            winreg.SetValueEx(key, "canary", 0, winreg.REG_BINARY, secret)
        created_registry = True
        source, output = Blob(len(secret), C.cast(secret_buffer, C.c_void_p)), Blob()
        api.checked(crypt.CryptProtectData(C.byref(source), "DSH owned canary", None, None, None, 1, C.byref(output)), "canary_dpapi_create")
        try:
            dpapi = base64.b64encode(C.string_at(output.data, output.size)).decode()
        finally:
            kernel.LocalFree(output.data)
        endpoints = []
        for label, family, address in (("ipv4", socket.AF_INET, "127.0.0.1"), ("ipv6", socket.AF_INET6, "::1")):
            listener = socket.socket(family, socket.SOCK_STREAM)
            listener.bind((address, 0))
            listener.listen(8)
            listeners.append(listener)
            endpoints.append((label, family, list(listener.getsockname()[:2])))

        class SA(C.Structure):
            _fields_ = [("length", W.DWORD), ("descriptor", C.c_void_p), ("inherit", W.BOOL)]

        kernel.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.POINTER(SA), W.DWORD, W.DWORD, W.HANDLE]
        kernel.CreateFileW.restype = W.HANDLE
        sa = SA(C.sizeof(SA), None, True)
        inherited_handle = kernel.CreateFileW(str(outside / "ordinary"), 0x80000000, 7, C.byref(sa), 3, 0, None)
        if inherited_handle == C.c_void_p(-1).value:
            raise C.WinError(C.get_last_error())
        stage, launcher = _owned(root, bundle, manifest, "permissions")
        candidate = stage / "candidate"
        candidate.mkdir()
        (candidate / "verified.bin").write_bytes(b"owned-verified-canary")
        policy = {
            "read_paths": {
                **{name: str(outside / name) for name in ("ordinary", "state", "leases", "all_apps")},
                "user_directory": str(user_file),
                "helper_read": str(launcher.bundle.root / "bundle.json"),
                "candidate_read": str(candidate / "verified.bin"),
            },
            "helper_readonly": str(launcher.bundle.root / "bundle.json"),
            "inherited_handle": inherited_handle,
            "readonly": str(candidate / "verified.bin"),
            "scratch": str(stage / "scratch"),
            "network": endpoints,
            "credential": credential_target,
            "registry": registry_path,
            "dpapi": dpapi,
            "owned_process_pid": os.getpid(),
            "desktop_name": desktop_name,
        }
        # Positive controls use generated fixtures only. This is not production execution.
        control_policy = dict(policy, scratch=str(root / "control-scratch"))
        Path(control_policy["scratch"]).mkdir()
        control = subprocess.run(
            [str(bundle / "dsh-feature-probe.exe")],
            input=json.dumps(control_policy).encode() + b"\n",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            close_fds=False,  # Explicit generated inheritable-file positive control only.
        )
        if control.returncode:
            raise AssertionError(f"normal control failed: rc={control.returncode}; {control.stderr[:1024]!r}")
        positive = json.loads(control.stdout)
        evidence["positive_control"] = positive
        if set(positive) != CANARY_CHECKS or not all(type(value) is bool and value for value in positive.values()):
            raise AssertionError(f"normal control could not prove fixture accessibility: {positive}")
        start = time.perf_counter()
        result = launcher.run(stage, input_data=json.dumps(policy).encode() + b"\n")
        evidence["permissions"] = {
            "returncode": result.returncode,
            "reason": result.reason,
            "stdout": result.stdout.decode(errors="replace"),
            "stderr": result.stderr.decode(errors="replace"),
            "isolation_enforced": result.evidence.isolation_enforced,
            "duration_ms": round((time.perf_counter() - start) * 1000, 3),
        }
        print(json.dumps(evidence["permissions"], sort_keys=True), flush=True)
        assert result.returncode == 0 and result.reason is None, evidence["permissions"]
        negative = json.loads(result.stdout)
        check_canary_results(positive, negative)
        check_network_results(_network_details(control.stderr), _network_details(result.stderr))
        evidence["network_positive"] = _network_details(control.stderr)
        evidence["network_negative"] = _network_details(result.stderr)
        evidence["positive_control"] = positive
        evidence["permissions"]["passed"] = True
        for mode in ("crash", "timeout", "overflow", "line-overflow", "input-block", "memory"):
            stage, launcher = _owned(root, bundle, manifest, mode)
            launcher.limits = ProbeLimits(timeout=2 if mode in {"timeout", "input-block"} else 30)
            result = launcher.run(stage, arguments=("--" + mode,), input_data=b"x" * (64 * 1024) if mode == "input-block" else b"")
            evidence[mode] = {
                "returncode": result.returncode,
                "reason": result.reason,
                "stdout": result.stdout[:1000].decode(errors="replace"),
                "stderr": result.stderr[:1000].decode(errors="replace"),
            }
            if mode == "crash":
                assert result.returncode == 27
            elif mode == "memory":
                assert result.returncode == 0 and json.loads(result.stdout)["memory_limit_enforced"]
            else:
                expected_reason = (
                    "probe_timeout" if mode in {"timeout", "input-block"} else "probe_line_limit" if mode == "line-overflow" else "probe_output_limit"
                )
                assert result.reason == expected_reason
            ownership = json.loads((stage / "probe-ownership.json").read_bytes())
            assert ownership["phase"] == "cleaned"
            evidence[mode]["owned_profile_cleaned"] = True
        parent_stage, _ = _owned(root, bundle, manifest, "parent-exit")
        parent = subprocess.run(
            [sys.executable, "-m", "scripts.validate_feature_probe_windows", str(parent_stage / "helper"), str(parent_stage), "--owned-parent-exit"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        assert parent.returncode == 29, parent.stderr[-1000:]
        receipt = json.loads((parent_stage / "parent-exit-ready.json").read_bytes())
        owner = json.loads((parent_stage / "probe-ownership.json").read_bytes())
        assert receipt["owned_probe_pid"] == owner["pid"]
        # Recovery queries the recorded creation identity and NEVER kills a PID.
        assert cleanup_owned_probe(parent_stage, manifest) == "completed"
        assert cleanup_owned_probe(parent_stage, manifest) == "idempotent"
        evidence["parent_exit"] = {"parent_returncode": 29, "owned_child_released": True, "profile_recovered": True}
        evidence["status"] = "passed"
    finally:
        if inherited_handle is not None:
            api.close(inherited_handle)
        user.CloseDesktop(desktop)
        for listener in listeners:
            listener.close()
        if created_credential:
            api.checked(adv.CredDeleteW(credential_target, 1, 0), "canary_credential_cleanup")
        if created_registry:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, registry_path)
        if user_file.exists():
            user_file.unlink()  # Exact unique fixture, not a directory cleanup.
        # No plaintext secrets, DPAPI blobs, credential target or actual registry
        # values are written to evidence. Only boolean results/reason codes.
        (root / "evidence.json").write_text(json.dumps(evidence, sort_keys=True, indent=2), encoding="utf-8")
    print(json.dumps({"status": "passed", "evidence": str(root / "evidence.json")}))
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--owned-parent-exit", action="store_true")
    args = parser.parse_args()
    if args.owned_parent_exit:
        digest = hashlib.sha256((args.bundle / "bundle.json").read_bytes()).hexdigest()

        def ready(line):
            receipt = json.loads(line)
            (args.output / "parent-exit-ready.json").write_text(json.dumps(receipt), encoding="utf-8")
            os._exit(29)  # Deliberately crash this newly created test parent only.

        WindowsProbeLauncher(TrustedProbeBundle(args.bundle, digest)).run(args.output, arguments=("--ready",), on_stdout_line=ready)
        raise SystemExit("owned parent-exit canary failed")
    validate(args.bundle, args.output)
