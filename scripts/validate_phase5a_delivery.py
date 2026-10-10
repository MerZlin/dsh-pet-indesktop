"""Production Phase5A acceptance with owned processes and real management UI.

Does not write state, load factories, collect desktop screenshots or kill Core.
Failure evidence is retained, and the caller must handle blocked natural exits.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import subprocess
import threading
import time
from ctypes import wintypes
from dataclasses import asdict, dataclass
from pathlib import Path

import psutil

PRODUCT = "dsh-pet-core-webm"
OWNERS = {"official.ai-chat": ("1.0.3", "AI 对话功能包"), "official.screen-understanding": ("1.0.3", "屏幕理解功能包")}
CASES = {"empty": (), "ai": ("official.ai-chat",), "screen": ("official.screen-understanding",), "both": tuple(OWNERS)}
MANAGEMENT_DIALOG_TITLES = frozenset({"安装本地扩展：分别预检和确认", "本地功能包确认", "安装本地官方扩展：分别预检和确认", "本地官方功能包确认"})


def clean_environment(environment: dict[str, str], root: Path) -> dict[str, str]:
    result = {key: value for key, value in environment.items() if not key.upper().startswith(("PYTHON", "DSH_PET", "QT_", "QML", "PYSIDE"))}
    result.update({name: str(root / name) for name in ("APPDATA", "LOCALAPPDATA", "TEMP", "HOME")})
    result.update(TMP=result["TEMP"], USERPROFILE=result["HOME"], QT_QPA_PLATFORM="windows")
    return result


def case_root(root: Path, name: str) -> Path:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name):
        raise ValueError("invalid_case_id")
    return root / name


@dataclass(frozen=True)
class RunIdentity:
    pid: int
    created: float
    executable: str

    def check(self, pid: int, created: float, executable: str) -> None:
        if pid != self.pid or abs(created - self.created) >= 0.001 or Path(executable).resolve() != Path(self.executable).resolve():
            raise RuntimeError("owned_process_identity_mismatch")

    def verify(self) -> None:
        process = psutil.Process(self.pid)
        self.check(process.pid, process.create_time(), process.exe())


def loaded_states(states: dict, expected: dict[str, str]) -> bool:
    return all(
        owner in states
        and states[owner].get("active") == version
        and states[owner].get("enabled") is True
        and states[owner].get("pending_transaction") is None
        and states[owner].get("revision", 0) >= 4
        for owner, version in expected.items()
    )


def check_menu(names: set[str], owners: tuple[str, ...]) -> None:
    if (
        "退出" not in names
        or (("AI 对话" in names) != ("official.ai-chat" in owners))
        or any((name in names) != ("official.screen-understanding" in owners) for name in ("看看屏幕", "主动识屏"))
    ):
        raise RuntimeError("menu_owner_mismatch:" + repr(sorted(names)))


_UIA = r"""param([Int64]$Handle,[int]$TargetPid,[double]$Created,[string]$Executable,[string]$Action='', [string]$Owner='')
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$process=Get-Process -Id $TargetPid
$timestamp=([DateTimeOffset]$process.StartTime.ToUniversalTime()).ToUnixTimeMilliseconds()/1000.0
if([Math]::Abs($timestamp-$Created) -gt .01 -or $process.Path -ne $Executable){throw 'owned_process_identity_mismatch'}
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$Handle)
if($null -eq $root -or $root.Current.ProcessId -ne $TargetPid -or $root.Current.NativeWindowHandle -ne $Handle){throw 'owned_window_identity_mismatch'}
$all=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
$rows=@();$chosen=@()
foreach($element in $all){
 if($element.Current.ProcessId -ne $TargetPid){throw 'foreign_process_element'}
 $type=$element.Current.ControlType.ProgrammaticName;$name=$element.Current.Name;$scope='';$parent=$element
 for($depth=0;$depth -lt 20 -and $null -ne $parent;$depth++){
  if($parent.Current.ProcessId -ne $TargetPid){throw 'foreign_scope'}
  if($parent.Current.Name -like '扩展管理：*'){$scope=$parent.Current.Name.Substring(5);break}
  if($parent -eq $root){break}
  $parent=[System.Windows.Automation.TreeWalker]::ControlViewWalker.GetParent($parent)
 }
 $rows+=@{type=$type;name=$name;owner=$scope;enabled=$element.Current.IsEnabled;offscreen=$element.Current.IsOffscreen}
 if($Action -ne '' -and $name -eq $Action -and $element.Current.IsEnabled -and ($Owner -eq '' -or $scope -eq $Owner) -and $type -in @('ControlType.Button','ControlType.MenuItem')){$chosen+=,$element}
}
if($Action -ne ''){
 if($Action -notin @('确认本次操作','退出','停用','启用','卸载','安全重试','显示桌宠','隐藏桌宠')){throw 'action_not_allowed'}
 if($chosen.Count -ne 1){throw 'owned_action_not_unique'}
 $chosen[0].GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
}
@{pid=$TargetPid;hwnd=$Handle;name=$root.Current.Name;action=$Action;controls=$rows}|ConvertTo-Json -Depth 8 -Compress
"""


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class OwnedUI:
    def __init__(self, process: subprocess.Popen, script: Path, environment: dict[str, str], case: Path):
        self.process, self.script, self.environment, self.case = process, script, environment, case
        self.identity = RunIdentity(process.pid, psutil.Process(process.pid).create_time(), psutil.Process(process.pid).exe())
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        self.user.EnumWindows.argtypes = [self.callback, wintypes.LPARAM]
        self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        self.user.IsWindowVisible.argtypes = [wintypes.HWND]
        self.user.SetForegroundWindow.argtypes = [wintypes.HWND]
        self.user.GetForegroundWindow.restype = wintypes.HWND
        self.user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.history: list[dict] = []

    def windows(self) -> list[dict]:
        self.identity.verify()
        rows = []

        @self.callback
        def visit(handle, _):
            pid = wintypes.DWORD()
            self.user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
            if pid.value == self.identity.pid:
                title, cls, rect = ctypes.create_unicode_buffer(512), ctypes.create_unicode_buffer(256), wintypes.RECT()
                self.user.GetWindowTextW(handle, title, 512)
                self.user.GetClassNameW(handle, cls, 256)
                self.user.GetWindowRect(handle, ctypes.byref(rect))
                rows.append(
                    dict(
                        hwnd=int(handle),
                        title=title.value,
                        cls=cls.value,
                        visible=bool(self.user.IsWindowVisible(handle)),
                        rect=[rect.left, rect.top, rect.right, rect.bottom],
                    )
                )
            return True

        self.user.EnumWindows(visit, 0)
        return rows

    def action(self, handle: int, name: str = "", owner: str = "") -> dict:
        self.identity.verify()
        if handle not in {row["hwnd"] for row in self.windows()}:
            raise RuntimeError("owned_window_identity_mismatch")
        powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        command = [
            str(powershell),
            "-NoProfile",
            "-NonInteractive",
            "-STA",
            "-File",
            str(self.script),
            "-Handle",
            str(handle),
            "-TargetPid",
            str(self.identity.pid),
            "-Created",
            str(self.identity.created),
            "-Executable",
            self.identity.executable,
        ]
        if name:
            command += ["-Action", name]
        if owner:
            command += ["-Owner", owner]
        result = subprocess.run(
            command, cwd=self.case, env=self.environment, capture_output=True, timeout=25, close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW
        )
        if result.returncode:
            raise RuntimeError("uia_failed:" + result.stderr.decode("utf-8", errors="replace")[:600])
        view = json.loads(result.stdout.decode("utf-8-sig"))
        self.history.append(view)
        return view

    def post(self, handle: int, message: int, wparam: int, lparam: int) -> None:
        self.identity.verify()
        if handle not in {row["hwnd"] for row in self.windows()}:
            raise RuntimeError("owned_window_identity_mismatch")
        if not self.user.PostMessageW(handle, message, wparam, lparam):
            raise ctypes.WinError(ctypes.get_last_error())

    def close_owned_windows(self) -> None:
        """Gracefully close only windows belonging to this verified process."""
        for row in self.windows():
            if row["visible"]:
                self.post(row["hwnd"], 0x0010, 0, 0)  # WM_CLOSE

    def activate(self, handle: int) -> None:
        self.identity.verify()
        if handle not in {row["hwnd"] for row in self.windows()}:
            raise RuntimeError("owned_window_identity_mismatch")
        if not self.user.SetForegroundWindow(handle) or self.user.GetForegroundWindow() != handle:
            raise RuntimeError("owned_foreground_activation_refused")

    def request_context_menu(self, handle: int) -> None:
        # Qt accepts the keyboard-semantic context-menu message. Do not require
        # foreground activation: the frozen pet window deliberately uses
        # WS_EX_NOACTIVATE, and the driver must not steal the user's focus.
        self.identity.verify()
        if handle not in {row["hwnd"] for row in self.windows()}:
            raise RuntimeError("owned_window_identity_mismatch")
        self.post(handle, 0x007B, handle, -1)


def read_states(case: Path, owners: tuple[str, ...]) -> dict:
    states = {}
    for owner in owners:
        path = case / "APPDATA" / PRODUCT / "plugins" / owner / "state.json"
        if path.exists():
            states[owner] = json.loads(path.read_text(encoding="utf-8"))
    return states


def ready_for_next_confirmation(confirmed: tuple[str, ...] | set[str], states: dict) -> bool:
    """Avoid competing accepted applies across the shared management lock."""
    return all(states.get(owner, {}).get("pending_transaction") for owner in confirmed)


def launch(core: Path, args: list[str], case: Path, script: Path, label: str):
    environment = clean_environment(dict(os.environ), case)
    for name in ("APPDATA", "LOCALAPPDATA", "TEMP", "HOME"):
        (case / name).mkdir(exist_ok=True)
    log = (case / (label + ".log")).open("xb")
    process = subprocess.Popen(
        [str(core), *args], cwd=case, env=environment, stdout=log, stderr=subprocess.STDOUT, close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW
    )
    ui = OwnedUI(process, script, environment, case)
    write_json(case / (label + "-identity.json"), asdict(ui.identity))
    return process, ui, log


def install(core: Path, packages: Path, case: Path, owners: tuple[str, ...], script: Path) -> dict:
    process, ui, log = launch(core, ["--install-local-packages", str(packages), *owners], case, script, "install")
    wait, confirmed, states = threading.Event(), set[str](), {}
    deadline = time.monotonic() + 420
    result: dict[str, object] = {"status": "failed"}
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("management_early_exit:" + str(process.returncode))
            matches = [row for row in ui.windows() if row["title"] in MANAGEMENT_DIALOG_TITLES and row["visible"]]
            if len(matches) == 1:
                handle = matches[0]["hwnd"]
                view = ui.action(handle)
                states = read_states(case, owners)
                for owner in owners:
                    label = OWNERS[owner][1]
                    if (
                        owner not in confirmed
                        and ready_for_next_confirmation(confirmed, states)
                        and any(row["name"] == "确认本次操作" and row["owner"] == label and row["enabled"] for row in view["controls"])
                    ):
                        ui.action(handle, "确认本次操作", label)
                        confirmed.add(owner)
                        print("CONFIRMED", owner, flush=True)
                        break
                states = read_states(case, owners)
                if len(states) == len(owners) and all(
                    states[owner].get("active") == OWNERS[owner][0] and states[owner].get("pending_transaction") for owner in owners
                ):
                    ui.post(handle, 0x0010, 0, 0)  # normal close of the owned confirmation dialog
                    code = process.wait(timeout=60)
                    if code != 3:
                        raise RuntimeError("management_exit_not_pending:" + str(code))
                    result = dict(status="awaiting_startup_confirmation", states=states, exit_code=code)
                    return result
            wait.wait(0.5)
        raise RuntimeError("management_confirmation_timeout")
    except Exception as error:
        result.update(reason=str(error), live=process.poll() is None, states=states)
        raise
    finally:
        if process.poll() is None:
            try:
                ui.close_owned_windows()
                process.wait(timeout=15)
            except (RuntimeError, subprocess.TimeoutExpired):
                pass
        write_json(case / "install-receipt.json", result)
        write_json(case / "install-uia.json", ui.history)
        log.close()


def normal(core: Path, case: Path, owners: tuple[str, ...], script: Path) -> dict:
    started = time.perf_counter()
    process, ui, log = launch(core, [], case, script, "normal")
    wait, states = threading.Event(), {}
    result: dict[str, object] = {"status": "failed"}
    expected = {owner: OWNERS[owner][0] for owner in owners}
    deadline, menu_requested = time.monotonic() + 160, False
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("ordinary_core_early_exit:" + str(process.returncode))
            states = read_states(case, owners)
            rows = ui.windows()
            write_json(case / "normal-windows.json", rows)
            if loaded_states(states, expected):
                for row in rows:
                    if not row["visible"] or "QWindowPopup" not in row["cls"]:
                        continue
                    view = ui.action(row["hwnd"])
                    names = {control["name"] for control in view["controls"] if control["type"] == "ControlType.MenuItem"}
                    if "退出" in names:
                        check_menu(names, owners)
                        metrics = psutil.Process(process.pid)
                        result.update(
                            rss_bytes=metrics.memory_info().rss, threads=metrics.num_threads(), io=metrics.io_counters()._asdict(), menu=sorted(names)
                        )
                        ui.action(row["hwnd"], "退出")
                        code = process.wait(timeout=75)
                        if code != 0:
                            raise RuntimeError("normal_exit_failed:" + str(code))
                        result.update(status="passed", exit_code=code, states=states, settings_opened=False, production_core=True)
                        return result
                pets = [row for row in rows if row["visible"] and row["title"] == PRODUCT and row["rect"][3] - row["rect"][1] > 100]
                if not menu_requested and len(pets) == 1:
                    result["startup_ms"] = (time.perf_counter() - started) * 1000
                    result["startup_boundary"] = "visible_pet_and_selected_pending_cleared"
                    ui.request_context_menu(pets[0]["hwnd"])
                    menu_requested = True
                    print("OWNED_NATIVE_CONTEXT_MENU_REQUEST", process.pid, pets[0]["hwnd"], flush=True)
            wait.wait(0.3)
        raise RuntimeError("owned_menu_or_load_timeout")
    except Exception as error:
        result.update(reason=str(error), live=process.poll() is None, states=states)
        raise
    finally:
        if process.poll() is None:
            try:
                ui.close_owned_windows()
                process.wait(timeout=15)
            except (RuntimeError, subprocess.TimeoutExpired):
                pass
        write_json(case / "normal-receipt.json", result)
        write_json(case / "normal-uia.json", ui.history)
        log.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--packages", required=True, type=Path)
    parser.add_argument("--cases", nargs="+", choices=tuple(CASES), default=list(CASES))
    args = parser.parse_args(argv)
    root, core, packages = args.run_root.resolve(strict=True), args.core.resolve(strict=True), args.packages.resolve(strict=True)
    if not (root / "ownership.json").is_file():
        raise RuntimeError("owned_run_marker_missing")
    script = root / "owned-uia.ps1"
    if script.exists():
        if script.read_text(encoding="utf-8-sig") != _UIA:
            raise RuntimeError("owned_script_changed")
    else:
        script.write_text(_UIA, encoding="utf-8-sig")
    results = {}
    for name in args.cases:
        case = case_root(root, name)
        case.mkdir(exist_ok=False)
        owners = CASES[name]
        try:
            if owners:
                install(core, packages, case, owners, script)
            results[name] = normal(core, case, owners, script)
        except Exception as error:
            results[name] = dict(status="failed", reason=str(error))
            write_json(root / "frozen-results.json", results)
            return 1  # do not accumulate live failed Core processes
        write_json(root / "frozen-results.json", results)
        print("CASE_PASSED", name, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
