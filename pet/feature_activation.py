# -*- coding: utf-8 -*-
"""功能模块激活层（期 1）：启用集合的迁移判定 + 进程级快照。

设计见 ``.scratch/feature-modules/SPEC.md``。本模块只管三件事：

1. **一次性迁移判定**：``feature_activation`` 为 ``None``（未判定）时，区分
   新装/升级写回初始启用集合——升级继承 ``MIGRATION_BASELINE``（全模块），
   新装默认纯核心（``PURE_CORE_FOR_NEW_INSTALL = True``，全部模块已过
   「不出现、不启动、不写回」验收后于 2026-10-21 生效）。
2. **本次运行快照**：主进程在创建任何可选服务之前 ``decide_and_freeze`` 一次，
   之后 ``is_active`` 只读快照——功能页写盘（下次启动生效）绝不影响本次运行。
3. **写下次启动值**：``write_enabled`` 给功能管理页用，校验后整值提交。

快照未冻结时（测试、工具脚本、独立设置进程）``is_active`` 一律 True——
门控是调用点可选接入的，旧行为默认不变。

本模块禁止 import Qt（启动早期使用，import 必须便宜）。
"""
from __future__ import annotations

import logging
import os

from .features import FEATURES, MIGRATION_BASELINE, normalize_enabled

log = logging.getLogger(__name__)

#: 启用集合结构的当前版本（不认识的高版本按损坏处理：保留原数据 + 诊断）。
CURRENT_VERSION = 1

#: 新装用户默认纯核心（enabled=[]）：全部 14 个模块已过「不出现、不启动、
#: 不写回」门控验收（tests/test_feature_modules_gating.py 84 例），期 5 落地。
#: 升级安装不受本开关影响——永远继承 MIGRATION_BASELINE。
PURE_CORE_FOR_NEW_INSTALL = True

#: 主进程拉起独立设置进程时透传本次冻结快照的环境变量（空串 = 纯核心，
#: 与「未设置」可区分）。盘上未判定时设置进程以它为准——新装用户在主进程
#: 首次自然保存前打开设置，不会拿到全激活基线（盲审存疑 B）。
ENV_FEATURE_SNAPSHOT = "DSH_PET_FEATURE_SNAPSHOT"

#: 本次运行冻结的启用集合；None = 未冻结（is_active 一律 True）。
_snapshot: frozenset[str] | None = None


def _is_upgrade_install(config) -> bool:
    """升级安装判定：有任一既有数据痕迹即按升级处理（继承全部模块）。

    「config.json 缺失但 slot 档案/会话目录在」不能判新装（SPEC §4）。
    """
    if config.path.is_file():
        return True
    try:
        if any(config.dir.glob("config-slot-*.json")):
            return True
        if (config.dir / "sessions").is_dir():
            return True
    except OSError:
        # 目录不可读时按升级处理（保守：宁可多激活，不可藏掉用户在用的功能）
        return True
    # 已知限制（盲审存疑 A，2026-10-21 决议）：从未留下任何痕迹的老用户
    # （没保存过配置、没聊过天）会被判成新装拿到纯核心。不能用日志当痕迹——
    # 新装第二次启动也会有自己的历史日志，会误判成升级（实测推演）；此类
    # 用户可在「功能」页一键全部开启，发版说明需写明。
    return False


def decide_and_freeze(config) -> frozenset[str]:
    """启动早期调用一次：完成迁移判定（若需要）并冻结本次运行快照。

    - 已判定（盘上有合法值）：直接冻结，未知 ID 进不了快照但保留在盘上；
    - 未判定（None）：按新装/升级置出初始集合——**只进内存草稿，不主动落盘**：
      判定是确定性的（同一份磁盘痕迹必然判出同一结果），进程死掉重判无妨；
      草稿随下一次自然配置保存落盘。启动链上主动 save 会过早触发 slot-0
      档案引导（`Config.save` 的 pet_profiles  bootstrap），破坏「AppShell
      构造前零配置副作用」的启动不变量（tests/test_overlay_instance_gate.py）；
    - 损坏（非 dict / 版本不认识 / enabled 不是 list）：保留原数据、打诊断，
      保守冻结全模块——绝不拿默认值覆盖用户数据；
    - 合法空列表是明确选择，原样冻结（绝不回退成全激活）。
    """
    global _snapshot
    raw = config.data.get("feature_activation")
    if raw is None:
        upgrade = _is_upgrade_install(config)
        enabled = [] if (not upgrade and PURE_CORE_FOR_NEW_INSTALL) else list(MIGRATION_BASELINE)
        config.set("feature_activation", {"version": CURRENT_VERSION, "enabled": enabled})
        log.info("功能模块激活：%s安装，初始启用集合 %d 项（随下次配置保存落盘）",
                 "升级" if upgrade else "新装", len(enabled))
        _snapshot = frozenset(enabled)
        return _snapshot
    if not isinstance(raw, dict) or raw.get("version") != CURRENT_VERSION \
            or not isinstance(raw.get("enabled"), list):
        log.warning("功能模块激活：配置值损坏或版本不认识（保留原数据，本次按全模块运行）: %r",
                    type(raw).__name__)
        _snapshot = frozenset(MIGRATION_BASELINE)
        return _snapshot
    try:
        enabled, unknown = normalize_enabled(raw["enabled"])
    except ValueError:
        # 元素级损坏（如 ["weather", 3]）：同整体损坏口径——保留原数据 + 诊断，
        # 保守冻结全模块，不拿默认值覆盖
        log.warning("功能模块激活：启用列表元素损坏（保留原数据，本次按全模块运行）")
        _snapshot = frozenset(MIGRATION_BASELINE)
        return _snapshot
    if unknown:
        log.info("功能模块激活：%d 个未知模块 ID 保留在盘上但不激活: %s", len(unknown), unknown)
    _snapshot = frozenset(enabled)
    return _snapshot


