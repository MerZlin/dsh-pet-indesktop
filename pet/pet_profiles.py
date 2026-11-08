# -*- coding: utf-8 -*-
"""G1a 存储层：每宠档案（slot profile）+ 活跃集合 v2 + 数据代次。

overlay、legacy与设置路由共同使用本模块的固定身份档案、活跃集合和代次API。
每宠数据不写global；旧入口只保留委托包装。

## 一、所有权与真值（规范 §五）

- **槽档案** = ``<config_dir>/config-slot-N.json``（含 0；沿用既有命名约定）；
- **活跃集合** = ``<config_dir>/overlay-active-pets.json``（运行时状态，既不属于
  全局设置也不属于任何一只宠；v2 起含 0）；
- **数据代次** = ``<config_dir>/overlay-slot-generations.json``（运行时状态）。

**每宠键集为什么不能塞进档案文件里**：``Config.reload`` 只吸收它认识的键，
``Config.save`` 写的是**整份内存视图**——任何 Config 不认识的键会在下一次保存时
被丢掉。把代次塞进 ``config-slot-N.json`` 就等于让它随时可能消失，那时"迟到写不得
覆盖 reset 结果"这条保护会静默失效。故代次单独一个侧车文件（单一真值，不是第二份
配置值：它不描述任何设置）。

每宠键集唯一来源在本模块，overlay_spawn_state只re-export兼容子集；设置路由含位置与朝向。

规范 §五 的每宠类别里，**碰撞物理参数**（``collision_restitution`` /
``collision_friction`` / ``collision_mass_scale`` / ``collision_impulse_cap``）
今天仍只有全局读点（设置页写主配置、``collision.py`` 读主配置），本批**不**把它们
悄悄塞进每宠集（那会立刻造出第二份有效真值），而是登记在
:data:`DEFERRED_PER_PET_KEYS` 交给接线批裁决。

## 二、活跃集合 v2 的三种状态必须分开（规范 §一）

| 盘上状态 | 视图 | 说明 |
|---|---|---|
| 文件缺失 | ``(0,)`` | 新安装默认只有主宠；**不**因此创建文件 |
| v2（含空列表） | 原样 | 空集合是合法状态，绝不静默补一只 |
| v1（含空列表） | ``0`` + 旧集合 | v1 的 0 是隐式主宠，任何旧列表都要补 0 |
| 损坏 / 未知版本 | ``()`` + 诊断 | **不**当新装（不带 0）、**不**覆盖文件 |

## 三、slot0 迁移（规范 §一/§五）

``migrate_slot0_profile``：从**全局** ``config.json`` 抽取每宠键（含几何）写进
``config-slot-0.json``。铁律：

- 目标已存在 → ``already-exists``，**绝不覆盖目标**（锁内复查，避免两个进程同时创建）；
- **先备份再写**（``config.json.pre-slot0-migration.bak``，已存在则保留第一份、不覆盖）；
- **两条支都清理源每宠键**（``created`` 与 ``already-exists`` 同一条规则：全局
  ``config.json`` 不留每宠键）。目标已存在时源里的那批键是**未被本次迁移采用**的
  源值，清理前必须保证恢复路径真的留住了它们：规范备份若本来就是这批值 → 直接用；
  若该路径已被**另一份快照**占用（不覆盖它）→ 另存一份时间戳恢复副本
  （``config.json.pre-slot0-migration-recovery-<毫秒>-<pid>.bak``），结果见
  ``recovery_path``。取不到恢复副本就**不清源**并返回 ``backup-failed``。
  这条"清理"不是可有可无的对称美：``Config.save()`` 对全局身份**无条件**
  ``pop(PER_PET_KEYS | SLOT_LOCAL_KEYS)``，迁不迁移都会在下一次保存时把源里的每宠键
  抹掉——迁移侧若"不清源"，只会留下一个骗人的 ``retained_keys``（文件里其实留不住）。
- legacy 由同一档案派生读取；旧版本退回读全局键的场面由上面那份备份兜底。
- **不是事务**：备份 + 写目标 + 将来删源是多个文件的多个步骤，中途中断只会留下
  "备份在、目标未写"或"目标已写、源未清"这类可重跑状态，本模块**不声称原子**——
  重跑一遍即可收敛（幂等）。
- 迁移源必须是**全局配置**：``--slot N`` 形态下那个进程的"全局配置"就是
  ``config-slot-N.json``（另一只宠的档案），``--slot 0`` 形态下源与目标同文件——
  两种情况都返回 ``refused-slot-source``，一个字节不写（跨身份替换是规范明令禁止的）。

## 四、返回结构与异常合同

- **写类 API 一律返回** :class:`ProfileResult`（``status`` / ``reason`` / 变更明细），
  不抛异常：配置目录坏了、锁被占、备份失败都不该炸掉壳层。
  ``status`` 取值见各函数 docstring；``"ok"`` 之外都**没有落盘**（或只有备份落盘）。
  公共失败词表：``lock-timeout``（等锁超时）、``lock-unavailable``（拿不到锁原语 /
  打不开锁文件，如只读盘）、``write-failed``（写盘/替换失败，旧数据未动）、
  ``backup-failed``（备份这一步失败）、``backup-invalid``（备份路径上的既有文件
  不是有效的迁移前快照：不覆盖它、也不继续）、``refused-corrupt``、``refused-key``、
  ``refused-legacy-v1``（v1 活跃集合不许在写路径绕过备份升级）、``stale-generation``、
  ``invalid-geometry``、``partial``（两文件写入的第二步失败：第一个文件已落盘，
  ``generation`` 给出新代次，**不声称"无变化"**）、``no-changes``、``no-profile``。

**代次 token 的取法（唯一合法路径）**：token 只能来自 :func:`read_slot_snapshot`
（档案 + 代次在同一把锁内一次读出）或 :func:`begin_slot_generation`（新住客入住）。
**禁止**"保存前再新读一次代次"——那读到的是另一代的代次，代次门会形同虚设。
- **读类 API** 返回数据对象（:class:`ActiveSet` / :class:`SlotProfile`），用
  ``status`` 回答"读到了什么"；只有"读不出来会让保护静默失效"的场合才抛：
  :func:`slot_generation` 在代次文件损坏时抛 :class:`ProfileStateError`
  （返回 0 会被当成"全新槽"，从而错误接受迟到的旧写）。
- **编程错误**抛 :class:`SlotRangeError`（slot 不是 0..128 的整数）。
- 等锁超时与写盘失败分别返回 ``"lock-timeout"`` / ``"write-failed"``（由
  :mod:`pet.config_write_lock` 的异常翻译而来）。

## 五、两套 slot↔身份映射（禁止合并，规范 §五/规划 §4.3）

``slot_manager.slot_to_instance_id(0) == ""``（**进程身份**：主实例 / env
``DSH_PET_INSTANCE`` / runtime 标记命名）；本模块只处理**档案身份**
（``config-slot-0.json``）。设置进程的 ``--instance slot-0`` 属接线批的事，本模块
不提供也不改写任何"进程身份"映射——两套语义不同，合并即事故。
"""
from __future__ import annotations

import copy
import logging
import math
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from . import config_write_lock as cwl
from .config import Config, atomic_replace_with_retry

logger = logging.getLogger(__name__)

__all__ = [
    "ACTIVE_SET_FILENAME",
    "BACKUP_FAILED",
    "BACKUP_INVALID",
    "BACKUP_OK",
    "ACTIVE_SET_V1_BACKUP_SUFFIX",
    "ACTIVE_SET_VERSION",
    "ActiveSet",
    "CapacityFullError",
    "DEFERRED_PER_PET_KEYS",
    "GENERATION_FILENAME",
    "GENERATION_FILE_VERSION",
    "LEGACY_ACTIVE_SET_VERSION",
    "PER_PET_KEYS",
    "PER_PET_PREFERENCE_KEYS",
    "PER_PET_SETTING_KEYS",
    "POSITION_KEYS",
    "PROFILE_FILE_VERSION",
    "PROFILE_MIGRATION_BACKUP_SUFFIX",
    "PROFILE_MIGRATION_RECOVERY_MARKER",
    "ProfileError",
    "ProfileResult",
    "ProfileStateError",
    "SLOT_COUNT",
    "SLOT_LOCAL_KEYS",
    "SLOT0_DEGRADED_STATUSES",
    "SLOT_MAX",
    "SLOT_MIN",
    "Slot0Migration",
    "SlotProfile",
    "SlotSnapshot",
    "SlotRangeError",
    "active_set_path",
    "allocate_slot",
    "begin_slot_generation",
    "generation_path",
    "generation_status",
    "global_config_path",
    "load_active_set",
    "migrate_active_set",
    "migrate_slot0_profile",
    "normalize_slots",
    "per_pet_defaults",
    "read_slot_profile",
    "read_slot_snapshot",
    "reset_slot_profile",
    "same_source_default",
    "save_active_set",
    "slot_generation",
    "slot_profile_exists",
    "slot_profile_path",
    "write_profile_geometry",
    "write_profile_settings",
]

