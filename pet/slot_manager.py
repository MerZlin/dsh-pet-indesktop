# -*- coding: utf-8 -*-
"""桌宠槽位管理与跨平台文件锁协议。

实现多开桌宠的槽位竞争、内核排他锁、配置播种与旧 spawn 迁移。
纯 Python 实现，不依赖 Qt。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import time
import re
from pathlib import Path
from typing import Any, BinaryIO

# 定长 PID 格式（16字节，右补空格与换行）
PID_RECORD_LEN = 16
LOCK_BYTE_COUNT = 1

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class SlotLockError(Exception):
    """槽位锁获取异常。"""


class SlotManagerError(Exception):
    """槽位管理通用异常。"""


def get_slot_lock_path(config_dir: Path | str, slot_id: int) -> Path:
    """返回槽位对应的锁文件路径：<config_dir>/slots/slot-{N}.lock"""
    return Path(config_dir) / "slots" / f"slot-{slot_id}.lock"


def _try_lock_file(file_obj: BinaryIO) -> bool:
    """尝试对打开的文件对象取得 1 字节排他锁（非阻塞）。"""
    fileno = file_obj.fileno()
    if sys.platform == "win32":
        try:
            file_obj.seek(0)
            msvcrt.locking(fileno, msvcrt.LK_NBLCK, LOCK_BYTE_COUNT)
            return True
        except (OSError, IOError):
            return False
    else:
        try:
            file_obj.seek(0)
            fcntl.flock(fileno, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (BlockingIOError, OSError, IOError):
            return False


def _unlock_file(file_obj: BinaryIO) -> None:
    """释放锁并关闭文件。"""
    try:
        fileno = file_obj.fileno()
        if sys.platform == "win32":
            file_obj.seek(0)
            msvcrt.locking(fileno, msvcrt.LK_UNLCK, LOCK_BYTE_COUNT)
        else:
            fcntl.flock(fileno, fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        file_obj.close()
    except Exception:
        pass


def _format_pid_record(pid: int) -> bytes:
    """生成定长 16 字节 PID 记录。"""
    text = f"{pid}\n"
    raw = text.encode("ascii", errors="replace")
    if len(raw) < PID_RECORD_LEN:
        raw = raw + b" " * (PID_RECORD_LEN - len(raw))
    else:
        raw = raw[:PID_RECORD_LEN]
    return raw


def _open_lock_file(path: Path) -> BinaryIO:
    """Open or create a lock file and ensure the lock byte exists.

    The absolute truncate is intentionally idempotent.  Multiple first-time
    openers may all observe size zero before any of them acquires the kernel
    lock; appending the observed deficit would grow the file to 32+ bytes.
    """
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    handle = os.fdopen(fd, "r+b")
    try:
        size = os.fstat(fd).st_size
        if size < PID_RECORD_LEN:
            os.ftruncate(fd, PID_RECORD_LEN)
        handle.seek(0)
        return handle
    except Exception:
        handle.close()
        raise


def _write_pid_record(file_obj: BinaryIO) -> None:
    """Write this process PID and restore the fixed-size lock-file format."""
    file_obj.seek(0)
    file_obj.write(_format_pid_record(os.getpid()))
    file_obj.flush()
    os.ftruncate(file_obj.fileno(), PID_RECORD_LEN)
    file_obj.seek(0)


def _try_acquire_slot_lock(config_dir: Path, slot_id: int) -> BinaryIO | None:
    """尝试获取指定 slot_id 的文件锁并写入定长 PID。成功返回 open 句柄，失败返回 None。"""
    lock_path = get_slot_lock_path(config_dir, slot_id)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        f = _open_lock_file(lock_path)
    except OSError:
        return None

    # 尝试加锁 1 字节排他锁
    if not _try_lock_file(f):
        try:
            f.close()
        except Exception:
            pass
        return None

    # 加锁成功后，写入定长 PID 记录到文件头并 flush
    try:
        _write_pid_record(f)
    except OSError:
        _unlock_file(f)
        return None

    return f


def acquire_file_lock(lock_path: Path | str) -> BinaryIO | None:
    """Try to acquire the shared one-byte kernel lock at an arbitrary path."""
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = _open_lock_file(path)
    except OSError:
        return None
    if not _try_lock_file(handle):
        handle.close()
        return None
    try:
        _write_pid_record(handle)
    except OSError:
        _unlock_file(handle)
        return None
    return handle


def release_file_lock(handle: BinaryIO | None) -> None:
    if handle is not None:
        _unlock_file(handle)


def acquire_pet_slot(
    config_dir: Path | str,
    preferred_slot: int | None = None,
    max_scan_slots: int = 128,
) -> tuple[int, BinaryIO]:
    """获取桌宠槽位及排他锁句柄。

    参数:
        config_dir: 配置根目录（例如 APPDATA/dsh-pet-standalone）
        preferred_slot: 若指定 slot（如 0 或命令行 --slot N），只尝试该槽位，失败直接抛 SlotLockError
        max_scan_slots: 自动扫描时的最大槽位数上限

    返回:
        (slot_id, lock_handle)

    异常:
        SlotLockError: 指定槽位被占用或无法获取锁
        SlotManagerError: 无可用空闲槽位
    """
    config_path = Path(config_dir)

    if preferred_slot is not None:
        handle = _try_acquire_slot_lock(config_path, preferred_slot)
        if handle is None:
            raise SlotLockError(f"槽位 slot-{preferred_slot} 已被占用或无法获取锁")
        return preferred_slot, handle

    # 未指定时按 0, 1, 2, ... 顺序竞争
    for slot_id in range(max_scan_slots):
        handle = _try_acquire_slot_lock(config_path, slot_id)
        if handle is not None:
            return slot_id, handle

    raise SlotManagerError(f"在前 {max_scan_slots} 个槽位中未找到可用空闲槽位")


def slot_to_instance_id(slot_id: int) -> str:
    """slot-0 映射为空 instance_id（主实例），slot-N 映射为 'slot-N'。"""
    return "" if slot_id == 0 else f"slot-{slot_id}"


def get_config_path_for_slot(config_dir: Path | str, slot_id: int) -> Path:
    """获取槽位对应的配置文件路径。"""
    config_path = Path(config_dir)
    return config_path / "config.json" if slot_id == 0 else config_path / f"config-slot-{slot_id}.json"


def get_sessions_dir_for_slot(config_dir: Path | str, slot_id: int) -> Path:
    """获取槽位对应的会话目录。"""
    config_path = Path(config_dir)
    return config_path / "sessions" if slot_id == 0 else config_path / f"sessions-slot-{slot_id}"


def backup_corrupt_config(config_file: Path) -> Path | None:
    """损坏配置备份：使用毫秒时间戳加 PID 后缀，连续恢复不覆盖旧备份。"""
    if not config_file.is_file():
        return None
    timestamp = int(time.time() * 1000)
    backup_file = config_file.with_name(f"{config_file.name}.corrupt-{timestamp}-{os.getpid()}")
    try:
        shutil.copy2(config_file, backup_file)
        return backup_file
    except Exception as exc:
        logging.error("备份损坏配置文件失败: %s -> %s (%s)", config_file, backup_file, exc)
        return None


def _recover_migration_staging(config_path: Path, staging_dir: Path) -> bool:
    """恢复或清理残留的 .migration_staging 目录。

    若上次迁移中途被杀，staging 内可能残留尚未移入目标或尚未回滚的文件。
    若 staging 内有文件，将它们按元数据/对应旧名字或就近还原。
    为保证幂等与安全：
    staging 中的 target 格式文件（如 config-slot-N.json），若目标路径不存在则移入目标路径；若目标已存在则忽略。
    清理完成后移除 staging 目录。
    """
    if not staging_dir.is_dir():
        return True
    remaining = False
    for item in list(staging_dir.iterdir()):
        target = config_path / item.name
        match = re.match(r"(?:config|sessions)-slot-(\d+)", item.name)
        lock_handle = None
        if match:
            lock_handle = _try_acquire_slot_lock(config_path, int(match.group(1)))
            if lock_handle is None:
                logging.warning("恢复 staging 时目标槽位被占用，保留残留: %s", item)
                remaining = True
                continue
        try:
            if target.exists():
                logging.warning("恢复 staging 时目标已存在，保留残留: %s", item)
                remaining = True
            else:
                shutil.move(str(item), str(target))
        except Exception as exc:
            logging.warning("恢复 staging 文件失败: %s -> %s (%s)", item, target, exc)
            remaining = True
        finally:
            if lock_handle is not None:
                _unlock_file(lock_handle)
    if not remaining:
        try:
            staging_dir.rmdir()
        except OSError:
            remaining = True
    return not remaining


def migrate_legacy_spawns(config_dir: Path | str) -> bool:
    """将旧版 config-spawn*.json 和 sessions-spawn*/ 原子迁移到 slot-1, slot-2, ...。

    约束:
    1. 仅在确认没有运行中的旧 spawn 实例时进行；
    2. 按旧 config 的 mtime 升序稳定排序，依次映射到 slot-1, slot-2, ...；
    3. config 与对应 sessions 作为原子迁移单元，使用 staging 临时目录回滚；
    4. 目标槽位需尝试获取该槽位锁，被占用则跳过该槽位（保留原文件，记 warning）；
    5. config 已移到目标后 sessions 移动失败必须把目标 config 移回原路径（完整回滚）；
    6. 启动时检测到 .migration_staging 非空，先恢复或回滚上次中断的迁移；
    7. 完成后写入 migration-spawns.done 标记文件；
    8. 未迁移或无法配对的文件一律保留不删。
    """
    config_path = Path(config_dir)
    if not config_path.is_dir():
        return True

    staging_dir = config_path / ".migration_staging"
    if staging_dir.is_dir():
        if not _recover_migration_staging(config_path, staging_dir):
            return False

    marker_file = config_path / "migration-spawns.done"
    if marker_file.exists():
        return True

    # 查找所有旧 spawn 配置文件
    old_configs = list(config_path.glob("config-spawn*.json"))
    if not old_configs:
        try:
            marker_file.write_text("done\n", encoding="utf-8")
        except OSError:
            pass
        return True

    # 检查是否有旧实例正在运行（通过 runtime marker 探测）
    for runtime_file in config_path.glob("runtime-*.json"):
        try:
            data = json.loads(runtime_file.read_text(encoding="utf-8"))
            pid = data.get("pid")
            if pid and isinstance(pid, int):
                # 检查进程是否存活
                if sys.platform == "win32":
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                    if h:
                        kernel32.CloseHandle(h)
                        logging.warning("检测到正在运行的桌宠进程 (PID: %s)，跳过旧 spawn 迁移", pid)
                        return False
                else:
                    try:
                        os.kill(pid, 0)
                        logging.warning("检测到正在运行的桌宠进程 (PID: %s)，跳过旧 spawn 迁移", pid)
                        return False
                    except OSError:
                        pass
        except Exception:
            pass

    # 按 mtime 排序
    old_configs.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0)

    # 找到目标 slot-1, slot-2 等空闲槽位
    slot_idx = 1
    staging_dir.mkdir(parents=True, exist_ok=True)

    success_all = True
    for old_cfg in old_configs:
        spawn_name = old_cfg.stem.removeprefix("config-")
        old_sessions = config_path / f"sessions-{spawn_name}"

        # 校验 JSON 有效性
        try:
            _ = json.loads(old_cfg.read_text(encoding="utf-8"))
        except Exception as exc:
            logging.warning("旧 spawn 配置损坏，跳过迁移: %s (%s)", old_cfg, exc)
            continue

        # 寻找下一个目标 slot 文件不存在且能取得锁的 slot ID
        target_lock_handle = None
        target_cfg = None
        target_sessions = None
        while True:
            t_cfg = get_config_path_for_slot(config_path, slot_idx)
            t_sessions = get_sessions_dir_for_slot(config_path, slot_idx)
            if not t_cfg.exists() and not t_sessions.exists():
                # 尝试取得目标槽位锁
                lock_h = _try_acquire_slot_lock(config_path, slot_idx)
                if lock_h is not None:
                    target_lock_handle = lock_h
                    target_cfg = t_cfg
                    target_sessions = t_sessions
                    break
                else:
                    logging.warning("槽位 slot-%s 已被占用，跳过该槽位", slot_idx)
            slot_idx += 1

        # 执行单元原子移动（先 staging 再 target）
        staged_cfg = staging_dir / target_cfg.name
        staged_sessions = staging_dir / target_sessions.name if old_sessions.is_dir() else None

        step = 0  # 0: 未动, 1: old->staging, 2: staged_cfg->target_cfg, 3: staged_sessions->target_sessions
        try:
            shutil.move(str(old_cfg), str(staged_cfg))
            if staged_sessions and old_sessions.is_dir():
                shutil.move(str(old_sessions), str(staged_sessions))
            step = 1

            shutil.move(str(staged_cfg), str(target_cfg))
            step = 2

            if staged_sessions and staged_sessions.exists():
                shutil.move(str(staged_sessions), str(target_sessions))
                step = 3

            slot_idx += 1
        except Exception as exc:
            logging.error("迁移单元失败: %s -> slot-%s (%s)", old_cfg, slot_idx, exc)
            # 完整回滚到迁移前状态
            try:
                # 无论在哪一步出错，若 target 或 staging 存在目标文件，统一撤回至 old 路径
                if target_cfg.exists():
                    shutil.move(str(target_cfg), str(old_cfg))
                elif staged_cfg.exists():
                    shutil.move(str(staged_cfg), str(old_cfg))

                if target_sessions.exists():
                    shutil.move(str(target_sessions), str(old_sessions))
                elif staged_sessions and staged_sessions.exists():
                    shutil.move(str(staged_sessions), str(old_sessions))
            except Exception as rollback_exc:
                logging.error("回滚迁移单元失败: %s (%s)", old_cfg, rollback_exc)
            success_all = False
        finally:
            if target_lock_handle is not None:
                _unlock_file(target_lock_handle)

    try:
        shutil.rmtree(staging_dir, ignore_errors=True)
    except Exception:
        pass

    if success_all:
        try:
            marker_file.write_text("done\n", encoding="utf-8")
        except OSError:
            pass

    return success_all


def pid_alive(pid: int) -> bool:
    """跨平台探活：Windows 用 OpenProcess，其余用 kill(pid, 0)。"""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        # PROCESS_QUERY_LIMITED_INFORMATION
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# --- 批5.2 R4：runtime 标记格式版本化（多窗每窗一份，旧 glob 不匹配）--------
# 旧版（runtime-<pid>.json）用 glob('runtime-*.json') 读取；为避免新旧混跑时
# 旧版把新版标记也计入「存活实例」而虚高计数（预算被错误压小），新版标记
# 改用不与 'runtime-*.json' 匹配的 pet-runtime-v2-<pid>-slot-<N>.json 前缀。
_RUNTIME_V2_PREFIX = "pet-runtime-v2-"


def _slot_label(instance_id: str) -> str:
    """从 instance_id 提取 slot 标签（用于 runtime 标记文件名）。"""
    s = str(instance_id or "").strip()
    if not s or s == "slot-0":
        return "0"
    if s.startswith("slot-"):
        return s[len("slot-"):] or "0"
    return re.sub(r"[^A-Za-z0-9_-]", "_", s) or "0"


def runtime_marker_name(instance_id: str = "", *, versioned: bool = False) -> str:
    """返回某窗 runtime 标记文件名。versioned=False 用旧名
    runtime-<pid>.json（单窗/flag 关时保持旧行为）；True 用版本化新名
    pet-runtime-v2-<pid>-slot-<N>.json（批5.2 多窗，规避旧 glob 匹配）。"""
    pid = os.getpid()
    if versioned:
        return f"{_RUNTIME_V2_PREFIX}{pid}-slot-{_slot_label(instance_id)}.json"
    return f"runtime-{pid}.json"


def runtime_marker_path(config_dir: Path | str, instance_id: str = "",
                        *, versioned: bool = False) -> Path:
    """返回某窗 runtime 标记的完整路径。"""
    return Path(config_dir) / runtime_marker_name(instance_id, versioned=versioned)


def write_runtime_marker(config_dir: Path | str, instance_id: str,
                         x: int, y: int, w: int, h: int,
                         *, versioned: bool = False) -> Path:
    """写入本窗 runtime 标记（旧格式仅主窗/pflag 关时用；versioned 多窗用）。

    写版本化标记时顺手清掉同 pid 的旧格式标记，避免同进程混用重复计数。
    """
    path = runtime_marker_path(config_dir, instance_id, versioned=versioned)
    try:
        if versioned:
            legacy = Path(config_dir) / f"runtime-{os.getpid()}.json"
            try:
                if legacy.exists():
                    legacy.unlink()
            except OSError:
                pass
        path.write_text(json.dumps({
            'pid': os.getpid(),
            'x': int(x), 'y': int(y), 'w': int(w), 'h': int(h),
        }), encoding='utf-8')
    except OSError:
        pass
    return path


def delete_runtime_marker(config_dir: Path | str, instance_id: str = "",
                          *, versioned: bool = True) -> None:
    """删除本窗 runtime 标记（「退出这只」必须显式删，否则活 pid 的陈旧
    标记永久虚增计数/避让错乱）。新旧两种命名都尝试删（跨格式迁移兜底）。"""
    for ver in (bool(versioned), not bool(versioned)):
        try:
            path = runtime_marker_path(config_dir, instance_id, versioned=ver)
            if path.exists():
                path.unlink()
        except OSError:
            pass


def read_live_instances(
    config_dir: Path | str,
    *,
    exclude_pid: int | None = None,
    exclude_markers=None,
    pid_alive_fn=None,
) -> list[tuple[int, int, int, int, int]]:
    """读取目录内 runtime 标记，返回存活实例 (pid, x, y, w, h) 列表。

    同时认旧（runtime-<pid>.json）与批5.2 新（pet-runtime-v2-*）两种命名
    （避让定位兼容新旧混跑）。死进程 pid、损坏 JSON、字段非法的标记顺手
    删除（避免越积越多）；exclude_markers（本窗自己的标记路径/文件名）跳过
    且保留——批5.2 多窗同 pid 下不能再用 exclude_pid 这种按 pid 过滤的
    方式（会把同进程所有窗都排除）。pid_alive_fn 可注入（测试用）。
    """
    alive = pid_alive_fn if pid_alive_fn is not None else pid_alive
    try:
        exclude_names = {Path(m).name for m in (exclude_markers or ())}
    except (TypeError, OSError):
        exclude_names = set()
    instances: list[tuple[int, int, int, int, int]] = []
    try:
        files = list(Path(config_dir).glob('runtime-*.json'))
        files.extend(Path(config_dir).glob(f'{_RUNTIME_V2_PREFIX}*.json'))
    except OSError:
        return instances
    for f in files:
        try:
            data = json.loads(f.read_text(encoding='utf-8'))
            pid = int(data.get('pid', 0))
            if exclude_pid is not None and pid == exclude_pid:
                continue
            if f.name in exclude_names:
                continue
            if not alive(pid):
                raise OSError('stale marker')
            x, y, w, h = (int(data.get(k, 0)) for k in ('x', 'y', 'w', 'h'))
            instances.append((pid, x, y, w, h))
        except (OSError, ValueError, TypeError):
            try:
                f.unlink()
            except OSError:
                pass
    return instances
