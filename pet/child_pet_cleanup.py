# -*- coding: utf-8 -*-
"""一键退出子肥鱼：关闭所有小肥鱼进程并清理 runtime 标记。

只退出子肥鱼进程/窗口（相当于一键退出其他所有子肥鱼），**不删除**它们的
slot 配置、会话与待办数据——子肥鱼的设置（含 user_customized 占位）全部
保留，下次生成时按占位语义恢复。主肥鱼（slot-0/config.json）不受影响。
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import slot_manager as slot_manager_mod


def _pid_alive(pid: int) -> bool:
    """跨平台探活：Windows 用 GetExitCodeProcess==STILL_ACTIVE 判定真死活，
    其余用 kill(pid, 0)。

    注意不能用「OpenProcess 能不能打开」当死活：父进程 spawn 子进程后持有
    子进程句柄，子进程被 taskkill 杀掉后其进程对象仍因句柄存活而可被打开
    ——实机事故：杀成功了却被误判存活，白等重试两轮还误报「未能退出」。
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            handle = ctypes.windll.kernel32.OpenProcess(
                0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            try:
                code = wintypes.DWORD()
                if not ctypes.windll.kernel32.GetExitCodeProcess(
                        handle, ctypes.byref(code)):
                    return False
                return code.value == 259  # STILL_ACTIVE
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _terminate_pet_process(pid: int) -> None:
    """终止子肥鱼进程。Windows 使用 taskkill /T /F（CREATE_NO_WINDOW 防 GUI 应用
    每杀一只弹一个空白控制台窗口——实机反馈），POSIX 先 SIGTERM 再补 SIGKILL。

    taskkill 的输出不再吞掉：返回码/stdout/stderr 写日志——实机上出现过
    「taskkill 跑完进程还在」且零线索可查（20:56 事件），失败原因必须可见。
    """
    if os.name == "nt":
        try:
            proc = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True, text=True,
                timeout=5,
                # GUI 进程（无控制台）里起 taskkill 会弹空白控制台窗口
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if proc.returncode != 0:
                logging.warning(
                    "退出子肥鱼：taskkill pid=%d 返回码 %s: %s%s",
                    pid, proc.returncode,
                    (proc.stdout or "").strip(), (proc.stderr or "").strip())
        except Exception:
            logging.exception("退出子肥鱼：taskkill pid=%d 执行异常", pid)
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and _pid_alive(pid):
        time.sleep(0.05)
    if _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _pid_image_path(pid: int) -> str | None:
    """读取进程可执行文件路径（识别 pid 复用：陈旧标记的 pid 可能已被无关
    进程复用，直接 taskkill 会误杀无辜进程或打不动受保护进程——实机事故）。
    读不到（无权限/不支持）返回 None。"""
    if pid <= 0:
        return None
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return None
            try:
                buf = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(1024)
                ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(
                    handle, 0, buf, ctypes.byref(size))
                return buf.value if ok else None
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            return None
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return None


def _is_pet_process(pid: int) -> bool:
    """pid 对应的进程就是本程序（exe 路径与 sys.executable 一致）。

    Windows 读不到镜像路径 = 受保护/无关进程，不放行；POSIX（macOS 无
    /proc）读不到时保持既有行为放行。
    """
    img = _pid_image_path(pid)
    if img is None:
        return os.name != "nt"
    return (os.path.normcase(os.path.normpath(img))
            == os.path.normcase(os.path.normpath(sys.executable)))


def _slot_lock_pids(root: Path) -> list[int]:
    """从 slots/slot-*.lock 的定长 PID 记录读子槽位属主 pid——runtime 标记
    之外的第二枚举源（没写过标记的小肥鱼也持有 slot 锁）。锁文件本身不删。
    """
    pids: list[int] = []
    try:
        locks = sorted((root / "slots").glob("slot-*.lock"))
    except OSError:
        return pids
    for lock in locks:
        if lock.stem == "slot-0":
            continue  # 主肥鱼永不杀
        try:
            raw = lock.read_bytes()[:slot_manager_mod.PID_RECORD_LEN]
            pid = int(raw.decode("ascii", errors="ignore").strip())
        except (OSError, ValueError):
            continue
        if pid > 0:
            pids.append(pid)
    return pids


def clear_spawned_pets(config_dir: Path | str) -> dict:
    """关闭所有小肥鱼（slot-N）进程并清理其 runtime 标记。

    只退出进程，**不删除** slot 配置/会话/待办数据（子肥鱼设置保留，
    下次生成按占位语义恢复）。只处理非当前进程的 runtime 标记；
    slot-0 主肥鱼不受影响。
    返回 {"killed_pids": [...], "failed_pids": [...]}。

    批 G：杀进程后必须确认进程真的死掉才删标记——旧实现「杀失败静默吞掉 +
    无条件删标记」，子进程存活且痕迹清零（实机复现：第二只子肥鱼幸存），
    且全程零日志无法排查。杀失败的 pid 保留标记并记入 failed_pids，
    供下次重试/结果框呈报。

    批 H：①枚举源加 slots/slot-*.lock（没写过 runtime 标记的小肥鱼也持有
    slot 锁，否则未拖动过的新鱼漏清）；②杀前用 exe 路径核验身份——陈旧
    标记的 pid 可能已被无关进程复用，误杀会杀死无辜进程/打不动受保护进程
    （实机事故：0 杀 2 失败），识别为复用的按陈旧标记直接清理；③taskkill
    加 CREATE_NO_WINDOW，GUI 下不再每杀一只弹空白控制台窗口。

    批 I：①探活改 GetExitCodeProcess==STILL_ACTIVE——OpenProcess 对「已死但
    父进程还持有句柄」的子进程仍可打开，旧探活把杀成功的子鱼误判存活
    （实机：杀了还白等两轮 2s 并误报未能退出）；②两段式杀法——先一口气
    全部结束再统一等确认，N 只也只需一次等待窗口（旧逐只杀等串行 N×2s）；
    ③逐只重试取消（探活准确后重试无意义）。
    """
    root = Path(config_dir)
    killed_pids: list[int] = []
    failed_pids: list[int] = []
    seen: set[int] = {os.getpid(), 0}  # 自己的 pid 永远跳过；两枚举源去重
    targets: list[tuple[int, Path | None]] = []  # (pid, 对应标记文件或 None)

    # 第一阶段：收集目标。陈旧（进程已死 / pid 已被无关进程复用）的标记直接删。
    # 第一枚举源：runtime 标记（旧名 runtime-*.json + v2 新名
    # pet-runtime-v2-*.json，多进程模式只写旧名、单进程模式写 v2 名）。
    markers = slot_manager_mod.list_runtime_marker_files(root)
    if markers:
        logging.info(
            "退出子肥鱼：发现 %d 个 runtime 标记: %s",
            len(markers), [m.name for m in markers])
    for marker in markers:
        # 兜底防御：v2 标记名带 slot 编号，slot-0 是主肥鱼，永不杀（即便调用方
        # 是子肥鱼进程——其 pid==os.getpid() 只跳过自己，主鱼标记会被误杀）。
        if marker.name.startswith(slot_manager_mod._RUNTIME_V2_PREFIX):
            if marker.name.rsplit("-slot-", 1)[-1].removesuffix(".json") == "0":
                continue
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
            pid = int(data.get("pid", 0))
        except (OSError, ValueError, TypeError):
            pid = 0
        if pid in seen:
            continue
        seen.add(pid)
        if pid > 0 and _pid_alive(pid):
            if not _is_pet_process(pid):
                # pid 复用：标记是陈旧的，指向无关进程——不杀，按陈旧标记清理
                logging.info(
                    "退出子肥鱼：标记 %s 的 pid=%d 已被无关进程复用，按陈旧标记清理",
                    marker.name, pid)
            else:
                targets.append((pid, marker))
                continue  # 标记留待杀成后删（杀失败则保留供重试）
        try:
            marker.unlink()
        except OSError:
            pass

    # 第二枚举源：slot 锁文件（没写过 runtime 标记的小肥鱼也持有锁）。
    for pid in _slot_lock_pids(root):
        if pid in seen:
            continue
        seen.add(pid)
        if _pid_alive(pid) and _is_pet_process(pid):
            targets.append((pid, None))

    # 第二阶段：先一口气全部结束，再统一等确认——旧实现逐只「杀→等 2s 确认」，
    # N 只串行等 N×2s（实机反馈太慢）；两段式 N 只也只需一次等待窗口。
    for pid, marker in targets:
        logging.info("退出子肥鱼：结束子进程 pid=%d (%s)",
                     pid, marker.name if marker is not None else "slot 锁")
        _terminate_pet_process(pid)
    remaining = {pid for pid, _m in targets}
    deadline = time.monotonic() + 2.0
    while remaining and time.monotonic() < deadline:
        remaining = {pid for pid in remaining if _pid_alive(pid)}
        if remaining:
            time.sleep(0.05)
    for pid, marker in targets:
        if pid in remaining:
            failed_pids.append(pid)
            # 诊断：杀不掉时记录存活者到底是谁（pid 复用？还是真没杀掉）
            logging.warning(
                "退出子肥鱼：pid=%d 未能退出，存活者镜像=%s，保留标记供下次重试",
                pid, _pid_image_path(pid))
        else:
            killed_pids.append(pid)
            if marker is not None:
                try:
                    marker.unlink()
                except OSError:
                    pass

    return {"killed_pids": killed_pids, "failed_pids": failed_pids}