# ------------------------------------------------------------------ 身份空间与文件名
#: 槽编号空间：0（主宠）.. 128（沿用旧扫描上限 1..128 的最高编号）。**总数 129**——
#: 0 加入后不许把上界缩到 127（那会凭空丢掉既有 ``config-slot-128.json`` 的身份）。
SLOT_MIN = 0
SLOT_MAX = 128
SLOT_COUNT = SLOT_MAX - SLOT_MIN + 1

#: 档案文档版本 = Config schema 版本（``Config._initial_data()["version"]``）。
#: 不抄默认大表，只在新建档案时补这个字段；已有档案保留其原值。
PROFILE_FILE_VERSION = 4

#: 活跃集合文件名沿用生产名（v2 原地替换 v1，不另起名字）。
ACTIVE_SET_FILENAME = "overlay-active-pets.json"
ACTIVE_SET_VERSION = 2
LEGACY_ACTIVE_SET_VERSION = 1
ACTIVE_SET_V1_BACKUP_SUFFIX = ".v1.bak"

#: 代次侧车（运行时状态；名字不撞 ``config-*`` / ``config-slot-*`` / ``runtime-*``
#: / ``pet-runtime-v2-*`` 任何一个既有 glob）。
GENERATION_FILENAME = "overlay-slot-generations.json"
GENERATION_FILE_VERSION = 1

#: slot0 迁移备份后缀（先备份再写）。
PROFILE_MIGRATION_BACKUP_SUFFIX = ".pre-slot0-migration.bak"
#: 损坏文件备份的中缀（与 ``slot_manager.backup_corrupt_config`` 同形：
#: ``<name>.corrupt-<毫秒>-<pid>``，连续恢复不覆盖旧备份）。
CORRUPT_BACKUP_MARKER = ".corrupt-"

_SLOT_PROFILE_RE = re.compile(r"^config-slot-(\d+)\.json$")

# ------------------------------------------------------------------ 每宠键集
#: 每宠设置键 = 旧 per-slot 取源集（``overlay_spawn_state.PER_SLOT_SETTING_KEYS``
#: 的**临时副本**，见模块 docstring「每宠键集的临时双份」）**加上**
#: ``collision_enabled``——它的逐宠读点不在 ``_SpriteConfig.get`` 的第一分支，而在
#: ``overlay_shell._sprite_collision_enabled`` → ``read_slot_collision_enabled``
#: （读这只宠的 slot 文件、缺文件继承主配置）。与
#: ``settings_slot_config.SLOT_ROUTED_KEYS`` 同构，测试有逐字漂移守卫。
PER_PET_SETTING_KEYS = frozenset({
    # 几何 / 运动
    "scale", "playback_speed", "drag_physics", "drop_gravity", "throw_strength",
    "ffmpeg_recycle_minutes",
    # 音效（点击与碰撞各一对门/音量；音源包 click_sound_pack 两者共用）
    "click_sound_enabled", "click_sound_volume", "click_sound_pack",
    "collision_sound_enabled", "collision_sound_volume",
    # 气泡 / 自言自语族
    "self_talk_enabled", "self_talk_texts", "self_talk_duration_seconds",
    "self_talk_image_dir", "self_talk_image_scale", "self_talk_image_chance",
    "self_talk_min_interval", "self_talk_max_interval",
    "self_talk_speak_enabled", "self_talk_bubble_style", "bubble_text_scale",
    "self_talk_ai_enabled", "self_talk_ai_personality",
    "click_show_balance", "click_show_self_talk",
    # 场景扮演（每只宠一张场景卡：角色级设定，不同角色可以演不同的戏）
    "roleplay_enabled", "roleplay_scene", "roleplay_cast",
    # 角色（per-pet 素材库）
    "character",
}) | {"collision_enabled"}

#: slot 档案的本地键：只有落种/身份逻辑读它，运行期一个都不看（同 ``SLOT_LOCAL_KEYS``）。
SLOT_LOCAL_KEYS = frozenset({"user_customized"})

#: 位置三键（规范 §五「reset 保留位置」的判据；``Config._NULL_ACCEPTING_KEYS``
#: 同口径：显式 null = 没有位置记忆）。
POSITION_KEYS = ("rx", "ry", "screen_name")

#: 每宠**偏好**（reset 会重置它们）：每宠设置键 + 朝向。朝向属偏好而非位置——
#: 「回到默认落位」只回答"在哪"，不回答"朝哪"（默认 ``left``）。
PER_PET_PREFERENCE_KEYS = PER_PET_SETTING_KEYS | {"facing"}

#: 每宠键全集 = 偏好 + 位置（几何自存写的就是这些键）。
PER_PET_KEYS = PER_PET_PREFERENCE_KEYS | set(POSITION_KEYS)

#: 规范 §五「物理 / 碰撞」里**尚未**按每宠路由的键（今天只有全局读点）。
#: 本批只登记不采纳：把它们迁进每宠集之前，必须先有逐宠读点（否则是第二份真值）。
DEFERRED_PER_PET_KEYS = frozenset({
    "collision_restitution", "collision_friction",
    "collision_mass_scale", "collision_impulse_cap",
})

#: 几何写入允许的键（顺序即写入顺序）。
GEOMETRY_KEYS = ("rx", "ry", "screen_name", "facing", "scale")

#: 迁移抽取顺序（稳定：位置三键 → 偏好键按字典序）。
_EXTRACT_KEYS = tuple(dict.fromkeys((*POSITION_KEYS, *sorted(PER_PET_PREFERENCE_KEYS))))


# ------------------------------------------------------------------ 异常
class ProfileError(Exception):
    """档案/活跃集合/代次层的基类异常。"""


class SlotRangeError(ProfileError, ValueError):
    """slot 不在 ``SLOT_MIN..SLOT_MAX``（含非整数）。"""


class CapacityFullError(ProfileError):
    """``SLOT_COUNT`` 个身份全部活跃，没有可分配的非活跃槽。"""


class ProfileStateError(ProfileError):
    """读到了不可信状态，且"当成全新/默认"会让保护静默失效（如代次文件损坏）。"""


# ------------------------------------------------------------------ 返回结构
@dataclass(frozen=True)
class ProfileResult:
    """写类 API 的返回结构（``status`` 是唯一判据，``reason`` 供日志/界面）。"""

    status: str
    reason: str = ""
    path: Path | None = None
    changed: tuple[str, ...] = ()
    kept: tuple[str, ...] = ()
    cleared: tuple[str, ...] = ()
    dropped: tuple[Any, ...] = ()
    generation: int | None = None
    backup_path: Path | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def __str__(self) -> str:  # 便于测试失败信息直读
        return f"ProfileResult({self.status}, reason={self.reason!r})"


@dataclass(frozen=True)
class ActiveSet:
    """活跃集合的读取视图（``slots`` 已规范化、已按规范处理 v1/缺失/损坏）。"""

    slots: tuple[int, ...]
    status: str
    reason: str = ""
    path: Path | None = None
    raw_version: int | None = None
    dropped: tuple[Any, ...] = ()

    @property
    def ok(self) -> bool:
        """``True`` = 这份视图可以当"当前活跃集合"用（缺失也算，等于新装默认）。"""
        return self.status in ("ok", "missing", "migrated-v1")


@dataclass(frozen=True)
class SlotProfile:
    """一份槽档案的读取视图。``value()`` 是唯一的取值入口（缺键 = 同源默认）。"""

    path: Path
    exists: bool
    status: str
    reason: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)

    def value(self, key: str, default=None):
        """取一个**有效值**：档案里有且非 null → 该值；否则同源默认。

        只回答每宠键（``PER_PET_KEYS``）与本地键（``SLOT_LOCAL_KEYS``）：全局键一律
        返回 ``default``——即便档案文件里混进了全局键，也不许它因此变成有效值
        （否则档案就成了第二个全局真值源）。

        **值不做清洗**：区间/类型清洗的唯一实现在 ``Config``（``Config`` 构造即
        ``reload + _normalize_pet_settings``）。接线时按 ``settings_slot_config``
        的既有先例（``_cleaned_slot_value``）把原值过一遍绑定同一份档案文件的
        ``Config``——本模块不抄第二份默认值与范围表。
        """
        if key not in PER_PET_KEYS and key not in SLOT_LOCAL_KEYS:
            return default
        raw = self.data.get(key) if isinstance(self.data, Mapping) else None
        if raw is not None:
            return raw
        if key in PER_PET_KEYS:
            return same_source_default(key)
        return default


@dataclass(frozen=True)
class SlotSnapshot:
    """**锁内一次读出**的槽快照：档案视图 + 代次 token（审查 P4）。

    ``status``：``"ok"`` / ``"corrupt"``（档案或代次任一不可信）/ ``"lock-timeout"`` /
    ``"lock-unavailable"``。只有 ``"ok"`` 时 ``generation`` 才是可用 token；其余情况
    ``generation`` 一律 ``None``——**绝不**给出 0（那会把损坏当新装放行旧写）。
    ``profile`` 在任何状态下都返回（供诊断/日志）；``status != "ok"`` 时**不要**用它
    做任何写（档案那一侧同样可能是不可信的）。
    """

    slot: int
    status: str
    reason: str = ""
    profile: SlotProfile | None = None
    generation: int | None = None

    def __str__(self) -> str:
        return f"SlotSnapshot({self.status}, slot={self.slot}, gen={self.generation})"


