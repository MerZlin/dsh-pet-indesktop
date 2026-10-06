"""Core-owned, fail-closed Windows LPAC launcher. No ordinary-process fallback.

This module does not grant permission to arbitrary executables. Only a bundle
whose manifest digest is pinned by the trusted caller can be launched. Native
permissions must additionally pass the canary gate before production use.
"""

from __future__ import annotations

import ctypes as C
import hashlib
import json
import os
import re
import stat
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .plugins.package_trust import FeaturePackageVerifier


class ProbeLaunchError(RuntimeError):
    def __init__(self, reason: str, winerror: int | None = None):
        self.reason = reason
        self.winerror = winerror
        super().__init__(reason if winerror is None else f"{reason}: winerror={winerror}")


class ProbeOwnership:
    """Write-before-create ownership evidence; candidate scratch cannot edit it."""

    def __init__(self, root: Path, bundle_digest: str):
        self.path = root.absolute() / "probe-ownership.json"
        self.profile = "dshpet.probe." + uuid.uuid4().hex
        self.doc = {"schema": 1, "root": str(root.absolute()), "bundle_digest": bundle_digest, "profile": self.profile, "phase": "profile_intent"}
        with self.path.open("xb") as handle:
            handle.write((json.dumps(self.doc, sort_keys=True) + "\n").encode())
            handle.flush()
            os.fsync(handle.fileno())

    def update(self, phase: str, **evidence):
        self.doc.update(phase=phase, **evidence)
        temp = self.path.with_suffix(".tmp")
        with temp.open("xb") as handle:
            handle.write((json.dumps(self.doc, sort_keys=True) + "\n").encode())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, self.path)


class ProbeInputWriter:
    """Own bounded blocking pipe I/O so the parent's timeout remains live."""

    def __init__(self, write, close):
        self._write, self._close = write, close
        self._condition = threading.Condition()
        self._pending: deque[bytes] = deque()
        self._closing = False
        self.finished = threading.Event()
        self.error: Exception | None = None
        self._thread = threading.Thread(target=self._run, name="feature-probe-input", daemon=True)
        self._thread.start()

    def send(self, data: bytes):
        if len(data) > 64 * 1024:
            raise ProbeLaunchError("probe_input_limit")
        with self._condition:
            if self._closing or self.finished.is_set():
                raise ProbeLaunchError("probe_input_closed")
            if len(self._pending) >= 2:
                raise ProbeLaunchError("probe_input_backpressure")
            self._pending.append(bytes(data))
            self._condition.notify()

    def finish(self):
        with self._condition:
            self._closing = True
            self._condition.notify()

    def abort(self):
        with self._condition:
            self._pending.clear()
            self._closing = True
            self._condition.notify()

    def _run(self):
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._pending or self._closing)
                    if not self._pending:
                        break
                    data = self._pending.popleft()
                self._write(data)
        except Exception as exc:
            self.error = exc
        finally:
            try:
                self._close()
            except Exception as exc:
                if self.error is None:
                    self.error = exc
            self.finished.set()


class ProbeLineBuffer:
    """Bound protocol frames, including the newline, across pipe fragments."""

    def __init__(self):
        self.pending = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self.pending.extend(data)
        lines = []
        while b"\n" in self.pending:
            end = self.pending.index(b"\n")
            if end + 1 > 64 * 1024:
                raise ProbeLaunchError("probe_line_limit")
            lines.append(bytes(self.pending[:end]))
            del self.pending[: end + 1]
        if len(self.pending) > 64 * 1024:
            raise ProbeLaunchError("probe_line_limit")
        return lines


@dataclass(frozen=True)
class ProbeLimits:
    timeout: float = 30.0
    memory_bytes: int = 512 * 1024 * 1024
    output_bytes: int = 256 * 1024

    def __post_init__(self):
        if not 0 < self.timeout <= 120 or not 64 * 1024 * 1024 <= self.memory_bytes <= 512 * 1024 * 1024:
            raise ValueError("invalid probe budget")
        if not 1 <= self.output_bytes <= 256 * 1024:
            raise ValueError("invalid output budget")


