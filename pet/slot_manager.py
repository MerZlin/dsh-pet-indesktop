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


def _try_acquire_slot_lock(config_dir: Path, slot_id: int) -> BinaryIO | None:
    """尝试获取指定 slot_id 的文件锁并写入定长 PID。成功返回 open 句柄，失败返回 None。"""
    lock_path = get_slot_lock_path(config_dir, slot_id)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        if not lock_path.exists():
            # 文件不存在：以 w+b 创建，先写入初始定长空字节占位
            f = open(lock_path, "w+b")
            f.write(b" " * PID_RECORD_LEN)
            f.flush()
            f.seek(0)
        else:
            # 文件已存在：以 r+b 打开，不截断、不使用 a+b 追加
            f = open(lock_path, "r+b")
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
        f.seek(0)
        f.write(_format_pid_record(os.getpid()))
        f.flush()
        f.seek(0)
    except OSError:
        _unlock_file(f)
        return None

    return f


def acquire_file_lock(lock_path: Path | str) -> BinaryIO | None:
    """Try to acquire the shared one-byte kernel lock at an arbitrary path."""
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if not path.exists():
            handle = open(path, "w+b")
            handle.write(b" " * PID_RECORD_LEN)
            handle.flush()
            handle.seek(0)
        else:
            handle = open(path, "r+b")
    except OSError:
        return None
    if not _try_lock_file(handle):
        handle.close()
        return None
    try:
        handle.seek(0)
        handle.write(_format_pid_record(os.getpid()))
        handle.flush()
        handle.seek(0)
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


# 2.1 跟随主设置的字段白名单（首次播种时复制，之后副槽位独立保存）
FOLLOW_MASTER_KEYS = (
    # 外观动画
    "character", "scale", "facing", "playback_speed", "animation_gap_seconds",
    "pet_opacity", "context_menu_template", "context_menu_appearance",
    "menu_easter_egg", "character_aliases",
    # 交互物理
    "no_move", "mouse_through", "cursor_hidden_passthrough", "drag_physics",
    "lock_position", "shift_drag", "auto_hide_fullscreen", "slingshot_enabled",
    "throw_strength", "throw_max_speed", "on_top",
    "collision_enabled", "collision_restitution", "collision_friction",
    "collision_mass_scale", "collision_impulse_cap",
    # 音效与自言自语
    "click_sound_enabled", "click_sound_pack", "click_sound_path",
    "click_sound_volume", "music_sing_enabled", "self_talk_enabled",
    "self_talk_min_interval", "self_talk_max_interval", "self_talk_duration_seconds",
    "self_talk_texts", "self_talk_image_dir", "self_talk_bubble_style",
    # 功能扩展
    "quick_launch_apps", "stream_capture_mode", "proactive_screen", "agent_link",
    "balance_refresh_minutes", "balance_tier_labels_mode", "balance_tier_label_peak",
    "balance_tier_label_idle", "balance_tier_color_enabled", "click_show_balance",
    "click_show_self_talk",
    # 聊天 UI
    "chat_ui_style", "chat_background", "modern_chat_background",
    "chat_background_opacity", "chat_background_fill", "modern_chat_background_opacity",
    "modern_chat_background_fill", "modern_chat_card_opacity", "chat_bg_crops",
    "show_dock_icon", "chat_follow_pet",
    # 完整 chat 结构
    "chat",
)


def seed_slot_config_if_needed(config_dir: Path | str, slot_id: int) -> bool:
    """在已持有槽位锁的条件下，如果 slot-N (N>=1) 的配置文件不存在，则从主配置播种。

    位置 (rx, ry, screen_name) 设为 None，autostart_wanted 设为 False，会话目录为空。
    返回是否进行了播种。
    """
    if slot_id == 0:
        return False

    config_path = Path(config_dir)
    target_config_file = get_config_path_for_slot(config_path, slot_id)
    if target_config_file.exists():
        return False

    master_config_file = get_config_path_for_slot(config_path, 0)
    master_data: dict[str, Any] = {}
    if master_config_file.is_file():
        try:
            master_data = json.loads(master_config_file.read_text(encoding="utf-8"))
            if not isinstance(master_data, dict):
                master_data = {}
        except Exception:
            master_data = {}

    # 从主配置拷贝白名单字段
    seeded_data: dict[str, Any] = {
        "version": 4,
        "rx": None,
        "ry": None,
        "screen_name": None,
        "autostart_wanted": False,
    }

    import copy
    for key in FOLLOW_MASTER_KEYS:
        if key in master_data:
            seeded_data[key] = copy.deepcopy(master_data[key])

    # 剔除 chat providers 下可能残留的明文 key
    if "chat" in seeded_data and isinstance(seeded_data["chat"], dict):
        providers = seeded_data["chat"].get("providers")
        if isinstance(providers, dict):
            for provider in providers.values():
                if isinstance(provider, dict):
                    provider.pop("api_key", None)
                    provider.pop("vision_api_key", None)

    # 写入目标配置文件（使用唯一临时文件名带 PID）
    config_path.mkdir(parents=True, exist_ok=True)
    temp_file = target_config_file.with_name(f"{target_config_file.name}.{os.getpid()}.tmp")
    try:
        temp_file.write_text(
            json.dumps(seeded_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp_file, target_config_file)
    except Exception as exc:
        logging.warning("播种槽位配置失败: %s (%s)", target_config_file, exc)
        try:
            if temp_file.exists():
                temp_file.unlink()
        except OSError:
            pass
        return False

    # 确保会话目录存在且为空（不复制主会话）
    sessions_dir = get_sessions_dir_for_slot(config_path, slot_id)
    sessions_dir.mkdir(parents=True, exist_ok=True)

    return True


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


def _recover_migration_staging(config_path: Path, staging_dir: Path) -> None:
    """恢复或清理残留的 .migration_staging 目录。

    若上次迁移中途被杀，staging 内可能残留尚未移入目标或尚未回滚的文件。
    若 staging 内有文件，将它们按元数据/对应旧名字或就近还原。
    为保证幂等与安全：
    staging 中的 target 格式文件（如 config-slot-N.json），若目标路径不存在则移入目标路径；若目标已存在则忽略。
    清理完成后移除 staging 目录。
    """
    if not staging_dir.is_dir():
        return
    for item in staging_dir.iterdir():
        target = config_path / item.name
        if not target.exists():
            try:
                shutil.move(str(item), str(target))
            except Exception as exc:
                logging.warning("恢复 staging 文件失败: %s -> %s (%s)", item, target, exc)
    try:
        shutil.rmtree(staging_dir, ignore_errors=True)
    except Exception:
        pass


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
        _recover_migration_staging(config_path, staging_dir)

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