@dataclass(frozen=True)
class Slot0Migration:
    """slot0 迁移的结果。``status`` 见 :func:`migrate_slot0_profile`。

    三个与"数据去哪了"有关的字段别混：

    - ``backup_path``：**规范**备份（``config.json.pre-slot0-migration.bak``），
      已存在则保留第一份、绝不覆盖；
    - ``recovery_path``：规范备份路径已被**另一份快照**占用时才另存的时间戳副本——
      它才装着本次被清掉的那批源值；
    - ``retained_keys``：目标已存在时**未被本次迁移采用**的源每宠键（源里已清掉，
      值只在上面的备份里；供诊断与取回）。
    """

    status: str
    reason: str = ""
    profile_path: Path | None = None
    source_path: Path | None = None
    backup_path: Path | None = None
    recovery_path: Path | None = None
    copied_keys: tuple[str, ...] = ()
    retained_keys: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status in ("created", "already-exists", "no-source")

    @property
    def slot_only_problem(self) -> bool:
        """这个非 ok 结果是否**只牵连 slot0 这一份档案**（见 :data:`SLOT0_DEGRADED_STATUSES`）。"""
        return self.status in SLOT0_DEGRADED_STATUSES

    def __str__(self) -> str:
        return f"Slot0Migration({self.status}, reason={self.reason!r})"


#: ``migrate_slot0_profile`` 的**单槽**降级状态：只有 slot0 这一份档案不可信，
#: 全局配置（``config.json``）与其它槽一个字节都没被动过。调用方据此把"整个应用起不来"
#: 降级成"这个槽先空着、别的照常"，并**保留原文件与路径**（不覆盖、不自动修复）。
#: 其它任何非 ok 状态都涉及全局面或迁移基础设施（源配置损坏/读不出、备份失败、
#: 锁超时、源是别宠档案），一律照旧硬失败——把不可信的全局配置当默认值继续跑，
#: 比停下更危险。
SLOT0_DEGRADED_STATUSES: tuple[str, ...] = ("target-corrupt",)


# ------------------------------------------------------------------ 路径与编号
def _validate_slot(slot) -> int:
    if isinstance(slot, bool) or slot is None:
        raise SlotRangeError(f"slot 必须是 {SLOT_MIN}..{SLOT_MAX} 的整数，收到 {slot!r}")
    try:
        value = int(slot)
    except (TypeError, ValueError) as exc:
        raise SlotRangeError(f"slot 必须是整数，收到 {slot!r}") from exc
    if not SLOT_MIN <= value <= SLOT_MAX:
        raise SlotRangeError(f"slot 必须在 {SLOT_MIN}..{SLOT_MAX}，收到 {slot!r}")
    return value


def slot_profile_path(config_dir: Path | str, slot) -> Path:
    """槽档案路径：``<config_dir>/config-slot-N.json``（含 slot 0）。"""
    return Path(config_dir) / f"config-slot-{_validate_slot(slot)}.json"


def global_config_path(config_dir: Path | str) -> Path:
    """全局配置路径：``<config_dir>/config.json``（主配置真值，属接线批的读端）。"""
    return Path(config_dir) / "config.json"


def active_set_path(config_dir: Path | str) -> Path:
    return Path(config_dir) / ACTIVE_SET_FILENAME


def generation_path(config_dir: Path | str) -> Path:
    return Path(config_dir) / GENERATION_FILENAME


def slot_profile_exists(config_dir: Path | str, slot) -> bool:
    try:
        return slot_profile_path(config_dir, slot).is_file()
    except OSError:
        return False


def normalize_slots(slots: Iterable, *, max_slot: int = SLOT_MAX) -> list[int]:
    """去重保序的合法槽列表：``SLOT_MIN..max_slot`` 之外与非整数一律丢弃。"""
    normalized, _dropped = _normalize_with_dropped(slots, max_slot=max_slot)
    return normalized


def _normalize_with_dropped(slots: Iterable, *,
                            max_slot: int = SLOT_MAX) -> tuple[list[int], tuple[Any, ...]]:
    result: list[int] = []
    dropped: list[Any] = []
    seen: set[int] = set()
    for item in slots if slots is not None else ():
        if isinstance(item, bool) or item is None:
            dropped.append(item)
            continue
        try:
            value = int(item)
        except (TypeError, ValueError):
            dropped.append(item)
            continue
        if value < SLOT_MIN or value > int(max_slot) or value in seen:
            if value in seen:
                continue  # 重复不算非法（去重即可）
            dropped.append(item)
            continue
        seen.add(value)
        result.append(value)
    return result, tuple(dropped)


# ------------------------------------------------------------------ 同源默认
_DEFAULTS_CACHE: dict[str, Any] | None = None


def _config_defaults() -> dict:
    """``Config`` 的出厂默认表（唯一来源；不跑 ``Config.__init__``，不碰磁盘）。

    ``_initial_data`` 只依赖模块级常量（键集/区间字面量），故用 ``__new__`` 造一个
    未初始化的实例来取它——既不读盘也不落种；若将来它开始依赖实例状态，这里会
    立刻 ``AttributeError``（响亮失败，好过悄悄抄第二份默认值）。
    """
    global _DEFAULTS_CACHE
    if _DEFAULTS_CACHE is None:
        _DEFAULTS_CACHE = Config.__new__(Config)._initial_data()
    return _DEFAULTS_CACHE


def per_pet_defaults() -> dict[str, Any]:
    """每宠键的同源默认（每个调用拿一份深拷贝，改它不污染默认表）。

    源 = ``Config`` 的出厂默认；键必须全部命中，否则是"键集与默认表漂移"的
    程序错误（抛 :class:`ProfileError`，由测试守卫先行拦截）。
    """
    defaults = _config_defaults()
    missing = sorted(key for key in PER_PET_KEYS if key not in defaults)
    if missing:
        raise ProfileError(f"每宠键在 Config 默认表里缺失（键集漂移）: {missing}")
    return {key: copy.deepcopy(defaults[key]) for key in PER_PET_KEYS}


def same_source_default(key: str, default=None):
    """单个每宠键的同源默认（**只深拷贝这一个键**，不走全表）；非每宠键返回 ``default``。

    读路径（``SlotProfile.value``）每次都走这里，所以不能"顺手把 30 个键全拷一遍"。
    """
    if key not in PER_PET_KEYS:
        return default
    defaults = _config_defaults()
    if key not in defaults:
        raise ProfileError(f"每宠键在 Config 默认表里缺失（键集漂移）: {key}")
    return copy.deepcopy(defaults[key])


# ------------------------------------------------------------------ 活跃集合
def load_active_set(config_dir: Path | str) -> ActiveSet:
    """读活跃集合（**只读**，绝不创建/覆盖文件）。

    ``status``：``"missing"``（文件不在 → 新装默认 ``(0,)``）、``"ok"``（v2，可能
    为空）、``"migrated-v1"``（v1 视图 = 0 + 旧集合，**含旧空列表 → ``(0,)``**）、
    ``"corrupt"``（非法 JSON / 非对象 / 结构不对）、``"unsupported-version"``
    （版本不认识）、``"unreadable"``（OSError）。

    ``corrupt`` / ``unsupported-version`` / ``unreadable`` 一律返回 ``()`` 并留诊断
    日志：**不**当作新装补 0，也**不**覆盖原文件（覆盖要显式走
    :func:`save_active_set` 的 ``on_broken="backup-overwrite"``）。
    """
    path = active_set_path(config_dir)
    data, status = cwl.read_json_dict(path)
    if status == "missing":
        return ActiveSet(slots=(SLOT_MIN,), status="missing", path=path,
                         reason="活跃集合文件不在：新安装默认只有 slot0")
    if status == "ok":
        raw_version = data.get("version") if data else None
        if isinstance(raw_version, bool) or not isinstance(raw_version, int):
            logger.warning("overlay: 活跃集合缺合法 version，按损坏处理: %s", path)
            return ActiveSet(slots=(), status="corrupt", path=path,
                             reason=f"version 非法: {raw_version!r}")
        raw_slots = data.get("slots")
        if not isinstance(raw_slots, list):
            logger.warning("overlay: 活跃集合 slots 非列表，按损坏处理: %s", path)
            return ActiveSet(slots=(), status="corrupt", path=path,
                             raw_version=raw_version,
                             reason="slots 不是列表")
        slots, dropped = _normalize_with_dropped(raw_slots)
        if dropped:
            logger.warning("overlay: 活跃集合丢弃非法条目 %s: %s", dropped, path)
        if raw_version == ACTIVE_SET_VERSION:
            return ActiveSet(slots=tuple(slots), status="ok", path=path,
                             raw_version=raw_version, dropped=dropped)
        if raw_version == LEGACY_ACTIVE_SET_VERSION:
            if SLOT_MIN not in slots:
                slots.insert(0, SLOT_MIN)
            return ActiveSet(slots=tuple(slots), status="migrated-v1", path=path,
                             raw_version=raw_version, dropped=dropped,
                             reason="v1 的 slot0 是隐式主宠：迁移视图补 0")
        logger.warning("overlay: 活跃集合版本不受支持 (%s): %s", raw_version, path)
        return ActiveSet(slots=(), status="unsupported-version", path=path,
                         raw_version=raw_version, dropped=dropped,
                         reason=f"版本 {raw_version} 不受支持（不覆盖、不当新装）")
    # corrupt / not-object / unreadable
    resolved = "unreadable" if status == "unreadable" else "corrupt"
    logger.warning("overlay: 活跃集合 %s，按未知处理（不覆盖、不当新装）: %s",
                   resolved, path)
    return ActiveSet(slots=(), status=resolved, path=path,
                     reason=f"读取状态 {status}（不覆盖、不当新装）")