@dataclass(frozen=True)
class LaunchEvidence:
    appcontainer: bool
    zero_capabilities: bool
    lpac_access_denied: bool
    win32k_disabled: bool
    job_verified: bool

    @property
    def isolation_enforced(self) -> bool:
        return all((self.appcontainer, self.zero_capabilities, self.lpac_access_denied, self.win32k_disabled, self.job_verified))


@dataclass(frozen=True)
class NativeProbeResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    evidence: LaunchEvidence
    reason: str | None = None


def _safe_relative(name: str) -> str:
    parts = name.replace("\\", "/").split("/")
    if not parts or any(not part or part in (".", "..") or ":" in part for part in parts) or name.startswith(("/", "\\")):
        raise ProbeLaunchError("bundle_path")
    return "/".join(parts)


def _regular_tree(root: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    seen: set[str] = set()
    # Check ancestors as well: a lexically owned directory is not sufficient.
    for ancestor in (root, *root.parents):
        info = ancestor.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ProbeLaunchError("bundle_link")
    for path in root.rglob("*"):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400 or info.st_nlink > 1 and path.is_file():
            raise ProbeLaunchError("bundle_link")
        name = _safe_relative(path.relative_to(root).as_posix())
        if name.casefold() in seen:
            raise ProbeLaunchError("bundle_case_collision")
        seen.add(name.casefold())
        if stat.S_ISREG(info.st_mode):
            paths[name] = path
        elif not stat.S_ISDIR(info.st_mode):
            raise ProbeLaunchError("bundle_file_type")
    return paths


@dataclass(frozen=True)
class TrustedProbeBundle:
    root: Path
    manifest_digest: str

    def verify(self) -> Path:
        root = self.root.absolute()
        inventory = _regular_tree(root)
        manifest = inventory.get("bundle.json")
        if manifest is None or manifest.stat().st_size > 4 * 1024 * 1024:
            raise ProbeLaunchError("bundle_manifest")
        raw = manifest.read_bytes()
        if hashlib.sha256(raw).hexdigest() != self.manifest_digest:
            raise ProbeLaunchError("bundle_integrity")
        try:
            doc = json.loads(raw)
            if set(doc) != {"schema", "entry", "files"} or doc["schema"] != 1 or not isinstance(doc["files"], dict):
                raise ValueError()
            entry = _safe_relative(doc["entry"])
            files = {_safe_relative(name): digest for name, digest in doc["files"].items()}
            if entry not in files or any(not isinstance(d, str) or len(d) != 64 for d in files.values()):
                raise ValueError()
        except (TypeError, ValueError, KeyError) as exc:
            raise ProbeLaunchError("bundle_manifest") from exc
        if set(inventory) != {*files, "bundle.json"}:
            raise ProbeLaunchError("bundle_inventory")
        for name, digest in files.items():
            with inventory[name].open("rb") as handle:
                if hashlib.file_digest(handle, "sha256").hexdigest() != digest:
                    raise ProbeLaunchError("bundle_integrity")
        return root / entry


class _Win32:
    """Documented SDK ABI. DWORD is always 32-bit; pointer fields are native."""

    def __init__(self):
        if os.name != "nt":
            raise ProbeLaunchError("windows_sandbox_unavailable")
        from ctypes import wintypes as W

        self.W = W
        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        self.security = C.WinDLL("advapi32", use_last_error=True)
        self.userenv = C.WinDLL("userenv", use_last_error=True)
        P = C.c_void_p
        D = W.DWORD
        B = W.BOOL
        H = W.HANDLE
        S = W.LPCWSTR

        def bind(dll, name, args, ret):
            fn = getattr(dll, name)
            fn.argtypes, fn.restype = args, ret
            return fn

        self.close = bind(self.kernel, "CloseHandle", [H], B)
        self.open_process = bind(self.kernel, "OpenProcess", [D, B, D], H)
        self.process_times = bind(
            self.kernel, "GetProcessTimes", [H, C.POINTER(W.FILETIME), C.POINTER(W.FILETIME), C.POINTER(W.FILETIME), C.POINTER(W.FILETIME)], B
        )
        self.local_free = bind(self.kernel, "LocalFree", [P], P)
        self.free_sid = bind(self.security, "FreeSid", [P], P)
        self.create_profile = bind(self.userenv, "CreateAppContainerProfile", [S, S, S, P, D, C.POINTER(P)], C.c_long)
        self.delete_profile = bind(self.userenv, "DeleteAppContainerProfile", [S], C.c_long)
        self.sid_string = bind(self.security, "ConvertSidToStringSidW", [P, C.POINTER(P)], B)
        self.open_token = bind(self.security, "OpenProcessToken", [H, D, C.POINTER(H)], B)
        self.duplicate_token = bind(self.security, "DuplicateToken", [H, C.c_int, C.POINTER(H)], B)
        self.access_check = bind(self.security, "AccessCheck", [P, H, D, P, P, C.POINTER(D), C.POINTER(D), C.POINTER(B)], B)
        self.token_info = bind(self.security, "GetTokenInformation", [H, C.c_int, P, D, C.POINTER(D)], B)
        self.current_process = bind(self.kernel, "GetCurrentProcess", [], H)
        self.convert_sd = bind(self.security, "ConvertStringSecurityDescriptorToSecurityDescriptorW", [S, D, C.POINTER(P), P], B)
        self.get_dacl = bind(self.security, "GetSecurityDescriptorDacl", [P, C.POINTER(B), C.POINTER(P), C.POINTER(B)], B)
        self.set_security = bind(self.security, "SetNamedSecurityInfoW", [S, D, D, P, P, P, P], D)
        self.init_attributes = bind(self.kernel, "InitializeProcThreadAttributeList", [P, D, D, C.POINTER(C.c_size_t)], B)
        self.update_attribute = bind(self.kernel, "UpdateProcThreadAttribute", [P, D, C.c_size_t, P, C.c_size_t, P, P], B)
        self.delete_attributes = bind(self.kernel, "DeleteProcThreadAttributeList", [P], None)
        self.create_process = bind(self.kernel, "CreateProcessW", [S, W.LPWSTR, P, P, B, D, P, S, P, P], B)
        self.resume = bind(self.kernel, "ResumeThread", [H], D)
        self.terminate = bind(self.kernel, "TerminateProcess", [H, D], B)
        self.create_job = bind(self.kernel, "CreateJobObjectW", [P, S], H)
        self.set_job = bind(self.kernel, "SetInformationJobObject", [H, C.c_int, P, D], B)
        self.query_job = bind(self.kernel, "QueryInformationJobObject", [H, C.c_int, P, D, C.POINTER(D)], B)
        self.assign_job = bind(self.kernel, "AssignProcessToJobObject", [H, H], B)
        self.in_job = bind(self.kernel, "IsProcessInJob", [H, H, C.POINTER(B)], B)
        self.mitigation = bind(self.kernel, "GetProcessMitigationPolicy", [H, C.c_int, P, C.c_size_t], B)
        self.wait = bind(self.kernel, "WaitForSingleObject", [H, D], D)
        self.exit_code = bind(self.kernel, "GetExitCodeProcess", [H, C.POINTER(D)], B)
        self.create_pipe = bind(self.kernel, "CreatePipe", [C.POINTER(H), C.POINTER(H), P, D], B)
        self.set_handle_info = bind(self.kernel, "SetHandleInformation", [H, D, D], B)
        self.peek = bind(self.kernel, "PeekNamedPipe", [H, P, D, P, C.POINTER(D), P], B)
        self.read = bind(self.kernel, "ReadFile", [H, P, D, C.POINTER(D), P], B)
        self.write = bind(self.kernel, "WriteFile", [H, P, D, C.POINTER(D), P], B)

    @staticmethod
    def checked(ok, stage):
        if not ok:
            raise ProbeLaunchError(stage, C.get_last_error())

    def sid_text(self, sid):
        value = C.c_void_p()
        self.checked(self.sid_string(sid, C.byref(value)), "sid_string")
        try:
            return C.wstring_at(value)
        finally:
            self.local_free(value)

    def process_created(self, handle) -> int:
        creation, exit, kernel, user = (self.W.FILETIME() for _ in range(4))
        self.checked(self.process_times(handle, C.byref(creation), C.byref(exit), C.byref(kernel), C.byref(user)), "probe_process_identity")
        return (creation.dwHighDateTime << 32) | creation.dwLowDateTime

    def token_data(self, token, info_class):
        length = self.W.DWORD()
        self.token_info(token, info_class, None, 0, C.byref(length))
        if not length.value or length.value > 1024 * 1024:
            raise ProbeLaunchError("token_info", C.get_last_error())
        buffer = C.create_string_buffer(length.value)
        self.checked(self.token_info(token, info_class, buffer, length, C.byref(length)), "token_info")
        return buffer

    def lpac_access_check(self, token, app_sid):
        """Verify the actual token's LPAC access semantics before resuming it.

        TokenIsLessPrivilegedAppContainer (SDK enum 46) is rejected by the
        documented GetTokenInformation API on Windows build 26100 (error 87).
        Do not use undocumented NtQueryInformationToken or trust attributes.
        AccessCheck against explicit ACLs proves that the token can access its
        own package SID but not ALL_APPLICATION_PACKAGES. Both checks must
        succeed as kernel calls, with opposite authorization results.
        """
        W = self.W
        duplicate = W.HANDLE()
        self.checked(self.duplicate_token(token, 2, C.byref(duplicate)), "lpac_duplicate_token")

        class Mapping(C.Structure):
            _fields_ = [(key, W.DWORD) for key in ("read", "write", "execute", "all")]

        mapping = Mapping(1, 1, 1, 1)
        try:
            decisions = []
            for sid in (app_sid, "S-1-15-2-1"):
                descriptor = C.c_void_p()
                # Everyone satisfies the unrestricted half of an AppContainer
                # access check; the explicit SID satisfies the restricted half.
                self.checked(self.convert_sd(f"O:SYG:SYD:(A;;0x1;;;WD)(A;;0x1;;;{sid})", 1, C.byref(descriptor), None), "lpac_descriptor")
                try:
                    privileges = C.create_string_buffer(4096)
                    length, granted, authorized = W.DWORD(len(privileges)), W.DWORD(), W.BOOL()
                    self.checked(
                        self.access_check(descriptor, duplicate, 1, C.byref(mapping), privileges, C.byref(length), C.byref(granted), C.byref(authorized)),
                        "lpac_access_check",
                    )
                    decisions.append(bool(authorized.value))
                finally:
                    self.local_free(descriptor)
            return decisions == [True, False]
        finally:
            self.close(duplicate)

    def user_sid(self):
        token = self.W.HANDLE()
        self.checked(self.open_token(self.current_process(), 8, C.byref(token)), "current_token")
        try:
            data = self.token_data(token, 1)
            return self.sid_text(C.cast(data, C.POINTER(C.c_void_p))[0])
        finally:
            self.close(token)

    def acl(self, path: Path, app_sid: str, *, writable=False):
        # Protected DACL, scoped to an owned copy. Never change system/Core ACLs.
        self.set_acl(path, f"D:P(A;;FA;;;SY)(A;;FA;;;{self.user_sid()})(A;;{'FA' if writable else 'FRFX'};;;{app_sid})")

    def set_acl(self, path: Path, sddl: str):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ProbeLaunchError("acl_link")
        descriptor = C.c_void_p()
        self.checked(self.convert_sd(sddl, 1, C.byref(descriptor), None), "acl_descriptor")
        try:
            present, defaulted, dacl = self.W.BOOL(), self.W.BOOL(), C.c_void_p()
            self.checked(self.get_dacl(descriptor, C.byref(present), C.byref(dacl), C.byref(defaulted)), "acl_dacl")
            error = self.set_security(str(path), 1, 0x80000004, None, None, dacl, None)
            if error:
                raise ProbeLaunchError("acl_set", error)
        finally:
            self.local_free(descriptor)


@dataclass(frozen=True)
class VerifiedProbeWorker:
    descriptor: object
    verifier: FeaturePackageVerifier

    def verify(self, owned_root: Path) -> Path:
        from .plugins.package_trust import PackageVerificationError, VerifiedFeatureDescriptor

        descriptor = self.descriptor
        if not isinstance(descriptor, VerifiedFeatureDescriptor) or descriptor.trust_status != "trusted_official":
            raise ProbeLaunchError("worker_verification")
        if not descriptor.root.is_relative_to(owned_root.absolute()) or descriptor.worker_path.suffix.casefold() != ".exe":
            raise ProbeLaunchError("worker_boundary")
        try:
            self.verifier.reverify(descriptor)
        except (OSError, PackageVerificationError) as exc:
            raise ProbeLaunchError("worker_verification") from exc
        return descriptor.worker_path


class WindowsProbeLauncher:
    """Native creation only. Caller must provide owned, immutable run materials.

    The generic seam is used to establish the native canary gate; it is NOT a
    production transaction adapter until that gate and real package probes pass.
    """

    def __init__(self, bundle: TrustedProbeBundle, *, limits: ProbeLimits | None = None):
        self.bundle = bundle
        self.limits = limits or ProbeLimits()

    def run(
        self,
        run_root: Path,
        *,
        input_data: bytes = b"",
        arguments: tuple[str, ...] = (),
        on_stdout_line=None,
        candidate_worker: VerifiedProbeWorker | None = None,
    ) -> NativeProbeResult:
        exe = self.bundle.verify()
        root = run_root.absolute()
        if not root.is_dir() or not self.bundle.root.absolute().is_relative_to(root):
            raise ProbeLaunchError("run_boundary")
        _regular_tree(root)
        if candidate_worker is not None:
            if arguments != ("--feature-package-probe",) or input_data:
                raise ProbeLaunchError("worker_probe_contract")
            exe = candidate_worker.verify(root)
        if len(input_data) > 64 * 1024 or any("\x00" in arg for arg in arguments):
            raise ProbeLaunchError("probe_input")
        scratch = root / "scratch"
        scratch.mkdir(exist_ok=False)
        api = _Win32()
        W = api.W
        ownership = ProbeOwnership(root, self.bundle.manifest_digest)
        profile = ownership.profile
        sid = C.c_void_p()
        error = api.create_profile(profile, profile, "DSH owned installation probe", None, 0, C.byref(sid))
        if error < 0:
            raise ProbeLaunchError("profile_create", error & 0xFFFFFFFF)
        handles = []
        proc = None
        attr = None
        job = None
        resumed = False
        input_writer = None
        try:
            app_sid = api.sid_text(sid)
            ownership.update("profile_created", app_sid=app_sid, writable_paths=[str(scratch)], readonly_root=str(root))
            for path in [root, *root.rglob("*")]:
                api.acl(path, app_sid, writable=path == scratch or path.is_relative_to(scratch))

            # Explicit pipes are the only inherited handles.
            class SA(C.Structure):
                _fields_ = [("length", W.DWORD), ("descriptor", C.c_void_p), ("inherit", W.BOOL)]

            security = SA(C.sizeof(SA), None, True)
            pipes = []
            for _ in range(3):
                reader, writer = W.HANDLE(), W.HANDLE()
                api.checked(api.create_pipe(C.byref(reader), C.byref(writer), C.byref(security), 0), "create_pipe")
                handles.extend([reader, writer])
                pipes.append((reader, writer))
            (child_in, parent_in), (parent_out, child_out), (parent_err, child_err) = pipes
            for handle in (parent_in, parent_out, parent_err):
                api.checked(api.set_handle_info(handle, 1, 0), "pipe_no_inherit")

            class Capabilities(C.Structure):
                _fields_ = [("sid", C.c_void_p), ("capabilities", C.c_void_p), ("count", W.DWORD), ("reserved", W.DWORD)]

            capabilities = Capabilities(sid, None, 0, 0)
            lpac, child_policy = W.DWORD(1), W.DWORD(1)
            mitigation = C.c_ulonglong(1 << 28)  # WIN32K_SYSTEM_CALL_DISABLE_ALWAYS_ON
            inherited = (W.HANDLE * 3)(child_in, child_out, child_err)
            size = C.c_size_t()
            api.init_attributes(None, 5, 0, C.byref(size))
            attr = C.create_string_buffer(size.value)
            api.checked(api.init_attributes(attr, 5, 0, C.byref(size)), "attributes_init")
            for key, value in ((0x20009, capabilities), (0x2000F, lpac), (0x2000E, child_policy), (0x20007, mitigation), (0x20002, inherited)):
                api.checked(api.update_attribute(attr, 0, key, C.byref(value), C.sizeof(value), None, None), "attributes_update")

            class SI(C.Structure):
                _fields_ = [
                    ("cb", W.DWORD),
                    ("reserved", W.LPWSTR),
                    ("desktop", W.LPWSTR),
                    ("title", W.LPWSTR),
                    ("x", W.DWORD),
                    ("y", W.DWORD),
                    ("xs", W.DWORD),
                    ("ys", W.DWORD),
                    ("xc", W.DWORD),
                    ("yc", W.DWORD),
                    ("fill", W.DWORD),
                    ("flags", W.DWORD),
                    ("show", W.WORD),
                    ("reserved2size", W.WORD),
                    ("reserved2", C.c_void_p),
                    ("stdin", W.HANDLE),
                    ("stdout", W.HANDLE),
                    ("stderr", W.HANDLE),
                ]

            class SIX(C.Structure):
                _fields_ = [("startup", SI), ("attributes", C.c_void_p)]

            class PI(C.Structure):
                _fields_ = [("process", W.HANDLE), ("thread", W.HANDLE), ("pid", W.DWORD), ("tid", W.DWORD)]

            class BasicJob(C.Structure):
                _fields_ = [
                    ("process_time", C.c_longlong),
                    ("job_time", C.c_longlong),
                    ("flags", W.DWORD),
                    ("min_working", C.c_size_t),
                    ("max_working", C.c_size_t),
                    ("active_processes", W.DWORD),
                    ("affinity", C.c_size_t),
                    ("priority", W.DWORD),
                    ("scheduling", W.DWORD),
                ]

            class IO(C.Structure):
                _fields_ = [(n, C.c_ulonglong) for n in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

            class ExtendedJob(C.Structure):
                _fields_ = [
                    ("basic", BasicJob),
                    ("io", IO),
                    ("process_memory", C.c_size_t),
                    ("job_memory", C.c_size_t),
                    ("peak_process", C.c_size_t),
                    ("peak_job", C.c_size_t),
                ]

            limits = ExtendedJob()
            limits.basic.flags = 0x2000 | 0x8 | 0x100  # KILL_ON_JOB_CLOSE | ACTIVE_PROCESS | PROCESS_MEMORY, no breakaway
            limits.basic.active_processes = 1
            limits.process_memory = self.limits.memory_bytes
            job = api.create_job(None, None)
            api.checked(job, "job_create")
            api.checked(api.set_job(job, 9, C.byref(limits), C.sizeof(limits)), "job_limits")
            si, pi = SIX(), PI()
            si.startup.cb = C.sizeof(si)
            si.startup.flags = 0x100 | 1  # STARTF_USESTDHANDLES | USESHOWWINDOW
            si.startup.show = 0
            si.startup.stdin, si.startup.stdout, si.startup.stderr = child_in, child_out, child_err
            si.attributes = C.cast(attr, C.c_void_p)
            env = {
                "SystemRoot": os.environ.get("SystemRoot", "C:\\Windows"),
                "TEMP": str(scratch),
                "TMP": str(scratch),
                "USERPROFILE": str(scratch),
                "LOCALAPPDATA": str(scratch),
                "PYTHONNOUSERSITE": "1",
            }
            environment = C.create_unicode_buffer("\0".join(f"{k}={v}" for k, v in sorted(env.items())) + "\0\0")
            command = C.create_unicode_buffer(subprocess.list2cmdline([str(exe), *arguments]))
            api.checked(
                api.create_process(str(exe), command, None, None, True, 0x80000 | 0x400 | 4 | 0x08000000, environment, str(scratch), C.byref(si), C.byref(pi)),
                "process_create",
            )
            proc = pi.process
            ownership.update("process_suspended", pid=pi.pid, created=api.process_created(proc))
            handles.extend([pi.process, pi.thread])
            api.checked(api.assign_job(job, proc), "job_assign")
            member = W.BOOL()
            api.checked(api.in_job(proc, job, C.byref(member)), "job_verify")
            policy = W.DWORD()
            api.checked(api.mitigation(proc, 4, C.byref(policy), C.sizeof(policy)), "mitigation_verify")
            token = W.HANDLE()
            api.checked(api.open_token(proc, 8 | 2, C.byref(token)), "child_token")
            handles.append(token)
            app = C.cast(api.token_data(token, 29), C.POINTER(W.DWORD))[0]  # TokenIsAppContainer
            caps = C.cast(api.token_data(token, 30), C.POINTER(W.DWORD))[0]  # TokenCapabilities count
            token_sid = api.token_data(token, 31)
            if api.sid_text(C.cast(token_sid, C.POINTER(C.c_void_p))[0]) != app_sid:
                raise ProbeLaunchError("token_sid_mismatch")
            actual_job = ExtendedJob()
            api.checked(api.query_job(job, 9, C.byref(actual_job), C.sizeof(actual_job), None), "job_query_limits")
            job_valid = (
                actual_job.basic.flags == limits.basic.flags
                and actual_job.basic.active_processes == 1
                and actual_job.process_memory == self.limits.memory_bytes
            )
            lpac_actual = api.lpac_access_check(token, app_sid)
            evidence = LaunchEvidence(bool(app), caps == 0, bool(lpac_actual), bool(policy.value & 1), bool(member) and job_valid)
            if not evidence.isolation_enforced:
                raise ProbeLaunchError("isolation_verification")
            ownership.update("isolation_verified", evidence=evidence.__dict__, memory_limit=self.limits.memory_bytes, timeout=self.limits.timeout)
            if candidate_worker is not None:
                candidate_worker.verify(root)
            if api.resume(pi.thread) == 0xFFFFFFFF:
                raise ProbeLaunchError("thread_resume", C.get_last_error())
            resumed = True
            ownership.update("running")
            for handle in (child_in, child_out, child_err):
                api.close(handle)
                handles.remove(handle)
            deadline = time.monotonic() + self.limits.timeout

            def write_input(data):
                written = W.DWORD()
                api.checked(api.write(parent_in, data, len(data), C.byref(written), None), "probe_write")
                if written.value != len(data):
                    raise ProbeLaunchError("probe_short_write")

            # The writer alone owns this handle. Killing the owned Job unblocks
            # WriteFile when a hostile candidate never consumes its stdin.
            input_writer = ProbeInputWriter(write_input, lambda: api.close(parent_in))
            handles.remove(parent_in)
            if input_data:
                input_writer.send(input_data)
            if on_stdout_line is None:
                input_writer.finish()
            outputs = [bytearray(), bytearray()]
            frames = ProbeLineBuffer()
            reason = None
            while True:
                for index, handle in enumerate((parent_out, parent_err)):
                    available = W.DWORD()
                    if api.peek(handle, None, 0, None, C.byref(available), None) and available.value:
                        length = min(available.value, 64 * 1024, self.limits.output_bytes + 1)
                        data, count = C.create_string_buffer(length), W.DWORD()
                        api.checked(api.read(handle, data, length, C.byref(count), None), "probe_read")
                        chunk = data.raw[: count.value]
                        outputs[index].extend(chunk)
                        if index == 0:
                            try:
                                lines = frames.feed(chunk)
                            except ProbeLaunchError as exc:
                                reason = exc.reason
                                break
                            if on_stdout_line is not None:
                                for line in lines:
                                    reply = on_stdout_line(line)
                                    if reply:
                                        input_writer.send(reply)
                if sum(map(len, outputs)) > self.limits.output_bytes:
                    reason = "probe_output_limit"
                    break
                if reason:
                    break
                if input_writer.error is not None:
                    reason = "probe_input_failed"
                    break
                done = api.wait(proc, 20) == 0
                if done:
                    remaining = W.DWORD()
                    if not any(api.peek(h, None, 0, None, C.byref(remaining), None) and remaining.value for h in (parent_out, parent_err)):
                        break
                if time.monotonic() > deadline:
                    reason = "probe_timeout"
                    break
            if reason:
                api.close(job)
                job = None
                api.wait(proc, 5000)
            code = W.DWORD()
            api.checked(api.exit_code(proc, C.byref(code)), "probe_exit_code")
            ownership.update("process_exited", returncode=code.value, reason=reason)
            return NativeProbeResult(code.value, bytes(outputs[0]), bytes(outputs[1]), evidence, reason)
        finally:
            # Even failures between CreateProcess and AssignJob terminate only
            # the suspended process created by this invocation, never a user process.
            if proc and not resumed:
                api.terminate(proc, 1)
            if job:
                api.close(job)
            released = not proc or api.wait(proc, 5000) == 0
            for handle in reversed(handles):
                api.close(handle)
            try:
                phase = ownership.doc["phase"]
                assert isinstance(phase, str)  # Parent-owned ProbeOwnership invariant.
                ownership.update(phase, process_released=released)
            finally:
                _finish_probe_resources(api, ownership, attr, sid, profile, input_writer)


def _finish_probe_resources(api, ownership, attr, sid, profile, input_writer):
    """Try every independent cleanup even when a prior cleanup fails."""
    errors = []
    if input_writer is not None:
        input_writer.abort()
        if not input_writer.finished.wait(5):
            errors.append(ProbeLaunchError("probe_input_cleanup"))
    for action, value in ((api.delete_attributes, attr), (api.free_sid, sid)):
        if value is not None:
            try:
                action(value)
            except Exception:
                errors.append(ProbeLaunchError("probe_native_cleanup"))
    try:
        error = api.delete_profile(profile)
        if error < 0:
            errors.append(ProbeLaunchError("profile_cleanup", error & 0xFFFFFFFF))
    except Exception:
        errors.append(ProbeLaunchError("profile_cleanup"))
    if errors:
        ownership.update("cleanup_required", cleanup_reason=errors[0].reason, cleanup_error=errors[0].winerror)
        raise errors[0]
    ownership.update("cleaned")


def cleanup_owned_probe(run_root: Path, bundle_digest: str) -> str:
    """Recover only a recorded profile, never a process or arbitrary directory."""
    root = run_root.absolute()
    inventory = _regular_tree(root)
    path = inventory.get("probe-ownership.json")
    if path is None or path.stat().st_size > 64 * 1024:
        raise ProbeLaunchError("probe_ownership")
    try:
        doc = json.loads(path.read_bytes())
        if (
            doc.get("schema") != 1
            or doc.get("root") != str(root)
            or doc.get("bundle_digest") != bundle_digest
            or not re.fullmatch(r"dshpet\.probe\.[0-9a-f]{32}", doc.get("profile", ""))
        ):
            raise ValueError()
        TrustedProbeBundle(root / "helper", bundle_digest).verify()
    except (ValueError, TypeError, AttributeError) as exc:
        raise ProbeLaunchError("probe_ownership") from exc
    api = None
    pid, created = doc.get("pid"), doc.get("created")
    if pid is None and (created is not None or doc.get("process_released") is not True and doc.get("phase") != "profile_intent"):
        # profile_created can straddle CreateProcess before the PID receipt.
        # Without an actual returned-parent release receipt, do not guess.
        raise ProbeLaunchError("probe_release_unproven")
    if pid is not None:
        if type(pid) is not int or type(created) is not int or pid < 1 or created < 1:
            raise ProbeLaunchError("probe_ownership_identity")
        api = _Win32()
        handle = api.open_process(0x100000 | 0x1000, False, pid)  # synchronize/query limited; never terminate
        if handle:
            try:
                if api.process_created(handle) == created:
                    wait = api.wait(handle, 0)
                    if wait == 258:
                        return "awaiting_release"
                    if wait != 0:
                        raise ProbeLaunchError("probe_recovery_process", C.get_last_error())
            finally:
                api.close(handle)
        elif C.get_last_error() != 87:  # Original PID absent is distinct from access denied.
            raise ProbeLaunchError("probe_recovery_process", C.get_last_error())
    if doc.get("phase") == "cleaned":
        return "idempotent"
    api = api or _Win32()
    result = api.delete_profile(doc["profile"])
    if result < 0 and result & 0xFFFFFFFF != 0x80070002:
        raise ProbeLaunchError("profile_cleanup", result & 0xFFFFFFFF)
    owner = object.__new__(ProbeOwnership)
    owner.path, owner.doc, owner.profile = path, doc, doc["profile"]
    owner.update("cleaned", recovered=True)
    return "completed"