def is_active(feature_id: str) -> bool:
    """本次运行该模块是否可用（激活快照 ∧ 硬依赖满足）。

    快照未冻结（测试/工具/独立设置进程）恒 True。只勾了模块但没勾它的
    硬依赖（如只开 file_interpret 不开 chat）时按不可用计——门控三层
    （菜单/设置/执行）共用这个判据，不会出现「入口在、点了没反应」。
    不在清单里的 id（核心能力、空串）不受门控，恒 True。
    """
    if feature_id not in FEATURES:
        return True
    if _snapshot is None:
        return True
    from .features import resolve_dependencies

    return feature_id in resolve_dependencies(_snapshot).active


def snapshot() -> frozenset[str] | None:
    """当前冻结的快照（诊断/测试用）；None = 未冻结。"""
    return _snapshot


def snapshot_env_value() -> str | None:
    """主进程拉起设置进程时透传的快照串（``ENV_FEATURE_SNAPSHOT`` 的值）；
    未冻结返回 None（不注入，设置进程走自己的判定链）。"""
    if _snapshot is None:
        return None
    return ",".join(sorted(_snapshot))


def pending_enabled(config) -> list[str]:
    """功能页展示的「下次启动」启用集：盘上已判定的值；未判定时预览跟随
    **有效判定**——本进程已冻结的快照（主进程=启动判定结果；设置进程=
    透传快照/自己的判定），未冻结才给迁移基线预览。
    否则新装用户的功能页会显示全模块勾选，任何一次保存都把全激活写进盘，
    纯核心名存实亡（2026-10-21 复审补漏：is_active 投影与 pending 预览必须同源）。"""
    raw = config.data.get("feature_activation")
    if isinstance(raw, dict) and isinstance(raw.get("enabled"), list):
        try:
            enabled, _ = normalize_enabled(raw["enabled"])
        except ValueError:
            return list(MIGRATION_BASELINE)
        return enabled
    snap = snapshot()
    if snap is not None:
        return sorted(snap)
    return list(MIGRATION_BASELINE)


def write_enabled(config, enabled: list[str]) -> bool:
    """功能页保存入口：校验后整值提交「下次启动」启用集；返回是否落盘成功。

    未知/非法 ID 直接拒绝（调用方是 UI，给的是清单内的 id）；合法空列表允许。
    只写盘，不动本次运行快照——重启才生效。
    """
    unknown = [fid for fid in enabled if fid not in FEATURES]
    if unknown:
        log.warning("功能模块激活：拒绝写入未知模块 ID: %s", unknown)
        return False
    deduped = list(dict.fromkeys(enabled))
    config.set("feature_activation", {"version": CURRENT_VERSION, "enabled": deduped})
    return bool(config.save())


def reset_snapshot_for_tests() -> None:
    """测试专用：解除冻结，恢复 is_active 恒 True 的默认态。"""
    global _snapshot
    _snapshot = None


def freeze_for_settings_process(config) -> None:
    """独立设置进程：按全局 config.json 的「下次启动」集合冻结快照。

    设置 UI 必须反映**待生效**状态（主进程的运行快照是它启动时冻结的，
    两边本来就不必相同）。本进程不运行功能服务，is_active 只影响设置层
    投影（哪些标签/组/页出现）与预览入口。
    传入的可能是 slot 实例配置（feature_activation 是全局键，真值只在
    主 config.json），所以一律直读主文件；**不写盘**——落盘是主进程启动时
    的职责。盘上未判定时在内存里做同一套新装/升级判定（与主进程同一份
    磁盘痕迹必然同结论）：否则新装用户在主进程首次自然保存前打开设置，
    会拿到全激活基线，一次保存就把「全部启用」写进盘（盲审存疑 B）。
    读不到/值损坏就冻结迁移基线（全激活=旧行为，保守方向）。
    """
    import json

    global _snapshot
    raw = None
    try:
        main_path = config.dir / "config.json"
        if main_path.is_file():
            data = json.loads(main_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                raw = data.get("feature_activation")
    except (OSError, ValueError):
        raw = None
    if raw is None:
        env = os.environ.get(ENV_FEATURE_SNAPSHOT)
        if env is not None:
            # 主进程透传的本次冻结快照（空串 = 纯核心）：与主进程判定同源，
            # 新装不会在「首次自然保存前开设置」拿到全激活基线（盲审存疑 B）
            try:
                enabled, _ = normalize_enabled([s for s in env.split(",") if s.strip()])
            except ValueError:
                enabled = list(MIGRATION_BASELINE)
        else:
            # 无主进程透传（直接跑 --settings）：与主进程同一套新装/升级判定
            upgrade = _is_upgrade_install(config)
            enabled = [] if (not upgrade and PURE_CORE_FOR_NEW_INSTALL) else list(MIGRATION_BASELINE)
    elif isinstance(raw, dict) and isinstance(raw.get("enabled"), list):
        try:
            enabled, _ = normalize_enabled(raw["enabled"])
        except ValueError:
            enabled = list(MIGRATION_BASELINE)
    else:
        enabled = list(MIGRATION_BASELINE)
    _snapshot = frozenset(enabled)


def freeze_snapshot_for_tests(enabled) -> None:
    """测试专用：直接冻结成指定启用集（门控用例的开关）。"""
    global _snapshot
    _snapshot = frozenset(enabled)