def save_active_set(config_dir: Path | str, slots, *, on_broken: str = "refuse",
                    timeout: float = cwl.DEFAULT_TIMEOUT_S,
                    poll: float = cwl.DEFAULT_POLL_S) -> ProfileResult:
    """原子写 v2 活跃集合（``{"version": 2, "slots": [...]}``，保序去重）。

    允许空列表（空集合是合法状态）；0 正常持久化。默认 ``refuse`` 的三种情况**一个
    字节都不写**：损坏/非对象/不可读（``refused-corrupt``）、版本不认识
    （``refused-unsupported-version``）、**仍是 v1**（``refused-legacy-v1``——v1 → v2 属
    迁移，必须走 :func:`migrate_active_set` 的备份升级路径，不在写路径里偷偷升级）。
    ``on_broken="backup-overwrite"`` 才先备份（v1 用 ``.v1.bak`` 且校验既有备份，
    其它用 ``.corrupt-<ms>-<pid>``）再覆盖。
    """
    if on_broken not in ("refuse", "backup-overwrite"):
        raise ValueError(f"on_broken 只接受 'refuse'/'backup-overwrite'，收到 {on_broken!r}")
    path = active_set_path(config_dir)
    normalized, dropped = _normalize_with_dropped(slots)
    if dropped:
        logger.warning("overlay: 活跃集合忽略非法条目 %s", dropped)
    payload = {"version": ACTIVE_SET_VERSION, "slots": normalized}
    backup: Path | None = None
    try:
        with cwl.config_write_lock(path.parent, timeout=timeout, poll=poll):
            _existing, status = cwl.read_json_dict(path)
            if status not in ("ok", "missing"):
                if on_broken != "backup-overwrite":
                    return ProfileResult(
                        status="refused-corrupt", path=path, dropped=dropped,
                        reason=f"现有活跃集合不可信（{status}），拒绝覆盖")
                backup = _backup_corrupt(path)
                if backup is None:
                    return ProfileResult(status="backup-failed", path=path,
                                         reason="备份坏文件失败，放弃写入")
            elif status == "ok":
                version = _existing.get("version") if _existing else None
                if version == LEGACY_ACTIVE_SET_VERSION:
                    # 审查 P6：v1 → v2 必须走**同一条备份升级路径**（migrate_active_set），
                    # 直接在这里写 v2 会把 v1 文件无声升级、迁移前快照一份没留。
                    if on_broken != "backup-overwrite":
                        return ProfileResult(
                            status="refused-legacy-v1", path=path, dropped=dropped,
                            reason="现有活跃集合仍是 v1：先 migrate_active_set"
                                   "（走备份升级），不要在写路径绕过备份")
                    legacy_backup = path.with_name(
                        f"{path.name}{ACTIVE_SET_V1_BACKUP_SUFFIX}")
                    state = _backup_file(path, legacy_backup,
                                         validate=_is_v1_active_set_document)
                    if state != BACKUP_OK:
                        return ProfileResult(
                            status=("backup-invalid" if state == BACKUP_INVALID
                                    else "backup-failed"),
                            path=path,
                            reason="备份路径上的既有文件不是有效的 v1 活跃集合"
                                   "（不覆盖、不升级）" if state == BACKUP_INVALID
                                   else "备份 v1 文件失败，放弃写入")
                    backup = legacy_backup
                elif version != ACTIVE_SET_VERSION:
                    if on_broken != "backup-overwrite":
                        return ProfileResult(
                            status="refused-unsupported-version", path=path,
                            dropped=dropped,
                            reason=f"现有活跃集合版本 {version!r} 不支持，拒绝覆盖")
                    backup = _backup_corrupt(path)
                    if backup is None:
                        return ProfileResult(status="backup-failed", path=path,
                                             reason="备份失败，放弃写入")
            cwl.atomic_write_json(path, payload)
    except cwl.ConfigWriteError as exc:
        return ProfileResult(status=_failure_status(exc), path=path, reason=str(exc))
    return ProfileResult(status="ok", path=path,
                         changed=tuple(str(slot) for slot in normalized),
                         dropped=dropped, backup_path=backup)


def migrate_active_set(config_dir: Path | str, *,
                       timeout: float = cwl.DEFAULT_TIMEOUT_S,
                       poll: float = cwl.DEFAULT_POLL_S) -> ProfileResult:
    """v1 → v2 迁移：**先备份、可重跑（幂等）**，绝不覆盖坏文件。

    - v1（含空列表）→ ``[0] + 旧集合``（主代理裁决：空子集合迁移后是 ``[0]``，
      不是 ``[]``——v1 的 0 是隐式主宠）；
    - 备份写 ``<file>.v1.bak``（唯一 tmp + 原子发布；中断不在最终名上留残片）。
      已存在时**先校验它是有效的 v1 文档**：通过 → 保留第一份（重跑不覆盖）；
      不通过 → ``backup-invalid``（既不覆盖它也不迁移——备份路径上的任意文件不算
      "备份已完成"）；
    - v2 → ``already-v2``；文件缺失 → ``no-file``（缺失=新装默认 ``[0]``，不必凭空
      造文件）；损坏 → ``corrupt``；未知版本 → ``unsupported-version``（都不写）；
    - 多文件多步骤**不是事务**（文档与日志都如实说），中途失败重跑即可收敛。
    """
    path = active_set_path(config_dir)
    try:
        with cwl.config_write_lock(path.parent, timeout=timeout, poll=poll):
            data, status = cwl.read_json_dict(path)
            if status == "missing":
                return ProfileResult(status="no-file", path=path,
                                     reason="活跃集合文件不在（新装默认 [0]，无需创建）")
            if status != "ok":
                resolved = "unreadable" if status == "unreadable" else "corrupt"
                logger.warning("overlay: 活跃集合 %s，不迁移也不覆盖: %s", resolved, path)
                return ProfileResult(status=resolved, path=path,
                                     reason=f"读取状态 {status}：不迁移、不覆盖")
            version = data.get("version") if data else None
            if version == ACTIVE_SET_VERSION:
                return ProfileResult(status="already-v2", path=path)
            if version != LEGACY_ACTIVE_SET_VERSION:
                return ProfileResult(status="unsupported-version", path=path,
                                     reason=f"版本 {version!r} 不是 v1，不迁移")
            raw_slots = data.get("slots")
            if not isinstance(raw_slots, list):
                return ProfileResult(status="corrupt", path=path,
                                     reason="v1 的 slots 不是列表")
            slots, dropped = _normalize_with_dropped(raw_slots)
            if dropped:
                logger.warning("overlay: v1 活跃集合丢弃非法条目 %s", dropped)
            if SLOT_MIN not in slots:
                slots.insert(0, SLOT_MIN)
            backup = path.with_name(f"{path.name}{ACTIVE_SET_V1_BACKUP_SUFFIX}")
            backup_state = _backup_file(path, backup, validate=_is_v1_active_set_document)
            if backup_state != BACKUP_OK:
                return ProfileResult(
                    status=("backup-invalid" if backup_state == BACKUP_INVALID
                            else "backup-failed"),
                    path=path,
                    reason="备份路径上的既有文件不是有效的 v1 活跃集合"
                           "（不覆盖、不迁移）" if backup_state == BACKUP_INVALID
                           else "备份 v1 文件失败，放弃迁移")
            cwl.atomic_write_json(path, {"version": ACTIVE_SET_VERSION, "slots": slots})
    except cwl.ConfigWriteError as exc:
        return ProfileResult(status=_failure_status(exc), path=path, reason=str(exc))
    return ProfileResult(status="migrated", path=path, backup_path=backup,
                         changed=tuple(str(slot) for slot in slots), dropped=dropped,
                         reason="v1 → v2（已备份 .v1.bak，可重跑）")


# ------------------------------------------------------------------ 身份分配
def allocate_slot(*, active_slots: Iterable = (), max_slot: int = SLOT_MAX) -> int:
    """取**最小非活跃**槽编号（含 0）；``SLOT_COUNT`` 个身份全活跃时抛
    :class:`CapacityFullError`。

    判据只有**活跃集合**：盘上已有 ``config-slot-N.json`` **不**算占用——
    重新生成就该落回那个槽并**恢复**它的档案（"恢复"不是"覆盖"）。旧分配器
    "名额用尽复用最小退役身份并覆盖其位置/设置记忆"的分支被规范废除（跨身份替换
    禁止），故这里没有 recycle 分支：真满就报错，绝不为了"能生成"去覆盖别宠档案。
    """
    active = set(normalize_slots(active_slots, max_slot=max_slot))
    for candidate in range(SLOT_MIN, int(max_slot) + 1):
        if candidate not in active:
            return candidate
    count = int(max_slot) - SLOT_MIN + 1
    raise CapacityFullError(
        f"overlay: 身份空间已满（{SLOT_MIN}..{int(max_slot)} 共 {count} 个身份全部活跃）")


# ------------------------------------------------------------------ 档案读
def read_slot_profile(config_dir: Path | str, slot) -> SlotProfile:
    """读一份槽档案（只读，绝不创建/改写）。

    ``status``：``"ok"`` / ``"missing"``（不存在）/ ``"corrupt"``（非法 JSON）/
    ``"not-object"``（合法 JSON 非对象）/ ``"unreadable"``（OSError）。
    取值一律走 :meth:`SlotProfile.value`：缺键 = **同源默认**，**绝不**回落全局
    主宠当前值（规范 §五「一处真值」；否则关掉的 slot0 会"继承"主宠的新设置）。
    """
    path = slot_profile_path(config_dir, slot)
    data, status = cwl.read_json_dict(path)
    if status == "missing":
        return SlotProfile(path=path, exists=False, status="missing", data={},
                           reason="档案不存在：全部每宠键取同源默认")
    if status == "ok":
        return SlotProfile(path=path, exists=True, status="ok", data=data or {})
    try:
        exists = path.exists()
    except OSError:
        exists = False
    logger.warning("overlay: 槽档案读取状态 %s（不覆盖、不落盘）: %s", status, path)
    return SlotProfile(path=path, exists=exists, status=status, data={},
                       reason=f"读取状态 {status}：按同源默认读，不覆盖原文件")


def read_slot_snapshot(config_dir: Path | str, slot, *,
                       timeout: float = cwl.DEFAULT_TIMEOUT_S,
                       poll: float = cwl.DEFAULT_POLL_S) -> SlotSnapshot:
    """在**同一把目录锁内**读出（档案 + 代次），返回 :class:`SlotSnapshot`。

    为什么必须一次读（审查 P4）：`write_profile_geometry(..., generation=G)` 的保护
    建立在"G 与档案同代"这个前提上。如果调用方先读档案、落盘前再"顺手新读一次代次"，
    读到的可能是**另一代**的代次（reset/重建正好夹在中间），门就形同虚设。契约：

    - token 只从本函数（或 :func:`begin_slot_generation`）拿；
    - 拿到之后**不要**再读代次，直接把它交给几何写 / reset；
    - ``status != "ok"`` 时不要用这个快照做任何写（``generation`` 会是 ``None``）。
    """
    slot_id = _validate_slot(slot)
    path = slot_profile_path(config_dir, slot_id)
    try:
        with cwl.config_write_lock(path.parent, timeout=timeout, poll=poll):
            generations, gen_status = _read_generations(config_dir)
            profile = read_slot_profile(config_dir, slot_id)
            if gen_status == "corrupt":
                return SlotSnapshot(slot=slot_id, status="corrupt", profile=profile,
                                    generation=None,
                                    reason=f"代次文件不可信：{generation_path(config_dir)}")
            if profile.status not in ("ok", "missing"):
                return SlotSnapshot(slot=slot_id, status="corrupt", profile=profile,
                                    generation=None,
                                    reason=f"档案不可信（{profile.status}）")
            return SlotSnapshot(slot=slot_id, status="ok", profile=profile,
                                generation=generations.get(slot_id, 0),
                                reason="档案与代次在同一把锁内读出：token = generation")
    except cwl.ConfigWriteError as exc:
        return SlotSnapshot(slot=slot_id, status=_failure_status(exc), generation=None,
                            reason=str(exc))


# ------------------------------------------------------------------ 档案写
def write_profile_settings(config_dir: Path | str, slot, values: Mapping[str, Any], *,
                           user_customized: bool | None = None,
                           generation: int | None = None,
                           on_broken: str = "refuse",
                           timeout: float = cwl.DEFAULT_TIMEOUT_S,
                           poll: float = cwl.DEFAULT_POLL_S) -> ProfileResult:
    """把若干**每宠键**合并写进该槽档案（保留其它键；锁内重读 + 原子替换）。

    - 只接受 ``PER_PET_KEYS`` 与 ``SLOT_LOCAL_KEYS``；其它键一律 ``refused-key``
      （所有权表的闸门：全局键写进档案 = 第二份真值）；
    - ``user_customized`` 显式传 True/False 才改（None = 保持原值）；
    - 档案缺失即创建（含 ``version``）；损坏/非对象/不可读默认 ``refused-corrupt``，
      ``on_broken="backup-overwrite"`` 才先备份（``<file>.corrupt-<ms>-<pid>``）再重建；
    - 值**不做清洗**（唯一清洗实现是 ``Config``，见 :meth:`SlotProfile.value`）。
    """
    if on_broken not in ("refuse", "backup-overwrite"):
        raise ValueError(f"on_broken 只接受 'refuse'/'backup-overwrite'，收到 {on_broken!r}")
    slot_id = _validate_slot(slot)
    path = slot_profile_path(config_dir, slot_id)
    if not isinstance(values, Mapping):
        raise ProfileError(f"values 必须是映射，收到 {type(values)!r}")
    illegal = tuple(key for key in values if key not in PER_PET_KEYS
                    and key not in SLOT_LOCAL_KEYS)
    if illegal:
        return ProfileResult(status="refused-key", path=path, dropped=illegal,
                             reason=f"这些键不属于每宠档案: {sorted(illegal)}")
    if not values and user_customized is None:
        return ProfileResult(status="no-changes", path=path, reason="没有要写的键")

    backup: Path | None = None
    changed: list[str] = []
    try:
        with cwl.config_write_lock(path.parent, timeout=timeout, poll=poll):
            if _slot_reset_pending(config_dir, slot_id):
                return ProfileResult(status="reset-pending", path=path)
            if generation is not None:
                generations, state = _read_generations(config_dir)
                if state == "corrupt":
                    return ProfileResult(status="refused-corrupt", path=path)
                if generations.get(slot_id, 0) != generation:
                    return ProfileResult(status="stale-generation", path=path)
            data, status = cwl.read_json_dict(path)
            if status == "ok":
                working = dict(data or {})
            elif status == "missing":
                working = {}
            elif on_broken == "backup-overwrite":
                backup = _backup_corrupt(path)
                if backup is None:
                    return ProfileResult(status="backup-failed", path=path,
                                         reason="备份坏档案失败，放弃写入")
                working = {}
            else:
                return ProfileResult(status="refused-corrupt", path=path,
                                     reason=f"档案不可信（{status}），拒绝覆盖")
            working.setdefault("version", PROFILE_FILE_VERSION)
            for key, value in values.items():
                working[key] = value
                changed.append(key)
            if user_customized is not None:
                working["user_customized"] = bool(user_customized)
                changed.append("user_customized")
            cwl.atomic_write_json(path, working)
    except cwl.ConfigWriteError as exc:
        return ProfileResult(status=_failure_status(exc), path=path, reason=str(exc))
    return ProfileResult(status="ok", path=path, changed=tuple(changed),
                         backup_path=backup)


def write_profile_geometry(config_dir: Path | str, slot, geometry: Mapping[str, Any], *,
                           generation: int | None = None,
                           timeout: float = cwl.DEFAULT_TIMEOUT_S,
                           poll: float = cwl.DEFAULT_POLL_S) -> ProfileResult:
    """把几何（``rx`` / ``ry`` / ``screen_name`` / ``facing`` / ``scale``）合并写回档案。

    - 只写传入的键（其余键原样保留）；非法值丢弃并在 ``dropped`` 里报告；
      全部非法 → ``invalid-geometry``（不落盘）；
    - 显式 ``None`` 是合法取值（= "没有位置记忆"，对齐 ``Config._NULL_ACCEPTING_KEYS``）；
    - ``generation`` 非 None 时做**代次门**：与当前代次不符 → ``stale-generation``，
      一个字节不写（迟到几何写不得覆盖 reset / 重建后的结果）。代次文件损坏 →
      ``refused-corrupt``（绝不把损坏当代次 0 放行）。**token 只许来自
      :func:`read_slot_snapshot` / :func:`begin_slot_generation`，且拿到后不得再读一次
      代次**（"保存前新读"会让门失效）；
    - 档案损坏 → ``refused-corrupt``（几何是无人值守自存，不做自动覆盖）。

    其余失败态沿用写类 API 的公共词表：``lock-timeout`` / ``lock-unavailable`` /
    ``write-failed``（都没有落盘）。
    """
    slot_id = _validate_slot(slot)
    path = slot_profile_path(config_dir, slot_id)
    clean, dropped = _clean_geometry(geometry)
    if not clean:
        return ProfileResult(status="invalid-geometry", path=path, dropped=dropped,
                             reason="没有合法几何键可写")
    expected = int(generation) if generation is not None else None
    try:
        with cwl.config_write_lock(path.parent, timeout=timeout, poll=poll):
            if expected is not None:
                current, gen_status = _read_generations(config_dir)
                if gen_status == "corrupt":
                    return ProfileResult(status="refused-corrupt", path=path,
                                         reason="代次文件损坏：拒绝写入（不把损坏当代次 0）")
                if current.get(slot_id, 0) != expected:
                    return ProfileResult(
                        status="stale-generation", path=path,
                        generation=current.get(slot_id, 0),
                        reason=f"代次 {expected} 已过期（当前 {current.get(slot_id, 0)}）")
            if _slot_reset_pending(config_dir, slot_id):
                return ProfileResult(status="reset-pending", path=path)
            data, status = cwl.read_json_dict(path)
            if status == "ok":
                working = dict(data or {})
            elif status == "missing":
                working = {}
            else:
                return ProfileResult(status="refused-corrupt", path=path,
                                     reason=f"档案不可信（{status}），拒绝覆盖")
            working.setdefault("version", PROFILE_FILE_VERSION)
            working.update(clean)
            cwl.atomic_write_json(path, working)
    except cwl.ConfigWriteError as exc:
        return ProfileResult(status=_failure_status(exc), path=path, reason=str(exc))
    return ProfileResult(status="ok", path=path, changed=tuple(clean), dropped=dropped)


def reset_slot_profile(config_dir: Path | str, slot, *, generation: int | None = None,
                       timeout: float = cwl.DEFAULT_TIMEOUT_S,
                       poll: float = cwl.DEFAULT_POLL_S) -> ProfileResult:
    """本宠「恢复默认」的存储动作：**保身份、保位置、重置每宠偏好**。

    - **保身份**：同一个槽、同一个档案文件（不被删除、不被别宠档案替换）；
    - **保位置**：``rx`` / ``ry`` / ``screen_name`` 原样保留——"回到默认落位"是
      独立操作，不属于"恢复默认"（规范 §五）；
    - **重置偏好**：``PER_PET_PREFERENCE_KEYS``（含**朝向 facing**，它是偏好不是
      位置 → 回同源默认 ``left``）从档案里移除，读取侧落回同源默认；
      ``user_customized`` 与 ``version`` 原样保留（本地旗标/文档版本不是偏好值）；
    - **代次 +1（先落盘）**：reset 会作废该槽的迟到几何写。写顺序固定为
      "先代次、后档案"，两种中断都不留危险态：代次写失败 → ``write-failed``
      （**什么都没变**）；档案写失败 → ``partial``（代次已推进、档案未重置，
      ``result.generation`` 给出新代次供调用方重同步/重跑）。显式传入 ``generation``
      时是**代次门**：不符 → ``stale-generation``（不重置、不推进代次）；
    - 无档案 → ``no-profile``（不凭空创建），但**照样推进代次**——迟到的写必须被
      挡掉，否则"reset 了但没档案"会让旧几何又写回来；
    - 档案损坏 → ``refused-corrupt``（清理损坏档案属"清除档案"功能，规范明确不做）；
    - **不碰全局配置**：全局 reset 的作用域不在本函数（接线批负责），本函数只写
      这一个槽文件与代次侧车。
    """
    slot_id = _validate_slot(slot)
    path = slot_profile_path(config_dir, slot_id)
    expected = int(generation) if generation is not None else None
    try:
        with cwl.config_write_lock(path.parent, timeout=timeout, poll=poll):
            # 代次先读：损坏就什么都不写（不把损坏当"代次 0"，也不留半截 reset）
            generations, gen_status = _read_generations(config_dir)
            if gen_status == "corrupt":
                return ProfileResult(status="refused-corrupt", path=path,
                                     reason="代次文件损坏：拒绝 reset")
            current = generations.get(slot_id, 0)
            if expected is not None and current != expected:
                return ProfileResult(status="stale-generation", path=path,
                                     generation=current,
                                     reason=f"代次 {expected} 已过期（当前 {current}）")
            data, status = cwl.read_json_dict(path)
            if status not in ("ok", "missing"):
                return ProfileResult(status="refused-corrupt", path=path,
                                     reason=f"档案不可信（{status}），拒绝重置")
            bumped = current + 1
            if status == "missing":
                # 没有档案可重置，但照样推进代次：迟到的写必须被作废
                generations[slot_id] = bumped
                document = _generation_document(generations)
                previous, _ = cwl.read_json_dict(generation_path(config_dir))
                if (previous or {}).get("pending_resets"):
                    document["pending_resets"] = [n for n in previous["pending_resets"] if n != slot_id]
                cwl.atomic_write_json(generation_path(config_dir), document)
                return ProfileResult(status="no-profile", path=path, generation=bumped,
                                     reason="没有档案可重置；已推进代次作废迟到写")
            source = dict(data or {})
            kept = tuple(key for key in POSITION_KEYS if key in source)
            cleared = tuple(key for key in sorted(PER_PET_PREFERENCE_KEYS)
                            if key in source)
            rebuilt: dict[str, Any] = {"version": source.get("version", PROFILE_FILE_VERSION)}
            if "user_customized" in source:
                rebuilt["user_customized"] = source["user_customized"]
            for key in kept:
                rebuilt[key] = source[key]
            # 写顺序 = **先代次、后档案**（审查 P1）。反过来的话，第二次 replace 失败会
            # 留下"旧代次 + 已重置档案"：迟到几何写带着仍然有效的旧代次继续写回
            # scale/facing，reset 被静默抵消——正是代次门要防的那件事。
            # 先代次则两种中断都是安全态：代次写失败 = 什么都没变（write-failed）；
            # 档案写失败 = 代次已推进、档案未重置（partial，旧代次写已被拒）。
            generations[slot_id] = bumped
            try:
                document = _generation_document(generations)
                previous, _ = cwl.read_json_dict(generation_path(config_dir))
                pending = list((previous or {}).get("pending_resets", []))
                document["pending_resets"] = sorted(set(pending) | {slot_id})
                cwl.atomic_write_json(generation_path(config_dir), document)
            except cwl.ConfigWriteError as exc:
                return ProfileResult(status=_failure_status(exc), path=path,
                                     reason=f"代次未推进、档案未动（无变化）: {exc}")
            try:
                cwl.atomic_write_json(path, rebuilt)
                document["pending_resets"] = [n for n in document["pending_resets"] if n != slot_id]
                cwl.atomic_write_json(generation_path(config_dir), document)
            except cwl.ConfigWriteError as exc:
                return ProfileResult(
                    status="partial", path=path, generation=bumped, kept=kept,
                    cleared=cleared,
                    reason=f"代次已推进到 {bumped}，但档案写入失败（{exc}）："
                           "旧代次的迟到写已被拒；按这个 gen 重跑 reset 即收敛")
    except cwl.ConfigWriteError as exc:
        return ProfileResult(status=_failure_status(exc), path=path, reason=str(exc))
    return ProfileResult(status="ok", path=path, kept=kept, cleared=cleared,
                         generation=bumped)


# ------------------------------------------------------------------ slot0 迁移
def migrate_slot0_profile(config_dir: Path | str, *, source: Path | str | None = None,
                          timeout: float = cwl.DEFAULT_TIMEOUT_S,
                          poll: float = cwl.DEFAULT_POLL_S) -> Slot0Migration:
    """把全局配置里的每宠键（含几何）**幂等、先备份**地迁进 ``config-slot-0.json``。

    ``status`` 取值与含义：

    - ``created``：先备份、清洗抽档案、再清源每宠键；
    - ``already-exists``：已有档案不覆盖、**但照样清源每宠键**——与 ``Config.save()``
      的全局清理同一条规则（见模块头）。源里那批键是**未被本次迁移采用**的值，清理前
      先保证恢复路径留住了它们：规范备份若已是这批值就直接用，否则另存一份时间戳
      恢复副本（``recovery_path``）；拿不到恢复副本就**不清源**并返回
      ``backup-failed``。未采用的键名登记在 ``retained_keys``（可诊断）；
    - ``no-source``：源文件不存在，或源里没有任何每宠键（无事可做，不造空档案）；
    - ``source-corrupt`` / ``source-unreadable``：源读不出可信对象 → 不写目标；
    - ``target-corrupt``：目标（``config-slot-0.json``）不可信 → **一个字节都不写、
      也不清源**。这是**单槽**问题（``slot_only_problem``）：调用方把它降级成
      "slot0 这个槽先空着"，而不是让整个应用起不来——全局配置与其它槽根本没被碰过，
      拿一份坏档案当"全应用失败"是把范围判错了（2026-10-10 ops D2）；
    - ``refused-slot-source``：源是**某个槽档案**（``config-slot-N.json``）。
      ``--slot N`` 形态下进程的"全局配置"就是别宠档案、``--slot 0`` 形态下源即目标，
      两者都必须拒绝（跨身份替换禁止；一个字节不写）；
    - ``backup-invalid``：备份路径上已有文件但**不是**迁移前的全局配置（缺我们要迁的
      每宠键）→ 不覆盖它、也不迁移；
    - ``backup-failed`` / ``write-failed`` / ``lock-timeout``：失败态，源与目标都不丢，
      重跑即可（幂等）。

    ``source`` 缺省 ``<config_dir>/config.json``。**不是事务**：备份与写目标之间中断
    只会留下可重跑的状态。
    """
    config_dir = Path(config_dir)
    target = slot_profile_path(config_dir, SLOT_MIN)
    src = Path(source) if source is not None else global_config_path(config_dir)
    if _SLOT_PROFILE_RE.match(src.name):
        return Slot0Migration(
            status="refused-slot-source", profile_path=target, source_path=src,
            reason="迁移源不得是槽档案（--slot N 的全局配置就是别宠档案；--slot 0 时源即目标）")
    existed = False
    try:
        with cwl.config_write_lock(target.parent, timeout=timeout, poll=poll):
            existing, target_status = cwl.read_json_dict(target)
            existed = target_status != "missing"
            if existed and target_status != "ok":
                return Slot0Migration(status="target-corrupt", profile_path=target,
                                      source_path=src, reason="slot0 档案不可信：不覆盖、不清源")
            data, status = cwl.read_json_dict(src)
            if status == "missing":
                return Slot0Migration(status="no-source", profile_path=target,
                                      source_path=src, reason="全局配置不存在")
            if status != "ok":
                resolved = "source-unreadable" if status == "unreadable" else "source-corrupt"
                return Slot0Migration(status=resolved, profile_path=target,
                                      source_path=src,
                                      reason=f"全局配置读取状态 {status}：不写目标")
            copied = tuple(key for key in _EXTRACT_KEYS if key in (data or {}))
            if not copied:
                return Slot0Migration(status="already-exists" if existed else "no-source",
                                      profile_path=target, source_path=src,
                                      reason="全局配置里没有任何每宠键，无需迁移")
            backup = src.with_name(f"{src.name}{PROFILE_MIGRATION_BACKUP_SUFFIX}")
            backup_state = _backup_file(
                src, backup,
                validate=lambda existing: _is_global_config_snapshot(existing, copied))
            if backup_state != BACKUP_OK:
                return Slot0Migration(
                    status=("backup-invalid" if backup_state == BACKUP_INVALID
                            else "backup-failed"),
                    profile_path=target, source_path=src,
                    reason="备份路径上的既有文件不是迁移前的全局配置（不覆盖、不迁移）"
                           if backup_state == BACKUP_INVALID
                           else "备份全局配置失败：中止迁移，不写档案")
            from .config import normalize_pet_profile_snapshot

            retained: tuple[str, ...] = ()
            recovery: Path | None = None
            if existed:
                # 目标已存在：**绝不覆盖目标**，但源每宠键照样清算——与
                # ``Config.save()`` 的全局清理是**同一条规则**（它对全局身份无条件
                # ``pop(PER_PET_KEYS | SLOT_LOCAL_KEYS)``）。迁移侧若"不清源"，源里那批
                # 键活不过本进程的下一次保存，``retained_keys`` 就成了骗人的字段
                # （文件里根本留不住），而且全局配置 + 槽档案同时装着每宠值 = 规范
                # 禁止的双真值。这里源里的键是**未被本次迁移采用**的值，清理前先保证
                # 恢复路径真的留住了它们：规范备份本来就是这批值就直接用；该路径已被
                # 另一份快照占用（**不覆盖它**）就另存一份时间戳恢复副本。取不到副本
                # 就不清源。
                retained = tuple(copied)
                values = {key: data[key] for key in copied}
                if not _backup_holds_values(backup, values):
                    recovery = _recovery_copy_path(src)
                    if not _copy_file(src, recovery):
                        return Slot0Migration(
                            status="backup-failed", profile_path=target, source_path=src,
                            backup_path=backup,
                            reason="既有备份不含当前每宠值且恢复副本写不出："
                                   "不清源、不写目标（下次重跑即可收敛）")
                    logger.warning(
                        "slot0 迁移：目标档案已存在（不覆盖）；源每宠键已清理，"
                        "未采用的源值在规范备份之外另存于 %s（规范备份 %s 未被覆盖）：%s",
                        recovery, backup, ", ".join(retained))
                else:
                    logger.warning(
                        "slot0 迁移：目标档案已存在（不覆盖）；源每宠键已清理，"
                        "未采用的源值可从备份取回 %s：%s",
                        backup, ", ".join(retained))
            else:
                cleaned = normalize_pet_profile_snapshot(data)
                profile = {key: cleaned[key] for key in PER_PET_KEYS if key in cleaned}
                profile.update(version=PROFILE_FILE_VERSION, user_customized=True)
                cwl.atomic_write_json(target, profile)
            remaining = {key: value for key, value in data.items()
                         if key not in PER_PET_KEYS and key not in SLOT_LOCAL_KEYS
                         and key != "click_sound_path"}
            cwl.atomic_write_json(src, remaining)
    except cwl.ConfigWriteError as exc:
        return Slot0Migration(status=_failure_status(exc), profile_path=target,
                              source_path=src, reason=str(exc))
    source_backup = recovery or backup
    return Slot0Migration(
        status="already-exists" if existed else "created",
        profile_path=target, source_path=src, backup_path=backup, recovery_path=recovery,
        copied_keys=() if existed else copied,
        retained_keys=retained,
        reason=(f"备份已验证；目标档案已存在：不覆盖目标；源每宠键已清理，"
                f"未采用的源值留存于 {source_backup.name}（{source_backup}）"
                if existed else "备份已验证、档案已建、源每宠键已清理"))


# ------------------------------------------------------------------ 数据代次
#: 代次表键的规范写法：十进制无前导零（``0`` / ``7`` / ``128``）。``"01"`` / ``"+1"`` /
#: ``" 1"`` / ``"1.0"`` 都是"看起来像但不是我写的"——一律算坏。
_GENERATION_KEY_RE = re.compile(r"^(0|[1-9][0-9]*)$")


def _read_generations(config_dir: Path | str) -> tuple[dict[int, int], str]:
    """读代次映射，返回 ``(映射, 状态)``；状态 ``ok`` / ``missing`` / ``corrupt``。

    **严格整表口径**（审查 P2）：任何一条非法条目（bool/float/字符串值、非规范或
    越界的 slot 键、负代次、缺 ``version``/``generations``、版本不匹配）都让**整表**
    不可信 —— 只跳过那一条等于把那个槽当成全新槽（代次 0），迟到的旧代次写就会被
    错误接受，保护静默失效。缺项（表里没有该槽）才算新槽 = 0。
    """
    data, status = cwl.read_json_dict(generation_path(config_dir))
    if status == "missing":
        return {}, "missing"
    if status != "ok":
        return {}, "corrupt"
    if data.get("version") != GENERATION_FILE_VERSION:
        logger.warning("overlay: 代次表版本不匹配 (%r)，整表判为不可信",
                       data.get("version"))
        return {}, "corrupt"
    pending = data.get("pending_resets", [])
    if not isinstance(pending, list) or any(type(n) is not int or not 0 <= n <= SLOT_MAX for n in pending):
        return {}, "corrupt"
    raw = data.get("generations")
    if not isinstance(raw, dict):
        logger.warning("overlay: 代次表 generations 不是对象，整表判为不可信")
        return {}, "corrupt"
    result: dict[int, int] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not _GENERATION_KEY_RE.match(key):
            logger.warning("overlay: 代次表 slot 键不规范 (%r)，整表判为不可信", key)
            return {}, "corrupt"
        slot = int(key)
        if not SLOT_MIN <= slot <= SLOT_MAX:
            logger.warning("overlay: 代次表 slot 键越界 (%r)，整表判为不可信", key)
            return {}, "corrupt"
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            logger.warning("overlay: 代次表代次非法 (%r=%r)，整表判为不可信", key, value)
            return {}, "corrupt"
        result[slot] = value
    return result, "ok"


def _slot_reset_pending(config_dir, slot):
    data, status = cwl.read_json_dict(generation_path(config_dir))
    if status == "missing":
        return False
    pending = (data or {}).get("pending_resets", [])
    if not isinstance(pending, list) or any(type(n) is not int or not 0 <= n <= SLOT_MAX for n in pending):
        return True
    return slot in pending


def generation_status(config_dir: Path | str) -> str:
    """代次侧车状态：``"ok"`` / ``"missing"`` / ``"corrupt"``（调用方免异常预检）。"""
    return _read_generations(config_dir)[1]


def slot_generation(config_dir: Path | str, slot) -> int:
    """该槽当前代次（无记录 = 0）。

    代次文件损坏/未知版本时抛 :class:`ProfileStateError`：**不返回 0**——0 会被
    当成"全新槽"，从而错误接受迟到的旧写（"不把损坏当新装"）。需要非抛异常预检的
    调用方先用 :func:`generation_status`。
    """
    slot_id = _validate_slot(slot)
    generations, status = _read_generations(config_dir)
    if status == "corrupt":
        raise ProfileStateError(
            f"代次文件不可信（{generation_path(config_dir)}）：拒绝按 0 处理")
    return generations.get(slot_id, 0)


def begin_slot_generation(config_dir: Path | str, slot, *,
                          timeout: float = cwl.DEFAULT_TIMEOUT_S,
                          poll: float = cwl.DEFAULT_POLL_S) -> ProfileResult:
    """推进该槽代次并持久化，返回新值（``generation`` 字段）。

    一次"新住客入住"（生成/重建）推进一次：槽序号相同 ≠ 同一只宠，迟到的几何写
    带着上一代代次，必须被 :func:`write_profile_geometry` 的代次门挡掉。
    代次文件损坏 → ``refused-corrupt``（不把损坏当新装：静默从 0 重来会让老代次的
    写被错误接受）。
    """
    slot_id = _validate_slot(slot)
    path = generation_path(config_dir)
    try:
        with cwl.config_write_lock(path.parent, timeout=timeout, poll=poll):
            generations, status = _read_generations(config_dir)
            if status == "corrupt":
                return ProfileResult(status="refused-corrupt", path=path,
                                     reason="代次文件不可信：拒绝覆盖")
            if _slot_reset_pending(config_dir, slot_id):
                return ProfileResult(status="reset-pending", path=path)
            new_value = generations.get(slot_id, 0) + 1
            generations[slot_id] = new_value
            document = _generation_document(generations)
            previous, _ = cwl.read_json_dict(path)
            if (previous or {}).get("pending_resets"):
                document["pending_resets"] = previous["pending_resets"]
            cwl.atomic_write_json(path, document)
    except cwl.ConfigWriteError as exc:
        return ProfileResult(status=_failure_status(exc), path=path, reason=str(exc))
    return ProfileResult(status="ok", path=path, generation=new_value)


def _generation_document(generations: Mapping[int, int]) -> dict:
    """代次侧车文档（键排序 → 文件 diff 稳定，便于人工核对）。"""
    return {
        "version": GENERATION_FILE_VERSION,
        "generations": {str(key): int(generations[key]) for key in sorted(generations)},
    }


# ------------------------------------------------------------------ 内部工具
def _failure_status(exc: cwl.ConfigWriteError) -> str:
    """把写锁/写盘的异常翻成写类 API 的 ``status``（调用方只看状态，不看类型）。"""
    if isinstance(exc, cwl.WriteLockTimeout):
        return "lock-timeout"
    if isinstance(exc, cwl.AtomicWriteFailed):
        return "write-failed"
    # 既不是等锁超时也不是写失败：拿不到锁原语/打不开锁文件（权限、只读盘、平台无原语）
    return "lock-unavailable"


def _finite_or_none(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _clean_geometry(geometry: Mapping[str, Any]) -> tuple[dict, tuple[Any, ...]]:
    """几何取值过滤：合法值保留（显式 ``None`` 也算合法 = 没有记忆），非法丢弃。

    ``rx`` / ``ry`` 有限数；``screen_name`` 非空字符串；``facing`` 枚举；
    ``scale`` 正有限数。不做区间夹取（那是 ``Config`` 的事）。
    """
    clean: dict[str, Any] = {}
    dropped: list[Any] = []
    if not isinstance(geometry, Mapping):
        return {}, (geometry,)
    for key in GEOMETRY_KEYS:
        if key not in geometry:
            continue
        raw = geometry[key]
        if raw is None:
            clean[key] = None
            continue
        if key in ("rx", "ry"):
            number = _finite_or_none(raw)
            if number is None:
                dropped.append(key)
            else:
                clean[key] = number
        elif key == "screen_name":
            text = raw.strip() if isinstance(raw, str) else ""
            if text:
                clean[key] = text
            else:
                dropped.append(key)
        elif key == "facing":
            if raw in ("left", "right"):
                clean[key] = raw
            else:
                dropped.append(key)
        else:  # scale
            number = _finite_or_none(raw)
            if number is None or number <= 0:
                dropped.append(key)
            else:
                clean[key] = number
    return clean, tuple(dropped)


#: ``_backup_file`` 的三态返回（调用方据此给出 ``backup-invalid`` / ``backup-failed``）。
BACKUP_OK = "ok"
BACKUP_INVALID = "invalid"
BACKUP_FAILED = "failed"


def _copy_file(src: Path, dst: Path) -> bool:
    """**唯一 tmp → 完整写 → 原子发布**地复制一份文件（中断不在最终名上留残片）。

    直接 ``copy2(src, dst)`` 的致命处（审查 P3）：复制中断会在最终名上留下半截文件，
    而"已完成"的判据是 ``dst.exists()``——残片会被当成"备份已做好"，于是真正该做的
    备份永远没做，之后任何"先备份再写"的承诺都是空的。
    """
    temp = dst.with_name(f"{dst.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, temp)
        atomic_replace_with_retry(temp, dst)   # 发布（骑过瞬时共享冲突）
        return True
    except OSError:
        logger.warning("复制文件失败: %s -> %s", src, dst, exc_info=True)
        return False
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _backup_file(src: Path, dst: Path, *, validate=None) -> str:
    """先备份再写的前半步：``BACKUP_OK`` / ``BACKUP_INVALID`` / ``BACKUP_FAILED``。

    - ``dst`` 不存在 → 复制（唯一 tmp + 原子发布），返回 ``BACKUP_OK``；
    - ``dst`` 已存在 → **必须验证它是"正确的迁移前快照"**（``validate(dst)``）才算数：
      通过 → ``BACKUP_OK``（保留第一份，不覆盖）；
      不通过 → ``BACKUP_INVALID``，**既不覆盖它也不继续**（那可能是用户的别的东西，
      也可能是伪造/损坏的"证据"）；``validate`` 为 None 时任何已有文件都算未验证 →
      ``BACKUP_INVALID``（宁可停下让人看一眼，也不假装备份做了）。
    """
    try:
        if dst.exists():
            if validate is None or not validate(dst):
                logger.warning("备份路径上已有未经校验的文件，拒绝继续: %s", dst)
                return BACKUP_INVALID
            return BACKUP_OK
    except OSError:
        logger.warning("检查备份路径失败: %s", dst, exc_info=True)
        return BACKUP_FAILED
    return BACKUP_OK if _copy_file(src, dst) else BACKUP_FAILED


#: 恢复副本名字标记：规范备份路径被**另一份快照**占用时才用（时间戳 + pid，零覆盖）。
PROFILE_MIGRATION_RECOVERY_MARKER = ".pre-slot0-migration-recovery"


def _recovery_copy_path(src: Path) -> Path:
    """恢复副本路径（时间戳 + pid ⇒ 结构上不可能覆盖任何既有文件）。"""
    stamp = f"{int(time.time() * 1000)}-{os.getpid()}"
    return src.with_name(f"{src.name}{PROFILE_MIGRATION_RECOVERY_MARKER}-{stamp}.bak")


def _backup_holds_values(path: Path, values: Mapping[str, Any]) -> bool:
    """既有备份是否**装着这批值**——键在不算数，值也要逐条相等。

    与 :func:`_is_global_config_snapshot` 的区别是"证据强度"：那个只查键在不在
    （用作"这文件是不是一份全局配置快照"的入场判据），本函数用来回答"**这一批
    即将被清掉的源值**，能不能从这份备份取回"。后者答否时调用方必须另存恢复副本，
    否则清理就成了不可恢复的删除。
    """
    data, status = cwl.read_json_dict(path)
    if status != "ok" or not isinstance(data, dict):
        return False
    return all(key in data and data[key] == value for key, value in values.items())


def _is_v1_active_set_document(path: Path) -> bool:
    """校验"现有活跃集合备份"确实是 v1 文档（来源/形状都对才认）。"""
    data, status = cwl.read_json_dict(path)
    if status != "ok" or not isinstance(data, dict):
        return False
    return (data.get("version") == LEGACY_ACTIVE_SET_VERSION
            and isinstance(data.get("slots"), list))


def _is_global_config_snapshot(path: Path, copied_keys: tuple[str, ...]) -> bool:
    """校验"现有全局配置备份"确实是迁移前的全局配置（含我们要迁的每宠键）。"""
    data, status = cwl.read_json_dict(path)
    if status != "ok" or not isinstance(data, dict):
        return False
    return all(key in data for key in copied_keys)


def _backup_corrupt(path: Path) -> Path | None:
    """损坏/不认识的文件留一份取证备份（``<name>.corrupt-<毫秒>-<pid>``）。"""
    stamp = f"{int(time.time() * 1000)}-{os.getpid()}"
    dst = path.with_name(f"{path.name}{CORRUPT_BACKUP_MARKER}{stamp}")
    if _copy_file(path, dst):
        return dst
    logger.warning("备份不可信文件失败: %s", path)
    return None
